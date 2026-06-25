/* schedule.faculty.js — Class Schedule (SIS) for Faculty view
   FCS_ACTIVE_AY and FCS_ACTIVE_SEM are defined inline in the HTML template. */

/* ── Day sort order ── */
const _FCS_DAY_SORT = {
    'MON':0,'TUE':1,'WED':2,'THU':3,'FRI':4,'SAT':5,'SUN':6,
    'Monday':0,'Tuesday':1,'Wednesday':2,'Thursday':3,'Friday':4,'Saturday':5,'Sunday':6
};
function _sortDays(arr) {
    return [...arr].sort((a, b) => (_FCS_DAY_SORT[a.trim()] ?? 99) - (_FCS_DAY_SORT[b.trim()] ?? 99));
}

/* ── Color palette ── */
const fcsColorPalette = ['#16a085','#27ae60','#2980b9','#8e44ad','#2c3e50','#f39c12','#d35400','#c0392b'];
function fcsSubjectColor(code) {
    let h = 0;
    for (let i = 0; i < (code || '').length; i++) h = code.charCodeAt(i) + ((h << 5) - h);
    return fcsColorPalette[Math.abs(h) % fcsColorPalette.length];
}

/* ── Time helpers ── */
function fcsTimeToMins(t) {
    if (!t || typeof t !== 'string') return null;
    const parts = t.split(':');
    if (parts.length < 2) return null;
    return parseInt(parts[0]) * 60 + parseInt(parts[1]);
}
function fcsFmtTime(t) {
    if (!t) return '—';
    const [hh, mm] = t.split(':').map(Number);
    const suffix = hh >= 12 ? 'PM' : 'AM';
    const h12 = hh > 12 ? hh - 12 : (hh === 0 ? 12 : hh);
    return h12 + ':' + String(mm).padStart(2, '0') + ' ' + suffix;
}

/* ── State ── */
let fcsCurrentView = 'calendar';
let _fcsSessions   = [];
let _fcsController = null;

/* ── Init data ── */
const _fcsInitEl   = document.getElementById('fcs-init-data');
const _FCS_PROGRAMS = JSON.parse(_fcsInitEl.dataset.programs || '[]');
const _FCS_FACULTY  = JSON.parse(_fcsInitEl.dataset.faculty  || '[]');

/* ── Bootstrap ── */
document.addEventListener('DOMContentLoaded', () => {
    _fcsPopulateDropdowns();
    // Show prompt — do NOT auto-load; wait for faculty to select a filter
    document.getElementById('gridLabel').innerText = 'SELECT FILTERS TO VIEW SCHEDULE';
});

/* ── Dropdown population ── */
function _fcsPopulateDropdowns() {
    const progSel  = document.getElementById('view_prog');
    const instrSel = document.getElementById('view_instructor');

    _FCS_PROGRAMS.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.code;
        opt.textContent = p.name;
        progSel.appendChild(opt);
    });

    _FCS_FACULTY.forEach(f => {
        const opt = document.createElement('option');
        opt.value = f.emp;
        opt.textContent = f.name;
        instrSel.appendChild(opt);
    });
}

/* ── Year level rules ── */
function fcsMaxYl(progCode) {
    const code = (progCode || '').toUpperCase();
    if (code === 'BSARCH') return 5;
    if (code.startsWith('D')) return 3;
    return 4;
}
function fcsUpdateYearLevels() {
    const prog = document.getElementById('view_prog').value;
    const max  = fcsMaxYl(prog);
    const sel  = document.getElementById('view_yl');
    const cur  = parseInt(sel.value);
    sel.innerHTML = '<option value="">SELECT</option>';
    const labels = ['1ST YEAR','2ND YEAR','3RD YEAR','4TH YEAR','5TH YEAR'];
    for (let i = 1; i <= max; i++) {
        const opt = document.createElement('option');
        opt.value = i; opt.textContent = labels[i-1];
        if (i === Math.min(cur, max)) opt.selected = true;
        sel.appendChild(opt);
    }
}

async function fcsUpdateSections() {
    const prog = document.getElementById('view_prog').value;
    const yl   = document.getElementById('view_yl').value;
    const sel  = document.getElementById('view_section');
    sel.innerHTML = '<option value="">ALL SECTIONS</option>';
    if (!prog || !yl) return;
    try {
        const url = `/api/sections-by-program?program=${encodeURIComponent(prog)}&yearLevel=${encodeURIComponent(yl)}&ay=${encodeURIComponent(FCS_ACTIVE_AY)}&semester=${FCS_ACTIVE_SEM}`;
        const resp = await fetch(url);
        const data = await resp.json();
        (data.sections || []).forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.id;
            opt.textContent = s.name;
            sel.appendChild(opt);
        });
    } catch(e) { console.error('[fcs] sections fetch error', e); }
}

/* ── Filter change handlers ── */
function fcsOnProgChange() {
    fcsUpdateYearLevels();
    document.getElementById('view_section').innerHTML = '<option value="">ALL SECTIONS</option>';
    fcsLoadSchedule();
}

function fcsOnYlChange() {
    fcsUpdateSections();
    fcsLoadSchedule();
}

/* ── Load schedule ── */
async function fcsLoadSchedule() {
    const prog   = document.getElementById('view_prog').value;
    const yl     = document.getElementById('view_yl').value;
    const sec    = document.getElementById('view_section').value;
    const instr  = document.getElementById('view_instructor').value;

    // Require section OR instructor — program + year level alone shows too many results
    if (!sec && !instr) {
        document.getElementById('gridLabel').innerText = 'SELECT FILTERS TO VIEW SCHEDULE';
        document.getElementById('gridWrapper').querySelectorAll('.schedule-pill').forEach(p => p.remove());
        document.getElementById('offeringsTableBody').innerHTML =
            '<tr class="empty-row"><td colspan="11"><span class="empty-msg">Please select a Section or Instructor to view the schedule.</span></td></tr>';
        _fcsSessions = [];
        return;
    }

    const params = new URLSearchParams({ ay_id: FCS_ACTIVE_AY, semester: FCS_ACTIVE_SEM });
    if (prog)  params.set('program',    prog);
    if (yl)    params.set('year_level', yl);
    if (sec)   params.set('section_id', sec);
    if (instr) params.set('emp_num',    instr);

    // Update header label
    const progSel  = document.getElementById('view_prog');
    const secSel   = document.getElementById('view_section');
    const instrSel = document.getElementById('view_instructor');
    const progText  = prog  ? progSel.options[progSel.selectedIndex].text.toUpperCase() : '';
    const ylOrdinals = ['1st','2nd','3rd','4th','5th'];
    const ylText    = yl ? (ylOrdinals[parseInt(yl)-1] || yl) + ' Year' : '';
    const secText   = sec ? ' · ' + secSel.options[secSel.selectedIndex].text : '';
    const instrText = instr ? ' · ' + instrSel.options[instrSel.selectedIndex].text : '';
    const semLabel  = { A: '1ST SEMESTER', B: '2ND SEMESTER', C: 'SUMMER' }[FCS_ACTIVE_SEM] || FCS_ACTIVE_SEM;
    document.getElementById('gridLabel').innerText =
        `${progText}${ylText ? '  —  ' + ylText : ''}${secText}${instrText}  ·  ${semLabel}`;

    if (_fcsController) _fcsController.abort();
    _fcsController = new AbortController();

    try {
        const resp = await fetch('/api/faculty/schedule?' + params.toString() + '&_t=' + Date.now(), {
            signal: _fcsController.signal, cache: 'no-store'
        });
        if (!resp.ok) { console.error('[fcs] API error:', resp.status); return; }
        const data = await resp.json();
        _fcsSessions = data.success ? (data.sessions || []) : [];
        requestAnimationFrame(() => fcsRenderCurrentView(_fcsSessions));
    } catch(e) {
        if (e.name !== 'AbortError') console.error('[fcs] load error:', e);
    }
}

/* ── View toggle ── */
function fcsSetView(type) {
    fcsCurrentView = type;
    document.getElementById('btn-calendar').classList.toggle('active', type === 'calendar');
    document.getElementById('btn-table').classList.toggle('active', type === 'table');
    document.getElementById('calendarViewWrapper').style.display = type === 'calendar' ? 'block' : 'none';
    document.getElementById('tableViewWrapper').style.display    = type === 'table'    ? 'block' : 'none';
    fcsRenderCurrentView(_fcsSessions);
}

function fcsRenderCurrentView(sessions) {
    if (fcsCurrentView === 'calendar') fcsRenderCalendar(sessions);
    else fcsRenderTable(sessions);
}

/* ── Calendar render ── */
function fcsRenderCalendar(sessions) {
    const wrapper = document.getElementById('gridWrapper');
    const table   = document.getElementById('mainTimetable');
    wrapper.querySelectorAll('.schedule-pill, .cal-no-data').forEach(p => p.remove());

    if (!sessions || sessions.length === 0) {
        const msg = document.createElement('div');
        msg.className = 'cal-no-data';
        msg.style.cssText = 'position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#aaa;font-size:13px;pointer-events:none;z-index:5;text-align:center;';
        msg.textContent = 'No schedule data found for the selected filters.';
        wrapper.appendChild(msg);
        return;
    }

    // Deduplicate
    const seen = new Set();
    sessions = sessions.filter(s => {
        const key = `${s.subjectcode}|${s.daydesc}|${s.start_time}`;
        if (seen.has(key)) return false;
        seen.add(key); return true;
    });

    const days = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
    const firstCell = table.querySelector('tbody td:nth-child(2)');
    const timeCol   = table.querySelector('.time-cell');
    const thead     = table.querySelector('thead');
    if (!firstCell || firstCell.offsetWidth === 0 || firstCell.offsetHeight === 0) {
        requestAnimationFrame(() => fcsRenderCalendar(sessions)); return;
    }

    const colWidth   = firstCell.offsetWidth;
    const pxPerMin   = firstCell.offsetHeight / 30;
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
        daySessions.sort((a,b) => fcsTimeToMins(a.start_time) - fcsTimeToMins(b.start_time));

        daySessions.forEach((sess, idx) => {
            let start = fcsTimeToMins(sess.start_time), end = fcsTimeToMins(sess.end_time);
            if (start === null) return;
            if (start < GRID_ORIGIN) start += 12 * 60;
            if (end !== null && end <= start) end += 12 * 60;

            let overlapCount = 0, overlapIndex = 0;
            daySessions.forEach((other, oIdx) => {
                let oS = fcsTimeToMins(other.start_time), oE = fcsTimeToMins(other.end_time);
                if (oS < GRID_ORIGIN) oS += 12 * 60; if (oE !== null && oE <= oS) oE += 12 * 60;
                if (start < oE && end > oS) { overlapCount++; if (idx > oIdx) overlapIndex++; }
            });

            const pill = document.createElement('div');
            pill.className = 'schedule-pill';
            pill.style.backgroundColor = fcsSubjectColor(sess.subjectcode);
            const w = (colWidth - 6) / (overlapCount || 1);
            const pillH = (end - start) * pxPerMin - 2;

            pill.style.width  = (w - 2) + 'px';
            pill.style.height = pillH + 'px';
            pill.style.left   = (timeCol.offsetWidth + (dayIdx * colWidth) + (overlapIndex * w) + 4) + 'px';
            pill.style.top    = (thead.offsetHeight + (start - GRID_ORIGIN) * pxPerMin + 1) + 'px';

            const instrLast = (sess.instructor || 'TBA').split(',')[0].trim();
            pill.title = `${sess.subjectcode}\n${sess.subjectname}\n${sess.instructor}\n${fcsFmtTime(sess.start_time)} – ${fcsFmtTime(sess.end_time)}\n${sess.roomname}`;

            const pillContent = `
                <div class="pill-subject">${sess.subjectname}</div>
                <div class="pill-instructor">${instrLast}</div>
                <div class="pill-room">${sess.roomname || 'TBA'}</div>`;

            if (pillH < 55) pill.classList.add('schedule-pill--compact');
            else if (pillH < 85) pill.classList.add('schedule-pill--medium');
            pill.innerHTML = pillContent;
            wrapper.appendChild(pill);
        });
    });
}

/* ── Table render ── */
function fcsRenderTable(sessions) {
    const tbody = document.getElementById('offeringsTableBody');
    if (!sessions || sessions.length === 0) {
        tbody.innerHTML = '<tr class="empty-row"><td colspan="11"><span class="empty-msg"><i class="fas fa-calendar-times" style="margin-right:6px;"></i>No schedule data found for the selected filters.</span></td></tr>';
        return;
    }

    const grouped = {};
    sessions.forEach(sess => {
        const key = `${sess.subjectcode}||${sess.instructor}||${sess.sectionname}`;
        if (!grouped[key]) grouped[key] = { ...sess, daysArr: [], timesArr: [] };
        if (sess.daydesc) {
            const ab = _fcsDayAbbr(sess.daydesc);
            if (!grouped[key].daysArr.includes(ab)) grouped[key].daysArr.push(ab);
        }
        if (sess.start_time) {
            const t = `${fcsFmtTime(sess.start_time)} - ${fcsFmtTime(sess.end_time)}`;
            if (!grouped[key].timesArr.includes(t)) grouped[key].timesArr.push(t);
        }
    });

    const groupedRows = Object.values(grouped);
    const dataHtml = groupedRows.map(sess => {
        const yl = sess.yearlevel || '';
        const prog = sess.programcode || '';
        const sec  = sess.sectionname || '';
        const courseLabel = prog && yl ? `${prog} ${yl}${sec ? ' - ' + sec : ''}` : (prog || '-');
        const instrDisplay = sess.instructor && sess.instructor !== 'TBA'
            ? `<span title="${sess.instructor}">${sess.instructor}</span>`
            : '<span style="color:#aaa;font-style:italic;">TBA</span>';
        const daysDisplay  = sess.daysArr.length  ? _sortDays(sess.daysArr).join('/') : '<span style="color:#aaa;">—</span>';
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

function _fcsDayAbbr(day) {
    const map = { Monday:'MON', Tuesday:'TUE', Wednesday:'WED',
                  Thursday:'THU', Friday:'FRI', Saturday:'SAT', Sunday:'SUN' };
    return map[day] || day;
}

/* ── Export ── */
function fcsExport() {
    const prog = document.getElementById('view_prog').value;
    const yl   = document.getElementById('view_yl').value;
    if (!prog) { alert('Please select a program before exporting.'); return; }
    window.location.href = `/api/export_schedule?program=${encodeURIComponent(prog)}&year_level=${yl}&semester=${FCS_ACTIVE_SEM}&ay=${encodeURIComponent(FCS_ACTIVE_AY)}`;
}
