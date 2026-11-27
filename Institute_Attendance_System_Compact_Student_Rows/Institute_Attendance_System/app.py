import os, sqlite3, secrets, hashlib, hmac, datetime, re, csv, io
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.utils import secure_filename

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'data', 'attendance.sqlite3')
app_max_upload = 100 * 1024 * 1024
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = app_max_upload
app.secret_key = os.environ.get('ATTENDANCE_SECRET', 'change-this-secret-before-production-' + secrets.token_hex(16))


def db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA journal_mode=WAL')
    return con


def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS students (
            batch_no TEXT PRIMARY KEY, batch_nm TEXT NOT NULL, stu_name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, synced_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_no TEXT NOT NULL, day TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PRE','ABS','LEAVE MSG')),
            leave_msg TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            UNIQUE(batch_no, day),
            FOREIGN KEY(batch_no) REFERENCES students(batch_no)
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, batch_no TEXT NOT NULL,
            day TEXT NOT NULL, old_status TEXT, new_status TEXT NOT NULL,
            changed_at TEXT NOT NULL, changed_by TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('admin_hash','')")
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('admin_salt','')")


def now(): return datetime.datetime.now().isoformat(timespec='seconds')
def today(): return datetime.date.today().isoformat()
def hash_pin(pin, salt): return hashlib.pbkdf2_hmac('sha256', pin.encode(), salt.encode(), 240000).hex()
def get_setting(key):
    with db() as c:
        r=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return r['value'] if r else ''
def set_setting(key, value):
    with db() as c: c.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(key,value))
def admin_ready(): return bool(get_setting('admin_hash'))
def admin_required(fn):
    @wraps(fn)
    def wrap(*a,**kw):
        if not session.get('admin'): return redirect(url_for('admin_login'))
        return fn(*a,**kw)
    return wrap

def student_required(fn):
    @wraps(fn)
    def wrap(*a,**kw):
        if not session.get('student_batch'): return redirect(url_for('student_login'))
        return fn(*a,**kw)
    return wrap

@app.route('/')
def home():
    with db() as c:
        rows=c.execute('''SELECT s.batch_no,s.batch_nm,s.stu_name,a.status,a.leave_msg
          FROM students s LEFT JOIN attendance a ON a.batch_no=s.batch_no AND a.day=?
          WHERE s.active=1 ORDER BY s.batch_nm,s.stu_name''',(today(),)).fetchall()
    present=sum(1 for r in rows if r['status']=='PRE')
    absent=sum(1 for r in rows if r['status']=='ABS')
    leave=sum(1 for r in rows if r['status']=='LEAVE MSG')
    return render_template('home.html', rows=rows, present=present, absent=absent, leave=leave, day=today())

@app.route('/student/login', methods=['GET','POST'])
def student_login():
    with db() as c:
        batches=c.execute("SELECT batch_nm,COUNT(*) total FROM students WHERE active=1 GROUP BY batch_nm ORDER BY batch_nm").fetchall()
    if request.method=='POST':
        batch=request.form.get('batch_nm','').strip()
        if any(b['batch_nm']==batch for b in batches):
            session['student_batch']=batch
            return redirect(url_for('student_mark'))
        flash('Please choose a valid batch.')
    return render_template('student_login.html', batches=batches)

def table_exists(c,name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None

@app.route('/student/mark', methods=['GET','POST'])
def student_mark():
    batch=session.get('student_batch')
    if not batch: return redirect(url_for('student_login'))
    if request.method=='POST':
        student_no=request.form.get('batch_no','').strip()
        status=request.form.get('status','')
        msg=request.form.get('leave_msg','').strip()[:300]
        if status not in ('PRE','ABS','LEAVE MSG'):
            flash('Choose Present, Absent or Leave Message.')
        else:
            with db() as c:
                belongs=c.execute('SELECT 1 FROM students WHERE batch_no=? AND batch_nm=? AND active=1',(student_no,batch)).fetchone()
                if not belongs: abort(403)
                old=c.execute('SELECT status FROM attendance WHERE batch_no=? AND day=?',(student_no,today())).fetchone()
                c.execute("""INSERT INTO attendance(batch_no,day,status,leave_msg,updated_at,updated_by)
                  VALUES(?,?,?,?,?,?) ON CONFLICT(batch_no,day) DO UPDATE SET status=excluded.status,
                  leave_msg=excluded.leave_msg,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
                  (student_no,today(),status,msg,now(),'SELF-SERVICE'))
                c.execute('INSERT INTO audit_log(batch_no,day,old_status,new_status,changed_at,changed_by) VALUES(?,?,?,?,?,?)',
                  (student_no,today(),old['status'] if old else None,status,now(),'SELF-SERVICE'))
            flash('Attendance saved. It can be corrected again today.')
        return redirect(url_for('student_mark'))
    with db() as c:
        roster=c.execute("SELECT s.batch_no,s.batch_nm,s.stu_name,a.status,a.leave_msg FROM students s LEFT JOIN attendance a ON a.batch_no=s.batch_no AND a.day=? WHERE s.active=1 AND s.batch_nm=? ORDER BY s.stu_name",(today(),batch)).fetchall()
    if not roster:
        session.pop('student_batch',None)
        flash('No students found in that batch. Please choose again.')
        return redirect(url_for('student_login'))
    return render_template('student_mark.html', batch_nm=batch, roster=roster)

@app.route('/student/logout')
def student_logout():
    session.pop('student_batch',None)
    return redirect(url_for('student_login'))

@app.route('/admin/setup', methods=['GET','POST'])
def admin_setup():
    if admin_ready(): return redirect(url_for('admin_login'))
    if request.method=='POST':
        p=request.form.get('password','')
        if len(p)<10: flash('Choose an admin password of at least 10 characters.')
        else:
            salt=secrets.token_hex(16); set_setting('admin_salt',salt); set_setting('admin_hash',hash_pin(p,salt))
            flash('Admin account created. Please sign in.'); return redirect(url_for('admin_login'))
    return render_template('admin_setup.html')

@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if not admin_ready(): return redirect(url_for('admin_setup'))
    if request.method=='POST':
        p=request.form.get('password',''); salt=get_setting('admin_salt')
        if hmac.compare_digest(hash_pin(p,salt),get_setting('admin_hash')):
            session.clear(); session['admin']=True; return redirect(url_for('admin'))
        flash('Incorrect admin password.')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout(): session.clear(); return redirect(url_for('home'))

@app.route('/admin')
@admin_required
def admin():
    with db() as c:
        counts=c.execute('SELECT COUNT(*) n FROM students WHERE active=1').fetchone()['n']
        rows=c.execute('''SELECT s.batch_no,s.batch_nm,s.stu_name,a.status,a.leave_msg,a.updated_at
          FROM students s LEFT JOIN attendance a ON a.batch_no=s.batch_no AND a.day=?
          WHERE s.active=1 ORDER BY s.batch_nm,s.stu_name''',(today(),)).fetchall()
        batches=c.execute("SELECT batch_nm,COUNT(*) total,SUM(CASE WHEN a.status='PRE' THEN 1 ELSE 0 END) present,SUM(CASE WHEN a.status='ABS' THEN 1 ELSE 0 END) absent,SUM(CASE WHEN a.status='LEAVE MSG' THEN 1 ELSE 0 END) leave_count FROM students s LEFT JOIN attendance a ON a.batch_no=s.batch_no AND a.day=? WHERE s.active=1 GROUP BY batch_nm ORDER BY batch_nm",(today(),)).fetchall()
    return render_template('admin.html',rows=rows,total=counts,day=today(),batches=batches)

@app.route('/admin/import-csv', methods=['POST'])
@admin_required
def import_csv():
    uploaded=request.files.get('csv_file')
    if not uploaded or not uploaded.filename or not uploaded.filename.lower().endswith('.csv'):
        flash('Choose a CSV file.'); return redirect(url_for('admin'))
    try:
        reader=csv.DictReader(io.StringIO(uploaded.read().decode('utf-8-sig')))
        if not reader.fieldnames: raise ValueError('CSV header row is missing.')
        headers={h.strip().lower():h for h in reader.fieldnames if h}
        required=['batch-no','batch_nm','stu_name']
        if any(k not in headers for k in required): raise ValueError('Required columns: batch-no, batch_nm, stu_name.')
        clean={}; conflicts=set(); duplicates=invalid=0
        for row in reader:
            item={k:(row.get(headers[k]) or '').strip() for k in required}
            if not all(item.values()): invalid+=1; continue
            key=item['batch-no']
            if key in clean:
                if clean[key] != item: conflicts.add(key)
                else: duplicates+=1
            else: clean[key]=item
        for key in conflicts: clean.pop(key,None)
        added=updated=0
        with db() as c:
            for item in clean.values():
                exists=c.execute('SELECT 1 FROM students WHERE batch_no=?',(item['batch-no'],)).fetchone()
                c.execute("INSERT INTO students(batch_no,batch_nm,stu_name,active,synced_at) VALUES(?,?,?,1,?) ON CONFLICT(batch_no) DO UPDATE SET batch_nm=excluded.batch_nm,stu_name=excluded.stu_name,active=1,synced_at=excluded.synced_at",(item['batch-no'],item['batch_nm'],item['stu_name'],now()))
                if exists: updated+=1
                else: added+=1
        flash(f'CSV import complete: {added} added, {updated} updated, {duplicates} duplicates skipped, {len(conflicts)} conflicting batch numbers skipped, {invalid} incomplete rows skipped. Attendance history preserved.')
    except Exception as e:
        flash('CSV import failed: '+str(e))
    return redirect(url_for('admin'))

@app.route('/admin/batch/<path:batch_nm>')
@admin_required
def admin_batch(batch_nm):
    day=request.args.get('day', today())
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day): day=today()
    with db() as c:
        rows=c.execute('''SELECT s.batch_no,s.batch_nm,s.stu_name,a.status,a.leave_msg,a.updated_at
          FROM students s LEFT JOIN attendance a ON a.batch_no=s.batch_no AND a.day=?
          WHERE s.active=1 AND s.batch_nm=? ORDER BY s.stu_name''',(day,batch_nm)).fetchall()
    summary = {k: sum(1 for r in rows if r['status']==v) for k,v in [('present','PRE'),('absent','ABS'),('leave','LEAVE MSG')]}
    summary['pending'] = sum(1 for r in rows if not r['status'])
    return render_template('admin_batch.html', batch_nm=batch_nm, day=day, rows=rows, summary=summary)

@app.route('/admin/attendance/<batch>/<day>', methods=['POST'])
@admin_required
def admin_update(batch,day):
    status=request.form.get('status','')
    if status not in ('PRE','ABS','LEAVE MSG') or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day): abort(400)
    msg=request.form.get('leave_msg','').strip()[:300]
    with db() as c:
        old=c.execute('SELECT status FROM attendance WHERE batch_no=? AND day=?',(batch,day)).fetchone()
        c.execute('''INSERT INTO attendance(batch_no,day,status,leave_msg,updated_at,updated_by) VALUES(?,?,?,?,?, 'ADMIN')
          ON CONFLICT(batch_no,day) DO UPDATE SET status=excluded.status,leave_msg=excluded.leave_msg,
          updated_at=excluded.updated_at,updated_by='ADMIN' ''',(batch,day,status,msg,now()))
        c.execute('INSERT INTO audit_log(batch_no,day,old_status,new_status,changed_at,changed_by) VALUES(?,?,?,?,?,?)',
          (batch,day,old['status'] if old else None,status,now(),'ADMIN'))
    flash('Attendance updated.')
    return redirect(url_for('admin'))

@app.route('/admin/reports')
@admin_required
def reports():
    start=request.args.get('start',today()[:7]+'-01'); end=request.args.get('end',today())
    batch=request.args.get('batch','').strip()
    with db() as c:
        batches=c.execute('SELECT DISTINCT batch_nm FROM students WHERE active=1 ORDER BY batch_nm').fetchall()
        sql='''SELECT a.day,s.batch_no,s.batch_nm,s.stu_name,a.status,a.leave_msg,a.updated_at,a.updated_by
          FROM attendance a JOIN students s ON s.batch_no=a.batch_no WHERE a.day BETWEEN ? AND ?'''
        args=[start,end]
        if batch:
            sql += ' AND s.batch_nm=?'; args.append(batch)
        sql += ' ORDER BY a.day DESC,s.batch_nm,s.stu_name'
        rows=c.execute(sql,args).fetchall()
    return render_template('reports.html',rows=rows,start=start,end=end,batches=batches,selected_batch=batch)

@app.route('/admin/student-report/<path:batch_no>')
@admin_required
def student_report(batch_no):
    start=request.args.get('start',today()[:7]+'-01'); end=request.args.get('end',today())
    with db() as c:
        student=c.execute('SELECT batch_no,batch_nm,stu_name FROM students WHERE batch_no=?',(batch_no,)).fetchone()
        if not student: abort(404)
        rows=c.execute('''SELECT day,status,leave_msg,updated_at,updated_by FROM attendance
          WHERE batch_no=? AND day BETWEEN ? AND ? ORDER BY day DESC''',(batch_no,start,end)).fetchall()
    counts={'present':sum(1 for r in rows if r['status']=='PRE'),'absent':sum(1 for r in rows if r['status']=='ABS'),'leave':sum(1 for r in rows if r['status']=='LEAVE MSG')}
    return render_template('student_report.html',student=student,rows=rows,start=start,end=end,counts=counts)

@app.route('/health')
def health(): return {'status':'ok','date':today()}

init_db()
if __name__=='__main__':
    # LAN access: other devices on the same network can connect to this host.
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT','5000')), debug=False, threaded=True)
