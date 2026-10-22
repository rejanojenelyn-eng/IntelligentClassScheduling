/* Session guard — added to every signed-in page by app.py (_inject_session_guard).
 *
 * 1. Open pages: each page is registered (with its own id) by the server as it is sent,
 *    re-registers on load, and sends a beacon when it closes. A page load while none of the login's
 *    pages is open means the tabs/browser were closed, and the server ends the
 *    session on the spot — reopening the link requires a login.
 * 2. Heartbeat: an open page re-registers once a minute, also reporting whether the
 *    user did anything (drives SESSION_IDLE_LIMIT). If the closing beacon never
 *    arrives, the missing heartbeats end the session after SESSION_CLOSED_LIMIT.
 * 3. Back/Forward: a page restored from the browser's back/forward cache is shown
 *    without asking the server, so reload it and let the server re-check the session.
 */
(function () {
    // The server registers this page as open when it sends it (data-page), so there is
    // no gap between the previous page closing and this one's first heartbeat.
    var me = document.currentScript;
    var pageId = (me && me.getAttribute('data-page'))
        || Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    var active = false;

    ['mousedown', 'keydown', 'scroll', 'touchstart'].forEach(function (ev) {
        window.addEventListener(ev, function () { active = true; }, { passive: true, capture: true });
    });

    function beat() {
        var wasActive = active;
        active = false;
        fetch('/api/session/heartbeat', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active: wasActive, page: pageId })
        }).then(function (r) {
            if (r.status === 401) window.location.href = '/login';
        }).catch(function () { /* offline — the next beat retries */ });
    }

    window.addEventListener('pagehide', function () {
        var body = new Blob([JSON.stringify({ page: pageId })], { type: 'application/json' });
        if (navigator.sendBeacon) navigator.sendBeacon('/api/session/page_closed', body);
    });

    // Register before reloading: the page just left has already reported itself closed,
    // and the server ends the session on a page load while no page is open.
    window.addEventListener('pageshow', function (e) {
        if (!e.persisted) return;
        pageId = Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
        fetch('/api/session/heartbeat', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active: true, page: pageId })
        }).then(function (r) {
            if (r.status === 401) window.location.href = '/login';
            else window.location.reload();
        }).catch(function () { window.location.reload(); });
    });

    beat();
    setInterval(beat, 60000);
})();
