"""Import leave data from the PDF into all_leave_records.csv and final_leave_records.csv."""
import csv, os, re, shutil, uuid
from datetime import datetime
from collections import defaultdict
import pdfplumber

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_DIR = os.path.join(BASE_DIR, 'csv')
DELETED_DIR = os.path.join(BASE_DIR, 'deleted_records')
os.makedirs(DELETED_DIR, exist_ok=True)

PDF_PATH = os.path.join(CSV_DIR, 'Leave Details  CAIR Student - CAIR Students_ Jan- June AY25-26_Even.pdf')
ALL_FIELDS = ['id','application_id','roll','name','email','leave_type','from_date','to_date','days','reason','semester','approved_by','approved_at']
FINAL_FIELDS = ['roll','name','email','semester','casual_total','casual_used','casual_remaining','medical_total','medical_used','medical_remaining','last_updated']


def backup_and_clear(filename, fields):
    src = os.path.join(CSV_DIR, filename)
    if os.path.exists(src):
        ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        dest = os.path.join(DELETED_DIR, f"{os.path.splitext(filename)[0]}_{ts}.csv")
        shutil.copy2(src, dest)
    with open(src, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()


def parse_date(d):
    """Parse DD/MM/YY or DD/MM/YYYY into YYYY-MM-DD."""
    d = d.strip().replace(' ', '')
    for fmt in ('%d/%m/%Y', '%d/%m/%y'):
        try:
            dt = datetime.strptime(d, fmt)
            # If year parsed as < 2000 (from %y), assume 20xx
            if dt.year < 2000:
                dt = dt.replace(year=dt.year + 2000)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def reason_to_type(reason):
    r = reason.lower().strip()
    if 'medical' in r:
        return 'medical'
    if 'office' in r or 'official' in r or 'work' in r:
        return 'official_work'
    # personal, CL, festival, holi, etc. -> casual
    return 'casual'


def parse_period_line(line):
    """Parse a line like '19/03/26 to27/03/26(9Days)' into (from_date, to_date, days)."""
    line = line.strip()
    # Extract days from parentheses
    days_match = re.search(r'\((\d+)\s*(?:Days?|Day)\)', line, re.IGNORECASE)
    days = int(days_match.group(1)) if days_match else None
    # Remove parenthetical
    line = re.sub(r'\([^)]*\)', '', line).strip()
    # Normalize 'to' spacing and split
    line = re.sub(r'\s*to\s*', ' to ', line, flags=re.IGNORECASE)
    parts = [p.strip() for p in line.split(' to ')]
    if len(parts) != 2:
        return None
    from_date = parse_date(parts[0])
    to_date = parse_date(parts[1])
    if not from_date or not to_date:
        return None
    if days is None:
        d1 = datetime.strptime(from_date, '%Y-%m-%d')
        d2 = datetime.strptime(to_date, '%Y-%m-%d')
        days = (d2 - d1).days + 1
    return from_date, to_date, days


def parse_pdf():
    students = []
    seen_rolls = set()
    with pdfplumber.open(PDF_PATH) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table[1:]:  # skip header
                    if len(row) < 9:
                        continue
                    sr, name, roll, email, total_days, period_str, reason_str, total_sem, remaining = row
                    name = name.strip()
                    roll = roll.strip()
                    email = email.strip()
                    if not roll or not name:
                        continue
                    # Skip duplicate roll entries (header repeated or footer)
                    if roll in seen_rolls:
                        continue
                    seen_rolls.add(roll)
                    total_days = total_days.strip()
                    period_str = period_str.strip() if period_str else ''
                    reason_str = reason_str.strip() if reason_str else ''
                    periods = [p.strip() for p in period_str.split('\n') if p.strip()]
                    reasons = [r.strip() for r in reason_str.split('\n') if r.strip()]
                    parsed_periods = []
                    if periods:
                        # If only one reason for multiple periods, repeat it
                        if len(reasons) == 1 and len(periods) > 1:
                            reasons = reasons * len(periods)
                        # Pad reasons if necessary
                        while len(reasons) < len(periods):
                            reasons.append('Personal')
                        for i, p in enumerate(periods):
                            parsed = parse_period_line(p)
                            if parsed:
                                parsed_periods.append((parsed[0], parsed[1], parsed[2], reasons[i] if i < len(reasons) else 'Personal'))
                    students.append({
                        'name': name,
                        'roll': roll,
                        'email': email,
                        'periods': parsed_periods
                    })
    return students


def write_records(all_students):
    backup_and_clear('all_leave_records.csv', ALL_FIELDS)
    backup_and_clear('final_leave_records.csv', FINAL_FIELDS)

    all_rows = []
    final_rows = []
    now = datetime.now().isoformat()
    semester = 'sem2'  # Jan-June AY25-26

    for student in all_students:
        roll = student['roll']
        name = student['name']
        email = student['email']
        casual_used = 0
        medical_used = 0

        for from_date, to_date, days, reason in student.get('periods', []):
            leave_type = reason_to_type(reason)
            if leave_type == 'casual':
                casual_used += days
            elif leave_type == 'medical':
                medical_used += days
            all_rows.append({
                'id': f'PDF-{uuid.uuid4().hex[:8].upper()}',
                'application_id': '',
                'roll': roll,
                'name': name,
                'email': email,
                'leave_type': leave_type,
                'from_date': from_date,
                'to_date': to_date,
                'days': str(days),
                'reason': reason,
                'semester': semester,
                'approved_by': 'office',
                'approved_at': ''
            })

        final_rows.append({
            'roll': roll,
            'name': name,
            'email': email,
            'semester': semester,
            'casual_total': '15',
            'casual_used': str(casual_used),
            'casual_remaining': str(15 - casual_used),
            'medical_total': '15',
            'medical_used': str(medical_used),
            'medical_remaining': str(15 - medical_used),
            'last_updated': now
        })

    with open(os.path.join(CSV_DIR, 'all_leave_records.csv'), 'a', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=ALL_FIELDS)
        w.writerows(all_rows)

    with open(os.path.join(CSV_DIR, 'final_leave_records.csv'), 'a', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=FINAL_FIELDS)
        w.writerows(final_rows)

    return len(all_rows), len(final_rows)


if __name__ == '__main__':
    rows = parse_pdf()
    record_count, student_count = write_records(rows)
    print(f'Imported {record_count} leave periods for {student_count} students.')
    print(f'Old files backed up to {DELETED_DIR}')
