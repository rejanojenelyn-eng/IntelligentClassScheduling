document.addEventListener('DOMContentLoaded', function() {
    const sidebar   = document.getElementById("sidebar");
    const toggleBtn = document.getElementById("toggleBtn");
    if (localStorage.getItem('sidebarCollapsed') === 'true') sidebar.classList.add('collapsed');
    if (toggleBtn) {
        toggleBtn.onclick = () => {
            sidebar.classList.toggle("collapsed");
            localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
        };
    }
});

// Collapsed sidebar: a group (Schedule Management) folds into one icon. The icon is a
// link to the group's first page (Generate Schedule); clicking it also leaves the
// sidebar expanded (remembered like the ☰ toggle), so on arrival every page in the
// group is listed for further selection. Expanded, the icon is hidden.
function expandSidebarGroup(event) {
    const sidebar = document.getElementById('sidebar');
    if (sidebar) sidebar.classList.remove('collapsed');
    localStorage.setItem('sidebarCollapsed', 'false');
    // no preventDefault: the link then opens Generate Schedule
}

function toggleDropdown(event, menuId, arrowId) {
    event.preventDefault(); event.stopPropagation();
    const menu  = document.getElementById(menuId);
    const arrow = document.getElementById(arrowId);
    if (menu)  menu.classList.toggle('show-submenu');
    if (arrow) arrow.classList.toggle('rotated');
}
function toggleSubMenu(event, menuId, arrowId) { toggleDropdown(event, menuId, arrowId); }

// Pages with unsaved work register window.pageHasUnsavedWork = () => bool (Generate
// Schedule, Manual Editor). The Logout modal then says so itself, and confirming it sets
// window.isLeavingIntentionally so the page's beforeunload guard does not ALSO pop the
// browser's own "Leave site?" dialog on top of it.
function openLogoutModal(e) {
    if (e) e.preventDefault();
    const modal = document.getElementById("logoutModal");
    const unsaved = typeof window.pageHasUnsavedWork === 'function' && window.pageHasUnsavedWork();
    const title = modal.querySelector('.modal-body-logout h2');
    const text  = modal.querySelector('.modal-body-logout p');
    const btn   = modal.querySelector('.btn-confirm-logout');
    if (title) title.textContent = unsaved ? 'Log out without saving?' : 'Confirm Logout';
    if (text)  text.textContent  = unsaved
        ? 'You have unsaved changes on this page. If you log out now, they will be lost.'
        : 'Are you sure you want to log out of the PUPLC System?';
    if (btn)   btn.textContent   = unsaved ? 'Log out anyway' : 'Logout';
    modal.classList.toggle('logout-unsaved', !!unsaved);
    modal.style.display = "flex";
    closeUpDropdown();
}
function closeLogoutModal() { document.getElementById("logoutModal").style.display = "none"; }
function proceedLogout() {
    window.isLeavingIntentionally = true;   // already confirmed here — no second browser dialog
    window.location.href = "/logout";
}

window.onclick = function(event) {
    const modal = document.getElementById("logoutModal");
    if (event.target === modal) closeLogoutModal();
};

// --- ALERT POPUP (shared, see .notif-overlay in style.css) ---
// Centered success/error/warning/info popup for AJAX-driven actions (no page
// redirect to carry a flash() message through) — call
// showAlertPopup('success'|'error'|'warning'|'info', msg[, duration]) from any
// Academic Head page's JS instead of the browser's native alert(). `duration`
// (ms) defaults to 6000 — pass a longer value for messages long enough that
// 6s isn't enough time to read them (e.g. multi-line DB error detail).
function showAlertPopup(type, msg, duration) {
    document.getElementById('sysNotifOverlay')?.remove();

    const kind  = ['success', 'warning', 'info'].includes(type) ? type : 'error';
    const icon  = { success: 'fa-check-circle', warning: 'fa-exclamation-triangle',
                    info: 'fa-info-circle', error: 'fa-exclamation-circle' }[kind];
    const title = { success: 'Success', warning: 'Warning', info: 'Notice', error: 'Error' }[kind];

    const overlay = document.createElement('div');
    overlay.id = 'sysNotifOverlay';
    overlay.className = 'notif-overlay';
    overlay.onclick = closeAlertPopup;
    overlay.innerHTML = `
        <div class="notif-popup">
            <div class="notif-icon notif-${kind}"><i class="fas ${icon}"></i></div>
            <div class="notif-body">
                <div class="notif-title">${title}</div>
                <div class="notif-message"></div>
            </div>
            <button class="notif-close-btn" type="button"><i class="fas fa-times"></i></button>
        </div>`;
    overlay.querySelector('.notif-popup').onclick = e => e.stopPropagation();
    overlay.querySelector('.notif-message').textContent = msg;
    overlay.querySelector('.notif-close-btn').onclick = closeAlertPopup;
    document.body.appendChild(overlay);

    setTimeout(closeAlertPopup, duration || 6000);
}

// --- CORNER TOAST ---
// Small, non-blocking notice in the top-right corner for a finished action
// ("Schedule Published", "Saved as Draft", "Schedule Deleted"): nothing to click, it
// fades out by itself. Several stack downward from the top-right. Use showAlertPopup / a modal instead for
// anything the user must read or decide.
function showCornerToast(title, msg, type, duration) {
    const kind = ['success', 'warning', 'info', 'error'].includes(type) ? type : 'success';
    const color = { success: '#2e7d32', warning: '#c8860a', info: '#1a6fa0', error: '#c0392b' }[kind];
    const icon  = { success: 'fa-check-circle', warning: 'fa-exclamation-triangle',
                    info: 'fa-info-circle', error: 'fa-exclamation-circle' }[kind];
    let stack = document.getElementById('cornerToastStack');
    if (!stack) {
        stack = document.createElement('div');
        stack.id = 'cornerToastStack';
        stack.setAttribute('role', 'status');
        stack.setAttribute('aria-live', 'polite');
        // Top-right, under the page header: the bottom-right corner holds the editor's
        // Save as Draft / Approve buttons.
        stack.style.cssText = 'position:fixed;right:20px;top:116px;z-index:100000;display:flex;' +
            'flex-direction:column;gap:8px;max-width:min(340px,calc(100vw - 32px));pointer-events:none;';
        document.body.appendChild(stack);
    }
    const t = document.createElement('div');
    t.style.cssText = `pointer-events:auto;display:flex;align-items:flex-start;gap:10px;background:#fff;` +
        `border:1px solid #e6dede;border-left:4px solid ${color};border-radius:8px;padding:10px 12px;` +
        `box-shadow:0 6px 20px rgba(0,0,0,.14);font-size:.8rem;color:#333;` +
        `opacity:0;transform:translateY(8px);transition:opacity .2s,transform .2s;`;
    t.innerHTML = `<i class="fas ${icon}" style="color:${color};font-size:1rem;margin-top:1px;"></i>` +
        `<div style="flex:1;min-width:0;"><div class="ct-title" style="font-weight:800;color:#2c3e50;"></div>` +
        `<div class="ct-msg" style="margin-top:2px;color:#666;font-size:.74rem;white-space:pre-line;"></div></div>` +
        `<button type="button" aria-label="Close" style="border:0;background:none;color:#aaa;cursor:pointer;padding:0 2px;">` +
        `<i class="fas fa-times"></i></button>`;
    t.querySelector('.ct-title').textContent = title || '';
    const m = t.querySelector('.ct-msg');
    if (msg) m.textContent = msg; else m.remove();
    const close = () => { t.style.opacity = '0'; t.style.transform = 'translateY(8px)'; setTimeout(() => t.remove(), 220); };
    t.querySelector('button').onclick = close;
    stack.appendChild(t);
    requestAnimationFrame(() => { t.style.opacity = '1'; t.style.transform = 'none'; });
    setTimeout(close, duration || 3500);
}

function closeAlertPopup() {
    const el = document.getElementById('sysNotifOverlay');
    if (!el) return;
    el.classList.add('notif-fade-out');
    setTimeout(() => el.remove(), 300);
}
