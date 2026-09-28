from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'templates/admin/settings_admin.html').read_text(encoding='utf-8')


def test_settings_has_four_policy_navigation_targets():
    for anchor in ('settings-faculty-policy','settings-designee-policy','settings-constraints','settings-merge-calendar'):
        assert f'#{anchor}' in HTML
        assert f'id="{anchor}"' in HTML


def test_schedule_constraints_use_final_rule_labels():
    for rule in ('HC5','HC6','HC7','HC10','HC11','HC12','HC13','HC14','HC15','HC16','HC17','SC9'):
        assert rule in HTML


def test_specialization_is_presented_as_soft_sc9():
    assert 'SC9' in HTML
    assert 'Faculty Specialization Match' in HTML
    assert 'SOFT PREFERENCE' in HTML
    assert 'does not invalidate an otherwise feasible schedule' in HTML


def test_core_conflicts_are_presented_as_system_enforced():
    assert 'Core Conflict Validation' in HTML
    assert 'HC10' in HTML and 'Faculty Schedule Conflict' in HTML
    assert 'HC11' in HTML and 'Room Schedule Conflict' in HTML
    assert 'HC12' in HTML and 'Section Schedule Conflict' in HTML
    assert 'HC15' in HTML and 'Cross-Schedule Conflict Validation' in HTML
    assert 'SYSTEM ENFORCED' in HTML
