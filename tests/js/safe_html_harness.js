// Loads the real static/js/safe_html.js and checks escapeHtml / jsArg / safeUrl.
// Run by tests/test_safe_html_js.py.
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'safe_html.js'), 'utf8'), sandbox);
const { escapeHtml, jsArg, safeUrl } = sandbox.window;

// What a browser does with an attribute value before running an inline handler.
const decodeAttr = s => s.replace(/&(amp|lt|gt|quot|#39|#96);/g, (_, e) =>
    ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", '#96': '`' })[e]);

const nasty = [
    "x');alert(1);('", '"><img src=x onerror=alert(1)>', '</script><script>alert(1)</script>',
    'back\\slash\\', 'tab\tand\nnewline', "O'Neil & Sons \"Ltd\"", '`${alert(1)}`', 'Ñoño – ü 日本',
    ' line sep', '', '&amp; already escaped',
];

// escapeHtml: no markup characters survive, nothing else changes
for (const s of nasty) {
    const out = escapeHtml(s);
    assert.ok(!/[<>"'`]/.test(out), `escapeHtml left markup in ${JSON.stringify(s)}`);
    assert.equal(decodeAttr(out), s, 'escapeHtml round-trips');
}
assert.equal(escapeHtml(null), '');
assert.equal(escapeHtml(undefined), '');
assert.equal(escapeHtml(0), '0');
assert.equal(escapeHtml('Room 101'), 'Room 101');

// jsArg inside onclick="f(${jsArg(x)})": decoded, then run as JS, it yields exactly x
for (const v of [...nasty, 42, 3.5, null, true]) {
    const attr = jsArg(v);
    assert.ok(!/[<>"']/.test(attr), `jsArg output must be attribute-safe: ${attr}`);
    const got = vm.runInNewContext(`(function f(a){ return a; })(${decodeAttr(attr)})`);
    assert.deepEqual(got, v, `jsArg round-trip for ${JSON.stringify(v)}`);
}
assert.equal(jsArg(undefined), 'null');
// A breakout attempt never becomes code: the handler only ever sees one argument.
let calls = 0;
const ctx = vm.createContext({ alert: () => { calls++; } });
vm.runInContext(`(function f(a){})(${decodeAttr(jsArg("');alert(1);('"))})`, ctx);
assert.equal(calls, 0, 'payload executed');

// safeUrl: blocks script URLs, keeps normal ones
for (const bad of ['javascript:alert(1)', ' JavaScript:alert(1)', 'java\tscript:alert(1)', 'data:text/html,x', 'vbscript:x'])
    assert.equal(safeUrl(bad), '#', bad);
assert.equal(safeUrl('/schedule?overlay_id=3&prog=BSIT'), '/schedule?overlay_id=3&amp;prog=BSIT');
assert.equal(safeUrl('https://example.com/a"b'), 'https://example.com/a&quot;b');

console.log('safe_html.js: all checks passed');
