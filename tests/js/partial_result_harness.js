// Drives the REAL static/js/ACAD HEAD/scheduleGeneration.acad.js (stub DOM) for
// Fix #6: complete / partial / true-failure generation results, the
// "Schedule Generated with Issues" decision (Discard vs Review), the approval
// gate, Save as Draft of a partial result, and recovery via Re-generate
// Selected. Prints one JSON object; tests/test_partial_generation_result_handling.py
// asserts on it.
'use strict';
const page = require('./page_stub').loadPage();
const { getEl, calls, docListeners, tbody, rowHtml, setRow } = page;

const flush = async () => { for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r)); };
const modalOpen = () => !getEl('infoModal').classList.contains('hidden');
const modalInfo = () => ({
    title: getEl('infoModalTitle').textContent, message: getEl('infoModalMessage').innerHTML,
    confirm: getEl('infoModalConfirmBtn').textContent, cancel: getEl('infoModalCancelBtn').textContent,
    cancelHidden: getEl('infoModalCancelBtn').classList.contains('hidden'),
    tertiaryHidden: getEl('infoModalTertiaryBtn').classList.contains('hidden'),
});
// Click `el`; for each modal that opens, record it and answer (true = confirm).
async function clickAnswering(el, answers = []) {
    const seen = [];
    const done = el.click();
    for (const a of answers) {
        await flush();
        if (!modalOpen()) break;
        seen.push(modalInfo());
        const _btn = a ? getEl("infoModalConfirmBtn") : getEl("infoModalCancelBtn");
        if (typeof _btn.onclick !== "function") throw new Error("no handler on open modal: " + JSON.stringify(modalInfo()).slice(0, 300));
        _btn.onclick();
    }
    await done; await flush();
    return seen;
}
const approve = () => ({ disabled: getEl('btnApprove').disabled, reason: getEl('approveWrap').title || '' });
const shown = (code) => rowHtml(code) !== '';
const checked = (code) => /<input type="checkbox" class="row-select"[^>]*checked/.test(rowHtml(code));

const row = (code, extra) => Object.assign({ subject_code: code, faculty_id: 'F1', instructor: 'Fac F1',
    class_type: 'Lecture', course: 'BEED', room_id: 18, room: 'LQ117', time: '9:00 AM - 12:00 PM',
    days: 'MON', days_list: ['Monday'] }, extra || {});
const OLD = [row('OLD 101')];
const NEW = [row('GEED 002'), row('GEED 003', { room_id: null, room: 'TBA', incomplete: true }),
             row('GEED 004', { time: 'TBA', days: '', days_list: [], incomplete: true }), row('GEED 005'), row('GEED 006')];
const ev = ({ inc = 0, hard = 0, rate = 100, score = 90, incomplete = [], conflicts = [] } = {}) => ({
    success: true, overallScore: score, cspPassed: hard === 0, hardViolationCount: hard,
    eligibleForApproval: hard === 0 && inc === 0, completionRate: rate, incompleteCount: inc,
    categories: {}, violationsBySubject: {}, conflicts, incomplete });
const INC = [
    { id: 1, subject_code: 'GEED 003', components: ['room'], reasons: ['Room is TBA (not assigned).'],
      targets: [{ subject_code: 'GEED 003', faculty_id: 'F1' }] },
    { id: 2, subject_code: 'GEED 004', components: ['schedule'], reasons: ['Time/Days is not assigned.'],
      targets: [{ subject_code: 'GEED 004', faculty_id: 'F1' }] },
];
const HC9 = { id: 9, rule: 'HC9', type: 'Conflict: Faculty Load', subject: 'GEED 002', source: 'schedule',
              detail: 'Barros Ii, Salvador PT load 30.0 hrs exceeds limit 12.0', affected_components: ['instructor'],
              resolution_components: ['faculty'], targets: [{ subject_code: 'GEED 002', faculty_id: null }] };
const ADV = { rule: 'HC_SPEC', severity: 'warning', subject: 'GEED 005',
              detail: 'Jaysom Ucom may not match the expected specialization for GEED 005.' };
const complete = (rows, extra) => Object.assign({ success: true, result_status: 'COMPLETE_VALID',
    result_state: 'COMPLETE_SUCCESS', schedule_data: rows, evaluation: ev(), advisories: [],
    blocking_violations: [], incomplete_components: [] }, extra || {});
const partial = (evaluation, extra) => Object.assign({ success: false, result_status: 'INVALID_RESULT',
    result_state: 'PARTIAL_RESULT', schedule_data: NEW, evaluation,
    advisories: [ADV], blocking_violations: evaluation.conflicts, incomplete_components: evaluation.incomplete,
    error: 'One or more retained assignments still violate a hard constraint.' }, extra || {});
const PARTIAL_EV = () => ev({ inc: 2, hard: 1, rate: 60, score: 94, incomplete: INC, conflicts: [HC9] });

async function generate(response, answers) {
    calls.length = 0;
    page.nextResponse = (url) => (url === '/api/schedule/generate' ? response : { success: true });
    return clickAnswering(getEl('btnGenerate'), answers);
}
const panel = () => ({ completion: getEl('evalCompletionText').textContent, csp: getEl('evalCspText').textContent,
                       eligibleBadgeHidden: getEl('evalApprovalBadge').classList.contains('hidden') });

(async () => {
    const R = {};
    for (const fn of (docListeners.DOMContentLoaded || [])) await fn();
    // As in the real markup, the dialog / badge / hint start hidden.
    ['infoModal', 'infoModalTertiaryBtn', 'evalApprovalBadge'].forEach(id => getEl(id).classList.add('hidden'));
    Object.assign(getEl('acadYear'), { value: 'AY2627' });
    Object.assign(getEl('term'), { value: 'A' });
    Object.assign(getEl('program'), { value: 'BEED' });
    Object.assign(getEl('yearLevel'), { value: '1' });
    Object.assign(getEl('sectionFilter'), { value: '464' });
    Object.assign(getEl('curriculum'), { value: '2022-2023' });

    // 1. Complete generation -> applied at once, approvable.
    let seen = await generate(complete(OLD), [true]);
    R.complete = { modalShown: seen.length > 0, applied: shown('OLD 101'), approve: approve(), csp: panel().csp };

    // 2 + 6. Partial result -> dialog; Discard keeps the previous working schedule.
    await setRow(OLD[0], true);
    seen = await generate(partial(PARTIAL_EV()), [false]);
    R.partialModal = seen[0];
    R.discard = { oldRowShown: shown('OLD 101'), newRowShown: shown('GEED 002'), stillSelected: checked('OLD 101'),
                  approve: approve(), csp: panel().csp };

    // 3. Hard violation only (complete) -> same dialog.
    seen = await generate(partial(ev({ hard: 1, conflicts: [HC9] }), { advisories: [] }), [false]);
    R.hardOnlyModal = seen[0];

    // 4. True failure -> Generation Failed, no Review option, nothing applied.
    seen = await generate({ success: false, result_status: 'GENERATION_ERROR', result_state: 'TRUE_FAILURE',
                            error: 'solver crashed' }, [true]);
    R.trueFailure = Object.assign({}, seen[0], { keptOld: shown('OLD 101') });

    // 5. SC9 advisory only -> not a failure, stays approvable.
    seen = await generate(complete(OLD, { advisories: [ADV] }), [true]);
    R.advisoryOnly = { modalShown: seen.length > 0, approve: approve() };

    // 7-10. Review the partial result.
    seen = await generate(partial(PARTIAL_EV()), [true]);
    R.review = Object.assign({ newRowShown: shown('GEED 002'), oldRowShown: shown('OLD 101'),
        tbaIncompleteFlags: (rowHtml('GEED 003').match(/cell-incomplete-flag/g) || []).length
                          + (rowHtml('GEED 004').match(/cell-incomplete-flag/g) || []).length,
        saveDraftDisabled: getEl('btnSaveDraft').disabled, approve: approve() }, panel());

    // Save as Draft of the partial result: allowed, reports the open issues.
    calls.length = 0;
    page.nextResponse = (url) => (url === '/api/schedule/check-existing' ? { exists: false }
        : url === '/api/schedule/save-draft'
            ? { success: true, draft_version: 4, is_incomplete: true,
                draft_warnings: [{ rule: 'HC9', subject: 'GEED 002', detail: HC9.detail }] }
            : { success: true });
    seen = await clickAnswering(getEl('btnSaveDraft'), [true]);
    R.saveDraft = { title: seen[0] && seen[0].title, message: seen[0] && seen[0].message,
                    request: (calls.find(c => c.url === '/api/schedule/save-draft') || {}).body || null,
                    approveStillDisabled: getEl('btnApprove').disabled };

    // A stale/forced Approve click on the not-approvable schedule: the server
    // rejects it and the button must not come back enabled.
    page.nextResponse = (url) => (url === '/api/schedule/approve'
        ? { success: false, error_code: 'SCHEDULE_NOT_APPROVABLE', error: 'Cannot approve: unresolved required components.' }
        : { success: true });
    seen = await clickAnswering(getEl('btnApprove'), [true, true]);
    R.rejectedApproval = { title: seen[1] && seen[1].title, approve: approve() };

    // 11-15. Approval gate matrix.
    const gateAfter = async (resp, answers) => { await generate(resp, answers); return approve(); };
    R.gate = {
        incompleteOnly: await gateAfter(partial(ev({ inc: 2, rate: 60, incomplete: INC })), [true]),
        hardOnly:       await gateAfter(partial(ev({ hard: 1, conflicts: [HC9] })), [true]),
        both:           await gateAfter(partial(PARTIAL_EV()), [true]),
        clean:          await gateAfter(complete(OLD), [true]),
        advisoryOnly:   await gateAfter(complete(OLD, { advisories: [ADV] }), [true]),
        highScoreButIncomplete: await gateAfter(partial(ev({ inc: 1, rate: 99, score: 99, incomplete: [INC[0]] })), [true]),
    };

    // 18-22. Recovery: review a partial result, fix it with Re-generate Selected.
    await generate(partial(PARTIAL_EV(), { advisories: [] }), [true]);
    R.recovery = { before: { approve: approve(), saveDraftDisabled: getEl('btnSaveDraft').disabled } };
    for (const code of ['GEED 002']) await setRow(NEW.find(r => r.subject_code === code), false);   // only the incomplete rows
    const FIXED = NEW.map(r => Object.assign({}, r, { room_id: 18, room: 'LQ117', time: '1:30 PM - 3:00 PM',
                                                      days: 'TUE', days_list: ['Tuesday'], incomplete: false }));
    calls.length = 0;
    page.nextResponse = (url) => (url === '/api/schedule/generate'
        ? complete(FIXED, { released_locks: {} }) : { success: true });
    seen = await clickAnswering(getEl('btnRegenerate'), [true]);
    const regen = calls.find(c => c.url === '/api/schedule/generate');
    R.recovery.regenPayloadSelected = regen ? regen.body.locked_sessions.filter(s => s.selected).map(s => s.subject_code).sort() : null;
    R.recovery.after = Object.assign({ approve: approve(), modalShown: seen.length > 0,
        saveDraftDisabled: getEl('btnSaveDraft').disabled,
        selectedRows: NEW.map(r => r.subject_code).filter(checked) }, panel());

    process.stdout.write(JSON.stringify(R));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
