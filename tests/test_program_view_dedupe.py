from pathlib import Path
ROOT=Path(__file__).parents[1]
APP=(ROOT/"app.py").read_text(encoding="utf-8")
JS=(ROOT/"static/js/ACAD HEAD/manualEditor.acad2.js").read_text(encoding="utf-8")

def offerings_block():
    s=APP.index("def get_offerings_schedule")
    return APP[s:APP.index("@app.route",s+20)]

def test_offerings_schedule_returns_occurrence_id():
    # Without ss.sessionid, _isDbRowHidden can't match a mirror's `s:<sessionid>` key and
    # the saved row renders next to its local copy in Program View.
    assert "ss.sessionid" in offerings_block()

def test_program_view_dedupes_by_slot_index_not_time_text():
    # API returns "16:30"; local entries hold "04:30 PM" — keys must use the slot index.
    s=JS.index("async function renderProgramTimetable")
    b=JS[s:JS.index("function _renderProgPills",s)]
    assert "`${s.subjectcode}|${s.daydesc}|${_sIdx}`" in b
    assert "`${_le.subject_code}|${_le.day}|${timeStrToSlotIdx(_le.start_time)}`" in b
    assert "|${s.start_time}`" not in b and "|${_le.start_time}`" not in b
