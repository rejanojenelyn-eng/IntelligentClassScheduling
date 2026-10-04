// Runs the REAL static/js/ACAD HEAD/scheduleGeneration.acad.js against a
// minimal stub DOM (no jsdom dependency) and drives the selective-regeneration
// UI: retrieve a schedule, check/uncheck rows, toggle locks, Select All,
// Select Incomplete, Re-generate Selected. Prints one JSON object with every
// observation; tests/test_retrieve_previous_and_selective_regen.py asserts on it.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// ── Minimal DOM stub ──────────────────────────────────────────────────────
function makeEl(id) {
    const listeners = {};
    const classes = new Set(id === 'calendarViewContainer' ? ['hidden'] : []);
    const el = {
        id, value: '', checked: false, disabled: false, textContent: '', innerHTML: '',
        style: {}, dataset: {}, options: [], selectedIndex: 0, offsetWidth: 0, offsetHeight: 0,
        classList: {
            add: (...c) => c.forEach(x => classes.add(x)),
            remove: (...c) => c.forEach(x => classes.delete(x)),
            toggle: (c, on) => { const v = on === undefined ? !classes.has(c) : on; v ? classes.add(c) : classes.delete(c); return v; },
            contains: c => classes.has(c),
        },
        addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
        removeEventListener: () => {},
        dispatch: async (t, ev) => { for (const fn of (listeners[t] || [])) await fn(ev); },
        click: () => el.dispatch('click', { target: el, preventDefault() {}, stopPropagation() {} }),
        appendChild: () => {}, remove: () => {}, focus: () => {},
        querySelector: () => makeEl('_q'), querySelectorAll: () => [],
        closest: () => null, contains: () => false, setAttribute: () => {}, getAttribute: () => null,
    };
    return el;
}
const els = {};
const getEl = id => (els[id] = els[id] || makeEl(id));
const docListeners = {};
const document = {
    getElementById: getEl,
    querySelector: sel => getEl('q:' + sel),
    querySelectorAll: () => [],
    createElement: t => makeEl('new:' + t),
    addEventListener: (t, fn) => { (docListeners[t] = docListeners[t] || []).push(fn); },
    body: makeEl('body'),
};

const calls = [];
let nextResponse = null;
async function fetch(url, opts) {
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    calls.push({ url: String(url), body });
    const payload = nextResponse ? nextResponse(String(url), body) : { success: true };
    return { ok: true, status: 200, json: async () => payload };
}
const storage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const sandbox = {
    document, fetch, localStorage: storage, sessionStorage: storage, console,
    window: { location: { href: '' }, addEventListener: () => {} },
    setTimeout: (fn) => { fn(); return 0; }, clearTimeout: () => {},
    setInterval: () => 0, clearInterval: () => {},
    requestAnimationFrame: () => 0, AbortController, URLSearchParams,
    MANUAL_EDITOR_URL: '/x', confirm: () => true, alert: () => {},
};
sandbox.window.document = document;
vm.createContext(sandbox);
const src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'ACAD HEAD', 'scheduleGeneration.acad.js'), 'utf8');
vm.runInContext(src, sandbox);

// ── Helpers over the rendered table ───────────────────────────────────────
const tbody = () => getEl('scheduleTableBody');
function rowHtml(code) {
    const rows = tbody().innerHTML.split('<tr').slice(1);
    return rows.find(r => r.includes(`<td class="td-code">${code}</td>`)) || '';
}
function lockState(code) {
    const html = rowHtml(code);
    const out = {};
    for (const f of ['faculty', 'schedule', 'room']) {
        const m = html.match(new RegExp(`class="regen-lock (is-locked|is-unlocked)"[^>]*data-field="${f}"`));
        out[f] = m ? m[1] : null;
    }
    out.icons = (html.match(/class="regen-lock /g) || []).length;
    return out;
}
const keyOf = (r) => r.subject_code + '||' + (r.faculty_id || r.instructor || '');
async function setRow(row, checked) {
    await tbody().dispatch('change', {
        target: { checked, dataset: { rowKey: keyOf(row) }, classList: { contains: c => c === 'row-select' } },
    });
}
async function toggleLock(row, field) {
    const btn = { dataset: { rowKey: keyOf(row), field } };
    await tbody().dispatch('click', { target: { closest: sel => (sel === '.regen-lock' ? btn : null) } });
}

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
    nextResponse = () => ({ success: true, schedule_data: ROWS, retrieved_from: { ay_label: 'AY202526' },
                            evaluation: null, conflict_count: 0 });
    await getEl('btnGenerate').click();
    getEl('useHistorical').checked = false;

    R.unselected = lockState(ELED.subject_code);                 // no icons
    await setRow(ELED, true);
    R.checked = lockState(ELED.subject_code);                    // 1: all unlocked

    const regenerate = async () => {
        calls.length = 0;
        nextResponse = () => ({ success: true, result_status: 'COMPLETE_VALID', schedule_data: ROWS, conflict_count: 0 });
        await getEl('btnRegenerate').click();
        const call = calls.find(c => c.url === '/api/schedule/generate');
        if (!call) return null;
        const m = {};
        call.body.locked_sessions.forEach(s => { m[s.subject_code] = { selected: s.selected, lock: s.lock }; });
        return m;
    };
    R.payloadDefault = await regenerate();                       // 2

    await toggleLock(ELED, 'faculty');
    R.lockedInstructor = lockState(ELED.subject_code);
    R.payloadInstructor = await regenerate();                    // 3

    await toggleLock(ELED, 'room');
    R.payloadInstructorRoom = await regenerate();                // 4

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

    process.stdout.write(JSON.stringify(R));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
