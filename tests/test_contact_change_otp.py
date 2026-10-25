"""Profile Settings > Contact Number: saved only after entering a 6-digit code emailed to
the account's REGISTERED address. Account Setup's Step 4 registers the number WITHOUT a
code (format + not another account's), kept with the setup until Save and Confirm.

Runs contact_change.py and the real Flask routes against the real database inside one
rolled-back transaction; outgoing email is captured instead of sent.
"""
import re
import types

import pytest
from psycopg2.extras import RealDictCursor

import contact_change
import email_change
from tests.conftest import requires_db, DB_AVAILABLE

if DB_AVAILABLE:
    # Run app.py's startup migrations now, before a fixture below holds a lock on accounts
    # (they give up after lock_timeout, which would leave other test modules without the columns).
    import app  # noqa: F401
from tests._shared_tx import SharedTx, install

pytestmark = requires_db

_CFG = types.SimpleNamespace()
NEW = '09175550142'            # stored (normalized) form
NEW_TYPED = '+63 917 555 0142'


@pytest.fixture
def tx(monkeypatch):
    t = SharedTx()
    sent = []
    monkeypatch.setattr(contact_change, 'smtp_configured', lambda cfg: True)
    monkeypatch.setattr(contact_change.mailer, 'send_email',
                        lambda cfg, to, subject, text, html=None: sent.append((to, subject, text)))
    conn = t.handle()
    cur = conn.cursor()
    for sql in email_change.ENSURE_SCHEMA_SQL + contact_change.ENSURE_SCHEMA_SQL:
        cur.execute(sql)
    conn.commit()
    try:
        yield t, sent
    finally:
        t.close()


def _account(conn, email='contact.otp.owner@example.com'):
    """A faculty-linked account with a known registered email and no clash on NEW."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT a.userid, a.username FROM accounts a
                   JOIN faculty f ON f.employeenumber = a.employeenumber
                   ORDER BY a.userid LIMIT 1""")
    row = cur.fetchone()
    if not row:
        pytest.skip('no faculty-linked account in this database')
    cur.execute("UPDATE accounts SET contactnumber = NULL WHERE BTRIM(contactnumber) = %s", [NEW])
    cur.execute("""UPDATE accounts SET email = %s, email_verified_addr = NULL, contactnumber = '09175550100',
                          contact_verified = NULL,
                          contact_otp_hash = NULL, contact_otp_new = NULL, contact_otp_sent_at = NULL,
                          contact_otp_expires_at = NULL, contact_otp_attempts = 0,
                          contact_otp_window_start = NULL, contact_otp_window_sends = 0
                   WHERE userid = %s""", [email, row['userid']])
    conn.commit()
    return row['username']


def _row(conn, username):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM accounts WHERE username = %s", [username])
    return cur.fetchone()


def _code(sent):
    return re.search(r'code is: (\d{6})', sent[-1][2]).group(1)


def _wrong(sent):
    return '000000' if _code(sent) != '000000' else '111111'


def _err(fn, *args):
    with pytest.raises(contact_change.ContactChangeError) as e:
        fn(*args)
    return e.value


def _allow_resend(conn, username):
    cur = conn.cursor()
    cur.execute("UPDATE accounts SET contact_otp_sent_at = NOW() - INTERVAL '2 minutes' WHERE username = %s",
                [username])
    conn.commit()


# ---- module ------------------------------------------------------------------------

def test_code_goes_to_registered_email_and_number_saves_only_after_it(tx):
    t, sent = tx
    conn = t.handle()
    user = _account(conn)
    out = contact_change.request_code(conn, user, NEW, _CFG)
    assert sent[-1][0] == 'contact.otp.owner@example.com'
    assert out['expires_in_minutes'] == 5 and out['resend_after'] == 60
    row = _row(conn, user)
    assert row['contactnumber'] == '09175550100'            # not saved before verification
    assert row['contact_otp_new'] == NEW
    assert _code(sent) not in (row['contact_otp_hash'] or '')   # only a hash is stored
    saved, email = contact_change.confirm(conn, user, NEW, _code(sent))
    assert (saved, email) == (NEW, 'contact.otp.owner@example.com')
    row = _row(conn, user)
    assert row['contactnumber'] == NEW and row['contact_otp_hash'] is None
    # Reading that inbox proved the email, so it now counts as verified.
    assert email_change.is_verified(row['email'], row['email_verified_addr'])


def test_code_is_single_use(tx):
    t, sent = tx
    conn = t.handle()
    user = _account(conn)
    contact_change.request_code(conn, user, NEW, _CFG)
    code = _code(sent)
    contact_change.confirm(conn, user, NEW, code)
    assert _err(contact_change.confirm, conn, user, NEW, code).error == 'no_code'


def test_wrong_codes_are_counted_and_the_fifth_ends_the_challenge(tx):
    t, sent = tx
    conn = t.handle()
    user = _account(conn)
    contact_change.request_code(conn, user, NEW, _CFG)
    right, wrong = _code(sent), _wrong(sent)
    for left in (4, 3, 2, 1):
        e = _err(contact_change.confirm, conn, user, NEW, wrong)
        assert e.error == 'invalid_code' and e.extra['attempts_left'] == left
    assert _err(contact_change.confirm, conn, user, NEW, wrong).error == 'too_many_attempts'
    # The correct code no longer works either: a new code must be requested.
    assert _err(contact_change.confirm, conn, user, NEW, right).error == 'no_code'
    assert _row(conn, user)['contactnumber'] == '09175550100'


def test_expired_code_is_rejected_and_cleared(tx):
    t, sent = tx
    conn = t.handle()
    user = _account(conn)
    contact_change.request_code(conn, user, NEW, _CFG)
    cur = conn.cursor()
    cur.execute("UPDATE accounts SET contact_otp_expires_at = NOW() - INTERVAL '1 second' WHERE username = %s",
                [user])
    conn.commit()
    assert _err(contact_change.confirm, conn, user, NEW, _code(sent)).error == 'code_expired'
    assert _row(conn, user)['contact_otp_hash'] is None
    assert _row(conn, user)['contactnumber'] == '09175550100'


def test_code_is_bound_to_the_number_it_was_sent_for(tx):
    t, sent = tx
    conn = t.handle()
    user = _account(conn)
    contact_change.request_code(conn, user, NEW, _CFG)
    e = _err(contact_change.confirm, conn, user, '09175550143', _code(sent))
    assert e.error == 'contact_mismatch'
    assert _row(conn, user)['contactnumber'] == '09175550100'


def test_resend_cooldown_and_hourly_limit(tx):
    t, sent = tx
    conn = t.handle()
    user = _account(conn)
    contact_change.request_code(conn, user, NEW, _CFG)
    e = _err(contact_change.request_code, conn, user, NEW, _CFG)
    assert e.status == 429 and e.error == 'too_soon' and 0 < e.extra['retry_after'] <= 60
    for _ in range(4):                              # 5 sends in this window so far
        _allow_resend(conn, user)
        contact_change.request_code(conn, user, NEW, _CFG)
    _allow_resend(conn, user)
    e = _err(contact_change.request_code, conn, user, NEW, _CFG)
    assert e.status == 429 and e.error == 'rate_limited'
    assert len(sent) == 5
    # A new window starts an hour after the first send.
    cur = conn.cursor()
    cur.execute("UPDATE accounts SET contact_otp_window_start = NOW() - INTERVAL '61 minutes' WHERE username = %s",
                [user])
    conn.commit()
    contact_change.request_code(conn, user, NEW, _CFG)
    assert _row(conn, user)['contact_otp_window_sends'] == 1


def test_duplicate_same_and_no_email_are_refused(tx):
    t, sent = tx
    conn = t.handle()
    user = _account(conn)
    cur = conn.cursor()
    cur.execute("""UPDATE accounts SET contactnumber = %s
                   WHERE userid = (SELECT userid FROM accounts WHERE username <> %s ORDER BY userid LIMIT 1)""",
                [NEW, user])
    conn.commit()
    assert _err(contact_change.request_code, conn, user, NEW, _CFG).error == 'contact_taken'
    # The saved number is "the same" only once the user verified it (an Admin-entered one can be verified).
    cur.execute("UPDATE accounts SET contact_verified = contactnumber WHERE username = %s", [user])
    conn.commit()
    assert _err(contact_change.request_code, conn, user, '09175550100', _CFG).error == 'same_contact'
    cur.execute("UPDATE accounts SET email = NULL WHERE username = %s", [user])
    conn.commit()
    e = _err(contact_change.request_code, conn, user, '09175550144', _CFG)
    assert e.error == 'no_email' and 'Administrator' in e.message
    assert sent == []


def test_number_taken_after_the_code_was_sent_is_not_saved(tx):
    t, sent = tx
    conn = t.handle()
    user = _account(conn)
    contact_change.request_code(conn, user, NEW, _CFG)
    cur = conn.cursor()
    cur.execute("""UPDATE accounts SET contactnumber = %s
                   WHERE userid = (SELECT userid FROM accounts WHERE username <> %s ORDER BY userid LIMIT 1)""",
                [NEW, user])
    conn.commit()
    assert _err(contact_change.confirm, conn, user, NEW, _code(sent)).error == 'contact_taken'
    assert _row(conn, user)['contactnumber'] == '09175550100'


# ---- routes ------------------------------------------------------------------------

@pytest.fixture
def client(tx, monkeypatch):
    import app as app_module
    t, sent = tx
    install(monkeypatch, t)
    user = _account(t.handle())
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, username=user, role='Faculty',
                 must_change_password=False, account_setup_complete=True)
    return c, user, t, sent


def test_routes_change_the_number_and_send_a_notice(client):
    c, user, t, sent = client
    assert c.get('/api/user/contact/status').get_json()['has_email'] is True
    r = c.post('/api/user/contact/request-code', data={'contact_number': NEW_TYPED})
    assert r.status_code == 200 and r.get_json()['email_masked'].endswith('@example.com')
    r = c.post('/api/user/contact/update', data={'contact_number': NEW_TYPED, 'code': _code(sent)})
    assert r.status_code == 200 and r.get_json()['contact_number'] == NEW
    assert _row(t.handle(), user)['contactnumber'] == NEW
    to, subject, text = sent[-1]
    assert to == 'contact.otp.owner@example.com' and 'was changed' in subject and NEW not in text


def test_routes_validate_the_number_on_the_server(client):
    c, user, t, sent = client
    for bad in ('+63 12345', '0812 345 6789', '12', 'abc'):
        r = c.post('/api/user/contact/request-code', data={'contact_number': bad})
        assert r.status_code == 400 and r.get_json()['error'] == 'invalid_contact', bad
    assert sent == []


def test_route_wrong_code_does_not_save(client):
    c, user, t, sent = client
    c.post('/api/user/contact/request-code', data={'contact_number': NEW_TYPED})
    r = c.post('/api/user/contact/update', data={'contact_number': NEW_TYPED, 'code': _wrong(sent)})
    assert r.status_code == 400 and r.get_json()['error'] == 'invalid_code'
    assert _row(t.handle(), user)['contactnumber'] == '09175550100'


def test_routes_need_a_signed_in_account(client):
    import app as app_module
    anon = app_module.app.test_client()
    assert anon.post('/api/user/contact/request-code', data={'contact_number': NEW}).status_code == 401
    assert anon.get('/api/user/contact/status').status_code == 401


# ---- Account Setup Step 4 -----------------------------------------------------------

@pytest.fixture
def setup_client(client):
    c, user, t, sent = client
    cur = t.handle().cursor()
    cur.execute("DELETE FROM account_setup_draft WHERE username = %s", [user])
    # The database matches the session: password already changed, setup not finished.
    cur.execute("UPDATE accounts SET must_change_password = FALSE, account_setup_complete = FALSE "
                "WHERE username = %s", [user])
    with c.session_transaction() as s:
        s['account_setup_complete'] = False
    return c, user, t, sent


def _continue(c, number):
    return c.post('/account-setup/contact', data={'action': 'continue', 'contact_number': number})


def _draft_contact(t, user):
    cur = t.handle().cursor()
    cur.execute("SELECT d.contactnumber FROM account_setup_draft d JOIN accounts a ON a.userid = d.userid "
                "WHERE a.username = %s", [user])
    row = cur.fetchone()
    return row[0] if row else None


def test_setup_step4_has_no_verification_code(setup_client):
    c, user, t, sent = setup_client
    page = c.get('/account-setup/contact').data.decode()
    assert 'Set Up Your Contact Number' in page and 'id="setupContactLocal"' in page
    for gone in ('Verification Code', 'Send Verification Code', 'otpSection', 'btnSendCode', 'Set up your email first'):
        assert gone not in page, gone
    assert '9175550100' not in page                       # the Admin-entered number is not shown


def test_setup_number_is_kept_without_a_code_and_saved_on_confirm(setup_client):
    c, user, t, sent = setup_client                     # (no verified email needed)
    r = _continue(c, NEW_TYPED)
    assert r.headers['Location'].endswith('/account-setup/verify')
    assert sent == []                                     # no code is emailed
    assert _draft_contact(t, user) == NEW
    assert _row(t.handle(), user)['contactnumber'] == '09175550100'     # nothing saved yet
    assert NEW in c.get('/account-setup/verify').data.decode()          # shown on Review
    assert NEW_TYPED in c.get('/account-setup/contact').data.decode()   # Back / Edit shows it
    r = c.post('/account-setup/verify', data={'action': 'confirm'})
    assert r.headers['Location'].endswith('/account-setup/done')
    row = _row(t.handle(), user)
    assert row['contactnumber'] == NEW and row['contact_verified'] == NEW


def test_setup_number_is_checked_on_the_server(setup_client):
    c, user, t, sent = setup_client
    for bad, text in (('+63 12345', 'valid Philippine mobile number'),
                      ('0812 345 6789', 'valid Philippine mobile number'), ('', 'Please enter a contact number')):
        r = _continue(c, bad)
        assert r.headers['Location'].endswith('/account-setup/contact'), bad
        assert text in c.get(r.headers['Location']).data.decode(), bad
    cur = t.handle().cursor()
    cur.execute("""UPDATE accounts SET contactnumber = %s
                   WHERE userid = (SELECT userid FROM accounts WHERE username <> %s ORDER BY userid LIMIT 1)""",
                [NEW, user])
    r = _continue(c, NEW_TYPED)
    assert 'already used by another account' in c.get(r.headers['Location']).data.decode()
    assert _draft_contact(t, user) is None
    # The Admin-entered number on this account is fine to keep.
    assert _continue(c, '09175550100').headers['Location'].endswith('/account-setup/verify')
    # Skip keeps nothing.
    assert c.post('/account-setup/contact', data={'action': 'skip'}).headers['Location'].endswith('/account-setup/verify')


def test_contact_codes_are_not_used_during_setup(setup_client):
    c, user, t, sent = setup_client
    assert c.post('/api/user/contact/request-code', data={'contact_number': NEW_TYPED}).status_code == 403
    assert c.post('/api/user/contact/update', data={'contact_number': NEW_TYPED, 'code': '123456'}).status_code == 403
    assert sent == [] and _row(t.handle(), user)['contactnumber'] == '09175550100'


def test_profile_settings_still_works_without_a_step_3_verification(client):
    # Account Settings sends to the registered email even if it was never verified with a
    # code: entering the code proves that inbox (and then marks it verified).
    c, user, t, sent = client
    assert c.get('/api/user/contact/status').get_json()['can_send'] is True
    assert c.post('/api/user/contact/request-code', data={'contact_number': NEW_TYPED}).status_code == 200
