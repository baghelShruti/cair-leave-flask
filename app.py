from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory, Response
import csv, os, hashlib, uuid, random, string, io, shutil
from datetime import datetime, date, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import bcrypt
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'cair_leave_system_secret_2026_secure'
app.permanent_session_lifetime = timedelta(hours=2)  # session timeout

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CSV_DIR    = os.path.join(BASE_DIR, 'csv')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads', 'medical')
LOG_DIR    = os.path.join(BASE_DIR, 'logs')
DELETED_DIR = os.path.join(BASE_DIR, 'deleted_records')
for d in [CSV_DIR, UPLOAD_DIR, LOG_DIR, DELETED_DIR]:
    os.makedirs(d, exist_ok=True)

ALLOWED_EXT = {'pdf', 'jpg', 'jpeg', 'png'}

# ── SMTP ─────────────────────────────────────────────────────────────────────
SMTP_SERVER   = 'smtp.gmail.com'
SMTP_PORT     = 587
SMTP_USER     = os.environ.get('SMTP_USER', 'shrutibaghel19@gmail.com')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', 'skaw aymy cgak fira')
APP_BASE_URL  = os.environ.get('APP_BASE_URL', 'http://localhost:5000')

# ── Faculty (hard-coded, synced with FACULTY dict) ───────────────────────────
FACULTY = {
    'Prof. Dipankar Deb':       {'email': 'shrutibaghel19@gmail.com', 'role': 'supervisor'},
    'Dr. Amit Shukla':          {'email': 'shrutibaghel19@gmail.com', 'role': 'supervisor'},
    'Dr. Radhe Shyam Sharma':   {'email': 'shrutibaghel19@gmail.com', 'role': 'supervisor'},
    'Dr. Praful Hambarde':      {'email': 'shrutibaghel19@gmail.com', 'role': 'supervisor'},
    'Dr. Deepak Raina':         {'email': 'shrutibaghel19@gmail.com', 'role': 'supervisor'},
    'Shruti Singh':             {'email': 'shrutibaghel19@gmail.com', 'role': 'supervisor'},
    'Dr. Narendra Kumar Dhar ': {'email': 'shrutibaghel19@gmail.com', 'role': 'supervisor'},
    'Dr. Narendra Kumar Dhar':  {'email': 'shrutibaghel19@gmail.com', 'role': 'hod'},
}


CAIR_OFFICE = {'email': 'shrutibaghel19@gmail.com', 'name': 'CAIR Office'}

# Admin login (faculty web-login removed; approvals are via email only)
ADMIN_EMAIL = 'admin@iitmandi.ac.in'
ADMIN_FIELDS = ['Email','PasswordHash','Name','Phone','Status']

def has_active_admin():
    return any(r.get('Status','').strip().lower() == 'active' for r in read_csv('admin.csv'))

def get_admin_by_email(email):
    for r in read_csv('admin.csv'):
        if r.get('Email','').strip().lower() == email.strip().lower():
            return r
    return None

def get_admin_hash(email=None):
    target = email.strip().lower() if email else ADMIN_EMAIL
    r = get_admin_by_email(target)
    return r.get('PasswordHash','') if r else ''

APPLICATION_FIELDS = [
    'id','roll','name','type','from_date','to_date','days','phone','email',
    'reason','supervisor','hod','supervisor_status','hod_status','office_status',
    'status','supervisor_remark','hod_remark','office_remark',
    'medical_file','semester','is_draft','submitted_at','updated_at',
    'sup_approved_at','hod_approved_at','office_approved_at'
]

# ── OTP store (in-memory, keyed by email) ────────────────────────────────────
# { email: { otp, expires, purpose, attempts } }
otp_store = {}

# ── Helpers ───────────────────────────────────────────────────────────────────
def csv_path(f): return os.path.join(CSV_DIR, f)

def read_csv(f):
    p = csv_path(f)
    if not os.path.exists(p): return []
    with open(p, newline='', encoding='utf-8') as fh:
        return list(csv.DictReader(fh))

def write_csv(f, rows, fields):
    with open(csv_path(f), 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

def append_csv(f, row, fields):
    p = csv_path(f); exists = os.path.exists(p)
    with open(p, 'a', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if not exists: w.writeheader()
        w.writerow(row)

def hash_pw(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def check_pw(pw, hashed):
    try: return bcrypt.checkpw(pw.encode(), hashed.encode())
    except: return False

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def get_semester():
    m = date.today().month
    return 'sem1' if (m >= 10 or m <= 3) else 'sem2'

# ── Leave-record CSV helpers ─────────────────────────────────────────────────
ALL_LEAVE_FIELDS = [
    'id','application_id','roll','name','email','leave_type','from_date','to_date',
    'days','reason','semester','approved_by','approved_at'
]
FINAL_LEAVE_FIELDS = [
    'roll','name','email','semester','casual_total','casual_used','casual_remaining',
    'medical_total','medical_used','medical_remaining','last_updated'
]

def read_all_leave_records():
    return read_csv('all_leave_records.csv')

def write_all_leave_records(rows):
    write_csv('all_leave_records.csv', rows, ALL_LEAVE_FIELDS)

def read_final_leave_records():
    return read_csv('final_leave_records.csv')

def write_final_leave_records(rows):
    write_csv('final_leave_records.csv', rows, FINAL_LEAVE_FIELDS)

def recalculate_final_record(roll):
    """Recalculate totals/remaining for one student from all_leave_records.csv (casual/medical only)."""
    records = [r for r in read_all_leave_records() if r['roll'] == roll and r['leave_type'] in ('casual','medical')]
    casual_used = sum(int(r['days']) for r in records if r['leave_type'] == 'casual')
    medical_used = sum(int(r['days']) for r in records if r['leave_type'] == 'medical')
    # Use existing name/email if no casual/medical records found
    all_recs = [r for r in read_all_leave_records() if r['roll'] == roll]
    name = all_recs[0]['name'] if all_recs else ''
    email = all_recs[0]['email'] if all_recs else ''
    sem = all_recs[0]['semester'] if all_recs else get_semester()

    rows = read_final_leave_records()
    found = False
    for r in rows:
        if r['roll'] == roll:
            r['name'] = name or r['name']
            r['email'] = email or r['email']
            r['semester'] = sem
            r['casual_used'] = casual_used
            r['casual_remaining'] = 15 - casual_used
            r['medical_used'] = medical_used
            r['medical_remaining'] = 15 - medical_used
            r['last_updated'] = datetime.now().isoformat()
            found = True
    if not found:
        rows.append({
            'roll': roll, 'name': name, 'email': email, 'semester': sem,
            'casual_total': 15, 'casual_used': casual_used, 'casual_remaining': 15 - casual_used,
            'medical_total': 15, 'medical_used': medical_used, 'medical_remaining': 15 - medical_used,
            'last_updated': datetime.now().isoformat()
        })
    write_final_leave_records(rows)

def add_leave_record(app_row, approved_by, update_final=True):
    """Insert one approved leave into all_leave_records.csv and optionally recalculate final record."""
    if app_row.get('type') == 'official_work':
        leave_type = 'official_work'
    else:
        leave_type = app_row.get('type', '')
    row = {
        'id': f'REC-{uuid.uuid4().hex[:8].upper()}',
        'application_id': app_row.get('id', ''),
        'roll': app_row.get('roll', ''),
        'name': app_row.get('name', ''),
        'email': app_row.get('email', ''),
        'leave_type': leave_type,
        'from_date': app_row.get('from_date', ''),
        'to_date': app_row.get('to_date', ''),
        'days': app_row.get('days', '0'),
        'reason': app_row.get('reason', ''),
        'semester': app_row.get('semester', get_semester()),
        'approved_by': approved_by,
        'approved_at': datetime.now().isoformat()
    }
    append_csv('all_leave_records.csv', row, ALL_LEAVE_FIELDS)
    if update_final:
        recalculate_final_record(row['roll'])

def delete_leave_records_by_application_id(app_id):
    """Remove all records linked to an application and recalculate affected student."""
    records = read_all_leave_records()
    to_keep = [r for r in records if r['application_id'] != app_id]
    if len(to_keep) != len(records):
        affected = list({r['roll'] for r in records if r['application_id'] == app_id})
        write_all_leave_records(to_keep)
        for roll in affected:
            recalculate_final_record(roll)

def get_balance(roll, leave_type):
    """Return remaining leave from final_leave_records.csv."""
    if leave_type not in ('casual', 'medical'):
        return 0
    for r in read_final_leave_records():
        if r['roll'] == roll:
            return int(r.get(f'{leave_type}_remaining', 15))
    return 15

def generate_leave_id():
    apps = read_csv('applications.csv')
    max_num = 0
    prefix = f'CAIR-{date.today().year}-'
    for a in apps:
        aid = a.get('id', '')
        if aid.startswith(prefix):
            try:
                num = int(aid.split('-')[-1])
                max_num = max(max_num, num)
            except ValueError:
                pass
    return f'{prefix}{max_num + 1:04d}'

def generate_draft_id():
    return f'DRAFT-{uuid.uuid4().hex[:8].upper()}'

def activity_log(user, user_type, action):
    append_csv('activity_log.csv', {
        'name': user, 'user_type': user_type, 'action': action,
        'date': date.today().isoformat(), 'time': datetime.now().strftime('%H:%M:%S')
    }, ['name','user_type','action','date','time'])

# ── Immutable admin changes audit log ─────────────────────────────────────────
ADMIN_CHANGE_FIELDS = ['timestamp','admin_email','action','record_id','roll','old_values','new_values']

def log_admin_change(admin_email, action, record_id='', roll='', old_values='', new_values=''):
    """Append one row to admin_changes_log.csv. No UI allows editing this log."""
    append_csv('admin_changes_log.csv', {
        'timestamp': datetime.now().isoformat(),
        'admin_email': admin_email,
        'action': action,
        'record_id': record_id,
        'roll': roll,
        'old_values': old_values,
        'new_values': new_values
    }, ADMIN_CHANGE_FIELDS)

REMINDER_LOG_FIELDS = ['timestamp','application_id','role','recipient_email','status']

def log_reminder(app_id, role, recipient_email, status):
    append_csv('reminder_log.csv', {
        'timestamp': datetime.now().isoformat(),
        'application_id': app_id,
        'role': role,
        'recipient_email': recipient_email,
        'status': status
    }, REMINDER_LOG_FIELDS)

# ── Email ─────────────────────────────────────────────────────────────────────
def send_email(to, subject, html, reply_to=None):
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = SMTP_USER
        msg['To']      = to if isinstance(to, str) else ', '.join(to)
        if reply_to:
            msg['Reply-To'] = reply_to
        msg.attach(MIMEText(html, 'html'))
        s = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        s.starttls(); s.login(SMTP_USER, SMTP_PASSWORD)
        s.sendmail(SMTP_USER, [to] if isinstance(to, str) else to, msg.as_string())
        s.quit()
        append_csv('email_log.csv', {
            'timestamp': datetime.now().isoformat(), 'to': str(to), 'subject': subject, 'status': 'sent'
        }, ['timestamp','to','subject','status'])
        return True
    except Exception as e:
        append_csv('email_log.csv', {
            'timestamp': datetime.now().isoformat(), 'to': str(to), 'subject': subject, 'status': f'failed:{e}'
        }, ['timestamp','to','subject','status'])
        return False

def email_wrap(title, body, color='#1a237e'):
    return f"""<!DOCTYPE html><html><body style="font-family:Segoe UI,sans-serif;background:#f5f7fa;margin:0;padding:20px;">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1);">
<div style="background:{color};padding:30px;text-align:center;">
  <h2 style="color:#fff;margin:0;">🎓 CAIR Leave Portal</h2>
  <p style="color:rgba(255,255,255,.8);margin:6px 0 0;">IIT Mandi — Centre for AI &amp; Robotics</p>
</div>
<div style="padding:30px;">{body}</div>
<div style="background:#f5f7fa;padding:15px;text-align:center;font-size:12px;color:#90a4ae;">
  CAIR Leave Management System · IIT Mandi · Do not reply to this email.
</div></div></body></html>"""

def send_otp_email(email, otp, purpose='verify'):
    action = 'Email Verification' if purpose == 'verify' else 'Password Reset'
    body = f"""<p>Your OTP for <strong>{action}</strong> is:</p>
<div style="font-size:40px;font-weight:800;letter-spacing:12px;color:#1a237e;text-align:center;padding:20px;
background:#e8eaf6;border-radius:12px;margin:20px 0;">{otp}</div>
<p style="color:#607d8b;">This OTP expires in <strong>5 minutes</strong>. Do not share it with anyone.</p>"""
    send_email(email, f'CAIR Portal – {action} OTP', email_wrap(action, body))

def otp_generate(email, purpose):
    otp = ''.join(random.choices(string.digits, k=6))
    otp_store[email] = {'otp': otp, 'expires': datetime.now() + timedelta(minutes=5),
                        'purpose': purpose, 'attempts': 0}
    send_otp_email(email, otp, purpose)
    return otp

def otp_verify(email, code, purpose):
    entry = otp_store.get(email)
    if not entry: return False, 'OTP not found or expired'
    if entry['purpose'] != purpose: return False, 'Invalid OTP purpose'
    if datetime.now() > entry['expires']:
        del otp_store[email]; return False, 'OTP expired'
    entry['attempts'] += 1
    if entry['attempts'] > 5:
        del otp_store[email]; return False, 'Too many attempts'
    if entry['otp'] != code: return False, 'Incorrect OTP'
    del otp_store[email]; return True, 'OK'

# ── Approval email builders ───────────────────────────────────────────────────
def approval_email(app, role, reviewer_label):
    aid = app['id']
    sup_line = f"<p><strong>Supervisor:</strong> {app['supervisor']} ✅</p>" if role in ('hod','office') else ''
    hod_line  = f"<p><strong>HoD:</strong> {app['hod']} ✅</p>" if role == 'office' else ''
    meet_btn  = '' if role == 'office' else f"""
      <a href="{APP_BASE_URL}/approve?id={aid}&role={role}&decision=explanation"
         style="display:inline-block;background:#ff9800;color:#fff;padding:12px 24px;
                text-decoration:none;border-radius:8px;margin:5px;font-weight:700;">📅 MEET IN OFFICE</a>"""
    roll = app['roll']
    casual_remaining = get_balance(roll, 'casual')
    casual_used = 15 - casual_remaining
    medical_remaining = get_balance(roll, 'medical')
    medical_used = 15 - medical_remaining
    body = f"""<p>A leave application requires your review as <strong>{reviewer_label}</strong>.</p>
<div style="background:#f5f7fa;padding:16px;border-radius:10px;margin:16px 0;">
  <p><strong>Student:</strong> {app['name']} ({app['roll']})</p>
  <p><strong>Leave Type:</strong> {app['type'].title()} Leave</p>
  <p><strong>Period:</strong> {app['from_date']} → {app['to_date']} ({app['days']} days)</p>
  <p><strong>Reason:</strong> {app['reason']}</p>
  <p><strong>Contact:</strong> {app['phone']} | {app['email']}</p>
  <div style="background:#fff;border:1px solid #e0e6ed;border-radius:10px;padding:12px;margin-top:12px;">
    <p style="margin:0;font-weight:700;color:#1a237e;">📊 Leave Balance (Current Semester)</p>
    <p style="margin:6px 0 0;">🏖️ <strong>Casual:</strong> Used {casual_used} | Remaining {casual_remaining}</p>
    <p style="margin:6px 0 0;">🏥 <strong>Medical:</strong> Used {medical_used} | Remaining {medical_remaining}</p>
  </div>
  {sup_line}{hod_line}
  <p style="margin-top:12px;"><strong>Application ID:</strong> {aid}</p>
</div>
<div style="text-align:center;margin:24px 0;">
  <a href="{APP_BASE_URL}/approve?id={aid}&role={role}&decision=approved"
     style="display:inline-block;background:#00c853;color:#fff;padding:12px 24px;
            text-decoration:none;border-radius:8px;margin:5px;font-weight:700;">✅ ACCEPT</a>
  <a href="{APP_BASE_URL}/approve?id={aid}&role={role}&decision=rejected"
     style="display:inline-block;background:#ff1744;color:#fff;padding:12px 24px;
            text-decoration:none;border-radius:8px;margin:5px;font-weight:700;">❌ REJECT</a>
  {meet_btn}
</div>
<p style="font-size:12px;color:#90a4ae;text-align:center;">Click a button to respond directly — no login required.</p>"""
    return email_wrap(f'{reviewer_label} Review', body)

def student_status_email(app, role, decision):
    aid   = app['id']
    color = '#00c853' if decision == 'approved' else '#ff1744' if decision == 'rejected' else '#ff9800'
    label = 'APPROVED' if decision == 'approved' else 'REJECTED' if decision == 'rejected' else 'MEET IN OFFICE'
    role_label = {'supervisor':'Supervisor','hod':'HoD','office':'CAIR Office'}.get(role, role.upper())
    approver   = app['supervisor'] if role=='supervisor' else app['hod'] if role=='hod' else CAIR_OFFICE['name']
    if   decision == 'approved' and role == 'supervisor': nxt = 'Your application has been forwarded to the HoD.'
    elif decision == 'approved' and role == 'hod':        nxt = 'Your application has been forwarded to the CAIR Office.'
    elif decision == 'approved' and role == 'office':     nxt = '🎉 Your leave is fully approved!'
    elif decision == 'rejected':                          nxt = f'Rejected by {approver}. Please contact them for details.'
    else:                                                 nxt = f'Please meet {approver} in their office.'
    remark = app.get(f'{role}_remark','') or app.get('office_remark','')
    remark_html = f'<p><strong>Remark:</strong> {remark}</p>' if remark else ''
    body = f"""<p>Dear <strong>{app['name']}</strong>,</p>
<p>Your application <strong>{aid}</strong> has been <strong style="color:{color};">{label}</strong>
   by <strong>{approver}</strong> ({role_label}).</p>
<div style="background:#f5f7fa;padding:16px;border-radius:10px;margin:16px 0;">
  <p><strong>Leave Type:</strong> {app['type'].title()}</p>
  <p><strong>Period:</strong> {app['from_date']} → {app['to_date']} ({app['days']} days)</p>
  {remark_html}
</div>
<div style="background:{'#e8f5e9' if decision=='approved' else '#ffebee' if decision=='rejected' else '#fff8e1'};
            padding:14px;border-radius:10px;border-left:4px solid {color};">
  <p>{nxt}</p>
</div>
<p style="margin-top:16px;color:#607d8b;font-size:13px;">
  Status: Supervisor={app['supervisor_status']} | HoD={app['hod_status']} | Office={app['office_status']}
</p>"""
    return email_wrap('Leave Update', body, color)

# ── Notification helpers ──────────────────────────────────────────────────────
def notify_supervisor(app):
    to = FACULTY[app['supervisor']]['email']
    send_email(to, f"[SUPERVISOR] Leave {app['id']} – {app['name']}",
               approval_email(app, 'supervisor', 'Supervisor'),
               reply_to=app.get('email'))
    send_email(app['email'], f"Leave Submitted – {app['id']}",
               email_wrap('Leave Submitted', f"<p>Your leave application <strong>{app['id']}</strong> has been submitted and sent to your Supervisor for review.</p>"))

def notify_hod(app):
    to = FACULTY[app['hod']]['email']
    send_email(to, f"[HOD] Leave {app['id']} – {app['name']}",
               approval_email(app, 'hod', 'HoD'),
               reply_to=app.get('email'))

def notify_office(app):
    send_email(CAIR_OFFICE['email'], f"[OFFICE] Leave {app['id']} – {app['name']}",
               approval_email(app, 'office', 'CAIR Office'))

def notify_student(app, role, decision):
    send_email(app['email'], f"Leave {app['id']} – {decision.upper()}",
               student_status_email(app, role, decision))

def reminder_email(app, role):
    reviewer_label = 'Supervisor' if role == 'supervisor' else 'HoD'
    approver_name = app['supervisor'] if role == 'supervisor' else app['hod']
    body = f"""<p>Dear <strong>{approver_name}</strong>,</p>
<p>This is a friendly reminder that a leave application requires your review as <strong>{reviewer_label}</strong>.</p>
<div style="background:#f5f7fa;padding:16px;border-radius:10px;margin:16px 0;">
  <p><strong>Student:</strong> {app['name']} ({app['roll']})</p>
  <p><strong>Leave Type:</strong> {app['type'].title()}</p>
  <p><strong>Period:</strong> {app['from_date']} → {app['to_date']} ({app['days']} days)</p>
  <p><strong>Reason:</strong> {app['reason']}</p>
  <p><strong>Application ID:</strong> {app['id']}</p>
</div>
<div style="text-align:center;margin:24px 0;">
  <a href="{APP_BASE_URL}/approve?id={app['id']}&role={role}&decision=approved"
     style="display:inline-block;background:#00c853;color:#fff;padding:12px 24px;
            text-decoration:none;border-radius:8px;margin:5px;font-weight:700;">✅ ACCEPT</a>
  <a href="{APP_BASE_URL}/approve?id={app['id']}&role={role}&decision=rejected"
     style="display:inline-block;background:#ff1744;color:#fff;padding:12px 24px;
            text-decoration:none;border-radius:8px;margin:5px;font-weight:700;">❌ REJECT</a>
</div>
<p style="font-size:12px;color:#90a4ae;text-align:center;">Click a button to respond directly — no login required.</p>"""
    return email_wrap(f'Reminder: {reviewer_label} Review', body, '#ff9800')

# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('landing.html', has_admin=has_active_admin())

@app.route('/uploads/medical/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# ── OTP ───────────────────────────────────────────────────────────────────────
@app.route('/api/otp/send', methods=['POST'])
def api_otp_send():
    d = request.json
    email, purpose = d.get('email','').strip().lower(), d.get('purpose','verify')
    if not email: return jsonify(success=False, message='Email required')
    otp_generate(email, purpose)
    return jsonify(success=True, message='OTP sent')

@app.route('/api/otp/verify', methods=['POST'])
def api_otp_verify():
    d = request.json
    ok, msg = otp_verify(d.get('email','').strip().lower(), d.get('otp',''), d.get('purpose','verify'))
    return jsonify(success=ok, message=msg)

# ── Student Registration ───────────────────────────────────────────────────────
@app.route('/register/student')
def student_register_page():
    return render_template('student_register.html')

@app.route('/api/student/register', methods=['POST'])
def student_register():
    d = request.json
    name  = d.get('name','').strip()
    roll  = d.get('roll','').strip().upper()
    email = d.get('email','').strip().lower()
    phone = d.get('phone','').strip()
    pw    = d.get('password','')

    if not all([name, roll, email, phone, pw]):
        return jsonify(success=False, message='All fields are required')
    if len(pw) < 6:
        return jsonify(success=False, message='Password must be at least 6 characters')

    students = read_csv('student_registry.csv')
    if any(s['RollNumber'] == roll for s in students):
        return jsonify(success=False, message='Roll number already registered')
    if any(s['OfficialEmail'] == email for s in students):
        return jsonify(success=False, message='Email already registered')

    row = {'Name': name, 'RollNumber': roll, 'OfficialEmail': email,
           'MobileNumber': phone, 'PasswordHash': hash_pw(pw)}
    append_csv('student_registry.csv', row, ['Name','RollNumber','OfficialEmail','MobileNumber','PasswordHash'])
    activity_log(name, 'Student', 'Registered')

    send_email(email, 'Welcome to CAIR Leave Portal',
               email_wrap('Registration Successful',
                          f'<p>Dear <strong>{name}</strong>,</p><p>Your student account has been created successfully. You can now log in using your official email.</p>'))
    return jsonify(success=True, message='Account created successfully')

# ── Admin Registration (first admin only) ──────────────────────────────────────
@app.route('/register/admin')
def admin_register_page():
    if has_active_admin():
        return redirect('/login?type=admin')
    return render_template('admin_register.html')

@app.route('/api/admin/register', methods=['POST'])
def admin_register():
    if has_active_admin():
        return jsonify(success=False, message='Admin already exists. Contact existing admin.')
    d = request.json
    name  = d.get('name','').strip()
    email = d.get('email','').strip().lower()
    phone = d.get('phone','').strip()
    pw    = d.get('password','')

    if not all([name, email, phone, pw]):
        return jsonify(success=False, message='All fields are required')
    if len(pw) < 6:
        return jsonify(success=False, message='Password must be at least 6 characters')

    admins = read_csv('admin.csv')
    if any(a['Email'].strip().lower() == email for a in admins):
        return jsonify(success=False, message='Email already registered')

    row = {'Email': email, 'PasswordHash': hash_pw(pw), 'Name': name, 'Phone': phone, 'Status': 'active'}
    append_csv('admin.csv', row, ADMIN_FIELDS)
    activity_log(name, 'Admin', 'Registered as first admin')
    return jsonify(success=True, message='Admin account created successfully')

# ── Login ─────────────────────────────────────────────────────────────────────
@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    d     = request.json
    email = d.get('email','').strip().lower()
    pw    = d.get('password','')
    utype = d.get('type','student')   # 'student' | 'admin'

    if utype == 'student':
        users = read_csv('student_registry.csv')
        user  = next((u for u in users if u['OfficialEmail'] == email), None)
        if not user or not check_pw(pw, user['PasswordHash']):
            return jsonify(success=False, message='Invalid email or password')
        session.permanent = True
        session['user']   = {'name': user['Name'], 'email': email,
                             'roll': user['RollNumber'], 'phone': user['MobileNumber'], 'type': 'student'}
        activity_log(user['Name'], 'Student', 'Login')
        return jsonify(success=True, redirect='/student/dashboard')

    elif utype == 'admin':
        admin = get_admin_by_email(email)
        if not admin or admin.get('Status','').strip().lower() != 'active' or not check_pw(pw, admin.get('PasswordHash','')):
            return jsonify(success=False, message='Invalid admin credentials')
        session.permanent = True
        session['user'] = {'name': admin.get('Name','Admin'), 'email': email, 'type': 'faculty', 'role': 'admin'}
        activity_log(admin.get('Name','Admin'), 'Admin', 'Login')
        return jsonify(success=True, redirect='/admin/dashboard')

    else:
        return jsonify(success=False, message='Invalid login type')

@app.route('/logout')
def logout():
    u = session.get('user',{})
    activity_log(u.get('name','?'), u.get('type','?'), 'Logout')
    session.clear()
    return redirect('/')

# ── Forgot / Reset Password ───────────────────────────────────────────────────
@app.route('/forgot')
def forgot_page():
    return render_template('forgot_password.html')

@app.route('/api/forgot/reset', methods=['POST'])
def forgot_reset():
    d     = request.json
    email = d.get('email','').strip().lower()
    otp   = d.get('otp','')
    pw    = d.get('password','')
    utype = d.get('type','student')

    ok, msg = otp_verify(email, otp, 'reset')
    if not ok: return jsonify(success=False, message=msg)

    if utype == 'student':
        rows  = read_csv('student_registry.csv')
        found = False
        for r in rows:
            if r['OfficialEmail'] == email:
                r['PasswordHash'] = hash_pw(pw); found = True
        if not found: return jsonify(success=False, message='Email not found')
        write_csv('student_registry.csv', rows,
                  ['Name','RollNumber','OfficialEmail','MobileNumber','PasswordHash'])
        activity_log(email, 'Student', 'Password Reset')
    elif utype == 'admin':
        rows = read_csv('admin.csv')
        found = False
        for r in rows:
            if r.get('Email','').strip().lower() == email:
                r['PasswordHash'] = hash_pw(pw); found = True
        if not found: return jsonify(success=False, message='Email not found')
        write_csv('admin.csv', rows, ADMIN_FIELDS)
        activity_log(email, 'Admin', 'Password Reset')
    else:
        return jsonify(success=False, message='Invalid user type')

    send_email(email, 'CAIR Portal – Password Reset Successful',
               email_wrap('Password Changed', '<p>Your password has been reset successfully.</p>'))
    return jsonify(success=True, message='Password reset successful')

# ── Change Password ───────────────────────────────────────────────────────────
@app.route('/api/change-password', methods=['POST'])
def change_password():
    u = session.get('user')
    if not u: return jsonify(success=False, message='Not logged in')
    d       = request.json
    old_pw  = d.get('old_password','')
    new_pw  = d.get('new_password','')
    email   = u['email']
    utype   = u['type']

    if utype == 'student':
        rows = read_csv('student_registry.csv')
        for r in rows:
            if r['OfficialEmail'] == email:
                if not check_pw(old_pw, r['PasswordHash']):
                    return jsonify(success=False, message='Current password incorrect')
                r['PasswordHash'] = hash_pw(new_pw)
        write_csv('student_registry.csv', rows,
                  ['Name','RollNumber','OfficialEmail','MobileNumber','PasswordHash'])
    elif u.get('role') == 'admin':
        rows = read_csv('admin.csv')
        for r in rows:
            if r.get('Email','').strip().lower() == email:
                if not check_pw(old_pw, r['PasswordHash']):
                    return jsonify(success=False, message='Current password incorrect')
                r['PasswordHash'] = hash_pw(new_pw)
        write_csv('admin.csv', rows, ADMIN_FIELDS)
    else:
        return jsonify(success=False, message='Invalid user type')

    activity_log(u['name'], u.get('role','user').title(), 'Password Changed')
    send_email(email, 'CAIR Portal – Password Changed',
               email_wrap('Password Changed', '<p>Your password was changed successfully. If this was not you, contact admin immediately.</p>'))
    return jsonify(success=True, message='Password changed')

# ── Faculty list (for apply form) ─────────────────────────────────────────────
@app.route('/api/faculty')
def api_faculty():
    supervisors = {k: v for k, v in FACULTY.items() if v['role'] == 'supervisor'}
    hod         = {k: v for k, v in FACULTY.items() if v['role'] == 'hod'}
    return jsonify(supervisors=supervisors, hod=hod)

# ── Student Dashboard ─────────────────────────────────────────────────────────
@app.route('/student/dashboard')
def student_dashboard():
    if not session.get('user') or session['user']['type'] != 'student':
        return redirect('/login')
    return render_template('student_dashboard.html')

@app.route('/api/student/dashboard')
def api_student_dashboard():
    u = session.get('user')
    if not u: return jsonify(success=False, message='Not logged in'), 401
    roll = u['roll']
    sem  = get_semester()
    final = next((r for r in read_final_leave_records() if r['roll'] == roll), None)
    if not final:
        # If student has no final record yet, create one
        recalculate_final_record(roll)
        final = next((r for r in read_final_leave_records() if r['roll'] == roll), None)
    return jsonify(
        success=True,
        user=u,
        casual_used=int(final.get('casual_used', 0)) if final else 0,
        casual_remaining=int(final.get('casual_remaining', 15)) if final else 15,
        medical_used=int(final.get('medical_used', 0)) if final else 0,
        medical_remaining=int(final.get('medical_remaining', 15)) if final else 15,
    )

# ── Leave Draft ───────────────────────────────────────────────────────────────
@app.route('/api/draft/save', methods=['POST'])
def save_draft():
    u = session.get('user')
    if not u: return jsonify(success=False, message='Not logged in')
    d    = request.json
    roll = u['roll']
    apps = read_csv('applications.csv')

    # update existing draft if same draft_id, else create
    did = d.get('draft_id')
    if did:
        updated = False
        for a in apps:
            if a['id'] == did and a['roll'] == roll and a['is_draft'] == '1':
                for k in ['type','from_date','to_date','days','phone','email','reason','supervisor','hod']:
                    if k in d: a[k] = d[k]
                a['updated_at'] = datetime.now().isoformat()
                updated = True
        if updated:
            write_csv('applications.csv', apps, APPLICATION_FIELDS)
            return jsonify(success=True, draft_id=did, message='Draft updated')

    new_id = generate_draft_id()
    sem    = get_semester()
    row    = {
        'id': new_id, 'roll': roll, 'name': u['name'], 'type': d.get('type',''),
        'from_date': d.get('from_date',''), 'to_date': d.get('to_date',''),
        'days': d.get('days','0'), 'phone': d.get('phone', u.get('phone','')),
        'email': d.get('email', u['email']), 'reason': d.get('reason',''),
        'supervisor': d.get('supervisor',''), 'hod': d.get('hod',''),
        'supervisor_status':'pending','hod_status':'pending','office_status':'pending',
        'status':'draft','supervisor_remark':'','hod_remark':'','office_remark':'',
        'medical_file':'','semester': sem,'is_draft':'1',
        'submitted_at':'','updated_at': datetime.now().isoformat(),
        'sup_approved_at':'','hod_approved_at':'','office_approved_at':''
    }
    append_csv('applications.csv', row, APPLICATION_FIELDS)
    return jsonify(success=True, draft_id=new_id, message='Draft saved')

@app.route('/api/draft/<draft_id>', methods=['DELETE'])
def delete_draft(draft_id):
    u = session.get('user')
    if not u: return jsonify(success=False, message='Not logged in')
    apps = read_csv('applications.csv')
    app  = next((a for a in apps if a['id'] == draft_id and a['is_draft'] == '1'), None)
    if not app: return jsonify(success=False, message='Draft not found')
    if app['roll'] != u['roll']: return jsonify(success=False, message='Unauthorised')
    apps = [a for a in apps if a['id'] != draft_id]
    write_csv('applications.csv', apps, APPLICATION_FIELDS)
    return jsonify(success=True, message='Draft deleted')

# ── Apply Leave ───────────────────────────────────────────────────────────────
@app.route('/api/apply', methods=['POST'])
def apply_leave():
    u = session.get('user')
    if not u: return jsonify(success=False, message='Not logged in')

    leave_type = request.form.get('type','')
    from_date  = request.form.get('from','')
    to_date    = request.form.get('to','')
    days       = int(request.form.get('days', 0))
    phone      = request.form.get('phone','').strip()
    email      = request.form.get('email','').strip()
    reason     = request.form.get('reason','').strip()
    supervisor = request.form.get('supervisor','')
    hod        = request.form.get('hod','')
    draft_id   = request.form.get('draft_id','')

    if not all([leave_type, from_date, to_date, days, phone, email, reason]):
        return jsonify(success=False, message='All fields are required')
    if not email.endswith('@students.iitmandi.ac.in'):
        return jsonify(success=False, message='Must use institute email (@students.iitmandi.ac.in)')

    # Medical proof upload (optional)
    medical_file = ''
    if leave_type == 'medical':
        f = request.files.get('medical_proof')
        if f and f.filename:
            if not allowed_file(f.filename):
                return jsonify(success=False, message='Invalid file type. Allowed: PDF, JPG, JPEG, PNG')
            fname = secure_filename(f'{uuid.uuid4().hex}_{f.filename}')
            f.save(os.path.join(UPLOAD_DIR, fname))
            medical_file = fname

    # Balance check (only casual/medical deduct)
    if leave_type in ('casual','medical'):
        bal = get_balance(u['roll'], leave_type)
        if days > bal:
            return jsonify(success=False, message=f'Insufficient {leave_type} leave. Have {bal}, need {days}')

    if supervisor not in FACULTY:
        return jsonify(success=False, message='Invalid supervisor')
    if not hod:
        hod_entry = next(((k,v) for k,v in FACULTY.items() if v['role']=='hod'), None)
        if not hod_entry:
            return jsonify(success=False, message='No HoD configured')
        hod = hod_entry[0]
    sup_status = 'pending'
    hod_status = 'pending'
    off_status = 'pending'
    app_status = 'pending'

    app_id = generate_leave_id()
    sem    = get_semester()
    now    = datetime.now().isoformat()

    row = {
        'id': app_id, 'roll': u['roll'], 'name': u['name'], 'type': leave_type,
        'from_date': from_date, 'to_date': to_date, 'days': str(days),
        'phone': phone, 'email': email, 'reason': reason,
        'supervisor': supervisor, 'hod': hod,
        'supervisor_status': sup_status, 'hod_status': hod_status, 'office_status': off_status,
        'status': app_status, 'supervisor_remark': '', 'hod_remark': '', 'office_remark': '',
        'medical_file': medical_file, 'semester': sem, 'is_draft': '0',
        'submitted_at': now, 'updated_at': now,
        'sup_approved_at': '', 'hod_approved_at': '', 'office_approved_at': ''
    }

    # If converting a draft, remove it first
    apps = read_csv('applications.csv')
    if draft_id:
        apps = [a for a in apps if a['id'] != draft_id]
    apps.append(row)
    write_csv('applications.csv', apps, APPLICATION_FIELDS)

    activity_log(u['name'], 'Student', f'Leave submitted {app_id}')
    notify_supervisor(row)
    return jsonify(success=True, application=row, message=f'Application {app_id} submitted!')

# ── Get applications ──────────────────────────────────────────────────────────
@app.route('/api/applications/<roll>')
def get_applications(roll):
    u = session.get('user')
    if not u: return jsonify(success=False), 401
    apps = [a for a in read_csv('applications.csv') if a['roll'] == roll]
    apps.sort(key=lambda x: x.get('submitted_at',''), reverse=True)
    return jsonify(applications=apps)

@app.route('/api/drafts')
def get_drafts():
    u = session.get('user')
    if not u: return jsonify(success=False), 401
    drafts = [a for a in read_csv('applications.csv')
              if a['roll'] == u['roll'] and a.get('is_draft','0') == '1']
    return jsonify(drafts=drafts)

# ── Delete application ────────────────────────────────────────────────────────
@app.route('/api/delete/<app_id>', methods=['DELETE'])
def delete_application(app_id):
    u    = session.get('user')
    roll = request.args.get('roll')
    apps = read_csv('applications.csv')
    
    # Find application matching BOTH id AND roll (handles duplicate IDs)
    matching = [x for x in apps if x['id'] == app_id and x['roll'] == (u['roll'] if u else roll)]
    if not matching:
        # Fallback: try by ID only, then verify roll
        a = next((x for x in apps if x['id'] == app_id), None)
        if not a:
            return jsonify(success=False, message='Not found')
        if a['roll'] != (u['roll'] if u else roll):
            return jsonify(success=False, message='Unauthorised')
    else:
        a = matching[0]  # Take the one belonging to this student
    
    # Strip whitespace from CSV values (defensive)
    status = a.get('status', '').strip().lower()
    sup_status = a.get('supervisor_status', '').strip().lower()
    
    if status not in ('pending', 'draft'):
        return jsonify(success=False, message='Cannot delete — already processed')
    if status == 'pending' and sup_status != 'pending':
        return jsonify(success=False, message='Cannot delete — supervisor already actioned')
    
    # Remove only this specific record
    apps = [x for x in apps if not (x['id'] == app_id and x['roll'] == a['roll'])]
    write_csv('applications.csv', apps, APPLICATION_FIELDS)
    
    # Also remove from permanent leave records if it was ever recorded
    delete_leave_records_by_application_id(app_id)
    
    activity_log(u['name'] if u else roll, 'Student', f'Deleted {app_id}')
    return jsonify(success=True, message='Deleted successfully')

def _process_decision(apps, app, role, decision, remark):
    now = datetime.now().isoformat()
    leave_type = app.get('type','')
    is_official = (leave_type == 'official_work')
    
    if role == 'supervisor':
        if app['supervisor_status'] != 'pending':
            return jsonify(success=False, message='Already actioned')
        app['supervisor_status'] = decision
        app['supervisor_remark'] = remark
        if decision == 'approved':
            app['status'] = 'hod'; app['sup_approved_at'] = now
        elif decision == 'rejected':
            app['status'] = 'rejected'
        else:
            app['status'] = 'explanation'

    elif role == 'hod':
        if app['supervisor_status'] != 'approved':
            return jsonify(success=False, message='Supervisor must approve first')
        if app['hod_status'] != 'pending':
            return jsonify(success=False, message='Already actioned')
        app['hod_status'] = decision
        app['hod_remark'] = remark
        if decision == 'approved':
            app['status'] = 'approved'; app['hod_approved_at'] = now
            # HoD approval is final; CAIR Office is notified only and does not need to action
            app['office_status'] = 'approved'; app['office_approved_at'] = now
        elif decision == 'rejected':
            app['status'] = 'rejected'
        else:
            app['status'] = 'explanation'

    app['updated_at'] = now
    write_csv('applications.csv', apps, APPLICATION_FIELDS)
    notify_student(app, role, decision)

    # ── CHAIN FORWARD + NOTIFY OFFICE ON FINAL STATE ──
    if decision == 'approved':
        if role == 'supervisor':
            notify_hod(app)
        elif role == 'hod':
            # Office is notified of the final decision; it does not approve/reject
            notify_office_final(app, 'approved')
            if is_official:
                # Official work: recorded but no balance deduction
                add_leave_record(app, 'hod', update_final=False)
            else:
                # Casual/Medical: recorded + balance updated
                add_leave_record(app, 'hod')
    elif decision == 'rejected':
        notify_office_final(app, 'rejected')

    activity_log(app.get('supervisor','?'), 'Faculty', f'{role} {decision} {app["id"]}')
    return jsonify(success=True, message=f'Application {decision}')

# ── NEW: Office notification only, no approval ──
def notify_office_final(app, final_status):
    color = '#00c853' if final_status == 'approved' else '#ff1744'
    label = 'FULLY APPROVED' if final_status == 'approved' else 'REJECTED'
    nxt = '🎉 Leave fully approved!' if final_status == 'approved' else f'Application rejected. No further action required.'
    
    body = f"""<p>Dear <strong>CAIR Office</strong>,</p>
<p>Application <strong>{app['id']}</strong> has been <strong style="color:{color};">{label}</strong>.</p>
<div style="background:#f5f7fa;padding:16px;border-radius:10px;margin:16px 0;">
  <p><strong>Student:</strong> {app['name']} ({app['roll']})</p>
  <p><strong>Leave Type:</strong> {app['type'].title()}</p>
  <p><strong>Period:</strong> {app['from_date']} → {app['to_date']} ({app['days']} days)</p>
  <p><strong>Supervisor:</strong> {app['supervisor']} — {app['supervisor_status']}</p>
  <p><strong>HoD:</strong> {app['hod']} — {app['hod_status']}</p>
</div>
<div style="background:{'#e8f5e9' if final_status=='approved' else '#ffebee'};
            padding:14px;border-radius:10px;border-left:4px solid {color};">
  <p>{nxt}</p>
</div>"""
    send_email(CAIR_OFFICE['email'], f"[FINAL] Leave {app['id']} – {label}", 
               email_wrap(f'Leave {label}', body, color),
               reply_to=app.get('email'))

# ── Approve via email link ────────────────────────────────────────────────────
@app.route('/approve')
def approve_via_email():
    app_id   = request.args.get('id')
    decision = request.args.get('decision')

    apps = read_csv('applications.csv')
    app  = next((a for a in apps if a['id'] == app_id), None)
    if not app:
        return render_template('approve_page.html', error='Application not found', app=None)

    # Auto-detect role from application state
    if app['supervisor_status'] == 'pending':
        role = 'supervisor'
    elif app['hod_status'] == 'pending':
        role = 'hod'
    else:
        return render_template('approve_page.html', 
                               error='This application has already been fully processed.', app=app)

    # Guards
    if role == 'supervisor':
        if app['supervisor_status'] != 'pending':
            return render_template('approve_page.html', error='Already actioned by Supervisor', app=app)
    elif role == 'hod':
        if app['supervisor_status'] != 'approved':
            return render_template('approve_page.html', error='Supervisor has not approved yet', app=app)
        if app['hod_status'] != 'pending':
            return render_template('approve_page.html', error='Already actioned by HoD', app=app)

    # Rejection requires remark
    if decision == 'rejected' and not request.args.get('remark'):
        return render_template('approve_page.html', app=app, role=role,
                               need_remark=True, decision=decision)

    remark = request.args.get('remark', '')
    result = _process_decision(apps, app, role, decision, remark)
    return render_template('approve_page.html', app=app, role=role,
                           decision=decision, done=True)

# ── Admin Dashboard ───────────────────────────────────────────────────────────
@app.route('/admin/dashboard')
def admin_dashboard():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return redirect('/login')
    return render_template('admin_dashboard.html')

@app.route('/api/admin/stats')
def admin_stats():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    students = read_csv('student_registry.csv')
    faculty  = read_csv('faculty_registry.csv')
    apps     = [a for a in read_csv('applications.csv') if a.get('is_draft','0') == '0']
    return jsonify(success=True, stats={
        'total_students': len(students),
        'total_faculty':  len(faculty),
        'pending':  len([a for a in apps if a['status'] == 'pending']),
        'approved': len([a for a in apps if a['status'] == 'approved']),
        'rejected': len([a for a in apps if a['status'] == 'rejected']),
        'medical':  len([a for a in apps if a['type'] == 'medical']),
        'casual':   len([a for a in apps if a['type'] == 'casual']),
    })

@app.route('/api/admin/users')
def admin_users():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    return jsonify(success=True,
                   students=read_csv('student_registry.csv'),
                   faculty=read_csv('faculty_registry.csv'))

@app.route('/api/admin/user/disable', methods=['POST'])
def admin_disable():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    d     = request.json
    email = d.get('email'); utype = d.get('utype','student')
    f     = 'student_registry.csv' if utype == 'student' else 'faculty_registry.csv'
    fields = ['Name','RollNumber','OfficialEmail','MobileNumber','PasswordHash'] if utype=='student' \
             else ['Name','EmployeeID','OfficialEmail','MobileNumber','PasswordHash','Role']
    rows = read_csv(f)
    for r in rows:
        if r['OfficialEmail'] == email:
            r['PasswordHash'] = 'DISABLED'
    write_csv(f, rows, fields)
    return jsonify(success=True, message='Account disabled')

@app.route('/api/admin/reset-password', methods=['POST'])
def admin_reset():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    d = request.json
    email = d.get('email'); new_pw = d.get('password',''); utype = d.get('utype','student')
    f      = 'student_registry.csv' if utype == 'student' else 'faculty_registry.csv'
    fields = ['Name','RollNumber','OfficialEmail','MobileNumber','PasswordHash'] if utype=='student' \
             else ['Name','EmployeeID','OfficialEmail','MobileNumber','PasswordHash','Role']
    rows = read_csv(f)
    for r in rows:
        if r['OfficialEmail'] == email:
            r['PasswordHash'] = hash_pw(new_pw)
    write_csv(f, rows, fields)
    activity_log('Admin', 'Admin', f'Reset password for {email}')
    return jsonify(success=True, message='Password reset')

@app.route('/api/admin/applications')
def admin_applications():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    apps = [a for a in read_csv('applications.csv') if a.get('is_draft','0') == '0']
    return jsonify(success=True, applications=apps)

@app.route('/api/admin/logs')
def admin_logs():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    return jsonify(success=True, logs=read_csv('activity_log.csv'))

@app.route('/api/admin/admin-changes-log')
def admin_changes_log():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    return jsonify(success=True, logs=read_csv('admin_changes_log.csv'))

def _filter_leave_records(records, roll=None, date_from=None, date_to=None, leave_type=None):
    """Filter approved leave records by roll, overlapping date range, and/or leave type."""
    filtered = []
    for r in records:
        if roll and roll.lower() not in r.get('roll','').lower():
            continue
        if leave_type and r.get('leave_type','') != leave_type:
            continue
        if date_from or date_to:
            try:
                rf = datetime.strptime(r.get('from_date',''), '%Y-%m-%d').date()
                rt = datetime.strptime(r.get('to_date',''), '%Y-%m-%d').date()
                df = datetime.strptime(date_from, '%Y-%m-%d').date() if date_from else date.min
                dt = datetime.strptime(date_to, '%Y-%m-%d').date() if date_to else date.max
                if rt < df or rf > dt:
                    continue
            except ValueError:
                continue
        filtered.append(r)
    return filtered

@app.route('/api/admin/leave-records')
def admin_leave_records():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    records = read_all_leave_records()
    roll = request.args.get('roll','').strip()
    date_from = request.args.get('from','').strip()
    date_to = request.args.get('to','').strip()
    leave_type = request.args.get('type','').strip()
    records = _filter_leave_records(records, roll or None, date_from or None, date_to or None, leave_type or None)
    records.sort(key=lambda x: x.get('approved_at',''), reverse=True)
    return jsonify(success=True, records=records)

@app.route('/api/admin/leave-records/export.csv')
def admin_export_leave_records():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    records = read_all_leave_records()
    roll = request.args.get('roll','').strip()
    date_from = request.args.get('from','').strip()
    date_to = request.args.get('to','').strip()
    leave_type = request.args.get('type','').strip()
    records = _filter_leave_records(records, roll or None, date_from or None, date_to or None, leave_type or None)
    records.sort(key=lambda x: x.get('approved_at',''), reverse=True)
    si = io.StringIO()
    w = csv.DictWriter(si, fieldnames=ALL_LEAVE_FIELDS)
    w.writeheader(); w.writerows(records)
    filename = f"leave_records_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(si.getvalue(), mimetype='text/csv',
                    headers={"Content-Disposition": f"attachment; filename={filename}"})

@app.route('/api/admin/final-records')
def admin_final_records():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    return jsonify(success=True, records=read_final_leave_records())

@app.route('/api/admin/calendar')
def admin_calendar():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    month = request.args.get('month','').strip()
    if not month:
        month = date.today().strftime('%Y-%m')
    try:
        year, mon = int(month.split('-')[0]), int(month.split('-')[1])
        start = date(year, mon, 1)
        if mon == 12:
            end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(year, mon + 1, 1) - timedelta(days=1)
    except Exception:
        return jsonify(success=False, message='Invalid month format. Use YYYY-MM')

    # Approved leave records overlapping the month
    leaves = []
    for r in read_all_leave_records():
        try:
            rf = datetime.strptime(r.get('from_date',''), '%Y-%m-%d').date()
            rt = datetime.strptime(r.get('to_date',''), '%Y-%m-%d').date()
            if rt < start or rf > end:
                continue
            leaves.append({
                'id': r['id'], 'roll': r['roll'], 'name': r['name'],
                'leave_type': r['leave_type'], 'from_date': r['from_date'], 'to_date': r['to_date'],
                'days': r['days'], 'status': 'approved'
            })
        except ValueError:
            continue

    # Pending applications overlapping the month
    for a in read_csv('applications.csv'):
        if a.get('is_draft','0') == '1' or a.get('status','') in ('approved','rejected'):
            continue
        try:
            rf = datetime.strptime(a.get('from_date',''), '%Y-%m-%d').date()
            rt = datetime.strptime(a.get('to_date',''), '%Y-%m-%d').date()
            if rt < start or rf > end:
                continue
            leaves.append({
                'id': a['id'], 'roll': a['roll'], 'name': a['name'],
                'leave_type': a['type'], 'from_date': a['from_date'], 'to_date': a['to_date'],
                'days': a['days'], 'status': a['status']
            })
        except ValueError:
            continue

    return jsonify(success=True, month=month, start=start.isoformat(), end=end.isoformat(), leaves=leaves)

@app.route('/api/admin/leave-record/delete', methods=['POST'])
def admin_delete_leave_record():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    d = request.json
    record_id = d.get('id')
    records = read_all_leave_records()
    target = next((r for r in records if r['id'] == record_id), None)
    if not target:
        return jsonify(success=False, message='Record not found')
    roll = target['roll']
    old_values = '|'.join(f"{k}={target.get(k,'')}" for k in ALL_LEAVE_FIELDS)
    records = [r for r in records if r['id'] != record_id]
    write_all_leave_records(records)
    recalculate_final_record(roll)
    log_admin_change(u.get('email',''), 'DELETE_LEAVE_RECORD', record_id, roll, old_values, '')
    activity_log('Admin', 'Admin', f'Deleted leave record {record_id}')
    return jsonify(success=True, message='Record deleted and balance recalculated')

@app.route('/api/admin/leave-record/update', methods=['POST'])
def admin_update_leave_record():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    d = request.json
    record_id = d.get('id')
    records = read_all_leave_records()
    target = next((r for r in records if r['id'] == record_id), None)
    if not target:
        return jsonify(success=False, message='Record not found')
    old_roll = target['roll']
    old_values = '|'.join(f"{k}={target.get(k,'')}" for k in ['name','email','leave_type','from_date','to_date','days','reason','semester','approved_by','roll'])
    # Update allowed fields
    for field in ['name','email','leave_type','from_date','to_date','days','reason','semester','approved_by']:
        if field in d:
            target[field] = str(d[field])
    new_roll = target['roll']
    new_values = '|'.join(f"{k}={target.get(k,'')}" for k in ['name','email','leave_type','from_date','to_date','days','reason','semester','approved_by','roll'])
    write_all_leave_records(records)
    # Recalculate for both old and new roll in case roll changed
    recalculate_final_record(old_roll)
    if new_roll != old_roll:
        recalculate_final_record(new_roll)
    log_admin_change(u.get('email',''), 'UPDATE_LEAVE_RECORD', record_id, new_roll, old_values, new_values)
    activity_log('Admin', 'Admin', f'Updated leave record {record_id}')
    return jsonify(success=True, message='Record updated and balance recalculated')

def _backup_and_clear_csv(filename, fields):
    """Copy current CSV to deleted_records, then write header-only empty file."""
    src = csv_path(filename)
    if os.path.exists(src):
        ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        dest = os.path.join(DELETED_DIR, f"{os.path.splitext(filename)[0]}_{ts}.csv")
        shutil.copy2(src, dest)
    write_csv(filename, [], fields)
    return True

@app.route('/api/admin/reset/applications', methods=['POST'])
def admin_reset_applications():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    _backup_and_clear_csv('applications.csv', APPLICATION_FIELDS)
    log_admin_change(u.get('email',''), 'RESET_APPLICATIONS', '', '', '', '')
    activity_log('Admin', 'Admin', 'Reset applications CSV')
    return jsonify(success=True, message='Applications reset and backup saved')

@app.route('/api/admin/reset/leave-records', methods=['POST'])
def admin_reset_leave_records():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    _backup_and_clear_csv('all_leave_records.csv', ALL_LEAVE_FIELDS)
    # Also clear final records since they derive from leave records
    _backup_and_clear_csv('final_leave_records.csv', FINAL_LEAVE_FIELDS)
    log_admin_change(u.get('email',''), 'RESET_LEAVE_RECORDS', '', '', '', '')
    activity_log('Admin', 'Admin', 'Reset leave records CSV')
    return jsonify(success=True, message='Leave records reset and backup saved')

@app.route('/api/admin/reset/final-records', methods=['POST'])
def admin_reset_final_records():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    _backup_and_clear_csv('final_leave_records.csv', FINAL_LEAVE_FIELDS)
    log_admin_change(u.get('email',''), 'RESET_FINAL_RECORDS', '', '', '', '')
    activity_log('Admin', 'Admin', 'Reset final records CSV')
    return jsonify(success=True, message='Final records reset and backup saved')

@app.route('/api/admin/admins')
def admin_list():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    return jsonify(success=True, admins=read_csv('admin.csv'))

@app.route('/api/admin/create', methods=['POST'])
def admin_create():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    d = request.json
    name  = d.get('name','').strip()
    email = d.get('email','').strip().lower()
    phone = d.get('phone','').strip()
    pw    = d.get('password','')
    if not all([name, email, phone, pw]):
        return jsonify(success=False, message='All fields are required')
    if len(pw) < 6:
        return jsonify(success=False, message='Password must be at least 6 characters')
    admins = read_csv('admin.csv')
    if any(a['Email'].strip().lower() == email for a in admins):
        return jsonify(success=False, message='Email already registered')
    row = {'Email': email, 'PasswordHash': hash_pw(pw), 'Name': name, 'Phone': phone, 'Status': 'active'}
    append_csv('admin.csv', row, ADMIN_FIELDS)
    log_admin_change(u.get('email',''), 'CREATE_ADMIN', email, '', '', f"name={name}|phone={phone}")
    activity_log(u.get('name','Admin'), 'Admin', f'Created admin {email}')
    send_email(email, 'CAIR Portal – Admin Account Created',
               email_wrap('Admin Account', f'<p>Hi <strong>{name}</strong>,</p><p>An administrator account has been created for you. You can now log in at <a href="{APP_BASE_URL}/login?type=admin">{APP_BASE_URL}/login?type=admin</a>.</p>'))
    return jsonify(success=True, message='Admin created successfully')

@app.route('/api/admin/update-status', methods=['POST'])
def admin_update_status():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    d = request.json
    email = d.get('email','').strip().lower()
    status = d.get('status','')
    if status not in ('active','disabled'):
        return jsonify(success=False, message='Invalid status')
    if email == u.get('email','').lower() and status == 'disabled':
        return jsonify(success=False, message='Cannot disable your own account')
    rows = read_csv('admin.csv')
    found = False
    for r in rows:
        if r['Email'].strip().lower() == email:
            r['Status'] = status; found = True
    if not found: return jsonify(success=False, message='Admin not found')
    write_csv('admin.csv', rows, ADMIN_FIELDS)
    log_admin_change(u.get('email',''), 'UPDATE_ADMIN_STATUS', email, '', '', f"status={status}")
    activity_log(u.get('name','Admin'), 'Admin', f'Updated admin {status} {email}')
    return jsonify(success=True, message=f'Admin {status}')

@app.route('/api/admin/reset-admin-password', methods=['POST'])
def admin_reset_admin_password():
    u = session.get('user')
    if not u or u.get('role') != 'admin': return jsonify(success=False), 403
    d = request.json
    email = d.get('email','').strip().lower()
    pw    = d.get('password','')
    if len(pw) < 6:
        return jsonify(success=False, message='Password must be at least 6 characters')
    rows = read_csv('admin.csv')
    found = False
    for r in rows:
        if r['Email'].strip().lower() == email:
            r['PasswordHash'] = hash_pw(pw); found = True
    if not found: return jsonify(success=False, message='Admin not found')
    write_csv('admin.csv', rows, ADMIN_FIELDS)
    log_admin_change(u.get('email',''), 'RESET_ADMIN_PASSWORD', email, '', '', '')
    activity_log(u.get('name','Admin'), 'Admin', f'Reset password for admin {email}')
    return jsonify(success=True, message='Admin password reset')

# ── Pending approval reminders ────────────────────────────────────────────────
def send_pending_reminders():
    """Hourly job: remind supervisors/HoD pending > 24h, at most once per app+role."""
    with app.app_context():
        try:
            apps = read_csv('applications.csv')
            reminder_log = read_csv('reminder_log.csv')
            already_sent = set((r['application_id'], r['role']) for r in reminder_log)
            cutoff = datetime.now() - timedelta(hours=24)
            for a in apps:
                if a.get('is_draft','0') == '1' or a.get('status','') in ('approved','rejected','explanation'):
                    continue
                try:
                    submitted = datetime.fromisoformat(a.get('submitted_at',''))
                except ValueError:
                    continue
                if submitted > cutoff:
                    continue
                for role in ['supervisor','hod']:
                    status_key = f"{role}_status"
                    if a.get(status_key,'') != 'pending':
                        continue
                    if role == 'hod' and a.get('supervisor_status','') != 'approved':
                        continue
                    if (a['id'], role) in already_sent:
                        continue
                    recipient = FACULTY.get(a[role] if role == 'supervisor' else a['hod'], {}).get('email')
                    if not recipient:
                        continue
                    ok = send_email(recipient, f"[REMINDER] Leave {a['id']} – {a['name']}",
                                    reminder_email(a, role),
                                    reply_to=a.get('email'))
                    log_reminder(a['id'], role, recipient, 'sent' if ok else 'failed')
        except Exception as e:
            print('Reminder job error:', e)

scheduler = BackgroundScheduler()
scheduler.add_job(send_pending_reminders, 'interval', hours=1, id='pending_reminders', replace_existing=True)
# Start scheduler only in the actual app process, not the Flask reloader parent
if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or 'WERKZEUG_RUN_MAIN' not in os.environ:
    scheduler.start()

if __name__ == '__main__':
    try:
        app.run(debug=True, host='0.0.0.0', port=5000)
    finally:
        scheduler.shutdown()