####################################################################################################
# CODE TO COPY INTO THE GROUPMATE'S app.py  (generated from Jenelyn's app.py)
# This file is NOT imported by the app. It is only a copy-paste reference. Delete it after merging.
#
# HOW TO USE
#   STEP 0  Copy these files/folders from Jenelyn's project into the groupmate's project:
#           makeup_requests.py, password_change.py, password_reset.py,
#           teaching_assignment_export.py, weekly_schedule_export.py,
#           templates/forgot_password.html, templates/setup/  (whole folder),
#           templates/_schedule_export_modal.html
#   STEP 1  Add the imports in PART 1 at the top of app.py.
#   STEP 2  Paste every block in PART 2 (brand-new code). Each block says where to paste it.
#   STEP 3  PART 3: replace each whole function with this version (Ctrl+F the 'def' line).
#   STEP 4  PART 4: these functions were ALSO changed by the groupmate. Do NOT replace them
#           blindly - apply only the '+' / '-' lines shown in the diff, keeping her other changes.
####################################################################################################

####################################################################################################
# PART 1 - IMPORTS (add to the import lines at the top of app.py)
####################################################################################################
# Change the flask import line so it also includes make_response:
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash, Response, make_response
import psycopg2.errors
import makeup_requests as _makeup
import password_change as _pwchange
import password_reset as _pwreset
import teaching_assignment_export as _ta_export
import weekly_schedule_export as _ws_export

####################################################################################################
# PART 2 - BRAND-NEW CODE (she does not have any of this)
####################################################################################################

#===================================================================================================
# 1. ADD / EDIT EMPLOYEE WITHOUT PAGE RELOAD
#===================================================================================================

# ---- _friendly_faculty_db_error   (my app.py lines 28-42)
# PASTE near the top, after the imports
def _friendly_faculty_db_error(e):
    """Map a raw Faculty INSERT/UPDATE failure to a message safe to show a
    user — never the raw Postgres exception text. faculty has exactly two
    uniqueness constraints that can fire here — verified directly against
    the live database (pg_constraint), since the live schema's actual
    constraint names (pk_faculty / uq_faculty_email) differ from the
    faculty_pkey / faculty_email_key names in the checked-in schema.sql."""
    if isinstance(e, psycopg2.errors.UniqueViolation):
        constraint = getattr(getattr(e, 'diag', None), 'constraint_name', '') or ''
        if constraint in ('pk_faculty', 'faculty_pkey'):
            return "This Faculty Number is already registered. Please use a different faculty number."
        if constraint in ('uq_faculty_email', 'faculty_email_key'):
            return "This email address is already registered. Please use a different email address."
        return "Unable to save faculty. This record conflicts with an existing one."
    return "Unable to save faculty. Please check the information and try again."

# ---- api_add_employee   (my app.py lines 2343-2376)
# PASTE AFTER: def edit_employee(...)  (the end of that function)
@app.route('/api/add_employee', methods=['POST'])
def api_add_employee():
    if 'loggedin' not in session:
        return jsonify({'success': False, 'error': 'Not logged in.'}), 401
    data = request.get_json() or {}
    emp_num  = (data.get('employee_number') or '').strip()
    f_name   = (data.get('first_name') or '').strip()
    m_name   = (data.get('middle_name') or '').strip() or None
    l_name   = (data.get('last_name') or '').strip()
    email    = (data.get('email') or '').strip()
    contact  = (data.get('contact') or '').strip()
    spec_id  = data.get('specialization_id')
    type_id  = data.get('type_id')
    status   = data.get('status')
    desig_id = data.get('designation_id') or None
    if not all([emp_num, f_name, l_name, email, contact, spec_id, type_id, status]):
        return jsonify({'success': False, 'error': 'Please fill in all required fields.'}), 400
    if not contact.isdigit():
        return jsonify({'success': False, 'error': 'Contact Number must contain numbers only.'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (emp_num, f_name, m_name, l_name, email, contact, spec_id, type_id, desig_id, status))
        conn.commit()
        flash("Employee added successfully!", "success")
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': _friendly_faculty_db_error(e)}), 409
    finally:
        cur.close(); conn.close()

# ---- api_edit_employee   (my app.py lines 2378-2414)
# PASTE AFTER: def edit_employee(...)  (the end of that function)
@app.route('/api/edit_employee', methods=['POST'])
def api_edit_employee():
    if 'loggedin' not in session:
        return jsonify({'success': False, 'error': 'Not logged in.'}), 401
    data = request.get_json() or {}
    emp_num  = (data.get('employee_number') or '').strip()
    f_name   = (data.get('first_name') or '').strip()
    m_name   = (data.get('middle_name') or '').strip() or None
    l_name   = (data.get('last_name') or '').strip()
    email    = (data.get('email') or '').strip()
    contact  = (data.get('contact') or '').strip()
    spec_id  = data.get('specialization_id')
    type_id  = data.get('type_id')
    status   = data.get('status')
    desig_id = data.get('designation_id') or None
    if not all([emp_num, f_name, l_name, email, contact, spec_id, type_id, status]):
        return jsonify({'success': False, 'error': 'Please fill in all required fields.'}), 400
    if not contact.isdigit():
        return jsonify({'success': False, 'error': 'Contact Number must contain numbers only.'}), 400

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
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': _friendly_faculty_db_error(e)}), 409
    finally:
        cur.close(); conn.close()

# ---- admin_api_add_employee   (my app.py lines 16351-16400)
# PASTE AFTER: def admin_edit_employee(...)  (the end of that function)
@app.route('/admin/api/add_employee', methods=['POST'])
def admin_api_add_employee():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    emp_num  = (data.get('employee_number') or '').strip()
    f_name   = (data.get('first_name') or '').strip()
    m_name   = (data.get('middle_name') or '').strip() or None
    l_name   = (data.get('last_name') or '').strip()
    email    = (data.get('email') or '').strip()
    contact  = (data.get('contact') or '').strip()
    spec_id  = data.get('specialization_id')
    type_id  = data.get('type_id')
    status   = data.get('status')
    desig_id = data.get('designation_id') or None
    if not all([emp_num, f_name, l_name, email, contact, spec_id, type_id, status]):
        return jsonify({'success': False, 'error': 'Please fill in all required fields.'}), 400
    if not contact.isdigit():
        return jsonify({'success': False, 'error': 'Contact Number must contain numbers only.'}), 400

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        role = 'Faculty'
        if desig_id:
            cur.execute("SELECT DesignationName FROM Designation WHERE DesignationID = %s", (desig_id,))
            designation = cur.fetchone()
            if designation and designation['designationname'] == 'Academic Head':
                role = 'Academic Head'

        cur.execute(
            "INSERT INTO Faculty (EmployeeNumber, FirstName, MiddleName, LastName, Email, ContactNumber, SpecializationID, EmployeeTypeID, DesignationID, EmployeeStatus) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (emp_num, f_name, m_name, l_name, email, contact, spec_id, type_id, desig_id, status))

        hashed_password = generate_password_hash(emp_num)
        cur.execute("""
            INSERT INTO Accounts (Username, PasswordHash, Role, IsActive, EmployeeNumber,
                                   must_change_password, account_setup_complete)
            VALUES (%s, %s, %s, TRUE, %s, TRUE, FALSE)
            ON CONFLICT (Username) DO NOTHING
        """, (emp_num, hashed_password, role, emp_num))

        conn.commit()
        flash("Employee added successfully and account created!", "success")
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': _friendly_faculty_db_error(e)}), 409
    finally:
        cur.close(); conn.close()

# ---- admin_api_edit_employee   (my app.py lines 16402-16447)
# PASTE AFTER: def admin_edit_employee(...)  (the end of that function)
@app.route('/admin/api/edit_employee', methods=['POST'])
def admin_api_edit_employee():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    emp_num  = (data.get('employee_number') or '').strip()
    f_name   = (data.get('first_name') or '').strip()
    m_name   = (data.get('middle_name') or '').strip() or None
    l_name   = (data.get('last_name') or '').strip()
    email    = (data.get('email') or '').strip()
    contact  = (data.get('contact') or '').strip()
    spec_id  = data.get('specialization_id')
    type_id  = data.get('type_id')
    status   = data.get('status')
    desig_id = data.get('designation_id') or None
    if not all([emp_num, f_name, l_name, email, contact, spec_id, type_id, status]):
        return jsonify({'success': False, 'error': 'Please fill in all required fields.'}), 400
    if not contact.isdigit():
        return jsonify({'success': False, 'error': 'Contact Number must contain numbers only.'}), 400

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
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': _friendly_faculty_db_error(e)}), 409
    finally:
        cur.close(); conn.close()

#===================================================================================================
# 2. FORGOT PASSWORD (needs password_reset.py + templates/forgot_password.html)
#===================================================================================================

# ---- _client_ip   (my app.py lines 787-798)
# PASTE AFTER: def _dashboard_url_for_role(...)  (the end of that function)
def _client_ip():
    """Client IP for rate limiting. Behind Render's proxy (TRUST_PROXY=1) use
    the address the edge proxy reports; otherwise the socket address."""
    if Config.TRUST_PROXY:
        for h in ('CF-Connecting-IP', 'True-Client-IP'):
            v = (request.headers.get(h) or '').strip()
            if v:
                return v[:64]
        xff = [p.strip() for p in (request.headers.get('X-Forwarded-For') or '').split(',') if p.strip()]
        if xff:
            return xff[0][:64]
    return (request.remote_addr or '')[:64]

# ---- _dispatch_reset_email   (my app.py lines 801-816)
# PASTE AFTER: def _dashboard_url_for_role(...)  (the end of that function)
def _dispatch_reset_email(job):
    """Send outside the request so the response time is the same whether or not
    an email goes out (no account enumeration by timing). Failures are logged
    without the address or the code."""
    if not job:
        return
    def run():
        try:
            job()
        except Exception as e:
            print(f"[forgot_password] reset email could not be sent: {type(e).__name__}: {e}", flush=True)
    if _pwreset.SEND_IN_BACKGROUND:
        import threading
        threading.Thread(target=run, daemon=True).start()
    else:
        run()

# ---- _fp_page   (my app.py lines 819-823)
# PASTE AFTER: def _dashboard_url_for_role(...)  (the end of that function)
def _fp_page(step, status=200, **ctx):
    resp = make_response(render_template('forgot_password.html', step=step,
                                         code_ttl=_pwreset.CODE_TTL_MINUTES, **ctx), status)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp

# ---- _fp_logged_in_redirect   (my app.py lines 826-829)
# PASTE AFTER: def _dashboard_url_for_role(...)  (the end of that function)
def _fp_logged_in_redirect():
    if session.get('loggedin') and _dashboard_url_for_role(session.get('role')) != 'login':
        return redirect(url_for(_dashboard_url_for_role(session.get('role'))))
    return None

# ---- forgot_password   (my app.py lines 832-857)
# PASTE AFTER: def _dashboard_url_for_role(...)  (the end of that function)
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    r = _fp_logged_in_redirect()
    if r:
        return r
    if request.method == 'GET':
        if request.args.get('restart'):
            session.pop('fp_rid', None); session.pop('fp_token', None)
        return _fp_page('request')

    identifier = (request.form.get('identifier') or '').strip()
    conn = get_db_connection()
    try:
        rid, job = _pwreset.start(conn, Config, identifier, _client_ip())
    except _pwreset.ResetError as e:
        return _fp_page('request', e.status, error=e.message, identifier=identifier)
    except Exception as e:
        print(f"[forgot_password] start failed: {type(e).__name__}")
        return _fp_page('request', 500, error='Something went wrong. Please try again.', identifier=identifier)
    finally:
        conn.close()
    session.pop('fp_token', None)
    session['fp_rid'] = rid
    _dispatch_reset_email(job)
    flash(_pwreset.GENERIC_SENT, 'info')
    return redirect(url_for('forgot_password_verify'))

# ---- forgot_password_verify   (my app.py lines 860-888)
# PASTE AFTER: def _dashboard_url_for_role(...)  (the end of that function)
@app.route('/forgot-password/verify', methods=['GET', 'POST'])
def forgot_password_verify():
    r = _fp_logged_in_redirect()
    if r:
        return r
    rid = session.get('fp_rid')
    if not rid:
        return redirect(url_for('forgot_password'))
    conn = get_db_connection()
    try:
        error, status_code = None, 200
        if request.method == 'POST':
            try:
                session['fp_token'] = _pwreset.verify(conn, rid, request.form.get('code', ''))
                return redirect(url_for('forgot_password_new'))
            except _pwreset.ResetError as e:
                if e.error == 'no_session':
                    session.pop('fp_rid', None); session.pop('fp_token', None)
                    flash(e.message, 'error')
                    return redirect(url_for('forgot_password'))
                error, status_code = e.message, e.status
        try:
            wait = _pwreset.status(conn, rid)['resend_wait']
        except _pwreset.ResetError:
            session.pop('fp_rid', None)
            return redirect(url_for('forgot_password'))
        return _fp_page('verify', status_code, error=error, resend_wait=wait)
    finally:
        conn.close()

# ---- forgot_password_resend   (my app.py lines 891-916)
# PASTE AFTER: def _dashboard_url_for_role(...)  (the end of that function)
@app.route('/forgot-password/resend', methods=['POST'])
def forgot_password_resend():
    r = _fp_logged_in_redirect()
    if r:
        return r
    rid = session.get('fp_rid')
    if not rid:
        return redirect(url_for('forgot_password'))
    conn = get_db_connection()
    try:
        new_rid, job = _pwreset.resend(conn, Config, rid, _client_ip())
    except _pwreset.ResetError as e:
        if e.error == 'no_session':
            session.pop('fp_rid', None); session.pop('fp_token', None)
            flash(e.message, 'error')
            return redirect(url_for('forgot_password'))
        flash(e.message, 'error')
        return redirect(url_for('forgot_password_verify'))
    finally:
        conn.close()
    session['fp_rid'] = new_rid
    session.pop('fp_token', None)
    _dispatch_reset_email(job)
    flash('A new verification code has been sent to your registered email (if the account has one). '
          'Earlier codes no longer work.', 'info')
    return redirect(url_for('forgot_password_verify'))

# ---- forgot_password_new   (my app.py lines 919-949)
# PASTE AFTER: def _dashboard_url_for_role(...)  (the end of that function)
@app.route('/forgot-password/new-password', methods=['GET', 'POST'])
def forgot_password_new():
    r = _fp_logged_in_redirect()
    if r:
        return r
    rid, token = session.get('fp_rid'), session.get('fp_token')
    if not rid:
        return redirect(url_for('forgot_password'))
    conn = get_db_connection()
    try:
        if not _pwreset.is_verified(conn, rid, token):
            session.pop('fp_token', None)
            flash('Please verify the code sent to your email first. If it has expired, request a new one.', 'error')
            return redirect(url_for('forgot_password_verify'))
        if request.method == 'GET':
            return _fp_page('reset')
        try:
            username = _pwreset.reset(conn, rid, token, request.form.get('new_password', ''),
                                      request.form.get('confirm_password', ''))
        except _pwreset.ResetError as e:
            if e.error == 'not_verified':
                session.pop('fp_token', None)
                flash(e.message, 'error')
                return redirect(url_for('forgot_password_verify'))
            return _fp_page('reset', e.status, error=e.message)
    finally:
        conn.close()
    session.pop('fp_rid', None); session.pop('fp_token', None)
    session['saved_username'] = username          # pre-fills the Login form
    flash('Your password has been reset. You can now log in with your new password.', 'success')
    return redirect(url_for('login'))

#===================================================================================================
# 3. ACCOUNT SETUP: EMAIL / PHOTO / CONTACT STEPS (needs templates/setup/)
#===================================================================================================

# ---- constants   (my app.py lines 996-1002)
# PASTE AFTER: def _enforce_account_setup(...)  (the end of that function)
_SETUP_STEPS = [
    ('password', 'Password',          'account_setup_password'),
    ('email',    'Email',             'account_setup_email'),
    ('photo',    'Profile Photo',     'account_setup_photo'),
    ('contact',  'Contact No.',       'account_setup_contact'),
    ('verify',   'Review',            'account_setup_verify'),
]

# ---- _setup_ctx   (my app.py lines 1005-1007)
# PASTE AFTER: def _enforce_account_setup(...)  (the end of that function)
def _setup_ctx(step):
    idx = [s[0] for s in _SETUP_STEPS].index(step)
    return {'steps': _SETUP_STEPS, 'step_index': idx, 'step_key': step}

# ---- _setup_draft   (my app.py lines 1010-1011)
# PASTE AFTER: def _enforce_account_setup(...)  (the end of that function)
def _setup_draft():
    return session.get('setup_draft') or {}

# ---- _setup_set_draft   (my app.py lines 1014-1017)
# PASTE AFTER: def _enforce_account_setup(...)  (the end of that function)
def _setup_set_draft(**kw):
    d = dict(_setup_draft())
    d.update(kw)
    session['setup_draft'] = d

# ---- _setup_clear_draft   (my app.py lines 1020-1024)
# PASTE AFTER: def _enforce_account_setup(...)  (the end of that function)
def _setup_clear_draft(*keys):
    d = dict(_setup_draft())
    for k in keys:
        d.pop(k, None)
    session['setup_draft'] = d

# ---- _setup_guard   (my app.py lines 1027-1035)
# PASTE AFTER: def _enforce_account_setup(...)  (the end of that function)
def _setup_guard():
    """Redirect for anyone who may NOT be on an optional step right now (or None)."""
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    if session.get('must_change_password'):
        return redirect(url_for('account_setup_password'))      # never skippable
    if session.get('account_setup_complete', True):
        return redirect(url_for(_dashboard_url_for_role(session.get('role'))))
    return None

# ---- _setup_account   (my app.py lines 1038-1044)
# PASTE AFTER: def _enforce_account_setup(...)  (the end of that function)
def _setup_account():
    return query_db("""
        SELECT a.username, a.employeenumber, a.profile_photo, a.email AS account_email,
               f.email AS faculty_email, f.contactnumber
        FROM accounts a LEFT JOIN faculty f ON f.employeenumber = a.employeenumber
        WHERE a.username = %s
    """, (session.get('username'),), one=True) or {}

# ---- account_setup_email   (my app.py lines 1105-1153)
# PASTE AFTER: def account_setup_info(...)  (the end of that function)
@app.route('/account-setup/email', methods=['GET', 'POST'])
def account_setup_email():
    guard = _setup_guard()
    if guard:
        return guard
    acc = _setup_account()
    on_file = (acc.get('faculty_email') if acc.get('employeenumber') else acc.get('account_email')) or ''

    if request.method == 'POST':
        action = request.form.get('action', 'continue')
        email  = request.form.get('email', '').strip()
        if action == 'back':
            _setup_set_draft(email=email)
            return redirect(url_for('account_setup_password'))
        if action == 'skip':
            _setup_clear_draft('email')
            return redirect(url_for('account_setup_photo'))
        if not email and on_file:
            _setup_clear_draft('email')                     # keep the email already on file
            return redirect(url_for('account_setup_photo'))
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 255:
            _setup_set_draft(email=email)
            flash("Please enter a valid email address, or choose Skip.", "error")
            return redirect(url_for('account_setup_email'))
        conn = get_db_connection(); cur = conn.cursor()
        try:
            if acc.get('employeenumber'):
                cur.execute("UPDATE faculty SET email = %s WHERE employeenumber = %s",
                            (email, acc['employeenumber']))
            else:
                cur.execute("UPDATE accounts SET email = %s WHERE username = %s AND employeenumber IS NULL",
                            (email, session.get('username')))
            conn.commit()
        except Exception as e:
            conn.rollback()
            _setup_set_draft(email=email)
            if isinstance(e, psycopg2.errors.UniqueViolation):
                flash("That email address is already used by another faculty member.", "error")
            else:
                print(f"[account setup] email save failed: {type(e).__name__}")
                flash("Your email could not be saved. Please try again.", "error")
            return redirect(url_for('account_setup_email'))
        finally:
            cur.close(); conn.close()
        _setup_clear_draft('email')
        return redirect(url_for('account_setup_photo'))

    return render_template('setup/setup_email.html', current_email=on_file,
                           draft_email=_setup_draft().get('email', ''), **_setup_ctx('email'))

# ---- account_setup_photo   (my app.py lines 1156-1181)
# PASTE AFTER: def account_setup_info(...)  (the end of that function)
@app.route('/account-setup/photo', methods=['GET', 'POST'])
def account_setup_photo():
    guard = _setup_guard()
    if guard:
        return guard
    acc = _setup_account()

    if request.method == 'POST':
        action = request.form.get('action', 'continue')
        photo  = request.files.get('photo')
        chosen = bool(photo and photo.filename)
        if chosen and action in ('continue', 'back'):
            # A picked photo is saved on Continue AND on Back, so going back loses nothing.
            _url, err = _save_profile_photo(session.get('username'), photo)
            if err:
                flash(err, "error")
                return redirect(url_for('account_setup_photo'))
        if action == 'back':
            return redirect(url_for('account_setup_email'))
        if action == 'continue' and not chosen and not acc.get('profile_photo'):
            flash("Please choose a photo to upload, or choose Skip.", "error")
            return redirect(url_for('account_setup_photo'))
        return redirect(url_for('account_setup_contact'))

    return render_template('setup/setup_photo.html', current_photo=acc.get('profile_photo') or '',
                           **_setup_ctx('photo'))

# ---- account_setup_contact   (my app.py lines 1184-1231)
# PASTE AFTER: def account_setup_info(...)  (the end of that function)
@app.route('/account-setup/contact', methods=['GET', 'POST'])
def account_setup_contact():
    guard = _setup_guard()
    if guard:
        return guard
    acc = _setup_account()
    if not acc.get('employeenumber'):
        # Standalone account (no faculty record): there is nowhere to keep a contact number.
        if request.method == 'POST' and request.form.get('action') == 'back':
            return redirect(url_for('account_setup_photo'))
        return redirect(url_for('account_setup_verify'))
    on_file = acc.get('contactnumber') or ''

    if request.method == 'POST':
        action  = request.form.get('action', 'continue')
        contact = request.form.get('contact_number', '').strip()
        if action == 'back':
            _setup_set_draft(contact=contact)
            return redirect(url_for('account_setup_photo'))
        if action == 'skip':
            _setup_clear_draft('contact')
            return redirect(url_for('account_setup_verify'))
        if not contact and on_file:
            _setup_clear_draft('contact')
            return redirect(url_for('account_setup_verify'))
        digits = re.sub(r'\D', '', contact)
        if not re.fullmatch(r'[0-9+()\-\s]+', contact or 'x') or not (7 <= len(digits) <= 15):
            _setup_set_draft(contact=contact)
            flash("Please enter a valid contact number, or choose Skip.", "error")
            return redirect(url_for('account_setup_contact'))
        conn = get_db_connection(); cur = conn.cursor()
        try:
            cur.execute("UPDATE faculty SET contactnumber = %s WHERE employeenumber = %s",
                        (contact, acc['employeenumber']))
            conn.commit()
        except Exception as e:
            conn.rollback()
            _setup_set_draft(contact=contact)
            print(f"[account setup] contact save failed: {type(e).__name__}")
            flash("Your contact number could not be saved. Please try again.", "error")
            return redirect(url_for('account_setup_contact'))
        finally:
            cur.close(); conn.close()
        _setup_clear_draft('contact')
        return redirect(url_for('account_setup_verify'))

    return render_template('setup/setup_contact.html', current_contact=on_file,
                           draft_contact=_setup_draft().get('contact', ''), **_setup_ctx('contact'))

#===================================================================================================
# 4. PROFILE PHOTO SAVING (used by account setup photo step + /api/user/photo/upload)
#===================================================================================================

# ---- constants   (my app.py lines 25525-25527)
# PASTE AFTER: def api_faculty_my_teaching_load(...)  (the end of that function)
_PROFILE_PHOTO_URL_PREFIX = '/static/uploads/profile_photos/'
_PROFILE_PHOTO_MAX_BYTES  = 2 * 1024 * 1024
_PROFILE_PHOTO_FORMATS    = {'JPEG': '.jpg', 'PNG': '.png'}

# ---- _save_profile_photo   (my app.py lines 25530-25586)
# PASTE AFTER: def api_faculty_my_teaching_load(...)  (the end of that function)
def _save_profile_photo(username, file_storage):
    """Store an uploaded profile picture for this account.

    The image file lives in static/uploads/profile_photos/ (the project's
    existing file storage); the database keeps only its path, in
    accounts.profile_photo, on the logged-in user's own account row. Each
    upload gets a unique name (so browsers never show a cached old photo) and
    the account's previous photo file is removed.
    Returns (photo_url, None) or (None, user-facing error)."""
    if not file_storage or not file_storage.filename:
        return None, 'Please choose a photo to upload.'
    data = file_storage.read(_PROFILE_PHOTO_MAX_BYTES + 1)
    if len(data) > _PROFILE_PHOTO_MAX_BYTES:
        return None, 'Photo is too large. The maximum size is 2 MB.'
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            img.verify()
    except Exception:
        return None, 'That file is not a valid JPG or PNG image.'
    ext = _PROFILE_PHOTO_FORMATS.get(fmt)
    if not ext:
        return None, 'Photo must be a JPG or PNG image.'

    safe_user = secure_filename(username) or 'user'
    filename  = f"profile_{safe_user}_{uuid.uuid4().hex[:12]}{ext}"
    path      = os.path.join(_PROFILE_PHOTO_DIR, filename)
    url       = _PROFILE_PHOTO_URL_PREFIX + filename
    with open(path, 'wb') as fh:
        fh.write(data)

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT profile_photo FROM accounts WHERE username = %s FOR UPDATE", (username,))
        row = cur.fetchone()
        if not row:
            raise ValueError('account not found')
        cur.execute("UPDATE accounts SET profile_photo = %s WHERE username = %s", (url, username))
        conn.commit()
    except Exception as e:
        conn.rollback()
        try: os.remove(path)
        except OSError: pass
        print(f"[profile photo] save failed: {type(e).__name__}")
        return None, 'The photo could not be saved. Please try again.'
    finally:
        cur.close(); conn.close()

    # Remove this account's previous photo file (only files it owns in our folder).
    old = (row or {}).get('profile_photo') or ''
    old_name = os.path.basename(old)
    if (old.startswith(_PROFILE_PHOTO_URL_PREFIX) and old_name != filename
            and old_name.startswith(f"profile_{safe_user}")):
        try: os.remove(os.path.join(_PROFILE_PHOTO_DIR, old_name))
        except OSError: pass
    return url, None

#===================================================================================================
# 5. CHANGE PASSWORD WITH CODE (needs password_change.py)
#===================================================================================================

# ---- _password_step   (my app.py lines 25788-25806)
# PASTE AFTER: def api_user_photo_upload(...)  (the end of that function)
def _password_step(fn, *args):
    if 'loggedin' not in session:
        return jsonify({'success': False, 'error': 'unauthorized',
                        'message': 'Please log in again.'}), 401
    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': 'server_error',
                        'message': 'Database connection failed.'}), 500
    try:
        return fn(conn, session['username'], *args)
    except _pwchange.PasswordChangeError as pe:
        return jsonify(pe.to_json()), pe.status
    except Exception as e:
        conn.rollback()
        print(f"[password change] unexpected error: {type(e).__name__}")
        return jsonify({'success': False, 'error': 'server_error',
                        'message': 'Something went wrong. Please try again.'}), 500
    finally:
        conn.close()

# ---- api_user_password_eligibility   (my app.py lines 25809-25811)
# PASTE AFTER: def api_user_photo_upload(...)  (the end of that function)
@app.route('/api/user/password/eligibility')
def api_user_password_eligibility():
    return _password_step(lambda c, u: jsonify(_pwchange.eligibility(c, u)))

# ---- api_user_password_request_code   (my app.py lines 25814-25817)
# PASTE AFTER: def api_user_photo_upload(...)  (the end of that function)
@app.route('/api/user/password/request-code', methods=['POST'])
def api_user_password_request_code():
    session.pop('pw_otp_verified_at', None)
    return _password_step(lambda c, u: jsonify(_pwchange.request_code(c, u, Config)))

# ---- api_user_password_verify_code   (my app.py lines 25820-25825)
# PASTE AFTER: def api_user_photo_upload(...)  (the end of that function)
@app.route('/api/user/password/verify-code', methods=['POST'])
def api_user_password_verify_code():
    def _verify(c, u):
        session['pw_otp_verified_at'] = _pwchange.verify_code(c, u, request.form.get('code', ''))
        return jsonify({'success': True})
    return _password_step(_verify)

#===================================================================================================
# 6. FACULTY CLASS SCHEDULE EXPORT (needs teaching_assignment_export.py + templates/_schedule_export_modal.html)
#===================================================================================================

# ---- _sch_filter_export   (my app.py lines 5307-5327)
# PASTE AFTER: def _sch_export_context(...)  (the end of that function)
def _sch_filter_export(cur, rows, groups, cal_rows, cal_groups, section_id=None, faculty_id=None, room_id=None):
    """Narrow an _sch_export_context result to one section / instructor / room
    (Reports > Class Schedule and the Faculty Class Schedule export)."""
    if not (section_id or room_id or faculty_id):
        return rows, groups, cal_rows, cal_groups
    _fac_fname = None
    if faculty_id:
        cur.execute("SELECT lastname, firstname, middlename FROM faculty WHERE employeenumber = %s", (faculty_id,))
        _f = cur.fetchone()
        _fac_fname = (f"{_f['lastname']}, {_f['firstname']}" + (f" {_f['middlename']}" if _f.get('middlename') else '')) if _f else None

    def _filt(rs):
        if section_id: rs = [r for r in rs if str(r.get('SectionID')) == str(section_id)]
        if room_id:    rs = [r for r in rs if str(r.get('RoomID')) == str(room_id)]
        if faculty_id: rs = [r for r in rs if _fac_fname and r.get('Instructor') == _fac_fname]
        return rs

    rows = _filt(rows); groups = _sch_exp_groups(rows)
    if cal_rows is not None:
        cal_rows = _filt(cal_rows); cal_groups = _sch_exp_groups(cal_rows)
    return rows, groups, cal_rows, cal_groups

# ---- _faculty_active_term   (my app.py lines 5427-5431)
# PASTE AFTER: def schedule_export_multi(...)  (the end of that function)
def _faculty_active_term(cur):
    """The active (academicyearid, semestertype) — the only term the Faculty
    Class Schedule page and its export ever use, whatever the browser sends."""
    cur.execute("SELECT s.academicyearid, s.semestertype FROM semester s WHERE s.isactive = TRUE LIMIT 1")
    return cur.fetchone()

# ---- _faculty_se_filters   (my app.py lines 5434-5439)
# PASTE AFTER: def schedule_export_multi(...)  (the end of that function)
def _faculty_se_filters(data):
    """Program / Year Level lists from the shared Class Schedule export dialog.
    Any ay_ids / sem_types in the body are ignored on purpose."""
    programs = [str(p).strip() for p in (data.get('programs') or []) if str(p).strip()]
    year_levels = [int(y) for y in (data.get('year_levels') or []) if str(y).strip()]
    return programs, year_levels

# ---- faculty_schedule_export_count   (my app.py lines 5442-5466)
# PASTE AFTER: def schedule_export_multi(...)  (the end of that function)
@app.route('/faculty/schedule/export/count', methods=['POST'])
def faculty_schedule_export_count():
    """Row / program count for the Faculty Class Schedule export dialog — same
    as /academic/schedule/export/count, but always the active term and only
    Published schedules (what the Faculty page shows)."""
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json(silent=True) or {}
    try:
        programs, year_levels = _faculty_se_filters(data)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid year level.'}), 400
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        active = _faculty_active_term(cur)
        if not active:
            return jsonify({'count': 0, 'programs': 0})
        rows = _sch_exp_fetch(cur, [active['academicyearid']], [active['semestertype']],
                              programs, year_levels, published_only=True)
        return jsonify({'count': len(rows), 'programs': len(_sch_exp_groups(rows))})
    except Exception:
        import traceback; traceback.print_exc()
        return jsonify({'error': 'Could not count the schedule rows.'}), 500
    finally:
        cur.close(); conn.close()

# ---- faculty_schedule_export   (my app.py lines 5469-5532)
# PASTE AFTER: def schedule_export_multi(...)  (the end of that function)
@app.route('/faculty/schedule/export', methods=['POST'])
def faculty_schedule_export():
    """Faculty → Class Schedule (SIS) → Export. Documents come from the same
    official generators as the Academic Head's Class Schedule export and
    Reports > Class Schedule (_sch_export_context / _sch_export_bytes), so the
    file looks identical to every other class schedule the system produces.

    Two request shapes:
      • {formats: [...], programs: [...], year_levels: [...], layout, filename}
        — the shared Academic Head export dialog: one or more formats (several
        → one .zip), always the active term and Published schedules only.
      • {format, program, year_level, section_id | emp_num, layout, filename}
        — single format narrowed to one section / instructor (kept working)."""
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return jsonify({'error': 'Unauthorized'}), 403
    data    = request.get_json(silent=True) or {}
    if isinstance(data.get('formats'), list):
        return _faculty_schedule_export_multi(data)
    fmt     = (data.get('format') or '').strip().lower()
    layout  = (data.get('layout') or 'table').strip().lower()
    if layout not in ('table', 'calendar'):
        layout = 'table'
    mime_map = _ta_export.MIME
    if fmt not in mime_map:
        return jsonify({'error': 'Please choose one format: XLSX, CSV, PDF or DOCX.'}), 400

    program    = (data.get('program') or '').strip()
    section_id = str(data.get('section_id') or '').strip()
    emp_num    = str(data.get('emp_num') or '').strip()
    try:
        year_levels = [int(data['year_level'])] if str(data.get('year_level') or '').strip() else []
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid year level.'}), 400
    if not (section_id or emp_num):
        return jsonify({'error': 'Select a Section or an Instructor first.'}), 400

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # The Faculty page is locked to the active term, so the export is too.
        cur.execute("""SELECT s.academicyearid, s.semestertype FROM semester s
                       WHERE s.isactive = TRUE LIMIT 1""")
        active = cur.fetchone()
        if not active:
            return jsonify({'error': 'No active academic term found.'}), 400

        rows, groups, cal_rows, cal_groups, sem_labels, ay_label, prog_name_map, layout = \
            _sch_export_context(cur, [active['academicyearid']], [active['semestertype']],
                                [program] if program else [], year_levels, layout, published_only=True)
        rows, groups, cal_rows, cal_groups = _sch_filter_export(
            cur, rows, groups, cal_rows, cal_groups, section_id or None, emp_num or None)
        if not rows:
            return jsonify({'error': 'There is no published schedule for the selected filters.'}), 404

        out = _sch_export_bytes(fmt, layout, rows, groups, cal_rows, cal_groups, sem_labels, ay_label, prog_name_map)
        filename = _ta_export.clean_filename(data.get('filename')) or \
            _ta_export.clean_filename(f"Class_Schedule_{sem_labels}_{ay_label}")
        mime, ext = mime_map[fmt]
        return Response(out, mimetype=mime,
                        headers={'Content-Disposition': f'attachment; filename="{filename}{ext}"'})
    except Exception:
        import traceback; traceback.print_exc()
        return jsonify({'error': 'The export could not be generated. Please try again.'}), 500
    finally:
        cur.close(); conn.close()

# ---- _faculty_schedule_export_multi   (my app.py lines 5535-5581)
# PASTE AFTER: def schedule_export_multi(...)  (the end of that function)
def _faculty_schedule_export_multi(data):
    """The shared (Academic Head) export dialog on the Faculty page — same
    output as schedule_export_multi, locked to the active term, Published only."""
    formats = []
    for f in data.get('formats') or []:
        f = str(f or '').strip().lower()
        if f and f not in formats:
            formats.append(f)
    if not formats or any(f not in _ta_export.MIME for f in formats):
        return jsonify({'error': 'Please choose at least one format: PDF, DOCX, XLSX or CSV.'}), 400
    layout = (data.get('layout') or 'table').strip().lower()
    try:
        programs, year_levels = _faculty_se_filters(data)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid year level.'}), 400

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        active = _faculty_active_term(cur)
        if not active:
            return jsonify({'error': 'No active academic term found.'}), 400
        rows, groups, cal_rows, cal_groups, sem_labels, ay_label, prog_name_map, layout = \
            _sch_export_context(cur, [active['academicyearid']], [active['semestertype']],
                                programs, year_levels, layout, published_only=True)
        if not rows:
            return jsonify({'error': 'There is no published schedule for the selected filters.'}), 404
        filename = _ta_export.clean_filename(data.get('filename')) or 'schedule_export'
        if len(formats) == 1:
            mime, ext = _ta_export.MIME[formats[0]]
            out = _sch_export_bytes(formats[0], layout, rows, groups, cal_rows, cal_groups,
                                    sem_labels, ay_label, prog_name_map)
            return Response(out, mimetype=mime,
                            headers={'Content-Disposition': f'attachment; filename="{filename}{ext}"'})
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in formats:
                zf.writestr(filename + _ta_export.MIME[f][1],
                            _sch_export_bytes(f, layout, rows, groups, cal_rows, cal_groups,
                                              sem_labels, ay_label, prog_name_map))
        return Response(buf.getvalue(), mimetype='application/zip',
                        headers={'Content-Disposition': f'attachment; filename="{filename}.zip"'})
    except Exception:
        import traceback; traceback.print_exc()
        return jsonify({'error': 'The export could not be generated. Please try again.'}), 500
    finally:
        cur.close(); conn.close()

#===================================================================================================
# 7. FACULTY TEACHING ASSIGNMENT + WEEKLY SCHEDULE EXPORT, MY MAKE-UPS (needs teaching_assignment_export.py + weekly_schedule_export.py)
#===================================================================================================

# ---- _session_employee_number   (my app.py lines 25209-25216)
# PASTE AFTER: def api_faculty_my_teaching_load(...)  (the end of that function)
def _session_employee_number():
    """The logged-in user's own employee number (never taken from the request)."""
    emp_num = session.get('employeenumber')
    if not emp_num:
        acc = query_db("SELECT employeenumber FROM accounts WHERE username = %s",
                       [session.get('username')], one=True)
        emp_num = acc['employeenumber'] if acc else None
    return emp_num

# ---- _my_teaching_load_payload   (my app.py lines 25219-25317)
# PASTE AFTER: def api_faculty_my_teaching_load(...)  (the end of that function)
def _my_teaching_load_payload(cur, emp_num, ay_id, sem, source):
    """Shared by the Teaching Assignment tab (/api/faculty/my_teaching_load) and
    its export (/faculty/teaching-assignment/export), so an exported file always
    matches what the page shows. Returns (payload, None) or
    (None, (error_message, http_status))."""
    ay_id  = (ay_id or '').strip()
    sem    = (sem or '').strip()
    source = (source or 'official').strip().lower()
    if source not in ('official', 'local'):
        source = 'official'

    if not ay_id or not sem:
        cur.execute("""
            SELECT ay.academicyearid, s.semestertype
            FROM semester s JOIN academicyear ay ON s.academicyearid = ay.academicyearid
            WHERE s.isactive = TRUE LIMIT 1
        """)
        active = cur.fetchone()
        ay_id = ay_id or (active['academicyearid'] if active else '')
        sem   = sem   or (active['semestertype']   if active else '')
    if not ay_id or not sem:
        return None, ('No active academic term found.', 400)

    fac = _faculty_identity_and_caps_row(cur, emp_num)
    if not fac:
        return None, ('Faculty record not found.', 404)

    if source == 'local':
        sessions = _local_teaching_sessions(cur, emp_num, ay_id, sem)
    else:
        # Published-only — the one existing "real scheduled hours" source in
        # the codebase (faculty_load.FACULTY_SESSIONS_SQL, published_only variant).
        sessions = faculty_load.get_faculty_sessions(cur, emp_num, ay_id, sem, published_only=True)
    buckets = faculty_load.compute_load_buckets(sessions, fac)

    cur.execute("""
        SELECT TO_CHAR(s.semstartdate,'MM/DD/YYYY') AS effectivity, ay.yearstart, ay.yearend
        FROM semester s JOIN academicyear ay ON ay.academicyearid = s.academicyearid
        WHERE s.academicyearid = %s AND s.semestertype = %s LIMIT 1
    """, (ay_id, sem))
    sem_row = cur.fetchone()
    effectivity = (sem_row['effectivity'] if sem_row and sem_row.get('effectivity') else '—')
    ay_label  = f"{sem_row['yearstart']}-{sem_row['yearend']}" if sem_row and sem_row.get('yearstart') else ay_id
    sem_label = _ta_export.SEM_LABELS.get(sem, sem)

    # Faculty Assignment form fields: employment status + department (specialization)
    cur.execute("""SELECT f.employeestatus, s.specializationname FROM faculty f
                   LEFT JOIN specialization s ON s.specializationid = f.specializationid
                   WHERE f.employeenumber = %s""", (emp_num,))
    _fx = cur.fetchone() or {}

    # Teaching load per day (hours), split Regular / Part-Time with the same
    # per-slice rule faculty_load uses for the Regular/PT buckets.
    per_day = {'regular': {}, 'part_time': {}}
    for s in sessions:
        day = s.get('days')
        if not day:
            continue
        h = float(s.get('hrs') or 0)
        is_reg = (not buckets['isPartTime']) and faculty_load.is_reg_slice(day, s.get('time_code'))
        bucket = per_day['regular' if is_reg else 'part_time']
        bucket[day] = round(bucket.get(day, 0) + h, 2)

    def _row(g):
        return {
            'subjectcode':  g.get('subjectcode') or '—',
            'subjectname':  g.get('subjectname') or '—',
            'units':        g.get('units'),
            'year_section': g.get('year_section') or '—',
            'time_range':   _collapse_time_range(g.get('time_range')) or '—',
            'days':         _abbr_days(g.get('days')),
            'room':         g.get('room') or 'TBA',
            'effectivity':  effectivity,
        }

    return {
        'success': True,
        'source': source,
        'has_schedule': bool(sessions),
        'employeenumber': fac['employeenumber'],
        'fullname': fac['fullname'],
        'employee_type': fac['employee_type'],
        'employee_status': _fx.get('employeestatus') or '',
        'dept_code': _fx.get('specializationname') or '',
        'per_day_hours': per_day,
        'ay_id': ay_id, 'semester': sem,
        'ay_label': ay_label, 'sem_label': sem_label,
        'export_filename': _ta_export.export_filename(fac['fullname'], sem_label, ay_label),
        'regular':  [_row(g) for g in buckets['groupedReg']],
        'partTime': [_row(g) for g in buckets['groupedPt']],
        'totals': {
            'regularUnits':   buckets['regUsed'],
            'partTimeUnits':  buckets['ptUsed'],
            'tsUnits':        buckets['tsUsed'],
            'totalUnits':     buckets['used'],
            'maxUnits':       buckets['maxTotal'],
            'availableUnits': max(0, round(buckets['maxTotal'] - buckets['used'], 2)),
        },
    }, None

# ---- faculty_teaching_assignment_export   (my app.py lines 25320-25366)
# PASTE AFTER: def api_faculty_my_teaching_load(...)  (the end of that function)
@app.route('/faculty/teaching-assignment/export', methods=['POST'])
def faculty_teaching_assignment_export():
    """Teaching Assignment export (CSV / XLSX / PDF / DOCX) for the logged-in
    faculty member's OWN load — the account comes from the session only.
    Same official letterhead and table design as the system's other exports
    (see teaching_assignment_export.py)."""
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return jsonify({'error': 'Unauthorized'}), 403
    data   = request.get_json(silent=True) or {}
    # One or more formats: a single file for one format, a .zip for several
    # (same behaviour as the Reports / Class Schedule exports).
    raw = data.get('formats') if isinstance(data.get('formats'), list) else [data.get('format')]
    formats = []
    for f in raw:
        f = str(f or '').strip().lower()
        if f and f not in formats:
            formats.append(f)
    if not formats or any(f not in _ta_export.MIME for f in formats):
        return jsonify({'error': 'Please choose at least one format: PDF, XLSX, CSV or DOCX.'}), 400
    emp_num = _session_employee_number()
    if not emp_num:
        return jsonify({'error': 'No employee number linked to this account.'}), 400

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        payload, err = _my_teaching_load_payload(cur, emp_num, data.get('ay_id'),
                                                 data.get('semester'), data.get('source'))
        if err:
            return jsonify({'error': err[0]}), err[1]
        filename = _ta_export.clean_filename(data.get('filename')) or payload['export_filename']
        if len(formats) == 1:
            out = _ta_export.GENERATORS[formats[0]](payload, _SCH_LOGO_PATH)
            mime, ext = _ta_export.MIME[formats[0]]
            return Response(out, mimetype=mime,
                            headers={'Content-Disposition': f'attachment; filename="{filename}{ext}"'})
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in formats:
                zf.writestr(filename + _ta_export.MIME[f][1], _ta_export.GENERATORS[f](payload, _SCH_LOGO_PATH))
        return Response(buf.getvalue(), mimetype='application/zip',
                        headers={'Content-Disposition': f'attachment; filename="{filename}.zip"'})
    except Exception:
        import traceback; traceback.print_exc()
        return jsonify({'error': 'The export could not be generated. Please try again.'}), 500
    finally:
        cur.close(); conn.close()

# ---- _week_monday   (my app.py lines 25370-25376)
# PASTE AFTER: def api_faculty_my_teaching_load(...)  (the end of that function)
def _week_monday(value):
    """Monday of the week containing `value` (ISO date string), default: this week."""
    try:
        d = date.fromisoformat(str(value)[:10]) if value else date.today()
    except ValueError:
        d = date.today()
    return d - timedelta(days=d.weekday())

# ---- _faculty_makeups   (my app.py lines 25379-25402)
# PASTE AFTER: def api_faculty_my_teaching_load(...)  (the end of that function)
def _faculty_makeups(cur, emp_num, start, end):
    """Approved make-up classes (one-time, date-specific — schedule_exception_log)
    taught by this faculty between start and end inclusive."""
    cur.execute("""
        SELECT sel.exception_date, TO_CHAR(sel.exception_date, 'FMDay') AS days,
               sel.starttimeid, sel.endtimeid,
               TO_CHAR(ts_s.timevalue,'HH12:MI AM') || ' - ' || TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS time_range,
               ROUND(EXTRACT(EPOCH FROM (ts_e.timevalue - ts_s.timevalue)) / 3600.0, 2) AS hrs,
               cs.subjectcode, cs.subjectname, COALESCE(cs.creditunits, 0) AS units,
               COALESCE(sec.sectionname, '') AS year_section, pyl.programcode, pyl.yearlevel,
               COALESCE(r.roomname, 'TBA') AS room, sel.source_requestid
        FROM schedule_exception_log sel
        JOIN schedule sc          ON sc.scheduleid = sel.scheduleid
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = sc.curriculumsubjectid
        LEFT JOIN sections sec    ON sec.sectionid = sc.sectionid
        LEFT JOIN program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
        LEFT JOIN room r          ON r.roomid = sel.roomid
        JOIN timeslot ts_s ON ts_s.timeid = sel.starttimeid
        JOIN timeslot ts_e ON ts_e.timeid = sel.endtimeid
        WHERE sel.source_type = 'makeup_class' AND sel.employeenumber = %s
          AND sel.exception_date BETWEEN %s AND %s
        ORDER BY sel.exception_date, ts_s.timevalue
    """, (emp_num, start, end))
    return [dict(r) for r in (cur.fetchall() or [])]

# ---- api_faculty_my_makeups   (my app.py lines 25405-25431)
# PASTE AFTER: def api_faculty_my_teaching_load(...)  (the end of that function)
@app.route('/api/faculty/my_makeups')
def api_faculty_my_makeups():
    """The logged-in faculty member's own approved make-up classes in a date
    range (the Weekly Schedule shows them only in the week they happen)."""
    if 'loggedin' not in session or session.get('role') not in ('Faculty', 'Academic Head'):
        return jsonify([]), 401
    emp_num = _session_employee_number()
    if not emp_num:
        return jsonify([])
    start = _week_monday(request.args.get('start'))
    try:
        end = date.fromisoformat(request.args.get('end', '')[:10])
    except ValueError:
        end = start + timedelta(days=6)
    if end < start or (end - start).days > 62:
        end = start + timedelta(days=6)
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        rows = _faculty_makeups(cur, emp_num, start, end)
        for r in rows:
            r['exception_date'] = r['exception_date'].isoformat()
        return jsonify(rows)
    except Exception as e:
        print(f"[api_faculty_my_makeups] {type(e).__name__}: {e}")
        return jsonify([]), 500
    finally:
        cur.close(); conn.close()

# ---- faculty_weekly_schedule_export   (my app.py lines 25434-25520)
# PASTE AFTER: def api_faculty_my_teaching_load(...)  (the end of that function)
@app.route('/faculty/weekly-schedule/export', methods=['POST'])
def faculty_weekly_schedule_export():
    """Weekly Schedule export (PDF / XLSX / CSV / DOCX — one file, or a .zip
    for several) of exactly the week shown on the Faculty Weekly Schedule tab:
    the recurring classes that fall inside the semester that week, plus that
    week's approved make-up classes, for the chosen Official/Local source and
    Program filter. Same document design as the Teaching Assignment export."""
    if 'loggedin' not in session or session.get('role') != 'Faculty':
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json(silent=True) or {}
    raw = data.get('formats') if isinstance(data.get('formats'), list) else [data.get('format')]
    formats = []
    for f in raw:
        f = str(f or '').strip().lower()
        if f and f not in formats:
            formats.append(f)
    if not formats or any(f not in _ta_export.MIME for f in formats):
        return jsonify({'error': 'Please choose at least one format: PDF, XLSX, CSV or DOCX.'}), 400
    emp_num = _session_employee_number()
    if not emp_num:
        return jsonify({'error': 'No employee number linked to this account.'}), 400
    source  = 'local' if (data.get('source') or '') == 'local' else 'official'
    program = (data.get('program') or '').strip().upper()
    monday  = _week_monday(data.get('week_start'))
    week    = [monday + timedelta(days=i) for i in range(7)]

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        info, err = _my_teaching_load_payload(cur, emp_num, data.get('ay_id'), data.get('semester'), source)
        if err:
            return jsonify({'error': err[0]}), err[1]
        cur.execute("""SELECT semstartdate, semenddate FROM semester
                       WHERE academicyearid = %s AND semestertype = %s LIMIT 1""",
                    (info['ay_id'], info['semester']))
        sem = cur.fetchone() or {}
        s_start, s_end = sem.get('semstartdate'), sem.get('semenddate')
        in_sem = lambda d: (not s_start or d >= s_start) and (not s_end or d <= s_end)

        if source == 'local':
            sessions = _local_teaching_sessions(cur, emp_num, info['ay_id'], info['semester'])
            for s in sessions:
                s['programcode'] = (s.get('year_section') or '').split('-')[0]
        else:
            sessions = faculty_load.get_faculty_sessions(cur, emp_num, info['ay_id'], info['semester'],
                                                         published_only=True)
            sec_ids = sorted({s['sectionid'] for s in sessions if s.get('sectionid')})
            prog_of = {}
            if sec_ids:
                cur.execute("""SELECT s.sectionid, pyl.programcode FROM sections s
                               JOIN program_yearlevel pyl ON pyl.programyearlevelid = s.programyearlevelid
                               WHERE s.sectionid = ANY(%s)""", (sec_ids,))
                prog_of = {r['sectionid']: r['programcode'] for r in cur.fetchall()}
            for s in sessions:
                s['programcode'] = prog_of.get(s.get('sectionid'), '')

        date_of = {d.strftime('%A'): d for d in week}
        entries = []
        for s in sessions:
            d = date_of.get(s.get('days'))
            if d and in_sem(d) and (not program or (s.get('programcode') or '').upper() == program):
                entries.append({**s, 'date': d, 'kind': 'class'})
        if source == 'official':
            for mk in _faculty_makeups(cur, emp_num, week[0], week[-1]):
                if not program or (mk.get('programcode') or '').upper() == program:
                    entries.append({**mk, 'date': mk['exception_date'], 'kind': 'makeup'})

        doc = {**info, 'program_filter': program, 'week': week,
               'week_in_semester': [in_sem(d) for d in week],
               'sem_start': s_start, 'sem_end': s_end, 'entries': entries}
        name_bits = f"Weekly_Schedule_{info['fullname']}_{week[0].isoformat()}_to_{week[-1].isoformat()}"
        filename = _ta_export.clean_filename(data.get('filename')) or _ta_export.clean_filename(name_bits)
        if len(formats) == 1:
            mime, ext = _ta_export.MIME[formats[0]]
            return Response(_ws_export.GENERATORS[formats[0]](doc, _SCH_LOGO_PATH), mimetype=mime,
                            headers={'Content-Disposition': f'attachment; filename="{filename}{ext}"'})
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in formats:
                zf.writestr(filename + _ta_export.MIME[f][1], _ws_export.GENERATORS[f](doc, _SCH_LOGO_PATH))
        return Response(buf.getvalue(), mimetype='application/zip',
                        headers={'Content-Disposition': f'attachment; filename="{filename}.zip"'})
    except Exception:
        import traceback; traceback.print_exc()
        return jsonify({'error': 'The export could not be generated. Please try again.'}), 500
    finally:
        cur.close(); conn.close()

#===================================================================================================
# 8. MAKE-UP REQUEST SAVE HELPER (needs makeup_requests.py)
#===================================================================================================

# ---- _save_makeup_request   (my app.py lines 26165-26232)
# PASTE AFTER: def api_faculty_schedule_full(...)  (the end of that function)
def _save_makeup_request(data, faculty_empno, existing_requestid=None):
    _ensure_request_tables()
    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': 'server_error',
                        'message': 'Database connection failed.'}), 500
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if existing_requestid is None:
            subject = (data.get('subject_code') or '').strip()
            try:
                section_id = int(data.get('section_id'))
            except (TypeError, ValueError):
                section_id = None
            if not subject or not section_id:
                raise _makeup.MakeupError(400, 'validation_error', 'Please select a subject and section.')
            ctx = _makeup.schedule_context_by_subject_section(cur, subject, section_id)
        else:
            cur.execute("""
                SELECT requestid, scheduleid FROM class_meeting_request
                WHERE requestid = %s AND submitted_by = %s AND status = 'Pending'
                FOR UPDATE
            """, [existing_requestid, str(faculty_empno)])
            existing = cur.fetchone()
            if not existing:
                raise _makeup.MakeupError(404, 'not_found', 'Request not found or no longer editable.')
            ctx = _makeup.schedule_context_by_id(cur, existing['scheduleid'])

        vals = _makeup.validate_submission(
            cur, faculty_empno=faculty_empno, ctx=ctx,
            request_date=data.get('request_date'),
            start_time=data.get('start_time'), end_time=data.get('end_time'),
            room_id=data.get('room_id'), reason=data.get('reason'),
            exclude_requestid=existing_requestid)
        notes = (data.get('note') or '').strip() or None

        if existing_requestid is None:
            cur.execute("""
                INSERT INTO class_meeting_request
                    (scheduleid, requested_date, new_starttimeid, new_endtimeid,
                     new_roomid, reason, notes, submitted_by, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pending')
                RETURNING requestid
            """, [vals['scheduleid'], vals['requested_date'], vals['starttimeid'],
                  vals['endtimeid'], vals['roomid'], vals['reason'], notes, str(faculty_empno)])
        else:
            cur.execute("""
                UPDATE class_meeting_request
                SET requested_date = %s, new_starttimeid = %s, new_endtimeid = %s,
                    new_roomid = %s, reason = %s
                WHERE requestid = %s AND status = 'Pending'
                RETURNING requestid
            """, [vals['requested_date'], vals['starttimeid'], vals['endtimeid'],
                  vals['roomid'], vals['reason'], existing_requestid])
        saved = cur.fetchone()
        conn.commit()
        return jsonify({'success': True, 'requestid': saved['requestid'], 'status': 'Pending'})
    except _makeup.MakeupError as me:
        conn.rollback()
        return jsonify(me.to_json()), me.status
    except Exception as e:
        conn.rollback()
        print(f"[_save_makeup_request] Error: {e}")
        return jsonify({'success': False, 'error': 'server_error',
                        'message': 'The make-up request could not be saved. Please try again.'}), 500
    finally:
        cur.close()
        conn.close()

####################################################################################################
# PART 3 - REPLACE THESE WHOLE FUNCTIONS WITH MY VERSION
####################################################################################################

# ---- REPLACE whole function: def login   (my app.py lines 685-771)
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
                if not user_data.get('isactive', True):
                    flash("Your account has been deactivated. Please contact the Administrator.")
                    session['saved_username'] = username
                    return redirect(url_for('login'))

                must_change = bool(user_data.get('must_change_password'))
                setup_done  = bool(user_data.get('account_setup_complete', True))

                # Accounts created before must_change_password existed (or created by a
                # path that doesn't set it -- employee-import flows use the Employee
                # Number as both username and password, the admin "add account" form
                # uses PUP@{username}, and the /setup dev bootstrap route uses
                # 'password123') never got flagged even though they're still on a
                # system-assigned default password. Detect that here from the password
                # actually submitted and self-heal the flag so this only runs once.
                if not must_change and password in (f"PUP@{username}", username, 'password123'):
                    must_change = True
                    setup_done  = False
                    try:
                        _c3 = get_db_connection(); _cur3 = _c3.cursor()
                        _cur3.execute("""
                            UPDATE accounts SET must_change_password = TRUE, account_setup_complete = FALSE
                            WHERE username = %s
                        """, (username,))
                        _c3.commit(); _cur3.close(); _c3.close()
                    except Exception: pass

                session.clear()
                session['loggedin'] = True
                session['username'] = user_data.get('username')
                session['role']     = db_role
                session['must_change_password']   = must_change
                session['account_setup_complete'] = setup_done
                try:
                    _conn2 = get_db_connection(); _cur2 = _conn2.cursor()
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
                flash("Invalid username or password.")
                session['saved_username'] = username
        else:
            flash("Invalid username or password.")
            session['saved_username'] = username

        return redirect(url_for('login'))

    # Already logged in (e.g. the browser Back button walked back onto /login
    # from a dashboard): send them to their own dashboard instead of wiping the
    # session. Logging out is done only through /logout.
    if session.get('loggedin') and _dashboard_url_for_role(session.get('role')) != 'login':
        return redirect(url_for(_dashboard_url_for_role(session.get('role'))))

    saved_username = session.get('saved_username', '')
    flashes = session.get('_flashes')
    session.clear()
    if flashes:
        session['_flashes'] = flashes
    resp = make_response(render_template('login.html', saved_username=saved_username))
    # Never serve the login page from the back/forward cache, so Back always
    # re-asks the server (which redirects a logged-in user to their dashboard).
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp

# ---- REPLACE whole function: def _enforce_account_setup   (my app.py lines 952-984)
@app.before_request
def _enforce_account_setup():
    """Single enforcement point for every already-logged-in request:
    1. A deactivated account loses access immediately, even mid-session --
       re-checked fresh from the DB on every request (not just at login),
       so an Admin deactivating someone right now cuts them off right now,
       not just on their next login attempt. Covers API/AJAX calls too.
    2. Forces a just-created/just-reset account through the setup wizard
       (change password -> confirm contact info -> verify personal info)
       before it can reach any other page.
    This means the other ~150 existing routes don't each need their own check."""
    if not session.get('loggedin'):
        return
    ep = request.endpoint or ''
    if request.path.startswith('/static/') or ep in ('login', 'logout'):
        return

    row = query_db("SELECT isactive FROM accounts WHERE username = %s", (session.get('username'),), one=True)
    if not row or not row.get('isactive'):
        session.clear()
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': "Your account has been deactivated. Please contact the Administrator."}), 403
        flash("Your account has been deactivated. Please contact the Administrator.")
        return redirect(url_for('login'))

    if request.path.startswith('/api/'):
        return
    if ep.startswith('account_setup_'):
        return
    if session.get('must_change_password'):
        return redirect(url_for('account_setup_password'))
    if not session.get('account_setup_complete', True):
        return redirect(url_for('account_setup_email'))

# ---- REPLACE whole function: def account_setup_password   (my app.py lines 1047-1096)
@app.route('/account-setup/password', methods=['GET', 'POST'])
def account_setup_password():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    if not session.get('must_change_password'):
        if session.get('account_setup_complete', True):
            return redirect(url_for(_dashboard_url_for_role(session.get('role'))))
        # Came Back to step 1 after already changing the password: show it as done.
        if request.method == 'POST':
            return redirect(url_for('account_setup_email'))
        return render_template('setup/setup_password.html', password_done=True, **_setup_ctx('password'))

    if request.method == 'POST':
        username   = session.get('username')
        current_pw = request.form.get('current_password', '')
        new_pw     = request.form.get('new_password', '')
        confirm_pw = request.form.get('confirm_password', '')

        row = query_db("SELECT passwordhash FROM accounts WHERE username = %s", (username,), one=True)
        if not row or not check_password_hash(row['passwordhash'], current_pw):
            flash("Current password is incorrect.", "error")
        elif _pwchange.password_problem(new_pw, username):
            flash(_pwchange.password_problem(new_pw, username), "error")
        elif new_pw != confirm_pw:
            flash("New password and confirmation do not match.", "error")
        elif check_password_hash(row['passwordhash'], new_pw):
            flash("New password must be different from your current password.", "error")
        else:
            conn = get_db_connection(); cur = conn.cursor()
            try:
                cur.execute("""
                    UPDATE accounts SET passwordhash = %s, must_change_password = FALSE,
                                        password_changed_at = NOW()
                    WHERE username = %s
                """, (generate_password_hash(new_pw), username))
                conn.commit()
                session['must_change_password'] = False
                if not session.get('account_setup_complete', True):
                    return redirect(url_for('account_setup_email'))
                flash("Password updated.", "success")
                return redirect(url_for(_dashboard_url_for_role(session.get('role'))))
            except Exception as e:
                conn.rollback()
                print(f"[account setup] password update failed: {type(e).__name__}")
                flash("Your password could not be updated. Please try again.", "error")
            finally:
                cur.close(); conn.close()
        return redirect(url_for('account_setup_password'))

    return render_template('setup/setup_password.html', password_done=False, **_setup_ctx('password'))

# ---- REPLACE whole function: def account_setup_info   (my app.py lines 1099-1102)
@app.route('/account-setup/info', methods=['GET', 'POST'])
def account_setup_info():
    # Former single "profile info" step, now split into Email / Photo / Contact.
    return redirect(url_for('account_setup_email'))

# ---- REPLACE whole function: def account_setup_verify   (my app.py lines 1237-1350)
@app.route('/account-setup/verify', methods=['GET', 'POST'])
def account_setup_verify():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    if session.get('must_change_password'):
        return redirect(url_for('account_setup_password'))
    if session.get('account_setup_complete', True):
        return redirect(url_for(_dashboard_url_for_role(session.get('role'))))

    username = session.get('username')
    info = query_db("""
        SELECT f.employeenumber, f.firstname, f.middlename, f.lastname, f.suffix,
               f.email, f.contactnumber, d.designationname, s.specializationname
        FROM accounts a
        LEFT JOIN faculty f ON a.employeenumber = f.employeenumber
        LEFT JOIN designation d ON f.designationid = d.designationid
        LEFT JOIN specialization s ON f.specializationid = s.specializationid
        WHERE a.username = %s
    """, (username,), one=True)

    if not info or not info.get('employeenumber'):
        # No linked faculty record (e.g. a pure Admin account) -- nothing to verify.
        conn = get_db_connection(); cur = conn.cursor()
        try:
            cur.execute("UPDATE accounts SET account_setup_complete = TRUE WHERE username = %s", (username,))
            conn.commit()
        finally:
            cur.close(); conn.close()
        session['account_setup_complete'] = True
        session.pop('setup_draft', None)
        flash("Account setup complete.", "success")
        return redirect(url_for(_dashboard_url_for_role(session.get('role'))))

    full_name = ' '.join(filter(None, [info.get('firstname'), info.get('middlename'),
                                        info.get('lastname'), info.get('suffix')]))
    current_values = {
        'Employee ID':    info.get('employeenumber') or '',
        'First Name':     info.get('firstname') or '',
        'Middle Name':    info.get('middlename') or '',
        'Last Name':      info.get('lastname') or '',
        'Suffix':         info.get('suffix') or '',
        'Designation':    info.get('designationname') or '',
        'Specialization': info.get('specializationname') or '',
    }

    if request.method == 'POST':
        action = request.form.get('action')
        conn = get_db_connection(); cur = conn.cursor()
        try:
            if action == 'correction':
                try:
                    items = json.loads(request.form.get('corrections_json', '[]'))
                except (ValueError, TypeError):
                    items = []
                remarks  = request.form.get('remarks', '').strip() or None
                batch_id = uuid.uuid4().hex[:12]
                inserted = 0

                for item in items if isinstance(items, list) else []:
                    if not isinstance(item, dict):
                        continue
                    field_name     = str(item.get('field_name', '')).strip()
                    proposed_value = str(item.get('proposed_value', '')).strip()
                    if field_name not in _CORRECTION_FIELDS:
                        continue
                    current_value = current_values.get(field_name, '')
                    # Middle Name / Suffix may legitimately be corrected to blank
                    # (e.g. clearing a wrongly-entered value); every other field
                    # needs an actual value. Either way, skip a "correction" that
                    # doesn't actually change anything.
                    if field_name not in ('Middle Name', 'Suffix') and not proposed_value:
                        continue
                    if proposed_value == current_value:
                        continue
                    cur.execute("""
                        INSERT INTO personal_info_correction_request
                            (username, employeenumber, field_name, current_value, proposed_value, remarks, batch_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (username, info.get('employeenumber'), field_name,
                          current_value, proposed_value, remarks, batch_id))
                    inserted += 1

                if inserted == 0:
                    flash("Please select at least one field to correct and provide a new value.", "error")
                    return redirect(url_for('account_setup_verify'))

                cur.execute("UPDATE accounts SET account_setup_complete = TRUE WHERE username = %s", (username,))
                conn.commit()
                flash(f"Correction request submitted for admin review ({inserted} field"
                      f"{'s' if inserted != 1 else ''}). Your official record stays unchanged until it's approved.", "success")
            else:
                cur.execute("UPDATE accounts SET account_setup_complete = TRUE WHERE username = %s", (username,))
                conn.commit()
                flash("Account setup complete. Welcome!", "success")
            session['account_setup_complete'] = True
            session.pop('setup_draft', None)
            return redirect(url_for(_dashboard_url_for_role(session.get('role'))))
        except Exception as e:
            conn.rollback()
            print(f"[account setup] complete failed: {type(e).__name__}")
            flash("Your setup could not be completed. Please try again.", "error")
            return redirect(url_for('account_setup_verify'))
        finally:
            cur.close(); conn.close()

    designations    = query_db("SELECT designationid, designationname FROM designation ORDER BY designationname")
    specializations = query_db("SELECT specializationid, specializationname FROM specialization WHERE isactive = TRUE ORDER BY specializationname")

    return render_template('setup/setup_verify.html',
                           info=current_values, full_name=full_name,
                           account_email=info.get('email') or '',
                           account_contact=info.get('contactnumber') or '',
                           designations=designations, specializations=specializations,
                           back_url=url_for('account_setup_contact'), **_setup_ctx('verify'))

# ---- REPLACE whole function: def faculty_schedule   (my app.py lines 24679-24743)
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
        my_emp_num=_session_employee_number() or '',
        # Export dialog = the Academic Head's (templates/_schedule_export_modal.html),
        # with Academic Year / Semester fixed to the active term.
        programs=programs,
        se_fixed_term=({'ay_id': active_ay_id, 'yearstart': active['yearstart'],
                        'yearend': active['yearend'], 'sem': active_sem} if active else None),
    )

# ---- REPLACE whole function: def api_faculty_my_teaching_load   (my app.py lines 25179-25206)
@app.route('/api/faculty/my_teaching_load')
def api_faculty_my_teaching_load():
    """Authoritative Regular/Part-Time/TS load + assignment rows for the
    logged-in user's own "Teaching Assignment" tab (Faculty or Academic Head
    viewing their own load). Reuses faculty_load.py (the project's single
    source of truth for load, already used correctly by the Academic Head's
    Manual Editor) instead of re-deriving classification or unit totals here.
    `source=official` (default) is always Published-only, never Draft/Archived.
    `source=local` reads Published local-arrangement sessions instead — the
    two are never combined into one set of numbers."""
    if 'loggedin' not in session:
        return jsonify({'success': False}), 401

    emp_num = _session_employee_number()
    if not emp_num:
        return jsonify({'success': False, 'error': 'No employee number linked to this account.'}), 400

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        payload, err = _my_teaching_load_payload(cur, emp_num, request.args.get('ay_id'),
                                                 request.args.get('semester'), request.args.get('source'))
        if err:
            return jsonify({'success': False, 'error': err[0]}), err[1]
        return jsonify(payload)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close(); conn.close()

# ---- REPLACE whole function: def api_user_me   (my app.py lines 25684-25731)
@app.route('/api/user/me')
def api_user_me():
    if 'loggedin' not in session: return jsonify({'success': False}), 401
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        username = session.get('username')
        cur.execute("""
            SELECT a.last_login, a.profile_photo,
                   f.employeenumber, f.firstname, f.middlename, f.lastname,
                   CASE WHEN a.employeenumber IS NULL THEN a.email ELSE f.email END AS email,
                   f.employeestatus,
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
            'fullname': (f"{row['firstname'] or ''} {row['lastname'] or ''}".strip()
                         or ('Administrator' if session.get('role') == 'Admin' else '')),
            'firstname': row['firstname'] or '',
            'middlename': row['middlename'] or '',
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
            'profile_editable': _PROFILE_SELF_EDIT_ENABLED,
            'photo_editable': True,   # own profile picture is always changeable
        })
    except Exception as e:
        conn.rollback(); return jsonify({'success': False, 'error': str(e)}), 500
    finally: cur.close(); conn.close()

# ---- REPLACE whole function: def api_user_photo_upload   (my app.py lines 25775-25783)
@app.route('/api/user/photo/upload', methods=['POST'])
def api_user_photo_upload():
    # A user's own profile picture can always be changed — unlike Designation /
    # Specialization / Employment Status, which stay behind _PROFILE_SELF_EDIT_ENABLED.
    if 'loggedin' not in session: return jsonify({'success': False}), 401
    url, err = _save_profile_photo(session.get('username'), request.files.get('photo'))
    if err:
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({'success': True, 'photo_url': url})

# ---- REPLACE whole function: def api_user_password_change   (my app.py lines 25828-25841)
@app.route('/api/user/password/change', methods=['POST'])
def api_user_password_change():
    def _change(c, u):
        result = _pwchange.change_password(
            c, u, session.get('pw_otp_verified_at'),
            request.form.get('current_password', ''),
            request.form.get('new_password', ''),
            request.form.get('confirm_password', ''))
        session.pop('pw_otp_verified_at', None)
        write_activity_log(action='Password Changed',
                           details=f"{u} changed their password (verified by email code)",
                           category='account', color='blue')
        return jsonify(result)
    return _password_step(_change)

# ---- REPLACE whole function: def api_faculty_update_request   (my app.py lines 24889-24977)
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

    if req_type == 'makeup':
        if not emp_num:
            return jsonify({'success': False, 'error': 'Could not determine faculty employee number.'}), 400
        return _save_makeup_request(data, emp_num, existing_requestid=req_id)

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
            if req_type == 'adjustment':
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

####################################################################################################
# PART 4 - CAREFUL: SHE ALSO CHANGED THESE. APPLY ONLY MY CHANGES (diff below)
####################################################################################################
# '-' = line to remove, '+' = line to add. Compared against jajaBranch on GitHub.

# ---- def _ensure_request_tables   (my app.py lines 303-358)
# --- hers
# +++ mine
@@ -41,6 +41,9 @@
             )
         """)
-        for _col in ["decided_by VARCHAR(100)", "decided_at TIMESTAMP", "remarks TEXT", "end_date DATE", "official_sessionid INT"]:
+        for _col in ["decided_by VARCHAR(100)", "decided_at TIMESTAMP", "remarks TEXT", "end_date DATE"]:
             _cur.execute(f"ALTER TABLE public.schedule_change_request ADD COLUMN IF NOT EXISTS {_col}")
+
+        # Date-specific make-up exceptions live in schedule_exception_log
+        _makeup.ensure_schema(_cur)
 
         _conn.commit()

# ---- def api_requests_validate   (my app.py lines 1893-2060)
# --- hers
# +++ mine
@@ -1,2 +1,3 @@
+@app.route('/api/requests/validate')
 def api_requests_validate():
     _ensure_request_tables()
@@ -9,119 +10,59 @@
     try:
         if req_type == 'makeup':
-            row = query_db("""
-                SELECT cmr.*, sc.employeenumber, sc.scheduleid AS orig_sched,
-                       ts_s.timevalue AS start_t, ts_e.timevalue AS end_t
-                FROM class_meeting_request cmr
-                JOIN schedule sc ON cmr.scheduleid = sc.scheduleid
-                JOIN timeslot ts_s ON cmr.new_starttimeid = ts_s.timeid
-                JOIN timeslot ts_e ON cmr.new_endtimeid   = ts_e.timeid
-                WHERE cmr.requestid = %s
-            """, [req_id], one=True)
-            if not row:
-                return jsonify({'success': False, 'error': 'Not found'}), 404
+            # Date-specific check against the CURRENT database state, scoped to
+            # the request's own semester. Shares its conflict definitions with
+            # submission and with the final re-check inside /api/requests/decide
+            # (which remains authoritative — this endpoint is a preview only).
+            _vconn = get_db_connection()
+            try:
+                _vcur = _vconn.cursor(cursor_factory=RealDictCursor)
+                _vcur.execute("""
+                    SELECT cmr.requestid, cmr.scheduleid, cmr.requested_date, cmr.status,
+                           cmr.new_starttimeid, cmr.new_endtimeid, cmr.new_roomid
+                    FROM class_meeting_request cmr
+                    WHERE cmr.requestid = %s
+                """, [req_id])
+                row = _vcur.fetchone()
+                if not row:
+                    return jsonify({'success': False, 'error': 'not_found',
+                                    'message': 'Make-up request not found.'}), 404
+                ctx = _makeup.schedule_context_by_id(_vcur, row['scheduleid'])
+                if not ctx:
+                    return jsonify({'success': False, 'error': 'schedule_not_found',
+                                    'message': 'The class schedule for this request no longer exists.'}), 404
+                hard, warnings = _makeup.request_conflict_snapshot(_vcur, row, ctx)
+            finally:
+                _vconn.close()
 
-            from datetime import date as _date
-            req_date  = row['requested_date']
-            day_name  = req_date.strftime('%A') if req_date else None
-            start_t   = row['start_t']
-            end_t     = row['end_t']
-            room_id   = row['new_roomid']
-            emp_num   = row['employeenumber']
-            sched_id  = row['scheduleid']
+            kinds = {c['type'] for c in hard}
+            room_available = 'room' not in kinds
+            faculty_free   = 'faculty' not in kinds
+            section_free   = 'section' not in kinds
+            program_ok     = not warnings
+            return jsonify({
+                'success':        True,
+                'status':         row['status'],
+                'requested_date': row['requested_date'].isoformat(),
+                'semesterid':     ctx['semesterid'],
+                'room_available': room_available,
+                'faculty_free':   faculty_free,
+                'section_free':   section_free,
+                'program_ok':     program_ok,
+                'conflicts':      [c['message'] for c in hard],
+                'conflict_details': hard,
+                'warnings':       warnings,
+                # Program/year-level overlap is advisory; hard conflicts block approval.
+                'all_ok':         not hard,
+                'constraint_rules': list(dict.fromkeys(
+                    _context_conflicts.request_conflict_rules(
+                        room_conflict=not room_available,
+                        faculty_conflict=not faculty_free,
+                        cohort_conflict=not program_ok,
+                    ) + ([] if section_free else [_context_conflicts.SECTION_CONFLICT_RULE,
+                                                  _context_conflicts.CROSS_SCHEDULE_RULE])
+                )),
+            })
 
-            # 1. Room: published schedule conflict
-            rc1 = query_db("""
-                SELECT 1 FROM schedule_sessions ss
-                JOIN schedule_version sv ON ss.versionid = sv.versionid
-                JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
-                JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
-                WHERE sv.status = 'Published' AND ss.roomid = %s
-                  AND UPPER(ss.daydesc) = UPPER(%s)
-                  AND ts_s.timevalue < %s AND ts_e.timevalue > %s
-                LIMIT 1
-            """, [room_id, day_name, end_t, start_t]) or []
-
-            # 2. Room: other approved makeup on same date
-            rc2 = query_db("""
-                SELECT 1 FROM class_meeting_request c2
-                JOIN timeslot ts_s ON c2.new_starttimeid = ts_s.timeid
-                JOIN timeslot ts_e ON c2.new_endtimeid   = ts_e.timeid
-                WHERE c2.status = 'Approved' AND c2.new_roomid = %s
-                  AND c2.requested_date = %s
-                  AND ts_s.timevalue < %s AND ts_e.timevalue > %s
-                  AND c2.requestid != %s
-                LIMIT 1
-            """, [room_id, req_date, end_t, start_t, req_id]) or []
-
-            room_local = _request_local_conflict(
-                sched_id, day_name, start_t, end_t, room_id=room_id
-            )
-            room_available = not rc1 and not rc2 and not room_local
-
-            # 3. Faculty: published schedule (all schedules — makeup is an extra session, not a replacement)
-            fc1 = query_db("""
-                SELECT 1 FROM schedule_sessions ss
-                JOIN schedule_version sv ON ss.versionid = sv.versionid
-                JOIN schedule sc2 ON sv.scheduleid = sc2.scheduleid
-                JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
-                JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
-                WHERE sv.status = 'Published' AND sc2.employeenumber = %s
-                  AND UPPER(ss.daydesc) = UPPER(%s)
-                  AND ts_s.timevalue < %s AND ts_e.timevalue > %s
-                LIMIT 1
-            """, [emp_num, day_name, end_t, start_t]) or []
-
-            # 4. Faculty: other approved makeup on same date
-            fc2 = query_db("""
-                SELECT 1 FROM class_meeting_request c2
-                JOIN schedule sc2 ON c2.scheduleid = sc2.scheduleid
-                JOIN timeslot ts_s ON c2.new_starttimeid = ts_s.timeid
-                JOIN timeslot ts_e ON c2.new_endtimeid   = ts_e.timeid
-                WHERE c2.status = 'Approved' AND sc2.employeenumber = %s
-                  AND c2.requested_date = %s
-                  AND ts_s.timevalue < %s AND ts_e.timevalue > %s
-                  AND c2.requestid != %s
-                LIMIT 1
-            """, [emp_num, req_date, end_t, start_t, req_id]) or []
-
-            faculty_local = _request_local_conflict(
-                sched_id, day_name, start_t, end_t, employee_number=emp_num
-            )
-            faculty_free = not fc1 and not fc2 and not faculty_local
-
-            # 5. Program conflict
-            pyl = query_db("""
-                SELECT sec.programyearlevelid FROM schedule sc2
-                JOIN sections sec ON sc2.sectionid = sec.sectionid
-                WHERE sc2.scheduleid = %s LIMIT 1
-            """, [sched_id], one=True)
-            if pyl:
-                pc1 = query_db("""
-                    SELECT 1 FROM schedule_sessions ss
-                    JOIN schedule_version sv ON ss.versionid = sv.versionid
-                    JOIN schedule sc2 ON sv.scheduleid = sc2.scheduleid
-                    JOIN sections sec ON sc2.sectionid = sec.sectionid
-                    JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
-                    JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
-                    WHERE sv.status = 'Published'
-                      AND sec.programyearlevelid = %s
-                      AND UPPER(ss.daydesc) = UPPER(%s)
-                      AND ts_s.timevalue < %s AND ts_e.timevalue > %s
-                      AND sc2.scheduleid != %s
-                    LIMIT 1
-                """, [pyl['programyearlevelid'], day_name, end_t, start_t, sched_id]) or []
-                program_local = _request_local_conflict(
-                    sched_id, day_name, start_t, end_t,
-                    programyearlevelid=pyl['programyearlevelid']
-                )
-                program_local = _request_local_conflict(
-                    sched_id, day, start_t, end_t,
-                    programyearlevelid=pyl['programyearlevelid']
-                )
-                program_ok = not pc1 and not program_local and not program_local
-            else:
-                program_ok = True
-
-        elif req_type == 'adjustment':
+        if req_type == 'adjustment':
             row = query_db("""
                 SELECT scr.*, sc.employeenumber, sc.scheduleid AS orig_sched,
@@ -155,8 +96,5 @@
                     LIMIT 1
                 """, [room_id, day, end_t, start_t, sched_id]) or []
-                room_local = _request_local_conflict(
-                    sched_id, day, start_t, end_t, room_id=room_id
-                )
-                room_available = not rc1 and not room_local
+                room_available = not rc1
             else:
                 room_available = True
@@ -175,8 +113,5 @@
                     LIMIT 1
                 """, [emp_num, day, end_t, start_t, sched_id]) or []
-                faculty_local = _request_local_conflict(
-                    sched_id, day, start_t, end_t, employee_number=emp_num
-                )
-                faculty_free = not fc1 and not faculty_local
+                faculty_free = not fc1
             else:
                 faculty_free = True
@@ -202,9 +137,5 @@
                     LIMIT 1
                 """, [pyl['programyearlevelid'], day, end_t, start_t, sched_id]) or []
-                program_local = _request_local_conflict(
-                    sched_id, day, start_t, end_t,
-                    programyearlevelid=pyl['programyearlevelid']
-                )
-                program_ok = not pc1 and not program_local
+                program_ok = not pc1
             else:
                 program_ok = True

# ---- def api_requests_decide   (my app.py lines 2063-2187)
# --- hers
# +++ mine
@@ -2,5 +2,4 @@
 def api_requests_decide():
     _ensure_request_tables()
-    _ensure_local_tables()
     if 'loggedin' not in session or session.get('role') != 'Academic Head':
         return jsonify({'success': False, 'error': 'Unauthorized'}), 401
@@ -15,4 +14,35 @@
         return jsonify({'success': False, 'error': 'Invalid decision'}), 400
 
+    if req_type == 'makeup':
+        # A make-up class is a one-time, date-specific exception: approval writes
+        # one schedule_exception_log row and never touches the recurring
+        # schedule_sessions grid. Lock, state check, conflict re-check, status
+        # update and exception insert all happen in one transaction.
+        reviewer = session.get('employeenumber')
+        if not reviewer:
+            _acc = query_db("SELECT employeenumber FROM accounts WHERE username = %s",
+                            [session.get('username')], one=True)
+            reviewer = _acc['employeenumber'] if _acc else None
+        _mconn = get_db_connection()
+        if not _mconn:
+            return jsonify({'success': False, 'error': 'server_error',
+                            'message': 'Database connection failed.'}), 500
+        try:
+            result = _makeup.decide(_mconn, request_id=req_id, decision=decision,
+                                    reviewer=reviewer, remarks=remarks)
+        except _makeup.MakeupError as me:
+            return jsonify(me.to_json()), me.status
+        finally:
+            _mconn.close()
+        write_activity_log(
+            action=f"Request {decision}",
+            details=(f"MAKEUP request #{result['requestid']} {decision.lower()} by {reviewer}"
+                     + (f" — one-time exception #{result['exception_logid']}"
+                        if result.get('exception_logid') else '')),
+            category='approval',
+            color='green' if decision == 'Approved' else 'orange'
+        )
+        return jsonify({'success': True, **result})
+
     decided_by = session.get('employeenumber') or session.get('username', 'unknown')
 
@@ -20,143 +50,60 @@
     _cur  = _conn.cursor(cursor_factory=RealDictCursor)
     try:
-        if req_type == 'makeup':
+        if req_type == 'adjustment':
             _cur.execute("""
-                UPDATE class_meeting_request
+                UPDATE schedule_change_request
                 SET status = %s, decided_by = %s, decided_at = NOW(), remarks = %s,
                     reviewed_by = %s, reviewed_at = NOW()
                 WHERE requestid = %s
             """, [decision, decided_by, remarks, decided_by, req_id])
-            if decision == 'Approved':
-                _cur.execute("SELECT * FROM class_meeting_request WHERE requestid = %s", [req_id])
-                mk = _cur.fetchone()
-                if mk and mk.get('scheduleid') and mk.get('new_starttimeid') and mk.get('new_endtimeid') and mk.get('requested_date'):
-                    _cur.execute("""
-                        SELECT versionid FROM schedule_version
-                        WHERE scheduleid = %s AND status = 'Published'
-                        ORDER BY version_number DESC LIMIT 1
-                    """, [mk['scheduleid']])
-                    ver = _cur.fetchone()
-                    if ver:
-                        # Room Schedule has no concept of a one-time calendar date — it's a
-                        # purely weekly-recurring grid keyed by day-of-week. Derive the
-                        # day-of-week from the requested date so the approved make-up class
-                        # is visible there (it will show as a normal weekly slot on that day).
-                        day_of_week = mk['requested_date'].strftime('%A')
-                        _cur.execute("""
-                            SELECT 1 FROM schedule_sessions
-                            WHERE versionid = %s AND daydesc = %s
-                              AND starttimeid = %s AND endtimeid = %s
-                              AND roomid IS NOT DISTINCT FROM %s
-                        """, [ver['versionid'], day_of_week, mk['new_starttimeid'], mk['new_endtimeid'], mk.get('new_roomid')])
-                        if not _cur.fetchone():
-                            _cur.execute("""
-                                INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid)
-                                VALUES (%s, %s, %s, %s, %s)
-                            """, [ver['versionid'], day_of_week, mk['new_starttimeid'], mk['new_endtimeid'], mk.get('new_roomid')])
-
-        elif req_type == 'adjustment':
-            _cur.execute("""
-                SELECT scr.*, sc.employeenumber, sc.semesterid, sc.sectionid,
-                       cs.subjectcode, pyl.programcode, pyl.yearlevel,
-                       ss.daydesc AS official_daydesc,
-                       ss.starttimeid AS official_starttimeid,
-                       ss.endtimeid AS official_endtimeid,
-                       ss.roomid AS official_roomid
-                FROM schedule_change_request scr
-                JOIN schedule sc ON scr.scheduleid = sc.scheduleid
-                JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
-                JOIN sections sec ON sc.sectionid = sec.sectionid
-                LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
-                LEFT JOIN schedule_sessions ss
-                  ON ss.sessionid = scr.official_sessionid AND ss.versionid = scr.versionid
-                WHERE scr.requestid = %s
-                FOR UPDATE
-            """, [req_id])
-            adj = _cur.fetchone()
-            if not adj:
-                return jsonify({'success': False, 'error': 'Schedule adjustment request not found.'}), 404
 
             if decision == 'Approved':
-                if not adj.get('official_sessionid') or not adj.get('official_daydesc'):
-                    _conn.rollback()
-                    return jsonify({'success': False, 'error':
-                        'This is a legacy Schedule Adjustment without an exact Official meeting. Ask the faculty to submit the request again before approving it.'}), 409
-
                 _cur.execute("""
-                    SELECT 1 FROM schedule_sessions ss
-                    JOIN schedule_version sv ON ss.versionid = sv.versionid
-                    WHERE ss.sessionid=%s AND ss.versionid=%s
-                      AND sv.scheduleid=%s AND sv.status='Published'
-                    LIMIT 1
-                """, [adj['official_sessionid'], adj['versionid'], adj['scheduleid']])
-                if not _cur.fetchone():
-                    _conn.rollback()
-                    return jsonify({'success': False, 'error':
-                        'The Official meeting changed after this request was submitted. Please submit a new adjustment.'}), 409
-
-                reason_key = f"Approved Schedule Adjustment request #{req_id}"
-                _cur.execute("""
-                    SELECT la.arrangementid
-                    FROM public.local_arrangement la
-                    JOIN public.local_arrangement_sessions las ON las.arrangementid=la.arrangementid
-                    WHERE la.status='Published' AND la.is_active=TRUE
-                      AND las.official_sessionid=%s AND la.override_reason=%s
-                    LIMIT 1
-                """, [adj['official_sessionid'], reason_key])
-                existing = _cur.fetchone()
-
-                if not existing:
-                    # C3 supersession rule: only one operational Local override may
-                    # replace a given Official occurrence. Archive any older override
-                    # before publishing this newly approved adjustment.
-                    _cur.execute("""
-                        UPDATE public.local_arrangement la
-                        SET status='Archived', is_active=FALSE,
-                            archive_reason='Superseded by a newer approved Schedule Adjustment',
-                            archived_at=CURRENT_TIMESTAMP
-                        WHERE la.status='Published' AND la.is_active=TRUE
-                          AND EXISTS (
-                              SELECT 1 FROM public.local_arrangement_sessions las
-                              WHERE las.arrangementid=la.arrangementid
-                                AND las.official_sessionid=%s
-                          )
-                    """, [adj['official_sessionid']])
-
-                    new_day   = adj.get('new_daydesc')     or adj['official_daydesc']
-                    new_start = adj.get('new_starttimeid') or adj['official_starttimeid']
-                    new_end   = adj.get('new_endtimeid')   or adj['official_endtimeid']
-                    new_room  = adj.get('new_roomid')      or adj['official_roomid']
-
-                    _cur.execute("""
-                        INSERT INTO public.local_arrangement
-                            (description, programcode, yearlevel, sectionid, semesterid,
-                             ref_versionid, override_reason, is_active, created_by, status)
-                        VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE,%s,'Published')
-                        RETURNING arrangementid
-                    """, (f"Approved Schedule Adjustment #{req_id}", adj.get('programcode'),
-                          adj.get('yearlevel'), adj.get('sectionid'), adj.get('semesterid'),
-                          adj.get('versionid'), reason_key, decided_by))
-                    arrangementid = _cur.fetchone()['arrangementid']
-                    _cur.execute("""
-                        INSERT INTO public.local_arrangement_sessions
-                            (arrangementid, subjectcode, daydesc, starttimeid, endtimeid,
-                             roomid, faculty_employeenumber, official_sessionid)
-                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
-                    """, (arrangementid, adj['subjectcode'], new_day, new_start, new_end,
-                          new_room, adj.get('employeenumber'), adj['official_sessionid']))
-
-                _cur.execute("""
-                    UPDATE schedule_change_request
-                    SET status=%s, decided_by=%s, decided_at=NOW(), remarks=%s,
-                        reviewed_by=%s, reviewed_at=NOW()
-                    WHERE requestid=%s
-                """, [decision, decided_by, remarks, decided_by, req_id])
-            else:
-                _cur.execute("""
-                    UPDATE schedule_change_request
-                    SET status=%s, decided_by=%s, decided_at=NOW(), remarks=%s,
-                        reviewed_by=%s, reviewed_at=NOW()
-                    WHERE requestid=%s
-                """, [decision, decided_by, remarks, decided_by, req_id])
+                    SELECT * FROM schedule_change_request WHERE requestid = %s
+                """, [req_id])
+                adj = _cur.fetchone()
+                if adj:
+                    upd_parts = []
+                    upd_vals  = []
+                    if adj.get('new_daydesc'):
+                        upd_parts.append('daydesc = %s')
+                        upd_vals.append(adj['new_daydesc'])
+                    if adj.get('new_starttimeid'):
+                        upd_parts.append('starttimeid = %s')
+                        upd_vals.append(adj['new_starttimeid'])
+                    if adj.get('new_endtimeid'):
+                        upd_parts.append('endtimeid = %s')
+                        upd_vals.append(adj['new_endtimeid'])
+                    if adj.get('new_roomid'):
+                        upd_parts.append('roomid = %s')
+                        upd_vals.append(adj['new_roomid'])
+                    if upd_parts and adj.get('versionid'):
+                        _cur.execute(
+                            "SELECT COUNT(*) AS cnt FROM schedule_sessions WHERE versionid = %s",
+                            [adj['versionid']]
+                        )
+                        _sess_count = _cur.fetchone()['cnt']
+                        if _sess_count == 1:
+                            upd_vals.append(adj['versionid'])
+                            _cur.execute(
+                                f"UPDATE schedule_sessions SET {', '.join(upd_parts)} WHERE versionid = %s",
+                                upd_vals
+                            )
+                        else:
+                            # This subject meets more than once a week under this versionid —
+                            # applying blindly would overwrite every meeting to the same new
+                            # day/time/room. Skip the auto-apply rather than corrupt the other
+                            # sessions; the Academic Head must adjust the specific meeting
+                            # manually via the Manual Editor.
+                            write_activity_log(
+                                action="Adjustment needs manual follow-up",
+                                details=(
+                                    f"Schedule Adjustment request #{req_id} was approved but "
+                                    f"versionid {adj['versionid']} has {_sess_count} sessions — "
+                                    f"auto-apply was skipped to avoid overwriting all of them. "
+                                    f"Please adjust the specific session manually in the Manual Editor."
+                                ),
+                                category='approval', color='orange'
+                            )
         else:
             return jsonify({'success': False, 'error': 'Invalid type'}), 400

# ---- def api_faculty_submit_request   (my app.py lines 26237-26350)
# --- hers
# +++ mine
@@ -14,5 +14,4 @@
         start_time = data.get('start_time', '')     # "07:30 AM"
         end_time   = data.get('end_time', '')
-        official_sessionid = data.get('official_sessionid')
 
         # Resolve submitted_by from session; fall back to accounts lookup
@@ -26,4 +25,7 @@
         if not submitted_by:
             return jsonify({'success': False, 'error': 'Could not determine faculty employee number.'}), 400
+
+        if req_type == 'makeup':
+            return _save_makeup_request(data, submitted_by)
 
         # Map "07:30 AM" → timeid in timeslot table
@@ -82,92 +84,21 @@
         try:
             cur = conn.cursor()
-            if req_type == 'makeup':
-                req_date = data.get('request_date') or None
-                if not scheduleid:
-                    return jsonify({'success': False,
-                        'error': 'No published schedule found for the given subject and section.'}), 400
-                if not start_tid or not end_tid:
-                    return jsonify({'success': False, 'error': 'Invalid time selection.'}), 400
-                # Block submission if faculty already has a committed schedule at the requested day/time
-                if req_date and start_time and end_time:
-                    try:
-                        day_of_week = _dt.strptime(req_date, '%Y-%m-%d').strftime('%A')
-                        fac_conflict = query_db("""
-                            SELECT cs.subjectcode FROM schedule_sessions ss
-                            JOIN schedule_version sv ON ss.versionid = sv.versionid
-                            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
-                            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
-                            JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
-                            JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
-                            WHERE sv.status = 'Published'
-                              AND sc.employeenumber = %s
-                              AND UPPER(ss.daydesc) = UPPER(%s)
-                              AND ts_s.timevalue < %s::time AND ts_e.timevalue > %s::time
-                            LIMIT 1
-                        """, [submitted_by, day_of_week, end_time, start_time], one=True)
-                        if fac_conflict:
-                            return jsonify({'success': False,
-                                'error': f"You already have {fac_conflict['subjectcode']} scheduled on {day_of_week} at this time. Make-up class cannot be submitted when you have an existing commitment."}), 400
-                        local_fac_conflict = _request_local_conflict(
-                            scheduleid, day_of_week,
-                            _dt.strptime(start_time, '%I:%M %p').time(),
-                            _dt.strptime(end_time, '%I:%M %p').time(),
-                            employee_number=submitted_by
-                        )
-                        if local_fac_conflict:
-                            return jsonify({'success': False,
-                                'error': f"You already have a Local Schedule commitment on {day_of_week} at this time. Make-up class cannot be submitted when you have an existing commitment."}), 400
-                    except Exception as _ce:
-                        print(f"[submit_request conflict check] {_ce}")
-                cur.execute("""
-                    INSERT INTO class_meeting_request
-                        (scheduleid, requested_date, new_starttimeid, new_endtimeid,
-                         new_roomid, reason, notes, submitted_by, status)
-                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Pending')
-                """, (scheduleid, req_date, start_tid, end_tid, rid, reason, notes, submitted_by))
-
-            elif req_type == 'adjustment':
+            if req_type == 'adjustment':
                 day = data.get('day', '') or None
-                try:
-                    official_sessionid = int(official_sessionid)
-                except (TypeError, ValueError):
-                    return jsonify({'success': False,
-                        'error': 'Please select the exact class meeting to adjust.'}), 400
-
                 if not scheduleid or not versionid:
                     return jsonify({'success': False,
                         'error': 'No published schedule found for the given subject and section.'}), 400
-
-                occurrence = query_db("""
-                    SELECT ss.sessionid
-                    FROM schedule_sessions ss
-                    JOIN schedule_version sv ON ss.versionid = sv.versionid
-                    WHERE ss.sessionid = %s
-                      AND sv.versionid = %s
-                      AND sv.scheduleid = %s
-                      AND sv.status = 'Published'
-                    LIMIT 1
-                """, [official_sessionid, versionid, scheduleid], one=True)
-                if not occurrence:
-                    return jsonify({'success': False,
-                        'error': 'The selected Official meeting is no longer available. Please select it again.'}), 400
-
                 parts = []
-                if day:
-                    parts.append('Day')
-                if start_tid and end_tid:
-                    parts.append('Time')
-                if rid:
-                    parts.append('Room')
+                if day:                   parts.append('Day')
+                if start_tid and end_tid: parts.append('Time')
+                if rid:                   parts.append('Room')
                 change_type = '+'.join(parts) if parts else 'Day'
-
                 cur.execute("""
                     INSERT INTO schedule_change_request
-                        (scheduleid, versionid, official_sessionid, change_type, new_daydesc,
+                        (scheduleid, versionid, change_type, new_daydesc,
                          new_starttimeid, new_endtimeid, new_roomid,
                          effective_from, reason, submitted_by, status)
-                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
-                            CURRENT_DATE, %s, %s, 'Pending')
-                """, (scheduleid, versionid, official_sessionid, change_type, day,
+                    VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_DATE, %s, %s, 'Pending')
+                """, (scheduleid, versionid, change_type, day,
                       start_tid, end_tid, rid, reason, submitted_by))
             else:

# ---- def api_faculty_check_request_conflicts   (my app.py lines 26353-26637)
# --- hers
# +++ mine
@@ -56,5 +56,46 @@
         room_detail      = ''
 
-        if emp_num:
+        # Make-up (one-time, date-specific): same conflict definitions as the
+        # authoritative submission/approval checks in makeup_requests.py —
+        # weekly Published sessions on that weekday, approved make-up exceptions
+        # on that exact date, and Local Arrangements, all within the class's semester.
+        mk_conflicts = []
+        if req_date:
+            from datetime import date as _d8, datetime as _dt8
+            try:
+                _mk_date = _d8.fromisoformat(req_date)
+                _mk_st   = _dt8.strptime(start_time, '%I:%M %p').time()
+                _mk_et   = _dt8.strptime(end_time,   '%I:%M %p').time()
+            except ValueError:
+                _mk_date = None
+            if _mk_date and _mk_et > _mk_st:
+                _mconn = get_db_connection()
+                try:
+                    _mcur = _mconn.cursor(cursor_factory=RealDictCursor)
+                    ctx = None
+                    if schedule_id.isdigit():
+                        ctx = _makeup.schedule_context_by_id(_mcur, int(schedule_id))
+                    elif subject_code and section_id.isdigit():
+                        ctx = _makeup.schedule_context_by_subject_section(_mcur, subject_code, int(section_id))
+                    _excl = request.args.get('request_id', '').strip()
+                    mk_conflicts = _makeup.find_conflicts(
+                        _mcur,
+                        semesterid=ctx['semesterid'] if ctx else sem_id_for_check,
+                        req_date=_mk_date, start_time=_mk_st, end_time=_mk_et,
+                        room_id=int(room_id) if room_id.isdigit() else None,
+                        employeenumber=ctx['employeenumber'] if ctx else emp_num,
+                        sectionid=ctx['sectionid'] if ctx else (int(section_id) if section_id.isdigit() else None),
+                        exclude_requestid=int(_excl) if _excl.isdigit() else None)
+                finally:
+                    _mconn.close()
+            for c in mk_conflicts:
+                if c['type'] == 'faculty':
+                    faculty_conflict, faculty_detail = True, c['message']
+                elif c['type'] == 'section':
+                    section_conflict, section_detail = True, c['message']
+                elif c['type'] == 'room':
+                    room_conflict, room_detail = True, c['message']
+
+        if emp_num and not req_date:
             _fac_excl = f"AND sv.scheduleid != {int(schedule_id)}" if schedule_id and schedule_id.isdigit() else ""
             _fac_params = [str(emp_num), day, end_time, start_time]
@@ -115,5 +156,5 @@
                     pass
 
-        if section_id:
+        if section_id and not req_date:
             _sect_excl = f"AND sv.scheduleid != {int(schedule_id)}" if schedule_id and schedule_id.isdigit() else ""
             _sect_params = [int(section_id), day, end_time, start_time]
@@ -144,5 +185,5 @@
                 section_detail = f"This section already has {r2['subjectcode']} ({r2['start_t']}–{r2['end_t']}) on {day}."
 
-        if room_id:
+        if room_id and not req_date:
             try:
                 _room_excl = f"AND sv.scheduleid != {int(schedule_id)}" if schedule_id and schedule_id.isdigit() else ""
@@ -238,5 +279,7 @@
             'room_detail':       room_detail,
             'duration_detail':   duration_detail,
+            'conflicts':         mk_conflicts,
         })
     except Exception as e:
-        return jsonify({'success': False, 'error': str(e)}), 500
+        print(f"[api_faculty_check_request_conflicts] Error: {e}")
+        return jsonify({'success': False, 'error': 'server_error'}), 500

# ---- def report_export   (my app.py lines 24213-24495)
# --- hers
# +++ mine
@@ -65,20 +65,6 @@
                 _sch_export_context(cur, ay_ids, sem_types, programs, year_levels, layout)
 
-            if section_id or room_id or faculty_id:
-                _fac_fname = None
-                if faculty_id:
-                    cur.execute("SELECT lastname, firstname, middlename FROM faculty WHERE employeenumber = %s", (faculty_id,))
-                    _f = cur.fetchone()
-                    _fac_fname = (f"{_f['lastname']}, {_f['firstname']}" + (f" {_f['middlename']}" if _f.get('middlename') else '')) if _f else None
-
-                def _filt(rs):
-                    if section_id: rs = [r for r in rs if str(r.get('SectionID')) == str(section_id)]
-                    if room_id:    rs = [r for r in rs if str(r.get('RoomID')) == str(room_id)]
-                    if faculty_id: rs = [r for r in rs if _fac_fname and r.get('Instructor') == _fac_fname]
-                    return rs
-
-                rows = _filt(rows); groups = _sch_exp_groups(rows)
-                if cal_rows is not None:
-                    cal_rows = _filt(cal_rows); cal_groups = _sch_exp_groups(cal_rows)
+            rows, groups, cal_rows, cal_groups = _sch_filter_export(
+                cur, rows, groups, cal_rows, cal_groups, section_id, faculty_id, room_id)
 
             mime_map = {

# ---- def _run_startup_migrations   (my app.py lines 31258-31540)
# --- hers
# +++ mine
@@ -216,4 +216,29 @@
     ])
 
+    # Profile picture + last login on the account. The image itself is a file in
+    # static/uploads/profile_photos/; profile_photo stores only its path. These
+    # used to be created lazily inside request handlers (login, /api/user/me,
+    # uploads) — now they're part of the schema.
+    _step('sm_account_profile_photo', [
+        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS last_login    TIMESTAMP",
+        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS profile_photo VARCHAR(255)",
+    ])
+
+    # Self-service password change: once-per-month limit + emailed verification code
+    # (see password_change.py / migrations/2026-09-28_password_change_otp.sql).
+    _step('sm_password_change_otp', _pwchange.ENSURE_SCHEMA_SQL)
+
+    # Forgot Password (logged out): hashed one-time codes + reset windows, kept in
+    # the database so they survive restarts (see password_reset.py /
+    # migrations/2026-09-29_password_reset_requests.sql).
+    _step('sm_password_reset_requests', _pwreset.ENSURE_SCHEMA_SQL)
+    import mailer as _mailer
+    if not _mailer.configured(Config):
+        print("[email] WARNING: no email service is configured — Forgot Password and Profile Settings "
+              "verification codes cannot be sent. Set BREVO_API_KEY + EMAIL_FROM, RESEND_API_KEY + "
+              "EMAIL_FROM, or SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM (see .env.example).", flush=True)
+    else:
+        print(f"[email] sending verification codes via {_mailer.provider(Config)}", flush=True)
+
     _step('sm_correction_requests', [
         """
