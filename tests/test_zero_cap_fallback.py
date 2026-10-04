"""
Phase B checkpoint 2: regression tests for the zero-cap fallback bug.

Before this fix, `et.get('regularload') or 99` (and the equivalent
`et.get('parttimeload') or 99`) treated an explicitly configured cap of 0
the same as "not configured", silently replacing a genuine zero-allowance
with the generous 99-hour fallback. scheduler._cap_or_default fixes this by
checking for None specifically, not falsiness.
"""
from datetime import time

from scheduler import CSPValidator, _cap_or_default


def _gene(**over):
    g = {
        'subject_code': 'IT101', 'faculty_id': 'F1', 'room_id': 1,
        'day': 'Tuesday', 'days_list': ['Tuesday'],
        'start_time': time(10, 30), 'end_time': time(12, 0),
        'duration_hrs': 1.5,
    }
    g.update(over)
    return g


def test_cap_or_default_preserves_an_explicit_zero():
    assert _cap_or_default(0, 99) == 0


def test_cap_or_default_falls_back_only_when_none():
    assert _cap_or_default(None, 99) == 99


def test_cap_or_default_passes_through_any_other_configured_value():
    assert _cap_or_default(5, 99) == 5


def test_hc9_regularload_explicit_zero_rejects_any_regular_hours():
    fac = {'F1': {'employeestatus': 'Permanent',
                  'employeetype': {'regularload': 0, 'parttimeload': 5,
                                    'teachingsubstitution': 0}}}
    v = CSPValidator(config={'hc_time_blocks_enabled': 0}).validate([_gene()], fac)
    assert 'HC9' in {x['rule'] for x in v}


def test_hc9_regularload_missing_falls_back_to_generous_default():
    fac = {'F1': {'employeestatus': 'Permanent',
                  'employeetype': {'parttimeload': 5, 'teachingsubstitution': 0}}}
    v = CSPValidator(config={'hc_time_blocks_enabled': 0}).validate([_gene()], fac)
    assert 'HC9' not in {x['rule'] for x in v}


def test_hc9_parttimeload_explicit_zero_rejects_any_pt_hours():
    fac = {'F1': {'employeestatus': 'Permanent',
                  'employeetype': {'regularload': 5, 'parttimeload': 0,
                                    'teachingsubstitution': 0}}}
    # Evening slot -> classified PT
    cls = _gene(start_time=time(18, 0), end_time=time(19, 30))
    v = CSPValidator(config={'hc_time_blocks_enabled': 0}).validate([cls], fac)
    assert 'HC9' in {x['rule'] for x in v}


def test_hc9_parttimeload_missing_falls_back_to_generous_default():
    fac = {'F1': {'employeestatus': 'Permanent',
                  'employeetype': {'regularload': 5, 'teachingsubstitution': 0}}}
    cls = _gene(start_time=time(18, 0), end_time=time(19, 30))
    v = CSPValidator(config={'hc_time_blocks_enabled': 0}).validate([cls], fac)
    assert 'HC9' not in {x['rule'] for x in v}
