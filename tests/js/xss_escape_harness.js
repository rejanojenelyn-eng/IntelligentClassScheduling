// The escape helpers the dashboards and rooms pages use before innerHTML / inline onclick.
// Each helper is extracted from the real file, then attacked with XSS payloads.
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..', '..');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');

function extract(src, name) {
    const start = src.indexOf(`function ${name}(`);
    if (start < 0) throw new Error(`${name} not found`);
    let depth = 0, i = src.indexOf('{', start);
    for (; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}' && --depth === 0) break;
    }
    return new Function(`${src.slice(start, i + 1)}; return ${name};`)();
}

const PAYLOADS = [
    '<img src=x onerror=alert(1)>', '"><script>alert(1)</script>', "');alert(1);//",
    '\\\');alert(1);//', "</td><svg onload=alert(1)>", 'a&b "q" \'s\'\nline2\u2028x',
];
let failures = 0;
const check = (ok, msg) => { if (!ok) { failures++; console.error('FAIL', msg); } };

// Decode the HTML entities a browser decodes in an attribute value before running onclick.
const decodeAttr = s => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<')
                         .replace(/&gt;/g, '>').replace(/&amp;/g, '&');

for (const f of ['templates/academic/dashboard.html', 'templates/admin/dashboard_admin.html']) {
    const esc = extract(read(f), '_dashEsc');
    for (const p of PAYLOADS) {
        const out = esc(p);
        check(!/[<>"']/.test(out), `${f} _dashEsc left markup: ${out}`);
        check(decodeAttr(out) === p, `${f} _dashEsc changed the text: ${out}`);
    }
    check(esc(null) === '' && esc(0) === '0', `${f} _dashEsc null/0`);
}

for (const f of ['static/js/ADMIN/rooms.admin.js', 'static/js/ACAD HEAD/room.acad.js']) {
    const src = read(f), esc = extract(src, '_esc'), html = extract(src, '_html');
    for (const p of PAYLOADS) {
        const js = esc(p);
        // nothing that can end the JS string or the HTML attribute survives
        check(!/["'<>&\n\r\u2028\u2029]/.test(js) && !/\\(?!u[0-9a-f]{4})/.test(js), `${f} _esc unsafe: ${js}`);
        // the JS string still evaluates to exactly the original text (decoded attr == raw here)
        check(eval(`'${decodeAttr(js)}'`) === p, `${f} _esc round trip: ${js}`);
        check(eval(`'${js}'`) === p, `${f} _esc round trip (setAttribute): ${js}`);
        const h = html(p);
        check(!/[<>"']/.test(h) && decodeAttr(h) === p, `${f} _html: ${h}`);
    }
    // the row builder uses the helpers for every database text value
    for (const bad of ['>${r.roomname}<', '>${r.roomtype}<', '>${r.buildingname}<', "'${r.roomtype}'"]) {
        check(!src.includes(bad), `${f} still interpolates ${bad} raw`);
    }
}

for (const f of ['templates/academic/dashboard.html', 'templates/admin/dashboard_admin.html']) {
    check(!/<td>\$\{item\./.test(read(f)), `${f} still puts \${item.*} raw into a cell`);
}

if (failures) { console.error(`${failures} check(s) failed`); process.exit(1); }
console.log('xss escape helpers: all checks passed');
