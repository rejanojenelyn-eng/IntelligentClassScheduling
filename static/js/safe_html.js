/* Output encoding for values inserted into HTML built in JavaScript (C4: stored XSS).
   Loaded at the top of <head> on every HTML page (see _inject_csrf in app.py), before the
   page's own scripts. Every database / user value that goes into innerHTML,
   insertAdjacentHTML or a template string of markup must pass through one of these:

     escapeHtml(v)  text, or a quoted attribute value:  `<td>${escapeHtml(r.roomname)}</td>`
     jsArg(v)       an argument inside an inline handler: `onclick="openRoom(${jsArg(r.roomname)})"`
                    (JSON-encodes, then HTML-escapes: the browser decodes the attribute before
                    running the code, so HTML escaping alone is not enough there)
     safeUrl(v)     a whole URL from data in href/src: blocks javascript:, data:, vbscript:
*/
(function () {
    var MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;', '`': '&#96;' };
    function escapeHtml(v) {
        return v == null ? '' : String(v).replace(/[&<>"'`]/g, function (c) { return MAP[c]; });
    }
    function jsArg(v) {
        return escapeHtml(JSON.stringify(v === undefined ? null : v));
    }
    function safeUrl(v) {
        var s = v == null ? '' : String(v);
        return /^\s*(javascript|data|vbscript)\s*:/i.test(s.replace(/[\u0000-\u001F\u007F]/g, '')) ? '#' : escapeHtml(s);
    }
    window.escapeHtml = escapeHtml;
    window.jsArg = jsArg;
    window.safeUrl = safeUrl;
})();
