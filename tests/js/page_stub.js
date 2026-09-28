// Loads the REAL static/js/ACAD HEAD/scheduleGeneration.acad.js into a minimal
// stub DOM (no jsdom dependency) and returns handles for driving it: element
// lookup, captured fetch calls, a settable fetch responder and table helpers.
// Shared by the harnesses in this folder.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function loadPage() {
    const page = { nextResponse: null };
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
    async function fetch(url, opts) {
        const body = opts && opts.body ? JSON.parse(opts.body) : null;
        calls.push({ url: String(url), body });
        const payload = page.nextResponse ? page.nextResponse(String(url), body) : { success: true };
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

    Object.assign(page, { getEl, calls, sandbox, docListeners, tbody, rowHtml, lockState, keyOf, setRow, toggleLock });
    return page;
}

module.exports = { loadPage };
