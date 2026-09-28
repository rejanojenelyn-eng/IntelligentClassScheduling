"""Phase 3E regression guards for the frozen constraint/evaluation boundary."""
import inspect
import ast
from pathlib import Path


def _app_source():
    return (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8')


def test_evaluation_routes_hard_validation_through_constraint_service():
    src = _app_source()
    start = src.index('def _compute_schedule_evaluation(')
    end = src.index('\ndef _check_cross_schedule_conflicts', start)
    fn = src[start:end]
    assert 'ConstraintService.with_current_policy().validate_schedule' in fn


def test_room_type_evaluation_uses_final_hc13_not_duplicate_lab_rule():
    src = _app_source()
    start = src.index('def _compute_schedule_evaluation(')
    end = src.index('\ndef _check_cross_schedule_conflicts', start)
    fn = src[start:end]
    # Room type suitability is derived from the final HC13 (and HC14) output via
    # the shared criterion->rule map, never from a duplicate lab-room rule.
    assert "_EVAL_CRITERION_RULES['roomTypeSuitability']" in fn
    assert "has_lab_room = any" not in fn
    import app
    assert 'HC13' in app._EVAL_CRITERION_RULES['roomTypeSuitability']


def test_evaluation_exposes_frozen_rule_traceability():
    # Criterion -> rule traceability. Every violation-emitting hard rule is
    # reflected by some criterion (HC6 time grid, HC1-HC4 teaching windows,
    # HC8 night limit and HC14 were previously in none, so a CSP failure could
    # leave every criterion at 100%).
    import app
    m = app._EVAL_CRITERION_RULES
    assert m['facultyConflictFree'] == ('HC10',)
    assert m['roomConflictFree'] == ('HC11',)
    assert m['sectionConflictFree'] == ('HC12',)
    assert m['roomTypeSuitability'] == ('HC13', 'HC14')
    assert m['subjectDayCompliance'] == ('HC5',)
    assert m['schedulePairingDistribution'] == ('HC6', 'HC7')
    assert m['facultyLoadCompliance'] == ('HC1', 'HC2', 'HC3', 'HC4', 'HC8', 'HC9', 'HC17')
    src = _app_source()
    assert "historicalFacultyMatch=['SC8']" in src and "facultyAssignmentSuitability=['SC9']" in src


def test_evaluation_explicitly_separates_ga_fitness():
    src = _app_source()
    assert "'gaFitnessSeparate': True" in src
    assert "'evaluationModel': '60/30/10'" in src


def test_app_remains_valid_python():
    ast.parse(_app_source())
