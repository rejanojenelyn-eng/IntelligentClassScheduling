"""
Constraint-fix Phase 2: HC9 / HC17 merge-aware faculty load.

Requirements pinned here:
  A. A normal 3-hour class is 3 hours.
  B. A same-faculty valid merged 3-hour class is 3 hours total, not 3 x sections --
     inside one payload, across sections (existing load), and in every load path.
  C. 3+ section merges need every section pair to be HC16-valid.
  D. Different-faculty NSTP/OU shared classes: each faculty keeps their own hours.
  E. Local arrangements never enter load (placement only); Draft Local neither.
  F. Draft + Published versions are not both counted (Draft-preferred batch).
  G. Regular and PT buckets stay separate; a legacy unbucketed number is counted once.
  H. Sibling sections count; only the current section is excluded when known.
No real database is touched.
"""
from datetime import time
from pathlib import Path

import pytest

import app as app_module
import faculty_load
from scheduler import CSPValidator

APP_SRC = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8')

NSTP_ONLY = {'hc_merge_enabled': 1, 'hc_merge_scope': 'nstp_only'}
PAIRS_AB_AC = dict(NSTP_ONLY, hc_merge_section_pairs='[["BSIT-A","BSIT-B"],["BSIT-A","BSIT-C"]]')
PAIRS_ALL = dict(NSTP_ONLY, hc_merge_section_pairs=(
    '[["BSIT-A","BSIT-B"],["BSIT-A","BSIT-C"],["BSIT-B","BSIT-C"]]'))


def _fac(reg=18, pt=12, ts=0, status='Permanent'):
    return {'fullname': 'Faculty', 'employeestatus': status, 'designationid': None,
            'employeetype': {'regularload': reg, 'parttimeload': pt, 'teachingsubstitution': ts,
                             'regular_start': time(7, 30), 'regular_end': time(16, 30)}}


def _gene(code='IT101', fac='F1', day='Monday', start=time(9, 0), end=time(12, 0),
          section=None, days=None, hrs=None):
    days = days or [day]
    g = {'subject_code': code, 'faculty_id': fac, 'day': days[0], 'days_list': days,
         'start_time': start, 'end_time': end,
         'duration_hrs': hrs if hrs is not None else
         len(days) * ((end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)) / 60.0}
    if section:
        g.update(course='BSIT', section_name=section)
    return g


def _slice(code='NSTP101', fac='F1', day='Sunday', start=time(8, 0), end=time(11, 0), section='B'):
    return {'faculty_id': fac, 'subject_code': code, 'day': day, 'start_time': start,
            'end_time': end, 'programcode': 'BSIT', 'section_name': section}


def _hc9(schedule, fmap, existing=None, cfg=NSTP_ONLY):
    return [v for v in CSPValidator(config=cfg)._check_load_limits(schedule, fmap, existing)
            if v['rule'] == 'HC9']


# ── A / G: buckets and legacy numbers (CSPValidator HC9) ────────────────────

def test_a_normal_three_hour_class_is_three_hours():
    assert _hc9([_gene()], {'F1': _fac(reg=3)}) == []
    assert _hc9([_gene()], {'F1': _fac(reg=2.5)})


def test_g_bucketed_existing_regular_load_only_hits_the_regular_bucket():
    sched = [_gene(), _gene(code='IT102', start=time(18, 0), end=time(21, 0))]  # 3h reg + 3h PT
    fmap = {'F1': _fac(reg=18, pt=12)}
    assert _hc9(sched, fmap, {'F1': {'regular': 15, 'pt': 0}}) == []      # 18/18, 3/12
    assert _hc9(sched, fmap, {'F1': {'regular': 16, 'pt': 0}})            # 19/18 regular
    assert _hc9(sched, fmap, {'F1': {'regular': 0, 'pt': 10}})            # 13/12 PT


def test_g_legacy_number_is_counted_once_not_added_to_both_buckets():
    sched = [_gene(), _gene(code='IT102', start=time(18, 0), end=time(21, 0))]
    fmap = {'F1': _fac(reg=18, pt=12)}          # total capacity 30
    # Old behavior added 10 to BOTH buckets -> PT 13/12 violation. 3+3+10 = 16 <= 30.
    assert _hc9(sched, fmap, {'F1': 10}) == []
    assert _hc9(sched, fmap, {'F1': 25})         # 31 > 30


# ── B / C / D: merged meetings across sections (existing slices) ────────────

def test_b_merge_with_other_section_already_counted_is_not_counted_again():
    gene = _gene(code='NSTP101', day='Sunday', start=time(8, 0), end=time(11, 0), section='A')
    fmap = {'F1': _fac(pt=3)}
    existing = {'F1': {'regular': 0, 'pt': 3, 'total': 3, 'slices': [_slice(section='B')]}}
    assert _hc9([gene], fmap, existing) == []                     # one 3h meeting, not 6h


def test_b_without_the_partner_the_same_meeting_is_counted():
    gene = _gene(code='NSTP101', day='Sunday', start=time(8, 0), end=time(11, 0), section='A')
    existing = {'F1': {'regular': 0, 'pt': 3, 'total': 3, 'slices': []}}
    assert _hc9([gene], {'F1': _fac(pt=3)}, existing)             # a different 3h class: 6h


def test_c_disallowed_section_pair_is_not_deduplicated_across_sections():
    gene = _gene(code='NSTP101', day='Sunday', start=time(8, 0), end=time(11, 0), section='C')
    existing = {'F1': {'regular': 0, 'pt': 3, 'total': 3, 'slices': [_slice(section='B')]}}
    cfg = dict(NSTP_ONLY, hc_merge_section_pairs='[["BSIT-A","BSIT-B"]]')
    assert _hc9([gene], {'F1': _fac(pt=3)}, existing, cfg=cfg)


def test_d_other_faculty_shared_nstp_session_does_not_reduce_this_faculty_load():
    gene = _gene(code='NSTP101', day='Sunday', start=time(8, 0), end=time(11, 0), section='A')
    existing = {'F2': {'regular': 0, 'pt': 3, 'total': 3, 'slices': [_slice(fac='F2')]}}
    assert _hc9([gene], {'F1': _fac(pt=2), 'F2': _fac(pt=3)}, existing)   # F1 still 3h > 2h


# ── merge_aware_additional_hours (Publish load gate submitted rows) ─────────

def test_additional_hours_normal_and_multi_day_rows():
    assert faculty_load.merge_aware_additional_hours([_gene()], config=NSTP_ONLY) == 3.0
    two_day = _gene(days=['Monday', 'Thursday'], start=time(9, 0), end=time(10, 30))
    assert faculty_load.merge_aware_additional_hours([two_day], config=NSTP_ONLY) == 3.0


def test_additional_hours_same_faculty_merge_counts_once():
    rows = [_gene(code='NSTP101', day='Sunday', start=time(8, 0), end=time(11, 0), section=s)
            for s in ('A', 'B')]
    assert faculty_load.merge_aware_additional_hours(rows, config=NSTP_ONLY) == 3.0


def test_additional_hours_three_sections_need_the_full_clique():
    rows = [_gene(code='NSTP101', day='Sunday', start=time(8, 0), end=time(11, 0), section=s)
            for s in ('A', 'B', 'C')]
    assert faculty_load.merge_aware_additional_hours(rows, config=PAIRS_AB_AC) == 9.0
    assert faculty_load.merge_aware_additional_hours(rows, config=PAIRS_ALL) == 3.0


def test_additional_hours_out_of_scope_same_subject_is_not_a_merge():
    rows = [_gene(section='A'), _gene(section='B')]            # IT101 under nstp_only scope
    assert faculty_load.merge_aware_additional_hours(rows, config=NSTP_ONLY) == 6.0


def test_additional_hours_meeting_already_counted_in_other_section_adds_nothing():
    row = _gene(code='NSTP101', day='Sunday', start=time(8, 0), end=time(11, 0), section='A')
    assert faculty_load.merge_aware_additional_hours([row], [_slice()], config=NSTP_ONLY) == 0.0
    assert faculty_load.merge_aware_additional_hours(
        [row], [_slice(fac='F2')], config=NSTP_ONLY) == 3.0     # D: other faculty's slice


# ── summarize_faculty_load / batch query ────────────────────────────────────

def _batch_row(code, section_id, section, day, start, end, emp='F1'):
    hrs = ((end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)) / 60.0
    return {'employeenumber': emp, 'subjectcode': code, 'sectionid': section_id,
            'year_section': f'BSIT-1 {section}', 'days': day,
            'time_code': f'{start.hour:02d}{end.hour:02d}', 'time_range': 'x', 'hrs': hrs,
            'start_time': start, 'end_time': end, 'programcode': 'BSIT', 'sectionname': section}


def test_summary_counts_merged_class_once_and_keeps_buckets_separate():
    rows = [_batch_row('NSTP101', 1, 'A', 'Sunday', time(8, 0), time(11, 0)),
            _batch_row('NSTP101', 2, 'B', 'Sunday', time(8, 0), time(11, 0)),
            _batch_row('IT101', 1, 'A', 'Monday', time(9, 0), time(12, 0))]
    summary = faculty_load.summarize_faculty_load(rows, NSTP_ONLY)
    assert (summary['regular'], summary['pt'], summary['total']) == (3.0, 3.0, 6.0)
    assert len(summary['slices']) == 3
    assert faculty_load.load_total(summary) == 6.0 and faculty_load.load_total(4.5) == 4.5


class _RecordingCursor:
    def __init__(self, rows=()):
        self.rows, self.calls = list(rows), []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows


def test_h_batch_excludes_only_the_current_section_when_known():
    cur = _RecordingCursor()
    faculty_load.get_faculty_load_batch(cur, 'AY', 'A', exclude_program='BSIT',
                                        exclude_year_level=1, exclude_section_id=5, config={})
    sql, params = cur.calls[0]
    assert 'sc.sectionid IS DISTINCT FROM %(excl_sec)s' in sql and params['excl_sec'] == 5
    assert 'excl_prog' not in params                 # sibling sections still count


def test_h_batch_falls_back_to_program_year_without_a_section():
    cur = _RecordingCursor()
    faculty_load.get_faculty_load_batch(cur, 'AY', 'A', exclude_program='BSIT',
                                        exclude_year_level=1, config={})
    sql, params = cur.calls[0]
    assert params['excl_prog'] == 'BSIT' and 'excl_sec' not in params


def test_e_f_load_sources_are_the_live_effective_schedule():
    # Phase 9.5: live load = Published Official minus Local-overridden occurrences plus
    # active Published Local. Pending Drafts never change live load.
    for sql in (faculty_load._BATCH_HOURS_SQL_TMPL, faculty_load.FACULTY_SESSIONS_SQL):
        assert faculty_load.EFFECTIVE_SESSIONS_CTE in sql
        assert "'Draft'" not in sql
        assert 'has_draft' not in sql
    cte = faculty_load.EFFECTIVE_SESSIONS_CTE
    assert "sv.status = 'Published'" in cte
    assert "las_x.official_sessionid = ss.sessionid" in cte          # override displaces exactly
    assert "WHERE la.status = 'Published' AND la.is_active = TRUE" in cte


def test_get_faculty_scheduled_hours_is_merge_aware(monkeypatch):
    rows = [_batch_row('NSTP101', 1, 'A', 'Sunday', time(8, 0), time(11, 0)),
            _batch_row('NSTP101', 2, 'B', 'Sunday', time(8, 0), time(11, 0))]
    monkeypatch.setattr(faculty_load, 'get_faculty_sessions', lambda *a, **k: rows)
    total, sessions = faculty_load.get_faculty_scheduled_hours(None, 'F1', 'AY', 'A', config=NSTP_ONLY)
    assert total == 3.0 and len(sessions) == 2


# ── Publish/Save Draft total-load gate (app) ────────────────────────────────

class _SemCursor:
    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return {'academicyearid': 'AY2627', 'semestertype': 'A'}

    def close(self):
        pass


class _SemConn:
    def cursor(self, **k):
        return _SemCursor()

    def close(self):
        pass


def _gate(monkeypatch, existing, rows, **kw):
    seen = {}

    def fake_batch(cur, ay, sem, **k):
        seen.update(k, ay=ay, sem=sem)
        return existing
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: _SemConn())
    monkeypatch.setattr(app_module, 'load_scheduler_config', lambda: NSTP_ONLY)
    monkeypatch.setattr(faculty_load, 'get_faculty_load_batch', fake_batch)
    viols = app_module._check_cross_program_faculty_loads(
        rows, {'F1': _fac(reg=18, pt=12)}, 42, exclude_program='BSIT', exclude_year_level=1, **kw)
    return viols, seen


def test_gate_uses_merge_aware_batch_and_counts_only_new_hours(monkeypatch):
    existing = {'F1': {'regular': 28, 'pt': 0, 'total': 28, 'slices': []}}
    viols, seen = _gate(monkeypatch, existing, [_gene()], exclude_section_id=5)
    assert seen['exclude_section_id'] == 5 and (seen['ay'], seen['sem']) == ('AY2627', 'A')
    assert len(viols) == 1
    assert (viols[0]['current_load'], viols[0]['new_units'], viols[0]['total_load']) == (28.0, 3.0, 31.0)


def test_gate_does_not_recount_a_merged_meeting_from_another_section(monkeypatch):
    existing = {'F1': {'regular': 0, 'pt': 29, 'total': 29, 'slices': [_slice(section='B')]}}
    row = _gene(code='NSTP101', day='Sunday', start=time(8, 0), end=time(11, 0), section='A')
    viols, _ = _gate(monkeypatch, existing, [row])
    assert viols == []                                   # 29 + 0, not 29 + 3


def test_gate_no_longer_uses_a_raw_sql_sum():
    gate = APP_SRC[APP_SRC.index('def _check_cross_program_faculty_loads'):
                   APP_SRC.index('def _check_designee_night_limit')]
    assert 'SUM(ROUND' not in gate
    assert 'faculty_load.get_faculty_load_batch' in gate


def test_callers_pass_the_current_section():
    save_draft = APP_SRC[APP_SRC.index('def api_save_draft'):APP_SRC.index('def api_draft_sessions')]
    approve = APP_SRC[APP_SRC.index('def api_approve_schedule'):APP_SRC.index('def api_delete_draft')]
    assert 'exclude_section_id=ctx_section_id' in save_draft
    assert 'section_id=ctx_section_id' in save_draft
    assert 'exclude_section_id=_ctx_section_id' in approve
    assert 'section_id=_ctx_section_id' in approve
    generate = APP_SRC[APP_SRC.index('def api_generate_schedule'):APP_SRC.index('def api_save_draft')]
    assert 'section_id=_section_id_or_none(' in generate


def test_other_sections_load_is_bucketed_and_section_scoped(monkeypatch):
    seen = {}

    class _Conn(_SemConn):
        pass

    def fake_batch(cur, ay, sem, **k):
        seen.update(k)
        return {'F1': {'regular': 1, 'pt': 2, 'total': 3, 'slices': []}}
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: _Conn())
    monkeypatch.setattr(faculty_load, 'get_faculty_load_batch', fake_batch)
    out = app_module._other_sections_faculty_hours('AY2627', 'A', 'BSIT', 1, section_id='14')
    assert out['F1']['regular'] == 1 and seen['exclude_section_id'] == 14


# ── teaching_assignments respects HC16 section pairs ────────────────────────

_TA_FAC = {'employeenumber': 'F1', 'fullname': 'Faculty', 'employee_type': 'Permanent',
           'employeestatus': 'Permanent', 'designationid': None, 'reg_load': 18,
           'pt_load': 12, 'teach_sub': 0, 'desig_reg_load': 0, 'desig_night_service': 0}


def _ta_session(section, sid):
    return {'subjectcode': 'NSTP101', 'subjectname': 'NSTP', 'units': 3, 'hrs': 3.0,
            'year_section': f'BSIT-1 {section}', 'sectionid': sid, 'programcode': 'BSIT',
            'sectionname': section, 'subj_ref': 'A', 'time_range': '08:00 AM - 11:00 AM',
            'time_code': '0811', 'days': 'Sunday', 'room': 'R1', 'effectivity': '—',
            'status': 'Published'}


class _TaCursor:
    def __init__(self, sessions):
        self.sessions, self.last = sessions, ''

    def execute(self, sql, params=None):
        self.last = sql

    def fetchone(self):
        return dict(_TA_FAC) if 'FROM faculty f' in self.last else None

    def fetchall(self):
        return [dict(s) for s in self.sessions] if 'FROM ranked rk' in self.last else []

    def close(self):
        pass


class _TaConn:
    def __init__(self, sessions):
        self.cur = _TaCursor(sessions)

    def cursor(self, **k):
        return self.cur

    def commit(self):
        pass

    def close(self):
        pass


@pytest.mark.parametrize('cfg,expected_hrs', [(PAIRS_AB_AC, 9.0), (PAIRS_ALL, 3.0)])
def test_teaching_assignments_collapses_only_a_full_hc16_clique(monkeypatch, cfg, expected_hrs):
    import database
    sessions = [_ta_session(s, i) for i, s in enumerate(('A', 'B', 'C'), start=1)]
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: _TaConn(sessions))
    monkeypatch.setattr(database, 'load_scheduler_config', lambda: cfg)
    monkeypatch.setattr(app_module, 'load_scheduler_config', lambda: cfg)
    body = app_module.app.test_client().get(
        '/api/faculty/teaching_assignments?emp_num=F1&ay_id=AY2627&sem=A').get_json()
    assert body['success'] is True, body
    assert body['total_teaching_hours'] == expected_hrs
    assert body['buckets']['used'] == expected_hrs
