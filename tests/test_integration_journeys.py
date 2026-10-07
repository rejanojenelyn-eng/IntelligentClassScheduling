"""Final integration matrix — complete user journeys through the real endpoints.

Everything runs inside one rolled-back transaction per test (tests/_shared_tx.py).
Official journeys isolate persistence from scheduling-rule evaluation with `clean_gates`
(the rules have their own suites); Local journeys use real Local validation unless noted.

Journeys covered elsewhere:
  6  Official Published -> Local Draft -> Local Publish ....... test_local_override_snapshot_repair
  8  Local Published -> Schedule Adjustment approved .......... test_order_b_* (same file)
  9  Schedule Adjustment approved -> later Local Publish ...... test_order_a_* (same file)
  11 Failed Official Publish keeps old Published ............. test_official_persistence_repair
  13 Successful save -> navigate: no false warning ........... test_manual_editor_dirty_state
  14 Failed save -> navigate: warning remains ................ test_manual_editor_dirty_state
  15 Restart -> first requests: no schema race / deadlock .... test_local_override_snapshot_repair,
                                                               test_official_persistence_repair
"""
import datetime
import json

import pytest

import app as app_module
from conftest import requires_db
from test_official_persistence_repair import (  # noqa: F401  (fixtures)
    tx, scope, client, clean_gates, _row, _seed, _state, _versions, _approve, _save_draft, _cur)


def _existing(client, scope, code):
    q = (f"/api/manual/existing_sessions?subject_code={code}&ay_id={scope['academicyearid']}"
         f"&semester={scope['semestertype']}&section_id={scope['sectionid']}&scheduler_mode=official")
    return client.get(q).get_json()


# 1. Generate -> Save Draft -> Manual -> edit -> Save Draft -> Publish
@requires_db
def test_journey_1_generate_save_edit_save_publish(tx, scope, client, clean_gates):
    A, B = scope['A'], scope['B']
    generated = [_row(scope, A, 'Monday', '07:30', '09:00'), _row(scope, B, 'Tuesday', '07:30', '09:00', room_idx=1)]
    assert _save_draft(client, scope, generated, replace_draft=False).get_json()['success']
    assert _state(tx, scope) == {(A, 'Draft'): [('Monday', '07:30')], (B, 'Draft'): [('Tuesday', '07:30')]}
    # Manual Editor edits A and saves again; B's Draft is carried forward.
    assert _save_draft(client, scope, [_row(scope, A, 'Wednesday', '10:00', '11:30')]).get_json()['success']
    assert _state(tx, scope) == {(A, 'Draft'): [('Wednesday', '10:00')], (B, 'Draft'): [('Tuesday', '07:30')]}
    # Publish All.
    r = _approve(client, scope, [_row(scope, A, 'Wednesday', '10:00', '11:30'),
                                 _row(scope, B, 'Tuesday', '07:30', '09:00', room_idx=1)])
    assert r.get_json()['success'], r.get_json()
    assert _state(tx, scope) == {(A, 'Published'): [('Wednesday', '10:00')],
                                 (B, 'Published'): [('Tuesday', '07:30')]}


# 2. Generate -> Publish directly
@requires_db
def test_journey_2_generate_then_publish_directly(tx, scope, client, clean_gates):
    A = scope['A']
    r = _approve(client, scope, [_row(scope, A, 'Monday', '07:30', '09:00')])
    assert r.get_json()['success'], r.get_json()
    assert _state(tx, scope) == {(A, 'Published'): [('Monday', '07:30')]}


# Generation's "replace existing Draft" is atomic with the save.
@requires_db
def test_journey_2b_generation_replace_is_atomic(tx, scope, client, clean_gates):
    A, B = scope['A'], scope['B']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00')])
    _seed(tx, scope, 'Draft', 2, [_row(scope, B, 'Friday', '07:30', '09:00', room_idx=1)])
    # A failing save (removing a subject that is not Published) changes nothing.
    r = _save_draft(client, scope, [_row(scope, A, 'Tuesday', '07:30', '09:00')],
                    replace_draft=True, removed_subjects=['NOT-LIVE'])
    assert r.status_code == 409
    assert _state(tx, scope)[(B, 'Draft')] == [('Friday', '07:30')]
    # A successful replace: the generated schedule is the whole new Draft.
    r = _save_draft(client, scope, [_row(scope, A, 'Tuesday', '07:30', '09:00')], replace_draft=True)
    assert r.get_json()['success'], r.get_json()
    assert _state(tx, scope) == {(A, 'Published'): [('Monday', '07:30')], (A, 'Draft'): [('Tuesday', '07:30')]}


# 3. Existing Published -> edit -> Save Draft -> leave/reopen -> Publish
@requires_db
def test_journey_3_edit_save_reopen_publish(tx, scope, client, clean_gates):
    A = scope['A']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00'),
                                      _row(scope, A, 'Thursday', '07:30', '09:00')])
    # Edit Thursday -> Friday; the Draft is the complete subject.
    assert _save_draft(client, scope, [_row(scope, A, 'Monday', '07:30', '09:00'),
                                       _row(scope, A, 'Friday', '07:30', '09:00')]).get_json()['success']
    assert _state(tx, scope)[(A, 'Published')] == [('Monday', '07:30'), ('Thursday', '07:30')]
    reopened = _existing(client, scope, A)['sessions']          # what the editor loads on reopen
    assert sorted(s['daydesc'] for s in reopened) == ['Friday', 'Monday']
    draft = _versions(tx, scope, A, 'Draft')[0]['versionid']
    r = _approve(client, scope, [_row(scope, A, s['daydesc'], '07:30', '09:00', versionid=draft,
                                      sessionid=s['sessionid']) for s in reopened])
    assert r.get_json()['success'], r.get_json()
    assert _state(tx, scope) == {(A, 'Published'): [('Friday', '07:30'), ('Monday', '07:30')]}


# 4. Existing Published -> remove one slice -> Save Draft -> Publish
@requires_db
def test_journey_4_remove_one_slice(tx, scope, client, clean_gates):
    A = scope['A']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00'),
                                      _row(scope, A, 'Thursday', '07:30', '09:00')])
    assert _save_draft(client, scope, [_row(scope, A, 'Monday', '07:30', '09:00')]).get_json()['success']
    assert _state(tx, scope)[(A, 'Published')] == [('Monday', '07:30'), ('Thursday', '07:30')]
    r = _approve(client, scope, [_row(scope, A, 'Monday', '07:30', '09:00')])
    assert r.get_json()['success'], r.get_json()
    assert _state(tx, scope) == {(A, 'Published'): [('Monday', '07:30')]}


# 5. Existing Published -> remove entire subject -> Save Draft -> Publish
@requires_db
def test_journey_5_remove_entire_subject(tx, scope, client, clean_gates):
    A, B = scope['A'], scope['B']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00'),
                                      _row(scope, B, 'Tuesday', '07:30', '09:00', room_idx=1)])
    assert _save_draft(client, scope, [], removed_subjects=[B]).get_json()['success']
    assert _state(tx, scope)[(B, 'Published')] == [('Tuesday', '07:30')]       # still live
    assert _existing(client, scope, B)['removed_in_draft'] is True
    r = _approve(client, scope, [], removed_subjects=[B])
    assert r.get_json()['success'], r.get_json()
    assert _state(tx, scope) == {(A, 'Published'): [('Monday', '07:30')]}


# ── Local journeys ───────────────────────────────────────────────────────────────
from test_local_override_snapshot_repair import (  # noqa: E402,F401  (fixtures)
    ls, clean_request_rules, _save as _local_save, _publish as _local_publish,
    _active_local, _official_rows)
from test_local_override_snapshot_repair import client as local_client  # noqa: E402,F401


# 7. Local Published -> later Local edit -> Publish: unrelated overrides survive
@requires_db
def test_journey_7_later_local_edit_keeps_unrelated_overrides(tx, ls, local_client):
    o1, o2 = ls['occ'][0], ls['occ'][1]
    first = _local_save(local_client, ls, [o1])
    assert _local_publish(local_client, first).get_json()['success']
    second = _local_save(local_client, ls, [o2], reason='later edit')
    r = _local_publish(local_client, second)
    assert r.status_code == 200 and r.get_json()['success'], r.get_json()
    ids, occs = _active_local(tx, ls)
    assert ids == {second['arrangement_id']}
    assert occs == sorted([o1['sessionid'], o2['sessionid']])


# 10. Make-up approved: one-date occupancy only; Official and Local persistence unaffected
@requires_db
def test_journey_10_makeup_approval_is_temporary_only(tx, ls, local_client, monkeypatch):
    monkeypatch.setattr(app_module, '_request_conflict_summary',
                        lambda *a, **k: ({'all_ok': True, 'conflicts': []}, None))
    o = ls['occ'][0]
    official_before, local_before = _official_rows(tx, ls), _active_local(tx, ls)
    cur = _cur(tx)
    cur.execute("""INSERT INTO class_meeting_request (scheduleid, requested_date, new_starttimeid,
                       new_endtimeid, reason, submitted_by, status)
                   VALUES (%s, %s, %s, %s, 'make-up', %s, 'Pending') RETURNING requestid""",
                (o['scheduleid'], datetime.date.today() + datetime.timedelta(days=3),
                 o['starttimeid'], o['endtimeid'], o['fac']))
    rid = cur.fetchone()['requestid']
    tx.handle().commit()
    r = local_client.post('/api/requests/decide', json={'type': 'makeup', 'id': rid,
                                                       'decision': 'Approved', 'remarks': ''})
    assert r.get_json()['success'], r.get_json()
    cur.execute("SELECT status FROM class_meeting_request WHERE requestid = %s", (rid,))
    assert cur.fetchone()['status'] == 'Approved'
    assert _official_rows(tx, ls) == official_before
    assert _active_local(tx, ls) == local_before


# 12. Failed Local Publish: the previous Local Published snapshot stays active
@requires_db
def test_journey_12_failed_local_publish_keeps_previous_snapshot(tx, ls, local_client, monkeypatch):
    o1, o2 = ls['occ'][0], ls['occ'][1]
    first = _local_save(local_client, ls, [o1])
    assert _local_publish(local_client, first).get_json()['success']
    before = _active_local(tx, ls)
    second = _local_save(local_client, ls, [o2], reason='will fail')
    monkeypatch.setattr(app_module, '_validate_local_faculty_service_rules',
                        lambda *a, **k: (False, 'faculty no longer available'))
    r = _local_publish(local_client, second)
    assert r.status_code == 409
    assert _active_local(tx, ls) == before
    cur = _cur(tx)
    cur.execute("SELECT status, is_active FROM local_arrangement WHERE arrangementid = %s",
                (second['arrangement_id'],))
    assert dict(cur.fetchone()) == {'status': 'Draft', 'is_active': False}
