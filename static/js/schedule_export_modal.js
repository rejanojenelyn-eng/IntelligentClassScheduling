(function () {
    const CFG = Object.assign({
        countUrl:  '/academic/schedule/export/count',
        exportUrl: '/academic/schedule/export',
    }, window.SE_EXPORT_CONFIG || {});
    const FMTS = ['pdf','docx','xlsx','csv'];
    let _selFmts = new Set();
    let _countTmr = null;


    /* ── Open / Close ── */
    window.openSchedExport = () => {
        _selFmts.clear();
        document.querySelectorAll('.se-ay-cb,.se-sem-cb,.se-yl-cb,.se-prog-cb').forEach(cb => cb.checked = cb.classList.contains('se-fixed-cb'));
        document.getElementById('seProgSearch').value = '';
        seFilterProgs();


        /* Pre-apply the filters currently active on the schedule page (Acad Year,
           Semester, Program, Year Level) so Export defaults to the filtered view. */
        const active = _activePageFilters();
        let appliedAny = false;
        if (document.querySelector('.se-fixed-cb')) appliedAny = true;   // active term always applies
        if (active.ay) {
            const cb = document.querySelector(`.se-ay-cb[value="${CSS.escape(active.ay)}"]`);
            if (cb) { cb.checked = true; appliedAny = true; }
        }
        if (active.sem) {
            const cb = document.querySelector(`.se-sem-cb[value="${CSS.escape(active.sem)}"]`);
            if (cb) { cb.checked = true; appliedAny = true; }
        }
        if (active.prog) {
            const cb = document.querySelector(`.se-prog-cb[value="${CSS.escape(active.prog)}"]`);
            if (cb) { cb.checked = true; appliedAny = true; }
        }
        if (active.yl) {
            const cb = document.querySelector(`.se-yl-cb[value="${CSS.escape(active.yl)}"]`);
            if (cb) { cb.checked = true; appliedAny = true; }
        }


        FMTS.forEach(f => {
            const card = document.getElementById('seFmt' + f[0].toUpperCase() + f.slice(1));
            if (card) card.classList.remove('sel');
        });
        document.getElementById('seFilename').value = 'schedule_export';
        document.getElementById('seCountRows').textContent = '—';
        document.getElementById('seCountProgs').textContent = '—';
        document.getElementById('seSumMsg').textContent = appliedAny
            ? 'Filters applied from the current schedule view. Select a format to begin.'
            : 'Apply filters and select a format to begin.';
        document.getElementById('seBtnExport').disabled = true;
        document.getElementById('seBtnLbl').textContent = 'Export';
        document.getElementById('seLoading').classList.remove('active');
        document.getElementById('seModal').style.display = 'flex';
        document.getElementById('seLayoutModal').style.display = 'none';


        if (appliedAny) seUpdateCount();
    };


    function _activePageFilters() {
        return {
            ay:   document.getElementById('view_ay')?.value || '',
            sem:  document.getElementById('view_sem')?.value || '',
            prog: document.getElementById('view_prog')?.value || '',
            yl:   document.getElementById('view_yl')?.value || ''
        };
    }
    window.seClose = () => { document.getElementById('seModal').style.display = 'none'; };


    /* ── Format toggle ── */
    window.seToggleFmt = (fmt) => {
        _selFmts.has(fmt) ? _selFmts.delete(fmt) : _selFmts.add(fmt);
        FMTS.forEach(f => {
            const card = document.getElementById('seFmt' + f[0].toUpperCase() + f.slice(1));
            if (card) card.classList.toggle('sel', _selFmts.has(f));
        });
        _updateBtn();
    };


    /* ── Program search ── */
    window.seFilterProgs = () => {
        const q = (document.getElementById('seProgSearch').value || '').toLowerCase();
        document.querySelectorAll('.se-prog-item').forEach(item => {
            const match = !q || item.dataset.prog.toLowerCase().includes(q) ||
                          (item.querySelector('label')?.textContent || '').toLowerCase().includes(q);
            item.style.display = match ? '' : 'none';
        });
    };
    window.seSelectAllProgs = (checked) => {
        document.querySelectorAll('.se-prog-item:not([style*="display: none"]) .se-prog-cb').forEach(cb => cb.checked = checked);
        seUpdateCount();
    };
    window.seSelectAllYL = (checked) => {
        document.querySelectorAll('.se-yl-cb').forEach(cb => cb.checked = checked);
        seUpdateCount();
    };


    /* ── Get filters ── */
    function _filters() {
        return {
            ay_ids:      [...document.querySelectorAll('.se-ay-cb:checked')].map(c => c.value),
            sem_types:   [...document.querySelectorAll('.se-sem-cb:checked')].map(c => c.value),
            programs:    [...document.querySelectorAll('.se-prog-cb:checked')].map(c => c.value),
            year_levels: [...document.querySelectorAll('.se-yl-cb:checked')].map(c => parseInt(c.value))
        };
    }


    /* ── Count-up animation ── */
    function _countUp(el, target, ms) {
        if (target <= 0) { el.textContent = 0; return; }
        const steps = Math.min(40, target);
        const interval = Math.max(16, Math.round(ms / steps));
        let cur = 0;
        const tmr = setInterval(() => {
            cur = Math.min(cur + Math.ceil(target / steps), target);
            el.textContent = cur;
            if (cur >= target) clearInterval(tmr);
        }, interval);
    }


    /* ── Count (debounced) ── */
    window.seUpdateCount = () => { clearTimeout(_countTmr); _countTmr = setTimeout(_fetchCount, 420); };


    async function _fetchCount() {
        const f = _filters();
        const summary = document.querySelector('.se-summary');
        const hasAny = f.ay_ids.length || f.sem_types.length || f.programs.length || f.year_levels.length;
        if (!hasAny) {
            summary.classList.remove('counting');
            document.getElementById('seCountRows').textContent  = '—';
            document.getElementById('seCountProgs').textContent = '—';
            document.getElementById('seSumMsg').textContent = 'Apply filters and select a format to begin.';
            _updateBtn(); return;
        }
        // Pulsing counting state
        summary.classList.add('counting');
        document.getElementById('seCountRows').textContent  = '•••';
        document.getElementById('seCountProgs').textContent = '•••';
        document.getElementById('seSumMsg').textContent = 'Calculating…';
        try {
            const resp = await fetch(CFG.countUrl, {
                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(f)
            });
            const data = await resp.json();
            summary.classList.remove('counting');
            if (data.error) { document.getElementById('seSumMsg').textContent = 'Error: ' + data.error; return; }
            _countUp(document.getElementById('seCountRows'),  data.count    || 0, 600);
            _countUp(document.getElementById('seCountProgs'), data.programs || 0, 400);
            document.getElementById('seSumMsg').textContent = data.count
                ? `${data.count} row(s) across ${data.programs} program(s) ready for export.`
                : 'No matching schedule data found for the selected filters.';
            document.getElementById('seFilename').value = _genFilename();
            _updateBtn();
        } catch(e) {
            summary.classList.remove('counting');
            document.getElementById('seSumMsg').textContent = 'Network error — check server connection.';
        }
    }


    /* ── Update export button ── */
    function _updateBtn() {
        const f       = _filters();
        const count   = parseInt(document.getElementById('seCountRows').textContent) || 0;
        const hasFmt  = _selFmts.size > 0;
        const hasFilter = f.ay_ids.length || f.sem_types.length || f.programs.length || f.year_levels.length;
        const ok      = hasFmt && hasFilter && count > 0;
        const btn     = document.getElementById('seBtnExport');
        btn.disabled  = !ok;
        if (ok) {
            const fmtStr = [..._selFmts].map(s => s.toUpperCase()).join(' + ');
            document.getElementById('seBtnLbl').textContent = 'Export as ' + fmtStr;
        } else {
            document.getElementById('seBtnLbl').textContent = 'Export';
        }
    }


    /* ── Auto-generate filename ── */
    function _genFilename() {
        const sems = [...document.querySelectorAll('.se-sem-cb:checked')].map(c => ({'A':'1stSem','B':'2ndSem','C':'Summer'})[c.value] || c.value);
        const progs = [...document.querySelectorAll('.se-prog-cb:checked')].map(c => c.value);
        const ayCbs = [...document.querySelectorAll('.se-ay-cb:checked')];
        let ayLbl = '';
        if (ayCbs.length === 1) {
            const txt = ayCbs[0].closest('.se-cb-item')?.textContent?.trim() || '';
            ayLbl = txt.replace(/^AY\s*/,'').replace(/\s+/g,'');
        } else if (ayCbs.length > 1) {
            ayLbl = 'MultiAY';
        }
        const parts = ['schedule'];
        if (progs.length === 1) parts.push(progs[0]);
        else if (progs.length > 1) parts.push('MultiProg');
        if (sems.length === 1) parts.push(sems[0]);
        if (ayLbl) parts.push(ayLbl);
        return parts.join('_');
    }


    /* ── Do export ──
       PDF/DOCX/XLSX support a Layout choice (Table View vs Calendar View);
       CSV is always flat data, so it skips the layout picker entirely. */
    window.seDoExport = async () => {
        const formats = [..._selFmts];
        if (!formats.length) { alert('Please select at least one export format.'); return; }
        const needsLayout = formats.some(f => f === 'pdf' || f === 'docx' || f === 'xlsx');
        if (needsLayout) {
            document.getElementById('seLayoutModal').style.display = 'flex';
        } else {
            _seRunExport('table');
        }
    };


    window.seCloseLayout = () => { document.getElementById('seLayoutModal').style.display = 'none'; };
    window.seChooseLayout = (layout) => {
        document.getElementById('seLayoutModal').style.display = 'none';
        _seRunExport(layout);
    };


    async function _seRunExport(layout) {
        const formats  = [..._selFmts];
        const filename = document.getElementById('seFilename').value.trim() || 'schedule_export';


        const loadEl  = document.getElementById('seLoading');
        const loadLbl = document.getElementById('seLoadLbl');
        const STEPS   = ['Fetching schedule data…','Building document…','Applying formatting…','Finalizing file…'];
        let stepIdx   = 0;
        loadLbl.textContent = STEPS[0];
        loadEl.classList.add('active');
        const stepTmr = setInterval(() => {
            stepIdx = Math.min(stepIdx + 1, STEPS.length - 1);
            loadLbl.textContent = STEPS[stepIdx];
        }, 900);


        try {
            const resp = await fetch(CFG.exportUrl, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ ..._filters(), formats, filename, layout })
            });
            clearInterval(stepTmr);
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                alert('Export failed: ' + (err.error || resp.statusText));
                return;
            }
            loadLbl.textContent = 'Download starting…';
            const blob = await resp.blob();
            const ext  = formats.length > 1 ? '.zip' : '.' + formats[0];
            const url  = URL.createObjectURL(blob);
            const a    = document.createElement('a');
            a.href = url; a.download = filename + ext; a.click();
            URL.revokeObjectURL(url);
            seClose();
        } catch(e) {
            clearInterval(stepTmr);
            alert('Network error: ' + e.message);
        } finally {
            loadEl.classList.remove('active');
        }
    }
})();

