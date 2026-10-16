"""HC8 (designee PT/Night Teaching Service) is blocking for Save as Draft, and a
Re-generate never keeps a kept row's night time that breaks the allowance.

Real database, ONE transaction always rolled back (tests/_shared_tx.py): the
designation's allowance is set to 0 only inside it.
"""
from datetime import time

import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
import merge_groups as mg
from conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def tx(monkeypatch):
    from _shared_tx import SharedTx, install
    t = SharedTx()
    install(monkeypatch, t)
    try:
        yield t
    finally:
        t.close()


def _cur(tx):
    return tx.raw.cursor(cursor_factory=RealDictCursor)


@pytest.fixture
def world(tx, monkeypatch):
    """A designee whose designation allows 0 nights (inside the transaction), and a
    lecture subject of an active section in the active semester."""
    cur = _cur(tx)
    cur.execute("SELECT semesterid, academicyearid, semestertype FROM semester WHERE isactive "
                "ORDER BY semesterid LIMIT 1")
    sem = cur.fetchone()
    if not sem:
        pytest.skip('no active semester')
    cur.execute("SELECT employeenumber, designationid FROM faculty WHERE designationid IS NOT NULL "
                "AND employeestatus != 'Archive' ORDER BY employeenumber LIMIT 1")
    fac = cur.fetchone()
    if not fac:
        pytest.skip('no designee faculty')
    cur.execute("UPDATE designation SET nightteachingservice = 0 WHERE designationid = %s",
                (fac['designationid'],))
    monkeypatch.setattr(app_module, '_faculty_map_cache', {})
    sections, subjects = mg.load_offered(cur, sem['semesterid'])
    for sid, sec in sections.items():
        if not sec['isactive']:
            continue
        for csid in sec['offered']:
            s = subjects[csid]
            if list(mg.required_parts(s)) == ['Lecture']:
                cur.execute("SELECT roomid FROM room WHERE roomtype = 'Lecture' ORDER BY roomid LIMIT 1")
                room = cur.fetchone()
                return {'sem': sem, 'fac': fac['employeenumber'], 'sid': sid, 'sec': sec,
                        'subject': s, 'room': room['roomid']}
    pytest.skip('no lecture subject offered')


@pytest.fixture
def head(tx):
    cur = _cur(tx)
    cur.execute("SELECT username, employeenumber FROM accounts WHERE isactive AND role = 'Academic Head' "
                "ORDER BY userid LIMIT 1")
    acct = cur.fetchone()
    if not acct:
        pytest.skip('no active Academic Head account')
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username=acct['username'],
                 employeenumber=acct['employeenumber'])
    return c


def _row(w, start, end, day='Friday'):
    code = w['subject']['subjectcode']
    return {'subject_code': code, 'subjectcode': code, 'class_type': 'Lecture',
            'day': day, 'days_list': [day], 'start_time': start, 'end_time': end,
            'room_id': w['room'], 'faculty_id': w['fac'],
            'lec_hours': w['subject']['lecturehours'], 'units': w['subject']['creditunits']}


def _context(w):
    return {'program': w['sec']['programcode'], 'yearLevel': w['sec']['yearlevel'],
            'term': w['sem']['semestertype'], 'acadYear': w['sem']['academicyearid'],
            'section_id': w['sid']}


def _drafts(tx, w):
    cur = _cur(tx)
    cur.execute("SELECT COUNT(*) AS n FROM schedule_version sv JOIN schedule s ON s.scheduleid = sv.scheduleid "
                "WHERE s.sectionid = %s AND s.semesterid = %s AND sv.status = 'Draft'",
                (w['sid'], w['sem']['semesterid']))
    return cur.fetchone()['n']


def test_save_draft_blocks_a_night_class_the_designation_does_not_allow(tx, world, head):
    before = _drafts(tx, world)
    res = head.post('/api/schedule/save-draft', json={
        'context': _context(world), 'schedule_data': [_row(world, '18:00', '21:00')]})
    data = res.get_json()
    assert res.status_code == 400 and data['success'] is False
    assert data['code'] == 'HC8_NIGHT_LIMIT'
    assert 'does not allow PT/Night Teaching Service' in data['error']
    assert _drafts(tx, world) == before


def test_regenerate_releases_a_kept_night_time_over_the_allowance(tx, world):
    w = world
    ls = dict(_row(w, time(18, 0), time(21, 0)), course=w['sec']['programcode'], selected=False,
              lock={'faculty': True, 'room': True, 'schedule': True},
              user_lock={'faculty': False, 'room': False, 'schedule': False})
    released = app_module._release_night_cap_locks(
        [ls], w['sec']['programcode'], w['sec']['yearlevel'], w['sem']['semestertype'],
        w['sem']['academicyearid'], w['sid'])
    assert released == [w['subject']['subjectcode'].strip().upper()]
    assert ls['lock'] == {'faculty': True, 'room': True, 'schedule': False}
    assert ls['selected'] is True


def test_regenerate_keeps_an_explicit_time_lock_and_daytime_rows(tx, world):
    w = world
    locked = dict(_row(w, time(18, 0), time(21, 0)), selected=True,
                  lock={'faculty': True, 'room': True, 'schedule': True},
                  user_lock={'faculty': False, 'room': False, 'schedule': True})
    day = dict(_row(w, time(9, 0), time(12, 0), day='Monday'), selected=False,
               lock={'faculty': True, 'room': True, 'schedule': True}, user_lock={})
    assert app_module._release_night_cap_locks(
        [locked, day], w['sec']['programcode'], w['sec']['yearlevel'], w['sem']['semestertype'],
        w['sem']['academicyearid'], w['sid']) == []
    assert locked['lock']['schedule'] is True and day['lock']['schedule'] is True


def test_save_draft_blocks_a_faculty_over_their_load_limit(tx, world, head):
    """HC9 blocks Save as Draft exactly like Approve: one faculty given more weekly
    hours than their cap is refused and nothing is written."""
    fac = app_module._load_faculty_map(force_refresh=True)[world['fac']]
    et = fac['employeetype']
    cap = (et.get('regularload') or 0) + (et.get('parttimeload') or 0) + (et.get('teachingsubstitution') or 0)
    rows, n = [], 0
    for day in ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'):
        for s, e in (('07:30', '10:30'), ('10:30', '13:30'), ('13:30', '16:30')):
            if n * 3 > cap:
                break
            rows.append(dict(_row(world, s, e, day=day), subject_code=f'ZZLOAD {n}', subjectcode=f'ZZLOAD {n}'))
            n += 1
    if n * 3 <= cap:
        pytest.skip(f'cap {cap} hrs too large to exceed in one week')
    before = _drafts(tx, world)
    res = head.post('/api/schedule/save-draft', json={'context': _context(world), 'schedule_data': rows})
    data = res.get_json()
    assert res.status_code == 400 and data['success'] is False, data
    assert data['code'] == 'HC9_FACULTY_LOAD'
    assert _drafts(tx, world) == before
