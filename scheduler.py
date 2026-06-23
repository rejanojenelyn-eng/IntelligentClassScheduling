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

    # ── Helper: is this HC toggle enabled? ────────────────────
    def _enabled(self, key: str) -> bool:
        return bool(self._cfg.get(key, 1))

    def validate(self, schedule: list, faculty_map: dict) -> list:
        violations = []
        # HC1/HC2/HC3  Faculty time-window & load limits
        if self._enabled('hc_faculty_load_enabled'):
            violations += self._check_time_windows(schedule, faculty_map)
        # HC4  Sunday / NSTP restriction
        if self._enabled('hc_weekend_enabled'):
            violations += self._check_sunday_restriction(schedule)
        # HC6  Day pairing
        if self._enabled('hc_day_pairing_enabled'):
            violations += self._check_day_pairing(schedule)
        # HC7  Night PT cap (designees)
        if self._enabled('hc_faculty_load_enabled'):
            violations += self._check_night_pt_cap(schedule, faculty_map)
        # HC8  Teaching load limits
        if self._enabled('hc_faculty_load_enabled'):
            violations += self._check_load_limits(schedule, faculty_map)
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
            regular_end   = et.get('regular_end')   or time(16, 30)

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
                    if start < time(8, 0) or end > time(17, 0):
                        violations.append({
                            'rule': 'HC2',
                            'subject': subj_code,
                            'detail': (
                                f'Designee/administrator teaching hours are 8:00 AM–5:00 PM on weekdays. '
                                f'The slot on {day} ({format_time_12h(start)}–{format_time_12h(end)}) is outside this window.'
                            )
                        })
                else:
                    if not is_weekend:
                        if night_svc and night_svc > 0:
                            if start < time(16, 30) or end > time(18, 0):
                                violations.append({
                                    'rule': 'HC3',
                                    'subject': subj_code,
                                    'detail': (
                                        f'This designee has approved evening teaching service (4:30–6:00 PM). '
                                        f'The slot on {day} ({format_time_12h(start)}–{format_time_12h(end)}) '
                                        f'falls outside this permitted evening window.'
                                    )
                                })
                        else:
                            violations.append({
                                'rule': 'HC3',
                                'subject': subj_code,
                                'detail': (
                                    f'This designee/administrator does not have approved evening teaching service '
                                    f'and may not be scheduled for classes outside regular hours on {day}. '
                                    f'Please assign a different faculty or update the faculty hours settings.'
                                )
                            })

            elif emp_status == 'Part-Time':
                if not is_weekend:
                    if start < time(16, 30) or end > time(21, 0):
                        violations.append({
                            'rule': 'HC3',
                            'subject': subj_code,
                            'detail': (
                                f'Part-time faculty may only be scheduled between 4:30 PM and 9:00 PM on weekdays '
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
                    if not is_weekend and (start < time(16, 30) or end > time(21, 0)):
                        violations.append({
                            'rule': 'HC3',
                            'subject': subj_code,
                            'detail': (
                                f'Full-time faculty evening classes must be within 4:30 PM–9:00 PM. '
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
        violations = []
        night_count = defaultdict(int)
        for cls in schedule:
            fnum = cls.get('faculty_id')
            if not fnum or fnum not in faculty_map:
                continue
            fac = faculty_map[fnum]
            if fac.get('designationid') is None:
                continue
            if cls['day'] in WEEKDAYS and is_night_time(cls['start_time']):
                night_count[fnum] += 1

        for fnum, count in night_count.items():
            if count > 2:
                violations.append({
                    'rule': 'HC7',
                    'subject': 'multiple',
                    'detail': f'Faculty {fnum} has {count} night PT classes (max 2 for designees)'
                })
        return violations

    # ── HC8 Teaching load limits ────────────────────────────────

    def _check_load_limits(self, schedule, faculty_map):
        violations = []
        regular_units = defaultdict(int)
        pt_units      = defaultdict(int)

        # Track (faculty, subject) pairs already counted to avoid double-counting
        # subjects that span multiple time slices.
        seen_regular = set()
        seen_pt      = set()

        for cls in schedule:
            fnum = cls.get('faculty_id')
            if not fnum or fnum not in faculty_map:
                continue
            fac   = faculty_map[fnum]
            et    = fac.get('employeetype', {})
            units = cls.get('units', 0)
            if units == 0:
                continue  # lab part carries 0 units (counted in lecture part)

            subj = (cls.get('subject_code') or cls.get('subjectcode') or '').upper()

            regular_end = et.get('regular_end') or time(16, 30)
            is_regular  = cls['day'] in WEEKDAYS and cls['end_time'] <= regular_end
            if is_regular:
                key = (fnum, subj)
                if key not in seen_regular:
                    seen_regular.add(key)
                    regular_units[fnum] += units
            else:
                key = (fnum, subj)
                if key not in seen_pt:
                    seen_pt.add(key)
                    pt_units[fnum] += units

        for fnum, units in regular_units.items():
            et      = faculty_map[fnum].get('employeetype', {})
            max_reg = et.get('regularload') or 99
            if units > max_reg:
                ts_hours = et.get('teachingsubstitution', 0) or 0
                excess   = units - max_reg
                if excess > ts_hours:
                    violations.append({
                        'rule': 'HC8',
                        'subject': 'multiple',
                        'detail': f'Faculty {fnum} regular load {units} exceeds limit {max_reg}'
                                  + (f' (TS {ts_hours}h available, short {excess - ts_hours}h)' if ts_hours else '')
                    })

        for fnum, units in pt_units.items():
            et     = faculty_map[fnum].get('employeetype', {})
            max_pt = et.get('parttimeload') or 99
            if units > max_pt:
                ts_hours = et.get('teachingsubstitution', 0) or 0
                excess   = units - max_pt
                if excess > ts_hours:
                    violations.append({
                        'rule': 'HC8',
                        'subject': 'multiple',
                        'detail': f'Faculty {fnum} PT load {units} exceeds limit {max_pt}'
                                  + (f' (TS {ts_hours}h available, short {excess - ts_hours}h)' if ts_hours else '')
                    })

        return violations

    # ── HC9 Room overlap ────────────────────────────────────────

    def _check_room_overlaps(self, schedule):
        violations = []
        for i, a in enumerate(schedule):
            for b in schedule[i+1:]:
                if a.get('room_id') != b.get('room_id'):
                    continue
                for day_a in a.get('days_list', [a.get('day')]):
                    for day_b in b.get('days_list', [b.get('day')]):
                        if day_a != day_b:
                            continue
                        if self._times_overlap(a['start_time'], a['end_time'],
                                               b['start_time'], b['end_time']):
                            violations.append({
                                'rule': 'HC9',
                                'subject': f"{a.get('subject_code')} / {b.get('subject_code')}",
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
                    'rule': 'HC_SPEC',
                    'subject': subj_code or '?',
                    'detail': (
                        f'"{fac_name}" ({spec}) cannot teach "{subj_code}" — '
                        f'this subject requires a {required_spec} specialization.'
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
            if not has_lab_room:
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
                   cs.creditunits, c.programcode AS offeringcode
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
            has_desig    = row['designationid'] is not None
            eff_regular  = row['designation_regular_load'] if (has_desig and row['designation_regular_load']) else row['regularload']
            eff_parttime = (row['nightteachingservice'] or 0) if has_desig else row['parttimeload']
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
                    'teachingsubstitution': row['teachingsubstitution'],
                    'regular_start':        row['regular_start'] or time(7, 30),
                    'regular_end':          row['regular_end']   or time(16, 30),
                    'parttime_start':       row['parttime_start'],
                    'parttime_end':         row['parttime_end'],
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
                JOIN room rm ON UPPER(TRIM(hd."Room")) = UPPER(TRIM(rm.roomname))
                WHERE UPPER(REGEXP_REPLACE(hd."Program", '\\s+\\d+$', '')) = UPPER(%s)
                  AND CAST(hd."Year Level" AS TEXT) = %s
                  AND UPPER(sem.semestertype) = UPPER(%s)
                  AND hd."Subject Code" IS NOT NULL
                  AND TRIM(hd."Subject Code") != ''
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
                prefs[code] = {
                    'faculty':   row.get('faculty'),
                    'room_id':   room_info.get('room_id'),
                    'room_name': room_info.get('room_name'),
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

    def fetch_current_faculty_loads(self, term: str, acad_year_id: str) -> dict:
        """
        #9: Return {faculty_id: total_units_already_committed} for the given
        term across ALL programs/sections that have Published or Draft schedules.
        This allows the builder to exclude faculty who have already hit their load cap.
        """
        if not term or not acad_year_id:
            return {}
        rows = query_db("""
            SELECT sc.employeenumber AS faculty_id,
                   SUM(COALESCE(cs.creditunits, 0)) AS committed_units
            FROM schedule_version sv
            JOIN schedule sc ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN semester sem ON sc.semesterid = sem.semesterid
            WHERE sem.academicyearid = %s
              AND UPPER(sem.semestertype) = UPPER(%s)
              AND sv.status IN ('Published', 'Draft')
              AND sc.employeenumber IS NOT NULL
              AND cs.creditunits > 0
            GROUP BY sc.employeenumber
        """, (acad_year_id, term))
        return {r['faculty_id']: int(r['committed_units'] or 0) for r in (rows or [])}

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

        return allowed if allowed else [(s, e, 'any') for (s, e) in valid_blks]

    # ── Individual builder ───────────────────────────────────────

    def _build_individual(self, subjects, faculty_list, faculty_map, rooms,
                          historical_faculty: dict = None, preferences: dict = None,
                          subject_history: dict = None, existing_load: dict = None):
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
        """
        individual = []
        historical_faculty = historical_faculty or {}
        preferences        = preferences        or {}
        subject_history    = subject_history    or {}
        # #9: pre-loaded committed units from other sections/programs for this term
        _existing_load     = dict(existing_load or {})
        # Track units being assigned within this individual so load compounds correctly
        _sched_units: dict = defaultdict(int)

        # Track slots during building to eliminate hard overlaps in generated individuals
        faculty_slots = defaultdict(list)   # fac_id  → [(day, start, end)]
        room_slots    = defaultdict(list)   # room_id → [(day, start, end)]
        section_slots = []                   # all booked slots in this section → [(day, start, end)]

        def _has_overlap(fac_id, days, start, end, room_id):
            for d in days:
                for (fd, fs, fe) in faculty_slots.get(fac_id, []):
                    if fd == d and start < fe and end > fs:
                        return True
                for (rd, rs, re) in room_slots.get(room_id, []):
                    if rd == d and start < re and end > rs:
                        return True
                # Section-level: no two subjects may share a time slot for the same section
                for (sd, ss, se) in section_slots:
                    if sd == d and start < se and end > ss:
                        return True
            return False

        def _register(fac_id, days, start, end, room_id):
            for d in days:
                faculty_slots[fac_id].append((d, start, end))
                room_slots[room_id].append((d, start, end))
                section_slots.append((d, start, end))

        for sub in subjects:
            lec_hrs      = sub.get('lecturehours', 0)
            lab_hrs      = sub.get('laboratoryhours', 0)
            credit_units = sub.get('creditunits', 3)
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
            def _has_load_capacity(fac, units):
                if not units:
                    return True  # lab parts have 0 units — no limit check needed
                fid = fac['employeenumber']
                et  = fac.get('employeetype', {})
                has_desig = fac.get('designationid') is not None
                max_reg   = et.get('regularload') or 99
                max_pt    = et.get('parttimeload') or 0
                ts_sub    = et.get('teachingsubstitution') or 0
                max_total = max_reg + max_pt + ts_sub
                committed = _existing_load.get(fid, 0) + _sched_units.get(fid, 0)
                return (committed + units) <= max_total

            with_capacity = [f for f in qualified_faculty if _has_load_capacity(f, sub.get('creditunits', 3))]
            if with_capacity:
                qualified_faculty = with_capacity
            # (if no faculty has remaining capacity, keep all qualified faculty as fallback)

            pref       = preferences.get(sub['subjectcode'], {})
            pref_fnum  = pref.get('faculty')
            hist_fnum  = historical_faculty.get(sub['subjectcode'])

            # Discard preferred/historical faculty that fail the specialization check
            if pref_fnum and pref_fnum in faculty_map and not _is_qualified(faculty_map[pref_fnum]):
                pref_fnum = None
            if hist_fnum and hist_fnum in faculty_map and not _is_qualified(faculty_map[hist_fnum]):
                hist_fnum = None

            # Priority 3 source: broad program-wide subject history
            wide_fnum = subject_history.get(sub['subjectcode'].upper())
            if wide_fnum and wide_fnum in faculty_map and not _is_qualified(faculty_map[wide_fnum]):
                wide_fnum = None  # discard if unqualified (e.g. PE teacher for GEED)

            chosen_fac = None
            _cu = sub.get('creditunits', 3)
            # Priority 1: preference map (historical_data > published > draft) — strongest signal
            if (pref_fnum and pref_fnum in faculty_map
                    and _has_load_capacity(faculty_map[pref_fnum], _cu)
                    and random.random() < 0.82):
                chosen_fac = faculty_map[pref_fnum]
            # Priority 2: merged historical/CBR faculty hint
            elif (hist_fnum and hist_fnum in faculty_map
                    and _has_load_capacity(faculty_map[hist_fnum], _cu)
                    and random.random() < 0.68):
                chosen_fac = faculty_map[hist_fnum]
            # Priority 3: program-wide subject history (who has taught this subject before)
            elif (wide_fnum and wide_fnum in faculty_map
                    and _has_load_capacity(faculty_map[wide_fnum], _cu)
                    and random.random() < 0.60):
                chosen_fac = faculty_map[wide_fnum]
            if chosen_fac is None:
                chosen_fac = random.choice(qualified_faculty)
            # #9: Track units committed within this schedule build
            if _cu > 0:
                _sched_units[chosen_fac['employeenumber']] += _cu

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
                regular_blks = [(s, e) for (s, e, k) in allowed_blks if k == 'regular']
                pt_blks      = [(s, e) for (s, e, k) in allowed_blks if k != 'regular']

                # Compute available weekday pool once (respects all_weekends HC4 scope)
                _avail_days = (
                    WEEKDAYS
                    if (self._nstp_force_sunday and self._weekend_day_scope == 'all_weekends')
                    else WEEKDAYS + ['Saturday']
                )

                # Try up to 20 times to find a non-overlapping slot
                start_t = end_t = chosen_room = days_list = None
                for _ in range(20):
                    if regular_blks and random.random() < 0.75:
                        _s, _e = random.choice(regular_blks)
                    elif pt_blks:
                        _s, _e = random.choice(pt_blks)
                    else:
                        _s, _e = random.choice(valid_blks)

                    _dur = duration_hours(_s, _e)
                    if pref_room and random.random() < 0.65:
                        _r = pref_room
                    else:
                        _r = random.choice(valid_rooms)

                    # Day selection (uses DB-configurable pairs and NSTP toggle)
                    if is_nstp_ou and self._nstp_force_sunday:
                        # HC4: NSTP/OU subjects forced to Sunday when restriction is on
                        _days = ['Sunday']
                    elif abs(_dur - 1.5) < 0.1 and lec_hrs >= 3 and not is_lab_part:
                        # HC6: pair only for 1.5-hr sessions of 3+ hr/week subjects
                        _pair = random.choice(self._builder_pairs)
                        _days = list(_pair)
                    else:
                        # Single-day; labs always single-day
                        _days = [random.choice(_avail_days)]

                    if not _has_overlap(chosen_fac['employeenumber'], _days, _s, _e, _r['roomid']):
                        start_t, end_t, chosen_room, days_list = _s, _e, _r, _days
                        break

                # Fallback if all 8 attempts overlapped (overlap handled by GA validator)
                if start_t is None:
                    if regular_blks:
                        start_t, end_t = random.choice(regular_blks)
                    elif pt_blks:
                        start_t, end_t = random.choice(pt_blks)
                    else:
                        start_t, end_t = random.choice(valid_blks)
                    _dur = duration_hours(start_t, end_t)
                    if is_nstp_ou and self._nstp_force_sunday:
                        days_list = ['Sunday']
                    elif abs(_dur - 1.5) < 0.1 and lec_hrs >= 3 and not is_lab_part:
                        _pair = random.choice(self._builder_pairs)
                        days_list = list(_pair)
                    else:
                        days_list = [random.choice(_avail_days)]
                    chosen_room = pref_room or random.choice(valid_rooms)

                _register(chosen_fac['employeenumber'], days_list, start_t, end_t,
                          chosen_room['roomid'])

                hrs_str = str(lec_hrs + lab_hrs)

                individual.append({
                    'subject_code':      sub['subjectcode'],
                    'description':       sub['subjectname'],
                    'lec_hours':         lec_hrs if class_type == 'Lecture' else 0,
                    'lab_hours':         lab_hrs if class_type == 'Lab'     else 0,
                    'units':             units,
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

        self._repair_overlaps(individual, faculty_map)
        return individual

    # ── Overlap repair ───────────────────────────────────────────

    def _repair_overlaps(self, individual: list, faculty_map: dict) -> list:
        """
        Multi-pass repair: detect and fix section, faculty, and room time conflicts
        by reassigning conflicting entries to a valid non-overlapping time+day.
        Runs up to 12 passes or until no overlap remains.
        """
        for _pass in range(12):
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
                """Return True only if the proposed time+days conflict with nothing else."""
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

    # ── Fitness scoring ──────────────────────────────────────────

    def _fitness(self, individual, faculty_map):
        score = 1000

        # SC8: Reward assignments that match historical / published / manual preferences
        for cls in individual:
            if cls.get('is_preferred_faculty'):
                score += 12
            if cls.get('is_preferred_room'):
                score += 8

        violations   = self.csp.validate(individual, faculty_map)
        score       -= len(violations) * 200

        faculty_schedule = defaultdict(list)
        for cls in individual:
            faculty_schedule[cls['faculty_id']].append(cls)

        for fnum, classes in faculty_schedule.items():
            fac = faculty_map.get(fnum, {})
            et  = fac.get('employeetype', {})

            day_counts     = defaultdict(int)
            total_pt_units = 0
            sat_count = 0
            sun_count = 0

            sorted_cls = sorted(classes, key=lambda c: (c['day'], minutes(c['start_time'])))

            for i, cls in enumerate(sorted_cls):
                if is_night_time(cls['start_time']):
                    score -= 15
                if not is_daytime_block(cls['start_time'], cls['end_time']):
                    score -= 20

                day_counts[cls['day']] += cls.get('units', 0)

                reg_end = et.get('regular_end') or time(16, 30)
                if cls['day'] in WEEKDAYS and cls['end_time'] > reg_end:
                    total_pt_units += cls.get('units', 0)

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
                score -= abs(total_pt_units - max_pt) * 10

            if abs(sat_count - sun_count) > 1:
                score -= 10

        return score, len(violations)

    # ── Mutation ─────────────────────────────────────────────────

    def _mutate(self, child, subjects_by_code, faculty_list, faculty_map, rooms,
                historical_faculty: dict = None, preferences: dict = None,
                subject_history: dict = None):
        historical_faculty = historical_faculty or {}
        preferences        = preferences        or {}
        subject_history    = subject_history    or {}
        if not child:
            return child

        idx        = random.randint(0, len(child) - 1)
        gene       = child[idx]
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

            # Priority 3 source: broad program-wide subject history
            wide_fnum = subject_history.get(sub_code.upper())
            if wide_fnum and wide_fnum in faculty_map and not _mut_qualified(faculty_map[wide_fnum]):
                wide_fnum = None

            chosen_fac = None
            # Priority 1: preference map (historical_data > published > draft)
            if pref_fnum and pref_fnum in faculty_map and random.random() < 0.80:
                chosen_fac = faculty_map[pref_fnum]
            # Priority 2: merged historical/CBR faculty hint
            elif hist_fnum and hist_fnum in faculty_map and random.random() < 0.68:
                chosen_fac = faculty_map[hist_fnum]
            # Priority 3: program-wide subject history
            elif wide_fnum and wide_fnum in faculty_map and random.random() < 0.60:
                chosen_fac = faculty_map[wide_fnum]
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

            # Broad program-wide subject history: who has EVER taught each subject for
            # this program (any year level, any term).  Used as Priority 3 fallback so
            # subjects like GEED that are shared across year levels still get assigned to
            # the faculty member who historically teaches them, not a random qualified one.
            all_subject_codes = [s['subjectcode'] for s in subjects]
            subject_history   = self.fetch_subject_wide_faculty(program, all_subject_codes)

            # #9: Pre-load current semester faculty loads from existing scheduled sections.
            # This prevents assigning faculty who are already at their load limit.
            existing_load = self.fetch_current_faculty_loads(term, current_ay)

            POP_SIZE      = 60
            GENERATIONS   = 200
            MUTATION_RATE = 0.30
            ELITE_RATIO   = 0.35
            MAX_ATTEMPTS  = 4   # initial run + up to 3 restarts

            best_schedule_global    = None
            least_violations_global = 9999
            best_score_global       = -999999

            for attempt in range(MAX_ATTEMPTS):
                # First attempt: seed heavily with historical preferences for accuracy.
                # Restarts: reduce seeding ratio to escape local optima via diversity.
                seed_cutoff = POP_SIZE // 2 if attempt == 0 else POP_SIZE // 4

                population = []
                for i in range(POP_SIZE):
                    hist       = merged_faculty if i < seed_cutoff else {}
                    prefs_seed = preferences    if i < seed_cutoff else {}
                    population.append(
                        self._build_individual(
                            subjects, faculty_list, faculty_map, rooms,
                            hist, prefs_seed, subject_history,
                            existing_load=existing_load  # #9
                        )
                    )

                best_schedule    = None
                least_violations = 9999
                best_score       = -999999

                for gen in range(GENERATIONS):
                    scored = []
                    for ind in population:
                        score, n_violations = self._fitness(ind, faculty_map)
                        scored.append((score, n_violations, ind))

                        if n_violations == 0 and score > best_score:
                            best_score       = score
                            best_schedule    = copy.deepcopy(ind)
                            least_violations = 0

                        if n_violations < least_violations:
                            least_violations = n_violations
                            best_schedule    = copy.deepcopy(ind)
                            best_score       = score

                    # Stop early once conflict-free and sufficiently evolved
                    if least_violations == 0 and gen >= 10:
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
                        child = copy.deepcopy(p1[:split]) + copy.deepcopy(p2[split:])

                        if random.random() < MUTATION_RATE:
                            child = self._mutate(
                                child, subjects_by_code, faculty_list, faculty_map, rooms,
                                historical_faculty, preferences, subject_history
                            )
                        self._repair_overlaps(child, faculty_map)
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
            final_violations = self.csp.validate(best_schedule_global, faculty_map)

            if final_violations:
                # Could not produce a conflict-free schedule — explain why and return an error
                # so the UI never displays a conflicted schedule to the user.
                violation_rules = {v['rule'] for v in final_violations}
                reasons = []
                if 'HC11' in violation_rules:
                    reasons.append(
                        'not enough distinct time slots for all subjects in this section'
                    )
                if 'HC9' in violation_rules:
                    reasons.append('room double-booking could not be resolved')
                if 'HC10' in violation_rules:
                    reasons.append('faculty assigned to overlapping classes')
                if 'HC_SPEC' in violation_rules:
                    reasons.append(
                        'insufficient qualified faculty for one or more subjects '
                        '(check specialization assignments in Employee Management)'
                    )
                if 'HC_LAB' in violation_rules:
                    reasons.append(
                        'no laboratory rooms available for subjects with lab hours '
                        '(check room types in Rooms, or disable the Lab Room Requirement in Settings)'
                    )
                if any(r in violation_rules for r in ('HC1', 'HC2', 'HC3')):
                    reasons.append(
                        'faculty teaching-hour restrictions cannot be satisfied '
                        '(check employee type settings)'
                    )
                if 'HC4' in violation_rules:
                    reasons.append(
                        'weekend restriction: non-NSTP subjects cannot be placed on the restricted day'
                    )
                if 'HC6' in violation_rules:
                    reasons.append(
                        'day-pairing constraints could not be satisfied '
                        '(check day-pair settings in Settings)'
                    )
                if 'HC8' in violation_rules:
                    reasons.append(
                        'faculty load limits exceeded '
                        '(too many subjects for available faculty load capacity)'
                    )
                reason_str = '; '.join(reasons) if reasons else (
                    'scheduling constraints could not all be satisfied simultaneously'
                )
                return {
                    "success": False,
                    "error": (
                        f"Unable to generate a conflict-free schedule after {MAX_ATTEMPTS} attempts. "
                        f"Issues detected: {reason_str}. "
                        f"Please verify room availability, faculty qualifications, load limits, "
                        f"and constraint settings, then try again."
                    ),
                }

            return {
                "success":        True,
                "schedule_data":  best_schedule_global,
                "violations":     [],
                "conflict_count": 0,
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
