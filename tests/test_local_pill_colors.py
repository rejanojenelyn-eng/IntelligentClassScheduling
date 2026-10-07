from pathlib import Path
ROOT = Path(__file__).parents[1]
JS = (ROOT / "static/js/ACAD HEAD/manualEditor.acad2.js").read_text(encoding="utf-8")


def test_local_palette_only_for_local_arrangement_sessions():
    s = JS.index("function getSubjectColor(code, isLocalSource = false)")
    assert "if (_IS_LOCAL_MODE && isLocalSource)" in JS[s:s + 600]


def test_official_mirror_is_not_local_but_new_local_slices_are():
    s = JS.index("function _isLocalSourcePending(c)")
    assert "_IS_LOCAL_MODE && !(c.fromExisting && c.versionid)" in JS[s:s + 200]
    assert "String(s.schedule_source || '').toLowerCase() === 'local'" in JS


def test_both_views_pass_the_source_to_the_color():
    assert "getSubjectColor(sess.subjectcode, sess.localSource ?? _isLocalSourceRow(sess))" in JS  # program view
    assert "getSubjectColor(sess.subjectcode, sess.localSource)" in JS                             # room view
    assert "rep.localSource = group.some(" in JS
