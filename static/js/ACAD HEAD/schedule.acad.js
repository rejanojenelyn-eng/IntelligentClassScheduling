/* ---- Overlay mode URL params (parsed before anything else) ---- */
(function () {
    const p = new URLSearchParams(window.location.search);
    window._OVERLAY_ID   = p.get('overlay_id');
    window._OVERLAY_PROG = p.get('prog');
    window._OVERLAY_YL   = p.get('yl');
    window._OVERLAY_SEM  = p.get('sem');
    window._OVERLAY_AY   = p.get('ay');
    window._OVERLAY_VNUM = p.get('vnum');
    window._overlaySessions = [];
})();

/* ---- Schedule List filters ---- */
const _initEl  = document.getElementById('schedule-init-data');
const SEM_DATA  = JSON.parse(_initEl.dataset.sems);
const TODAY_STR = _initEl.dataset.today;
const ACTIVE_AY = _initEl.dataset.activeAy;
const ACTIVE_SEM = _initEl.dataset.activeSem;

const SEM_LABELS = { A: '1st Semester', B: '2nd Semester', C: 'Summer' };

function populateSemDropdown(selectedAy, preselectSem) {
    const sel  = document.getElementById('sl_sem');
    const today = TODAY_STR;
    sel.innerHTML = '';
    SEM_DATA
        .filter(s => s.ay === selectedAy && (!s.end || s.end >= today))
        .forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.type;
            opt.textContent = SEM_LABELS[s.type] || s.type;
            if (s.type === preselectSem) opt.selected = true;
            sel.appendChild(opt);
        });
    filterStatusList();
}

function onAyChange() {
    const ay = document.getElementById('sl_ay').value;
    populateSemDropdown(ay, null);
}

function filterStatusList() {
    const prog = document.getElementById('sl_prog').value;
    const ay   = document.getElementById('sl_ay').value;
    const sem  = document.getElementById('sl_sem').value;
    document.querySelectorAll('.status-monitor-table tbody tr').forEach(row => {
        const match = (!prog || row.dataset.prog === prog)
                   && (!ay   || row.dataset.ay   === ay)
                   && (!sem  || row.dataset.sem   === sem);
        row.style.display = match ? '' : 'none';
    });
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('sl_ay').addEventListener('change', onAyChange);
    populateSemDropdown(ACTIVE_AY, ACTIVE_SEM);
});

/* ---- Notification dismiss ---- */
function closeNotif() {
    const el = document.getElementById('notif-overlay');
    if (el) { el.classList.add('notif-fade-out'); setTimeout(() => el.remove(), 300); }
}
setTimeout(closeNotif, 6000);

/* ---- Modal ---- */
function openImportModal()  { document.getElementById('importScheduleModal').style.display = 'flex'; }
function closeImportModal() { document.getElementById('importScheduleModal').style.display = 'none'; }

/* ---- View toggle ---- */
let currentView = 'calendar';
function setView(type) {
    currentView = type;
    document.getElementById('btn-calendar').classList.toggle('active', type === 'calendar');
    document.getElementById('btn-table').classList.toggle('active', type === 'table');
    document.getElementById('calendarViewWrapper').style.display = type === 'calendar' ? 'block' : 'none';
    document.getElementById('tableViewWrapper').style.display    = type === 'table'    ? 'block' : 'none';
    renderCurrentView(window._lastSessions || []);
}

/* ---- Year-level rules ---- */
function maxYearLevel(progCode) {
    const code = (progCode || '').toUpperCase();
    if (code === 'BSARCH') return 5;
    if (code.startsWith('D')) return 3;
    return 4;
}
function updateYearLevels() {
    const prog = document.getElementById('view_prog').value;
    const max  = maxYearLevel(prog);
    const sel  = document.getElementById('view_yl');
    const cur  = parseInt(sel.value);
    sel.innerHTML = '';
    const labels = ['1ST YEAR','2ND YEAR','3RD YEAR','4TH YEAR','5TH YEAR'];
    for (let i = 1; i <= max; i++) {
        const opt = document.createElement('option');
        opt.value = i; opt.text = labels[i-1];
        if (i === Math.min(cur, max)) opt.selected = true;
        sel.appendChild(opt);
    }
}

async function updateSections() {
    const prog = document.getElementById('view_prog').value;
    const ay   = document.getElementById('view_ay').value;
    const sem  = document.getElementById('view_sem').value;
    const sel  = document.getElementById('view_section');
    if (!sel) return;
    sel.innerHTML = '<option value="">ALL SECTIONS</option>';
    if (!ay || !sem) return;
    try {
        let url;
        if (prog) {
            // When offering is known, omit yearLevel so all sections of the offering are visible;
            // the API already ordered them by yearlevel, sectionname for readability
            url = `/api/sections-by-program?program=${encodeURIComponent(prog)}&ay=${encodeURIComponent(ay)}&semester=${sem}`;
        } else {
            url = `/api/sections-by-program?ay=${encodeURIComponent(ay)}&semester=${sem}`;
        }
        const resp = await fetch(url);
        const data = await resp.json();
        (data.sections || []).forEach(s => {
            const opt = document.createElement('option');
            opt.value            = s.id;
            opt.dataset.offering  = s.offeringcode || '';
            opt.dataset.yearlevel = String(s.yearlevel || '');
            opt.text = s.name;
            sel.appendChild(opt);
        });
    } catch (e) {
        console.error('[updateSections]', e);
    }
}

async function onSectionChange() {
    const secSel  = document.getElementById('view_section');
    const progSel = document.getElementById('view_prog');
    if (!secSel || !progSel) { refreshOfferings(); return; }

    const selectedId      = secSel.value;
    const selOpt          = secSel.options[secSel.selectedIndex];
    const sectionOffering = selOpt?.dataset?.offering   || '';
    const sectionYl       = selOpt?.dataset?.yearlevel  || '';

    if (!selectedId) {
        // Section cleared → reset program + year level and reload all sections
        progSel.value = '';
        const ylSel = document.getElementById('view_yl');
        if (ylSel) ylSel.value = '';
        await updateSections();
        refreshOfferings();
        return;
    }

    // Auto-set program + year level from selected section's metadata
    if (sectionOffering) {
        const match = Array.from(progSel.options)
            .find(o => o.value.toUpperCase() === sectionOffering.toUpperCase());
        if (match && progSel.value.toUpperCase() !== match.value.toUpperCase()) {
            progSel.value = match.value;
            updateYearLevels();
            if (sectionYl) {
                const ylSel = document.getElementById('view_yl');
                if (ylSel) ylSel.value = sectionYl;
            }
            // Reload section dropdown scoped to this program, then re-select same section
            const ay  = document.getElementById('view_ay').value;
            const sem = document.getElementById('view_sem').value;
            secSel.innerHTML = '<option value="">ALL SECTIONS</option>';
            try {
                const url = `/api/sections-by-program?program=${encodeURIComponent(match.value)}&ay=${encodeURIComponent(ay)}&semester=${sem}`;
                const resp = await fetch(url);
                const data = await resp.json();
                (data.sections || []).forEach(s => {
                    const opt = document.createElement('option');
                    opt.value             = s.id;
                    opt.dataset.offering  = s.offeringcode || '';
                    opt.dataset.yearlevel = String(s.yearlevel || '');
                    opt.text = s.name;
                    secSel.appendChild(opt);
                });
            } catch(e) { console.error('[onSectionChange] fetch', e); }
            const reselect = Array.from(secSel.options).find(o => o.value === selectedId);
            if (reselect) secSel.value = reselect.value;
        } else if (!match) {
            // Program not in dropdown — set year level only
            if (sectionYl) {
                const ylSel = document.getElementById('view_yl');
                if (ylSel) ylSel.value = sectionYl;
            }
        }
    }
    refreshOfferings();
}


/* ---- Color palette ---- */
const colorPalette = ['#16a085','#27ae60','#2980b9','#8e44ad','#2c3e50','#f39c12','#d35400','#c0392b'];
function getSubjectColor(code) {
    let h = 0;
    for (let i = 0; i < code.length; i++) h = code.charCodeAt(i) + ((h << 5) - h);
    return colorPalette[Math.abs(h) % colorPalette.length];
}

/* ---- Helpers ---- */
function dayAbbr(day) {
    const map = { Monday:'MON', Tuesday:'TUE', Wednesday:'WED',
                  Thursday:'THU', Friday:'FRI', Saturday:'SAT', Sunday:'SUN' };
    return map[day] || day;
}
const GRID_ORIGIN_MINS = 7 * 60 + 30;

function timeStrToMins(t) {
    if (!t || typeof t !== 'string') return null;
    const parts = t.split(':');
    if (parts.length < 2) return null;
    return parseInt(parts[0]) * 60 + parseInt(parts[1]);
}

function fmtTime(t) {
    if (!t) return '—';
    const [hh, mm] = t.split(':').map(Number);
    const suffix = hh >= 12 ? 'PM' : 'AM';
    const h12    = hh > 12 ? hh - 12 : (hh === 0 ? 12 : hh);
    return h12 + ':' + String(mm).padStart(2, '0') + ' ' + suffix;
}

function renderCurrentView(sessions) {
    if (currentView === 'calendar') {
        renderCalendar(sessions);
        if (window._OVERLAY_ID && window._overlaySessions.length > 0) {
            renderOverlayPills(window._overlaySessions, sessions);
        }
    } else {
        renderTable(sessions);
    }
}

/* ---- Calendar render ---- */
function renderCalendar(sessions) {
    const wrapper = document.getElementById('gridWrapper');
    const table   = document.getElementById('mainTimetable');
    wrapper.querySelectorAll('.schedule-pill, .historical-pill, .cal-no-data').forEach(p => p.remove());

    if (!sessions || sessions.length === 0) {
        const msg = document.createElement('div');
        msg.className = 'cal-no-data';
        msg.style.cssText = 'position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#aaa;font-size:13px;pointer-events:none;z-index:5;text-align:center;';
        msg.textContent = 'No schedule data found for the selected filters.';
        wrapper.appendChild(msg);
        return;
    }

    const seen = new Set();
    sessions = sessions.filter(s => {
        const key = `${s.subjectcode}|${s.daydesc}|${s.start_time}`;
        if (seen.has(key)) return false;
        seen.add(key); return true;
    });

    const days = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
    const firstCell = table.querySelector('tbody td:nth-child(2)');
    const timeCol = table.querySelector('.time-cell');
    const thead = table.querySelector('thead');
    if (!firstCell || firstCell.offsetWidth === 0 || firstCell.offsetHeight === 0) {
        requestAnimationFrame(() => renderCalendar(sessions)); return;
    }

    const colWidth = firstCell.offsetWidth;
    const pxPerMin = firstCell.offsetHeight / 30;
    const GRID_ORIGIN = 7 * 60 + 30;

    const dayGroups = {};
    sessions.forEach(s => {
        if (!dayGroups[s.daydesc]) dayGroups[s.daydesc] = [];
        dayGroups[s.daydesc].push(s);
    });

    Object.keys(dayGroups).forEach(dayName => {
        const dayIdx = days.indexOf(dayName);
        if (dayIdx < 0) return;
        const daySessions = dayGroups[dayName];
        daySessions.sort((a,b) => timeStrToMins(a.start_time) - timeStrToMins(b.start_time));

        daySessions.forEach((sess, idx) => {
            let start = timeStrToMins(sess.start_time), end = timeStrToMins(sess.end_time);
            if (start === null) return;
            if (start < GRID_ORIGIN) start += 12 * 60;
            if (end <= start) end += 12 * 60;

            let overlapCount = 0, overlapIndex = 0;
            daySessions.forEach((other, oIdx) => {
                let oS = timeStrToMins(other.start_time), oE = timeStrToMins(other.end_time);
                if (oS < GRID_ORIGIN) oS += 12 * 60; if (oE <= oS) oE += 12 * 60;
                if (start < oE && end > oS) { overlapCount++; if (idx > oIdx) overlapIndex++; }
            });

            const pill = document.createElement('div');
            pill.className = 'schedule-pill';
            pill.style.backgroundColor = getSubjectColor(sess.subjectcode);
            const w = (colWidth - 6) / (overlapCount || 1);
            const pillH = (end - start) * pxPerMin - 2;

            pill.style.width = (w - 2) + 'px';
            pill.style.height = pillH + 'px';
            pill.style.left = (timeCol.offsetWidth + (dayIdx * colWidth) + (overlapIndex * w) + 4) + 'px';
            pill.style.top = (thead.offsetHeight + (start - GRID_ORIGIN) * pxPerMin + 1) + 'px';

            const instrLast = (sess.instructor || 'TBA').split(',')[0].trim();
            pill.title = `${sess.subjectcode}\n${sess.subjectname}\n${sess.instructor}\n${fmtTime(sess.start_time)} – ${fmtTime(sess.end_time)}\n${sess.roomname}`;

            const pillContent = `
                <div class="pill-subject">${sess.subjectname}</div>
                <div class="pill-instructor">${instrLast}</div>
                <div class="pill-room">${sess.roomname}</div>`;

            if (pillH < 55) pill.classList.add('schedule-pill--compact');
            else if (pillH < 85) pill.classList.add('schedule-pill--medium');
            pill.innerHTML = pillContent;
            wrapper.appendChild(pill);
        });
    });
}

/* ---- Table render ---- */
function renderTable(sessions) {
    const tbody = document.getElementById('offeringsTableBody');
    if (!sessions || sessions.length === 0) {
        tbody.innerHTML = '<tr class="empty-row"><td colspan="11"><span class="empty-msg"><i class="fas fa-calendar-times" style="margin-right:6px;"></i>No schedule data found for the selected filters.</span></td></tr>';
        return;
    }

    const progSel = document.getElementById('view_prog');
    const ylSel   = document.getElementById('view_yl');
    const progCode = progSel ? progSel.value : '';
    const yl       = ylSel  ? ylSel.value   : '';
    const courseLabel = progCode && yl ? `${progCode} ${yl}` : (progCode || '-');

    const grouped = {};
    sessions.forEach(sess => {
        const key = `${sess.subjectcode}||${sess.instructor}`;
        if (!grouped[key]) grouped[key] = { ...sess, daysArr: [], timesArr: [] };
        if (sess.daydesc) {
            const ab = dayAbbr(sess.daydesc);
            if (!grouped[key].daysArr.includes(ab)) grouped[key].daysArr.push(ab);
        }
        if (sess.start_time) {
            const t = `${fmtTime(sess.start_time)} - ${fmtTime(sess.end_time)}`;
            if (!grouped[key].timesArr.includes(t)) grouped[key].timesArr.push(t);
        }
    });

    const groupedRows = Object.values(grouped);
    const dataHtml = groupedRows.map(sess => {
        const instrDisplay = sess.instructor && sess.instructor !== 'TBA'
            ? `<span title="${sess.instructor}">${sess.instructor}</span>`
            : '<span style="color:#aaa;font-style:italic;">TBA</span>';
        const daysDisplay  = sess.daysArr.length  ? sess.daysArr.join('/') : '<span style="color:#aaa;">—</span>';
        const timeDisplay  = sess.timesArr.length ? sess.timesArr.join(' / ') : '<span style="color:#aaa;">—</span>';
        const roomDisplay  = sess.roomname && sess.roomname !== 'TBA'
            ? sess.roomname
            : '<span style="color:#aaa;font-style:italic;">TBA</span>';
        return `<tr>
            <td class="td-instructor">${instrDisplay}</td>
            <td class="td-code">${sess.subjectcode || '—'}</td>
            <td class="td-desc" title="${sess.subjectname || ''}">${sess.subjectname || '—'}</td>
            <td>${sess.lecturehours || 0}</td>
            <td>${sess.laboratoryhours || 0}</td>
            <td class="td-units">${sess.creditunits || 0}</td>
            <td>${courseLabel}</td>
            <td class="td-time">${timeDisplay}</td>
            <td>${sess.total_hours || 0}</td>
            <td class="td-days">${daysDisplay}</td>
            <td class="td-room">${roomDisplay}</td>
        </tr>`;
    }).join('');

    const totLec   = groupedRows.reduce((s, r) => s + (+(r.lecturehours    || 0)), 0);
    const totLab   = groupedRows.reduce((s, r) => s + (+(r.laboratoryhours || 0)), 0);
    const totCred  = groupedRows.reduce((s, r) => s + (+(r.creditunits     || 0)), 0);
    const totHours = groupedRows.reduce((s, r) => s + (+(r.total_hours     || 0)), 0);
    const totalHtml = `<tr class="sis-total-row">
        <td colspan="3">TOTAL</td>
        <td>${totLec}</td>
        <td>${totLab}</td>
        <td class="td-units">${totCred}</td>
        <td></td>
        <td class="td-time"></td>
        <td>${totHours}</td>
        <td class="td-days"></td>
        <td class="td-room"></td>
    </tr>`;

    tbody.innerHTML = dataHtml + totalHtml;
}

/* ---- Data fetch ---- */
let _refreshController = null;
async function refreshOfferings() {
    const pSel    = document.getElementById('view_prog');
    const yl      = document.getElementById('view_yl').value;
    const sem     = document.getElementById('view_sem').value;
    const ay      = document.getElementById('view_ay').value;
    const secSel  = document.getElementById('view_section');
    const section = secSel ? secSel.value : '';

    if (!pSel.value || !ay) {
        document.getElementById('gridLabel').innerText = 'SELECT FILTERS TO VIEW SCHEDULE';
        document.getElementById('offeringsTableBody').innerHTML = '<tr><td colspan="11">No data loaded. Select a program and year level.</td></tr>';
        document.getElementById('gridWrapper').querySelectorAll('.schedule-pill').forEach(p => p.remove());
        window._lastSessions = [];
        return;
    }

    const pText    = pSel.options[pSel.selectedIndex].text;
    const ayEl     = document.getElementById('view_ay');
    const ayTxt    = ayEl.options[ayEl.selectedIndex]?.text || ay;
    const semLabel = { A: '1ST SEMESTER', B: '2ND SEMESTER', C: 'SUMMER' }[sem] || sem;
    const ylOrdinals = ['1st', '2nd', '3rd', '4th', '5th'];
    const ylOrd = (ylOrdinals[parseInt(yl) - 1] || yl + 'th') + ' Year';
    const ylNum = parseInt(yl) || '';
    const secName = secSel && section ? ` · ${secSel.options[secSel.selectedIndex].text}` : '';
    document.getElementById('gridLabel').innerText = `${pText.toUpperCase()}  —  ${ylNum}${secName}  ·  A.Y. ${ayTxt}  ·  ${semLabel}`;

    if (_refreshController) _refreshController.abort();
    _refreshController = new AbortController();

    try {
        const url = `/api/get_offerings_schedule?program=${encodeURIComponent(pSel.value)}&year_level=${yl}&semester=${sem}&ay=${encodeURIComponent(ay)}${section ? '&section_id=' + section : ''}&_t=${Date.now()}`;
        const resp = await fetch(url, { signal: _refreshController.signal, cache: 'no-store' });
        if (!resp.ok) { console.error('[Schedule] API error:', resp.status); return; }
        const data = await resp.json();
        window._lastSessions = data;
        requestAnimationFrame(() => renderCurrentView(data));
    } catch (e) {
        if (e.name !== 'AbortError') console.error('[refreshOfferings]', e);
    }
}

/* ---- Export ---- */
function exportSchedule() {
    const prog = document.getElementById('view_prog').value;
    const yl   = document.getElementById('view_yl').value;
    const sem  = document.getElementById('view_sem').value;
    const ay   = document.getElementById('view_ay').value;
    if (!prog) { alert('Please select a program before exporting.'); return; }
    window.location.href = `/api/export_schedule?program=${encodeURIComponent(prog)}&year_level=${yl}&semester=${sem}&ay=${encodeURIComponent(ay)}`;
}

/* ---- Overlay mode ---- */
function initOverlayMode() {
    if (!window._OVERLAY_ID) return;

    // Show banner
    const banner = document.getElementById('overlayBanner');
    if (banner) banner.style.display = 'flex';

    const label = document.getElementById('overlayVersionLabel');
    if (label) {
        label.textContent =
            `R${window._OVERLAY_VNUM || '?'} — ${window._OVERLAY_PROG || ''} · Year ${window._OVERLAY_YL || ''}`;
    }

    // Pre-select filter dropdowns from URL params
    const ayEl   = document.getElementById('view_ay');
    const semEl  = document.getElementById('view_sem');
    const progEl = document.getElementById('view_prog');
    const ylEl   = document.getElementById('view_yl');

    if (window._OVERLAY_AY  && ayEl)   ayEl.value  = window._OVERLAY_AY;
    if (window._OVERLAY_SEM && semEl)  semEl.value = window._OVERLAY_SEM;
    if (window._OVERLAY_PROG && progEl) {
        progEl.value = window._OVERLAY_PROG;
        updateYearLevels();
    }
    if (window._OVERLAY_YL && ylEl) ylEl.value = window._OVERLAY_YL;
    updateSections();

    // Lock filter controls so the overlay context stays consistent
    ['view_ay', 'view_sem', 'view_prog', 'view_yl', 'view_section', 'view_instructor'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.disabled = true; el.style.opacity = '0.65'; }
    });

    // Hide import button (editing not allowed in overlay mode)
    const importGroup = document.querySelector('.pill-import-group');
    if (importGroup) importGroup.style.display = 'none';

    // Load the current active (Published) schedule as base
    refreshOfferings();

    // Fetch the historical overlay sessions
    _fetchOverlaySessions();
}

async function _fetchOverlaySessions() {
    try {
        const resp = await fetch(`/api/schedule/versions/${window._OVERLAY_ID}/sessions`);
        const data = await resp.json();
        if (data.success) {
            window._overlaySessions = data.sessions;
            // If the current schedule is already rendered, add the overlay now
            if (window._lastSessions && currentView === 'calendar') {
                renderOverlayPills(window._overlaySessions, window._lastSessions);
            }
        } else {
            console.warn('[Overlay] sessions fetch failed:', data.error);
        }
    } catch (e) {
        console.error('[Overlay] fetch error:', e);
    }
}

function renderOverlayPills(historicalSessions, currentSessions) {
    const wrapper  = document.getElementById('gridWrapper');
    const table    = document.getElementById('mainTimetable');
    wrapper.querySelectorAll('.historical-pill').forEach(p => p.remove());

    if (!historicalSessions || historicalSessions.length === 0) return;

    const days      = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
    const firstCell = table.querySelector('tbody td:nth-child(2)');
    const timeCol   = table.querySelector('.time-cell');
    const thead     = table.querySelector('thead');
    if (!firstCell || firstCell.offsetWidth === 0) return;

    const colWidth   = firstCell.offsetWidth;
    const pxPerMin   = firstCell.offsetHeight / 30;
    const GRID_ORIGIN = 7 * 60 + 30;

    // Build match set: current slots keyed by daydesc|start_time|roomname
    const matchSet = new Set();
    (currentSessions || []).forEach(s => {
        if (s.daydesc && s.start_time) {
            matchSet.add(`${s.daydesc}|${s.start_time}|${(s.roomname || '').trim().toLowerCase()}`);
        }
    });

    const seen = new Set();
    historicalSessions.forEach(sess => {
        if (!sess.daydesc || !sess.start_time) return;
        const dedupeKey = `${sess.subjectcode}|${sess.daydesc}|${sess.start_time}`;
        if (seen.has(dedupeKey)) return;
        seen.add(dedupeKey);

        const dayIdx = days.indexOf(sess.daydesc);
        if (dayIdx < 0) return;

        let start = timeStrToMins(sess.start_time);
        let end   = timeStrToMins(sess.end_time);
        if (start === null) return;
        if (start < GRID_ORIGIN) start += 12 * 60;
        if (end === null) end = start + 60;
        else if (end <= start) end += 12 * 60;

        const matchKey = `${sess.daydesc}|${sess.start_time}|${(sess.roomname || '').trim().toLowerCase()}`;
        const isMatch  = matchSet.has(matchKey);

        const pill   = document.createElement('div');
        pill.className = 'historical-pill' + (isMatch ? ' historical-pill--match' : '');

        const pillH = (end - start) * pxPerMin - 2;
        pill.style.width  = (colWidth - 8) + 'px';
        pill.style.height = pillH + 'px';
        pill.style.left   = (timeCol.offsetWidth + dayIdx * colWidth + 2) + 'px';
        pill.style.top    = (thead.offsetHeight + (start - GRID_ORIGIN) * pxPerMin + 1) + 'px';

        const instrLast = (sess.instructor || 'TBA').split(',')[0].trim();
        pill.title = `[HISTORICAL] ${sess.subjectcode} — ${sess.subjectname}\n`
                   + `${sess.instructor}\n`
                   + `${fmtTime(sess.start_time)} – ${fmtTime(sess.end_time)}\n`
                   + `${sess.roomname}`;

        const badgeText = isMatch ? 'MATCH' : 'HISTORICAL';
        pill.innerHTML =
            `<div class="hist-pill-badge">${badgeText}</div>`
          + `<div class="pill-subject">${sess.subjectname}</div>`
          + (pillH >= 45 ? `<div class="pill-instructor">${instrLast}</div>` : '')
          + (pillH >= 60 ? `<div class="pill-room">${sess.roomname}</div>` : '');

        wrapper.appendChild(pill);
    });
}

function exitOverlayMode() {
    window.location.href = '/schedule/version-history';
}

async function restoreFromOverlay() {
    if (!window._OVERLAY_ID) return;

    // Check current Draft/Published state before showing confirmation
    let stateData = { isCurrentlyPublished: false, hasActiveDraft: false, hasActivePublished: false };
    try {
        const checkResp = await fetch(`/api/schedule/versions/${window._OVERLAY_ID}/check`);
        const checkData = await checkResp.json();
        if (checkData.success) stateData = checkData;
    } catch (e) { /* proceed with safe defaults */ }

    const { isCurrentlyPublished, hasActiveDraft, hasActivePublished } = stateData;

    // Pick scenario message and buttons
    let title        = 'Restore Revision';
    let message      = 'Are you sure you want to restore this revision as Draft?';
    let primaryLabel = 'Restore Draft';
    let cancelLabel  = 'Cancel';
    let showWarning  = false;

    if (isCurrentlyPublished && hasActiveDraft) {
        title        = 'Active Schedule Conflict';
        message      = 'This schedule currently has an active Published version and an existing Draft. Restoring this revision may replace the Draft and unpublish the current schedule.';
        primaryLabel = 'Continue Restore';
        showWarning  = true;
    } else if (isCurrentlyPublished) {
        title        = 'Restore Published Revision';
        message      = 'This revision is currently Published. Restoring it will automatically unpublish the current schedule. Do you want to continue?';
        primaryLabel = 'Continue Restore';
        showWarning  = true;
    } else if (hasActiveDraft && hasActivePublished) {
        title        = 'Active Schedule Conflict';
        message      = 'This schedule currently has an active Published version and an existing Draft. Restoring this revision may replace the Draft and affect the current schedule.';
        primaryLabel = 'Continue Restore';
        showWarning  = true;
    } else if (hasActiveDraft) {
        title        = 'Replace Existing Draft?';
        message      = 'There is already an existing Draft for this schedule. Restoring this revision will replace the current Draft. Do you want to continue?';
        primaryLabel = 'Replace Draft';
        cancelLabel  = 'Keep Current Draft';
        showWarning  = true;
    }

    const confirmed = await _showOverlayRestoreModal(title, message, primaryLabel, cancelLabel, showWarning);
    if (!confirmed) return;

    const restoreBtn = document.getElementById('btnOverlayRestore');
    if (restoreBtn) {
        restoreBtn.disabled = true;
        restoreBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Restoring…';
    }

    try {
        const resp = await fetch(`/api/schedule/versions/${window._OVERLAY_ID}/restore`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ restore_mode: 'draft' })
        });
        const data = await resp.json();
        if (data.success) {
            _showOverlayToast('Revision restored successfully as Draft.');
            setTimeout(() => { window.location.href = '/schedule/version-history'; }, 2200);
        } else {
            if (restoreBtn) {
                restoreBtn.disabled = false;
                restoreBtn.innerHTML = '<i class="fas fa-undo-alt"></i> Restore Revision';
            }
            alert('Restore failed: ' + (data.error || 'Unknown error'));
        }
    } catch (e) {
        if (restoreBtn) {
            restoreBtn.disabled = false;
            restoreBtn.innerHTML = '<i class="fas fa-undo-alt"></i> Restore Revision';
        }
        alert('Restore failed: ' + e.message);
    }
}

function _showOverlayRestoreModal(title, message, primaryLabel, cancelLabel, showWarning) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:99000;display:flex;align-items:center;justify-content:center;';

        const modal = document.createElement('div');
        modal.style.cssText = 'background:#fff;border-radius:10px;max-width:460px;width:92%;box-shadow:0 12px 40px rgba(0,0,0,0.22);overflow:hidden;font-family:inherit;';

        const header = document.createElement('div');
        header.style.cssText = 'padding:14px 20px;background:#630100;color:#fff;display:flex;align-items:center;gap:10px;font-size:0.9rem;font-weight:900;letter-spacing:0.3px;text-transform:uppercase;';
        header.innerHTML = '<i class="fas fa-undo-alt"></i> ' + title;

        const body = document.createElement('div');
        body.style.cssText = 'padding:20px 24px 0;';

        if (showWarning) {
            const warn = document.createElement('div');
            warn.style.cssText = 'display:flex;align-items:flex-start;gap:10px;background:#fff8e1;border:1px solid #ffe082;border-radius:8px;padding:10px 14px;';
            warn.innerHTML = '<i class="fas fa-exclamation-triangle" style="color:#946300;font-size:1rem;flex-shrink:0;margin-top:2px;"></i>' +
                '<span style="font-size:0.78rem;color:#946300;font-weight:600;line-height:1.55;">' + message + '</span>';
            body.appendChild(warn);
        } else {
            const msg = document.createElement('p');
            msg.style.cssText = 'font-size:0.8rem;color:#555;line-height:1.6;margin:0;';
            msg.textContent = message;
            body.appendChild(msg);
        }

        const actions = document.createElement('div');
        actions.style.cssText = 'padding:14px 24px 20px;display:flex;gap:10px;justify-content:flex-end;border-top:1px solid #f0f0f0;margin-top:16px;';

        const cancelBtn = document.createElement('button');
        cancelBtn.textContent = cancelLabel;
        cancelBtn.style.cssText = 'padding:9px 20px;border-radius:6px;border:1.5px solid #ddd;background:#fff;color:#555;font-weight:800;font-size:0.75rem;letter-spacing:1px;text-transform:uppercase;cursor:pointer;font-family:inherit;';
        cancelBtn.onmouseover = () => { cancelBtn.style.borderColor = '#999'; };
        cancelBtn.onmouseout  = () => { cancelBtn.style.borderColor = '#ddd'; };
        cancelBtn.onclick = () => { overlay.remove(); resolve(false); };

        const confirmBtn = document.createElement('button');
        confirmBtn.innerHTML = '<i class="fas fa-undo-alt"></i> ' + primaryLabel;
        confirmBtn.style.cssText = 'padding:9px 22px;border-radius:6px;border:none;background:#630100;color:#fff;font-weight:800;font-size:0.75rem;letter-spacing:1px;text-transform:uppercase;cursor:pointer;display:inline-flex;align-items:center;gap:6px;font-family:inherit;';
        confirmBtn.onmouseover = () => { confirmBtn.style.background = '#7a0100'; };
        confirmBtn.onmouseout  = () => { confirmBtn.style.background = '#630100'; };
        confirmBtn.onclick = () => { overlay.remove(); resolve(true); };

        actions.appendChild(cancelBtn);
        actions.appendChild(confirmBtn);
        modal.appendChild(header);
        modal.appendChild(body);
        modal.appendChild(actions);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
    });
}

function _showOverlayToast(msg) {
    const toast = document.createElement('div');
    toast.style.cssText = 'position:fixed;bottom:28px;right:28px;background:#1a7a2e;color:#fff;padding:12px 20px;border-radius:8px;font-size:0.8rem;font-weight:800;display:flex;align-items:center;gap:8px;box-shadow:0 4px 20px rgba(0,0,0,0.2);z-index:99999;letter-spacing:0.3px;';
    toast.innerHTML = '<i class="fas fa-check-circle"></i> ' + msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

/* ---- Init ---- */
window.onload = function () {
    document.getElementById('calendarViewWrapper').style.display = 'block';
    document.getElementById('tableViewWrapper').style.display    = 'none';
    updateYearLevels();
    updateSections();
    initOverlayMode();
};
