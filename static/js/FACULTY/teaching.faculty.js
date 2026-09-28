// EMP_NUM, AY_ID, SEM are defined inline in the HTML template above this script

const DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
const PALETTE = ['#16a085','#27ae60','#2980b9','#8e44ad','#2c3e50','#f39c12','#d35400','#c0392b'];

let _sessions      = [];   // official published schedule sessions (one row per meeting-day)
let _localSessions = [];   // published local-arrangement sessions for this faculty
let _myLoad        = null; // /api/faculty/my_teaching_load?source=official response
let _myLoadLocal    = null; // /api/faculty/my_teaching_load?source=local response
let _facMe         = null; // /api/faculty/me response
let _activeTab     = 'weekly';
let _scheduleMode  = 'official'; // 'official' | 'local'
let _programFilter = '';
let _weekOffset    = 0;    // 0 = current real week; cosmetic only, never re-fetches data

function subjectColor(code) {
    let h = 0;
    for (let i = 0; i < code.length; i++) h = code.charCodeAt(i) + ((h << 5) - h);
    return PALETTE[Math.abs(h) % PALETTE.length];
}

// ── Tab switching ────────────────────────────────────────────────────────────
function showTab(name, el) {
    _activeTab = name;
    document.getElementById('tab-weekly').style.display     = name === 'weekly'     ? '' : 'none';
    document.getElementById('tab-assignment').style.display = name === 'assignment' ? '' : 'none';
    document.getElementById('statsWeekly').style.display     = name === 'weekly'     ? '' : 'none';
    document.getElementById('statsAssignment').style.display = name === 'assignment' ? '' : 'none';
    document.getElementById('taPageTitle').textContent = name === 'weekly' ? 'MY WEEKLY SCHEDULE' : 'MY TEACHING ASSIGNMENT';
    document.querySelectorAll('.tab-link').forEach(t => t.classList.remove('active'));
    if (el) el.classList.add('active');

    if (name === 'weekly')     renderCalendar();
    if (name === 'assignment') renderAssignment();
}

// ── Schedule mode toggle (Official/Local — shared by both tabs; Weekly filters
//    its pills by it, Teaching Assignment switches which load dataset shows) ──
function setScheduleMode(mode) {
    _scheduleMode = mode;
    document.getElementById('btnOfficial').classList.toggle('active', mode === 'official');
    document.getElementById('btnLocal').classList.toggle('active', mode === 'local');
    if (_activeTab === 'weekly') renderCalendar(); else renderAssignment();
}

// ── BY PROGRAM filter (weekly view only) ─────────────────────────────────────
function populateProgramFilter() {
    const sel = document.getElementById('programFilter');
    const current = sel.value;
    const programs = Array.from(new Set(_sessions.map(s => s.programcode).filter(Boolean))).sort();
    sel.innerHTML = '<option value="">BY PROGRAM</option>' +
        programs.map(p => `<option value="${p}">${p}</option>`).join('');
    if (programs.includes(current)) sel.value = current;
}

function onProgramFilterChange() {
    _programFilter = document.getElementById('programFilter').value;
    renderCalendar();
}

// ── Week navigation (cosmetic — the schedule is a recurring weekly pattern for
//    the whole AY/semester, not a per-calendar-date schedule, so these controls
//    only move the displayed date label / today-highlight, never the data) ────
function _startOfWeek(d) {
    const day = d.getDay(); // 0=Sun..6=Sat
    const diff = (day === 0 ? -6 : 1) - day; // back up to Monday
    const s = new Date(d);
    s.setDate(d.getDate() + diff);
    s.setHours(0,0,0,0);
    return s;
}
function _fmtShort(d) { return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); }

function navWeek(delta) {
    _weekOffset = delta === 'today' ? 0 : (_weekOffset + delta);
    const base = new Date();
    base.setDate(base.getDate() + _weekOffset * 7);
    const start = _startOfWeek(base);
    const end = new Date(start); end.setDate(start.getDate() + 6);
    document.getElementById('weekRangeLabel').textContent = `Week of ${_fmtShort(start)} – ${_fmtShort(end)}`;
    renderCalendar();
}

// ── Data loading ─────────────────────────────────────────────────────────────
async function loadData() {
    if (!EMP_NUM) return;
    navWeek('today'); // seed the week label on first load
    await Promise.all([
        loadOfficialData(), loadLocalData(),
        loadMyTeachingLoad('official'), loadMyTeachingLoad('local'),
        loadFacultyMe(),
    ]);
    populateProgramFilter();
    if (_activeTab === 'weekly') renderCalendar();
    else renderAssignment();
}

async function loadOfficialData() {
    try {
        const url = `/api/get_faculty_schedule?emp_num=${encodeURIComponent(EMP_NUM)}&ay_id=${encodeURIComponent(AY_ID)}&semester=${encodeURIComponent(SEM)}`;
        const resp = await fetch(url);
        _sessions = await resp.json();
    } catch(e) {
        console.error('loadOfficialData error:', e);
        _sessions = [];
    }
}

async function loadLocalData() {
    try {
        const url = `/api/faculty/my_local_schedule?ay_id=${encodeURIComponent(AY_ID)}&semester=${encodeURIComponent(SEM)}`;
        const resp = await fetch(url);
        if (!resp.ok) { _localSessions = []; return; }
        _localSessions = await resp.json();
    } catch(e) {
        console.error('loadLocalData error:', e);
        _localSessions = [];
    }
}

async function loadMyTeachingLoad(source) {
    try {
        const url = `/api/faculty/my_teaching_load?ay_id=${encodeURIComponent(AY_ID)}&semester=${encodeURIComponent(SEM)}&source=${source}`;
        const resp = await fetch(url);
        const data = await resp.json();
        const result = data && data.success ? data : null;
        if (source === 'local') _myLoadLocal = result; else _myLoad = result;
    } catch(e) {
        console.error('loadMyTeachingLoad error:', e);
        if (source === 'local') _myLoadLocal = null; else _myLoad = null;
    }
}

async function loadFacultyMe() {
    try {
        const resp = await fetch('/api/faculty/me');
        const data = await resp.json();
        _facMe = data && data.success ? data : null;
        const nameEl = document.getElementById('taFacName');
        if (nameEl) nameEl.textContent = _facMe ? _facMe.fullname.trim() : '—';
    } catch(e) {
        console.error('loadFacultyMe error:', e);
        _facMe = null;
    }
}

// ── Legend ───────────────────────────────────────────────────────────────────
function renderLegend(sessions) {
    const el = document.getElementById('calLegend');
    if (!el) return;
    const codes = Array.from(new Set(sessions.map(s => s.subjectcode).filter(Boolean))).sort();
    el.innerHTML = codes.map(code =>
        `<span class="ta-legend-item"><span class="ta-legend-dot" style="background:${subjectColor(code)}"></span>${code}</span>`
    ).join('');
}

// ── Weekly summary cards (Total Classes / Teaching Units / Programs) ─────────
// "Total Classes" = distinct (subjectcode + section) assignments, never raw
// meeting-day rows — same definition as the Teaching Assignment tab's row
// count, so the two tabs never disagree about how many classes exist.
function _groupSessionsForCount(sessions) {
    const map = new Map();
    sessions.forEach(s => {
        const key = `${s.subjectcode}||${s.sectionname||''}||${s.programcode||''}||${s.yearlevel||''}`;
        if (!map.has(key)) {
            map.set(key, { subjectcode: s.subjectcode, programcode: s.programcode, units: parseFloat(s.creditunits) || 0 });
        }
    });
    return Array.from(map.values());
}

function renderWeeklyStats(filteredSessions) {
    const allPrograms = document.getElementById('programFilter').value === '';
    // Match whichever dataset the Official/Local toggle currently shows, so
    // switching modes never leaves the cards reporting the other source's numbers.
    const load = _scheduleMode === 'local' ? _myLoadLocal : _myLoad;
    let totalClasses, teachingUnits, programs;

    if (allPrograms && load) {
        // All Programs: reuse the exact authoritative numbers the Teaching
        // Assignment tab shows for this same source, so the two tabs agree.
        totalClasses  = load.regular.length + load.partTime.length;
        teachingUnits = load.totals.totalUnits;
    } else {
        // A specific program is selected (or the load endpoint hasn't answered
        // yet) — recompute from the de-duplicated session groups for just that
        // subset. Basis here is per-assignment credit units, since narrowing to
        // one program doesn't have a corresponding narrowed hours-bucket call.
        const groups = _groupSessionsForCount(filteredSessions);
        totalClasses  = groups.length;
        teachingUnits = groups.reduce((sum, g) => sum + g.units, 0);
    }
    programs = new Set(filteredSessions.map(s => s.programcode).filter(Boolean)).size;

    document.getElementById('statTotalClasses').textContent   = totalClasses;
    document.getElementById('statTeachingUnits').textContent  = teachingUnits;
    document.getElementById('statPrograms').textContent       = programs;
}

// ── Calendar renderer ────────────────────────────────────────────────────────
function renderCalendar() {
    const source = _scheduleMode === 'local' ? _localSessions : _sessions;
    const sessions = _programFilter
        ? source.filter(s => (s.programcode || '') === _programFilter)
        : source;

    renderLegend(_programFilter ? sessions : (_scheduleMode === 'local' ? _localSessions : _sessions));
    if (_scheduleMode === 'official') renderWeeklyStats(sessions);

    // Highlight the real current weekday's column only while looking at the
    // actual current week — navigating to a different nominal week shouldn't
    // claim some other day is "today".
    document.querySelectorAll('.cal-table thead th').forEach(th => th.classList.remove('ta-today-col'));
    if (_weekOffset === 0) {
        const jsDay = new Date().getDay(); // 0=Sun..6=Sat
        const colIdx = jsDay === 0 ? 7 : jsDay; // MON..SUN -> col 1..7 (col 0 is TIME)
        const th = document.querySelector(`.cal-table thead th:nth-child(${colIdx + 1})`);
        if (th) th.classList.add('ta-today-col');
    }

    const inner   = document.getElementById('calInner');
    const table   = document.getElementById('calTable');
    const emptyEl = document.getElementById('calEmptyMsg');

    inner.querySelectorAll('.cal-pill').forEach(p => p.remove());

    if (!sessions.length) { emptyEl.style.display = 'block'; return; }
    emptyEl.style.display = 'none';

    const firstCell = table.querySelector('tbody td:nth-child(2)');
    const tcCol     = table.querySelector('.tc');
    const thead     = table.querySelector('thead');

    if (!firstCell || firstCell.offsetWidth === 0) { requestAnimationFrame(renderCalendar); return; }

    const colW    = firstCell.offsetWidth;
    const rowH    = firstCell.offsetHeight;
    const leftOff = tcCol.offsetWidth;
    const topOff  = thead.offsetHeight;

    const byDay = {};
    sessions.forEach(s => {
        if (!s.starttimeid || !s.endtimeid) return;
        (byDay[s.daydesc] = byDay[s.daydesc] || []).push(s);
    });

    Object.keys(byDay).forEach(day => {
        const dayIdx = DAYS.indexOf(day);
        if (dayIdx < 0) return;
        const daySessions = byDay[day].slice().sort((a,b) => a.starttimeid - b.starttimeid);

        daySessions.forEach((sess, idx) => {
            const s = sess.starttimeid, e = sess.endtimeid;
            let overlapCount = 0, overlapIndex = 0;
            daySessions.forEach((other, oIdx) => {
                if (s < other.endtimeid && e > other.starttimeid) {
                    overlapCount++;
                    if (idx > oIdx) overlapIndex++;
                }
            });

            const pill = document.createElement('div');
            pill.className = 'cal-pill' + (_scheduleMode === 'local' ? ' local-pill' : '');
            pill.style.backgroundColor = subjectColor(sess.subjectcode);

            const w = (colW - 6) / (overlapCount || 1);
            pill.style.width  = (w - 3) + 'px';
            pill.style.height = Math.max((e - s) * rowH - 4, 18) + 'px';
            pill.style.left   = (leftOff + dayIdx * colW + overlapIndex * w + 3) + 'px';
            pill.style.top    = (topOff + (s - 1) * rowH + 2) + 'px';

            const yearSec = sess.yearlevel ? `${sess.yearlevel} - ${sess.programcode || ''}` : (sess.programcode || '');
            pill.innerHTML = `
                <div class="cal-pill-code">${sess.subjectcode}</div>
                <div class="cal-pill-sub">${yearSec || sess.roomname || ''}</div>`;
            pill.title = `${sess.subjectcode} – ${sess.subjectname}\n${sess.time_range || ''}\nRoom: ${sess.roomname || 'TBA'}\nYear/Section: ${yearSec || '—'}`;
            pill.onclick = () => openDetail(sess);
            inner.appendChild(pill);
        });
    });
}

// ── Teaching Assignment renderer (official schedule only, authoritative load) ─
function renderAssignment() {
    const box = document.getElementById('taFacBoxName');
    if (_facMe) box.textContent = _facMe.fullname.trim();

    // The Official/Local toggle (shared with Weekly Schedule) picks which
    // dataset this tab shows — the two are never merged into one table.
    const isLocal = _scheduleMode === 'local';
    const load = isLocal ? _myLoadLocal : _myLoad;
    const emptyMsg = isLocal
        ? 'No Local Schedule.'
        : 'No published teaching assignments are available for this academic period.';

    if (!load) {
        document.getElementById('taEmptyMsg').textContent = emptyMsg;
        document.getElementById('taEmptyMsg').style.display = 'block';
        document.getElementById('taTablesWrap').style.display = 'none';
        return;
    }

    document.getElementById('taEmpType').textContent = load.employee_type || '—';

    const t = load.totals;
    document.getElementById('statTotalUnits').textContent   = t.totalUnits;
    document.getElementById('statRegularUnits').textContent = t.regularUnits;
    document.getElementById('statPtUnits').textContent      = t.partTimeUnits;
    document.getElementById('statTsUnits').textContent      = t.tsUnits;
    document.getElementById('taTotalUnitsInline').textContent     = `${t.totalUnits} / ${t.maxUnits}`;
    document.getElementById('taAvailableUnitsInline').textContent = t.availableUnits;

    if (!load.has_schedule) {
        document.getElementById('taEmptyMsg').textContent = emptyMsg;
        document.getElementById('taEmptyMsg').style.display = 'block';
        document.getElementById('taTablesWrap').style.display = 'none';
        return;
    }
    document.getElementById('taEmptyMsg').style.display = 'none';
    document.getElementById('taTablesWrap').style.display = '';

    const makeRow = row => `<tr>
        <td style="font-weight:800;color:#630100;">${row.subjectcode}</td>
        <td>${row.subjectname}</td>
        <td>${row.units ?? '—'}</td>
        <td>${row.year_section}</td>
        <td>${row.time_range}</td>
        <td>${row.days}</td>
        <td>${row.room}</td>
        <td>${row.effectivity}</td>
    </tr>`;

    document.getElementById('tbl-regular').innerHTML = load.regular.length
        ? load.regular.map(makeRow).join('')
        : `<tr><td colspan="8" style="text-align:center;color:#999;padding:16px;">${isLocal ? 'No Local Schedule.' : 'No regular load assigned.'}</td></tr>`;

    document.getElementById('tbl-pt').innerHTML = load.partTime.length
        ? load.partTime.map(makeRow).join('')
        : `<tr><td colspan="8" style="text-align:center;color:#999;padding:16px;">${isLocal ? 'No Local Schedule.' : 'No part-time load assigned.'}</td></tr>`;

    document.getElementById('total-regular-units').textContent = t.regularUnits || '—';
    document.getElementById('total-pt-units').textContent      = t.partTimeUnits || '—';
    document.getElementById('total-overall-units').textContent = t.totalUnits || '—';
}

// ── Detail modal ─────────────────────────────────────────────────────────────
function openDetail(sess) {
    document.getElementById('det-code').textContent    = sess.subjectcode;
    document.getElementById('det-name').textContent    = sess.subjectname;
    document.getElementById('det-day').textContent     = sess.daydesc;
    document.getElementById('det-time').textContent    = sess.time_range;
    document.getElementById('det-room').textContent    = sess.roomname || 'TBA';
    document.getElementById('det-section').textContent = sess.yearlevel ? `${sess.yearlevel} - ${sess.programcode || ''}` : (sess.programcode || '—');
    document.getElementById('det-units').textContent   = sess.creditunits;
    document.getElementById('det-status').innerHTML    =
        sess.status === 'Published'
            ? '<span class="badge-pub">Published</span>'
            : '<span class="badge-draft">Draft</span>';
    document.getElementById('detailModal').classList.add('active');
}

function closeDetail() { document.getElementById('detailModal').classList.remove('active'); }

document.getElementById('detailModal').addEventListener('click', function(e) {
    if (e.target === this) closeDetail();
});

// ── Export (client-side PDF of whichever tab is currently on screen — never
//    re-fetches, so it can never diverge from, or leak Draft data beyond,
//    what's already displayed) ─────────────────────────────────────────────
function exportActiveTabPdf() {
    if (_activeTab === 'weekly') exportWeeklyPdf(); else exportAssignmentPdf();
}

function _pdfHeader(doc, title) {
    const W = doc.internal.pageSize.getWidth();
    doc.setFont('helvetica', 'bold'); doc.setFontSize(13);
    doc.text('PUPLC SCHEDULING SYSTEM', W / 2, 12, { align: 'center' });
    doc.setFont('helvetica', 'normal'); doc.setFontSize(10);
    doc.text(title, W / 2, 19, { align: 'center' });
    const fac = _facMe ? _facMe.fullname.trim() : (EMP_NUM || '');
    doc.setFontSize(8);
    doc.text(`${fac}  •  AY ${AY_ID}  •  Semester ${SEM}  •  ${_scheduleMode === 'local' ? 'Local' : 'Official'} Schedule`, W / 2, 25, { align: 'center' });
    return 32;
}

function exportWeeklyPdf() {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4' });
    let y = _pdfHeader(doc, 'MY WEEKLY SCHEDULE');
    const source = _scheduleMode === 'local' ? _localSessions : _sessions;
    const sessions = (_programFilter ? source.filter(s => (s.programcode || '') === _programFilter) : source)
        .slice().sort((a, b) => DAYS.indexOf(a.daydesc) - DAYS.indexOf(b.daydesc) || a.starttimeid - b.starttimeid);
    doc.autoTable({
        startY: y,
        head: [['Subject', 'Description', 'Day', 'Time', 'Room', 'Year/Section']],
        body: sessions.map(s => [
            s.subjectcode, s.subjectname, s.daydesc, s.time_range || '',
            s.roomname || 'TBA', s.yearlevel ? `${s.yearlevel} - ${s.programcode || ''}` : (s.programcode || '—'),
        ]),
        styles: { fontSize: 8 },
        headStyles: { fillColor: [99, 1, 0] },
    });
    doc.save(`Weekly_Schedule_${EMP_NUM}.pdf`);
}

function exportAssignmentPdf() {
    const isLocal = _scheduleMode === 'local';
    const load = isLocal ? _myLoadLocal : _myLoad;
    if (!load) return;
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4' });
    let y = _pdfHeader(doc, `MY TEACHING ASSIGNMENT (${isLocal ? 'Local' : 'Official'} Schedule)`);
    const cols = ['Subject', 'Description', 'Units', 'Year/Section', 'Time', 'Day/s', 'Room', 'Effectivity'];
    const toRow = r => [r.subjectcode, r.subjectname, r.units, r.year_section, r.time_range, r.days, r.room, r.effectivity];

    doc.setFont('helvetica', 'bold'); doc.setFontSize(10);
    doc.text('REGULAR LOAD', 10, y); y += 4;
    doc.autoTable({ startY: y, head: [cols], body: load.regular.map(toRow), styles: { fontSize: 7.5 }, headStyles: { fillColor: [99, 1, 0] } });
    y = doc.lastAutoTable.finalY + 8;

    doc.text('PART TIME LOAD', 10, y); y += 4;
    doc.autoTable({ startY: y, head: [cols], body: load.partTime.map(toRow), styles: { fontSize: 7.5 }, headStyles: { fillColor: [99, 1, 0] } });
    y = doc.lastAutoTable.finalY + 8;

    const t = load.totals;
    doc.setFontSize(9);
    doc.text(`TOTAL REGULAR LOAD: ${t.regularUnits} units    TOTAL PART-TIME LOAD: ${t.partTimeUnits} units    AVAILABLE UNITS: ${t.availableUnits}`, 10, y);
    doc.save(`Teaching_Assignment_${isLocal ? 'Local' : 'Official'}_${EMP_NUM}.pdf`);
}

window.addEventListener('DOMContentLoaded', loadData);
