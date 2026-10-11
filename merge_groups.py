"""HC16 Merge Groups — configuration, validation, MergeIndex, discovery and dry run.

Phase P1 of the HC16/HC17 redesign. A Merge Group is the administrator-authored
definition of ONE merged class: a semester, a reference subject, 2+ member
sections (each with its OWN curriculum subject, validated equivalent to the
reference), a faculty mode, and optionally the group's authoritative meeting(s).

Event identity
    A merged event is (mergegroupid, mergegroupmeetingid). A schedule occurrence
    belongs to it if and only if its (section, subject) is an active member of the
    group AND its day, start, end and room exactly equal that stored meeting.
    Nothing is stamped on schedule rows: session IDs change on every Save/Publish,
    so identity is always resolved against the stored meetings, here, in ONE place
    (MergeIndex). No other module or JavaScript re-implements it.

Configuration validity vs scheduling completeness (kept separate on purpose)
    validate_group()            -> may this group exist? (semester, members,
                                   subjects, equivalence, faculty mode, duplicates)
    scheduling_completeness()   -> are its meetings complete? UNSCHEDULED (none),
                                   INCOMPLETE (hours/blocks/rooms not satisfied),
                                   SCHEDULED (complete).

P7 "allowed" model (current)
    Sections MAY merge; nothing is ever required. A merged class exists only as a
    stored meeting, recorded when Save Draft / Publish places a class exactly on
    another section's equivalent class with the same faculty (or TBA) and the user
    confirms the merge notice (detect_merges / apply_merges). A member scheduled
    anywhere else is an ordinary class (classify -> 'separate'), never an error.
    Settings merge sets (create_merge_set) are groups without meetings: allowances.
    HC17 credits a merged class per section unless an explicit policy is mapped.

P1 boundary (history)
    Under the internal switch `hc_merge_model` = 'legacy' nothing in scheduling
    reads this module (the old pair/scope rules run unchanged). `hc_merge_model`
    is never exposed or accepted through the Settings hard-constraint endpoint.

The pure part (everything above the "Database layer" banner) needs no database,
so it is unit-testable without PostgreSQL. The database helpers take the
caller's cursor and never open their own connection.
"""
import json
import os
from collections import defaultdict
from datetime import date, time

import faculty_load

# ── Internal cutover switch ──────────────────────────────────────────────────────
MODEL_KEY = 'hc_merge_model'
MODEL_LEGACY = 'legacy'
MODEL_GROUPS = 'groups'
# Legacy HC16 configuration: read-only from P1 on (still enforced under 'legacy'),
# deleted only by the explicit retire step after the cutover is confirmed.
LEGACY_MERGE_KEYS = ('hc_merge_scope', 'hc_merge_scope_subjects', 'hc_merge_section_pairs')
# Never written through /admin/settings/hard_constraints; MODEL_KEY is also never returned.
FROZEN_CONFIG_KEYS = frozenset((MODEL_KEY,) + LEGACY_MERGE_KEYS)

SAME_FACULTY = 'SAME_FACULTY'
MULTIPLE_FACULTY = 'MULTIPLE_FACULTY'
FACULTY_MODES = (SAME_FACULTY, MULTIPLE_FACULTY)

BASIS_REFERENCE = 'reference'
BASIS_SAME_CODE = 'same_code'
BASIS_SAME_NAME_AND_HOURS = 'same_name_and_hours'
BASIS_ADMIN_OVERRIDE = 'admin_override'
EQUIVALENCE_BASES = (BASIS_REFERENCE, BASIS_SAME_CODE, BASIS_SAME_NAME_AND_HOURS, BASIS_ADMIN_OVERRIDE)

STATE_INVALID = 'INVALID'            # configuration itself is not valid
STATE_UNSCHEDULED = 'UNSCHEDULED'    # valid group, no meetings yet
STATE_INCOMPLETE = 'INCOMPLETE'      # valid group, meetings do not satisfy the subject yet
STATE_SCHEDULED = 'SCHEDULED'        # valid group, complete meetings (authoritative event)

CLASS_TYPES = ('Lecture', 'Lab')
DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
SEMESTER_LABELS = {'A': '1st Semester', 'B': '2nd Semester', 'C': 'Summer'}


def merge_model(cfg):
    """The active HC16 model. Anything but an explicit 'groups' is 'legacy', so a
    missing, blank or unknown value can never switch enforcement by accident."""
    raw = (cfg or {}).get(MODEL_KEY)
    return MODEL_GROUPS if str(raw or '').strip().lower() == MODEL_GROUPS else MODEL_LEGACY


# ── Small normalizers ────────────────────────────────────────────────────────────

def norm_code(code):
    return ' '.join(str(code or '').upper().split())


def norm_name(name):
    return ' '.join(str(name or '').lower().split())


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def hhmm(value):
    """'HH:MM' for a time / 'H:MM' / 'HH:MM:SS' value, None when absent."""
    if value is None or value == '':
        return None
    if isinstance(value, time):
        return f'{value.hour:02d}:{value.minute:02d}'
    text = str(value).strip()
    parts = text.split(':')
    if len(parts) < 2:
        return None
    try:
        return f'{int(parts[0]):02d}:{int(parts[1]):02d}'
    except ValueError:
        return None


def _minutes(value):
    t = hhmm(value)
    if not t:
        return None
    h, m = t.split(':')
    return int(h) * 60 + int(m)


def norm_room(value):
    """Room identity for exact comparison; None means room TBA."""
    if value is None or value == '':
        return None
    text = str(value).strip()
    if not text or text.upper() in ('TBA', 'NONE', 'NULL'):
        return None
    return int(text) if text.isdigit() else text


def section_label(programcode, yearlevel, sectionname):
    """Display label, same convention as faculty_load._YEAR_SECTION_SQL."""
    prog = str(programcode or '').strip()
    name = str(sectionname or '').strip()
    if not name:
        return f'{prog}-{yearlevel or ""}'
    if prog and name.upper().startswith(prog.upper()):
        return name
    return f'{prog}-{yearlevel or ""} {name}'.strip()


def semester_label(semestertype, yearstart=None, yearend=None, academicyearid=None):
    term = SEMESTER_LABELS.get(str(semestertype or '').strip().upper(), str(semestertype or ''))
    if yearstart and yearend:
        return f'AY {yearstart}-{yearend} · {term}'
    return f'{academicyearid or ""} · {term}'.strip(' ·')


# ── Subject equivalence (cross-curriculum) ───────────────────────────────────────

def teaching_structure(subject):
    """(lecture hours, lab hours, teaching hours) — the structure a single shared
    meeting pattern has to satisfy for every member. Teaching hours fall back to
    lecture+lab when tuitionhours is stored as 0 (a known data-entry quirk)."""
    lec = _num(subject.get('lecturehours'))
    lab = _num(subject.get('laboratoryhours'))
    tuition = _num(subject.get('tuitionhours')) or (lec + lab)
    return lec, lab, tuition


def required_parts(subject):
    """{class_type: required weekly hours}, mirroring the GA's own split in
    IntelligentScheduler._build_individual (lecture+lab / lab only / lecture,
    a zero-hour lecture defaulting to 3h)."""
    lec = _num(subject.get('lecturehours'))
    lab = _num(subject.get('laboratoryhours'))
    if lec > 0 and lab > 0:
        return {'Lecture': lec, 'Lab': lab}
    if lab > 0:
        return {'Lab': lab}
    return {'Lecture': lec or 3.0}


def subject_equivalence(ref, cand):
    """How `cand` (a member's own curriculum subject) is equivalent to the group's
    reference subject `ref`.

    Returns {'basis', 'override_eligible', 'differences'}:
      basis 'reference'            the reference subject itself
            'same_code'            same normalized subject code
            'same_name_and_hours'  same normalized name and same credit units
            None                   not automatically equivalent
    In EVERY case the teaching structure (lecture, lab and teaching hours) must be
    identical — one physical meeting pattern cannot satisfy different required
    hours. A structural mismatch is never override-eligible."""
    differences = []
    r_lec, r_lab, r_teach = teaching_structure(ref)
    c_lec, c_lab, c_teach = teaching_structure(cand)
    for label, a, b in (('lecture hours', r_lec, c_lec), ('lab hours', r_lab, c_lab),
                        ('teaching hours', r_teach, c_teach)):
        if abs(a - b) > 1e-9:
            differences.append(f'{label} {b:g} vs reference {a:g}')
    if _int(ref.get('curriculumsubjectid')) is not None and \
            _int(ref.get('curriculumsubjectid')) == _int(cand.get('curriculumsubjectid')):
        return {'basis': BASIS_REFERENCE, 'override_eligible': False, 'differences': []}
    if differences:
        return {'basis': None, 'override_eligible': False, 'differences': differences}
    if norm_code(ref.get('subjectcode')) == norm_code(cand.get('subjectcode')):
        return {'basis': BASIS_SAME_CODE, 'override_eligible': False, 'differences': []}
    same_name = norm_name(ref.get('subjectname')) == norm_name(cand.get('subjectname'))
    same_units = abs(_num(ref.get('creditunits')) - _num(cand.get('creditunits'))) < 1e-9
    if same_name and same_units:
        return {'basis': BASIS_SAME_NAME_AND_HOURS, 'override_eligible': False, 'differences': []}
    if not same_name:
        differences.append(f"name '{cand.get('subjectname')}' vs '{ref.get('subjectname')}'")
    if not same_units:
        differences.append(f"credit units {_num(cand.get('creditunits')):g} vs "
                           f"{_num(ref.get('creditunits')):g}")
    differences.insert(0, f"code '{cand.get('subjectcode')}' vs '{ref.get('subjectcode')}'")
    return {'basis': None, 'override_eligible': True, 'differences': differences}


# ── Payload normalization + configuration validity ──────────────────────────────

def normalize_payload(data):
    """Clean, typed copy of a create/update request body (unknown keys dropped)."""
    data = data or {}
    members = []
    for m in data.get('members') or []:
        if not isinstance(m, dict):
            continue
        members.append({
            'sectionid': _int(m.get('sectionid')),
            'curriculumsubjectid': _int(m.get('curriculumsubjectid')),
            'equivalence_basis': (str(m.get('equivalence_basis') or '').strip() or None),
            'equivalence_note': (str(m.get('equivalence_note') or '').strip() or None),
        })
    meetings = []
    for mt in data.get('meetings') or []:
        if not isinstance(mt, dict):
            continue
        meetings.append({
            'class_type': str(mt.get('class_type') or 'Lecture').strip(),
            'daydesc': str(mt.get('daydesc') or '').strip(),
            'starttimeid': _int(mt.get('starttimeid')),
            'endtimeid': _int(mt.get('endtimeid')),
            'roomid': _int(mt.get('roomid')) if norm_room(mt.get('roomid')) is not None else None,
        })
    mode = str(data.get('faculty_mode') or '').strip().upper()
    emp = str(data.get('employeenumber') or '').strip() or None
    if emp and emp.upper() == 'TBA':
        emp = None
    active = data.get('is_active', True)
    return {
        'groupname': ' '.join(str(data.get('groupname') or '').split()),
        'semesterid': _int(data.get('semesterid')),
        'ref_curriculumsubjectid': _int(data.get('ref_curriculumsubjectid')),
        'faculty_mode': mode,
        'employeenumber': emp,
        'is_active': bool(active) if not isinstance(active, str) else active.lower() in ('1', 'true', 'yes', 'on'),
        'members': members,
        'meetings': meetings,
    }


def validate_group(payload, ctx):
    """Configuration validity of a normalized payload against `ctx` (see
    load_validation_context). Returns (errors, resolved_members); each resolved
    member carries its computed equivalence basis. Scheduling completeness is NOT
    checked here (see scheduling_completeness)."""
    errors = []
    sem = ctx.get('semester')
    if not payload.get('groupname'):
        errors.append('Group name is required.')
    elif len(payload['groupname']) > 100:
        errors.append('Group name must be at most 100 characters.')
    elif payload['groupname'].lower() in ctx.get('other_names', set()):
        errors.append(f"Another merge group in this semester is already named '{payload['groupname']}'.")
    if not sem:
        errors.append('A valid semester is required.')
    if payload.get('faculty_mode') not in FACULTY_MODES:
        errors.append('Faculty mode must be Same Faculty Required or Multiple Faculty Allowed.')
    emp = payload.get('employeenumber')
    if emp:
        if payload.get('faculty_mode') != SAME_FACULTY:
            errors.append('A designated faculty can only be set for a Same Faculty Required group.')
        fac = (ctx.get('faculty') or {}).get(emp)
        if not fac:
            errors.append(f'Designated faculty {emp} does not exist.')
        elif not fac.get('isactive', True):
            errors.append(f'Designated faculty {emp} is inactive.')

    subjects = ctx.get('subjects') or {}
    ref = subjects.get(payload.get('ref_curriculumsubjectid'))
    if not ref:
        errors.append('The group subject must be a subject offered in this semester.')

    members = payload.get('members') or []
    section_ids = [m.get('sectionid') for m in members]
    if len({s for s in section_ids if s is not None}) < 2:
        errors.append('A merge group needs at least 2 participating sections.')
    if len(section_ids) != len(set(section_ids)):
        errors.append('A section can only be listed once in a merge group.')

    sections = ctx.get('sections') or {}
    taken = ctx.get('active_memberships') or {}
    resolved = []
    for m in members:
        sid, csid = m.get('sectionid'), m.get('curriculumsubjectid')
        sec = sections.get(sid)
        if not sec:
            errors.append(f'Section {sid} is not a section of this semester\'s academic year.')
            continue
        label = sec.get('label') or str(sid)
        if not sec.get('isactive', True):
            errors.append(f'Section {label} is inactive.')
        subj = subjects.get(csid)
        if not subj or csid not in sec.get('offered', set()):
            errors.append(f'{label}: the selected subject is not offered to this section in this semester.')
            continue
        basis_info = subject_equivalence(ref, subj) if ref else {'basis': None, 'override_eligible': False,
                                                                 'differences': []}
        basis = basis_info['basis']
        note = m.get('equivalence_note')
        if basis is None:
            if m.get('equivalence_basis') == BASIS_ADMIN_OVERRIDE and basis_info['override_eligible']:
                # The written note is optional; the override itself is still audited
                # (who/when) and the differences stay visible on the group.
                basis = BASIS_ADMIN_OVERRIDE
            elif basis_info['override_eligible']:
                errors.append(f"{label}: {subj.get('subjectcode')} is not automatically equivalent to "
                              f"{ref.get('subjectcode') if ref else 'the group subject'} "
                              f"({'; '.join(basis_info['differences'])}). An administrator override "
                              f"is required.")
            elif ref:
                errors.append(f"{label}: {subj.get('subjectcode')} cannot be merged with "
                              f"{ref.get('subjectcode')} — incompatible teaching hours "
                              f"({'; '.join(basis_info['differences'])}).")
        key_csid = (sid, csid)
        key_code = (sid, norm_code(subj.get('subjectcode')))
        clash = taken.get(key_csid) or taken.get(key_code)
        if clash and payload.get('is_active'):
            errors.append(f"{label} already takes {subj.get('subjectcode')} in active merge group "
                          f"'{clash}'. A section's subject can belong to only one active group.")
        resolved.append(dict(m, equivalence_basis=basis,
                             equivalence_note=note if basis == BASIS_ADMIN_OVERRIDE else None,
                             subjectcode=subj.get('subjectcode'), label=label,
                             differences=basis_info['differences']))

    errors.extend(validate_meeting_structure(payload.get('meetings') or [], ctx))
    return errors, resolved


def validate_meeting_structure(meetings, ctx):
    """Meetings that can be STORED at all (well-formed, existing time slots/rooms,
    no duplicate or overlapping meeting). Whether they are COMPLETE is separate."""
    errors = []
    times = ctx.get('timeslots') or {}
    rooms = ctx.get('rooms') or {}
    seen = []
    for i, mt in enumerate(meetings, 1):
        tag = f'Meeting {i}'
        if mt.get('class_type') not in CLASS_TYPES:
            errors.append(f'{tag}: class type must be Lecture or Lab.')
        if mt.get('daydesc') not in DAYS:
            errors.append(f'{tag}: a valid day is required.')
        st, et = times.get(mt.get('starttimeid')), times.get(mt.get('endtimeid'))
        if st is None or et is None:
            errors.append(f'{tag}: start and end must be existing time slots.')
            continue
        if _minutes(et) <= _minutes(st):
            errors.append(f'{tag}: end time must be after start time.')
            continue
        if mt.get('roomid') is not None and mt['roomid'] not in rooms:
            errors.append(f'{tag}: room {mt["roomid"]} does not exist.')
        for j, (day, s, e) in enumerate(seen, 1):
            if day == mt.get('daydesc') and _minutes(st) < e and _minutes(et) > s:
                errors.append(f'{tag} overlaps meeting {j} on {day}.')
        seen.append((mt.get('daydesc'), _minutes(st), _minutes(et)))
    return errors


# ── Scheduling completeness ──────────────────────────────────────────────────────

def scheduling_completeness(ref_subject, meetings, *, valid_blocks=None, rooms=None,
                            lab_rooms_required=True):
    """State of the group's meetings against its reference subject.

    meetings: [{'class_type','daydesc','start','end','roomid'}] with 'HH:MM' times.
    valid_blocks: set of ('HH:MM','HH:MM') HC6 blocks, or None to skip that check.
    Returns {'state','issues','warnings','required','scheduled','complete_types'};
    `issues` make a group INCOMPLETE, `warnings` (e.g. room TBA) do not.
    `complete_types` lists the class types whose meetings are themselves complete
    (exact required hours, valid blocks, suitable rooms) — HC16 only treats a
    meeting of a complete class type as a usable merged event."""
    required = required_parts(ref_subject or {})
    scheduled = {k: 0.0 for k in CLASS_TYPES}
    issues, warnings = [], []
    if not meetings:
        return {'state': STATE_UNSCHEDULED, 'issues': ['No meetings scheduled yet.'],
                'warnings': [], 'required': required, 'scheduled': scheduled, 'complete_types': []}
    rooms = rooms or {}
    bad_types = set()
    for i, mt in enumerate(meetings, 1):
        ctype = mt.get('class_type')
        start, end = hhmm(mt.get('start')), hhmm(mt.get('end'))
        tag = f"{mt.get('daydesc')} {start}-{end}"
        if ctype not in required:
            issues.append(f'{tag}: this subject has no {ctype} hours.')
            bad_types.add(ctype)
        if start and end:
            scheduled[ctype] = scheduled.get(ctype, 0.0) + (_minutes(end) - _minutes(start)) / 60.0
            if valid_blocks is not None and (start, end) not in valid_blocks:
                issues.append(f'{tag}: not a valid time block (HC6).')
                bad_types.add(ctype)
        room_id = norm_room(mt.get('roomid'))
        if room_id is None:
            warnings.append(f'{tag}: room is TBA.')
        elif ctype == 'Lab' and lab_rooms_required:
            room = rooms.get(room_id) or {}
            if str(room.get('roomtype') or '').lower() != 'laboratory':
                issues.append(f"{tag}: a Lab meeting needs a Laboratory room "
                              f"({room.get('roomname') or room_id} is {room.get('roomtype') or 'unknown'}).")
                bad_types.add(ctype)
    for ctype, need in required.items():
        got = round(scheduled.get(ctype, 0.0), 2)
        if abs(got - need) > 1e-6:
            issues.append(f'{ctype}: {got:g}h scheduled of {need:g}h required.')
            bad_types.add(ctype)
    complete = [t for t in required if t not in bad_types]
    return {'state': STATE_INCOMPLETE if issues else STATE_SCHEDULED, 'issues': issues,
            'warnings': warnings, 'required': required,
            'scheduled': {k: round(v, 2) for k, v in scheduled.items() if v or k in required},
            'complete_types': complete}


# ── MergeIndex: the ONE event-identity resolver ─────────────────────────────────

def _occ_section(o):
    return _int(o.get('sectionid') if o.get('sectionid') is not None else o.get('section_id'))


def _occ_label(o):
    prog = o.get('programcode') or o.get('course') or o.get('program')
    name = o.get('sectionname') or o.get('section_name')
    if not prog or not name:
        return None
    return (str(prog).strip().upper(), str(name).strip().upper())


def _occ_code(o):
    return norm_code(o.get('subject_code') or o.get('subjectcode'))


def _occ_day(o):
    # Faculty-load rows (faculty_load.FACULTY_SESSIONS_SQL) carry one day as `days`.
    return o.get('day') or o.get('daydesc') or (o.get('days') if o.get('days') in DAYS else None)


def _occ_room(o):
    return norm_room(o.get('roomid') if 'roomid' in o else o.get('room_id'))


def _occ_faculty(o):
    return o.get('faculty_id') or o.get('employeenumber') or o.get('faculty_employeenumber')


class MergeIndex:
    """Membership and merged-event resolution for one set of loaded groups — the
    ONE place merged-event identity is decided (scheduler.CSPValidator, app.py's
    cross-schedule / Local / request checks and the Settings previews all call it).

    Only ACTIVE groups whose configuration is valid participate. Membership is
    known for every such group (an UNSCHEDULED group still owns its members'
    subject). An occurrence has an event only when it exactly matches a stored
    meeting (day, start, end, room); every stored meeting is a usable merged class
    (P7); and `same_event` further requires two different sections and a satisfied
    faculty mode. Occurrences may give times as
    time objects / 'HH:MM' strings or as timeslot ids, and the day either as one
    `day`/`daydesc` or per call (`day=` — multi-day genes are judged day by day)."""

    def __init__(self, groups=()):
        self._groups = {}
        self._members = {}
        self._labels = {}
        self._meetings = defaultdict(dict)     # gid -> {(day, 'HH:MM', 'HH:MM', room): meeting}
        self._meetings_by_id = defaultdict(dict)   # gid -> {(day, stid, etid, room): meeting}
        for g in groups or ():
            if not g.get('is_active') or g.get('config_errors'):
                continue
            gid = g['mergegroupid']
            self._groups[gid] = g
            for m in g.get('members') or ():
                sid = _int(m.get('sectionid'))
                self._members[(sid, norm_code(m.get('subjectcode')))] = gid
                label = _occ_label(m)
                if label:
                    self._labels[label] = sid
            for mt in g.get('meetings') or ():
                room = norm_room(mt.get('roomid'))
                self._meetings[gid][(mt.get('daydesc'), hhmm(mt.get('start')), hhmm(mt.get('end')), room)] = mt
                if mt.get('starttimeid') is not None and mt.get('endtimeid') is not None:
                    self._meetings_by_id[gid][(mt.get('daydesc'), _int(mt['starttimeid']),
                                               _int(mt['endtimeid']), room)] = mt

    def __len__(self):
        return len(self._groups)

    def group(self, gid):
        return self._groups.get(gid)

    def member_section_ids(self):
        return sorted({sid for (sid, _code) in self._members if sid is not None})

    def section_of(self, occ):
        sid = _occ_section(occ)
        if sid is None:
            label = _occ_label(occ)
            sid = self._labels.get(label) if label else None
        return sid

    def member_of(self, occ):
        """mergegroupid owning this occurrence's (section, subject), else None."""
        return self._members.get((self.section_of(occ), _occ_code(occ)))

    def meeting_of(self, occ, day=None):
        """The stored meeting this occurrence exactly is (on `day`), else None."""
        gid = self.member_of(occ)
        if gid is None:
            return None
        own_days = [x for x in (occ.get('days_list') or [_occ_day(occ)]) if x]
        if day and own_days and day not in own_days:
            return None   # the occurrence does not meet on that day at all
        d = day or _occ_day(occ)
        room = _occ_room(occ)
        start = hhmm(occ.get('start_time') or occ.get('start'))
        end = hhmm(occ.get('end_time') or occ.get('end'))
        if start and end:
            return self._meetings[gid].get((d, start, end, room))
        stid, etid = _int(occ.get('starttimeid')), _int(occ.get('endtimeid'))
        if stid is not None and etid is not None:
            return self._meetings_by_id[gid].get((d, stid, etid, room))
        return None

    def event_of(self, occ, day=None):
        """(mergegroupid, mergegroupmeetingid) when the occurrence is exactly a stored
        meeting of its own group, else None."""
        mt = self.meeting_of(occ, day)
        return (self.member_of(occ), mt.get('mergegroupmeetingid')) if mt is not None else None

    def usable_meeting_ids(self, gid):
        """Meetings of `gid` that form merged events. P7 ("allowed" model): every stored
        meeting is a merged class on its own — a subject may merge only some of its
        meetings, so completeness no longer gates event identity (it still decides
        what generation pins, see generation_plan)."""
        g = self._groups.get(gid) or {}
        return {m.get('mergegroupmeetingid') for m in g.get('meetings') or ()}

    def event_usable(self, ev):
        """True when `ev` is a stored meeting of an indexed group."""
        if ev is None:
            return False
        g = self._groups.get(ev[0]) or {}
        return any(m.get('mergegroupmeetingid') == ev[1] for m in g.get('meetings') or ())

    def faculty_ok(self, gid, fa, fb):
        """SAME_FACULTY: A+A valid; A+TBA and TBA+TBA compatible (incomplete); A+B
        invalid; a designated faculty must match every known faculty.
        MULTIPLE_FACULTY: always compatible."""
        g = self._groups.get(gid) or {}
        if g.get('faculty_mode') != SAME_FACULTY:
            return True
        known = [str(f).strip() for f in (fa, fb) if faculty_load.has_assigned_faculty(f)]
        designated = g.get('employeenumber')
        if designated and any(f != str(designated).strip() for f in known):
            return False
        return len(set(known)) <= 1

    def same_event(self, a, b, day=None):
        """True only for two DIFFERENT sections' occurrences of the same USABLE merged
        event (same group AND same meeting) whose faculty mode they satisfy."""
        ev = self.event_of(a, day)
        if ev is None or ev != self.event_of(b, day):
            return False
        if self.section_of(a) == self.section_of(b):
            return False
        if not self.event_usable(ev):
            return False
        return self.faculty_ok(ev[0], _occ_faculty(a), _occ_faculty(b))

    def classify(self, occ, day=None):
        """None (not a group member) | 'event' (exactly at one of its group's merged
        meetings) | 'separate' (a member scheduled anywhere else: an ordinary class of
        its own section). P7 "allowed" model: being off the group's slot is never an
        error and never blocks — merging is something a section opts into."""
        gid = self.member_of(occ)
        if gid is None:
            return None
        return 'event' if self.event_of(occ, day) is not None else 'separate'


# ── Group-mode HC16 policy (consumed by every server-side conflict path) ─────────

POLICY_CFG_KEY = '_merge_policy'   # where callers hand a policy to CSPValidator via its config

HC16_DIVERGED = 'HC16_DIVERGED'          # invalid: off the group's authoritative meeting
HC16_UNSCHEDULED = 'HC16_UNSCHEDULED'    # incomplete: no usable meeting for it yet
HC16_FACULTY = 'HC16_FACULTY'            # invalid: incompatible known faculty / not the designee
HC16_FACULTY_TBA = 'HC16_FACULTY_TBA'    # incomplete: SAME_FACULTY event with TBA faculty
INVALID_CODES = frozenset((HC16_DIVERGED, HC16_FACULTY))
INCOMPLETE_CODES = frozenset((HC16_UNSCHEDULED, HC16_FACULTY_TBA))
PUBLISH_BLOCKING_INCOMPLETE = frozenset((HC16_UNSCHEDULED,))


def _occ_days(o):
    return [d for d in (o.get('days_list') or [_occ_day(o)]) if d]


def _fmt12(hm):
    m = _minutes(hm)
    if m is None:
        return '?'
    h, mm = divmod(m, 60)
    return f"{(h + 11) % 12 + 1}:{mm:02d} {'PM' if h >= 12 else 'AM'}"


class GroupMergePolicy:
    """HC16 in 'groups' mode, over one MergeIndex. The ONLY merge interpretation
    the group model uses: no legacy scope, no section pairs, no NSTP/OU prefix
    rule and no nstp_shared_faculty_exempt — an overlap is exempt from HC10/HC11
    only between occurrences of the SAME usable merged event (same group AND same
    meeting) whose faculty mode they satisfy. HC12 is never exempt.

    `default_section_id` stamps the caller's section on occurrences that carry no
    section identity (single-section payloads such as a Save Draft batch)."""

    mode = MODEL_GROUPS

    def __init__(self, index=None, *, default_section_id=None, enabled=True, load_rules=None):
        self.index = index if index is not None else MergeIndex()
        self.default_section_id = _int(default_section_id)
        self.enabled = bool(enabled)
        # HC17: {mergegroupid: [{'policyid','policyname','subjectcode','min_sections',
        # 'max_sections'}]} — ONLY explicit policy-to-group mappings
        # (merge_load_policy_group); never subject codes or prefixes.
        self.load_rules = dict(load_rules or {})

    def _occ(self, o):
        if self.default_section_id is not None and self.index.section_of(o) is None:
            return dict(o, section_id=self.default_section_id)
        return o

    # HC10 / HC11 -------------------------------------------------------------
    def same_event(self, a, b, day=None):
        if not self.enabled:
            return False
        return self.index.same_event(self._occ(a), self._occ(b), day)

    def exempt_days(self, a, b, days):
        """The subset of `days` on which a and b are the same usable merged event."""
        return [d for d in days or () if self.same_event(a, b, day=d)]

    # Messages ----------------------------------------------------------------
    def _section_label(self, o, gid):
        prog = o.get('programcode') or o.get('course')
        name = o.get('section_name') or o.get('sectionname')
        if name:
            return section_label(prog, None, name) if prog else str(name)
        sid = self.index.section_of(self._occ(o))
        for m in (self.index.group(gid) or {}).get('members') or ():
            if _int(m.get('sectionid')) == sid:
                return m.get('label') or section_label(m.get('programcode'), m.get('yearlevel'),
                                                       m.get('sectionname'))
        return f'section {sid}' if sid is not None else 'this section'

    @staticmethod
    def _meeting_text(m):
        return (f"{m.get('class_type', 'Lecture')} {m.get('daydesc')} {_fmt12(m.get('start'))}–"
                f"{_fmt12(m.get('end'))} in {m.get('roomname') or ('room TBA' if m.get('roomid') is None else m.get('roomid'))}")

    @staticmethod
    def _actual_text(o, day):
        start = hhmm(o.get('start_time') or o.get('start'))
        end = hhmm(o.get('end_time') or o.get('end'))
        room = o.get('room') or o.get('roomname') or (
            'room TBA' if _occ_room(o) is None else f'room {_occ_room(o)}')
        return f"{day} {_fmt12(start)}–{_fmt12(end)} in {room}"

    def _violation(self, code, o, gid, days, detail, *, expected=None, faculty=None):
        g = self.index.group(gid) or {}
        incomplete = code in INCOMPLETE_CODES
        v = {
            'rule': 'HC16', 'code': code,
            'merge_state': 'incomplete' if incomplete else 'invalid',
            'blocks_publish': code in INVALID_CODES or code in PUBLISH_BLOCKING_INCOMPLETE,
            'subject': (o.get('subject_code') or o.get('subjectcode') or '?'),
            'subject_code': (o.get('subject_code') or o.get('subjectcode') or '?'),
            'group': g.get('groupname'), 'section_name': self._section_label(o, gid),
            'normalized_days': list(days),
            'expected': expected if expected is not None else [self._meeting_text(m) for m in g.get('meetings') or ()],
            'actual': [self._actual_text(o, d) for d in days],
            'affected_components': (['instructor'] if code in (HC16_FACULTY, HC16_FACULTY_TBA)
                                    else ['day', 'time', 'room']),
            'detail': detail,
        }
        if faculty is not None:
            v['faculty'] = faculty
        if incomplete:
            v['severity'] = 'warning'   # never blocks a Draft; Publish checks blocks_publish
        for key in ('assignment_id', 'id', 'schedulesessionid', 'schedule_id', 'scheduleid'):
            if o.get(key) is not None:
                v.setdefault('assignment_id', o.get(key))
        return v

    # HC16 consistency of the given occurrences ---------------------------------
    def consistency_violations(self, rows):
        """HC16_FACULTY (designee) / HC16_FACULTY_TBA for every occurrence in `rows`
        that sits at a merged meeting. P7 "allowed" model: a member scheduled off its
        group's meetings is an ordinary class, so HC16_DIVERGED / HC16_UNSCHEDULED
        are no longer raised (the codes stay defined for older clients)."""
        if not self.enabled or not len(self.index):
            return []
        out = []
        for raw in rows or ():
            o = self._occ(raw)
            gid = self.index.member_of(o)
            if gid is None:
                continue
            g = self.index.group(gid) or {}
            by_state = defaultdict(list)
            for d in _occ_days(o):
                by_state[self.index.classify(o, day=d)].append(d)
            code = o.get('subject_code') or o.get('subjectcode')
            sec = self._section_label(o, gid)
            if by_state.get('event') and g.get('faculty_mode') == SAME_FACULTY:
                fac = _occ_faculty(o)
                designated = g.get('employeenumber')
                days = by_state['event']
                if faculty_load.has_assigned_faculty(fac):
                    if designated and str(fac).strip() != str(designated).strip():
                        out.append(self._violation(
                            HC16_FACULTY, o, gid, days,
                            f"{sec} {code}: merged class '{g.get('groupname')}' requires its designated "
                            f"faculty {designated}, but this class is assigned {fac}.",
                            expected=[str(designated)], faculty={'expected': designated, 'actual': fac}))
                else:
                    out.append(self._violation(
                        HC16_FACULTY_TBA, o, gid, days,
                        f"{sec} {code}: merged class '{g.get('groupname')}' requires the same faculty for "
                        f"every section; this section's faculty is still TBA.",
                        expected=[str(designated)] if designated else [], faculty={'expected': designated,
                                                                                  'actual': None}))
        return out

    def faculty_conflicts(self, rows, others):
        """HC16_FACULTY between `rows` and `others` (e.g. other member sections'
        Published occurrences): the same SAME_FACULTY event with two different known
        faculty. TBA never makes two different known faculty compatible."""
        if not self.enabled or not len(self.index):
            return []
        out, seen = [], set()
        for raw in rows or ():
            a = self._occ(raw)
            for d in _occ_days(a):
                ev = self.index.event_of(a, day=d)
                if ev is None:
                    continue
                g = self.index.group(ev[0]) or {}
                if g.get('faculty_mode') != SAME_FACULTY:
                    continue
                fa = _occ_faculty(a)
                if not faculty_load.has_assigned_faculty(fa):
                    continue
                for b in others or ():
                    if self.index.section_of(b) == self.index.section_of(a):
                        continue
                    if d not in _occ_days(b) or self.index.event_of(b, day=d) != ev:
                        continue
                    fb = _occ_faculty(b)
                    if not faculty_load.has_assigned_faculty(fb) or str(fa).strip() == str(fb).strip():
                        continue
                    key = (ev, d, str(fa).strip(), str(fb).strip())
                    if key in seen:
                        continue
                    seen.add(key)
                    other_sec = self._section_label(b, ev[0])
                    out.append(self._violation(
                        HC16_FACULTY, a, ev[0], [d],
                        f"{self._section_label(a, ev[0])} {a.get('subject_code') or a.get('subjectcode')}: "
                        f"merged class '{g.get('groupname')}' requires the same faculty for every section, "
                        f"but this section has {fa} while {other_sec} has {fb}.",
                        expected=[str(fb)], faculty={'expected': fb, 'actual': fa, 'other_section': other_sec}))
        return out


    # HC17 — merged-class faculty load, consuming HC16's event identity ---------
    def hc17_rule_for(self, gid, section_count):
        """The explicit HC17 policy mapped to group `gid` whose section range covers
        `section_count`, else None (fallback). Mapped ranges never overlap for one
        group (enforced on save and by the database), so at most one matches."""
        hits = [r for r in self.load_rules.get(gid, ())
                if _int(r.get('min_sections')) <= section_count <= _int(r.get('max_sections'))]
        return min(hits, key=lambda r: _int(r.get('policyid')) or 0) if hits else None

    def _usable_event(self, occ, fac, day):
        """HC16 event of this faculty's occurrence on `day` that may receive merged
        load, else None: an assigned faculty, a usable event (complete class type),
        and the group's faculty mode/designee satisfied for that faculty."""
        if not self.enabled or not faculty_load.has_assigned_faculty(fac):
            return None
        ev = self.index.event_of(occ, day=day)
        if ev is None or not self.index.event_usable(ev):
            return None
        return ev if self.index.faculty_ok(ev[0], fac, fac) else None

    def credit_rows(self, rows, *, hours=None):
        """THE group-mode HC17 load calculation (every load path uses it).

        `rows` are teaching occurrences (any mix of faculty); `hours(row)` gives a
        row's real scheduled hours (default: row['hrs']), split evenly across the
        row's meeting days. For each faculty:
          1. a row-day with no usable HC16 event is a normal assignment — full hours;
          2. P7 default: a merged class with NO HC17 policy mapped to its group (for
             the faculty's section count) is credited PER SECTION — every section's
             row-day keeps its full hours and the subject's units, exactly like
             separate classes. Merging shares the room and slot, not the load;
          3. only when an explicit HC17 policy (e.g. the NSTP rule) is mapped to the
             group for that section count is the load shared: row-days of the SAME
             event (same group + same meeting) count ONCE for that faculty — the
             first one in `rows` keeps the hours (pass already counted rows first),
             the rest add 0 — and when the faculty teaches EVERY meeting of the
             group, the kept hours are scaled to the group subject's curriculum
             teaching hours (credited once, Regular/PT split kept proportional),
             with the policy's curriculum units credited once.
        Returns one dict per row: credit (hours), merged_group (set only for a
        policy-shared assignment), events, keeper, section_count, sections, rule
        (policy dict or None), units (credited units for a shared assignment)."""
        hours = hours or (lambda r: _num(r.get('hrs')))
        meta = [{'credit': 0.0, 'merged_group': None, 'events': [], 'keeper': False,
                 'section_count': 0, 'sections': [], 'rule': None, 'units': None} for _ in rows]
        # Pass 1: which (faculty, group) assignments exist and with how many sections,
        # so each one's HC17 policy (or per-section default) is known up front.
        plan, sections_of = [], defaultdict(set)
        for i, raw in enumerate(rows):
            occ = self._occ(raw)
            fac = _occ_faculty(occ)
            days = _occ_days(occ) or [None]
            share = hours(raw) / len(days)
            for d in days:
                ev = self._usable_event(occ, fac, d) if d else None
                plan.append((i, occ, fac, d, share, ev))
                if ev is not None:
                    sections_of[(str(fac).strip(), ev[0])].add(self.index.section_of(occ))
        shared = {k for k, secs in sections_of.items() if self.hc17_rule_for(k[1], len(secs))}

        keepers, assign = {}, {}
        for i, occ, fac, d, share, ev in plan:
            if ev is None or (str(fac).strip(), ev[0]) not in shared:
                meta[i]['credit'] += share     # normal class, or per-section merged default
                continue
            key = (str(fac).strip(), ev)
            meta[i]['merged_group'] = ev[0]
            meta[i]['events'].append(ev)
            a = assign.setdefault((key[0], ev[0]), {'kept': [], 'sections': set()})
            a['sections'].add(self.index.section_of(occ))
            if key in keepers:
                continue              # the same shared event for this faculty: once
            keepers[key] = i
            meta[i]['keeper'] = True
            a['kept'].append((i, share, ev[1]))
        for (fac, gid), a in assign.items():
            n = len(a['sections'])
            rule = self.hc17_rule_for(gid, n)
            g = self.index.group(gid) or {}
            kept_hours = sum(h for _i, h, _mid in a['kept'])
            taught = {mid for _i, _h, mid in a['kept']}
            teaches_all = bool(taught) and self.index.usable_meeting_ids(gid) <= taught
            target = _num(g.get('ref_teachinghours'))
            factor = (target / kept_hours) if (rule and teaches_all and kept_hours > 0 and target > 0) else 1.0
            for i, h, _mid in a['kept']:
                meta[i]['credit'] += h * factor
            for i, raw in enumerate(rows):
                if meta[i]['merged_group'] == gid and str(_occ_faculty(self._occ(raw)) or '').strip() == fac:
                    meta[i].update(section_count=n, sections=sorted(s for s in a['sections'] if s is not None),
                                   rule=rule, partial=not teaches_all,
                                   units=(_num(g.get('ref_creditunits')) if rule else None))
        for m in meta:
            m['credit'] = round(m['credit'], 4)
        return meta

    def collapse_for_display(self, rows, *, hours=None, label=None):
        """Display rows for ONE faculty's load: every merged event becomes one row
        carrying all its participating sections (year_section joined), rooms joined
        and credited hours; duplicate member rows disappear; normal rows pass through.
        Uses credit_rows, so displayed hours always equal enforced hours."""
        meta = self.credit_rows(rows, hours=hours)
        labels_by_assignment = defaultdict(list)
        rooms_by_assignment = defaultdict(list)
        for raw, m in zip(rows, meta):
            if m['merged_group'] is None:
                continue
            key = (m['merged_group'], str(_occ_faculty(self._occ(raw)) or '').strip())
            lbl = (label(raw) if label else None) or raw.get('year_section')
            if lbl and lbl not in labels_by_assignment[key]:
                labels_by_assignment[key].append(lbl)
            room = raw.get('room')
            if room and room not in rooms_by_assignment[key]:
                rooms_by_assignment[key].append(room)
        out = []
        for raw, m in zip(rows, meta):
            if m['merged_group'] is None:
                out.append(dict(raw, hrs=round(m['credit'], 2)))
                continue
            if not m['keeper']:
                continue
            key = (m['merged_group'], str(_occ_faculty(self._occ(raw)) or '').strip())
            g = self.index.group(m['merged_group']) or {}
            row = dict(raw, hrs=round(m['credit'], 2),
                       year_section=', '.join(labels_by_assignment[key]) or raw.get('year_section'),
                       merge_group=g.get('groupname'), merged_sections=m['section_count'],
                       merge_policy=(m['rule'] or {}).get('policyname') or (m['rule'] or {}).get('subjectcode'))
            if rooms_by_assignment[key]:
                row['room'] = ', '.join(rooms_by_assignment[key])
            if m['units'] is not None:
                row['units'] = m['units']
            out.append(row)
        return out


def generation_plan(policy, section_id, live_occurrences=()):
    """What automated generation must do with this section's Merge Group subjects
    (group model). Deterministic for the same configuration and live data — it never
    depends on which member section is generated first: the group's MEETINGS are the
    anchor, never a section.

    Returns {(SUBJECT CODE, class_type): entry} with
      state     'pinned'      — day/time/room come from the group's meeting(s)
      sets      [{'days','start','end','starttimeid','endtimeid','roomid','roomname',
                  'roomtype','meeting_ids'}] — meetings sharing time+room form one set
      faculty   {'status': designated|known|conflict|tba|free, 'faculty_id', 'known'}
      events    usable (mergegroupid, mergegroupmeetingid) of the group
    P7 "allowed" model: only a class type whose merged meetings are COMPLETE (they
    cover all of its hours) is pinned; a class type with no or only some merged
    meetings gets no entry and is generated like any other subject (merging stays
    opt-in). The top-level 'divergent' list is kept for callers and stays empty.
    `live_occurrences`: other member sections' effective Published occurrences."""
    plan, divergent = {}, []
    sid = _int(section_id)
    if policy is None or sid is None:
        return {'entries': plan, 'divergent': divergent}
    idx = policy.index
    for (member_sid, code), gid in sorted(idx._members.items(), key=lambda kv: (str(kv[0][1]), kv[1])):
        if member_sid != sid:
            continue
        g = idx.group(gid) or {}
        complete = _complete_types(g)
        required = list(((g.get('completeness') or {}).get('required') or {}).keys()) or \
            sorted({m.get('class_type', 'Lecture') for m in g.get('meetings') or ()}) or ['Lecture']
        usable = {(gid, mid) for mid in idx.usable_meeting_ids(gid)}

        # SAME_FACULTY: designee, else the one known faculty of the OTHER members' valid
        # live events; never the first generated section's choice.
        mode = g.get('faculty_mode')
        if mode == SAME_FACULTY:
            if g.get('employeenumber'):
                fac = {'status': 'designated', 'faculty_id': str(g['employeenumber']), 'known': []}
            else:
                known = sorted({str(_occ_faculty(o)).strip() for o in live_occurrences
                                if idx.section_of(o) != sid and idx.event_of(o) in usable
                                and faculty_load.has_assigned_faculty(_occ_faculty(o))})
                fac = ({'status': 'known', 'faculty_id': known[0], 'known': known} if len(known) == 1 else
                       {'status': 'conflict', 'faculty_id': None, 'known': known} if known else
                       {'status': 'tba', 'faculty_id': None, 'known': []})
        else:
            fac = {'status': 'free', 'faculty_id': None, 'known': []}

        for ctype in required:
            mts = [m for m in g.get('meetings') or () if m.get('class_type', 'Lecture') == ctype]
            if not mts or ctype not in complete:
                continue       # not (fully) merged: generated normally
            entry = {'group': gid, 'groupname': g.get('groupname'), 'faculty_mode': mode,
                     'class_type': ctype, 'faculty': fac, 'events': usable, 'sets': [],
                     'state': 'pinned'}
            sets = {}
            for m in sorted(mts, key=lambda m: (DAYS.index(m['daydesc']) if m.get('daydesc') in DAYS else 9,
                                               hhmm(m.get('start')) or '')):
                key = (hhmm(m.get('start')), hhmm(m.get('end')), norm_room(m.get('roomid')))
                s = sets.setdefault(key, {'days': [], 'start': key[0], 'end': key[1],
                                          'starttimeid': m.get('starttimeid'), 'endtimeid': m.get('endtimeid'),
                                          'roomid': key[2], 'roomname': m.get('roomname'),
                                          'roomtype': m.get('roomtype'), 'meeting_ids': []})
                s['days'].append(m['daydesc'])
                s['meeting_ids'].append(m.get('mergegroupmeetingid'))
            entry['sets'] = list(sets.values())
            plan[(norm_code(code), ctype)] = entry
    return {'entries': plan, 'divergent': divergent}


def _complete_types(g):
    """Class types whose stored meetings are complete (see scheduling_completeness)."""
    comp = g.get('completeness')
    if comp is not None and 'complete_types' in comp:
        return set(comp['complete_types'])
    return set(CLASS_TYPES) if g.get('state') == STATE_SCHEDULED else set()


def _to_time(hm):
    m = _minutes(hm)
    return time(m // 60, m % 60) if m is not None else None


def pin_rows(rows, plan):
    """Apply a generation plan to already-built schedule rows (exact reuse / Retrieve
    Previous): the CURRENT group meeting always wins over any historical day/time/room.
    Pinned rows take the meeting's day/time/room (one extra row per further meeting
    set) and, for SAME_FACULTY, the designated/known faculty; an unscheduled merged
    subject keeps no slot. Returns (rows, reports)."""
    entries = (plan or {}).get('entries') or {}
    out, reports = [], []

    def _incomplete(r, reason):
        r['incomplete'] = True
        reasons = list(r.get('incomplete_reason') or [])
        if reason not in reasons:
            reasons.append(reason)
        r['incomplete_reason'] = reasons

    def _slot(r, s):
        st, et = _to_time(s['start']), _to_time(s['end'])
        r.update(start_time=st, end_time=et, days_list=list(s['days']), day=s['days'][0],
                 days='/'.join(d[:3].upper() for d in s['days']), room_id=s['roomid'],
                 room=s.get('roomname') or ('TBA' if s['roomid'] is None else r.get('room')),
                 room_type=s.get('roomtype') or '', time_resolved=True,
                 duration_hrs=round(((_minutes(s['end']) - _minutes(s['start'])) / 60.0) * len(s['days']), 2))
        if s['roomid'] is None:
            _incomplete(r, 'Merged class meets in a room TBA.')

    for row in rows or ():
        entry = entries.get((norm_code(row.get('subject_code') or row.get('subjectcode')),
                             row.get('class_type') or 'Lecture'))
        if not entry:
            out.append(row)
            continue
        row['merge_group'] = entry['groupname']
        if entry['state'] != 'pinned':
            row.update(start_time=None, end_time=None, days_list=[], day=None, days='', time='',
                       room_id=None, room=None, room_type='')
            _incomplete(row, entry['reason'])
            out.append(row)
            continue
        fac = entry['faculty']
        if fac['status'] in ('designated', 'known'):
            if row.get('faculty_id') != fac['faculty_id']:
                row['faculty_id'] = fac['faculty_id']
                row['instructor'] = fac['faculty_id']
        elif fac['status'] in ('tba', 'conflict'):
            row['faculty_id'] = None
            row['instructor'] = 'TBA'
            _incomplete(row, f"Merged class '{entry['groupname']}' requires the same faculty; "
                             + ('none is designated or known yet.' if fac['status'] == 'tba'
                                else 'its sections have conflicting faculty.'))
            if fac['status'] == 'conflict':
                reports.append({'rule': 'HC16', 'code': HC16_FACULTY, 'merge_state': 'invalid',
                                'blocks_publish': True, 'subject': row.get('subject_code'),
                                'group': entry['groupname'], 'affected_components': ['instructor'],
                                'detail': (f"{row.get('subject_code')}: merged class '{entry['groupname']}' "
                                           f"has sections with different faculty ({', '.join(fac['known'])}).")})
        _slot(row, entry['sets'][0])
        out.append(row)
        for s in entry['sets'][1:]:
            clone = dict(row)
            _slot(clone, s)
            out.append(clone)
    return out, reports


def load_generation_plan(cfg, sem_id, section_id, *, cur=None):
    """(policy, plan) for generating `section_id` in semester `sem_id` under the group
    model; (None, None) under the legacy model."""
    if merge_model(cfg) != MODEL_GROUPS or sem_id is None:
        return None, None

    def _go(c):
        pol = policy_for(cfg, [sem_id], cur=c, default_section_id=section_id)
        members = [s for s in pol.index.member_section_ids() if s != _int(section_id)]
        live = fetch_occurrences(c, [sem_id], members, draft_preferred=False) if members else []
        return pol, generation_plan(pol, section_id, live)

    if cur is not None:
        return _go(cur)
    import database
    from psycopg2.extras import RealDictCursor
    conn = database.get_db_connection()
    own = conn.cursor(cursor_factory=RealDictCursor)
    try:
        out = _go(own)
        conn.commit()
        return out
    finally:
        own.close()
        conn.close()


def published_merges(cur, cfg, sem_id):
    """Group model: the same-faculty merged classes ACTUALLY present in the Official
    Published schedule of `sem_id`, derived only from valid HC16 events (never from
    subject/day/time inference). One entry per (Merge Group, faculty) with ≥2 sections:
    {'mergegroupid','groupname','employeenumber','sections': {sectionid: curriculumsubjectid},
     'sectionnames': {sectionid: name}, 'subjectcode'}. Read-only: it never touches the
    Merge Group configuration tables."""
    pol = policy_for(cfg, [sem_id], cur=cur)
    by = {}
    for o in fetch_occurrences(cur, [sem_id], draft_preferred=False):
        if o.get('origin') != 'Official':
            continue
        fac = o.get('faculty_id')
        if not faculty_load.has_assigned_faculty(fac):
            continue
        ev = pol._usable_event(o, fac, o.get('day'))
        if ev is None:
            continue
        g = pol.index.group(ev[0]) or {}
        e = by.setdefault((ev[0], str(fac).strip()), {
            'mergegroupid': ev[0], 'groupname': g.get('groupname'), 'employeenumber': str(fac).strip(),
            'sections': {}, 'sectionnames': {}, 'subjectcode': o.get('subjectcode')})
        e['sections'][o['sectionid']] = o['curriculumsubjectid']
        e['sectionnames'][o['sectionid']] = o.get('sectionname')
    return [e for e in by.values() if len(e['sections']) >= 2]


# ── Manual Editor (P5): server-provided merge metadata ──────────────────────────
# The editor never queries the group tables and never resolves merged events itself:
# it shows what editor_context returns and compares only the opaque `event_key`
# strings stamped here (occupancy rows via annotate_event_keys, its own slices via
# editor_context).

SYNC_IN = 'in_sync'            # exactly at one of its group's merged meetings
SYNC_SEPARATE = 'separate'     # anywhere else: an ordinary class of this section (P7)
SYNC_EMPTY = 'empty'           # a blank slice (no day/time yet)

EDITOR_MERGED = 'merged'           # at least one slice of the subject is a merged class
EDITOR_NOT_MERGED = 'not_merged'   # a member (Settings / earlier merge) not merged right now


def event_key(ev):
    """Opaque string identity of a merged event for the editor ('gid:mid'), else None."""
    return f'{ev[0]}:{ev[1]}' if ev else None


def clock(value):
    """'HH:MM' from a time, 'HH:MM[:SS]' or the editor's 'hh:mm AM' labels."""
    if isinstance(value, str):
        text = value.strip().upper()
        if text.endswith(('AM', 'PM')):
            base = hhmm(text[:-2].strip())
            if not base:
                return None
            h, m = map(int, base.split(':'))
            h = h % 12 + (12 if text.endswith('PM') else 0)
            return f'{h:02d}:{m:02d}'
    return hhmm(value)


def label12(hm):
    """'hh:mm AM' — the Manual Editor's own time-slot label format."""
    m = _minutes(hm)
    if m is None:
        return ''
    h, mm = divmod(m, 60)
    return f"{(h + 11) % 12 + 1:02d}:{mm:02d} {'PM' if h >= 12 else 'AM'}"


def annotate_event_keys(policy, rows, *, room_id=None):
    """Stamp `merge_event` on occupancy rows (room / faculty schedules): the event key
    when the row is exactly a USABLE merged event of its own group, else None. The
    editor treats two rows as the same class only when both keys are equal and the
    sections differ — an outsider never matches, so it always blocks."""
    for r in rows or ():
        key = None
        if policy is not None and policy.enabled and len(policy.index):
            occ = dict(r)
            if room_id is not None and 'roomid' not in occ:
                occ['roomid'] = room_id
            ev = policy.index.event_of(occ, day=_occ_day(occ))
            key = event_key(ev) if policy.index.event_usable(ev) else None
        r['merge_event'] = key
    return rows


def _editor_meeting(gid, m, sections_at=()):
    return {
        'event_key': event_key((gid, m.get('mergegroupmeetingid'))),
        'mergegroupmeetingid': m.get('mergegroupmeetingid'),
        'class_type': m.get('class_type', 'Lecture'), 'day': m.get('daydesc'),
        'start': hhmm(m.get('start')), 'end': hhmm(m.get('end')),
        'start_label': label12(m.get('start')), 'end_label': label12(m.get('end')),
        'starttimeid': m.get('starttimeid'), 'endtimeid': m.get('endtimeid'),
        'roomid': m.get('roomid'), 'roomname': m.get('roomname') or ('TBA' if m.get('roomid') is None else None),
        'sections': list(sections_at),
        'text': GroupMergePolicy._meeting_text(m),
    }


def editor_context(policy, section_id, rows=(), *, live_others=(), invalid_groups=()):
    """What the Manual Editor shows for ONE section's merged / mergeable subjects
    (group model, P7 "allowed" semantics — information only, nothing is locked).

    `rows`: the editor's current slices of this section — [{'key','subject_code','day',
    'start_time','end_time','room_id','faculty_id'}] (times as 'HH:MM' or 'hh:mm AM');
    `live_others`: the other member sections' occurrences (who sits at each meeting,
    SAME_FACULTY conflicts); `invalid_groups`: stored groups HC16 ignores (shown as
    advisory text only).

    Returns {'model','section_id','subjects': {CODE: {...}}, 'rows': {key: {...}}}. A
    subject entry lists the group's merged meetings with the sections at each, and
    status 'merged' (some slice of this section is at a merged meeting) or
    'not_merged'. A slice is 'in_sync' (merged class), 'separate' (ordinary class) or
    'empty'. Pure: decides nothing Save Draft / Publish would decide differently."""
    sid = _int(section_id)
    out = {'model': MODEL_GROUPS, 'section_id': sid, 'subjects': {}, 'rows': {}}
    if policy is None or sid is None:
        return out
    idx = policy.index
    norm = []
    for r in rows or ():
        fac = r.get('faculty_id')
        norm.append({
            'key': r.get('key'), 'section_id': sid,
            'subject_code': r.get('subject_code') or r.get('subjectcode'),
            'day': r.get('day') or r.get('daydesc') or None,
            'start_time': clock(r.get('start_time')), 'end_time': clock(r.get('end_time')),
            'room_id': norm_room(r.get('room_id')),
            'faculty_id': fac if faculty_load.has_assigned_faculty(fac) else None,
            'roomname': r.get('room_name') or r.get('roomname') or None,
        })
    fac_conflicts = defaultdict(list)
    if live_others:
        for v in policy.faculty_conflicts([o for o in norm if o['day'] and o['start_time']], live_others):
            fac_conflicts[(norm_code(v['subject_code']), tuple(v['normalized_days']))].append(v['detail'])

    # Which OTHER member sections currently sit at each merged meeting.
    at_event = defaultdict(list)
    for o in live_others or ():
        if idx.section_of(o) == sid:
            continue
        for d in _occ_days(o):
            ev = idx.event_of(o, day=d)
            if ev is None:
                continue
            sec = idx.section_of(o)
            label = next((m.get('label') for m in (idx.group(ev[0]) or {}).get('members') or ()
                          if _int(m.get('sectionid')) == sec and m.get('label')), None) \
                or policy._section_label(o, ev[0])
            if label not in at_event[ev]:
                at_event[ev].append(label)

    for (member_sid, code), gid in sorted(idx._members.items(), key=lambda kv: str(kv[0][1])):
        if member_sid != sid or not policy.enabled:
            continue
        g = idx.group(gid) or {}
        meetings = [_editor_meeting(gid, m, at_event.get((gid, m.get('mergegroupmeetingid')), ()))
                    for m in g.get('meetings') or ()]
        by_key = {m['event_key']: m for m in meetings}
        mode = g.get('faculty_mode')
        designated = ({'id': str(g['employeenumber']), 'name': g.get('faculty_name') or str(g['employeenumber'])}
                      if g.get('employeenumber') else None)
        merged_any = False
        for o in (x for x in norm if norm_code(x['subject_code']) == code):
            meta = {'key': o['key'], 'subject_code': o['subject_code'], 'mergegroupid': gid,
                    'group_name': g.get('groupname'), 'event_key': None, 'merged_with': [],
                    'faculty_issue': None, 'faculty_advisory': None, 'actual': None}
            if not (o['day'] and o['start_time'] and o['end_time']):
                meta['sync'] = SYNC_EMPTY
            else:
                meta['actual'] = GroupMergePolicy._actual_text(o, o['day'])
                ev = idx.event_of(o, day=o['day'])
                if ev is None:
                    meta['sync'] = SYNC_SEPARATE
                else:
                    merged_any = True
                    meta['sync'] = SYNC_IN
                    meta['event_key'] = event_key(ev)
                    meta['merged_with'] = list((by_key.get(meta['event_key']) or {}).get('sections') or ())
                    if mode == SAME_FACULTY:
                        if o['faculty_id'] and designated and str(o['faculty_id']).strip() != designated['id']:
                            meta['faculty_issue'] = (f"Merged class '{g.get('groupname')}' requires its "
                                                     f"designated faculty {designated['name']}.")
                        elif not o['faculty_id']:
                            meta['faculty_advisory'] = ('A merged class needs the same faculty for every '
                                                        'section; TBA keeps it incomplete.')
                        hits = fac_conflicts.get((code, (o['day'],)))
                        if hits and not meta['faculty_issue']:
                            meta['faculty_issue'] = hits[0]
            if o['key'] is not None:
                out['rows'][str(o['key'])] = meta
        out['subjects'][code] = {
            'subject_code': code, 'mergegroupid': gid, 'group_name': g.get('groupname'),
            'subject_name': g.get('ref_subjectname'), 'faculty_mode': mode,
            'designated_faculty': designated,
            'sections': [m.get('label') for m in g.get('members') or ()],
            'section_count': len(g.get('members') or ()),
            'origin': g.get('origin'),
            'status': EDITOR_MERGED if merged_any else EDITOR_NOT_MERGED,
            'meetings': meetings,
        }
    for g in invalid_groups or ():
        for m in g.get('members') or ():
            code = norm_code(m.get('subjectcode'))
            if _int(m.get('sectionid')) != sid or code in out['subjects']:
                continue
            out['subjects'][code] = {
                'subject_code': code, 'mergegroupid': g.get('mergegroupid'), 'group_name': g.get('groupname'),
                'status': 'invalid_config', 'advisory_only': True,
                'reason': '; '.join(g.get('config_errors') or []) or (
                    'This Merge Group is inactive.' if not g.get('is_active') else 'Invalid configuration.'),
                'sections': [x.get('label') for x in g.get('members') or ()],
                'section_count': len(g.get('members') or ()), 'meetings': [],
            }
    return out


# ── Local Scheduler / Faculty requests (P6) ──────────────────────────────────────
# A merged class (a member occurrence at its group's meeting) is shared: its day, time,
# room (and, through Local, faculty) are the same for every member section, so a
# one-section change — a Local adjustment or a Faculty Schedule Adjustment request —
# would silently split the merged class. Such changes are rejected, never applied.
# Make-up classes are separate, date-specific occurrences and are not restricted here.

MERGED_OCCURRENCE_LOCKED = 'MERGED_OCCURRENCE_LOCKED'
_CHANGE_ORDER = ('day', 'time', 'room', 'faculty')


def occurrence_changes(occ, proposed):
    """Which of day/time/room/faculty `proposed` changes on Official occurrence `occ`.
    A key missing from `proposed` (or None) keeps the Official value."""
    out = []
    p = proposed or {}
    day = p.get('daydesc') or p.get('day')
    if day and str(day).strip() != str(_occ_day(occ) or '').strip():
        out.append('day')
    for key in ('starttimeid', 'endtimeid'):
        if p.get(key) is not None and _int(p.get(key)) != _int(occ.get(key)):
            out.append('time')
            break
    if 'roomid' in p and p['roomid'] is not None and norm_room(p['roomid']) != _occ_room(occ):
        out.append('room')
    fac = p.get('faculty_id') or p.get('employeenumber') or p.get('faculty_employeenumber')
    if fac and faculty_load.has_assigned_faculty(fac) and str(fac).strip() != str(_occ_faculty(occ) or '').strip():
        out.append('faculty')
    return out


def occurrence_change_restriction(policy, occ, proposed=None, *, action='Local adjustment', targeted=False):
    """HC16 group model: why `action` may not change Official occurrence `occ`
    (sectionid, subjectcode, daydesc, starttimeid, endtimeid, roomid, employeenumber),
    else None. `targeted=True` (a Schedule Adjustment request) restricts any member
    occurrence — the request exists to change it; `proposed` then only names what it
    asks to change. Otherwise only an actual change of day/time/room/faculty is
    restricted (an unchanged Local copy changes nothing). Applies to a member
    occurrence that is AT one of its active, valid group's merged meetings, in any
    faculty mode (MULTIPLE_FACULTY substitutions included)."""
    if policy is None or not policy.enabled or not len(policy.index):
        return None
    gid = policy.index.member_of(occ)
    if gid is None:
        return None
    # P7: only an occurrence that IS a merged class is shared with other sections; a
    # member scheduled anywhere else is an ordinary class and may be adjusted freely.
    if policy.index.event_of(occ, day=_occ_day(occ)) is None:
        return None
    changes = occurrence_changes(occ, proposed) if proposed is not None else []
    if targeted and not changes:
        changes = ['day', 'time', 'room']
    if not changes:
        return None
    g = policy.index.group(gid) or {}
    code = occ.get('subjectcode') or occ.get('subject_code') or 'This class'
    section = policy._section_label(occ, gid)
    mode = ('one faculty for every section' if g.get('faculty_mode') == SAME_FACULTY
            else 'each section with its own faculty')
    what = ', '.join(c for c in _CHANGE_ORDER if c in changes)
    meetings = '; '.join(GroupMergePolicy._meeting_text(m) for m in g.get('meetings') or ()) or \
        'no meeting scheduled yet'
    msg = (f"{code} for {section} is part of Merge Group '{g.get('groupname')}' ({mode}; {meetings}). "
           f"Its day, time, room and faculty are set by the Merge Group for every member section, "
           f"so a {action} cannot change its {what}. Ask the scheduling office to change the "
           f"Merge Group instead; a make-up class for a single date is still possible.")
    return {'code': MERGED_OCCURRENCE_LOCKED, 'mergegroupid': gid, 'group': g.get('groupname'),
            'faculty_mode': g.get('faculty_mode'), 'subject': code, 'section': section,
            'changes': [c for c in _CHANGE_ORDER if c in changes], 'action': action, 'error': msg}


def load_editor_context(cur, cfg, sem_id, section_id, rows=()):
    """editor_context for one section, loading the group policy and the other member
    sections' live occurrences with `cur`. {'model': 'legacy'} under the legacy model."""
    if merge_model(cfg) != MODEL_GROUPS:
        return {'model': MODEL_LEGACY}
    pol = policy_for(cfg, [sem_id], cur=cur, default_section_id=section_id)
    others = [s for s in pol.index.member_section_ids() if s != _int(section_id)]
    # Draft-preferred: a section that merged in its Draft already shows as merged.
    live = fetch_occurrences(cur, [sem_id], others, draft_preferred=True) if others else []
    sid = _int(section_id)
    invalid = [g for g in load_groups(cur, cfg, semester_ids=[sem_id])
               if (g.get('config_errors') or not g.get('is_active'))
               and any(_int(m.get('sectionid')) == sid for m in g.get('members') or ())] if sid else []
    return editor_context(pol, section_id, rows, live_others=live, invalid_groups=invalid)


def with_policy(cfg, policy):
    """A copy of `cfg` carrying `policy` for CSPValidator (None -> `cfg` unchanged)."""
    if policy is None:
        return cfg
    out = dict(cfg or {})
    out[POLICY_CFG_KEY] = policy
    return out


def policy_for(cfg, semester_ids, *, cur=None, default_section_id=None):
    """The group-mode HC16 policy for these semesters, or None in legacy mode (the
    caller then runs its unchanged legacy merge code). Uses `cur` when given,
    otherwise a short-lived connection of its own."""
    if merge_model(cfg) != MODEL_GROUPS:
        return None
    enabled = bool(_num((cfg or {}).get('hc_merge_enabled', 1)))
    sem_ids = [s for s in (semester_ids or ()) if s is not None]
    if not enabled or not sem_ids:
        return GroupMergePolicy(MergeIndex(), default_section_id=default_section_id, enabled=enabled)

    def _load(c):
        groups = load_groups(c, cfg, semester_ids=sem_ids)
        return groups, load_hc17_rules(c, [g['mergegroupid'] for g in groups])

    if cur is not None:
        groups, rules = _load(cur)
    else:
        import database
        from psycopg2.extras import RealDictCursor
        conn = database.get_db_connection()
        own = conn.cursor(cursor_factory=RealDictCursor)
        try:
            groups, rules = _load(own)
            conn.commit()
        finally:
            own.close()
            conn.close()
    return GroupMergePolicy(MergeIndex(groups), default_section_id=default_section_id, load_rules=rules)


HC17_SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'migrations', '2026-10-08_hc17_policy_groups.sql')


def hc17_schema_sql():
    with open(HC17_SCHEMA_FILE, encoding='utf-8') as f:
        return f.read()


def ensure_hc17_schema(cur):
    cur.execute("SELECT to_regclass('public.merge_load_policy_group') IS NOT NULL AS ok")
    row = cur.fetchone()
    if not (row and (row['ok'] if isinstance(row, dict) else row[0])):
        ensure_schema(cur)
        cur.execute(hc17_schema_sql())


def load_hc17_rules(cur, group_ids):
    """{mergegroupid: [policy rule]} from the explicit merge_load_policy_group
    mappings only. Empty when the mapping table does not exist yet."""
    if not group_ids:
        return {}
    cur.execute("SELECT to_regclass('public.merge_load_policy_group') IS NOT NULL AS ok")
    row = cur.fetchone()
    if not (row and (row['ok'] if isinstance(row, dict) else row[0])):
        return {}
    cur.execute("""
        SELECT m.mergegroupid, p.policyid, p.policyname, p.subjectcode, p.min_sections, p.max_sections
        FROM public.merge_load_policy_group m
        JOIN public.merge_load_policy p ON p.policyid = m.policyid
        WHERE m.mergegroupid = ANY(%s)
        ORDER BY m.mergegroupid, p.min_sections
    """, (list(group_ids),))
    out = defaultdict(list)
    for r in cur.fetchall() or []:
        out[r['mergegroupid']].append(dict(r))
    return dict(out)


def hc17_policy_groups(cur, policy_ids):
    """{policyid: [mapped Merge Group summary]} for the HC17 settings, each group with
    its curriculum-derived load (read-only)."""
    ensure_hc17_schema(cur)
    if not policy_ids:
        return {}
    cur.execute("""
        SELECT m.policyid, g.mergegroupid, g.groupname, g.is_active, g.semesterid,
               cs.subjectcode AS ref_subjectcode, cs.creditunits AS ref_creditunits,
               COALESCE(NULLIF(cs.tuitionhours, 0), cs.lecturehours + cs.laboratoryhours, 0) AS ref_teachinghours,
               s.semestertype, ay.yearstart, ay.yearend, s.academicyearid
        FROM public.merge_load_policy_group m
        JOIN public.merge_group g ON g.mergegroupid = m.mergegroupid
        JOIN public.curriculumsubject cs ON cs.curriculumsubjectid = g.ref_curriculumsubjectid
        JOIN public.semester s ON s.semesterid = g.semesterid
        JOIN public.academicyear ay ON ay.academicyearid = s.academicyearid
        WHERE m.policyid = ANY(%s)
        ORDER BY LOWER(g.groupname)
    """, (list(policy_ids),))
    out = {}
    for r in cur.fetchall() or []:
        d = dict(r)
        d['semester_label'] = semester_label(d.pop('semestertype'), d.pop('yearstart'), d.pop('yearend'),
                                             d.pop('academicyearid'))
        out.setdefault(d.pop('policyid'), []).append(d)
    return out


def hc17_mapping_conflicts(cur, policy, group_ids=None):
    """Overlapping section-count ranges that mapping `policy` (policyid/min/max) to
    `group_ids` would create; group_ids None = the groups it is already mapped to."""
    ensure_hc17_schema(cur)
    if group_ids is None:
        cur.execute("SELECT mergegroupid FROM public.merge_load_policy_group WHERE policyid = %s",
                    (policy['policyid'],))
        group_ids = [r['mergegroupid'] for r in cur.fetchall() or []]
    if not group_ids:
        return []
    cur.execute("""
        SELECT m.mergegroupid, g.groupname, p.policyid, p.policyname, p.subjectcode,
               p.min_sections, p.max_sections
        FROM public.merge_load_policy_group m
        JOIN public.merge_load_policy p ON p.policyid = m.policyid
        JOIN public.merge_group g ON g.mergegroupid = m.mergegroupid
        WHERE m.mergegroupid = ANY(%s) AND m.policyid <> %s
    """, (list(group_ids), policy['policyid']))
    existing = {}
    for r in cur.fetchall() or []:
        existing.setdefault(r['mergegroupid'], []).append(dict(r))
    return policy_mapping_conflicts(policy, group_ids, existing)


def set_policy_groups(cur, policyid, group_ids, *, username):
    """Replace the explicit Merge Groups an HC17 policy applies to. Returns
    {'ok'} | {'not_found'} | {'unknown_groups': [...]} | {'conflicts': [...]}. The
    database trigger re-checks ambiguity (SQLSTATE 23P01) as a final guard."""
    ensure_hc17_schema(cur)
    cur.execute("SELECT policyid, policyname, subjectcode, min_sections, max_sections "
                "FROM public.merge_load_policy WHERE policyid = %s FOR UPDATE", (policyid,))
    pol = cur.fetchone()
    if not pol:
        return {'not_found': True}
    group_ids = sorted({_int(g) for g in group_ids or () if _int(g) is not None})
    if group_ids:
        cur.execute("SELECT mergegroupid FROM public.merge_group WHERE mergegroupid = ANY(%s)", (group_ids,))
        found = {r['mergegroupid'] for r in cur.fetchall() or []}
        missing = [g for g in group_ids if g not in found]
        if missing:
            return {'unknown_groups': missing}
    for gid in group_ids:
        cur.execute('SELECT pg_advisory_xact_lock(%s, %s)', (4217, gid))
    conflicts = hc17_mapping_conflicts(cur, dict(pol), group_ids)
    if conflicts:
        return {'conflicts': conflicts, 'policy': dict(pol)}
    cur.execute("DELETE FROM public.merge_load_policy_group WHERE policyid = %s AND NOT (mergegroupid = ANY(%s))",
                (policyid, group_ids))
    for gid in group_ids:
        cur.execute("""
            INSERT INTO public.merge_load_policy_group (policyid, mergegroupid, created_by)
            VALUES (%s, %s, %s) ON CONFLICT (policyid, mergegroupid) DO NOTHING
        """, (policyid, gid, username))
    return {'ok': True, 'policy': dict(pol), 'mergegroupids': group_ids}


def ranges_overlap(a_min, a_max, b_min, b_max):
    return _int(a_min) <= _int(b_max) and _int(b_min) <= _int(a_max)


def policy_mapping_conflicts(policy, group_ids, existing):
    """Ambiguity check for mapping `policy` (min/max) to `group_ids`, given
    `existing` = {mergegroupid: [rules already mapped to it, other policies]}.
    Returns human-readable conflicts; [] when unambiguous."""
    out = []
    for gid in group_ids:
        for r in existing.get(gid, ()):
            if _int(r.get('policyid')) == _int(policy.get('policyid')):
                continue
            if ranges_overlap(policy['min_sections'], policy['max_sections'], r['min_sections'], r['max_sections']):
                out.append({'mergegroupid': gid, 'groupname': r.get('groupname'), 'policyid': r.get('policyid'),
                            'policyname': r.get('policyname') or r.get('subjectcode'),
                            'range': f"{r['min_sections']}–{r['max_sections']}"})
    return out


def policy_from_config(cfg):
    """The policy a caller injected into a validator config, or — in groups mode with
    none injected — an EMPTY policy that exempts nothing (fail closed: no overlap is
    ever waived without a resolvable merged event). None in legacy mode."""
    if merge_model(cfg) != MODEL_GROUPS:
        return None
    pol = (cfg or {}).get(POLICY_CFG_KEY)
    return pol if isinstance(pol, GroupMergePolicy) else GroupMergePolicy(MergeIndex())


# ── Discovery (legacy merges -> candidate groups) ────────────────────────────────

def _legacy_gene(o):
    return {'subject_code': o.get('subjectcode'), 'faculty_id': o.get('faculty_id'),
            'course': o.get('programcode'), 'section_name': o.get('sectionname')}


def discover_candidates(occurrences, legacy_cfg):
    """Candidate Merge Groups from merges the LEGACY rule currently accepts.

    Occurrences of the same semester+subject at an identical day/start/end in 2+
    different sections whose every pair is legacy-valid (faculty_load.is_valid_merge
    under the stored legacy configuration) form one meeting; meetings shared by the
    same section set form one candidate. Nothing is written."""
    by_slot = defaultdict(list)
    for o in occurrences:
        if o.get('sectionid') is None or not o.get('subjectcode'):
            continue
        key = (o.get('semesterid'), norm_code(o['subjectcode']), o.get('day'),
               hhmm(o.get('start_time')), hhmm(o.get('end_time')))
        by_slot[key].append(o)

    clusters = {}
    notes = []
    for (sem, code, day, start, end), occs in sorted(by_slot.items(), key=lambda kv: str(kv[0])):
        per_section = {}
        for o in occs:
            per_section.setdefault(o['sectionid'], []).append(o)
        if len(per_section) < 2:
            continue
        reps = [v[0] for v in per_section.values()]
        clique = all(faculty_load.is_valid_merge(_legacy_gene(a), _legacy_gene(b), legacy_cfg)
                     for i, a in enumerate(reps) for b in reps[i + 1:])
        if not clique:
            notes.append(f'{code} {day} {start}-{end}: {len(per_section)} sections meet together '
                         f'but are not all a legacy-valid merge; not proposed.')
            continue
        ckey = (sem, code, frozenset(per_section))
        c = clusters.setdefault(ckey, {'semesterid': sem, 'subjectcode': code, 'sections': {},
                                       'meetings': [], 'faculty': set(), 'origins': set(),
                                       'subjects': {}})
        rooms = {}
        for sid, lst in per_section.items():
            first = lst[0]
            c['sections'][sid] = first
            c['subjects'][sid] = first.get('curriculumsubjectid')
            for o in lst:
                rooms[norm_room(o.get('roomid'))] = o.get('roomname') or 'TBA'
                c['origins'].add(o.get('origin') or 'Official')
                if faculty_load.has_assigned_faculty(o.get('faculty_id')):
                    c['faculty'].add(str(o['faculty_id']).strip())
        c['meetings'].append({'daydesc': day, 'start': start, 'end': end,
                              'starttimeid': occs[0].get('starttimeid'),
                              'endtimeid': occs[0].get('endtimeid'),
                              'rooms': rooms})

    candidates = []
    seen_members = defaultdict(list)
    for (sem, code, secs), c in clusters.items():
        reps = sorted(c['sections'].values(),
                      key=lambda o: section_label(o.get('programcode'), o.get('yearlevel'), o.get('sectionname')))
        ref_occ = reps[0]
        parts = required_parts(ref_occ)
        issues = []
        meetings = []
        for mt in c['meetings']:
            room_ids = [r for r in mt['rooms']]
            if len(room_ids) == 1:
                room_id = room_ids[0]
            else:
                room_id = None
                issues.append(f"{mt['daydesc']} {mt['start']}-{mt['end']}: members use different rooms "
                              f"({', '.join(sorted(str(v) for v in mt['rooms'].values()))}). A merged "
                              f"event now requires one room — choose it before confirming.")
            ctype = 'Lab' if list(parts) == ['Lab'] else 'Lecture'
            if len(parts) == 2:
                issues.append(f"{mt['daydesc']} {mt['start']}-{mt['end']}: subject has Lecture and Lab "
                              f"hours — confirm the class type of this meeting.")
            meetings.append({'class_type': ctype, 'daydesc': mt['daydesc'],
                             'starttimeid': mt['starttimeid'], 'endtimeid': mt['endtimeid'],
                             'start': mt['start'], 'end': mt['end'], 'roomid': room_id,
                             'roomname': mt['rooms'].get(room_id) if room_id is not None else None})
        known = sorted(c['faculty'])
        mode = SAME_FACULTY if len(known) <= 1 else MULTIPLE_FACULTY
        labels = [section_label(o.get('programcode'), o.get('yearlevel'), o.get('sectionname')) for o in reps]
        name = f"{code} — {' / '.join(labels)}"
        cand = {
            'key': f"{sem}:{code}:{'-'.join(str(s) for s in sorted(secs))}",
            'groupname': name[:100],
            'semesterid': sem,
            'subjectcode': code,
            'subjectname': ref_occ.get('subjectname'),
            'ref_curriculumsubjectid': ref_occ.get('curriculumsubjectid'),
            'faculty_mode': mode,
            'employeenumber': known[0] if mode == SAME_FACULTY and known else None,
            'known_faculty': known,
            'members': [{'sectionid': o['sectionid'], 'label': lbl,
                         'curriculumsubjectid': c['subjects'][o['sectionid']]}
                        for o, lbl in zip(reps, labels)],
            'meetings': sorted(meetings, key=lambda m: (DAYS.index(m['daydesc']) if m['daydesc'] in DAYS else 9,
                                                        m['start'] or '')),
            'origins': sorted(c['origins']),
            'issues': issues,
            'conflicts_with': [],
        }
        for sid in secs:
            seen_members[(sem, code, sid)].append(cand['key'])
        candidates.append(cand)

    by_key = {c['key']: c for c in candidates}
    for keys in seen_members.values():
        if len(keys) > 1:
            for k in keys:
                others = [o for o in keys if o != k]
                by_key[k]['conflicts_with'] = sorted(set(by_key[k]['conflicts_with']) | set(others))
    for c in candidates:
        if c['conflicts_with']:
            c['issues'].append('A section here also appears in another candidate for this subject; '
                               'only one of them can be an active group.')
    candidates.sort(key=lambda c: (c['semesterid'] or 0, c['subjectcode'], c['groupname']))
    return {'candidates': candidates, 'notes': notes}


# ── Dry run (legacy vs groups, nothing enforced) ─────────────────────────────────

def _overlaps(a, b):
    sa, ea = _minutes(a.get('start_time')), _minutes(a.get('end_time'))
    sb, eb = _minutes(b.get('start_time')), _minutes(b.get('end_time'))
    if None in (sa, ea, sb, eb):
        return False
    return sa < eb and ea > sb


def _occ_brief(o):
    return {'section': section_label(o.get('programcode'), o.get('yearlevel'), o.get('sectionname')),
            'sectionid': o.get('sectionid'), 'subjectcode': o.get('subjectcode'),
            'day': o.get('day'), 'start': hhmm(o.get('start_time')), 'end': hhmm(o.get('end_time')),
            'room': o.get('roomname') or ('TBA' if norm_room(o.get('roomid')) is None else o.get('roomid')),
            'faculty': o.get('faculty_id'), 'origin': o.get('origin')}


def _group_reason(index, a, b):
    ga, gb = index.member_of(a), index.member_of(b)
    if ga is None and gb is None:
        return 'neither section is in a merge group for this subject'
    if ga is None or gb is None:
        return 'only one of the two sections is in a merge group'
    if ga != gb:
        return 'the sections are in different merge groups'
    g = index.group(ga) or {}
    if g.get('state') != STATE_SCHEDULED:
        return f"merge group '{g.get('groupname')}' is {str(g.get('state') or '').lower()}"
    if index.event_of(a) is None or index.event_of(b) is None:
        return f"not at merge group '{g.get('groupname')}' meeting (day/time/room differs)"
    if index.event_of(a) != index.event_of(b):
        return 'different meetings of the same group'
    return f"faculty does not satisfy merge group '{g.get('groupname')}' faculty mode"


def dry_run(occurrences, legacy_cfg, index):
    """What HC10/HC11/HC16 would report under the group model compared with the
    legacy rule, for the given occurrences. Read-only; nothing is enforced.

    Only pairs of DIFFERENT sections are compared (HC12 section conflicts are
    identical under both models — a merge never exempts them)."""
    new_conflicts, newly_allowed = [], []
    by_bucket = defaultdict(list)
    for o in occurrences:
        if o.get('day'):
            by_bucket[(o.get('semesterid'), o['day'])].append(o)
    for (_sem, _day), occs in by_bucket.items():
        occs.sort(key=lambda o: _minutes(o.get('start_time')) or 0)
        for i, a in enumerate(occs):
            ea = _minutes(a.get('end_time')) or 0
            for b in occs[i + 1:]:
                if (_minutes(b.get('start_time')) or 0) >= ea:
                    break
                if a.get('sectionid') == b.get('sectionid') or not _overlaps(a, b):
                    continue
                same_room = _occ_room(a) is not None and _occ_room(a) == _occ_room(b)
                same_fac = (faculty_load.has_assigned_faculty(a.get('faculty_id'))
                            and str(a.get('faculty_id')).strip() == str(b.get('faculty_id')).strip())
                if not (same_room or same_fac):
                    continue
                ga, gb = _legacy_gene(a), _legacy_gene(b)
                legacy_merge = faculty_load.is_valid_merge(ga, gb, legacy_cfg)
                legacy_fac = faculty_load.faculty_overlap_exempt(ga, gb, config=legacy_cfg,
                                                                 valid_merge=legacy_merge)
                group_ok = index.same_event(a, b)
                for rule, applies, legacy_ok in (('HC11', same_room, legacy_merge),
                                                 ('HC10', same_fac, legacy_fac)):
                    if not applies or legacy_ok == group_ok:
                        continue
                    rec = {'rule': rule, 'a': _occ_brief(a), 'b': _occ_brief(b)}
                    if legacy_ok:
                        rec['reason'] = _group_reason(index, a, b)
                        new_conflicts.append(rec)
                    else:
                        newly_allowed.append(rec)

    member_issues = []
    events = defaultdict(list)
    for o in occurrences:
        # P7: a member off its group's meetings is an ordinary class — not an issue.
        if index.classify(o) != 'event':
            continue
        events[index.event_of(o)].append(o)
    faculty_issues = []
    for ev, occs in events.items():
        g = index.group(ev[0]) or {}
        if g.get('faculty_mode') != SAME_FACULTY:
            continue
        known = sorted({str(o.get('faculty_id')).strip() for o in occs
                        if faculty_load.has_assigned_faculty(o.get('faculty_id'))})
        designated = g.get('employeenumber')
        if len(known) > 1 or (designated and known and any(k != str(designated) for k in known)):
            faculty_issues.append({'group': g.get('groupname'), 'mergegroupid': g.get('mergegroupid'),
                                   'faculty': known, 'designated': designated,
                                   'occurrences': [_occ_brief(o) for o in occs]})
    return {
        'summary': {
            'occurrences': len(occurrences),
            'new_conflicts': len(new_conflicts),
            'newly_allowed': len(newly_allowed),
            'diverged': sum(1 for m in member_issues if m['kind'] == 'diverged'),
            'unscheduled': sum(1 for m in member_issues if m['kind'] == 'unscheduled'),
            'faculty_mode_violations': len(faculty_issues),
        },
        'new_conflicts': new_conflicts,
        'newly_allowed': newly_allowed,
        'member_issues': member_issues,
        'faculty_issues': faculty_issues,
    }


def compare_impact(occurrences, before, after):
    """Effect of replacing MergeIndex `before` with `after` on existing occurrences:
      newly_diverging — kept for callers; always empty since P7 (being off a group's
                        meeting is an ordinary class, never an error);
      losing_merge    — currently a merged-event occurrence, would no longer be one."""
    newly_diverging, losing_merge = [], []
    for o in occurrences:
        b, a = before.classify(o), after.classify(o)
        if b == 'event' and a != 'event':
            g = before.group(before.member_of(o)) or {}
            losing_merge.append(dict(_occ_brief(o), group=g.get('groupname')))
    return {'newly_diverging': newly_diverging, 'losing_merge': losing_merge}


# ═════════════════════════════════════════════════════════════════════════════════
#  Database layer — every helper takes the caller's cursor (RealDictCursor).
# ═════════════════════════════════════════════════════════════════════════════════

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'migrations', '2026-10-08_merge_groups.sql')
_TABLES = ('merge_group', 'merge_group_member', 'merge_group_meeting')


def schema_sql():
    with open(SCHEMA_FILE, encoding='utf-8') as f:
        return f.read()


def ensure_schema(cur):
    """Create the Merge Group tables when missing (normally done at startup)."""
    cur.execute("SELECT to_regclass('public.merge_group_meeting') IS NOT NULL AS ok")
    row = cur.fetchone()
    if not (row and (row['ok'] if isinstance(row, dict) else row[0])):
        cur.execute(schema_sql())


def get_merge_model(cur):
    cur.execute("SELECT config_value FROM public.scheduler_config WHERE config_key = %s", (MODEL_KEY,))
    row = cur.fetchone()
    return merge_model({MODEL_KEY: row['config_value'] if row else None})


def set_merge_model(cur, model):
    """INTERNAL cutover switch (never reachable from the Settings form). P1 only
    provides the primitive; the guarded cutover action arrives with group-based
    enforcement, so flipping it now would have no effect on scheduling."""
    if model not in (MODEL_LEGACY, MODEL_GROUPS):
        raise ValueError(f'unknown merge model: {model!r}')
    cur.execute("""
        INSERT INTO public.scheduler_config (config_key, config_value) VALUES (%s, %s)
        ON CONFLICT (config_key) DO UPDATE SET config_value = EXCLUDED.config_value
    """, (MODEL_KEY, model))


def load_semesters(cur):
    cur.execute("""
        SELECT s.semesterid, s.academicyearid, s.semestertype, s.semstartdate, s.semenddate,
               s.isactive, ay.yearstart, ay.yearend, ay.isactive AS ay_active
        FROM public.semester s
        JOIN public.academicyear ay ON ay.academicyearid = s.academicyearid
        ORDER BY ay.yearstart DESC, s.semestertype
    """)
    out = []
    for r in cur.fetchall() or []:
        d = dict(r)
        d['label'] = semester_label(d['semestertype'], d.get('yearstart'), d.get('yearend'), d['academicyearid'])
        d['ended'] = bool(d.get('semenddate') and d['semenddate'] < date.today())
        out.append(d)
    return out


def default_semester_ids(semesters):
    """The active academic year's semesters (falls back to the active semester)."""
    ids = [s['semesterid'] for s in semesters if s.get('ay_active')]
    return ids or [s['semesterid'] for s in semesters if s.get('isactive')]


def load_offered(cur, semesterid):
    """Sections of the semester's academic year with every curriculum subject they
    take in that semester (regular curriculum + its bridging counterpart)."""
    cur.execute("""
        WITH sem AS (
            SELECT semesterid, academicyearid, semestertype FROM public.semester WHERE semesterid = %s
        ), secs AS (
            SELECT sec.sectionid, sec.sectionname, sec.isactive, pyl.programcode, pyl.yearlevel,
                   pyl.curriculumid, c.curriculumyear
            FROM public.sections sec
            JOIN public.program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            JOIN sem ON pyl.academicyearid = sem.academicyearid
            LEFT JOIN public.curriculum c ON c.curriculumid = pyl.curriculumid
        ), curr AS (
            SELECT s.sectionid, s.curriculumid FROM secs s WHERE s.curriculumid IS NOT NULL
            UNION
            SELECT s.sectionid, cb.curriculumid
            FROM secs s
            JOIN public.curriculum cb ON UPPER(cb.programcode) = UPPER(s.programcode)
             AND cb.curriculumyear = s.curriculumyear AND cb.curriculumtype = 'WITH_BRIDGING'
        )
        SELECT s.sectionid, s.sectionname, s.isactive, s.programcode, s.yearlevel,
               cs.curriculumsubjectid, cs.subjectcode, cs.subjectname, cs.lecturehours,
               cs.laboratoryhours, cs.creditunits, cs.tuitionhours, cs.curriculumid,
               cur.curriculumyear, cur.curriculumtype
        FROM secs s
        LEFT JOIN curr ON curr.sectionid = s.sectionid
        LEFT JOIN public.curriculumsubject cs
               ON cs.curriculumid = curr.curriculumid AND cs.yearlevel = s.yearlevel
              AND cs.semester = (SELECT semestertype FROM sem)
        LEFT JOIN public.curriculum cur ON cur.curriculumid = cs.curriculumid
        ORDER BY s.programcode, s.yearlevel, s.sectionname, cs.subjectcode
    """, (semesterid,))
    sections, subjects = {}, {}
    for r in cur.fetchall() or []:
        sid = r['sectionid']
        sec = sections.setdefault(sid, {
            'sectionid': sid, 'sectionname': r['sectionname'], 'programcode': r['programcode'],
            'yearlevel': r['yearlevel'], 'isactive': bool(r['isactive']),
            'label': section_label(r['programcode'], r['yearlevel'], r['sectionname']),
            'offered': set()})
        if r['curriculumsubjectid'] is not None:
            sec['offered'].add(r['curriculumsubjectid'])
            subjects.setdefault(r['curriculumsubjectid'], {
                k: r[k] for k in ('curriculumsubjectid', 'subjectcode', 'subjectname', 'lecturehours',
                                  'laboratoryhours', 'creditunits', 'tuitionhours', 'curriculumid',
                                  'curriculumyear', 'curriculumtype')})
    return sections, subjects


def load_timeslots(cur):
    cur.execute("SELECT timeid, timevalue FROM public.timeslot ORDER BY timevalue")
    return {r['timeid']: hhmm(r['timevalue']) for r in cur.fetchall() or []}


def load_rooms(cur):
    cur.execute("""
        SELECT r.roomid, r.roomname, r.roomtype, r.roomcapacity,
               COALESCE(r.isactive, TRUE) AND COALESCE(b.isactive, TRUE) AS isactive,
               b.buildingname
        FROM public.room r LEFT JOIN public.building b ON b.buildingid = r.buildingid
        ORDER BY r.roomname
    """)
    return {r['roomid']: dict(r) for r in cur.fetchall() or []}


def load_faculty(cur):
    cur.execute("""
        SELECT employeenumber, COALESCE(isactive, TRUE) AS isactive,
               TRIM(lastname || ', ' || firstname) AS fullname
        FROM public.faculty ORDER BY lastname, firstname
    """)
    return {r['employeenumber']: dict(r) for r in cur.fetchall() or []}


def valid_time_blocks(cfg):
    """HC6 blocks as {('HH:MM','HH:MM')}: the built-in STANDARD_BLOCKS plus the
    configured hc_time_slots — the same set CSPValidator validates against. None
    when HC6 is switched off."""
    if not bool(_num((cfg or {}).get('hc_time_blocks_enabled', 1))):
        return None
    from scheduler import STANDARD_BLOCKS, _parse_time_slot_pairs
    raw = (cfg or {}).get('hc_time_slots', '')
    pairs = set(STANDARD_BLOCKS) | (_parse_time_slot_pairs(raw) if raw else set())
    return {(hhmm(s), hhmm(e)) for s, e in pairs}


def _active_memberships(cur, semesterid, exclude_group_id=None):
    cur.execute("""
        SELECT mgm.sectionid, mgm.curriculumsubjectid, cs.subjectcode, mg.groupname
        FROM public.merge_group_member mgm
        JOIN public.merge_group mg ON mg.mergegroupid = mgm.mergegroupid
        JOIN public.curriculumsubject cs ON cs.curriculumsubjectid = mgm.curriculumsubjectid
        WHERE mgm.is_active AND mg.is_active AND mg.semesterid = %s
          AND mg.mergegroupid IS DISTINCT FROM %s
    """, (semesterid, exclude_group_id))
    taken = {}
    for r in cur.fetchall() or []:
        taken[(r['sectionid'], r['curriculumsubjectid'])] = r['groupname']
        taken[(r['sectionid'], norm_code(r['subjectcode']))] = r['groupname']
    return taken


def load_validation_context(cur, semesterid, exclude_group_id=None, *, cache=None):
    """Everything validate_group needs for one semester. `cache` (a dict) lets a
    caller validating many groups of the same semester load the shared parts once."""
    cache = cache if cache is not None else {}
    if 'semesters' not in cache:
        cache['semesters'] = {s['semesterid']: s for s in load_semesters(cur)}
        cache['timeslots'] = load_timeslots(cur)
        cache['rooms'] = load_rooms(cur)
        cache['faculty'] = load_faculty(cur)
    if ('offered', semesterid) not in cache:
        cache[('offered', semesterid)] = load_offered(cur, semesterid) if semesterid else ({}, {})
    sections, subjects = cache[('offered', semesterid)]
    cur.execute("SELECT LOWER(groupname) AS n FROM public.merge_group "
                "WHERE semesterid = %s AND mergegroupid IS DISTINCT FROM %s", (semesterid, exclude_group_id))
    other_names = {r['n'] for r in cur.fetchall() or []}
    return {
        'semester': cache['semesters'].get(semesterid),
        'sections': sections, 'subjects': subjects,
        'timeslots': cache['timeslots'], 'rooms': cache['rooms'], 'faculty': cache['faculty'],
        'active_memberships': _active_memberships(cur, semesterid, exclude_group_id) if semesterid else {},
        'other_names': other_names,
    }


def _completeness_for(group, ctx, cfg, blocks):
    ref = (ctx.get('subjects') or {}).get(group.get('ref_curriculumsubjectid')) or group.get('ref_subject') or {}
    return scheduling_completeness(
        ref, group.get('meetings') or [], valid_blocks=blocks, rooms=ctx.get('rooms'),
        lab_rooms_required=bool(_num((cfg or {}).get('hc_lab_session_enabled', 1))))


def load_groups(cur, cfg=None, *, semester_ids=None, group_ids=None):
    """Stored groups with members and meetings, each RE-VALIDATED against current
    curriculum data (config_errors) and annotated with completeness and `state`.
    A group whose configuration became invalid (e.g. a curriculum edit) is INVALID
    and is ignored by MergeIndex."""
    ensure_schema(cur)
    where, params = [], []
    if semester_ids is not None:
        where.append('mg.semesterid = ANY(%s)')
        params.append(list(semester_ids))
    if group_ids is not None:
        where.append('mg.mergegroupid = ANY(%s)')
        params.append(list(group_ids))
    cur.execute(f"""
        SELECT mg.*, cs.subjectcode AS ref_subjectcode, cs.subjectname AS ref_subjectname,
               cs.creditunits AS ref_creditunits,
               -- Curriculum teaching hours (tuitionhours, falling back to lecture+lab when
               -- stored as 0) — the same rule HC17's _subject_load_info has always used.
               COALESCE(NULLIF(cs.tuitionhours, 0), cs.lecturehours + cs.laboratoryhours, 0)
                   AS ref_teachinghours,
               TRIM(f.lastname || ', ' || f.firstname) AS faculty_name
        FROM public.merge_group mg
        JOIN public.curriculumsubject cs ON cs.curriculumsubjectid = mg.ref_curriculumsubjectid
        LEFT JOIN public.faculty f ON f.employeenumber = mg.employeenumber
        {('WHERE ' + ' AND '.join(where)) if where else ''}
        ORDER BY mg.semesterid DESC, LOWER(mg.groupname)
    """, params)
    groups = [dict(r) for r in cur.fetchall() or []]
    if not groups:
        return []
    ids = [g['mergegroupid'] for g in groups]
    cur.execute("""
        SELECT mgm.*, sec.sectionname, pyl.programcode, pyl.yearlevel,
               cs.subjectcode, cs.subjectname
        FROM public.merge_group_member mgm
        JOIN public.sections sec ON sec.sectionid = mgm.sectionid
        JOIN public.program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
        JOIN public.curriculumsubject cs ON cs.curriculumsubjectid = mgm.curriculumsubjectid
        WHERE mgm.mergegroupid = ANY(%s)
        ORDER BY pyl.programcode, pyl.yearlevel, sec.sectionname
    """, (ids,))
    members = defaultdict(list)
    for r in cur.fetchall() or []:
        d = dict(r)
        d['label'] = section_label(d['programcode'], d['yearlevel'], d['sectionname'])
        members[d['mergegroupid']].append(d)
    cur.execute("""
        SELECT m.*, ts_s.timevalue AS start, ts_e.timevalue AS "end", r.roomname, r.roomtype
        FROM public.merge_group_meeting m
        JOIN public.timeslot ts_s ON ts_s.timeid = m.starttimeid
        JOIN public.timeslot ts_e ON ts_e.timeid = m.endtimeid
        LEFT JOIN public.room r ON r.roomid = m.roomid
        WHERE m.mergegroupid = ANY(%s)
        ORDER BY m.mergegroupid,
                 ARRAY_POSITION(ARRAY['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']::text[], m.daydesc::text),
                 ts_s.timevalue
    """, (ids,))
    meetings = defaultdict(list)
    for r in cur.fetchall() or []:
        d = dict(r)
        d['start'], d['end'] = hhmm(d['start']), hhmm(d['end'])
        meetings[d['mergegroupid']].append(d)

    cfg = cfg or {}
    blocks = valid_time_blocks(cfg)
    cache = {}
    for g in groups:
        g['members'] = members.get(g['mergegroupid'], [])
        g['meetings'] = meetings.get(g['mergegroupid'], [])
        ctx = load_validation_context(cur, g['semesterid'], g['mergegroupid'], cache=cache)
        payload = {
            'groupname': g['groupname'], 'semesterid': g['semesterid'],
            'ref_curriculumsubjectid': g['ref_curriculumsubjectid'], 'faculty_mode': g['faculty_mode'],
            'employeenumber': g['employeenumber'], 'is_active': g['is_active'],
            'members': [{'sectionid': m['sectionid'], 'curriculumsubjectid': m['curriculumsubjectid'],
                         'equivalence_basis': m['equivalence_basis'],
                         'equivalence_note': m['equivalence_note']} for m in g['members']],
            'meetings': [{k: m[k] for k in ('class_type', 'daydesc', 'starttimeid', 'endtimeid', 'roomid')}
                         for m in g['meetings']],
        }
        errors, _resolved = validate_group(payload, ctx)
        g['config_errors'] = errors
        g['completeness'] = _completeness_for(g, ctx, cfg, blocks)
        g['state'] = STATE_INVALID if errors else g['completeness']['state']
        sem = ctx.get('semester') or {}
        g['semester_label'] = sem.get('label')
        g['read_only'] = bool(sem.get('ended'))
    return groups


def build_index(cur, cfg, semester_ids):
    return MergeIndex(load_groups(cur, cfg, semester_ids=semester_ids))


# Draft-preferred occurrence view: a schedule with an Official Draft is represented
# by that Draft (removal Drafts have no sessions, so the subject disappears);
# otherwise by the live effective schedule (Published minus Local-overridden
# occurrences, plus active Published Local rows).
_OCCURRENCES_SQL = """
    WITH """ + faculty_load.EFFECTIVE_SESSIONS_CTE + """,
    drafted AS (
        SELECT DISTINCT sv.scheduleid FROM public.schedule_version sv
        WHERE sv.status = 'Draft' AND sv.source IS DISTINCT FROM 'local'
    ),
    occ AS (
        SELECT es.source AS origin, es.sectionid, es.semesterid, es.curriculumsubjectid,
               es.employeenumber, es.daydesc, es.starttimeid, es.endtimeid, es.roomid
        FROM effective_sessions es
        WHERE NOT %(draft_pref)s OR es.scheduleid NOT IN (SELECT scheduleid FROM drafted)
        UNION ALL
        SELECT 'Draft', sc.sectionid, sc.semesterid, sc.curriculumsubjectid,
               COALESCE(sv.employeenumber, sc.employeenumber), ss.daydesc, ss.starttimeid,
               ss.endtimeid, ss.roomid
        FROM public.schedule_version sv
        JOIN public.schedule sc ON sc.scheduleid = sv.scheduleid
        JOIN public.schedule_sessions ss ON ss.versionid = sv.versionid
        WHERE %(draft_pref)s AND sv.status = 'Draft' AND sv.source IS DISTINCT FROM 'local'
    )
    SELECT occ.origin, occ.sectionid, occ.semesterid, occ.curriculumsubjectid,
           occ.employeenumber AS faculty_id, occ.daydesc AS day, occ.starttimeid, occ.endtimeid,
           occ.roomid, ts_s.timevalue AS start_time, ts_e.timevalue AS end_time,
           cs.subjectcode, cs.subjectname, cs.lecturehours, cs.laboratoryhours,
           cs.creditunits, cs.tuitionhours,
           sec.sectionname, pyl.programcode, pyl.yearlevel, r.roomname
    FROM occ
    JOIN public.curriculumsubject cs ON cs.curriculumsubjectid = occ.curriculumsubjectid
    LEFT JOIN public.sections sec ON sec.sectionid = occ.sectionid
    LEFT JOIN public.program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
    LEFT JOIN public.timeslot ts_s ON ts_s.timeid = occ.starttimeid
    LEFT JOIN public.timeslot ts_e ON ts_e.timeid = occ.endtimeid
    LEFT JOIN public.room r ON r.roomid = occ.roomid
    WHERE occ.semesterid = ANY(%(sems)s)
      AND (%(secs)s::int[] IS NULL OR occ.sectionid = ANY(%(secs)s::int[]))
      AND occ.daydesc IS NOT NULL AND ts_s.timevalue IS NOT NULL AND ts_e.timevalue IS NOT NULL
"""


def fetch_occurrences(cur, semester_ids, section_ids=None, *, draft_preferred=True):
    """Occurrences of the given semesters (optionally only some sections).
    draft_preferred=True: a schedule with an Official Draft is represented by it
    (Settings previews). False: the live effective schedule only (Published Official
    minus Local-overridden occurrences plus active Published Local) — the same scope
    HC15 cross-schedule validation uses."""
    cur.execute(_OCCURRENCES_SQL, {'sems': list(semester_ids),
                                   'secs': list(section_ids) if section_ids is not None else None,
                                   'draft_pref': bool(draft_preferred)})
    out = []
    for r in cur.fetchall() or []:
        d = dict(r)
        d['start_time'], d['end_time'] = hhmm(d['start_time']), hhmm(d['end_time'])
        out.append(d)
    return out


def _payload_to_group(payload, ctx, group_id=None, *, state_cfg=None):
    """In-memory group dict (same shape as load_groups) for a proposed payload."""
    times = ctx.get('timeslots') or {}
    subjects = ctx.get('subjects') or {}
    g = {
        'mergegroupid': group_id if group_id is not None else -1,
        'groupname': payload.get('groupname'), 'semesterid': payload.get('semesterid'),
        'ref_curriculumsubjectid': payload.get('ref_curriculumsubjectid'),
        'faculty_mode': payload.get('faculty_mode'), 'employeenumber': payload.get('employeenumber'),
        'is_active': payload.get('is_active'), 'config_errors': [],
        'members': [{'sectionid': m['sectionid'], 'curriculumsubjectid': m['curriculumsubjectid'],
                     'subjectcode': (subjects.get(m['curriculumsubjectid']) or {}).get('subjectcode')}
                    for m in payload.get('members') or []],
        'meetings': [dict(mt, start=times.get(mt.get('starttimeid')), end=times.get(mt.get('endtimeid')),
                          mergegroupmeetingid=('new', i))
                     for i, mt in enumerate(payload.get('meetings') or [])],
    }
    comp = _completeness_for(g, ctx, state_cfg, valid_time_blocks(state_cfg))
    g['completeness'] = comp
    g['state'] = comp['state']
    return g


def preview(cur, data, cfg, *, group_id=None, action='save'):
    """Validate a proposed create/update (or the effect of deactivate/delete) without
    writing. Returns {'errors','members','completeness','state','impact'}."""
    ensure_schema(cur)
    existing = None
    if group_id is not None:
        found = load_groups(cur, cfg, group_ids=[group_id])
        if not found:
            return {'not_found': True, 'errors': ['Merge group not found.']}
        existing = found[0]
    if action == 'save':
        payload = normalize_payload(data)
        if existing is not None and payload.get('semesterid') is None:
            payload['semesterid'] = existing['semesterid']
    else:
        payload = None
    semesterid = (payload or {}).get('semesterid') or (existing or {}).get('semesterid')
    ctx = load_validation_context(cur, semesterid, group_id)
    errors, resolved, completeness, state = [], [], None, None
    sem = ctx.get('semester') or {}
    if sem.get('ended') or (existing and existing.get('read_only')):
        errors.append('This semester has ended; its merge groups are read-only.')
    if existing is not None and payload is not None and payload['semesterid'] != existing['semesterid']:
        errors.append('A merge group cannot be moved to a different semester.')
    if payload is not None:
        errs, resolved = validate_group(payload, ctx)
        errors.extend(errs)
        proposed = _payload_to_group(payload, ctx, group_id, state_cfg=cfg)
        completeness, state = proposed['completeness'], proposed['state']
    elif action == 'activate' and existing is not None:
        errors.extend(existing['config_errors'])
        if not existing['config_errors']:
            reactivate = dict({k: existing[k] for k in ('groupname', 'semesterid', 'ref_curriculumsubjectid',
                                                        'faculty_mode', 'employeenumber')},
                              is_active=True,
                              members=[{'sectionid': m['sectionid'], 'curriculumsubjectid': m['curriculumsubjectid'],
                                        'equivalence_basis': m['equivalence_basis'],
                                        'equivalence_note': m['equivalence_note']} for m in existing['members']],
                              meetings=[{k: m[k] for k in ('class_type', 'daydesc', 'starttimeid',
                                                           'endtimeid', 'roomid')} for m in existing['meetings']])
            errs, _ = validate_group(reactivate, ctx)
            errors.extend(errs)

    impact = {'newly_diverging': [], 'losing_merge': []}
    if semesterid and not errors:
        current = [g for g in load_groups(cur, cfg, semester_ids=[semesterid])]
        before = MergeIndex(current)
        others = [g for g in current if g['mergegroupid'] != group_id]
        if action == 'save':
            after_groups = others + [_payload_to_group(payload, ctx, group_id, state_cfg=cfg)]
        elif action == 'activate':
            after_groups = others + [dict(existing, is_active=True)]
        else:   # deactivate / delete
            after_groups = others
        after = MergeIndex(after_groups)
        section_ids = {m['sectionid'] for g in current + after_groups
                       if g.get('mergegroupid') in (group_id, -1) for m in g.get('members') or []}
        if section_ids:
            impact = compare_impact(fetch_occurrences(cur, [semesterid], sorted(section_ids)), before, after)
    return {'errors': errors, 'members': resolved, 'completeness': completeness, 'state': state,
            'impact': impact, 'existing': existing, 'payload': payload}


def _lock_semester(cur, semesterid):
    # Serializes group writes per semester so the duplicate-membership check and the
    # write it guards cannot interleave with another admin's save.
    cur.execute('SELECT pg_advisory_xact_lock(%s, %s)', (4216, int(semesterid or 0)))


def save_group(cur, data, cfg, *, username, group_id=None, origin='admin', confirm_impact=False):
    """Create (group_id None) or update a group. Returns a result dict:
      {'ok': True, 'mergegroupid', 'state', 'impact'}
      {'ok': False, 'errors': [...]} | {'ok': False, 'needs_confirmation': True, 'impact'}
      {'ok': False, 'not_found': True}"""
    pv = preview(cur, data, cfg, group_id=group_id, action='save')
    if pv.get('not_found'):
        return {'ok': False, 'not_found': True, 'errors': pv['errors']}
    payload = pv['payload']
    if payload and payload.get('semesterid'):
        _lock_semester(cur, payload['semesterid'])
        pv = preview(cur, data, cfg, group_id=group_id, action='save')   # re-check under the lock
        payload = pv['payload']
    if pv['errors']:
        return {'ok': False, 'errors': pv['errors']}
    if pv['impact']['newly_diverging'] and not confirm_impact:
        return {'ok': False, 'needs_confirmation': True, 'impact': pv['impact'],
                'state': pv['state'], 'completeness': pv['completeness']}

    if group_id is None:
        cur.execute("""
            INSERT INTO public.merge_group
                (groupname, semesterid, ref_curriculumsubjectid, faculty_mode, employeenumber,
                 is_active, origin, created_by, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING mergegroupid
        """, (payload['groupname'], payload['semesterid'], payload['ref_curriculumsubjectid'],
              payload['faculty_mode'], payload['employeenumber'], payload['is_active'],
              origin if origin in ORIGINS else 'admin', username, username))
        group_id = cur.fetchone()['mergegroupid']
        old_members, old_meetings = {}, {}
    else:
        cur.execute("""
            UPDATE public.merge_group
            SET groupname = %s, ref_curriculumsubjectid = %s, faculty_mode = %s, employeenumber = %s,
                is_active = %s, updated_by = %s, updated_at = NOW()
            WHERE mergegroupid = %s
        """, (payload['groupname'], payload['ref_curriculumsubjectid'], payload['faculty_mode'],
              payload['employeenumber'], payload['is_active'], username, group_id))
        existing = pv['existing']
        old_members = {m['sectionid']: m for m in existing['members']}
        old_meetings = {(m['daydesc'], m['starttimeid']): m for m in existing['meetings']}

    # Members: diff by section so unchanged override audit (who/when) is preserved.
    keep = set()
    for m in pv['members']:
        sid = m['sectionid']
        keep.add(sid)
        old = old_members.get(sid)
        override_by = override_at = None
        if m['equivalence_basis'] == BASIS_ADMIN_OVERRIDE:
            if (old and old['equivalence_basis'] == BASIS_ADMIN_OVERRIDE
                    and old['curriculumsubjectid'] == m['curriculumsubjectid']
                    and (old['equivalence_note'] or '') == (m['equivalence_note'] or '')):
                override_by, override_at = old['override_by'], old['override_at']
            else:
                override_by = username
        if old:
            cur.execute("""
                UPDATE public.merge_group_member
                SET curriculumsubjectid = %s, equivalence_basis = %s, equivalence_note = %s,
                    override_by = %s, override_at = COALESCE(%s, CASE WHEN %s IS NULL THEN NULL ELSE NOW() END),
                    is_active = %s
                WHERE mergegroupmemberid = %s
            """, (m['curriculumsubjectid'], m['equivalence_basis'], m['equivalence_note'],
                  override_by, override_at, override_by, payload['is_active'], old['mergegroupmemberid']))
        else:
            cur.execute("""
                INSERT INTO public.merge_group_member
                    (mergegroupid, sectionid, curriculumsubjectid, equivalence_basis, equivalence_note,
                     override_by, override_at, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s IS NULL THEN NULL ELSE NOW() END, %s)
            """, (group_id, sid, m['curriculumsubjectid'], m['equivalence_basis'], m['equivalence_note'],
                  override_by, override_by, payload['is_active']))
    gone = [old_members[s]['mergegroupmemberid'] for s in old_members if s not in keep]
    if gone:
        cur.execute("DELETE FROM public.merge_group_member WHERE mergegroupmemberid = ANY(%s)", (gone,))

    # Meetings: diff by (day, start) so an unchanged meeting keeps its event identity.
    keep_slots = set()
    for mt in payload['meetings']:
        slot = (mt['daydesc'], mt['starttimeid'])
        keep_slots.add(slot)
        old = old_meetings.get(slot)
        if old:
            cur.execute("""
                UPDATE public.merge_group_meeting SET class_type = %s, endtimeid = %s, roomid = %s
                WHERE mergegroupmeetingid = %s
            """, (mt['class_type'], mt['endtimeid'], mt['roomid'], old['mergegroupmeetingid']))
        else:
            cur.execute("""
                INSERT INTO public.merge_group_meeting
                    (mergegroupid, class_type, daydesc, starttimeid, endtimeid, roomid)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (group_id, mt['class_type'], mt['daydesc'], mt['starttimeid'], mt['endtimeid'], mt['roomid']))
    gone = [old_meetings[s]['mergegroupmeetingid'] for s in old_meetings if s not in keep_slots]
    if gone:
        cur.execute("DELETE FROM public.merge_group_meeting WHERE mergegroupmeetingid = ANY(%s)", (gone,))
    return {'ok': True, 'mergegroupid': group_id, 'state': pv['state'], 'impact': pv['impact'],
            'overrides': [m for m in pv['members'] if m['equivalence_basis'] == BASIS_ADMIN_OVERRIDE]}


def set_group_active(cur, group_id, active, cfg, *, username, confirm_impact=False):
    pv = preview(cur, None, cfg, group_id=group_id, action='activate' if active else 'deactivate')
    if pv.get('not_found'):
        return {'ok': False, 'not_found': True, 'errors': pv['errors']}
    _lock_semester(cur, pv['existing']['semesterid'])
    if pv['errors']:
        return {'ok': False, 'errors': pv['errors']}
    blocking = pv['impact']['newly_diverging'] if active else pv['impact']['losing_merge']
    if blocking and not confirm_impact:
        return {'ok': False, 'needs_confirmation': True, 'impact': pv['impact']}
    cur.execute("UPDATE public.merge_group SET is_active = %s, updated_by = %s, updated_at = NOW() "
                "WHERE mergegroupid = %s", (bool(active), username, group_id))
    cur.execute("UPDATE public.merge_group_member SET is_active = %s WHERE mergegroupid = %s",
                (bool(active), group_id))
    return {'ok': True, 'mergegroupid': group_id, 'impact': pv['impact']}


def delete_group(cur, group_id, cfg, *, confirm_impact=False):
    pv = preview(cur, None, cfg, group_id=group_id, action='delete')
    if pv.get('not_found'):
        return {'ok': False, 'not_found': True, 'errors': pv['errors']}
    _lock_semester(cur, pv['existing']['semesterid'])
    if pv['errors']:
        return {'ok': False, 'errors': pv['errors']}
    if pv['impact']['losing_merge'] and not confirm_impact:
        return {'ok': False, 'needs_confirmation': True, 'impact': pv['impact']}
    cur.execute("DELETE FROM public.merge_group WHERE mergegroupid = %s", (group_id,))
    return {'ok': True, 'mergegroupid': group_id, 'groupname': pv['existing']['groupname'],
            'impact': pv['impact']}


def member_candidates(cur, semesterid, ref_csid, cfg=None):
    """For the Create/Edit form: every section of the semester with the subject(s) it
    could contribute for this reference subject, each with its server-computed
    equivalence (so the browser never re-implements equivalence)."""
    sections, subjects = load_offered(cur, semesterid)
    ref = subjects.get(ref_csid)
    if not ref:
        return {'ref': None, 'sections': []}
    out = []
    for sid, sec in sections.items():
        options = []
        for csid in sec['offered']:
            info = subject_equivalence(ref, subjects[csid])
            if info['basis'] or info['override_eligible']:
                s = subjects[csid]
                options.append({'curriculumsubjectid': csid, 'subjectcode': s['subjectcode'],
                                'subjectname': s['subjectname'], 'curriculumtype': s.get('curriculumtype'),
                                'basis': info['basis'], 'override_eligible': info['override_eligible'],
                                'differences': info['differences']})
        if not options:
            continue
        options.sort(key=lambda o: (o['basis'] is None,
                                    EQUIVALENCE_BASES.index(o['basis']) if o['basis'] else 9,
                                    o['subjectcode']))
        out.append({'sectionid': sid, 'label': sec['label'], 'isactive': sec['isactive'],
                    'programcode': sec['programcode'], 'yearlevel': sec['yearlevel'], 'options': options})
    out.sort(key=lambda s: s['label'])
    return {'ref': dict(ref), 'sections': out}


# ── P7: merging from the Manual Editor + Settings merge sets ─────────────────────
# "Allowed" model. Nothing merges by itself: a merged class is recorded only when a
# section's Save Draft / Publish places a slice EXACTLY where another section already
# has an equivalent subject (same day, start, end and room) with the same faculty
# (A+A, A+TBA, TBA+TBA), and the user confirms the merge notice. The confirmed merge
# is stored as a Merge Group meeting (created or extended here, origin 'editor'), so
# Settings lists it and HC10/HC11/HC16/HC17 recognise it through MergeIndex as usual.
# Settings "merge sets" pre-register sections (and optionally subjects) as Merge
# Groups without meetings — a plan, never a requirement.

ORIGIN_EDITOR = 'editor'
ORIGINS = ('admin', 'migrated', ORIGIN_EDITOR)


class MergeApplyError(Exception):
    """A confirmed merge could not be recorded (the whole save is rolled back)."""


def _unique_group_name(cur, semesterid, base):
    base = ' '.join(str(base or 'Merged class').split())[:100]
    cur.execute("SELECT LOWER(groupname) AS n FROM public.merge_group WHERE semesterid = %s", (semesterid,))
    taken = {r['n'] for r in cur.fetchall() or []}
    if base.lower() not in taken:
        return base
    for i in range(2, 10000):
        sfx = f' ({i})'
        cand = base[:100 - len(sfx)] + sfx
        if cand.lower() not in taken:
            return cand
    raise MergeApplyError('Could not name the merged class.')


def _section_subject(sec, subjects, code, prefer=None):
    """The section's OWN offered curriculum subject for `code` (`prefer` when offered)."""
    offered = (sec or {}).get('offered') or set()
    if prefer in offered:
        return prefer
    same = sorted(cs for cs in offered if norm_code(subjects[cs]['subjectcode']) == norm_code(code))
    return same[0] if same else None


def _meeting_class_type(subject, room):
    """Lecture/Lab of a merged meeting: a lab-only subject is Lab; a lecture+lab subject
    is Lab in a Laboratory room; everything else is Lecture (the GA's own split)."""
    lec, lab = _num(subject.get('lecturehours')), _num(subject.get('laboratoryhours'))
    if lab > 0 and lec <= 0:
        return 'Lab'
    if lab > 0 and str((room or {}).get('roomtype') or '').lower() == 'laboratory':
        return 'Lab'
    return 'Lecture'


def _active_member_map(cur, semesterid):
    cur.execute("""
        SELECT mgm.mergegroupid, mgm.sectionid, mgm.curriculumsubjectid
        FROM public.merge_group_member mgm
        JOIN public.merge_group mg ON mg.mergegroupid = mgm.mergegroupid
        WHERE mg.semesterid = %s AND mg.is_active AND mgm.is_active
    """, (semesterid,))
    return {(r['sectionid'], r['curriculumsubjectid']): r['mergegroupid'] for r in cur.fetchall() or []}


def detect_merges(cur, cfg, sem_id, section_id, rows, *, policy=None):
    """Merges a Save Draft / Publish of `section_id` would create (group model).

    `rows`: the section's submitted classes (subject_code, day/days_list, start_time,
    end_time, room_id, faculty_id). A row-day is a merge when another section's
    current class (its Draft if it has one, else Published; Local arrangements never)
    has an EQUIVALENT subject at exactly the same day, start, end and room, the
    faculty are the same or TBA, and the two are not already the same merged class.
    A different subject or a different faculty is never a merge (it stays an
    ordinary conflict). Returns {'merges': [...], 'problems': [...]}: `problems` are
    would-be merges that cannot be recorded (e.g. the sections already belong to
    two different merged classes of that subject)."""
    out = {'merges': [], 'problems': []}
    sid = _int(section_id)
    if (merge_model(cfg) != MODEL_GROUPS or sid is None or not rows
            or not bool(_num((cfg or {}).get('hc_merge_enabled', 1)))):
        return out
    policy = policy or policy_for(cfg, [sem_id], cur=cur, default_section_id=sid)
    sections, subjects = load_offered(cur, sem_id)
    me = sections.get(sid)
    if not me:
        return out
    rooms, faculty = load_rooms(cur), load_faculty(cur)
    members = _active_member_map(cur, sem_id)
    groups = {g['mergegroupid']: g for g in load_groups(cur, cfg, semester_ids=[sem_id]) if g.get('is_active')}

    by_slot = defaultdict(list)
    for o in fetch_occurrences(cur, [sem_id], None, draft_preferred=True):
        if o.get('origin') == 'Local' or _int(o.get('sectionid')) == sid or norm_room(o.get('roomid')) is None:
            continue
        by_slot[(o['day'], o['start_time'], o['end_time'], norm_room(o['roomid']))].append(o)

    def _fac(v):
        return str(v).strip() if faculty_load.has_assigned_faculty(v) else None

    def _fname(emp):
        return (faculty.get(emp) or {}).get('fullname') or emp or 'TBA'

    seen = set()
    for r in rows or ():
        code = norm_code(r.get('subject_code') or r.get('subjectcode'))
        room = norm_room(r.get('room_id') if r.get('room_id') is not None else r.get('roomid'))
        st, et = clock(r.get('start_time')), clock(r.get('end_time'))
        if not code or room is None or not st or not et:
            continue
        my_fac = _fac(r.get('faculty_id') or r.get('employeenumber'))
        my_csid = _section_subject(me, subjects, code)
        if my_csid is None:
            continue
        mine = subjects[my_csid]
        for d in _occ_days(r):
            for o in by_slot.get((d, st, et, room), ()):
                osid = _int(o['sectionid'])
                osec = sections.get(osid)
                o_csid = _section_subject(osec, subjects, o['subjectcode'], prefer=o['curriculumsubjectid'])
                if o_csid is None:
                    continue
                theirs = subjects[o_csid]
                if subject_equivalence(theirs, mine)['basis'] is None:
                    continue          # a different subject: an ordinary conflict, never a merge
                o_fac = _fac(o.get('faculty_id'))
                if my_fac and o_fac and my_fac != o_fac:
                    continue          # different faculty: an ordinary conflict, never a merge
                key = f'{osid}|{d}|{st}|{et}|{room}'
                if key in seen:
                    continue
                seen.add(key)
                mine_occ = {'section_id': sid, 'subject_code': code, 'faculty_id': my_fac,
                            'room_id': room, 'day': d, 'start_time': st, 'end_time': et}
                if policy is not None and policy.same_event(mine_occ, o, day=d):
                    continue          # already this merged class
                ga, gb = members.get((sid, my_csid)), members.get((osid, o_csid))
                g = groups.get(ga or gb)
                info = {
                    'key': key, 'subject_code': code, 'subject_name': mine.get('subjectname'),
                    'day': d, 'start': st, 'end': et, 'start_label': label12(st), 'end_label': label12(et),
                    'roomid': room, 'roomname': (rooms.get(room) or {}).get('roomname') or str(room),
                    'faculty_id': my_fac or o_fac, 'faculty_name': _fname(my_fac or o_fac),
                    'section_id': sid, 'section_label': me['label'],
                    'other_section_id': osid, 'other_section_label': osec['label'],
                    'other_status': 'Draft' if o.get('origin') == 'Draft' else 'Published',
                    'my_csid': my_csid, 'other_csid': o_csid,
                    'starttimeid': _int(o.get('starttimeid')), 'endtimeid': _int(o.get('endtimeid')),
                    'class_type': _meeting_class_type(theirs, rooms.get(room)),
                    'group': (g or {}).get('groupname'),
                }
                reason = None
                if ga and gb and ga != gb:
                    reason = (f"{me['label']} and {osec['label']} already belong to two different merged "
                              f"classes of {code} ('{groups.get(ga, {}).get('groupname')}' and "
                              f"'{groups.get(gb, {}).get('groupname')}'). Remove one in Settings first.")
                elif g is not None:
                    designee = g.get('employeenumber')
                    if designee and any(f and f != str(designee).strip() for f in (my_fac, o_fac)):
                        reason = (f"Merged class '{g.get('groupname')}' requires its designated faculty "
                                  f"{_fname(str(designee))}.")
                    else:
                        smin, emin = _minutes(st), _minutes(et)
                        clash = next((m for m in g.get('meetings') or ()
                                      if m.get('daydesc') == d and _minutes(m.get('start')) < emin
                                      and _minutes(m.get('end')) > smin
                                      and not (hhmm(m.get('start')) == st and hhmm(m.get('end')) == et
                                               and norm_room(m.get('roomid')) == room)), None)
                        if clash is not None:
                            reason = (f"Merged class '{g.get('groupname')}' already meets "
                                      f"{GroupMergePolicy._meeting_text(clash)}, which overlaps this slot.")
                (out['problems'] if reason else out['merges']).append(dict(info, reason=reason) if reason else info)
    return out


def apply_merges(cur, cfg, sem_id, merges, *, username):
    """Record confirmed merges (from detect_merges, in the SAME request) as Merge Group
    members + meetings: join the existing merged class of either section, else create
    one (origin 'editor', Same Faculty, no designee). Raises MergeApplyError when a
    merge cannot be stored validly — the caller rolls the whole save back."""
    if not merges:
        return {'applied': []}
    _lock_semester(cur, sem_id)
    sections, subjects = load_offered(cur, sem_id)
    applied, touched = [], set()
    for m in merges:
        members = _active_member_map(cur, sem_id)
        a = (m['section_id'], m['my_csid'])
        b = (m['other_section_id'], m['other_csid'])
        ga, gb = members.get(a), members.get(b)
        if ga and gb and ga != gb:
            raise MergeApplyError(f"{m['section_label']} and {m['other_section_label']} already belong to "
                                  f"different merged classes of {m['subject_code']}.")
        gid = ga or gb
        if gid is None:
            name = _unique_group_name(cur, sem_id, f"{m['subject_code']} · {m['other_section_label']} + "
                                                  f"{m['section_label']}")
            cur.execute("""
                INSERT INTO public.merge_group
                    (groupname, semesterid, ref_curriculumsubjectid, faculty_mode, employeenumber,
                     is_active, origin, created_by, updated_by)
                VALUES (%s, %s, %s, %s, NULL, TRUE, %s, %s, %s) RETURNING mergegroupid
            """, (name, sem_id, m['other_csid'], SAME_FACULTY, ORIGIN_EDITOR, username, username))
            gid = cur.fetchone()['mergegroupid']
        cur.execute("SELECT ref_curriculumsubjectid FROM public.merge_group WHERE mergegroupid = %s", (gid,))
        ref = subjects.get(cur.fetchone()['ref_curriculumsubjectid'])
        for sec_id, csid in (b, a):
            if members.get((sec_id, csid)) == gid:
                continue
            basis = subject_equivalence(ref or {}, subjects.get(csid) or {})['basis']
            if basis is None:
                raise MergeApplyError(f"{m['subject_code']} of {(sections.get(sec_id) or {}).get('label')} is not "
                                      f"equivalent to the merged class's subject.")
            cur.execute("""
                INSERT INTO public.merge_group_member
                    (mergegroupid, sectionid, curriculumsubjectid, equivalence_basis, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
            """, (gid, sec_id, csid, basis))
        cur.execute("""
            SELECT m.mergegroupmeetingid, m.daydesc, m.starttimeid, m.endtimeid, m.roomid
            FROM public.merge_group_meeting m WHERE m.mergegroupid = %s
        """, (gid,))
        existing = cur.fetchall() or []
        same = any(x['daydesc'] == m['day'] and x['starttimeid'] == m['starttimeid']
                   and x['endtimeid'] == m['endtimeid'] and norm_room(x['roomid']) == m['roomid'] for x in existing)
        if not same:
            if any(x['daydesc'] == m['day'] and x['starttimeid'] < m['endtimeid']
                   and x['endtimeid'] > m['starttimeid'] for x in existing):
                raise MergeApplyError(f"The merged class of {m['subject_code']} already meets at an overlapping "
                                      f"time on {m['day']}.")
            cur.execute("""
                INSERT INTO public.merge_group_meeting
                    (mergegroupid, class_type, daydesc, starttimeid, endtimeid, roomid)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (gid, m['class_type'], m['day'], m['starttimeid'], m['endtimeid'], m['roomid']))
        cur.execute("UPDATE public.merge_group SET updated_by = %s, updated_at = NOW() WHERE mergegroupid = %s",
                    (username, gid))
        touched.add(gid)
        applied.append(dict(m, mergegroupid=gid))
    bad = [(g['groupname'], g['config_errors']) for g in load_groups(cur, cfg, group_ids=sorted(touched))
           if g.get('config_errors')]
    if bad:
        raise MergeApplyError('; '.join(f"{n}: {' '.join(e)}" for n, e in bad))
    return {'applied': applied}


def merge_notice_text(merges):
    """One line per merge for the confirmation notice / activity log."""
    return [f"{m['subject_code']}: {m['section_label']} merges with {m['other_section_label']} — "
            f"{m['day']} {m['start_label']}–{m['end_label']}, {m['roomname']}, {m['faculty_name']}"
            for m in merges or ()]


def common_subjects(cur, semesterid, section_ids):
    """Subjects EVERY one of `section_ids` takes this semester, matched by equivalence
    (same code, or same name and hours) — what a Settings merge set may cover. One
    entry per subject of the first section: {'key','subjectcode','subjectname',
    'members': [{'sectionid','label','curriculumsubjectid','subjectcode','basis'}],
    'groups': names of active Merge Groups any of those members already belong to}."""
    sections, subjects = load_offered(cur, semesterid)
    ids = [s for s in (_int(x) for x in section_ids or ()) if s is not None]
    secs = [sections.get(s) for s in ids]
    if len(ids) < 2 or any(s is None for s in secs):
        return []
    members = _active_member_map(cur, semesterid)
    cur.execute("SELECT mergegroupid, groupname FROM public.merge_group WHERE semesterid = %s", (semesterid,))
    names = {r['mergegroupid']: r['groupname'] for r in cur.fetchall() or []}
    out = []
    first = secs[0]
    for ref_csid in sorted(first['offered'], key=lambda c: norm_code(subjects[c]['subjectcode'])):
        ref = subjects[ref_csid]
        row = {'key': ref_csid, 'subjectcode': ref['subjectcode'], 'subjectname': ref['subjectname'],
               'members': [{'sectionid': first['sectionid'], 'label': first['label'],
                            'curriculumsubjectid': ref_csid, 'subjectcode': ref['subjectcode'],
                            'basis': BASIS_REFERENCE}]}
        ok = True
        for sec in secs[1:]:
            options = []
            for csid in sec['offered']:
                basis = subject_equivalence(ref, subjects[csid])['basis']
                if basis:
                    options.append((EQUIVALENCE_BASES.index(basis), norm_code(subjects[csid]['subjectcode']),
                                    csid, basis))
            if not options:
                ok = False
                break
            _r, _c, csid, basis = min(options)
            row['members'].append({'sectionid': sec['sectionid'], 'label': sec['label'],
                                   'curriculumsubjectid': csid, 'subjectcode': subjects[csid]['subjectcode'],
                                   'basis': basis})
        if not ok:
            continue
        row['groups'] = sorted({names.get(members[(x['sectionid'], x['curriculumsubjectid'])])
                                for x in row['members']
                                if (x['sectionid'], x['curriculumsubjectid']) in members} - {None})
        out.append(row)
    return out


def create_merge_set(cur, cfg, semesterid, section_ids, subject_keys=None, *, username):
    """Settings: allow `section_ids` to merge — for the chosen subjects (`subject_keys`:
    curriculum subject ids of the FIRST section, from common_subjects) or, when none
    are chosen, every subject they all take. Each subject becomes (or extends) one
    Merge Group without meetings; the merged class itself is created later in the
    Manual Editor. Returns {'created': [...], 'extended': [...], 'skipped': [...]}."""
    sems = {s['semesterid']: s for s in load_semesters(cur)}
    sem = sems.get(_int(semesterid))
    if not sem:
        raise MergeApplyError('A valid semester is required.')
    if sem.get('ended'):
        raise MergeApplyError('This semester has ended; its merge groups are read-only.')
    ids = []
    for x in section_ids or ():
        v = _int(x)
        if v is not None and v not in ids:
            ids.append(v)
    if len(ids) < 2:
        raise MergeApplyError('Pick at least 2 sections.')
    _lock_semester(cur, sem['semesterid'])
    sections, subjects = load_offered(cur, sem['semesterid'])
    for s in ids:
        sec = sections.get(s)
        if not sec:
            raise MergeApplyError(f"Section {s} is not a section of {sem['label']}.")
        if not sec.get('isactive', True):
            raise MergeApplyError(f"Section {sec['label']} is inactive.")
    rows = common_subjects(cur, sem['semesterid'], ids)
    wanted = {_int(k) for k in subject_keys or () if _int(k) is not None}
    if wanted:
        rows = [r for r in rows if r['key'] in wanted]
    if not rows:
        raise MergeApplyError('These sections have no subject in common to merge.' if not wanted
                              else 'None of the chosen subjects is taken by every selected section.')
    created, extended, skipped = [], [], []
    for r in rows:
        members = _active_member_map(cur, sem['semesterid'])
        gids = {members[(x['sectionid'], x['curriculumsubjectid'])] for x in r['members']
                if (x['sectionid'], x['curriculumsubjectid']) in members}
        if len(gids) > 1:
            skipped.append({'subjectcode': r['subjectcode'],
                            'reason': 'these sections already belong to different merge groups of this subject'})
            continue
        if gids:
            gid = gids.pop()
            cur.execute("SELECT ref_curriculumsubjectid, groupname FROM public.merge_group WHERE mergegroupid = %s",
                        (gid,))
            g = cur.fetchone()
            ref = subjects.get(g['ref_curriculumsubjectid']) or {}
            added = []
            for x in r['members']:
                if (x['sectionid'], x['curriculumsubjectid']) in members:
                    continue
                basis = subject_equivalence(ref, subjects[x['curriculumsubjectid']])['basis']
                if basis is None:
                    continue
                cur.execute("""
                    INSERT INTO public.merge_group_member
                        (mergegroupid, sectionid, curriculumsubjectid, equivalence_basis, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                """, (gid, x['sectionid'], x['curriculumsubjectid'], basis))
                added.append(x['label'])
            if added:
                cur.execute("UPDATE public.merge_group SET updated_by = %s, updated_at = NOW() "
                            "WHERE mergegroupid = %s", (username, gid))
                extended.append({'mergegroupid': gid, 'groupname': g['groupname'], 'added': added})
            continue
        name = _unique_group_name(cur, sem['semesterid'],
                                  f"{r['subjectcode']} · {' + '.join(x['label'] for x in r['members'])}")
        cur.execute("""
            INSERT INTO public.merge_group
                (groupname, semesterid, ref_curriculumsubjectid, faculty_mode, employeenumber,
                 is_active, origin, created_by, updated_by)
            VALUES (%s, %s, %s, %s, NULL, TRUE, 'admin', %s, %s) RETURNING mergegroupid
        """, (name, sem['semesterid'], r['key'], SAME_FACULTY, username, username))
        gid = cur.fetchone()['mergegroupid']
        for x in r['members']:
            cur.execute("""
                INSERT INTO public.merge_group_member
                    (mergegroupid, sectionid, curriculumsubjectid, equivalence_basis, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
            """, (gid, x['sectionid'], x['curriculumsubjectid'], x['basis']))
        created.append({'mergegroupid': gid, 'groupname': name})
    return {'created': created, 'extended': extended, 'skipped': skipped}


def remove_member(cur, group_id, section_id, *, username):
    """Unmerge one section from a merged class. A group left with fewer than 2
    sections is deleted. Returns {'ok', 'deleted_group'} or {'ok': False, 'errors'}."""
    cur.execute("""
        SELECT mg.mergegroupid, mg.semesterid, mg.groupname FROM public.merge_group mg WHERE mg.mergegroupid = %s
    """, (group_id,))
    g = cur.fetchone()
    if not g:
        return {'ok': False, 'not_found': True, 'errors': ['Merge group not found.']}
    sem = {s['semesterid']: s for s in load_semesters(cur)}.get(g['semesterid']) or {}
    if sem.get('ended'):
        return {'ok': False, 'errors': ['This semester has ended; its merge groups are read-only.']}
    _lock_semester(cur, g['semesterid'])
    cur.execute("DELETE FROM public.merge_group_member WHERE mergegroupid = %s AND sectionid = %s",
                (group_id, _int(section_id)))
    if not cur.rowcount:
        return {'ok': False, 'errors': ['That section is not part of this merge group.']}
    cur.execute("SELECT COUNT(*) AS n FROM public.merge_group_member WHERE mergegroupid = %s", (group_id,))
    if cur.fetchone()['n'] < 2:
        cur.execute("DELETE FROM public.merge_group WHERE mergegroupid = %s", (group_id,))
        return {'ok': True, 'deleted_group': True, 'groupname': g['groupname']}
    cur.execute("UPDATE public.merge_group SET updated_by = %s, updated_at = NOW() WHERE mergegroupid = %s",
                (username, group_id))
    return {'ok': True, 'deleted_group': False, 'groupname': g['groupname']}


def discovery(cur, semester_ids, cfg):
    """Read-only migration preview: candidate groups from current legacy merges,
    each validated exactly like a create request."""
    ensure_schema(cur)
    occ = fetch_occurrences(cur, semester_ids)
    result = discover_candidates(occ, cfg)
    cache = {}
    for c in result['candidates']:
        ctx = load_validation_context(cur, c['semesterid'], cache=cache)
        # A schedule row can still point at the subject of a curriculum the section's
        # year level has since been re-mapped away from. Members must use the section's
        # CURRENT curriculum subject, so map to the same-code subject there and say so.
        for m in c['members']:
            sec = (ctx.get('sections') or {}).get(m['sectionid']) or {}
            offered = sec.get('offered') or set()
            if m['curriculumsubjectid'] in offered:
                continue
            same = [cs for cs in offered
                    if norm_code(ctx['subjects'][cs]['subjectcode']) == norm_code(c['subjectcode'])]
            if len(same) == 1:
                if c['ref_curriculumsubjectid'] == m['curriculumsubjectid']:
                    c['ref_curriculumsubjectid'] = same[0]
                m['curriculumsubjectid'] = same[0]
                c['issues'].append(f"{m['label']}: its schedule uses {c['subjectcode']} from an older "
                                   f"curriculum; the section's current curriculum subject is used instead.")
        payload = normalize_payload(c)
        errors, resolved = validate_group(payload, ctx)
        c['errors'] = errors
        c['members'] = [dict(m, basis=r.get('equivalence_basis'))
                        for m, r in zip(c['members'], resolved)] if len(resolved) == len(c['members']) else c['members']
        comp = _completeness_for(_payload_to_group(payload, ctx, state_cfg=cfg), ctx, cfg, valid_time_blocks(cfg))
        c['completeness'] = comp
        c['state'] = STATE_INVALID if errors else comp['state']
        c['semester_label'] = (ctx.get('semester') or {}).get('label')
    return result


def dry_run_report(cur, semester_ids, cfg):
    ensure_schema(cur)
    groups = load_groups(cur, cfg, semester_ids=semester_ids)
    report = dry_run(fetch_occurrences(cur, semester_ids), cfg, MergeIndex(groups))
    report['groups'] = {'total': len(groups),
                        'active': sum(1 for g in groups if g['is_active']),
                        'by_state': {s: sum(1 for g in groups if g['is_active'] and g['state'] == s)
                                     for s in (STATE_SCHEDULED, STATE_INCOMPLETE, STATE_UNSCHEDULED,
                                               STATE_INVALID)}}
    # HC17 (informational): which explicit load policy each active group would use.
    rules = load_hc17_rules(cur, [g['mergegroupid'] for g in groups])
    report['hc17'] = [{
        'group': g['groupname'], 'mergegroupid': g['mergegroupid'],
        'policies': [{'policyid': r['policyid'], 'name': r.get('policyname') or r.get('subjectcode'),
                      'range': f"{r['min_sections']}–{r['max_sections']}"} for r in rules.get(g['mergegroupid'], [])],
        'note': (None if rules.get(g['mergegroupid']) else
                 'Merge Group has no special HC17 policy; fallback will be used '
                 '(actual shared duration and the subject\'s own units, credited once).'),
    } for g in groups if g['is_active']]
    return report


def serialize(value):
    """JSON-safe copy (sets -> sorted lists, dates/times -> ISO strings)."""
    if isinstance(value, dict):
        return {str(k): serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(serialize(v) for v in value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return value


def legacy_config_view(cfg):
    """Read-only view of the legacy merge configuration still enforced under 'legacy'."""
    def _list(raw):
        try:
            v = json.loads(raw) if isinstance(raw, str) else raw
            return v if isinstance(v, list) else []
        except (TypeError, ValueError):
            return []
    return {
        'model': merge_model(cfg),
        'enabled': bool(_num((cfg or {}).get('hc_merge_enabled', 1))),
        'scope_preset': (cfg or {}).get('hc_merge_scope') or 'nstp_only',
        'scope_subjects': _list((cfg or {}).get('hc_merge_scope_subjects')),
        'section_pairs': _list((cfg or {}).get('hc_merge_section_pairs')),
    }
