--
-- PostgreSQL database dump
--

\restrict LzhUy6hBnCKXULuahbRzLMfjTfH0EQj33tN01wK4UqF9HWcueyt6vbcXeSHAtYz

-- Dumped from database version 18.3
-- Dumped by pg_dump version 18.3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: academicyear; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.academicyear (
    academicyearid character varying(9) NOT NULL,
    yearstart integer NOT NULL,
    yearend integer NOT NULL,
    startdate date,
    enddate date,
    isactive boolean DEFAULT false,
    isfinalized boolean DEFAULT false NOT NULL,
    status character varying(20) DEFAULT 'Upcoming'::character varying,
    CONSTRAINT chk_academicyear_status CHECK (((status)::text = ANY ((ARRAY['Upcoming'::character varying, 'Current'::character varying, 'Past'::character varying, 'Finalized'::character varying])::text[])))
);


--
-- Name: accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounts (
    userid integer NOT NULL,
    username character varying(50) NOT NULL,
    passwordhash character varying(255) NOT NULL,
    isactive boolean DEFAULT true NOT NULL,
    datecreated timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    role character varying(20) NOT NULL,
    employeenumber character varying(30),
    last_login timestamp without time zone,
    profile_photo character varying(255),
    CONSTRAINT chk_accountrole CHECK (((role)::text = ANY (ARRAY[('Admin'::character varying)::text, ('Faculty'::character varying)::text, ('Academic Head'::character varying)::text])))
);


--
-- Name: accounts_userid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.accounts ALTER COLUMN userid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.accounts_userid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: activity_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.activity_log (
    logid integer NOT NULL,
    logtime timestamp without time zone DEFAULT now(),
    action character varying(100) NOT NULL,
    details text,
    initiated_by character varying(100),
    category character varying(50) DEFAULT 'system'::character varying,
    log_color character varying(20) DEFAULT 'gray'::character varying
);


--
-- Name: activity_log_logid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.activity_log_logid_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: activity_log_logid_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.activity_log_logid_seq OWNED BY public.activity_log.logid;


--
-- Name: building; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.building (
    buildingid integer NOT NULL,
    buildingname character varying(100) NOT NULL,
    isactive boolean DEFAULT true NOT NULL
);


--
-- Name: building_buildingid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.building ALTER COLUMN buildingid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.building_buildingid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: class_meeting_request; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.class_meeting_request (
    requestid integer NOT NULL,
    scheduleid integer NOT NULL,
    requested_date date NOT NULL,
    new_starttimeid integer NOT NULL,
    new_endtimeid integer NOT NULL,
    new_roomid integer,
    reason text,
    csp_flags jsonb DEFAULT '{}'::jsonb NOT NULL,
    submitted_by character varying(30) NOT NULL,
    status character varying(20) DEFAULT 'Pending'::character varying NOT NULL,
    reviewed_by character varying(30),
    reviewed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    decided_by character varying(100),
    decided_at timestamp without time zone,
    remarks text,
    notes text,
    CONSTRAINT chk_mkup_status CHECK (((status)::text = ANY ((ARRAY['Pending'::character varying, 'Approved'::character varying, 'Rejected'::character varying])::text[])))
);


--
-- Name: class_meeting_request_requestid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.class_meeting_request ALTER COLUMN requestid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.class_meeting_request_requestid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: curriculum; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.curriculum (
    curriculumid integer NOT NULL,
    curriculumcode character varying(6) NOT NULL,
    programcode character varying(10) NOT NULL,
    curriculumyear character varying(9) NOT NULL,
    curriculumversion integer,
    isactive boolean DEFAULT true,
    curriculumtype character varying(10) DEFAULT 'Regular'::character varying NOT NULL
);


--
-- Name: curriculum_curriculumid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.curriculum ALTER COLUMN curriculumid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.curriculum_curriculumid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: curriculumsubject; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.curriculumsubject (
    curriculumsubjectid integer NOT NULL,
    curriculumid integer NOT NULL,
    subjectcode character varying(15) NOT NULL,
    subjectname character varying(150) NOT NULL,
    lecturehours integer DEFAULT 0 NOT NULL,
    laboratoryhours integer DEFAULT 0 NOT NULL,
    creditunits integer DEFAULT 0 NOT NULL,
    tuitionhours integer DEFAULT 0 NOT NULL,
    prerequisite character varying(255),
    corequisite character varying(255),
    yearlevel integer NOT NULL,
    semester character varying(10) NOT NULL,
    isshared boolean DEFAULT false,
    isbridging boolean DEFAULT false NOT NULL,
    CONSTRAINT chk_curriculumsubject_semester CHECK (((semester)::text = ANY ((ARRAY['A'::character varying, 'B'::character varying, 'C'::character varying])::text[]))),
    CONSTRAINT chk_curriculumsubject_yearlevel CHECK (((yearlevel >= 1) AND (yearlevel <= 5)))
);


--
-- Name: curriculum_view; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.curriculum_view AS
 SELECT c.curriculumcode,
    c.curriculumid,
    cs.curriculumsubjectid,
    cs.subjectcode,
    cs.prerequisite AS "Prerequisite",
    cs.corequisite AS "Co-requisite",
    cs.subjectname,
    cs.lecturehours,
    cs.laboratoryhours,
    cs.creditunits,
    cs.tuitionhours,
    (((c.programcode)::text || '-'::text) || (cs.yearlevel)::text) AS programyearlevel,
    cs.yearlevel,
    cs.semester
   FROM (public.curriculumsubject cs
     JOIN public.curriculum c ON ((cs.curriculumid = c.curriculumid)));


--
-- Name: curriculumsubject_curriculumsubjectid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.curriculumsubject ALTER COLUMN curriculumsubjectid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.curriculumsubject_curriculumsubjectid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: designation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.designation (
    designationid integer NOT NULL,
    designationname character varying(100) NOT NULL,
    regularloadunit integer,
    nightteachingservice integer
);


--
-- Name: designation_designationid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.designation ALTER COLUMN designationid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.designation_designationid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employeetype; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employeetype (
    employeetypeid integer NOT NULL,
    typename character varying(50) NOT NULL,
    regularload integer,
    parttimeload integer,
    teachingsubstitution integer,
    regular_start time without time zone,
    regular_end time without time zone,
    parttime_start time without time zone,
    parttime_end time without time zone,
    restrict_pt_hours boolean DEFAULT true NOT NULL
);


--
-- Name: employeetype_employeetypeid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.employeetype ALTER COLUMN employeetypeid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.employeetype_employeetypeid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: faculty; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.faculty (
    employeenumber character varying(30) NOT NULL,
    firstname character varying(50) NOT NULL,
    middlename character varying(50),
    lastname character varying(50) NOT NULL,
    email character varying(100) NOT NULL,
    contactnumber character varying(15) NOT NULL,
    specializationid integer NOT NULL,
    employeetypeid integer NOT NULL,
    designationid integer,
    employeestatus character varying(20) NOT NULL,
    CONSTRAINT chk_employeestatus CHECK (((employeestatus)::text = ANY (ARRAY[('Temporary'::character varying)::text, ('Permanent'::character varying)::text, ('Part-Time'::character varying)::text])))
);


--
-- Name: faculty_archive; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.faculty_archive (
    facultyarchiveid integer NOT NULL,
    employeenumber character varying(30),
    firstname character varying(50),
    middlename character varying(50),
    lastname character varying(50),
    email character varying(100),
    contactnumber character varying(15),
    specializationid integer,
    employeetypeid integer,
    designationid integer,
    employeestatus character varying(20),
    archivedat timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: faculty_archive_facultyarchiveid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.faculty_archive ALTER COLUMN facultyarchiveid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.faculty_archive_facultyarchiveid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: historical_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.historical_data (
    historyid integer NOT NULL,
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
    archivedat timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    employeenumber character varying(50)
);


--
-- Name: historical_data_historyid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.historical_data_historyid_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: historical_data_historyid_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.historical_data_historyid_seq OWNED BY public.historical_data.historyid;


--
-- Name: local_arrangement; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.local_arrangement (
    arrangementid integer NOT NULL,
    description character varying(200),
    programcode character varying(20),
    yearlevel integer,
    semesterid integer,
    ref_versionid integer,
    has_hc_violation boolean DEFAULT false,
    violated_rules jsonb DEFAULT '[]'::jsonb,
    override_reason text,
    is_active boolean DEFAULT true,
    created_by character varying(100),
    created_at timestamp without time zone DEFAULT now(),
    status character varying(20) DEFAULT 'Draft'::character varying
);


--
-- Name: local_arrangement_arrangementid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.local_arrangement_arrangementid_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: local_arrangement_arrangementid_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.local_arrangement_arrangementid_seq OWNED BY public.local_arrangement.arrangementid;


--
-- Name: local_arrangement_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.local_arrangement_sessions (
    sessionid integer NOT NULL,
    arrangementid integer,
    subjectcode character varying(50),
    daydesc character varying(20),
    starttimeid integer,
    endtimeid integer,
    roomid integer,
    faculty_employeenumber character varying(50)
);


--
-- Name: local_arrangement_sessions_sessionid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.local_arrangement_sessions_sessionid_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: local_arrangement_sessions_sessionid_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.local_arrangement_sessions_sessionid_seq OWNED BY public.local_arrangement_sessions.sessionid;


--
-- Name: local_displaced_subjects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.local_displaced_subjects (
    id integer NOT NULL,
    programcode character varying(20) NOT NULL,
    yearlevel integer NOT NULL,
    semesterid integer NOT NULL,
    subjectcode character varying(50) NOT NULL,
    displaced_by integer,
    is_active boolean DEFAULT true,
    displaced_at timestamp without time zone DEFAULT now()
);


--
-- Name: local_displaced_subjects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.local_displaced_subjects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: local_displaced_subjects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.local_displaced_subjects_id_seq OWNED BY public.local_displaced_subjects.id;


--
-- Name: local_schedule_adjustment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.local_schedule_adjustment (
    adjustmentid integer NOT NULL,
    source_type character varying(30) NOT NULL,
    source_requestid integer NOT NULL,
    scheduleid integer NOT NULL,
    sessionid integer,
    old_daydesc character varying(10),
    old_starttimeid integer,
    old_endtimeid integer,
    old_roomid integer,
    new_daydesc character varying(10),
    new_starttimeid integer,
    new_endtimeid integer,
    new_roomid integer,
    effective_from date NOT NULL,
    effective_until date,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_lsa_source_type CHECK (((source_type)::text = ANY ((ARRAY['class_session'::character varying, 'schedule_change'::character varying])::text[])))
);


--
-- Name: local_schedule_adjustment_adjustmentid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.local_schedule_adjustment ALTER COLUMN adjustmentid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.local_schedule_adjustment_adjustmentid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: merge_load_policy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.merge_load_policy (
    policyid integer NOT NULL,
    subjectcode character varying(50) NOT NULL,
    min_sections integer NOT NULL,
    max_sections integer NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: merge_load_policy_policyid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.merge_load_policy_policyid_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: merge_load_policy_policyid_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.merge_load_policy_policyid_seq OWNED BY public.merge_load_policy.policyid;


--
-- Name: mergedclass; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mergedclass (
    mergedclassid integer NOT NULL,
    curriculumsubjectid integer NOT NULL,
    semesterid integer NOT NULL,
    employeenumber character varying(30),
    classname character varying(100),
    isactive boolean DEFAULT true,
    datecreated timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: mergedclass_mergedclassid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.mergedclass ALTER COLUMN mergedclassid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.mergedclass_mergedclassid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: mergedclass_sections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mergedclass_sections (
    mergedclasssectionid integer NOT NULL,
    mergedclassid integer NOT NULL,
    sectionid integer NOT NULL
);


--
-- Name: mergedclass_sections_mergedclasssectionid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.mergedclass_sections ALTER COLUMN mergedclasssectionid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.mergedclass_sections_mergedclasssectionid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: program_name_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.program_name_history (
    id integer NOT NULL,
    programcode character varying(20) NOT NULL,
    old_name character varying(200) NOT NULL,
    new_name character varying(200) NOT NULL,
    changed_by character varying(100),
    changed_at timestamp without time zone DEFAULT now()
);


--
-- Name: program_name_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.program_name_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: program_name_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.program_name_history_id_seq OWNED BY public.program_name_history.id;


--
-- Name: program_yearlevel; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.program_yearlevel (
    programyearlevelid integer NOT NULL,
    programcode character varying(10) NOT NULL,
    academicyearid character varying(9) NOT NULL,
    startacademicyear character varying(9) NOT NULL,
    yearlevel integer NOT NULL,
    curriculumid integer,
    isactive boolean DEFAULT true,
    remarks character varying(255),
    section_naming_format character varying(30),
    CONSTRAINT chk_pyl_yearlevel CHECK (((yearlevel >= 1) AND (yearlevel <= 5)))
);


--
-- Name: program_yearlevel_programyearlevelid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.program_yearlevel ALTER COLUMN programyearlevelid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.program_yearlevel_programyearlevelid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: programs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.programs (
    programcode character varying(10) NOT NULL,
    programname character varying(100) NOT NULL,
    programtype character varying(20) NOT NULL,
    isactive boolean DEFAULT true NOT NULL,
    numyearlevel integer,
    CONSTRAINT chk_programtype CHECK (((programtype)::text = ANY (ARRAY[('Undergraduate'::character varying)::text, ('Diploma'::character varying)::text])))
);


--
-- Name: room; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.room (
    roomid integer NOT NULL,
    roomname character varying(50) NOT NULL,
    roomdesc character varying(200),
    roomtype character varying(20) NOT NULL,
    roomcapacity integer DEFAULT 0,
    buildingid integer NOT NULL,
    CONSTRAINT chk_roomcapacity CHECK ((roomcapacity >= 0)),
    CONSTRAINT chk_roomtype CHECK (((roomtype)::text = ANY (ARRAY[('Lecture'::character varying)::text, ('Laboratory'::character varying)::text])))
);


--
-- Name: room_roomid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.room ALTER COLUMN roomid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.room_roomid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: schedule; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule (
    scheduleid integer NOT NULL,
    curriculumsubjectid integer NOT NULL,
    sectionid integer NOT NULL,
    employeenumber character varying(30),
    semesterid integer NOT NULL,
    datecreated timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: schedule_change_request; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_change_request (
    requestid integer NOT NULL,
    scheduleid integer NOT NULL,
    versionid integer NOT NULL,
    change_type character varying(30) NOT NULL,
    new_daydesc character varying(10),
    new_starttimeid integer,
    new_endtimeid integer,
    new_roomid integer,
    effective_from date NOT NULL,
    reason text,
    has_hc_violation boolean DEFAULT false NOT NULL,
    submitted_by character varying(30) NOT NULL,
    status character varying(20) DEFAULT 'Pending'::character varying NOT NULL,
    reviewed_by character varying(30),
    reviewed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    decided_by character varying(100),
    decided_at timestamp without time zone,
    remarks text,
    end_date date,
    CONSTRAINT chk_scr_change_type CHECK (((change_type)::text = ANY ((ARRAY['Day'::character varying, 'Time'::character varying, 'Room'::character varying, 'Day+Time'::character varying, 'Day+Room'::character varying, 'Time+Room'::character varying, 'Day+Time+Room'::character varying])::text[]))),
    CONSTRAINT chk_scr_daydesc CHECK ((((new_daydesc)::text = ANY ((ARRAY['Monday'::character varying, 'Tuesday'::character varying, 'Wednesday'::character varying, 'Thursday'::character varying, 'Friday'::character varying, 'Saturday'::character varying, 'Sunday'::character varying])::text[])) OR (new_daydesc IS NULL))),
    CONSTRAINT chk_scr_status CHECK (((status)::text = ANY ((ARRAY['Pending'::character varying, 'Approved'::character varying, 'Rejected'::character varying])::text[])))
);


--
-- Name: schedule_change_request_requestid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.schedule_change_request ALTER COLUMN requestid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.schedule_change_request_requestid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: schedule_exception_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_exception_log (
    logid integer NOT NULL,
    source_type character varying(30) NOT NULL,
    source_requestid integer NOT NULL,
    scheduleid integer NOT NULL,
    violated_rules jsonb DEFAULT '[]'::jsonb NOT NULL,
    approved_by character varying(30) NOT NULL,
    approved_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    semesterid integer NOT NULL,
    notes text,
    CONSTRAINT chk_sel_source_type CHECK (((source_type)::text = ANY ((ARRAY['makeup_class'::character varying, 'remedial_class'::character varying, 'schedule_change'::character varying, 'manual_edit'::character varying])::text[])))
);


--
-- Name: schedule_exception_log_logid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.schedule_exception_log ALTER COLUMN logid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.schedule_exception_log_logid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: schedule_scheduleid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.schedule ALTER COLUMN scheduleid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.schedule_scheduleid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: schedule_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_sessions (
    sessionid integer NOT NULL,
    versionid integer NOT NULL,
    daydesc character varying(10) NOT NULL,
    starttimeid integer NOT NULL,
    endtimeid integer NOT NULL,
    roomid integer,
    CONSTRAINT chk_session_daydesc CHECK (((daydesc)::text = ANY ((ARRAY['Monday'::character varying, 'Tuesday'::character varying, 'Wednesday'::character varying, 'Thursday'::character varying, 'Friday'::character varying, 'Saturday'::character varying, 'Sunday'::character varying])::text[]))),
    CONSTRAINT chk_session_timeorder CHECK ((endtimeid > starttimeid))
);


--
-- Name: schedule_sessions_sessionid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.schedule_sessions ALTER COLUMN sessionid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.schedule_sessions_sessionid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: schedule_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_version (
    versionid integer NOT NULL,
    scheduleid integer NOT NULL,
    version_number integer NOT NULL,
    status character varying(20) NOT NULL,
    datecreated timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    source character varying(50) DEFAULT 'official'::character varying,
    original_status character varying(20),
    employeenumber character varying(30),
    is_incomplete boolean DEFAULT false NOT NULL,
    incomplete_components jsonb,
    CONSTRAINT chk_schedule_status CHECK (((status)::text = ANY ((ARRAY['Published'::character varying, 'Draft'::character varying, 'Archive'::character varying, 'Approved'::character varying])::text[])))
);


--
-- Name: schedule_version_versionid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.schedule_version ALTER COLUMN versionid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.schedule_version_versionid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: scheduler_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scheduler_config (
    config_key character varying(60) NOT NULL,
    config_value text NOT NULL
);


--
-- Name: section_curriculum_lock; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.section_curriculum_lock (
    sectionid integer NOT NULL,
    semesterid integer NOT NULL,
    curriculum_mode character varying(10) DEFAULT 'regular'::character varying NOT NULL,
    locked_at timestamp without time zone DEFAULT now()
);


--
-- Name: sections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sections (
    sectionid integer NOT NULL,
    programyearlevelid integer NOT NULL,
    sectionname character varying(100) NOT NULL,
    isactive boolean DEFAULT true
);


--
-- Name: sections_sectionid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.sections ALTER COLUMN sectionid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.sections_sectionid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: semester; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.semester (
    semesterid integer NOT NULL,
    academicyearid character varying(9) NOT NULL,
    semestertype character(1),
    semstartdate date,
    semenddate date,
    isactive boolean DEFAULT false,
    CONSTRAINT chk_semestertype CHECK ((semestertype = ANY (ARRAY['A'::bpchar, 'B'::bpchar, 'C'::bpchar])))
);


--
-- Name: semester_semesterid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.semester ALTER COLUMN semesterid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.semester_semesterid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: specialization; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.specialization (
    specializationid integer NOT NULL,
    specializationname character varying(100) NOT NULL,
    isactive boolean DEFAULT true NOT NULL
);


--
-- Name: specialization_specializationid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.specialization ALTER COLUMN specializationid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.specialization_specializationid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: subject_faculty_assignment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subject_faculty_assignment (
    id integer NOT NULL,
    programcode character varying(20) NOT NULL,
    yearlevel smallint NOT NULL,
    semesterid integer NOT NULL,
    subjectcode character varying(50) NOT NULL,
    employeenumber character varying(50) NOT NULL,
    createdat timestamp without time zone DEFAULT now(),
    sectionid integer
);


--
-- Name: subject_faculty_assignment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.subject_faculty_assignment_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: subject_faculty_assignment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.subject_faculty_assignment_id_seq OWNED BY public.subject_faculty_assignment.id;


--
-- Name: timeslot; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.timeslot (
    timeid integer NOT NULL,
    timevalue time without time zone NOT NULL
);


--
-- Name: timeslot_timeid_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.timeslot ALTER COLUMN timeid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.timeslot_timeid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: vw_academic_year_semesters; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.vw_academic_year_semesters AS
 SELECT ay.academicyearid,
    max(
        CASE
            WHEN (s.semestertype = 'A'::bpchar) THEN s.semstartdate
            ELSE NULL::date
        END) AS "1st Sem Start",
    max(
        CASE
            WHEN (s.semestertype = 'A'::bpchar) THEN s.semenddate
            ELSE NULL::date
        END) AS "1st Sem End",
    max(
        CASE
            WHEN (s.semestertype = 'B'::bpchar) THEN s.semstartdate
            ELSE NULL::date
        END) AS "2nd Sem Start",
    max(
        CASE
            WHEN (s.semestertype = 'B'::bpchar) THEN s.semenddate
            ELSE NULL::date
        END) AS "2nd Sem End",
    max(
        CASE
            WHEN (s.semestertype = 'C'::bpchar) THEN s.semstartdate
            ELSE NULL::date
        END) AS "3rd Sem Start",
    max(
        CASE
            WHEN (s.semestertype = 'C'::bpchar) THEN s.semenddate
            ELSE NULL::date
        END) AS "3rd Sem End"
   FROM (public.academicyear ay
     LEFT JOIN public.semester s ON (((ay.academicyearid)::text = (s.academicyearid)::text)))
  GROUP BY ay.academicyearid;


--
-- Name: activity_log logid; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity_log ALTER COLUMN logid SET DEFAULT nextval('public.activity_log_logid_seq'::regclass);


--
-- Name: historical_data historyid; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historical_data ALTER COLUMN historyid SET DEFAULT nextval('public.historical_data_historyid_seq'::regclass);


--
-- Name: local_arrangement arrangementid; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_arrangement ALTER COLUMN arrangementid SET DEFAULT nextval('public.local_arrangement_arrangementid_seq'::regclass);


--
-- Name: local_arrangement_sessions sessionid; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_arrangement_sessions ALTER COLUMN sessionid SET DEFAULT nextval('public.local_arrangement_sessions_sessionid_seq'::regclass);


--
-- Name: local_displaced_subjects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_displaced_subjects ALTER COLUMN id SET DEFAULT nextval('public.local_displaced_subjects_id_seq'::regclass);


--
-- Name: merge_load_policy policyid; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.merge_load_policy ALTER COLUMN policyid SET DEFAULT nextval('public.merge_load_policy_policyid_seq'::regclass);


--
-- Name: program_name_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_name_history ALTER COLUMN id SET DEFAULT nextval('public.program_name_history_id_seq'::regclass);


--
-- Name: subject_faculty_assignment id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_faculty_assignment ALTER COLUMN id SET DEFAULT nextval('public.subject_faculty_assignment_id_seq'::regclass);


--
-- Name: academicyear academicyear_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.academicyear
    ADD CONSTRAINT academicyear_pkey PRIMARY KEY (academicyearid);


--
-- Name: accounts accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (userid);


--
-- Name: accounts accounts_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_username_key UNIQUE (username);


--
-- Name: activity_log activity_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity_log
    ADD CONSTRAINT activity_log_pkey PRIMARY KEY (logid);


--
-- Name: building building_buildingname_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.building
    ADD CONSTRAINT building_buildingname_key UNIQUE (buildingname);


--
-- Name: building building_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.building
    ADD CONSTRAINT building_pkey PRIMARY KEY (buildingid);


--
-- Name: curriculum curriculum_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.curriculum
    ADD CONSTRAINT curriculum_pkey PRIMARY KEY (curriculumid);


--
-- Name: curriculumsubject curriculumsubject_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.curriculumsubject
    ADD CONSTRAINT curriculumsubject_pkey PRIMARY KEY (curriculumsubjectid);


--
-- Name: designation designation_designationname_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.designation
    ADD CONSTRAINT designation_designationname_key UNIQUE (designationname);


--
-- Name: designation designation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.designation
    ADD CONSTRAINT designation_pkey PRIMARY KEY (designationid);


--
-- Name: employeetype employeetype_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employeetype
    ADD CONSTRAINT employeetype_pkey PRIMARY KEY (employeetypeid);


--
-- Name: employeetype employeetype_typename_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employeetype
    ADD CONSTRAINT employeetype_typename_key UNIQUE (typename);


--
-- Name: faculty_archive faculty_archive_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.faculty_archive
    ADD CONSTRAINT faculty_archive_pkey PRIMARY KEY (facultyarchiveid);


--
-- Name: faculty faculty_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.faculty
    ADD CONSTRAINT faculty_email_key UNIQUE (email);


--
-- Name: faculty faculty_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.faculty
    ADD CONSTRAINT faculty_pkey PRIMARY KEY (employeenumber);


--
-- Name: historical_data historical_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historical_data
    ADD CONSTRAINT historical_data_pkey PRIMARY KEY (historyid);


--
-- Name: local_arrangement local_arrangement_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_arrangement
    ADD CONSTRAINT local_arrangement_pkey PRIMARY KEY (arrangementid);


--
-- Name: local_arrangement_sessions local_arrangement_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_arrangement_sessions
    ADD CONSTRAINT local_arrangement_sessions_pkey PRIMARY KEY (sessionid);


--
-- Name: local_displaced_subjects local_displaced_subjects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_displaced_subjects
    ADD CONSTRAINT local_displaced_subjects_pkey PRIMARY KEY (id);


--
-- Name: local_displaced_subjects local_displaced_subjects_programcode_yearlevel_semesterid_s_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_displaced_subjects
    ADD CONSTRAINT local_displaced_subjects_programcode_yearlevel_semesterid_s_key UNIQUE (programcode, yearlevel, semesterid, subjectcode);


--
-- Name: local_schedule_adjustment local_schedule_adjustment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_schedule_adjustment
    ADD CONSTRAINT local_schedule_adjustment_pkey PRIMARY KEY (adjustmentid);


--
-- Name: class_meeting_request makeupclass_request_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.class_meeting_request
    ADD CONSTRAINT makeupclass_request_pkey PRIMARY KEY (requestid);


--
-- Name: merge_load_policy merge_load_policy_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.merge_load_policy
    ADD CONSTRAINT merge_load_policy_pkey PRIMARY KEY (policyid);


--
-- Name: mergedclass mergedclass_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mergedclass
    ADD CONSTRAINT mergedclass_pkey PRIMARY KEY (mergedclassid);


--
-- Name: mergedclass_sections mergedclass_sections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mergedclass_sections
    ADD CONSTRAINT mergedclass_sections_pkey PRIMARY KEY (mergedclasssectionid);


--
-- Name: program_name_history program_name_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_name_history
    ADD CONSTRAINT program_name_history_pkey PRIMARY KEY (id);


--
-- Name: program_yearlevel program_yearlevel_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_yearlevel
    ADD CONSTRAINT program_yearlevel_pkey PRIMARY KEY (programyearlevelid);


--
-- Name: programs programs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.programs
    ADD CONSTRAINT programs_pkey PRIMARY KEY (programcode);


--
-- Name: room room_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.room
    ADD CONSTRAINT room_pkey PRIMARY KEY (roomid);


--
-- Name: schedule_change_request schedule_change_request_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_change_request
    ADD CONSTRAINT schedule_change_request_pkey PRIMARY KEY (requestid);


--
-- Name: schedule_exception_log schedule_exception_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_exception_log
    ADD CONSTRAINT schedule_exception_log_pkey PRIMARY KEY (logid);


--
-- Name: schedule schedule_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule
    ADD CONSTRAINT schedule_pkey PRIMARY KEY (scheduleid);


--
-- Name: schedule_sessions schedule_session_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_sessions
    ADD CONSTRAINT schedule_session_pkey PRIMARY KEY (sessionid);


--
-- Name: schedule_version schedule_version_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_version
    ADD CONSTRAINT schedule_version_pkey PRIMARY KEY (versionid);


--
-- Name: scheduler_config scheduler_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scheduler_config
    ADD CONSTRAINT scheduler_config_pkey PRIMARY KEY (config_key);


--
-- Name: section_curriculum_lock section_curriculum_lock_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.section_curriculum_lock
    ADD CONSTRAINT section_curriculum_lock_pkey PRIMARY KEY (sectionid, semesterid);


--
-- Name: sections sections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sections
    ADD CONSTRAINT sections_pkey PRIMARY KEY (sectionid);


--
-- Name: semester semester_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.semester
    ADD CONSTRAINT semester_pkey PRIMARY KEY (semesterid);


--
-- Name: specialization specialization_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.specialization
    ADD CONSTRAINT specialization_pkey PRIMARY KEY (specializationid);


--
-- Name: specialization specialization_specializationname_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.specialization
    ADD CONSTRAINT specialization_specializationname_key UNIQUE (specializationname);


--
-- Name: subject_faculty_assignment subject_faculty_assignment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_faculty_assignment
    ADD CONSTRAINT subject_faculty_assignment_pkey PRIMARY KEY (id);


--
-- Name: subject_faculty_assignment subject_faculty_assignment_programcode_yearlevel_semesterid_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_faculty_assignment
    ADD CONSTRAINT subject_faculty_assignment_programcode_yearlevel_semesterid_key UNIQUE (programcode, yearlevel, semesterid, subjectcode);


--
-- Name: timeslot timeslot_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.timeslot
    ADD CONSTRAINT timeslot_pkey PRIMARY KEY (timeid);


--
-- Name: timeslot timeslot_timevalue_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.timeslot
    ADD CONSTRAINT timeslot_timevalue_key UNIQUE (timevalue);


--
-- Name: academicyear uq_academicyear; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.academicyear
    ADD CONSTRAINT uq_academicyear UNIQUE (yearstart, yearend);


--
-- Name: curriculum uq_curriculum; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.curriculum
    ADD CONSTRAINT uq_curriculum UNIQUE (programcode, curriculumcode, curriculumversion);


--
-- Name: curriculumsubject uq_curriculumsubject; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.curriculumsubject
    ADD CONSTRAINT uq_curriculumsubject UNIQUE (curriculumid, subjectcode, yearlevel, semester);


--
-- Name: mergedclass_sections uq_mergedclass_section; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mergedclass_sections
    ADD CONSTRAINT uq_mergedclass_section UNIQUE (mergedclassid, sectionid);


--
-- Name: program_yearlevel uq_program_yearlevel; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_yearlevel
    ADD CONSTRAINT uq_program_yearlevel UNIQUE (programcode, academicyearid, startacademicyear, yearlevel);


--
-- Name: room uq_roomname; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.room
    ADD CONSTRAINT uq_roomname UNIQUE (roomname);


--
-- Name: schedule_version uq_schedule_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_version
    ADD CONSTRAINT uq_schedule_version UNIQUE (scheduleid, version_number);


--
-- Name: schedule uq_schedule_subject_section_semester; Type: CONSTRAINT; Schema: public; Owner: -
--
-- Enforces ONE logical schedule row per (curriculumsubjectid, sectionid, semesterid) —
-- every Draft/Published/Archive version of that same subject+section+semester shares
-- the same scheduleid. This existed on the live dev database as untracked ad-hoc drift
-- (see migrations/README.md) before being formally captured here on 2026-09-17 as part
-- of the Manual Scheduler Draft/Publish fix (app.py's _find_or_create_schedule).
--

ALTER TABLE ONLY public.schedule
    ADD CONSTRAINT uq_schedule_subject_section_semester UNIQUE (curriculumsubjectid, sectionid, semesterid);


--
-- Name: sections uq_section; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sections
    ADD CONSTRAINT uq_section UNIQUE (programyearlevelid, sectionname);


--
-- Name: semester uq_semester; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.semester
    ADD CONSTRAINT uq_semester UNIQUE (academicyearid, semestertype);


--
-- Name: idx_academicyear_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_academicyear_status ON public.academicyear USING btree (status);


--
-- Name: idx_program_name_history_code_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_program_name_history_code_date ON public.program_name_history USING btree (programcode, changed_at);


--
-- Name: accounts fk_accounts_employeenumber; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT fk_accounts_employeenumber FOREIGN KEY (employeenumber) REFERENCES public.faculty(employeenumber);


--
-- Name: curriculum fk_curriculum_program; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.curriculum
    ADD CONSTRAINT fk_curriculum_program FOREIGN KEY (programcode) REFERENCES public.programs(programcode);


--
-- Name: program_yearlevel fk_curriculum_program; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_yearlevel
    ADD CONSTRAINT fk_curriculum_program FOREIGN KEY (programcode) REFERENCES public.programs(programcode);


--
-- Name: curriculumsubject fk_curriculumsubject_curriculum; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.curriculumsubject
    ADD CONSTRAINT fk_curriculumsubject_curriculum FOREIGN KEY (curriculumid) REFERENCES public.curriculum(curriculumid);


--
-- Name: faculty fk_faculty_designation; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.faculty
    ADD CONSTRAINT fk_faculty_designation FOREIGN KEY (designationid) REFERENCES public.designation(designationid);


--
-- Name: faculty fk_faculty_employeetype; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.faculty
    ADD CONSTRAINT fk_faculty_employeetype FOREIGN KEY (employeetypeid) REFERENCES public.employeetype(employeetypeid);


--
-- Name: faculty fk_faculty_specialization; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.faculty
    ADD CONSTRAINT fk_faculty_specialization FOREIGN KEY (specializationid) REFERENCES public.specialization(specializationid);


--
-- Name: schedule fk_group_curriculumsubject; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule
    ADD CONSTRAINT fk_group_curriculumsubject FOREIGN KEY (curriculumsubjectid) REFERENCES public.curriculumsubject(curriculumsubjectid);


--
-- Name: schedule fk_group_faculty; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule
    ADD CONSTRAINT fk_group_faculty FOREIGN KEY (employeenumber) REFERENCES public.faculty(employeenumber);


--
-- Name: schedule fk_group_semester; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule
    ADD CONSTRAINT fk_group_semester FOREIGN KEY (semesterid) REFERENCES public.semester(semesterid);


--
-- Name: schedule_version schedule_version_employeenumber_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_version
    ADD CONSTRAINT schedule_version_employeenumber_fkey FOREIGN KEY (employeenumber) REFERENCES public.faculty(employeenumber);


--
-- Name: local_schedule_adjustment fk_lsa_new_endtime; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_schedule_adjustment
    ADD CONSTRAINT fk_lsa_new_endtime FOREIGN KEY (new_endtimeid) REFERENCES public.timeslot(timeid);


--
-- Name: local_schedule_adjustment fk_lsa_new_room; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_schedule_adjustment
    ADD CONSTRAINT fk_lsa_new_room FOREIGN KEY (new_roomid) REFERENCES public.room(roomid);


--
-- Name: local_schedule_adjustment fk_lsa_new_starttime; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_schedule_adjustment
    ADD CONSTRAINT fk_lsa_new_starttime FOREIGN KEY (new_starttimeid) REFERENCES public.timeslot(timeid);


--
-- Name: local_schedule_adjustment fk_lsa_old_endtime; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_schedule_adjustment
    ADD CONSTRAINT fk_lsa_old_endtime FOREIGN KEY (old_endtimeid) REFERENCES public.timeslot(timeid);


--
-- Name: local_schedule_adjustment fk_lsa_old_room; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_schedule_adjustment
    ADD CONSTRAINT fk_lsa_old_room FOREIGN KEY (old_roomid) REFERENCES public.room(roomid);


--
-- Name: local_schedule_adjustment fk_lsa_old_starttime; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_schedule_adjustment
    ADD CONSTRAINT fk_lsa_old_starttime FOREIGN KEY (old_starttimeid) REFERENCES public.timeslot(timeid);


--
-- Name: local_schedule_adjustment fk_lsa_schedule; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_schedule_adjustment
    ADD CONSTRAINT fk_lsa_schedule FOREIGN KEY (scheduleid) REFERENCES public.schedule(scheduleid);


--
-- Name: local_schedule_adjustment fk_lsa_session; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_schedule_adjustment
    ADD CONSTRAINT fk_lsa_session FOREIGN KEY (sessionid) REFERENCES public.schedule_sessions(sessionid);


--
-- Name: mergedclass_sections fk_mcs_mergedclass; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mergedclass_sections
    ADD CONSTRAINT fk_mcs_mergedclass FOREIGN KEY (mergedclassid) REFERENCES public.mergedclass(mergedclassid) ON DELETE CASCADE;


--
-- Name: mergedclass_sections fk_mcs_section; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mergedclass_sections
    ADD CONSTRAINT fk_mcs_section FOREIGN KEY (sectionid) REFERENCES public.sections(sectionid) ON DELETE CASCADE;


--
-- Name: mergedclass fk_mergedclass_curriculumsubject; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mergedclass
    ADD CONSTRAINT fk_mergedclass_curriculumsubject FOREIGN KEY (curriculumsubjectid) REFERENCES public.curriculumsubject(curriculumsubjectid);


--
-- Name: mergedclass fk_mergedclass_faculty; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mergedclass
    ADD CONSTRAINT fk_mergedclass_faculty FOREIGN KEY (employeenumber) REFERENCES public.faculty(employeenumber);


--
-- Name: mergedclass fk_mergedclass_semester; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mergedclass
    ADD CONSTRAINT fk_mergedclass_semester FOREIGN KEY (semesterid) REFERENCES public.semester(semesterid);


--
-- Name: class_meeting_request fk_mkup_endtime; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.class_meeting_request
    ADD CONSTRAINT fk_mkup_endtime FOREIGN KEY (new_endtimeid) REFERENCES public.timeslot(timeid);


--
-- Name: class_meeting_request fk_mkup_reviewed_by; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.class_meeting_request
    ADD CONSTRAINT fk_mkup_reviewed_by FOREIGN KEY (reviewed_by) REFERENCES public.faculty(employeenumber);


--
-- Name: class_meeting_request fk_mkup_room; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.class_meeting_request
    ADD CONSTRAINT fk_mkup_room FOREIGN KEY (new_roomid) REFERENCES public.room(roomid);


--
-- Name: class_meeting_request fk_mkup_schedule; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.class_meeting_request
    ADD CONSTRAINT fk_mkup_schedule FOREIGN KEY (scheduleid) REFERENCES public.schedule(scheduleid);


--
-- Name: class_meeting_request fk_mkup_starttime; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.class_meeting_request
    ADD CONSTRAINT fk_mkup_starttime FOREIGN KEY (new_starttimeid) REFERENCES public.timeslot(timeid);


--
-- Name: class_meeting_request fk_mkup_submitted_by; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.class_meeting_request
    ADD CONSTRAINT fk_mkup_submitted_by FOREIGN KEY (submitted_by) REFERENCES public.faculty(employeenumber);


--
-- Name: program_yearlevel fk_pyl_academicyear; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_yearlevel
    ADD CONSTRAINT fk_pyl_academicyear FOREIGN KEY (academicyearid) REFERENCES public.academicyear(academicyearid);


--
-- Name: program_yearlevel fk_pyl_curriculum; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.program_yearlevel
    ADD CONSTRAINT fk_pyl_curriculum FOREIGN KEY (curriculumid) REFERENCES public.curriculum(curriculumid);


--
-- Name: room fk_room_building; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.room
    ADD CONSTRAINT fk_room_building FOREIGN KEY (buildingid) REFERENCES public.building(buildingid);


--
-- Name: schedule_change_request fk_scr_endtime; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_change_request
    ADD CONSTRAINT fk_scr_endtime FOREIGN KEY (new_endtimeid) REFERENCES public.timeslot(timeid);


--
-- Name: schedule_change_request fk_scr_reviewed_by; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_change_request
    ADD CONSTRAINT fk_scr_reviewed_by FOREIGN KEY (reviewed_by) REFERENCES public.faculty(employeenumber);


--
-- Name: schedule_change_request fk_scr_room; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_change_request
    ADD CONSTRAINT fk_scr_room FOREIGN KEY (new_roomid) REFERENCES public.room(roomid);


--
-- Name: schedule_change_request fk_scr_schedule; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_change_request
    ADD CONSTRAINT fk_scr_schedule FOREIGN KEY (scheduleid) REFERENCES public.schedule(scheduleid);


--
-- Name: schedule_change_request fk_scr_starttime; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_change_request
    ADD CONSTRAINT fk_scr_starttime FOREIGN KEY (new_starttimeid) REFERENCES public.timeslot(timeid);


--
-- Name: schedule_change_request fk_scr_submitted_by; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_change_request
    ADD CONSTRAINT fk_scr_submitted_by FOREIGN KEY (submitted_by) REFERENCES public.faculty(employeenumber);


--
-- Name: schedule_change_request fk_scr_version; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_change_request
    ADD CONSTRAINT fk_scr_version FOREIGN KEY (versionid) REFERENCES public.schedule_version(versionid);


--
-- Name: sections fk_section_programyearlevel; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sections
    ADD CONSTRAINT fk_section_programyearlevel FOREIGN KEY (programyearlevelid) REFERENCES public.program_yearlevel(programyearlevelid) ON DELETE CASCADE;


--
-- Name: schedule_exception_log fk_sel_approved_by; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_exception_log
    ADD CONSTRAINT fk_sel_approved_by FOREIGN KEY (approved_by) REFERENCES public.faculty(employeenumber);


--
-- Name: schedule_exception_log fk_sel_semester; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_exception_log
    ADD CONSTRAINT fk_sel_semester FOREIGN KEY (semesterid) REFERENCES public.semester(semesterid);


--
-- Name: semester fk_semester_academicyear; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.semester
    ADD CONSTRAINT fk_semester_academicyear FOREIGN KEY (academicyearid) REFERENCES public.academicyear(academicyearid);


--
-- Name: schedule_sessions fk_session_endtime; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_sessions
    ADD CONSTRAINT fk_session_endtime FOREIGN KEY (endtimeid) REFERENCES public.timeslot(timeid);


--
-- Name: schedule_sessions fk_session_room; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_sessions
    ADD CONSTRAINT fk_session_room FOREIGN KEY (roomid) REFERENCES public.room(roomid);


--
-- Name: schedule_sessions fk_session_starttime; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_sessions
    ADD CONSTRAINT fk_session_starttime FOREIGN KEY (starttimeid) REFERENCES public.timeslot(timeid);


--
-- Name: schedule_sessions fk_session_version; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_sessions
    ADD CONSTRAINT fk_session_version FOREIGN KEY (versionid) REFERENCES public.schedule_version(versionid) ON DELETE CASCADE;


--
-- Name: schedule_version fk_version_group; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_version
    ADD CONSTRAINT fk_version_group FOREIGN KEY (scheduleid) REFERENCES public.schedule(scheduleid) ON DELETE CASCADE;


--
-- Name: local_arrangement_sessions local_arrangement_sessions_arrangementid_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_arrangement_sessions
    ADD CONSTRAINT local_arrangement_sessions_arrangementid_fkey FOREIGN KEY (arrangementid) REFERENCES public.local_arrangement(arrangementid);


--
-- Name: local_displaced_subjects local_displaced_subjects_displaced_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.local_displaced_subjects
    ADD CONSTRAINT local_displaced_subjects_displaced_by_fkey FOREIGN KEY (displaced_by) REFERENCES public.local_arrangement(arrangementid);


--
-- PostgreSQL database dump complete
--

\unrestrict LzhUy6hBnCKXULuahbRzLMfjTfH0EQj33tN01wK4UqF9HWcueyt6vbcXeSHAtYz

