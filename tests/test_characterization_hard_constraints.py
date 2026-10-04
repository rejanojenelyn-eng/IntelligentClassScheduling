"""
Characterization tests for CSPValidator's behavior.

FINAL HC1-HC17 numbering is active as of Phase B checkpoint 2 (see
scheduler.RULE_LABELS / LEGACY_RULE_ID_MAP for the mapping from the internal
IDs used before that checkpoint). These tests pin down what the code
actually does; if one of them ever needs to change, that change is a
deliberate, approved behavior decision, not a refactor side-effect.

No database required (same pattern as test_csp_validator.py): CSPValidator
takes plain dicts.
"""
from datetime import time

from scheduler import CSPValidator


def _fac(status='', designation=None, night_svc=0, spec=None, **et_over):
    """A faculty_map entry. Employee status '' (blank) deliberately bypasses
    every HC1/HC2/HC3 branch in _check_time_windows -- that's current, real
    behavior (the code only checks designation-not-None, 'Part-Time', or
    'Permanent'/'Temporary'; anything else falls through unchecked)."""
    et = dict(et_over)
    return {
        'designationid': designation,
        'employeestatus': status,
        'employeetype': et,
        'nightteachingservice': night_svc,
        'fullname': 'Test Faculty',
        'specializationname': spec or '',
    }


def _gene(**over):
    g = {
        'subject_code': 'IT101', 'class_type': 'Lecture', 'course': 'BSIT',
        'faculty_id': 'F1', 'room_id': 1, 'room_type': 'Lecture',
        'day': 'Monday', 'days_list': ['Monday'],
        'start_time': time(7, 30), 'end_time': time(9, 0),
        'duration_hrs': 1.5,
    }
    g.update(over)
    return g


def _rules(violations):
    return {v['rule'] for v in violations}


# ── HC1 Regular Teaching Hours (full-time) ─────────────────────────────────

def test_hc1_fulltime_class_before_window_and_not_am_pt_slot_violates():
    fac = _fac(status='Permanent')
    cls = _gene(faculty_id='F1', start_time=time(6, 0), end_time=time(7, 0))
    v = CSPValidator(config={}).validate([cls], {'F1': fac})
    assert 'HC1' in _rules(v)


def test_hc1_fulltime_class_within_regular_window_passes():
    fac = _fac(status='Permanent')
    cls = _gene(faculty_id='F1', start_time=time(7, 30), end_time=time(16, 30))
    v = CSPValidator(config={}).validate([cls], {'F1': fac})
    assert 'HC1' not in _rules(v)


def test_hc1_am_pt_slot_7_30_to_9_00_is_allowed_and_shifts_window():
    # A 7:30-9:00 AM slot is explicitly allowed and shifts this faculty's
    # regular window to 9:00-18:00 for the REST of that same day.
    fac = _fac(status='Permanent')
    am_slot = _gene(faculty_id='F1', start_time=time(7, 30), end_time=time(9, 0))
    shifted_ok = _gene(faculty_id='F1', subject_code='IT102',
                        start_time=time(9, 0), end_time=time(18, 0))
    v = CSPValidator(config={}).validate([am_slot, shifted_ok], {'F1': fac})
    assert 'HC1' not in _rules(v)


# ── HC2 Designee Regular Teaching Hours ─────────────────────────────────────

def test_hc2_designee_no_night_service_uses_8_to_17_window():
    # PHASE B CHECKPOINT 1 CHANGE: this used to assert a 7:30-8:00 designee
    # class violates HC2, because the designee branch had no AM-PT exception
    # at all and evaluated it as a "regular slot" against the 8:00-17:00
    # window. Under the final policy, ANY weekday 7:30-9:00 slice is
    # PT/extra-teaching load for every faculty type (including designees) --
    # HC2 no longer evaluates it at all; final HC4 does (see
    # test_designee_am_pt_window_is_hc4_not_hc2 below and
    # tests/test_phase_b_designee_pt.py). This test now proves a GENUINELY
    # still-Regular-classified slot (starts within the plain 7:30-16:30
    # window, extends past the AM-PT window's 9:00 end, and starts before
    # the designee's own 8:00 privilege window) still correctly violates HC2.
    fac = _fac(designation=1, night_svc=0)
    cls = _gene(faculty_id='F1', start_time=time(7, 30), end_time=time(9, 30))
    v = CSPValidator(config={'hc_time_blocks_enabled': 0}).validate([cls], {'F1': fac})
    assert 'HC2' in _rules(v)


def test_designee_am_pt_window_is_hc4_not_hc2():
    # Final policy: a designee's 7:30-9:00 AM class is PT/extra-teaching
    # load, never Regular -- HC2 must not evaluate it at all, regardless of
    # night-teaching-service status.
    fac = _fac(designation=1, night_svc=0)
    cls = _gene(faculty_id='F1', start_time=time(7, 30), end_time=time(8, 0))
    v = CSPValidator(config={}).validate([cls], {'F1': fac})
    assert 'HC2' not in _rules(v)
    assert 'HC4' not in _rules(v)  # within the AM window -> final HC4 passes too


def test_hc2_designee_with_night_service_uses_regular_7_30_to_16_30_window():
    # Current behavior: nightteachingservice > 0 relaxes the designee window
    # back to the faculty's own regular_start/regular_end (default 7:30-16:30)
    # instead of the stricter 8:00-17:00.
    fac = _fac(designation=1, night_svc=2)
    cls = _gene(faculty_id='F1', start_time=time(7, 30), end_time=time(8, 0))
    v = CSPValidator(config={}).validate([cls], {'F1': fac})
    assert 'HC2' not in _rules(v)


# ── HC3 Full-Time Extra Teaching Load / HC4 Designee Extra Teaching Load ───

def test_hc3_designee_night_service_no_longer_gates_evening_teaching_per_slot():
    # PHASE B CHECKPOINT 1 CHANGE: this used to assert a fully-night-service-
    # committed designee (night_svc=6 -> old "allowed_nights = 6-6 = 0"
    # formula) could never teach ANY evening class, flagged (mislabeled)
    # 'HC3'. That per-slot dynamic-formula gate has been REMOVED from
    # _check_time_windows: it's superseded by the centralized, correctly-
    # scoped final HC8 unique-teaching-night count (_check_night_pt_cap),
    # which now uses a flat hc7_max_night cap and no longer considers
    # nightteachingservice at all (see
    # tests/test_phase_b_designee_pt.py::test_hc7_max_night_config_value_is_now_active).
    # This test proves _check_time_windows itself no longer produces this
    # 'HC3' gate for ANY night_svc value -- the class below still gets
    # flagged, but under final HC4, because 17:00-18:30 falls outside the
    # designee's configured PM PT window (16:30-18:00), not because of
    # night-service capacity.
    fac = _fac(designation=1, night_svc=6)
    cls = _gene(faculty_id='F1', start_time=time(17, 0), end_time=time(18, 30))
    v = CSPValidator(config={'hc_time_blocks_enabled': 0}).validate([cls], {'F1': fac})
    assert 'HC3' not in _rules(v)
    assert 'HC4' in _rules(v)


def test_hc3_part_time_daytime_slot_without_ts_hours_violates():
    fac = _fac(status='Part-Time', restrict_pt_hours=True, teachingsubstitution=0)
    cls = _gene(faculty_id='F1', start_time=time(9, 0), end_time=time(10, 30))
    v = CSPValidator(config={}).validate([cls], {'F1': fac})
    assert 'HC3' in _rules(v)


def test_hc3_part_time_daytime_slot_with_ts_hours_is_allowed():
    # Current behavior: PT faculty WITH teachingsubstitution hours may teach
    # during regular daytime hours -- it's counted as TS load, not PT load.
    fac = _fac(status='Part-Time', restrict_pt_hours=True, teachingsubstitution=3)
    cls = _gene(faculty_id='F1', start_time=time(9, 0), end_time=time(10, 30))
    v = CSPValidator(config={}).validate([cls], {'F1': fac})
    assert 'HC3' not in _rules(v)


def test_hc3_fulltime_evening_class_outside_16_30_to_21_00_window_violates():
    fac = _fac(status='Permanent')
    cls = _gene(faculty_id='F1', start_time=time(21, 0), end_time=time(22, 30))
    v = CSPValidator(config={}).validate([cls], {'F1': fac})
    assert 'HC3' in _rules(v)


def test_hc3_weekend_slot_outside_7_30_to_21_00_violates():
    fac = _fac(status='Permanent')
    cls = _gene(faculty_id='F1', day='Saturday', days_list=['Saturday'],
                start_time=time(5, 0), end_time=time(6, 30))
    v = CSPValidator(config={}).validate([cls], {'F1': fac})
    assert 'HC3' in _rules(v)


# ── HC5 Restricted-Day Subject Requirement (was HC4) ────────────────────────

def test_hc5_non_nstp_subject_on_sunday_violates_by_default():
    cls = _gene(subject_code='IT101', day='Sunday', days_list=['Sunday'])
    v = CSPValidator(config={}).validate([cls], {})
    assert 'HC5' in _rules(v)


def test_hc5_nstp_subject_on_sunday_is_allowed():
    cls = _gene(subject_code='NSTP101', day='Sunday', days_list=['Sunday'])
    v = CSPValidator(config={}).validate([cls], {})
    assert 'HC5' not in _rules(v)


def test_hc5_can_be_disabled_via_config():
    cls = _gene(subject_code='IT101', day='Sunday', days_list=['Sunday'])
    v = CSPValidator(config={'hc_weekend_enabled': 0}).validate([cls], {})
    assert 'HC5' not in _rules(v)


# ── HC7 Required Day Pairing (was HC6) ──────────────────────────────────────

def test_hc7_invalid_day_pair_violates():
    cls = _gene(days_list=['Monday', 'Friday'])
    v = CSPValidator(config={}).validate([cls], {})
    assert 'HC7' in _rules(v)


def test_hc7_default_valid_pair_mon_thu_passes():
    cls = _gene(days_list=['Monday', 'Thursday'])
    v = CSPValidator(config={}).validate([cls], {})
    assert 'HC7' not in _rules(v)


def test_hc7_can_be_disabled_via_config():
    cls = _gene(days_list=['Monday', 'Friday'])
    v = CSPValidator(config={'hc_day_pairing_enabled': 0}).validate([cls], {})
    assert 'HC7' not in _rules(v)


# ── HC8 Designee PT Teaching-Night Limit (was HC7; flat hc7_max_night cap) ─

def test_hc8_within_allowed_nights_passes():
    # PHASE B CHECKPOINT 1: night_svc is set here but is NO LONGER consulted
    # by _check_night_pt_cap under the final policy -- the cap is now a flat
    # hc7_max_night (default 2), coincidentally the same number the old
    # "6 - night_svc" formula produced at night_svc=4. Kept at 4 (rather than
    # 0) specifically to prove night_svc's value no longer matters. Times
    # are within the designee PM PT window to isolate the night-count
    # behavior from the final HC4 window check.
    fac = _fac(designation=1, night_svc=4)
    c1 = _gene(faculty_id='F1', day='Monday', days_list=['Monday'],
               start_time=time(16, 30), end_time=time(18, 0))
    c2 = _gene(faculty_id='F1', subject_code='IT102', day='Tuesday',
               days_list=['Tuesday'], start_time=time(16, 30), end_time=time(18, 0))
    v = CSPValidator(config={'hc_time_blocks_enabled': 0}).validate([c1, c2], {'F1': fac})
    assert 'HC8' not in _rules(v)


def test_hc8_exceeding_allowed_nights_violates():
    # See test_hc8_within_allowed_nights_passes -- night_svc is now inert;
    # the flat hc7_max_night=2 default is what makes 3 nights violate.
    fac = _fac(designation=1, night_svc=4)
    c1 = _gene(faculty_id='F1', day='Monday', days_list=['Monday'],
               start_time=time(16, 30), end_time=time(18, 0))
    c2 = _gene(faculty_id='F1', subject_code='IT102', day='Tuesday',
               days_list=['Tuesday'], start_time=time(16, 30), end_time=time(18, 0))
    c3 = _gene(faculty_id='F1', subject_code='IT103', day='Wednesday',
               days_list=['Wednesday'], start_time=time(16, 30), end_time=time(18, 0))
    v = CSPValidator(config={'hc_time_blocks_enabled': 0}).validate([c1, c2, c3], {'F1': fac})
    assert 'HC8' in _rules(v)


def test_hc8_max_night_config_value_is_now_active():
    # PHASE B CHECKPOINT 1 CHANGE: this test used to be named
    # test_hc7_ignores_hc7_max_night_config_value and proved the OPPOSITE --
    # that hc7_max_night was write-only dead config (see the Phase A audit)
    # because _check_night_pt_cap always used a dynamic
    # "6 - nightteachingservice" formula instead. Under the final policy,
    # hc7_max_night IS the real, active cap (the config KEY NAME is kept
    # for backward compatibility even though the rule it controls is now
    # final HC8, not HC7), and nightteachingservice no longer affects it at
    # all. With the cap raised to 3, a three-evening schedule that violates
    # by default now correctly passes, proving the config value is live.
    # See tests/test_phase_b_designee_pt.py for the fuller HC8 test matrix.
    fac = _fac(designation=1, night_svc=0)
    c1 = _gene(faculty_id='F1', day='Monday', days_list=['Monday'],
               start_time=time(16, 30), end_time=time(18, 0))
    c2 = _gene(faculty_id='F1', subject_code='IT102', day='Tuesday',
               days_list=['Tuesday'], start_time=time(16, 30), end_time=time(18, 0))
    c3 = _gene(faculty_id='F1', subject_code='IT103', day='Wednesday',
               days_list=['Wednesday'], start_time=time(16, 30), end_time=time(18, 0))
    cfg = {'hc_time_blocks_enabled': 0, 'hc7_max_night': 3}
    v = CSPValidator(config=cfg).validate([c1, c2, c3], {'F1': fac})
    assert 'HC8' not in _rules(v)

    cfg_default = {'hc_time_blocks_enabled': 0}   # default hc7_max_night = 2
    v_default = CSPValidator(config=cfg_default).validate([c1, c2, c3], {'F1': fac})
    assert 'HC8' in _rules(v_default)


# ── HC9 Teaching Load Limit (was HC8) ───────────────────────────────────────

def test_hc9_regular_load_over_limit_violates():
    fac = _fac(status='Permanent', regularload=3, teachingsubstitution=0,
               regular_end=time(16, 30))
    c1 = _gene(faculty_id='F1', day='Monday', days_list=['Monday'],
               start_time=time(7, 30), end_time=time(9, 30), duration_hrs=2)
    c2 = _gene(faculty_id='F1', subject_code='IT102', day='Tuesday',
               days_list=['Tuesday'], start_time=time(7, 30), end_time=time(9, 30),
               duration_hrs=2)
    v = CSPValidator(config={}).validate([c1, c2], {'F1': fac})
    assert 'HC9' in _rules(v)


def test_hc9_regular_load_within_limit_passes():
    fac = _fac(status='Permanent', regularload=5, teachingsubstitution=0,
               regular_end=time(16, 30))
    c1 = _gene(faculty_id='F1', day='Monday', days_list=['Monday'],
               start_time=time(7, 30), end_time=time(9, 30), duration_hrs=2)
    c2 = _gene(faculty_id='F1', subject_code='IT102', day='Tuesday',
               days_list=['Tuesday'], start_time=time(7, 30), end_time=time(9, 30),
               duration_hrs=2)
    v = CSPValidator(config={}).validate([c1, c2], {'F1': fac})
    assert 'HC9' not in _rules(v)


# ── HC11 Room Schedule Conflict (was HC9) ───────────────────────────────────

def test_hc11_same_room_overlapping_time_violates():
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=5,
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='IT102', faculty_id='F2', room_id=5,
              start_time=time(9, 0), end_time=time(10, 30))
    v = CSPValidator(config={}).validate([a, b], {})
    assert 'HC11' in _rules(v)


def test_hc11_tba_room_never_conflicts():
    a = _gene(subject_code='IT101', faculty_id='F1', room_id='TBA',
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='IT102', faculty_id='F2', room_id='TBA',
              start_time=time(9, 0), end_time=time(10, 30))
    v = CSPValidator(config={}).validate([a, b], {})
    assert 'HC11' not in _rules(v)


# ── HC10 Faculty Schedule Conflict (unchanged number) ───────────────────────

def test_hc10_same_faculty_overlapping_time_violates():
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=1,
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='IT102', faculty_id='F1', room_id=2,
              start_time=time(9, 0), end_time=time(10, 30))
    v = CSPValidator(config={}).validate([a, b], {})
    assert 'HC10' in _rules(v)


def test_hc10_same_faculty_two_nstp_ou_sections_overlapping_is_allowed():
    a = _gene(subject_code='NSTP101', faculty_id='F1', room_id=1,
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='NSTP102', faculty_id='F1', room_id=2,
              start_time=time(9, 0), end_time=time(10, 30))
    v = CSPValidator(config={}).validate([a, b], {})
    assert 'HC10' not in _rules(v)


# ── HC12 Section Schedule Conflict (was HC11) ───────────────────────────────

def test_hc12_overlapping_entries_with_unknown_section_identity_violate():
    # CORRECTION (Phase B checkpoint 2): _check_section_overlaps DOES check
    # section identity via _same_section -- it just treats "identity unknown
    # on either side" as "same section" by design (most single-section
    # generation contexts never stamp a per-gene section identity at all,
    # since the whole validate() call is already implicitly scoped to one
    # section). These genes carry no section_name, so identity is unknown on
    # both sides -> treated as the same section -> HC12 fires, even with
    # different faculty AND different rooms. See
    # test_hc12_different_sections_with_known_identity_do_not_violate below
    # for the case where identity IS known and genuinely differs.
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=1,
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='MATH201', faculty_id='F2', room_id=2,
              start_time=time(9, 0), end_time=time(10, 30))
    v = CSPValidator(config={}).validate([a, b], {})
    assert 'HC12' in _rules(v)


def test_hc12_different_sections_with_known_identity_do_not_violate():
    # Final HC12 requirement: two DIFFERENT, identifiable sections meeting at
    # the same time must NOT be flagged as a section conflict -- that's
    # normal for a multi-section timetable.
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=1,
              course='BSIT', section_name='A',
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='MATH201', faculty_id='F2', room_id=2,
              course='BSIT', section_name='B',
              start_time=time(9, 0), end_time=time(10, 30))
    v = CSPValidator(config={}).validate([a, b], {})
    assert 'HC12' not in _rules(v)


def test_hc12_same_known_section_overlapping_violates():
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=1,
              course='BSIT', section_name='A',
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='MATH201', faculty_id='F2', room_id=2,
              course='BSIT', section_name='A',
              start_time=time(9, 0), end_time=time(10, 30))
    v = CSPValidator(config={}).validate([a, b], {})
    assert 'HC12' in _rules(v)


def test_hc12_different_sections_same_faculty_still_violates_hc10():
    # Final requirement: faculty conflicts belong to HC10 regardless of
    # section identity -- a genuine double-booking must still be caught even
    # when HC12 (section) correctly stays silent.
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=1,
              course='BSIT', section_name='A',
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='MATH201', faculty_id='F1', room_id=2,
              course='BSIT', section_name='B',
              start_time=time(9, 0), end_time=time(10, 30))
    v = CSPValidator(config={}).validate([a, b], {})
    assert 'HC12' not in _rules(v)
    assert 'HC10' in _rules(v)


def test_hc12_different_sections_same_room_still_violates_hc11():
    # Final requirement: room conflicts belong to HC11 regardless of section
    # identity.
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=5,
              course='BSIT', section_name='A',
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='MATH201', faculty_id='F2', room_id=5,
              course='BSIT', section_name='B',
              start_time=time(9, 0), end_time=time(10, 30))
    v = CSPValidator(config={}).validate([a, b], {})
    assert 'HC12' not in _rules(v)
    assert 'HC11' in _rules(v)


def test_hc12_is_always_enforced_regardless_of_hc_section_conflict_enabled_config():
    # AUDIT FINDING (still true post-renumbering): hc_section_conflict_enabled
    # exists in scheduler_config defaults but validate() never checks it --
    # HC12 has no `if self._enabled(...)` gate at all. This pins that down:
    # the toggle has zero effect.
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=1,
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='MATH201', faculty_id='F2', room_id=2,
              start_time=time(9, 0), end_time=time(10, 30))
    v = CSPValidator(config={'hc_section_conflict_enabled': 0}).validate([a, b], {})
    assert 'HC12' in _rules(v)


# ── HC_SPEC Faculty specialization (advisory/warning; not in final HC1-17) ─

def test_hc_spec_mismatched_specialization_produces_warning_violation():
    fac = _fac(spec='Accountancy and Finance')
    cls = _gene(subject_code='COMP101', faculty_id='F1')
    v = CSPValidator(config={}).validate([cls], {'F1': fac})
    matches = [x for x in v if x['rule'] == 'HC_SPEC']
    assert matches
    # AUDIT FINDING: HC_SPEC is returned as a violation dict alongside hard
    # (blocking) rules, distinguished only by an internal 'severity': 'warning'
    # key -- it is not filtered out of validate()'s return list.
    assert matches[0].get('severity') == 'warning'


def test_hc_spec_matching_specialization_passes():
    fac = _fac(spec='Information Technology')
    cls = _gene(subject_code='COMP101', faculty_id='F1')
    v = CSPValidator(config={}).validate([cls], {'F1': fac})
    assert 'HC_SPEC' not in _rules(v)


def test_hc_spec_blank_specialization_is_never_restricted():
    fac = _fac(spec='')
    cls = _gene(subject_code='COMP101', faculty_id='F1')
    v = CSPValidator(config={}).validate([cls], {'F1': fac})
    assert 'HC_SPEC' not in _rules(v)


# ── HC13 Laboratory Room Requirement (was HC_LAB) ───────────────────────────

def test_hc13_lab_subject_without_any_lab_room_session_violates():
    cls = _gene(subject_code='IT101', room_id=1, room_type='Lecture', lab_hours=3)
    v = CSPValidator(config={}).validate([cls], {})
    assert 'HC13' in _rules(v)


def test_hc13_lab_subject_with_a_lab_room_session_passes():
    cls = _gene(subject_code='IT101', room_id=1, room_type='Laboratory', lab_hours=3)
    v = CSPValidator(config={}).validate([cls], {})
    assert 'HC13' not in _rules(v)


def test_hc13_lab_requirement_deferred_when_room_is_tba():
    cls = _gene(subject_code='IT101', room_id=None, room_type='Lecture', lab_hours=3)
    v = CSPValidator(config={}).validate([cls], {})
    assert 'HC13' not in _rules(v)


# ── HC16 Merged-Class Validity (decision primitive, no standalone violation) ─

def test_hc16_valid_merge_same_subject_nstp_different_faculty():
    csp = CSPValidator(config={})
    a = _gene(subject_code='NSTP101', faculty_id='F1', room_id=1,
              course='BSIT', section_name='A')
    b = _gene(subject_code='NSTP101', faculty_id='F2', room_id=1,
              course='BSIT', section_name='B')
    assert csp.is_valid_merge(a, b) is True


def test_hc16_invalid_merge_different_subjects():
    csp = CSPValidator(config={})
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=1)
    b = _gene(subject_code='IT102', faculty_id='F1', room_id=1)
    assert csp.is_valid_merge(a, b) is False


def test_hc16_non_nstp_merge_requires_same_faculty():
    csp = CSPValidator(config={'hc_merge_scope': 'all_subjects'})
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=1, section_name='A')
    b = _gene(subject_code='IT101', faculty_id='F2', room_id=1, section_name='B')
    assert csp.is_valid_merge(a, b) is False   # different faculty, non-NSTP -> invalid
    c = _gene(subject_code='IT101', faculty_id='F1', room_id=1, section_name='C')
    assert csp.is_valid_merge(a, c) is True    # same faculty, in scope -> valid


def test_hc16_merge_disabled_makes_every_merge_invalid():
    csp = CSPValidator(config={'hc_merge_enabled': 0})
    a = _gene(subject_code='NSTP101', faculty_id='F1', room_id=1)
    b = _gene(subject_code='NSTP101', faculty_id='F2', room_id=1)
    assert csp.is_valid_merge(a, b) is False


def test_hc16_nstp_faculty_sharing_exemption_is_separate_from_merge_policy():
    # Documents the deliberate split: the NSTP faculty-overlap exemption
    # (HC10's own concern) is unconditional and does NOT require
    # hc_merge_enabled, unlike is_valid_merge (HC16/HC11's concern).
    csp = CSPValidator(config={'hc_merge_enabled': 0})
    a = _gene(subject_code='NSTP101', faculty_id='F1')
    b = _gene(subject_code='OU201', faculty_id='F1')
    assert csp._nstp_shared_faculty_exempt(a, b) is True
    assert csp.is_valid_merge(a, b) is False  # different subjects -> never a valid merge


# ── HC17 Merged-Class Faculty Load (folded into HC9's counting) ────────────

def test_hc17_valid_merged_session_counts_load_once():
    fac = {'F1': _fac(status='Permanent', regularload=1.5, teachingsubstitution=0)}
    a = _gene(subject_code='NSTP101', faculty_id='F1', room_id=1,
              course='BSIT', section_name='A',
              start_time=time(10, 30), end_time=time(12, 0), duration_hrs=1.5)
    b = _gene(subject_code='NSTP101', faculty_id='F1', room_id=1,
              course='BSIT', section_name='B',
              start_time=time(10, 30), end_time=time(12, 0), duration_hrs=1.5)
    v = CSPValidator(config={}).validate([a, b], fac)
    assert 'HC9' not in _rules(v)   # 1.5h counted once, within the 1.5h cap


def test_hc17_invalid_merge_attempt_still_double_counts_load():
    fac = {'F1': _fac(status='Permanent', regularload=1.5, teachingsubstitution=0)}
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=2,
              course='BSIT', section_name='A',
              start_time=time(10, 30), end_time=time(12, 0), duration_hrs=1.5)
    b = _gene(subject_code='IT102', faculty_id='F1', room_id=3,
              course='BSIT', section_name='B',
              start_time=time(10, 30), end_time=time(12, 0), duration_hrs=1.5)
    v = CSPValidator(config={}).validate([a, b], fac)
    assert 'HC9' in _rules(v)   # not a valid merge (different subjects) -> 3.0h counted, exceeds 1.5h cap
