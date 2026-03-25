from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash
from database import get_db_connection, query_db
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template
from datetime import datetime
import csv
import io

# 1. Initialize the Flask App
app = Flask(__name__)
app.secret_key = 'pup_lopez_super_secret_key' # Required for Login Sessions

# --- 1. ROOT ROUTE (Always goes to login first as requested) ---
@app.route('/')
def index():
    return redirect(url_for('login'))

# --- 2. ONE-TIME SETUP ROUTE ---
@app.route('/setup')
def setup():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("TRUNCATE TABLE Accounts RESTART IDENTITY")
        
        # FORCE the method to pbkdf2:sha256 for maximum compatibility
        hashed = generate_password_hash('password123', method='pbkdf2:sha256')
        
        cur.execute("INSERT INTO Accounts (Username, PasswordHash, Role) VALUES (%s, %s, %s)", ('admin', hashed, 'Admin'))
        cur.execute("INSERT INTO Accounts (Username, PasswordHash, Role) VALUES (%s, %s, %s)", ('acadhead', hashed, 'Academic Head'))
        
        conn.commit()
        return "Setup Success! Login with password123"
    except Exception as e:
        return f"Error: {e}"
    finally:
        cur.close(); conn.close()


# --- 3. AUTHENTICATION ROUTES ---
@app.route('/logout')
def logout():
    session.clear() 
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    session.clear() 

    if request.method == 'POST':
        username = str(request.form.get('username')).strip()
        password = str(request.form.get('password')).strip()
        role_selected = request.form.get('role')

        user = query_db("SELECT * FROM Accounts WHERE Username = %s", (username,), one=True)

        if user:
            # Ensure keys are lowercase for consistency
            user_data = {k.lower(): v for k, v in user.items()}
            db_password = str(user_data.get('passwordhash')).strip()
            db_role = user_data.get('role')

            if check_password_hash(db_password, password):
                if db_role == role_selected:
                    session['loggedin'] = True
                    session['username'] = user_data.get('username')
                    session['role'] = db_role
                    
                    if db_role == 'Admin':
                        return redirect(url_for('admin_dashboard'))
                    else:
                        return redirect(url_for('dashboard'))
                else:
                    flash(f"Role mismatch. You are registered as {db_role}")
            else:
                flash("Wrong password. Try again.")
        else:
            flash("Username not found!")
        
        return redirect(url_for('login'))

    return render_template('login.html')

# ==============================================================================
# --- ACADEMIC HEAD SPECIFIC ROUTES ---
# ==============================================================================

# --- ACADEMIC HEAD TABS ROUTES ---

# --- ACADEMIC HEAD DASHBOARD ---
@app.route('/dashboard')
def dashboard():
    if 'loggedin' not in session: 
        return redirect(url_for('login'))
    
    try:
        # We use .get('t', 0) to ensure if the table is empty, it returns 0 instead of crashing
        f_data = query_db("SELECT COUNT(*) as t FROM Faculty", one=True)
        r_data = query_db("SELECT COUNT(*) as t FROM Room", one=True)
        s_data = query_db("SELECT COUNT(*) as t FROM Subject", one=True)

        return render_template('academic/dashboard.html', 
                               faculty_count=f_data['t'] if f_data else 0,
                               room_count=r_data['t'] if r_data else 0,
                               course_count=s_data['t'] if s_data else 0)
    except Exception as e:
        print(f"Dashboard Database Error: {e}")
        # If there is a database error, still show the page but with 0 values
        return render_template('academic/dashboard.html', faculty_count=0, room_count=0, course_count=0)


@app.route('/employee')
def employee():
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    if session.get('role') != 'Academic Head':
        return redirect(url_for('admin_employee'))  # or login

    # rest of your code
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
        employees = [{k.lower(): v for k, v in row.items()} for row in raw_employees] if raw_employees else []
        
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

@app.route('/curriculum')
def curriculum():
    if 'loggedin' not in session: return redirect(url_for('login'))
    return render_template('academic/curriculum.html')

@app.route('/reports')
def reports():
    if 'loggedin' not in session: return redirect(url_for('login'))
    return render_template('academic/reports.html')

# --- ACADEMIC HEAD - EMPLOYEE ACTIONS ---

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
            flash("Employee added successfully!")
        except Exception as e:
            conn.rollback()
            flash(f"Error adding employee: {e}")
        finally:
            cur.close(); conn.close()
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
        
        conn = get_db_connection(); cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE Faculty 
                SET FirstName=%s, MiddleName=%s, LastName=%s, Email=%s, ContactNumber=%s, 
                    SpecializationID=%s, EmployeeTypeID=%s, DesignationID=%s, EmployeeStatus=%s 
                WHERE EmployeeNumber=%s
            """, (f_name, m_name, l_name, email, contact, spec_id, type_id, desig_id, status, emp_num))
            conn.commit()
            flash("Employee details updated successfully!")
        except Exception as e:
            conn.rollback()
            flash(f"Error updating employee: {e}")
        finally:
            cur.close(); conn.close()
        return redirect(url_for('employee'))

@app.route('/archive_employee/<emp_num>')
def archive_employee(emp_num):
    if 'loggedin' not in session: return redirect(url_for('login'))
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO Faculty_Archive (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty WHERE EmployeeNumber = %s
        """, (emp_num,))
        cur.execute("DELETE FROM Faculty WHERE EmployeeNumber = %s", (emp_num,))
        conn.commit()
        flash("Employee archived successfully")
    except Exception as e:
        conn.rollback()
        flash(f"Error archiving employee: {e}")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('employee')) 

@app.route('/bulk_archive', methods=['POST'])
def bulk_archive():
    if 'loggedin' not in session: return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    emp_ids = data.get('employee_ids', [])
    if not emp_ids: return jsonify({'error': 'No employees selected'}), 400
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        placeholders = ', '.join(['%s'] * len(emp_ids))
        cur.execute(f"""
            INSERT INTO Faculty_Archive (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty WHERE EmployeeNumber IN ({placeholders})
        """, emp_ids)
        cur.execute(f"DELETE FROM Faculty WHERE EmployeeNumber IN ({placeholders})", emp_ids)
        conn.commit()
        return jsonify({'success': f'{len(emp_ids)} employees archived successfully'})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()


@app.route('/bulk_import', methods=['POST'])
def bulk_import():
    if 'loggedin' not in session: return redirect(url_for('login'))
    if 'file' not in request.files: return redirect(url_for('employee'))
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'): 
        flash("Invalid file format. Please upload a .csv file.")
        return redirect(url_for('employee'))

    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    csv_input = csv.reader(stream)
    next(csv_input) # Skip header row
    conn = get_db_connection(); cur = conn.cursor()
    try:
        for row_num, row in enumerate(csv_input, 2):
            if len(row) < 10: continue
            try:
                emp_num, last_name, first_name, middle_name, email, contact, spec_id_str, etype_id_str, status, desig_id_str = [r.strip() for r in row[:10]]
                middle_name = middle_name if middle_name else None
                spec_id = int(spec_id_str) if spec_id_str else None
                etype_id = int(etype_id_str) if etype_id_str else None
                desig_id = None if desig_id_str.upper() == 'NULL' or not desig_id_str else int(desig_id_str)
                
                cur.execute("""
                    INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (EmployeeNumber) DO NOTHING
                """, 
                # --- FIXED: The order of variables now matches the SQL INSERT statement ---
                (emp_num, first_name, middle_name, last_name, email, contact, spec_id, etype_id, desig_id, status))

            except ValueError as ve:
                conn.rollback()
                flash(f"Import failed at row {row_num}: Please check if SpecializationID, EmployeeTypeID, and DesignationID are valid numbers. Details: {ve}", "error")
                return redirect(url_for('employee'))
        conn.commit()
        flash("Bulk import completed successfully.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"An error occurred during bulk import: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('employee'))

# --- ACADEMIC ROOMS & SCHEDULE ---

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
        rooms = [{k.lower(): v for k, v in row.items()} for row in raw_rooms] if raw_rooms else []

        return render_template('academic/room.html',
                               total_labs=total_labs['count'] if total_labs else 0,
                               total_lec=total_lec['count'] if total_lec else 0,
                               total_rooms=total_rooms['count'] if total_rooms else 0,
                               total_bldgs=total_bldgs['count'] if total_bldgs else 0,
                               buildings=buildings, rooms=rooms)
    except Exception as e:
        print(f"Room Error: {e}")
        return render_template('academic/room.html', total_labs=0, buildings=[], rooms=[])

@app.route('/schedule')
def schedule():
    if 'loggedin' not in session: return redirect(url_for('login'))
    schedules = query_db("SELECT * FROM Schedule")
    return render_template('academic/schedule.html', schedules=schedules)




# ==============================================================================
# --- ADMIN SPECIFIC ROUTES ---
# ==============================================================================

# --- ADMIN DASHBOARD ---
@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('role') != 'Admin': 
        return redirect(url_for('login'))
    
    try:
        now = datetime.now().strftime("%B %d, %Y")
        u_data = query_db("SELECT COUNT(*) as t FROM Accounts", one=True)
        f_data = query_db("SELECT COUNT(*) as t FROM Faculty", one=True)
        
        return render_template('admin/dashboard_admin.html', 
                               current_date=now,
                               user_count=u_data['t'] if u_data else 0,
                               total=f_data['t'] if f_data else 0)
    except Exception as e:
        print(f"Admin Dashboard Error: {e}")
        return render_template('admin/dashboard_admin.html', user_count=0, total=0, current_date="N/A")


# --- ADMIN EMPLOYEE PAGE ---
@app.route('/admin/employee')
def admin_employee():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    raw = query_db("SELECT f.*, s.SpecializationName, et.TypeName, des.DesignationName FROM Faculty f JOIN Specialization s ON f.SpecializationID = s.SpecializationID JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID LEFT JOIN Designation des ON f.DesignationID = des.DesignationID ORDER BY f.LastName ASC")
    employees = [{k.lower(): v for k, v in row.items()} for row in raw] if raw else []
    
    specializations = query_db("SELECT * FROM Specialization WHERE IsActive = TRUE")
    employee_types = query_db("SELECT * FROM EmployeeType")
    designations = query_db("SELECT * FROM Designation")

    pt = query_db("SELECT COUNT(*) as c FROM Faculty f JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID WHERE et.TypeName = 'Part-Time'", one=True)
    reg = query_db("SELECT COUNT(*) as c FROM Faculty f JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID WHERE et.TypeName = 'Regular'", one=True)
    des = query_db("SELECT COUNT(*) as c FROM Faculty f JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID WHERE et.TypeName = 'Designee'", one=True)

    return render_template('admin/employee_admin.html', 
                           employees=employees, specializations=specializations,
                           employee_types=employee_types, designations=designations,
                           pt=pt['c'] if pt else 0, reg=reg['c'] if reg else 0, 
                           des=des['c'] if des else 0, total=len(employees))

# --- ADMIN - EMPLOYEE ACTIONS (DUPLICATED & CORRECTED) ---

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
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (emp_num, f_name, m_name, l_name, email, contact, spec_id, type_id, desig_id, status))
            conn.commit()
            flash("Employee added successfully!")
        except Exception as e:
            conn.rollback()
            flash(f"Error adding employee: {e}")
        finally:
            cur.close(); conn.close()
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
        
        conn = get_db_connection(); cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE Faculty 
                SET FirstName=%s, MiddleName=%s, LastName=%s, Email=%s, ContactNumber=%s, 
                    SpecializationID=%s, EmployeeTypeID=%s, DesignationID=%s, EmployeeStatus=%s 
                WHERE EmployeeNumber=%s
            """, (f_name, m_name, l_name, email, contact, spec_id, type_id, desig_id, status, emp_num))
            conn.commit()
            flash("Employee details updated successfully!")
        except Exception as e:
            conn.rollback()
            flash(f"Error updating employee: {e}")
        finally:
            cur.close(); conn.close()
        return redirect(url_for('admin_employee'))

# In app.py

@app.route('/admin/archive_employee/<emp_num>')
def admin_archive_employee(emp_num):
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        # --- NEW: Check if faculty is in the schedule table ---
        cur.execute("SELECT 1 FROM Schedule WHERE EmployeeNumber = %s", (emp_num,))
        if cur.fetchone():
            flash(f"Cannot archive Employee {emp_num}. They are currently assigned to a schedule. Please remove them from all schedules first.", "error")
            return redirect(url_for('admin_employee'))
        # --- END NEW ---

        cur.execute("""
            INSERT INTO Faculty_Archive (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty WHERE EmployeeNumber = %s
        """, (emp_num,))
        cur.execute("DELETE FROM Faculty WHERE EmployeeNumber = %s", (emp_num,))
        conn.commit()
        flash("Employee archived successfully", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error archiving employee: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_employee'))

@app.route('/admin/bulk_archive', methods=['POST'])
def admin_bulk_archive():
    if session.get('role') != 'Admin': return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    emp_ids = data.get('employee_ids', [])
    if not emp_ids: return jsonify({'error': 'No employees selected'}), 400
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        placeholders = ', '.join(['%s'] * len(emp_ids))
        
        # --- NEW: Check for any selected employees in the schedule table ---
        cur.execute(f"SELECT EmployeeNumber FROM Schedule WHERE EmployeeNumber IN ({placeholders})", tuple(emp_ids))
        conflicts = [row[0] for row in cur.fetchall()]
        if conflicts:
            conn.rollback()
            return jsonify({'error': f"Cannot archive. The following employees are still in a schedule: {', '.join(conflicts)}"}), 409 # 409 is Conflict status
        # --- END NEW ---

        cur.execute(f"""
            INSERT INTO Faculty_Archive (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty WHERE EmployeeNumber IN ({placeholders})
        """, tuple(emp_ids))
        cur.execute(f"DELETE FROM Faculty WHERE EmployeeNumber IN ({placeholders})", tuple(emp_ids))
        conn.commit()
        return jsonify({'success': f'{len(emp_ids)} employees archived successfully'})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close(); conn.close()

# --- FIXED: Bulk import with better error handling ---
@app.route('/admin/bulk_import', methods=['POST'])
def admin_bulk_import():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    if 'file' not in request.files: 
        flash("No file selected.")
        return redirect(url_for('admin_employee'))

    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'): 
        flash("Invalid file format. Please upload a .csv file.")
        return redirect(url_for('admin_employee'))
        
    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    csv_input = csv.reader(stream)
    next(csv_input) # Skip header
    conn = get_db_connection(); cur = conn.cursor()
    try:
        for i, row in enumerate(csv_input, 2):
            if len(row) < 10: continue
            try:
                # Step 1: Read CSV columns in their physical order
                emp_num, last_name, first_name, middle_name, email, contact, spec_id_str, etype_id_str, status, desig_id_str = [r.strip() for r in row[:10]]
                
                # Clean up the data
                middle_name = middle_name if middle_name else None
                spec_id = int(spec_id_str) if spec_id_str else None
                etype_id = int(etype_id_str) if etype_id_str else None
                desig_id = None if not desig_id_str or desig_id_str.upper() == 'NULL' else int(desig_id_str)
                
                # Step 2: Provide variables to the database in the order specified by the INSERT statement
                cur.execute("""
                    INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (EmployeeNumber) DO NOTHING
                """, 
                # This order matches the database table structure: FirstName, then MiddleName, then LastName
                (emp_num, first_name, middle_name, last_name, email, contact, spec_id, etype_id, desig_id, status))

            except ValueError:
                conn.rollback()
                flash(f"Import Error: Invalid number format in row {i}. Please check ID columns.", "error")
                return redirect(url_for('admin_employee'))
        conn.commit()
        flash("Bulk import completed successfully.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"An error occurred during bulk import: {e}", "error")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_employee'))


# --- NEW: Route to view archived employees ---
@app.route('/admin/archived_employees')
def admin_archived_employees():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    # 1. Fetch Archived Employees
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
    # Convert to lowercase keys to match your HTML template (emp.typename, etc.)
    archived_employees = [dict(row) for row in archives] if archives else []

    # 2. FETCH DATA FOR THE DROPDOWN FILTERS (This was missing!)
    # We select distinct names to populate your <select> menus
    et_rows = query_db("SELECT TypeName as typename FROM EmployeeType ORDER BY TypeName")
    spec_rows = query_db("SELECT SpecializationName as specializationname FROM Specialization ORDER BY SpecializationName")

    return render_template('admin/archived_employees_admin.html', 
                           archived_employees=archived_employees,
                           employee_types=et_rows,
                           specializations=spec_rows)


# --- NEW: Route to restore an employee ---
@app.route('/admin/restore_employee/<int:archive_id>')
def admin_restore_employee(archive_id):
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        # Step 1: Insert from archive back to faculty
        cur.execute("""
            INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
            SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
            FROM Faculty_Archive WHERE FacultyArchiveID = %s
        """, (archive_id,))
        
        # Step 2: Delete from archive
        cur.execute("DELETE FROM Faculty_Archive WHERE FacultyArchiveID = %s", (archive_id,))
        
        conn.commit()
        flash("Employee restored successfully.")
    except Exception as e:
        conn.rollback()
        flash(f"Error restoring employee: {e}")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('admin_archived_employees'))

from flask import request, jsonify

@app.route('/admin/bulk_restore', methods=['POST'])
def admin_bulk_restore():
    if session.get('role') != 'Admin': 
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    
    data = request.get_json()
    archive_ids = data.get('archive_ids', [])
    
    if not archive_ids:
        return jsonify({"success": False, "message": "No employees selected"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for aid in archive_ids:
            # Move from Archive to Faculty
            cur.execute("""
                INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus)
                SELECT EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus
                FROM Faculty_Archive WHERE FacultyArchiveID = %s
            """, (aid,))
            
            # Delete from Archive
            cur.execute("DELETE FROM Faculty_Archive WHERE FacultyArchiveID = %s", (aid,))
        
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/admin/rooms')
def admin_rooms():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    # Fetch Counts
    total_labs = query_db("SELECT COUNT(*) as c FROM Room WHERE RoomType = 'Laboratory'", one=True)
    total_lec = query_db("SELECT COUNT(*) as c FROM Room WHERE RoomType = 'Lecture'", one=True)
    total_rooms = query_db("SELECT COUNT(*) as c FROM Room", one=True)
    total_bldgs = query_db("SELECT COUNT(*) as c FROM Building WHERE IsActive = TRUE", one=True)

    # Fetch List Data - raw_buildings is for the Modal, buildings is for the Sidebar
    raw_buildings = query_db("SELECT BuildingID as buildingid, BuildingName as buildingname FROM Building WHERE IsActive = TRUE ORDER BY BuildingName ASC")
    
    raw_rooms = query_db("SELECT r.*, b.BuildingName FROM Room r JOIN Building b ON r.BuildingID = b.BuildingID ORDER BY r.RoomName ASC")
    rooms = [{k.lower(): v for k, v in row.items()} for row in raw_rooms] if raw_rooms else []

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
        
        # Security check: Check for active schedules again in Python
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
        # 1. UNIQUE CHECK
        cur.execute("SELECT RoomID FROM Room WHERE UPPER(RoomName) = UPPER(%s) AND RoomID != %s", (new_name, r_id))
        if cur.fetchone():
            flash(f"Error: Room '{new_name}' already exists.", "error")
            return redirect(url_for('admin_rooms'))

        # 2. SCHEDULE CHECK (If name is changing)
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
    finally: cur.close(); conn.close()
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

# --- CURRICULUM LIST VIEW ---
# --- ADMIN CURRICULUM SECTION ---

@app.route('/admin/curriculum')
def admin_curriculum():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # --- AUTOMATION SYNC LOGIC ---
        cur.execute("SELECT AcademicYearID FROM AcademicYear WHERE IsActive = TRUE LIMIT 1")
        active_ay_row = cur.fetchone()
        
        if active_ay_row:
            ay_id = active_ay_row['academicyearid'] if isinstance(active_ay_row, dict) else active_ay_row[0]
            base_year_int = int(ay_id[2:4]) 

            cur.execute("""
                INSERT INTO Cohort (ProgramCode, CurriculumID, StartAcademicYear, NumberOfSections)
                SELECT p.ProgramCode,
                    (SELECT c.CurriculumID FROM Curriculum c WHERE c.ProgramCode = p.ProgramCode ORDER BY c.CurriculumYear DESC LIMIT 1),
                    'AY' || LPAD((b.base_year - gs.n)::text, 2, '0') || LPAD((b.base_year - gs.n + 1)::text, 2, '0'), 1
                FROM Programs p JOIN LATERAL generate_series(0, p.NumYearLevel - 1) AS gs(n) ON TRUE
                CROSS JOIN (SELECT %s AS base_year) AS b WHERE p.IsActive = TRUE
                AND EXISTS (SELECT 1 FROM Curriculum c WHERE c.ProgramCode = p.ProgramCode)
                ON CONFLICT (ProgramCode, StartAcademicYear) DO NOTHING
            """, (base_year_int,))

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
    finally:
        cur.close(); conn.close()

    # --- UI DATA FETCHING ---
    programs = query_db("SELECT * FROM Programs WHERE IsActive = TRUE ORDER BY ProgramName")
    selected_program = request.args.get('program_code')
    
    currs_raw = []
    if selected_program == 'All':
        currs_raw = query_db("SELECT c.*, p.ProgramName FROM Curriculum c JOIN Programs p ON c.ProgramCode = p.ProgramCode ORDER BY p.ProgramName ASC, c.CurriculumYear DESC")
    elif selected_program:
        currs_raw = query_db("SELECT * FROM Curriculum WHERE ProgramCode = %s ORDER BY CurriculumYear DESC", (selected_program,))
    curriculums = [{k.lower(): v for k, v in row.items()} for row in currs_raw] if currs_raw else []

    # THE FIX: Two separate lists. One for the Filter (clean), one for the Modal (with IDs)
    unique_codes_raw = query_db("SELECT DISTINCT CurriculumCode FROM Curriculum ORDER BY CurriculumCode DESC")
    unique_codes = [{k.lower(): v for k, v in row.items()} for row in unique_codes_raw] if unique_codes_raw else []

    all_curriculums_raw = query_db("SELECT * FROM Curriculum ORDER BY CurriculumYear DESC")
    all_curriculums = [{k.lower(): v for k, v in row.items()} for row in all_curriculums_raw] if all_curriculums_raw else []

    cohorts_raw = query_db("""
        SELECT c.CohortID, c.ProgramCode, p.ProgramName, c.CurriculumID, curr.CurriculumCode, 
               c.StartAcademicYear, c.NumberOfSections, MAX(sec.YearLevel) as year_level
        FROM Cohort c JOIN Curriculum curr ON c.CurriculumID = curr.CurriculumID
        JOIN Programs p ON c.ProgramCode = p.ProgramCode LEFT JOIN Sections sec ON c.CohortID = sec.CohortID
        GROUP BY c.CohortID, c.ProgramCode, p.ProgramName, c.CurriculumID, curr.CurriculumCode, c.StartAcademicYear, c.NumberOfSections
        ORDER BY c.StartAcademicYear DESC, p.ProgramName ASC
    """)
    cohorts = [{k.lower(): v for k, v in row.items()} for row in cohorts_raw] if cohorts_raw else []

    # Make sure we pass unique_codes to the template!
    return render_template('admin/curriculum_admin.html', programs=programs, curriculums=curriculums, 
                           selected_program=selected_program, cohorts=cohorts, 
                           unique_codes=unique_codes, all_curriculums=all_curriculums)

@app.route('/admin/export/assignments')
def export_assignments():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    # Logic matches your table exactly
    data = query_db("""
        SELECT 
            curr.CurriculumCode, p.ProgramName, 
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
            # Remove commas from names to prevent CSV breakage
            clean_prog = r['programname'].replace(',', '')
            yield f"{r['curriculumcode']},{clean_prog},{r['year_level']},{r['numberofsections']},{r['startacademicyear']}\n"

    return Response(generate(), mimetype='text/csv', 
                    headers={"Content-Disposition": "attachment;filename=Curriculum_Assignments.csv"})

# --- EXPORT ALL CURRICULUMS FOR A PROGRAM ---
# --- EXPORT ALL CURRICULUMS FOR A PROGRAM (OR ALL PROGRAMS) ---
@app.route('/admin/export/program/<program_code>')
def export_program_all(program_code):
    # BASE QUERY
    query = """
        SELECT 
            p.ProgramName,
            c.CurriculumYear, 
            CAST(SUBSTRING(cv.ProgramYearLevel FROM '-(.*)') AS INT) as yearlevel, 
            cv.Semester, 
            cv.SubjectCode, 
            cv."Prerequisite", 
            cv."Co-requisite", 
            cv."Description", 
            cv.LectureHours, 
            cv.LaboratoryHours, 
            cv.CreditUnits, 
            cv.TuitionHours
        FROM Curriculum_View cv
        JOIN Curriculum c ON cv.CurriculumID = c.CurriculumID
        JOIN Programs p ON c.ProgramCode = p.ProgramCode
    """
    params = []

    # NEW: If not 'All', filter by specific program. If 'All', fetch everything.
    if program_code != 'All':
        query += " WHERE c.ProgramCode = %s"
        params.append(program_code)

    query += " ORDER BY p.ProgramName ASC, c.CurriculumYear DESC, yearlevel ASC, cv.Semester ASC"
    raw_data = query_db(query, tuple(params))

    if not raw_data:
        flash("No data found to export.")
        return redirect(request.referrer)

    def generate():
        # Added 'Program' as the first column header
        yield 'Program,Curriculum Year,Year Level,Semester,Subject code,Pre-requisite,Co-requisite,Subject description,Lecture Hours,Lab Hours,Credited Units,Tuition Hours\n'
        for row in raw_data:
            r = {k.lower(): v for k, v in row.items()}
            sem = "1st Sem" if r['semester'] == 'A' else "2nd Sem" if r['semester'] == 'B' else "Summer"
            # Cleaning names for CSV compatibility
            clean_prog = r['programname'].replace(',', '')
            clean_desc = r['description'].replace(',', '').replace('"', '')
            
            yield f"{clean_prog},{r['curriculumyear']},{r['yearlevel']},{sem},{r['subjectcode']},{r.get('prerequisite','-')},{r.get('co-requisite','-')},\"{clean_desc}\",{r['lecturehours']},{r['laboratoryhours']},{r['creditunits']},{r['tuitionhours']}\n"

    filename = f"All_Curriculums_{program_code}.csv"
    return Response(generate(), mimetype='text/csv', headers={"Content-Disposition": f"attachment;filename={filename}"})
    
# --- FIXED EXPORT SINGLE CURRICULUM ---
@app.route('/admin/export/curriculum/<int:curriculum_id>')
def export_single_curriculum(curriculum_id):
    # Get filters from the URL parameters sent by your JS function
    year_level = request.args.get('year', '0')
    semester = request.args.get('semester', 'All')

    # Query exactly like your View page to ensure data consistency
    query = """
        SELECT 
            Semester, 
            SubjectCode, 
            "Prerequisite" as prerequisite, 
            "Co-requisite" as corequisite, 
            "Description" as description, 
            LectureHours, 
            LaboratoryHours, 
            CreditUnits, 
            TuitionHours,
            CAST(SUBSTRING(ProgramYearLevel FROM '-(.*)') AS INT) as yearlevel
        FROM Curriculum_View
        WHERE CurriculumID = %s
    """
    params = [curriculum_id]

    # Apply same filters used in the UI
    if year_level != '0':
        query += " AND CAST(SUBSTRING(ProgramYearLevel FROM '-(.*)') AS INT) = %s"
        params.append(int(year_level))
    if semester != 'All':
        query += " AND Semester = %s"
        params.append(semester)

    query += " ORDER BY yearlevel ASC, Semester ASC"
    raw_data = query_db(query, tuple(params))

    # If the list is empty, return to the page with a notice
    if not raw_data:
        flash("No data found for these filters.")
        return redirect(request.referrer)

    def generate():
        # CSV Headers
        yield 'Year Level,Semester,Subject code,Pre-requisite,Co-requisite,Subject description,Lecture Hours,Lab Hours,Credited Units,Tuition Hours\n'
        for row in raw_data:
            # Ensure keys are lowercase so Jinja-style r['semester'] works
            r = {k.lower(): v for k, v in row.items()}
            sem_label = "1st Sem" if r['semester'] == 'A' else "2nd Sem" if r['semester'] == 'B' else "Summer"
            
            # Format rows safely (handling potential None values with 'or -')
            yield f"{r['yearlevel']},{sem_label},{r['subjectcode']},{r.get('prerequisite') or '-'},{r.get('corequisite') or '-'},\"{r['description']}\",{r['lecturehours']},{r['laboratoryhours']},{r['creditunits']},{r['tuitionhours']}\n"

    # Fetch year for a clean filename
    info = query_db("SELECT CurriculumYear FROM Curriculum WHERE CurriculumID = %s", (curriculum_id,), one=True)
    filename_year = info['curriculumyear'] if info else "Export"

    return Response(generate(), mimetype='text/csv', 
                    headers={"Content-Disposition": f"attachment;filename=Curriculum_{filename_year}.csv"})
    
@app.route('/admin/curriculum/import', methods=['POST'])
def import_curriculum():
    file = request.files.get('file')
    prog_code = request.form.get('program_code')
    curr_year = request.form.get('curriculum_year') # e.g., 2024-2025
    
    ui_year = request.form.get('year_level')
    ui_sem = request.form.get('semester')

    try:
        idx = {}
        for i in range(10):
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

    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # STRICT RULE: Check if curriculum already exists for this program
        cur.execute("SELECT 1 FROM Curriculum WHERE ProgramCode = %s AND CurriculumYear = %s", (prog_code, curr_year))
        if cur.fetchone():
            flash(f"Import Blocked: Curriculum for {prog_code} C.Y {curr_year} already exists in the system.")
            return redirect(request.referrer)

        def get_val(row, key): return row[idx[key]].strip() if key in idx and idx[key] < len(row) else ""
        def parse_int(val):
            try: return int(float(val)) if val else 0
            except: return 0

        # Resolve Curriculum ID
        csv_cc = get_val(csv_data[0], 'cc') if 'cc' in idx else None
        if csv_cc:
            curr_code_str = csv_cc[:6]
        else:
            years = curr_year.split('-')
            curr_code_str = f"CY{years[0][-2:]}{years[1][-2:]}" if len(years) == 2 else "CY0000"

        # Create new curriculum
        cur.execute("""
            INSERT INTO Curriculum (CurriculumCode, ProgramCode, CurriculumYear) 
            VALUES (%s, %s, %s) RETURNING CurriculumID
        """, (curr_code_str, prog_code, curr_year))
        target_curr_id = cur.fetchone()[0]

        # Pass 1: Upsert Subjects
        for row in csv_data:
            if not row: continue
            s_code = get_val(row, 'sc')
            s_name = get_val(row, 'sn')
            if not s_code: continue
            
            cur.execute("""
                INSERT INTO Subject (SubjectCode, SubjectName, CreditUnits, LectureHours, LaboratoryHours) 
                VALUES (%s,%s,%s,%s,%s) 
                ON CONFLICT (SubjectCode) DO UPDATE SET 
                SubjectName = EXCLUDED.SubjectName,
                CreditUnits = EXCLUDED.CreditUnits
            """, (s_code, s_name or s_code, parse_int(get_val(row, 'u')), parse_int(get_val(row, 'lc')), parse_int(get_val(row, 'lb'))))

        # Pass 2: Link Subjects to Curriculum
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

            # Handle Prerequisites/Corequisites
            for key, table, col in [('pre', 'SubjectPrerequisite', 'PrerequisiteCode'), ('co', 'SubjectCorequisite', 'CorequisiteCode')]:
                req_val = get_val(row, key)
                if req_val and req_val.upper() not in ['NONE', '-', 'N/A']:
                    for r_code in req_val.split(','):
                        clean_r = r_code.strip()
                        if clean_r:
                            cur.execute(f"INSERT INTO {table} (SubjectCode, {col}) VALUES (%s, %s) ON CONFLICT DO NOTHING", (s_code, clean_r))

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
    cur = conn.cursor()
    
    try:
        # 1. SECURITY CHECK: Is it used in any Schedule?
        cur.execute("""
            SELECT COUNT(*) FROM Schedule s
            JOIN CurriculumSubject cs ON s.CurriculumSubjectID = cs.CurriculumSubjectID
            WHERE cs.CurriculumID = %s
        """, (curriculum_id,))
        if cur.fetchone()[0] > 0:
            flash("Deletion Denied: This curriculum is currently linked to one or more active class schedules.")
            return redirect(request.referrer)

        # 2. SECURITY CHECK: Is it used in any student sections (via Cohort)?
        cur.execute("""
            SELECT COUNT(*) FROM Sections sec
            JOIN Cohort c ON sec.CohortID = c.CohortID
            WHERE c.CurriculumID = %s
        """, (curriculum_id,))
        if cur.fetchone()[0] > 0:
            flash("Deletion Denied: This curriculum is currently assigned to active student batches/sections.")
            return redirect(request.referrer)

        # 3. CASCADE DELETE: Delete subjects first, then the curriculum parent
        cur.execute("DELETE FROM CurriculumSubject WHERE CurriculumID = %s", (curriculum_id,))
        cur.execute("DELETE FROM Curriculum WHERE CurriculumID = %s", (curriculum_id,))
        
        conn.commit()
        flash("Curriculum and associated subjects successfully removed.")
        
    except Exception as e:
        conn.rollback()
        print(f"Delete Error: {str(e)}")
        flash("Error: Could not delete. Ensure no other data (like student batches) depends on this curriculum.")
    finally:
        cur.close(); conn.close()
        
    return redirect(url_for('admin_curriculum'))

# NEW: Route to handle Adding/Editing Curriculum Assignment (Cohort)
@app.route('/admin/curriculum/assign', methods=['POST'])
def assign_curriculum():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    cohort_id = request.form.get('cohort_id')
    curr_id = request.form.get('curriculum_id') # Safely grabs from the hidden input
    sections_count = request.form.get('number_of_sections')

    if not curr_id or curr_id == "":
        flash("Error: No curriculum version selected.")
        return redirect(url_for('admin_curriculum'))

    conn = get_db_connection(); cur = conn.cursor()
    try:
        if cohort_id and cohort_id.strip() != "":
            # Edit Mode
            cur.execute("""
                UPDATE Cohort 
                SET CurriculumID = %s, NumberOfSections = %s 
                WHERE CohortID = %s
            """, (int(curr_id), int(sections_count), int(cohort_id)))
            flash("Assignment updated successfully.")
        else:
            # Add Mode
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

@app.route('/admin/curriculum/autogenerate', methods=['POST'])
def autogenerate_assignments():
    if session.get('role') != 'Admin': return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # 1. GET ACTIVE ACADEMIC YEAR (Basis for math)
        cur.execute("SELECT AcademicYearID, YearStart FROM AcademicYear WHERE IsActive = TRUE LIMIT 1")
        active_ay = cur.fetchone()
        if not active_ay:
            flash("Error: No Active Academic Year found. Please set one in Settings.")
            return redirect(url_for('admin_curriculum'))

        ay_id = active_ay['academicyearid'] if isinstance(active_ay, dict) else active_ay[0]
        ay_start = int(active_ay['yearstart'] if isinstance(active_ay, dict) else active_ay[1])

        # 2. GET ALL ACTIVE PROGRAMS (Now fetching ProgramDuration directly)
        cur.execute("SELECT ProgramCode, ProgramDuration FROM Programs WHERE IsActive = TRUE")
        programs = cur.fetchall()

        for prog in programs:
            p_code = prog['programcode'] if isinstance(prog, dict) else prog[0]
            # Use the DB column we just updated in Step 1
            p_duration = int(prog['programduration'] if isinstance(prog, dict) else prog[1])

            # THE "PUSH" OPERATION: Loop based on the Duration (e.g. 1 to 3 or 1 to 4)
            for yr in range(1, p_duration + 1):
                
                # Calculate the start year for this specific Year Level
                target_start_year_int = ay_start - (yr - 1)
                target_ay_string = f"AY{str(target_start_year_int)[2:]}{str(target_start_year_int + 1)[2:]}"

                # Find if a Batch (Cohort) exists starting in that year
                cur.execute("SELECT CohortID, NumberOfSections FROM Cohort WHERE ProgramCode = %s AND StartAcademicYear = %s", (p_code, target_ay_string))
                cohort = cur.fetchone()

                target_cohort_id = None
                num_sections = 1

                if not cohort:
                    # FRESHMEN LOGIC: Auto-assign the NEWEST available curriculum
                    if yr == 1:
                        cur.execute("SELECT CurriculumID FROM Curriculum WHERE ProgramCode = %s ORDER BY CurriculumYear DESC LIMIT 1", (p_code,))
                        latest_curr = cur.fetchone()
                        
                        if latest_curr:
                            curr_id = latest_curr['curriculumid'] if isinstance(latest_curr, dict) else latest_curr[0]
                            cur.execute("""
                                INSERT INTO Cohort (ProgramCode, CurriculumID, StartAcademicYear, NumberOfSections)
                                VALUES (%s, %s, %s, 1) RETURNING CohortID
                            """, (p_code, curr_id, target_ay_string))
                            res = cur.fetchone()
                            target_cohort_id = res['cohortid'] if isinstance(res, dict) else res[0]
                    else:
                        continue # If no cohort exists for older years, skip
                else:
                    target_cohort_id = cohort['cohortid'] if isinstance(cohort, dict) else cohort[0]
                    num_sections = cohort['numberofsections'] if isinstance(cohort, dict) else cohort[1]

                # GENERATE SECTIONS FOR THIS YEAR LEVEL
                # This automatically increments the section's year level for the batch
                if target_cohort_id:
                    for i in range(1, num_sections + 1):
                        sec_letter = chr(64 + i) # 1=A, 2=B...
                        sec_name = f"{p_code} {yr}-{sec_letter}"
                        
                        cur.execute("""
                            INSERT INTO Sections (CohortID, YearLevel, SectionName, IsActive)
                            VALUES (%s, %s, %s, TRUE)
                            ON CONFLICT (CohortID, YearLevel, SectionName) DO NOTHING
                        """, (target_cohort_id, yr, sec_name))

        conn.commit()
        flash("Academic Year Transition Successful: Year levels promoted and curricula inherited.")

    except Exception as e:
        conn.rollback()
        print(f"Auto-Gen Error: {str(e)}")
        flash("System Error: Could not complete auto-generation.")
    finally:
        cur.close(); conn.close()

    return redirect(url_for('admin_curriculum'))

# THIS IS YOUR VIEW ROUTE - IT IS 100% UNTOUCHED
@app.route('/admin/curriculum/view/<int:curriculum_id>')
def admin_view_curriculum(curriculum_id):
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    year_level = request.args.get('year', '0') 
    semester = request.args.get('semester', 'All')

    info = query_db("""
        SELECT c.*, p.ProgramName, p.ProgramCode 
        FROM Curriculum c 
        JOIN Programs p ON c.ProgramCode = p.ProgramCode 
        WHERE c.CurriculumID = %s
    """, (curriculum_id,), one=True)

    if not info:
        return redirect(url_for('admin_curriculum'))

    programs = query_db("SELECT * FROM Programs WHERE IsActive = TRUE ORDER BY ProgramName")
    
    other_curriculums = query_db("""
        SELECT CurriculumID, CurriculumYear, CurriculumCode 
        FROM Curriculum 
        WHERE ProgramCode = %s 
        ORDER BY CurriculumYear DESC
    """, (info['programcode'],))

    query = """
        SELECT 
            SubjectCode as subjectcode, 
            "Prerequisite" as prerequisite, 
            "Co-requisite" as corequisite, 
            "Description" as subjectname, 
            LectureHours as lecturehours, 
            LaboratoryHours as laboratoryhours, 
            CreditUnits as creditunits, 
            TuitionHours as tuitionhours,
            Semester as semester,
            CAST(SUBSTRING(ProgramYearLevel FROM '-(.*)') AS INT) as yearlevel
        FROM Curriculum_View
        WHERE CurriculumID = %s
    """
    params = [curriculum_id]
    
    if year_level != '0':
        query += " AND CAST(SUBSTRING(ProgramYearLevel FROM '-(.*)') AS INT) = %s"
        params.append(int(year_level))
    if semester != 'All':
        query += " AND Semester = %s"
        params.append(semester)

    query += " ORDER BY yearlevel ASC, semester ASC"
    subjects = query_db(query, tuple(params))

    return render_template('admin/curriculum_view_admin.html', 
                           info=info, subjects=subjects, 
                           programs=programs, other_curriculums=other_curriculums,
                           curr_year=year_level, curr_sem=semester)

# --- NEXT SECTION ---
@app.route('/admin/schedule')
def admin_schedule():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    return render_template('admin/schedule_admin.html')

@app.route('/admin/reports')
def admin_reports():
# ... (rest of your code)
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    return render_template('admin/reports_admin.html')

@app.route('/admin/user-management')
def admin_users():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    return render_template('admin/user_management_admin.html')

@app.route('/admin/settings')
def admin_settings():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    return render_template('admin/settings_admin.html')


# --- MAIN EXECUTION ---
if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)