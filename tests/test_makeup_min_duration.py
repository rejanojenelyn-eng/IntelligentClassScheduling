"""Make-up classes may be 30 minutes up to one regular session; Schedule Adjustments
still require the full session length."""
from pathlib import Path
import makeup_requests as m

ROOT = Path(__file__).parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")


def test_makeup_duration_rule():
    assert m.makeup_duration_error(0.25, 5, 'COMP 018') == "A make-up class must be at least 30 minutes long."
    for ok in (0.5, 2, 5):
        assert m.makeup_duration_error(ok, 5, 'COMP 018') is None
    assert "at most 5h" in m.makeup_duration_error(5.5, 5, 'COMP 018')
    assert m.makeup_duration_error(8, 0, 'X') is None          # no hours on file: no upper cap


def test_live_check_and_submit_use_the_makeup_rule_only_for_makeups():
    s = APP.index("def api_faculty_check_request_conflicts")
    chk = APP[s:APP.index("@app.route", s + 40)]
    assert "_is_makeup_check" in chk and "makeup_duration_error as _mu_rule" in chk
    s = APP.index("def api_faculty_submit_request")
    sub = APP[s:APP.index("@app.route", s + 40)]
    assert "if req_type == 'makeup':" in sub and "makeup_duration_error as _mu_rule" in sub
    assert "requires \"\n" in sub or "per session." in sub        # adjustments keep the full-session rule
