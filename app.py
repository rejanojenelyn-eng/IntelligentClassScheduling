from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash
from database import get_db_connection, query_db
from werkzeug.security import generate_password_hash, check_password_hash
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

# --- ACADEMIC HEAD SPECIFIC ROUTES ---
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

# --- EMPLOYEE ACTIONS ---

@app.route('/add_employee', methods=['POST'])
def add_employee():
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
        except Exception as e:
            conn.rollback()
            print(f"Insert Error: {e}")
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
        except Exception as e:
            conn.rollback()
            print(f"Update Error: {e}")
        finally:
            cur.close(); conn.close()
        return redirect(url_for('employee'))

@app.route('/delete_employee/<emp_num>')
def delete_employee(emp_num):
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("DELETE FROM Faculty WHERE EmployeeNumber = %s", (emp_num,))
        conn.commit()
    except Exception as e:
        print(f"Delete Error: {e}")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('employee'))

@app.route('/bulk_delete', methods=['POST'])
def bulk_delete():
    conn = None
    try:
        data = request.get_json()
        emp_numbers_to_delete = data.get('employee_ids', [])
        if not emp_numbers_to_delete: return jsonify({'error': 'No employees selected'}), 400
        conn = get_db_connection(); cur = conn.cursor()
        placeholders = ', '.join(['%s'] * len(emp_numbers_to_delete))
        cur.execute(f"DELETE FROM Faculty WHERE EmployeeNumber IN ({placeholders})", emp_numbers_to_delete)
        conn.commit()
        return jsonify({'success': f'{cur.rowcount} employees deleted successfully'})
    except Exception as e:
        if conn: conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn: cur.close(); conn.close()

@app.route('/bulk_import', methods=['POST'])
def bulk_import():
    if 'file' not in request.files: return redirect(url_for('employee'))
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'): return "Invalid file", 400
    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    csv_input = csv.reader(stream)
    next(csv_input)
    conn = get_db_connection(); cur = conn.cursor()
    try:
        for row in csv_input:
            if len(row) < 10: continue
            emp_num, last_name, first_name, middle_name, email, contact, spec_id_str, etype_id_str, status, desig_id_str = [r.strip() for r in row[:10]]
            middle_name = middle_name if middle_name else None
            spec_id = int(spec_id_str) if spec_id_str else None
            etype_id = int(etype_id_str) if etype_id_str else None
            desig_id = None if desig_id_str.upper() == 'NULL' or not desig_id_str else int(desig_id_str)
            cur.execute("""
                INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (EmployeeNumber) DO NOTHING
            """, (emp_num, first_name, middle_name, last_name, email, contact, spec_id, etype_id, desig_id, status))
        conn.commit()
    except Exception as e:
        conn.rollback(); print(e)
    finally:
        cur.close(); conn.close()
    return redirect(url_for('employee'))

# --- ROOMS & SCHEDULE ---

@app.route('/room')
def room():
    if 'loggedin' not in session: return redirect(url_for('login'))
    try:
        total_labs = query_db("SELECT COUNT(*) as count FROM Room WHERE RoomType = 'Laboratory'", one=True)
        total_lec = query_db("SELECT COUNT(*) as count FROM Room WHERE RoomType = 'Lecture'", one=True)
        total_rooms = query_db("SELECT COUNT(*) as count FROM Room", one=True)
        total_bldgs = query_db("SELECT COUNT(*) as count FROM Building WHERE IsActive = TRUE", one=True)
        buildings = query_db("SELECT BuildingName AS buildingname FROM Building WHERE IsActive = TRUE ORDER BY BuildingName ASC")
        
        # FIX: Explicitly lowercase keys for the template
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



# --- ADMIN SPECIFIC ROUTES ---
# --- ADMIN TABS ROUTES ---
# --- ADMIN DASHBOARD ---
@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('role') != 'Admin': 
        return redirect(url_for('login'))
    
    try:
        # Admin usually wants to see total users too
        u_data = query_db("SELECT COUNT(*) as t FROM Accounts", one=True)
        f_data = query_db("SELECT COUNT(*) as t FROM Faculty", one=True)
        
        return render_template('admin/dashboard_admin.html', 
                               user_count=u_data['t'] if u_data else 0,
                               total=f_data['t'] if f_data else 0)
    except Exception as e:
        print(f"Admin Dashboard Error: {e}")
        return render_template('admin/dashboard_admin.html', user_count=0, total=0)

@app.route('/admin/employee')
def admin_employee():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    # 1. Fetch the actual employee data
    raw = query_db("SELECT f.*, s.SpecializationName, et.TypeName, des.DesignationName FROM Faculty f JOIN Specialization s ON f.SpecializationID = s.SpecializationID JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID LEFT JOIN Designation des ON f.DesignationID = des.DesignationID ORDER BY f.LastName ASC")
    employees = [{k.lower(): v for k, v in row.items()} for row in raw] if raw else []
    
    # 2. Fetch the metadata for dropdowns
    specializations = query_db("SELECT * FROM Specialization WHERE IsActive = TRUE")
    employee_types = query_db("SELECT * FROM EmployeeType")
    designations = query_db("SELECT * FROM Designation")

    # 3. Fetch counts for the Stats Cards
    pt = query_db("SELECT COUNT(*) as c FROM Faculty f JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID WHERE et.TypeName = 'Part-Time'", one=True)
    reg = query_db("SELECT COUNT(*) as c FROM Faculty f JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID WHERE et.TypeName = 'Regular'", one=True)
    des = query_db("SELECT COUNT(*) as c FROM Faculty f JOIN EmployeeType et ON f.EmployeeTypeID = et.EmployeeTypeID WHERE et.TypeName = 'Designee'", one=True)

    return render_template('admin/employee_admin.html', 
                           employees=employees, specializations=specializations,
                           employee_types=employee_types, designations=designations,
                           pt=pt['c'] if pt else 0, reg=reg['c'] if reg else 0, 
                           des=des['c'] if des else 0, total=len(employees))

@app.route('/admin/rooms')
def admin_rooms():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    
    # Fetch Counts
    total_labs = query_db("SELECT COUNT(*) as c FROM Room WHERE RoomType = 'Laboratory'", one=True)
    total_lec = query_db("SELECT COUNT(*) as c FROM Room WHERE RoomType = 'Lecture'", one=True)
    total_rooms = query_db("SELECT COUNT(*) as c FROM Room", one=True)
    total_bldgs = query_db("SELECT COUNT(*) as c FROM Building WHERE IsActive = TRUE", one=True)

    # Fetch List Data
    buildings = query_db("SELECT BuildingName AS buildingname FROM Building WHERE IsActive = TRUE ORDER BY BuildingName ASC")
    raw_rooms = query_db("SELECT r.*, b.BuildingName FROM Room r JOIN Building b ON r.BuildingID = b.BuildingID ORDER BY r.RoomName ASC")
    rooms = [{k.lower(): v for k, v in row.items()} for row in raw_rooms] if raw_rooms else []

    return render_template('admin/rooms_admin.html',
                           total_labs=total_labs['c'], total_lec=total_lec['c'], 
                           total_rooms=total_rooms['c'], total_bldgs=total_bldgs['c'], 
                           buildings=buildings, rooms=rooms)

@app.route('/admin/schedule')
def admin_schedule():
    if session.get('role') != 'Admin': return redirect(url_for('login'))
    return render_template('admin/schedule_admin.html')

@app.route('/admin/reports')
def admin_reports():
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