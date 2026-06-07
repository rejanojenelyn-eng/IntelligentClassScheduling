/* room_schedule.faculty.js — Room Schedule page for Faculty */

/* ── Config ── */
const FRS_GRID_START = 7 * 60 + 30;  // 7:30 AM
const FRS_GRID_END   = 18 * 60 + 30; // 6:30 PM (mockup limit)
const FRS_SLOT_H     = 60;           // px per hour (2x 30px cells)
const FRS_DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];

// Pastel colors to match the mockup exactly
const FRS_COLORS = [
    '#A9C2F0', // Soft Blue
    '#FDE08B', // Soft Yellow
    '#F9A17A', // Soft Orange
    '#F07C7C', // Soft Red
    '#B5E5CF', // Soft Green
    '#D2A6E8'  // Soft Purple
];

const FRS_DAY_MAP = {
    MONDAY:'Monday',TUESDAY:'Tuesday',WEDNESDAY:'Wednesday',THURSDAY:'Thursday',
    FRIDAY:'Friday',SATURDAY:'Saturday',SUNDAY:'Sunday',
    MON:'Monday',TUE:'Tuesday',WED:'Wednesday',THU:'Thursday',
    FRI:'Friday',SAT:'Saturday',SUN:'Sunday'
};

/* ── State ── */
let _buildings = [];
let _rooms     = [];
let _activeBldg = null;
let _activeRoom = null;
let _floorFilter = '';

/* ── Init ── */
const _initEl = document.getElementById('frs-init-data');
_buildings    = JSON.parse(_initEl.dataset.buildings || '[]');
_rooms        = JSON.parse(_initEl.dataset.rooms     || '[]');

document.addEventListener('DOMContentLoaded', () => {
    _buildBldgTabs();
    _buildAvailBuildingFilter();
    _buildGrid();
});

/* ═══════════════════════════════════════════
   MAIN TAB SWITCH
═══════════════════════════════════════════ */
function frsSetMainTab(tab) {
    const isSchedule = tab === 'schedule';
    document.getElementById('btnFrsSchedView').classList.toggle('active', isSchedule);
    document.getElementById('btnFrsAvailRooms').classList.toggle('active', !isSchedule);
    document.getElementById('frsScheduleViewPanel').style.display = isSchedule ? 'block' : 'none';
    document.getElementById('frsAvailPanel').style.display        = isSchedule ? 'none'  : 'block';
    
    if(!isSchedule) {
        frsFindAvailable(); // auto load if switching
    }
}

/* ═══════════════════════════════════════════
   BUILDING TABS
═══════════════════════════════════════════ */
function _buildBldgTabs() {
    const container = document.getElementById('frsBldgTabs');
    container.innerHTML = '';

    if (!_buildings.length) {
        container.innerHTML = '<span style="padding:10px;color:#aaa;font-size:0.75rem;">No buildings found.</span>';
        return;
    }

    _buildings.forEach((b, i) => {
        const btn = document.createElement('button');
        btn.className   = 'frs-bldg-tab' + (i === 0 ? ' active' : '');
        btn.textContent = b.name.toUpperCase();
        btn.dataset.bid = b.id;
        btn.onclick     = () => frsSelectBuilding(b.id);
        container.appendChild(btn);
    });

    if (_buildings.length) frsSelectBuilding(_buildings[0].id);
}

function frsSelectBuilding(bid) {
    _activeBldg = bid;
    _activeRoom = null;

    document.querySelectorAll('.frs-bldg-tab').forEach(t =>
        t.classList.toggle('active', parseInt(t.dataset.bid) === bid)
    );

    document.getElementById('frsFloorSel').value = '';
    _floorFilter = '';

    _renderRoomList();
    _clearGrid(false);
}

/* ═══════════════════════════════════════════
   ROOM LIST (left sidebar)
═══════════════════════════════════════════ */
function frsFilterFloor() {
    _floorFilter = document.getElementById('frsFloorSel').value;
    _renderRoomList();
}

function _renderRoomList() {
    const list = document.getElementById('frsRoomList');
    list.innerHTML = '';

    if (_activeBldg === null) {
        list.innerHTML = '<div class="frs-room-empty">Select a building</div>';
        return;
    }

    let filtered = _rooms.filter(r => r.bid === _activeBldg);

    if (_floorFilter) {
        const floor = parseInt(_floorFilter);
        filtered = filtered.filter(r => {
            const num = parseInt((r.name.match(/\d+/) || [])[0] || '0');
            return Math.floor(num / 100) === floor;
        });
    }

    if (!filtered.length) {
        list.innerHTML = '<div class="frs-room-empty">No rooms found.</div>';
        return;
    }

    filtered.forEach((r, idx) => {
        const item = document.createElement('div');
        item.className   = 'frs-room-item';
        item.textContent = r.name;
        item.dataset.rid = r.id;
        item.onclick     = () => frsSelectRoom(r.id, r.name, r.bid);
        list.appendChild(item);
        
        // Auto select first room to match mockup view
        if(idx === 0 && !_activeRoom) {
            frsSelectRoom(r.id, r.name, r.bid);
        }
    });
}

/* ═══════════════════════════════════════════
   ROOM SELECTION & CALENDAR
═══════════════════════════════════════════ */
async function frsSelectRoom(rid, rname, bid) {
    _activeRoom = rid;

    document.querySelectorAll('.frs-room-item').forEach(el =>
        el.classList.toggle('active', parseInt(el.dataset.rid) === rid)
    );

    const bldgObj = _buildings.find(b => b.id === bid);
    const bldgName = bldgObj ? bldgObj.name.toUpperCase() : '';
    document.getElementById('frsCalTitle').textContent =
        `BUILDING: ${bldgName} – ROOM ${rname}`;

    _clearGrid(true);

    try {
        const res  = await fetch(`/api/get_room_schedule/${rid}`);
        const data = await res.json();
        _renderRoomCalendar(Array.isArray(data) ? data : []);
    } catch (e) {
        console.error('[frs] room schedule error:', e);
        _renderRoomCalendar([]);
    }
}

/* ═══════════════════════════════════════════
   GRID BUILD + RENDER (30 min increments)
═══════════════════════════════════════════ */
function _buildGrid() {
    const grid = document.getElementById('frsGrid');
    grid.innerHTML = '';

    // Header row
    ['TIME', 'MONDAY','TUESDAY','WEDNESDAY','THURSDAY','FRIDAY','SATURDAY','SUNDAY'].forEach(d => {
        const h = document.createElement('div');
        h.className   = 'frs-grid-header-cell';
        h.textContent = d;
        grid.appendChild(h);
    });

    // 30 min slots
    for (let m = FRS_GRID_START; m <= FRS_GRID_END; m += 30) {
        // Time label (e.g. 7:30 AM)
        const lbl = document.createElement('div');
        lbl.className   = 'frs-time-cell';
        lbl.textContent = _minsToLabel(m);
        grid.appendChild(lbl);

        // Day cells
        FRS_DAYS.forEach(day => {
            const cell = document.createElement('div');
            cell.className   = 'frs-day-col-cell';
            cell.dataset.day = day;
            cell.dataset.slot = m;
            grid.appendChild(cell);
        });
    }
}

function _clearGrid(showLoading) {
    document.querySelectorAll('.frs-pill').forEach(p => p.remove());
}

function _renderRoomCalendar(sessions) {
    _clearGrid(false);

    sessions.forEach(s => {
        const day     = _normalizeDay(s.daydesc);
        if (!day) return;

        const startM  = _timeidToMins(s.starttimeid);
        const endM    = _timeidToMins(s.endtimeid);
        if (!startM || !endM) return;

        // Find the nearest 30-min block cell
        const slotM   = Math.floor((startM - FRS_GRID_START) / 30) * 30 + FRS_GRID_START;
        const cell    = document.querySelector(`.frs-day-col-cell[data-day="${day}"][data-slot="${slotM}"]`);
        if (!cell) return;

        const offsetPx = (startM - slotM) * (FRS_SLOT_H / 60);
        const heightPx = (endM - startM) * (FRS_SLOT_H / 60);
        if (heightPx <= 0) return;

        const pill = document.createElement('div');
        pill.className   = 'frs-pill';
        pill.style.top   = offsetPx + 'px';
        pill.style.height = heightPx + 'px';
        pill.style.background = _subjectColor(s.subjectcode || '');
        
        // Mockup text is dark (#333) for all pills
        pill.innerHTML = `
            <span class="frs-pill-code">${_esc(s.subjectcode || '')}</span>
            <span class="frs-pill-name">${_esc(s.subjectname || '')}</span>
            <div class="frs-pill-meta"><i class="fas fa-user-friends"></i> ${_esc((s.instructor || 'TBA').split(',')[0])}</div>
            <div class="frs-pill-meta"><i class="fas fa-door-open"></i> ${_esc(s.roomname || 'TBA')}</div>
        `;
        cell.appendChild(pill);
    });
}

function _timeidToMins(timeid) {
    if (!timeid) return null;
    return 450 + (parseInt(timeid) - 1) * 30;
}

/* ═══════════════════════════════════════════
   AVAILABLE ROOMS
═══════════════════════════════════════════ */
function _buildAvailBuildingFilter() {
    const bSel = document.getElementById('frsAvailBuilding');
    _buildings.forEach(b => {
        const opt = document.createElement('option');
        opt.value = b.id;
        opt.textContent = b.name;
        bSel.appendChild(opt);
    });
    
    // Setup Room Number filter
    const rSel = document.getElementById('frsAvailRoomNum');
    _rooms.forEach(r => {
        const opt = document.createElement('option');
        opt.value = r.name;
        opt.textContent = r.name;
        rSel.appendChild(opt);
    });
}

async function frsFindAvailable() {
    const day   = document.getElementById('frsAvailDay').value;
    const start = document.getElementById('frsAvailStart').value;
    const end   = document.getElementById('frsAvailEnd').value;
    const type  = document.getElementById('frsAvailType').value;
    const bid   = document.getElementById('frsAvailBuilding').value;
    const rnum  = document.getElementById('frsAvailRoomNum').value;

    const content = document.getElementById('frsAvailContent');
    content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-spinner fa-spin"></i>Searching…</div>`;

    const bldgName = bid ? (_buildings.find(b => String(b.id) === String(bid))?.name || 'Selected Building') : 'ALL BUILDINGS';
    document.getElementById('frsAvailHeader').textContent =
        `HERE ARE THE AVAILABLE ROOMS FOR (${bldgName.toUpperCase()})`;

    const params = new URLSearchParams();
    if (day)   params.set('day',         day);
    if (start) params.set('start_time',  start);
    if (end)   params.set('end_time',    end);
    if (type)  params.set('room_type',   type);
    if (bid)   params.set('building_id', bid);

    try {
        const res  = await fetch('/api/faculty/available_rooms?' + params.toString());
        const data = await res.json();
        if (!data.success) {
            content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-exclamation-circle"></i>Error fetching rooms.</div>`;
            return;
        }
        
        let rooms = data.rooms || [];
        if (rnum) {
            rooms = rooms.filter(r => r.name.toLowerCase().includes(rnum.toLowerCase()));
        }
        
        _renderAvailRooms(rooms, start, end);
    } catch (e) {
        content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-exclamation-circle"></i>Network error.</div>`;
    }
}

function _renderAvailRooms(rooms, start, end) {
    const content = document.getElementById('frsAvailContent');

    if (!rooms.length) {
        content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-door-closed"></i>No available rooms found for the selected criteria.</div>`;
        return;
    }

    const byBldg = {};
    rooms.forEach(r => {
        const key = r.buildingname || 'Other';
        if (!byBldg[key]) byBldg[key] = [];
        byBldg[key].push(r);
    });

    const timeLabel = (start && end) ? `${_fmt12h(start)} - ${_fmt12h(end)}` : '';
    let html = '';

    Object.entries(byBldg).forEach(([bname, bRooms]) => {
        html += `<div class="frs-avail-building-title">${_esc(bname)}</div>`;
        html += `<div class="frs-avail-cards">`;
        bRooms.forEach(r => {
            const isLab = (r.roomtype || '').toLowerCase() === 'laboratory';
            const badge = isLab
                ? '<span class="frs-avail-badge frs-badge-lab">LAB</span>'
                : '<span class="frs-avail-badge frs-badge-lec">LECTURE</span>';
            html += `
            <div class="frs-avail-card">
                <div class="frs-avail-dot"></div>
                <div class="frs-avail-room-name">ROOM ${_esc(r.roomname)}</div>
                ${badge}
                ${timeLabel ? `<div class="frs-avail-time">${_esc(timeLabel)}</div>` : ''}
            </div>`;
        });
        html += `</div>`;
    });

    content.innerHTML = html;
}

/* ═══════════════════════════════════════════
   HELPERS
═══════════════════════════════════════════ */
function _normalizeDay(s) {
    if (!s) return null;
    return FRS_DAY_MAP[s.toUpperCase()] || null;
}

function _minsToLabel(m) {
    const h   = Math.floor(m / 60);
    const min = m % 60;
    const ampm = h < 12 ? 'AM' : 'PM';
    const h12  = h === 0 ? 12 : h > 12 ? h - 12 : h;
    return `${h12}:${String(min).padStart(2,'0')} ${ampm}`;
}

function _fmt12h(timeStr) {
    if (!timeStr) return '';
    const [h, m] = timeStr.split(':').map(Number);
    const ampm = h < 12 ? 'AM' : 'PM';
    const h12  = h === 0 ? 12 : h > 12 ? h - 12 : h;
    return `${h12}:${String(m).padStart(2,'0')} ${ampm}`;
}

function _subjectColor(code) {
    let h = 0;
    for (let i = 0; i < (code||'').length; i++) h = (code||'').charCodeAt(i) + ((h << 5) - h);
    return FRS_COLORS[Math.abs(h) % FRS_COLORS.length];
}

function _esc(str) {
    return String(str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}