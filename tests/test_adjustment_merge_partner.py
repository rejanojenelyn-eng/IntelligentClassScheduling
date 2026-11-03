"""A Schedule Adjustment onto the SAME class of other sections — HC16 as Settings → Class
Merging defines it: same subject (in the merge scope, e.g. NSTP), faculty, day, start,
end AND room — merges with it instead of being a room/faculty double booking. Approval
publishes it to the Official schedule as a merged class (Publish's confirm_merges); a
Local arrangement can't take part in one. No database: query_db is stubbed."""
import inspect
from datetime import time

import app as app_module

REQ = {'subjectcode': 'NSTP 001', 'employeenumber': '20118', 'sectionid': 28,
       '_cfg': {'hc_merge_scope': 'nstp_only'}}


def _rows(*rooms):
    return [{'scheduleid': 100 + i, 'sectionid': 50 + i, 'sectionname': f'SEC-{i}', 'roomid': r,
             'roomname': f'ROOM {r}'} for i, r in enumerate(rooms)]


def _partners(monkeypatch, rows, room_id, req=REQ):
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: rows)
    return app_module._request_merge_partners(1, 'Sunday', time(7, 30), time(10, 30), room_id, req)


def test_same_room_is_a_merge(monkeypatch):
    out = _partners(monkeypatch, _rows(46, 46), 46)
    assert [p['sectionname'] for p in out['partners']] == ['SEC-0', 'SEC-1'] and out['other_room'] is None


def _stub(monkeypatch, partner_rows, room_rows):
    """query_db: the partner lookup, then the "is that room free otherwise" check."""
    calls = iter([partner_rows, room_rows])
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: next(calls, []))


def test_another_room_is_no_merge_but_says_which_room(monkeypatch):
    _stub(monkeypatch, _rows(46, 46), [])
    out = app_module._request_merge_partners(1, 'Sunday', time(7, 30), time(10, 30), 12, REQ)
    assert out['partners'] == []
    assert out['other_room'] == {'roomname': 'ROOM 46', 'sections': ['SEC-0', 'SEC-1']}


def test_hint_says_when_that_room_also_has_another_class(monkeypatch):
    other = [{'subjectcode': 'NSTP 001', 'sectionname': 'BSHM1', 'employeenumber': '999'}]
    _stub(monkeypatch, _rows(46, 46), other)
    out = app_module._request_merge_partners(1, 'Sunday', time(7, 30), time(10, 30), 12, REQ)
    assert out['other_room']['blocked_by'] == 'NSTP 001 (BSHM1)'


def test_subject_outside_the_merge_scope_never_merges(monkeypatch):
    req = dict(REQ, subjectcode='COMP 017')
    out = _partners(monkeypatch, _rows(46), 46, req)
    assert out == {'partners': [], 'other_room': None}


def test_tba_faculty_never_merges(monkeypatch):
    out = _partners(monkeypatch, _rows(46), 46, dict(REQ, employeenumber='TBA'))
    assert out['partners'] == []


def test_validation_and_approval_wiring():
    summary = inspect.getsource(app_module._request_conflict_summary)
    assert '_request_merge_partners(' in summary and "'merge_with':" in summary
    decide = inspect.getsource(app_module.api_requests_decide)
    assert 'MERGE_NEEDS_OFFICIAL' in decide and 'confirm_merges=bool(_merge_with)' in decide
    live = inspect.getsource(app_module.api_faculty_check_request_conflicts)
    assert "'merge_with':" in live and 'exclude_schedule_ids=_merge_ids' in live


# ── Settings' merge size limit (the HC17 rule's max sections, e.g. NSTP 2–3) ──────────

class _Cur:
    def __init__(self, policies):
        self.policies, self._last = policies, ''

    def execute(self, sql, params=None):
        self._last = sql

    def fetchone(self):
        return {'ok': True}

    def fetchall(self):
        return [{'code': c, 'max_sections': m} for c, m in self.policies]


def test_limit_comes_from_the_subjects_rule_and_nstp_covers_every_nstp_code():
    import merge_groups as mg
    cur = _Cur([('NSTP', 3), ('GEED 001', 2)])
    assert mg.merge_section_limit(cur, 'NSTP 001') == 3
    assert mg.merge_section_limit(cur, 'OU 101') == 3
    assert mg.merge_section_limit(cur, 'GEED 001') == 2
    assert mg.merge_section_limit(cur, 'COMP 017') is None


def test_over_the_limit_is_local_only(monkeypatch):
    import merge_groups as mg
    monkeypatch.setattr(mg, 'merge_section_limit', lambda cur, code: 3)
    # two partners already merged with a third section: 1 + 3 = 4 > 3
    monkeypatch.setattr(mg, 'load_groups', lambda *a, **k: [{'is_active': True, 'members': [
        {'sectionid': 50, 'subjectcode': 'NSTP 001'}, {'sectionid': 51, 'subjectcode': 'NSTP 001'},
        {'sectionid': 52, 'subjectcode': 'NSTP 001'}]}])
    partners = [{'scheduleid': 1, 'sectionid': 50, 'sectionname': 'A'}, {'scheduleid': 2, 'sectionid': 51, 'sectionname': 'B'}]
    assert app_module._request_merge_over_limit(1, REQ, partners) == {'size': 4, 'limit': 3}
    monkeypatch.setattr(mg, 'load_groups', lambda *a, **k: [])
    assert app_module._request_merge_over_limit(1, REQ, partners) is None     # 1 + 2 = 3: allowed


def test_limit_is_enforced_wherever_a_merge_is_created():
    import merge_groups as mg
    assert 'merge_limit_error(' in inspect.getsource(mg.preview)          # Settings / import / save_group
    assert 'merge_limit_error(' in inspect.getsource(mg.detect_merges)    # Manual Editor Save Draft / Publish
    gate = inspect.getsource(app_module._merge_confirmation_gate)
    assert 'MERGE_NOT_ALLOWED' in gate
    decide = inspect.getsource(app_module.api_requests_decide)
    assert "'MERGE_LIMIT'" in decide                                       # Official refused, Local allowed


def test_save_draft_published_check_sees_a_just_confirmed_merge():
    # Save Draft records a confirmed merge in its own still-open transaction; the
    # Published-conflict check must read merge groups through that same cursor, or the
    # merge it just recorded looks like a faculty/room double booking.
    save = inspect.getsource(app_module.api_save_draft)
    assert 'fail_closed=True, merge_cur=cur)' in save
    cross = inspect.getsource(app_module._check_cross_schedule_conflicts)
    assert 'cur=merge_cur)' in cross
