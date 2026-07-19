"""
scheduler.py  —  Intelligent Scheduling Engine
================================================
Architecture
------------
Layer 1  · StandardSlots   – PUP-standard time blocks
Layer 2  · CSPValidator    – hard constraint checker
Layer 3  · IntelligentScheduler – Genetic Algorithm with soft-constraint fitness

Hard constraints (CSP):
  HC1  Full-time faculty regular hours  : Mon–Fri 7:30–16:30
  HC2  Designee regular hours           : Mon–Fri 8:00–17:00
  HC3  Part-time windows
  HC4  Sunday restriction               : only OU / NSTP subjects
  HC5  Standard time slots
  HC6  Day pairing for 1.5-hr sessions of 3+ hr/week subjects
  HC7  Max 2 night PT classes/designee
  HC8  Teaching load limits
  HC9  No room overlap
  HC10 No faculty double-booking

Soft constraints (GA fitness):
  SC1  Preferred daytime slots          (−20)
  SC2  Minimize night classes           (−15)
  SC3  Even day distribution            (−10)
  SC4  Compact schedule                 (−10)
  SC5  Balance PT load                  (−10/unit)
  SC6  Even weekend spread              (−10)
  SC7  No 4+ consecutive teaching hrs   (−30)
"""

import json
import random
import copy
from datetime import time
from collections import defaultdict
from database import get_db_connection, query_db, load_scheduler_config
import faculty_load


# ─────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────

WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
WEEKEND  = ['Saturday', 'Sunday']
ALL_DAYS = WEEKDAYS + WEEKEND

# Default day pairs (used as fallback when DB config is unavailable)
DAY_PAIRS = {
    'MTH': ['Monday', 'Thursday'],
    'TF':  ['Tuesday', 'Friday'],
    'WS':  ['Wednesday', 'Saturday'],
}

SUNDAY_ALLOWED_PREFIXES = ('NSTP', 'OU')


_SUBJ_SPEC_MAP = [
    (
        ['COMP', 'INTE', 'ICTE', 'ITEC', 'ELEC IT', 'ELECT IT'],
        {'Computer and Information Sciences', 'Information Technology',
         'Information and Communication Technology'},
    ),
    (
        ['ARCH', 'ARCHS'],
        {'Architecture, Design and the Built Environment', 'Architecture'},
    ),
    (
        ['CIEN', 'ENSC'],
        {'Engineering', 'Computer Engineering', 'Electronics Engineering'},
    ),
    (
        ['ACCO'],
        {'Accountancy and Finance', 'Accountancy'},
    ),
    (
        ['BUMA', 'HRMA'],
        {'Business Administration', 'Business Management'},
    ),
    # PE / PATHFIT teachers may only teach physical education subjects,
    # not general education academic courses.
    (
        ['PE', 'PHED', 'PATHFIT'],
        {'Physical Education', 'Physical Education and Sports',
         'Health and Physical Education', 'PE and Health',
         'Sports Science', 'Kinesiology'},
    ),
]

# Flat set of every restricted specialization across all groups.
# Faculty whose specialization is in this set are "domain-locked":
# they may ONLY teach subjects whose prefix maps to their own group.
_ALL_RESTRICTED_SPECS: set = {sp for _, specs in _SUBJ_SPEC_MAP for sp in specs}


def _spec_matches_subject(spec_name: str, subject_code: str) -> bool:
    """Return True if the faculty specialization is compatible with the subject code.

    Rules:
    1. If the subject prefix is in _SUBJ_SPEC_MAP, the faculty's specialization
       must belong to that group's valid set.
    2. If the subject is NOT in the map (GEED, MATH, NSTP, etc.) it is
       considered a General-Education / unrestricted subject — but faculty
       whose specialization is domain-locked (in _ALL_RESTRICTED_SPECS) may
       NOT be assigned to it.  This prevents PE teachers from teaching GEED,
       IT teachers from teaching humanities, etc.
    3. Faculty with NO specialization on record are always allowed.
    """
    upper = (subject_code or '').upper()

    # Rule 1: restricted subject — must match the group's valid spec set
    for prefixes, valid_specs in _SUBJ_SPEC_MAP:
        for pfx in prefixes:
            if upper.startswith(pfx):
                return spec_name in valid_specs

    # Rule 2: unrestricted subject — block domain-locked specializations
    if spec_name in _ALL_RESTRICTED_SPECS:
        return False

    # Rule 3: no restriction applies
    return True


def _required_spec_for_subject(subject_code: str) -> str:
    """Return the accepted specialization(s) for a subject code, or '' if unrestricted."""
    upper = (subject_code or '').upper()
    for prefixes, valid_specs in _SUBJ_SPEC_MAP:
        for pfx in prefixes:
            if upper.startswith(pfx):
                return ' / '.join(sorted(valid_specs))
    return 'General Education / Humanities / Social Sciences (any non-domain-locked specialization)'


# ── HC config helpers ──────────────────────────────────────────

_DAY_SORT_IDX = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
def _sort_day_pair(pair):
    """Sort a day pair by canonical week order (Mon→Sun), not alphabetically."""
    return sorted(pair, key=lambda d: _DAY_SORT_IDX.index(d) if d in _DAY_SORT_IDX else 99)

def _parse_day_pairs(raw) -> list:
    """Convert JSON string or list → list of week-order-sorted day-name lists."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return [_sort_day_pair(p) for p in data if len(p) == 2]
    except Exception:
        return [list(p) for p in DAY_PAIRS.values()]

def _parse_time_slots(raw) -> tuple:
    """Convert JSON string → (set of start times, set of end times)."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        starts = {time(s[0], s[1]) for s in data}
        ends   = {time(s[2], s[3]) for s in data}
        return starts, ends
    except Exception:
        return VALID_START_TIMES, VALID_END_TIMES

STANDARD_BLOCKS = [
    # 1.5-hour blocks (90 min) — paired-day sessions
    (time(7,  30), time(9,   0)),
    (time(9,   0), time(10, 30)),
    (time(10, 30), time(12,  0)),
    (time(12,  0), time(13, 30)),
    (time(13, 30), time(15,  0)),
    (time(15,  0), time(16, 30)),
    (time(16, 30), time(18,  0)),   # evening
    (time(18,  0), time(19, 30)),
    (time(19, 30), time(21,  0)),
    # 2-hour blocks (120 min) — for subjects with exactly 2 contact hours
    (time(7,  30), time(9,  30)),   # regular
    (time(9,   0), time(11,  0)),   # regular
    (time(10, 30), time(12, 30)),   # regular
    (time(12,  0), time(14,  0)),   # regular
    (time(13, 30), time(15, 30)),   # regular
    (time(14, 30), time(16, 30)),   # regular — ends exactly at regular limit
    (time(16, 30), time(18, 30)),   # evening PT
    (time(18,  0), time(20,  0)),   # evening PT
    (time(19,  0), time(21,  0)),   # evening PT
    # 3-hour blocks (180 min) — lab sessions and 3-credit single-session lectures
    (time(10, 30), time(13, 30)),
    (time(13, 30), time(16, 30)),
    (time(7,  30), time(10, 30)),
    (time(9,   0), time(12,  0)),
    (time(16, 30), time(19, 30)),   # evening 3-hr PT
    (time(18,  0), time(21,  0)),   # evening 3-hr PT
]

VALID_START_TIMES = {s for (s, _) in STANDARD_BLOCKS}
VALID_END_TIMES   = {e for (_, e) in STANDARD_BLOCKS}

# All (day, start, end) combinations that the scheduler may ever assign.
_ALL_STANDARD_SLOTS: list = [
    (day, s, e)
    for day in ALL_DAYS
    for (s, e) in STANDARD_BLOCKS
]
_TOTAL_STANDARD_CAPACITY = len(_ALL_STANDARD_SLOTS)


def _exclude_fully_booked_rooms(rooms: list, published_room_slots: dict) -> list:
    """Return a filtered copy of `rooms` excluding any room with no free standard slots.

    A room is considered fully booked for the semester when every (day, start, end)
    combination from STANDARD_BLOCKS × ALL_DAYS is already occupied in
    `published_room_slots`.  In practice this only removes rooms that are truly
    exhausted (e.g. a dedicated room used around the clock).  Rooms with even one
    free slot remain in the candidate list.
    """
    if not published_room_slots:
        return rooms

    # Build per-room set of occupied (day, start, end) tuples
    occupied: dict = defaultdict(set)
    for (room_id, day), slots in published_room_slots.items():
        for (s, e) in slots:
            occupied[room_id].add((day, s, e))

    available = []
    for r in rooms:
        rid = r.get('roomid') or r.get('room_id')
        occ = occupied.get(rid, set())
        if len(occ) < _TOTAL_STANDARD_CAPACITY:
            available.append(r)

    removed = len(rooms) - len(available)
    if removed:
        print(f'[SCHED] Pre-filtered {removed} fully-booked room(s) from candidate list.')
    return available if available else rooms   # never leave the list empty


def _faculty_is_at_max_load(faculty: dict, committed_hours: float) -> bool:
    """Return True if a faculty member has already reached their maximum teaching load
    (load is measured in actual/nominal HOURS now, not credit units — see faculty_load.py)."""
    if not faculty:
        return False
    et       = faculty.get('employeetype', {})
    max_reg  = et.get('regularload')  or 99
    max_pt   = et.get('parttimeload') or 0
    ts_sub   = et.get('teachingsubstitution') or 0
    max_total = max_reg + max_pt + ts_sub
    return committed_hours >= max_total


def minutes(t_obj):
    return t_obj.hour * 60 + t_obj.minute


def duration_hours(start: time, end: time) -> float:
    return (minutes(end) - minutes(start)) / 60.0


def format_time_12h(t_obj: time) -> str:
    return t_obj.strftime('%I:%M %p')


def is_night_time(t_obj: time) -> bool:
    return t_obj >= time(18, 0)


def is_daytime_block(start: time, end: time) -> bool:
    return end <= time(16, 30)


def get_blocks_for_hours(target_hours: float, is_lab: bool = False) -> list:
    """
    Return STANDARD_BLOCKS matching the target session duration exactly.
    Lab parts prefer 3-hour blocks. Never silently round to a different duration.
    """
    if is_lab:
        blks = [(s, e) for (s, e) in STANDARD_BLOCKS if abs(duration_hours(s, e) - 3) < 0.1]
        if blks:
            return blks

    # Exact match first (±6 min tolerance for float precision)
    blks = [(s, e) for (s, e) in STANDARD_BLOCKS
            if abs(duration_hours(s, e) - target_hours) < 0.1]
    if blks:
        return blks

    # Narrow fallback: only accept blocks within 0.25 hrs (15 min) — prevents
    # assigning a 1.5-hr slot to a 2-hr subject or vice-versa.
    blks = [(s, e) for (s, e) in STANDARD_BLOCKS
            if abs(duration_hours(s, e) - target_hours) < 0.26]
    return blks if blks else list(STANDARD_BLOCKS)


# kept for backward-compat with any external callers
def get_valid_blocks_for_subject(subject: dict, is_lab: bool = False) -> list:
    lec = subject.get('lecturehours', 0)
    lab = subject.get('laboratoryhours', 0)
    if is_lab and lab > 0:
        return get_blocks_for_hours(lab, is_lab=True)
    target = lec or lab or 3
    return get_blocks_for_hours(target, is_lab=False)


# ─────────────────────────────────────────────────────────────
#  LAYER 2 — HARD CONSTRAINT (CSP) VALIDATOR
# ─────────────────────────────────────────────────────────────

class CSPValidator:

    def __init__(self, config: dict = None):
        """Load hard-constraint configuration from DB (or use supplied dict)."""
        try:
            self._cfg = config if config is not None else load_scheduler_config()
        except Exception:
            self._cfg = {}
        self._build_runtime_constants()

    def _build_runtime_constants(self):
        cfg = self._cfg

        # ── Day pairs (HC6) ───────────────────────────────────
        raw_pairs = cfg.get('hc_day_pairs', '')
        self._day_pairs = _parse_day_pairs(raw_pairs) if raw_pairs else [
            list(p) for p in DAY_PAIRS.values()
        ]

        # ── Valid time blocks (HC5) ────────────────────────────
        raw_slots = cfg.get('hc_time_slots', '')
        if raw_slots:
            self._valid_starts, self._valid_ends = _parse_time_slots(raw_slots)
            # Always include the module-level standard blocks so 2hr/3hr
            # lab blocks are never flagged as invalid by HC5.
            self._valid_starts |= VALID_START_TIMES
            self._valid_ends   |= VALID_END_TIMES
        else:
            self._valid_starts = VALID_START_TIMES
            self._valid_ends   = VALID_END_TIMES

        # ── Sunday / weekend restriction (HC4) ────────────────
        subj_restr = cfg.get('hc_weekend_subject', 'nstp_only')
        if subj_restr == 'all_allowed':
            self._sunday_prefixes = None          # no restriction
        else:
            self._sunday_prefixes = ('NSTP', 'OU')

        # Which days are NSTP-restricted: Sunday only, or all weekends
        weekend_day = cfg.get('hc_weekend_day', 'sunday_only')
        self._weekend_restricted_days = WEEKEND if weekend_day == 'all_weekends' else ['Sunday']

        # ── Allowed merge section pairs ────────────────────────
        # Each pair is ["PROGRAMCODE-SECTIONNAME", "PROGRAMCODE-SECTIONNAME"]; stored as an
        # unordered frozenset so either ordering matches. Empty/unparsable → no pairs configured.
        raw_merge_pairs = cfg.get('hc_merge_section_pairs', '[]')
        self._merge_section_pairs = set()
        try:
            for pair in json.loads(raw_merge_pairs or '[]'):
                if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] and pair[1]:
                    self._merge_section_pairs.add(frozenset((str(pair[0]).upper(), str(pair[1]).upper())))
        except (TypeError, ValueError):
            pass

    # ── Helper: is this HC toggle enabled? ────────────────────
    def _enabled(self, key: str) -> bool:
        return bool(self._cfg.get(key, 1))

    def validate(self, schedule: list, faculty_map: dict, skip_rules: set = None,
                 existing_load: dict = None) -> list:
        """
        existing_load: {faculty_id: units_already_committed_in_other_sections}
            When supplied, HC8 adds these cross-section units to each faculty's
            intra-schedule total before checking limits.  This makes the validation
            match what the Manual Editor shows (total load across all sections).
        """
        skip_rules = skip_rules or set()
        violations = []
        # HC1/HC2/HC3  Faculty time-window & load limits
        if self._enabled('hc_faculty_load_enabled'):
            violations += self._check_time_windows(schedule, faculty_map)
        # HC4  Sunday / NSTP restriction
        if self._enabled('hc_weekend_enabled'):
            violations += self._check_sunday_restriction(schedule)
        # HC6  Day pairing — skipped during draft saves; only enforced at publish time
        if self._enabled('hc_day_pairing_enabled') and 'HC6' not in skip_rules:
            violations += self._check_day_pairing(schedule)
        # HC7  Night PT cap (designees)
        if self._enabled('hc_faculty_load_enabled'):
            violations += self._check_night_pt_cap(schedule, faculty_map)
        # HC8  Teaching load limits (include cross-section load when available)
        if self._enabled('hc_faculty_load_enabled'):
            violations += self._check_load_limits(schedule, faculty_map,
                                                   existing_load=existing_load)
        # HC9  Room overlap
        if self._enabled('hc_room_conflict_enabled'):
            violations += self._check_room_overlaps(schedule)
        # HC10 Faculty double-booking
        if self._enabled('hc_faculty_conflict_enabled'):
            violations += self._check_faculty_overlaps(schedule)
        # HC11 Section time conflict — always enforced; two subjects cannot share a time slot
        violations += self._check_section_overlaps(schedule)
        # HC_SPEC  Faculty specialization restriction
        if self._enabled('hc_faculty_spec_enabled'):
            violations += self._check_faculty_specialization(schedule, faculty_map)
        # HC_LAB   Lab subjects must be in Laboratory rooms
        if self._enabled('hc_lab_session_enabled'):
            violations += self._check_lab_room(schedule)
        return violations

    # ── HC1 / HC2 / HC3 ────────────────────────────────────────

    def _check_time_windows(self, schedule, faculty_map):
        violations = []
        for cls in schedule:
            fnum = cls.get('faculty_id')
            if not fnum or fnum not in faculty_map:
                continue

            fac         = faculty_map[fnum]
            emp_status  = fac.get('employeestatus', '')
            designation = fac.get('designationid')
            et          = fac.get('employeetype', {})
            night_svc   = fac.get('nightteachingservice')

            start: time = cls['start_time']
            end:   time = cls['end_time']
            day:   str  = cls['day']
            is_weekend  = day in WEEKEND

            regular_start = et.get('regular_start') or time(7, 30)
            _raw_re       = et.get('regular_end')
            # If regular_end equals regular_start (no effective band, e.g. Part-time type),
            # fall back to the default so daytime slots aren't misclassified as evening.
            regular_end   = _raw_re if (_raw_re and _raw_re != regular_start) else time(16, 30)

            is_regular_slot = (
                day in WEEKDAYS and
                start >= regular_start and
                end   <= regular_end
            )
            subj_code = cls.get('subject_code', '?')

            # Designee check takes priority — a faculty member with a designation
            # is always validated under HC2 rules, regardless of employment status.
            if designation is not None:
                if is_regular_slot:
                    # Designees with approved night service (nightteachingservice > 0) use 07:30–16:30;
                    # designees without it use the standard 08:00–17:00 window.
                    if night_svc and night_svc > 0:
                        desig_min, desig_max = regular_start, regular_end
                    else:
                        desig_min, desig_max = time(8, 0), time(17, 0)
                    if start < desig_min or end > desig_max:
                        violations.append({
                            'rule': 'HC2',
                            'subject': subj_code,
                            'detail': (
                                f'Designee/administrator teaching hours are '
                                f'{format_time_12h(desig_min)}–{format_time_12h(desig_max)} on weekdays. '
                                f'The slot on {day} ({format_time_12h(start)}–{format_time_12h(end)}) is outside this window.'
                            )
                        })
                else:
                    if not is_weekend:
                        # PT/Night Teaching Service is a count of nights/week this designee
                        # is on OFFICE duty in the evening, not a count of nights they may
                        # teach. It's subtracted from the 6-night (Mon–Sat) week to get the
                        # nights actually available for evening teaching (see HC7 /
                        # _check_night_pt_cap) — 0 office nights means all 6 are free.
                        allowed_nights = max(0, 6 - int(night_svc or 0))
                        if allowed_nights <= 0:
                            violations.append({
                                'rule': 'HC3',
                                'subject': subj_code,
                                'detail': (
                                    f'This designee/administrator has no evening teaching nights available '
                                    f'(fully committed to night office service) and may not be scheduled for '
                                    f'classes outside regular hours on {day}. '
                                    f'Please assign a different faculty or update the faculty hours settings.'
                                )
                            })

            elif emp_status == 'Part-Time':
                ts_hours = int(et.get('teachingsubstitution', 0) or 0)
                if not is_weekend and et.get('restrict_pt_hours', True):
                    # Part-time faculty with TS hours may teach during regular daytime hours
                    # (daytime sessions are counted as TS load, not PT load).
                    if is_regular_slot and ts_hours > 0:
                        pass  # Allowed via Teaching Substitution
                    else:
                        pt_start = et.get('parttime_start') or time(16, 30)
                        pt_end   = et.get('parttime_end')   or time(21, 0)
                        if end <= pt_start or start >= pt_end:
                            violations.append({
                                'rule': 'HC3',
                                'subject': subj_code,
                                'detail': (
                                    f'Part-time faculty may only be scheduled between '
                                    f'{format_time_12h(pt_start)} and {format_time_12h(pt_end)} on weekdays '
                                    f'based on the configured faculty hours settings. '
                                    f'The slot on {day} ({format_time_12h(start)}–{format_time_12h(end)}) '
                                    f'falls outside this allowed window.'
                                )
                            })

            elif emp_status in ('Permanent', 'Temporary'):
                if is_regular_slot:
                    if start < time(7, 30) or end > time(16, 30):
                        violations.append({
                            'rule': 'HC1',
                            'subject': subj_code,
                            'detail': (
                                f'Full-time faculty regular classes must be within 7:30 AM–4:30 PM. '
                                f'The slot on {day} ({format_time_12h(start)}–{format_time_12h(end)}) '
                                f'is outside the allowed regular teaching window.'
                            )
                        })
                else:
                    # Only enforce evening window if the slot actually extends past regular hours
                    if not is_weekend and end > regular_end:
                        ft_pt_start = et.get('parttime_start') or time(16, 30)
                        ft_pt_end   = et.get('parttime_end')   or time(21, 0)
                        # Violation only when the slot has NO overlap with the allowed evening window.
                        # A slot that starts slightly before ft_pt_start (e.g. 4:00–5:30 PM with
                        # a 4:30 PM window) is still valid because it overlaps the allowed range.
                        if end <= ft_pt_start or start >= ft_pt_end:
                            violations.append({
                                'rule': 'HC3',
                                'subject': subj_code,
                                'detail': (
                                    f'Full-time faculty evening classes must be within '
                                    f'{format_time_12h(ft_pt_start)}–{format_time_12h(ft_pt_end)}. '
                                    f'The slot on {day} ({format_time_12h(start)}–{format_time_12h(end)}) '
                                    f'is outside the allowed evening teaching window based on the faculty hours settings.'
                                )
                            })

        return violations

    # ── HC4 Weekend / NSTP restriction ─────────────────────────

    def _check_sunday_restriction(self, schedule):
        violations = []
        allowed = self._sunday_prefixes          # None = no restriction
        if not allowed:
            return violations
        for cls in schedule:
            days = cls.get('days_list', [cls.get('day', '')])
            for restricted_day in self._weekend_restricted_days:
                if restricted_day in days:
                    code = cls.get('subject_code', '')
                    if not any(code.upper().startswith(p) for p in allowed):
                        violations.append({
                            'rule': 'HC4',
                            'subject': code,
                            'detail': (
                                f'"{code}" cannot be scheduled on {restricted_day}. '
                                f'Based on the current Weekend Restriction settings, '
                                f'only NSTP/OU subjects are allowed on {restricted_day}.'
                            )
                        })
                    break  # one violation per class entry
        return violations

    # ── HC5 Standard slots ──────────────────────────────────────

    def _check_standard_slots(self, schedule):
        violations = []
        v_starts = self._valid_starts
        v_ends   = self._valid_ends
        for cls in schedule:
            start = cls.get('start_time')
            end   = cls.get('end_time')
            subj = cls.get('subject_code', '?')
            if start and start not in v_starts:
                violations.append({
                    'rule': 'HC5',
                    'subject': subj,
                    'detail': f'"{subj}" — invalid start time ({format_time_12h(start)}). Please select a standard time block.'
                })
            if end and end not in v_ends:
                violations.append({
                    'rule': 'HC5',
                    'subject': subj,
                    'detail': f'"{subj}" — invalid end time ({format_time_12h(end)}). Please select a standard time block.'
                })
        return violations

    # ── HC6 Day pairing ─────────────────────────────────────────
    # When enabled, any subject that spans exactly 2 days must use only the
    # configured valid pairings (default: Mon-Thu, Tue-Fri, Wed-Sat).
    # Two complementary checks are performed:
    #   A) Per-entry  — a single schedule entry with days_list of length 2
    #                   is validated directly.
    #   B) Cross-entry — multiple single-day entries for the same subject AND
    #                    same class_type (Lecture/Lab) that together span exactly
    #                    2 distinct days are also validated.  Grouping by class_type
    #                    prevents false positives when a lecture is on paired days
    #                    and the lab is placed on a third independent day.

    def _check_day_pairing(self, schedule):
        violations = []
        reported: set = set()   # (code, tuple(sorted_days)) already reported
        pair_strs = ' / '.join('-'.join(p) for p in self._day_pairs)

        def _flag(code, sorted_days):
            key = (code, tuple(sorted_days))
            if key in reported:
                return
            reported.add(key)
            violations.append({
                'rule': 'HC6',
                'subject': code,
                'detail': (
                    f'"{code}": The day combination {list(sorted_days)} is not a valid pairing. '
                    f'Allowed pairings are: {pair_strs}. '
                    f'Please adjust the schedule days to match one of the configured pairs '
                    f'(Mon-Thu, Tue-Fri, or Wed-Sat).'
                )
            })

        # ── Check A: per-entry ────────────────────────────────────────────────
        for cls in schedule:
            code = (cls.get('subject_code') or cls.get('subjectcode') or '').strip()
            if not code:
                continue
            entry_days = cls.get('days_list') or []
            if not isinstance(entry_days, list):
                entry_days = [entry_days] if entry_days else []
            # Only check entries that explicitly carry exactly 2 days
            if len(entry_days) != 2:
                continue
            sorted_days = _sort_day_pair(entry_days)
            if not any(pair == sorted_days for pair in self._day_pairs):
                _flag(code, sorted_days)

        # ── Check B: cross-entry accumulation per (subject_code, class_type) ──
        # Groups single-day entries (e.g. from the manual editor) so that a
        # subject placed on Mon by one entry and Tue by another is caught.
        # Lecture and Lab are kept separate to avoid combining their days.
        subj_type_days: dict = defaultdict(set)
        for cls in schedule:
            code  = (cls.get('subject_code') or cls.get('subjectcode') or '').strip()
            if not code:
                continue
            ctype = cls.get('class_type', 'Lecture')
            days_list = cls.get('days_list') or [cls.get('day', '')]
            for d in days_list:
                if d:
                    subj_type_days[(code, ctype)].add(d)

        for (code, ctype), days_set in subj_type_days.items():
            if len(days_set) != 2:
                continue   # single-day or 3+-day groups are outside HC6 scope
            sorted_days = _sort_day_pair(list(days_set))
            if not any(pair == sorted_days for pair in self._day_pairs):
                _flag(code, sorted_days)

        return violations

    # ── HC7 Night PT cap ────────────────────────────────────────

    def _check_night_pt_cap(self, schedule, faculty_map):
        # PT/Night Teaching Service is the number of nights/week this designee is on
        # night OFFICE duty, not a count of teaching nights. Subtract it from the
        # 6-night (Mon–Sat) week to get the nights available for evening teaching —
        # not a class count (one night can hold more than one session) and not a
        # fixed clock window (see the HC3 note in _check_time_windows).
        violations = []
        night_days: dict = defaultdict(set)
        for cls in schedule:
            fnum = cls.get('faculty_id')
            if not fnum or fnum not in faculty_map:
                continue
            fac = faculty_map[fnum]
            if fac.get('designationid') is None:
                continue
            day = cls.get('day')
            if day not in WEEKDAYS:
                continue

            et            = fac.get('employeetype', {})
            regular_start = et.get('regular_start') or time(7, 30)
            _raw_re       = et.get('regular_end')
            regular_end   = _raw_re if (_raw_re and _raw_re != regular_start) else time(16, 30)
            # Within this designee's own regular daytime hours — doesn't count as a night
            if cls['start_time'] >= regular_start and cls['end_time'] <= regular_end:
                continue
            night_days[fnum].add(day)

        for fnum, days in night_days.items():
            fac            = faculty_map[fnum]
            night_svc      = int(fac.get('nightteachingservice') or 0)
            allowed_nights = max(0, 6 - night_svc)
            if len(days) > allowed_nights:
                violations.append({
                    'rule': 'HC7',
                    'subject': 'multiple',
                    'detail': (
                        f'Faculty {fac.get("fullname") or fnum} has {night_svc} night office '
                        f'service duty/duties per week, leaving {allowed_nights} evening night(s) '
                        f'available for teaching, but this schedule uses {len(days)} '
                        f'({", ".join(sorted(days))}).'
                    )
                })
        return violations

    # ── HC8 Teaching load limits ────────────────────────────────

    def _check_load_limits(self, schedule, faculty_map, existing_load: dict = None):
        """
        existing_load: HOURS already committed by each faculty in OTHER sections/programs
            this term (from fetch_current_faculty_loads — real scheduled hours). Added to
            the intra-schedule hours so the check matches the total load visible in the
            Manual Editor (faculty_load.py is the shared source of truth for both).
        """
        violations = []
        regular_hrs = defaultdict(float)
        pt_hrs      = defaultdict(float)
        _cross = existing_load or {}

        for cls in schedule:
            fnum = cls.get('faculty_id')
            if not fnum or fnum not in faculty_map:
                continue
            fac = faculty_map[fnum]
            et  = fac.get('employeetype', {})
            # Real elapsed hours for this gene, now that a day/time has been chosen (see
            # duration_hrs in _build_individual) — unlike the old credit-units model, BOTH
            # the lecture AND lab parts of a subject carry their own real hours and must be
            # summed independently; no per-subject dedup needed since each gene is a
            # genuinely distinct time commitment.
            hrs = cls.get('duration_hrs', 0) or 0
            if hrs == 0:
                continue

            regular_end = et.get('regular_end') or time(16, 30)
            is_regular  = cls['day'] in WEEKDAYS and cls['end_time'] <= regular_end
            if is_regular:
                regular_hrs[fnum] += hrs
            else:
                pt_hrs[fnum] += hrs

        for fnum, hrs in regular_hrs.items():
            # Add cross-section committed hours so the check matches the Manual Editor total
            hrs += _cross.get(fnum, 0)
            et         = faculty_map[fnum].get('employeetype', {})
            emp_status = faculty_map[fnum].get('employeestatus', '')
            ts_hours   = float(et.get('teachingsubstitution', 0) or 0)
            is_pt_fac  = 'part' in emp_status.lower()
            if is_pt_fac:
                # Part-time faculty have no regular load; daytime sessions are TS sessions.
                max_reg = ts_hours if ts_hours else 0
            else:
                max_reg = et.get('regularload') or 99
            if hrs > max_reg:
                excess = hrs - max_reg
                fac_name = (faculty_map[fnum].get('fullname') or fnum)
                if is_pt_fac:
                    violations.append({
                        'rule': 'HC8',
                        'subject': 'multiple',
                        'detail': f'{fac_name} Teaching Substitution load {hrs:.1f} hrs exceeds TS limit of {max_reg:.1f} hrs.'
                    })
                else:
                    if excess > ts_hours:
                        violations.append({
                            'rule': 'HC8',
                            'subject': 'multiple',
                            'detail': f'{fac_name} regular load {hrs:.1f} hrs exceeds limit {max_reg:.1f}'
                                      + (f' (TS {ts_hours:.1f}h available, short {excess - ts_hours:.1f}h)' if ts_hours else '')
                        })

        for fnum, hrs in pt_hrs.items():
            hrs += _cross.get(fnum, 0)
            et     = faculty_map[fnum].get('employeetype', {})
            max_pt = et.get('parttimeload') or 99
            if hrs > max_pt:
                ts_hours = float(et.get('teachingsubstitution', 0) or 0)
                excess   = hrs - max_pt
                fac_name = (faculty_map[fnum].get('fullname') or fnum)
                if excess > ts_hours:
                    violations.append({
                        'rule': 'HC8',
                        'subject': 'multiple',
                        'detail': f'{fac_name} PT load {hrs:.1f} hrs exceeds limit {max_pt:.1f}'
                                  + (f' (TS {ts_hours:.1f}h available, short {excess - ts_hours:.1f}h)' if ts_hours else '')
                    })

        return violations

    # ── HC9 Room overlap ────────────────────────────────────────

    def _section_label(self, cls):
        """'PROGRAMCODE-SECTIONNAME' identity for a gene/session dict, or None when the
        dict carries no section identity (e.g. the GA's internal single-section gene
        representation) — callers must treat None as "can't restrict, allow through"."""
        prog = cls.get('course') or cls.get('program') or cls.get('programcode')
        sect = cls.get('section_name') or cls.get('sectionname')
        if not prog or not sect:
            return None
        return f"{str(prog).upper()}-{sect}"

    def _section_pair_allowed(self, a, b):
        """Only configured section pairs (Merge Class Configuration, Settings page) may
        merge. When either side has no resolvable section identity, the restriction
        can't be evaluated — fall back to allowing the merge (legacy behavior) rather
        than blocking scheduling paths that never carried section context to begin with."""
        if not self._merge_section_pairs:
            return True  # nothing configured yet — don't newly restrict existing behavior
        label_a, label_b = self._section_label(a), self._section_label(b)
        if not label_a or not label_b:
            return True
        if label_a == label_b:
            return True  # same section, e.g. two time-slices of the same assignment
        return frozenset((label_a, label_b)) in self._merge_section_pairs

    def _check_room_overlaps(self, schedule):
        violations = []
        merge_enabled = bool(self._cfg.get('hc_merge_enabled', 1))
        merge_scope   = str(self._cfg.get('hc_merge_scope', 'nstp_only'))
        for i, a in enumerate(schedule):
            for b in schedule[i+1:]:
                a_room, b_room = a.get('room_id'), b.get('room_id')
                # A room that's TBA (not yet decided) can never conflict with anything —
                # skip before the equality check, otherwise two unrelated TBA sessions
                # (both falsy/'TBA') would be treated as "same room" and falsely flagged.
                if not a_room or not b_room or str(a_room).upper() == 'TBA' or str(b_room).upper() == 'TBA':
                    continue
                if a_room != b_room:
                    continue
                a_code = (a.get('subject_code') or a.get('subjectcode') or '').upper()
                b_code = (b.get('subject_code') or b.get('subjectcode') or '').upper()
                # Merge class: same subject + policy-aware faculty check
                if a_code == b_code and merge_enabled:
                    is_nstp = a_code.startswith(('NSTP', 'OU'))
                    in_scope = (
                        (merge_scope == 'nstp_only'    and is_nstp) or
                        (merge_scope == 'non_nstp'     and not is_nstp) or
                        (merge_scope == 'all_subjects')
                    )
                    if in_scope:
                        # NSTP/OU: flexible — different faculty may teach merged sections
                        # Non-NSTP: strict — only same faculty may share a slot
                        same_fac = a.get('faculty_id') == b.get('faculty_id')
                        if (is_nstp or same_fac) and self._section_pair_allowed(a, b):
                            continue  # valid merge — skip room conflict
                for day_a in a.get('days_list', [a.get('day')]):
                    for day_b in b.get('days_list', [b.get('day')]):
                        if day_a != day_b:
                            continue
                        if self._times_overlap(a['start_time'], a['end_time'],
                                               b['start_time'], b['end_time']):
                            violations.append({
                                'rule': 'HC9',
                                'subject': f"{a_code} / {b_code}",
                                'detail': (
                                    f"Room {a.get('room')} double-booked on {day_a} "
                                    f"{format_time_12h(a['start_time'])}–{format_time_12h(a['end_time'])}"
                                )
                            })
        return violations

    # ── HC10 Faculty overlap ────────────────────────────────────

    def _check_faculty_overlaps(self, schedule):
        violations = []
        for i, a in enumerate(schedule):
            for b in schedule[i+1:]:
                if a.get('faculty_id') != b.get('faculty_id'):
                    continue

                # Exception: one teacher may handle multiple NSTP/OU groups
                # simultaneously (different sections, overlapping time allowed).
                a_nstp = any(a.get('subject_code', '').upper().startswith(p)
                             for p in SUNDAY_ALLOWED_PREFIXES)
                b_nstp = any(b.get('subject_code', '').upper().startswith(p)
                             for p in SUNDAY_ALLOWED_PREFIXES)
                if a_nstp and b_nstp:
                    continue

                for day_a in a.get('days_list', [a.get('day')]):
                    for day_b in b.get('days_list', [b.get('day')]):
                        if day_a != day_b:
                            continue
                        if self._times_overlap(a['start_time'], a['end_time'],
                                               b['start_time'], b['end_time']):
                            violations.append({
                                'rule': 'HC10',
                                'subject': f"{a.get('subject_code')} / {b.get('subject_code')}",
                                'detail': (
                                    f"Faculty {a.get('instructor')} double-booked on {day_a} "
                                    f"{format_time_12h(a['start_time'])}"
                                    f"–{format_time_12h(a['end_time'])}"
                                )
                            })
        return violations

    # ── HC_SPEC Faculty specialization restriction ──────────────

    def _check_faculty_specialization(self, schedule, faculty_map):
        violations = []
        for cls in schedule:
            fac_id = cls.get('faculty_id')
            if not fac_id or fac_id not in faculty_map:
                continue
            fac  = faculty_map[fac_id]
            spec = (fac.get('specializationname') or '').strip()
            if not spec:
                continue  # no specialization assigned → no restriction
            subj_code = (cls.get('subject_code') or cls.get('subjectcode') or '').strip()
            if not _spec_matches_subject(spec, subj_code):
                fac_name     = fac.get('fullname') or 'The assigned faculty'
                required_spec = _required_spec_for_subject(subj_code)
                violations.append({
                    'rule':     'HC_SPEC',
                    'severity': 'warning',   # Advisory only — Academic Head may override
                    'subject':  subj_code or '?',
                    'detail': (
                        f'"{fac_name}" ({spec}) may not match the expected specialization for '
                        f'"{subj_code}" (expected: {required_spec}). '
                        f'You may still save and approve — this is an advisory notice only.'
                    )
                })
        return violations

    def _check_lab_room(self, schedule):
        # Group by subject — at least ONE session per lab subject must be in a laboratory room.
        # Lecture sessions of a LEC+LAB subject may still use regular rooms; only lab sessions need lab rooms.
        subj_sessions: dict = defaultdict(list)
        for cls in schedule:
            lab_h = cls.get('lab_hours') or cls.get('laboratoryhours') or 0
            if lab_h:
                code = cls.get('subject_code') or cls.get('subjectcode') or '?'
                subj_sessions[code].append(cls)

        violations = []
        for code, sessions in subj_sessions.items():
            has_lab_room = any(
                (cls.get('room_type') or cls.get('roomtype') or '').strip().lower() == 'laboratory'
                for cls in sessions
            )
            # A session with room TBA means the room isn't decided yet — defer the lab-room
            # requirement for this subject rather than hard-blocking (the Academic Head will
            # resolve it once a real room is assigned).
            has_tba_room = any(
                not cls.get('room_id') or str(cls.get('room_id')).upper() == 'TBA'
                for cls in sessions
            )
            if not has_lab_room and not has_tba_room:
                violations.append({
                    'rule':    'HC_LAB',
                    'subject': code,
                    'detail': (
                        f'"{code}" has laboratory hours but none of its sessions are assigned to a '
                        f'Laboratory room. At least one session must use a Laboratory room. '
                        f'Please assign a Laboratory room to one of the sessions, or disable the '
                        f'Laboratory Room Requirement in Settings.'
                    )
                })
        return violations

    # ── HC11 Section time conflict ──────────────────────────────
    # Separate from HC9/HC10: even with different rooms and faculty, students
    # in the same section cannot attend two classes simultaneously.

    def _check_section_overlaps(self, schedule):
        violations = []
        for i, a in enumerate(schedule):
            for b in schedule[i+1:]:
                a_code = a.get('subject_code', '?')
                b_code = b.get('subject_code', '?')
                for day_a in a.get('days_list', [a.get('day', '')]):
                    if not day_a:
                        continue
                    for day_b in b.get('days_list', [b.get('day', '')]):
                        if day_a != day_b:
                            continue
                        if self._times_overlap(a['start_time'], a['end_time'],
                                               b['start_time'], b['end_time']):
                            violations.append({
                                'rule': 'HC11',
                                'subject': f"{a_code} / {b_code}",
                                'detail': (
                                    f'Section conflict on {day_a}: "{a_code}" '
                                    f'({format_time_12h(a["start_time"])}–{format_time_12h(a["end_time"])}) '
                                    f'and "{b_code}" '
                                    f'({format_time_12h(b["start_time"])}–{format_time_12h(b["end_time"])}) '
                                    f'overlap. A section cannot have two classes at the same time.'
                                )
                            })
        seen = set()
        unique = []
        for v in violations:
            key = (v['rule'], v['subject'])
            if key not in seen:
                seen.add(key)
                unique.append(v)
        return unique

    @staticmethod
    def _times_overlap(s1, e1, s2, e2):
        return s1 < e2 and e1 > s2


# ─────────────────────────────────────────────────────────────
#  LAYER 2.5 — CASE-BASED REASONING (CBR) RETRIEVER
# ─────────────────────────────────────────────────────────────
# CBR 4-R cycle as applied in this system:
#   Retrieve : score all past schedule versions by similarity to the current query
#             (program, year level, term, curriculum year) and pick the best match
#   Reuse    : extract that case's faculty assignments to seed the GA population
#   Revise   : the GA evolution loop (Layer 3) refines the CBR-seeded individuals
#   Retain   : approving/publishing a schedule writes it back to the DB as a new case

class CaseBasedRetriever:
    """
    CBR: finds the most similar past case from historical_data (imported schedules)
    and returns its faculty assignments as hints for seeding the GA's initial population.
    Cases are distinct (program, year_level, term, academic_year) groups in historical_data,
    not draft/published schedule versions.
    """

    # Similarity weights per feature dimension (must sum to 1.0)
    _W = {'program': 0.40, 'yearlevel': 0.30, 'term': 0.20, 'acayear': 0.10}

    @staticmethod
    def _ay_to_int(ay_str: str) -> int:
        """Parse academicyearid (e.g. 'AY2425') to a comparable integer (e.g. 2024)."""
        try:
            digits = ''.join(c for c in (ay_str or '') if c.isdigit())
            return int(digits[:4]) if len(digits) >= 4 else int(digits[:2]) + 2000 if digits else 0
        except (ValueError, TypeError):
            return 0

    def _build_case_library(self) -> list:
        """
        Load all distinct past cases from historical_data.
        Each case = a unique (program, year_level, semestertype, semesterid, academicyearid)
        group — representing one semester's worth of imported schedule data.
        """
        rows = query_db("""
            SELECT DISTINCT
                REGEXP_REPLACE(hd."Program", '\\s+\\d+$', '') AS programcode,
                CAST(hd."Year Level" AS INTEGER)              AS yearlevel,
                sem.semestertype                              AS term,
                hd.semesterid,
                hd.academicyearid
            FROM historical_data hd
            JOIN semester sem ON hd.semesterid = sem.semesterid
            WHERE hd."Subject Code" IS NOT NULL AND TRIM(hd."Subject Code") != ''
              AND hd."Instructor"   IS NOT NULL AND TRIM(hd."Instructor")   != ''
            ORDER BY hd.academicyearid DESC
        """)
        return list(rows) if rows else []

    def _similarity(self, query: dict, case: dict) -> float:
        """Weighted similarity score [0.0–1.0] between a query and a stored case."""
        w = self._W
        score = 0.0
        if (query['programcode'] or '').upper() == (case.get('programcode') or '').upper():
            score += w['program']
        if str(query['yearlevel']) == str(case.get('yearlevel')):
            score += w['yearlevel']
        if query['term'] == case.get('term'):
            score += w['term']
        # Academic year proximity: each year apart reduces score by 0.10/5 = 0.02
        diff   = abs(self._ay_to_int(query['academicyearid']) - self._ay_to_int(case.get('academicyearid', '')))
        score += w['acayear'] * max(0.0, 1.0 - diff / 5.0)
        return score

    def retrieve_best_case_faculty(self, program: str, year_level: int,
                                   term: str, academicyearid: str) -> dict:
        """
        Returns {subjectcode: employeenumber} from the most similar historical_data case.
        Instructor names are matched to faculty.employeenumber by last-name comparison.
        Falls back to empty dict when no suitable case is found.
        """
        query = {
            'programcode':   program,
            'yearlevel':     year_level,
            'term':          term,
            'academicyearid': academicyearid or '',
        }
        cases = self._build_case_library()
        if not cases:
            return {}

        best = max(cases, key=lambda c: self._similarity(query, c))
        if self._similarity(query, best) == 0:
            return {}

        # Extract (subject_code, employeenumber) from the best-matching historical case.
        # Match instructor names stored as "Lastname, Firstname M." to faculty using
        # both last name AND the first word of the first name to avoid ambiguity
        # when multiple faculty share the same surname.
        rows = query_db("""
            SELECT
                TRIM(hd."Subject Code") AS subjectcode,
                f.employeenumber
            FROM historical_data hd
            JOIN faculty f
              ON UPPER(TRIM(SPLIT_PART(hd."Instructor", ',', 1))) = UPPER(TRIM(f.lastname))
             AND (
                 SPLIT_PART(TRIM(SPLIT_PART(hd."Instructor", ',', 2)), ' ', 1) = ''
                 OR UPPER(TRIM(f.firstname)) LIKE (UPPER(SPLIT_PART(TRIM(SPLIT_PART(hd."Instructor", ',', 2)), ' ', 1)) || '%%')
             )
            WHERE REGEXP_REPLACE(hd."Program", '\\s+\\d+$', '') ILIKE %s
              AND CAST(hd."Year Level" AS TEXT) = %s
              AND hd.semesterid      = %s
              AND hd.academicyearid  = %s
              AND hd."Subject Code" IS NOT NULL AND TRIM(hd."Subject Code") != ''
              AND hd."Instructor"   IS NOT NULL AND TRIM(hd."Instructor")   != ''
              AND f.employeestatus  != 'Archive'
        """, (program, str(year_level), best['semesterid'], best['academicyearid']))

        result = {}
        for row in (rows or []):
            code = row['subjectcode']
            if code not in result:
                result[code] = row['employeenumber']
        return result


# ─────────────────────────────────────────────────────────────
#  LAYER 3 — GA ENGINE
# ─────────────────────────────────────────────────────────────

class IntelligentScheduler:

    def __init__(self):
        try:
            self._hc_cfg = load_scheduler_config()
        except Exception:
            self._hc_cfg = {}
        self.csp = CSPValidator(config=self._hc_cfg)

        # Runtime constants used by the builder
        raw_pairs = self._hc_cfg.get('hc_day_pairs', '')
        self._builder_pairs = (
            _parse_day_pairs(raw_pairs) if raw_pairs
            else [list(p) for p in DAY_PAIRS.values()]
        )
        # NSTP/Sunday enforcement for the builder
        weekend_on = bool(self._hc_cfg.get('hc_weekend_enabled', 1))
        subj_restr = self._hc_cfg.get('hc_weekend_subject', 'nstp_only')
        self._nstp_force_sunday  = weekend_on and (subj_restr != 'all_allowed')
        self._weekend_day_scope  = self._hc_cfg.get('hc_weekend_day', 'sunday_only')
        # Lab room enforcement for the builder
        self._enforce_lab_rooms  = bool(self._hc_cfg.get('hc_lab_session_enabled', 1))
        # Faculty specialization enforcement for the builder
        self._spec_enabled       = bool(self._hc_cfg.get('hc_faculty_spec_enabled', 1))

    # ── Database helpers ─────────────────────────────────────────

    def fetch_data(self, program, year_level, term, curriculum_year):
        subjects_query = """
            SELECT cs.subjectcode, cs.subjectname, cs.lecturehours, cs.laboratoryhours,
                   cs.creditunits, cs.tuitionhours, c.programcode AS offeringcode
            FROM curriculumsubject cs
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
            WHERE c.programcode  = %s
              AND c.curriculumyear = %s
              AND cs.yearlevel     = %s
              AND cs.semester      = %s
        """
        subjects = query_db(subjects_query, (program, curriculum_year, year_level, term))

        faculty_query = """
            SELECT f.employeenumber,
                   CONCAT(f.lastname, ', ', f.firstname) AS fullname,
                   f.employeestatus,
                   f.designationid,
                   et.regularload,
                   et.parttimeload,
                   et.teachingsubstitution,
                   et.regular_start,
                   et.regular_end,
                   et.parttime_start,
                   et.parttime_end,
                   COALESCE(et.restrict_pt_hours, TRUE) AS restrict_pt_hours,
                   d.nightteachingservice,
                   COALESCE(d.regularloadunit, 0) AS designation_regular_load,
                   sp.specializationname
            FROM faculty f
            JOIN employeetype et ON f.employeetypeid = et.employeetypeid
            LEFT JOIN designation d      ON f.designationid    = d.designationid
            LEFT JOIN specialization sp  ON f.specializationid = sp.specializationid
            WHERE f.employeestatus != 'Archive'
        """
        faculty_rows = query_db(faculty_query)

        faculty_map  = {}
        faculty_list = []
        for row in (faculty_rows or []):
            fnum = row['employeenumber']
            # Cap lookup via faculty_load (shared with app.py so the GA and Manual Editor
            # can't drift) — regularload/parttimeload/teachingsubstitution/regularloadunit
            # are HOUR caps now, not credit units. This also fixes a pre-existing bug where
            # a designee's PT cap was read from nightteachingservice (a "nights on night
            # office duty" count) instead of the faculty's own employeetype.parttimeload.
            eff_regular, eff_parttime, eff_ts = faculty_load.get_faculty_caps(row)
            fac = {
                'employeenumber':   fnum,
                'fullname':         row['fullname'],
                'employeestatus':   row['employeestatus'],
                'designationid':    row['designationid'],
                'nightteachingservice': row['nightteachingservice'],
                'specializationname':   row.get('specializationname') or '',
                'employeetype': {
                    'regularload':          eff_regular,
                    'parttimeload':         eff_parttime,
                    'teachingsubstitution': eff_ts,
                    'regular_start':        row['regular_start'] or time(7, 30),
                    'regular_end':          row['regular_end']   or time(16, 30),
                    'parttime_start':       row['parttime_start'],
                    'parttime_end':         row['parttime_end'],
                    'restrict_pt_hours':    row.get('restrict_pt_hours', True),
                },
            }
            faculty_map[fnum] = fac
            faculty_list.append(fac)

        rooms = query_db("SELECT roomid, roomname, roomtype FROM room")

        return subjects, faculty_list, faculty_map, rooms

    def fetch_historical_faculty(self, program: str, term: str = None) -> dict:
        """
        Returns subjectcode → employeenumber from the most recent published/draft
        schedule for this program. Filters by term (same semester) when provided,
        so suggestions come from the same semester last year.
        """
        if term:
            query = """
                SELECT cs.subjectcode, sg.employeenumber
                FROM public.schedule_version sv
                JOIN public.schedule sg ON sv.scheduleid = sg.scheduleid
                JOIN public.curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
                JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
                JOIN public.semester sem ON sg.semesterid = sem.semesterid
                WHERE c.programcode = %s
                  AND sem.semestertype = %s
                  AND sv.status IN ('Published', 'Draft')
                ORDER BY sv.datecreated DESC
            """
            rows = query_db(query, (program, term))
        else:
            query = """
                SELECT cs.subjectcode, sg.employeenumber
                FROM public.schedule_version sv
                JOIN public.schedule sg ON sv.scheduleid = sg.scheduleid
                JOIN public.curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
                JOIN public.curriculum c ON cs.curriculumid = c.curriculumid
                WHERE c.programcode = %s
                  AND sv.status IN ('Published', 'Draft')
                ORDER BY sv.datecreated DESC
            """
            rows = query_db(query, (program,))

        result = {}
        for row in (rows or []):
            code = row['subjectcode']
            if code not in result:
                result[code] = row['employeenumber']
        return result

    def fetch_all_preferences(self, program: str, year_level: int, term: str) -> dict:
        """
        Build a subject-level preference map from three sources in ascending priority
        (lower priority stored first; higher priority overwrites):
          3. Draft schedule_version   (manual editor assignments)
          2. Published schedule_version
          1. historical_data table    (imported SIS records — highest priority)

        Returns {subjectcode: {faculty, room_id, room_name, source}} with one entry
        per subject — the highest-priority source wins per subject.
        """
        prefs = {}

        # ── Priority 3 → 2: schedule_version (Draft first, Published overwrites) ──
        for status in ('Draft', 'Published'):
            rows = query_db("""
                SELECT DISTINCT ON (cs.subjectcode)
                    cs.subjectcode,
                    sg.employeenumber   AS faculty,
                    r.roomid            AS room_id,
                    r.roomname          AS room_name
                FROM public.schedule_version sv
                JOIN public.schedule sg          ON sv.scheduleid          = sg.scheduleid
                JOIN public.curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
                JOIN public.curriculum c         ON cs.curriculumid        = c.curriculumid
                JOIN public.semester sem          ON sg.semesterid          = sem.semesterid
                LEFT JOIN public.schedule_sessions ss ON ss.versionid = sv.versionid
                LEFT JOIN public.room r               ON ss.roomid    = r.roomid
                WHERE UPPER(c.programcode) = UPPER(%s)
                  AND cs.yearlevel            = %s
                  AND UPPER(sem.semestertype) = UPPER(%s)
                  AND sv.status               = %s
                ORDER BY cs.subjectcode, sv.datecreated DESC
            """, (program, year_level, term, status))
            for row in (rows or []):
                code = (row.get('subjectcode') or '').strip()
                if code:
                    prefs[code] = {
                        'faculty':   row.get('faculty'),
                        'room_id':   row.get('room_id'),
                        'room_name': row.get('room_name'),
                        'source':    status.lower(),
                    }

        # ── Priority 1: historical_data (overwrites both Draft and Published above) ──

        # #5: Room preference = MOST FREQUENTLY used room for this subject+program+year+term.
        # Use a window function to rank rooms by count (desc) and pick rank 1 per subject.
        hist_rooms = query_db("""
            WITH room_freq AS (
                SELECT
                    UPPER(TRIM(hd."Subject Code"))  AS subjectcode,
                    rm.roomid,
                    rm.roomname,
                    COUNT(*)                         AS cnt,
                    ROW_NUMBER() OVER (
                        PARTITION BY UPPER(TRIM(hd."Subject Code"))
                        ORDER BY COUNT(*) DESC
                    ) AS rn
                FROM historical_data hd
                JOIN semester sem ON hd.semesterid = sem.semesterid
                JOIN room rm ON REGEXP_REPLACE(UPPER(TRIM(hd."Room")), '[^A-Z0-9]', '', 'g')
                             = REGEXP_REPLACE(UPPER(TRIM(rm.roomname)), '[^A-Z0-9]', '', 'g')
                WHERE UPPER(REGEXP_REPLACE(hd."Program", '\\s+\\d+$', '')) = UPPER(%s)
                  AND CAST(hd."Year Level" AS TEXT) = %s
                  AND UPPER(sem.semestertype) = UPPER(%s)
                  AND hd."Subject Code" IS NOT NULL
                  AND TRIM(hd."Subject Code") != ''
                  AND hd."Room" IS NOT NULL
                  AND TRIM(hd."Room") != ''
                GROUP BY UPPER(TRIM(hd."Subject Code")), rm.roomid, rm.roomname
            )
            SELECT subjectcode, roomid AS room_id, roomname AS room_name
            FROM room_freq
            WHERE rn = 1
        """, (program, str(year_level), term))

        hist_room_map: dict = {}
        for row in (hist_rooms or []):
            code = (row.get('subjectcode') or '').strip()
            if code:
                hist_room_map[code] = {
                    'room_id':   row.get('room_id'),
                    'room_name': row.get('room_name'),
                }

        # Faculty = most recent historical instructor per subject.
        hist_rows = query_db("""
            SELECT DISTINCT ON (UPPER(TRIM(hd."Subject Code")))
                UPPER(TRIM(hd."Subject Code")) AS subjectcode,
                f.employeenumber               AS faculty
            FROM historical_data hd
            JOIN semester sem
                ON hd.semesterid = sem.semesterid
            LEFT JOIN faculty f
                ON UPPER(TRIM(SPLIT_PART(hd."Instructor", ',', 1))) = UPPER(TRIM(f.lastname))
               AND (
                   SPLIT_PART(TRIM(SPLIT_PART(hd."Instructor", ',', 2)), ' ', 1) = ''
                   OR UPPER(TRIM(f.firstname)) LIKE (UPPER(SPLIT_PART(TRIM(SPLIT_PART(hd."Instructor", ',', 2)), ' ', 1)) || '%%')
               )
               AND f.employeestatus != 'Archive'
            WHERE UPPER(REGEXP_REPLACE(hd."Program", '\\s+\\d+$', '')) = UPPER(%s)
              AND CAST(hd."Year Level" AS TEXT) = %s
              AND UPPER(sem.semestertype) = UPPER(%s)
              AND hd."Subject Code" IS NOT NULL
              AND TRIM(hd."Subject Code") != ''
            ORDER BY UPPER(TRIM(hd."Subject Code")), hd.academicyearid DESC
        """, (program, str(year_level), term))

        for row in (hist_rows or []):
            code = (row.get('subjectcode') or '').strip()
            if code:
                room_info = hist_room_map.get(code, {})
                existing  = prefs.get(code, {})
                prefs[code] = {
                    'faculty':   row.get('faculty'),
                    # Prefer historical room; fall back to any room already found from
                    # schedule_version so the historical pass never clears a valid room.
                    'room_id':   room_info.get('room_id')   or existing.get('room_id'),
                    'room_name': room_info.get('room_name') or existing.get('room_name'),
                    'source':    'historical',
                }

        # ── Priority 0: mergedclass (admin pre-assignment — highest priority) ──
        # The mergedclass table stores explicit admin assignments of faculty to
        # curriculumsubjects for a specific semester.  These override everything
        # else because an admin intentionally chose that teacher.
        merged_rows = query_db("""
            SELECT DISTINCT ON (cs.subjectcode)
                cs.subjectcode,
                mc.employeenumber AS faculty
            FROM mergedclass mc
            JOIN curriculumsubject cs ON mc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c         ON cs.curriculumid        = c.curriculumid
            JOIN semester sem         ON mc.semesterid          = sem.semesterid
            WHERE UPPER(c.programcode)       = UPPER(%s)
              AND cs.yearlevel               = %s
              AND UPPER(sem.semestertype)    = UPPER(%s)
              AND mc.employeenumber IS NOT NULL
              AND mc.isactive = TRUE
            ORDER BY cs.subjectcode, mc.datecreated DESC
        """, (program, year_level, term))

        for row in (merged_rows or []):
            code = (row.get('subjectcode') or '').strip()
            if code:
                existing = prefs.get(code, {})
                prefs[code] = {
                    'faculty':   row.get('faculty'),
                    'room_id':   existing.get('room_id'),
                    'room_name': existing.get('room_name'),
                    'source':    'mergedclass',
                }

        return prefs

    def fetch_subject_wide_faculty(self, program: str, subject_codes: list) -> dict:
        """
        Broad, program-wide subject-history lookup.

        For each subject code, find the faculty member who has MOST RECENTLY
        taught that subject for this program — across ANY year level and ANY term.
        This fills preference gaps for subjects like GEED/PE that are shared
        across multiple year levels and wouldn't appear in the year-level-specific
        fetch_all_preferences() results.

        Sources (lower → higher priority so the most recent wins):
          1. historical_data table  (imported SIS records, all years)
          2. schedule_version table (Draft / Published, all years)

        Returns {subjectcode: employeenumber}.
        """
        if not subject_codes:
            return {}

        result = {}

        # ── Source 1: historical_data (any year level, any term for this program) ──
        hist_rows = query_db("""
            SELECT DISTINCT ON (UPPER(TRIM(hd."Subject Code")))
                UPPER(TRIM(hd."Subject Code")) AS subjectcode,
                f.employeenumber
            FROM historical_data hd
            JOIN faculty f
                ON UPPER(TRIM(SPLIT_PART(hd."Instructor", ',', 1))) = UPPER(TRIM(f.lastname))
               AND (
                   SPLIT_PART(TRIM(SPLIT_PART(hd."Instructor", ',', 2)), ' ', 1) = ''
                   OR UPPER(TRIM(f.firstname)) LIKE (UPPER(SPLIT_PART(TRIM(SPLIT_PART(hd."Instructor", ',', 2)), ' ', 1)) || '%%')
               )
            WHERE UPPER(REGEXP_REPLACE(hd."Program", '\\s+\\d+$', '')) = UPPER(%s)
              AND hd."Subject Code" IS NOT NULL
              AND TRIM(hd."Subject Code") != ''
              AND f.employeestatus != 'Archive'
            ORDER BY UPPER(TRIM(hd."Subject Code")), hd.academicyearid DESC
        """, (program,))

        for row in (hist_rows or []):
            code = (row.get('subjectcode') or '').strip()
            if code:
                result[code] = row['employeenumber']

        # ── Source 2: schedule_version — overwrites historical (more recent) ──
        sv_rows = query_db("""
            SELECT DISTINCT ON (UPPER(cs.subjectcode))
                UPPER(cs.subjectcode) AS subjectcode,
                sg.employeenumber
            FROM schedule_version sv
            JOIN schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c         ON cs.curriculumid        = c.curriculumid
            WHERE UPPER(c.programcode) = UPPER(%s)
              AND sv.status IN ('Published', 'Draft')
              AND sg.employeenumber IS NOT NULL
            ORDER BY UPPER(cs.subjectcode), sv.datecreated DESC
        """, (program,))

        for row in (sv_rows or []):
            code = (row.get('subjectcode') or '').strip()
            if code:
                result[code] = row['employeenumber']

        # ── Extra pass: shared subjects with no match yet ──
        # Subjects marked isshared = TRUE (e.g. GEED, PE, NSTP) are taught by
        # the SAME teacher regardless of program.  If no program-specific history
        # was found above, look across ALL programs so a GE/PE teacher recorded
        # under BSA schedules is still considered for BSIT generation.
        unmatched = [c for c in subject_codes if c.upper() not in
                     {k.upper() for k in result}]
        if unmatched:
            shared_sv = query_db("""
                SELECT DISTINCT ON (UPPER(cs.subjectcode))
                    UPPER(cs.subjectcode) AS subjectcode,
                    sg.employeenumber
                FROM schedule_version sv
                JOIN schedule sg          ON sv.scheduleid          = sg.scheduleid
                JOIN curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
                WHERE UPPER(cs.subjectcode) = ANY(%s)
                  AND cs.isshared = TRUE
                  AND sv.status IN ('Published', 'Draft')
                  AND sg.employeenumber IS NOT NULL
                ORDER BY UPPER(cs.subjectcode), sv.datecreated DESC
            """, (unmatched,))

            for row in (shared_sv or []):
                code = (row.get('subjectcode') or '').strip()
                if code and code.upper() not in {k.upper() for k in result}:
                    result[code] = row['employeenumber']

            # Also check historical_data for shared subjects across all programs
            shared_hist = query_db("""
                SELECT DISTINCT ON (UPPER(TRIM(hd."Subject Code")))
                    UPPER(TRIM(hd."Subject Code")) AS subjectcode,
                    f.employeenumber
                FROM historical_data hd
                JOIN faculty f
                    ON UPPER(TRIM(SPLIT_PART(hd."Instructor", ',', 1))) = UPPER(TRIM(f.lastname))
                   AND (
                       SPLIT_PART(TRIM(SPLIT_PART(hd."Instructor", ',', 2)), ' ', 1) = ''
                       OR UPPER(TRIM(f.firstname)) LIKE (UPPER(SPLIT_PART(TRIM(SPLIT_PART(hd."Instructor", ',', 2)), ' ', 1)) || '%%')
                   )
                JOIN curriculumsubject cs
                    ON UPPER(TRIM(hd."Subject Code")) = UPPER(cs.subjectcode)
                   AND cs.isshared = TRUE
                WHERE UPPER(TRIM(hd."Subject Code")) = ANY(%s)
                  AND f.employeestatus != 'Archive'
                ORDER BY UPPER(TRIM(hd."Subject Code")), hd.academicyearid DESC
            """, (unmatched,))

            for row in (shared_hist or []):
                code = (row.get('subjectcode') or '').strip()
                if code and code.upper() not in {k.upper() for k in result}:
                    result[code] = row['employeenumber']

        return result

    def fetch_cross_program_faculty(self, subject_codes: list) -> dict:
        """
        Priority 3 (cross-program) faculty lookup.

        For each subject code, return the most recent faculty member who taught it
        across ANY program — used as a fallback when no same-program history exists.
        This ensures that even niche or shared subjects (e.g. GEED, NSTP, PE) resolve
        to a real instructor from the historical record rather than a random pick.

        Sources (lower → higher priority so the most recent data wins):
          1. historical_data — SIS imports across all programs
          2. schedule_version — Published/Draft across all programs (more recent)

        Returns {SUBJECTCODE_UPPER: employeenumber}.
        """
        if not subject_codes:
            return {}

        upper_codes = [c.upper() for c in subject_codes]
        result: dict = {}

        # Source 1: historical_data — any program, most recent academic year
        hist_rows = query_db("""
            SELECT DISTINCT ON (UPPER(TRIM(hd."Subject Code")))
                UPPER(TRIM(hd."Subject Code")) AS subjectcode,
                f.employeenumber
            FROM historical_data hd
            JOIN faculty f
                ON UPPER(TRIM(SPLIT_PART(hd."Instructor", ',', 1))) = UPPER(TRIM(f.lastname))
               AND (
                   SPLIT_PART(TRIM(SPLIT_PART(hd."Instructor", ',', 2)), ' ', 1) = ''
                   OR UPPER(TRIM(f.firstname)) LIKE (
                       UPPER(SPLIT_PART(TRIM(SPLIT_PART(hd."Instructor", ',', 2)), ' ', 1)) || '%%'
                   )
               )
            WHERE UPPER(TRIM(hd."Subject Code")) = ANY(%s)
              AND hd."Subject Code" IS NOT NULL
              AND TRIM(hd."Subject Code") != ''
              AND f.employeestatus != 'Archive'
            ORDER BY UPPER(TRIM(hd."Subject Code")), hd.academicyearid DESC
        """, (upper_codes,))

        for row in (hist_rows or []):
            code = (row.get('subjectcode') or '').strip()
            if code:
                result[code] = row['employeenumber']

        # Source 2: schedule_version across all programs (overwrites — more recent)
        sv_rows = query_db("""
            SELECT DISTINCT ON (UPPER(cs.subjectcode))
                UPPER(cs.subjectcode) AS subjectcode,
                sg.employeenumber
            FROM schedule_version sv
            JOIN schedule sg          ON sv.scheduleid          = sg.scheduleid
            JOIN curriculumsubject cs ON sg.curriculumsubjectid = cs.curriculumsubjectid
            WHERE UPPER(cs.subjectcode) = ANY(%s)
              AND sv.status IN ('Published', 'Draft')
              AND sg.employeenumber IS NOT NULL
            ORDER BY UPPER(cs.subjectcode), sv.datecreated DESC
        """, (upper_codes,))

        for row in (sv_rows or []):
            code = (row.get('subjectcode') or '').strip()
            if code:
                result[code] = row['employeenumber']

        return result

    def fetch_published_room_faculty_slots(
        self, term: str, acad_year_id: str,
        exclude_program: str = '', exclude_year_level: int = None,
        exclude_subject_codes: list = None,
    ) -> tuple:
        """
        Load existing Published AND Draft room/faculty occupancies for the semester.

        Only sessions for the subjects actively being regenerated are excluded.
        Sessions for OTHER subjects in the same section (residual Published subjects from
        a previous curriculum that are not being regenerated) are kept as occupied so
        the generator does not double-book those rooms/faculty.

        exclude_subject_codes: subject codes being regenerated in the current run.
            When supplied, only sessions whose subjectcode is in this list are excluded
            for the current program+year. Sessions for OTHER subjects in the same section
            are treated as occupied. When None (legacy call), the entire section is excluded.
        """
        if not term or not acad_year_id:
            return defaultdict(list), defaultdict(list)
        rows = query_db("""
            SELECT ss.roomid,
                   sc.employeenumber          AS faculty_id,
                   ss.daydesc                 AS day,
                   ts_s.timevalue             AS start_time,
                   ts_e.timevalue             AS end_time,
                   UPPER(c.programcode)       AS programcode,
                   cs.yearlevel,
                   UPPER(cs.subjectcode)      AS subjectcode
            FROM   schedule_sessions ss
            JOIN   schedule_version sv  ON ss.versionid           = sv.versionid
            JOIN   schedule sc          ON sv.scheduleid           = sc.scheduleid
            JOIN   curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN   curriculum c         ON cs.curriculumid        = c.curriculumid
            JOIN   semester sem         ON sc.semesterid          = sem.semesterid
            LEFT JOIN timeslot ts_s     ON ss.starttimeid         = ts_s.timeid
            LEFT JOIN timeslot ts_e     ON ss.endtimeid           = ts_e.timeid
            WHERE  sem.academicyearid          = %s
              AND  UPPER(sem.semestertype)     = UPPER(%s)
              AND  sv.status                  IN ('Published', 'Draft')
              AND  ss.roomid                  IS NOT NULL
              AND  ss.daydesc                 IS NOT NULL
              AND  ts_s.timevalue             IS NOT NULL
              AND  ts_e.timevalue             IS NOT NULL
        """, (acad_year_id, term))

        excl_prog  = (exclude_program or '').strip().upper()
        excl_codes = {c.upper() for c in (exclude_subject_codes or [])}

        # Keyed by (id, day) → [(start, end)] so callers can do O(1) per-day lookups
        # without iterating wrong-day slots (critical for performance when many
        # schedules are already published in the same semester).
        room_slots    = defaultdict(list)   # (room_id, day) → [(start, end)]
        faculty_slots = defaultdict(list)   # (fac_id,  day) → [(start, end)]
        loaded = 0
        for r in (rows or []):
            row_prog = str(r['programcode']).upper()
            row_yr   = r['yearlevel']
            row_sc   = str(r.get('subjectcode') or '').upper()
            # Skip only sessions that ARE being regenerated for the current section.
            # Residual sessions for OTHER subjects in the same section must still block
            # so the new schedule does not reuse a room that will remain Published.
            if excl_prog and row_prog == excl_prog \
                    and exclude_year_level is not None \
                    and row_yr == exclude_year_level:
                if not excl_codes or row_sc in excl_codes:
                    continue   # this subject is being replaced — don't block
            day   = r['day']
            start = r['start_time']
            end   = r['end_time']
            if day and start and end:
                room_slots[(r['roomid'], day)].append((start, end))
                if r['faculty_id']:
                    faculty_slots[(r['faculty_id'], day)].append((start, end))
                loaded += 1
        print(f'[SCHED] Loaded {loaded} published/draft slots ({len(room_slots)} room-days, '
              f'{len(faculty_slots)} faculty-days) to block during generation '
              f'(excl {excl_prog} Yr{exclude_year_level} codes={len(excl_codes)})')
        return room_slots, faculty_slots

    def fetch_current_faculty_loads(self, term: str, acad_year_id: str,
                                    exclude_program: str = '',
                                    exclude_year_level: int = None) -> dict:
        """
        #9: Return {faculty_id: total_hours_already_committed} for the given term across
        ALL programs/sections that have Published or Draft schedules — REAL scheduled
        hours via the shared live-hours query (faculty_load.py), not credit units. This
        allows the builder to exclude faculty who have already hit their load cap.

        exclude_program + exclude_year_level: skip sessions for the section currently
        being regenerated.  Those records (Draft from a previous run, or the Published
        schedule being superseded) will be replaced, so counting them would inflate load
        figures and prevent the same faculty from being re-used in the new schedule.
        """
        if not term or not acad_year_id:
            return {}

        conn = get_db_connection()
        if conn is None:
            return {}
        try:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            loads = faculty_load.get_faculty_hours_batch(
                cur, acad_year_id, term,
                exclude_program=(exclude_program or '').strip().upper() or None,
                exclude_year_level=exclude_year_level)
            cur.close()
            return loads
        finally:
            conn.close()

    def fetch_historical_schedule(self, program: str, year_level: int, term: str,
                                  curriculum_year: str) -> list:
        """
        Retrieve the most recent published/draft schedule for this program/year/term,
        validated against the current curriculum subjects.
        Returns None when no historical schedule exists.
        """
        curr_rows = query_db("""
            SELECT cs.subjectcode, cs.subjectname, cs.lecturehours, cs.laboratoryhours,
                   cs.creditunits
            FROM curriculumsubject cs
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
            WHERE c.programcode = %s AND c.curriculumyear = %s
              AND cs.yearlevel = %s AND cs.semester = %s
        """, (program, curriculum_year, year_level, term))
        curr_subjects = {r['subjectcode']: r for r in (curr_rows or [])}
        if not curr_subjects:
            return None

        ver_rows = query_db("""
            SELECT sv.versionid
            FROM schedule_version sv
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
            JOIN semester sem ON sc.semesterid = sem.semesterid
            WHERE c.programcode = %s AND cs.yearlevel = %s AND sem.semestertype = %s
              AND sv.status IN ('Published', 'Draft')
            ORDER BY sv.datecreated DESC
            LIMIT 1
        """, (program, year_level, term))
        if not ver_rows:
            return None
        vid = ver_rows[0]['versionid']

        rows = query_db("""
            SELECT cs.subjectcode, sc.employeenumber AS faculty_id,
                   CONCAT(f.lastname, ', ', f.firstname) AS instructor,
                   c.programcode AS course,
                   ss.daydesc AS day, ts_s.timevalue AS start_time, ts_e.timevalue AS end_time,
                   r.roomname AS room, r.roomid AS room_id
            FROM schedule_sessions ss
            JOIN schedule_version sv ON ss.versionid = sv.versionid
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN curriculum c ON cs.curriculumid = c.curriculumid
            LEFT JOIN faculty f ON sc.employeenumber = f.employeenumber
            LEFT JOIN room r ON ss.roomid = r.roomid
            LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
            WHERE ss.versionid = %s
            ORDER BY cs.subjectcode, ts_s.timevalue
        """, (vid,))
        if not rows:
            return None

        grouped = {}
        for row in rows:
            code = row['subjectcode']
            if code not in curr_subjects:
                continue
            s_t = row['start_time'] if isinstance(row['start_time'], time) else time(7, 30)
            e_t = row['end_time']   if isinstance(row['end_time'],   time) else time(9,  0)
            key = (code, row['faculty_id'], s_t, e_t)
            if key not in grouped:
                subj = curr_subjects[code]
                lec  = subj['lecturehours']
                lab  = subj['laboratoryhours']
                grouped[key] = {
                    'subject_code':      code,
                    'description':       subj['subjectname'],
                    'lec_hours':         lec,
                    'lab_hours':         lab,
                    'units':             subj['creditunits'],
                    'course':            row['course'],
                    'class_type':        'Lab' if lab > 0 and lec == 0 else 'Lecture',
                    'total_subject_hrs': lec + lab,
                    'faculty_id':        row['faculty_id'],
                    'instructor':        row['instructor'] or 'TBA',
                    'room_id':           row['room_id'],
                    'room':              row['room'] or 'TBA',
                    'start_time':        s_t,
                    'end_time':          e_t,
                    'days_list':         [],
                    'day':               row['day'],
                    'is_historical':     True,
                }
            if row['day'] not in grouped[key]['days_list']:
                grouped[key]['days_list'].append(row['day'])

        if not grouped:
            return None

        result = []
        for entry in grouped.values():
            dl = entry['days_list']
            entry['days']  = '/'.join(d[:3].upper() for d in dl)
            entry['time']  = (f"{format_time_12h(entry['start_time'])} – "
                              f"{format_time_12h(entry['end_time'])}")
            entry['hours'] = str(entry['lec_hours'] + entry['lab_hours'])
            # Real elapsed hours (one meeting's duration × days/week it meets) — same
            # convention as _build_individual, so this retrieved schedule gets HC8-checked
            # against ACTUAL scheduled hours like every other path, not credit units.
            entry['duration_hrs'] = round(
                duration_hours(entry['start_time'], entry['end_time']) * max(1, len(dl)), 2)
            result.append(entry)
        return result

    # ── Allowed blocks for faculty ───────────────────────────────

    def _get_allowed_blocks_for_faculty(self, fac: dict, valid_blks: list) -> list:
        emp_status  = fac.get('employeestatus', '')
        designation = fac.get('designationid')
        et          = fac.get('employeetype', {})
        night_svc   = fac.get('nightteachingservice')

        regular_start = et.get('regular_start') or time(7, 30)
        regular_end   = et.get('regular_end')   or time(16, 30)

        allowed = []
        for (s, e) in valid_blks:
            if e <= regular_end and s >= regular_start:
                if emp_status != 'Part-Time':
                    allowed.append((s, e, 'regular'))
            elif s >= time(16, 30):
                if emp_status in ('Permanent', 'Temporary'):
                    if e <= time(21, 0):
                        allowed.append((s, e, 'pt'))
                elif designation is not None:
                    if night_svc and night_svc > 0:
                        if e <= time(18, 0):
                            allowed.append((s, e, 'pt'))
                elif emp_status == 'Part-Time':
                    if e <= time(21, 0):
                        allowed.append((s, e, 'pt'))

        if allowed:
            return allowed
        # When no block fits the faculty's declared windows (edge case for unusual
        # schedules), prefer blocks within normal teaching hours rather than returning
        # arbitrary night slots — this keeps HC1/HC2/HC3 violations to a minimum.
        daytime_fallback = [(s, e, 'regular') for (s, e) in valid_blks
                            if s >= time(7, 0) and e <= time(18, 0)]
        return daytime_fallback if daytime_fallback else [(s, e, 'any') for (s, e) in valid_blks]

    # ── Individual builder ───────────────────────────────────────

    def _build_individual(self, subjects, faculty_list, faculty_map, rooms,
                          historical_faculty: dict = None, preferences: dict = None,
                          subject_history: dict = None, existing_load: dict = None,
                          published_room_slots: dict = None,
                          published_faculty_slots: dict = None,
                          cross_program_faculty: dict = None):
        """
        Build one schedule candidate.

        Subjects with both lecture AND lab hours produce two entries each
        (one Lecture entry with full credit_units, one Lab entry with 0 units
        so load is not double-counted).

        The 'hours' field reflects actual scheduled contact hours per week:
          block_duration × number_of_days_per_week.

        Day pairing (MTH / TF / WS) is applied only when:
          • the time block is 1.5 hours, AND
          • the subject's lecture hours ≥ 3 (so two 1.5-hr sessions = 3 hrs/week).
          Labs always meet on a single day.

        published_room_slots / published_faculty_slots: pre-loaded occupancies from
          existing Published schedules for other sections.  Pre-seeding these dicts
          prevents the builder from assigning rooms or faculty that are already taken.
        """
        individual = []
        historical_faculty = historical_faculty or {}
        preferences        = preferences        or {}
        subject_history    = subject_history    or {}
        # #9: pre-loaded committed HOURS from other sections/programs for this term
        # (faculty_load.get_faculty_hours_batch — real scheduled hours, not credit units)
        _existing_load     = dict(existing_load or {})
        # Track nominal hours being assigned within this individual so load compounds
        # correctly. No real day/time exists yet at this point in gene construction, so
        # this uses each subject's NOMINAL catalog hours (tuitionhours/lecturehours+
        # laboratoryhours via faculty_load.get_subject_nominal_hours) as a stand-in —
        # the post-hoc CSP validator re-checks against each gene's REAL elapsed hours
        # once a day/time has actually been chosen (see duration_hrs below).
        _sched_units: dict = defaultdict(float)

        # HC7: Track night PT class count per designee faculty so the builder never
        # exceeds their cap (6 minus night office-service duties) during placement,
        # matching CSP validator logic.
        _night_cls_count: dict = defaultdict(int)

        # Track slots during building to eliminate hard overlaps in generated individuals.
        # Keyed by (id, day) for O(1) per-day lookups — avoids scanning all days when
        # the schedule is large (many published sessions from other programs/sections).
        faculty_slots = defaultdict(list)   # (fac_id,  day) → [(start, end)]
        room_slots    = defaultdict(list)   # (room_id, day) → [(start, end)]
        section_slots = defaultdict(list)   # day            → [(start, end)]

        # Pre-seed with Published/Draft occupancies from OTHER sections.
        # published_room_slots / published_faculty_slots are already (id, day) keyed.
        for key, _slots in (published_room_slots or {}).items():
            room_slots[key].extend(_slots)
        for key, _slots in (published_faculty_slots or {}).items():
            faculty_slots[key].extend(_slots)
            # HC7: pre-count night classes already committed by designees in other sections
            fac_id_key, day_key = key
            if day_key in WEEKDAYS and fac_id_key in faculty_map:
                if faculty_map[fac_id_key].get('designationid') is not None:
                    for (s, _e) in _slots:
                        if is_night_time(s):
                            _night_cls_count[fac_id_key] += 1

        def _has_overlap(fac_id, days, start, end, room_id):
            for d in days:
                for (fs, fe) in faculty_slots.get((fac_id, d), []):
                    if start < fe and end > fs:
                        return True
                for (rs, re) in room_slots.get((room_id, d), []):
                    if start < re and end > rs:
                        return True
                # Section-level: no two subjects may share a time slot
                for (ss, se) in section_slots.get(d, []):
                    if start < se and end > ss:
                        return True
            return False

        def _register(fac_id, days, start, end, room_id):
            for d in days:
                faculty_slots[(fac_id, d)].append((start, end))
                room_slots[(room_id, d)].append((start, end))
                section_slots[d].append((start, end))

        for sub in subjects:
            lec_hrs      = sub.get('lecturehours', 0)
            lab_hrs      = sub.get('laboratoryhours', 0)
            credit_units = sub.get('creditunits', 3)
            # Nominal hours for load-capacity purposes only (no real day/time chosen yet
            # at this point) — see faculty_load.get_subject_nominal_hours.
            nominal_hrs  = faculty_load.get_subject_nominal_hours(sub) or credit_units
            is_nstp_ou   = any(sub['subjectcode'].upper().startswith(p)
                               for p in SUNDAY_ALLOWED_PREFIXES)

            # ── Faculty selection (shared by lec + lab parts) ──

            # Build list of faculty qualified to teach this subject.
            # Specialization matching always runs so the builder never proactively
            # assigns a mis-specialised teacher (e.g. PE teacher to GEED).
            # HC_SPEC only controls whether the CSP *validator* rejects the final
            # schedule — it does not mean "ignore specs during assignment".
            def _is_qualified(fac):
                spec_name = (fac.get('specializationname') or '').strip()
                if not spec_name:
                    return True  # no specialization on record → no restriction
                return _spec_matches_subject(spec_name, sub['subjectcode'])

            qualified_faculty = [f for f in faculty_list if _is_qualified(f)]
            if not qualified_faculty:
                qualified_faculty = faculty_list  # fallback when no match found

            # #9: Further filter to faculty who still have load capacity for this subject.
            # `hrs` is the subject's NOMINAL hours (no real day/time chosen yet).
            def _has_load_capacity(fac, hrs):
                if not hrs:
                    return True  # lab parts have 0 hours here — no limit check needed
                fid = fac['employeenumber']
                et  = fac.get('employeetype', {})
                max_reg   = et.get('regularload') or 99
                max_pt    = et.get('parttimeload') or 0
                ts_sub    = et.get('teachingsubstitution') or 0
                max_total = max_reg + max_pt + ts_sub
                committed = _existing_load.get(fid, 0) + _sched_units.get(fid, 0)
                return (committed + hrs) <= max_total

            with_capacity = [f for f in qualified_faculty if _has_load_capacity(f, nominal_hrs)]
            if with_capacity:
                qualified_faculty = with_capacity
            # HC8 fallback: if every qualified faculty member has exhausted their load,
            # keep the full set so the subject is still placed — the CSP validator will
            # flag this as an HC8 violation and it will be visible as an error in the
            # generator result instead of producing an unplaceable schedule.

            pref       = preferences.get(sub['subjectcode'], {})
            pref_fnum  = pref.get('faculty')
            hist_fnum  = historical_faculty.get(sub['subjectcode'])

            # Discard preferred/historical faculty that fail the specialization check
            if pref_fnum and pref_fnum in faculty_map and not _is_qualified(faculty_map[pref_fnum]):
                pref_fnum = None
            if hist_fnum and hist_fnum in faculty_map and not _is_qualified(faculty_map[hist_fnum]):
                hist_fnum = None

            # P2b source: same program, any year level, any term
            wide_fnum = subject_history.get(sub['subjectcode'].upper())
            if wide_fnum and wide_fnum in faculty_map and not _is_qualified(faculty_map[wide_fnum]):
                wide_fnum = None

            # P3 source: same subject in any program
            cross_fnum = (cross_program_faculty or {}).get(sub['subjectcode'].upper())
            if cross_fnum and cross_fnum in faculty_map and not _is_qualified(faculty_map[cross_fnum]):
                cross_fnum = None

            chosen_fac = None

            # P1 — same subject + same program + same year level (historical > published > draft)
            if (pref_fnum and pref_fnum in faculty_map
                    and _has_load_capacity(faculty_map[pref_fnum], nominal_hrs)
                    and random.random() < 0.95):
                chosen_fac = faculty_map[pref_fnum]
            # P2a — same subject + same program + same term (any year level)
            elif (hist_fnum and hist_fnum in faculty_map
                    and _has_load_capacity(faculty_map[hist_fnum], nominal_hrs)
                    and random.random() < 0.90):
                chosen_fac = faculty_map[hist_fnum]
            # P2b — same subject + same program (any year level, any term)
            elif (wide_fnum and wide_fnum in faculty_map
                    and _has_load_capacity(faculty_map[wide_fnum], nominal_hrs)
                    and random.random() < 0.85):
                chosen_fac = faculty_map[wide_fnum]
            # P3 — same subject in any program
            elif (cross_fnum and cross_fnum in faculty_map
                    and _has_load_capacity(faculty_map[cross_fnum], nominal_hrs)
                    and random.random() < 0.78):
                chosen_fac = faculty_map[cross_fnum]
            # P4 — any qualified faculty member (specialization-filtered, load-checked)
            if chosen_fac is None:
                chosen_fac = random.choice(qualified_faculty)
            # #9: Track nominal hours committed within this schedule build
            if nominal_hrs > 0:
                _sched_units[chosen_fac['employeenumber']] += nominal_hrs

            # ── Determine parts (lecture only / lab only / both) ──
            # (class_type, target_hrs, is_lab_part, units_for_load)
            parts = []
            if lec_hrs > 0 and lab_hrs > 0:
                parts.append(('Lecture', lec_hrs, False, credit_units))
                parts.append(('Lab',     lab_hrs, True,  0))
            elif lab_hrs > 0:
                parts.append(('Lab', lab_hrs, True, credit_units))
            else:
                parts.append(('Lecture', lec_hrs or 3, False, credit_units))

            # Track the day chosen for the lecture part so the lab part
            # can be placed on the paired day (e.g. Wed lecture → Sat lab).
            _lec_day_chosen = None

            for (class_type, target_hrs, is_lab_part, units) in parts:

                # Valid time blocks for this part.
                # When day-pairing is on and lec_hrs >= 3, both options are offered:
                #   • 1.5h blocks  → scheduler will choose paired days (HC6)
                #   • full-length  → scheduler will choose a single day
                # Splitting is not forced; the GA picks whichever fits best given
                # faculty and room availability.
                _day_pair_on = bool(self._hc_cfg.get('hc_day_pairing_enabled', 1))
                if _day_pair_on and not is_lab_part and lec_hrs >= 3:
                    valid_blks = (get_blocks_for_hours(1.5)
                                  + get_blocks_for_hours(target_hrs, is_lab=False))
                else:
                    valid_blks = get_blocks_for_hours(target_hrs, is_lab=is_lab_part)

                # Room type — only filter by type when lab constraint is enabled
                if is_lab_part and self._enforce_lab_rooms:
                    req_type    = 'Laboratory'
                    valid_rooms = [r for r in rooms if r['roomtype'] == 'Laboratory'] or list(rooms)
                elif not is_lab_part:
                    req_type    = 'Lecture'
                    valid_rooms = [r for r in rooms if r['roomtype'] == 'Lecture'] or list(rooms)
                else:
                    req_type    = 'Lecture'
                    valid_rooms = list(rooms)

                # Room preference from preference map (re-checked per part type)
                pref_room_id = pref.get('room_id')
                pref_room    = next(
                    (r for r in valid_rooms if r['roomid'] == pref_room_id), None
                ) if pref_room_id else None

                # Faculty-allowed blocks
                allowed_blks = self._get_allowed_blocks_for_faculty(chosen_fac, valid_blks)
                # HC7: if this designee has already reached their night PT cap (6 minus
                # their night office-service duties), remove night blocks so this
                # placement cannot push them over the limit.
                _fac_id_build = chosen_fac['employeenumber']
                if chosen_fac.get('designationid') is not None:
                    _allowed_nights_build = max(0, 6 - int(chosen_fac.get('nightteachingservice') or 0))
                    if _night_cls_count[_fac_id_build] >= _allowed_nights_build:
                        _day_only = [(s, e, k) for s, e, k in allowed_blks if not is_night_time(s)]
                        if _day_only:
                            allowed_blks = _day_only
                regular_blks = [(s, e) for (s, e, k) in allowed_blks if k == 'regular']
                pt_blks      = [(s, e) for (s, e, k) in allowed_blks if k != 'regular']

                # Compute available weekday pool once (respects all_weekends HC4 scope)
                _avail_days = (
                    WEEKDAYS
                    if (self._nstp_force_sunday and self._weekend_day_scope == 'all_weekends')
                    else WEEKDAYS + ['Saturday']
                )

                # ── Slot selection ──────────────────────────────────────────
                # Phase 1: up to 60 random attempts (fast path — preserves diversity).
                # Phase 2: exhaustive systematic search over all (time × room × day)
                #          combinations — guarantees we always produce conflict-free output
                #          when ANY valid slot exists.  Only the true last-resort blind
                #          fallback (Phase 3) can still create an intra-individual conflict,
                #          and that only fires when the entire schedule space is occupied.
                # ────────────────────────────────────────────────────────────────────────

                def _candidate_days(_s, _e, _random=True):
                    """
                    Return all day combinations valid for this part.
                    When _random=True, returns a single random candidate (fast path).
                    When _random=False, returns a list of all candidates (systematic search).
                    """
                    _dur = duration_hours(_s, _e)
                    if is_nstp_ou and self._nstp_force_sunday:
                        return [['Sunday']]
                    if abs(_dur - 1.5) < 0.1 and lec_hrs >= 3 and not is_lab_part:
                        if _random:
                            return [list(random.choice(self._builder_pairs))]
                        return [list(p) for p in self._builder_pairs]
                    if is_lab_part and _lec_day_chosen and _day_pair_on:
                        _paired = None
                        for _p in self._builder_pairs:
                            if _lec_day_chosen in _p:
                                _paired = _p[1 - _p.index(_lec_day_chosen)]
                                break
                        return [[_paired]] if _paired else ([[random.choice(_avail_days)]] if _random else [[d] for d in _avail_days])
                    return [[random.choice(_avail_days)]] if _random else [[d] for d in _avail_days]

                start_t = end_t = chosen_room = days_list = None

                # Phase 1: random attempts (60 tries, fast)
                for _ in range(60):
                    if regular_blks and random.random() < 0.75:
                        _s, _e = random.choice(regular_blks)
                    elif pt_blks:
                        _s, _e = random.choice(pt_blks)
                    else:
                        _s, _e = random.choice(valid_blks)

                    _r    = pref_room if (pref_room and random.random() < 0.65) else random.choice(valid_rooms)
                    _days = _candidate_days(_s, _e, _random=True)[0]

                    if not _has_overlap(chosen_fac['employeenumber'], _days, _s, _e, _r['roomid']):
                        start_t, end_t, chosen_room, days_list = _s, _e, _r, _days
                        break

                # Phase 2: exhaustive systematic search (only if random phase failed)
                if start_t is None:
                    _blks_ordered = list(regular_blks or valid_blks)
                    random.shuffle(_blks_ordered)
                    _rooms_ordered = list(valid_rooms)
                    random.shuffle(_rooms_ordered)
                    for _s, _e in _blks_ordered:
                        if start_t is not None:
                            break
                        for _r in _rooms_ordered:
                            if start_t is not None:
                                break
                            for _days in _candidate_days(_s, _e, _random=False):
                                if not _has_overlap(chosen_fac['employeenumber'], _days, _s, _e, _r['roomid']):
                                    start_t, end_t, chosen_room, days_list = _s, _e, _r, _days
                                    break

                # Phase 3: last-resort blind fallback — only reachable when every
                # possible (time, room, day) combination is already occupied.
                if start_t is None:
                    _fb_blks = regular_blks or pt_blks or valid_blks
                    start_t, end_t = random.choice(_fb_blks)
                    days_list = _candidate_days(start_t, end_t, _random=True)[0]
                    if pref_room:
                        chosen_room = pref_room
                    elif is_lab_part and self._enforce_lab_rooms:
                        # HC_LAB: even in last-resort, prefer a laboratory room
                        _lab_fb = [r for r in rooms if r.get('roomtype', '') == 'Laboratory']
                        chosen_room = random.choice(_lab_fb) if _lab_fb else random.choice(valid_rooms)
                    else:
                        chosen_room = random.choice(valid_rooms)

                # ────────────────────────────────────────────────────────────────────

                # After lecture placement, record the day so the lab part can pair with it.
                if not is_lab_part and days_list:
                    _lec_day_chosen = days_list[0]

                # HC7: track night class count for designees so subsequent subjects
                # in this build respect the cap (see allowed_blks filter above).
                if (start_t and chosen_fac.get('designationid') is not None
                        and is_night_time(start_t) and days_list
                        and days_list[0] in WEEKDAYS):
                    _night_cls_count[chosen_fac['employeenumber']] += 1

                _register(chosen_fac['employeenumber'], days_list, start_t, end_t,
                          chosen_room['roomid'])

                hrs_str = str(lec_hrs + lab_hrs)
                # Real elapsed hours for THIS part, now that a day/time has actually been
                # chosen — one meeting's duration × how many days/week it meets. This is
                # what post-hoc load validation (HC8) and SC5 fitness use now, not `units`.
                duration_hrs = round(duration_hours(start_t, end_t) * len(days_list), 2)

                individual.append({
                    'subject_code':      sub['subjectcode'],
                    'description':       sub['subjectname'],
                    'lec_hours':         lec_hrs if class_type == 'Lecture' else 0,
                    'lab_hours':         lab_hrs if class_type == 'Lab'     else 0,
                    'units':             units,
                    'duration_hrs':      duration_hrs,
                    'course':            sub['offeringcode'],
                    'class_type':        class_type,
                    'total_subject_hrs': lec_hrs + lab_hrs,

                    'faculty_id':  chosen_fac['employeenumber'],
                    'instructor':  chosen_fac['fullname'],
                    'room_id':     chosen_room['roomid'],
                    'room':        chosen_room['roomname'],
                    'room_type':   chosen_room.get('roomtype', ''),

                    'start_time':  start_t,
                    'end_time':    end_t,
                    'days_list':   list(days_list),
                    'day':         days_list[0],

                    'time':  f"{format_time_12h(start_t)} – {format_time_12h(end_t)}",
                    'days':  '/'.join(d[:3].upper() for d in days_list),
                    'hours': hrs_str,

                    'is_historical':       (chosen_fac['employeenumber'] == hist_fnum),
                    'is_preferred_faculty': (chosen_fac['employeenumber'] == pref_fnum) if pref_fnum else False,
                    'is_preferred_room':    (chosen_room['roomid'] == pref_room_id) if pref_room_id else False,
                    'pref_source':          pref.get('source', ''),
                })

        self._repair_overlaps(
            individual, faculty_map,
            published_room_slots=published_room_slots,
            published_faculty_slots=published_faculty_slots,
        )
        return individual

    # ── Overlap repair ───────────────────────────────────────────

    def _repair_overlaps(self, individual: list, faculty_map: dict,
                         published_room_slots: dict = None,
                         published_faculty_slots: dict = None) -> list:
        """
        Multi-pass repair: detect and fix section, faculty, and room time conflicts
        by reassigning conflicting entries to a valid non-overlapping time+day.
        Runs up to 12 passes or until no overlap remains.
        Published/Draft slots from other sections are also checked so the repair
        never picks a time that is already occupied by an existing schedule.
        """
        _pub_rooms = published_room_slots  or {}
        _pub_facs  = published_faculty_slots or {}
        for _pass in range(8):
            fac_day     = defaultdict(list)   # (fac_id,  day) → [(idx, start, end)]
            room_day    = defaultdict(list)   # (room_id, day) → [(idx, start, end)]
            section_day = defaultdict(list)   # day            → [(idx, start, end)]

            for i, cls in enumerate(individual):
                fid = cls.get('faculty_id')
                rid = cls.get('room_id')
                for day in cls.get('days_list', [cls.get('day', '')]):
                    if not day:
                        continue
                    if fid:
                        fac_day[(fid, day)].append((i, cls['start_time'], cls['end_time']))
                    if rid:
                        room_day[(rid, day)].append((i, cls['start_time'], cls['end_time']))
                    section_day[day].append((i, cls['start_time'], cls['end_time']))

            fixed_any = False

            def _slot_free(gene_idx, gene, new_s, new_e, new_days):
                """Return True only if the proposed time+days conflict with nothing else.
                Checks both intra-individual slots and already-published/draft occupancies."""
                fid = gene.get('faculty_id')
                rid = gene.get('room_id')
                for d in new_days:
                    for (ix, ss, se) in section_day.get(d, []):
                        if ix != gene_idx and new_s < se and new_e > ss:
                            return False
                    for (ix, ss, se) in fac_day.get((fid, d), []):
                        if ix != gene_idx and new_s < se and new_e > ss:
                            return False
                    for (ix, ss, se) in room_day.get((rid, d), []):
                        if ix != gene_idx and new_s < se and new_e > ss:
                            return False
                    # Block slots already occupied in Published/Draft schedules.
                    # Dicts are (id, day) keyed — no day scan needed.
                    for (ps, pe) in _pub_rooms.get((rid, d), []):
                        if new_s < pe and new_e > ps:
                            return False
                    for (ps, pe) in _pub_facs.get((fid, d), []):
                        if new_s < pe and new_e > ps:
                            return False
                return True

            def _try_fix_gene(gene_idx):
                nonlocal fixed_any
                gene       = individual[gene_idx]
                is_lab     = gene.get('class_type') == 'Lab'
                is_nstp_ou = any(gene.get('subject_code', '').upper().startswith(p)
                                 for p in SUNDAY_ALLOWED_PREFIXES)
                lh         = gene.get('lec_hours', 0)
                labh       = gene.get('lab_hours', 0)
                target     = labh if is_lab else (lh or 3)

                _day_pair_on = bool(self._hc_cfg.get('hc_day_pairing_enabled', 1))
                if _day_pair_on and not is_lab and lh >= 3:
                    blks = (get_blocks_for_hours(1.5)
                            + get_blocks_for_hours(target, is_lab=False))
                else:
                    blks = get_blocks_for_hours(target, is_lab=is_lab)

                fac     = faculty_map.get(gene.get('faculty_id'), {})
                allowed = self._get_allowed_blocks_for_faculty(fac, blks)
                reg     = [(s, e) for (s, e, k) in allowed if k == 'regular']
                pt      = [(s, e) for (s, e, k) in allowed if k != 'regular']
                cands   = list(reg) + list(pt)
                if not cands:
                    cands = [(s, e) for (s, e) in blks]
                random.shuffle(cands)

                avail = WEEKDAYS if (
                    self._nstp_force_sunday and self._weekend_day_scope == 'all_weekends'
                ) else WEEKDAYS + ['Saturday']
                cur_days = gene.get('days_list', [gene.get('day', '')])

                # Build consistent (days, start, end) triples so a 1.5h block is
                # always paired with valid paired days and a full-length block is
                # always paired with a single day — never cross-combined.
                if is_nstp_ou and self._nstp_force_sunday:
                    slot_triples = [(['Sunday'], s, e) for (s, e) in cands]
                elif _day_pair_on and not is_lab and lh >= 3:
                    slot_triples = []
                    for s, e in cands:
                        if abs(duration_hours(s, e) - 1.5) < 0.1:
                            for pair in self._builder_pairs:
                                slot_triples.append((list(pair), s, e))
                        else:
                            for d in avail:
                                slot_triples.append(([d], s, e))
                else:
                    slot_triples = [([d], s, e) for d in avail for (s, e) in cands]

                # Try the current days first to minimise unnecessary changes
                cur_first  = [t for t in slot_triples if t[0] == cur_days]
                others     = [t for t in slot_triples if t[0] != cur_days]
                random.shuffle(others)
                ordered = cur_first + others

                for new_days, new_s, new_e in ordered:
                    if _slot_free(gene_idx, gene, new_s, new_e, new_days):
                        gene['start_time'] = new_s
                        gene['end_time']   = new_e
                        gene['time']  = f"{format_time_12h(new_s)} – {format_time_12h(new_e)}"
                        gene['hours'] = str(lh + labh)
                        gene['days_list'] = list(new_days)
                        gene['day']       = new_days[0]
                        gene['days']      = '/'.join(d[:3].upper() for d in new_days)
                        fixed_any = True
                        return

            # Collect all conflicting indices across section, faculty, and room
            conflicted = set()

            for slots in section_day.values():
                slots.sort(key=lambda x: x[1])
                for k in range(len(slots) - 1):
                    _, _s_a, e_a = slots[k]
                    idx_b, s_b, _ = slots[k + 1]
                    if s_b < e_a:
                        conflicted.add(idx_b)

            for slots in fac_day.values():
                slots.sort(key=lambda x: x[1])
                for k in range(len(slots) - 1):
                    _, _s_a, e_a = slots[k]
                    idx_b, s_b, _ = slots[k + 1]
                    if s_b < e_a:
                        conflicted.add(idx_b)

            for slots in room_day.values():
                slots.sort(key=lambda x: x[1])
                for k in range(len(slots) - 1):
                    _, _s_a, e_a = slots[k]
                    idx_b, s_b, _ = slots[k + 1]
                    if s_b < e_a:
                        conflicted.add(idx_b)

            for idx in sorted(conflicted):
                _try_fix_gene(idx)

            if not fixed_any:
                break

        return individual

    # ── Cross-section conflict repair ────────────────────────────

    def _repair_cross_conflicts(
        self, individual: list, rooms: list,
        published_room_slots: dict, published_faculty_slots: dict,
        faculty_map: dict = None,
    ) -> list:
        """
        Post-GA repair: after the genetic algorithm finishes, check every class in
        the winning individual against the pre-loaded published room/faculty occupancies.

        Repair strategy (in priority order):
          1. Room conflict only   → try an alternative room of the same type
          2. Faculty conflict     → try an alternative time/day that the faculty is free
                                    OR try an alternative qualified faculty member
          3. Both                 → try alternative room; if still conflicts, try alt faculty + room

        Debug lines prefixed with [REPAIR] are printed to the server console.
        """
        if not individual:
            return individual

        faculty_map = faculty_map or {}

        def _pub_room_free(rid, days, st, et):
            for day in days:
                for (ps, pe) in published_room_slots.get((rid, day), []):
                    if st < pe and et > ps:
                        return False
            return True

        def _pub_fac_free(fid, days, st, et):
            for day in days:
                for (ps, pe) in published_faculty_slots.get((fid, day), []):
                    if st < pe and et > ps:
                        return False
            return True

        def _ind_room_free(rid, days, st, et, exclude_idx):
            for j, other in enumerate(individual):
                if j == exclude_idx:
                    continue
                if other.get('room_id') != rid:
                    continue
                ost = other.get('start_time')
                oet = other.get('end_time')
                for oday in other.get('days_list', [other.get('day', '')]):
                    for day in days:
                        if oday == day and ost and oet and st < oet and et > ost:
                            return False
            return True

        def _ind_fac_free(fid, days, st, et, exclude_idx):
            for j, other in enumerate(individual):
                if j == exclude_idx:
                    continue
                if other.get('faculty_id') != fid:
                    continue
                ost = other.get('start_time')
                oet = other.get('end_time')
                for oday in other.get('days_list', [other.get('day', '')]):
                    for day in days:
                        if oday == day and ost and oet and st < oet and et > ost:
                            return False
            return True

        def _ind_sec_free(days, st, et, exclude_idx):
            for j, other in enumerate(individual):
                if j == exclude_idx:
                    continue
                ost = other.get('start_time')
                oet = other.get('end_time')
                for oday in other.get('days_list', [other.get('day', '')]):
                    for day in days:
                        if oday == day and ost and oet and st < oet and et > ost:
                            return False
            return True

        def _pub_conflict_desc(fid, rid, days, st, et):
            descs = []
            for day in days:
                for (ps, pe) in published_room_slots.get((rid, day), []):
                    if st < pe and et > ps:
                        descs.append(f'Room {rid} occupied on {day} {ps}–{pe}')
                for (ps, pe) in published_faculty_slots.get((fid, day), []):
                    if st < pe and et > ps:
                        descs.append(f'Faculty {fid} occupied on {day} {ps}–{pe}')
            return '; '.join(descs) if descs else 'unknown conflict'

        _day_pair_on = bool(self._hc_cfg.get('hc_day_pairing_enabled', 1))
        _avail_days  = WEEKDAYS + (['Saturday'] if not self._nstp_force_sunday else [])

        def _slot_triples_for(cls):
            """All valid (days, start, end) triples for a class."""
            is_lab  = cls.get('class_type') == 'Lab'
            lh      = cls.get('lec_hours', 0)
            labh    = cls.get('lab_hours', 0)
            target  = labh if is_lab else (lh or 3)
            is_nstp = any(cls.get('subject_code', '').upper().startswith(p)
                          for p in SUNDAY_ALLOWED_PREFIXES)
            if _day_pair_on and not is_lab and lh >= 3:
                blks = get_blocks_for_hours(1.5) + get_blocks_for_hours(target, is_lab=False)
            else:
                blks = get_blocks_for_hours(target, is_lab=is_lab)
            triples = []
            if is_nstp and self._nstp_force_sunday:
                for s, e in blks:
                    triples.append((['Sunday'], s, e))
            elif _day_pair_on and not is_lab and lh >= 3:
                for s, e in blks:
                    dur = duration_hours(s, e)
                    if abs(dur - 1.5) < 0.1:
                        for pair in self._builder_pairs:
                            triples.append((list(pair), s, e))
                    else:
                        for d in _avail_days:
                            triples.append(([d], s, e))
            else:
                for d in _avail_days:
                    for s, e in blks:
                        triples.append(([d], s, e))
            random.shuffle(triples)
            return triples

        for cls_idx, cls in enumerate(individual):
            fid   = cls.get('faculty_id')
            rid   = cls.get('room_id')
            days  = cls.get('days_list') or [cls.get('day', '')]
            st    = cls.get('start_time')
            et    = cls.get('end_time')
            scode = (cls.get('subject_code') or '').upper()
            if not (st and et and days):
                continue

            room_conflict = rid is not None and not _pub_room_free(rid, days, st, et)
            fac_conflict  = fid is not None and not _pub_fac_free(fid, days, st, et)

            if not room_conflict and not fac_conflict:
                continue

            conflict_desc = _pub_conflict_desc(fid, rid, days, st, et)
            print(f'[REPAIR] {scode} has cross-section conflict → {conflict_desc}')

            fixed = False

            # ── Strategy 1: try alternative room (same time/day, different room) ──
            if not fac_conflict:
                rtype = cls.get('room_type', '')
                alt_rooms = [r for r in rooms if (not rtype or r.get('roomtype') == rtype)]
                if not alt_rooms:
                    alt_rooms = list(rooms)
                random.shuffle(alt_rooms)
                for alt_r in alt_rooms:
                    alt_rid = alt_r['roomid']
                    if alt_rid == rid:
                        continue
                    if (_pub_room_free(alt_rid, days, st, et)
                            and _ind_room_free(alt_rid, days, st, et, cls_idx)):
                        print(f'[REPAIR]   Fixed {scode}: room {rid}→{alt_rid} ({alt_r["roomname"]})')
                        cls['room_id'] = alt_rid
                        cls['room']    = alt_r['roomname']
                        fixed = True
                        break

            # ── Strategy 2: try alternative time/day for the same faculty ──
            if not fixed and fac_conflict:
                rtype = cls.get('room_type', '')
                alt_rooms = [r for r in rooms if (not rtype or r.get('roomtype') == rtype)] or list(rooms)
                for new_days, new_s, new_e in _slot_triples_for(cls):
                    if not _pub_fac_free(fid, new_days, new_s, new_e):
                        continue  # faculty still busy at new time
                    if not _ind_fac_free(fid, new_days, new_s, new_e, cls_idx):
                        continue  # intra-individual faculty clash
                    if not _ind_sec_free(new_days, new_s, new_e, cls_idx):
                        continue  # intra-individual section clash
                    # Find a room free at the new time
                    random.shuffle(alt_rooms)
                    for alt_r in alt_rooms:
                        alt_rid = alt_r['roomid']
                        if (_pub_room_free(alt_rid, new_days, new_s, new_e)
                                and _ind_room_free(alt_rid, new_days, new_s, new_e, cls_idx)):
                            print(f'[REPAIR]   Fixed {scode}: time {st}–{et} {days}→{new_s}–{new_e} {new_days}, room→{alt_r["roomname"]}')
                            cls['start_time'] = new_s
                            cls['end_time']   = new_e
                            cls['days_list']  = list(new_days)
                            cls['day']        = new_days[0]
                            cls['days']       = '/'.join(d[:3].upper() for d in new_days)
                            cls['time']       = f"{format_time_12h(new_s)} – {format_time_12h(new_e)}"
                            cls['room_id']    = alt_rid
                            cls['room']       = alt_r['roomname']
                            fixed = True
                            break
                    if fixed:
                        break

            if not fixed:
                print(f'[REPAIR]   UNRESOLVED {scode}: no conflict-free slot found — '
                      f'faculty {fid} or room space exhausted by published schedules.')

        return individual

    # ── Fitness scoring ──────────────────────────────────────────

    def _fitness(self, individual, faculty_map, existing_load: dict = None):
        score = 1000

        # SC8: Reward assignments that match historical / published / manual preferences
        for cls in individual:
            if cls.get('is_preferred_faculty'):
                score += 12
            if cls.get('is_preferred_room'):
                score += 8

        violations   = self.csp.validate(individual, faculty_map, existing_load=existing_load)
        score       -= len(violations) * 200

        faculty_schedule = defaultdict(list)
        for cls in individual:
            faculty_schedule[cls['faculty_id']].append(cls)

        for fnum, classes in faculty_schedule.items():
            fac = faculty_map.get(fnum, {})
            et  = fac.get('employeetype', {})

            day_counts    = defaultdict(float)
            total_pt_hrs  = 0
            sat_count = 0
            sun_count = 0

            sorted_cls = sorted(classes, key=lambda c: (c['day'], minutes(c['start_time'])))

            for i, cls in enumerate(sorted_cls):
                if is_night_time(cls['start_time']):
                    score -= 15
                if not is_daytime_block(cls['start_time'], cls['end_time']):
                    score -= 20

                _hrs = cls.get('duration_hrs', 0) or 0
                day_counts[cls['day']] += _hrs

                reg_end = et.get('regular_end') or time(16, 30)
                if cls['day'] in WEEKDAYS and cls['end_time'] > reg_end:
                    total_pt_hrs += _hrs

                if cls['day'] == 'Saturday':
                    sat_count += 1
                if cls['day'] == 'Sunday':
                    sun_count += 1

                if i > 0 and sorted_cls[i-1]['day'] == cls['day']:
                    gap = minutes(cls['start_time']) - minutes(sorted_cls[i-1]['end_time'])
                    if gap > 90:
                        score -= 10
                    consec = minutes(cls['end_time']) - minutes(sorted_cls[i-1]['start_time'])
                    if consec >= 240:
                        score -= 30

            if day_counts:
                avg = sum(day_counts.values()) / len(day_counts)
                for cnt in day_counts.values():
                    if cnt > avg * 2:
                        score -= 10

            max_pt = et.get('parttimeload') or 0
            if max_pt:
                score -= abs(total_pt_hrs - max_pt) * 10

            if abs(sat_count - sun_count) > 1:
                score -= 10

        return score, len(violations)

    # ── Mutation ─────────────────────────────────────────────────

    def _mutate(self, child, subjects_by_code, faculty_list, faculty_map, rooms,
                historical_faculty: dict = None, preferences: dict = None,
                subject_history: dict = None,
                published_room_slots: dict = None,
                published_faculty_slots: dict = None,
                cross_program_faculty: dict = None):
        historical_faculty = historical_faculty or {}
        preferences        = preferences        or {}
        subject_history    = subject_history    or {}
        if not child:
            return child

        idx        = random.randint(0, len(child) - 1)
        gene       = child[idx]
        orig_gene  = {**gene, 'days_list': list(gene.get('days_list', []))}  # saved for published-conflict rollback
        sub_code   = gene['subject_code']
        class_type = gene.get('class_type', 'Lecture')
        is_lab_part = (class_type == 'Lab')
        is_nstp_ou  = any(sub_code.upper().startswith(p) for p in SUNDAY_ALLOWED_PREFIXES)

        sub = subjects_by_code.get(sub_code)
        if not sub:
            return child

        lec_hrs    = sub.get('lecturehours', 0)
        lab_hrs    = sub.get('laboratoryhours', 0)
        target_hrs = lab_hrs if is_lab_part else (lec_hrs or 3)

        # Mirror _build_individual: offer both split (1.5h) and single-session
        # blocks when pairing is on so mutations also have the same flexibility.
        _day_pair_on = bool(self._hc_cfg.get('hc_day_pairing_enabled', 1))
        if _day_pair_on and not is_lab_part and lec_hrs >= 3:
            valid_blks = (get_blocks_for_hours(1.5)
                          + get_blocks_for_hours(target_hrs, is_lab=False))
        else:
            valid_blks = get_blocks_for_hours(target_hrs, is_lab=is_lab_part)

        mutation_type = random.choice(['time', 'day', 'faculty', 'room'])

        if mutation_type == 'faculty':
            # Same rule as _build_individual: always check specs so mutations
            # never re-introduce a mis-specialised teacher.  HC_SPEC only controls
            # whether the CSP rejects the schedule, not what the builder prefers.
            def _mut_qualified(fac):
                spec_name = (fac.get('specializationname') or '').strip()
                if not spec_name:
                    return True
                return _spec_matches_subject(spec_name, sub_code)

            qualified_faculty = [f for f in faculty_list if _mut_qualified(f)]
            if not qualified_faculty:
                qualified_faculty = faculty_list

            pref      = preferences.get(sub_code, {})
            pref_fnum = pref.get('faculty')
            hist_fnum = historical_faculty.get(sub_code)

            # Discard unqualified preferred/historical candidates
            if pref_fnum and pref_fnum in faculty_map and not _mut_qualified(faculty_map[pref_fnum]):
                pref_fnum = None
            if hist_fnum and hist_fnum in faculty_map and not _mut_qualified(faculty_map[hist_fnum]):
                hist_fnum = None

            # P2b source: same program, any year level, any term
            wide_fnum = subject_history.get(sub_code.upper())
            if wide_fnum and wide_fnum in faculty_map and not _mut_qualified(faculty_map[wide_fnum]):
                wide_fnum = None

            # P3 source: same subject in any program
            cross_fnum = (cross_program_faculty or {}).get(sub_code.upper())
            if cross_fnum and cross_fnum in faculty_map and not _mut_qualified(faculty_map[cross_fnum]):
                cross_fnum = None

            chosen_fac = None
            # P1 — same subject + same program + same year level
            if pref_fnum and pref_fnum in faculty_map and random.random() < 0.95:
                chosen_fac = faculty_map[pref_fnum]
            # P2a — same subject + same program + same term (any year level)
            elif hist_fnum and hist_fnum in faculty_map and random.random() < 0.90:
                chosen_fac = faculty_map[hist_fnum]
            # P2b — same subject + same program (any year level, any term)
            elif wide_fnum and wide_fnum in faculty_map and random.random() < 0.85:
                chosen_fac = faculty_map[wide_fnum]
            # P3 — same subject in any program
            elif cross_fnum and cross_fnum in faculty_map and random.random() < 0.78:
                chosen_fac = faculty_map[cross_fnum]
            # P4 — any qualified faculty member
            if chosen_fac is None:
                chosen_fac = random.choice(qualified_faculty)
            gene['faculty_id']           = chosen_fac['employeenumber']
            gene['instructor']           = chosen_fac['fullname']
            gene['is_historical']        = (chosen_fac['employeenumber'] == hist_fnum)
            gene['is_preferred_faculty'] = (chosen_fac['employeenumber'] == pref_fnum) if pref_fnum else False

        elif mutation_type == 'time':
            fnum = gene.get('faculty_id')
            fac  = faculty_map.get(fnum) or random.choice(faculty_list)
            allowed_blks = self._get_allowed_blocks_for_faculty(fac, valid_blks)
            regular_blks = [(s, e) for (s, e, k) in allowed_blks if k == 'regular']
            pt_blks      = [(s, e) for (s, e, k) in allowed_blks if k != 'regular']
            if regular_blks and random.random() < 0.75:
                start_t, end_t = random.choice(regular_blks)
            elif pt_blks:
                start_t, end_t = random.choice(pt_blks)
            else:
                start_t, end_t = random.choice(valid_blks)
            gene['start_time'] = start_t
            gene['end_time']   = end_t
            gene['time']       = f"{format_time_12h(start_t)} – {format_time_12h(end_t)}"
            gene['hours'] = str(gene.get('lec_hours', 0) + gene.get('lab_hours', 0))
            # Sync days_list with the new block's duration so paired/single stays consistent.
            if _day_pair_on and not is_lab_part and lec_hrs >= 3:
                _new_dur = duration_hours(start_t, end_t)
                _mut_avail = (
                    WEEKDAYS if (self._nstp_force_sunday and self._weekend_day_scope == 'all_weekends')
                    else WEEKDAYS + ['Saturday']
                )
                if abs(_new_dur - 1.5) < 0.1:
                    # New block is 1.5h → must be on paired days
                    _pair = random.choice(self._builder_pairs)
                    gene['days_list'] = list(_pair)
                    gene['day']  = gene['days_list'][0]
                    gene['days'] = '/'.join(d[:3].upper() for d in gene['days_list'])
                elif len(gene.get('days_list') or []) > 1:
                    # New block is full-length but gene still carries paired days → single day
                    gene['days_list'] = [random.choice(_mut_avail)]
                    gene['day']  = gene['days_list'][0]
                    gene['days'] = gene['days_list'][0][:3].upper()

        elif mutation_type == 'day':
            _avail_days = (
                WEEKDAYS
                if (self._nstp_force_sunday and self._weekend_day_scope == 'all_weekends')
                else WEEKDAYS + ['Saturday']
            )
            # Use the gene's current block duration so paired/single stays consistent:
            # a 1.5h gene stays on paired days; a full-length gene stays on a single day.
            _gene_s = gene.get('start_time')
            _gene_e = gene.get('end_time')
            _cur_dur = (duration_hours(_gene_s, _gene_e)
                        if _gene_s and _gene_e else target_hrs)
            if is_nstp_ou and self._nstp_force_sunday:
                days_list = ['Sunday']
            elif (_day_pair_on and not is_lab_part and lec_hrs >= 3
                  and abs(_cur_dur - 1.5) < 0.1):
                # Gene is a 1.5h paired block — must keep paired days
                _pair = random.choice(self._builder_pairs)
                days_list = list(_pair)
            else:
                days_list = [random.choice(_avail_days)]
            gene['days_list'] = days_list
            gene['day']       = days_list[0]
            gene['days']      = '/'.join(d[:3].upper() for d in days_list)
            gene['hours'] = str(gene.get('lec_hours', 0) + gene.get('lab_hours', 0))

        elif mutation_type == 'room':
            if is_lab_part and self._enforce_lab_rooms:
                valid_rooms = [r for r in rooms if r['roomtype'] == 'Laboratory'] or list(rooms)
            elif not is_lab_part:
                valid_rooms = [r for r in rooms if r['roomtype'] == 'Lecture'] or list(rooms)
            else:
                valid_rooms = list(rooms)
            pref     = preferences.get(sub_code, {})
            pref_rid = pref.get('room_id')
            pref_r   = next((rm for rm in valid_rooms if rm['roomid'] == pref_rid), None) if pref_rid else None
            if pref_r and random.random() < 0.65:
                r = pref_r
            else:
                r = random.choice(valid_rooms)
            gene['room_id']          = r['roomid']
            gene['room']             = r['roomname']
            gene['is_preferred_room'] = (r['roomid'] == pref_rid) if pref_rid else False

        # Roll back if the mutation landed in a Published/Draft occupied slot.
        # This prevents mutations from introducing cross-section conflicts that
        # _repair_overlaps (which only sees intra-individual slots) cannot fix.
        _pub_rooms = published_room_slots  or {}
        _pub_facs  = published_faculty_slots or {}
        _new_rid   = gene.get('room_id')
        _new_fid   = gene.get('faculty_id')
        _new_days  = gene.get('days_list') or [gene.get('day', '')]
        _new_s     = gene.get('start_time')
        _new_e     = gene.get('end_time')
        if _new_s and _new_e and _new_days:
            _pub_conflict = False
            for _d in _new_days:
                # (id, day) keyed — O(1) lookup, no day scan needed
                for (_ps, _pe) in _pub_rooms.get((_new_rid, _d), []):
                    if _new_s < _pe and _new_e > _ps:
                        _pub_conflict = True
                        break
                if not _pub_conflict:
                    for (_ps, _pe) in _pub_facs.get((_new_fid, _d), []):
                        if _new_s < _pe and _new_e > _ps:
                            _pub_conflict = True
                            break
                if _pub_conflict:
                    break
            if _pub_conflict:
                child[idx] = orig_gene   # revert to pre-mutation state

        return child

    # ── Main entry point ─────────────────────────────────────────

    def generate_draft(self, program, year_level, term, curriculum,
                       use_historical=False, acad_year_id: str = ''):
        try:
            # Reload constraint config on every call so admin Settings changes
            # take effect immediately without a server restart.
            try:
                self._hc_cfg = load_scheduler_config()
            except Exception:
                pass  # keep existing config if the DB is temporarily unavailable
            self.csp = CSPValidator(config=self._hc_cfg)
            raw_pairs = self._hc_cfg.get('hc_day_pairs', '')
            self._builder_pairs = (
                _parse_day_pairs(raw_pairs) if raw_pairs
                else [list(p) for p in DAY_PAIRS.values()]
            )
            weekend_on = bool(self._hc_cfg.get('hc_weekend_enabled', 1))
            subj_restr = self._hc_cfg.get('hc_weekend_subject', 'nstp_only')
            self._nstp_force_sunday = weekend_on and (subj_restr != 'all_allowed')
            self._weekend_day_scope = self._hc_cfg.get('hc_weekend_day', 'sunday_only')
            self._enforce_lab_rooms = bool(self._hc_cfg.get('hc_lab_session_enabled', 1))
            self._spec_enabled      = bool(self._hc_cfg.get('hc_faculty_spec_enabled', 1))

            subjects, faculty_list, faculty_map, rooms = self.fetch_data(
                program, year_level, term, curriculum
            )

            if not subjects:
                return {"success": False, "error": "No subjects found for this curriculum/year/term."}
            if not faculty_list:
                return {"success": False, "error": "No active faculty in the database."}
            if not rooms:
                return {"success": False, "error": "No rooms defined in the database."}

            # Retrieve and return previous schedule when requested
            if use_historical:
                hist_sched = self.fetch_historical_schedule(
                    program, year_level, term, curriculum
                )
                if hist_sched:
                    violations = self.csp.validate(hist_sched, faculty_map)
                    return {
                        "success":        True,
                        "schedule_data":  hist_sched,
                        "violations":     violations,
                        "conflict_count": len(violations),
                    }

            subjects_by_code = {s['subjectcode']: s for s in subjects}

            # Always seed GA with historical teachers from same term
            historical_faculty = self.fetch_historical_faculty(program, term)

            # CBR: supplement with faculty hints from the most similar historical_data case.
            # fetch_historical_faculty() only filters by program + term (ignores year level);
            # CBR also weighs year level and academic year, giving more targeted hints.
            # Merge strategy: historical_faculty (recency-biased) overrides CBR where both exist.
            ay_rows    = query_db("SELECT academicyearid FROM academicyear ORDER BY yearstart DESC LIMIT 1")
            current_ay = acad_year_id or (ay_rows[0]['academicyearid'] if ay_rows else '')
            cbr_faculty = CaseBasedRetriever().retrieve_best_case_faculty(
                program, year_level, term, current_ay
            )
            merged_faculty = {**cbr_faculty, **historical_faculty}

            # Comprehensive preference map: historical_data (P1) > Published (P2) > Draft (P3)
            # Used to seed faculty AND room assignments with proven historical patterns.
            preferences = self.fetch_all_preferences(program, year_level, term)

            # Subject-level faculty history — layered lookup to drive the 4-level priority:
            #   P2b: same subject + same program, any year level / any term
            #   P3 : same subject in any program (cross-program fallback)
            # Together these ensure a real historical instructor is always tried before
            # falling back to a random qualified faculty member (P4).
            all_subject_codes     = [s['subjectcode'] for s in subjects]
            subject_history       = self.fetch_subject_wide_faculty(program, all_subject_codes)
            cross_program_faculty = self.fetch_cross_program_faculty(all_subject_codes)

            # #9: Pre-load current semester faculty loads from existing scheduled sections.
            # Exclude the section being regenerated so its old Draft/Published units don't
            # count against the same faculty when we re-assign them in the new schedule.
            existing_load = self.fetch_current_faculty_loads(
                term, current_ay,
                exclude_program=program,
                exclude_year_level=year_level,
            )

            # Pre-load existing Published/Draft room/faculty occupancies for conflict blocking.
            # Pass the current subject codes so that ONLY sessions being regenerated are
            # excluded — residual Published sessions for other subjects in the same section
            # remain blocked and the generator will not double-book those rooms/faculty.
            published_room_slots, published_faculty_slots = \
                self.fetch_published_room_faculty_slots(
                    term, current_ay,
                    exclude_program=program, exclude_year_level=year_level,
                    exclude_subject_codes=all_subject_codes,
                )

            # Pre-filter rooms that have zero free standard time slots for this semester.
            # Rooms fully occupied across every STANDARD_BLOCKS × day combination are
            # excluded from candidate selection rather than being tried and rejected.
            rooms = _exclude_fully_booked_rooms(rooms, published_room_slots)
            if not rooms:
                return {"success": False,
                        "error": "All rooms are fully booked for the selected semester. "
                                 "No available room exists to generate a schedule."}

            # Pre-filter faculty whose total committed load for this semester is already at
            # or above their configured maximum.  faculty_map is kept intact so that the
            # CSP validator (which runs after the GA) can still read constraints for any
            # faculty referenced in historical preferences that happen to be at max load.
            fully_loaded = {
                fid for fid, committed in existing_load.items()
                if _faculty_is_at_max_load(faculty_map.get(fid, {}), committed)
            }
            if fully_loaded:
                faculty_list = [f for f in faculty_list
                                if f['employeenumber'] not in fully_loaded]
                print(f'[SCHED] Pre-filtered {len(fully_loaded)} faculty at max load '
                      f'for {current_ay} {term}.')
            if not faculty_list:
                return {"success": False,
                        "error": "All faculty have reached their maximum teaching load "
                                 "for the selected semester."}

            POP_SIZE      = 50
            GENERATIONS   = 150
            MUTATION_RATE = 0.30
            ELITE_RATIO   = 0.35
            MAX_ATTEMPTS  = 5   # initial run + up to 4 restarts with increasing diversity

            best_schedule_global    = None
            least_violations_global = 9999
            best_score_global       = -999999

            for attempt in range(MAX_ATTEMPTS):
                # Attempt 0: seed half the population with historical preferences.
                # Later attempts: progressively reduce seeding to encourage diversity
                # and escape local optima where the same conflict keeps reappearing.
                if attempt == 0:
                    seed_cutoff = POP_SIZE // 2
                elif attempt <= 2:
                    seed_cutoff = POP_SIZE // 4
                else:
                    seed_cutoff = POP_SIZE // 8   # mostly random restarts for later attempts

                population = []
                for i in range(POP_SIZE):
                    hist       = merged_faculty if i < seed_cutoff else {}
                    prefs_seed = preferences    if i < seed_cutoff else {}
                    population.append(
                        self._build_individual(
                            subjects, faculty_list, faculty_map, rooms,
                            hist, prefs_seed, subject_history,
                            existing_load=existing_load,
                            published_room_slots=published_room_slots,
                            published_faculty_slots=published_faculty_slots,
                            cross_program_faculty=cross_program_faculty,
                        )
                    )

                best_schedule    = None
                least_violations = 9999
                best_score       = -999999

                for gen in range(GENERATIONS):
                    scored = []
                    for ind in population:
                        score, n_violations = self._fitness(ind, faculty_map,
                                                             existing_load=existing_load)
                        scored.append((score, n_violations, ind))

                        if n_violations == 0 and score > best_score:
                            best_score       = score
                            best_schedule    = [{**g, 'days_list': list(g.get('days_list', []))} for g in ind]
                            least_violations = 0

                        if n_violations < least_violations:
                            least_violations = n_violations
                            best_schedule    = [{**g, 'days_list': list(g.get('days_list', []))} for g in ind]
                            best_score       = score

                    # Stop early once conflict-free and sufficiently evolved
                    if least_violations == 0 and gen >= 5:
                        break

                    scored.sort(key=lambda x: (x[1], -x[0]))
                    elite_n   = max(2, int(POP_SIZE * ELITE_RATIO))
                    survivors = [x[2] for x in scored[:elite_n]]

                    new_pop = list(survivors)
                    while len(new_pop) < POP_SIZE:
                        p1 = random.choice(survivors)
                        p2 = random.choice(survivors)
                        # Use actual individual length (may be > len(subjects) due to lec+lab split)
                        split = random.randint(1, max(1, len(p1) - 1))
                        child = [{**g, 'days_list': list(g.get('days_list', []))} for g in p1[:split]] + \
                                [{**g, 'days_list': list(g.get('days_list', []))} for g in p2[split:]]

                        if random.random() < MUTATION_RATE:
                            child = self._mutate(
                                child, subjects_by_code, faculty_list, faculty_map, rooms,
                                historical_faculty, preferences, subject_history,
                                published_room_slots=published_room_slots,
                                published_faculty_slots=published_faculty_slots,
                                cross_program_faculty=cross_program_faculty,
                            )
                        self._repair_overlaps(
                            child, faculty_map,
                            published_room_slots=published_room_slots,
                            published_faculty_slots=published_faculty_slots,
                        )
                        new_pop.append(child)

                    population = new_pop

                # Keep the global best across all attempts
                if least_violations < least_violations_global or (
                    least_violations == least_violations_global
                    and best_score > best_score_global
                ):
                    best_schedule_global    = best_schedule
                    least_violations_global = least_violations
                    best_score_global       = best_score

                if least_violations_global == 0:
                    break   # Conflict-free schedule found — no more restarts needed

            # ── Final validation ──────────────────────────────────────
            # Pass existing_load so HC8 counts cross-section units — matching what
            # the Manual Editor shows as the faculty's total teaching load.
            final_violations = self.csp.validate(
                best_schedule_global, faculty_map, existing_load=existing_load
            )

            # Separate hard constraint failures from advisory warnings.
            # HC_SPEC (specialization mismatch) is advisory — generation still succeeds;
            # the warning is surfaced in the UI so the Academic Head can review assignments.
            hard_violations     = [v for v in final_violations if v.get('severity') != 'warning']
            advisory_violations = [v for v in final_violations if v.get('severity') == 'warning']

            if hard_violations:
                # Build a map from rule → affected subjects for clear error reporting
                by_rule: dict = {}
                for v in hard_violations:
                    rule = v.get('rule', '?')
                    subj = (v.get('subject') or '').strip()
                    by_rule.setdefault(rule, set())
                    if subj:
                        by_rule[rule].add(subj)

                def _subj_str(rule: str) -> str:
                    codes = sorted(by_rule.get(rule, set()))
                    return f' ({", ".join(codes)})' if codes else ''

                reasons = []
                if 'HC11' in by_rule:
                    reasons.append(
                        'not enough distinct time slots for all subjects in this section'
                        + _subj_str('HC11')
                    )
                if 'HC9' in by_rule:
                    reasons.append(
                        'room double-booking could not be resolved'
                        + _subj_str('HC9')
                    )
                if 'HC10' in by_rule:
                    reasons.append(
                        'faculty assigned to overlapping classes'
                        + _subj_str('HC10')
                    )
                if 'HC_LAB' in by_rule:
                    reasons.append(
                        'no laboratory room available for'
                        + _subj_str('HC_LAB')
                        + ' — check room types in Rooms, or disable the Lab Room Requirement in Settings'
                    )
                if any(r in by_rule for r in ('HC1', 'HC2', 'HC3')):
                    combined = set()
                    for _r in ('HC1', 'HC2', 'HC3'):
                        combined |= by_rule.get(_r, set())
                    subs = f' ({", ".join(sorted(combined))})' if combined else ''
                    reasons.append(
                        f'faculty teaching-hour restrictions cannot be satisfied{subs}'
                        ' — check employee type settings'
                    )
                if 'HC4' in by_rule:
                    reasons.append(
                        'weekend restriction: non-NSTP subjects cannot be placed on the restricted day'
                        + _subj_str('HC4')
                    )
                if 'HC6' in by_rule:
                    reasons.append(
                        'day-pairing constraints could not be satisfied'
                        + _subj_str('HC6')
                        + ' — check day-pair settings in Settings'
                    )
                if 'HC8' in by_rule:
                    reasons.append(
                        'faculty load limits exceeded'
                        + _subj_str('HC8')
                        + ' — too many subjects for available faculty load capacity'
                    )
                if not reasons:
                    # Catch-all for unrecognised rules — list them with subjects
                    for rule, codes in sorted(by_rule.items()):
                        subs = f' ({", ".join(sorted(codes))})' if codes else ''
                        reasons.append(f'{rule} violation{subs}')

                reason_str = '; '.join(reasons)
                return {
                    "success": False,
                    "error": (
                        f"Unable to generate a conflict-free schedule after {MAX_ATTEMPTS} attempts. "
                        f"Issues detected: {reason_str}. "
                        f"Please verify room availability, faculty qualifications, load limits, "
                        f"and constraint settings, then try again."
                    ),
                }
            # Advisory warnings (HC_SPEC) don't block generation — they're returned so
            # the UI can display informational notices without preventing publishing.

            # ── Post-GA cross-section conflict repair ─────────────────────────────
            # The GA evolves only on intra-individual fitness.  Mutations that change
            # rooms/days don't check against published slots.  Repair any cross-section
            # conflicts in the winner before returning so the generated schedule is
            # always conflict-free w.r.t. already-published schedules.
            if published_room_slots or published_faculty_slots:
                print(f'[REPAIR] Running cross-section conflict repair on final schedule...')
                best_schedule_global = self._repair_cross_conflicts(
                    best_schedule_global,
                    rooms,
                    published_room_slots,
                    published_faculty_slots,
                    faculty_map=faculty_map,
                )

            # ── Final cross-section validation ────────────────────────────────────
            # Scan the repaired schedule against published/draft slots.  If any
            # unresolved conflicts remain, fail the generation with an explanation
            # so the approval screen never sees conflicted data.
            if published_room_slots or published_faculty_slots:
                unresolved = []
                for cls in best_schedule_global:
                    rid  = cls.get('room_id')
                    fid  = cls.get('faculty_id')
                    days = cls.get('days_list') or [cls.get('day', '')]
                    st   = cls.get('start_time')
                    et   = cls.get('end_time')
                    if not (st and et and days):
                        continue
                    for d in days:
                        # (id, day) keyed — direct lookup, no day scan
                        for (ps, pe) in (published_room_slots or {}).get((rid, d), []):
                            if st < pe and et > ps:
                                sc = (cls.get('subject_code') or '?').upper()
                                unresolved.append(
                                    f'{sc}: room conflict on {d} {ps}–{pe}'
                                )
                                break
                        for (ps, pe) in (published_faculty_slots or {}).get((fid, d), []):
                            if st < pe and et > ps:
                                sc = (cls.get('subject_code') or '?').upper()
                                unresolved.append(
                                    f'{sc}: faculty conflict on {d} {ps}–{pe}'
                                )
                                break
                if unresolved:
                    detail = '; '.join(dict.fromkeys(unresolved))   # dedupe, keep order
                    return {
                        "success": False,
                        "error": (
                            f"Could not generate a schedule free of conflicts with "
                            f"existing Published/Draft sessions after {MAX_ATTEMPTS} attempts. "
                            f"Unresolved: {detail}. "
                            f"Try adding more rooms, adjusting faculty load limits, "
                            f"or generating schedules for other sections first."
                        ),
                    }
            # ──────────────────────────────────────────────────────────────────────

            return {
                "success":        True,
                "schedule_data":  best_schedule_global,
                "violations":     advisory_violations,   # HC_SPEC etc. — shown but don't block
                "conflict_count": 0,                     # hard violations only; 0 = publishable
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────
#  STANDALONE VALIDATOR
# ─────────────────────────────────────────────────────────────

def validate_draft(schedule_data: list, faculty_map: dict) -> dict:
    validator  = CSPValidator()
    violations = validator.validate(schedule_data, faculty_map)
    return {
        "valid":       len(violations) == 0,
        "violations":  violations,
        "can_publish": len(violations) == 0,
    }
