import csv
from collections import OrderedDict

# Read all apps
with open('csv/applications.csv', newline='', encoding='utf-8') as f:
    apps = list(csv.DictReader(f))

# Find duplicates
seen = {}
dups = []
for i, a in enumerate(apps):
    key = (a['id'], a['roll'])
    if key in seen:
        dups.append((i, a['id'], a['roll']))
    else:
        seen[key] = i

if dups:
    print("Duplicate IDs found:", dups)
    # Reassign IDs for duplicates
    prefix = 'CAIR-2026-'
    max_num = max(int(a['id'].split('-')[-1]) for a in apps if a['id'].startswith(prefix))
    for idx, aid, roll in dups:
        max_num += 1
        old_id = apps[idx]['id']
        apps[idx]['id'] = f'{prefix}{max_num:04d}'
        print(f"Fixed: {old_id} -> {apps[idx]['id']} (roll: {roll})")

# Write back
with open('csv/applications.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=apps[0].keys())
    w.writeheader()
    w.writerows(apps)