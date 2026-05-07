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

        // Sub-menu toggle function (Only drops down, does not navigate)
        function toggleSubMenu(event, menuId, arrowId) {
            event.preventDefault();
            event.stopPropagation(); // Stops the click from affecting the main link

            const menu = document.getElementById(menuId);
            const arrow = document.getElementById(arrowId);

            if (menu) menu.classList.toggle('show-submenu');
            if (arrow) arrow.classList.toggle('rotate-arrow');
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
