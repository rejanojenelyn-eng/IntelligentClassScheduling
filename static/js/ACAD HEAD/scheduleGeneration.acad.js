// MANUAL_EDITOR_URL is defined inline in the HTML template above this script

// Canonical day sort order (Mon=0 … Sun=6) — used to normalise display across all views
const _DAY_SORT_ORDER = {
    'MON':0,'TUE':1,'WED':2,'THU':3,'FRI':4,'SAT':5,'SUN':6,
    'Monday':0,'Tuesday':1,'Wednesday':2,'Thursday':3,'Friday':4,'Saturday':5,'Sunday':6
};
function _sortDays(arr) {
    return [...arr].sort((a, b) => (_DAY_SORT_ORDER[a.trim()] ?? 99) - (_DAY_SORT_ORDER[b.trim()] ?? 99));
}

document.addEventListener('DOMContentLoaded', () => {
    let currentScheduleData = [];
    let currentBatchId      = null;
    let canPublish          = false;
    // The single shared evaluation result object — same object backs the panel UI,
    // row-conflict highlighting, exports, and Approve-button gating.
    let currentEvaluation   = null;
    // True from the moment a Generate/Regenerate/Retrieve result is on screen
    // until it's saved as Draft, Published, or handed off to Manual Editor —
    // guards both switching filters mid-page and leaving/closing the tab.
    let _hasUnsavedGenerated  = false;
    let _leavingIntentionally = false;

    // ── Selective regeneration: which rows are checked, and which of their
    // fields are marked "preserve" (see renderTable() and btnRegenerate below).
    // Matches the ORIGINAL table grouping (subject + faculty) rather than
    // splitting a subject's Lecture/Lab into separate rows, so a subject
    // taught by one instructor still shows as one row with one checkbox —
    // that checkbox's selected/preserve state is shared by every underlying
    // gene (Lecture and/or Lab) it represents when the "Re-generate selected"
    // payload is built. Trade-off: if a regenerate changes the instructor for
    // a subject that was left unchecked, its key changes too, so a *previous*
    // selection on that subject (if any) won't carry forward — acceptable,
    // since an unchecked row is meant to stay untouched anyway.
    const rowSelectionState = new Map(); // rowKey -> { selected, preserve: {faculty, room, schedule} }
    function rowKeyOf(cls) {
        return (cls.subject_code || '') + '||' + (cls.faculty_id || cls.instructor || '');
    }

    const acadYear      = document.getElementById('acadYear');
    const term          = document.getElementById('term');
    const program       = document.getElementById('program');
    const yearLevel     = document.getElementById('yearLevel');
    const sectionFilter = document.getElementById('sectionFilter');
    const curriculum    = document.getElementById('curriculum');
    const curriculumText = document.getElementById('curriculumText');
    const useHistorical  = document.getElementById('useHistorical');

    const btnGenerate      = document.getElementById('btnGenerate');
    const btnRegenerate    = document.getElementById('btnRegenerate');
    const btnSaveDraft     = document.getElementById('btnSaveDraft');
    const btnApprove       = document.getElementById('btnApprove');
    const btnManualEditor  = document.getElementById('btnManualEditor');
    const btnExport        = document.getElementById('btnExport');
    const btnCalendarView  = document.getElementById('btnCalendarView');
    const btnTableView     = document.getElementById('btnTableView');
    const sortSelect       = document.getElementById('sortSelect');
    const btnSelectIncomplete = document.getElementById('btnSelectIncomplete');

    // ── Custom searchable program dropdown ───────────────────────────────────
    const genProgWrapper     = document.getElementById('genProgWrapper');
    const genProgTrigger     = document.getElementById('genProgTrigger');
    const genProgTriggerText = document.getElementById('genProgTriggerText');
    const genProgSearch      = document.getElementById('genProgSearch');
    const genProgList        = document.getElementById('genProgList');

    function openProgDropdown() {
        genProgWrapper.classList.add('open');
        genProgSearch.value = '';
        filterProgOptions('');
        genProgSearch.focus();
    }
    function closeProgDropdown() {
        genProgWrapper.classList.remove('open');
    }

    genProgTrigger.addEventListener('click', (e) => {
        e.stopPropagation();
        genProgWrapper.classList.contains('open') ? closeProgDropdown() : openProgDropdown();
    });

    genProgSearch.addEventListener('click', e => e.stopPropagation());

    genProgSearch.addEventListener('input', function() {
        filterProgOptions(this.value.trim().toLowerCase());
    });

    function filterProgOptions(q) {
        genProgList.querySelectorAll('.gen-prog-option').forEach(opt => {
            const code = (opt.dataset.value || '').toLowerCase();
            const name = (opt.dataset.name  || '').toLowerCase();
            opt.style.display = (!q || code.includes(q) || name.includes(q)) ? '' : 'none';
        });
    }

    genProgList.addEventListener('click', function(e) {
        const opt = e.target.closest('.gen-prog-option');
        if (!opt) return;
        const val  = opt.dataset.value;
        const name = opt.dataset.name;
        program.value = val;
        genProgTriggerText.textContent = val ? `${val} – ${name}` : '-SELECT PROGRAM-';
        closeProgDropdown();
        program.dispatchEvent(new Event('change'));
    });

    document.addEventListener('click', (e) => {
        if (genProgWrapper && !genProgWrapper.contains(e.target)) closeProgDropdown();
    });
    // ─────────────────────────────────────────────────────────────────────────

    async function loadSections() {
        const prog = program.value;
        const yl   = yearLevel.value;
        const ay   = acadYear.value;
        const sem  = term.value;
        sectionFilter.innerHTML = '<option value="">SELECT</option>';
        if (!prog || !yl) { checkFormValidity(); return; }
        sectionFilter.innerHTML = '<option value="">Loading...</option>';
        try {
            const params = new URLSearchParams({ program: prog, yearLevel: yl });
            if (ay)  params.set('ay', ay);
            if (sem) params.set('semester', sem);
            const res  = await fetch(`/api/sections-by-program?${params}`);
            const data = await res.json();
            sectionFilter.innerHTML = '<option value="">SELECT</option>';
            (data.sections || []).forEach(sec => {
                const opt = document.createElement('option');
                opt.value = sec.id;
                opt.textContent = sec.name;
                sectionFilter.appendChild(opt);
            });
            // #6: Never auto-select a section; the user must choose manually.
        } catch (e) {
            sectionFilter.innerHTML = '<option value="">SELECT</option>';
        }
        checkFormValidity();
    }

    async function loadYearLevels(programValue) {
        yearLevel.innerHTML = '<option value="">Loading...</option>';
        yearLevel.disabled  = true;
        sectionFilter.innerHTML = '<option value="">SELECT</option>';
        curriculumText.textContent = "Select Year Level";
        curriculum.value = "";
        try {
            const res  = await fetch(`/api/year-levels-by-program?program=${encodeURIComponent(programValue)}`);
            const data = await res.json();
            const levels = data.year_levels || [1, 2, 3, 4];
            yearLevel.innerHTML = '<option value="">SELECT</option>';
            levels.forEach(lvl => {
                const opt = document.createElement('option');
                opt.value       = lvl;
                opt.textContent = String(lvl);
                yearLevel.appendChild(opt);
            });
        } catch (e) {
            yearLevel.innerHTML = '<option value="">SELECT</option>';
            [1, 2, 3, 4].forEach(lvl => {
                const opt = document.createElement('option');
                opt.value = lvl; opt.textContent = String(lvl);
                yearLevel.appendChild(opt);
            });
        } finally {
            yearLevel.disabled = false;
        }
        checkFormValidity();
    }

    // ── Unsaved-generated-schedule guard ─────────────────────────────────────
    // Switching AY/Term/Program/Year Level/Section after a Generate/Regenerate
    // abandons the on-screen (unsaved) result, so confirm first — same idea as
    // the "Unsaved Changes" prompt in Manual Editor when switching subjects.
    // Registered before the real change handlers below so, for the same
    // element, this one runs first and can stopImmediatePropagation() them
    // when the user cancels.
    function _syncProgramTriggerText(val) {
        if (!genProgTriggerText) return;
        const selOpt = program.querySelector(`option[value="${CSS.escape(val || '')}"]`);
        const progName = selOpt ? (selOpt.dataset.name || selOpt.textContent.split('–')[1]?.trim() || '') : '';
        genProgTriggerText.textContent = val ? (progName ? `${val} – ${progName}` : val) : '-SELECT PROGRAM-';
    }

    const _guardedFilters = [acadYear, term, program, yearLevel, sectionFilter];
    const _prevFilterValue = new Map(_guardedFilters.map(el => [el, el.value]));

    _guardedFilters.forEach(el => {
        el.addEventListener('change', function(e) {
            const newValue = el.value;
            if (!_hasUnsavedGenerated) {
                _prevFilterValue.set(el, newValue);
                return;
            }
            const oldValue = _prevFilterValue.get(el) || '';
            if (newValue === oldValue) return;

            e.stopImmediatePropagation();
            el.value = oldValue;
            if (el === program) _syncProgramTriggerText(oldValue);

            showInfo(
                'Unsaved Changes',
                'You have an unsaved generated schedule.<br>Switch selection and discard it?',
                'confirm'
            ).then(ok => {
                if (!ok) return;
                _discardUnsavedGenerated();
                _prevFilterValue.set(el, newValue);
                el.value = newValue;
                if (el === program) _syncProgramTriggerText(newValue);
                el.dispatchEvent(new Event('change'));
            });
        });
    });

    program.addEventListener('change', async function() {
        const programValue = this.value;
        if (!programValue) {
            curriculumText.textContent = "Select Program first";
            curriculum.value = "";
            yearLevel.innerHTML = '<option value="">SELECT</option>';
            sectionFilter.innerHTML = '<option value="">SELECT</option>';
            checkFormValidity();
            return;
        }
        await loadYearLevels(programValue);
    });

    function checkFormValidity() {
        const allFilled = acadYear.value && term.value && program.value && yearLevel.value && sectionFilter.value && curriculum.value;
        btnGenerate.disabled = !allFilled;
    }

    yearLevel.addEventListener('change', async function() {
        await loadSections();
        // Re-fetch curriculum now that both program + year level are known
        const prog = program.value;
        const yl   = yearLevel.value;
        const ay   = acadYear.value;
        if (prog && yl) {
            curriculumText.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
            try {
                const params = new URLSearchParams({ program: prog, year_level: yl });
                if (ay) params.set('acad_year', ay);
                const res  = await fetch(`/api/curriculum-by-year?${params}`);
                const data = await res.json();
                if (data.curriculum) {
                    curriculum.value = data.curriculum;
                    curriculumText.textContent = `CY ${data.curriculum}`;
                } else {
                    curriculumText.textContent = "No curriculum found";
                    curriculum.value = "";
                }
            } catch (e) {
                curriculumText.textContent = "Error";
                curriculum.value = "";
            }
        }
        checkFormValidity();
    });
    sectionFilter.addEventListener('change', checkFormValidity);
    // When AY or term changes, section list must be reloaded (sections are AY-specific)
    [acadYear, term].forEach(el => {
        el.addEventListener('change', async () => {
            if (program.value && yearLevel.value) await loadSections();
            else checkFormValidity();
        });
    });
    useHistorical.addEventListener('change', checkFormValidity);

    checkFormValidity();

    btnCalendarView.addEventListener('click', () => {
        document.getElementById('tableViewContainer').classList.add('hidden');
        document.getElementById('calendarViewContainer').classList.remove('hidden');
        btnCalendarView.classList.add('active');
        btnTableView.classList.remove('active');
        if (currentScheduleData.length > 0) renderCalendarView(currentScheduleData);
    });

    btnTableView.addEventListener('click', () => {
        document.getElementById('calendarViewContainer').classList.add('hidden');
        document.getElementById('tableViewContainer').classList.remove('hidden');
        btnTableView.classList.add('active');
        btnCalendarView.classList.remove('active');
        // Re-render table to ensure it reflects the latest data (#7)
        if (currentScheduleData.length) renderTable(currentScheduleData, sortSelect.value);
    });

    sortSelect.addEventListener('change', () => {
        if (currentScheduleData.length) renderTable(currentScheduleData, sortSelect.value);
    });

    function getContext() {
        return {
            program:   program.value,
            yearLevel: parseInt(yearLevel.value),
            term:      term.value,
            acadYear:  acadYear.value,
            section:   sectionFilter.value,
        };
    }

    let _generating = false; // #13: prevent concurrent generation calls
    let _generateController = null; // AbortController for the active generate fetch

    const _LS_KEY = 'schedGen_lastResult';

    // #1: Reset everything to a clean initial state after saving as Draft.
    function _resetPageState() {
        currentScheduleData = [];
        currentBatchId      = null;
        canPublish          = false;
        currentEvaluation   = null;
        rowSelectionState.clear();
        try { localStorage.removeItem(_LS_KEY); } catch(e) {}

        document.getElementById('scheduleTableBody').innerHTML = `
            <tr class="table-empty-row">
                <td colspan="12">
                    <i class="fas fa-calendar-plus"></i>
                    Select filters above and click Generate Schedule to begin
                </td>
            </tr>`;
        document.getElementById('genGridWrapper').querySelectorAll('.schedule-pill').forEach(p => p.remove());
        document.getElementById('tableTitle').innerHTML = 'WAITING FOR SELECTION...';
        document.getElementById('conflictBanner').classList.add('hidden');

        // Reset all form controls
        acadYear.value = '';
        term.value     = '';
        program.value  = '';
        yearLevel.innerHTML   = '<option value="">SELECT</option>';
        sectionFilter.innerHTML = '<option value="">SELECT</option>';
        curriculum.value      = '';
        curriculumText.textContent = 'Select Program first';
        if (genProgTriggerText) genProgTriggerText.textContent = '-SELECT PROGRAM-';
        if (genProgWrapper) genProgWrapper.classList.remove('open');

        btnRegenerate.disabled   = true;
        btnSaveDraft.disabled    = true;
        btnManualEditor.disabled = true;
        btnExport.disabled       = true;
        btnApprove.disabled      = true;
        btnGenerate.disabled     = true;

        // Reset schedule evaluation panel
        const pctEl        = document.getElementById('evalScorePct');
        const cspBadge     = document.getElementById('evalCspBadge');
        const cspIcon      = document.getElementById('evalCspIcon');
        const cspText      = document.getElementById('evalCspText');
        const approvalEl   = document.getElementById('evalApprovalBadge');
        const breakdownEl  = document.getElementById('evalBreakdown');
        if (pctEl)      { pctEl.textContent = '—'; pctEl.className = 'eval-score-pct'; }
        if (cspBadge)   cspBadge.className = 'eval-csp-badge status-pending';
        if (cspIcon)    cspIcon.className  = 'fas fa-circle-notch';
        if (cspText)    cspText.textContent = 'Generate a schedule to evaluate it';
        if (approvalEl) approvalEl.classList.add('hidden');
        if (breakdownEl) { breakdownEl.innerHTML = ''; breakdownEl.classList.remove('visible'); }
    }

    function _saveStateToStorage() {
        if (!currentScheduleData.length) return;
        try {
            localStorage.setItem(_LS_KEY, JSON.stringify({
                scheduleData: currentScheduleData,
                batchId: currentBatchId,
                canPublish,
                context: getContext(),
                formValues: {
                    acadYear: acadYear.value,
                    term: term.value,
                    program: program.value,
                    yearLevel: yearLevel.value,
                    section: sectionFilter.value,
                    curriculum: curriculum.value,
                }
            }));
        } catch(e) {}
    }

    // Shared helper: restore form dropdowns from saved values and update the
    // custom program trigger text (which is a separate visible element).
    async function _restoreFormDropdowns(fv) {
        if (fv.program) program.value = fv.program;
        if (fv.acadYear) acadYear.value = fv.acadYear;
        if (fv.term) term.value = fv.term;

        // Fix custom program dropdown — the hidden <select> is updated above but
        // the visible trigger text must also be synced or it looks blank/empty.
        if (fv.program && genProgTriggerText) {
            const selOpt = program.querySelector(`option[value="${CSS.escape(fv.program)}"]`);
            const progName = selOpt ? (selOpt.dataset.name || selOpt.textContent.split('–')[1]?.trim() || '') : '';
            genProgTriggerText.textContent = progName
                ? `${fv.program} – ${progName}`
                : fv.program;
        }

        if (fv.program) {
            await loadYearLevels(fv.program);
            if (fv.yearLevel) {
                yearLevel.value = fv.yearLevel;
                await loadSections();
                if (fv.section) sectionFilter.value = fv.section;
                if (fv.curriculum) {
                    curriculum.value = fv.curriculum;
                    curriculumText.textContent = `CY ${fv.curriculum}`;
                }
            }
        }
    }

    async function _restoreStateFromStorage() {
        try {
            const raw = localStorage.getItem(_LS_KEY);
            if (!raw) return;
            const saved = JSON.parse(raw);
            if (!saved || !saved.scheduleData || !saved.scheduleData.length) return;

            const fv  = saved.formValues || {};
            const ctx = saved.context    || {};

            // Check whether the Manual Editor saved a newer draft for this context.
            // If it did, the localStorage cache is stale — fetch fresh data from the DB.
            let draftMarker = null;
            try {
                const markerRaw = sessionStorage.getItem('schedGen_draftUpdated');
                if (markerRaw) draftMarker = JSON.parse(markerRaw);
            } catch(_e) {}

            const ctxProg = ctx.program   || fv.program   || '';
            const ctxYl   = ctx.yearLevel || fv.yearLevel || '';
            const ctxAy   = ctx.acadYear  || fv.acadYear  || '';
            const ctxTerm = ctx.term      || fv.term      || '';

            const markerMatches = draftMarker &&
                draftMarker.program   === ctxProg &&
                String(draftMarker.yearLevel) === String(ctxYl) &&
                draftMarker.acadYear  === ctxAy &&
                draftMarker.term      === ctxTerm;

            if (markerMatches) {
                // Consume the marker so future reloads don't loop.
                try { sessionStorage.removeItem('schedGen_draftUpdated'); } catch(_e) {}
                try { localStorage.removeItem(_LS_KEY); } catch(_e) {}
                // Restore form first so the dropdowns look right, then load fresh draft.
                await _restoreFormDropdowns(fv);
                await _loadLatestDraftFromDb(ctxProg, ctxYl, ctxAy, ctxTerm);
                return;
            }

            // Standard restore from localStorage (no newer draft detected).
            await _restoreFormDropdowns(fv);

            currentScheduleData = saved.scheduleData;
            currentBatchId      = saved.batchId || null;
            canPublish          = saved.canPublish || false;

            renderTable(currentScheduleData, sortSelect.value);
            updateTitleBar();

            btnRegenerate.disabled   = false;
            btnSaveDraft.disabled    = false;
            btnManualEditor.disabled = false;
            btnExport.disabled       = false;
            btnApprove.disabled      = !canPublish;

            updateEvaluationWidget(currentScheduleData, getContext());
        } catch(e) {}
    }

    // Fetch the latest Draft from the DB for the given context and display it.
    async function _loadLatestDraftFromDb(prog, yl, ay, term) {
        try {
            const url = `/api/schedule/latest-draft?program=${encodeURIComponent(prog)}`
                      + `&year_level=${encodeURIComponent(yl)}`
                      + `&ay=${encodeURIComponent(ay)}`
                      + `&term=${encodeURIComponent(term)}`;
            const res  = await fetch(url);
            const data = await res.json();

            if (data.success && data.schedule_data && data.schedule_data.length) {
                currentScheduleData = data.schedule_data;
                currentBatchId      = `DRAFT-V${data.version || 'LATEST'}`;
                canPublish          = false; // re-validate before approving

                renderTable(currentScheduleData, sortSelect.value);
                updateTitleBar();

                btnRegenerate.disabled   = false;
                btnSaveDraft.disabled    = false;
                btnSaveDraft.innerHTML   = '<i class="fas fa-save"></i> Save as Draft';
                btnManualEditor.disabled = false;
                btnExport.disabled       = false;
                btnApprove.disabled      = true; // re-validated below once the evaluation loads

                updateEvaluationWidget(currentScheduleData, getContext());
            }
        } catch(_e) {}
    }

    // opts.confirmLabel/opts.cancelLabel let a caller relabel the two buttons
    // (e.g. "View Partial Schedule" / "Discard Results" for type 'partial')
    // without touching any existing call site — both default to the original
    // "OK"/"Cancel" text so every prior showInfo(...) call keeps working as-is.
    function showInfo(title, message, type = 'info', opts = {}) {
        return new Promise(resolve => {
            const modal    = document.getElementById('infoModal');
            const icon     = document.getElementById('infoModalIcon');
            const titleEl  = document.getElementById('infoModalTitle');
            const msgEl    = document.getElementById('infoModalMessage');
            const confirmBtn = document.getElementById('infoModalConfirmBtn');
            const cancelBtn  = document.getElementById('infoModalCancelBtn');

            titleEl.textContent = title;
            msgEl.innerHTML     = message;
            confirmBtn.textContent = opts.confirmLabel || 'OK';
            cancelBtn.textContent  = opts.cancelLabel  || 'Cancel';

            icon.className = 'info-modal-icon';
            if (type === 'success') {
                icon.innerHTML = '<i class="fas fa-check-circle"></i>';
                icon.classList.add('success');
            } else if (type === 'error') {
                icon.innerHTML = '<i class="fas fa-times-circle"></i>';
                icon.classList.add('error');
            } else if (type === 'confirm' || type === 'partial') {
                icon.innerHTML = '<i class="fas fa-question-circle"></i>';
                icon.classList.add('warning');
            } else {
                icon.innerHTML = '<i class="fas fa-info-circle"></i>';
            }

            modal.classList.remove('hidden');

            if (type === 'confirm' || type === 'partial') {
                cancelBtn.classList.remove('hidden');
                confirmBtn.onclick = () => { modal.classList.add('hidden'); resolve(true); };
                cancelBtn.onclick  = () => { modal.classList.add('hidden'); resolve(false); };
            } else {
                cancelBtn.classList.add('hidden');
                confirmBtn.onclick = () => { modal.classList.add('hidden'); resolve(true); };
            }
        });
    }

    function showLoading() {
        document.getElementById('loadingModal').classList.remove('hidden');
        document.getElementById('progressBarFill').style.width = '0%';
        document.getElementById('progressLabel').textContent = '0%';
        simulateProgress();
    }
    function hideLoading() {
        document.getElementById('loadingModal').classList.add('hidden');
    }

    function simulateProgress() {
        let progress = 0;
        const steps = [
            'Initializing genetic algorithm...',
            'Analyzing constraints and faculty availability...',
            'Generating schedule population...',
            'Evaluating fitness scores...',
            'Performing crossover and mutation...',
            'Optimizing conflicts...',
            'Finalizing schedule...',
        ];
        let stepIndex = 0;

        const interval = setInterval(() => {
            // This bar is a fake animation (the actual /api/schedule/generate call is one
            // request with no real progress to report), only meant to reassure the user
            // something is happening — it always caps at 99% and completeProgress() snaps it
            // to 100% once the real response arrives. It used to jump to 88% fast and then
            // crawl the last 11 points at 1/60th the speed (~0.125%/tick, 35+ seconds to
            // creep from 88% to 99%) — that dead-slow stretch is exactly what read as "the
            // generation gets slower past 90%", even on a fast, ordinary generation. Pushing
            // the fast phase further (to 95%) and roughly doubling the crawl rate keeps the
            // same "don't finish before the real work does" safety margin for a genuinely
            // slow generation, while cutting the felt stall from ~35s down to ~6s.
            if (progress < 95) {
                progress += Math.random() * 15;
            } else {
                progress += Math.random() * 0.5;
            }
            if (progress > 99) progress = 99;

            document.getElementById('progressBarFill').style.width = progress + '%';
            document.getElementById('progressLabel').textContent = Math.floor(progress) + '%';

            if (stepIndex < steps.length && progress > (stepIndex + 1) * (95 / steps.length)) {
                document.getElementById('loadingStep').textContent = steps[stepIndex];
                stepIndex++;
            }
        }, 400);

        btnGenerate._progressInterval = interval;
    }

    function completeProgress() {
        if (btnGenerate._progressInterval) clearInterval(btnGenerate._progressInterval);
        document.getElementById('progressBarFill').style.width = '100%';
        document.getElementById('progressLabel').textContent = '100%';
        document.getElementById('loadingStep').textContent = 'Complete!';
        setTimeout(hideLoading, 500);
    }

    function renderTable(scheduleArray, sortBy = 'default') {
        const tbody = document.getElementById('scheduleTableBody');
        if (!scheduleArray.length) {
            tbody.innerHTML = '<tr class="empty-row"><td colspan="12"><span class="empty-msg"><i class="fas fa-calendar-plus"></i> No classes scheduled yet</span></td></tr>';
            _updatePreserveCaption();
            _syncSelectAllCheckbox();
            return;
        }

        // Row-conflict highlighting reads the shared evaluation object's violationsBySubject —
        // same object that drives the evaluation panel and Approve gating.
        const violBySubject = (currentEvaluation && currentEvaluation.violationsBySubject) || {};

        const groups = {};
        scheduleArray.forEach(cls => {
            // Same subject+faculty grouping as before this feature — a subject's
            // Lecture and Lab parts still merge into one row/checkbox when taught
            // by the same instructor (see rowKeyOf() above for the shared-state
            // trade-off this implies for selective regeneration).
            const key = rowKeyOf(cls);
            if (!groups[key]) {
                groups[key] = {
                    rowKey:       key,
                    instructor:   cls.instructor || '-',
                    subject_code: cls.subject_code || '-',
                    description:  cls.subject_name || cls.description || cls.subjectname || '-',
                    lec_hours:    0,
                    lab_hours:    0,
                    credit_units: cls.credit_units || cls.creditunits || 0,
                    course:       cls.course || '-',
                    times:        [],
                    days_set:     [],
                    rooms:        [],
                    incomplete:        false,
                    incompleteReasons: [],
                };
            }
            const g = groups[key];
            g.lec_hours += (cls.lec_hours || cls.lecturehours || 0);
            g.lab_hours += (cls.lab_hours || cls.laboratoryhours || 0);

            // Partial-generation support (architecture spec section 6): a gene
            // the backend stripped down to an unresolved component (or that
            // never resolved at all) carries incomplete/incomplete_reason —
            // surfaced here as a distinct row state from a plain CSP conflict.
            if (cls.incomplete) {
                g.incomplete = true;
                (cls.incomplete_reason || []).forEach(r => {
                    if (!g.incompleteReasons.includes(r)) g.incompleteReasons.push(r);
                });
            }

            const t = cls.time || '';
            if (t && !g.times.includes(t)) g.times.push(t);

            // #12: split on '/', ',', or whitespace so both "MON/THU" and "MON THU" are handled
            (cls.days || '').split(/[\/,\s]+/).filter(Boolean).forEach(d => {
                const day = d.trim();
                if (day && !g.days_set.includes(day)) g.days_set.push(day);
            });

            const room = cls.room || 'TBA';
            if (!g.rooms.includes(room)) g.rooms.push(room);
        });

        let entries = Object.values(groups);
        if (sortBy === 'az') entries.sort((a, b) => a.subject_code.localeCompare(b.subject_code));
        else if (sortBy === 'za') entries.sort((a, b) => b.subject_code.localeCompare(a.subject_code));
        else if (sortBy === 'time') entries.sort((a, b) => (a.times[0] || '').localeCompare(b.times[0] || ''));

        tbody.innerHTML = entries.map((g) => {
            const state = rowSelectionState.get(g.rowKey);
            const sel   = !!(state && state.selected);
            const pres  = (state && state.preserve) || {};
            // Only a checked (selected-for-regeneration) row shows its per-field
            // "preserve this value" checkboxes — an unchecked row is already
            // fully preserved as a whole, so field-level checkboxes on it would
            // be meaningless.
            const preserveBox = (field) => sel ? `
                <label class="preserve-select-wrap" title="Preserve this value during regeneration">
                    <input type="checkbox" class="preserve-select" data-row-key="${g.rowKey}" data-field="${field}" ${pres[field] ? 'checked' : ''}>
                    Preserve
                </label>` : '';
            // Value + its "Preserve" checkbox sit side by side on one line
            // (not stacked) via .cell-with-preserve — see scheduleGeneration.css.
            const cellWithPreserve = (valueHtml, field) => `
                <div class="cell-with-preserve">
                    <span>${valueHtml}</span>${preserveBox(field)}
                </div>`;

            const violations  = violBySubject[g.subject_code] || [];
            const hasConflict  = violations.length > 0;
            const conflictTip  = hasConflict
                ? violations.map(v => (typeof v === 'string') ? v : (v.detail || v.rule || 'Conflict detected')).join(' | ')
                : '';
            // role="img" + aria-label + title makes this reachable by keyboard (tabindex),
            // not just mouse hover, per the panel's accessibility requirement.
            const conflictFlag = hasConflict ? `
                <span class="row-conflict-flag" role="img" tabindex="0" aria-label="Conflict: ${conflictTip.replace(/"/g, '&quot;')}" title="${conflictTip.replace(/"/g, '&quot;')}">
                    <i class="fas fa-exclamation-triangle"></i>
                </span>` : '';

            const incompleteTip = g.incompleteReasons.join(' | ') || 'This component could not be resolved.';
            const incompleteFlag = g.incomplete ? `
                <span class="row-incomplete-flag" role="img" tabindex="0" aria-label="Incomplete: ${incompleteTip.replace(/"/g, '&quot;')}" title="${incompleteTip.replace(/"/g, '&quot;')}">
                    <i class="fas fa-circle-question"></i>
                </span>` : '';

            return `
            <tr class="${hasConflict ? 'row-conflict' : ''} ${g.incomplete ? 'row-incomplete' : ''}">
                <td class="td-check"><input type="checkbox" class="row-select" data-row-key="${g.rowKey}" ${sel ? 'checked' : ''}></td>
                <td class="td-instructor">${conflictFlag}${incompleteFlag}${cellWithPreserve(g.instructor, 'faculty')}</td>
                <td class="td-code">${g.subject_code}</td>
                <td class="td-desc">${g.description}</td>
                <td class="td-num">${g.lec_hours}</td>
                <td class="td-num">${g.lab_hours}</td>
                <td class="td-num">${g.credit_units}</td>
                <td class="td-course">${g.course}</td>
                <td class="td-time">${cellWithPreserve(g.times.map(t => `<span class="time-line">${t}</span>`).join(''), 'schedule')}</td>
                <td class="td-num">${g.lec_hours + g.lab_hours}</td>
                <td class="td-days">${_sortDays(g.days_set).join(' / ')}</td>
                <td class="td-room">${cellWithPreserve(g.rooms.join('<br>'), 'room')}</td>
            </tr>`;
        }).join('');

        _updatePreserveCaption();
        _syncSelectAllCheckbox();
    }

    // ── Selective regeneration: checkbox wiring ───────────────────────────────
    // Delegated on the tbody (registered once) since renderTable() rebuilds
    // innerHTML on every render, which would otherwise drop per-checkbox
    // listeners. Re-rendering the whole table on every checkbox change is
    // cheap here (a schedule table is small) and keeps the caption/select-all
    // checkbox trivially in sync with rowSelectionState.
    function _anyRowSelected() {
        return [...rowSelectionState.values()].some(s => s.selected);
    }

    function _updatePreserveCaption() {
        const anySelected = _anyRowSelected();
        const caption = document.getElementById('preserveCaption');
        if (caption) caption.classList.toggle('hidden', !anySelected);
        _updateRegenerateButtonLabel();
    }

    // The button only reads "Re-generate selected" once at least one row is
    // checked — otherwise it reads plain "Re-generate" (full regenerate,
    // exactly what clicking it does when nothing is selected).
    function _updateRegenerateButtonLabel() {
        if (!btnRegenerate || _generating) return; // don't clobber the "Regenerating..." label mid-request
        btnRegenerate.innerHTML = _anyRowSelected()
            ? '<i class="fas fa-sync-alt"></i> Re-generate selected'
            : '<i class="fas fa-sync-alt"></i> Re-generate';
    }

    function _syncSelectAllCheckbox() {
        const chk = document.getElementById('chkSelectAllRows');
        if (!chk) return;
        const keys = [...new Set(currentScheduleData.map(rowKeyOf))];
        chk.checked = keys.length > 0 && keys.every(k => {
            const s = rowSelectionState.get(k);
            return s && s.selected;
        });
    }

    document.getElementById('scheduleTableBody').addEventListener('change', (e) => {
        const t = e.target;
        if (t.classList.contains('row-select')) {
            const key   = t.dataset.rowKey;
            const state = rowSelectionState.get(key) || { selected: false, preserve: {} };
            state.selected = t.checked;
            if (!state.selected) state.preserve = {}; // unchecking a row clears its field-level preserves too
            rowSelectionState.set(key, state);
            renderTable(currentScheduleData, sortSelect.value);
        } else if (t.classList.contains('preserve-select')) {
            const key   = t.dataset.rowKey;
            const field = t.dataset.field;
            const state = rowSelectionState.get(key) || { selected: true, preserve: {} };
            state.preserve[field] = t.checked;
            rowSelectionState.set(key, state);
            renderTable(currentScheduleData, sortSelect.value);
        }
    });

    const chkSelectAllRows = document.getElementById('chkSelectAllRows');
    if (chkSelectAllRows) {
        chkSelectAllRows.addEventListener('change', (e) => {
            const checked = e.target.checked;
            const keys = new Set(currentScheduleData.map(rowKeyOf));
            keys.forEach(key => {
                const state = rowSelectionState.get(key) || { selected: false, preserve: {} };
                state.selected = checked;
                if (!checked) state.preserve = {};
                rowSelectionState.set(key, state);
            });
            renderTable(currentScheduleData, sortSelect.value);
        });
    }

    const CAL_DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
    const CAL_DAY_MAP = {
        'MONDAY':'Monday','TUESDAY':'Tuesday','WEDNESDAY':'Wednesday',
        'THURSDAY':'Thursday','FRIDAY':'Friday','SATURDAY':'Saturday','SUNDAY':'Sunday',
        'MON':'Monday','TUE':'Tuesday','WED':'Wednesday','THU':'Thursday',
        'FRI':'Friday','SAT':'Saturday','SUN':'Sunday',
        'M':'Monday','W':'Wednesday','F':'Friday','S':'Saturday',
        'T':'Tuesday','TH':'Thursday','SU':'Sunday'
    };
    const GRID_ORIGIN_MINS = 7 * 60 + 30;

    function calParseTimeMins(t) {
        if (!t) return null;
        t = t.trim();
        let m = t.match(/^(\d{1,2}):(\d{2})$/);
        if (m) return parseInt(m[1]) * 60 + parseInt(m[2]);
        m = t.match(/^(\d{1,2}):(\d{2})\s*(AM|PM)$/i);
        if (m) {
            let h = parseInt(m[1]), mn = parseInt(m[2]);
            if (m[3].toUpperCase() === 'PM' && h !== 12) h += 12;
            if (m[3].toUpperCase() === 'AM' && h === 12) h = 0;
            return h * 60 + mn;
        }
        return null;
    }

    function calParseTimeRange(timeStr) {
        if (!timeStr) return { start: null, end: null };
        const parts = timeStr.split(/\s*[-–]\s*/);
        if (parts.length >= 2) return { start: calParseTimeMins(parts[0]), end: calParseTimeMins(parts[parts.length - 1]) };
        return { start: null, end: null };
    }

    function calParseDays(daysStr) {
        if (!daysStr) return [];
        const result = [];
        const push = (d) => { if (!result.includes(d)) result.push(d); };
        const parts = daysStr.split(/[\/,\s]+/).filter(p => p);
        for (const part of parts) {
            const up = part.toUpperCase();
            if (CAL_DAY_MAP[up]) { push(CAL_DAY_MAP[up]); continue; }
            if (up === 'TTH') { push('Tuesday'); push('Thursday'); }
            else if (up === 'MWF') { push('Monday'); push('Wednesday'); push('Friday'); }
            else if (up === 'MW')  { push('Monday'); push('Wednesday'); }
            else if (up === 'TF')  { push('Tuesday'); push('Friday'); }
            else if (up === 'MTH') { push('Monday'); push('Thursday'); }
        }
        return result;
    }

    function calSubjectColor(code) {
        const colors = ['#16a085','#27ae60','#2980b9','#8e44ad','#2c3e50','#f39c12','#d35400','#c0392b'];
        let h = 0;
        for (let i = 0; i < (code || '').length; i++) h = (code || '').charCodeAt(i) + ((h << 5) - h);
        return colors[Math.abs(h) % colors.length];
    }

    function calFmtMins(mins) {
        if (mins === null || mins === undefined) return '—';
        const h = Math.floor(mins / 60), m = mins % 60;
        const suffix = h >= 12 ? 'PM' : 'AM';
        const h12 = h > 12 ? h - 12 : (h === 0 ? 12 : h);
        return h12 + ':' + String(m).padStart(2, '0') + ' ' + suffix;
    }

    function renderCalendarView(scheduleArray) {
        const wrapper = document.getElementById('genGridWrapper');
        const table   = document.getElementById('genMainTimetable');
        wrapper.querySelectorAll('.schedule-pill').forEach(p => p.remove());

        if (!scheduleArray.length) return;

        const sessions = [];
        scheduleArray.forEach(cls => {
            const { start, end } = calParseTimeRange(cls.time);
            const parsedDays = calParseDays(cls.days);
            parsedDays.forEach(day => {
                sessions.push({
                    daydesc:    day,
                    startMins:  start,
                    endMins:    end,
                    subjectcode: cls.subject_code || '',
                    subjectname: cls.subject_name || cls.description || cls.subjectname || '',
                    instructor:  cls.instructor || 'TBA',
                    roomname:    cls.room || 'TBA',
                });
            });
        });

        const firstCell = table.querySelector('tbody td:nth-child(2)');
        const timeCol   = table.querySelector('.time-cell');
        const thead     = table.querySelector('thead');
        if (!firstCell || firstCell.offsetWidth === 0) {
            requestAnimationFrame(() => renderCalendarView(scheduleArray));
            return;
        }

        const colWidth = firstCell.offsetWidth;
        const pxPerMin = firstCell.offsetHeight / 30;

        const dayGroups = {};
        sessions.forEach(s => {
            if (!dayGroups[s.daydesc]) dayGroups[s.daydesc] = [];
            dayGroups[s.daydesc].push(s);
        });

        Object.keys(dayGroups).forEach(dayName => {
            const dayIdx = CAL_DAYS.indexOf(dayName);
            if (dayIdx < 0) return;
            const daySessions = dayGroups[dayName];
            daySessions.sort((a, b) => a.startMins - b.startMins);

            daySessions.forEach((sess, idx) => {
                let start = sess.startMins, end = sess.endMins;
                if (start === null) return;
                if (start < GRID_ORIGIN_MINS) start += 12 * 60;
                if (end <= start) end += 12 * 60;

                let overlapCount = 0, overlapIndex = 0;
                daySessions.forEach((other, oIdx) => {
                    let oS = other.startMins, oE = other.endMins;
                    if (oS < GRID_ORIGIN_MINS) oS += 12 * 60;
                    if (oE <= oS) oE += 12 * 60;
                    if (start < oE && end > oS) { overlapCount++; if (idx > oIdx) overlapIndex++; }
                });

                const pill  = document.createElement('div');
                pill.className = 'schedule-pill';
                pill.style.backgroundColor = calSubjectColor(sess.subjectcode);

                const w     = (colWidth - 6) / (overlapCount || 1);
                const pillH = (end - start) * pxPerMin - 2;

                pill.style.width  = (w - 2) + 'px';
                pill.style.height = pillH + 'px';
                pill.style.left   = (timeCol.offsetWidth + dayIdx * colWidth + overlapIndex * w + 4) + 'px';
                pill.style.top    = (thead.offsetHeight + (start - GRID_ORIGIN_MINS) * pxPerMin + 1) + 'px';

                pill.title = `${sess.subjectcode}\n${sess.subjectname}\n${sess.instructor}\n${calFmtMins(start)} – ${calFmtMins(end)}\n${sess.roomname}`;
                pill.innerHTML = `
                    <div class="pill-subject">${sess.subjectname}</div>
                    <div class="pill-instructor">${sess.instructor}</div>
                    <div class="pill-room">${sess.roomname}</div>`;

                wrapper.appendChild(pill);
            });
        });
    }

    function updateTitleBar() {
        const ctx = getContext();
        const termName    = term.options[term.selectedIndex]?.text || ctx.term;
        const sectionText = sectionFilter.options[sectionFilter.selectedIndex]?.text || '';
        const sectionPart = (sectionText && sectionText !== 'SELECT') ? ` — ${sectionText}` : '';
        // Context strip format: [PROGRAM] — YEAR [YEAR LEVEL] — [SECTION] | [SEMESTER] | AY [ACADEMIC YEAR]
        document.getElementById('tableTitle').innerHTML =
            `<i class="fas fa-calendar-alt"></i> ${ctx.program || 'Program'} — YEAR ${ctx.yearLevel || '?'}${sectionPart} | ${termName} | AY ${ctx.acadYear || '—'}`;
    }

    function applyScheduleResult(data, isRetrieve) {
        currentScheduleData = data.schedule_data || [];
        currentBatchId      = data.batch_id || 'DRAFT-NEW-001';

        const internalConflicts = data.conflict_count       || 0;
        const crossConflicts    = data.cross_conflict_count || 0;
        const totalConflicts    = internalConflicts + crossConflicts;

        // The evaluation object (when the generate response carries one) is the
        // authoritative source for Approve gating — a high weighted score never
        // overrides a CSP failure. Set it before renderTable() so row-conflict
        // highlighting reflects this result on the very first paint.
        currentEvaluation = data.evaluation || null;
        canPublish = currentEvaluation
            ? !!currentEvaluation.eligibleForApproval
            : (totalConflicts === 0);

        renderTable(currentScheduleData, sortSelect.value);
        updateTitleBar();

        // #7/#8: Always refresh calendar if it is currently visible so both views stay in sync.
        if (!document.getElementById('calendarViewContainer').classList.contains('hidden')) {
            renderCalendarView(currentScheduleData);
        }

        btnRegenerate.disabled   = false;
        btnSaveDraft.disabled    = false;
        // Reset the label too — a prior generation's "Saved as Draft" state (see
        // btnSaveDraft's success handler) must not linger onto this new, unsaved result.
        btnSaveDraft.innerHTML   = '<i class="fas fa-save"></i> Save as Draft';
        btnManualEditor.disabled = false;
        btnExport.disabled       = false;
        btnApprove.disabled      = !canPublish;

        const conflictDetailList = document.getElementById('conflictDetailList');
        if (totalConflicts > 0) {
            let conflictMsg = `Schedule has ${totalConflicts} unresolved conflict(s). Approve is blocked until resolved.`;
            if (internalConflicts > 0 && crossConflicts > 0) {
                conflictMsg += ` (${internalConflicts} internal, ${crossConflicts} with already-approved schedules.)`;
            } else if (crossConflicts > 0) {
                conflictMsg += ` (${crossConflicts} conflict(s) with already-approved schedules from other programs/sections.)`;
            }
            document.getElementById('conflictText').textContent = conflictMsg;

            // Populate the detail list with individual conflict descriptions
            if (conflictDetailList) {
                conflictDetailList.innerHTML = '';
                const allViolations = [
                    ...(data.violations          || []),
                    ...(data.cross_violations    || []),
                ];
                if (allViolations.length > 0) {
                    allViolations.forEach(v => {
                        const li = document.createElement('li');
                        // Formal type label (e.g. "Conflict: Room Schedule") ahead of the
                        // plain-language detail, so each line names the kind of conflict.
                        const _typeLabel = v.type || (v.rule ? `Conflict: ${v.rule}` : '');
                        const _body      = v.detail || v.subject || 'Conflict detected.';
                        li.textContent = _typeLabel ? `${_typeLabel} — ${_body}` : _body;
                        conflictDetailList.appendChild(li);
                    });
                    conflictDetailList.style.display = 'block';
                } else {
                    conflictDetailList.style.display = 'none';
                }
            }
            document.getElementById('conflictBanner').classList.remove('hidden');
        } else {
            document.getElementById('conflictBanner').classList.add('hidden');
            if (conflictDetailList) conflictDetailList.style.display = 'none';
        }

        // Partial-generation support (architecture spec section 6/8): surface
        // how many components are still unresolved and offer a one-click way to
        // select them all for targeted regeneration (via the existing per-row
        // checkbox + "Re-generate Selected" flow — this is a selection helper,
        // not a "Regenerate All" button).
        const incompleteBanner = document.getElementById('incompleteBanner');
        const incompleteCount  = data.incomplete_count || 0;
        if (incompleteBanner) {
            if (incompleteCount > 0) {
                document.getElementById('incompleteText').textContent =
                    `${incompleteCount} component${incompleteCount === 1 ? '' : 's'} could not be resolved ` +
                    `within the generation limit (completion ${data.completion_rate != null ? data.completion_rate : '?'}%). ` +
                    `Select the incomplete rows and use "Re-generate Selected" to try again.`;
                incompleteBanner.classList.remove('hidden');
            } else {
                incompleteBanner.classList.add('hidden');
            }
        }

        const toast = document.getElementById('successToast');
        if (isRetrieve && data.retrieved_from) {
            const rf = data.retrieved_from;
            const msg = rf.source === 'historical'
                ? `Previous schedule retrieved from historical data — ${rf.ay_label} ${rf.term}`
                : `Previous official schedule retrieved — ${rf.ay_label} ${rf.term}`;
            toast.innerHTML = `<i class="fas fa-check-circle"></i> ${msg}`;
        } else {
            toast.innerHTML = '<i class="fas fa-check-circle"></i> Schedule Generated Successfully!';
        }
        toast.classList.remove('hidden');
        setTimeout(() => toast.classList.add('hidden'), 4000);

        if (currentEvaluation) {
            _renderEvaluationResult(currentEvaluation);
        } else {
            updateEvaluationWidget(currentScheduleData, getContext());
        }
    }

    // Score-band coloring, shared by the overall score and every criterion row.
    // Green = full/high compliance, Amber = partial, Red = failed/invalid.
    function _evalBand(pct) {
        return pct >= 85 ? 'eval-good' : pct >= 60 ? 'eval-warn' : 'eval-bad';
    }

    // Criterion metadata: label + which band-coloring to use. "hist" criteria use the
    // purple historical-match token instead of the plain good/warn/bad traffic-light scale.
    const _EVAL_CATEGORIES = [
        { key: 'conflictValidation',   heading: 'CONFLICT VALIDATION',    criteria: [
            { key: 'facultyConflictFree', label: 'Faculty Conflict-Free' },
            { key: 'roomConflictFree',    label: 'Room Conflict-Free' },
            { key: 'sectionConflictFree', label: 'Section Conflict-Free' },
        ]},
        { key: 'constraintCompliance', heading: 'CONSTRAINT COMPLIANCE',  criteria: [
            { key: 'facultyAssignmentSuitability', label: 'Faculty Assignment Suitability' },
            { key: 'roomTypeSuitability',           label: 'Room Type Suitability' },
            { key: 'subjectDayCompliance',          label: 'Subject-Day Compliance' },
            { key: 'schedulePairingDistribution',   label: 'Schedule Pairing & Distribution' },
            { key: 'facultyLoadCompliance',         label: 'Faculty Load Compliance' },
        ]},
        { key: 'recommendationQuality', heading: 'RECOMMENDATION QUALITY', hist: true, criteria: [
            { key: 'historicalFacultyMatch', label: 'Historical Faculty Match' },
            { key: 'historicalRoomMatch',    label: 'Historical Room Match' },
            { key: 'historicalScheduleMatch', label: 'Historical Schedule Match' },
        ]},
    ];

    // Renders the shared evaluation result object into the Schedule Evaluation panel.
    // This same object also drives row-conflict highlighting (renderTable) and
    // Approve-button gating (applyScheduleResult) — one source of truth throughout.
    function _renderEvaluationResult(data) {
        const pctEl       = document.getElementById('evalScorePct');
        const cspBadge     = document.getElementById('evalCspBadge');
        const cspIcon      = document.getElementById('evalCspIcon');
        const cspText      = document.getElementById('evalCspText');
        const approvalEl   = document.getElementById('evalApprovalBadge');
        const breakdownEl  = document.getElementById('evalBreakdown');
        if (!pctEl) return;

        if (data && data.success === false) {
            pctEl.textContent = 'N/A';
            pctEl.className   = 'eval-score-pct';
            if (cspBadge) cspBadge.className = 'eval-csp-badge status-pending';
            if (cspIcon)  cspIcon.className  = 'fas fa-question';
            if (cspText)  cspText.textContent = data.error || 'Could not evaluate this schedule.';
            if (approvalEl) approvalEl.classList.add('hidden');
            if (breakdownEl) { breakdownEl.innerHTML = ''; breakdownEl.classList.remove('visible'); }
            return;
        }

        const overall     = Math.round(data.overallScore || 0);
        const cspPassed    = !!data.cspPassed;
        const violationCnt = data.hardViolationCount || 0;
        const eligible      = !!data.eligibleForApproval;

        pctEl.textContent = overall + '%';
        pctEl.className   = 'eval-score-pct ' + _evalBand(overall);

        // CSP pass/fail is independent of the weighted score — never let a high
        // score visually imply approval eligibility on its own.
        if (cspBadge) cspBadge.className = 'eval-csp-badge ' + (cspPassed ? 'status-pass' : 'status-fail');
        if (cspIcon)  cspIcon.className  = cspPassed ? 'fas fa-check-circle' : 'fas fa-times-circle';
        if (cspText)  cspText.textContent = cspPassed
            ? `CSP PASSED · 0 HARD-CONSTRAINT VIOLATIONS`
            : `CSP FAILED · ${violationCnt} HARD-CONSTRAINT VIOLATION${violationCnt === 1 ? '' : 'S'}`;

        if (approvalEl) approvalEl.classList.toggle('hidden', !eligible);

        // Partial-generation support (architecture spec section 12): completion
        // rate + incomplete count, shown ahead of the category breakdown. A
        // schedule can be 100% hard-constraint-compliant among what completed
        // and still be < 100% complete — completionRate/incompleteCount and
        // eligibleForApproval are deliberately separate signals (see this
        // function's own module docstring and _compute_schedule_evaluation's).
        const completionEl = document.getElementById('evalCompletionText');
        if (completionEl) {
            const rate = (data.completionRate != null) ? Math.round(data.completionRate) : 100;
            const inc  = data.incompleteCount || 0;
            completionEl.textContent = inc > 0
                ? `COMPLETION ${rate}% · ${inc} COMPONENT${inc === 1 ? '' : 'S'} INCOMPLETE`
                : `COMPLETION ${rate}%`;
            completionEl.className = 'eval-completion-text ' + (inc > 0 ? 'eval-warn' : 'eval-good');
        }

        if (breakdownEl && data.categories) {
            let html = '';
            _EVAL_CATEGORIES.forEach(catDef => {
                const cat = data.categories[catDef.key];
                if (!cat) return;
                html += `<div class="eval-cat-label">${catDef.heading} · ${cat.weight}%</div>`;
                catDef.criteria.forEach(cDef => {
                    const sc = cat.criteria ? cat.criteria[cDef.key] : undefined;
                    if (sc === undefined || sc === null) return;
                    // 'N/A' (architecture spec section 12): no usable historical case —
                    // never rendered as 0% or a fabricated percentage/bar.
                    if (sc === 'N/A') {
                        html += `<div class="eval-item">
                          <div class="eval-item-header">
                            <span class="eval-item-label" title="${cDef.label}">${cDef.label}</span>
                            <span class="eval-item-score eval-na">N/A</span>
                          </div>
                          <div class="eval-bar-track"><div class="eval-bar-fill eval-na" style="width:0%"></div></div>
                        </div>`;
                        return;
                    }
                    const scRounded = Math.round(sc);
                    const band = catDef.hist ? 'eval-hist' : _evalBand(scRounded);
                    html += `<div class="eval-item">
                      <div class="eval-item-header">
                        <span class="eval-item-label" title="${cDef.label}">${cDef.label}</span>
                        <span class="eval-item-score ${band}">${scRounded}%</span>
                      </div>
                      <div class="eval-bar-track">
                        <div class="eval-bar-fill ${band}" style="width:${scRounded}%"></div>
                      </div>
                    </div>`;
                });
            });
            breakdownEl.innerHTML = html;
            breakdownEl.classList.add('visible');
        }
    }

    // Fallback path — used when the generate/retrieve response didn't already carry
    // an evaluation object (e.g. restored from localStorage / loaded from a saved draft).
    async function updateEvaluationWidget(scheduleData, ctx) {
        const pctEl       = document.getElementById('evalScorePct');
        const cspBadge     = document.getElementById('evalCspBadge');
        const cspIcon      = document.getElementById('evalCspIcon');
        const cspText      = document.getElementById('evalCspText');
        const breakdownEl  = document.getElementById('evalBreakdown');
        if (!pctEl) return;

        pctEl.textContent = '...';
        pctEl.className   = 'eval-score-pct';
        if (cspBadge) cspBadge.className = 'eval-csp-badge status-pending';
        if (cspIcon)  cspIcon.className  = 'fas fa-spinner fa-spin';
        if (cspText)  cspText.textContent = 'Evaluating schedule...';
        if (breakdownEl) { breakdownEl.innerHTML = ''; breakdownEl.classList.remove('visible'); }

        try {
            const res  = await fetch('/api/schedule/accuracy', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({
                    schedule_data: scheduleData,
                    program:       ctx.program,
                    year_level:    ctx.yearLevel,
                    term:          ctx.term,
                }),
            });
            const data = await res.json();
            currentEvaluation = (data && data.success !== false) ? data : null;
            // Re-validate Approve gating and row highlighting now that the real
            // evaluation is in — this is the "re-validate before approving" path.
            canPublish = currentEvaluation ? !!currentEvaluation.eligibleForApproval : false;
            btnApprove.disabled = !canPublish;
            renderTable(currentScheduleData, sortSelect.value);
            _renderEvaluationResult(data);
        } catch (e) {
            if (pctEl)   pctEl.textContent = 'N/A';
            if (cspBadge) cspBadge.className = 'eval-csp-badge status-pending';
            if (cspIcon)  cspIcon.className  = 'fas fa-question';
            if (cspText)  cspText.textContent = 'Evaluation unavailable.';
        }
    }

    // Partial-generation support (architecture spec section 6/7): branches on
    // result_status instead of a bare success boolean, shared by both the full
    // Generate and the selective Regenerate handlers below. Falls back to the
    // old COMPLETE_VALID/GENERATION_ERROR inference for a response from a
    // server that hasn't been updated (defensive, should never actually hit
    // that branch against this build's own backend).
    async function _handleGenerationResult(data, isRegenerate) {
        const status = data.result_status || (data.success ? 'COMPLETE_VALID' : 'GENERATION_ERROR');

        if (status === 'COMPLETE_VALID') {
            applyScheduleResult(data, false);
            return;
        }

        if (status === 'PARTIAL_VALID') {
            // Snapshot what's currently on screen so "Discard Results" can put it
            // back exactly — Discard must never touch a previously saved Draft,
            // and a generate/regenerate response is never auto-persisted (Save as
            // Draft is a separate explicit action), so restoring these in-memory
            // values is sufficient to return to "the prior Generation-tab state".
            const priorScheduleData = currentScheduleData;
            const priorBatchId      = currentBatchId;
            const priorEvaluation   = currentEvaluation;

            const viewPartial = await showInfo(
                'Schedule Partially Generated',
                'The system preserved all valid assignments but could not complete some ' +
                'schedule entries within the generation limit. Review the partial schedule ' +
                'and regenerate the incomplete or selected components.',
                'partial',
                { confirmLabel: 'View Partial Schedule', cancelLabel: 'Discard Results' }
            );

            if (viewPartial) {
                // Stays in the Generation tab (no redirect to Manual Editor), renders
                // every valid assignment with incomplete rows flagged (renderTable's
                // .row-incomplete handling above) and wires the existing per-row
                // checkbox + Regenerate flow as the targeted-regeneration affordance.
                applyScheduleResult(data, false);
                const n = data.incomplete_count || 0;
                await showInfo(
                    'Incomplete Components',
                    `${n} component${n === 1 ? '' : 's'} could not be resolved — look for the amber ` +
                    `<i class="fas fa-circle-question"></i> marker in the Faculty column. Check the row(s) ` +
                    `you want to try again and use "Re-generate Selected", or use Generate Schedule to ` +
                    `retry the whole section.`,
                    'info'
                );
            } else {
                currentScheduleData = priorScheduleData;
                currentBatchId      = priorBatchId;
                currentEvaluation   = priorEvaluation;
                renderTable(currentScheduleData, sortSelect.value);
                updateTitleBar();
            }
            return;
        }

        // INVALID_RESULT / GENERATION_ERROR — the only cases that still use the
        // plain failure dialog. INVALID_RESULT additionally names which
        // component(s) remain invalid, since CSP prevalidation should make this
        // rare/transient rather than the normal incomplete-schedule outcome.
        const extra = (status === 'INVALID_RESULT' && Array.isArray(data.violations) && data.violations.length)
            ? '<br><br>' + data.violations.map(v => v.detail || v.rule || '').filter(Boolean).join('<br>')
            : '';
        await showInfo(
            isRegenerate ? 'Regeneration Failed' : 'Generation Failed',
            (data.error || 'Could not generate schedule.') + extra,
            'error'
        );
    }

    if (btnSelectIncomplete) {
        btnSelectIncomplete.addEventListener('click', () => {
            // Check every incomplete row and preserve NOTHING on it (empty
            // preserve set == full regeneration of that row), matching the
            // existing "checked row is locked only on ticked fields" semantics.
            currentScheduleData.forEach(cls => {
                if (cls.incomplete) {
                    rowSelectionState.set(rowKeyOf(cls), { selected: true, preserve: {} });
                }
            });
            renderTable(currentScheduleData, sortSelect.value);
            _updateRegenerateButtonLabel();
        });
    }

    btnGenerate.addEventListener('click', async () => {
        if (_generating) return; // #13: prevent double-generation
        const ctx = getContext();

        if (!ctx.acadYear || !ctx.term || !ctx.program || !ctx.yearLevel) {
            await showInfo('Missing Fields', 'Please select all required fields before generating.', 'error');
            return;
        }

        // Clear any previous saved state — a fresh generate replaces it
        try { localStorage.removeItem(_LS_KEY); } catch(e) {}
        rowSelectionState.clear(); // a full regenerate replaces every row — stale selections would misapply to the new table

        const isRetrieve = useHistorical.checked;
        _generating = true;

        btnGenerate.disabled = true;
        btnGenerate.innerHTML = isRetrieve
            ? '<i class="fas fa-spinner fa-spin"></i> Retrieving...'
            : '<i class="fas fa-spinner fa-spin"></i> Generating...';

        document.querySelector('#loadingModal h2').textContent =
            isRetrieve ? 'RETRIEVING PREVIOUS SCHEDULE' : 'GENERATING SCHEDULE';
        document.querySelector('#loadingModal .loading-subtitle').textContent =
            isRetrieve
                ? 'Loading saved schedule from database...'
                : 'Detecting conflicts & applying constraints...';

        showLoading();

        try {
            if (isRetrieve) {
                const res = await fetch('/api/schedule/retrieve-previous', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({
                        program:   ctx.program,
                        yearLevel: ctx.yearLevel,
                        term:      ctx.term,
                        acadYear:  ctx.acadYear,
                    }),
                });
                const data = await res.json();
                completeProgress();

                if (data.success) {
                    applyScheduleResult(data, true);
                    // #10: Show notice if any overloaded historical faculty were replaced with TBA
                    const notices = data.overload_notices || [];
                    if (notices.length > 0) {
                        await showInfo(
                            'Faculty Load Notice',
                            'Some faculty from the previous schedule are already at their load limit '
                            + 'for this term and have been replaced with <strong>TBA</strong>:<br><br>'
                            + notices.map(n => `• ${n}`).join('<br>'),
                            'info'
                        );
                    }
                } else {
                    const proceed = await showInfo(
                        'No Previous Schedule Found',
                        (data.error || 'No saved schedule found for this selection.')
                        + '<br><br>Would you like to <strong>generate a new schedule</strong> instead?',
                        'confirm'
                    );
                    if (proceed) {
                        useHistorical.checked = false;
                        btnGenerate.click();
                    }
                }
            } else {
                _generateController = new AbortController();
                const res = await fetch('/api/schedule/generate', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({
                        program:    ctx.program,
                        yearLevel:  ctx.yearLevel,
                        term:       ctx.term,
                        curriculum: curriculum.value,
                        section:    ctx.section,
                        acadYear:   ctx.acadYear,  // #9: needed for faculty load lookup
                    }),
                    signal: _generateController.signal,
                });
                _generateController = null;
                const data = await res.json();
                completeProgress();
                await _handleGenerationResult(data, false);
            }
        } catch (e) {
            if (e.name === 'AbortError') return; // user hit Cancel — button already reset by cancel handler
            completeProgress();
            await showInfo('Error', 'Connection error. Please try again.', 'error');
        } finally {
            _generateController = null;
            _generating = false; // #13: release lock
            btnGenerate.disabled = false;
            btnGenerate.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> GENERATE SCHEDULE';
            document.querySelector('#loadingModal h2').textContent = 'GENERATING SCHEDULE';
            document.querySelector('#loadingModal .loading-subtitle').textContent =
                'Detecting conflicts & applying constraints...';
        }
    });

    // Copies a gene's own fields for the locked_sessions payload below. No
    // start_time/end_time here — _serialize_class (server-side) strips raw
    // time objects before they ever reach the client, so only the display
    // strings (time/days) + days_list/day travel back; _rehydrate_schedule
    // (server-side, reused for this payload too) already knows how to parse
    // start_time/end_time back out of the 'time' string.
    function _pluckGeneFields(cls) {
        const keys = ['subject_code', 'class_type', 'course', 'description', 'lec_hours', 'lab_hours',
                      'units', 'duration_hrs', 'total_subject_hrs', 'faculty_id', 'instructor',
                      'room_id', 'room', 'room_type', 'days_list', 'day', 'time', 'days',
                      'incomplete', 'incomplete_reason'];
        const out = {};
        keys.forEach(k => { if (cls[k] !== undefined) out[k] = cls[k]; });
        return out;
    }

    btnRegenerate.addEventListener('click', async () => {
        const selectedKeys = [...rowSelectionState.entries()].filter(([, v]) => v.selected).map(([k]) => k);
        if (selectedKeys.length === 0) { btnGenerate.click(); return; } // nothing checked → same as full generate

        if (_generating) return; // #13: prevent double-generation
        const ctx = getContext();
        if (!ctx.acadYear || !ctx.term || !ctx.program || !ctx.yearLevel) {
            await showInfo('Missing Fields', 'Please select all required fields before generating.', 'error');
            return;
        }

        // Build the lock list: an unchecked row is fully preserved (faculty +
        // room + schedule all locked, exactly as it currently is); a checked
        // row is locked only on the fields whose own "Preserve" box is ticked.
        const locked_sessions = [];
        currentScheduleData.forEach(cls => {
            const key     = rowKeyOf(cls);
            const state   = rowSelectionState.get(key);
            const preserve = (state && state.selected)
                ? (state.preserve || {})
                : { faculty: true, room: true, schedule: true };
            if (preserve.faculty || preserve.room || preserve.schedule) {
                locked_sessions.push({
                    row_key: key,
                    lock: { faculty: !!preserve.faculty, room: !!preserve.room, schedule: !!preserve.schedule },
                    ..._pluckGeneFields(cls),
                });
            }
        });

        _generating = true;
        btnRegenerate.disabled  = true;
        btnRegenerate.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Regenerating...';
        document.querySelector('#loadingModal h2').textContent = 'REGENERATING SELECTED';
        document.querySelector('#loadingModal .loading-subtitle').textContent =
            'Preserving locked rows & fields, regenerating the rest...';
        showLoading();

        try {
            _generateController = new AbortController();
            const res = await fetch('/api/schedule/generate', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({
                    program:    ctx.program,
                    yearLevel:  ctx.yearLevel,
                    term:       ctx.term,
                    curriculum: curriculum.value,
                    section:    ctx.section,
                    acadYear:   ctx.acadYear,
                    locked_sessions,
                }),
                signal: _generateController.signal,
            });
            _generateController = null;
            const data = await res.json();
            completeProgress();

            // Note: rowSelectionState is deliberately NOT cleared here (whether the
            // result is complete, partial, or discarded), so checked rows/preserved
            // fields stay visibly checked and the user can immediately chain
            // another selective/targeted regenerate.
            await _handleGenerationResult(data, true);
        } catch (e) {
            if (e.name === 'AbortError') return; // user hit Cancel — button already reset by cancel handler
            completeProgress();
            await showInfo('Error', 'Connection error. Please try again.', 'error');
        } finally {
            _generateController = null;
            _generating = false; // #13: release lock
            btnRegenerate.disabled  = false;
            _updateRegenerateButtonLabel(); // "Re-generate selected" vs plain "Re-generate", based on current selection
            document.querySelector('#loadingModal h2').textContent = 'GENERATING SCHEDULE';
            document.querySelector('#loadingModal .loading-subtitle').textContent =
                'Detecting conflicts & applying constraints...';
        }
    });

    const btnCancelGenerate = document.getElementById('btnCancelGenerate');
    if (btnCancelGenerate) {
        btnCancelGenerate.addEventListener('click', () => {
            if (_generateController) {
                _generateController.abort();
                _generateController = null;
            }
            if (btnGenerate._progressInterval) {
                clearInterval(btnGenerate._progressInterval);
                btnGenerate._progressInterval = null;
            }
            hideLoading();
            _generating = false;
            btnGenerate.disabled = false;
            btnGenerate.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> GENERATE SCHEDULE';
        });
    }

    // ── Export modal wiring ──────────────────────────────────────────────
    const exportModal    = document.getElementById('exportModal');
    const exportFilename = document.getElementById('exportFilename');
    const btnDoExport    = document.getElementById('btnDoExport');
    const fmtCards       = exportModal ? exportModal.querySelectorAll('.export-fmt-card') : [];

    fmtCards.forEach(card => {
        card.addEventListener('click', () => {
            fmtCards.forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            card.querySelector('input[type=radio]').checked = true;
        });
    });

    btnExport.addEventListener('click', () => {
        if (!currentScheduleData.length || !exportModal) return;
        const ctx = getContext();
        const semLabel = { A: '1stSem', B: '2ndSem', C: 'Summer' }[ctx.term] || ctx.term;
        exportFilename.value = `Schedule_${ctx.program || 'Program'}_Year${ctx.yearLevel || '?'}_${ctx.acadYear || 'AY'}_${semLabel}`;
        exportModal.classList.remove('hidden');
    });

    if (btnDoExport) {
        btnDoExport.addEventListener('click', async () => {
            if (!currentScheduleData.length) return;
            const ctx    = getContext();
            const selRad = exportModal.querySelector('input[name=exportFmt]:checked');
            const fmt    = selRad ? selRad.value : 'csv';
            const fname  = exportFilename.value.trim() || 'schedule_export';

            btnDoExport.disabled = true;
            btnDoExport.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Exporting...';
            try {
                const res = await fetch('/api/schedule/export-generated', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        schedule_data: currentScheduleData,
                        format:        fmt,
                        filename:      fname,
                        context:       ctx,
                    }),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    await showInfo('Export Failed', err.error || 'Could not generate export file.', 'error');
                    return;
                }
                const blob = await res.blob();
                const url  = URL.createObjectURL(blob);
                const a    = document.createElement('a');
                const extMap = { pdf:'.pdf', docx:'.docx', xlsx:'.xlsx', csv:'.csv' };
                a.href     = url;
                a.download = fname + (extMap[fmt] || '.csv');
                a.click();
                URL.revokeObjectURL(url);
                exportModal.classList.add('hidden');
            } catch (e) {
                await showInfo('Error', 'Connection error during export.', 'error');
            } finally {
                btnDoExport.disabled = false;
                btnDoExport.innerHTML = '<i class="fas fa-download"></i> Export';
            }
        });
    }

    btnSaveDraft.addEventListener('click', async () => {
        if (!currentScheduleData.length) return;

        const ctx = getContext();

        // ── Ask for consent before silently replacing an existing Draft/Published ──
        let existingCheck = { exists: false };
        try {
            const _cr = await fetch('/api/schedule/check-existing', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify(ctx),
            });
            existingCheck = await _cr.json();
        } catch (_ce) { /* network error — treat as no existing */ }

        if (existingCheck.exists) {
            const _statusLabel = existingCheck.status || 'existing';
            const _countStr    = existingCheck.subject_count
                ? ` with <strong>${existingCheck.subject_count}</strong> subject(s)`
                : '';

            const _confirmBtn = document.getElementById('infoModalConfirmBtn');
            const _prevLabel  = _confirmBtn.textContent;
            _confirmBtn.textContent = 'Continue';

            const _proceed = await showInfo(
                'Existing Schedule Detected',
                `This section already has a <strong>${_statusLabel}</strong> schedule${_countStr}.<br><br>`
                + '<strong>Cancel</strong> &mdash; stop and keep the existing schedule as is.<br>'
                + '<strong>Continue</strong> &mdash; archive it and save this as the new Draft.',
                'confirm'
            );
            _confirmBtn.textContent = _prevLabel;

            if (!_proceed) return;

            // Override replaces the entire schedule — archive both the old Draft AND the
            // old Published so this subject shows as DRAFT only, not a stale PUB/DRAFT combo.
            try {
                await fetch('/api/schedule/archive-draft-for-editor', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify(ctx),
                });
            } catch (_ae) { /* archive failure is non-blocking */ }
        }

        btnSaveDraft.disabled = true;
        btnSaveDraft.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';

        try {
            const res  = await fetch('/api/schedule/save-draft', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    batch_id:      currentBatchId,
                    schedule_data: currentScheduleData,
                    context:       ctx,
                }),
            });
            const data = await res.json();

            if (data.success) {
                await showInfo(
                    'Draft Saved!',
                    `Schedule saved as <strong>Draft V${data.draft_version}</strong>.<br>You can edit it later from <em>View Drafts</em>.`,
                    'success'
                );
                // The generated schedule stays on screen after saving — just a
                // confirmation, not a full page reset. Same behavior Approve already
                // has (see _submitApproval above). Selecting a different Program to
                // generate for still clears/replaces this normally via Generate Schedule.
                btnSaveDraft.disabled  = true;
                btnSaveDraft.innerHTML = '<i class="fas fa-check-circle"></i> Saved as Draft';
                return; // skip finally re-enable below — button intentionally stays "Saved"
            } else {
                // #11: Load violations get a dedicated message listing each faculty
                const lvs = data.load_violations || [];
                if (lvs.length) {
                    const details = lvs.map(v =>
                        `• ${v.type || 'Conflict: Faculty Load'} — ${v.faculty_name}: ${v.total_load}/${v.max_load} units (+${v.overload_by} over limit)`
                    ).join('<br>');
                    await showInfo('Conflict: Faculty Load',
                        'Cannot save draft — the following faculty exceed their load limit '
                        + 'across all assigned sections this term:<br><br>' + details,
                        'error');
                } else {
                    await showInfo('Save Failed', data.error || 'Could not save draft.', 'error');
                }
            }
        } catch (e) {
            await showInfo('Error', 'Connection error. Please try again.', 'error');
        } finally {
            btnSaveDraft.disabled = false;
            btnSaveDraft.innerHTML = '<i class="fas fa-save"></i> Save as Draft';
        }
    });

    btnManualEditor.addEventListener('click', async () => {
        if (!currentScheduleData.length) {
            window.location.href = MANUAL_EDITOR_URL;
            return;
        }
        const ctx = getContext();

        btnManualEditor.disabled = true;
        btnManualEditor.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Checking...';

        try {
            // ── Step 1: check for an existing Draft / Published schedule for this section ──
            let existingCheck = { exists: false };
            try {
                const _cr = await fetch('/api/schedule/check-existing', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify(ctx),
                });
                existingCheck = await _cr.json();
            } catch (_ce) { /* network error — treat as no existing */ }

            if (existingCheck.exists) {
                const _statusLabel = existingCheck.status || 'existing';
                const _countStr    = existingCheck.subject_count
                    ? ` with <strong>${existingCheck.subject_count}</strong> subject(s)`
                    : '';

                const _confirmBtn = document.getElementById('infoModalConfirmBtn');
                const _prevLabel  = _confirmBtn.textContent;
                _confirmBtn.textContent = 'Override';

                const _doOverride = await showInfo(
                    'Existing Schedule Detected',
                    `This section already has a <strong>${_statusLabel}</strong> schedule${_countStr}.<br><br>`
                    + '<strong>Cancel</strong> &mdash; stay on Generate Schedule and keep the existing schedule.<br>'
                    + '<strong>Override</strong> &mdash; archive the existing schedule and open Manual Editor with the generated schedule.',
                    'confirm'
                );
                _confirmBtn.textContent = _prevLabel;

                if (!_doOverride) {
                    btnManualEditor.disabled = false;
                    btnManualEditor.innerHTML = '<i class="fas fa-edit"></i> Go to Manual Editor';
                    return;
                }

                // Archive the existing Draft for this section before navigating
                try {
                    await fetch('/api/schedule/archive-draft-for-editor', {
                        method:  'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body:    JSON.stringify(ctx),
                    });
                } catch (_ae) { /* archive failure is non-blocking */ }
            }

            // ── Step 2: save generator sessions as Draft to DB ──
            // This gives Manual Editor full editing capabilities (delete, drag-and-drop, etc.).
            // If the save fails for any reason, fall back to sessionStorage so the sessions
            // are still visible via the generator overlay approach.
            btnManualEditor.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving Draft...';
            let _savedAsDraft = false;
            try {
                const _sr = await fetch('/api/schedule/save-draft', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({
                        batch_id:      currentBatchId,
                        schedule_data: currentScheduleData,
                        context:       ctx,
                    }),
                });
                const _sd = await _sr.json();
                _savedAsDraft = !!_sd.success;
            } catch (_se) { _savedAsDraft = false; }

            if (_savedAsDraft) {
                // Draft saved — clear stale sessionStorage so Manual Editor loads from DB,
                // giving the user a fully editable Draft identical to one saved manually.
                try { sessionStorage.removeItem('_sched_gen_transfer'); } catch (_se) {}
            } else {
                // Fallback: pass via sessionStorage so Manual Editor can still show sessions.
                try {
                    sessionStorage.setItem('_sched_gen_transfer', JSON.stringify({
                        schedule_data: currentScheduleData,
                        context:       ctx,
                    }));
                } catch (_se) { /* sessionStorage unavailable — proceed anyway */ }
            }

            btnManualEditor.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Opening...';
            const sectName = sectionFilter.options[sectionFilter.selectedIndex]?.text || '';
            const url = MANUAL_EDITOR_URL
                + `?mode=program`
                + `&prog=${encodeURIComponent(ctx.program)}`
                + `&yl=${encodeURIComponent(ctx.yearLevel)}`
                + `&ay=${encodeURIComponent(ctx.acadYear)}`
                + `&sem=${encodeURIComponent(ctx.term)}`
                + `&sect=${encodeURIComponent(ctx.section)}`
                + `&sect_name=${encodeURIComponent(sectName)}`
                + `&from_generator=1`;
            window.location.href = url;
        } catch (e) {
            btnManualEditor.disabled = false;
            btnManualEditor.innerHTML = '<i class="fas fa-edit"></i> Go to Manual Editor';
        }
    });

    // Shared helper: call /api/schedule/approve and handle all response cases.
    // override=true skips the duplicate-schedule confirmation gate on the server.
    async function _submitApproval(override) {
        btnApprove.disabled = true;
        btnApprove.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Publishing...';

        const ctx = getContext();
        try {
            const res  = await fetch('/api/schedule/approve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    batch_id:      currentBatchId,
                    schedule_data: currentScheduleData,
                    context:       ctx,
                    override:      override,
                }),
            });
            const data = await res.json();

            if (data.success) {
                const draftNote = data.draft_version
                    ? `<br>Draft <strong>V${data.draft_version}</strong> has been preserved for further editing.`
                    : '';
                await showInfo(
                    'Published!',
                    `Schedule published as <strong>V${data.published_version}</strong>.${draftNote}`,
                    'success'
                );
                btnApprove.disabled = true;
                btnApprove.innerHTML = '<i class="fas fa-check-circle"></i> Published';

            } else if (data.needs_confirmation) {
                // An existing Published schedule was found — notify, then auto-override
                // (same behavior as Save Draft: the prior version is archived automatically).
                const ei       = data.existing_info || {};
                const dateStr  = ei.date         ? ` (published on ${ei.date})`                       : '';
                const subjStr  = ei.subject_count ? ` with <strong>${ei.subject_count}</strong> subjects` : '';

                await showInfo(
                    'Existing Schedule Found',
                    `This section already has an approved schedule${subjStr}${dateStr}.<br>`
                    + 'It will be archived and replaced by the schedule you are publishing now.',
                    'info'
                );

                await _submitApproval(true);

            } else {
                // #11: Show per-faculty load violation details when applicable
                const lvs = data.load_violations || [];
                if (lvs.length) {
                    const details = lvs.map(v =>
                        `• ${v.type || 'Conflict: Faculty Load'} — ${v.faculty_name}: ${v.total_load}/${v.max_load} units (+${v.overload_by} over limit)`
                    ).join('<br>');
                    await showInfo('Conflict: Faculty Load',
                        'Cannot approve — the following faculty exceed their load limit '
                        + 'across all assigned sections this term:<br><br>' + details,
                        'error');
                } else {
                    await showInfo('Publish Failed', data.error || 'Could not publish schedule.', 'error');
                }
                btnApprove.disabled = false;
                btnApprove.innerHTML = '<i class="fas fa-check-circle"></i> Approve Schedule';
            }
        } catch (e) {
            await showInfo('Error', 'Connection error. Please try again.', 'error');
            btnApprove.disabled = false;
            btnApprove.innerHTML = '<i class="fas fa-check-circle"></i> Approve Schedule';
        }
    }

    btnApprove.addEventListener('click', async () => {
        if (!currentScheduleData.length) return;

        const confirmed = await showInfo(
            'Approve & Publish Schedule?',
            'This will <strong>publish</strong> this schedule as the active version.<br>The previous published version will be archived.',
            'confirm'
        );
        if (!confirmed) return;

        await _submitApproval(false);
    });

    window.loadDraft = async function(versionId) {
        try {
            const res = await fetch(`/api/schedule/load-draft/${versionId}`);
            const data = await res.json();

            if (data.success) {
                document.getElementById('draftsModal').classList.add('hidden');

                currentScheduleData = data.schedule_data || [];
                currentBatchId = `DRAFT-${versionId}`;

                if (data.context) {
                    acadYear.value  = data.context.acadYear  || '';
                    term.value      = data.context.term      || '';
                    program.value   = data.context.program   || '';
                    yearLevel.value = data.context.yearLevel || '';
                }

                renderTable(currentScheduleData, sortSelect.value);
                updateTitleBar();

                btnRegenerate.disabled   = false;
                btnSaveDraft.disabled    = false;
                btnSaveDraft.innerHTML   = '<i class="fas fa-save"></i> Save as Draft';
                btnManualEditor.disabled = false;
                btnExport.disabled       = false;
                btnApprove.disabled      = true;

                await showInfo('Draft Loaded', `Draft V${data.version} has been loaded successfully.`, 'success');
            } else {
                await showInfo('Load Failed', data.error || 'Could not load draft.', 'error');
            }
        } catch (e) {
            await showInfo('Error', 'Failed to load draft. Please try again.', 'error');
        }
    };

    window.openDraftsModal = async function() {
        const modal = document.getElementById('draftsModal');
        const body  = document.getElementById('draftsModalBody');
        modal.classList.remove('hidden');
        body.innerHTML = '<p class="empty-modal-msg"><i class="fas fa-spinner fa-spin"></i> Loading drafts...</p>';

        try {
            const res  = await fetch('/api/schedule/drafts');
            const data = await res.json();

            if (!Array.isArray(data) || !data.length) {
                body.innerHTML = '<p class="empty-modal-msg"><i class="fas fa-folder-open"></i> No drafts saved yet.</p>';
                return;
            }

            body.innerHTML = `
                <table class="modal-table">
                    <thead><tr><th>Program</th><th>Year</th><th>Term</th><th>A.Y.</th><th>Version</th><th>Saved</th><th>Action</th></tr></thead>
                    <tbody>
                        ${data.map(d => `
                            <tr>
                                <td>${d.programcode || '-'}</td>
                                <td>Year ${d.yearlevel || '-'}</td>
                                <td>${d.term || '-'}</td>
                                <td>${d.acadyear || '-'}</td>
                                <td><span class="badge draft">Draft V${d.version_number}</span></td>
                                <td>${d.datecreated ? new Date(d.datecreated).toLocaleDateString() : '-'}</td>
                                <td><button class="btn-load-draft" onclick="loadDraft(${d.versionid})"><i class="fas fa-folder-open"></i> Load</button></td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        } catch (e) {
            body.innerHTML = '<p class="empty-modal-msg error-msg"><i class="fas fa-times-circle"></i> Failed to load drafts.</p>';
        }
    };

    window.openVersionHistoryModal = async function() {
        const modal = document.getElementById('versionModal');
        const body  = document.getElementById('versionModalBody');
        modal.classList.remove('hidden');
        body.innerHTML = '<p class="empty-modal-msg"><i class="fas fa-spinner fa-spin"></i> Loading history...</p>';

        try {
            const res  = await fetch('/api/schedule/versions');
            const data = await res.json();

            if (!Array.isArray(data) || !data.length) {
                body.innerHTML = '<p class="empty-modal-msg"><i class="fas fa-history"></i> No version history yet.</p>';
                return;
            }

            const statusClass = { Published: 'published', Draft: 'draft', Archive: 'archive' };

            body.innerHTML = `
                <table class="modal-table">
                    <thead><tr><th>Program</th><th>Year</th><th>Term</th><th>A.Y.</th><th>Version</th><th>Status</th><th>Date</th><th>Action</th></tr></thead>
                    <tbody>
                        ${data.map(d => `
                            <tr>
                                <td>${d.programcode || '-'}</td>
                                <td>Year ${d.yearlevel || '-'}</td>
                                <td>${d.term || '-'}</td>
                                <td>${d.acadyear || '-'}</td>
                                <td>V${d.version_number}</td>
                                <td><span class="badge ${statusClass[d.status] || ''}">${d.status}</span></td>
                                <td>${d.datecreated ? new Date(d.datecreated).toLocaleDateString() : '-'}</td>
                                <td>${d.status === 'Draft' ? `<button class="btn-load-draft" onclick="loadDraft(${d.versionid})"><i class="fas fa-folder-open"></i> Load</button>` : '-'}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        } catch (e) {
            body.innerHTML = '<p class="empty-modal-msg error-msg"><i class="fas fa-times-circle"></i> Failed to load version history.</p>';
        }
    };

    document.querySelectorAll('.modal-overlay').forEach(overlay => {
        overlay.addEventListener('click', e => {
            if (e.target === overlay) overlay.classList.add('hidden');
        });
    });

    // #14: Re-sync button states when the user returns to this browser tab.
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') return;
        // Guarantee button enable/disable is consistent with current page data.
        const hasSchedule = currentScheduleData.length > 0;
        if (hasSchedule) {
            btnRegenerate.disabled   = false;
            btnSaveDraft.disabled    = false;
            btnManualEditor.disabled = false;
            btnExport.disabled       = false;
        }
        // Re-validate the generate button in case dropdown state was lost.
        checkFormValidity();
    });

    // Always start with a clean slate — clear any schedule left from a previous session.
    try { localStorage.removeItem(_LS_KEY); } catch(e) {}
});
