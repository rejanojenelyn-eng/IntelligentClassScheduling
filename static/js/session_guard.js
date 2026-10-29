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
 * 4. Account changed in another tab: every tab of a browser shares ONE login cookie, so
 *    signing in as someone else in another tab turns this tab into that account
 *    silently. Each page knows who it was opened as (data-user); when the browser's
 *    account differs — told by the other tab through localStorage, or by the server on
 *    the heartbeat — this tab is covered with a notice instead of acting as the wrong user.
 */
(function () {
    // The server registers this page as open when it sends it (data-page), so there is
    // no gap between the previous page closing and this one's first heartbeat.
    var me = document.currentScript;
    var pageId = (me && me.getAttribute('data-page'))
        || Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    var active = false;

    // ── 4. Account changed in another tab ────────────────────────────────────────
    var mine = {
        user: (me && me.getAttribute('data-user')) || '',
        name: (me && me.getAttribute('data-name')) || '',
        role: (me && me.getAttribute('data-role')) || ''
    };
    var LOGIN_KEY = 'dss_login_account';
    var switched = false;
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function label(a) { return '<b>' + esc(a.name || 'another account') + '</b>' + (a.role ? ' (' + esc(a.role) + ')' : ''); }
    function accountChanged(now) {
        if (switched || !mine.user || !now || !now.user || String(now.user) === String(mine.user)) return;
        switched = true;
        var ov = document.createElement('div');
        ov.setAttribute('role', 'alertdialog');
        ov.setAttribute('aria-modal', 'true');
        ov.style.cssText = 'position:fixed;inset:0;z-index:2147483000;background:rgba(20,0,0,.55);' +
            'display:flex;align-items:center;justify-content:center;padding:16px;font-family:Inter,Arial,sans-serif;';
        ov.innerHTML =
            '<div style="width:min(460px,100%);background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 18px 50px rgba(0,0,0,.35);">' +
              '<div style="background:#800000;color:#fff;padding:14px 18px;font-weight:800;font-size:1rem;">' +
                '<span style="margin-right:8px;">&#9888;</span>Signed in as another account</div>' +
              '<div style="padding:16px 18px;font-size:.88rem;line-height:1.55;color:#333;">' +
                '<p style="margin:0 0 10px;">This tab was opened as ' + label(mine) + ', but this browser is now signed in as ' +
                label(now) + ' from another tab.</p>' +
                '<p style="margin:0 0 4px;color:#666;font-size:.8rem;">All tabs of one browser share one login. ' +
                'To use two accounts at once, open the second one in an InPrivate / Incognito window or another browser.</p>' +
              '</div>' +
              '<div style="display:flex;gap:10px;justify-content:flex-end;padding:0 18px 16px;flex-wrap:wrap;">' +
                '<button type="button" data-act="signin" style="padding:9px 14px;border-radius:6px;border:1.5px solid #ccc;background:#fff;color:#444;font-weight:700;cursor:pointer;">Sign in as ' + esc(mine.name || 'previous account') + '</button>' +
                '<button type="button" data-act="continue" style="padding:9px 14px;border-radius:6px;border:0;background:#800000;color:#fff;font-weight:700;cursor:pointer;">Continue as ' + esc(now.name || 'current account') + '</button>' +
              '</div>' +
            '</div>';
        ov.addEventListener('click', function (e) {
            var b = e.target.closest('button[data-act]');
            if (!b) return;
            // "Sign in as …" signs the whole browser out (the other tab's account too).
            window.location.href = b.getAttribute('data-act') === 'continue' ? '/' : '/logout';
        });
        // Swallow keyboard input meant for the page underneath (Enter submitting a form).
        document.addEventListener('keydown', function (e) {
            if (!ov.contains(e.target)) { e.preventDefault(); e.stopPropagation(); }
        }, true);
        (document.body || document.documentElement).appendChild(ov);
        ov.querySelector('button[data-act="continue"]').focus();
    }
    function readShared() {
        try { return JSON.parse(localStorage.getItem(LOGIN_KEY) || 'null'); } catch (e) { return null; }
    }
    if (mine.user) {
        // A page loaded NOW reflects the cookie NOW: announce it to the other tabs.
        try { localStorage.setItem(LOGIN_KEY, JSON.stringify({ user: mine.user, name: mine.name, role: mine.role, t: Date.now() })); } catch (e) {}
        window.addEventListener('storage', function (e) {
            if (e.key === LOGIN_KEY) accountChanged(readShared());
        });
        // Coming back to this tab: check again (covers a missed storage event).
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') accountChanged(readShared());
        });
    }

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
            if (r.status === 401) { window.location.href = '/login'; return null; }
            return r.json();
        }).then(function (d) {
            if (d && d.user) accountChanged(d);   // the server's view of the cookie (no localStorage needed)
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
