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
    // three scheduler-controlled fields (faculty = Instructor, schedule =
    // Time/Days, room = Room) are LOCKED (see renderTable() and btnRegenerate
    // below). A newly checked row starts with all three UNLOCKED (regenerate
    // the whole assignment); the user locks only the values to preserve.
    // Matches the ORIGINAL table grouping (subject + faculty) rather than
    // splitting a subject's Lecture/Lab into separate rows, so a subject
    // taught by one instructor still shows as one row with one checkbox —
    // that checkbox's selected/lock state is shared by every underlying
    // gene (Lecture and/or Lab) it represents when the "Re-generate selected"
    // payload is built. Trade-off: if a regenerate changes the instructor for
    // a subject that was left unchecked, its key changes too, so a *previous*
    // selection on that subject (if any) won't carry forward — acceptable,
    // since an unchecked row is meant to stay untouched anyway.
    const rowSelectionState = new Map(); // rowKey -> { selected, locks: {faculty, schedule, room} }
    function rowKeyOf(cls) {
        return (cls.subject_code || '') + '||' + (cls.faculty_id || cls.instructor || '');
    }
    const REGEN_FIELDS = ['faculty', 'schedule', 'room'];
    // Component names from any source -> the three UI locks. Time/Days is ONE
    // lock: day and/or time (under any spelling) both map to 'schedule'.
    const _COMPONENT_TO_LOCK = {
        instructor: 'faculty', faculty: 'faculty', room: 'room',
        day: 'schedule', days: 'schedule', time: 'schedule', time_day: 'schedule',
        time_days: 'schedule', day_time: 'schedule', section: 'schedule', schedule: 'schedule',
        subject: 'subject',
    };
    const _toLockFields = (names) => [...new Set((names || [])
        .map(n => _COMPONENT_TO_LOCK[String(n).trim().toLowerCase()]).filter(Boolean))];
    const allLocked   = () => ({ faculty: true,  schedule: true,  room: true  });
    const allUnlocked = () => ({ faculty: false, schedule: false, room: false });

    // ── Conflicts → rows ──────────────────────────────────────────────────────
    // The evaluation's de-duplicated `conflicts` list (see _build_conflict_list
    // server-side) is the single source for the conflict panel, its count, the
    // row indicators and Select Conflict Rows. Each conflict names the rows it
    // targets (subject code, plus faculty id for faculty-load rules) and the
    // lock(s) the solver should be allowed to change (resolution_components).
    function _currentConflicts() {
        const ev = currentEvaluation;
        return (ev && ev.success !== false && Array.isArray(ev.conflicts)) ? ev.conflicts : [];
    }
    function _targetHits(t, code, facultyIds) {
        return (t.subject_code || '').toUpperCase() === (code || '').toUpperCase()
            && (!t.faculty_id || facultyIds.has(String(t.faculty_id).toUpperCase()));
    }
    // rowKey -> Set of locks to open ('faculty' | 'schedule' | 'room'), for
    // every row with at least one actionable hard conflict.
    function _conflictLockMap() {
        const map = new Map();
        const conflicts = _currentConflicts();
        if (!conflicts.length) return map;
        currentScheduleData.forEach(cls => {
            const fids = new Set([String(cls.faculty_id || '').toUpperCase()].filter(Boolean));
            conflicts.forEach(c => {
                if (!(c.targets || []).some(t => _targetHits(t, cls.subject_code, fids))) return;
                const key = rowKeyOf(cls);
                if (!map.has(key)) map.set(key, new Set());
                // Every component the conflict involves (normalized), so a
                // day- or time-related conflict always opens Time/Days.
                let open = _toLockFields([...(c.resolution_components || []), ...(c.affected_components || [])])
                    .filter(f => f !== 'subject');
                if (!open.length) open = REGEN_FIELDS;
                open.forEach(f => map.get(key).add(f));
            });
        });
        return map;
    }
    // rowKey -> Set of incomplete/unresolved components ('faculty' | 'schedule'
    // | 'room' | 'subject') — from the evaluation's `incomplete` list (the same
    // present-AND-valid definition the server uses everywhere: Room "TBA" or a
    // nonexistent record is unresolved). Falls back to the rows' own flags when
    // an evaluation without that list is on screen; a row flagged incomplete
    // with no component detail counts all three as unresolved.
    function _incompleteMap() {
        const map = new Map();
        const ev = currentEvaluation;
        const list = (ev && ev.success !== false && Array.isArray(ev.incomplete)) ? ev.incomplete : null;
        currentScheduleData.forEach(cls => {
            const key = rowKeyOf(cls);
            let comps = [];
            if (list) {
                const fids = new Set([String(cls.faculty_id || '').toUpperCase()].filter(Boolean));
                list.forEach(e => {
                    if ((e.targets || []).some(t => _targetHits(t, cls.subject_code, fids))) comps = comps.concat(e.components || []);
                });
            } else if (cls.incomplete || (cls.incomplete_components || []).length) {
                comps = (cls.incomplete_components && cls.incomplete_components.length)
                    ? cls.incomplete_components : REGEN_FIELDS;
            }
            if (!comps.length) return;
            if (!map.has(key)) map.set(key, new Set());
            _toLockFields(comps).forEach(c => map.get(key).add(c));
        });
        return map;
    }
    // Everything wrong with each row, whichever button selected it:
    // rowKey -> { conflict: Set, incomplete: Set }.
    function _rowProblems() {
        const conflictMap = _conflictLockMap(), incompleteMap = _incompleteMap(), out = new Map();
        new Set([...conflictMap.keys(), ...incompleteMap.keys()]).forEach(key => out.set(key, {
            conflict:   conflictMap.get(key)   || new Set(),
            incomplete: incompleteMap.get(key) || new Set(),
        }));
        return out;
    }
    // Default locks when a row becomes selected — decided by the row's complete
    // validation state, never by which button selected it. A row with an
    // incomplete Instructor/Time/Days/Room component, or with no problem at
    // all, starts with all three UNLOCKED (the Academic Head locks what to
    // keep). A conflict-only row unlocks its conflicting component(s) and
    // AUTO-keeps the valid ones (preserved if possible; the server may release
    // them progressively).
    function _defaultLocksFor(key, problems) {
        const p = (problems || _rowProblems()).get(key);
        if (!p) return allUnlocked();
        if (REGEN_FIELDS.some(f => p.incomplete.has(f))) return allUnlocked();
        const open = new Set([...p.conflict, ...p.incomplete]);
        if (!REGEN_FIELDS.some(f => open.has(f))) return allUnlocked();
        return { faculty: !open.has('faculty'), schedule: !open.has('schedule'), room: !open.has('room') };
    }
    // prepareRowForRegeneration: the ONE preparation every selection path uses
    // (row checkbox, Select Conflict Rows, Select Incomplete, Select All).
    // components_to_regenerate = conflict ∪ incomplete (unlocked); every other
    // component is AUTO-kept. On a row that is already selected it reconciles:
    // AUTO states are recomputed from the current validation state, explicit
    // USER choices are kept — except that an unresolved value can never stay
    // locked (there is nothing valid to preserve).
    function prepareRowForRegeneration(key, problems, existing) {
        problems = problems || _rowProblems();
        const locks   = _defaultLocksFor(key, problems);
        const userSet = {};
        const unresolved = (problems.get(key) || {}).incomplete || new Set();
        if (existing && existing.selected && existing.userSet) {
            REGEN_FIELDS.forEach(f => {
                if (existing.userSet[f] && !unresolved.has(f)) {
                    locks[f] = !!existing.locks[f];
                    userSet[f] = true;
                }
            });
        }
        return { selected: true, locks, userSet };
    }

    // ── Automatic selection of incomplete rows ────────────────────────────────
    // Every newly displayed schedule (Generate, Retrieve Previous, an applied
    // Re-generate Selected result, a loaded draft) is a new schedule STATE. Once
    // per state — when its evaluation is rendered — every row with an
    // incomplete/unresolved component it can regenerate (Instructor, Time/Days,
    // Room; from the evaluation's structured `incomplete` list) is checked and
    // prepared with prepareRowForRegeneration. Conflict-only rows are not
    // auto-selected (Select Conflict Rows does that). Rows the Academic Head
    // unchecks stay unchecked for the rest of that state, until Select
    // Incomplete is clicked. Never triggers regeneration.
    let _scheduleStateId = 0;
    let _autoSelectedStateId = -1;
    const _userUncheckedKeys = new Set();
    function _beginScheduleState() {
        _scheduleStateId++;
        _userUncheckedKeys.clear();
    }
    // Checks and prepares the incomplete rows; `respectUnchecked` keeps rows
    // the user unchecked during this state unchecked. Returns true if anything changed.
    function _selectIncompleteRows(respectUnchecked) {
        const problems = _rowProblems();
        let changed = false;
        _incompleteMap().forEach((comps, key) => {
            if (!REGEN_FIELDS.some(f => comps.has(f))) return;   // e.g. subject-only: nothing to regenerate
            if (respectUnchecked && _userUncheckedKeys.has(key)) return;
            _userUncheckedKeys.delete(key);
            const prepared = prepareRowForRegeneration(key, problems, rowSelectionState.get(key));
            rowSelectionState.set(key, Object.assign(prepared, { autoSelected: true }));
            changed = true;
        });
        return changed;
    }
    function _autoSelectIncompleteOnce() {
        if (_autoSelectedStateId === _scheduleStateId) return false;
        _autoSelectedStateId = _scheduleStateId;
        return _selectIncompleteRows(true);
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
                if (ay) params.set('ay_id', ay);
                const res  = await fetch(`/api/get_curriculum?${params}`);
                const data = await res.json();
                if (data.success && data.curriculum_year) {
                    curriculum.value = data.curriculum_year;
                    curriculumText.textContent = `CY ${data.curriculum_year}`;
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
        _refreshApprovalGate();   // no schedule: disabled, no reason shown
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
            currentEvaluation        = null;
            _refreshApprovalGate();   // re-validated once the evaluation loads

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
                currentEvaluation        = null;
                _refreshApprovalGate();   // re-validated below once the evaluation loads

                updateEvaluationWidget(currentScheduleData, getContext());
            }
        } catch(_e) {}
    }

    // opts.confirmLabel/opts.cancelLabel let a caller relabel the two buttons
    // (e.g. "View Partial Schedule" / "Discard Results" for type 'partial')
    // without touching any existing call site — both default to the original
    // "OK"/"Cancel" text so every prior showInfo(...) call keeps working as-is.
    //
    // opts.tertiaryLabel opts a caller into a THREE-button dialog (e.g. "Try
    // Again" / "Generate New Schedule" / "Cancel" for a server error, where a
    // plain OK would be wrong because the dialog is genuinely asking the user
    // to choose between distinct actions). When set, the promise resolves to
    // one of the strings 'primary' | 'secondary' | 'cancel' instead of a
    // boolean — every existing 2-button call site never sets this option, so
    // it keeps resolving true/false exactly as before.
    function showInfo(title, message, type = 'info', opts = {}) {
        return new Promise(resolve => {
            const modal      = document.getElementById('infoModal');
            const icon       = document.getElementById('infoModalIcon');
            const titleEl    = document.getElementById('infoModalTitle');
            const msgEl      = document.getElementById('infoModalMessage');
            const confirmBtn = document.getElementById('infoModalConfirmBtn');
            const cancelBtn  = document.getElementById('infoModalCancelBtn');
            const tertiaryBtn = document.getElementById('infoModalTertiaryBtn');

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

            if (opts.tertiaryLabel && tertiaryBtn) {
                tertiaryBtn.textContent = opts.tertiaryLabel;
                tertiaryBtn.classList.remove('hidden');
                cancelBtn.classList.remove('hidden');
                confirmBtn.onclick  = () => { modal.classList.add('hidden'); resolve('primary'); };
                tertiaryBtn.onclick = () => { modal.classList.add('hidden'); resolve('secondary'); };
                cancelBtn.onclick   = () => { modal.classList.add('hidden'); resolve('cancel'); };
                return;
            }
            if (tertiaryBtn) tertiaryBtn.classList.add('hidden');

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

    let _hideLoadingTimer = null;
    function showLoading() {
        // A previous run's delayed hide (completeProgress) must not close the
        // overlay of a run that starts right after it (e.g. Generate New Schedule).
        clearTimeout(_hideLoadingTimer);
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
        _hideLoadingTimer = setTimeout(hideLoading, 500);
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
                    faculty_ids:  new Set(),
                    incomplete:        false,
                    incompleteReasons: [],
                    timeUnresolved:        false,
                    timeUnresolvedReasons: [],
                };
            }
            const g = groups[key];
            if (cls.faculty_id) g.faculty_ids.add(String(cls.faculty_id).toUpperCase());
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

            // Requirement I: never present an ambiguous bare time (e.g.
            // "3:00-6:00", no AM/PM) as if it were a normal, trustworthy value —
            // the backend now marks any value it could not resolve unambiguously
            // via time_resolved/time_unresolved_reason instead of silently
            // guessing, so surface that here rather than rendering it plain.
            if (cls.time_resolved === false) {
                g.timeUnresolved = true;
                const reason = cls.time_unresolved_reason || 'unresolved';
                if (!g.timeUnresolvedReasons.includes(reason)) g.timeUnresolvedReasons.push(reason);
            }

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
            const locks = (state && state.locks) || allUnlocked();
            const userSet = (state && state.userSet) || {};
            const unresolved = _incompleteMap().get(g.rowKey) || new Set();
            // Only a checked (selected-for-regeneration) row shows its lock
            // icons — an unchecked row is fully protected as a whole, so a
            // per-field control on it would be meaningless. Closed lock =
            // keep this value; open lock = the scheduler may regenerate it.
            const FIELD_LABEL = { faculty: 'Instructor', schedule: 'Time/Days', room: 'Room' };
            // Three states behind the same two icons: USER-locked (absolute —
            // never changed), AUTO-kept (kept if possible, may be released to
            // resolve the row), unlocked. An unresolved value (Room "TBA", a
            // nonexistent record) can't be locked at all — there is nothing
            // valid to preserve.
            const lockBtn = (field) => {
                if (!sel) return '';
                if (unresolved.has(field)) {
                    const tip = `${FIELD_LABEL[field]}: unresolved value — will be regenerated`;
                    return `<button type="button" class="regen-lock is-unlocked" data-origin="unresolved" disabled
                                data-row-key="${g.rowKey}" data-field="${field}"
                                aria-pressed="false" aria-label="${tip}" title="${tip}">
                                <i class="fas fa-lock-open"></i>
                            </button>`;
                }
                const locked = !!locks[field];
                const origin = locked ? (userSet[field] ? 'user' : 'auto') : 'open';
                const tip = origin === 'user' ? 'Locked by you — this value will not be changed'
                          : origin === 'auto' ? 'Kept if possible — may be changed if needed to resolve the row'
                          : 'Available for regeneration';
                return `<button type="button" class="regen-lock ${locked ? 'is-locked' : 'is-unlocked'}" data-origin="${origin}"
                            data-row-key="${g.rowKey}" data-field="${field}"
                            aria-pressed="${locked}" aria-label="${FIELD_LABEL[field]}: ${tip}" title="${tip}">
                            <i class="fas ${locked ? 'fa-lock' : 'fa-lock-open'}"></i>
                        </button>`;
            };
            // Value + its lock icon sit side by side on one line (not stacked)
            // via .cell-with-preserve — see scheduleGeneration.css.
            const cellWithPreserve = (valueHtml, field) => `
                <div class="cell-with-preserve">
                    <span>${valueHtml}</span>${lockBtn(field)}
                </div>`;

            const _conflicts  = _currentConflicts();
            const violations  = (currentEvaluation && Array.isArray(currentEvaluation.conflicts))
                ? _conflicts.filter(c => (c.targets || []).some(t => _targetHits(t, g.subject_code, g.faculty_ids)))
                : (violBySubject[g.subject_code] || []);
            const hasConflict  = violations.length > 0;
            const violText = (v) => (typeof v === 'string') ? v : (v.detail || v.rule_name || v.rule || 'Conflict detected');

            // Place each violation's indicator on the cell(s) it actually affects
            // (per its affected_components — e.g. a room conflict marks the Room
            // cell, a faculty conflict marks Instructor) instead of defaulting
            // every violation type onto the Instructor column. A violation with
            // no affected_components (older/unrecognized shape) falls back to the
            // generic row-level flag, so nothing is silently dropped.
            const COMPONENT_TO_CELL = {
                instructor: 'instructor', faculty: 'instructor',
                room: 'room', section: 'time', day: 'days', time: 'time',
            };
            const byCell = { instructor: [], room: [], time: [], days: [] };
            const rowLevelOnly = [];
            violations.forEach(v => {
                const comps = (v && typeof v === 'object' && Array.isArray(v.affected_components))
                    ? v.affected_components : null;
                const cells = comps ? new Set(comps.map(c => COMPONENT_TO_CELL[c]).filter(Boolean)) : null;
                if (!cells || !cells.size) { rowLevelOnly.push(v); return; }
                cells.forEach(cell => byCell[cell].push(v));
            });

            // One icon per cell even when several violations affect it — the
            // tooltip/aria-label lists all of them, joined, rather than stacking
            // multiple icons in the same cell.
            const cellFlag = (cellViolations) => {
                if (!cellViolations.length) return '';
                const tip = cellViolations.map(violText).join(' | ').replace(/"/g, '&quot;');
                return `<span class="cell-conflict-flag" role="img" tabindex="0" aria-label="Conflict: ${tip}" title="${tip}">
                    <i class="fas fa-exclamation-triangle"></i>
                </span>`;
            };

            // role="img" + aria-label + title makes this reachable by keyboard (tabindex),
            // not just mouse hover, per the panel's accessibility requirement.
            const conflictTip  = rowLevelOnly.length ? rowLevelOnly.map(violText).join(' | ') : '';
            const conflictFlag = rowLevelOnly.length ? `
                <span class="row-conflict-flag" role="img" tabindex="0" aria-label="Conflict: ${conflictTip.replace(/"/g, '&quot;')}" title="${conflictTip.replace(/"/g, '&quot;')}">
                    <i class="fas fa-exclamation-triangle"></i>
                </span>` : '';

            // Incomplete marker on the cell of each unresolved component (a row
            // flagged with no component detail keeps the row-level marker).
            const _evInc = (currentEvaluation && Array.isArray(currentEvaluation.incomplete))
                ? currentEvaluation.incomplete.filter(e => (e.targets || []).some(t => _targetHits(t, g.subject_code, g.faculty_ids)))
                : [];
            const _incReasons = [...new Set(_evInc.flatMap(e => e.reasons || []).concat(g.incompleteReasons))];
            const incCell = (comp) => unresolved.has(comp) ? `
                <span class="cell-incomplete-flag" role="img" tabindex="0"
                      aria-label="Incomplete: ${_escHtml(_incReasons.join(' | ') || 'Unresolved value')}"
                      title="${_escHtml(_incReasons.join(' | ') || 'Unresolved value')}">
                    <i class="fas fa-circle-question"></i>
                </span>` : '';
            const _rowIncomplete = g.incomplete || unresolved.size > 0;
            const _knownComps = ['faculty', 'schedule', 'room', 'subject'].some(c => unresolved.has(c));
            const incompleteTip = g.incompleteReasons.join(' | ') || 'This component could not be resolved.';
            const incompleteFlag = (g.incomplete && !_knownComps) ? `
                <span class="row-incomplete-flag" role="img" tabindex="0" aria-label="Incomplete: ${incompleteTip.replace(/"/g, '&quot;')}" title="${incompleteTip.replace(/"/g, '&quot;')}">
                    <i class="fas fa-circle-question"></i>
                </span>` : '';

            return `
            <tr class="${hasConflict ? 'row-conflict' : ''} ${_rowIncomplete ? 'row-incomplete' : ''} ${sel ? 'row-selected' : ''}">
                <td class="td-check"><input type="checkbox" class="row-select" data-row-key="${g.rowKey}" ${sel ? 'checked' : ''}></td>
                <td class="td-instructor">${conflictFlag}${incompleteFlag}${incCell('faculty')}${cellFlag(byCell.instructor)}${cellWithPreserve(g.instructor, 'faculty')}</td>
                <td class="td-code">${incCell('subject')}${g.subject_code}</td>
                <td class="td-desc">${g.description}</td>
                <td class="td-num">${g.lec_hours}</td>
                <td class="td-num">${g.lab_hours}</td>
                <td class="td-num">${g.credit_units}</td>
                <td class="td-course">${g.course}</td>
                <td class="td-time">${incCell('schedule')}${cellFlag(byCell.time)}${g.timeUnresolved ? `
                    <span class="cell-conflict-flag" role="img" tabindex="0" aria-label="Time could not be resolved unambiguously: ${g.timeUnresolvedReasons.join(', ').replace(/"/g, '&quot;')}" title="Time could not be resolved unambiguously: ${g.timeUnresolvedReasons.join(', ').replace(/"/g, '&quot;')}">
                        <i class="fas fa-circle-question"></i>
                    </span>` : ''}${cellWithPreserve(g.times.map(t => `<span class="time-line">${t}</span>`).join(''), 'schedule')}</td>
                <td class="td-num">${g.lec_hours + g.lab_hours}</td>
                <td class="td-days">${cellFlag(byCell.days)}${_sortDays(g.days_set).join(' / ')}</td>
                <td class="td-room">${incCell('room')}${cellFlag(byCell.room)}${cellWithPreserve(g.rooms.join('<br>'), 'room')}</td>
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
            const key = t.dataset.rowKey;
            // Checking a row starts it with conflict-based locks (only its
            // conflicting component(s) unlocked) or, with no conflict, fully
            // unlocked; unchecking drops its temporary lock state entirely, so a
            // re-check starts from those defaults again. Neither touches the
            // row's actual assignment.
            if (t.checked) {
                _userUncheckedKeys.delete(key);
                rowSelectionState.set(key, prepareRowForRegeneration(key, null, rowSelectionState.get(key)));
            } else {
                _userUncheckedKeys.add(key);   // don't auto-check it again in this schedule state
                rowSelectionState.delete(key);
            }
            renderTable(currentScheduleData, sortSelect.value);
        }
    });

    document.getElementById('scheduleTableBody').addEventListener('click', (e) => {
        const btn = e.target.closest('.regen-lock');
        if (!btn || btn.disabled || btn.dataset.origin === 'unresolved') return;
        const state = rowSelectionState.get(btn.dataset.rowKey);
        if (!state || !state.selected) return;
        const field = btn.dataset.field;
        state.locks[field] = !state.locks[field];
        // A lock the Academic Head sets is absolute (USER-locked); unlocking
        // it again simply makes the field available for regeneration.
        state.userSet = state.userSet || {};
        state.userSet[field] = true;
        renderTable(currentScheduleData, sortSelect.value);
    });

    const chkSelectAllRows = document.getElementById('chkSelectAllRows');
    if (chkSelectAllRows) {
        chkSelectAllRows.addEventListener('change', (e) => {
            const checked = e.target.checked;
            const keys = new Set(currentScheduleData.map(rowKeyOf));
            const problems = _rowProblems();
            keys.forEach(key => {
                const state = rowSelectionState.get(key);
                if (!checked) rowSelectionState.delete(key);
                else rowSelectionState.set(key, prepareRowForRegeneration(key, problems, state));
            });
            renderTable(currentScheduleData, sortSelect.value);
        });
    }

    // ── Collapsible conflict panel ────────────────────────────────────────────
    // Rebuilt from the evaluation every time one is rendered (Generate,
    // Retrieve Previous, Re-generate Selected, draft load, re-evaluation), so its
    // count, list and actionable rows always describe the schedule on screen.
    // 0 conflicts -> hidden; 1-3 -> expanded; 4+ -> collapsed by default.
    let _conflictPanelExpanded = false;
    const _escHtml = (x) => String(x == null ? '' : x)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

    function _applyConflictToggle() {
        const list   = document.getElementById('conflictDetailList');
        const toggle = document.getElementById('btnToggleConflictDetails');
        if (list) list.classList.toggle('hidden', !_conflictPanelExpanded);
        if (toggle) {
            toggle.innerHTML = _conflictPanelExpanded
                ? '<i class="fas fa-caret-up"></i> Hide Details'
                : '<i class="fas fa-caret-down"></i> Show Details';
            toggle.setAttribute('aria-expanded', String(_conflictPanelExpanded));
        }
    }

    function _renderConflictPanel() {
        const panel = document.getElementById('conflictBanner');
        if (!panel) return;
        const conflicts = _currentConflicts();
        if (!conflicts.length) { panel.classList.add('hidden'); return; }

        const n = conflicts.length;
        document.getElementById('conflictText').textContent =
            `${n} Schedule Conflict${n === 1 ? '' : 's'}`;
        document.getElementById('conflictDetailList').innerHTML = conflicts.map(c =>
            `<li><span class="conflict-rule">${_escHtml(c.type || c.rule || 'Conflict')}</span>`
            + ` — ${_escHtml(c.detail || c.subject || '')}</li>`).join('');
        const selBtn = document.getElementById('btnSelectConflictRows');
        if (selBtn) selBtn.classList.toggle('hidden', _conflictLockMap().size === 0);
        _conflictPanelExpanded = n <= 3;
        _applyConflictToggle();
        panel.classList.remove('hidden');
    }

    // ⚠ N Incomplete Components [Select Incomplete] — hidden when none. Counts
    // unresolved components (a row missing Instructor and Room counts 2).
    function _renderIncompleteBanner() {
        const banner = document.getElementById('incompleteBanner');
        if (!banner) return;
        const map = _incompleteMap();
        const n = [...map.values()].reduce((sum, comps) => sum + comps.size, 0);
        if (!n) {
            banner.classList.add('hidden');
            const _list = document.getElementById('incompleteDetailList');
            if (_list) _list.innerHTML = '';
            return;
        }
        document.getElementById('incompleteText').textContent =
            `${n} Incomplete Component${n === 1 ? '' : 's'}`;
        // Exactly which row and which field — the same evaluation list the
        // count, the row markers and Select Incomplete come from.
        const list = document.getElementById('incompleteDetailList');
        if (list) {
            const LABEL = { faculty: 'Instructor', schedule: 'Time/Days', room: 'Room', subject: 'Subject' };
            const ev = currentEvaluation;
            const items = (ev && Array.isArray(ev.incomplete))
                ? ev.incomplete.map(e => ({ code: e.subject_code, comps: e.components || [], why: (e.reasons || [])[0] || '' }))
                : [...map.entries()].map(([key, comps]) => ({ code: key.split('||')[0], comps: [...comps], why: '' }));
            list.innerHTML = items.map(it =>
                `<li><strong>${_escHtml(it.code)}</strong> — ${_escHtml(it.comps.map(c => LABEL[c] || c).join(', '))}`
                + (it.why ? `: ${_escHtml(it.why)}` : '') + '</li>').join('');
        }
        if (btnSelectIncomplete) {
            btnSelectIncomplete.classList.toggle('hidden',
                ![...map.values()].some(comps => REGEN_FIELDS.some(f => comps.has(f))));
        }
        banner.classList.remove('hidden');
    }

    const btnToggleConflictDetails = document.getElementById('btnToggleConflictDetails');
    if (btnToggleConflictDetails) {
        btnToggleConflictDetails.addEventListener('click', () => {
            _conflictPanelExpanded = !_conflictPanelExpanded;
            _applyConflictToggle();
        });
    }

    // Checks every row with an actionable hard conflict and sets its
    // conflict-based default locks. Never regenerates — the Academic Head
    // reviews/changes the locks and then clicks Re-generate Selected.
    const btnSelectConflictRows = document.getElementById('btnSelectConflictRows');
    if (btnSelectConflictRows) {
        btnSelectConflictRows.addEventListener('click', () => {
            const problems = _rowProblems();
            _conflictLockMap().forEach((_open, key) => {
                rowSelectionState.set(key, prepareRowForRegeneration(key, problems, rowSelectionState.get(key)));
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

    // Small top-right toast (reuses #successToast); kind 'warn' = amber variant.
    function _showToast(msg, kind) {
        const toast = document.getElementById('successToast');
        if (!toast) return;
        const icon = kind === 'warn' ? 'fa-exclamation-circle' : 'fa-check-circle';
        toast.innerHTML = `<i class="fas ${icon}"></i> ${msg}`;
        toast.classList.toggle('toast-warn', kind === 'warn');
        toast.classList.remove('hidden');
        clearTimeout(_showToast._t);
        _showToast._t = setTimeout(() => toast.classList.add('hidden'), 4000);
    }

    // ── Approval gate ──────────────────────────────────────────────────────────
    // The ONE place Approve Schedule's enabled state is decided: the current
    // evaluation must say the schedule is complete (no incomplete component)
    // AND has zero blocking hard-constraint violations. Advisory notices (SC9
    // specialization, severity 'warning') are not hard violations, so they never
    // block approval. Never based on the overall score. Re-run on every
    // evaluation change (Generate, Retrieve, Re-generate Selected, draft load,
    // re-evaluation), so the button re-enables by itself once fixed. The server
    // enforces the same rule on /api/schedule/approve.
    const APPROVE_LABEL = '<i class="fas fa-check-circle"></i> Approve Schedule';
    const APPROVE_TIMEOUT_MS = 120000;
    function _approvalState() {
        const ev = currentEvaluation;
        if (!currentScheduleData.length) return { eligible: false, reason: '' };
        if (!ev || ev.success === false) {
            return { eligible: false, reason: 'The schedule must be evaluated before it can be approved.' };
        }
        const incomplete = (ev.incompleteCount || 0) > 0
            || (ev.completionRate != null && Number(ev.completionRate) < 100);
        const hard = (ev.hardViolationCount || 0) > 0;
        if (incomplete && hard) {
            return { eligible: false, reason: 'Complete all required assignments and resolve all hard-constraint violations before approval.' };
        }
        if (incomplete) return { eligible: false, reason: 'Complete all required schedule assignments before approval.' };
        if (hard)       return { eligible: false, reason: 'Resolve all hard-constraint violations before approval.' };
        if (!ev.eligibleForApproval) {
            return { eligible: false, reason: 'Complete all required schedule assignments and resolve hard-constraint violations before approval.' };
        }
        return { eligible: true, reason: '' };
    }
    function _refreshApprovalGate() {
        const st = _approvalState();
        canPublish = st.eligible;
        btnApprove.disabled = !st.eligible;
        btnApprove.title = st.reason;
        btnApprove.setAttribute('aria-label', st.reason ? `Approve Schedule — ${st.reason}` : 'Approve Schedule');
        // A disabled button shows no tooltip in some browsers — the wrapper
        // carries the same reason so hovering the disabled control explains it.
        const wrap = document.getElementById('approveWrap');
        if (wrap) wrap.title = st.reason;
    }

    function applyScheduleResult(data, isRetrieve) {
        _beginScheduleState();
        currentScheduleData = data.schedule_data || [];
        currentBatchId      = data.batch_id || 'DRAFT-NEW-001';

        // The evaluation object (when the generate response carries one) is the
        // authoritative source for Approve gating — a high weighted score never
        // overrides a CSP failure. Set it before renderTable() so row-conflict
        // highlighting reflects this result on the very first paint.
        currentEvaluation = data.evaluation || null;

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
        _refreshApprovalGate();   // disabled until an evaluation exists (see updateEvaluationWidget)

        // Incomplete components: the compact banner below the table is rebuilt
        // from the evaluation with the conflict panel (_renderIncompleteBanner).

        // historical_data is the expected (only) source of Retrieve Previous, so
        // a successful retrieval is a plain success — no fallback warning.
        _showToast(isRetrieve && data.retrieved_from
            ? `Previous AY schedule retrieved from ${data.retrieved_from.ay_label}.`
            : (currentEvaluation && !currentEvaluation.eligibleForApproval)
                ? 'Schedule generated with issues — approval stays disabled until they are resolved.'
                : 'Schedule Generated Successfully!',
            (!isRetrieve && currentEvaluation && !currentEvaluation.eligibleForApproval) ? 'warn' : undefined);

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
        _renderConflictPanel();
        _renderIncompleteBanner();
        if (!(data && data.success === false) && _autoSelectIncompleteOnce()) {
            renderTable(currentScheduleData, sortSelect.value);
        }
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
        // The 60/30/10 figure is a QUALITY score (scoreKind 'quality'); it never
        // reflects completeness or approval. A schedule that is not approvable
        // is never shown in the "good" color, however high its quality score.
        const _band = _evalBand(overall);
        pctEl.className   = 'eval-score-pct ' + (eligible ? _band : (_band === 'eval-good' ? 'eval-warn' : _band));

        // CSP pass/fail is independent of the weighted score — never let a high
        // score visually imply approval eligibility on its own.
        if (cspBadge) cspBadge.className = 'eval-csp-badge ' + (cspPassed ? 'status-pass' : 'status-fail');
        if (cspIcon)  cspIcon.className  = cspPassed ? 'fas fa-check-circle' : 'fas fa-times-circle';
        if (cspText)  cspText.textContent = cspPassed
            ? `CSP PASSED · 0 HARD-CONSTRAINT VIOLATIONS`
            : `CSP FAILED · ${violationCnt} HARD-CONSTRAINT VIOLATION${violationCnt === 1 ? '' : 'S'}`;

        if (approvalEl) approvalEl.classList.toggle('hidden', !eligible);
        // Not approvable: say so next to the score, with the reason(s).
        const readinessEl = document.getElementById('evalReadiness');
        if (readinessEl) {
            const incN  = data.incompleteComponentCount != null ? data.incompleteComponentCount : (data.incompleteCount || 0);
            const parts = [];
            if (incN) parts.push(`${incN} incomplete component${incN === 1 ? '' : 's'}`);
            if (violationCnt) parts.push(`${violationCnt} hard-constraint violation${violationCnt === 1 ? '' : 's'}`);
            readinessEl.classList.toggle('hidden', eligible);
            const txt = document.getElementById('evalReadinessText');
            if (txt) txt.textContent = eligible ? '' : 'NOT ELIGIBLE FOR APPROVAL' + (parts.length ? ' · ' + parts.join(' · ').toUpperCase() : '');
        }

        // Partial-generation support (architecture spec section 12): completion
        // rate + incomplete count, shown ahead of the category breakdown. A
        // schedule can be 100% hard-constraint-compliant among what completed
        // and still be < 100% complete — completionRate/incompleteCount and
        // eligibleForApproval are deliberately separate signals (see this
        // function's own module docstring and _compute_schedule_evaluation's).
        const completionEl = document.getElementById('evalCompletionText');
        if (completionEl) {
            const rate = (data.completionRate != null) ? Math.round(data.completionRate) : 100;
            // Components, from the same list the Incomplete banner names.
            const inc  = data.incompleteComponentCount != null ? data.incompleteComponentCount : (data.incompleteCount || 0);
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
        // The schedule changed and its evaluation is being recomputed — never
        // leave the previous schedule's conflicts on screen meanwhile.
        const _panel = document.getElementById('conflictBanner');
        if (_panel) _panel.classList.add('hidden');
        const _incBanner = document.getElementById('incompleteBanner');
        if (_incBanner) _incBanner.classList.add('hidden');
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
                    // Current AY/section: historical matches compare against the
                    // immediately previous AY, and cross-section conflicts count.
                    acad_year:     ctx.acadYear,
                    section:       ctx.section,
                    curriculum:    curriculum.value,
                }),
            });
            const data = await res.json();
            currentEvaluation = (data && data.success !== false) ? data : null;
            // Re-validate Approve gating and row highlighting now that the real
            // evaluation is in — this is the "re-validate before approving" path.
            _refreshApprovalGate();
            renderTable(currentScheduleData, sortSelect.value);
            _renderEvaluationResult(data);
        } catch (e) {
            currentEvaluation = null;
            _refreshApprovalGate();
            _renderConflictPanel();
            _renderIncompleteBanner();
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
    // onApplied: runs right before a result is actually put on screen (never
    // for a failure or a discarded partial) — Re-generate Selected uses it to
    // finish the successful rows' temporary selection/lock state.
    // Three result states (server field result_state, from the canonical
    // evaluation): COMPLETE_SUCCESS -> applied at once; PARTIAL_RESULT -> a
    // usable schedule with incomplete components and/or blocking hard
    // violations, HELD (this function's `data`, never applied) until the
    // Academic Head chooses Review or Discard; TRUE_FAILURE -> nothing usable,
    // plain "Generation Failed". Advisories never decide the state.
    function _resultState(data, status) {
        const usable = Array.isArray(data.schedule_data) && data.schedule_data.length > 0;
        if (!usable || status === 'GENERATION_ERROR') return 'TRUE_FAILURE';
        if (data.result_state) return data.result_state;
        if (status === 'COMPLETE_VALID') return 'COMPLETE_SUCCESS';
        return (status === 'PARTIAL_VALID' || status === 'INVALID_RESULT') ? 'PARTIAL_RESULT' : 'TRUE_FAILURE';
    }

    // Concise summary for the "Schedule Generated with Issues" dialog: counts,
    // then a few specific problems; advisories are listed separately (muted).
    function _issuesSummaryHtml(data) {
        const ev = data.evaluation || {};
        const inc = (data.incomplete_components || []);
        const hard = (data.blocking_violations || []);
        const adv = (data.advisories || []);
        const incCount  = ev.incompleteCount != null ? ev.incompleteCount : inc.length;
        const hardCount = ev.hardViolationCount != null ? ev.hardViolationCount : hard.length;
        const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
        const counts = [];
        if (incCount)  counts.push(plural(incCount, 'incomplete assignment', 'incomplete assignments'));
        if (hardCount) counts.push(plural(hardCount, 'hard-constraint violation', 'hard-constraint violations'));
        if (adv.length) counts.push(plural(adv.length, 'advisory notice', 'advisory notices'));
        const MAX = 5;
        const problems = inc.map(e => `${e.subject_code}: ${(e.reasons || [])[0] || 'incomplete'}`)
            .concat(hard.map(v => v.detail || v.type || v.rule || 'Hard-constraint violation'));
        let html = 'A schedule was generated, but some assignments could not be completed or still '
            + 'require correction.<br><br>You can review the generated result and save it as a draft, '
            + 'but approval is disabled until all required assignments are complete and all blocking '
            + 'constraints are resolved.';
        if (counts.length) html += `<br><br><strong>${counts.map(_escHtml).join(' · ')}</strong>`;
        if (problems.length) {
            html += '<ul class="issue-list issue-blocking">'
                + problems.slice(0, MAX).map(t => `<li>${_escHtml(t)}</li>`).join('')
                + (problems.length > MAX ? `<li>…and ${problems.length - MAX} more</li>` : '')
                + '</ul>';
        }
        if (adv.length) {
            html += '<div class="issue-advisory-title">Advisory (does not block approval):</div>'
                + '<ul class="issue-list issue-advisory">'
                + adv.slice(0, 3).map(v => `<li>${_escHtml(v.detail || v.rule || '')}</li>`).join('')
                + (adv.length > 3 ? `<li>…and ${adv.length - 3} more</li>` : '')
                + '</ul>';
        }
        return html;
    }

    async function _handleGenerationResult(data, isRegenerate, onApplied) {
        const status = data.result_status || (data.success ? 'COMPLETE_VALID' : 'GENERATION_ERROR');
        const apply = () => { if (onApplied) onApplied(); applyScheduleResult(data, false); };
        const state = _resultState(data, status);

        if (status === 'REGENERATION_PARTIAL') {
            // Some selected rows were regenerated, the rest came back unchanged
            // and stay selected with their locks for another attempt.
            apply();
            await showInfo(
                'Some Rows Could Not Be Regenerated',
                (data.error || 'Some selected rows could not be resolved.')
                + '<br><br>They are still selected so you can adjust their locks and try again.',
                'info'
            );
            return;
        }

        // True failure (nothing usable), or a Re-generate Selected that
        // resolved nothing: plain failure dialog, nothing applied.
        if (state === 'TRUE_FAILURE' || status === 'REGENERATION_INFEASIBLE') {
            const blocking = (data.blocking_violations || data.violations || [])
                .filter(v => v && v.severity !== 'warning');
            const extra = blocking.length
                ? '<br><br>' + blocking.map(v => _escHtml(v.detail || v.rule || '')).filter(Boolean).join('<br>')
                : '';
            await showInfo(
                isRegenerate ? 'Regeneration Failed' : 'Generation Failed',
                _escHtml(data.error || 'Could not generate schedule.') + extra,
                'error'
            );
            return;
        }

        // Does this usable result need the Review/Discard decision? A fresh
        // Generate: whenever it is not COMPLETE_SUCCESS. Re-generate Selected:
        // only when the solver itself could not complete (PARTIAL_VALID /
        // INVALID_RESULT) — problems left in rows the user did not select are
        // shown on the table as before, not in a dialog after every regenerate.
        const needsDecision = isRegenerate
            ? (status === 'PARTIAL_VALID' || status === 'INVALID_RESULT')
            : state === 'PARTIAL_RESULT';
        if (!needsDecision) {
            apply();
            return;
        }

        // Schedule Generated with Issues: the result stays pending (`data`) and
        // is applied ONLY on Review — Discard just drops it, so the schedule on
        // screen, the filters, selections and any saved draft are untouched.
        const review = await showInfo(
            'Schedule Generated with Issues',
            _issuesSummaryHtml(data),
            'partial',
            { confirmLabel: 'Review Incomplete Schedule', cancelLabel: 'Discard Result' }
        );
        if (review) apply();
    }

    // Select Incomplete: (re)checks every row with an incomplete/unresolved
    // component it can regenerate — including rows unchecked by hand after the
    // automatic selection — and prepares its locks from ALL of that row's
    // problems (incomplete ∪ conflicting). Never regenerates.
    if (btnSelectIncomplete) {
        btnSelectIncomplete.addEventListener('click', () => {
            _selectIncompleteRows(false);   // explicit: reselect even rows unchecked by hand
            renderTable(currentScheduleData, sortSelect.value);
            _updateRegenerateButtonLabel();
        });
    }

    // The ONE Generate Schedule workflow (button, modal follow-ups). A retrieval
    // that offers "Generate New Schedule" / "Try Again" only records the
    // follow-up; it runs after this attempt has fully released its loading
    // state and the _generating lock — re-clicking the button from inside the
    // handler was swallowed by that lock, so the user had to click again.
    // forceFresh: bypass Retrieve Previous for this run (no second retrieval,
    // so no NO_PREVIOUS_SCHEDULE modal loop). Context is always read from the
    // CURRENT filters (getContext), never from the schedule on screen.
    async function runGeneration(opts = {}) {
        if (_generating) return; // #13: prevent double-generation
        const ctx = getContext();

        if (!ctx.acadYear || !ctx.term || !ctx.program || !ctx.yearLevel) {
            await showInfo('Missing Fields', 'Please select all required fields before generating.', 'error');
            return;
        }

        // The on-screen schedule's temporary state (selections/locks, saved
        // state) is only dropped when a NEW result is actually applied — a
        // discarded partial result or a failure must leave it untouched.
        const _replaceWorkingState = () => {
            try { localStorage.removeItem(_LS_KEY); } catch(e) {}
            rowSelectionState.clear(); // a new schedule replaces every row — stale selections would misapply to it
        };

        const isRetrieve = !opts.forceFresh && useHistorical.checked;
        let followUp = null;   // 'fresh' | 'retry' — chosen in a retrieval modal
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
                        section:   ctx.section,
                        curriculum: curriculum.value,
                    }),
                });
                const data = await res.json();
                completeProgress();

                if (data.success) {
                    _replaceWorkingState();
                    applyScheduleResult(data, true);
                    // #10: current-term load check — the retrieved faculty are kept
                    // and flagged, so the Academic Head decides what to change.
                    const notices = data.overload_notices || [];
                    if (notices.length > 0) {
                        await showInfo(
                            'Faculty Load Notice',
                            'Some faculty from the previous schedule would exceed their load limit '
                            + 'for this term. Select those rows and unlock <strong>Instructor</strong> '
                            + 'to reassign them:<br><br>'
                            + notices.map(n => `• ${n}`).join('<br>'),
                            'info'
                        );
                    }
                } else {
                    // NO_PREVIOUS_SCHEDULE (the query ran fine, nothing eligible
                    // exists) is a completely different situation from a server/
                    // serialization/parse/database failure (data may well have
                    // been found — it just couldn't be prepared or returned) —
                    // conflating them as one generic "not found" dialog is
                    // exactly the bug this fixes: it used to show the raw
                    // Python exception text as if it meant "no schedule exists."
                    const code = data.error_code || 'NO_PREVIOUS_SCHEDULE';
                    if (code === 'NO_PREVIOUS_SCHEDULE') {
                        const proceed = await showInfo(
                            'No Previous Schedule Available',
                            data.error || 'No previous AY schedule was found for the selected program, '
                            + 'year level, semester, and section.',
                            'confirm',
                            { confirmLabel: 'Generate New Schedule', cancelLabel: 'Stay on Page' }
                        );
                        if (proceed) followUp = 'fresh';
                    } else {
                        // RETRIEVE_PREVIOUS_SERIALIZATION_ERROR / _PARSE_ERROR /
                        // _DATABASE_ERROR / _UNEXPECTED_ERROR — a real server-side
                        // problem, not a "nothing to retrieve" outcome.
                        const choice = await showInfo(
                            'Unable to Load Previous Schedule',
                            'The previous schedule could not be prepared for display. '
                            + 'Please try again or generate a new schedule.',
                            'confirm',
                            {
                                confirmLabel:  'Try Again',
                                tertiaryLabel: 'Generate New Schedule',
                                cancelLabel:   'Cancel',
                            }
                        );
                        if (choice === 'primary') followUp = 'retry';        // the same (retrieve) action
                        else if (choice === 'secondary') followUp = 'fresh';
                        // 'cancel' -> do nothing, stay on page
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
                await _handleGenerationResult(data, false, _replaceWorkingState);
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

        if (followUp === 'fresh') {
            // Retrieve Previous is off for this and later runs (UI + state synced).
            useHistorical.checked = false;
            checkFormValidity();
            await runGeneration({ forceFresh: true });
        } else if (followUp === 'retry') {
            await runGeneration();
        }
    }

    btnGenerate.addEventListener('click', () => runGeneration());

    // Copies a gene's own fields for the locked_sessions payload below. No
    // start_time/end_time here — _serialize_class (server-side) strips raw
    // time objects before they ever reach the client, so only the display
    // strings (time/days) + days_list/day travel back; _rehydrate_schedule
    // (server-side, reused for this payload too) already knows how to parse
    // start_time/end_time back out of the 'time' string.
    const _codeOfKey = (key) => String(key).split('||')[0].trim().toUpperCase();

    // Successful rows of a Re-generate Selected: drop their temporary
    // selection + locks (under the old key and, if the instructor changed, the
    // new one — both carry the subject code). Failed rows are left as they are.
    function _finishRegeneratedRows(regeneratedCodes, failedCodes) {
        [...rowSelectionState.keys()].forEach(key => {
            const code = _codeOfKey(key);
            if (regeneratedCodes.has(code) && !failedCodes.has(code)) rowSelectionState.delete(key);
        });
    }

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

        // A checked row with Instructor, Time/Days and Room all locked has
        // nothing to regenerate; if that's every checked row, stop here.
        const hasUnlocked = (k) => {
            const s = rowSelectionState.get(k);
            return !!(s && s.selected && REGEN_FIELDS.some(f => !s.locks[f]));
        };
        if (!selectedKeys.some(hasUnlocked)) {
            _showToast('Unlock at least one field to regenerate.', 'warn');
            return;
        }

        // Build the lock list for EVERY row so the solver sees the whole
        // schedule at once: an unchecked row (and a checked-but-fully-locked
        // row) is sent fully locked, exactly as it currently is; a checked row
        // with something unlocked carries its own per-field locks and
        // selected:true so the server knows it is a mutable assignment.
        const locked_sessions = [];
        currentScheduleData.forEach(cls => {
            const key     = rowKeyOf(cls);
            const mutable = hasUnlocked(key);
            const locks   = mutable ? rowSelectionState.get(key).locks : allLocked();
            const userSet = (mutable && rowSelectionState.get(key).userSet) || {};
            locked_sessions.push({
                row_key:  key,
                selected: mutable,
                lock: { faculty: !!locks.faculty, room: !!locks.room, schedule: !!locks.schedule },
                // Only explicit Academic Head locks are absolute; every other kept
                // field is AUTO-kept and may be released progressively server-side.
                user_lock: {
                    faculty:  !!(locks.faculty  && userSet.faculty),
                    room:     !!(locks.room     && userSet.room),
                    schedule: !!(locks.schedule && userSet.schedule),
                },
                ..._pluckGeneFields(cls),
            });
        });

        // Subject codes of the rows this request actually regenerates.
        const regeneratedCodes = new Set(selectedKeys.filter(hasUnlocked).map(_codeOfKey));

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

            // A regeneration is a finished operation for every row it resolved:
            // once the result is applied those rows are unchecked and their
            // temporary locks dropped (re-checking one later starts fresh).
            // Rows the server reports as failed — and every row of a failure or
            // a discarded partial result — keep their selection and locks for a
            // retry. Warnings then come from the new result's own evaluation.
            const failed = new Set((data.failed_subjects || []).map(c => String(c).trim().toUpperCase()));
            await _handleGenerationResult(data, true,
                () => _finishRegeneratedRows(regeneratedCodes, failed));
            // Tell the Academic Head which AUTO-kept values had to change.
            const released = data.released_locks || {};
            const _lbl = { faculty: 'Instructor', schedule: 'Time/Days', room: 'Room' };
            const notes = Object.entries(released)
                .map(([code, fields]) => `${fields.map(f => _lbl[f] || f).join(' and ')} for ${code}`);
            if (data.success && notes.length) {
                _showToast(`Also regenerated ${notes.join('; ')} to resolve ${notes.length === 1 ? 'it' : 'them'}.`);
            }
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

    // One persistence request with a timeout and a safe JSON parse. Never throws:
    // resolves to { ok, status, data, error } so callers always reset their loading state.
    const SAVE_TIMEOUT_MS = 120000;
    async function _postJson(url, body, timeoutMs = SAVE_TIMEOUT_MS) {
        const ctrl  = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), timeoutMs);
        try {
            const res = await fetch(url, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body), signal: ctrl.signal,
            });
            let data = null;
            try { data = await res.json(); } catch (_pe) { data = null; }
            if (!data || typeof data !== 'object') {
                return { ok: false, status: res.status, data: null,
                         error: `Server error (HTTP ${res.status}). Nothing was saved.` };
            }
            return { ok: !!data.success, status: res.status, data, error: data.error || null };
        } catch (e) {
            return { ok: false, status: 0, data: null,
                     error: e && e.name === 'AbortError'
                        ? 'The server took too long to respond. Nothing was changed; please try again.'
                        : 'Could not reach the server. Nothing was changed; please try again.' };
        } finally {
            clearTimeout(timer);
        }
    }

    btnSaveDraft.addEventListener('click', async () => {
        if (!currentScheduleData.length || btnSaveDraft.disabled) return;

        const ctx = getContext();
        btnSaveDraft.disabled = true;     // no second save while this one is deciding
        let saved = false;
        try {

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

            const _proceed = await showInfo(
                'Existing Schedule Detected',
                `This section already has a <strong>${_statusLabel}</strong> schedule${_countStr}.<br><br>`
                + '<strong>Cancel</strong> &mdash; stop and keep the existing schedule as is.<br>'
                + '<strong>Continue</strong> &mdash; save this as the new Draft (it replaces any existing Draft). '
                + 'The Published schedule stays live until you publish.',
                'confirm',
                { confirmLabel: 'Continue' }
            );

            if (!_proceed) return;
        }

        btnSaveDraft.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';

        {
            // replace_draft: the server retires the section's existing Draft inside the SAME
            // transaction as this save, so a failed save leaves the old Draft intact. The
            // Published schedule is never touched by Save as Draft.
            const result = await _postJson('/api/schedule/save-draft', {
                batch_id:      currentBatchId,
                schedule_data: currentScheduleData,
                context:       ctx,
                replace_draft: !!existingCheck.exists,
            });
            const data = result.data || { success: false, error: result.error };

            if (result.ok) {
                // A draft is work in progress: incomplete components and hard
                // violations are saved with it — they only block approval.
                const _issues = [];
                if (data.is_incomplete) _issues.push('some required assignments are still incomplete');
                if ((data.draft_warnings || []).length) {
                    _issues.push(`${data.draft_warnings.length} hard-constraint issue${data.draft_warnings.length === 1 ? '' : 's'} remain`);
                }
                const _issueNote = _issues.length
                    ? `<br><br>Saved with open issues (${_issues.join('; ')}). It cannot be approved until they are resolved.`
                    : '';
                await showInfo(
                    'Draft Saved!',
                    `Schedule saved as <strong>Draft V${data.draft_version}</strong>.<br>You can edit it later from <em>View Drafts</em>.${_issueNote}`,
                    'success'
                );
                // The generated schedule stays on screen after saving — just a
                // confirmation, not a full page reset. Same behavior Approve already
                // has (see _submitApproval above). Selecting a different Program to
                // generate for still clears/replaces this normally via Generate Schedule.
                saved = true;
                btnSaveDraft.disabled  = true;
                btnSaveDraft.innerHTML = '<i class="fas fa-check-circle"></i> Saved as Draft';
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
                    await showInfo('Save Failed', data.error || result.error || 'Could not save draft.', 'error');
                }
            }
        }
        } finally {
            // Saved: the button keeps showing "Saved as Draft". Otherwise always restore it —
            // the generated schedule on screen is untouched either way.
            if (!saved) {
                btnSaveDraft.disabled = false;
                btnSaveDraft.innerHTML = '<i class="fas fa-save"></i> Save as Draft';
            }
        }
    });

    btnManualEditor.addEventListener('click', async () => {
        if (!currentScheduleData.length) {
            window.location.href = MANUAL_EDITOR_URL;
            return;
        }
        const ctx = getContext();

        // The generated/retrieved schedule isn't eligible for publishing: say so and ask
        // before carrying it into the Manual Editor (it would arrive with the same issues).
        const _elig = _approvalState();
        if (!_elig.eligible) {
            const ev = currentEvaluation || {};
            const issues = [];
            if ((ev.hardViolationCount || 0) > 0) {
                issues.push(`<li><strong>${ev.hardViolationCount}</strong> hard-constraint violation${ev.hardViolationCount === 1 ? '' : 's'}</li>`);
            }
            if ((ev.incompleteCount || 0) > 0) {
                issues.push(`<li><strong>${ev.incompleteCount}</strong> incomplete assignment${ev.incompleteCount === 1 ? '' : 's'}</li>`);
            }
            const _go = await showInfo(
                'Schedule Not Eligible for Publishing',
                `${_elig.reason || 'This schedule is not eligible for publishing yet.'}`
                + (issues.length ? `<ul style="text-align:left;margin:10px 0 0 18px;">${issues.join('')}</ul>` : '')
                + '<br>You haven\'t re-generated or fixed it. It will open in the Manual Editor '
                + 'with these issues, and you\'ll need to fix them there before it can be approved.'
                + '<br><br><strong>Continue to the Manual Editor anyway?</strong>',
                'confirm',
                { confirmLabel: 'Continue to Manual Editor' }
            );
            if (!_go) return;
        }

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

                const _doOverride = await showInfo(
                    'Existing Schedule Detected',
                    `This section already has a <strong>${_statusLabel}</strong> schedule${_countStr}.<br><br>`
                    + '<strong>Cancel</strong> &mdash; stay on Generate Schedule and keep the existing schedule.<br>'
                    + '<strong>Override</strong> &mdash; replace the existing Draft with the generated schedule and open Manual Editor. '
                    + 'The Published schedule stays live until you publish.',
                    'confirm',
                    { confirmLabel: 'Override' }
                );

                if (!_doOverride) {
                    btnManualEditor.disabled = false;
                    btnManualEditor.innerHTML = '<i class="fas fa-edit"></i> Go to Manual Editor';
                    return;
                }
                // The existing Draft is replaced by the save below, in the same transaction.
            }

            // ── Step 2: save generator sessions as Draft to DB ──
            // This gives Manual Editor full editing capabilities (delete, drag-and-drop, etc.).
            // If the save fails for any reason, fall back to sessionStorage so the sessions
            // are still visible via the generator overlay approach.
            btnManualEditor.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving Draft...';
            let _savedAsDraft = false;
            let _saveDraftResult = null;
            const _handoff = await _postJson('/api/schedule/save-draft', {
                batch_id:      currentBatchId,
                schedule_data: currentScheduleData,
                context:       ctx,
                replace_draft: !!existingCheck.exists,
            });
            _saveDraftResult = _handoff.data;
            _savedAsDraft = _handoff.ok;
            if (!_savedAsDraft) {
                // Nothing was replaced (the server rolled back). The editor still opens with
                // the generated schedule as UNSAVED changes so no work is lost.
                await showInfo('Not Saved as Draft',
                    `${_handoff.error || 'The draft could not be saved.'}<br><br>`
                    + 'The Manual Editor will open with the generated schedule as unsaved changes.',
                    'error');
            }

            // Handoff rule:
            //   1) Complete successful save -> DB is authoritative; no transfer.
            //   2) Successful PARTIAL save -> transfer ONLY subjects that the DB could
            //      not represent (fully unresolved/TBA occurrences).
            //   3) Failed save -> retain the original full-transfer fallback.
            // This avoids hydrating DB rows + a second full generator snapshot together.
            try { sessionStorage.removeItem('_sched_gen_transfer'); } catch (_se) {}

            if (_savedAsDraft) {
                const _unsavedCodes = new Set(
                    ((_saveDraftResult && _saveDraftResult.unsaved_incomplete_subjects) || [])
                        .map(v => String(v || '').trim().toUpperCase())
                        .filter(Boolean)
                );

                if (_unsavedCodes.size) {
                    const _unsavedRows = (currentScheduleData || []).filter(row => {
                        const code = String(row.subject_code || row.subjectcode || '').trim().toUpperCase();
                        return _unsavedCodes.has(code);
                    });
                    if (_unsavedRows.length) {
                        try {
                            sessionStorage.setItem('_sched_gen_transfer', JSON.stringify({
                                mode:          'unsaved_only',
                                schedule_data: _unsavedRows,
                                context:       ctx,
                                subject_codes: Array.from(_unsavedCodes),
                            }));
                        } catch (_se) { /* DB rows remain available even if fallback storage is unavailable */ }
                    }
                }
            } else {
                try {
                    sessionStorage.setItem('_sched_gen_transfer', JSON.stringify({
                        mode:          'full_fallback',
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
        // A publish that never answers must not leave the button spinning forever.
        const _approveAbort   = new AbortController();
        const _approveTimeout = setTimeout(() => _approveAbort.abort(), APPROVE_TIMEOUT_MS);
        let published = false;
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
                signal: _approveAbort.signal,
            });
            clearTimeout(_approveTimeout);
            let data;
            try { data = await res.json(); }
            catch (_pe) { data = { success: false, error: `Server error (HTTP ${res.status}). Nothing was published.` }; }

            if (data.success) {
                const draftNote = data.draft_version
                    ? `<br>Draft <strong>V${data.draft_version}</strong> has been preserved for further editing.`
                    : '';
                await showInfo(
                    'Published!',
                    `Schedule published as <strong>V${data.published_version}</strong>.${draftNote}`,
                    'success'
                );
                published = true;
                btnApprove.disabled = true;
                btnApprove.innerHTML = '<i class="fas fa-check-circle"></i> Published';

                // Same end state as Draft List → Approve: the DB Published snapshot is now
                // authoritative, so open the Manual Editor on it directly instead of leaving
                // this generated result on the page to be re-saved as a new Draft.
                try { localStorage.removeItem(_LS_KEY); } catch (e) {}
                const sectName = sectionFilter.options[sectionFilter.selectedIndex]?.text || '';
                window.location.href = MANUAL_EDITOR_URL
                    + `?mode=program`
                    + `&prog=${encodeURIComponent(ctx.program)}`
                    + `&yl=${encodeURIComponent(ctx.yearLevel)}`
                    + `&ay=${encodeURIComponent(ctx.acadYear)}`
                    + `&sem=${encodeURIComponent(ctx.term)}`
                    + `&scheduler=official`
                    + `&sect=${encodeURIComponent(ctx.section)}`
                    + `&sect_name=${encodeURIComponent(sectName)}`;
                return;

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

                published = true;   // the nested call owns the button state from here
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
            }
        } catch (e) {
            const msg = e && e.name === 'AbortError'
                ? 'The server took too long to respond. The previous published schedule is unchanged; please try again.'
                : 'Connection error. Please try again.';
            await showInfo('Error', msg, 'error');
        } finally {
            clearTimeout(_approveTimeout);
            if (!published) {
                btnApprove.innerHTML = APPROVE_LABEL;
                _refreshApprovalGate();
            }
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

                _beginScheduleState();
                rowSelectionState.clear();   // a different schedule: old selections don't apply
                currentScheduleData = data.schedule_data || [];
                currentBatchId = `DRAFT-${versionId}`;

                if (data.context) {
                    acadYear.value  = data.context.acadYear  || '';
                    term.value      = data.context.term      || '';
                    program.value   = data.context.program   || '';
                    yearLevel.value = data.context.yearLevel || '';
                }

                // A loaded draft is a different schedule — never keep the previous
                // schedule's evaluation (it also drives row highlighting/Approve).
                currentEvaluation = null;
                renderTable(currentScheduleData, sortSelect.value);
                updateTitleBar();
                updateEvaluationWidget(currentScheduleData, getContext());

                btnRegenerate.disabled   = false;
                btnSaveDraft.disabled    = false;
                btnSaveDraft.innerHTML   = '<i class="fas fa-save"></i> Save as Draft';
                btnManualEditor.disabled = false;
                btnExport.disabled       = false;
                _refreshApprovalGate();   // re-validated once the evaluation loads

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
