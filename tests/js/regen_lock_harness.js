// Runs the REAL static/js/ACAD HEAD/scheduleGeneration.acad.js against a
// minimal stub DOM (no jsdom dependency) and drives the selective-regeneration
// UI: retrieve a schedule, check/uncheck rows, toggle locks, Select All,
// Select Incomplete, Re-generate Selected. Prints one JSON object with every
// observation; tests/test_retrieve_previous_and_selective_regen.py asserts on it.
'use strict';
const page = require('./page_stub').loadPage();
const { getEl, calls, sandbox, docListeners, tbody, rowHtml, lockState, keyOf, setRow, toggleLock } = page;

// ── Scenario ──────────────────────────────────────────────────────────────
const ROWS = [
    { subject_code: 'ELED 116', faculty_id: '17179', instructor: 'Valencia, Christopher', class_type: 'Lecture',
      course: 'BEED', room_id: 18, room: 'LQ117', time: '7:30 PM - 9:00 PM', days: 'TUE/FRI', days_list: ['Tuesday', 'Friday'] },
    { subject_code: 'GEED 001', faculty_id: 'EMP019', instructor: 'Entienza, Jocelyn', class_type: 'Lecture',
      course: 'BEED', room_id: 18, room: 'LQ117', time: '6:00 PM - 9:00 PM', days: 'MON', days_list: ['Monday'] },
    { subject_code: 'GEED 002', faculty_id: '20123', instructor: 'Magtibay, Joel', class_type: 'Lecture',
      course: 'BEED', room_id: 18, room: 'LQ117', time: '4:30 PM - 6:00 PM', days: 'MON/THU', days_list: ['Monday', 'Thursday'],
      incomplete: true, incomplete_reason: ['x'] },
];
const [ELED, GEED1, GEED2] = ROWS;

(async () => {
    const R = {};
    for (const fn of (docListeners.DOMContentLoaded || [])) await fn();

    // Load a schedule through the real Retrieve Previous handler.
    Object.assign(getEl('acadYear'), { value: 'AY2627' });
    Object.assign(getEl('term'), { value: 'A' });
    Object.assign(getEl('program'), { value: 'BEED' });
    Object.assign(getEl('yearLevel'), { value: '1' });
    Object.assign(getEl('sectionFilter'), { value: '464' });
    Object.assign(getEl('curriculum'), { value: '2022-2023' });
    getEl('useHistorical').checked = true;
    page.nextResponse = () => ({ success: true, schedule_data: ROWS, retrieved_from: { ay_label: 'AY202526' },
                            evaluation: null, conflict_count: 0 });
    calls.length = 0;
    await getEl('btnGenerate').click();
    getEl('useHistorical').checked = false;
    // GEED 002 is flagged incomplete -> automatically selected on display,
    // without any regeneration request. (This response has no evaluation, so
    // the page re-evaluates in the background first; let that settle.)
    for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r));
    R.autoFirst = { GEED2: lockState(GEED2.subject_code), ELED: lockState(ELED.subject_code),
                    generateCalls: calls.filter(c => c.url === '/api/schedule/generate').length };
    await setRow(GEED2, false);                                  // the Academic Head unchecks it
    R.afterUserUncheck = lockState(GEED2.subject_code);

    R.unselected = lockState(ELED.subject_code);                 // no icons
    await setRow(ELED, true);
    R.checked = lockState(ELED.subject_code);                    // 1: all unlocked

    const regenerate = async () => {
        calls.length = 0;
        page.nextResponse = () => ({ success: true, result_status: 'COMPLETE_VALID', schedule_data: ROWS, conflict_count: 0 });
        await getEl('btnRegenerate').click();
        // The applied result still has GEED 002 incomplete, so it is auto-selected
        // again (new schedule state); these scenarios are about other rows, so the
        // Academic Head unchecks it again.
        await setRow(GEED2, false);
        const call = calls.find(c => c.url === '/api/schedule/generate');
        if (!call) return null;
        const m = {};
        call.body.locked_sessions.forEach(s => { m[s.subject_code] = { selected: s.selected, lock: s.lock }; });
        return m;
    };
    R.payloadDefault = await regenerate();                       // 2

    // A successful regeneration unchecks the row and drops its temporary locks
    // (Fix #4), so each scenario below selects ELED again and sets its locks.
    await setRow(ELED, true);
    await toggleLock(ELED, 'faculty');
    R.lockedInstructor = lockState(ELED.subject_code);
    R.payloadInstructor = await regenerate();                    // 3

    await setRow(ELED, true);
    await toggleLock(ELED, 'faculty');
    await toggleLock(ELED, 'room');
    R.payloadInstructorRoom = await regenerate();                // 4

    await setRow(ELED, true);
    await toggleLock(ELED, 'faculty');
    await toggleLock(ELED, 'room');
    await toggleLock(ELED, 'schedule');
    R.allLockedState = lockState(ELED.subject_code);
    R.payloadAllLocked = await regenerate();                     // 5: no request
    R.allLockedToast = getEl('successToast').innerHTML;

    await setRow(ELED, false);
    R.afterUncheck = lockState(ELED.subject_code);               // 6
    await setRow(ELED, true);
    R.afterRecheck = lockState(ELED.subject_code);               // 7
    await setRow(ELED, false);

    getEl('chkSelectAllRows').checked = true;
    await getEl('chkSelectAllRows').dispatch('change', { target: { checked: true } });
    R.selectAll = ROWS.map(r => lockState(r.subject_code));      // 8
    getEl('chkSelectAllRows').checked = false;
    await getEl('chkSelectAllRows').dispatch('change', { target: { checked: false } });

    await getEl('btnSelectIncomplete').click();
    R.selectIncomplete = ROWS.map(r => lockState(r.subject_code)); // 9 (GEED 002 only)

    // ── Evaluation panel refresh after every schedule-changing operation ────
    const mkEval = (score, viol, hist) => ({
        overallScore: score, cspPassed: viol === 0, hardViolationCount: viol,
        eligibleForApproval: viol === 0, completionRate: 100, incompleteCount: 0,
        categories: {
            conflictValidation: { weight: 60, criteria: { facultyConflictFree: viol ? 50 : 100,
                                  roomConflictFree: 100, sectionConflictFree: 100 } },
            constraintCompliance: { weight: 30, criteria: {} },
            recommendationQuality: { weight: 10, criteria: { historicalFacultyMatch: hist,
                                     historicalRoomMatch: hist, historicalScheduleMatch: hist } },
        },
        violationsBySubject: {},
    });
    const panel = () => ({
        score: getEl('evalScorePct').textContent,
        csp: getEl('evalCspText').textContent,
        breakdown: getEl('evalBreakdown').innerHTML,
    });
    const regenWith = async (response) => {
        await setRow(ELED, true);                       // all three unlocked
        calls.length = 0;
        page.nextResponse = (url) => (url === '/api/schedule/generate' ? response
            : { success: true, ...mkEval(64, 0, 42.5) });   // /api/schedule/accuracy fallback
        await getEl('btnRegenerate').click();
        await setRow(ELED, false);
        return calls.map(c => ({ url: c.url, body: c.body }));
    };
    const ok = (ev, conflicts) => ({ success: true, result_status: 'COMPLETE_VALID', schedule_data: ROWS,
                                     conflict_count: conflicts, evaluation: ev });

    await regenWith(ok(mkEval(91, 0, 87.5), 0));
    R.evalBaseline = panel();
    await regenWith(ok(mkEval(77, 2, 62.5), 2));        // regeneration CREATES a conflict
    R.evalAfterConflict = panel();
    await regenWith(ok(mkEval(100, 0, 100), 0));        // regeneration RESOLVES it, zero conflicts
    R.evalAfterResolve = panel();
    const fb = await regenWith(ok(null, 0));            // server sent no evaluation -> recompute
    R.evalFallbackCall = fb.find(c => c.url === '/api/schedule/accuracy') || null;
    R.evalAfterFallback = panel();

    calls.length = 0;                                   // loading a draft re-evaluates too
    page.nextResponse = (url) => (url.startsWith('/api/schedule/load-draft')
        ? { success: true, schedule_data: ROWS, version: 3, context: null }
        : { success: true, ...mkEval(55, 1, 25) });
    sandbox.window.loadDraft(7);                        // not awaited: it ends on a modal
    for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r));
    R.evalDraftCall = calls.find(c => c.url === '/api/schedule/accuracy') || null;
    R.evalAfterDraft = panel();

    // ── Conflict panel + conflict-aware selection/locking ───────────────────
    const NSTP = { subject_code: 'NSTP 001', faculty_id: '89128', instructor: 'Bulfa, Ronaldo', class_type: 'Lecture',
                   course: 'BEED', room_id: 47, room: 'LQ-QUAD', time: '10:30 AM - 1:30 PM', days: 'SAT', days_list: ['Saturday'] };
    const ROWS2 = [ELED, Object.assign({}, GEED1, { incomplete: true, incomplete_reason: ['x'], incomplete_components: ['room'] }),
                   Object.assign({}, GEED2, { incomplete: false }), NSTP];
    const conflict = (id, rule, resolution, targets, detail) => ({
        id, rule, type: `Conflict: ${rule}`, detail, subject: targets.map(t => t.subject_code).join(' / '),
        source: 'schedule', affected_components: [], resolution_components: resolution, targets });
    const C_FAC_TIME = conflict(1, 'HC10', ['faculty', 'schedule'], [{ subject_code: 'ELED 116', faculty_id: null }],
                                'Faculty 17179 double-booked on Tuesday.');
    const C_ROOM_TIME = conflict(2, 'HC11', ['room', 'schedule'], [{ subject_code: 'GEED 001', faculty_id: null },
                                 { subject_code: 'NOT IN TABLE', faculty_id: null }], 'Room LQ117 is already occupied.');
    const C_LOAD = conflict(3, 'HC9', ['faculty'], [{ subject_code: 'GEED 002', faculty_id: '20123' }],
                            'Magtibay, Joel regular load exceeds limit.');
    const C_TIME = conflict(4, 'HC6', ['schedule'], [{ subject_code: 'ELED 116', faculty_id: null }],
                            '"ELED 116" <b>invalid</b> start time.');
    const C_OTHER_FAC = conflict(5, 'HC9', ['faculty'], [{ subject_code: 'NSTP 001', faculty_id: 'SOMEONE-ELSE' }],
                                 'Load of a faculty not teaching this row.');
    const evalWith = (conflicts) => Object.assign(mkEval(80, conflicts.length, 90), { conflicts });
    const conflictPanel = () => ({
        hidden: getEl('conflictBanner').classList.contains('hidden'),
        title: getEl('conflictText').textContent,
        listHidden: getEl('conflictDetailList').classList.contains('hidden'),
        items: (getEl('conflictDetailList').innerHTML.match(/<li>/g) || []).length,
        listHtml: getEl('conflictDetailList').innerHTML,
        toggle: getEl('btnToggleConflictDetails').innerHTML.replace(/<[^>]+>/g, '').trim(),
        selectBtnHidden: getEl('btnSelectConflictRows').classList.contains('hidden'),
    });
    const allLocks = () => Object.fromEntries(ROWS2.map(r => [r.subject_code, lockState(r.subject_code)]));
    const clearSelection = async () => { for (const r of ROWS2) await setRow(r, false); };

    // Retrieve Previous result with 5 conflicts (4+ -> collapsed).
    getEl('useHistorical').checked = true;
    page.nextResponse = () => ({ success: true, schedule_data: ROWS2, retrieved_from: { ay_label: 'AY202526' },
                            conflict_count: 5, evaluation: evalWith([C_FAC_TIME, C_ROOM_TIME, C_LOAD, C_TIME, C_OTHER_FAC]) });
    await getEl('btnGenerate').click();
    getEl('useHistorical').checked = false;
    R.cpCollapsed = conflictPanel();
    R.cpFlags = Object.fromEntries(ROWS2.map(r => [r.subject_code,
        (rowHtml(r.subject_code).match(/cell-conflict-flag|row-conflict-flag/g) || []).length]));
    await getEl('btnToggleConflictDetails').click();
    R.cpExpanded = conflictPanel();
    await getEl('btnToggleConflictDetails').click();
    R.cpCollapsedAgain = conflictPanel();

    calls.length = 0;
    await getEl('btnSelectConflictRows').click();
    R.cpSelectConflictRows = allLocks();
    R.cpSelectConflictCalls = calls.length;              // must not regenerate

    // Payload built from those defaults: selected rows carry their locks,
    // unselected (NSTP 001) stays fully locked.
    calls.length = 0;
    page.nextResponse = (url) => (url === '/api/schedule/generate'
        ? { success: true, result_status: 'COMPLETE_VALID', schedule_data: ROWS2, conflict_count: 1,
            evaluation: evalWith([C_TIME]) }
        : { success: true });
    await getEl('btnRegenerate').click();
    const regenCall = calls.find(c => c.url === '/api/schedule/generate');
    R.cpRegenPayload = regenCall ? Object.fromEntries(regenCall.body.locked_sessions
        .map(x => [x.subject_code, { selected: x.selected, lock: x.lock }])) : null;
    R.cpAfterRegenOne = conflictPanel();                  // 1 conflict -> expanded
    await clearSelection();

    // Individual checks: conflict row -> conflict locks; clean row -> all unlocked.
    page.nextResponse = (url) => (url === '/api/schedule/generate'
        ? { success: true, result_status: 'COMPLETE_VALID', schedule_data: ROWS2, conflict_count: 3,
            evaluation: evalWith([C_FAC_TIME, C_ROOM_TIME, C_LOAD]) }
        : { success: true });
    await setRow(NSTP, true);                             // regenerate once to load 3 conflicts
    await getEl('btnRegenerate').click();
    await clearSelection();
    R.cpThree = conflictPanel();                          // 1-3 -> expanded
    await setRow(ELED, true);  await setRow(GEED1, true);  await setRow(GEED2, true);  await setRow(NSTP, true);
    R.cpIndividual = allLocks();
    await toggleLock(ELED, 'room');                       // manual change still possible
    R.cpManualToggle = lockState('ELED 116');
    await setRow(ELED, false); await setRow(ELED, true);  // re-check -> defaults again
    R.cpRecheck = lockState('ELED 116');
    await clearSelection();

    getEl('chkSelectAllRows').checked = true;
    await getEl('chkSelectAllRows').dispatch('change', { target: { checked: true } });
    R.cpSelectAll = allLocks();
    getEl('chkSelectAllRows').checked = false;
    await getEl('chkSelectAllRows').dispatch('change', { target: { checked: false } });

    await getEl('btnSelectIncomplete').click();           // GEED 001 is incomplete + conflicting
    R.cpSelectIncomplete = allLocks();
    await clearSelection();

    // Regeneration resolves everything -> panel disappears.
    page.nextResponse = (url) => (url === '/api/schedule/generate'
        ? { success: true, result_status: 'COMPLETE_VALID', schedule_data: ROWS2, conflict_count: 0,
            evaluation: evalWith([]) }
        : { success: true });
    await setRow(ELED, true);
    await getEl('btnRegenerate').click();
    R.cpResolved = conflictPanel();
    R.cpResolvedFlags = (tbody().innerHTML.match(/cell-conflict-flag|row-conflict-flag/g) || []).length;

    // ── EDUC 021/022/023: conflict ∪ incomplete, TBA never lockable, user locks ─
    const mk = (code, extra) => Object.assign({ subject_code: code, faculty_id: '20123', instructor: 'Magtibay, Joel',
        class_type: 'Lecture', course: 'BEED', room_id: null, room: 'TBA', days: 'SAT', days_list: ['Saturday'] }, extra);
    const E21 = mk('EDUC 021', { time: '10:30 AM - 1:30 PM' });
    const E22 = mk('EDUC 022', { time: '2:00 PM - 5:00 PM' });
    const E23 = mk('EDUC 023', { time: '6:00 PM - 9:00 PM' });
    const CLEAN = Object.assign({}, NSTP);
    const ROWS3 = [E21, E22, E23, CLEAN];
    const inc = (code) => ({ subject_code: code, components: ['room'], reasons: ['Room is TBA (not assigned).'],
                             targets: [{ subject_code: code, faculty_id: '20123' }] });
    const evalEduc = () => Object.assign(mkEval(70, 3, 50), {
        conflicts: [
            conflict(1, 'HC6', ['schedule'], [{ subject_code: 'EDUC 022', faculty_id: null }], '"EDUC 022" invalid start time.'),
            Object.assign(conflict(2, 'HC10', ['faculty'], [{ subject_code: 'EDUC 022', faculty_id: null }], 'Faculty 20123 double-booked (022).'), { affected_components: ['instructor', 'day', 'time'] }),
            Object.assign(conflict(3, 'HC10', ['faculty'], [{ subject_code: 'EDUC 023', faculty_id: null }], 'Faculty 20123 double-booked (023).'), { affected_components: ['instructor', 'day', 'time'] }),
        ],
        incomplete: [inc('EDUC 021'), inc('EDUC 022'), inc('EDUC 023')],
        incompleteComponentCount: 3,
    });
    const educLocks = () => Object.fromEntries(ROWS3.map(r => [r.subject_code, lockState(r.subject_code)]));
    const originOf = (code, field) => {
        const m = rowHtml(code).match(new RegExp(`data-origin="([a-z]+)"(?:\\s+disabled)?\\s+data-row-key="[^"]*"\\s+data-field="${field}"`));
        return m ? m[1] : null;
    };
    const clearEduc = async () => { for (const r of ROWS3) await setRow(r, false); };

    getEl('useHistorical').checked = true;
    page.nextResponse = () => ({ success: true, schedule_data: ROWS3, retrieved_from: { ay_label: 'AY202526' },
                            conflict_count: 3, evaluation: evalEduc() });
    calls.length = 0;
    await getEl('btnGenerate').click();
    getEl('useHistorical').checked = false;
    R.rsAuto = educLocks();                                // all three TBA rows auto-selected
    R.rsAutoGenerateCalls = calls.filter(c => c.url === '/api/schedule/generate').length;
    R.rsBanner = { hidden: getEl('incompleteBanner').classList.contains('hidden'),
                   text: getEl('incompleteText').textContent,
                   selectHidden: getEl('btnSelectIncomplete').classList.contains('hidden') };
    R.rsIncompleteCellFlags = Object.fromEntries(ROWS3.map(r => [r.subject_code,
        (rowHtml(r.subject_code).match(/cell-incomplete-flag/g) || []).length]));

    await clearEduc();                                     // the Academic Head unchecks everything
    await getEl('btnSelectConflictRows').click();          // selected because of conflicts...
    R.rsViaConflictRows = educLocks();                     // ...but TBA Room still starts unlocked
    R.rsRoomOrigin = originOf('EDUC 022', 'room');
    await toggleLock(E22, 'room');                         // trying to lock TBA does nothing
    R.rsRoomAfterClick = lockState('EDUC 022').room;
    await clearEduc();

    await getEl('btnSelectIncomplete').click();            // selected because of TBA...
    R.rsViaSelectIncomplete = educLocks();                 // ...but conflicts still start unlocked
    await clearEduc();

    await setRow(E23, true);                               // individual check: same combined rule
    R.rsIndividual23 = lockState('EDUC 023');
    await setRow(E21, true);
    R.rsAutoOrigin = originOf('EDUC 021', 'faculty');      // incomplete row: starts unlocked
    await toggleLock(E21, 'faculty');                      // lock it => user lock
    R.rsUserOrigin = originOf('EDUC 021', 'faculty');
    await setRow(E22, true);

    calls.length = 0;
    page.nextResponse = (url) => (url === '/api/schedule/generate'
        ? { success: true, result_status: 'COMPLETE_VALID', conflict_count: 0,
            schedule_data: [E21, Object.assign({}, E22, { room_id: 214, room: 'LQ214' }),
                            Object.assign({}, E23, { room_id: 118, room: 'LQ118' }), CLEAN],
            evaluation: Object.assign(mkEval(95, 0, 90), { conflicts: [], incomplete: [inc('EDUC 021')] }),
            released_locks: { 'EDUC 023': ['faculty'] } }
        : { success: true });
    await getEl('btnRegenerate').click();
    const rc = calls.find(c => c.url === '/api/schedule/generate');
    R.rsPayload = rc ? Object.fromEntries(rc.body.locked_sessions.map(x =>
        [x.subject_code, { selected: x.selected, lock: x.lock, user_lock: x.user_lock }])) : null;
    R.rsToast = getEl('successToast').innerHTML.replace(/<[^>]+>/g, '').trim();
    R.rsBannerAfter = { hidden: getEl('incompleteBanner').classList.contains('hidden'),
                        text: getEl('incompleteText').textContent };
    R.rsConflictPanelAfter = getEl('conflictBanner').classList.contains('hidden');
    await clearEduc();

    page.nextResponse = (url) => (url === '/api/schedule/generate'
        ? { success: true, result_status: 'COMPLETE_VALID', conflict_count: 0, schedule_data: ROWS3,
            evaluation: Object.assign(mkEval(100, 0, 100), { conflicts: [], incomplete: [] }) }
        : { success: true });
    await setRow(CLEAN, true);
    await getEl('btnRegenerate').click();
    R.rsAllResolved = { banner: getEl('incompleteBanner').classList.contains('hidden'),
                        panel: getEl('conflictBanner').classList.contains('hidden'),
                        cellFlags: (tbody().innerHTML.match(/cell-incomplete-flag/g) || []).length };

    process.stdout.write(JSON.stringify(R));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
