"""Profile Settings > Email: the new address must be verified with a code sent to it.

Runs email_change.py against the real database inside one rolled-back transaction;
the outgoing email is captured instead of sent.
"""
import re
import types

import pytest
from psycopg2.extras import RealDictCursor

import email_change
from tests.conftest import requires_db
from tests._shared_tx import SharedTx

pytestmark = requires_db

_CFG = types.SimpleNamespace()


@pytest.fixture
def tx(monkeypatch):
    t = SharedTx()
    sent = []
    monkeypatch.setattr(email_change, 'smtp_configured', lambda cfg: True)
    monkeypatch.setattr(email_change.mailer, 'send_email',
                        lambda cfg, to, subject, text, html=None: sent.append((to, text)))
    conn = t.handle()
    cur = conn.cursor()
    for sql in email_change.ENSURE_SCHEMA_SQL:
        cur.execute(sql)
    conn.commit()
    try:
        yield t, sent
    finally:
        t.close()


def _faculty_account(conn):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT a.username, a.employeenumber FROM accounts a
                   JOIN faculty f ON f.employeenumber = a.employeenumber
                   ORDER BY a.userid LIMIT 1""")
    row = cur.fetchone()
    if not row:
        pytest.skip('no faculty-linked account in this database')
    return row


def _code(sent):
    return re.search(r'code is: (\d{6})', sent[-1][1]).group(1)


def _faculty_email(conn, emp):
    cur = conn.cursor()
    cur.execute("SELECT email FROM faculty WHERE employeenumber = %s", [emp])
    return cur.fetchone()[0]


def test_code_goes_to_new_address_and_saves_it(tx):
    t, sent = tx
    conn = t.handle()
    acc = _faculty_account(conn)
    new = 'verify.test.unique.addr@example.com'
    email_change.request_code(conn, acc['username'], new, _CFG)
    assert sent[-1][0] == new
    assert email_change.confirm(conn, acc['username'], new, _code(sent)) == new
    assert _faculty_email(conn, acc['employeenumber']) == new


def test_wrong_code_does_not_save_and_counts_attempts(tx):
    t, sent = tx
    conn = t.handle()
    acc = _faculty_account(conn)
    before = _faculty_email(conn, acc['employeenumber'])
    new = 'verify.test.wrong.code@example.com'
    email_change.request_code(conn, acc['username'], new, _CFG)
    wrong = '000000' if _code(sent) != '000000' else '111111'
    with pytest.raises(email_change.EmailChangeError) as e:
        email_change.confirm(conn, acc['username'], new, wrong)
    assert e.value.error == 'invalid_code'
    assert _faculty_email(conn, acc['employeenumber']) == before


def test_code_only_works_for_the_address_it_was_sent_to(tx):
    t, sent = tx
    conn = t.handle()
    acc = _faculty_account(conn)
    email_change.request_code(conn, acc['username'], 'verify.test.first@example.com', _CFG)
    with pytest.raises(email_change.EmailChangeError) as e:
        email_change.confirm(conn, acc['username'], 'verify.test.other@example.com', _code(sent))
    assert e.value.error == 'email_mismatch'


def test_address_used_by_another_faculty_is_refused(tx):
    t, sent = tx
    conn = t.handle()
    acc = _faculty_account(conn)
    cur = conn.cursor()
    cur.execute("""SELECT email FROM faculty WHERE employeenumber <> %s
                   AND email IS NOT NULL AND TRIM(email) <> '' LIMIT 1""", [acc['employeenumber']])
    row = cur.fetchone()
    if not row:
        pytest.skip('no other faculty email to collide with')
    with pytest.raises(email_change.EmailChangeError) as e:
        email_change.request_code(conn, acc['username'], row[0].upper(), _CFG)
    assert e.value.error == 'email_taken'
    assert not sent


def test_resend_is_rate_limited(tx):
    t, sent = tx
    conn = t.handle()
    acc = _faculty_account(conn)
    email_change.request_code(conn, acc['username'], 'verify.test.resend@example.com', _CFG)
    with pytest.raises(email_change.EmailChangeError) as e:
        email_change.request_code(conn, acc['username'], 'verify.test.resend@example.com', _CFG)
    assert e.value.error == 'too_soon'
    assert len(sent) == 1
