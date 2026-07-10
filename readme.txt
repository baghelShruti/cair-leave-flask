cd "c:\Users\LENOVO\Downloads\CAIR Leave  application"
source venv/Scripts/activate
python app.py

Notes:
- Only students register and log in through the web portal.
- Faculty approve/reject leave requests directly via the action buttons in email notifications — no web login required.
- First admin registers at: /register/admin (only available when no admin exists)
- Admin login at: /login?type=admin
  Default admin email: admin@iitmandi.ac.in
  Default admin password: admin123
- Existing admin can create more admins from the Admin Dashboard → Admins tab.

SMTP_USERNAME = " "  # CHANGE THIS
SMTP_PASSWORD = "   "              # CHANGE THIS
