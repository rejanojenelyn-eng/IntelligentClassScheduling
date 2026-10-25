// Loads the REAL static/js/profile.dropdown.js into a stub DOM and checks two Profile
// Settings behaviours:
//   - Request Correction: Submit Request is enabled only while some proposed value really
//     differs from what is on file (changing a value back disables it again).
//   - Change profile photo: Choose from Files validates type and size; Take a Photo uses the
//     camera module; the picked photo is previewed and "Keep current photo" restores the saved one.
//   - My Correction Requests: Cancel Request only on Pending requests; Keep Request changes
//     nothing; Cancel Request posts the cancel and refreshes the list and the pending badges.
// Usage: node profile_settings_harness.js
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const SRC = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'profile.dropdown.js'), 'utf8');

function makeEl(id, props = {}) {
    const classes = new Set();
    return Object.assign({
        id, hidden: false, disabled: false, value: '', textContent: '', innerHTML: '', src: '', style: {},
        files: [], clicked: 0,
        classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), contains: c => classes.has(c),
                     add: c => classes.add(c), remove: c => classes.delete(c) },
        setAttribute() {}, getAttribute() { return null; }, focus() {}, addEventListener() {},
        click() { this.clicked++; }, closest() { return null; },
        appendChild(el) { el.parentElement = this; }, remove() {},
        querySelector() { return null; }, querySelectorAll() { return []; },
    }, props);
}

function load() {
    const els = {};
    const get = id => (els[id] = els[id] || makeEl(id));
    const page = { items: [], cropped: [], camera: null, fetches: [], requests: [], cancelReply: { success: true, pending: 0 },
                   dots: [makeEl('dot')] };
    const document = {
        getElementById: get,
        querySelector: () => null,
        querySelectorAll: sel => (sel === '#crmItems .crm-ok input' ? page.items.map(i => i.box)
                                  : sel === '.upd-menu .cr-dot' ? page.dots : []),
        addEventListener() {}, createElement: () => makeEl('x'),
        body: makeEl('body', { appendChild(el) { el.parentElement = this; } }),
    };
    const ctx = {
        console, Promise, Array, Object, JSON, Math, Set, Map, String, Number, RegExp,
        document, URL: { createObjectURL: () => 'blob:preview' },
        history: { state: null, pushState() {}, replaceState() {}, back() {} },
        FormData: class { append() {} },
        fetch: async (url, opts = {}) => {
            page.fetches.push([opts.method || 'GET', url]);
            if (url === '/api/user/correction-requests') return { status: 200, json: async () => ({ success: true, requests: page.requests }) };
            if (url.endsWith('/cancel')) return { status: page.cancelReply.success ? 200 : 409, json: async () => page.cancelReply };
            return { status: 200, json: async () => ({ success: true }) };
        },
        openPhotoCropper: async file => { page.cropped.push(file); return { blob: true, from: file.name }; },
        openPhotoCamera: async () => page.camera,
        setTimeout, clearTimeout, setInterval, clearInterval, requestAnimationFrame: fn => fn(),
    };
    ctx.window = Object.assign(ctx, { addEventListener() {} });
    vm.createContext(ctx);
    vm.runInContext(SRC, ctx);
    return { ctx, get, page };
}

// One correction item: an unticked/ticked checkbox and its proposed inputs.
function item(fields, unticked = true) {
    const inputs = fields.map(([field, value]) => ({ dataset: { field }, value, disabled: false }));
    const el = { querySelectorAll: () => inputs };
    return { box: { checked: !unticked, closest: () => el }, inputs };
}

(async () => {
    // ── Submit Request ────────────────────────────────────────────────────────
    let { ctx, get, page } = load();
    vm.runInContext(`_crmCurrent = { 'Employee ID': 'EMP1', 'Specialization': 'Biology',
                                     'First Name': 'Ana', 'Middle Name': 'B', 'Last Name': 'Cruz', 'Suffix': '' };`, ctx);
    const submit = get('crmSubmit');
    ctx._crmSync();
    assert.strictEqual(submit.disabled, true, 'disabled by default (nothing unticked)');

    const spec = item([['Specialization', '']]);           // unticked, nothing chosen yet
    page.items = [spec];
    ctx._crmSync();
    assert.strictEqual(submit.disabled, true, 'unticking alone is not a change');
    spec.inputs[0].value = 'Biology';
    ctx._crmSync();
    assert.strictEqual(submit.disabled, true, 'choosing the value already on file is not a change');
    spec.inputs[0].value = 'Chemistry';
    ctx._crmSync();
    assert.strictEqual(submit.disabled, false, 'a real change enables Submit Request');
    spec.inputs[0].value = 'Biology';
    ctx._crmSync();
    assert.strictEqual(submit.disabled, true, 'changing it back disables it again');

    const emp = item([['Employee ID', '  EMP1 ']]);
    page.items = [emp];
    ctx._crmSync();
    assert.strictEqual(submit.disabled, true, 'surrounding spaces are not a change');
    emp.inputs[0].value = '';
    ctx._crmSync();
    assert.strictEqual(submit.disabled, true, 'a required field left empty is not a change');
    emp.inputs[0].value = 'EMP2';
    ctx._crmSync();
    assert.strictEqual(submit.disabled, false);

    const name = item([['First Name', 'Ana'], ['Middle Name', ''], ['Last Name', 'Cruz']]);
    page.items = [name];
    ctx._crmSync();
    assert.strictEqual(submit.disabled, false, 'Middle Name may be corrected to blank');
    name.inputs[1].value = 'B';
    ctx._crmSync();
    assert.strictEqual(submit.disabled, true);

    page.items = [item([['Specialization', 'Chemistry']], false)];   // ticked "correct": ignored
    ctx._crmSync();
    assert.strictEqual(submit.disabled, true, 'a ticked item never counts');

    // ── Change profile photo ──────────────────────────────────────────────────
    ({ ctx, get, page } = load());
    vm.runInContext(`_profileData = { photo_url: '/static/uploads/profile_photos/saved.jpg' };`, ctx);
    const err = get('pfPhotoError'), img = get('profilePhotoImg'), notice = get('pfPhotoPending');
    notice.hidden = true;

    ctx.togglePhotoMenu(true);
    assert.strictEqual(get('pfPhotoMenu').hidden, false, 'the camera button opens the two options');
    ctx.choosePhotoFile();
    assert.strictEqual(get('pfPhotoMenu').hidden, true);
    assert.strictEqual(get('profilePhotoInput').clicked, 1, 'Choose from Files opens the file picker');

    const input = get('profilePhotoInput');
    input.files = [{ name: 'notes.pdf', type: 'application/pdf', size: 1000 }];
    await ctx.handlePhotoUpload(input);
    assert.ok(err.textContent.includes('JPG or PNG') && err.style.display === 'block');
    assert.strictEqual(page.cropped.length, 0);
    input.files = [{ name: 'big.jpg', type: 'image/jpeg', size: 3 * 1024 * 1024 }];
    await ctx.handlePhotoUpload(input);
    assert.ok(err.textContent.includes('larger than 2 MB'));
    assert.strictEqual(page.cropped.length, 0);

    input.files = [{ name: 'me.png', type: 'image/png', size: 50000 }];
    await ctx.handlePhotoUpload(input);
    assert.strictEqual(err.style.display, 'none', 'a good file clears the error');
    assert.strictEqual(page.cropped[0].name, 'me.png');
    assert.strictEqual(img.src, 'blob:preview', 'the picked photo is previewed');
    assert.strictEqual(notice.hidden, false);
    assert.strictEqual(get('pfSaveBtn').disabled, false, 'Save Changes is now available');

    ctx.discardPendingPhoto();
    assert.strictEqual(img.src, '/static/uploads/profile_photos/saved.jpg', 'Keep current photo restores the saved one');
    assert.strictEqual(notice.hidden, true);
    assert.strictEqual(get('pfSaveBtn').disabled, true);

    page.camera = null;                                       // camera cancelled / denied
    await ctx.takeProfilePhoto();
    assert.strictEqual(page.cropped.length, 1);
    page.camera = { name: 'camera.jpg', type: 'image/jpeg', size: 900000 };
    await ctx.takeProfilePhoto();
    assert.strictEqual(page.cropped[1].name, 'camera.jpg', 'Take a Photo goes through the same crop and preview');
    assert.strictEqual(notice.hidden, false);

    // ── Cancel a correction request ─────────────────────────────────────────────
    ({ ctx, get, page } = load());
    page.requests = [
        { id: 7, field: 'Specialization', status: 'Pending', current: 'A', proposed: 'B', submitted: 'x' },
        { id: 8, field: 'Employment Status', status: 'Approved', current: 'A', proposed: 'B', submitted: 'x', decided: 'y' },
        { id: 9, field: 'Designation', status: 'Cancelled', current: 'A', proposed: 'B', submitted: 'x', cancelled: 'z' },
    ];
    const list = get('crList'), dlg = get('crCancelDialog');
    list.hidden = true; dlg.hidden = true;
    await ctx.toggleMyCorrections();
    const html = list.innerHTML;
    assert.strictEqual((html.match(/Cancel Request/g) || []).length, 1, 'only the Pending request can be cancelled');
    assert.ok(html.includes('askCancelCorrection(7)') && !html.includes('askCancelCorrection(8)'));
    assert.ok(html.includes('cr-status cr-Cancelled') && html.includes('You cancelled this request.'));

    ctx.askCancelCorrection(7);
    assert.strictEqual(dlg.hidden, false, 'Cancel Request asks first');
    page.fetches.length = 0;
    ctx.closeCancelCorrection();                         // Keep Request
    assert.strictEqual(dlg.hidden, true);
    assert.deepStrictEqual(page.fetches, [], 'Keep Request changes nothing');

    ctx.askCancelCorrection(7);
    page.requests[0].status = 'Cancelled';
    await ctx.confirmCancelCorrection();
    assert.deepStrictEqual(page.fetches[0], ['POST', '/api/user/correction-requests/7/cancel']);
    assert.strictEqual(dlg.hidden, true);
    assert.strictEqual(page.dots[0].hidden, true, 'the pending dot goes once nothing is pending');
    assert.ok(page.fetches.some(([m, u]) => m === 'GET' && u === '/api/user/correction-requests'), 'list refreshed');

    // The server refuses (e.g. the Admin decided it meanwhile): explained, dialog stays open.
    ctx.askCancelCorrection(7);
    page.cancelReply = { success: false, error: 'This request was already approved and can no longer be cancelled.' };
    await ctx.confirmCancelCorrection();
    assert.strictEqual(dlg.hidden, false);
    assert.ok(get('crcError').textContent.includes('already approved'));

    console.log('all checks passed');
})().catch(e => { console.error(e); process.exit(1); });
