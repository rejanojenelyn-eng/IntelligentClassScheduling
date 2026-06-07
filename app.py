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

# ─────────────────────────────────────────────────────────────
#  RANDOM FOREST DSS  (Manual Scheduling — see rf_dss.py)
# ─────────────────────────────────────────────────────────────
from rf_dss import SKLEARN_OK as _SKLEARN_OK, train_rf_dss as _train_rf_dss
from rf_dss import rf_score_faculty as _rf_score_faculty, rf_score_room as _rf_score_room


# 1. INITIALIZE APP FIRST
app = Flask(__name__)
app.secret_key = 'pup_lopez_super_secret_key' # Required for Login Sessions

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

    today_day = datetime.now().strftime('%A')
    cur.execute("""
        SELECT ss.*, sub.subjectcode, sub.subjectname, r.roomname,
               TO_CHAR(ts_s.timevalue, 'HH12:MI AM') as start_time,
               TO_CHAR(ts_e.timevalue, 'HH12:MI AM') as end_time
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        JOIN subject sub ON cs.subjectcode = sub.subjectcode
        LEFT JOIN room r ON ss.roomid = r.roomid
        LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
        LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
        WHERE sc.employeenumber = %s AND sv.status = 'Published'
          AND ss.daydesc = %s
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
                           total_units=total_units, total_subjects=total_subjects)

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
    
    try:
        f_data = query_db("SELECT COUNT(*) as t FROM Faculty", one=True)
        r_data = query_db("SELECT COUNT(*) as t FROM Room", one=True)
        s_data = query_db("SELECT COUNT(*) as t FROM Subject", one=True)

        return render_template('academic/dashboard.html', 
                               faculty_count=f_data['t'] if f_data else 0,
                               room_count=r_data['t'] if r_data else 0,
                               course_count=s_data['t'] if s_data else 0)
    except Exception as e:
        print(f"Dashboard Database Error: {e}")
        return render_template('academic/dashboard.html', faculty_count=0, room_count=0, course_count=0)

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
        currs_raw = query_db("SELECT c.*, p.ProgramName FROM Curriculum c JOIN Programs p ON c.ProgramCode = p.ProgramCode ORDER BY p.ProgramName ASC, c.CurriculumYear DESC")
    elif selected_program:
        currs_raw = query_db("SELECT * FROM Curriculum WHERE ProgramCode = %s ORDER BY CurriculumYear DESC", (selected_program,))

    curriculums = [{k.lower(): v for k, v in row.items()} for row in currs_raw] if currs_raw else []
    
    today = date.today()
    cohorts_raw = query_db("""
        SELECT c.CohortID, c.ProgramCode, p.ProgramName, c.CurriculumID, curr.CurriculumCode, 
               c.StartAcademicYear, c.NumberOfSections,
               (CAST(SUBSTRING(ay.AcademicYearID, 3, 2) AS INT) - CAST(SUBSTRING(c.StartAcademicYear, 3, 2) AS INT)) + 1 as year_level
        FROM Cohort c 
        JOIN Curriculum curr ON c.CurriculumID = curr.CurriculumID
        JOIN Programs p ON c.ProgramCode = p.ProgramCode 
        CROSS JOIN (
            (SELECT AcademicYearID FROM Semester WHERE %s BETWEEN SemStartDate AND SemEndDate LIMIT 1)
            UNION ALL 
            (SELECT AcademicYearID FROM AcademicYear ORDER BY YearStart DESC LIMIT 1)
            LIMIT 1
        ) ay
        WHERE 
            ((CAST(SUBSTRING(ay.AcademicYearID, 3, 2) AS INT) - CAST(SUBSTRING(c.StartAcademicYear, 3, 2) AS INT)) + 1) >= 1
            AND 
            ((CAST(SUBSTRING(ay.AcademicYearID, 3, 2) AS INT) - CAST(SUBSTRING(c.StartAcademicYear, 3, 2) AS INT)) + 1) <= p.NumYearLevel
        ORDER BY c.StartAcademicYear DESC, p.ProgramName ASC
    """, (today,))

    cohorts = [{k.lower(): v for k, v in row.items()} for row in cohorts_raw] if cohorts_raw else []

    return render_template('academic/curriculum.html', programs=programs, curriculums=curriculums, selected_program=selected_program, cohorts=cohorts)

@app.route('/curriculum/view/<int:curriculum_id>')
def view_curriculum(curriculum_id):
    if 'loggedin' not in session: return redirect(url_for('login'))
    
    y_lvl = request.args.get('year', '0') 
    sem = request.args.get('semester', 'All')

    info_sql = "SELECT c.*, p.ProgramName, p.ProgramCode, p.NumYearLevel FROM Curriculum c JOIN Programs p ON c.ProgramCode = p.ProgramCode WHERE c.CurriculumID = %s"
    info_raw = query_db(info_sql, (curriculum_id,), one=True)
    if not info_raw: return redirect(url_for('curriculum'))
    info = {k.lower(): v for k, v in info_raw.items()}

    raw_progs = query_db("SELECT * FROM Programs WHERE IsActive = TRUE ORDER BY ProgramName")
    programs =[{k.lower(): v for k, v in row.items()} for row in raw_progs] if raw_progs else[]
    
    other_sql = "SELECT CurriculumID, CurriculumYear, CurriculumCode FROM Curriculum WHERE ProgramCode = %s ORDER BY CurriculumYear DESC"
    other_currs = query_db(other_sql, (info['programcode'],))

    main_sql = "SELECT cv.SubjectCode as subjectcode, cv.\"Prerequisite\" as prerequisite, cv.\"Co-requisite\" as corequisite, s.SubjectName as subjectname, cv.LectureHours as lecturehours, cv.LaboratoryHours as laboratoryhours, cv.CreditUnits as creditunits, cv.TuitionHours as tuitionhours, cv.Semester as semester, CAST(SUBSTRING(cv.ProgramYearLevel FROM '-(.*)') AS INT) as yearlevel FROM Curriculum_View cv LEFT JOIN Subject s ON cv.SubjectCode = s.SubjectCode WHERE cv.CurriculumID = %s"
    
    params =[curriculum_id]
    if y_lvl != '0':
        main_sql += " AND CAST(SUBSTRING(cv.ProgramYearLevel FROM '-(.*)') AS INT) = %s"
        params.append(int(y_lvl))
    if sem != 'All':
        main_sql += " AND cv.Semester = %s"
        params.append(sem)

    main_sql += " ORDER BY yearlevel ASC, cv.Semester ASC"
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
            p.programname,
            p.programcode,
            sec.yearlevel,
            ay.academicyearid,
            sem.semestertype,
            MAX(s.datecreated) AS datecreated
        FROM schedule s
        JOIN sections sec    ON s.sectionid          = sec.sectionid
        JOIN cohort c        ON sec.cohortid          = c.cohortid
        JOIN programs p      ON c.programcode         = p.programcode
        JOIN semester sem    ON s.semesterid          = sem.semesterid
        JOIN academicyear ay ON sem.academicyearid    = ay.academicyearid
        GROUP BY p.programname, p.programcode, sec.yearlevel, ay.academicyearid, sem.semestertype
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
            JOIN schedule sc          ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs  ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub           ON cs.subjectcode = sub.subjectcode
            JOIN sections sec          ON sc.sectionid = sec.sectionid
            JOIN cohort co             ON sec.cohortid = co.cohortid
            LEFT JOIN faculty f        ON sc.employeenumber = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid = sv.versionid
            LEFT JOIN room r           ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s    ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e    ON ss.endtimeid   = ts_e.timeid
            WHERE {status_clause}
              AND UPPER(co.programcode) = UPPER(%s)
              AND sec.yearlevel = %s
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
            JOIN cohort co ON sec.cohortid=co.cohortid
            LEFT JOIN schedule_sessions ss ON ss.versionid=sv.versionid
            WHERE UPPER(co.programcode)=UPPER(%s) AND sec.yearlevel=%s
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
                co.programcode   AS "Program",
                sec.yearlevel    AS "YearLevel",
                (COALESCE(sub.lecturehours,0) + COALESCE(sub.laboratoryhours,0)) AS "Hours",
                ss.daydesc       AS "Day/s",
                TO_CHAR(ts_s.timevalue,'HH12:MI AM') || ' - ' || TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS "Time",
                COALESCE(r.roomname, 'TBA') AS "Room"
            FROM schedule_version sv
            JOIN schedule sc           ON sv.scheduleid = sc.scheduleid AND sv.status = 'Published'
            JOIN curriculumsubject cs   ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub            ON cs.subjectcode = sub.subjectcode
            JOIN sections sec           ON sc.sectionid = sec.sectionid
            JOIN cohort co              ON sec.cohortid = co.cohortid
            LEFT JOIN faculty f         ON sc.employeenumber = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid = sv.versionid
            LEFT JOIN room r            ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s     ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e     ON ss.endtimeid   = ts_e.timeid
        """
        if prog: norm_filters.append("UPPER(co.programcode) = UPPER(%s)"); norm_params.append(prog)
        if yl:   norm_filters.append("sec.yearlevel = %s");                 norm_params.append(int(yl))
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
        nf.append(f"UPPER(co.programcode) IN ({','.join(['%s']*len(programs))})"); np_.extend([p.upper() for p in programs])
    if year_levels:
        nf.append(f"sec.yearlevel IN ({','.join(['%s']*len(year_levels))})"); np_.extend(year_levels)

    norm_q = """
        SELECT
            COALESCE(f.lastname||', '||f.firstname||COALESCE(' '||f.middlename,''),'TBA') AS "Instructor",
            sub.subjectcode   AS "SubjectCode",
            sub.subjectname   AS "SubjectName",
            COALESCE(sub.lecturehours,0)    AS "LectureHours",
            COALESCE(sub.laboratoryhours,0) AS "LaboratoryHours",
            COALESCE(sub.creditunits,0)     AS "CreditUnits",
            co.programcode   AS "Program",
            sec.yearlevel    AS "YearLevel",
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
        JOIN cohort co             ON sec.cohortid=co.cohortid
        JOIN semester sem          ON sc.semesterid=sem.semesterid
        JOIN academicyear ay       ON sem.academicyearid=ay.academicyearid
        LEFT JOIN faculty f        ON sc.employeenumber=f.employeenumber
        LEFT JOIN schedule_sessions ss ON ss.versionid=sv.versionid
        LEFT JOIN room r           ON ss.roomid=r.roomid
        LEFT JOIN timeslot ts_s    ON ss.starttimeid=ts_s.timeid
        LEFT JOIN timeslot ts_e    ON ss.endtimeid=ts_e.timeid
        WHERE """ + " AND ".join(nf) + """
        ORDER BY co.programcode, sec.yearlevel, sub.subjectcode, ss.daydesc NULLS LAST
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
                        WHERE UPPER(c.programcode) = UPPER(%s) AND UPPER(cs.subjectcode) = UPPER(%s)
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
                        JOIN cohort co ON sec.cohortid = co.cohortid
                        WHERE UPPER(co.programcode) = UPPER(%s) AND sec.yearlevel = %s AND sec.isactive = TRUE
                        ORDER BY sec.sectionname LIMIT 1
                    """, (prog, yl))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                if not sec_id and prog and yl:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN cohort co ON sec.cohortid = co.cohortid
                        WHERE UPPER(co.programcode) = UPPER(%s) AND sec.yearlevel = %s
                        ORDER BY sec.sectionname LIMIT 1
                    """, (prog, yl))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                if not sec_id and prog:
                    # No section for this year level — use any section for the program
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec
                        JOIN cohort co ON sec.cohortid = co.cohortid
                        WHERE UPPER(co.programcode) = UPPER(%s)
                        ORDER BY sec.yearlevel, sec.sectionname LIMIT 1
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
                    cur.execute("""
                        SELECT cs.curriculumsubjectid FROM curriculumsubject cs
                        JOIN curriculum c ON cs.curriculumid = c.curriculumid
                        WHERE UPPER(c.programcode)=UPPER(%s) AND UPPER(cs.subjectcode)=UPPER(%s) LIMIT 1
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
                cur.execute("""
                    SELECT sec.sectionid FROM sections sec JOIN cohort co ON sec.cohortid=co.cohortid
                    WHERE UPPER(co.programcode)=UPPER(%s) AND sec.yearlevel=%s AND sec.isactive=TRUE
                    ORDER BY sec.sectionname LIMIT 1
                """, (prog, yl))
                r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                if not sec_id:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec JOIN cohort co ON sec.cohortid=co.cohortid
                        WHERE UPPER(co.programcode)=UPPER(%s) AND sec.yearlevel=%s
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
                    cur.execute("""
                        SELECT cs.curriculumsubjectid FROM curriculumsubject cs
                        JOIN curriculum c ON cs.curriculumid=c.curriculumid
                        WHERE UPPER(c.programcode)=UPPER(%s) AND UPPER(cs.subjectcode)=UPPER(%s) LIMIT 1
                    """, (prog, s_code))
                    r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None
                if not cs_id and s_code:
                    cur.execute("SELECT curriculumsubjectid FROM curriculumsubject WHERE UPPER(subjectcode)=UPPER(%s) LIMIT 1", (s_code,))
                    r = cur.fetchone(); cs_id = r['curriculumsubjectid'] if r else None

                sec_id = None
                if prog and yl:
                    cur.execute("""
                        SELECT sec.sectionid FROM sections sec JOIN cohort co ON sec.cohortid=co.cohortid
                        WHERE UPPER(co.programcode)=UPPER(%s) AND sec.yearlevel=%s AND sec.isactive=TRUE
                        ORDER BY sec.sectionname LIMIT 1
                    """, (prog, yl))
                    r = cur.fetchone(); sec_id = r['sectionid'] if r else None
                    if not sec_id:
                        cur.execute("""
                            SELECT sec.sectionid FROM sections sec JOIN cohort co ON sec.cohortid=co.cohortid
                            WHERE UPPER(co.programcode)=UPPER(%s) AND sec.yearlevel=%s
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
                    WHERE UPPER(c.programcode)=UPPER(%s) AND UPPER(cs.subjectcode)=UPPER(%s) LIMIT 1
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
                JOIN cohort co ON sec.cohortid=co.cohortid
                WHERE UPPER(co.programcode)=UPPER(%s) AND sec.yearlevel=%s AND sec.isactive=TRUE
                ORDER BY sec.sectionname LIMIT 1
            """, (prog, yl))
            r = cur.fetchone()
            sec_id   = r['sectionid']   if r else None
            sec_name = r['sectionname'] if r else ''
            if not sec_id:
                cur.execute("""
                    SELECT sec.sectionid, sec.sectionname FROM sections sec
                    JOIN cohort co ON sec.cohortid=co.cohortid
                    WHERE UPPER(co.programcode)=UPPER(%s) AND sec.yearlevel=%s
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
        # Only show academic years that still have at least one semester that hasn't ended yet.
        # NULL SemEndDate is treated as "not ended" so unfinished setup isn't silently hidden.
        cur.execute("""
            SELECT DISTINCT ay.AcademicYearID
            FROM AcademicYear ay
            JOIN Semester s ON s.AcademicYearID = ay.AcademicYearID
            WHERE s.SemEndDate >= %s OR s.SemEndDate IS NULL
            ORDER BY ay.AcademicYearID ASC
        """, (today,))
        acad_years = [row[0] for row in cur.fetchall()]

        # Build per-AY list of available (non-past) semesters for the frontend
        cur.execute("""
            SELECT ay.AcademicYearID, s.SemesterType
            FROM AcademicYear ay
            JOIN Semester s ON s.AcademicYearID = ay.AcademicYearID
            WHERE s.SemEndDate >= %s OR s.SemEndDate IS NULL
            ORDER BY ay.AcademicYearID ASC, s.SemStartDate ASC
        """, (today,))
        _ay_sem_map = {}
        _sem_labels = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}
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
        return render_template('academic/manualScheduleEditor.html',
                               scheduler_mode=scheduler_mode,
                               is_acad_head=is_acad_head,
                               acad_years=acad_years, programs=programs,
                               faculty=faculty, faculty_json=faculty_json,
                               buildings=buildings, rooms_json=json.dumps(rooms_list),
                               available_sems_json=available_sems_json,
                               init_mode=init_mode, init_prog=init_prog,
                               init_yl=init_yl, init_ay=init_ay, init_sem=init_sem)
    finally:
        cur.close()
        conn.close()


@app.route('/schedule/local-editor')
def local_schedule_editor():
    """Backward-compat redirect — all scheduler modes now share one route."""
    return redirect(url_for('manual_schedule_editor', scheduler='local'))


@app.route('/api/get_room_schedule/<int:room_id>')
def get_room_schedule(room_id):
    if 'loggedin' not in session: return jsonify([])
    ay_id    = request.args.get('ay_id')
    semester = request.args.get('semester')
    program  = request.args.get('program')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        filters = ["ss.roomid = %s", "sv.status IN ('Published', 'Draft')"]
        params  = [room_id]
        joins = """ JOIN semester sem ON s.semesterid = sem.semesterid
            LEFT JOIN sections sec ON s.sectionid = sec.sectionid
            LEFT JOIN cohort c ON sec.cohortid = c.cohortid
            JOIN room r ON ss.roomid = r.roomid"""

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
            filters.append("c.programcode = %s")
            params.append(program)

        cur.execute(f"""
            SELECT
                cs.subjectcode,
                subj.subjectname,
                f.lastname || ', ' || f.firstname AS instructor,
                ss.daydesc,
                ss.starttimeid,
                ss.endtimeid,
                cs.yearlevel AS year_level,
                c.programcode,
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
        filters.append("UPPER(co.programcode) = UPPER(%s)")
        params.append(program)

    if year_level:
        filters.append("sec.yearlevel = %s")
        params.append(int(year_level))

    try:
        rows = query_db(f"""
            SELECT sub.subjectcode, sub.subjectname, sub.creditunits,
                   sec.sectionname, sec.yearlevel, co.programcode, ss.daydesc, r.roomname,
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
            LEFT JOIN cohort co ON sec.cohortid = co.cohortid
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
            cur.execute("""
                SELECT c.CurriculumID, c.CurriculumYear, c.CurriculumCode,
                       ARRAY(SELECT DISTINCT cs.YearLevel FROM CurriculumSubject cs
                             WHERE cs.CurriculumID = c.CurriculumID ORDER BY cs.YearLevel) AS year_levels
                FROM Cohort co
                JOIN Curriculum c ON co.CurriculumID = c.CurriculumID
                WHERE co.ProgramCode = %s
                  AND (CAST(SUBSTRING(%s, 3, 2) AS INT) - CAST(SUBSTRING(co.StartAcademicYear, 3, 2) AS INT)) + 1 = %s
                LIMIT 1
            """, (prog, ay_id, int(yl)))
            res = cur.fetchone()

        # 2. Kung sakaling walang nakitang cohort (e.g. hindi pa naka-set up sa Admin), 
        # tsaka lang tayo gagamit ng fallback na kukunin ang pinakabagong Curriculum.
        if not res:
            cur.execute("""
                SELECT c.CurriculumID, c.CurriculumYear, c.CurriculumCode,
                       ARRAY(SELECT DISTINCT cs.YearLevel FROM CurriculumSubject cs
                             WHERE cs.CurriculumID = c.CurriculumID ORDER BY cs.YearLevel) AS year_levels
                FROM Curriculum c
                WHERE c.ProgramCode = %s
                ORDER BY c.CurriculumYear DESC LIMIT 1
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
                JOIN cohort c ON sec.cohortid = c.cohortid
                WHERE c.programcode = %s AND ay.academicyearid = %s
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

        return jsonify({
            "success": True,
            "is_lab": is_lab,
            "faculty": {"recommended": recommended_faculty, "others": others_faculty},
            "rooms":   {"recommended": recommended_rooms,  "others": others_rooms}
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
    finally:
        cur.close(); conn.close()

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
    return jsonify({
        'success': True,
        'lecturehours': lh,
        'laboratoryhours': lab,
        'creditunits': int(row['creditunits'] or 0),
        'total_hours': (lh + lab) if (lh + lab) > 0 else 3.0,
        'is_sunday_allowed': subject_code.upper().startswith(('NSTP', 'OU')),
        'is_sunday_only':    subject_code.upper().startswith('NSTP')
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
    emp_num = request.args.get('emp_num', '').strip()
    ay_id   = request.args.get('ay_id', '').strip()
    sem     = request.args.get('sem', '').strip()
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
                  AND sv.status IN ('Published', 'Draft')
            ) AS d
        """, (emp_num, ay_id, sem), one=True)
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
        filters.append("UPPER(co.programcode) = UPPER(%s)")
        params.append(prog)
    if yl:
        filters.append("sec.yearlevel = %s")
        params.append(int(yl))
    rows = query_db(f"""
        SELECT DISTINCT ss.daydesc
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        JOIN subject sub ON cs.subjectcode = sub.subjectcode
        LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
        LEFT JOIN cohort co ON sec.cohortid = co.cohortid
        WHERE {' AND '.join(filters)}
    """, params or None) or []
    return jsonify({'success': True, 'days': [r['daydesc'] for r in rows]})


@app.route('/api/manual/existing_sessions')
def api_manual_existing_sessions():
    subj  = request.args.get('subject_code', '').strip()
    ay_id = request.args.get('ay_id', '').strip()
    sem   = request.args.get('semester', '').strip()
    prog  = request.args.get('program', '').strip()
    yl    = request.args.get('year_level', '').strip()
    if not subj or not ay_id or not sem:
        return jsonify({'success': True, 'sessions': []})
    base_filters = [
        "UPPER(sub.subjectcode) = UPPER(%s)",
        "sc.semesterid = (SELECT semesterid FROM semester WHERE academicyearid = %s AND semestertype = %s LIMIT 1)"
    ]
    base_params = [subj, ay_id, sem]
    if prog:
        base_filters.append("UPPER(co.programcode) = UPPER(%s)")
        base_params.append(prog)
    if yl:
        base_filters.append("sec.yearlevel = %s")
        base_params.append(int(yl))

    session_query = f"""
        SELECT ss.starttimeid, ss.endtimeid, ss.daydesc,
               sv.versionid,
               sub.subjectcode, sub.subjectname,
               sec.yearlevel, co.programcode,
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
        LEFT JOIN cohort co ON sec.cohortid = co.cohortid
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

    # Draft is a complete replacement of Published for this subject — save_draft archives
    # the old Draft and inserts the full new session set. Never mix: sessions intentionally
    # removed in the Draft would ghost-reappear from Published if we merged by slot key.
    if draft_rows:
        rows = draft_rows
    else:
        rows = query_db(
            session_query.format(status_clause=' AND '.join(base_filters + ["sv.status = 'Published'"])),
            base_params) or []
    return jsonify({'success': True, 'sessions': [dict(r) for r in rows]})


@app.route('/api/manual/section_schedule')
def api_manual_section_schedule():
    """Published + Draft sessions for a section (program + year level).
    Used by the manual editor for section-level conflict checking."""
    if 'loggedin' not in session:
        return jsonify([])
    prog  = request.args.get('program', '').strip()
    yl    = request.args.get('year_level', '').strip()
    ay_id = request.args.get('ay_id', '').strip()
    sem   = request.args.get('semester', '').strip()
    if not prog or not yl or not ay_id or not sem:
        return jsonify([])
    try:
        rows = query_db("""
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
            JOIN sections sec         ON sc.sectionid   = sec.sectionid
            JOIN cohort co            ON sec.cohortid   = co.cohortid
            JOIN curriculumsubject cs  ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject subj         ON cs.subjectcode = subj.subjectcode
            WHERE UPPER(co.programcode) = UPPER(%s)
              AND sec.yearlevel::text   = %s
              AND ay.academicyearid::text = %s
              AND s.semestertype        = %s
              AND sv.status IN ('Published', 'Draft')
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
    curr_id = request.args.get('curriculum_id')
    yl      = request.args.get('year_level')
    sem     = request.args.get('semester')
    ay_id   = request.args.get('ay_id', '').strip()
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
                sched_rows = query_db("""
                    WITH sem_cte AS (
                        SELECT semesterid FROM semester
                        WHERE  academicyearid = %s AND semestertype = %s LIMIT 1
                    ),
                    prog_cte AS (
                        SELECT UPPER(programcode) AS programcode
                        FROM   curriculum WHERE curriculumid = %s LIMIT 1
                    ),
                    max_version AS (
                        SELECT UPPER(sub2.subjectcode) AS subjectcode,
                               MAX(sv2.version_number)  AS max_v
                        FROM   schedule_version sv2
                        JOIN   schedule sc2          ON sv2.scheduleid          = sc2.scheduleid
                        JOIN   curriculumsubject cs2 ON sc2.curriculumsubjectid = cs2.curriculumsubjectid
                        JOIN   subject sub2          ON cs2.subjectcode         = sub2.subjectcode
                        JOIN   sections sec2         ON sc2.sectionid           = sec2.sectionid
                        JOIN   cohort co2            ON sec2.cohortid           = co2.cohortid
                        JOIN   sem_cte               ON sc2.semesterid          = sem_cte.semesterid
                        WHERE  sv2.status IN ('Draft', 'Published')
                          AND  sec2.yearlevel                = %s
                          AND  UPPER(co2.programcode) = (SELECT programcode FROM prog_cte)
                        GROUP BY UPPER(sub2.subjectcode)
                    )
                    SELECT mv.subjectcode,
                           COALESCE(SUM((ss.endtimeid - ss.starttimeid) * 0.5), 0) AS scheduled_hours,
                           CASE WHEN bool_or(sv.status = 'Published') THEN 'Published'
                                WHEN bool_or(sv.status = 'Draft')     THEN 'Draft'
                                ELSE NULL END AS saved_status
                    FROM   max_version mv
                    JOIN   curriculumsubject cs2 ON UPPER(cs2.subjectcode) = mv.subjectcode
                    JOIN   schedule sc           ON sc.curriculumsubjectid = cs2.curriculumsubjectid
                    JOIN   sections sec          ON sc.sectionid           = sec.sectionid
                    JOIN   cohort co             ON sec.cohortid           = co.cohortid
                    JOIN   sem_cte               ON sc.semesterid          = sem_cte.semesterid
                    JOIN   schedule_version sv   ON sv.scheduleid          = sc.scheduleid
                                                AND sv.version_number      = mv.max_v
                                                AND sv.status IN ('Draft', 'Published')
                    JOIN   schedule_sessions ss  ON ss.versionid           = sv.versionid
                    WHERE  sec.yearlevel                = %s
                      AND  UPPER(co.programcode) = (SELECT programcode FROM prog_cte)
                    GROUP BY mv.subjectcode
                """, (ay_id, sem, curr_id, int(yl), int(yl))) or []
                for r in sched_rows:
                    sched_map[r['subjectcode']] = {
                        'hours':  float(r['scheduled_hours'] or 0),
                        'status': r['saved_status'] or None,
                    }

                # Detect subjects with BOTH Published and Draft versions (different version numbers)
                # These get a combined 'Published/Draft' badge in the UI.
                dual_rows = query_db("""
                    WITH sem_cte AS (
                        SELECT semesterid FROM semester
                        WHERE academicyearid = %s AND semestertype = %s LIMIT 1
                    ),
                    prog_cte AS (
                        SELECT UPPER(programcode) AS programcode
                        FROM curriculum WHERE curriculumid = %s LIMIT 1
                    )
                    SELECT UPPER(sub.subjectcode) AS subjectcode
                    FROM   schedule_version sv
                    JOIN   schedule sc    ON sv.scheduleid          = sc.scheduleid
                    JOIN   curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                    JOIN   subject sub    ON cs.subjectcode         = sub.subjectcode
                    JOIN   sections sec   ON sc.sectionid           = sec.sectionid
                    JOIN   cohort co      ON sec.cohortid           = co.cohortid
                    JOIN   sem_cte        ON sc.semesterid          = sem_cte.semesterid
                    WHERE  sv.status IN ('Draft', 'Published')
                      AND  sec.yearlevel                = %s
                      AND  UPPER(co.programcode) = (SELECT programcode FROM prog_cte)
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
    curricula       = query_db("SELECT curriculumid, curriculumcode, curriculumyear, programcode FROM curriculum ORDER BY programcode, curriculumyear DESC")
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
    try:
        now = datetime.now().strftime("%B %d, %Y")
        u_data = query_db("SELECT COUNT(*) as t FROM Accounts", one=True)
        f_data = query_db("SELECT COUNT(*) as t FROM Faculty", one=True)
        
        return render_template('admin/dashboard_admin.html', 
                               current_date=now,
                               user_count=u_data['t'] if u_data else 0,
                               total=f_data['t'] if f_data else 0)
    except Exception as e:
        return render_template('admin/dashboard_admin.html', user_count=0, total=0, current_date="N/A")

# ── Employee DOCX Export ──────────────────────────────────────────────────────
def _build_employee_docx():
    """Generate a DOCX file from POSTed employee JSON and return a Flask Response."""
    data = request.get_json(silent=True) or {}
    employees = data.get('employees', [])
    title     = data.get('title', 'Employee Records')
    timestamp = data.get('timestamp', '')

    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = Inches(0.9); sec.right_margin = Inches(0.9)
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

    has_contact = any(str(e.get('contact', '')).strip() for e in employees)
    headers = ['#', 'Employee Number', 'Employee Name', 'Specialization', 'Email']
    if has_contact: headers.append('Contact')
    headers += ['Employment Type', 'Status']

    table = doc.add_table(rows=1, cols=len(headers))
    table.style = 'Table Grid'

    hdr = table.rows[0]
    for i, h_text in enumerate(headers):
        cell = hdr.cells[i]
        cell.text = h_text
        for para in cell.paragraphs:
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in para.runs:
                run.bold = True; run.font.size = Pt(9)
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        tc = cell._tc; tcp = tc.get_or_add_tcPr()
        shd = OxmlElement('w:shd')
        shd.set(qn('w:fill'), '800000'); shd.set(qn('w:color'), 'auto'); shd.set(qn('w:val'), 'clear')
        tcp.append(shd)

    for idx, emp in enumerate(employees):
        row = table.add_row()
        vals = [str(idx + 1), emp.get('emp_num', ''), emp.get('name', ''),
                emp.get('spec', ''), emp.get('email', '')]
        if has_contact: vals.append(emp.get('contact', ''))
        vals += [emp.get('type', ''), emp.get('status', '')]
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
        headers={'Content-Disposition': 'attachment; filename="employees.docx"'}
    )

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
        LEFT JOIN Curriculum c ON c.ProgramCode = p.ProgramCode
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
        SELECT c.CurriculumID as curriculum_id, c.CurriculumCode as curriculum_code,
               c.CurriculumYear as curriculum_year, p.ProgramName as program_name,
               p.ProgramCode as program_code
        FROM Curriculum c
        JOIN Programs p ON c.ProgramCode = p.ProgramCode
        WHERE c.CurriculumID = ANY(%s)
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
        # 1. AUTOMATION: Find the AY based on today's date
        today = date.today()
        cur.execute("""
            (SELECT AcademicYearID FROM Semester WHERE %s BETWEEN SemStartDate AND SemEndDate LIMIT 1)
            UNION ALL 
            (SELECT AcademicYearID FROM AcademicYear ORDER BY YearStart DESC LIMIT 1)
            LIMIT 1
        """, (today,))
        
        active_res = cur.fetchone()

        if active_res:
            ay_id = active_res[0] 
            base_year_int = int(ay_id[2:4]) 

            # 2. Logic: Assign the latest curriculum that is NOT newer than the student's entry year
            cur.execute("""
                INSERT INTO Cohort (ProgramCode, CurriculumID, StartAcademicYear, NumberOfSections)
                SELECT p.ProgramCode,
                    (
                        SELECT c.CurriculumID FROM Curriculum c 
                        WHERE c.ProgramCode = p.ProgramCode 
                        AND CAST(SUBSTRING(c.CurriculumYear, 1, 4) AS INT) <= (2000 + %s - gs.n)
                        ORDER BY c.CurriculumYear DESC LIMIT 1
                    ),
                    'AY' || LPAD((%s - gs.n)::text, 2, '0') || LPAD((%s - gs.n + 1)::text, 2, '0'), 1
                FROM Programs p 
                JOIN LATERAL generate_series(0, p.NumYearLevel - 1) AS gs(n) ON TRUE
                WHERE p.IsActive = TRUE
                AND EXISTS (SELECT 1 FROM Curriculum c WHERE c.ProgramCode = p.ProgramCode)
                ON CONFLICT (ProgramCode, StartAcademicYear) 
                DO UPDATE SET CurriculumID = EXCLUDED.CurriculumID
            """, (base_year_int, base_year_int, base_year_int))

            # 3. Sync Sections
            cur.execute("""
                INSERT INTO Sections (CohortID, YearLevel, SectionName)
                SELECT c.CohortID, (CAST(SUBSTRING(%s, 3, 2) AS INT) - CAST(SUBSTRING(c.StartAcademicYear, 3, 2) AS INT)) + 1, CHR(64 + gs.num)
                FROM Cohort c JOIN Programs p ON c.ProgramCode = p.ProgramCode JOIN LATERAL generate_series(1, c.NumberOfSections) AS gs(num) ON TRUE
                WHERE (CAST(SUBSTRING(%s, 3, 2) AS INT) - CAST(SUBSTRING(c.StartAcademicYear, 3, 2) AS INT)) + 1 BETWEEN 1 AND p.NumYearLevel
                ON CONFLICT (CohortID, YearLevel, SectionName) DO NOTHING
            """, (ay_id, ay_id))
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
        currs_raw = query_db("SELECT c.*, p.ProgramName FROM Curriculum c JOIN Programs p ON c.ProgramCode = p.ProgramCode ORDER BY p.ProgramName ASC, c.CurriculumYear DESC")
    elif selected_program:
        currs_raw = query_db("SELECT * FROM Curriculum WHERE ProgramCode = %s ORDER BY CurriculumYear DESC", (selected_program,))
    curriculums = [{k.lower(): v for k, v in row.items()} for row in currs_raw] if currs_raw else []

    unique_codes_raw = query_db("SELECT DISTINCT CurriculumCode FROM Curriculum ORDER BY CurriculumCode DESC")
    unique_codes = [{k.lower(): v for k, v in row.items()} for row in unique_codes_raw] if unique_codes_raw else []

    all_curriculums_raw = query_db("SELECT * FROM Curriculum ORDER BY CurriculumYear DESC")
    all_curriculums = [{k.lower(): v for k, v in row.items()} for row in all_curriculums_raw] if all_curriculums_raw else []

    # Get cohorts based on automatic year detection for the table display
    today = date.today()
    cohorts_raw = query_db("""
        SELECT c.CohortID, c.ProgramCode, p.ProgramName, c.CurriculumID, curr.CurriculumCode, 
               c.StartAcademicYear, c.NumberOfSections,
               (CAST(SUBSTRING(ay.AcademicYearID, 3, 2) AS INT) - CAST(SUBSTRING(c.StartAcademicYear, 3, 2) AS INT)) + 1 as year_level
        FROM Cohort c 
        JOIN Curriculum curr ON c.CurriculumID = curr.CurriculumID
        JOIN Programs p ON c.ProgramCode = p.ProgramCode 
        CROSS JOIN (
            (SELECT AcademicYearID FROM Semester WHERE %s BETWEEN SemStartDate AND SemEndDate LIMIT 1)
            UNION ALL 
            (SELECT AcademicYearID FROM AcademicYear ORDER BY YearStart DESC LIMIT 1)
            LIMIT 1
        ) ay
        -- THIS LINE ENFORCES YOUR RULES (3yr for Diploma, 4yr for Degree, 5yr for Arch)
        WHERE 
            ((CAST(SUBSTRING(ay.AcademicYearID, 3, 2) AS INT) - CAST(SUBSTRING(c.StartAcademicYear, 3, 2) AS INT)) + 1) >= 1
            AND 
            ((CAST(SUBSTRING(ay.AcademicYearID, 3, 2) AS INT) - CAST(SUBSTRING(c.StartAcademicYear, 3, 2) AS INT)) + 1) <= p.NumYearLevel
        ORDER BY c.StartAcademicYear DESC, p.ProgramName ASC
    """, (today,))

    cohorts = [{k.lower(): v for k, v in row.items()} for row in cohorts_raw] if cohorts_raw else []

    return render_template('admin/curriculum_admin.html', programs=programs, curriculums=curriculums, 
                           selected_program=selected_program, cohorts=cohorts, 
                           unique_codes=unique_codes, all_curriculums=all_curriculums)

@app.route('/admin/export/assignments')
def export_assignments():
    if session.get('role') not in['Admin', 'Academic Head']: return redirect(url_for('login'))
    
    data = query_db("""
        SELECT curr.CurriculumCode, p.ProgramName, 
            (CAST(SUBSTRING(ay.AcademicYearID, 3, 2) AS INT) - CAST(SUBSTRING(c.StartAcademicYear, 3, 2) AS INT)) + 1 as year_level,
            c.NumberOfSections, c.StartAcademicYear
        FROM Cohort c
        JOIN Curriculum curr ON c.CurriculumID = curr.CurriculumID
        JOIN Programs p ON c.ProgramCode = p.ProgramCode
        CROSS JOIN (SELECT AcademicYearID FROM AcademicYear WHERE IsActive = TRUE LIMIT 1) ay
        ORDER BY c.StartAcademicYear DESC, p.ProgramName ASC
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
        SELECT p.ProgramName, c.CurriculumYear, CAST(SUBSTRING(cv.ProgramYearLevel FROM '-(.*)') AS INT) as yearlevel, 
            cv.Semester, cv.SubjectCode, cv."Prerequisite", cv."Co-requisite", cv."Description", 
            cv.LectureHours, cv.LaboratoryHours, cv.CreditUnits, cv.TuitionHours
        FROM Curriculum_View cv JOIN Curriculum c ON cv.CurriculumID = c.CurriculumID
        JOIN Programs p ON c.ProgramCode = p.ProgramCode
    """
    params =[]
    if program_code != 'All':
        query += " WHERE c.ProgramCode = %s"
        params.append(program_code)

    query += " ORDER BY p.ProgramName ASC, c.CurriculumYear DESC, yearlevel ASC, cv.Semester ASC"
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
        cur.execute("SELECT 1 FROM Curriculum WHERE ProgramCode = %s AND CurriculumYear = %s", (prog_code, curr_year))
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
            INSERT INTO Curriculum (CurriculumCode, ProgramCode, CurriculumYear) 
            VALUES (%s, %s, %s) RETURNING CurriculumID
        """, (curr_code_str, prog_code, curr_year))
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
                ON CONFLICT (CurriculumID, SubjectCode) DO NOTHING
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
        cur.execute("SELECT curriculumid FROM Curriculum WHERE ProgramCode = %s AND CurriculumYear = %s", (prog_code, curr_year))
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
            years = curr_year.split('-')
            curr_code = f"CY{years[0][-2:]}{years[1][-2:]}" if len(years) == 2 else "CY0000"
            cur.execute("""
                INSERT INTO Curriculum (CurriculumCode, ProgramCode, CurriculumYear)
                VALUES (%s, %s, %s) RETURNING CurriculumID
            """, (curr_code, prog_code, curr_year))
            curr_id = cur.fetchone()[0]

        for s in subjects:
            sc = str(s.get('sc', '')).strip()
            if not sc: continue
            sn = str(s.get('sn', sc)).strip() or sc
            cur.execute("""
                INSERT INTO Subject (SubjectCode, SubjectName, CreditUnits, LectureHours, LaboratoryHours, TuitionHours)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (SubjectCode) DO UPDATE SET
                    SubjectName = EXCLUDED.SubjectName,
                    CreditUnits = EXCLUDED.CreditUnits,
                    TuitionHours = EXCLUDED.TuitionHours
            """, (sc, sn, parse_int(s.get('u')), parse_int(s.get('lc')), parse_int(s.get('lb')), parse_int(s.get('th'))))

        for s in subjects:
            sc = str(s.get('sc', '')).strip()
            if not sc: continue
            yl  = parse_int(s.get('yl')) or 1
            sem = norm_sem(s.get('sem', 'A'))
            cur.execute("""
                INSERT INTO CurriculumSubject (CurriculumID, SubjectCode, YearLevel, Semester)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (CurriculumID, SubjectCode) DO NOTHING
            """, (curr_id, sc, yl, sem))

            pre = str(s.get('pre', '')).strip()
            co  = str(s.get('co',  '')).strip()
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
        cur.execute(
            "SELECT 1 FROM Curriculum WHERE ProgramCode = %s AND CurriculumYear = %s",
            (prog_code, curr_year)
        )
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

        csv_cc = get_val(data_rows[0], 'cc') if data_rows and 'cc' in idx else None
        if csv_cc:
            curr_code_str = csv_cc[:6]
        else:
            years = curr_year.split('-')
            curr_code_str = f"CY{years[0][-2:]}{years[1][-2:]}" if len(years) == 2 else "CY0000"

        cur.execute(
            "INSERT INTO Curriculum (CurriculumCode, ProgramCode, CurriculumYear) "
            "VALUES (%s, %s, %s) RETURNING CurriculumID",
            (curr_code_str, prog_code, curr_year)
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
                ON CONFLICT (CurriculumID, SubjectCode) DO NOTHING
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
        cur.execute(
            "SELECT curriculumid FROM Curriculum WHERE ProgramCode = %s AND CurriculumYear = %s",
            (prog_code, curr_year)
        )
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
        # 4a. Unlink cohorts assigned to this curriculum (preserve cohort, just clear the link)
        cur.execute("UPDATE cohort SET curriculumid = NULL WHERE curriculumid = %s", (curriculum_id,))

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
    
    cohort_id = request.form.get('cohort_id')
    curr_id = request.form.get('curriculum_id') 
    sections_count = request.form.get('number_of_sections')

    if not curr_id or curr_id == "":
        flash("Error: No curriculum version selected.")
        return redirect(url_for('admin_curriculum'))

    conn = get_db_connection(); cur = conn.cursor()
    try:
        if cohort_id and cohort_id.strip() != "":
            cur.execute("""
                UPDATE Cohort 
                SET CurriculumID = %s, NumberOfSections = %s 
                WHERE CohortID = %s
            """, (int(curr_id), int(sections_count), int(cohort_id)))
            flash("Assignment updated successfully.")
        else:
            prog_code = request.form.get('program_code')
            start_year = request.form.get('start_academic_year')
            cur.execute("""
                INSERT INTO Cohort (ProgramCode, CurriculumID, StartAcademicYear, NumberOfSections)
                VALUES (%s, %s, %s, %s)
            """, (prog_code, int(curr_id), start_year, int(sections_count)))
            flash("New assignment created.")
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        flash(f"Database Error: {str(e)}")
    finally:
        cur.close(); conn.close()
        
    return redirect(url_for('admin_curriculum'))

# --- ORIGINAL AUTOGENERATE LOGIC FULLY RESTORED ---
@app.route('/admin/curriculum/autogenerate', methods=['POST'])
def autogenerate_assignments():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    conn = get_db_connection(); cur = conn.cursor()

    try:
        cur.execute("SELECT AcademicYearID, YearStart FROM AcademicYear WHERE IsActive = TRUE LIMIT 1")
        active_ay = cur.fetchone()
        if not active_ay:
            flash("Error: No Active Academic Year found.")
            return redirect(url_for('admin_curriculum'))

        ay_start = int(active_ay['yearstart'] if isinstance(active_ay, dict) else active_ay[1])
        cur.execute("SELECT ProgramCode, ProgramDuration FROM Programs WHERE IsActive = TRUE")
        programs = cur.fetchall()

        for prog in programs:
            p_code = prog['programcode'] if isinstance(prog, dict) else prog[0]
            p_duration = int(prog['programduration'] if isinstance(prog, dict) else prog[1])

            for yr in range(1, p_duration + 1):
                target_start_year_int = ay_start - (yr - 1)
                target_ay_string = f"AY{str(target_start_year_int)[2:]}{str(target_start_year_int + 1)[2:]}"

                # Find the Best Matching Curriculum for this specific year
                cur.execute("""
                    SELECT CurriculumID FROM Curriculum 
                    WHERE ProgramCode = %s AND CAST(SUBSTRING(CurriculumYear, 1, 4) AS INT) <= %s
                    ORDER BY CurriculumYear DESC LIMIT 1
                """, (p_code, target_start_year_int))
                best_curr = cur.fetchone()
                
                if best_curr:
                    curr_id = best_curr['curriculumid'] if isinstance(best_curr, dict) else best_curr[0]
                    cur.execute("""
                        INSERT INTO Cohort (ProgramCode, CurriculumID, StartAcademicYear, NumberOfSections)
                        VALUES (%s, %s, %s, 1)
                        ON CONFLICT (ProgramCode, StartAcademicYear) 
                        DO UPDATE SET CurriculumID = EXCLUDED.CurriculumID
                        RETURNING CohortID
                    """, (p_code, curr_id, target_ay_string))
                    res = cur.fetchone()
                    target_cohort_id = res['cohortid'] if isinstance(res, dict) else res[0]

                    # Generate Sections
                    cur.execute("SELECT NumberOfSections FROM Cohort WHERE CohortID = %s", (target_cohort_id,))
                    num_sections = cur.fetchone()[0]
                    for i in range(1, num_sections + 1):
                        sec_name = f"{p_code} {yr}-{chr(64 + i)}"
                        cur.execute("""
                            INSERT INTO Sections (CohortID, YearLevel, SectionName, IsActive)
                            VALUES (%s, %s, %s, TRUE)
                            ON CONFLICT (CohortID, YearLevel, SectionName) DO NOTHING
                        """, (target_cohort_id, yr, sec_name))

        conn.commit()
        flash("Curriculum Assignment successfully updated based on batch entry years.")
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

    info_sql = "SELECT c.*, p.ProgramName, p.ProgramCode, p.NumYearLevel FROM Curriculum c JOIN Programs p ON c.ProgramCode = p.ProgramCode WHERE c.CurriculumID = %s"
    info_raw = query_db(info_sql, (curriculum_id,), one=True)
    if not info_raw: return redirect(url_for('admin_curriculum'))
    
    info = {k.lower(): v for k, v in info_raw.items()}
    progs = query_db("SELECT * FROM Programs WHERE IsActive = TRUE ORDER BY ProgramName")
    
    other_sql = "SELECT CurriculumID, CurriculumYear, CurriculumCode FROM Curriculum WHERE ProgramCode = %s ORDER BY CurriculumYear DESC"
    other_currs = query_db(other_sql, (info['programcode'],))

    main_sql = "SELECT cv.SubjectCode as subjectcode, cv.\"Prerequisite\" as prerequisite, cv.\"Co-requisite\" as corequisite, s.SubjectName as subjectname, cv.LectureHours as lecturehours, cv.LaboratoryHours as laboratoryhours, cv.CreditUnits as creditunits, cv.TuitionHours as tuitionhours, cv.Semester as semester, CAST(SUBSTRING(cv.ProgramYearLevel FROM '-(.*)') AS INT) as yearlevel FROM Curriculum_View cv LEFT JOIN Subject s ON cv.SubjectCode = s.SubjectCode WHERE cv.CurriculumID = %s"
    
    params = [curriculum_id]
    if y_lvl != '0':
        main_sql += " AND CAST(SUBSTRING(cv.ProgramYearLevel FROM '-(.*)') AS INT) = %s"
        params.append(int(y_lvl))
    if sem != 'All':
        main_sql += " AND cv.Semester = %s"
        params.append(sem)

    main_sql += " ORDER BY yearlevel ASC, cv.Semester ASC"
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

@app.route('/admin/settings')
def admin_settings():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        def to_dict(cursor):
            columns =[col[0].lower() for col in cursor.description]
            return[dict(zip(columns, row)) for row in cursor.fetchall()]

        today = date.today()
        cur.execute("""
            SELECT SemesterID FROM Semester 
            WHERE %s BETWEEN SemStartDate AND SemEndDate 
            LIMIT 1
        """, (today,))
        active_sem_row = cur.fetchone()
        
        is_locked = False
        if active_sem_row:
            active_sem_id = active_sem_row[0]
            cur.execute("SELECT COUNT(*) FROM schedule WHERE semesterid = %s", (active_sem_id,))
            if cur.fetchone()[0] > 0:
                is_locked = True

        cur.execute('SELECT * FROM vw_academic_year_semesters ORDER BY academicyearid DESC')
        ay_data = to_dict(cur)

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

        return render_template('admin/settings_admin.html',
                               ay_list=ay_data, emp_types=emp_types,
                               designations=designations, designee_base=designee_base,
                               is_locked=is_locked, sched_cfg=sched_cfg,
                               accounts=accounts, unlinked_employees=unlinked_employees)
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
        cur.execute("SELECT COUNT(*) FROM Schedule s JOIN Semester sem ON s.SemesterID = sem.SemesterID WHERE sem.IsActive = TRUE")
        if cur.fetchone()[0] > 0:
            flash("Cannot edit settings while an active schedule exists.", "error")
            return redirect(url_for('admin_settings'))

        des_id = request.form.get('designation_id')
        reg_load = request.form.get('reg_load') or 0
        nt_service = request.form.get('night_teaching') or 0

        cur.execute("""
            UPDATE Designation 
            SET RegularLoadUnit=%s, NightTeachingService=%s
            WHERE DesignationID=%s
        """, (reg_load, nt_service, des_id))
        conn.commit()
        flash("Designation rules updated.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error: {str(e)}", "error")
    finally:
        cur.close(); conn.close()
        
    return redirect(url_for('admin_settings'))

@app.route('/admin/reports')
def admin_reports():
    if session.get('role') not in ('Admin', 'Academic Head'):
        return redirect(url_for('login'))
    ay_list       = query_db("SELECT academicyearid, yearstart, yearend FROM academicyear ORDER BY yearstart DESC")
    programs      = query_db("SELECT programcode, programname FROM programs WHERE isactive = TRUE ORDER BY programname")
    faculty       = query_db("SELECT employeenumber, lastname || ', ' || firstname AS fullname FROM faculty ORDER BY lastname, firstname")
    curricula     = query_db("SELECT curriculumid, curriculumcode, curriculumyear, programcode FROM curriculum ORDER BY programcode, curriculumyear DESC")
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
            where.append("p.programcode = %s"); p.append(effective_prog)
        if yl and yl != 'All':
            where.append("sec.yearlevel = %s"); p.append(int(yl))
        cur.execute(f"""
            SELECT
                f.lastname || ', ' || f.firstname AS "Instructor",
                sub.subjectcode AS "Subject Code",
                sub.subjectname AS "Subject Description",
                sub.lecturehours AS "Lec",
                sub.laboratoryhours AS "Lab",
                sub.creditunits AS "Units",
                p.programcode || ' ' || sec.yearlevel AS "Course",
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
            JOIN curriculumsubject cs      ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub               ON cs.subjectcode         = sub.subjectcode
            JOIN sections sec              ON sg.sectionid           = sec.sectionid
            JOIN cohort c                  ON sec.cohortid           = c.cohortid
            JOIN programs p               ON c.programcode          = p.programcode
            JOIN faculty f                ON sg.employeenumber      = f.employeenumber
            JOIN semester sem              ON sg.semesterid          = sem.semesterid
            LEFT JOIN schedule_sessions ss ON sv.versionid           = ss.versionid
            LEFT JOIN timeslot ts_s        ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN timeslot ts_e        ON ss.endtimeid           = ts_e.timeid
            LEFT JOIN room r               ON ss.roomid              = r.roomid
            WHERE {' AND '.join(where)}
            GROUP BY f.lastname, f.firstname, sub.subjectcode, sub.subjectname,
                     sub.lecturehours, sub.laboratoryhours, sub.creditunits,
                     p.programcode, sec.yearlevel
            ORDER BY f.lastname, p.programcode, sec.yearlevel, sub.subjectcode
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
                p.programcode AS "Program",
                sec.sectionname AS "Section",
                (sub.lecturehours + sub.laboratoryhours) AS "Hours"
            FROM schedule_version sv
            JOIN schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub          ON cs.subjectcode         = sub.subjectcode
            JOIN sections sec         ON sg.sectionid           = sec.sectionid
            JOIN cohort c             ON sec.cohortid           = c.cohortid
            JOIN programs p           ON c.programcode          = p.programcode
            JOIN faculty f            ON sg.employeenumber      = f.employeenumber
            JOIN semester sem         ON sg.semesterid          = sem.semesterid
            WHERE {' AND '.join(where)}
            ORDER BY f.lastname, p.programcode, sub.subjectcode
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
                where.append("c.programcode = %s"); p.append(prog)
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
                combo_where.append("sec.yearlevel = %s"); combo_params.append(int(yl))

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
                JOIN cohort co      ON sec.cohortid      = co.cohortid
                JOIN programs p     ON co.programcode    = p.programcode
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
            cursor.execute("""
                SELECT sub.subjectcode, sub.subjectname, r.roomname, ss.daydesc,
                       TO_CHAR(ts_s.timevalue, 'HH12:MI AM') as start_time,
                       TO_CHAR(ts_e.timevalue, 'HH12:MI AM') as end_time
                FROM schedule_sessions ss
                JOIN schedule_version sv ON ss.versionid = sv.versionid
                JOIN schedule sc ON sv.scheduleid = sc.scheduleid
                JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
                JOIN subject sub ON cs.subjectcode = sub.subjectcode
                LEFT JOIN room r ON ss.roomid = r.roomid
                LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
                LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
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
                               total_subjects=total_subjects)

    except Exception as e:
        if conn:
            conn.close()
        return f"<div style='padding: 50px; font-family: Arial;'><h2 style='color: red;'>A Python Error Happened!</h2><p><b>Exact Error Message:</b> {str(e)}</p></div>"

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

# --- Route for Schedule/Rooms Page ---
@app.route('/faculty_schedule')
def faculty_schedule():
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return redirect(url_for('login'))
    return render_template('faculty/schedule_faculty.html')

# --- Route for 'My Requests' Page ---
# Note: Renders request_faculty.html as per your file structure
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
               d.nightteachingservice, COALESCE(d.regularloadunit, 0) AS designation_regular_load
        FROM faculty f
        JOIN employeetype et ON f.employeetypeid = et.employeetypeid
        LEFT JOIN designation d ON f.designationid = d.designationid
        WHERE f.employeestatus != 'Archive'
    """)
    faculty_map = {}
    for row in (rows or []):
        fnum = row['employeenumber']
        has_desig = row['designationid'] is not None
        # Designees: override both load limits from the designation table
        eff_regular  = row['designation_regular_load'] if (has_desig and row['designation_regular_load']) else row['regularload']
        eff_parttime = (row['nightteachingservice'] or 0) if has_desig else row['parttimeload']
        faculty_map[fnum] = {
            'employeenumber': fnum, 'fullname': row['fullname'],
            'employeestatus': row['employeestatus'], 'designationid': row['designationid'],
            'nightteachingservice': row['nightteachingservice'],
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

def _archive_status(cur, program, year_level, term, semester_id, status_to_archive):
    cur.execute("""
        UPDATE public.schedule_version sv
        SET status = 'Archive'
        FROM public.schedule s, public.curriculumsubject cs, public.curriculum c
        WHERE sv.scheduleid = s.scheduleid AND s.curriculumsubjectid = cs.curriculumsubjectid
          AND cs.curriculumid = c.curriculumid AND UPPER(c.programcode) = UPPER(%s)
          AND cs.yearlevel = %s AND cs.semester = %s AND s.semesterid = %s AND sv.status = %s
    """, (program, year_level, term, semester_id, status_to_archive))

def _archive_status_for_subjects(cur, program, year_level, term, semester_id, status_to_archive, subject_codes):
    """Archive only sessions for specific subject codes, leaving other subjects' versions intact."""
    if not subject_codes: return
    upper_codes = [s.upper() for s in subject_codes]
    placeholders = ','.join(['%s'] * len(upper_codes))
    cur.execute(f"""
        UPDATE public.schedule_version sv
        SET status = 'Archive'
        FROM public.schedule s, public.curriculumsubject cs, public.curriculum c
        WHERE sv.scheduleid = s.scheduleid AND s.curriculumsubjectid = cs.curriculumsubjectid
          AND cs.curriculumid = c.curriculumid AND UPPER(c.programcode) = UPPER(%s)
          AND cs.yearlevel = %s AND cs.semester = %s AND s.semesterid = %s AND sv.status = %s
          AND UPPER(cs.subjectcode) IN ({placeholders})
    """, [program, year_level, term, semester_id, status_to_archive] + upper_codes)

def _insert_batch(cur, schedule_data, semester_id, target_status, version_number, program, year_level):
    cur.execute("""
        SELECT sec.sectionid FROM public.sections sec JOIN public.cohort co ON sec.cohortid = co.cohortid
        WHERE UPPER(co.programcode) = UPPER(%s) AND sec.yearlevel = %s LIMIT 1
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
            WHERE UPPER(cs.subjectcode) = UPPER(%s) AND UPPER(c.programcode) = UPPER(%s)
              AND cs.yearlevel = %s AND cs.semester = %s LIMIT 1
        """, (s_code, program, year_level, cls.get('sem') or cls.get('semester', '')))
        cs_res = cur.fetchone()
        if not cs_res:
            # Fallback: match by subject+program only (in case semester data is missing)
            cur.execute("""
                SELECT cs.curriculumsubjectid FROM public.curriculumsubject cs
                JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
                WHERE UPPER(cs.subjectcode) = UPPER(%s) AND UPPER(c.programcode) = UPPER(%s) LIMIT 1
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
            INSERT INTO public.schedule_version (scheduleid, version_number, status, datecreated) 
            VALUES (%s, %s, %s, NOW()) RETURNING versionid
        """, (sched_id, version_number, target_status))
        ver_id = cur.fetchone()['versionid']

        cur.execute("INSERT INTO public.schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid) VALUES (%s, %s, %s, %s, %s)",
                    (ver_id, day, s_id, e_id, r_id if str(r_id).isdigit() else None))

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
                   c.programcode, cs.yearlevel, sem.semestertype AS term,
                   ay.academicyearid AS acadyear, sc.semesterid,
                   ay.yearstart, ay.yearend
            FROM   public.schedule_version sv
            JOIN   public.schedule sc         ON sv.scheduleid           = sc.scheduleid
            JOIN   public.curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   public.curriculum c         ON cs.curriculumid        = c.curriculumid
            JOIN   public.semester sem         ON sc.semesterid          = sem.semesterid
            JOIN   public.academicyear ay      ON sem.academicyearid     = ay.academicyearid
            WHERE  UPPER(c.programcode) = UPPER(%s)
              AND  cs.yearlevel         = %s
              AND  sem.semestertype     = %s
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
                   c.programcode          AS course,
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
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        sem_id = _get_semester_id(cur, ay, term)

        cur.execute("""
            SELECT COALESCE(MAX(sv.version_number), 0) 
            FROM public.schedule_version sv
            JOIN public.schedule s ON sv.scheduleid = s.scheduleid
            JOIN public.curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
            JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
            WHERE UPPER(c.programcode) = UPPER(%s) 
              AND cs.yearlevel = %s 
              AND s.semesterid = %s
        """, (program, year_level, sem_id))
        new_v = cur.fetchone()['coalesce'] + 1

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
                JOIN   cohort co            ON sec.cohortid             = co.cohortid
                JOIN   curriculumsubject cs2 ON sc.curriculumsubjectid = cs2.curriculumsubjectid
                WHERE  UPPER(co.programcode) = UPPER(%s)
                  AND  sec.yearlevel         = %s
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

        # --- FIX: I-archive ang LAHAT ng involved na subjects (kasama ang mga binura) ---
        if all_codes_to_archive:
            _archive_status_for_subjects(cur, program, year_level, term, sem_id, 'Draft', all_codes_to_archive)
        
        # I-insert lang ang bagong scheds kung may laman
        if rehydrated:
            _insert_batch(cur, rehydrated, sem_id, 'Draft', new_v, program, year_level)
            
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
            LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
            LEFT JOIN cohort co ON sec.cohortid = co.cohortid
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

@app.route('/api/schedule/approve', methods=['POST'])
def api_approve_schedule():
    try:
        data, ctx = request.json or {}, (request.json or {}).get('context', {})
        program, year_level, term, ay = ctx.get('program'), int(ctx.get('yearLevel')), ctx.get('term'), ctx.get('acadYear')

        # ── Server-side CSP guard ── block approval if any hard constraint is violated
        from scheduler import CSPValidator
        rehydrated  = _rehydrate_schedule(list(data.get('schedule_data', [])))
        faculty_map = _load_faculty_map()
        violations  = CSPValidator().validate(rehydrated, faculty_map)
        
        # --- FIX: Filter out "Non-standard time" constraints entirely ---
        filtered_violations = []
        if violations:
            for v in violations:
                msg = v.get('detail', '')
                if 'Non-standard start time' in msg or 'Non-standard end time' in msg:
                    continue # I-ignore ang validation na ito
                filtered_violations.append(v)
                
        if filtered_violations:
            return jsonify({
                'success': False,
                'error':   f'Cannot approve: {len(filtered_violations)} unresolved constraint violation(s). '
                           'Resolve all conflicts in the Manual Editor before approving.',
                'violations': filtered_violations,
            }), 400
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        sem_id = _get_semester_id(cur, ay, term)

        cur.execute("""
    SELECT COALESCE(MAX(sv.version_number), 0) 
    FROM public.schedule_version sv
    JOIN public.schedule s ON sv.scheduleid = s.scheduleid
    JOIN public.curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
    JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
    WHERE UPPER(c.programcode) = UPPER(%s) 
      AND cs.yearlevel = %s 
      AND s.semesterid = %s
""", (program, year_level, sem_id))
        max_v = cur.fetchone()['coalesce']

        sched_data = _rehydrate_schedule(list(data.get('schedule_data', [])))
        submitted_codes = list({
            (cls.get('subject_code') or cls.get('subjectcode') or '').upper()
            for cls in sched_data
            if cls.get('subject_code') or cls.get('subjectcode')
        })
        # Archive only the selected subjects' versions — leaves unselected subjects' Draft intact
        _archive_status_for_subjects(cur, program, year_level, term, sem_id, 'Published', submitted_codes)
        _archive_status_for_subjects(cur, program, year_level, term, sem_id, 'Draft', submitted_codes)
        # Insert as Published only — no parallel Draft copy, so approved subjects show Published
        # badge and disappear from draftView. User re-drafts via Manual Editor if edits needed.
        _insert_batch(cur, sched_data, sem_id, 'Published', max_v + 1, program, year_level)

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
            SELECT c.programcode, cs.yearlevel, cs.semester AS term, s.semesterid
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
                   COALESCE(et.typename, f.employeestatus, 'Regular') AS employee_type,
                   COALESCE(et.regularload, 0) AS reg_load,
                   COALESCE(et.parttimeload, 0) AS pt_load
            FROM faculty f
            LEFT JOIN employeetype et ON f.employeetypeid = et.employeetypeid
            WHERE f.employeenumber = %s
        """, (emp_num,))
        fac = cur.fetchone()
        if not fac:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Faculty not found'}), 404
        cur.execute("""
            SELECT
                sub.subjectcode,
                sub.subjectname,
                (COALESCE(sub.lecturehours,0) + COALESCE(sub.laboratoryhours,0)) AS units,
                COALESCE(co.programcode,'') || '-' || COALESCE(sec.yearlevel::text,'')
                    || ' ' || COALESCE(sec.sectionname,'') AS year_section,
                sem.semestertype AS subj_ref,
                TO_CHAR(ts_s.timevalue,'HH12:MI AM') || ' - ' || TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS time_range,
                LPAD(EXTRACT(HOUR FROM ts_s.timevalue)::text,2,'0') ||
                LPAD(EXTRACT(HOUR FROM ts_e.timevalue)::text,2,'0') AS time_code,
                ss.daydesc AS days,
                COALESCE(r.roomname,'—') AS room,
                COALESCE(TO_CHAR(sem.startdate,'MM/DD/YYYY'),'—') AS effectivity,
                sv.status
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub ON cs.subjectcode = sub.subjectcode
            JOIN semester sem ON sc.semesterid = sem.semesterid
            LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
            LEFT JOIN cohort co ON sec.cohortid = co.cohortid
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
        max_units = int(fac['reg_load'] or 0) + int(fac['pt_load'] or 0)
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


@app.route('/api/schedule/drafts')
@app.route('/api/schedule/versions')
def api_list_versions():
    try:
        is_drafts = 'drafts' in request.path
        if is_drafts:
            # One consolidated entry per program/year/semester — use the latest versionid/version_number
            rows = query_db("""
                SELECT DISTINCT ON (c.programcode, cs.yearlevel, cs.semester, ay.academicyearid)
                       sv.versionid, sv.version_number, sv.status, sv.datecreated,
                       c.programcode, cs.yearlevel, cs.semester AS term, ay.academicyearid AS acadyear
                FROM   public.schedule_version sv
                JOIN   public.schedule sg ON sv.scheduleid = sg.scheduleid
                JOIN   public.curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
                JOIN   public.curriculum c ON cs.curriculumid = c.curriculumid
                JOIN   public.semester sem ON sg.semesterid = sem.semesterid
                JOIN   public.academicyear ay ON sem.academicyearid = ay.academicyearid
                WHERE  sv.status = 'Draft'
                ORDER BY c.programcode, cs.yearlevel, cs.semester, ay.academicyearid, sv.version_number DESC
            """)
        else:
            # CTE groups all subjects per section per version_number into one row with
            # an aggregated status (any Draft → Draft; all Published → Published; else Archive).
            # Window function then counts Published revisions in order to assign Version numbers
            # (only Published revisions get a Version label; all revisions get a Revision label).
            rows = query_db("""
                WITH versioned AS (
                    SELECT MAX(sv.versionid)   AS versionid,
                           sv.version_number,
                           CASE
                               WHEN bool_or(sv.status = 'Draft')      THEN 'Draft'
                               WHEN bool_and(sv.status = 'Published') THEN 'Published'
                               ELSE 'Archive'
                           END                 AS status,
                           MAX(sv.datecreated) AS datecreated,
                           c.programcode,
                           cs.yearlevel,
                           cs.semester         AS term,
                           ay.academicyearid   AS acadyear
                    FROM   public.schedule_version sv
                    JOIN   public.schedule sg          ON sv.scheduleid           = sg.scheduleid
                    JOIN   public.curriculumsubject cs  ON sg.curriculumsubjectid  = cs.curriculumsubjectid
                    JOIN   public.curriculum c          ON cs.curriculumid         = c.curriculumid
                    JOIN   public.semester sem          ON sg.semesterid           = sem.semesterid
                    JOIN   public.academicyear ay       ON sem.academicyearid      = ay.academicyearid
                    GROUP BY c.programcode, cs.yearlevel, cs.semester, ay.academicyearid, sv.version_number
                )
                SELECT *,
                       CASE WHEN status = 'Published' THEN
                           SUM(CASE WHEN status = 'Published' THEN 1 ELSE 0 END)
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
                'published_version_number': r.get('published_version_number'),
                'status':                  r['status'],
                'datecreated':             dt.isoformat() if hasattr(dt, 'isoformat') else str(dt),
                'programcode':             r['programcode'],
                'yearlevel':               r['yearlevel'],
                'term':                    r['term'],
                'acadyear':                r['acadyear'],
            })
        return jsonify(sorted(output, key=lambda x: x['datecreated'], reverse=True))
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/api/schedule/versions/<int:version_id>/restore', methods=['POST'])
def api_restore_version(version_id):
    """Restore any version back to Draft status, archiving any existing Draft first."""
    try:
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT c.programcode, cs.yearlevel, cs.semester AS term, sg.semesterid
            FROM schedule_version sv
            JOIN schedule sg ON sv.scheduleid = sg.scheduleid
            JOIN curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
            WHERE sv.versionid = %s LIMIT 1
        """, (version_id,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return jsonify({'success': False, 'error': 'Version not found'}), 404
        # Archive any existing Draft for this program/year/semester
        _archive_status(cur, row['programcode'], row['yearlevel'], row['term'], row['semesterid'], 'Draft')
        # Restore the target version to Draft
        cur.execute("UPDATE schedule_version SET status = 'Draft' WHERE versionid = %s", (version_id,))
        conn.commit(); cur.close(); conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/schedule/versions/<int:version_id>/sessions')
def api_overlay_sessions(version_id):
    """Return all schedule sessions for a historical versionid for timetable overlay comparison."""
    try:
        rows = query_db("""
            WITH ref AS (
                SELECT sv.version_number, c.programcode, cs.yearlevel, sg.semesterid
                FROM   schedule_version sv
                JOIN   schedule sg          ON sv.scheduleid          = sg.scheduleid
                JOIN   curriculumsubject cs  ON sg.curriculumsubjectid = cs.curriculumsubjectid
                JOIN   curriculum c          ON cs.curriculumid        = c.curriculumid
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
            LEFT JOIN faculty f          ON sg.employeenumber      = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid         = sv.versionid
            LEFT JOIN room r             ON ss.roomid              = r.roomid
            LEFT JOIN timeslot ts_s      ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN timeslot ts_e      ON ss.endtimeid           = ts_e.timeid
            WHERE  c.programcode = ref.programcode
              AND  cs.yearlevel  = ref.yearlevel
              AND  sg.semesterid = ref.semesterid
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
            JOIN cohort co             ON sec.cohortid            = co.cohortid
            LEFT JOIN faculty f        ON sc.employeenumber       = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid        = sv.versionid
            LEFT JOIN room r           ON ss.roomid               = r.roomid
            LEFT JOIN timeslot ts_s    ON ss.starttimeid          = ts_s.timeid
            LEFT JOIN timeslot ts_e    ON ss.endtimeid            = ts_e.timeid
            WHERE UPPER(co.programcode) = UPPER(%s)
              AND sec.yearlevel          = %s
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
                   c.programcode, cs.yearlevel, sem.semestertype AS term,
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
            SELECT sub.subjectcode AS subject_code, sub.subjectname AS description, sub.lecturehours AS lec_hours,
                   sub.laboratoryhours AS lab_hours, sub.creditunits AS units, c.programcode AS course,
                   sc.employeenumber AS faculty_id, CONCAT(f.lastname, ', ', f.firstname) AS instructor,
                   ss.daydesc, TO_CHAR(ts_s.timevalue, 'HH24:MI') AS start_time, TO_CHAR(ts_e.timevalue, 'HH24:MI') AS end_time,
                   r.roomname AS room, r.roomid, sv.version_number AS row_version
            FROM schedule_version sv JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN schedule_sessions ss ON ss.versionid = sv.versionid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub ON cs.subjectcode = sub.subjectcode
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
        return jsonify({'success': True, 'schedule_data': sched, 'version': max_v, 'context': {'program': m['programcode'], 'yearLevel': m['yearlevel'], 'term': m['term'], 'acadYear': m['acadyear']}})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/curriculum-by-year')
def api_curriculum_by_year():
    p = request.args.get('program', '')
    try:
        row = query_db("SELECT curriculumyear FROM curriculum WHERE programcode = %s ORDER BY curriculumyear DESC LIMIT 1", (p,))
        return jsonify({'curriculum': row[0]['curriculumyear'] if row else ''})
    except: return jsonify({'curriculum': ''}), 500

# --- MAIN EXECUTION ---
if __name__ == '__main__':
   app.run(debug=True, use_reloader=False)
   