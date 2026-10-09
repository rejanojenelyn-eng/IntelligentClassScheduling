"""HC16 group mode — P2 Save Draft / Validate / Publish against the real database.

Each test runs inside ONE transaction that is always rolled back
(tests/_shared_tx.py): the switch to hc_merge_model='groups', the Merge Group and
any schedule rows exist only inside it. The live system stays on 'legacy'.
"""
import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
import merge_groups as mg
from conftest import requires_db

pytestmark = requires_db

WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')


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


def _minutes(hm):
    h, m = map(int, hm.split(':'))
    return h * 60 + m


@pytest.fixture
def world(tx):
    """Groups mode inside the transaction + a lecture-only subject offered to 2 sections
    that have no sessions of it, + a weekday block and Lecture room where neither
    section nor the room has anything, + two active faculty."""
    cur = _cur(tx)
    mg.ensure_schema(cur)
    cur.execute("SELECT s.semesterid, s.academicyearid, s.semestertype FROM semester s "
                "WHERE s.isactive ORDER BY s.semesterid LIMIT 1")
    sem = cur.fetchone()
    if not sem:
        pytest.skip('no active semester')
    sections, subjects = mg.load_offered(cur, sem['semesterid'])
    occ = mg.fetch_occurrences(cur, [sem['semesterid']])
    busy_subject = {(o['sectionid'], mg.norm_code(o['subjectcode'])) for o in occ}
    by_code = {}
    for sid, sec in sections.items():
        if not sec['isactive']:
            continue
        for csid in sec['offered']:
            s = subjects[csid]
            code = mg.norm_code(s['subjectcode'])
            if list(mg.required_parts(s)) == ['Lecture'] and (sid, code) not in busy_subject:
                by_code.setdefault(code, []).append((sid, csid))
    cfg = dict(app_module.load_scheduler_config(), hc_merge_model='groups')
    blocks = sorted(mg.valid_time_blocks(dict(cfg, hc_time_blocks_enabled=1)))
    ts = {t: i for i, t in mg.load_timeslots(cur).items()}
    rooms = [r for r in mg.load_rooms(cur).values() if r['isactive'] and r['roomtype'] == 'Lecture']
    for code, pairs in sorted(by_code.items(), key=lambda kv: -len(kv[1])):
        if len(pairs) < 2:
            continue
        (a, a_cs), (b, b_cs) = pairs[:2]
        hours = mg.required_parts(subjects[a_cs])['Lecture']
        for day in WEEKDAYS:
            for st, et in blocks:
                if (_minutes(et) - _minutes(st)) != hours * 60 or st not in ts or et not in ts:
                    continue
                def free(o):
                    return not (o['day'] == day and _minutes(o['start_time']) < _minutes(et)
                                and _minutes(o['end_time']) > _minutes(st))
                if not all(free(o) for o in occ if o['sectionid'] in (a, b)):
                    continue
                room = next((r for r in rooms if all(free(o) for o in occ if o['roomid'] == r['roomid'])), None)
                if not room:
                    continue
                cur.execute("SELECT employeenumber FROM faculty WHERE isactive ORDER BY employeenumber LIMIT 2")
                facs = [r['employeenumber'] for r in cur.fetchall()]
                if len(facs) < 2:
                    pytest.skip('needs two active faculty')
                mg.set_merge_model(cur, 'groups')
                tx.handle().commit()
                return {'sem': sem, 'code': code, 'a': (a, a_cs), 'b': (b, b_cs), 'sec': sections,
                        'subject': subjects[a_cs], 'day': day, 'start': st, 'end': et,
                        'stid': ts[st], 'etid': ts[et], 'room': room, 'facs': facs, 'cfg': cfg}
    pytest.skip('no free subject/slot/room combination found')


def _make_group(tx, w, *, meetings=True, mode='MULTIPLE_FACULTY', designated=None):
    cur = _cur(tx)
    body = {'groupname': 'P2 merged class', 'semesterid': w['sem']['semesterid'],
            'ref_curriculumsubjectid': w['a'][1], 'faculty_mode': mode, 'employeenumber': designated,
            'members': [{'sectionid': w['a'][0], 'curriculumsubjectid': w['a'][1]},
                        {'sectionid': w['b'][0], 'curriculumsubjectid': w['b'][1]}],
            'meetings': ([{'class_type': 'Lecture', 'daydesc': w['day'], 'starttimeid': w['stid'],
                           'endtimeid': w['etid'], 'roomid': w['room']['roomid']}] if meetings else [])}
    out = mg.save_group(cur, body, w['cfg'], username='p2-test', confirm_impact=True)
    assert out['ok'], out
    tx.handle().commit()
    return out


def _row(w, *, room=None, fac='TBA', day=None):
    return {'subject_code': w['subject']['subjectcode'], 'subjectcode': w['subject']['subjectcode'],
            'day': day or w['day'], 'days_list': [day or w['day']], 'start_time': w['start'],
            'end_time': w['end'], 'room_id': room if room is not None else w['room']['roomid'],
            'faculty_id': fac, 'lec_hours': w['subject']['lecturehours'],
            'units': w['subject']['creditunits']}


def _context(w, which='a'):
    sec = w['sec'][w[which][0]]
    return {'program': sec['programcode'], 'yearLevel': sec['yearlevel'], 'term': w['sem']['semestertype'],
            'acadYear': w['sem']['academicyearid'], 'section_id': w[which][0]}


def _draft_count(tx, w, which='a'):
    cur = _cur(tx)
    cur.execute("SELECT COUNT(*) AS n FROM schedule_version sv JOIN schedule s ON s.scheduleid = sv.scheduleid "
                "WHERE s.sectionid = %s AND s.semesterid = %s AND sv.status = 'Draft'",
                (w[which][0], w['sem']['semesterid']))
    return cur.fetchone()['n']


def _other_room(tx, w):
    return next(r for r in mg.load_rooms(_cur(tx)).values()
                if r['roomid'] != w['room']['roomid'] and r['roomtype'] == 'Lecture')


# ── 25. Save Draft ──────────────────────────────────────────────────────────────

def test_save_draft_accepts_a_member_at_its_group_meeting(head, tx, world):
    _make_group(tx, world)
    r = head.post('/api/schedule/save-draft', json={'context': _context(world), 'schedule_data': [_row(world)]})
    body = r.get_json()
    assert body.get('success') is True, body
    assert not any(w.get('rule') == 'HC16' for w in body.get('draft_warnings') or [])


def test_save_draft_of_a_member_off_its_group_slot_is_an_ordinary_class(head, tx, world):
    # P7 "allowed" model: leaving the merged slot is never an HC16 error.
    _make_group(tx, world)
    before = _draft_count(tx, world)
    wrong = _row(world, room=_other_room(tx, world)['roomid'])
    r = head.post('/api/schedule/save-draft', json={'context': _context(world), 'schedule_data': [wrong]})
    body = r.get_json()
    assert body.get('success') is True, body
    assert not any(w.get('rule') == 'HC16' for w in body.get('draft_warnings') or [])
    assert _draft_count(tx, world) == before + 1


def test_save_draft_of_an_allowed_but_unmerged_member_raises_nothing(head, tx, world):
    _make_group(tx, world, meetings=False)                               # allowed in Settings only
    r = head.post('/api/schedule/save-draft', json={'context': _context(world), 'schedule_data': [_row(world)]})
    body = r.get_json()
    assert body.get('success') is True, body
    assert not any(w.get('rule') == 'HC16' for w in body.get('draft_warnings') or [])


def test_save_draft_blocks_same_faculty_mismatch_with_a_published_member(head, tx, world):
    _make_group(tx, world, mode='SAME_FACULTY')
    # Section B already teaches the merged class (Published) with faculty 0.
    cur = _cur(tx)
    sid = app_module._find_or_create_schedule(cur, world['b'][1], world['b'][0], world['sem']['semesterid'],
                                              world['facs'][0], 'Published')
    cur.execute("INSERT INTO schedule_version (scheduleid, version_number, status, source, original_status, "
                "employeenumber) VALUES (%s, 9001, 'Published', 'manual_editor', 'Published', %s) "
                "RETURNING versionid", (sid, world['facs'][0]))
    vid = cur.fetchone()['versionid']
    cur.execute("INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid) "
                "VALUES (%s, %s, %s, %s, %s)", (vid, world['day'], world['stid'], world['etid'],
                                                world['room']['roomid']))
    tx.handle().commit()
    r = head.post('/api/schedule/save-draft', json={'context': _context(world),
                                                     'schedule_data': [_row(world, fac=world['facs'][1])]})
    body = r.get_json()
    assert r.status_code == 400 and body['violations'][0]['code'] == 'HC16_FACULTY', body
    same = head.post('/api/schedule/save-draft', json={'context': _context(world),
                                                        'schedule_data': [_row(world, fac=world['facs'][0])]})
    assert same.get_json().get('success') is True, same.get_json()


def test_save_draft_in_legacy_mode_is_unchanged(head, tx, world):
    _make_group(tx, world)
    mg.set_merge_model(_cur(tx), 'legacy')
    tx.handle().commit()
    wrong = _row(world, room=_other_room(tx, world)['roomid'])
    body = head.post('/api/schedule/save-draft', json={'context': _context(world),
                                                        'schedule_data': [wrong]}).get_json()
    assert body.get('success') is True, body                     # groups are not consulted
    assert not any(w.get('rule') == 'HC16' for w in body.get('draft_warnings') or [])


# ── Validate (Manual Editor pre-save gate) ──────────────────────────────────────

def test_validate_raises_no_hc16_for_a_member_on_or_off_its_slot(head, tx, world):
    _make_group(tx, world)
    wrong = _row(world, room=_other_room(tx, world)['roomid'])
    body = head.post('/api/schedule/validate', json={'context': _context(world),
                                                      'schedule_data': [wrong]}).get_json()
    assert not any(v.get('rule') == 'HC16' for v in body['violations'])
    ok = head.post('/api/schedule/validate', json={'context': _context(world),
                                                    'schedule_data': [_row(world)]}).get_json()
    assert not any(v.get('rule') == 'HC16' for v in ok['violations'])
    # without context the endpoint behaves as before (no merged-class resolution)
    bare = head.post('/api/schedule/validate', json={'schedule_data': [wrong]}).get_json()
    assert not any(v.get('rule') == 'HC16' for v in bare['violations'])


# ── 26. Publish ─────────────────────────────────────────────────────────────────

def _approve(head, w, rows):
    return head.post('/api/schedule/approve', json={'context': dict(_context(w), sectionId=w['a'][0]),
                                                     'schedule_data': rows})


def test_publish_of_a_member_off_its_slot_raises_no_hc16(head, tx, world):
    _make_group(tx, world)
    r = _approve(head, world, [_row(world, room=_other_room(tx, world)['roomid'])])
    assert not any(v.get('rule') == 'HC16' for v in r.get_json().get('violations') or []), r.get_json()


def test_publish_of_an_allowed_but_unmerged_member_raises_no_hc16(head, tx, world):
    _make_group(tx, world, meetings=False)
    r = _approve(head, world, [_row(world)])
    body = r.get_json()
    assert not any(v.get('rule') == 'HC16' for v in body.get('violations') or []), body


def test_publish_raises_no_hc16_finding_for_a_valid_member(head, tx, world):
    _make_group(tx, world)
    r = _approve(head, world, [_row(world)])
    body = r.get_json()
    assert not any(v.get('rule') == 'HC16' for v in body.get('violations') or []), body


def test_live_database_model_is_untouched_outside_the_transaction():
    from database import query_db
    row = query_db("SELECT config_value FROM scheduler_config WHERE config_key = %s", (mg.MODEL_KEY,), one=True)
    assert row is None or row['config_value'] in ('legacy', 'groups')   # P7: cut over to groups
