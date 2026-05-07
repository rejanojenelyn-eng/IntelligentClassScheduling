// MANUAL_EDITOR_URL is defined inline in the HTML template above this script

document.addEventListener('DOMContentLoaded', () => {
    let currentScheduleData = [];
    let currentBatchId      = null;
    let canPublish          = false;

    const acadYear    = document.getElementById('acadYear');
    const term        = document.getElementById('term');
    const program     = document.getElementById('program');
    const yearLevel   = document.getElementById('yearLevel');
    const curriculum  = document.getElementById('curriculum');
    const curriculumText = document.getElementById('curriculumText');
    const useHistorical = document.getElementById('useHistorical');

    const btnGenerate      = document.getElementById('btnGenerate');
    const btnRegenerate    = document.getElementById('btnRegenerate');
    const btnSaveDraft     = document.getElementById('btnSaveDraft');
    const btnApprove       = document.getElementById('btnApprove');
    const btnManualEditor  = document.getElementById('btnManualEditor');
    const btnExport        = document.getElementById('btnExport');
    const btnCalendarView  = document.getElementById('btnCalendarView');
    const btnTableView     = document.getElementById('btnTableView');

    program.addEventListener('change', async function() {
        const programValue = this.value;
        if (!programValue) {
            curriculumText.textContent = "Select Program first";
            curriculum.value = "";
            checkFormValidity();
            return;
        }
        curriculumText.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
        try {
            const res = await fetch(`/api/curriculum-by-year?program=${encodeURIComponent(programValue)}`);
            const data = await res.json();
            if (data.curriculum) {
                curriculum.value = data.curriculum;
                curriculumText.textContent = `CY ${data.curriculum}`;
            } else {
                curriculumText.textContent = "No curriculum found";
                curriculum.value = "";
            }
        } catch (e) {
            console.error('Error fetching curriculum:', e);
            curriculumText.textContent = "Error loading curriculum";
            curriculum.value = "";
        }
        checkFormValidity();
    });

    function checkFormValidity() {
        const allFilled = acadYear.value && term.value && program.value && yearLevel.value && curriculum.value;
        btnGenerate.disabled = !allFilled;
    }

    [acadYear, term, yearLevel, useHistorical].forEach(el => {
        el.addEventListener('change', checkFormValidity);
    });

    checkFormValidity();

    btnCalendarView.addEventListener('click', () => {
        document.getElementById('tableViewContainer').classList.add('hidden');
        document.getElementById('calendarViewContainer').classList.remove('hidden');
        btnCalendarView.classList.add('active-btn');
        btnCalendarView.classList.remove('outline-btn');
        btnTableView.classList.add('outline-btn');
        btnTableView.classList.remove('active-btn');
        if (currentScheduleData.length > 0) renderCalendarView(currentScheduleData);
    });

    btnTableView.addEventListener('click', () => {
        document.getElementById('calendarViewContainer').classList.add('hidden');
        document.getElementById('tableViewContainer').classList.remove('hidden');
        btnTableView.classList.add('active-btn');
        btnTableView.classList.remove('outline-btn');
        btnCalendarView.classList.add('outline-btn');
        btnCalendarView.classList.remove('active-btn');
    });

    function getContext() {
        return {
            program:   program.value,
            yearLevel: parseInt(yearLevel.value),
            term:      term.value,
            acadYear:  acadYear.value,
        };
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
            progress += Math.random() * 15;
            if (progress > 95) progress = 95;

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

    function renderTable(scheduleArray) {
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

            (cls.days || '').split('/').filter(Boolean).forEach(d => {
                if (!g.days_set.includes(d)) g.days_set.push(d);
            });

            const room = cls.room || 'TBA';
            if (!g.rooms.includes(room)) g.rooms.push(room);
        });

        tbody.innerHTML = Object.values(groups).map(g => `
            <tr>
                <td>${g.instructor}</td>
                <td>${g.subject_code}</td>
                <td>${g.description}</td>
                <td>${g.lec_hours}</td>
                <td>${g.lab_hours}</td>
                <td>${g.credit_units}</td>
                <td>${g.course}</td>
                <td>${g.times.join(' / ')}</td>
                <td>${g.lec_hours + g.lab_hours}</td>
                <td>${g.days_set.join('/')}</td>
                <td>${g.rooms.join(' / ')}</td>
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
        const termName = term.options[term.selectedIndex]?.text || ctx.term;
        document.getElementById('tableTitle').innerHTML =
            `<i class="fas fa-calendar-alt"></i> ${ctx.program || 'Program'} - Year ${ctx.yearLevel || '?'} | ${termName} | ${ctx.acadYear || 'AY'}`;
    }

    function applyScheduleResult(data, isRetrieve) {
        currentScheduleData = data.schedule_data || [];
        currentBatchId      = data.batch_id || 'DRAFT-NEW-001';
        canPublish          = (data.conflict_count || 0) === 0;

        renderTable(currentScheduleData);
        updateTitleBar();

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
            toast.innerHTML =
                `<i class="fas fa-check-circle"></i> Previous schedule loaded`
                + ` — ${rf.status} V${rf.version} (${rf.ay_label} ${rf.term})`;
        } else {
            toast.innerHTML = '<i class="fas fa-check-circle"></i> Schedule Generated Successfully!';
        }
        toast.classList.remove('hidden');
        setTimeout(() => toast.classList.add('hidden'), 4000);
    }

    btnGenerate.addEventListener('click', async () => {
        const ctx = getContext();

        if (!ctx.acadYear || !ctx.term || !ctx.program || !ctx.yearLevel) {
            await showInfo('Missing Fields', 'Please select all required fields before generating.', 'error');
            return;
        }

        const isRetrieve = useHistorical.checked;

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
                    }),
                });
                const data = await res.json();
                completeProgress();

                if (data.success) {
                    applyScheduleResult(data, true);
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
                const res = await fetch('/api/schedule/generate', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({
                        program:    ctx.program,
                        yearLevel:  ctx.yearLevel,
                        term:       ctx.term,
                        curriculum: curriculum.value,
                    }),
                });
                const data = await res.json();
                completeProgress();

                if (data.success) {
                    applyScheduleResult(data, false);
                } else {
                    await showInfo('Generation Failed', data.error || 'Could not generate schedule.', 'error');
                }
            }
        } catch (e) {
            completeProgress();
            await showInfo('Error', 'Connection error. Please try again.', 'error');
        } finally {
            btnGenerate.disabled = false;
            btnGenerate.innerHTML = '<i class="fas fa-magic"></i> GENERATE SCHEDULE';
            document.querySelector('#loadingModal h2').textContent = 'GENERATING SCHEDULE';
            document.querySelector('#loadingModal .loading-subtitle').textContent =
                'Detecting conflicts & applying constraints...';
        }
    });

    btnRegenerate.addEventListener('click', () => { btnGenerate.click(); });

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
            } else {
                await showInfo('Save Failed', data.error || 'Could not save draft.', 'error');
            }
        } catch (e) {
            await showInfo('Error', 'Connection error. Please try again.', 'error');
        } finally {
            btnSaveDraft.disabled = false;
            btnSaveDraft.innerHTML = '<i class="fas fa-save"></i> Save as Draft';
        }
    });

    btnManualEditor.addEventListener('click', () => {
        window.location.href = MANUAL_EDITOR_URL;
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
                await showInfo(
                    'Published!',
                    `Schedule published as <strong>V${data.published_version}</strong>.<br>A new draft <strong>V${data.draft_version}</strong> has been created automatically.`,
                    'success'
                );
                btnApprove.disabled = true;
                btnApprove.innerHTML = '<i class="fas fa-check-circle"></i> Published';
            } else {
                await showInfo('Publish Failed', data.error || 'Could not publish schedule.', 'error');
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

                renderTable(currentScheduleData);
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
});
