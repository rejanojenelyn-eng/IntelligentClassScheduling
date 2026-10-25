"""Route access policy: who may call each Flask endpoint (app._access_gate enforces it).

Deny by default:
  - PUBLIC_ENDPOINTS are the only endpoints reachable without signing in.
  - Every other endpoint needs a signed-in session AND the session's role must be listed
    for that endpoint in ENDPOINT_ROLES. An endpoint missing from ENDPOINT_ROLES is
    refused for everyone until it is reviewed and added here.

Rules are per endpoint (the view function), not per URL prefix: '/admin/...' routes are
not all Admin-only, and '/api/...' routes are not all shared. The role checks already
inside many view functions stay in place as a second layer.

Roles are the values of accounts.role / session['role'].
"""

ADMIN = 'Admin'
HEAD = 'Academic Head'
FACULTY = 'Faculty'

_A = frozenset({ADMIN})
_H = frozenset({HEAD})
_F = frozenset({FACULTY})
_AH = frozenset({ADMIN, HEAD})
_HF = frozenset({HEAD, FACULTY})
_ALL = frozenset({ADMIN, HEAD, FACULTY})

# Reachable without signing in. Keep this list short and reviewed.
PUBLIC_ENDPOINTS = frozenset({
    'static', 'favicon',
    'index',                       # landing page; shows "Go to dashboard" when signed in
    'login', 'logout',
    'forgot_password', 'forgot_password_verify', 'forgot_password_resend', 'forgot_password_new',
    'session_page_closed',         # navigator.sendBeacon when a tab closes; does nothing without a session
    'setup',                       # seeds the first accounts ONLY while the accounts table is empty
})


def _grant(roles, *endpoints):
    return {e: roles for e in endpoints}


ENDPOINT_ROLES = {
    # ── Every signed-in user: own session, account setup, own profile ─────────────
    **_grant(_ALL,
             'session_heartbeat',
             'account_setup_password', 'account_setup_photo', 'account_setup_email',
             'account_setup_contact', 'account_setup_verify', 'account_setup_done',
             'account_setup_leave',
             'api_user_me', 'api_user_options', 'api_user_photo_upload',
             'api_user_profile_name',
             'api_user_contact_status', 'api_user_contact_request_code', 'api_user_contact_update',
             'api_user_password_eligibility', 'api_user_password_request_code',
             'api_user_password_verify_code', 'api_user_password_change',
             'api_user_email_request_code', 'api_user_email_update',
             'api_user_correction_requests', 'api_user_submit_correction_request',
             'api_user_cancel_correction_request',
             'api_search'),                       # results are filtered by role inside the view

    # ── Academic Head: own faculty view, requests, dashboard ─────────────────────
    **_grant(_H,
             'academic_my_dashboard', 'academic_my_teaching_assignment',
             'dashboard', 'api_dashboard_requests_list',
             'requests_view', 'api_requests_list', 'api_requests_validate', 'api_requests_decide',
             'api_manual_own_makeups', 'api_manual_own_makeup', 'api_manual_own_makeup_cancel'),

    # ── Academic Head: Employee Management (the Academic Head's own pages) ───────
    **_grant(_H,
             'employee', 'add_employee', 'edit_employee', 'api_add_employee', 'api_edit_employee',
             'archive_employee', 'bulk_archive', 'bulk_import', 'archived_employees',
             'acad_restore_employee', 'acad_bulk_restore',
             'acad_faculty_import_xlsx', 'acad_faculty_csv_analyze', 'acad_faculty_xlsx_analyze',
             'acad_faculty_pdf_analyze', 'acad_faculty_docx_analyze', 'acad_faculty_check_duplicates',
             'acad_faculty_restore_archived', 'acad_faculty_doc_confirm'),

    # ── Academic Head: curriculum / rooms / class schedule pages ─────────────────
    **_grant(_H,
             'curriculum', 'view_curriculum', 'room', 'room_schedule_view', 'schedule', 'reports',
             'get_offerings_schedule', 'api_schedule_list_release', 'api_schedule_list_delete',
             'api_export_generated_schedule',
             'import_schedule', 'sis_import_preview', 'schedule_unified_preview'),

    # ── Academic Head: schedule generation, manual editor, drafts, publishing ───
    **_grant(_H,
             'schedule_generation', 'schedule_generation_view', 'manual_schedule_editor',
             'schedule_drafts_list', 'schedule_draft_detail', 'schedule_version_history',
             'api_generate_schedule', 'api_check_existing_schedule', 'api_archive_draft_for_editor',
             'api_schedule_accuracy', 'api_retrieve_previous_schedule', 'api_save_draft',
             'api_delete_session', 'api_draft_sessions', 'api_validate_schedule',
             'api_designee_night_check', 'api_local_republish_impact', 'api_approve_schedule',
             'api_delete_draft', 'api_list_versions', 'api_restore_version', 'api_check_version_state',
             'api_overlay_sessions', 'api_version_sessions', 'api_load_draft', 'api_schedule_latest_draft',
             'api_get_curriculum', 'api_get_existing_schedule_periods', 'api_get_valid_semesters',
             'api_get_subjects', 'api_year_levels_by_program', 'api_dss_suggest', 'api_hc_config',
             'api_manual_subject_info', 'api_manual_faculty_info',
             'api_manual_get_assignments', 'api_manual_save_assignment', 'api_manual_delete_assignment',
             'api_manual_curriculum_lock', 'api_manual_existing_days', 'api_manual_existing_sessions',
             'api_manual_merge_context', 'api_manual_section_schedule',
             'api_faculty_teaching_assignments', 'api_faculty_assigned_list'),

    # ── Academic Head: Local Scheduler (local arrangements) ──────────────────────
    **_grant(_H,
             'local_schedule_editor', 'schedule_local_arrangements',
             'api_save_local_arrangement', 'api_get_local_arrangements', 'api_get_local_arrangement',
             'api_local_merge_divergence', 'api_deactivate_local_arrangement',
             'api_publish_local_arrangement', 'api_restore_local_arrangement',
             'api_local_check_room_conflicts', 'api_rooms_occupancy_by_day', 'api_local_faculty_sessions'),

    # ── Admin and Academic Head: shared lookups, exports, reports, rooms ─────────
    **_grant(_AH,
             'api_dashboard_analytics', 'api_dashboard_programs_list', 'api_dashboard_faculty_list',
             'api_dashboard_rooms_list', 'api_dashboard_buildings_list',
             'api_manual_faculty_schedule', 'api_manual_faculty_load',
             'room_view', 'api_add_building', 'api_add_room', 'api_edit_room', 'api_edit_building',
             'api_delete_room', 'api_delete_building',
             'admin_rooms_export_list', 'admin_rooms_export_data', 'admin_export_rooms_docx',
             'admin_export_rooms_xlsx', 'room_schedule_export_count', 'room_schedule_export',
             'export_schedule', 'schedule_export_count', 'schedule_export_multi',
             'facsub_export_count', 'facsub_export',
             'sis_import_confirm', 'schedule_import_check_existing', 'schedule_unified_analyze',
             'schedule_unified_validate',
             'export_employees_docx', 'export_employees_xlsx',
             'export_archived_employees_docx', 'export_archived_employees_xlsx',
             'admin_curriculum_export_list', 'admin_curriculum_export_data',
             'admin_export_curriculum_docx', 'admin_export_curriculum_xlsx',
             'export_assignments', 'export_program_all', 'export_single_curriculum',
             'reports_data', 'report_class_schedule_sections', 'report_preview', 'report_export'),

    # ── Admin: dashboard, user accounts, account corrections ─────────────────────
    **_grant(_A,
             'admin_dashboard', 'admin_user_management', 'account_reset_password', 'account_toggle_status',
             'admin_account_corrections', 'admin_account_correction_decide', 'admin_reports'),

    # ── Admin: Employee Management ───────────────────────────────────────────────
    **_grant(_A,
             'admin_employee', 'admin_add_employee', 'admin_edit_employee', 'admin_api_add_employee',
             'admin_api_edit_employee', 'admin_archive_employee', 'admin_bulk_archive', 'admin_bulk_import',
             'admin_archived_employees', 'admin_restore_employee', 'admin_bulk_restore',
             'admin_faculty_import_xlsx', 'admin_faculty_csv_analyze', 'admin_faculty_xlsx_analyze',
             'admin_faculty_pdf_analyze', 'admin_faculty_docx_analyze', 'admin_faculty_check_duplicates',
             'admin_faculty_restore_archived', 'admin_faculty_doc_confirm',
             'admin_export_employees_docx', 'admin_export_employees_xlsx',
             'admin_export_archived_employees_docx', 'admin_export_archived_employees_xlsx'),

    # ── Admin: rooms and buildings (form posts), curriculum management ───────────
    **_grant(_A,
             'admin_rooms', 'add_building', 'add_room', 'delete_room', 'edit_room_submit',
             'delete_building', 'edit_building',
             'admin_curriculum', 'admin_view_curriculum', 'admin_delete_curriculum', 'assign_curriculum',
             'autogenerate_assignments', 'check_curriculum_duplicate', 'curriculum_blocking_schedules',
             'import_curriculum', 'import_curriculum_xlsx', 'analyze_curriculum_csv',
             'analyze_curriculum_xlsx', 'analyze_curriculum_pdf', 'analyze_curriculum_docx_route',
             'confirm_pdf_import'),

    # ── Admin: schedule views, settings, programs/sections/offerings ─────────────
    **_grant(_A,
             'admin_schedule', 'admin_class_schedule_sis', 'admin_room_schedule',
             'admin_settings', 'admin_program_management', 'update_scheduler_config',
             'add_designation', 'update_designation', 'update_emp_type',
             'activate_period', 'finalize_ay', 'delete_ay', 'upsert_ay',
             'get_hard_constraints', 'save_hard_constraints',
             'settings_add_program', 'settings_edit_program', 'settings_delete_program',
             'settings_toggle_program_active', 'settings_toggle_program_yearlevel',
             'settings_update_program_yearlevel', 'api_program_yearlevel_auto_setup',
             'settings_add_curriculum', 'settings_edit_curriculum', 'settings_delete_curriculum',
             'settings_toggle_curriculum_active',
             'settings_add_section', 'settings_edit_section', 'settings_delete_section',
             'settings_deactivate_section',
             'settings_add_offering', 'settings_edit_offering', 'settings_delete_offering'),

    # ── Admin: merged-class load policies and merge groups ───────────────────────
    **_grant(_A,
             'api_list_merge_load_policies', 'api_create_merge_load_policy', 'api_update_merge_load_policy',
             'api_delete_merge_load_policy', 'api_merge_load_policy_groups', 'api_set_merge_load_policy_groups',
             'api_merge_load_policy_subject_preview', 'api_list_all_subjects',
             'api_list_merge_groups', 'api_create_merge_group', 'api_update_merge_group',
             'api_set_merge_group_active', 'api_delete_merge_group', 'api_merge_group_sections',
             'api_merge_group_common_subjects', 'api_create_merge_set', 'api_remove_merge_group_member',
             'api_merge_group_options', 'api_merge_group_member_candidates', 'api_merge_group_preview',
             'api_merge_group_discovery', 'api_merge_group_discovery_confirm', 'api_merge_group_dry_run'),

    # ── Admin: diagnostics (not part of any page) ────────────────────────────────
    **_grant(_A, 'debug_hist', 'schedule_diagnose',
             'admin_client_ip_diagnostics'),        # H2: shows what the hosting proxy sends

    # ── Faculty: own dashboard, schedules, requests, exports ─────────────────────
    **_grant(_F,
             'faculty_dashboard', 'faculty_teaching_assignment', 'faculty_schedule', 'faculty_room_schedule',
             'faculty_requests', 'faculty_subject_offerings',
             'api_faculty_my_requests', 'api_faculty_update_request', 'api_faculty_cancel_request',
             'api_faculty_submit_request', 'api_faculty_check_request_conflicts',
             'api_faculty_my_schedule_subjects', 'api_faculty_my_assignments', 'api_faculty_blocked_times',
             'api_faculty_schedule_full',            # Published class schedule (the Class Schedule page)
             'faculty_schedule_export_count', 'faculty_schedule_export',
             'faculty_teaching_assignment_export', 'faculty_weekly_schedule_export'),

    # ── Faculty and Academic Head (an Academic Head is also a faculty member) ───
    **_grant(_HF,
             'api_faculty_me', 'api_faculty_my_teaching_load', 'api_faculty_my_local_schedule',
             'api_faculty_my_makeups', 'api_faculty_available_rooms', 'api_sections_by_program',
             'get_room_schedule',
             'api_get_faculty_schedule'),            # Faculty: Published only (no prefer_draft), see the view
}

_overlap = PUBLIC_ENDPOINTS & set(ENDPOINT_ROLES)
assert not _overlap, f"endpoints listed as both public and role-restricted: {sorted(_overlap)}"


def allowed_roles(endpoint):
    """Roles allowed to call a non-public endpoint; empty when it has not been reviewed."""
    return ENDPOINT_ROLES.get(endpoint, frozenset())
