INSTITUTE ATTENDANCE SYSTEM - CSV + BATCH-WISE EDITION

IMPORTANT
- This edition imports students from CSV. It does NOT read .accdb directly.
- No Microsoft Access ODBC driver and no pyodbc are required.
- 64-bit Python is fine. Keep Office 2007 installed; do not uninstall it.

INSTALL / RUN
1. Extract this ZIP into a normal folder, e.g. C:\Institute_Attendance_System.
2. Double-click run_windows.bat. It creates a local Python virtual environment and installs Flask.
3. Open http://127.0.0.1:5000 on the server computer.
4. First run: create the admin password.
5. Admin: open /admin, export CSV from Access with headers batch-no, batch_nm, stu_name, then upload it.
6. CSV batch_nm values automatically group students into batches. Different batch_nm values appear as separate batches.
7. Students open /student/login, choose their batch, and see the complete batch roster. No student PIN is required.

STUDENT VIEW
- Students choose a batch and see all students in that batch with today's status.
- Each row has Present / Absent / Leave MSG controls; a student finds their own name and submits attendance.
- Students can correct records for today. Past dates remain admin-only.
- IMPORTANT: With no PIN/login, the system cannot verify identity. Anyone with access to the batch page can submit for any student. Use on a supervised institute device/network.

ADMIN VIEW
- Admin can view all batches and today's batch-wise totals.
- Admin can import/update students from CSV, edit attendance and view date-range reports.
- CSV importer reads only batch-no, batch_nm, stu_name; other columns are ignored.
- Existing batch-no updates name and batch automatically. Identical duplicate rows are skipped.
- If one batch-no has conflicting details within the same CSV, that batch-no is skipped for review.
- Students missing from a later CSV are NOT deleted. Existing attendance history is preserved; any old PIN table is ignored by this PIN-free student flow.

DATA SAFETY
- Original .accdb is not uploaded to or modified by this application.
- Attendance and student data are stored separately in data/attendance.sqlite3.
- Back up the data folder regularly. To preserve existing data during an upgrade, copy the old data/attendance.sqlite3 into the new project's data folder BEFORE first launch.

LAN USE
- Run the app on one trusted institute computer. Other devices on the same LAN open http://SERVER-LAN-IP:5000.
- Do not expose port 5000 to the public internet.
- Change ATTENDANCE_SECRET to a long random secret for a production deployment.

This project has been syntax-checked, but should be tested with a small CSV and a backup before real student use.
