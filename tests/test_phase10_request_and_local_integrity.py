"""Phase 10: additive integrity migration (migrations/2026-10-01_request_cancel_and_local_integrity.sql).

DB-backed tests run inside one rolled-back transaction (tests/_shared_tx.py).
"""
import datetime

import psycopg2
import pytest

import app as app_module
from conftest import requires_db
from test_local_override_snapshot_repair import tx, ls, _cur  # noqa: F401  (fixtures)


def _faculty_client(tx, fac):
    cur = _cur(tx)
    cur.execute("SELECT username FROM accounts WHERE isactive ORDER BY userid LIMIT 1")
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['loggedin'] = True; s['role'] = 'Faculty'
        s['username'] = cur.fetchone()['username']; s['employeenumber'] = fac
    return c


def _pending_adjustment(tx, o):
    cur = _cur(tx)
    cur.execute("""
        INSERT INTO schedule_change_request (scheduleid, versionid, change_type, new_daydesc,
            effective_from, reason, submitted_by, status, official_sessionid)
        VALUES (%s, %s, 'Day', 'Saturday', %s, 'test', %s, 'Pending', %s) RETURNING requestid
    """, (o['scheduleid'], o['versionid'], datetime.date.today(), o['fac'], o['sessionid']))
    rid = cur.fetchone()['requestid']
    tx.handle().commit()
    return rid


def _pending_makeup(tx, o):
    cur = _cur(tx)
    cur.execute("""
        INSERT INTO class_meeting_request (scheduleid, requested_date, new_starttimeid, new_endtimeid,
            reason, submitted_by, status)
        VALUES (%s, %s, %s, %s, 'test', %s, 'Pending') RETURNING requestid
    """, (o['scheduleid'], datetime.date.today() + datetime.timedelta(days=7),
          o['starttimeid'], o['endtimeid'], o['fac']))
    rid = cur.fetchone()['requestid']
    tx.handle().commit()
    return rid


def _status(tx, table, rid):
    cur = _cur(tx)
    cur.execute(f"SELECT status, reviewed_by FROM {table} WHERE requestid = %s", (rid,))
    return dict(cur.fetchone())


@requires_db
@pytest.mark.parametrize('kind, table, make', [
    ('adjustment', 'schedule_change_request', _pending_adjustment),
    ('makeup', 'class_meeting_request', _pending_makeup)])
def test_faculty_can_cancel_their_pending_request(tx, ls, kind, table, make):
    o = ls['occ'][0]
    rid = make(tx, o)
    r = _faculty_client(tx, o['fac']).delete(f'/api/faculty/my_requests/{kind}/{rid}')
    assert r.status_code == 200 and r.get_json()['success'], r.get_json()
    assert _status(tx, table, rid) == {'status': 'Cancelled', 'reviewed_by': None}


@requires_db
@pytest.mark.parametrize('table', ['schedule_change_request', 'class_meeting_request'])
def test_request_status_constraints_still_reject_invalid_states(tx, ls, table):
    o = ls['occ'][0]
    rid = (_pending_adjustment if table == 'schedule_change_request' else _pending_makeup)(tx, o)
    cur = _cur(tx)
    for sql in (f"UPDATE {table} SET status = 'Bogus' WHERE requestid = %s",
                # Approved needs a reviewer; Cancelled must not have one.
                f"UPDATE {table} SET status = 'Approved' WHERE requestid = %s",
                f"UPDATE {table} SET status = 'Cancelled', reviewed_by = submitted_by, "
                f"reviewed_at = NOW() WHERE requestid = %s"):
        cur.execute("SAVEPOINT chk")
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(sql, (rid,))
        cur.execute("ROLLBACK TO SAVEPOINT chk")


@requires_db
def test_adjustment_request_occurrence_must_exist(tx, ls):
    o = dict(ls['occ'][0], sessionid=-1)
    cur = _cur(tx)
    cur.execute("SAVEPOINT fk")
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cur.execute("""
            INSERT INTO schedule_change_request (scheduleid, versionid, change_type, effective_from,
                reason, submitted_by, status, official_sessionid)
            VALUES (%s, %s, 'Day', CURRENT_DATE, 'x', %s, 'Pending', %s)
        """, (o['scheduleid'], o['versionid'], o['fac'], o['sessionid']))
    cur.execute("ROLLBACK TO SAVEPOINT fk")


@requires_db
@pytest.mark.parametrize('sql, err', [
    ("INSERT INTO local_arrangement (programcode, yearlevel, sectionid, semesterid, is_active, status) "
     "VALUES ('X', 1, NULL, %(sem)s, FALSE, 'Approved')", psycopg2.errors.CheckViolation),
    ("INSERT INTO local_arrangement (programcode, yearlevel, sectionid, semesterid, is_active, status) "
     "VALUES ('X', 1, NULL, -1, FALSE, 'Draft')", psycopg2.errors.ForeignKeyViolation),
])
def test_local_arrangement_status_and_semester_are_enforced(tx, ls, sql, err):
    cur = _cur(tx)
    cur.execute("SAVEPOINT loc")
    with pytest.raises(err):
        cur.execute(sql, {'sem': ls['semesterid']})
    cur.execute("ROLLBACK TO SAVEPOINT loc")


def test_migration_runs_from_the_reviewed_sql_file():
    import inspect
    src = inspect.getsource(app_module._run_startup_migrations)
    assert '2026-10-01_request_cancel_and_local_integrity.sql' in src
