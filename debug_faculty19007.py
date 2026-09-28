from database import query_db
import pprint

rows = query_db(
    """
    SELECT
        sc.scheduleid,
        sc.sectionid,
        sv.versionid,
        sv.status,
        cs.subjectcode,
        c.programcode,
        cs.yearlevel,
        ss.daydesc,
        ts1.timevalue AS start_time,
        ts2.timevalue AS end_time,
        ss.roomid
    FROM schedule_sessions ss
    JOIN schedule_version sv
      ON sv.versionid = ss.versionid
    JOIN schedule sc
      ON sc.scheduleid = sv.scheduleid
    JOIN curriculumsubject cs
      ON cs.curriculumsubjectid = sc.curriculumsubjectid
    JOIN curriculum c
      ON c.curriculumid = cs.curriculumid
    LEFT JOIN timeslot ts1
      ON ts1.timeid = ss.starttimeid
    LEFT JOIN timeslot ts2
      ON ts2.timeid = ss.endtimeid
    JOIN semester sem
      ON sem.semesterid = sc.semesterid
    WHERE sc.employeenumber = '19007'
      AND sem.academicyearid = 'AY2627'
      AND sem.semestertype = 'A'
      AND ss.daydesc = 'Tuesday'
      AND sv.status = 'Published'
    ORDER BY ts1.timevalue, cs.subjectcode
    """
)

pprint.pp(rows)