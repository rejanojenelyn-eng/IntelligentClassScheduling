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
    assert "v.get('rule') == 'HC13'" in fn
    assert "has_lab_room = any" not in fn


def test_evaluation_exposes_frozen_rule_traceability():
    src = _app_source()
    for token in ["'facultyConflictFree': ['HC10']", "'roomConflictFree': ['HC11']",
                  "'sectionConflictFree': ['HC12']", "'roomTypeSuitability': ['HC13']",
                  "'subjectDayCompliance': ['HC5']", "'schedulePairingDistribution': ['HC7']",
                  "'facultyLoadCompliance': ['HC9', 'HC17']", "'historicalFacultyMatch': ['SC8']"]:
        assert token in src


def test_evaluation_explicitly_separates_ga_fitness():
    src = _app_source()
    assert "'gaFitnessSeparate': True" in src
    assert "'evaluationModel': '60/30/10'" in src


def test_app_remains_valid_python():
    ast.parse(_app_source())
