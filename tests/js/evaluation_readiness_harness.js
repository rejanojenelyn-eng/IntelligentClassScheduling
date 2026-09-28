// Drives the REAL static/js/ACAD HEAD/scheduleGeneration.acad.js (stub DOM):
// the evaluation panel keeps quality score, completion, CSP and approval
// readiness separate; the Incomplete banner names the exact row + field;
// Select Incomplete picks exactly those rows (all unlocked); regeneration
// updates counts and approval. Output is asserted by tests/test_evaluation_readiness.py.
'use strict';
const page = require('./page_stub').loadPage();
const { getEl, calls, docListeners, rowHtml, lockState } = page;

const flush = async () => { for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r)); };
const modalOpen = () => !getEl('infoModal').classList.contains('hidden');
async function clickAnswering(el, answers = []) {
    const done = el.click();
    for (const a of answers) {
        await flush();
        if (!modalOpen()) break;
        (a ? getEl('infoModalConfirmBtn') : getEl('infoModalCancelBtn')).onclick();
    }
    await done; await flush();
}
const checked = (code) => /<input type="checkbox" class="row-select"[^>]*checked/.test(rowHtml(code));
const row = (code, fac, extra) => Object.assign({ subject_code: code, faculty_id: fac, instructor: `Fac ${fac}`,
    class_type: 'Lecture', course: 'BSA', room_id: 18, room: 'LQ122', time: '7:30 AM - 10:30 AM',
    days: 'MON', days_list: ['Monday'] }, extra || {});
const BASE = [row('ACCO 301', 'F1'), row('ACCO 302', 'F2'), row('ACCO 303', 'F3'),
              row('ACCO 304', 'F1', { time: '1:00 PM - 4:00 PM', days: 'FRI', days_list: ['Friday'] }),
              row('ACCO 305', 'F3', { room_id: null, room: '122/TBA', days: 'WED', days_list: ['Wednesday'] }),
              row('ELEC BSA-P2', 'F2'), row('LAW 014', 'F4')];
const HC6 = { id: 1, rule: 'HC6', type: 'Conflict: Time Block', subject: 'ACCO 304', source: 'schedule',
              detail: '"ACCO 304" — invalid start time (01:00 PM). Please select a standard time block.',
              affected_components: ['time'], resolution_components: ['schedule'],
              targets: [{ subject_code: 'ACCO 304', faculty_id: null }] };
const INC305 = { id: 1, subject_code: 'ACCO 305', components: ['room'],
                 reasons: ["Room '122/TBA' is not a valid room record."],
                 targets: [{ subject_code: 'ACCO 305', faculty_id: 'F3' }] };
const ev = ({ score, hard = [], inc = [], rate = 100 }) => ({
    success: true, overallScore: score, scoreKind: 'quality', cspPassed: hard.length === 0,
    hardViolationCount: hard.length, eligibleForApproval: hard.length === 0 && inc.length === 0,
    completionRate: rate, incompleteCount: inc.length,
    incompleteComponentCount: inc.reduce((n, e) => n + e.components.length, 0),
    categories: {}, violationsBySubject: {}, conflicts: hard, incomplete: inc });
const panel = () => ({
    score: getEl('evalScorePct').textContent, scoreClass: getEl('evalScorePct').className,
    completion: getEl('evalCompletionText').textContent, csp: getEl('evalCspText').textContent,
    eligibleBadgeHidden: getEl('evalApprovalBadge').classList.contains('hidden'),
    readinessHidden: getEl('evalReadiness').classList.contains('hidden'),
    readiness: getEl('evalReadinessText').textContent });

(async () => {
    const R = {};
    for (const fn of (docListeners.DOMContentLoaded || [])) await fn();
    ['infoModal', 'infoModalTertiaryBtn', 'evalApprovalBadge', 'evalReadiness'].forEach(id => getEl(id).classList.add('hidden'));
    Object.assign(getEl('acadYear'), { value: 'AY2627' });
    Object.assign(getEl('term'), { value: 'A' });
    Object.assign(getEl('program'), { value: 'BSA' });
    Object.assign(getEl('yearLevel'), { value: '3' });
    Object.assign(getEl('sectionFilter'), { value: '30' });
    Object.assign(getEl('curriculum'), { value: '2022-2023' });

    // The reported state: quality 100, CSP failed (ACCO 304), 86% complete (ACCO 305 room).
    page.nextResponse = (url) => (url === '/api/schedule/generate'
        ? { success: false, result_status: 'INVALID_RESULT', result_state: 'PARTIAL_RESULT', schedule_data: BASE,
            evaluation: ev({ score: 100, hard: [HC6], inc: [INC305], rate: 85.7 }),
            advisories: [], blocking_violations: [HC6], incomplete_components: [INC305] }
        : { success: true });
    await clickAnswering(getEl('btnGenerate'), [true]);            // Review Incomplete Schedule
    R.reviewed = {
        panel: panel(),
        banner: { text: getEl('incompleteText').textContent,
                  items: (getEl('incompleteDetailList').innerHTML.match(/<li>(.*?)<\/li>/g) || [])
                      .map(li => li.replace(/<[^>]+>/g, '').replace(/&#39;|&apos;/g, "'").replace(/&amp;/g, '&')) },
        tools: { saveDraft: getEl('btnSaveDraft').disabled, approve: getEl('btnApprove').disabled,
                 regenerate: getEl('btnRegenerate').disabled, manualEditor: getEl('btnManualEditor').disabled,
                 selectConflictHidden: getEl('btnSelectConflictRows').classList.contains('hidden'),
                 selectIncompleteHidden: getEl('btnSelectIncomplete').classList.contains('hidden') },
    };

    // Select Incomplete: exactly the affected row(s), all three unlocked.
    for (const r of BASE) {                                        // start from nothing selected
        if (checked(r.subject_code)) await page.setRow(r, false);
    }
    await getEl('btnSelectIncomplete').click();
    R.selectIncomplete = Object.fromEntries(BASE.filter(r => checked(r.subject_code))
        .map(r => [r.subject_code, lockState(r.subject_code)]));

    // Regenerate the incomplete row: completion 100%, the HC6 still there.
    const FIXED305 = BASE.map(r => r.subject_code === 'ACCO 305' ? Object.assign({}, r, { room_id: 21, room: 'LQ121' }) : r);
    page.nextResponse = (url) => (url === '/api/schedule/generate'
        ? { success: true, result_status: 'COMPLETE_VALID', result_state: 'PARTIAL_RESULT', schedule_data: FIXED305,
            evaluation: ev({ score: 99, hard: [HC6] }), released_locks: {} }
        : { success: true });
    await clickAnswering(getEl('btnRegenerate'), [true]);
    R.afterFixIncomplete = Object.assign(panel(), { approveDisabled: getEl('btnApprove').disabled,
        checked: BASE.map(r => r.subject_code).filter(checked) });

    // Regenerate the conflict row: CSP passes, now approvable.
    await getEl('btnSelectConflictRows').click();
    const FIXED = FIXED305.map(r => r.subject_code === 'ACCO 304' ? Object.assign({}, r, { time: '1:30 PM - 4:30 PM' }) : r);
    page.nextResponse = (url) => (url === '/api/schedule/generate'
        ? { success: true, result_status: 'COMPLETE_VALID', result_state: 'COMPLETE_SUCCESS', schedule_data: FIXED,
            evaluation: ev({ score: 100 }), released_locks: {} }
        : { success: true });
    await clickAnswering(getEl('btnRegenerate'), [true]);
    R.afterFixViolation = Object.assign(panel(), { approveDisabled: getEl('btnApprove').disabled,
        bannerListAfter: getEl('incompleteDetailList').innerHTML,
        regenCalls: calls.filter(c => c.url === '/api/schedule/generate').length });

    process.stdout.write(JSON.stringify(R));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
