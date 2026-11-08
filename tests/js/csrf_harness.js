// Loads the real static/js/csrf.js against a minimal fake page and checks that it adds
// the CSRF token to same-site state-changing requests only. Run by tests/test_csrf_js.py.
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const calls = [];
class FakeXHR {
    open(method) { this.method = method; this.headers = {}; }
    setRequestHeader(k, v) { this.headers[k] = v; }
    send() { calls.push({ kind: 'xhr', method: this.method, headers: this.headers }); }
}
class FakeForm {
    constructor(attrs) { this.tagName = 'FORM'; this.attrs = Object.assign({}, attrs); this.children = []; this.style = {}; }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    set method(v) { this.attrs.method = v; }
    set action(v) { this.attrs.action = v; }
    querySelector(sel) { return this.children.find(c => sel.includes(`name="${c.name}"`)) || null; }
    appendChild(c) { this.children.push(c); }
    reset() {}
    submit() { calls.push({ kind: 'submit', attrs: this.attrs, fields: this.children.map(c => [c.name, c.value]) }); }
}
const listeners = {};
const document = {
    querySelector: sel => (sel.includes('csrf-token') ? { getAttribute: () => 'TOKEN-123' } : null),
    addEventListener: (type, fn) => { listeners[type] = fn; },
    createElement: tag => (tag === 'form' ? new FakeForm() : { tagName: tag.toUpperCase() }),
    body: { appendChild() {} },
};
const window = {
    location: { href: 'https://app.test/schedule', origin: 'https://app.test' },
    fetch: (input, init) => { calls.push({ kind: 'fetch', input, init }); return Promise.resolve(); },
};
const sandbox = { window, document, XMLHttpRequest: FakeXHR, HTMLFormElement: FakeForm,
                  Headers, Request, URL, Object, console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'csrf.js'), 'utf8'), sandbox);

const last = () => calls[calls.length - 1];
const tokenOf = c => c.init && c.init.headers ? new Headers(c.init.headers).get('X-CSRFToken') : null;

// fetch
assert.equal(window.CSRF_TOKEN, 'TOKEN-123');
window.fetch('/api/schedule/approve', { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json' } });
assert.equal(tokenOf(last()), 'TOKEN-123', 'POST gets the token');
assert.equal(new Headers(last().init.headers).get('Content-Type'), 'application/json', 'existing headers kept');
window.fetch('/api/schedule/drafts/4', { method: 'delete' });
assert.equal(tokenOf(last()), 'TOKEN-123', 'lower-case delete gets the token');
window.fetch('/api/user/me');
assert.equal(tokenOf(last()), null, 'GET gets no token');
window.fetch('https://evil.example/steal', { method: 'POST' });
assert.equal(tokenOf(last()), null, 'other sites never get the token');
window.fetch(new Request('https://app.test/api/x', { method: 'PUT' }));
assert.equal(tokenOf(last()), 'TOKEN-123', 'a Request object gets the token');
window.fetch('/api/x', { method: 'POST', headers: { 'X-CSRFToken': 'own' } });
assert.equal(tokenOf(last()), 'own', 'an explicit token is not overwritten');

// XMLHttpRequest
let x = new FakeXHR(); x.open('POST', '/admin/settings/upsert_ay'); x.send();
assert.equal(last().headers['X-CSRFToken'], 'TOKEN-123');
x = new FakeXHR(); x.open('GET', '/admin/settings'); x.send();
assert.equal(last().headers['X-CSRFToken'], undefined);
x = new FakeXHR(); x.open('POST', 'https://evil.example/x'); x.send();
assert.equal(last().headers['X-CSRFToken'], undefined);

// forms: submit event and form.submit(); an <input name="action"> must not confuse it
const posted = new FakeForm({ method: 'post' });
posted.children.push({ name: 'action', value: 'confirm' });
listeners.submit({ target: posted });
assert.deepEqual(posted.children.find(c => c.name === 'csrf_token').value, 'TOKEN-123');
const viaSubmit = new FakeForm({ method: 'POST', action: '/account-setup/verify' });
viaSubmit.submit();
assert.deepEqual(last().fields, [['csrf_token', 'TOKEN-123']]);
const getForm = new FakeForm({ method: 'get' });
listeners.submit({ target: getForm });
assert.equal(getForm.children.length, 0, 'GET forms get no token');
const external = new FakeForm({ method: 'post', action: 'https://evil.example/x' });
listeners.submit({ target: external });
assert.equal(external.children.length, 0, 'forms to other sites get no token');

// csrfPost: the buttons that used to be GET links
window.csrfPost('/archive_employee/E-1');
assert.equal(last().kind, 'submit');
assert.equal(last().attrs.method, 'POST');
assert.equal(last().attrs.action, '/archive_employee/E-1');
assert.deepEqual(last().fields, [['csrf_token', 'TOKEN-123']]);

console.log('csrf.js: all checks passed');
