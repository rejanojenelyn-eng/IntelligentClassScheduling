// HC16 group model (P5, reworked for P7) — Manual Editor harness. Loads the REAL
// static/js/ACAD HEAD/manualEditor.acad2.js, manualEditor.mergeGroups.js and the REAL inline
// <script> blocks of templates/academic/manualScheduleEditor.html into ONE vm context (the
// browser's shared global scope), with a small id-addressable stub DOM and a scripted fetch().
// Each scenario drives the real functions; output is one JSON object on stdout.
'use strict';
const fs = require('fs'), path = require('path'), vm = require('vm');
const ROOT = path.join(__dirname, '..', '..');

function makeEnv(model, schedulerMode) {
    const log = [], fetchLog = [], modals = [], confirms = [];
    const els = {};
    function makeEl(id) {
        const cls = new Set();
        const el = {
            id, value: '', textContent: '', innerHTML: '', disabled: false, hidden: false, checked: false,
            style: {}, dataset: {}, options: [], selectedIndex: 0, children: [],
            offsetWidth: 0, offsetHeight: 0, scrollTop: 0, _cls: cls,
            classList: { add: (...c) => c.forEach(x => cls.add(x)), remove: (...c) => c.forEach(x => cls.delete(x)),
                         toggle: (c, on) => { const v = on === undefined ? !cls.has(c) : !!on; v ? cls.add(c) : cls.delete(c); return v; },
                         contains: c => cls.has(c) },
            addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true, click: () => {},
            appendChild: c => c, insertBefore: c => c, removeChild: () => {},
            remove: () => { delete els[id]; },
            insertAdjacentHTML: () => {}, focus: () => {}, blur: () => {}, scrollIntoView: () => {},
            setAttribute: () => {}, getAttribute: () => null, removeAttribute: () => {}, hasAttribute: () => false,
            getBoundingClientRect: () => ({ top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 }),
            closest: () => null, contains: () => false, matches: () => false,
            querySelector: () => null, querySelectorAll: () => [], cloneNode: () => makeEl(id + '_c'),
        };
        return el;
    }
    const getEl = id => (els[id] = els[id] || makeEl(id));
    const document = {
        getElementById: getEl,
        querySelector: () => null,
        querySelectorAll: sel => (sel === '.ts-row' ? Object.values(els).filter(e => e._isRow)
            .sort((a, b) => a._seq - b._seq) : []),
        createElement: t => makeEl('new:' + t + Math.random()), createTextNode: () => makeEl('text'),
        addEventListener: () => {}, removeEventListener: () => {},
        body: makeEl('body'), documentElement: makeEl('html'), head: null, readyState: 'complete',
    };
    const routes = [];
    async function fetch(url, opts = {}) {
        url = String(url);
        const body = opts.body ? JSON.parse(opts.body) : null;
        fetchLog.push({ url, method: opts.method || 'GET', body });
        const r = routes.find(([frag]) => url.includes(frag));
        const payload = r ? (typeof r[1] === 'function' ? await r[1](url, body) : r[1]) : [];
        if (payload === 'REJECT') throw new TypeError('Failed to fetch');
        return { ok: true, status: 200, json: async () => payload };
    }
    const storage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
    const sandbox = {
        document, fetch, localStorage: storage, sessionStorage: storage,
        console: { log: (...a) => log.push(a.join(' ')), warn: () => {}, error: () => {}, info: () => {}, debug: () => {} },
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
        faculty: JSON.stringify([{ id: 'F1', name: 'Dela Cruz, Juan' }, { id: 'F2', name: 'Santos, Ana' },
                                 { id: 'F9', name: 'Reyes, Ana' }]),
        weekendEnabled: 'true', mergeEnabled: 'true', mergeScope: 'nstp_only', mergeModel: model,
        schedulerMode: schedulerMode || 'official',
    });
    const js = n => fs.readFileSync(path.join(ROOT, 'static', 'js', 'ACAD HEAD', n), 'utf8');
    run('acad2.js', js('manualEditor.acad2.js'));
    run('mergeGroups.js', js('manualEditor.mergeGroups.js'));
    const html = fs.readFileSync(path.join(ROOT, 'templates', 'academic', 'manualScheduleEditor.html'), 'utf8');
    const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
    let m, i = 0;
    while ((m = re.exec(html))) {
        run(`inline#${i++}`, m[1].replace(/\{\{\s*scheduler_mode\s*\}\}/g, schedulerMode || 'official')
                                 .replace(/\{\{[\s\S]*?\}\}/g, '').replace(/\{%[\s\S]*?%\}/g, ''));
    }
    sandbox.__get = new vm.Script('(n) => eval(n)').runInContext(sandbox);
    sandbox.__set = new vm.Script('(n, v) => { globalThis.__v = v; eval(n + " = globalThis.__v"); }').runInContext(sandbox);
    sandbox.showValidationModal = async (t, msg) => { modals.push(`${t}: ${msg || ''}`); };
    sandbox.showConflictModal = async (msg) => { modals.push('CONFLICT ' + msg); };
    sandbox.showConfirmModal = async (msg, title) => { confirms.push(`${title || ''}: ${msg}`); return true; };
    return { sandbox, els, getEl, routes, fetchLog, log, errors, modals, confirms };
}

const g = (env, n) => env.sandbox.__get(n);
const T = ['07:30 AM','08:00 AM','08:30 AM','09:00 AM','09:30 AM','10:00 AM','10:30 AM','11:00 AM','11:30 AM',
           '12:00 PM','12:30 PM','01:00 PM','01:30 PM','02:00 PM','02:30 PM','03:00 PM'];

// Slice rows built by id, the way the real addNewTimeSlot names its fields.
let rowSeq = 0;
function addRow(env, { day = '', start = '', end = '', room = '', existingJson = '' } = {}) {
    const id = ++rowSeq, E = env.getEl, row = E(`ts-row-${id}`);
    const sel = E(`ts-day-${id}`);
    sel.value = day; sel.options = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        .map(d => ({ value: d }));
    E(`tsd-txt-${id}`).value = day; E(`tsst-hid-${id}`).value = start; E(`tset-hid-${id}`).value = end;
    E(`tsst-txt-${id}`).value = start; E(`tset-txt-${id}`).value = end;
    E(`tsr-hid-${id}`).value = room; E(`tsr-txt-${id}`).value = room;
    const map = { '.ts-day-sel': `ts-day-${id}`, '.ts-start-hidden': `tsst-hid-${id}`,
                  '.ts-end-hidden': `tset-hid-${id}`, '.ts-room-hidden': `tsr-hid-${id}`,
                  '.ts-room-field .ts-ss-input': `tsr-txt-${id}`, '.ts-row-num': `num-${id}` };
    Object.assign(row, { _isRow: true, _seq: id, dataset: existingJson ? { existingJson } : {} });
    row.querySelector = s => (map[s] ? E(map[s]) : null);
    return id;
}

function setup(model = 'groups', schedulerMode) {
    rowSeq = 0;
    const env = makeEnv(model, schedulerMode);
    const E = env.getEl;
    Object.entries({ sel_ay: 'AY1', sel_sem: 'A', sel_prog: 'BSIT', sel_year: '1', sel_section: '9',
                     sel_room: '5', sel_faculty: 'F1', fac_display_name: 'Dela Cruz, Juan', room_display_name: 'R5',
                     sel_subj: 'NSTP 001' }).forEach(([k, v]) => { E(k).value = v; });
    E('bc-sect-text').textContent = 'BSIT 1-A';
    env.sandbox.__set('_currentSubjectCode', 'NSTP 001');
    const subj = E('sel_subj');
    subj.options = [{ value: 'NSTP 001', text: 'NSTP 001 Civic Welfare', dataset: {} }];
    env.routes.push(['/api/hc_config', {}], ['/api/manual/section_schedule', []],
                    ['/api/manual/faculty_schedule', []], ['/api/get_room_schedule/', []]);
    return env;
}

const MEETING = { event_key: '1:11', mergegroupmeetingid: 11, class_type: 'Lecture', day: 'Sunday',
                  start: '09:00', end: '12:00', start_label: '09:00 AM', end_label: '12:00 PM',
                  roomid: 5, roomname: 'R5', sections: ['BSCS 1-A'], text: 'Lecture Sunday 9:00 AM–12:00 PM in R5' };

function subjectCtx(over = {}) {
    return Object.assign({
        subject_code: 'NSTP 001', mergegroupid: 1, group_name: 'NSTP Sunday Block', subject_name: 'Civic Welfare',
        faculty_mode: 'SAME_FACULTY', designated_faculty: null, origin: 'editor',
        sections: ['BSIT 1-A', 'BSCS 1-A'], section_count: 2, status: 'merged', meetings: [MEETING],
    }, over);
}

function rowMeta(key, sync, over = {}) {
    return Object.assign({ key, subject_code: 'NSTP 001', mergegroupid: 1, group_name: 'NSTP Sunday Block',
        sync, event_key: sync === 'in_sync' ? '1:11' : null, merged_with: sync === 'in_sync' ? ['BSCS 1-A'] : [],
        actual: sync === 'empty' ? null : 'Sunday 9:00 AM–12:00 PM in R5',
        faculty_issue: null, faculty_advisory: null }, over);
}

function serve(env, current, rows = {}) {
    env.routes.unshift(['/api/manual/merge_context', (url, body) =>
        ({ success: true, model: 'groups', section_id: 9, subjects: { 'NSTP 001': current }, rows, current,
           _posted: body })]);
}

const SCENARIOS = {
    async legacy_mode() {
        const env = setup('legacy');
        const out = {
            model: g(env, 'MERGE_MODEL'), nstp: g(env, '_getMergeMode')('NSTP 001'),
            active: g(env, '_mgActive')(), refreshed: await g(env, '_mgRefresh')(),
        };
        addRow(env, { day: 'Monday', start: T[0], end: T[6], room: '7' });
        out.add_ok = g(env, '_mgControlled')() === false;
        out.fetched_merge_context = env.fetchLog.some(f => f.url.includes('merge_context'));
        out.legacy_viol_type = g(env, '_violParse')({ rule: 'HC16', type: 'Conflict: Merged-Class Validity', detail: 'x.' }).type;
        out.legacy_viol_html = g(env, '_violMergeHtml')({ rule: 'HC16', detail: 'x.' });
        out.merge_candidate = g(env, '_mgMergeCandidate')({ subjectcode: 'NSTP 001', employee_number: 'F1', section_id: 10 });
        out.errors = env.errors;
        return out;
    },
    async groups_disable_legacy() {
        const env = setup('groups');
        return { model: g(env, 'MERGE_MODEL'), nstp: g(env, '_getMergeMode')('NSTP 001'),
                 eligible: g(env, '_isSubjectMergeEligible')('NSTP 001'), active: g(env, '_mgActive')(),
                 errors: env.errors };
    },
    async local_mode_is_untouched() {
        const env = setup('groups', 'local');
        return { active: g(env, '_mgActive')(), refreshed: await g(env, '_mgRefresh')(),
                 fetched: env.fetchLog.some(f => f.url.includes('merge_context')) };
    },
    async render_states() {
        const env = setup('groups');
        const r1 = addRow(env, { day: 'Sunday', start: '09:00 AM', end: '12:00 PM', room: '5', existingJson: '{"_localTempId":"t1"}' });
        const r2 = addRow(env, { day: 'Monday', start: T[0], end: T[6], room: '7', existingJson: '{"_localTempId":"t2"}' });
        const r3 = addRow(env);
        serve(env, subjectCtx(),
              { [r1]: rowMeta(String(r1), 'in_sync'), [r2]: rowMeta(String(r2), 'separate'),
                [r3]: rowMeta(String(r3), 'empty') });
        await g(env, '_mgRefresh')();
        const E = env.getEl, vm = g(env, '_mgViewModel')(g(env, '_mgCtx'));
        const posted = env.fetchLog.find(f => f.url.includes('merge_context')).body;
        return {
            posted_rows: posted.rows.map(r => [r.key, r.day, r.start_time, r.room_id, r.faculty_id]),
            posted_keys: Object.keys(posted.rows[0]).sort(),
            posted_section: posted.section_id, posted_subject: posted.subject_code,
            badge_text: vm.badge, tooltip: vm.tooltip,
            badge_html: E('mg-badge').innerHTML, badge_hidden: E('mg-badge').hidden,
            r1: { locked: E(`ts-row-${r1}`).dataset.mergeControlled || null, ev: E(`ts-row-${r1}`).dataset.mergeEvent,
                  disabled: ['tsd-txt-', 'tsst-txt-', 'tset-txt-', 'tsr-txt-', 'ts-day-'].map(p => E(p + r1).disabled),
                  note: E(`ts-mg-${r1}`).innerHTML, cls: E(`ts-row-${r1}`).classList.contains('ts-merge-locked') },
            r2: { locked: E(`ts-row-${r2}`).dataset.mergeControlled || null, ev: E(`ts-row-${r2}`).dataset.mergeEvent || '',
                  sync: E(`ts-row-${r2}`).dataset.mergeSync, note: E(`ts-mg-${r2}`).innerHTML },
            r3_present: !!env.els[`ts-row-${r3}`],
            add_hidden: E('btnAddTimeSlot').style.display || '', banner: E('mg-banner').innerHTML,
            day_value_kept: E(`ts-day-${r2}`).value,
            errors: env.errors,
        };
    },
    async not_merged_group() {
        const env = setup('groups');
        env.getEl('bc-sect-text').textContent = 'BSIT 1-A';
        const r1 = addRow(env, { day: 'Monday', start: T[0], end: T[6], room: '7', existingJson: '{"_localTempId":"t1"}' });
        serve(env, subjectCtx({ status: 'not_merged', meetings: [] }), { [r1]: rowMeta(String(r1), 'separate') });
        await g(env, '_mgRefresh')();
        const E = env.getEl;
        return { badge: g(env, '_mgViewModel')(g(env, '_mgCtx')).badge, banner: E('mg-banner').innerHTML,
                 note: E(`ts-mg-${r1}`).innerHTML, locked: E(`ts-row-${r1}`).dataset.mergeControlled || null,
                 disabled: E(`tsr-txt-${r1}`).disabled };
    },
    async guards() {
        const env = setup('groups');
        const r1 = addRow(env, { day: 'Sunday', start: '09:00 AM', end: '12:00 PM', room: '5', existingJson: '{"_localTempId":"t1"}' });
        serve(env, subjectCtx(), { [r1]: rowMeta(String(r1), 'in_sync') });
        await g(env, '_mgRefresh')();
        return {
            block_add: await g(env, '_mgBlockFreeSlot')('added'), fac_guard: await g(env, '_mgFacultyGuard')(),
            row_locked: g(env, '_mgRowLocked')(r1), controlled: g(env, '_mgControlled')(),
            fac_locked: g(env, '_mgFacultyLocked')(), modals: env.modals.slice(),
            reset_fn: g(env, 'typeof _mgResetToGroupSlot'),
        };
    },
    async designee_never_locks_faculty() {
        const env = setup('groups');
        const r1 = addRow(env, { day: 'Sunday', start: '09:00 AM', end: '12:00 PM', room: '5', existingJson: '{"_localTempId":"t1"}' });
        serve(env, subjectCtx({ designated_faculty: { id: 'F9', name: 'Reyes, Ana' } }),
              { [r1]: rowMeta(String(r1), 'in_sync', { faculty_issue: "Merged class 'NSTP Sunday Block' requires its designated faculty Reyes, Ana." }) });
        await g(env, '_mgRefresh')();
        const E = env.getEl;
        g(env, 'toggleDSSMenu')('fac', { stopPropagation() {} });
        await new Promise(r => setImmediate(r));
        return { note: E(`ts-mg-${r1}`).innerHTML, wrapper_locked: E('fac_wrapper').classList.contains('mg-fac-locked'),
                 menu_opened: E('fac_menu').style.display, sel_faculty: E('sel_faculty').value,
                 modals: env.modals.slice() };
    },
    async same_faculty_tba_advisory() {
        const env = setup('groups');
        env.getEl('sel_faculty').value = 'TBA';
        const r1 = addRow(env, { day: 'Sunday', start: '09:00 AM', end: '12:00 PM', room: '5', existingJson: '{"_localTempId":"t1"}' });
        serve(env, subjectCtx(),
              { [r1]: rowMeta(String(r1), 'in_sync', { faculty_advisory: 'A merged class needs the same faculty for every section; TBA keeps it incomplete.' }) });
        await g(env, '_mgRefresh')();
        g(env, 'toggleDSSMenu')('fac', { stopPropagation() {} });
        return { note: env.getEl(`ts-mg-${r1}`).innerHTML, menu_opened: env.getEl('fac_menu').style.display,
                 locked: g(env, '_mgFacultyLocked')(), modals: env.modals.slice() };
    },
    async occupancy_same_event() {
        const env = setup('groups');
        const cache = [
            { daydesc: 'Sunday', startIdx: 3, endIdx: 9, subjectcode: 'NSTP 001', employee_number: 'F2', merge_event: '1:11', section_id: 10, versionid: 1 },
            { daydesc: 'Sunday', startIdx: 3, endIdx: 9, subjectcode: 'NSTP 001', employee_number: 'F2', merge_event: '1:11', section_id: 9, versionid: 2 },
            { daydesc: 'Sunday', startIdx: 4, endIdx: 8, subjectcode: 'NSTP 001', employee_number: 'F2', merge_event: null, section_id: 11, versionid: 3 },
            { daydesc: 'Sunday', startIdx: 0, endIdx: 2, subjectcode: 'MATH 1', employee_number: 'F1', merge_event: null, section_id: 12, versionid: 4 },
        ];
        env.sandbox._roomDbCache['5|AY1|A'] = cache;
        const occ = g(env, '_getOccupiedRanges');
        const withKey = occ('5', 'Sunday', 'AY1', 'A', 'x', null, '1:11');
        const noKey = occ('5', 'Sunday', 'AY1', 'A', 'x', null, '');
        const legacy = setup('legacy');
        legacy.sandbox._roomDbCache['5|AY1|A'] = cache;
        const legacyRanges = g(legacy, '_getOccupiedRanges')('5', 'Sunday', 'AY1', 'A', 'x', null, '1:11');
        return { with_key: withKey.length, no_key: noKey.length, legacy: legacyRanges.length,
                 merge_flags: noKey.map(r => !!r.merge),
                 same: g(env, '_mgSameEvent')({ merge_event: '1:11', section_id: 10 }, '1:11', '9'),
                 own_section: g(env, '_mgSameEvent')({ merge_event: '1:11', section_id: 9 }, '1:11', '9'),
                 other_key: g(env, '_mgSameEvent')({ merge_event: '1:12', section_id: 10 }, '1:11', '9'),
                 outsider: g(env, '_mgSameEvent')({ merge_event: null, section_id: 11 }, '1:11', '9') };
    },
    // P7: an occupied slot of another section with the same subject and faculty (or TBA)
    // stays available — at exactly that slot only.
    async occupancy_merge_candidate() {
        const env = setup('groups');
        const row = over => Object.assign({ daydesc: 'Sunday', startIdx: 3, endIdx: 9, subjectcode: 'NSTP 001',
                                            employee_number: 'F1', merge_event: null, section_id: 10, versionid: 1 }, over);
        const ranges = cache => { env.sandbox._roomDbCache['5|AY1|A'] = cache;
                                  return g(env, '_getOccupiedRanges')('5', 'Sunday', 'AY1', 'A', 'x', null, ''); };
        const blocks = g(env, '_mgRangeBlocks');
        const same = ranges([row()]);
        return {
            same_faculty: same.map(r => !!r.merge), exact_blocks: blocks(same[0], 3, 9),
            partial_blocks: blocks(same[0], 4, 9), longer_blocks: blocks(same[0], 3, 10),
            other_faculty: ranges([row({ employee_number: 'F2' })]).map(r => !!r.merge),
            other_tba: ranges([row({ employee_number: null })]).map(r => !!r.merge),
            other_subject: ranges([row({ subjectcode: 'MATH 1' })]).map(r => !!r.merge),
            own_section: ranges([row({ section_id: 9 })]).map(r => !!r.merge),
        };
    },
    async place_same_event_vs_outsider() {
        const out = {};
        for (const [name, rows] of Object.entries({
            same_event: [{ daydesc: 'Sunday', starttimeid: 4, endtimeid: 10, subjectcode: 'NSTP 001', subjectname: 'NSTP',
                           employee_number: 'F2', merge_event: '1:11', section_id: 10 }],
            outsider_same_subject: [{ daydesc: 'Sunday', starttimeid: 4, endtimeid: 10, subjectcode: 'NSTP 001',
                                      subjectname: 'NSTP', employee_number: 'F2', merge_event: null, section_id: 11 }],
        })) {
            const env = setup('groups');
            const E = env.getEl;
            env.routes.unshift(['/api/get_room_schedule/', rows]);
            Object.entries({ sel_day: 'Sunday', sel_room: '5' }).forEach(([k, v]) => { E(k).value = v; });
            E('sel_start_time').value = '09:00 AM'; E('sel_end_time').value = '12:00 PM';
            env.sandbox._mgPlacingEventKey = '1:11';
            env.sandbox.__set('pendingManualSchedule', []);
            try { await g(env, 'confirmAndPlace')(); } catch (e) { out[name + '_error'] = e.message; }
            const p = g(env, 'pendingManualSchedule');
            out[name] = { placed: p.length, merge_event: p[0] && p[0].merge_event, modals: env.modals.slice(),
                          merge_confirm: env.confirms.some(c => c.includes('Merge Class Detected')) };
        }
        return out;
    },
    // P7: placing a slice exactly on another section's same-subject, same-faculty class is a
    // merge (confirmed later by the Save Draft / Publish notice), never a conflict here.
    async place_merge_candidate() {
        const out = {};
        const base = { daydesc: 'Sunday', starttimeid: 4, endtimeid: 10, subjectcode: 'NSTP 001', subjectname: 'NSTP',
                       employee_number: 'F1', merge_event: null, section_id: 10, roomid: 5, sectionname: 'BSCS 1-A' };
        for (const [name, over, start, end] of [
            ['exact_same_faculty', {}, '09:00 AM', '12:00 PM'],
            ['exact_other_tba', { employee_number: null }, '09:00 AM', '12:00 PM'],
            ['exact_other_faculty', { employee_number: 'F2' }, '09:00 AM', '12:00 PM'],
            ['partial_same_faculty', {}, '09:30 AM', '12:00 PM'],
            ['exact_other_subject', { subjectcode: 'MATH 1', subjectname: 'Math' }, '09:00 AM', '12:00 PM'],
        ]) {
            const env = setup('groups');
            const E = env.getEl;
            const occupant = Object.assign({}, base, over);
            env.routes.unshift(['/api/get_room_schedule/', [occupant]]);
            env.routes.unshift(['/api/manual/faculty_schedule', occupant.employee_number === 'F1' ? [occupant] : []]);
            Object.entries({ sel_day: 'Sunday', sel_room: '5' }).forEach(([k, v]) => { E(k).value = v; });
            E('sel_start_time').value = start; E('sel_end_time').value = end;
            env.sandbox.__set('pendingManualSchedule', []);
            try { await g(env, 'confirmAndPlace')(); } catch (e) { out[name + '_error'] = e.message; }
            out[name] = { placed: g(env, 'pendingManualSchedule').length, modals: env.modals.slice() };
        }
        return out;
    },
    async faculty_assignment_conflict() {
        const out = {};
        for (const [name, ev] of Object.entries({ same_event: '1:11', outsider: null })) {
            const env = setup('groups');
            const r1 = addRow(env, { day: 'Sunday', start: '09:00 AM', end: '12:00 PM', room: '5' });
            env.getEl(`ts-row-${r1}`).dataset.mergeEvent = '1:11';
            env.routes.unshift(['/api/manual/faculty_schedule', [{ subjectcode: 'NSTP 001', subjectname: 'NSTP',
                daydesc: 'Sunday', starttimeid: 4, endtimeid: 10, section_id: 10, sectionname: 'BSCS 1-A', merge_event: ev }]]);
            out[name] = await g(env, '_findFacultySliceConflict')('F2', 'Santos, Ana');
        }
        // P7: the same subject taught by this faculty in another section at exactly this
        // slice's day/time/room is a merge, not a conflict; anywhere else it still is.
        for (const [name, room, st] of [['merge_exact', 5, 4], ['merge_other_room', 7, 4], ['merge_other_time', 5, 5]]) {
            const env = setup('groups');
            addRow(env, { day: 'Sunday', start: '09:00 AM', end: '12:00 PM', room: '5' });
            env.routes.unshift(['/api/manual/faculty_schedule', [{ subjectcode: 'NSTP 001', subjectname: 'NSTP',
                daydesc: 'Sunday', starttimeid: st, endtimeid: 10, section_id: 10, sectionname: 'BSCS 1-A',
                roomid: room, employee_number: 'F1', merge_event: null }]]);
            out[name] = await g(env, '_findFacultySliceConflict')('F1', 'Dela Cruz, Juan');
        }
        return out;
    },
    async save_draft_merge_notice() {
        const env = setup('groups');
        const E = env.getEl;
        let calls = 0;
        env.routes.unshift(['/api/schedule/validate', { success: true, violations: [], has_violations: false }]);
        env.routes.unshift(['/api/schedule/save-draft', (url, body) => {
            calls++;
            return body.confirm_merges
                ? { success: true, draft_version: 2, merged: ['NSTP 001: BSIT 1-A merges with BSCS 1-A'] }
                : { success: false, code: 'MERGE_CONFIRMATION_REQUIRED', error: 'merge',
                    merges: [{ subject_code: 'NSTP 001', section_label: 'BSIT 1-A', other_section_label: 'BSCS 1-A' }],
                    notice: ['NSTP 001: BSIT 1-A merges with BSCS 1-A'] };
        }]);
        const draft = () => [{ temp_id: 'd1', ay: 'AY1', sem: 'A', subject_code: 'NSTP 001',
            section_id: '9', course: 'BSIT', year_level: '1', day: 'Sunday', start_time: '09:00 AM', end_time: '12:00 PM',
            room_id: '5', faculty_id: 'F1', status: 'Draft' }];
        env.sandbox.__set('pendingManualSchedule', draft());
        const shown = [];
        const answers = { value: true };
        env.sandbox.__set('_confirmMergeNotice', async d => { shown.push(d.notice); return answers.value; });
        let ok, declined;
        try { ok = await g(env, '_triggerSaveDraft')(); } catch (e) { ok = 'error: ' + e.message; }
        const bodies = env.fetchLog.filter(f => f.url.includes('save-draft')).map(f => f.body.confirm_merges);
        answers.value = false;
        env.fetchLog.length = 0;
        env.sandbox.__set('pendingManualSchedule', draft());
        try { declined = await g(env, '_triggerSaveDraft')(); } catch (e) { declined = 'error: ' + e.message; }
        const declinedBodies = env.fetchLog.filter(f => f.url.includes('save-draft')).map(f => f.body.confirm_merges);
        return { ok, bodies, shown, declined, declined_bodies: declinedBodies, errors: env.errors };
    },
    async conflict_card() {
        const env = setup('groups');
        const v = { rule: 'HC16', code: 'HC16_DIVERGED', merge_state: 'invalid', type: 'Conflict: Merged-Class Validity',
                    group: 'NSTP Sunday Block', section_name: 'BSIT 1-A', expected: ['Lecture Sunday 9:00 AM–12:00 PM in R5'],
                    actual: ['Monday 7:30 AM–10:30 AM in R7'], detail: 'BSIT 1-A NSTP 001 is part of merged class.' };
        const f = { rule: 'HC16', code: 'HC16_FACULTY', merge_state: 'invalid', group: 'NSTP Sunday Block',
                    section_name: 'BSIT 1-A', expected: ['F2'], actual: ['Sunday 9:00 AM–12:00 PM in R5'],
                    faculty: { expected: 'F2', actual: 'F1', other_section: 'BSCS 1-A' }, detail: 'x.' };
        return { type: g(env, '_violParse')(v).type, html: g(env, '_violMergeHtml')(v),
                 fac_html: g(env, '_violMergeHtml')(f), other_rule: g(env, '_violParse')({ rule: 'HC11', type: 'Conflict: Room', detail: 'x.' }).type };
    },
    async stale_and_error() {
        const env = setup('groups');
        let n = 0;
        env.routes.unshift(['/api/manual/merge_context', (url, body) => {
            n++;
            return { success: true, model: 'groups', subjects: {}, rows: {},
                     current: subjectCtx({ subject_code: body.subject_code, group_name: 'G-' + body.subject_code }) };
        }]);
        const p1 = g(env, '_mgRefresh')();
        env.sandbox.__set('_currentSubjectCode', 'NSTP 002');
        env.getEl('sel_subj').value = 'NSTP 002';
        const p2 = g(env, '_mgRefresh')();
        await Promise.all([p1, p2]);
        const kept = g(env, '_mgCtx')._code;
        const env2 = setup('groups');
        const r1 = addRow(env2, { day: 'Sunday', start: '09:00 AM', end: '12:00 PM', room: '5' });
        env2.routes.unshift(['/api/manual/merge_context', { success: false, error: 'Merge Group context unavailable: boom' }]);
        await g(env2, '_mgRefresh')();
        return { calls: n, kept, error_banner: env2.getEl('mg-banner').innerHTML,
                 error_locked: !!env2.getEl(`ts-row-${r1}`).dataset.mergeControlled,
                 error_controlled: g(env2, '_mgControlled')() };
    },
    async fail_closed_and_retry() {
        const env = setup('groups');
        const E = env.getEl;
        const r1 = addRow(env, { day: 'Monday', start: T[0], end: T[6], room: '7', existingJson: '{"_localTempId":"t1"}' });
        let mode = 'reject';
        env.routes.unshift(['/api/manual/merge_context', () => mode === 'reject' ? 'REJECT'
            : mode === 'http500' ? { success: false, error: 'Merge Group context unavailable: boom' }
            : { success: true, model: 'groups', subjects: {}, rows: {}, current: null }]);
        env.routes.unshift(['/api/schedule/save-draft', { success: true }], ['/api/schedule/approve', { success: true }]);
        await g(env, '_mgRefresh')();
        const broken = {
            broken: g(env, '_mgContextBroken')(), controlled: g(env, '_mgControlled')(),
            row_locked: E(`ts-row-${r1}`).dataset.mergeControlled, field_disabled: E(`tsr-txt-${r1}`).disabled,
            add_hidden: E('btnAddTimeSlot').style.display, fac_locked: E('fac_wrapper').classList.contains('mg-fac-locked'),
            banner: E('mg-banner').innerHTML, note: E(`ts-mg-${r1}`).innerHTML,
        };
        env.modals.length = 0;
        broken.save_draft = await g(env, '_triggerSaveDraft')();
        broken.publish = await g(env, '_runManualApprove')();
        await g(env, 'confirmAllSlots')();
        await g(env, 'saveIndividualSlice')(r1);
        g(env, 'addNewTimeSlot')();
        g(env, 'tsSel')('tsr', r1, '5');
        g(env, 'toggleDSSMenu')('fac', { stopPropagation() {} });
        await new Promise(r => setImmediate(r));
        broken.modals = env.modals.slice();
        broken.room_after = E(`tsr-hid-${r1}`).value;
        broken.writes = env.fetchLog.filter(f => /save-draft|approve/.test(f.url)).length;
        mode = 'http500';
        await g(env, '_mgRetry')();
        const still = { broken: g(env, '_mgContextBroken')(), banner: E('mg-banner').innerHTML };
        mode = 'ok';
        await g(env, '_mgRetry')();
        const recovered = {
            broken: g(env, '_mgContextBroken')(), controlled: g(env, '_mgControlled')(),
            row_locked: E(`ts-row-${r1}`).dataset.mergeControlled || null, field_disabled: E(`tsr-txt-${r1}`).disabled,
            add_hidden: E('btnAddTimeSlot').style.display, fac_locked: E('fac_wrapper').classList.contains('mg-fac-locked'),
            banner: E('mg-banner').innerHTML, guard: await g(env, '_mgGuardSave')('publish'),
        };
        return { broken, still, recovered, errors: env.errors };
    },
    async loading_blocks_save() {
        const env = setup('groups');
        let release;
        env.routes.unshift(['/api/manual/merge_context', () => new Promise(r => {
            release = () => r({ success: true, model: 'groups', subjects: {}, rows: {}, current: null }); })]);
        const p = g(env, '_mgRefresh')();
        await new Promise(r => setImmediate(r));
        const during = await g(env, '_mgGuardSave')('publish');
        const msg = env.modals.slice();
        release();
        await p;
        return { during, msg, after: await g(env, '_mgGuardSave')('publish') };
    },
    async legacy_never_blocks() {
        const env = setup('legacy');
        env.routes.unshift(['/api/manual/merge_context', 'REJECT']);
        await g(env, '_mgRefresh')();
        return { broken: g(env, '_mgContextBroken')(), guard: await g(env, '_mgGuardSave')('publish'),
                 controlled: g(env, '_mgControlled')(), fac: g(env, '_mgFacultyLocked')(),
                 fetched: env.fetchLog.some(f => f.url.includes('merge_context')) };
    },
};

(async () => {
    const name = process.argv[2];
    const names = name ? [name] : Object.keys(SCENARIOS);
    const out = {};
    for (const n of names) {
        try { out[n] = await SCENARIOS[n](); } catch (e) { out[n] = { scenario_error: e.stack }; }
    }
    process.stdout.write(JSON.stringify(out));
})().catch(e => { process.stdout.write(JSON.stringify({ harness_error: e.stack })); });
