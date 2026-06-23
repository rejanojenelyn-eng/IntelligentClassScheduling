document.addEventListener('DOMContentLoaded', function() {
            // Sidebar toggle for collapsing the whole sidebar
            const sidebar = document.getElementById("sidebar");
            const toggleBtn = document.getElementById("toggleBtn");
            if(toggleBtn) {
                toggleBtn.onclick = () => { sidebar.classList.toggle("collapsed"); };
            }
        });

        // Modal functions
        function openLogoutModal(e) {
            if (e) e.preventDefault();
            if (typeof closeUpDropdown === 'function') closeUpDropdown();
            document.getElementById("logoutModal").style.display = "flex";
        }
        function closeLogoutModal() {
            document.getElementById("logoutModal").style.display = "none";
        }
        function proceedLogout() {
            window.location.href = "/logout";
        }
