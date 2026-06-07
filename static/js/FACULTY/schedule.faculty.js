/* schedule.faculty.js — Class Schedule (SIS) for Faculty view */

/* ── Config ── */
const FCS_GRID_START = 7 * 60 + 30;   // 7:30 AM in minutes
const FCS_GRID_END   = 18 * 60 + 30;   // Extended to 6:30 PM for mockup scope
const FCS_SLOT_H     = 60;             // px per hour (= 1px per minute)
const FCS_DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];

// Mockup Colors (Blue, Yellow, Red, Green etc.)
const FCS_COLORS = [
    '#5B8DF2', // Soft Blue
    '#F2D05B', // Soft Yellow
    '#F24C4C', // Soft Red
    '#4CBF8A', // Soft Green
    '#A661D9', // Soft Purple
    '#F28F5B', // Soft Orange
];
const FCS_SEM_LABELS = { A: '1ST SEMESTER', B: '2ND SEMESTER', C: 'SUMMER' };
const DAY_MAP = {
    MONDAY:'Monday',TUESDAY:'Tuesday',WEDNESDAY:'Wednesday',THURSDAY:'Thursday',
    FRIDAY:'Friday',SATURDAY:'Saturday',SUNDAY:'Sunday',
    MON:'Monday',TUE:'Tuesday',WED:'Wednesday',THU:'Thursday',
    FRI:'Friday',SAT:'Saturday',SUN:'Sunday'
};

/* ── State ── */
let _sessions   = [];
let _sortAsc    = true;
let _curView    = 'calendar';

/* ── Init data (from server-embedded JSON) ── */
const _initEl     = document.getElementById('fcs-init-data');
const _PROGRAMS   = JSON.parse(_initEl.dataset.programs   || '[]');
const _ACAD_YEARS = JSON.parse(_initEl.dataset.acadYears  || '[]');
const _FACULTY    = JSON.parse(_initEl.dataset.faculty    || '[]');
const _AY_SEM_MAP = {};  // ay_id -> sems[]

/* ── Bootstrap ── */
document.addEventListener('DOMContentLoaded', () => {
    _populateDropdowns();
    _buildEmptyGrid();
});

/* ═══════════════════════════════════════════
   DROPDOWN POPULATION
═══════════════════════════════════════════ */
function _populateDropdowns() {
    const aySel   = document.getElementById('fcsAy');
    const progSel = document.getElementById('fcsProg');
    const instrSel = document.getElementById('fcsInstr');

    _ACAD_YEARS.forEach(ay => {
        const opt = document.createElement('option');
        opt.value = ay.id;
        opt.textContent = ay.label;
        aySel.appendChild(opt);
        if (ay.sems) _AY_SEM_MAP[ay.id] = ay.sems;
    });

    _PROGRAMS.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.code;
        opt.textContent = p.name;
        progSel.appendChild(opt);
    });

    _FACULTY.forEach(f => {
        const opt = document.createElement('option');
        opt.value = f.emp;
        opt.textContent = f.name;
        instrSel.appendChild(opt);
    });
}

function fcsOnAyChange() {
    const ayId = document.getElementById('fcsAy').value;
    const sel  = document.getElementById('fcsSem');
    Array.from(sel.options).forEach(o => { if (o.value) o.remove(); });

    const sems = _AY_SEM_MAP[ayId] || [];
    sems.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.type;
        opt.textContent = FCS_SEM_LABELS[s.type] || s.type;
        sel.appendChild(opt);
    });
    _autoLoad();
}

function fcsOnProgChange() {
    document.getElementById('fcsYl').value  = '';
    document.getElementById('fcsSec').innerHTML = '<option value="">SELECT</option>';
    _autoLoad();
}

function fcsOnYlChange() {
    _populateSections();
    _autoLoad();
}

async function _populateSections() {
    const prog = document.getElementById('fcsProg').value;
    const yl   = document.getElementById('fcsYl').value;
    const sel  = document.getElementById('fcsSec');
    sel.innerHTML = '<option value="">SELECT</option>';
    if (!prog || !yl) return;

    try {
        const res  = await fetch(`/api/sections-by-program?program=${encodeURIComponent(prog)}&yearLevel=${yl}`);
        const data = await res.json();
        (data.sections || []).forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.id;
            opt.textContent = s.name;
            sel.appendChild(opt);
        });
    } catch (e) { /* ignore */ }
}

function _autoLoad() {
    const ay  = document.getElementById('fcsAy').value;
    const sem = document.getElementById('fcsSem').value;
    if (ay && sem) fcsLoadSchedule();
}

/* ═══════════════════════════════════════════
   LOAD SCHEDULE
═══════════════════════════════════════════ */
async function fcsLoadSchedule() {
    const ay     = document.getElementById('fcsAy').value;
    const sem    = document.getElementById('fcsSem').value;
    const prog   = document.getElementById('fcsProg').value;
    const yl     = document.getElementById('fcsYl').value;
    const sec    = document.getElementById('fcsSec').value;
    const instr  = document.getElementById('fcsInstr').value;

    if (!ay || !sem) { _showState('empty'); return; }

    _showState('loading');

    const params = new URLSearchParams({ ay_id: ay, semester: sem });
    if (prog)  params.set('program',    prog);
    if (yl)    params.set('year_level', yl);
    if (sec)   params.set('section_id', sec);
    if (instr) params.set('emp_num',    instr);

    try {
        const res  = await fetch('/api/faculty/schedule?' + params.toString());
        const data = await res.json();

        if (!data.success || !(data.sessions || []).length) {
            _sessions = [];
            _showState('empty');
            return;
        }

        _sessions = data.sessions;
        _showState('none');
        _updateContextLabel(ay, sem, prog, yl);
        _renderView();

        // Attach change listeners for live filter
        ['fcsSec','fcsInstr'].forEach(id => {
            const el = document.getElementById(id);
            // remove old listener to avoid stacking
            const newEl = el.cloneNode(true);
            el.parentNode.replaceChild(newEl, el);
            newEl.addEventListener('change', () => fcsLoadSchedule());
        });
    } catch (e) {
        console.error('[fcs] load error:', e);
        _sessions = [];
        _showState('empty');
    }
}

document.getElementById('fcsSem').addEventListener('change', fcsLoadSchedule);

/* ═══════════════════════════════════════════
   VIEW TOGGLE
═══════════════════════════════════════════ */
function fcsSetView(type) {
    _curView = type;
    document.getElementById('btnFcsCalendar').classList.toggle('active', type === 'calendar');
    document.getElementById('btnFcsTable').classList.toggle('active', type === 'table');
    _renderView();
}

function _renderView() {
    if (_curView === 'calendar') {
        document.getElementById('fcsCalendarPanel').style.display = 'block';
        document.getElementById('fcsTablePanel').style.display    = 'none';
        _renderCalendar();
    } else {
        document.getElementById('fcsCalendarPanel').style.display = 'none';
        document.getElementById('fcsTablePanel').style.display    = 'block';
        _renderTable();
    }
}

/* ═══════════════════════════════════════════
   CALENDAR VIEW
═══════════════════════════════════════════ */
function _buildEmptyGrid() {
    const grid = document.getElementById('fcsCalGrid');
    grid.innerHTML = '';

    const slots = [];
    for (let m = FRS_GRID_START; m <= FCS_GRID_END; m += 60) {
        slots.push(m);
    }

    slots.forEach(m => {
        // Time label (No AM/PM to match mockup)
        const lbl = document.createElement('div');
        lbl.className = 'fcs-time-label';
        
        const h = Math.floor(m / 60);
        const min = m % 60;
        const h12  = h === 0 ? 12 : h > 12 ? h - 12 : h;
        lbl.textContent = `${h12}:${String(min).padStart(2,'0')}`;
        
        grid.appendChild(lbl);

        // Day columns
        FCS_DAYS.forEach(day => {
            const cell = document.createElement('div');
            cell.className = 'fcs-day-col';
            cell.dataset.day = day;
            cell.dataset.slot = m;

            const line = document.createElement('div');
            line.className = 'fcs-slot-line';
            cell.appendChild(line);
            grid.appendChild(cell);
        });
    });
}

function _renderCalendar() {
    document.querySelectorAll('.fcs-pill').forEach(p => p.remove());

    const sorted = _sortedSessions();
    const byDay = {};
    FCS_DAYS.forEach(d => byDay[d] = []);

    sorted.forEach(s => {
        const day = _normalizeDay(s.daydesc);
        if (day && byDay[day] !== undefined) byDay[day].push(s);
    });

    FCS_DAYS.forEach(day => {
        const cells = document.querySelectorAll(`.fcs-day-col[data-day="${day}"]`);
        if (!cells.length) return;

        byDay[day].forEach(s => {
            const startM = _parseMins(s.start_time);
            const endM   = _parseMins(s.end_time);
            if (!startM || !endM) return;

            const topPx    = (startM - FCS_GRID_START) * (FCS_SLOT_H / 60);
            const heightPx = (endM - startM) * (FCS_SLOT_H / 60);
            if (heightPx <= 0) return;

            const slotM    = Math.floor((startM - FCS_GRID_START) / 60) * 60 + FCS_GRID_START;
            const targetCell = document.querySelector(`.fcs-day-col[data-day="${day}"][data-slot="${slotM}"]`);
            if (!targetCell) return;

            const offsetInSlot = (startM - slotM) * (FCS_SLOT_H / 60);

            // Assign color based on string hash for consistency
            const bgColor = _subjectColor(s.subjectcode);
            // Text color is white, except for Yellow background which needs dark text
            const isYellow = bgColor === '#F2D05B';
            const txtColor = isYellow ? '#333' : '#fff';

            const pill = document.createElement('div');
            pill.className  = 'fcs-pill';
            pill.style.top  = offsetInSlot + 'px';
            pill.style.height = heightPx + 'px';
            pill.style.background = bgColor;
            pill.style.color = txtColor;
            pill.innerHTML = `
                <span class="fcs-pill-code" style="color:${txtColor}">${_esc(s.subjectcode || '')}</span>
                <span class="fcs-pill-name" style="color:${txtColor}">${_esc(s.subjectname || '')}</span>
                <div class="fcs-pill-meta"><i class="fas fa-user-friends"></i> ${_esc((s.instructor || 'TBA').split(',')[0])}</div>
                <div class="fcs-pill-meta"><i class="fas fa-door-open"></i> ${_esc(s.roomname || 'TBA')}</div>
            `;
            targetCell.appendChild(pill);
        });
    });
}

/* ═══════════════════════════════════════════
   TABLE VIEW
═══════════════════════════════════════════ */
function _renderTable() {
    const tbody  = document.getElementById('fcsTbody');
    const sorted = _sortedSessions();

    if (!sorted.length) {
        tbody.innerHTML = `<tr><td colspan="11" style="text-align:center;padding:40px;color:#aaa;">No sessions to display.</td></tr>`;
        return;
    }

    const merged = _mergeSessionRows(sorted);

    tbody.innerHTML = merged.map(s => `
        <tr>
            <td>${_esc(s.instructor || '—')}</td>
            <td class="td-code">${_esc(s.subjectcode || '—')}</td>
            <td>${_esc(s.subjectname || '—')}</td>
            <td class="td-num">${s.lecturehours ?? '—'}</td>
            <td class="td-num">${s.laboratoryhours ?? '—'}</td>
            <td class="td-units">${s.creditunits ?? '—'}</td>
            <td>${_esc(s.programcode || '—')} ${s.yearlevel || '1'}</td>
            <td style="white-space:nowrap; font-size:0.75rem;">${(_esc(s.time_range) || '—').replace(' - ', '<br> - ')}</td>
            <td class="td-num">${(s.lecturehours||0)+(s.laboratoryhours||0) || '—'}</td>
            <td class="td-days">${(_esc(s.days || s.daydesc) || '—').replace('/', '/<br>')}</td>
            <td style="font-weight:900;">${_esc(s.roomname || '—')}</td>
        </tr>
    `).join('');
}

function _mergeSessionRows(sessions) {
    const map = {};
    sessions.forEach(s => {
        const key = `${s.subjectcode}|${s.employeenumber}|${s.time_range}|${s.roomname}`;
        if (!map[key]) {
            map[key] = { ...s, days: _dayAbbr(s.daydesc) };
        } else {
            const abbr = _dayAbbr(s.daydesc);
            if (!map[key].days.includes(abbr)) map[key].days += '/' + abbr;
        }
    });
    return Object.values(map);
}

/* ═══════════════════════════════════════════
   SORT
═══════════════════════════════════════════ */
function fcsSortToggle() {
    _sortAsc = !_sortAsc;
    document.getElementById('fcsSortLabel').textContent = _sortAsc ? 'A-Z' : 'Z-A';
    document.getElementById('fcsSortIcon').className = _sortAsc ? 'fas fa-sort-amount-down' : 'fas fa-sort-amount-up';
    if (_sessions.length) _renderView();
}

function _sortedSessions() {
    return [..._sessions].sort((a, b) => {
        const cmp = (a.subjectcode || '').localeCompare(b.subjectcode || '');
        return _sortAsc ? cmp : -cmp;
    });
}

/* ═══════════════════════════════════════════
   EXPORT
═══════════════════════════════════════════ */
function fcsExport() {
    if (!_sessions.length) { alert('No schedule data to export.'); return; }

    const merged = _mergeSessionRows(_sessions);
    const header = ['Instructor','Subject Code','Subject Description','Lec Hours','Lab Hours','Credit Units','Course','Time','Hours','Days','Room'];
    const rows   = merged.map(s => [
        s.instructor || '',
        s.subjectcode || '',
        s.subjectname || '',
        s.lecturehours ?? '',
        s.laboratoryhours ?? '',
        s.creditunits ?? '',
        `${s.programcode || ''} ${s.yearlevel || ''}`.trim(),
        s.time_range || '',
        (s.lecturehours||0)+(s.laboratoryhours||0),
        s.days || s.daydesc || '',
        s.roomname || ''
    ]);

    const csv = [header, ...rows].map(r => r.map(v => `"${String(v).replace(/"/g,'""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = 'class_schedule.csv';
    a.click();
    URL.revokeObjectURL(url);
}

/* ═══════════════════════════════════════════
   CONTEXT LABEL
═══════════════════════════════════════════ */
function _updateContextLabel(ayId, sem, prog, yl) {
    const bar     = document.getElementById('fcsContextBar');
    const ayObj   = _ACAD_YEARS.find(a => String(a.id) === String(ayId));
    const progObj = _PROGRAMS.find(p => p.code === prog);
    
    let label = prog ? `${progObj ? progObj.name.toUpperCase() : prog.toUpperCase()} (${prog})` : 'ALL PROGRAMS';
    if (yl) label += ` - ${yl}`; // Match mockup "BSIT - 1"

    document.getElementById('fcsContextProg').textContent = label;
    document.getElementById('fcsContextAy').textContent   = ayObj ? ayObj.label.toUpperCase() : '';
    document.getElementById('fcsContextSem').textContent  = FCS_SEM_LABELS[sem] || sem;
    bar.style.display = 'flex';
}

/* ═══════════════════════════════════════════
   STATE HELPERS
═══════════════════════════════════════════ */
function _showState(state) {
    document.getElementById('fcsLoading').style.display      = state === 'loading' ? 'block' : 'none';
    document.getElementById('fcsEmpty').style.display        = state === 'empty'   ? 'block' : 'none';
    document.getElementById('fcsCalendarPanel').style.display = (state === 'none' && _curView === 'calendar') ? 'block' : 'none';
    document.getElementById('fcsTablePanel').style.display    = (state === 'none' && _curView === 'table')    ? 'block' : 'none';
    if (state !== 'none') document.getElementById('fcsContextBar').style.display = 'none';
}

/* ═══════════════════════════════════════════
   HELPERS
═══════════════════════════════════════════ */
function _parseMins(timeStr) {
    if (!timeStr) return null;
    const m = String(timeStr).trim().match(/^(\d{1,2}):(\d{2})\s*(AM|PM)?$/i);
    if (!m) return null;
    let h = parseInt(m[1]), mn = parseInt(m[2]);
    const ampm = (m[3] || '').toUpperCase();
    if (ampm === 'PM' && h !== 12) h += 12;
    if (ampm === 'AM' && h === 12) h = 0;
    return h * 60 + mn;
}

function _normalizeDay(s) {
    if (!s) return null;
    return DAY_MAP[s.toUpperCase()] || null;
}

function _dayAbbr(s) {
    if (!s) return '';
    const abbrs = { Monday:'MON', Tuesday:'TUE', Wednesday:'WED', Thursday:'THU', Friday:'FRI', Saturday:'SAT', Sunday:'SUN' };
    return abbrs[s] || s.slice(0,3).toUpperCase();
}

function _subjectColor(code) {
    let h = 0;
    for (let i = 0; i < (code||'').length; i++) h = (code||'').charCodeAt(i) + ((h << 5) - h);
    return FCS_COLORS[Math.abs(h) % FCS_COLORS.length];
}

function _esc(str) {
    return String(str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}