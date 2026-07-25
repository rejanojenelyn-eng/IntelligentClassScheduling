/* room_schedule.acad.js — Room Schedule page for Academic Head */

const FRS_GRID_START = 7 * 60 + 30;  // 7:30 AM
const FRS_GRID_END   = 21 * 60;      // 9:00 PM
const FRS_SLOT_H     = 30;
const FRS_DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];

const FRS_COLORS = [
    '#A9C2F0', '#FDE08B', '#F9A17A',
    '#F07C7C', '#B5E5CF', '#D2A6E8'
];

const FRS_DAY_MAP = {
    MONDAY:'Monday', TUESDAY:'Tuesday', WEDNESDAY:'Wednesday', THURSDAY:'Thursday',
    FRIDAY:'Friday', SATURDAY:'Saturday', SUNDAY:'Sunday',
    MON:'Monday', TUE:'Tuesday', WED:'Wednesday', THU:'Thursday',
    FRI:'Friday', SAT:'Saturday', SUN:'Sunday'
};

let _buildings  = [];
let _rooms      = [];
let _activeBldg = null;
let _activeRoom = null;
let _floorFilter = '';

// Reused (read-only) on the Reports module's Room Schedule preview, which has
// no #frs-init-data element -- guard so it's inert there instead of throwing.
const _initEl = document.getElementById('frs-init-data');
if (_initEl) {
    _buildings = JSON.parse(_initEl.dataset.buildings || '[]');
    _rooms     = JSON.parse(_initEl.dataset.rooms     || '[]');
}

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('frs-init-data')) return;
    _buildBldgTabs();
    _buildAvailBuildingFilter();
    _buildGrid();
});

/* ═══════════════════════════════════════════
   MAIN TAB SWITCH
═══════════════════════════════════════════ */
function frsSetMainTab(tab) {
    const isSchedule = tab === 'schedule';
    document.getElementById('btnFrsSchedView').classList.toggle('active',  isSchedule);
    document.getElementById('btnFrsAvailRooms').classList.toggle('active', !isSchedule);
    document.getElementById('frsScheduleViewPanel').style.display = isSchedule ? 'block' : 'none';
    document.getElementById('frsAvailPanel').style.display        = isSchedule ? 'none'  : 'block';
    if (!isSchedule) frsFindAvailable();
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
        t.classList.toggle('active', String(t.dataset.bid) === String(bid))
    );
    _buildFloorDropdown();
    _floorFilter = '';
    _renderRoomList();
    _clearGrid();
}

/* ═══════════════════════════════════════════
   FLOOR DROPDOWN
═══════════════════════════════════════════ */
const _FLOOR_LABELS = { '1': '1ST FLOOR', '2': '2ND FLOOR', '3': '3RD FLOOR', 'other': 'OTHER' };

function _buildFloorDropdown() {
    const sel = document.getElementById('frsFloorSel');
    sel.innerHTML = '<option value="">ALL FLOORS</option>';
    const bldgRooms = _rooms.filter(r => String(r.bid) === String(_activeBldg));
    const floors    = [...new Set(bldgRooms.map(r => r.floor || 'other'))].sort();
    floors.forEach(f => {
        const opt = document.createElement('option');
        opt.value = f;
        opt.textContent = _FLOOR_LABELS[f] || f.toUpperCase();
        sel.appendChild(opt);
    });
    if (floors.length === 1) {
        sel.value = floors[0];
        _floorFilter = floors[0];
    }
}

function frsFilterFloor() {
    _floorFilter = document.getElementById('frsFloorSel').value;
    _activeRoom  = null;
    _renderRoomList();
    _clearGrid();
}

/* ═══════════════════════════════════════════
   ROOM LIST
═══════════════════════════════════════════ */
function _renderRoomList() {
    const list = document.getElementById('frsRoomList');
    list.innerHTML = '';
    if (_activeBldg === null) {
        list.innerHTML = '<div class="frs-room-empty">Select a building</div>';
        return;
    }
    let filtered = _rooms.filter(r => String(r.bid) === String(_activeBldg));
    if (_floorFilter) {
        filtered = filtered.filter(r => (r.floor || 'other') === _floorFilter);
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
        if (idx === 0) frsSelectRoom(r.id, r.name, r.bid);
    });
}

/* ═══════════════════════════════════════════
   ROOM SELECTION & CALENDAR
═══════════════════════════════════════════ */
async function frsSelectRoom(rid, rname, bid) {
    _activeRoom = rid;
    document.querySelectorAll('.frs-room-item').forEach(el =>
        el.classList.toggle('active', String(el.dataset.rid) === String(rid))
    );
    const bldgObj  = _buildings.find(b => String(b.id) === String(bid));
    const bldgName = bldgObj ? bldgObj.name.toUpperCase() : '';
    document.getElementById('frsCalTitle').textContent = `BUILDING: ${bldgName} – ROOM ${rname}`;
    _clearGrid();
    try {
        const res  = await fetch(`/api/get_room_schedule/${rid}?scheduler_mode=local`);
        const data = await res.json();
        _renderRoomCalendar(Array.isArray(data) ? data : []);
    } catch (e) {
        console.error('[ars] room schedule error:', e);
    }
}

/* ═══════════════════════════════════════════
   GRID BUILD + RENDER
═══════════════════════════════════════════ */
function _buildGrid(targetId = 'frsGrid') {
    const grid = document.getElementById(targetId);
    grid.innerHTML = '';
    ['TIME', 'MONDAY','TUESDAY','WEDNESDAY','THURSDAY','FRIDAY','SATURDAY','SUNDAY'].forEach(d => {
        const h = document.createElement('div');
        h.className   = 'frs-grid-header-cell';
        h.textContent = d;
        grid.appendChild(h);
    });
    for (let m = FRS_GRID_START; m <= FRS_GRID_END; m += 30) {
        const lbl = document.createElement('div');
        lbl.className   = 'frs-time-cell';
        lbl.textContent = _minsToLabel(m);
        grid.appendChild(lbl);
        FRS_DAYS.forEach(day => {
            const cell = document.createElement('div');
            cell.className    = 'frs-day-col-cell';
            cell.dataset.day  = day;
            cell.dataset.slot = m;
            grid.appendChild(cell);
        });
    }
}

function _clearGrid(targetId = 'frsGrid') {
    const grid = document.getElementById(targetId);
    if (!grid) return;
    grid.querySelectorAll('.frs-pill').forEach(p => p.remove());
}

function _renderRoomCalendar(sessions, targetId = 'frsGrid') {
    _clearGrid(targetId);
    const grid = document.getElementById(targetId);
    if (!grid) return;
    sessions.forEach(s => {
        const day = _normalizeDay(s.daydesc);
        if (!day) return;
        const startM = _timeidToMins(s.starttimeid);
        const endM   = _timeidToMins(s.endtimeid);
        if (!startM || !endM || endM <= startM) return;
        if (endM <= FRS_GRID_START || startM >= FRS_GRID_END) return;
        const visStart   = Math.max(startM, FRS_GRID_START);
        const visEnd     = Math.min(endM, FRS_GRID_END);
        const anchorSlot = Math.floor((visStart - FRS_GRID_START) / 30) * 30 + FRS_GRID_START;
        const cell = grid.querySelector(`.frs-day-col-cell[data-day="${day}"][data-slot="${anchorSlot}"]`);
        if (!cell) return;
        const offsetPx = (visStart - anchorSlot) * (FRS_SLOT_H / 30);
        const heightPx = Math.max((visEnd - visStart) * (FRS_SLOT_H / 30), 20);
        if (heightPx <= 0) return;
        const isLocal = (s.status || '').toLowerCase() === 'local';
        const pill = document.createElement('div');
        pill.className        = 'frs-pill' + (isLocal ? ' frs-pill-local' : '');
        pill.style.top        = offsetPx + 'px';
        pill.style.height     = heightPx + 'px';
        pill.style.background = _subjectColor(s.subjectcode || '');
        pill.style.cursor     = 'pointer';
        pill.title            = isLocal ? 'Local Arrangement — Click to view details' : 'Click to view details';
        pill.addEventListener('click', () => frsShowDetail(s));
        const instrName = (s.instructor || 'TBA').split(',')[0].trim();
        pill.innerHTML =
            (isLocal ? `<span class="frs-pill-la-badge">LA</span>` : '') +
            `<span class="frs-pill-code">${_esc(s.subjectcode || '')}</span>` +
            `<span class="frs-pill-name">${_esc(s.subjectname || '')}</span>` +
            `<div class="frs-pill-meta"><i class="fas fa-user"></i> ${_esc(instrName)}</div>` +
            `<div class="frs-pill-meta"><i class="fas fa-door-open"></i> ${_esc(s.roomname || 'TBA')}</div>`;
        cell.appendChild(pill);
    });
}

/* ─── Schedule Detail Modal ─── */
function frsShowDetail(s) {
    // Reused (read-only) on the Reports module's Room Schedule preview, which
    // has no detail-modal markup -- inert there instead of throwing on click.
    if (!document.getElementById('frsDetailOverlay')) return;
    const startLabel = _minsToLabel(_timeidToMins(s.starttimeid) || FRS_GRID_START);
    const endLabel   = _minsToLabel(_timeidToMins(s.endtimeid)   || FRS_GRID_START);
    const timeRange  = `${startLabel} – ${endLabel}`;
    const _st = (s.status || '').toLowerCase();
    const statusCls  = _st === 'published' ? 'frs-detail-badge-pub'
                     : _st === 'local'     ? 'frs-detail-badge-local'
                     : 'frs-detail-badge-draft';
    const statusTxt  = _st === 'local' ? 'Local Arrangement' : (s.status || 'Draft');
    const yrLabel    = s.year_level ? `${s.year_level}${_ordSuffix(s.year_level)} Year` : '—';

    document.getElementById('frsDetailSubjCode').textContent = s.subjectcode  || '—';
    document.getElementById('frsDetailSubjName').textContent = s.subjectname  || '—';
    document.getElementById('frsDetailInstr').textContent    = s.instructor   || 'TBA';
    document.getElementById('frsDetailDay').textContent      = s.daydesc      || '—';
    document.getElementById('frsDetailTime').textContent     = timeRange;
    document.getElementById('frsDetailRoom').textContent     = s.roomname     || '—';
    document.getElementById('frsDetailProg').textContent     = s.programcode  || '—';
    document.getElementById('frsDetailYr').textContent       = yrLabel;
    const badgeEl = document.getElementById('frsDetailStatus');
    badgeEl.textContent = statusTxt;
    badgeEl.className   = 'frs-detail-badge ' + statusCls;
    document.getElementById('frsDetailAccent').style.background = _subjectColor(s.subjectcode || '');
    document.getElementById('frsDetailOverlay').classList.add('open');
}

function frsCloseDetail() {
    document.getElementById('frsDetailOverlay').classList.remove('open');
}

function _ordSuffix(n) {
    const v = parseInt(n, 10);
    if (v === 1) return 'st';
    if (v === 2) return 'nd';
    if (v === 3) return 'rd';
    return 'th';
}

function _timeidToMins(timeid) {
    if (!timeid) return null;
    const idx = parseInt(timeid, 10);
    if (isNaN(idx) || idx < 1) return null;
    return FRS_GRID_START + (idx - 1) * 30;
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
    const rSel = document.getElementById('frsAvailRoomNum');
    _rooms.forEach(r => {
        const opt = document.createElement('option');
        opt.value = r.name;
        opt.textContent = r.name;
        rSel.appendChild(opt);
    });
}

function frsDateChanged() {
    const dateEl = document.getElementById('frsAvailDate');
    const dayEl  = document.getElementById('frsAvailDay');
    if (dateEl && dateEl.value) {
        const days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
        const d    = new Date(dateEl.value + 'T00:00:00');
        dayEl.value = days[d.getDay()];
    }
    frsFindAvailable();
}

async function frsFindAvailable() {
    const day   = document.getElementById('frsAvailDay').value;
    const start = document.getElementById('frsAvailStart').value;
    const end   = document.getElementById('frsAvailEnd').value;
    const type  = document.getElementById('frsAvailType').value;
    const bid   = document.getElementById('frsAvailBuilding').value;
    const rnum  = document.getElementById('frsAvailRoomNum').value;
    const selDate = document.getElementById('frsAvailDate')?.value || '';
    const hasTimeFilter = !!(day && start && end);

    const content = document.getElementById('frsAvailContent');
    content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-spinner fa-spin"></i> Loading rooms…</div>`;

    const bldgName = bid
        ? (_buildings.find(b => String(b.id) === String(bid))?.name || 'Selected Building')
        : 'ALL BUILDINGS';
    document.getElementById('frsAvailHeader').textContent =
        `HERE ARE THE AVAILABLE ROOMS FOR (${bldgName.toUpperCase()})`;

    const params = new URLSearchParams();
    if (day)     params.set('day',         day);
    if (start)   params.set('start_time',  start);
    if (end)     params.set('end_time',    end);
    if (type)    params.set('room_type',   type);
    if (bid)     params.set('building_id', bid);
    if (selDate) params.set('date',        selDate);

    try {
        const res  = await fetch('/api/faculty/available_rooms?' + params.toString());
        const data = await res.json();
        if (!data.success) {
            content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-exclamation-circle"></i> Error fetching rooms.</div>`;
            return;
        }
        let rooms = data.rooms || [];
        if (rnum) rooms = rooms.filter(r => (r.roomname || '').toLowerCase().includes(rnum.toLowerCase()));
        _renderAvailRooms(rooms, start, end, hasTimeFilter);
    } catch (e) {
        content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-exclamation-circle"></i> Network error.</div>`;
    }
}

function _renderAvailRooms(rooms, start, end, hasTimeFilter) {
    const content = document.getElementById('frsAvailContent');
    if (!rooms.length) {
        const msg = hasTimeFilter
            ? 'No available rooms found for the selected criteria.'
            : 'No rooms found.';
        content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-door-closed"></i> ${msg}</div>`;
        return;
    }
    const byBldg = {};
    rooms.forEach(r => {
        const key = r.buildingname || 'Other';
        if (!byBldg[key]) byBldg[key] = [];
        byBldg[key].push(r);
    });
    const timeLabel = hasTimeFilter ? `${_fmt12h(start)} – ${_fmt12h(end)}` : '';
    let html = '';
    Object.entries(byBldg).forEach(([bname, bRooms]) => {
        html += `<div class="frs-avail-building-title">${_esc(bname)}</div>`;
        html += `<div class="frs-avail-cards">`;
        bRooms.forEach(r => {
            const isLab = (r.roomtype || '').toLowerCase() === 'laboratory';
            const badge = isLab
                ? '<span class="frs-avail-badge frs-badge-lab">LAB</span>'
                : '<span class="frs-avail-badge frs-badge-lec">LECTURE</span>';
            const cap     = r.roomcapacity ? `<div class="frs-avail-detail"><i class="fas fa-users"></i> Capacity: ${_esc(String(r.roomcapacity))}</div>` : '';
            const timeLbl = timeLabel ? `<div class="frs-avail-time">${_esc(timeLabel)}</div>` : '';
            const statusChip = hasTimeFilter ? `<div class="frs-avail-status-chip">&#10003; Available</div>` : '';
            html += `
            <div class="frs-avail-card">
                ${hasTimeFilter ? '<div class="frs-avail-dot"></div>' : ''}
                <div class="frs-avail-room-name">ROOM ${_esc(r.roomname || '')}</div>
                ${badge}
                ${timeLbl}
                ${cap}
                ${statusChip}
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
    return FRS_DAY_MAP[(s || '').toUpperCase()] || null;
}

function _minsToLabel(m) {
    const h    = Math.floor(m / 60);
    const min  = m % 60;
    const ampm = h < 12 ? 'AM' : 'PM';
    const h12  = h === 0 ? 12 : h > 12 ? h - 12 : h;
    return `${h12}:${String(min).padStart(2, '0')} ${ampm}`;
}

function _fmt12h(timeStr) {
    if (!timeStr) return '';
    const parts = timeStr.split(':');
    const h = parseInt(parts[0], 10);
    const m = parseInt(parts[1] || '0', 10);
    const ampm = h < 12 ? 'AM' : 'PM';
    const h12  = h === 0 ? 12 : h > 12 ? h - 12 : h;
    return `${h12}:${String(m).padStart(2, '0')} ${ampm}`;
}

function _subjectColor(code) {
    let h = 0;
    for (let i = 0; i < (code || '').length; i++)
        h = (code || '').charCodeAt(i) + ((h << 5) - h);
    return FRS_COLORS[Math.abs(h) % FRS_COLORS.length];
}

function _esc(str) {
    return String(str || '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
