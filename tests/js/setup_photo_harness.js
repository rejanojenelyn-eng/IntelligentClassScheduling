// Drives the REAL inline script of the Account Setup photo step (rendered by Flask and passed
// in as a file) in a stub DOM:
//   - Remove Photo: a photo picked on this visit is discarded locally; a photo already saved
//     on the account is removed through the server (form posted with action=remove).
//   - Take Photo: live camera preview -> Capture -> cropper -> the form's photo input;
//     falls back to the device camera picker without getUserMedia; explains a blocked camera.
// Usage: node setup_photo_harness.js <rendered-script.js> <saved photo url or "">
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

function load(savedPhoto, { media = true, deny = false } = {}) {
    const page = { submitted: null, clicked: [], tracksStopped: 0 };
    const els = {};
    const el = (id, props = {}) => (els[id] = els[id] || Object.assign({
        id, hidden: false, disabled: false, value: '', textContent: '', style: {}, files: [],
        click() { page.clicked.push(id); }, focus() {}, removeAttribute() {}, appendChild() {},
    }, props));
    el('btnRemovePhoto', { disabled: !savedPhoto });
    el('camDialog', { hidden: true });
    el('camMsg', { hidden: true });
    el('camVideo', { videoWidth: 640, videoHeight: 480, play: async () => {} });
    const form = el('setupForm', {
        children: [], appendChild(c) { this.children.push(c); },
        submit() { page.submitted = this.children.map(c => [c.name, c.value]); },
    });
    class DataTransfer {
        constructor() { const list = []; this.items = { add: f => list.push(f) }; this.files = list; }
    }
    const ctx = {
        console, Promise, Array, Object, JSON, parseInt, Math,
        DataTransfer,
        File: class { constructor(parts, name, opts) { this.name = name; this.type = opts.type; this.size = 10; } },
        URL: { createObjectURL: () => 'blob:preview' },
        openPhotoCropper: async () => ({ cropped: true }),
        document: {
            getElementById: id => el(id),
            createElement: tag => (tag === 'canvas'
                ? { getContext: () => ({ translate() {}, scale() {}, drawImage() {} }),
                    toBlob: cb => cb({ blob: true }) }
                : { type: '', name: '', value: '' }),
            addEventListener() {},
            body: { style: {} },
        },
        navigator: { mediaDevices: media ? {
            getUserMedia: async () => {
                if (deny) { const e = new Error('denied'); e.name = 'NotAllowedError'; throw e; }
                return { getTracks: () => [{ stop: () => { page.tracksStopped++; } }] };
            } } : undefined },
    };
    ctx.window = Object.assign(ctx, { isSecureContext: true, addEventListener() {} });
    const src = fs.readFileSync(process.argv[2], 'utf8')
        .replace(/const _currentPhoto = "[^"]*";/, `const _currentPhoto = ${JSON.stringify(savedPhoto)};`);
    vm.createContext(ctx);
    vm.runInContext(src, ctx);
    return Object.assign(page, { ctx, el });
}

(async () => {
    // 1. Saved photo (came back with Back): Remove posts action=remove to the server.
    let p = load('/static/uploads/profile_photos/profile_x.jpg');
    assert.strictEqual(p.el('btnRemovePhoto').disabled, false);
    p.ctx.removePhoto();
    assert.deepStrictEqual(p.submitted, [['action', 'remove']]);
    assert.strictEqual(p.el('photoInput').disabled, true, 'no file is uploaded with a removal');

    // 2. A new pick on this visit is discarded locally first (no server call).
    p = load('/static/uploads/profile_photos/profile_x.jpg');
    await p.ctx.usePhoto({ name: 'me.png' }, 'me.png');
    assert.strictEqual(p.el('photoInput').files.length, 1);
    p.ctx.removePhoto();
    assert.strictEqual(p.submitted, null);
    assert.strictEqual(p.el('photoInput').files.length, 0);
    assert.strictEqual(p.el('btnRemovePhoto').disabled, false, 'the saved photo can still be removed');

    // 3. No saved photo: Remove is enabled only while a new pick exists.
    p = load('');
    assert.strictEqual(p.el('btnRemovePhoto').disabled, true);
    await p.ctx.usePhoto({ name: 'me.png' }, 'me.png');
    assert.strictEqual(p.el('btnRemovePhoto').disabled, false);
    p.ctx.removePhoto();
    assert.strictEqual(p.el('btnRemovePhoto').disabled, true);

    // 4. Take Photo: preview opens, Capture puts the cropped picture in the form, camera stops.
    p = load('');
    await p.ctx.openCamera();
    assert.strictEqual(p.el('camDialog').hidden, false);
    assert.strictEqual(p.el('btnCapture').disabled, false);
    p.ctx.capturePhoto();
    await new Promise(r => setImmediate(r));
    assert.strictEqual(p.el('camDialog').hidden, true);
    assert.strictEqual(p.tracksStopped, 1, 'the camera is turned off after capturing');
    assert.strictEqual(p.el('photoInput').files.length, 1);
    assert.strictEqual(p.el('photoInput').files[0].type, 'image/jpeg');
    assert.strictEqual(p.el('photoName').textContent, 'Photo taken with your camera');
    assert.strictEqual(p.ctx.setupValidateContinue(), '', 'Save and Next accepts the captured photo');

    // 5. Cancel turns the camera off and changes nothing.
    p = load('');
    await p.ctx.openCamera();
    p.ctx.closeCamera();
    assert.strictEqual(p.tracksStopped, 1);
    assert.strictEqual(p.el('photoInput').files.length, 0);

    // 6. Camera blocked: explained, Capture stays off.
    p = load('', { deny: true });
    await p.ctx.openCamera();
    assert.strictEqual(p.el('camMsg').hidden, false);
    assert.ok(p.el('camMsg').textContent.includes('blocked'));
    assert.strictEqual(p.el('btnCapture').disabled, true);

    // 7. No live camera API: the device's camera picker is used instead.
    p = load('', { media: false });
    await p.ctx.openCamera();
    assert.deepStrictEqual(p.clicked, ['cameraInput']);
    assert.strictEqual(p.el('camDialog').hidden, true);

    console.log('all checks passed');
})().catch(e => { console.error(e); process.exit(1); });
