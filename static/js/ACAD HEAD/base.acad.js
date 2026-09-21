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

function toggleDropdown(event, menuId, arrowId) {
    event.preventDefault(); event.stopPropagation();
    const menu  = document.getElementById(menuId);
    const arrow = document.getElementById(arrowId);
    if (menu)  menu.classList.toggle('show-submenu');
    if (arrow) arrow.classList.toggle('rotated');
}
function toggleSubMenu(event, menuId, arrowId) { toggleDropdown(event, menuId, arrowId); }

function openLogoutModal(e) {
    if (e) e.preventDefault();
    document.getElementById("logoutModal").style.display = "flex";
    closeUpDropdown();
}
function closeLogoutModal() { document.getElementById("logoutModal").style.display = "none"; }
function proceedLogout()    { window.location.href = "/logout"; }

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

function closeAlertPopup() {
    const el = document.getElementById('sysNotifOverlay');
    if (!el) return;
    el.classList.add('notif-fade-out');
    setTimeout(() => el.remove(), 300);
}
