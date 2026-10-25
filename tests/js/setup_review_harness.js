// Drives the REAL inline script of the Account Setup Step 5 page (rendered by Flask and
// passed in as a file) against stub forms:
//   - Employment Type: Designee shows the Designation picker (optional, as in Add Faculty);
//     any other type hides it and shows the "will be removed" note.
//   - Save and Confirm: refused without the confirmation tick, or while an open editor
//     holds unsaved changes.
// Usage: node setup_review_harness.js <rendered-script.js>
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

function el(props = {}) {
    return { hidden: false, disabled: false, required: false, value: '', ...props };
}

// A <select> with options; `defaultSelected` is the saved value.
function select(values, saved) {
    const options = values.map(v => ({ value: v, defaultSelected: v === saved, selected: v === saved }));
    const s = {
        tagName: 'SELECT', options, disabled: false, hidden: false,
        get value() { return (options.find(o => o.selected) || {}).value || ''; },
        set value(v) { options.forEach(o => { o.selected = o.value === v; }); },
        reset() { options.forEach(o => { o.selected = o.defaultSelected; }); },
    };
    return s;
}

function makeTypeForm(savedType, savedDesignation) {
    const typeSel = select(['Designee', 'Part-time', 'Regular'], savedType);
    const desigSel = select(['', 'Campus Director', 'Campus Registrar'], savedDesignation);
    desigSel.disabled = true;
    const pick = el({ hidden: true });
    const note = savedDesignation ? el({ hidden: true }) : null;
    const classes = new Set();
    const parts = { '[name=value]': typeSel, '.ty-designation': pick, '[name=designation]': desigSel, '.ty-clear': note,
                    '.ed-btn': { textContent: 'Save Employment Type' } };
    const form = {
        classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), contains: c => classes.has(c) },
        querySelector: sel => (sel in parts ? parts[sel] : null),
        querySelectorAll: () => [typeSel, desigSel],
        reset() { typeSel.reset(); desigSel.reset(); },
        focus() {},
    };
    return { form, typeSel, desigSel, pick, note };
}

const src = fs.readFileSync(process.argv[2], 'utf8');
const forms = {};
const confirmBox = { checked: false };
const unsaved = { hidden: true, span: { textContent: '' }, querySelector() { return this.span; } };
const ctx = {
    console, Array, Math,
    window: { addEventListener() {}, innerHeight: 900 },
    getComputedStyle: () => ({ paddingTop: '40px', paddingBottom: '40px' }),
    document: {
        getElementById: id => forms[id] || (id === 'confirmCorrect' ? confirmBox
                             : id === 'unsavedMsg' ? unsaved
                             : { checked: false, disabled: false, title: '', style: {},
                                 getBoundingClientRect: () => ({ height: 300 }) }),
        querySelector: () => ({ getBoundingClientRect: () => ({ height: 800 }) }),
        querySelectorAll: sel => (sel === '.ed-form.open'
            ? Object.values(forms).filter(f => f.classList.contains('open')) : []),
        addEventListener() {},
    },
};
vm.createContext(ctx);
vm.runInContext(src, ctx);

// Regular -> Designee: the picker appears at once (no reload) and is optional.
let f = makeTypeForm('Regular', '');
forms['ed-employment_type'] = f.form;
ctx.toggleEdit('ed-employment_type', true);
assert.strictEqual(f.pick.hidden, true, 'no picker while the type is not Designee');
f.typeSel.value = 'Designee';
ctx.syncTypeForm(f.form);
assert.strictEqual(f.pick.hidden, false);
assert.strictEqual(f.desigSel.disabled, false, 'the chosen Designation is sent');
f.typeSel.value = 'Part-time';
ctx.syncTypeForm(f.form);
assert.strictEqual(f.pick.hidden, true);
assert.strictEqual(f.desigSel.disabled, true, 'a hidden Designation is never sent');

// Designee -> Part-time: the "will be removed" note appears; Cancel resets everything.
f = makeTypeForm('Designee', 'Campus Registrar');
forms['ed-employment_type'] = f.form;
ctx.toggleEdit('ed-employment_type', true);
assert.strictEqual(f.pick.hidden, false, 'a Designee sees the current Designation');
assert.strictEqual(f.desigSel.value, 'Campus Registrar', 'the saved Designation is preselected');
assert.strictEqual(f.note.hidden, true);
f.typeSel.value = 'Part-time';
ctx.syncTypeForm(f.form);
assert.strictEqual(f.note.hidden, false);
assert.strictEqual(f.pick.hidden, true);

// Save and Confirm is refused while that editor holds an unsaved change...
confirmBox.checked = true;
assert.strictEqual(ctx.confirmFinish(), false);
assert.strictEqual(unsaved.hidden, false);
assert.ok(unsaved.span.textContent.includes('Employment Type'), unsaved.span.textContent);
// ...and allowed after Cancel (or Save).
ctx.toggleEdit('ed-employment_type', false);
assert.strictEqual(f.typeSel.value, 'Designee');
assert.strictEqual(f.note.hidden, true);
assert.strictEqual(ctx.confirmFinish(), true);
assert.strictEqual(unsaved.hidden, true);
// An editor opened but unchanged doesn't block.
ctx.toggleEdit('ed-employment_type', true);
assert.strictEqual(ctx.confirmFinish(), true);
// The confirmation tick is required.
confirmBox.checked = false;
assert.strictEqual(ctx.confirmFinish(), false);

console.log('all checks passed');
