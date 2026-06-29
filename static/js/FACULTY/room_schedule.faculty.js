/* room_schedule.faculty.js — Room Schedule page for Faculty */

/* ── Grid config ── */
const FRS_GRID_START = 7 * 60 + 30;  // 7:30 AM  (450 min)
const FRS_GRID_END   = 21 * 60;      // 9:00 PM  (1260 min)
const FRS_SLOT_H     = 30;           // px per 30-min cell row
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

/* ── State ── */
let _buildings       = [];
let _rooms           = [];
let _activeBldg      = null;
let _activeRoom      = null;
let _floorFilter     = '';
let _currentConflict = null;  // set by _renderAvailRooms; blocks room card clicks when non-null

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
    _buildFloorDropdown();   // dynamically populate floors for this building
    _floorFilter = '';
    _renderRoomList();
    _clearGrid();
}

/* ═══════════════════════════════════════════
   FLOOR DROPDOWN — dynamically built
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

    // Auto-select the only floor if there's just one
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
   ROOM LIST (left sidebar)
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
        if (idx === 0) frsSelectRoom(r.id, r.name, r.bid); // auto-select first
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
        console.error('[frs] room schedule error:', e);
    }
}

/* ═══════════════════════════════════════════
   GRID BUILD + RENDER (30-min rows, 7:30 AM – 9:00 PM)
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

    // 30-min slot rows from 7:30 AM to 9:00 PM
    for (let m = FRS_GRID_START; m <= FRS_GRID_END; m += 30) {
        const lbl = document.createElement('div');
        lbl.className   = 'frs-time-cell';
        lbl.textContent = _minsToLabel(m);
        grid.appendChild(lbl);

        FRS_DAYS.forEach(day => {
            const cell = document.createElement('div');
            cell.className    = 'frs-day-col-cell';
            cell.dataset.day  = day;
            cell.dataset.slot = m;   // absolute minutes value of this slot
            grid.appendChild(cell);
        });
    }
}

function _clearGrid() {
    document.querySelectorAll('.frs-pill').forEach(p => p.remove());
}

function _renderRoomCalendar(sessions) {
    _clearGrid();
    sessions.forEach(s => {
        const day    = _normalizeDay(s.daydesc);
        if (!day) return;

        const startM = _timeidToMins(s.starttimeid);
        const endM   = _timeidToMins(s.endtimeid);
        if (!startM || !endM || endM <= startM) return;
        if (endM <= FRS_GRID_START || startM >= FRS_GRID_END) return;

        const visStart   = Math.max(startM, FRS_GRID_START);
        const visEnd     = Math.min(endM, FRS_GRID_END);
        const anchorSlot = Math.floor((visStart - FRS_GRID_START) / 30) * 30 + FRS_GRID_START;
        const cell = document.querySelector(`.frs-day-col-cell[data-day="${day}"][data-slot="${anchorSlot}"]`);
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
        pill.dataset.sess     = JSON.stringify(s);
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
    const startLabel = _minsToLabel(_timeidToMins(s.starttimeid) || FRS_GRID_START);
    const endLabel   = _minsToLabel(_timeidToMins(s.endtimeid)   || FRS_GRID_START);
    const timeRange  = `${startLabel} – ${endLabel}`;
    const _st = (s.status || '').toLowerCase();
    const statusCls  = _st === 'published' ? 'frs-detail-badge-pub'
                     : _st === 'local'     ? 'frs-detail-badge-local'
                     : 'frs-detail-badge-draft';
    const statusTxt  = _st === 'local' ? 'Local Arrangement' : (s.status || 'Draft');
    const yrLabel    = s.year_level ? `${s.year_level}${_ordSuffix(s.year_level)} Year` : '—';

    document.getElementById('frsDetailSubjCode').textContent  = s.subjectcode  || '—';
    document.getElementById('frsDetailSubjName').textContent  = s.subjectname  || '—';
    document.getElementById('frsDetailInstr').textContent     = s.instructor   || 'TBA';
    document.getElementById('frsDetailDay').textContent       = s.daydesc      || '—';
    document.getElementById('frsDetailTime').textContent      = timeRange;
    document.getElementById('frsDetailRoom').textContent      = s.roomname     || '—';
    document.getElementById('frsDetailProg').textContent      = s.programcode  || '—';
    document.getElementById('frsDetailYr').textContent        = yrLabel;
    const badgeEl = document.getElementById('frsDetailStatus');
    badgeEl.textContent  = statusTxt;
    badgeEl.className    = 'frs-detail-badge ' + statusCls;
    // Accent strip color matches pill
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

// timeid 1 = 7:30 AM = 450 min; each step = 30 min
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

    // Room-number filter: populated from all rooms
    const rSel = document.getElementById('frsAvailRoomNum');
    _rooms.forEach(r => {
        const opt = document.createElement('option');
        opt.value = r.name;
        opt.textContent = r.name;
        rSel.appendChild(opt);
    });
}

/* Auto-fill day-of-week when a date is selected */
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
    const selDate = document.getElementById('frsAvailDate') ? document.getElementById('frsAvailDate').value : '';

    // Availability is only computed when all three time criteria are provided
    const hasTimeFilter = !!(day && start && end);
    if (!hasTimeFilter) _currentConflict = null;  // no time selected → no conflict possible

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
    if (selDate) params.set('date',        selDate);  // pass specific date for makeup checks

    try {
        const [roomRes, conflictRes] = await Promise.all([
            fetch('/api/faculty/available_rooms?' + params.toString()),
            hasTimeFilter
                ? fetch('/api/faculty/check_request_conflicts?' + params.toString())
                : Promise.resolve(null)
        ]);
        const data = await roomRes.json();
        if (!data.success) {
            content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-exclamation-circle"></i> Error fetching rooms.</div>`;
            return;
        }
        let rooms = data.rooms || [];
        if (rnum) rooms = rooms.filter(r => (r.roomname || '').toLowerCase().includes(rnum.toLowerCase()));

        let conflicts = null;
        if (conflictRes) {
            try { const cd = await conflictRes.json(); if (cd.success) conflicts = cd; } catch(e) {}
        }
        _renderAvailRooms(rooms, start, end, hasTimeFilter, conflicts);
    } catch (e) {
        content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-exclamation-circle"></i> Network error.</div>`;
    }
}

function _renderAvailRooms(rooms, start, end, hasTimeFilter, conflicts) {
    const content = document.getElementById('frsAvailContent');

    // Store whether there is an active scheduling conflict — used by frsRoomCardClick to block requests
    const hasConflict = !!(conflicts && hasTimeFilter &&
        (conflicts.faculty_conflict || conflicts.section_conflict));
    _currentConflict = hasConflict ? conflicts : null;

    if (!rooms.length) {
        const msg = hasTimeFilter
            ? 'No available rooms found for the selected criteria.'
            : 'No rooms found.';
        content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-door-closed"></i> ${msg}</div>`;
        return;
    }

    // Group by building name
    const byBldg = {};
    rooms.forEach(r => {
        const key = r.buildingname || 'Other';
        if (!byBldg[key]) byBldg[key] = [];
        byBldg[key].push(r);
    });

    const timeLabel = hasTimeFilter ? `${_fmt12h(start)} – ${_fmt12h(end)}` : '';

    // Show conflict warning banner above rooms if faculty/section already has a class at this time
    let conflictBanner = '';
    if (conflicts && hasTimeFilter) {
        const warnings = [];
        if (conflicts.faculty_conflict) warnings.push(`<b>Faculty Conflict:</b> ${conflicts.faculty_detail}`);
        if (conflicts.section_conflict) warnings.push(`<b>Section Conflict:</b> ${conflicts.section_detail}`);
        if (warnings.length) {
            conflictBanner = `<div style="background:#fff8e1;border:1.5px solid #f0a500;border-radius:7px;padding:12px 15px;margin-bottom:14px;font-size:0.78rem;color:#7a5200;">
                <div style="font-weight:800;margin-bottom:5px;display:flex;align-items:center;gap:7px;"><i class="fas fa-triangle-exclamation" style="color:#f0a500;"></i> SCHEDULE CONFLICT AT THIS TIME</div>
                ${warnings.join('<br>')}
                <div style="margin-top:8px;font-size:0.71rem;opacity:.8;">Rooms below are physically available, but you already have a commitment at this time.</div>
            </div>`;
        }
    }

    let html = conflictBanner;
    Object.entries(byBldg).forEach(([bname, bRooms]) => {
        html += `<div class="frs-avail-building-title">${_esc(bname)}</div>`;
        html += `<div class="frs-avail-cards">`;
        bRooms.forEach(r => {
            const isLab = (r.roomtype || '').toLowerCase() === 'laboratory';
            const badge = isLab
                ? '<span class="frs-avail-badge frs-badge-lab">LAB</span>'
                : '<span class="frs-avail-badge frs-badge-lec">LECTURE</span>';
            const cap  = r.roomcapacity
                ? `<div class="frs-avail-detail"><i class="fas fa-users"></i> Capacity: ${_esc(String(r.roomcapacity))}</div>`
                : '';
            const timeLbl    = timeLabel ? `<div class="frs-avail-time">${_esc(timeLabel)}</div>` : '';
            const statusChip = hasTimeFilter
                ? `<div class="frs-avail-status-chip">&#10003; Available</div>`
                : '';
            // When there is a scheduling conflict, style the card as blocked and show a conflict hint
            const cardStyle   = hasConflict ? ' style="opacity:.7;cursor:not-allowed;"' : '';
            const requestHint = hasConflict
                ? `<div class="frs-avail-request-hint" style="color:#f0a500;"><i class="fas fa-triangle-exclamation"></i> Conflict — Cannot Request</div>`
                : `<div class="frs-avail-request-hint"><i class="fas fa-plus-circle"></i> Request</div>`;
            html += `
            <div class="frs-avail-card frs-avail-card-clickable"${cardStyle}
                 title="${hasConflict ? 'You have a scheduling conflict at this time' : 'Request this room'}"
                 onclick="frsRoomCardClick(${r.roomid}, '${_esc(r.roomname || '')}', '${_esc(r.buildingname || '')}')">
                ${hasTimeFilter ? `<div class="frs-avail-dot"${hasConflict ? ' style="background:#f0a500;"' : ''}></div>` : ''}
                <div class="frs-avail-room-name">ROOM ${_esc(r.roomname || '')}</div>
                ${badge}
                ${timeLbl}
                ${cap}
                ${statusChip}
                ${requestHint}
            </div>`;
        });
        html += `</div>`;
    });
    content.innerHTML = html;
}

function frsRoomCardClick(roomId, roomName, buildingName) {
    if (_currentConflict) {
        _showConflictBlock();
        return;
    }
    if (typeof frsOpenRequestFromRoom === 'function') {
        frsOpenRequestFromRoom(roomId, roomName, buildingName);
    }
}

function _showConflictBlock() {
    const lines = [];
    if (_currentConflict.faculty_conflict) lines.push(`<b>Faculty Conflict:</b> ${_esc(_currentConflict.faculty_detail || '')}`);
    if (_currentConflict.section_conflict) lines.push(`<b>Section Conflict:</b> ${_esc(_currentConflict.section_detail || '')}`);

    let el = document.getElementById('frsConflictBlock');
    if (!el) {
        el = document.createElement('div');
        el.id = 'frsConflictBlock';
        el.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:9999;';
        el.addEventListener('click', e => { if (e.target === el) el.style.display = 'none'; });
        document.body.appendChild(el);
    }
    el.innerHTML = `
        <div style="background:#fff;border-radius:10px;max-width:440px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.28);overflow:hidden;">
            <div style="background:#fff3cd;border-bottom:2px solid #f0a500;padding:14px 18px;display:flex;align-items:center;gap:10px;">
                <i class="fas fa-triangle-exclamation" style="color:#f0a500;font-size:1.2rem;flex-shrink:0;"></i>
                <span style="font-weight:800;font-size:0.88rem;color:#7a5200;letter-spacing:.3px;">SCHEDULE CONFLICT AT THIS TIME</span>
            </div>
            <div style="padding:16px 18px;font-size:0.82rem;color:#333;line-height:1.6;">
                ${lines.join('<br>')}
                <div style="margin-top:10px;font-size:0.76rem;color:#888;">You cannot submit a room request for a time when you already have a scheduled class.</div>
            </div>
            <div style="padding:10px 18px 16px;display:flex;justify-content:flex-end;">
                <button onclick="document.getElementById('frsConflictBlock').style.display='none'"
                        style="background:#c0392b;color:#fff;border:none;border-radius:6px;padding:8px 24px;font-size:0.82rem;font-weight:700;cursor:pointer;letter-spacing:.3px;">
                    OK
                </button>
            </div>
        </div>`;
    el.style.display = 'flex';
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
    // Already in "HH:MM AM/PM" format — return as-is
    if (/AM|PM/i.test(timeStr)) return timeStr.toUpperCase();
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
