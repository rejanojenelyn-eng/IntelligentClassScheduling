-----------------------------------------------------------------------
--DDL--
-----------------------------------------------------------------------
-- Programs
CREATE TABLE IF NOT EXISTS public.programs
(
    programcode character varying(10) COLLATE pg_catalog."default" NOT NULL,
    programname character varying(100) COLLATE pg_catalog."default" NOT NULL,
    programtype character varying(20) COLLATE pg_catalog."default" NOT NULL,
    isactive boolean NOT NULL DEFAULT true,
    numyearlevel integer,
    CONSTRAINT programs_pkey PRIMARY KEY (programcode),
    CONSTRAINT chk_programtype CHECK (programtype::text = ANY (ARRAY['Undergraduate'::character varying::text, 'Diploma'::character varying::text]))
);

-----------------------------------------------------------------------
-- Curriculum
CREATE TABLE IF NOT EXISTS public.curriculum
(	curriculumid INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    curriculumcode VARCHAR(6) NOT NULL,
    programcode VARCHAR(10) NOT NULL,
    curriculumyear VARCHAR(9) NOT NULL,
    curriculumversion INTEGER,
    isactive BOOLEAN DEFAULT true,
    CONSTRAINT uq_curriculum
        UNIQUE ( programcode, curriculumcode, curriculumversion ),
    CONSTRAINT fk_curriculum_program
        FOREIGN KEY (programcode)
        REFERENCES public.programs(programcode)
);
-----------------------------------------------------------------------
-- Subject
CREATE TABLE IF NOT EXISTS public.curriculumsubject
(
    curriculumsubjectid INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    curriculumid INTEGER NOT NULL,

    subjectcode VARCHAR(15) NOT NULL,

    subjectname VARCHAR(150) NOT NULL,

    lecturehours INTEGER NOT NULL DEFAULT 0,

    laboratoryhours INTEGER NOT NULL DEFAULT 0,

    creditunits INTEGER NOT NULL DEFAULT 0,

    tuitionhours INTEGER NOT NULL DEFAULT 0,

    prerequisite VARCHAR(255),

    corequisite VARCHAR(255),

    yearlevel INTEGER NOT NULL,

    semester VARCHAR(10) NOT NULL,

    isshared BOOLEAN DEFAULT FALSE,

    CONSTRAINT uq_curriculumsubject
        UNIQUE
        (
            curriculumid,
            subjectcode,
            yearlevel,
            semester
        ),

    CONSTRAINT fk_curriculumsubject_curriculum
        FOREIGN KEY (curriculumid)
        REFERENCES public.curriculum(curriculumid),

    CONSTRAINT chk_curriculumsubject_yearlevel
        CHECK (yearlevel BETWEEN 1 AND 5),

    CONSTRAINT chk_curriculumsubject_semester
        CHECK (semester IN ('A','B','C'))
);
-----------------------------------------------------------------------
-- Academicyear
CREATE TABLE IF NOT EXISTS public.academicyear
(
    academicyearid character varying(9) PRIMARY KEY,
    yearstart integer NOT NULL,
    yearend integer NOT NULL,
    startdate date,
    enddate date,
    isactive boolean DEFAULT false,
    CONSTRAINT uq_academicyear UNIQUE (yearstart, yearend)
);

-----------------------------------------------------------------------
-- Program Year Level
CREATE TABLE IF NOT EXISTS public.program_yearlevel
(
    programyearlevelid    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    programcode VARCHAR(10) NOT NULL,
    academicyearid        VARCHAR(9) NOT NULL,
	startacademicyear character varying(9) COLLATE pg_catalog."default" NOT NULL,
    yearlevel             INTEGER NOT NULL,
    curriculumid          INTEGER,
    isactive              BOOLEAN DEFAULT true,
    remarks               VARCHAR(255),

    CONSTRAINT uq_program_yearlevel
        UNIQUE (
            programcode,
            academicyearid,
			startacademicyear,
            yearlevel
        ),

    CONSTRAINT fk_curriculum_program
        FOREIGN KEY (programcode)
        REFERENCES public.programs(programcode),

    CONSTRAINT fk_pyl_curriculum
        FOREIGN KEY (curriculumid)
        REFERENCES public.curriculum(curriculumid),

    CONSTRAINT fk_pyl_academicyear
        FOREIGN KEY (academicyearid)
        REFERENCES public.academicyear(academicyearid),

    CONSTRAINT chk_pyl_yearlevel
        CHECK (yearlevel >= 1 AND yearlevel <= 5)
);
-----------------------------------------------------------------------
-- Sections
CREATE TABLE IF NOT EXISTS public.sections
(
    sectionid             INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    programyearlevelid    INTEGER NOT NULL,
    sectionname           VARCHAR(10) NOT NULL,
    isactive              BOOLEAN DEFAULT true,
    start_semestertype    VARCHAR(5),   -- A/B/C: first semester of its AY it exists in; NULL = whole AY
    CONSTRAINT uq_section
        UNIQUE ( programyearlevelid, sectionname ),
    CONSTRAINT fk_section_programyearlevel
        FOREIGN KEY (programyearlevelid)
        REFERENCES public.program_yearlevel(programyearlevelid)
        ON DELETE CASCADE
);
-----------------------------------------------------------------------
-- Timeslot

CREATE TABLE IF NOT EXISTS public.timeslot
(
    timeid integer NOT NULL GENERATED ALWAYS AS IDENTITY ( INCREMENT 1 START 1 MINVALUE 1 MAXVALUE 2147483647 CACHE 1 ),
    timevalue time without time zone NOT NULL,
    CONSTRAINT timeslot_pkey PRIMARY KEY (timeid),
    CONSTRAINT timeslot_timevalue_key UNIQUE (timevalue)
);

-----------------------------------------------------------------------
-- Semester
CREATE TABLE IF NOT EXISTS public.semester
(
    semesterid integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    academicyearid  character varying(9) NOT NULL,
    semestertype character NULL,
    semstartdate date,
    semenddate date,
    isactive boolean DEFAULT false,
    CONSTRAINT fk_semester_academicyear FOREIGN KEY (academicyearid)
        REFERENCES public.academicyear(academicyearid),
    CONSTRAINT chk_semestertype CHECK (
        semestertype IN ('A', 'B', 'C')
    ),
    CONSTRAINT uq_semester UNIQUE (academicyearid, semestertype)
);

-----------------------------------------------------------------------
-- Employeetype
CREATE TABLE IF NOT EXISTS public.employeetype
(
    employeetypeid integer NOT NULL GENERATED ALWAYS AS IDENTITY ( INCREMENT 1 START 1 MINVALUE 1 MAXVALUE 2147483647 CACHE 1 ),
    typename character varying(50) COLLATE pg_catalog."default" NOT NULL,
    regularload integer,
    parttimeload integer,
    teachingsubstitution integer,
    regular_start time without time zone,
    regular_end time without time zone,
    parttime_start time without time zone,
    parttime_end time without time zone,
    CONSTRAINT employeetype_pkey PRIMARY KEY (employeetypeid),
    CONSTRAINT employeetype_typename_key UNIQUE (typename)
);
-----------------------------------------------------------------------
-- Designation;
CREATE TABLE IF NOT EXISTS public.designation
(
    designationid integer NOT NULL GENERATED ALWAYS AS IDENTITY ( INCREMENT 1 START 1 MINVALUE 1 MAXVALUE 2147483647 CACHE 1 ),
    designationname character varying(100) COLLATE pg_catalog."default" NOT NULL,
    regularloadunit integer,
    nightteachingservice integer,
    CONSTRAINT designation_pkey PRIMARY KEY (designationid),
    CONSTRAINT designation_designationname_key UNIQUE (designationname)
);
-----------------------------------------------------------------------
-- Specialization;
CREATE TABLE IF NOT EXISTS public.specialization
(
    specializationid integer NOT NULL GENERATED ALWAYS AS IDENTITY ( INCREMENT 1 START 1 MINVALUE 1 MAXVALUE 2147483647 CACHE 1 ),
    specializationname character varying(100) COLLATE pg_catalog."default" NOT NULL,
    isactive boolean NOT NULL DEFAULT true,
    CONSTRAINT specialization_pkey PRIMARY KEY (specializationid),
    CONSTRAINT specialization_specializationname_key UNIQUE (specializationname)
);
-----------------------------------------------------------------------
-- Faculty;
CREATE TABLE IF NOT EXISTS public.faculty
(
    employeenumber character varying(30) COLLATE pg_catalog."default" NOT NULL,
    firstname character varying(50) COLLATE pg_catalog."default" NOT NULL,
    middlename character varying(50) COLLATE pg_catalog."default",
    lastname character varying(50) COLLATE pg_catalog."default" NOT NULL,
    email character varying(100) COLLATE pg_catalog."default" NOT NULL,
    contactnumber character varying(15) COLLATE pg_catalog."default" NOT NULL,
    specializationid integer NOT NULL,
    employeetypeid integer NOT NULL,
    designationid integer,
    employeestatus character varying(20) COLLATE pg_catalog."default" NOT NULL,
    CONSTRAINT faculty_pkey PRIMARY KEY (employeenumber),
    CONSTRAINT faculty_email_key UNIQUE (email),
    CONSTRAINT fk_faculty_designation FOREIGN KEY (designationid)
        REFERENCES public.designation (designationid) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE NO ACTION,
    CONSTRAINT fk_faculty_employeetype FOREIGN KEY (employeetypeid)
        REFERENCES public.employeetype (employeetypeid) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE NO ACTION,
    CONSTRAINT fk_faculty_specialization FOREIGN KEY (specializationid)
        REFERENCES public.specialization (specializationid) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE NO ACTION,
    CONSTRAINT chk_employeestatus CHECK (employeestatus::text = ANY (ARRAY['Temporary'::character varying::text, 'Permanent'::character varying::text, 'Part-Time'::character varying::text]))
);
-----------------------------------------------------------------------
-- MERGED CLASS
CREATE TABLE IF NOT EXISTS public.mergedclass
(	mergedclassid       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
	curriculumsubjectid INTEGER NOT NULL,
    semesterid          INTEGER NOT NULL,
    employeenumber      VARCHAR(30),
    classname           VARCHAR(100),
    isactive            BOOLEAN DEFAULT true,
    datecreated         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_mergedclass_curriculumsubject
        FOREIGN KEY (curriculumsubjectid)
        REFERENCES public.curriculumsubject(curriculumsubjectid),
    CONSTRAINT fk_mergedclass_semester
        FOREIGN KEY (semesterid)
        REFERENCES public.semester(semesterid),
    CONSTRAINT fk_mergedclass_faculty
        FOREIGN KEY (employeenumber)
        REFERENCES public.faculty(employeenumber)
);
-----------------------------------------------------------------------
-- MERGED CLASS SECTIONS
CREATE TABLE IF NOT EXISTS public.mergedclass_sections
(
    mergedclasssectionid INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mergedclassid        INTEGER NOT NULL,
    sectionid            INTEGER NOT NULL,
    CONSTRAINT uq_mergedclass_section
        UNIQUE ( mergedclassid, sectionid),
    CONSTRAINT fk_mcs_mergedclass
        FOREIGN KEY (mergedclassid)
        REFERENCES public.mergedclass(mergedclassid)
        ON DELETE CASCADE,
    CONSTRAINT fk_mcs_section
        FOREIGN KEY (sectionid)
        REFERENCES public.sections(sectionid)
        ON DELETE CASCADE
);

-----------------------------------------------------------------------
-- Accounts
CREATE TABLE IF NOT EXISTS public.accounts
(
    userid integer NOT NULL GENERATED ALWAYS AS IDENTITY ( INCREMENT 1 START 1 MINVALUE 1 MAXVALUE 2147483647 CACHE 1 ),
    username character varying(50) COLLATE pg_catalog."default" NOT NULL,
    passwordhash character varying(255) COLLATE pg_catalog."default" NOT NULL,
    isactive boolean NOT NULL DEFAULT true,
    datecreated timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    role character varying(20) COLLATE pg_catalog."default" NOT NULL,
    employeenumber character varying(30) COLLATE pg_catalog."default",
    CONSTRAINT accounts_pkey PRIMARY KEY (userid),
    CONSTRAINT accounts_username_key UNIQUE (username),
    CONSTRAINT fk_accounts_employeenumber FOREIGN KEY (employeenumber)
        REFERENCES public.faculty (employeenumber) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE NO ACTION,
    CONSTRAINT chk_accountrole CHECK (role::text = ANY (ARRAY['Admin'::character varying::text, 'Faculty'::character varying::text, 'Academic Head'::character varying::text]))
);

-----------------------------------------------------------------------
-- Faculty_archive

CREATE TABLE IF NOT EXISTS public.faculty_archive
(
    facultyarchiveid integer NOT NULL GENERATED ALWAYS AS IDENTITY ( INCREMENT 1 START 1 MINVALUE 1 MAXVALUE 2147483647 CACHE 1 ),
    employeenumber character varying(30) COLLATE pg_catalog."default",
    firstname character varying(50) COLLATE pg_catalog."default",
    middlename character varying(50) COLLATE pg_catalog."default",
    lastname character varying(50) COLLATE pg_catalog."default",
    email character varying(100) COLLATE pg_catalog."default",
    contactnumber character varying(15) COLLATE pg_catalog."default",
    specializationid integer,
    employeetypeid integer,
    designationid integer,
    employeestatus character varying(20) COLLATE pg_catalog."default",
    archivedat timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT faculty_archive_pkey PRIMARY KEY (facultyarchiveid)
);
-----------------------------------------------------------------------
-- Building
CREATE TABLE IF NOT EXISTS public.building
(
    buildingid integer NOT NULL GENERATED ALWAYS AS IDENTITY ( INCREMENT 1 START 1 MINVALUE 1 MAXVALUE 2147483647 CACHE 1 ),
    buildingname character varying(100) COLLATE pg_catalog."default" NOT NULL,
    isactive boolean NOT NULL DEFAULT true,
    CONSTRAINT building_pkey PRIMARY KEY (buildingid),
    CONSTRAINT building_buildingname_key UNIQUE (buildingname)
);
-----------------------------------------------------------------------
-- Room
CREATE TABLE IF NOT EXISTS public.room
(
    roomid integer NOT NULL GENERATED ALWAYS AS IDENTITY ( INCREMENT 1 START 1 MINVALUE 1 MAXVALUE 2147483647 CACHE 1 ),
    roomname character varying(50) COLLATE pg_catalog."default" NOT NULL,
    roomdesc VARCHAR(200),
	roomtype character varying(20) COLLATE pg_catalog."default" NOT NULL,
    roomcapacity integer DEFAULT 0,
    buildingid integer NOT NULL,
    CONSTRAINT room_pkey PRIMARY KEY (roomid),
    CONSTRAINT uq_roomname UNIQUE (roomname),
    CONSTRAINT fk_room_building FOREIGN KEY (buildingid)
        REFERENCES public.building (buildingid) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE NO ACTION,
    CONSTRAINT chk_roomcapacity CHECK (roomcapacity >= 0),
    CONSTRAINT chk_roomtype CHECK (roomtype::text = ANY (ARRAY['Lecture'::character varying::text, 'Laboratory'::character varying::text]))
);
-----------------------------------------------------------------------
--SCHEDULE
-- One row per subject + section + semester. Every Draft / Published / Archive version of
-- that subject shares this scheduleid (the app finds-or-creates it with
-- INSERT ... ON CONFLICT on uq_schedule_subject_section_semester).

CREATE TABLE IF NOT EXISTS public.schedule
(
    scheduleid integer NOT NULL GENERATED ALWAYS AS IDENTITY,
    curriculumsubjectid integer NOT NULL,
    sectionid integer NOT NULL,
    -- Mirrors the faculty of the current Published version (per-version faculty lives in
    -- schedule_version.employeenumber).
    employeenumber character varying(30),
    semesterid INT NOT NULL,
    datecreated timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT schedule_pkey PRIMARY KEY (scheduleid),
    CONSTRAINT fk_group_curriculumsubject FOREIGN KEY (curriculumsubjectid)
        REFERENCES public.curriculumsubject (curriculumsubjectid),
    CONSTRAINT fk_group_section FOREIGN KEY (sectionid)
        REFERENCES public.sections (sectionid),
    CONSTRAINT fk_group_faculty FOREIGN KEY (employeenumber)
        REFERENCES public.faculty (employeenumber),
    CONSTRAINT fk_group_semester FOREIGN KEY (semesterid)
        REFERENCES public.semester (semesterid),
    CONSTRAINT uq_schedule_subject_section_semester
        UNIQUE (curriculumsubjectid, sectionid, semesterid)
);
CREATE INDEX IF NOT EXISTS idx_schedule_section  ON public.schedule (sectionid);
CREATE INDEX IF NOT EXISTS idx_schedule_semester ON public.schedule (semesterid);
CREATE INDEX IF NOT EXISTS idx_schedule_faculty  ON public.schedule (employeenumber);
-----------------------------------------------------------------------
-- SCHEDULE VERSION
-- Official lifecycle: Draft -> Published -> Archive. There is NO 'Approved' version status
-- (Approve is the Publish action). Draft and Published may coexist for one subject; only a
-- successful Publish archives the previous Published version.
CREATE TABLE IF NOT EXISTS public.schedule_version
(
    versionid integer NOT NULL GENERATED ALWAYS AS IDENTITY,
    scheduleid integer NOT NULL,
    -- Batch stamp shared by every subject of one Publish/Save; counted across ALL sources.
    version_number integer NOT NULL,
    status character varying(20) NOT NULL,
    datecreated timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    original_status character varying(20),
    source character varying(50) DEFAULT 'official',      -- manual_editor | import | official
    employeenumber character varying(30),                 -- this version's own faculty
    is_incomplete boolean NOT NULL DEFAULT false,         -- Draft with unresolved components
    incomplete_components jsonb,
    -- A Draft that REMOVES its subject: is_removal = true and no schedule_sessions.
    -- Published stays live until a Publish consumes the marker.
    is_removal boolean NOT NULL DEFAULT false,
    CONSTRAINT schedule_version_pkey PRIMARY KEY (versionid),
    CONSTRAINT fk_version_group FOREIGN KEY (scheduleid)
        REFERENCES public.schedule (scheduleid)
        ON DELETE CASCADE,
    CONSTRAINT fk_version_faculty FOREIGN KEY (employeenumber)
        REFERENCES public.faculty (employeenumber),
    CONSTRAINT chk_schedule_status CHECK (status IN ('Published','Draft','Archive')),
    CONSTRAINT chk_version_number CHECK (version_number > 0),
	CONSTRAINT uq_schedule_version
        UNIQUE (scheduleid, version_number)
);
CREATE INDEX IF NOT EXISTS idx_scheduleversion_schedule ON public.schedule_version (scheduleid);
-- At most one Published and one Draft version per subject+section+semester.
CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_one_published
    ON public.schedule_version (scheduleid) WHERE status = 'Published';
CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_one_draft
    ON public.schedule_version (scheduleid) WHERE status = 'Draft';

-----------------------------------------------------------------------
-- SCHEDULE SESSIONS-- SCHEDULE SESSIONS
CREATE TABLE IF NOT EXISTS public.schedule_sessions
(
    sessionid integer NOT NULL GENERATED ALWAYS AS IDENTITY,
    versionid integer NOT NULL,
    daydesc character varying(10) NOT NULL,
    starttimeid integer NOT NULL,
    endtimeid integer NOT NULL,
    roomid integer,
    CONSTRAINT schedule_session_pkey PRIMARY KEY (sessionid),
    CONSTRAINT fk_session_version FOREIGN KEY (versionid)
        REFERENCES public.schedule_version (versionid)
        ON DELETE CASCADE,
    CONSTRAINT fk_session_room FOREIGN KEY (roomid)
        REFERENCES public.room (roomid),
    CONSTRAINT fk_session_starttime FOREIGN KEY (starttimeid)
        REFERENCES public.timeslot (timeid),
    CONSTRAINT fk_session_endtime FOREIGN KEY (endtimeid)
        REFERENCES public.timeslot (timeid),
    CONSTRAINT chk_session_timeorder CHECK (endtimeid > starttimeid),
    CONSTRAINT chk_session_daydesc CHECK (
        daydesc IN ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')
    )
);

-----------------------------------------------------------------------
-- Archiveschedule
CREATE TABLE IF NOT EXISTS public.historical_data
(
    historyid SERIAL PRIMARY KEY,
    schedid integer,
    "Instructor" character varying(150),
    "Subject Code" character varying(15),
    "Subject Name" character varying(150),
    "Lecture Hours" integer,
    "Laboratory Hours" integer,
    "Credit Units" integer,
    "Program" character varying(20),
    "Year Level" integer,
    "Hours" integer,
    "Day/s" character varying(50),
    "Time" character varying(100),
    "Room" character varying(100),
    semesterid integer,
    academicyearid character varying(9),
    archivedat timestamp DEFAULT CURRENT_TIMESTAMP
);
----------------------------------------------------------------------
-- makeup class request
-- Request lifecycle: Pending -> Approved | Rejected (reviewed by the Academic Head)
--                    Pending -> Cancelled (withdrawn by the submitting faculty; never reviewed).
-- An approved Make-up is ONE meeting on requested_date: it never creates an Official version
-- or a Local override.

CREATE TABLE IF NOT EXISTS public.class_meeting_request
(
    requestid        integer                     NOT NULL GENERATED ALWAYS AS IDENTITY,
    scheduleid       integer                     NOT NULL,
    requested_date   date                        NOT NULL,
    new_starttimeid  integer                     NOT NULL,
    new_endtimeid    integer                     NOT NULL,
    new_roomid       integer,
    reason           text                        NOT NULL,
    csp_flags        jsonb                       NOT NULL DEFAULT '{}'::jsonb,
    submitted_by     character varying(30)       NOT NULL,
    status           character varying(20)       NOT NULL DEFAULT 'Pending',
    reviewed_by      character varying(30),
    reviewed_at      timestamp without time zone,
    created_at       timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at       timestamp without time zone,
    decided_by       character varying(100),
    notes            text,
    remarks          text,

    CONSTRAINT makeupclass_request_pkey   PRIMARY KEY (requestid),

    CONSTRAINT fk_mkup_schedule           FOREIGN KEY (scheduleid)
        REFERENCES public.schedule (scheduleid),

    CONSTRAINT fk_mkup_starttime          FOREIGN KEY (new_starttimeid)
        REFERENCES public.timeslot (timeid),

    CONSTRAINT fk_mkup_endtime            FOREIGN KEY (new_endtimeid)
        REFERENCES public.timeslot (timeid),

    CONSTRAINT fk_mkup_room               FOREIGN KEY (new_roomid)
        REFERENCES public.room (roomid),

    CONSTRAINT fk_mkup_submitted_by       FOREIGN KEY (submitted_by)
        REFERENCES public.faculty (employeenumber),

    CONSTRAINT fk_mkup_reviewed_by        FOREIGN KEY (reviewed_by)
        REFERENCES public.faculty (employeenumber),

    CONSTRAINT chk_cmr_reason             CHECK (TRIM(reason) <> ''),
    CONSTRAINT chk_cmr_different_times    CHECK (new_starttimeid <> new_endtimeid),
    CONSTRAINT chk_cmr_status             CHECK (status IN ('Pending','Approved','Rejected','Cancelled')),
    CONSTRAINT chk_cmr_review             CHECK (
        (status IN ('Pending','Cancelled') AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (status IN ('Approved','Rejected') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);

-----------------------------------------------------------------------
-- Schedule_change_request
-- Schedule Adjustment request for ONE exact Official occurrence (official_sessionid).
-- Same request lifecycle as above. Approval publishes it into the section's Local
-- Published snapshot (local_arrangement); it never creates an Official version.
CREATE TABLE IF NOT EXISTS public.schedule_change_request
(
    requestid        integer                     NOT NULL GENERATED ALWAYS AS IDENTITY,
    scheduleid       integer                     NOT NULL,
    versionid        integer                     NOT NULL,
    change_type      character varying(30)       NOT NULL,
    new_daydesc      character varying(10),
    new_starttimeid  integer,
    new_endtimeid    integer,
    new_roomid       integer,
    effective_from   date                        NOT NULL,
    reason           text                        NOT NULL,
    has_hc_violation boolean                     NOT NULL DEFAULT false,
    submitted_by     character varying(30)       NOT NULL,
    status           character varying(20)       NOT NULL DEFAULT 'Pending',
    reviewed_by      character varying(30),
    reviewed_at      timestamp without time zone,
    created_at       timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at       timestamp without time zone,
    decided_by       character varying(100),
    end_date         date,
    remarks          text,
    official_sessionid integer,

    CONSTRAINT schedule_change_request_pkey  PRIMARY KEY (requestid),

    CONSTRAINT fk_scr_schedule               FOREIGN KEY (scheduleid)
        REFERENCES public.schedule (scheduleid),

    CONSTRAINT fk_scr_version                FOREIGN KEY (versionid)
        REFERENCES public.schedule_version (versionid),

    CONSTRAINT fk_scr_official_session       FOREIGN KEY (official_sessionid)
        REFERENCES public.schedule_sessions (sessionid),

    CONSTRAINT fk_scr_starttime              FOREIGN KEY (new_starttimeid)
        REFERENCES public.timeslot (timeid),

    CONSTRAINT fk_scr_endtime                FOREIGN KEY (new_endtimeid)
        REFERENCES public.timeslot (timeid),

    CONSTRAINT fk_scr_room                   FOREIGN KEY (new_roomid)
        REFERENCES public.room (roomid),

    CONSTRAINT fk_scr_submitted_by           FOREIGN KEY (submitted_by)
        REFERENCES public.faculty (employeenumber),

    CONSTRAINT fk_scr_reviewed_by            FOREIGN KEY (reviewed_by)
        REFERENCES public.faculty (employeenumber),

    CONSTRAINT chk_scr_change_type           CHECK (change_type IN (
        'Day','Time','Room','Day+Time','Day+Room','Time+Room','Day+Time+Room')),

    CONSTRAINT chk_scr_reason                CHECK (TRIM(reason) <> ''),

    CONSTRAINT chk_scr_status                CHECK (status IN ('Pending','Approved','Rejected','Cancelled')),

    CONSTRAINT chk_scr_review                CHECK (
        (status IN ('Pending','Cancelled') AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (status IN ('Approved','Rejected') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),

    CONSTRAINT chk_scr_daydesc               CHECK (
        new_daydesc IN ('Monday','Tuesday','Wednesday','Thursday',
                        'Friday','Saturday','Sunday') OR new_daydesc IS NULL)
);

-----------------------------------------------------------------------
-- Schedule_exception_log-- Schedule_exception_log

CREATE TABLE IF NOT EXISTS public.schedule_exception_log
(
    logid            integer                     NOT NULL GENERATED ALWAYS AS IDENTITY,
    source_type      character varying(30)       NOT NULL,
    source_requestid integer                     NOT NULL,
    scheduleid       integer                     NOT NULL,
    violated_rules   jsonb                       NOT NULL DEFAULT '[]'::jsonb,
    approved_by      character varying(30)       NOT NULL,
    approved_at      timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    semesterid       integer                     NOT NULL,
    notes            text,

    CONSTRAINT schedule_exception_log_pkey   PRIMARY KEY (logid),

    CONSTRAINT fk_sel_approved_by            FOREIGN KEY (approved_by)
        REFERENCES public.faculty (employeenumber),

    CONSTRAINT fk_sel_semester               FOREIGN KEY (semesterid)
        REFERENCES public.semester (semesterid),

    CONSTRAINT chk_sel_source_type           CHECK (source_type IN (
        'makeup_class', 'remedial_class','schedule_change', 'manual_edit' ))
);

-----------------------------------------------------------------------
-- Local_schedule_adjustment
-- LEGACY: not used by the application. Local overrides live in local_arrangement /
-- local_arrangement_sessions below. Kept only so older databases keep loading.

CREATE TABLE IF NOT EXISTS public.local_schedule_adjustment
(
    adjustmentid     integer                     NOT NULL GENERATED ALWAYS AS IDENTITY,
    source_type      character varying(30)       NOT NULL,
    source_requestid integer                     NOT NULL,
    scheduleid       integer                     NOT NULL,
    sessionid        integer,
    old_daydesc      character varying(10),
    old_starttimeid  integer,
    old_endtimeid    integer,
    old_roomid       integer,
    new_daydesc      character varying(10),
    new_starttimeid  integer,
    new_endtimeid    integer,
    new_roomid       integer,
    effective_from   date                        NOT NULL,
    effective_until  date,
    is_active        boolean                     NOT NULL DEFAULT true,
    created_at       timestamp without time zone DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT local_schedule_adjustment_pkey PRIMARY KEY (adjustmentid),

    CONSTRAINT fk_lsa_schedule               FOREIGN KEY (scheduleid)
        REFERENCES public.schedule (scheduleid),

    CONSTRAINT fk_lsa_session                FOREIGN KEY (sessionid)
        REFERENCES public.schedule_sessions (sessionid),

    CONSTRAINT fk_lsa_old_starttime          FOREIGN KEY (old_starttimeid)
        REFERENCES public.timeslot (timeid),
    CONSTRAINT fk_lsa_old_endtime            FOREIGN KEY (old_endtimeid)
        REFERENCES public.timeslot (timeid),
    CONSTRAINT fk_lsa_old_room               FOREIGN KEY (old_roomid)
        REFERENCES public.room (roomid),

    CONSTRAINT fk_lsa_new_starttime          FOREIGN KEY (new_starttimeid)
        REFERENCES public.timeslot (timeid),
    CONSTRAINT fk_lsa_new_endtime            FOREIGN KEY (new_endtimeid)
        REFERENCES public.timeslot (timeid),
    CONSTRAINT fk_lsa_new_room               FOREIGN KEY (new_roomid)
        REFERENCES public.room (roomid),

    CONSTRAINT chk_lsa_source_type           CHECK (source_type IN (
        'class_session','schedule_change'))
);

-----------------------------------------------------------------------
-- LOCAL SCHEDULER (override layer over the Published Official schedule)
-- Local lifecycle: Draft (editable, inactive) -> Published (the section's ONE active override
-- snapshot) -> Archived (immutable history). Local never writes schedule / schedule_version /
-- schedule_sessions.
-- Effective schedule = Official Published
--                      MINUS occurrences replaced by the active Local Published snapshot
--                      PLUS  its local_arrangement_sessions.
CREATE TABLE IF NOT EXISTS public.local_arrangement
(
    arrangementid    SERIAL PRIMARY KEY,
    description      character varying(200),
    programcode      character varying(20),
    yearlevel        integer,
    sectionid        integer,
    semesterid       integer,
    -- Snapshot/audit anchor: a versionid of the Official Published snapshot the overrides
    -- were made against. NOT the occurrence identity (see official_sessionid).
    ref_versionid    integer,
    has_hc_violation boolean DEFAULT false,
    violated_rules   jsonb DEFAULT '[]'::jsonb,
    override_reason  text,
    reason           text,
    is_active        boolean DEFAULT true,
    status           character varying(20) DEFAULT 'Draft',
    draft_fingerprint character varying(64),
    archive_reason   text,
    archived_at      timestamp without time zone,
    restored_from_arrangementid integer,
    created_by       character varying(100),
    created_at       timestamp without time zone DEFAULT now(),
    updated_at       timestamp without time zone DEFAULT now(),
    CONSTRAINT local_arrangement_sectionid_fkey FOREIGN KEY (sectionid)
        REFERENCES public.sections (sectionid),
    CONSTRAINT fk_local_arrangement_semester FOREIGN KEY (semesterid)
        REFERENCES public.semester (semesterid),
    CONSTRAINT local_arrangement_restored_from_arrangementid_fkey FOREIGN KEY (restored_from_arrangementid)
        REFERENCES public.local_arrangement (arrangementid),
    CONSTRAINT chk_local_arrangement_status CHECK (status IN ('Draft','Published','Archived'))
);
CREATE INDEX IF NOT EXISTS idx_local_arrangement_section_context
    ON public.local_arrangement (programcode, yearlevel, sectionid, semesterid, status, is_active);
-- Identical Draft submissions collapse into one Draft.
CREATE UNIQUE INDEX IF NOT EXISTS uq_local_active_draft_fingerprint
    ON public.local_arrangement (draft_fingerprint)
    WHERE status = 'Draft' AND draft_fingerprint IS NOT NULL;
-- ONE active Published Local snapshot per section scope.
CREATE UNIQUE INDEX IF NOT EXISTS uq_local_one_active_published_scope
    ON public.local_arrangement (UPPER(programcode), yearlevel, sectionid, semesterid)
    WHERE status = 'Published' AND is_active = true;

CREATE TABLE IF NOT EXISTS public.local_arrangement_sessions
(
    sessionid              SERIAL PRIMARY KEY,
    arrangementid          integer,
    subjectcode            character varying(50),
    daydesc                character varying(20),
    starttimeid            integer,
    endtimeid              integer,
    roomid                 integer,
    faculty_employeenumber character varying(50),
    -- Authoritative link: the exact Official occurrence this Local session replaces.
    official_sessionid     integer,
    CONSTRAINT local_arrangement_sessions_arrangementid_fkey FOREIGN KEY (arrangementid)
        REFERENCES public.local_arrangement (arrangementid),
    CONSTRAINT local_arrangement_sessions_official_sessionid_fkey FOREIGN KEY (official_sessionid)
        REFERENCES public.schedule_sessions (sessionid)
);
CREATE INDEX IF NOT EXISTS idx_local_session_official_source
    ON public.local_arrangement_sessions (official_sessionid);

-- LEGACY: created by older versions; no longer written or read by the application.
CREATE TABLE IF NOT EXISTS public.local_displaced_subjects
(
    id            SERIAL PRIMARY KEY,
    programcode   character varying(20) NOT NULL,
    yearlevel     integer NOT NULL,
    sectionid     integer,
    semesterid    integer NOT NULL,
    subjectcode   character varying(50) NOT NULL,
    displaced_by  integer,
    is_active     boolean DEFAULT true,
    displaced_at  timestamp without time zone DEFAULT now(),
    CONSTRAINT local_displaced_subjects_programcode_yearlevel_semesterid_s_key
        UNIQUE (programcode, yearlevel, semesterid, subjectcode),
    CONSTRAINT local_displaced_subjects_displaced_by_fkey FOREIGN KEY (displaced_by)
        REFERENCES public.local_arrangement (arrangementid),
    CONSTRAINT local_displaced_subjects_sectionid_fkey FOREIGN KEY (sectionid)
        REFERENCES public.sections (sectionid)
);
CREATE INDEX IF NOT EXISTS idx_local_displaced_section_context
    ON public.local_displaced_subjects (programcode, yearlevel, sectionid, semesterid, subjectcode, is_active);

-- Archived Local history is immutable; a Published Local is never reverted to Draft in place.
CREATE OR REPLACE FUNCTION public.guard_local_arrangement_history()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND OLD.status = 'Archived' THEN
        RAISE EXCEPTION 'Archived Local Arrangement history is immutable';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.status = 'Archived' THEN
        RAISE EXCEPTION 'Archived Local Arrangement history is immutable';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.status = 'Published' AND NEW.status = 'Draft' THEN
        RAISE EXCEPTION 'Published Local Arrangement cannot be reverted in place';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_local_archive_immutable ON public.local_arrangement;
CREATE TRIGGER trg_local_archive_immutable
    BEFORE UPDATE OR DELETE ON public.local_arrangement
    FOR EACH ROW EXECUTE FUNCTION public.guard_local_arrangement_history();

CREATE OR REPLACE FUNCTION public.guard_local_archived_sessions()
RETURNS trigger AS $$
DECLARE parent_status VARCHAR(20);
BEGIN
    SELECT status INTO parent_status
    FROM public.local_arrangement
    WHERE arrangementid = OLD.arrangementid;
    IF parent_status = 'Archived' THEN
        RAISE EXCEPTION 'Sessions belonging to Archived Local history are immutable';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_local_archived_sessions_immutable ON public.local_arrangement_sessions;
CREATE TRIGGER trg_local_archived_sessions_immutable
    BEFORE UPDATE OR DELETE ON public.local_arrangement_sessions
    FOR EACH ROW EXECUTE FUNCTION public.guard_local_archived_sessions();
----------------------------------------------------------------------
-- CURRICULUM VIEW-
CREATE OR REPLACE VIEW public.curriculum_view AS
SELECT
    c.curriculumcode,
    c.curriculumid,
    cs.curriculumsubjectid,
    cs.subjectcode,
    cs.prerequisite AS "Prerequisite",
    cs.corequisite AS "Co-requisite",
    cs.subjectname AS "Description",
    cs.lecturehours,
    cs.laboratoryhours,
    cs.creditunits,
    cs.tuitionhours,
    (c.programcode || '-' || cs.yearlevel) AS programyearlevel,
    cs.yearlevel,
    cs.semester
FROM curriculumsubject cs
INNER JOIN curriculum c
    ON cs.curriculumid = c.curriculumid;
-----------------------------------------------------------------------
-- ACAD YEAR AND SEMESTER DATE -
CREATE OR REPLACE VIEW vw_academic_year_semesters AS
SELECT
    ay.academicyearid,

    -- 1st Semester (A)
    MAX(CASE WHEN s.semestertype = 'A' THEN s.semstartdate END) AS "1st Sem Start",
    MAX(CASE WHEN s.semestertype = 'A' THEN s.semenddate END)   AS "1st Sem End",

    -- 2nd Semester (B)
    MAX(CASE WHEN s.semestertype = 'B' THEN s.semstartdate END) AS "2nd Sem Start",
    MAX(CASE WHEN s.semestertype = 'B' THEN s.semenddate END)   AS "2nd Sem End",

    -- 3rd Semester (C)
    MAX(CASE WHEN s.semestertype = 'C' THEN s.semstartdate END) AS "3rd Sem Start",
    MAX(CASE WHEN s.semestertype = 'C' THEN s.semenddate END)   AS "3rd Sem End"

FROM public.academicyear ay
LEFT JOIN public.semester s
    ON ay.academicyearid = s.academicyearid
GROUP BY ay.academicyearid;

-----------------------------------------------------------------------
--DCL--
-----------------------------------------------------------------------
-- PROGRAMS
INSERT INTO public.programs
(programcode, programname, programtype, isactive, numyearlevel)
VALUES
('BSA',      'Bachelor of Science in Accountancy',                                                   'Undergraduate', TRUE, 4),
('BSAM',     'Bachelor of Science in Agricultural Management',                                        'Undergraduate', TRUE, 4),
('BSIT',     'Bachelor of Science in Information Technology',                                         'Undergraduate', TRUE, 4),
('BSND',     'Bachelor of Science in Nutrition and Dietetics',                                        'Undergraduate', TRUE, 4),
('DIT',      'Diploma in Information Technology',                                                     'Diploma',       TRUE, 3),
('DOMT-MOM', 'Diploma in Office Management Technology - Marketing Operations Management',             'Diploma',       TRUE, 3),
('BSED-MT',  'Bachelor of Secondary Education major in Mathematics',                                  'Undergraduate', TRUE, 4),
('DCPET',    'Diploma in Computer Engineering Technology',                                            'Diploma',       TRUE, 3),
('BSBIO-AT', 'Bachelor of Science in Biology - Animal Technology',                                    'Undergraduate', TRUE, 4),
('BSBIO-PT', 'Bachelor of Science in Biology - Plant Technology',                                     'Undergraduate', TRUE, 4),
('BSBA-FM',  'Bachelor of Science in Business Administration major in Financial Management',          'Undergraduate', TRUE, 4),
('DCVET',    'Diploma in Civil Engineering Technology',                                               'Diploma',       TRUE, 3),
('BSBIO',    'Bachelor of Science in Biology',                                                        'Undergraduate', TRUE, 4),
('DEET',     'Diploma in Electrical Engineering Technology',                                          'Diploma',       TRUE, 3),
('DOMT-LOM', 'Diploma in Office Management Technology - Logistics Operations Management',             'Diploma',       TRUE, 3),
('DOMT',     'Diploma in Office Management Technology',                                               'Diploma',       TRUE, 3),
('BSARCH',   'Bachelor of Science in Architecture',                                                   'Undergraduate', TRUE, 5),
('BSED',     'Bachelor of Secondary Education',                                                       'Undergraduate', TRUE, 4),
('BEED',     'Bachelor of Elementary Education',                                                      'Undergraduate', TRUE, 4),
('BSCE',     'Bachelor of Science in Civil Engineering',                                              'Undergraduate', TRUE, 4),
('BSBA-MM',  'Bachelor of Science in Business Administration major in Marketing Management',          'Undergraduate', TRUE, 4),
('BSBA',     'Bachelor of Science in Business Administration',                                        'Undergraduate', TRUE, 4),
('BSEE',     'Bachelor of Science in Electrical Engineering',                                         'Undergraduate', TRUE, 4),
('BPA',      'Bachelor of Public Administration',                                                     'Undergraduate', TRUE, 4),
('BPA-FA',   'Bachelor of Public Administration with Specialization in Financial Administration',     'Undergraduate', TRUE, 4),
('BPA-PFM',  'Bachelor of Public Administration major in Public Financial Management',                'Undergraduate', TRUE, 4),
('BSHM',     'Bachelor of Science in Hospitality Management',                                         'Undergraduate', TRUE, 4),
('BSOA-LOA', 'Bachelor of Science in Office Administration with Specialization in Legal Office Administration', 'Undergraduate', TRUE, 4),
('BSOA',     'Bachelor of Science in Office Administration',                                          'Undergraduate', TRUE, 4);
-----------------------------------------------------------------------
-- DESIGNATION
INSERT INTO designation (designationname, regularloadunit, nightteachingservice)
VALUES
('Campus Director', 3, 0),
('Academic Head', 6, 0),
('Campus Registrar', 6, 0),
('Property Custodian', 6, 2),
('CSS Office Head', 6, 2),
('Collecting and Disbursing Officer', 9, 2),
('Office Directors', 3, 2),
('Section Chiefs', 6, 2),
('QA Coordinator', 9, 2),
('Sports Coordinator', 9, 2),
('OJT Coordinator', 9, 2),
('ICT Coordinator', 9, 2),
('Laboratory Head', 9, 0),
('Laboratory Technician', 9, 2);
-----------------------------------------------------------------------
INSERT INTO employeetype (typename, regularload, parttimeload, teachingsubstitution, regular_start, regular_end, parttime_start, parttime_end )
VALUES
('Regular', 15, 12, 13, '07:30:00', '16:30:00', '04:30:00', '21:00:00'),
('Part-time', NULL, 15, 25, NULL, NULL, '04:30:00', '21:00:00'),
('Designee', 9, 12, 19, '07:30:00', '16:30:00', '04:30:00', '18:00:00');
-----------------------------------------------------------------------
-- BUILDING
INSERT INTO Building (BuildingName, IsActive)
VALUES
('Kitchen Lab', TRUE),
('HM Lab', TRUE),
('Tissue Lab', TRUE),
('ICTLAB 1', TRUE),
('ICTLAB 2', TRUE),
('Health&Science', TRUE),
('Engineering', TRUE),
('Education', TRUE),
('Nantes', TRUE),
('Grandstand', TRUE),
('Guest House', TRUE),
('Gymnasium', TRUE),
('Quadrangle', TRUE);
-----------------------------------------------------------------------
-- ROOMS
INSERT INTO Room (RoomName, RoomDesc, RoomType, RoomCapacity, BuildingID)
VALUES
('LQ100','Kitchen Lab','Laboratory',40,1),
('LQ101','Beverage Lab','Laboratory',40,2),
('LQ102','Tissue Lab','Laboratory',40,3),
('LQ103','ICTLAB1','Laboratory',40,4),
('LQ104','ICTLAB2','Laboratory',40,5),
('LQ105','FOOD LAB','Laboratory',40,6),
('LQ106',NULL,'Lecture',40,6),
('LQ107',NULL,'Lecture',40,6),
('LQ108',NULL,'Lecture',40,6),
('LQ109',NULL,'Lecture',40,7),
('LQ110',NULL,'Lecture',40,7),
('LQ111','EE LAB','Laboratory',40,7),
('LQ112','CE LAB','Laboratory',40,7),
('LQ113',NULL,'Lecture',40,7),
('LQ114',NULL,'Lecture',40,7),
('LQ115',NULL,'Lecture',40,7),
('LQ116',NULL,'Lecture',40,7),
('LQ117',NULL,'Lecture',40,8),
('LQ118',NULL,'Lecture',40,8),
('LQ119','EDTECH','Laboratory',40,8),
('LQ120',NULL,'Lecture',40,9),
('LQ121',NULL,'Lecture',40,9),
('LQ122',NULL,'Lecture',40,9),
('LQ200',NULL,'Lecture',40,9),
('LQ201',NULL,'Lecture',40,9),
('LQ202',NULL,'Lecture',40,9),
('LQ203',NULL,'Lecture',40,6),
('LQ204',NULL,'Lecture',40,6),
('LQ205','PHYS LAB','Laboratory',40,6),
('LQ206','CHEM LAB','Laboratory',40,6),
('LQ207','ICT LAB 3','Laboratory',40,7),
('LQ208','DRAFT LAB','Laboratory',40,7),
('LQ209 A','CEA FUNC RM','Laboratory',40,7),
('LQ209 B','CEA FUNC RM','Laboratory',40,7),
('LQ210',NULL,'Lecture',40,7),
('LQ211',NULL,'Lecture',40,7),
('LQ212',NULL,'Lecture',40,8),
('LQ213',NULL,'Lecture',40,8),
('LQ214',NULL,'Lecture',40,8),
('LQ215',NULL,'Lecture',40,8),
('LQ216','SPEECH LAB','Laboratory',40,8),
('LQ217','KEYBOAR','Laboratory',40,8),
('LQ218',NULL,'Laboratory',40,8),
('PUP GYM',NULL,'Lecture',60,12),
('LQ-QUAD',NULL,'Lecture',60,13);
-----------------------------------------------------------------------
-- TIME SLOT
INSERT INTO timeslot (TimeValue) VALUES
('07:30:00'),
('08:00:00'),
('08:30:00'),
('09:00:00'),
('09:30:00'),
('10:00:00'),
('10:30:00'),
('11:00:00'),
('11:30:00'),
('12:00:00'),
('12:30:00'),
('13:00:00'),
('13:30:00'),
('14:00:00'),
('14:30:00'),
('15:00:00'),
('15:30:00'),
('16:00:00'),
('16:30:00'),
('17:00:00'),
('17:30:00'),
('18:00:00'),
('18:30:00'),
('19:00:00'),
('19:30:00'),
('20:00:00'),
('20:30:00'),
('21:00:00'),
('21:30:00');
-----------------------------------------------------------------------
-- ACADEMIC YEAR
INSERT INTO AcademicYear (Academicyearid, yearstart, yearend, isactive) VALUES
('AY2526', 2025, 2026, TRUE),
('AY2425', 2024, 2025, TRUE),
('AY2324', 2023, 2024, TRUE),
('AY2223', 2022, 2023, TRUE),
('AY2122', 2021, 2022, TRUE);
-----------------------------------------------------------------------
INSERT INTO Semester (academicyearid, semestertype, semstartdate, semenddate, isactive) VALUES
-- AY 25-26
('AY2526', 'A', '2025-09-01', '2026-01-17', FALSE),
('AY2526', 'B', '2026-02-09', '2026-06-21', FALSE),
('AY2526', 'C', '2026-06-29', '2026-08-08', FALSE),
-- AY 24-25
('AY2425', 'A', '2024-09-09', '2025-01-28', FALSE),
('AY2425', 'B', '2025-02-17', '2025-06-29', FALSE),
('AY2425', 'C', '2025-07-14', '2025-08-28', FALSE),
-- AY 23-24
('AY2324', 'A', '2023-09-25', '2024-02-11', FALSE),
('AY2324', 'B', '2024-02-28', '2024-07-14', FALSE),
('AY2324', 'C', '2024-07-22', '2024-09-02', FALSE),
-- AY 22-23
('AY2223', 'A', '2022-10-10', '2023-02-26', FALSE),
('AY2223', 'B', '2023-03-20', '2023-07-30', FALSE),
('AY2223', 'C', '2023-08-02', '2023-09-11', FALSE),
-- AY 21-22
('AY2122', 'A', '2021-10-11', '2022-03-05', FALSE),
('AY2122', 'B', '2022-03-28', '2022-07-30', FALSE),
('AY2122', 'C', '2022-08-04', '2022-09-13', FALSE);
