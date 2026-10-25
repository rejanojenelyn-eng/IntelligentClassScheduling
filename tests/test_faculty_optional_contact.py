"""Adding / importing a faculty member: email and contact number are optional (the faculty
member sets their own in Account Setup). The Add Faculty form no longer asks for them; Edit
keeps them as optional fields; when given they must still be valid.

Runs the real Flask routes (Academic Head and Admin) against the local database inside one
rolled-back transaction.
"""
import json

import pytest
from psycopg2.extras import RealDictCursor

from tests.conftest import requires_db, DB_AVAILABLE
from tests._shared_tx import SharedTx, install

pytestmark = requires_db

if DB_AVAILABLE:
    import app as app_module

IDS = ('OPTC-0001', 'OPTC-0002', 'OPTC-0003', 'OPTC-0004')


@pytest.fixture
def env(monkeypatch):
    t = SharedTx()
    install(monkeypatch, t)
    conn = t.handle()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT specializationid, specializationname FROM specialization WHERE isactive ORDER BY 1 LIMIT 1")
    spec_row = cur.fetchone()
    spec, spec_name = spec_row['specializationid'], spec_row['specializationname']
    cur.execute("SELECT employeetypeid FROM employeetype WHERE typename = 'Regular'")
    etype = cur.fetchone()['employeetypeid']
    cur.execute("SELECT username, role FROM accounts WHERE role IN ('Admin', 'Academic Head') AND isactive "
                "ORDER BY role, username")
    users = {r['role']: r['username'] for r in cur.fetchall()}
    if len(users) < 2:
        t.close()
        pytest.skip('need an active Admin and an active Academic Head')
    clients = {}
    for role, username in users.items():
        c = app_module.app.test_client()
        with c.session_transaction() as s:
            s.update(loggedin=True, username=username, role=role,
                     must_change_password=False, account_setup_complete=True)
        clients[role] = c

    def q(sql, args=()):
        c = conn.cursor(cursor_factory=RealDictCursor); c.execute(sql, args)
        r = c.fetchall() if c.description else []
        c.close(); return r

    try:
        yield type('Env', (), dict(q=staticmethod(q), acad=clients['Academic Head'], admin=clients['Admin'],
                                   spec=spec, spec_name=spec_name, etype=etype))
    finally:
        t.close()


def _new(env, emp, **extra):
    return dict(employee_number=emp, first_name='Opt', last_name='Contact', specialization_id=env.spec,
                type_id=env.etype, status='Permanent', **extra)


def _account(env, emp):
    rows = env.q("SELECT email, contactnumber, must_change_password, account_setup_complete "
                 "FROM accounts WHERE employeenumber = %s", (emp,))
    return rows[0] if rows else None


@pytest.mark.parametrize('who, url', [('acad', '/api/add_employee'), ('admin', '/admin/api/add_employee')])
def test_add_faculty_without_email_or_contact(env, who, url):
    client = getattr(env, who)
    r = client.post(url, json=_new(env, IDS[0]))
    assert r.status_code == 200 and r.get_json()['success'] is True, r.get_json()
    assert env.q("SELECT 1 FROM faculty WHERE employeenumber = %s", (IDS[0],))
    acc = _account(env, IDS[0])
    assert acc['email'] is None and acc['contactnumber'] is None            # set later in Account Setup
    assert acc['must_change_password'] is True and acc['account_setup_complete'] is False
    # Still required: the faculty number, names and employment fields.
    r = client.post(url, json=dict(_new(env, IDS[1]), first_name=''))
    assert r.status_code == 400 and 'required' in r.get_json()['error']


@pytest.mark.parametrize('who, url', [('acad', '/api/add_employee'), ('admin', '/admin/api/add_employee')])
def test_optional_values_must_be_valid_when_given(env, who, url):
    client = getattr(env, who)
    r = client.post(url, json=_new(env, IDS[0], email='not-an-email'))
    assert r.status_code == 400 and 'valid email' in r.get_json()['error']
    r = client.post(url, json=_new(env, IDS[0], contact='0917-555'))
    assert r.status_code == 400 and 'numbers only' in r.get_json()['error']
    assert not env.q("SELECT 1 FROM faculty WHERE employeenumber = %s", (IDS[0],))
    r = client.post(url, json=_new(env, IDS[0], email='opt.contact@example.com', contact='09175550901'))
    assert r.status_code == 200
    assert (_account(env, IDS[0])['email'], _account(env, IDS[0])['contactnumber']) == \
        ('opt.contact@example.com', '09175550901')


@pytest.mark.parametrize('who, add, edit', [('acad', '/api/add_employee', '/api/edit_employee'),
                                            ('admin', '/admin/api/add_employee', '/admin/api/edit_employee')])
def test_edit_keeps_email_and_contact_optional(env, who, add, edit):
    client = getattr(env, who)
    client.post(add, json=_new(env, IDS[2], email='opt.edit@example.com', contact='09175550902'))
    r = client.post(edit, json=dict(_new(env, IDS[2]), first_name='Edited', email='', contact=''))
    assert r.status_code == 200, r.get_json()
    assert env.q("SELECT firstname FROM faculty WHERE employeenumber = %s", (IDS[2],))[0]['firstname'] == 'Edited'
    assert (_account(env, IDS[2])['email'], _account(env, IDS[2])['contactnumber']) == (None, None)


@pytest.mark.parametrize('who, url', [('acad', '/faculty/import/doc/confirm'),
                                      ('admin', '/admin/faculty/import/doc/confirm')])
def test_import_without_email_or_contact(env, who, url):
    client = getattr(env, who)
    rows = [{'emp_num': IDS[3], 'last_name': 'Imported', 'first_name': 'Opt', 'middle_name': '',
             'email': '', 'contact': '', 'specialization': env.spec_name, 'emp_type': 'Regular', 'status': 'Permanent',
             'designation': ''}]
    r = client.post(url, data={'employees_data': json.dumps(rows)})
    assert r.status_code == 302
    with client.session_transaction() as sess:
        flashes = [m for _, m in sess.get('_flashes', [])]
    assert any('Import successful' in m for m in flashes), flashes
    assert env.q("SELECT lastname FROM faculty WHERE employeenumber = %s", (IDS[3],))[0]['lastname'] == 'Imported'
    acc = _account(env, IDS[3])
    assert acc and acc['email'] is None and acc['contactnumber'] is None


@pytest.mark.parametrize('who, page', [('acad', '/employee'), ('admin', '/admin/employee')])
def test_add_form_has_no_email_or_contact_and_edit_makes_them_optional(env, who, page):
    html = getattr(env, who).get(page).data.decode()
    add_form = html.split('id="addEmployeeForm"')[1].split('</form>')[0]
    assert 'name="email"' not in add_form and 'name="contact"' not in add_form
    assert 'name="employee_number"' in add_form and 'name="first_name"' in add_form
    edit_email = html.split('id="edit_email"')[1].split('>')[0]
    edit_contact = html.split('id="edit_contact"')[1].split('>')[0]
    assert 'required' not in edit_email and 'required' not in edit_contact
    assert 'EMAIL ADDRESS (OPTIONAL)' in html and 'CONTACT NUMBER (OPTIONAL)' in html
    assert 'Email (optional), Contact Number (optional)' in html             # import help text
