/* ui_dialog.js — the system's own confirm / alert dialogs (instead of the browser's
   "127.0.0.1:5000 says" pop-ups). Same look as the "Leave Account Setup?" dialog.
   Loaded by the three base layouts and setup/_setup_layout.html.

     await uiConfirm({ title, html | message, confirmText, cancelText, icon, tone })  -> true / false
     await uiAlert({ title, html | message, okText, icon, tone })
   tone: 'warn' (default, amber icon), 'success' (green), 'error' (red).
   dismissible: false — Esc and clicking outside do nothing; only the button closes it.
   Use uiEsc() on any value put into `html`. */
(function () {
    'use strict';

    const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function injectStyles() {
        if (document.getElementById('uiDialogStyles')) return;
        const style = document.createElement('style');
        style.id = 'uiDialogStyles';
        style.textContent = `
        .uid-overlay { position: fixed; inset: 0; z-index: 10100; background: rgba(40, 0, 0, 0.45);
            display: flex; align-items: center; justify-content: center; padding: 16px; font-family: 'Inter', sans-serif; }
        .uid-card { background: #fff; border-radius: 14px; width: 100%; max-width: 440px; padding: 26px 24px 22px;
            text-align: center; box-shadow: 0 16px 40px rgba(0, 0, 0, 0.25); border-top: 5px solid #800000;
            animation: uidIn .16s ease-out; }
        @keyframes uidIn { from { transform: translateY(8px); opacity: 0; } to { transform: none; opacity: 1; } }
        .uid-icon { width: 52px; height: 52px; border-radius: 50%; background: #fff8e1; color: #d48806;
            display: flex; align-items: center; justify-content: center; font-size: 1.3rem; margin: 0 auto 12px; }
        .uid-card.success .uid-icon { background: #eef8f1; color: #1a6b3c; }
        .uid-card.error .uid-icon { background: #fdecec; color: #9b1c1c; }
        .uid-title { color: #4b0a0a; font-size: 1.1rem; font-weight: 800; margin: 0 0 8px; }
        .uid-text { color: #555; font-size: 0.9rem; line-height: 1.55; margin: 0 0 20px; }
        .uid-text strong { color: #800000; }
        .uid-note { display: block; margin-top: 10px; padding: 10px 12px; border-radius: 8px; background: #fff8e1;
            color: #7a5200; font-size: 0.84rem; text-align: left; }
        .uid-actions { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }
        .uid-btn { padding: 11px 18px; border-radius: 8px; font-weight: 800; font-size: 0.82rem; cursor: pointer;
            flex: 1 1 160px; font-family: inherit; letter-spacing: .3px; }
        .uid-ok { background: #800000; border: 2px solid #800000; color: #fff; }
        .uid-ok:hover { background: #5c0000; border-color: #5c0000; }
        .uid-cancel { background: #fff; border: 2px solid #ccc; color: #666; }
        .uid-cancel:hover { border-color: #800000; color: #800000; }
        .uid-btn:focus-visible { outline: 3px solid rgba(128, 0, 0, 0.3); outline-offset: 2px; }`;
        document.head.appendChild(style);
    }

    function open(opts, withCancel) {
        injectStyles();
        return new Promise(resolve => {
            const prevFocus = document.activeElement;
            const tone = opts.tone || 'warn';
            const icon = opts.icon || (tone === 'success' ? 'fa-circle-check'
                                     : tone === 'error' ? 'fa-circle-exclamation' : 'fa-triangle-exclamation');
            const overlay = document.createElement('div');
            overlay.className = 'uid-overlay';
            overlay.innerHTML = `
                <div class="uid-card ${tone}" role="${withCancel ? 'alertdialog' : 'dialog'}" aria-modal="true"
                     aria-labelledby="uidTitle" aria-describedby="uidText">
                    <div class="uid-icon"><i class="fas ${esc(icon)}"></i></div>
                    <h2 class="uid-title" id="uidTitle">${esc(opts.title || (withCancel ? 'Are you sure?' : 'Notice'))}</h2>
                    <div class="uid-text" id="uidText">${opts.html != null ? opts.html : esc(opts.message || '')}</div>
                    <div class="uid-actions">
                        ${withCancel ? `<button type="button" class="uid-btn uid-cancel">${esc(opts.cancelText || 'Cancel')}</button>` : ''}
                        <button type="button" class="uid-btn uid-ok">${esc(opts.confirmText || opts.okText || 'OK')}</button>
                    </div>
                </div>`;
            document.body.appendChild(overlay);
            const done = value => {
                overlay.remove();
                document.removeEventListener('keydown', onKey, true);
                if (prevFocus && prevFocus.focus) prevFocus.focus();
                resolve(value);
            };
            const dismissible = opts.dismissible !== false;   // false: only the button closes it
            const onKey = e => {
                if (e.key === 'Escape') { e.preventDefault(); if (dismissible) done(false); }
                else if (e.key === 'Tab') {                       // keep focus inside the dialog
                    const btns = Array.from(overlay.querySelectorAll('button'));
                    const i = btns.indexOf(document.activeElement);
                    e.preventDefault();
                    btns[(i + (e.shiftKey ? -1 : 1) + btns.length) % btns.length].focus();
                }
            };
            document.addEventListener('keydown', onKey, true);
            overlay.querySelector('.uid-ok').addEventListener('click', () => done(true));
            const cancel = overlay.querySelector('.uid-cancel');
            if (cancel) cancel.addEventListener('click', () => done(false));
            overlay.addEventListener('click', e => { if (e.target === overlay && dismissible) done(false); });
            // The safe choice gets the focus when there is one.
            (cancel || overlay.querySelector('.uid-ok')).focus();
        });
    }

    window.uiEsc = esc;
    window.uiConfirm = opts => open(opts || {}, true);
    window.uiAlert = opts => open(typeof opts === 'string' ? { message: opts } : (opts || {}), false).then(() => undefined);
})();
