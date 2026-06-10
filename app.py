from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash, Response
from database import get_db_connection, query_db
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, time, timedelta
import csv
import io
import json
import psycopg2.extras
from psycopg2.extras import RealDictCursor
from pdf_curriculum_parser import parse_curriculum_pdf
from docx_curriculum_parser import parse_curriculum_docx
from pdf_faculty_parser import parse_faculty_pdf
from docx_faculty_parser import parse_faculty_docx

# One-time idempotent migration: ensure schedule_version.source column exists
_source_col_ensured = False
def _ensure_source_col(cur):
    global _source_col_ensured
    if not _source_col_ensured:
        cur.execute(
            "ALTER TABLE public.schedule_version "
            "ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'official'"
        )
        _source_col_ensured = True

# One-time idempotent migration: ensure schedule_version.original_status column exists.
# Tracks whether a row was created as 'Draft' or 'Published' — survives archiving.
_original_status_col_ensured = False
def _ensure_original_status_col(cur):
    global _original_status_col_ensured
    if not _original_status_col_ensured:
        cur.execute(
            "ALTER TABLE public.schedule_version "
            "ADD COLUMN IF NOT EXISTS original_status VARCHAR(20)"
        )
        # Pass 1: still-active rows — their current status IS their original status
        cur.execute("""
            UPDATE public.schedule_version
            SET    original_status = status
            WHERE  original_status IS NULL AND status IN ('Draft', 'Published')
        """)
        # Pass 2: archived rows that share a version_number+semesterid with a row
        # that already has original_status (e.g. new rows in the same snapshot group)
        cur.execute("""
            UPDATE public.schedule_version sv1
            SET    original_status = (
                SELECT MAX(sv2.original_status)
                FROM   public.schedule_version sv2
                JOIN   public.schedule sg2 ON sv2.scheduleid = sg2.scheduleid
                JOIN   public.schedule sg1 ON sv1.scheduleid = sg1.scheduleid
                WHERE  sv2.version_number  = sv1.version_number
                  AND  sg2.semesterid      = sg1.semesterid
                  AND  sv2.original_status IS NOT NULL
            )
            WHERE  sv1.original_status IS NULL AND sv1.status = 'Archive'
        """)
        # Pass 3: any still-null rows (pre-migration legacy data) default to 'Draft'
        cur.execute("""
            UPDATE public.schedule_version
            SET    original_status = 'Draft'
            WHERE  original_status IS NULL
        """)
        _original_status_col_ensured = True

# One-time idempotent migration: create local_arrangement, local_arrangement_sessions,
# and schedule_exception_log tables if they don't exist yet.
# Uses its OWN connection + commit so the DDL is durable regardless of the caller's
# transaction state (early-returns, rollbacks, etc. cannot leave the flag True but
# the tables absent).
_local_tables_ensured = False
def _ensure_local_tables(cur=None):   # cur param kept for backward-compat but not used
    global _local_tables_ensured
    if _local_tables_ensured:
        return
    _conn = get_db_connection()
    _cur  = _conn.cursor()
    try:
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS public.local_arrangement (
                arrangementid  SERIAL PRIMARY KEY,
                description    VARCHAR(200),
                programcode    VARCHAR(20),
                yearlevel      INT,
                semesterid     INT,
                ref_versionid  INT,
                has_hc_violation BOOLEAN DEFAULT FALSE,
                violated_rules   JSONB   DEFAULT '[]'::jsonb,
                override_reason  TEXT,
                is_active        BOOLEAN DEFAULT TRUE,
                created_by       VARCHAR(100),
                created_at       TIMESTAMP DEFAULT NOW()
            )
        """)
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS public.local_arrangement_sessions (
                sessionid              SERIAL PRIMARY KEY,
                arrangementid          INT REFERENCES public.local_arrangement(arrangementid),
                subjectcode            VARCHAR(50),
                daydesc                VARCHAR(20),
                starttimeid            INT,
                endtimeid              INT,
                roomid                 INT,
                faculty_employeenumber VARCHAR(50)
            )
        """)
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS public.schedule_exception_log (
                logid            SERIAL PRIMARY KEY,
                source_type      VARCHAR(30) NOT NULL,
                source_requestid INT         DEFAULT NULL,
                arrangementid    INT         DEFAULT NULL,
                violated_rules   JSONB       NOT NULL DEFAULT '[]'::jsonb,
                approved_by      VARCHAR(100) NOT NULL,
                approved_at      TIMESTAMP   DEFAULT NOW(),
                semesterid       INT
            )
        """)
        # Tracks Official slots that were vacated by a Local Arrangement override.
        # Subjects in this table are "unscheduled" in the Local Scheduler context;
        # their Official Published sessions are excluded from room-conflict detection.
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS public.local_displaced_subjects (
                id              SERIAL PRIMARY KEY,
                programcode     VARCHAR(20)  NOT NULL,
                yearlevel       INT          NOT NULL,
                semesterid      INT          NOT NULL,
                subjectcode     VARCHAR(50)  NOT NULL,
                displaced_by    INT          REFERENCES public.local_arrangement(arrangementid),
                is_active       BOOLEAN      DEFAULT TRUE,
                displaced_at    TIMESTAMP    DEFAULT NOW(),
                UNIQUE (programcode, yearlevel, semesterid, subjectcode)
            )
        """)

        # status column for Draft → Published → Archived lifecycle
        _cur.execute("""
            ALTER TABLE public.local_arrangement
            ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'Draft'
        """)
        # Backfill: existing rows without explicit status → treat as Published
        _cur.execute("""
            UPDATE public.local_arrangement
            SET status = 'Published'
            WHERE status IS NULL OR status = 'Draft'
              AND is_active = TRUE
        """)
        _conn.commit()
        _local_tables_ensured = True
    except Exception:
        _conn.rollback()
        raise
    finally:
        _cur.close()
        _conn.close()

# ── Activity Log table ────────────────────────────────────────
_activity_log_ensured = False
def _ensure_activity_log_table():
    global _activity_log_ensured
    if _activity_log_ensured:
        return
    _conn = get_db_connection()
    _cur  = _conn.cursor()
    try:
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS public.activity_log (
                logid        SERIAL PRIMARY KEY,
                logtime      TIMESTAMP DEFAULT NOW(),
                action       VARCHAR(100) NOT NULL,
                details      TEXT,
                initiated_by VARCHAR(100),
                category     VARCHAR(50)  DEFAULT 'system',
                log_color    VARCHAR(20)  DEFAULT 'gray'
            )
        """)
        _conn.commit()
        _activity_log_ensured = True
    except Exception:
        _conn.rollback()
    finally:
        _cur.close()
        _conn.close()

# ─────────────────────────────────────────────────────────────
#  RANDOM FOREST DSS  (Manual Scheduling — see rf_dss.py)
# ─────────────────────────────────────────────────────────────
from rf_dss import SKLEARN_OK as _SKLEARN_OK, train_rf_dss as _train_rf_dss
from rf_dss import rf_score_faculty as _rf_score_faculty, rf_score_room as _rf_score_room


# 1. INITIALIZE APP FIRST
app = Flask(__name__)
app.secret_key = 'pup_lopez_super_secret_key' # Required for Login Sessions

def write_activity_log(action, details, category='system', color='gray'):
    """Insert one row into activity_log. Never raises — logging must not break the caller."""
    _ensure_activity_log_table()
    try:
        _conn = get_db_connection()
        _cur  = _conn.cursor()
        user  = session.get('username', 'System')
        _cur.execute(
            "INSERT INTO activity_log (action, details, initiated_by, category, log_color)"
            " VALUES (%s, %s, %s, %s, %s)",
            (action, details, user, category, color)
        )
        _conn.commit()
        _cur.close(); _conn.close()
    except Exception:
        pass

# Category → dot colour mapping
_LOG_COLORS = {
    'calendar':   'purple',
    'settings':   'orange',
    'employee':   'blue',
    'approval':   'green',
    'schedule':   'green',
    'program':    'indigo',
    'curriculum': 'teal',
    'system':     'gray',
}

# --- CONTEXT PROCESSOR FOR LOGGED-IN USER DISPLAY NAME ---
@app.context_processor
def inject_user_display():
    try:
        username = session.get('username')
        if not username:
            return {'current_user_display': 'User'}
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT f.firstname, f.lastname
            FROM accounts a
            LEFT JOIN faculty f ON a.employeenumber = f.employeenumber
            WHERE a.username = %s
        """, (username,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if row and row.get('lastname'):
            return {'current_user_display': f"{row['firstname']} {row['lastname']}"}
        return {'current_user_display': username.title()}
    except:
        return {'current_user_display': session.get('username', 'User')}

# --- CONTEXT PROCESSOR FOR DYNAMIC ACADEMIC YEAR ---
@app.context_processor
def inject_active_period():
    try:
        today = date.today()
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. FIND the semester that SHOULD be active based on Today's Date
        cur.execute("""
            SELECT s.semesterid, s.semestertype, ay.academicyearid, ay.yearstart, ay.yearend
            FROM semester s 
            JOIN academicyear ay ON s.academicyearid = ay.academicyearid 
            WHERE %s BETWEEN s.semstartdate AND s.semenddate
            LIMIT 1
        """, (today,))
        actual = cur.fetchone()
        
        if actual:
            # 2. AUTO-SYNC: Set this specific semester as the ONLY active one
            cur.execute("UPDATE academicyear SET isactive = FALSE")
            cur.execute("UPDATE semester SET isactive = FALSE")
            cur.execute("UPDATE academicyear SET isactive = TRUE WHERE academicyearid = %s", (actual['academicyearid'],))
            cur.execute("UPDATE semester SET isactive = TRUE WHERE semesterid = %s", (actual['semesterid'],))
            conn.commit()
            
            ay_label = f"A.Y {actual['yearstart']} - {actual['yearend']}"
            s_type = actual['semestertype']
            sem_label = "1ST SEMESTER" if s_type == 'A' else "2ND SEMESTER" if s_type == 'B' else "SUMMER"
            ay_id = actual['academicyearid']
        else:
            # Fallback if today doesn't fall in any range
            cur.execute("""
                SELECT s.semestertype, ay.yearstart, ay.yearend, ay.academicyearid 
                FROM semester s JOIN academicyear ay ON s.academicyearid = ay.academicyearid 
                WHERE s.isactive = TRUE LIMIT 1
            """)
            res = cur.fetchone()
            if res:
                ay_label, ay_id = f"A.Y {res['yearstart']} - {res['yearend']}", res['academicyearid']
                sem_label = "1ST SEMESTER" if res['semestertype'] == 'A' else "2ND SEMESTER" if res['semestertype'] == 'B' else "SUMMER"
            else:
                ay_label, sem_label, ay_id = "NOT SET", "NOT SET", None
            
        cur.close(); conn.close()
        return {'current_ay_label': ay_label, 'current_sem_label': sem_label, 'current_ay_id': ay_id}
    except:
        return {'current_ay_label': "ERROR", 'current_sem_label': "ERROR", 'current_ay_id': None}

# --- 1. ROOT ROUTE ---
@app.route('/')
def index():
    return redirect(url_for('login'))

# --- 2. ONE-TIME SETUP ROUTE ---
@app.route('/setup')
def setup():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("TRUNCATE TABLE Accounts RESTART IDENTITY CASCADE")
        
        hashed = generate_password_hash('password123', method='pbkdf2:sha256')
        
        cur.execute("INSERT INTO Accounts (Username, PasswordHash, Role) VALUES (%s, %s, %s)", ('admin', hashed, 'Admin'))
        cur.execute("INSERT INTO Accounts (Username, PasswordHash, Role) VALUES (%s, %s, %s)", ('acadhead', hashed, 'Academic Head'))
        cur.execute("INSERT INTO Accounts (Username, PasswordHash, Role) VALUES (%s, %s, %s)", ('faculty', hashed, 'Faculty'))

        conn.commit()
        return "Setup Success! Login with password123"
    except Exception as e:
        return f"Error: {e}"
    finally:
        cur.close()
        conn.close()

# --- 3. AUTHENTICATION ROUTES ---
@app.route('/logout')
def logout():
    session.clear() 
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = str(request.form.get('username')).strip()
        password = str(request.form.get('password')).strip()
        role_selected = request.form.get('role')

        user = query_db("SELECT * FROM Accounts WHERE Username = %s", (username,), one=True)

        if user:
            user_data = {k.lower(): v for k, v in user.items()}
            db_password = str(user_data.get('passwordhash')).strip()
            db_role = user_data.get('role')

            if check_password_hash(db_password, password):
                if db_role == role_selected:
                    session.clear()
                    session['loggedin'] = True
                    session['username'] = user_data.get('username')
                    session['role'] = db_role
                    
                    if db_role == 'Admin':
                        return redirect(url_for('admin_dashboard'))
                    elif db_role == 'Academic Head':
                        return redirect(url_for('dashboard'))
                    elif db_role == 'Faculty':
                        return redirect(url_for('faculty_dashboard'))
                    else:
                        flash("Your role does not have an assigned dashboard.")
                        return redirect(url_for('login'))
                else:
                    flash(f"Role mismatch. You are registered as {db_role}.")
                    session['active_role'] = db_role
                    session['saved_username'] = username  # <-- SAVE USERNAME
            else:
                flash("Wrong password. Try again.")
                session['active_role'] = role_selected
                session['saved_username'] = username  # <-- SAVE USERNAME
        else:
            flash("Username not found!")
            session['active_role'] = role_selected
            session['saved_username'] = username  # <-- SAVE USERNAME (so they can fix typos)
        
        return redirect(url_for('login'))

    active_role = session.pop('active_role', 'Admin') 
    saved_username = session.pop('saved_username', '') # <-- GRAB THE SAVED USERNAME
    
    session.pop('loggedin', None)
    session.pop('username', None)
    session.pop('role', None)

    # Pass BOTH the active_role and saved_username to the HTML
    return render_template('login.html', active_role=active_role, saved_username=saved_username)

# ==============================================================================
# --- ACADEMIC HEAD PERSONAL FACULTY VIEW ---
# ==============================================================================
@app.route('/academic/my-dashboard')
def academic_my_dashboard():
    if 'loggedin' not in session or session.get('role') != 'Academic Head':
        return redirect(url_for('login'))
    
    username = session.get('username')
    today_day = datetime.now().strftime('%A')
    current_date_formatted = datetime.now().strftime('%B %d, %Y, %A')
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("SELECT employeenumber FROM accounts WHERE username = %s", (username,))
    acc = cur.fetchone()
    emp_num = acc['employeenumber'] if acc else None

    cur.execute("""
        SELECT f.*, d.designationname 
        FROM faculty f 
        LEFT JOIN designation d ON f.designationid = d.designationid 
        WHERE f.employeenumber = %s
    """, (emp_num,))
    user_data = cur.fetchone()

    cur.execute("""
        SELECT sub.subjectcode, sub.subjectname, r.roomname, ss.daydesc,
               TO_CHAR(ts_s.timevalue, 'HH12:MI AM') as start_time,
               TO_CHAR(ts_e.timevalue, 'HH12:MI AM') as end_time,
               ao.offeringcode, pyl.yearlevel
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        JOIN subject sub ON cs.subjectcode = sub.subjectcode
        LEFT JOIN room r ON ss.roomid = r.roomid
        LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
        LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
        LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
        LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
        LEFT JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
        WHERE sc.employeenumber = %s AND sv.status = 'Published'
          AND ss.daydesc = %s
        ORDER BY ts_s.timevalue
    """, (emp_num, today_day))
    my_schedule = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(SUM(sub.creditunits), 0) as total_units,
               COUNT(DISTINCT sub.subjectcode) as total_subjects
        FROM schedule_version sv
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        JOIN subject sub ON cs.subjectcode = sub.subjectcode
        WHERE sc.employeenumber = %s AND sv.status = 'Published'
    """, (emp_num,))
    stats = cur.fetchone()
    total_units    = int(stats['total_units'])    if stats else 0
    total_subjects = int(stats['total_subjects']) if stats else 0

    cur.close(); conn.close()
    return render_template('academic/AcadAsFaculty.html', user=user_data, schedule=my_schedule,
                           total_units=total_units, total_subjects=total_subjects, current_date=current_date_formatted)

@app.route('/academic/my-teaching-assignment')
def academic_my_teaching_assignment():
    if 'loggedin' not in session or session.get('role') != 'Academic Head':
        return redirect(url_for('login'))

    username = session.get('username')
    acc = query_db("SELECT employeenumber FROM accounts WHERE username = %s", (username,), one=True)
    own_emp_num = acc['employeenumber'] if acc else None

    active = query_db("""
        SELECT ay.academicyearid, s.semestertype
        FROM semester s
        JOIN academicyear ay ON s.academicyearid = ay.academicyearid
        WHERE s.isactive = TRUE LIMIT 1
    """, one=True)
    active_ay_id = active['academicyearid'] if active else ''
    active_sem   = active['semestertype']    if active else 'A'

    return render_template('academic/AcadTeachingAssign.html',
                           own_emp_num=own_emp_num,
                           active_ay_id=active_ay_id,
                           active_sem=active_sem)

@app.route('/requests')
def requests_view():
    if 'loggedin' not in session or session.get('role') != 'Academic Head':
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    if 'loggedin' not in session: return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        now = datetime.now().strftime("%B %d, %Y, %A")
        
        # --- 1. KPI Counts ---
        cur.execute("SELECT COUNT(*) as t FROM programs WHERE isactive = TRUE")
        prog_data = cur.fetchone()
        
        cur.execute("SELECT COUNT(*) as t FROM faculty WHERE employeestatus != 'Archive'")
        fac_data = cur.fetchone()
        
        cur.execute("SELECT COUNT(*) as t FROM room")
        room_data = cur.fetchone()
        
        # Safely count pending requests (handling potential missing tables)
        pending_reqs = 0
        try:
            cur.execute("SELECT COUNT(*) as t FROM class_meeting_request WHERE status = 'Pending'")
            req_data = cur.fetchone()
            if req_data: pending_reqs += req_data['t']
            
            cur.execute("SELECT COUNT(*) as t FROM schedule_change_request WHERE status = 'Pending'")
            change_data = cur.fetchone()
            if change_data: pending_reqs += change_data['t']
        except Exception:
            conn.rollback()

        # --- 2. Recent Requests (Mocked fallback if table empty) ---
        recent_requests = []
        try:
            cur.execute("""
                SELECT 'MAKE-UP CLASS' as req_type, cmr.status, 
                       TO_CHAR(cmr.created_at, 'Mon DD, YYYY') as date_sub,
                       UPPER(f.firstname || ' ' || f.lastname) as faculty_name,
                       sub.subjectcode, r.roomname
                FROM class_meeting_request cmr
                JOIN faculty f ON cmr.submitted_by = f.employeenumber
                JOIN schedule s ON cmr.scheduleid = s.scheduleid
                JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
                JOIN subject sub ON cs.subjectcode = sub.subjectcode
                LEFT JOIN room r ON cmr.new_roomid = r.roomid
                ORDER BY cmr.created_at DESC LIMIT 3
            """)
            recent_requests = cur.fetchall()
        except Exception:
            conn.rollback()

        # --- 3. Recent Schedule List ---
        cur.execute("""
            SELECT
                ao.offeringcode AS programcode,
                pyl.yearlevel,
                ay.yearstart || '-' || ay.yearend AS acad_year,
                sem.semestertype,
                TO_CHAR(MAX(sv.datecreated), 'MM/DD/YYYY') AS date_imported
            FROM schedule_version sv
            JOIN schedule s ON sv.scheduleid = s.scheduleid
            JOIN sections sec ON s.sectionid = sec.sectionid
            JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN academic_offering ao  ON pyl.academicofferingid = ao.academicofferingid
            JOIN semester sem          ON s.semesterid = sem.semesterid
            JOIN academicyear ay       ON sem.academicyearid = ay.academicyearid
            GROUP BY ao.offeringcode, pyl.yearlevel, ay.yearstart, ay.yearend, sem.semestertype
            ORDER BY MAX(sv.datecreated) DESC
            LIMIT 5
        """)
        scheds = cur.fetchall()

        return render_template('academic/dashboard.html', 
                               current_date=now,
                               program_count=prog_data['t'] if prog_data else 0,
                               faculty_count=fac_data['t'] if fac_data else 0,
                               room_count=room_data['t'] if room_data else 0,
                               pending_requests=pending_reqs,
                               recent_requests=recent_requests,
                               schedules=scheds)
    except Exception as e:
        print(f"Dashboard Database Error: {e}")
        return render_template('academic/dashboard.html', 
                               current_date="N/A", program_count=0, faculty_count=0,
                               room_count=0, pending_requests=0, recent_requests=[], schedules=[])
    finally:
        cur.close()
        conn.close()

# --- API Endpoints for Dashboard Popups ---

@app.route('/api/dashboard/programs_list')
def api_dashboard_programs_list():
    if 'loggedin' not in session: return jsonify([])
    rows = query_db("SELECT programcode, programname, programtype FROM programs WHERE isactive = TRUE ORDER BY programname")
    return jsonify([dict(r) for r in rows])

@app.route('/api/dashboard/faculty_list')
def api_dashboard_faculty_list():
    if 'loggedin' not in session: return jsonify([])
    rows = query_db("""
        SELECT f.employeenumber, f.lastname || ', ' || f.firstname AS name, 
               et.typename, f.employeestatus 
        FROM faculty f 
        LEFT JOIN employeetype et ON f.employeetypeid = et.employeetypeid
        WHERE f.employeestatus != 'Archive' ORDER BY f.lastname
    """)
    return jsonify([dict(r) for r in rows])

@app.route('/api/dashboard/rooms_list')
def api_dashboard_rooms_list():
    if 'loggedin' not in session: return jsonify([])
    rows = query_db("""
        SELECT r.roomname, r.roomtype, b.buildingname 
        FROM room r JOIN building b ON r.buildingid = b.buildingid 
        ORDER BY b.buildingname, r.roomname
    """)
    return jsonify([dict(r) for r in rows])

@app.route('/api/dashboard/requests_list')
def api_dashboard_requests_list():
    if 'loggedin' not in session: return jsonify([])
    # Combine make-up and change requests
    rows = query_db("""
        SELECT 'Make-up' as type, f.lastname as faculty, sub.subjectcode, status 
        FROM class_meeting_request cmr
        JOIN faculty f ON cmr.submitted_by = f.employeenumber
        JOIN schedule s ON cmr.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        JOIN subject sub ON cs.subjectcode = sub.subjectcode
        WHERE cmr.status = 'Pending'
    """)
    return jsonify([dict(r) for r in (rows or [])])
    
@app.route('/api/dashboard/buildings_list')
def api_dashboard_buildings_list():
    if 'loggedin' not in session: return jsonify([])
    rows = query_db("SELECT buildingname FROM building WHERE isactive = TRUE ORDER BY buildingname")
    return jsonify([dict(r) for r in rows])

@app.route('/employee')
def employee():
    if 'loggedin' not in session: return redirect(url_for('login'))

    conn = get_db_connection()
    if not conn:
        return render_template('academic/employee.html', employees=[], total=0,
                               reg=0, pt=0, des=0, specializations=[], employee_types=[], designations=[])
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT
                f.EmployeeNumber, f.FirstName, f.MiddleName, f.LastName, f.Email,
                f.ContactNumber, f.EmployeeStatus, f.SpecializationID, f.EmployeeTypeID, f.DesignationID,
                s.SpecializationName, et.TypeName, d.DesignationName
            FROM Faculty f
            LEFT JOIN Specialization s ON f.SpecializationID = s.SpecializationID
            LEFT JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID
            LEFT JOIN Designation d ON f.DesignationID = d.DesignationID
            ORDER BY f.LastName, f.FirstName
        """)
        employees = cur.fetchall()

        cur.execute("""
            SELECT et.TypeName, COUNT(f.EmployeeNumber) as count
            FROM EmployeeType et
            LEFT JOIN Faculty f ON et.EmployeeTypeID = f.EmployeeTypeID
            GROUP BY et.TypeName
        """)
        counts = {row['typename']: row['count'] for row in cur.fetchall()}

        cur.execute("SELECT * FROM Specialization ORDER BY SpecializationName")
        specializations = cur.fetchall()

        cur.execute("SELECT * FROM EmployeeType ORDER BY TypeName")
        employee_types = cur.fetchall()

        cur.execute("SELECT * FROM Designation ORDER BY DesignationName")
        designations = cur.fetchall()

        cur.close()
        conn.close()

        return render_template(
            'academic/employee.html',
            employees=employees,
            specializations=specializations,
            employee_types=employee_types,
            designations=designations,
            reg=counts.get('Regular', 0),
            pt=counts.get('Part-time', 0),
            des=counts.get('Designee', 0),
            total=len(employees)
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        try: conn.close()
        except: pass
        return render_template('academic/employee.html', employees=[], total=0,
                               reg=0, pt=0, des=0, specializations=[], employee_types=[], designations=[])

@app.route('/add_employee', methods=['POST'])
def add_employee():
    if 'loggedin' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        emp_num = request.form['employee_number']
        f_name = request.form['first_name']
        m_name = request.form.get('middle_name')
        l_name = request.form['last_name']
        email = request.form['email']
        contact = request.form['contact']
        spec_id = request.form['specialization_id']
        type_id = request.form['type_id']
        status = request.form['status']
        desig_id = request.form.get('designation_id')
        if not desig_id or desig_id == '': desig_id = None
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (emp_num, f_name, m_name, l_name, email, contact, spec_id, type_id, desig_id, status))
            conn.commit()
            flash("Employee added successfully!", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error adding employee: {e}", "error")
        finally:
            cur.close()
            conn.close()
        return redirect(url_for('employee'))

@app.route('/edit_employee', methods=['POST'])
def edit_employee():
    if 'loggedin' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        emp_num = request.form['employee_number']
        f_name = request.form['first_name']
        m_name = request.form.get('middle_name')
        l_name = request.form['last_name']
        email = request.form['email']
        contact = request.form['contact']
        spec_id = request.form['specialization_id']
        type_id = request.form['type_id']
        status = request.form['status']
        desig_id = request.form.get('designation_id')
        if not desig_id or desig_id == '': desig_id = None
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE Faculty 
                SET FirstName=%s, MiddleName=%s, LastName=%s, Email=%s, ContactNumber=%s, 
                    SpecializationID=%s, EmployeeTypeID=%s, DesignationID=%s, EmployeeStatus=%s 
                WHERE EmployeeNumber=%s
            """, (f_name, m_name, l_name, email, contact, spec_id, type_id, desig_id, status, emp_num))
            conn.commit()
            flash("Employee details updated successfully!", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error updating employee: {e}", "error")
        finally:
            cur.close()
            conn.close()
        return redirect(url_for('employee'))

@app.route('/archive_employee/<emp_num>')
def archive_employee(emp_num):
    if 'loggedin' not in session: return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM Schedule WHERE EmployeeNumber = %s", (emp_num,))
        if cur.fetchone():
            flash(f"Cannot archive Employee {emp_num}. They are currently assigned to an active schedule.", "error")
            return redirect(url_for('employee'))

        cur.execute("""
            INSERT INTO Faculty_Archive (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty WHERE EmployeeNumber = %s
        """, (emp_num,))

        cur.execute("UPDATE Accounts SET IsActive = FALSE, EmployeeNumber = NULL WHERE EmployeeNumber = %s", (emp_num,))
        cur.execute("DELETE FROM Faculty WHERE EmployeeNumber = %s", (emp_num,))
        
        conn.commit()
        flash("Employee archived successfully.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error archiving employee: {str(e)}", "error")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('employee')) 

@app.route('/bulk_archive', methods=['POST'])
def bulk_archive():
    if 'loggedin' not in session: return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    emp_ids = data.get('employee_ids',[])
    if not emp_ids: return jsonify({'error': 'No employees selected'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        placeholders = ', '.join(['%s'] * len(emp_ids))
        
        cur.execute(f"SELECT EmployeeNumber FROM Schedule WHERE EmployeeNumber IN ({placeholders})", tuple(emp_ids))
        conflicts = [row[0] for row in cur.fetchall()]
        if conflicts:
            return jsonify({'error': f"Cannot archive. The following are in a schedule: {', '.join(conflicts)}"}), 409

        cur.execute(f"""
            INSERT INTO Faculty_Archive (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty WHERE EmployeeNumber IN ({placeholders})
        """, tuple(emp_ids))

        cur.execute(f"UPDATE Accounts SET IsActive = FALSE, EmployeeNumber = NULL WHERE EmployeeNumber IN ({placeholders})", tuple(emp_ids))
        cur.execute(f"DELETE FROM Faculty WHERE EmployeeNumber IN ({placeholders})", tuple(emp_ids))
        
        conn.commit()
        return jsonify({'success': f'{len(emp_ids)} employees archived successfully'})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/bulk_import', methods=['POST'])
def bulk_import():
    if 'loggedin' not in session: return redirect(url_for('login'))
    if 'file' not in request.files: return redirect(url_for('employee'))
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        flash("Invalid file format. Please upload a .csv file.", "error")
        return redirect(url_for('employee'))

    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    csv_input = csv.reader(stream)
    next(csv_input)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Build name → ID lookup dicts so the CSV can use either IDs or names
        cur.execute("SELECT SpecializationID, SpecializationName FROM Specialization")
        spec_map  = {r[1].strip().lower(): r[0] for r in cur.fetchall()}
        cur.execute("SELECT EmployeeTypeID, TypeName FROM EmployeeType")
        etype_map = {r[1].strip().lower(): r[0] for r in cur.fetchall()}
        cur.execute("SELECT DesignationID, DesignationName FROM Designation")
        desig_map = {r[1].strip().lower(): r[0] for r in cur.fetchall()}

        def _fuzzy_lookup(key, name_map):
            """Try progressively looser matches; return ID or None."""
            if key in name_map: return name_map[key]
            m = {k: v for k, v in name_map.items() if k.startswith(key)}
            if len(m) == 1: return next(iter(m.values()))
            m = {k: v for k, v in name_map.items() if key.startswith(k)}
            if len(m) == 1: return next(iter(m.values()))
            m = {k: v for k, v in name_map.items() if key in k}
            if len(m) == 1: return next(iter(m.values()))
            return None

        def resolve_etype(val):
            """Employee Type must already exist – controlled values with load rules."""
            if not val or val.strip().upper() in ('NULL', 'N/A', '-', ''):
                return None
            try:
                return int(val)
            except ValueError:
                result = _fuzzy_lookup(val.strip().lower(), etype_map)
                if result is not None:
                    return result
                avail = ', '.join(f'"{n}"' for n in sorted(etype_map.keys()))
                raise ValueError(
                    f"'{val}' is not a recognised Employee Type. "
                    f"Available: {avail}"
                )

        def get_or_create_spec(val):
            """Specialization: fuzzy match first, auto-create if nothing matches."""
            if not val or val.strip().upper() in ('NULL', 'N/A', '-', ''):
                return None
            try:
                return int(val)
            except ValueError:
                name = val.strip()
                key  = name.lower()
                result = _fuzzy_lookup(key, spec_map)
                if result is not None:
                    return result
                # Not found – insert it automatically
                cur.execute(
                    "INSERT INTO Specialization (SpecializationName, IsActive) "
                    "VALUES (%s, TRUE) ON CONFLICT (SpecializationName) DO NOTHING",
                    (name,)
                )
                cur.execute(
                    "SELECT SpecializationID FROM Specialization "
                    "WHERE LOWER(SpecializationName) = LOWER(%s)", (name,)
                )
                row = cur.fetchone(); new_id = row[0]
                spec_map[key] = new_id
                return new_id

        def get_or_create_desig(val):
            """Designation: fuzzy match first, auto-create if nothing matches."""
            if not val or val.strip().upper() in ('NULL', 'N/A', '-', ''):
                return None
            try:
                return int(val)
            except ValueError:
                name = val.strip()
                key  = name.lower()
                result = _fuzzy_lookup(key, desig_map)
                if result is not None:
                    return result
                # Not found – insert it automatically
                cur.execute(
                    "INSERT INTO Designation (DesignationName) "
                    "VALUES (%s) ON CONFLICT (DesignationName) DO NOTHING",
                    (name,)
                )
                cur.execute(
                    "SELECT DesignationID FROM Designation "
                    "WHERE LOWER(DesignationName) = LOWER(%s)", (name,)
                )
                row = cur.fetchone(); new_id = row[0]
                desig_map[key] = new_id
                return new_id

        for row_num, row in enumerate(csv_input, 2):
            if len(row) < 10: continue
            try:
                emp_num, last_name, first_name, middle_name, email, contact, \
                    spec_raw, etype_raw, status, desig_raw = [r.strip() for r in row[:10]]

                spec_id  = get_or_create_spec(spec_raw)
                etype_id = resolve_etype(etype_raw)
                desig_id = get_or_create_desig(desig_raw)

                # Normalise status to exact DB-accepted values (case-insensitive)
                status_norm = status.lower().replace('-', '').replace(' ', '')
                if status_norm == 'parttime':
                    status = 'Part-Time'
                elif status_norm == 'permanent':
                    status = 'Permanent'
                elif status_norm == 'temporary':
                    status = 'Temporary'

                cur.execute("""
                    INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName,
                        Email, ContactNumber, SpecializationID, EmployeeTypeID,
                        DesignationID, EmployeeStatus)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (EmployeeNumber) DO NOTHING
                """, (emp_num, first_name, middle_name or None, last_name,
                      email or None, contact or None,
                      spec_id, etype_id, desig_id, status))

            except ValueError as ve:
                conn.rollback()
                flash(f"Import failed at row {row_num}: {ve}", "error")
                return redirect(url_for('employee'))
        conn.commit()
        flash("Bulk import completed successfully.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"An error occurred: {e}", "error")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('employee'))


# ── Shared helper: insert employee list using regular cursor ─────────────────
def _acad_insert_employees(rows, conn, cur):
    cur.execute("SELECT SpecializationID, SpecializationName FROM Specialization")
    spec_map  = {r[1].strip().lower(): r[0] for r in cur.fetchall()}
    cur.execute("SELECT EmployeeTypeID, TypeName FROM EmployeeType")
    etype_map = {r[1].strip().lower(): r[0] for r in cur.fetchall()}
    cur.execute("SELECT DesignationID, DesignationName FROM Designation")
    desig_map = {r[1].strip().lower(): r[0] for r in cur.fetchall()}

    def _fuzzy(key, nm):
        if key in nm: return nm[key]
        m = {k: v for k, v in nm.items() if k.startswith(key)}
        if len(m) == 1: return next(iter(m.values()))
        m = {k: v for k, v in nm.items() if key.startswith(k)}
        if len(m) == 1: return next(iter(m.values()))
        m = {k: v for k, v in nm.items() if key in k}
        if len(m) == 1: return next(iter(m.values()))
        return None

    def resolve_etype(val):
        if not val or val.strip().upper() in ('NULL', 'N/A', '-', ''): return None
        try: return int(val)
        except ValueError:
            r = _fuzzy(val.strip().lower(), etype_map)
            if r is not None: return r
            avail = ', '.join(f'"{n}"' for n in sorted(etype_map.keys()))
            raise ValueError(f"'{val}' is not a recognised Employee Type. Available: {avail}")

    def get_or_create_spec(val):
        if not val or val.strip().upper() in ('NULL', 'N/A', '-', ''): return None
        try: return int(val)
        except ValueError:
            name = val.strip(); key = name.lower()
            r = _fuzzy(key, spec_map)
            if r is not None: return r
            cur.execute("INSERT INTO Specialization (SpecializationName, IsActive) VALUES (%s, TRUE) ON CONFLICT (SpecializationName) DO NOTHING", (name,))
            cur.execute("SELECT SpecializationID FROM Specialization WHERE LOWER(SpecializationName) = LOWER(%s)", (name,))
            new_id = cur.fetchone()[0]; spec_map[key] = new_id; return new_id

    def get_or_create_desig(val):
        if not val or val.strip().upper() in ('NULL', 'N/A', '-', ''): return None
        try: return int(val)
        except ValueError:
            name = val.strip(); key = name.lower()
            r = _fuzzy(key, desig_map)
            if r is not None: return r
            cur.execute("INSERT INTO Designation (DesignationName) VALUES (%s) ON CONFLICT (DesignationName) DO NOTHING", (name,))
            cur.execute("SELECT DesignationID FROM Designation WHERE LOWER(DesignationName) = LOWER(%s)", (name,))
            new_id = cur.fetchone()[0]; desig_map[key] = new_id; return new_id

    def norm_status(s):
        sn = s.lower().replace('-', '').replace(' ', '')
        if sn == 'parttime': return 'Part-Time'
        if sn == 'permanent': return 'Permanent'
        if sn == 'temporary': return 'Temporary'
        return s

    errors = []
    for i, emp in enumerate(rows, 1):
        try:
            emp_num     = str(emp.get('emp_num',     '') or '').strip()
            last_name   = str(emp.get('last_name',   '') or '').strip()
            first_name  = str(emp.get('first_name',  '') or '').strip()
            middle_name = str(emp.get('middle_name', '') or '').strip()
            email       = str(emp.get('email',       '') or '').strip()
            contact     = str(emp.get('contact',     '') or '').strip()
            spec_id     = get_or_create_spec(emp.get('specialization', ''))
            etype_id    = resolve_etype(emp.get('emp_type', ''))
            desig_id    = get_or_create_desig(emp.get('designation', ''))
            status      = norm_status(str(emp.get('status', 'Permanent') or 'Permanent'))
            if not emp_num:
                errors.append(f"Row {i}: missing Employee Number"); continue
            # Block insert if the employee number exists in archived records
            cur.execute("SELECT 1 FROM Faculty_Archive WHERE EmployeeNumber = %s LIMIT 1", (emp_num,))
            if cur.fetchone():
                errors.append(
                    f"Row {i} (Emp# {emp_num}): Employee Number already exists in archived records. "
                    "Numbers must be unique across active and archived employees."
                )
                continue
            cur.execute("""
                INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName,
                    Email, ContactNumber, SpecializationID, EmployeeTypeID,
                    DesignationID, EmployeeStatus)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (EmployeeNumber) DO NOTHING
            """, (emp_num, first_name, middle_name or None, last_name,
                  email or None, contact or None, spec_id, etype_id, desig_id, status))
            hashed_pw = generate_password_hash(emp_num)
            cur.execute("""
                INSERT INTO Accounts (Username, PasswordHash, Role, IsActive, EmployeeNumber)
                VALUES (%s, %s, 'Faculty', TRUE, %s) ON CONFLICT (Username) DO NOTHING
            """, (emp_num, hashed_pw, emp_num))
        except ValueError as ve:
            errors.append(f"Row {i}: {ve}")
    return errors


# ── Academic Head: XLSX faculty import ───────────────────────────────────────
@app.route('/faculty/import/xlsx', methods=['POST'])
def acad_faculty_import_xlsx():
    if 'loggedin' not in session: return redirect(url_for('login'))
    if 'file' not in request.files or request.files['file'].filename == '':
        flash("No file selected.", "error"); return redirect(url_for('employee'))

    file = request.files['file']
    if not file.filename.endswith('.xlsx'):
        flash("Please upload a .xlsx file.", "error"); return redirect(url_for('employee'))

    import openpyxl
    _HEADER_KEYS = {
        'employeenumber': 'emp_num',   'employee number': 'emp_num',   'emp no': 'emp_num',
        'lastname':   'last_name',     'last name':   'last_name',     'surname':  'last_name',
        'firstname':  'first_name',    'first name':  'first_name',
        'middlename': 'middle_name',   'middle name': 'middle_name',
        'email': 'email',              'email address': 'email',
        'contactnumber': 'contact',    'contact number': 'contact',    'contact': 'contact',
        'specialization': 'specialization',
        'employeetype':   'emp_type',  'employee type': 'emp_type',    'type': 'emp_type',
        'employeestatus': 'status',    'employment status': 'status',  'status': 'status',
        'designation': 'designation',
    }

    wb = openpyxl.load_workbook(file.stream, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        flash("Excel file is empty.", "error"); return redirect(url_for('employee'))

    col_map = {}
    for i, cell in enumerate(rows[0]):
        key = str(cell or '').lower().strip()
        if key in _HEADER_KEYS:
            col_map[_HEADER_KEYS[key]] = i

    employees = []
    for raw in rows[1:]:
        if not any(c for c in raw if c is not None): continue
        def gv(field):
            idx = col_map.get(field)
            return str(raw[idx] or '').strip() if idx is not None and idx < len(raw) else ''
        employees.append({
            'emp_num': gv('emp_num'), 'last_name': gv('last_name'),
            'first_name': gv('first_name'), 'middle_name': gv('middle_name'),
            'email': gv('email'), 'contact': gv('contact'),
            'specialization': gv('specialization'), 'emp_type': gv('emp_type'),
            'status': gv('status'), 'designation': gv('designation'),
        })

    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        errs = _acad_insert_employees(employees, conn, cur)
        conn.commit()
        if errs:
            flash("Import completed with errors: " + "; ".join(errs[:3]), "error")
        else:
            flash(f"XLSX import successful — {len(employees)} employee(s) processed.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Import error: {e}", "error")
    finally:
        cur.close(); conn.close()

    return redirect(url_for('employee'))


# ── Shared helpers: parse CSV / XLSX for analyze-before-import ───────────────
_STRUCT_HEADER_MAP = {
    'employeenumber': 'emp_num',  'employee number': 'emp_num',
    'emp no': 'emp_num',          'emp no.': 'emp_num',
    'emp_num': 'emp_num',         'employee id': 'emp_num',
    'lastname':    'last_name',   'last name':    'last_name',
    'last_name':   'last_name',   'surname':      'last_name',
    'firstname':   'first_name',  'first name':   'first_name',  'first_name':  'first_name',
    'middlename':  'middle_name', 'middle name':  'middle_name', 'middle_name': 'middle_name',
    'middle initial': 'middle_name', 'm.i.': 'middle_name',
    'email': 'email', 'email address': 'email', 'e-mail': 'email', 'e-mail address': 'email',
    'contactnumber': 'contact',   'contact number': 'contact',   'contact': 'contact',
    'mobile number': 'contact',   'mobile': 'contact',
    'phone number': 'contact',    'phone': 'contact',
    'specialization': 'specialization', 'specialty': 'specialization', 'speciality': 'specialization',
    'employeetype': 'emp_type',   'employee type': 'emp_type',   'type': 'emp_type',
    'employeestatus': 'status',   'employment status': 'status', 'status': 'status',
    'designation': 'designation',
}

def _detect_struct_col_map(header_row):
    col_map = {}
    for i, cell in enumerate(header_row):
        key = str(cell or '').strip().lower()
        if key in _STRUCT_HEADER_MAP and _STRUCT_HEADER_MAP[key] not in col_map:
            col_map[_STRUCT_HEADER_MAP[key]] = i
    has_header = any(f in col_map for f in ('emp_num', 'last_name', 'first_name'))
    return col_map, has_header

_POSITIONAL_MAP = {
    'emp_num': 0, 'last_name': 1, 'first_name': 2, 'middle_name': 3,
    'email': 4, 'contact': 5, 'specialization': 6, 'emp_type': 7,
    'status': 8, 'designation': 9,
}

_POSITIONAL_LABELS = [
    'EmployeeNumber', 'LastName', 'FirstName', 'MiddleName',
    'Email', 'Contact', 'Specialization', 'EmployeeType', 'EmployeeStatus', 'Designation',
]

def _rows_to_employee_list(data_rows, col_map):
    fields = ('emp_num','last_name','first_name','middle_name',
              'email','contact','specialization','emp_type','status','designation')
    employees = []
    for row in data_rows:
        row = list(row)
        if not any(str(c or '').strip() for c in row):
            continue
        def gv(f, _r=row, _m=col_map):
            idx = _m.get(f)
            return str(_r[idx] or '').strip() if idx is not None and idx < len(_r) else ''
        emp = {f: gv(f) for f in fields}
        if not emp['emp_num'] and not emp['last_name']:
            continue
        employees.append(emp)
    return employees

def _build_struct_result(employees, has_header, col_map):
    warnings = []
    if not has_header:
        warnings.append(
            "No column headers detected — using fixed positional order "
            "(EmpNum, LastName, FirstName, MiddleName, Email, Contact, "
            "Specialization, Type, Status, Designation). Please verify."
        )
    else:
        missing = [f.replace('_', ' ') for f in ('emp_num', 'last_name', 'first_name')
                   if f not in col_map]
        if missing:
            warnings.append(f"Missing expected column(s): {', '.join(missing)}.")
    if not employees:
        warnings.append("No employee records found in the file.")
    confidence = 95 if (has_header and employees and not warnings) else (75 if employees else 0)
    return {'employees': employees, 'confidence': confidence,
            'warnings': warnings, 'employee_count': len(employees)}

def _parse_csv_employees(file_bytes):
    stream = None
    for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
        try:
            stream = io.StringIO(file_bytes.decode(enc), newline=None)
            break
        except UnicodeDecodeError:
            continue
    rows = list(csv.reader(stream))
    if not rows:
        return {'error': 'The CSV file is empty.'}
    col_map, has_header = _detect_struct_col_map(rows[0])
    if not has_header:
        col_map = _POSITIONAL_MAP.copy()
    data_rows  = rows[1:] if has_header else rows
    raw_headers = [str(c or '') for c in rows[0]] if has_header else _POSITIONAL_LABELS[:]
    raw_rows   = [[str(c or '') for c in row] for row in data_rows]
    employees  = _rows_to_employee_list(data_rows, col_map)
    result     = _build_struct_result(employees, has_header, col_map)
    result['raw_rows']   = raw_rows
    result['raw_headers'] = raw_headers
    result['col_map']    = col_map
    result['has_header'] = has_header
    return result

def _parse_xlsx_employees(file_bytes):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {'error': 'The Excel file is empty.'}
    col_map, has_header = _detect_struct_col_map(rows[0])
    if not has_header:
        col_map = _POSITIONAL_MAP.copy()
    data_rows   = rows[1:] if has_header else rows
    raw_headers = [str(c or '') for c in rows[0]] if has_header else _POSITIONAL_LABELS[:]
    raw_rows    = [[str(c or '') for c in row] for row in data_rows]
    employees   = _rows_to_employee_list(data_rows, col_map)
    result      = _build_struct_result(employees, has_header, col_map)
    result['raw_rows']    = raw_rows
    result['raw_headers'] = raw_headers
    result['col_map']     = col_map
    result['has_header']  = has_header
    return result


# ── Academic Head: CSV faculty analyze ───────────────────────────────────────
@app.route('/faculty/import/csv/analyze', methods=['POST'])
def acad_faculty_csv_analyze():
    if 'loggedin' not in session:
        return jsonify({'error': 'Unauthorized'}), 403
    if 'file' not in request.files or request.files['file'].filename == '':
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    if not f.filename.endswith('.csv'):
        return jsonify({'error': 'Please upload a .csv file.'}), 400
    try:
        result = _parse_csv_employees(f.read())
        return jsonify(result), (400 if 'error' in result else 200)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Academic Head: XLSX faculty analyze ──────────────────────────────────────
@app.route('/faculty/import/xlsx/analyze', methods=['POST'])
def acad_faculty_xlsx_analyze():
    if 'loggedin' not in session:
        return jsonify({'error': 'Unauthorized'}), 403
    if 'file' not in request.files or request.files['file'].filename == '':
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    if not f.filename.endswith('.xlsx'):
        return jsonify({'error': 'Please upload a .xlsx file.'}), 400
    try:
        result = _parse_xlsx_employees(f.read())
        return jsonify(result), (400 if 'error' in result else 200)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Academic Head: PDF faculty analyze ───────────────────────────────────────
@app.route('/faculty/import/pdf/analyze', methods=['POST'])
def acad_faculty_pdf_analyze():
    if 'loggedin' not in session:
        return jsonify({'error': 'Unauthorized'}), 403
    if 'file' not in request.files or request.files['file'].filename == '':
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        data = parse_faculty_pdf(request.files['file'].read())
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Academic Head: DOCX faculty analyze ──────────────────────────────────────
@app.route('/faculty/import/docx/analyze', methods=['POST'])
def acad_faculty_docx_analyze():
    if 'loggedin' not in session:
        return jsonify({'error': 'Unauthorized'}), 403
    if 'file' not in request.files or request.files['file'].filename == '':
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        data = parse_faculty_docx(request.files['file'].read())
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Academic Head: pre-import duplicate check ────────────────────────────────
@app.route('/faculty/import/check-duplicates', methods=['POST'])
def acad_faculty_check_duplicates():
    if 'loggedin' not in session:
        return jsonify({'error': 'Unauthorized'}), 403
    data     = request.get_json() or {}
    emp_nums = [str(n).strip() for n in data.get('emp_nums', []) if str(n).strip()]
    if not emp_nums:
        return jsonify({'active': [], 'archived': []})
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT f.EmployeeNumber, f.FirstName, f.LastName, et.TypeName, f.EmployeeStatus
            FROM Faculty f
            LEFT JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID
            WHERE f.EmployeeNumber = ANY(%s)
        """, (emp_nums,))
        active = [{'emp_num': r[0], 'name': f"{r[2] or ''}, {r[1] or ''}".strip(', '),
                   'typename': r[3] or '', 'status': r[4] or ''} for r in cur.fetchall()]

        cur.execute("""
            SELECT fa.FacultyArchiveID, fa.EmployeeNumber, fa.FirstName, fa.LastName,
                   et.TypeName, fa.EmployeeStatus
            FROM Faculty_Archive fa
            LEFT JOIN EmployeeType et ON fa.EmployeeTypeID = et.EmployeeTypeID
            WHERE fa.EmployeeNumber = ANY(%s)
        """, (emp_nums,))
        archived = [{'archiveid': r[0], 'emp_num': r[1],
                     'name': f"{r[3] or ''}, {r[2] or ''}".strip(', '),
                     'typename': r[4] or '', 'status': r[5] or ''} for r in cur.fetchall()]

        return jsonify({'active': active, 'archived': archived})
    except Exception as e:
        return jsonify({'active': [], 'archived': [], 'error': str(e)})
    finally:
        cur.close(); conn.close()


# ── Academic Head: restore archived employee during import ───────────────────
@app.route('/faculty/import/restore-archived', methods=['POST'])
def acad_faculty_restore_archived():
    if 'loggedin' not in session:
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    archive_id = data.get('archive_id')
    if not archive_id:
        return jsonify({'error': 'No archive_id provided'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT EmployeeNumber FROM Faculty_Archive WHERE FacultyArchiveID = %s", (archive_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Archived employee not found'}), 404
        emp_num = row[0]
        cur.execute("""
            INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber,
                SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber,
                SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty_Archive WHERE FacultyArchiveID = %s
            ON CONFLICT (EmployeeNumber) DO NOTHING
        """, (archive_id,))
        cur.execute("UPDATE Accounts SET IsActive = TRUE WHERE Username = %s", (emp_num,))
        cur.execute("DELETE FROM Faculty_Archive WHERE FacultyArchiveID = %s", (archive_id,))
        conn.commit()
        return jsonify({'success': True, 'emp_num': emp_num})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


# ── Academic Head: PDF/DOCX faculty confirm ──────────────────────────────────
@app.route('/faculty/import/doc/confirm', methods=['POST'])
def acad_faculty_doc_confirm():
    if 'loggedin' not in session: return redirect(url_for('login'))
    raw = request.form.get('employees_data', '[]')
    try:
        employees = json.loads(raw)
    except Exception:
        flash("Invalid data submitted.", "error"); return redirect(url_for('employee'))

    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        errs = _acad_insert_employees(employees, conn, cur)
        conn.commit()
        if errs:
            flash("Import completed with errors: " + "; ".join(errs[:3]), "error")
        else:
            flash(f"Import successful — {len(employees)} employee(s) processed.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Import error: {e}", "error")
    finally:
        cur.close(); conn.close()

    return redirect(url_for('employee'))


@app.route('/restore_employee/<int:archive_id>')
def acad_restore_employee(archive_id):
    if 'loggedin' not in session: return redirect(url_for('login'))

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT EmployeeNumber FROM Faculty_Archive WHERE FacultyArchiveID = %s", (archive_id,))
        result = cur.fetchone()
        if not result:
            flash("Archived employee not found.", "error")
            return redirect(url_for('archived_employees'))

        emp_num = result['employeenumber']

        cur.execute("""
            INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber,
                                 SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber,
                   SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty_Archive WHERE FacultyArchiveID = %s
        """, (archive_id,))
        cur.execute("UPDATE Accounts SET IsActive = TRUE, EmployeeNumber = %s WHERE Username = %s",
                    (emp_num, emp_num))
        cur.execute("DELETE FROM Faculty_Archive WHERE FacultyArchiveID = %s", (archive_id,))
        conn.commit()
        flash(f"Employee {emp_num} restored successfully.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error restoring employee: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('archived_employees'))


@app.route('/employee/bulk_restore', methods=['POST'])
def acad_bulk_restore():
    if 'loggedin' not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    data        = request.get_json()
    archive_ids = data.get('archive_ids', [])
    if not archive_ids:
        return jsonify({"success": False, "message": "No employees selected"}), 400

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        placeholders = ', '.join(['%s'] * len(archive_ids))
        cur.execute(f"SELECT EmployeeNumber FROM Faculty_Archive WHERE FacultyArchiveID IN ({placeholders})",
                    tuple(archive_ids))
        emp_nums = [row['employeenumber'] for row in cur.fetchall()]

        for aid in archive_ids:
            cur.execute("""
                INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber,
                                     SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
                SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber,
                       SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
                FROM Faculty_Archive WHERE FacultyArchiveID = %s
            """, (aid,))
            cur.execute("DELETE FROM Faculty_Archive WHERE FacultyArchiveID = %s", (aid,))

        if emp_nums:
            ph2 = ', '.join(['%s'] * len(emp_nums))
            cur.execute(f"UPDATE Accounts SET IsActive = TRUE, EmployeeNumber = Username WHERE Username IN ({ph2})",
                        tuple(emp_nums))

        conn.commit()
        return jsonify({"success": True, "message": f"{len(emp_nums)} employees restored successfully."})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close(); conn.close()


@app.route('/employee/archived')
def archived_employees():
    if 'loggedin' not in session: return redirect(url_for('login'))
    
    query = """
        SELECT 
            fa.FacultyArchiveID, fa.EmployeeNumber, fa.FirstName, fa.MiddleName, fa.LastName, 
            fa.Email, fa.ContactNumber, fa.EmployeeStatus, fa.ArchivedAt,
            s.SpecializationName, et.TypeName
        FROM Faculty_Archive fa
        LEFT JOIN Specialization s ON fa.SpecializationID = s.SpecializationID
        LEFT JOIN EmployeeType et ON fa.EmployeeTypeID = et.EmployeeTypeID
        ORDER BY fa.ArchivedAt DESC
    """
    archives = query_db(query)
    archived_employees =[dict(row) for row in archives] if archives else[]

    et_rows = query_db("SELECT TypeName as typename FROM EmployeeType ORDER BY TypeName")
    spec_rows = query_db("SELECT SpecializationName as specializationname FROM Specialization ORDER BY SpecializationName")

    return render_template('academic/archived_employee.html', 
                           archived_employees=archived_employees,
                           employee_types=et_rows,
                           specializations=spec_rows)

@app.route('/curriculum')
def curriculum():
    if 'loggedin' not in session: return redirect(url_for('login'))
    
    raw_programs = query_db("SELECT * FROM Programs WHERE IsActive = TRUE ORDER BY ProgramName")
    programs = [{k.lower(): v for k, v in row.items()} for row in raw_programs] if raw_programs else []
    selected_program = request.args.get('program_code', 'All')
    
    currs_raw = []
    if selected_program == 'All':
        currs_raw = query_db("""
            SELECT c.*, ao.offeringcode AS programcode, ao.offeringdescription AS programname
            FROM curriculum c
            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
            JOIN programs p ON ao.programcode = p.programcode
            WHERE p.isactive = TRUE
            ORDER BY p.programname ASC, c.curriculumyear DESC
        """)
    elif selected_program:
        currs_raw = query_db("""
            SELECT c.*, ao.offeringcode AS programcode, ao.offeringdescription AS programname
            FROM curriculum c
            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
            WHERE ao.programcode = %s
            ORDER BY c.curriculumyear DESC
        """, (selected_program,))

    curriculums = [{k.lower(): v for k, v in row.items()} for row in currs_raw] if currs_raw else []
    
    today = date.today()
    cohorts_raw = query_db("""
        SELECT pyl.programyearlevelid AS cohortid, ao.offeringcode AS programcode,
               p.programname, pyl.curriculumid, curr.curriculumcode,
               ay.academicyearid AS startacademicyear,
               COUNT(sec.sectionid) AS numberofsections,
               pyl.yearlevel AS year_level
        FROM program_yearlevel pyl
        JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
        JOIN programs p ON ao.programcode = p.programcode
        JOIN academicyear ay ON pyl.academicyearid = ay.academicyearid
        LEFT JOIN curriculum curr ON pyl.curriculumid = curr.curriculumid
        LEFT JOIN sections sec ON sec.programyearlevelid = pyl.programyearlevelid AND sec.isactive = TRUE
        WHERE pyl.isactive = TRUE
          AND ay.isactive = TRUE
        GROUP BY pyl.programyearlevelid, ao.offeringcode, p.programname,
                 pyl.curriculumid, curr.curriculumcode, ay.academicyearid, pyl.yearlevel
        ORDER BY ay.academicyearid DESC, p.programname ASC
    """)

    cohorts = [{k.lower(): v for k, v in row.items()} for row in cohorts_raw] if cohorts_raw else []

    return render_template('academic/curriculum.html', programs=programs, curriculums=curriculums, selected_program=selected_program, cohorts=cohorts)

@app.route('/curriculum/view/<int:curriculum_id>')
def view_curriculum(curriculum_id):
    if 'loggedin' not in session: return redirect(url_for('login'))
    
    y_lvl = request.args.get('year', '0') 
    sem = request.args.get('semester', 'All')

    info_sql = """
        SELECT c.*, ao.offeringcode AS programcode, ao.offeringdescription AS programname,
               ao.programcode AS baseprogramcode, p.numyearlevel
        FROM curriculum c
        JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
        JOIN programs p ON ao.programcode = p.programcode
        WHERE c.curriculumid = %s
    """
    info_raw = query_db(info_sql, (curriculum_id,), one=True)
    if not info_raw: return redirect(url_for('curriculum'))
    info = {k.lower(): v for k, v in info_raw.items()}

    raw_progs = query_db("SELECT * FROM Programs WHERE IsActive = TRUE ORDER BY ProgramName")
    programs =[{k.lower(): v for k, v in row.items()} for row in raw_progs] if raw_progs else[]
    
    other_sql = """
        SELECT c.curriculumid, c.curriculumyear, c.curriculumcode
        FROM curriculum c
        JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
        WHERE ao.offeringcode = %s ORDER BY c.curriculumyear DESC
    """
    other_currs = query_db(other_sql, (info['programcode'],))

    main_sql = """
        SELECT cv.subjectcode, cv."Prerequisite" AS prerequisite, cv."Co-requisite" AS corequisite,
               s.subjectname, cv.lecturehours, cv.laboratoryhours, cv.creditunits,
               cv.tuitionhours, cv.semester, cv.yearlevel
        FROM curriculum_view cv
        LEFT JOIN subject s ON cv.subjectcode = s.subjectcode
        WHERE cv.curriculumid = %s
    """
    params = [curriculum_id]
    if y_lvl != '0':
        main_sql += " AND cv.yearlevel = %s"
        params.append(int(y_lvl))
    if sem != 'All':
        main_sql += " AND cv.semester = %s"
        params.append(sem)

    main_sql += " ORDER BY cv.yearlevel ASC, cv.semester ASC"
    raw_subs = query_db(main_sql, tuple(params))
    subs =[{k.lower(): v for k, v in row.items()} for row in raw_subs] if raw_subs else[]

    return render_template('academic/curriculum_view.html', info=info, subjects=subs, programs=programs, other_curriculums=other_currs, curr_year=y_lvl, curr_sem=sem)

@app.route('/room')
def room():
    if 'loggedin' not in session: return redirect(url_for('login'))
    try:
        total_labs = query_db("SELECT COUNT(*) as count FROM Room WHERE RoomType = 'Laboratory'", one=True)
        total_lec = query_db("SELECT COUNT(*) as count FROM Room WHERE RoomType = 'Lecture'", one=True)
        total_rooms = query_db("SELECT COUNT(*) as count FROM Room", one=True)
        total_bldgs = query_db("SELECT COUNT(*) as count FROM Building WHERE IsActive = TRUE", one=True)
        raw_buildings = query_db("SELECT BuildingID AS buildingid, BuildingName AS buildingname FROM Building WHERE IsActive = TRUE ORDER BY BuildingName ASC")
        buildings = [{k.lower(): v for k, v in row.items()} for row in raw_buildings] if raw_buildings else []

        raw_rooms = query_db("""
            SELECT r.RoomID, r.RoomName, r.RoomType, r.RoomCapacity, b.BuildingName
            FROM Room r
            JOIN Building b ON r.BuildingID = b.BuildingID
            ORDER BY
                regexp_replace(r.RoomName, '[0-9]', '', 'g'),
                CASE WHEN regexp_replace(r.RoomName, '[^0-9]', '', 'g') = ''
                     THEN 0
                     ELSE CAST(regexp_replace(r.RoomName, '[^0-9]', '', 'g') AS BIGINT)
                END
        """)
        rooms =[{k.lower(): v for k, v in row.items()} for row in raw_rooms] if raw_rooms else[]

        return render_template('academic/room.html',
                               total_labs=total_labs['count'] if total_labs else 0,
                               total_lec=total_lec['count'] if total_lec else 0,
                               total_rooms=total_rooms['count'] if total_rooms else 0,
                               total_bldgs=total_bldgs['count'] if total_bldgs else 0,
                               buildings=buildings, rooms=rooms)
    except Exception as e:
        return render_template('academic/room.html', total_labs=0, buildings=[], rooms=[])



@app.route('/room/view/<int:room_id>')
def room_view(room_id):
    if 'loggedin' not in session: return redirect(url_for('login'))
    try:
        room_info = query_db("""
            SELECT r.roomid, r.roomname, r.roomtype, r.roomcapacity,
                   b.buildingname, b.buildingid
            FROM Room r
            JOIN Building b ON r.buildingid = b.buildingid
            WHERE r.roomid = %s
        """, (room_id,), one=True)
        if not room_info:
            return redirect(url_for('room'))

        buildings = query_db("SELECT buildingid, buildingname FROM Building WHERE IsActive = TRUE ORDER BY BuildingName ASC")

        raw_rooms = query_db("""
            SELECT r.roomid, r.roomname, r.roomtype, b.buildingid, b.buildingname
            FROM Room r
            JOIN Building b ON r.buildingid = b.buildingid
            WHERE b.isactive = TRUE
            ORDER BY b.buildingname,
                regexp_replace(r.roomname, '[0-9]', '', 'g'),
                CASE WHEN regexp_replace(r.roomname, '[^0-9]', '', 'g') = ''
                     THEN 0
                     ELSE CAST(regexp_replace(r.roomname, '[^0-9]', '', 'g') AS BIGINT)
                END
        """)
        rooms_by_bldg = {}
        if raw_rooms:
            for r in raw_rooms:
                bid = r['buildingid']
                if bid not in rooms_by_bldg:
                    rooms_by_bldg[bid] = []
                rooms_by_bldg[bid].append({k.lower(): v for k, v in r.items()})

        active = query_db("""
            SELECT ay.academicyearid, s.semestertype
            FROM semester s
            JOIN academicyear ay ON s.academicyearid = ay.academicyearid
            WHERE s.isactive = TRUE LIMIT 1
        """, one=True)
        active_ay_id = active['academicyearid'] if active else ''
        active_sem   = active['semestertype']    if active else ''

        import json
        return render_template('academic/room_detail.html',
                               room=dict(room_info),
                               buildings=buildings or [],
                               rooms_by_bldg_json=json.dumps(rooms_by_bldg),
                               active_ay_id=active_ay_id,
                               active_sem=active_sem)
    except Exception as e:
        print(f"room_view error: {e}")
        return redirect(url_for('room'))

# Insert these routes into the "ACADEMIC HEAD SPECIFIC ROUTES" section of your app.py

@app.route('/schedule')
def schedule():
    if 'loggedin' not in session: return redirect(url_for('login'))

    from datetime import date as _date
    programs   = query_db("SELECT programcode, programname FROM programs WHERE isactive = TRUE ORDER BY programname")
    acad_years = query_db("SELECT academicyearid, yearstart, yearend FROM academicyear ORDER BY yearstart DESC")

    # Detect current semester for pre-selecting dropdowns
    today = _date.today()
    active_sem_res = query_db("""
        SELECT s.semestertype, ay.academicyearid
        FROM semester s JOIN academicyear ay ON s.academicyearid = ay.academicyearid
        WHERE %s BETWEEN s.semstartdate AND s.semenddate
        LIMIT 1
    """, [today])

    if active_sem_res:
        active_sem_type = active_sem_res[0]['semestertype']
        active_ay       = active_sem_res[0]['academicyearid']
    else:
        # No live semester — pick the AY+semester that has the most recent imported data
        most_recent_data = query_db("""
            SELECT sem.semestertype, sem.academicyearid
            FROM historical_data hd
            JOIN semester sem ON hd.semesterid = sem.semesterid
            GROUP BY sem.semestertype, sem.academicyearid
            ORDER BY MAX(hd.semesterid) DESC
            LIMIT 1
        """)
        if most_recent_data:
            active_sem_type = most_recent_data[0]['semestertype']
            active_ay       = most_recent_data[0]['academicyearid']
        else:
            # Final fallback: most recent AY, 2nd semester
            active_sem_type = 'B'
            active_ay       = acad_years[0]['academicyearid'] if acad_years else ''

    status_query = """
        SELECT
            ao.offeringdescription AS programname,
            ao.offeringcode        AS programcode,
            pyl.yearlevel,
            ay.academicyearid,
            sem.semestertype,
            MAX(s.datecreated) AS datecreated
        FROM schedule s
        JOIN sections sec          ON s.sectionid             = sec.sectionid
        JOIN program_yearlevel pyl ON sec.programyearlevelid  = pyl.programyearlevelid
        JOIN academic_offering ao  ON pyl.academicofferingid  = ao.academicofferingid
        JOIN semester sem          ON s.semesterid            = sem.semesterid
        JOIN academicyear ay       ON sem.academicyearid      = ay.academicyearid
        GROUP BY ao.offeringdescription, ao.offeringcode, pyl.yearlevel, ay.academicyearid, sem.semestertype
        ORDER BY MAX(s.datecreated) DESC
    """
    status_list = query_db(status_query)

    sem_data = query_db("""
        SELECT academicyearid, semestertype, semenddate
        FROM semester
        WHERE academicyearid IN (SELECT academicyearid FROM academicyear WHERE yearend >= %s)
        ORDER BY academicyearid, semestertype
    """, [today.year])
    import json as _json
    sem_json = _json.dumps([
        {'ay': r['academicyearid'], 'type': r['semestertype'],
         'end': r['semenddate'].isoformat() if r['semenddate'] else None}
        for r in (sem_data or [])
    ])

    return render_template('academic/schedule.html',
                           programs=programs,
                           acad_years=acad_years,
                           status_list=status_list,
                           active_sem_type=active_sem_type,
                           active_ay=active_ay,
                           current_year=today.year,
                           today=today.isoformat(),
                           sem_json=sem_json)

@app.route('/api/get_offerings_schedule')
def get_offerings_schedule():
    import re
    prog          = request.args.get('program')
    yl            = request.args.get('year_level')
    sem           = request.args.get('semester')
    ay            = request.args.get('ay')
    version_status = request.args.get('status', 'Published')
    # 'active' = both Published and Draft (excludes Archive)
    if version_status == 'active':
        status_clause = "sv.status IN ('Published', 'Draft')"
        status_param  = None
    elif version_status in ('Published', 'Draft', 'Archive'):
        status_clause = "sv.status = %s"
        status_param  = version_status
    else:
        status_clause = "sv.status = %s"
        status_param  = 'Published'

    print(f"\n[DEBUG get_offerings_schedule] prog={prog!r} yl={yl!r} sem={sem!r} ay={ay!r} status={version_status!r}")

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # ── Determine data source: current semester → schedule tables,
        #    past semester (ended before today) → historical_data only ──────
        from datetime import date as _date
        _today = _date.today()
        cur.execute("""
            SELECT semenddate FROM semester
            WHERE semestertype = %s AND academicyearid = %s LIMIT 1
        """, (sem, ay))
        _sem_row = cur.fetchone()
        is_past_sem = bool(
            _sem_row and _sem_row['semenddate'] and _sem_row['semenddate'] < _today
        )
        print(f"[DEBUG get_offerings_schedule] ay={ay!r} sem={sem!r} is_past_sem={is_past_sem}")

        # ── 1. Normalized schedule tables (always queried) ───────────────
        normalized_rows = []
        if True:
            q_params = ([status_param] if status_param else []) + [prog, int(yl), sem, ay]
            cur.execute(f"""
            SELECT
                sub.subjectcode,
                sub.subjectname,
                COALESCE(f.lastname || ', ' || f.firstname || COALESCE(' ' || f.middlename, ''), 'TBA') AS instructor,
                f.employeenumber                                    AS faculty_id,
                COALESCE(r.roomname, 'TBA')                         AS roomname,
                ss.daydesc,
                TO_CHAR(ts_s.timevalue, 'HH24:MI')                 AS start_time,
                TO_CHAR(ts_e.timevalue, 'HH24:MI')                 AS end_time,
                COALESCE(sub.lecturehours,    0)                    AS lecturehours,
                COALESCE(sub.laboratoryhours, 0)                    AS laboratoryhours,
                COALESCE(sub.creditunits,     0)                    AS creditunits,
                (COALESCE(sub.lecturehours,0) + COALESCE(sub.laboratoryhours,0)) AS total_hours,
                sv.status,
                sv.versionid
            FROM schedule_version sv
            JOIN schedule sc              ON sv.scheduleid             = sc.scheduleid
            JOIN curriculumsubject cs     ON sc.curriculumsubjectid    = cs.curriculumsubjectid
            JOIN subject sub              ON cs.subjectcode            = sub.subjectcode
            JOIN sections sec             ON sc.sectionid              = sec.sectionid
            JOIN program_yearlevel pyl    ON sec.programyearlevelid    = pyl.programyearlevelid
            JOIN academic_offering ao     ON pyl.academicofferingid    = ao.academicofferingid
            LEFT JOIN faculty f           ON sc.employeenumber         = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid             = sv.versionid
            LEFT JOIN room r              ON ss.roomid                 = r.roomid
            LEFT JOIN timeslot ts_s       ON ss.starttimeid            = ts_s.timeid
            LEFT JOIN timeslot ts_e       ON ss.endtimeid              = ts_e.timeid
            WHERE {status_clause}
              AND ao.offeringcode = %s
              AND pyl.yearlevel = %s
              AND sc.semesterid = (
                  SELECT semesterid FROM semester
                  WHERE semestertype = %s AND academicyearid = %s LIMIT 1
              )
            ORDER BY
                CASE WHEN sv.status = 'Published' THEN 0 ELSE 1 END,
                ts_s.timevalue NULLS LAST
        """, q_params)
            normalized_rows = cur.fetchall()
            print(f"[DEBUG] normalized_rows count={len(normalized_rows)}")
            for r in normalized_rows[:3]:
                print(f"  norm: subj={r['subjectcode']!r} start={r['start_time']!r} day={r['daydesc']!r}")

        # ── 2. historical_data (always queried as fallback) ──────────────
        raw_rows = []
        if True:
            cur.execute("""
                SELECT
                    "Subject Code"      AS subjectcode,
                    "Subject Name"      AS subjectname,
                    "Instructor"        AS instructor,
                    "Room"              AS roomname,
                    "Day/s"             AS raw_days,
                    "Time"              AS raw_time,
                    "Lecture Hours"     AS lecturehours,
                    "Laboratory Hours"  AS laboratoryhours,
                    "Credit Units"      AS creditunits,
                    "Hours"             AS total_hours
                FROM historical_data
                WHERE REGEXP_REPLACE("Program", '\\s+\\d+$', '') ILIKE %s
                  AND CAST("Year Level" AS TEXT) = %s
                  AND academicyearid = %s
                  AND (
                    semesterid = (
                        SELECT semesterid FROM semester
                        WHERE semestertype = %s AND academicyearid = %s
                        LIMIT 1
                    )
                    OR semesterid IS NULL
                  )
            """, (prog, str(yl), ay, sem, ay))
            raw_rows = cur.fetchall()
            print(f"[DEBUG] historical raw_rows count={len(raw_rows)}")
            for r in raw_rows[:3]:
                print(f"  hist: subj={r['subjectcode']!r} raw_days={r['raw_days']!r} raw_time={r['raw_time']!r} ay={r.get('total_hours')!r}")

        # ── Greedy day tokenizer — longest token wins, handles all formats ──
        # Order matters: longer tokens must come before shorter prefixes
        _DAY_TOKENS = [
            ('WEDNESDAY', 'Wednesday'), ('THURSDAY',  'Thursday'),
            ('SATURDAY',  'Saturday'),  ('SATURDAY',  'Saturday'),
            ('TUESDAY',   'Tuesday'),   ('SUNDAY',    'Sunday'),
            ('MONDAY',    'Monday'),    ('FRIDAY',    'Friday'),
            ('THURS',     'Thursday'),  ('THUR',      'Thursday'),
            ('TUES',      'Tuesday'),   ('WED',       'Wednesday'),
            ('THU',       'Thursday'),  ('SAT',       'Saturday'),
            ('SUN',       'Sunday'),    ('MON',       'Monday'),
            ('TUE',       'Tuesday'),   ('FRI',       'Friday'),
            ('TH',        'Thursday'),  ('M',         'Monday'),
            ('T',         'Tuesday'),   ('W',         'Wednesday'),
            ('F',         'Friday'),    ('S',         'Saturday'),
        ]

        def resolve_days(raw):
            raw = raw.strip().upper()
            raw = re.sub(r'[^A-Z/]', '', raw)   # strip digits, spaces, etc.
            if not raw:
                return []
            # Slash-separated → recurse each segment
            if '/' in raw:
                result = []
                for part in raw.split('/'):
                    result.extend(resolve_days(part))
                return result
            # Greedy left-to-right tokenize
            result, s = [], raw
            while s:
                for tok, day in _DAY_TOKENS:
                    if s.startswith(tok):
                        result.append(day)
                        s = s[len(tok):]
                        break
                else:
                    s = s[1:]   # skip unrecognised character
            return result

        def clean_time_str(t):
            if not t:
                return None
            t = str(t).strip().upper()
            is_pm = 'PM' in t
            is_am = 'AM' in t
            t = t.replace('AM', '').replace('PM', '').replace(' ', '')
            if ':' not in t:
                t = t.zfill(4)
                t = t[:2] + ':' + t[2:]
            parts = t.split(':')
            try:
                hh = int(parts[0])
                mm = int(parts[1][:2]) if len(parts) > 1 else 0
                if is_pm and hh != 12:
                    hh += 12
                elif is_am and hh == 12:
                    hh = 0   # explicit 12:xx AM → midnight
                elif not is_pm and not is_am:
                    total_mins = hh * 60 + mm
                    # Anything before school-day start of 07:30 → PM
                    if 60 <= total_mins < 7 * 60 + 30:
                        hh += 12
                    # 12 no meridiem stays 12 (noon); ≥07:30 no meridiem stays AM
                return f"{hh:02d}:{mm:02d}"
            except (ValueError, IndexError):
                return None

        def parse_time_range(raw):
            s = raw.strip()
            if not s:
                return None, None
            if ' - ' in s:
                parts = s.split(' - ', 1)
            else:
                parts = re.split(r'-(?=\s*\d)', s, maxsplit=1)
            if len(parts) != 2:
                return None, None
            ts, te = clean_time_str(parts[0]), clean_time_str(parts[1])
            if ts and te:
                sm = int(ts[:2]) * 60 + int(ts[3:5])
                em = int(te[:2]) * 60 + int(te[3:5])
                # Invalid/reversed range (e.g., "19:00-09:00" from "7:00-9:00 AM"
                # where start got inferred PM but end's explicit AM left it < start).
                # School sessions never cross noon/midnight, so bump end by 12h.
                if em <= sm:
                    new_h = (int(te[:2]) + 12) % 24
                    te = f"{new_h:02d}:{te[3:5]}"
            return ts, te
 
        # ── Build output — one record per (subject × day × time-block) ────
        def _hist_slot_hours(t_seg):
            ts, te = parse_time_range(t_seg)
            if not ts or not te:
                return 0.0
            def _m(t): return int(t[:2]) * 60 + int(t[3:5])
            dur = _m(te) - _m(ts)
            if dur <= 0:
                dur += 720  # 12-hr PM correction
            return dur / 60.0

        result = []
        for row in raw_rows:
            raw_days    = str(row.get('raw_days') or '').strip()
            raw_time    = str(row.get('raw_time') or '').strip()
            total_hours = int(row.get('total_hours') or 0)

            full_days = resolve_days(raw_days)
            # Split on "/" or newlines, then on spaces-between-ranges
            _raw_t = [b.strip() for b in re.split(r'[/\n]', raw_time) if b.strip()]
            time_parts = []
            for _blk in _raw_t:
                _sub = re.split(r'(?<=\d)\s+(?=\d{1,2}:\d{2}\s*-)', _blk)
                time_parts.extend([s.strip() for s in _sub if s.strip()])

            def _make_row(day_name, t_start, t_end):
                return {
                    'subjectcode':     row.get('subjectcode') or '',
                    'subjectname':     row.get('subjectname') or '',
                    'instructor':      row.get('instructor')  or 'TBA',
                    'roomname':        row.get('roomname')    or 'TBA',
                    'daydesc':         day_name,
                    'start_time':      t_start,
                    'end_time':        t_end,
                    'lecturehours':    row.get('lecturehours')    or 0,
                    'laboratoryhours': row.get('laboratoryhours') or 0,
                    'creditunits':     row.get('creditunits')     or 0,
                    'total_hours':     total_hours,
                }

            if not full_days:
                t_raw = time_parts[0] if time_parts else ''
                ts, te = parse_time_range(t_raw) if t_raw else (None, None)
                result.append(_make_row(None, ts, te))
                continue

            n_days, n_times = len(full_days), len(time_parts)
            if n_times == 0:
                for day_name in full_days:
                    result.append(_make_row(day_name, None, None))
            elif n_days == 1:
                # All time blocks on the single listed day
                for t_seg in time_parts:
                    ts, te = parse_time_range(t_seg)
                    result.append(_make_row(full_days[0], ts, te))
            elif total_hours <= 0:
                # Hours unknown: cycle through blocks for all days
                for i, day_name in enumerate(full_days):
                    ts, te = parse_time_range(time_parts[i % n_times])
                    result.append(_make_row(day_name, ts, te))
            else:
                _block_h = [_hist_slot_hours(t) for t in time_parts]
                _hours_if_shared = sum(_block_h) * n_days
                if _hours_if_shared <= total_hours:
                    # Shared: every block on every day
                    for day_name in full_days:
                        for t_seg in time_parts:
                            ts, te = parse_time_range(t_seg)
                            result.append(_make_row(day_name, ts, te))
                else:
                    # Cycling: one block per day, wrap when more days than blocks
                    for i, day_name in enumerate(full_days):
                        ts, te = parse_time_range(time_parts[i % n_times])
                        result.append(_make_row(day_name, ts, te))

        # ── Merge both sources: schedule tables take priority over historical ─
        # schedule rows with time data → always included
        norm_subjects_with_time = {r['subjectcode'] for r in normalized_rows if r['start_time']}
        norm_timed = [dict(r) for r in normalized_rows if r['start_time']]
        # historical rows only for subjects not already covered by a timed schedule row
        hist_extra = [row for row in result if row['subjectcode'] not in norm_subjects_with_time]
        # schedule rows without time only if historical has nothing for that subject
        hist_subjects = {r['subjectcode'] for r in result}
        norm_untimed_fallback = [
            dict(r) for r in normalized_rows
            if not r['start_time'] and r['subjectcode'] not in hist_subjects
        ]
        combined = norm_timed + hist_extra + norm_untimed_fallback
        print(f"[DEBUG] combined={len(combined)} norm_timed={len(norm_timed)} hist_extra={len(hist_extra)} untimed_fb={len(norm_untimed_fallback)}")
        for c in combined[:3]:
            print(f"  out: subj={c['subjectcode']!r} day={c['daydesc']!r} start={c['start_time']!r} hours={c['total_hours']!r}")
        return jsonify(combined)
 
    except Exception as e:
        print(f"[get_offerings_schedule] ERROR: {e}")
        import traceback; traceback.print_exc()
        return jsonify([])
    finally:
        cur.close()
        conn.close()

@app.route('/api/debug_hist')
def debug_hist():
    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute('SELECT academicyearid, semesterid, COUNT(*) as cnt FROM historical_data GROUP BY academicyearid, semesterid ORDER BY academicyearid, semesterid')
        counts = [dict(r) for r in cur.fetchall()]
        cur.execute('SELECT DISTINCT "Program", "Year Level", academicyearid, semesterid FROM historical_data WHERE academicyearid = \'AY2425\' ORDER BY "Program", "Year Level"')
        ay2425 = [dict(r) for r in cur.fetchall()]
        cur.execute('SELECT semesterid, semestertype, academicyearid FROM semester ORDER BY academicyearid, semestertype')
        sems = [dict(r) for r in cur.fetchall()]
        return jsonify({'counts': counts, 'ay2425_programs': ay2425, 'semesters': sems})
    finally:
        cur.close(); conn.close()

@app.route('/api/schedule/diagnose')
def schedule_diagnose():
    """Diagnostic endpoint — shows exactly what data exists for the given filters."""
    prog = request.args.get('program', '')
    yl   = request.args.get('year_level', '1')
    sem  = request.args.get('semester', 'B')
    ay   = request.args.get('ay', '')
    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 1. Semester lookup
        cur.execute("SELECT semesterid, semestertype, academicyearid, semstartdate, semenddate FROM semester WHERE semestertype=%s AND academicyearid=%s LIMIT 1", (sem, ay))
        sem_row = dict(cur.fetchone()) if cur.rowcount else None

        # 2. All semesters
        cur.execute("SELECT semesterid, semestertype, academicyearid FROM semester ORDER BY academicyearid, semestertype")
        all_sems = [dict(r) for r in cur.fetchall()]

        # 3. Historical data count for this filter
        cur.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN "Time" IS NOT NULL AND "Time" != '' THEN 1 ELSE 0 END) as with_time
            FROM historical_data
            WHERE REGEXP_REPLACE("Program", '\\s+\\d+$', '') ILIKE %s
              AND CAST("Year Level" AS TEXT) = %s
              AND academicyearid = %s
        """, (prog, str(yl), ay))
        hist_count = dict(cur.fetchone())

        # 4. Historical with exact semester match
        sem_id = sem_row['semesterid'] if sem_row else None
        if sem_id:
            cur.execute("""SELECT COUNT(*) as cnt FROM historical_data
                WHERE REGEXP_REPLACE("Program",'\\s+\\d+$','') ILIKE %s
                AND CAST("Year Level" AS TEXT)=%s AND academicyearid=%s AND semesterid=%s
            """, (prog, str(yl), ay, sem_id))
            hist_sem_count = cur.fetchone()['cnt']
        else:
            hist_sem_count = 'N/A (semester not found)'

        # 5. All historical programs for this AY
        cur.execute("SELECT DISTINCT \"Program\", \"Year Level\", semesterid, academicyearid FROM historical_data WHERE academicyearid=%s ORDER BY \"Program\", \"Year Level\"", (ay,))
        hist_programs = [dict(r) for r in cur.fetchall()]

        # 6. Published schedule_sessions count
        cur.execute("""
            SELECT COUNT(ss.*) as sessions_count
            FROM schedule_version sv
            JOIN schedule sc ON sv.scheduleid=sc.scheduleid AND sv.status='Published'
            JOIN sections sec ON sc.sectionid=sec.sectionid
            JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
            JOIN academic_offering ao ON pyl.academicofferingid=ao.academicofferingid
            LEFT JOIN schedule_sessions ss ON ss.versionid=sv.versionid
            WHERE ao.offeringcode=%s AND pyl.yearlevel=%s
        """, (prog, int(yl)))
        sched_row = cur.fetchone()

        return jsonify({
            'filters': {'program': prog, 'year_level': yl, 'semester': sem, 'ay': ay},
            'semester_lookup': sem_row,
            'all_semesters': all_sems,
            'historical_for_ay_any_sem': hist_count,
            'historical_matching_semester': hist_sem_count,
            'all_hist_programs_in_ay': hist_programs,
            'published_sessions_count': sched_row['sessions_count'] if sched_row else 0,
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()})
    finally:
        cur.close(); conn.close()

@app.route('/api/export_schedule')
def export_schedule():
    if session.get('role') not in ('Academic Head', 'Admin'): return redirect(url_for('login'))
    prog = request.args.get('program', '')
    yl   = request.args.get('year_level', '')
    sem  = request.args.get('semester', '')
    ay   = request.args.get('ay', '')

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # ── 1. Normalized schedule_sessions (current schedule) ────────────
        norm_filters = []
        norm_params  = []
        norm_q = """
            SELECT
                COALESCE(f.lastname || ', ' || f.firstname || COALESCE(' ' || f.middlename, ''), 'TBA') AS "Instructor",
                sub.subjectcode   AS "SubjectCode",
                sub.subjectname   AS "SubjectName",
                COALESCE(sub.lecturehours, 0)    AS "LectureHours",
                COALESCE(sub.laboratoryhours, 0) AS "LaboratoryHours",
                COALESCE(sub.creditunits, 0)     AS "CreditUnits",
                ao.offeringcode  AS "Program",
                pyl.yearlevel    AS "YearLevel",
                (COALESCE(sub.lecturehours,0) + COALESCE(sub.laboratoryhours,0)) AS "Hours",
                ss.daydesc       AS "Day/s",
                TO_CHAR(ts_s.timevalue,'HH12:MI AM') || ' - ' || TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS "Time",
                COALESCE(r.roomname, 'TBA') AS "Room"
            FROM schedule_version sv
            JOIN schedule sc              ON sv.scheduleid = sc.scheduleid AND sv.status = 'Published'
            JOIN curriculumsubject cs      ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub               ON cs.subjectcode = sub.subjectcode
            JOIN sections sec              ON sc.sectionid = sec.sectionid
            JOIN program_yearlevel pyl     ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN academic_offering ao      ON pyl.academicofferingid = ao.academicofferingid
            LEFT JOIN faculty f            ON sc.employeenumber = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid = sv.versionid
            LEFT JOIN room r               ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s        ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e        ON ss.endtimeid   = ts_e.timeid
        """
        if prog: norm_filters.append("ao.offeringcode = %s"); norm_params.append(prog)
        if yl:   norm_filters.append("pyl.yearlevel = %s");   norm_params.append(int(yl))
        if sem or ay:
            sem_sub = "SELECT semesterid FROM semester WHERE TRUE"
            if sem: sem_sub += " AND semestertype = %s"; norm_params.append(sem)
            if ay:  sem_sub += " AND academicyearid = %s"; norm_params.append(ay)
            sem_sub += " LIMIT 1"
            norm_filters.append(f"sc.semesterid = ({sem_sub})")
        if norm_filters:
            norm_q += " WHERE " + " AND ".join(norm_filters)
        norm_q += " ORDER BY f.lastname, sub.subjectcode, ss.daydesc NULLS LAST"
        cur.execute(norm_q, norm_params)
        norm_rows = cur.fetchall()

        # ── 2. historical_data fallback ───────────────────────────────────
        hist_filters = ["TRUE"]
        hist_params  = []
        if prog: hist_filters.append('REGEXP_REPLACE("Program", \'\\s+\\d+$\', \'\') ILIKE %s'); hist_params.append(prog)
        if yl:   hist_filters.append('CAST("Year Level" AS TEXT) = %s'); hist_params.append(str(yl))
        if ay:   hist_filters.append("academicyearid = %s"); hist_params.append(ay)
        if sem:
            hist_filters.append("""semesterid = (
                SELECT semesterid FROM semester WHERE semestertype = %s
                AND (%s = '' OR academicyearid = %s) LIMIT 1
            )""")
            hist_params.extend([sem, ay, ay])
        hist_q = """
            SELECT
                "Instructor"        AS "Instructor",
                "Subject Code"      AS "SubjectCode",
                "Subject Name"      AS "SubjectName",
                "Lecture Hours"     AS "LectureHours",
                "Laboratory Hours"  AS "LaboratoryHours",
                "Credit Units"      AS "CreditUnits",
                "Program"           AS "Program",
                "Year Level"        AS "YearLevel",
                "Hours"             AS "Hours",
                "Day/s"             AS "Day/s",
                "Time"              AS "Time",
                "Room"              AS "Room"
            FROM historical_data
            WHERE """ + " AND ".join(hist_filters) + """
            ORDER BY "Instructor", "Subject Code"
        """
        cur.execute(hist_q, hist_params)
        hist_rows = cur.fetchall()

        # Same logic as API: prefer normalized rows with time, fall back to historical
        norm_exp_with_time = {r['SubjectCode'] for r in norm_rows if r['Time']}
        norm_timed_exp     = [r for r in norm_rows if r['Time']]
        hist_exp_extra     = [r for r in hist_rows if r['SubjectCode'] not in norm_exp_with_time]
        hist_exp_subjects  = {r['SubjectCode'] for r in hist_rows}
        norm_untimed_exp   = [r for r in norm_rows if not r['Time'] and r['SubjectCode'] not in hist_exp_subjects]
        all_rows = norm_timed_exp + hist_exp_extra + norm_untimed_exp

        cols = ["Instructor","SubjectCode","SubjectName","LectureHours","LaboratoryHours",
                "CreditUnits","Program","YearLevel","Hours","Day/s","Time","Room"]

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(cols)
        for row in all_rows:
            writer.writerow([row.get(c) or '' for c in cols])

        prog_label = prog or 'all'
        filename = f"schedule_{prog_label}_{ay}_{sem}.csv"
        return Response(output.getvalue(), mimetype='text/csv',
                        headers={"Content-Disposition": f"attachment; filename={filename}"})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════
#  SCHEDULE EXPORT  (multi-format: CSV / XLSX / DOCX / PDF)
# ══════════════════════════════════════════════════════════════

def _sch_exp_fetch(cur, ay_ids, sem_types, programs, year_levels):
    """Return merged list of schedule rows (normalized + historical fallback)."""
    nf, np_ = ["sv.status IN ('Published', 'Draft')"], []
    if ay_ids:
        nf.append(f"ay.academicyearid IN ({','.join(['%s']*len(ay_ids))})"); np_.extend(ay_ids)
    if sem_types:
        nf.append(f"sem.semestertype IN ({','.join(['%s']*len(sem_types))})"); np_.extend(sem_types)
    if programs:
        nf.append(f"UPPER(ao.offeringcode) IN ({','.join(['%s']*len(programs))})"); np_.extend([p.upper() for p in programs])
    if year_levels:
        nf.append(f"pyl.yearlevel IN ({','.join(['%s']*len(year_levels))})"); np_.extend(year_levels)

    norm_q = """
        SELECT
            COALESCE(f.lastname||', '||f.firstname||COALESCE(' '||f.middlename,''),'TBA') AS "Instructor",
            sub.subjectcode   AS "SubjectCode",
            sub.subjectname   AS "SubjectName",
            COALESCE(sub.lecturehours,0)    AS "LectureHours",
            COALESCE(sub.laboratoryhours,0) AS "LaboratoryHours",
            COALESCE(sub.creditunits,0)     AS "CreditUnits",
            ao.offeringcode  AS "Program",
            pyl.yearlevel    AS "YearLevel",
            sec.sectionname  AS "Section",
            (COALESCE(sub.lecturehours,0)+COALESCE(sub.laboratoryhours,0)) AS "Hours",
            ss.daydesc       AS "Day/s",
            CASE WHEN ts_s.timevalue IS NOT NULL AND ts_e.timevalue IS NOT NULL
                 THEN TO_CHAR(ts_s.timevalue,'HH12:MI AM')||' - '||TO_CHAR(ts_e.timevalue,'HH12:MI AM')
                 ELSE NULL END AS "Time",
            COALESCE(r.roomname,'TBA') AS "Room",
            ay.yearstart||'-'||ay.yearend AS "AcademicYear",
            sem.semestertype AS "SemesterType"
        FROM schedule_version sv
        JOIN schedule sc          ON sv.scheduleid=sc.scheduleid
        JOIN curriculumsubject cs  ON sc.curriculumsubjectid=cs.curriculumsubjectid
        JOIN subject sub           ON cs.subjectcode=sub.subjectcode
        JOIN sections sec          ON sc.sectionid=sec.sectionid
        JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
        JOIN academic_offering ao  ON pyl.academicofferingid=ao.academicofferingid
        JOIN semester sem          ON sc.semesterid=sem.semesterid
        JOIN academicyear ay       ON sem.academicyearid=ay.academicyearid
        LEFT JOIN faculty f        ON sc.employeenumber=f.employeenumber
        LEFT JOIN schedule_sessions ss ON ss.versionid=sv.versionid
        LEFT JOIN room r           ON ss.roomid=r.roomid
        LEFT JOIN timeslot ts_s    ON ss.starttimeid=ts_s.timeid
        LEFT JOIN timeslot ts_e    ON ss.endtimeid=ts_e.timeid
        WHERE """ + " AND ".join(nf) + """
        ORDER BY ao.offeringcode, pyl.yearlevel, sub.subjectcode, ss.daydesc NULLS LAST
    """
    cur.execute(norm_q, np_)
    norm_rows = cur.fetchall()

    hf, hp_ = ["TRUE"], []
    if ay_ids:
        hf.append(f"hd.academicyearid IN ({','.join(['%s']*len(ay_ids))})"); hp_.extend(ay_ids)
    if sem_types:
        ay_clause = (f" AND s2.academicyearid IN ({','.join(['%s']*len(ay_ids))})" if ay_ids else '')
        hf.append(f"hd.semesterid IN (SELECT semesterid FROM semester s2 WHERE s2.semestertype IN ({','.join(['%s']*len(sem_types))}){ay_clause})")
        hp_.extend(sem_types)
        if ay_ids: hp_.extend(ay_ids)
    if programs:
        hf.append(f"UPPER(REGEXP_REPLACE(hd.\"Program\",'\\s+\\d+$','')) IN ({','.join(['%s']*len(programs))})")
        hp_.extend([p.upper() for p in programs])
    if year_levels:
        # "Year Level" may be TEXT in historical_data; cast for safe comparison
        hf.append(f"CAST(hd.\"Year Level\" AS TEXT) IN ({','.join(['%s']*len(year_levels))})")
        hp_.extend([str(y) for y in year_levels])

    hist_q = """
        SELECT
            hd."Instructor"        AS "Instructor",
            hd."Subject Code"      AS "SubjectCode",
            hd."Subject Name"      AS "SubjectName",
            hd."Lecture Hours"     AS "LectureHours",
            hd."Laboratory Hours"  AS "LaboratoryHours",
            hd."Credit Units"      AS "CreditUnits",
            hd."Program"           AS "Program",
            hd."Year Level"        AS "YearLevel",
            NULL                   AS "Section",
            hd."Hours"             AS "Hours",
            hd."Day/s"             AS "Day/s",
            hd."Time"              AS "Time",
            hd."Room"              AS "Room",
            ay.yearstart||'-'||ay.yearend AS "AcademicYear",
            sem.semestertype AS "SemesterType"
        FROM historical_data hd
        LEFT JOIN academicyear ay ON hd.academicyearid=ay.academicyearid
        LEFT JOIN semester sem    ON hd.semesterid=sem.semesterid
        WHERE """ + " AND ".join(hf) + """
        ORDER BY hd."Program", hd."Year Level", hd."Subject Code"
    """
    cur.execute(hist_q, hp_)
    hist_rows = cur.fetchall()

    norm_with_time = {r['SubjectCode'] for r in norm_rows if r['Time']}
    norm_timed     = [r for r in norm_rows if r['Time']]
    hist_extra     = [r for r in hist_rows if r['SubjectCode'] not in norm_with_time]
    hist_codes     = {r['SubjectCode'] for r in hist_rows}
    norm_untimed   = [r for r in norm_rows if not r['Time'] and r['SubjectCode'] not in hist_codes]
    return norm_timed + hist_extra + norm_untimed


def _sch_exp_groups(rows):
    g = {}
    for r in rows:
        p  = r['Program']   or 'Unknown'
        yl = r['YearLevel'] or 0
        g.setdefault(p, {}).setdefault(yl, []).append(r)
    return {p: dict(sorted(ylmap.items())) for p, ylmap in sorted(g.items())}


@app.route('/academic/schedule/export/count', methods=['POST'])
def schedule_export_count():
    if session.get('role') not in ('Academic Head', 'Admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    data        = request.get_json() or {}
    ay_ids      = data.get('ay_ids', [])
    sem_types   = data.get('sem_types', [])
    programs    = data.get('programs', [])
    year_levels = [int(y) for y in data.get('year_levels', [])]
    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        rows   = _sch_exp_fetch(cur, ay_ids, sem_types, programs, year_levels)
        groups = _sch_exp_groups(rows)
        return jsonify({'count': len(rows), 'programs': len(groups)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


@app.route('/academic/schedule/export', methods=['POST'])
def schedule_export_multi():
    if session.get('role') not in ('Academic Head', 'Admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    data        = request.get_json() or {}
    ay_ids      = data.get('ay_ids', [])
    sem_types   = data.get('sem_types', [])
    programs    = data.get('programs', [])
    year_levels = [int(y) for y in data.get('year_levels', [])]
    formats     = [f.lower() for f in data.get('formats', ['csv'])]
    filename    = (data.get('filename', '') or 'schedule_export').strip()
    for ext in ('.pdf', '.docx', '.xlsx', '.csv', '.zip'):
        if filename.lower().endswith(ext):
            filename = filename[:-len(ext)]

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        rows   = _sch_exp_fetch(cur, ay_ids, sem_types, programs, year_levels)
        groups = _sch_exp_groups(rows)
        sem_map    = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}
        sem_labels = ', '.join(sem_map.get(s, s) for s in sorted(sem_types)) if sem_types else 'All Semesters'

        if len(formats) == 1:
            fmt = formats[0]
            if fmt == 'csv':
                out, mime, ext = _sch_gen_csv(rows), 'text/csv', '.csv'
            elif fmt == 'xlsx':
                out, mime, ext = _sch_gen_xlsx(rows, groups), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'
            elif fmt == 'docx':
                out, mime, ext = _sch_gen_docx(rows, groups, sem_labels), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'
            elif fmt == 'pdf':
                out, mime, ext = _sch_gen_pdf(rows, groups, sem_labels), 'application/pdf', '.pdf'
            else:
                return jsonify({'error': f'Unknown format: {fmt}'}), 400
            return Response(out, mimetype=mime,
                            headers={'Content-Disposition': f'attachment; filename="{filename}{ext}"'})
        else:
            import zipfile
            buf = io.BytesIO()
            fmt_map = {
                'csv':  (lambda: _sch_gen_csv(rows),                   '.csv'),
                'xlsx': (lambda: _sch_gen_xlsx(rows, groups),          '.xlsx'),
                'docx': (lambda: _sch_gen_docx(rows, groups, sem_labels), '.docx'),
                'pdf':  (lambda: _sch_gen_pdf(rows, groups, sem_labels),  '.pdf'),
            }
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for fmt in formats:
                    if fmt in fmt_map:
                        fn, ext = fmt_map[fmt]
                        zf.writestr(filename + ext, fn())
            buf.seek(0)
            return Response(buf.read(), mimetype='application/zip',
                            headers={'Content-Disposition': f'attachment; filename="{filename}.zip"'})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


def _sch_gen_csv(rows):
    cols = ['Instructor','SubjectCode','SubjectName','LectureHours','LaboratoryHours',
            'CreditUnits','Program','YearLevel','Section','Hours','Day/s','Time','Room',
            'AcademicYear','SemesterType']
    out = io.StringIO()
    w   = csv.writer(out)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c) or '' for c in cols])
    return out.getvalue().encode('utf-8-sig')


def _sch_gen_xlsx(rows, groups):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb  = Workbook()
    ws  = wb.active
    ws.title = 'Schedule'

    hdr_fill  = PatternFill('solid', fgColor='440000')
    prog_fill = PatternFill('solid', fgColor='5C0000')
    yl_fill   = PatternFill('solid', fgColor='7A0000')
    wht_font  = Font(bold=True, color='FFFFFF', size=10)
    thin      = Side(style='thin', color='DDDDDD')
    brd       = Border(left=thin, right=thin, top=thin, bottom=thin)

    HEADERS  = ['Instructor','Subject Code','Subject Description',
                'Lec Hrs','Lab Hrs','Units','Section','Day/s','Time','Hours','Room']
    COL_KEYS = ['Instructor','SubjectCode','SubjectName',
                'LectureHours','LaboratoryHours','CreditUnits','Section','Day/s','Time','Hours','Room']
    YL_LBL   = {1:'FIRST YEAR',2:'SECOND YEAR',3:'THIRD YEAR',4:'FOURTH YEAR',5:'FIFTH YEAR'}
    NCOLS    = len(HEADERS)
    COL_W    = [22, 14, 38, 7, 7, 7, 16, 12, 22, 7, 14]

    rn = 1
    for prog, ylmap in groups.items():
        ws.merge_cells(start_row=rn, start_column=1, end_row=rn, end_column=NCOLS)
        c = ws.cell(rn, 1, prog)
        c.font = wht_font; c.fill = prog_fill
        c.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[rn].height = 22; rn += 1

        for yl, yl_rows in ylmap.items():
            ws.merge_cells(start_row=rn, start_column=1, end_row=rn, end_column=NCOLS)
            c = ws.cell(rn, 1, YL_LBL.get(yl, f'YEAR {yl}') if yl else 'UNCLASSIFIED')
            c.font = wht_font; c.fill = yl_fill
            c.alignment = Alignment(horizontal='left', vertical='center', indent=1)
            ws.row_dimensions[rn].height = 18; rn += 1

            for ci, h in enumerate(HEADERS, 1):
                c = ws.cell(rn, ci, h)
                c.font = wht_font; c.fill = hdr_fill; c.border = brd
                c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            ws.row_dimensions[rn].height = 28; rn += 1

            for i, r in enumerate(yl_rows):
                bg = 'FFFFFF' if i % 2 == 0 else 'FDF5F5'
                for ci, key in enumerate(COL_KEYS, 1):
                    c = ws.cell(rn, ci, r.get(key) or '')
                    c.fill = PatternFill('solid', fgColor=bg)
                    c.border = brd; c.font = Font(size=9)
                    c.alignment = Alignment(vertical='center')
                ws.row_dimensions[rn].height = 16; rn += 1

        rn += 1

    for i, w in enumerate(COL_W, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return buf.read()


def _sch_gen_docx(rows, groups, sem_label):
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    doc = Document()
    sec = doc.sections[0]
    sec.page_width  = Inches(13)
    sec.page_height = Inches(8.5)
    sec.left_margin = sec.right_margin  = Cm(1.5)
    sec.top_margin  = sec.bottom_margin = Cm(1.5)

    DARK  = RGBColor(0x5C, 0x00, 0x00)
    MED   = RGBColor(0x7A, 0x00, 0x00)
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)

    h1 = doc.add_heading('CLASS SCHEDULE', 0)
    h1.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in h1.runs:
        run.font.color.rgb = DARK; run.font.size = Pt(16)

    sp = doc.add_paragraph(sem_label)
    sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if sp.runs:
        sp.runs[0].font.color.rgb = MED; sp.runs[0].font.size = Pt(11)
    doc.add_paragraph()

    HDR_COLS = ['Instructor','Subject Code','Subject Description',
                'Lec','Lab','Units','Section','Day/s','Time','Hrs','Room']
    COL_KEYS = ['Instructor','SubjectCode','SubjectName',
                'LectureHours','LaboratoryHours','CreditUnits','Section','Day/s','Time','Hours','Room']
    YL_LBL   = {1:'First Year',2:'Second Year',3:'Third Year',4:'Fourth Year',5:'Fifth Year'}

    def _bg(cell, hex6):
        tc   = cell._tc
        tcPr = tc.get_or_add_tcPr()
        shd  = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear')
        shd.set(qn('w:color'), 'auto')
        shd.set(qn('w:fill'), hex6)
        tcPr.append(shd)

    def _hdr_cell(cell, text, sz=8):
        cell.text = ''
        run = cell.paragraphs[0].add_run(text)
        run.font.bold = True; run.font.color.rgb = WHITE; run.font.size = Pt(sz)
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        _bg(cell, '440000')
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    def _data_cell(cell, text, hex6, sz=8):
        cell.text = ''
        run = cell.paragraphs[0].add_run(str(text) if text is not None else '')
        run.font.size = Pt(sz)
        _bg(cell, hex6)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    for prog, ylmap in groups.items():
        h = doc.add_heading(prog, level=1)
        for run in h.runs:
            run.font.color.rgb = DARK; run.font.size = Pt(13)

        for yl, yl_rows in ylmap.items():
            h2 = doc.add_heading(YL_LBL.get(yl, f'Year {yl}').upper() if yl else 'UNCLASSIFIED', level=2)
            for run in h2.runs:
                run.font.color.rgb = MED; run.font.size = Pt(11)

            tbl = doc.add_table(rows=1 + len(yl_rows), cols=len(HDR_COLS))
            tbl.style = 'Table Grid'
            tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

            for ci, h_txt in enumerate(HDR_COLS):
                _hdr_cell(tbl.rows[0].cells[ci], h_txt)

            for ri, r in enumerate(yl_rows):
                bg = 'FFFFFF' if ri % 2 == 0 else 'FDF5F5'
                for ci, key in enumerate(COL_KEYS):
                    _data_cell(tbl.rows[ri + 1].cells[ci], r.get(key) or '', bg)

            doc.add_paragraph()

    buf = io.BytesIO()
    doc.save(buf); buf.seek(0)
    return buf.read()


def _sch_gen_pdf(rows, groups, sem_label):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)

    DARK   = colors.HexColor('#5C0000')
    MED    = colors.HexColor('#7A0000')
    LIGHT  = colors.HexColor('#A03030')
    STRIPE = colors.HexColor('#FDF5F5')
    WHITE  = colors.white
    LGRAY  = colors.HexColor('#DDDDDD')

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle('H1', parent=styles['Heading1'], textColor=DARK, fontSize=16, spaceAfter=4, alignment=TA_CENTER)
    h2 = ParagraphStyle('H2', parent=styles['Heading2'], textColor=MED,  fontSize=13, spaceAfter=2, alignment=TA_LEFT)
    h3 = ParagraphStyle('H3', parent=styles['Heading3'], textColor=LIGHT,fontSize=10, spaceAfter=2, alignment=TA_LEFT)
    sm = ParagraphStyle('SM', parent=styles['Normal'],   textColor=MED,  fontSize=10, spaceAfter=6, alignment=TA_CENTER)

    HDR_COLS = ['Instructor','Subj Code','Subject Description','Lec','Lab','Units','Section','Day/s','Time','Hrs','Room']
    COL_KEYS = ['Instructor','SubjectCode','SubjectName','LectureHours','LaboratoryHours','CreditUnits','Section','Day/s','Time','Hours','Room']
    YL_LBL   = {1:'First Year',2:'Second Year',3:'Third Year',4:'Fourth Year',5:'Fifth Year'}
    COL_W    = [3.8*cm, 2.2*cm, 5.5*cm, 1.1*cm, 1.1*cm, 1.1*cm, 2.5*cm, 1.8*cm, 3.5*cm, 1.1*cm, 2.3*cm]

    story = [Paragraph('CLASS SCHEDULE', h1), Paragraph(sem_label, sm), Spacer(1, 0.3*cm)]

    for prog, ylmap in groups.items():
        story.append(Paragraph(prog, h2))
        for yl, yl_rows in ylmap.items():
            yl_label = YL_LBL.get(yl, f'Year {yl}').upper() if yl else 'UNCLASSIFIED'
            story.append(Paragraph(yl_label, h3))

            tbl_data = [HDR_COLS]
            for r in yl_rows:
                tbl_data.append([str(r.get(k) or '') for k in COL_KEYS])

            tbl = Table(tbl_data, colWidths=COL_W, repeatRows=1)
            tbl.setStyle(TableStyle([
                ('BACKGROUND',     (0,0),  (-1,0),  DARK),
                ('TEXTCOLOR',      (0,0),  (-1,0),  WHITE),
                ('FONTNAME',       (0,0),  (-1,0),  'Helvetica-Bold'),
                ('FONTSIZE',       (0,0),  (-1,-1), 7),
                ('FONTNAME',       (0,1),  (-1,-1), 'Helvetica'),
                ('ALIGN',          (0,0),  (-1,0),  'CENTER'),
                ('VALIGN',         (0,0),  (-1,-1), 'MIDDLE'),
                ('GRID',           (0,0),  (-1,-1), 0.5, LGRAY),
                ('ROWBACKGROUNDS', (0,1),  (-1,-1), [WHITE, STRIPE]),
                ('LEFTPADDING',    (0,0),  (-1,-1), 3),
                ('RIGHTPADDING',   (0,0),  (-1,-1), 3),
                ('TOPPADDING',     (0,0),  (-1,-1), 3),
                ('BOTTOMPADDING',  (0,0),  (-1,-1), 3),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 0.3*cm))
        story.append(Spacer(1, 0.4*cm))

    doc.build(story)
    buf.seek(0)
    return buf.read()


def _insert_historical(cur, inst, s_code, subj_name, prog, yl, days_raw, time_raw, room, sem_id, ay_id, lec, lab, unit, hrs):
    # Guard column-length limits before inserting
    s_code   = (s_code   or '')[:15]
    inst     = (inst     or '')[:150]
    subj_name= (subj_name or '')[:150]
    prog     = (prog     or '')[:20]
    room     = (room     or '')[:100]
    days_raw = (days_raw or '')[:50]
    time_raw = (time_raw or '')[:100]
    cur.execute("""
        INSERT INTO historical_data (
            "Instructor", "Subject Code", "Subject Name", "Program", "Year Level",
            "Day/s", "Time", "Room", semesterid, academicyearid,
            "Lecture Hours", "Laboratory Hours", "Credit Units", "Hours"
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (inst, s_code, subj_name, prog, yl, days_raw, time_raw, room,
          sem_id, ay_id, lec, lab, unit, hrs))


@app.route('/academic/schedule/import', methods=['POST'])
def import_schedule():
    if session.get('role') != 'Academic Head': return redirect(url_for('login'))
    file, ay_id, sem_type = request.files.get('file'), request.form.get('ay_id'), request.form.get('semester_type')
    
    # HELPER: Converts empty strings or text to 0 for Integer columns
    def safe_int(val):
        if not val or str(val).strip() == "": return 0
        try:
            # Extract only digits (handles cases like "3 units")
            numeric_part = ''.join(filter(str.isdigit, str(val)))
            return int(numeric_part) if numeric_part else 0
        except: return 0

    DAY_MAP = {
        'M': 'Monday', 'MON': 'Monday',
        'T': 'Tuesday', 'TUE': 'Tuesday', 'TUES': 'Tuesday',
        'W': 'Wednesday', 'WED': 'Wednesday',
        'TH': 'Thursday', 'THU': 'Thursday', 'THUR': 'Thursday', 'THURS': 'Thursday',
        'F': 'Friday', 'FRI': 'Friday',
        'SAT': 'Saturday', 'S': 'Saturday',
        'SUN': 'Sunday',
    }

    def parse_days(days_raw):
        """Handle slash-separated ('M/TH', 'TF/W') and concatenated ('MTH', 'MTHS', 'FSAT') formats."""
        # Longest-first greedy tokenizer; S=Saturday must come after SAT/SUN
        _tokens = ['THURS', 'THUR', 'WED', 'SAT', 'SUN', 'THU', 'TH', 'MON', 'TUES', 'TUE', 'FRI', 'M', 'T', 'W', 'F', 'S']

        def _greedy(s):
            result, s = [], s.upper().strip()
            while s:
                for tok in _tokens:
                    if s.startswith(tok):
                        result.append(tok)
                        s = s[len(tok):]
                        break
                else:
                    s = s[1:]
            return result

        if '/' in days_raw:
            result = []
            for part in days_raw.split('/'):
                part = part.strip()
                if not part:
                    continue
                if part.upper() in DAY_MAP:
                    result.append(part.upper())
                else:
                    # e.g. 'TF' or 'MTHS' inside a slash-list
                    result.extend(_greedy(part))
            return result
        return _greedy(days_raw)

    def parse_time_str(t, force_pm=False):
        """Return HH:MM:SS string from a time token like '07:30', '07:30 AM', '07:30 PM'."""
        import re
        t = t.strip()
        m = re.match(r'(\d{1,2}):(\d{2})\s*(AM|PM)?', t, re.IGNORECASE)
        if not m:
            return None
        h, mi, meridiem = int(m.group(1)), int(m.group(2)), (m.group(3) or '').upper()
        if meridiem == 'PM' and h != 12:
            h += 12
        elif meridiem == 'AM' and h == 12:
            h = 0
        elif not meridiem and force_pm and h != 12:
            h += 12
        elif not meridiem:
            total_mins = h * 60 + mi
            # No AM/PM: anything before school-day start of 07:30 → PM
            if 60 <= total_mins < 7 * 60 + 30:
                h += 12
        return f"{h:02d}:{mi:02d}:00"

    def normalize_room(raw):
        r = raw.strip()
        if not r:
            return 'TBA'
        u = r.upper()
        if u in ('G', 'GYM', 'PUP GYM'):
            return 'PUP GYM'
        if u in ('Q', 'QUAD', 'LQ-QUAD'):
            return 'LQ-Quad'
        if u.startswith('LQ-'):
            return r  # already formatted
        return f'LQ-{r}'  # everything else gets the prefix

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT semesterid, semstartdate, semenddate
            FROM semester
            WHERE academicyearid = %s AND semestertype = %s
        """, (ay_id, sem_type))
        sem_res = cur.fetchone()
        if not sem_res:
            flash("Semester not found. Please check the academic year and semester settings.")
            return redirect(url_for('schedule'))

        sem_id  = sem_res['semesterid']
        sem_end = sem_res['semenddate']
        from datetime import date as _date, timedelta as _td
        today      = _date.today()
        # Current = semester has not ended yet (or no end date configured)
        is_current = not (sem_end and sem_end < today)
        print(f"\n[DEBUG IMPORT] ay_id={ay_id!r} sem_type={sem_type!r} sem_id={sem_id!r} sem_end={sem_end} is_current={is_current}")

        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)

        saved_current = 0
        saved_historical = 0

        for row in csv_input:
            row = {k.strip(): v.strip() for k, v in row.items() if k}

            inst       = row.get('Instructor', '')
            s_code     = row.get('SubjectCode') or row.get('Subject Code') or row.get('SubjectCo') or ''
            # Strip trailing year-level number from program (e.g. "BSIT 3" → "BSIT")
            import re as _re_prog
            prog       = _re_prog.sub(r'\s+\d+$', '', row.get('Program', '').strip()).strip()
            # Clamp yl to 1-5 — prevents smallint overflow when CSV has bad data in YearLevel column
            _yl_raw = safe_int(row.get('YearLevel') or row.get('Year Level')) or 1
            yl      = max(1, min(_yl_raw, 5)) if _yl_raw <= 32767 else 1
            lec        = min(safe_int(row.get('LectureHours') or row.get('Lecture Hours') or row.get('Lec')), 999)
            lab        = min(safe_int(row.get('LaboratoryHours') or row.get('Laboratory Hours') or row.get('Lab')), 999)
            unit       = min(safe_int(row.get('CreditUnits') or row.get('Credit Units') or row.get('Units')), 999)
            hrs        = min(safe_int(row.get('Hours')) or (lec + lab), 999)
            days_raw   = (row.get('Day/s') or row.get('Days') or '').strip()
            time_raw   = (row.get('Time') or '').strip()
            room_raw   = (row.get('Room') or '').strip()
            subj_name  = (row.get('SubjectName') or row.get('Subject Name') or '').strip()

            # Skip completely empty rows (e.g. trailing blank lines in CSV)
            if not inst and not s_code:
                continue

            inserted_as_current = False

            if is_current:
                # --- Resolve FKs needed for the normalized schedule tables ---

                # 1. Faculty by name — match only, never create new records
                emp_num = None
                if inst:
                    parts = inst.split(',', 1)
                    lastname      = parts[0].strip()
                    firstname_key = parts[1].strip().split()[0] if len(parts) > 1 and parts[1].strip() else ''

                    # Pass 1: last name + first word of first name
                    cur.execute("""
                        SELECT employeenumber FROM faculty
                        WHERE UPPER(lastname) = UPPER(%s) AND UPPER(firstname) LIKE UPPER(%s) || '%%'
                        LIMIT 1
                    """, (lastname, firstname_key))
                    r = cur.fetchone()
                    emp_num = r['employeenumber'] if r else None

                    # Pass 2: last name only
                    if not emp_num:
                        cur.execute("""
                            SELECT employeenumber FROM faculty
                            WHERE UPPER(lastname) = UPPER(%s)
                            LIMIT 1
                        """, (lastname,))
                        r = cur.fetchone()
                        emp_num = r['employeenumber'] if r else None
                    # No match → emp_num stays None; row will be saved to historical_data

                # 2. Curriculum subject — try program+subjectcode, fallback to subjectcode only
                cs_id = None
                if s_code and prog:
                    cur.execute("""
                        SELECT cs.curriculumsubjectid
                        FROM curriculumsubject cs
                        JOIN curriculum c ON cs.curriculumid = c.curriculumid
                        JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
                        WHERE ao.offeringcode = %s AND UPPER(cs.subjectcode) = UPPER(%s)
                        LIMIT 1
                    """, (prog, s_code))
                    r = cur.fetchone()
                    cs_id = r['curriculumsubjectid'] if r else None
                if not cs_id and s_code:
                    cur.execute("""
                        SELECT curriculumsubjectid FROM curriculumsubject
                        WHERE subjectcode = %s LIMIT 1
                    """, (s_code,))
                    r = cur.fetchone()
                    cs_id = r['curriculumsubjectid'] if r else None

                # 3. Section — try active first, then any, then any year level, then any in program
                sec_id = None
                if prog and yl:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                        JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
                        WHERE ao.offeringcode = %s AND pyl.yearlevel = %s AND sec.isactive = TRUE
                        ORDER BY sec.sectionname LIMIT 1
                    """, (prog, yl))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                if not sec_id and prog and yl:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                        JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
                        WHERE ao.offeringcode = %s AND pyl.yearlevel = %s
                        ORDER BY sec.sectionname LIMIT 1
                    """, (prog, yl))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                if not sec_id and prog:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                        JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
                        WHERE ao.offeringcode = %s
                        ORDER BY pyl.yearlevel, sec.sectionname LIMIT 1
                    """, (prog,))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None

                if cs_id and sec_id:
                    import re as _re2

                    # 4. Skip if already imported for this semester (prevent duplicates on re-import)
                    cur.execute("""
                        SELECT scheduleid FROM schedule
                        WHERE curriculumsubjectid = %s AND sectionid = %s AND semesterid = %s
                        LIMIT 1
                    """, (cs_id, sec_id, sem_id))
                    if cur.fetchone():
                        print(f"[IMPORT DUPLICATE SKIP] subj={s_code!r} prog={prog!r} yl={yl} cs_id={cs_id} sec_id={sec_id}")
                        inserted_as_current = True
                        saved_current += 1
                    else:
                        cur.execute("""
                            INSERT INTO schedule (curriculumsubjectid, sectionid, employeenumber, semesterid)
                            VALUES (%s, %s, %s, %s) RETURNING scheduleid
                        """, (cs_id, sec_id, emp_num, sem_id))
                        sched_id = cur.fetchone()['scheduleid']

                        cur.execute("""
                            INSERT INTO schedule_version (scheduleid, version_number, status)
                            VALUES (%s, 1, 'Published') RETURNING versionid
                        """, (sched_id,))
                        ver_id = cur.fetchone()['versionid']

                        # 5. Parse sessions
                        days_parts  = parse_days(days_raw)
                        # Split on "/" or newlines, then on spaces-between-ranges ("HH:MM-HH:MM HH:MM-HH:MM")
                        _raw_t = [b.strip() for b in _re2.split(r'[/\n]', time_raw) if b.strip()]
                        times_parts = []
                        for _blk in _raw_t:
                            _sub = _re2.split(r'(?<=\d)\s+(?=\d{1,2}:\d{2}\s*-)', _blk)
                            times_parts.extend([s.strip() for s in _sub if s.strip()])
                        rooms_parts = [r.strip() for r in room_raw.split('/') if r.strip()]

                        def _resolve_session(day_abbr, time_seg, room_seg):
                            day_full = DAY_MAP.get(day_abbr.upper(), day_abbr) if day_abbr else None

                            start_str = end_str = None
                            if time_seg and '-' in time_seg:
                                _parts = _re2.split(r'\s*-\s*', time_seg, maxsplit=1)
                                if len(_parts) == 2:
                                    _s_raw, _e_raw = _parts[0].strip(), _parts[1].strip()
                                    _e_merid = _re2.search(r'(AM|PM)\s*$', _e_raw, _re2.IGNORECASE)
                                    _s_merid = _re2.search(r'(AM|PM)', _s_raw, _re2.IGNORECASE)
                                    _force_pm = bool(_e_merid and _e_merid.group(1).upper() == 'PM' and not _s_merid)
                                    start_str = parse_time_str(_s_raw, force_pm=_force_pm)
                                    end_str   = parse_time_str(_e_raw)

                            s_id = e_id = None
                            if start_str:
                                cur.execute("SELECT timeid FROM timeslot WHERE timevalue = %s::time", (start_str,))
                                _r = cur.fetchone(); s_id = _r['timeid'] if _r else None
                            if end_str:
                                cur.execute("SELECT timeid FROM timeslot WHERE timevalue = %s::time", (end_str,))
                                _r = cur.fetchone(); e_id = _r['timeid'] if _r else None
                            # Shift unlabeled PM end time +12h if end <= start
                            if s_id and e_id and e_id <= s_id and end_str:
                                m2 = _re2.match(r'(\d{2}):(\d{2}):00', end_str)
                                if m2:
                                    eh = int(m2.group(1))
                                    if eh < 12:
                                        alt = f"{eh + 12:02d}:{m2.group(2)}:00"
                                        cur.execute("SELECT timeid FROM timeslot WHERE timevalue = %s::time", (alt,))
                                        _r = cur.fetchone()
                                        if _r: e_id = _r['timeid']

                            rm_id = None
                            if room_seg and room_seg.upper() not in ('TBA', ''):
                                norm_room = normalize_room(room_seg)
                                cur.execute("SELECT roomid FROM room WHERE UPPER(roomname) = UPPER(%s)", (norm_room,))
                                _r = cur.fetchone(); rm_id = _r['roomid'] if _r else None
                                if not rm_id and norm_room.upper().startswith('LQ-'):
                                    bare = norm_room[3:]
                                    cur.execute("SELECT roomid FROM room WHERE UPPER(roomname) = UPPER(%s)", (bare,))
                                    _r = cur.fetchone(); rm_id = _r['roomid'] if _r else None
                                if not rm_id:
                                    cur.execute("SELECT roomid FROM room WHERE UPPER(roomname) LIKE '%%' || UPPER(%s) || '%%' LIMIT 1", (room_seg.strip(),))
                                    _r = cur.fetchone(); rm_id = _r['roomid'] if _r else None
                            return day_full, s_id, e_id, rm_id

                        def _slot_hours(time_seg):
                            """Decimal hours for a 'HH:MM-HH:MM' range string."""
                            if not time_seg or '-' not in time_seg:
                                return 0.0
                            ps = _re2.split(r'\s*-\s*', time_seg, maxsplit=1)
                            if len(ps) != 2:
                                return 0.0
                            s = parse_time_str(ps[0].strip())
                            e = parse_time_str(ps[1].strip())
                            if not s or not e:
                                return 0.0
                            def _m(t): return int(t[:2]) * 60 + int(t[3:5])
                            dur = _m(e) - _m(s)
                            if dur <= 0:
                                dur += 720  # 12-hr PM correction
                            return dur / 60.0

                        def _room_for(idx):
                            if not rooms_parts: return ''
                            return rooms_parts[idx] if idx < len(rooms_parts) else rooms_parts[0]

                        n_days, n_times = len(days_parts), len(times_parts)
                        if n_days == 0 or n_times == 0:
                            session_plan = []
                        elif n_days == 1:
                            # All blocks on the single listed day
                            session_plan = [
                                (days_parts[0], times_parts[t], _room_for(t))
                                for t in range(n_times)
                            ]
                        elif hrs <= 0:
                            # Hours unknown: cycle through blocks for all days
                            session_plan = [
                                (days_parts[i], times_parts[i % n_times], _room_for(i % n_times))
                                for i in range(n_days)
                            ]
                        else:
                            _block_h = [_slot_hours(t) for t in times_parts]
                            _sum_h   = sum(_block_h)
                            _hours_if_shared = _sum_h * n_days
                            if _hours_if_shared <= hrs:
                                # Shared: every block applies to every day
                                session_plan = [
                                    (days_parts[d], times_parts[t], _room_for(t))
                                    for d in range(n_days) for t in range(n_times)
                                ]
                            else:
                                # Cycling: one block per day, wrap when more days than blocks
                                session_plan = [
                                    (days_parts[i], times_parts[i % n_times], _room_for(i % n_times))
                                    for i in range(n_days)
                                ]

                        sessions_inserted = 0
                        for day_abbr, time_seg, room_seg in session_plan:
                            day_full, start_id, end_id, room_id = _resolve_session(day_abbr, time_seg, room_seg)
                            if day_full and start_id and end_id:
                                cur.execute("""
                                    INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid)
                                    VALUES (%s, %s, %s, %s, %s)
                                """, (ver_id, day_full, start_id, end_id, room_id))
                                sessions_inserted += 1

                        if sessions_inserted == 0 and (days_raw.strip() or time_raw.strip()):
                            norm_hist_room = normalize_room(room_raw) if room_raw.strip() else ''
                            _insert_historical(cur, inst, s_code, subj_name, prog, yl, days_raw, time_raw, norm_hist_room, sem_id, ay_id, lec, lab, unit, hrs)
                            saved_historical += 1

                        inserted_as_current = True
                        saved_current += 1

            if not inserted_as_current:
                norm_hist_room = normalize_room(room_raw) if room_raw.strip() else ''
                _insert_historical(cur, inst, s_code, subj_name, prog, yl, days_raw, time_raw, norm_hist_room, sem_id, ay_id, lec, lab, unit, hrs)
                saved_historical += 1

        conn.commit()
        if is_current:
            msg = f"Import successful! {saved_current} row(s) saved to current schedule."
            if saved_historical:
                msg += f" {saved_historical} row(s) could not be matched and were saved to historical data."
        else:
            msg = f"Import successful! {saved_historical} row(s) saved to historical data (semester is not currently active)."
        flash(msg, 'success')
    except Exception as e:
        conn.rollback()
        print(f"Import Error Detail: {str(e)}")
        flash(f"Import failed: {str(e)}")
    finally: cur.close(); conn.close()
    return redirect(url_for('schedule'))


def _parse_sis_excel(file_obj):
    """Parse a PUP LQ Subject Offerings Excel file.

    Actual file structure (per program block):
      Row 1       : Title   "SUBJECT OFFERINGS FOR …"
      Row 2       : Yellow  "LOPEZ, QUEZON CAMPUS"  ← campus, skip
      Row 4       : Bold    "BACHELOR OF ELEMENTARY EDUCATION (BEED)"  ← PROGRAM HEADER
      Row 6       : Plain   "FIRST YEAR"            ← year-level marker
      Row 7       : Column  Instructor | Subject Code | Subject Description | …
      Rows 8+     : Data rows

    Program headers are BOLD rows (not necessarily yellow) whose text contains
    known programme keywords or a parenthesised code like (BSIT).
    Yellow rows are typically campus banners or instructor names — both are skipped.
    The Course column (G) holds section identifiers like "BEED 1" or "BSIT-2-A";
    program code and year level are also inferred from there as a fallback.
    """
    import openpyxl, re

    YEAR_MAP   = {'FIRST': 1, 'SECOND': 2, 'THIRD': 3, 'FOURTH': 4, 'FIFTH': 5}
    TOTAL_KW   = ('TOTAL', 'SUB-TOTAL', 'SUBTOTAL', 'GRAND TOTAL')
    HEADER_KW  = ('INSTRUCTOR', 'SUBJ', 'DESCRIPTION', 'LEC', 'LAB',
                  'CREDIT', 'HOURS', 'DAY', 'TIME', 'ROOM')
    SKIP_WORDS = ('DEAR FACULTY', 'YOU ARE HEREBY', 'TENTATIVELY ASSIGNED',
                  'FOLLOWING SUBJECTS', 'SEMESTER,')
    PROG_KW    = ('BACHELOR', 'MASTER', 'DIPLOMA', 'ASSOCIATE', 'DEGREE',
                  'BSIT', 'BSED', 'BSEE', 'BSCE', 'BSAM', 'BSND', 'BSHM',
                  'BSOA', 'BSARCH', 'BSBA', 'BSBIO', 'BSA', 'BSND',
                  'DCET', 'DCVET', 'DEET', 'DOMT', 'DIT',
                  'BPA', 'BEED', 'COLLEGE OF', 'DEPARTMENT OF', 'SCHOOL OF')

    # ── Canonical programme codes used in this campus (21 total) ─────────────
    # All PROG_NORM values and all pass-through codes MUST appear here.
    VALID_PROGS = frozenset({
        'BEED', 'BPA', 'BPAFA', 'BSARCH', 'BSA', 'BSAM',
        'BSBAFM', 'BSBAMM', 'BSBIO', 'BSCE', 'BSEDMT',
        'BSEE', 'BSHM', 'BSIT', 'BSND', 'BSOA',
        'DCET', 'DCVET', 'DEET', 'DIT', 'DOMT-LOM',
    })

    # Map every SIS-file variant → the matching canonical code above.
    PROG_NORM = {
        # ── BPA / BPAFA ──────────────────────────────────────────────────────
        'BPA-FA':   'BPAFA',
        'BPAFA':    'BPAFA',
        'BPAPFM':   'BPAFA',    # old erroneous alias
        # ── BSAM ─────────────────────────────────────────────────────────────
        'BSAME':    'BSAM',     # old erroneous alias
        # ── BSBA specializations ─────────────────────────────────────────────
        'BSBA-FM':  'BSBAFM',
        'BSBA FM':  'BSBAFM',
        'BSBAFM':   'BSBAFM',
        'BSBA-MM':  'BSBAMM',
        'BSBA MM':  'BSBAMM',
        'BSBAMM':   'BSBAMM',
        # ── BSED (Mathematics specialisation only at this campus) ─────────────
        'BSED':     'BSEDMT',
        'BSED-MT':  'BSEDMT',
        'BSED MT':  'BSEDMT',
        'BSEDMT':   'BSEDMT',
        # ── BSOA ─────────────────────────────────────────────────────────────
        'BASOA':    'BSOA',     # old erroneous alias
        'BSOA-LOA': 'BSOA',    # old erroneous alias
        'BSOALOA':  'BSOA',    # old erroneous alias
        # ── DCET = Diploma in Computer Engineering Technology ─────────────────
        'DCPET':    'DCET',     # old erroneous alias
        # ── DOMT-LOM (only LOM track at this campus) ─────────────────────────
        'DOMT-LOM': 'DOMT-LOM',
        'DOMT - LOM':'DOMT-LOM',
        'DOMTLOM':  'DOMT-LOM',
        'LOM':      'DOMT-LOM',
        'DOMT':     'DOMT-LOM',
        # Old combined codes that should now resolve to the single LOM track
        'DOMT-MOM':         'DOMT-LOM',
        'DOMT - MOM':       'DOMT-LOM',
        'DOMTMOM':          'DOMT-LOM',
        'MOM':              'DOMT-LOM',
        'DOMT-LOM/DOMT-MOM':'DOMT-LOM',
        'DOMTLOM/DOMTMOM':  'DOMT-LOM',
    }

    def _norm_prog(code):
        if not code: return ''
        # Strip spaces around hyphens (e.g. "DOMT - LOM" → "DOMT-LOM")
        normalised = re.sub(r'\s*-\s*', '-', code.strip()).upper()
        if normalised in PROG_NORM:
            return PROG_NORM[normalised]
        # Try first part of slash-combined codes (e.g. "BSBA-FM/BSBA-MM")
        first = normalised.split('/')[0].strip()
        if first and first != normalised and first in PROG_NORM:
            return PROG_NORM[first]
        # Fall back to global normaliser which handles full programme names
        # (e.g. "BACHELOR OF SCIENCE IN INFORMATION TECHNOLOGY" → "BSIT")
        global_result = _sis_norm_prog(code)
        if global_result:
            return global_result
        # Return the normalised uppercase form so VALID_PROGS lookup works
        return normalised

    def _is_yellow(cell):
        try:
            f = cell.fill
            if f.fill_type != 'solid': return False
            c = f.fgColor
            if c.type == 'rgb' and c.rgb and c.rgb != '00000000':
                rgb = c.rgb[-6:]
                r, g, b = int(rgb[0:2],16), int(rgb[2:4],16), int(rgb[4:6],16)
                return r > 180 and g > 180 and b < 120
        except:
            pass
        return False

    def _is_bold(cell):
        try:
            return bool(cell.font and cell.font.bold)
        except:
            return False

    def _cv(v):
        return str(v).strip() if v is not None else ''

    def _is_person_name(txt):
        """True when txt looks like  LASTNAME, FIRSTNAME [M.I.]"""
        t = txt.strip()
        if ',' not in t: return False
        before, after = t.split(',', 1)
        if not re.match(r'^[A-Za-z\s\-\.]+$', before.strip()): return False
        return bool(re.search(r'[A-Za-z]', after))

    def _detect_prog_header(vals, row):
        """
        Return (True, prog_name) when the row is a programme header.
        Detects:
          - bold row whose text contains programme keywords, OR
          - parenthesised code pattern  "… (BSIT)"
        Returns (False, '') otherwise.
        """
        non_empty = [v for v in vals if v]
        if not non_empty: return False, ''
        txt = ' '.join(non_empty).strip()
        tu  = txt.upper()

        # Ignore year-level rows
        if any(w + ' YEAR' in tu for w in YEAR_MAP): return False, ''
        # Ignore column header rows
        col_hit = sum(1 for kw in HEADER_KW if kw in tu)
        if col_hit >= 3: return False, ''
        # Ignore TOTAL rows
        if any(kw in tu for kw in TOTAL_KW): return False, ''

        has_prog_kw = any(kw in tu for kw in PROG_KW)
        has_code    = bool(re.search(r'\([A-Z][A-Z0-9\-/]{1,20}\)', tu))
        is_bold_row = _is_bold(row[0])

        if not (has_prog_kw or has_code): return False, ''
        # Ignore rows that look like data (many filled cells) but keep clear programme headers
        # Use a higher threshold to avoid dropping headers that span several merged/unmerged cells
        if len(non_empty) > 8 and not has_code: return False, ''

        # Extract short programme code from parentheses if present (supports DCET/DCPET style)
        m = re.search(r'\(([A-Z][A-Z0-9\-/\s]{1,20})\)', tu)
        if m:
            return True, m.group(1).strip()
        # Short all-caps token at start (e.g. "BSIT – Bachelor…")
        m2 = re.match(r'^([A-Z]{2,10}(?:[- ][A-Z]{1,5})?)\b', tu)
        if m2 and has_prog_kw:
            return True, m2.group(1).strip()
        # Use first non-empty cell as programme name (full name)
        return True, non_empty[0].strip()

    def _section_to_prog_yl(course):
        """
        Parse section code like "BEED 1", "BSIT-2-A", "BPA 3B" into (prog, yl).
        Returns ('', 0) if the code does not contain a digit (so plain names like
        'AMOLAR' are never treated as programme codes).
        """
        if not course: return '', 0
        c = course.strip().upper()
        if not re.search(r'\d', c): return '', 0          # must contain a digit
        # Extract only the leading uppercase letters before any hyphen/slash/space/digit.
        # Max 6 chars: longer runs are section-type suffixes (e.g. "BSOALOA"), not programme codes.
        m = re.match(r'^([A-Z]{2,8})', c)
        prog = m.group(1).strip() if m else ''
        if len(prog) > 6: prog = ''   # reject oversized tokens
        yl = 0
        for pat in (r'[^A-Z0-9](\d)[^0-9]', r'[^A-Z0-9](\d)$', r'[A-Z](\d)'):
            m2 = re.search(pat, c)
            if m2:
                n = int(m2.group(1))
                if 1 <= n <= 5: yl = n; break
        return prog, yl

    wb = openpyxl.load_workbook(file_obj, data_only=True)
    rows_out = []

    for ws in wb.worksheets:
        # Resolve merged cells
        mc_map = {}
        for rng in ws.merged_cells.ranges:
            top_val = ws.cell(rng.min_row, rng.min_col).value
            for ri in range(rng.min_row, rng.max_row + 1):
                for ci in range(rng.min_col, rng.max_col + 1):
                    mc_map[(ri, ci)] = top_val

        current_prog     = ''
        current_raw_prog = ''   # original code from file before normalization
        current_yl       = 1
        current_yl_label = ''   # full original year-level header text (e.g. "FIRST YEAR – BRIDGE COURSES")
        col_map          = {}

        for row in ws.iter_rows():
            vals        = [_cv(mc_map.get((c.row, c.column), c.value)) for c in row]
            actual_vals = [_cv(c.value) for c in row]   # raw cell values without merged-range propagation
            non_empty   = [v for v in vals if v]
            if not non_empty: continue

            full_upper = ' '.join(non_empty).upper()

            # ── Skip narrative letter-text rows ───────────────────────────
            if any(kw in full_upper for kw in SKIP_WORDS):
                continue

            # ── 1. Yellow row (campus banner or instructor name) → skip ───
            any_yellow = _is_yellow(row[0]) or any(_is_yellow(c) for c in row[1:4])
            if any_yellow:
                # If it looks like a person's name, it's an instructor header row
                # (instructor-organised SIS).  If it's a programme name, capture it.
                yt = vals[0] or (non_empty[0] if non_empty else '')
                yu = yt.upper()
                is_yr = any(w + ' YEAR' in yu for w in YEAR_MAP)
                if not is_yr:
                    if not _is_person_name(yt):
                        # Could be a genuine programme yellow header
                        ok, code = _detect_prog_header(vals, row)
                        if ok:
                            candidate = _norm_prog(code)
                            if candidate and candidate in VALID_PROGS:
                                # Recognised programme — update both context vars
                                current_raw_prog = code.strip().upper() if code else candidate
                                current_prog     = candidate
                            else:
                                # False positive (e.g. instructor name) — clear context
                                current_raw_prog = ''
                                current_prog     = ''
                            col_map = {}
                continue  # always skip yellow rows as data

            # ── 2. Bold programme header row ──────────────────────────────
            ok, prog_code = _detect_prog_header(vals, row)
            if ok:
                candidate = _norm_prog(prog_code)
                if candidate and candidate in VALID_PROGS:
                    # Known programme — save original file code alongside canonical
                    current_raw_prog = prog_code.strip().upper() if prog_code else candidate
                    current_prog     = candidate
                else:
                    # Unrecognised header (person name, room label, etc.) — clear context
                    current_raw_prog = ''
                    current_prog     = ''
                current_yl       = 1
                current_yl_label = ''
                col_map          = {}
                continue

            # ── 3. Year-level header ──────────────────────────────────────
            yl_found = False
            for w, n in YEAR_MAP.items():
                if w + ' YEAR' in full_upper:
                    current_yl = n
                    # Use actual_vals (no merged-cell propagation) so a merged header row
                    # like "FIRST YEAR" spanning 11 columns doesn't become
                    # "FIRST YEAR FIRST YEAR FIRST YEAR …" repeated 11 times.
                    actual_ne = [v for v in actual_vals if v]
                    raw_lbl = (actual_ne[0] if actual_ne else non_empty[0] if non_empty else '').strip()
                    current_yl_label = ' '.join(raw_lbl.split())  # collapse any internal whitespace
                    yl_found = True; break
            if yl_found:
                continue

            # ── 4. Column header row ──────────────────────────────────────
            upper_vals = [v.upper() for v in vals]
            hdr_score  = sum(1 for kw in HEADER_KW if any(kw in v for v in upper_vals))
            if hdr_score >= 4:
                col_map   = {}
                kw_fields = [
                    ('INSTRUCTOR', 'instructor'), ('SUBJ', 'subj_code'),
                    ('DESCRIPTION', 'subj_name'), ('LEC',  'lec_hrs'),
                    ('LAB',  'lab_hrs'),  ('CREDIT', 'credit_units'),
                    ('COURSE', 'course'), ('HOURS',  'hours'),
                    ('DAY',  'days'),     ('TIME',   'time'), ('ROOM', 'room'),
                ]
                used = set()
                for i, v in enumerate(upper_vals):
                    for kw, fld in kw_fields:
                        if kw in v and fld not in used:
                            col_map[fld] = i; used.add(fld); break
                continue

            # ── 5. Total / summary row ────────────────────────────────────
            if any(kw in full_upper for kw in TOTAL_KW):
                continue

            # ── 6. Data row ───────────────────────────────────────────────
            def gc(fld, di):
                idx = col_map.get(fld, di)
                return vals[idx] if idx < len(vals) else ''

            inst     = gc('instructor',   0)
            s_code   = ' '.join(gc('subj_code', 1).split())   # normalize internal whitespace
            subj_nm  = gc('subj_name',    2)
            lec_raw  = gc('lec_hrs',      3)
            lab_raw  = gc('lab_hrs',      4)
            unit_raw = gc('credit_units', 5)
            course   = gc('course',       6)
            hrs_raw  = gc('hours',        7)
            days_raw = gc('days',         8)
            time_raw = gc('time',         9)
            room_raw = gc('room',         10)

            # Subject code must be present and short (not narrative text)
            if not s_code or len(s_code) > 25:
                continue
            if any(kw in s_code.upper() for kw in SKIP_WORDS):
                continue
            # Skip continuation rows from vertically merged cells:
            # if the subject-code cell has no actual value it inherited its value
            # from a merged cell above and this row is formatting-only.
            s_code_idx = col_map.get('subj_code', 1)
            if s_code_idx < len(actual_vals) and not actual_vals[s_code_idx]:
                continue

            # ── Derive programme & year level ─────────────────────────────
            # Priority 1: explicit bold programme header (current_prog)
            # Priority 2: section code in Course column (must contain a digit)
            prog             = current_prog
            raw_prog_for_row = current_raw_prog   # from header (may be '' if header was invalid)
            yl               = current_yl

            sec_prog, sec_yl = _section_to_prog_yl(course)
            if not prog and sec_prog:
                prog             = sec_prog
                raw_prog_for_row = sec_prog       # program resolved from section code
            # Only fall back to the section-derived year level when no year-level header
            # has been seen yet.  In PUP SIS files the trailing digit in a section code
            # like "BEED 1-A" is the section-group number, not the year level, so
            # unconditionally overriding current_yl would pull subjects from every year
            # block into FIRST YEAR whenever Course starts with "<PROG> 1…".
            if sec_yl and not current_yl_label:
                yl = sec_yl

            prog = _norm_prog(prog)   # resolve to canonical programme code

            # Skip rows with no programme or a programme not in the official 21
            if not prog or prog not in VALID_PROGS:
                continue

            rows_out.append({
                'instructor': inst,  'subj_code': s_code,   'subj_name': subj_nm,
                'lec_hrs':  lec_raw, 'lab_hrs':   lab_raw,  'credit_units': unit_raw,
                'course':   course,  'hours':     hrs_raw,  'days': days_raw,
                'time':     time_raw,'room':       room_raw,
                'program':     prog,
                'raw_program': raw_prog_for_row or prog,  # original file code; fallback to canonical
                'year_level':  yl,
                'year_label':  current_yl_label,  # full original header text preserved from Excel
            })

    # ── Deduplicate across sections ───────────────────────────────────────────────
    # The SIS Excel has one data row per SECTION per subject.  E.g. ELED 101 may
    # appear 6 times (BEED 1-A through 1-F).  Collapse to one row per unique
    # subject code within each (program, year-level, year-label) group so the
    # preview shows the same number of subjects as the Excel curriculum list.
    #
    # year_label normalisation:
    #   "" (no header found)      → standard name  e.g. "FIRST YEAR"
    #   "FIRST YEAR"              → "FIRST YEAR"
    #   "FIRST YEAR – BRIDGE …"   → kept as-is (its own separate group)
    _YL_STD = {1:'FIRST YEAR', 2:'SECOND YEAR', 3:'THIRD YEAR', 4:'FOURTH YEAR', 5:'FIFTH YEAR'}

    def _label_key(label, yl):
        # Collapse ALL whitespace variants (tabs, double-spaces, non-breaking spaces)
        # to single ASCII spaces before comparing, so "FIRST  YEAR" == "FIRST YEAR".
        s = ' '.join((label or '').upper().split())
        return s if s else _YL_STD.get(yl, f'{yl} YEAR')

    def _code_key(code):
        return ' '.join((code or '').upper().split())

    from collections import OrderedDict as _OD
    seen = _OD()
    for r in rows_out:
        lk  = _label_key(r.get('year_label', ''), r['year_level'])
        key = (r['program'], r['year_level'], lk, _code_key(r['subj_code']))
        if key not in seen:
            r['year_label']     = lk          # store the normalised label
            r['section_count']  = 1
            r['conflict_notes'] = ''
            seen[key] = r

    deduped = list(seen.values())
    deduped.sort(key=lambda r: (
        r['program'],
        r['year_level'],
        r.get('year_label', '').upper(),
        r['subj_code'].upper(),
    ))
    return deduped


@app.route('/academic/schedule/import/sis/preview', methods=['POST'])
def sis_import_preview():
    if session.get('role') != 'Academic Head':
        return jsonify({'error': 'Unauthorized'}), 403

    file     = request.files.get('file')
    ay_id    = request.form.get('ay_id')
    sem_type = request.form.get('semester_type')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400

    try:
        raw_rows = _parse_sis_excel(file.stream)
    except Exception as e:
        return jsonify({'error': f'Could not parse Excel file: {e}'}), 400

    if not raw_rows:
        return jsonify({'error': 'No data rows found. Ensure the file is in SIS format.'}), 400

    def _si(val):
        if not val: return 0
        try:
            num = ''.join(filter(str.isdigit, str(val))); return int(num) if num else 0
        except: return 0

    def _nr(raw):
        r = raw.strip()
        if not r: return 'TBA'
        u = r.upper()
        if u in ('G', 'GYM', 'PUP GYM'): return 'PUP GYM'
        if u in ('Q', 'QUAD', 'LQ-QUAD'): return 'LQ-Quad'
        if u.startswith('LQ-'): return r
        return f'LQ-{r}'

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    preview_rows = []
    try:
        for item in raw_rows:
            inst     = item['instructor']
            s_code   = item['subj_code']
            prog     = item['program']
            yl       = item['year_level']
            room_raw = item['room']
            flags, status = [], 'ready'

            cs_id = None
            if s_code:
                if prog:
                    # 1. Match by offering code (most specific)
                    cur.execute("""
                        SELECT cs.curriculumsubjectid FROM curriculumsubject cs
                        JOIN curriculum c ON cs.curriculumid = c.curriculumid
                        JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
                        WHERE UPPER(ao.offeringcode)=UPPER(%s) AND UPPER(cs.subjectcode)=UPPER(%s) LIMIT 1
                    """, (prog, s_code))
                    r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None
                    # 2. Fall back to program code (handles canonical→offering code mismatch)
                    if not cs_id:
                        cur.execute("""
                            SELECT cs.curriculumsubjectid FROM curriculumsubject cs
                            JOIN curriculum c ON cs.curriculumid = c.curriculumid
                            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
                            WHERE UPPER(ao.programcode)=UPPER(%s) AND UPPER(cs.subjectcode)=UPPER(%s) LIMIT 1
                        """, (prog, s_code))
                        r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None
                if not cs_id:
                    cur.execute("SELECT curriculumsubjectid FROM curriculumsubject WHERE UPPER(subjectcode)=UPPER(%s) LIMIT 1", (s_code,))
                    r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None
            if not cs_id:
                status = 'blocked'
                flags.append(f'Subject "{s_code or "—"}" not in curriculum')

            emp_num = None
            if inst:
                parts = inst.split(',', 1)
                ln = parts[0].strip()
                fn = parts[1].strip().split()[0] if len(parts) > 1 and parts[1].strip() else ''
                if fn:
                    cur.execute("""
                        SELECT employeenumber FROM faculty
                        WHERE UPPER(lastname)=UPPER(%s) AND UPPER(firstname) LIKE UPPER(%s)||'%%' LIMIT 1
                    """, (ln, fn))
                    r = cur.fetchone(); emp_num = r['employeenumber'] if r else None
                if not emp_num:
                    cur.execute("SELECT employeenumber FROM faculty WHERE UPPER(lastname)=UPPER(%s) LIMIT 1", (ln,))
                    r = cur.fetchone(); emp_num = r['employeenumber'] if r else None
            if not emp_num:
                if status != 'blocked': status = 'warning'
                flags.append('Instructor not matched → TBA')

            sec_id = None
            if prog and yl:
                # 1. Exact offering code match — active sections only
                cur.execute("""
                    SELECT sec.sectionid FROM sections sec
                    JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
                    JOIN academic_offering ao ON pyl.academicofferingid=ao.academicofferingid
                    WHERE UPPER(ao.offeringcode)=UPPER(%s) AND pyl.yearlevel=%s AND sec.isactive=TRUE
                    ORDER BY sec.sectionname LIMIT 1
                """, (prog, yl))
                r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                # 2. Fall back to program code — active sections (canonical→offering code mismatch)
                if not sec_id:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
                        JOIN academic_offering ao ON pyl.academicofferingid=ao.academicofferingid
                        WHERE UPPER(ao.programcode)=UPPER(%s) AND pyl.yearlevel=%s AND sec.isactive=TRUE
                        ORDER BY sec.sectionname LIMIT 1
                    """, (prog, yl))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                # 3. Offering code match — include inactive sections
                if not sec_id:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
                        JOIN academic_offering ao ON pyl.academicofferingid=ao.academicofferingid
                        WHERE UPPER(ao.offeringcode)=UPPER(%s) AND pyl.yearlevel=%s
                        ORDER BY sec.sectionname LIMIT 1
                    """, (prog, yl))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                # 4. Program code match — include inactive sections
                if not sec_id:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
                        JOIN academic_offering ao ON pyl.academicofferingid=ao.academicofferingid
                        WHERE UPPER(ao.programcode)=UPPER(%s) AND pyl.yearlevel=%s
                        ORDER BY sec.sectionname LIMIT 1
                    """, (prog, yl))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None
            if not sec_id:
                status = 'blocked'
                flags.append(f'No section for "{prog or "—"}" Yr {yl}')

            room_display = room_raw or 'TBA'
            if room_raw and room_raw.upper() not in ('TBA', ''):
                norm_r = _nr(room_raw)
                cur.execute("SELECT roomid FROM room WHERE UPPER(roomname)=UPPER(%s)", (norm_r,))
                _rx = cur.fetchone()
                if not _rx and norm_r.upper().startswith('LQ-'):
                    cur.execute("SELECT roomid FROM room WHERE UPPER(roomname)=UPPER(%s)", (norm_r[3:],))
                    _rx = cur.fetchone()
                if not _rx:
                    cur.execute("SELECT roomid FROM room WHERE UPPER(roomname) LIKE '%%'||UPPER(%s)||'%%' LIMIT 1", (room_raw.strip(),))
                    _rx = cur.fetchone()
                if not _rx:
                    if status != 'blocked': status = 'warning'
                    flags.append(f'Room "{room_raw}" not found → TBA')
                    room_display = f'{room_raw} → TBA'

            preview_rows.append({
                **item,
                'lec_hrs': _si(item['lec_hrs']), 'lab_hrs': _si(item['lab_hrs']),
                'credit_units': _si(item['credit_units']), 'hours': _si(item['hours']),
                'status': status, 'flags': '; '.join(flags), 'room_display': room_display,
            })
    finally:
        cur.close(); conn.close()

    stats = {
        'ready':   sum(1 for rw in preview_rows if rw['status'] == 'ready'),
        'warning': sum(1 for rw in preview_rows if rw['status'] == 'warning'),
        'blocked': sum(1 for rw in preview_rows if rw['status'] == 'blocked'),
        'total':   len(preview_rows),
    }
    return jsonify({'rows': preview_rows, 'stats': stats, 'ay_id': ay_id, 'semester_type': sem_type})


@app.route('/academic/schedule/import/sis/confirm', methods=['POST'])
def sis_import_confirm():
    if session.get('role') != 'Academic Head':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data received'}), 400

    ay_id    = data.get('ay_id')
    sem_type = data.get('semester_type')
    rows     = data.get('rows', [])
    if not rows:
        return jsonify({'error': 'No rows to import'}), 400

    import re

    DAY_MAP = {
        'M': 'Monday', 'MON': 'Monday',
        'T': 'Tuesday', 'TUE': 'Tuesday', 'TUES': 'Tuesday',
        'W': 'Wednesday', 'WED': 'Wednesday',
        'TH': 'Thursday', 'THU': 'Thursday', 'THUR': 'Thursday', 'THURS': 'Thursday',
        'F': 'Friday', 'FRI': 'Friday',
        'SAT': 'Saturday', 'S': 'Saturday', 'SUN': 'Sunday',
    }

    def _si(val):
        if not val or str(val).strip() == '': return 0
        try:
            num = ''.join(filter(str.isdigit, str(val))); return int(num) if num else 0
        except: return 0

    def _pd(days_raw):
        _tk = ['THURS','THUR','WED','SAT','SUN','THU','TH','MON','TUES','TUE','FRI','M','T','W','F','S']
        def _g(s):
            res, s = [], s.upper().strip()
            while s:
                for t in _tk:
                    if s.startswith(t): res.append(t); s = s[len(t):]; break
                else: s = s[1:]
            return res
        if '/' in days_raw:
            out = []
            for p in days_raw.split('/'):
                p = p.strip()
                if not p: continue
                if p.upper() in DAY_MAP: out.append(p.upper())
                else: out.extend(_g(p))
            return out
        return _g(days_raw)

    def _pts(t, force_pm=False):
        t = t.strip()
        m = re.match(r'(\d{1,2}):(\d{2})\s*(AM|PM)?', t, re.IGNORECASE)
        if not m: return None
        h, mi, mer = int(m.group(1)), int(m.group(2)), (m.group(3) or '').upper()
        if mer == 'PM' and h != 12: h += 12
        elif mer == 'AM' and h == 12: h = 0
        elif not mer and force_pm and h != 12: h += 12
        elif not mer:
            if 60 <= h * 60 + mi < 7 * 60 + 30: h += 12
        return f"{h:02d}:{mi:02d}:00"

    def _nr(raw):
        r = raw.strip()
        if not r: return 'TBA'
        u = r.upper()
        if u in ('G', 'GYM', 'PUP GYM'): return 'PUP GYM'
        if u in ('Q', 'QUAD', 'LQ-QUAD'): return 'LQ-Quad'
        if u.startswith('LQ-'): return r
        return f'LQ-{r}'

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    saved_c = 0; saved_h = 0

    try:
        cur.execute("""
            SELECT semesterid, semstartdate, semenddate FROM semester
            WHERE academicyearid=%s AND semestertype=%s
        """, (ay_id, sem_type))
        sem_res = cur.fetchone()
        if not sem_res:
            return jsonify({'error': 'Semester not found'}), 400

        sem_id = sem_res['semesterid']
        e_dt   = sem_res['semenddate']
        from datetime import date as _date2
        _today2 = _date2.today()
        # Current = semester has not ended yet (or no end date configured)
        is_cur = not (e_dt and e_dt < _today2)

        for row in rows:
            inst     = row.get('instructor', '')
            s_code   = row.get('subj_code', '')
            subj_nm  = row.get('subj_name', '')
            prog     = re.sub(r'\s+\d+$', '', row.get('program', '').strip()).strip()
            yl       = max(1, min(_si(row.get('year_level')) or 1, 5))
            lec      = min(_si(row.get('lec_hrs')), 999)
            lab      = min(_si(row.get('lab_hrs')), 999)
            unit     = min(_si(row.get('credit_units')), 999)
            hrs      = min(_si(row.get('hours')) or lec + lab, 999)
            days_raw = str(row.get('days', '') or '').strip()
            time_raw = str(row.get('time', '') or '').strip()
            room_raw = str(row.get('room', '') or '').strip()

            if not inst and not s_code: continue
            ins_cur = False

            if is_cur:
                emp_num = None
                if inst:
                    parts = inst.split(',', 1)
                    ln = parts[0].strip()
                    fn = parts[1].strip().split()[0] if len(parts) > 1 and parts[1].strip() else ''
                    if fn:
                        cur.execute("""
                            SELECT employeenumber FROM faculty
                            WHERE UPPER(lastname)=UPPER(%s) AND UPPER(firstname) LIKE UPPER(%s)||'%%' LIMIT 1
                        """, (ln, fn))
                        r = cur.fetchone(); emp_num = r['employeenumber'] if r else None
                    if not emp_num:
                        cur.execute("SELECT employeenumber FROM faculty WHERE UPPER(lastname)=UPPER(%s) LIMIT 1", (ln,))
                        r = cur.fetchone(); emp_num = r['employeenumber'] if r else None

                cs_id = None
                if s_code and prog:
                    # 1. Match by offering code
                    cur.execute("""
                        SELECT cs.curriculumsubjectid FROM curriculumsubject cs
                        JOIN curriculum c ON cs.curriculumid=c.curriculumid
                        JOIN academic_offering ao ON c.academicofferingid=ao.academicofferingid
                        WHERE UPPER(ao.offeringcode)=UPPER(%s) AND UPPER(cs.subjectcode)=UPPER(%s) LIMIT 1
                    """, (prog, s_code))
                    r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None
                    # 2. Fall back to program code
                    if not cs_id:
                        cur.execute("""
                            SELECT cs.curriculumsubjectid FROM curriculumsubject cs
                            JOIN curriculum c ON cs.curriculumid=c.curriculumid
                            JOIN academic_offering ao ON c.academicofferingid=ao.academicofferingid
                            WHERE UPPER(ao.programcode)=UPPER(%s) AND UPPER(cs.subjectcode)=UPPER(%s) LIMIT 1
                        """, (prog, s_code))
                        r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None
                if not cs_id and s_code:
                    cur.execute("SELECT curriculumsubjectid FROM curriculumsubject WHERE UPPER(subjectcode)=UPPER(%s) LIMIT 1", (s_code,))
                    r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None

                sec_id = None
                if prog and yl:
                    # 1. Exact offering code — active sections only
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
                        JOIN academic_offering ao ON pyl.academicofferingid=ao.academicofferingid
                        WHERE UPPER(ao.offeringcode)=UPPER(%s) AND pyl.yearlevel=%s AND sec.isactive=TRUE
                        ORDER BY sec.sectionname LIMIT 1
                    """, (prog, yl))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                    # 2. Program code — active sections (canonical→offering code mismatch)
                    if not sec_id:
                        cur.execute("""
                            SELECT sec.sectionid FROM sections sec
                            JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
                            JOIN academic_offering ao ON pyl.academicofferingid=ao.academicofferingid
                            WHERE UPPER(ao.programcode)=UPPER(%s) AND pyl.yearlevel=%s AND sec.isactive=TRUE
                            ORDER BY sec.sectionname LIMIT 1
                        """, (prog, yl))
                        r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                    # 3. Offering code — include inactive sections
                    if not sec_id:
                        cur.execute("""
                            SELECT sec.sectionid FROM sections sec
                            JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
                            JOIN academic_offering ao ON pyl.academicofferingid=ao.academicofferingid
                            WHERE UPPER(ao.offeringcode)=UPPER(%s) AND pyl.yearlevel=%s
                            ORDER BY sec.sectionname LIMIT 1
                        """, (prog, yl))
                        r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                    # 4. Program code — include inactive sections
                    if not sec_id:
                        cur.execute("""
                            SELECT sec.sectionid FROM sections sec
                            JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
                            JOIN academic_offering ao ON pyl.academicofferingid=ao.academicofferingid
                            WHERE UPPER(ao.programcode)=UPPER(%s) AND pyl.yearlevel=%s
                            ORDER BY sec.sectionname LIMIT 1
                        """, (prog, yl))
                        r = cur.fetchone(); sec_id = r['sectionid'] if r else None

                if cs_id and sec_id:
                    cur.execute("""
                        SELECT scheduleid FROM schedule
                        WHERE curriculumsubjectid=%s AND sectionid=%s AND semesterid=%s LIMIT 1
                    """, (cs_id, sec_id, sem_id))
                    if cur.fetchone():
                        ins_cur = True; saved_c += 1
                    else:
                        cur.execute("""
                            INSERT INTO schedule (curriculumsubjectid, sectionid, employeenumber, semesterid)
                            VALUES (%s,%s,%s,%s) RETURNING scheduleid
                        """, (cs_id, sec_id, emp_num, sem_id))
                        sched_id = cur.fetchone()['scheduleid']
                        cur.execute("""
                            INSERT INTO schedule_version (scheduleid, version_number, status)
                            VALUES (%s, 1, 'Published') RETURNING versionid
                        """, (sched_id,))
                        ver_id = cur.fetchone()['versionid']

                        days_p  = _pd(days_raw) if days_raw else []
                        _rt     = [b.strip() for b in re.split(r'[/\n]', time_raw) if b.strip()] if time_raw else []
                        times_p = []
                        for _b in _rt:
                            _sub = re.split(r'(?<=\d)\s+(?=\d{1,2}:\d{2}\s*-)', _b)
                            times_p.extend([s.strip() for s in _sub if s.strip()])
                        rooms_p = [x.strip() for x in room_raw.split('/') if x.strip()] if room_raw else []

                        def _rs(da, ts, rs):
                            df = DAY_MAP.get(da.upper(), da) if da else None
                            ss2 = es2 = None
                            if ts and '-' in ts:
                                pp = re.split(r'\s*-\s*', ts, maxsplit=1)
                                if len(pp) == 2:
                                    sr, er = pp[0].strip(), pp[1].strip()
                                    em = re.search(r'(AM|PM)\s*$', er, re.IGNORECASE)
                                    sm = re.search(r'(AM|PM)', sr, re.IGNORECASE)
                                    ss2 = _pts(sr, force_pm=bool(em and em.group(1).upper()=='PM' and not sm))
                                    es2 = _pts(er)
                            si2 = ei2 = None
                            if ss2:
                                cur.execute("SELECT timeid FROM timeslot WHERE timevalue=%s::time", (ss2,))
                                _x = cur.fetchone(); si2 = _x['timeid'] if _x else None
                            if es2:
                                cur.execute("SELECT timeid FROM timeslot WHERE timevalue=%s::time", (es2,))
                                _x = cur.fetchone(); ei2 = _x['timeid'] if _x else None
                            if si2 and ei2 and ei2 <= si2 and es2:
                                m2 = re.match(r'(\d{2}):(\d{2}):00', es2)
                                if m2:
                                    eh2 = int(m2.group(1))
                                    if eh2 < 12:
                                        alt = f"{eh2+12:02d}:{m2.group(2)}:00"
                                        cur.execute("SELECT timeid FROM timeslot WHERE timevalue=%s::time", (alt,))
                                        _x = cur.fetchone()
                                        if _x: ei2 = _x['timeid']
                            ri2 = None
                            if rs and rs.upper() not in ('TBA', ''):
                                nr2 = _nr(rs)
                                cur.execute("SELECT roomid FROM room WHERE UPPER(roomname)=UPPER(%s)", (nr2,))
                                _x = cur.fetchone(); ri2 = _x['roomid'] if _x else None
                                if not ri2 and nr2.upper().startswith('LQ-'):
                                    cur.execute("SELECT roomid FROM room WHERE UPPER(roomname)=UPPER(%s)", (nr2[3:],))
                                    _x = cur.fetchone(); ri2 = _x['roomid'] if _x else None
                                if not ri2:
                                    cur.execute("SELECT roomid FROM room WHERE UPPER(roomname) LIKE '%%'||UPPER(%s)||'%%' LIMIT 1", (rs.strip(),))
                                    _x = cur.fetchone(); ri2 = _x['roomid'] if _x else None
                            return df, si2, ei2, ri2

                        def _sh(ts):
                            if not ts or '-' not in ts: return 0.0
                            pp = re.split(r'\s*-\s*', ts, maxsplit=1)
                            if len(pp) != 2: return 0.0
                            s2 = _pts(pp[0].strip()); e2 = _pts(pp[1].strip())
                            if not s2 or not e2: return 0.0
                            def _m(t): return int(t[:2])*60+int(t[3:5])
                            d = _m(e2)-_m(s2); return (d+720 if d<=0 else d)/60.0

                        def _rf(i):
                            if not rooms_p: return ''
                            return rooms_p[i] if i < len(rooms_p) else rooms_p[0]

                        nd, nt = len(days_p), len(times_p)
                        if nd == 0 or nt == 0:
                            sp = []
                        elif nd == 1:
                            sp = [(days_p[0], times_p[t], _rf(t)) for t in range(nt)]
                        elif hrs <= 0:
                            sp = [(days_p[i], times_p[i%nt], _rf(i%nt)) for i in range(nd)]
                        else:
                            bh = [_sh(t) for t in times_p]; sh2 = sum(bh)
                            if sh2 * nd <= hrs:
                                sp = [(days_p[d], times_p[t], _rf(t)) for d in range(nd) for t in range(nt)]
                            else:
                                sp = [(days_p[i], times_p[i%nt], _rf(i%nt)) for i in range(nd)]

                        si_cnt = 0
                        for da, ts, rs in sp:
                            df, s_id, e_id, rm_id = _rs(da, ts, rs)
                            if df and s_id and e_id:
                                cur.execute("""
                                    INSERT INTO schedule_sessions (versionid,daydesc,starttimeid,endtimeid,roomid)
                                    VALUES (%s,%s,%s,%s,%s)
                                """, (ver_id, df, s_id, e_id, rm_id))
                                si_cnt += 1

                        if si_cnt == 0 and (days_raw or time_raw):
                            nr3 = _nr(room_raw) if room_raw else ''
                            _insert_historical(cur, inst, s_code, subj_nm, prog, yl, days_raw, time_raw, nr3, sem_id, ay_id, lec, lab, unit, hrs)
                            saved_h += 1

                        ins_cur = True; saved_c += 1

            if not ins_cur:
                nr3 = _nr(room_raw) if room_raw else ''
                _insert_historical(cur, inst, s_code, subj_nm, prog, yl, days_raw, time_raw, nr3, sem_id, ay_id, lec, lab, unit, hrs)
                saved_h += 1

        conn.commit()
        msg = f'{saved_c} row(s) imported to schedule.'
        if saved_h:
            msg += f' {saved_h} row(s) could not be matched (no section or subject found in curriculum) and were saved to historical data.'
        return jsonify({'success': True, 'message': msg, 'saved_current': saved_c, 'saved_historical': saved_h})

    except Exception as e:
        conn.rollback()
        print(f'SIS Import Confirm Error: {e}')
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


# ──────────────────────────────────────────────────────────────────
#  UNIFIED SCHEDULE IMPORT — parsers + validation + endpoint
# ──────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════
# SHARED IMPORT HELPERS — used by CSV, DOCX, PDF (and XLSX) parsers
# ══════════════════════════════════════════════════════════════

_SIS_VALID_PROGS = frozenset({
    'BEED', 'BPA', 'BPAFA', 'BSARCH', 'BSA', 'BSAM',
    'BSBAFM', 'BSBAMM', 'BSBIO', 'BSCE', 'BSEDMT',
    'BSEE', 'BSHM', 'BSIT', 'BSND', 'BSOA',
    'DCET', 'DCVET', 'DEET', 'DIT', 'DOMT-LOM',
})

_SIS_PROG_NORM = {
    'BPA-FA': 'BPAFA', 'BPAPFM': 'BPAFA',
    'BSAME': 'BSAM',
    'BSBA-FM': 'BSBAFM', 'BSBA FM': 'BSBAFM', 'BSBAFM': 'BSBAFM',
    'BSBA-MM': 'BSBAMM', 'BSBA MM': 'BSBAMM', 'BSBAMM': 'BSBAMM',
    'BSED': 'BSEDMT', 'BSED-MT': 'BSEDMT', 'BSED MT': 'BSEDMT', 'BSEDMT': 'BSEDMT',
    'BASOA': 'BSOA', 'BSOA-LOA': 'BSOA', 'BSOALOA': 'BSOA',
    'DCPET': 'DCET',
    'DOMT-LOM': 'DOMT-LOM', 'DOMTLOM': 'DOMT-LOM',
    'LOM': 'DOMT-LOM',  'DOMT': 'DOMT-LOM',
    'DOMT-MOM': 'DOMT-LOM', 'DOMTMOM': 'DOMT-LOM', 'MOM': 'DOMT-LOM',
    'DOMT - LOM': 'DOMT-LOM', 'DOMT - MOM': 'DOMT-LOM',
    'DOMT-LOM/DOMT-MOM': 'DOMT-LOM', 'DOMTLOM/DOMTMOM': 'DOMT-LOM',
}

# Keyword phrases in full programme names, ordered most-specific first
_SIS_PROG_NAME_KEYS = (
    ('FISCAL ADMINISTRATION',              'BPAFA'),
    ('FINANCIAL MANAGEMENT',               'BSBAFM'),
    ('MARKETING MANAGEMENT',               'BSBAMM'),
    ('AGRIBUSINESS MANAGEMENT',            'BSAM'),
    ('OFFICE ADMINISTRATION',              'BSOA'),
    ('ELEMENTARY EDUCATION',               'BEED'),
    ('SECONDARY EDUCATION',                'BSEDMT'),
    ('SCIENCE IN ARCHITECTURE',            'BSARCH'),
    ('ACCOUNTANCY',                        'BSA'),
    ('CIVIL ENGINEERING TECHNOLOGY',       'DCVET'),
    ('COMPUTER ENGINEERING TECHNOLOGY',    'DCET'),
    ('ELECTRICAL ENGINEERING TECHNOLOGY',  'DEET'),
    ('CIVIL ENGINEERING',                  'BSCE'),
    ('ELECTRICAL ENGINEERING',             'BSEE'),
    ('HOSPITALITY MANAGEMENT',             'BSHM'),
    ('NUTRITION AND DIETETICS',            'BSND'),
    ('BIOLOGY',                            'BSBIO'),
    ('OFFICE MANAGEMENT TECHNOLOGY',       'DOMT-LOM'),
    # BSIT must match "SCIENCE IN IT" to avoid false-match on "DIPLOMA IN IT"
    ('SCIENCE IN INFORMATION TECHNOLOGY',  'BSIT'),
    ('DIPLOMA IN INFORMATION TECHNOLOGY',  'DIT'),   # specific before generic
    ('INFORMATION TECHNOLOGY',             'DIT'),
    ('PUBLIC ADMINISTRATION',              'BPA'),
)


def _sis_norm_prog(code):
    """
    Resolve a programme code or full name to a canonical _SIS_VALID_PROGS entry.
    Handles parenthesised codes ("... (BSIT)"), short codes, hyphen normalisation,
    slash-combined codes, and full programme name keyword matching.
    Returns '' when no valid match is found.
    """
    if not code: return ''
    import re as _re
    upr = code.strip().upper()

    # 1. Parenthesised code:  "BACHELOR OF SCIENCE IN IT (BSIT)"
    m = _re.search(r'\(([A-Z][A-Z0-9\-/\s]{1,20})\)', upr)
    if m:
        ext = _re.sub(r'\s*-\s*', '-', m.group(1).strip())
        if ext in _SIS_PROG_NORM: return _SIS_PROG_NORM[ext]
        if ext in _SIS_VALID_PROGS: return ext
        f = ext.split('/')[0].strip()
        if f in _SIS_PROG_NORM: return _SIS_PROG_NORM[f]
        if f in _SIS_VALID_PROGS: return f

    # 2. Direct code (normalise hyphen spacing)
    norm = _re.sub(r'\s*-\s*', '-', upr)
    if norm in _SIS_PROG_NORM: return _SIS_PROG_NORM[norm]
    if norm in _SIS_VALID_PROGS: return norm
    f = norm.split('/')[0].strip()
    if f != norm:
        if f in _SIS_PROG_NORM: return _SIS_PROG_NORM[f]
        if f in _SIS_VALID_PROGS: return f

    # 3. Full-name keyword matching
    for phrase, canonical in _SIS_PROG_NAME_KEYS:
        if phrase in upr:
            return canonical

    return ''


def _sis_post_process(rows_out):
    """
    Shared post-processing for all format parsers:
      1. Normalise programme codes via _sis_norm_prog and filter by VALID_PROGS.
      2. Deduplicate by (prog, year_level, subj_code) while detecting numeric
         field conflicts across sections (section_count, conflict_notes).
      3. Sort subjects by code within each (prog, year_level) group, preserving
         the discovery order of those groups from the source file.
    """
    from collections import OrderedDict as _OD

    # Normalise + whitelist filter
    filtered = []
    for r in rows_out:
        if not (r.get('subj_code') or '').strip(): continue
        prog = _sis_norm_prog(r.get('program', '') or '')
        if not prog: continue
        r = dict(r); r['program'] = prog
        filtered.append(r)

    # Group and deduplicate
    groups = _OD()
    for r in filtered:
        key = (r['program'], r.get('year_level', 1), r['subj_code'].upper())
        groups.setdefault(key, []).append(r)

    deduped = []
    for group in groups.values():
        canonical = dict(group[0])
        canonical['section_count'] = len(group)
        conflicts = []
        for fld, lbl in (('lec_hrs', 'Lec Hrs'), ('lab_hrs', 'Lab Hrs'), ('credit_units', 'Units')):
            int_vals = set()
            for r in group:
                v = str(r.get(fld, '') or '')
                digits = ''.join(c for c in v if c.isdigit())
                int_vals.add(int(digits) if digits else 0)
            if len(int_vals) > 1:
                conflicts.append(f'{lbl}: {"/".join(str(v) for v in sorted(int_vals))}')
        canonical['conflict_notes'] = '; '.join(conflicts)
        deduped.append(canonical)

    # Sort subjects by code within each (prog, year_level) group
    pg_index, pg_buckets = {}, []
    for r in deduped:
        k = (r['program'], r.get('year_level', 1))
        if k not in pg_index:
            pg_index[k] = len(pg_buckets)
            pg_buckets.append([])
        pg_buckets[pg_index[k]].append(r)

    final = []
    for bucket in pg_buckets:
        bucket.sort(key=lambda r: r['subj_code'].upper())
        final.extend(bucket)
    return final


def _parse_schedule_csv(file_bytes):
    """Parse a flat CSV schedule file. Returns list of row dicts."""
    import csv as _csv, re

    def _si(v):
        if not v: return 0
        try:
            n = ''.join(filter(str.isdigit, str(v))); return int(n) if n else 0
        except: return 0

    rows_out = []
    # Decode with utf-8-sig so a BOM written by our own CSV export is stripped.
    # Falls back gracefully to plain UTF-8 when no BOM is present.
    text   = file_bytes.decode('utf-8-sig', errors='replace')
    reader = _csv.DictReader(io.StringIO(text, newline=None))

    for row in reader:
        # Normalise every key: strip whitespace + BOM remnants, fold to lower-case
        # so the lookup below is case-insensitive regardless of the source tool.
        rc = {k.strip().lstrip('﻿').lower(): (v or '').strip()
              for k, v in row.items() if k}

        def _g(*names):
            """Case-insensitive column lookup — returns first non-empty match."""
            for n in names:
                v = rc.get(n.lower(), '')
                if v: return v
            return ''

        inst    = _g('Instructor')
        s_code  = _g('SubjectCode', 'Subject Code', 'SubjectCode', 'subjectcode',
                      'subject_code', 'Code', 'code')
        subj_nm = _g('SubjectName', 'Subject Name', 'subjectname',
                     'subject_name', 'Description', 'description')
        prog_raw= _g('Program', 'program', 'Programme', 'programme',
                     'ProgramCode', 'programcode')
        prog    = re.sub(r'\s+\d+$', '', prog_raw).strip()
        yl_raw  = _g('YearLevel', 'Year Level', 'yearlevel', 'year_level',
                     'YrLevel', 'yrlevel')
        _yl     = _si(yl_raw) or 1
        yl      = max(1, min(_yl, 5)) if _yl <= 32767 else 1

        if not inst and not s_code:
            continue

        rows_out.append({
            'instructor':   inst,
            'subj_code':    s_code,
            'subj_name':    subj_nm,
            'lec_hrs':      _si(_g('LectureHours', 'Lecture Hours', 'lecturehours',
                                    'lecture_hours', 'Lec', 'lec')),
            'lab_hrs':      _si(_g('LaboratoryHours', 'Laboratory Hours', 'laboratoryhours',
                                    'laboratory_hours', 'Lab', 'lab')),
            'credit_units': _si(_g('CreditUnits', 'Credit Units', 'creditunits',
                                    'credit_units', 'Units', 'units')),
            'hours':        _si(_g('Hours', 'hours')),
            'days':         _g('Day/s', 'Days', 'day/s', 'days', 'Day', 'day'),
            'time':         _g('Time', 'time'),
            'room':         _g('Room', 'room'),
            'course':       _g('Section', 'section', 'Course', 'course'),
            'program':      prog,
            'year_level':   yl,
        })
    return _sis_post_process(rows_out)


def _parse_schedule_docx(file_bytes):
    """Parse a schedule Word document (flat or hierarchical tables)."""
    from docx import Document
    from docx.oxml.ns import qn
    import re

    YEAR_MAP = {'FIRST': 1, 'SECOND': 2, 'THIRD': 3, 'FOURTH': 4, 'FIFTH': 5}
    TOTAL_KW = ('TOTAL', 'SUB-TOTAL', 'SUBTOTAL', 'GRAND TOTAL')
    HDR_KW   = ('INSTRUCTOR', 'SUBJ', 'DESCRIPTION', 'LEC', 'LAB', 'CREDIT', 'HOURS', 'DAY', 'TIME', 'ROOM')
    KW_FIELDS = [
        ('INSTRUCTOR','instructor'), ('SUBJ','subj_code'), ('DESCRIPTION','subj_name'),
        ('LEC','lec_hrs'), ('LAB','lab_hrs'), ('CREDIT','credit_units'),
        ('COURSE','course'), ('HOURS','hours'), ('DAY','days'), ('TIME','time'), ('ROOM','room'),
    ]

    def _cell_txt(cell):
        return ' '.join(p.text.strip() for p in cell.paragraphs if p.text.strip())

    def _build_col_map(vals):
        cm, used = {}, set()
        uv = [v.upper() for v in vals]
        for i, v in enumerate(uv):
            for kw, fld in KW_FIELDS:
                if kw in v and fld not in used:
                    cm[fld] = i; used.add(fld); break
        return cm

    doc = Document(io.BytesIO(file_bytes))
    rows_out = []
    current_prog = ''
    current_yl   = 1
    col_map      = {}

    for block in doc.element.body:
        tag = block.tag.split('}')[-1] if '}' in block.tag else block.tag

        if tag == 'p':
            from docx.text.paragraph import Paragraph
            para = Paragraph(block, doc)
            txt  = para.text.strip()
            upr  = txt.upper()
            if not txt: continue
            # Year level
            yl_found = False
            for w, n in YEAR_MAP.items():
                if w + ' YEAR' in upr: current_yl = n; yl_found = True; break
            if yl_found: continue
            # Program header (bold short text)
            is_bold = any(r.bold for r in para.runs if r.text.strip())
            if is_bold and len(txt) < 80 and not any(kw in upr for kw in TOTAL_KW):
                prog = re.sub(r'\s+\d+$', '', txt).strip()
                if prog and not any(w + ' YEAR' in upr for w in YEAR_MAP):
                    current_prog = prog; col_map = {}

        elif tag == 'tbl':
            from docx.table import Table
            tbl = Table(block, doc)
            if not tbl.rows: continue

            first_vals = [_cell_txt(c) for c in tbl.rows[0].cells]
            uv0 = [v.upper() for v in first_vals]
            if sum(1 for kw in HDR_KW if any(kw in v for v in uv0)) >= 4:
                col_map = _build_col_map(first_vals)
                start = 1
            else:
                start = 0

            for row in tbl.rows[start:]:
                vals = [_cell_txt(c) for c in row.cells]
                full_u = ' '.join(vals).upper()
                if not any(vals): continue
                if any(kw in full_u for kw in TOTAL_KW): continue
                # Year level
                yl_found = False
                for w, n in YEAR_MAP.items():
                    if w + ' YEAR' in full_u: current_yl = n; yl_found = True; break
                if yl_found: continue
                # Inner header row
                uv = [v.upper() for v in vals]
                if sum(1 for kw in HDR_KW if any(kw in v for v in uv)) >= 4:
                    col_map = _build_col_map(vals); continue
                # Program header (all-same merged row or bold)
                if len(set(v for v in vals if v)) == 1:
                    prog = re.sub(r'\s+\d+$', '', vals[0]).strip()
                    if prog and not any(w + ' YEAR' in prog.upper() for w in YEAR_MAP):
                        current_prog = prog; col_map = {}; continue

                def gc(fld, di):
                    idx = col_map.get(fld, di)
                    return vals[idx] if idx < len(vals) else ''

                inst     = gc('instructor', 0)
                s_code   = gc('subj_code',  1)
                subj_nm  = gc('subj_name',  2)
                lec_raw  = gc('lec_hrs',    3)
                lab_raw  = gc('lab_hrs',    4)
                unit_raw = gc('credit_units', 5)
                course   = gc('course',     6)
                hrs_raw  = gc('hours',      7)
                days_raw = gc('days',       8)
                time_raw = gc('time',       9)
                room_raw = gc('room',       10)
                if not s_code and not inst: continue
                prog = current_prog or (re.sub(r'\s+\d+$', '', course).strip() if course else '')
                rows_out.append({
                    'instructor': inst, 'subj_code': s_code, 'subj_name': subj_nm,
                    'lec_hrs': lec_raw, 'lab_hrs': lab_raw, 'credit_units': unit_raw,
                    'hours': hrs_raw, 'days': days_raw, 'time': time_raw,
                    'room': room_raw, 'course': course,
                    'program': prog, 'year_level': current_yl,
                })
    return _sis_post_process(rows_out)


def _parse_schedule_pdf(file_bytes):
    """Parse a schedule PDF using pdfplumber."""
    import pdfplumber, re

    YEAR_MAP = {'FIRST': 1, 'SECOND': 2, 'THIRD': 3, 'FOURTH': 4, 'FIFTH': 5}
    TOTAL_KW = ('TOTAL', 'SUB-TOTAL', 'SUBTOTAL', 'GRAND TOTAL')
    HDR_KW   = ('INSTRUCTOR', 'SUBJ', 'DESCRIPTION', 'LEC', 'LAB', 'CREDIT', 'HOURS', 'DAY', 'TIME', 'ROOM')
    KW_FIELDS = [
        ('INSTRUCTOR','instructor'), ('SUBJ','subj_code'), ('DESCRIPTION','subj_name'),
        ('LEC','lec_hrs'), ('LAB','lab_hrs'), ('CREDIT','credit_units'),
        ('COURSE','course'), ('HOURS','hours'), ('DAY','days'), ('TIME','time'), ('ROOM','room'),
    ]

    def _build_col_map(vals):
        cm, used = {}, set()
        uv = [str(v or '').upper() for v in vals]
        for i, v in enumerate(uv):
            for kw, fld in KW_FIELDS:
                if kw in v and fld not in used:
                    cm[fld] = i; used.add(fld); break
        return cm

    rows_out = []
    current_prog = ''
    current_yl   = 1
    col_map      = {}

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            # Check page text for year-level headers between tables
            page_text = page.extract_text() or ''
            for line in page_text.split('\n'):
                lu = line.strip().upper()
                for w, n in YEAR_MAP.items():
                    if w + ' YEAR' in lu: current_yl = n; break

            tables = page.extract_tables({'vertical_strategy': 'lines', 'horizontal_strategy': 'lines'})
            if not tables:
                tables = page.extract_tables({'vertical_strategy': 'text', 'horizontal_strategy': 'text'})
            if not tables:
                continue

            for table in tables:
                if not table: continue
                tbl_col_map = dict(col_map)

                for row in table:
                    if not row: continue
                    vals = [str(c).strip() if c is not None else '' for c in row]
                    vals = [v for v in vals if v.lower() not in ('none',)]
                    # Pad to at least 11 cols
                    while len(vals) < 11: vals.append('')
                    non_empty = [v for v in vals if v]
                    if not non_empty: continue
                    full_u = ' '.join(non_empty).upper()

                    # Year level
                    yl_found = False
                    for w, n in YEAR_MAP.items():
                        if w + ' YEAR' in full_u: current_yl = n; yl_found = True; break
                    if yl_found: continue

                    # Skip totals
                    if any(kw in full_u for kw in TOTAL_KW): continue

                    # Column header
                    uv = [v.upper() for v in vals]
                    if sum(1 for kw in HDR_KW if any(kw in v for v in uv)) >= 3:
                        tbl_col_map = _build_col_map(vals)
                        col_map = tbl_col_map
                        continue

                    # ── Programme header detection ────────────────────────────
                    # Two cases:
                    #  (a) Very few non-empty cells  → likely a merged-cell banner
                    #  (b) Row starts with BACHELOR/DIPLOMA + parenthesised code
                    #      regardless of cell count  → catches long titles that
                    #      pdfplumber splits across several cells (len > 80 chars)
                    v0   = vals[0].strip() if vals else ''
                    v0u  = v0.upper()
                    is_few   = len(non_empty) <= 3 and v0
                    is_hdr   = bool(v0 and
                                    (v0u.startswith('BACHELOR') or v0u.startswith('DIPLOMA')) and
                                    re.search(r'\([A-Z][A-Z0-9\-/]{1,20}\)', v0u))
                    if (is_few or is_hdr) and \
                            not any(w + ' YEAR' in full_u for w in YEAR_MAP) and \
                            not any(kw in full_u for kw in HDR_KW):
                        prog_cand = re.sub(r'\s*\d+$', '', v0).strip()
                        if prog_cand and len(prog_cand) < 200:
                            current_prog = prog_cand; tbl_col_map = {}; continue

                    def gc(fld, di):
                        idx = tbl_col_map.get(fld, di)
                        return vals[idx] if idx < len(vals) else ''

                    inst     = gc('instructor', 0)
                    s_code   = gc('subj_code',  1)
                    subj_nm  = gc('subj_name',  2)
                    lec_raw  = gc('lec_hrs',    3)
                    lab_raw  = gc('lab_hrs',    4)
                    unit_raw = gc('credit_units', 5)
                    course   = gc('course',     6)
                    hrs_raw  = gc('hours',      7)
                    days_raw = gc('days',       8)
                    time_raw = gc('time',       9)
                    room_raw = gc('room',       10)
                    if not s_code and not inst: continue

                    # Derive prog: strip trailing digits (with or without space)
                    # so "BSEE 1", "BSEE1", "BPAPFM4" all resolve correctly.
                    course_raw  = re.sub(r'\s*\d+$', '', course).strip() if course else ''
                    course_prog = _sis_norm_prog(course_raw) if course_raw else ''
                    if current_prog:
                        # Switch to course-based programme when header was missed and
                        # the course column clearly indicates a different valid programme.
                        if course_prog and course_prog != _sis_norm_prog(current_prog):
                            current_prog = course_prog
                        prog = current_prog
                    else:
                        prog = course_prog
                    rows_out.append({
                        'instructor': inst, 'subj_code': s_code, 'subj_name': subj_nm,
                        'lec_hrs': lec_raw, 'lab_hrs': lab_raw, 'credit_units': unit_raw,
                        'hours': hrs_raw, 'days': days_raw, 'time': time_raw,
                        'room': room_raw, 'course': course,
                        'program': prog, 'year_level': current_yl,
                    })
    return _sis_post_process(rows_out)


def _validate_schedule_rows(raw_rows, cur, config=None):
    """Run FK validation on parsed rows and return preview-ready list.

    config (dict, optional) — when provided, enables configured-import mode:
        prog_map     : {detected_prog: db_prog}  program remapping
        default_prog : fallback program when row has none
        default_yls  : list of fallback year levels applied when row year level is 0/missing
                       (rows are duplicated for each selected year level)
        Relaxed blocking: only subject-code-missing or program-missing-after-mapping
        blocks a row.  Section / curriculum mismatches become warnings instead.
    """
    config       = config or {}
    prog_map     = config.get('prog_map', {})
    default_prog = (config.get('default_prog') or '').strip()
    relaxed      = bool(config)

    # Resolve default year levels (may be a list or a single int)
    raw_yls = config.get('default_yls') or []
    if isinstance(raw_yls, int):
        raw_yls = [raw_yls]
    default_yls = [int(y) for y in raw_yls if str(y).isdigit() and 1 <= int(y) <= 5]
    if not default_yls:
        legacy = int(config.get('default_yl') or 0)
        default_yls = [legacy] if legacy > 0 else [1]

    def _si(v):
        if not v: return 0
        try:
            n = ''.join(filter(str.isdigit, str(v))); return int(n) if n else 0
        except: return 0

    def _nr(raw):
        r = str(raw).strip()
        if not r: return 'TBA'
        u = r.upper()
        if u in ('G', 'GYM', 'PUP GYM'): return 'PUP GYM'
        if u in ('Q', 'QUAD', 'LQ-QUAD'): return 'LQ-Quad'
        if u.startswith('LQ-'): return r
        return f'LQ-{r}'

    # Expand rows that have no year level into one row per default year level
    expanded = []
    for item in raw_rows:
        yl = item.get('year_level') or 0
        if yl:
            expanded.append(item)
        else:
            for dyl in default_yls:
                expanded.append({**item, 'year_level': dyl})
    if not expanded:
        expanded = raw_rows

    out = []
    for item in expanded:
        inst     = item.get('instructor', '')
        s_code   = (item.get('subj_code') or '').strip()
        raw_prog = (item.get('program') or '').strip()
        yl       = item.get('year_level') or default_yls[0]
        room_raw = item.get('room', '')
        flags, status = [], 'ready'

        # Apply user program mapping — prefer raw_program (original file code) as key
        raw_prog_file = (item.get('raw_program') or raw_prog).strip()
        prog = (prog_map.get(raw_prog_file) or prog_map.get(raw_prog) or
                raw_prog_file or raw_prog) or default_prog
        prog = prog.strip()

        # CRITICAL: missing subject code → always blocked
        if not s_code:
            out.append({**item, 'lec_hrs': 0, 'lab_hrs': 0, 'credit_units': 0,
                        'hours': 0, 'status': 'blocked',
                        'flags': 'Missing subject code',
                        'room_display': room_raw or 'TBA',
                        'section_name': item.get('course', ''),
                        'section_count': item.get('section_count', 1),
                        'program': prog})
            continue

        # CRITICAL: program still unknown after mapping → blocked
        if not prog:
            status = 'blocked'
            flags.append('Program not assigned — use the Configure step to assign a program')

        # Subject in curriculum (warning only — never blocks in relaxed mode)
        cs_id = None
        if s_code:
            if prog:
                cur.execute("""
                    SELECT cs.curriculumsubjectid FROM curriculumsubject cs
                    JOIN curriculum c ON cs.curriculumid=c.curriculumid
                    JOIN academic_offering ao ON c.academicofferingid=ao.academicofferingid
                    WHERE UPPER(ao.offeringcode)=UPPER(%s) AND UPPER(cs.subjectcode)=UPPER(%s) LIMIT 1
                """, (prog, s_code))
                r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None
            if not cs_id:
                cur.execute("SELECT curriculumsubjectid FROM curriculumsubject WHERE UPPER(subjectcode)=UPPER(%s) LIMIT 1", (s_code,))
                r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None
        if not cs_id:
            if status != 'blocked': status = 'warning'
            flags.append(f'Subject "{s_code}" not in curriculum → will save to historical data')

        # Instructor (warning only)
        emp_num = None
        if inst:
            parts = inst.split(',', 1)
            ln = parts[0].strip()
            fn = parts[1].strip().split()[0] if len(parts) > 1 and parts[1].strip() else ''
            if fn:
                cur.execute("SELECT employeenumber FROM faculty WHERE UPPER(lastname)=UPPER(%s) AND UPPER(firstname) LIKE UPPER(%s)||'%%' LIMIT 1", (ln, fn))
                r = cur.fetchone(); emp_num = r['employeenumber'] if r else None
            if not emp_num:
                cur.execute("SELECT employeenumber FROM faculty WHERE UPPER(lastname)=UPPER(%s) LIMIT 1", (ln,))
                r = cur.fetchone(); emp_num = r['employeenumber'] if r else None
        if not emp_num:
            if status != 'blocked': status = 'warning'
            flags.append('Instructor not matched → will be saved as TBA')

        # Section — blocks in legacy mode, warning-only in relaxed/configured mode
        sec_id, sec_name = None, ''
        if prog and yl:
            cur.execute("""
                SELECT sec.sectionid, sec.sectionname FROM sections sec
                JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
                JOIN academic_offering ao ON pyl.academicofferingid=ao.academicofferingid
                WHERE UPPER(ao.offeringcode)=UPPER(%s) AND pyl.yearlevel=%s AND sec.isactive=TRUE
                ORDER BY sec.sectionname LIMIT 1
            """, (prog, yl))
            r = cur.fetchone()
            sec_id   = r['sectionid']   if r else None
            sec_name = r['sectionname'] if r else ''
            if not sec_id:
                cur.execute("""
                    SELECT sec.sectionid, sec.sectionname FROM sections sec
                    JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
                    JOIN academic_offering ao ON pyl.academicofferingid=ao.academicofferingid
                    WHERE UPPER(ao.offeringcode)=UPPER(%s) AND pyl.yearlevel=%s
                    ORDER BY sec.sectionname LIMIT 1
                """, (prog, yl))
                r = cur.fetchone()
                sec_id   = r['sectionid']   if r else None
                sec_name = r['sectionname'] if r else ''
        if not sec_id:
            if relaxed:
                if status != 'blocked': status = 'warning'
                flags.append(f'No section for "{prog or "—"}" Yr {yl} → will save to historical data')
            else:
                status = 'blocked'
                flags.append(f'No section for "{prog or "—"}" Yr {yl}')

        # Room (warning only)
        room_display = room_raw or 'TBA'
        if room_raw and room_raw.upper() not in ('TBA', ''):
            norm_r = _nr(room_raw)
            cur.execute("SELECT roomid FROM room WHERE UPPER(roomname)=UPPER(%s)", (norm_r,))
            _rx = cur.fetchone()
            if not _rx and norm_r.upper().startswith('LQ-'):
                cur.execute("SELECT roomid FROM room WHERE UPPER(roomname)=UPPER(%s)", (norm_r[3:],))
                _rx = cur.fetchone()
            if not _rx:
                cur.execute("SELECT roomid FROM room WHERE UPPER(roomname) LIKE '%%'||UPPER(%s)||'%%' LIMIT 1", (room_raw.strip(),))
                _rx = cur.fetchone()
            if not _rx:
                if status != 'blocked': status = 'warning'
                flags.append(f'Room "{room_raw}" not found → TBA')
                room_display = f'{room_raw} → TBA'

        # Propagate section-merge metadata from the parser
        section_count  = item.get('section_count', 1)
        conflict_notes = item.get('conflict_notes', '')
        if conflict_notes:
            if status == 'ready': status = 'warning'
            flags.append(f'Data conflict across sections — {conflict_notes}')

        out.append({
            **item,
            'program':       prog,
            'year_level':    yl,
            'lec_hrs':       _si(item.get('lec_hrs')),
            'lab_hrs':       _si(item.get('lab_hrs')),
            'credit_units':  _si(item.get('credit_units')),
            'hours':         _si(item.get('hours')),
            'status':        status,
            'flags':         '; '.join(flags),
            'room_display':  room_display,
            'section_name':  sec_name or item.get('course', ''),
            'section_count': section_count,
        })

    # Secondary dedup: collapse any subjects that share (program, year_level,
    # year_label, subj_code) — catches duplicates that arise from year_level=0
    # expansion or whitespace differences that survived the parse-time pass.
    _seen2 = {}
    deduped_out = []
    for row in out:
        lbl = ' '.join((row.get('year_label') or '').upper().split())
        ck  = ' '.join((row.get('subj_code') or '').upper().split())
        k   = (row.get('program', ''), row.get('year_level', 0), lbl, ck)
        if k not in _seen2:
            _seen2[k] = True
            deduped_out.append(row)
    return deduped_out


@app.route('/academic/schedule/import/check-existing', methods=['POST'])
def schedule_import_check_existing():
    """Return whether schedule data already exists for the given AY + semester.
    Checks both the normalized schedule table AND historical_data so that
    re-imports are correctly blocked regardless of how the previous import stored records.
    """
    if session.get('role') != 'Academic Head':
        return jsonify({'error': 'Unauthorized'}), 403
    data     = request.get_json() or {}
    ay_id    = data.get('ay_id')
    sem_type = data.get('semester_type')
    if not ay_id or not sem_type:
        return jsonify({'exists': False, 'count': 0})
    conn = get_db_connection(); cur = conn.cursor()
    try:
        # Check normalized schedule table
        cur.execute("""
            SELECT COUNT(*) FROM schedule s
            JOIN semester sem ON s.semesterid = sem.semesterid
            WHERE sem.academicyearid = %s AND sem.semestertype = %s
        """, (ay_id, sem_type))
        sched_count = cur.fetchone()[0]

        # Check historical_data (populated when FK resolution fails or semester has ended)
        cur.execute("""
            SELECT COUNT(*) FROM historical_data hd
            JOIN semester sem ON hd.semesterid = sem.semesterid
            WHERE sem.academicyearid = %s AND sem.semestertype = %s
        """, (ay_id, sem_type))
        hist_count = cur.fetchone()[0]

        count = sched_count + hist_count
        return jsonify({'exists': count > 0, 'count': count,
                        'sched_count': sched_count, 'hist_count': hist_count})
    except Exception as e:
        return jsonify({'exists': False, 'count': 0, 'error': str(e)})
    finally:
        cur.close(); conn.close()


@app.route('/academic/schedule/import/unified/analyze', methods=['POST'])
def schedule_unified_analyze():
    """Step 1 of wizard: parse the file, return raw rows + detected programs.
    No DB validation is performed here — the configure screen uses this data."""
    if session.get('role') != 'Academic Head':
        return jsonify({'error': 'Unauthorized'}), 403

    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400

    fname      = (file.filename or '').lower()
    file_bytes = file.stream.read()
    fmt        = None

    try:
        if fname.endswith('.csv'):
            raw_rows = _parse_schedule_csv(file_bytes);         fmt = 'CSV'
        elif fname.endswith(('.xlsx', '.xls')):
            raw_rows = _parse_sis_excel(io.BytesIO(file_bytes)); fmt = 'XLSX'
        elif fname.endswith('.docx'):
            raw_rows = _parse_schedule_docx(file_bytes);        fmt = 'DOCX'
        elif fname.endswith('.pdf'):
            raw_rows = _parse_schedule_pdf(file_bytes);         fmt = 'PDF'
        else:
            ext = fname.rsplit('.', 1)[-1].upper() if '.' in fname else 'unknown'
            return jsonify({'error': f'Unsupported format: .{ext}'}), 400
    except Exception as e:
        return jsonify({'error': f'Parse error ({fmt or "unknown"}): {e}'}), 400

    if not raw_rows:
        return jsonify({'error': 'No schedule data found in the file.'}), 400

    # Collect unique program codes as they appear in the file.
    # Use raw_program (original file code) when available so the configure screen
    # shows e.g. "BSOA-LOA" instead of the normalized "BSOA".
    # Deduplicate: if both "BSOA-LOA" and "BSOA" resolve to the same canonical code,
    # prefer the more specific (longer / original) one.
    _canon_to_raw = {}
    for r in raw_rows:
        raw = (r.get('raw_program') or r.get('program') or '').strip()
        canon = (r.get('program') or '').strip()
        if not raw or not canon: continue
        existing = _canon_to_raw.get(canon, '')
        # Keep the entry whose raw_program differs most from the canonical code
        # (i.e. the original file variant is more informative than the normalized one)
        if not existing or (raw != canon and existing == canon):
            _canon_to_raw[canon] = raw
    detected_progs = sorted(_canon_to_raw.values())
    has_program_col = bool(detected_progs)

    # Fetch DB programs for the mapping dropdowns
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT programcode, programname FROM programs WHERE isactive=TRUE ORDER BY programname")
        db_programs = [dict(r) for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()

    # Build db_map with multiple lookup keys per DB program:
    #   "BSBA-FM" (exact), "BSBAFM" (stripped), "BSBAFM" (via _sis_norm_prog)
    # This lets raw file codes like "BSBA-FM", "BSBA FM", "DCPET" auto-match their DB entries.
    import re as _re_map
    db_map = {}
    for r in db_programs:
        code = r['programcode']
        db_map[code.upper()] = code                          # exact
        stripped = _re_map.sub(r'[\s\-]', '', code).upper()  # strip hyphens/spaces
        if stripped not in db_map:
            db_map[stripped] = code
        canonical = _sis_norm_prog(code)                     # normalised form
        if canonical and canonical.upper() not in db_map:
            db_map[canonical.upper()] = code

    auto_map = {}
    for dp in detected_progs:
        # 1. Direct lookup (exact, stripped, normalised)
        match = (db_map.get(dp.upper()) or
                 db_map.get(_re_map.sub(r'[\s\-]', '', dp).upper()))
        if match:
            auto_map[dp] = match
            continue
        # 2. Normalise the raw file code and try again
        canonical = _sis_norm_prog(dp)
        if canonical:
            match = (db_map.get(canonical.upper()) or
                     db_map.get(_re_map.sub(r'[\s\-]', '', canonical).upper()))
            if match:
                auto_map[dp] = match

    return jsonify({
        'raw_rows':          raw_rows,
        'detected_programs': detected_progs,
        'auto_map':          auto_map,
        'db_programs':       db_programs,
        'has_program_col':   has_program_col,
        'total':             len(raw_rows),
        'format':            fmt,
    })


@app.route('/academic/schedule/import/unified/validate', methods=['POST'])
def schedule_unified_validate():
    """Step 2 of wizard: apply user config (program mapping, AY, semester) and
    validate rows against the DB with relaxed blocking rules."""
    if session.get('role') != 'Academic Head':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data received'}), 400

    raw_rows = data.get('raw_rows', [])
    if not raw_rows:
        return jsonify({'error': 'No rows to validate'}), 400

    config = {
        'prog_map':     data.get('prog_map', {}),
        'default_prog': data.get('default_prog', ''),
        'default_yls':  data.get('default_yls', []),
        'default_yl':   data.get('default_yl', 1),   # legacy fallback
    }

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        preview_rows = _validate_schedule_rows(raw_rows, cur, config=config)
    finally:
        cur.close(); conn.close()

    progs = set(r.get('program') or '(Unknown)' for r in preview_rows)
    subjs = set(r.get('subj_code', '') for r in preview_rows if r.get('subj_code'))
    stats = {
        'ready':    sum(1 for r in preview_rows if r['status'] == 'ready'),
        'warning':  sum(1 for r in preview_rows if r['status'] == 'warning'),
        'blocked':  sum(1 for r in preview_rows if r['status'] == 'blocked'),
        'total':    len(preview_rows),
        'programs': len(progs),
        'subjects': len(subjs),
    }
    return jsonify({
        'rows':          preview_rows,
        'stats':         stats,
        'format':        data.get('format', ''),
        'ay_id':         data.get('ay_id', ''),
        'semester_type': data.get('sem_type', ''),
    })


@app.route('/academic/schedule/import/unified/preview', methods=['POST'])
def schedule_unified_preview():
    if session.get('role') != 'Academic Head':
        return jsonify({'error': 'Unauthorized'}), 403

    file     = request.files.get('file')
    ay_id    = request.form.get('ay_id')
    sem_type = request.form.get('semester_type')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400

    fname      = (file.filename or '').lower()
    file_bytes = file.stream.read()
    fmt        = None

    try:
        if fname.endswith('.csv'):
            raw_rows = _parse_schedule_csv(file_bytes); fmt = 'CSV'
        elif fname.endswith(('.xlsx', '.xls')):
            raw_rows = _parse_sis_excel(io.BytesIO(file_bytes)); fmt = 'XLSX'
        elif fname.endswith('.docx'):
            raw_rows = _parse_schedule_docx(file_bytes); fmt = 'DOCX'
        elif fname.endswith('.pdf'):
            raw_rows = _parse_schedule_pdf(file_bytes); fmt = 'PDF'
        else:
            ext = fname.rsplit('.', 1)[-1].upper() if '.' in fname else 'unknown'
            return jsonify({'error': f'Unsupported format: .{ext}. Use CSV, XLSX, DOCX, or PDF.'}), 400
    except Exception as e:
        return jsonify({'error': f'Parse error ({fmt or "unknown"}): {e}'}), 400

    if not raw_rows:
        return jsonify({'error': 'No schedule data found. Check that the file is a valid schedule document.'}), 400

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        preview_rows = _validate_schedule_rows(raw_rows, cur)
    finally:
        cur.close(); conn.close()

    progs = set(r.get('program') or '(Unknown)' for r in preview_rows)
    subjs = set(r.get('subj_code', '') for r in preview_rows if r.get('subj_code'))
    stats = {
        'ready':    sum(1 for r in preview_rows if r['status'] == 'ready'),
        'warning':  sum(1 for r in preview_rows if r['status'] == 'warning'),
        'blocked':  sum(1 for r in preview_rows if r['status'] == 'blocked'),
        'total':    len(preview_rows),
        'programs': len(progs),
        'subjects': len(subjs),
    }
    return jsonify({'rows': preview_rows, 'stats': stats, 'format': fmt, 'ay_id': ay_id, 'semester_type': sem_type})


@app.route('/schedule/generation')
def schedule_generation():
    if 'loggedin' not in session: return redirect(url_for('login'))
    return render_template('academic/scheduleGeneration.html')

@app.route('/schedule/manual-editor')
def manual_schedule_editor():
    if 'loggedin' not in session: return redirect(url_for('login'))

    scheduler_mode = request.args.get('scheduler', 'official')   # 'official' | 'local'
    if scheduler_mode not in ('official', 'local'):
        scheduler_mode = 'official'
    is_acad_head = session.get('role') == 'Academic Head'

    init_mode = request.args.get('mode', 'subject')
    init_prog = request.args.get('prog', '')
    init_yl   = request.args.get('yl', '')
    init_ay   = request.args.get('ay', '')
    init_sem  = request.args.get('sem', '')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        today = date.today()
        _sem_labels = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}

        if scheduler_mode == 'local':
            # Local Scheduler: only the currently active semester is a valid context.
            # Find the active semester first; fall back to the most-recent non-past one.
            cur.execute("""
                SELECT ay.AcademicYearID, s.SemesterType
                FROM Semester s
                JOIN AcademicYear ay ON s.AcademicYearID = ay.AcademicYearID
                WHERE s.IsActive = TRUE
                LIMIT 1
            """)
            row = cur.fetchone()
            if not row:
                # Fallback: most-recent semester that hasn't ended
                cur.execute("""
                    SELECT ay.AcademicYearID, s.SemesterType
                    FROM Semester s
                    JOIN AcademicYear ay ON s.AcademicYearID = ay.AcademicYearID
                    WHERE s.SemEndDate >= %s OR s.SemEndDate IS NULL
                    ORDER BY s.SemStartDate DESC
                    LIMIT 1
                """, (today,))
                row = cur.fetchone()
            if row:
                local_ay, local_sem = row[0], row[1]
                acad_years    = [local_ay]
                _ay_sem_map   = {local_ay: [{'type': local_sem, 'label': _sem_labels.get(local_sem, local_sem)}]}
                if not init_ay:  init_ay  = local_ay
                if not init_sem: init_sem = local_sem
            else:
                acad_years  = []
                _ay_sem_map = {}
            available_sems_json = json.dumps(_ay_sem_map)
        else:
            # Official Scheduler: show all non-past academic years and semesters.
            # NULL SemEndDate is treated as "not ended" so unfinished setup isn't silently hidden.
            cur.execute("""
                SELECT DISTINCT ay.AcademicYearID
                FROM AcademicYear ay
                JOIN Semester s ON s.AcademicYearID = ay.AcademicYearID
                WHERE s.SemEndDate >= %s OR s.SemEndDate IS NULL
                ORDER BY ay.AcademicYearID ASC
            """, (today,))
            acad_years = [row[0] for row in cur.fetchall()]

            cur.execute("""
                SELECT ay.AcademicYearID, s.SemesterType
                FROM AcademicYear ay
                JOIN Semester s ON s.AcademicYearID = ay.AcademicYearID
                WHERE s.SemEndDate >= %s OR s.SemEndDate IS NULL
                ORDER BY ay.AcademicYearID ASC, s.SemStartDate ASC
            """, (today,))
            _ay_sem_map = {}
            for row in cur.fetchall():
                ay_key, st = row[0], row[1]
                _ay_sem_map.setdefault(ay_key, []).append({'type': st, 'label': _sem_labels.get(st, st)})
            available_sems_json = json.dumps(_ay_sem_map)

        cur.execute("SELECT ProgramCode, ProgramName, COALESCE(NumYearLevel, 4) AS NumYearLevel FROM Programs WHERE IsActive = TRUE ORDER BY ProgramName")
        programs = cur.fetchall()
        cur.execute("""
            SELECT f.EmployeeNumber,
                   f.LastName || ', ' || f.FirstName || ' ' || COALESCE(f.MiddleName, '') AS fullname,
                   COALESCE(et.typename, 'Regular') AS typename
            FROM Faculty f
            LEFT JOIN employeetype et ON f.employeetypeid = et.employeetypeid
            WHERE f.EmployeeStatus != 'Archived'
            ORDER BY f.LastName
        """)
        faculty = cur.fetchall()
        cur.execute("SELECT BuildingID, BuildingName FROM Building WHERE IsActive = TRUE ORDER BY BuildingName")
        buildings = cur.fetchall()
        cur.execute("""
            SELECT r.RoomID, r.RoomName, r.BuildingID, b.BuildingName,
                   COALESCE(r.RoomType, 'Lecture') AS RoomType
            FROM Room r
            LEFT JOIN Building b ON r.BuildingID = b.BuildingID
            ORDER BY
                regexp_replace(r.RoomName, '[0-9]', '', 'g'),
                CASE WHEN regexp_replace(r.RoomName, '[^0-9]', '', 'g') = ''
                     THEN 0
                     ELSE CAST(regexp_replace(r.RoomName, '[^0-9]', '', 'g') AS BIGINT)
                END
        """)
        raw_rooms = cur.fetchall()

        rooms_list = [{"id": r[0], "name": r[1], "bldg_id": r[2], "bldg_name": r[3] or "Unknown",
                       "type": r[4],
                       "floor": "1" if "LQ1" in r[1].replace(" ","") else "2" if "LQ2" in r[1].replace(" ","") else "ALL"} for r in raw_rooms]
        
        faculty_json = json.dumps([{"id": f[0], "name": f[1], "typename": f[2]} for f in faculty])
        from database import load_scheduler_config as _load_sched_cfg
        _sched_cfg = _load_sched_cfg()
        lab_constraint_enabled  = bool(_sched_cfg.get('hc_lab_session_enabled', 1))
        weekend_enabled         = bool(_sched_cfg.get('hc_weekend_enabled', 1))
        weekend_day             = _sched_cfg.get('hc_weekend_day', 'sunday_only')
        weekend_subject         = _sched_cfg.get('hc_weekend_subject', 'nstp_only')
        spec_constraint_enabled = bool(_sched_cfg.get('hc_faculty_spec_enabled', 1))

        return render_template('academic/manualScheduleEditor.html',
                               scheduler_mode=scheduler_mode,
                               is_acad_head=is_acad_head,
                               acad_years=acad_years, programs=programs,
                               faculty=faculty, faculty_json=faculty_json,
                               buildings=buildings, rooms_json=json.dumps(rooms_list),
                               available_sems_json=available_sems_json,
                               init_mode=init_mode, init_prog=init_prog,
                               init_yl=init_yl, init_ay=init_ay, init_sem=init_sem,
                               lab_constraint_enabled=lab_constraint_enabled,
                               weekend_enabled=weekend_enabled,
                               weekend_day=weekend_day,
                               weekend_subject=weekend_subject,
                               spec_constraint_enabled=spec_constraint_enabled)
    finally:
        cur.close()
        conn.close()


@app.route('/schedule/local-editor')
def local_schedule_editor():
    """Backward-compat redirect — all scheduler modes now share one route."""
    return redirect(url_for('manual_schedule_editor', scheduler='local'))


# ── Local Scheduler pages ────────────────────────────────────────────────────

@app.route('/schedule/local-arrangements')
def schedule_local_arrangements():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT ay.academicyearid, ay.yearstart, ay.yearend,
                   ARRAY_AGG(s.semestertype ORDER BY s.semstartdate) AS sems
            FROM academicyear ay
            JOIN semester s ON s.academicyearid = ay.academicyearid
            GROUP BY ay.academicyearid, ay.yearstart, ay.yearend
            ORDER BY ay.yearstart DESC
        """)
        ay_rows = cur.fetchall()
        cur.execute("SELECT programcode, programname FROM programs WHERE isactive = TRUE ORDER BY programname")
        prog_rows = cur.fetchall()
    finally:
        cur.close(); conn.close()

    acad_years_json = json.dumps([{
        'id':    r['academicyearid'],
        'label': f"A.Y {r['yearstart']}–{r['yearend']}",
        'sems':  list(r['sems'] or []),
    } for r in (ay_rows or [])])
    programs_json = json.dumps([{
        'code': r['programcode'],
        'name': r['programname'],
    } for r in (prog_rows or [])])

    return render_template('academic/localArrangements.html',
                           acad_years_json=acad_years_json,
                           programs_json=programs_json)


# ── Local Scheduler API ──────────────────────────────────────────────────────

@app.route('/api/local/save_arrangement', methods=['POST'])
def api_save_local_arrangement():
    if 'loggedin' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    try:
        data       = request.json or {}
        ctx        = data.get('context', {})
        program    = ctx.get('program', '')
        year_level = int(ctx.get('yearLevel', 1))
        term       = ctx.get('term', '')
        ay         = ctx.get('acadYear', '')
        reason     = data.get('reason', '')
        sessions   = data.get('sessions', [])
        violations = data.get('violations', [])
        username   = session.get('username', 'unknown')

        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_local_tables(cur)

        sem_id = _get_semester_id(cur, ay, term)
        if not sem_id:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Semester not found'})

        # Find the current Published version for this section to use as reference
        cur.execute("""
            SELECT sv.versionid
            FROM schedule_version sv
            JOIN schedule s ON sv.scheduleid = s.scheduleid
            JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
            WHERE sv.status = 'Published'
              AND UPPER(ao.offeringcode) = UPPER(%s)
              AND cs.yearlevel = %s
              AND s.semesterid = %s
            LIMIT 1
        """, (program, year_level, sem_id))
        pub_row       = cur.fetchone()
        ref_versionid = pub_row['versionid'] if pub_row else None

        has_hc = len(violations) > 0

        # New saves always start as Draft; user explicitly publishes when ready.
        cur.execute("""
            INSERT INTO public.local_arrangement
                (description, programcode, yearlevel, semesterid, ref_versionid,
                 has_hc_violation, violated_rules, override_reason,
                 is_active, status, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, 'Draft', %s)
            RETURNING arrangementid
        """, (
            f"{program.upper()} YR{year_level} — Local Draft",
            program.upper(), year_level, sem_id, ref_versionid,
            has_hc, json.dumps(violations), reason, username
        ))
        arr_id = cur.fetchone()['arrangementid']

        for sess in sessions:
            s_code  = (sess.get('subject_code') or sess.get('subjectcode') or '').upper()
            day     = sess.get('day') or sess.get('daydesc') or ''
            st      = sess.get('start_time') or sess.get('starttimeid')
            et      = sess.get('end_time')   or sess.get('endtimeid')
            room_id = sess.get('room_id')    or sess.get('roomid')
            emp_num = sess.get('faculty_id') or sess.get('employeenumber')

            # Resolve time-string → timeid if necessary
            def _resolve_time(val):
                if val is None:
                    return None
                if isinstance(val, int):
                    return val
                # Try common formats: "08:30 AM", "08:30:00"
                for fmt in ("HH12:MI AM", "HH24:MI", "HH24:MI:SS"):
                    cur.execute(
                        "SELECT timeid FROM public.timeslot "
                        "WHERE TO_CHAR(timevalue, %s) = %s LIMIT 1",
                        (fmt, str(val).strip())
                    )
                    r = cur.fetchone()
                    if r:
                        return r['timeid']
                return None

            start_id = _resolve_time(st)
            end_id   = _resolve_time(et)

            cur.execute("""
                INSERT INTO public.local_arrangement_sessions
                    (arrangementid, subjectcode, daydesc, starttimeid, endtimeid,
                     roomid, faculty_employeenumber)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                arr_id, s_code or None, day or None,
                start_id, end_id,
                int(room_id) if room_id else None,
                str(emp_num) if emp_num else None
            ))

        if has_hc:
            cur.execute("""
                INSERT INTO public.schedule_exception_log
                    (source_type, arrangementid, violated_rules, approved_by, semesterid)
                VALUES ('local_override', %s, %s, %s, %s)
            """, (arr_id, json.dumps(violations), username, sem_id))

        # Mark any explicitly displaced subjects as unscheduled in Local Scheduler context.
        # These are Official-schedule subjects whose slot was taken over by this arrangement.
        for sc in data.get('displace_subjects', []):
            sc_upper = (sc or '').strip().upper()
            if not sc_upper:
                continue
            cur.execute("""
                INSERT INTO public.local_displaced_subjects
                    (programcode, yearlevel, semesterid, subjectcode, displaced_by, is_active)
                VALUES (UPPER(%s), %s, %s, %s, %s, TRUE)
                ON CONFLICT (programcode, yearlevel, semesterid, subjectcode)
                DO UPDATE SET is_active   = TRUE,
                              displaced_by = EXCLUDED.displaced_by,
                              displaced_at = NOW()
            """, (program, year_level, sem_id, sc_upper, arr_id))

        conn.commit()
        cur.close(); conn.close()
        return jsonify({'success': True, 'arrangementid': arr_id})

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/local/arrangements')
def api_get_local_arrangements():
    if 'loggedin' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    try:
        program    = request.args.get('program', '')
        year_level = request.args.get('year_level', '')
        ay         = request.args.get('ay_id', '')
        sem        = request.args.get('sem', '')

        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_local_tables(cur)

        status_filter = request.args.get('status', '')   # 'Draft' | 'Published' | 'Archived' | ''

        filters = ["la.is_active = TRUE"]
        params  = []

        if status_filter:
            filters.append("la.status = %s")
            params.append(status_filter)
        if program:
            filters.append("UPPER(la.programcode) = UPPER(%s)")
            params.append(program)
        if year_level:
            filters.append("la.yearlevel = %s")
            params.append(int(year_level))
        if ay and sem:
            filters.append("""la.semesterid = (
                SELECT semesterid FROM semester
                WHERE academicyearid = %s AND semestertype = %s LIMIT 1
            )""")
            params += [ay, sem]

        cur.execute(f"""
            SELECT la.*,
                   s.semestertype,
                   ay.academicyearid,
                   ay.yearstart,
                   ay.yearend,
                   (SELECT COUNT(*) FROM public.local_arrangement_sessions las
                    WHERE las.arrangementid = la.arrangementid) AS session_count
            FROM public.local_arrangement la
            LEFT JOIN semester s   ON la.semesterid    = s.semesterid
            LEFT JOIN academicyear ay ON s.academicyearid = ay.academicyearid
            WHERE {' AND '.join(filters)}
            ORDER BY la.created_at DESC
        """, params or None)

        rows   = cur.fetchall()
        cur.close(); conn.close()

        _sem_labels = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}
        result = []
        for r in (rows or []):
            result.append({
                'arrangementid':  r['arrangementid'],
                'description':    r['description'],
                'programcode':    r['programcode'],
                'yearlevel':      r['yearlevel'],
                'semesterid':     r['semesterid'],
                'sem_label':      _sem_labels.get(r['semestertype'], r['semestertype'] or '—'),
                'acadyear':       r['academicyearid'],
                'yearstart':      r['yearstart'],
                'yearend':        r['yearend'],
                'has_hc_violation': r['has_hc_violation'],
                'override_reason':  r['override_reason'],
                'session_count':    r['session_count'],
                'is_active':        r['is_active'],
                'status':           r.get('status', 'Draft'),
                'created_by':       r['created_by'],
                'created_at':       r['created_at'].isoformat() if r['created_at'] else None,
            })
        return jsonify({'success': True, 'arrangements': result})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/local/arrangement/<int:arr_id>')
def api_get_local_arrangement(arr_id):
    if 'loggedin' not in session:
        return jsonify({'success': False}), 401
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_local_tables(cur)

        cur.execute("""
            SELECT la.*,
                   s.semestertype,
                   ay.academicyearid, ay.yearstart, ay.yearend
            FROM public.local_arrangement la
            LEFT JOIN semester s   ON la.semesterid    = s.semesterid
            LEFT JOIN academicyear ay ON s.academicyearid = ay.academicyearid
            WHERE la.arrangementid = %s
        """, (arr_id,))
        arr = cur.fetchone()
        if not arr:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Not found'}), 404

        cur.execute("""
            SELECT las.*,
                   subj.subjectname,
                   TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_time,
                   TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_time,
                   r.roomname,
                   f.lastname || ', ' || f.firstname AS instructor
            FROM public.local_arrangement_sessions las
            LEFT JOIN subject  subj ON las.subjectcode            = subj.subjectcode
            LEFT JOIN timeslot ts_s ON las.starttimeid             = ts_s.timeid
            LEFT JOIN timeslot ts_e ON las.endtimeid               = ts_e.timeid
            LEFT JOIN room     r    ON las.roomid                  = r.roomid
            LEFT JOIN faculty  f    ON las.faculty_employeenumber  = f.employeenumber
            WHERE las.arrangementid = %s
            ORDER BY ts_s.timevalue, las.daydesc
        """, (arr_id,))
        sessions = cur.fetchall()
        cur.close(); conn.close()

        _sem_labels = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}
        sem_label   = _sem_labels.get(arr['semestertype'], arr['semestertype'] or '—')

        return jsonify({
            'success': True,
            'arrangement': {
                'arrangementid':    arr['arrangementid'],
                'description':      arr['description'],
                'programcode':      arr['programcode'],
                'yearlevel':        arr['yearlevel'],
                'sem_label':        sem_label,
                'acadyear':         arr['academicyearid'],
                'yearstart':        arr['yearstart'],
                'yearend':          arr['yearend'],
                'has_hc_violation': arr['has_hc_violation'],
                'override_reason':  arr['override_reason'],
                'is_active':        arr['is_active'],
                'status':           arr.get('status') or 'Draft',
                'created_by':       arr['created_by'],
                'created_at':       arr['created_at'].isoformat() if arr['created_at'] else None,
            },
            'sessions': [dict(s) for s in (sessions or [])],
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/local/arrangement/<int:arr_id>/deactivate', methods=['POST'])
def api_deactivate_local_arrangement(arr_id):
    if 'loggedin' not in session:
        return jsonify({'success': False}), 401
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_local_tables(cur)
        cur.execute(
            "UPDATE public.local_arrangement SET is_active = FALSE WHERE arrangementid = %s",
            (arr_id,)
        )
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/local/arrangement/<int:arr_id>/publish', methods=['POST'])
def api_publish_local_arrangement(arr_id):
    """Publish a Draft local arrangement.
    Rule: only one Published arrangement per (programcode, yearlevel, semesterid) at a time.
    The previous Published arrangement is archived automatically."""
    if 'loggedin' not in session:
        return jsonify({'success': False}), 401
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_local_tables(cur)

        # Load the target arrangement
        cur.execute("SELECT * FROM public.local_arrangement WHERE arrangementid = %s", (arr_id,))
        arr = cur.fetchone()
        if not arr:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Arrangement not found'}), 404
        if arr['status'] != 'Draft':
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Only Draft arrangements can be published'})

        # Archive the current Published arrangement for the same context (if any)
        cur.execute("""
            UPDATE public.local_arrangement
            SET    status = 'Archived'
            WHERE  programcode  = %s
              AND  yearlevel    = %s
              AND  semesterid   = %s
              AND  status       = 'Published'
              AND  is_active    = TRUE
        """, (arr['programcode'], arr['yearlevel'], arr['semesterid']))

        # Always reference the latest Published Official version at publish time
        cur.execute("""
            SELECT sv.versionid
            FROM   schedule_version sv
            JOIN   schedule s  ON sv.scheduleid = s.scheduleid
            JOIN   curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c ON cs.curriculumid = c.curriculumid
            JOIN   academic_offering ao ON c.academicofferingid = ao.academicofferingid
            WHERE  sv.status = 'Published'
              AND  UPPER(ao.offeringcode) = UPPER(%s)
              AND  cs.yearlevel = %s
              AND  s.semesterid = %s
            LIMIT 1
        """, (arr['programcode'], arr['yearlevel'], arr['semesterid']))
        pub_row       = cur.fetchone()
        ref_versionid = pub_row['versionid'] if pub_row else arr['ref_versionid']

        cur.execute("""
            UPDATE public.local_arrangement
            SET    status        = 'Published',
                   ref_versionid = %s,
                   description   = REPLACE(description, '— Local Draft', '— Local Published')
            WHERE  arrangementid = %s
        """, (ref_versionid, arr_id))

        conn.commit()
        cur.close(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/local/arrangement/<int:arr_id>/restore', methods=['POST'])
def api_restore_local_arrangement(arr_id):
    """Create a new Draft arrangement by copying sessions from a Published or Archived one.
    The restored Draft always references the current Published Official Schedule."""
    if 'loggedin' not in session:
        return jsonify({'success': False}), 401
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_local_tables(cur)
        username = session.get('username', 'unknown')

        cur.execute("SELECT * FROM public.local_arrangement WHERE arrangementid = %s", (arr_id,))
        src = cur.fetchone()
        if not src:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Source arrangement not found'}), 404

        # Fetch sessions from the source
        cur.execute("""
            SELECT subjectcode, daydesc, starttimeid, endtimeid, roomid, faculty_employeenumber
            FROM   public.local_arrangement_sessions
            WHERE  arrangementid = %s
        """, (arr_id,))
        src_sessions = cur.fetchall()

        # Reference the latest Published Official version
        cur.execute("""
            SELECT sv.versionid
            FROM   schedule_version sv
            JOIN   schedule s  ON sv.scheduleid = s.scheduleid
            JOIN   curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c ON cs.curriculumid = c.curriculumid
            JOIN   academic_offering ao ON c.academicofferingid = ao.academicofferingid
            WHERE  sv.status = 'Published'
              AND  UPPER(ao.offeringcode) = UPPER(%s)
              AND  cs.yearlevel = %s
              AND  s.semesterid = %s
            LIMIT 1
        """, (src['programcode'], src['yearlevel'], src['semesterid']))
        pub_row       = cur.fetchone()
        ref_versionid = pub_row['versionid'] if pub_row else src['ref_versionid']

        # Create a new Draft
        cur.execute("""
            INSERT INTO public.local_arrangement
                (description, programcode, yearlevel, semesterid, ref_versionid,
                 has_hc_violation, violated_rules, override_reason,
                 is_active, status, created_by)
            VALUES (%s, %s, %s, %s, %s, FALSE, '[]'::jsonb,
                    'Restored from arrangement #' || %s::text,
                    TRUE, 'Draft', %s)
            RETURNING arrangementid
        """, (
            f"{src['programcode']} YR{src['yearlevel']} — Local Draft (restored)",
            src['programcode'], src['yearlevel'], src['semesterid'], ref_versionid,
            arr_id, username
        ))
        new_id = cur.fetchone()['arrangementid']

        for s in (src_sessions or []):
            cur.execute("""
                INSERT INTO public.local_arrangement_sessions
                    (arrangementid, subjectcode, daydesc, starttimeid, endtimeid,
                     roomid, faculty_employeenumber)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (new_id, s['subjectcode'], s['daydesc'], s['starttimeid'],
                  s['endtimeid'], s['roomid'], s['faculty_employeenumber']))

        conn.commit()
        cur.close(); conn.close()
        return jsonify({'success': True, 'new_arrangementid': new_id})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/local/check_room_conflicts', methods=['POST'])
def api_local_check_room_conflicts():
    """Return Official Published sessions that would be displaced by a new Local Arrangement.
    Only returns sessions whose subjects have NO existing Local Arrangement or displacement record,
    meaning they are still "protected" by their Official schedule slot.

    Request JSON:
        ay_id           – academicyearid
        term            – semester type (A/B/C)
        sessions        – list of {room_id, day, start_time, end_time}
        exclude_subject – subjectcode to ignore (the one being saved)
    Response:
        {success, conflicts: [{subjectcode, subjectname, day, start_fmt, end_fmt, roomname,
                               instructor, programcode, yearlevel}]}
    """
    if 'loggedin' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    try:
        data             = request.json or {}
        ay_id            = data.get('ay_id', '').strip()
        term             = data.get('term', '').strip()
        sessions         = data.get('sessions', [])
        exclude_subj     = (data.get('exclude_subject') or '').strip().upper()

        if not ay_id or not term or not sessions:
            return jsonify({'success': True, 'conflicts': []})

        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_local_tables()
        try:
            sem_id = _get_semester_id(cur, ay_id, term)
        except Exception:
            cur.close(); conn.close()
            return jsonify({'success': True, 'conflicts': []})

        def _resolve_time_str(val):
            """Convert a time string like '04:00 PM' to timeslot.timeid."""
            if val is None:
                return None
            if isinstance(val, int):
                return val
            for fmt in ('HH12:MI AM', 'HH24:MI', 'HH24:MI:SS'):
                cur.execute(
                    "SELECT timeid FROM public.timeslot WHERE TO_CHAR(timevalue,%s)=%s LIMIT 1",
                    (fmt, str(val).strip())
                )
                row = cur.fetchone()
                if row:
                    return row['timeid']
            return None

        seen_conflicts = set()
        conflicts      = []

        for sess in sessions:
            room_id   = sess.get('room_id') or sess.get('roomid')
            day       = sess.get('day') or sess.get('daydesc') or ''
            start_id  = _resolve_time_str(sess.get('start_time') or sess.get('starttimeid'))
            end_id    = _resolve_time_str(sess.get('end_time')   or sess.get('endtimeid'))

            if not room_id or not day or start_id is None or end_id is None:
                continue

            cur.execute("""
                SELECT DISTINCT
                    UPPER(cs.subjectcode)                           AS subjectcode,
                    subj.subjectname,
                    ss.daydesc,
                    TO_CHAR(ts_s.timevalue,'HH12:MI AM')           AS start_fmt,
                    TO_CHAR(ts_e.timevalue,'HH12:MI AM')           AS end_fmt,
                    r.roomname,
                    COALESCE(f.lastname||', '||f.firstname,'—')    AS instructor,
                    UPPER(COALESCE(ao.offeringcode,''))             AS programcode,
                    pyl.yearlevel
                FROM schedule_sessions ss
                JOIN schedule_version sv ON ss.versionid = sv.versionid AND sv.status = 'Published'
                JOIN schedule s          ON sv.scheduleid = s.scheduleid AND s.semesterid = %s
                JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
                JOIN subject subj        ON cs.subjectcode = subj.subjectcode
                LEFT JOIN sections sec      ON s.sectionid = sec.sectionid
                LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                LEFT JOIN academic_offering ao  ON pyl.academicofferingid = ao.academicofferingid
                JOIN room r              ON ss.roomid = r.roomid
                LEFT JOIN faculty f      ON s.employeenumber = f.employeenumber
                LEFT JOIN timeslot ts_s  ON ss.starttimeid = ts_s.timeid
                LEFT JOIN timeslot ts_e  ON ss.endtimeid   = ts_e.timeid
                WHERE ss.roomid      = %s
                  AND ss.daydesc     = %s
                  AND ss.starttimeid < %s
                  AND ss.endtimeid   > %s
                  AND UPPER(cs.subjectcode) != %s
                  AND NOT EXISTS (
                      SELECT 1 FROM public.local_arrangement_sessions las
                      JOIN public.local_arrangement la ON las.arrangementid = la.arrangementid
                      WHERE UPPER(las.subjectcode) = UPPER(cs.subjectcode)
                        AND la.is_active  = TRUE
                        AND la.semesterid = s.semesterid
                        AND UPPER(la.programcode) = UPPER(COALESCE(ao.offeringcode,''))
                        AND la.yearlevel  = pyl.yearlevel
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM public.local_displaced_subjects lds
                      WHERE UPPER(lds.subjectcode) = UPPER(cs.subjectcode)
                        AND lds.semesterid  = s.semesterid
                        AND UPPER(lds.programcode) = UPPER(COALESCE(ao.offeringcode,''))
                        AND lds.yearlevel   = pyl.yearlevel
                        AND lds.is_active   = TRUE
                  )
            """, (sem_id, int(room_id), day, int(end_id), int(start_id), exclude_subj or ''))

            for row in cur.fetchall():
                key = (row['subjectcode'], row['daydesc'], row['start_fmt'])
                if key not in seen_conflicts:
                    seen_conflicts.add(key)
                    conflicts.append(dict(row))

        cur.close(); conn.close()
        return jsonify({'success': True, 'conflicts': conflicts})

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/get_room_schedule/<int:room_id>')
def get_room_schedule(room_id):
    if 'loggedin' not in session: return jsonify([])
    ay_id          = request.args.get('ay_id')
    semester       = request.args.get('semester')
    program        = request.args.get('program')
    scheduler_mode = request.args.get('scheduler_mode', 'official')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _ensure_local_tables()
        status_filter = "sv.status = 'Published'" if scheduler_mode == 'local' else "sv.status IN ('Published', 'Draft')"
        filters = ["ss.roomid = %s", status_filter]
        params  = [room_id]
        joins = """ JOIN semester sem ON s.semesterid = sem.semesterid
            LEFT JOIN sections sec ON s.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
            JOIN room r ON ss.roomid = r.roomid"""

        if scheduler_mode == 'local':
            # Exclude Official Published sessions for subjects that have already been
            # locally arranged (their slot is replaced) or explicitly displaced by an
            # override (their slot was freed for another Local Arrangement).
            # Scope by the schedule's own programcode + yearlevel so BSIT-3 overrides
            # don't incorrectly vacate BSCE-3's sessions in the same room.
            filters.append("""
                NOT EXISTS (
                    SELECT 1 FROM public.local_arrangement_sessions las
                    JOIN public.local_arrangement la ON las.arrangementid = la.arrangementid
                    WHERE UPPER(las.subjectcode) = UPPER(cs.subjectcode)
                      AND la.is_active = TRUE
                      AND la.semesterid = s.semesterid
                      AND UPPER(la.programcode) = UPPER(COALESCE(ao.offeringcode, ''))
                      AND la.yearlevel = pyl.yearlevel
                )
            """)
            filters.append("""
                NOT EXISTS (
                    SELECT 1 FROM public.local_displaced_subjects lds
                    WHERE UPPER(lds.subjectcode) = UPPER(cs.subjectcode)
                      AND lds.semesterid = s.semesterid
                      AND UPPER(lds.programcode) = UPPER(COALESCE(ao.offeringcode, ''))
                      AND lds.yearlevel = pyl.yearlevel
                      AND lds.is_active = TRUE
                )
            """)

        # Always exclude semesters that have already ended
        filters.append("(sem.semenddate IS NULL OR sem.semenddate >= CURRENT_DATE)")

        if ay_id:
            joins += " JOIN academicyear ay ON sem.academicyearid = ay.academicyearid"
            filters.append("ay.academicyearid = %s")
            params.append(ay_id)
        if semester:
            filters.append("sem.semestertype = %s")
            params.append(semester)
        if program:
            filters.append("ao.offeringcode = %s")
            params.append(program)

        cur.execute(f"""
            SELECT
                cs.subjectcode,
                subj.subjectname,
                f.lastname || ', ' || f.firstname AS instructor,
                ss.daydesc,
                ss.starttimeid,
                ss.endtimeid,
                pyl.yearlevel AS year_level,
                ao.offeringcode AS programcode,
                r.roomname,
                sv.status,
                sv.versionid
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule s ON sv.scheduleid = s.scheduleid
            {joins}
            JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject subj ON cs.subjectcode = subj.subjectcode
            JOIN faculty f ON s.employeenumber = f.employeenumber
            WHERE {' AND '.join(filters)}
        """, params)
        return jsonify(cur.fetchall())
    except Exception as e:
        print(f"Error fetching room schedule: {e}")
        return jsonify([])
    finally:
        cur.close(); conn.close()


# ── Single source of truth: faculty schedule (calendar + table) ──────────────
@app.route('/api/get_faculty_schedule')
def api_get_faculty_schedule():
    """
    Returns schedule sessions for a faculty member (or all faculty if emp_num omitted).
    Used by both the Weekly Schedule calendar and the Teaching Assignment table.
    Query params:
      emp_num    – faculty employee number (required for calendar; optional for table)
      ay_id      – academicyearid
      semester   – semester type ('A', 'B', 'C')
      program    – programcode filter (optional)
      year_level – yearlevel filter (optional)
    """
    if 'loggedin' not in session:
        return jsonify([])

    emp_num    = request.args.get('emp_num')
    ay_id      = request.args.get('ay_id')
    semester   = request.args.get('semester')
    program    = request.args.get('program')
    year_level = request.args.get('year_level')

    filters = ["sv.status = 'Published'"]
    params  = []

    if emp_num:
        filters.append("sc.employeenumber = %s")
        params.append(emp_num)

    if ay_id and semester:
        filters.append("""sc.semesterid = (
            SELECT semesterid FROM semester
            WHERE academicyearid = %s AND semestertype = %s LIMIT 1
        )""")
        params += [ay_id, semester]

    if program:
        filters.append("UPPER(ao.offeringcode) = UPPER(%s)")
        params.append(program)

    if year_level:
        filters.append("pyl.yearlevel = %s")
        params.append(int(year_level))

    try:
        rows = query_db(f"""
            SELECT sub.subjectcode, sub.subjectname, sub.creditunits,
                   sec.sectionname, pyl.yearlevel, ao.offeringcode AS programcode, ss.daydesc, r.roomname,
                   ss.starttimeid, ss.endtimeid, sv.status,
                   f.lastname || ', ' || f.firstname AS instructor,
                   sc.employeenumber,
                   TO_CHAR(ts_s.timevalue, 'HH12:MI AM') || ' - ' ||
                   TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS time_range
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub ON cs.subjectcode = sub.subjectcode
            LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
            LEFT JOIN room r ON ss.roomid = r.roomid
            LEFT JOIN faculty f ON sc.employeenumber = f.employeenumber
            LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
            WHERE {' AND '.join(filters)}
            ORDER BY ts_s.timevalue, ss.daydesc
        """, params or None)
        return jsonify([dict(r) for r in (rows or [])])
    except Exception as e:
        print(f"[api_get_faculty_schedule] Error: {e}")
        return jsonify([]), 500


@app.route('/api/manual/faculty_schedule')
def api_manual_faculty_schedule():
    """Published + Draft sessions for a faculty member — used for conflict checking in the manual scheduler."""
    if 'loggedin' not in session:
        return jsonify([])
    emp_num  = request.args.get('emp_num')
    ay_id    = request.args.get('ay_id')
    semester = request.args.get('semester')
    if not emp_num:
        return jsonify([])
    filters = ["sv.status IN ('Published', 'Draft')", "sc.employeenumber = %s"]
    params  = [emp_num]
    if ay_id and semester:
        filters.append("""sc.semesterid = (
            SELECT semesterid FROM semester
            WHERE academicyearid = %s AND semestertype = %s LIMIT 1
        )""")
        params += [ay_id, semester]
    try:
        rows = query_db(f"""
            SELECT sub.subjectcode, sub.subjectname, ss.daydesc, ss.starttimeid, ss.endtimeid, sv.status
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub ON cs.subjectcode = sub.subjectcode
            WHERE {' AND '.join(filters)}
        """, params)
        return jsonify([dict(r) for r in (rows or [])])
    except Exception as e:
        print(f"[api_manual_faculty_schedule] Error: {e}")
        return jsonify([]), 500

@app.route('/api/get_curriculum')
def api_get_curriculum():
    prog  = request.args.get('program')
    ay_id = request.args.get('ay_id')
    yl    = request.args.get('year_level')
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        res = None
        
        # 1. Hahanapin muna natin ang mismong Curriculum na naka-assign sa Cohort (Batch) nila
        # Kino-compute natin ang StartAcademicYear nila base sa kasalukuyang AY at sa Year Level nila.
        if prog and ay_id and yl and yl.isdigit():
            # Primary: find the curriculum assigned to this offering's program_yearlevel for this AY + year level
            cur.execute("""
                SELECT c.curriculumid, c.curriculumyear, c.curriculumcode,
                       ARRAY(SELECT DISTINCT cs2.yearlevel FROM curriculumsubject cs2
                             WHERE cs2.curriculumid = c.curriculumid ORDER BY cs2.yearlevel) AS year_levels
                FROM program_yearlevel pyl
                JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
                JOIN curriculum c ON pyl.curriculumid = c.curriculumid
                WHERE ao.offeringcode = %s
                  AND pyl.academicyearid = %s
                  AND pyl.yearlevel = %s
                LIMIT 1
            """, (prog, ay_id, int(yl)))
            res = cur.fetchone()

        # Fallback: latest curriculum for this offering
        if not res:
            cur.execute("""
                SELECT c.curriculumid, c.curriculumyear, c.curriculumcode,
                       ARRAY(SELECT DISTINCT cs2.yearlevel FROM curriculumsubject cs2
                             WHERE cs2.curriculumid = c.curriculumid ORDER BY cs2.yearlevel) AS year_levels
                FROM curriculum c
                JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
                WHERE ao.offeringcode = %s
                ORDER BY c.curriculumyear DESC LIMIT 1
            """, (prog,))
            res = cur.fetchone()

        if res:
            return jsonify({
                "success": True,
                "curriculum_id": res['curriculumid'],
                "curriculum_code": res['curriculumcode'],
                "label": f"{res['curriculumcode']} (C.Y {res['curriculumyear']})",
                "available_year_levels": res['year_levels']
            })
        return jsonify({"success": False})
    finally:
        cur.close(); conn.close()

@app.route('/api/get_existing_schedule_periods')
def api_get_existing_schedule_periods():
    prog = request.args.get('program')
    ay_id = request.args.get('ay_id')
    if not ay_id:
        return jsonify({"success": False, "done_semesters": [], "current_sem": None, "valid_sems": []})
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # done_semesters is program-scoped — only populate when a program is chosen
        done = []
        if prog:
            cur.execute("""
                SELECT DISTINCT sem.semestertype AS semester_code
                FROM schedule sch
                JOIN semester sem ON sch.semesterid = sem.semesterid
                JOIN academicyear ay ON sem.academicyearid = ay.academicyearid
                JOIN sections sec ON sch.sectionid = sec.sectionid
                JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
                WHERE ao.offeringcode = %s AND ay.academicyearid = %s
            """, (prog, ay_id))
            done = [r['semester_code'] for r in cur.fetchall()]
        from datetime import date as _date
        today = _date.today()
        # Current semester = one that is ongoing right now (started, not yet ended)
        cur.execute("""
            SELECT semestertype FROM semester
            WHERE academicyearid = %s
              AND (semstartdate IS NULL OR semstartdate <= %s)
              AND (semenddate   IS NULL OR semenddate   >= %s)
            LIMIT 1
        """, (ay_id, today, today))
        row = cur.fetchone()
        current_sem = row['semestertype'] if row else None
        # Valid = not ended yet (NULL end date counts as "not past")
        cur.execute("""
            SELECT semestertype FROM semester
            WHERE academicyearid = %s
              AND (semenddate IS NULL OR semenddate >= %s)
        """, (ay_id, today))
        valid_sems = [r['semestertype'] for r in cur.fetchall()]
        return jsonify({"success": True, "done_semesters": done, "current_sem": current_sem, "valid_sems": valid_sems})
    except Exception:
        return jsonify({"success": False, "done_semesters": [], "current_sem": None, "valid_sems": []})
    finally:
        cur.close(); conn.close()

@app.route('/api/dss/suggest')
def api_dss_suggest():
    subject_code = request.args.get('subject_code', '').strip()
    ay_id        = request.args.get('ay_id', '').strip()
    sem          = request.args.get('sem', '').strip()
    if not subject_code:
        return jsonify({"success": False, "error": "subject_code required"})
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 1. Is this a lab subject?
        cur.execute("SELECT LaboratoryHours FROM Subject WHERE SubjectCode = %s", (subject_code,))
        subj_row = cur.fetchone()
        is_lab = bool(subj_row and subj_row.get('laboratoryhours') and subj_row['laboratoryhours'] > 0)

        # 2. All active faculty with type info
        cur.execute("""
            SELECT f.EmployeeNumber,
                   f.LastName || ', ' || f.FirstName ||
                   CASE WHEN f.MiddleName IS NOT NULL AND f.MiddleName != ''
                        THEN ' ' || f.MiddleName ELSE '' END AS fullname,
                   COALESCE(et.TypeName, 'Regular') AS typename,
                   COALESCE(et.regularload, 0)           AS regularload,
                   COALESCE(et.parttimeload, 0)          AS parttimeload,
                   COALESCE(et.teachingsubstitution, 0)  AS teachingsubstitution,
                   f.EmployeeStatus, f.DesignationID,
                   COALESCE(d.regularloadunit, 0)        AS designation_regular_load,
                   COALESCE(d.nightteachingservice, 0)   AS designation_night_service
            FROM Faculty f
            LEFT JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID
            LEFT JOIN Designation d   ON f.DesignationID  = d.DesignationID
            WHERE f.EmployeeStatus != 'Archived' ORDER BY f.LastName
        """)
        all_faculty = [dict(r) for r in cur.fetchall()]

        # 2b. Batch-fetch assigned units per faculty for this AY/semester
        fac_loads = {}
        if ay_id and sem:
            cur.execute("""
                SELECT d.employeenumber,
                       COALESCE(SUM(d.creditunits), 0) AS assigned_units
                FROM (
                    SELECT DISTINCT sc.employeenumber, cs.subjectcode, sub.creditunits
                    FROM schedule_version sv
                    JOIN schedule sc          ON sv.scheduleid = sc.scheduleid
                    JOIN curriculumsubject cs  ON sc.curriculumsubjectid = cs.curriculumsubjectid
                    JOIN subject sub           ON cs.subjectcode = sub.subjectcode
                    JOIN semester s            ON sc.semesterid  = s.semesterid
                    WHERE s.academicyearid = %s AND s.semestertype = %s
                      AND sv.status IN ('Published', 'Draft')
                ) AS d
                GROUP BY d.employeenumber
            """, (ay_id, sem))
            for r in cur.fetchall():
                fac_loads[r['employeenumber']] = int(r['assigned_units'] or 0)

        def _fac_item(f, **extra):
            status    = (f.get('employeestatus') or '').lower()
            has_desig = f.get('designationid') is not None
            teach_sub = int(f.get('teachingsubstitution') or 0)
            if has_desig:
                reg_load = int(f.get('designation_regular_load') or 0)
                pt_load  = int(f.get('designation_night_service') or 0)
            elif 'part' in status:
                reg_load = 0
                pt_load  = int(f.get('parttimeload') or 0)
            else:
                reg_load = int(f.get('regularload') or 0)
                pt_load  = int(f.get('parttimeload') or 0)
            max_u = reg_load + pt_load + teach_sub
            assigned = fac_loads.get(f['employeenumber'], 0) if (ay_id and sem) else None
            item = {
                "id":       f['employeenumber'],
                "name":     f['fullname'].strip(),
                "typename": f.get('typename', 'Regular'),
            }
            if ay_id and sem:
                item["max_units"]      = max_u
                item["assigned_units"] = assigned
            item.update(extra)
            return item

        # 3. Historical instructor frequency for this subject
        cur.execute("""
            SELECT TRIM("Instructor") AS instructor, COUNT(*) AS freq
            FROM historical_data
            WHERE UPPER(TRIM("Subject Code")) = UPPER(TRIM(%s))
              AND "Instructor" IS NOT NULL AND TRIM("Instructor") != ''
            GROUP BY TRIM("Instructor")
            ORDER BY freq DESC
            LIMIT 15
        """, (subject_code,))
        hist_faculty = cur.fetchall()

        # 4. Match historical names to faculty records (by last name prefix)
        recommended_ids = set()
        recommended_faculty = []
        for hf in hist_faculty:
            hist_name = (hf['instructor'] or '').strip().lower()
            for f in all_faculty:
                last = f['fullname'].split(',')[0].strip().lower()
                if last and (hist_name.startswith(last + ',') or hist_name.startswith(last + ' ') or hist_name == last):
                    if f['employeenumber'] not in recommended_ids:
                        recommended_ids.add(f['employeenumber'])
                        recommended_faculty.append(_fac_item(f, count=int(hf['freq'])))
                    break
        others_faculty = [
            _fac_item(f)
            for f in all_faculty if f['employeenumber'] not in recommended_ids
        ]

        # RF: re-rank recommended_faculty by predicted score.
        # Replaces raw frequency-count ordering with a multi-variate RF prediction
        # that weighs freq_together, instructor_total, subject_total, and ratio jointly.
        if _SKLEARN_OK:
            _train_rf_dss()
            for entry in recommended_faculty:
                entry['rf_score'] = _rf_score_faculty(subject_code, entry['name'])
            recommended_faculty.sort(key=lambda x: x.get('rf_score', 0.0), reverse=True)

        # 5. All rooms with type
        cur.execute("""
            SELECT r.RoomID, r.RoomName, COALESCE(r.RoomType, 'Lecture') AS RoomType
            FROM Room r
            ORDER BY
                regexp_replace(r.RoomName, '[0-9]', '', 'g'),
                CASE WHEN regexp_replace(r.RoomName, '[^0-9]', '', 'g') = ''
                     THEN 0
                     ELSE CAST(regexp_replace(r.RoomName, '[^0-9]', '', 'g') AS BIGINT) END
        """)
        all_rooms = [dict(r) for r in cur.fetchall()]

        # 6. Historical room frequency for this subject
        cur.execute("""
            SELECT TRIM("Room") AS room, COUNT(*) AS freq
            FROM historical_data
            WHERE UPPER(TRIM("Subject Code")) = UPPER(TRIM(%s))
              AND "Room" IS NOT NULL AND TRIM("Room") != ''
            GROUP BY TRIM("Room")
            ORDER BY freq DESC
            LIMIT 15
        """, (subject_code,))
        hist_rooms = cur.fetchall()

        # 7. Match historical room names to room records
        recommended_room_ids = set()
        recommended_rooms = []
        for hr in hist_rooms:
            hist_room = (hr['room'] or '').strip().upper()
            for r in all_rooms:
                if r['roomname'].upper() == hist_room or hist_room in r['roomname'].upper():
                    if r['roomid'] not in recommended_room_ids:
                        recommended_room_ids.add(r['roomid'])
                        recommended_rooms.append({
                            "id": r['roomid'],
                            "name": r['roomname'],
                            "type": r['roomtype'],
                            "count": int(hr['freq'])
                        })
                    break
        others_rooms = [
            {"id": r['roomid'], "name": r['roomname'], "type": r['roomtype']}
            for r in all_rooms if r['roomid'] not in recommended_room_ids
        ]
        # Sort Others by type compatibility: lab rooms first for lab subjects, lecture first for non-lab
        if is_lab:
            others_rooms.sort(key=lambda r: (0 if r['type'] == 'Laboratory' else 1))
        else:
            others_rooms.sort(key=lambda r: (0 if r['type'] != 'Laboratory' else 1))

        # RF: re-rank recommended_rooms by predicted score.
        # Same multi-variate approach as faculty — RF considers freq_together,
        # room_total usage, subject_total, and ratio to produce a richer ranking.
        if _SKLEARN_OK:
            for entry in recommended_rooms:
                entry['rf_score'] = _rf_score_room(subject_code, entry['name'])
            recommended_rooms.sort(key=lambda x: x.get('rf_score', 0.0), reverse=True)

        from database import load_scheduler_config as _lsc
        _lab_cfg = _lsc()
        lab_constraint_enabled = bool(_lab_cfg.get('hc_lab_session_enabled', 1))

        return jsonify({
            "success": True,
            "is_lab": is_lab,
            "lab_constraint_enabled": lab_constraint_enabled,
            "faculty": {"recommended": recommended_faculty, "others": others_faculty},
            "rooms":   {"recommended": recommended_rooms,  "others": others_rooms}
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
    finally:
        cur.close(); conn.close()

@app.route('/api/hc_config')
def api_hc_config():
    """Return hard-constraint settings for the manual editor frontend."""
    from database import load_scheduler_config as _lsc
    import json as _j
    hc = _lsc()

    # Day pairs — convert [[d1,d2],...] JSON string to list of pairs
    raw_pairs = hc.get('hc_day_pairs', '[]')
    try:
        pairs = _j.loads(raw_pairs)
    except Exception:
        pairs = [['Monday','Thursday'],['Tuesday','Friday'],['Wednesday','Saturday']]

    return jsonify({
        'hc_weekend_enabled':       int(hc.get('hc_weekend_enabled',       1)),
        'hc_day_pairing_enabled':   int(hc.get('hc_day_pairing_enabled',   1)),
        'hc_faculty_load_enabled':  int(hc.get('hc_faculty_load_enabled',  1)),
        'hc_room_conflict_enabled': int(hc.get('hc_room_conflict_enabled', 1)),
        'hc_publish_gate_enabled':  int(hc.get('hc_publish_gate_enabled',  1)),
        'hc_faculty_spec_enabled':  int(hc.get('hc_faculty_spec_enabled',  1)),
        'hc_weekend_subject':       hc.get('hc_weekend_subject', 'nstp_only'),
        'hc_weekend_day':           hc.get('hc_weekend_day',     'sunday_only'),
        'day_pairs': pairs,
    })

@app.route('/api/manual/subject_info')
def api_manual_subject_info():
    subject_code = request.args.get('subject_code', '').strip()
    if not subject_code:
        return jsonify({'success': False})
    row = query_db("""
        SELECT lecturehours, laboratoryhours, creditunits
        FROM subject WHERE UPPER(subjectcode) = UPPER(%s)
    """, (subject_code,), one=True)
    if not row:
        return jsonify({'success': False})
    lh  = float(row['lecturehours']    or 0)
    lab = float(row['laboratoryhours'] or 0)

    # Read weekend restriction from HC config (respects Settings toggle)
    from database import load_scheduler_config as _lsc
    hc         = _lsc()
    wk_enabled = bool(hc.get('hc_weekend_enabled', 1))
    subj_restr = hc.get('hc_weekend_subject', 'nstp_only')

    is_nstp_ou = subject_code.upper().startswith(('NSTP', 'OU'))
    is_nstp    = subject_code.upper().startswith('NSTP')

    wk_day = hc.get('hc_weekend_day', 'sunday_only')

    if not wk_enabled or subj_restr == 'all_allowed':
        # HC4 is disabled — all subjects may use any day including Sunday
        is_sunday_allowed = True
        is_sunday_only    = False
        is_weekend_only   = False
    else:
        # HC4 enabled
        is_sunday_allowed = is_nstp_ou
        # sunday_only scope: NSTP locked to Sunday only
        # all_weekends scope: NSTP allowed on both Saturday AND Sunday
        is_sunday_only    = is_nstp and (wk_day == 'sunday_only')
        is_weekend_only   = is_nstp and (wk_day == 'all_weekends')

    return jsonify({
        'success': True,
        'lecturehours':    lh,
        'laboratoryhours': lab,
        'creditunits':     int(row['creditunits'] or 0),
        'total_hours':     (lh + lab) if (lh + lab) > 0 else 3.0,
        'is_sunday_allowed': is_sunday_allowed,
        'is_sunday_only':    is_sunday_only,
        'is_weekend_only':   is_weekend_only,
    })


@app.route('/api/manual/faculty_info')
def api_manual_faculty_info():
    emp_num = request.args.get('emp_num', '').strip()
    if not emp_num:
        return jsonify({'success': False})
    row = query_db("""
        SELECT f.employeestatus, et.typename, f.designationid,
               COALESCE(d.nightteachingservice, 0)  AS night_service,
               COALESCE(d.regularloadunit, 0)        AS designation_load,
               COALESCE(d.nightteachingservice, 0)   AS designation_pt_load,
               d.designationname,
               COALESCE(et.regularload, 0)           AS regularload,
               COALESCE(et.parttimeload, 0)          AS parttimeload,
               et.regular_start, et.regular_end,
               et.parttime_start, et.parttime_end,
               f.specializationid,
               sp.specializationname
        FROM faculty f
        JOIN employeetype et ON f.employeetypeid = et.employeetypeid
        LEFT JOIN designation d   ON f.designationid   = d.designationid
        LEFT JOIN specialization sp ON f.specializationid = sp.specializationid
        WHERE f.employeenumber = %s
    """, (emp_num,), one=True)
    if not row:
        return jsonify({'success': False})
    def _fmt(t):
        if t is None: return None
        return t.strftime('%H:%M') if hasattr(t, 'strftime') else str(t)[:5]
    has_desig = row['designationid'] is not None
    status    = (row['employeestatus'] or '').lower()
    eff_regular  = row['designation_load'] if (has_desig and row['designation_load']) else row['regularload']
    eff_parttime = row['designation_pt_load'] if has_desig else row['parttimeload']
    return jsonify({
        'success':            True,
        'typename':           row['typename'],
        'employeestatus':     row['employeestatus'],
        'has_designation':    has_desig,
        'designation_name':   row['designationname'] or '',
        'night_service':      int(row['night_service']       or 0),
        'designation_load':   int(row['designation_load']    or 0),
        'effective_regularload':  int(eff_regular  or 0),
        'effective_parttimeload': int(eff_parttime or 0),
        'regularload':        int(row['regularload']         or 0),
        'parttimeload':       int(row['parttimeload']        or 0),
        'regular_start':      _fmt(row['regular_start']),
        'regular_end':        _fmt(row['regular_end']),
        'parttime_start':     _fmt(row['parttime_start']),
        'parttime_end':       _fmt(row['parttime_end']),
        'specializationid':   row['specializationid'],
        'specializationname': row['specializationname'],
    })


@app.route('/api/manual/faculty_load')
def api_manual_faculty_load():
    emp_num        = request.args.get('emp_num', '').strip()
    ay_id          = request.args.get('ay_id', '').strip()
    sem            = request.args.get('sem', '').strip()
    scheduler_mode = request.args.get('scheduler_mode', 'official')
    if not emp_num:
        return jsonify({'success': False})
    row = query_db("""
        SELECT f.employeestatus, f.designationid,
               COALESCE(et.regularload, 0)           AS regularload,
               COALESCE(et.parttimeload, 0)          AS parttimeload,
               COALESCE(et.teachingsubstitution, 0)  AS teachingsubstitution,
               COALESCE(d.regularloadunit, 0)        AS designation_regular_load,
               COALESCE(d.nightteachingservice, 0)   AS designation_night_service
        FROM faculty f
        JOIN employeetype et ON f.employeetypeid = et.employeetypeid
        LEFT JOIN designation d ON f.designationid = d.designationid
        WHERE f.employeenumber = %s
    """, (emp_num,), one=True)
    if not row:
        return jsonify({'success': False})
    status    = (row['employeestatus'] or '').lower()
    has_desig = row['designationid'] is not None
    teach_sub = int(row['teachingsubstitution'] or 0)
    if has_desig:
        reg_load = int(row['designation_regular_load'] or 0)
        pt_load  = int(row['designation_night_service'] or 0)
    elif 'part' in status:
        reg_load = 0
        pt_load  = int(row['parttimeload'] or 0)
    else:
        reg_load = int(row['regularload'] or 0)
        pt_load  = int(row['parttimeload'] or 0)
    max_load = reg_load + pt_load + teach_sub
    scheduled = 0
    if ay_id and sem:
        # --- FIX: Ginamit ang Lecture + Lab Hours bilang load units ---
        sched = query_db("""
            SELECT COALESCE(SUM(d.load_units), 0) AS sched_units
            FROM (
                SELECT DISTINCT cs.subjectcode, 
                       (COALESCE(sub.lecturehours, 0) + COALESCE(sub.laboratoryhours, 0)) AS load_units
                FROM schedule_version sv
                JOIN schedule sc ON sv.scheduleid = sc.scheduleid
                JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                JOIN subject sub ON cs.subjectcode = sub.subjectcode
                JOIN semester s ON sc.semesterid = s.semesterid
                WHERE sc.employeenumber = %s
                  AND s.academicyearid = %s
                  AND s.semestertype = %s
                  AND sv.status IN ('Published', {})
            ) AS d
        """.format("'Published'" if scheduler_mode == 'local' else "'Published', 'Draft'"),
        (emp_num, ay_id, sem), one=True)
        if sched:
            scheduled = int(sched['sched_units'] or 0)
    available = max(0, max_load - scheduled)

    # HC7 — count weekday night sessions
    night_classes = 0
    if ay_id and sem:
        nq = query_db("""
            SELECT COUNT(DISTINCT ss.sessionid) AS night_count
            FROM schedule_sessions ss
            JOIN schedule_version sv ON sv.versionid = sv.versionid
            JOIN schedule sc          ON sv.scheduleid = sc.scheduleid
            JOIN semester s           ON sc.semesterid  = s.semesterid
            JOIN timeslot ts          ON ss.starttimeid  = ts.timeid
            WHERE sc.employeenumber = %s
              AND s.academicyearid  = %s
              AND s.semestertype    = %s
              AND sv.status IN ('Published', 'Draft')
              AND ss.daydesc IN ('Monday','Tuesday','Wednesday','Thursday','Friday')
              AND ts.timevalue >= '18:00:00'
        """, (emp_num, ay_id, sem), one=True)
        if nq:
            night_classes = int(nq['night_count'] or 0)

    assigned_subjects = []
    if ay_id and sem:
        # --- FIX: Gamitin ang sum ng Lec + Lab para sa display sa UI ---
        rows = query_db("""
            SELECT DISTINCT sub.subjectcode, sub.subjectname,
                   (COALESCE(sub.lecturehours, 0) + COALESCE(sub.laboratoryhours, 0)) AS creditunits
            FROM schedule_version sv
            JOIN schedule sc          ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs  ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub           ON cs.subjectcode = sub.subjectcode
            JOIN semester s            ON sc.semesterid  = s.semesterid
            WHERE sc.employeenumber = %s
              AND s.academicyearid  = %s
              AND s.semestertype    = %s
              AND sv.status IN ('Published', 'Draft')
            ORDER BY sub.subjectcode
        """, (emp_num, ay_id, sem))
        assigned_subjects = [dict(r) for r in (rows or [])]

    return jsonify({'success': True, 'total_units': max_load,
                    'available_units': available, 'scheduled_units': scheduled,
                    'night_classes': night_classes,
                    'assigned_subjects': assigned_subjects})


# ── Faculty assignment persistence ─────────────────────────────────────────
# Stores faculty→subject assignments that were confirmed via the "Assign Faculty"
# button but have not yet been backed by saved schedule sessions.
# Once sessions are saved (save_draft), this record is cleaned up automatically.

def _ensure_faculty_assignment_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS public.subject_faculty_assignment (
            id           SERIAL PRIMARY KEY,
            programcode  VARCHAR(20)  NOT NULL,
            yearlevel    SMALLINT     NOT NULL,
            semesterid   INTEGER      NOT NULL,
            subjectcode  VARCHAR(50)  NOT NULL,
            employeenumber VARCHAR(50) NOT NULL,
            createdat    TIMESTAMP    DEFAULT NOW(),
            UNIQUE (programcode, yearlevel, semesterid, subjectcode)
        )
    """)

@app.route('/api/manual/assign_faculty', methods=['GET'])
def api_manual_get_assignments():
    program    = request.args.get('program', '').strip()
    year_level = request.args.get('year_level', '').strip()
    ay         = request.args.get('ay_id', '').strip()
    term       = request.args.get('sem', '').strip()
    if not (program and year_level and ay and term):
        return jsonify({'success': True, 'assignments': []})
    try:
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_faculty_assignment_table(cur)
        conn.commit()
        sem_id = _get_semester_id(cur, ay, term)
        cur.execute("""
            SELECT sfa.subjectcode, sfa.employeenumber,
                   TRIM(CONCAT(f.lastname, ', ', f.firstname,
                       CASE WHEN f.middlename IS NOT NULL AND f.middlename <> ''
                            THEN ' ' || LEFT(f.middlename,1) || '.' ELSE '' END)) AS facultyname
            FROM public.subject_faculty_assignment sfa
            JOIN public.faculty f ON f.employeenumber = sfa.employeenumber
            WHERE UPPER(sfa.programcode) = UPPER(%s)
              AND sfa.yearlevel  = %s
              AND sfa.semesterid = %s
        """, (program, int(year_level), sem_id))
        rows = cur.fetchall()
        cur.close(); conn.close()
        return jsonify({'success': True, 'assignments': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'success': True, 'assignments': [], 'error': str(e)})

@app.route('/api/manual/assign_faculty', methods=['POST'])
def api_manual_save_assignment():
    data = request.json or {}
    program     = data.get('program', '').strip()
    year_level  = data.get('year_level', 0)
    ay          = data.get('acadYear', '').strip()
    term        = data.get('term', '').strip()
    subjectcode = data.get('subjectcode', '').strip().upper()
    emp_num     = str(data.get('employeenumber', '')).strip()
    if not all([program, year_level, ay, term, subjectcode, emp_num]):
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400
    try:
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_faculty_assignment_table(cur)
        sem_id = _get_semester_id(cur, ay, term)
        cur.execute("""
            INSERT INTO public.subject_faculty_assignment
                (programcode, yearlevel, semesterid, subjectcode, employeenumber)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (programcode, yearlevel, semesterid, subjectcode)
            DO UPDATE SET employeenumber = EXCLUDED.employeenumber, createdat = NOW()
        """, (program.upper(), int(year_level), sem_id, subjectcode, emp_num))
        conn.commit(); cur.close(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/manual/assign_faculty/<subjectcode>', methods=['DELETE'])
def api_manual_delete_assignment(subjectcode):
    program    = request.args.get('program', '').strip()
    year_level = request.args.get('year_level', '').strip()
    ay         = request.args.get('ay_id', '').strip()
    term       = request.args.get('sem', '').strip()
    if not all([program, year_level, ay, term]):
        return jsonify({'success': False, 'error': 'Missing parameters'}), 400
    try:
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_faculty_assignment_table(cur)
        sem_id = _get_semester_id(cur, ay, term)
        cur.execute("""
            DELETE FROM public.subject_faculty_assignment
            WHERE UPPER(programcode)   = UPPER(%s)
              AND yearlevel            = %s
              AND semesterid           = %s
              AND UPPER(subjectcode)   = UPPER(%s)
        """, (program, int(year_level), sem_id, subjectcode.upper()))
        conn.commit(); cur.close(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/manual/existing_days')
def api_manual_existing_days():
    subj  = request.args.get('subject_code', '').strip()
    ay_id = request.args.get('ay_id', '').strip()
    sem   = request.args.get('semester', '').strip()
    prog  = request.args.get('program', '').strip()
    yl    = request.args.get('year_level', '').strip()
    if not subj:
        return jsonify({'success': True, 'days': []})
    filters = ["UPPER(sub.subjectcode) = UPPER(%s)", "sv.status IN ('Published','Draft')"]
    params  = [subj]
    if ay_id and sem:
        filters.append("sc.semesterid = (SELECT semesterid FROM semester WHERE academicyearid = %s AND semestertype = %s LIMIT 1)")
        params += [ay_id, sem]
    if prog:
        filters.append("UPPER(ao.offeringcode) = UPPER(%s)")
        params.append(prog)
    if yl:
        filters.append("pyl.yearlevel = %s")
        params.append(int(yl))
    rows = query_db(f"""
        SELECT DISTINCT ss.daydesc
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        JOIN subject sub ON cs.subjectcode = sub.subjectcode
        LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
        LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
        LEFT JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
        WHERE {' AND '.join(filters)}
    """, params or None) or []
    return jsonify({'success': True, 'days': [r['daydesc'] for r in rows]})


@app.route('/api/manual/existing_sessions')
def api_manual_existing_sessions():
    subj          = request.args.get('subject_code', '').strip()
    ay_id         = request.args.get('ay_id', '').strip()
    sem           = request.args.get('semester', '').strip()
    prog          = request.args.get('program', '').strip()
    yl            = request.args.get('year_level', '').strip()
    scheduler_mode = request.args.get('scheduler_mode', 'official')
    if not subj or not ay_id or not sem:
        return jsonify({'success': True, 'sessions': []})
    base_filters = [
        "UPPER(sub.subjectcode) = UPPER(%s)",
        "sc.semesterid = (SELECT semesterid FROM semester WHERE academicyearid = %s AND semestertype = %s LIMIT 1)"
    ]
    base_params = [subj, ay_id, sem]
    if prog:
        base_filters.append("ao.offeringcode = %s")
        base_params.append(prog)
    if yl:
        base_filters.append("pyl.yearlevel = %s")
        base_params.append(int(yl))

    session_query = f"""
        SELECT ss.starttimeid, ss.endtimeid, ss.daydesc,
               sv.versionid,
               sub.subjectcode, sub.subjectname,
               pyl.yearlevel, ao.offeringcode AS programcode,
               r.roomid, r.roomname,
               f.employeenumber,
               f.lastname || ', ' || f.firstname AS instructor,
               sv.status,
               TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_fmt,
               TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_fmt
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        JOIN subject sub ON cs.subjectcode = sub.subjectcode
        LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
        LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
        LEFT JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
        LEFT JOIN room r ON ss.roomid = r.roomid
        LEFT JOIN faculty f ON sc.employeenumber = f.employeenumber
        LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
        LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
        WHERE {{status_clause}}
        ORDER BY ts_s.timevalue, ss.daydesc
    """

    draft_rows = query_db(
        session_query.format(status_clause=' AND '.join(base_filters + ["sv.status = 'Draft'"])),
        base_params) or []

    pub_rows = query_db(
        session_query.format(status_clause=' AND '.join(base_filters + ["sv.status = 'Published'"])),
        base_params) or []

    if scheduler_mode == 'local':
        # Local Scheduler: if this subject already has an active Local Arrangement,
        # return its sessions so the slice panel, calendar, and Version History all
        # show the same data. Prefer Published LA over Draft; fall back to Official
        # Published only when no Local Arrangement exists yet.
        if prog and yl:
            _ensure_local_tables()
            la_rows = query_db("""
                SELECT las.starttimeid, las.endtimeid, las.daydesc,
                       NULL::integer                                          AS versionid,
                       UPPER(las.subjectcode)                                AS subjectcode,
                       subj.subjectname,
                       la.yearlevel, la.programcode,
                       las.roomid, r.roomname,
                       las.faculty_employeenumber                            AS employeenumber,
                       COALESCE(f.lastname || ', ' || f.firstname, '')       AS instructor,
                       la.status,
                       TO_CHAR(ts_s.timevalue, 'HH12:MI AM')                AS start_fmt,
                       TO_CHAR(ts_e.timevalue, 'HH12:MI AM')                AS end_fmt
                FROM public.local_arrangement_sessions las
                JOIN public.local_arrangement la ON las.arrangementid = la.arrangementid
                LEFT JOIN subject    subj ON UPPER(las.subjectcode)          = UPPER(subj.subjectcode)
                LEFT JOIN room       r    ON las.roomid                      = r.roomid
                LEFT JOIN faculty    f    ON las.faculty_employeenumber      = f.employeenumber
                LEFT JOIN timeslot ts_s  ON las.starttimeid                 = ts_s.timeid
                LEFT JOIN timeslot ts_e  ON las.endtimeid                   = ts_e.timeid
                WHERE UPPER(las.subjectcode) = UPPER(%s)
                  AND la.arrangementid = (
                      SELECT la2.arrangementid
                      FROM   public.local_arrangement la2
                      JOIN   public.local_arrangement_sessions las2
                             ON la2.arrangementid = las2.arrangementid
                      WHERE  UPPER(las2.subjectcode) = UPPER(%s)
                        AND  la2.semesterid = (
                               SELECT semesterid FROM semester
                               WHERE  academicyearid = %s AND semestertype = %s LIMIT 1)
                        AND  UPPER(la2.programcode) = UPPER(%s)
                        AND  la2.yearlevel  = %s
                        AND  la2.is_active  = TRUE
                        AND  la2.status    IN ('Published', 'Draft')
                      ORDER BY CASE WHEN la2.status = 'Published' THEN 0 ELSE 1 END,
                               la2.arrangementid DESC
                      LIMIT 1
                  )
                ORDER BY ts_s.timevalue, las.daydesc
            """, (subj, subj, ay_id, sem, prog, int(yl))) or []

            if la_rows:
                return jsonify({'success': True, 'sessions': [dict(r) for r in la_rows]})

        # No Local Arrangement found — fall back to Official Published sessions
        rows = [dict(r) for r in pub_rows]
    elif draft_rows:
        # Official Scheduler: merge Draft + Published, promoting overlap slots to Published colour
        pub_slot_keys = {(r['daydesc'], r['starttimeid']) for r in pub_rows}
        rows = []
        for r in draft_rows:
            r = dict(r)
            if (r['daydesc'], r['starttimeid']) in pub_slot_keys:
                r['status'] = 'Published'
            rows.append(r)

        # With draft_only=True carry-forward, Published slots that are NOT in the Draft
        # snapshot were never "removed" — they still exist and must be shown alongside the
        # Draft additions. Include them here unless they were explicitly deleted this session.
        draft_slot_keys = {(r['daydesc'], r['starttimeid']) for r in draft_rows}
        for r in pub_rows:
            if (r['daydesc'], r['starttimeid']) not in draft_slot_keys:
                rows.append(dict(r))
    else:
        rows = [dict(r) for r in pub_rows]
    return jsonify({'success': True, 'sessions': rows})


@app.route('/api/manual/section_schedule')
def api_manual_section_schedule():
    """Published + Draft sessions for a section (program + year level).
    In local mode returns Published-only — the official base must not include Drafts."""
    if 'loggedin' not in session:
        return jsonify([])
    prog           = request.args.get('program', '').strip()
    yl             = request.args.get('year_level', '').strip()
    ay_id          = request.args.get('ay_id', '').strip()
    sem            = request.args.get('semester', '').strip()
    scheduler_mode = request.args.get('scheduler_mode', 'official')
    if not prog or not yl or not ay_id or not sem:
        return jsonify([])
    status_clause = "sv.status = 'Published'" if scheduler_mode == 'local' else "sv.status IN ('Published', 'Draft')"
    try:
        rows = query_db(f"""
            SELECT
                cs.subjectcode,
                subj.subjectname,
                ss.daydesc,
                ss.starttimeid,
                ss.endtimeid,
                sv.status
            FROM schedule_sessions ss
            JOIN schedule_version sv  ON ss.versionid  = sv.versionid
            JOIN schedule sc          ON sv.scheduleid  = sc.scheduleid
            JOIN semester s           ON sc.semesterid  = s.semesterid
            JOIN academicyear ay      ON s.academicyearid = ay.academicyearid
            JOIN sections sec             ON sc.sectionid            = sec.sectionid
            JOIN program_yearlevel pyl    ON sec.programyearlevelid  = pyl.programyearlevelid
            JOIN academic_offering ao     ON pyl.academicofferingid  = ao.academicofferingid
            JOIN curriculumsubject cs     ON sc.curriculumsubjectid  = cs.curriculumsubjectid
            JOIN subject subj            ON cs.subjectcode           = subj.subjectcode
            WHERE ao.offeringcode              = %s
              AND pyl.yearlevel::text          = %s
              AND ay.academicyearid::text      = %s
              AND s.semestertype               = %s
              AND {status_clause}
        """, (prog, yl, ay_id, sem))
        return jsonify([dict(r) for r in (rows or [])])
    except Exception as e:
        print(f"[api_manual_section_schedule] Error: {e}")
        return jsonify([])


@app.route('/api/get_valid_semesters')
def api_get_valid_semesters():
    curr_id = request.args.get('curriculum_id')
    yl = request.args.get('year_level')
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT DISTINCT Semester FROM CurriculumSubject
            WHERE CurriculumID = %s AND YearLevel = %s
            ORDER BY Semester
        """, (curr_id, yl))
        semesters = [r['semester'] for r in cur.fetchall()]
        return jsonify({"success": True, "semesters": semesters})
    finally:
        cur.close(); conn.close()

@app.route('/api/get_subjects')
def api_get_subjects():
    curr_id        = request.args.get('curriculum_id')
    yl             = request.args.get('year_level')
    sem            = request.args.get('semester')
    ay_id          = request.args.get('ay_id', '').strip()
    scheduler_mode = request.args.get('scheduler_mode', 'official')
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT s.SubjectCode, s.SubjectName,
                   COALESCE(s.CreditUnits, 0) AS creditunits,
                   COALESCE(s.LaboratoryHours, 0) AS laboratoryhours,
                   (COALESCE(s.LectureHours, 0) + COALESCE(s.LaboratoryHours, 0)) AS total_hours
            FROM CurriculumSubject cs
            JOIN Subject s ON cs.SubjectCode = s.SubjectCode
            WHERE cs.CurriculumID = %s AND cs.YearLevel = %s AND cs.Semester = %s
            ORDER BY s.SubjectName ASC
        """, (curr_id, yl, sem))
        subjects = cur.fetchall()

        # Attach scheduled hours per subject when AY context is provided.
        # Uses (endtimeid - starttimeid) * 0.5 to avoid fragile timeslot join.
        # Prefers Draft over Published per subject to avoid double-counting.
        # Does NOT filter by curriculum/yearlevel/semester in the outer query —
        # _insert_batch may save sessions under a different curriculumsubjectid
        # (subject code + program, no year/sem), so filtering by cs2.yearlevel/semester
        # would silently exclude valid sessions.
        sched_map = {}
        if ay_id and sem and curr_id and yl:
            try:
                # Local mode: only count Published Official sessions as the base.
                # Official Scheduler drafts are irrelevant inside the Local Scheduler.
                version_filter = "sv.status = 'Published'" if scheduler_mode == 'local' \
                                 else "sv.status IN ('Draft', 'Published')"

                sched_rows = query_db(f"""
                    WITH sem_cte AS (
                        SELECT semesterid FROM semester
                        WHERE  academicyearid = %s AND semestertype = %s LIMIT 1
                    ),
                    prog_cte AS (
                        SELECT ao.offeringcode
                        FROM   curriculum c
                        JOIN   academic_offering ao ON c.academicofferingid = ao.academicofferingid
                        WHERE  c.curriculumid = %s LIMIT 1
                    ),
                    max_version AS (
                        SELECT UPPER(sub2.subjectcode) AS subjectcode,
                               MAX(sv2.version_number)  AS max_v
                        FROM   schedule_version sv2
                        JOIN   schedule sc2           ON sv2.scheduleid          = sc2.scheduleid
                        JOIN   curriculumsubject cs2  ON sc2.curriculumsubjectid = cs2.curriculumsubjectid
                        JOIN   subject sub2           ON cs2.subjectcode         = sub2.subjectcode
                        JOIN   sections sec2          ON sc2.sectionid           = sec2.sectionid
                        JOIN   program_yearlevel pyl2 ON sec2.programyearlevelid = pyl2.programyearlevelid
                        JOIN   academic_offering ao2  ON pyl2.academicofferingid = ao2.academicofferingid
                        JOIN   sem_cte                ON sc2.semesterid          = sem_cte.semesterid
                        WHERE  {version_filter}
                          AND  pyl2.yearlevel          = %s
                          AND  ao2.offeringcode = (SELECT offeringcode FROM prog_cte)
                        GROUP BY UPPER(sub2.subjectcode)
                    )
                    SELECT mv.subjectcode,
                           COALESCE(SUM((ss.endtimeid - ss.starttimeid) * 0.5), 0) AS scheduled_hours,
                           CASE WHEN bool_or(sv.status = 'Published') THEN 'Published'
                                WHEN bool_or(sv.status = 'Draft')     THEN 'Draft'
                                ELSE NULL END AS saved_status
                    FROM   max_version mv
                    JOIN   curriculumsubject cs2  ON UPPER(cs2.subjectcode)    = mv.subjectcode
                    JOIN   schedule sc            ON sc.curriculumsubjectid    = cs2.curriculumsubjectid
                    JOIN   sections sec           ON sc.sectionid              = sec.sectionid
                    JOIN   program_yearlevel pyl  ON sec.programyearlevelid    = pyl.programyearlevelid
                    JOIN   academic_offering ao   ON pyl.academicofferingid    = ao.academicofferingid
                    JOIN   sem_cte                ON sc.semesterid             = sem_cte.semesterid
                    JOIN   schedule_version sv    ON sv.scheduleid             = sc.scheduleid
                                                 AND sv.version_number        = mv.max_v
                                                 AND {version_filter}
                    JOIN   schedule_sessions ss   ON ss.versionid              = sv.versionid
                    WHERE  pyl.yearlevel          = %s
                      AND  ao.offeringcode = (SELECT offeringcode FROM prog_cte)
                    GROUP BY mv.subjectcode
                """, (ay_id, sem, curr_id, int(yl), int(yl))) or []
                for r in sched_rows:
                    sched_map[r['subjectcode']] = {
                        'hours':  float(r['scheduled_hours'] or 0),
                        'status': r['saved_status'] or None,
                    }

                # PUB/DRAFT detection — skip in local mode (only official base matters there)
                if scheduler_mode != 'local':
                    dual_rows = query_db("""
                        WITH sem_cte AS (
                            SELECT semesterid FROM semester
                            WHERE academicyearid = %s AND semestertype = %s LIMIT 1
                        ),
                        prog_cte AS (
                            SELECT ao.offeringcode
                            FROM curriculum c
                            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
                            WHERE c.curriculumid = %s LIMIT 1
                        )
                        SELECT UPPER(sub.subjectcode) AS subjectcode
                        FROM   schedule_version sv
                        JOIN   schedule sc           ON sv.scheduleid          = sc.scheduleid
                        JOIN   curriculumsubject cs   ON sc.curriculumsubjectid = cs.curriculumsubjectid
                        JOIN   subject sub            ON cs.subjectcode         = sub.subjectcode
                        JOIN   sections sec           ON sc.sectionid           = sec.sectionid
                        JOIN   program_yearlevel pyl  ON sec.programyearlevelid = pyl.programyearlevelid
                        JOIN   academic_offering ao   ON pyl.academicofferingid = ao.academicofferingid
                        JOIN   sem_cte               ON sc.semesterid          = sem_cte.semesterid
                        WHERE  sv.status IN ('Draft', 'Published')
                          AND  pyl.yearlevel         = %s
                          AND  ao.offeringcode = (SELECT offeringcode FROM prog_cte)
                        GROUP BY UPPER(sub.subjectcode)
                        HAVING COUNT(DISTINCT sv.status) > 1
                    """, (ay_id, sem, curr_id, int(yl))) or []
                    for r in dual_rows:
                        sc_upper = r['subjectcode']
                        if sc_upper in sched_map:
                            sched_map[sc_upper]['status'] = 'Published/Draft'
            except Exception:
                pass

        result = []
        for s in subjects:
            sc = (s['subjectcode'] or '').upper()
            sm = sched_map.get(sc, {})
            result.append({
                'subjectcode':    s['subjectcode'],
                'subjectname':    s['subjectname'],
                'creditunits':    s['creditunits'],
                'laboratoryhours': s['laboratoryhours'],
                'total_hours':    s['total_hours'],
                'scheduled_hours': sm.get('hours', 0) if isinstance(sm, dict) else float(sm or 0),
                'saved_status':    sm.get('status')   if isinstance(sm, dict) else None,
            })
        return jsonify({"success": True, "subjects": result})
    finally:
        cur.close(); conn.close()
@app.route('/reports')
def reports():
    if 'loggedin' not in session: return redirect(url_for('login'))
    ay_list         = query_db("SELECT academicyearid, yearstart, yearend FROM academicyear ORDER BY yearstart DESC")
    programs        = query_db("SELECT programcode, programname FROM programs WHERE isactive = TRUE ORDER BY programname")
    faculty         = query_db("SELECT employeenumber, lastname || ', ' || firstname AS fullname FROM faculty ORDER BY lastname, firstname")
    curricula       = query_db("SELECT c.curriculumid, c.curriculumcode, c.curriculumyear, ao.offeringcode AS programcode FROM curriculum c JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid ORDER BY ao.offeringcode, c.curriculumyear DESC")
    emp_types       = query_db("SELECT employeetypeid, typename FROM employeetype ORDER BY typename")
    specializations = query_db("SELECT specializationid, specializationname FROM specialization ORDER BY specializationname")
    statuses        = query_db("SELECT DISTINCT employeestatus FROM faculty WHERE employeestatus IS NOT NULL ORDER BY employeestatus")
    buildings       = query_db("SELECT buildingid, buildingname FROM building WHERE isactive = TRUE ORDER BY buildingname")
    room_types      = query_db("SELECT DISTINCT roomtype FROM room WHERE roomtype IS NOT NULL ORDER BY roomtype")
    return render_template('academic/reports.html',
                           ay_list=ay_list, programs=programs,
                           faculty=faculty, curricula=curricula,
                           emp_types=emp_types, specializations=specializations,
                           statuses=statuses, buildings=buildings, room_types=room_types)

# ==============================================================================
# --- ADMIN SPECIFIC ROUTES ---
# ==============================================================================

@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        now = datetime.now().strftime("%B %d, %Y, %A")
        
        # --- 1. KPI Counts ---
        cur.execute("SELECT COUNT(*) as t FROM programs WHERE isactive = TRUE")
        prog_data = cur.fetchone()
        
        cur.execute("SELECT COUNT(*) as t FROM faculty WHERE employeestatus != 'Archive'")
        fac_data = cur.fetchone()
        
        cur.execute("SELECT COUNT(*) as t FROM room")
        room_data = cur.fetchone()
        
        cur.execute("SELECT COUNT(*) as t FROM building WHERE isactive = TRUE")
        bldg_data = cur.fetchone()

        # --- 2. Recent Activity Logs ---
        _ensure_activity_log_table()
        cur.execute("""
            SELECT TO_CHAR(logtime, 'YYYY-MM-DD HH24:MI:SS') as logtime,
                   action, details, initiated_by, log_color
            FROM activity_log
            ORDER BY logtime DESC LIMIT 6
        """)
        logs = cur.fetchall()

        # --- 3. Recent Schedule List ---
        cur.execute("""
            SELECT
                ao.offeringcode AS programcode,
                pyl.yearlevel,
                ay.yearstart || '-' || ay.yearend AS acad_year,
                sem.semestertype,
                TO_CHAR(MAX(sv.datecreated), 'MM/DD/YYYY') AS date_imported
            FROM schedule_version sv
            JOIN schedule s ON sv.scheduleid = s.scheduleid
            JOIN sections sec ON s.sectionid = sec.sectionid
            JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN academic_offering ao  ON pyl.academicofferingid = ao.academicofferingid
            JOIN semester sem          ON s.semesterid = sem.semesterid
            JOIN academicyear ay       ON sem.academicyearid = ay.academicyearid
            GROUP BY ao.offeringcode, pyl.yearlevel, ay.yearstart, ay.yearend, sem.semestertype
            ORDER BY MAX(sv.datecreated) DESC
            LIMIT 6
        """)
        scheds = cur.fetchall()

        return render_template('admin/dashboard_admin.html', 
                               current_date=now,
                               program_count=prog_data['t'] if prog_data else 0,
                               faculty_count=fac_data['t'] if fac_data else 0,
                               room_count=room_data['t'] if room_data else 0,
                               building_count=bldg_data['t'] if bldg_data else 0,
                               activity_logs=logs,
                               schedules=scheds)
    except Exception as e:
        print(f"Dashboard Database Error: {e}")
        return render_template('admin/dashboard_admin.html', 
                               current_date="N/A", program_count=0, faculty_count=0,
                               room_count=0, building_count=0, activity_logs=[], schedules=[])
    finally:
        cur.close()
        conn.close()

@app.route('/admin/employee/export/docx', methods=['POST'])
def admin_export_employees_docx():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    return _build_employee_docx()

@app.route('/employee/export/docx', methods=['POST'])
def export_employees_docx():
    if session.get('role') not in ('Admin', 'Academic Head'):
        return jsonify({'error': 'Unauthorized'}), 403
    return _build_employee_docx()

# ── Employee XLSX Export ──────────────────────────────────────────────────────
def _build_employee_xlsx():
    """Generate a styled XLSX file from POSTed employee JSON using openpyxl."""
    data      = request.get_json(silent=True) or {}
    employees = data.get('employees', [])
    title     = data.get('title', 'Employee Records')
    timestamp = data.get('timestamp', '')

    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Employees'

    has_contact = any(str(e.get('contact', '')).strip() for e in employees)
    headers = ['#', 'Employee Number', 'Employee Name', 'Specialization', 'Email']
    if has_contact: headers.append('Contact')
    headers += ['Employment Type', 'Status']
    ncols = len(headers)

    MAROON = 'FF800000'; WHITE = 'FFFFFFFF'
    thin   = Side(style='thin', color='FF888888')
    bdr    = Border(left=thin, right=thin, top=thin, bottom=thin)

    # ── Row 1: Title ──────────────────────────────────────────────────────────
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    c = ws.cell(row=1, column=1, value=title)
    c.font      = Font(bold=True, size=14, color=WHITE, name='Calibri')
    c.fill      = PatternFill('solid', fgColor=MAROON)
    c.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 30

    # ── Row 2: Subtitle ───────────────────────────────────────────────────────
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
    c = ws.cell(row=2, column=1, value=timestamp)
    c.font      = Font(size=9, color='FFDDDDDD', italic=True, name='Calibri')
    c.fill      = PatternFill('solid', fgColor=MAROON)
    c.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[2].height = 16

    # ── Row 3: Spacer ─────────────────────────────────────────────────────────
    ws.row_dimensions[3].height = 6

    # ── Row 4: Column headers ─────────────────────────────────────────────────
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=4, column=ci, value=h)
        c.font      = Font(bold=True, size=10, color=WHITE, name='Calibri')
        c.fill      = PatternFill('solid', fgColor=MAROON)
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border    = bdr
    ws.row_dimensions[4].height = 22
    ws.freeze_panes = 'A5'

    # ── Rows 5+: Data ─────────────────────────────────────────────────────────
    PINK  = PatternFill('solid', fgColor='FFFFF0F0')
    WHITE_FILL = PatternFill('solid', fgColor='FFFFFFFF')
    data_font = Font(size=9, name='Calibri')

    for ri, emp in enumerate(employees):
        rn   = ri + 5
        vals = [ri + 1, emp.get('emp_num',''), emp.get('name',''),
                emp.get('spec',''), emp.get('email','')]
        if has_contact: vals.append(emp.get('contact',''))
        vals += [emp.get('type',''), emp.get('status','')]
        fill = PINK if ri % 2 == 1 else WHITE_FILL
        for ci, val in enumerate(vals, 1):
            c = ws.cell(row=rn, column=ci, value=val)
            c.font = data_font; c.fill = fill; c.border = bdr
            c.alignment = Alignment(vertical='center')
        ws.row_dimensions[rn].height = 16

    # ── Column widths ─────────────────────────────────────────────────────────
    widths = [5, 20, 28, 26, 32]
    if has_contact: widths.append(18)
    widths += [20, 14]
    for ci, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(ci)].width = w

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename="employees.xlsx"'}
    )

@app.route('/admin/employee/export/xlsx', methods=['POST'])
def admin_export_employees_xlsx():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    return _build_employee_xlsx()

@app.route('/employee/export/xlsx', methods=['POST'])
def export_employees_xlsx():
    if session.get('role') not in ('Admin', 'Academic Head'):
        return jsonify({'error': 'Unauthorized'}), 403
    return _build_employee_xlsx()

# ── Archived Employee DOCX Export ─────────────────────────────────────────────
def _build_archived_employee_docx():
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    data      = request.get_json(silent=True) or {}
    employees = data.get('employees', [])
    title     = data.get('title', 'Archived Employee Records')
    timestamp = data.get('timestamp', '')

    headers = ['#', 'Employee Name', 'Specialization', 'Email', 'Contact', 'Employment Type', 'Status', 'Date Archived']

    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = Inches(0.8); sec.right_margin = Inches(0.8)
    sec.top_margin  = Inches(0.8); sec.bottom_margin = Inches(0.8)

    h = doc.add_heading(title, 0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in h.runs:
        run.font.color.rgb = RGBColor(0x80, 0x00, 0x00)

    if timestamp:
        p = doc.add_paragraph(timestamp)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in p.runs:
            run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
            run.font.size = Pt(9)

    doc.add_paragraph()

    table = doc.add_table(rows=1, cols=len(headers))
    table.style = 'Table Grid'

    def _shd(cell, hex_color):
        tcPr = cell._tc.get_or_add_tcPr()
        shd  = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear'); shd.set(qn('w:color'), 'auto')
        shd.set(qn('w:fill'), hex_color); tcPr.append(shd)

    for i, h_text in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = h_text
        for para in cell.paragraphs:
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in para.runs:
                run.bold = True; run.font.size = Pt(9)
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        _shd(cell, '800000')

    for idx, emp in enumerate(employees):
        row = table.add_row()
        vals = [str(idx + 1), emp.get('name', ''), emp.get('spec', ''),
                emp.get('email', ''), emp.get('contact', ''),
                emp.get('type', ''), emp.get('status', ''), emp.get('date_archived', '')]
        for j, val in enumerate(vals):
            cell = row.cells[j]
            cell.text = val
            for para in cell.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(9)

    buf = io.BytesIO()
    doc.save(buf); buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        headers={'Content-Disposition': 'attachment; filename="archived_employees.docx"'}
    )

@app.route('/admin/archived-employee/export/docx', methods=['POST'])
def admin_export_archived_employees_docx():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    return _build_archived_employee_docx()

@app.route('/archived-employee/export/docx', methods=['POST'])
def export_archived_employees_docx():
    if session.get('role') not in ('Admin', 'Academic Head'):
        return jsonify({'error': 'Unauthorized'}), 403
    return _build_archived_employee_docx()

# ── Archived Employee XLSX Export ─────────────────────────────────────────────
def _build_archived_employee_xlsx():
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    data      = request.get_json(silent=True) or {}
    employees = data.get('employees', [])
    title     = data.get('title', 'Archived Employee Records')
    timestamp = data.get('timestamp', '')

    headers = ['#', 'Employee Name', 'Specialization', 'Email', 'Contact', 'Employment Type', 'Status', 'Date Archived']
    ncols   = len(headers)

    MAROON = 'FF800000'; WHITE = 'FFFFFFFF'
    thin   = Side(style='thin', color='FF888888')
    bdr    = Border(left=thin, right=thin, top=thin, bottom=thin)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Archived Employees'

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    c = ws.cell(row=1, column=1, value=title)
    c.font      = Font(bold=True, size=14, color=WHITE, name='Calibri')
    c.fill      = PatternFill('solid', fgColor=MAROON)
    c.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 30

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
    c = ws.cell(row=2, column=1, value=timestamp)
    c.font      = Font(size=9, color='FFDDDDDD', italic=True, name='Calibri')
    c.fill      = PatternFill('solid', fgColor=MAROON)
    c.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[2].height = 16

    ws.row_dimensions[3].height = 6

    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=4, column=ci, value=h)
        c.font      = Font(bold=True, size=10, color=WHITE, name='Calibri')
        c.fill      = PatternFill('solid', fgColor=MAROON)
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border    = bdr
    ws.row_dimensions[4].height = 22
    ws.freeze_panes = 'A5'

    PINK       = PatternFill('solid', fgColor='FFFFF0F0')
    WHITE_FILL = PatternFill('solid', fgColor='FFFFFFFF')
    data_font  = Font(size=9, name='Calibri')

    for ri, emp in enumerate(employees):
        rn   = ri + 5
        vals = [ri + 1, emp.get('name', ''), emp.get('spec', ''),
                emp.get('email', ''), emp.get('contact', ''),
                emp.get('type', ''), emp.get('status', ''), emp.get('date_archived', '')]
        fill = PINK if ri % 2 == 1 else WHITE_FILL
        for ci, val in enumerate(vals, 1):
            c = ws.cell(row=rn, column=ci, value=val)
            c.font = data_font; c.fill = fill; c.border = bdr
            c.alignment = Alignment(vertical='center')
        ws.row_dimensions[rn].height = 16

    for ci, w in enumerate([5, 28, 26, 32, 18, 20, 14, 16], 1):
        ws.column_dimensions[get_column_letter(ci)].width = w

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename="archived_employees.xlsx"'}
    )

@app.route('/admin/archived-employee/export/xlsx', methods=['POST'])
def admin_export_archived_employees_xlsx():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    return _build_archived_employee_xlsx()

@app.route('/archived-employee/export/xlsx', methods=['POST'])
def export_archived_employees_xlsx():
    if session.get('role') not in ('Admin', 'Academic Head'):
        return jsonify({'error': 'Unauthorized'}), 403
    return _build_archived_employee_xlsx()

# ── Curriculum Export List & Data ────────────────────────────────────────────
@app.route('/admin/curriculum/export/list', methods=['GET'])
def admin_curriculum_export_list():
    if session.get('role') not in ('Admin', 'Academic Head'):
        return jsonify({'error': 'Unauthorized'}), 403
    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT p.ProgramCode as program_code, p.ProgramName as program_name,
               c.CurriculumID as curriculum_id, c.CurriculumCode as curriculum_code,
               c.CurriculumYear as curriculum_year
        FROM Programs p
        LEFT JOIN academic_offering ao ON ao.programcode = p.programcode
        LEFT JOIN Curriculum c ON c.academicofferingid = ao.academicofferingid
        WHERE p.IsActive = TRUE
        ORDER BY p.ProgramName ASC, c.CurriculumYear DESC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    from collections import OrderedDict
    programs = OrderedDict()
    for row in rows:
        pc = row['program_code']
        if pc not in programs:
            programs[pc] = {'program_code': pc, 'program_name': row['program_name'], 'curricula': []}
        if row['curriculum_id']:
            programs[pc]['curricula'].append({
                'curriculum_id':   row['curriculum_id'],
                'curriculum_code': row['curriculum_code'],
                'curriculum_year': row['curriculum_year'],
            })
    return jsonify([p for p in programs.values() if p['curricula']])

@app.route('/admin/curriculum/export/data', methods=['POST'])
def admin_curriculum_export_data():
    if session.get('role') not in ('Admin', 'Academic Head'):
        return jsonify({'error': 'Unauthorized'}), 403
    payload        = request.get_json(silent=True) or {}
    curriculum_ids = payload.get('curriculum_ids', [])
    if not curriculum_ids:
        return jsonify({'error': 'No curriculum IDs provided'}), 400
    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT c.curriculumid as curriculum_id, c.curriculumcode as curriculum_code,
               c.curriculumyear as curriculum_year, p.programname as program_name,
               ao.offeringcode as program_code
        FROM curriculum c
        JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
        JOIN programs p ON ao.programcode = p.programcode
        WHERE c.curriculumid = ANY(%s)
    """, (curriculum_ids,))
    info_map = {r['curriculum_id']: dict(r) for r in cur.fetchall()}
    cur.execute("""
        SELECT cv.CurriculumID as curriculum_id,
               CAST(SUBSTRING(cv.ProgramYearLevel FROM '-(.*)') AS INT) as year_level,
               cv.Semester as semester,
               cv.SubjectCode as subject_code,
               COALESCE(s.SubjectName, cv."Description", '') as subject_name,
               COALESCE(cv.LectureHours, 0)    as lecture_hours,
               COALESCE(cv.LaboratoryHours, 0) as lab_hours,
               COALESCE(cv.CreditUnits, 0)     as credit_units,
               COALESCE(cv.TuitionHours, 0)    as tuition_hours,
               COALESCE(cv."Prerequisite", '')  as prerequisite,
               COALESCE(cv."Co-requisite", '')  as corequisite
        FROM Curriculum_View cv
        LEFT JOIN Subject s ON cv.SubjectCode = s.SubjectCode
        WHERE cv.CurriculumID = ANY(%s)
        ORDER BY cv.CurriculumID,
                 CAST(SUBSTRING(cv.ProgramYearLevel FROM '-(.*)') AS INT),
                 cv.Semester, cv.SubjectCode
    """, (curriculum_ids,))
    subject_rows = cur.fetchall()
    cur.close(); conn.close()
    SEM_LABELS = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}
    YL_LABELS  = {1: '1st Year', 2: '2nd Year', 3: '3rd Year', 4: '4th Year', 5: '5th Year'}
    from collections import defaultdict
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in subject_rows:
        grouped[row['curriculum_id']][row['year_level']][row['semester']].append({
            'subject_code':  row['subject_code']   or '',
            'subject_name':  row['subject_name']   or '',
            'lecture_hours': int(row['lecture_hours']  or 0),
            'lab_hours':     int(row['lab_hours']      or 0),
            'credit_units':  int(row['credit_units']   or 0),
            'tuition_hours': int(row['tuition_hours']  or 0),
            'prerequisite':  row['prerequisite']   or '',
            'corequisite':   row['corequisite']    or '',
        })
    result = []
    for cid in curriculum_ids:
        if cid not in info_map:
            continue
        info    = info_map[cid]
        yl_data = []
        for yl in sorted(grouped[cid].keys()):
            sems = []
            for sem in sorted(grouped[cid][yl].keys()):
                subjs = grouped[cid][yl][sem]
                sems.append({
                    'semester_code': sem,
                    'label':         SEM_LABELS.get(sem, sem),
                    'subjects':      subjs,
                    'total_units':   sum(s['credit_units']  for s in subjs),
                    'total_tuition': sum(s['tuition_hours'] for s in subjs),
                })
            yl_data.append({
                'year_level': yl,
                'label':      YL_LABELS.get(yl, f'Year {yl}'),
                'semesters':  sems,
            })
        result.append({
            'curriculum_id':   cid,
            'curriculum_code': info['curriculum_code'],
            'curriculum_year': info['curriculum_year'],
            'program_name':    info['program_name'],
            'program_code':    info['program_code'],
            'year_levels':     yl_data,
        })
    return jsonify(result)

# ── Curriculum DOCX Export ────────────────────────────────────────────────────
def _build_curriculum_docx():
    """Generate a detailed DOCX export grouped by curriculum → year level → semester."""
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    payload   = request.get_json(silent=True) or {}
    curricula = payload.get('curricula', [])
    timestamp = payload.get('timestamp', '')

    HEADERS    = ['Subject Code', 'Prereq', 'Co-req', 'Description', 'Lec Hrs', 'Lab Hrs', 'Credited Units', 'Tuition Hrs']
    KEYS       = ['subject_code', 'prerequisite', 'corequisite', 'subject_name', 'lecture_hours', 'lab_hours', 'credit_units', 'tuition_hours']
    COL_WIDTHS = [Inches(0.9), Inches(0.9), Inches(0.7), Inches(2.5), Inches(0.5), Inches(0.5), Inches(0.5), Inches(0.5)]

    def _shd(cell, hex_color):
        tcPr = cell._tc.get_or_add_tcPr()
        shd  = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear'); shd.set(qn('w:color'), 'auto'); shd.set(qn('w:fill'), hex_color)
        tcPr.append(shd)

    def _shade_para(para, fill_hex):
        pPr = para._p.get_or_add_pPr()
        shd = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear'); shd.set(qn('w:color'), 'auto'); shd.set(qn('w:fill'), fill_hex)
        pPr.append(shd)

    def _sr(run, bold=False, size=9, color=None, italic=False):
        run.bold = bold; run.italic = italic
        run.font.size = Pt(size); run.font.name = 'Calibri'
        if color: run.font.color.rgb = RGBColor(*color)

    doc = Document()
    for sec in doc.sections:
        sec.top_margin = sec.bottom_margin = Inches(0.6)
        sec.left_margin = sec.right_margin = Inches(0.6)

    first_curr = True
    for curr in curricula:
        if not first_curr:
            doc.add_page_break()
        first_curr = False

        h = doc.add_paragraph(); h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = h.add_run(f"{curr.get('curriculum_code','')}  —  {curr.get('program_name','')}")
        _sr(r, bold=True, size=16, color=(128, 0, 0))

        h2 = doc.add_paragraph(); h2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r2 = h2.add_run(f"Curriculum Year: {curr.get('curriculum_year','')}   |   {timestamp}")
        _sr(r2, size=9, color=(100, 100, 100), italic=True)
        doc.add_paragraph()

        for yl_data in curr.get('year_levels', []):
            yh = doc.add_paragraph()
            yh.paragraph_format.space_before = Pt(10)
            yh.paragraph_format.space_after  = Pt(2)
            _shade_para(yh, 'E8DEDE')
            _sr(yh.add_run('  ' + yl_data['label'].upper() + '  '), bold=True, size=13, color=(80, 0, 0))

            for sem_data in yl_data.get('semesters', []):
                sh = doc.add_paragraph()
                sh.paragraph_format.space_before = Pt(4)
                sh.paragraph_format.space_after  = Pt(2)
                _sr(sh.add_run(sem_data['label']), bold=True, size=10, color=(128, 0, 0))

                tbl = doc.add_table(rows=1, cols=len(HEADERS))
                tbl.style = 'Table Grid'
                for ci, (cell, hdr) in enumerate(zip(tbl.rows[0].cells, HEADERS)):
                    cell.text = hdr; _shd(cell, '800000')
                    _sr(cell.paragraphs[0].runs[0], bold=True, size=9, color=(255, 255, 255))
                    cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                    cell.width = COL_WIDTHS[ci]

                subjs = sem_data.get('subjects', [])
                for ri, subj in enumerate(subjs):
                    fill = 'FFF0F0' if ri % 2 == 1 else 'FFFFFF'
                    row_cells = tbl.add_row().cells
                    for ci, (cell, key) in enumerate(zip(row_cells, KEYS)):
                        val = subj.get(key)
                        cell.text = '' if val is None else str(val)
                        _shd(cell, fill)
                        if cell.paragraphs[0].runs:
                            _sr(cell.paragraphs[0].runs[0], size=8)
                        cell.width = COL_WIDTHS[ci]

                if subjs:
                    tot_cells = tbl.add_row().cells
                    for ci in range(len(tot_cells)):
                        _shd(tot_cells[ci], 'F0F0F0')
                        if ci < len(COL_WIDTHS): tot_cells[ci].width = COL_WIDTHS[ci]
                    # Merge cols 0-3 (Subject Code through Description) for the label
                    merged = tot_cells[0].merge(tot_cells[3])
                    merged.text = 'TOTAL UNITS'
                    if merged.paragraphs[0].runs:
                        _sr(merged.paragraphs[0].runs[0], bold=True, size=9)
                    merged.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
                    # Credited Units column (index 6)
                    tot_cells[6].text = str(sem_data.get('total_units', 0))
                    if tot_cells[6].paragraphs[0].runs:
                        _sr(tot_cells[6].paragraphs[0].runs[0], bold=True, size=10, color=(128, 0, 0))
                    tot_cells[6].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                    # Tuition Hours column (index 7)
                    tot_cells[7].text = str(sem_data.get('total_tuition', 0))
                    if tot_cells[7].paragraphs[0].runs:
                        _sr(tot_cells[7].paragraphs[0].runs[0], bold=True, size=10, color=(128, 0, 0))
                    tot_cells[7].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

                doc.add_paragraph()

    buf = io.BytesIO(); doc.save(buf); buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        headers={'Content-Disposition': 'attachment; filename="curriculum.docx"'}
    )

@app.route('/admin/curriculum/export/docx', methods=['POST'])
def admin_export_curriculum_docx():
    if session.get('role') not in ('Admin', 'Academic Head'):
        return jsonify({'error': 'Unauthorized'}), 403
    return _build_curriculum_docx()

# ── Curriculum XLSX Export ────────────────────────────────────────────────────
def _build_curriculum_xlsx():
    """Generate a detailed XLSX export — one sheet per curriculum, grouped by year/semester."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    payload   = request.get_json(silent=True) or {}
    curricula = payload.get('curricula', [])
    title     = payload.get('title', 'Curriculum Export')
    timestamp = payload.get('timestamp', '')

    HEADERS    = ['Subject Code', 'Prereq', 'Co-req', 'Description', 'Lec Hrs', 'Lab Hrs', 'Credited Units', 'Tuition Hrs']
    KEYS       = ['subject_code', 'prerequisite', 'corequisite', 'subject_name', 'lecture_hours', 'lab_hours', 'credit_units', 'tuition_hours']
    COL_WIDTHS = [16, 18, 16, 42, 8, 8, 14, 12]
    NCOLS      = len(HEADERS)
    MAROON     = 'FF800000'; WHITE = 'FFFFFFFF'
    thin       = Side(style='thin', color='FFCCCCCC')
    bdr        = Border(left=thin, right=thin, top=thin, bottom=thin)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    for curr in curricula:
        sheet_name = (curr.get('curriculum_code') or 'Sheet')[:31]
        ws = wb.create_sheet(title=sheet_name)

        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=NCOLS)
        c = ws.cell(row=1, column=1, value=title)
        c.font = Font(bold=True, size=14, color=WHITE, name='Calibri')
        c.fill = PatternFill('solid', fgColor=MAROON)
        c.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[1].height = 28

        curr_label = f"{curr.get('curriculum_code','')}  |  {curr.get('program_name','')}  |  C.Y {curr.get('curriculum_year','')}"
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=NCOLS)
        c = ws.cell(row=2, column=1, value=curr_label)
        c.font = Font(bold=True, size=11, color=WHITE, name='Calibri')
        c.fill = PatternFill('solid', fgColor=MAROON)
        c.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[2].height = 20

        ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=NCOLS)
        c = ws.cell(row=3, column=1, value=timestamp)
        c.font = Font(size=8, color='FFDDDDDD', italic=True, name='Calibri')
        c.fill = PatternFill('solid', fgColor=MAROON)
        c.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[3].height = 14

        rn = 4
        for yl_data in curr.get('year_levels', []):
            ws.row_dimensions[rn].height = 8; rn += 1

            ws.merge_cells(start_row=rn, start_column=1, end_row=rn, end_column=NCOLS)
            c = ws.cell(row=rn, column=1, value=yl_data['label'].upper())
            c.font = Font(bold=True, size=11, color=WHITE, name='Calibri')
            c.fill = PatternFill('solid', fgColor='FF333333')
            c.alignment = Alignment(horizontal='left', vertical='center', indent=1)
            ws.row_dimensions[rn].height = 20; rn += 1

            for sem_data in yl_data.get('semesters', []):
                ws.merge_cells(start_row=rn, start_column=1, end_row=rn, end_column=NCOLS)
                c = ws.cell(row=rn, column=1, value=f"  {sem_data['label']}")
                c.font = Font(bold=True, size=10, color=MAROON, name='Calibri')
                c.fill = PatternFill('solid', fgColor='FFFFF5F5')
                c.alignment = Alignment(horizontal='left', vertical='center')
                ws.row_dimensions[rn].height = 17; rn += 1

                for ci, h in enumerate(HEADERS, 1):
                    c = ws.cell(row=rn, column=ci, value=h)
                    c.font = Font(bold=True, size=9, color=WHITE, name='Calibri')
                    c.fill = PatternFill('solid', fgColor=MAROON)
                    c.alignment = Alignment(horizontal='center', vertical='center')
                    c.border = bdr
                ws.row_dimensions[rn].height = 18; rn += 1

                subjs = sem_data.get('subjects', [])
                for si, subj in enumerate(subjs):
                    fill = PatternFill('solid', fgColor='FFFFF0F0') if si % 2 == 1 else PatternFill('solid', fgColor='FFFFFFFF')
                    vals = [subj.get(k, '') for k in KEYS]
                    for ci, val in enumerate(vals, 1):
                        c = ws.cell(row=rn, column=ci, value=val)
                        c.font = Font(size=9, name='Calibri')
                        c.fill = fill; c.border = bdr
                        c.alignment = Alignment(vertical='center', wrap_text=(ci == 4))
                    ws.row_dimensions[rn].height = 15; rn += 1

                if subjs:
                    fill_tot  = PatternFill('solid', fgColor='FFE8E8E8')
                    total_tu  = sem_data.get('total_tuition', 0)
                    # cols: [SubjCode, Prereq, Coreq, Description, Lec, Lab, CreditedUnits, TuitionHrs]
                    #        1         2       3      4             5    6    7               8
                    tot_vals  = ['', '', '', 'TOTAL UNITS', '', '', sem_data.get('total_units', 0), total_tu]
                    for ci, val in enumerate(tot_vals, 1):
                        c = ws.cell(row=rn, column=ci, value=val)
                        c.fill = fill_tot; c.border = bdr
                        if ci == 4:   # Description col — label
                            c.font = Font(bold=True, size=9, name='Calibri')
                            c.alignment = Alignment(horizontal='right', vertical='center')
                        elif ci in (7, 8):   # Credited Units & Tuition Hrs totals
                            c.font = Font(bold=True, size=10, color=MAROON, name='Calibri')
                            c.alignment = Alignment(horizontal='center', vertical='center')
                        else:
                            c.font = Font(size=9, name='Calibri')
                    ws.row_dimensions[rn].height = 17; rn += 1

                ws.row_dimensions[rn].height = 5; rn += 1

        for ci, w in enumerate(COL_WIDTHS, 1):
            ws.column_dimensions[get_column_letter(ci)].width = w
        ws.freeze_panes = 'A4'

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename="curriculum.xlsx"'}
    )

@app.route('/admin/curriculum/export/xlsx', methods=['POST'])
def admin_export_curriculum_xlsx():
    if session.get('role') not in ('Admin', 'Academic Head'):
        return jsonify({'error': 'Unauthorized'}), 403
    return _build_curriculum_xlsx()

# ─────────────────────────────────────────────────────────────────────────────

@app.route('/admin/employee')
def admin_employee():
    if session.get('role') != 'Admin': return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor) 

    cur.execute("""
        SELECT 
            f.EmployeeNumber, f.FirstName, f.MiddleName, f.LastName, f.Email, 
            f.ContactNumber, f.EmployeeStatus, f.SpecializationID, f.EmployeeTypeID, f.DesignationID,
            s.SpecializationName, et.TypeName, d.DesignationName
        FROM Faculty f
        LEFT JOIN Specialization s ON f.SpecializationID = s.SpecializationID
        LEFT JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID
        LEFT JOIN Designation d ON f.DesignationID = d.DesignationID
        ORDER BY f.LastName, f.FirstName
    """)
    employees = cur.fetchall()

    cur.execute("""
        SELECT et.TypeName, COUNT(f.EmployeeNumber) as count
        FROM EmployeeType et
        LEFT JOIN Faculty f ON et.EmployeeTypeID = f.EmployeeTypeID
        GROUP BY et.TypeName
    """)
    counts = {row['typename']: row['count'] for row in cur.fetchall()}

    specializations = query_db("SELECT * FROM Specialization ORDER BY SpecializationName")
    employee_types = query_db("SELECT * FROM EmployeeType ORDER BY TypeName")
    designations = query_db("SELECT * FROM Designation ORDER BY DesignationName")

    cur.close()
    conn.close()

    return render_template(
        'admin/employee_admin.html',
        employees=employees,
        specializations=specializations,
        employee_types=employee_types,
        designations=designations,
        reg=counts.get('Regular', 0),
        pt=counts.get('Part-time', 0),
        des=counts.get('Designee', 0),
        total=len(employees)
    )

@app.route('/admin/add_employee', methods=['POST'])
def admin_add_employee():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    if request.method == 'POST':
        emp_num = request.form['employee_number']
        f_name = request.form['first_name']
        m_name = request.form.get('middle_name')
        l_name = request.form['last_name']
        email = request.form['email']
        contact = request.form['contact']
        spec_id = request.form['specialization_id']
        type_id = request.form['type_id']
        status = request.form['status']
        desig_id = request.form.get('designation_id')
        if not desig_id or desig_id == '': desig_id = None
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        try:
            role = 'Faculty' 
            if desig_id:
                cur.execute("SELECT DesignationName FROM Designation WHERE DesignationID = %s", (desig_id,))
                designation = cur.fetchone()
                if designation and designation['designationname'] == 'Academic Head':
                    role = 'Academic Head'

            cur.execute("INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (emp_num, f_name, m_name, l_name, email, contact, spec_id, type_id, desig_id, status))
            
            hashed_password = generate_password_hash(emp_num)
            cur.execute("""
                INSERT INTO Accounts (Username, PasswordHash, Role, IsActive, EmployeeNumber)
                VALUES (%s, %s, %s, TRUE, %s)
                ON CONFLICT (Username) DO NOTHING
            """, (emp_num, hashed_password, role, emp_num))
           
            conn.commit()
            flash("Employee added successfully and account created!", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error adding employee: {e}", "error")
        finally:
            cur.close()
            conn.close()
        return redirect(url_for('admin_employee'))

@app.route('/admin/edit_employee', methods=['POST'])
def admin_edit_employee():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    if request.method == 'POST':
        emp_num = request.form['employee_number']
        f_name = request.form['first_name']
        m_name = request.form.get('middle_name')
        l_name = request.form['last_name']
        email = request.form['email']
        contact = request.form['contact']
        spec_id = request.form['specialization_id']
        type_id = request.form['type_id']
        status = request.form['status']
        desig_id = request.form.get('designation_id')
        if not desig_id or desig_id == '': desig_id = None
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        try:
            cur.execute("""
                UPDATE Faculty 
                SET FirstName=%s, MiddleName=%s, LastName=%s, Email=%s, ContactNumber=%s, 
                    SpecializationID=%s, EmployeeTypeID=%s, DesignationID=%s, EmployeeStatus=%s 
                WHERE EmployeeNumber=%s
            """, (f_name, m_name, l_name, email, contact, spec_id, type_id, desig_id, status, emp_num))

            role = 'Faculty' 
            if desig_id:
                cur.execute("SELECT DesignationName FROM Designation WHERE DesignationID = %s", (desig_id,))
                designation = cur.fetchone()
                if designation and designation['designationname'] == 'Academic Head':
                    role = 'Academic Head'
            
            cur.execute("UPDATE Accounts SET Role = %s WHERE Username = %s", (role, emp_num))
            
            conn.commit()
            flash("Employee details updated successfully!", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error updating employee: {e}", "error")
        finally:
            cur.close()
            conn.close()
        return redirect(url_for('admin_employee'))

@app.route('/admin/archive_employee/<emp_num>')
def admin_archive_employee(emp_num):
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM Schedule WHERE EmployeeNumber = %s", (emp_num,))
        if cur.fetchone():
            flash(f"Cannot archive Employee {emp_num}. They are currently assigned to an active schedule.", "error")
            return redirect(url_for('admin_employee'))

        cur.execute("""
            INSERT INTO Faculty_Archive (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty WHERE EmployeeNumber = %s
        """, (emp_num,))

        cur.execute("UPDATE Accounts SET IsActive = FALSE, EmployeeNumber = NULL WHERE EmployeeNumber = %s", (emp_num,))
        cur.execute("DELETE FROM Faculty WHERE EmployeeNumber = %s", (emp_num,))
        
        conn.commit()
        flash(f"Employee {emp_num} was archived successfully and their account has been deactivated.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error archiving employee: {str(e)}", "error")
    finally:
        cur.close()
        conn.close()
        
    return redirect(url_for('admin_employee'))

@app.route('/admin/bulk_archive', methods=['POST'])
def admin_bulk_archive():
    if session.get('role') != 'Admin': return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    emp_ids = data.get('employee_ids',[])
    if not emp_ids: return jsonify({'error': 'No employees selected'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        placeholders = ', '.join(['%s'] * len(emp_ids))
        
        cur.execute(f"SELECT EmployeeNumber FROM Schedule WHERE EmployeeNumber IN ({placeholders})", tuple(emp_ids))
        conflicts =[row[0] for row in cur.fetchall()]
        if conflicts:
            return jsonify({'error': f"Cannot archive. The following are in a schedule: {', '.join(conflicts)}"}), 409

        cur.execute(f"""
            INSERT INTO Faculty_Archive (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty WHERE EmployeeNumber IN ({placeholders})
        """, tuple(emp_ids))

        cur.execute(f"UPDATE Accounts SET IsActive = FALSE, EmployeeNumber = NULL WHERE EmployeeNumber IN ({placeholders})", tuple(emp_ids))
        cur.execute(f"DELETE FROM Faculty WHERE EmployeeNumber IN ({placeholders})", tuple(emp_ids))

        conn.commit()
        return jsonify({'success': f'{len(emp_ids)} employees archived and their accounts deactivated.'})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()

# --- FIXED: ADDED TUITION HOURS (th) TO ADMIN CURRICULUM IMPORT ---
@app.route('/admin/bulk_import', methods=['POST'])
def admin_bulk_import():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    if 'file' not in request.files:
        flash("No file selected.", "error")
        return redirect(url_for('admin_employee'))

    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        flash("Invalid file format. Please upload a .csv file.", "error")
        return redirect(url_for('admin_employee'))

    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    csv_input = csv.reader(stream)
    next(csv_input)

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Build name → ID lookup dicts so the CSV can use either IDs or names
        cur.execute("SELECT SpecializationID, SpecializationName FROM Specialization")
        spec_map  = {r['specializationname'].strip().lower(): r['specializationid']
                     for r in cur.fetchall()}
        cur.execute("SELECT EmployeeTypeID, TypeName FROM EmployeeType")
        etype_map = {r['typename'].strip().lower(): r['employeetypeid']
                     for r in cur.fetchall()}
        cur.execute("SELECT DesignationID, DesignationName FROM Designation")
        desig_map = {r['designationname'].strip().lower(): r['designationid']
                     for r in cur.fetchall()}

        def _fuzzy_lookup(key, name_map):
            if key in name_map: return name_map[key]
            m = {k: v for k, v in name_map.items() if k.startswith(key)}
            if len(m) == 1: return next(iter(m.values()))
            m = {k: v for k, v in name_map.items() if key.startswith(k)}
            if len(m) == 1: return next(iter(m.values()))
            m = {k: v for k, v in name_map.items() if key in k}
            if len(m) == 1: return next(iter(m.values()))
            return None

        def resolve_etype(val):
            if not val or val.strip().upper() in ('NULL', 'N/A', '-', ''):
                return None
            try:
                return int(val)
            except ValueError:
                result = _fuzzy_lookup(val.strip().lower(), etype_map)
                if result is not None: return result
                avail = ', '.join(f'"{n}"' for n in sorted(etype_map.keys()))
                raise ValueError(f"'{val}' is not a recognised Employee Type. Available: {avail}")

        def get_or_create_spec(val):
            if not val or val.strip().upper() in ('NULL', 'N/A', '-', ''):
                return None
            try:
                return int(val)
            except ValueError:
                name = val.strip()
                key = name.lower()
                result = _fuzzy_lookup(key, spec_map)
                if result is not None: return result
                cur.execute(
                    "INSERT INTO Specialization (SpecializationName, IsActive) VALUES (%s, TRUE) ON CONFLICT (SpecializationName) DO NOTHING",
                    (name,)
                )
                cur.execute(
                    "SELECT SpecializationID FROM Specialization WHERE LOWER(SpecializationName) = LOWER(%s)",
                    (name,)
                )
                row = cur.fetchone()
                new_id = row['specializationid']
                spec_map[key] = new_id
                return new_id

        def get_or_create_desig(val):
            if not val or val.strip().upper() in ('NULL', 'N/A', '-', ''):
                return None
            try:
                return int(val)
            except ValueError:
                name = val.strip()
                key = name.lower()
                result = _fuzzy_lookup(key, desig_map)
                if result is not None: return result
                cur.execute(
                    "INSERT INTO Designation (DesignationName) VALUES (%s) ON CONFLICT (DesignationName) DO NOTHING",
                    (name,)
                )
                cur.execute(
                    "SELECT DesignationID FROM Designation WHERE LOWER(DesignationName) = LOWER(%s)",
                    (name,)
                )
                row = cur.fetchone()
                new_id = row['designationid']
                desig_map[key] = new_id
                return new_id

        for i, row in enumerate(csv_input, 2):
            if len(row) < 10: continue
            try:
                emp_num, last_name, first_name, middle_name, email, contact, \
                    spec_raw, etype_raw, status, desig_raw = [r.strip() for r in row[:10]]

                spec_id  = get_or_create_spec(spec_raw)
                etype_id = resolve_etype(etype_raw)
                desig_id = get_or_create_desig(desig_raw)

                # Normalise status to exact DB-accepted values (case-insensitive)
                status_norm = status.lower().replace('-', '').replace(' ', '')
                if status_norm == 'parttime':
                    status = 'Part-Time'
                elif status_norm == 'permanent':
                    status = 'Permanent'
                elif status_norm == 'temporary':
                    status = 'Temporary'

                cur.execute("""
                    INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName,
                        Email, ContactNumber, SpecializationID, EmployeeTypeID,
                        DesignationID, EmployeeStatus)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (EmployeeNumber) DO NOTHING
                """, (emp_num, first_name, middle_name or None, last_name,
                      email or None, contact or None,
                      spec_id, etype_id, desig_id, status))

                # Determine account role from designation name
                role = 'Faculty'
                if desig_id:
                    cur.execute(
                        "SELECT DesignationName FROM Designation WHERE DesignationID = %s",
                        (desig_id,)
                    )
                    desig_row = cur.fetchone()
                    if desig_row and desig_row['designationname'] == 'Academic Head':
                        role = 'Academic Head'

                hashed_password = generate_password_hash(emp_num)
                cur.execute("""
                    INSERT INTO Accounts (Username, PasswordHash, Role, IsActive, EmployeeNumber)
                    VALUES (%s, %s, %s, TRUE, %s)
                    ON CONFLICT (Username) DO NOTHING
                """, (emp_num, hashed_password, role, emp_num))

            except ValueError as ve:
                conn.rollback()
                flash(f"Import Error at row {i}: {ve}", "error")
                return redirect(url_for('admin_employee'))

        conn.commit()
        flash("Bulk import completed successfully. New employees have been given accounts.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"An error occurred during bulk import: {e}", "error")
    finally:
        cur.close()
        conn.close()

    return redirect(url_for('admin_employee'))

# ── Shared helper: insert a list of employee dicts using RealDictCursor ──────
def _admin_insert_employees(rows, conn, cur):
    """
    rows: list of dicts with keys emp_num, last_name, first_name, middle_name,
          email, contact, specialization, emp_type, status, designation.
    Returns list of error strings (empty = all OK).
    """
    cur.execute("SELECT SpecializationID, SpecializationName FROM Specialization")
    spec_map  = {r['specializationname'].strip().lower(): r['specializationid'] for r in cur.fetchall()}
    cur.execute("SELECT EmployeeTypeID, TypeName FROM EmployeeType")
    etype_map = {r['typename'].strip().lower(): r['employeetypeid'] for r in cur.fetchall()}
    cur.execute("SELECT DesignationID, DesignationName FROM Designation")
    desig_map = {r['designationname'].strip().lower(): r['designationid'] for r in cur.fetchall()}

    def _fuzzy(key, nm):
        if key in nm: return nm[key]
        m = {k: v for k, v in nm.items() if k.startswith(key)}
        if len(m) == 1: return next(iter(m.values()))
        m = {k: v for k, v in nm.items() if key.startswith(k)}
        if len(m) == 1: return next(iter(m.values()))
        m = {k: v for k, v in nm.items() if key in k}
        if len(m) == 1: return next(iter(m.values()))
        return None

    def resolve_etype(val):
        if not val or val.strip().upper() in ('NULL', 'N/A', '-', ''): return None
        try: return int(val)
        except ValueError:
            r = _fuzzy(val.strip().lower(), etype_map)
            if r is not None: return r
            avail = ', '.join(f'"{n}"' for n in sorted(etype_map.keys()))
            raise ValueError(f"'{val}' is not a recognised Employee Type. Available: {avail}")

    def get_or_create_spec(val):
        if not val or val.strip().upper() in ('NULL', 'N/A', '-', ''): return None
        try: return int(val)
        except ValueError:
            name = val.strip(); key = name.lower()
            r = _fuzzy(key, spec_map)
            if r is not None: return r
            cur.execute("INSERT INTO Specialization (SpecializationName, IsActive) VALUES (%s, TRUE) ON CONFLICT (SpecializationName) DO NOTHING", (name,))
            cur.execute("SELECT SpecializationID FROM Specialization WHERE LOWER(SpecializationName) = LOWER(%s)", (name,))
            new_id = cur.fetchone()['specializationid']; spec_map[key] = new_id; return new_id

    def get_or_create_desig(val):
        if not val or val.strip().upper() in ('NULL', 'N/A', '-', ''): return None
        try: return int(val)
        except ValueError:
            name = val.strip(); key = name.lower()
            r = _fuzzy(key, desig_map)
            if r is not None: return r
            cur.execute("INSERT INTO Designation (DesignationName) VALUES (%s) ON CONFLICT (DesignationName) DO NOTHING", (name,))
            cur.execute("SELECT DesignationID FROM Designation WHERE LOWER(DesignationName) = LOWER(%s)", (name,))
            new_id = cur.fetchone()['designationid']; desig_map[key] = new_id; return new_id

    def norm_status(s):
        sn = s.lower().replace('-', '').replace(' ', '')
        if sn == 'parttime': return 'Part-Time'
        if sn == 'permanent': return 'Permanent'
        if sn == 'temporary': return 'Temporary'
        return s

    errors = []
    for i, emp in enumerate(rows, 1):
        try:
            emp_num     = str(emp.get('emp_num',     '') or '').strip()
            last_name   = str(emp.get('last_name',   '') or '').strip()
            first_name  = str(emp.get('first_name',  '') or '').strip()
            middle_name = str(emp.get('middle_name', '') or '').strip()
            email       = str(emp.get('email',       '') or '').strip()
            contact     = str(emp.get('contact',     '') or '').strip()
            spec_id     = get_or_create_spec(emp.get('specialization', ''))
            etype_id    = resolve_etype(emp.get('emp_type', ''))
            desig_id    = get_or_create_desig(emp.get('designation', ''))
            status      = norm_status(str(emp.get('status', 'Permanent') or 'Permanent'))

            if not emp_num:
                errors.append(f"Row {i}: missing Employee Number"); continue

            # Block insert if the employee number exists in archived records
            cur.execute("SELECT 1 FROM Faculty_Archive WHERE EmployeeNumber = %s LIMIT 1", (emp_num,))
            if cur.fetchone():
                errors.append(
                    f"Row {i} (Emp# {emp_num}): Employee Number already exists in archived records. "
                    "Numbers must be unique across active and archived employees."
                )
                continue

            cur.execute("""
                INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName,
                    Email, ContactNumber, SpecializationID, EmployeeTypeID,
                    DesignationID, EmployeeStatus)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (EmployeeNumber) DO NOTHING
            """, (emp_num, first_name, middle_name or None, last_name,
                  email or None, contact or None,
                  spec_id, etype_id, desig_id, status))

            role = 'Faculty'
            if desig_id:
                cur.execute("SELECT DesignationName FROM Designation WHERE DesignationID = %s", (desig_id,))
                dr = cur.fetchone()
                if dr and dr['designationname'] == 'Academic Head':
                    role = 'Academic Head'

            hashed_pw = generate_password_hash(emp_num)
            cur.execute("""
                INSERT INTO Accounts (Username, PasswordHash, Role, IsActive, EmployeeNumber)
                VALUES (%s, %s, %s, TRUE, %s) ON CONFLICT (Username) DO NOTHING
            """, (emp_num, hashed_pw, role, emp_num))

        except ValueError as ve:
            errors.append(f"Row {i}: {ve}")
    return errors


# ── Admin: XLSX faculty import ───────────────────────────────────────────────
@app.route('/admin/faculty/import/xlsx', methods=['POST'])
def admin_faculty_import_xlsx():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    if 'file' not in request.files or request.files['file'].filename == '':
        flash("No file selected.", "error"); return redirect(url_for('admin_employee'))

    file = request.files['file']
    if not file.filename.endswith('.xlsx'):
        flash("Please upload a .xlsx file.", "error"); return redirect(url_for('admin_employee'))

    import openpyxl
    _HEADER_KEYS = {
        'employeenumber': 'emp_num',   'employee number': 'emp_num',   'emp no': 'emp_num',
        'lastname':   'last_name',     'last name':   'last_name',     'surname':  'last_name',
        'firstname':  'first_name',    'first name':  'first_name',
        'middlename': 'middle_name',   'middle name': 'middle_name',   'middle initial': 'middle_name',
        'email': 'email',              'email address': 'email',
        'contactnumber': 'contact',    'contact number': 'contact',    'contact': 'contact',
        'specialization': 'specialization',
        'employeetype':   'emp_type',  'employee type': 'emp_type',    'type': 'emp_type',
        'employeestatus': 'status',    'employment status': 'status',  'status': 'status',
        'designation': 'designation',
    }

    wb = openpyxl.load_workbook(file.stream, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        flash("Excel file is empty.", "error"); return redirect(url_for('admin_employee'))

    col_map = {}
    for i, cell in enumerate(rows[0]):
        key = str(cell or '').lower().strip()
        if key in _HEADER_KEYS:
            col_map[_HEADER_KEYS[key]] = i

    employees = []
    for raw in rows[1:]:
        if not any(c for c in raw if c is not None): continue
        def gv(field):
            idx = col_map.get(field)
            return str(raw[idx] or '').strip() if idx is not None and idx < len(raw) else ''
        employees.append({
            'emp_num': gv('emp_num'), 'last_name': gv('last_name'),
            'first_name': gv('first_name'), 'middle_name': gv('middle_name'),
            'email': gv('email'), 'contact': gv('contact'),
            'specialization': gv('specialization'), 'emp_type': gv('emp_type'),
            'status': gv('status'), 'designation': gv('designation'),
        })

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        errs = _admin_insert_employees(employees, conn, cur)
        conn.commit()
        if errs:
            flash("Import completed with errors: " + "; ".join(errs[:3]), "error")
        else:
            flash(f"XLSX import successful — {len(employees)} employee(s) processed.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Import error: {e}", "error")
    finally:
        cur.close(); conn.close()

    return redirect(url_for('admin_employee'))


# ── Admin: CSV faculty analyze ───────────────────────────────────────────────
@app.route('/admin/faculty/import/csv/analyze', methods=['POST'])
def admin_faculty_csv_analyze():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    if 'file' not in request.files or request.files['file'].filename == '':
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    if not f.filename.endswith('.csv'):
        return jsonify({'error': 'Please upload a .csv file.'}), 400
    try:
        result = _parse_csv_employees(f.read())
        return jsonify(result), (400 if 'error' in result else 200)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Admin: XLSX faculty analyze ──────────────────────────────────────────────
@app.route('/admin/faculty/import/xlsx/analyze', methods=['POST'])
def admin_faculty_xlsx_analyze():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    if 'file' not in request.files or request.files['file'].filename == '':
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    if not f.filename.endswith('.xlsx'):
        return jsonify({'error': 'Please upload a .xlsx file.'}), 400
    try:
        result = _parse_xlsx_employees(f.read())
        return jsonify(result), (400 if 'error' in result else 200)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Admin: PDF faculty analyze ───────────────────────────────────────────────
@app.route('/admin/faculty/import/pdf/analyze', methods=['POST'])
def admin_faculty_pdf_analyze():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    if 'file' not in request.files or request.files['file'].filename == '':
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        data = parse_faculty_pdf(request.files['file'].read())
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Admin: DOCX faculty analyze ──────────────────────────────────────────────
@app.route('/admin/faculty/import/docx/analyze', methods=['POST'])
def admin_faculty_docx_analyze():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    if 'file' not in request.files or request.files['file'].filename == '':
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        data = parse_faculty_docx(request.files['file'].read())
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Admin: pre-import duplicate check ───────────────────────────────────────
@app.route('/admin/faculty/import/check-duplicates', methods=['POST'])
def admin_faculty_check_duplicates():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    data     = request.get_json() or {}
    emp_nums = [str(n).strip() for n in data.get('emp_nums', []) if str(n).strip()]
    if not emp_nums:
        return jsonify({'active': [], 'archived': []})
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT f.EmployeeNumber, f.FirstName, f.LastName, et.TypeName, f.EmployeeStatus
            FROM Faculty f
            LEFT JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID
            WHERE f.EmployeeNumber = ANY(%s)
        """, (emp_nums,))
        active = [{'emp_num': r[0], 'name': f"{r[2] or ''}, {r[1] or ''}".strip(', '),
                   'typename': r[3] or '', 'status': r[4] or ''} for r in cur.fetchall()]

        cur.execute("""
            SELECT fa.FacultyArchiveID, fa.EmployeeNumber, fa.FirstName, fa.LastName,
                   et.TypeName, fa.EmployeeStatus
            FROM Faculty_Archive fa
            LEFT JOIN EmployeeType et ON fa.EmployeeTypeID = et.EmployeeTypeID
            WHERE fa.EmployeeNumber = ANY(%s)
        """, (emp_nums,))
        archived = [{'archiveid': r[0], 'emp_num': r[1],
                     'name': f"{r[3] or ''}, {r[2] or ''}".strip(', '),
                     'typename': r[4] or '', 'status': r[5] or ''} for r in cur.fetchall()]

        return jsonify({'active': active, 'archived': archived})
    except Exception as e:
        return jsonify({'active': [], 'archived': [], 'error': str(e)})
    finally:
        cur.close(); conn.close()


# ── Admin: restore archived employee during import ───────────────────────────
@app.route('/admin/faculty/import/restore-archived', methods=['POST'])
def admin_faculty_restore_archived():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    archive_id = data.get('archive_id')
    if not archive_id:
        return jsonify({'error': 'No archive_id provided'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT EmployeeNumber FROM Faculty_Archive WHERE FacultyArchiveID = %s", (archive_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Archived employee not found'}), 404
        emp_num = row[0]
        cur.execute("""
            INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber,
                SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber,
                SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty_Archive WHERE FacultyArchiveID = %s
            ON CONFLICT (EmployeeNumber) DO NOTHING
        """, (archive_id,))
        cur.execute("UPDATE Accounts SET IsActive = TRUE WHERE Username = %s", (emp_num,))
        cur.execute("DELETE FROM Faculty_Archive WHERE FacultyArchiveID = %s", (archive_id,))
        conn.commit()
        return jsonify({'success': True, 'emp_num': emp_num})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


# ── Admin: PDF/DOCX faculty confirm ─────────────────────────────────────────
@app.route('/admin/faculty/import/doc/confirm', methods=['POST'])
def admin_faculty_doc_confirm():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    raw = request.form.get('employees_data', '[]')
    try:
        employees = json.loads(raw)
    except Exception:
        flash("Invalid data submitted.", "error"); return redirect(url_for('admin_employee'))

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        errs = _admin_insert_employees(employees, conn, cur)
        conn.commit()
        if errs:
            flash("Import completed with errors: " + "; ".join(errs[:3]), "error")
        else:
            flash(f"Import successful — {len(employees)} employee(s) processed.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Import error: {e}", "error")
    finally:
        cur.close(); conn.close()

    return redirect(url_for('admin_employee'))


@app.route('/admin/archived_employees')
def admin_archived_employees():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    query = """
        SELECT 
            fa.FacultyArchiveID, fa.EmployeeNumber, fa.FirstName, fa.MiddleName, fa.LastName, 
            fa.Email, fa.ContactNumber, fa.EmployeeStatus, fa.ArchivedAt,
            s.SpecializationName, et.TypeName
        FROM Faculty_Archive fa
        LEFT JOIN Specialization s ON fa.SpecializationID = s.SpecializationID
        LEFT JOIN EmployeeType et ON fa.EmployeeTypeID = et.EmployeeTypeID
        ORDER BY fa.ArchivedAt DESC
    """
    archives = query_db(query)
    archived_employees =[dict(row) for row in archives] if archives else[]

    et_rows = query_db("SELECT TypeName as typename FROM EmployeeType ORDER BY TypeName")
    spec_rows = query_db("SELECT SpecializationName as specializationname FROM Specialization ORDER BY SpecializationName")

    return render_template('admin/archived_employees_admin.html', 
                           archived_employees=archived_employees,
                           employee_types=et_rows,
                           specializations=spec_rows)

@app.route('/admin/restore_employee/<int:archive_id>')
def admin_restore_employee(archive_id):
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor) 

    try:
        cur.execute("SELECT EmployeeNumber FROM Faculty_Archive WHERE FacultyArchiveID = %s", (archive_id,))
        result = cur.fetchone()
        if not result:
            flash("Archived employee not found.", "error")
            return redirect(url_for('admin_archived_employees'))
        
        emp_num = result['employeenumber'] 

        cur.execute("""
            INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty_Archive WHERE FacultyArchiveID = %s
        """, (archive_id,))
        
        cur.execute("UPDATE Accounts SET IsActive = TRUE, EmployeeNumber = %s WHERE Username = %s", (emp_num, emp_num))
        cur.execute("DELETE FROM Faculty_Archive WHERE FacultyArchiveID = %s", (archive_id,))
        
        conn.commit()
        flash(f"Employee {emp_num} restored successfully and their account has been reactivated.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error restoring employee: {e}", "error")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('admin_archived_employees'))

@app.route('/admin/bulk_restore', methods=['POST'])
def admin_bulk_restore():
    if session.get('role') != 'Admin': 
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    
    data = request.get_json()
    archive_ids = data.get('archive_ids',[])
    
    if not archive_ids:
        return jsonify({"success": False, "message": "No employees selected"}), 400

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:
        placeholders = ', '.join(['%s'] * len(archive_ids))
        cur.execute(f"SELECT EmployeeNumber FROM Faculty_Archive WHERE FacultyArchiveID IN ({placeholders})", tuple(archive_ids))
        
        emp_nums =[row['employeenumber'] for row in cur.fetchall()]

        for aid in archive_ids:
            cur.execute("""
                INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
                SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
                FROM Faculty_Archive WHERE FacultyArchiveID = %s
            """, (aid,))
            cur.execute("DELETE FROM Faculty_Archive WHERE FacultyArchiveID = %s", (aid,))
        
        if emp_nums:
            placeholders_emp = ', '.join(['%s'] * len(emp_nums))
            cur.execute(f"UPDATE Accounts SET IsActive = TRUE, EmployeeNumber = Username WHERE Username IN ({placeholders_emp})", tuple(emp_nums))

        conn.commit()
        return jsonify({"success": True, "message": f"{len(emp_nums)} employees restored and reactivated."})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/admin/rooms')
def admin_rooms():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    total_labs = query_db("SELECT COUNT(*) as c FROM Room WHERE RoomType = 'Laboratory'", one=True)
    total_lec = query_db("SELECT COUNT(*) as c FROM Room WHERE RoomType = 'Lecture'", one=True)
    total_rooms = query_db("SELECT COUNT(*) as c FROM Room", one=True)
    total_bldgs = query_db("SELECT COUNT(*) as c FROM Building WHERE IsActive = TRUE", one=True)

    raw_buildings = query_db("SELECT BuildingID as buildingid, BuildingName as buildingname FROM Building WHERE IsActive = TRUE ORDER BY BuildingName ASC")
    raw_rooms = query_db("SELECT r.*, b.BuildingName FROM Room r JOIN Building b ON r.BuildingID = b.BuildingID ORDER BY r.RoomName ASC")
    rooms =[{k.lower(): v for k, v in row.items()} for row in raw_rooms] if raw_rooms else[]

    return render_template('admin/rooms_admin.html',
                           total_labs=total_labs['c'], total_lec=total_lec['c'], 
                           total_rooms=total_rooms['c'], total_bldgs=total_bldgs['c'], 
                           buildings=raw_buildings, rooms=rooms, raw_buildings=raw_buildings)

@app.route('/admin/add_building', methods=['POST'])
def add_building():
    name = (request.form.get('bldg_name') or '').strip()
    if name:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            try:
                cur.execute("INSERT INTO Building (BuildingName, IsActive) VALUES (%s, TRUE)", (name,))
                conn.commit()
                flash(f"Building '{name}' added successfully!", "success")
            except Exception as e:
                conn.rollback()
                flash(f"Error: {e}", "error")
            finally:
                cur.close(); conn.close()
    return redirect(url_for('admin_rooms'))

@app.route('/admin/add_room', methods=['POST'])
def add_room():
    name = (request.form.get('room_name') or '').strip()
    conn = get_db_connection()
    if conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO Room (RoomName, RoomType, RoomCapacity, BuildingID) VALUES (%s, %s, %s, %s)",
                (name, request.form.get('room_type'), request.form.get('capacity'), request.form.get('bldg_id'))
            )
            conn.commit()
            flash(f"Room '{name}' added successfully!", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Error: {e}", "error")
        finally:
            cur.close(); conn.close()
    return redirect(url_for('admin_rooms'))

@app.route('/admin/delete_room/<int:room_id>')
def delete_room(room_id):
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM Schedule WHERE RoomID = %s", (room_id,))
        if cur.fetchone()[0] > 0:
            flash("Delete Denied: This room is still assigned to an active schedule.", "error")
        else:
            cur.execute("DELETE FROM Room WHERE RoomID = %s", (room_id,))
            conn.commit()
            flash("Room deleted successfully.", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_rooms'))

@app.route('/admin/edit_room_submit', methods=['POST'])
def edit_room_submit():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    r_id = request.form.get('room_id')
    new_name = request.form.get('room_name').strip()
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT RoomID FROM Room WHERE UPPER(RoomName) = UPPER(%s) AND RoomID != %s", (new_name, r_id))
        if cur.fetchone():
            flash(f"Error: Room '{new_name}' already exists.", "error")
            return redirect(url_for('admin_rooms'))

        cur.execute("SELECT RoomName FROM Room WHERE RoomID = %s", (r_id,))
        old_name = cur.fetchone()[0]
        if old_name != new_name:
            cur.execute("SELECT COUNT(*) FROM Schedule WHERE RoomID = %s", (r_id,))
            if cur.fetchone()[0] > 0:
                flash("Error: Cannot change Room Number because it is linked to a schedule.", "error")
                return redirect(url_for('admin_rooms'))

        cur.execute("UPDATE Room SET RoomName=%s, RoomType=%s, RoomCapacity=%s, BuildingID=%s WHERE RoomID=%s", 
                    (new_name, request.form.get('room_type'), request.form.get('capacity'), request.form.get('bldg_id'), r_id))
        conn.commit()
        flash("Room updated successfully.", "success")
    finally: 
        cur.close(); conn.close()
    return redirect(url_for('admin_rooms'))

@app.route('/admin/delete_building/<int:bldg_id>')
def delete_building(bldg_id):
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM schedule_sessions ss
            JOIN room r ON ss.roomid = r.roomid
            WHERE r.buildingid = %s
        """, (bldg_id,))
        if cur.fetchone()[0] > 0:
            flash("Deletion Denied: Rooms in this building are currently in a schedule.", "error")
        else:
            cur.execute("DELETE FROM Room WHERE BuildingID = %s", (bldg_id,))
            cur.execute("DELETE FROM Building WHERE BuildingID = %s", (bldg_id,))
            conn.commit()
            flash("Building and its rooms deleted.", "success")
    except Exception as e: flash(f"Error: {e}", "error")
    finally: cur.close(); conn.close()
    return redirect(url_for('admin_rooms'))

@app.route('/admin/edit_building', methods=['POST'])
def edit_building():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    b_id = request.form.get('bldg_id')
    new_name = request.form.get('bldg_name').strip()
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT BuildingID FROM Building WHERE UPPER(BuildingName) = UPPER(%s) AND BuildingID != %s", (new_name, b_id))
        if cur.fetchone():
            flash("Error: Building name already exists.", "error")
        else:
            cur.execute("UPDATE Building SET BuildingName = %s WHERE BuildingID = %s", (new_name, b_id))
            conn.commit()
            flash("Building renamed successfully.", "success")
    except Exception as e: flash(f"Error: {e}", "error")
    finally: cur.close(); conn.close()
    return redirect(url_for('admin_rooms'))
# ── Room / Building JSON API (Admin + Academic Head) ─────────────────────────
_ROOM_ROLES = ('Admin', 'Academic Head')

@app.route('/admin/api/add_building', methods=['POST'])
def api_add_building():
    if session.get('role') not in _ROOM_ROLES:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Building name is required.'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT BuildingID FROM Building WHERE UPPER(BuildingName) = UPPER(%s)", (name,))
        if cur.fetchone():
            return jsonify({'success': False, 'error': f"Building '{name}' already exists."}), 409
        cur.execute("INSERT INTO Building (BuildingName, IsActive) VALUES (%s, TRUE) RETURNING BuildingID", (name,))
        bldg_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({'success': True, 'buildingid': bldg_id, 'buildingname': name})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close(); conn.close()

@app.route('/admin/api/add_room', methods=['POST'])
def api_add_room():
    if session.get('role') not in _ROOM_ROLES:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data     = request.get_json() or {}
    name     = (data.get('room_name') or '').strip()
    rtype    = data.get('room_type', 'Lecture')
    capacity = data.get('capacity')
    bldg_id  = data.get('bldg_id')
    if not name:     return jsonify({'success': False, 'error': 'Room number is required.'}), 400
    if not bldg_id:  return jsonify({'success': False, 'error': 'Building is required.'}), 400
    if not capacity: return jsonify({'success': False, 'error': 'Capacity is required.'}), 400
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT RoomID FROM Room WHERE UPPER(RoomName) = UPPER(%s)", (name,))
        if cur.fetchone():
            return jsonify({'success': False, 'error': f"Room '{name}' already exists."}), 409
        cur.execute(
            "INSERT INTO Room (RoomName, RoomType, RoomCapacity, BuildingID) VALUES (%s,%s,%s,%s) RETURNING RoomID",
            (name, rtype, int(capacity), int(bldg_id))
        )
        room_id = cur.fetchone()['roomid']
        cur.execute("SELECT BuildingName FROM Building WHERE BuildingID = %s", (int(bldg_id),))
        brow = cur.fetchone()
        conn.commit()
        return jsonify({'success': True, 'roomid': room_id, 'roomname': name, 'roomtype': rtype,
                        'roomcapacity': int(capacity), 'buildingid': int(bldg_id),
                        'buildingname': brow['buildingname'] if brow else ''})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close(); conn.close()

@app.route('/admin/api/edit_room', methods=['POST'])
def api_edit_room():
    if session.get('role') not in _ROOM_ROLES:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data      = request.get_json() or {}
    room_id   = data.get('room_id')
    new_name  = (data.get('room_name') or '').strip()
    room_type = data.get('room_type', 'Lecture')
    capacity  = data.get('capacity')
    bldg_id   = data.get('bldg_id')
    if not new_name: return jsonify({'success': False, 'error': 'Room number is required.'}), 400
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT RoomID FROM Room WHERE UPPER(RoomName)=UPPER(%s) AND RoomID!=%s", (new_name, room_id))
        if cur.fetchone():
            return jsonify({'success': False, 'error': f"Room '{new_name}' already exists."}), 409
        cur.execute("SELECT RoomName FROM Room WHERE RoomID = %s", (room_id,))
        old = cur.fetchone()
        if not old: return jsonify({'success': False, 'error': 'Room not found.'}), 404
        if old['roomname'] != new_name:
            cur.execute("SELECT COUNT(*) AS cnt FROM schedule_sessions WHERE roomid = %s", (room_id,))
            if cur.fetchone()['cnt'] > 0:
                return jsonify({'success': False, 'error': 'Cannot rename: room is linked to an active schedule.'}), 409
        cur.execute("UPDATE Room SET RoomName=%s,RoomType=%s,RoomCapacity=%s,BuildingID=%s WHERE RoomID=%s",
                    (new_name, room_type, int(capacity), int(bldg_id), room_id))
        cur.execute("SELECT BuildingName FROM Building WHERE BuildingID = %s", (int(bldg_id),))
        brow = cur.fetchone()
        conn.commit()
        return jsonify({'success': True, 'roomid': room_id, 'roomname': new_name, 'roomtype': room_type,
                        'roomcapacity': int(capacity), 'buildingid': int(bldg_id),
                        'buildingname': brow['buildingname'] if brow else ''})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close(); conn.close()

@app.route('/admin/api/edit_building', methods=['POST'])
def api_edit_building():
    if session.get('role') not in _ROOM_ROLES:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data     = request.get_json() or {}
    bldg_id  = data.get('bldg_id')
    new_name = (data.get('name') or '').strip()
    if not new_name: return jsonify({'success': False, 'error': 'Building name is required.'}), 400
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT BuildingID FROM Building WHERE UPPER(BuildingName)=UPPER(%s) AND BuildingID!=%s", (new_name, bldg_id))
        if cur.fetchone():
            return jsonify({'success': False, 'error': 'Building name already exists.'}), 409
        cur.execute("UPDATE Building SET BuildingName=%s WHERE BuildingID=%s", (new_name, bldg_id))
        conn.commit()
        return jsonify({'success': True, 'buildingid': bldg_id, 'buildingname': new_name})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close(); conn.close()

@app.route('/admin/api/delete_room', methods=['POST'])
def api_delete_room():
    if session.get('role') not in _ROOM_ROLES:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data    = request.get_json() or {}
    room_id = data.get('room_id')
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM schedule_sessions WHERE roomid = %s", (room_id,))
        if cur.fetchone()[0] > 0:
            return jsonify({'success': False, 'error': 'Room is linked to an active schedule and cannot be deleted.'}), 409
        cur.execute("DELETE FROM Room WHERE RoomID = %s", (room_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close(); conn.close()

@app.route('/admin/api/delete_building', methods=['POST'])
def api_delete_building():
    if session.get('role') not in _ROOM_ROLES:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data    = request.get_json() or {}
    bldg_id = data.get('bldg_id')
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT COUNT(*) FROM schedule_sessions ss
            JOIN room r ON ss.roomid = r.roomid WHERE r.buildingid = %s
        """, (bldg_id,))
        if cur.fetchone()[0] > 0:
            return jsonify({'success': False, 'error': 'Rooms in this building are in an active schedule.'}), 409
        cur.execute("DELETE FROM Room WHERE BuildingID = %s", (bldg_id,))
        cur.execute("DELETE FROM Building WHERE BuildingID = %s", (bldg_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close(); conn.close()

# ── Room Export Routes ─────────────────────────────────────────────────────────
@app.route('/admin/rooms/export/list', methods=['GET'])
def admin_rooms_export_list():
    if session.get('role') not in ('Admin', 'Acad Head'):
        return jsonify({'error': 'Unauthorized'}), 403
    rows = query_db("""
        SELECT b.BuildingID AS buildingid, b.BuildingName AS buildingname,
               COUNT(r.RoomID) AS room_count
        FROM Building b
        LEFT JOIN Room r ON r.BuildingID = b.BuildingID
        WHERE b.IsActive = TRUE
        GROUP BY b.BuildingID, b.BuildingName
        ORDER BY b.BuildingName
    """)
    return jsonify([{
        'buildingid':   row['buildingid'],
        'buildingname': row['buildingname'],
        'room_count':   int(row['room_count']),
    } for row in (rows or [])])

@app.route('/admin/rooms/export/data', methods=['POST'])
def admin_rooms_export_data():
    if session.get('role') not in ('Admin', 'Acad Head'):
        return jsonify({'error': 'Unauthorized'}), 403
    payload      = request.get_json(silent=True) or {}
    building_ids = payload.get('building_ids', [])
    if building_ids:
        placeholders = ','.join(['%s'] * len(building_ids))
        rows = query_db(f"""
            SELECT r.RoomID AS roomid, r.RoomName AS roomname,
                   r.RoomType AS roomtype, r.RoomCapacity AS roomcapacity,
                   b.BuildingID AS buildingid, b.BuildingName AS buildingname
            FROM Room r
            JOIN Building b ON r.BuildingID = b.BuildingID
            WHERE b.BuildingID IN ({placeholders})
            ORDER BY b.BuildingName, r.RoomType, r.RoomName
        """, tuple(building_ids))
    else:
        rows = query_db("""
            SELECT r.RoomID AS roomid, r.RoomName AS roomname,
                   r.RoomType AS roomtype, r.RoomCapacity AS roomcapacity,
                   b.BuildingID AS buildingid, b.BuildingName AS buildingname
            FROM Room r
            JOIN Building b ON r.BuildingID = b.BuildingID
            ORDER BY b.BuildingName, r.RoomType, r.RoomName
        """)
    buildings = {}
    for row in (rows or []):
        bid = row['buildingid']
        if bid not in buildings:
            buildings[bid] = {
                'buildingid':    bid,
                'buildingname':  row['buildingname'],
                'rooms':         [],
                'total_lecture': 0,
                'total_lab':     0,
            }
        bd = buildings[bid]
        bd['rooms'].append({
            'roomid':       row['roomid'],
            'roomname':     row['roomname'],
            'roomtype':     row['roomtype'],
            'roomcapacity': row['roomcapacity'],
        })
        if row['roomtype'] == 'Lecture':
            bd['total_lecture'] += 1
        else:
            bd['total_lab'] += 1
    return jsonify(list(buildings.values()))

def _build_rooms_docx():
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    payload   = request.get_json(silent=True) or {}
    buildings = payload.get('buildings', [])
    timestamp = payload.get('timestamp', '')

    HEADERS    = ['Room Number', 'Room Type', 'Capacity']
    COL_WIDTHS = [Inches(3.3), Inches(2.1), Inches(1.5)]

    def _shd(cell, hex_color):
        tcPr = cell._tc.get_or_add_tcPr()
        shd  = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear'); shd.set(qn('w:color'), 'auto')
        shd.set(qn('w:fill'), hex_color); tcPr.append(shd)

    def _shade_para(para, fill_hex):
        pPr = para._p.get_or_add_pPr()
        shd = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear'); shd.set(qn('w:color'), 'auto')
        shd.set(qn('w:fill'), fill_hex); pPr.append(shd)

    def _sr(run, bold=False, size=9, color=None, italic=False):
        run.bold = bold; run.italic = italic
        run.font.size = Pt(size); run.font.name = 'Calibri'
        if color: run.font.color.rgb = RGBColor(*color)

    doc = Document()
    for sec in doc.sections:
        sec.top_margin = sec.bottom_margin = Inches(0.7)
        sec.left_margin = sec.right_margin = Inches(0.8)

    h = doc.add_paragraph(); h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _sr(h.add_run('PUP LOPEZ CAMPUS  —  ROOMS AND BUILDINGS'),
        bold=True, size=16, color=(128, 0, 0))
    h2 = doc.add_paragraph(); h2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _sr(h2.add_run(timestamp), size=9, color=(100, 100, 100), italic=True)
    doc.add_paragraph()

    for idx, bldg in enumerate(buildings):
        bh = doc.add_paragraph()
        bh.paragraph_format.space_before = Pt(16 if idx > 0 else 4)
        bh.paragraph_format.space_after  = Pt(2)
        _shade_para(bh, 'E8DEDE')
        _sr(bh.add_run('  ' + bldg.get('buildingname', '').upper() + '  '),
            bold=True, size=13, color=(80, 0, 0))

        sl = doc.add_paragraph()
        sl.paragraph_format.space_before = Pt(0)
        sl.paragraph_format.space_after  = Pt(3)
        _sr(sl.add_run(
            f"Lecture Rooms: {bldg.get('total_lecture', 0)}   |   "
            f"Laboratories: {bldg.get('total_lab', 0)}   |   "
            f"Total: {len(bldg.get('rooms', []))}"),
            size=9, color=(100, 100, 100), italic=True)

        rooms = bldg.get('rooms', [])
        tbl = doc.add_table(rows=1, cols=len(HEADERS))
        tbl.style = 'Table Grid'
        for ci, (cell, hdr) in enumerate(zip(tbl.rows[0].cells, HEADERS)):
            cell.text = hdr; _shd(cell, '800000')
            _sr(cell.paragraphs[0].runs[0], bold=True, size=9, color=(255, 255, 255))
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            cell.width = COL_WIDTHS[ci]

        for ri, room in enumerate(rooms):
            fill = 'FFF0F0' if ri % 2 == 1 else 'FFFFFF'
            rc = tbl.add_row().cells
            vals = [room.get('roomname', ''),
                    room.get('roomtype', ''), str(room.get('roomcapacity', ''))]
            for ci, (cell, val) in enumerate(zip(rc, vals)):
                cell.text = val; _shd(cell, fill)
                if cell.paragraphs[0].runs:
                    _sr(cell.paragraphs[0].runs[0], size=9)
                    cell.paragraphs[0].alignment = (
                        WD_ALIGN_PARAGRAPH.LEFT if ci == 0 else WD_ALIGN_PARAGRAPH.CENTER)
                cell.width = COL_WIDTHS[ci]

        tot = tbl.add_row().cells
        for ci in range(len(tot)):
            _shd(tot[ci], 'F0E8E8')
            tot[ci].width = COL_WIDTHS[ci]
        merged = tot[0].merge(tot[1])
        merged.text = 'TOTAL ROOMS'
        if merged.paragraphs[0].runs:
            _sr(merged.paragraphs[0].runs[0], bold=True, size=9, color=(128, 0, 0))
        merged.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
        tot[2].text = str(len(rooms))
        if tot[2].paragraphs[0].runs:
            _sr(tot[2].paragraphs[0].runs[0], bold=True, size=10, color=(128, 0, 0))
        tot[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    buf = io.BytesIO(); doc.save(buf); buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        headers={'Content-Disposition': 'attachment; filename="rooms.docx"'})

def _build_rooms_xlsx():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    payload   = request.get_json(silent=True) or {}
    buildings = payload.get('buildings', [])
    timestamp = payload.get('timestamp', '')

    HEADERS    = ['Room Number', 'Room Type', 'Capacity']
    COL_WIDTHS = [32, 18, 12]

    maroon   = PatternFill('solid', fgColor='800000')
    dk_gray  = PatternFill('solid', fgColor='333333')
    lt_pink  = PatternFill('solid', fgColor='FFF0F0')
    tot_fill = PatternFill('solid', fgColor='F0E8E8')
    wht      = PatternFill('solid', fgColor='FFFFFF')
    thin     = Side(style='thin', color='E0D0D0')
    bdr      = Border(left=thin, right=thin, top=thin, bottom=thin)

    wb = Workbook()
    wb.remove(wb.active)

    for bldg in buildings:
        bname = bldg.get('buildingname', 'Building')[:31]
        rooms = bldg.get('rooms', [])
        ws    = wb.create_sheet(title=bname)

        ws.merge_cells('A1:C1')
        ws['A1'] = 'PUP LOPEZ CAMPUS  —  ROOMS AND BUILDINGS'
        ws['A1'].font      = Font(bold=True, size=13, color='FFFFFF', name='Calibri')
        ws['A1'].fill      = maroon
        ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[1].height = 24

        ws.merge_cells('A2:C2')
        ws['A2'] = bldg.get('buildingname', '')
        ws['A2'].font      = Font(bold=True, size=11, color='FFFFFF', name='Calibri')
        ws['A2'].fill      = dk_gray
        ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[2].height = 18

        ws.merge_cells('A3:C3')
        ws['A3'] = timestamp
        ws['A3'].font      = Font(italic=True, size=8, color='DDDDDD', name='Calibri')
        ws['A3'].fill      = dk_gray
        ws['A3'].alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[3].height = 14

        for ci, hdr in enumerate(HEADERS, 1):
            cell = ws.cell(row=4, column=ci, value=hdr)
            cell.font      = Font(bold=True, size=9, color='FFFFFF', name='Calibri')
            cell.fill      = maroon
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border    = bdr
        ws.row_dimensions[4].height = 16

        for ri, room in enumerate(rooms):
            rn   = ri + 5
            fill = lt_pink if ri % 2 == 1 else wht
            vals = [room.get('roomname', ''),
                    room.get('roomtype', ''), room.get('roomcapacity', '')]
            for ci, val in enumerate(vals, 1):
                cell = ws.cell(row=rn, column=ci, value=val)
                cell.font      = Font(size=9, name='Calibri',
                                      color='800000' if ci == 1 else '333333')
                cell.fill      = fill
                cell.alignment = Alignment(
                    horizontal='left' if ci == 1 else 'center', vertical='center')
                cell.border    = bdr

        tot_row = len(rooms) + 5
        ws.merge_cells(f'A{tot_row}:B{tot_row}')
        ws[f'A{tot_row}'] = 'TOTAL ROOMS'
        ws[f'A{tot_row}'].font      = Font(bold=True, size=9, color='800000', name='Calibri')
        ws[f'A{tot_row}'].fill      = tot_fill
        ws[f'A{tot_row}'].alignment = Alignment(horizontal='right', vertical='center')
        ws[f'A{tot_row}'].border    = bdr
        ws[f'C{tot_row}'] = len(rooms)
        ws[f'C{tot_row}'].font      = Font(bold=True, size=10, color='800000', name='Calibri')
        ws[f'C{tot_row}'].fill      = tot_fill
        ws[f'C{tot_row}'].alignment = Alignment(horizontal='center', vertical='center')
        ws[f'C{tot_row}'].border    = bdr

        for ci, w in enumerate(COL_WIDTHS, 1):
            ws.column_dimensions[get_column_letter(ci)].width = w
        ws.freeze_panes = 'A5'

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename="rooms.xlsx"'})

@app.route('/admin/rooms/export/docx', methods=['POST'])
def admin_export_rooms_docx():
    if session.get('role') not in ('Admin', 'Acad Head'):
        return jsonify({'error': 'Unauthorized'}), 403
    return _build_rooms_docx()

@app.route('/admin/rooms/export/xlsx', methods=['POST'])
def admin_export_rooms_xlsx():
    if session.get('role') not in ('Admin', 'Acad Head'):
        return jsonify({'error': 'Unauthorized'}), 403
    return _build_rooms_xlsx()

# ── End Room Export Routes ────────────────────────────────────────────────────
@app.route('/admin/curriculum')
def admin_curriculum():
    if session.get('role') != 'Admin': return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Resolve active AY from today's date, fall back to most recent AY
        today = date.today()
        cur.execute("""
            (SELECT academicyearid, yearstart FROM semester
              WHERE %s BETWEEN semstartdate AND semenddate LIMIT 1)
            UNION ALL
            (SELECT academicyearid, yearstart FROM academicyear ORDER BY yearstart DESC LIMIT 1)
            LIMIT 1
        """, (today,))
        active_res = cur.fetchone()

        if active_res:
            ay_id      = active_res[0]
            ay_start   = int(active_res[1])

            # Auto-sync: for every active offering × every year level, upsert a
            # program_yearlevel row with the best-matching curriculum (entry-year rule).
            cur.execute("""
                INSERT INTO program_yearlevel
                    (academicofferingid, academicyearid, yearlevel, curriculumid, isactive)
                SELECT
                    ao.academicofferingid,
                    %s,
                    gs.n,
                    (
                        SELECT c.curriculumid FROM curriculum c
                        WHERE c.academicofferingid = ao.academicofferingid
                          AND CAST(SUBSTRING(c.curriculumyear, 1, 4) AS INT) <= (%s - (gs.n - 1))
                        ORDER BY c.curriculumyear DESC LIMIT 1
                    ),
                    TRUE
                FROM academic_offering ao
                JOIN programs p ON ao.programcode = p.programcode
                JOIN LATERAL generate_series(1, p.numyearlevel) AS gs(n) ON TRUE
                WHERE ao.isactive = TRUE
                  AND EXISTS (
                      SELECT 1 FROM curriculum c WHERE c.academicofferingid = ao.academicofferingid
                  )
                ON CONFLICT (academicofferingid, academicyearid, yearlevel)
                DO UPDATE SET curriculumid = EXCLUDED.curriculumid
            """, (ay_id, ay_start))
            conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Sync error: {e}")
    finally:
        cur.close(); conn.close()

    # --- FETCH DATA FOR THE PAGE ---
    programs = query_db("SELECT * FROM Programs WHERE IsActive = TRUE ORDER BY ProgramName")
    selected_program = request.args.get('program_code')

    currs_raw = []
    if selected_program == 'All':
        currs_raw = query_db("""
            SELECT c.*, ao.offeringcode, ao.offeringdescription AS programname,
                   ao.programcode, p.programname AS parentprogramname
            FROM curriculum c
            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
            JOIN programs p ON ao.programcode = p.programcode
            ORDER BY p.programname ASC, c.curriculumyear DESC
        """)
    elif selected_program:
        currs_raw = query_db("""
            SELECT c.*, ao.offeringcode, ao.offeringdescription AS programname,
                   ao.programcode
            FROM curriculum c
            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
            WHERE ao.programcode = %s
            ORDER BY c.curriculumyear DESC
        """, (selected_program,))
    curriculums = [{k.lower(): v for k, v in row.items()} for row in currs_raw] if currs_raw else []

    unique_codes_raw = query_db("SELECT DISTINCT curriculumcode FROM curriculum ORDER BY curriculumcode DESC")
    unique_codes = [{k.lower(): v for k, v in row.items()} for row in unique_codes_raw] if unique_codes_raw else []

    all_curriculums_raw = query_db("""
        SELECT c.*, ao.offeringcode, ao.programcode
        FROM curriculum c
        JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
        ORDER BY c.curriculumyear DESC
    """)
    all_curriculums = [{k.lower(): v for k, v in row.items()} for row in all_curriculums_raw] if all_curriculums_raw else []

    # Build the assignments table from curriculum directly — robust even if program_yearlevel is unpopulated
    pylrows_raw = query_db("""
        SELECT
            c.curriculumid                                                           AS programyearlevelid,
            ao.offeringcode,
            p.programname,
            ao.programcode,
            c.curriculumyear                                                         AS academicyearid,
            0                                                                        AS yearlevel,
            c.curriculumid,
            c.curriculumcode,
            TRUE                                                                     AS isactive,
            COUNT(DISTINCT sec.sectionid) FILTER (WHERE sec.isactive = TRUE)         AS section_count
        FROM curriculum c
        JOIN academic_offering ao  ON ao.academicofferingid = c.academicofferingid
        JOIN programs p            ON p.programcode = ao.programcode
        LEFT JOIN program_yearlevel pyl ON pyl.academicofferingid = ao.academicofferingid
                                       AND pyl.curriculumid       = c.curriculumid
        LEFT JOIN sections sec     ON sec.programyearlevelid = pyl.programyearlevelid
        WHERE ao.isactive = TRUE
        GROUP BY c.curriculumid, ao.offeringcode, p.programname,
                 ao.programcode, c.curriculumyear, c.curriculumcode
        ORDER BY p.programname, ao.offeringcode, c.curriculumyear DESC
    """)
    cohorts = [{k.lower(): v for k, v in row.items()} for row in pylrows_raw] if pylrows_raw else []

    import_offerings = query_db("""
        SELECT ao.academicofferingid, ao.offeringcode, ao.offeringdescription,
               ao.programcode, t.trackname, t.trackcode,
               p.programname, COALESCE(p.numyearlevel, 4) AS numyearlevel
        FROM academic_offering ao
        LEFT JOIN track t ON t.trackid = ao.trackid
        LEFT JOIN programs p ON p.programcode = ao.programcode
        WHERE ao.isactive = TRUE
        ORDER BY p.programname, ao.offeringcode
    """)
    import_offerings = [dict(r) for r in (import_offerings or [])]

    return render_template('admin/curriculum_admin.html', programs=programs, curriculums=curriculums,
                           selected_program=selected_program, cohorts=cohorts,
                           unique_codes=unique_codes, all_curriculums=all_curriculums,
                           import_offerings=import_offerings)

@app.route('/admin/export/assignments')
def export_assignments():
    if session.get('role') not in['Admin', 'Academic Head']: return redirect(url_for('login'))
    
    data = query_db("""
        SELECT curr.curriculumcode, ao.offeringdescription AS programname,
               pyl.yearlevel AS year_level,
               COUNT(sec.sectionid) FILTER (WHERE sec.isactive = TRUE) AS numberofsections,
               pyl.academicyearid AS startacademicyear
        FROM program_yearlevel pyl
        JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
        JOIN programs p ON ao.programcode = p.programcode
        LEFT JOIN curriculum curr ON pyl.curriculumid = curr.curriculumid
        LEFT JOIN sections sec ON sec.programyearlevelid = pyl.programyearlevelid
        WHERE pyl.isactive = TRUE
        GROUP BY curr.curriculumcode, ao.offeringdescription, pyl.yearlevel, pyl.academicyearid
        ORDER BY pyl.academicyearid DESC, ao.offeringdescription ASC
    """)

    def generate():
        yield 'Curriculum Code,Program,Year Level,No. of Sections,AY of Entry\n'
        for row in data:
            r = {k.lower(): v for k, v in row.items()}
            clean_prog = r['programname'].replace(',', '')
            yield f"{r['curriculumcode']},{clean_prog},{r['year_level']},{r['numberofsections']},{r['startacademicyear']}\n"

    return Response(generate(), mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=Curriculum_Assignments.csv"})

@app.route('/admin/export/program/<program_code>')
def export_program_all(program_code):
    if session.get('role') not in['Admin', 'Academic Head']: return redirect(url_for('login'))
    
    query = """
        SELECT ao.offeringdescription AS programname, c.curriculumyear, cv.yearlevel,
               cv.semester, cv.subjectcode, cv."Prerequisite", cv."Co-requisite", cv.subjectname AS description,
               cv.lecturehours, cv.laboratoryhours, cv.creditunits, cv.tuitionhours
        FROM curriculum_view cv
        JOIN curriculum c ON cv.curriculumid = c.curriculumid
        JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
        JOIN programs p ON ao.programcode = p.programcode
    """
    params = []
    if program_code != 'All':
        query += " WHERE ao.offeringcode = %s"
        params.append(program_code)

    query += " ORDER BY ao.offeringdescription ASC, c.curriculumyear DESC, cv.yearlevel ASC, cv.semester ASC"
    raw_data = query_db(query, tuple(params))

    if not raw_data:
        flash("No data found to export.")
        return redirect(request.referrer)

    def generate():
        yield 'Program,Curriculum Year,Year Level,Semester,Subject code,Pre-requisite,Co-requisite,Subject description,Lecture Hours,Lab Hours,Credited Units,Tuition Hours\n'
        for row in raw_data:
            r = {k.lower(): v for k, v in row.items()}
            sem = "1st Sem" if r['semester'] == 'A' else "2nd Sem" if r['semester'] == 'B' else "Summer"
            clean_prog = r['programname'].replace(',', '')
            clean_desc = r['description'].replace(',', '').replace('"', '')
            yield f"{clean_prog},{r['curriculumyear']},{r['yearlevel']},{sem},{r['subjectcode']},{r.get('prerequisite','-')},{r.get('co-requisite','-')},\"{clean_desc}\",{r['lecturehours']},{r['laboratoryhours']},{r['creditunits']},{r['tuitionhours']}\n"

    return Response(generate(), mimetype='text/csv', headers={"Content-Disposition": f"attachment;filename=All_Curriculums_{program_code}.csv"})

@app.route('/admin/export/curriculum/<int:curriculum_id>')
def export_single_curriculum(curriculum_id):
    if session.get('role') not in ['Admin', 'Academic Head']: return redirect(url_for('login'))
    
    year_level = request.args.get('year', '0')
    semester = request.args.get('semester', 'All')

    query = """
        SELECT Semester, SubjectCode, "Prerequisite" as prerequisite, "Co-requisite" as corequisite, 
               "Description" as description, LectureHours, LaboratoryHours, CreditUnits, TuitionHours,
               CAST(SUBSTRING(ProgramYearLevel FROM '-(.*)') AS INT) as yearlevel
        FROM Curriculum_View WHERE CurriculumID = %s
    """
    params =[curriculum_id]

    if year_level != '0':
        query += " AND CAST(SUBSTRING(ProgramYearLevel FROM '-(.*)') AS INT) = %s"
        params.append(int(year_level))
    if semester != 'All':
        query += " AND Semester = %s"
        params.append(semester)

    query += " ORDER BY yearlevel ASC, Semester ASC"
    raw_data = query_db(query, tuple(params))

    if not raw_data:
        flash("No data found for these filters.")
        return redirect(request.referrer)

    def generate():
        yield 'Year Level,Semester,Subject code,Pre-requisite,Co-requisite,Subject description,Lecture Hours,Lab Hours,Credited Units,Tuition Hours\n'
        for row in raw_data:
            r = {k.lower(): v for k, v in row.items()}
            sem_label = "1st Sem" if r['semester'] == 'A' else "2nd Sem" if r['semester'] == 'B' else "Summer"
            yield f"{r['yearlevel']},{sem_label},{r['subjectcode']},{r.get('prerequisite') or '-'},{r.get('corequisite') or '-'},\"{r['description']}\",{r['lecturehours']},{r['laboratoryhours']},{r['creditunits']},{r['tuitionhours']}\n"

    info = query_db("SELECT CurriculumYear FROM Curriculum WHERE CurriculumID = %s", (curriculum_id,), one=True)
    filename_year = info['curriculumyear'] if info else "Export"

    return Response(generate(), mimetype='text/csv', headers={"Content-Disposition": f"attachment;filename=Curriculum_{filename_year}.csv"})
    
# --- FIXED: ADDED TUITION HOURS (th) TO CURRICULUM IMPORT ---
@app.route('/admin/curriculum/import', methods=['POST'])
def import_curriculum():
    file = request.files.get('file')
    prog_code = request.form.get('program_code')
    curr_year = request.form.get('curriculum_year')

    try:
        idx = {}
        for i in range(11):
            field_name = request.form.get(f'col_{i}')
            if field_name and field_name != 'skip': idx[field_name] = i
    except Exception:
        flash("Invalid column mapping.")
        return redirect(request.referrer)

    if not file or not prog_code or not curr_year: 
        flash("Missing required fields.")
        return redirect(request.referrer)

    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    csv_input = csv.reader(stream)
    header = next(csv_input)
    csv_data = list(csv_input)

    conn = get_db_connection(); cur = conn.cursor()
    
    try:
        # Resolve offering to academicofferingid (match by offeringcode, or base offering for programcode)
        cur.execute("""
            SELECT academicofferingid FROM academic_offering
            WHERE offeringcode = %s OR (programcode = %s AND trackid IS NULL)
            ORDER BY CASE WHEN offeringcode = %s THEN 0 ELSE 1 END
            LIMIT 1
        """, (prog_code, prog_code, prog_code))
        ao_row = cur.fetchone()
        if not ao_row:
            flash(f"Import Blocked: No academic offering found for '{prog_code}'.")
            return redirect(request.referrer)
        ao_id = ao_row[0]

        cur.execute("""
            SELECT 1 FROM curriculum c WHERE c.academicofferingid = %s AND c.curriculumyear = %s
        """, (ao_id, curr_year))
        if cur.fetchone():
            flash(f"Import Blocked: Curriculum for {prog_code} C.Y {curr_year} already exists in the system.")
            return redirect(request.referrer)

        def get_val(row, key): return row[idx[key]].strip() if key in idx and idx[key] < len(row) else ""
        def parse_int(val):
            try: return int(float(val)) if val else 0
            except: return 0

        csv_cc = get_val(csv_data[0], 'cc') if 'cc' in idx else None
        if csv_cc:
            curr_code_str = csv_cc[:6]
        else:
            years = curr_year.split('-')
            curr_code_str = f"CY{years[0][-2:]}{years[1][-2:]}" if len(years) == 2 else "CY0000"

        cur.execute("""
            INSERT INTO curriculum (curriculumcode, academicofferingid, curriculumyear)
            VALUES (%s, %s, %s) RETURNING curriculumid
        """, (curr_code_str, ao_id, curr_year))
        target_curr_id = cur.fetchone()[0]

        for row in csv_data:
            if not row: continue
            s_code = get_val(row, 'sc')[:15]   # VARCHAR(15) guard
            s_name = get_val(row, 'sn')[:100]  # VARCHAR(100) guard
            if not s_code: continue
            cur.execute("""
                INSERT INTO Subject (SubjectCode, SubjectName, CreditUnits, LectureHours, LaboratoryHours, TuitionHours)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (SubjectCode) DO UPDATE SET
                SubjectName = EXCLUDED.SubjectName,
                CreditUnits = EXCLUDED.CreditUnits,
                TuitionHours = EXCLUDED.TuitionHours
            """, (s_code, s_name or s_code, parse_int(get_val(row, 'u')), parse_int(get_val(row, 'lc')), parse_int(get_val(row, 'lb')), parse_int(get_val(row, 'th'))))

        last_yl  = parse_int(ui_year) if parse_int(ui_year) > 0 else 1
        last_sem = ui_sem if ui_sem != 'All' else 'A'

        for row in csv_data:
            if not row: continue
            s_code = get_val(row, 'sc')[:15]
            if not s_code: continue

            csv_yl = parse_int(get_val(row, 'yl'))
            if csv_yl > 0:
                last_yl = csv_yl
            final_yl = last_yl  # carry forward when cell is blank
            final_yl = parse_int(get_val(row, 'yl'))

            raw_s = get_val(row, 'sem').upper()
            if '1' in raw_s or 'A' in raw_s:        final_sem = 'A'
            elif '2' in raw_s or 'B' in raw_s:      final_sem = 'B'
            elif 'SUMMER' in raw_s or 'C' in raw_s: final_sem = 'C'
            else:                                    final_sem = last_sem
            last_sem = final_sem

            cur.execute("""
                INSERT INTO CurriculumSubject (CurriculumID, SubjectCode, YearLevel, Semester)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT uq_curriculumsubject DO NOTHING
            """, (target_curr_id, s_code, final_yl, final_sem))

            pre_val = get_val(row, 'pre') or ''
            co_val  = get_val(row, 'co')  or ''
            pre_clean = pre_val.strip() if pre_val.strip().upper() not in ('NONE', '-', 'N/A', '') else None
            co_clean  = co_val.strip()  if co_val.strip().upper()  not in ('NONE', '-', 'N/A', '') else None
            if pre_clean is not None or co_clean is not None:
                try:
                    cur.execute("SAVEPOINT prereq_sp")
                    cur.execute("""
                        UPDATE subject
                           SET prerequisite = COALESCE(%s, prerequisite),
                               corequisite  = COALESCE(%s, corequisite)
                         WHERE subjectcode = %s
                    """, (pre_clean, co_clean, s_code))
                    cur.execute("RELEASE SAVEPOINT prereq_sp")
                except Exception as _e:
                    cur.execute("ROLLBACK TO SAVEPOINT prereq_sp")
                    print(f"[WARN] Could not save prereq/coreq for {s_code!r}: {_e}")

        conn.commit()
        flash(f"Import Successful for {prog_code} C.Y {curr_year}")
        
    except Exception as e:
        conn.rollback()
        flash(f"System Error: {str(e)}")
    finally:
        cur.close(); conn.close()
    return redirect(request.referrer)

@app.route('/admin/curriculum/import/csv/analyze', methods=['POST'])
def analyze_curriculum_csv():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403

    file = request.files.get('file')
    if not file or not file.filename.lower().endswith('.csv'):
        return jsonify({'error': 'Please upload a valid CSV file.'}), 400

    idx = {}
    for i in range(11):
        v = request.form.get(f'col_{i}', '').strip()
        if v and v != 'skip':
            idx[v] = i

    ui_year = request.form.get('year_level', '0')
    ui_sem  = request.form.get('semester', 'All')

    try:
        stream = io.StringIO(file.stream.read().decode('UTF8'), newline=None)
        data_rows = list(csv.reader(stream))[1:]  # skip header
    except Exception as e:
        return jsonify({'error': f'Failed to read CSV: {e}', 'subjects': [], 'confidence': 0, 'warnings': [str(e)], 'subject_count': 0}), 400

    def _gv(row, key):
        if key not in idx or idx[key] >= len(row): return ''
        return str(row[idx[key]]).strip()
    def _pi(val):
        try: return int(float(val)) if val else 0
        except: return 0

    subjects = []
    last_yl  = _pi(ui_year) if _pi(ui_year) > 0 else 1
    last_sem = ui_sem if ui_sem != 'All' else 'A'

    for row in data_rows:
        if not row: continue
        sc = _gv(row, 'sc')[:15]
        if not sc: continue
        csv_yl = _pi(_gv(row, 'yl'))
        if csv_yl > 0: last_yl = csv_yl
        raw_s = _gv(row, 'sem').upper()
        if   '1' in raw_s or 'A' == raw_s:        last_sem = 'A'
        elif '2' in raw_s or 'B' == raw_s:        last_sem = 'B'
        elif 'SUMMER' in raw_s or 'C' == raw_s:   last_sem = 'C'
        pre = _gv(row, 'pre')
        co  = _gv(row, 'co')
        subjects.append({
            'sc': sc, 'sn': _gv(row, 'sn')[:100] or sc,
            'yl': last_yl, 'sem': last_sem,
            'u': _pi(_gv(row, 'u')), 'lc': _pi(_gv(row, 'lc')),
            'lb': _pi(_gv(row, 'lb')), 'th': _pi(_gv(row, 'th')),
            'pre': pre if pre.upper() not in ('NONE', '-', 'N/A', '') else '',
            'co':  co  if co.upper()  not in ('NONE', '-', 'N/A', '') else '',
        })

    warnings = [] if subjects else ['No subjects could be extracted. Check your column mapping and file format.']
    return jsonify({'subjects': subjects, 'confidence': 75 if subjects else 0,
                    'warnings': warnings, 'subject_count': len(subjects)})


@app.route('/admin/curriculum/import/xlsx/analyze', methods=['POST'])
def analyze_curriculum_xlsx():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403

    file = request.files.get('file')
    if not file or not file.filename.lower().endswith('.xlsx'):
        return jsonify({'error': 'Please upload a valid Excel (.xlsx) file.'}), 400

    idx = {}
    for i in range(11):
        v = request.form.get(f'col_{i}', '').strip()
        if v and v != 'skip':
            idx[v] = i

    ui_year = request.form.get('year_level', '0')
    ui_sem  = request.form.get('semester', 'All')

    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file.stream.read()), data_only=True)
        data_rows = list(wb.active.iter_rows(values_only=True))[1:]  # skip header
    except Exception as e:
        return jsonify({'error': f'Failed to read Excel file: {e}', 'subjects': [], 'confidence': 0, 'warnings': [str(e)], 'subject_count': 0}), 400

    def _gv(row, key):
        if key not in idx or idx[key] >= len(row): return ''
        v = row[idx[key]]
        return str(v).strip() if v is not None else ''
    def _pi(val):
        try: return int(float(val)) if val else 0
        except: return 0

    subjects = []
    last_yl  = _pi(ui_year) if _pi(ui_year) > 0 else 1
    last_sem = ui_sem if ui_sem != 'All' else 'A'

    for row in data_rows:
        if not any(c is not None for c in row): continue
        sc = _gv(row, 'sc')[:15]
        if not sc: continue
        csv_yl = _pi(_gv(row, 'yl'))
        if csv_yl > 0: last_yl = csv_yl
        raw_s = _gv(row, 'sem').upper()
        if   '1' in raw_s or 'A' == raw_s:        last_sem = 'A'
        elif '2' in raw_s or 'B' == raw_s:        last_sem = 'B'
        elif 'SUMMER' in raw_s or 'C' == raw_s:   last_sem = 'C'
        pre = _gv(row, 'pre')
        co  = _gv(row, 'co')
        subjects.append({
            'sc': sc, 'sn': _gv(row, 'sn')[:100] or sc,
            'yl': last_yl, 'sem': last_sem,
            'u': _pi(_gv(row, 'u')), 'lc': _pi(_gv(row, 'lc')),
            'lb': _pi(_gv(row, 'lb')), 'th': _pi(_gv(row, 'th')),
            'pre': pre if pre.upper() not in ('NONE', '-', 'N/A', '') else '',
            'co':  co  if co.upper()  not in ('NONE', '-', 'N/A', '') else '',
        })

    warnings = [] if subjects else ['No subjects could be extracted. Check your column mapping and file format.']
    return jsonify({'subjects': subjects, 'confidence': 75 if subjects else 0,
                    'warnings': warnings, 'subject_count': len(subjects)})


@app.route('/admin/curriculum/import/pdf/analyze', methods=['POST'])
def analyze_curriculum_pdf():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403

    file = request.files.get('file')
    if not file or not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Please upload a valid PDF file.'}), 400

    _CURR_FIELDS = ['cc', 'yl', 'sem', 'sc', 'pre', 'co', 'sn', 'lc', 'lb', 'u', 'th']
    override_col_map = None
    raw_cols = [request.form.get(f'col_{i}', '').strip() for i in range(11)]
    if any(v and v != 'skip' for v in raw_cols):
        override_col_map = {}
        for i, v in enumerate(raw_cols):
            if v and v != 'skip' and v in _CURR_FIELDS:
                override_col_map.setdefault(v, i)

    try:
        result = parse_curriculum_pdf(file.stream.read(), override_col_map)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e), 'subjects': [], 'confidence': 0, 'warnings': [str(e)], 'subject_count': 0}), 500


@app.route('/admin/curriculum/import/pdf/confirm', methods=['POST'])
def confirm_pdf_import():
    if session.get('role') != 'Admin': return redirect(url_for('login'))

    prog_code      = request.form.get('program_code', '').strip()
    curr_year      = request.form.get('curriculum_year', '').strip()
    subjects_json  = request.form.get('subjects_data', '[]')
    import_source  = request.form.get('import_source', 'File').strip() or 'File'
    override       = request.form.get('override', '0') == '1'

    if not prog_code or not curr_year:
        flash("Missing required fields.")
        return redirect(url_for('admin_curriculum'))

    try:
        subjects = json.loads(subjects_json)
    except Exception:
        flash("Invalid subject data format.")
        return redirect(url_for('admin_curriculum'))

    if not subjects:
        flash("No subject data to import.")
        return redirect(url_for('admin_curriculum'))

    def parse_int(val):
        try: return int(float(val)) if val else 0
        except: return 0

    def norm_sem(raw):
        r = str(raw).upper().strip()
        if r in ('A', '1', '1ST', 'FIRST'): return 'A'
        if r in ('B', '2', '2ND', 'SECOND'): return 'B'
        if r in ('C', 'SUMMER', 'S', 'MID', 'MIDYEAR'): return 'C'
        return 'A'

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT c.curriculumid FROM Curriculum c
            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
            WHERE ao.offeringcode = %s AND c.curriculumyear = %s
        """, (prog_code, curr_year))
        existing = cur.fetchone()
        if existing:
            if not override:
                flash(f"Import Blocked: Curriculum for {prog_code} C.Y {curr_year} already exists in the system.")
                return redirect(url_for('admin_curriculum'))
            # Override: remove existing subject links and re-import under same curriculum record
            existing_id = existing[0]
            cur.execute("DELETE FROM CurriculumSubject WHERE CurriculumID = %s", (existing_id,))

        if existing and override:
            curr_id = existing_id  # reuse existing curriculum record
        else:
            cur.execute("""
                SELECT academicofferingid FROM academic_offering
                WHERE offeringcode = %s OR (programcode = %s AND trackid IS NULL)
                ORDER BY CASE WHEN offeringcode = %s THEN 0 ELSE 1 END
                LIMIT 1
            """, (prog_code, prog_code, prog_code))
            ao_row = cur.fetchone()
            if not ao_row:
                # Auto-create base offering for programs that have none yet
                cur.execute("SELECT programcode FROM programs WHERE programcode = %s", (prog_code,))
                prog_row = cur.fetchone()
                if not prog_row:
                    flash(f"Import Failed: Program '{prog_code}' not found in the system.")
                    return redirect(url_for('admin_curriculum'))
                cur.execute("""
                    INSERT INTO academic_offering (programcode, trackid, offeringcode, offeringdescription, isactive)
                    VALUES (%s, NULL, %s, %s, TRUE)
                    ON CONFLICT DO NOTHING
                    RETURNING academicofferingid
                """, (prog_code, prog_code, prog_code))
                new_ao = cur.fetchone()
                if not new_ao:
                    cur.execute("SELECT academicofferingid FROM academic_offering WHERE offeringcode = %s LIMIT 1", (prog_code,))
                    new_ao = cur.fetchone()
                ao_id = new_ao[0]
            else:
                ao_id = ao_row[0]
            years = curr_year.split('-')
            curr_code = f"CY{years[0][-2:]}{years[1][-2:]}" if len(years) == 2 else "CY0000"
            cur.execute("""
                INSERT INTO Curriculum (CurriculumCode, academicofferingid, CurriculumYear)
                VALUES (%s, %s, %s) RETURNING CurriculumID
            """, (curr_code, ao_id, curr_year))
            curr_id = cur.fetchone()[0]

        for s in subjects:
            sc = str(s.get('sc', '')).strip()[:50]
            if not sc: continue
            sn = (str(s.get('sn', sc)).strip() or sc)[:100]
            cur.execute("""
                INSERT INTO Subject (SubjectCode, SubjectName, CreditUnits, LectureHours, LaboratoryHours, TuitionHours)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (SubjectCode) DO UPDATE SET
                    SubjectName = EXCLUDED.SubjectName,
                    CreditUnits = EXCLUDED.CreditUnits,
                    TuitionHours = EXCLUDED.TuitionHours
            """, (sc, sn, parse_int(s.get('u')), parse_int(s.get('lc')), parse_int(s.get('lb')), parse_int(s.get('th'))))

        for s in subjects:
            sc = str(s.get('sc', '')).strip()[:50]
            if not sc: continue
            yl  = parse_int(s.get('yl')) or 1
            sem = norm_sem(s.get('sem', 'A'))
            cur.execute("""
                INSERT INTO CurriculumSubject (CurriculumID, SubjectCode, YearLevel, Semester)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT uq_curriculumsubject DO NOTHING
            """, (curr_id, sc, yl, sem))

            pre = str(s.get('pre', '')).strip()[:100]
            co  = str(s.get('co',  '')).strip()[:100]
            pre_clean = pre if pre.upper() not in ('NONE', '-', 'N/A', '') else None
            co_clean  = co  if co.upper()  not in ('NONE', '-', 'N/A', '') else None
            if pre_clean or co_clean:
                try:
                    cur.execute("SAVEPOINT prereq_sp")
                    cur.execute("""
                        UPDATE Subject SET
                            Prerequisite = COALESCE(%s, Prerequisite),
                            Corequisite  = COALESCE(%s, Corequisite)
                        WHERE SubjectCode = %s
                    """, (pre_clean, co_clean, sc))
                    cur.execute("RELEASE SAVEPOINT prereq_sp")
                except Exception as _e:
                    cur.execute("ROLLBACK TO SAVEPOINT prereq_sp")

        conn.commit()
        action_word = "overridden" if (existing and override) else "imported"
        flash(f"{import_source} Import Successful: {prog_code} C.Y {curr_year} — {len(subjects)} subjects {action_word}.")
    except Exception as e:
        conn.rollback()
        flash(f"System Error: {str(e)}")
    finally:
        cur.close(); conn.close()

    return redirect(url_for('admin_curriculum'))


# ── Excel (.xlsx) curriculum import ──────────────────────────────────────────
@app.route('/admin/curriculum/import/xlsx', methods=['POST'])
def import_curriculum_xlsx():
    file      = request.files.get('file')
    prog_code = request.form.get('program_code')
    curr_year = request.form.get('curriculum_year')
    ui_year   = request.form.get('year_level')
    ui_sem    = request.form.get('semester')

    try:
        idx = {}
        for i in range(11):
            field_name = request.form.get(f'col_{i}')
            if field_name and field_name != 'skip':
                idx[field_name] = i
    except Exception:
        flash("Invalid column mapping.")
        return redirect(request.referrer)

    if not file or not prog_code or not curr_year:
        flash("Missing required fields.")
        return redirect(request.referrer)

    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file.stream.read()), data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    except Exception as e:
        flash(f"Failed to read Excel file: {e}")
        return redirect(request.referrer)

    if len(rows) < 2:
        flash("Excel file is empty or has no data rows.")
        return redirect(request.referrer)

    data_rows = rows[1:]

    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 1 FROM Curriculum c
            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
            WHERE ao.offeringcode = %s AND c.curriculumyear = %s
        """, (prog_code, curr_year))
        if cur.fetchone():
            flash(f"Import Blocked: Curriculum for {prog_code} C.Y {curr_year} already exists in the system.")
            return redirect(request.referrer)

        def get_val(row, key):
            if key not in idx or idx[key] >= len(row):
                return ""
            val = row[idx[key]]
            return str(val).strip() if val is not None else ""

        def parse_int(val):
            try: return int(float(val)) if val else 0
            except: return 0

        cur.execute("""
            SELECT academicofferingid FROM academic_offering
            WHERE offeringcode = %s OR (programcode = %s AND trackid IS NULL)
            ORDER BY CASE WHEN offeringcode = %s THEN 0 ELSE 1 END
            LIMIT 1
        """, (prog_code, prog_code, prog_code))
        ao_row = cur.fetchone()
        if not ao_row:
            flash(f"Import Failed: Program '{prog_code}' not found in the system.")
            return redirect(request.referrer)
        ao_id = ao_row[0]

        csv_cc = get_val(data_rows[0], 'cc') if data_rows and 'cc' in idx else None
        if csv_cc:
            curr_code_str = csv_cc[:6]
        else:
            years = curr_year.split('-')
            curr_code_str = f"CY{years[0][-2:]}{years[1][-2:]}" if len(years) == 2 else "CY0000"

        cur.execute(
            "INSERT INTO Curriculum (CurriculumCode, academicofferingid, CurriculumYear) "
            "VALUES (%s, %s, %s) RETURNING CurriculumID",
            (curr_code_str, ao_id, curr_year)
        )
        target_curr_id = cur.fetchone()[0]

        for row in data_rows:
            if not any(c is not None for c in row): continue
            s_code = get_val(row, 'sc')[:15]   # VARCHAR(15) guard
            s_name = get_val(row, 'sn')[:100]  # VARCHAR(100) guard
            if not s_code: continue
            cur.execute("""
                INSERT INTO Subject (SubjectCode, SubjectName, CreditUnits, LectureHours, LaboratoryHours, TuitionHours)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (SubjectCode) DO UPDATE SET
                    SubjectName = EXCLUDED.SubjectName,
                    CreditUnits = EXCLUDED.CreditUnits,
                    TuitionHours = EXCLUDED.TuitionHours
            """, (s_code, s_name or s_code, parse_int(get_val(row, 'u')),
                  parse_int(get_val(row, 'lc')), parse_int(get_val(row, 'lb')),
                  parse_int(get_val(row, 'th'))))

        last_yl  = parse_int(ui_year) if parse_int(ui_year) > 0 else 1
        last_sem = ui_sem if ui_sem != 'All' else 'A'

        for row in data_rows:
            if not any(c is not None for c in row): continue
            s_code = get_val(row, 'sc')[:15]
            if not s_code: continue

            csv_yl = parse_int(get_val(row, 'yl'))
            if csv_yl > 0:
                last_yl = csv_yl
            final_yl = last_yl  # carry forward when cell is blank

            raw_s = get_val(row, 'sem').upper()
            if '1' in raw_s or 'A' in raw_s:        final_sem = 'A'
            elif '2' in raw_s or 'B' in raw_s:      final_sem = 'B'
            elif 'SUMMER' in raw_s or 'C' in raw_s: final_sem = 'C'
            elif raw_s:                              final_sem = last_sem
            else:                                    final_sem = last_sem
            last_sem = final_sem

            cur.execute("""
                INSERT INTO CurriculumSubject (CurriculumID, SubjectCode, YearLevel, Semester)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT uq_curriculumsubject DO NOTHING
            """, (target_curr_id, s_code, final_yl, final_sem))

            pre_val   = get_val(row, 'pre') or ''
            co_val    = get_val(row, 'co')  or ''
            pre_clean = pre_val.strip() if pre_val.strip().upper() not in ('NONE', '-', 'N/A', '') else None
            co_clean  = co_val.strip()  if co_val.strip().upper()  not in ('NONE', '-', 'N/A', '') else None
            if pre_clean is not None or co_clean is not None:
                try:
                    cur.execute("SAVEPOINT prereq_sp")
                    cur.execute("""
                        UPDATE subject
                           SET prerequisite = COALESCE(%s, prerequisite),
                               corequisite  = COALESCE(%s, corequisite)
                         WHERE subjectcode = %s
                    """, (pre_clean, co_clean, s_code))
                    cur.execute("RELEASE SAVEPOINT prereq_sp")
                except Exception as _e:
                    cur.execute("ROLLBACK TO SAVEPOINT prereq_sp")

        conn.commit()
        flash(f"Import Successful for {prog_code} C.Y {curr_year}")

    except Exception as e:
        conn.rollback()
        flash(f"System Error: {str(e)}")
    finally:
        cur.close(); conn.close()

    return redirect(request.referrer)


# ── Word (.docx) curriculum import ───────────────────────────────────────────
@app.route('/admin/curriculum/import/docx/analyze', methods=['POST'])
def analyze_curriculum_docx_route():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403

    file = request.files.get('file')
    if not file or not file.filename.lower().endswith('.docx'):
        return jsonify({'error': 'Please upload a valid Word (.docx) file.'}), 400

    _CURR_FIELDS = ['cc', 'yl', 'sem', 'sc', 'pre', 'co', 'sn', 'lc', 'lb', 'u', 'th']
    override_col_map = None
    raw_cols = [request.form.get(f'col_{i}', '').strip() for i in range(11)]
    if any(v and v != 'skip' for v in raw_cols):
        override_col_map = {}
        for i, v in enumerate(raw_cols):
            if v and v != 'skip' and v in _CURR_FIELDS:
                override_col_map.setdefault(v, i)

    try:
        result = parse_curriculum_docx(file.stream.read(), override_col_map)
        return jsonify(result)
    except Exception as e:
        return jsonify({
            'error': str(e), 'subjects': [], 'confidence': 0,
            'warnings': [str(e)], 'subject_count': 0
        }), 500


@app.route('/admin/curriculum/check-duplicate')
def check_curriculum_duplicate():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    prog_code = request.args.get('program_code', '').strip()
    curr_year = request.args.get('curriculum_year', '').strip()
    if not prog_code or not curr_year:
        return jsonify({'exists': False})
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT c.curriculumid FROM Curriculum c
            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
            WHERE ao.offeringcode = %s AND c.curriculumyear = %s
        """, (prog_code, curr_year))
        row = cur.fetchone()
        return jsonify({'exists': bool(row), 'curriculum_id': row[0] if row else None})
    finally:
        cur.close(); conn.close()


@app.route('/admin/curriculum/delete/<int:curriculum_id>')
def admin_delete_curriculum(curriculum_id):
    if session.get('role') != 'Admin': return redirect(url_for('login'))

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # ── 1. Block if any current schedule rows reference this curriculum ──
        cur.execute("""
            SELECT COUNT(*) AS cnt
            FROM schedule s
            JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
            WHERE cs.curriculumid = %s
        """, (curriculum_id,))
        if cur.fetchone()['cnt'] > 0:
            flash("Cannot delete: this curriculum is linked to existing schedules.", "error")
            return redirect(request.referrer)

        # ── 2. Block if historical_data contains rows for any subject in this curriculum ──
        cur.execute("""
            SELECT COUNT(*) AS cnt
            FROM historical_data hd
            JOIN curriculumsubject cs
              ON UPPER(hd."Subject Code") = UPPER(cs.subjectcode)
            WHERE cs.curriculumid = %s
        """, (curriculum_id,))
        if cur.fetchone()['cnt'] > 0:
            flash("Cannot delete: this curriculum has historical schedule records.", "error")
            return redirect(request.referrer)

        # ── 3. Collect subjects exclusive to this curriculum ──────────────
        # A subject is "exclusive" if it appears in no other curriculum
        # and is not referenced in any schedule or historical record.
        cur.execute("""
            SELECT cs.subjectcode
            FROM curriculumsubject cs
            WHERE cs.curriculumid = %s
              AND cs.subjectcode NOT IN (
                  SELECT subjectcode FROM curriculumsubject WHERE curriculumid != %s
              )
              AND cs.subjectcode NOT IN (
                  SELECT UPPER(sub.subjectcode)
                  FROM curriculumsubject sub
                  JOIN schedule s ON s.curriculumsubjectid = sub.curriculumsubjectid
                  WHERE sub.curriculumid != %s
              )
              AND UPPER(cs.subjectcode) NOT IN (
                  SELECT UPPER(hd."Subject Code") FROM historical_data hd
              )
        """, (curriculum_id, curriculum_id, curriculum_id))
        exclusive_subjects = [r['subjectcode'] for r in cur.fetchall()]

        # ── 4. Cascade delete ──────────────────────────────────────────────
        # 4a. Clear curriculum from any program_yearlevel rows (preserves the year-level record, marks inactive)
        cur.execute("""
            UPDATE program_yearlevel
            SET curriculumid = NULL, isactive = FALSE
            WHERE curriculumid = %s
        """, (curriculum_id,))

        # 4b. Delete curriculum subjects
        cur.execute("DELETE FROM curriculumsubject WHERE curriculumid = %s", (curriculum_id,))

        if exclusive_subjects:
            cur.execute("DELETE FROM subject WHERE subjectcode = ANY(%s)", (exclusive_subjects,))

        # 4d. Delete the curriculum itself
        cur.execute("DELETE FROM curriculum WHERE curriculumid = %s", (curriculum_id,))

        conn.commit()
        msg = "Curriculum deleted."
        if exclusive_subjects:
            msg += f" {len(exclusive_subjects)} exclusive subject(s) also removed."
        flash(msg, "success")

    except Exception as e:
        conn.rollback()
        print(f"[delete_curriculum] ERROR: {e}")
        flash(f"Error deleting curriculum: {str(e)}", "error")
    finally:
        cur.close(); conn.close()

    return redirect(url_for('admin_curriculum'))

@app.route('/admin/curriculum/assign', methods=['POST'])
def assign_curriculum():
    if session.get('role') != 'Admin': return redirect(url_for('login'))

    pyl_id   = request.form.get('pyl_id')
    curr_id  = request.form.get('curriculum_id')
    isactive = request.form.get('isactive', '1') == '1'

    if not curr_id or curr_id == "":
        flash("Error: No curriculum version selected.")
        return redirect(url_for('admin_curriculum'))

    conn = get_db_connection(); cur = conn.cursor()
    try:
        if pyl_id and pyl_id.strip() != "":
            cur.execute("""
                UPDATE program_yearlevel
                SET curriculumid = %s, isactive = %s
                WHERE programyearlevelid = %s
            """, (int(curr_id), isactive, int(pyl_id)))
            flash("Assignment updated successfully.")
        else:
            offering_code = request.form.get('offering_code')
            academicyearid = request.form.get('academicyearid')
            yearlevel = int(request.form.get('yearlevel', 1))
            cur.execute("""
                SELECT academicofferingid FROM academic_offering WHERE offeringcode = %s
            """, (offering_code,))
            ao_row = cur.fetchone()
            if not ao_row:
                flash(f"Offering '{offering_code}' not found.")
                return redirect(url_for('admin_curriculum'))
            ao_id = ao_row[0]
            cur.execute("""
                INSERT INTO program_yearlevel
                    (academicofferingid, academicyearid, yearlevel, curriculumid, isactive)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (academicofferingid, academicyearid, yearlevel)
                DO UPDATE SET curriculumid = EXCLUDED.curriculumid, isactive = EXCLUDED.isactive
            """, (ao_id, academicyearid, yearlevel, int(curr_id), isactive))
            flash("New assignment created.")

        conn.commit()
    except Exception as e:
        conn.rollback()
        flash(f"Database Error: {str(e)}")
    finally:
        cur.close(); conn.close()

    return redirect(url_for('admin_curriculum'))

@app.route('/admin/curriculum/autogenerate', methods=['POST'])
def autogenerate_assignments():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    conn = get_db_connection(); cur = conn.cursor()

    try:
        # Resolve current active AY
        cur.execute("SELECT academicyearid, yearstart FROM academicyear WHERE isactive = TRUE ORDER BY yearstart DESC LIMIT 1")
        active_ay = cur.fetchone()
        if not active_ay:
            flash("Error: No Active Academic Year found.")
            return redirect(url_for('admin_curriculum'))

        ay_id    = active_ay[0] if not isinstance(active_ay, dict) else active_ay['academicyearid']
        ay_start = int(active_ay[1] if not isinstance(active_ay, dict) else active_ay['yearstart'])

        # Fetch all active academic offerings with their program duration
        cur.execute("""
            SELECT ao.academicofferingid, ao.offeringcode, p.numyearlevel
            FROM academic_offering ao
            JOIN programs p ON ao.programcode = p.programcode
            WHERE ao.isactive = TRUE
        """)
        offerings = cur.fetchall()

        for off in offerings:
            ao_id        = off[0] if not isinstance(off, dict) else off['academicofferingid']
            offering_code = off[1] if not isinstance(off, dict) else off['offeringcode']
            num_years    = int(off[2] if not isinstance(off, dict) else off['numyearlevel'])

            for yr in range(1, num_years + 1):
                # The batch that is currently in year `yr` entered (ay_start - yr + 1)
                entry_year_start = ay_start - (yr - 1)

                # Best-matching curriculum: latest whose year <= entry year of this batch
                cur.execute("""
                    SELECT curriculumid FROM curriculum
                    WHERE academicofferingid = %s
                      AND CAST(SUBSTRING(curriculumyear, 1, 4) AS INT) <= %s
                    ORDER BY curriculumyear DESC LIMIT 1
                """, (ao_id, entry_year_start))
                best = cur.fetchone()
                best_curr_id = (best[0] if not isinstance(best, dict) else best['curriculumid']) if best else None

                cur.execute("""
                    INSERT INTO program_yearlevel
                        (academicofferingid, academicyearid, yearlevel, curriculumid, isactive)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (academicofferingid, academicyearid, yearlevel)
                    DO UPDATE SET curriculumid = EXCLUDED.curriculumid,
                                  isactive     = EXCLUDED.isactive
                    RETURNING programyearlevelid
                """, (ao_id, ay_id, yr, best_curr_id, best_curr_id is not None))
                pyl_row = cur.fetchone()
                pyl_id = pyl_row[0] if not isinstance(pyl_row, dict) else pyl_row['programyearlevelid']

                # Seed one default section if none exist yet for this year level
                sec_name = f"{offering_code} {yr}-A"
                cur.execute("""
                    INSERT INTO sections (programyearlevelid, sectionname, isactive)
                    VALUES (%s, %s, TRUE)
                    ON CONFLICT (programyearlevelid, sectionname) DO NOTHING
                """, (pyl_id, sec_name))

        conn.commit()
        flash("Curriculum assignments updated based on batch entry years.")
    except Exception as e:
        conn.rollback()
        flash(f"Error: {str(e)}")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_curriculum'))

@app.route('/admin/curriculum/view/<int:curriculum_id>')
def admin_view_curriculum(curriculum_id):
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    y_lvl = request.args.get('year', '0') 
    sem = request.args.get('semester', 'All')

    info_sql = """
        SELECT c.*, ao.offeringcode, ao.offeringdescription AS programname,
               ao.academicofferingid, ao.programcode, p.numyearlevel
        FROM curriculum c
        JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
        JOIN programs p ON ao.programcode = p.programcode
        WHERE c.curriculumid = %s
    """
    info_raw = query_db(info_sql, (curriculum_id,), one=True)
    if not info_raw: return redirect(url_for('admin_curriculum'))

    info = {k.lower(): v for k, v in info_raw.items()}
    progs = query_db("SELECT * FROM Programs WHERE IsActive = TRUE ORDER BY ProgramName")

    other_sql = """
        SELECT curriculumid, curriculumyear, curriculumcode
        FROM curriculum
        WHERE academicofferingid = %s
        ORDER BY curriculumyear DESC
    """
    other_currs = query_db(other_sql, (info['academicofferingid'],))

    main_sql = """
        SELECT cv.subjectcode, cv."Prerequisite" AS prerequisite, cv."Co-requisite" AS corequisite,
               s.subjectname, cv.lecturehours, cv.laboratoryhours, cv.creditunits, cv.tuitionhours,
               cv.semester, cv.yearlevel
        FROM curriculum_view cv
        LEFT JOIN subject s ON cv.subjectcode = s.subjectcode
        WHERE cv.curriculumid = %s
    """
    params = [curriculum_id]
    if y_lvl != '0':
        main_sql += " AND cv.yearlevel = %s"
        params.append(int(y_lvl))
    if sem != 'All':
        main_sql += " AND cv.semester = %s"
        params.append(sem)

    main_sql += " ORDER BY cv.yearlevel ASC, cv.semester ASC"
    raw_subs = query_db(main_sql, tuple(params))
    subs =[{k.lower(): v for k, v in row.items()} for row in raw_subs] if raw_subs else[]

    return render_template('admin/curriculum_view_admin.html', info=info, subjects=subs, programs=progs, other_curriculums=other_currs, curr_year=y_lvl, curr_sem=sem)

@app.route('/admin/schedule')
def admin_schedule():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    return render_template('admin/schedule_admin.html')

def format_time(t):
    if not t: return "-"
    if isinstance(t, time): return t.strftime("%I:%M %p")
    if isinstance(t, str):
        try: return datetime.strptime(t, "%H:%M:%S").strftime("%I:%M %p")
        except ValueError: return t 
    if isinstance(t, timedelta):
        return (datetime.min + t).strftime("%I:%M %p")
    return str(t)

def format_date_input(d):
    return d.strftime('%Y-%m-%d') if d else ''

@app.route('/admin/user-management')
def admin_user_management():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    conn = get_db_connection(); cur = conn.cursor()
    try:
        def to_dict(cursor):
            columns = [col[0].lower() for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

        cur.execute("""
            SELECT a.userid, a.username, a.role, a.isactive, a.datecreated, a.employeenumber,
                   COALESCE(f.lastname || ', ' || f.firstname, '—') AS fullname
            FROM   accounts a
            LEFT JOIN faculty f ON a.employeenumber = f.employeenumber
            ORDER BY a.role ASC, a.username ASC
        """)
        accounts = to_dict(cur)

        cur.execute("""
            SELECT f.employeenumber,
                   f.lastname || ', ' || f.firstname AS fullname
            FROM   faculty f
            WHERE  f.employeenumber NOT IN (
                       SELECT employeenumber FROM accounts WHERE employeenumber IS NOT NULL
                   )
            ORDER BY f.lastname, f.firstname
        """)
        unlinked_employees = to_dict(cur)

        return render_template('admin/user_management_admin.html',
                               accounts=accounts, unlinked_employees=unlinked_employees)
    except Exception as e:
        flash(f"Error loading accounts: {e}", "error")
        return redirect(url_for('admin_dashboard'))
    finally:
        cur.close(); conn.close()

def _ensure_ay_finalized_col(cur):
    """Add isfinalized BOOLEAN DEFAULT FALSE to academicyear if not present."""
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'academicyear' AND column_name = 'isfinalized'
    """)
    if not cur.fetchone():
        cur.execute("ALTER TABLE academicyear ADD COLUMN isfinalized BOOLEAN NOT NULL DEFAULT FALSE")


@app.route('/admin/settings')
def admin_settings():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        def to_dict(cursor):
            columns =[col[0].lower() for col in cursor.description]
            return[dict(zip(columns, row)) for row in cursor.fetchall()]

        _ensure_ay_finalized_col(cur)
        # Ensure numberofsections column exists on program_yearlevel
        cur.execute("""
            ALTER TABLE program_yearlevel
            ADD COLUMN IF NOT EXISTS numberofsections INTEGER DEFAULT 1
        """)
        # Widen sectionname to accommodate generated names like BSBIO-AT-1A
        cur.execute("""
            ALTER TABLE sections
            ALTER COLUMN sectionname TYPE VARCHAR(100)
        """)
        # Snapshot columns — store pre-deactivation states for program reactivation restoration
        cur.execute("ALTER TABLE academic_offering ADD COLUMN IF NOT EXISTS snapshot_isactive BOOLEAN DEFAULT NULL")
        cur.execute("ALTER TABLE program_yearlevel ADD COLUMN IF NOT EXISTS snapshot_isactive BOOLEAN DEFAULT NULL")
        cur.execute("ALTER TABLE sections          ADD COLUMN IF NOT EXISTS snapshot_isactive BOOLEAN DEFAULT NULL")
        # Back-fill from actual section counts for any rows that are NULL
        cur.execute("""
            UPDATE program_yearlevel pyl
            SET numberofsections = (
                SELECT COUNT(*) FROM sections sec
                WHERE sec.programyearlevelid = pyl.programyearlevelid
            )
            WHERE pyl.numberofsections IS NULL
        """)
        # Backfill program_yearlevel for offerings that have no year-level rows at all
        cur.execute("SELECT academicyearid FROM academicyear WHERE isactive = TRUE LIMIT 1")
        _ay_row = cur.fetchone()
        if not _ay_row:
            cur.execute("SELECT academicyearid FROM academicyear ORDER BY yearstart DESC NULLS LAST LIMIT 1")
            _ay_row = cur.fetchone()
        if _ay_row:
            _active_ay = _ay_row[0]
            cur.execute("""
                SELECT ao.academicofferingid, COALESCE(p.numyearlevel, 4) AS num_yr
                FROM   academic_offering ao
                JOIN   programs p ON p.programcode = ao.programcode
                WHERE  ao.isactive = TRUE
                AND    NOT EXISTS (
                    SELECT 1 FROM program_yearlevel pyl
                    WHERE pyl.academicofferingid = ao.academicofferingid
                )
            """)
            _missing = cur.fetchall()
            for _ao_id, _num_yr in _missing:
                for _yr in range(1, _num_yr + 1):
                    cur.execute("""
                        INSERT INTO program_yearlevel
                            (academicofferingid, academicyearid, yearlevel, isactive, numberofsections)
                        VALUES (%s, %s, %s, TRUE, 1)
                        ON CONFLICT ON CONSTRAINT uq_program_yearlevel DO NOTHING
                    """, (_ao_id, _active_ay, _yr))

        # Backfill sections for year levels that have fewer sections than numberofsections
        _LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        cur.execute("""
            SELECT pyl.programyearlevelid, pyl.yearlevel,
                   COALESCE(pyl.numberofsections, 1) AS numberofsections,
                   ao.offeringcode
            FROM   program_yearlevel pyl
            JOIN   academic_offering ao ON ao.academicofferingid = pyl.academicofferingid
            WHERE  pyl.isactive = TRUE
        """)
        _pyl_rows = cur.fetchall()
        for _pyl_id, _yr, _num_sec, _offer_code in _pyl_rows:
            # Count ALL sections (active + inactive) — only create truly missing rows
            cur.execute("SELECT COUNT(*) FROM sections WHERE programyearlevelid=%s", (_pyl_id,))
            _existing_count = cur.fetchone()[0]
            if _existing_count >= _num_sec:
                continue
            cur.execute("SELECT sectionname FROM sections WHERE programyearlevelid=%s", (_pyl_id,))
            _existing_names = {row[0] for row in cur.fetchall()}
            _added = 0
            for _j in range(26):
                if _existing_count + _added >= _num_sec:
                    break
                _sec_name = f"{_offer_code}-{_yr}{_LETTERS[_j]}"
                if _sec_name not in _existing_names:
                    cur.execute("""
                        INSERT INTO sections (programyearlevelid, sectionname, isactive)
                        VALUES (%s, %s, TRUE)
                        ON CONFLICT (programyearlevelid, sectionname) DO NOTHING
                    """, (_pyl_id, _sec_name))
                    _existing_names.add(_sec_name)
                    _added += 1

        conn.commit()

        # Academic Years are never auto-locked — only finalized AYs are locked.
        is_locked     = False
        locked_detail = ""

        cur.execute('SELECT * FROM vw_academic_year_semesters ORDER BY academicyearid DESC')
        ay_data = to_dict(cur)

        # Compute per-AY status: isfinalized + whether any Published schedule exists
        cur.execute("""
            SELECT ay.academicyearid,
                   COALESCE(ay.isfinalized, FALSE)            AS isfinalized,
                   BOOL_OR(sv.status = 'Published') IS TRUE   AS has_published
            FROM   academicyear ay
            LEFT JOIN semester s   ON s.academicyearid  = ay.academicyearid
            LEFT JOIN schedule sc  ON sc.semesterid     = s.semesterid
            LEFT JOIN schedule_version sv ON sv.scheduleid = sc.scheduleid
            GROUP BY ay.academicyearid, ay.isfinalized
        """)
        ay_status_map = {row[0]: {'isfinalized': row[1], 'has_published': bool(row[2])}
                         for row in cur.fetchall()}
        def _to_date(val):
            if val is None: return None
            if isinstance(val, date): return val
            try: return date.fromisoformat(str(val)[:10])
            except: return None

        today = date.today()
        for ay in ay_data:
            st = ay_status_map.get(ay['academicyearid'], {'isfinalized': False, 'has_published': False})
            ay['isfinalized']   = st['isfinalized']
            ay['has_published'] = st['has_published']

            if ay['isfinalized']:
                ay['computed_status'] = 'finalized'
                continue

            sem_starts = [_to_date(ay.get(k)) for k in ['1st sem start', '2nd sem start', '3rd sem start']]
            sem_ends   = [_to_date(ay.get(k)) for k in ['1st sem end',   '2nd sem end',   '3rd sem end'  ]]
            sem_starts = [d for d in sem_starts if d]
            sem_ends   = [d for d in sem_ends   if d]

            if not sem_starts and not sem_ends:
                ay['computed_status'] = 'upcoming'
            elif sem_ends and all(e < today for e in sem_ends):
                ay['computed_status'] = 'past'
            elif sem_starts and any(s <= today for s in sem_starts) and sem_ends and any(e >= today for e in sem_ends):
                ay['computed_status'] = 'current'
            elif sem_starts and all(s > today for s in sem_starts):
                ay['computed_status'] = 'upcoming'
            else:
                ay['computed_status'] = 'current'

        cur.execute("SELECT * FROM EmployeeType ORDER BY EmployeeTypeID ASC")
        emp_types = to_dict(cur)
        for et in emp_types:
            et['reg_time_str'] = f"{format_time(et.get('regular_start'))} - {format_time(et.get('regular_end'))}" if et.get('regular_start') else "-"
            et['pt_time_str'] = f"{format_time(et.get('parttime_start'))} - {format_time(et.get('parttime_end'))}" if et.get('parttime_start') else "-"

        designee_base = next((et for et in emp_types if et['typename'] == 'Designee'), None)

        cur.execute("SELECT * FROM Designation ORDER BY DesignationName ASC")
        designations = to_dict(cur)

        from database import load_scheduler_config
        sched_cfg = load_scheduler_config()

        cur.execute("""
            SELECT a.userid, a.username, a.role, a.isactive, a.datecreated, a.employeenumber,
                   COALESCE(f.lastname || ', ' || f.firstname, '—') AS fullname
            FROM   accounts a
            LEFT JOIN faculty f ON a.employeenumber = f.employeenumber
            ORDER BY a.role ASC, a.username ASC
        """)
        accounts = to_dict(cur)

        cur.execute("""
            SELECT f.employeenumber,
                   f.lastname || ', ' || f.firstname AS fullname
            FROM   faculty f
            WHERE  f.employeenumber NOT IN (
                       SELECT employeenumber FROM accounts WHERE employeenumber IS NOT NULL
                   )
            ORDER BY f.lastname, f.firstname
        """)
        unlinked_employees = to_dict(cur)

        # ── Activity Logs ────────────────────────────────────
        _ensure_activity_log_table()
        try:
            cur.execute("""
                SELECT logid, TO_CHAR(logtime, 'YYYY-MM-DD HH24:MI:SS') AS logtime,
                       action, details, initiated_by, category, log_color
                FROM   activity_log
                ORDER  BY logtime DESC
                LIMIT  200
            """)
            activity_logs = to_dict(cur)
        except Exception:
            activity_logs = []

        # ── Program Management data ──────────────────────────
        cur.execute("""
            SELECT p.programcode, p.programname,
                   COALESCE(p.programtype, 'Undergraduate') AS programtype,
                   p.isactive,
                   COALESCE(p.numyearlevel, 4) AS numyearlevel,
                   COUNT(DISTINCT c.curriculumid)                                             AS curr_count,
                   COUNT(DISTINCT ao.academicofferingid) FILTER (WHERE ao.isactive = TRUE)    AS offering_count,
                   COUNT(DISTINCT pyl.programyearlevelid)                                     AS yearlevel_count,
                   COUNT(DISTINCT sec.sectionid) FILTER (WHERE sec.isactive = TRUE)           AS section_count
            FROM   programs p
            LEFT JOIN academic_offering  ao  ON ao.programcode  = p.programcode
            LEFT JOIN curriculum         c   ON c.academicofferingid = ao.academicofferingid
            LEFT JOIN program_yearlevel  pyl ON pyl.academicofferingid = ao.academicofferingid AND pyl.isactive = TRUE
            LEFT JOIN sections           sec ON sec.programyearlevelid = pyl.programyearlevelid
            GROUP  BY p.programcode, p.programname, p.programtype,
                      p.isactive, p.numyearlevel
            ORDER  BY p.programname
        """)
        programs_mgmt = to_dict(cur)

        cur.execute("""
            SELECT c.curriculumid, c.curriculumcode, ao.offeringcode AS programcode,
                   c.curriculumyear,
                   COUNT(cs.subjectcode)          AS subj_count,
                   COALESCE(SUM(s.creditunits), 0) AS total_units
            FROM   curriculum c
            JOIN   academic_offering ao ON ao.academicofferingid = c.academicofferingid
            LEFT JOIN curriculumsubject cs ON cs.curriculumid = c.curriculumid
            LEFT JOIN subject           s  ON s.subjectcode   = cs.subjectcode
            GROUP  BY c.curriculumid, c.curriculumcode,
                      ao.offeringcode, c.curriculumyear
            ORDER  BY ao.offeringcode, c.curriculumyear DESC
        """)
        curricula_mgmt = to_dict(cur)

        cur.execute("""
            SELECT sec.sectionid, sec.sectionname, pyl.yearlevel,
                   ao.offeringcode AS programcode, sec.isactive,
                   pyl.programyearlevelid, pyl.academicofferingid
            FROM   sections sec
            JOIN   program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN   academic_offering ao  ON pyl.academicofferingid = ao.academicofferingid
            JOIN   programs p            ON ao.programcode = p.programcode
            WHERE  p.isactive = TRUE
            ORDER  BY ao.offeringcode, pyl.yearlevel, sec.sectionname
        """)
        sections_mgmt = to_dict(cur)

        cur.execute("""
            SELECT ao.academicofferingid, ao.offeringcode, ao.offeringdescription,
                   ao.programcode, ao.isactive,
                   t.trackname, t.trackcode, t.tracktype,
                   COUNT(DISTINCT pyl.programyearlevelid)               AS yearlevel_count,
                   COALESCE(SUM(pyl.numberofsections), 0)               AS section_count
            FROM   academic_offering ao
            LEFT JOIN track             t   ON t.trackid = ao.trackid
            LEFT JOIN program_yearlevel pyl ON pyl.academicofferingid = ao.academicofferingid AND pyl.isactive = TRUE
            GROUP  BY ao.academicofferingid, ao.offeringcode, ao.offeringdescription,
                      ao.programcode, ao.isactive, t.trackname, t.trackcode, t.tracktype
            ORDER  BY ao.programcode, ao.offeringcode
        """)
        offerings_mgmt = to_dict(cur)

        cur.execute("""
            SELECT pyl.programyearlevelid, pyl.academicofferingid, pyl.yearlevel,
                   pyl.isactive, COALESCE(pyl.numberofsections, 1) AS numberofsections
            FROM   program_yearlevel pyl
            JOIN   academic_offering ao ON ao.academicofferingid = pyl.academicofferingid
            ORDER  BY pyl.academicofferingid, pyl.yearlevel
        """)
        yearlevel_data = to_dict(cur)

        return render_template('admin/settings_admin.html',
                               ay_list=ay_data, emp_types=emp_types,
                               designations=designations, designee_base=designee_base,
                               is_locked=is_locked, locked_detail=locked_detail,
                               sched_cfg=sched_cfg,
                               accounts=accounts, unlinked_employees=unlinked_employees,
                               programs_mgmt=programs_mgmt,
                               curricula_mgmt=curricula_mgmt,
                               sections_mgmt=sections_mgmt,
                               offerings_mgmt=offerings_mgmt,
                               yearlevel_data=yearlevel_data,
                               activity_logs=activity_logs)
    except Exception as e:
        flash(f"Error loading settings: {e}", "error")
        return redirect(url_for('admin_dashboard'))
    finally:
        cur.close(); conn.close()

@app.route('/admin/settings/update_scheduler_config', methods=['POST'])
def update_scheduler_config():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    keys = ['sc1_daytime','sc2_night','sc3_day_dist','sc4_compact',
            'sc5_pt_balance','sc6_weekend','sc7_consecutive','hc7_max_night']
    conn = get_db_connection(); cur = conn.cursor()
    try:
        for key in keys:
            val = request.form.get(key)
            if val is not None:
                cur.execute("""
                    INSERT INTO scheduler_config (config_key, config_value)
                    VALUES (%s, %s)
                    ON CONFLICT (config_key) DO UPDATE SET config_value = EXCLUDED.config_value
                """, (key, float(val)))
        conn.commit()
        flash("Scheduling constraint weights updated successfully.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error saving constraints: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))

@app.route('/admin/accounts/add', methods=['POST'])
def account_add():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    username   = request.form.get('username', '').strip()
    role       = request.form.get('role', '').strip()
    emp_number = request.form.get('employeenumber', '').strip() or None
    if not username or not role:
        flash("Username and role are required.", "error")
        return redirect(url_for('admin_settings'))
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM accounts WHERE LOWER(username) = LOWER(%s)", (username,))
        if cur.fetchone():
            flash(f"Username '{username}' already exists.", "error")
        else:
            default_pw = generate_password_hash(f"PUP@{username}")
            cur.execute("""
                INSERT INTO accounts (username, passwordhash, role, isactive, employeenumber)
                VALUES (%s, %s, %s, TRUE, %s)
            """, (username, default_pw, role, emp_number))
            conn.commit()
            flash(f"Account '{username}' created. Default password: PUP@{username}", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error creating account: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))

@app.route('/admin/accounts/reset_password', methods=['POST'])
def account_reset_password():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    user_id  = request.form.get('user_id')
    username = request.form.get('username', '').strip()
    conn = get_db_connection(); cur = conn.cursor()
    try:
        new_pw = generate_password_hash(f"PUP@{username}")
        cur.execute("UPDATE accounts SET passwordhash = %s WHERE userid = %s", (new_pw, user_id))
        conn.commit()
        flash(f"Password for '{username}' reset to: PUP@{username}", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error resetting password: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))

@app.route('/admin/accounts/toggle_status', methods=['POST'])
def account_toggle_status():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    user_id    = request.form.get('user_id')
    new_status = request.form.get('new_status') == 'true'
    conn = get_db_connection(); cur = conn.cursor()
    try:
        if not new_status:
            cur.execute("SELECT COUNT(*) FROM accounts WHERE role = 'Admin' AND isactive = TRUE AND userid != %s", (user_id,))
            if cur.fetchone()[0] == 0:
                flash("Cannot deactivate the last active admin account.", "error")
                return redirect(url_for('admin_settings'))
        cur.execute("UPDATE accounts SET isactive = %s WHERE userid = %s", (new_status, user_id))
        conn.commit()
        label = "activated" if new_status else "deactivated"
        flash(f"Account {label} successfully.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error updating account status: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/add_designation', methods=['POST'])
def add_designation():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    des_name = request.form.get('designation_name')
    reg_load = request.form.get('reg_load') or 0
    nt_service = request.form.get('night_teaching') or 0
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM Designation WHERE UPPER(DesignationName) = UPPER(%s)", (des_name,))
        if cur.fetchone():
            flash(f"Error: Designation '{des_name}' already exists.", "error")
        else:
            cur.execute("""
                INSERT INTO Designation (DesignationName, RegularLoadUnit, NightTeachingService)
                VALUES (%s, %s, %s)
            """, (des_name, reg_load, nt_service))
            conn.commit()
            flash(f"Successfully added {des_name}.", "success")
            write_activity_log(
                "Faculty Assignment Update",
                f'Added new designee assignment for {des_name} with {reg_load} credit limit',
                category='employee', color=_LOG_COLORS['employee']
            )
    except Exception as e:
        conn.rollback()
        flash(f"Database Error: {str(e)}", "error")
    finally:
        cur.close(); conn.close()

    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/activate_period', methods=['POST'])
def activate_period():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    ay_id = request.form.get('active_ay_select')
    sem_type = request.form.get('active_sem_select')
    today = date.today()

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 1. Reset everything to false first (Ensures only ONE is active)
        cur.execute("UPDATE AcademicYear SET IsActive = FALSE")
        cur.execute("UPDATE Semester SET IsActive = FALSE")
        
        # 2. Set the chosen ones to true
        cur.execute("UPDATE AcademicYear SET IsActive = TRUE WHERE AcademicYearID = %s", (ay_id,))
        cur.execute("UPDATE Semester SET IsActive = TRUE WHERE AcademicYearID = %s AND SemesterType = %s", (ay_id, sem_type))
        
        # 3. Check dates for warning only
        cur.execute("SELECT SemStartDate, SemEndDate FROM Semester WHERE AcademicYearID = %s AND SemesterType = %s", (ay_id, sem_type))
        res = cur.fetchone()
        
        conn.commit()

        if not res or not res[0] or not res[1]:
            flash(f"{ay_id} Activated, but please set dates to show on dashboard.", "warning")
        elif not (res[0] <= today <= res[1]):
            flash(f"{ay_id} Activated! Note: Today is outside the set date range.", "info")
        else:
            flash(f"System period updated to {ay_id} Successfully!", "success")

    except Exception as e:
        conn.rollback()
        flash(f"Error: {str(e)}", "error")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/finalize_ay', methods=['POST'])
def finalize_ay():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    ay_id = request.form.get('ay_id', '').strip()
    if not ay_id:
        flash("Invalid Academic Year.", "error")
        return redirect(url_for('admin_settings'))
    conn = get_db_connection(); cur = conn.cursor()
    try:
        _ensure_ay_finalized_col(cur)
        cur.execute("UPDATE academicyear SET isfinalized = TRUE WHERE academicyearid = %s", (ay_id,))
        conn.commit()
        flash(f"Academic Year {ay_id} has been finalized and is now locked.", "success")
        write_activity_log(
            "Finalized Academic Year",
            f"Academic Year {ay_id} has been marked as Finalized. Settings and schedules are now historically protected.",
            category='calendar', color=_LOG_COLORS.get('calendar', 'blue')
        )
    except Exception as e:
        conn.rollback()
        flash(f"Error finalizing Academic Year: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))


@app.route('/admin/settings/upsert_ay', methods=['POST'])
def upsert_ay():
    if session.get('role') != 'Admin': return redirect(url_for('login'))

    y_start = request.form.get('year_start')
    y_end = request.form.get('year_end')
    ay_id = f"AY{y_start[2:4]}{y_end[2:4]}"
    
    sem_configs =[
        ('1st Semester', request.form.get('sem1_start'), request.form.get('sem1_end'), 'A'),
        ('2nd Semester', request.form.get('sem2_start'), request.form.get('sem2_end'), 'B'),
        ('Summer', request.form.get('sem3_start'), request.form.get('sem3_end'), 'C')
    ]

    for label, s_start, s_end, s_type in sem_configs:
        if s_start and s_end:
            if s_end < s_start:
                flash(f"Validation Error: {label} end date cannot be earlier than start date.", "error")
                return redirect(url_for('admin_settings'))
            try:
                start_year_val = datetime.strptime(s_start, '%Y-%m-%d').year
                end_year_val = datetime.strptime(s_end, '%Y-%m-%d').year
                allowed_years =[int(y_start), int(y_end)]
                if start_year_val not in allowed_years or end_year_val not in allowed_years:
                    flash(f"Validation Error: {label} dates must fall within the years {y_start} or {y_end}.", "error")
                    return redirect(url_for('admin_settings'))
            except ValueError:
                pass 

    conn = get_db_connection(); cur = conn.cursor()
    try:
        _ensure_ay_finalized_col(cur)
        # Block save if this AY has been finalized
        cur.execute("SELECT COALESCE(isfinalized, FALSE) FROM academicyear WHERE academicyearid = %s", (ay_id,))
        existing = cur.fetchone()
        if existing and existing[0]:
            flash(f"Academic Year {ay_id} is finalized and cannot be edited.", "error")
            return redirect(url_for('admin_settings'))

        cur.execute("""
            INSERT INTO AcademicYear (AcademicYearID, YearStart, YearEnd, IsActive)
            VALUES (%s, %s, %s, FALSE)
            ON CONFLICT (AcademicYearID)
            DO UPDATE SET YearStart = EXCLUDED.YearStart, YearEnd = EXCLUDED.YearEnd
        """, (ay_id, int(y_start), int(y_end)))

        for label, s_start, s_end, s_type in sem_configs:
            final_start = s_start if s_start else None
            final_end = s_end if s_end else None
            cur.execute("""
                INSERT INTO Semester (AcademicYearID, SemesterType, SemStartDate, SemEndDate, IsActive)
                VALUES (%s, %s, %s, %s, FALSE)
                ON CONFLICT (AcademicYearID, SemesterType) 
                DO UPDATE SET SemStartDate = EXCLUDED.SemStartDate, SemEndDate = EXCLUDED.SemEndDate
            """, (ay_id, s_type, final_start, final_end))
            
        conn.commit()
        flash(f"Academic Year {ay_id} configuration saved successfully.", "success")
        sems_set = [lbl for lbl, ss, se, _ in sem_configs if ss and se]
        write_activity_log(
            "Created Academic Calendar",
            f'Registered start and end dates for Academic Year {y_start}-{y_end}'
            + (f' ({", ".join(sems_set)})' if sems_set else ''),
            category='calendar', color=_LOG_COLORS['calendar']
        )
    except Exception as e:
        conn.rollback()
        flash(f"Database Error: {str(e)}", "error")
    finally:
        cur.close(); conn.close()
        
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/update_emp_type', methods=['POST'])
def update_emp_type():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM Schedule s JOIN Semester sem ON s.SemesterID = sem.SemesterID WHERE sem.IsActive = TRUE")
        if cur.fetchone()[0] > 0:
            flash("Cannot edit settings while an active schedule exists.", "error")
            return redirect(url_for('admin_settings'))

        et_id = request.form.get('emp_type_id')
        r_load = request.form.get('reg_load') or None
        pt_load = request.form.get('pt_load') or None
        sub_load = request.form.get('sub_load') or None
        rs = request.form.get('reg_start') or None
        re = request.form.get('reg_end') or None
        ps = request.form.get('pt_start') or None
        pe = request.form.get('pt_end') or None

        cur.execute("""
            UPDATE EmployeeType 
            SET RegularLoad=%s, PartTimeLoad=%s, TeachingSubstitution=%s, 
                Regular_Start=%s, Regular_End=%s, PartTime_Start=%s, PartTime_End=%s
            WHERE EmployeeTypeID=%s
        """, (r_load, pt_load, sub_load, rs, re, ps, pe, et_id))
        conn.commit()
        flash("Employee Type constraints updated.", "success")
        write_activity_log(
            "Updated Faculty Hour Limits",
            f'Modified Regular Teaching Hour parameters for Employee Type ID {et_id}',
            category='settings', color=_LOG_COLORS['settings']
        )
    except Exception as e:
        conn.rollback()
        flash(f"Error: {str(e)}", "error")
    finally:
        cur.close(); conn.close()
        
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/update_designation', methods=['POST'])
def update_designation():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        des_id     = request.form.get('designation_id')
        reg_load   = request.form.get('reg_load') or 0
        nt_service = request.form.get('night_teaching') or 0

        # Fetch designation name for the log message
        cur.execute("SELECT DesignationName FROM Designation WHERE DesignationID=%s", (des_id,))
        row = cur.fetchone()
        des_name = row[0] if row else f"ID {des_id}"

        cur.execute("""
            UPDATE Designation
            SET RegularLoadUnit=%s, NightTeachingService=%s
            WHERE DesignationID=%s
        """, (reg_load, nt_service, des_id))
        conn.commit()
        flash("Designation rules updated.", "success")
        write_activity_log(
            "Updated Designee Rules",
            f'Modified Regular Teaching Hour parameters for "{des_name}"',
            category='settings', color=_LOG_COLORS['settings']
        )
    except Exception as e:
        conn.rollback()
        flash(f"Error: {str(e)}", "error")
    finally:
        cur.close(); conn.close()

    return redirect(url_for('admin_settings'))

## ── Hard Constraints API ─────────────────────────────────

@app.route('/admin/settings/hard_constraints', methods=['GET'])
def get_hard_constraints():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    from database import load_scheduler_config
    try:
        cfg = load_scheduler_config()
        # Return only hc_* keys (plus a few numeric ones the UI needs)
        result = {k: v for k, v in cfg.items() if k.startswith('hc_')}
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/settings/hard_constraints', methods=['POST'])
def save_hard_constraints():
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json(silent=True) or {}
    conn = get_db_connection(); cur = conn.cursor()
    try:
        for key, val in data.items():
            if not key.startswith('hc_'):
                continue
            # Ensure scheduler_config table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS scheduler_config (
                    config_key   VARCHAR(60) PRIMARY KEY,
                    config_value TEXT NOT NULL
                )
            """)
            cur.execute("""
                INSERT INTO scheduler_config (config_key, config_value)
                VALUES (%s, %s)
                ON CONFLICT (config_key) DO UPDATE
                    SET config_value = EXCLUDED.config_value
            """, (key, str(val)))
        conn.commit()
        write_activity_log(
            "Updated Hard Constraints",
            "Modified scheduling hard-constraint configuration from Settings",
            category='settings', color=_LOG_COLORS['settings']
        )
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()

## ── Program Management CRUD ──────────────────────────────

@app.route('/admin/settings/program/add', methods=['POST'])
def settings_add_program():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    code      = request.form.get('program_code', '').strip().upper()
    name      = request.form.get('program_name', '').strip()
    ptype     = request.form.get('program_type', 'Undergraduate').strip()
    yrs       = request.form.get('num_year_level', 4)
    conn = get_db_connection(); cur = conn.cursor()
    try:
        if not code or not name:
            flash("Program code and name are required.", "error")
            return redirect(url_for('admin_settings') + '#tab-program')
        cur.execute("SELECT 1 FROM Programs WHERE ProgramCode = %s", (code,))
        if cur.fetchone():
            flash(f"Program code '{code}' already exists.", "error")
        else:
            cur.execute("""
                INSERT INTO Programs (ProgramCode, ProgramName, ProgramType, IsActive, NumYearLevel)
                VALUES (%s, %s, %s, TRUE, %s)
            """, (code, name, ptype, int(yrs)))
            conn.commit()
            flash(f"Program '{code}' added successfully.", "success")
            write_activity_log("Added Academic Program", f'Created new program: {code} — {name}',
                               category='program', color=_LOG_COLORS['program'])
    except Exception as e:
        conn.rollback(); flash(f"Error adding program: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/program/edit', methods=['POST'])
def settings_edit_program():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    code  = request.form.get('program_code', '').strip()
    name  = request.form.get('program_name', '').strip()
    ptype = request.form.get('program_type', 'Undergraduate').strip()
    yrs   = request.form.get('num_year_level', 4)
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE Programs
            SET ProgramName=%s, ProgramType=%s, NumYearLevel=%s
            WHERE ProgramCode=%s
        """, (name, ptype, int(yrs), code))
        conn.commit()
        flash(f"Program '{code}' updated.", "success")
        write_activity_log("Updated Program Configuration", f'Modified program details for {code}: {name}',
                           category='settings', color=_LOG_COLORS['settings'])
    except Exception as e:
        conn.rollback(); flash(f"Error updating program: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/program/delete', methods=['POST'])
def settings_delete_program():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    code = request.form.get('program_code', '').strip()
    conn = get_db_connection(); cur = conn.cursor()
    try:
        # ── Block deactivation if program has sections in the current active semester ──
        cur.execute("""
            SELECT COUNT(*) FROM schedule sc
            JOIN semester sem ON sem.semesterid = sc.semesterid
            JOIN sections sec ON sec.sectionid = sc.sectionid
            JOIN program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
            JOIN academic_offering ao ON ao.academicofferingid = pyl.academicofferingid
            WHERE ao.programcode = %s AND sem.isactive = TRUE
        """, (code,))
        if cur.fetchone()[0] > 0:
            conn.rollback()
            flash(f"Cannot deactivate program '{code}': it has sections assigned to the active academic year schedule.", "error")
            return redirect(url_for('admin_settings'))

        # ── Step 1: Snapshot current states before cascade ──────
        cur.execute("SELECT academicofferingid FROM academic_offering WHERE programcode=%s", (code,))
        ao_ids_snap = [row[0] for row in cur.fetchall()]
        if ao_ids_snap:
            cur.execute("""
                UPDATE academic_offering SET snapshot_isactive = isactive
                WHERE academicofferingid = ANY(%s)
            """, (ao_ids_snap,))
            cur.execute("""
                SELECT programyearlevelid FROM program_yearlevel
                WHERE academicofferingid = ANY(%s)
            """, (ao_ids_snap,))
            pyl_ids_snap = [row[0] for row in cur.fetchall()]
            if pyl_ids_snap:
                cur.execute("""
                    UPDATE program_yearlevel SET snapshot_isactive = isactive
                    WHERE programyearlevelid = ANY(%s)
                """, (pyl_ids_snap,))
                cur.execute("""
                    UPDATE sections SET snapshot_isactive = isactive
                    WHERE programyearlevelid = ANY(%s)
                """, (pyl_ids_snap,))

        # ── Step 2: Cascade deactivation ────────────────────────
        cur.execute("UPDATE programs SET isactive=FALSE WHERE programcode=%s", (code,))
        cur.execute("""
            UPDATE academic_offering SET isactive=FALSE
            WHERE programcode=%s
            RETURNING academicofferingid
        """, (code,))
        ao_ids = [row[0] for row in cur.fetchall()]

        pyl_ids = []
        if ao_ids:
            cur.execute("""
                UPDATE program_yearlevel SET isactive=FALSE
                WHERE academicofferingid = ANY(%s)
                RETURNING programyearlevelid
            """, (ao_ids,))
            pyl_ids = [row[0] for row in cur.fetchall()]

        if pyl_ids:
            cur.execute("""
                UPDATE sections SET isactive=FALSE
                WHERE programyearlevelid = ANY(%s)
            """, (pyl_ids,))

        conn.commit()
        flash(f"Program '{code}' deactivated along with its offerings, year levels, and sections.", "success")
        write_activity_log("Deactivated Program",
                           f'Program {code} and all its academic offerings/year levels/sections marked inactive',
                           category='program', color=_LOG_COLORS['program'])
    except Exception as e:
        conn.rollback(); flash(f"Error deactivating program: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/curriculum/add', methods=['POST'])
def settings_add_curriculum():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    pcode  = request.form.get('program_code', '').strip()
    year   = request.form.get('curriculum_year', '').strip()
    code   = request.form.get('curriculum_code', '').strip().upper()
    conn = get_db_connection(); cur = conn.cursor()
    try:
        if not code:
            # Auto-generate from year e.g. "2025-2026" → "CY2526"
            parts = year.replace(' ','').split('-')
            code = 'CY' + (parts[0][-2:] if parts else '') + (parts[1][-2:] if len(parts)>1 else '')
        cur.execute("SELECT academicofferingid FROM academic_offering WHERE offeringcode = %s LIMIT 1", (pcode,))
        ao_row = cur.fetchone()
        if not ao_row:
            flash(f"No academic offering found for '{pcode}'.", "error")
            return redirect(url_for('admin_settings'))
        ao_id = ao_row[0]
        cur.execute("""
            INSERT INTO curriculum (curriculumcode, academicofferingid, curriculumyear)
            VALUES (%s, %s, %s)
        """, (code, ao_id, year))
        conn.commit()
        flash(f"Curriculum track '{code}' added to {pcode}.", "success")
        write_activity_log("Added Curriculum Track",
                           f'Created curriculum track {code} ({year}) for program {pcode}',
                           category='curriculum', color=_LOG_COLORS['curriculum'])
    except Exception as e:
        conn.rollback(); flash(f"Error adding curriculum: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/curriculum/edit', methods=['POST'])
def settings_edit_curriculum():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    cid  = request.form.get('curriculum_id')
    year = request.form.get('curriculum_year', '').strip()
    code = request.form.get('curriculum_code', '').strip().upper()
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE Curriculum SET CurriculumCode=%s, CurriculumYear=%s
            WHERE CurriculumID=%s
        """, (code, year, cid))
        conn.commit()
        flash("Curriculum track updated.", "success")
        write_activity_log("Updated Curriculum Track",
                           f'Modified curriculum ID {cid} to code {code}, year {year}',
                           category='curriculum', color=_LOG_COLORS['curriculum'])
    except Exception as e:
        conn.rollback(); flash(f"Error updating curriculum: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/curriculum/delete', methods=['POST'])
def settings_delete_curriculum():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    cid = request.form.get('curriculum_id')
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM CurriculumSubject WHERE CurriculumID=%s", (cid,))
        if cur.fetchone()[0] > 0:
            flash("Cannot delete a curriculum that has subjects assigned to it.", "warning")
        else:
            cur.execute("DELETE FROM Curriculum WHERE CurriculumID=%s", (cid,))
            conn.commit()
            flash("Curriculum track deleted.", "success")
            write_activity_log("Deleted Curriculum Track",
                               f'Removed empty curriculum ID {cid}',
                               category='curriculum', color=_LOG_COLORS['curriculum'])
    except Exception as e:
        conn.rollback(); flash(f"Error deleting curriculum: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))

## ── Section Management CRUD ─────────────────────────────────

@app.route('/admin/settings/section/add', methods=['POST'])
def settings_add_section():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    prog_code    = request.form.get('program_code', '').strip().upper()
    year_level   = int(request.form.get('year_level', '1'))
    section_name = request.form.get('section_name', '').strip()
    if not prog_code or not section_name:
        flash("Program code and section name are required.", "error")
        return redirect(url_for('admin_settings'))
    conn = get_db_connection(); cur = conn.cursor()
    try:
        # Resolve current AY
        cur.execute("SELECT academicyearid FROM academicyear ORDER BY yearstart DESC NULLS LAST LIMIT 1")
        ay_row = cur.fetchone()
        if not ay_row:
            flash("No academic year configured. Please add an academic year first.", "warning")
            return redirect(url_for('admin_settings'))
        ay_id = ay_row[0]

        # Find the program_yearlevel row for this offering + AY + year level.
        # Try exact match on programcode (programs without tracks) first,
        # then widen to any offering under this program.
        cur.execute("""
            SELECT pyl.programyearlevelid
            FROM program_yearlevel pyl
            JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
            WHERE UPPER(ao.offeringcode) = UPPER(%s)
              AND pyl.academicyearid = %s
              AND pyl.yearlevel = %s
            LIMIT 1
        """, (prog_code, ay_id, year_level))
        pyl_row = cur.fetchone()

        if not pyl_row:
            # Widen: any offering whose programcode matches
            cur.execute("""
                SELECT pyl.programyearlevelid
                FROM program_yearlevel pyl
                JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
                WHERE UPPER(ao.programcode) = UPPER(%s)
                  AND pyl.academicyearid = %s
                  AND pyl.yearlevel = %s
                ORDER BY ao.offeringcode
                LIMIT 1
            """, (prog_code, ay_id, year_level))
            pyl_row = cur.fetchone()

        if not pyl_row:
            # Auto-create program_yearlevel using the latest curriculum for this program
            cur.execute("""
                SELECT ao.academicofferingid, c.curriculumid
                FROM academic_offering ao
                LEFT JOIN curriculum c ON c.academicofferingid = ao.academicofferingid
                WHERE UPPER(ao.programcode) = UPPER(%s)
                ORDER BY c.curriculumyear DESC NULLS LAST
                LIMIT 1
            """, (prog_code,))
            off_row = cur.fetchone()
            if not off_row:
                flash(f"No academic offering found for '{prog_code}'. Set up program offerings first.", "warning")
                return redirect(url_for('admin_settings'))
            ao_id    = off_row[0]
            curr_id  = off_row[1]
            cur.execute("""
                INSERT INTO program_yearlevel
                    (academicofferingid, academicyearid, yearlevel, curriculumid, isactive)
                VALUES (%s, %s, %s, %s, TRUE)
                ON CONFLICT (academicofferingid, academicyearid, yearlevel)
                DO UPDATE SET isactive = TRUE
                RETURNING programyearlevelid
            """, (ao_id, ay_id, year_level, curr_id))
            pyl_row = cur.fetchone()

        pyl_id = pyl_row[0]

        # Guard: inactive year level blocks manual section creation
        cur.execute("SELECT isactive FROM program_yearlevel WHERE programyearlevelid = %s", (pyl_id,))
        active_check = cur.fetchone()
        if active_check and not active_check[0]:
            conn.commit()
            flash(f"Year {year_level} is inactive for this offering. Activate it first.", "warning")
            return redirect(url_for('admin_settings'))

        # Duplicate check
        cur.execute("""
            SELECT 1 FROM sections
            WHERE programyearlevelid = %s AND LOWER(sectionname) = LOWER(%s)
        """, (pyl_id, section_name))
        if cur.fetchone():
            conn.commit()
            flash(f"Section '{section_name}' already exists for Year {year_level}.", "warning")
        else:
            cur.execute("""
                INSERT INTO sections (programyearlevelid, sectionname, isactive)
                VALUES (%s, %s, TRUE)
            """, (pyl_id, section_name))
            conn.commit()
            flash(f"Section '{section_name}' added for Year {year_level}.", "success")
            write_activity_log("Added Section",
                               f"Added section '{section_name}' (Year {year_level}) to program {prog_code}",
                               category='program', color=_LOG_COLORS.get('program', 'blue'))
    except Exception as e:
        conn.rollback(); flash(f"Error adding section: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))


@app.route('/admin/settings/section/deactivate', methods=['POST'])
def settings_deactivate_section():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    section_id = request.form.get('section_id')
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("UPDATE sections SET isactive=FALSE WHERE sectionid=%s", (section_id,))
        conn.commit()
        flash("Section deactivated.", "success")
        write_activity_log("Deactivated Section",
                           f"Section ID {section_id} marked as inactive",
                           category='program', color=_LOG_COLORS.get('program', 'blue'))
    except Exception as e:
        conn.rollback(); flash(f"Error deactivating section: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))


@app.route('/admin/settings/program/toggle-active', methods=['POST'])
def settings_toggle_program_active():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    prog_code = request.form.get('program_code', '').strip().upper()
    is_active = request.form.get('isactive', 'false').lower() == 'true'
    if not prog_code:
        return jsonify({'success': False, 'error': 'Missing program_code'})
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        if is_active:
            # Reactivation: restore child entities to their pre-deactivation snapshot states.
            cur.execute("UPDATE programs SET isactive=TRUE WHERE programcode=%s", (prog_code,))

            cur.execute("SELECT academicofferingid FROM academic_offering WHERE programcode=%s", (prog_code,))
            ao_ids = [row[0] for row in cur.fetchall()]
            if ao_ids:
                cur.execute("SELECT programyearlevelid FROM program_yearlevel WHERE academicofferingid = ANY(%s)", (ao_ids,))
                pyl_ids = [row[0] for row in cur.fetchall()]

                if pyl_ids:
                    # 1. Restore sections from snapshot
                    cur.execute("""
                        UPDATE sections
                        SET isactive = COALESCE(snapshot_isactive, FALSE),
                            snapshot_isactive = NULL
                        WHERE programyearlevelid = ANY(%s)
                    """, (pyl_ids,))

                # 2. Restore year levels from snapshot
                cur.execute("""
                    UPDATE program_yearlevel
                    SET isactive = COALESCE(snapshot_isactive, FALSE),
                        snapshot_isactive = NULL
                    WHERE academicofferingid = ANY(%s)
                """, (ao_ids,))

                if pyl_ids:
                    # 3. Cascade: year levels with no active sections must remain inactive
                    cur.execute("""
                        UPDATE program_yearlevel pyl
                        SET isactive = FALSE
                        WHERE pyl.programyearlevelid = ANY(%s)
                          AND NOT EXISTS (
                              SELECT 1 FROM sections s
                              WHERE s.programyearlevelid = pyl.programyearlevelid
                                AND s.isactive = TRUE
                          )
                    """, (pyl_ids,))

                # 4. Restore academic offerings from snapshot
                cur.execute("""
                    UPDATE academic_offering
                    SET isactive = COALESCE(snapshot_isactive, FALSE),
                        snapshot_isactive = NULL
                    WHERE programcode = %s
                """, (prog_code,))

                # 5. Cascade: offerings with no active year levels must remain inactive
                cur.execute("""
                    UPDATE academic_offering ao
                    SET isactive = FALSE
                    WHERE ao.programcode = %s
                      AND NOT EXISTS (
                          SELECT 1 FROM program_yearlevel pyl
                          WHERE pyl.academicofferingid = ao.academicofferingid
                            AND pyl.isactive = TRUE
                      )
                """, (prog_code,))
        else:
            # Deactivation via AJAX path — cascade handled by settings_delete_program (form POST).
            cur.execute("UPDATE programs SET isactive=FALSE WHERE programcode=%s", (prog_code,))

        conn.commit()
        action = 'activated' if is_active else 'deactivated'
        write_activity_log(
            f'Program {action.capitalize()}',
            f'Program {prog_code} was {action} via Settings',
            category='program',
            color=_LOG_COLORS.get('program', 'blue')
        )
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        cur.close(); conn.close()


@app.route('/admin/settings/section/edit', methods=['POST'])
def settings_edit_section():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    section_id = request.form.get('section_id', '').strip()
    pyl_id     = request.form.get('pyl_id',     '').strip()
    new_name   = request.form.get('section_name', '').strip()
    if not new_name:
        return jsonify({'success': False, 'error': 'Section name is required.'})
    conn = None; cur = None
    try:
        conn = get_db_connection(); cur = conn.cursor()
        if section_id:
            cur.execute(
                "UPDATE sections SET sectionname=%s WHERE sectionid=%s RETURNING sectionid, sectionname",
                (new_name, section_id)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({'success': False, 'error': 'Section not found.'})
            sid, sname = row[0], row[1]
        elif pyl_id:
            # Materialise virtual section with the new name
            cur.execute("""
                INSERT INTO sections (programyearlevelid, sectionname, isactive)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (programyearlevelid, sectionname) DO UPDATE
                    SET sectionname = EXCLUDED.sectionname
                RETURNING sectionid, sectionname
            """, (pyl_id, new_name))
            row = cur.fetchone()
            sid, sname = row[0], row[1]
        else:
            return jsonify({'success': False, 'error': 'Missing section_id or pyl_id.'})
        conn.commit()
        try:
            write_activity_log("Renamed Section",
                               f"Section renamed to '{sname}'",
                               category='program', color=_LOG_COLORS.get('program', 'blue'))
        except: pass
        return jsonify({'success': True, 'sectionid': sid, 'sectionname': sname})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        if cur:  cur.close()
        if conn: conn.close()


@app.route('/admin/settings/section/delete', methods=['POST'])
def settings_delete_section():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    section_id = request.form.get('section_id', '').strip()
    conn = None; cur = None
    try:
        conn = get_db_connection(); cur = conn.cursor()
        if not section_id:
            return jsonify({'success': False, 'error': 'Section ID is required.'})

        # Resolve full hierarchy IDs
        cur.execute("""
            SELECT s.programyearlevelid, ao.academicofferingid, ao.programcode
            FROM sections s
            JOIN program_yearlevel pyl ON s.programyearlevelid = pyl.programyearlevelid
            JOIN academic_offering ao  ON pyl.academicofferingid = ao.academicofferingid
            WHERE s.sectionid = %s
        """, (section_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Section not found.'})
        pyl_id, ao_id, prog_code = row

        # Check current-semester schedule (blocks deactivation entirely)
        cur.execute("""
            SELECT COUNT(*) FROM schedule sc
            JOIN semester sem ON sem.semesterid = sc.semesterid
            WHERE sc.sectionid = %s AND sem.isactive = TRUE
        """, (section_id,))
        if cur.fetchone()[0] > 0:
            return jsonify({'success': False,
                            'error': 'Cannot deactivate this section: it is assigned to the active academic year schedule.'})

        # Check any historical schedule (blocks hard delete, allows soft delete)
        cur.execute("SELECT COUNT(*) FROM schedule WHERE sectionid=%s", (section_id,))
        sch_count = cur.fetchone()[0]
        if sch_count > 0:
            cur.execute("UPDATE sections SET isactive=FALSE WHERE sectionid=%s", (section_id,))
            mode = 'soft'
        else:
            cur.execute("DELETE FROM sections WHERE sectionid=%s", (section_id,))
            mode = 'hard'

        # ── Recalculate active section count and update year level ──
        cur.execute("SELECT COUNT(*) FROM sections WHERE programyearlevelid=%s AND isactive=TRUE", (pyl_id,))
        new_sec_count = cur.fetchone()[0]
        cur.execute("UPDATE program_yearlevel SET numberofsections=%s WHERE programyearlevelid=%s",
                    (new_sec_count, pyl_id))

        # ── Cascade: deactivate year level if no active sections ──
        if new_sec_count == 0:
            cur.execute("UPDATE program_yearlevel SET isactive=FALSE WHERE programyearlevelid=%s", (pyl_id,))
        cur.execute("SELECT isactive FROM program_yearlevel WHERE programyearlevelid=%s", (pyl_id,))
        pyl_active = bool(cur.fetchone()[0])

        # ── Cascade: deactivate offering if no active year levels ──
        cur.execute("SELECT COUNT(*) FROM program_yearlevel WHERE academicofferingid=%s AND isactive=TRUE", (ao_id,))
        if cur.fetchone()[0] == 0:
            cur.execute("UPDATE academic_offering SET isactive=FALSE WHERE academicofferingid=%s", (ao_id,))
        cur.execute("SELECT isactive FROM academic_offering WHERE academicofferingid=%s", (ao_id,))
        ao_active = bool(cur.fetchone()[0])

        # ── Cascade: deactivate program if no active offerings ──
        cur.execute("SELECT COUNT(*) FROM academic_offering WHERE programcode=%s AND isactive=TRUE", (prog_code,))
        if cur.fetchone()[0] == 0:
            cur.execute("UPDATE programs SET isactive=FALSE WHERE programcode=%s", (prog_code,))
        cur.execute("SELECT isactive FROM programs WHERE programcode=%s", (prog_code,))
        prog_active = bool(cur.fetchone()[0])

        conn.commit()
        try:
            write_activity_log("Deleted Section",
                               f"Section ID {section_id} {'deactivated' if mode=='soft' else 'removed'}; "
                               f"YL {pyl_id} count→{new_sec_count}",
                               category='program', color=_LOG_COLORS.get('program', 'blue'))
        except: pass

        return jsonify({
            'success':          True,
            'mode':             mode,
            'sectionid':        int(section_id),
            'pyl_id':           pyl_id,
            'numberofsections': new_sec_count,
            'pyl_isactive':     pyl_active,
            'ao_id':            ao_id,
            'ao_isactive':      ao_active,
            'prog_code':        prog_code,
            'prog_isactive':    prog_active,
        })
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        if cur:  cur.close()
        if conn: conn.close()


@app.route('/admin/settings/offering/add', methods=['POST'])
def settings_add_offering():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    prog_code  = request.form.get('program_code', '').strip().upper()
    track_name = request.form.get('track_name', '').strip()
    short_code = request.form.get('short_code', '').strip().upper()
    track_type = request.form.get('track_type', 'Track').strip()
    is_active  = request.form.get('status') == '1'

    debug_log = {
        'input': {
            'prog_code': prog_code, 'track_name': track_name,
            'short_code': short_code, 'track_type': track_type, 'is_active': is_active,
        }
    }

    if not prog_code or not track_name or not short_code:
        return jsonify({'success': False, 'error': 'Program, track name, and short code are all required.', 'log': debug_log})

    conn = None; cur = None
    try:
        conn = get_db_connection(); cur = conn.cursor()
        # Validate: duplicate track code
        cur.execute("SELECT 1 FROM track WHERE UPPER(trackcode) = UPPER(%s)", (short_code,))
        if cur.fetchone():
            return jsonify({'success': False, 'error': f"Track code '{short_code}' already exists.", 'log': debug_log})

        # Validate: duplicate track name under same program
        cur.execute("""
            SELECT 1 FROM track
            WHERE UPPER(parentprogramcode) = UPPER(%s) AND LOWER(trackname) = LOWER(%s)
        """, (prog_code, track_name))
        if cur.fetchone():
            return jsonify({'success': False, 'error': f"Track '{track_name}' already exists for program '{prog_code}'.", 'log': debug_log})

        # ── STEP 1: Create Track ──────────────────────────────
        cur.execute("""
            INSERT INTO track (trackcode, trackname, parentprogramcode, tracktype, isactive)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING trackid
        """, (short_code, track_name, prog_code, track_type, is_active))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return jsonify({'success': False, 'error': 'Track insert returned no ID — creation failed.', 'log': debug_log})
        track_id = row[0]
        debug_log['track_id'] = track_id
        print(f"[AddOffering] STEP 1 — Track created: trackid={track_id}, code={short_code}")

        # ── STEP 2: Ensure base offering exists for program ───
        cur.execute("SELECT academicofferingid FROM academic_offering WHERE programcode=%s AND trackid IS NULL", (prog_code,))
        if not cur.fetchone():
            cur.execute("SELECT programname FROM programs WHERE programcode=%s", (prog_code,))
            prow = cur.fetchone()
            prog_name = prow[0] if prow else prog_code
            cur.execute("""
                INSERT INTO academic_offering (programcode, trackid, offeringcode, offeringdescription, isactive)
                VALUES (%s, NULL, %s, %s, TRUE)
            """, (prog_code, prog_code, prog_name))
            print(f"[AddOffering] STEP 2 — Base offering created for {prog_code}")

        # ── STEP 3: Create Academic Offering ─────────────────
        offering_code = f"{prog_code}-{short_code}"
        offering_desc = f"{prog_code} - {track_name}"
        cur.execute("""
            INSERT INTO academic_offering (programcode, trackid, offeringcode, offeringdescription, isactive)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING academicofferingid
        """, (prog_code, track_id, offering_code, offering_desc, is_active))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return jsonify({'success': False, 'error': 'Academic offering insert returned no ID — creation failed.', 'log': debug_log})
        ao_id = row[0]
        debug_log['academicofferingid'] = ao_id
        debug_log['offeringcode'] = offering_code
        print(f"[AddOffering] STEP 3 — Academic Offering created: ao_id={ao_id}, code={offering_code}")

        # Verify offering exists
        cur.execute("SELECT academicofferingid FROM academic_offering WHERE academicofferingid=%s", (ao_id,))
        if not cur.fetchone():
            conn.rollback()
            return jsonify({'success': False, 'error': 'Offering verification failed — record not found after insert.', 'log': debug_log})

        # ── STEP 4: Resolve number of year levels + active AY ─
        cur.execute("SELECT numyearlevel FROM programs WHERE programcode=%s", (prog_code,))
        prow = cur.fetchone()
        num_yr = int(prow[0]) if prow and prow[0] else 4
        debug_log['num_yr'] = num_yr

        cur.execute("SELECT academicyearid FROM academicyear WHERE isactive=TRUE LIMIT 1")
        ay_row = cur.fetchone()
        if not ay_row:
            cur.execute("SELECT academicyearid FROM academicyear ORDER BY yearstart DESC NULLS LAST LIMIT 1")
            ay_row = cur.fetchone()
        if not ay_row:
            conn.rollback()
            return jsonify({'success': False, 'error': 'No academic year found. Please configure an academic year first.', 'log': debug_log})
        ay_id = ay_row[0]
        debug_log['academicyearid'] = ay_id
        print(f"[AddOffering] STEP 4 — Program has {num_yr} year levels, AY={ay_id}")

        # ── STEP 5: Create Program Year Level records ─────────
        pyl_ids = []
        section_counts = []
        for yr in range(1, num_yr + 1):
            raw_sec = request.form.get(f'sections_yr_{yr}', '1')
            sec_count = max(1, int(raw_sec) if str(raw_sec).isdigit() else 1)
            yr_active = request.form.get(f'active_yr_{yr}') == '1'
            cur.execute("""
                INSERT INTO program_yearlevel
                    (academicofferingid, academicyearid, yearlevel, isactive, numberofsections)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT uq_program_yearlevel DO UPDATE
                    SET numberofsections = EXCLUDED.numberofsections,
                        isactive         = EXCLUDED.isactive
                RETURNING programyearlevelid
            """, (ao_id, ay_id, yr, yr_active, sec_count))
            pyl_row = cur.fetchone()
            if not pyl_row:
                conn.rollback()
                return jsonify({'success': False, 'error': f'Year level {yr} insert failed — no ID returned.', 'log': debug_log})
            pyl_ids.append(pyl_row[0])
            section_counts.append({'year': yr, 'sections': sec_count, 'active': yr_active})
            print(f"[AddOffering] STEP 5 — Year {yr}: pyl_id={pyl_row[0]}, sections={sec_count}, active={yr_active}")

        # Verify at least one year level was created
        cur.execute("SELECT COUNT(*) FROM program_yearlevel WHERE academicofferingid=%s", (ao_id,))
        pyl_count = cur.fetchone()[0]
        if pyl_count == 0:
            conn.rollback()
            return jsonify({'success': False, 'error': 'No year level records were created. Transaction rolled back.', 'log': debug_log})

        debug_log['programyearlevelids'] = pyl_ids
        debug_log['section_counts'] = section_counts

        # ── STEP 6: Auto-create section records ────────────────
        _LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        created_sections = []
        for i, sc in enumerate(section_counts):
            if not sc['active']:
                continue
            pyl_id = pyl_ids[i]
            for j in range(sc['sections']):
                sec_name = f"{offering_code}-{sc['year']}{_LETTERS[j]}"
                cur.execute("""
                    INSERT INTO sections (programyearlevelid, sectionname, isactive)
                    VALUES (%s, %s, TRUE)
                    ON CONFLICT (programyearlevelid, sectionname) DO NOTHING
                    RETURNING sectionid
                """, (pyl_id, sec_name))
                row = cur.fetchone()
                if row:
                    created_sections.append({
                        'sectionid':          row[0],
                        'sectionname':        sec_name,
                        'programyearlevelid': pyl_id,
                        'academicofferingid': ao_id,
                        'yearlevel':          sc['year'],
                        'programcode':        offering_code,
                        'isactive':           True,
                    })
            print(f"[AddOffering] STEP 6 — Year {sc['year']}: inserted {sc['sections']} section(s)")

        # Validation: verify section counts match exactly
        for i, sc in enumerate(section_counts):
            if not sc['active']:
                continue
            pyl_id = pyl_ids[i]
            cur.execute(
                "SELECT COUNT(*) FROM sections WHERE programyearlevelid=%s AND isactive=TRUE",
                (pyl_id,)
            )
            actual = cur.fetchone()[0]
            if actual != sc['sections']:
                conn.rollback()
                return jsonify({
                    'success': False,
                    'error': (f"Section sync failed for Year {sc['year']}: "
                              f"expected {sc['sections']}, found {actual}. Transaction rolled back."),
                    'log': debug_log,
                })

        debug_log['created_sections'] = len(created_sections)

        # ── Enforce hierarchy: active offering requires at least one active year level ──
        active_yls_count = sum(1 for sc in section_counts if sc['active'])
        if active_yls_count == 0 and is_active:
            conn.rollback()
            return jsonify({
                'success': False,
                'error': 'An Academic Offering must contain at least one active Program Year Level before it can be activated.',
                'log': debug_log,
            })

        # ── Auto-activate parent program when offering is active ──
        if is_active:
            cur.execute("UPDATE programs SET isactive=TRUE WHERE programcode=%s AND isactive=FALSE", (prog_code,))

        conn.commit()
        print(f"[AddOffering] COMMIT — Offering '{offering_code}' fully created with {len(created_sections)} sections.")

        # ── Build response payload (matches offerings_mgmt format) ──
        total_sections = sum(s['sections'] for s in section_counts if s['active'])
        offering_payload = {
            'academicofferingid': ao_id,
            'offeringcode':       offering_code,
            'offeringdescription': offering_desc,
            'programcode':        prog_code,
            'isactive':           is_active,
            'trackname':          track_name,
            'trackcode':          short_code,
            'tracktype':          track_type,
            'yearlevel_count':    pyl_count,
            'section_count':      total_sections,
        }
        yearlevel_payload = [
            {
                'programyearlevelid': pyl_ids[i],
                'academicofferingid': ao_id,
                'yearlevel':          section_counts[i]['year'],
                'isactive':           section_counts[i]['active'],
                'numberofsections':   section_counts[i]['sections'],
            }
            for i in range(len(pyl_ids))
        ]

        try:
            write_activity_log("Added Academic Offering",
                               f"Created {track_type} offering {offering_code} ({track_name}) for {prog_code}",
                               category='program', color=_LOG_COLORS.get('program', 'blue'))
        except Exception:
            pass  # Activity log failure must not affect the JSON response

        cur.execute("SELECT isactive FROM programs WHERE programcode=%s", (prog_code,))
        prog_row = cur.fetchone()
        prog_isactive = bool(prog_row[0]) if prog_row else True

        return jsonify({
            'success':      True,
            'message':      f"Offering '{offering_code}' created successfully with {pyl_count} year levels and {len(created_sections)} sections.",
            'offering':     offering_payload,
            'yearlevels':   yearlevel_payload,
            'sections':     created_sections,
            'prog_isactive': prog_isactive,
            'log':          debug_log,
        })

    except Exception as e:
        if conn:
            try: conn.rollback()
            except Exception: pass
        import traceback
        print(f"[AddOffering] ERROR: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e), 'log': debug_log})
    finally:
        if cur:  cur.close()
        if conn: conn.close()


@app.route('/admin/settings/offering/edit', methods=['POST'])
def settings_edit_offering():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    ao_id      = request.form.get('offering_id')
    track_name = request.form.get('track_name', '').strip()
    track_code = request.form.get('track_code', '').strip().upper()
    is_active  = request.form.get('status') == '1'

    if not ao_id:
        return jsonify({'success': False, 'error': 'Offering ID is required.'})

    conn = None; cur = None
    try:
        conn = get_db_connection(); cur = conn.cursor()

        # Read current AO state and programcode before any changes
        cur.execute("SELECT programcode, isactive FROM academic_offering WHERE academicofferingid=%s", (ao_id,))
        ao_state = cur.fetchone()
        if not ao_state:
            return jsonify({'success': False, 'error': 'Offering not found.'})
        prog_code, prev_ao_active = ao_state

        # Read current YL states for schedule protection
        cur.execute("""
            SELECT programyearlevelid, yearlevel, isactive
            FROM program_yearlevel WHERE academicofferingid=%s ORDER BY yearlevel
        """, (ao_id,))
        prev_yls = {row[1]: {'pyl_id': row[0], 'prev_active': row[2]} for row in cur.fetchall()}

        # ── Schedule protection: block deactivation if current schedule exists ──
        if prev_ao_active and not is_active:
            cur.execute("""
                SELECT COUNT(*) FROM schedule sc
                JOIN semester sem ON sem.semesterid = sc.semesterid
                JOIN sections sec ON sec.sectionid = sc.sectionid
                JOIN program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
                WHERE pyl.academicofferingid = %s AND sem.isactive = TRUE
            """, (ao_id,))
            if cur.fetchone()[0] > 0:
                return jsonify({'success': False,
                                'error': 'Cannot deactivate this offering: it has sections assigned to the active academic year schedule.'})

        for yr, yl_info in prev_yls.items():
            new_yr_active = request.form.get(f'active_yr_{yr}') == '1'
            if yl_info['prev_active'] and not new_yr_active:
                cur.execute("""
                    SELECT COUNT(*) FROM schedule sc
                    JOIN semester sem ON sem.semesterid = sc.semesterid
                    JOIN sections sec ON sec.sectionid = sc.sectionid
                    WHERE sec.programyearlevelid = %s AND sem.isactive = TRUE
                """, (yl_info['pyl_id'],))
                if cur.fetchone()[0] > 0:
                    return jsonify({'success': False,
                                    'error': f'Cannot deactivate Year {yr}: its sections are assigned to the active academic year schedule.'})

        # Update offering status
        cur.execute("UPDATE academic_offering SET isactive=%s WHERE academicofferingid=%s",
                    (is_active, ao_id))
        print(f"[EditOffering] ao_id={ao_id}, is_active={is_active}")

        # Update linked track
        cur.execute("SELECT trackid FROM academic_offering WHERE academicofferingid=%s", (ao_id,))
        row = cur.fetchone()
        if row and row[0]:
            cur.execute("""
                UPDATE track SET trackname=%s, trackcode=%s, isactive=%s
                WHERE trackid=%s
            """, (track_name, track_code, is_active, row[0]))
            print(f"[EditOffering] Track updated: trackid={row[0]}, name={track_name}, code={track_code}")

        # Update per-year-level section counts
        cur.execute("""
            SELECT programyearlevelid, yearlevel FROM program_yearlevel
            WHERE academicofferingid = %s ORDER BY yearlevel
        """, (ao_id,))
        year_levels = cur.fetchall()
        updated_yls = []
        for pyl_id, yr in year_levels:
            raw_sec   = request.form.get(f'sections_yr_{yr}', '1')
            sec_count = max(1, int(raw_sec) if str(raw_sec).isdigit() else 1)
            yr_active = request.form.get(f'active_yr_{yr}') == '1'
            cur.execute("""
                UPDATE program_yearlevel
                SET numberofsections=%s, isactive=%s
                WHERE programyearlevelid=%s
            """, (sec_count, yr_active, pyl_id))
            updated_yls.append({
                'programyearlevelid': pyl_id,
                'academicofferingid': int(ao_id),
                'yearlevel':          yr,
                'isactive':           yr_active,
                'numberofsections':   sec_count,
            })
            print(f"[EditOffering] Year {yr}: pyl_id={pyl_id}, sections={sec_count}, active={yr_active}")

        # ── Enforce hierarchy: active offering requires at least one active year level ──
        active_yl_count = sum(1 for y in updated_yls if y['isactive'])
        if active_yl_count == 0 and is_active:
            conn.rollback()
            return jsonify({
                'success': False,
                'error': 'An Academic Offering must contain at least one active Program Year Level before it can be activated.',
            })

        # ── Sync sections for each year level ─────────────────
        _LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        cur.execute("SELECT offeringcode FROM academic_offering WHERE academicofferingid=%s", (ao_id,))
        _offer_row  = cur.fetchone()
        _offer_code = _offer_row[0] if _offer_row else ''

        all_sections = []
        for yl_data in updated_yls:
            pyl_id    = yl_data['programyearlevelid']
            yr        = yl_data['yearlevel']
            new_count = yl_data['numberofsections']
            yr_active = yl_data['isactive']

            # Load ALL sections (active + inactive) — never delete, only toggle isactive
            cur.execute("""
                SELECT sectionid, sectionname, isactive FROM sections
                WHERE programyearlevelid=%s ORDER BY sectionname
            """, (pyl_id,))
            all_secs      = cur.fetchall()
            active_secs   = [(sid, sn) for sid, sn, sa in all_secs if sa]
            inactive_secs = [(sid, sn) for sid, sn, sa in all_secs if not sa]
            active_count  = len(active_secs)
            all_names     = {sn for _, sn, _ in all_secs}

            if yr_active:
                if new_count > active_count:
                    needed = new_count - active_count
                    # 1. Reactivate existing inactive sections first (preserves original records)
                    reactivated = 0
                    for sid, _ in inactive_secs:
                        if reactivated >= needed:
                            break
                        cur.execute("UPDATE sections SET isactive=TRUE WHERE sectionid=%s", (sid,))
                        reactivated += 1
                    # 2. Only create new rows for genuinely new slots
                    if reactivated < needed:
                        still_needed = needed - reactivated
                        inserted = 0
                        for j in range(26):
                            if inserted >= still_needed:
                                break
                            sec_name = f"{_offer_code}-{yr}{_LETTERS[j]}"
                            if sec_name not in all_names:
                                cur.execute("""
                                    INSERT INTO sections (programyearlevelid, sectionname, isactive)
                                    VALUES (%s, %s, TRUE)
                                    ON CONFLICT (programyearlevelid, sectionname) DO NOTHING
                                """, (pyl_id, sec_name))
                                if cur.rowcount > 0:
                                    inserted += 1
                                    all_names.add(sec_name)
                elif new_count < active_count:
                    # Deactivate excess (last alphabetically) — do not delete
                    for sid, _ in active_secs[new_count:]:
                        cur.execute("UPDATE sections SET isactive=FALSE WHERE sectionid=%s", (sid,))
            else:
                # Year level inactive → deactivate all its sections (keep records)
                cur.execute(
                    "UPDATE sections SET isactive=FALSE WHERE programyearlevelid=%s AND isactive=TRUE",
                    (pyl_id,)
                )

            # Collect final state for response
            cur.execute("""
                SELECT sectionid, sectionname, isactive FROM sections
                WHERE programyearlevelid=%s ORDER BY sectionname
            """, (pyl_id,))
            for sid, sname, sactive in cur.fetchall():
                all_sections.append({
                    'sectionid':          sid,
                    'sectionname':        sname,
                    'programyearlevelid': pyl_id,
                    'academicofferingid': int(ao_id),
                    'yearlevel':          yr,
                    'programcode':        _offer_code,
                    'isactive':           sactive,
                })
            print(f"[EditOffering] Year {yr}: synced sections, new_count={new_count}, active={yr_active}")

        # ── Auto-activate parent program when offering is active ──
        if is_active:
            cur.execute("UPDATE programs SET isactive=TRUE WHERE programcode=%s AND isactive=FALSE", (prog_code,))

        # Read back offering data and final program state for response
        cur.execute("""
            SELECT ao.offeringcode, ao.offeringdescription, ao.programcode,
                   t.trackname, t.trackcode, t.tracktype,
                   COUNT(pyl.programyearlevelid) AS yearlevel_count,
                   COALESCE(SUM(pyl.numberofsections), 0) AS section_count
            FROM   academic_offering ao
            LEFT JOIN track t ON t.trackid = ao.trackid
            LEFT JOIN program_yearlevel pyl ON pyl.academicofferingid = ao.academicofferingid AND pyl.isactive = TRUE
            WHERE  ao.academicofferingid = %s
            GROUP  BY ao.offeringcode, ao.offeringdescription, ao.programcode,
                      t.trackname, t.trackcode, t.tracktype
        """, (ao_id,))
        ao_row = cur.fetchone()
        cur.execute("SELECT isactive FROM programs WHERE programcode=%s", (prog_code,))
        prog_row = cur.fetchone()
        prog_isactive = bool(prog_row[0]) if prog_row else True

        conn.commit()
        print(f"[EditOffering] COMMIT — offering ID {ao_id} updated.")

        offering_payload = None
        if ao_row:
            offering_payload = {
                'academicofferingid': int(ao_id),
                'offeringcode':       ao_row[0],
                'offeringdescription': ao_row[1],
                'programcode':        ao_row[2],
                'isactive':           is_active,
                'trackname':          ao_row[3] or '',
                'trackcode':          ao_row[4] or '',
                'tracktype':          ao_row[5] or '',
                'yearlevel_count':    ao_row[6],
                'section_count':      int(ao_row[7]),
            }

        try:
            write_activity_log("Updated Academic Offering",
                               f"Modified offering ID {ao_id}",
                               category='program', color=_LOG_COLORS.get('program', 'blue'))
        except Exception:
            pass

        return jsonify({
            'success':       True,
            'message':       'Offering updated successfully.',
            'offering':      offering_payload,
            'yearlevels':    updated_yls,
            'sections':      all_sections,
            'prog_isactive': prog_isactive,
        })

    except Exception as e:
        if conn:
            try: conn.rollback()
            except Exception: pass
        import traceback
        print(f"[EditOffering] ERROR: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)})
    finally:
        if cur:  cur.close()
        if conn: conn.close()


@app.route('/admin/settings/offering/delete', methods=['POST'])
def settings_delete_offering():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    ao_id = request.form.get('offering_id')
    if not ao_id:
        return jsonify({'success': False, 'error': 'Offering ID is required.'})

    conn = None; cur = None
    try:
        conn = get_db_connection(); cur = conn.cursor()

        # Read offering info for logging
        cur.execute("""
            SELECT ao.offeringcode, ao.offeringdescription, ao.programcode,
                   ao.trackid, ao.isactive
            FROM   academic_offering ao
            WHERE  ao.academicofferingid = %s
        """, (ao_id,))
        ao_row = cur.fetchone()
        if not ao_row:
            return jsonify({'success': False, 'error': 'Offering not found.'})
        offering_code, offering_desc, prog_code, track_id, _ = ao_row

        # ── Schedule protection ────────────────────────────
        # Block deactivation if offering has sections in the current active semester
        cur.execute("""
            SELECT COUNT(*) FROM schedule sc
            JOIN semester sem ON sem.semesterid = sc.semesterid
            JOIN sections sec ON sec.sectionid = sc.sectionid
            JOIN program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
            WHERE pyl.academicofferingid = %s AND sem.isactive = TRUE
        """, (ao_id,))
        if cur.fetchone()[0] > 0:
            return jsonify({'success': False,
                            'error': f"Cannot deactivate '{offering_code}': it has sections assigned to the active academic year schedule."})

        # ── Check for operational records ─────────────────
        cur.execute("""
            SELECT COUNT(*) FROM sections sec
            JOIN   program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            WHERE  pyl.academicofferingid = %s
        """, (ao_id,))
        section_count = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM schedule sc
            JOIN   sections sec ON sc.sectionid = sec.sectionid
            JOIN   program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            WHERE  pyl.academicofferingid = %s
        """, (ao_id,))
        schedule_count = cur.fetchone()[0]

        has_operational = section_count > 0 or schedule_count > 0

        if has_operational:
            # Soft delete only — preserve data integrity
            cur.execute("UPDATE academic_offering SET isactive=FALSE WHERE academicofferingid=%s", (ao_id,))
            if track_id:
                cur.execute("UPDATE track SET isactive=FALSE WHERE trackid=%s", (track_id,))
            conn.commit()
            print(f"[DeleteOffering] Soft-deleted {offering_code} (sections={section_count}, schedules={schedule_count})")
            try:
                write_activity_log("Deactivated Academic Offering",
                                   f"Soft-deleted {offering_code} — {section_count} sections, {schedule_count} schedules preserved",
                                   category='program', color=_LOG_COLORS.get('program', 'blue'))
            except Exception: pass
            return jsonify({
                'success': True,
                'mode':    'soft',
                'message': f"'{offering_code}' has been deactivated. Associated records are preserved.",
                'academicofferingid': int(ao_id),
            })

        # ── Hard delete — no operational records ──────────
        # Delete in FK-safe order
        cur.execute("""
            DELETE FROM program_yearlevel WHERE academicofferingid = %s
        """, (ao_id,))
        cur.execute("DELETE FROM academic_offering WHERE academicofferingid = %s", (ao_id,))
        if track_id:
            cur.execute("DELETE FROM track WHERE trackid = %s", (track_id,))

        conn.commit()
        print(f"[DeleteOffering] Hard-deleted {offering_code}")
        try:
            write_activity_log("Deleted Academic Offering",
                               f"Permanently deleted {offering_code} ({offering_desc}) from {prog_code}",
                               category='program', color=_LOG_COLORS.get('program', 'blue'))
        except Exception: pass
        return jsonify({
            'success': True,
            'mode':    'hard',
            'message': f"'{offering_code}' has been permanently deleted.",
            'academicofferingid': int(ao_id),
        })

    except Exception as e:
        if conn:
            try: conn.rollback()
            except Exception: pass
        import traceback
        print(f"[DeleteOffering] ERROR: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)})
    finally:
        if cur:  cur.close()
        if conn: conn.close()


@app.route('/admin/reports')
def admin_reports():
    if session.get('role') not in ('Admin', 'Academic Head'):
        return redirect(url_for('login'))
    ay_list       = query_db("SELECT academicyearid, yearstart, yearend FROM academicyear ORDER BY yearstart DESC")
    programs      = query_db("SELECT programcode, programname FROM programs WHERE isactive = TRUE ORDER BY programname")
    faculty       = query_db("SELECT employeenumber, lastname || ', ' || firstname AS fullname FROM faculty ORDER BY lastname, firstname")
    curricula     = query_db("SELECT c.curriculumid, c.curriculumcode, c.curriculumyear, ao.offeringcode AS programcode FROM curriculum c JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid ORDER BY ao.offeringcode, c.curriculumyear DESC")
    emp_types     = query_db("SELECT employeetypeid, typename FROM employeetype ORDER BY typename")
    specializations = query_db("SELECT specializationid, specializationname FROM specialization ORDER BY specializationname")
    statuses      = query_db("SELECT DISTINCT employeestatus FROM faculty WHERE employeestatus IS NOT NULL ORDER BY employeestatus")
    buildings     = query_db("SELECT buildingid, buildingname FROM building WHERE isactive = TRUE ORDER BY buildingname")
    room_types    = query_db("SELECT DISTINCT roomtype FROM room WHERE roomtype IS NOT NULL ORDER BY roomtype")
    return render_template('admin/reports_admin.html',
                           ay_list=ay_list, programs=programs,
                           faculty=faculty, curricula=curricula,
                           emp_types=emp_types, specializations=specializations,
                           statuses=statuses, buildings=buildings, room_types=room_types)

@app.route('/admin/reports/data', methods=['POST'])
def reports_data():
    if session.get('role') not in ('Admin', 'Academic Head'):
        return jsonify({'error': 'Unauthorized'}), 403

    rtype      = request.json.get('report_type')
    ay         = request.json.get('ay')
    sem        = request.json.get('semester')
    prog       = request.json.get('program')
    yl         = request.json.get('year_level')
    instr      = request.json.get('instructor')
    curr       = request.json.get('curriculum')
    fac_type   = request.json.get('faculty_type')
    fac_status = request.json.get('faculty_status')
    fac_spec   = request.json.get('specialization')
    bldg       = request.json.get('building')
    room_type  = request.json.get('room_type')

    SEM_LABEL = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)

    def rows_to_payload(rows):
        if not rows:
            return None
        cols = list(rows[0].keys())
        data = [[str(v) if v is not None else '—' for v in row.values()] for row in rows]
        return {'columns': cols, 'rows': data}

    def run_offerings(sem_type, ay_val, prog_val=None):
        where  = ["sv.status IN ('Published','Archive','Draft')"]
        p      = []
        if ay_val:
            where.append("sem.academicyearid = %s"); p.append(ay_val)
        if sem_type:
            where.append("sem.semestertype = %s"); p.append(sem_type)
        effective_prog = prog_val if prog_val else prog
        if effective_prog and effective_prog != 'All':
            where.append("ao.offeringcode = %s"); p.append(effective_prog)
        if yl and yl != 'All':
            where.append("pyl.yearlevel = %s"); p.append(int(yl))
        cur.execute(f"""
            SELECT
                f.lastname || ', ' || f.firstname AS "Instructor",
                sub.subjectcode AS "Subject Code",
                sub.subjectname AS "Subject Description",
                sub.lecturehours AS "Lec",
                sub.laboratoryhours AS "Lab",
                sub.creditunits AS "Units",
                ao.offeringcode || ' ' || pyl.yearlevel AS "Course",
                string_agg(DISTINCT
                    CASE ss.daydesc
                        WHEN 'Monday' THEN 'MON' WHEN 'Tuesday' THEN 'TUE'
                        WHEN 'Wednesday' THEN 'WED' WHEN 'Thursday' THEN 'THU'
                        WHEN 'Friday' THEN 'FRI' WHEN 'Saturday' THEN 'SAT'
                        WHEN 'Sunday' THEN 'SUN' ELSE ss.daydesc END, '/') AS "Days",
                string_agg(
                    to_char(ts_s.timevalue::interval,'HH12:MI AM') || ' – ' ||
                    to_char(ts_e.timevalue::interval,'HH12:MI AM'),
                    '/' ORDER BY ts_s.timevalue) AS "Time",
                string_agg(COALESCE(r.roomname,'TBA'), '/') AS "Room"
            FROM schedule_version sv
            JOIN schedule sg               ON sv.scheduleid          = sg.scheduleid
            JOIN curriculumsubject cs       ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub               ON cs.subjectcode         = sub.subjectcode
            JOIN sections sec              ON sg.sectionid           = sec.sectionid
            JOIN program_yearlevel pyl     ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN academic_offering ao      ON pyl.academicofferingid = ao.academicofferingid
            JOIN faculty f                 ON sg.employeenumber      = f.employeenumber
            JOIN semester sem              ON sg.semesterid          = sem.semesterid
            LEFT JOIN schedule_sessions ss ON sv.versionid           = ss.versionid
            LEFT JOIN timeslot ts_s        ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN timeslot ts_e        ON ss.endtimeid           = ts_e.timeid
            LEFT JOIN room r               ON ss.roomid              = r.roomid
            WHERE {' AND '.join(where)}
            GROUP BY f.lastname, f.firstname, sub.subjectcode, sub.subjectname,
                     sub.lecturehours, sub.laboratoryhours, sub.creditunits,
                     ao.offeringcode, pyl.yearlevel
            ORDER BY f.lastname, ao.offeringcode, pyl.yearlevel, sub.subjectcode
        """, p)
        return cur.fetchall()

    def run_assignments(sem_type, ay_val):
        where = ["sv.status IN ('Published','Archive','Draft')"]
        p     = []
        if ay_val:
            where.append("sem.academicyearid = %s"); p.append(ay_val)
        if sem_type:
            where.append("sem.semestertype = %s"); p.append(sem_type)
        if instr and instr != 'All':
            where.append("f.employeenumber = %s"); p.append(instr)
        cur.execute(f"""
            SELECT
                f.lastname || ', ' || f.firstname AS "Faculty Name",
                sub.subjectcode AS "Subject Code",
                sub.subjectname AS "Subject Description",
                ao.offeringcode AS "Program",
                sec.sectionname AS "Section",
                (sub.lecturehours + sub.laboratoryhours) AS "Hours"
            FROM schedule_version sv
            JOIN schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub          ON cs.subjectcode         = sub.subjectcode
            JOIN sections sec         ON sg.sectionid           = sec.sectionid
            JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN academic_offering ao  ON pyl.academicofferingid = ao.academicofferingid
            JOIN faculty f            ON sg.employeenumber      = f.employeenumber
            JOIN semester sem         ON sg.semesterid          = sem.semesterid
            WHERE {' AND '.join(where)}
            ORDER BY f.lastname, ao.offeringcode, sub.subjectcode
        """, p)
        return cur.fetchall()

    try:
        # ── Rooms ──────────────────────────────────────────────────
        if rtype == 'rooms':
            where, p = [], []
            if bldg and bldg != 'All':
                where.append("b.buildingid = %s"); p.append(int(bldg))
            if room_type and room_type != 'All':
                where.append("r.roomtype = %s"); p.append(room_type)
            cur.execute(f"""
                SELECT r.roomname AS "Room Name", b.buildingname AS "Building",
                       r.roomtype AS "Type", r.roomcapacity AS "Capacity",
                       CASE WHEN b.isactive THEN 'Active' ELSE 'Inactive' END AS "Status"
                FROM room r JOIN building b ON r.buildingid = b.buildingid
                {'WHERE ' + ' AND '.join(where) if where else ''}
                ORDER BY b.buildingname, r.roomname
            """, p)
            payload = rows_to_payload(cur.fetchall())
            return jsonify(payload or {'columns': [], 'rows': []})

        # ── Faculty ─────────────────────────────────────────────────
        elif rtype == 'faculty':
            where, p = [], []
            if fac_type and fac_type != 'All':
                where.append("f.employeetypeid = %s"); p.append(int(fac_type))
            if fac_status and fac_status != 'All':
                where.append("f.employeestatus = %s"); p.append(fac_status)
            if fac_spec and fac_spec != 'All':
                where.append("f.specializationid = %s"); p.append(int(fac_spec))
            cur.execute(f"""
                SELECT f.lastname || ', ' || f.firstname || ' ' || COALESCE(f.middlename,'') AS "Faculty Name",
                       et.typename AS "Employee Type",
                       COALESCE(s.specializationname,'—') AS "Specialization",
                       COALESCE(d.designationname,'—') AS "Designation",
                       COALESCE(et.regularload::text,'—') AS "Max Load (Units)",
                       f.employeestatus AS "Status"
                FROM faculty f
                LEFT JOIN employeetype et  ON f.employeetypeid   = et.employeetypeid
                LEFT JOIN designation d    ON f.designationid    = d.designationid
                LEFT JOIN specialization s ON f.specializationid = s.specializationid
                {'WHERE ' + ' AND '.join(where) if where else ''}
                ORDER BY f.lastname, f.firstname
            """, p)
            payload = rows_to_payload(cur.fetchall())
            return jsonify(payload or {'columns': [], 'rows': []})

        # ── Curriculum ──────────────────────────────────────────────
        elif rtype == 'curriculum':
            where, p = [], []
            if prog and prog != 'All':
                where.append("ao.offeringcode = %s"); p.append(prog)
            if curr and curr != 'All':
                where.append("cs.curriculumid = %s"); p.append(int(curr))
            cur.execute(f"""
                SELECT c.curriculumcode AS "Curriculum",
                       cs.yearlevel AS "Year Level",
                       CASE cs.semester WHEN 'A' THEN '1st Sem' WHEN 'B' THEN '2nd Sem'
                                        WHEN 'C' THEN 'Summer' ELSE cs.semester END AS "Semester",
                       sub.subjectcode AS "Subject Code",
                       sub.subjectname AS "Subject Description",
                       sub.lecturehours AS "Lec Hours",
                       sub.laboratoryhours AS "Lab Hours",
                       sub.creditunits AS "Credit Units",
                       COALESCE(sub.prerequisite,'—') AS "Pre-requisite"
                FROM curriculumsubject cs
                JOIN subject sub  ON cs.subjectcode  = sub.subjectcode
                JOIN curriculum c ON cs.curriculumid = c.curriculumid
                JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
                {'WHERE ' + ' AND '.join(where) if where else ''}
                ORDER BY c.curriculumcode, cs.yearlevel, cs.semester, sub.subjectcode
            """, p)
            payload = rows_to_payload(cur.fetchall())
            return jsonify(payload or {'columns': [], 'rows': []})

        # ── Subject Offerings — grouped by Program × AY × Semester ───
        elif rtype == 'offerings':
            needs_group = (ay == 'All' or prog == 'All' or sem == 'All')
            if not needs_group:
                rows    = run_offerings(sem, ay)
                payload = rows_to_payload(rows)
                return jsonify(payload or {'columns': [], 'rows': []})

            # Build distinct combo query
            combo_where  = ["sv.status IN ('Published','Archive','Draft')"]
            combo_params = []
            if ay and ay != 'All':
                combo_where.append("ay.academicyearid = %s"); combo_params.append(ay)
            if prog and prog != 'All':
                combo_where.append("p.programcode = %s"); combo_params.append(prog)
            if sem and sem != 'All':
                combo_where.append("sem.semestertype = %s"); combo_params.append(sem)
            if yl and yl != 'All':
                combo_where.append("pyl.yearlevel = %s"); combo_params.append(int(yl))

            cur.execute(f"""
                SELECT DISTINCT
                    p.programcode, p.programname,
                    ay.academicyearid, ay.yearstart, ay.yearend,
                    sem.semestertype
                FROM schedule_version sv
                JOIN schedule sg    ON sv.scheduleid     = sg.scheduleid
                JOIN semester sem   ON sg.semesterid     = sem.semesterid
                JOIN academicyear ay ON sem.academicyearid = ay.academicyearid
                JOIN sections sec   ON sg.sectionid      = sec.sectionid
                JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
                JOIN programs p     ON ao.programcode    = p.programcode
                WHERE {' AND '.join(combo_where)}
                ORDER BY ay.yearstart DESC, p.programcode, sem.semestertype
            """, combo_params)
            combos = cur.fetchall()

            sections = []
            for combo in combos:
                rows    = run_offerings(combo['semestertype'], combo['academicyearid'], combo['programcode'])
                payload = rows_to_payload(rows)
                if payload:
                    sem_lbl = SEM_LABEL.get(combo['semestertype'], combo['semestertype'])
                    sections.append({
                        'title': f"{combo['programname']} — A.Y. {combo['yearstart']}–{combo['yearend']} — {sem_lbl}",
                        'columns': payload['columns'],
                        'rows': payload['rows'],
                    })
            return jsonify({'grouped': True, 'sections': sections})

        # ── Teaching Assignments — grouped by Semester ──────────────
        elif rtype == 'assignments':
            ay_info  = query_db("SELECT yearstart, yearend FROM academicyear WHERE academicyearid = %s",
                                (ay,), one=True)
            ay_label = f"A.Y. {ay_info['yearstart']}–{ay_info['yearend']}" if ay_info else str(ay)

            if sem and sem != 'All':
                rows    = run_assignments(sem, ay)
                payload = rows_to_payload(rows)
                return jsonify(payload or {'columns': [], 'rows': []})

            cur.execute("""
                SELECT DISTINCT sem.semestertype
                FROM semester sem
                JOIN schedule sg ON sg.semesterid = sem.semesterid
                JOIN schedule_version sv ON sv.scheduleid = sg.scheduleid
                WHERE sem.academicyearid = %s
                  AND sv.status IN ('Published','Archive')
                ORDER BY sem.semestertype
            """, (ay,))
            sems_with_data = [r['semestertype'] for r in cur.fetchall()]

            sections = []
            for sem_type in sems_with_data:
                rows    = run_assignments(sem_type, ay)
                payload = rows_to_payload(rows)
                if payload:
                    sections.append({
                        'title': f"{ay_label} — {SEM_LABEL.get(sem_type, sem_type)}",
                        'columns': payload['columns'],
                        'rows': payload['rows'],
                    })
            return jsonify({'grouped': True, 'sections': sections})

        else:
            return jsonify({'error': 'Unknown report type'}), 400

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()

# ==============================================================================
# --- FACULTY SPECIFIC ROUTES ---
# ==============================================================================

@app.route('/faculty_dashboard')
def faculty_dashboard():
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return redirect(url_for('login'))

    username = session.get('username')
    today_day = datetime.now().strftime('%A')
    current_date_formatted = datetime.now().strftime('%B %d, %Y, %A')
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT a.employeenumber, f.firstname, f.lastname, d.designationname
            FROM accounts a
            LEFT JOIN faculty f ON a.employeenumber = f.employeenumber
            LEFT JOIN designation d ON f.designationid = d.designationid
            WHERE a.username = %s
        """, (username,))
        user_data = cursor.fetchone()

        emp_num = user_data['employeenumber'] if user_data else None

        today_schedule = []
        total_units = 0
        total_subjects = 0

        if emp_num:
            # Added offeringcode and yearlevel for the UI display
            cursor.execute("""
                SELECT sub.subjectcode, sub.subjectname, r.roomname, ss.daydesc,
                       TO_CHAR(ts_s.timevalue, 'HH12:MI AM') as start_time,
                       TO_CHAR(ts_e.timevalue, 'HH12:MI AM') as end_time,
                       ao.offeringcode, pyl.yearlevel
                FROM schedule_sessions ss
                JOIN schedule_version sv ON ss.versionid = sv.versionid
                JOIN schedule sc ON sv.scheduleid = sc.scheduleid
                JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                JOIN subject sub ON cs.subjectcode = sub.subjectcode
                LEFT JOIN room r ON ss.roomid = r.roomid
                LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
                LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
                LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                LEFT JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
                WHERE sc.employeenumber = %s AND sv.status = 'Published'
                  AND ss.daydesc = %s
                ORDER BY ts_s.timevalue
            """, (emp_num, today_day))
            today_schedule = cursor.fetchall()

            cursor.execute("""
                SELECT COALESCE(SUM(sub.creditunits), 0) as total_units,
                       COUNT(DISTINCT sub.subjectcode) as total_subjects
                FROM schedule_version sv
                JOIN schedule sc ON sv.scheduleid = sc.scheduleid
                JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                JOIN subject sub ON cs.subjectcode = sub.subjectcode
                WHERE sc.employeenumber = %s AND sv.status = 'Published'
            """, (emp_num,))
            stats = cursor.fetchone()
            if stats:
                total_units = int(stats['total_units'])
                total_subjects = int(stats['total_subjects'])

        cursor.close()
        conn.close()

        if not user_data:
            user_data = {"firstname": "Faculty", "lastname": "Member", "designationname": "None"}

        return render_template('faculty/dashboard_faculty.html', user=user_data,
                               schedule=today_schedule, total_units=total_units,
                               total_subjects=total_subjects, current_date=current_date_formatted)

    except Exception as e:
        if conn:
            conn.close()
        return f"<div style='padding: 50px;'><h2 style='color: red;'>Error</h2><p>{str(e)}</p></div>"

# --- Route for Teaching Assignment ---
@app.route('/faculty_teaching_assignment')
def faculty_teaching_assignment():
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return redirect(url_for('login'))

    username = session.get('username')
    try:
        acc = query_db("SELECT employeenumber FROM accounts WHERE username = %s", (username,), one=True)
        emp_num = acc['employeenumber'] if acc else None

        active = query_db("""
            SELECT ay.academicyearid, s.semestertype
            FROM semester s
            JOIN academicyear ay ON s.academicyearid = ay.academicyearid
            WHERE s.isactive = TRUE LIMIT 1
        """, one=True)
        active_ay_id = active['academicyearid'] if active else ''
        active_sem   = active['semestertype']    if active else 'A'

        return render_template('faculty/teaching_faculty.html',
                               emp_num=emp_num,
                               active_ay_id=active_ay_id,
                               active_sem=active_sem)
    except Exception as e:
        return f"<div style='padding: 50px; font-family: Arial;'><h2 style='color: red;'>Error</h2><p>{str(e)}</p></div>"

# --- Route for Class Schedule (SIS) Page ---
@app.route('/faculty_schedule')
def faculty_schedule():
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return redirect(url_for('login'))

    programs = query_db(
        "SELECT programcode, programname FROM programs WHERE isactive = true ORDER BY programname"
    ) or []

    ay_rows = query_db("""
        SELECT ay.academicyearid, ay.yearstart, ay.yearend,
               sem.semestertype, sem.semenddate
        FROM academicyear ay
        JOIN semester sem ON sem.academicyearid = ay.academicyearid
        ORDER BY ay.yearstart DESC, sem.semestertype
    """) or []

    faculty_list = query_db("""
        SELECT employeenumber, lastname || ', ' || firstname AS fullname
        FROM faculty WHERE employeestatus != 'Archive'
        ORDER BY lastname, firstname
    """) or []

    ay_map = {}
    for row in ay_rows:
        ay_id = row['academicyearid']
        if ay_id not in ay_map:
            ay_map[ay_id] = {
                'id': ay_id,
                'label': f"A.Y {row['yearstart']}-{row['yearend']}",
                'sems': []
            }
        ay_map[ay_id]['sems'].append({
            'type': row['semestertype'],
            'end': str(row['semenddate']) if row.get('semenddate') else None
        })

    import json as _json
    return render_template('faculty/schedule_faculty.html',
        programs_json=_json.dumps([{'code': p['programcode'], 'name': p['programname']} for p in programs]),
        acad_years_json=_json.dumps(list(ay_map.values())),
        faculty_json=_json.dumps([{'emp': str(f['employeenumber']), 'name': f['fullname']} for f in faculty_list])
    )

# --- Route for Room Schedule Page ---
@app.route('/faculty_room_schedule')
def faculty_room_schedule():
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return redirect(url_for('login'))

    buildings = query_db(
        "SELECT buildingid, buildingname FROM building WHERE isactive = TRUE ORDER BY buildingname"
    ) or []

    rooms = query_db("""
        SELECT r.roomid, r.roomname, r.roomtype, r.roomcapacity,
               b.buildingid, b.buildingname
        FROM room r JOIN building b ON r.buildingid = b.buildingid
        WHERE b.isactive = TRUE
        ORDER BY b.buildingname, r.roomname
    """) or []

    programs = query_db(
        "SELECT programcode, programname FROM programs WHERE isactive = true ORDER BY programname"
    ) or []

    import json as _json
    return render_template('faculty/room_schedule_faculty.html',
        buildings_json=_json.dumps([{'id': b['buildingid'], 'name': b['buildingname']} for b in buildings]),
        rooms_json=_json.dumps([{
            'id': r['roomid'], 'name': r['roomname'],
            'type': r['roomtype'] or 'Lecture',
            'bid': r['buildingid'], 'bname': r['buildingname'],
            'capacity': r['roomcapacity'] or 0
        } for r in rooms]),
        programs_json=_json.dumps([{'code': p['programcode'], 'name': p['programname']} for p in programs])
    )

# --- Route for 'My Requests' Page ---
@app.route('/faculty_requests')
def faculty_requests():
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return redirect(url_for('login'))
    return render_template('faculty/request_faculty.html')

# --- Route for Subject Offerings Page ---
@app.route('/faculty_subject_offerings')
def faculty_subject_offerings():
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return redirect(url_for('login'))
    return render_template('faculty/subject_faculty.html')

# --- API: Faculty full schedule (with lec/lab hours) ---
@app.route('/api/faculty/schedule')
def api_faculty_schedule_full():
    if 'loggedin' not in session:
        return jsonify({'success': False}), 401

    ay_id      = request.args.get('ay_id', '').strip()
    semester   = request.args.get('semester', '').strip()
    program    = request.args.get('program', '').strip()
    year_level = request.args.get('year_level', '').strip()
    section_id = request.args.get('section_id', '').strip()
    emp_num    = request.args.get('emp_num', '').strip()

    filters = ["sv.status = 'Published'"]
    params  = []

    if ay_id and semester:
        filters.append("""sc.semesterid = (
            SELECT semesterid FROM semester
            WHERE academicyearid = %s AND semestertype = %s LIMIT 1)""")
        params += [ay_id, semester]

    if program:
        filters.append("ao.offeringcode = %s")
        params.append(program)

    if year_level:
        try:
            filters.append("pyl.yearlevel = %s")
            params.append(int(year_level))
        except ValueError:
            pass

    if section_id:
        try:
            filters.append("sc.sectionid = %s")
            params.append(int(section_id))
        except ValueError:
            pass

    if emp_num:
        filters.append("sc.employeenumber = %s")
        params.append(emp_num)

    try:
        rows = query_db(f"""
            SELECT
                sub.subjectcode,
                sub.subjectname,
                sub.creditunits,
                COALESCE(sub.lecturehours,    0) AS lecturehours,
                COALESCE(sub.laboratoryhours, 0) AS laboratoryhours,
                sec.sectionname,
                pyl.yearlevel,
                ao.offeringcode AS programcode,
                ss.daydesc,
                r.roomname,
                f.lastname || ', ' || f.firstname AS instructor,
                sc.employeenumber,
                TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_time,
                TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_time,
                TO_CHAR(ts_s.timevalue, 'HH12:MI AM') || ' - ' ||
                TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS time_range
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub ON cs.subjectcode = sub.subjectcode
            LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
            LEFT JOIN room r ON ss.roomid = r.roomid
            LEFT JOIN faculty f ON sc.employeenumber = f.employeenumber
            LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
            WHERE {' AND '.join(filters)}
            ORDER BY ts_s.timevalue, ss.daydesc
        """, params if params else None)
        return jsonify({'success': True, 'sessions': [dict(r) for r in (rows or [])]})
    except Exception as e:
        print(f"[api_faculty_schedule_full] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# --- API: Submit a faculty room request ---
@app.route('/api/faculty/submit_request', methods=['POST'])
def api_faculty_submit_request():
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        from datetime import datetime as _dt
        data       = request.json or {}
        req_type   = data.get('request_type', '')   # 'makeup' | 'adjustment'
        room_id    = data.get('room_id')
        section_id = data.get('section_id')
        subject    = data.get('subject_code', '').strip()
        reason     = data.get('reason', '')
        start_time = data.get('start_time', '')     # "07:30 AM"
        end_time   = data.get('end_time', '')

        # Resolve submitted_by from session; fall back to accounts lookup
        submitted_by = session.get('employeenumber')
        if not submitted_by:
            acc_row = query_db(
                "SELECT employeenumber FROM accounts WHERE username = %s",
                [session.get('username')]
            )
            submitted_by = acc_row[0]['employeenumber'] if acc_row else None
        if not submitted_by:
            return jsonify({'success': False, 'error': 'Could not determine faculty employee number.'}), 400

        # Map "07:30 AM" → timeid in timeslot table
        def _time_to_id(t_str):
            if not t_str:
                return None
            try:
                t = _dt.strptime(t_str.strip(), '%I:%M %p').time()
                row = query_db("SELECT timeid FROM timeslot WHERE timevalue = %s", [t])
                return row[0]['timeid'] if row else None
            except Exception:
                return None

        start_tid = _time_to_id(start_time)
        end_tid   = _time_to_id(end_time)

        # Resolve scheduleid (and versionid) from subject code + section
        sched_row = None
        if subject and section_id:
            try:
                sched_row = query_db("""
                    SELECT sc.scheduleid, sv.versionid
                    FROM schedule sc
                    JOIN schedule_version sv ON sc.scheduleid = sv.scheduleid
                    JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                    WHERE cs.subjectcode = %s AND sc.sectionid = %s AND sv.status = 'Published'
                    ORDER BY sv.version_number DESC LIMIT 1
                """, [subject, int(section_id)])
            except Exception:
                sched_row = None

        scheduleid = sched_row[0]['scheduleid'] if sched_row else None
        versionid  = sched_row[0]['versionid']  if sched_row else None
        rid        = int(room_id) if room_id else None

        if req_type == 'makeup':
            req_date = data.get('request_date') or None
            if not scheduleid:
                return jsonify({'success': False,
                    'error': 'No published schedule found for the given subject and section.'}), 400
            if not start_tid or not end_tid:
                return jsonify({'success': False, 'error': 'Invalid time selection.'}), 400
            query_db("""
                INSERT INTO class_meeting_request
                    (scheduleid, requested_date, new_starttimeid, new_endtimeid,
                     new_roomid, reason, submitted_by, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'Pending')
            """, (scheduleid, req_date, start_tid, end_tid, rid, reason, submitted_by))

        elif req_type == 'adjustment':
            day        = data.get('day', '') or None
            start_date = data.get('start_date') or None
            if not scheduleid or not versionid:
                return jsonify({'success': False,
                    'error': 'No published schedule found for the given subject and section.'}), 400
            if not start_date:
                return jsonify({'success': False, 'error': 'Effective start date is required.'}), 400
            parts = []
            if day:                     parts.append('Day')
            if start_tid and end_tid:   parts.append('Time')
            if rid:                     parts.append('Room')
            change_type = '+'.join(parts) if parts else 'Day'
            query_db("""
                INSERT INTO schedule_change_request
                    (scheduleid, versionid, change_type, new_daydesc,
                     new_starttimeid, new_endtimeid, new_roomid,
                     effective_from, reason, submitted_by, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Pending')
            """, (scheduleid, versionid, change_type, day,
                  start_tid, end_tid, rid, start_date, reason, submitted_by))
        else:
            return jsonify({'success': False, 'error': 'Unknown request type.'}), 400

        return jsonify({'success': True})
    except Exception as e:
        print(f"[api_faculty_submit_request] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# --- API: Available rooms for a given day/time window ---
@app.route('/api/faculty/available_rooms')
def api_faculty_available_rooms():
    if 'loggedin' not in session:
        return jsonify({'success': False}), 401

    day         = request.args.get('day',         '').strip()
    start_time  = request.args.get('start_time',  '').strip()
    end_time    = request.args.get('end_time',    '').strip()
    room_type   = request.args.get('room_type',   '').strip()
    building_id = request.args.get('building_id', '').strip()

    try:
        room_sql    = """
            SELECT r.roomid, r.roomname, r.roomtype, r.roomcapacity,
                   b.buildingid, b.buildingname
            FROM room r JOIN building b ON r.buildingid = b.buildingid
            WHERE b.isactive = TRUE
        """
        room_params = []
        if room_type:
            room_sql += " AND LOWER(r.roomtype) = LOWER(%s)"
            room_params.append(room_type)
        if building_id:
            try:
                room_sql += " AND b.buildingid = %s"
                room_params.append(int(building_id))
            except ValueError:
                pass
        room_sql += " ORDER BY b.buildingname, r.roomname"

        all_rooms = query_db(room_sql, room_params if room_params else None) or []

        if not (day and start_time and end_time):
            return jsonify({'success': True, 'rooms': [dict(r) for r in all_rooms]})

        booked = query_db("""
            SELECT DISTINCT ss.roomid
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
            WHERE sv.status = 'Published'
              AND UPPER(ss.daydesc) LIKE UPPER(%s)
              AND ts_s.timevalue < %s::time
              AND ts_e.timevalue > %s::time
        """, (f'%{day}%', end_time, start_time)) or []

        booked_ids = {r['roomid'] for r in booked}
        available  = [dict(r) for r in all_rooms if r['roomid'] not in booked_ids]
        return jsonify({'success': True, 'rooms': available})
    except Exception as e:
        print(f"[api_faculty_available_rooms] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500



# =====================================================================
# SCHEDULE GENERATION ROUTES - FINAL WORKING VERSION
# =====================================================================

from scheduler import IntelligentScheduler, validate_draft
from datetime import time
import json

scheduler_engine = IntelligentScheduler()

# ── HELPERS ────────────────────────────────────────────────────────────

def _load_faculty_map():
    rows = query_db("""
        SELECT f.employeenumber, CONCAT(f.lastname, ', ', f.firstname) AS fullname,
               f.employeestatus, f.designationid, et.regularload, et.parttimeload,
               COALESCE(et.teachingsubstitution, 0) AS teachingsubstitution,
               et.regular_start, et.regular_end, et.parttime_start, et.parttime_end,
               d.nightteachingservice, COALESCE(d.regularloadunit, 0) AS designation_regular_load,
               sp.specializationname
        FROM faculty f
        JOIN employeetype et ON f.employeetypeid = et.employeetypeid
        LEFT JOIN designation d       ON f.designationid    = d.designationid
        LEFT JOIN specialization sp   ON f.specializationid = sp.specializationid
        WHERE f.employeestatus != 'Archive'
    """)
    faculty_map = {}
    for row in (rows or []):
        fnum = row['employeenumber']
        has_desig = row['designationid'] is not None
        eff_regular  = row['designation_regular_load'] if (has_desig and row['designation_regular_load']) else row['regularload']
        eff_parttime = (row['nightteachingservice'] or 0) if has_desig else row['parttimeload']
        faculty_map[fnum] = {
            'employeenumber': fnum, 'fullname': row['fullname'],
            'employeestatus': row['employeestatus'], 'designationid': row['designationid'],
            'nightteachingservice': row['nightteachingservice'],
            'specializationname': row.get('specializationname') or '',
            'employeetype': {
                'regularload': eff_regular, 'parttimeload': eff_parttime,
                'teachingsubstitution': int(row['teachingsubstitution'] or 0),
                'regular_start': row['regular_start'] or time(7, 30),
                'regular_end': row['regular_end'] or time(16, 30),
                'parttime_start': row['parttime_start'], 'parttime_end': row['parttime_end'],
            },
        }
    return faculty_map

def _parse_days(days_str: str) -> list:
    abbr_map = {'MON': 'Monday', 'TUE': 'Tuesday', 'WED': 'Wednesday', 'THU': 'Thursday', 'FRI': 'Friday', 'SAT': 'Saturday', 'SUN': 'Sunday'}
    if not days_str: return []
    return [abbr_map.get(d.strip().upper(), d.strip()) for d in days_str.split('/')]

def _parse_time_str(t_str: str):
    from datetime import datetime as _dt
    for fmt in ('%I:%M %p', '%H:%M', '%H:%M:%S'):
        try: return _dt.strptime(t_str.strip(), fmt).time()
        except ValueError: continue
    return None

def _serialize_class(cls: dict) -> dict:
    safe = {}
    for key, val in cls.items():
        if isinstance(val, time): continue
        elif isinstance(val, list): safe[key] = [str(v) for v in val]
        else: safe[key] = val
    return safe

def _rehydrate_schedule(schedule_data: list) -> list:
    for cls in schedule_data:
        if 'start_time' not in cls and 'time' in cls:
            try:
                parts = cls['time'].replace('–', '-').split('-')
                if len(parts) == 2:
                    cls['start_time'] = _parse_time_str(parts[0].strip())
                    cls['end_time']   = _parse_time_str(parts[1].strip())
            except Exception: pass
        # Also convert string start/end times to Python time objects
        # (the CSP validator requires time objects, not strings)
        if 'start_time' in cls and isinstance(cls['start_time'], str):
            cls['start_time'] = _parse_time_str(cls['start_time'])
        if 'end_time' in cls and isinstance(cls['end_time'], str):
            cls['end_time'] = _parse_time_str(cls['end_time'])
        if 'days_list' not in cls and 'days' in cls:
            cls['days_list'] = _parse_days(cls['days'])
        if 'day' not in cls and 'days_list' in cls and cls['days_list']:
            cls['day'] = cls['days_list'][0]
    return schedule_data

def _fmt_12h(t_str):
    try:
        parts = str(t_str).split(':')
        h, m = int(parts[0]), int(parts[1])
        suffix = 'PM' if h >= 12 else 'AM'
        h12 = h - 12 if h > 12 else (12 if h == 0 else h)
        return f"{h12}:{m:02d} {suffix}"
    except Exception: return str(t_str) if t_str else ''

def _get_semester_id(cur, acad_year, term):
    cur.execute("SELECT semesterid FROM public.semester WHERE academicyearid = %s AND semestertype = %s", (acad_year, term))
    result = cur.fetchone()
    if not result: raise Exception(f"Semester not found for {acad_year} {term}")
    return result['semesterid']

# ── DATABASE BATCH VERSIONING LOGIC ───────────────────────────────────

def _archive_status(cur, program, year_level, term, semester_id, status_to_archive, source=None):
    extra = "AND sv.source = %s" if source else ""
    params = [program, year_level, term, semester_id, status_to_archive]
    if source:
        params.append(source)
    cur.execute(f"""
        UPDATE public.schedule_version sv
        SET status = 'Archive'
        FROM public.schedule s, public.curriculumsubject cs, public.curriculum c,
             public.academic_offering ao
        WHERE sv.scheduleid = s.scheduleid AND s.curriculumsubjectid = cs.curriculumsubjectid
          AND cs.curriculumid = c.curriculumid
          AND c.academicofferingid = ao.academicofferingid
          AND ao.offeringcode = %s
          AND cs.yearlevel = %s AND cs.semester = %s AND s.semesterid = %s AND sv.status = %s
          {extra}
    """, params)

def _archive_status_for_subjects(cur, program, year_level, term, semester_id, status_to_archive, subject_codes):
    """Archive only sessions for specific subject codes, leaving other subjects' versions intact."""
    if not subject_codes: return
    upper_codes = [s.upper() for s in subject_codes]
    placeholders = ','.join(['%s'] * len(upper_codes))
    cur.execute(f"""
        UPDATE public.schedule_version sv
        SET status = 'Archive'
        FROM public.schedule s, public.curriculumsubject cs, public.curriculum c,
             public.academic_offering ao
        WHERE sv.scheduleid = s.scheduleid AND s.curriculumsubjectid = cs.curriculumsubjectid
          AND cs.curriculumid = c.curriculumid
          AND c.academicofferingid = ao.academicofferingid
          AND ao.offeringcode = %s
          AND cs.yearlevel = %s AND cs.semester = %s AND s.semesterid = %s AND sv.status = %s
          AND UPPER(cs.subjectcode) IN ({placeholders})
    """, [program, year_level, term, semester_id, status_to_archive] + upper_codes)

def _insert_batch(cur, schedule_data, semester_id, target_status, version_number, program, year_level, source='manual_editor'):
    _ensure_source_col(cur)
    _ensure_original_status_col(cur)
    cur.execute("""
        SELECT sec.sectionid FROM public.sections sec
        JOIN public.program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
        JOIN public.academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
        WHERE ao.offeringcode = %s AND pyl.yearlevel = %s LIMIT 1
    """, (program, year_level))
    sec_res = cur.fetchone()
    section_id = sec_res['sectionid'] if sec_res else None
    if not section_id:
        cur.execute("SELECT sectionid FROM public.sections LIMIT 1")
        section_id = cur.fetchone()['sectionid']

    for cls in schedule_data:
        s_code = cls.get('subjectcode') or cls.get('subject_code')
        f_num  = cls.get('employeenumber') or cls.get('faculty_id')
        r_id   = cls.get('roomid') or cls.get('room_id')
        day    = cls.get('daydesc') or cls.get('day')
        start_t, end_t = cls.get('start_time'), cls.get('end_time')

        if not all([s_code, f_num, start_t, end_t]): continue

        cur.execute("""
            SELECT cs.curriculumsubjectid FROM public.curriculumsubject cs
            JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
            JOIN public.academic_offering ao ON c.academicofferingid = ao.academicofferingid
            WHERE UPPER(cs.subjectcode) = UPPER(%s) AND ao.offeringcode = %s
              AND cs.yearlevel = %s AND cs.semester = %s LIMIT 1
        """, (s_code, program, year_level, cls.get('sem') or cls.get('semester', '')))
        cs_res = cur.fetchone()
        if not cs_res:
            cur.execute("""
                SELECT cs.curriculumsubjectid FROM public.curriculumsubject cs
                JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
                JOIN public.academic_offering ao ON c.academicofferingid = ao.academicofferingid
                WHERE UPPER(cs.subjectcode) = UPPER(%s) AND ao.offeringcode = %s LIMIT 1
            """, (s_code, program))
            cs_res = cur.fetchone()
        if not cs_res: continue
        cs_id = cs_res['curriculumsubjectid']

        try:
            cur.execute("SELECT timeid FROM public.timeslot WHERE timevalue = %s::time LIMIT 1", (str(start_t),))
            s_id = cur.fetchone()['timeid']
            cur.execute("SELECT timeid FROM public.timeslot WHERE timevalue = %s::time LIMIT 1", (str(end_t),))
            e_id = cur.fetchone()['timeid']
        except: continue

        cur.execute("""
            INSERT INTO public.schedule (curriculumsubjectid, sectionid, employeenumber, semesterid, datecreated) 
            VALUES (%s, %s, %s, %s, NOW()) RETURNING scheduleid
        """, (cs_id, section_id, f_num, semester_id))
        sched_id = cur.fetchone()['scheduleid']

        cur.execute("""
            INSERT INTO public.schedule_version (scheduleid, version_number, status, datecreated, source, original_status)
            VALUES (%s, %s, %s, NOW(), %s, %s) RETURNING versionid
        """, (sched_id, version_number, target_status, source, target_status))
        ver_id = cur.fetchone()['versionid']

        cur.execute("INSERT INTO public.schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid) VALUES (%s, %s, %s, %s, %s)",
                    (ver_id, day, s_id, e_id, r_id if str(r_id).isdigit() else None))

def _build_published_baseline(cur, program, year_level, sem_id, subject_codes, priority_sessions):
    """Return existing Published slots for subject_codes that are NOT already covered by priority_sessions.

    Used by both save-draft and approve to carry Published baseline slots forward so that adding
    a new slice to a multi-slice subject never replaces the existing slots.

    priority_sessions: list of sessions (rehydrated or sched_data) whose slot keys take precedence.
    Returns a list in the same shape as _fetch_section_sessions output.
    """
    if not subject_codes:
        return []
    upper_codes = set(c.upper() for c in subject_codes)

    # Build slot keys already covered by the priority (new) sessions so we don't duplicate
    priority_keys = set()
    for s in priority_sessions:
        sc  = (s.get('subjectcode') or s.get('subject_code') or '').upper()
        day = s.get('daydesc') or s.get('day') or ''
        st  = str(s.get('start_time', '')).split('.')[0]
        priority_keys.add((sc, day, st))

    all_published = _fetch_section_sessions(cur, program, year_level, sem_id, published_only=True)
    baseline = []
    for s in all_published:
        sc  = (s.get('subjectcode') or '').upper()
        if sc not in upper_codes:
            continue
        day = s.get('daydesc') or ''
        st  = str(s.get('start_time', '')).split('.')[0]
        if (sc, day, st) not in priority_keys:
            baseline.append(s)
    return baseline


def _fetch_section_sessions(cur, program, year_level, sem_id, exclude_codes=None,
                            published_only=False, draft_only=False):
    """Return active manual_editor sessions for a section, excluding specified subject codes.

    Default (save-draft carry-forward):
        Draft-preferred: for each subject, Draft takes priority over Published.
        Subjects with only a Draft are included. Subjects with only Published are included
        via fallback — but see draft_only for a stricter carry-forward.

    published_only=True (used by approve/publish):
        Only subjects that have a current Published version are returned.
        Draft-only subjects are excluded entirely so they don't bleed into Published snapshots.

    draft_only=True (used by save-draft carry-forward):
        Only subjects that have an active Draft version are carried forward.
        Subjects with only Published (never drafted) are excluded so Published content
        doesn't pollute the new Draft snapshot.
    """
    upper_excl = [c.upper() for c in (exclude_codes or [])]
    ph = ','.join(['%s'] * len(upper_excl)) if upper_excl else None
    excl_sql = f"AND UPPER(cs.subjectcode) NOT IN ({ph})" if ph else ""
    if published_only:
        status_filter = "'Published'"
    elif draft_only:
        status_filter = "'Draft'"
    else:
        status_filter = "'Draft', 'Published'"

    cur.execute(f"""
        SELECT cs.subjectcode, cs.semester, sg.employeenumber,
               ss.roomid, ss.daydesc,
               t_s.timevalue::text AS start_time,
               t_e.timevalue::text AS end_time,
               sv.status, sv.version_number
        FROM   schedule_version sv
        JOIN   schedule sg           ON sv.scheduleid          = sg.scheduleid
        JOIN   curriculumsubject cs   ON sg.curriculumsubjectid = cs.curriculumsubjectid
        JOIN   curriculum c           ON cs.curriculumid        = c.curriculumid
        JOIN   academic_offering ao   ON c.academicofferingid   = ao.academicofferingid
        JOIN   schedule_sessions ss   ON sv.versionid           = ss.versionid
        JOIN   timeslot t_s           ON ss.starttimeid         = t_s.timeid
        JOIN   timeslot t_e           ON ss.endtimeid           = t_e.timeid
        WHERE  ao.offeringcode = %s
          AND  cs.yearlevel    = %s
          AND  sg.semesterid        = %s
          AND  sv.status IN ({status_filter})
          AND  sv.source            = 'manual_editor'
          {excl_sql}
    """, [program, year_level, sem_id] + upper_excl)

    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return []

    subj_groups = {}
    for row in rows:
        subj_groups.setdefault(row['subjectcode'].upper(), []).append(row)

    result = []
    for rows_for_code in subj_groups.values():
        if published_only:
            chosen = [r for r in rows_for_code if r['status'] == 'Published']
        elif draft_only:
            # Only subjects with an actual Draft; subjects with only Published are skipped
            chosen = [r for r in rows_for_code if r['status'] == 'Draft']
        else:
            # Draft-preferred: use Draft if available, otherwise fall back to Published
            draft_rows = [r for r in rows_for_code if r['status'] == 'Draft']
            chosen = draft_rows or [r for r in rows_for_code if r['status'] == 'Published']
        if not chosen:
            continue
        best_v = max(r['version_number'] for r in chosen)
        result.extend(r for r in chosen if r['version_number'] == best_v)
    return result

# ── ROUTES ────────────────────────────────────────────────────────────
@app.route('/schedule/drafts')
def schedule_drafts_list():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    return render_template('academic/drafts.html')

@app.route('/schedule/drafts/<int:version_id>')
def schedule_draft_detail(version_id):
    if 'loggedin' not in session: return redirect(url_for('login'))
    return render_template('academic/draftView.html', version_id=version_id)

@app.route('/schedule/version-history')
def schedule_version_history():
    if 'loggedin' not in session: return redirect(url_for('login'))
    return render_template('academic/versionHistory.html')

@app.route('/schedule/room-schedule')
def room_schedule_view():
    if 'loggedin' not in session: return redirect(url_for('login'))
    return render_template('academic/roomScheduleView.html')

@app.route('/academic/schedule-generation')
def schedule_generation_view():
    if 'loggedin' not in session: return redirect(url_for('login'))
    programs_data = query_db("SELECT programcode, programname FROM programs WHERE isactive = true ORDER BY programname;")
    programs = [{'code': p['programcode'], 'name': p['programname']} for p in (programs_data or [])]
    acad_years = [a['academicyearid'] for a in (query_db("SELECT academicyearid FROM academicyear ORDER BY yearstart DESC;") or [])]
    curriculums = [c['curriculumyear'] for c in (query_db("SELECT DISTINCT curriculumyear FROM curriculum ORDER BY curriculumyear DESC;") or [])]
    terms = [{'id': 'A', 'name': '1ST SEMESTER'}, {'id': 'B', 'name': '2ND SEMESTER'}, {'id': 'C', 'name': 'SUMMER'}]
    return render_template('academic/scheduleGeneration.html', programs=programs, acad_years=acad_years, curriculums=curriculums, terms=terms, year_levels=[1,2,3,4,5])

@app.route('/api/schedule/generate', methods=['POST'])
def api_generate_schedule():
    data = request.json or {}
    res = scheduler_engine.generate_draft(
        data.get('program'), int(data.get('yearLevel', 1)),
        data.get('term'), data.get('curriculum'), False   # never use historical path here
    )
    if not res['success']: return jsonify({'success': False, 'error': res['error']}), 400
    return jsonify({
        'success': True,
        'batch_id': 'DRAFT-NEW-001',
        'schedule_data': [_serialize_class(cls) for cls in res['schedule_data']],
        'conflict_count': res['conflict_count'],
        'violations': res.get('violations', []),
    })


@app.route('/api/schedule/retrieve-previous', methods=['POST'])
def api_retrieve_previous_schedule():
    """
    Load the most recent Published (then Draft) schedule for the selected
    program / year level / semester — regardless of academic year.
    Bypasses the GA engine entirely: pure DB lookup.
    """
    try:
        data       = request.json or {}
        program    = data.get('program', '')
        year_level = int(data.get('yearLevel', 1))
        term       = data.get('term', '')

        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        # Find the most recent Published version first, then Draft
        cur.execute("""
            SELECT DISTINCT ON (sv.versionid)
                   sv.versionid, sv.version_number, sv.status, sv.datecreated,
                   ao.offeringcode AS programcode, cs.yearlevel, sem.semestertype AS term,
                   ay.academicyearid AS acadyear, sc.semesterid,
                   ay.yearstart, ay.yearend
            FROM   public.schedule_version sv
            JOIN   public.schedule sc         ON sv.scheduleid           = sc.scheduleid
            JOIN   public.curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   public.curriculum c         ON cs.curriculumid        = c.curriculumid
            JOIN   public.academic_offering ao ON c.academicofferingid   = ao.academicofferingid
            JOIN   public.semester sem         ON sc.semesterid          = sem.semesterid
            JOIN   public.academicyear ay      ON sem.academicyearid     = ay.academicyearid
            WHERE  UPPER(ao.offeringcode) = UPPER(%s)
              AND  cs.yearlevel           = %s
              AND  sem.semestertype       = %s
              AND  sv.status IN ('Published', 'Draft')
            ORDER BY sv.versionid,
                     CASE sv.status WHEN 'Published' THEN 1 WHEN 'Draft' THEN 2 ELSE 3 END,
                     sv.datecreated DESC
            LIMIT 1
        """, (program, year_level, term))
        ver = cur.fetchone()

        if not ver:
            cur.close(); conn.close()
            return jsonify({
                'success': False,
                'error':   (
                    f'No previous schedule found for {program} — Year {year_level} — '
                    f'{"1st Semester" if term == "A" else "2nd Semester" if term == "B" else "Summer"}. '
                    'Generate a new schedule instead.'
                ),
            }), 404

        vid = ver['versionid']

        # Load every session row for this version
        cur.execute("""
            SELECT sub.subjectcode        AS subject_code,
                   sub.subjectname        AS description,
                   sub.lecturehours       AS lec_hours,
                   sub.laboratoryhours    AS lab_hours,
                   sub.creditunits        AS credit_units,
                   ao.offeringcode        AS course,
                   sc.employeenumber      AS faculty_id,
                   CONCAT(f.lastname, ', ', f.firstname) AS instructor,
                   ss.daydesc             AS day,
                   TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_str,
                   TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_str,
                   r.roomname             AS room,
                   r.roomid               AS room_id
            FROM   public.schedule_sessions ss
            JOIN   public.schedule_version sv  ON ss.versionid           = sv.versionid
            JOIN   public.schedule sc           ON sv.scheduleid          = sc.scheduleid
            JOIN   public.curriculumsubject cs  ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   public.subject sub           ON cs.subjectcode         = sub.subjectcode
            JOIN   public.curriculum c          ON cs.curriculumid        = c.curriculumid
            JOIN   public.academic_offering ao  ON c.academicofferingid   = ao.academicofferingid
            LEFT JOIN public.faculty f          ON sc.employeenumber      = f.employeenumber
            LEFT JOIN public.room r             ON ss.roomid              = r.roomid
            LEFT JOIN public.timeslot ts_s      ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN public.timeslot ts_e      ON ss.endtimeid           = ts_e.timeid
            WHERE  ss.versionid = %s
            ORDER BY sub.subjectcode, ts_s.timevalue
        """, (vid,))
        rows = cur.fetchall()
        cur.close(); conn.close()

        if not rows:
            return jsonify({
                'success': False,
                'error':   'Previous schedule version exists but has no sessions saved.',
            }), 404

        # Group rows by (subject_code, faculty_id, start, end) — accumulate days
        _ABBR = {
            'Monday': 'MON', 'Tuesday': 'TUE', 'Wednesday': 'WED',
            'Thursday': 'THU', 'Friday': 'FRI', 'Saturday': 'SAT', 'Sunday': 'SUN',
        }
        groups = {}
        for row in rows:
            key = (row['subject_code'], row['faculty_id'], row['start_str'], row['end_str'])
            if key not in groups:
                lh  = row['lec_hours']  or 0
                lbh = row['lab_hours']  or 0
                cu  = row['credit_units'] or 0
                groups[key] = {
                    'subject_code':  row['subject_code'],
                    'description':   row['description'],
                    'lec_hours':     lh,
                    'lab_hours':     lbh,
                    'credit_units':  cu,
                    'units':         cu,
                    'course':        row['course'],
                    'faculty_id':    row['faculty_id'],
                    'instructor':    row['instructor'] or 'TBA',
                    'room':          row['room'] or 'TBA',
                    'room_id':       row['room_id'],
                    'time':          f"{row['start_str']} – {row['end_str']}",
                    'hours':         str(lh + lbh),
                    'days_list':     [],
                    'is_historical': True,
                }
            g = groups[key]
            if row['day'] and row['day'] not in g['days_list']:
                g['days_list'].append(row['day'])

        schedule_data = []
        for entry in groups.values():
            dl = entry['days_list']
            entry['days'] = '/'.join(_ABBR.get(d, d[:3].upper()) for d in dl)
            entry['day']  = dl[0] if dl else ''
            schedule_data.append(entry)

        term_label = ('1st Semester' if term == 'A'
                      else '2nd Semester' if term == 'B' else 'Summer')
        ay_label   = f"AY{ver['yearstart']}{str(ver['yearend'])[-2:]}"

        return jsonify({
            'success':        True,
            'schedule_data':  schedule_data,
            'conflict_count': 0,
            'violations':     [],
            'retrieved_from': {
                'version':    ver['version_number'],
                'status':     ver['status'],
                'acadYear':   ver['acadyear'],
                'ay_label':   ay_label,
                'term':       term_label,
            },
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/save-draft', methods=['POST'])
def api_save_draft():
    try:
        data, ctx = request.json or {}, (request.json or {}).get('context', {})
        program, year_level, term, ay = ctx.get('program'), int(ctx.get('yearLevel')), ctx.get('term'), ctx.get('acadYear')
        # scheduler_mode drives source tagging so Local/Official drafts stay in separate namespaces
        scheduler_mode = (data.get('scheduler_mode') or 'official').strip()
        draft_source   = 'local' if scheduler_mode == 'local' else 'manual_editor'

        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        sem_id = _get_semester_id(cur, ay, term)

        # Each save creates a new revision number so that version history
        # accumulates R1, R2, R3... Count only records from the same source namespace.
        _ensure_source_col(cur)
        cur.execute("""
            SELECT COALESCE(MAX(sv.version_number), 0) AS max_v
            FROM public.schedule_version sv
            JOIN public.schedule s ON sv.scheduleid = s.scheduleid
            JOIN public.curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
            JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
            JOIN public.academic_offering ao ON c.academicofferingid = ao.academicofferingid
            WHERE UPPER(ao.offeringcode) = UPPER(%s)
              AND cs.yearlevel = %s
              AND s.semesterid = %s
              AND sv.source = %s
        """, (program, year_level, sem_id, draft_source))
        new_v = cur.fetchone()['max_v'] + 1

        schedule_list = list(data.get('schedule_data', []))
        
        # --- FIX: Kuhanin ang listahan ng mga subject na sadyang binura ---
        deleted_subjects = data.get('deleted_subjects', [])
        
        submitted_codes = list({
            (cls.get('subjectcode') or cls.get('subject_code') or '').upper()
            for cls in schedule_list
            if cls.get('subjectcode') or cls.get('subject_code')
        })

        # Pagsamahin ang mga sadyang binura at ang mga normal na sinubmit para ma-archive ang lumang draft nila
        all_codes_to_archive = list(set(submitted_codes + [s.upper() for s in deleted_subjects]))

        # ── Pre-save CSP validation — block if any hard constraint is violated ──
        _pre_rehydrated = _rehydrate_schedule(list(schedule_list))
        if _pre_rehydrated:
            from scheduler import CSPValidator as _CSP
            from database import load_scheduler_config as _lsc_pre
            _hc_cfg_pre = _lsc_pre()
            _fmap_pre   = _load_faculty_map()
            _viols_pre  = _CSP(config=_hc_cfg_pre).validate(_pre_rehydrated, _fmap_pre)
            if _viols_pre:
                cur.close(); conn.close()
                return jsonify({
                    'success':    False,
                    'error':      f'{len(_viols_pre)} constraint violation(s) must be resolved before saving as draft.',
                    'violations': _viols_pre
                }), 400

        # ── Server-side section conflict check ──
        rehydrated = _rehydrate_schedule(list(schedule_list))
        incoming = []
        for cls in rehydrated:
            s_code = (cls.get('subjectcode') or cls.get('subject_code') or '').upper()
            day    = cls.get('daydesc') or cls.get('day') or ''
            st, et = cls.get('start_time'), cls.get('end_time')
            if not (day and st and et): continue
            cur.execute("SELECT timeid FROM public.timeslot WHERE timevalue = %s::time LIMIT 1", (str(st),))
            r_st = cur.fetchone()
            cur.execute("SELECT timeid FROM public.timeslot WHERE timevalue = %s::time LIMIT 1", (str(et),))
            r_et = cur.fetchone()
            if r_st and r_et:
                incoming.append({'code': s_code, 'day': day, 'start': r_st['timeid'], 'end': r_et['timeid']})

        for i, a in enumerate(incoming):
            for b in incoming[i+1:]:
                if a['day'] == b['day'] and a['start'] < b['end'] and a['end'] > b['start']:
                    cur.close(); conn.close()
                    return jsonify({'success': False,
                        'error': f"Section conflict: {a['code']} and {b['code']} overlap on {a['day']}."})

        if incoming:
            cur.execute("""
                SELECT UPPER(cs2.subjectcode) AS subjectcode, ss.daydesc, ss.starttimeid, ss.endtimeid
                FROM   schedule_sessions ss
                JOIN   schedule_version sv  ON ss.versionid           = sv.versionid
                JOIN   schedule sc          ON sv.scheduleid           = sc.scheduleid
                JOIN   sections sec         ON sc.sectionid            = sec.sectionid
                JOIN   program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                JOIN   academic_offering ao  ON pyl.academicofferingid = ao.academicofferingid
                JOIN   curriculumsubject cs2 ON sc.curriculumsubjectid = cs2.curriculumsubjectid
                WHERE  UPPER(ao.offeringcode) = UPPER(%s)
                  AND  pyl.yearlevel           = %s
                  AND  sc.semesterid         = %s
                  AND  sv.status IN ('Published', 'Draft')
                  AND  UPPER(cs2.subjectcode) NOT IN ({})
            """.format(','.join(['%s'] * len(submitted_codes))),
            [program, year_level, sem_id] + submitted_codes)
            existing = cur.fetchall()
            for sess in (existing or []):
                for inc in incoming:
                    if sess['daydesc'] == inc['day'] and \
                       sess['starttimeid'] < inc['end'] and sess['endtimeid'] > inc['start']:
                        cur.close(); conn.close()
                        return jsonify({'success': False,
                            'error': (f"Section conflict: {inc['code']} overlaps with "
                                      f"{sess['subjectcode']} already scheduled on {sess['daydesc']}.")})

        # Carry forward only subjects that have an active Draft (not Published-only ones).
        # This keeps the Draft snapshot pure — it only contains genuine Draft-state content.
        # Published-only subjects stay in their Published version and are NOT duplicated into Draft.
        # The UI merges Draft + Published for display via existing_sessions; the snapshot must not.
        other_sessions = _fetch_section_sessions(cur, program, year_level, sem_id,
                                                  exclude_codes=all_codes_to_archive,
                                                  draft_only=True)

        # Archive all Draft records for this section + source namespace before writing new snapshot.
        _archive_status(cur, program, year_level, term, sem_id, 'Draft', source=draft_source)

        # Draft snapshot = other subjects' Drafts (carry-forward) + newly submitted Draft slices.
        # NOTE: Published baseline is intentionally excluded — Draft must only contain Draft content.
        #       The Published slices are shown in the UI via existing_sessions without being in Draft.
        complete_snapshot = other_sessions + rehydrated
        if complete_snapshot:
            _insert_batch(cur, complete_snapshot, sem_id, 'Draft', new_v, program, year_level,
                          source=draft_source)

        # Clean up any faculty-only assignments whose subjects now have real sessions
        if submitted_codes:
            try:
                _ensure_faculty_assignment_table(cur)
                ph = ','.join(['%s'] * len(submitted_codes))
                cur.execute(f"""
                    DELETE FROM public.subject_faculty_assignment
                    WHERE UPPER(programcode) = UPPER(%s)
                      AND yearlevel          = %s
                      AND semesterid         = %s
                      AND UPPER(subjectcode) IN ({ph})
                """, [program.upper(), year_level, sem_id] + submitted_codes)
            except Exception:
                pass  # non-fatal: table may not exist yet on first save

        conn.commit(); cur.close(); conn.close()

        return jsonify({'success': True, 'draft_version': new_v})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedule/delete_session', methods=['POST'])
def api_delete_session():
    """Archive a specific schedule_version row (effectively deletes one time-slot session).
    Works for both Draft and Published versions."""
    data       = request.json or {}
    version_id = data.get('version_id')
    if not version_id:
        return jsonify({'success': False, 'error': 'Missing version_id'}), 400
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            UPDATE schedule_version
               SET status = 'Archive'
             WHERE versionid = %s
               AND status IN ('Draft', 'Published')
        """, (int(version_id),))
        deleted = cur.rowcount
        conn.commit()
        cur.close(); conn.close()
        if deleted == 0:
            return jsonify({'success': False, 'error': 'Session not found or already deleted'}), 404
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/draft_sessions')
def api_draft_sessions():
    """Return current Draft sessions for a program/year/ay/sem, formatted for the approve endpoint."""
    program    = request.args.get('program', '').strip()
    year_level = request.args.get('year_level', '').strip()
    ay         = request.args.get('ay_id', '').strip()
    sem        = request.args.get('sem', '').strip()
    if not (program and year_level and ay and sem):
        return jsonify({'success': False, 'error': 'Missing parameters'})
    try:
        rows = query_db("""
            SELECT ss.starttimeid, ss.endtimeid, ss.daydesc,
                   sub.subjectcode, sub.subjectname,
                   COALESCE(sub.creditunits, 0) AS creditunits,
                   f.employeenumber AS faculty_id,
                   f.lastname || ', ' || f.firstname AS instructor,
                   r.roomid AS room_id, r.roomname,
                   sv.status,
                   TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_time,
                   TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_time
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub ON cs.subjectcode = sub.subjectcode
            JOIN curriculum cu ON cs.curriculumid = cu.curriculumid
            JOIN academic_offering ao ON cu.academicofferingid = ao.academicofferingid
            LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN faculty f ON sc.employeenumber = f.employeenumber
            LEFT JOIN room r ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
            WHERE sv.status = 'Draft'
              AND UPPER(ao.offeringcode) = UPPER(%s)
              AND cs.yearlevel = %s
              AND sc.semesterid = (
                    SELECT semesterid FROM semester
                    WHERE academicyearid = %s AND semestertype = %s LIMIT 1)
        """, (program, int(year_level), ay, sem))
        sessions = []
        for r in (rows or []):
            sessions.append({
                'subject_code': r['subjectcode'],
                'subject_name': r['subjectname'],
                'faculty_id':   r['faculty_id'],
                'instructor':   r['instructor'] or '',
                'room_id':      str(r['room_id']) if r['room_id'] else '',
                'room':         r['roomname'] or '',
                'day':          r['daydesc'],
                'days':         (r['daydesc'] or '')[:3].upper(),
                'days_list':    [r['daydesc']],
                'start_time':   r['start_time'] or '',
                'end_time':     r['end_time']   or '',
                'time':         f"{r['start_time']} - {r['end_time']}" if r['start_time'] else '',
                'units':        float(r['creditunits'] or 0),
                'ay': ay, 'sem': sem,
                # _loadExistingSessionsIntoSlices-compatible aliases
                'subjectcode':  r['subjectcode'],
                'subjectname':  r['subjectname'],
                'daydesc':      r['daydesc'],
                'starttimeid':  r['starttimeid'],
                'endtimeid':    r['endtimeid'],
                'roomname':     r['roomname'] or '',
                'status':       'Draft',
            })
        return jsonify({'success': True, 'sessions': sessions})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/schedule/validate', methods=['POST'])
def api_validate_schedule():
    """Run CSP validation on submitted schedule_data and return violations without saving."""
    try:
        data        = request.json or {}
        rehydrated  = _rehydrate_schedule(list(data.get('schedule_data', [])))
        faculty_map = _load_faculty_map()
        from scheduler import CSPValidator
        from database import load_scheduler_config as _lsc
        _hc_cfg    = _lsc()
        violations  = CSPValidator(config=_hc_cfg).validate(rehydrated, faculty_map)
        return jsonify({'success': True, 'violations': violations, 'has_violations': bool(violations)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/approve', methods=['POST'])
def api_approve_schedule():
    try:
        data, ctx = request.json or {}, (request.json or {}).get('context', {})
        program, year_level, term, ay = ctx.get('program'), int(ctx.get('yearLevel')), ctx.get('term'), ctx.get('acadYear')

        # ── Server-side CSP guard ── block approval if any hard constraint is violated
        from scheduler import CSPValidator
        rehydrated  = _rehydrate_schedule(list(data.get('schedule_data', [])))
        faculty_map = _load_faculty_map()

        # Load HC config to honour the publish-gate toggle
        from database import load_scheduler_config as _load_hc_cfg
        _hc_cfg = _load_hc_cfg()
        publish_gate_on = bool(_hc_cfg.get('hc_publish_gate_enabled', 1))

        violations = CSPValidator(config=_hc_cfg).validate(rehydrated, faculty_map)

        if publish_gate_on and violations:
            return jsonify({
                'success': False,
                'error':   f'Cannot approve: {len(violations)} unresolved constraint violation(s). '
                           'Resolve all conflicts in the Manual Editor before approving.',
                'violations': violations,
            }), 400
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        sem_id = _get_semester_id(cur, ay, term)

        _ensure_source_col(cur)
        cur.execute("""
    SELECT COALESCE(MAX(sv.version_number), 0) AS max_v
    FROM public.schedule_version sv
    JOIN public.schedule s ON sv.scheduleid = s.scheduleid
    JOIN public.curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
    JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
    JOIN public.academic_offering ao ON c.academicofferingid = ao.academicofferingid
    WHERE UPPER(ao.offeringcode) = UPPER(%s)
      AND cs.yearlevel = %s
      AND s.semesterid = %s
      AND sv.source = 'manual_editor'
""", (program, year_level, sem_id))
        max_v = cur.fetchone()['max_v']

        sched_data = _rehydrate_schedule(list(data.get('schedule_data', [])))
        submitted_codes = list({
            (cls.get('subject_code') or cls.get('subjectcode') or '').upper()
            for cls in sched_data
            if cls.get('subject_code') or cls.get('subjectcode')
        })
        # Carry forward only currently Published subjects (not Draft-only ones).
        # Draft-only subjects must NOT appear in a Published snapshot unless explicitly published.
        other_sessions = _fetch_section_sessions(
            cur, program, year_level, sem_id,
            exclude_codes=submitted_codes,
            published_only=True
        )

        # When publishing new Draft slices for a subject that already has Published slices,
        # the existing Published slots (e.g. Friday) must survive in the new Published snapshot
        # alongside the newly published slots (e.g. Tuesday). Without this, publishing one slice
        # replaces the entire Published schedule for that subject.
        published_baseline = _build_published_baseline(cur, program, year_level, sem_id,
                                                        submitted_codes, sched_data)

        # Archive only the existing Published revisions — Draft history must remain intact.
        # Publishing creates a new independent Published snapshot; it does not consume the Draft.
        _archive_status(cur, program, year_level, term, sem_id, 'Published', source='manual_editor')

        # Insert Published snapshot: other subjects' Published sessions + existing Published baseline
        # for the submitted subject + the newly published sessions.
        complete_snapshot = other_sessions + published_baseline + sched_data
        _insert_batch(cur, complete_snapshot, sem_id, 'Published', max_v + 1, program, year_level)

        # Auto-cleanup: remove ONLY the exact slots that were just published from the active Draft.
        # Use slot-level keys (subject+day+time), NOT subject codes — a subject can have multiple
        # slices and publishing one slot (e.g. Thursday) must NOT wipe unrelated Draft slots for
        # the same subject (e.g. Monday Draft).
        published_slot_keys = set()
        for s in sched_data:
            sc  = (s.get('subjectcode') or s.get('subject_code') or '').upper()
            day = s.get('daydesc') or s.get('day') or ''
            st  = str(s.get('start_time', '')).split('.')[0]
            published_slot_keys.add((sc, day, st))

        all_current_draft = _fetch_section_sessions(
            cur, program, year_level, sem_id,
            draft_only=True
        )
        remaining_draft = [
            s for s in all_current_draft
            if ((s.get('subjectcode') or '').upper(),
                s.get('daydesc') or '',
                str(s.get('start_time', '')).split('.')[0]) not in published_slot_keys
        ]

        # Only rebuild Draft if at least one slot was actually removed (i.e. a Draft slot was
        # published). If the published slots were already Published (not in Draft), leave Draft
        # completely untouched — no new revision, no data loss.
        if len(remaining_draft) < len(all_current_draft):
            _archive_status(cur, program, year_level, term, sem_id, 'Draft', source='manual_editor')
            if remaining_draft:
                _insert_batch(cur, remaining_draft, sem_id, 'Draft', max_v + 2, program, year_level)

        conn.commit(); cur.close(); conn.close()
        return jsonify({'success': True, 'published_version': max_v + 1})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedule/drafts/<int:version_id>', methods=['DELETE'])
def api_delete_draft(version_id):
    """Archive all Draft schedule_versions that share the same program/yearlevel/semester as version_id."""
    try:
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        # Resolve program/year/term/semesterid from the given versionid.
        # The versionid may belong to any one Draft row for this group — that's enough to identify the group.
        cur.execute("""
            SELECT ao.offeringcode AS programcode, cs.yearlevel, cs.semester AS term, s.semesterid
            FROM public.schedule_version sv
            JOIN public.schedule s ON sv.scheduleid = s.scheduleid
            JOIN public.curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
            JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
            JOIN public.academic_offering ao ON c.academicofferingid = ao.academicofferingid
            WHERE sv.versionid = %s
            LIMIT 1
        """, (version_id,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Version not found'}), 404
        _archive_status(cur, row['programcode'], row['yearlevel'], row['term'], row['semesterid'], 'Draft')
        conn.commit(); cur.close(); conn.close()
        return jsonify({'success': True})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/faculty/teaching_assignments')
def api_faculty_teaching_assignments():
    emp_num = request.args.get('emp_num', '').strip()
    ay_id   = request.args.get('ay_id', '').strip()
    sem     = request.args.get('sem', '').strip()
    if not emp_num or not ay_id or not sem:
        return jsonify({'success': False, 'error': 'Missing parameters'}), 400
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT f.employeenumber,
                   f.lastname || ', ' || f.firstname AS fullname,
                   COALESCE(et.typename, f.employeestatus, 'Regular')  AS employee_type,
                   f.employeestatus,
                   f.designationid,
                   COALESCE(et.regularload,          0) AS reg_load,
                   COALESCE(et.parttimeload,          0) AS pt_load,
                   COALESCE(et.teachingsubstitution,  0) AS teach_sub,
                   COALESCE(d.regularloadunit,        0) AS desig_reg_load,
                   COALESCE(d.nightteachingservice,   0) AS desig_night_service
            FROM faculty f
            LEFT JOIN employeetype et  ON f.employeetypeid = et.employeetypeid
            LEFT JOIN designation  d   ON f.designationid  = d.designationid
            WHERE f.employeenumber = %s
        """, (emp_num,))
        fac = cur.fetchone()
        if not fac:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Faculty not found'}), 404

        # Mirror the same max-load logic used by /api/manual/faculty_load
        _status    = (fac['employeestatus'] or '').lower()
        _has_desig = fac['designationid'] is not None
        _teach_sub = int(fac['teach_sub'] or 0)
        if _has_desig:
            _reg  = int(fac['desig_reg_load']    or 0)
            _pt   = int(fac['desig_night_service'] or 0)
        elif 'part' in _status:
            _reg  = 0
            _pt   = int(fac['pt_load'] or 0)
        else:
            _reg  = int(fac['reg_load'] or 0)
            _pt   = int(fac['pt_load']  or 0)
        max_units = _reg + _pt + _teach_sub
        cur.execute("""
            SELECT
                sub.subjectcode,
                sub.subjectname,
                (COALESCE(sub.lecturehours,0) + COALESCE(sub.laboratoryhours,0)) AS units,
                COALESCE(ao.offeringcode,'') || '-' || COALESCE(pyl.yearlevel::text,'')
                    || ' ' || COALESCE(sec.sectionname,'') AS year_section,
                sem.semestertype AS subj_ref,
                TO_CHAR(ts_s.timevalue,'HH12:MI AM') || ' - ' || TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS time_range,
                LPAD(EXTRACT(HOUR FROM ts_s.timevalue)::text,2,'0') ||
                LPAD(EXTRACT(HOUR FROM ts_e.timevalue)::text,2,'0') AS time_code,
                ss.daydesc AS days,
                COALESCE(r.roomname,'—') AS room,
                COALESCE(TO_CHAR(sem.semstartdate,'MM/DD/YYYY'),'—') AS effectivity,
                sv.status
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub ON cs.subjectcode = sub.subjectcode
            JOIN semester sem ON sc.semesterid = sem.semesterid
            LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
            LEFT JOIN room r ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
            WHERE sc.employeenumber = %s
              AND sem.academicyearid = %s
              AND sem.semestertype   = %s
              AND sv.status IN ('Published','Draft')
            ORDER BY sub.subjectcode, ts_s.timevalue
        """, (emp_num, ay_id, sem))
        sessions = [dict(r) for r in (cur.fetchall() or [])]
        cur.execute("""
            SELECT COALESCE(SUM(d.units),0) AS total FROM (
                SELECT DISTINCT cs.subjectcode,
                    (COALESCE(sub.lecturehours,0)+COALESCE(sub.laboratoryhours,0)) AS units
                FROM schedule_version sv
                JOIN schedule sc ON sv.scheduleid=sc.scheduleid
                JOIN curriculumsubject cs ON sc.curriculumsubjectid=cs.curriculumsubjectid
                JOIN subject sub ON cs.subjectcode=sub.subjectcode
                JOIN semester sem ON sc.semesterid=sem.semesterid
                WHERE sc.employeenumber=%s AND sem.academicyearid=%s
                  AND sem.semestertype=%s AND sv.status IN ('Published','Draft')
            ) d
        """, (emp_num, ay_id, sem))
        total_row = cur.fetchone()
        assigned  = int(total_row['total'] or 0) if total_row else 0
        cur.close(); conn.close()
        return jsonify({
            'success': True,
            'faculty': dict(fac),
            'sessions': sessions,
            'assigned_units': assigned,
            'max_units': max_units,
            'available_units': max(0, max_units - assigned)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/faculty/assigned_list')
def api_faculty_assigned_list():
    """Return faculty who have assignments for the given AY/sem, optionally filtered by program + year level."""
    ay_id      = request.args.get('ay_id',      '').strip()
    sem        = request.args.get('sem',        '').strip()
    prog       = request.args.get('prog',       '').strip()
    year_level = request.args.get('year_level', '').strip()
    if not ay_id or not sem:
        return jsonify({'success': False, 'error': 'Missing parameters'}), 400
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        extra_where = ''
        params = [ay_id, sem]
        if prog:
            extra_where += ' AND UPPER(ao.offeringcode) = UPPER(%s)'
            params.append(prog)
        if year_level:
            extra_where += ' AND cs.yearlevel = %s'
            params.append(year_level)

        cur.execute(f"""
            SELECT DISTINCT
                f.employeenumber AS emp_num,
                f.lastname || ', ' || f.firstname
                    || COALESCE(' ' || LEFT(f.middlename, 1) || '.', '') AS name
            FROM faculty f
            JOIN schedule sc         ON sc.employeenumber = f.employeenumber
            JOIN semester s          ON sc.semesterid     = s.semesterid
            JOIN schedule_version sv ON sv.scheduleid     = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c         ON cs.curriculumid        = c.curriculumid
            JOIN academic_offering ao ON c.academicofferingid   = ao.academicofferingid
            WHERE s.academicyearid = %s
              AND s.semestertype   = %s
              AND sv.status IN ('Draft', 'Published')
              {extra_where}
            ORDER BY name
        """, params)
        rows = cur.fetchall()
        cur.close(); conn.close()
        return jsonify({'success': True, 'faculty': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/drafts')
@app.route('/api/schedule/versions')
def api_list_versions():
    try:
        # Idempotent: add source column if it doesn't exist yet
        try:
            query_db("ALTER TABLE public.schedule_version "
                     "ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'official'")
        except Exception:
            pass
        is_drafts = 'drafts' in request.path
        if is_drafts:
            # One consolidated entry per program/year/semester — use the latest versionid/version_number
            rows = query_db("""
                SELECT DISTINCT ON (ao.offeringcode, cs.yearlevel, cs.semester, ay.academicyearid)
                       sv.versionid, sv.version_number, sv.status, sv.datecreated,
                       COALESCE(sv.source, 'official') AS source,
                       ao.offeringcode AS programcode, cs.yearlevel, cs.semester AS term, ay.academicyearid AS acadyear
                FROM   public.schedule_version sv
                JOIN   public.schedule sg ON sv.scheduleid = sg.scheduleid
                JOIN   public.curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
                JOIN   public.curriculum c ON cs.curriculumid = c.curriculumid
                JOIN   public.academic_offering ao ON c.academicofferingid = ao.academicofferingid
                JOIN   public.semester sem ON sg.semesterid = sem.semesterid
                JOIN   public.academicyear ay ON sem.academicyearid = ay.academicyearid
                WHERE  sv.status = 'Draft'
                ORDER BY ao.offeringcode, cs.yearlevel, cs.semester, ay.academicyearid, sv.version_number DESC
            """)
        else:
            # CTE groups all subjects per section per version_number into one aggregated row.
            # original_status stores whether the revision was created as Draft or Published
            # (survives archiving). Two independent window-function counters derive
            # draft_version_number and published_version_number from their own sequences.
            rows = query_db("""
                WITH versioned AS (
                    SELECT MAX(sv.versionid)        AS versionid,
                           sv.version_number,
                           CASE
                               WHEN bool_or(sv.status = 'Draft')      THEN 'Draft'
                               WHEN bool_and(sv.status = 'Published') THEN 'Published'
                               ELSE 'Archive'
                           END                      AS status,
                           MAX(sv.datecreated)      AS datecreated,
                           -- All rows for a version share the same original_status; MAX picks it up.
                           MAX(sv.original_status)  AS original_status,
                           ao.offeringcode          AS programcode,
                           cs.yearlevel,
                           cs.semester              AS term,
                           ay.academicyearid        AS acadyear
                    FROM   public.schedule_version sv
                    JOIN   public.schedule sg          ON sv.scheduleid           = sg.scheduleid
                    JOIN   public.curriculumsubject cs  ON sg.curriculumsubjectid  = cs.curriculumsubjectid
                    JOIN   public.curriculum c          ON cs.curriculumid         = c.curriculumid
                    JOIN   public.academic_offering ao  ON c.academicofferingid    = ao.academicofferingid
                    JOIN   public.semester sem          ON sg.semesterid           = sem.semesterid
                    JOIN   public.academicyear ay       ON sem.academicyearid      = ay.academicyearid
                    GROUP BY ao.offeringcode, cs.yearlevel, cs.semester, ay.academicyearid, sv.version_number
                )
                SELECT *,
                       CASE WHEN COALESCE(original_status, status) = 'Draft' THEN
                           SUM(CASE WHEN COALESCE(original_status, status) = 'Draft' THEN 1 ELSE 0 END)
                               OVER (PARTITION BY programcode, yearlevel, term, acadyear
                                     ORDER BY version_number
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                       END AS draft_version_number,
                       CASE WHEN COALESCE(original_status, status) = 'Published' THEN
                           SUM(CASE WHEN COALESCE(original_status, status) = 'Published' THEN 1 ELSE 0 END)
                               OVER (PARTITION BY programcode, yearlevel, term, acadyear
                                     ORDER BY version_number
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                       END AS published_version_number
                FROM versioned
                ORDER BY programcode, yearlevel, term, acadyear, version_number
            """)
        output = []
        for r in (rows or []):
            dt = r['datecreated']
            output.append({
                'versionid':               r['versionid'],
                'version_number':          r['version_number'],
                'draft_version_number':    r.get('draft_version_number'),
                'published_version_number': r.get('published_version_number'),
                'original_status':         r.get('original_status'),
                'status':                  r['status'],
                'datecreated':             dt.isoformat() if hasattr(dt, 'isoformat') else str(dt),
                'programcode':             r['programcode'],
                'yearlevel':               r['yearlevel'],
                'term':                    r['term'],
                'acadyear':                r['acadyear'],
                'source':                  r.get('source', 'official'),
            })
        return jsonify(sorted(output, key=lambda x: x['datecreated'], reverse=True))
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/api/schedule/versions/<int:version_id>/restore', methods=['POST'])
def api_restore_version(version_id):
    """Restore a historical revision with two modes:

    restore_mode='draft'   (Recommended): Always create a new Draft revision from the snapshot.
                           The current Published history is never touched — only existing Draft
                           is archived first. Safe: nothing is permanently lost.

    restore_mode='replace' (default/legacy): Archive the matching active type (same as original_status)
                           and create a new revision of the same type. Replaces the current active
                           schedules for that type.
    """
    try:
        body         = request.json or {}
        restore_mode = body.get('restore_mode', 'replace')   # 'draft' | 'replace'

        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_source_col(cur)
        _ensure_original_status_col(cur)

        # Fetch context from any versionid that belongs to this snapshot group
        cur.execute("""
            SELECT ao.offeringcode AS programcode, cs.yearlevel, cs.semester AS term, sg.semesterid,
                   sv.version_number AS src_vn,
                   sv.original_status AS src_orig
            FROM   schedule_version sv
            JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
            JOIN   academic_offering ao  ON c.academicofferingid   = ao.academicofferingid
            WHERE  sv.versionid = %s LIMIT 1
        """, (version_id,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Version not found'}), 404

        prog, year_level, term, sem_id, src_vn = (
            row['programcode'], row['yearlevel'], row['term'],
            row['semesterid'], row['src_vn']
        )

        if restore_mode == 'draft':
            # Restore as Draft: only archive the current Draft (if any). Published is untouched.
            # The subject may show PUB/DRAFT if it also has an active Published version — that
            # is correct: the Published approved baseline coexists with the new Draft working copy.
            _archive_status(cur, prog, year_level, term, sem_id, 'Draft', source='manual_editor')
            target_status = 'Draft'
        else:
            # Full replace: archive the same type as the source, create new same-type revision.
            restore_as = row['src_orig'] or 'Draft'
            if restore_as == 'Published':
                _archive_status(cur, prog, year_level, term, sem_id, 'Published', source='manual_editor')
            else:
                _archive_status(cur, prog, year_level, term, sem_id, 'Draft', source='manual_editor')
            target_status = restore_as

        # New version_number = max manual_editor version for this section + 1
        cur.execute("""
            SELECT COALESCE(MAX(sv.version_number), 0) AS max_v
            FROM   schedule_version sv
            JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
            JOIN   academic_offering ao  ON c.academicofferingid   = ao.academicofferingid
            WHERE  UPPER(ao.offeringcode) = UPPER(%s) AND cs.yearlevel = %s AND sg.semesterid = %s
              AND  sv.source = 'manual_editor'
        """, (prog, year_level, sem_id))
        new_v = cur.fetchone()['max_v'] + 1

        # Collect all schedule_version rows at the source version_number for this section
        cur.execute("""
            SELECT sv.versionid, sv.scheduleid
            FROM   schedule_version sv
            JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
            JOIN   academic_offering ao  ON c.academicofferingid   = ao.academicofferingid
            WHERE  UPPER(ao.offeringcode) = UPPER(%s) AND cs.yearlevel = %s
              AND  sg.semesterid = %s AND sv.version_number = %s
        """, (prog, year_level, sem_id, src_vn))
        src_rows = cur.fetchall()

        for src in src_rows:
            cur.execute("""
                INSERT INTO public.schedule_version
                    (scheduleid, version_number, status, datecreated, source, original_status)
                VALUES (%s, %s, %s, NOW(), 'manual_editor', %s) RETURNING versionid
            """, (src['scheduleid'], new_v, target_status, target_status))
            new_vid = cur.fetchone()['versionid']

            cur.execute("""
                INSERT INTO public.schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid)
                SELECT %s, daydesc, starttimeid, endtimeid, roomid
                FROM   public.schedule_sessions
                WHERE  versionid = %s
            """, (new_vid, src['versionid']))

        conn.commit(); cur.close(); conn.close()
        return jsonify({'success': True, 'new_version': new_v, 'restore_as': target_status})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/versions/<int:version_id>/check')
def api_check_version_state(version_id):
    """Return the current Draft/Published state for the program/year/semester of a version.
    Used by the frontend to show context-aware restore warnings before performing a restore."""
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_source_col(cur)
        _ensure_original_status_col(cur)

        cur.execute("""
            SELECT ao.offeringcode AS programcode, cs.yearlevel, sg.semesterid, sv.status
            FROM   schedule_version sv
            JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
            JOIN   academic_offering ao  ON c.academicofferingid   = ao.academicofferingid
            WHERE  sv.versionid = %s LIMIT 1
        """, (version_id,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Version not found'}), 404

        prog, year_level, sem_id, this_status = (
            row['programcode'], row['yearlevel'], row['semesterid'], row['status']
        )

        cur.execute("""
            SELECT DISTINCT sv.status
            FROM   schedule_version sv
            JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
            JOIN   academic_offering ao  ON c.academicofferingid   = ao.academicofferingid
            WHERE  UPPER(ao.offeringcode) = UPPER(%s)
              AND  cs.yearlevel = %s
              AND  sg.semesterid = %s
              AND  sv.status IN ('Draft', 'Published')
        """, (prog, year_level, sem_id))
        active_statuses = {r['status'] for r in cur.fetchall()}

        cur.close(); conn.close()
        return jsonify({
            'success': True,
            'isCurrentlyPublished': (this_status or '').lower() == 'published',
            'hasActiveDraft':       'Draft'     in active_statuses,
            'hasActivePublished':   'Published' in active_statuses
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/versions/<int:version_id>/sessions')
def api_overlay_sessions(version_id):
    """Return all schedule sessions for a historical versionid for timetable overlay comparison."""
    try:
        rows = query_db("""
            WITH ref AS (
                SELECT sv.version_number, ao.offeringcode AS programcode, cs.yearlevel, sg.semesterid
                FROM   schedule_version sv
                JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
                JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
                JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
                JOIN   academic_offering ao  ON c.academicofferingid   = ao.academicofferingid
                WHERE  sv.versionid = %s
                LIMIT  1
            )
            SELECT sub.subjectcode, sub.subjectname,
                   COALESCE(f.lastname || ', ' || f.firstname, 'TBA') AS instructor,
                   COALESCE(r.roomname, 'TBA')                        AS roomname,
                   ss.daydesc,
                   TO_CHAR(ts_s.timevalue, 'HH24:MI')                AS start_time,
                   TO_CHAR(ts_e.timevalue, 'HH24:MI')                AS end_time,
                   COALESCE(sub.lecturehours,    0)                   AS lecturehours,
                   COALESCE(sub.laboratoryhours, 0)                   AS laboratoryhours,
                   COALESCE(sub.creditunits,     0)                   AS creditunits,
                   sv.status, sv.versionid, sv.version_number
            FROM   ref
            JOIN   schedule_version sv  ON sv.version_number = ref.version_number
            JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   subject sub           ON cs.subjectcode         = sub.subjectcode
            JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
            JOIN   academic_offering ao  ON c.academicofferingid   = ao.academicofferingid
            LEFT JOIN faculty f          ON sg.employeenumber      = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid         = sv.versionid
            LEFT JOIN room r             ON ss.roomid              = r.roomid
            LEFT JOIN timeslot ts_s      ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN timeslot ts_e      ON ss.endtimeid           = ts_e.timeid
            WHERE  ao.offeringcode = ref.programcode
              AND  cs.yearlevel    = ref.yearlevel
              AND  sg.semesterid   = ref.semesterid
            ORDER BY sub.subjectname, ts_s.timevalue NULLS LAST, ss.daydesc
        """, (version_id,)) or []
        return jsonify({'success': True, 'sessions': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/version_sessions')
def api_version_sessions():
    """Fetch all sessions for every subject at a specific version_number for a section."""
    prog    = request.args.get('program', '').strip()
    yl      = request.args.get('year_level', '').strip()
    ay_id   = request.args.get('ay_id', '').strip()
    sem     = request.args.get('semester', '').strip()
    ver_num = request.args.get('version_number', '').strip()
    if not (prog and yl and ay_id and sem and ver_num):
        return jsonify({'success': False, 'error': 'Missing parameters'}), 400
    try:
        rows = query_db("""
            SELECT sub.subjectcode, sub.subjectname,
                   COALESCE(f.lastname || ', ' || f.firstname, 'TBA') AS instructor,
                   COALESCE(r.roomname, 'TBA') AS roomname,
                   ss.daydesc,
                   TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_time,
                   TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_time,
                   sv.status
            FROM schedule_version sv
            JOIN schedule sc          ON sv.scheduleid           = sc.scheduleid
            JOIN curriculumsubject cs  ON sc.curriculumsubjectid  = cs.curriculumsubjectid
            JOIN subject sub           ON cs.subjectcode          = sub.subjectcode
            JOIN sections sec          ON sc.sectionid            = sec.sectionid
            JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN academic_offering ao  ON pyl.academicofferingid = ao.academicofferingid
            LEFT JOIN faculty f        ON sc.employeenumber       = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid        = sv.versionid
            LEFT JOIN room r           ON ss.roomid               = r.roomid
            LEFT JOIN timeslot ts_s    ON ss.starttimeid          = ts_s.timeid
            LEFT JOIN timeslot ts_e    ON ss.endtimeid            = ts_e.timeid
            WHERE UPPER(ao.offeringcode) = UPPER(%s)
              AND pyl.yearlevel           = %s
              AND sv.version_number      = %s
              AND sc.semesterid = (
                  SELECT semesterid FROM semester
                  WHERE academicyearid = %s AND semestertype = %s LIMIT 1
              )
            ORDER BY sub.subjectname, ts_s.timevalue NULLS LAST, ss.daydesc
        """, (prog, int(yl), int(ver_num), ay_id, sem)) or []
        return jsonify({'success': True, 'sessions': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/load-draft/<int:version_id>')
def api_load_draft(version_id):
    try:
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT sv.version_number, sv.status, sv.scheduleid,
                   COALESCE(sv.source, 'official') AS source,
                   ao.offeringcode AS programcode, cs.yearlevel, sem.semestertype AS term,
                   ay.academicyearid AS acadyear, sc.semesterid
            FROM schedule_version sv
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
            JOIN semester sem ON sc.semesterid = sem.semesterid
            JOIN academicyear ay ON sem.academicyearid = ay.academicyearid
            WHERE sv.versionid = %s
        """, (version_id,))
        m = cur.fetchone()
        if not m:
            # Diagnostic: check if versionid exists at all
            cur.execute("SELECT versionid, scheduleid, version_number, status FROM schedule_version WHERE versionid = %s", (version_id,))
            sv_raw = cur.fetchone()
            if not sv_raw:
                return jsonify({'success': False, 'error': f'Version ID {version_id} does not exist in schedule_version'}), 404
            return jsonify({'success': False, 'error': f'Version {version_id} exists (scheduleid={sv_raw["scheduleid"]}, status={sv_raw["status"]}) but join chain failed — linked schedule or curriculum data may be missing'}), 404
        cur.execute("""
            SELECT sub.subjectcode AS subject_code, sub.subjectname AS description, sub.lecturehours AS lec_hours,
                   sub.laboratoryhours AS lab_hours, sub.creditunits AS units, ao.offeringcode AS course,
                   sc.employeenumber AS faculty_id, CONCAT(f.lastname, ', ', f.firstname) AS instructor,
                   ss.daydesc, TO_CHAR(ts_s.timevalue, 'HH24:MI') AS start_time, TO_CHAR(ts_e.timevalue, 'HH24:MI') AS end_time,
                   r.roomname AS room, r.roomid, sv.version_number AS row_version
            FROM schedule_version sv JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN schedule_sessions ss ON ss.versionid = sv.versionid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub ON cs.subjectcode = sub.subjectcode
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
            LEFT JOIN faculty f ON sc.employeenumber = f.employeenumber
            LEFT JOIN room r ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
            WHERE ao.offeringcode = %s AND cs.yearlevel = %s AND sc.semesterid = %s AND sv.status = 'Draft'
        """, (m['programcode'], m['yearlevel'], m['semesterid']))
        rows = cur.fetchall(); cur.close(); conn.close()
        max_v = max((r['row_version'] for r in rows), default=m['version_number'])
        _abbr = {'Monday': 'MON', 'Tuesday': 'TUE', 'Wednesday': 'WED', 'Thursday': 'THU', 'Friday': 'FRI', 'Saturday': 'SAT', 'Sunday': 'SUN'}
        sched = [{
            'subject_code': r['subject_code'], 'description': r['description'], 'lec_hours': r['lec_hours'], 'lab_hours': r['lab_hours'],
            'units': r['units'], 'course': r['course'], 'faculty_id': r['faculty_id'], 'instructor': r['instructor'],
            'days': _abbr.get(r['daydesc'], r['daydesc'][:3].upper() if r['daydesc'] else ''), 'day': r['daydesc'],
            'time': f"{_fmt_12h(r['start_time'])} – {_fmt_12h(r['end_time'])}" if r['start_time'] else '',
            'hours': str((r['lec_hours'] or 0) + (r['lab_hours'] or 0)), 'room': r['room'], 'room_id': r['roomid']
        } for r in rows]
        return jsonify({'success': True, 'schedule_data': sched, 'version': max_v, 'context': {'program': m['programcode'], 'yearLevel': m['yearlevel'], 'term': m['term'], 'acadYear': m['acadyear'], 'source': m['source']}})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/curriculum-by-year')
def api_curriculum_by_year():
    p = request.args.get('program', '')
    try:
        row = query_db("""
            SELECT c.curriculumyear FROM curriculum c
            JOIN academic_offering ao ON c.academicofferingid = ao.academicofferingid
            WHERE ao.offeringcode = %s ORDER BY c.curriculumyear DESC LIMIT 1
        """, (p,))
        return jsonify({'curriculum': row[0]['curriculumyear'] if row else ''})
    except: return jsonify({'curriculum': ''}), 500

@app.route('/api/sections-by-program')
def api_sections_by_program():
    program    = request.args.get('program', '')
    year_level = request.args.get('yearLevel', '')
    try:
        params = [program]
        sql = """
            SELECT sec.sectionid, sec.sectionname
            FROM sections sec
            JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN academic_offering ao ON pyl.academicofferingid = ao.academicofferingid
            WHERE UPPER(ao.offeringcode) = UPPER(%s) AND sec.isactive = TRUE
        """
        if year_level:
            sql += " AND pyl.yearlevel = %s"
            params.append(int(year_level))
        sql += " ORDER BY sec.sectionname"
        rows = query_db(sql, tuple(params))
        return jsonify({'sections': [{'id': r['sectionid'], 'name': r['sectionname']} for r in (rows or [])]})
    except Exception as e:
        return jsonify({'sections': [], 'error': str(e)}), 500

# --- MAIN EXECUTION ---
if __name__ == '__main__':
   app.run(debug=True, use_reloader=False)
   