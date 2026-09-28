// Drives the REAL static/js/ACAD HEAD/scheduleGeneration.acad.js (stub DOM) for
// Fix #4 (successful Re-generate Selected resets temporary selection/locks) and
// Fix #5 ("Generate New Schedule" in the No Previous Schedule modal generates
// immediately). Prints one JSON object; tests/test_regen_cleanup_and_generate_new.py
// asserts on it.
'use strict';
const page = require('./page_stub').loadPage();
const { getEl, calls, docListeners, tbody, rowHtml, lockState, setRow, toggleLock } = page;

const flush = async () => { for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r)); };
const modalOpen = () => !getEl('infoModal').classList.contains('hidden');
// Clicks `el`, answers the info modal (if one opens) with confirm/cancel, and
// waits for the whole click handler (including follow-ups) to finish.
async function clickAnswering(el, answers = []) {
    const done = el.click();
    for (const a of answers) {
        await flush();
        if (!modalOpen()) break;
        (a ? getEl('infoModalConfirmBtn') : getEl('infoModalCancelBtn')).onclick();
    }
    await done;
    await flush();
}

const row = (code, fac, extra) => Object.assign({ subject_code: code, faculty_id: fac, instructor: `Fac ${fac}`,
    class_type: 'Lecture', course: 'BEED', room_id: 18, room: 'LQ117', time: '7:30 AM - 9:00 AM',
    days: 'MON/THU', days_list: ['Monday', 'Thursday'] }, extra || {});
const A = row('A 101', 'F1'), B = row('B 102', 'F2'), C = row('C 103', 'F3'), D = row('D 104', 'F4');
const ROWS = [A, B, C, D];
const mkEval = (conflicts) => ({ overallScore: 90, cspPassed: !conflicts.length, hardViolationCount: conflicts.length,
    eligibleForApproval: !conflicts.length, completionRate: 100, incompleteCount: 0, categories: {},
    violationsBySubject: {}, conflicts, incomplete: [] });
const conflict = (code, rule, detail, res) => ({ id: code + rule, rule, type: `Conflict: ${rule}`, detail, subject: code,
    source: 'schedule', affected_components: [], resolution_components: res, targets: [{ subject_code: code, faculty_id: null }] });
const flags = (code) => (rowHtml(code).match(/cell-conflict-flag|row-conflict-flag/g) || []).length;
const checked = (code) => /<input type="checkbox" class="row-select"[^>]*checked/.test(rowHtml(code));

(async () => {
    const R = {};
    for (const fn of (docListeners.DOMContentLoaded || [])) await fn();
    Object.assign(getEl('acadYear'), { value: 'AY2627' });
    Object.assign(getEl('term'), { value: 'A' });
    Object.assign(getEl('program'), { value: 'BEED' });
    Object.assign(getEl('yearLevel'), { value: '1' });
    Object.assign(getEl('sectionFilter'), { value: '464' });
    Object.assign(getEl('curriculum'), { value: '2022-2023' });

    // Retrieved schedule: A has a time-block conflict.
    const HC6_A = conflict('A 101', 'HC6', 'A 101 invalid start time.', ['schedule']);
    getEl('useHistorical').checked = true;
    page.nextResponse = () => ({ success: true, schedule_data: ROWS, retrieved_from: { ay_label: 'AY202526' },
                                 conflict_count: 1, evaluation: mkEval([HC6_A]) });
    await clickAnswering(getEl('btnGenerate'));
    getEl('useHistorical').checked = false;
    R.before = { aFlags: flags('A 101') };

    const regen = async (response, answers) => {
        calls.length = 0;
        page.nextResponse = (url) => (url === '/api/schedule/generate' ? response : { success: true });
        await clickAnswering(getEl('btnRegenerate'), answers);
        const call = calls.find(c => c.url === '/api/schedule/generate');
        return call ? Object.fromEntries(call.body.locked_sessions.map(s => [s.subject_code, { selected: s.selected, lock: s.lock }])) : null;
    };
    const ok = (rows, conflicts) => ({ success: true, result_status: 'COMPLETE_VALID', schedule_data: rows,
                                       conflict_count: conflicts.length, evaluation: mkEval(conflicts) });

    // C1 + C6 + C10: one successful row; its conflict is resolved by the new evaluation.
    await setRow(A, true);
    R.c1Selected = { checked: checked('A 101'), locks: lockState('A 101') };
    const A2 = Object.assign({}, A, { time: '9:00 AM - 10:30 AM' });
    R.c1Payload = await regen(ok([A2, B, C, D], []));
    R.c1 = { checked: checked('A 101'), locks: lockState('A 101'), time: rowHtml('A 101').includes('9:00 AM - 10:30 AM'),
             flags: flags('A 101'), panelHidden: getEl('conflictBanner').classList.contains('hidden'),
             others: ['B 102', 'C 103', 'D 104'].map(c => ({ checked: checked(c), icons: lockState(c).icons })) };
    R.c8AfterC1 = getEl('chkSelectAllRows').checked;

    // C9: reselect the successful row -> all unlocked.
    await setRow(A2, true);
    R.c9 = lockState('A 101');
    await setRow(A2, false);

    // C2: preserved Instructor (user lock) -> value stays, lock disappears.
    await setRow(A2, true);
    await toggleLock(A2, 'faculty');
    R.c2LockedBefore = lockState('A 101');
    R.c2Payload = await regen(ok([A2, B, C, D], []));
    R.c2 = { checked: checked('A 101'), locks: lockState('A 101'), instructor: rowHtml('A 101').includes('Fac F1') };

    // Instructor changed by the regeneration (row key changes): no stale state left.
    await setRow(A2, true);
    const A3 = Object.assign({}, A2, { faculty_id: 'F9', instructor: 'Fac F9' });
    await regen(ok([A3, B, C, D], []));
    R.keyChange = { checked: checked('A 101'), locks: lockState('A 101'), instructor: rowHtml('A 101').includes('Fac F9') };

    // C3 + C7: several successful rows; the new result has a NEW legitimate warning on C.
    await setRow(A3, true); await setRow(B, true);
    await toggleLock(B, 'room');
    await setRow(C, true);
    const HC11_C = conflict('C 103', 'HC11', 'Room LQ117 is already occupied.', ['room', 'schedule']);
    await regen(ok([A3, B, C, D], [HC11_C]));
    R.c3 = ['A 101', 'B 102', 'C 103'].map(c => ({ checked: checked(c), icons: lockState(c).icons }));
    R.c7 = { cFlags: flags('C 103'), panelHidden: getEl('conflictBanner').classList.contains('hidden'),
             panelText: getEl('conflictText').textContent };
    R.c8AfterC3 = getEl('chkSelectAllRows').checked;
    R.c10D = { checked: checked('D 104'), icons: lockState('D 104').icons };

    // C4: partial success — B fails, A and C succeed.
    await setRow(A3, true); await setRow(B, true); await setRow(C, true);
    await toggleLock(B, 'room');
    R.c4BLocksBefore = lockState('B 102');
    const C2 = Object.assign({}, C, { room_id: 20, room: 'LQ120' });
    R.c4Payload = await regen({ success: true, result_status: 'REGENERATION_PARTIAL', failed_subjects: ['B 102'],
                                error: 'B 102 could not be resolved while Room is locked by you.',
                                schedule_data: [A3, B, C2, D], conflict_count: 0, evaluation: mkEval([]) }, [true]);
    R.c4 = { A: { checked: checked('A 101'), icons: lockState('A 101').icons },
             B: { checked: checked('B 102'), locks: lockState('B 102') },
             C: { checked: checked('C 103'), icons: lockState('C 103').icons, room: rowHtml('C 103').includes('LQ120') },
             modalTitle: getEl('infoModalTitle').textContent };
    R.c8AfterC4 = getEl('chkSelectAllRows').checked;

    // C5: total failure -> selection and locks preserved, nothing applied.
    await setRow(A3, true);
    await toggleLock(A3, 'faculty');
    const lockedBefore = { A: lockState('A 101'), B: lockState('B 102') };
    await regen({ success: false, result_status: 'REGENERATION_INFEASIBLE', failed_subjects: ['A 101', 'B 102'],
                  error: 'A 101 could not be resolved while Instructor is locked by you.' }, [true]);
    R.c5 = { before: lockedBefore, A: { checked: checked('A 101'), locks: lockState('A 101') },
             B: { checked: checked('B 102'), locks: lockState('B 102') }, modalTitle: getEl('infoModalTitle').textContent };

    // ── Fix #5: No Previous Schedule modal ──────────────────────────────────
    // Old schedule (BEED Year 1) is on screen; the filters now say BPAFA 4.
    const retrieveThenFresh = (fresh) => (url) => (url === '/api/schedule/retrieve-previous'
        ? { success: false, error_code: 'NO_PREVIOUS_SCHEDULE', error: 'No previous AY schedule was found.' }
        : fresh);
    const setFilters = () => {
        Object.assign(getEl('program'), { value: 'BPAFA' });
        Object.assign(getEl('yearLevel'), { value: '4' });
        Object.assign(getEl('sectionFilter'), { value: '777' });
        Object.assign(getEl('curriculum'), { value: '2022-2023' });
    };
    const FRESH = { success: true, result_status: 'COMPLETE_VALID', conflict_count: 0,
                    schedule_data: [row('PADM 401', 'F7', { course: 'BPAFA' })], evaluation: mkEval([]) };
    const urls = () => calls.map(c => c.url);

    // D3: Stay on Page -> nothing generated, display + filters kept.
    setFilters();
    getEl('useHistorical').checked = true;
    calls.length = 0;
    page.nextResponse = retrieveThenFresh(FRESH);
    await clickAnswering(getEl('btnGenerate'), [false]);
    R.d3 = { urls: urls(), stillOld: rowHtml('A 101') !== '', retrieveChecked: getEl('useHistorical').checked,
             program: getEl('program').value, modalTitle: getEl('infoModalTitle').textContent,
             btnDisabled: getEl('btnGenerate').disabled };

    // D2/D4-D10: Generate New Schedule -> one fresh generation, current filters.
    await setRow(A3, true);                                   // stale selection from the old display
    calls.length = 0;
    let release;
    const inFlight = new Promise(r => { release = r; });        // hold the fresh generation open
    page.nextResponse = retrieveThenFresh(inFlight.then(() => FRESH));
    const done = getEl('btnGenerate').click();
    await flush();
    R.d2ModalTitle = getEl('infoModalTitle').textContent;
    const confirmBtn = getEl('infoModalConfirmBtn');
    confirmBtn.onclick(); confirmBtn.onclick();               // D8: double click
    await flush();
    R.d5InFlight = { urls: urls(), btnDisabled: getEl('btnGenerate').disabled,
                     loading: !getEl('loadingModal').classList.contains('hidden') };
    getEl('btnGenerate').click();                              // D8: impatient extra click mid-run
    await flush();
    release();
    await done; await flush();
    const gen = calls.filter(c => c.url === '/api/schedule/generate');
    R.d4 = { urls: urls(), genBody: gen.length ? gen[0].body : null,
             retrieveChecked: getEl('useHistorical').checked,
             newRow: rowHtml('PADM 401') !== '', oldRowGone: rowHtml('A 101') === '',
             title: getEl('tableTitle').innerHTML, selectedLeft: /row-selected/.test(tbody().innerHTML),
             modalHidden: !modalOpen(), btnDisabled: getEl('btnGenerate').disabled,
             btnLabel: getEl('btnGenerate').innerHTML, csp: getEl('evalCspText').textContent };

    // D11: fresh generation fails -> error shown, UI not stuck.
    getEl('useHistorical').checked = true;
    calls.length = 0;
    page.nextResponse = retrieveThenFresh({ success: false, result_status: 'GENERATION_ERROR', error: 'Solver failed.' });
    await clickAnswering(getEl('btnGenerate'), [true, true]);
    R.d11 = { urls: urls(), modalTitle: getEl('infoModalTitle').textContent,
              btnDisabled: getEl('btnGenerate').disabled, retrieveChecked: getEl('useHistorical').checked,
              keptDisplay: rowHtml('PADM 401') !== '' };

    // D1: previous schedule exists -> retrieval works normally, no generation.
    getEl('useHistorical').checked = true;
    calls.length = 0;
    page.nextResponse = (url) => (url === '/api/schedule/retrieve-previous'
        ? { success: true, schedule_data: ROWS, retrieved_from: { ay_label: 'AY202526' }, conflict_count: 0, evaluation: mkEval([]) }
        : { success: true });
    await clickAnswering(getEl('btnGenerate'));
    R.d1 = { urls: urls(), shown: rowHtml('A 101') !== '', retrieveChecked: getEl('useHistorical').checked };

    process.stdout.write(JSON.stringify(R));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
