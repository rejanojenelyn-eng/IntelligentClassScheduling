/* Multi-select dropdown for long checkbox lists (Reports filter modals).

   Mark a checkbox list with data-multi-dd="<placeholder>" and call MultiDD.initAll(root).
   The list's own checkboxes are kept and simply moved into a dropdown panel, so every
   existing filter / preview / export reads them exactly as before (".cs-prog-cb:checked").
   The trigger shows what is chosen ("BSIT, BEED", "3 selected", or the placeholder); the
   panel has a search box (name, or a code such as BSIT — spaces/dashes ignored) and
   Select all / Clear. Esc or a click outside closes it.

   Code that ticks/unticks the checkboxes itself (a reset) calls MultiDD.refreshAll(). */
(function () {
    var CSS =
        '.mdd{position:relative;}' +
        '.mdd-trigger{width:100%;display:flex;align-items:center;gap:8px;padding:9px 12px;background:#fff;' +
        'border:1.5px solid #e3e3e3;border-radius:8px;cursor:pointer;font:inherit;font-size:.8rem;color:#333;text-align:left;}' +
        '.mdd-trigger:hover,.mdd.open .mdd-trigger{border-color:#800000;}' +
        '.mdd-trigger:focus-visible{outline:2px solid #800000;outline-offset:1px;}' +
        '.mdd-value{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}' +
        '.mdd-value.placeholder{color:#999;}' +
        '.mdd-count{background:#800000;color:#fff;border-radius:10px;padding:1px 7px;font-size:.68rem;font-weight:700;}' +
        '.mdd-caret{color:#888;font-size:.75rem;transition:transform .15s;}' +
        '.mdd.open .mdd-caret{transform:rotate(180deg);}' +
        '.mdd-panel{display:none;position:absolute;left:0;right:0;top:calc(100% + 4px);z-index:60;background:#fff;' +
        'border:1px solid #e3d6d6;border-radius:10px;box-shadow:0 10px 28px rgba(0,0,0,.16);padding:8px;}' +
        '.mdd.open .mdd-panel{display:block;}' +
        '.mdd-search{display:flex;align-items:center;gap:8px;padding:7px 10px;border:1.5px solid #eee;border-radius:7px;margin-bottom:6px;}' +
        '.mdd-search:focus-within{border-color:#800000;}' +
        '.mdd-search i{color:#999;font-size:.78rem;}' +
        '.mdd-search input{flex:1;min-width:0;border:0;outline:0;font-size:.8rem;background:transparent;}' +
        '.mdd-actions{display:flex;justify-content:space-between;align-items:center;padding:2px 4px 6px;font-size:.72rem;color:#888;}' +
        '.mdd-actions button{border:0;background:none;color:#800000;font-weight:700;cursor:pointer;font-size:.72rem;padding:2px 4px;}' +
        '.mdd-panel .mdd-list{display:block !important;max-height:240px !important;overflow-y:auto;border:0 !important;padding:0 !important;margin:0;}' +
        '.mdd-panel .mdd-list > *{display:flex;width:auto;margin:0 0 2px !important;border-radius:6px;}' +
        '.mdd-panel .mdd-list > .mdd-hide{display:none !important;}' +
        '.mdd-none{display:none;font-size:.76rem;color:#999;padding:8px 6px;}';

    function ensureStyle() {
        if (document.getElementById('mdd-style')) return;
        var st = document.createElement('style');
        st.id = 'mdd-style'; st.textContent = CSS;
        document.head.appendChild(st);
    }

    function norm(t) { return String(t || '').toLowerCase().replace(/[^a-z0-9]/g, ''); }
    function items(list) {
        return Array.prototype.filter.call(list.children, function (el) {
            return el.querySelector && el.querySelector('input[type=checkbox]');
        });
    }
    // The item's own name, without trailing details such as "· 10043 · Regular".
    function labelOf(item) {
        if (item.dataset.label) return item.dataset.label;
        return (item.textContent || '').replace(/\s+/g, ' ').trim().split(' · ')[0];
    }

    function refresh(wrap) {
        var list = wrap._mddList, out = wrap.querySelector('.mdd-value'), cnt = wrap.querySelector('.mdd-count');
        var chosen = items(list).filter(function (it) { return it.querySelector('input[type=checkbox]').checked; })
                                .map(labelOf);
        if (!chosen.length) {
            out.textContent = wrap._mddPlaceholder; out.classList.add('placeholder'); out.title = '';
            cnt.style.display = 'none';
        } else {
            out.textContent = chosen.length <= 2 ? chosen.join(', ') : chosen.slice(0, 2).join(', ') + ' +' + (chosen.length - 2) + ' more';
            out.title = chosen.join('\n');
            out.classList.remove('placeholder');
            cnt.textContent = chosen.length; cnt.style.display = '';
        }
    }

    function filter(wrap) {
        var q = (wrap.querySelector('.mdd-search input') || {}).value || '';
        var words = q.toLowerCase().split(/\s+/).filter(Boolean), qn = norm(q), shown = 0;
        items(wrap._mddList).forEach(function (it) {
            var cb = it.querySelector('input[type=checkbox]');
            var text = (it.dataset.search || it.textContent || '').toLowerCase();
            var ok = !q || words.every(function (w) { return text.indexOf(w) !== -1; }) ||
                     (qn && norm(it.dataset.code || cb.value).indexOf(qn) !== -1);
            it.classList.toggle('mdd-hide', !ok);
            if (ok && it.style.display !== 'none') shown++;
        });
        wrap.querySelector('.mdd-none').style.display = shown ? 'none' : 'block';
    }

    function closeAll(except) {
        document.querySelectorAll('.mdd.open').forEach(function (w) { if (w !== except) w.classList.remove('open'); });
    }

    function init(list) {
        if (!list || list._mddWrap) return;
        ensureStyle();
        var wrap = document.createElement('div');
        wrap.className = 'mdd';
        wrap._mddList = list;
        wrap._mddPlaceholder = list.getAttribute('data-multi-dd') || 'Select…';
        list._mddWrap = wrap;
        list.parentNode.insertBefore(wrap, list);
        wrap.innerHTML =
            '<button type="button" class="mdd-trigger" aria-haspopup="listbox" aria-expanded="false">' +
                '<span class="mdd-value placeholder"></span><span class="mdd-count" style="display:none"></span>' +
                '<i class="fas fa-chevron-down mdd-caret"></i></button>' +
            '<div class="mdd-panel">' +
                '<label class="mdd-search"><i class="fas fa-magnifying-glass"></i>' +
                    '<input type="text" placeholder="Search…" autocomplete="off"></label>' +
                '<div class="mdd-actions"><span class="mdd-shown"></span><span>' +
                    '<button type="button" data-act="all">Select all</button>' +
                    '<button type="button" data-act="none">Clear</button></span></div>' +
                '<div class="mdd-none">No matches.</div>' +
            '</div>';
        var panel = wrap.querySelector('.mdd-panel'), trig = wrap.querySelector('.mdd-trigger');
        list.classList.add('mdd-list');
        panel.insertBefore(list, wrap.querySelector('.mdd-none'));
        if (items(list).length <= 6) wrap.querySelector('.mdd-search').style.display = 'none';

        trig.addEventListener('click', function (e) {
            e.preventDefault(); e.stopPropagation();
            var open = !wrap.classList.contains('open');
            closeAll(wrap);
            wrap.classList.toggle('open', open);
            trig.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (open) {
                var s = wrap.querySelector('.mdd-search input');
                s.value = ''; filter(wrap);
                if (wrap.querySelector('.mdd-search').style.display !== 'none') setTimeout(function () { s.focus(); }, 0);
            }
            refresh(wrap);
        });
        panel.addEventListener('click', function (e) { e.stopPropagation(); });
        wrap.querySelector('.mdd-search input').addEventListener('input', function () { filter(wrap); });
        wrap.querySelector('.mdd-search input').addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {            // tick the first visible match
                e.preventDefault();
                var first = items(list).filter(function (it) {
                    return !it.classList.contains('mdd-hide') && it.style.display !== 'none';
                })[0];
                var cb = first && first.querySelector('input[type=checkbox]');
                if (cb && !cb.checked) { cb.checked = true; cb.dispatchEvent(new Event('change', { bubbles: true })); }
                this.select();
            }
        });
        wrap.querySelectorAll('.mdd-actions button').forEach(function (b) {
            b.addEventListener('click', function () {
                var on = b.getAttribute('data-act') === 'all';
                items(list).forEach(function (it) {
                    if (on && (it.classList.contains('mdd-hide') || it.style.display === 'none')) return;  // only what is shown
                    var cb = it.querySelector('input[type=checkbox]');
                    if (cb.checked !== on) { cb.checked = on; cb.dispatchEvent(new Event('change', { bubbles: true })); }
                });
                refresh(wrap);
            });
        });
        list.addEventListener('change', function () { refresh(wrap); });
        // Items that toggle on a click of the row (not only the checkbox) report it here too.
        list.addEventListener('click', function () { setTimeout(function () { refresh(wrap); }, 0); });
        refresh(wrap);
    }

    document.addEventListener('click', function () { closeAll(); });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && document.querySelector('.mdd.open')) { e.stopPropagation(); closeAll(); }
    }, true);

    window.MultiDD = {
        init: init,
        initAll: function (root) { (root || document).querySelectorAll('[data-multi-dd]').forEach(init); },
        refreshAll: function () {
            document.querySelectorAll('.mdd').forEach(function (w) { refresh(w); if (w.classList.contains('open')) filter(w); });
        },
    };
})();
