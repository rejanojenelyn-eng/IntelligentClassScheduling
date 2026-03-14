from flask import Flask, render_template, request, redirect, url_for
from database import get_db_connection, query_db
import csv
import io

# 1. Initialize the Flask App
app = Flask(__name__)

# --- DASHBOARD ROUTE ---
@app.route('/')
@app.route('/dashboard')
def dashboard():
    try:
        faculty = query_db("SELECT COUNT(*) as total FROM Instructor", one=True)
        rooms = query_db("SELECT COUNT(*) as total FROM Room", one=True)
        courses = query_db("SELECT COUNT(*) as total FROM Course", one=True)

        return render_template('academic/dashboard.html', 
                               faculty_count=faculty['total'] if faculty else 0,
                               room_count=rooms['total'] if rooms else 0,
                               course_count=courses['total'] if courses else 0)
    except Exception as e:
        print(f"Dashboard Error: {e}")
        return render_template('academic/dashboard.html', faculty_count=0, room_count=0, course_count=0)


# --- EMPLOYEE PAGE ROUTE ---
@app.route('/employee')
def employee():
    try:
        query = """
            SELECT 
                i.EmployeeNumber, i.FirstName, i.LastName, i.Email, i.ContactNumber, 
                i.EmployeeStatus, d.DepartmentName, et.TypeName, des.DesignationName,
                i.DepartmentID, i.EmployeeTypeID, i.DesignationID
            FROM Instructor i
            JOIN Department d ON i.DepartmentID = d.DepartmentID
            JOIN EmployeeType et ON i.EmployeeTypeID = et.EmployeeTypeID
            LEFT JOIN Designation des ON i.DesignationID = des.DesignationID
            ORDER BY i.LastName ASC
        """
        employees = query_db(query)

        departments = query_db("SELECT DepartmentID, DepartmentName FROM Department WHERE IsActive = TRUE")
        employee_types = query_db("SELECT EmployeeTypeID, TypeName FROM EmployeeType")
        designations = query_db("SELECT DesignationID, DesignationName FROM Designation")

        perm_data = query_db("SELECT COUNT(*) as count FROM Instructor WHERE EmployeeStatus = 'Permanent'", one=True)
        pt_data = query_db("SELECT COUNT(*) as count FROM Instructor WHERE EmployeeStatus = 'Part-Time'", one=True)
        temp_data = query_db("SELECT COUNT(*) as count FROM Instructor WHERE EmployeeStatus = 'Temporary'", one=True)

        return render_template('academic/employee.html', 
                               employees=employees, departments=departments,
                               employee_types=employee_types, designations=designations,
                               reg=perm_data['count'] if perm_data else 0, 
                               pt=pt_data['count'] if pt_data else 0, 
                               temp=temp_data['count'] if temp_data else 0, 
                               total=len(employees) if employees else 0)
    except Exception as e:
        print(f"Employee Page Error: {e}")
        return render_template('academic/employee.html', employees=[])


# --- ADD NEW EMPLOYEE ACTION ---
@app.route('/add_employee', methods=['POST'])
def add_employee():
    if request.method == 'POST':
        emp_num = request.form['employee_number']
        f_name = request.form['first_name']
        l_name = request.form['last_name']
        email = request.form['email']
        contact = request.form['contact']
        dept_id = request.form['department_id']
        type_id = request.form['type_id']
        status = request.form['status']

        desig_id = request.form.get('designation_id')
        if not desig_id:
            desig_id = None

        try:
            conn = get_db_connection()
            cur = conn.cursor()
            insert_query = """
                INSERT INTO Instructor 
                (EmployeeNumber, FirstName, LastName, Email, ContactNumber, DepartmentID, EmployeeTypeID, DesignationID, EmployeeStatus) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cur.execute(insert_query, (emp_num, f_name, l_name, email, contact, dept_id, type_id, desig_id, status))
            conn.commit()
            cur.close()
            conn.close()
            return redirect(url_for('employee'))
        except Exception as e:
            print(f"Insert Error: {e}")
            return f"An error occurred: {e}", 500


# --- EDIT EMPLOYEE ACTION ---
@app.route('/edit_employee', methods=['POST'])
def edit_employee():
    if request.method == 'POST':
        emp_num = request.form['employee_number']
        f_name = request.form['first_name']
        l_name = request.form['last_name']
        email = request.form['email']
        contact = request.form['contact']
        dept_id = request.form['department_id']
        type_id = request.form['type_id']
        status = request.form['status']
        
        desig_id = request.form.get('designation_id')
        if not desig_id: desig_id = None

        try:
            conn = get_db_connection()
            cur = conn.cursor()
            update_query = """
                UPDATE Instructor 
                SET FirstName=%s, LastName=%s, Email=%s, ContactNumber=%s, 
                    DepartmentID=%s, EmployeeTypeID=%s, DesignationID=%s, EmployeeStatus=%s
                WHERE EmployeeNumber=%s
            """
            cur.execute(update_query, (f_name, l_name, email, contact, dept_id, type_id, desig_id, status, emp_num))
            conn.commit()
            cur.close()
            conn.close()
            return redirect(url_for('employee'))
        except Exception as e:
            print(f"Update Error: {e}")
            return f"An error occurred during update: {e}", 500


# --- DELETE EMPLOYEE ACTION ---
@app.route('/delete_employee/<emp_num>')
def delete_employee(emp_num):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM Instructor WHERE EmployeeNumber = %s", (emp_num,))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('employee'))
    except Exception as e:
        print(f"Delete Error: {e}")
        return f"Cannot delete instructor. Error: {e}", 500


# --- BULK IMPORT EXCEL/CSV ACTION ---
@app.route('/bulk_import', methods=['POST'])
def bulk_import():
    if 'file' not in request.files:
        return redirect(url_for('employee'))
    
    file = request.files['file']
    if file.filename == '':
        return redirect(url_for('employee'))
        
    if file and file.filename.endswith('.csv'):
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.reader(stream)
        next(csv_input)  # Skip the header row
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        for row in csv_input:
            if len(row) < 9:
                continue # Skip empty or invalid rows
            try:
                # CSV Format: EmpNum, First, Last, Email, Contact, DeptID, TypeID, DesigID, Status
                desig = row[7].strip()
                desig_id = desig if desig else None
                
                cur.execute("""
                    INSERT INTO Instructor 
                    (EmployeeNumber, FirstName, LastName, Email, ContactNumber, DepartmentID, EmployeeTypeID, DesignationID, EmployeeStatus) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (EmployeeNumber) DO NOTHING
                """, (row[0].strip(), row[1].strip(), row[2].strip(), row[3].strip(), row[4].strip(), row[5].strip(), row[6].strip(), desig_id, row[8].strip()))
            except Exception as e:
                print(f"Skipping row due to error: {e}")
                
        conn.commit()
        cur.close()
        conn.close()
        
    return redirect(url_for('employee'))


# --- ROOMS & SCHEDULE ---
@app.route('/room')
def room():
    rooms = query_db("SELECT * FROM Room ORDER BY RoomName ASC")
    return render_template('academic/room.html', rooms=rooms)

@app.route('/schedule')
def schedule():
    schedules = query_db("SELECT * FROM Schedule")
    return render_template('academic/schedule.html', schedules=schedules)


if __name__ == '__main__':
    app.run(debug=True)