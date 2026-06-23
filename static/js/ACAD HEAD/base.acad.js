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
