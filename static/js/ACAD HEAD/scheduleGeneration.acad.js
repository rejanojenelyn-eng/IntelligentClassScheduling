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
        sectionFilter.innerHTML = '<option value="">SELECT</option>';
        if (!prog || !yl) { checkFormValidity(); return; }
        sectionFilter.innerHTML = '<option value="">Loading...</option>';
        try {
            const res  = await fetch(`/api/sections-by-program?program=${encodeURIComponent(prog)}&yearLevel=${encodeURIComponent(yl)}`);
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
    [acadYear, term, useHistorical].forEach(el => {
        el.addEventListener('change', checkFormValidity);
    });

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
        try { localStorage.removeItem(_LS_KEY); } catch(e) {}

        document.getElementById('scheduleTableBody').innerHTML = `
            <tr class="table-empty-row">
                <td colspan="11">
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

        // Reset accuracy widget
        const pctEl       = document.getElementById('accuracyPct');
        const iconEl      = document.getElementById('accuracyIcon');
        const circleEl    = document.getElementById('accuracyCircle');
        const breakdownEl = document.getElementById('accuracyBreakdown');
        const descEl      = document.getElementById('accuracyDesc');
        if (pctEl)       pctEl.textContent = '—';
        if (iconEl)      iconEl.className  = 'fas fa-chart-bar';
        if (circleEl)    { circleEl.style.borderColor = ''; }
        if (breakdownEl) { breakdownEl.innerHTML = ''; breakdownEl.classList.remove('visible'); }
        if (descEl)      descEl.textContent = 'Generate a schedule to see how well it satisfies all scheduling rules and constraints.';
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

    async function _restoreStateFromStorage() {
        try {
            const raw = localStorage.getItem(_LS_KEY);
            if (!raw) return;
            const saved = JSON.parse(raw);
            if (!saved || !saved.scheduleData || !saved.scheduleData.length) return;

            const fv = saved.formValues || {};
            // Restore form dropdowns first (program triggers year-level/section loads)
            if (fv.program) program.value = fv.program;
            if (fv.acadYear) acadYear.value = fv.acadYear;
            if (fv.term) term.value = fv.term;

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

            updateAccuracyWidget(currentScheduleData, getContext());
        } catch(e) {}
    }

    function showInfo(title, message, type = 'info') {
        return new Promise(resolve => {
            const modal    = document.getElementById('infoModal');
            const icon     = document.getElementById('infoModalIcon');
            const titleEl  = document.getElementById('infoModalTitle');
            const msgEl    = document.getElementById('infoModalMessage');
            const confirmBtn = document.getElementById('infoModalConfirmBtn');
            const cancelBtn  = document.getElementById('infoModalCancelBtn');

            titleEl.textContent = title;
            msgEl.innerHTML     = message;

            icon.className = 'info-modal-icon';
            if (type === 'success') {
                icon.innerHTML = '<i class="fas fa-check-circle"></i>';
                icon.classList.add('success');
            } else if (type === 'error') {
                icon.innerHTML = '<i class="fas fa-times-circle"></i>';
                icon.classList.add('error');
            } else if (type === 'confirm') {
                icon.innerHTML = '<i class="fas fa-question-circle"></i>';
                icon.classList.add('warning');
            } else {
                icon.innerHTML = '<i class="fas fa-info-circle"></i>';
            }

            modal.classList.remove('hidden');

            if (type === 'confirm') {
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
            // Fast progress to 88%, then slow creep up to 99% so the bar never freezes
            if (progress < 88) {
                progress += Math.random() * 15;
            } else {
                progress += Math.random() * 0.25;
            }
            if (progress > 99) progress = 99;

            document.getElementById('progressBarFill').style.width = progress + '%';
            document.getElementById('progressLabel').textContent = Math.floor(progress) + '%';

            if (stepIndex < steps.length && progress > (stepIndex + 1) * (88 / steps.length)) {
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
            tbody.innerHTML = '<tr class="empty-row"><td colspan="11"><span class="empty-msg"><i class="fas fa-calendar-plus"></i> No classes scheduled yet</span></td></tr>';
            return;
        }

        const groups = {};
        scheduleArray.forEach(cls => {
            const key = (cls.subject_code || '') + '||' + (cls.faculty_id || cls.instructor || '');
            if (!groups[key]) {
                groups[key] = {
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
                };
            }
            const g = groups[key];
            g.lec_hours += (cls.lec_hours || cls.lecturehours || 0);
            g.lab_hours += (cls.lab_hours || cls.laboratoryhours || 0);

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

        tbody.innerHTML = entries.map((g) => `
            <tr>
                <td class="td-instructor">${g.instructor}</td>
                <td class="td-code">${g.subject_code}</td>
                <td class="td-desc">${g.description}</td>
                <td class="td-num">${g.lec_hours}</td>
                <td class="td-num">${g.lab_hours}</td>
                <td class="td-num">${g.credit_units}</td>
                <td class="td-course">${g.course}</td>
                <td class="td-time">${g.times.map(t => `<span class="time-line">${t}</span>`).join('')}</td>
                <td class="td-num">${g.lec_hours + g.lab_hours}</td>
                <td class="td-days">${_sortDays(g.days_set).join(' / ')}</td>
                <td class="td-room">${g.rooms.join('<br>')}</td>
            </tr>
        `).join('');
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
        const sectionPart = (sectionText && sectionText !== 'SELECT') ? ` - ${sectionText}` : '';
        document.getElementById('tableTitle').innerHTML =
            `<i class="fas fa-calendar-alt"></i> ${ctx.program || 'Program'} - Year ${ctx.yearLevel || '?'}${sectionPart} | ${termName} | ${ctx.acadYear || 'AY'}`;
    }

    function applyScheduleResult(data, isRetrieve) {
        currentScheduleData = data.schedule_data || [];
        currentBatchId      = data.batch_id || 'DRAFT-NEW-001';
        canPublish          = (data.conflict_count || 0) === 0;

        renderTable(currentScheduleData, sortSelect.value);
        updateTitleBar();

        // #7/#8: Always refresh calendar if it is currently visible so both views stay in sync.
        if (!document.getElementById('calendarViewContainer').classList.contains('hidden')) {
            renderCalendarView(currentScheduleData);
        }

        btnRegenerate.disabled   = false;
        btnSaveDraft.disabled    = false;
        btnManualEditor.disabled = false;
        btnExport.disabled       = false;
        btnApprove.disabled      = !canPublish;

        if ((data.conflict_count || 0) > 0) {
            document.getElementById('conflictText').textContent =
                `Schedule has ${data.conflict_count} unresolved conflict(s). Approve is blocked until resolved.`;
            document.getElementById('conflictBanner').classList.remove('hidden');
        } else {
            document.getElementById('conflictBanner').classList.add('hidden');
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

        _saveStateToStorage();
        if (data && data.accuracy_data && data.accuracy_data.success) {
            _renderAccuracyResult(data.accuracy_data);
        } else {
            updateAccuracyWidget(currentScheduleData, getContext());
        }
    }

    function _renderAccuracyResult(data) {
        const circleEl    = document.getElementById('accuracyCircle');
        const pctEl       = document.getElementById('accuracyPct');
        const descEl      = document.getElementById('accuracyDesc');
        const labelEl     = document.getElementById('accuracyLabelEl');
        const iconEl      = document.getElementById('accuracyIcon');
        const breakdownEl = document.getElementById('accuracyBreakdown');
        if (!pctEl) return;

        if (data && data.success) {
            const pct   = data.accuracy || 0;
            const color = pct >= 85 ? '#16a34a' : pct >= 60 ? '#d97706' : '#dc2626';
            const icon  = pct >= 85 ? 'fas fa-check' : pct >= 60 ? 'fas fa-chart-bar' : 'fas fa-exclamation';

            pctEl.textContent          = pct + '%';
            iconEl.className           = icon;
            circleEl.style.borderColor = color;
            pctEl.style.color          = color;
            if (labelEl) labelEl.style.color = color;

            const noHist = !data.hist_total;
            if (descEl) {
                descEl.textContent = noHist
                    ? 'No historical data — score reflects constraint compliance only.'
                    : `${data.matched_faculty || 0} of ${data.hist_total || 0} subjects match historical faculty.`;
            }

            if (breakdownEl && Array.isArray(data.breakdown)) {
                const GROUPS = [
                    { heading: 'Conflict Validation',    keys: ['faculty_conflict','room_conflict','section_conflict'] },
                    { heading: 'Constraint Compliance',  keys: ['faculty_qual','lab_compliance','weekend','day_pairing','load_compliance'] },
                    { heading: 'Recommendation Quality', keys: ['hist_faculty','hist_room','hist_time'] },
                ];
                const byKey = {};
                data.breakdown.forEach(b => { byKey[b.key] = b; });
                function itemColor(sc) { return sc >= 85 ? '#16a34a' : sc >= 60 ? '#d97706' : '#dc2626'; }
                let html = '';
                GROUPS.forEach((grp, gi) => {
                    if (gi > 0) html += '<hr class="acc-divider">';
                    html += `<div class="acc-section-label">${grp.heading}</div>`;
                    grp.keys.forEach(key => {
                        const item = byKey[key];
                        if (!item) return;
                        const sc = item.score;
                        const bc = itemColor(sc);
                        html += `<div class="acc-item">
                          <div class="acc-item-header">
                            <span class="acc-item-label" title="${item.label}">${item.label}</span>
                            <span class="acc-item-score" style="color:${bc}">${sc}%</span>
                          </div>
                          <div class="acc-bar-track">
                            <div class="acc-bar-fill" style="width:${sc}%;background:${bc}"></div>
                          </div>
                        </div>`;
                    });
                });
                breakdownEl.innerHTML = html;
                breakdownEl.classList.add('visible');
            }
        } else {
            pctEl.textContent          = 'N/A';
            iconEl.className           = 'fas fa-question';
            circleEl.style.borderColor = '#aaa';
            pctEl.style.color          = '#aaa';
            if (labelEl) labelEl.style.color = '#aaa';
            if (descEl)  descEl.textContent  = (data && data.error) || 'Could not calculate accuracy.';
        }
    }

    async function updateAccuracyWidget(scheduleData, ctx) {
        const pctEl       = document.getElementById('accuracyPct');
        const iconEl      = document.getElementById('accuracyIcon');
        const circleEl    = document.getElementById('accuracyCircle');
        const labelEl     = document.getElementById('accuracyLabelEl');
        const breakdownEl = document.getElementById('accuracyBreakdown');
        if (!pctEl) return;

        pctEl.textContent          = '...';
        iconEl.className           = 'fas fa-spinner fa-spin';
        circleEl.style.borderColor = '#aaa';
        pctEl.style.color          = '#aaa';
        if (labelEl)     labelEl.style.color = '#aaa';
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
            _renderAccuracyResult(data);
        } catch (e) {
            if (pctEl)    pctEl.textContent          = 'N/A';
            if (iconEl)   iconEl.className            = 'fas fa-question';
            if (circleEl) circleEl.style.borderColor  = '#aaa';
            if (pctEl)    pctEl.style.color           = '#aaa';
            const descEl = document.getElementById('accuracyDesc');
            if (descEl)   descEl.textContent           = 'Accuracy calculation unavailable.';
        }
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

                if (data.success) {
                    applyScheduleResult(data, false);
                } else {
                    await showInfo('Generation Failed', data.error || 'Could not generate schedule.', 'error');
                }
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

    btnRegenerate.addEventListener('click', () => { btnGenerate.click(); });

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

        btnSaveDraft.disabled = true;
        btnSaveDraft.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';

        const ctx = getContext();
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
                // #1: Reset page to clean state after a successful draft save.
                _resetPageState();
                return; // skip finally re-enable since _resetPageState handles it
            } else {
                // #11: Load violations get a dedicated message listing each faculty
                const lvs = data.load_violations || [];
                if (lvs.length) {
                    const details = lvs.map(v =>
                        `• ${v.faculty_name}: ${v.total_load}/${v.max_load} units (+${v.overload_by} over limit)`
                    ).join('<br>');
                    await showInfo('Faculty Load Exceeded',
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
        btnManualEditor.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';

        const _saveTimeout = new Promise(resolve => setTimeout(() => resolve({ _timedOut: true }), 10000));
        try {
            const _saveFetch = fetch('/api/schedule/save-draft', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    batch_id:      currentBatchId,
                    schedule_data: currentScheduleData,
                    context:       ctx,
                }),
            }).then(r => r.json());
            const result = await Promise.race([_saveFetch, _saveTimeout]);
            if (result && result._timedOut) {
                // Save is taking too long — navigate anyway; draft can be saved later
            } else if (result && !result.success) {
                await showInfo('Warning', 'Could not auto-save draft. The editor will still open.', 'error');
            }
        } catch (e) {
            // proceed anyway
        }

        const sectName = sectionFilter.options[sectionFilter.selectedIndex]?.text || '';
        const url = MANUAL_EDITOR_URL
            + `?mode=program&prog=${encodeURIComponent(ctx.program)}&yl=${encodeURIComponent(ctx.yearLevel)}&ay=${encodeURIComponent(ctx.acadYear)}&sem=${encodeURIComponent(ctx.term)}&sect=${encodeURIComponent(ctx.section)}&sect_name=${encodeURIComponent(sectName)}`;
        window.location.href = url;
    });

    btnApprove.addEventListener('click', async () => {
        if (!currentScheduleData.length) return;

        const confirmed = await showInfo(
            'Approve & Publish Schedule?',
            'This will <strong>publish</strong> this schedule as the active version.<br>The previous published version will be archived.',
            'confirm'
        );
        if (!confirmed) return;

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
            } else {
                // #11: Show per-faculty load violation details when applicable
                const lvs = data.load_violations || [];
                if (lvs.length) {
                    const details = lvs.map(v =>
                        `• ${v.faculty_name}: ${v.total_load}/${v.max_load} units (+${v.overload_by} over limit)`
                    ).join('<br>');
                    await showInfo('Faculty Load Exceeded',
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

    // Restore the last generated schedule if the user came back from Manual Editor
    _restoreStateFromStorage();
});
