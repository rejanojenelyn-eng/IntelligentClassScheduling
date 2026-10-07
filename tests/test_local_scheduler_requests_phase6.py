from pathlib import Path
APP=Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
def block(a,b):
    s=APP.index(f"def {a}"); e=APP.index(f"def {b}",s); return APP[s:e]

def test_request_local_conflict_is_active_published_and_semester_scoped():
    b=block("_request_local_conflict","api_requests_validate")
    assert "la.status = 'Published'" in b and "la.is_active = TRUE" in b
    assert "SELECT semesterid FROM public.schedule WHERE scheduleid = %s" in b

def test_request_local_conflict_supports_room_faculty_and_cohort():
    b=block("_request_local_conflict","api_requests_validate")
    assert "las.roomid = %s" in b
    assert "las.faculty_employeenumber = %s" in b
    assert "programyearlevelid = %s" in b

def test_makeup_validation_checks_local_room_faculty_program():
    b=block("api_requests_validate","api_requests_decide")
    m=b[:b.index("elif req_type == 'adjustment':")]
    assert "room_local = _request_local_conflict" in m
    assert "faculty_local = _request_local_conflict" in m
    assert "program_local = _request_local_conflict" in m

def test_adjustment_validation_checks_local_room_faculty_program():
    b=block("api_requests_validate","api_requests_decide")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "room_local = _request_local_conflict" in a
    assert "faculty_local = _request_local_conflict" in a
    assert "program_local = _request_local_conflict" in a

def test_faculty_makeup_submission_checks_local_faculty_commitment():
    b=block("api_faculty_submit_request","api_faculty_check_request_conflicts")
    assert "local_fac_conflict = _request_local_conflict" in b
    assert "employee_number=submitted_by" in b
    assert "Local Schedule commitment" in b

# Constraint-fix Phase 5: a Make-up is ONE meeting on requested_date. The four
# tests below used to pin the obsolete behavior where approval inserted a
# recurring weekly schedule_sessions row into the Published Official schedule
# (which also added teaching load). They now pin the approved requirement.

def test_makeup_is_one_date_but_adjustment_uses_local_override():
    b=block("api_requests_decide","api_faculty_my_requests")
    m=b[:b.index("elif req_type == 'adjustment':")]
    assert "INSERT INTO schedule_sessions" not in m
    adjustment=b[b.index("elif req_type == 'adjustment':"):]
    assert "UPDATE schedule_sessions SET" not in adjustment
    assert "INSERT INTO public.local_arrangement" in adjustment
    assert "INSERT INTO public.local_arrangement_sessions" in adjustment

def test_approved_makeup_adds_no_official_session_and_no_local_override():
    b=block("api_requests_decide","api_faculty_my_requests")
    m=b[:b.index("elif req_type == 'adjustment':")]
    assert "INSERT INTO schedule_sessions" not in m
    assert "INSERT INTO local_arrangement" not in m
    assert "INSERT INTO public.local_arrangement" not in m
    assert "UPDATE schedule_sessions SET" not in m

def test_makeup_approval_revalidates_its_requested_date_and_slot():
    b=block("api_requests_decide","api_faculty_my_requests")
    m=b[:b.index("elif req_type == 'adjustment':")]
    assert "_request_conflict_summary('makeup', req_id)" in m
    assert "REQUEST_NO_LONGER_VALID" in m
    assert "mk.get('requested_date')" in m
    assert "mk.get('new_starttimeid')" in m

def test_makeup_approval_only_records_the_decision_on_the_request_row():
    b=block("api_requests_decide","api_faculty_my_requests")
    m=b[:b.index("elif req_type == 'adjustment':")]
    assert "UPDATE class_meeting_request" in m
    assert "schedule_sessions" not in m

def test_local_displacement_only_targets_sessions_explicitly_linked_by_occurrence():
    assert "las_x.official_sessionid = ss.sessionid" in APP

def test_makeup_is_not_a_recurring_published_official_session():
    # Phase 5: previously pinned that the make-up became a weekly Official row.
    approval=block("api_requests_decide","api_faculty_my_requests")
    makeup=approval[:approval.index("elif req_type == 'adjustment':")]
    assert "INSERT INTO schedule_sessions" not in makeup
    # Effective schedule integrations throughout the app source Published Official sessions.
    assert "sv.status = 'Published'" in APP
    assert "las_x.official_sessionid = ss.sessionid" in APP

def test_room_and_class_schedule_reports_read_the_official_schedule_source():
    # Renamed in Phase 5 (was "..._makeup_flows_into_room_and_class_schedule_reports_...");
    # the assertions below never involved make-ups and are unchanged.
    rs=APP.index("def _room_report_fetch")
    re_=APP.index("def _room_report_groups",rs)
    room=APP[rs:re_]
    cs=APP.index("def _sch_exp_fetch")
    ce=APP.index("_SCH_WEEKDAY_ORDER",cs)
    cls=APP[cs:ce]
    import faculty_load as _fl
    # Room report = LIVE occupancy via the shared effective schedule (Phase 9.3);
    # the class-schedule export keeps its Official Draft preview.
    assert "faculty_load.EFFECTIVE_SESSIONS_CTE" in room
    assert "schedule_sessions ss" in _fl.EFFECTIVE_SESSIONS_CTE
    assert "'Draft'" not in room
    assert "schedule_sessions ss" in cls
    assert "sv.status IN ('Published', 'Draft')" in cls

def test_adjustment_request_table_has_occurrence_identity_column():
    assert '"official_sessionid INT"' in APP

def test_faculty_subject_picker_returns_concrete_published_occurrences():
    b=block("api_faculty_my_schedule_subjects","api_faculty_me")
    assert "JOIN schedule_sessions ss ON ss.versionid = sv.versionid" in b
    assert "ss.sessionid AS official_sessionid" in b
    assert "ss.daydesc" in b
    assert "start_time" in b and "end_time" in b
    assert "roomname" in b

def test_adjustment_submission_requires_exact_occurrence():
    b=block("api_faculty_submit_request","api_faculty_check_request_conflicts")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "Please select the exact class meeting to adjust." in a
    assert "ss.sessionid = %s" in a
    assert "sv.versionid = %s" in a
    assert "sv.scheduleid = %s" in a
    assert "sv.status = 'Published'" in a

def test_adjustment_request_persists_official_sessionid():
    b=block("api_faculty_submit_request","api_faculty_check_request_conflicts")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "(scheduleid, versionid, official_sessionid, change_type" in a
    assert "(scheduleid, versionid, official_sessionid, change_type, day" in a

def test_actual_faculty_assignment_source_exposes_occurrence_identity():
    b=block("api_faculty_my_assignments","api_faculty_blocked_times")
    # Live effective rows still expose the Official occurrence + version a request binds to.
    assert "faculty_load.EFFECTIVE_SESSIONS_CTE" in b
    assert "es.versionid" in b
    assert "es.official_sessionid" in b

def test_adjustment_approval_never_mutates_official_session():
    b=block("api_requests_decide","api_faculty_my_requests")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "UPDATE schedule_sessions SET" not in a

def test_adjustment_approval_links_local_to_exact_official_occurrence():
    b=block("api_requests_decide","api_faculty_my_requests")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "official_sessionid" in a
    assert "INSERT INTO public.local_arrangement_sessions" in a
    assert "adj['official_sessionid']" in a

def test_adjustment_approval_preserves_unspecified_original_values():
    b=block("api_requests_decide","api_faculty_my_requests")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "or adj['official_daydesc']" in a
    assert "or adj['official_starttimeid']" in a
    assert "or adj['official_endtimeid']" in a
    assert "or adj['official_roomid']" in a

def test_legacy_adjustment_is_not_guessed_on_approval():
    b=block("api_requests_decide","api_faculty_my_requests")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "legacy Schedule Adjustment without an exact" in a
    assert "return jsonify" in a and "), 409" in a

def test_adjustment_override_is_immediately_published_and_active():
    b=block("api_requests_decide","api_faculty_my_requests")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "TRUE,%s,'Published'" in a
    assert "la.status = 'Published'" in a
    assert "la.is_active = TRUE" in a

def test_adjustment_approval_is_idempotent_for_same_request_occurrence():
    b=block("api_requests_decide","api_faculty_my_requests")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "Approved Schedule Adjustment request #{req_id}" in a
    assert "if not existing:" in a

def test_adjustment_supersedes_older_local_override_for_same_occurrence():
    b=block("api_requests_decide","api_faculty_my_requests")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "SET status='Archived', is_active=FALSE" in a
    assert "las.official_sessionid=%s" in a
    assert a.index("SET status='Archived', is_active=FALSE") < a.index("INSERT INTO public.local_arrangement")

def test_approved_adjustment_is_not_faculty_editable_or_cancellable():
    u=block("api_faculty_update_request","api_faculty_cancel_request")
    c=block("api_faculty_cancel_request","faculty_requests")
    assert "status='Pending'" in u
    assert "status='Pending'" in c

def test_adjustment_approval_is_idempotent_before_supersession():
    b=block("api_requests_decide","api_faculty_my_requests")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "la.override_reason=%s" in a
    assert "if not existing:" in a

def test_adjustment_approval_preserves_official_schedule():
    b=block("api_requests_decide","api_faculty_my_requests")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "UPDATE schedule_sessions SET" not in a
    assert "official_sessionid" in a

def test_request_created_override_uses_same_local_tables_as_manual_local_scheduler():
    b=block("api_requests_decide","api_faculty_my_requests")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "public.local_arrangement" in a
    assert "public.local_arrangement_sessions" in a
    assert "official_sessionid" in a
    assert "'Published'" in a

def test_request_adjustment_is_consumed_by_effective_class_export():
    s=APP.index("def _sch_exp_fetch")
    e=APP.index("_SCH_WEEKDAY_ORDER",s)
    b=APP[s:e]
    assert "local_arrangement_sessions" in b
    assert "official_sessionid" in b
    assert "Published" in b

def test_request_adjustment_is_consumed_by_effective_room_report():
    import faculty_load as _fl
    s=APP.index("def _room_report_fetch")
    e=APP.index("def _room_report_groups",s)
    b=APP[s:e]
    assert "faculty_load.EFFECTIVE_SESSIONS_CTE" in b
    cte=_fl.EFFECTIVE_SESSIONS_CTE
    assert "local_arrangement_sessions" in cte
    assert "official_sessionid" in cte
    assert "Published" in cte

def test_request_conflict_validation_reads_published_local_occupancy():
    b=block("_request_local_conflict","api_requests_validate")
    assert "local_arrangement_sessions" in b
    assert "la.status = 'Published'" in b
    assert "la.is_active = TRUE" in b

def test_request_override_does_not_change_teaching_assignment_load():
    s=APP.index("def _fetch_report_data")
    e=APP.index("# ─── Report title mapping",s)
    b=APP[s:e]
    s=b.index("elif report_type == 'assignments':")
    e=b.index("# ── ACADEMIC OFFERINGS",s)
    a=b[s:e]
    assert "local_arrangement" not in a

def test_request_override_does_not_rewrite_historical_data():
    s=APP.index("def _sch_exp_fetch")
    e=APP.index("_SCH_WEEKDAY_ORDER",s)
    b=APP[s:e]
    assert "INSERT INTO historical_data" not in b
    assert "UPDATE historical_data" not in b
    assert "DELETE FROM historical_data" not in b

def test_request_and_manual_local_share_supersession_identity():
    b=block("api_requests_decide","api_faculty_my_requests")
    a=b[b.index("elif req_type == 'adjustment':"):]
    assert "las.official_sessionid=%s" in a
    assert "SET status='Archived', is_active=FALSE" in a

def test_inactive_semester_local_records_are_archived_centrally():
    b=block("_archive_local_for_inactive_semesters","_local_semester_is_active")
    assert "status = 'Archived'" in b
    assert "is_active = FALSE" in b
    assert "la.status IN ('Draft', 'Published')" in b
    assert "COALESCE(sem.isactive, FALSE) = FALSE" in b

def test_local_publish_rejects_inactive_semester():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert "_archive_local_for_inactive_semesters(cur)" in b
    assert "_local_semester_is_active(cur, arr['semesterid'])" in b
    assert "inactive semester and cannot be published" in b

def test_local_restore_rejects_inactive_semester():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "_archive_local_for_inactive_semesters(cur)" in b
    assert "_local_semester_is_active(cur, src['semesterid'])" in b
    assert "inactive semester cannot be restored" in b

def test_local_semester_lifecycle_does_not_write_historical_data():
    s=APP.index("def _archive_local_for_inactive_semesters")
    e=APP.index("@app.route('/api/local/check_room_conflicts",s)
    b=APP[s:e]
    assert "INSERT INTO historical_data" not in b
    assert "UPDATE historical_data" not in b
    assert "DELETE FROM historical_data" not in b

def test_admin_period_activation_archives_outgoing_local_state_immediately():
    b=block("activate_period","finalize_ay")
    assert "UPDATE Semester SET IsActive = FALSE" in b
    assert "_ensure_local_tables(cur)" in b
    assert "archived_local_count = _archive_local_for_inactive_semesters(cur)" in b
    assert b.index("_archive_local_for_inactive_semesters(cur)") < b.index("conn.commit()")

def test_admin_period_activation_logs_local_archival_count():
    b=block("activate_period","finalize_ay")
    assert "Local Scheduler Semester Archive" in b
    assert "archived_local_count" in b

def test_date_driven_period_transition_archives_local_before_commit():
    marker="Only write to DB when the active semester has changed"
    s=APP.index(marker)
    e=APP.index("# --- 1. ROOT ROUTE ---",s)
    b=APP[s:e]
    assert "_auto_archive_semester(cur, currently_active['semesterid'])" in b
    assert "_archive_local_for_inactive_semesters(cur)" in b
    assert b.index("_archive_local_for_inactive_semesters(cur)") < b.index("conn.commit()")

def test_period_transition_keeps_newly_activated_semester_local_records():
    # Central cleanup only archives semesters whose isactive flag is FALSE;
    # activation sets the selected semester TRUE before invoking cleanup.
    h=block("_archive_local_for_inactive_semesters","_local_semester_is_active")
    a=block("activate_period","finalize_ay")
    assert "COALESCE(sem.isactive, FALSE) = FALSE" in h
    assert a.index("UPDATE Semester SET IsActive = TRUE") < a.index("_archive_local_for_inactive_semesters(cur)")

def test_finalize_ay_archives_all_local_state_for_that_ay():
    b=block("finalize_ay","delete_ay")
    assert "_ensure_local_tables(cur)" in b
    assert "_archive_local_for_academic_year(cur, ay_id)" in b
    assert b.index("_archive_local_for_academic_year(cur, ay_id)") < b.rindex("conn.commit()")

def test_ay_local_archive_helper_targets_only_selected_ay():
    b=block("_archive_local_for_academic_year","_local_semester_is_active")
    assert "sem.academicyearid = %s" in b
    assert "la.status IN ('Draft', 'Published')" in b
    assert "status = 'Archived'" in b
    assert "is_active = FALSE" in b

def test_delete_ay_is_blocked_when_local_scheduler_records_exist():
    b=block("delete_ay","upsert_ay")
    assert "FROM public.local_arrangement la" in b
    assert "JOIN public.semester sem ON sem.semesterid = la.semesterid" in b
    assert "It has existing Local Scheduler records" in b

def test_finalize_ay_local_cleanup_does_not_copy_local_to_history():
    b=block("_archive_local_for_academic_year","_local_semester_is_active")
    assert "historical_data" not in b

def test_local_creation_is_limited_to_current_active_semester():
    b=block("api_save_local_arrangement","api_publish_local_arrangement")
    assert "_local_semester_is_active(cur, sem_id)" in b
    assert "currently active semester" in b
    assert b.index("_local_semester_is_active(cur, sem_id)") < b.index("INSERT INTO public.local_arrangement")

def test_local_creation_still_requires_published_official_source():
    b=block("api_save_local_arrangement","api_publish_local_arrangement")
    assert "sv.status = 'Published'" in b
    assert "Local Scheduler requires a Published Official Schedule" in b

def test_future_official_preparation_is_not_blocked_by_local_guard():
    b=block("api_save_local_arrangement","api_publish_local_arrangement")
    # Guard is scoped to Local save only; it does not alter Official generation/publish endpoints.
    assert "Local Scheduler can only create arrangements for the currently active semester." in b
    assert "UPDATE schedule_version SET status" not in b

def test_restore_requires_current_published_official_not_stale_reference():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "Cannot restore because this section has no current Published Official Schedule." in b
    assert "if pub_row else src['ref_versionid']" not in b

def test_official_republish_archives_section_local_state():
    b=block("_archive_local_for_official_republish","_archive_local_for_academic_year")
    assert "status = 'Archived'" in b
    assert "is_active = FALSE" in b
    assert "la.sectionid = %s" in b
    assert "la.status IN ('Draft', 'Published')" in b

def test_official_publish_reconciles_local_before_archiving_old_official():
    s=APP.index("def api_approve_schedule")
    e=APP.index("@app.route",s+20)
    b=APP[s:e]
    # Local state is reconciled (per subject) before the old Official snapshot is archived.
    assert "_plan_local_for_official_republish(" in b
    assert "UPDATE public.schedule_version sv" in b
    assert b.index("_plan_local_for_official_republish(") < b.index("UPDATE public.schedule_version sv")
    # ...and kept Local sessions are re-linked only after the new snapshot exists.
    assert b.index("_insert_batch(cur, complete_snapshot") < b.index("_rebind_local_after_official_republish(")

def test_official_republish_rebinds_only_exact_unique_matches():
    # Kept Local sessions (subjects NOT in the publish) are re-linked to the new Official
    # occurrence with the identical subject/day/time/room — and only when that match is
    # unique; otherwise the arrangement is archived instead of being silently re-bound.
    b=block("_rebind_local_after_official_republish","_archive_local_arrangements")
    assert "(code, r['o_day'], r['o_start'], r['o_end'], r['o_room'])" in b
    assert "if len(hits) != 1:" in b
    assert "failed.append(arr_id)" in b
    assert "SET ref_versionid = %s" in b

def test_official_republish_local_cleanup_is_section_scoped():
    b=block("_archive_local_for_official_republish","_archive_local_for_academic_year")
    assert "UPPER(la.programcode) = UPPER(%s)" in b
    assert "la.yearlevel = %s" in b
    assert "la.semesterid = %s" in b
    assert "la.sectionid = %s" in b

def test_official_republish_returns_local_archive_notice():
    s=APP.index("def api_approve_schedule")
    e=APP.index("@app.route", s+20)
    b=APP[s:e]
    assert "local_republish_notice = None" in b
    assert "existing Local Scheduler arrangement" in b
    assert "'local_scheduler_notice': local_republish_notice" in b

def test_official_republish_notice_is_conditional():
    s=APP.index("def api_approve_schedule")
    e=APP.index("@app.route", s+20)
    b=APP[s:e]
    assert "if archived_local_count:" in b
    assert "local_republish_notice = None" in b

def test_official_publish_response_exposes_archived_local_count():
    s=APP.index("def api_approve_schedule")
    e=APP.index("@app.route", s+20)
    b=APP[s:e]
    assert "'archived_local_arrangements': archived_local_count" in b

def test_republish_notice_explains_why_local_was_archived():
    s=APP.index("def api_approve_schedule")
    e=APP.index("@app.route", s+20)
    b=APP[s:e]
    assert "because" in b
    assert "their subjects were republished in the Official schedule" in b

def test_local_archive_history_schema_records_reason_and_time():
    s=APP.index("def _ensure_local_tables")
    e=APP.index("@app.route('/api/local", s)
    b=APP[s:e]
    assert "ADD COLUMN IF NOT EXISTS archive_reason TEXT" in b
    assert "ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP" in b

def test_local_archive_paths_record_distinct_reasons():
    assert "Semester became inactive" in APP
    assert "Academic Year finalized" in APP
    assert "Official schedule for this section was republished" in APP
    assert "Superseded by a newer Local Scheduler publication" in APP
    assert "Superseded by a newer approved Schedule Adjustment" in APP

def test_archived_local_history_is_not_hidden_by_active_filter():
    s=APP.index("def api_get_local_arrangements")
    e=APP.index("@app.route('/api/local/arrangement/<int:arr_id>')", s)
    b=APP[s:e]
    assert "if status_filter != 'Archived':" in b
    assert "'archive_reason':" in b
    assert "'archived_at':" in b

def test_local_archive_history_does_not_reactivate_records():
    s=APP.index("def api_get_local_arrangements")
    e=APP.index("@app.route('/api/local/arrangement/<int:arr_id>')", s)
    b=APP[s:e]
    assert "UPDATE public.local_arrangement" not in b

def test_archive_restore_policy_blocks_obsolete_official_identity():
    b=block("_local_archive_is_restorable","_local_semester_is_active")
    assert "Semester became inactive" in b
    assert "Academic Year finalized" in b
    assert "Official schedule for this section was republished" in b

def test_restore_endpoint_enforces_archive_reason_policy():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "_local_archive_is_restorable(src.get('archive_reason'))" in b
    assert "cannot be restored because" in b

def test_archived_list_exposes_restore_capability():
    s=APP.index("def api_get_local_arrangements")
    e=APP.index("@app.route('/api/local/arrangement/<int:arr_id>')", s)
    b=APP[s:e]
    assert "'can_restore':" in b
    assert "_local_archive_is_restorable" in b

def test_superseded_local_history_remains_restore_candidate():
    b=block("_local_archive_is_restorable","_local_semester_is_active")
    assert "Superseded by a newer Local Scheduler publication" not in b
    assert "Superseded by a newer approved Schedule Adjustment" not in b

def test_local_detail_exposes_archive_restore_explanation_data():
    s=APP.index("def api_get_local_arrangement(")
    e=APP.index("@app.route", s+20)
    b=APP[s:e]
    assert "'archive_reason':" in b
    assert "'archived_at':" in b
    assert "'can_restore':" in b
    assert "_local_archive_is_restorable" in b

def test_restore_policy_is_backend_enforced_not_ui_only():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "_local_archive_is_restorable(src.get('archive_reason'))" in b
    assert "cannot be restored because" in b

def test_manual_local_deactivation_is_auditable_archive_not_delete():
    b=block("api_deactivate_local_arrangement","_archive_local_for_inactive_semesters")
    assert "status = 'Archived'" in b
    assert "is_active = FALSE" in b
    assert "archive_reason = 'Manually archived by Academic Head'" in b
    assert "archived_at = CURRENT_TIMESTAMP" in b
    assert "DELETE FROM public.local_arrangement" not in b
    assert "DELETE FROM public.local_arrangement_sessions" not in b

def test_archived_local_history_cannot_be_removed_by_deactivate_endpoint():
    b=block("api_deactivate_local_arrangement","_archive_local_for_inactive_semesters")
    assert "if arr.get('status') == 'Archived':" in b
    assert "Archived Local Scheduler history cannot be removed." in b

def test_manual_archive_remains_restore_candidate():
    b=block("_local_archive_is_restorable","_local_semester_is_active")
    assert "Manually archived by Academic Head" not in b

def test_local_scheduler_api_auth_helper_requires_academic_head():
    b=block("_require_academic_head_api","_archive_local_for_inactive_semesters")
    assert "'loggedin' not in session" in b
    assert "session.get('role') != 'Academic Head'" in b
    assert "Authentication required." in b
    assert "Academic Head access required." in b
    assert "401" in b
    assert "403" in b

def test_all_local_scheduler_endpoints_enforce_academic_head_role():
    endpoints = [
        "api_save_local_arrangement",
        "api_get_local_arrangements",
        "api_get_local_arrangement",
        "api_deactivate_local_arrangement",
        "api_publish_local_arrangement",
        "api_restore_local_arrangement",
        "api_local_check_room_conflicts",
    ]
    for fn in endpoints:
        s=APP.index("def " + fn)
        try:
            e=APP.index("@app.route", s+20)
        except ValueError:
            e=len(APP)
        b=APP[s:e]
        assert "_require_academic_head_api()" in b, fn
        assert "if auth_error:" in b, fn

def test_local_scheduler_management_has_no_loggedin_only_endpoint():
    endpoints = [
        "api_save_local_arrangement",
        "api_get_local_arrangements",
        "api_get_local_arrangement",
        "api_deactivate_local_arrangement",
        "api_publish_local_arrangement",
        "api_restore_local_arrangement",
        "api_local_check_room_conflicts",
    ]
    for fn in endpoints:
        s=APP.index("def " + fn)
        try:
            e=APP.index("@app.route", s+20)
        except ValueError:
            e=len(APP)
        b=APP[s:e]
        assert "session.get('role')" not in b or "_require_academic_head_api()" in b

def test_local_optimistic_concurrency_helper_requires_matching_timestamp():
    b=block("_local_expected_updated_at_matches","_local_archive_is_restorable")
    assert "if not expected_updated_at:" in b
    assert "actual.isoformat()" in b
    assert "actual_iso == str(expected_updated_at)" in b

def test_publish_rejects_stale_local_arrangement():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert "expected_updated_at" in b
    assert "_local_expected_updated_at_matches(arr, expected_updated_at)" in b
    assert "STALE_LOCAL_ARRANGEMENT" in b
    assert "FOR UPDATE" in b

def test_archive_rejects_stale_local_arrangement():
    b=block("api_deactivate_local_arrangement","_require_academic_head_api")
    assert "expected_updated_at" in b
    assert "_local_expected_updated_at_matches(arr, expected_updated_at)" in b
    assert "STALE_LOCAL_ARRANGEMENT" in b
    assert "FOR UPDATE" in b

def test_restore_rejects_stale_local_history_and_locks_source():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "expected_updated_at" in b
    assert "_local_expected_updated_at_matches(src, expected_updated_at)" in b
    assert "STALE_LOCAL_ARRANGEMENT" in b
    assert "FOR UPDATE" in b

def test_local_draft_fingerprint_is_canonical_and_content_sensitive():
    b=block("_local_draft_fingerprint","_local_expected_updated_at_matches")
    assert "hashlib.sha256" in b
    assert "canonical_sessions = sorted" in b
    assert "'sectionid': sectionid" in b
    assert "'ref_versionid': ref_versionid" in b
    assert "'reason':" in b

def test_local_schema_has_partial_unique_draft_fingerprint_index():
    b=block("_ensure_local_tables","api_save_local_arrangement")
    assert "draft_fingerprint VARCHAR(64)" in b
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_local_active_draft_fingerprint" in b
    assert "WHERE status = 'Draft' AND draft_fingerprint IS NOT NULL" in b

def test_identical_local_draft_submission_is_idempotent():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    assert "draft_fingerprint = _local_draft_fingerprint" in b
    assert "AND draft_fingerprint = %s" in b
    assert "'duplicate': True" in b
    assert "reason, draft_fingerprint, is_active" in b

def test_duplicate_protection_keeps_content_dimensions_in_fingerprint():
    b=block("_local_draft_fingerprint","_local_expected_updated_at_matches")
    for field in ["subjectcode", "daydesc", "starttimeid", "endtimeid",
                  "roomid", "faculty", "official_sessionid"]:
        assert ("'%s'" % field) in b

def test_simultaneous_identical_draft_unique_collision_is_recovered():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    assert 'SAVEPOINT local_draft_insert' in b
    assert "except psycopg2.errors.UniqueViolation:" in b
    assert "ROLLBACK TO SAVEPOINT local_draft_insert" in b
    assert "AND draft_fingerprint = %s" in b
    assert "'race_recovered': True" in b

def test_race_recovery_does_not_swallow_unrelated_insert_errors():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    assert "if not raced_draft:" in b
    assert "raise" in b

def test_database_allows_only_one_active_published_local_per_scope():
    b=block("_ensure_local_tables","api_save_local_arrangement")
    assert "uq_local_one_active_published_scope" in b
    assert "(UPPER(programcode), yearlevel, sectionid, semesterid)" in b
    assert "WHERE status = 'Published' AND is_active = TRUE" in b

def test_publish_serializes_same_scope_with_transaction_advisory_lock():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert "pg_advisory_xact_lock(hashtext(%s))" in b
    assert "scope_lock_key" in b
    assert "programcode" in b
    assert "yearlevel" in b
    assert "sectionid" in b
    assert "semesterid" in b

def test_publish_rereads_draft_after_scope_lock():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    lock_pos=b.index("pg_advisory_xact_lock")
    reread_pos=b.index("SELECT * FROM public.local_arrangement", lock_pos)
    assert reread_pos > lock_pos
    assert "LOCAL_PUBLISH_RACE" in b

def test_publish_unique_invariant_collision_returns_409_not_generic_500():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert "except psycopg2.errors.UniqueViolation:" in b
    assert "'code': 'LOCAL_PUBLISH_RACE'" in b
    assert "}), 409" in b

def test_restore_uses_same_canonical_draft_fingerprint_protection():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "draft_fingerprint = _local_draft_fingerprint" in b
    assert 'restore_reason = f"Restored from arrangement #{arr_id}"' in b
    assert "AND draft_fingerprint = %s" in b
    assert "'duplicate': True" in b

def test_restore_fingerprint_includes_source_sessions_and_current_official_version():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "fingerprint_sessions" in b
    assert "'official_sessionid': s.get('official_sessionid')" in b
    assert "src['semesterid'], ref_versionid, restore_reason" in b

def test_simultaneous_identical_restore_unique_collision_is_recovered():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "SAVEPOINT local_restore_insert" in b
    assert "except psycopg2.errors.UniqueViolation:" in b
    assert "ROLLBACK TO SAVEPOINT local_restore_insert" in b
    assert "'race_recovered': True" in b

def test_restore_unique_collision_does_not_swallow_unrelated_error():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "if not raced_restore:" in b
    assert "raise" in b

def test_official_session_binding_validator_checks_full_scope():
    b=block("_validate_local_official_session_binding","_local_draft_fingerprint")
    # Bound to the anchor's whole Published snapshot (one version row per subject).
    for term in ["ss.sessionid = %s", "_OFFICIAL_SNAPSHOT_OF_ANCHOR",
                 "s.sectionid = %s", "s.semesterid = %s",
                 "UPPER(c.programcode) = UPPER(%s)", "cs.yearlevel = %s",
                 "UPPER(cs.subjectcode) = UPPER(%s)"]:
        assert term in b

def test_save_requires_valid_current_official_occurrence_binding():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    assert "_validate_local_official_session_binding(" in b
    assert "INVALID_OFFICIAL_SESSION_BINDING" in b
    assert "missing, obsolete, or cross-scope" in b

def test_publish_revalidates_every_official_occurrence_binding():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert "_validate_local_official_session_binding(" in b
    assert "latest_official_versionid" in b
    assert "INVALID_OFFICIAL_SESSION_BINDING" in b

def test_restore_cannot_copy_obsolete_official_occurrence_binding():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "_validate_local_official_session_binding(" in b
    assert "can no longer be restored" in b
    assert "INVALID_OFFICIAL_SESSION_BINDING" in b

def test_conflict_validation_does_not_trust_cross_scope_occurrence_id():
    s=APP.index("def api_local_check_room_conflicts")
    try:
        e=APP.index("@app.route", s+20)
    except ValueError:
        e=len(APP)
    b=APP[s:e]
    assert "_validate_local_official_session_binding(" in b
    assert "current_official['versionid']" in b
    assert "INVALID_OFFICIAL_SESSION_BINDING" in b

def test_local_session_structure_validator_rejects_missing_and_invalid_assignments():
    b=block("_validate_local_session_structure","_validate_local_official_session_binding")
    assert "if not subject or not day:" in b
    assert "start_id >= end_id" in b
    assert "SELECT 1 FROM public.room WHERE roomid = %s" in b
    assert "FROM public.timeslot" in b

def test_save_rejects_structurally_invalid_local_sessions():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    assert "_validate_local_session_structure(cur, sess)" in b
    assert "INVALID_LOCAL_SESSION_STRUCTURE" in b
    assert "DUPLICATE_OFFICIAL_OCCURRENCE" in b

def test_publish_revalidates_structure_and_duplicate_occurrence_mapping():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert "_validate_local_session_structure(cur, ds)" in b
    assert "published_occurrence_ids" in b
    assert "DUPLICATE_OFFICIAL_OCCURRENCE" in b

def test_restore_rejects_malformed_or_duplicate_archived_sessions():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "_validate_local_session_structure(cur, ss)" in b
    assert "restored_occurrence_ids" in b
    assert "INVALID_LOCAL_SESSION_STRUCTURE" in b
    assert "DUPLICATE_OFFICIAL_OCCURRENCE" in b

def test_local_occurrence_set_is_a_partial_override_of_the_current_snapshot():
    # A Local Arrangement lists only the occurrences it overrides; each must be
    # unique and belong to the current Published snapshot of the same section.
    b=block("_validate_local_occurrence_coverage","_validate_local_session_structure")
    assert "SELECT ss.sessionid" in b
    assert "ss.sessionid = ANY(%s)" in b
    assert "_OFFICIAL_SNAPSHOT_OF_ANCHOR" in b
    assert "s.sectionid = %s" in b
    assert "len(local_ids) != len(set(local_ids))" in b
    assert "set(official_ids)" not in b          # whole-section equality is gone

def test_save_validates_its_overridden_occurrence_set():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    assert "_validate_local_occurrence_coverage(" in b
    assert "official_version['versionid']" in b
    assert "INVALID_LOCAL_OCCURRENCE_SET" in b

def test_publish_rechecks_overridden_occurrences_against_latest_official():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert "_validate_local_occurrence_coverage(" in b
    assert "latest_official_versionid" in b
    assert "INVALID_LOCAL_OCCURRENCE_SET" in b

def test_restore_requires_archived_set_to_match_current_official_exactly():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "_validate_local_occurrence_coverage(" in b
    assert "ref_versionid" in b
    assert "no longer matches the current Official schedule" in b

def test_exact_official_occurrence_faculty_lookup_uses_full_scope():
    b=block("_get_official_occurrence_faculty","_validate_local_official_session_binding")
    # Bound to the anchor's whole Published snapshot (one version row per subject).
    for term in ["ss.sessionid = %s", "_OFFICIAL_SNAPSHOT_OF_ANCHOR",
                 "s.sectionid = %s", "s.semesterid = %s",
                 "UPPER(c.programcode) = UPPER(%s)", "cs.yearlevel = %s",
                 "UPPER(cs.subjectcode) = UPPER(%s)", "s.employeenumber"]:
        assert term in b

def test_save_inherits_faculty_from_exact_official_occurrence():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    assert "_get_official_occurrence_faculty(" in b
    assert "requested_faculty != official_faculty" in b
    assert "OFFICIAL_FACULTY_IMMUTABLE" in b
    assert "emp_num = official_faculty" in b

def test_publish_revalidates_persisted_faculty_against_current_official():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert "_get_official_occurrence_faculty(" in b
    assert "stored_faculty != official_faculty" in b
    assert "OFFICIAL_FACULTY_IMMUTABLE" in b

def test_restore_reinherits_current_official_faculty_not_archived_faculty():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "current_official_faculty" in b
    assert "ss['faculty_employeenumber'] = current_official_faculty" in b
    assert "INVALID_OFFICIAL_FACULTY_BINDING" in b

def test_conflict_check_ignores_client_faculty_and_uses_official_occurrence():
    s=APP.index("def api_local_check_room_conflicts")
    try:
        e=APP.index("@app.route", s+20)
    except ValueError:
        e=len(APP)
    b=APP[s:e]
    assert "candidate_official_faculty" in b
    assert "_get_official_occurrence_faculty(" in b
    assert "cnd['employeenumber'] = candidate_official_faculty" in b

def test_protected_identity_validator_limits_local_edits_to_day_time_room():
    b=block("_validate_local_protected_identity","_get_official_occurrence_faculty")
    for term in ["subjectcode", "sectionid", "semesterid", "programcode", "yearlevel"]:
        assert term in b
    assert "may change only Day, Time, and Room" in b

def test_save_rejects_crafted_protected_identity_changes():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    assert "_validate_local_protected_identity(" in b
    assert "PROTECTED_LOCAL_IDENTITY_IMMUTABLE" in b

def test_publish_revalidates_protected_identity_against_current_official():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert "publish_identity_ok" in b
    assert "_validate_local_protected_identity(" in b
    assert "PROTECTED_LOCAL_IDENTITY_IMMUTABLE" in b

def test_restore_rejects_archived_identity_that_no_longer_matches_official():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "restore_identity_ok" in b
    assert "_validate_local_protected_identity(" in b
    assert "PROTECTED_LOCAL_IDENTITY_IMMUTABLE" in b

def test_local_editable_dimension_policy_validates_day_time_room():
    b=block("_validate_local_editable_dimensions","_validate_local_session_structure")
    assert "day_aliases" in b
    assert "FROM public.timeslot" in b
    assert "SELECT roomtype FROM public.room" in b
    assert "hc_lab_session_enabled" in b
    assert "hc_weekend_enabled" in b
    assert "hc_weekend_subject" in b

def test_structural_time_validation_uses_timeslot_values_not_numeric_id_order():
    b=block("_validate_local_session_structure","_validate_local_protected_identity")
    assert "FROM public.timeslot" in b
    assert "time_rows[start_id] >= time_rows[end_id]" in b
    assert "public.time_slot" not in b

def test_save_rejects_invalid_local_day_time_room_policy():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    assert "_validate_local_editable_dimensions(" in b
    assert "INVALID_LOCAL_EDITABLE_DIMENSIONS" in b

def test_publish_rechecks_local_day_time_room_policy():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert "publish_editable_ok" in b
    assert "_validate_local_editable_dimensions(" in b
    assert "INVALID_LOCAL_EDITABLE_DIMENSIONS" in b

def test_phase_z_conflict_preview_uses_resolved_semester_id():
    s=APP.index("def api_local_check_room_conflicts")
    try: e=APP.index("@app.route", s+20)
    except ValueError: e=len(APP)
    b=APP[s:e]
    assert "_current_official_anchor(cur, program, year_level, section_id, sem_id)" in b
    assert "semester_id, section_id)" not in b

def test_phase_z_effective_conflicts_use_official_and_active_published_local_only():
    s=APP.index("def api_local_check_room_conflicts")
    try: e=APP.index("@app.route", s+20)
    except ValueError: e=len(APP)
    b=APP[s:e]
    assert "sv.status='Published'" in b
    assert "la.status='Published'" in b
    assert "la.is_active=TRUE" in b
    assert "la.status IN ('Published','Draft')" not in b
    assert "la_x.status='Published'" in b
    assert "la_x.is_active=TRUE" in b
    assert "las_x.official_sessionid=ss.sessionid" in b

def test_phase_z_publish_rechecks_active_published_local_occupancy():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert "la.status = 'Published'" in b
    assert "la.is_active = TRUE" in b
    assert "la.status IN ('Published', 'Draft')" not in b
    assert "ss.sessionid <> %s" in b

def test_phase_68a_save_serializes_same_scope_before_official_snapshot():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    lock=b.index("pg_advisory_xact_lock")
    official=b.index("Local Scheduler requires a Published Official Schedule")
    assert lock < official
    assert "local-scope|" in b

def test_phase_68a_publish_locks_draft_and_scope():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert "FOR UPDATE" in b
    assert "pg_advisory_xact_lock(hashtext(%s))" in b
    assert "local-scope|" in b
    assert "uq_local_one_active_published_scope" in APP

def test_phase_68a_publish_rejects_stale_official_reference():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    # Stale = a different Official SNAPSHOT, not merely another subject's versionid.
    assert "_same_official_snapshot(cur, arr.get('ref_versionid'), latest_official_versionid)" in b
    assert "STALE_OFFICIAL_SCHEDULE" in b
    assert "create a new Draft from the current Official Schedule" in b

def test_phase_68a_restore_uses_same_scope_lock_and_current_official():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "restore_scope_lock_key" in b
    assert "local-scope|" in b
    assert "pg_advisory_xact_lock(hashtext(%s))" in b
    assert "_current_official_anchor(cur, src['programcode']" in b
    assert "ref_versionid = pub_row['versionid']" in b

def test_phase_68a_draft_insert_and_restore_keep_unique_race_recovery():
    save=block("api_save_local_arrangement","api_get_local_arrangements")
    restore=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "SAVEPOINT local_draft_insert" in save
    assert "except psycopg2.errors.UniqueViolation" in save
    assert "SAVEPOINT local_restore_insert" in restore
    assert "except psycopg2.errors.UniqueViolation" in restore

def test_phase_68b_official_republish_archives_local_and_displacement_state():
    b=block("_archive_local_for_official_republish","_archive_local_for_academic_year")
    assert "status = 'Archived'" in b
    assert "archive_reason = 'Official schedule for this section was republished'" in b
    assert "la.status IN ('Draft', 'Published')" in b
    assert "UPDATE public.local_displaced_subjects" in b
    assert "SET is_active = FALSE" in b

def test_phase_68b_official_publish_uses_same_local_scope_lock_before_archive():
    s=APP.index("def api_approve_schedule")
    p=APP.index("_local_plan = _plan_local_for_official_republish(", s)
    b=APP[s:p]
    assert "official_scope_lock_key" in b
    assert "local-scope|" in b
    assert "pg_advisory_xact_lock(hashtext(%s))" in b
    assert b.rindex("pg_advisory_xact_lock") > b.index("official_scope_lock_key")

def test_phase_68b_official_publish_requires_exact_section_for_safe_sync():
    s=APP.index("def api_approve_schedule")
    p=APP.index("_local_plan = _plan_local_for_official_republish(", s)
    b=APP[s:p]
    assert "if not _ctx_section_id:" in b
    assert "OFFICIAL_SECTION_REQUIRED" in b

def test_phase_68b_legacy_local_state_can_fail_closed_when_official_ref_obsolete():
    b=block("_archive_local_with_obsolete_official_refs","_archive_local_for_inactive_semesters")
    assert "la.ref_versionid IS NULL" in b
    assert "sv.status = 'Published'" in b
    assert "Referenced Official schedule is no longer current" in b

def test_phase_68b_obsolete_official_archive_is_not_restorable():
    b=block("_local_archive_is_restorable","_local_semester_is_active")
    assert "'Referenced Official schedule is no longer current'" in b

def test_phase_68d_has_transaction_aware_audit_writer():
    s=APP.index("def _write_activity_log_tx")
    e=APP.index("# Category", s)
    b=APP[s:e]
    assert "INSERT INTO activity_log" in b
    assert "initiated_by" in b
    assert "cur.execute" in b

def test_phase_68d_successful_local_draft_creation_is_audited():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    assert '"Created Local Draft"' in b
    assert "Official version #" in b
    assert "_write_activity_log_tx(" in b

def test_phase_68d_publish_audit_is_atomic_and_records_reason_and_official_version():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert '"Published Local Arrangement"' in b
    assert "Official version #{latest_official_versionid}" in b
    assert "Reason: {publish_reason}" in b
    assert b.index('"Published Local Arrangement"') < b.rindex("conn.commit()")

def test_phase_68d_constraint_override_publication_has_distinct_audit_event():
    b=block("api_publish_local_arrangement","api_restore_local_arrangement")
    assert '"Published Local Constraint Override"' in b
    assert "arr.get('has_hc_violation')" in b
    assert "Override reason:" in b

def test_phase_68d_restore_audit_records_source_and_new_draft():
    b=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert '"Restored Local Arrangement"' in b
    assert "history arrangement #{arr_id} copied to new Local Draft #{new_id}" in b

def test_phase_68d_official_republish_invalidation_is_audited():
    b=block("_archive_local_for_official_republish","_archive_local_for_academic_year")
    assert '"Archived Local Arrangements After Official Republish"' in b
    assert "archived_count" in b
    assert "Official schedule was republished" in b

def test_phase_68e_academic_head_guard_is_server_side():
    b=block("_require_academic_head_api","_validate_local_scope_binding")
    assert "if 'loggedin' not in session" in b
    assert "session.get('role') != 'Academic Head'" in b
    assert "401" in b and "403" in b

def test_phase_68e_all_core_local_endpoints_call_academic_head_guard():
    for fn, nxt in [
        ("api_get_local_arrangements","api_save_local_arrangement"),
        ("api_save_local_arrangement","api_get_local_arrangements"),
        ("api_publish_local_arrangement","api_restore_local_arrangement"),
        ("api_restore_local_arrangement","api_local_check_room_conflicts"),
    ]:
        s=APP.index("def "+fn)
        try: e=APP.index("@app.route", s+20)
        except ValueError: e=len(APP)
        assert "_require_academic_head_api()" in APP[s:e]
    s=APP.index("def api_local_check_room_conflicts")
    assert "_require_academic_head_api()" in APP[s:]

def test_phase_68e_conflict_check_validates_exact_client_scope():
    s=APP.index("def api_local_check_room_conflicts")
    try: e=APP.index("@app.route",s+20)
    except ValueError: e=len(APP)
    b=APP[s:e]
    assert "_validate_local_scope_binding(" in b
    assert "INVALID_LOCAL_SCOPE" in b
    assert "section_id, sem_id, program, year_level" in b
    assert "section_id, semester_id, program, year_level" not in b

def test_phase_68e_scope_binding_checks_section_program_year_and_academic_year():
    b=block("_validate_local_scope_binding","_safe_local_api_error")
    assert "sec.sectionid = %s" in b
    assert "UPPER(pyl.programcode) = UPPER(%s)" in b
    assert "pyl.yearlevel = %s" in b
    assert "pyl.academicyearid = sem.academicyearid" in b

def test_phase_68e_local_api_uses_generic_internal_error_response():
    s=APP.index("def _safe_local_api_error")
    e=APP.index("\ndef ", s+5)
    b=APP[s:e]
    assert "str(e)" not in b
    assert "could not be completed" in b
    for fn in ["api_get_local_arrangements","api_save_local_arrangement",
               "api_publish_local_arrangement","api_restore_local_arrangement"]:
        s=APP.index("def "+fn)
        try:e=APP.index("@app.route",s+20)
        except ValueError:e=len(APP)
        assert "error': str(e)" not in APP[s:e]

def test_phase_68e_history_status_filter_is_whitelisted():
    s=APP.index("def api_get_local_arrangements")
    e=APP.index("@app.route",s+20)
    b=APP[s:e]
    assert "status_filter not in ('', 'Draft', 'Published', 'Archived')" in b
    assert "INVALID_LOCAL_STATUS" in b

def test_phase_68f_inactive_rooms_are_rejected_by_local_validators():
    s=APP.index("def _validate_local_session_structure")
    e=APP.index("\ndef ", s+5)
    structural=APP[s:e]
    s=APP.index("def _validate_local_editable_dimensions")
    e=APP.index("\ndef ", s+5)
    editable=APP[s:e]
    assert "COALESCE(isactive, TRUE) = TRUE" in structural
    assert "does not exist or is inactive" in structural
    assert "COALESCE(isactive, TRUE) = TRUE" in editable

def test_phase_68f_conflict_check_fails_closed_on_malformed_day_or_time():
    s=APP.index("def api_local_check_room_conflicts")
    try:e=APP.index("@app.route",s+20)
    except ValueError:e=len(APP)
    b=APP[s:e]
    assert "if not day or st is None or en is None:" in b
    assert "INVALID_LOCAL_SESSION_STRUCTURE" in b
    assert "continue" not in b[b.index("if not day or st is None or en is None:"):b.index("rid=x.get", b.index("if not day or st is None or en is None:"))]

def test_phase_68f_conflict_check_requires_current_published_official():
    s=APP.index("def api_local_check_room_conflicts")
    try:e=APP.index("@app.route",s+20)
    except ValueError:e=len(APP)
    b=APP[s:e]
    assert "if not current_official:" in b
    assert "NO_CURRENT_OFFICIAL_SCHEDULE" in b

def test_phase_68f_conflict_check_reuses_canonical_editable_validation():
    s=APP.index("def api_local_check_room_conflicts")
    try:e=APP.index("@app.route",s+20)
    except ValueError:e=len(APP)
    b=APP[s:e]
    assert "_validate_local_session_structure(cur, normalized_candidate)" in b
    assert "_validate_local_editable_dimensions(" in b
    assert "candidate_official_faculty" in b

def test_phase_68f_save_still_rejects_duplicate_and_incomplete_occurrence_coverage():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    assert "DUPLICATE_OFFICIAL_OCCURRENCE" in b
    assert "INVALID_LOCAL_OCCURRENCE_SET" in b
    assert "INVALID_OFFICIAL_SESSION_BINDING" in b

def test_phase_68g_save_audit_uses_real_backend_variables():
    b=block("api_save_local_arrangement","api_get_local_arrangements")
    assert "Local Draft #{arr_id} created" in b
    assert "Official version #{official_version['versionid']}" in b
    assert "Local Draft #{arrangementid} created" not in b
    assert "Official version #{ref_versionid}" not in b
    assert "error': str(exc)" not in b

def test_phase_68g_frontend_save_publish_contract_is_consistent():
    from pathlib import Path
    html=(Path(__file__).parents[1]/"templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
    assert "async function saveLocalArrangement(skipModal = false, options = {})" in html
    assert "window.__lastLocalArrangementSaveResult = data" in html
    assert "data.arrangement_id" in html
    assert "data.arrangementid" not in html
    assert "const draft = await saveLocalArrangement(true" in html
    assert "silent: true" in html

def test_phase_68g_publish_from_editor_has_network_and_double_click_protection():
    from pathlib import Path
    html=(Path(__file__).parents[1]/"templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
    s=html.index("async function publishLocalArrangementFromEditor()")
    e=html.index("async function publishLocalArrangement(",s)
    b=html[s:e]
    assert "if (btn?.disabled) return;" in b
    assert "try {" in b and "finally {" in b
    assert "btn.disabled = true" in b
    assert "btn.disabled = false" in b
    # Network failures are classified by _localApiRequest, not a catch-all here.
    assert "_publishLocalArrangementRequest(draftId, cleanReason)" in b
    assert "return { kind: 'network'" in html

def test_phase_68g_conflict_save_publish_restore_use_matching_api_routes():
    from pathlib import Path
    html=(Path(__file__).parents[1]/"templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
    assert "_localApiRequest('/api/local/check_room_conflicts'" in html
    assert "_localApiRequest('/api/local/save_arrangement'" in html
    assert "_localApiRequest(`/api/local/arrangement/${arrId}/publish`" in html
    assert "_localApiRequest(`/api/local/arrangement/${arrId}/restore`" in html

def test_phase_68h_final_local_lifecycle_contract():
    save=block("api_save_local_arrangement","api_get_local_arrangements")
    publish=block("api_publish_local_arrangement","api_restore_local_arrangement")
    restore=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "FALSE, 'Draft'" in save
    assert "SET status = 'Published'" in publish
    assert "SET status = 'Archived'" in publish
    assert "FALSE, 'Draft'" in restore
    assert "restored_from_arrangementid" in restore

def test_phase_68h_final_effective_schedule_contract():
    s=APP.index("def api_local_check_room_conflicts")
    try:e=APP.index("@app.route",s+20)
    except ValueError:e=len(APP)
    b=APP[s:e]
    assert "sv.status='Published'" in b
    assert "la.status='Published'" in b
    assert "la.is_active=TRUE" in b
    assert "la_x.status='Published'" in b
    assert "las_x.official_sessionid=ss.sessionid" in b

def test_phase_68h_final_stale_and_concurrency_guards_remain_present():
    save=block("api_save_local_arrangement","api_get_local_arrangements")
    publish=block("api_publish_local_arrangement","api_restore_local_arrangement")
    restore=block("api_restore_local_arrangement","api_local_check_room_conflicts")
    assert "local-scope|" in save
    assert "local-scope|" in publish
    assert "local-scope|" in restore
    assert "STALE_OFFICIAL_SCHEDULE" in publish
    assert "FOR UPDATE" in publish
    assert "FOR UPDATE" in restore

def test_phase_68h_final_frontend_publish_chain_matches_backend_contract():
    from pathlib import Path
    html=(Path(__file__).parents[1]/"templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
    assert "const draft = await saveLocalArrangement(true" in html
    assert "draft.arrangement_id" in html
    assert "/publish`" in html
    assert "silent: true" in html
    assert "refreshScheduleGrid" in html

def test_phase_68h_no_raw_internal_exception_text_in_core_local_mutations():
    for fn in ["api_save_local_arrangement","api_publish_local_arrangement",
               "api_restore_local_arrangement","api_local_check_room_conflicts"]:
        s=APP.index("def "+fn)
        try:e=APP.index("@app.route",s+20)
        except ValueError:e=len(APP)
        b=APP[s:e]
        assert "error': str(e)" not in b
        assert "error': str(exc)" not in b

def test_phase_68h_archived_history_and_official_republish_guards_remain_present():
    schema=block("_ensure_local_tables","_ensure_program_name_history_table")
    republish=block("_archive_local_for_official_republish","_archive_local_for_academic_year")
    assert "trg_local_archive_immutable" in schema
    assert "trg_local_archived_sessions_immutable" in schema
    assert "Official schedule for this section was republished" in republish
    assert "UPDATE public.local_displaced_subjects" in republish

