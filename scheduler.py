# scheduler.py

"""
scheduler.py  —  Intelligent Scheduling Engine
================================================
Architecture
------------
Layer 1  · StandardSlots   – builds PUP-standard time blocks from the DB timeslot table
Layer 2  · CSPValidator    – hard constraint checker (returns violations, never silently ignores)
Layer 3  · IntelligentScheduler – Genetic Algorithm with soft-constraint fitness scoring

Hard constraints (CSP) — a draft with ANY of these is flagged as invalid:
  HC1  Full-time faculty regular hours  : Mon–Fri 7:30–16:30
  HC2  Designee regular hours           : Mon–Fri 8:00–17:00
  HC3  Part-time windows                : 16:30–21:00 wkday / any time wkend (full-time)
       Designee-with-night              : 16:30–18:00 wkday / any time wkend
       Designee-no-night                : weekends only
  HC4  Sunday restriction               : only OU / NSTP subjects
  HC5  Standard time slots              : must align to PUP blocks
  HC6  Day pairing for 1.5-hr classes   : MTH | TF | WS only
  HC7  Max 2 night PT classes/designee
  HC8  Teaching load limits             : regular + PT caps from employeetype
  HC9  No room overlap
  HC10 No faculty double-booking

Soft constraints (GA fitness) — violations reduce the score but do not block:
  SC1  Preferred time of day           (−20 per violation)
  SC2  Minimize night classes          (−15 per night class)
  SC3  Even day distribution           (−10 per overloaded day)
  SC4  Compact schedule / no idle gaps (−10 per large gap)
  SC5  Balance PT load                 (−10 per unit deviation)
  SC6  Even weekend spread             (−10 per imbalance)
  SC7  No 4+ consecutive teaching hrs  (−30 per occurrence)
"""

import random
import copy
from datetime import time, datetime, timedelta
from collections import defaultdict
from database import get_db_connection, query_db


# ─────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────

WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
WEEKEND  = ['Saturday', 'Sunday']
ALL_DAYS = WEEKDAYS + WEEKEND

# Valid paired-day patterns for 1.5-hour (3-slot) classes
DAY_PAIRS = {
    'MTH': ['Monday', 'Thursday'],
    'TF':  ['Tuesday', 'Friday'],
    'WS':  ['Wednesday', 'Saturday'],
}

# OU/NSTP subject code prefixes allowed on Sunday
SUNDAY_ALLOWED_PREFIXES = ('NSTP', 'OU')

# PUP standard time blocks (start_time, end_time) as (hour, minute) tuples
# Covers 7:30 AM through 9:00 PM in the canonical PUP blocks.
# Laboratory blocks allow extended slots (e.g. 10:30–13:30 = 3 hrs).
STANDARD_BLOCKS = [
    (time(7,  30), time(9,  0)),    # 1.5 hr
    (time(9,  0),  time(10, 30)),   # 1.5 hr
    (time(10, 30), time(12, 0)),    # 1.5 hr
    (time(10, 30), time(13, 30)),   # 3 hr lab block
    (time(12, 0),  time(13, 30)),   # 1.5 hr
    (time(13, 30), time(15, 0)),    # 1.5 hr
    (time(13, 30), time(16, 30)),   # 3 hr lab block
    (time(15, 0),  time(16, 30)),   # 1.5 hr
    (time(16, 30), time(18, 0)),    # 1.5 hr (evening start)
    (time(18, 0),  time(19, 30)),   # 1.5 hr
    (time(19, 30), time(21, 0)),    # 1.5 hr
    (time(7,  30), time(10, 30)),   # 3 hr lecture block
    (time(9,  0),  time(12, 0)),    # 3 hr lecture block
]


def t(h, m=0):
    """Helper: create a time object."""
    return time(h, m)


def minutes(t_obj):
    """Convert time → total minutes since midnight."""
    return t_obj.hour * 60 + t_obj.minute


def duration_hours(start: time, end: time) -> float:
    """Return duration in hours between two time objects."""
    return (minutes(end) - minutes(start)) / 60.0


# ─────────────────────────────────────────────────────────────
#  LAYER 1 — STANDARD SLOT HELPERS
# ─────────────────────────────────────────────────────────────

def get_valid_blocks_for_subject(subject: dict, is_lab: bool = False) -> list:
    """
    Return the subset of STANDARD_BLOCKS that match the subject's required duration.
    Lab subjects (laboratoryhours > 0) may use extended 3-hr blocks.
    """
    total_hours = subject['lecturehours'] + subject['laboratoryhours']
    if total_hours == 0:
        total_hours = 3  # fallback

    valid = []
    for (start, end) in STANDARD_BLOCKS:
        block_dur = duration_hours(start, end)
        # Allow ±0.1 tolerance for floating point
        if abs(block_dur - total_hours) < 0.1:
            valid.append((start, end))

    # If nothing matched exactly, fall back to all blocks with similar duration
    if not valid:
        target = total_hours
        valid = [(s, e) for (s, e) in STANDARD_BLOCKS
                 if abs(duration_hours(s, e) - target) < 0.6]
    return valid if valid else STANDARD_BLOCKS


def format_time_12h(t_obj: time) -> str:
    """Format time as '07:30 AM'."""
    return t_obj.strftime('%I:%M %p')


def is_night_time(t_obj: time) -> bool:
    """True if time is 18:00 or later (6 PM+)."""
    return t_obj >= time(18, 0)


def is_evening_start(t_obj: time) -> bool:
    """True if time is 16:30 or later (evening/PT window)."""
    return t_obj >= time(16, 30)


# ─────────────────────────────────────────────────────────────
#  LAYER 2 — HARD CONSTRAINT (CSP) VALIDATOR
# ─────────────────────────────────────────────────────────────

class CSPValidator:
    """
    Validates a full schedule draft against all hard constraints.
    Returns a list of violation dicts — empty list = valid draft.
    """

    def validate(self, schedule: list, faculty_map: dict) -> list:
        """
        Args:
            schedule   : list of class assignment dicts (one per subject)
            faculty_map: dict keyed by employeenumber with faculty + employeetype data

        Returns:
            List of {"rule": str, "subject": str, "detail": str} dicts.
            Empty = no violations.
        """
        violations = []

        violations += self._check_time_windows(schedule, faculty_map)
        violations += self._check_sunday_restriction(schedule)
        violations += self._check_standard_slots(schedule)
        violations += self._check_day_pairing(schedule)
        violations += self._check_night_pt_cap(schedule, faculty_map)
        violations += self._check_load_limits(schedule, faculty_map)
        violations += self._check_room_overlaps(schedule)
        violations += self._check_faculty_overlaps(schedule)

        return violations

    # ── HC1 / HC2 / HC3  Teaching time windows ──────────────────

    def _check_time_windows(self, schedule, faculty_map):
        violations = []
        for cls in schedule:
            fnum = cls.get('faculty_id')
            if not fnum or fnum not in faculty_map:
                continue

            fac  = faculty_map[fnum]
            emp_status  = fac.get('employeestatus', '')   # Permanent / Temporary / Part-Time
            designation = fac.get('designationid')        # None if no designation
            et           = fac.get('employeetype', {})
            night_svc    = fac.get('nightteachingservice') # from designation join
            # night_svc > 0  → designee WITH night teaching privilege

            start: time = cls['start_time']
            end:   time = cls['end_time']
            day:   str  = cls['day']
            is_weekend  = day in WEEKEND

            # Determine if this is a "regular" or "part-time" slot
            regular_end   = et.get('regular_end', time(16, 30))  # default 4:30 PM
            regular_start = et.get('regular_start', time(7, 30))

            is_regular_slot = (
                day in WEEKDAYS and
                start >= regular_start and
                end   <= regular_end
            )
            is_pt_slot = not is_regular_slot

            subj_code = cls.get('subject_code', '?')

            if emp_status in ('Permanent', 'Temporary'):
                # Full-time faculty
                if is_regular_slot:
                    # HC1: regular must be within 7:30–16:30 Mon–Fri
                    if start < time(7, 30) or end > time(16, 30):
                        violations.append({
                            'rule': 'HC1',
                            'subject': subj_code,
                            'detail': f'Full-time regular class outside 7:30–16:30 on {day} ({format_time_12h(start)}–{format_time_12h(end)})'
                        })
                else:
                    # HC3-A: PT window 16:30–21:00 weekday OR any time weekend
                    if not is_weekend and (start < time(16, 30) or end > time(21, 0)):
                        violations.append({
                            'rule': 'HC3',
                            'subject': subj_code,
                            'detail': f'Full-time PT class outside allowed window on {day} ({format_time_12h(start)}–{format_time_12h(end)})'
                        })

            elif designation is not None:
                # Designee faculty
                if is_regular_slot:
                    # HC2: regular must be within 8:00–17:00 Mon–Fri
                    if start < time(8, 0) or end > time(17, 0):
                        violations.append({
                            'rule': 'HC2',
                            'subject': subj_code,
                            'detail': f'Designee regular class outside 8:00–17:00 on {day} ({format_time_12h(start)}–{format_time_12h(end)})'
                        })
                else:
                    # HC3-B/C: PT window depends on night_svc
                    if is_weekend:
                        pass  # Always allowed on weekends for designees
                    elif night_svc and night_svc > 0:
                        # HC3-B: night PT window 16:30–18:00 weekday
                        if start < time(16, 30) or end > time(18, 0):
                            violations.append({
                                'rule': 'HC3',
                                'subject': subj_code,
                                'detail': f'Designee (with night svc) PT outside 16:30–18:00 on {day} ({format_time_12h(start)}–{format_time_12h(end)})'
                            })
                    else:
                        # HC3-C: no weekday PT allowed for this designee
                        violations.append({
                            'rule': 'HC3',
                            'subject': subj_code,
                            'detail': f'Designee (no night svc) cannot have PT class on weekday {day}'
                        })

        return violations

    # ── HC4  Sunday restriction ──────────────────────────────────

    def _check_sunday_restriction(self, schedule):
        violations = []
        for cls in schedule:
            days = cls.get('days_list', [cls.get('day', '')])
            if 'Sunday' in days:
                code = cls.get('subject_code', '')
                if not any(code.upper().startswith(p) for p in SUNDAY_ALLOWED_PREFIXES):
                    violations.append({
                        'rule': 'HC4',
                        'subject': code,
                        'detail': f'Non-OU/NSTP subject scheduled on Sunday'
                    })
        return violations

    # ── HC5  Standard slots ──────────────────────────────────────

    def _check_standard_slots(self, schedule):
        violations = []
        valid_starts = {s for (s, _) in STANDARD_BLOCKS}
        valid_ends   = {e for (_, e) in STANDARD_BLOCKS}
        for cls in schedule:
            start = cls.get('start_time')
            end   = cls.get('end_time')
            if start and start not in valid_starts:
                violations.append({
                    'rule': 'HC5',
                    'subject': cls.get('subject_code', '?'),
                    'detail': f'Non-standard start time {format_time_12h(start)}'
                })
            if end and end not in valid_ends:
                violations.append({
                    'rule': 'HC5',
                    'subject': cls.get('subject_code', '?'),
                    'detail': f'Non-standard end time {format_time_12h(end)}'
                })
        return violations

    # ── HC6  Day pairing for 1.5-hr classes ─────────────────────

    def _check_day_pairing(self, schedule):
        violations = []
        for cls in schedule:
            dur = duration_hours(cls['start_time'], cls['end_time'])
            if abs(dur - 1.5) < 0.1:
                days_list = cls.get('days_list', [cls.get('day')])
                if len(days_list) < 2:
                    violations.append({
                        'rule': 'HC6',
                        'subject': cls.get('subject_code', '?'),
                        'detail': '1.5-hr class must meet on a paired day pattern (MTH/TF/WS)'
                    })
                else:
                    # Check it matches one of the valid pairs
                    sorted_days = sorted(days_list)
                    valid = any(
                        sorted(pair) == sorted_days
                        for pair in DAY_PAIRS.values()
                    )
                    if not valid:
                        violations.append({
                            'rule': 'HC6',
                            'subject': cls.get('subject_code', '?'),
                            'detail': f'Invalid day pairing: {days_list}. Must be MTH, TF, or WS.'
                        })
        return violations

    # ── HC7  Max 2 night PT classes per designee ────────────────

    def _check_night_pt_cap(self, schedule, faculty_map):
        violations = []
        night_count = defaultdict(int)
        for cls in schedule:
            fnum = cls.get('faculty_id')
            if not fnum or fnum not in faculty_map:
                continue
            fac = faculty_map[fnum]
            if fac.get('designationid') is None:
                continue  # only applies to designees
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

    # ── HC8  Teaching load limits ────────────────────────────────

    def _check_load_limits(self, schedule, faculty_map):
        violations = []
        regular_units = defaultdict(int)
        pt_units      = defaultdict(int)

        for cls in schedule:
            fnum = cls.get('faculty_id')
            if not fnum or fnum not in faculty_map:
                continue
            fac = faculty_map[fnum]
            et  = fac.get('employeetype', {})
            units = cls.get('units', 3)

            regular_end = et.get('regular_end', time(16, 30))
            is_regular  = cls['day'] in WEEKDAYS and cls['end_time'] <= regular_end
            if is_regular:
                regular_units[fnum] += units
            else:
                pt_units[fnum] += units

        for fnum, units in regular_units.items():
            fac      = faculty_map[fnum]
            et       = fac.get('employeetype', {})
            max_reg  = et.get('regularload') or 99
            if units > max_reg:
                violations.append({
                    'rule': 'HC8',
                    'subject': 'multiple',
                    'detail': f'Faculty {fnum} regular load {units} exceeds limit {max_reg}'
                })

        for fnum, units in pt_units.items():
            fac     = faculty_map[fnum]
            et      = fac.get('employeetype', {})
            max_pt  = et.get('parttimeload') or 99
            if units > max_pt:
                violations.append({
                    'rule': 'HC8',
                    'subject': 'multiple',
                    'detail': f'Faculty {fnum} PT load {units} exceeds limit {max_pt}'
                })

        return violations

    # ── HC9  Room overlap ────────────────────────────────────────

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
                                'detail': f"Room {a.get('room')} double-booked on {day_a} "
                                          f"{format_time_12h(a['start_time'])}–{format_time_12h(a['end_time'])}"
                            })
        return violations

    # ── HC10  Faculty overlap ────────────────────────────────────

    def _check_faculty_overlaps(self, schedule):
        violations = []
        for i, a in enumerate(schedule):
            for b in schedule[i+1:]:
                if a.get('faculty_id') != b.get('faculty_id'):
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
                                'detail': f"Faculty {a.get('instructor')} double-booked on {day_a} "
                                          f"{format_time_12h(a['start_time'])}–{format_time_12h(a['end_time'])}"
                            })
        return violations

    @staticmethod
    def _times_overlap(s1, e1, s2, e2):
        return s1 < e2 and e1 > s2


# ─────────────────────────────────────────────────────────────
#  LAYER 3 — GA ENGINE WITH SOFT CONSTRAINT FITNESS
# ─────────────────────────────────────────────────────────────

class IntelligentScheduler:

    def __init__(self):
        self.csp = CSPValidator()

    # ── Database helpers ─────────────────────────────────────────

    def fetch_data(self, program, year_level, term, curriculum_year):
        """Fetch subjects, faculty (with type + designation), rooms."""

        subjects_query = """
            SELECT s.subjectcode, s.subjectname, s.lecturehours, s.laboratoryhours,
                   s.creditunits, c.programcode
            FROM curriculumsubject cs
            JOIN subject      s ON cs.subjectcode  = s.subjectcode
            JOIN curriculum   c ON cs.curriculumid = c.curriculumid
            WHERE c.programcode    = %s
              AND c.curriculumyear = %s
              AND cs.yearlevel     = %s
              AND cs.semester      = %s
        """
        subjects = query_db(subjects_query, (program, curriculum_year, year_level, term))

        # Fetch faculty joined with employeetype AND designation
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
                   d.nightteachingservice
            FROM faculty f
            JOIN employeetype et ON f.employeetypeid = et.employeetypeid
            LEFT JOIN designation d ON f.designationid = d.designationid
            WHERE f.employeestatus != 'Archive'
        """
        faculty_rows = query_db(faculty_query)

        # Build faculty_map keyed by employeenumber
        faculty_map = {}
        faculty_list = []
        for row in (faculty_rows or []):
            fnum = row['employeenumber']
            fac = {
                'employeenumber': fnum,
                'fullname':       row['fullname'],
                'employeestatus': row['employeestatus'],
                'designationid':  row['designationid'],
                'nightteachingservice': row['nightteachingservice'],
                'employeetype': {
                    'regularload':          row['regularload'],
                    'parttimeload':         row['parttimeload'],
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

    # ── Individual (one schedule candidate) builder ──────────────

    def _build_individual(self, subjects, faculty_list, faculty_map, rooms):
        """
        Randomly assign a time block, day(s), faculty, and room to each subject.
        Respects hard constraints at construction time when easily possible
        (standard blocks, day pairing, Sunday restriction).
        """
        individual = []

        for sub in subjects:
            is_lab     = sub['laboratoryhours'] > 0
            total_hrs  = sub['lecturehours'] + sub['laboratoryhours'] or 3
            valid_blks = get_valid_blocks_for_subject(sub, is_lab)

            start_t, end_t = random.choice(valid_blks)
            dur = duration_hours(start_t, end_t)

            # Pick day(s)
            is_nstp_ou = any(sub['subjectcode'].upper().startswith(p)
                             for p in SUNDAY_ALLOWED_PREFIXES)

            if abs(dur - 1.5) < 0.1:
                # Must use a valid pair
                pair_key   = random.choice(list(DAY_PAIRS.keys()))
                days_list  = DAY_PAIRS[pair_key]
            else:
                # Single day class — avoid Sunday unless OU/NSTP
                pool = ALL_DAYS if is_nstp_ou else WEEKDAYS + ['Saturday']
                days_list = [random.choice(pool)]

            # Pick faculty
            f = random.choice(faculty_list)

            # Pick room matching type
            req_type    = 'Laboratory' if is_lab else 'Lecture'
            valid_rooms = [r for r in rooms if r['roomtype'] == req_type] or rooms
            r = random.choice(valid_rooms)

            individual.append({
                'subject_code': sub['subjectcode'],
                'description':  sub['subjectname'],
                'lec_hours':    sub['lecturehours'],
                'lab_hours':    sub['laboratoryhours'],
                'units':        sub['creditunits'],
                'course':       sub['programcode'],

                'faculty_id':   f['employeenumber'],
                'instructor':   f['fullname'],
                'room_id':      r['roomid'],
                'room':         r['roomname'],

                'start_time':   start_t,
                'end_time':     end_t,
                'days_list':    days_list,
                'day':          days_list[0],  # primary day (for compatibility)

                # Display fields
                'time':  f"{format_time_12h(start_t)} – {format_time_12h(end_t)}",
                'days':  '/'.join(d[:3].upper() for d in days_list),
                'hours': str(total_hrs),
            })

        return individual

    # ── Fitness scoring (soft constraints) ───────────────────────

    def _fitness(self, individual, faculty_map):
        """
        Returns (score, hard_violation_count).
        Hard violations each cost −200 (mirroring original logic but now rule-aware).
        Soft penalties are smaller deductions.
        """
        score = 1000

        # ── Hard constraint penalties ────────────────────────────
        violations = self.csp.validate(individual, faculty_map)
        hard_penalty = len(violations) * 200
        score -= hard_penalty

        # ── Soft constraint penalties ────────────────────────────
        faculty_schedule = defaultdict(list)   # fnum → list of cls
        for cls in individual:
            faculty_schedule[cls['faculty_id']].append(cls)

        for fnum, classes in faculty_schedule.items():
            fac = faculty_map.get(fnum, {})
            et  = fac.get('employeetype', {})

            day_counts   = defaultdict(int)
            night_count  = 0
            total_pt_units = 0
            sat_count = 0
            sun_count = 0

            sorted_by_time = sorted(classes,
                                    key=lambda c: (c['day'], minutes(c['start_time'])))

            for i, cls in enumerate(sorted_by_time):
                # SC2 – minimize night classes
                if is_night_time(cls['start_time']):
                    score -= 15
                    night_count += 1

                # SC3 – even day distribution (count primary day)
                day_counts[cls['day']] += cls['units']

                # SC5 – PT load balance
                reg_end = et.get('regular_end', time(16, 30))
                if cls['day'] in WEEKDAYS and cls['end_time'] > reg_end:
                    total_pt_units += cls['units']

                # SC6 – weekend spread
                if cls['day'] == 'Saturday':
                    sat_count += 1
                if cls['day'] == 'Sunday':
                    sun_count += 1

                # SC4 – compact schedule (gaps between consecutive classes same day)
                if i > 0 and sorted_by_time[i-1]['day'] == cls['day']:
                    gap_mins = minutes(cls['start_time']) - minutes(sorted_by_time[i-1]['end_time'])
                    if gap_mins > 90:   # more than 1.5 hr idle gap
                        score -= 10

                # SC7 – no 4+ consecutive teaching hours
                if i > 0 and sorted_by_time[i-1]['day'] == cls['day']:
                    consecutive = minutes(cls['end_time']) - minutes(sorted_by_time[i-1]['start_time'])
                    if consecutive >= 240:  # 4 hours
                        score -= 30

            # SC3 – penalise if any day has > 2x the average load
            if day_counts:
                avg = sum(day_counts.values()) / len(day_counts)
                for cnt in day_counts.values():
                    if cnt > avg * 2:
                        score -= 10

            # SC5 – PT load balance
            max_pt = et.get('parttimeload') or 0
            if max_pt:
                deviation = abs(total_pt_units - max_pt)
                score -= deviation * 10

            # SC6 – weekend spread
            if abs(sat_count - sun_count) > 1:
                score -= 10

        return score, len(violations)

    # ── Mutation operator ────────────────────────────────────────

    def _mutate(self, child, subjects_by_code, faculty_list, faculty_map, rooms):
        """Randomly alter one gene (class assignment) in the child."""
        idx = random.randint(0, len(child) - 1)
        sub_code = child[idx]['subject_code']

        # Find original subject dict
        sub = subjects_by_code.get(sub_code)
        if not sub:
            return child

        is_lab     = sub['laboratoryhours'] > 0
        valid_blks = get_valid_blocks_for_subject(sub, is_lab)
        start_t, end_t = random.choice(valid_blks)
        dur = duration_hours(start_t, end_t)

        is_nstp_ou = any(sub_code.upper().startswith(p) for p in SUNDAY_ALLOWED_PREFIXES)

        if abs(dur - 1.5) < 0.1:
            pair_key  = random.choice(list(DAY_PAIRS.keys()))
            days_list = DAY_PAIRS[pair_key]
        else:
            pool      = ALL_DAYS if is_nstp_ou else WEEKDAYS + ['Saturday']
            days_list = [random.choice(pool)]

        mutation_type = random.choice(['time', 'day', 'faculty', 'room'])

        if mutation_type == 'time':
            child[idx]['start_time'] = start_t
            child[idx]['end_time']   = end_t
            child[idx]['time']       = f"{format_time_12h(start_t)} – {format_time_12h(end_t)}"
        elif mutation_type == 'day':
            child[idx]['days_list'] = days_list
            child[idx]['day']       = days_list[0]
            child[idx]['days']      = '/'.join(d[:3].upper() for d in days_list)
        elif mutation_type == 'faculty':
            f = random.choice(faculty_list)
            child[idx]['faculty_id'] = f['employeenumber']
            child[idx]['instructor'] = f['fullname']
        elif mutation_type == 'room':
            req_type    = 'Laboratory' if is_lab else 'Lecture'
            valid_rooms = [r for r in rooms if r['roomtype'] == req_type] or rooms
            r = random.choice(valid_rooms)
            child[idx]['room_id'] = r['roomid']
            child[idx]['room']    = r['roomname']

        return child

    # ── Main entry point ─────────────────────────────────────────

    def generate_draft(self, program, year_level, term, curriculum,
                       use_historical=False):
        """
        Run the GA to produce the best schedule draft found.

        Returns:
            {
                "success": bool,
                "schedule_data": [...],          # list of class assignment dicts
                "violations": [...],             # hard violations remaining (empty = clean)
                "conflict_count": int,
                "error": str  (only if success=False)
            }
        """
        try:
            subjects, faculty_list, faculty_map, rooms = self.fetch_data(
                program, year_level, term, curriculum
            )

            if not subjects:
                return {"success": False, "error": "No subjects found for this curriculum/year/term."}
            if not faculty_list:
                return {"success": False, "error": "No active faculty in the database."}
            if not rooms:
                return {"success": False, "error": "No rooms defined in the database."}

            subjects_by_code = {s['subjectcode']: s for s in subjects}

            # GA parameters
            POP_SIZE   = 30
            GENERATIONS = 80
            MUTATION_RATE = 0.15
            ELITE_RATIO   = 0.4

            # Initialise population
            population = [
                self._build_individual(subjects, faculty_list, faculty_map, rooms)
                for _ in range(POP_SIZE)
            ]

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

                # Early exit if conflict-free
                if least_violations == 0:
                    break

                # Sort by score descending, keep elites
                scored.sort(key=lambda x: x[0], reverse=True)
                elite_n   = max(2, int(POP_SIZE * ELITE_RATIO))
                survivors = [x[2] for x in scored[:elite_n]]

                # Build next generation via crossover + mutation
                new_pop = list(survivors)
                while len(new_pop) < POP_SIZE:
                    p1 = random.choice(survivors)
                    p2 = random.choice(survivors)
                    split = random.randint(1, len(subjects) - 1)
                    child = copy.deepcopy(p1[:split]) + copy.deepcopy(p2[split:])

                    if random.random() < MUTATION_RATE:
                        child = self._mutate(child, subjects_by_code,
                                             faculty_list, faculty_map, rooms)
                    new_pop.append(child)

                population = new_pop

            # Final violations on best found schedule
            final_violations = self.csp.validate(best_schedule, faculty_map)

            return {
                "success":        True,
                "schedule_data":  best_schedule,
                "violations":     final_violations,
                "conflict_count": len(final_violations),
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────
#  STANDALONE DRAFT VALIDATOR  (called from save-draft endpoint)
# ─────────────────────────────────────────────────────────────

def validate_draft(schedule_data: list, faculty_map: dict) -> dict:
    """
    Validate a draft schedule (e.g. after manual edits) against all hard constraints.

    Args:
        schedule_data : list of class dicts (same shape as generate_draft output)
        faculty_map   : dict keyed by employeenumber (fetch from DB before calling)

    Returns:
        {
            "valid":      bool,
            "violations": list of violation dicts,
            "can_publish": bool   (True only if violations is empty)
        }
    """
    validator  = CSPValidator()
    violations = validator.validate(schedule_data, faculty_map)
    return {
        "valid":       len(violations) == 0,
        "violations":  violations,
        "can_publish": len(violations) == 0,
    }

