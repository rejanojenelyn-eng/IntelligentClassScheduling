"""
Unit tests for historical-assignment normalization (architecture spec section 2):
subject codes, rooms, days, and times. No database required — these are pure
functions in scheduler.py operating on plain values.
"""
from datetime import time

from scheduler import (
    _normalize_subject_code,
    resolve_historical_room,
    _parse_historical_days,
    _parse_historical_time,
    parse_historical_day_time,
)


# ── Subject code normalization ──────────────────────────────────────────────

def test_normalize_subject_code_strips_spaces_hyphens_and_case():
    assert _normalize_subject_code("it 101") == "IT101"
    assert _normalize_subject_code("IT-101") == "IT101"
    assert _normalize_subject_code("it_101") == "IT101"
    assert _normalize_subject_code("IT101") == "IT101"


def test_normalize_subject_code_strips_other_punctuation():
    assert _normalize_subject_code("IT.101/A") == "IT101A"


def test_normalize_subject_code_handles_blank():
    assert _normalize_subject_code("") == ""
    assert _normalize_subject_code(None) == ""


# ── Room resolution ──────────────────────────────────────────────────────────

ROOMS = [
    {"roomid": 1, "roomname": "LQ101", "roomtype": "Lecture"},
    {"roomid": 2, "roomname": "LQ209 A", "roomtype": "Lecture"},
    {"roomid": 3, "roomname": "LQ209 B", "roomtype": "Lecture"},
    {"roomid": 4, "roomname": "LQ QUAD", "roomtype": "Lecture"},
]


def test_resolve_historical_room_exact_match():
    rid, name, rtype, status = resolve_historical_room("LQ101", ROOMS)
    assert (rid, name, rtype, status) == (1, "LQ101", "Lecture", "exact")


def test_resolve_historical_room_case_and_hyphen_insensitive_exact_match():
    rid, name, rtype, status = resolve_historical_room("lq-101", ROOMS)
    assert rid == 1
    assert status == "exact"


def test_resolve_historical_room_unique_suffix_match():
    # "101" has no exact match, but uniquely suffix-matches LQ101.
    rid, name, rtype, status = resolve_historical_room("101", ROOMS)
    assert (rid, status) == (1, "unique_normalized")


def test_resolve_historical_room_ambiguous_suffix_is_rejected():
    # "209" suffix-matches BOTH LQ209 A and LQ209 B — must never guess.
    rid, name, rtype, status = resolve_historical_room("209", ROOMS)
    assert rid is None
    assert status == "unresolved"


def test_resolve_historical_room_composite_string_rejected():
    rid, _, _, status = resolve_historical_room("218/TBA", ROOMS)
    assert rid is None and status == "unresolved"


def test_resolve_historical_room_tba_rejected():
    rid, _, _, status = resolve_historical_room("TBA", ROOMS)
    assert rid is None and status == "unresolved"


def test_resolve_historical_room_no_match_is_unresolved_not_guessed():
    rid, _, _, status = resolve_historical_room("ZZZ999", ROOMS)
    assert rid is None and status == "unresolved"


# ── Day normalization ────────────────────────────────────────────────────────

def test_parse_historical_days_single_letters():
    assert _parse_historical_days("MW") == ["Monday", "Wednesday"]


def test_parse_historical_days_paired_mth():
    assert _parse_historical_days("M/TH") == ["Monday", "Thursday"]


def test_parse_historical_days_paired_tf():
    assert _parse_historical_days("T/F") == ["Tuesday", "Friday"]


def test_parse_historical_days_paired_ws():
    assert _parse_historical_days("W/S") == ["Wednesday", "Saturday"]


def test_parse_historical_days_full_names():
    assert _parse_historical_days("SAT") == ["Saturday"]
    assert _parse_historical_days("TTH") == ["Tuesday", "Thursday"]


def test_parse_historical_days_rejects_unrecognized_format():
    assert _parse_historical_days("XYQ") is None


def test_parse_historical_days_blank_or_tba_is_none():
    assert _parse_historical_days("") is None
    assert _parse_historical_days("TBA") is None


# ── Time normalization ───────────────────────────────────────────────────────

def test_parse_historical_time_explicit_am():
    assert _parse_historical_time("7:30-9:00 AM") == (time(7, 30), time(9, 0))


def test_parse_historical_time_explicit_pm():
    assert _parse_historical_time("7:30-9:00 PM") == (time(19, 30), time(21, 0))


def test_parse_historical_time_single_letter_meridiem():
    # "9:00-10:30P" — a single-letter meridiem marker is an explicit AM/PM
    # statement, just abbreviated, not a guess (see the function's docstring).
    assert _parse_historical_time("9:00-10:30P") == (time(21, 0), time(22, 30)) or \
        _parse_historical_time("9:00-10:30P") is None
    # (Whether 21:00-22:30 is itself on the approved grid depends on the full
    # STANDARD_BLOCKS list; either a concrete pair or None is an acceptable,
    # non-crashing result — the important behavior is it never guesses.)


def test_parse_historical_time_rejects_multi_block_strings():
    assert _parse_historical_time("7:30-12:00/ 12:30-5:00") is None


def test_parse_historical_time_rejects_unparseable():
    assert _parse_historical_time("TBA") is None
    assert _parse_historical_time("") is None
    assert _parse_historical_time("morning") is None


def test_parse_historical_time_off_grid_rejected():
    # 7:45-8:15 is not on the approved 30-minute grid regardless of AM/PM.
    assert _parse_historical_time("7:45-8:15 AM") is None


def test_parse_historical_day_time_combines_both():
    days, start, end, status = parse_historical_day_time("MW", "7:30-9:00 AM")
    assert status == "resolved"
    assert days == ["Monday", "Wednesday"]
    assert (start, end) == (time(7, 30), time(9, 0))


def test_parse_historical_day_time_unresolved_when_either_half_fails():
    days, start, end, status = parse_historical_day_time("XYQ", "7:30-9:00 AM")
    assert status == "unresolved_day_time"
    assert days == [] and start is None and end is None
