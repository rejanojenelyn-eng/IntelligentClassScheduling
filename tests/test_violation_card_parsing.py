"""The publish/save violation notice is shown as a readable card (type chip, subject,
problem, info, how-to-fix) instead of one run-on paragraph."""
import json, shutil, subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).parents[1]
JS = (ROOT / "static/js/ACAD HEAD/manualEditor.acad2.js").read_text(encoding="utf-8")


def test_publish_violations_use_the_card_view():
    assert "await _showFirstViolation(data.violations, {" in JS
    assert "Cannot publish — constraint violation(s)" not in JS


def test_plain_notices_keep_line_breaks():
    assert "_vm.style.whiteSpace = 'pre-line';" in JS


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_day_pairing_message_is_split_into_readable_parts():
    parser = JS[JS.index("function _violEsc"):JS.index("async function _showFirstViolation")]
    msg = ("\"COMP 015\": The day combination ['Monday', 'Tuesday'] is not a valid pairing. "
           "Allowed pairings are: Monday-Thursday / Tuesday-Friday / Wednesday-Saturday. "
           "Please adjust the schedule days to match one of the configured pairs (Mon-Thu, Tue-Fri, or Wed-Sat).")
    script = parser + f"\nconsole.log(JSON.stringify(_violParse({{type:'Conflict: Day Pairing', detail:{json.dumps(msg)}}})));"
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out["type"] == "Day Pairing"
    assert out["subject"] == "COMP 015"
    assert out["what"] == "The day combination Monday + Tuesday is not a valid pairing."
    assert out["info"].startswith("Allowed pairings are:")
    assert out["fix"].startswith("Adjust the schedule days")
