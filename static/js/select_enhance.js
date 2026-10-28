/* select_enhance.js — shared behaviour for every <select> in the system.
   Loaded by academic/base.html, admin/base_admin.html, faculty/base_faculty.html and
   setup/_setup_layout.html, so it covers dropdowns in the page HTML and ones that
   scripts fill in later.

   0. "Select…" prompts can't be picked. An empty option that only asks the user to
      choose ("SELECT", "Select year level", "-SELECT PROGRAM-", "— Select subject —")
      still shows while nothing is chosen, but is not in the list to click. "All …"
      options are real filter choices and stay pickable.
   1. One real choice → pick it. A dropdown sitting on a placeholder ("SELECT",
      "-SELECT PROGRAM-", "ALL SECTIONS", …) whose only other option is a single real
      value selects that value and fires `change`, so the page loads what depends on
      it. Only when the options change (or on page load), never after the user picks
      the placeholder themselves.
   2. Long lists are searchable. A dropdown with many options opens a menu with a
      search box (same look as Schedule Generation's PROGRAM picker) instead of the
      browser's list. The <select> itself stays where it is and keeps its value,
      `change` events and form submission, so page code is unaffected.

   Opt out per dropdown (or any ancestor): data-allow-placeholder, data-no-autoselect,
   data-no-search.
   Force search on a short list: data-searchable. */
(function () {
    'use strict';

    const SEARCH_MIN_OPTIONS = 8;
    const PLACEHOLDER_RE = /^[\s\-–—]*(select|choose|pick|all\b)/i;
    const TIME_RE = /^\s*\d{1,2}:\d{2}/;

    const PROMPT_RE = /^[\s\-–—]*(select|choose|pick)\b/i;
    const isPlaceholder = o => o.value === '' && PLACEHOLDER_RE.test(o.textContent || '');
    const isPrompt = o => o.value === '' && PROMPT_RE.test(o.textContent || '');
    const realOptions = sel => Array.from(sel.options).filter(o => o.value !== '' && !o.disabled && !o.hidden);
    const optedOut = (sel, attr) => !!sel.closest(`[${attr}]`);
    // A <select> hidden by itself backs a page's own custom picker (Generation's PROGRAM,
    // the SIS schedule's PROGRAM): that picker shows the value, so leave it alone. (Its own
    // display ignores hidden ancestors, so dropdowns in a closed modal still count.)
    const backsCustomPicker = sel => getComputedStyle(sel).display === 'none';

    /* ── 0. A "Select…" prompt is shown, never picked ─────────────────── */
    function lockPrompts(sel) {
        if (sel.multiple || optedOut(sel, 'data-allow-placeholder')) return;
        const others = Array.from(sel.options).some(o => !isPrompt(o) && o.defaultSelected);
        Array.from(sel.options).forEach(o => {
            if (!isPrompt(o) || o.disabled) return;
            // Keep it as what a reset form returns to (when nothing else is the default),
            // or a reset would jump to the first real option.
            if (!others && o.selected) o.defaultSelected = true;
            o.disabled = true;
            o.hidden = true;
        });
    }

    /* Hovering a dropdown shows the full selected text (long values get cut off). */
    function syncTitle(sel) {
        if (sel.multiple || (sel.title && !sel.dataset.seTitle)) return;   // keep a page's own title
        const o = sel.options[sel.selectedIndex];
        sel.title = o && o.value ? o.textContent.trim() : '';
        sel.dataset.seTitle = '1';
    }
    document.addEventListener('change', e => { if (e.target.tagName === 'SELECT') syncTitle(e.target); }, true);

    /* ── 1. Auto-select the only real option ───────────────────────────── */
    function autoSelect(sel) {
        if (sel.multiple || sel.disabled || optedOut(sel, 'data-no-autoselect') || backsCustomPicker(sel)) return;
        if (sel.value !== '') return;
        const current = sel.options[sel.selectedIndex];
        if (current && !isPlaceholder(current)) return;      // e.g. "No Designation" is a real choice
        const real = realOptions(sel);
        if (real.length !== 1) return;
        sel.value = real[0].value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
    }

    /* ── 2. Searchable menu for long lists ─────────────────────────────── */
    function wantsSearch(sel) {
        if (sel.multiple || sel.size > 1 || optedOut(sel, 'data-no-search')) return false;
        if (sel.hasAttribute('data-searchable')) return true;
        const real = realOptions(sel);
        if (real.length < SEARCH_MIN_OPTIONS) return false;
        // Time pickers (7:30 AM, 8:00 AM, …) are easier with the plain list.
        return real.filter(o => TIME_RE.test(o.textContent)).length < real.length / 2;
    }

    let menu = null, menuSel = null, activeIdx = -1;

    function injectStyles() {
        if (document.getElementById('seStyles')) return;
        const css = `
        .se-menu { position: fixed; z-index: 10050; background: #fff; border: 1px solid #630100; border-radius: 4px;
            box-shadow: 0 6px 18px rgba(0,0,0,.18); overflow: hidden; font-family: 'Inter', sans-serif;
            min-width: 180px; max-width: min(420px, calc(100vw - 16px)); }
        .se-search-box { padding: 7px 8px; border-bottom: 1px solid #e8e8e8; background: #fafafa; }
        .se-search { width: 100%; box-sizing: border-box; padding: 6px 8px; border: 1px solid #ddd; border-radius: 3px;
            font-size: .78rem; color: #333; outline: none; font-family: inherit; }
        .se-search:focus { border-color: #630100; }
        .se-search::placeholder { color: #bbb; font-style: italic; }
        .se-list { max-height: 240px; overflow-y: auto; }
        .se-opt { padding: 8px 12px; cursor: pointer; font-size: .8rem; color: #333; border-bottom: 1px solid #f0f0f0;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .se-opt:last-child { border-bottom: none; }
        .se-opt:hover, .se-opt.active { background: #fff8f0; color: #630100; }
        .se-opt.selected { background: #fdf0f0; color: #630100; font-weight: 700; }
        .se-opt.disabled { color: #bbb; cursor: not-allowed; background: none; }
        .se-opt strong { color: #630100; font-weight: 900; }
        .se-opt.placeholder { color: #888; font-style: italic; }
        .se-group { padding: 6px 12px 4px; font-size: .68rem; font-weight: 800; color: #888; letter-spacing: .4px;
            text-transform: uppercase; background: #fafafa; }
        .se-empty { padding: 10px 12px; font-size: .78rem; color: #999; font-style: italic; }`;
        const style = document.createElement('style');
        style.id = 'seStyles';
        style.textContent = css;
        document.head.appendChild(style);
    }

    const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

    // "BSIT — Bachelor of …" shows the code in bold, like the Generation picker.
    function optionHtml(text) {
        const m = /^(\S+)\s+([—–-])\s+(.+)$/.exec(text);
        return m ? `<strong>${esc(m[1])}</strong> ${m[2]} ${esc(m[3])}` : esc(text);
    }

    function buildList(filter) {
        const list = menu.querySelector('.se-list');
        const q = (filter || '').trim().toLowerCase();
        let html = '', shown = 0, lastGroup = null;
        Array.from(menuSel.options).forEach((o, i) => {
            if (o.hidden) return;
            const text = o.textContent.trim();
            if (q && !text.toLowerCase().includes(q)) return;
            const group = o.parentElement && o.parentElement.tagName === 'OPTGROUP' ? o.parentElement.label : null;
            if (group !== lastGroup) {
                if (group) html += `<div class="se-group">${esc(group)}</div>`;
                lastGroup = group;
            }
            const cls = ['se-opt'];
            if (i === menuSel.selectedIndex) cls.push('selected');
            if (o.disabled) cls.push('disabled');
            if (isPlaceholder(o)) cls.push('placeholder');
            html += `<div class="${cls.join(' ')}" data-i="${i}" title="${esc(text)}">${optionHtml(text)}</div>`;
            shown++;
        });
        list.innerHTML = shown ? html : '<div class="se-empty">No matches found.</div>';
        const items = Array.from(list.querySelectorAll('.se-opt:not(.disabled)'));
        activeIdx = q ? 0 : items.findIndex(el => el.classList.contains('selected'));
        markActive(items, false);
    }

    function markActive(items, scroll = true) {
        items = items || Array.from(menu.querySelectorAll('.se-opt:not(.disabled)'));
        items.forEach((el, k) => el.classList.toggle('active', k === activeIdx));
        if (scroll && items[activeIdx]) items[activeIdx].scrollIntoView({ block: 'nearest' });
    }

    function position() {
        if (!menu || !menuSel) return;
        const r = menuSel.getBoundingClientRect();
        if (!r.width && !r.height) return;          // not laid out (yet): keep the last position
        menu.style.minWidth = Math.max(180, r.width) + 'px';
        const below = window.innerHeight - r.bottom;
        const h = menu.offsetHeight || 290;
        menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8)) + 'px';
        menu.style.top = (below < h + 8 && r.top > h + 8 ? r.top - h - 4 : r.bottom + 4) + 'px';
    }

    function openMenu(sel) {
        closeMenu();
        injectStyles();
        menuSel = sel;
        menu = document.createElement('div');
        menu.className = 'se-menu';
        menu.setAttribute('role', 'listbox');
        menu.innerHTML = `<div class="se-search-box"><input type="text" class="se-search" placeholder="Search…"
            autocomplete="off" aria-label="Search options"></div><div class="se-list"></div>`;
        document.body.appendChild(menu);
        buildList('');
        position();
        const search = menu.querySelector('.se-search');
        search.addEventListener('input', () => { buildList(search.value); position(); });
        search.addEventListener('keydown', onMenuKey);
        menu.addEventListener('mousedown', e => e.preventDefault());       // keep focus in the search box
        menu.addEventListener('click', e => {
            const item = e.target.closest('.se-opt');
            if (item && !item.classList.contains('disabled')) pick(+item.dataset.i);
        });
        search.focus();
    }

    function closeMenu(refocus) {
        if (!menu) return;
        menu.remove();
        const sel = menuSel;
        menu = null; menuSel = null;
        if (refocus && sel) sel.focus();
    }

    function pick(i) {
        const sel = menuSel;
        closeMenu(true);
        if (!sel || sel.selectedIndex === i) return;
        sel.selectedIndex = i;
        sel.dispatchEvent(new Event('input', { bubbles: true }));
        sel.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function onMenuKey(e) {
        const items = Array.from(menu.querySelectorAll('.se-opt:not(.disabled)'));
        if (e.key === 'ArrowDown') { e.preventDefault(); activeIdx = Math.min(items.length - 1, activeIdx + 1); markActive(items); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); activeIdx = Math.max(0, activeIdx - 1); markActive(items); }
        else if (e.key === 'Enter') { e.preventDefault(); if (items[activeIdx]) pick(+items[activeIdx].dataset.i); }
        else if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); closeMenu(true); }
    }

    // Mouse users get the search menu instead of the browser's list; touch devices keep
    // their native picker (it is already a full-screen list with scrolling).
    const finePointer = !window.matchMedia || window.matchMedia('(pointer: fine)').matches;
    document.addEventListener('mousedown', e => {
        const sel = e.target.closest && e.target.closest('select');
        if (menu && !menu.contains(e.target) && sel !== menuSel) closeMenu();
        if (!sel || !finePointer || sel.disabled || e.button !== 0 || !wantsSearch(sel)) return;
        e.preventDefault();
        if (menuSel === sel) return closeMenu(true);
        sel.focus();
        openMenu(sel);
    }, true);
    document.addEventListener('keydown', e => {
        const sel = e.target;
        if (!sel || sel.tagName !== 'SELECT' || sel.disabled || !wantsSearch(sel)) return;
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'F4' || (e.altKey && e.key === 'ArrowDown')) {
            e.preventDefault();
            openMenu(sel);
        }
    }, true);
    window.addEventListener('resize', () => closeMenu());
    document.addEventListener('scroll', e => { if (menu && !menu.contains(e.target)) position(); }, true);

    /* ── Watch for dropdowns being filled or added ─────────────────────── */
    const pending = new Set();
    let scheduled = false;
    function queue(sel) {
        pending.add(sel);
        if (scheduled) return;
        scheduled = true;
        // After the page's own code finishes filling the list (it may set a value itself).
        setTimeout(() => {
            scheduled = false;
            const sels = Array.from(pending); pending.clear();
            sels.forEach(s => { if (s.isConnected) { lockPrompts(s); autoSelect(s); syncTitle(s); } });
            if (menuSel && sels.includes(menuSel)) {
                buildList(menu.querySelector('.se-search').value);
                position();
            }
        }, 0);
    }

    function start() {
        document.querySelectorAll('select').forEach(queue);
        new MutationObserver(muts => {
            muts.forEach(m => {
                const t = m.target;
                if (t.tagName === 'SELECT') queue(t);
                else if (t.tagName === 'OPTGROUP' && t.parentElement) queue(t.parentElement);
                m.addedNodes.forEach(n => {
                    if (n.nodeType !== 1) return;
                    if (n.tagName === 'SELECT') queue(n);
                    else if (n.querySelectorAll) n.querySelectorAll('select').forEach(queue);
                });
            });
        }).observe(document.body, { childList: true, subtree: true });
    }

    // After the pages' own DOMContentLoaded code has filled and wired their dropdowns.
    if (document.readyState === 'complete') start();
    else window.addEventListener('load', start);

    window.SelectEnhance = { autoSelect, lockPrompts, openMenu, closeMenu };
})();
