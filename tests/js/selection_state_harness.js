// Loads a real Retrieve Previous/Generate response (JSON file given as argv[2])
// into the REAL page script and records the lock state every row gets through
// each way of selecting it: individual checkbox, Select Conflict Rows, Select
// Incomplete, Select All, a bulk selection over an already-selected row, and an
// attempted lock on an unresolved value. Prints one JSON object.
'use strict';
const fs = require('fs');
const page = require('./page_stub').loadPage();
const { getEl, docListeners, lockState, setRow, toggleLock, rowHtml, calls } = page;

const response = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const rows = response.schedule_data;
const codes = rows.map(r => r.subject_code);
const all = () => Object.fromEntries(codes.map(c => [c, lockState(c)]));
const clear = async () => { for (const r of rows) await setRow(r, false); };
const byCode = (c) => rows.find(r => r.subject_code === c);
const originOf = (code, field) => {
    const m = rowHtml(code).match(new RegExp(`data-origin="([a-z]+)"(?:\\s+disabled)?\\s+data-row-key="[^"]*"\\s+data-field="${field}"`));
    return m ? m[1] : null;
};

(async () => {
    const R = {};
    for (const fn of (docListeners.DOMContentLoaded || [])) await fn();
    Object.assign(getEl('acadYear'), { value: 'AY2627' });
    Object.assign(getEl('term'), { value: 'A' });
    Object.assign(getEl('program'), { value: 'BEED' });
    Object.assign(getEl('yearLevel'), { value: '4' });
    Object.assign(getEl('sectionFilter'), { value: '12' });
    Object.assign(getEl('curriculum'), { value: '2022-2023' });
    getEl('useHistorical').checked = true;
    page.nextResponse = () => response;
    calls.length = 0;
    await getEl('btnGenerate').click();
    for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r));
    // Automatic selection on display (incomplete rows only), no regeneration.
    R.initial = all();
    R.initialGenerateCalls = calls.filter(c => c.url === '/api/schedule/generate').length;

    R.manual = {};
    for (const r of rows) { await setRow(r, true); R.manual[r.subject_code] = lockState(r.subject_code); await setRow(r, false); }
    // Unchecked by hand: stay unchecked (other actions don't re-check them).
    await getEl('btnToggleConflictDetails').click();
    R.afterManualUnchecks = all();

    await getEl('btnSelectConflictRows').click();
    R.selectConflictRows = all();
    await clear();

    await getEl('btnSelectIncomplete').click();
    R.selectIncomplete = all();
    await clear();

    getEl('chkSelectAllRows').checked = true;
    await getEl('chkSelectAllRows').dispatch('change', { target: { checked: true } });
    R.selectAll = all();
    getEl('chkSelectAllRows').checked = false;
    await getEl('chkSelectAllRows').dispatch('change', { target: { checked: false } });

    // Already selected before the bulk button: the last row, with its first
    // valid field USER-locked and another field's state changed by hand.
    const last = rows[rows.length - 1];
    await setRow(last, true);
    const before = lockState(last.subject_code);
    const validField = ['faculty', 'schedule', 'room'].find(f => originOf(last.subject_code, f) !== 'unresolved');
    // Make it a USER lock: if it starts unlocked one click locks it; if it is
    // auto-kept, unlock then re-lock.
    if (before[validField] === 'is-unlocked') await toggleLock(last, validField);
    else { await toggleLock(last, validField); await toggleLock(last, validField); }
    R.preBulk = { code: last.subject_code, userField: validField,
                  state: lockState(last.subject_code), origin: originOf(last.subject_code, validField) };
    await getEl('btnSelectConflictRows').click();
    R.afterBulkOnSelected = { state: lockState(last.subject_code), origin: originOf(last.subject_code, validField) };
    await getEl('btnSelectIncomplete').click();
    R.afterSecondBulkOnSelected = { state: lockState(last.subject_code), origin: originOf(last.subject_code, validField) };
    await clear();

    // Try to lock an unresolved Room (TBA / nonexistent) on every row that has one.
    R.lockUnresolved = {};
    for (const r of rows) {
        await setRow(r, true);
        if (originOf(r.subject_code, 'room') === 'unresolved') {
            await toggleLock(r, 'room');
            R.lockUnresolved[r.subject_code] = { room: lockState(r.subject_code).room, origin: originOf(r.subject_code, 'room') };
        }
        await setRow(r, false);
    }

    process.stdout.write(JSON.stringify(R));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
