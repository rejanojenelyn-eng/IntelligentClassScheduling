from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'app.py').read_text(encoding='utf-8')
JS = (ROOT / 'static/js/ACAD HEAD/manualEditor.acad2.js').read_text(encoding='utf-8')


def test_manual_validation_uses_constraint_service():
    assert 'ConstraintService.with_current_policy().validate_schedule' in APP
    assert "'warnings': warn_viols" in APP
    assert "'has_violations': bool(violations)" in APP


def test_manual_specialization_comes_from_scheduler_authority():
    assert '_spec_matches_subject' in APP
    assert '"spec_match":     _spec_matches_subject' in APP
    assert "'spec_match': (_spec_matches_subject" in APP
    assert 'required_specialization' in APP


def test_sc9_is_not_a_manual_save_blocker():
    assert 'SC9 specialization is intentionally NOT a pre-save blocking gate' in JS
    assert "showValidationModal('Specialization Mismatch'" not in JS
    assert 'SC9 — Specialization Advisory' in JS


def test_dss_spec_fallback_uses_backend_match_flag():
    assert 'facRec = facOth.filter(f => f.spec_match === true);' in JS
    assert 'facOth = facOth.filter(f => f.spec_match !== true);' in JS
