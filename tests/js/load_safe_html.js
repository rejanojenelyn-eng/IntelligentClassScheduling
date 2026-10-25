// Loads the real static/js/safe_html.js into a harness sandbox. In the browser every page
// gets it first (app._inject_csrf), so page scripts can call escapeHtml / jsArg / safeUrl.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'safe_html.js'), 'utf8');

module.exports = function loadSafeHtml(sandbox) {
    vm.runInContext(SRC, sandbox, { filename: 'safe_html.js' });
    const w = sandbox.window || sandbox;
    for (const k of ['escapeHtml', 'jsArg', 'safeUrl']) sandbox[k] = w[k];   // globals, like window.* in a page
};
