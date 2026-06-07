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
           document.addEventListener("DOMContentLoaded", function() {
        const toasts = document.querySelectorAll('.flash-toast');
        
        toasts.forEach(toast => {
            // Maghintay ng 5 seconds bago simulan ang pag-fade out
            setTimeout(() => {
                toast.style.transition = "opacity 0.5s ease, transform 0.5s ease";
                toast.style.opacity = '0';
                toast.style.transform = 'translateX(20px)'; // Konting move pakanan habang nagfe-fade
                
                // Tuluyan nang tanggalin sa HTML pagkatapos ng animation
                setTimeout(() => {
                    toast.remove();
                }, 500);
            }, 5000);
        });
    });
