from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash, Response
from database import get_db_connection, query_db
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, time, timedelta
import csv
import io
import json
import psycopg2.extras
from psycopg2.extras import RealDictCursor

# 1. INITIALIZE APP FIRST
app = Flask(__name__)
app.secret_key = 'pup_lopez_super_secret_key' # Required for Login Sessions

# --- CONTEXT PROCESSOR FOR DYNAMIC ACADEMIC YEAR ---
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

    cur.execute("""
        SELECT ss.*, sub.subjectcode, sub.subjectname, r.roomname,
               TO_CHAR(ts_s.timevalue, 'HH12:MI AM') as start_time,
               TO_CHAR(ts_e.timevalue, 'HH12:MI AM') as end_time
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        JOIN subject sub ON cs.subjectcode = sub.subjectcode
        JOIN room r ON ss.roomid = r.roomid
        JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
        JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
        WHERE sc.employeenumber = %s AND sv.status = 'Published'
    """, (emp_num,))
    my_schedule = cur.fetchall()
    cur.close(); conn.close()
    return render_template('academic/AcadAsFaculty.html', user=user_data, schedule=my_schedule)

@app.route('/academic/my-teaching-assignment')
def academic_my_teaching_assignment():
    if 'loggedin' not in session or session.get('role') != 'Academic Head':
        return redirect(url_for('login'))
    
    username = session.get('username')
    acc = query_db("SELECT employeenumber FROM accounts WHERE username = %s", (username,), one=True)
    emp_num = acc['employeenumber'] if acc else None
    
    query = """
        SELECT sub.subjectcode, sub.subjectname, sub.creditunits, 
               sec.sectionname, ss.daydesc, r.roomname,
               TO_CHAR(ts_s.timevalue, 'HH12:MI AM') || ' - ' || TO_CHAR(ts_e.timevalue, 'HH12:MI AM') as time_range
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        JOIN subject sub ON cs.subjectcode = sub.subjectcode
        JOIN sections sec ON sc.sectionid = sec.sectionid
        JOIN room r ON ss.roomid = r.roomid
        JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
        JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
        WHERE sc.employeenumber = %s AND sv.status = 'Published'
    """
    assignments = query_db(query, (emp_num,))
    return render_template('academic/AcadTeachingAssign.html', assignments=assignments)

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

    try:
        query = """
            SELECT 
                f.EmployeeNumber, f.FirstName, f.MiddleName, f.LastName, f.Email, f.ContactNumber, 
                f.EmployeeStatus, s.SpecializationName, et.TypeName, des.DesignationName,
                f.SpecializationID, f.EmployeeTypeID, f.DesignationID
            FROM Faculty f
            JOIN Specialization s ON f.SpecializationID = s.SpecializationID
            JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID
            LEFT JOIN Designation des ON f.DesignationID = des.DesignationID
            ORDER BY f.LastName ASC
        """
        raw_employees = query_db(query)
        employees =[{k.lower(): v for k, v in row.items()} for row in raw_employees] if raw_employees else[]
        
        specializations = query_db("SELECT SpecializationID, SpecializationName FROM Specialization WHERE IsActive = TRUE")
        employee_types = query_db("SELECT EmployeeTypeID, TypeName FROM EmployeeType")
        designations = query_db("SELECT DesignationID, DesignationName FROM Designation")
        
        pt_data = query_db("SELECT COUNT(*) as count FROM Faculty f JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID WHERE et.TypeName = 'Part-Time'", one=True)
        reg_data = query_db("SELECT COUNT(*) as count FROM Faculty f JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID WHERE et.TypeName = 'Regular'", one=True)
        des_data = query_db("SELECT COUNT(*) as count FROM Faculty f JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID WHERE et.TypeName = 'Designee'", one=True)
        
        return render_template('academic/employee.html', 
                               employees=employees, specializations=specializations,
                               employee_types=employee_types, designations=designations,
                               pt=pt_data['count'] if pt_data else 0, 
                               reg=reg_data['count'] if reg_data else 0, 
                               des=des_data['count'] if des_data else 0, 
                               total=len(employees))
    except Exception as e:
        print(f"Employee Page Error: {e}")
        return render_template('academic/employee.html', employees=[], total=0)

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
        for row_num, row in enumerate(csv_input, 2):
            if len(row) < 10: continue
            try:
                emp_num, last_name, first_name, middle_name, email, contact, spec_id_str, etype_id_str, status, desig_id_str =[r.strip() for r in row[:10]]
                
                middle_name = middle_name if middle_name else None
                email = email if email else None
                contact = contact if contact else None
                spec_id = int(spec_id_str) if spec_id_str else None
                etype_id = int(etype_id_str) if etype_id_str else None
                desig_id = None if desig_id_str.upper() == 'NULL' or not desig_id_str else int(desig_id_str)
                
                cur.execute("""
                    INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (EmployeeNumber) DO NOTHING
                """, (emp_num, first_name, middle_name, last_name, email, contact, spec_id, etype_id, desig_id, status))

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
        buildings = query_db("SELECT BuildingName AS buildingname FROM Building WHERE IsActive = TRUE ORDER BY BuildingName ASC")
        
        raw_rooms = query_db("""
            SELECT r.RoomID, r.RoomName, r.RoomType, r.RoomCapacity, b.BuildingName 
            FROM Room r 
            JOIN Building b ON r.BuildingID = b.BuildingID 
            ORDER BY r.RoomName ASC
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
    active_sem_type = active_sem_res[0]['semestertype'] if active_sem_res else 'B'
    active_ay       = active_sem_res[0]['academicyearid'] if active_sem_res else (acad_years[0]['academicyearid'] if acad_years else '')

    status_query = """
        SELECT DISTINCT
            p.programname,
            p.programcode,
            sec.yearlevel,
            ay.academicyearid,
            s.datecreated
        FROM schedule s
        JOIN sections sec   ON s.sectionid         = sec.sectionid
        JOIN cohort c       ON sec.cohortid         = c.cohortid
        JOIN programs p     ON c.programcode        = p.programcode
        JOIN semester sem   ON s.semesterid         = sem.semesterid
        JOIN academicyear ay ON sem.academicyearid  = ay.academicyearid
        ORDER BY s.datecreated DESC
    """
    status_list = query_db(status_query)

    return render_template('academic/schedule.html',
                           programs=programs,
                           acad_years=acad_years,
                           status_list=status_list,
                           active_sem_type=active_sem_type,
                           active_ay=active_ay)

@app.route('/api/get_offerings_schedule')
def get_offerings_schedule():
    import re
    prog = request.args.get('program')
    yl   = request.args.get('year_level')
    sem  = request.args.get('semester')
    ay   = request.args.get('ay')

    print(f"\n[DEBUG get_offerings_schedule] prog={prog!r} yl={yl!r} sem={sem!r} ay={ay!r}")

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # ── 1. Try normalized schedule_sessions first ─────────────────────
        cur.execute("""
            SELECT
                sub.subjectcode,
                sub.subjectname,
                COALESCE(f.lastname || ', ' || f.firstname || COALESCE(' ' || f.middlename, ''), 'TBA') AS instructor,
                COALESCE(r.roomname, 'TBA')                         AS roomname,
                ss.daydesc,
                TO_CHAR(ts_s.timevalue, 'HH24:MI')                 AS start_time,
                TO_CHAR(ts_e.timevalue, 'HH24:MI')                 AS end_time,
                COALESCE(sub.lecturehours,    0)                    AS lecturehours,
                COALESCE(sub.laboratoryhours, 0)                    AS laboratoryhours,
                COALESCE(sub.creditunits,     0)                    AS creditunits,
                (COALESCE(sub.lecturehours,0) + COALESCE(sub.laboratoryhours,0)) AS total_hours
            FROM schedule_version sv
            JOIN schedule sc          ON sv.scheduleid = sc.scheduleid AND sv.status = 'Published'
            JOIN curriculumsubject cs  ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub           ON cs.subjectcode = sub.subjectcode
            JOIN sections sec          ON sc.sectionid = sec.sectionid
            JOIN cohort co             ON sec.cohortid = co.cohortid
            LEFT JOIN faculty f        ON sc.employeenumber = f.employeenumber
            LEFT JOIN schedule_sessions ss ON ss.versionid = sv.versionid
            LEFT JOIN room r           ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s    ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e    ON ss.endtimeid   = ts_e.timeid
            WHERE UPPER(co.programcode) = UPPER(%s)
              AND sec.yearlevel = %s
              AND sc.semesterid = (
                  SELECT semesterid FROM semester
                  WHERE semestertype = %s AND academicyearid = %s LIMIT 1
              )
            ORDER BY ts_s.timevalue NULLS LAST
        """, (prog, int(yl), sem, ay))
        normalized_rows = cur.fetchall()
        print(f"[DEBUG] normalized_rows count={len(normalized_rows)}")
        for r in normalized_rows[:3]:
            print(f"  norm: subj={r['subjectcode']!r} start={r['start_time']!r} day={r['daydesc']!r}")

        # ── 2. historical_data — always query; covers unmatched faculty and past semesters ─
        # Build a set of subject codes already in normalized results to avoid duplicates
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

        # Subjects that have REAL time data in the normalized (schedule_sessions) path
        norm_subjects_with_time = {r['subjectcode'] for r in normalized_rows if r['start_time']}

        # From normalized: keep only rows that have time data
        norm_timed = [dict(r) for r in normalized_rows if r['start_time']]

        # From historical: include any subject not already covered by a timed normalized row
        hist_extra = [row for row in result if row['subjectcode'] not in norm_subjects_with_time]

        # For normalized rows with NO time, keep them only if historical has nothing for that subject
        hist_subjects = {r['subjectcode'] for r in result}
        norm_untimed_fallback = [
            dict(r) for r in normalized_rows
            if not r['start_time'] and r['subjectcode'] not in hist_subjects
        ]

        combined = norm_timed + hist_extra + norm_untimed_fallback
        print(f"[DEBUG] combined={len(combined)} (norm_timed={len(norm_timed)}, hist_extra={len(hist_extra)}, fallback={len(norm_untimed_fallback)})")
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


def _insert_historical(cur, inst, s_code, subj_name, prog, yl, days_raw, time_raw, room, sem_id, ay_id, lec, lab, unit, hrs):
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

        sem_id = sem_res['semesterid']
        print(f"\n[DEBUG IMPORT] ay_id={ay_id!r} sem_type={sem_type!r} sem_id={sem_id!r} is_current will be based on dates: {sem_res['semstartdate']} → {sem_res['semenddate']}")
        today = date.today()
        sem_start = sem_res['semstartdate']
        sem_end   = sem_res['semenddate']

        # A semester is "current" only when the admin has configured its date range
        # AND today falls within that range.
        is_current = bool(sem_start and sem_end and sem_start <= today <= sem_end)
        print(f"[DEBUG IMPORT] today={today} is_current={is_current}")

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

                if emp_num and cs_id and sec_id:
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

@app.route('/schedule/generation')
def schedule_generation():
    if 'loggedin' not in session: return redirect(url_for('login'))
    return render_template('academic/scheduleGeneration.html')

@app.route('/schedule/manual-editor')
def manual_schedule_editor():
    if 'loggedin' not in session: return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT AcademicYearID 
            FROM AcademicYear 
            WHERE YearStart >= COALESCE((SELECT YearStart FROM AcademicYear WHERE IsActive = TRUE LIMIT 1), 0)
            ORDER BY YearStart ASC
        """)
        acad_years = [row[0] for row in cur.fetchall()]
        cur.execute("SELECT ProgramCode, ProgramName FROM Programs WHERE IsActive = TRUE ORDER BY ProgramName")
        programs = cur.fetchall()
        cur.execute("SELECT EmployeeNumber, LastName || ', ' || FirstName || ' ' || COALESCE(MiddleName, '') FROM Faculty WHERE EmployeeStatus != 'Archived' ORDER BY LastName")
        faculty = cur.fetchall()
        cur.execute("SELECT BuildingID, BuildingName FROM Building WHERE IsActive = TRUE ORDER BY BuildingName")
        buildings = cur.fetchall()
        cur.execute("""
            SELECT r.RoomID, r.RoomName, r.BuildingID, b.BuildingName
            FROM Room r
            LEFT JOIN Building b ON r.BuildingID = b.BuildingID
            ORDER BY b.BuildingName, r.RoomName
        """)
        raw_rooms = cur.fetchall()

        rooms_list = [{"id": r[0], "name": r[1], "bldg_id": r[2], "bldg_name": r[3] or "Unknown", "floor": "1" if "LQ1" in r[1].replace(" ","") else "2" if "LQ2" in r[1].replace(" ","") else "ALL"} for r in raw_rooms]
        
        return render_template('academic/manualScheduleEditor.html',
                               acad_years=acad_years, programs=programs,
                               faculty=faculty, buildings=buildings, rooms_json=json.dumps(rooms_list))
    finally:
        cur.close()
        conn.close()

@app.route('/api/get_room_schedule/<int:room_id>')
def get_room_schedule(room_id):
    if 'loggedin' not in session: return jsonify([])
    ay_id    = request.args.get('ay_id')
    semester = request.args.get('semester')
    program  = request.args.get('program')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        filters = ["ss.roomid = %s"]
        params  = [room_id]
        # Always join semester — we use its end date to filter out past semesters by default
        joins = " JOIN semester sem ON s.semesterid = sem.semesterid"
        if ay_id:
            joins += " JOIN academicyear ay ON sem.academicyearid = ay.academicyearid"
            filters.append("ay.academicyearid = %s")
            params.append(ay_id)
        if semester:
            filters.append("sem.semestertype = %s")
            params.append(semester)
        if not (ay_id or semester):
            # No explicit AY/Sem chosen — exclude semesters whose end date has passed so the past doesn't bleed in
            filters.append("(sem.semenddate IS NULL OR sem.semenddate >= CURRENT_DATE)")
        if program:
            joins += " JOIN sections sec ON s.sectionid = sec.sectionid JOIN cohort c ON sec.cohortid = c.cohortid"
            filters.append("c.programcode = %s")
            params.append(program)

        cur.execute(f"""
            SELECT
                cs.subjectcode,
                subj.subjectname,
                f.lastname || ', ' || f.firstname AS instructor,
                ss.daydesc,
                ss.starttimeid,
                ss.endtimeid
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
        
@app.route('/api/get_curriculum')
def api_get_curriculum():
    prog = request.args.get('program')
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
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
        cur.execute("""
            SELECT semestertype FROM semester
            WHERE academicyearid = %s AND isactive = TRUE
            LIMIT 1
        """, (ay_id,))
        row = cur.fetchone()
        current_sem = row['semestertype'] if row else None
        # Semesters whose end date has not yet passed
        cur.execute("""
            SELECT semestertype FROM semester
            WHERE academicyearid = %s AND semenddate >= %s
        """, (ay_id, today))
        valid_sems = [r['semestertype'] for r in cur.fetchall()]
        # Only show current + future — a semester past its end date is dropped even if it's still marked active
        return jsonify({"success": True, "done_semesters": done, "current_sem": current_sem, "valid_sems": valid_sems})
    except Exception:
        return jsonify({"success": False, "done_semesters": [], "current_sem": None, "valid_sems": []})
    finally:
        cur.close(); conn.close()

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
    yl = request.args.get('year_level')
    sem = request.args.get('semester')
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT s.SubjectCode, s.SubjectName
            FROM CurriculumSubject cs
            JOIN Subject s ON cs.SubjectCode = s.SubjectCode
            WHERE cs.CurriculumID = %s AND cs.YearLevel = %s AND cs.Semester = %s
            ORDER BY s.SubjectName ASC
        """, (curr_id, yl, sem))
        subjects = cur.fetchall()
        return jsonify({"success": True, "subjects": subjects})
    finally:
        cur.close(); conn.close()
@app.route('/reports')
def reports():
    if 'loggedin' not in session: return redirect(url_for('login'))
    return render_template('academic/reports.html')

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
        for i, row in enumerate(csv_input, 2):
            if len(row) < 10: continue
            try:
                emp_num, last_name, first_name, middle_name, email, contact, spec_id_str, etype_id_str, status, desig_id_str =[r.strip() for r in row[:10]]
                
                middle_name = middle_name if middle_name else None
                email = email if email else None
                contact = contact if contact else None
                spec_id = int(spec_id_str) if spec_id_str else None
                etype_id = int(etype_id_str) if etype_id_str else None
                desig_id = None if not desig_id_str or desig_id_str.upper() == 'NULL' else int(desig_id_str)
                
                cur.execute("""
                    INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (EmployeeNumber) DO NOTHING
                """, (emp_num, first_name, middle_name, last_name, email, contact, spec_id, etype_id, desig_id, status))

                role = 'Faculty'
                if desig_id:
                    cur.execute("SELECT DesignationName FROM Designation WHERE DesignationID = %s", (desig_id,))
                    designation = cur.fetchone()
                    if designation and designation['designationname'] == 'Academic Head':
                        role = 'Academic Head'

                hashed_password = generate_password_hash(emp_num)
                cur.execute("""
                    INSERT INTO Accounts (Username, PasswordHash, Role, IsActive, EmployeeNumber)
                    VALUES (%s, %s, %s, TRUE, %s)
                    ON CONFLICT (Username) DO NOTHING
                """, (emp_num, hashed_password, role, emp_num))

            except ValueError:
                conn.rollback()
                flash(f"Import Error: Invalid number format in row {i}. Please check ID columns.", "error")
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
    name = request.form.get('bldg_name')
    if name:
        try:
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute("INSERT INTO Building (BuildingName) VALUES (%s)", (name,))
            conn.commit()
            flash(f"Building '{name}' added successfully!", "success")
        except Exception as e:
            flash(f"Error: {e}", "error")
        finally:
            cur.close(); conn.close()
    return redirect(url_for('admin_rooms'))

@app.route('/admin/add_room', methods=['POST'])
def add_room():
    name = request.form.get('room_name')
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("INSERT INTO Room (RoomName, RoomType, RoomCapacity, BuildingID) VALUES (%s, %s, %s, %s)", 
                    (name, request.form.get('room_type'), request.form.get('capacity'), request.form.get('bldg_id')))
        conn.commit()
        flash(f"Room '{name}' added successfully!", "success")
    except Exception as e:
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
        cur.execute("SELECT COUNT(*) FROM Schedule s JOIN Room r ON s.RoomID = r.RoomID WHERE r.BuildingID = %s", (bldg_id,))
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
    
    ui_year = request.form.get('year_level')
    ui_sem = request.form.get('semester')

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
            s_code = get_val(row, 'sc')
            s_name = get_val(row, 'sn')
            if not s_code: continue
            
            # --- FIX: ADDED TUITION HOURS (th) HERE ---
            cur.execute("""
                INSERT INTO Subject (SubjectCode, SubjectName, CreditUnits, LectureHours, LaboratoryHours, TuitionHours) 
                VALUES (%s,%s,%s,%s,%s,%s) 
                ON CONFLICT (SubjectCode) DO UPDATE SET 
                SubjectName = EXCLUDED.SubjectName,
                CreditUnits = EXCLUDED.CreditUnits,
                TuitionHours = EXCLUDED.TuitionHours
            """, (s_code, s_name or s_code, parse_int(get_val(row, 'u')), parse_int(get_val(row, 'lc')), parse_int(get_val(row, 'lb')), parse_int(get_val(row, 'th'))))

        for row in csv_data:
            if not row: continue
            s_code = get_val(row, 'sc')
            if not s_code: continue

            csv_yl = parse_int(get_val(row, 'yl'))
            final_yl = csv_yl if csv_yl > 0 else parse_int(ui_year)
            
            raw_s = get_val(row, 'sem').upper()
            if '1' in raw_s or 'A' in raw_s: final_sem = 'A'
            elif '2' in raw_s or 'B' in raw_s: final_sem = 'B'
            elif 'SUMMER' in raw_s or 'C' in raw_s: final_sem = 'C'
            else: final_sem = ui_sem if ui_sem != 'All' else 'A'

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

        return render_template('admin/settings_admin.html', 
                               ay_list=ay_data, emp_types=emp_types, 
                               designations=designations, designee_base=designee_base,
                               is_locked=is_locked)
    except Exception as e:
        flash(f"Error loading settings: {e}", "error")
        return redirect(url_for('admin_dashboard'))
    finally:
        cur.close(); conn.close()

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
    if session.get('role') != 'Admin': 
        return redirect(url_for('login'))
    return render_template('admin/reports_admin.html')

# ==============================================================================
# --- FACULTY SPECIFIC ROUTES ---
# ==============================================================================

@app.route('/faculty_dashboard')
def faculty_dashboard():
    # 1. Verify Login
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return redirect(url_for('login')) 

    username = session.get('username')
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        query = """
            SELECT 
                a.username,
                f.firstname, 
                f.lastname, 
                d.designationname 
            FROM accounts a
            LEFT JOIN faculty f ON a.employeenumber = f.employeenumber
            LEFT JOIN designation d ON f.designationid = d.designationid
            WHERE a.username = %s
        """
        cursor.execute(query, (username,))
        user_data = cursor.fetchone()
        
        cursor.close()
        conn.close()

        if not user_data:
            user_data = {"firstname": "Faculty", "lastname": "Member", "designationname": "None"}

        return render_template('faculty/dashboard_faculty.html', user=user_data)

    except Exception as e:
        if conn:
            conn.close()
        return f"<div style='padding: 50px; font-family: Arial;'><h2 style='color: red;'>A Python Error Happened!</h2><p><b>Exact Error Message:</b> {str(e)}</p></div>"

# --- Route for Teaching Assignment ---
@app.route('/faculty_teaching_assignment')
def faculty_teaching_assignment():
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return redirect(url_for('login'))
    return render_template('faculty/teaching_faculty.html')

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
               et.regular_start, et.regular_end, et.parttime_start, et.parttime_end,
               d.nightteachingservice
        FROM faculty f
        JOIN employeetype et ON f.employeetypeid = et.employeetypeid
        LEFT JOIN designation d ON f.designationid = d.designationid
        WHERE f.employeestatus != 'Archive'
    """)
    faculty_map = {}
    for row in (rows or []):
        fnum = row['employeenumber']
        faculty_map[fnum] = {
            'employeenumber': fnum, 'fullname': row['fullname'],
            'employeestatus': row['employeestatus'], 'designationid': row['designationid'],
            'nightteachingservice': row['nightteachingservice'],
            'employeetype': {
                'regularload': row['regularload'], 'parttimeload': row['parttimeload'],
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
    res = scheduler_engine.generate_draft(data.get('program'), int(data.get('yearLevel', 1)), data.get('term'), data.get('curriculum'), data.get('useHistorical', False))
    if not res['success']: return jsonify({'success': False, 'error': res['error']}), 400
    return jsonify({'success': True, 'batch_id': 'DRAFT-NEW-001', 'schedule_data': [_serialize_class(cls) for cls in res['schedule_data']], 'conflict_count': res['conflict_count'], 'violations': res.get('violations', [])})

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

        _archive_status(cur, program, year_level, term, sem_id, 'Draft')
        _insert_batch(cur, _rehydrate_schedule(list(data.get('schedule_data', []))), sem_id, 'Draft', new_v, program, year_level)
        conn.commit(); cur.close(); conn.close()
        return jsonify({'success': True, 'draft_version': new_v})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedule/approve', methods=['POST'])
def api_approve_schedule():
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
        max_v = cur.fetchone()['coalesce']

        _archive_status(cur, program, year_level, term, sem_id, 'Published')
        _archive_status(cur, program, year_level, term, sem_id, 'Draft')
        
        sched_data = _rehydrate_schedule(list(data.get('schedule_data', [])))
        _insert_batch(cur, sched_data, sem_id, 'Published', max_v + 1, program, year_level)
        _insert_batch(cur, sched_data, sem_id, 'Draft', max_v + 2, program, year_level)

        conn.commit(); cur.close(); conn.close()
        return jsonify({'success': True, 'published_version': max_v + 1, 'draft_version': max_v + 2})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/schedule/drafts')
@app.route('/api/schedule/versions')
def api_list_versions():
    try:
        status_filter = "WHERE sv.status = 'Draft'" if 'drafts' in request.path else ""
        rows = query_db(f"""
            SELECT DISTINCT ON (c.programcode, cs.yearlevel, cs.semester, ay.academicyearid, sv.version_number)
                   sv.versionid, sv.version_number, sv.status, sv.datecreated,
                   c.programcode, cs.yearlevel, cs.semester AS term, ay.academicyearid AS acadyear
            FROM   public.schedule_version sv
            JOIN   public.schedule sg ON sv.scheduleid = sg.scheduleid
            JOIN   public.curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   public.curriculum c ON cs.curriculumid = c.curriculumid
            JOIN   public.semester sem ON sg.semesterid = sem.semesterid
            JOIN   public.academicyear ay ON sem.academicyearid = ay.academicyearid
            {status_filter}
            ORDER BY c.programcode, cs.yearlevel, cs.semester, ay.academicyearid, sv.version_number DESC
        """)
        output = []
        for r in (rows or []):
            dt = r['datecreated']
            output.append({
                'versionid': r['versionid'], 'version_number': r['version_number'], 'status': r['status'],
                'datecreated': dt.isoformat() if hasattr(dt, 'isoformat') else str(dt),
                'programcode': r['programcode'], 'yearlevel': r['yearlevel'], 'term': r['term'], 'acadyear': r['acadyear']
            })
        return jsonify(sorted(output, key=lambda x: x['datecreated'], reverse=True))
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/api/schedule/load-draft/<int:version_id>')
def api_load_draft(version_id):
    try:
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT sv.version_number, c.programcode, cs.yearlevel, sem.semestertype AS term, ay.academicyearid AS acadyear, sc.semesterid
            FROM schedule_version sv JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
            JOIN semester sem ON sc.semesterid = sem.semesterid
            JOIN academicyear ay ON sem.academicyearid = ay.academicyearid
            WHERE sv.versionid = %s
        """, (version_id,))
        m = cur.fetchone()
        if not m: return jsonify({'success': False, 'error': 'Not found'}), 404
        cur.execute("""
            SELECT sub.subjectcode AS subject_code, sub.subjectname AS description, cs.lecturehours AS lec_hours,
                   cs.laboratoryhours AS lab_hours, cs.creditunits AS units, c.programcode AS course,
                   sc.employeenumber AS faculty_id, CONCAT(f.lastname, ', ', f.firstname) AS instructor,
                   ss.daydesc, TO_CHAR(ts_s.timevalue, 'HH24:MI') AS start_time, TO_CHAR(ts_e.timevalue, 'HH24:MI') AS end_time,
                   r.roomname AS room, r.roomid
            FROM schedule_version sv JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN schedule_sessions ss ON ss.versionid = sv.versionid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN subject sub ON cs.subjectcode = sub.subjectcode
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
            LEFT JOIN faculty f ON sc.employeenumber = f.employeenumber
            LEFT JOIN room r ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
            WHERE c.programcode = %s AND cs.yearlevel = %s AND sc.semesterid = %s AND sv.version_number = %s
        """, (m['programcode'], m['yearlevel'], m['semesterid'], m['version_number']))
        rows = cur.fetchall(); cur.close(); conn.close()
        _abbr = {'Monday': 'MON', 'Tuesday': 'TUE', 'Wednesday': 'WED', 'Thursday': 'THU', 'Friday': 'FRI', 'Saturday': 'SAT', 'Sunday': 'SUN'}
        sched = [{
            'subject_code': r['subject_code'], 'description': r['description'], 'lec_hours': r['lec_hours'], 'lab_hours': r['lab_hours'],
            'units': r['units'], 'course': r['course'], 'faculty_id': r['faculty_id'], 'instructor': r['instructor'],
            'days': _abbr.get(r['daydesc'], r['daydesc'][:3].upper() if r['daydesc'] else ''), 'day': r['daydesc'],
            'time': f"{_fmt_12h(r['start_time'])} – {_fmt_12h(r['end_time'])}" if r['start_time'] else '',
            'hours': str((r['lec_hours'] or 0) + (r['lab_hours'] or 0)), 'room': r['room'], 'room_id': r['roomid']
        } for r in rows]
        return jsonify({'success': True, 'schedule_data': sched, 'version': m['version_number'], 'context': {'program': m['programcode'], 'yearLevel': m['yearlevel'], 'term': m['term'], 'acadYear': m['acadyear']}})
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