// Manual Scheduler state harness: loads the REAL static/js/ACAD HEAD/manualEditor.acad2.js
// and the REAL inline <script> blocks of templates/academic/manualScheduleEditor.html into
// ONE vm context -- exactly how the browser evaluates them (shared global lexical scope,
// later definitions win) -- with a permissive stub DOM and a scripted fetch(). Each
// scenario drives the real functions and reports pendingManualSchedule, hiddenDbSchedules
// and the pills the real renderGrid would draw. Output: one JSON object on stdout.
'use strict';
const fs = require('fs'), path = require('path'), vm = require('vm');
const ROOT = path.join(__dirname, '..', '..');

function makeEnv() {
    const log = [], fetchLog = [], modals = [];
    const els = {};
    function makeEl(id) {
        const cls = new Set();
        const el = {
            id, value: '', textContent: '', innerHTML: '', disabled: false, checked: false,
            style: {}, dataset: {}, options: [], selectedIndex: 0, children: [],
            offsetWidth: 0, offsetHeight: 0, scrollTop: 0,
            classList: { add: (...c) => c.forEach(x => cls.add(x)), remove: (...c) => c.forEach(x => cls.delete(x)),
                         toggle: (c, on) => { const v = on === undefined ? !cls.has(c) : on; v ? cls.add(c) : cls.delete(c); return v; },
                         contains: c => cls.has(c) },
            addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true, click: () => {},
            appendChild: c => c, insertBefore: c => c, removeChild: () => {}, remove: () => { delete els[id]; },
            insertAdjacentHTML: () => {}, focus: () => {}, blur: () => {}, scrollIntoView: () => {},
            setAttribute: () => {}, getAttribute: () => null, removeAttribute: () => {}, hasAttribute: () => false,
            getBoundingClientRect: () => ({ top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 }),
            closest: () => null, contains: () => false, matches: () => false,
            querySelector: () => makeEl('_q'), querySelectorAll: () => [], cloneNode: () => makeEl(id + '_c'),
        };
        return el;
    }
    const getEl = id => (els[id] = els[id] || makeEl(id));
    const document = {
        getElementById: getEl,
        querySelector: sel => (sel === '.editor-right-panel-new' ? null : makeEl('q:' + sel)),
        querySelectorAll: sel => (sel === '.ts-row' ? Object.values(els).filter(e => e._isRow) : []),
        createElement: t => makeEl('new:' + t), createTextNode: () => makeEl('text'),
        addEventListener: () => {}, removeEventListener: () => {},
        body: makeEl('body'), documentElement: makeEl('html'), readyState: 'complete',
    };
    const routes = [];
    async function fetch(url, opts = {}) {
        url = String(url);
        fetchLog.push({ url, body: opts.body ? JSON.parse(opts.body) : null });
        const r = routes.find(([frag]) => url.includes(frag));
        const payload = r ? (typeof r[1] === 'function' ? await r[1](url) : r[1]) : [];
        if (payload === 'REJECT') throw new TypeError('Failed to fetch');
        return { ok: true, status: 200, json: async () => payload };
    }
    const storage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
    const sandbox = {
        document, fetch, localStorage: storage, sessionStorage: storage,
        console: { log: (...a) => log.push(a.map(x => typeof x === 'string' ? x : JSON.stringify(x)).join(' ')),
                   warn: () => {}, error: () => {}, info: () => {}, debug: () => {} },
        setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
        requestAnimationFrame: () => 0, AbortController, URLSearchParams, URL,
        confirm: () => true, alert: () => {}, performance: { now: () => 0 },
        navigator: { userAgent: 'node' }, location: { href: '', search: '', reload() {} },
        MutationObserver: class { observe() {} disconnect() {} }, ResizeObserver: class { observe() {} disconnect() {} },
        IntersectionObserver: class { observe() {} disconnect() {} },
        getComputedStyle: () => ({ getPropertyValue: () => '' }), CSS: { escape: s => s },
        Event: class {}, CustomEvent: class {},
    };
    sandbox.window = sandbox; sandbox.self = sandbox; sandbox.globalThis = sandbox;
    sandbox.addEventListener = () => {}; sandbox.removeEventListener = () => {};
    vm.createContext(sandbox);
    const errors = [];
    const run = (label, src) => { try { vm.runInContext(src, sandbox, { filename: label }); } catch (e) { errors.push(`${label}: ${e.message}`); } };

    Object.assign(getEl('app-init-data').dataset, {
        rooms: JSON.stringify([{ id: '5', name: 'R5' }, { id: '7', name: 'R7' }]),
        faculty: JSON.stringify([{ id: 'F1', name: 'Dela Cruz, Juan' }, { id: 'F2', name: 'Santos, Ana' }]),
        weekendEnabled: 'true', mergeEnabled: 'true', mergeScope: 'nstp_only',
    });
    run('acad2.js', fs.readFileSync(path.join(ROOT, 'static', 'js', 'ACAD HEAD', 'manualEditor.acad2.js'), 'utf8'));
    const html = fs.readFileSync(path.join(ROOT, 'templates', 'academic', 'manualScheduleEditor.html'), 'utf8');
    const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
    let m, i = 0;
    while ((m = re.exec(html))) {
        run(`inline#${i++}`, m[1].replace(/\{\{\s*scheduler_mode\s*\}\}/g, 'official')
                                 .replace(/\{\{[\s\S]*?\}\}/g, '').replace(/\{%[\s\S]*?%\}/g, ''));
    }
    sandbox.__get = new vm.Script('(n) => eval(n)').runInContext(sandbox);
    sandbox.__set = new vm.Script('(n, v) => { globalThis.__v = v; eval(n + " = globalThis.__v"); }').runInContext(sandbox);
    return { sandbox, els, getEl, routes, fetchLog, log, errors, modals };
}

// ── fixtures (existing_sessions / get_room_schedule row shapes) ────────────
const T = ['07:30 AM','08:00 AM','08:30 AM','09:00 AM','09:30 AM','10:00 AM','10:30 AM','11:00 AM','11:30 AM',
           '12:00 PM','12:30 PM','01:00 PM','01:30 PM','02:00 PM','02:30 PM','03:00 PM'];
function dbRow(code, day, st, et, vid, sid, sectId, sectName, extra = {}) {
    return Object.assign({ sessionid: sid, official_sessionid: sid, starttimeid: st, endtimeid: et, daydesc: day,
        versionid: vid, subjectcode: code, subjectname: code, yearlevel: 1, year_level: 1, programcode: 'BSIT',
        sectionid: sectId, section_id: sectId, sectionname: sectName, roomid: 5, roomname: 'R5',
        employeenumber: 'FX', employee_number: 'FX', instructor: 'Unlisted, Faculty', status: 'Published',
        start_fmt: T[st - 1], end_fmt: T[et - 1] }, extra);
}
// Subject A: two Published slices sharing ONE schedule_version (v100), distinct sessions.
const A_MON = dbRow('A101', 'Monday',   4, 7, 100, 1001, 9, 'BSIT1A');
const A_THU = dbRow('A101', 'Thursday', 4, 7, 100, 1002, 9, 'BSIT1A');
const B_TUE = dbRow('B201', 'Tuesday', 13, 16, 200, 2001, 9, 'BSIT1A');
const A_1B  = dbRow('A101', 'Friday',   4, 7, 101, 1101, 10, 'BSIT1B');

function setup(roomRows) {
    const env = makeEnv();
    const s = env.sandbox, E = env.getEl;
    Object.entries({ sel_ay: 'AY2627', sel_sem: 'A', sel_prog: 'BSIT', sel_year: '1', sel_section: '9',
                     sel_room: '5', sel_faculty: 'FX', fac_display_name: 'Faculty X', room_display_name: 'R5' })
        .forEach(([k, v]) => { E(k).value = v; });
    E('bc-sect-text').textContent = 'BSIT1A';
    const subj = E('sel_subj');
    subj.options = ['A101', 'B201'].map(c => ({ value: c, text: c + ' Subject',
        dataset: { hours: '3', units: '3', dbScheduled: '0', savedStatus: '' } }));
    Object.defineProperty(subj, 'selectedIndex', { get: () => Math.max(0, subj.options.findIndex(o => o.value === subj.value)) });
    s._DEBUG_RENDERGRID = true;
    let rowSeq = 0;
    s.addNewTimeSlot = (day = '', st = '', et = '', roomId = '', existingJson = '') => {
        const id = ++rowSeq, row = E(`ts-row-${id}`);
        const fld = v => ({ value: v, dataset: {}, innerHTML: '', querySelectorAll: () => [], querySelector: () => null,
                            classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
                            closest: () => row, addEventListener() {} });
        const f = { '.ts-day-sel': fld(day), '.ts-start-hidden': fld(st), '.ts-end-hidden': fld(et),
                    '.ts-room-hidden': fld(roomId), '.ts-room-field .ts-ss-input': fld('') };
        Object.assign(row, { _isRow: true, dataset: existingJson ? { existingJson } : {} });
        row.querySelector = sel => f[sel] || null;
        return id;
    };
    s._injectTimeOpt = (id, v) => { E(id).value = v; };
    s.showValidationModal = async (t) => { env.modals.push(t); };
    s.showConflictModal   = async (m) => { env.modals.push('CONFLICT ' + m); };
    s.showConfirmModal    = async () => true;
    s.showNochangeModal   = async () => { env.modals.push('NO-CHANGE'); };
    s._showPublishSelectModal = async (c) => c;
    s._confirmSubjectSwitch = async () => true;      // user chooses "discard and switch"
    env.routes.push(['/api/get_room_schedule/5', () => roomRows()]);
    env.routes.push(['/api/manual/faculty_schedule', []], ['/api/manual/section_schedule', []]);
    env.routes.push(['/api/schedule/draft_sessions', { success: true, sessions: [] }]);
    // Official Approve first checks for Local Arrangements it would archive (none here).
    env.routes.push(['/api/schedule/local_republish_impact', { success: true, sessions: [] }]);
    env.routes.push(['/api/manual/existing_sessions', (url) => {
        const q = new URL('http://x' + url);
        const code = q.searchParams.get('subject_code'), sect = q.searchParams.get('section_id');
        return { success: true, sessions: roomRows().filter(r => r.subjectcode === code && (!sect || String(r.sectionid) === sect)) };
    }]);
    return env;
}

const g = (env, n) => env.sandbox.__get(n);
function state(env) {
    return {
        pending: g(env, 'pendingManualSchedule').map(c => ({
            subject: c.subject_code, day: c.day, start: c.start_time, temp_id: c.temp_id,
            fromExisting: !!c.fromExisting, versionid: c.versionid ?? null, sessionid: c.sessionid ?? null,
            official_sessionid: c.official_sessionid ?? null, section_id: c.section_id ?? null,
            loaded_day: c._loaded ? c._loaded.day : null, status: c.status })),
        hidden: Array.from(g(env, 'hiddenDbSchedules')).sort(),
    };
}
async function pills(env) {
    env.log.length = 0;
    await g(env, 'renderGrid')('5', 'AY2627', 'A');
    // "[renderGrid] FINAL pills to draw: N — [...]": the JSON list follows the dash.
    const line = env.log.filter(l => l.startsWith('[renderGrid] FINAL')).pop() || '';
    const at = line.indexOf('— ');
    return at >= 0 ? JSON.parse(line.slice(at + 2)).sort() : [];
}
async function snap(env) { return Object.assign(state(env), { pills: await pills(env) }); }
async function safely(fn) { try { await fn(); } catch (e) { /* downstream UI stubs; state already applied */ } }
async function openSubject(env, code, sessions, token) {
    env.sandbox.__set('_currentSubjectCode', code);
    env.getEl('sel_subj').value = code;
    await safely(() => g(env, '_loadExistingSessionsIntoSlices')(sessions, token));
}
function editRow(env, id, day) {
    env.getEl(`ts-row-${id}`).querySelector('.ts-day-sel').value = day;
    g(env, '_dirtySliceIds').add(id);
    g(env, '_syncSliceToPending')(id);
}

const APPROVE_OUTCOMES = {
    violations:        { success: false, violations: [{ type: 'Conflict: Faculty Schedule', detail: 'double-booked' }] },
    error:             { success: false, error: 'Cannot approve: something failed.' },
    network:           'REJECT',
    replace_declined:  { success: false, needs_confirmation: true, existing_info: {} },
};

const SCENARIOS = {
    async failed_approve() {
        const out = {};
        for (const [name, resp] of Object.entries(APPROVE_OUTCOMES)) {
            const env = setup(() => [A_MON, A_THU, B_TUE]);
            env.routes.unshift(['/api/schedule/approve', resp]);
            if (name === 'replace_declined') env.sandbox.showConfirmModal = async () => false;
            await openSubject(env, 'A101', [A_MON, A_THU]);
            editRow(env, 1, 'Wednesday');
            const before = await snap(env);
            let result;
            await safely(async () => { result = await g(env, '_runManualApprove')(); });
            const after = await snap(env);
            out[name] = { before, after, result: result ?? null,
                          approve_called: env.fetchLog.some(f => f.url.includes('/api/schedule/approve')),
                          in_flight: !!env.sandbox._approveInFlight, load_errors: env.errors };
        }
        return out;
    },
    async save_slice_identity() {
        const env = setup(() => [A_MON, A_THU, B_TUE]);
        await openSubject(env, 'A101', [A_MON, A_THU]);
        const loaded = state(env);
        const origJson = env.getEl('ts-row-1').dataset.existingJson;
        editRow(env, 1, 'Wednesday');
        await safely(() => g(env, 'saveIndividualSlice')(1));
        const saved = await snap(env);
        return { loaded, saved, existing_json_unchanged: env.getEl('ts-row-1').dataset.existingJson === origJson };
    },
    async multi_slice_hiding() {
        const env = setup(() => [A_MON, A_THU, B_TUE]);
        await openSubject(env, 'A101', [A_MON, A_THU]);
        editRow(env, 1, 'Wednesday');
        await safely(() => g(env, 'saveIndividualSlice')(1));
        await safely(() => g(env, '_onSubjectClick')('B201', 'B201', 3, 3));   // discard A's edit
        const afterSwitch = await snap(env);
        // Direct: only Thursday's mirror left -> Monday's DB row must stay visible.
        const env2 = setup(() => [A_MON, A_THU, B_TUE]);
        await openSubject(env2, 'A101', [A_MON, A_THU]);
        env2.sandbox.__set('pendingManualSchedule', g(env2, 'pendingManualSchedule').filter(c => c.day === 'Thursday'));
        g(env2, '_rebuildHiddenDbSchedules')();
        return { afterSwitch, onlyThursdayMirror: await snap(env2) };
    },
    async late_response() {
        const env = setup(() => [A_MON, A_THU, B_TUE]);
        await openSubject(env, 'A101', [A_MON, A_THU]);
        const loaded = await snap(env);
        const staleToken = { subject: 'A101', section: '9', seq: 1 };
        env.sandbox._existingSessionsSeq = 2;                    // a newer request started
        env.getEl('sel_subj').value = 'B201';                    // user is on B now
        env.sandbox.__set('_currentSubjectCode', 'B201');
        await safely(() => g(env, '_loadExistingSessionsIntoSlices')([A_MON, A_THU], staleToken));
        const afterStaleToken = await snap(env);
        await safely(() => g(env, '_loadExistingSessionsIntoSlices')([A_MON, A_THU]));   // no token, subject mismatch
        const afterNoToken = await snap(env);
        return { loaded, afterStaleToken, afterNoToken };
    },
    async section_isolation() {
        const env = setup(() => [A_MON, A_THU, A_1B]);
        await openSubject(env, 'A101', [A_MON, A_THU]);
        const loaded1A = await snap(env);
        env.getEl('sel_section').value = '10'; env.getEl('bc-sect-text').textContent = 'BSIT1B';
        env.sandbox._existingSessionsSeq = 5;
        // late response for section 1A arrives while 1B is on screen
        await safely(() => g(env, '_loadExistingSessionsIntoSlices')([A_MON, A_THU], { subject: 'A101', section: '9', seq: 5 }));
        const afterLate1A = await snap(env);
        await openSubject(env, 'A101', [A_1B], { subject: 'A101', section: '10', seq: 5 });
        const loaded1B = await snap(env);
        return { loaded1A, afterLate1A, loaded1B };
    },
    async empty_faculty_fallback() {
        const env = setup(() => [A_MON]);
        const unknown = dbRow('A101', 'Monday', 4, 7, 100, 1001, 9, 'BSIT1A', { employeenumber: 'ZZ', instructor: '' });
        env.getEl('sel_faculty').value = '';
        await openSubject(env, 'A101', [unknown]);
        return { sel_faculty: env.getEl('sel_faculty').value, mirrors: state(env).pending.length };
    },
    // ── sibling-slice overlap (same subject + section) and failed-Save rollback ──
    async sibling_overlap() {
        // A101 Mon/Thu 09:00-10:30 AM (s1001/s1002). Each case: run Save (confirmAllSlots)
        // or Approve and report modals, server calls, state before/after and whether the
        // grid was redrawn after the call.
        async function run(prep, action, routes = []) {
            const env = setup(() => [A_MON, A_THU, B_TUE]);
            env.routes.unshift(['/api/schedule/validate', { success: true, has_violations: false, violations: [] }]);
            env.routes.unshift(['/api/schedule/save-draft', { success: true }]);
            env.routes.unshift(['/api/schedule/approve', { success: true }]);
            routes.forEach(r => env.routes.unshift(r));             // case-specific routes win
            env.sandbox._showSliceSaveModal = async (c) => c;
            env.sandbox._showFirstViolation = async (v) => { env.modals.push('VIOLATION ' + v[0].detail); };
            await prep(env);
            const before = Object.assign(await snap(env), { dirty: Array.from(g(env, '_dirtySliceIds')).sort() });
            env.modals.length = 0; env.fetchLog.length = 0; env.log.length = 0;
            await safely(() => g(env, action)());
            const redrawn = env.log.some(l => l.startsWith('[renderGrid] FINAL'));
            const calls = env.fetchLog.map(f => f.url.split('?')[0]).filter(u => u.startsWith('/api/schedule/'));
            const after = Object.assign(await snap(env), { dirty: Array.from(g(env, '_dirtySliceIds')).sort() });
            return { modals: [...env.modals], calls, before, after, redrawn };
        }
        const open = (sessions = [A_MON, A_THU]) => env => openSubject(env, 'A101', sessions);
        const addSlice = (day, st, et, room, sessions) => async env => {
            await open(sessions)(env);
            const id = env.sandbox.addNewTimeSlot(day, st, et, room, '');
            g(env, '_dirtySliceIds').add(id); g(env, '_syncSliceToPending')(id);
        };
        const editSlice = (id, day, st, et) => async env => {
            await open()(env);
            const row = env.getEl(`ts-row-${id}`);
            row.querySelector('.ts-day-sel').value = day;
            row.querySelector('.ts-start-hidden').value = st;
            row.querySelector('.ts-end-hidden').value = et;
            g(env, '_dirtySliceIds').add(id); g(env, '_syncSliceToPending')(id);
        };
        const A_THU_DB = ['/api/manual/section_schedule',
            [{ subjectcode: 'A101', daydesc: 'Thursday', starttimeid: 4, endtimeid: 7, status: 'Published', official_sessionid: 1002 }]];
        const A_THU_DB_WITH_DRAFT = ['/api/manual/section_schedule', [
            { subjectcode: 'A101', daydesc: 'Thursday', starttimeid: 4, endtimeid: 7, status: 'Published', official_sessionid: 1002 },
            { subjectcode: 'A101', daydesc: 'Friday',   starttimeid: 4, endtimeid: 7, status: 'Draft',     official_sessionid: 3002 }]];
        return {
            new_same_room:  await run(addSlice('Monday', '09:30 AM', '11:00 AM', '5'), 'confirmAllSlots'),
            new_other_room: await run(addSlice('Monday', '09:30 AM', '11:00 AM', '6'), 'confirmAllSlots'),
            edited:         await run(editSlice(1, 'Thursday', '09:30 AM', '11:00 AM'), 'confirmAllSlots'),
            db_only:        await run(addSlice('Thursday', '09:30 AM', '11:00 AM', '6', [A_MON]), 'confirmAllSlots', [A_THU_DB]),
            db_draft_supersedes: await run(addSlice('Thursday', '09:30 AM', '11:00 AM', '6', [A_MON]), 'confirmAllSlots', [A_THU_DB_WITH_DRAFT]),
            adjacent:       await run(addSlice('Monday', '10:30 AM', '12:00 PM', '6'), 'confirmAllSlots'),
            server_rejects: await run(addSlice('Monday', '10:30 AM', '12:00 PM', '6'), 'confirmAllSlots',
                [['/api/schedule/save-draft', { success: false, violations: [{ rule: 'HC12', detail: 'server says no' }] }]]),
            approve_new:    await run(addSlice('Monday', '09:30 AM', '11:00 AM', '6'), '_runManualApprove'),
            approve_edited: await run(editSlice(1, 'Thursday', '09:30 AM', '11:00 AM'), '_runManualApprove'),
        };
    },
    // ── complete Draft: Save → (server Draft) → reload → Approve ──
    async complete_draft() {
        const slot = c => `${c.day}|${c.start_time}-${c.end_time}`;
        async function run(act, initial = [A_MON, A_THU]) {
            const env = setup(() => [...initial, B_TUE]);
            env.routes.unshift(['/api/schedule/validate', { success: true, has_violations: false, violations: [] }]);
            env.routes.unshift(['/api/schedule/save-draft', { success: true }]);
            env.routes.unshift(['/api/schedule/delete_session', { success: true, whole_version_deleted: false }]);
            env.sandbox._showSliceSaveModal = async (c) => c;
            await openSubject(env, 'A101', initial);
            await act(env);
            env.modals.length = 0; env.fetchLog.length = 0;
            await safely(() => g(env, 'confirmAllSlots')());
            const body = (env.fetchLog.find(f => f.url.includes('/api/schedule/save-draft')) || {}).body;
            const payload = body ? body.schedule_data : [];
            const saved = payload.map(c => ({ slot: slot(c), fromExisting: !!c.fromExisting,
                                              sessionid: c.sessionid ?? null, section_id: c.section_id ?? null }));

            // Server side (api_save_draft + existing_sessions): the payload becomes the
            // subject's Draft; slots that still match a Published slot read back as 'Published'.
            const pubSlots = new Set([A_MON, A_THU].map(r => `${r.daydesc}|${r.starttimeid}`));
            const draftRows = payload.map((c, i) => {
                const st = T.indexOf(c.start_time) + 1, et = T.indexOf(c.end_time) + 1;
                return dbRow('A101', c.day, st, et, 300, 3001 + i, 9, 'BSIT1A',
                             { status: pubSlots.has(`${c.day}|${st}`) ? 'Published' : 'Draft' });
            });
            const env2 = setup(() => [...draftRows, B_TUE]);
            let approveBody = null;
            env2.routes.unshift(['/api/schedule/approve', (u, opts) => ({ success: true })]);
            await openSubject(env2, 'A101', draftRows);
            const reloaded = state(env2).pending.filter(p => p.subject === 'A101').map(p => `${p.day}|${p.start}`).sort();
            env2.modals.length = 0; env2.fetchLog.length = 0;
            await safely(() => g(env2, '_runManualApprove')());
            const ap = env2.fetchLog.find(f => f.url.includes('/api/schedule/approve'));
            approveBody = ap ? ap.body.schedule_data.map(slot).sort() : null;
            return { saved, save_modals: [...env.modals], reloaded, approved: approveBody,
                     approve_modals: [...env2.modals] };
        }
        const setRow = (env, id, day, st, et) => {
            const row = env.getEl(`ts-row-${id}`);
            row.querySelector('.ts-day-sel').value = day;
            if (st) row.querySelector('.ts-start-hidden').value = st;
            if (et) row.querySelector('.ts-end-hidden').value = et;
            g(env, '_dirtySliceIds').add(id); g(env, '_syncSliceToPending')(id);
        };
        const addRow = (env, day, st, et) => {
            const id = env.sandbox.addNewTimeSlot(day, st, et, '5', '');
            g(env, '_dirtySliceIds').add(id); g(env, '_syncSliceToPending')(id);
        };
        return {
            edit_one:   await run(async env => setRow(env, 1, 'Wednesday')),
            add_one:    await run(async env => addRow(env, 'Friday', '09:00 AM', '10:30 AM')),
            edit_add:   await run(async env => { setRow(env, 1, 'Wednesday'); addRow(env, 'Friday', '09:00 AM', '10:30 AM'); }),
            // Re-save over an existing Draft: unchanged Thu reads back relabelled 'Published'.
            resave_draft: await run(async env => setRow(env, 1, 'Friday'), [
                dbRow('A101', 'Wednesday', 4, 7, 300, 3001, 9, 'BSIT1A', { status: 'Draft' }),
                dbRow('A101', 'Thursday',  4, 7, 300, 3002, 9, 'BSIT1A', { status: 'Published' })]),
            remove_edit: await run(async env => {
                await safely(() => env.sandbox.removeTimeSlot(2));     // Thu deleted server-side
                setRow(env, 1, 'Wednesday');
            }),
        };
    },
    async complete_draft_rules() {
        const env = setup(() => []);
        const e = (subject, day, extra = {}) => Object.assign({ ay: 'AY2627', sem: 'A', subject_code: subject,
            day, start_time: '09:00 AM', end_time: '10:30 AM', section_id: '9' }, extra);
        const pub = { fromExisting: true, status: 'Published' };
        const changed = [e('A101', 'Wednesday', { sessionid: 1001, temp_id: 'D1' })];
        env.sandbox.__set('pendingManualSchedule', [
            ...changed,
            e('A101', 'Monday',   Object.assign({ sessionid: 1001, temp_id: 'M-stale' }, pub)), // edited occurrence
            e('A101', 'Thursday', Object.assign({ sessionid: 1002, temp_id: 'M-thu' }, pub)),
            e('A101', 'Thursday', Object.assign({ sessionid: 1002, temp_id: 'M-thu-dup' }, pub)),
            e('A101', 'Friday',   Object.assign({ sessionid: 1101, temp_id: 'M-1B', section_id: '10' }, pub)),
            e('A101', 'Tuesday',  Object.assign({ temp_id: 'PREVIEW_9', isPreview: true }, pub)),
            e('B201', 'Tuesday',  Object.assign({ sessionid: 2001, temp_id: 'M-B' }, pub)),       // untouched subject
        ]);
        return g(env, '_completeSubjectDrafts')(changed, 'AY2627', 'A', '9').map(c => c.temp_id);
    },
    async dirty_state() {
        // Dirty only clears after the server confirms persistence; any failure keeps it.
        const dirty = env => ({
            unsaved: !!g(env, 'hasUnsavedChanges')(),
            removed: g(env, '_touchedCodesForSection')('9'),
            faculty: !!env.sandbox._pendingFacultyAssignment,
        });
        const out = {};
        const SAVE = { ok: { success: true, draft_version: 2 },
                       error: { success: false, error: 'Unable to save the schedule.' },
                       network: 'REJECT' };
        for (const [name, resp] of Object.entries(SAVE)) {
            const env = setup(() => [A_MON, A_THU, B_TUE]);
            env.routes.unshift(['/api/schedule/validate', { success: true, has_violations: false, violations: [] }]);
            env.routes.unshift(['/api/schedule/save-draft', resp]);
            env.sandbox._showSliceSaveModal = async (c) => c;
            await openSubject(env, 'A101', [A_MON, A_THU]);
            const clean = dirty(env);
            editRow(env, 1, 'Wednesday');
            const edited = dirty(env);
            await safely(() => g(env, 'confirmAllSlots')());
            out['save_' + name] = { clean, edited, after: dirty(env) };
        }
        const PUB = { ok: { success: true, published_version: 3 },
                      error: { success: false, error: 'Cannot approve: something failed.' },
                      network: 'REJECT' };
        for (const [name, resp] of Object.entries(PUB)) {
            const env = setup(() => [A_MON, A_THU, B_TUE]);
            env.routes.unshift(['/api/schedule/approve', resp]);
            await openSubject(env, 'A101', [A_MON, A_THU]);
            editRow(env, 1, 'Wednesday');
            const edited = dirty(env);
            await safely(() => g(env, '_runManualApprove')());
            out['publish_' + name] = { edited, after: dirty(env) };
        }
        // A faculty pick saved with the subject is no longer pending.
        {
            const env = setup(() => [A_MON, A_THU, B_TUE]);
            env.routes.unshift(['/api/schedule/validate', { success: true, has_violations: false, violations: [] }]);
            env.routes.unshift(['/api/schedule/save-draft', { success: true }]);
            env.sandbox._showSliceSaveModal = async (c) => c;
            await openSubject(env, 'A101', [A_MON, A_THU]);
            editRow(env, 1, 'Wednesday');
            env.sandbox._pendingFacultyAssignment = true;
            await safely(() => g(env, 'confirmAllSlots')());
            out.faculty_saved = dirty(env);
        }
        return out;
    },
    async edit_kinds() {
        // Every kind of edit makes the editor dirty; only a confirmed save clears it.
        const setRow = (env, id, f) => {
            const row = env.getEl(`ts-row-${id}`);
            Object.entries(f).forEach(([sel, v]) => { row.querySelector(sel).value = v; });
            g(env, '_dirtySliceIds').add(id); g(env, '_syncSliceToPending')(id);
        };
        const KINDS = {
            change_day:   env => setRow(env, 1, { '.ts-day-sel': 'Wednesday' }),
            change_time:  env => setRow(env, 1, { '.ts-start-hidden': '10:30 AM', '.ts-end-hidden': '12:00 PM' }),
            change_room:  env => setRow(env, 1, { '.ts-room-hidden': '7' }),
            add_slice:    env => {
                const id = env.sandbox.addNewTimeSlot('Friday', '01:00 PM', '02:30 PM', '5', '');
                g(env, '_dirtySliceIds').add(id); g(env, '_syncSliceToPending')(id);
            },
            change_faculty: env => {
                env.getEl('sel_faculty').value = 'F2';
                env.sandbox._pendingFacultyAssignment = true;
                setRow(env, 1, { '.ts-day-sel': 'Monday' });       // faculty applies to the subject's slices
            },
            multi_session_edit_one: env => setRow(env, 2, { '.ts-day-sel': 'Friday' }),   // Thu -> Fri
        };
        const out = {};
        for (const [kind, act] of Object.entries(KINDS)) {
            out[kind] = {};
            for (const [outcome, resp] of Object.entries({ ok: { success: true },
                                                            error: { success: false, error: 'nope' } })) {
                const env = setup(() => [A_MON, A_THU, B_TUE]);
                env.routes.unshift(['/api/schedule/validate', { success: true, has_violations: false, violations: [] }]);
                env.routes.unshift(['/api/schedule/save-draft', resp]);
                env.routes.unshift(['/api/manual/assign_faculty', { success: true }]);
                env.sandbox._showSliceSaveModal = async (c) => c;
                await openSubject(env, 'A101', [A_MON, A_THU]);
                const clean = !!g(env, 'hasUnsavedChanges')();
                act(env);
                const edited = !!g(env, 'hasUnsavedChanges')();
                env.fetchLog.length = 0;
                await safely(() => g(env, 'confirmAllSlots')());
                const save = env.fetchLog.find(f => f.url.includes('/api/schedule/save-draft'));
                out[kind][outcome] = {
                    clean, edited, after: !!g(env, 'hasUnsavedChanges')(),
                    saved_slots: save ? save.body.schedule_data.map(c => c.day || c.daydesc).sort() : null,
                };
            }
        }
        return out;
    },
    async cache_invalidation() {
        // A confirmed Save/Publish gives rows new versionids; stale room occupancy would make a
        // slice's own old copy "occupy" its new day and blank its day dropdown.
        const out = {};
        for (const [name, route, act] of [
            ['save_ok', ['/api/schedule/save-draft', { success: true }], 'confirmAllSlots'],
            ['save_error', ['/api/schedule/save-draft', { success: false, error: 'x' }], 'confirmAllSlots'],
            ['publish_ok', ['/api/schedule/approve', { success: true }], '_runManualApprove'],
        ]) {
            const env = setup(() => [A_MON, A_THU, B_TUE]);
            env.routes.unshift(['/api/schedule/validate', { success: true, has_violations: false, violations: [] }]);
            env.routes.unshift(route);
            env.sandbox._showSliceSaveModal = async (c) => c;
            await openSubject(env, 'A101', [A_MON, A_THU]);
            editRow(env, 1, 'Wednesday');
            env.sandbox._roomDbCache = { '9|AY2627|A': [{ stale: true }], '9|AY2526|B': [{ other: true }] };   // room 9: not on screen, never re-fetched by the redraw
            await safely(() => g(env, act)());
            const c = env.sandbox._roomDbCache;
            out[name] = { this_term_stale: JSON.stringify(c['9|AY2627|A'] || null).includes('stale'),
                          other_term_kept: JSON.stringify(c['9|AY2526|B'] || null).includes('other') };
        }
        return out;
    },
    async published_removal() {
        const slot = c => `${c.day}|${c.start_time}`;
        const out = {};
        // Board-only removal of one Published slice; Save as Draft carries the rest.
        {
            const env = setup(() => [A_MON, A_THU, B_TUE]);
            env.routes.unshift(['/api/schedule/save-draft', { success: true }]);
            env.routes.unshift(['/api/schedule/delete_session', { success: false, code: 'PUBLISHED_IMMUTABLE' }]);
            await openSubject(env, 'A101', [A_MON, A_THU]);
            env.fetchLog.length = 0;
            await safely(() => env.sandbox.removeTimeSlot(1));          // Monday (Published)
            const afterRemove = {
                unsaved: !!g(env, 'hasUnsavedChanges')(),
                removed: g(env, '_touchedCodesForSection')('9'),
                hidden_mon: Array.from(g(env, 'hiddenDbSchedules')).includes('s:1001'),
                delete_called: env.fetchLog.some(f => f.url.includes('/api/schedule/delete_session')),
            };
            g(env, '_rebuildHiddenDbSchedules')();
            afterRemove.hidden_mon_after_rebuild = Array.from(g(env, 'hiddenDbSchedules')).includes('s:1001');
            env.fetchLog.length = 0;
            await safely(() => g(env, 'confirmAllSlots')());
            const save = env.fetchLog.find(f => f.url.includes('/api/schedule/save-draft'));
            out.board = Object.assign(afterRemove, {
                saved: save ? save.body.schedule_data.map(slot).sort() : null,
                after_save: { unsaved: !!g(env, 'hasUnsavedChanges')(),
                              removed: g(env, '_touchedCodesForSection')('9') },
            });
        }
        // Discarding the board removal (switch subject) brings the Published slice back.
        {
            const env = setup(() => [A_MON, A_THU, B_TUE]);
            await openSubject(env, 'A101', [A_MON, A_THU]);
            await safely(() => env.sandbox.removeTimeSlot(1));
            await safely(() => g(env, '_onSubjectClick')('B201', 'B201', 3, 3));
            out.discard = { removed: g(env, '_touchedCodesForSection')('9'),
                            hidden_mon: Array.from(g(env, 'hiddenDbSchedules')).includes('s:1001') };
        }
        // Removing a subject's LAST slice is a board edit too: nothing is sent on removal;
        // Save as Draft records an explicit removal; only Publish makes it live.
        {
            const env = setup(() => [A_MON, A_THU, B_TUE]);
            env.routes.unshift(['/api/schedule/approve', { success: true, published_version: 4 }]);
            env.routes.unshift(['/api/schedule/save-draft', { success: true, removed_subjects: ['B201'] }]);
            await openSubject(env, 'B201', [B_TUE]);
            env.fetchLog.length = 0;
            await safely(() => env.sandbox.removeTimeSlot(1));
            const onRemove = {
                server_calls: env.fetchLog.map(f => f.url.split('?')[0]),
                unsaved: !!g(env, 'hasUnsavedChanges')(),
                removed: g(env, '_touchedCodesForSection')('9'),
            };
            env.fetchLog.length = 0;
            await safely(() => g(env, 'confirmAllSlots')());
            const save = env.fetchLog.find(f => f.url.includes('/api/schedule/save-draft'));
            const onSave = {
                approve_called: env.fetchLog.some(f => f.url.includes('/api/schedule/approve')),
                schedule_data: save ? save.body.schedule_data : null,
                removed_subjects: save ? save.body.removed_subjects : null,
                unsaved_after: !!g(env, 'hasUnsavedChanges')(),
            };
            out.last_slice = { on_remove: onRemove, on_save: onSave };
        }
        // Explicit Publish of a saved removal Draft (reported by draft_sessions).
        {
            const env = setup(() => [A_MON, A_THU]);       // B201 still Published on the server
            env.routes.unshift(['/api/schedule/approve', { success: true, published_version: 5 }]);
            env.routes.unshift(['/api/schedule/draft_sessions',
                                { success: true, sessions: [], removed_subjects: ['B201'] }]);
            env.sandbox.__set('_currentSubjectCode', 'B201');
            env.getEl('sel_subj').value = 'B201';
            env.fetchLog.length = 0;
            await safely(() => g(env, '_runManualApprove')());
            const ap = env.fetchLog.find(f => f.url.includes('/api/schedule/approve'));
            out.publish_removal = ap ? { schedule_data: ap.body.schedule_data,
                                         removed: ap.body.removed_subjects,
                                         section: ap.body.context.sectionId } : null;
        }
        return out;
    },
    async token_rules() {
        const env = setup(() => []);
        const cur = g(env, '_existingLoadTokenIsCurrent');
        env.sandbox._existingSessionsSeq = 3;
        env.getEl('sel_subj').value = 'A101';
        return {
            current:       cur({ subject: 'A101', section: '9', seq: 3 }),
            newer_request: cur({ subject: 'A101', section: '9', seq: 2 }),
            other_subject: cur({ subject: 'B201', section: '9', seq: 3 }),
            other_section: cur({ subject: 'A101', section: '10', seq: 3 }),
            no_token:      cur(undefined),
        };
    },
};

(async () => {
    const name = process.argv[2];
    const names = name ? [name] : Object.keys(SCENARIOS);
    const out = {};
    for (const n of names) out[n] = await SCENARIOS[n]();
    process.stdout.write(JSON.stringify(out));
})().catch(e => { process.stdout.write(JSON.stringify({ harness_error: e.stack })); });
