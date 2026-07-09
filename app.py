import os
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash, Response
from database import get_db_connection, query_db
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
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

# ── Request Tables: class_meeting_request + schedule_change_request ──────────
_request_tables_ensured = False
def _ensure_request_tables():
    global _request_tables_ensured
    if _request_tables_ensured:
        return
    _conn = get_db_connection()
    _cur  = _conn.cursor()
    try:
        _cur.execute("""
            CREATE TABLE IF NOT EXISTS public.class_meeting_request (
                requestid        SERIAL PRIMARY KEY,
                scheduleid       INT,
                requested_date   DATE,
                new_starttimeid  INT,
                new_endtimeid    INT,
                new_roomid       INT,
                reason           TEXT,
                submitted_by     VARCHAR(50),
                status           VARCHAR(20) DEFAULT 'Pending',
                created_at       TIMESTAMP DEFAULT NOW()
            )
        """)
        for _col in ["decided_by VARCHAR(100)", "decided_at TIMESTAMP", "remarks TEXT", "notes TEXT"]:
            _cur.execute(f"ALTER TABLE public.class_meeting_request ADD COLUMN IF NOT EXISTS {_col}")

        _cur.execute("""
            CREATE TABLE IF NOT EXISTS public.schedule_change_request (
                requestid        SERIAL PRIMARY KEY,
                scheduleid       INT,
                versionid        INT,
                change_type      VARCHAR(50),
                new_daydesc      VARCHAR(20),
                new_starttimeid  INT,
                new_endtimeid    INT,
                new_roomid       INT,
                effective_from   DATE,
                end_date         DATE,
                reason           TEXT,
                submitted_by     VARCHAR(50),
                status           VARCHAR(20) DEFAULT 'Pending',
                created_at       TIMESTAMP DEFAULT NOW()
            )
        """)
        for _col in ["decided_by VARCHAR(100)", "decided_at TIMESTAMP", "remarks TEXT", "end_date DATE"]:
            _cur.execute(f"ALTER TABLE public.schedule_change_request ADD COLUMN IF NOT EXISTS {_col}")

        _conn.commit()
        _request_tables_ensured = True
    except Exception:
        _conn.rollback()
        raise
    finally:
        _cur.close()
        _conn.close()

# ─────────────────────────────────────────────────────────────
#  RANDOM FOREST DSS  (Manual Scheduling — see rf_dss.py)
# ─────────────────────────────────────────────────────────────
from rf_dss import SKLEARN_OK as _SKLEARN_OK, train_rf_dss as _train_rf_dss
from rf_dss import rf_get_faculty_info as _rf_get_faculty_info, rf_score_room as _rf_score_room


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


def _auto_archive_semester(cur, old_sem_id):
    """
    On semester transition: copy all Published schedule records from the old semester
    into historical_data, then mark their schedule_version rows as 'Archive'.
    Called automatically when inject_active_period() detects a semester change.
    Uses the caller's cursor so all writes share the same DB connection.
    """
    try:
        # Skip if historical_data already contains records for this semester
        cur.execute("SELECT COUNT(*) AS cnt FROM historical_data WHERE semesterid = %s", (old_sem_id,))
        row = cur.fetchone()
        if row and int(row['cnt'] or 0) > 0:
            # Already archived — ensure version status is consistent
            cur.execute("""
                UPDATE schedule_version sv
                SET    status = 'Archive'
                FROM   schedule sc
                WHERE  sv.scheduleid = sc.scheduleid
                  AND  sc.semesterid = %s
                  AND  sv.status     = 'Published'
            """, (old_sem_id,))
            return

        # Aggregate Published sessions per subject-section into one historical row
        cur.execute("""
            SELECT
                COALESCE(f.lastname || ', ' || f.firstname, 'TBA')      AS instructor,
                cs.subjectcode,
                cs.subjectname,
                UPPER(c.programcode)                                     AS program,
                cs.yearlevel,
                STRING_AGG(DISTINCT ss.daydesc, '/'
                           ORDER BY ss.daydesc)                         AS days,
                COALESCE(
                    MIN(ts_s.timevalue::text) || ' - ' || MAX(ts_e.timevalue::text),
                    'TBA'
                )                                                        AS time_str,
                COALESCE(MAX(r.roomname), 'TBA')                         AS room,
                sc.semesterid,
                sem.academicyearid,
                COALESCE(cs.lecturehours, 0)                             AS lec,
                COALESCE(cs.laboratoryhours, 0)                          AS lab,
                COALESCE(cs.creditunits, 0)                              AS unit,
                COALESCE(cs.tuitionhours,
                         cs.lecturehours + cs.laboratoryhours, 0)        AS hrs,
                sc.employeenumber
            FROM   schedule_version sv
            JOIN   schedule sc          ON sv.scheduleid          = sc.scheduleid
            JOIN   curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c         ON cs.curriculumid        = c.curriculumid
            JOIN   semester sem         ON sc.semesterid          = sem.semesterid
            LEFT JOIN faculty f         ON sc.employeenumber      = f.employeenumber
            JOIN   schedule_sessions ss ON ss.versionid           = sv.versionid
            LEFT JOIN room r            ON ss.roomid              = r.roomid
            LEFT JOIN timeslot ts_s     ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN timeslot ts_e     ON ss.endtimeid           = ts_e.timeid
            WHERE  sc.semesterid = %s
              AND  sv.status     = 'Published'
            GROUP BY
                f.lastname, f.firstname, cs.subjectcode, cs.subjectname,
                c.programcode, cs.yearlevel, sc.semesterid, sem.academicyearid,
                cs.lecturehours, cs.laboratoryhours, cs.creditunits, cs.tuitionhours,
                sc.employeenumber
        """, (old_sem_id,))
        rows = cur.fetchall()

        archived = 0
        for r in rows:
            _insert_historical(
                cur,
                (r['instructor'] or 'TBA')[:150],
                (r['subjectcode'] or '')[:15],
                (r['subjectname'] or '')[:150],
                (r['program'] or '')[:20],
                r['yearlevel'],
                (r['days'] or '')[:50],
                (r['time_str'] or '')[:100],
                (r['room'] or 'TBA')[:100],
                r['semesterid'],
                r['academicyearid'],
                int(r['lec'] or 0),
                int(r['lab'] or 0),
                int(r['unit'] or 0),
                int(r['hrs'] or 0),
                r['employeenumber'],
            )
            archived += 1

        # Mark Published versions as Archive (they are now in historical_data)
        cur.execute("""
            UPDATE schedule_version sv
            SET    status = 'Archive'
            FROM   schedule sc
            WHERE  sv.scheduleid = sc.scheduleid
              AND  sc.semesterid = %s
              AND  sv.status     = 'Published'
        """, (old_sem_id,))

        if archived > 0:
            print(f"[AUTO-ARCHIVE] Semester {old_sem_id}: moved {archived} Published records "
                  f"to historical_data and set status=Archive.")

    except Exception:
        import traceback
        traceback.print_exc()
        # Non-fatal — archive failure must not block the semester transition


# --- CONTEXT PROCESSOR FOR DYNAMIC ACADEMIC YEAR ---
@app.context_processor
def inject_active_period():
    """
    Auto-detect and display the active Academic Year and Semester based on today's date.
    - If today falls within a configured semester's date range, that semester is active.
    - On transition (detected semester differs from currently-active), auto-archives
      the old semester's Published schedules into historical_data, then updates flags.
    - Between semesters, falls back to whichever semester was last set as active.
    - 'Academic Year Not Detected' appears only when no semester records exist at all.
    """
    try:
        today = date.today()
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        _SEM_LABEL = {'A': '1ST SEMESTER', 'B': '2ND SEMESTER', 'C': 'SUMMER'}

        # 1. Find the semester whose date range contains today
        cur.execute("""
            SELECT s.semesterid, s.semestertype, ay.academicyearid, ay.yearstart, ay.yearend
            FROM   semester s
            JOIN   academicyear ay ON s.academicyearid = ay.academicyearid
            WHERE  %s BETWEEN s.semstartdate AND s.semenddate
            LIMIT  1
        """, (today,))
        date_matched = cur.fetchone()

        # 2. Find what is currently flagged as active
        cur.execute("""
            SELECT s.semesterid, s.semestertype, ay.academicyearid, ay.yearstart, ay.yearend
            FROM   semester s
            JOIN   academicyear ay ON s.academicyearid = ay.academicyearid
            WHERE  s.isactive = TRUE
            LIMIT  1
        """)
        currently_active = cur.fetchone()

        if date_matched:
            target = date_matched
            # Only write to DB when the active semester has changed (semester transition)
            if not currently_active or currently_active['semesterid'] != target['semesterid']:
                if currently_active:
                    # Archive Published schedules from the outgoing semester
                    _auto_archive_semester(cur, currently_active['semesterid'])
                cur.execute("UPDATE academicyear SET isactive = FALSE")
                cur.execute("UPDATE semester   SET isactive = FALSE")
                cur.execute("UPDATE academicyear SET isactive = TRUE WHERE academicyearid = %s",
                            (target['academicyearid'],))
                cur.execute("UPDATE semester   SET isactive = TRUE WHERE semesterid = %s",
                            (target['semesterid'],))
                conn.commit()
        elif currently_active:
            # Between semesters: keep showing the last known active period
            target = currently_active
        else:
            cur.close(); conn.close()
            return {
                'current_ay_label': 'Academic Year Not Detected',
                'current_sem_label': 'Not Configured',
                'current_ay_id':    None,
            }

        cur.close(); conn.close()
        s_type = target['semestertype']
        return {
            'current_ay_label': f"A.Y {target['yearstart']} - {target['yearend']}",
            'current_sem_label': _SEM_LABEL.get(s_type, s_type),
            'current_ay_id':    target['academicyearid'],
        }
    except Exception:
        return {
            'current_ay_label': 'Academic Year Not Detected',
            'current_sem_label': 'Error',
            'current_ay_id':    None,
        }

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

        user = query_db("SELECT * FROM Accounts WHERE Username = %s", (username,), one=True)

        if user:
            user_data   = {k.lower(): v for k, v in user.items()}
            db_password = str(user_data.get('passwordhash')).strip()
            db_role     = user_data.get('role')

            if check_password_hash(db_password, password):
                session.clear()
                session['loggedin'] = True
                session['username'] = user_data.get('username')
                session['role']     = db_role
                try:
                    _conn2 = get_db_connection(); _cur2 = _conn2.cursor()
                    _cur2.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS last_login TIMESTAMP")
                    _cur2.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS profile_photo VARCHAR(255)")
                    _cur2.execute("UPDATE accounts SET last_login = NOW() WHERE username = %s", (user_data.get('username'),))
                    _conn2.commit(); _cur2.close(); _conn2.close()
                except Exception: pass

                if db_role == 'Admin':
                    return redirect(url_for('admin_dashboard'))
                elif db_role == 'Academic Head':
                    return redirect(url_for('academic_my_dashboard'))
                elif db_role == 'Faculty':
                    return redirect(url_for('faculty_dashboard'))
                else:
                    flash("Your account does not have an assigned dashboard.")
                    return redirect(url_for('login'))
            else:
                flash("Wrong password. Try again.")
                session['saved_username'] = username
        else:
            flash("Username not found!")
            session['saved_username'] = username

        return redirect(url_for('login'))

    saved_username = session.get('saved_username', '')
    flashes = session.get('_flashes')
    session.clear()
    if flashes:
        session['_flashes'] = flashes
    return render_template('login.html', saved_username=saved_username)

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
        SELECT cs.subjectcode, cs.subjectname, r.roomname, ss.daydesc,
               TO_CHAR(ts_s.timevalue, 'HH12:MI AM') as start_time,
               TO_CHAR(ts_e.timevalue, 'HH12:MI AM') as end_time,
               pyl.programcode AS offeringcode, pyl.yearlevel
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN semester sem ON sc.semesterid = sem.semesterid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        LEFT JOIN room r ON ss.roomid = r.roomid
        LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
        LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
        LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
        LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
        WHERE sc.employeenumber = %s AND sv.status = 'Published'
          AND sem.isactive = TRUE
          AND ss.daydesc = %s
        ORDER BY ts_s.timevalue
    """, (emp_num, today_day))
    my_schedule = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(SUM(cs.creditunits), 0) as total_units,
               COUNT(DISTINCT cs.subjectcode) as total_subjects
        FROM schedule_version sv
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN semester sem ON sc.semesterid = sem.semesterid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        WHERE sc.employeenumber = %s AND sv.status = 'Published'
          AND sem.isactive = TRUE
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
    _ensure_request_tables()
    return render_template('academic/requests_hub.html')

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

        # --- 2. Recent Requests (both makeup + adjustment, with status/reason/remarks) ---
        recent_requests = []
        try:
            cur.execute("""
                SELECT * FROM (
                    SELECT
                        'MAKE-UP CLASS'                           AS req_type,
                        cmr.requestid,
                        cmr.status,
                        TO_CHAR(cmr.created_at, 'Mon DD, YYYY')  AS date_sub,
                        cmr.created_at                            AS sort_ts,
                        UPPER(f.firstname || ' ' || f.lastname)  AS faculty_name,
                        cs.subjectcode,
                        COALESCE(cs.subjectname, '')              AS subjectname,
                        COALESCE(r.roomname, '')                  AS roomname,
                        TO_CHAR(cmr.requested_date, 'MM/DD/YYYY') AS requested_date,
                        TO_CHAR(ts_s.timevalue, 'HH12:MI AM')    AS start_time,
                        TO_CHAR(ts_e.timevalue, 'HH12:MI AM')    AS end_time,
                        COALESCE(cmr.reason, '')                  AS reason,
                        COALESCE(cmr.remarks, '')                 AS remarks,
                        COALESCE(pyl.programcode, '')             AS programcode,
                        COALESCE(CAST(pyl.yearlevel AS TEXT), '') AS yearlevel,
                        COALESCE(sec.sectionname, '')             AS sectionname
                    FROM class_meeting_request cmr
                    JOIN faculty f   ON cmr.submitted_by = f.employeenumber
                    JOIN schedule sc ON cmr.scheduleid   = sc.scheduleid
                    JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                    LEFT JOIN room r      ON cmr.new_roomid      = r.roomid
                    LEFT JOIN timeslot ts_s ON cmr.new_starttimeid = ts_s.timeid
                    LEFT JOIN timeslot ts_e ON cmr.new_endtimeid   = ts_e.timeid
                    LEFT JOIN sections sec  ON sc.sectionid = sec.sectionid
                    LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                    UNION ALL
                    SELECT
                        'SCHEDULE ADJUSTMENT'                     AS req_type,
                        scr.requestid,
                        scr.status,
                        TO_CHAR(scr.created_at, 'Mon DD, YYYY')  AS date_sub,
                        scr.created_at                            AS sort_ts,
                        UPPER(f.firstname || ' ' || f.lastname)  AS faculty_name,
                        cs.subjectcode,
                        COALESCE(cs.subjectname, '')              AS subjectname,
                        COALESCE(r.roomname, '')                  AS roomname,
                        TO_CHAR(scr.effective_from, 'MM/DD/YYYY') AS requested_date,
                        TO_CHAR(ts_s.timevalue, 'HH12:MI AM')    AS start_time,
                        TO_CHAR(ts_e.timevalue, 'HH12:MI AM')    AS end_time,
                        COALESCE(scr.reason, '')                  AS reason,
                        COALESCE(scr.remarks, '')                 AS remarks,
                        COALESCE(pyl.programcode, '')             AS programcode,
                        COALESCE(CAST(pyl.yearlevel AS TEXT), '') AS yearlevel,
                        COALESCE(sec.sectionname, '')             AS sectionname
                    FROM schedule_change_request scr
                    JOIN faculty f   ON scr.submitted_by = f.employeenumber
                    JOIN schedule sc ON scr.scheduleid   = sc.scheduleid
                    JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                    LEFT JOIN room r      ON scr.new_roomid      = r.roomid
                    LEFT JOIN timeslot ts_s ON scr.new_starttimeid = ts_s.timeid
                    LEFT JOIN timeslot ts_e ON scr.new_endtimeid   = ts_e.timeid
                    LEFT JOIN sections sec  ON sc.sectionid = sec.sectionid
                    LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                ) combined
                ORDER BY sort_ts DESC
                LIMIT 5
            """)
            recent_requests = cur.fetchall()
        except Exception as _re:
            print(f"Dashboard recent_requests error: {_re}")
            conn.rollback()

        # --- 3. Recent Schedule List ---
        cur.execute("""
            SELECT
                pyl.programcode,
                pyl.yearlevel,
                ay.yearstart || '-' || ay.yearend AS acad_year,
                sem.semestertype,
                TO_CHAR(MAX(sv.datecreated), 'MM/DD/YYYY') AS date_imported
            FROM schedule_version sv
            JOIN schedule s ON sv.scheduleid = s.scheduleid
            JOIN sections sec ON s.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN semester sem ON s.semesterid = sem.semesterid
            JOIN academicyear ay ON sem.academicyearid = ay.academicyearid
            GROUP BY pyl.programcode, pyl.yearlevel, ay.yearstart, ay.yearend, sem.semestertype
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
    _ensure_request_tables()
    rows = query_db("""
        SELECT 'Make-up' as type, f.lastname as faculty, cs.subjectcode, status
        FROM class_meeting_request cmr
        JOIN faculty f ON cmr.submitted_by = f.employeenumber
        JOIN schedule s ON cmr.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        WHERE cmr.status = 'Pending'
    """)
    return jsonify([dict(r) for r in (rows or [])])

# ── REQUESTS HUB: List, Validate, Decide ──────────────────────────────────────

@app.route('/api/requests/list')
def api_requests_list():
    _ensure_request_tables()
    if 'loggedin' not in session or session.get('role') != 'Academic Head':
        return jsonify({'success': False}), 401

    status_filter = request.args.get('status', 'all').strip().lower()
    category      = request.args.get('category', 'all').strip().lower()
    faculty_kw    = request.args.get('faculty', '').strip().lower()

    try:
        makeup_sql = """
            SELECT
                cmr.requestid,
                'makeup'        AS req_type,
                'MAKE-UP CLASS' AS req_type_label,
                cmr.status,
                TO_CHAR(cmr.created_at, 'Mon DD, YYYY') AS date_submitted,
                cmr.created_at::text                    AS created_at_raw,
                TO_CHAR(cmr.requested_date, 'Mon DD, YYYY') AS requested_date_fmt,
                COALESCE(cmr.requested_date::text, '')  AS requested_date,
                COALESCE(cmr.reason, '')                AS reason,
                COALESCE(cmr.notes,  '')                AS notes,
                UPPER(f.firstname || ' ' || f.lastname) AS faculty_name,
                f.employeenumber,
                COALESCE(d.designationname, et.typename, '') AS designation,
                cs.subjectcode,
                COALESCE(cs.subjectname, '')            AS subjectname,
                COALESCE(r.roomname, '')                AS room_name,
                COALESCE(b.buildingname || ' – ' || r.roomname, r.roomname, '') AS room_full,
                COALESCE(pyl.programcode, '')           AS program_code,
                COALESCE(pyl.yearlevel::text, '')       AS year_level,
                COALESCE(sec.sectionname, '')           AS section_name,
                COALESCE(TO_CHAR(ts_s.timevalue, 'HH12:MI AM'), '') AS start_time,
                COALESCE(TO_CHAR(ts_e.timevalue, 'HH12:MI AM'), '') AS end_time
            FROM class_meeting_request cmr
            JOIN schedule sc ON cmr.scheduleid = sc.scheduleid
            JOIN faculty f ON sc.employeenumber = f.employeenumber
            LEFT JOIN designation d ON f.designationid = d.designationid
            LEFT JOIN employeetype et ON f.employeetypeid = et.employeetypeid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            LEFT JOIN room r ON cmr.new_roomid = r.roomid
            LEFT JOIN building b ON r.buildingid = b.buildingid
            LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN timeslot ts_s ON cmr.new_starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON cmr.new_endtimeid   = ts_e.timeid
            ORDER BY cmr.created_at DESC
        """
        adj_sql = """
            SELECT
                scr.requestid,
                'adjustment'           AS req_type,
                'SCHEDULE ADJUSTMENT'  AS req_type_label,
                scr.status,
                TO_CHAR(scr.created_at, 'Mon DD, YYYY') AS date_submitted,
                scr.created_at::text                    AS created_at_raw,
                TO_CHAR(scr.effective_from, 'Mon DD, YYYY') AS requested_date_fmt,
                COALESCE(scr.effective_from::text, '')  AS requested_date,
                COALESCE(scr.reason, '')                AS reason,
                UPPER(f.firstname || ' ' || f.lastname) AS faculty_name,
                f.employeenumber,
                COALESCE(d.designationname, et.typename, '') AS designation,
                cs.subjectcode,
                COALESCE(cs.subjectname, '')            AS subjectname,
                COALESCE(r.roomname, '')                AS room_name,
                COALESCE(b.buildingname || ' – ' || r.roomname, r.roomname, '') AS room_full,
                COALESCE(pyl.programcode, '')           AS program_code,
                COALESCE(pyl.yearlevel::text, '')       AS year_level,
                COALESCE(sec.sectionname, '')           AS section_name,
                COALESCE(TO_CHAR(ts_s.timevalue, 'HH12:MI AM'), '') AS start_time,
                COALESCE(TO_CHAR(ts_e.timevalue, 'HH12:MI AM'), '') AS end_time,
                COALESCE(scr.new_daydesc, '')           AS new_daydesc,
                COALESCE(scr.change_type, '')           AS change_type
            FROM schedule_change_request scr
            JOIN schedule sc ON scr.scheduleid = sc.scheduleid
            JOIN faculty f ON sc.employeenumber = f.employeenumber
            LEFT JOIN designation d ON f.designationid = d.designationid
            LEFT JOIN employeetype et ON f.employeetypeid = et.employeetypeid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            LEFT JOIN room r ON scr.new_roomid = r.roomid
            LEFT JOIN building b ON r.buildingid = b.buildingid
            LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN timeslot ts_s ON scr.new_starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON scr.new_endtimeid   = ts_e.timeid
            ORDER BY scr.created_at DESC
        """
        makeup_rows = [dict(r) for r in (query_db(makeup_sql) or [])]
        adj_rows    = [dict(r) for r in (query_db(adj_sql)    or [])]
        all_rows    = makeup_rows + adj_rows
        all_rows.sort(key=lambda r: r.get('created_at_raw') or '', reverse=True)

        stats = {
            'total':    len(all_rows),
            'pending':  sum(1 for r in all_rows if r['status'] == 'Pending'),
            'approved': sum(1 for r in all_rows if r['status'] == 'Approved'),
            'rejected': sum(1 for r in all_rows if r['status'] in ('Rejected', 'Cancelled')),
        }

        for i, r in enumerate(all_rows, 1):
            r['request_number'] = i
            r.pop('created_at_raw', None)

        filtered = all_rows
        if status_filter != 'all':
            if status_filter == 'rejected':
                filtered = [r for r in filtered if r['status'] in ('Rejected', 'Cancelled')]
            else:
                filtered = [r for r in filtered if r['status'].lower() == status_filter]
        if category != 'all':
            filtered = [r for r in filtered if r['req_type'] == category]
        if faculty_kw:
            filtered = [r for r in filtered if faculty_kw in r['faculty_name'].lower()]

        return jsonify({'success': True, 'requests': filtered, 'stats': stats})
    except Exception as e:
        print(f"[api_requests_list] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/requests/validate')
def api_requests_validate():
    _ensure_request_tables()
    if 'loggedin' not in session or session.get('role') != 'Academic Head':
        return jsonify({'success': False}), 401

    req_type = request.args.get('type', '').strip()
    req_id   = request.args.get('id',   '').strip()

    try:
        if req_type == 'makeup':
            row = query_db("""
                SELECT cmr.*, sc.employeenumber, sc.scheduleid AS orig_sched,
                       ts_s.timevalue AS start_t, ts_e.timevalue AS end_t
                FROM class_meeting_request cmr
                JOIN schedule sc ON cmr.scheduleid = sc.scheduleid
                JOIN timeslot ts_s ON cmr.new_starttimeid = ts_s.timeid
                JOIN timeslot ts_e ON cmr.new_endtimeid   = ts_e.timeid
                WHERE cmr.requestid = %s
            """, [req_id], one=True)
            if not row:
                return jsonify({'success': False, 'error': 'Not found'}), 404

            from datetime import date as _date
            req_date  = row['requested_date']
            day_name  = req_date.strftime('%A') if req_date else None
            start_t   = row['start_t']
            end_t     = row['end_t']
            room_id   = row['new_roomid']
            emp_num   = row['employeenumber']
            sched_id  = row['scheduleid']

            # 1. Room: published schedule conflict
            rc1 = query_db("""
                SELECT 1 FROM schedule_sessions ss
                JOIN schedule_version sv ON ss.versionid = sv.versionid
                JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
                WHERE sv.status = 'Published' AND ss.roomid = %s
                  AND UPPER(ss.daydesc) = UPPER(%s)
                  AND ts_s.timevalue < %s AND ts_e.timevalue > %s
                LIMIT 1
            """, [room_id, day_name, end_t, start_t]) or []

            # 2. Room: other approved makeup on same date
            rc2 = query_db("""
                SELECT 1 FROM class_meeting_request c2
                JOIN timeslot ts_s ON c2.new_starttimeid = ts_s.timeid
                JOIN timeslot ts_e ON c2.new_endtimeid   = ts_e.timeid
                WHERE c2.status = 'Approved' AND c2.new_roomid = %s
                  AND c2.requested_date = %s
                  AND ts_s.timevalue < %s AND ts_e.timevalue > %s
                  AND c2.requestid != %s
                LIMIT 1
            """, [room_id, req_date, end_t, start_t, req_id]) or []

            room_available = not rc1 and not rc2

            # 3. Faculty: published schedule (all schedules — makeup is an extra session, not a replacement)
            fc1 = query_db("""
                SELECT 1 FROM schedule_sessions ss
                JOIN schedule_version sv ON ss.versionid = sv.versionid
                JOIN schedule sc2 ON sv.scheduleid = sc2.scheduleid
                JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
                WHERE sv.status = 'Published' AND sc2.employeenumber = %s
                  AND UPPER(ss.daydesc) = UPPER(%s)
                  AND ts_s.timevalue < %s AND ts_e.timevalue > %s
                LIMIT 1
            """, [emp_num, day_name, end_t, start_t]) or []

            # 4. Faculty: other approved makeup on same date
            fc2 = query_db("""
                SELECT 1 FROM class_meeting_request c2
                JOIN schedule sc2 ON c2.scheduleid = sc2.scheduleid
                JOIN timeslot ts_s ON c2.new_starttimeid = ts_s.timeid
                JOIN timeslot ts_e ON c2.new_endtimeid   = ts_e.timeid
                WHERE c2.status = 'Approved' AND sc2.employeenumber = %s
                  AND c2.requested_date = %s
                  AND ts_s.timevalue < %s AND ts_e.timevalue > %s
                  AND c2.requestid != %s
                LIMIT 1
            """, [emp_num, req_date, end_t, start_t, req_id]) or []

            faculty_free = not fc1 and not fc2

            # 5. Program conflict
            pyl = query_db("""
                SELECT sec.programyearlevelid FROM schedule sc2
                JOIN sections sec ON sc2.sectionid = sec.sectionid
                WHERE sc2.scheduleid = %s LIMIT 1
            """, [sched_id], one=True)
            if pyl:
                pc1 = query_db("""
                    SELECT 1 FROM schedule_sessions ss
                    JOIN schedule_version sv ON ss.versionid = sv.versionid
                    JOIN schedule sc2 ON sv.scheduleid = sc2.scheduleid
                    JOIN sections sec ON sc2.sectionid = sec.sectionid
                    JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                    JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
                    WHERE sv.status = 'Published'
                      AND sec.programyearlevelid = %s
                      AND UPPER(ss.daydesc) = UPPER(%s)
                      AND ts_s.timevalue < %s AND ts_e.timevalue > %s
                      AND sc2.scheduleid != %s
                    LIMIT 1
                """, [pyl['programyearlevelid'], day_name, end_t, start_t, sched_id]) or []
                program_ok = not pc1
            else:
                program_ok = True

        elif req_type == 'adjustment':
            row = query_db("""
                SELECT scr.*, sc.employeenumber, sc.scheduleid AS orig_sched,
                       ts_s.timevalue AS start_t, ts_e.timevalue AS end_t
                FROM schedule_change_request scr
                JOIN schedule sc ON scr.scheduleid = sc.scheduleid
                LEFT JOIN timeslot ts_s ON scr.new_starttimeid = ts_s.timeid
                LEFT JOIN timeslot ts_e ON scr.new_endtimeid   = ts_e.timeid
                WHERE scr.requestid = %s
            """, [req_id], one=True)
            if not row:
                return jsonify({'success': False, 'error': 'Not found'}), 404

            day      = row['new_daydesc']
            start_t  = row['start_t']
            end_t    = row['end_t']
            room_id  = row['new_roomid']
            emp_num  = row['employeenumber']
            sched_id = row['scheduleid']

            if room_id and day and start_t and end_t:
                rc1 = query_db("""
                    SELECT 1 FROM schedule_sessions ss
                    JOIN schedule_version sv ON ss.versionid = sv.versionid
                    JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                    JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
                    WHERE sv.status = 'Published' AND ss.roomid = %s
                      AND UPPER(ss.daydesc) = UPPER(%s)
                      AND ts_s.timevalue < %s AND ts_e.timevalue > %s
                      AND sv.scheduleid != %s
                    LIMIT 1
                """, [room_id, day, end_t, start_t, sched_id]) or []
                room_available = not rc1
            else:
                room_available = True

            if emp_num and day and start_t and end_t:
                fc1 = query_db("""
                    SELECT 1 FROM schedule_sessions ss
                    JOIN schedule_version sv ON ss.versionid = sv.versionid
                    JOIN schedule sc2 ON sv.scheduleid = sc2.scheduleid
                    JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                    JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
                    WHERE sv.status = 'Published' AND sc2.employeenumber = %s
                      AND UPPER(ss.daydesc) = UPPER(%s)
                      AND ts_s.timevalue < %s AND ts_e.timevalue > %s
                      AND sc2.scheduleid != %s
                    LIMIT 1
                """, [emp_num, day, end_t, start_t, sched_id]) or []
                faculty_free = not fc1
            else:
                faculty_free = True

            pyl = query_db("""
                SELECT sec.programyearlevelid FROM schedule sc2
                JOIN sections sec ON sc2.sectionid = sec.sectionid
                WHERE sc2.scheduleid = %s LIMIT 1
            """, [sched_id], one=True)
            if pyl and day and start_t and end_t:
                pc1 = query_db("""
                    SELECT 1 FROM schedule_sessions ss
                    JOIN schedule_version sv ON ss.versionid = sv.versionid
                    JOIN schedule sc2 ON sv.scheduleid = sc2.scheduleid
                    JOIN sections sec ON sc2.sectionid = sec.sectionid
                    JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                    JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
                    WHERE sv.status = 'Published'
                      AND sec.programyearlevelid = %s
                      AND UPPER(ss.daydesc) = UPPER(%s)
                      AND ts_s.timevalue < %s AND ts_e.timevalue > %s
                      AND sc2.scheduleid != %s
                    LIMIT 1
                """, [pyl['programyearlevelid'], day, end_t, start_t, sched_id]) or []
                program_ok = not pc1
            else:
                program_ok = True
        else:
            return jsonify({'success': False, 'error': 'Invalid type'}), 400

        conflicts = []
        if not room_available: conflicts.append('Room is already booked at the requested time.')
        if not faculty_free:   conflicts.append('Faculty has another class at the requested time.')
        if not program_ok:     conflicts.append('Program/Year Level has a class conflict at the requested time.')

        return jsonify({
            'success':        True,
            'room_available': room_available,
            'faculty_free':   faculty_free,
            'program_ok':     program_ok,
            'conflicts':      conflicts,
            'all_ok':         room_available and faculty_free and program_ok,
        })
    except Exception as e:
        print(f"[api_requests_validate] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/requests/decide', methods=['POST'])
def api_requests_decide():
    _ensure_request_tables()
    if 'loggedin' not in session or session.get('role') != 'Academic Head':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    data     = request.json or {}
    req_type = data.get('type', '').strip()
    req_id   = data.get('id')
    decision = data.get('decision', '').strip()
    remarks  = data.get('remarks', '').strip()

    if decision not in ('Approved', 'Rejected'):
        return jsonify({'success': False, 'error': 'Invalid decision'}), 400

    decided_by = session.get('employeenumber') or session.get('username', 'unknown')

    _conn = get_db_connection()
    _cur  = _conn.cursor(cursor_factory=RealDictCursor)
    try:
        if req_type == 'makeup':
            _cur.execute("""
                UPDATE class_meeting_request
                SET status = %s, decided_by = %s, decided_at = NOW(), remarks = %s
                WHERE requestid = %s
            """, [decision, decided_by, remarks, req_id])

            if decision == 'Approved':
                _cur.execute("SELECT * FROM class_meeting_request WHERE requestid = %s", [req_id])
                mk = _cur.fetchone()
                if mk and mk.get('scheduleid') and mk.get('new_starttimeid') and mk.get('new_endtimeid') and mk.get('requested_date'):
                    _cur.execute("""
                        SELECT versionid FROM schedule_version
                        WHERE scheduleid = %s AND status = 'Published'
                        ORDER BY version_number DESC LIMIT 1
                    """, [mk['scheduleid']])
                    ver = _cur.fetchone()
                    if ver:
                        # Room Schedule has no concept of a one-time calendar date — it's a
                        # purely weekly-recurring grid keyed by day-of-week. Derive the
                        # day-of-week from the requested date so the approved make-up class
                        # is visible there (it will show as a normal weekly slot on that day).
                        day_of_week = mk['requested_date'].strftime('%A')
                        _cur.execute("""
                            SELECT 1 FROM schedule_sessions
                            WHERE versionid = %s AND daydesc = %s
                              AND starttimeid = %s AND endtimeid = %s
                              AND roomid IS NOT DISTINCT FROM %s
                        """, [ver['versionid'], day_of_week, mk['new_starttimeid'], mk['new_endtimeid'], mk.get('new_roomid')])
                        if not _cur.fetchone():
                            _cur.execute("""
                                INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid)
                                VALUES (%s, %s, %s, %s, %s)
                            """, [ver['versionid'], day_of_week, mk['new_starttimeid'], mk['new_endtimeid'], mk.get('new_roomid')])

        elif req_type == 'adjustment':
            _cur.execute("""
                UPDATE schedule_change_request
                SET status = %s, decided_by = %s, decided_at = NOW(), remarks = %s
                WHERE requestid = %s
            """, [decision, decided_by, remarks, req_id])

            if decision == 'Approved':
                _cur.execute("""
                    SELECT * FROM schedule_change_request WHERE requestid = %s
                """, [req_id])
                adj = _cur.fetchone()
                if adj:
                    upd_parts = []
                    upd_vals  = []
                    if adj.get('new_daydesc'):
                        upd_parts.append('daydesc = %s')
                        upd_vals.append(adj['new_daydesc'])
                    if adj.get('new_starttimeid'):
                        upd_parts.append('starttimeid = %s')
                        upd_vals.append(adj['new_starttimeid'])
                    if adj.get('new_endtimeid'):
                        upd_parts.append('endtimeid = %s')
                        upd_vals.append(adj['new_endtimeid'])
                    if adj.get('new_roomid'):
                        upd_parts.append('roomid = %s')
                        upd_vals.append(adj['new_roomid'])
                    if upd_parts and adj.get('versionid'):
                        _cur.execute(
                            "SELECT COUNT(*) AS cnt FROM schedule_sessions WHERE versionid = %s",
                            [adj['versionid']]
                        )
                        _sess_count = _cur.fetchone()['cnt']
                        if _sess_count == 1:
                            upd_vals.append(adj['versionid'])
                            _cur.execute(
                                f"UPDATE schedule_sessions SET {', '.join(upd_parts)} WHERE versionid = %s",
                                upd_vals
                            )
                        else:
                            # This subject meets more than once a week under this versionid —
                            # applying blindly would overwrite every meeting to the same new
                            # day/time/room. Skip the auto-apply rather than corrupt the other
                            # sessions; the Academic Head must adjust the specific meeting
                            # manually via the Manual Editor.
                            write_activity_log(
                                action="Adjustment needs manual follow-up",
                                details=(
                                    f"Schedule Adjustment request #{req_id} was approved but "
                                    f"versionid {adj['versionid']} has {_sess_count} sessions — "
                                    f"auto-apply was skipped to avoid overwriting all of them. "
                                    f"Please adjust the specific session manually in the Manual Editor."
                                ),
                                category='approval', color='orange'
                            )
        else:
            return jsonify({'success': False, 'error': 'Invalid type'}), 400

        _conn.commit()
        write_activity_log(
            action=f"Request {decision}",
            details=f"{req_type.upper()} request #{req_id} {decision.lower()} by {decided_by}",
            category='approval',
            color='green' if decision == 'Approved' else 'orange'
        )
        return jsonify({'success': True})
    except Exception as e:
        _conn.rollback()
        print(f"[api_requests_decide] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        _cur.close()
        _conn.close()


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
                               reg=0, pt=0, des=0, specializations=[], employee_types=[], designations=[],
                               active_ay_id='', active_sem='')
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

        active = query_db("""
            SELECT ay.academicyearid, s.semestertype
            FROM semester s
            JOIN academicyear ay ON s.academicyearid = ay.academicyearid
            WHERE s.isactive = TRUE LIMIT 1
        """, one=True)
        active_ay_id = active['academicyearid'] if active else ''
        active_sem   = active['semestertype']    if active else ''

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
            total=len(employees),
            active_ay_id=active_ay_id,
            active_sem=active_sem
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        try: conn.close()
        except: pass
        return render_template('academic/employee.html', employees=[], total=0,
                               reg=0, pt=0, des=0, specializations=[], employee_types=[], designations=[],
                               active_ay_id='', active_sem='')

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
        def gv(field, _r=raw, _m=col_map):
            idx = _m.get(field)
            return _clean_cell(_r[idx]) if idx is not None and idx < len(_r) else ''
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

def _clean_cell(val):
    """Convert a cell value to string. Integer floats (e.g. 12345.0) are returned without .0."""
    if val is None:
        return ''
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val).strip()

def _rows_to_employee_list(data_rows, col_map):
    fields = ('emp_num','last_name','first_name','middle_name',
              'email','contact','specialization','emp_type','status','designation')
    employees = []
    for row in data_rows:
        row = list(row)
        if not any(_clean_cell(c) for c in row):
            continue
        def gv(f, _r=row, _m=col_map):
            idx = _m.get(f)
            return _clean_cell(_r[idx]) if idx is not None and idx < len(_r) else ''
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
    raw_rows    = [[_clean_cell(c) for c in row] for row in data_rows]
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

def _auto_setup_program_yearlevels(cur, prog_filter=None):
    """
    Upserts program_yearlevel rows for every active program across ALL academic years
    (needed for historical schedule data integrity).

    Sections are only created for the currently active academic year.
    Sections belonging to non-active academic years are deactivated.

    Pass prog_filter (programcode string) to limit to a single program.
    Returns count of program_yearlevel rows upserted.
    """
    # Determine the active academic year — sections are scoped to this AY only
    cur.execute("SELECT academicyearid, yearstart FROM academicyear WHERE isactive = TRUE LIMIT 1")
    active_ay_row = cur.fetchone()
    active_ay_id  = active_ay_row['academicyearid'] if active_ay_row else None

    # All academic years — program_yearlevel rows must cover every AY for historical lookups
    cur.execute("SELECT academicyearid, yearstart FROM academicyear ORDER BY yearstart ASC")
    all_ays = cur.fetchall()
    if not all_ays:
        return 0

    # Active programs, optionally filtered to one program
    if prog_filter:
        cur.execute("""
            SELECT p.programcode, COALESCE(p.numyearlevel, 4) AS numyearlevel
            FROM programs p
            WHERE p.isactive = TRUE AND UPPER(p.programcode) = UPPER(%s)
        """, (prog_filter,))
    else:
        cur.execute("""
            SELECT p.programcode, COALESCE(p.numyearlevel, 4) AS numyearlevel
            FROM programs p
            WHERE p.isactive = TRUE
            ORDER BY p.programcode
        """)
    programs = cur.fetchall()

    upserted = 0

    for prog_row in programs:
        prog_code = prog_row['programcode']
        num_years = int(prog_row['numyearlevel'])

        for ay_row in all_ays:
            ay_id        = ay_row['academicyearid']
            ay_yearstart = int(ay_row['yearstart'])

            for yl in range(1, num_years + 1):
                # Cohort's entry year: subtract (year_level - 1) from the AY start
                start_yr = ay_yearstart - (yl - 1)
                startacademicyear = f"{start_yr}-{start_yr + 1}"

                # Best curriculum: latest whose curriculumyear start <= cohort's entry year
                cur.execute("""
                    SELECT curriculumid FROM curriculum
                    WHERE UPPER(programcode) = UPPER(%s)
                      AND CAST(SUBSTRING(curriculumyear, 1, 4) AS INT) <= %s
                    ORDER BY curriculumyear DESC LIMIT 1
                """, (prog_code, start_yr))
                best = cur.fetchone()
                best_curr_id = best['curriculumid'] if best else None

                # Upsert — COALESCE preserves a previously-set curriculumid when no cohort
                # curriculum is found (avoids overwriting a manual assignment with NULL).
                try:
                    cur.execute("SAVEPOINT pyl_auto")
                    cur.execute("""
                        INSERT INTO program_yearlevel
                            (programcode, academicyearid, startacademicyear, yearlevel, curriculumid, isactive)
                        VALUES (%s, %s, %s, %s, %s, TRUE)
                        ON CONFLICT (programcode, academicyearid, startacademicyear, yearlevel)
                            DO UPDATE SET
                                curriculumid = COALESCE(EXCLUDED.curriculumid, program_yearlevel.curriculumid),
                                isactive = TRUE
                    """, (prog_code.upper(), ay_id, startacademicyear, yl, best_curr_id))
                    cur.execute("RELEASE SAVEPOINT pyl_auto")
                    upserted += 1
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT pyl_auto")
                    continue

    # Ensure every program_yearlevel in the active AY has at least one default section
    if active_ay_id:
        _ensure_default_sections(cur, active_ay_id)

    return upserted


def _ensure_default_sections(cur, ay_id):
    """
    For every program_yearlevel row under ay_id that has NO sections at all,
    insert one default active section.  Name = section_naming_format if set,
    otherwise '{programcode}{yearlevel}' (e.g. 'BPA1'), truncated to 10 chars.
    Uses SAVEPOINT so a single conflict never aborts the outer transaction.
    Returns the number of sections created.
    """
    if not ay_id:
        return 0
    cur.execute("""
        SELECT pyl.programyearlevelid,
               pyl.programcode,
               pyl.yearlevel,
               COALESCE(pyl.section_naming_format, '') AS naming_format
        FROM   program_yearlevel pyl
        WHERE  pyl.academicyearid = %s
          AND  NOT EXISTS (
                   SELECT 1 FROM sections sec
                   WHERE  sec.programyearlevelid = pyl.programyearlevelid
               )
    """, (ay_id,))
    rows = cur.fetchall()
    created = 0
    for r in rows:
        pyl_id  = r['programyearlevelid']
        prefix  = (r['naming_format'] or (r['programcode'] + str(r['yearlevel'])))[:10]
        try:
            cur.execute("SAVEPOINT sec_default")
            cur.execute("""
                INSERT INTO sections (programyearlevelid, sectionname, isactive)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (programyearlevelid, sectionname) DO NOTHING
            """, (pyl_id, prefix))
            cur.execute("RELEASE SAVEPOINT sec_default")
            created += 1
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT sec_default")
    return created


def _reassign_curriculum_for_program(cur, programcode):
    """
    Recalculate and UPDATE curriculumid for all program_yearlevel rows of a program.
    Rule: highest curriculumyear that is <= the cohort's startacademicyear.
    COALESCE preserves the existing curriculumid when no matching curriculum is found,
    so a manually-assigned or previously-correct value is never overwritten with NULL.
    Call this after any curriculum INSERT/UPDATE for that program.
    """
    cur.execute("""
        UPDATE program_yearlevel pyl
        SET curriculumid = COALESCE(
            (
                SELECT c.curriculumid
                FROM curriculum c
                WHERE UPPER(c.programcode) = UPPER(pyl.programcode)
                  AND CAST(SUBSTRING(c.curriculumyear, 1, 4) AS INT)
                      <= CAST(SUBSTRING(pyl.startacademicyear, 1, 4) AS INT)
                ORDER BY c.curriculumyear DESC
                LIMIT 1
            ),
            pyl.curriculumid
        )
        WHERE UPPER(pyl.programcode) = UPPER(%s)
    """, (programcode,))
    return cur.rowcount


@app.route('/api/program-yearlevel/auto-setup', methods=['POST'])
def api_program_yearlevel_auto_setup():
    if 'loggedin' not in session:
        return jsonify({'error': 'Unauthorized'}), 403
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        upserted = _auto_setup_program_yearlevels(cur)
        conn.commit()
        return jsonify({'success': True, 'upserted': upserted})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


@app.route('/curriculum')
def curriculum():
    if 'loggedin' not in session: return redirect(url_for('login'))
    
    raw_programs = query_db("SELECT * FROM Programs WHERE IsActive = TRUE ORDER BY ProgramName")
    programs = [{k.lower(): v for k, v in row.items()} for row in raw_programs] if raw_programs else []
    selected_program = request.args.get('program_code', 'All')
    
    currs_raw = []
    if selected_program == 'All':
        currs_raw = query_db("""
            SELECT c.*, c.programcode, p.programname
            FROM curriculum c
            JOIN programs p ON c.programcode = p.programcode
            WHERE p.isactive = TRUE
            ORDER BY p.programname ASC, c.curriculumyear DESC
        """)
    elif selected_program:
        currs_raw = query_db("""
            SELECT c.*, c.programcode, p.programname
            FROM curriculum c
            JOIN programs p ON c.programcode = p.programcode
            WHERE c.programcode = %s
            ORDER BY c.curriculumyear DESC
        """, (selected_program,))

    curriculums = [{k.lower(): v for k, v in row.items()} for row in currs_raw] if currs_raw else []

    today = date.today()
    # Auto-setup: ensure every program×year-level has program_yearlevel rows + default sections.
    try:
        _conn = get_db_connection(); _cur = _conn.cursor(cursor_factory=RealDictCursor)
        _auto_setup_program_yearlevels(_cur)
        _conn.commit(); _cur.close(); _conn.close()
    except Exception:
        pass

    cohorts_raw = query_db("""
        SELECT pyl.programyearlevelid AS cohortid,
               pyl.programcode,
               p.programname,
               pyl.curriculumid,
               curr.curriculumcode,
               pyl.startacademicyear,
               pyl.yearlevel AS year_level,
               GREATEST(COUNT(sec.sectionid), 1) AS numberofsections
        FROM program_yearlevel pyl
        JOIN programs p ON pyl.programcode = p.programcode
        LEFT JOIN curriculum curr ON pyl.curriculumid = curr.curriculumid
        LEFT JOIN sections sec ON sec.programyearlevelid = pyl.programyearlevelid AND sec.isactive = TRUE
        WHERE pyl.isactive = TRUE
        GROUP BY pyl.programyearlevelid, pyl.programcode, p.programname,
                 pyl.curriculumid, curr.curriculumcode, pyl.startacademicyear, pyl.yearlevel
        ORDER BY pyl.startacademicyear DESC, p.programname ASC
    """)

    cohorts = [{k.lower(): v for k, v in row.items()} for row in cohorts_raw] if cohorts_raw else []

    return render_template('academic/curriculum.html', programs=programs, curriculums=curriculums, selected_program=selected_program, cohorts=cohorts)

@app.route('/curriculum/view/<int:curriculum_id>')
def view_curriculum(curriculum_id):
    if 'loggedin' not in session: return redirect(url_for('login'))
    
    y_lvl = request.args.get('year', '0') 
    sem = request.args.get('semester', 'All')

    info_sql = """
        SELECT c.*, c.programcode, p.programname,
               c.programcode AS baseprogramcode, p.numyearlevel
        FROM curriculum c
        JOIN programs p ON c.programcode = p.programcode
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
        WHERE c.programcode = %s ORDER BY c.curriculumyear DESC
    """
    other_currs = query_db(other_sql, (info['programcode'],))

    main_sql = """
        SELECT cs.subjectcode, cs.prerequisite, cs.corequisite,
               cs.subjectname, cs.lecturehours, cs.laboratoryhours, cs.creditunits,
               cs.tuitionhours, cs.semester, cs.yearlevel
        FROM curriculumsubject cs
        WHERE cs.curriculumid = %s
    """
    params = [curriculum_id]
    if y_lvl != '0':
        main_sql += " AND cs.yearlevel = %s"
        params.append(int(y_lvl))
    if sem != 'All':
        main_sql += " AND cs.semester = %s"
        params.append(sem)

    main_sql += " ORDER BY cs.yearlevel ASC, cs.semester ASC"
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
    is_modal = request.args.get('modal') == '1'
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
                               active_sem=active_sem,
                               is_modal=is_modal,
                               base_template=('shared/blank_layout.html' if is_modal else 'academic/base.html'))
    except Exception as e:
        print(f"room_view error: {e}")
        return redirect(url_for('room'))

# Insert these routes into the "ACADEMIC HEAD SPECIFIC ROUTES" section of your app.py

@app.route('/schedule')
def schedule():
    if 'loggedin' not in session: return redirect(url_for('login'))

    from datetime import date as _date
    programs   = query_db("""
        SELECT programcode, programname
        FROM   programs
        WHERE  isactive = TRUE
        ORDER  BY programname, programcode
    """)
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
            p.programname,
            pyl.programcode,
            pyl.yearlevel,
            ay.academicyearid,
            ay.yearstart || '-' || ay.yearend AS acad_year,
            sem.semestertype,
            TO_CHAR(MAX(sv.datecreated), 'MM/DD/YYYY') AS date_imported
        FROM schedule_version sv
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN sections sec ON s.sectionid = sec.sectionid
        LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
        LEFT JOIN programs p ON pyl.programcode = p.programcode
        JOIN semester sem ON s.semesterid = sem.semesterid
        JOIN academicyear ay ON sem.academicyearid = ay.academicyearid
        GROUP BY p.programname, pyl.programcode, pyl.yearlevel, ay.academicyearid, ay.yearstart, ay.yearend, sem.semestertype
        ORDER BY MAX(sv.datecreated) DESC
    """
    status_list = query_db(status_query)
    import json as _json
    status_list_json = _json.dumps([
        {
            'programname':   r['programname']   or '',
            'programcode':   r['programcode']   or '',
            'yearlevel':     int(r['yearlevel']) if r['yearlevel'] else 0,
            'academicyearid': str(r['academicyearid']) if r['academicyearid'] else '',
            'acad_year':     r['acad_year']     or '',
            'semestertype':  r['semestertype']  or '',
            'date_imported': r['date_imported'] or 'N/A',
        }
        for r in (status_list or [])
    ])

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

    faculty_list = query_db("""
        SELECT employeenumber, lastname || ', ' || firstname AS fullname
        FROM faculty WHERE employeestatus != 'Archive'
        ORDER BY lastname, firstname
    """) or []
    import json as _json2
    faculty_json = _json2.dumps([
        {'emp': str(f['employeenumber']), 'name': f['fullname']}
        for f in faculty_list
    ])

    return render_template('academic/schedule.html',
                           programs=programs,
                           acad_years=acad_years,
                           status_list=status_list,
                           status_list_json=status_list_json,
                           active_sem_type=active_sem_type,
                           active_ay=active_ay,
                           current_year=today.year,
                           today=today.isoformat(),
                           sem_json=sem_json,
                           faculty_json=faculty_json)

@app.route('/api/schedule/list/delete', methods=['POST'])
def api_schedule_list_delete():
    if session.get('role') != 'Academic Head':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data           = request.get_json() or {}
    programcode    = (data.get('programcode') or '').strip()
    yearlevel      = data.get('yearlevel')
    academicyearid = data.get('academicyearid')
    semestertype   = (data.get('semestertype') or '').strip()
    if not all([programcode, yearlevel, academicyearid, semestertype]):
        return jsonify({'success': False, 'error': 'Missing parameters'}), 400
    conn = None
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT semesterid FROM semester
            WHERE academicyearid = %s AND semestertype = %s
        """, (academicyearid, semestertype))
        sem_row = cur.fetchone()
        if not sem_row:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Semester not found'}), 404
        sem_id = sem_row['semesterid']
        cur.execute("""
            SELECT s.scheduleid FROM schedule s
            JOIN sections sec ON s.sectionid = sec.sectionid
            JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            WHERE UPPER(pyl.programcode) = UPPER(%s) AND pyl.yearlevel = %s AND s.semesterid = %s
        """, (programcode, yearlevel, sem_id))
        sched_ids = [r['scheduleid'] for r in cur.fetchall()]
        if not sched_ids:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'No schedule records found for this combination'}), 404
        cur.execute("DELETE FROM schedule_version WHERE scheduleid = ANY(%s)", (sched_ids,))
        cur.execute("DELETE FROM schedule WHERE scheduleid = ANY(%s)", (sched_ids,))
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'success': True, 'deleted': len(sched_ids)})
    except Exception as e:
        if conn:
            conn.rollback()
            try: cur.close(); conn.close()
            except: pass
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/get_offerings_schedule')
def get_offerings_schedule():
    import re
    prog          = request.args.get('program')
    yl            = request.args.get('year_level')
    sem           = request.args.get('semester')
    ay            = request.args.get('ay')
    section_id    = request.args.get('section_id')
    emp_num       = request.args.get('emp_num')
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
            # Build WHERE clauses dynamically so instructor-only queries work
            # (no program required when filtering by emp_num alone)
            q_params = ([status_param] if status_param else [])
            prog_clause = ''
            yl_clause   = ''
            if prog:
                prog_clause = 'AND UPPER(pyl.programcode) = UPPER(%s)'
                q_params = q_params + [prog]
            _yl_int = int(yl) if str(yl).lstrip('-').isdigit() and int(yl) > 0 else 0
            if _yl_int:
                yl_clause = 'AND pyl.yearlevel = %s'
                q_params = q_params + [_yl_int]
            q_params = q_params + [sem, ay]
            section_clause = ''
            if section_id:
                section_clause = 'AND sec.sectionid = %s'
                q_params = q_params + [int(section_id)]
            instructor_clause = ''
            if emp_num:
                instructor_clause = 'AND sc.employeenumber = %s'
                q_params = q_params + [emp_num]
            cur.execute(f"""
            SELECT
                cs.subjectcode,
                cs.subjectname,
                COALESCE(f.lastname || ', ' || f.firstname || COALESCE(' ' || f.middlename, ''), 'TBA') AS instructor,
                f.employeenumber                                    AS faculty_id,
                COALESCE(r.roomname, 'TBA')                         AS roomname,
                ss.daydesc,
                TO_CHAR(ts_s.timevalue, 'HH24:MI')                 AS start_time,
                TO_CHAR(ts_e.timevalue, 'HH24:MI')                 AS end_time,
                COALESCE(cs.lecturehours,    0)                    AS lecturehours,
                COALESCE(cs.laboratoryhours, 0)                    AS laboratoryhours,
                COALESCE(cs.creditunits,     0)                    AS creditunits,
                (COALESCE(cs.lecturehours,0) + COALESCE(cs.laboratoryhours,0)) AS total_hours,
                pyl.programcode                                     AS programcode,
                pyl.yearlevel                                       AS yearlevel,
                sec.sectionname                                     AS sectionname,
                sv.status,
                sv.versionid
            FROM schedule_version sv
            JOIN schedule sc              ON sv.scheduleid             = sc.scheduleid
            JOIN curriculumsubject cs     ON sc.curriculumsubjectid    = cs.curriculumsubjectid
            JOIN sections sec             ON sc.sectionid              = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid  = pyl.programyearlevelid
            LEFT JOIN faculty f           ON sc.employeenumber         = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid             = sv.versionid
            LEFT JOIN room r              ON ss.roomid                 = r.roomid
            LEFT JOIN timeslot ts_s       ON ss.starttimeid            = ts_s.timeid
            LEFT JOIN timeslot ts_e       ON ss.endtimeid              = ts_e.timeid
            WHERE {status_clause}
              {prog_clause}
              {yl_clause}
              AND sc.semesterid = (
                  SELECT semesterid FROM semester
                  WHERE semestertype = %s AND academicyearid = %s LIMIT 1
              )
              {section_clause}
              {instructor_clause}
              AND sv.version_number = (
                  SELECT MAX(sv2.version_number)
                  FROM schedule_version sv2
                  WHERE sv2.scheduleid = sv.scheduleid
                    AND sv2.status = sv.status
              )
            ORDER BY
                CASE WHEN sv.status = 'Published' THEN 0 ELSE 1 END,
                pyl.programcode, pyl.yearlevel, ts_s.timevalue NULLS LAST
        """, q_params)
            normalized_rows = cur.fetchall()
            print(f"[DEBUG] normalized_rows count={len(normalized_rows)}")
            for r in normalized_rows[:3]:
                print(f"  norm: subj={r['subjectcode']!r} start={r['start_time']!r} day={r['daydesc']!r}")

        # ── 2. historical_data — reference only for semesters that already ended.
        # Must NOT leak into the current/upcoming term being actively scheduled,
        # otherwise Program View shows subjects as "scheduled" (room/instructor filled
        # in) for a section that has no Draft/Published session at all.
        raw_rows = []
        if is_past_sem:
            hist_params = [prog, str(yl), ay, sem, ay]
            hist_instructor_clause = ''
            if emp_num:
                hist_instructor_clause = 'AND employeenumber = %s'
                hist_params.append(emp_num)
            cur.execute(f"""
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
                  {hist_instructor_clause}
            """, hist_params)
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
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
            LEFT JOIN schedule_sessions ss ON ss.versionid=sv.versionid
            WHERE UPPER(pyl.programcode)=UPPER(%s)
              AND pyl.yearlevel=%s
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
                cs.subjectcode   AS "SubjectCode",
                cs.subjectname   AS "SubjectName",
                COALESCE(cs.lecturehours, 0)    AS "LectureHours",
                COALESCE(cs.laboratoryhours, 0) AS "LaboratoryHours",
                COALESCE(cs.creditunits, 0)     AS "CreditUnits",
                pyl.programcode AS "Program",
                pyl.yearlevel  AS "YearLevel",
                (COALESCE(cs.lecturehours,0) + COALESCE(cs.laboratoryhours,0)) AS "Hours",
                ss.daydesc       AS "Day/s",
                TO_CHAR(ts_s.timevalue,'HH12:MI AM') || ' - ' || TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS "Time",
                COALESCE(r.roomname, 'TBA') AS "Room"
            FROM schedule_version sv
            JOIN schedule sc              ON sv.scheduleid = sc.scheduleid AND sv.status = 'Published'
            JOIN curriculumsubject cs      ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN sections sec              ON sc.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN faculty f            ON sc.employeenumber = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid = sv.versionid
            LEFT JOIN room r               ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s        ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e        ON ss.endtimeid   = ts_e.timeid
        """
        if prog: norm_filters.append("UPPER(pyl.programcode) = UPPER(%s)"); norm_params.append(prog)
        if yl:   norm_filters.append("pyl.yearlevel = %s");  norm_params.append(int(yl))
        if sem or ay:
            sem_sub = "SELECT semesterid FROM semester WHERE TRUE"
            if sem: sem_sub += " AND semestertype = %s"; norm_params.append(sem)
            if ay:  sem_sub += " AND academicyearid = %s"; norm_params.append(ay)
            sem_sub += " LIMIT 1"
            norm_filters.append(f"sc.semesterid = ({sem_sub})")
        if norm_filters:
            norm_q += " WHERE " + " AND ".join(norm_filters)
        norm_q += " ORDER BY f.lastname, cs.subjectcode, ss.daydesc NULLS LAST"
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
        nf.append(f"UPPER(pyl.programcode) IN ({','.join(['%s']*len(programs))})"); np_.extend([p.upper() for p in programs])
    if year_levels:
        nf.append(f"pyl.yearlevel IN ({','.join(['%s']*len(year_levels))})"); np_.extend(year_levels)

    norm_q = """
        SELECT
            COALESCE(f.lastname||', '||f.firstname||COALESCE(' '||f.middlename,''),'TBA') AS "Instructor",
            cs.subjectcode   AS "SubjectCode",
            cs.subjectname   AS "SubjectName",
            COALESCE(cs.lecturehours,0)    AS "LectureHours",
            COALESCE(cs.laboratoryhours,0) AS "LaboratoryHours",
            COALESCE(cs.creditunits,0)     AS "CreditUnits",
            pyl.programcode AS "Program",
            pyl.yearlevel  AS "YearLevel",
            sec.sectionname  AS "Section",
            (COALESCE(cs.lecturehours,0)+COALESCE(cs.laboratoryhours,0)) AS "Hours",
            ss.daydesc       AS "Day/s",
            CASE WHEN ts_s.timevalue IS NOT NULL AND ts_e.timevalue IS NOT NULL
                 THEN TO_CHAR(ts_s.timevalue,'HH12:MI AM')||' - '||TO_CHAR(ts_e.timevalue,'HH12:MI AM')
                 ELSE NULL END AS "Time",
            COALESCE(r.roomname,'TBA') AS "Room",
            ay.yearstart||'-'||ay.yearend AS "AcademicYear",
            sem.semestertype AS "SemesterType"
        FROM schedule_version sv
        JOIN schedule sc           ON sv.scheduleid=sc.scheduleid
        JOIN curriculumsubject cs   ON sc.curriculumsubjectid=cs.curriculumsubjectid
        JOIN sections sec           ON sc.sectionid=sec.sectionid
        LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid=pyl.programyearlevelid
        JOIN semester sem           ON sc.semesterid=sem.semesterid
        JOIN academicyear ay        ON sem.academicyearid=ay.academicyearid
        LEFT JOIN faculty f         ON sc.employeenumber=f.employeenumber
        LEFT JOIN schedule_sessions ss ON ss.versionid=sv.versionid
        LEFT JOIN room r            ON ss.roomid=r.roomid
        LEFT JOIN timeslot ts_s     ON ss.starttimeid=ts_s.timeid
        LEFT JOIN timeslot ts_e     ON ss.endtimeid=ts_e.timeid
        WHERE """ + " AND ".join(nf) + """
          AND sv.version_number = (
              SELECT MAX(sv2.version_number)
              FROM schedule_version sv2
              WHERE sv2.scheduleid = sv.scheduleid
                AND sv2.status = sv.status
          )
        ORDER BY pyl.programcode, pyl.yearlevel, cs.subjectcode, ss.daydesc NULLS LAST
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


@app.route('/api/schedule/export-generated', methods=['POST'])
def api_export_generated_schedule():
    """
    Export the in-memory generated schedule (not yet saved to DB) in the requested format.
    Accepts: {schedule_data: [...], format: 'pdf'|'docx'|'xlsx'|'csv', context: {...}, filename: '...'}
    """
    try:
        data          = request.get_json() or {}
        fmt           = (data.get('format') or 'csv').lower()
        raw_sched     = data.get('schedule_data') or []
        ctx           = data.get('context') or {}
        filename_base = (data.get('filename') or 'schedule_export').strip()
        for ext in ('.pdf', '.docx', '.xlsx', '.csv'):
            if filename_base.lower().endswith(ext):
                filename_base = filename_base[:-len(ext)]

        program    = ctx.get('program', '')
        year_level = ctx.get('yearLevel', '')
        acad_year  = ctx.get('acadYear', '')
        term       = ctx.get('term', '')
        section    = ctx.get('section', '')
        sem_map    = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}
        sem_label  = sem_map.get(term, term)

        # Normalize the JS schedule_data format → the column keys expected by _sch_gen_*
        rows = []
        for cls in raw_sched:
            rows.append({
                'Instructor':      cls.get('instructor') or 'TBA',
                'SubjectCode':     cls.get('subject_code') or cls.get('subjectcode') or '',
                'SubjectName':     cls.get('description') or cls.get('subjectname') or '',
                'LectureHours':    cls.get('lec_hours') or cls.get('lecturehours') or 0,
                'LaboratoryHours': cls.get('lab_hours') or cls.get('laboratoryhours') or 0,
                'CreditUnits':     cls.get('credit_units') or cls.get('creditunits') or cls.get('units') or 0,
                'Program':         cls.get('course') or program,
                'YearLevel':       year_level,
                'Section':         section,
                'Hours':           cls.get('hours') or '',
                'Day/s':           cls.get('days') or '',
                'Time':            (cls.get('time') or '').replace('–', '-'),
                'Room':            cls.get('room') or 'TBA',
                'AcademicYear':    acad_year,
                'SemesterType':    sem_label,
            })

        groups = _sch_exp_groups(rows)

        if fmt == 'csv':
            out, mime, ext = _sch_gen_csv(rows), 'text/csv', '.csv'
        elif fmt == 'xlsx':
            out, mime, ext = _sch_gen_xlsx(rows, groups), \
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'
        elif fmt == 'docx':
            out, mime, ext = _sch_gen_docx(rows, groups, f'{program} Year {year_level} — {sem_label} {acad_year}'), \
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'
        elif fmt == 'pdf':
            out, mime, ext = _sch_gen_pdf(rows, groups, f'{program} Year {year_level} — {sem_label} {acad_year}'), \
                'application/pdf', '.pdf'
        else:
            return jsonify({'error': f'Unsupported format: {fmt}'}), 400

        return Response(out, mimetype=mime,
                        headers={'Content-Disposition': f'attachment; filename="{filename_base}{ext}"'})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


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


def _resolve_hist_empnum(cur, inst_name):
    """Resolve an instructor name string to an employee number using DB lookup.
    Returns the employee number string, or None if unresolved/ambiguous."""
    if not inst_name or not inst_name.strip():
        return None
    parts = inst_name.strip().split(',', 1)
    lastname = parts[0].strip()
    firstname_key = parts[1].strip().split()[0].rstrip('.') if len(parts) > 1 and parts[1].strip() else ''
    if not lastname:
        return None
    # Try last name + first name prefix
    if firstname_key:
        cur.execute("""
            SELECT employeenumber FROM faculty
            WHERE UPPER(lastname) = UPPER(%s)
              AND (f.employeestatus IS NULL OR f.employeestatus != 'Archive')
              AND UPPER(firstname) LIKE UPPER(%s) || '%%'
            LIMIT 2
        """.replace('f.', ''), (lastname, firstname_key))
        rows = cur.fetchall()
        if len(rows) == 1:
            return str(rows[0]['employeenumber'])
    # Try last name only
    cur.execute("""
        SELECT employeenumber FROM faculty
        WHERE UPPER(lastname) = UPPER(%s)
          AND (employeestatus IS NULL OR employeestatus != 'Archive')
        LIMIT 2
    """, (lastname,))
    rows = cur.fetchall()
    if len(rows) == 1:
        return str(rows[0]['employeenumber'])
    return None  # ambiguous or not found


def _insert_historical(cur, inst, s_code, subj_name, prog, yl, days_raw, time_raw, room, sem_id, ay_id, lec, lab, unit, hrs, emp_num=None):
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
            "Lecture Hours", "Laboratory Hours", "Credit Units", "Hours",
            employeenumber
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (inst, s_code, subj_name, prog, yl, days_raw, time_raw, room,
          sem_id, ay_id, lec, lab, unit, hrs, emp_num or None))


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
    # Ensure schedule_version extra columns exist before any INSERT uses them
    try:
        _ensure_source_col(cur)
        _ensure_original_status_col(cur)
        conn.commit()
    except Exception:
        pass
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

        # Determine whether this import belongs to the current (active) semester.
        # A schedule is "current" ONLY if:
        #   1. The selected AY is the active AY in the system (academicyear.isactive = TRUE), AND
        #   2. The specific semester's end date has NOT passed yet
        # Any schedule for a past semester (even in the active AY) goes to historical_data.
        cur.execute("SELECT academicyearid FROM academicyear WHERE isactive = TRUE LIMIT 1")
        _active_ay_row = cur.fetchone()
        _active_ay = _active_ay_row['academicyearid'] if _active_ay_row else None
        is_current = (ay_id == _active_ay) and not (sem_end and sem_end < today)
        print(f"\n[DEBUG IMPORT] ay_id={ay_id!r} sem_type={sem_type!r} sem_id={sem_id!r} sem_end={sem_end} active_ay={_active_ay!r} is_current={is_current}")

        # Pre-compute values used by auto-create logic inside the row loop
        cur.execute("SELECT yearstart FROM academicyear WHERE academicyearid = %s", (ay_id,))
        _ay_info = cur.fetchone()
        _ay_yearstart = _ay_info['yearstart'] if _ay_info else None
        _sem_char = 'B' if ('2' in str(sem_type or '') or str(sem_type or '').upper() == 'B') else \
                    'C' if ('SUM' in str(sem_type or '').upper() or str(sem_type or '').upper() == 'C') else 'A'

        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)

        saved_current = 0
        saved_historical = 0
        # Initialised before loop so the except block can always report context.
        prog = ''; yl = 1; s_code = ''

        for row in csv_input:
            row = {k.strip(): v.strip() for k, v in row.items() if k}

            inst       = row.get('Instructor', '')
            s_code     = row.get('SubjectCode') or row.get('Subject Code') or row.get('SubjectCo') or ''
            # Strip trailing year-level number from program (e.g. "BSIT 3" → "BSIT")
            import re as _re_prog
            prog       = _re_prog.sub(r'\s+\d+$', '', row.get('Program', '').strip()).strip()
            # Clamp yl to 1-5 — prevents smallint overflow when CSV has bad data in YearLevel column
            _yl_raw = safe_int(row.get('YearLevel') or row.get('Year Level')) or 0
            yl      = max(1, min(_yl_raw, 5)) if 0 < _yl_raw <= 32767 else 0

            # Fallback: parse program / year level from Course column when
            # dedicated columns are absent — e.g. "BSIT 2", "DCPET 3"
            _course_col = (row.get('Course') or row.get('Section') or row.get('course') or '').strip()
            if (_course_col and (not prog or not yl)):
                _mc_csv = _re_prog.match(
                    r'^([A-Z][A-Z0-9\-]{1,15})\s+([1-5])(?:[^0-9]|$)',
                    _course_col.upper()
                )
                if _mc_csv:
                    if not prog:
                        prog = _mc_csv.group(1)
                    if not yl:
                        yl = int(_mc_csv.group(2))

            yl = yl or 1  # final fallback
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
                    # No match → emp_num stays None; row saved to schedule with NULL instructor (TBA)

                # 2. Curriculum subject — try program+subjectcode, fallback to subjectcode only
                cs_id = None
                if s_code and prog:
                    cur.execute("""
                        SELECT cs.curriculumsubjectid
                        FROM curriculumsubject cs
                        JOIN curriculum c ON cs.curriculumid = c.curriculumid
                        WHERE c.programcode = %s AND UPPER(cs.subjectcode) = UPPER(%s)
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

                # 3. Section — program_yearlevel join (active, then all, then any year)
                sec_id = None
                if prog and yl:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                        WHERE UPPER(pyl.programcode)=UPPER(%s) AND pyl.yearlevel=%s AND sec.isactive=TRUE
                        ORDER BY sec.sectionname LIMIT 1
                    """, (prog, yl))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                if not sec_id and prog and yl:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                        WHERE UPPER(pyl.programcode)=UPPER(%s) AND pyl.yearlevel=%s
                        ORDER BY sec.sectionname LIMIT 1
                    """, (prog, yl))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                if not sec_id and prog:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                        WHERE UPPER(pyl.programcode)=UPPER(%s)
                        ORDER BY pyl.yearlevel, sec.sectionname LIMIT 1
                    """, (prog,))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None

                # ── Auto-create missing records so imported data always lands in
                #    the schedule tables (not historical_data) for the current AY ──
                # Only auto-create for current semesters; past semesters go to historical_data
                if is_current and not (cs_id and sec_id) and s_code and prog:
                    # 1. Find best curriculum for this program
                    _best_curr_id = None
                    _entry_year = (_ay_yearstart - (yl - 1)) if _ay_yearstart else None
                    if _entry_year:
                        cur.execute("""
                            SELECT curriculumid FROM curriculum
                            WHERE UPPER(programcode)=UPPER(%s)
                              AND CAST(SUBSTRING(curriculumyear, 1, 4) AS INT) <= %s
                            ORDER BY curriculumyear DESC LIMIT 1
                        """, (prog, _entry_year))
                        _cr = cur.fetchone(); _best_curr_id = _cr['curriculumid'] if _cr else None
                    if not _best_curr_id:
                        cur.execute("""
                            SELECT curriculumid FROM curriculum
                            WHERE UPPER(programcode)=UPPER(%s)
                            ORDER BY curriculumyear DESC NULLS LAST LIMIT 1
                        """, (prog,))
                        _cr = cur.fetchone(); _best_curr_id = _cr['curriculumid'] if _cr else None
                    if not _best_curr_id:
                        _cy = str(_entry_year) if _entry_year else str(_ay_yearstart or 'IMPORTED')
                        _cc = f"{prog.upper()}-{_cy}"
                        try:
                            cur.execute("SAVEPOINT curr_auto")
                            cur.execute("""
                                INSERT INTO curriculum (programcode, curriculumcode, curriculumyear, isactive)
                                VALUES (%s, %s, %s, TRUE) RETURNING curriculumid
                            """, (prog.upper(), _cc, _cy))
                            _ca = cur.fetchone()
                            cur.execute("RELEASE SAVEPOINT curr_auto")
                            if _ca: _best_curr_id = _ca['curriculumid']
                        except Exception:
                            cur.execute("ROLLBACK TO SAVEPOINT curr_auto")
                            cur.execute("SELECT curriculumid FROM curriculum WHERE UPPER(programcode)=UPPER(%s) ORDER BY curriculumyear DESC NULLS LAST LIMIT 1", (prog,))
                            _ca2 = cur.fetchone()
                            if _ca2: _best_curr_id = _ca2['curriculumid']

                    # 2. Find or create program_yearlevel
                    _pyl_id = None
                    _start_ay_str = f"{_entry_year}-{_entry_year+1}" if _entry_year else ''
                    if _start_ay_str:
                        cur.execute("""
                            SELECT programyearlevelid FROM program_yearlevel
                            WHERE UPPER(programcode)=UPPER(%s) AND academicyearid=%s
                              AND startacademicyear=%s AND yearlevel=%s LIMIT 1
                        """, (prog, ay_id, _start_ay_str, yl))
                        _pylr = cur.fetchone(); _pyl_id = _pylr['programyearlevelid'] if _pylr else None
                    if not _pyl_id:
                        cur.execute("""
                            SELECT programyearlevelid FROM program_yearlevel
                            WHERE UPPER(programcode)=UPPER(%s) AND academicyearid=%s AND yearlevel=%s LIMIT 1
                        """, (prog, ay_id, yl))
                        _pylr = cur.fetchone(); _pyl_id = _pylr['programyearlevelid'] if _pylr else None
                    if not _pyl_id:
                        try:
                            cur.execute("SAVEPOINT pyl_create")
                            cur.execute("""
                                INSERT INTO program_yearlevel (programcode, academicyearid, startacademicyear, yearlevel, curriculumid, isactive)
                                VALUES (%s, %s, %s, %s, %s, TRUE)
                                ON CONFLICT (programcode, academicyearid, startacademicyear, yearlevel) DO UPDATE SET curriculumid=EXCLUDED.curriculumid, isactive=TRUE
                                RETURNING programyearlevelid
                            """, (prog.upper(), ay_id, _start_ay_str or '', yl, _best_curr_id))
                            _pylr2 = cur.fetchone()
                            cur.execute("RELEASE SAVEPOINT pyl_create")
                            if _pylr2: _pyl_id = _pylr2['programyearlevelid']
                        except Exception:
                            cur.execute("ROLLBACK TO SAVEPOINT pyl_create")
                            cur.execute("SELECT programyearlevelid FROM program_yearlevel WHERE UPPER(programcode)=UPPER(%s) AND academicyearid=%s AND yearlevel=%s LIMIT 1", (prog, ay_id, yl))
                            _pylr3 = cur.fetchone()
                            if _pylr3: _pyl_id = _pylr3['programyearlevelid']

                    # 3. Find or create section
                    if not sec_id and _pyl_id:
                        # Pass 0: exact match by course column value (e.g. "BSIT 2", "DCPET 3")
                        if _course_col:
                            cur.execute("SELECT sectionid FROM sections WHERE programyearlevelid=%s AND UPPER(sectionname)=UPPER(%s) LIMIT 1", (_pyl_id, _course_col))
                            _sr = cur.fetchone(); sec_id = _sr['sectionid'] if _sr else None
                        # Pass 1: any active section under this program_yearlevel
                        if not sec_id:
                            cur.execute("SELECT sectionid FROM sections WHERE programyearlevelid=%s AND isactive=TRUE ORDER BY sectionname LIMIT 1", (_pyl_id,))
                            _sr = cur.fetchone(); sec_id = _sr['sectionid'] if _sr else None
                        if not sec_id:
                            cur.execute("SELECT sectionid FROM sections WHERE programyearlevelid=%s ORDER BY sectionname LIMIT 1", (_pyl_id,))
                            _sr = cur.fetchone(); sec_id = _sr['sectionid'] if _sr else None
                        if not sec_id:
                            # Prefer course column value as the section name; fall back to generic
                            _sec_nm = (_course_col if _course_col else f"{prog.upper()}-{yl}A")[:10]
                            try:
                                cur.execute("SAVEPOINT sec_create")
                                cur.execute("""
                                    INSERT INTO sections (programyearlevelid, sectionname, isactive)
                                    VALUES (%s, %s, TRUE)
                                    ON CONFLICT (programyearlevelid, sectionname) DO NOTHING
                                    RETURNING sectionid
                                """, (_pyl_id, _sec_nm))
                                _sr2 = cur.fetchone()
                                cur.execute("RELEASE SAVEPOINT sec_create")
                                if _sr2: sec_id = _sr2['sectionid']
                            except Exception:
                                cur.execute("ROLLBACK TO SAVEPOINT sec_create")
                                cur.execute("SELECT sectionid FROM sections WHERE programyearlevelid=%s AND UPPER(sectionname)=UPPER(%s) LIMIT 1", (_pyl_id, _sec_nm))
                                _sr3 = cur.fetchone()
                                if _sr3: sec_id = _sr3['sectionid']

                    # 4. Find or create curriculumsubject
                    if not cs_id and _best_curr_id:
                        cur.execute("""
                            INSERT INTO curriculumsubject (curriculumid, subjectcode, subjectname, creditunits, lecturehours, laboratoryhours, tuitionhours, yearlevel, semester)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT ON CONSTRAINT uq_curriculumsubject DO UPDATE SET
                                subjectname=EXCLUDED.subjectname, creditunits=EXCLUDED.creditunits,
                                lecturehours=EXCLUDED.lecturehours, laboratoryhours=EXCLUDED.laboratoryhours,
                                tuitionhours=EXCLUDED.tuitionhours
                            RETURNING curriculumsubjectid
                        """, (_best_curr_id, s_code.upper(), subj_name or s_code, unit, lec, lab, lec + lab, yl, _sem_char))
                        _cs_r = cur.fetchone()
                        if not _cs_r:
                            cur.execute("SELECT curriculumsubjectid FROM curriculumsubject WHERE curriculumid=%s AND UPPER(subjectcode)=UPPER(%s) LIMIT 1", (_best_curr_id, s_code))
                            _cs_r = cur.fetchone()
                        if _cs_r: cs_id = _cs_r['curriculumsubjectid']

                # Only insert into current schedule tables for current semesters
                if is_current and cs_id and sec_id:
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
                            INSERT INTO schedule_version (scheduleid, version_number, status, source, original_status)
                            VALUES (%s, 1, 'Published', 'import', 'Published') RETURNING versionid
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

                        inserted_as_current = True
                        saved_current += 1

            # Insert to historical_data if: (1) past semester, OR (2) current semester but couldn't insert
            if not is_current or not inserted_as_current:
                # Skip if already inserted as current (prevents duplicates for current semester)
                if not inserted_as_current:
                    norm_hist_room = normalize_room(room_raw) if room_raw.strip() else ''
                    hist_emp_num = _resolve_hist_empnum(cur, inst)
                    _insert_historical(cur, inst, s_code, subj_name, prog, yl, days_raw, time_raw, norm_hist_room, sem_id, ay_id, lec, lab, unit, hrs, emp_num=hist_emp_num)
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
        _yl_name = {1:'First',2:'Second',3:'Third',4:'Fourth',5:'Fifth'}.get(yl, str(yl))
        _ctx = f"Program: {prog or '(unknown)'}, {_yl_name} Year, Subject: {s_code or '(unknown)'}"
        print(f"Import Error Detail [{_ctx}]: {str(e)}")
        flash(f"Import failed — {_ctx}: {str(e)}")
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

    # ── Canonical programme codes: hardcoded base + all active DB programs ───
    _base_valid = frozenset({
        'BEED', 'BPA', 'BPAFA', 'BSARCH', 'BSA', 'BSAM',
        'BSBAFM', 'BSBAMM', 'BSBIO', 'BSCE', 'BSEDMT',
        'BSEE', 'BSHM', 'BSIT', 'BSND', 'BSOA',
        'DCET', 'DCVET', 'DEET', 'DIT', 'DOMT-LOM',
    })
    _db_prog_rows = query_db("SELECT UPPER(programcode) AS programcode FROM programs WHERE isactive = TRUE")
    _db_prog_codes = frozenset(r['programcode'] for r in (_db_prog_rows or []))
    # Build a stripped-code map: strip spaces+hyphens from both file code and DB code
    # so "BSBIO AT", "BSBIO-AT", "BSBIOAT" all resolve to the same DB canonical code.
    _db_stripped_map = {}
    for _c in _db_prog_codes:
        _stripped = re.sub(r'[-\s]', '', _c)
        _db_stripped_map.setdefault(_stripped, _c)
    # VALID_PROGS includes both the exact DB codes and their stripped forms
    VALID_PROGS = _base_valid | _db_prog_codes | frozenset(_db_stripped_map.keys())
    # Add DB program codes to keyword detection so header rows are recognized
    PROG_KW = PROG_KW + tuple(_db_prog_codes)

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
        normalised = re.sub(r'\s*[-–—]\s*', '-', code.strip()).upper()
        # DB lookup FIRST — exact code takes priority over all hardcoded aliases.
        # This prevents old entries like 'BPAPFM'→'BPAFA' from overriding
        # an actual DB program like 'BPA-PFM'.
        if normalised in _db_prog_codes:
            return normalised
        stripped = re.sub(r'[-\s]', '', normalised)
        if stripped in _db_stripped_map:
            return _db_stripped_map[stripped]
        # Hardcoded aliases (backward-compat for files that use old codes)
        if normalised in PROG_NORM:
            return PROG_NORM[normalised]
        first = normalised.split('/')[0].strip()
        if first and first != normalised and first in PROG_NORM:
            return PROG_NORM[first]
        # Full programme name matching (e.g. "BACHELOR OF SCIENCE IN IT" → "BSIT")
        global_result = _sis_norm_prog(code)
        if global_result:
            return global_result
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
        if v is None: return ''
        if isinstance(v, float) and v == int(v): return str(int(v))
        return str(v).strip()

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

    def _course_to_offering_yl(course):
        """
        Extract the FULL academic offering code and year level from the COURSE column.
        Handles multi-word offering codes such as "BSBIO AT 3" or "BSBIO PT 4".
          "BSBIO AT 3"  → ("BSBIO AT", 3)
          "BSBIO PT 4"  → ("BSBIO PT", 4)
          "BSBIO 1"     → ("BSBIO", 1)
          "BEED 1-A"    → ("BEED", 1)
          "BSIT-2-A"    → ("BSIT", 2)
        Returns ('', 0) when no year digit 1-5 is found.
        """
        if not course: return '', 0
        c = course.strip().upper()
        if not re.search(r'\d', c): return '', 0

        # Style 1: space-separated words before a standalone year digit 1-5
        # Captures "BSBIO AT" from "BSBIO AT 3", "BSBIO" from "BSBIO 1", "BEED" from "BEED 1-A"
        m = re.search(r'^([A-Z][A-Z0-9]*(?:\s+[A-Z][A-Z0-9]*)*)\s+([1-5])(?:[^0-9]|$)', c)
        if m:
            return m.group(1).strip(), int(m.group(2))

        # Style 2: hyphen-separated first token, e.g. "BSIT-2-A" → ("BSIT", 2)
        m2 = re.match(r'^([A-Z][A-Z0-9]+)-([1-5])(?:[^0-9]|$)', c)
        if m2:
            return m2.group(1).strip(), int(m2.group(2))

        return '', 0

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

            # ── Signatory / approval section → stop reading this sheet ────
            # Once the signatory block is reached there is no more schedule data.
            if _is_signatory_section(full_upper, len(non_empty)):
                break

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
            # Reject subject codes that look like signatory labels (names with commas,
            # position titles, etc.) — these are footer rows that slipped past the
            # per-row signatory break because the keyword landed in a non-first cell.
            if _is_signatory_code(s_code):
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

            # Extract FULL offering code from COURSE column (e.g. "BSBIO AT" from "BSBIO AT 3")
            course_offering, course_yl = _course_to_offering_yl(course)

            # Offering code: COURSE column takes priority (preserves track suffix like "AT"/"PT")
            offering_code = course_offering if course_offering else (raw_prog_for_row or prog or '')

            # Derive base programme from offering code's first token for VALID_PROGS check
            if not prog and course_offering:
                base_from_course = _norm_prog(course_offering.split()[0])
                if base_from_course in VALID_PROGS:
                    prog             = base_from_course
                    raw_prog_for_row = course_offering  # keep full offering for display

            sec_prog, sec_yl = _section_to_prog_yl(course)
            if not prog and sec_prog:
                prog             = sec_prog
                raw_prog_for_row = sec_prog       # program resolved from section code
            # Only fall back to the section-derived year level when no year-level header
            # has been seen yet.  In PUP SIS files the trailing digit in a section code
            # like "BEED 1-A" is the section-group number, not the year level, so
            # unconditionally overriding current_yl would pull subjects from every year
            # block into FIRST YEAR whenever Course starts with "<PROG> 1…".
            if course_yl and not current_yl_label:
                yl = course_yl
            elif sec_yl and not current_yl_label:
                yl = sec_yl

            prog = _norm_prog(prog)   # resolve to canonical programme code

            # Skip rows with no programme or a programme not in the official 21
            if not prog or prog not in VALID_PROGS:
                continue

            # Normalise offering code: fall back to canonical prog if nothing better was found
            if not offering_code:
                offering_code = prog

            rows_out.append({
                'instructor': inst,  'subj_code': s_code,   'subj_name': subj_nm,
                'lec_hrs':  lec_raw, 'lab_hrs':   lab_raw,  'credit_units': unit_raw,
                'course':   course,  'hours':     hrs_raw,  'days': days_raw,
                'time':     time_raw,'room':       room_raw,
                'program':        prog,
                'offering_code':  offering_code.upper().strip(),  # full code e.g. "BSBIO AT"
                'raw_program':    raw_prog_for_row or prog,  # original file code; fallback to canonical
                'year_level':     yl,
                'year_label':     current_yl_label,  # full original header text preserved from Excel
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
        # Use offering_code (not just program) so BSBIO, BSBIO AT, BSBIO PT are separate groups
        key = (r.get('offering_code', r['program']), r['year_level'], lk, _code_key(r['subj_code']))
        if key not in seen:
            r['year_label']     = lk          # store the normalised label
            r['section_count']  = 1
            r['conflict_notes'] = ''
            seen[key] = r

    deduped = list(seen.values())
    deduped.sort(key=lambda r: (
        r.get('offering_code', r['program']),
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
            f = float(str(val).strip())
            return int(f) if f == int(f) else f
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
    # Ensure all programs have program_yearlevel rows + default sections before we look them up
    try:
        _auto_setup_program_yearlevels(cur)
        conn.commit()
    except Exception:
        pass

    # Resolve the import AY's yearstart so we can calculate each program_yearlevel's entry year
    ay_yearstart = None
    if ay_id:
        cur.execute("SELECT yearstart FROM academicyear WHERE academicyearid=%s", (ay_id,))
        _ayr = cur.fetchone()
        ay_yearstart = int(_ayr['yearstart']) if _ayr else None

    # Build offering-code normalization map (space ↔ hyphen variants → DB canonical code)
    _db_progs_prev = query_db("SELECT programcode FROM programs WHERE isactive=TRUE") or []
    _prog_norm_prev = {}
    for _pp in _db_progs_prev:
        _cc = _pp['programcode'].upper()
        import re as _re_prev
        for _v in [_cc, _re_prev.sub(r'\s+', '-', _cc), _re_prev.sub(r'-', ' ', _cc), _re_prev.sub(r'[-\s]', '', _cc)]:
            _prog_norm_prev.setdefault(_v, _cc)
        # Also map the suffix after the last hyphen (e.g. "LOM" → "DOMT-LOM")
        if '-' in _cc:
            _prog_norm_prev.setdefault(_cc.rsplit('-', 1)[-1], _cc)

    preview_rows = []
    try:
        for item in raw_rows:
            inst          = item['instructor']
            s_code        = item['subj_code']
            prog          = item['program']
            offering_code = (item.get('offering_code') or prog or '').strip().upper()
            offering_code = _prog_norm_prev.get(offering_code, offering_code)
            yl            = item['year_level']
            room_raw      = item['room']
            flags, status = [], 'ready'

            cs_id = None
            if s_code and offering_code:
                # Strict: subject must exist in this specific offering's own curriculum only
                cur.execute("""
                    SELECT cs.curriculumsubjectid FROM curriculumsubject cs
                    JOIN curriculum c ON cs.curriculumid = c.curriculumid
                    WHERE UPPER(c.programcode)=UPPER(%s) AND UPPER(cs.subjectcode)=UPPER(%s) LIMIT 1
                """, (offering_code, s_code))
                r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None
            if not cs_id:
                status = 'blocked'
                flags.append(f'Subject "{s_code or "—"}" not in {offering_code or "—"} curriculum')

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

            # ── Resolve entry year and best-matching curriculum ──
            entry_year = (ay_yearstart - (yl - 1)) if ay_yearstart and yl else None
            best_curr_id = None
            if offering_code and entry_year:
                cur.execute("""
                    SELECT curriculumid FROM curriculum
                    WHERE UPPER(programcode)=UPPER(%s)
                      AND CAST(SUBSTRING(curriculumyear, 1, 4) AS INT) <= %s
                    ORDER BY curriculumyear DESC LIMIT 1
                """, (offering_code, entry_year))
                _cr = cur.fetchone()
                best_curr_id = _cr['curriculumid'] if _cr else None
            if not best_curr_id and offering_code:
                # Fallback: latest curriculum for this program
                cur.execute("""
                    SELECT curriculumid FROM curriculum
                    WHERE UPPER(programcode)=UPPER(%s)
                    ORDER BY curriculumyear DESC NULLS LAST LIMIT 1
                """, (offering_code,))
                _cr = cur.fetchone()
                best_curr_id = _cr['curriculumid'] if _cr else None

            # Resolve entry academicyearid (e.g. AY2023)
            entry_ay_id = None
            if entry_year:
                cur.execute("SELECT academicyearid FROM academicyear WHERE yearstart=%s LIMIT 1", (entry_year,))
                _eayr = cur.fetchone()
                entry_ay_id = _eayr['academicyearid'] if _eayr else f'AY{entry_year}'

            # ── Find section via program_yearlevel ──
            sec_id = None
            if offering_code and ay_id:
                cur.execute("""
                    SELECT sec.sectionid FROM sections sec
                    JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                    WHERE UPPER(pyl.programcode) = UPPER(%s) AND pyl.yearlevel = %s
                      AND pyl.academicyearid = %s AND sec.isactive = TRUE
                    ORDER BY sec.sectionname LIMIT 1
                """, (offering_code, yl, ay_id))
                _sr = cur.fetchone(); sec_id = _sr['sectionid'] if _sr else None
                if not sec_id:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                        WHERE UPPER(pyl.programcode) = UPPER(%s) AND pyl.yearlevel = %s
                          AND pyl.academicyearid = %s
                        ORDER BY sec.sectionname LIMIT 1
                    """, (offering_code, yl, ay_id))
                    _sr = cur.fetchone(); sec_id = _sr['sectionid'] if _sr else None

            if not sec_id:
                if not best_curr_id:
                    status = 'blocked'
                    flags.append(f'No curriculum found for "{offering_code or "—"}" — cannot import')
                else:
                    if status != 'blocked': status = 'warning'
                    flags.append(f'Cohort/section for "{offering_code or "—"}" Yr {yl} will be auto-created')

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
        try: conn.commit()
        except Exception: pass
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
    override = data.get('override', False)
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
            f = float(str(val).strip())
            return int(f) if f == int(f) else f
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
    # Ensure schedule_version extra columns exist before any INSERT uses them
    try:
        _ensure_source_col(cur)
        _ensure_original_status_col(cur)
        conn.commit()
    except Exception:
        pass
    # Ensure all programs have program_yearlevel rows + default sections before we look them up
    try:
        _auto_setup_program_yearlevels(cur)
        conn.commit()
    except Exception:
        pass

    # Build a map from uppercase programcode -> exact DB programcode for normalization
    cur.execute("SELECT programcode FROM programs")
    _prog_norm_map = {r['programcode'].strip().upper(): r['programcode'].strip() for r in cur.fetchall()}

    saved_c = 0; saved_h = 0; created_subj = 0; created_sec = 0; saved_skip = 0
    # Initialised before loop so the except block can always report context.
    prog = ''; yl = 1; s_code = ''

    try:
        # Fetch ALL semester rows for this AY+type — multiple rows can exist if
        # the semester was re-created, causing schedules to accumulate across IDs.
        cur.execute("""
            SELECT semesterid, semstartdate, semenddate, isactive
            FROM semester
            WHERE academicyearid=%s AND semestertype=%s
            ORDER BY isactive DESC NULLS LAST, semesterid DESC
        """, (ay_id, sem_type))
        sem_rows = cur.fetchall()
        if not sem_rows:
            return jsonify({'error': 'Semester not found'}), 400

        # Canonical semesterid for inserting new records: prefer isactive=TRUE, else latest
        sem_res    = sem_rows[0]
        sem_id     = sem_res['semesterid']
        s_dt       = sem_res['semstartdate']
        e_dt       = sem_res['semenddate']
        all_sem_ids = [r['semesterid'] for r in sem_rows]

        # Resolve yearstart + active flag of the import AY
        cur.execute("SELECT yearstart, isactive FROM academicyear WHERE academicyearid=%s", (ay_id,))
        _ayr2 = cur.fetchone()
        ay_yearstart  = int(_ayr2['yearstart']) if _ayr2 else None
        ay_is_active  = bool(_ayr2 and _ayr2['isactive'])
        from datetime import date as _date2
        _today2 = _date2.today()
        # Current = AY is active AND today is within [semstartdate, semenddate] (or dates not set)
        # Past semesters (even in active AY) go to historical_data
        is_cur = ay_is_active and (
            (s_dt is None or s_dt <= _today2) and
            (e_dt is None or _today2 <= e_dt)
        )

        # Purge stale historical_data for ALL semesterids of this period
        if is_cur:
            cur.execute("DELETE FROM historical_data WHERE semesterid = ANY(%s)", (all_sem_ids,))
            print(f"[SIS Confirm] Purged historical_data for semesterids={all_sem_ids}")

        # Override: clear ALL schedule data across every semesterid for this AY+type
        if override:
            cur.execute("""
                DELETE FROM schedule_sessions
                WHERE versionid IN (
                    SELECT sv.versionid FROM schedule_version sv
                    JOIN schedule s ON sv.scheduleid = s.scheduleid
                    WHERE s.semesterid = ANY(%s)
                )
            """, (all_sem_ids,))
            cur.execute("""
                DELETE FROM schedule_version
                WHERE scheduleid IN (SELECT scheduleid FROM schedule WHERE semesterid = ANY(%s))
            """, (all_sem_ids,))
            cur.execute("DELETE FROM schedule WHERE semesterid = ANY(%s)", (all_sem_ids,))
            print(f"[SIS Override] Cleared existing schedule for semesterids={all_sem_ids}")

        saved_skip = 0; created_subj = 0; created_sec = 0
        skipped_details = []

        # Build a normalization map so "BSBIO AT" (space) → "BSBIO-AT" (hyphen),
        # "BSBIO-AT" → "BSBIO-AT", etc. — handles all format variants the SIS
        # file produces vs. how programs are actually stored in the DB.
        _db_progs_conf = query_db("SELECT programcode FROM programs WHERE isactive=TRUE") or []
        _prog_norm_map = {}
        for _p in _db_progs_conf:
            _c = _p['programcode'].upper()
            for _variant in [
                _c,
                re.sub(r'\s+', '-', _c),        # spaces → hyphen
                re.sub(r'-', ' ', _c),            # hyphens → space
                re.sub(r'[-\s]', '', _c),         # no separator
            ]:
                _prog_norm_map.setdefault(_variant, _c)
            # Also map the suffix after the last hyphen (e.g. "LOM" → "DOMT-LOM")
            if '-' in _c:
                _prog_norm_map.setdefault(_c.rsplit('-', 1)[-1], _c)

        for row in rows:
            inst          = row.get('instructor', '')
            s_code        = row.get('subj_code', '')
            subj_nm       = row.get('subj_name', '')
            prog          = re.sub(r'\s+\d+$', '', row.get('program', '').strip()).strip()
            offering_code = (row.get('offering_code') or prog or '').strip().upper()
            # Normalize offering_code to the exact programcode stored in the DB
            offering_code = _prog_norm_map.get(offering_code, offering_code)
            yl            = max(1, min(_si(row.get('year_level')) or 1, 5))
            lec           = min(_si(row.get('lec_hrs')), 999)
            lab           = min(_si(row.get('lab_hrs')), 999)
            unit          = min(_si(row.get('credit_units')), 999)
            hrs           = min(_si(row.get('hours')) or lec + lab, 999)
            days_raw      = str(row.get('days', '') or '').strip()
            time_raw      = str(row.get('time', '') or '').strip()
            room_raw      = str(row.get('room', '') or '').strip()

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

                # ── Resolve entry year and best-matching curriculum ──
                entry_year = (ay_yearstart - (yl - 1)) if ay_yearstart else None
                best_curr_id = None
                if offering_code and entry_year:
                    cur.execute("""
                        SELECT curriculumid FROM curriculum
                        WHERE UPPER(programcode)=UPPER(%s)
                          AND CAST(SUBSTRING(curriculumyear, 1, 4) AS INT) <= %s
                        ORDER BY curriculumyear DESC LIMIT 1
                    """, (offering_code, entry_year))
                    _cr = cur.fetchone(); best_curr_id = _cr['curriculumid'] if _cr else None
                if not best_curr_id and offering_code:
                    cur.execute("""
                        SELECT curriculumid FROM curriculum
                        WHERE UPPER(programcode)=UPPER(%s)
                        ORDER BY curriculumyear DESC NULLS LAST LIMIT 1
                    """, (offering_code,))
                    _cr = cur.fetchone(); best_curr_id = _cr['curriculumid'] if _cr else None

                # ── Auto-create a minimal curriculum if still none found ──
                if not best_curr_id and offering_code:
                    _cy = str(entry_year) if entry_year else str(ay_yearstart or '')
                    _cc = f"{offering_code.upper()}-{_cy or 'IMPORTED'}"
                    try:
                        cur.execute("SAVEPOINT curr_auto")
                        cur.execute("""
                            INSERT INTO curriculum (programcode, curriculumcode, curriculumyear, isactive)
                            VALUES (%s, %s, %s, TRUE)
                            RETURNING curriculumid
                        """, (offering_code.upper(), _cc, _cy))
                        _ca = cur.fetchone()
                        cur.execute("RELEASE SAVEPOINT curr_auto")
                        if _ca: best_curr_id = _ca['curriculumid']
                    except Exception:
                        cur.execute("ROLLBACK TO SAVEPOINT curr_auto")
                        cur.execute("""
                            SELECT curriculumid FROM curriculum
                            WHERE UPPER(programcode)=UPPER(%s)
                            ORDER BY curriculumyear DESC NULLS LAST LIMIT 1
                        """, (offering_code,))
                        _ca2 = cur.fetchone()
                        if _ca2: best_curr_id = _ca2['curriculumid']

                # Resolve entry academicyearid
                entry_ay_id = None
                if entry_year:
                    cur.execute("SELECT academicyearid FROM academicyear WHERE yearstart=%s LIMIT 1",
                                (entry_year,))
                    _eayr = cur.fetchone()
                    entry_ay_id = _eayr['academicyearid'] if _eayr else f'AY{entry_year}'

                # ── Find or create program_yearlevel for this program+entry-year ──
                _pyl_id = None
                if offering_code and ay_id:
                    _start_ay_str = f"{entry_year}-{entry_year+1}" if entry_year else None
                    if _start_ay_str:
                        cur.execute("""
                            SELECT programyearlevelid FROM program_yearlevel
                            WHERE UPPER(programcode)=UPPER(%s) AND academicyearid=%s
                              AND startacademicyear=%s AND yearlevel=%s
                            LIMIT 1
                        """, (offering_code, ay_id, _start_ay_str, yl))
                        _pylr = cur.fetchone()
                        _pyl_id = _pylr['programyearlevelid'] if _pylr else None

                    if not _pyl_id:
                        try:
                            cur.execute("SAVEPOINT pyl_create")
                            cur.execute("""
                                INSERT INTO program_yearlevel
                                    (programcode, academicyearid, startacademicyear, yearlevel, curriculumid, isactive)
                                VALUES (%s, %s, %s, %s, %s, TRUE)
                                ON CONFLICT (programcode, academicyearid, startacademicyear, yearlevel)
                                    DO UPDATE SET curriculumid = EXCLUDED.curriculumid, isactive = TRUE
                                RETURNING programyearlevelid
                            """, (offering_code.upper(), ay_id, _start_ay_str or '', yl, best_curr_id))
                            _pylr2 = cur.fetchone()
                            cur.execute("RELEASE SAVEPOINT pyl_create")
                            if _pylr2: _pyl_id = _pylr2['programyearlevelid']
                        except Exception:
                            cur.execute("ROLLBACK TO SAVEPOINT pyl_create")
                            cur.execute("""
                                SELECT programyearlevelid FROM program_yearlevel
                                WHERE UPPER(programcode)=UPPER(%s) AND academicyearid=%s
                                  AND yearlevel=%s LIMIT 1
                            """, (offering_code, ay_id, yl))
                            _pylr3 = cur.fetchone()
                            if _pylr3: _pyl_id = _pylr3['programyearlevelid']

                # ── cs_id: look up subject in the program_yearlevel's curriculum ──
                cs_id = None
                if s_code and best_curr_id:
                    cur.execute("""
                        SELECT curriculumsubjectid FROM curriculumsubject
                        WHERE curriculumid=%s AND UPPER(subjectcode)=UPPER(%s) LIMIT 1
                    """, (best_curr_id, s_code))
                    r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None

                # ── Find or create section under that program_yearlevel ──
                # Section name comes from the SIS Course column (e.g. "BSIT-2-A").
                # Only fall back to a generic name if the course field is empty.
                _course_raw = str(row.get('course', '') or '').strip()
                _sec_nm = (_course_raw if _course_raw else f"{offering_code.upper()}-{yl}A")[:10]

                sec_id = None
                if _pyl_id:
                    # Look for the specific section by name first
                    cur.execute("""
                        SELECT sectionid FROM sections
                        WHERE programyearlevelid=%s AND UPPER(sectionname)=UPPER(%s) LIMIT 1
                    """, (_pyl_id, _sec_nm))
                    _sr = cur.fetchone(); sec_id = _sr['sectionid'] if _sr else None

                    if not sec_id:
                        # Section doesn't exist yet — create it from the Course column value
                        try:
                            cur.execute("SAVEPOINT sec_create")
                            cur.execute("""
                                INSERT INTO sections (programyearlevelid, sectionname, isactive)
                                VALUES (%s, %s, TRUE)
                                ON CONFLICT (programyearlevelid, sectionname) DO NOTHING
                                RETURNING sectionid
                            """, (_pyl_id, _sec_nm))
                            _sr2 = cur.fetchone()
                            cur.execute("RELEASE SAVEPOINT sec_create")
                            if _sr2: created_sec += 1; sec_id = _sr2['sectionid']
                        except Exception:
                            cur.execute("ROLLBACK TO SAVEPOINT sec_create")
                        if not sec_id:
                            cur.execute("""
                                SELECT sectionid FROM sections
                                WHERE programyearlevelid=%s AND UPPER(sectionname)=UPPER(%s) LIMIT 1
                            """, (_pyl_id, _sec_nm))
                            _sr3 = cur.fetchone()
                            if _sr3: sec_id = _sr3['sectionid']

                # ── Auto-create curriculum entry if cs_id still not found ──
                if not cs_id and s_code and best_curr_id:
                    _sem_char = 'B' if ('2' in str(sem_type or '') or str(sem_type or '').upper() == 'B') else \
                                'C' if ('SUM' in str(sem_type or '').upper() or str(sem_type or '').upper() == 'C') else 'A'
                    cur.execute("""
                        INSERT INTO curriculumsubject (curriculumid, subjectcode, subjectname, creditunits, lecturehours, laboratoryhours, tuitionhours, yearlevel, semester)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT ON CONSTRAINT uq_curriculumsubject DO UPDATE SET
                            subjectname = EXCLUDED.subjectname,
                            creditunits = EXCLUDED.creditunits,
                            lecturehours = EXCLUDED.lecturehours,
                            laboratoryhours = EXCLUDED.laboratoryhours,
                            tuitionhours = EXCLUDED.tuitionhours
                        RETURNING curriculumsubjectid
                    """, (best_curr_id, s_code.upper(), subj_nm or s_code, unit, lec, lab, lec + lab, yl, _sem_char))
                    _cs_r = cur.fetchone()
                    if _cs_r:
                        created_subj += 1
                    else:
                        cur.execute("""
                            SELECT curriculumsubjectid FROM curriculumsubject
                            WHERE curriculumid=%s AND UPPER(subjectcode)=UPPER(%s) LIMIT 1
                        """, (best_curr_id, s_code))
                        _cs_r = cur.fetchone()
                    if _cs_r: cs_id = _cs_r['curriculumsubjectid']

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
                            INSERT INTO schedule_version (scheduleid, version_number, status, source, original_status)
                            VALUES (%s, 1, 'Published', 'import', 'Published') RETURNING versionid
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
                            _sr_raw = _er_raw = ''
                            if ts and '-' in ts:
                                pp = re.split(r'\s*-\s*', ts, maxsplit=1)
                                if len(pp) == 2:
                                    _sr_raw, _er_raw = pp[0].strip(), pp[1].strip()
                                    em = re.search(r'(AM|PM)\s*$', _er_raw, re.IGNORECASE)
                                    sm = re.search(r'(AM|PM)', _sr_raw, re.IGNORECASE)
                                    ss2 = _pts(_sr_raw, force_pm=bool(em and em.group(1).upper()=='PM' and not sm))
                                    es2 = _pts(_er_raw)
                            si2 = ei2 = None
                            if ss2:
                                cur.execute("SELECT timeid FROM timeslot WHERE timevalue=%s::time", (ss2,))
                                _x = cur.fetchone(); si2 = _x['timeid'] if _x else None
                            if es2:
                                cur.execute("SELECT timeid FROM timeslot WHERE timevalue=%s::time", (es2,))
                                _x = cur.fetchone(); ei2 = _x['timeid'] if _x else None
                            # Recovery 1: if end <= start, try adding 12h to end (unmarked PM end)
                            if si2 and ei2 and ei2 <= si2 and es2:
                                m2 = re.match(r'(\d{2}):(\d{2}):00', es2)
                                if m2:
                                    eh2 = int(m2.group(1))
                                    if eh2 < 12:
                                        alt = f"{eh2+12:02d}:{m2.group(2)}:00"
                                        cur.execute("SELECT timeid FROM timeslot WHERE timevalue=%s::time", (alt,))
                                        _x = cur.fetchone()
                                        if _x: ei2 = _x['timeid']
                            # Recovery 2: if still inverted, the force_pm on start was wrong —
                            # retry start WITHOUT force_pm (e.g. "7:00 - 12:00 PM" → 7:00 AM)
                            if si2 and ei2 and ei2 <= si2 and _sr_raw:
                                ss2_retry = _pts(_sr_raw, force_pm=False)
                                if ss2_retry:
                                    cur.execute("SELECT timeid FROM timeslot WHERE timevalue=%s::time", (ss2_retry,))
                                    _x = cur.fetchone()
                                    if _x and _x['timeid'] < ei2:
                                        si2 = _x['timeid']
                            # Final guard: skip session if still inverted (never crash on constraint)
                            if si2 and ei2 and ei2 <= si2:
                                si2 = ei2 = None
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

                        if si_cnt == 0 and (days_raw or time_raw) and not is_cur:
                            # Past semester only: no timeslot match → archive to historical
                            nr3 = _nr(room_raw) if room_raw else ''
                            _insert_historical(cur, inst, s_code, subj_nm, prog, yl, days_raw, time_raw, nr3, sem_id, ay_id, lec, lab, unit, hrs)
                            saved_h += 1

                        ins_cur = True; saved_c += 1

            if not ins_cur:
                if is_cur:
                    # Build a human-readable reason for the skip
                    _reasons = []
                    if not best_curr_id:
                        _reasons.append('no curriculum found for program')
                    elif not cs_id:
                        _reasons.append('subject not in curriculum')
                    if not _pyl_id:
                        _reasons.append('program year level not set up')
                    elif not sec_id:
                        _reasons.append('no section found')
                    _reason_str = '; '.join(_reasons) if _reasons else 'unresolved'
                    print(f"[SIS skip] prog={offering_code!r} subj={s_code!r} yl={yl} reason={_reason_str}")
                    saved_skip += 1
                    skipped_details.append({
                        'program': offering_code or prog or '—',
                        'subject': s_code or '—',
                        'yearlevel': yl,
                        'reason': _reason_str,
                    })
                else:
                    nr3 = _nr(room_raw) if room_raw else ''
                    _insert_historical(cur, inst, s_code, subj_nm, prog, yl, days_raw, time_raw, nr3, sem_id, ay_id, lec, lab, unit, hrs)
                    saved_h += 1

        conn.commit()
        msg = f'{saved_c} row(s) imported to schedule.'
        if created_subj:
            msg += f' {created_subj} new subject(s) auto-added to curriculum.'
        if created_sec:
            msg += f' {created_sec} new section(s) auto-created.'
        if saved_skip:
            msg += f' {saved_skip} row(s) skipped (no valid program code or subject code in row).'
        if saved_h:
            msg += f' {saved_h} row(s) archived to historical data (past semester).'
        return jsonify({'success': True, 'message': msg, 'saved_current': saved_c, 'saved_historical': saved_h, 'saved_skipped': saved_skip, 'skipped_details': skipped_details})

    except Exception as e:
        conn.rollback()
        _yl_name = {1:'First',2:'Second',3:'Third',4:'Fourth',5:'Fifth'}.get(yl, str(yl))
        _ctx = f"Program: {prog or '(unknown)'}, {_yl_name} Year, Subject: {s_code or '(unknown)'}"
        print(f'SIS Import Confirm Error [{_ctx}]: {e}')
        return jsonify({'error': f'{_ctx} — {str(e)}'}), 500
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
    # Normalise en-dash (–) and em-dash (—) to regular hyphen so all variants
    # of codes like "DOMT–MOM" or "BSBA—FM" are matched correctly.
    upr = _re.sub(r'[–—]', '-', upr)

    # 1. Parenthesised code:  "BACHELOR OF SCIENCE IN IT (BSIT)"
    m = _re.search(r'\(([A-Z][A-Z0-9\-/\s]{1,20})\)', upr)
    if m:
        ext = _re.sub(r'\s*[-–—]\s*', '-', m.group(1).strip())
        if ext in _SIS_PROG_NORM: return _SIS_PROG_NORM[ext]
        if ext in _SIS_VALID_PROGS: return ext
        f = ext.split('/')[0].strip()
        if f in _SIS_PROG_NORM: return _SIS_PROG_NORM[f]
        if f in _SIS_VALID_PROGS: return f

    # 2. Direct code (normalise hyphen spacing; upr already has en/em dashes → '-')
    norm = _re.sub(r'\s*[-–—]\s*', '-', upr)
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


# ── Signatory / approval section detection ───────────────────────────────────
# Any row whose joined text contains one of these phrases belongs to the
# signatory/footer block, not to the schedule proper.  Detection causes the
# parser to stop reading rows immediately.
_SIGNATORY_STOP_KW = (
    'PREPARED AND SUBMITTED BY', 'RECOMMENDING APPROVAL',
    'APPROVED AS RECOMMENDED', 'APPROVED BY', 'PREPARED BY',
    'NOTED BY', 'CERTIFIED CORRECT', 'CAMPUS DIRECTOR',
    'VICE PRESIDENT', 'HEAD, ACADEMIC PROGRAMS',
    'ASSOCIATE PROFESSOR', 'ASSISTANT PROFESSOR',
)

def _is_signatory_section(full_u, non_empty_count=None):
    """Return True when a row/line marks the start of a signatory/footer block."""
    import re as _re
    if any(kw in full_u for kw in _SIGNATORY_STOP_KW):
        return True
    # Standalone position titles only trigger when the row has very few cells
    # (avoids false positives in institutional descriptions like "Dean's Office").
    if _re.search(r'\bDEAN\b', full_u) or _re.search(r'\bREGISTRAR\b', full_u):
        if non_empty_count is None or non_empty_count <= 3:
            return True
    return False


def _is_signatory_code(s_code):
    """Return True if s_code looks like a signatory label rather than a subject code."""
    import re as _re
    u = s_code.upper()
    if ',' in s_code:
        return True
    if any(kw in u for kw in _SIGNATORY_STOP_KW):
        return True
    if _re.search(r'\b(DEAN|REGISTRAR)\b', u):
        return True
    return False


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
    import re as _re_pp

    # Load DB program codes; build a stripped-code map so spacing/hyphens are ignored
    # e.g. "BSBIO AT", "BSBIO-AT", "BSBIOAT" all resolve to the DB canonical code.
    _db_prog_rows = query_db("SELECT UPPER(programcode) AS programcode FROM programs WHERE isactive = TRUE")
    _db_valid = frozenset(r['programcode'] for r in (_db_prog_rows or []))
    _db_stripped = {}   # stripped (no spaces/hyphens) → canonical DB code
    for _c in _db_valid:
        _db_stripped.setdefault(_re_pp.sub(r'[-\s]', '', _c), _c)

    def _resolve_prog(raw_prog):
        if not raw_prog: return ''
        norm = _re_pp.sub(r'\s*[-–—]\s*', '-', raw_prog.strip().upper())
        # 1. Exact DB match (hyphen-unified)
        if norm in _db_valid: return norm
        # 2. Stripped match: ignores all spaces+hyphens so "BPA PFM"/"BPA-PFM"/"BPAPFM" all hit "BPA-PFM"
        # Must come BEFORE _sis_norm_prog to avoid broad keyword matches ("PUBLIC ADMINISTRATION"→"BPA")
        stripped = _re_pp.sub(r'[-\s]', '', norm)
        db_hit = _db_stripped.get(stripped, '')
        if db_hit: return db_hit
        # 3. Standard normaliser (handles known aliases and full programme names)
        return _sis_norm_prog(raw_prog)

    # Normalise + whitelist filter
    filtered = []
    for r in rows_out:
        if not (r.get('subj_code') or '').strip(): continue
        prog = _resolve_prog(r.get('program', '') or '')
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
            f = float(str(v).strip())
            return int(f) if f == int(f) else f
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
        prog_raw = _g('Program', 'program', 'Programme', 'programme',
                      'ProgramCode', 'programcode')
        prog     = re.sub(r'\s+\d+$', '', prog_raw).strip()
        yl_raw   = _g('YearLevel', 'Year Level', 'yearlevel', 'year_level',
                      'YrLevel', 'yrlevel')
        _yl      = _si(yl_raw)   # 0 = not provided in dedicated column
        course   = _g('Section', 'section', 'Course', 'course')

        # Fallback: extract program / year level from Course column when their
        # dedicated columns are absent — e.g. "BSIT 2", "DCPET 3", "DIT 1"
        if (not prog or not _yl) and course:
            import re as _rcc
            _mc = _rcc.match(
                r'^([A-Z][A-Z0-9\-]{1,15})\s+([1-5])(?:[^0-9]|$)',
                course.strip().upper()
            )
            if _mc:
                if not prog:
                    prog = _mc.group(1)
                if not _yl:
                    _yl = int(_mc.group(2))

        yl = max(1, min(_yl or 1, 5)) if (_yl or 1) <= 32767 else 1

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
            'course':       course,
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
    current_prog    = ''
    current_yl      = 1
    col_map         = {}
    signatory_found = False

    for block in doc.element.body:
        if signatory_found: break
        tag = block.tag.split('}')[-1] if '}' in block.tag else block.tag

        if tag == 'p':
            from docx.text.paragraph import Paragraph
            para = Paragraph(block, doc)
            txt  = para.text.strip()
            upr  = txt.upper()
            if not txt: continue
            # Stop at signatory/approval paragraphs
            if _is_signatory_section(upr):
                signatory_found = True; break
            # Year level
            yl_found = False
            for w, n in YEAR_MAP.items():
                if w + ' YEAR' in upr: current_yl = n; yl_found = True; break
            if yl_found: continue
            # Program header (bold short text) — skip if it looks like a signatory label
            is_bold = any(r.bold for r in para.runs if r.text.strip())
            if is_bold and len(txt) < 80 and not any(kw in upr for kw in TOTAL_KW):
                if not _is_signatory_section(upr):
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
                if signatory_found: break
                vals   = [_cell_txt(c) for c in row.cells]
                full_u = ' '.join(vals).upper()
                ne_cnt = sum(1 for v in vals if v)
                if not any(vals): continue
                # Stop at signatory rows
                if _is_signatory_section(full_u, ne_cnt):
                    signatory_found = True; break
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
                # Program header (all-same merged row) — only when not signatory text
                if len(set(v for v in vals if v)) == 1 and not _is_signatory_section(full_u, ne_cnt):
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
                if _is_signatory_code(s_code): continue
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

    rows_out        = []
    current_prog    = ''
    current_yl      = 1
    col_map         = {}
    signatory_found = False

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            if signatory_found: break
            # Check page text for year-level headers between tables.
            # Normalise en/em dashes so "THIRD YEAR – MEDICAL" is still detected.
            page_text = re.sub(r'[–—]', '-', page.extract_text() or '')
            for line in page_text.split('\n'):
                lu = line.strip().upper()
                if _is_signatory_section(lu):
                    signatory_found = True; break
                for w, n in YEAR_MAP.items():
                    if w + ' YEAR' in lu: current_yl = n; break
            if signatory_found: break

            tables = page.extract_tables({'vertical_strategy': 'lines', 'horizontal_strategy': 'lines'})
            if not tables:
                tables = page.extract_tables({'vertical_strategy': 'text', 'horizontal_strategy': 'text'})
            if not tables:
                continue

            for table in tables:
                if signatory_found: break
                if not table: continue
                tbl_col_map = dict(col_map)

                for row in table:
                    if signatory_found: break
                    if not row: continue
                    vals = [str(c).strip() if c is not None else '' for c in row]
                    # OCR normalisation: collapse multiple spaces, normalise dash variants
                    vals = [re.sub(r'\s+', ' ', re.sub(r'[–—]', '-', v)) for v in vals]
                    vals = [v for v in vals if v.lower() not in ('none',)]
                    # Pad to at least 11 cols
                    while len(vals) < 11: vals.append('')
                    non_empty = [v for v in vals if v]
                    if not non_empty: continue
                    full_u = ' '.join(non_empty).upper()

                    # Stop at signatory / approval rows
                    if _is_signatory_section(full_u, len(non_empty)):
                        signatory_found = True; break

                    # Year level
                    yl_found = False
                    for w, n in YEAR_MAP.items():
                        if w + ' YEAR' in full_u: current_yl = n; yl_found = True; break
                    if yl_found: continue

                    # Skip totals (continue, not break — more year blocks may follow)
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
                    # Guard: never treat signatory text as a programme header —
                    # that would corrupt current_prog and col_map for later rows.
                    v0   = vals[0].strip() if vals else ''
                    v0u  = v0.upper()
                    is_few = len(non_empty) <= 3 and v0
                    is_hdr = bool(v0 and
                                  (v0u.startswith('BACHELOR') or v0u.startswith('DIPLOMA')) and
                                  re.search(r'\([A-Z][A-Z0-9\-/]{1,20}\)', v0u))
                    if (is_few or is_hdr) and \
                            not any(w + ' YEAR' in full_u for w in YEAR_MAP) and \
                            not any(kw in full_u for kw in HDR_KW) and \
                            not _is_signatory_section(full_u, len(non_empty)):
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
                    if _is_signatory_code(s_code): continue

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
            f = float(str(v).strip())
            return int(f) if f == int(f) else f
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
                    WHERE UPPER(c.programcode)=UPPER(%s) AND UPPER(cs.subjectcode)=UPPER(%s) LIMIT 1
                """, (prog, s_code))
                r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None
            if not cs_id:
                cur.execute("SELECT curriculumsubjectid FROM curriculumsubject WHERE UPPER(subjectcode)=UPPER(%s) LIMIT 1", (s_code,))
                r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None
        if not cs_id:
            if status != 'blocked': status = 'warning'
            flags.append(f'Subject "{s_code}" not in curriculum → will be auto-added on import')

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

        # Section — look up by exact course name first, then any active section;
        # always a warning (auto-create on confirm), never a hard block.
        course_name = (item.get('course') or '').strip()
        sec_id, sec_name = None, ''
        if prog and yl:
            # Pass 1: exact match on the course column value (section name as written in the file)
            if course_name:
                cur.execute("""
                    SELECT sec.sectionid, sec.sectionname FROM sections sec
                    JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                    WHERE UPPER(pyl.programcode)=UPPER(%s) AND pyl.yearlevel=%s
                      AND UPPER(sec.sectionname)=UPPER(%s) AND sec.isactive=TRUE
                    LIMIT 1
                """, (prog, yl, course_name))
                r = cur.fetchone()
                sec_id   = r['sectionid']   if r else None
                sec_name = r['sectionname'] if r else ''
            # Pass 2: any active section for that program + year
            if not sec_id:
                cur.execute("""
                    SELECT sec.sectionid, sec.sectionname FROM sections sec
                    JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                    WHERE UPPER(pyl.programcode)=UPPER(%s) AND pyl.yearlevel=%s AND sec.isactive=TRUE
                    ORDER BY sec.sectionname LIMIT 1
                """, (prog, yl))
                r = cur.fetchone()
                sec_id   = r['sectionid']   if r else None
                sec_name = r['sectionname'] if r else ''
            # Pass 3: include inactive sections as last resort
            if not sec_id:
                cur.execute("""
                    SELECT sec.sectionid, sec.sectionname FROM sections sec
                    JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                    WHERE UPPER(pyl.programcode)=UPPER(%s) AND pyl.yearlevel=%s
                    ORDER BY sec.sectionname LIMIT 1
                """, (prog, yl))
                r = cur.fetchone()
                sec_id   = r['sectionid']   if r else None
                sec_name = r['sectionname'] if r else ''
        if not sec_id:
            # Section will be auto-created during confirm — always a warning, never blocked
            if status != 'blocked': status = 'warning'
            _sec_label = course_name or f'{prog} {yl}' if prog else '—'
            flags.append(f'Section "{_sec_label}" not found → will be auto-created on import')

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
        cur.execute("""
            SELECT ARRAY(
                SELECT semesterid FROM semester
                WHERE academicyearid = %s AND semestertype = %s
            )
        """, (ay_id, sem_type))
        sem_ids = cur.fetchone()[0] or []
        if not sem_ids:
            return jsonify({'exists': False, 'count': 0})

        cur.execute(
            "SELECT COUNT(*) FROM schedule WHERE semesterid = ANY(%s)",
            (sem_ids,)
        )
        sched_count = cur.fetchone()[0]

        return jsonify({'exists': sched_count > 0, 'count': sched_count})
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
        'ay_id':        data.get('ay_id', ''),        # for program_yearlevel lookup
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

    init_mode         = request.args.get('mode', 'subject')
    init_prog         = request.args.get('prog', '')
    init_yl           = request.args.get('yl', '')
    init_ay           = request.args.get('ay', '')
    init_sem          = request.args.get('sem', '')
    init_sect         = request.args.get('sect', '')
    init_sect_name    = request.args.get('sect_name', '')
    init_from_generator = request.args.get('from_generator', '')

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
        merge_enabled           = bool(_sched_cfg.get('hc_merge_enabled', 1))
        merge_scope             = _sched_cfg.get('hc_merge_scope', 'nstp_only')
        day_pairing_enabled     = bool(_sched_cfg.get('hc_day_pairing_enabled', 1))
        _raw_pairs = _sched_cfg.get('hc_day_pairs', '')
        if _raw_pairs:
            try:
                _parsed = json.loads(_raw_pairs) if isinstance(_raw_pairs, str) else _raw_pairs
                day_pairs_json = json.dumps([list(p) for p in _parsed if len(p) == 2])
            except Exception:
                day_pairs_json = json.dumps([["Monday","Thursday"],["Tuesday","Friday"],["Wednesday","Saturday"]])
        else:
            day_pairs_json = json.dumps([["Monday","Thursday"],["Tuesday","Friday"],["Wednesday","Saturday"]])

        return render_template('academic/manualScheduleEditor.html',
                               scheduler_mode=scheduler_mode,
                               is_acad_head=is_acad_head,
                               acad_years=acad_years, programs=programs,
                               faculty=faculty, faculty_json=faculty_json,
                               buildings=buildings, rooms_json=json.dumps(rooms_list),
                               available_sems_json=available_sems_json,
                               init_mode=init_mode, init_prog=init_prog,
                               init_yl=init_yl, init_ay=init_ay, init_sem=init_sem,
                               init_sect=init_sect, init_sect_name=init_sect_name,
                               init_from_generator=init_from_generator,
                               lab_constraint_enabled=lab_constraint_enabled,
                               weekend_enabled=weekend_enabled,
                               weekend_day=weekend_day,
                               weekend_subject=weekend_subject,
                               spec_constraint_enabled=spec_constraint_enabled,
                               merge_enabled=merge_enabled,
                               merge_scope=merge_scope,
                               day_pairing_enabled=day_pairing_enabled,
                               day_pairs_json=day_pairs_json)
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
            WHERE sv.status = 'Published'
              AND UPPER(c.programcode) = UPPER(%s)
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
            LEFT JOIN LATERAL (
                SELECT subjectname FROM curriculumsubject
                WHERE UPPER(subjectcode) = UPPER(las.subjectcode)
                LIMIT 1
            ) subj ON TRUE
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
            WHERE  sv.status = 'Published'
              AND  UPPER(c.programcode) = UPPER(%s)
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
            WHERE  sv.status = 'Published'
              AND  UPPER(c.programcode) = UPPER(%s)
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
                    cs.subjectname,
                    ss.daydesc,
                    TO_CHAR(ts_s.timevalue,'HH12:MI AM')           AS start_fmt,
                    TO_CHAR(ts_e.timevalue,'HH12:MI AM')           AS end_fmt,
                    r.roomname,
                    COALESCE(f.lastname||', '||f.firstname,'—')    AS instructor,
                    UPPER(COALESCE(pyl.programcode,''))             AS programcode,
                    pyl.yearlevel
                FROM schedule_sessions ss
                JOIN schedule_version sv ON ss.versionid = sv.versionid AND sv.status = 'Published'
                JOIN schedule s          ON sv.scheduleid = s.scheduleid AND s.semesterid = %s
                JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
                LEFT JOIN sections sec      ON s.sectionid = sec.sectionid
                LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
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
                        AND UPPER(la.programcode) = UPPER(COALESCE(c.programcode,''))
                        AND la.yearlevel  = pyl.yearlevel
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM public.local_displaced_subjects lds
                      WHERE UPPER(lds.subjectcode) = UPPER(cs.subjectcode)
                        AND lds.semesterid  = s.semesterid
                        AND UPPER(lds.programcode) = UPPER(COALESCE(c.programcode,''))
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
            JOIN room r ON ss.roomid = r.roomid"""

        if scheduler_mode == 'local':
            filters.append("""
                NOT EXISTS (
                    SELECT 1 FROM public.local_arrangement_sessions las
                    JOIN public.local_arrangement la ON las.arrangementid = la.arrangementid
                    WHERE UPPER(las.subjectcode) = UPPER(cs.subjectcode)
                      AND la.is_active = TRUE
                      AND la.semesterid = s.semesterid
                      AND UPPER(la.programcode) = UPPER(COALESCE(pyl.programcode, ''))
                      AND la.yearlevel = pyl.yearlevel
                )
            """)
            filters.append("""
                NOT EXISTS (
                    SELECT 1 FROM public.local_displaced_subjects lds
                    WHERE UPPER(lds.subjectcode) = UPPER(cs.subjectcode)
                      AND lds.semesterid = s.semesterid
                      AND UPPER(lds.programcode) = UPPER(COALESCE(pyl.programcode, ''))
                      AND lds.yearlevel = pyl.yearlevel
                      AND lds.is_active = TRUE
                )
            """)

        # When ay_id is explicitly provided the user already scoped to that academic year,
        # so there is no need to filter by semester end date.  When browsing by room only
        # (no ay_id), limit to semesters that ended within the last 12 months so very old
        # data stays out of a single-room weekly view while recently-ended semesters still show.
        if not ay_id:
            filters.append("(sem.semenddate IS NULL OR sem.semenddate >= (CURRENT_DATE - INTERVAL '12 months'))")

        if ay_id:
            joins += " JOIN academicyear ay ON sem.academicyearid = ay.academicyearid"
            filters.append("ay.academicyearid = %s")
            params.append(ay_id)
        if semester:
            filters.append("sem.semestertype = %s")
            params.append(semester)
        if program:
            filters.append("pyl.programcode = %s")
            params.append(program)

        main_q = f"""
            SELECT
                cs.subjectcode,
                cs.subjectname,
                f.employeenumber AS employee_number,
                f.lastname || ', ' || f.firstname AS instructor,
                ss.daydesc,
                ss.starttimeid,
                ss.endtimeid,
                pyl.yearlevel AS year_level,
                pyl.programcode,
                sec.sectionname,
                sec.sectionid AS section_id,
                r.roomname,
                sv.status,
                sv.versionid
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule s ON sv.scheduleid = s.scheduleid
            {joins}
            JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
            JOIN faculty f ON s.employeenumber = f.employeenumber
            WHERE {' AND '.join(filters)}
        """
        all_params = list(params)

        if scheduler_mode == 'local':
            la_filters = ["las.roomid = %s", "la.is_active = TRUE", "la.status = 'Published'"]
            la_params  = [room_id]
            la_joins   = "JOIN semester sem2 ON la.semesterid = sem2.semesterid"
            if not ay_id:
                la_filters.append("(sem2.semenddate IS NULL OR sem2.semenddate >= (CURRENT_DATE - INTERVAL '12 months'))")
            if ay_id:
                la_joins += " JOIN academicyear ay2 ON sem2.academicyearid = ay2.academicyearid"
                la_filters.append("ay2.academicyearid = %s")
                la_params.append(ay_id)
            if semester:
                la_filters.append("sem2.semestertype = %s")
                la_params.append(semester)
            if program:
                la_filters.append("UPPER(la.programcode) = UPPER(%s)")
                la_params.append(program)

            la_q = f"""
                SELECT
                    las.subjectcode,
                    COALESCE(cs_la.subjectname, las.subjectcode) AS subjectname,
                    las.faculty_employeenumber AS employee_number,
                    COALESCE(f_la.lastname || ', ' || f_la.firstname, 'TBA') AS instructor,
                    las.daydesc,
                    las.starttimeid,
                    las.endtimeid,
                    la.yearlevel AS year_level,
                    la.programcode,
                    NULL AS sectionname,
                    NULL AS section_id,
                    r_la.roomname,
                    'Local' AS status,
                    NULL AS versionid
                FROM public.local_arrangement_sessions las
                JOIN public.local_arrangement la ON las.arrangementid = la.arrangementid
                {la_joins}
                LEFT JOIN LATERAL (
                    SELECT subjectname FROM curriculumsubject
                    WHERE UPPER(subjectcode) = UPPER(las.subjectcode) LIMIT 1
                ) cs_la ON TRUE
                LEFT JOIN faculty f_la ON las.faculty_employeenumber = f_la.employeenumber
                LEFT JOIN room r_la ON las.roomid = r_la.roomid
                WHERE {' AND '.join(la_filters)}
            """
            all_params += la_params
            cur.execute(main_q + " UNION ALL " + la_q, all_params)
        else:
            cur.execute(main_q, all_params)

        rows = [dict(r) for r in (cur.fetchall() or [])]

        # Draft is the definitive replacement for Published for the same class — suppress
        # old Published slots once a Draft exists for that (subject, section), the same rule
        # /api/manual/existing_sessions already applies. Without this, moving an existing
        # Published session's day/time and saving as Draft leaves the old Published slot
        # visible in the room calendar alongside the new Draft slot, looking like two classes.
        _groups: dict = {}
        _ungrouped = []
        for r in rows:
            if r.get('status') in ('Published', 'Draft') and r.get('section_id'):
                _groups.setdefault((r['subjectcode'], r['section_id']), []).append(r)
            else:
                _ungrouped.append(r)

        result_rows = list(_ungrouped)
        for (_subj, _sect), grp in _groups.items():
            draft = [r for r in grp if r['status'] == 'Draft']
            pub   = [r for r in grp if r['status'] == 'Published']
            if not draft:
                result_rows.extend(pub)
                continue
            pub_slot_keys = {(r['daydesc'], r['starttimeid']) for r in pub}
            for r in draft:
                if (r['daydesc'], r['starttimeid']) in pub_slot_keys:
                    r['status'] = 'Published'
                result_rows.append(r)

        return jsonify(result_rows)
    except Exception as e:
        print(f"Error fetching room schedule: {e}")
        return jsonify([])
    finally:
        cur.close(); conn.close()


@app.route('/api/rooms_occupancy_by_day')
def api_rooms_occupancy_by_day():
    """
    Bulk occupancy lookup for the Manual Editor's Room dropdown: for a given day
    (+ optional ay/semester), returns each occupied room's booked time-index ranges
    in one query, instead of one /api/get_room_schedule call per room. Mirrors the
    Published+Draft status filter get_room_schedule already uses so the dropdown stays
    consistent with the occupancy dimming already applied to start-time options.
    """
    if 'loggedin' not in session: return jsonify({})
    day      = request.args.get('day', '')
    ay_id    = request.args.get('ay_id', '')
    semester = request.args.get('semester', '')
    if not day:
        return jsonify({})
    try:
        filters = ["ss.daydesc = %s", "sv.status IN ('Published', 'Draft')"]
        params  = [day]
        joins   = " JOIN semester sem ON s.semesterid = sem.semesterid"

        if ay_id:
            joins += " JOIN academicyear ay ON sem.academicyearid = ay.academicyearid"
            filters.append("ay.academicyearid = %s")
            params.append(ay_id)
        if semester:
            filters.append("sem.semestertype = %s")
            params.append(semester)

        rows = query_db(f"""
            SELECT ss.roomid, ss.starttimeid, ss.endtimeid
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule s ON sv.scheduleid = s.scheduleid
            {joins}
            WHERE {' AND '.join(filters)} AND ss.roomid IS NOT NULL
        """, tuple(params))

        occupancy = {}
        for r in (rows or []):
            occupancy.setdefault(str(r['roomid']), []).append({
                'startIdx': (r['starttimeid'] or 1) - 1,
                'endIdx':   (r['endtimeid']   or 1) - 1,
            })
        return jsonify(occupancy)
    except Exception as e:
        print(f"Error fetching rooms occupancy by day: {e}")
        return jsonify({})


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
        filters.append("UPPER(pyl.programcode) = UPPER(%s)")
        params.append(program)

    if year_level:
        filters.append("pyl.yearlevel = %s")
        params.append(int(year_level))

    try:
        rows = query_db(f"""
            SELECT cs.subjectcode, cs.subjectname, cs.creditunits,
                   sec.sectionname, pyl.yearlevel, pyl.programcode, ss.daydesc, r.roomname,
                   ss.starttimeid, ss.endtimeid, sv.status,
                   f.lastname || ', ' || f.firstname AS instructor,
                   sc.employeenumber,
                   TO_CHAR(ts_s.timevalue, 'HH12:MI AM') || ' - ' ||
                   TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS time_range,
                   TO_CHAR(sem.semstartdate, 'MM/DD/YYYY') AS effectivity
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN room r ON ss.roomid = r.roomid
            LEFT JOIN faculty f ON sc.employeenumber = f.employeenumber
            LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
            LEFT JOIN semester sem ON sc.semesterid = sem.semesterid
            WHERE {' AND '.join(filters)}
            ORDER BY ts_s.timevalue, ss.daydesc
        """, params or None)
        return jsonify([dict(r) for r in (rows or [])])
    except Exception as e:
        print(f"[api_get_faculty_schedule] Error: {e}")
        return jsonify([]), 500


@app.route('/api/faculty/my_local_schedule')
def api_faculty_my_local_schedule():
    """Local arrangement sessions assigned to the logged-in user (Faculty or Academic Head)."""
    if 'loggedin' not in session or session.get('role') not in ('Faculty', 'Academic Head'):
        return jsonify([])

    emp_num = session.get('employeenumber')
    if not emp_num:
        acc = query_db("SELECT employeenumber FROM accounts WHERE username = %s",
                       [session.get('username')], one=True)
        emp_num = acc['employeenumber'] if acc else None
    if not emp_num:
        return jsonify([])

    ay_id    = request.args.get('ay_id')
    semester = request.args.get('semester')

    filters = [
        "la.is_active = TRUE",
        "la.status = 'Published'",
        "las.faculty_employeenumber = %s"
    ]
    params = [emp_num]

    if ay_id and semester:
        filters.append("""la.semesterid = (
            SELECT semesterid FROM semester
            WHERE academicyearid = %s AND semestertype = %s LIMIT 1
        )""")
        params += [ay_id, semester]

    try:
        _ensure_local_tables()
        rows = query_db(f"""
            SELECT las.subjectcode,
                   COALESCE(cs.subjectname, las.subjectcode) AS subjectname,
                   COALESCE(cs.creditunits, 0) AS creditunits,
                   las.daydesc,
                   r.roomname,
                   las.starttimeid, las.endtimeid,
                   TO_CHAR(ts_s.timevalue, 'HH12:MI AM') || ' - ' ||
                   TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS time_range,
                   la.programcode,
                   la.yearlevel,
                   'Published' AS status,
                   NULL AS effectivity,
                   NULL AS sectionname,
                   NULL AS instructor
            FROM public.local_arrangement_sessions las
            JOIN public.local_arrangement la ON las.arrangementid = la.arrangementid
            LEFT JOIN LATERAL (
                SELECT subjectname, creditunits FROM curriculumsubject
                WHERE UPPER(subjectcode) = UPPER(las.subjectcode)
                LIMIT 1
            ) cs ON TRUE
            LEFT JOIN room r ON las.roomid = r.roomid
            LEFT JOIN timeslot ts_s ON las.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON las.endtimeid   = ts_e.timeid
            WHERE {' AND '.join(filters)}
            ORDER BY ts_s.timevalue, las.daydesc
        """, params)
        return jsonify([dict(r) for r in (rows or [])])
    except Exception as e:
        print(f"[api_faculty_my_local_schedule] Error: {e}")
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
            SELECT cs.subjectcode, cs.subjectname, ss.daydesc, ss.starttimeid, ss.endtimeid, sv.status,
                   ts_s.timevalue AS starttimevalue, ts_e.timevalue AS endtimevalue,
                   r.roomname, sec.sectionname
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
            LEFT JOIN room r        ON ss.roomid      = r.roomid
            LEFT JOIN sections sec  ON sc.sectionid   = sec.sectionid
            WHERE {' AND '.join(filters)}
            ORDER BY ss.daydesc, ts_s.timevalue
        """, params)
        result = []
        for r in (rows or []):
            d = dict(r)
            d['starttime'] = format_time(d.get('starttimevalue'))
            d['endtime']   = format_time(d.get('endtimevalue'))
            result.append(d)
        return jsonify(result)
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

        if prog and ay_id and yl and yl.isdigit():
            # Primary: find curriculum via program_yearlevel for this program + AY + year level
            cur.execute("""
                SELECT c.curriculumid, c.curriculumyear, c.curriculumcode,
                       ARRAY(SELECT DISTINCT cs2.yearlevel FROM curriculumsubject cs2
                             WHERE cs2.curriculumid = c.curriculumid ORDER BY cs2.yearlevel) AS year_levels
                FROM program_yearlevel pyl
                JOIN curriculum c ON c.curriculumid = pyl.curriculumid
                WHERE UPPER(pyl.programcode) = UPPER(%s)
                  AND pyl.academicyearid = %s
                  AND pyl.yearlevel = %s
                LIMIT 1
            """, (prog, ay_id, int(yl)))
            res = cur.fetchone()

        # Fallback: compute the cohort's entry year from AY + year_level and find the
        # correct curriculum via cohort logic, matching program_yearlevel's own rule.
        if not res and prog:
            entry_start = None
            if ay_id and yl and yl.isdigit():
                cur.execute("SELECT yearstart FROM academicyear WHERE academicyearid = %s", (ay_id,))
                _ay = cur.fetchone()
                if _ay:
                    entry_start = int(_ay['yearstart']) - (int(yl) - 1)
            if entry_start is not None:
                cur.execute("""
                    SELECT c.curriculumid, c.curriculumyear, c.curriculumcode,
                           ARRAY(SELECT DISTINCT cs2.yearlevel FROM curriculumsubject cs2
                                 WHERE cs2.curriculumid = c.curriculumid ORDER BY cs2.yearlevel) AS year_levels
                    FROM curriculum c
                    WHERE UPPER(c.programcode) = UPPER(%s)
                      AND CAST(SUBSTRING(c.curriculumyear, 1, 4) AS INT) <= %s
                    ORDER BY c.curriculumyear DESC LIMIT 1
                """, (prog, entry_start))
                res = cur.fetchone()
            if not res:
                # Last resort: oldest available curriculum for the program — better than
                # returning the newest, which would be wrong for senior cohorts.
                cur.execute("""
                    SELECT c.curriculumid, c.curriculumyear, c.curriculumcode,
                           ARRAY(SELECT DISTINCT cs2.yearlevel FROM curriculumsubject cs2
                                 WHERE cs2.curriculumid = c.curriculumid ORDER BY cs2.yearlevel) AS year_levels
                    FROM curriculum c
                    WHERE UPPER(c.programcode) = UPPER(%s)
                    ORDER BY c.curriculumyear ASC LIMIT 1
                """, (prog,))
                res = cur.fetchone()

        if res:
            return jsonify({
                "success": True,
                "curriculum_id": res['curriculumid'],
                "curriculum_code": res['curriculumcode'],
                "curriculum_year": str(res['curriculumyear'] or ''),
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
                WHERE UPPER(pyl.programcode) = UPPER(%s) AND ay.academicyearid = %s
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
        # 1. Is this a lab subject? Fetch subject name for fallback matching.
        cur.execute("SELECT LaboratoryHours, SubjectName FROM curriculumsubject WHERE UPPER(SubjectCode) = UPPER(%s) LIMIT 1", (subject_code,))
        subj_row = cur.fetchone()
        is_lab   = bool(subj_row and subj_row.get('laboratoryhours') and subj_row['laboratoryhours'] > 0)
        subj_name = (subj_row.get('subjectname') or '').strip() if subj_row else ''

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
                   COALESCE(d.nightteachingservice, 0)   AS designation_night_service,
                   COALESCE(s.SpecializationName, '')    AS specializationname
            FROM Faculty f
            LEFT JOIN EmployeeType et  ON f.EmployeeTypeID = et.EmployeeTypeID
            LEFT JOIN Designation d    ON f.DesignationID  = d.DesignationID
            LEFT JOIN Specialization s ON f.SpecializationID = s.SpecializationID
            WHERE (f.EmployeeStatus IS NULL OR f.EmployeeStatus != 'Archive') ORDER BY f.LastName
        """)
        all_faculty = [dict(r) for r in cur.fetchall()]

        # 2b. Batch-fetch assigned units per faculty for this AY/semester
        fac_loads = {}
        if ay_id and sem:
            cur.execute("""
                SELECT d.employeenumber,
                       COALESCE(SUM(d.creditunits), 0) AS assigned_units
                FROM (
                    SELECT DISTINCT sc.employeenumber, cs.subjectcode, cs.creditunits
                    FROM schedule_version sv
                    JOIN schedule sc          ON sv.scheduleid = sc.scheduleid
                    JOIN curriculumsubject cs  ON sc.curriculumsubjectid = cs.curriculumsubjectid
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
                "id":             f['employeenumber'],
                "name":           f['fullname'].strip(),
                "typename":       f.get('typename', 'Regular'),
                "specialization": f.get('specializationname', ''),
            }
            if ay_id and sem:
                item["max_units"]      = max_u
                item["assigned_units"] = assigned
            item.update(extra)
            return item

        # 3a. Schedule-table frequency (most reliable — uses exact curriculumsubjectid FK,
        #     no subject code format mismatch possible).  Covers current + archived schedules.
        cur.execute("""
            SELECT sc.employeenumber, COUNT(DISTINCT sv.scheduleid) AS freq
            FROM schedule sc
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN schedule_version sv  ON sv.scheduleid = sc.scheduleid
            WHERE UPPER(cs.subjectcode) = UPPER(%s)
              AND sc.employeenumber IS NOT NULL
              AND sv.status IN ('Published', 'Archive', 'Draft')
            GROUP BY sc.employeenumber
            ORDER BY freq DESC
        """, (subject_code,))
        sched_fac_counts = {r['employeenumber']: int(r['freq']) for r in cur.fetchall()}

        # 3b. Historical_data frequency — code match first (normalized to strip spaces/hyphens/symbols).
        #     Group by (instructor, employeenumber) so the stored ID is returned directly
        #     when available; NULL employeenumber falls back to name-based matching in step 4b.
        cur.execute("""
            SELECT TRIM("Instructor") AS instructor, COUNT(*) AS freq,
                   MAX(employeenumber) AS employeenumber
            FROM historical_data
            WHERE REGEXP_REPLACE(UPPER(TRIM("Subject Code")), '[^A-Z0-9]', '', 'g') =
                  REGEXP_REPLACE(UPPER(TRIM(%s)), '[^A-Z0-9]', '', 'g')
              AND "Instructor" IS NOT NULL AND TRIM("Instructor") != ''
            GROUP BY TRIM("Instructor"), COALESCE(employeenumber, '')
            ORDER BY freq DESC
            LIMIT 200
        """, (subject_code,))
        hist_faculty = cur.fetchall()
        print(f'[DSS DEBUG] hist_faculty (code match, {len(hist_faculty)} rows): {[(r["instructor"], r["freq"]) for r in hist_faculty[:5]]}')

        # Fallback 1: exact subject-name match when the code produced 0 results.
        if not hist_faculty and subj_name:
            cur.execute("""
                SELECT TRIM("Instructor") AS instructor, COUNT(*) AS freq,
                       MAX(employeenumber) AS employeenumber
                FROM historical_data
                WHERE LOWER(TRIM("Subject Name")) = LOWER(TRIM(%s))
                  AND "Instructor" IS NOT NULL AND TRIM("Instructor") != ''
                GROUP BY TRIM("Instructor"), COALESCE(employeenumber, '')
                ORDER BY freq DESC
                LIMIT 200
            """, (subj_name,))
            hist_faculty = cur.fetchall()
            print(f'[DSS DEBUG] hist_faculty (name fallback, {len(hist_faculty)} rows): {[(r["instructor"], r["freq"]) for r in hist_faculty[:5]]}')

        # Fallback 2: when both exact lookups return nothing, try a partial subject-name
        # ILIKE match.  This bridges cases where "Subject Name" differs slightly (e.g.
        # "Computer Programming 2" vs "Computer Prog 2").
        if not hist_faculty and subj_name and len(subj_name) >= 6:
            # Use the first 6+ characters of the subject name as a wildcard prefix
            like_pat = subj_name[:max(6, len(subj_name) // 2)] + '%'
            cur.execute("""
                SELECT TRIM("Instructor") AS instructor, COUNT(*) AS freq,
                       MAX(employeenumber) AS employeenumber
                FROM historical_data
                WHERE LOWER(TRIM("Subject Name")) ILIKE %s
                  AND "Instructor" IS NOT NULL AND TRIM("Instructor") != ''
                GROUP BY TRIM("Instructor"), COALESCE(employeenumber, '')
                ORDER BY freq DESC
                LIMIT 200
            """, (like_pat.lower(),))
            hist_faculty = cur.fetchall()
            if hist_faculty:
                print(f'[DSS DEBUG] hist_faculty (name ILIKE fallback "{like_pat}", {len(hist_faculty)} rows): {[(r["instructor"], r["freq"]) for r in hist_faculty[:5]]}')

        # ── DEBUG: print what the two data sources found ────────────────────────
        print(f'[DSS DEBUG] subject_code={subject_code!r}')
        print(f'[DSS DEBUG] sched_fac_counts keys={list(sched_fac_counts.keys())[:20]}')
        print(f'[DSS DEBUG] all_faculty count={len(all_faculty)}')
        # ────────────────────────────────────────────────────────────────────────

        # 4. Build recommended list — schedule-table hits first (ID-based, no name guessing),
        #    then historical_data hits for anyone not yet captured.
        recommended_ids = set()
        recommended_faculty = []

        # 4a. From schedule tables — direct employee-number match
        fac_by_emp = {f['employeenumber']: f for f in all_faculty}
        for emp_num, freq in sched_fac_counts.items():
            f = fac_by_emp.get(emp_num)
            if f and emp_num not in recommended_ids:
                recommended_ids.add(emp_num)
                recommended_faculty.append(_fac_item(f, count=freq, _src='schedule'))
            elif not f:
                print(f'[DSS DEBUG] empnum {emp_num!r} in sched_fac_counts but NOT found in all_faculty (may be archived or status mismatch)')

        # 4b. From historical_data — ID-based when employeenumber is stored, name-based otherwise.
        #
        # For "LAST, FIRST [MIDDLE]" names we compare BOTH the last-name segment AND
        # the first name (full when both sides have it, initial otherwise).  This prevents
        # two problems:
        #   (a) "SANTOS, MARIA" incorrectly matching Faculty "SANTOS, JOSE" → wrong person
        #       shown in recommendations.
        #   (b) "SANTOS, JOSE" and "SANTOS, MARIA" both collapsing to the first Santos
        #       found in all_faculty → only one of the two historically-teaching faculty
        #       ever appears in recommendations.
        # For no-comma names (ambiguous format) we keep the original last-name heuristics
        # since we cannot reliably extract first vs last in that case.
        # Academic titles that may appear as the first token of a name's first-name part.
        # When a historical record says "DELA CRUZ, DR. JUAN", hist_fi becomes "dr" instead
        # of "juan" — stripping these tokens lets us reach the actual first name.
        _NAME_TITLES = {'dr', 'prof', 'engr', 'atty', 'arch', 'rev', 'mr', 'ms', 'mrs', 'phd', 'jr', 'sr', 'ii', 'iii'}

        for hf in hist_faculty:
            # Fast path: historical_data row has a stored employee number — no name matching needed.
            stored_emp = (hf.get('employeenumber') or '').strip()
            if stored_emp:
                f = fac_by_emp.get(stored_emp)
                if f and stored_emp not in recommended_ids:
                    recommended_ids.add(stored_emp)
                    total = int(hf['freq']) + sched_fac_counts.get(stored_emp, 0)
                    recommended_faculty.append(_fac_item(f, count=total, _src='historical', _hist_name=hf['instructor']))
                    print(f'[DSS DEBUG] hist ID match: "{hf["instructor"]}" (empnum={stored_emp}) → {f["fullname"]}')
                continue  # skip name-matching for this row regardless

            hist_name = (hf['instructor'] or '').strip().lower()
            for f in all_faculty:
                last = f['fullname'].split(',')[0].strip().lower()
                if not last:
                    continue

                if ',' in hist_name:
                    # "LAST, FIRST [MIDDLE]" format
                    hist_last = hist_name.split(',')[0].strip()
                    hist_rest = hist_name.split(',', 1)[1].strip()

                    # Extract first name token, skipping academic titles (DR., PROF., etc.)
                    # so "DELA CRUZ, DR. JUAN" → hist_fi = "juan" not "dr"
                    hist_fi = ''
                    for _tok in hist_rest.split():
                        _clean = _tok.rstrip('.')
                        if _clean and _clean not in _NAME_TITLES:
                            hist_fi = _clean
                            break

                    # Normalize both last names: treat hyphens the same as spaces so that
                    # Faculty "SANTOS-REYES" matches historical "SANTOS REYES" and vice versa.
                    last_n  = last.replace('-', ' ')
                    hlast_n = hist_last.replace('-', ' ')
                    # No-space variant catches compound-particle differences:
                    # "DE LA ROSA" vs "DELA ROSA", "DE GUZMAN" vs "DEGUZMAN", etc.
                    last_nospace  = last_n.replace(' ', '')
                    hlast_nospace = hlast_n.replace(' ', '')

                    # Compound-surname matching (prefix-only to avoid false positives).
                    # Suffix/endswith checks were removed — a 4-char surname like "REYES"
                    # would otherwise match any historical "SANTOS REYES", bypassing the
                    # first-name guard and producing wrong recommendations.
                    last_matched = (
                        hlast_n == last_n or                                       # exact (hyphens normalized)
                        hlast_nospace == last_nospace or                           # "DELA ROSA" == "DE LA ROSA"
                        hlast_n.startswith(last_n + ' ') or                       # Faculty last is prefix of hist compound
                        (len(hlast_n) >= 4 and last_n.startswith(hlast_n + ' '))  # hist is prefix of Faculty compound
                    )

                    matched = last_matched
                    if last_matched and hist_fi:
                        # Refine with first-name check to separate same-surname faculty.
                        fac_rest = f['fullname'].split(',', 1)[1].strip().lower() if ',' in f['fullname'] else ''
                        fac_fi   = fac_rest.split()[0].rstrip('.') if fac_rest else ''
                        if fac_fi:
                            if len(hist_fi) > 1 and len(fac_fi) > 1:
                                # Both sides have a multi-char first name.
                                # Use bidirectional prefix so that common Filipino name
                                # variations ("JOSE"/"JOSEPH", "MA."/"MARIA", "JUAN"/"JUANITO")
                                # still match without requiring exact spelling equality.
                                matched = (
                                    hist_fi == fac_fi or
                                    fac_fi.startswith(hist_fi) or
                                    hist_fi.startswith(fac_fi)
                                )
                            else:
                                # At least one side is a single initial — compare initial only.
                                matched = (hist_fi[0] == fac_fi[0])
                else:
                    # No comma — could be "LAST" only, or abbreviated compound surname.
                    # No first-name guard is possible here, so we keep matches strict:
                    # only exact, no-space-variant, or clear prefix cases.
                    # endswith / "in" checks were removed — without a first-name guard they
                    # produced false positives (e.g. Faculty "SANTOS" matching historical
                    # "JOSE SANTOS REYES" where Santos is a middle word).
                    last_nospace      = last.replace('-', ' ').replace(' ', '')
                    hist_name_nospace = hist_name.replace('-', ' ').replace(' ', '')
                    matched = (
                        hist_name == last or
                        hist_name_nospace == last_nospace or                           # "DELA ROSA" == "DE LA ROSA"
                        hist_name.startswith(last + ' ') or                            # compound: hist starts with faculty last
                        (len(hist_name) >= 4 and last.startswith(hist_name + ' '))    # Faculty compound, hist is prefix
                    )

                if matched:
                    if f['employeenumber'] not in recommended_ids:
                        recommended_ids.add(f['employeenumber'])
                        total = int(hf['freq']) + sched_fac_counts.get(f['employeenumber'], 0)
                        recommended_faculty.append(_fac_item(f, count=total, _src='historical', _hist_name=hf['instructor']))
                        print(f'[DSS DEBUG] hist match: "{hf["instructor"]}" → {f["fullname"]} (empnum={f["employeenumber"]})')
                    break
            else:
                # Inner loop completed without break = no Faculty record matched this historical name
                print(f'[DSS DEBUG] hist NO MATCH: "{hf["instructor"]}" — not found in {len(all_faculty)} active faculty')

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
                info = _rf_get_faculty_info(
                    subject_code,
                    entry['name'],
                    entry.get('_hist_name'),
                    entry.get('id'),        # employee_number — primary identity key
                )
                entry['rf_score'] = info['score']
                if info.get('rf_explanation'):
                    entry['rf_explanation'] = info['rf_explanation']
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

        # 6a. Schedule-table room frequency (exact FK match — no room name ambiguity)
        cur.execute("""
            SELECT ss.roomid, COUNT(*) AS freq
            FROM schedule_sessions ss
            JOIN schedule_version sv  ON ss.versionid = sv.versionid
            JOIN schedule sc          ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            WHERE UPPER(cs.subjectcode) = UPPER(%s)
              AND ss.roomid IS NOT NULL
              AND sv.status IN ('Published', 'Archive')
            GROUP BY ss.roomid
            ORDER BY freq DESC
        """, (subject_code,))
        sched_room_counts = {r['roomid']: int(r['freq']) for r in cur.fetchall()}

        # 6b. Historical_data room frequency — code match first; name match only if both
        #     the code and the schedule-table lookups returned nothing.
        cur.execute("""
            SELECT TRIM("Room") AS room, COUNT(*) AS freq
            FROM historical_data
            WHERE REGEXP_REPLACE(UPPER(TRIM("Subject Code")), '[^A-Z0-9]', '', 'g') =
                  REGEXP_REPLACE(UPPER(TRIM(%s)), '[^A-Z0-9]', '', 'g')
              AND "Room" IS NOT NULL AND TRIM("Room") != ''
            GROUP BY TRIM("Room")
            ORDER BY freq DESC
            LIMIT 15
        """, (subject_code,))
        hist_rooms = cur.fetchall()

        # 7. Build recommended room list — schedule-table IDs first, then historical names
        room_by_id = {r['roomid']: r for r in all_rooms}
        recommended_room_ids = set()
        recommended_rooms = []

        # 7a. From schedule tables (roomid-based — no name guessing)
        for room_id, freq in sorted(sched_room_counts.items(), key=lambda x: -x[1]):
            r = room_by_id.get(room_id)
            if r and room_id not in recommended_room_ids:
                recommended_room_ids.add(room_id)
                recommended_rooms.append({
                    "id": r['roomid'], "name": r['roomname'],
                    "type": r['roomtype'], "count": freq
                })

        # 7b. From historical_data (name-based matching for any room not already captured)
        for hr in hist_rooms:
            hist_room = (hr['room'] or '').strip().upper()
            for r in all_rooms:
                if r['roomname'].upper() == hist_room or hist_room in r['roomname'].upper():
                    if r['roomid'] not in recommended_room_ids:
                        recommended_room_ids.add(r['roomid'])
                        total = int(hr['freq']) + sched_room_counts.get(r['roomid'], 0)
                        recommended_rooms.append({
                            "id": r['roomid'], "name": r['roomname'],
                            "type": r['roomtype'], "count": total
                        })
                    break
        # Sort recommended_rooms by combined count descending before RF re-rank
        recommended_rooms.sort(key=lambda x: x.get('count', 0), reverse=True)

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
        FROM curriculumsubject WHERE UPPER(subjectcode) = UPPER(%s) LIMIT 1
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
    try:
        return _api_manual_faculty_load_impl()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def _api_manual_faculty_load_impl():
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
        LEFT JOIN employeetype et ON f.employeetypeid = et.employeetypeid
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
        try:
            # Draft-preferred: for each subject+section pick the latest version, preferring
            # Draft over Published. This prevents double-counting when a faculty is reassigned —
            # the old Published (old faculty) is superseded by the new Draft (new faculty).
            _status_filter = "'Published'" if scheduler_mode == 'local' else "'Draft', 'Published'"
            _draft_priority = "" if scheduler_mode == 'local' else "CASE WHEN sv.status = 'Draft' THEN 0 ELSE 1 END,"
            sched = query_db(f"""
                SELECT COALESCE(SUM(load_units), 0) AS sched_units
                FROM (
                    SELECT DISTINCT ON (cs.subjectcode, sg.sectionid)
                           COALESCE(cs.creditunits, 0) AS load_units
                    FROM schedule_version sv
                    JOIN schedule sg ON sv.scheduleid = sg.scheduleid
                    JOIN curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
                    JOIN semester s ON sg.semesterid = s.semesterid
                    WHERE sg.employeenumber = %s
                      AND s.academicyearid = %s
                      AND s.semestertype = %s
                      AND sv.status IN ({_status_filter})
                    ORDER BY cs.subjectcode, sg.sectionid,
                             {_draft_priority}
                             sv.version_number DESC
                ) AS d
            """, (emp_num, ay_id, sem), one=True)
            if sched:
                scheduled = int(sched['sched_units'] or 0)
        except Exception as _e:
            import traceback; traceback.print_exc()
            scheduled = 0
    available = max(0, max_load - scheduled)

    # HC7 — count weekday night sessions
    night_classes = 0
    if ay_id and sem:
        try:
            nq = query_db("""
                SELECT COUNT(*) AS night_count
                FROM schedule_sessions ss
                JOIN schedule_version sv ON ss.versionid  = sv.versionid
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
        except Exception:
            night_classes = 0

    assigned_subjects = []
    if ay_id and sem:
        try:
            # Draft-preferred: pick the latest version per subject+section to avoid showing
            # old Published assignment (old faculty) alongside a new Draft assignment (new faculty).
            rows = query_db("""
                SELECT DISTINCT ON (cs.subjectcode, sg.sectionid)
                       cs.subjectcode, cs.subjectname, sg.scheduleid,
                       COALESCE(sec.sectionname, '') AS sectionname,
                       COALESCE(cs.creditunits, 0) AS creditunits
                FROM schedule_version sv
                JOIN schedule sg          ON sv.scheduleid  = sg.scheduleid
                JOIN curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
                LEFT JOIN sections sec     ON sg.sectionid   = sec.sectionid
                JOIN semester s            ON sg.semesterid  = s.semesterid
                WHERE sg.employeenumber = %s
                  AND s.academicyearid  = %s
                  AND s.semestertype    = %s
                  AND sv.status IN ('Published', 'Draft')
                ORDER BY cs.subjectcode, sg.sectionid,
                         CASE WHEN sv.status = 'Draft' THEN 0 ELSE 1 END,
                         sv.version_number DESC
            """, (emp_num, ay_id, sem))
            assigned_subjects = [dict(r) for r in (rows or [])]
        except Exception:
            assigned_subjects = []

    # Subjects reserved via "Assign Faculty" but not yet saved as schedule sessions.
    # These are cross-program reservations stored in subject_faculty_assignment.
    pending_subjects = []
    pending_units    = 0
    if ay_id and sem:
        try:
            _ensure_faculty_assignment_table_exists = lambda cur: None  # table ensured at write time
            sem_row = query_db(
                "SELECT semesterid FROM semester WHERE academicyearid=%s AND semestertype=%s LIMIT 1",
                (ay_id, sem), one=True)
            if sem_row:
                _sem_id = sem_row['semesterid']
                # Subjects already in schedule sessions (don't double-count)
                _sched_codes = set(r['subjectcode'].upper() for r in assigned_subjects)
                pend_rows = query_db("""
                    SELECT sfa.subjectcode,
                           MAX(sfa.programcode) AS programcode,
                           MAX(sfa.yearlevel)   AS yearlevel,
                           COALESCE(MAX(cs.subjectname), MAX(sfa.subjectcode)) AS subjectname,
                           COALESCE(MAX(cs.creditunits), 0) AS creditunits
                    FROM public.subject_faculty_assignment sfa
                    LEFT JOIN curriculumsubject cs
                           ON UPPER(cs.subjectcode) = UPPER(sfa.subjectcode)
                    WHERE sfa.employeenumber = %s
                      AND sfa.semesterid     = %s
                    GROUP BY sfa.subjectcode
                """, (emp_num, _sem_id)) or []
                for pr in pend_rows:
                    code = (pr['subjectcode'] or '').upper()
                    if code in _sched_codes:
                        continue   # already counted in schedule sessions
                    units_val = int(pr['creditunits'] or 0)
                    pending_subjects.append({
                        'subjectcode':  pr['subjectcode'],
                        'subjectname':  pr['subjectname'],
                        'programcode':  pr['programcode'],
                        'yearlevel':    pr['yearlevel'],
                        'creditunits':  units_val,
                        'is_pending':   True,
                    })
                    pending_units += units_val
        except Exception:
            pending_subjects = []
            pending_units    = 0

    total_assigned = scheduled + pending_units
    available      = max(0, max_load - total_assigned)

    return jsonify({'success': True, 'total_units': max_load,
                    'available_units': available, 'scheduled_units': scheduled,
                    'pending_units': pending_units, 'pending_subjects': pending_subjects,
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
    filters = ["UPPER(cs.subjectcode) = UPPER(%s)", "sv.status IN ('Published','Draft')"]
    params  = [subj]
    if ay_id and sem:
        filters.append("sc.semesterid = (SELECT semesterid FROM semester WHERE academicyearid = %s AND semestertype = %s LIMIT 1)")
        params += [ay_id, sem]
    if prog:
        filters.append("UPPER(pyl.programcode) = UPPER(%s)")
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
        LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
        LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
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
    section_id    = request.args.get('section_id', '').strip()
    scheduler_mode = request.args.get('scheduler_mode', 'official')
    if not subj or not ay_id or not sem:
        return jsonify({'success': True, 'sessions': []})
    base_filters = [
        "UPPER(cs.subjectcode) = UPPER(%s)",
        "sc.semesterid = (SELECT semesterid FROM semester WHERE academicyearid = %s AND semestertype = %s LIMIT 1)"
    ]
    base_params = [subj, ay_id, sem]
    if prog:
        base_filters.append("pyl.programcode = %s")
        base_params.append(prog)
    if yl:
        base_filters.append("pyl.yearlevel = %s")
        base_params.append(int(yl))
    if section_id:
        try:
            base_filters.append("sec.sectionid = %s")
            base_params.append(int(section_id))
        except (ValueError, TypeError):
            pass

    session_query = f"""
        SELECT ss.starttimeid, ss.endtimeid, ss.daydesc,
               sv.versionid,
               cs.subjectcode, cs.subjectname,
               pyl.yearlevel, pyl.programcode,
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
        LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
        LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
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
                LEFT JOIN LATERAL (
                    SELECT subjectname FROM curriculumsubject
                    WHERE UPPER(subjectcode) = UPPER(las.subjectcode)
                    LIMIT 1
                ) subj ON TRUE
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
        # Official Scheduler: Draft is the definitive replacement for Published.
        # Suppress ALL old Published slots so the calendar never shows the same subject
        # as both Published and Draft simultaneously — even when time or room changed.
        # Slots whose (day, timeid) still match a Published slot are promoted to
        # 'Published' appearance so the UI shows they were already approved.
        pub_slot_keys = {(r['daydesc'], r['starttimeid']) for r in pub_rows}
        rows = []
        for r in draft_rows:
            r = dict(r)
            if (r['daydesc'], r['starttimeid']) in pub_slot_keys:
                r['status'] = 'Published'
            rows.append(r)
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
    section_id     = request.args.get('section_id', '').strip()
    scheduler_mode = request.args.get('scheduler_mode', 'official')
    if not prog or not yl or not ay_id or not sem:
        return jsonify([])
    status_clause = "sv.status = 'Published'" if scheduler_mode == 'local' else "sv.status IN ('Published', 'Draft')"
    extra_where = ''
    extra_params = []
    if section_id:
        try:
            extra_where = 'AND sec.sectionid = %s'
            extra_params = [int(section_id)]
        except (ValueError, TypeError):
            pass
    try:
        rows = query_db(f"""
            SELECT
                cs.subjectcode,
                cs.subjectname,
                ss.daydesc,
                ss.starttimeid,
                ss.endtimeid,
                sv.status
            FROM schedule_sessions ss
            JOIN schedule_version sv  ON ss.versionid  = sv.versionid
            JOIN schedule sc          ON sv.scheduleid  = sc.scheduleid
            JOIN semester s           ON sc.semesterid  = s.semesterid
            JOIN academicyear ay      ON s.academicyearid = ay.academicyearid
            JOIN sections sec             ON sc.sectionid              = sec.sectionid
            JOIN program_yearlevel pyl    ON sec.programyearlevelid   = pyl.programyearlevelid
            JOIN curriculumsubject cs     ON sc.curriculumsubjectid   = cs.curriculumsubjectid
            WHERE pyl.programcode                = %s
              AND pyl.yearlevel::text           = %s
              AND ay.academicyearid::text      = %s
              AND s.semestertype               = %s
              AND {status_clause}
              {extra_where}
        """, (prog, yl, ay_id, sem) + tuple(extra_params))
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
    _gs_section_id = request.args.get('section_id', '').strip()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT cs.SubjectCode, cs.SubjectName,
                   COALESCE(cs.CreditUnits, 0) AS creditunits,
                   COALESCE(cs.LaboratoryHours, 0) AS laboratoryhours,
                   (COALESCE(cs.LectureHours, 0) + COALESCE(cs.LaboratoryHours, 0)) AS total_hours
            FROM CurriculumSubject cs
            WHERE cs.CurriculumID = %s AND cs.YearLevel = %s AND cs.Semester = %s
            ORDER BY cs.SubjectName ASC
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
                # Two aliases are used in the query: sv2 (CTE) and sv (outer); keep separate strings.
                version_filter_sv2 = "sv2.status = 'Published'" if scheduler_mode == 'local' \
                                     else "sv2.status IN ('Draft', 'Published')"
                version_filter_sv  = "sv.status = 'Published'"  if scheduler_mode == 'local' \
                                     else "sv.status IN ('Draft', 'Published')"
                _gs_sect_clause_cte  = 'AND sec2.sectionid = %s' if _gs_section_id else ''
                _gs_sect_clause_main = 'AND sec.sectionid  = %s' if _gs_section_id else ''

                sched_rows = query_db(f"""
                    WITH sem_cte AS (
                        SELECT semesterid FROM semester
                        WHERE  academicyearid = %s AND semestertype = %s LIMIT 1
                    ),
                    prog_cte AS (
                        SELECT c.programcode
                        FROM   curriculum c
                        WHERE  c.curriculumid = %s LIMIT 1
                    ),
                    max_version AS (
                        SELECT UPPER(cs2.subjectcode) AS subjectcode,
                               MAX(sv2.version_number)  AS max_v
                        FROM   schedule_version sv2
                        JOIN   schedule sc2           ON sv2.scheduleid          = sc2.scheduleid
                        JOIN   curriculumsubject cs2  ON sc2.curriculumsubjectid = cs2.curriculumsubjectid
                        JOIN   sections sec2          ON sc2.sectionid           = sec2.sectionid
                        JOIN   program_yearlevel pyl2 ON sec2.programyearlevelid  = pyl2.programyearlevelid
                        JOIN   sem_cte                ON sc2.semesterid          = sem_cte.semesterid
                        WHERE  {version_filter_sv2}
                          AND  pyl2.yearlevel                    = %s
                          AND  UPPER(pyl2.programcode) = UPPER((SELECT programcode FROM prog_cte))
                          {_gs_sect_clause_cte}
                        GROUP BY UPPER(cs2.subjectcode)
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
                    JOIN   sem_cte                ON sc.semesterid             = sem_cte.semesterid
                    JOIN   schedule_version sv    ON sv.scheduleid             = sc.scheduleid
                                                 AND sv.version_number        = mv.max_v
                                                 AND {version_filter_sv}
                    JOIN   schedule_sessions ss   ON ss.versionid              = sv.versionid
                    WHERE  pyl.yearlevel                    = %s
                      AND  UPPER(pyl.programcode) = UPPER((SELECT programcode FROM prog_cte))
                      {_gs_sect_clause_main}
                    GROUP BY mv.subjectcode
                """, (ay_id, sem, curr_id, int(yl))
                   + ((int(_gs_section_id),) if _gs_section_id else ())
                   + (int(yl),)
                   + ((int(_gs_section_id),) if _gs_section_id else ())) or []
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
                            SELECT c.programcode
                            FROM curriculum c
                            WHERE c.curriculumid = %s LIMIT 1
                        )
                        SELECT UPPER(cs.subjectcode) AS subjectcode
                        FROM   schedule_version sv
                        JOIN   schedule sc           ON sv.scheduleid          = sc.scheduleid
                        JOIN   curriculumsubject cs   ON sc.curriculumsubjectid = cs.curriculumsubjectid
                        JOIN   sections sec           ON sc.sectionid           = sec.sectionid
                        JOIN   program_yearlevel pyl  ON sec.programyearlevelid  = pyl.programyearlevelid
                        JOIN   sem_cte               ON sc.semesterid          = sem_cte.semesterid
                        WHERE  sv.status IN ('Draft', 'Published')
                          AND  pyl.yearlevel                    = %s
                          AND  UPPER(pyl.programcode) = UPPER((SELECT programcode FROM prog_cte))
                    """ + ('AND sec.sectionid = %s' if _gs_section_id else '') + """
                        GROUP BY UPPER(cs.subjectcode)
                        HAVING COUNT(DISTINCT sv.status) > 1
                    """, (ay_id, sem, curr_id, int(yl))
                       + ((int(_gs_section_id),) if _gs_section_id else ())) or []
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
    curricula       = query_db("SELECT c.curriculumid, c.curriculumcode, c.curriculumyear, p.programcode AS programcode FROM curriculum c JOIN programs p ON c.programcode = p.programcode ORDER BY p.programcode, c.curriculumyear DESC")
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
                pyl.programcode,
                pyl.yearlevel,
                ay.yearstart || '-' || ay.yearend AS acad_year,
                sem.semestertype,
                TO_CHAR(MAX(sv.datecreated), 'MM/DD/YYYY') AS date_imported
            FROM schedule_version sv
            JOIN schedule s ON sv.scheduleid = s.scheduleid
            JOIN sections sec ON s.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN semester sem ON s.semesterid = sem.semesterid
            JOIN academicyear ay ON sem.academicyearid = ay.academicyearid
            WHERE pyl.isactive IS DISTINCT FROM FALSE
            GROUP BY pyl.programcode, pyl.yearlevel, ay.yearstart, ay.yearend, sem.semestertype
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
        LEFT JOIN Curriculum c ON UPPER(c.programcode) = UPPER(p.programcode)
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
               c.programcode as program_code
        FROM curriculum c
        JOIN programs p ON UPPER(p.programcode) = UPPER(c.programcode)
        WHERE c.curriculumid = ANY(%s)
    """, (curriculum_ids,))
    info_map = {r['curriculum_id']: dict(r) for r in cur.fetchall()}
    cur.execute("""
        SELECT cv.CurriculumID as curriculum_id,
               CAST(SUBSTRING(cv.ProgramYearLevel FROM '-(.*)') AS INT) as year_level,
               cv.Semester as semester,
               cv.SubjectCode as subject_code,
               COALESCE(cv.SubjectName, '') as subject_name,
               COALESCE(cv.LectureHours, 0)    as lecture_hours,
               COALESCE(cv.LaboratoryHours, 0) as lab_hours,
               COALESCE(cv.CreditUnits, 0)     as credit_units,
               COALESCE(cv.TuitionHours, 0)    as tuition_hours,
               COALESCE(cv."Prerequisite", '')  as prerequisite,
               COALESCE(cv."Co-requisite", '')  as corequisite
        FROM Curriculum_View cv
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

    active = query_db("""
        SELECT ay.academicyearid, s.semestertype
        FROM semester s
        JOIN academicyear ay ON s.academicyearid = ay.academicyearid
        WHERE s.isactive = TRUE LIMIT 1
    """, one=True)
    active_ay_id = active['academicyearid'] if active else ''
    active_sem   = active['semestertype']    if active else ''

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
        total=len(employees),
        active_ay_id=active_ay_id,
        active_sem=active_sem
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
        if not contact.isdigit():
            flash("Contact Number must contain numbers only.", "error")
            return redirect(url_for('admin_employee'))

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
        if not contact.isdigit():
            flash("Contact Number must contain numbers only.", "error")
            return redirect(url_for('admin_employee'))

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
        def gv(field, _r=raw, _m=col_map):
            idx = _m.get(field)
            return _clean_cell(_r[idx]) if idx is not None and idx < len(_r) else ''
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
    if int(capacity) < 35: return jsonify({'success': False, 'error': 'Capacity must be at least 35.'}), 400
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
    if not capacity: return jsonify({'success': False, 'error': 'Capacity is required.'}), 400
    if int(capacity) < 35: return jsonify({'success': False, 'error': 'Capacity must be at least 35.'}), 400
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

        # No auto-sync needed in v10 — cohort rows are managed manually
        pass
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
            SELECT c.*, c.programcode AS offeringcode, p.programname,
                   p.programname AS parentprogramname
            FROM curriculum c
            JOIN programs p ON c.programcode = p.programcode
            ORDER BY p.programname ASC, c.curriculumyear DESC
        """)
    elif selected_program:
        currs_raw = query_db("""
            SELECT c.*, c.programcode AS offeringcode, p.programname
            FROM curriculum c
            JOIN programs p ON c.programcode = p.programcode
            WHERE c.programcode = %s
            ORDER BY c.curriculumyear DESC
        """, (selected_program,))
    curriculums = [{k.lower(): v for k, v in row.items()} for row in currs_raw] if currs_raw else []

    unique_codes_raw = query_db("SELECT DISTINCT curriculumcode FROM curriculum ORDER BY curriculumcode DESC")
    unique_codes = [{k.lower(): v for k, v in row.items()} for row in unique_codes_raw] if unique_codes_raw else []

    all_curriculums_raw = query_db("""
        SELECT c.*, c.programcode AS offeringcode
        FROM curriculum c
        ORDER BY c.curriculumyear DESC
    """)
    all_curriculums = [{k.lower(): v for k, v in row.items()} for row in all_curriculums_raw] if all_curriculums_raw else []

    # program_yearlevel rows for the admin curriculum page
    pylrows_raw = query_db("""
        SELECT pyl.programyearlevelid AS cohortid,
               pyl.programcode      AS offeringcode,
               p.programname,
               pyl.programcode,
               pyl.startacademicyear AS academicyearid,
               pyl.yearlevel,
               pyl.curriculumid,
               c.curriculumcode,
               pyl.isactive,
               GREATEST(COUNT(DISTINCT sec.sectionid) FILTER (WHERE sec.isactive = TRUE), 1) AS section_count
        FROM program_yearlevel pyl
        JOIN programs   p ON p.programcode  = pyl.programcode
        LEFT JOIN curriculum c ON c.curriculumid = pyl.curriculumid
        LEFT JOIN sections sec ON sec.programyearlevelid = pyl.programyearlevelid
        WHERE pyl.isactive = TRUE
        GROUP BY pyl.programyearlevelid, pyl.programcode, p.programname,
                 pyl.startacademicyear, pyl.yearlevel, pyl.curriculumid,
                 c.curriculumcode, pyl.isactive
        ORDER BY p.programname, pyl.startacademicyear DESC, pyl.yearlevel
    """)
    cohorts = [{k.lower(): v for k, v in row.items()} for row in pylrows_raw] if pylrows_raw else []

    import_offerings = query_db("""
        SELECT p.programcode, p.programcode AS offeringcode, p.programname,
               COALESCE(p.numyearlevel, 4) AS numyearlevel,
               NULL::text AS trackname, NULL::text AS trackcode
        FROM programs p
        WHERE p.isactive = TRUE
        ORDER BY p.programname
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
        SELECT c.curriculumcode,
               p.programname,
               pyl.yearlevel AS year_level,
               COUNT(sec.sectionid) FILTER (WHERE sec.isactive = TRUE) AS numberofsections,
               pyl.startacademicyear
        FROM program_yearlevel pyl
        JOIN programs   p ON p.programcode    = pyl.programcode
        LEFT JOIN curriculum c ON c.curriculumid = pyl.curriculumid
        LEFT JOIN sections sec ON sec.programyearlevelid = pyl.programyearlevelid
        WHERE pyl.isactive = TRUE
        GROUP BY c.curriculumcode, p.programname, pyl.yearlevel, pyl.startacademicyear
        ORDER BY pyl.startacademicyear DESC, p.programname ASC
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
        SELECT p.programname, c.curriculumyear, cs.yearlevel,
               cs.semester, cs.subjectcode, cs.prerequisite, cs.corequisite, cs.subjectname AS description,
               cs.lecturehours, cs.laboratoryhours, cs.creditunits, cs.tuitionhours
        FROM curriculumsubject cs
        JOIN curriculum c ON cs.curriculumid = c.curriculumid
        JOIN programs   p ON c.programcode   = p.programcode
    """
    params = []
    if program_code != 'All':
        query += " WHERE c.programcode = %s"
        params.append(program_code)

    query += " ORDER BY p.programname ASC, c.curriculumyear DESC, cs.yearlevel ASC, cs.semester ASC"
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
               SubjectName as description, LectureHours, LaboratoryHours, CreditUnits, TuitionHours,
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

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    step = 'verifying program exists'
    try:
        # Verify program exists
        cur.execute("SELECT 1 FROM programs WHERE programcode = %s", (prog_code,))
        if not cur.fetchone():
            flash(f"Import Blocked: Program '{prog_code}' not found.")
            return redirect(request.referrer)

        cur.execute("""
            SELECT 1 FROM curriculum WHERE programcode = %s AND curriculumyear = %s
        """, (prog_code, curr_year))
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

        step = 'creating curriculum record'
        cur.execute("""
            INSERT INTO curriculum (curriculumcode, programcode, curriculumyear)
            VALUES (%s, %s, %s) RETURNING curriculumid
        """, (curr_code_str, prog_code, curr_year))
        target_curr_id = cur.fetchone()['curriculumid']

        last_yl  = int(request.form.get('year_level', 0) or 0) or 1
        last_sem = request.form.get('semester', 'All') or 'All'
        if last_sem == 'All': last_sem = 'A'

        step = 'inserting subjects'
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

            s_name_val = get_val(row, 'sn')[:100] if get_val(row, 'sn') else s_code
            cur.execute("""
                INSERT INTO CurriculumSubject (CurriculumID, SubjectCode, SubjectName, CreditUnits, LectureHours, LaboratoryHours, TuitionHours, YearLevel, Semester)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT uq_curriculumsubject DO UPDATE SET
                    SubjectName = EXCLUDED.SubjectName,
                    CreditUnits = EXCLUDED.CreditUnits,
                    LectureHours = EXCLUDED.LectureHours,
                    LaboratoryHours = EXCLUDED.LaboratoryHours,
                    TuitionHours = EXCLUDED.TuitionHours
            """, (target_curr_id, s_code, s_name_val, parse_int(get_val(row, 'u')), parse_int(get_val(row, 'lc')), parse_int(get_val(row, 'lb')), parse_int(get_val(row, 'th')), final_yl, final_sem))

            pre_val = get_val(row, 'pre') or ''
            co_val  = get_val(row, 'co')  or ''
            pre_clean = pre_val.strip() if pre_val.strip().upper() not in ('NONE', '-', 'N/A', '') else None
            co_clean  = co_val.strip()  if co_val.strip().upper()  not in ('NONE', '-', 'N/A', '') else None
            if pre_clean is not None or co_clean is not None:
                try:
                    cur.execute("SAVEPOINT prereq_sp")
                    cur.execute("""
                        UPDATE curriculumsubject
                           SET prerequisite = COALESCE(%s, prerequisite),
                               corequisite  = COALESCE(%s, corequisite)
                         WHERE curriculumid = %s AND UPPER(subjectcode) = UPPER(%s)
                    """, (pre_clean, co_clean, target_curr_id, s_code))
                    cur.execute("RELEASE SAVEPOINT prereq_sp")
                except Exception as _e:
                    cur.execute("ROLLBACK TO SAVEPOINT prereq_sp")
                    print(f"[WARN] Could not save prereq/coreq for {s_code!r}: {_e}")

        step = 'setting up year levels'
        _auto_setup_program_yearlevels(cur, prog_filter=prog_code)
        step = 'reassigning curriculum to cohorts'
        _reassign_curriculum_for_program(cur, prog_code)
        conn.commit()
        flash(f"Import Successful for {prog_code} C.Y {curr_year}")

    except Exception as e:
        conn.rollback()
        flash(f"Import failed while {step}: {e}")
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

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    step = 'checking for existing curriculum'
    try:
        cur.execute("""
            SELECT c.curriculumid FROM curriculum c
            WHERE c.programcode = %s AND c.curriculumyear = %s
        """, (prog_code, curr_year))
        existing = cur.fetchone()
        if existing:
            if not override:
                flash(f"Import Blocked: Curriculum for {prog_code} C.Y {curr_year} already exists in the system.")
                return redirect(url_for('admin_curriculum'))
            existing_id = existing['curriculumid']
            step = 'clearing existing subjects for override'
            cur.execute("DELETE FROM CurriculumSubject WHERE CurriculumID = %s", (existing_id,))

        if existing and override:
            curr_id = existing_id
        else:
            step = 'verifying program exists'
            cur.execute("SELECT 1 FROM programs WHERE programcode = %s", (prog_code,))
            if not cur.fetchone():
                flash(f"Import Failed: Program '{prog_code}' not found in the system.")
                return redirect(url_for('admin_curriculum'))
            years = curr_year.split('-')
            curr_code = f"CY{years[0][-2:]}{years[1][-2:]}" if len(years) == 2 else "CY0000"
            step = 'creating curriculum record'
            cur.execute("""
                INSERT INTO Curriculum (CurriculumCode, programcode, CurriculumYear)
                VALUES (%s, %s, %s) RETURNING CurriculumID
            """, (curr_code, prog_code, curr_year))
            curr_id = cur.fetchone()['curriculumid']

        step = 'inserting subjects'
        for s in subjects:
            sc = str(s.get('sc', '')).strip()[:50]
            if not sc: continue
            sn = (str(s.get('sn', sc)).strip() or sc)[:100]
            yl  = parse_int(s.get('yl')) or 1
            sem = norm_sem(s.get('sem', 'A'))
            pre = str(s.get('pre', '')).strip()[:100]
            co  = str(s.get('co',  '')).strip()[:100]
            pre_clean = pre if pre.upper() not in ('NONE', '-', 'N/A', '') else None
            co_clean  = co  if co.upper()  not in ('NONE', '-', 'N/A', '') else None
            cur.execute("""
                INSERT INTO CurriculumSubject (CurriculumID, SubjectCode, SubjectName, CreditUnits, LectureHours, LaboratoryHours, TuitionHours, YearLevel, Semester)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT uq_curriculumsubject DO UPDATE SET
                    SubjectName = EXCLUDED.SubjectName,
                    CreditUnits = EXCLUDED.CreditUnits,
                    LectureHours = EXCLUDED.LectureHours,
                    LaboratoryHours = EXCLUDED.LaboratoryHours,
                    TuitionHours = EXCLUDED.TuitionHours
            """, (curr_id, sc, sn, parse_int(s.get('u')), parse_int(s.get('lc')), parse_int(s.get('lb')), parse_int(s.get('th')), yl, sem))

            if pre_clean or co_clean:
                try:
                    cur.execute("SAVEPOINT prereq_sp")
                    cur.execute("""
                        UPDATE curriculumsubject SET
                            prerequisite = COALESCE(%s, prerequisite),
                            corequisite  = COALESCE(%s, corequisite)
                        WHERE curriculumid = %s AND UPPER(subjectcode) = UPPER(%s)
                    """, (pre_clean, co_clean, curr_id, sc))
                    cur.execute("RELEASE SAVEPOINT prereq_sp")
                except Exception as _e:
                    cur.execute("ROLLBACK TO SAVEPOINT prereq_sp")

        step = 'setting up year levels'
        _auto_setup_program_yearlevels(cur, prog_filter=prog_code)
        step = 'reassigning curriculum to cohorts'
        _reassign_curriculum_for_program(cur, prog_code)
        conn.commit()
        action_word = "overridden" if (existing and override) else "imported"
        flash(f"{import_source} Import Successful: {prog_code} C.Y {curr_year} — {len(subjects)} subjects {action_word}.")
    except Exception as e:
        conn.rollback()
        flash(f"Import failed while {step}: {e}")
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

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    step = 'checking for existing curriculum'
    try:
        cur.execute("""
            SELECT 1 FROM curriculum WHERE programcode = %s AND curriculumyear = %s
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

        step = 'verifying program exists'
        cur.execute("SELECT 1 FROM programs WHERE programcode = %s", (prog_code,))
        if not cur.fetchone():
            flash(f"Import Failed: Program '{prog_code}' not found in the system.")
            return redirect(request.referrer)

        csv_cc = get_val(data_rows[0], 'cc') if data_rows and 'cc' in idx else None
        if csv_cc:
            curr_code_str = csv_cc[:6]
        else:
            years = curr_year.split('-')
            curr_code_str = f"CY{years[0][-2:]}{years[1][-2:]}" if len(years) == 2 else "CY0000"

        step = 'creating curriculum record'
        cur.execute(
            "INSERT INTO Curriculum (CurriculumCode, programcode, CurriculumYear) "
            "VALUES (%s, %s, %s) RETURNING CurriculumID",
            (curr_code_str, prog_code, curr_year)
        )
        target_curr_id = cur.fetchone()['curriculumid']

        last_yl  = parse_int(ui_year) if parse_int(ui_year) > 0 else 1
        last_sem = ui_sem if ui_sem != 'All' else 'A'

        for row in data_rows:
            if not any(c is not None for c in row): continue
            s_code = get_val(row, 'sc')[:15]   # VARCHAR(15) guard
            s_name = get_val(row, 'sn')[:100]  # VARCHAR(100) guard
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
                INSERT INTO CurriculumSubject (CurriculumID, SubjectCode, SubjectName, CreditUnits, LectureHours, LaboratoryHours, TuitionHours, YearLevel, Semester)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT uq_curriculumsubject DO UPDATE SET
                    SubjectName = EXCLUDED.SubjectName,
                    CreditUnits = EXCLUDED.CreditUnits,
                    LectureHours = EXCLUDED.LectureHours,
                    LaboratoryHours = EXCLUDED.LaboratoryHours,
                    TuitionHours = EXCLUDED.TuitionHours
            """, (target_curr_id, s_code, s_name or s_code, parse_int(get_val(row, 'u')),
                  parse_int(get_val(row, 'lc')), parse_int(get_val(row, 'lb')),
                  parse_int(get_val(row, 'th')), final_yl, final_sem))

            pre_val   = get_val(row, 'pre') or ''
            co_val    = get_val(row, 'co')  or ''
            pre_clean = pre_val.strip() if pre_val.strip().upper() not in ('NONE', '-', 'N/A', '') else None
            co_clean  = co_val.strip()  if co_val.strip().upper()  not in ('NONE', '-', 'N/A', '') else None
            if pre_clean is not None or co_clean is not None:
                try:
                    cur.execute("SAVEPOINT prereq_sp")
                    cur.execute("""
                        UPDATE curriculumsubject
                           SET prerequisite = COALESCE(%s, prerequisite),
                               corequisite  = COALESCE(%s, corequisite)
                         WHERE curriculumid = %s AND UPPER(subjectcode) = UPPER(%s)
                    """, (pre_clean, co_clean, target_curr_id, s_code))
                    cur.execute("RELEASE SAVEPOINT prereq_sp")
                except Exception as _e:
                    cur.execute("ROLLBACK TO SAVEPOINT prereq_sp")

        step = 'setting up year levels'
        _auto_setup_program_yearlevels(cur, prog_filter=prog_code)
        step = 'reassigning curriculum to cohorts'
        _reassign_curriculum_for_program(cur, prog_code)
        conn.commit()
        flash(f"Import Successful for {prog_code} C.Y {curr_year}")

    except Exception as e:
        conn.rollback()
        flash(f"Import failed while {step}: {e}")
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
            SELECT curriculumid FROM curriculum
            WHERE programcode = %s AND curriculumyear = %s
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
        # 4a. Deactivate any program_yearlevel rows referencing this curriculum (and their sections)
        cur.execute("""
            UPDATE sections SET isactive = FALSE
            WHERE programyearlevelid IN (
                SELECT programyearlevelid FROM program_yearlevel WHERE curriculumid = %s
            )
        """, (curriculum_id,))
        cur.execute("""
            UPDATE program_yearlevel SET isactive = FALSE, curriculumid = NULL WHERE curriculumid = %s
        """, (curriculum_id,))

        # 4b. Delete curriculum subjects
        cur.execute("DELETE FROM curriculumsubject WHERE curriculumid = %s", (curriculum_id,))

        # exclusive_subjects cleanup removed: subject table no longer exists

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
                UPDATE program_yearlevel SET isactive = %s, curriculumid = %s
                WHERE programyearlevelid = %s
            """, (isactive, int(curr_id), int(pyl_id)))
            flash("Assignment updated successfully.")
        else:
            offering_code  = request.form.get('offering_code', '').strip()
            academicyearid = request.form.get('academicyearid', '').strip()
            yearlevel      = int(request.form.get('yearlevel', 1))
            if not offering_code:
                flash("Error: No program selected.")
                return redirect(url_for('admin_curriculum'))
            # Compute startacademicyear from the academicyear's yearstart
            conn2 = get_db_connection(); cur2 = conn2.cursor()
            cur2.execute("SELECT yearstart FROM academicyear WHERE academicyearid=%s", (academicyearid,))
            _ayr = cur2.fetchone(); cur2.close(); conn2.close()
            _ys = int(_ayr[0]) if _ayr else 2025
            start_ay = f"{_ys - (yearlevel - 1)}-{_ys - (yearlevel - 1) + 1}"
            cur.execute("""
                INSERT INTO program_yearlevel
                    (programcode, academicyearid, startacademicyear, yearlevel, curriculumid, isactive)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (programcode, academicyearid, startacademicyear, yearlevel)
                DO UPDATE SET curriculumid = EXCLUDED.curriculumid, isactive = EXCLUDED.isactive
            """, (offering_code.upper(), academicyearid, start_ay, yearlevel, int(curr_id), isactive))
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
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        count = _auto_setup_program_yearlevels(cur)
        conn.commit()
        flash(f"Program year levels updated. {count} row(s) processed.")
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
        SELECT c.*, c.programcode, p.programname,
               c.programcode AS baseprogramcode, p.numyearlevel
        FROM curriculum c
        JOIN programs p ON c.programcode = p.programcode
        WHERE c.curriculumid = %s
    """
    info_raw = query_db(info_sql, (curriculum_id,), one=True)
    if not info_raw: return redirect(url_for('admin_curriculum'))

    info = {k.lower(): v for k, v in info_raw.items()}
    progs = query_db("SELECT * FROM Programs WHERE IsActive = TRUE ORDER BY ProgramName")

    other_sql = """
        SELECT curriculumid, curriculumyear, curriculumcode
        FROM curriculum
        WHERE programcode = %s
        ORDER BY curriculumyear DESC
    """
    other_currs = query_db(other_sql, (info['programcode'],))

    main_sql = """
        SELECT cs.subjectcode, cs.prerequisite, cs.corequisite,
               cs.subjectname, cs.lecturehours, cs.laboratoryhours, cs.creditunits, cs.tuitionhours,
               cs.semester, cs.yearlevel
        FROM curriculumsubject cs
        WHERE cs.curriculumid = %s
    """
    params = [curriculum_id]
    if y_lvl != '0':
        main_sql += " AND cs.yearlevel = %s"
        params.append(int(y_lvl))
    if sem != 'All':
        main_sql += " AND cs.semester = %s"
        params.append(sem)

    main_sql += " ORDER BY cs.yearlevel ASC, cs.semester ASC"
    raw_subs = query_db(main_sql, tuple(params))
    subs =[{k.lower(): v for k, v in row.items()} for row in raw_subs] if raw_subs else[]

    return render_template('admin/curriculum_view_admin.html', info=info, subjects=subs, programs=progs, other_curriculums=other_currs, curr_year=y_lvl, curr_sem=sem)

@app.route('/admin/schedule')
def admin_schedule():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    return render_template('admin/schedule_admin.html')

@app.route('/admin/schedule/sis')
def admin_class_schedule_sis():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    from datetime import date as _date
    programs   = query_db("SELECT programcode, programname FROM programs WHERE isactive = TRUE ORDER BY programname")
    acad_years = query_db("SELECT academicyearid, yearstart, yearend FROM academicyear ORDER BY yearstart DESC")
    today = _date.today()
    active_sem_res = query_db("""
        SELECT s.semestertype, ay.academicyearid
        FROM semester s JOIN academicyear ay ON s.academicyearid = ay.academicyearid
        WHERE %s BETWEEN s.semstartdate AND s.semenddate LIMIT 1
    """, [today])
    if active_sem_res:
        active_sem_type = active_sem_res[0]['semestertype']
        active_ay       = active_sem_res[0]['academicyearid']
    else:
        most_recent = query_db("""
            SELECT sem.semestertype, sem.academicyearid
            FROM historical_data hd JOIN semester sem ON hd.semesterid = sem.semesterid
            GROUP BY sem.semestertype, sem.academicyearid
            ORDER BY MAX(hd.semesterid) DESC LIMIT 1
        """)
        if most_recent:
            active_sem_type = most_recent[0]['semestertype']
            active_ay       = most_recent[0]['academicyearid']
        else:
            active_sem_type = 'B'
            active_ay       = acad_years[0]['academicyearid'] if acad_years else ''
    sem_data = query_db("""
        SELECT academicyearid, semestertype, semenddate FROM semester
        WHERE academicyearid IN (SELECT academicyearid FROM academicyear WHERE yearend >= %s)
        ORDER BY academicyearid, semestertype
    """, [today.year])
    import json as _json
    sem_json = _json.dumps([
        {'ay': r['academicyearid'], 'type': r['semestertype'],
         'end': r['semenddate'].isoformat() if r['semenddate'] else None}
        for r in (sem_data or [])
    ])
    return render_template('admin/class_schedule_sis_admin.html',
                           programs=programs, acad_years=acad_years,
                           active_sem_type=active_sem_type, active_ay=active_ay,
                           today=today.isoformat(), sem_json=sem_json)

@app.route('/admin/schedule/room-schedule')
def admin_room_schedule():
    if session.get('role') != 'Admin': return redirect(url_for('login'))

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

    def _room_floor(name):
        n = (name or '').replace(' ', '')
        if 'LQ1' in n: return '1'
        if 'LQ2' in n: return '2'
        if 'LQ3' in n: return '3'
        return 'other'

    import json as _json
    return render_template('admin/room_schedule_admin.html',
        buildings_json=_json.dumps([{'id': b['buildingid'], 'name': b['buildingname']} for b in buildings]),
        rooms_json=_json.dumps([{
            'id': r['roomid'], 'name': r['roomname'],
            'type': r['roomtype'] or 'Lecture',
            'bid': r['buildingid'], 'bname': r['buildingname'],
            'capacity': r['roomcapacity'] or 0,
            'floor': _room_floor(r['roomname'])
        } for r in rooms])
    )

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


def _ensure_ay_status_col(cur):
    """Add status column to academicyear and migrate existing rows to proper lifecycle statuses."""
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'academicyear' AND column_name = 'status'
    """)
    if not cur.fetchone():
        cur.execute("ALTER TABLE academicyear ADD COLUMN status VARCHAR(20) DEFAULT 'Upcoming'")
        # Migrate existing Finalized AYs
        cur.execute("""
            UPDATE academicyear
            SET status = 'Finalized'
            WHERE COALESCE(isfinalized, FALSE) = TRUE
        """)
    # Add check constraint if missing
    cur.execute("""
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'chk_academicyear_status'
          AND table_name = 'academicyear'
    """)
    if not cur.fetchone():
        try:
            cur.execute("""
                ALTER TABLE academicyear
                ADD CONSTRAINT chk_academicyear_status
                CHECK (status IN ('Upcoming', 'Current', 'Past', 'Finalized'))
            """)
        except Exception:
            pass


def _ensure_restrict_pt_col(cur):
    """Add restrict_pt_hours BOOLEAN DEFAULT TRUE to employeetype if not present."""
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'employeetype' AND column_name = 'restrict_pt_hours'
    """)
    if not cur.fetchone():
        cur.execute("ALTER TABLE employeetype ADD COLUMN restrict_pt_hours BOOLEAN NOT NULL DEFAULT TRUE")


# ═══════════════════════════════════════════════════════════════════════════════
# Academic Year Lifecycle Validation Functions
# ═══════════════════════════════════════════════════════════════════════════════

def _validate_ay_year_order(year_start, year_end):
    """
    Validate that year_start < year_end (prevents invalid AYs like 2023-2022).
    Returns (is_valid, error_message).
    """
    try:
        y_start = int(year_start)
        y_end = int(year_end)

        if y_start >= y_end:
            return False, f"Invalid Academic Year: {year_start}-{year_end}. Start year must be less than end year."

        if y_end - y_start != 1:
            return False, f"Invalid Academic Year: {year_start}-{year_end}. Academic years must span exactly one year (e.g., 2025-2026)."

        return True, None
    except (ValueError, TypeError):
        return False, "Invalid year format. Years must be numeric."


def _validate_ay_not_duplicate(cur, ay_id, is_new=True):
    """
    Check if academic year already exists.
    Returns (is_valid, error_message).
    """
    cur.execute("SELECT academicyearid, status FROM academicyear WHERE academicyearid = %s", (ay_id,))
    existing = cur.fetchone()

    if is_new and existing:
        status = existing[1] if len(existing) > 1 else 'Unknown'
        return False, f"Academic Year {ay_id} already exists with status '{status}'."

    return True, None


def _validate_ay_no_gaps(cur, year_start, year_end, ay_id):
    """
    Prevent skipping years and creating AYs that are already in the past.
    Returns (is_valid, error_message).
    """
    try:
        y_start = int(year_start)
        y_end   = int(year_end)
        current_year = date.today().year

        # Block AYs whose end year is already in the past
        if y_end <= current_year:
            return False, (
                f"Cannot create Academic Year {year_start}–{year_end}. "
                f"That Academic Year is already in the past."
            )

        # Get min and max of existing years (excluding the AY being created/edited)
        cur.execute("""
            SELECT MIN(yearstart), MAX(yearend)
            FROM academicyear
            WHERE academicyearid != %s
              AND yearstart IS NOT NULL AND yearend IS NOT NULL
        """, (ay_id,))
        row = cur.fetchone()

        if not row or row[0] is None:
            # First academic year — no sequence to maintain
            return True, None

        min_year_start = int(row[0])
        max_year_end   = int(row[1])

        # Forward gap: new AY skips years at the top of the sequence
        if y_start > max_year_end + 1:
            next_expected = f"{max_year_end}–{max_year_end + 1}"
            return False, (
                f"Cannot skip years. The next Academic Year to add must be "
                f"AY {next_expected}, not {year_start}–{year_end}."
            )

        # Backward gap: new AY would create a hole below the earliest AY
        if y_end < min_year_start - 1:
            return False, (
                f"Cannot create Academic Year {year_start}–{year_end}. "
                f"It would leave a gap before the existing sequence "
                f"(earliest AY starts {min_year_start})."
            )

        return True, None
    except (ValueError, TypeError, IndexError) as e:
        return False, f"Error validating year sequence: {e}"


def _validate_ay_no_overlap(cur, ay_id, sem_configs):
    """
    Validate that academic year date ranges don't overlap with other AYs.
    sem_configs: list of (label, start_date, end_date, sem_type) tuples.
    Returns (is_valid, error_message).
    """
    from datetime import datetime

    # Get all date ranges for this AY
    this_ay_dates = []
    for label, start_str, end_str, sem_type in sem_configs:
        if start_str and end_str:
            try:
                start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
                this_ay_dates.append((label, start_date, end_date))
            except ValueError:
                continue

    if not this_ay_dates:
        return True, None  # No dates to validate

    # Get all other academic years with their semester dates
    cur.execute("""
        SELECT ay.academicyearid, sem.semestertype, sem.semstartdate, sem.semenddate
        FROM academicyear ay
        JOIN semester sem ON sem.academicyearid = ay.academicyearid
        WHERE ay.academicyearid != %s
        AND sem.semstartdate IS NOT NULL
        AND sem.semenddate IS NOT NULL
        ORDER BY ay.academicyearid, sem.semestertype
    """, (ay_id,))

    other_semesters = cur.fetchall()

    # Check for overlaps
    for this_label, this_start, this_end in this_ay_dates:
        for other_ay_id, other_sem_type, other_start, other_end in other_semesters:
            # Check if date ranges overlap
            if (this_start <= other_end and this_end >= other_start):
                sem_type_labels = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}
                other_sem_label = sem_type_labels.get(other_sem_type, other_sem_type)

                return False, (f"{this_label} ({this_start} to {this_end}) overlaps with "
                             f"{other_ay_id} {other_sem_label} ({other_start} to {other_end}). "
                             f"Academic years cannot have overlapping date ranges.")

    return True, None


def _validate_semester_chronology(sem_configs):
    """
    Validate that semesters follow chronological order within an AY.
    Returns (is_valid, error_message).
    """
    from datetime import datetime

    sems_with_dates = []
    for label, start_str, end_str, sem_type in sem_configs:
        if start_str and end_str:
            try:
                start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
                sems_with_dates.append((label, start_date, end_date, sem_type))
            except ValueError:
                return False, f"{label} has invalid date format."

    if len(sems_with_dates) < 2:
        return True, None  # Not enough semesters to check order

    # Check chronological order
    for i in range(len(sems_with_dates) - 1):
        current_label, current_start, current_end, current_type = sems_with_dates[i]
        next_label, next_start, next_end, next_type = sems_with_dates[i + 1]

        if current_end >= next_start:
            return False, (f"{current_label} (ends {current_end}) must end before "
                         f"{next_label} (starts {next_start}). Semesters must follow chronological order.")

    return True, None


def _check_ay_dependencies(cur, ay_id):
    """
    Check if an academic year has dependent records that would prevent deletion.
    Returns (has_dependencies, dependency_list, count).
    """
    dependencies = []
    total_count = 0

    # Check for schedules
    cur.execute("""
        SELECT COUNT(*) FROM schedule sc
        JOIN semester sem ON sem.semesterid = sc.semesterid
        WHERE sem.academicyearid = %s
    """, (ay_id,))
    schedule_count = cur.fetchone()[0]
    if schedule_count > 0:
        dependencies.append(f"{schedule_count} schedule record(s)")
        total_count += schedule_count

    # Check for published schedules
    cur.execute("""
        SELECT COUNT(*) FROM schedule_version sv
        JOIN schedule sc ON sc.scheduleid = sv.scheduleid
        JOIN semester sem ON sem.semesterid = sc.semesterid
        WHERE sem.academicyearid = %s AND sv.status = 'Published'
    """, (ay_id,))
    published_count = cur.fetchone()[0]
    if published_count > 0:
        dependencies.append(f"{published_count} published schedule(s)")

    # Check for program year levels
    cur.execute("""
        SELECT COUNT(*) FROM program_yearlevel
        WHERE academicyearid = %s
    """, (ay_id,))
    pyl_count = cur.fetchone()[0]
    if pyl_count > 0:
        dependencies.append(f"{pyl_count} program year level record(s)")
        total_count += pyl_count

    # Check for sections
    cur.execute("""
        SELECT COUNT(*) FROM sections sec
        JOIN program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
        WHERE pyl.academicyearid = %s
    """, (ay_id,))
    section_count = cur.fetchone()[0]
    if section_count > 0:
        dependencies.append(f"{section_count} section(s)")
        total_count += section_count

    # Check for historical data
    cur.execute("""
        SELECT COUNT(*) FROM historical_data
        WHERE academicyearid = %s
    """, (ay_id,))
    hist_count = cur.fetchone()[0]
    if hist_count > 0:
        dependencies.append(f"{hist_count} historical data record(s)")
        total_count += hist_count

    return len(dependencies) > 0, dependencies, total_count


@app.route('/admin/settings')
def admin_settings():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        def to_dict(cursor):
            columns =[col[0].lower() for col in cursor.description]
            return[dict(zip(columns, row)) for row in cursor.fetchall()]

        _ensure_ay_finalized_col(cur)
        _ensure_ay_status_col(cur)
        _ensure_restrict_pt_col(cur)
        cur.execute("ALTER TABLE program_yearlevel ADD COLUMN IF NOT EXISTS section_naming_format VARCHAR(30)")
        cur.execute("ALTER TABLE sections ALTER COLUMN sectionname TYPE VARCHAR(100)")

        # Auto-sync AY status from semester dates (skip Finalized — those are permanent)
        cur.execute("""
            UPDATE academicyear ay
            SET status = CASE
                WHEN EXISTS (
                    SELECT 1 FROM semester s
                    WHERE s.academicyearid = ay.academicyearid
                      AND CURRENT_DATE BETWEEN s.semstartdate AND s.semenddate
                ) THEN 'Current'
                WHEN (
                    SELECT COUNT(*) FROM semester s
                    WHERE s.academicyearid = ay.academicyearid
                      AND s.semenddate IS NOT NULL
                ) > 0
                AND NOT EXISTS (
                    SELECT 1 FROM semester s
                    WHERE s.academicyearid = ay.academicyearid
                      AND s.semenddate >= CURRENT_DATE
                ) THEN 'Past'
                ELSE 'Upcoming'
            END
            WHERE COALESCE(ay.isfinalized, FALSE) = FALSE
        """)
        # Keep isactive in sync with the Current AY for backward compatibility
        cur.execute("""
            UPDATE academicyear
            SET isactive = (status = 'Current')
            WHERE COALESCE(isfinalized, FALSE) = FALSE
        """)
        conn.commit()

        # Academic Years are never auto-locked — only finalized AYs are locked.
        is_locked     = False
        locked_detail = ""

        cur.execute('SELECT * FROM vw_academic_year_semesters ORDER BY academicyearid DESC')
        ay_data = to_dict(cur)

        # Get per-AY status, published flag, and invalid-year flag
        cur.execute("""
            SELECT ay.academicyearid,
                   COALESCE(ay.isfinalized, FALSE)                         AS isfinalized,
                   COALESCE(ay.status, 'Upcoming')                         AS status,
                   BOOL_OR(sv.status = 'Published') IS TRUE                AS has_published,
                   COALESCE(ay.yearstart, 0) >= COALESCE(ay.yearend, 1)   AS is_invalid
            FROM   academicyear ay
            LEFT JOIN semester s   ON s.academicyearid  = ay.academicyearid
            LEFT JOIN schedule sc  ON sc.semesterid     = s.semesterid
            LEFT JOIN schedule_version sv ON sv.scheduleid = sc.scheduleid
            GROUP BY ay.academicyearid, ay.isfinalized, ay.status, ay.yearstart, ay.yearend
        """)
        ay_status_map = {row[0]: {
            'isfinalized': row[1],
            'status': row[2],
            'has_published': bool(row[3]),
            'is_invalid': bool(row[4]),
        } for row in cur.fetchall()}

        for ay in ay_data:
            st = ay_status_map.get(ay['academicyearid'], {
                'isfinalized': False,
                'status': 'Upcoming',
                'has_published': False,
                'is_invalid': False,
            })
            ay['isfinalized']     = st['isfinalized']
            ay['has_published']   = st['has_published']
            ay['is_invalid']      = st['is_invalid']
            ay['computed_status'] = st['status'].lower()  # For template compatibility

        cur.execute("SELECT * FROM EmployeeType ORDER BY EmployeeTypeID ASC")
        emp_types = to_dict(cur)
        for et in emp_types:
            _rs, _re = et.get('regular_start'), et.get('regular_end')
            et['reg_time_str'] = (
                f"{format_time(_rs)} - {format_time(_re)}"
                if _rs and _re and _rs != _re else "—"
            )
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

        # ── Active AY for Program Management ────────────────
        cur.execute("""
            SELECT academicyearid FROM academicyear
            WHERE isactive = TRUE
            ORDER BY yearstart DESC LIMIT 1
        """)
        _active_ay_row = cur.fetchone()
        active_ay_id = _active_ay_row[0] if _active_ay_row else None

        # ── Program Management data ──────────────────────────
        cur.execute("""
            SELECT p.programcode, p.programname,
                   COALESCE(p.programtype, 'Undergraduate') AS programtype,
                   p.isactive,
                   COALESCE(p.numyearlevel, 4) AS numyearlevel,
                   COUNT(DISTINCT c.curriculumid)                                    AS curr_count,
                   COUNT(DISTINCT pyl.programyearlevelid) FILTER (WHERE pyl.isactive = TRUE) AS offering_count,
                   COUNT(DISTINCT pyl.programyearlevelid)                            AS yearlevel_count,
                   COUNT(DISTINCT sec.sectionid) FILTER (WHERE sec.isactive = TRUE AND pyl.academicyearid = %s) AS section_count
            FROM   programs p
            LEFT JOIN curriculum       c   ON c.programcode  = p.programcode
            LEFT JOIN program_yearlevel pyl ON pyl.programcode = p.programcode
            LEFT JOIN sections          sec ON sec.programyearlevelid = pyl.programyearlevelid
            GROUP  BY p.programcode, p.programname, p.programtype, p.isactive, p.numyearlevel
            ORDER  BY p.programname
        """, (active_ay_id,))
        programs_mgmt = to_dict(cur)

        cur.execute("""
            SELECT c.curriculumid, c.curriculumcode, c.programcode,
                   c.curriculumyear, c.isactive,
                   COUNT(cs.subjectcode)           AS subj_count,
                   COALESCE(SUM(cs.creditunits), 0) AS total_units
            FROM   curriculum c
            LEFT JOIN curriculumsubject cs ON cs.curriculumid = c.curriculumid
            GROUP  BY c.curriculumid, c.curriculumcode, c.programcode, c.curriculumyear, c.isactive
            ORDER  BY c.programcode, c.curriculumyear DESC
        """)
        curricula_mgmt = to_dict(cur)

        # Show only sections that belong to the active AY
        cur.execute("""
            SELECT sec.sectionid, sec.sectionname, pyl.yearlevel,
                   pyl.programcode, sec.isactive,
                   pyl.programyearlevelid,
                   pyl.programyearlevelid AS cohortid,
                   pyl.academicyearid,
                   COALESCE(curr.curriculumcode, '') AS curriculumcode,
                   EXISTS (
                       SELECT 1 FROM schedule sc2 WHERE sc2.sectionid = sec.sectionid
                   ) AS has_schedule
            FROM   sections sec
            JOIN   program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN curriculum curr ON curr.curriculumid = pyl.curriculumid
            JOIN   programs p    ON pyl.programcode = p.programcode
            WHERE  p.isactive = TRUE
              AND  pyl.academicyearid = %s
            ORDER  BY pyl.programcode, pyl.yearlevel, sec.sectionname
        """, (active_ay_id,))
        sections_mgmt = to_dict(cur)

        # program_yearlevel rows act as "offerings"
        cur.execute("""
            SELECT pyl.programyearlevelid  AS academicofferingid,
                   pyl.programcode         AS offeringcode,
                   pyl.programcode,
                   COALESCE(c.curriculumcode,'') AS offeringdescription,
                   pyl.startacademicyear,
                   COUNT(DISTINCT sec.sectionid) FILTER (WHERE sec.isactive = TRUE) AS numberofsections,
                   pyl.isactive,
                   NULL::text              AS trackname,
                   NULL::text              AS trackcode,
                   NULL::text              AS tracktype,
                   1                       AS yearlevel_count,
                   COUNT(DISTINCT sec.sectionid) FILTER (WHERE sec.isactive = TRUE) AS section_count
            FROM   program_yearlevel pyl
            LEFT JOIN curriculum c ON c.curriculumid = pyl.curriculumid
            LEFT JOIN sections sec ON sec.programyearlevelid = pyl.programyearlevelid
            WHERE  pyl.academicyearid = %s
            GROUP  BY pyl.programyearlevelid, pyl.programcode, c.curriculumcode,
                      pyl.startacademicyear, pyl.isactive
            ORDER  BY pyl.programcode, pyl.startacademicyear DESC
        """, (active_ay_id,))
        offerings_mgmt = to_dict(cur)

        # Per-program year-level management — pick the active-AY PYL row per program+yearlevel.
        # Section counts are scoped to that same AY (not spanning all historical AYs).
        cur.execute("""
            WITH pyl_active AS (
                SELECT DISTINCT ON (programcode, yearlevel)
                    programyearlevelid, programcode, yearlevel, isactive,
                    COALESCE(section_naming_format, '') AS section_naming_format,
                    startacademicyear, academicyearid
                FROM program_yearlevel
                WHERE academicyearid = %s
                ORDER BY programcode, yearlevel, startacademicyear DESC NULLS LAST
            )
            SELECT
                pl.programyearlevelid,
                pl.programcode,
                pl.yearlevel,
                pl.isactive,
                pl.section_naming_format,
                pl.startacademicyear,
                pl.academicyearid,
                (
                    SELECT COUNT(sec.sectionid)
                    FROM sections sec
                    WHERE sec.programyearlevelid = pl.programyearlevelid
                      AND sec.isactive = TRUE
                ) AS active_section_count,
                (
                    SELECT COUNT(sec.sectionid)
                    FROM sections sec
                    WHERE sec.programyearlevelid = pl.programyearlevelid
                ) AS total_section_count
            FROM pyl_active pl
            ORDER BY pl.programcode, pl.yearlevel
        """, (active_ay_id,))
        prog_yearlevel_mgmt = to_dict(cur)

        # Year-level rows from program_yearlevel
        cur.execute("""
            SELECT pyl.programyearlevelid                              AS programyearlevelid,
                   pyl.programyearlevelid                              AS academicofferingid,
                   pyl.yearlevel,
                   pyl.isactive,
                   COUNT(sec.sectionid) FILTER (WHERE sec.isactive = TRUE) AS numberofsections
            FROM   program_yearlevel pyl
            LEFT JOIN sections sec ON sec.programyearlevelid = pyl.programyearlevelid
            GROUP  BY pyl.programyearlevelid, pyl.programcode, pyl.yearlevel, pyl.isactive
            ORDER  BY pyl.programcode, pyl.yearlevel
        """)
        yearlevel_data = to_dict(cur)

        # ── Current AY display labels ────────────────────────
        _SEM_LABEL_MAP = {'A': '1ST SEMESTER', 'B': '2ND SEMESTER', 'C': 'SUMMER'}
        cur.execute("""
            SELECT ay.yearstart, ay.yearend, s.semestertype
            FROM   academicyear ay
            LEFT JOIN semester s ON s.academicyearid = ay.academicyearid AND s.isactive = TRUE
            WHERE  ay.status = 'Current' OR ay.isactive = TRUE
            ORDER  BY ay.isactive DESC NULLS LAST, ay.yearstart DESC
            LIMIT  1
        """)
        _cur_ay = cur.fetchone()
        if _cur_ay:
            current_ay_label  = f"A.Y {_cur_ay[0]} - {_cur_ay[1]}"
            current_sem_label = _SEM_LABEL_MAP.get(_cur_ay[2], _cur_ay[2] or '—')
        else:
            current_ay_label  = "Not Set"
            current_sem_label = "—"

        # Max existing year end — used by JS to enforce no forward gaps and no past AYs
        cur.execute("""
            SELECT COALESCE(MAX(yearend), 0)
            FROM academicyear
            WHERE yearstart IS NOT NULL AND yearend IS NOT NULL
              AND COALESCE(yearstart, 0) < COALESCE(yearend, 1)
        """)
        _max_ye = cur.fetchone()
        max_existing_year_end = int(_max_ye[0]) if _max_ye and _max_ye[0] else 0

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
                               prog_yearlevel_mgmt=prog_yearlevel_mgmt,
                               active_ay_id=active_ay_id,
                               activity_logs=activity_logs,
                               current_ay_label=current_ay_label,
                               current_sem_label=current_sem_label,
                               max_existing_year_end=max_existing_year_end)
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

        # 3. Re-generate program_yearlevel rows and default sections for the newly active AY
        _pyl_cur = conn.cursor(cursor_factory=RealDictCursor)
        _auto_setup_program_yearlevels(_pyl_cur)   # also calls _ensure_default_sections internally
        _ensure_default_sections(_pyl_cur, ay_id)  # explicit call in case AY just became active
        _pyl_cur.close()

        # 4. Check dates for warning only
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
    """
    Finalize an Academic Year - transitions status to 'Finalized' and locks all modifications.
    This is a permanent action that can only be done by Admin.
    """
    if session.get('role') != 'Admin':
        return redirect(url_for('login'))

    ay_id = request.form.get('ay_id', '').strip()
    if not ay_id:
        flash("Invalid Academic Year.", "error")
        return redirect(url_for('admin_settings'))

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        _ensure_ay_finalized_col(cur)
        _ensure_ay_status_col(cur)

        # Get current status
        cur.execute("SELECT status, isfinalized FROM academicyear WHERE academicyearid = %s", (ay_id,))
        ay_row = cur.fetchone()

        if not ay_row:
            flash(f"Academic Year {ay_id} not found.", "error")
            return redirect(url_for('admin_settings'))

        current_status = ay_row[0]
        is_already_finalized = ay_row[1]

        if is_already_finalized:
            flash(f"Academic Year {ay_id} is already Finalized.", "info")
            return redirect(url_for('admin_settings'))

        # Finalize: set status to 'Finalized' and isfinalized flag
        cur.execute("""
            UPDATE academicyear
            SET status = 'Finalized', isfinalized = TRUE
            WHERE academicyearid = %s
        """, (ay_id,))

        conn.commit()

        flash(f"Academic Year {ay_id} has been finalized and is now permanently locked.", "success")
        write_activity_log(
            "Finalized Academic Year",
            f"Academic Year {ay_id} has been marked as Finalized. All settings and schedules are now historically protected and cannot be modified.",
            category='calendar', color=_LOG_COLORS.get('calendar', 'blue')
        )

    except Exception as e:
        conn.rollback()
        flash(f"Error finalizing Academic Year: {e}", "error")
    finally:
        cur.close()
        conn.close()

    return redirect(url_for('admin_settings'))


@app.route('/admin/settings/delete_ay', methods=['POST'])
def delete_ay():
    """
    Delete an Academic Year - only allowed if it has no dependent records.
    Upcoming AYs with no schedules can be deleted.
    AYs with schedules, reports, or other dependencies cannot be deleted.
    """
    if session.get('role') != 'Admin':
        return redirect(url_for('login'))

    ay_id = request.form.get('ay_id', '').strip()
    if not ay_id:
        flash("Invalid Academic Year.", "error")
        return redirect(url_for('admin_settings'))

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        _ensure_ay_finalized_col(cur)
        _ensure_ay_status_col(cur)

        # Get the AY info
        cur.execute("""
            SELECT status, isfinalized, yearstart, yearend
            FROM academicyear WHERE academicyearid = %s
        """, (ay_id,))
        ay_row = cur.fetchone()

        if not ay_row:
            flash(f"Academic Year {ay_id} not found.", "error")
            return redirect(url_for('admin_settings'))

        current_status = ay_row[0]
        is_finalized   = ay_row[1]
        year_start_val = ay_row[2]
        year_end_val   = ay_row[3]

        # Detect invalid AYs (reversed/wrong years, e.g. 2025-2024 stored as yearstart=2025, yearend=2024)
        is_invalid_ay = (
            year_start_val is not None and year_end_val is not None
            and int(year_start_val) >= int(year_end_val)
        )

        # Finalized AYs are permanently locked — even if they somehow have wrong years
        if is_finalized or current_status == 'Finalized':
            flash(f"Cannot delete {ay_id}. Finalized Academic Years are permanently locked.", "error")
            return redirect(url_for('admin_settings'))

        # Only Upcoming AYs or invalid AYs (reversed years) may be deleted
        if current_status != 'Upcoming' and not is_invalid_ay:
            flash(
                f"Cannot delete {ay_id}. Only Upcoming Academic Years can be deleted. "
                f"This Academic Year is currently '{current_status}'.",
                "error"
            )
            return redirect(url_for('admin_settings'))

        # Block if real schedule records exist
        cur.execute("""
            SELECT COUNT(*) FROM schedule sc
            JOIN semester sem ON sem.semesterid = sc.semesterid
            WHERE sem.academicyearid = %s
        """, (ay_id,))
        if cur.fetchone()[0] > 0:
            flash(f"Cannot delete {ay_id}. It has existing schedule records. Remove all schedules first.", "error")
            return redirect(url_for('admin_settings'))

        # Block if historical data exists
        cur.execute("SELECT COUNT(*) FROM historical_data WHERE academicyearid = %s", (ay_id,))
        if cur.fetchone()[0] > 0:
            flash(f"Cannot delete {ay_id}. It contains historical data records that cannot be removed.", "error")
            return redirect(url_for('admin_settings'))

        # Cascade delete: sections → program_yearlevel → semesters → academic year
        cur.execute("""
            DELETE FROM sections
            WHERE programyearlevelid IN (
                SELECT programyearlevelid FROM program_yearlevel WHERE academicyearid = %s
            )
        """, (ay_id,))
        cur.execute("DELETE FROM program_yearlevel WHERE academicyearid = %s", (ay_id,))
        cur.execute("DELETE FROM semester WHERE academicyearid = %s", (ay_id,))
        cur.execute("DELETE FROM academicyear WHERE academicyearid = %s", (ay_id,))

        conn.commit()

        flash(f"Academic Year {ay_id} has been deleted successfully.", "success")
        write_activity_log(
            "Deleted Academic Year",
            f"Academic Year {ay_id} (Upcoming) was deleted with no schedule or historical records.",
            category='calendar', color=_LOG_COLORS.get('calendar', 'blue')
        )

    except Exception as e:
        conn.rollback()
        flash(f"Error deleting Academic Year: {e}", "error")
    finally:
        cur.close()
        conn.close()

    return redirect(url_for('admin_settings'))


@app.route('/admin/settings/upsert_ay', methods=['POST'])
def upsert_ay():
    """
    Create or update an Academic Year with comprehensive lifecycle validation.
    Implements all business rules for Academic Year management.
    """
    if session.get('role') != 'Admin': return redirect(url_for('login'))

    y_start = request.form.get('year_start')
    y_end = request.form.get('year_end')

    # Validate year format
    if not y_start or not y_end:
        flash("Both start and end years are required.", "error")
        return redirect(url_for('admin_settings'))

    ay_id = f"AY{y_start[2:4]}{y_end[2:4]}"

    sem_configs = [
        ('1st Semester', request.form.get('sem1_start'), request.form.get('sem1_end'), 'A'),
        ('2nd Semester', request.form.get('sem2_start'), request.form.get('sem2_end'), 'B'),
        ('Summer', request.form.get('sem3_start'), request.form.get('sem3_end'), 'C')
    ]

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        _ensure_ay_finalized_col(cur)
        _ensure_ay_status_col(cur)

        form_action = request.form.get('form_action', 'edit')

        # Check if this is a new AY or editing existing
        cur.execute("SELECT academicyearid, status, isfinalized FROM academicyear WHERE academicyearid = %s", (ay_id,))
        existing_ay = cur.fetchone()
        is_new = existing_ay is None

        # When submitted from the Add modal, reject if AY already exists
        if form_action == 'add' and existing_ay:
            existing_status = existing_ay[1] if len(existing_ay) > 1 else 'Unknown'
            flash(
                f"Academic Year {y_start}–{y_end} already exists (status: {existing_status}). "
                f"Use the edit button on that row to modify it.",
                "error"
            )
            return redirect(url_for('admin_settings'))
        current_status = existing_ay[1] if existing_ay and len(existing_ay) > 1 else 'Upcoming'
        is_finalized = existing_ay[2] if existing_ay and len(existing_ay) > 2 else False

        # ═══════════════════════════════════════════════════════
        # VALIDATION 1: Prevent editing Finalized Academic Years
        # ═══════════════════════════════════════════════════════
        if is_finalized:
            flash(f"Academic Year {ay_id} is Finalized and cannot be edited. Finalized academic years are permanently locked.", "error")
            return redirect(url_for('admin_settings'))

        # ═══════════════════════════════════════════════════════
        # VALIDATION 2: Year order validation (prevent 2023-2022)
        # ═══════════════════════════════════════════════════════
        valid, error_msg = _validate_ay_year_order(y_start, y_end)
        if not valid:
            flash(error_msg, "error")
            return redirect(url_for('admin_settings'))

        # ═══════════════════════════════════════════════════════
        # VALIDATION 3: Prevent duplicate Academic Years
        # ═══════════════════════════════════════════════════════
        valid, error_msg = _validate_ay_not_duplicate(cur, ay_id, is_new=is_new)
        if not valid:
            flash(error_msg, "error")
            return redirect(url_for('admin_settings'))

        # ═══════════════════════════════════════════════════════
        # VALIDATION 4: Prevent skipping years
        # ═══════════════════════════════════════════════════════
        if is_new:
            valid, error_msg = _validate_ay_no_gaps(cur, y_start, y_end, ay_id)
            if not valid:
                flash(error_msg, "error")
                return redirect(url_for('admin_settings'))

        # ═══════════════════════════════════════════════════════
        # VALIDATION 5: Validate semester dates within each semester
        # ═══════════════════════════════════════════════════════
        for label, s_start, s_end, s_type in sem_configs:
            if s_start and s_end:
                if s_end < s_start:
                    flash(f"Validation Error: {label} end date cannot be earlier than start date.", "error")
                    return redirect(url_for('admin_settings'))

                try:
                    _span_days = (datetime.strptime(s_end, '%Y-%m-%d') - datetime.strptime(s_start, '%Y-%m-%d')).days
                    if _span_days // 7 < 15:
                        flash(f"Validation Error: {label} must span at least 15 weeks.", "error")
                        return redirect(url_for('admin_settings'))
                except ValueError:
                    pass

                try:
                    start_year_val = datetime.strptime(s_start, '%Y-%m-%d').year
                    end_year_val = datetime.strptime(s_end, '%Y-%m-%d').year
                    allowed_years = [int(y_start), int(y_end)]
                    if start_year_val not in allowed_years or end_year_val not in allowed_years:
                        flash(f"Validation Error: {label} dates must fall within the years {y_start} or {y_end}.", "error")
                        return redirect(url_for('admin_settings'))
                except ValueError:
                    flash(f"Validation Error: {label} has invalid date format.", "error")
                    return redirect(url_for('admin_settings'))

        # ═══════════════════════════════════════════════════════
        # VALIDATION 6: Semester chronological order
        # ═══════════════════════════════════════════════════════
        valid, error_msg = _validate_semester_chronology(sem_configs)
        if not valid:
            flash(error_msg, "error")
            return redirect(url_for('admin_settings'))

        # ═══════════════════════════════════════════════════════
        # VALIDATION 7: No overlapping date ranges with other AYs
        # ═══════════════════════════════════════════════════════
        valid, error_msg = _validate_ay_no_overlap(cur, ay_id, sem_configs)
        if not valid:
            flash(error_msg, "error")
            return redirect(url_for('admin_settings'))

        # ═══════════════════════════════════════════════════════
        # VALIDATION 8: Past AYs with published schedules
        # Can extend semester dates but not move them to past
        # ═══════════════════════════════════════════════════════
        if current_status == 'Past' or (existing_ay and not is_new):
            cur.execute("""
                SELECT COUNT(*) FROM schedule_version sv
                JOIN schedule sc ON sc.scheduleid = sv.scheduleid
                JOIN semester s  ON s.semesterid  = sc.semesterid
                WHERE s.academicyearid = %s AND sv.status = 'Published'
            """, (ay_id,))

            if cur.fetchone()[0] > 0:
                today_str = date.today().strftime('%Y-%m-%d')
                cur.execute("""
                    SELECT semestertype, semstartdate, semenddate
                    FROM semester WHERE academicyearid = %s
                """, (ay_id,))
                existing_sems = {row[0]: (str(row[1]) if row[1] else None, str(row[2]) if row[2] else None)
                                 for row in cur.fetchall()}

                for label, s_start, s_end, s_type in sem_configs:
                    old_start, old_end = existing_sems.get(s_type, (None, None))

                    # Only reject if moving dates backwards to the past
                    if s_start and s_start != old_start and s_start < today_str:
                        flash(f"Validation Error: {label} start date cannot be set to a past date for Academic Years with published schedules.", "error")
                        return redirect(url_for('admin_settings'))

                    # Allow extending end dates, but not moving them to the past
                    if s_end and s_end != old_end and old_end and s_end < old_end:
                        flash(f"Validation Error: {label} end date cannot be moved earlier for Academic Years with published schedules. You may only extend semester dates.", "error")
                        return redirect(url_for('admin_settings'))

        # ═══════════════════════════════════════════════════════
        # Save Academic Year
        # ═══════════════════════════════════════════════════════
        if is_new:
            # New academic year starts as 'Upcoming'
            cur.execute("""
                INSERT INTO AcademicYear (AcademicYearID, YearStart, YearEnd, IsActive, status)
                VALUES (%s, %s, %s, FALSE, 'Upcoming')
            """, (ay_id, int(y_start), int(y_end)))
        else:
            # Update existing academic year (keep current status)
            cur.execute("""
                UPDATE AcademicYear
                SET YearStart = %s, YearEnd = %s
                WHERE AcademicYearID = %s
            """, (int(y_start), int(y_end), ay_id))

        # Save semester dates
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

        # Auto-create program_yearlevel rows + default sections for new AYs
        if is_new:
            try:
                _pyl_cur = conn.cursor(cursor_factory=RealDictCursor)
                _auto_setup_program_yearlevels(_pyl_cur)
                _ensure_default_sections(_pyl_cur, ay_id)
                _pyl_cur.close()
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

        action_verb = "Created" if is_new else "Updated"
        flash(f"Academic Year {ay_id} configuration saved successfully.", "success")

        sems_set = [lbl for lbl, ss, se, _ in sem_configs if ss and se]
        write_activity_log(
            f"{action_verb} Academic Calendar",
            f'{action_verb} start and end dates for Academic Year {y_start}-{y_end}'
            + (f' ({", ".join(sems_set)})' if sems_set else ''),
            category='calendar', color=_LOG_COLORS.get('calendar', 'blue')
        )

    except Exception as e:
        conn.rollback()
        flash(f"Database Error: {str(e)}", "error")
    finally:
        cur.close()
        conn.close()

    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/update_emp_type', methods=['POST'])
def update_emp_type():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        et_id = request.form.get('emp_type_id')
        r_load = request.form.get('reg_load') or None
        pt_load = request.form.get('pt_load') or None
        sub_load = request.form.get('sub_load') or None
        rs = request.form.get('reg_start') or None
        re = request.form.get('reg_end') or None
        ps = request.form.get('pt_start') or None
        pe = request.form.get('pt_end') or None
        restrict_pt = request.form.get('restrict_pt', 'true') == 'true'

        cur.execute("""
            UPDATE EmployeeType
            SET RegularLoad=%s, PartTimeLoad=%s, TeachingSubstitution=%s,
                Regular_Start=%s, Regular_End=%s, PartTime_Start=%s, PartTime_End=%s,
                restrict_pt_hours=%s
            WHERE EmployeeTypeID=%s
        """, (r_load, pt_load, sub_load, rs, re, ps, pe, restrict_pt, et_id))
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
            # Auto-generate program_yearlevel rows for the new program
            _pyl_cur = conn.cursor(cursor_factory=RealDictCursor)
            _auto_setup_program_yearlevels(_pyl_cur)
            _pyl_cur.close()
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
        # Re-generate program_yearlevel rows (numyearlevel may have changed)
        _pyl_cur = conn.cursor(cursor_factory=RealDictCursor)
        _auto_setup_program_yearlevels(_pyl_cur)
        _pyl_cur.close()
        conn.commit()
        flash(f"Program '{code}' updated.", "success")
        write_activity_log("Updated Program Configuration", f'Modified program details for {code}: {name}',
                           category='settings', color=_LOG_COLORS['settings'])
    except Exception as e:
        conn.rollback(); flash(f"Error updating program: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_settings'))

@app.route('/admin/settings/program/yearlevel/toggle', methods=['POST'])
def settings_toggle_program_yearlevel():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    prog_code = request.form.get('program_code', '').strip()
    yearlevel = request.form.get('yearlevel', '')
    isactive  = request.form.get('isactive') == 'true'
    if not prog_code or not yearlevel:
        return jsonify({'success': False, 'error': 'Missing parameters.'})
    conn = None; cur = None
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("""
            UPDATE program_yearlevel SET isactive = %s
            WHERE programcode = %s AND yearlevel = %s
        """, (isactive, prog_code, int(yearlevel)))
        updated = cur.rowcount
        conn.commit()
        return jsonify({'success': True, 'updated': updated, 'isactive': isactive})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        if cur:  cur.close()
        if conn: conn.close()

@app.route('/admin/settings/program/delete', methods=['POST'])
def settings_delete_program():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    code = request.form.get('program_code', '').strip()
    conn = get_db_connection(); cur = conn.cursor()
    try:
        # ── Block deactivation if program has sections in the active semester ──
        cur.execute("""
            SELECT COUNT(*) FROM schedule sc
            JOIN semester sem ON sem.semesterid = sc.semesterid
            JOIN sections sec ON sec.sectionid  = sc.sectionid
            JOIN program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
            WHERE UPPER(pyl.programcode) = UPPER(%s) AND sem.isactive = TRUE
        """, (code,))
        if cur.fetchone()[0] > 0:
            conn.rollback()
            flash(f"Cannot deactivate program '{code}': it has sections assigned to the active semester schedule.", "error")
            return redirect(url_for('admin_settings'))

        # ── Cascade deactivation ─────────────────────────────────
        cur.execute("UPDATE programs SET isactive=FALSE WHERE programcode=%s", (code,))
        cur.execute("""
            UPDATE sections SET isactive=FALSE
            WHERE programyearlevelid IN (
                SELECT programyearlevelid FROM program_yearlevel WHERE UPPER(programcode)=UPPER(%s)
            )
        """, (code,))
        cur.execute("UPDATE program_yearlevel SET isactive=FALSE WHERE UPPER(programcode)=UPPER(%s)", (code,))

        conn.commit()
        flash(f"Program '{code}' deactivated along with its cohorts and sections.", "success")
        write_activity_log("Deactivated Program",
                           f'Program {code} and all its cohorts/sections marked inactive',
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
            parts = year.replace(' ','').split('-')
            code = 'CY' + (parts[0][-2:] if parts else '') + (parts[1][-2:] if len(parts)>1 else '')
        cur.execute("SELECT 1 FROM programs WHERE programcode = %s", (pcode,))
        if not cur.fetchone():
            flash(f"Program '{pcode}' not found.", "error")
            return redirect(url_for('admin_settings'))
        cur.execute("""
            INSERT INTO curriculum (curriculumcode, programcode, curriculumyear)
            VALUES (%s, %s, %s)
        """, (code, pcode, year))
        _reassign_curriculum_for_program(cur, pcode)
        conn.commit()
        flash(f"Curriculum '{code}' added to {pcode}.", "success")
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
            WHERE CurriculumID=%s RETURNING programcode
        """, (code, year, cid))
        row = cur.fetchone()
        if row:
            _reassign_curriculum_for_program(cur, row[0] if not isinstance(row, dict) else row['programcode'])
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

@app.route('/admin/settings/curriculum/toggle-active', methods=['POST'])
def settings_toggle_curriculum_active():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    cid      = request.form.get('curriculum_id')
    isactive = request.form.get('isactive') == 'true'
    if not cid:
        return jsonify({'success': False, 'error': 'Curriculum ID required.'})
    conn = None; cur = None
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("UPDATE curriculum SET isactive=%s WHERE curriculumid=%s RETURNING curriculumcode", (isactive, cid))
        row = cur.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Curriculum not found.'})
        conn.commit()
        try:
            write_activity_log(
                f"{'Activated' if isactive else 'Deactivated'} Curriculum",
                f"Curriculum ID {cid} ({row[0]}) set to {'active' if isactive else 'inactive'}",
                category='curriculum', color=_LOG_COLORS.get('curriculum', 'blue'))
        except Exception:
            pass
        return jsonify({'success': True, 'isactive': isactive, 'curriculum_id': int(cid)})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except Exception: pass
        return jsonify({'success': False, 'error': str(e)})
    finally:
        if cur:  cur.close()
        if conn: conn.close()


## ── Section Management CRUD ─────────────────────────────────

@app.route('/admin/settings/program/yearlevel/update', methods=['POST'])
def settings_update_program_yearlevel():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    pyl_id        = request.form.get('pyl_id', '').strip()
    is_active     = request.form.get('isactive') == 'true'
    num_sections  = int(request.form.get('num_sections', 0) or 0)
    naming_format = request.form.get('section_naming_format', '').strip().upper()
    if not pyl_id:
        return jsonify({'success': False, 'error': 'Missing pyl_id.'})
    conn = None; cur = None
    try:
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT pyl.programcode, pyl.yearlevel
            FROM program_yearlevel pyl WHERE pyl.programyearlevelid = %s
        """, (pyl_id,))
        pyl_row = cur.fetchone()
        if not pyl_row:
            return jsonify({'success': False, 'error': 'Year level not found.'})
        prog_code = pyl_row['programcode']
        yearlevel = pyl_row['yearlevel']
        prefix    = naming_format or (prog_code + str(yearlevel))

        if is_active:
            # If activating, ensure at least 1 section will exist
            if num_sections == 0:
                cur.execute("SELECT COUNT(*) FROM sections WHERE programyearlevelid=%s AND isactive=TRUE", (pyl_id,))
                if cur.fetchone()['count'] == 0:
                    num_sections = 1  # auto-create default

            # Sync sections to match target count
            if num_sections > 0:
                suffix_letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
                target_names = [prefix] + [prefix + suffix_letters[i] for i in range(num_sections - 1)]
                cur.execute("SELECT sectionid, sectionname FROM sections WHERE programyearlevelid=%s", (pyl_id,))
                existing = cur.fetchall()
                existing_names = {r['sectionname'] for r in existing}
                for name in target_names:
                    if name not in existing_names:
                        cur.execute("""
                            INSERT INTO sections (programyearlevelid, sectionname, isactive)
                            VALUES (%s, %s, TRUE)
                            ON CONFLICT (programyearlevelid, sectionname) DO NOTHING
                        """, (pyl_id, name))
                for sec in existing:
                    if sec['sectionname'] in target_names:
                        # Reactivate sections that match the target naming (e.g. this
                        # year level was previously deactivated and is being turned back on)
                        cur.execute("UPDATE sections SET isactive=TRUE WHERE sectionid=%s", (sec['sectionid'],))
                    else:
                        cur.execute("SELECT COUNT(*) FROM schedule WHERE sectionid=%s", (sec['sectionid'],))
                        if cur.fetchone()['count'] == 0:
                            cur.execute("DELETE FROM sections WHERE sectionid=%s", (sec['sectionid'],))
                        else:
                            cur.execute("UPDATE sections SET isactive=FALSE WHERE sectionid=%s", (sec['sectionid'],))
        else:
            # Deactivating this year level must cascade to ALL its sections, regardless
            # of naming — they should no longer appear as active anywhere in the system.
            cur.execute("UPDATE sections SET isactive=FALSE WHERE programyearlevelid=%s", (pyl_id,))

        # Update the pyl row
        cur.execute("""
            UPDATE program_yearlevel
            SET isactive=%s, section_naming_format=%s
            WHERE programyearlevelid=%s
        """, (is_active, naming_format or None, pyl_id))

        # Cascade program active status
        if not is_active:
            cur.execute("""
                SELECT COUNT(*) FROM program_yearlevel
                WHERE programcode=%s AND isactive=TRUE AND programyearlevelid != %s
            """, (prog_code, pyl_id))
            if cur.fetchone()['count'] == 0:
                cur.execute("UPDATE programs SET isactive=FALSE WHERE programcode=%s", (prog_code,))
        else:
            cur.execute("UPDATE programs SET isactive=TRUE WHERE programcode=%s", (prog_code,))

        # Return updated sections
        cur.execute("""
            SELECT sectionid, sectionname, isactive
            FROM sections WHERE programyearlevelid=%s ORDER BY sectionname
        """, (pyl_id,))
        sections = [dict(r) for r in cur.fetchall()]
        active_count = sum(1 for s in sections if s['isactive'])

        # Re-check program active
        cur.execute("SELECT isactive FROM programs WHERE programcode=%s", (prog_code,))
        prog_row = cur.fetchone()
        prog_active = bool(prog_row['isactive']) if prog_row else False

        conn.commit()
        try:
            write_activity_log(
                'Updated Program Year Level',
                f'Year {yearlevel} of {prog_code}: {"activated" if is_active else "deactivated"}, '
                f'{num_sections} sections, prefix={prefix}',
                category='program', color=_LOG_COLORS.get('program', 'blue')
            )
        except: pass
        return jsonify({
            'success': True, 'pyl_id': int(pyl_id),
            'prog_code': prog_code, 'yearlevel': yearlevel,
            'isactive': is_active, 'active_section_count': active_count,
            'sections': sections, 'prog_isactive': prog_active,
            'section_naming_format': naming_format or '',
        })
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        if cur:  cur.close()
        if conn: conn.close()


@app.route('/admin/settings/section/add', methods=['POST'])
def settings_add_section():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    prog_code    = request.form.get('program_code', '').strip().upper()
    year_level   = int(request.form.get('year_level', '1'))
    section_name = request.form.get('section_name', '').strip()
    ay_id        = request.form.get('ay_id', '').strip()
    if not prog_code or not section_name:
        flash("Program code and section name are required.", "error")
        return redirect(url_for('admin_settings'))
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # If no AY specified, use the most recent active AY
        if not ay_id:
            cur.execute("SELECT academicyearid FROM academicyear WHERE isactive=TRUE ORDER BY yearstart DESC LIMIT 1")
            _ay = cur.fetchone()
            ay_id = _ay['academicyearid'] if _ay else None

        # Find the program_yearlevel row for this program + year_level + active AY
        pyl_row = None
        if ay_id:
            cur.execute("""
                SELECT pyl.programyearlevelid
                FROM program_yearlevel pyl
                WHERE UPPER(pyl.programcode) = UPPER(%s) AND pyl.yearlevel = %s
                  AND pyl.academicyearid = %s
                ORDER BY pyl.startacademicyear DESC NULLS LAST
                LIMIT 1
            """, (prog_code, year_level, ay_id))
            pyl_row = cur.fetchone()
        # Fallback: any active pyl row
        if not pyl_row:
            cur.execute("""
                SELECT pyl.programyearlevelid
                FROM program_yearlevel pyl
                WHERE UPPER(pyl.programcode) = UPPER(%s) AND pyl.yearlevel = %s AND pyl.isactive = TRUE
                ORDER BY pyl.startacademicyear DESC NULLS LAST
                LIMIT 1
            """, (prog_code, year_level))
            pyl_row = cur.fetchone()

        if not pyl_row:
            # Auto-create program_yearlevel row using _auto_setup_program_yearlevels
            _auto_setup_program_yearlevels(cur)
            cur.execute("""
                SELECT pyl.programyearlevelid
                FROM program_yearlevel pyl
                WHERE UPPER(pyl.programcode) = UPPER(%s) AND pyl.yearlevel = %s AND pyl.isactive = TRUE
                ORDER BY pyl.startacademicyear DESC NULLS LAST
                LIMIT 1
            """, (prog_code, year_level))
            pyl_row = cur.fetchone()
            if not pyl_row:
                flash(f"No active program_yearlevel found for '{prog_code}' Year {year_level}. Ensure the program and AY are active.", "warning")
                return redirect(url_for('admin_settings'))

        pyl_id = pyl_row['programyearlevelid']

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
                ON CONFLICT (programyearlevelid, sectionname) DO NOTHING
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
            cur.execute("UPDATE programs SET isactive=TRUE WHERE programcode=%s", (prog_code,))
            cur.execute("UPDATE program_yearlevel SET isactive=TRUE WHERE UPPER(programcode)=UPPER(%s)", (prog_code,))
            cur.execute("""
                UPDATE sections SET isactive=TRUE
                WHERE programyearlevelid IN (
                    SELECT programyearlevelid FROM program_yearlevel WHERE UPPER(programcode)=UPPER(%s)
                )
            """, (prog_code,))
        else:
            cur.execute("UPDATE programs SET isactive=FALSE WHERE programcode=%s", (prog_code,))
            cur.execute("UPDATE program_yearlevel SET isactive=FALSE WHERE UPPER(programcode)=UPPER(%s)", (prog_code,))
            cur.execute("""
                UPDATE sections SET isactive=FALSE
                WHERE programyearlevelid IN (
                    SELECT programyearlevelid FROM program_yearlevel WHERE UPPER(programcode)=UPPER(%s)
                )
            """, (prog_code,))

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
            # pyl_id is the actual programyearlevelid
            cur.execute("""
                INSERT INTO sections (programyearlevelid, sectionname, isactive)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (programyearlevelid, sectionname) DO UPDATE
                    SET sectionname = EXCLUDED.sectionname
                RETURNING sectionid, sectionname
            """, (int(pyl_id), new_name))
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
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        if not section_id:
            return jsonify({'success': False, 'error': 'Section ID is required.'})

        # Resolve program_yearlevel and program
        cur.execute("""
            SELECT pyl.programyearlevelid, pyl.programcode, pyl.yearlevel
            FROM sections s
            JOIN program_yearlevel pyl ON s.programyearlevelid = pyl.programyearlevelid
            WHERE s.sectionid = %s
        """, (section_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Section not found.'})
        pyl_id   = row['programyearlevelid']
        prog_code = row['programcode']

        # Block if section is in an active-semester schedule
        cur.execute("""
            SELECT COUNT(*) FROM schedule sc
            JOIN semester sem ON sem.semesterid = sc.semesterid
            WHERE sc.sectionid = %s AND sem.isactive = TRUE
        """, (section_id,))
        if cur.fetchone()['count'] > 0:
            return jsonify({'success': False,
                            'error': 'Cannot remove this section: it is assigned to the active semester schedule.'})

        cur.execute("SELECT COUNT(*) FROM schedule WHERE sectionid=%s", (section_id,))
        sch_count = cur.fetchone()['count']
        if sch_count > 0:
            cur.execute("UPDATE sections SET isactive=FALSE WHERE sectionid=%s", (section_id,))
            mode = 'soft'
        else:
            cur.execute("DELETE FROM sections WHERE sectionid=%s", (section_id,))
            mode = 'hard'

        # Count remaining active sections for this program_yearlevel
        cur.execute("SELECT COUNT(*) FROM sections WHERE programyearlevelid=%s AND isactive=TRUE", (pyl_id,))
        new_sec_count = cur.fetchone()['count']
        pyl_active = new_sec_count > 0
        if not pyl_active:
            cur.execute("UPDATE program_yearlevel SET isactive=FALSE WHERE programyearlevelid=%s", (pyl_id,))

        cur.execute("SELECT isactive FROM programs WHERE programcode=%s", (prog_code,))
        prog_row   = cur.fetchone()
        prog_active = bool(prog_row['isactive']) if prog_row else False

        conn.commit()
        try:
            write_activity_log("Deleted Section",
                               f"Section ID {section_id} {'deactivated' if mode=='soft' else 'removed'}",
                               category='program', color=_LOG_COLORS.get('program', 'blue'))
        except: pass

        return jsonify({
            'success':          True,
            'mode':             mode,
            'sectionid':        int(section_id),
            'pyl_id':           pyl_id,
            'numberofsections': new_sec_count,
            'pyl_isactive':     pyl_active,
            'ao_id':            pyl_id,
            'ao_isactive':      pyl_active,
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

    prog_code = request.form.get('program_code', '').strip().upper()
    is_active = request.form.get('status') == '1'

    if not prog_code:
        return jsonify({'success': False, 'error': 'Program code is required.'})

    conn = None; cur = None
    try:
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)

        # Validate program exists
        cur.execute("SELECT numyearlevel FROM programs WHERE programcode=%s", (prog_code,))
        prow = cur.fetchone()
        if not prow:
            return jsonify({'success': False, 'error': f"Program '{prog_code}' not found."})
        num_yr = int(prow['numyearlevel']) if prow['numyearlevel'] else 4

        # Run auto-setup to create program_yearlevel rows for this program
        _auto_setup_program_yearlevels(cur)

        if is_active:
            cur.execute("UPDATE programs SET isactive=TRUE WHERE programcode=%s AND isactive=FALSE", (prog_code,))
            cur.execute("UPDATE program_yearlevel SET isactive=TRUE WHERE UPPER(programcode)=UPPER(%s)", (prog_code,))
            cur.execute("""
                UPDATE sections SET isactive=TRUE
                WHERE programyearlevelid IN (
                    SELECT programyearlevelid FROM program_yearlevel WHERE UPPER(programcode)=UPPER(%s)
                )
            """, (prog_code,))

        # Build payload from program_yearlevel rows
        cur.execute("""
            SELECT pyl.programyearlevelid, pyl.yearlevel, pyl.isactive,
                   pyl.startacademicyear, c.curriculumcode
            FROM program_yearlevel pyl
            LEFT JOIN curriculum c ON c.curriculumid = pyl.curriculumid
            WHERE UPPER(pyl.programcode) = UPPER(%s)
            ORDER BY pyl.yearlevel
        """, (prog_code,))
        pyl_rows = cur.fetchall() or []

        created_sections = []
        yearlevel_payload = []
        total_sections = 0

        _LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        for prow2 in pyl_rows:
            pyl_id   = prow2['programyearlevelid']
            yr       = prow2['yearlevel']
            yr_active = prow2['isactive']
            raw_sec   = request.form.get(f'sections_yr_{yr}', '1')
            sec_count = max(1, int(raw_sec) if str(raw_sec).isdigit() else 1)
            curr_code = prow2['curriculumcode'] or ''

            if yr_active:
                for j in range(sec_count):
                    sec_name = f"{prog_code}-{yr}{_LETTERS[j]}"
                    cur.execute("""
                        INSERT INTO sections (programyearlevelid, sectionname, isactive)
                        VALUES (%s, %s, TRUE)
                        ON CONFLICT (programyearlevelid, sectionname) DO NOTHING
                        RETURNING sectionid
                    """, (pyl_id, sec_name))
                    row = cur.fetchone()
                    if row:
                        total_sections += 1
                        created_sections.append({
                            'sectionid':          row['sectionid'],
                            'sectionname':        sec_name,
                            'programyearlevelid': pyl_id,
                            'academicofferingid': pyl_id,
                            'yearlevel':          yr,
                            'programcode':        prog_code,
                            'isactive':           True,
                        })

            yearlevel_payload.append({
                'programyearlevelid': pyl_id,
                'academicofferingid': pyl_id,
                'yearlevel':          yr,
                'isactive':           yr_active,
                'numberofsections':   sec_count if yr_active else 0,
            })

        conn.commit()
        print(f"[AddOffering] COMMIT — program_yearlevel for '{prog_code}' auto-setup with {len(created_sections)} sections.")

        cur.execute("SELECT isactive FROM programs WHERE programcode=%s", (prog_code,))
        prog_row = cur.fetchone()
        prog_isactive = bool(prog_row['isactive']) if prog_row else True

        start_ay = pyl_rows[0]['startacademicyear'] if pyl_rows else ''
        offering_payload = {
            'academicofferingid':  pyl_rows[0]['programyearlevelid'] if pyl_rows else 0,
            'offeringcode':        prog_code,
            'offeringdescription': curr_code,
            'programcode':         prog_code,
            'startacademicyear':   start_ay,
            'numberofsections':    total_sections,
            'isactive':            is_active,
            'trackname':           '',
            'trackcode':           '',
            'tracktype':           '',
            'yearlevel_count':     num_yr,
            'section_count':       len(created_sections),
        }

        try:
            write_activity_log("Added Program Yearlevels",
                               f"Auto-setup program_yearlevel for {prog_code} / AY {start_ay}",
                               category='program', color=_LOG_COLORS.get('program', 'blue'))
        except Exception:
            pass

        return jsonify({
            'success':       True,
            'message':       f"Program '{prog_code}' yearlevels created/updated successfully.",
            'offering':      offering_payload,
            'yearlevels':    yearlevel_payload,
            'sections':      created_sections,
            'prog_isactive': prog_isactive,
        })

    except Exception as e:
        if conn:
            try: conn.rollback()
            except Exception: pass
        import traceback
        print(f"[AddOffering] ERROR: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)})
    finally:
        if cur:  cur.close()
        if conn: conn.close()


@app.route('/admin/settings/offering/edit', methods=['POST'])
def settings_edit_offering():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    ao_id     = request.form.get('offering_id')
    is_active = request.form.get('status') == '1'

    if not ao_id:
        return jsonify({'success': False, 'error': 'Offering ID is required.'})

    conn = None; cur = None
    try:
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)

        # Read current program_yearlevel (ao_id = programyearlevelid)
        cur.execute("""
            SELECT pyl.programyearlevelid, pyl.programcode, pyl.yearlevel,
                   pyl.isactive, pyl.startacademicyear,
                   COALESCE(c.curriculumcode, '') AS curriculumcode
            FROM program_yearlevel pyl
            LEFT JOIN curriculum c ON c.curriculumid = pyl.curriculumid
            WHERE pyl.programyearlevelid = %s
        """, (ao_id,))
        pyl_row = cur.fetchone()
        if not pyl_row:
            return jsonify({'success': False, 'error': 'Program year-level record not found.'})
        prog_code  = pyl_row['programcode']
        prev_active = pyl_row['isactive']
        curr_code  = pyl_row['curriculumcode']

        # Schedule protection: block deactivation if active semester has scheduled sections
        if prev_active and not is_active:
            cur.execute("""
                SELECT COUNT(*) FROM schedule sc
                JOIN semester sem ON sem.semesterid = sc.semesterid
                JOIN sections sec ON sec.sectionid = sc.sectionid
                WHERE sec.programyearlevelid = %s AND sem.isactive = TRUE
            """, (ao_id,))
            if cur.fetchone()['count'] > 0:
                return jsonify({'success': False,
                                'error': 'Cannot deactivate: sections are assigned to the active academic year schedule.'})

        # Get program's year count
        cur.execute("SELECT numyearlevel FROM programs WHERE programcode=%s", (prog_code,))
        prow = cur.fetchone()
        num_yr = int(prow['numyearlevel']) if prow and prow['numyearlevel'] else 4

        # Update program_yearlevel isactive
        cur.execute("UPDATE program_yearlevel SET isactive=%s WHERE programyearlevelid=%s", (is_active, ao_id))

        # Sync sections for this program_yearlevel
        _LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        yr      = pyl_row['yearlevel']
        raw_sec = request.form.get(f'sections_yr_{yr}', '1')
        sec_count = max(1, int(raw_sec) if str(raw_sec).isdigit() else 1)
        yr_active = is_active

        cur.execute("""
            SELECT sectionid, sectionname, isactive FROM sections
            WHERE programyearlevelid=%s ORDER BY sectionname
        """, (ao_id,))
        all_secs      = cur.fetchall()
        active_secs   = [r for r in all_secs if r['isactive']]
        inactive_secs = [r for r in all_secs if not r['isactive']]
        active_count  = len(active_secs)
        all_names     = {r['sectionname'] for r in all_secs}

        if yr_active:
            if sec_count > active_count:
                needed = sec_count - active_count
                reactivated = 0
                for r in inactive_secs:
                    if reactivated >= needed: break
                    cur.execute("UPDATE sections SET isactive=TRUE WHERE sectionid=%s", (r['sectionid'],))
                    reactivated += 1
                if reactivated < needed:
                    still_needed = needed - reactivated
                    inserted = 0
                    for j in range(26):
                        if inserted >= still_needed: break
                        sec_name = f"{prog_code}-{yr}{_LETTERS[j]}"
                        if sec_name not in all_names:
                            cur.execute("""
                                INSERT INTO sections (programyearlevelid, sectionname, isactive)
                                VALUES (%s, %s, TRUE)
                                ON CONFLICT (programyearlevelid, sectionname) DO NOTHING
                            """, (ao_id, sec_name))
                            if cur.rowcount > 0:
                                inserted += 1
                                all_names.add(sec_name)
            elif sec_count < active_count:
                for r in active_secs[sec_count:]:
                    cur.execute("UPDATE sections SET isactive=FALSE WHERE sectionid=%s", (r['sectionid'],))
        else:
            cur.execute(
                "UPDATE sections SET isactive=FALSE WHERE programyearlevelid=%s AND isactive=TRUE",
                (ao_id,)
            )

        # Cascade program state
        if is_active:
            cur.execute("UPDATE programs SET isactive=TRUE WHERE programcode=%s AND isactive=FALSE", (prog_code,))
        else:
            cur.execute("SELECT COUNT(*) FROM program_yearlevel WHERE UPPER(programcode)=UPPER(%s) AND isactive=TRUE", (prog_code,))
            if cur.fetchone()['count'] == 0:
                cur.execute("UPDATE programs SET isactive=FALSE WHERE programcode=%s", (prog_code,))

        cur.execute("SELECT isactive FROM programs WHERE programcode=%s", (prog_code,))
        prog_row = cur.fetchone()
        prog_isactive = bool(prog_row['isactive']) if prog_row else True

        # Build final sections payload
        cur.execute("""
            SELECT sectionid, sectionname, isactive FROM sections
            WHERE programyearlevelid=%s ORDER BY sectionname
        """, (ao_id,))
        final_secs = cur.fetchall()
        total_sections = sum(1 for r in final_secs if r['isactive'])

        conn.commit()
        print(f"[EditOffering] COMMIT — program_yearlevel ID {ao_id} updated.")

        updated_yls = [{
            'programyearlevelid': int(ao_id),
            'academicofferingid': int(ao_id),
            'yearlevel':          yr,
            'isactive':           yr_active,
            'numberofsections':   total_sections,
        }]
        all_sections = [{
            'sectionid':          r['sectionid'],
            'sectionname':        r['sectionname'],
            'programyearlevelid': int(ao_id),
            'academicofferingid': int(ao_id),
            'yearlevel':          yr,
            'programcode':        prog_code,
            'isactive':           r['isactive'],
        } for r in final_secs]

        offering_payload = {
            'academicofferingid':  int(ao_id),
            'offeringcode':        prog_code,
            'offeringdescription': curr_code,
            'programcode':         prog_code,
            'isactive':            is_active,
            'trackname':           '',
            'trackcode':           '',
            'tracktype':           '',
            'yearlevel_count':     num_yr,
            'section_count':       total_sections,
        }

        try:
            write_activity_log("Updated Program Year-Level",
                               f"Modified program_yearlevel ID {ao_id} for {prog_code}",
                               category='program', color=_LOG_COLORS.get('program', 'blue'))
        except Exception:
            pass

        return jsonify({
            'success':       True,
            'message':       'Program year-level updated successfully.',
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
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)

        # Read program_yearlevel info (ao_id = programyearlevelid)
        cur.execute("""
            SELECT pyl.programyearlevelid, pyl.programcode,
                   COALESCE(c.curriculumcode, '') AS curriculumcode
            FROM program_yearlevel pyl
            LEFT JOIN curriculum c ON c.curriculumid = pyl.curriculumid
            WHERE pyl.programyearlevelid = %s
        """, (ao_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Program year-level record not found.'})
        prog_code = row['programcode']
        curr_code = row['curriculumcode']

        # Schedule protection: block if active semester has scheduled sections
        cur.execute("""
            SELECT COUNT(*) FROM schedule sc
            JOIN semester sem ON sem.semesterid = sc.semesterid
            JOIN sections sec ON sec.sectionid = sc.sectionid
            WHERE sec.programyearlevelid = %s AND sem.isactive = TRUE
        """, (ao_id,))
        if cur.fetchone()['count'] > 0:
            return jsonify({'success': False,
                            'error': f"Cannot deactivate '{prog_code}': it has sections assigned to the active academic year schedule."})

        # Check for operational records
        cur.execute("SELECT COUNT(*) FROM sections WHERE programyearlevelid=%s", (ao_id,))
        section_count = cur.fetchone()['count']

        cur.execute("""
            SELECT COUNT(*) FROM schedule sc
            JOIN sections sec ON sc.sectionid = sec.sectionid
            WHERE sec.programyearlevelid = %s
        """, (ao_id,))
        schedule_count = cur.fetchone()['count']

        if section_count > 0 or schedule_count > 0:
            # Soft delete — preserve data integrity
            cur.execute("UPDATE program_yearlevel SET isactive=FALSE WHERE programyearlevelid=%s", (ao_id,))
            cur.execute("UPDATE sections SET isactive=FALSE WHERE programyearlevelid=%s", (ao_id,))
            conn.commit()
            print(f"[DeleteOffering] Soft-deleted program_yearlevel {ao_id} for {prog_code} (sections={section_count}, schedules={schedule_count})")
            try:
                write_activity_log("Deactivated Program Year-Level",
                                   f"Soft-deleted program_yearlevel {ao_id} ({curr_code}) for {prog_code} — {section_count} sections, {schedule_count} schedules preserved",
                                   category='program', color=_LOG_COLORS.get('program', 'blue'))
            except Exception: pass
            return jsonify({
                'success': True,
                'mode':    'soft',
                'message': f"'{prog_code}' year-level has been deactivated. Associated records are preserved.",
                'academicofferingid': int(ao_id),
            })

        # Hard delete — no operational records (sections cascade via FK)
        cur.execute("DELETE FROM program_yearlevel WHERE programyearlevelid=%s", (ao_id,))
        conn.commit()
        print(f"[DeleteOffering] Hard-deleted program_yearlevel {ao_id} for {prog_code}")
        try:
            write_activity_log("Deleted Program Year-Level",
                               f"Permanently deleted program_yearlevel {ao_id} ({curr_code}) from {prog_code}",
                               category='program', color=_LOG_COLORS.get('program', 'blue'))
        except Exception: pass
        return jsonify({
            'success': True,
            'mode':    'hard',
            'message': f"'{prog_code}' year-level has been permanently deleted.",
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
    if session.get('role') != 'Admin': 
        return redirect(url_for('login'))
    
    # Fetch data for all dropdown filters
    ay_list = query_db("SELECT academicyearid, yearstart, yearend FROM academicyear ORDER BY yearstart DESC")
    programs = query_db("SELECT programcode, programname FROM programs WHERE isactive = TRUE ORDER BY programname")
    faculty = query_db("SELECT employeenumber, lastname || ', ' || firstname AS fullname FROM faculty ORDER BY lastname, firstname")
    curricula = query_db("SELECT c.curriculumid, c.curriculumcode, c.curriculumyear, p.programcode FROM curriculum c JOIN programs p ON c.programcode = p.programcode ORDER BY p.programcode, c.curriculumyear DESC")
    emp_types = query_db("SELECT employeetypeid, typename FROM employeetype ORDER BY typename")
    specializations = query_db("SELECT specializationid, specializationname FROM specialization ORDER BY specializationname")
    statuses = query_db("SELECT DISTINCT employeestatus FROM faculty WHERE employeestatus IS NOT NULL ORDER BY employeestatus")
    buildings = query_db("SELECT buildingid, buildingname FROM building WHERE isactive = TRUE ORDER BY buildingname")
    room_types = query_db("SELECT DISTINCT roomtype FROM room WHERE roomtype IS NOT NULL ORDER BY roomtype")
    
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
            where.append("p.programcode = %s"); p.append(effective_prog)
        if yl and yl != 'All':
            where.append("pyl.yearlevel = %s"); p.append(int(yl))
        cur.execute(f"""
            SELECT
                f.lastname || ', ' || f.firstname AS "Instructor",
                cs.subjectcode AS "Subject Code",
                ccs.subjectname AS "Subject Description",
                cs.lecturehours AS "Lec",
                cs.laboratoryhours AS "Lab",
                cs.creditunits AS "Units",
                p.programcode || ' ' || pyl.yearlevel AS "Course",
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
            JOIN sections sec              ON sg.sectionid           = sec.sectionid
            JOIN program_yearlevel pyl     ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN programs p ON pyl.programcode = p.programcode
            JOIN faculty f                 ON sg.employeenumber      = f.employeenumber
            JOIN semester sem              ON sg.semesterid          = sem.semesterid
            LEFT JOIN schedule_sessions ss ON sv.versionid           = ss.versionid
            LEFT JOIN timeslot ts_s        ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN timeslot ts_e        ON ss.endtimeid           = ts_e.timeid
            LEFT JOIN room r               ON ss.roomid              = r.roomid
            WHERE {' AND '.join(where)}
            GROUP BY f.lastname, f.firstname, cs.subjectcode, ccs.subjectname,
                     cs.lecturehours, cs.laboratoryhours, cs.creditunits,
                     p.programcode, pyl.yearlevel
            ORDER BY f.lastname, p.programcode, pyl.yearlevel, cs.subjectcode
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
                cs.subjectcode AS "Subject Code",
                ccs.subjectname AS "Subject Description",
                p.programcode AS "Program",
                sec.sectionname AS "Section",
                (cs.lecturehours + cs.laboratoryhours) AS "Hours"
            FROM schedule_version sv
            JOIN schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN sections sec         ON sg.sectionid           = sec.sectionid
            JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN programs p ON pyl.programcode = p.programcode
            JOIN faculty f            ON sg.employeenumber      = f.employeenumber
            JOIN semester sem         ON sg.semesterid          = sem.semesterid
            WHERE {' AND '.join(where)}
            ORDER BY f.lastname, p.programcode, cs.subjectcode
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
                where.append("p.programcode = %s"); p.append(prog)
            if curr and curr != 'All':
                where.append("cs.curriculumid = %s"); p.append(int(curr))
            cur.execute(f"""
                SELECT c.curriculumcode AS "Curriculum",
                       cs.yearlevel AS "Year Level",
                       CASE cs.semester WHEN 'A' THEN '1st Sem' WHEN 'B' THEN '2nd Sem'
                                        WHEN 'C' THEN 'Summer' ELSE cs.semester END AS "Semester",
                       cs.subjectcode AS "Subject Code",
                       ccs.subjectname AS "Subject Description",
                       cs.lecturehours AS "Lec Hours",
                       cs.laboratoryhours AS "Lab Hours",
                       cs.creditunits AS "Credit Units",
                       COALESCE(cs.prerequisite,'—') AS "Pre-requisite"
                FROM curriculumsubject cs
                JOIN curriculum c ON cs.curriculumid = c.curriculumid
                JOIN programs p ON c.programcode = p.programcode
                {'WHERE ' + ' AND '.join(where) if where else ''}
                ORDER BY c.curriculumcode, cs.yearlevel, cs.semester, cs.subjectcode
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
                JOIN programs p ON pyl.programcode = p.programcode
                JOIN programs p     ON p.programcode    = p.programcode
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

# ══════════════════════════════════════════════════════════════════════════════
# REPORTS — PREVIEW & EXPORT
# ══════════════════════════════════════════════════════════════════════════════

# ─── Helper: pretty label for active filter chips ─────────────────────────────
def _rpt_chip(label, value, lookup=None):
    """Return a human-readable chip string, or None if value is empty / All."""
    if not value or value == 'All':
        return None
    display = lookup.get(str(value), str(value)) if lookup else str(value)
    return f"{label}: {display}"


# ─── Helper: generic flat-table export (CSV / XLSX / PDF / DOCX) ──────────────
def _generic_gen_csv(columns, rows):
    out = io.StringIO()
    w   = csv.writer(out)
    w.writerow(columns)
    for row in rows:
        w.writerow(row)
    return out.getvalue().encode('utf-8-sig')


def _generic_gen_xlsx(title, columns, rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb  = Workbook()
    ws  = wb.active
    ws.title = title[:31]

    hdr_fill = PatternFill('solid', fgColor='440000')
    wht_font = Font(bold=True, color='FFFFFF', size=10)
    thin     = Side(style='thin', color='DDDDDD')
    brd      = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Title row
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
    tc = ws.cell(1, 1, title.upper())
    tc.font      = Font(bold=True, color='FFFFFF', size=12)
    tc.fill      = PatternFill('solid', fgColor='2C0000')
    tc.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 26

    # Header row
    for ci, col in enumerate(columns, 1):
        c = ws.cell(2, ci, col)
        c.font = wht_font; c.fill = hdr_fill; c.border = brd
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[2].height = 28

    # Data rows
    for ri, row in enumerate(rows):
        bg = 'FFFFFF' if ri % 2 == 0 else 'FDF5F5'
        for ci, val in enumerate(row, 1):
            c = ws.cell(ri + 3, ci, val)
            c.fill      = PatternFill('solid', fgColor=bg)
            c.border    = brd
            c.font      = Font(size=9)
            c.alignment = Alignment(vertical='center')
        ws.row_dimensions[ri + 3].height = 16

    # Auto-width (capped at 40)
    for ci in range(1, len(columns) + 1):
        max_len = max(
            (len(str(ws.cell(r, ci).value or '')) for r in range(1, len(rows) + 4)),
            default=10
        )
        ws.column_dimensions[get_column_letter(ci)].width = min(max_len + 4, 40)

    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return buf.read()


def _generic_gen_pdf(title, columns, rows, filters_text=''):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    buf = io.BytesIO()
    # Use landscape for wide tables (>5 columns)
    page = landscape(A4) if len(columns) > 5 else A4
    doc = SimpleDocTemplate(buf, pagesize=page,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)

    DARK   = colors.HexColor('#5C0000')
    MED    = colors.HexColor('#7A0000')
    STRIPE = colors.HexColor('#FDF5F5')
    WHITE  = colors.white
    LGRAY  = colors.HexColor('#DDDDDD')

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle('H1', parent=styles['Heading1'], textColor=DARK, fontSize=16,
                         spaceAfter=4, alignment=TA_CENTER)
    sm = ParagraphStyle('SM', parent=styles['Normal'],   textColor=MED,  fontSize=9,
                         spaceAfter=8, alignment=TA_CENTER)

    story = [Paragraph(title.upper(), h1)]
    if filters_text:
        story.append(Paragraph(filters_text, sm))
    story.append(Spacer(1, 0.3*cm))

    if rows:
        # Build equal-width columns
        avail_w = (page[0] if len(columns) > 5 else page[0]) - 3*cm
        col_w   = [avail_w / len(columns)] * len(columns)

        tbl_data = [columns] + [list(r) for r in rows]
        tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)
        tbl.setStyle(TableStyle([
            ('BACKGROUND',     (0,0),  (-1,0),  DARK),
            ('TEXTCOLOR',      (0,0),  (-1,0),  WHITE),
            ('FONTNAME',       (0,0),  (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',       (0,0),  (-1,-1), 8),
            ('FONTNAME',       (0,1),  (-1,-1), 'Helvetica'),
            ('ALIGN',          (0,0),  (-1,0),  'CENTER'),
            ('VALIGN',         (0,0),  (-1,-1), 'MIDDLE'),
            ('GRID',           (0,0),  (-1,-1), 0.5, LGRAY),
            ('ROWBACKGROUNDS', (0,1),  (-1,-1), [WHITE, STRIPE]),
            ('LEFTPADDING',    (0,0),  (-1,-1), 4),
            ('RIGHTPADDING',   (0,0),  (-1,-1), 4),
            ('TOPPADDING',     (0,0),  (-1,-1), 4),
            ('BOTTOMPADDING',  (0,0),  (-1,-1), 4),
        ]))
        story.append(tbl)
    else:
        story.append(Paragraph('No records found.', sm))

    doc.build(story)
    buf.seek(0)
    return buf.read()


def _generic_gen_docx(title, columns, rows, filters_text=''):
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    doc = Document()
    sec = doc.sections[0]
    sec.page_width  = Inches(11.7)
    sec.page_height = Inches(8.27)
    sec.left_margin = sec.right_margin  = Cm(1.5)
    sec.top_margin  = sec.bottom_margin = Cm(1.5)

    DARK  = RGBColor(0x5C, 0x00, 0x00)
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)

    def _bg(cell, hex6):
        tc   = cell._tc
        tcPr = tc.get_or_add_tcPr()
        shd  = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear')
        shd.set(qn('w:color'), 'auto')
        shd.set(qn('w:fill'), hex6)
        tcPr.append(shd)

    h = doc.add_heading(title.upper(), 0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in h.runs:
        run.font.color.rgb = DARK; run.font.size = Pt(14)

    if filters_text:
        p = doc.add_paragraph(filters_text)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph()

    if rows:
        tbl = doc.add_table(rows=1 + len(rows), cols=len(columns))
        tbl.style = 'Table Grid'
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

        for ci, h_txt in enumerate(columns):
            cell = tbl.rows[0].cells[ci]
            cell.text = ''
            run = cell.paragraphs[0].add_run(h_txt)
            run.font.bold = True; run.font.color.rgb = WHITE; run.font.size = Pt(8)
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            _bg(cell, '440000')
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

        for ri, row in enumerate(rows):
            bg = 'FFFFFF' if ri % 2 == 0 else 'FDF5F5'
            for ci, val in enumerate(row):
                cell = tbl.rows[ri + 1].cells[ci]
                cell.text = ''
                run = cell.paragraphs[0].add_run(str(val) if val is not None else '')
                run.font.size = Pt(8)
                _bg(cell, bg)
                cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    buf = io.BytesIO()
    doc.save(buf); buf.seek(0)
    return buf.read()


def _generic_export_response(title, columns, rows, formats, filename):
    """Build a Response (single file or zip) for any flat-table report."""
    import zipfile as _zip

    def _build(fmt):
        if fmt == 'csv':
            return _generic_gen_csv(columns, rows), 'text/csv', '.csv'
        elif fmt == 'xlsx':
            return _generic_gen_xlsx(title, columns, rows), \
                   'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'
        elif fmt == 'pdf':
            return _generic_gen_pdf(title, columns, rows), 'application/pdf', '.pdf'
        elif fmt == 'docx':
            return _generic_gen_docx(title, columns, rows), \
                   'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'
        else:
            raise ValueError(f'Unknown format: {fmt}')

    if len(formats) == 1:
        data, mime, ext = _build(formats[0])
        return Response(data, mimetype=mime,
                        headers={'Content-Disposition': f'attachment; filename="{filename}{ext}"'})
    else:
        buf = io.BytesIO()
        with _zip.ZipFile(buf, 'w', _zip.ZIP_DEFLATED) as zf:
            for fmt in formats:
                data, _m, ext = _build(fmt)
                zf.writestr(filename + ext, data)
        buf.seek(0)
        return Response(buf.read(), mimetype='application/zip',
                        headers={'Content-Disposition': f'attachment; filename="{filename}.zip"'})





# ─── Data fetchers (reuse the logic already in reports_data) ──────────────────

def _fetch_report_data(report_type, params, cur):
    """
    Returns (columns, rows, sections) where sections is non-None for grouped reports.
    rows is a list of lists; sections is a list of dicts with 'title','columns','rows'.
    """
    ay         = params.get('ay')
    sem        = params.get('sem')
    prog       = params.get('prog')
    yl         = params.get('yl')
    bldg       = params.get('bldg')
    rt         = params.get('rt')
    emp_type   = params.get('emp_type')
    status     = params.get('status')
    spec       = params.get('spec')
    curr       = params.get('curr')

    SEM_LABEL = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}

    def _to_rows(db_rows):
        if not db_rows:
            return [], []
        cols = list(db_rows[0].keys())
        data = [[str(v) if v is not None else '—' for v in row.values()] for row in db_rows]
        return cols, data

    # ── CLASS SCHEDULE ──────────────────────────────────────────────────────
    if report_type == 'class_schedule':
        ay_ids      = [ay]          if ay   else []
        sem_types   = [sem]         if sem  else []
        programs    = [prog]        if prog else []
        year_levels = [int(yl)]     if yl   else []
        rows        = _sch_exp_fetch(cur, ay_ids, sem_types, programs, year_levels)
        if not rows:
            return [], [], None
        cols = ['Instructor','Subject Code','Subject Description',
                'Lec','Lab','Units','Program','Year Level','Section',
                'Hours','Day/s','Time','Room','Academic Year','Semester']
        key_map = ['Instructor','SubjectCode','SubjectName',
                   'LectureHours','LaboratoryHours','CreditUnits',
                   'Program','YearLevel','Section','Hours',
                   'Day/s','Time','Room','AcademicYear','SemesterType']
        data = [[str(r.get(k) or '') for k in key_map] for r in rows]
        return cols, data, None

    # ── ROOM SCHEDULE ────────────────────────────────────────────────────────
    elif report_type == 'room_schedule':
        where, p = ["sv.status IN ('Published','Draft')"], []
        if ay:
            where.append("sem.academicyearid = %s"); p.append(ay)
        if sem:
            where.append("sem.semestertype = %s"); p.append(sem)
        if bldg:
            where.append("b.buildingid = %s"); p.append(int(bldg))
        if rt:
            where.append("r.roomtype = %s"); p.append(rt)
        cur.execute(f"""
            SELECT
                r.roomname                      AS "Room",
                b.buildingname                  AS "Building",
                r.roomtype                      AS "Type",
                ss.daydesc                      AS "Day",
                TO_CHAR(ts_s.timevalue,'HH12:MI AM') || ' – ' ||
                TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS "Time",
                cs.subjectcode                 AS "Subject Code",
                ccs.subjectname                 AS "Subject Name",
                p.programcode                 AS "Program",
                pyl.yearlevel                   AS "Year",
                COALESCE(f.lastname||', '||f.firstname,'TBA') AS "Instructor",
                ay.yearstart||'–'||ay.yearend   AS "A.Y.",
                sem.semestertype                AS "Sem"
            FROM schedule_sessions ss
            JOIN schedule_version sv  ON ss.versionid = sv.versionid
            JOIN schedule sc          ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs  ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN sections sec         ON sc.sectionid = sec.sectionid
            JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN programs p ON pyl.programcode = p.programcode
            JOIN semester sem         ON sc.semesterid = sem.semesterid
            JOIN academicyear ay      ON sem.academicyearid = ay.academicyearid
            JOIN room r               ON ss.roomid = r.roomid
            JOIN building b           ON r.buildingid = b.buildingid
            LEFT JOIN faculty f       ON sc.employeenumber = f.employeenumber
            LEFT JOIN timeslot ts_s   ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e   ON ss.endtimeid = ts_e.timeid
            WHERE {' AND '.join(where)}
            ORDER BY b.buildingname, r.roomname, ss.daydesc, ts_s.timevalue
        """, p)
        return _to_rows(cur.fetchall()) + (None,)

    # ── EMPLOYEE LIST ────────────────────────────────────────────────────────
    elif report_type == 'faculty':
        where, p = [], []
        if emp_type:
            where.append("f.employeetypeid = %s"); p.append(int(emp_type))
        if status:
            where.append("f.employeestatus = %s"); p.append(status)
        if spec:
            where.append("f.specializationid = %s"); p.append(int(spec))
        else:
            where.append("f.employeestatus != 'Archive'")
        cur.execute(f"""
            SELECT
                f.lastname||', '||f.firstname||' '||COALESCE(f.middlename,'') AS "Faculty Name",
                et.typename                        AS "Employee Type",
                COALESCE(s.specializationname,'—') AS "Specialization",
                COALESCE(d.designationname,'—')    AS "Designation",
                COALESCE(et.regularload::text,'—') AS "Max Load (Units)",
                f.employeestatus                   AS "Status",
                f.email                            AS "Email"
            FROM faculty f
            LEFT JOIN employeetype et  ON f.employeetypeid   = et.employeetypeid
            LEFT JOIN designation d    ON f.designationid    = d.designationid
            LEFT JOIN specialization s ON f.specializationid = s.specializationid
            {'WHERE ' + ' AND '.join(where) if where else ''}
            ORDER BY f.lastname, f.firstname
        """, p)
        return _to_rows(cur.fetchall()) + (None,)

    # ── CURRICULUM LIST ──────────────────────────────────────────────────────
    elif report_type == 'curriculum':
        where, p = [], []
        if prog:
            where.append("p.programcode = %s"); p.append(prog)
        if curr:
            where.append("cs.curriculumid = %s"); p.append(int(curr))
        if sem:
            where.append("cs.semester = %s"); p.append(sem)
        cur.execute(f"""
            SELECT
                c.curriculumcode          AS "Curriculum",
                p.programcode           AS "Program",
                cs.yearlevel              AS "Year Level",
                CASE cs.semester
                    WHEN 'A' THEN '1st Sem'
                    WHEN 'B' THEN '2nd Sem'
                    WHEN 'C' THEN 'Summer'
                    ELSE cs.semester END  AS "Semester",
                cs.subjectcode           AS "Subject Code",
                ccs.subjectname           AS "Subject Description",
                cs.lecturehours          AS "Lec Hours",
                cs.laboratoryhours       AS "Lab Hours",
                cs.creditunits           AS "Credit Units",
                COALESCE(cs.prerequisite,'—') AS "Pre-requisite"
            FROM curriculumsubject cs
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
            JOIN programs p ON c.programcode = p.programcode
            {'WHERE ' + ' AND '.join(where) if where else ''}
            ORDER BY c.curriculumcode, cs.yearlevel, cs.semester, cs.subjectcode
        """, p)
        return _to_rows(cur.fetchall()) + (None,)

    # ── ROOM AND BUILDING LIST ───────────────────────────────────────────────
    elif report_type == 'rooms':
        where, p = [], []
        if bldg:
            where.append("b.buildingid = %s"); p.append(int(bldg))
        if rt:
            where.append("r.roomtype = %s"); p.append(rt)
        cur.execute(f"""
            SELECT
                r.roomname      AS "Room Name",
                b.buildingname  AS "Building",
                r.roomtype      AS "Type",
                r.roomcapacity  AS "Capacity",
                CASE WHEN b.isactive THEN 'Active' ELSE 'Inactive' END AS "Status"
            FROM room r
            JOIN building b ON r.buildingid = b.buildingid
            {'WHERE ' + ' AND '.join(where) if where else ''}
            ORDER BY b.buildingname, r.roomname
        """, p)
        return _to_rows(cur.fetchall()) + (None,)

    # ── TEACHING ASSIGNMENT ──────────────────────────────────────────────────
    elif report_type == 'assignments':
        where = ["sv.status IN ('Published','Archive','Draft')"]
        p     = []
        if ay:
            where.append("sem.academicyearid = %s"); p.append(ay)
        if sem:
            where.append("sem.semestertype = %s"); p.append(sem)
        if prog:
            where.append("p.programcode = %s"); p.append(prog)
        cur.execute(f"""
            SELECT
                f.lastname||', '||f.firstname  AS "Faculty Name",
                cs.subjectcode                AS "Subject Code",
                ccs.subjectname                AS "Subject Description",
                p.programcode                AS "Program",
                sec.sectionname                AS "Section",
                pyl.yearlevel                  AS "Year Level",
                (cs.lecturehours+cs.laboratoryhours) AS "Hours",
                sem.semestertype               AS "Semester",
                ay.yearstart||'–'||ay.yearend  AS "A.Y."
            FROM schedule_version sv
            JOIN schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN sections sec         ON sg.sectionid           = sec.sectionid
            JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN programs p ON pyl.programcode = p.programcode
            JOIN faculty f            ON sg.employeenumber      = f.employeenumber
            JOIN semester sem         ON sg.semesterid          = sem.semesterid
            JOIN academicyear ay      ON sem.academicyearid     = ay.academicyearid
            WHERE {' AND '.join(where)}
            ORDER BY f.lastname, p.programcode, cs.subjectcode
        """, p)
        return _to_rows(cur.fetchall()) + (None,)

    # ── ACADEMIC OFFERINGS ───────────────────────────────────────────────────
    elif report_type == 'offerings':
        where = ["sv.status IN ('Published','Archive','Draft')"]
        p     = []
        if ay:
            where.append("sem.academicyearid = %s"); p.append(ay)
        if sem:
            where.append("sem.semestertype = %s"); p.append(sem)
        if prog:
            where.append("p.programcode = %s"); p.append(prog)
        if yl:
            where.append("pyl.yearlevel = %s"); p.append(int(yl))
        cur.execute(f"""
            SELECT
                f.lastname||', '||f.firstname AS "Instructor",
                cs.subjectcode               AS "Subject Code",
                ccs.subjectname               AS "Subject Description",
                cs.lecturehours              AS "Lec",
                cs.laboratoryhours           AS "Lab",
                cs.creditunits               AS "Units",
                p.programcode||' '||pyl.yearlevel AS "Course",
                string_agg(DISTINCT
                    CASE ss.daydesc
                        WHEN 'Monday'    THEN 'MON'
                        WHEN 'Tuesday'   THEN 'TUE'
                        WHEN 'Wednesday' THEN 'WED'
                        WHEN 'Thursday'  THEN 'THU'
                        WHEN 'Friday'    THEN 'FRI'
                        WHEN 'Saturday'  THEN 'SAT'
                        WHEN 'Sunday'    THEN 'SUN'
                        ELSE ss.daydesc END, '/') AS "Days",
                string_agg(
                    to_char(ts_s.timevalue::interval,'HH12:MI AM')||' – '||
                    to_char(ts_e.timevalue::interval,'HH12:MI AM'),
                    '/' ORDER BY ts_s.timevalue) AS "Time",
                string_agg(COALESCE(r.roomname,'TBA'),'/') AS "Room",
                sem.semestertype               AS "Semester",
                ay.yearstart||'–'||ay.yearend  AS "A.Y."
            FROM schedule_version sv
            JOIN schedule sg               ON sv.scheduleid          = sg.scheduleid
            JOIN curriculumsubject cs       ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN sections sec              ON sg.sectionid           = sec.sectionid
            JOIN program_yearlevel pyl     ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN programs p ON pyl.programcode = p.programcode
            JOIN faculty f                 ON sg.employeenumber      = f.employeenumber
            JOIN semester sem              ON sg.semesterid          = sem.semesterid
            JOIN academicyear ay           ON sem.academicyearid     = ay.academicyearid
            LEFT JOIN schedule_sessions ss ON sv.versionid           = ss.versionid
            LEFT JOIN timeslot ts_s        ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN timeslot ts_e        ON ss.endtimeid           = ts_e.timeid
            LEFT JOIN room r               ON ss.roomid              = r.roomid
            WHERE {' AND '.join(where)}
            GROUP BY f.lastname, f.firstname, cs.subjectcode, ccs.subjectname,
                     cs.lecturehours, cs.laboratoryhours, cs.creditunits,
                     p.programcode, pyl.yearlevel, sem.semestertype,
                     ay.yearstart, ay.yearend
            ORDER BY f.lastname, p.programcode, pyl.yearlevel, cs.subjectcode
        """, p)
        return _to_rows(cur.fetchall()) + (None,)

    return [], [], None


# ─── Report title mapping ────────────────────────────────────────────────────
_RPT_TITLES = {
    'class_schedule': 'Class Schedule',
    'room_schedule':  'Room Schedule',
    'faculty':        'Employee List',
    'curriculum':     'Curriculum List',
    'rooms':          'Room and Building List',
    'assignments':    'Teaching Assignment',
    'offerings':      'Academic Offerings',
}

_RPT_FILENAMES = {
    'class_schedule': 'Class_Schedule',
    'room_schedule':  'Room_Schedule',
    'faculty':        'Employee_List',
    'curriculum':     'Curriculum_List',
    'rooms':          'Room_Building_List',
    'assignments':    'Teaching_Assignment',
    'offerings':      'Academic_Offerings',
}


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE: /reports/preview/<report_type>
# Replaces the stub at the bottom of app.py (around line 14389)
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/reports/preview/<report_type>')
def report_preview(report_type):
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    # Collect filter params from query string
    params = {
        'ay':       request.args.get('ay'),
        'sem':      request.args.get('sem'),
        'prog':     request.args.get('prog'),
        'yl':       request.args.get('yl'),
        'bldg':     request.args.get('bldg'),
        'rt':       request.args.get('rt'),
        'emp_type': request.args.get('emp_type'),
        'status':   request.args.get('status'),
        'spec':     request.args.get('spec'),
        'curr':     request.args.get('curr'),
    }
    # Remove None values so JS payload is clean
    filter_params = {k: v for k, v in params.items() if v}

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        columns, rows, sections = _fetch_report_data(report_type, params, cur)
    except Exception as e:
        columns, rows, sections = [], [], None
        import traceback; traceback.print_exc()
    finally:
        cur.close(); conn.close()

    # Build filter chips for human-readable display
    # Load lookup tables for chip labels
    ay_lookup   = {}
    prog_lookup = {}
    bldg_lookup = {}
    spec_lookup = {}
    et_lookup   = {}
    curr_lookup = {}
    try:
        ay_rows = query_db("SELECT academicyearid, yearstart, yearend FROM academicyear")
        ay_lookup = {str(r['academicyearid']): f"A.Y. {r['yearstart']}–{r['yearend']}" for r in (ay_rows or [])}

        pr_rows = query_db("SELECT programcode, programname FROM programs")
        prog_lookup = {r['programcode']: r['programname'] for r in (pr_rows or [])}

        bl_rows = query_db("SELECT buildingid, buildingname FROM building")
        bldg_lookup = {str(r['buildingid']): r['buildingname'] for r in (bl_rows or [])}

        sp_rows = query_db("SELECT specializationid, specializationname FROM specialization")
        spec_lookup = {str(r['specializationid']): r['specializationname'] for r in (sp_rows or [])}

        et_rows = query_db("SELECT employeetypeid, typename FROM employeetype")
        et_lookup = {str(r['employeetypeid']): r['typename'] for r in (et_rows or [])}

        cu_rows = query_db("SELECT curriculumid, curriculumcode FROM curriculum")
        curr_lookup = {str(r['curriculumid']): r['curriculumcode'] for r in (cu_rows or [])}
    except Exception:
        pass

    SEM_LABEL = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}

    chip_map = [
        ('Academic Year', params.get('ay'),       ay_lookup),
        ('Semester',      params.get('sem'),       SEM_LABEL),
        ('Program',       params.get('prog'),      prog_lookup),
        ('Year Level',    params.get('yl'),        None),
        ('Building',      params.get('bldg'),      bldg_lookup),
        ('Room Type',     params.get('rt'),        None),
        ('Emp. Type',     params.get('emp_type'),  et_lookup),
        ('Status',        params.get('status'),    None),
        ('Specialization',params.get('spec'),      spec_lookup),
        ('Curriculum',    params.get('curr'),      curr_lookup),
    ]
    active_filters = [
        chip for (label, val, lkp) in chip_map
        if (chip := _rpt_chip(label, val, lkp))
    ]

    # Row count
    if sections:
        row_count = sum(len(s['rows']) for s in sections)
    else:
        row_count = len(rows)

    title           = _RPT_TITLES.get(report_type, 'Report')
    export_filename = _RPT_FILENAMES.get(report_type, 'Report')

    return render_template(
        'academic/reports_preview.html',
        report_type    = report_type,
        title          = title,
        columns        = columns,
        rows           = rows,
        sections       = sections,
        row_count      = row_count,
        active_filters = active_filters,
        filter_params  = filter_params,
        export_filename= export_filename,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ROUTE: POST /reports/export/<report_type>
# Called from the Export modal on the preview page
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/reports/export/<report_type>', methods=['POST'])
def report_export(report_type):
    if 'loggedin' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    payload  = request.get_json() or {}
    formats  = [f.lower() for f in payload.get('formats', ['csv'])]
    filename = (payload.get('filename') or _RPT_FILENAMES.get(report_type, 'report')).strip()
    for ext in ('.pdf','.docx','.xlsx','.csv','.zip'):
        if filename.lower().endswith(ext):
            filename = filename[:-len(ext)]

    # Re-run the same query using filter params from request body
    params = {
        'ay':       payload.get('ay'),
        'sem':      payload.get('sem'),
        'prog':     payload.get('prog'),
        'yl':       payload.get('yl'),
        'bldg':     payload.get('bldg'),
        'rt':       payload.get('rt'),
        'emp_type': payload.get('emp_type'),
        'status':   payload.get('status'),
        'spec':     payload.get('spec'),
        'curr':     payload.get('curr'),
    }

    # For class_schedule, reuse the fully-featured schedule export
    if report_type == 'class_schedule':
        ay_ids      = [params['ay']]       if params.get('ay')   else []
        sem_types   = [params['sem']]      if params.get('sem')  else []
        programs    = [params['prog']]     if params.get('prog') else []
        year_levels = [int(params['yl'])]  if params.get('yl')   else []

        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        try:
            rows   = _sch_exp_fetch(cur, ay_ids, sem_types, programs, year_levels)
            groups = _sch_exp_groups(rows)
            SEM_MAP   = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}
            sem_label = ', '.join(SEM_MAP.get(s, s) for s in sorted(sem_types)) if sem_types else 'All Semesters'

            if len(formats) == 1:
                fmt = formats[0]
                if fmt == 'csv':
                    out, mime, ext = _sch_gen_csv(rows), 'text/csv', '.csv'
                elif fmt == 'xlsx':
                    out, mime, ext = _sch_gen_xlsx(rows, groups), \
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'
                elif fmt == 'docx':
                    out, mime, ext = _sch_gen_docx(rows, groups, sem_label), \
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'
                elif fmt == 'pdf':
                    out, mime, ext = _sch_gen_pdf(rows, groups, sem_label), 'application/pdf', '.pdf'
                else:
                    return jsonify({'error': f'Unknown format: {fmt}'}), 400
                return Response(out, mimetype=mime,
                                headers={'Content-Disposition': f'attachment; filename="{filename}{ext}"'})
            else:
                import zipfile
                buf = io.BytesIO()
                fmt_map = {
                    'csv':  (lambda: _sch_gen_csv(rows), '.csv'),
                    'xlsx': (lambda: _sch_gen_xlsx(rows, groups), '.xlsx'),
                    'docx': (lambda: _sch_gen_docx(rows, groups, sem_label), '.docx'),
                    'pdf':  (lambda: _sch_gen_pdf(rows, groups, sem_label), '.pdf'),
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

    # For all other report types — generic exporter
    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        columns, rows, sections = _fetch_report_data(report_type, params, cur)

        # Flatten grouped sections for export (sections → single flat list)
        if sections:
            flat_rows = []
            for sec in sections:
                flat_rows.extend(sec['rows'])
            rows = flat_rows

        title = _RPT_TITLES.get(report_type, 'Report')
        return _generic_export_response(title, columns, rows, formats, filename)

    except Exception as e:
        import traceback; traceback.print_exc()
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
            # Scope to the active semester so only the current period's schedule shows
            cursor.execute("""
                SELECT cs.subjectcode, cs.subjectname, r.roomname, ss.daydesc,
                       TO_CHAR(ts_s.timevalue, 'HH12:MI AM') as start_time,
                       TO_CHAR(ts_e.timevalue, 'HH12:MI AM') as end_time,
                       pyl.programcode AS offeringcode, pyl.yearlevel
                FROM schedule_sessions ss
                JOIN schedule_version sv ON ss.versionid = sv.versionid
                JOIN schedule sc ON sv.scheduleid = sc.scheduleid
                JOIN semester sem ON sc.semesterid = sem.semesterid
                JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                LEFT JOIN room r ON ss.roomid = r.roomid
                LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
                LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
                LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
                WHERE sc.employeenumber = %s AND sv.status = 'Published'
                  AND sem.isactive = TRUE
                  AND ss.daydesc = %s
                ORDER BY ts_s.timevalue
            """, (emp_num, today_day))
            today_schedule = cursor.fetchall()

            cursor.execute("""
                SELECT COALESCE(SUM(cs.creditunits), 0) as total_units,
                       COUNT(DISTINCT cs.subjectcode) as total_subjects
                FROM schedule_version sv
                JOIN schedule sc ON sv.scheduleid = sc.scheduleid
                JOIN semester sem ON sc.semesterid = sem.semesterid
                JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                WHERE sc.employeenumber = %s AND sv.status = 'Published'
                  AND sem.isactive = TRUE
            """, (emp_num,))
            stats = cursor.fetchone()
            if stats:
                total_units = int(stats['total_units'])
                total_subjects = int(stats['total_subjects'])

        # Inbox notifications & request summary
        inbox_items = []
        req_summary = {'pending': 0, 'approved': 0, 'rejected': 0}
        if emp_num:
            _ensure_request_tables()
            # Request counts by status
            cursor.execute("""
                SELECT status, COUNT(*) AS cnt FROM (
                    SELECT status FROM class_meeting_request WHERE submitted_by = %s
                    UNION ALL
                    SELECT status FROM schedule_change_request WHERE submitted_by = %s
                ) all_reqs
                WHERE status IN ('Pending','Approved','Rejected')
                GROUP BY status
            """, (emp_num, emp_num))
            for row in (cursor.fetchall() or []):
                key = row['status'].lower()
                if key in req_summary:
                    req_summary[key] = int(row['cnt'])

            # Most recent 3 request activity for inbox
            cursor.execute("""
                SELECT * FROM (
                    SELECT cmr.status,
                           'makeup' AS req_type,
                           COALESCE(cs.subjectcode,'') AS subjectcode,
                           COALESCE(cs.subjectname,'') AS subjectname,
                           COALESCE(cmr.decided_at, cmr.created_at) AS sort_time
                    FROM class_meeting_request cmr
                    LEFT JOIN schedule sc ON cmr.scheduleid = sc.scheduleid
                    LEFT JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                    WHERE cmr.submitted_by = %s AND cmr.status != 'Cancelled'
                    UNION ALL
                    SELECT scr.status,
                           'adjustment' AS req_type,
                           COALESCE(cs.subjectcode,'') AS subjectcode,
                           COALESCE(cs.subjectname,'') AS subjectname,
                           COALESCE(scr.decided_at, scr.created_at) AS sort_time
                    FROM schedule_change_request scr
                    LEFT JOIN schedule sc ON scr.scheduleid = sc.scheduleid
                    LEFT JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                    WHERE scr.submitted_by = %s AND scr.status != 'Cancelled'
                ) combined
                ORDER BY sort_time DESC
                LIMIT 3
            """, (emp_num, emp_num))
            now_dt = datetime.now()
            for n in (cursor.fetchall() or []):
                n = dict(n)
                st = n.get('sort_time')
                if st:
                    diff = (now_dt - st).total_seconds()
                    if diff < 60:
                        n['time_ago'] = 'just now'
                    elif diff < 3600:
                        n['time_ago'] = f"{int(diff/60)}m ago"
                    elif diff < 86400:
                        n['time_ago'] = f"{int(diff/3600)}h ago"
                    else:
                        n['time_ago'] = f"{int(diff/86400)}d ago"
                else:
                    n['time_ago'] = ''
                inbox_items.append(n)

        cursor.close()
        conn.close()

        if not user_data:
            user_data = {"firstname": "Faculty", "lastname": "Member", "designationname": "None"}

        return render_template('faculty/dashboard_faculty.html', user=user_data,
                               schedule=today_schedule, total_units=total_units,
                               total_subjects=total_subjects, current_date=current_date_formatted,
                               inbox_items=inbox_items, req_summary=req_summary)

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

    active = query_db("""
        SELECT ay.academicyearid, ay.yearstart, ay.yearend, s.semestertype
        FROM semester s
        JOIN academicyear ay ON s.academicyearid = ay.academicyearid
        WHERE s.isactive = TRUE LIMIT 1
    """, one=True)
    active_ay_id  = str(active['academicyearid']) if active else ''
    active_sem    = active['semestertype']         if active else ''
    active_ay_label = (f"A.Y {active['yearstart']}-{active['yearend']}") if active else ''
    _sem_labels   = {'A': '1ST SEMESTER', 'B': '2ND SEMESTER', 'C': 'SUMMER'}
    active_sem_label = _sem_labels.get(active_sem, active_sem)

    import json as _json
    return render_template('faculty/schedule_faculty.html',
        programs_json=_json.dumps([{'code': p['programcode'], 'name': p['programname']} for p in programs]),
        acad_years_json=_json.dumps(list(ay_map.values())),
        faculty_json=_json.dumps([{'emp': str(f['employeenumber']), 'name': f['fullname']} for f in faculty_list]),
        active_ay_id=active_ay_id,
        active_sem=active_sem,
        active_ay_label=active_ay_label,
        active_sem_label=active_sem_label,
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

    def _fac_room_floor(name):
        n = (name or '').replace(' ', '')
        if 'LQ1' in n: return '1'
        if 'LQ2' in n: return '2'
        if 'LQ3' in n: return '3'
        return 'other'

    return render_template('faculty/room_schedule_faculty.html',
        buildings_json=_json.dumps([{'id': b['buildingid'], 'name': b['buildingname']} for b in buildings]),
        rooms_json=_json.dumps([{
            'id': r['roomid'], 'name': r['roomname'],
            'type': r['roomtype'] or 'Lecture',
            'bid': r['buildingid'], 'bname': r['buildingname'],
            'capacity': r['roomcapacity'] or 0,
            'floor': _fac_room_floor(r['roomname'])
        } for r in rooms]),
        programs_json=_json.dumps([{'code': p['programcode'], 'name': p['programname']} for p in programs])
    )

# --- API: Faculty's own requests list ---
@app.route('/api/faculty/my_requests')
def api_faculty_my_requests():
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return jsonify({'success': False}), 401
    _ensure_request_tables()
    emp_num = session.get('employeenumber')
    if not emp_num:
        acc = query_db("SELECT employeenumber FROM accounts WHERE username=%s",
                       [session.get('username')], one=True)
        emp_num = acc['employeenumber'] if acc else None
    if not emp_num:
        return jsonify({'success': False, 'error': 'No employee number'}), 400
    try:
        makeup = query_db("""
            SELECT
                cmr.requestid,
                'makeup'                              AS req_type,
                COALESCE(cs.subjectcode,'')           AS subjectcode,
                COALESCE(cs.subjectname,'')           AS subjectname,
                COALESCE(pyl.programcode,'')          AS programcode,
                COALESCE(CAST(pyl.yearlevel AS TEXT),'') AS yearlevel,
                COALESCE(sec.sectionname,'')          AS sectionname,
                COALESCE(sec.sectionid::text,'')      AS sectionid,
                TO_CHAR(cmr.requested_date,'MM/DD/YYYY') AS requested_date,
                TO_CHAR(cmr.created_at,'MM/DD/YYYY') AS date_submitted,
                cmr.status,
                cmr.reason,
                COALESCE(cmr.notes,'')                AS notes,
                COALESCE(r.roomname,'')               AS roomname,
                COALESCE(r.roomid::text,'')           AS roomid,
                COALESCE(b.buildingname,'')           AS buildingname,
                TO_CHAR(ts_s.timevalue,'HH12:MI AM')  AS start_time,
                TO_CHAR(ts_e.timevalue,'HH12:MI AM')  AS end_time,
                cmr.new_starttimeid                   AS starttimeid,
                cmr.new_endtimeid                     AS endtimeid,
                COALESCE(cmr.remarks,'')              AS remarks
            FROM class_meeting_request cmr
            LEFT JOIN schedule sc    ON cmr.scheduleid = sc.scheduleid
            LEFT JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            LEFT JOIN sections sec   ON sc.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN room r         ON cmr.new_roomid = r.roomid
            LEFT JOIN building b     ON r.buildingid = b.buildingid
            LEFT JOIN timeslot ts_s  ON cmr.new_starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e  ON cmr.new_endtimeid   = ts_e.timeid
            WHERE cmr.submitted_by = %s
            ORDER BY cmr.created_at DESC
        """, [emp_num]) or []

        adj = query_db("""
            SELECT
                scr.requestid,
                scr.scheduleid,
                'adjustment'                          AS req_type,
                COALESCE(cs.subjectcode,'')           AS subjectcode,
                COALESCE(cs.subjectname,'')           AS subjectname,
                COALESCE(pyl.programcode,'')          AS programcode,
                COALESCE(CAST(pyl.yearlevel AS TEXT),'') AS yearlevel,
                COALESCE(sec.sectionname,'')          AS sectionname,
                COALESCE(sec.sectionid::text,'')      AS sectionid,
                TO_CHAR(scr.effective_from,'MM/DD/YYYY') AS requested_date,
                TO_CHAR(scr.created_at,'MM/DD/YYYY') AS date_submitted,
                scr.status,
                scr.reason,
                COALESCE(r.roomname,'')               AS roomname,
                COALESCE(r.roomid::text,'')           AS roomid,
                COALESCE(b.buildingname,'')           AS buildingname,
                TO_CHAR(ts_s.timevalue,'HH12:MI AM')  AS start_time,
                TO_CHAR(ts_e.timevalue,'HH12:MI AM')  AS end_time,
                scr.new_starttimeid                   AS starttimeid,
                scr.new_endtimeid                     AS endtimeid,
                COALESCE(scr.new_daydesc,'')          AS new_daydesc,
                TO_CHAR(scr.effective_from,'YYYY-MM-DD') AS effective_from,
                TO_CHAR(scr.end_date,'YYYY-MM-DD')    AS end_date,
                COALESCE(scr.remarks,'')              AS remarks,
                (SELECT ss2.daydesc
                 FROM schedule_sessions ss2
                 JOIN schedule_version sv2 ON ss2.versionid = sv2.versionid
                 WHERE sv2.scheduleid = scr.scheduleid AND sv2.status = 'Published'
                 ORDER BY sv2.version_number DESC LIMIT 1) AS current_daydesc
            FROM schedule_change_request scr
            LEFT JOIN schedule sc    ON scr.scheduleid = sc.scheduleid
            LEFT JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            LEFT JOIN sections sec   ON sc.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN room r         ON scr.new_roomid = r.roomid
            LEFT JOIN building b     ON r.buildingid = b.buildingid
            LEFT JOIN timeslot ts_s  ON scr.new_starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e  ON scr.new_endtimeid   = ts_e.timeid
            WHERE scr.submitted_by = %s
            ORDER BY scr.created_at DESC
        """, [emp_num]) or []

        combined = [dict(r) for r in makeup] + [dict(r) for r in adj]
        combined.sort(key=lambda x: x.get('date_submitted',''), reverse=True)
        return jsonify({'success': True, 'requests': combined})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# --- API: Update a pending faculty request ---
@app.route('/api/faculty/my_requests/<req_type>/<int:req_id>', methods=['PUT'])
def api_faculty_update_request(req_type, req_id):
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return jsonify({'success': False}), 401
    _ensure_request_tables()
    emp_num = session.get('employeenumber')
    if not emp_num:
        acc = query_db("SELECT employeenumber FROM accounts WHERE username=%s",
                       [session.get('username')], one=True)
        emp_num = acc['employeenumber'] if acc else None

    data = request.json or {}

    def _time_to_id(t_str):
        from datetime import datetime as _dt
        if not t_str: return None
        try:
            t = _dt.strptime(t_str.strip(), '%I:%M %p').time()
            row = query_db("SELECT timeid FROM timeslot WHERE timevalue=%s",[t],one=True)
            return row['timeid'] if row else None
        except Exception: return None

    try:
        room_id    = data.get('room_id')
        start_tid  = _time_to_id(data.get('start_time',''))
        end_tid    = _time_to_id(data.get('end_time',''))
        reason     = data.get('reason','')
        rid        = int(room_id) if room_id else None

        if start_tid and end_tid and end_tid <= start_tid:
            return jsonify({'success': False, 'error': 'End Time must be later than Start Time.'}), 400

        def _check_duration(scheduleid):
            """Returns an error string if the new time span doesn't match the subject's
            required contact hours (lecture + laboratory), else None."""
            if not (scheduleid and start_tid and end_tid):
                return None
            subj_row = query_db("""
                SELECT cs.subjectcode, cs.lecturehours, cs.laboratoryhours
                FROM schedule sc JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                WHERE sc.scheduleid = %s
            """, [scheduleid], one=True)
            if not subj_row:
                return None
            required_hours  = float(subj_row.get('lecturehours') or 0) + float(subj_row.get('laboratoryhours') or 0)
            requested_hours = (end_tid - start_tid) * 0.5
            if required_hours > 0 and abs(requested_hours - required_hours) > 0.01:
                return (f"Selected time span is {requested_hours:g}h, but {subj_row['subjectcode']} requires "
                        f"{required_hours:g}h per session.")
            return None

        conn2 = get_db_connection()
        if not conn2:
            return jsonify({'success': False, 'error': 'Database connection failed.'}), 500
        try:
            cur2 = conn2.cursor()
            if req_type == 'makeup':
                req_date = data.get('request_date') or None
                existing = query_db(
                    "SELECT requestid, scheduleid FROM class_meeting_request WHERE requestid=%s AND submitted_by=%s AND status='Pending'",
                    [req_id, emp_num], one=True)
                if not existing:
                    return jsonify({'success': False, 'error': 'Request not found or not editable.'}), 404
                _dur_err = _check_duration(existing.get('scheduleid'))
                if _dur_err:
                    return jsonify({'success': False, 'error': _dur_err}), 400
                cur2.execute("""
                    UPDATE class_meeting_request
                    SET requested_date=%s, new_starttimeid=%s, new_endtimeid=%s,
                        new_roomid=%s, reason=%s
                    WHERE requestid=%s
                """, (req_date, start_tid, end_tid, rid, reason, req_id))

            elif req_type == 'adjustment':
                new_day  = data.get('day','') or None
                eff_from = data.get('start_date') or None
                end_date = data.get('end_date') or None
                existing = query_db(
                    "SELECT requestid, scheduleid FROM schedule_change_request WHERE requestid=%s AND submitted_by=%s AND status='Pending'",
                    [req_id, emp_num], one=True)
                if not existing:
                    return jsonify({'success': False, 'error': 'Request not found or not editable.'}), 404
                _dur_err = _check_duration(existing.get('scheduleid'))
                if _dur_err:
                    return jsonify({'success': False, 'error': _dur_err}), 400
                cur2.execute("""
                    UPDATE schedule_change_request
                    SET new_daydesc=%s, new_starttimeid=%s, new_endtimeid=%s,
                        new_roomid=%s, effective_from=%s, end_date=%s, reason=%s
                    WHERE requestid=%s
                """, (new_day, start_tid, end_tid, rid, eff_from, end_date, reason, req_id))
            else:
                return jsonify({'success': False, 'error': 'Unknown type.'}), 400

            conn2.commit()
        finally:
            conn2.close()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# --- API: Cancel a pending faculty request ---
@app.route('/api/faculty/my_requests/<req_type>/<int:req_id>', methods=['DELETE'])
def api_faculty_cancel_request(req_type, req_id):
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return jsonify({'success': False}), 401
    _ensure_request_tables()
    emp_num = session.get('employeenumber')
    if not emp_num:
        acc = query_db("SELECT employeenumber FROM accounts WHERE username=%s",
                       [session.get('username')], one=True)
        emp_num = acc['employeenumber'] if acc else None
    try:
        conn3 = get_db_connection()
        if not conn3:
            return jsonify({'success': False, 'error': 'Database connection failed.'}), 500
        try:
            cur3 = conn3.cursor()
            if req_type == 'makeup':
                existing = query_db(
                    "SELECT requestid FROM class_meeting_request WHERE requestid=%s AND submitted_by=%s AND status='Pending'",
                    [req_id, emp_num], one=True)
                if not existing:
                    return jsonify({'success': False, 'error': 'Request not found or already decided.'}), 404
                cur3.execute("UPDATE class_meeting_request SET status='Cancelled' WHERE requestid=%s", [req_id])
            elif req_type == 'adjustment':
                existing = query_db(
                    "SELECT requestid FROM schedule_change_request WHERE requestid=%s AND submitted_by=%s AND status='Pending'",
                    [req_id, emp_num], one=True)
                if not existing:
                    return jsonify({'success': False, 'error': 'Request not found or already decided.'}), 404
                cur3.execute("UPDATE schedule_change_request SET status='Cancelled' WHERE requestid=%s", [req_id])
            else:
                return jsonify({'success': False, 'error': 'Unknown type.'}), 400
            conn3.commit()
        finally:
            conn3.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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

# --- API: Faculty's published subjects for new request submission ---
@app.route('/api/faculty/my_schedule_subjects')
def api_faculty_my_schedule_subjects():
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return jsonify({'success': False}), 401
    try:
        emp_num = session.get('employeenumber')
        if not emp_num:
            acc = query_db("SELECT employeenumber FROM accounts WHERE username=%s", [session.get('username')], one=True)
            emp_num = acc['employeenumber'] if acc else None
        if not emp_num:
            return jsonify({'success': False, 'error': 'No employee number'}), 400
        rows = query_db("""
            SELECT DISTINCT
                cs.subjectcode,
                cs.subjectname,
                sec.sectionid,
                sec.sectionname,
                sc.scheduleid
            FROM schedule sc
            JOIN schedule_version sv ON sc.scheduleid = sv.scheduleid AND sv.status = 'Published'
            JOIN semester sem         ON sc.semesterid = sem.semesterid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN sections sec ON sc.sectionid = sec.sectionid
            WHERE sc.employeenumber = %s
              AND sem.isactive = TRUE
            ORDER BY cs.subjectcode, sec.sectionname
        """, [str(emp_num)]) or []
        return jsonify({'success': True, 'subjects': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- API: Current faculty profile (name, designation) ---
@app.route('/api/faculty/me')
def api_faculty_me():
    if 'loggedin' not in session:
        return jsonify({'success': False}), 401
    try:
        emp_num = session.get('employeenumber')
        if not emp_num:
            acc = query_db("SELECT employeenumber FROM accounts WHERE username = %s",
                           [session.get('username')], one=True)
            emp_num = acc['employeenumber'] if acc else None
        if not emp_num:
            return jsonify({'success': False, 'error': 'No employee number'}), 400
        row = query_db("""
            SELECT f.employeenumber,
                   f.firstname, f.lastname,
                   COALESCE(d.designationname, et.typename, '') AS designation
            FROM faculty f
            LEFT JOIN designation d    ON f.designationid    = d.designationid
            LEFT JOIN employeetype et  ON f.employeetypeid   = et.employeetypeid
            WHERE f.employeenumber = %s
        """, [emp_num], one=True)
        if not row:
            return jsonify({'success': False, 'error': 'Not found'}), 404
        return jsonify({
            'success': True,
            'employeenumber': row['employeenumber'],
            'fullname': (row['firstname'] or '') + ' ' + (row['lastname'] or ''),
            'designation': row['designation'],
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ── User profile API ────────────────────────────────────────────────────────
_PROFILE_PHOTO_DIR = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'profile_photos')
os.makedirs(_PROFILE_PHOTO_DIR, exist_ok=True)

@app.route('/api/search')
def api_search():
    if 'loggedin' not in session: return jsonify({'results': []}), 401
    q = request.args.get('q', '').strip()
    if len(q) < 2: return jsonify({'results': []})
    role = session.get('role', '')
    like = f'%{q}%'
    results = []
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Rooms — all roles
        cur.execute("""
            SELECT r.roomname, r.roomtype, b.buildingname
            FROM room r LEFT JOIN building b ON r.buildingid = b.buildingid
            WHERE r.roomname ILIKE %s OR r.roomtype ILIKE %s OR b.buildingname ILIKE %s
            ORDER BY r.roomname LIMIT 5
        """, (like, like, like))
        room_url = '/admin/rooms' if role == 'Admin' else '/room'
        for row in cur.fetchall():
            sub = ' · '.join(filter(None, [row.get('roomtype'), row.get('buildingname')]))
            results.append({'category': 'Room', 'label': row['roomname'], 'sub': sub, 'url': room_url, 'icon': 'fa-door-open'})

        # Faculty/Employees — all roles (Faculty role shows info-only, no nav)
        cur.execute("""
            SELECT f.employeenumber, f.firstname, f.lastname, et.typename
            FROM faculty f
            LEFT JOIN employeetype et ON f.employeetypeid = et.employeetypeid
            WHERE f.employeestatus != 'Archive'
              AND (f.firstname ILIKE %s OR f.lastname ILIKE %s
                   OR f.employeenumber ILIKE %s
                   OR CONCAT(f.firstname, ' ', f.lastname) ILIKE %s)
            ORDER BY f.lastname LIMIT 5
        """, (like, like, like, like))
        if role == 'Academic Head':
            emp_url = '/employee'
        elif role == 'Admin':
            emp_url = '/admin/employee'
        else:
            emp_url = None
        for row in cur.fetchall():
            results.append({'category': 'Faculty', 'label': f"{row['firstname']} {row['lastname']}",
                'sub': ' · '.join(filter(None, [row.get('employeenumber'), row.get('typename')])),
                'url': emp_url, 'icon': 'fa-user'})

        # Programs — Academic Head and Admin
        if role in ('Academic Head', 'Admin'):
            cur.execute("""
                SELECT programcode, programname FROM programs
                WHERE isactive = TRUE AND (programcode ILIKE %s OR programname ILIKE %s)
                ORDER BY programcode LIMIT 4
            """, (like, like))
            cur_url = '/curriculum' if role == 'Academic Head' else '/admin/curriculum'
            for row in cur.fetchall():
                results.append({'category': 'Program', 'label': row['programcode'],
                    'sub': row['programname'], 'url': cur_url, 'icon': 'fa-graduation-cap'})

        # Curriculum subjects — Academic Head and Admin
        if role in ('Academic Head', 'Admin'):
            cur.execute("""
                SELECT cs.subjectcode, cs.subjectname
                FROM curriculumsubject cs
                WHERE cs.subjectcode ILIKE %s OR cs.subjectname ILIKE %s
                GROUP BY cs.subjectcode, cs.subjectname
                ORDER BY cs.subjectcode LIMIT 5
            """, (like, like))
            for row in cur.fetchall():
                results.append({'category': 'Subject', 'label': row['subjectcode'],
                    'sub': row['subjectname'], 'url': cur_url, 'icon': 'fa-book'})

        # Sections — Academic Head and Admin
        if role in ('Academic Head', 'Admin'):
            cur.execute("""
                SELECT s.sectionname, pyl.programcode
                FROM sections s
                LEFT JOIN program_yearlevel pyl ON s.programyearlevelid = pyl.programyearlevelid
                WHERE s.isactive = TRUE AND s.sectionname ILIKE %s
                ORDER BY s.sectionname LIMIT 5
            """, (like,))
            sched_url = '/schedule' if role == 'Academic Head' else '/admin/schedule'
            for row in cur.fetchall():
                results.append({'category': 'Section', 'label': row['sectionname'],
                    'sub': row.get('programcode') or '', 'url': sched_url, 'icon': 'fa-users'})

        return jsonify({'results': results})
    except Exception as e:
        return jsonify({'results': [], 'error': str(e)})
    finally:
        cur.close(); conn.close()

@app.route('/api/user/me')
def api_user_me():
    if 'loggedin' not in session: return jsonify({'success': False}), 401
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS last_login TIMESTAMP")
        cur.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS profile_photo VARCHAR(255)")
        conn.commit()
        username = session.get('username')
        cur.execute("""
            SELECT a.last_login, a.profile_photo,
                   f.employeenumber, f.firstname, f.lastname, f.email, f.employeestatus,
                   d.designationid, d.designationname,
                   s.specializationid, s.specializationname,
                   et.employeetypeid, et.typename
            FROM accounts a
            LEFT JOIN faculty f ON a.employeenumber = f.employeenumber
            LEFT JOIN designation d ON f.designationid = d.designationid
            LEFT JOIN specialization s ON f.specializationid = s.specializationid
            LEFT JOIN employeetype et ON f.employeetypeid = et.employeetypeid
            WHERE a.username = %s
        """, (username,))
        row = cur.fetchone()
        if not row: return jsonify({'success': False}), 404
        ll = row['last_login']
        return jsonify({
            'success': True,
            'emp_num': row['employeenumber'] or '',
            'fullname': f"{row['firstname'] or ''} {row['lastname'] or ''}".strip(),
            'firstname': row['firstname'] or '',
            'lastname': row['lastname'] or '',
            'designation': row['designationname'] or '',
            'designation_id': row['designationid'],
            'specialization': row['specializationname'] or '',
            'specialization_id': row['specializationid'],
            'employment_status': row['employeestatus'] or '',
            'employee_type': row['typename'] or '',
            'last_login': ll.strftime('%B %d, %Y %I:%M %p') if ll else None,
            'photo_url': row['profile_photo'] or None,
            'email': row['email'] or '',
            'role': session.get('role', ''),
        })
    except Exception as e:
        conn.rollback(); return jsonify({'success': False, 'error': str(e)}), 500
    finally: cur.close(); conn.close()

@app.route('/api/user/options')
def api_user_options():
    if 'loggedin' not in session: return jsonify({'success': False}), 401
    try:
        designations    = query_db("SELECT designationid, designationname FROM designation ORDER BY designationname")
        specializations = query_db("SELECT specializationid, specializationname FROM specialization ORDER BY specializationname")
        return jsonify({
            'success': True,
            'designations': [dict(r) for r in designations],
            'specializations': [dict(r) for r in specializations],
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/user/profile/update', methods=['POST'])
def api_user_profile_update():
    if 'loggedin' not in session: return jsonify({'success': False}), 401
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        username = session.get('username')
        cur.execute("SELECT employeenumber FROM accounts WHERE username = %s", (username,))
        acc = cur.fetchone()
        emp_num = acc['employeenumber'] if acc else None
        if not emp_num: return jsonify({'success': False, 'error': 'No linked faculty record'}), 400
        designation_id    = request.form.get('designation_id') or None
        specialization_id = request.form.get('specialization_id') or None
        employment_status = request.form.get('employment_status') or None
        cur.execute("""
            UPDATE faculty SET
                designationid    = COALESCE(%s::int, designationid),
                specializationid = COALESCE(%s::int, specializationid),
                employeestatus   = COALESCE(%s, employeestatus)
            WHERE employeenumber = %s
        """, (designation_id, specialization_id, employment_status, emp_num))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback(); return jsonify({'success': False, 'error': str(e)}), 500
    finally: cur.close(); conn.close()

@app.route('/api/user/photo/upload', methods=['POST'])
def api_user_photo_upload():
    if 'loggedin' not in session: return jsonify({'success': False}), 401
    if 'photo' not in request.files: return jsonify({'success': False, 'error': 'No file'}), 400
    file = request.files['photo']
    if not file.filename: return jsonify({'success': False, 'error': 'No file selected'}), 400
    ext = os.path.splitext(secure_filename(file.filename))[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png'): return jsonify({'success': False, 'error': 'Only JPG/PNG allowed'}), 400
    username   = session.get('username')
    filename   = secure_filename(f"profile_{username}{ext}")
    save_path  = os.path.join(_PROFILE_PHOTO_DIR, filename)
    file.save(save_path)
    photo_url  = f"/static/uploads/profile_photos/{filename}"
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS profile_photo VARCHAR(255)")
        cur.execute("UPDATE accounts SET profile_photo = %s WHERE username = %s", (photo_url, username))
        conn.commit()
        return jsonify({'success': True, 'photo_url': photo_url})
    except Exception as e:
        conn.rollback(); return jsonify({'success': False, 'error': str(e)}), 500
    finally: cur.close(); conn.close()

@app.route('/api/user/password/change', methods=['POST'])
def api_user_password_change():
    if 'loggedin' not in session: return jsonify({'success': False}), 401
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        username     = session.get('username')
        current_pw   = request.form.get('current_password', '')
        new_pw       = request.form.get('new_password', '')
        confirm_pw   = request.form.get('confirm_password', '')
        if new_pw != confirm_pw: return jsonify({'success': False, 'error': 'New passwords do not match'}), 400
        if len(new_pw) < 6: return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400
        cur.execute("SELECT passwordhash FROM accounts WHERE username = %s", (username,))
        row = cur.fetchone()
        if not row or not check_password_hash(row['passwordhash'], current_pw):
            return jsonify({'success': False, 'error': 'Current password is incorrect'}), 400
        cur.execute("UPDATE accounts SET passwordhash = %s WHERE username = %s",
                    (generate_password_hash(new_pw), username))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback(); return jsonify({'success': False, 'error': str(e)}), 500
    finally: cur.close(); conn.close()

@app.route('/api/user/email/update', methods=['POST'])
def api_user_email_update():
    if 'loggedin' not in session: return jsonify({'success': False}), 401
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        username = session.get('username')
        email    = request.form.get('email', '').strip()
        cur.execute("SELECT employeenumber FROM accounts WHERE username = %s", (username,))
        acc = cur.fetchone()
        emp_num = acc['employeenumber'] if acc else None
        if emp_num and email:
            cur.execute("UPDATE faculty SET email = %s WHERE employeenumber = %s", (email, emp_num))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback(); return jsonify({'success': False, 'error': str(e)}), 500
    finally: cur.close(); conn.close()

# --- API: Faculty's own published-schedule assignments (for subject/section dropdown) ---
@app.route('/api/faculty/my_assignments')
def api_faculty_my_assignments():
    if 'loggedin' not in session:
        return jsonify({'success': False}), 401
    try:
        emp_num = session.get('employeenumber')
        if not emp_num:
            acc = query_db("SELECT employeenumber FROM accounts WHERE username = %s",
                           [session.get('username')], one=True)
            emp_num = acc['employeenumber'] if acc else None
        if not emp_num:
            return jsonify({'success': False, 'error': 'No employee number'}), 400

        # Mirror the exact query pattern from api_faculty_teaching_assignments which works:
        # start from schedule_sessions → schedule_version → schedule (all INNER JOINs)
        # Include both 'Published' and 'Draft' statuses to match the Teaching Assignment page
        rows = query_db("""
            SELECT DISTINCT
                cs.subjectcode,
                cs.subjectname,
                sc.scheduleid,
                COALESCE(sec.sectionid, sc.sectionid)              AS sectionid,
                COALESCE(sec.sectionname, '')                      AS sectionname,
                COALESCE(pyl.programcode, '')                      AS programcode,
                COALESCE(pyl.yearlevel::text, '')                  AS yearlevel,
                COALESCE(ss.daydesc, '')                           AS daydesc,
                TO_CHAR(ts_s.timevalue, 'HH12:MI AM')             AS start_time,
                TO_CHAR(ts_e.timevalue, 'HH12:MI AM')             AS end_time,
                COALESCE(r.roomid::text, '')                       AS roomid,
                COALESCE(r.roomname, '')                           AS roomname,
                COALESCE(b.buildingname, '')                       AS buildingname,
                COALESCE(r.roomtype, 'Lecture')                    AS roomtype,
                sv.status
            FROM schedule_sessions ss
            JOIN schedule_version sv  ON ss.versionid            = sv.versionid
            JOIN schedule sc          ON sv.scheduleid            = sc.scheduleid
            JOIN semester sem         ON sc.semesterid            = sem.semesterid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid   = cs.curriculumsubjectid
            LEFT JOIN sections sec    ON sc.sectionid             = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN room r          ON ss.roomid                = r.roomid
            LEFT JOIN building b      ON r.buildingid             = b.buildingid
            LEFT JOIN timeslot ts_s   ON ss.starttimeid           = ts_s.timeid
            LEFT JOIN timeslot ts_e   ON ss.endtimeid             = ts_e.timeid
            WHERE sv.status IN ('Published', 'Draft')
              AND sc.employeenumber = %s
              AND sem.isactive = TRUE
            ORDER BY cs.subjectcode, sectionname, daydesc
        """, [str(emp_num)]) or []
        return jsonify({'success': True, 'assignments': [dict(r) for r in rows],
                        '_debug_emp': str(emp_num), '_count': len(rows)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), '_debug_emp': str(emp_num) if 'emp_num' in dir() else 'N/A'}), 500

# --- API: Faculty's blocked time slots for a given day (published schedule occupancy) ---
@app.route('/api/faculty/blocked_times')
def api_faculty_blocked_times():
    if 'loggedin' not in session:
        return jsonify({'success': False}), 401
    day        = request.args.get('day', '').strip()
    req_date   = request.args.get('date', '').strip()
    room_id    = request.args.get('room_id', '').strip()
    section_id = request.args.get('section_id', '').strip()
    if req_date and not day:
        try:
            from datetime import date as _date
            day = _date.fromisoformat(req_date).strftime('%A')
        except Exception:
            pass
    if not day:
        return jsonify({'success': True, 'blocked': []})
    try:
        emp_num = session.get('employeenumber')
        if not emp_num:
            acc = query_db("SELECT employeenumber FROM accounts WHERE username = %s",
                           [session.get('username')], one=True)
            emp_num = acc['employeenumber'] if acc else None
        if not emp_num:
            return jsonify({'success': True, 'blocked': []})
        rows = query_db("""
            SELECT DISTINCT
                TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_time,
                TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_time,
                ts_s.timevalue                          AS start_raw,
                ts_e.timevalue                          AS end_raw
            FROM schedule_sessions ss
            JOIN schedule_version sv  ON ss.versionid  = sv.versionid
            JOIN schedule sc          ON sv.scheduleid  = sc.scheduleid
            JOIN timeslot ts_s        ON ss.starttimeid = ts_s.timeid
            JOIN timeslot ts_e        ON ss.endtimeid   = ts_e.timeid
            WHERE sv.status IN ('Published', 'Draft')
              AND sc.employeenumber = %s
              AND UPPER(ss.daydesc) = UPPER(%s)
            ORDER BY ts_s.timevalue
        """, [str(emp_num), day]) or []
        blocked = [
            {'start_time': r['start_time'], 'end_time': r['end_time'],
             'start_raw': str(r['start_raw']), 'end_raw': str(r['end_raw'])}
            for r in rows
        ]
        # When a specific room is provided, also block times that room is already occupied
        # so the time dropdown grays out those slots before the user selects them.
        if room_id:
            try:
                room_rows = query_db("""
                    SELECT DISTINCT
                        TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_time,
                        TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_time,
                        ts_s.timevalue                          AS start_raw,
                        ts_e.timevalue                          AS end_raw
                    FROM schedule_sessions ss
                    JOIN schedule_version sv ON ss.versionid  = sv.versionid
                    JOIN timeslot ts_s       ON ss.starttimeid = ts_s.timeid
                    JOIN timeslot ts_e       ON ss.endtimeid   = ts_e.timeid
                    WHERE sv.status = 'Published'
                      AND ss.roomid = %s
                      AND UPPER(ss.daydesc) = UPPER(%s)
                    ORDER BY ts_s.timevalue
                """, [int(room_id), day]) or []
                blocked += [
                    {'start_time': r['start_time'], 'end_time': r['end_time'],
                     'start_raw': str(r['start_raw']), 'end_raw': str(r['end_raw'])}
                    for r in room_rows
                ]
            except Exception:
                pass
        # When a section is provided, also block times that section already has another class.
        if section_id:
            try:
                sect_rows = query_db("""
                    SELECT DISTINCT
                        TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_time,
                        TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_time,
                        ts_s.timevalue                          AS start_raw,
                        ts_e.timevalue                          AS end_raw
                    FROM schedule_sessions ss
                    JOIN schedule_version sv ON ss.versionid  = sv.versionid
                    JOIN schedule sc         ON sv.scheduleid = sc.scheduleid
                    JOIN timeslot ts_s       ON ss.starttimeid = ts_s.timeid
                    JOIN timeslot ts_e       ON ss.endtimeid   = ts_e.timeid
                    WHERE sv.status = 'Published'
                      AND sc.sectionid = %s
                      AND UPPER(ss.daydesc) = UPPER(%s)
                    ORDER BY ts_s.timevalue
                """, [int(section_id), day]) or []
                blocked += [
                    {'start_time': r['start_time'], 'end_time': r['end_time'],
                     'start_raw': str(r['start_raw']), 'end_raw': str(r['end_raw'])}
                    for r in sect_rows
                ]
            except Exception:
                pass
        # Also block times where the faculty has an active Local Arrangement on this day.
        if emp_num:
            try:
                la_rows = query_db("""
                    SELECT DISTINCT
                        TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_time,
                        TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_time,
                        ts_s.timevalue                          AS start_raw,
                        ts_e.timevalue                          AS end_raw
                    FROM local_arrangement_sessions las
                    JOIN local_arrangement la ON las.arrangementid = la.arrangementid
                    JOIN timeslot ts_s ON las.starttimeid = ts_s.timeid
                    JOIN timeslot ts_e ON las.endtimeid   = ts_e.timeid
                    WHERE la.status = 'Approved'
                      AND las.faculty_employeenumber = %s
                      AND UPPER(las.daydesc) = UPPER(%s)
                    ORDER BY ts_s.timevalue
                """, [str(emp_num), day]) or []
                blocked += [
                    {'start_time': r['start_time'], 'end_time': r['end_time'],
                     'start_raw': str(r['start_raw']), 'end_raw': str(r['end_raw'])}
                    for r in la_rows
                ]
            except Exception:
                pass
        # Also block times for the selected room via Local Arrangements.
        if room_id:
            try:
                la_room_rows = query_db("""
                    SELECT DISTINCT
                        TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_time,
                        TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_time,
                        ts_s.timevalue                          AS start_raw,
                        ts_e.timevalue                          AS end_raw
                    FROM local_arrangement_sessions las
                    JOIN local_arrangement la ON las.arrangementid = la.arrangementid
                    JOIN timeslot ts_s ON las.starttimeid = ts_s.timeid
                    JOIN timeslot ts_e ON las.endtimeid   = ts_e.timeid
                    WHERE la.status = 'Approved'
                      AND las.roomid = %s
                      AND UPPER(las.daydesc) = UPPER(%s)
                    ORDER BY ts_s.timevalue
                """, [int(room_id), day]) or []
                blocked += [
                    {'start_time': r['start_time'], 'end_time': r['end_time'],
                     'start_raw': str(r['start_raw']), 'end_raw': str(r['end_raw'])}
                    for r in la_room_rows
                ]
            except Exception:
                pass
        return jsonify({'success': True, 'blocked': blocked})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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
        filters.append("UPPER(pyl.programcode) = UPPER(%s)")
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

    # Require at least one meaningful filter to prevent dumping all programs
    if not any([program, year_level, section_id, emp_num]):
        return jsonify({'success': False, 'error': 'Select at least one filter'}), 400

    try:
        rows = query_db(f"""
            SELECT
                cs.subjectcode,
                cs.subjectname,
                cs.creditunits,
                COALESCE(cs.lecturehours,    0) AS lecturehours,
                COALESCE(cs.laboratoryhours, 0) AS laboratoryhours,
                sec.sectionname,
                pyl.yearlevel,
                pyl.programcode,
                ss.daydesc,
                r.roomname,
                f.lastname || ', ' || f.firstname AS instructor,
                sc.employeenumber,
                TO_CHAR(ts_s.timevalue, 'HH24:MI') AS start_time,
                TO_CHAR(ts_e.timevalue, 'HH24:MI') AS end_time,
                TO_CHAR(ts_s.timevalue, 'HH12:MI AM') || ' - ' ||
                TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS time_range,
                (COALESCE(cs.lecturehours,0) + COALESCE(cs.laboratoryhours,0)) AS total_hours
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
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
        notes      = data.get('note', '')
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

        if start_tid and end_tid and end_tid <= start_tid:
            return jsonify({'success': False, 'error': 'End Time must be later than Start Time.'}), 400

        # The requested time span must match the subject's required contact hours
        # (lecture + laboratory) — each timeslot step is 30 minutes.
        if subject and start_tid and end_tid:
            subj_hours_row = query_db(
                "SELECT lecturehours, laboratoryhours FROM curriculumsubject WHERE subjectcode = %s LIMIT 1",
                [subject], one=True)
            if subj_hours_row:
                required_hours = float(subj_hours_row.get('lecturehours') or 0) + float(subj_hours_row.get('laboratoryhours') or 0)
                requested_hours = (end_tid - start_tid) * 0.5
                if required_hours > 0 and abs(requested_hours - required_hours) > 0.01:
                    return jsonify({'success': False, 'error':
                        f"Selected time span is {requested_hours:g}h, but {subject} requires "
                        f"{required_hours:g}h per session."}), 400

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

        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'error': 'Database connection failed.'}), 500
        try:
            cur = conn.cursor()
            if req_type == 'makeup':
                req_date = data.get('request_date') or None
                if not scheduleid:
                    return jsonify({'success': False,
                        'error': 'No published schedule found for the given subject and section.'}), 400
                if not start_tid or not end_tid:
                    return jsonify({'success': False, 'error': 'Invalid time selection.'}), 400
                # Block submission if faculty already has a committed schedule at the requested day/time
                if req_date and start_time and end_time:
                    try:
                        day_of_week = _dt.strptime(req_date, '%Y-%m-%d').strftime('%A')
                        fac_conflict = query_db("""
                            SELECT cs.subjectcode FROM schedule_sessions ss
                            JOIN schedule_version sv ON ss.versionid = sv.versionid
                            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
                            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                            JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                            JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
                            WHERE sv.status = 'Published'
                              AND sc.employeenumber = %s
                              AND UPPER(ss.daydesc) = UPPER(%s)
                              AND ts_s.timevalue < %s::time AND ts_e.timevalue > %s::time
                            LIMIT 1
                        """, [submitted_by, day_of_week, end_time, start_time], one=True)
                        if fac_conflict:
                            return jsonify({'success': False,
                                'error': f"You already have {fac_conflict['subjectcode']} scheduled on {day_of_week} at this time. Make-up class cannot be submitted when you have an existing commitment."}), 400
                    except Exception as _ce:
                        print(f"[submit_request conflict check] {_ce}")
                cur.execute("""
                    INSERT INTO class_meeting_request
                        (scheduleid, requested_date, new_starttimeid, new_endtimeid,
                         new_roomid, reason, notes, submitted_by, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pending')
                """, (scheduleid, req_date, start_tid, end_tid, rid, reason, notes, submitted_by))

            elif req_type == 'adjustment':
                day = data.get('day', '') or None
                if not scheduleid or not versionid:
                    return jsonify({'success': False,
                        'error': 'No published schedule found for the given subject and section.'}), 400
                parts = []
                if day:                   parts.append('Day')
                if start_tid and end_tid: parts.append('Time')
                if rid:                   parts.append('Room')
                change_type = '+'.join(parts) if parts else 'Day'
                cur.execute("""
                    INSERT INTO schedule_change_request
                        (scheduleid, versionid, change_type, new_daydesc,
                         new_starttimeid, new_endtimeid, new_roomid,
                         effective_from, reason, submitted_by, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_DATE, %s, %s, 'Pending')
                """, (scheduleid, versionid, change_type, day,
                      start_tid, end_tid, rid, reason, submitted_by))
            else:
                return jsonify({'success': False, 'error': 'Unknown request type.'}), 400

            conn.commit()
        finally:
            conn.close()

        return jsonify({'success': True})
    except Exception as e:
        print(f"[api_faculty_submit_request] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# --- API: Available rooms for a given day/time window ---
@app.route('/api/faculty/check_request_conflicts')
def api_faculty_check_request_conflicts():
    """Check room, faculty, and section conflicts at the requested day+time."""
    if 'loggedin' not in session:
        return jsonify({'success': False}), 401
    req_date    = request.args.get('date', '').strip()
    start_time  = request.args.get('start_time', '').strip()
    end_time    = request.args.get('end_time', '').strip()
    section_id  = request.args.get('section_id', '').strip()
    day         = request.args.get('day', '').strip()
    room_id     = request.args.get('room_id', '').strip()
    schedule_id = request.args.get('schedule_id', '').strip()   # exclude own schedule from conflict check
    subject_code = request.args.get('subject_code', '').strip()
    if not (start_time and end_time):
        return jsonify({'success': True,
                        'faculty_conflict': False, 'section_conflict': False, 'room_conflict': False})
    # Derive day-of-week from date if not provided
    if req_date and not day:
        try:
            from datetime import date as _date
            day = _date.fromisoformat(req_date).strftime('%A')
        except Exception:
            pass
    if not day:
        return jsonify({'success': True,
                        'faculty_conflict': False, 'section_conflict': False, 'room_conflict': False})
    try:
        emp_num = session.get('employeenumber')
        if not emp_num:
            acc = query_db("SELECT employeenumber FROM accounts WHERE username=%s", [session.get('username')], one=True)
            emp_num = acc['employeenumber'] if acc else None

        # Derive semesterid from the schedule being changed, so conflict checks only
        # compare sessions within the same Academic Year and Semester.
        sem_id_for_check = None
        if schedule_id and schedule_id.isdigit():
            _sr = query_db("SELECT semesterid FROM schedule WHERE scheduleid = %s LIMIT 1",
                           [int(schedule_id)], one=True)
            if _sr:
                sem_id_for_check = _sr['semesterid']
        if sem_id_for_check is None:
            # Fallback: use the currently-active semester
            _as = query_db("""
                SELECT s.semesterid FROM semester s WHERE s.isactive = TRUE LIMIT 1
            """, one=True)
            if _as:
                sem_id_for_check = _as['semesterid']

        _sem_filter = "AND sc.semesterid = %s" if sem_id_for_check else ""

        faculty_conflict = False
        section_conflict = False
        room_conflict    = False
        faculty_detail   = ''
        section_detail   = ''
        room_detail      = ''

        if emp_num:
            _fac_excl = f"AND sv.scheduleid != {int(schedule_id)}" if schedule_id and schedule_id.isdigit() else ""
            _fac_params = [str(emp_num), day, end_time, start_time]
            if sem_id_for_check:
                _fac_params.append(sem_id_for_check)
            rows = query_db(f"""
                SELECT cs.subjectcode,
                       TO_CHAR(ts_s.timevalue,'HH12:MI AM') AS start_t,
                       TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS end_t
                FROM schedule_sessions ss
                JOIN schedule_version sv ON ss.versionid  = sv.versionid
                JOIN schedule sc         ON sv.scheduleid  = sc.scheduleid
                JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
                WHERE sv.status IN ('Published','Draft')
                  AND sc.employeenumber = %s
                  AND UPPER(ss.daydesc) = UPPER(%s)
                  AND ts_s.timevalue < %s::time
                  AND ts_e.timevalue > %s::time
                  {_sem_filter}
                  {_fac_excl}
                LIMIT 1
            """, _fac_params) or []
            if rows:
                faculty_conflict = True
                r = rows[0]
                faculty_detail = f"You already have {r['subjectcode']} ({r['start_t']}–{r['end_t']}) on {day}."
            # Also check Local Arrangement sessions for this faculty (scoped by semester)
            if not faculty_conflict:
                try:
                    _la_fac_params = [str(emp_num), day, end_time, start_time]
                    _la_sem_filter = ""
                    if sem_id_for_check:
                        _la_sem_filter = "AND la.semesterid = %s"
                        _la_fac_params.append(sem_id_for_check)
                    la_rows = query_db(f"""
                        SELECT las.subjectcode,
                               TO_CHAR(ts_s.timevalue,'HH12:MI AM') AS start_t,
                               TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS end_t
                        FROM local_arrangement_sessions las
                        JOIN local_arrangement la ON las.arrangementid = la.arrangementid
                        JOIN timeslot ts_s ON las.starttimeid = ts_s.timeid
                        JOIN timeslot ts_e ON las.endtimeid   = ts_e.timeid
                        WHERE la.status = 'Approved'
                          AND las.faculty_employeenumber = %s
                          AND UPPER(las.daydesc) = UPPER(%s)
                          AND ts_s.timevalue < %s::time
                          AND ts_e.timevalue > %s::time
                          {_la_sem_filter}
                        LIMIT 1
                    """, _la_fac_params) or []
                    if la_rows:
                        faculty_conflict = True
                        r = la_rows[0]
                        faculty_detail = f"You have a Local Arrangement ({r['subjectcode'] or 'session'}) on {day} at {r['start_t']}–{r['end_t']}."
                except Exception:
                    pass

        if section_id:
            _sect_excl = f"AND sv.scheduleid != {int(schedule_id)}" if schedule_id and schedule_id.isdigit() else ""
            _sect_params = [int(section_id), day, end_time, start_time]
            if sem_id_for_check:
                _sect_params.append(sem_id_for_check)
            rows2 = query_db(f"""
                SELECT cs.subjectcode,
                       TO_CHAR(ts_s.timevalue,'HH12:MI AM') AS start_t,
                       TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS end_t
                FROM schedule_sessions ss
                JOIN schedule_version sv ON ss.versionid  = sv.versionid
                JOIN schedule sc         ON sv.scheduleid  = sc.scheduleid
                JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
                WHERE sv.status IN ('Published','Draft')
                  AND sc.sectionid = %s
                  AND UPPER(ss.daydesc) = UPPER(%s)
                  AND ts_s.timevalue < %s::time
                  AND ts_e.timevalue > %s::time
                  {_sem_filter}
                  {_sect_excl}
                LIMIT 1
            """, _sect_params) or []
            if rows2:
                section_conflict = True
                r2 = rows2[0]
                section_detail = f"This section already has {r2['subjectcode']} ({r2['start_t']}–{r2['end_t']}) on {day}."

        if room_id:
            try:
                _room_excl = f"AND sv.scheduleid != {int(schedule_id)}" if schedule_id and schedule_id.isdigit() else ""
                _room_params = [int(room_id), day, end_time, start_time]
                if sem_id_for_check:
                    _room_params.append(sem_id_for_check)
                rows3 = query_db(f"""
                    SELECT cs.subjectcode,
                           TO_CHAR(ts_s.timevalue,'HH12:MI AM') AS start_t,
                           TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS end_t
                    FROM schedule_sessions ss
                    JOIN schedule_version sv ON ss.versionid  = sv.versionid
                    JOIN schedule sc         ON sv.scheduleid  = sc.scheduleid
                    JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                    JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                    JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
                    WHERE sv.status = 'Published'
                      AND ss.roomid = %s
                      AND UPPER(ss.daydesc) = UPPER(%s)
                      AND ts_s.timevalue < %s::time
                      AND ts_e.timevalue > %s::time
                      {_sem_filter}
                      {_room_excl}
                    LIMIT 1
                """, _room_params) or []
                if rows3:
                    room_conflict = True
                    r3 = rows3[0]
                    room_detail = f"The selected room is already occupied by {r3['subjectcode']} ({r3['start_t']}–{r3['end_t']}) on {day}."
                # Also check Local Arrangement room occupancy (same semester)
                if not room_conflict:
                    _la_room_params = [int(room_id), day, end_time, start_time]
                    _la_room_sem = ""
                    if sem_id_for_check:
                        _la_room_sem = "AND la.semesterid = %s"
                        _la_room_params.append(sem_id_for_check)
                    la_room = query_db(f"""
                        SELECT las.subjectcode,
                               TO_CHAR(ts_s.timevalue,'HH12:MI AM') AS start_t,
                               TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS end_t
                        FROM local_arrangement_sessions las
                        JOIN local_arrangement la ON las.arrangementid = la.arrangementid
                        JOIN timeslot ts_s ON las.starttimeid = ts_s.timeid
                        JOIN timeslot ts_e ON las.endtimeid   = ts_e.timeid
                        WHERE la.status = 'Approved'
                          AND las.roomid = %s
                          AND UPPER(las.daydesc) = UPPER(%s)
                          AND ts_s.timevalue < %s::time
                          AND ts_e.timevalue > %s::time
                          {_la_room_sem}
                        LIMIT 1
                    """, _la_room_params) or []
                    if la_room:
                        room_conflict = True
                        r_la = la_room[0]
                        room_detail = f"The selected room has a Local Arrangement ({r_la['subjectcode'] or 'session'}) at {r_la['start_t']}–{r_la['end_t']} on {day}."
            except Exception:
                pass

        # Duration check: the requested time span must match the subject's required
        # contact hours (lecture + laboratory) — flagged the same way as the other
        # conflict types so the UI can show it in the same banner.
        duration_conflict = False
        duration_detail   = ''
        if subject_code:
            try:
                from datetime import datetime as _dt3
                _s = _dt3.strptime(start_time, '%I:%M %p')
                _e = _dt3.strptime(end_time, '%I:%M %p')
                req_hours = (_e - _s).total_seconds() / 3600.0
                subj_row = query_db(
                    "SELECT lecturehours, laboratoryhours FROM curriculumsubject WHERE subjectcode = %s LIMIT 1",
                    [subject_code], one=True)
                if subj_row:
                    required_hours = float(subj_row.get('lecturehours') or 0) + float(subj_row.get('laboratoryhours') or 0)
                    if required_hours > 0 and abs(req_hours - required_hours) > 0.01:
                        duration_conflict = True
                        duration_detail = (
                            f"This time span is {req_hours:g}h, but {subject_code} requires "
                            f"{required_hours:g}h per session."
                        )
            except Exception:
                pass

        return jsonify({
            'success':           True,
            'faculty_conflict':  faculty_conflict,
            'section_conflict':  section_conflict,
            'room_conflict':     room_conflict,
            'duration_conflict': duration_conflict,
            'faculty_detail':    faculty_detail,
            'section_detail':    section_detail,
            'room_detail':       room_detail,
            'duration_detail':   duration_detail,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/faculty/available_rooms')
def api_faculty_available_rooms():
    if 'loggedin' not in session:
        return jsonify({'success': False}), 401

    day         = request.args.get('day',         '').strip()
    start_time  = request.args.get('start_time',  '').strip()
    end_time    = request.args.get('end_time',    '').strip()
    room_type   = request.args.get('room_type',   '').strip()
    building_id = request.args.get('building_id', '').strip()
    # Optional: specific date — when provided, also checks approved makeup classes
    req_date    = request.args.get('date',        '').strip()
    # If date provided and day not, derive day-of-week from date
    if req_date and not day:
        try:
            from datetime import date as _date
            _d = _date.fromisoformat(req_date)
            day = _d.strftime('%A')
        except Exception:
            pass

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
            return jsonify({'success': True, 'rooms': [
                {**dict(r), 'available': True} for r in all_rooms
            ]})

        # Official Published schedule occupancy
        booked_official = query_db("""
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

        # Published local arrangement occupancy
        booked_local = query_db("""
            SELECT DISTINCT las.roomid
            FROM local_arrangement_sessions las
            JOIN local_arrangement la ON las.arrangementid = la.arrangementid
            JOIN timeslot ts_s ON las.starttimeid = ts_s.timeid
            JOIN timeslot ts_e ON las.endtimeid   = ts_e.timeid
            WHERE la.status = 'Published'
              AND la.is_active = TRUE
              AND UPPER(las.daydesc) LIKE UPPER(%s)
              AND ts_s.timevalue < %s::time
              AND ts_e.timevalue > %s::time
        """, (f'%{day}%', end_time, start_time)) or []

        booked_ids = {r['roomid'] for r in booked_official} | {r['roomid'] for r in booked_local}

        # If a specific date was given, also block rooms with approved makeup classes on that date
        if req_date and start_time and end_time:
            try:
                _ensure_request_tables()
                booked_makeup = query_db("""
                    SELECT DISTINCT cmr.new_roomid AS roomid
                    FROM class_meeting_request cmr
                    JOIN timeslot ts_s ON cmr.new_starttimeid = ts_s.timeid
                    JOIN timeslot ts_e ON cmr.new_endtimeid   = ts_e.timeid
                    WHERE cmr.status = 'Approved'
                      AND cmr.requested_date = %s
                      AND ts_s.timevalue < %s::time
                      AND ts_e.timevalue > %s::time
                """, (req_date, end_time, start_time)) or []
                booked_ids |= {r['roomid'] for r in booked_makeup if r['roomid']}
            except Exception as _me:
                print(f"[available_rooms makeup check] {_me}")

        available  = [{**dict(r), 'available': r['roomid'] not in booked_ids} for r in all_rooms]
        # Return only truly available rooms
        available  = [r for r in available if r['available']]
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

import time as _time_mod
_faculty_map_cache: dict = {}          # {'data': ..., 'ts': float}
_FACULTY_MAP_TTL = 60                  # seconds — refresh after 1 minute

def _load_faculty_map(force_refresh: bool = False):
    global _faculty_map_cache
    now = _time_mod.monotonic()
    if (not force_refresh
            and _faculty_map_cache.get('data')
            and now - _faculty_map_cache.get('ts', 0) < _FACULTY_MAP_TTL):
        return _faculty_map_cache['data']

    rows = query_db("""
        SELECT f.employeenumber, CONCAT(f.lastname, ', ', f.firstname) AS fullname,
               f.employeestatus, f.designationid, et.regularload, et.parttimeload,
               COALESCE(et.teachingsubstitution, 0) AS teachingsubstitution,
               et.regular_start, et.regular_end, et.parttime_start, et.parttime_end,
               COALESCE(et.restrict_pt_hours, TRUE) AS restrict_pt_hours,
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
                'restrict_pt_hours': bool(row['restrict_pt_hours']),
            },
        }
    _faculty_map_cache = {'data': faculty_map, 'ts': now}
    return faculty_map


def _check_cross_program_faculty_loads(schedule_list, faculty_map, sem_id,
                                       exclude_program=None, exclude_year_level=None):
    """
    #11: Returns overload dicts for faculty who exceed their load limit when the
    submitted schedule is combined with existing Published/Draft schedules for
    OTHER programs/year-levels in the same semester.
    """
    if not schedule_list or not sem_id:
        return []

    from collections import defaultdict as _dd
    submitted_units: dict = _dd(int)
    for cls in schedule_list:
        fid   = cls.get('faculty_id') or cls.get('employeenumber')
        if str(fid or '').strip().upper() == 'TBA':
            continue  # TBA has no faculty to load-check
        units = int(cls.get('units', 0) or cls.get('credit_units', 0)
                    or cls.get('creditunits', 0) or 0)
        if fid and units > 0:
            submitted_units[fid] += units

    if not submitted_units:
        return []

    conn = None
    try:
        conn = get_db_connection()
        if conn is None:
            return []
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        excl_prog = (exclude_program or '').upper()
        excl_yl   = int(exclude_year_level or 0)
        # Sum units already committed in OTHER sections (exclude the one being saved)
        cur.execute("""
            SELECT sc.employeenumber AS faculty_id,
                   COALESCE(SUM(COALESCE(cs.creditunits, 0)), 0) AS committed_units
            FROM schedule_version sv
            JOIN schedule sc          ON sv.scheduleid         = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c         ON cs.curriculumid        = c.curriculumid
            WHERE sc.semesterid = %s
              AND sv.status IN ('Published', 'Draft')
              AND sc.employeenumber = ANY(%s)
              AND cs.creditunits > 0
              AND NOT (UPPER(c.programcode) = %s AND cs.yearlevel = %s)
            GROUP BY sc.employeenumber
        """, (sem_id, list(submitted_units.keys()), excl_prog, excl_yl))
        existing_loads = {r['faculty_id']: int(r['committed_units'] or 0)
                          for r in (cur.fetchall() or [])}
        cur.close()
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()

    violations = []
    for fid, new_units in submitted_units.items():
        fac = faculty_map.get(fid, {})
        if not fac:
            continue
        et        = fac.get('employeetype', {})
        max_total = ((et.get('regularload') or 0)
                     + (et.get('parttimeload') or 0)
                     + (et.get('teachingsubstitution') or 0))
        if max_total <= 0:
            max_total = 99  # no configured limit → no cap
        existing  = existing_loads.get(fid, 0)
        total     = existing + new_units
        if total > max_total:
            violations.append({
                'faculty_id':   fid,
                'faculty_name': fac.get('fullname') or fid,
                'current_load': existing,
                'new_units':    new_units,
                'total_load':   total,
                'max_load':     max_total,
                'overload_by':  total - max_total,
            })
    return violations


def _check_designee_night_limit(schedule_list, faculty_map, sem_id,
                                exclude_program=None, exclude_year_level=None):
    """
    PT/Night Teaching Service is a per-designee cap on the number of DISTINCT weekday
    nights they may be scheduled outside their regular daytime hours (see settings —
    "sets the maximum evening assignments allowed, not a fixed time restriction").
    Combines the submitted payload with whatever nights are already committed in
    OTHER programs/year-levels this semester, so approving a new subject correctly
    detects "this designee already has N nights elsewhere."
    """
    _WEEKDAYS = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'}

    def _regular_end_for(fac):
        et = fac.get('employeetype', {})
        rs = et.get('regular_start') or time(7, 30)
        re = et.get('regular_end')
        return re if (re and re != rs) else time(16, 30)

    def _regular_start_for(fac):
        return fac.get('employeetype', {}).get('regular_start') or time(7, 30)

    # Nights the submitted payload would add, per designee faculty
    submitted_nights: dict = {}
    for cls in schedule_list:
        fid = cls.get('faculty_id') or cls.get('employeenumber')
        if not fid or str(fid).strip().upper() == 'TBA':
            continue
        fac = faculty_map.get(fid)
        if not fac or fac.get('designationid') is None:
            continue
        day = cls.get('day') or cls.get('daydesc') or (cls.get('days_list') or [None])[0]
        if day not in _WEEKDAYS:
            continue
        start = cls.get('start_time')
        if isinstance(start, str):
            start = _parse_time_str(start)
        end = cls.get('end_time')
        if isinstance(end, str):
            end = _parse_time_str(end)
        if not start or not end:
            continue
        if start >= _regular_start_for(fac) and end <= _regular_end_for(fac):
            continue  # within regular hours — not a night assignment
        submitted_nights.setdefault(fid, set()).add(day)

    if not submitted_nights:
        return []

    conn = None
    try:
        conn = get_db_connection()
        if conn is None:
            return []
        cur = conn.cursor(cursor_factory=RealDictCursor)
        excl_prog = (exclude_program or '').upper()
        excl_yl   = int(exclude_year_level or 0)
        cur.execute("""
            SELECT sc.employeenumber AS faculty_id, ss.daydesc,
                   TO_CHAR(ts_s.timevalue, 'HH24:MI') AS start_time,
                   TO_CHAR(ts_e.timevalue, 'HH24:MI') AS end_time
            FROM schedule_version sv
            JOIN schedule sc          ON sv.scheduleid         = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c         ON cs.curriculumid        = c.curriculumid
            LEFT JOIN schedule_sessions ss ON ss.versionid       = sv.versionid
            LEFT JOIN timeslot ts_s        ON ss.starttimeid     = ts_s.timeid
            LEFT JOIN timeslot ts_e        ON ss.endtimeid       = ts_e.timeid
            WHERE sc.semesterid = %s
              AND sv.status IN ('Published', 'Draft')
              AND sc.employeenumber = ANY(%s)
              AND NOT (UPPER(c.programcode) = %s AND cs.yearlevel = %s)
        """, (sem_id, list(submitted_nights.keys()), excl_prog, excl_yl))
        rows = cur.fetchall() or []
        cur.close()
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()

    existing_nights: dict = {}
    for r in rows:
        fid = r['faculty_id']
        day = r['daydesc']
        if fid not in submitted_nights or day not in _WEEKDAYS or not r['start_time'] or not r['end_time']:
            continue
        st = _parse_time_str(r['start_time'])
        et_ = _parse_time_str(r['end_time'])
        if not st or not et_:
            continue
        fac = faculty_map[fid]
        if st >= _regular_start_for(fac) and et_ <= _regular_end_for(fac):
            continue
        existing_nights.setdefault(fid, set()).add(day)

    violations = []
    for fid, new_days in submitted_nights.items():
        fac       = faculty_map[fid]
        night_svc = int(fac.get('nightteachingservice') or 0)
        if night_svc <= 0:
            continue  # "no approved evening service" is enforced elsewhere (HC3)
        already  = existing_nights.get(fid, set())
        combined = already | new_days
        if len(combined) > night_svc:
            violations.append({
                'faculty_id':     fid,
                'faculty_name':   fac.get('fullname') or fid,
                'allowed_nights': night_svc,
                'existing_nights': sorted(already),
                'total_nights':    len(combined),
                'detail': (
                    f'{fac.get("fullname") or fid} is approved for {night_svc} evening '
                    f'night(s) per week, but is already scheduled on '
                    f'{", ".join(sorted(already)) or "none"} elsewhere this term — adding '
                    f'{", ".join(sorted(new_days))} would bring it to {len(combined)}.'
                )
            })
    return violations


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
    # Lazy-load room_type lookup map so HC_LAB can check room type from room_id.
    _room_type_map = {}
    # Exclude non-numeric room ids (e.g. the 'TBA' sentinel) — passing one to the IN (...)
    # query below would raise a type error and silently drop room_type backfill for every
    # id in the batch, not just the TBA one.
    _room_ids_missing = [
        cls.get('room_id') for cls in schedule_data
        if cls.get('room_id') and not cls.get('room_type') and not cls.get('roomtype')
        and str(cls.get('room_id')).isdigit()
    ]
    if _room_ids_missing:
        try:
            _rt_rows = query_db(
                f"SELECT roomid, roomtype FROM room WHERE roomid IN ({','.join(['%s']*len(_room_ids_missing))})",
                _room_ids_missing
            ) or []
            _room_type_map = {str(r['roomid']): (r['roomtype'] or '') for r in _rt_rows}
        except Exception:
            pass

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
        # Normalize the hours field: manual editor sends 'units'; CSP validator
        # expects 'total_hours' or 'total_subject_hrs' for HC6 day-pairing check.
        if 'total_hours' not in cls and 'total_subject_hrs' not in cls:
            u = cls.get('units')
            if u is not None:
                try:
                    cls['total_hours'] = float(u)
                except (ValueError, TypeError):
                    pass
        # Populate room_type from DB lookup if client did not send it.
        # HC_LAB (_check_lab_room) needs this to verify lab-hour subjects are in lab rooms.
        if not cls.get('room_type') and not cls.get('roomtype'):
            rid = str(cls.get('room_id') or '')
            if rid and rid in _room_type_map:
                cls['room_type'] = _room_type_map[rid]
                cls['roomtype']  = _room_type_map[rid]
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
    # 'term' kept for signature compatibility; 's.semesterid = %s' already identifies the semester.
    # The old 'cs.semester = %s' filter was incorrectly restricting archiving to subjects whose
    # curriculum semester designation matched the current term, leaving other subjects un-archived
    # and causing duplicate Published records in SIS queries.
    extra = "AND sv.source = %s" if source else ""
    params = [program, year_level, semester_id, status_to_archive]
    if source:
        params.append(source)
    cur.execute(f"""
        UPDATE public.schedule_version sv
        SET status = 'Archive'
        FROM public.schedule s, public.curriculumsubject cs, public.curriculum c
        WHERE sv.scheduleid = s.scheduleid AND s.curriculumsubjectid = cs.curriculumsubjectid
          AND cs.curriculumid = c.curriculumid
          AND c.programcode = %s
          AND cs.yearlevel = %s AND s.semesterid = %s AND sv.status = %s
          {extra}
    """, params)

def _archive_status_for_subjects(cur, program, year_level, term, semester_id, status_to_archive, subject_codes):
    """Archive only sessions for specific subject codes, leaving other subjects' versions intact."""
    if not subject_codes: return
    upper_codes = [s.upper() for s in subject_codes]
    placeholders = ','.join(['%s'] * len(upper_codes))
    # 'term' kept for signature compatibility; removed cs.semester = %s (same fix as _archive_status)
    cur.execute(f"""
        UPDATE public.schedule_version sv
        SET status = 'Archive'
        FROM public.schedule s, public.curriculumsubject cs, public.curriculum c
        WHERE sv.scheduleid = s.scheduleid AND s.curriculumsubjectid = cs.curriculumsubjectid
          AND cs.curriculumid = c.curriculumid
          AND c.programcode = %s
          AND cs.yearlevel = %s AND s.semesterid = %s AND sv.status = %s
          AND UPPER(cs.subjectcode) IN ({placeholders})
    """, [program, year_level, semester_id, status_to_archive] + upper_codes)

def _group_carry_forward_sessions(sessions):
    """Group per-day rows from _fetch_section_sessions into multi-day items.

    _fetch_section_sessions returns one row per schedule_sessions entry (one per day).
    A paired-day subject yields two rows (e.g. Tuesday + Friday). Grouping them ensures
    _insert_batch creates a single schedule+version record with multiple sessions rows
    instead of two separate schedule+version records.
    """
    groups = {}
    order  = []
    for row in sessions:
        key = (
            (row.get('subjectcode') or '').upper(),
            str(row.get('employeenumber') or ''),
            str(row.get('start_time', '')),
            str(row.get('end_time', '')),
        )
        if key not in groups:
            item = dict(row)
            item['days_list'] = [row['daydesc']] if row.get('daydesc') else []
            groups[key] = item
            order.append(key)
        else:
            if row.get('daydesc') and row['daydesc'] not in groups[key]['days_list']:
                groups[key]['days_list'].append(row['daydesc'])
    return [groups[k] for k in order]


def _insert_batch(cur, schedule_data, semester_id, target_status, version_number, program, year_level, source='manual_editor', section_id=None):
    _ensure_source_col(cur)
    _ensure_original_status_col(cur)
    if not section_id:
        # Filter by the AY derived from semester_id so sections from other academic years
        # are not accidentally selected when multiple AYs share the same program/year-level.
        cur.execute("""
            SELECT sec.sectionid FROM public.sections sec
            JOIN public.program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            WHERE UPPER(pyl.programcode) = UPPER(%s)
              AND pyl.yearlevel = %s
              AND pyl.academicyearid = (
                  SELECT academicyearid FROM public.semester WHERE semesterid = %s LIMIT 1
              )
            LIMIT 1
        """, (program, year_level, semester_id))
        sec_res = cur.fetchone()
        section_id = sec_res['sectionid'] if sec_res else None
    if not section_id:
        cur.execute("SELECT sectionid FROM public.sections LIMIT 1")
        section_id = cur.fetchone()['sectionid']

    # ── Pre-load lookup tables once instead of querying per subject ──────────
    # Timeslot map: "HH:MM:SS" → timeid
    cur.execute("SELECT timeid, CAST(timevalue AS TEXT) AS tv FROM public.timeslot")
    _ts_map = {}
    for r in (cur.fetchall() or []):
        tv = str(r['tv'])
        _ts_map[tv] = r['timeid']
        if len(tv) == 8:                 # "HH:MM:SS" → also index short form "HH:MM"
            _ts_map[tv[:5]] = r['timeid']

    def _ts_id(t):
        if not t: return None
        s = str(t).strip()
        return _ts_map.get(s) or _ts_map.get(s[:8]) or _ts_map.get(s[:5])

    # Curriculum subject map: UPPER(subjectcode) → curriculumsubjectid
    # Load all subjects for this program to avoid per-iteration SELECTs.
    cur.execute("""
        SELECT UPPER(cs.subjectcode) AS code, cs.curriculumsubjectid,
               cs.yearlevel, cs.semester
        FROM public.curriculumsubject cs
        JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
        WHERE c.programcode = %s
    """, (program,))
    _cs_rows = cur.fetchall() or []
    # Full key (code+yl+sem) takes priority; fallback key (code+yl) tolerates a missing/
    # mismatched semester but must NEVER cross year levels — a subject that only exists in
    # the curriculum at one year level must not get silently attached to a different year
    # level's section just because the subject code happens to match.
    _cs_full: dict = {}   # (code, yl, sem) → cs_id
    _cs_yl:   dict = {}   # (code, yl) → cs_id  (fallback: ignores semester mismatch only)
    for r in _cs_rows:
        _cs_full[(r['code'], r['yearlevel'], r['semester'])] = r['curriculumsubjectid']
        _cs_yl[(r['code'], r['yearlevel'])] = r['curriculumsubjectid']
    # ─────────────────────────────────────────────────────────────────────────

    for cls in schedule_data:
        s_code = cls.get('subjectcode') or cls.get('subject_code')
        f_num  = cls.get('employeenumber') or cls.get('faculty_id')
        r_id   = cls.get('roomid') or cls.get('room_id')
        start_t, end_t = cls.get('start_time'), cls.get('end_time')

        # 'TBA' sentinel means faculty is To Be Announced — stored as NULL in the DB
        if str(f_num or '').strip().upper() == 'TBA':
            f_num = None

        # Collect all days — GA items have days_list with both days; per-day items use daydesc/day
        days_to_insert = list(cls.get('days_list') or [])
        if not days_to_insert:
            single = cls.get('daydesc') or cls.get('day')
            if single:
                days_to_insert = [single]

        # f_num may be None for TBA schedules — only skip if core fields are missing
        if not all([s_code, start_t, end_t]) or not days_to_insert: continue

        # Resolve curriculumsubjectid from pre-loaded map (no DB round-trip)
        code_up  = s_code.upper()
        cls_sem  = cls.get('sem') or cls.get('semester', '')
        cs_id    = (_cs_full.get((code_up, year_level, cls_sem))
                    or _cs_yl.get((code_up, year_level)))
        if not cs_id: continue

        # Resolve timeslot IDs from pre-loaded map (no DB round-trip)
        s_id = _ts_id(start_t)
        e_id = _ts_id(end_t)
        if not (s_id and e_id): continue

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

        # Insert ONE schedule_sessions row per day (paired subjects get two rows under the same version)
        for day in days_to_insert:
            cur.execute("INSERT INTO public.schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid) VALUES (%s, %s, %s, %s, %s)",
                        (ver_id, day, s_id, e_id, r_id if str(r_id).isdigit() else None))

def _build_published_baseline(cur, program, year_level, sem_id, subject_codes, priority_sessions):
    """Return [] — all submitted subjects are fully replaced by sched_data.

    The old slot-level priority_keys approach carried forward any old Published session
    whose (subject, day, start_time) didn't match the new submission.  When the generator
    changed a subject's time or room, the stale old session appeared in existing_for_conflict
    at approval time and produced false 'room conflict' errors.

    The correct model: approving a schedule is a complete replacement for every submitted
    subject.  No old Published slot for a submitted subject should survive.
    Non-submitted subjects are already handled by other_sessions in api_approve_schedule.
    """
    return []


def _fetch_section_sessions(cur, program, year_level, sem_id, exclude_codes=None,
                            published_only=False, draft_only=False, section_id=None):
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

    section_id: when given, scopes rows to that section only. A program+year-level can have
        multiple sections (BSIT 1, BSIT 2, ...), each with its own independent schedule rows —
        omitting this let another section's Draft/Published sessions bleed into this section's
        carry-forward snapshot.
    """
    upper_excl = [c.upper() for c in (exclude_codes or [])]
    ph = ','.join(['%s'] * len(upper_excl)) if upper_excl else None
    excl_sql = f"AND UPPER(cs.subjectcode) NOT IN ({ph})" if ph else ""
    sect_sql = "AND sg.sectionid = %s" if section_id else ""
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
        JOIN   schedule_sessions ss   ON sv.versionid           = ss.versionid
        JOIN   timeslot t_s           ON ss.starttimeid         = t_s.timeid
        JOIN   timeslot t_e           ON ss.endtimeid           = t_e.timeid
        WHERE  c.programcode = %s
          AND  cs.yearlevel    = %s
          AND  sg.semesterid        = %s
          AND  sv.status IN ({status_filter})
          AND  sv.source != 'local'
          {excl_sql}
          {sect_sql}
    """, [program, year_level, sem_id] + upper_excl + ([int(section_id)] if section_id else []))

    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return []

    subj_groups = {}
    for row in rows:
        subj_groups.setdefault(row['subjectcode'].upper(), []).append(row)

    result = []
    for rows_for_code in subj_groups.values():
        if published_only:
            # A subject only ever gets carried into a Published snapshot if it has
            # actually been approved at least once (never leak a brand-new, never-
            # approved Draft into someone else's approval). But if it HAS been
            # published before and now also has a newer, unapproved Draft on top of
            # it, use the Draft's content — otherwise approving an unrelated subject
            # would silently overwrite this subject's in-progress edit back to its
            # stale last-Published state (see the "carry forward pending Drafts" fix).
            pub_rows = [r for r in rows_for_code if r['status'] == 'Published']
            if not pub_rows:
                continue
            draft_rows = [r for r in rows_for_code if r['status'] == 'Draft']
            chosen = draft_rows or pub_rows
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
    import json as _json

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

    def _room_floor(name):
        n = (name or '').replace(' ', '')
        if 'LQ1' in n: return '1'
        if 'LQ2' in n: return '2'
        if 'LQ3' in n: return '3'
        return 'other'

    return render_template('academic/roomScheduleView.html',
        buildings_json=_json.dumps([{'id': b['buildingid'], 'name': b['buildingname']} for b in buildings]),
        rooms_json=_json.dumps([{
            'id': r['roomid'], 'name': r['roomname'],
            'type': r['roomtype'] or 'Lecture',
            'bid': r['buildingid'], 'bname': r['buildingname'],
            'capacity': r['roomcapacity'] or 0,
            'floor': _room_floor(r['roomname'])
        } for r in rooms])
    )

@app.route('/academic/schedule-generation')
def schedule_generation_view():
    if 'loggedin' not in session: return redirect(url_for('login'))
    from datetime import date as _date
    _today = _date.today()
    programs_data = query_db("SELECT programcode, programname FROM programs WHERE isactive = true ORDER BY programname;")
    programs = [{'code': p['programcode'], 'name': p['programname']} for p in (programs_data or [])]
    # #2: Only show the current academic year and future academic years.
    acad_years = [a['academicyearid'] for a in (query_db(
        "SELECT academicyearid FROM academicyear WHERE yearend >= %s ORDER BY yearstart ASC;",
        [_today.year]
    ) or [])]
    curriculums = [c['curriculumyear'] for c in (query_db("SELECT DISTINCT curriculumyear FROM curriculum ORDER BY curriculumyear DESC;") or [])]
    terms = [{'id': 'A', 'name': '1ST SEMESTER'}, {'id': 'B', 'name': '2ND SEMESTER'}, {'id': 'C', 'name': 'SUMMER'}]
    active_info = query_db("""
        SELECT ay.academicyearid, s.semestertype
        FROM semester s
        JOIN academicyear ay ON s.academicyearid = ay.academicyearid
        WHERE s.isactive = TRUE LIMIT 1
    """, one=True)
    active_ay_id = active_info['academicyearid'] if active_info else ''
    active_sem   = active_info['semestertype']   if active_info else ''
    return render_template('academic/scheduleGeneration.html', programs=programs, acad_years=acad_years, curriculums=curriculums, terms=terms, active_ay_id=active_ay_id, active_sem=active_sem)

def _compute_schedule_accuracy(schedule_data, program, year_level, term):
    """
    Multi-criteria accuracy computation shared by the generate and accuracy endpoints.
    Returns a plain dict (not a Response). Raises on unrecoverable error.
    """
    from collections import defaultdict as _dd
    import re as _re_c9

    try:
        from scheduler import _spec_matches_subject
    except Exception:
        _spec_matches_subject = lambda spec, code: True  # noqa: E731

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)

    try:
        n = len(schedule_data)

        def parse_t(t):
            if t is None: return 0
            if isinstance(t, str):
                p = t.split(':')
                return int(p[0]) * 60 + int(p[1]) if len(p) >= 2 else 0
            if hasattr(t, 'hour'): return t.hour * 60 + t.minute
            return 0

        def _parse_t_12h(t_str):
            try:
                s = t_str.strip().upper()
                if 'AM' in s or 'PM' in s:
                    parts = s.split()
                    hm    = parts[0].split(':')
                    h, m  = int(hm[0]), int(hm[1])
                    mer   = parts[-1] if len(parts) > 1 else ''
                    if mer == 'PM' and h != 12: h += 12
                    elif mer == 'AM' and h == 12: h = 0
                    return h * 60 + m
                else:
                    hm = s.split(':')
                    return int(hm[0]) * 60 + int(hm[1])
            except Exception:
                return 0

        def get_session_times(s):
            st = parse_t(s.get('start_time'))
            et = parse_t(s.get('end_time'))
            if st or et: return st, et
            time_str = (s.get('time') or '').replace('–', '-').replace('—', '-')
            if '-' in time_str:
                halves = time_str.split('-', 1)
                if len(halves) == 2:
                    return _parse_t_12h(halves[0]), _parse_t_12h(halves[1])
            return 0, 0

        def session_days(s):
            dl = s.get('days_list')
            if isinstance(dl, list) and dl: return [d for d in dl if d]
            d = s.get('day', '')
            return [d] if d else []

        def sessions_overlap(a, b):
            sa, ea = parse_t(a.get('start_time')), parse_t(a.get('end_time'))
            sb, eb = parse_t(b.get('start_time')), parse_t(b.get('end_time'))
            if sa >= ea or sb >= eb: return False
            return bool(set(session_days(a)) & set(session_days(b))) and sa < eb and ea > sb

        NSTP_PFXS    = ('NSTP', 'OU')
        WEEKDAYS_SET = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'}
        RESTRICTED   = {'Sunday'}

        # C1 – Faculty conflict-free (20%)
        fac_conflict_set = set()
        for i in range(n):
            for j in range(i + 1, n):
                a, b = schedule_data[i], schedule_data[j]
                fa, fb = a.get('faculty_id'), b.get('faculty_id')
                if not (fa and fb and fa == fb): continue
                a_nstp = any((a.get('subject_code') or '').upper().startswith(p) for p in NSTP_PFXS)
                b_nstp = any((b.get('subject_code') or '').upper().startswith(p) for p in NSTP_PFXS)
                if a_nstp and b_nstp: continue
                if sessions_overlap(a, b): fac_conflict_set |= {i, j}
        c1 = max(0.0, 1.0 - len(fac_conflict_set) / n) if n else 1.0

        # C2 – Room conflict-free (20%)
        room_conflict_set = set()
        for i in range(n):
            for j in range(i + 1, n):
                a, b = schedule_data[i], schedule_data[j]
                ra, rb = a.get('room_id'), b.get('room_id')
                if ra and rb and ra == rb and sessions_overlap(a, b): room_conflict_set |= {i, j}
        c2 = max(0.0, 1.0 - len(room_conflict_set) / n) if n else 1.0

        # C3 – Section conflict-free (20%)
        sec_conflict_set = set()
        for i in range(n):
            for j in range(i + 1, n):
                if sessions_overlap(schedule_data[i], schedule_data[j]): sec_conflict_set |= {i, j}
        c3 = max(0.0, 1.0 - len(sec_conflict_set) / n) if n else 1.0

        # C4 – Faculty qualification match (10%)
        faculty_ids  = list({s.get('faculty_id') for s in schedule_data if s.get('faculty_id')})
        fac_spec_map = {}
        if faculty_ids:
            cur.execute("""
                SELECT f.employeenumber, sp.specializationname
                FROM   faculty f
                LEFT JOIN specialization sp ON f.specializationid = sp.specializationid
                WHERE  f.employeenumber = ANY(%s)
            """, (faculty_ids,))
            for row in cur.fetchall():
                fac_spec_map[row['employeenumber']] = (row['specializationname'] or '').strip()

        qual_pass = qual_total = 0
        for s in schedule_data:
            fid = s.get('faculty_id')
            if not fid: continue
            qual_total += 1
            spec = fac_spec_map.get(fid, '')
            if not spec:
                qual_pass += 1
                continue
            code = (s.get('subject_code') or s.get('subjectcode') or '').strip()
            if _spec_matches_subject(spec, code): qual_pass += 1
        c4 = qual_pass / qual_total if qual_total else 1.0

        # C5 – Laboratory compliance (5%)
        lab_subj = _dd(list)
        for s in schedule_data:
            if s.get('lab_hours') or s.get('laboratoryhours'):
                lab_subj[(s.get('subject_code') or s.get('subjectcode') or '?')].append(s)
        lab_ok = lab_ttl = 0
        for code, sessions in lab_subj.items():
            lab_ttl += 1
            if any((ss.get('room_type') or ss.get('roomtype') or '').strip().lower() == 'laboratory'
                   for ss in sessions):
                lab_ok += 1
        c5 = lab_ok / lab_ttl if lab_ttl else 1.0

        # C6 – Weekend restriction (5%)
        weekend_viol = 0
        for s in schedule_data:
            code = (s.get('subject_code') or '').upper()
            if any(code.startswith(p) for p in NSTP_PFXS): continue
            if RESTRICTED & set(session_days(s)): weekend_viol += 1
        c6 = max(0.0, 1.0 - weekend_viol / n) if n else 1.0

        # C7 – Day pairing alignment (5%)
        VALID_PAIRS = [
            sorted(['Monday', 'Thursday']),
            sorted(['Tuesday', 'Friday']),
            sorted(['Wednesday', 'Saturday']),
        ]
        def dur_h(s):
            return (parse_t(s.get('end_time')) - parse_t(s.get('start_time'))) / 60.0

        subj_days_acc  = _dd(list)
        subj_hrs_cache = {}
        for s in schedule_data:
            if abs(dur_h(s) - 1.5) > 0.1: continue
            code = s.get('subject_code') or s.get('subjectcode') or ''
            hrs  = float(s.get('total_subject_hrs') or s.get('total_hours') or 0)
            dl   = session_days(s)
            if len(dl) > 1: subj_days_acc[code] = dl
            else:
                for d in dl:
                    if d and d not in subj_days_acc[code]: subj_days_acc[code].append(d)
            if hrs > 0: subj_hrs_cache[code] = hrs

        pair_ok = pair_ttl = 0
        seen_pair = set()
        for s in schedule_data:
            if abs(dur_h(s) - 1.5) > 0.1: continue
            code = s.get('subject_code') or s.get('subjectcode') or ''
            hrs  = float(s.get('total_subject_hrs') or s.get('total_hours') or subj_hrs_cache.get(code, 0))
            if hrs < 3 or code in seen_pair: continue
            seen_pair.add(code)
            pair_ttl += 1
            grouped = subj_days_acc.get(code, session_days(s))
            if len(grouped) >= 2 and sorted(grouped[:2]) in VALID_PAIRS: pair_ok += 1
        c7 = pair_ok / pair_ttl if pair_ttl else 1.0

        # C8 – Faculty load compliance (5%)
        fac_limits = {}
        if faculty_ids:
            cur.execute("""
                SELECT f.employeenumber,
                       COALESCE(d.regularloadunit, et.regularload, 99)      AS max_regular,
                       COALESCE(d.nightteachingservice, et.parttimeload, 99) AS max_pt,
                       COALESCE(et.teachingsubstitution, 0)                  AS ts_hrs,
                       et.regular_end
                FROM   faculty f
                JOIN   employeetype et ON f.employeetypeid = et.employeetypeid
                LEFT JOIN designation d ON f.designationid = d.designationid
                WHERE  f.employeenumber = ANY(%s)
            """, (faculty_ids,))
            for row in cur.fetchall():
                fac_limits[row['employeenumber']] = row

        fac_reg_u = _dd(int)
        fac_pt_u  = _dd(int)
        seen_reg  = set()
        seen_pt   = set()
        for s in schedule_data:
            fid   = s.get('faculty_id')
            units = int(s.get('units', 0) or 0)
            if not fid or not units: continue
            code = (s.get('subject_code') or '').upper()
            lim  = fac_limits.get(fid, {})
            reg_raw     = lim.get('regular_end')
            reg_end_min = parse_t(reg_raw) if reg_raw else (16 * 60 + 30)
            end_min     = parse_t(s.get('end_time'))
            sday        = (session_days(s) or [''])[0]
            is_reg      = sday in WEEKDAYS_SET and end_min <= reg_end_min
            if is_reg:
                key = (fid, code)
                if key not in seen_reg:
                    seen_reg.add(key)
                    fac_reg_u[fid] += units
            else:
                key = (fid, code)
                if key not in seen_pt:
                    seen_pt.add(key)
                    fac_pt_u[fid] += units

        load_viol = 0
        all_fids  = set(fac_reg_u) | set(fac_pt_u)
        for fid in all_fids:
            lim     = fac_limits.get(fid, {})
            max_reg = int(lim.get('max_regular') or 99)
            max_pt  = int(lim.get('max_pt') or 99)
            ts_hrs  = int(lim.get('ts_hrs') or 0)
            if (fac_reg_u[fid] > max_reg and fac_reg_u[fid] - max_reg > ts_hrs) or \
               (fac_pt_u[fid]  > max_pt  and fac_pt_u[fid]  - max_pt  > ts_hrs):
                load_viol += 1
        fac_ttl = len(all_fids) if all_fids else 1
        c8 = max(0.0, 1.0 - load_viol / fac_ttl)

        # C9/C10 – Historical faculty & room match (5% + 3%)
        # Also fetch "Time" here so C11 can compare actual times against historical data.
        cur.execute("""
            SELECT hd."Subject Code" AS subjectcode,
                   hd."Instructor"   AS instructor,
                   hd."Room"         AS room,
                   hd."Day/s"        AS days,
                   hd."Time"         AS time_raw
            FROM   historical_data hd
            JOIN   semester sem ON hd.semesterid = sem.semesterid
            WHERE  UPPER(REGEXP_REPLACE(hd."Program", '\\s+\\d+$', '')) = %s
              AND  CAST(hd."Year Level" AS TEXT) = %s
              AND  UPPER(sem.semestertype) = %s
              AND  hd."Subject Code" IS NOT NULL
              AND  TRIM(hd."Subject Code") != ''
        """, (program, str(year_level), term))

        _DAY_NORM = {
            'MONDAY': 'MON', 'TUESDAY': 'TUE', 'WEDNESDAY': 'WED',
            'THURSDAY': 'THU', 'FRIDAY': 'FRI', 'SATURDAY': 'SAT', 'SUNDAY': 'SUN',
        }
        # Single-letter day codes used in historical_data (e.g. 'M', 'W', 'S').
        _SINGLE_DAY = {'M': 'MON', 'W': 'WED', 'F': 'FRI', 'S': 'SAT',
                       'T': 'TUE', 'H': 'THU', 'U': 'SUN'}

        def norm_days(day_list):
            result = set()
            for d in day_list:
                u = d.upper()
                result.add(_DAY_NORM.get(u, u[:3]))
            return result

        def _parse_hist_time(t_str):
            """Parse a historical_data Time string (may lack AM/PM) to minutes-from-midnight.
            Uses the school-hours heuristic: bare times 01:00–07:29 are treated as PM."""
            try:
                s = str(t_str).strip().upper()
                has_pm = 'PM' in s
                has_am = 'AM' in s
                s = s.replace('AM', '').replace('PM', '').strip()
                parts = s.split(':')
                h, m = int(parts[0]), (int(parts[1][:2]) if len(parts) > 1 else 0)
                if has_pm and h != 12: h += 12
                elif has_am and h == 12: h = 0
                elif not has_pm and not has_am and 60 <= h * 60 + m < 7 * 60 + 30:
                    h += 12   # school-hours PM assumption
                return h * 60 + m
            except Exception:
                return 0

        hist_map = _dd(lambda: {'instructors': set(), 'rooms': set(), 'days': set(), 'times': set()})
        for r in cur.fetchall():
            code = (r['subjectcode'] or '').strip().upper()
            if not code: continue
            if r['instructor']:
                full = r['instructor'].strip().upper()
                hist_map[code]['instructors'].add(full)
                _norm = _re_c9.sub(r'\s+[A-Z]\.?\s*$', '', full).strip()
                if _norm and _norm != full: hist_map[code]['instructors'].add(_norm)
            if r['room']: hist_map[code]['rooms'].add(r['room'].strip().upper())
            if r['days']: hist_map[code]['days'].add(r['days'].strip().upper())
            # Parse time range so C11 can do a real time comparison.
            _tr = str(r.get('time_raw') or '').strip().replace('–', '-').replace('—', '-')
            if _tr:
                # Split on ' - ' (with spaces) or '-' before a digit (handles "3:00-6:00").
                _m = _re_c9.split(r'\s+-\s+|\s*-(?=\s*\d)', _tr, maxsplit=1)
                if len(_m) == 2:
                    _hs = _parse_hist_time(_m[0])
                    _he = _parse_hist_time(_m[1])
                    if _hs or _he:
                        hist_map[code]['times'].add((_hs, _he))

        def _norm_room_code(x):
            return _re_c9.sub(r'[^A-Z0-9]', '', (x or '').upper())

        seen_sc = set()
        hist_total = matched_fac = matched_room = 0
        for s in schedule_data:
            code = (s.get('subject_code') or '').strip().upper()
            if not code or code in seen_sc: continue
            seen_sc.add(code)
            hentry = hist_map.get(code)
            if not hentry: continue
            hist_total += 1
            gen_instr = (s.get('instructor') or '').strip().upper()
            gen_room  = (s.get('room') or '').strip().upper()
            if gen_instr and gen_instr in hentry['instructors']: matched_fac += 1
            if gen_room:
                gen_room_norm = _norm_room_code(gen_room)
                if any(_norm_room_code(h) == gen_room_norm for h in hentry['rooms']): matched_room += 1

        c9  = matched_fac  / hist_total if hist_total else 1.0
        c10 = matched_room / hist_total if hist_total else 1.0

        # C11 – Historical time match (2%)
        cur.execute("""
            SELECT DISTINCT ON (cs.subjectcode)
                cs.subjectcode,
                ts_s.timevalue AS start_time,
                ts_e.timevalue AS end_time
            FROM   schedule_sessions ss
            JOIN   schedule_version sv  ON ss.versionid           = sv.versionid
            JOIN   schedule sc          ON sv.scheduleid           = sc.scheduleid
            JOIN   curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c         ON cs.curriculumid        = c.curriculumid
            JOIN   semester sem         ON sc.semesterid          = sem.semesterid
            LEFT JOIN timeslot ts_s     ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN timeslot ts_e     ON ss.endtimeid           = ts_e.timeid
            WHERE  UPPER(c.programcode)    = %s
              AND  cs.yearlevel            = %s
              AND  UPPER(sem.semestertype) = %s
              AND  sv.status               = 'Published'
            ORDER BY cs.subjectcode, sv.datecreated DESC
        """, (program, year_level, term))

        prev_time_map = {}
        for _row in cur.fetchall():
            _code = (_row['subjectcode'] or '').strip().upper()
            if _code and _code not in prev_time_map:
                _st = parse_t(_row['start_time'])
                _et = parse_t(_row['end_time'])
                if _st or _et: prev_time_map[_code] = (_st, _et)

        time_total = matched_time = 0
        seen_c11   = set()

        def _check_day_match(gen_dnorm, hentry):
            for hdays_str in hentry['days']:
                hist_dnorm = set()
                for token in hdays_str.replace('/', ' ').replace(',', ' ').replace('-', ' ').split():
                    token = token.upper()
                    if len(token) == 1:
                        mapped = _SINGLE_DAY.get(token)
                        if mapped: hist_dnorm.add(mapped)
                    elif len(token) >= 2:
                        hist_dnorm.add(token[:3])
                if gen_dnorm & hist_dnorm:
                    return True
            return False

        for s in schedule_data:
            code = (s.get('subject_code') or '').strip().upper()
            if not code or code in seen_c11: continue
            seen_c11.add(code)
            gen_s, gen_e = get_session_times(s)
            hentry = hist_map.get(code)

            # Priority 1: compare against historical_data "Time" column (same source
            # as "Retrieve previous schedule").  This is always the best signal.
            if hentry and hentry['times']:
                time_total += 1
                if (gen_s, gen_e) in hentry['times']:
                    matched_time += 1

            # Priority 2: compare against Published schedule_version times (DB).
            # Only used when historical_data has no time column for this subject.
            elif code in prev_time_map:
                time_total += 1
                prev_s, prev_e = prev_time_map[code]
                if gen_s == prev_s and gen_e == prev_e: matched_time += 1

            # Priority 3: day-as-proxy (last resort, e.g. historical_data has no Time).
            elif hentry and hentry['days']:
                time_total += 1
                gen_dnorm = norm_days(session_days(s))
                if gen_dnorm and _check_day_match(gen_dnorm, hentry):
                    matched_time += 1

        c11 = matched_time / time_total if time_total else 1.0

        criteria = [
            ('faculty_conflict', 'Faculty Conflict-Free',      0.20, c1),
            ('room_conflict',    'Room Conflict-Free',          0.20, c2),
            ('section_conflict', 'Section Conflict-Free',       0.20, c3),
            ('faculty_qual',     'Faculty Qualification Match', 0.10, c4),
            ('lab_compliance',   'Laboratory Compliance',       0.05, c5),
            ('weekend',          'Weekend Restriction',         0.05, c6),
            ('day_pairing',      'Day Pairing Alignment',       0.05, c7),
            ('load_compliance',  'Faculty Load Compliance',     0.05, c8),
            ('hist_faculty',     'Historical Faculty Match',    0.05, c9),
            ('hist_room',        'Historical Room Match',       0.03, c10),
            ('hist_time',        'Historical Time Match',       0.02, c11),
        ]

        accuracy = round(sum(w * sc for _, _, w, sc in criteria) * 100)
        breakdown = [
            {
                'key':          key,
                'label':        label,
                'weight':       round(w * 100),
                'score':        round(sc * 100),
                'contribution': round(w * sc * 100, 1),
            }
            for key, label, w, sc in criteria
        ]

        return {
            'success':         True,
            'accuracy':        accuracy,
            'breakdown':       breakdown,
            'hist_total':      hist_total,
            'matched_faculty': matched_fac,
            'matched_room':    matched_room,
            'matched_time':    matched_time,
            'total':           len(seen_sc),
        }

    finally:
        cur.close()
        conn.close()


def _check_cross_schedule_conflicts(schedule_data, program, year_level, term, acad_year_id):
    """
    Check a freshly-generated schedule (raw, with datetime.time objects) against
    existing Published sessions from OTHER sections in the same semester.
    Returns (violations_list, count) — mirrors the same logic used in api_approve_schedule.
    Room conflict  = same room,    same day, overlapping time.
    Faculty conflict = same faculty, same day, overlapping time (different subject).
    """
    if not schedule_data or not acad_year_id or not term:
        return [], 0
    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT semesterid FROM semester WHERE academicyearid = %s AND semestertype = %s LIMIT 1",
            (acad_year_id, term)
        )
        sem_row = cur.fetchone()
        if not sem_row:
            cur.close(); conn.close(); return [], 0
        sem_id = sem_row['semesterid']

        cur.execute("""
            SELECT ss.roomid,
                   sc.employeenumber          AS faculty_id,
                   ss.daydesc                 AS day,
                   ts_s.timevalue             AS start_time,
                   ts_e.timevalue             AS end_time,
                   UPPER(cs.subjectcode)      AS subjectcode,
                   UPPER(c.programcode)       AS programcode,
                   cs.yearlevel               AS yearlevel,
                   COALESCE(r.roomname, CAST(ss.roomid AS TEXT)) AS roomname
            FROM   schedule_sessions ss
            JOIN   schedule_version sv  ON ss.versionid           = sv.versionid
            JOIN   schedule sc          ON sv.scheduleid           = sc.scheduleid
            JOIN   curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c         ON cs.curriculumid        = c.curriculumid
            LEFT JOIN room r            ON ss.roomid              = r.roomid
            LEFT JOIN timeslot ts_s     ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN timeslot ts_e     ON ss.endtimeid           = ts_e.timeid
            WHERE  sc.semesterid           = %s
              AND  sv.status               IN ('Published', 'Draft')
              AND  NOT (UPPER(c.programcode) = UPPER(%s) AND cs.yearlevel = %s)
              AND  ss.roomid               IS NOT NULL
              AND  ss.daydesc              IS NOT NULL
              AND  ts_s.timevalue          IS NOT NULL
              AND  ts_e.timevalue          IS NOT NULL
        """, (sem_id, program, year_level))
        published = cur.fetchall()
        cur.close(); conn.close()
    except Exception:
        try: cur.close()
        except Exception: pass
        try: conn.close()
        except Exception: pass
        return [], 0

    if not published:
        return [], 0

    violations = []
    seen       = set()

    for new_cls in schedule_data:
        r_new   = new_cls.get('room_id') or new_cls.get('roomid')
        fac_new = new_cls.get('faculty_id') or new_cls.get('employeenumber')
        days_new = new_cls.get('days_list') or (
            [new_cls['day']] if new_cls.get('day') else []
        )
        st_new  = new_cls.get('start_time')   # datetime.time from scheduler
        et_new  = new_cls.get('end_time')
        sc_new  = (new_cls.get('subject_code') or new_cls.get('subjectcode') or '?').upper()
        rm_name = new_cls.get('room') or str(r_new or '')

        if not st_new or not et_new:
            continue

        for day_new in days_new:
            if not day_new:
                continue
            for ex in published:
                st_ex = ex['start_time']   # datetime.time from DB
                et_ex = ex['end_time']
                if not (st_new < et_ex and et_new > st_ex):
                    continue
                if ex['day'] != day_new:
                    continue

                # Room conflict
                if r_new is not None and str(r_new) == str(ex['roomid']):
                    key = ('room', str(r_new), day_new, sc_new, ex['subjectcode'])
                    if key not in seen:
                        seen.add(key)
                        detail = (
                            f"Room {rm_name} is already occupied on {day_new} "
                            f"by {ex['subjectcode']} "
                            f"({ex['programcode']} Yr{ex['yearlevel']})."
                        )
                        print(f'[CONFLICT] ROOM: {sc_new} vs {ex["subjectcode"]} '
                              f'({ex["programcode"]} Yr{ex["yearlevel"]}) — '
                              f'{rm_name} on {day_new} {ex["start_time"]}–{ex["end_time"]}')
                        violations.append({
                            'rule':    'HC9',
                            'type':    'room',
                            'subject': f"{sc_new} / {ex['subjectcode']}",
                            'detail':  detail,
                        })

                # Faculty conflict (different subject, same instructor, same time)
                if fac_new and str(fac_new) == str(ex.get('faculty_id') or ''):
                    if sc_new != ex['subjectcode']:
                        key = ('faculty', str(fac_new), day_new, sc_new, ex['subjectcode'])
                        if key not in seen:
                            seen.add(key)
                            detail = (
                                f"Faculty {fac_new} double-booked on {day_new}: "
                                f"teaches both {sc_new} and {ex['subjectcode']} "
                                f"({ex['programcode']} Yr{ex['yearlevel']}) "
                                f"at the same time."
                            )
                            print(f'[CONFLICT] FACULTY {fac_new}: {sc_new} vs '
                                  f'{ex["subjectcode"]} ({ex["programcode"]} Yr{ex["yearlevel"]}) — '
                                  f'{day_new} {ex["start_time"]}–{ex["end_time"]}')
                            violations.append({
                                'rule':    'HC10',
                                'type':    'faculty',
                                'subject': f"{sc_new} / {ex['subjectcode']}",
                                'detail':  detail,
                            })

    if violations:
        print(f'[CONFLICT] Total cross-section conflicts after repair: {len(violations)}')
    return violations, len(violations)


@app.route('/api/schedule/generate', methods=['POST'])
def api_generate_schedule():
    data = request.json or {}
    res = scheduler_engine.generate_draft(
        data.get('program'), int(data.get('yearLevel', 1)),
        data.get('term'), data.get('curriculum'), False,  # never use historical path here
        acad_year_id=data.get('acadYear', '')             # #9: pass AY for load checks
    )
    if not res['success']: return jsonify({'success': False, 'error': res['error']}), 400

    # Cross-schedule conflict check against existing Published sessions from other sections.
    # Must run on the RAW schedule_data (before _serialize_class drops datetime.time fields).
    cross_violations    = []
    cross_conflict_count = 0
    try:
        cross_violations, cross_conflict_count = _check_cross_schedule_conflicts(
            res['schedule_data'],
            (data.get('program') or '').strip().upper(),
            int(data.get('yearLevel', 1)),
            (data.get('term') or '').strip().upper(),
            data.get('acadYear', ''),
        )
    except Exception:
        pass  # cross-check is advisory at generate time; never block generation itself

    serialized = [_serialize_class(cls) for cls in res['schedule_data']]
    acc_data = None
    try:
        acc_data = _compute_schedule_accuracy(
            serialized,
            (data.get('program') or '').strip().upper(),
            int(data.get('yearLevel', 1)),
            (data.get('term') or '').strip().upper(),
        )
    except Exception:
        pass  # accuracy is supplemental — never fail a generation because of it

    return jsonify({
        'success':              True,
        'batch_id':             'DRAFT-NEW-001',
        'schedule_data':        serialized,
        'conflict_count':       res['conflict_count'],
        'violations':           res.get('violations', []),
        'cross_conflict_count': cross_conflict_count,
        'cross_violations':     cross_violations,
        'accuracy_data':        acc_data,
    })


@app.route('/api/schedule/check-existing', methods=['POST'])
def api_check_existing_schedule():
    """Return whether a Draft or Published schedule already exists for the given section context."""
    try:
        data       = request.json or {}
        program    = (data.get('program') or '').strip().upper()
        year_level = int(data.get('yearLevel', 1))
        term       = (data.get('term') or '').strip().upper()
        acad_year  = data.get('acadYear', '')
        section_id = data.get('section', '')

        if not program or not term or not acad_year or not section_id:
            return jsonify({'exists': False}), 200

        try:
            section_id = int(section_id)
        except (ValueError, TypeError):
            return jsonify({'exists': False}), 200

        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        try:
            sem_id = _get_semester_id(cur, acad_year, term)
        except Exception:
            cur.close(); conn.close()
            return jsonify({'exists': False}), 200

        cur.execute("""
            SELECT sv.status, COUNT(DISTINCT s.scheduleid) AS schedule_count
            FROM   schedule_version sv
            JOIN   schedule s              ON sv.scheduleid           = s.scheduleid
            JOIN   curriculumsubject cs    ON s.curriculumsubjectid   = cs.curriculumsubjectid
            JOIN   curriculum c            ON cs.curriculumid         = c.curriculumid
            WHERE  UPPER(c.programcode) = UPPER(%s)
              AND  cs.yearlevel            = %s
              AND  s.semesterid            = %s
              AND  s.sectionid             = %s
              AND  sv.status               IN ('Draft', 'Published')
              AND  sv.source               IS DISTINCT FROM 'local'
            GROUP BY sv.status
            ORDER BY CASE sv.status WHEN 'Draft' THEN 1 WHEN 'Published' THEN 2 ELSE 3 END
            LIMIT 1
        """, (program, year_level, sem_id, section_id))
        row = cur.fetchone()
        cur.close(); conn.close()

        if row and int(row['schedule_count']) > 0:
            return jsonify({
                'exists':        True,
                'status':        row['status'],
                'subject_count': int(row['schedule_count']),
            }), 200
        return jsonify({'exists': False}), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'exists': False, 'error': str(e)}), 200


@app.route('/api/schedule/archive-draft-for-editor', methods=['POST'])
def api_archive_draft_for_editor():
    """Archive the active Draft for the given section (Override path when going to Manual Editor)."""
    conn = None
    try:
        data       = request.json or {}
        program    = (data.get('program') or '').strip().upper()
        year_level = int(data.get('yearLevel', 1))
        term       = (data.get('term') or '').strip().upper()
        acad_year  = data.get('acadYear', '')
        section_id = data.get('section', '')

        if not program or not term or not acad_year:
            return jsonify({'success': True, 'archived': 0}), 200

        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)
        try:
            sem_id = _get_semester_id(cur, acad_year, term)
        except Exception:
            cur.close(); conn.close()
            return jsonify({'success': True, 'archived': 0}), 200

        params = [program, year_level, sem_id]
        section_clause = ''
        if section_id:
            try:
                params.append(int(section_id))
                section_clause = 'AND s.sectionid = %s'
            except (ValueError, TypeError):
                pass

        # Archive both Draft AND Published — Override replaces the entire schedule,
        # so old Published rows must be retired or they appear alongside the new Draft.
        cur.execute(f"""
            UPDATE schedule_version sv
            SET    status = 'Archive'
            FROM   schedule s, curriculumsubject cs, curriculum c
            WHERE  sv.scheduleid           = s.scheduleid
              AND  s.curriculumsubjectid   = cs.curriculumsubjectid
              AND  cs.curriculumid         = c.curriculumid
              AND  UPPER(c.programcode)    = UPPER(%s)
              AND  cs.yearlevel            = %s
              AND  s.semesterid            = %s
              {section_clause}
              AND  sv.status               IN ('Draft', 'Published')
              AND  sv.source               IS DISTINCT FROM 'local'
        """, params)
        archived = cur.rowcount
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'success': True, 'archived': archived}), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        try: conn.rollback()
        except Exception: pass
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/accuracy', methods=['POST'])
def api_schedule_accuracy():
    """Multi-criteria weighted accuracy rate for a generated schedule."""
    try:
        body          = request.json or {}
        schedule_data = body.get('schedule_data', [])
        program       = (body.get('program') or '').strip().upper()
        year_level    = int(body.get('year_level') or 0)
        term          = (body.get('term') or '').strip().upper()

        if not schedule_data or not program or not year_level or not term:
            return jsonify({'success': False, 'error': 'Missing required fields.'}), 400

        result = _compute_schedule_accuracy(schedule_data, program, year_level, term)
        return jsonify(result)

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/retrieve-previous', methods=['POST'])
def api_retrieve_previous_schedule():
    """
    Retrieve the previous OFFICIAL (Published) schedule for the same program/year/semester.
    Priority: Published records from schedule/schedule_version/schedule_sessions first,
              then historical_data as fallback.
    Never retrieves Draft versions.
    """
    try:
        data       = request.json or {}
        program    = data.get('program', '')
        year_level = int(data.get('yearLevel', 1))
        term       = data.get('term', '')       # 'A', 'B', or 'C'
        acad_year  = data.get('acadYear', '')   # e.g. "AY2627"

        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        term_label = '1st Semester' if term == 'A' else '2nd Semester' if term == 'B' else 'Summer'

        # ── Step 1: resolve the immediate previous academic year ─────────────
        prev_ay_id    = None
        prev_ay_label = None
        if acad_year:
            cur.execute("""
                SELECT ay_prev.academicyearid AS prev_id,
                       ay_prev.yearstart,
                       ay_prev.yearend
                FROM   academicyear ay_curr
                JOIN   academicyear ay_prev
                       ON ay_prev.yearend = ay_curr.yearstart
                WHERE  ay_curr.academicyearid = %s
                LIMIT 1
            """, (acad_year,))
            prev_row = cur.fetchone()
            if prev_row:
                prev_ay_id    = prev_row['prev_id']
                prev_ay_label = f"AY{prev_row['yearstart']}{str(prev_row['yearend'])[-2:]}"

        if not prev_ay_id:
            cur.close(); conn.close()
            return jsonify({
                'success': False,
                'error': (
                    f'No previous academic year found before {acad_year}. '
                    f'Cannot retrieve a prior {term_label} schedule for {program} Year {year_level}.'
                ),
            }), 404

        # ── Step 2: resolve semester ID for the previous AY ─────────────────
        cur.execute("""
            SELECT semesterid FROM semester
            WHERE  academicyearid = %s AND semestertype = %s
            LIMIT  1
        """, (prev_ay_id, term))
        sem_row = cur.fetchone()
        if not sem_row:
            cur.close(); conn.close()
            return jsonify({
                'success': False,
                'error':   f'No {term_label} semester record found for {prev_ay_id}.',
            }), 404
        prev_sem_id = sem_row['semesterid']

        # ── Step 3: find the highest Published version_number ────────────────
        # Each subject in a snapshot gets its own schedule_version row but they all
        # share the same version_number.  We want the most recent Published snapshot,
        # so we find MAX(version_number) where status='Published' then load every
        # subject at that version — giving us the full schedule, not just one row.
        cur.execute("""
            SELECT COALESCE(MAX(sv.version_number), 0) AS max_pub_v
            FROM   schedule_version sv
            JOIN   schedule sc          ON sv.scheduleid          = sc.scheduleid
            JOIN   curriculumsubject cs  ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
            WHERE  UPPER(c.programcode) = UPPER(%s)
              AND  cs.yearlevel         = %s
              AND  sc.semesterid        = %s
              AND  sv.status            = 'Published'
        """, (program, year_level, prev_sem_id))
        pub_v_row = cur.fetchone()
        max_pub_v = pub_v_row['max_pub_v'] if pub_v_row else 0

        rows = []
        if max_pub_v:
            # ── Step 4: load ALL sessions for ALL subjects in that Published snapshot
            cur.execute("""
                SELECT cs.subjectcode                                 AS subject_code,
                       cs.subjectname                                 AS description,
                       cs.lecturehours                                AS lec_hours,
                       cs.laboratoryhours                             AS lab_hours,
                       cs.creditunits                                 AS credit_units,
                       c.programcode                                  AS course,
                       sc.employeenumber                              AS faculty_id,
                       CONCAT(f.lastname, ', ', f.firstname)          AS instructor,
                       ss.daydesc                                     AS day,
                       TO_CHAR(ts_s.timevalue, 'HH12:MI AM')         AS start_str,
                       TO_CHAR(ts_e.timevalue, 'HH12:MI AM')         AS end_str,
                       r.roomname                                     AS room,
                       r.roomid                                       AS room_id
                FROM   schedule_sessions ss
                JOIN   schedule_version sv  ON ss.versionid           = sv.versionid
                JOIN   schedule sc          ON sv.scheduleid           = sc.scheduleid
                JOIN   curriculumsubject cs  ON sc.curriculumsubjectid = cs.curriculumsubjectid
                JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
                LEFT JOIN faculty f          ON sc.employeenumber      = f.employeenumber
                LEFT JOIN room r             ON ss.roomid              = r.roomid
                LEFT JOIN timeslot ts_s      ON ss.starttimeid         = ts_s.timeid
                LEFT JOIN timeslot ts_e      ON ss.endtimeid           = ts_e.timeid
                WHERE  UPPER(c.programcode) = UPPER(%s)
                  AND  cs.yearlevel         = %s
                  AND  sc.semesterid        = %s
                  AND  sv.status            = 'Published'
                  AND  sv.version_number    = %s
                ORDER BY cs.subjectcode, ts_s.timevalue
            """, (program, year_level, prev_sem_id, max_pub_v))
            rows = cur.fetchall()

        # ── Step 5: historical_data fallback ────────────────────────────────
        if not rows:
            cur.execute("""
                SELECT
                    hd."Subject Code"      AS subject_code,
                    hd."Subject Name"      AS description,
                    hd."Instructor"        AS instructor,
                    hd."Room"              AS room,
                    hd."Day/s"             AS raw_days,
                    hd."Time"              AS raw_time,
                    hd."Lecture Hours"     AS lec_hours,
                    hd."Laboratory Hours"  AS lab_hours,
                    hd."Credit Units"      AS credit_units,
                    hd."Hours"             AS hours
                FROM historical_data hd
                WHERE REGEXP_REPLACE(hd."Program", '\\s+\\d+$', '') ILIKE %s
                  AND CAST(hd."Year Level" AS TEXT) = %s
                  AND hd.academicyearid = %s
                  AND (hd.semesterid = %s OR hd.semesterid IS NULL)
                ORDER BY hd."Subject Code"
            """, (program, str(year_level), prev_ay_id, prev_sem_id))
            hist_rows = cur.fetchall()
            cur.close(); conn.close()

            if not hist_rows:
                return jsonify({
                    'success': False,
                    'error': (
                        f'No schedule found for {program} — Year {year_level} — {term_label} '
                        f'in {prev_ay_id}. Generate a new schedule instead.'
                    ),
                }), 404

            schedule_data = []
            for row in hist_rows:
                lh  = int(row['lec_hours']  or 0)
                lbh = int(row['lab_hours']  or 0)
                cu  = int(row['credit_units'] or 0)
                schedule_data.append({
                    'subject_code':  row['subject_code'] or '',
                    'description':   row['description']  or '',
                    'lec_hours':     lh,
                    'lab_hours':     lbh,
                    'credit_units':  cu,
                    'units':         cu,
                    'course':        program,
                    'faculty_id':    None,
                    'instructor':    row['instructor'] or 'TBA',
                    'room':          row['room']       or 'TBA',
                    'room_id':       None,
                    'time':          row['raw_time']   or '',
                    'hours':         str(row['hours']  or (lh + lbh)),
                    'days':          row['raw_days']   or '',
                    'day':           '',
                    'is_historical': True,
                })

            return jsonify({
                'success':        True,
                'schedule_data':  schedule_data,
                'conflict_count': 0,
                'violations':     [],
                'retrieved_from': {
                    'source':   'historical',
                    'version':  None,
                    'status':   'Historical',
                    'acadYear': prev_ay_id,
                    'ay_label': prev_ay_label or prev_ay_id,
                    'term':     term_label,
                },
            })

        # ── #10: Check if retrieved faculty are already overloaded in the CURRENT term ──
        # Resolve current AY's semester (the one being scheduled, not the previous one)
        cur_sem_id = None
        if acad_year and term:
            cur.execute("""
                SELECT semesterid FROM semester
                WHERE  academicyearid = %s AND semestertype = %s
                LIMIT  1
            """, (acad_year, term))
            csem = cur.fetchone()
            if csem:
                cur_sem_id = csem['semesterid']

        # Tally units each retrieved faculty would bring
        from collections import defaultdict as _dd2
        fac_units_retrieved: dict = _dd2(int)
        for row in rows:
            fid = row.get('faculty_id')
            cu  = int(row.get('credit_units') or 0)
            if fid and cu > 0:
                fac_units_retrieved[fid] += cu

        # Query what those faculty are already committed to in the current term
        current_committed: dict = {}
        if cur_sem_id and fac_units_retrieved:
            cur.execute("""
                SELECT sc.employeenumber AS fid,
                       COALESCE(SUM(COALESCE(cs.creditunits, 0)), 0) AS committed
                FROM   schedule_version sv
                JOIN   schedule sc          ON sv.scheduleid          = sc.scheduleid
                JOIN   curriculumsubject cs  ON sc.curriculumsubjectid = cs.curriculumsubjectid
                WHERE  sc.semesterid = %s
                  AND  sv.status IN ('Published', 'Draft')
                  AND  sc.employeenumber = ANY(%s)
                  AND  cs.creditunits > 0
                GROUP  BY sc.employeenumber
            """, (cur_sem_id, list(fac_units_retrieved.keys())))
            current_committed = {r['fid']: int(r['committed'] or 0)
                                 for r in (cur.fetchall() or [])}

        cur.close(); conn.close()

        # Build faculty map to look up load limits
        _fmap_retrieve = _load_faculty_map()

        # Determine which faculty are AT or OVER their limit already
        _overloaded_fids: set = set()
        for fid in fac_units_retrieved:
            fac  = _fmap_retrieve.get(fid, {})
            et   = fac.get('employeetype', {})
            maxt = ((et.get('regularload') or 0)
                    + (et.get('parttimeload') or 0)
                    + (et.get('teachingsubstitution') or 0))
            if maxt <= 0:
                continue
            if current_committed.get(fid, 0) >= maxt:
                _overloaded_fids.add(fid)

        # ── Step 6: group multi-day sessions (e.g. MON+WED same time) ───────
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
                fid = row['faculty_id']
                # #10: Replace overloaded historical faculty with TBA
                overloaded = fid in _overloaded_fids
                groups[key] = {
                    'subject_code':  row['subject_code'],
                    'description':   row['description'],
                    'lec_hours':     lh,
                    'lab_hours':     lbh,
                    'credit_units':  cu,
                    'units':         cu,
                    'course':        row['course'],
                    'faculty_id':    None if overloaded else fid,
                    'instructor':    'TBA (overloaded)' if overloaded else (row['instructor'] or 'TBA'),
                    'room':          row['room']       or 'TBA',
                    'room_id':       row['room_id'],
                    'time':          f"{row['start_str']} – {row['end_str']}",
                    'hours':         str(lh + lbh),
                    'days_list':     [],
                    'is_historical': True,
                    'overloaded_faculty': overloaded,
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

        # Human-readable notices for overloaded faculty
        overload_notices = []
        for fid in _overloaded_fids:
            fac  = _fmap_retrieve.get(fid, {})
            name = fac.get('fullname') or fid
            et   = fac.get('employeetype', {})
            maxt = ((et.get('regularload') or 0)
                    + (et.get('parttimeload') or 0)
                    + (et.get('teachingsubstitution') or 0))
            already = current_committed.get(fid, 0)
            overload_notices.append(
                f"{name} already has {already}/{maxt} units this term — replaced with TBA."
            )

        return jsonify({
            'success':          True,
            'schedule_data':    schedule_data,
            'conflict_count':   0,
            'violations':       [],
            'overload_notices': overload_notices,
            'retrieved_from': {
                'source':   'official',
                'version':  max_pub_v,
                'status':   'Published',
                'acadYear': prev_ay_id,
                'ay_label': prev_ay_label or prev_ay_id,
                'term':     term_label,
            },
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/save-draft', methods=['POST'])
def api_save_draft():
    conn = None; cur = None
    try:
        data, ctx = request.json or {}, (request.json or {}).get('context', {})
        program, year_level, term, ay = ctx.get('program'), int(ctx.get('yearLevel')), ctx.get('term'), ctx.get('acadYear')
        ctx_section_id = ctx.get('section_id') or ctx.get('section') or None
        if ctx_section_id:
            try: ctx_section_id = int(ctx_section_id)
            except (ValueError, TypeError): ctx_section_id = None
        # scheduler_mode drives source tagging so Local/Official drafts stay in separate namespaces
        scheduler_mode = (data.get('scheduler_mode') or 'official').strip()
        draft_source   = 'local' if scheduler_mode == 'local' else 'manual_editor'

        # Run one-time DDL migrations in a separate, immediately-committed transaction
        # BEFORE opening the main transaction. ALTER TABLE acquires ACCESS EXCLUSIVE
        # on schedule_version; if held inside the main transaction, it blocks
        # _check_cross_program_faculty_loads which opens its own connection to read
        # the same table — causing a deadlock that hangs the request indefinitely.
        if not _source_col_ensured or not _original_status_col_ensured:
            _mig_conn = get_db_connection()
            _mig_cur  = _mig_conn.cursor()
            _ensure_source_col(_mig_cur)
            _ensure_original_status_col(_mig_cur)
            _mig_conn.commit()
            _mig_conn.close()

        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        sem_id = _get_semester_id(cur, ay, term)

        # Each save creates a new revision number so that version history
        # accumulates R1, R2, R3... Count only records from the same source namespace.
        cur.execute("""
            SELECT COALESCE(MAX(sv.version_number), 0) AS max_v
            FROM public.schedule_version sv
            JOIN public.schedule s ON sv.scheduleid = s.scheduleid
            JOIN public.curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
            JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
            WHERE UPPER(c.programcode) = UPPER(%s)
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
        # HC6 (day-pairing) is enforced here too: it only fires when ≥ 2 days are
        # present for the same subject, so saving a single slice still passes.
        _pre_rehydrated = _rehydrate_schedule(list(schedule_list))
        _fmap_pre = _load_faculty_map()
        if _pre_rehydrated:
            from scheduler import CSPValidator as _CSP
            from database import load_scheduler_config as _lsc_pre
            _hc_cfg_pre = _lsc_pre()
            _viols_pre = _CSP(config=_hc_cfg_pre).validate(_pre_rehydrated, _fmap_pre)
            # HC_SPEC is advisory-only (warning severity) — skip it so specialization
            # mismatches don't block saves; the Academic Head can still approve.
            _hard_viols = [v for v in _viols_pre if v.get('severity') != 'warning']
            if _hard_viols:
                cur.close(); conn.close()
                return jsonify({
                    'success':    False,
                    'error':      f'{len(_hard_viols)} constraint violation(s) must be resolved before saving as draft.',
                    'violations': _hard_viols
                }), 400

        # ── #11: Cross-program faculty load check ─────────────────────────────
        # Block saving if any faculty in THIS schedule would exceed their total
        # teaching load limit when combined with other sections already saved.
        _load_viols = _check_cross_program_faculty_loads(
            schedule_list, _fmap_pre, sem_id,
            exclude_program=program, exclude_year_level=year_level
        )
        if _load_viols:
            cur.close(); conn.close()
            _load_msg = '; '.join(
                f"{v['faculty_name']}: {v['total_load']}/{v['max_load']} units "
                f"(+{v['overload_by']} over limit)"
                for v in _load_viols
            )
            return jsonify({
                'success':          False,
                'error':            f'Faculty load limit exceeded — cannot save draft: {_load_msg}',
                'load_violations':  _load_viols,
            }), 400

        # ── Designee night-cap check (PT/Night Teaching Service) ──────────────
        _night_viols = _check_designee_night_limit(
            schedule_list, _fmap_pre, sem_id,
            exclude_program=program, exclude_year_level=year_level
        )
        if _night_viols:
            cur.close(); conn.close()
            return jsonify({
                'success':         False,
                'error':           'Designee night-teaching cap exceeded — cannot save draft: '
                                   + '; '.join(v['detail'] for v in _night_viols),
                'night_violations': _night_viols,
            }), 400

        # ── Server-side section conflict check ──
        rehydrated = _rehydrate_schedule(list(schedule_list))
        # Pre-load all timeslots once to avoid per-slot DB queries
        cur.execute("SELECT timeid, CAST(timevalue AS TEXT) AS tv FROM public.timeslot")
        _ts_map = {r['tv']: r['timeid'] for r in (cur.fetchall() or [])}
        def _ts_id(t):
            key = str(t)[:8] if t else ''
            return _ts_map.get(key) or _ts_map.get(key + ':00')
        incoming = []
        for cls in rehydrated:
            s_code = (cls.get('subjectcode') or cls.get('subject_code') or '').upper()
            st, et = cls.get('start_time'), cls.get('end_time')
            if not (st and et): continue
            r_st = _ts_id(st)
            r_et = _ts_id(et)
            if not (r_st and r_et): continue
            # Check EVERY day in days_list (paired subjects have 2 days)
            all_days = cls.get('days_list') or []
            if not all_days:
                single = cls.get('daydesc') or cls.get('day') or ''
                if single:
                    all_days = [single]
            for day in all_days:
                if day:
                    incoming.append({'code': s_code, 'day': day, 'start': r_st, 'end': r_et})

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
                JOIN   sections sec         ON sc.sectionid              = sec.sectionid
                JOIN   program_yearlevel pyl ON sec.programyearlevelid   = pyl.programyearlevelid
                JOIN   curriculumsubject cs2 ON sc.curriculumsubjectid  = cs2.curriculumsubjectid
                WHERE  UPPER(pyl.programcode) = UPPER(%s)
                  AND  pyl.yearlevel           = %s
                  AND  sc.semesterid          = %s
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
        # _group_carry_forward_sessions merges per-day rows for paired subjects so _insert_batch
        # creates a single schedule+version record with multiple sessions (not duplicate records).
        other_sessions = _group_carry_forward_sessions(
            _fetch_section_sessions(cur, program, year_level, sem_id,
                                    exclude_codes=all_codes_to_archive,
                                    draft_only=True, section_id=ctx_section_id)
        )

        # Archive all Draft records for this section + source namespace before writing new snapshot.
        _archive_status(cur, program, year_level, term, sem_id, 'Draft', source=draft_source)

        # Draft snapshot = other subjects' Drafts (carry-forward) + newly submitted Draft slices.
        # NOTE: Published baseline is intentionally excluded — Draft must only contain Draft content.
        #       The Published slices are shown in the UI via existing_sessions without being in Draft.
        complete_snapshot = other_sessions + rehydrated
        if complete_snapshot:
            _insert_batch(cur, complete_snapshot, sem_id, 'Draft', new_v, program, year_level,
                          source=draft_source, section_id=ctx_section_id)

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

        conn.commit()

        return jsonify({'success': True, 'draft_version': new_v})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        try:
            if cur:  cur.close()
            if conn: conn.close()
        except Exception:
            pass

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
                   cs.subjectcode, cs.subjectname,
                   COALESCE(cs.creditunits, 0)      AS creditunits,
                   COALESCE(cs.lecturehours,  0)    AS lecturehours,
                   COALESCE(cs.laboratoryhours, 0)  AS laboratoryhours,
                   f.employeenumber AS faculty_id,
                   f.lastname || ', ' || f.firstname AS instructor,
                   r.roomid AS room_id, r.roomname, r.roomtype,
                   sv.status,
                   TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_time,
                   TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_time
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum cu ON cs.curriculumid = cu.curriculumid
            LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
            LEFT JOIN faculty f ON sc.employeenumber = f.employeenumber
            LEFT JOIN room r ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
            WHERE sv.status = 'Draft'
              AND UPPER(cu.programcode) = UPPER(%s)
              AND cs.yearlevel = %s
              AND sc.semesterid = (
                    SELECT semesterid FROM semester
                    WHERE academicyearid = %s AND semestertype = %s LIMIT 1)
        """, (program, int(year_level), ay, sem))
        sessions = []
        for r in (rows or []):
            sessions.append({
                'subject_code':    r['subjectcode'],
                'subject_name':    r['subjectname'],
                'faculty_id':      r['faculty_id'],
                'instructor':      r['instructor'] or '',
                'room_id':         str(r['room_id']) if r['room_id'] else '',
                'room':            r['roomname'] or '',
                'room_type':       r['roomtype'] or '',
                'roomtype':        r['roomtype'] or '',
                'day':             r['daydesc'],
                'days':            (r['daydesc'] or '')[:3].upper(),
                'days_list':       [r['daydesc']],
                'start_time':      r['start_time'] or '',
                'end_time':        r['end_time']   or '',
                'time':            f"{r['start_time']} - {r['end_time']}" if r['start_time'] else '',
                'units':           float(r['creditunits'] or 0),
                'lab_hours':       float(r['laboratoryhours'] or 0),
                'laboratoryhours': float(r['laboratoryhours'] or 0),
                'lecturehours':    float(r['lecturehours'] or 0),
                'ay': ay, 'sem': sem,
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
        all_viols  = CSPValidator(config=_hc_cfg).validate(rehydrated, faculty_map)
        # HC_SPEC is warning-only — separate it so the client can show an advisory without blocking
        hard_viols = [v for v in all_viols if v.get('severity') != 'warning']
        warn_viols = [v for v in all_viols if v.get('severity') == 'warning']
        violations = hard_viols   # only hard violations block save
        return jsonify({
            'success': True,
            'violations': violations,
            'warnings': warn_viols,
            'has_violations': bool(violations)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/approve', methods=['POST'])
def api_approve_schedule():
    try:
        data, ctx = request.json or {}, (request.json or {}).get('context', {})
        program, year_level, term, ay = ctx.get('program'), int(ctx.get('yearLevel')), ctx.get('term'), ctx.get('acadYear')
        _ctx_section_id = ctx.get('sectionId') or ctx.get('section_id') or ctx.get('section') or None
        if _ctx_section_id:
            try: _ctx_section_id = int(_ctx_section_id)
            except (ValueError, TypeError): _ctx_section_id = None

        # ── Server-side CSP guard ── block approval if any hard constraint is violated
        from scheduler import CSPValidator
        sched_data  = _rehydrate_schedule(list(data.get('schedule_data', [])))
        faculty_map = _load_faculty_map()

        # Guard: nothing to publish
        if not sched_data:
            return jsonify({
                'success': False,
                'error':   'No sessions to publish. Please add schedule entries before approving.',
            }), 400

        # Load HC config to honour the publish-gate toggle
        from database import load_scheduler_config as _load_hc_cfg
        _hc_cfg = _load_hc_cfg()
        publish_gate_on = bool(_hc_cfg.get('hc_publish_gate_enabled', 1))

        # Intra-payload check: HC1–HC10 among the submitted sessions themselves
        all_viols  = CSPValidator(config=_hc_cfg).validate(sched_data, faculty_map)
        violations = [v for v in all_viols if v.get('severity') != 'warning']

        if publish_gate_on and violations:
            return jsonify({
                'success': False,
                'error':   f'Cannot approve: {len(violations)} unresolved constraint violation(s). '
                           'Resolve all conflicts in the Manual Editor before approving.',
                'violations': violations,
            }), 400

        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        sem_id = _get_semester_id(cur, ay, term)

        # ── #11: Cross-program faculty load check ─────────────────────────────
        # Block approval if any faculty would exceed their load across all sections.
        _load_viols_approve = _check_cross_program_faculty_loads(
            list(data.get('schedule_data', [])), faculty_map, sem_id,
            exclude_program=program, exclude_year_level=year_level
        )
        if _load_viols_approve:
            cur.close(); conn.close()
            _load_msg_a = '; '.join(
                f"{v['faculty_name']}: {v['total_load']}/{v['max_load']} units "
                f"(+{v['overload_by']} over limit)"
                for v in _load_viols_approve
            )
            return jsonify({
                'success':         False,
                'error':           f'Faculty load limit exceeded — cannot approve: {_load_msg_a}',
                'load_violations': _load_viols_approve,
            }), 400

        # ── Designee night-cap check (PT/Night Teaching Service) ──────────────
        _night_viols_approve = _check_designee_night_limit(
            list(data.get('schedule_data', [])), faculty_map, sem_id,
            exclude_program=program, exclude_year_level=year_level
        )
        if _night_viols_approve:
            cur.close(); conn.close()
            return jsonify({
                'success':         False,
                'error':           'Designee night-teaching cap exceeded — cannot approve: '
                                   + '; '.join(v['detail'] for v in _night_viols_approve),
                'night_violations': _night_viols_approve,
            }), 400

        _ensure_source_col(cur)
        cur.execute("""
    SELECT COALESCE(MAX(sv.version_number), 0) AS max_v
    FROM public.schedule_version sv
    JOIN public.schedule s ON sv.scheduleid = s.scheduleid
    JOIN public.curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
    JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
    WHERE UPPER(c.programcode) = UPPER(%s)
      AND cs.yearlevel = %s
      AND s.semesterid = %s
      AND sv.source != 'local'
""", (program, year_level, sem_id))
        max_v = cur.fetchone()['max_v']

        submitted_codes = list({
            (cls.get('subject_code') or cls.get('subjectcode') or '').upper()
            for cls in sched_data
            if cls.get('subject_code') or cls.get('subjectcode')
        })
        # Carry forward only currently Published subjects (not Draft-only ones).
        # Draft-only subjects must NOT appear in a Published snapshot unless explicitly published.
        # A subject that's already Published but ALSO has a newer, unapproved Draft on top of it
        # gets carried forward using the DRAFT's content — otherwise approving this (unrelated)
        # subject would silently revert that other subject back to its stale last-Published state.
        other_sessions = _group_carry_forward_sessions(
            _fetch_section_sessions(
                cur, program, year_level, sem_id,
                exclude_codes=submitted_codes,
                published_only=True, section_id=_ctx_section_id
            )
        )

        # The Draft rows just absorbed into this Published snapshot are no longer a pending,
        # unreviewed change — archive them so they don't linger as a stale "still Draft" copy.
        _draft_absorbed_codes = {
            r['subjectcode'].upper() for r in other_sessions if r.get('status') == 'Draft'
        }
        if _draft_absorbed_codes:
            _archive_status_for_subjects(cur, program, year_level, term, sem_id,
                                         'Draft', list(_draft_absorbed_codes))

        # _build_published_baseline now always returns [] — every submitted subject is fully
        # replaced by sched_data.  The old slot-level carry-forward caused false room conflicts
        # when the generator changed a subject's time/room (stale old Published slot appeared
        # in existing_for_conflict and triggered a spurious conflict against the new assignment).
        published_baseline = _group_carry_forward_sessions(
            _build_published_baseline(cur, program, year_level, sem_id, submitted_codes, sched_data)
        )

        # Cross-session HC9: check new sessions against already-published existing sessions
        # (the intra-payload check above only compares new sessions with each other).
        # Generator entries carry days_list with ALL days (e.g. ['Monday','Thursday']) so we
        # check EVERY day, not just the primary 'day' field, to catch Thursday-only conflicts.
        if publish_gate_on:
            from scheduler import format_time_12h as _fmt12h
            existing_for_conflict = other_sessions + published_baseline
            cross_violations = []
            seen_cross_keys = set()
            for new in sched_data:
                r_new  = str(new.get('room_id') or new.get('roomid') or '')
                st_new = new.get('start_time')
                et_new = new.get('end_time')
                if not r_new or not st_new or not et_new:
                    continue
                # Collect all days this session occupies
                all_new_days = list(new.get('days_list') or [])
                if not all_new_days:
                    primary = new.get('day') or new.get('daydesc') or ''
                    if primary:
                        all_new_days = [primary]
                if not all_new_days:
                    continue
                for day_new in all_new_days:
                    if not day_new:
                        continue
                    for ex in existing_for_conflict:
                        r_ex   = str(ex.get('roomid') or ex.get('room_id') or '')
                        day_ex = ex.get('daydesc') or ex.get('day') or ''
                        st_ex  = ex.get('start_time')
                        et_ex  = ex.get('end_time')
                        if r_new != r_ex or day_new != day_ex:
                            continue
                        if isinstance(st_ex, str): st_ex = _parse_time_str(st_ex)
                        if isinstance(et_ex, str): et_ex = _parse_time_str(et_ex)
                        if st_ex and et_ex and st_new < et_ex and et_new > st_ex:
                            sc_new = (new.get('subject_code') or new.get('subjectcode') or '?').upper()
                            sc_ex  = (ex.get('subjectcode') or ex.get('subject_code') or '?').upper()
                            fac_new = str(new.get('faculty_id') or new.get('employeenumber') or '')
                            fac_ex  = str(ex.get('employeenumber') or ex.get('faculty_id') or '')
                            # Merge class: same subject + same faculty in same room/time is allowed
                            if sc_new == sc_ex and fac_new and fac_new == fac_ex:
                                continue
                            vkey = (r_new, day_new, str(st_new), sc_new, sc_ex)
                            if vkey in seen_cross_keys:
                                continue
                            seen_cross_keys.add(vkey)
                            room_label = new.get('room') or r_new
                            cross_violations.append({
                                'rule':    'HC9',
                                'subject': f'{sc_new} / {sc_ex}',
                                'detail':  (
                                    f'Room {room_label} is already occupied on {day_new} '
                                    f'{_fmt12h(st_ex)}–{_fmt12h(et_ex)} '
                                    f'by {sc_ex}. The new session '
                                    f'({_fmt12h(st_new)}–{_fmt12h(et_new)}) '
                                    f'cannot be published into the same slot.'
                                )
                            })
            if cross_violations:
                cur.close(); conn.close()
                return jsonify({
                    'success': False,
                    'error':   f'Cannot approve: {len(cross_violations)} room conflict(s) with already-published sessions.',
                    'violations': cross_violations,
                }), 400

        # ── Duplicate guard: block approval only if a subject being submitted now ──
        # already has a Published schedule (a genuine overwrite). Publishing a
        # different subject for the same program/year-level must NOT trigger this —
        # other already-published subjects are carried forward untouched regardless.
        # Check AFTER all validation so conflicts are caught first.
        override = bool(data.get('override'))
        if not override:
            cur.execute("""
                SELECT MAX(sv.datecreated) AS latest_date,
                       COUNT(DISTINCT cs.subjectcode) AS subject_count
                FROM public.schedule_version sv
                JOIN public.schedule s ON sv.scheduleid = s.scheduleid
                JOIN public.curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
                JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
                WHERE UPPER(c.programcode) = UPPER(%s)
                  AND cs.yearlevel = %s
                  AND s.semesterid = %s
                  AND sv.status = 'Published'
                  AND sv.source IS DISTINCT FROM 'local'
                  AND UPPER(cs.subjectcode) = ANY(%s)
            """, (program, year_level, sem_id, submitted_codes))
            _ex = cur.fetchone()
            if _ex and _ex['subject_count'] and int(_ex['subject_count']) > 0:
                _date_str = ''
                if _ex['latest_date']:
                    try:    _date_str = _ex['latest_date'].strftime('%B %d, %Y')
                    except: _date_str = str(_ex['latest_date'])
                cur.close(); conn.close()
                return jsonify({
                    'success':           False,
                    'needs_confirmation': True,
                    'existing_info': {
                        'subject_count': int(_ex['subject_count']),
                        'date':          _date_str,
                    },
                }), 200

        cur.execute("""
    SELECT COALESCE(MAX(sv.version_number), 0) AS max_v
    FROM public.schedule_version sv
    JOIN public.schedule s ON sv.scheduleid = s.scheduleid
    JOIN public.curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
    JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
    WHERE UPPER(c.programcode) = UPPER(%s)
      AND cs.yearlevel = %s
      AND s.semesterid = %s
      AND sv.source IS DISTINCT FROM 'local'
""", (program, year_level, sem_id))
        max_v = cur.fetchone()['max_v']

        # Archive ALL existing Published revisions (any source except local arrangements)
        # to prevent duplicate Published schedules from accumulating in SIS.
        # Draft history is preserved — only Published status is archived here.
        cur.execute("""
            UPDATE public.schedule_version sv
            SET status = 'Archive'
            FROM public.schedule s, public.curriculumsubject cs, public.curriculum c
            WHERE sv.scheduleid = s.scheduleid
              AND s.curriculumsubjectid = cs.curriculumsubjectid
              AND cs.curriculumid = c.curriculumid
              AND c.programcode = %s
              AND cs.yearlevel = %s
              AND s.semesterid = %s
              AND sv.status = 'Published'
              AND sv.source IS DISTINCT FROM 'local'
        """, (program, year_level, sem_id))

        # Published snapshot = residual Published subjects (not being replaced) + new sessions.
        # published_baseline is always [] now (see _build_published_baseline).
        complete_snapshot = other_sessions + published_baseline + sched_data
        _insert_batch(cur, complete_snapshot, sem_id, 'Published', max_v + 1, program, year_level,
                      section_id=_ctx_section_id)

        # Auto-cleanup: remove ONLY the exact slots that were just published from the active Draft.
        # Use slot-level keys (subject+day+time), NOT subject codes — a subject can have multiple
        # slices and publishing one slot (e.g. Thursday) must NOT wipe unrelated Draft slots for
        # the same subject (e.g. Monday Draft).
        # Loop over days_list so GA items (with multiple days) mark ALL their days as published.
        published_slot_keys = set()
        for s in sched_data:
            sc  = (s.get('subjectcode') or s.get('subject_code') or '').upper()
            st  = str(s.get('start_time', '')).split('.')[0]
            all_pub_days = list(s.get('days_list') or [])
            if not all_pub_days:
                single = s.get('daydesc') or s.get('day') or ''
                if single:
                    all_pub_days = [single]
            for day in all_pub_days:
                if day:
                    published_slot_keys.add((sc, day, st))

        all_current_draft = _fetch_section_sessions(
            cur, program, year_level, sem_id,
            draft_only=True, section_id=_ctx_section_id
        )
        remaining_draft = _group_carry_forward_sessions([
            s for s in all_current_draft
            if ((s.get('subjectcode') or '').upper(),
                s.get('daydesc') or '',
                str(s.get('start_time', '')).split('.')[0]) not in published_slot_keys
        ])

        # Only rebuild Draft if at least one slot was actually removed (i.e. a Draft slot was
        # published). If the published slots were already Published (not in Draft), leave Draft
        # completely untouched — no new revision, no data loss.
        new_draft_v = None
        if len(remaining_draft) < len(all_current_draft):
            _archive_status(cur, program, year_level, term, sem_id, 'Draft', source='manual_editor')
            if remaining_draft:
                new_draft_v = max_v + 2
                _insert_batch(cur, remaining_draft, sem_id, 'Draft', new_draft_v, program, year_level,
                              section_id=_ctx_section_id)
        else:
            # No draft slots were consumed — check whether a draft already exists so we can
            # surface its version number to the caller (used by Generate Schedule UI).
            cur.execute("""
                SELECT COALESCE(MAX(sv.version_number), 0) AS max_draft_v
                FROM schedule_version sv
                JOIN schedule s ON sv.scheduleid = s.scheduleid
                JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
                JOIN curriculum c ON cs.curriculumid = c.curriculumid
                WHERE UPPER(c.programcode) = UPPER(%s)
                  AND cs.yearlevel = %s
                  AND s.semesterid = %s
                  AND sv.status = 'Draft'
                  AND sv.source = 'manual_editor'
            """, (program, year_level, sem_id))
            dv_row = cur.fetchone()
            if dv_row and dv_row['max_draft_v']:
                new_draft_v = dv_row['max_draft_v']

        conn.commit(); cur.close(); conn.close()
        return jsonify({'success': True, 'published_version': max_v + 1, 'draft_version': new_draft_v})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedule/drafts/<int:version_id>', methods=['DELETE'])
def api_delete_draft(version_id):
    """Archive all Draft schedule_versions that share the same program/yearlevel/semester as version_id."""
    try:
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        # Resolve program/year/term/semesterid from the given versionid.
        # The versionid may belong to any one Draft row for this group — that's enough to identify the group.
        cur.execute("""
            SELECT c.programcode AS programcode, cs.yearlevel, cs.semester AS term, s.semesterid
            FROM public.schedule_version sv
            JOIN public.schedule s ON sv.scheduleid = s.scheduleid
            JOIN public.curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
            JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
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
                cs.subjectcode,
                cs.subjectname,
                COALESCE(cs.creditunits,0) AS units,
                COALESCE(cs.tuitionhours, cs.lecturehours+cs.laboratoryhours, 0) AS hrs,
                COALESCE(pyl.programcode,'') || '-' || COALESCE(pyl.yearlevel::text,'')
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
            JOIN semester sem ON sc.semesterid = sem.semesterid
            LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            LEFT JOIN room r ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
            WHERE sc.employeenumber = %s
              AND sem.academicyearid = %s
              AND sem.semestertype   = %s
              AND sv.status IN ('Published','Draft')
            ORDER BY cs.subjectcode, ts_s.timevalue
        """, (emp_num, ay_id, sem))
        sessions = [dict(r) for r in (cur.fetchall() or [])]
        cur.execute("""
            SELECT COALESCE(SUM(d.units),0) AS total,
                   COALESCE(SUM(d.hrs),0)   AS total_hrs
            FROM (
                SELECT DISTINCT cs.subjectcode,
                    COALESCE(cs.creditunits,0) AS units,
                    COALESCE(cs.tuitionhours, cs.lecturehours+cs.laboratoryhours, 0) AS hrs
                FROM schedule_version sv
                JOIN schedule sc ON sv.scheduleid=sc.scheduleid
                JOIN curriculumsubject cs ON sc.curriculumsubjectid=cs.curriculumsubjectid
                JOIN semester sem ON sc.semesterid=sem.semesterid
                WHERE sc.employeenumber=%s AND sem.academicyearid=%s
                  AND sem.semestertype=%s AND sv.status IN ('Published','Draft')
            ) d
        """, (emp_num, ay_id, sem))
        total_row = cur.fetchone()
        assigned  = int(total_row['total'] or 0) if total_row else 0
        assigned_hrs = float(total_row['total_hrs'] or 0) if total_row else 0.0

        # Subjects assigned via "Assign Faculty" button but not yet scheduled (no sessions yet).
        # Show these as pending rows so Faculty Load reflects ALL reservations, not just sessions.
        pending_sessions = []
        pending_units    = 0
        pending_hrs      = 0.0
        try:
            _ensure_faculty_assignment_table(cur)
            cur.execute(
                "SELECT semesterid FROM semester WHERE academicyearid=%s AND semestertype=%s LIMIT 1",
                (ay_id, sem))
            _sem_row = cur.fetchone()
            if _sem_row:
                _sem_id = _sem_row['semesterid']
                _sched_codes = set(s['subjectcode'].upper() for s in sessions)
                cur.execute("""
                    SELECT sfa.subjectcode,
                           MAX(sfa.programcode) AS programcode,
                           MAX(sfa.yearlevel)   AS yearlevel,
                           COALESCE(MAX(cs.subjectname), MAX(sfa.subjectcode)) AS subjectname,
                           COALESCE(MAX(cs.creditunits), 0) AS units,
                           COALESCE(MAX(COALESCE(cs.tuitionhours, cs.lecturehours+cs.laboratoryhours, 0)), 0) AS hrs
                    FROM public.subject_faculty_assignment sfa
                    LEFT JOIN curriculumsubject cs
                           ON UPPER(cs.subjectcode) = UPPER(sfa.subjectcode)
                    WHERE sfa.employeenumber = %s
                      AND sfa.semesterid     = %s
                    GROUP BY sfa.subjectcode
                """, (emp_num, _sem_id))
                for pr in (cur.fetchall() or []):
                    code = (pr['subjectcode'] or '').upper()
                    if code in _sched_codes:
                        continue
                    units_val = int(pr['units'] or 0)
                    hrs_val   = float(pr['hrs'] or 0)
                    pending_sessions.append({
                        'subjectcode':  pr['subjectcode'],
                        'subjectname':  pr['subjectname'],
                        'units':        units_val,
                        'year_section': f"{pr['programcode']}-{pr['yearlevel']}",
                        'time_range':   '(Pending — no schedule)',
                        'time_code':    '0000',
                        'days':         '—',
                        'room':         '—',
                        'effectivity':  '—',
                        'status':       'Pending',
                    })
                    pending_units += units_val
                    pending_hrs   += hrs_val
        except Exception:
            pass

        total_assigned  = assigned + pending_units
        total_hrs       = assigned_hrs + pending_hrs
        cur.close(); conn.close()
        return jsonify({
            'success': True,
            'faculty': dict(fac),
            'sessions': sessions,
            'pending_sessions': pending_sessions,
            'assigned_units': total_assigned,
            'max_units': max_units,
            'available_units': max(0, max_units - total_assigned),
            'total_teaching_hours': total_hrs
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
            extra_where += ' AND UPPER(c.programcode) = UPPER(%s)'
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
            JOIN schedule sc          ON sc.employeenumber      = f.employeenumber
            JOIN semester s           ON sc.semesterid          = s.semesterid
            JOIN schedule_version sv  ON sv.scheduleid          = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c         ON cs.curriculumid        = c.curriculumid
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
        is_drafts = 'drafts' in request.path
        if is_drafts:
            # One entry per program/year/semester/section — use the latest versionid/version_number.
            # Including sectionid in DISTINCT ON keeps each section as a separate draft card.
            rows = query_db("""
                SELECT DISTINCT ON (c.programcode, cs.yearlevel, cs.semester, ay.academicyearid, sg.sectionid)
                       sv.versionid, sv.version_number, sv.status, sv.datecreated,
                       COALESCE(sv.source, 'official') AS source,
                       c.programcode AS programcode, cs.yearlevel, cs.semester AS term,
                       ay.academicyearid AS acadyear,
                       sg.sectionid, COALESCE(sec.sectionname, '') AS sectionname
                FROM   public.schedule_version sv
                JOIN   public.schedule sg    ON sv.scheduleid             = sg.scheduleid
                LEFT JOIN public.sections sec ON sg.sectionid             = sec.sectionid
                JOIN   public.curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
                JOIN   public.curriculum c   ON cs.curriculumid           = c.curriculumid
                JOIN   public.semester sem   ON sg.semesterid             = sem.semesterid
                JOIN   public.academicyear ay ON sem.academicyearid       = ay.academicyearid
                WHERE  sv.status = 'Draft'
                ORDER BY c.programcode, cs.yearlevel, cs.semester, ay.academicyearid,
                         sg.sectionid, sv.version_number DESC
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
                           MAX(sv.original_status)  AS original_status,
                           MAX(sv.source)           AS source,
                           c.programcode            AS programcode,
                           cs.yearlevel,
                           cs.semester              AS term,
                           ay.academicyearid        AS acadyear,
                           sg.sectionid,
                           MAX(sec.sectionname)     AS sectionname
                    FROM   public.schedule_version sv
                    JOIN   public.schedule sg          ON sv.scheduleid           = sg.scheduleid
                    LEFT JOIN public.sections sec      ON sg.sectionid            = sec.sectionid
                    JOIN   public.curriculumsubject cs  ON sg.curriculumsubjectid  = cs.curriculumsubjectid
                    JOIN   public.curriculum c          ON cs.curriculumid         = c.curriculumid
                    JOIN   public.semester sem          ON sg.semesterid           = sem.semesterid
                    JOIN   public.academicyear ay       ON sem.academicyearid      = ay.academicyearid
                    GROUP BY c.programcode, cs.yearlevel, cs.semester, ay.academicyearid,
                             sv.version_number, sg.sectionid
                )
                SELECT *,
                       CASE WHEN COALESCE(original_status, status) = 'Draft' THEN
                           SUM(CASE WHEN COALESCE(original_status, status) = 'Draft' THEN 1 ELSE 0 END)
                               OVER (PARTITION BY programcode, yearlevel, term, acadyear, sectionid
                                     ORDER BY version_number
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                       END AS draft_version_number,
                       CASE WHEN COALESCE(original_status, status) = 'Published' THEN
                           SUM(CASE WHEN COALESCE(original_status, status) = 'Published' THEN 1 ELSE 0 END)
                               OVER (PARTITION BY programcode, yearlevel, term, acadyear, sectionid
                                     ORDER BY version_number
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                       END AS published_version_number
                FROM versioned
                ORDER BY programcode, yearlevel, term, acadyear, sectionid, version_number
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
                'sectionid':               r.get('sectionid'),
                'sectionname':             r.get('sectionname', ''),
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
            SELECT c.programcode AS programcode, cs.yearlevel, cs.semester AS term, sg.semesterid,
                   sv.version_number AS src_vn,
                   sv.original_status AS src_orig
            FROM   schedule_version sv
            JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
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

        # Always archive BOTH Published and Draft before restoring.
        # This prevents the subject from showing PUB/DRAFT simultaneously after restore,
        # which confuses users and creates duplicate calendar blocks.
        _archive_status(cur, prog, year_level, term, sem_id, 'Published', source='manual_editor')
        _archive_status(cur, prog, year_level, term, sem_id, 'Draft',     source='manual_editor')

        if restore_mode == 'draft':
            target_status = 'Draft'
        else:
            target_status = row['src_orig'] or 'Published'

        # New version_number = max manual_editor version for this section + 1
        cur.execute("""
            SELECT COALESCE(MAX(sv.version_number), 0) AS max_v
            FROM   schedule_version sv
            JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
            WHERE  UPPER(c.programcode) = UPPER(%s) AND cs.yearlevel = %s AND sg.semesterid = %s
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
            WHERE  UPPER(c.programcode) = UPPER(%s) AND cs.yearlevel = %s
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
            SELECT c.programcode AS programcode, cs.yearlevel, sg.semesterid, sv.status
            FROM   schedule_version sv
            JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
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
            WHERE  UPPER(c.programcode) = UPPER(%s)
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
                SELECT sv.version_number, c.programcode AS programcode, cs.yearlevel, sg.semesterid
                FROM   schedule_version sv
                JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
                JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
                JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
                WHERE  sv.versionid = %s
                LIMIT  1
            )
            SELECT cs.subjectcode, cs.subjectname,
                   COALESCE(f.lastname || ', ' || f.firstname, 'TBA') AS instructor,
                   COALESCE(r.roomname, 'TBA')                        AS roomname,
                   ss.daydesc,
                   TO_CHAR(ts_s.timevalue, 'HH24:MI')                AS start_time,
                   TO_CHAR(ts_e.timevalue, 'HH24:MI')                AS end_time,
                   COALESCE(cs.lecturehours,    0)                    AS lecturehours,
                   COALESCE(cs.laboratoryhours, 0)                    AS laboratoryhours,
                   COALESCE(cs.creditunits,     0)                    AS creditunits,
                   sv.status, sv.versionid, sv.version_number
            FROM   ref
            JOIN   schedule_version sv  ON sv.version_number = ref.version_number
            JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
            LEFT JOIN faculty f          ON sg.employeenumber      = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid         = sv.versionid
            LEFT JOIN room r             ON ss.roomid              = r.roomid
            LEFT JOIN timeslot ts_s      ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN timeslot ts_e      ON ss.endtimeid           = ts_e.timeid
            WHERE  c.programcode = ref.programcode
              AND  cs.yearlevel    = ref.yearlevel
              AND  sg.semesterid   = ref.semesterid
            ORDER BY cs.subjectname, ts_s.timevalue NULLS LAST, ss.daydesc
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
            SELECT cs.subjectcode, cs.subjectname,
                   COALESCE(f.lastname || ', ' || f.firstname, 'TBA') AS instructor,
                   COALESCE(r.roomname, 'TBA') AS roomname,
                   ss.daydesc,
                   TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS start_time,
                   TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS end_time,
                   sv.status
            FROM schedule_version sv
            JOIN schedule sc          ON sv.scheduleid           = sc.scheduleid
            JOIN curriculumsubject cs  ON sc.curriculumsubjectid  = cs.curriculumsubjectid
            JOIN sections sec          ON sc.sectionid            = sec.sectionid
            JOIN program_yearlevel pyl ON sec.programyearlevelid  = pyl.programyearlevelid
            LEFT JOIN faculty f        ON sc.employeenumber       = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid        = sv.versionid
            LEFT JOIN room r           ON ss.roomid               = r.roomid
            LEFT JOIN timeslot ts_s    ON ss.starttimeid          = ts_s.timeid
            LEFT JOIN timeslot ts_e    ON ss.endtimeid            = ts_e.timeid
            WHERE UPPER(pyl.programcode) = UPPER(%s)
              AND pyl.yearlevel           = %s
              AND sv.version_number      = %s
              AND sc.semesterid = (
                  SELECT semesterid FROM semester
                  WHERE academicyearid = %s AND semestertype = %s LIMIT 1
              )
            ORDER BY cs.subjectname, ts_s.timevalue NULLS LAST, ss.daydesc
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
                   c.programcode AS programcode, cs.yearlevel, sem.semestertype AS term,
                   ay.academicyearid AS acadyear, sc.semesterid
            FROM schedule_version sv
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
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
            SELECT cs.subjectcode AS subject_code, cs.subjectname AS description, cs.lecturehours AS lec_hours,
                   cs.laboratoryhours AS lab_hours, cs.creditunits AS units, c.programcode AS course,
                   sc.employeenumber AS faculty_id, CONCAT(f.lastname, ', ', f.firstname) AS instructor,
                   ss.daydesc, TO_CHAR(ts_s.timevalue, 'HH24:MI') AS start_time, TO_CHAR(ts_e.timevalue, 'HH24:MI') AS end_time,
                   r.roomname AS room, r.roomid, sv.version_number AS row_version
            FROM schedule_version sv JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN schedule_sessions ss ON ss.versionid = sv.versionid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
            LEFT JOIN faculty f ON sc.employeenumber = f.employeenumber
            LEFT JOIN room r ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
            WHERE c.programcode = %s AND cs.yearlevel = %s AND sc.semesterid = %s AND sv.status = 'Draft'
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


@app.route('/api/schedule/latest-draft')
def api_schedule_latest_draft():
    """Return the latest Draft sessions for a context so Generate Schedule can display
    the most recently saved Manual Editor draft when the user navigates back."""
    program       = request.args.get('program', '').strip().upper()
    year_level_s  = request.args.get('year_level', '').strip()
    ay            = request.args.get('ay', '').strip()
    term          = request.args.get('term', '').strip()

    if not (program and year_level_s and ay and term):
        return jsonify({'success': False, 'error': 'Missing parameters'}), 400
    try:
        year_level = int(year_level_s)
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid year_level'}), 400

    try:
        conn = get_db_connection()
        cur  = conn.cursor(cursor_factory=RealDictCursor)

        sem_id = _get_semester_id(cur, ay, term)
        if not sem_id:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Semester not found'})

        # Fetch all Draft sessions for this context (latest version per subject wins).
        cur.execute("""
            SELECT cs.subjectcode AS subject_code, cs.subjectname AS description,
                   cs.lecturehours AS lec_hours, cs.laboratoryhours AS lab_hours,
                   cs.creditunits AS units, c.programcode AS course,
                   sc.employeenumber AS faculty_id,
                   COALESCE(f.lastname || ', ' || f.firstname, 'TBA') AS instructor,
                   ss.daydesc,
                   TO_CHAR(ts_s.timevalue, 'HH24:MI') AS start_time,
                   TO_CHAR(ts_e.timevalue, 'HH24:MI') AS end_time,
                   r.roomname AS room, r.roomid,
                   sv.version_number AS row_version,
                   sv.versionid
            FROM schedule_version sv
            JOIN schedule sc          ON sv.scheduleid          = sc.scheduleid
            JOIN schedule_sessions ss ON ss.versionid           = sv.versionid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c         ON cs.curriculumid        = c.curriculumid
            LEFT JOIN faculty f       ON sc.employeenumber      = f.employeenumber
            LEFT JOIN room r          ON ss.roomid              = r.roomid
            LEFT JOIN timeslot ts_s   ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN timeslot ts_e   ON ss.endtimeid           = ts_e.timeid
            WHERE UPPER(c.programcode) = %s
              AND cs.yearlevel = %s
              AND sc.semesterid = %s
              AND sv.status = 'Draft'
              AND sv.version_number = (
                  SELECT MAX(sv2.version_number)
                  FROM schedule_version sv2
                  JOIN schedule sc2 ON sv2.scheduleid = sc2.scheduleid
                  JOIN curriculumsubject cs2 ON sc2.curriculumsubjectid = cs2.curriculumsubjectid
                  JOIN curriculum c2 ON cs2.curriculumid = c2.curriculumid
                  WHERE sv2.status = 'Draft'
                    AND sc2.semesterid = %s
                    AND UPPER(c2.programcode) = %s
                    AND cs2.yearlevel = %s
              )
            ORDER BY ts_s.timevalue NULLS LAST
        """, (program, year_level, sem_id, sem_id, program, year_level))
        rows = cur.fetchall()
        cur.close(); conn.close()

        if not rows:
            return jsonify({'success': False, 'error': 'No draft sessions found'})

        _abbr = {'Monday':'MON','Tuesday':'TUE','Wednesday':'WED',
                 'Thursday':'THU','Friday':'FRI','Saturday':'SAT','Sunday':'SUN'}
        max_v = max(r['row_version'] for r in rows)
        sched = [{
            'subject_code': r['subject_code'],
            'description':  r['description'],
            'lec_hours':    r['lec_hours'],
            'lab_hours':    r['lab_hours'],
            'units':        r['units'],
            'course':       r['course'],
            'faculty_id':   r['faculty_id'],
            'instructor':   r['instructor'],
            'days': _abbr.get(r['daydesc'], (r['daydesc'] or '')[:3].upper()),
            'day':  r['daydesc'],
            'time': (f"{_fmt_12h(r['start_time'])} – {_fmt_12h(r['end_time'])}"
                     if r['start_time'] else ''),
            'start_time': r['start_time'],
            'end_time':   r['end_time'],
            'hours': str((r['lec_hours'] or 0) + (r['lab_hours'] or 0)),
            'room':    r['room'],
            'room_id': r['roomid'],
        } for r in rows]

        return jsonify({'success': True, 'schedule_data': sched, 'version': max_v})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/year-levels-by-program')
def api_year_levels_by_program():
    """Return active year levels for a program in the current Academic Year."""
    program = request.args.get('program', '').strip()
    if not program:
        return jsonify({'year_levels': [1, 2, 3, 4]})
    try:
        active = query_db("""
            SELECT ay.academicyearid FROM semester s
            JOIN academicyear ay ON s.academicyearid = ay.academicyearid
            WHERE s.isactive = TRUE LIMIT 1
        """, one=True)
        if not active:
            return jsonify({'year_levels': [1, 2, 3, 4]})
        rows = query_db("""
            SELECT pyl.yearlevel
            FROM program_yearlevel pyl
            WHERE pyl.programcode = %s AND pyl.academicyearid = %s AND pyl.isactive = TRUE
            ORDER BY pyl.yearlevel
        """, (program, active['academicyearid']))
        return jsonify({'year_levels': [r['yearlevel'] for r in rows] if rows else []})
    except Exception:
        return jsonify({'year_levels': [1, 2, 3, 4]}), 500


@app.route('/api/curriculum-by-year')
def api_curriculum_by_year():
    p          = request.args.get('program', '').strip()
    acad_year  = request.args.get('acad_year', '').strip()   # e.g. "AY2526"
    year_level = request.args.get('year_level', '').strip()  # e.g. "3"

    try:
        # When we know the academic year and year level, find the curriculum that
        # was in effect when this cohort first enrolled.
        # Entry year = AY start year − (year_level − 1)
        # e.g. Year 3 in AY2526 → 2025 − 2 = 2023 → use curriculum whose start ≤ 2023
        if acad_year and year_level:
            # Parse "AY2526" → extract digits "2526" → first 2 = "25" → 2000+25 = 2025
            ay_digits  = ''.join(c for c in acad_year if c.isdigit())
            ay_start   = int(ay_digits[:2]) + 2000 if len(ay_digits) >= 2 else 0
            entry_start = ay_start - (int(year_level) - 1)

            # curriculumyear is stored as "2022-2023", "2025-2026", etc.
            # Use SUBSTRING(..., 1, 4) to extract the 4-digit start year — same
            # pattern used throughout the rest of this app (see program_yearlevel logic).
            row = query_db("""
                SELECT c.curriculumyear FROM curriculum c
                WHERE c.programcode = %s
                  AND CAST(SUBSTRING(c.curriculumyear, 1, 4) AS INTEGER) <= %s
                ORDER BY c.curriculumyear DESC
                LIMIT 1
            """, (p, entry_start))
            if row:
                return jsonify({'curriculum': row[0]['curriculumyear']})

        # Fallback: return latest curriculum for the program
        row = query_db("""
            SELECT c.curriculumyear FROM curriculum c
            WHERE c.programcode = %s ORDER BY c.curriculumyear DESC LIMIT 1
        """, (p,))
        return jsonify({'curriculum': row[0]['curriculumyear'] if row else ''})
    except:
        return jsonify({'curriculum': ''}), 500

@app.route('/api/sections-by-program')
def api_sections_by_program():
    program    = request.args.get('program', '')
    year_level = request.args.get('yearLevel', '')
    ay         = request.args.get('ay', '')
    semester   = request.args.get('semester', '')
    try:
        # Self-heal: ensure this program's program_yearlevel row (and a default section)
        # exists and is active for the current AY before querying. Without this, a program
        # whose program_yearlevel row is missing/stale silently returns zero sections here
        # until an admin deactivates+reactivates the whole Program (which happens to cascade
        # this same repair) — this makes that workaround unnecessary.
        if program:
            _heal_conn = get_db_connection()
            _heal_cur  = _heal_conn.cursor(cursor_factory=RealDictCursor)
            try:
                _auto_setup_program_yearlevels(_heal_cur, prog_filter=program)

                # Second repair: _auto_setup_program_yearlevels only auto-creates a default
                # section when a program_yearlevel row has NONE at all. It doesn't help when
                # sections already exist for this exact (program, year level, AY) but were all
                # left isactive=FALSE by an earlier cascade (e.g. a program-wide deactivate that
                # touched every academic year, not just the one being reactivated) — that combo
                # is otherwise permanently invisible here even though the program/year level are
                # active. If the specific (program, yearLevel, ay) being requested has an active
                # program_yearlevel row but zero active sections, reactivate its existing ones.
                if year_level and ay:
                    _heal_cur.execute("""
                        SELECT pyl.programyearlevelid
                        FROM program_yearlevel pyl
                        WHERE UPPER(pyl.programcode) = UPPER(%s)
                          AND pyl.yearlevel = %s AND pyl.academicyearid = %s AND pyl.isactive = TRUE
                    """, (program, int(year_level), ay))
                    _pyl_row = _heal_cur.fetchone()
                    if _pyl_row:
                        _pyl_id = _pyl_row['programyearlevelid']
                        _heal_cur.execute(
                            "SELECT 1 FROM sections WHERE programyearlevelid = %s AND isactive = TRUE LIMIT 1",
                            (_pyl_id,))
                        if not _heal_cur.fetchone():
                            _heal_cur.execute(
                                "UPDATE sections SET isactive = TRUE WHERE programyearlevelid = %s",
                                (_pyl_id,))

                _heal_conn.commit()
            except Exception:
                _heal_conn.rollback()
            finally:
                _heal_cur.close(); _heal_conn.close()

        params = []
        where  = ["sec.isactive = TRUE", "pyl.isactive = TRUE"]

        if program:
            where.append("UPPER(pyl.programcode) = UPPER(%s)")
            params.append(program)
        if year_level:
            where.append("pyl.yearlevel = %s")
            params.append(int(year_level))
        if ay:
            where.append("pyl.academicyearid = %s")
            params.append(ay)

        sql = """
            SELECT DISTINCT sec.sectionid, sec.sectionname,
                pyl.programcode AS offeringcode, pyl.yearlevel
            FROM sections sec
            JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            WHERE {where}
            ORDER BY pyl.programcode, pyl.yearlevel, sec.sectionname
        """.format(where=" AND ".join(where))

        rows = query_db(sql, tuple(params)) if params else query_db(sql)
        return jsonify({'sections': [
            {'id': r['sectionid'], 'name': r['sectionname'],
             'offeringcode': r['offeringcode'], 'yearlevel': r['yearlevel']}
            for r in (rows or [])
        ]})
    except Exception as e:
        return jsonify({'sections': [], 'error': str(e)}), 500

# --- STARTUP MIGRATIONS ---
def _backfill_historical_empnums(cur):
    """One-time backfill: resolve instructor names in existing historical_data rows
    to employee numbers so the DSS can use ID-based matching going forward."""
    try:
        cur.execute("""
            SELECT DISTINCT TRIM("Instructor") AS inst_raw
            FROM historical_data
            WHERE "Instructor" IS NOT NULL AND TRIM("Instructor") != ''
              AND employeenumber IS NULL
        """)
        unresolved = [r['inst_raw'] for r in (cur.fetchall() or [])]
        resolved = 0
        for inst_raw in unresolved:
            emp_num = _resolve_hist_empnum(cur, inst_raw)
            if emp_num:
                cur.execute("""
                    UPDATE historical_data
                    SET employeenumber = %s
                    WHERE TRIM("Instructor") = %s AND employeenumber IS NULL
                """, (emp_num, inst_raw))
                resolved += 1
        if resolved:
            print(f'[startup] backfilled employeenumber for {resolved} instructor(s) in historical_data')
    except Exception as _be:
        print(f'[startup] historical_data empnum backfill error: {_be}')


def _run_startup_migrations():
    try:
        _c = get_db_connection()
        _cur = _c.cursor(cursor_factory=RealDictCursor)
        _cur.execute("DROP VIEW IF EXISTS public.curriculum_view")
        _cur.execute("""
            CREATE VIEW public.curriculum_view AS
            SELECT
                c.curriculumcode,
                c.curriculumid,
                cs.curriculumsubjectid,
                cs.subjectcode,
                cs.prerequisite          AS "Prerequisite",
                cs.corequisite           AS "Co-requisite",
                cs.subjectname,
                cs.lecturehours,
                cs.laboratoryhours,
                cs.creditunits,
                cs.tuitionhours,
                (c.programcode || '-' || cs.yearlevel::text) AS programyearlevel,
                cs.yearlevel,
                cs.semester
            FROM curriculumsubject cs
            INNER JOIN curriculum c ON cs.curriculumid = c.curriculumid
        """)
        _cur.execute("""
            ALTER TABLE program_yearlevel
            ADD COLUMN IF NOT EXISTS section_naming_format VARCHAR(30)
        """)
        _cur.execute("""
            ALTER TABLE historical_data
            ADD COLUMN IF NOT EXISTS employeenumber VARCHAR(50)
        """)
        _auto_setup_program_yearlevels(_cur)
        _c.commit()
        _backfill_historical_empnums(_cur)
        _c.commit()
        _cur.close(); _c.close()
    except Exception as _e:
        print(f'[startup migration] {_e}')

_run_startup_migrations()

# --- MAIN EXECUTION ---
if __name__ == '__main__':
   app.run(debug=True, use_reloader=False)
   
