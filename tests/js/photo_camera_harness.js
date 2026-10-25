// Loads the REAL static/js/photo.camera.js into a stub DOM and checks openPhotoCamera():
// permission is asked only when it is called, Capture returns a JPEG File and turns the
// camera off, Cancel returns null and turns it off, a blocked / missing camera is explained,
// and without a live camera API the device's camera picker is used instead.
// Usage: node photo_camera_harness.js
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const SRC = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'photo.camera.js'), 'utf8');
const tick = () => new Promise(r => setImmediate(r));

function load({ media = true, fail = null } = {}) {
    const page = { asked: 0, stopped: 0, overlays: [], inputs: [], keyHandlers: [] };
    function el(extra = {}) {
        const listeners = {};
        return Object.assign({
            style: {}, textContent: '', disabled: false, innerHTML: '',
            addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
            fire: (t, ev = {}) => (listeners[t] || []).forEach(fn => fn(ev)),
            setAttribute() {}, focus() {}, remove() { this.removed = true; },
        }, extra);
    }
    const document = {
        createElement: tag => {
            if (tag === 'canvas') {
                return { getContext: () => ({ translate() {}, scale() {}, drawImage() {} }),
                         toBlob: cb => cb({ jpeg: true }) };
            }
            if (tag === 'input') { const i = el({ click() { this.clicked = true; }, files: [] }); page.inputs.push(i); return i; }
            const parts = { '.pcam-video': el({ videoWidth: 640, videoHeight: 480, play: async () => {} }),
                            '.pcam-msg': el(), '.pcam-shoot': el({ disabled: true }), '.pcam-cancel': el() };
            const o = el({ querySelector: sel => parts[sel], parts });
            page.overlays.push(o);
            return o;
        },
        body: { appendChild() {} },
        addEventListener: (t, fn) => { if (t === 'keydown') page.keyHandlers.push(fn); },
        removeEventListener: (t, fn) => { page.keyHandlers = page.keyHandlers.filter(h => h !== fn); },
    };
    const navigator = { mediaDevices: media ? {
        getUserMedia: async () => {
            page.asked++;
            if (fail) { const e = new Error(fail); e.name = fail; throw e; }
            return { getTracks: () => [{ stop: () => { page.stopped++; } }] };
        } } : undefined };
    const ctx = { console, Promise, document, navigator,
                  File: class { constructor(parts, name, opts) { this.name = name; this.type = opts.type; } } };
    ctx.window = Object.assign(ctx, { isSecureContext: true });
    vm.createContext(ctx);
    vm.runInContext(SRC, ctx);
    return { ctx, page };
}

(async () => {
    // Nothing happens (no permission prompt) until the user picks Take a Photo.
    let { ctx, page } = load();
    assert.strictEqual(page.asked, 0);

    // Capture.
    let result = ctx.openPhotoCamera();
    await tick(); await tick();
    assert.strictEqual(page.asked, 1, 'permission is asked when the camera is opened');
    let o = page.overlays[0];
    assert.strictEqual(o.parts['.pcam-shoot'].disabled, false, 'Capture is enabled once the camera runs');
    o.parts['.pcam-shoot'].fire('click');
    let file = await result;
    assert.strictEqual(file.type, 'image/jpeg');
    assert.strictEqual(page.stopped, 1, 'the camera is turned off after capturing');
    assert.ok(o.removed, 'the dialog closes');

    // Cancel (button, and Escape).
    ({ ctx, page } = load());
    result = ctx.openPhotoCamera();
    await tick(); await tick();
    page.overlays[0].parts['.pcam-cancel'].fire('click');
    assert.strictEqual(await result, null);
    assert.strictEqual(page.stopped, 1);
    ({ ctx, page } = load());
    result = ctx.openPhotoCamera();
    await tick(); await tick();
    page.keyHandlers.forEach(h => h({ key: 'Escape' }));
    assert.strictEqual(await result, null);
    assert.strictEqual(page.keyHandlers.length, 0, 'its key handler is removed');

    // Permission denied / no camera: explained, Capture stays off, Cancel still works.
    for (const [fail, text] of [['NotAllowedError', 'blocked'], ['NotFoundError', 'No camera was found']]) {
        ({ ctx, page } = load({ fail }));
        result = ctx.openPhotoCamera();
        await tick(); await tick();
        o = page.overlays[0];
        assert.ok(o.parts['.pcam-msg'].textContent.includes(text), fail);
        assert.strictEqual(o.parts['.pcam-shoot'].disabled, true);
        o.parts['.pcam-cancel'].fire('click');
        assert.strictEqual(await result, null);
    }

    // No live camera API: the device's camera picker (capture) is used.
    ({ ctx, page } = load({ media: false }));
    result = ctx.openPhotoCamera();
    const input = page.inputs[0];
    assert.ok(input.clicked && input.accept === 'image/*');
    input.files = [{ name: 'IMG_0001.jpg', type: 'image/jpeg' }];
    input.fire('change');
    assert.strictEqual((await result).name, 'IMG_0001.jpg');

    console.log('all checks passed');
})().catch(e => { console.error(e); process.exit(1); });
