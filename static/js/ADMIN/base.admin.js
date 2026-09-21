// --- SIDEBAR TOGGLE (SAFE VERSION) ---
        document.addEventListener("DOMContentLoaded", function () {
            const sidebar = document.getElementById("sidebar");
            const toggleBtn = document.getElementById("toggleBtn");
            if (toggleBtn && sidebar) {
                toggleBtn.onclick = function () {
                    sidebar.classList.toggle("collapsed");
                };
            }
        });

        function toggleDropdown(event, menuId, arrowId) {
            event.preventDefault();
            event.stopPropagation();
            const menu  = document.getElementById(menuId);
            const arrow = document.getElementById(arrowId);
            if (menu)  menu.classList.toggle('show-submenu');
            if (arrow) arrow.classList.toggle('rotated');
        }

        // --- LOGOUT MODAL ---
        function openLogoutModal(e) {
            if (e) e.preventDefault();
            if (typeof closeUpDropdown === 'function') closeUpDropdown();
            const modal = document.getElementById("logoutModal");
            if (modal) modal.style.display = "flex";
        }

        function closeLogoutModal() {
            const modal = document.getElementById("logoutModal");
            if (modal) modal.style.display = "none";
        }

        function proceedLogout() {
            window.location.href = "/logout";
        }

        // Close modals when clicking background
        window.addEventListener("click", function (event) {
            const logoutModal = document.getElementById("logoutModal");
            if (event.target === logoutModal) {
                closeLogoutModal();
            }
        });

// --- ALERT POPUP (shared, see .notif-overlay in style.css) ---
// Centered success/error/warning/info popup for AJAX-driven actions (no page
// redirect to carry a flash() message through) — call
// showAlertPopup('success'|'error'|'warning'|'info', msg) from any admin
// page's JS instead of the browser's native alert().
function showAlertPopup(type, msg) {
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

    setTimeout(closeAlertPopup, 6000);
}

function closeAlertPopup() {
    const el = document.getElementById('sysNotifOverlay');
    if (!el) return;
    el.classList.add('notif-fade-out');
    setTimeout(() => el.remove(), 300);
}
