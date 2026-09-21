"""
Unit tests for the partial-generation building blocks in IntelligentScheduler
(architecture spec section 6/9): _strip_component and _find_incomplete_genes.
Pure gene-dict manipulation — IntelligentScheduler() itself tolerates a missing
database (falls back to an empty config), so no DB is required here either.
"""
from datetime import time

from scheduler import IntelligentScheduler


def _full_gene():
    return {
        "subject_code": "IT101", "class_type": "Lecture",
        "faculty_id": "E001", "instructor": "Dela Cruz, J.",
        "room_id": 1, "room": "LQ101", "room_type": "Lecture",
        "day": "Monday", "days_list": ["Monday"],
        "start_time": time(7, 30), "end_time": time(9, 0),
        "time": "7:30 AM – 9:00 AM", "days": "MON",
    }


def test_strip_component_faculty_clears_only_faculty_fields():
    sch = IntelligentScheduler()
    g = _full_gene()
    sch._strip_component(g, "faculty", "HC10: double-booked")
    assert g["faculty_id"] is None and g["instructor"] is None
    # Room and schedule untouched
    assert g["room_id"] == 1 and g["start_time"] == time(7, 30)
    assert g["incomplete"] is True
    assert g["incomplete_reason"] == ["HC10: double-booked"]


def test_strip_component_room_clears_only_room_fields():
    sch = IntelligentScheduler()
    g = _full_gene()
    sch._strip_component(g, "room", "HC9: room conflict")
    assert g["room_id"] is None and g["room"] is None and g["room_type"] == ""
    assert g["faculty_id"] == "E001"  # untouched


def test_strip_component_schedule_clears_only_schedule_fields():
    sch = IntelligentScheduler()
    g = _full_gene()
    sch._strip_component(g, "schedule", "HC5: off grid")
    assert g["start_time"] is None and g["end_time"] is None
    assert g["days_list"] == [] and g["day"] is None
    assert g["time"] == "" and g["days"] == ""
    assert g["room_id"] == 1  # untouched


def test_strip_component_accumulates_distinct_reasons_without_duplicating():
    sch = IntelligentScheduler()
    g = _full_gene()
    sch._strip_component(g, "faculty", "reason A")
    sch._strip_component(g, "faculty", "reason A")  # duplicate, must not repeat
    sch._strip_component(g, "faculty", "reason B")
    assert g["incomplete_reason"] == ["reason A", "reason B"]


def test_find_incomplete_genes_detects_each_missing_component():
    sch = IntelligentScheduler()
    complete = _full_gene()
    no_faculty = _full_gene(); no_faculty["faculty_id"] = None
    no_room = _full_gene(); no_room["room_id"] = None
    no_schedule = _full_gene(); no_schedule["start_time"] = None; no_schedule["day"] = None
    no_schedule["days_list"] = []

    result = dict((g["subject_code"] + str(i), missing)
                  for i, (g, missing) in enumerate(
                      sch._find_incomplete_genes([complete, no_faculty, no_room, no_schedule])))
    missing_lists = [m for _, m in sch._find_incomplete_genes(
        [complete, no_faculty, no_room, no_schedule])]
    assert missing_lists == [["faculty"], ["room"], ["schedule"]]


def test_find_incomplete_genes_empty_for_fully_complete_schedule():
    sch = IntelligentScheduler()
    assert sch._find_incomplete_genes([_full_gene(), _full_gene()]) == []
