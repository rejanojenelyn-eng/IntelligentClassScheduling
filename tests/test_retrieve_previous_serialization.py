"""
Regression tests for the "Object of type time is not JSON serializable" live
bug in /api/schedule/retrieve-previous.

Root cause: the historical-fallback branch of api_retrieve_previous_schedule
put the raw datetime.time objects returned by parse_historical_day_time_verbose
directly into schedule_data (as 'start_time'/'end_time'), and that same list is
later handed straight to jsonify(). Flask's default JSON encoder cannot
serialize datetime.time, so the request raised inside the try block and fell
into the generic `except Exception as e: return jsonify({'success': False,
'error': str(e)})` handler — which the frontend then displayed as "No Previous
Schedule Found" (with the raw Python exception text as the body), even though
a previous schedule WAS found. That conflation is the second bug fixed here.

These tests run entirely against app._json_safe / app._display_time_range /
app._safe_json_response with hand-built payloads shaped like the real
response, since the live DB (Postgres) is not reachable from this sandboxed
environment — see conftest.py's DB_AVAILABLE/requires_db split. They do not
need a database because they test the serialization boundary itself, not the
SQL that feeds it.
"""
import json
from datetime import time, date, datetime
from decimal import Decimal

import app as app_module


def _extract_json(response_tuple_or_response):
    """app._safe_json_response returns (flask.Response, status) — pull the
    parsed body out of either that or a bare jsonify() Response."""
    resp = response_tuple_or_response[0] if isinstance(response_tuple_or_response, tuple) else response_tuple_or_response
    return json.loads(resp.get_data(as_text=True))


# ── 1: datetime.time in a retrieved schedule row ──────────────────────────

def test_json_safe_converts_time_in_a_retrieved_row():
    row = {"subject_code": "IT101", "start_time": time(15, 0), "end_time": time(18, 0)}
    safe = app_module._json_safe(row)
    assert safe["start_time"] == "15:00"
    assert safe["end_time"] == "18:00"
    json.dumps(safe)  # must not raise


# ── 2: datetime.time inside a violation dict ──────────────────────────────

def test_json_safe_converts_time_inside_a_violation():
    payload = {
        "violations": [
            {"rule": "HC9", "start_time": time(9, 0), "end_time": time(10, 30),
             "normalized_days": ["Monday", "Wednesday"]},
        ]
    }
    safe = app_module._json_safe(payload)
    v = safe["violations"][0]
    assert v["start_time"] == "09:00"
    assert v["end_time"] == "10:30"
    assert v["normalized_days"] == ["Monday", "Wednesday"]  # list unaffected
    json.dumps(safe)


# ── 3: datetime.time inside evaluation metadata ───────────────────────────

def test_json_safe_converts_time_inside_evaluation_metadata():
    payload = {
        "evaluation": {
            "overallScore": 89,
            "violationsBySubject": {
                "IT101": [{"rule": "HC10", "start_time": time(13, 30), "end_time": time(15, 0)}],
            },
        }
    }
    safe = app_module._json_safe(payload)
    nested = safe["evaluation"]["violationsBySubject"]["IT101"][0]
    assert nested["start_time"] == "13:30"
    assert nested["end_time"] == "15:00"
    json.dumps(safe)


# ── 4: 3:00 PM-6:00 PM -> start_time "15:00", end_time "18:00", display ──

def test_full_pipeline_3pm_to_6pm():
    from scheduler import parse_historical_day_time_verbose
    days, start_t, end_t, status, reason = parse_historical_day_time_verbose("MW", "3:00 PM-6:00 PM")
    assert status == "resolved" and reason is None
    display = app_module._display_time_range(start_t, end_t)
    assert display == "3:00 PM - 6:00 PM"

    row = {"start_time": start_t, "end_time": end_t, "time": display}
    safe = app_module._json_safe(row)
    assert safe["start_time"] == "15:00"
    assert safe["end_time"] == "18:00"
    assert safe["time"] == "3:00 PM - 6:00 PM"


def test_display_time_range_has_no_leading_zero_on_the_hour():
    assert app_module._display_time_range(time(9, 0), time(12, 0)) == "9:00 AM - 12:00 PM"
    assert app_module._display_time_range(time(0, 0), time(3, 0)) == "12:00 AM - 3:00 AM"
    assert app_module._display_time_range(time(12, 0), time(15, 0)) == "12:00 PM - 3:00 PM"


# ── Other non-JSON-native types the recursive serializer must handle ──────

def test_json_safe_handles_date_datetime_decimal_set_tuple():
    payload = {
        "a_date": date(2026, 9, 22),
        "a_datetime": datetime(2026, 9, 22, 14, 30),
        "a_decimal": Decimal("3.50"),
        "a_set": {"Monday", "Wednesday"},
        "a_tuple": (time(7, 30), time(9, 0)),
    }
    safe = app_module._json_safe(payload)
    assert safe["a_date"] == "2026-09-22"
    assert safe["a_datetime"] == "2026-09-22T14:30:00"
    assert safe["a_decimal"] == 3.5 and isinstance(safe["a_decimal"], float)
    assert isinstance(safe["a_set"], list) and set(safe["a_set"]) == {"Monday", "Wednesday"}
    assert safe["a_tuple"] == ["07:30", "09:00"]
    json.dumps(safe)


def test_json_safe_never_uses_str_for_known_types_datetime_checked_before_date():
    # datetime is a subclass of date -- must not be coerced to a bare date string.
    dt = datetime(2026, 9, 22, 8, 0)
    safe = app_module._json_safe({"x": dt})
    assert safe["x"] == "2026-09-22T08:00:00"  # full ISO datetime, not just the date part


# ── 5/7: NO_PREVIOUS_SCHEDULE vs SERIALIZATION_ERROR must never be confused ─

def test_safe_json_response_serializes_a_realistic_success_payload():
    payload = {
        "success": True,
        "result_status": "PARTIAL_VALID",
        "schedule_data": [
            {"subject_code": "IT101", "start_time": time(15, 0), "end_time": time(18, 0),
             "days_list": ["Monday", "Wednesday"]},
        ],
        "violations": [
            {"rule": "HC9", "start_time": time(9, 0), "end_time": time(10, 30)},
        ],
        "evaluation": {
            "overallScore": 89,
            "violationsBySubject": {"IT101": [{"rule": "HC9", "start_time": time(9, 0)}]},
        },
        "exact_reuse": {"requested": True, "succeeded": False, "source": "historical_fallback"},
    }
    with app_module.app.test_request_context():
        resp, status = app_module._safe_json_response(payload)
    assert status == 200
    body = json.loads(resp.get_data(as_text=True))
    assert body["success"] is True
    assert body["schedule_data"][0]["start_time"] == "15:00"
    assert body["schedule_data"][0]["end_time"] == "18:00"
    assert body["violations"][0]["start_time"] == "09:00"
    # This is the literal assertion that the original bug is fixed: the whole
    # response round-trips through json.dumps without raising.
    json.dumps(body)


def test_serialization_failure_returns_serialization_error_not_not_found(monkeypatch):
    # Force _json_safe to blow up to simulate a genuinely unanticipated type
    # slipping through, and confirm the failure is reported as
    # RETRIEVE_PREVIOUS_SERIALIZATION_ERROR — never as "no previous schedule",
    # since the whole point is that data WAS found.
    def _boom(value):
        raise TypeError("simulated: Object of type time is not JSON serializable")
    monkeypatch.setattr(app_module, "_json_safe", _boom)

    with app_module.app.test_request_context():
        resp, status = app_module._safe_json_response({"success": True, "schedule_data": [{"x": time(9, 0)}]})
    assert status == 500
    body = json.loads(resp.get_data(as_text=True))
    assert body["success"] is False
    assert body["error_code"] == "RETRIEVE_PREVIOUS_SERIALIZATION_ERROR"
    assert body["error_code"] != "NO_PREVIOUS_SCHEDULE"
    # The primary user-facing message must not be a raw Python exception string.
    assert "TypeError" not in body["message"]
    assert "simulated" not in body["message"]


def test_real_empty_query_result_reports_no_previous_schedule_error_code():
    # This is the actual shape api_retrieve_previous_schedule returns for its
    # three "genuinely nothing found" cases (no prior AY / no semester row / no
    # hist_rows) — verified directly against the source rather than requiring
    # a live DB, since all three are simple, deterministic dict literals.
    import inspect
    src = inspect.getsource(app_module.api_retrieve_previous_schedule)
    assert src.count("'error_code': 'NO_PREVIOUS_SCHEDULE'") == 3, (
        "expected all three 'not found' returns to carry error_code NO_PREVIOUS_SCHEDULE"
    )


# ── frontend error_code handling (static review — no JS test runner in this
#    project; this asserts the exact branching contract the JS relies on) ──

def test_frontend_branches_on_error_code_not_on_plain_success_false():
    import pathlib
    js_path = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "ACAD HEAD" / "scheduleGeneration.acad.js"
    js = js_path.read_text(encoding="utf-8")
    assert "data.error_code || 'NO_PREVIOUS_SCHEDULE'" in js
    assert "No Previous Schedule Available" in js
    assert "Unable to Load Previous Schedule" in js
    assert "Generate New Schedule" in js
    assert "Try Again" in js
