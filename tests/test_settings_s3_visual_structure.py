from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'templates/admin/settings_admin.html').read_text(encoding='utf-8')
CSS = (ROOT / 'static/css/settings.css').read_text(encoding='utf-8')
JS = (ROOT / 'static/js/ADMIN/settings.admin.js').read_text(encoding='utf-8')


def test_s3_has_four_step_policy_wizard():
    for step in range(1, 5):
        assert f'data-settings-step="{step}"' in HTML
        assert f'data-policy-step="{step}"' in HTML


def test_s3_has_previous_next_and_progress_navigation():
    assert 'settingsPolicyPrev' in HTML
    assert 'settingsPolicyNext' in HTML
    assert 'settingsPolicyCounter' in HTML
    assert 'changeSettingsPolicyStep' in JS


def test_s3_is_ui_only_and_preserves_final_policy_labels():
    assert 'HC10' in HTML and 'HC11' in HTML and 'HC12' in HTML and 'HC15' in HTML
    assert 'SC9' in HTML and 'SOFT PREFERENCE' in HTML
    assert 'showSettingsPolicyStep' in JS


def test_s3_has_responsive_visual_rules():
    assert '.settings-policy-stepper' in CSS
    assert '.settings-policy-footer' in CSS
    assert '@media(max-width:620px)' in CSS
