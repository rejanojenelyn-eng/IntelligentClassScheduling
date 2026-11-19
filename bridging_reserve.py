"""
Bridging reservations — a Bridging section's class times, kept free in its Regular sections.

A Bridging section (Program Management → "With Bridging", sections.withbridging) holds
the bridging subjects (curriculumsubject.isbridging) that some students of the program's
Regular sections take on top of their regular load. Its sessions belong ONLY to the
Bridging section (that is where they are saved, exported and counted), but every
Regular section of the same program + year level + school year must keep those times
free, or a bridging student would have two classes at once.

    reserved_sessions(section_id, semestertype, ay_id)

returns those sessions — bridging subjects only — for a Regular section (Published + Draft, a subject's Draft
replacing its Published rows — the same rule the Manual Editor uses), or [] when the
section is itself a Bridging section or there is no Bridging section. Used by:

    • the Manual Editor's Program View calendar/table (read-only "BRIDGING" blocks) and
      its section-conflict checks (/api/get_offerings_schedule, /api/manual/section_schedule)
    • Schedule Generation (IntelligentScheduler) — the slots are treated as already taken
      by the section (HC12), so the generator places nothing there.

Nothing here writes anything: exports and every other per-section listing keep reading
the Bridging section's own rows only, so a Regular section's export never includes them.
"""
from database import query_db


def reserved_sessions(section_id, semestertype, ay_id):
    try:
        section_id = int(section_id)
    except (TypeError, ValueError):
        return []
    semestertype = str(semestertype or '').strip().upper()
    ay_id = str(ay_id or '').strip()
    if not semestertype or not ay_id:
        return []
    rows = query_db("""
        WITH target AS (
            SELECT UPPER(pyl.programcode) AS programcode, pyl.yearlevel, pyl.academicyearid
            FROM sections s
            JOIN program_yearlevel pyl ON pyl.programyearlevelid = s.programyearlevelid
            WHERE s.sectionid = %s
              AND COALESCE(s.withbridging, FALSE) = FALSE
        )
        SELECT cs.subjectcode,
               cs.subjectname,
               COALESCE(f.lastname || ', ' || f.firstname, 'TBA') AS instructor,
               sc.employeenumber                                  AS faculty_id,
               COALESCE(r.roomname, 'TBA')                        AS roomname,
               ss.daydesc,
               ss.starttimeid,
               ss.endtimeid,
               ts_s.timevalue                                     AS start_tv,
               ts_e.timevalue                                     AS end_tv,
               sv.status                                          AS bridging_status,
               bs.sectionid                                       AS bridging_section_id,
               bs.sectionname                                     AS bridging_section
        FROM target t
        JOIN program_yearlevel bpyl ON UPPER(bpyl.programcode) = t.programcode
                                   AND bpyl.yearlevel         = t.yearlevel
                                   AND bpyl.academicyearid    = t.academicyearid
        JOIN sections bs            ON bs.programyearlevelid  = bpyl.programyearlevelid
        JOIN schedule sc            ON sc.sectionid           = bs.sectionid
        JOIN semester sem           ON sem.semesterid         = sc.semesterid
        JOIN schedule_version sv    ON sv.scheduleid          = sc.scheduleid
        JOIN schedule_sessions ss   ON ss.versionid           = sv.versionid
        JOIN curriculumsubject cs   ON cs.curriculumsubjectid = sc.curriculumsubjectid
        LEFT JOIN faculty f         ON f.employeenumber       = sc.employeenumber
        LEFT JOIN room r            ON r.roomid               = ss.roomid
        LEFT JOIN timeslot ts_s     ON ts_s.timeid            = ss.starttimeid
        LEFT JOIN timeslot ts_e     ON ts_e.timeid            = ss.endtimeid
        WHERE bs.withbridging = TRUE
          AND COALESCE(bs.isactive, TRUE) = TRUE
          -- Only the bridging subjects: a section switched to Bridging can still hold
          -- Regular-curriculum classes from an earlier term, which are not reserved.
          AND cs.isbridging = TRUE
          AND bs.sectionid <> %s
          AND UPPER(sem.semestertype) = %s
          AND sem.academicyearid      = %s
          AND sv.status IN ('Published', 'Draft')
          AND ss.daydesc IS NOT NULL
          -- A subject's Draft is the complete replacement of its Published schedule.
          AND NOT (
              sv.status = 'Published'
              AND EXISTS (
                  SELECT 1
                  FROM schedule_version sv_d
                  JOIN schedule sc_d          ON sc_d.scheduleid = sv_d.scheduleid
                  JOIN curriculumsubject cs_d ON cs_d.curriculumsubjectid = sc_d.curriculumsubjectid
                  WHERE sv_d.status = 'Draft'
                    AND sc_d.sectionid  = sc.sectionid
                    AND sc_d.semesterid = sc.semesterid
                    AND UPPER(cs_d.subjectcode) = UPPER(cs.subjectcode)
              )
          )
        ORDER BY bs.sectionname, ss.daydesc, ss.starttimeid
    """, (section_id, section_id, semestertype, ay_id)) or []
    return [dict(r) for r in rows]


def label(row):
    """'CHEM 015 (bridging — LQBSCE-1-1)' for conflict messages."""
    return f"{row.get('subjectcode') or '?'} (bridging — {row.get('bridging_section') or 'Bridging section'})"
