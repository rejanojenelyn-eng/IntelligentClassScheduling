"""
Make-up class request workflow (class_meeting_request → schedule_exception_log).

A make-up class is a ONE-TIME meeting on a specific calendar date. It is never
written into schedule_sessions (the recurring weekly grid). When an Academic
Head approves a request, exactly one date-specific 'makeup_class' row is
written to schedule_exception_log, in the same transaction that flips the
request from Pending to Approved.

Everything in here works on a caller-supplied psycopg2 cursor so the same
conflict definitions are shared by:
  - POST /api/faculty/submit_request         (submission, authoritative)
  - PUT  /api/faculty/my_requests/makeup/<id> (faculty edit, authoritative)
  - GET  /api/faculty/check_request_conflicts (live warning banner)
  - GET  /api/requests/validate               (Academic Head preview)
  - POST /api/requests/decide                 (final re-check inside the approval transaction)
"""
from datetime import date as _date, datetime as _datetime
import json

import psycopg2
from psycopg2.extras import RealDictCursor


MAKEUP_SOURCE_TYPE = 'makeup_class'

# Namespace for pg_advisory_xact_lock(ns, key). Approvals for the same calendar
# date are serialized so two different requests can't both pass the conflict
# check and then both claim the same room/faculty/section slot.
_APPROVAL_LOCK_NS = 7401


class MakeupError(Exception):
    """A controlled, user-safe failure. `status` is the HTTP status code."""

    def __init__(self, status, error, message, conflicts=None, extra=None):
        super().__init__(message)
        self.status    = status
        self.error     = error
        self.message   = message
        self.conflicts = conflicts or []
        self.extra     = extra or {}

    def to_json(self):
        body = {'success': False, 'error': self.error, 'message': self.message}
        if self.conflicts:
            body['conflicts'] = self.conflicts
        body.update(self.extra)
        return body


# ─────────────────────────────────────────────────────────────
#  Schema (idempotent) — also shipped as migrations/2026-09-26_makeup_exception_log.sql
# ─────────────────────────────────────────────────────────────
ENSURE_SCHEMA_SQL = """
DO $$
BEGIN
    IF to_regclass('public.schedule_exception_log') IS NULL THEN
        RETURN;
    END IF;

    ALTER TABLE public.schedule_exception_log ADD COLUMN IF NOT EXISTS exception_date DATE;
    ALTER TABLE public.schedule_exception_log ADD COLUMN IF NOT EXISTS starttimeid    INTEGER;
    ALTER TABLE public.schedule_exception_log ADD COLUMN IF NOT EXISTS endtimeid      INTEGER;
    ALTER TABLE public.schedule_exception_log ADD COLUMN IF NOT EXISTS roomid         INTEGER;
    ALTER TABLE public.schedule_exception_log ADD COLUMN IF NOT EXISTS employeenumber VARCHAR(30);

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_sel_starttime') THEN
        ALTER TABLE public.schedule_exception_log
            ADD CONSTRAINT fk_sel_starttime FOREIGN KEY (starttimeid) REFERENCES public.timeslot (timeid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_sel_endtime') THEN
        ALTER TABLE public.schedule_exception_log
            ADD CONSTRAINT fk_sel_endtime FOREIGN KEY (endtimeid) REFERENCES public.timeslot (timeid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_sel_room') THEN
        ALTER TABLE public.schedule_exception_log
            ADD CONSTRAINT fk_sel_room FOREIGN KEY (roomid) REFERENCES public.room (roomid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_sel_faculty') THEN
        ALTER TABLE public.schedule_exception_log
            ADD CONSTRAINT fk_sel_faculty FOREIGN KEY (employeenumber) REFERENCES public.faculty (employeenumber);
    END IF;
    -- A make-up exception must always carry its date, time and originating request.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_sel_makeup_complete') THEN
        ALTER TABLE public.schedule_exception_log
            ADD CONSTRAINT chk_sel_makeup_complete CHECK (
                source_type <> 'makeup_class' OR (
                    source_requestid IS NOT NULL AND exception_date IS NOT NULL
                    AND starttimeid IS NOT NULL AND endtimeid IS NOT NULL
                    AND starttimeid <> endtimeid));
    END IF;
END $$;

-- At most one exception per originating request (per source type): blocks
-- duplicate approvals from double-clicks, retries or concurrent reviewers.
CREATE UNIQUE INDEX IF NOT EXISTS uq_sel_source_request
    ON public.schedule_exception_log (source_type, source_requestid)
    WHERE source_requestid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sel_exception_date
    ON public.schedule_exception_log (exception_date)
    WHERE exception_date IS NOT NULL;
"""


def ensure_schema(cur):
    cur.execute(ENSURE_SCHEMA_SQL)


# ─────────────────────────────────────────────────────────────
#  Input parsing
# ─────────────────────────────────────────────────────────────
def parse_request_date(value):
    if not value:
        raise MakeupError(400, 'validation_error', 'Please select the make-up class date.')
    try:
        return _date.fromisoformat(str(value).strip())
    except ValueError:
        raise MakeupError(400, 'validation_error', 'The make-up class date is not a valid date.')


def clean_reason(value):
    reason = str(value).strip() if value is not None else ''
    if not reason:
        raise MakeupError(400, 'validation_error',
                          'Please provide a reason for the make-up class request.')
    return reason


def resolve_timeslot(cur, t_str, label):
    """'07:30 AM' → timeslot row {timeid, timevalue}."""
    if not t_str or not str(t_str).strip():
        raise MakeupError(400, 'validation_error', f'Please select the {label} time.')
    try:
        t = _datetime.strptime(str(t_str).strip(), '%I:%M %p').time()
    except ValueError:
        raise MakeupError(400, 'validation_error', f'The {label} time is not a valid time.')
    cur.execute("SELECT timeid, timevalue FROM timeslot WHERE timevalue = %s", [t])
    row = cur.fetchone()
    if not row:
        raise MakeupError(400, 'validation_error',
                          f'The {label} time ({t_str}) is not an available timeslot.')
    return row


def _fmt_time(t):
    return t.strftime('%I:%M %p').lstrip('0') if t else ''


# ─────────────────────────────────────────────────────────────
#  Schedule context
# ─────────────────────────────────────────────────────────────
_SCHEDULE_CTX_SQL = """
    SELECT sc.scheduleid, sc.sectionid, sc.semesterid, sc.employeenumber,
           cs.subjectcode, cs.lecturehours, cs.laboratoryhours,
           sec.sectionname, sec.programyearlevelid,
           sem.semstartdate, sem.semenddate,
           (SELECT sv.versionid FROM schedule_version sv
             WHERE sv.scheduleid = sc.scheduleid AND sv.status = 'Published'
             ORDER BY sv.version_number DESC LIMIT 1) AS published_versionid
    FROM schedule sc
    JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
    JOIN sections sec         ON sc.sectionid = sec.sectionid
    JOIN semester sem         ON sc.semesterid = sem.semesterid
"""


def schedule_context_by_id(cur, scheduleid):
    cur.execute(_SCHEDULE_CTX_SQL + " WHERE sc.scheduleid = %s", [scheduleid])
    return cur.fetchone()


def schedule_context_by_subject_section(cur, subject_code, section_id):
    """The Published schedule for this subject + section. If more than one
    semester has one, the most recent semester wins."""
    cur.execute(_SCHEDULE_CTX_SQL + """
        WHERE cs.subjectcode = %s AND sc.sectionid = %s
          AND EXISTS (SELECT 1 FROM schedule_version sv
                       WHERE sv.scheduleid = sc.scheduleid AND sv.status = 'Published')
        ORDER BY sem.isactive DESC, sem.semstartdate DESC NULLS LAST, sc.scheduleid DESC
        LIMIT 1
    """, [subject_code, section_id])
    return cur.fetchone()


# ─────────────────────────────────────────────────────────────
#  Conflict detection (date-specific)
# ─────────────────────────────────────────────────────────────
def find_conflicts(cur, *, semesterid, req_date, start_time, end_time,
                   room_id=None, employeenumber=None, sectionid=None,
                   exclude_requestid=None):
    """Hard conflicts for a one-time meeting on `req_date` between
    start_time and end_time (datetime.time values).

    Considered, all restricted to `semesterid`:
      - Published weekly sessions meeting on req_date's weekday
      - Approved make-up exceptions on exactly req_date
      - Published, active Local Arrangement sessions on that weekday (room/faculty)

    Returns a list of {type: 'room'|'faculty'|'section', message, source}.
    """
    day = req_date.strftime('%A')
    conflicts = []

    def _weekly(where, params):
        cur.execute(f"""
            SELECT cs.subjectcode, sec.sectionname,
                   ts_s.timevalue AS st, ts_e.timevalue AS et
            FROM schedule_sessions ss
            JOIN schedule_version sv  ON ss.versionid  = sv.versionid
            JOIN schedule sc          ON sv.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN sections sec         ON sc.sectionid = sec.sectionid
            JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
            JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
            WHERE sv.status = 'Published'
              AND sc.semesterid = %s
              AND ss.daydesc = %s
              AND ts_s.timevalue < %s AND ts_e.timevalue > %s
              AND {where}
            ORDER BY ts_s.timevalue LIMIT 1
        """, [semesterid, day, end_time, start_time] + params)
        return cur.fetchone()

    def _makeup(where, params):
        cur.execute(f"""
            SELECT sel.source_requestid, cs.subjectcode, sec.sectionname,
                   ts_s.timevalue AS st, ts_e.timevalue AS et
            FROM schedule_exception_log sel
            JOIN schedule sc          ON sel.scheduleid = sc.scheduleid
            JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
            JOIN sections sec         ON sc.sectionid = sec.sectionid
            JOIN timeslot ts_s ON sel.starttimeid = ts_s.timeid
            JOIN timeslot ts_e ON sel.endtimeid   = ts_e.timeid
            WHERE sel.source_type = %s
              AND sel.semesterid = %s
              AND sel.exception_date = %s
              AND ts_s.timevalue < %s AND ts_e.timevalue > %s
              AND sel.source_requestid IS DISTINCT FROM %s
              AND {where}
            ORDER BY ts_s.timevalue LIMIT 1
        """, [MAKEUP_SOURCE_TYPE, semesterid, req_date, end_time, start_time,
              exclude_requestid] + params)
        return cur.fetchone()

    def _local(where, params):
        cur.execute(f"""
            SELECT las.subjectcode, ts_s.timevalue AS st, ts_e.timevalue AS et
            FROM local_arrangement_sessions las
            JOIN local_arrangement la ON las.arrangementid = la.arrangementid
            JOIN timeslot ts_s ON las.starttimeid = ts_s.timeid
            JOIN timeslot ts_e ON las.endtimeid   = ts_e.timeid
            WHERE la.status = 'Published'
              AND COALESCE(la.is_active, TRUE) = TRUE
              AND la.semesterid = %s
              AND UPPER(las.daydesc) = UPPER(%s)
              AND ts_s.timevalue < %s AND ts_e.timevalue > %s
              AND {where}
            ORDER BY ts_s.timevalue LIMIT 1
        """, [semesterid, day, end_time, start_time] + params)
        return cur.fetchone()

    date_lbl = req_date.strftime('%a, %b %d, %Y').replace(' 0', ' ')

    def _add(kind, row, what, source):
        span = f"{_fmt_time(row['st'])}–{_fmt_time(row['et'])}"
        conflicts.append({'type': kind, 'source': source,
                          'message': f"{what} ({span}) on {date_lbl}."})

    if room_id:
        cur.execute("SELECT roomname FROM room WHERE roomid = %s", [room_id])
        rn = cur.fetchone()
        room_lbl = f"Room {rn['roomname']}" if rn else 'The selected room'
        r = _weekly("ss.roomid = %s", [room_id])
        if r:
            _add('room', r, f"{room_lbl} is occupied by {r['subjectcode']} ({r['sectionname']})", 'weekly')
        else:
            r = _makeup("sel.roomid = %s", [room_id])
            if r:
                _add('room', r, f"{room_lbl} is booked by an approved make-up class of "
                                f"{r['subjectcode']} ({r['sectionname']})", 'makeup')
            else:
                r = _local("las.roomid = %s", [room_id])
                if r:
                    _add('room', r, f"{room_lbl} is used by a Local Arrangement "
                                    f"({r['subjectcode'] or 'session'})", 'local_arrangement')

    if employeenumber:
        emp = str(employeenumber)
        r = _weekly("sc.employeenumber = %s", [emp])
        if r:
            _add('faculty', r, f"The faculty already teaches {r['subjectcode']} ({r['sectionname']})", 'weekly')
        else:
            r = _makeup("sel.employeenumber = %s", [emp])
            if r:
                _add('faculty', r, f"The faculty already has an approved make-up class of "
                                   f"{r['subjectcode']} ({r['sectionname']})", 'makeup')
            else:
                r = _local("las.faculty_employeenumber = %s", [emp])
                if r:
                    _add('faculty', r, f"The faculty has a Local Arrangement "
                                       f"({r['subjectcode'] or 'session'})", 'local_arrangement')

    if sectionid:
        r = _weekly("sc.sectionid = %s", [int(sectionid)])
        if r:
            _add('section', r, f"Section {r['sectionname']} already has {r['subjectcode']}", 'weekly')
        else:
            r = _makeup("sc.sectionid = %s", [int(sectionid)])
            if r:
                _add('section', r, f"Section {r['sectionname']} already has an approved make-up class of "
                                   f"{r['subjectcode']}", 'makeup')

    return conflicts


def find_program_warnings(cur, *, semesterid, req_date, start_time, end_time,
                          programyearlevelid, scheduleid):
    """Legacy program/year-level cohort guard: another section of the same
    program + year level has a Published class at that time. Reported as an
    advisory warning (it does not block approval) — the hard per-section check
    lives in find_conflicts()."""
    if not programyearlevelid:
        return []
    cur.execute("""
        SELECT cs.subjectcode, sec.sectionname, ts_s.timevalue AS st, ts_e.timevalue AS et
        FROM schedule_sessions ss
        JOIN schedule_version sv  ON ss.versionid  = sv.versionid
        JOIN schedule sc          ON sv.scheduleid = sc.scheduleid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        JOIN sections sec         ON sc.sectionid = sec.sectionid
        JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
        JOIN timeslot ts_e ON ss.endtimeid   = ts_e.timeid
        WHERE sv.status = 'Published'
          AND sc.semesterid = %s
          AND sec.programyearlevelid = %s
          AND ss.daydesc = %s
          AND ts_s.timevalue < %s AND ts_e.timevalue > %s
          AND sc.scheduleid <> %s
        ORDER BY ts_s.timevalue LIMIT 1
    """, [semesterid, programyearlevelid, req_date.strftime('%A'),
          end_time, start_time, scheduleid])
    r = cur.fetchone()
    if not r:
        return []
    return [{'type': 'program', 'source': 'weekly',
             'message': f"Same program/year level: {r['sectionname']} has {r['subjectcode']} "
                        f"({_fmt_time(r['st'])}–{_fmt_time(r['et'])})."}]


def conflicts_error(conflicts):
    return MakeupError(409, 'schedule_conflict',
                       'The requested make-up period conflicts with an existing schedule.',
                       conflicts=conflicts)


# ─────────────────────────────────────────────────────────────
#  Submission / edit validation
# ─────────────────────────────────────────────────────────────
def validate_submission(cur, *, faculty_empno, ctx, request_date, start_time,
                        end_time, room_id, reason, exclude_requestid=None, today=None):
    """Full server-side validation of a make-up request. Returns a dict of
    normalized values ready to insert; raises MakeupError otherwise."""
    reason = clean_reason(reason)
    req_date = parse_request_date(request_date)

    if not ctx:
        raise MakeupError(404, 'schedule_not_found',
                          'No published schedule was found for the selected subject and section.')
    if not ctx.get('published_versionid'):
        raise MakeupError(400, 'schedule_not_published',
                          'This class does not have a published schedule yet.')
    if str(ctx.get('employeenumber') or '') != str(faculty_empno):
        raise MakeupError(403, 'forbidden',
                          'You can only request a make-up class for a class assigned to you.')

    today = today or _date.today()
    if req_date < today:
        raise MakeupError(400, 'validation_error', 'The make-up class date cannot be in the past.')
    sem_start, sem_end = ctx.get('semstartdate'), ctx.get('semenddate')
    if (sem_start and req_date < sem_start) or (sem_end and req_date > sem_end):
        rng = ' – '.join(d.strftime('%b %d, %Y') for d in (sem_start, sem_end) if d)
        raise MakeupError(400, 'validation_error',
                          f"The make-up date must be within the class's semester ({rng}).")

    st = resolve_timeslot(cur, start_time, 'start')
    et = resolve_timeslot(cur, end_time, 'end')
    if et['timevalue'] <= st['timevalue']:
        raise MakeupError(400, 'validation_error', 'End Time must be later than Start Time.')

    required = float(ctx.get('lecturehours') or 0) + float(ctx.get('laboratoryhours') or 0)
    span = (_datetime.combine(req_date, et['timevalue'])
            - _datetime.combine(req_date, st['timevalue'])).total_seconds() / 3600.0
    if required > 0 and abs(span - required) > 0.01:
        raise MakeupError(400, 'validation_error',
                          f"Selected time span is {span:g}h, but {ctx['subjectcode']} requires "
                          f"{required:g}h per session.")

    rid = None
    if room_id not in (None, ''):
        try:
            rid = int(room_id)
        except (TypeError, ValueError):
            raise MakeupError(400, 'validation_error', 'The selected room is not valid.')
        cur.execute("SELECT 1 FROM room WHERE roomid = %s", [rid])
        if not cur.fetchone():
            raise MakeupError(400, 'validation_error', 'The selected room no longer exists.')
    else:
        raise MakeupError(400, 'validation_error', 'Please select a room.')

    conflicts = find_conflicts(
        cur, semesterid=ctx['semesterid'], req_date=req_date,
        start_time=st['timevalue'], end_time=et['timevalue'],
        room_id=rid, employeenumber=ctx['employeenumber'], sectionid=ctx['sectionid'],
        exclude_requestid=exclude_requestid)
    if conflicts:
        raise conflicts_error(conflicts)

    return {
        'scheduleid': ctx['scheduleid'], 'requested_date': req_date,
        'starttimeid': st['timeid'], 'endtimeid': et['timeid'],
        'roomid': rid, 'reason': reason,
    }


# ─────────────────────────────────────────────────────────────
#  Decision (approve / reject)
# ─────────────────────────────────────────────────────────────
def _lock_pending(cur, request_id):
    cur.execute("""
        SELECT requestid, scheduleid, requested_date, new_starttimeid, new_endtimeid,
               new_roomid, reason, notes, status, submitted_by
        FROM class_meeting_request
        WHERE requestid = %s
        FOR UPDATE
    """, [request_id])
    row = cur.fetchone()
    if not row:
        raise MakeupError(404, 'not_found', 'Make-up request not found.')
    if row['status'] != 'Pending':
        raise MakeupError(409, 'invalid_state',
                          f"This request has already been {row['status'].lower()} and can no longer be changed.",
                          extra={'current_status': row['status']})
    return row


def request_conflict_snapshot(cur, req, ctx, exclude_self=True):
    """(conflicts, warnings) for a stored request against current DB state."""
    cur.execute("SELECT timeid, timevalue FROM timeslot WHERE timeid IN (%s, %s)",
                [req['new_starttimeid'], req['new_endtimeid']])
    tv = {r['timeid']: r['timevalue'] for r in cur.fetchall()}
    st, et = tv.get(req['new_starttimeid']), tv.get(req['new_endtimeid'])
    common = dict(semesterid=ctx['semesterid'], req_date=req['requested_date'],
                  start_time=st, end_time=et)
    conflicts = find_conflicts(
        cur, room_id=req['new_roomid'], employeenumber=ctx['employeenumber'],
        sectionid=ctx['sectionid'],
        exclude_requestid=req['requestid'] if exclude_self else None, **common)
    warnings = find_program_warnings(
        cur, programyearlevelid=ctx['programyearlevelid'],
        scheduleid=ctx['scheduleid'], **common)
    return conflicts, warnings


def decide(conn, *, request_id, decision, reviewer, remarks=''):
    """Pending → Approved | Rejected, atomically. Commits on success, rolls
    back and raises MakeupError on any failure. Returns a result dict."""
    if decision not in ('Approved', 'Rejected'):
        raise MakeupError(400, 'validation_error', 'Invalid decision.')
    try:
        request_id = int(request_id)
    except (TypeError, ValueError):
        raise MakeupError(400, 'validation_error', 'Invalid request id.')

    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT 1 FROM faculty WHERE employeenumber = %s", [reviewer])
        if not reviewer or not cur.fetchone():
            raise MakeupError(403, 'missing_reviewer',
                              'Your account is not linked to a faculty record, so the decision cannot be recorded.')

        req = _lock_pending(cur, request_id)

        result = {'requestid': request_id, 'status': decision}
        if decision == 'Approved':
            # Serialize approvals for the same date so concurrent approvals of
            # different requests can't both claim the same slot.
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)",
                        [_APPROVAL_LOCK_NS, req['requested_date'].toordinal()])
            ctx = schedule_context_by_id(cur, req['scheduleid'])
            if not ctx:
                raise MakeupError(404, 'schedule_not_found',
                                  'The class schedule for this request no longer exists.')
            conflicts, warnings = request_conflict_snapshot(cur, req, ctx)
            if conflicts:
                raise conflicts_error(conflicts)
            flags = {'checked_at': _datetime.now().isoformat(timespec='seconds'),
                     'conflicts': [], 'warnings': warnings}
        else:
            flags = None

        cur.execute("""
            UPDATE class_meeting_request
            SET status = %s, reviewed_by = %s, reviewed_at = NOW(),
                decided_by = %s, decided_at = NOW(), remarks = %s,
                csp_flags = COALESCE(%s::jsonb, csp_flags)
            WHERE requestid = %s AND status = 'Pending'
            RETURNING reviewed_at
        """, [decision, reviewer, reviewer, remarks or None,
              json.dumps(flags) if flags is not None else None, request_id])
        upd = cur.fetchone()
        if not upd:
            raise MakeupError(409, 'invalid_state', 'This request has already been decided.')
        result['reviewed_at'] = upd['reviewed_at'].isoformat(timespec='seconds')

        if decision == 'Approved':
            cur.execute("""
                INSERT INTO schedule_exception_log
                    (source_type, source_requestid, scheduleid, violated_rules,
                     approved_by, approved_at, semesterid, notes,
                     exception_date, starttimeid, endtimeid, roomid, employeenumber)
                VALUES (%s, %s, %s, '[]'::jsonb, %s, NOW(), %s, %s, %s, %s, %s, %s, %s)
                RETURNING logid
            """, [MAKEUP_SOURCE_TYPE, request_id, req['scheduleid'], reviewer,
                  ctx['semesterid'], req['reason'], req['requested_date'],
                  req['new_starttimeid'], req['new_endtimeid'], req['new_roomid'],
                  ctx['employeenumber']])
            result['exception_logid'] = cur.fetchone()['logid']
            result['warnings'] = warnings

        conn.commit()
        return result
    except MakeupError:
        conn.rollback()
        raise
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise MakeupError(409, 'invalid_state', 'This request has already been approved.')
    except psycopg2.Error as e:
        conn.rollback()
        print(f"[makeup_requests.decide] DB error: {e}")
        raise MakeupError(500, 'server_error', 'The decision could not be saved. Please try again.')
    finally:
        cur.close()
