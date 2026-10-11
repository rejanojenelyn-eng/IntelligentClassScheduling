// ═══════════════════════════════════════════════════════
// RESPONSIVE LAYER — pairs with css/responsive.css.
// Loaded by the three base layouts after their own base.*.js.
//  • ≤ 1024px: the ☰ button opens the sidebar as a slide-out drawer
//    instead of the desktop "collapse to icons" toggle.
//  • ≤ 768px: the global search moves from the header into the toggle bar.
//  • ≤ 1024px: tables wider than the screen get a horizontal scroller.
// ═══════════════════════════════════════════════════════
(function () {
    const TABLET = window.matchMedia('(max-width: 1024px)');
    const PHONE  = window.matchMedia('(max-width: 768px)');

    document.addEventListener('DOMContentLoaded', function () {
        const sidebar   = document.getElementById('sidebar');
        const toggleBtn = document.getElementById('toggleBtn');
        if (!sidebar || !toggleBtn) return;

        // ── Drawer ──
        const backdrop = document.createElement('div');
        backdrop.className = 'rsp-backdrop';
        document.body.appendChild(backdrop);

        function setDrawer(open) {
            sidebar.classList.toggle('rsp-open', open);
            backdrop.classList.toggle('show', open);
            document.body.classList.toggle('rsp-drawer-open', open);
        }

        // Capture phase runs before base.*.js's onclick, so on small screens the
        // desktop collapse toggle (and its saved localStorage state) is left alone.
        document.addEventListener('click', function (e) {
            if (!TABLET.matches || !toggleBtn.contains(e.target)) return;
            e.stopPropagation();
            setDrawer(!sidebar.classList.contains('rsp-open'));
        }, true);

        backdrop.addEventListener('click', () => setDrawer(false));
        document.addEventListener('keydown', e => { if (e.key === 'Escape') setDrawer(false); });
        sidebar.addEventListener('click', function (e) {
            const link = e.target.closest('a[href]');
            if (link && TABLET.matches && !link.getAttribute('href').startsWith('javascript')) setDrawer(false);
        });
        TABLET.addEventListener('change', () => setDrawer(false));

        // ── Search: header on desktop, toggle bar on phones ──
        const search    = document.getElementById('globalSearchWrap');
        const subHeader = document.querySelector('.sub-header');
        const searchHome = search && search.parentNode, searchNext = search && search.nextSibling;
        function placeSearch() {
            if (!search || !subHeader) return;
            if (PHONE.matches) { if (search.parentNode !== subHeader) subHeader.appendChild(search); }
            else if (search.parentNode !== searchHome) searchHome.insertBefore(search, searchNext);
        }
        placeSearch();
        PHONE.addEventListener('change', placeSearch);

        // ── Wide tables scroll sideways instead of stretching the page ──
        const content = document.querySelector('.content-body');
        if (!content) return;

        function hasScrollingAncestor(el) {
            for (let p = el.parentElement; p && p !== content; p = p.parentElement) {
                const ox = getComputedStyle(p).overflowX;
                if (ox === 'auto' || ox === 'scroll') return true;
            }
            return false;
        }
        function wrapTables() {
            if (!TABLET.matches) return;
            const limit = content.clientWidth;
            content.querySelectorAll('table').forEach(function (t) {
                if (t.parentElement.classList.contains('rsp-table-wrap')) return;
                if (t.offsetParent === null || t.offsetWidth <= limit) return;
                if (hasScrollingAncestor(t)) return;
                const wrap = document.createElement('div');
                wrap.className = 'rsp-table-wrap';
                t.parentNode.insertBefore(wrap, t);
                wrap.appendChild(t);
            });
        }
        let timer = null;
        const schedule = () => { clearTimeout(timer); timer = setTimeout(wrapTables, 150); };
        new MutationObserver(schedule).observe(content, { childList: true, subtree: true });
        window.addEventListener('resize', schedule);
        schedule();
    });
})();
