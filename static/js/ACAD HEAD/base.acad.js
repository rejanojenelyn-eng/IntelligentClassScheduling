document.addEventListener('DOMContentLoaded', function() {
            const sidebar = document.getElementById("sidebar");
            const toggleBtn = document.getElementById("toggleBtn");
            // Restore collapse state across page navigations
            if (localStorage.getItem('sidebarCollapsed') === 'true') {
                sidebar.classList.add('collapsed');
            }
            if (toggleBtn) {
                toggleBtn.onclick = () => {
                    sidebar.classList.toggle("collapsed");
                    localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
                };
            }
        });

        function toggleDropdown(event, menuId, arrowId) {
            event.preventDefault();
            event.stopPropagation();
            const menu = document.getElementById(menuId);
            const arrow = document.getElementById(arrowId);
            if (menu) menu.classList.toggle('show-submenu');
            if (arrow) arrow.classList.toggle('rotated');
        }

        // Legacy alias kept for any other callers
        function toggleSubMenu(event, menuId, arrowId) {
            toggleDropdown(event, menuId, arrowId);
        }

        // Logout Modal Functions
        function openLogoutModal(e) { 
            e.preventDefault(); 
            document.getElementById("logoutModal").style.display = "flex"; 
        }
        function closeLogoutModal() { 
            document.getElementById("logoutModal").style.display = "none"; 
        }
        function proceedLogout() { 
            window.location.href = "/logout"; 
        }
        
        // Close modal when clicking outside of it
        window.onclick = function (event) { 
            let modal = document.getElementById("logoutModal"); 
            if (event.target == modal) { 
                closeLogoutModal(); 
            } 
        }
