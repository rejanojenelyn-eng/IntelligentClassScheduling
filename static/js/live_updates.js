/* Live schedule updates — another device or tab saved, published or deleted schedules,
   and this page shows it without a manual reload.

   A page opts in by setting window.DSS_LIVE_SCHEDULE = true before the page finishes
   loading. While the tab is visible it asks /api/live/schedule_rev every few seconds (a
   tiny one-row read). When the revision moved because of ANOTHER page (not this page's
   own save — see X-Page-Id in csrf.js), it fires a cancelable 'dss:schedule-changed'
   event on window (detail: {rev, by, action, scope, at}). A page that refreshes itself
   in place (the Manual Editor) calls event.preventDefault(); otherwise a banner offers
   a Refresh button. */
(function () {
    var POLL_MS = 5000;
    var lastRev = null, timer = null, stopped = false, failures = 0;

    function esc(t) {
        return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function describe(d) {
        var who = d.by ? d.by : 'Someone';
        return who + ' ' + (d.action || 'changed the schedules') + (d.scope ? ' (' + d.scope + ')' : '') +
               (d.at ? ' at ' + d.at : '');
    }

    function ensureStyle() {
        if (document.getElementById('dss-live-style')) return;
        var st = document.createElement('style');
        st.id = 'dss-live-style';
        st.textContent =
            '.dss-live{position:fixed;left:50%;top:76px;transform:translateX(-50%);z-index:10040;max-width:min(560px,calc(100vw - 32px));' +
            'display:flex;align-items:center;gap:10px;padding:10px 12px 10px 14px;background:#fff;border:1px solid #e8d0d0;' +
            'border-left:4px solid #800000;border-radius:10px;box-shadow:0 8px 26px rgba(0,0,0,.18);font-size:.8rem;color:#333;}' +
            '.dss-live i.dss-live-ico{color:#800000;}' +
            '.dss-live-txt{flex:1;min-width:0;line-height:1.4;}' +
            '.dss-live-btn{border:0;background:#800000;color:#fff;font-weight:700;font-size:.74rem;padding:6px 12px;border-radius:6px;cursor:pointer;white-space:nowrap;}' +
            '.dss-live-x{border:0;background:none;color:#888;font-size:1.05rem;cursor:pointer;line-height:1;padding:2px 4px;}' +
            '.dss-live.toast{border-left-color:#2e7d32;}.dss-live.toast i.dss-live-ico{color:#2e7d32;}';
        document.head.appendChild(st);
    }

    function removeBox() {
        var old = document.getElementById('dssLiveBox');
        if (old) old.remove();
    }

    // Banner with a Refresh button (pages that can't refresh themselves in place).
    function showBanner(d) {
        ensureStyle(); removeBox();
        var box = document.createElement('div');
        box.id = 'dssLiveBox'; box.className = 'dss-live'; box.setAttribute('role', 'status');
        box.innerHTML = '<i class="fas fa-rotate dss-live-ico"></i>' +
            '<div class="dss-live-txt"><b>Schedules were updated.</b> ' + esc(describe(d)) + '.</div>' +
            '<button type="button" class="dss-live-btn">Refresh</button>' +
            '<button type="button" class="dss-live-x" aria-label="Dismiss">&times;</button>';
        box.querySelector('.dss-live-btn').onclick = function () { window.location.reload(); };
        box.querySelector('.dss-live-x').onclick = removeBox;
        document.body.appendChild(box);
    }

    // Short notice after a page refreshed itself (disappears by itself).
    function showToast(text) {
        ensureStyle(); removeBox();
        var box = document.createElement('div');
        box.id = 'dssLiveBox'; box.className = 'dss-live toast'; box.setAttribute('role', 'status');
        box.innerHTML = '<i class="fas fa-circle-check dss-live-ico"></i><div class="dss-live-txt">' + esc(text) + '</div>' +
            '<button type="button" class="dss-live-x" aria-label="Dismiss">&times;</button>';
        box.querySelector('.dss-live-x').onclick = removeBox;
        document.body.appendChild(box);
        setTimeout(function () { if (box.isConnected) box.remove(); }, 6000);
    }

    function schedule(ms) {
        clearTimeout(timer);
        if (!stopped) timer = setTimeout(poll, ms);
    }

    function poll() {
        if (stopped) return;
        if (document.visibilityState !== 'visible') { schedule(POLL_MS); return; }
        fetch('/api/live/schedule_rev', { credentials: 'same-origin' }).then(function (r) {
            if (r.status === 401) { stopped = true; return null; }
            return r.json();
        }).then(function (d) {
            if (!d || !d.success) { if (!stopped) schedule(POLL_MS); return; }
            failures = 0;
            if (lastRev === null || d.rev <= lastRev || d.mine) {
                lastRev = Math.max(lastRev === null ? d.rev : lastRev, d.rev);
            } else {
                lastRev = d.rev;
                var ev = new CustomEvent('dss:schedule-changed', { detail: d, cancelable: true });
                var handled = !window.dispatchEvent(ev);
                if (!handled) showBanner(d);
            }
            schedule(POLL_MS);
        }).catch(function () {
            failures = Math.min(failures + 1, 6);
            schedule(POLL_MS * (1 + failures));   // offline: back off a little
        });
    }

    window.DSSLive = { toast: showToast, describe: describe, pollNow: function () { schedule(0); } };

    function start() {
        if (!window.DSS_LIVE_SCHEDULE) return;   // only pages that opted in
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') schedule(0);
        });
        schedule(0);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
