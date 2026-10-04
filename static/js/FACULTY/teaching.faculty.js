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
let _weekOffset    = 0;    // 0 = week containing today; -1 = previous week, +1 = next week …
let _makeups       = [];   // approved make-up classes dated inside the displayed week
let _makeupWeekKey = '';   // week (Monday ISO) _makeups was loaded for

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

// ── Week navigation ──────────────────────────────────────────────────────────
// The class schedule repeats every week of the semester, so each displayed
// week shows those recurring classes on that week's dates — only on days that
// fall inside the semester — plus any approved make-up classes dated in that
// week (fetched per week). ← / → move one week; TODAY returns to the week
// containing today and highlights today's column.
function _startOfWeek(d) {
    const day = d.getDay(); // 0=Sun..6=Sat
    const diff = (day === 0 ? -6 : 1) - day; // back up to Monday
    const s = new Date(d);
    s.setDate(d.getDate() + diff);
    s.setHours(0,0,0,0);
    return s;
}
function _fmtShort(d) { return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }); }
function _iso(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function _parseIso(s) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || '');
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
}

// The 7 dates (Mon..Sun) of the displayed week.
function _weekDates() {
    const start = _startOfWeek(new Date());
    start.setDate(start.getDate() + _weekOffset * 7);
    return Array.from({ length: 7 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d; });
}
function _inSemester(d) {
    const iso = _iso(d);
    return (!SEM_START || iso >= SEM_START) && (!SEM_END || iso <= SEM_END);
}

function _weekLabel(dates) {
    const a = dates[0], b = dates[6];
    const left = _fmtShort(a) + (a.getFullYear() !== b.getFullYear() ? `, ${a.getFullYear()}` : '');
    return `Week of ${left} – ${_fmtShort(b)}, ${b.getFullYear()}`;
}
function _relativeWeek(n) {
    if (n === 0)  return 'This week';
    if (n === -1) return 'Last week';
    if (n === 1)  return 'Next week';
    return n < 0 ? `${-n} weeks ago` : `In ${n} weeks`;
}

function _updateWeekToolbar() {
    const dates = _weekDates();
    document.getElementById('weekRangeLabel').textContent = _weekLabel(dates);
    const badge = document.getElementById('weekRelBadge');
    badge.textContent = _relativeWeek(_weekOffset);
    badge.classList.toggle('is-current', _weekOffset === 0);
    const today = document.getElementById('weekTodayBtn');
    today.classList.toggle('ta-nav-current', _weekOffset === 0);
    today.title = _weekOffset === 0 ? 'You are viewing the current week' : 'Go back to the current week';
    today.setAttribute('aria-pressed', _weekOffset === 0 ? 'true' : 'false');
}

async function navWeek(delta) {
    _weekOffset = delta === 'today' ? 0 : (_weekOffset + delta);
    _updateWeekToolbar();
    renderCalendar();                     // recurring classes show immediately
    await loadMakeups();                  // then that week's make-ups are added
    renderCalendar();
}

async function loadMakeups() {
    const dates = _weekDates();
    const key = _iso(dates[0]);
    try {
        const resp = await fetch(`/api/faculty/my_makeups?start=${key}&end=${_iso(dates[6])}`);
        const data = resp.ok ? await resp.json() : null;
        if (key !== _iso(_weekDates()[0])) return;      // user already moved to another week
        _makeups = Array.isArray(data) ? data : [];
    } catch (e) {
        console.error('loadMakeups error:', e);
        _makeups = [];
    }
    _makeupWeekKey = key;
}

// ── Data loading ─────────────────────────────────────────────────────────────
async function loadData() {
    if (!EMP_NUM) return;
    _updateWeekToolbar(); // seed the week label on first load
    loadMakeups().then(() => { if (_activeTab === 'weekly') renderCalendar(); });
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

    // Dates under each day header; today's column is highlighted only in the
    // week that actually contains today; days outside the semester are shaded.
    const dates    = _weekDates();
    const todayIso = _iso(new Date());
    const inSem    = dates.map(_inSemester);
    const ths      = document.querySelectorAll('#calTable thead th.cal-day-th');
    ths.forEach((th, i) => {
        th.querySelector('.cal-th-date').textContent = _fmtShort(dates[i]);
        const isToday = _iso(dates[i]) === todayIso;
        th.classList.toggle('ta-today-col', isToday);
        th.classList.toggle('cal-off-day-th', !inSem[i] && !isToday);
        th.setAttribute('aria-current', isToday ? 'date' : 'false');
        th.title = isToday ? 'Today' : (inSem[i] ? '' : 'Outside the semester — no classes');
    });
    document.querySelectorAll('#calTable tbody tr').forEach(tr => {
        tr.querySelectorAll('td:not(.tc)').forEach((td, i) => {
            td.classList.toggle('cal-today-cell', _iso(dates[i]) === todayIso && inSem[i]);
            td.classList.toggle('cal-off-day', !inSem[i]);
        });
    });

    // Official mode also shows the approved make-up classes dated this week.
    const makeups = (_scheduleMode === 'official' && _makeupWeekKey === _iso(dates[0]) ? _makeups : [])
        .filter(m => !_programFilter || (m.programcode || '') === _programFilter)
        .map(m => ({ ...m, daydesc: DAYS[dates.findIndex(d => _iso(d) === m.exception_date)],
                     roomname: m.room, creditunits: m.units, isMakeup: true }))
        .filter(m => m.daydesc);
    // Recurring classes only on days inside the semester.
    const shown = sessions.filter(s => inSem[DAYS.indexOf(s.daydesc)]).concat(makeups);

    const inner   = document.getElementById('calInner');
    const table   = document.getElementById('calTable');
    const emptyEl = document.getElementById('calEmptyMsg');
    const noteEl  = document.getElementById('calWeekNote');

    inner.querySelectorAll('.cal-pill').forEach(p => p.remove());

    const semRange = SEM_START && SEM_END
        ? ` (${_fmtShort(_parseIso(SEM_START))}, ${_parseIso(SEM_START).getFullYear()} – ${_fmtShort(_parseIso(SEM_END))}, ${_parseIso(SEM_END).getFullYear()})` : '';
    let note = '';
    if (!inSem.some(Boolean)) {
        note = `<i class="fa-solid fa-calendar-xmark"></i>This week is outside the semester${semRange}, so there are no classes. Use TODAY to return to the current week.`;
    } else if (!inSem.every(Boolean)) {
        note = `<i class="fa-solid fa-circle-info"></i>Shaded days fall outside the semester${semRange} — no classes are held on those days.`;
    }
    if (makeups.length) {
        note += `${note ? '<br>' : ''}<i class="fa-solid fa-rotate"></i>${makeups.length} approved make-up class${makeups.length > 1 ? 'es' : ''} this week (marked MAKE-UP).`;
    }
    noteEl.innerHTML = note;
    noteEl.style.display = note ? '' : 'none';

    if (!shown.length) {
        emptyEl.textContent = inSem.some(Boolean) ? 'No schedule found for the selected period.' : 'No classes this week — it is outside the semester.';
        emptyEl.style.display = 'block';
        return;
    }
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
    shown.forEach(s => {
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
            pill.className = 'cal-pill' + (_scheduleMode === 'local' ? ' local-pill' : '') + (sess.isMakeup ? ' makeup-pill' : '');
            pill.style.backgroundColor = subjectColor(sess.subjectcode);

            const w = (colW - 6) / (overlapCount || 1);
            pill.style.width  = (w - 3) + 'px';
            pill.style.height = Math.max((e - s) * rowH - 4, 18) + 'px';
            pill.style.left   = (leftOff + dayIdx * colW + overlapIndex * w + 3) + 'px';
            pill.style.top    = (topOff + (s - 1) * rowH + 2) + 'px';

            const yearSec = sess.yearlevel ? `${sess.yearlevel} - ${sess.programcode || ''}` : (sess.programcode || '');
            pill.innerHTML = `
                <div class="cal-pill-code">${sess.subjectcode}</div>
                <div class="cal-pill-sub">${yearSec || sess.roomname || ''}</div>
                ${sess.isMakeup ? '<span class="cal-pill-badge">MAKE-UP</span>' : ''}`;
            pill.title = `${sess.isMakeup ? 'MAKE-UP CLASS · ' + sess.exception_date + '\n' : ''}${sess.subjectcode} – ${sess.subjectname}\n${sess.time_range || ''}\nRoom: ${sess.roomname || 'TBA'}\nYear/Section: ${yearSec || '—'}`;
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
        <td>${String(row.time_range || '').split(', ').join('<br>')}</td>
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
    document.getElementById('det-day').textContent     = sess.isMakeup
        ? `${sess.daydesc}, ${_fmtShort(_parseIso(sess.exception_date))} (one time only)` : sess.daydesc;
    document.getElementById('det-time').textContent    = sess.time_range;
    document.getElementById('det-room').textContent    = sess.roomname || 'TBA';
    document.getElementById('det-section').textContent = sess.yearlevel ? `${sess.yearlevel} - ${sess.programcode || ''}` : (sess.programcode || '—');
    document.getElementById('det-units').textContent   = sess.creditunits;
    document.getElementById('det-status').innerHTML    =
        sess.isMakeup ? '<span class="badge-pub">Approved Make-up Class</span>'
        : sess.status === 'Published'
            ? '<span class="badge-pub">Published</span>'
            : '<span class="badge-draft">Draft</span>';
    document.getElementById('detailModal').classList.add('active');
}

function closeDetail() { document.getElementById('detailModal').classList.remove('active'); }

document.getElementById('detailModal').addEventListener('click', function(e) {
    if (e.target === this) closeDetail();
});

// ── Export (XLSX / CSV / PDF / DOCX) — one dialog for both tabs ─────────────
// Same dialog and flow as Reports > Subject/Faculty Assignment Export: pick one
// or more formats → Export → the server builds the file(s) with the system's
// official letterhead from the same data the tab shows:
//   Teaching Assignment → teaching_assignment_export.py
//   Weekly Schedule     → weekly_schedule_export.py (the week on screen)
const _TA_EXPORT_ROUTE = '/faculty/teaching-assignment/export';
const _WS_EXPORT_ROUTE = '/faculty/weekly-schedule/export';
let _exportKind = 'ta';   // 'ta' | 'weekly' — which export the open dialog runs

function exportActiveTabPdf() {
    if (_activeTab === 'weekly') openWeeklyExportModal(); else openTaExportModal();
}

function _taExportLoad() { return _scheduleMode === 'local' ? _myLoadLocal : _myLoad; }

function _resetExportFormats() {
    document.querySelectorAll('#taExportModal .emp-export-format-card').forEach(c => c.classList.remove('selected'));
    document.getElementById('taExpFmtError').style.display = 'none';
    _updateTaFmtButtons();
}

function openTaExportModal() {
    const load = _taExportLoad();
    if (!load) {
        _showTaExportToast('error', 'Nothing to Export', 'Your teaching assignment has not loaded yet. Please try again.');
        return;
    }
    _exportKind = 'ta';
    _resetExportFormats();
    document.getElementById('taExpTitle').textContent = 'Teaching Assignment Export';
    document.getElementById('taExpHeaderIcon').className = 'fas fa-chalkboard-teacher';
    document.getElementById('taExpCountLabel').textContent = 'Assignments to Export';
    const n = load.regular.length + load.partTime.length;
    document.getElementById('taExpCount').textContent = n;
    document.getElementById('taExpScopeLabel').textContent =
        `${_scheduleMode === 'local' ? 'Local' : 'Official'} Schedule · ${load.sem_label}, AY ${load.ay_label}`;
    document.getElementById('taExpFilenameInput').value = load.export_filename || 'Teaching_Assignment';
    document.getElementById('taExportModal').style.display = 'flex';
}

// Classes shown for the displayed week (same rules as renderCalendar).
function _weeklyExportCount() {
    const dates = _weekDates();
    const inSem = dates.map(_inSemester);
    const source = _scheduleMode === 'local' ? _localSessions : _sessions;
    const recurring = source.filter(s => (!_programFilter || (s.programcode || '') === _programFilter)
        && inSem[DAYS.indexOf(s.daydesc)]).length;
    const mk = _scheduleMode === 'official' && _makeupWeekKey === _iso(dates[0])
        ? _makeups.filter(m => !_programFilter || (m.programcode || '') === _programFilter).length : 0;
    return recurring + mk;
}

function _cleanName(s) {   // mirrors teaching_assignment_export.clean_filename
    return String(s || '').trim().replace(/\s+/g, '_').replace(/[^A-Za-z0-9._-]/g, '')
        .replace(/^[._]+|[._]+$/g, '').slice(0, 150);
}

function openWeeklyExportModal() {
    const dates = _weekDates();
    _exportKind = 'weekly';
    _resetExportFormats();
    document.getElementById('taExpTitle').textContent = 'Weekly Schedule Export';
    document.getElementById('taExpHeaderIcon').className = 'fas fa-calendar-week';
    document.getElementById('taExpCountLabel').textContent = 'Classes This Week';
    document.getElementById('taExpCount').textContent = _weeklyExportCount();
    document.getElementById('taExpScopeLabel').textContent =
        `${_weekLabel(dates)} · ${_scheduleMode === 'local' ? 'Local' : 'Official'} Schedule`
        + (_programFilter ? ` · ${_programFilter}` : '');
    const load = _taExportLoad();   // same "SURNAME, Given" form the server's default name uses
    const name = (load && load.fullname) || (_facMe && _facMe.fullname) || EMP_NUM;
    document.getElementById('taExpFilenameInput').value =
        _cleanName(`Weekly_Schedule_${name}_${_iso(dates[0])}_to_${_iso(dates[6])}`) || 'Weekly_Schedule';
    document.getElementById('taExportModal').style.display = 'flex';
}

function closeTaExportModal() {
    document.getElementById('taExportModal').style.display = 'none';
}

// One, several or all formats (same multi-select as the Reports export dialog):
// one format downloads that file; several download one .zip with each file.
function _selectedTaFmts() {
    return Array.from(document.querySelectorAll('#taExportModal .emp-export-format-card.selected'))
        .map(c => c.dataset.format);
}
function _updateTaFmtButtons() {
    const all = document.querySelectorAll('#taExportModal .emp-export-format-card');
    const n   = _selectedTaFmts().length;
    document.getElementById('taExpFmtAllBtn').textContent = n === all.length ? 'Deselect All' : 'Select All';
    document.getElementById('taExpConfirmBtn').disabled = n === 0;
    document.getElementById('taExpConfirmBtn').innerHTML =
        `<i class="fas fa-download"></i> Export${n > 1 ? ` ${n} Files (.zip)` : ''}`;
}
function _selectTaFmt(el) {
    el.classList.toggle('selected');
    document.getElementById('taExpFmtError').style.display = 'none';
    _updateTaFmtButtons();
}
function _toggleTaAllFmts() {
    const cards  = document.querySelectorAll('#taExportModal .emp-export-format-card');
    const allSel = Array.from(cards).every(c => c.classList.contains('selected'));
    cards.forEach(c => c.classList.toggle('selected', !allSel));
    document.getElementById('taExpFmtError').style.display = 'none';
    _updateTaFmtButtons();
}

function _showTaExportLoading(text, sub) {
    document.getElementById('taExportLoadingText').textContent = text || 'Exporting...';
    document.getElementById('taExportLoadingSubText').textContent = sub || 'Please wait';
    document.getElementById('taExportLoadingOverlay').style.display = 'flex';
}
function _hideTaExportLoading() { document.getElementById('taExportLoadingOverlay').style.display = 'none'; }
function _showTaExportToast(type, title, msg) {
    const toast = document.getElementById('taExportToast');
    toast.className = `emp-export-toast ${type}`;
    document.getElementById('taExportToastIcon').innerHTML = type === 'success'
        ? '<i class="fas fa-check-circle"></i>' : '<i class="fas fa-times-circle"></i>';
    document.getElementById('taExportToastTitle').textContent = title;
    document.getElementById('taExportToastMsg').textContent   = msg;
    toast.style.display = 'flex';
    setTimeout(() => { toast.style.display = 'none'; }, 5000);
}

async function executeTaExport() {
    const formats = _selectedTaFmts();
    if (!formats.length) { document.getElementById('taExpFmtError').style.display = 'block'; return; }
    const ext  = formats.length > 1 ? 'zip' : formats[0];
    const weekly = _exportKind === 'weekly';
    const load = _taExportLoad();
    const fallback = weekly ? 'Weekly_Schedule' : ((load && load.export_filename) || 'Teaching_Assignment');
    const filename = (document.getElementById('taExpFilenameInput').value || fallback)
        .trim().replace(/[\/\\:*?"<>|]/g, '_');
    const btn = document.getElementById('taExpConfirmBtn');
    btn.disabled = true;
    closeTaExportModal();
    _showTaExportLoading('Generating Export', weekly ? 'Building your weekly schedule...' : 'Building your teaching assignment...');
    const body = { formats, source: _scheduleMode, ay_id: AY_ID, semester: SEM, filename };
    if (weekly) Object.assign(body, { program: _programFilter, week_start: _iso(_weekDates()[0]) });
    try {
        const resp = await fetch(weekly ? _WS_EXPORT_ROUTE : _TA_EXPORT_ROUTE, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.error || resp.statusText);
        }
        const blob = await resp.blob();
        // Use the server's final (sanitized) name so the saved file matches it exactly.
        const cd = resp.headers.get('Content-Disposition') || '';
        const m  = cd.match(/filename="([^"]+)"/);
        const a  = Object.assign(document.createElement('a'),
            { href: URL.createObjectURL(blob), download: m ? m[1] : `${filename}.${ext}` });
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        _showTaExportToast('success', 'Export Complete', formats.length > 1
            ? `${formats.length} files (${formats.map(f => f.toUpperCase()).join(', ')}) downloaded as one .zip.`
            : (weekly ? 'Weekly schedule export downloaded.' : 'Teaching assignment export downloaded.'));
    } catch (e) {
        _showTaExportToast('error', 'Export Failed', e.message);
    } finally {
        _hideTaExportLoading();
        btn.disabled = false;
    }
}

window.addEventListener('DOMContentLoaded', loadData);
