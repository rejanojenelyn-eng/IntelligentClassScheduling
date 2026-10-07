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
let _availHasTimeFilter = false; // set by _renderAvailRooms: were Day + Start + End chosen?
let _viewedRoom      = null;  // room whose schedule is currently shown (enables "Request This Room")
let _roomLoadSeq     = 0;     // ignores stale schedule responses when rooms are clicked quickly
let _cameFromAvail   = false; // opened from Available Rooms → show the "back" button
let _frsView         = 'week';      // 'week' | 'day'
let _frsAnchor       = _frsToday(); // a date inside the shown week (or the shown day)
let _frsSessions     = [];          // last loaded room sessions (re-drawn on view/date change)
let _frsRoomSearch   = '';

function _frsToday() { const d = new Date(); d.setHours(0, 0, 0, 0); return d; }


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
function frsSetMainTab(tab, fromAvailable = false) {
    const isSchedule = tab === 'schedule';
    _cameFromAvail = isSchedule && fromAvailable;
    document.getElementById('frsBackToAvail').style.display = _cameFromAvail ? '' : 'none';
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
        const icon = /lab/i.test(b.name) ? 'fa-flask' : /gym/i.test(b.name) ? 'fa-dumbbell'
                   : /quad|stand/i.test(b.name) ? 'fa-landmark' : 'fa-building';
        btn.innerHTML   = `<i class="fas ${icon}"></i> ${_esc(b.name.toUpperCase())}`;
        btn.dataset.bid = b.id;
        btn.onclick     = () => frsSelectBuilding(b.id);
        container.appendChild(btn);
    });
    if (_buildings.length) frsSelectBuilding(_buildings[0].id);
}

function frsSelectBuilding(bid, focusRoomId = null) {
    _activeBldg = bid;
    _frsRoomSearch = '';   // a building pick ends any all-buildings room search
    const _rs = document.getElementById('frsRoomSearch'); if (_rs) _rs.value = '';
    _activeRoom = null;
    document.querySelectorAll('.frs-bldg-tab').forEach(t =>
        t.classList.toggle('active', String(t.dataset.bid) === String(bid))
    );
    _floorFilter = '';
    _buildFloorDropdown();   // dynamically populate floors for this building
    _clearGrid();
    _renderRoomList(focusRoomId);
}

/* ═══════════════════════════════════════════
   FLOOR DROPDOWN — dynamically built
═══════════════════════════════════════════ */
const _FLOOR_LABELS = { '1': '1st Floor', '2': '2nd Floor', '3': '3rd Floor', 'other': 'Other' };
function _floorLabel(f) { return _FLOOR_LABELS[f || 'other'] || `${f} Floor`; }

function _buildFloorDropdown() {
    const sel = document.getElementById('frsFloorSel');
    sel.innerHTML = '<option value="">All Floors</option>';

    const bldgRooms = _rooms.filter(r => String(r.bid) === String(_activeBldg));
    const floors    = [...new Set(bldgRooms.map(r => r.floor || 'other'))].sort();

    floors.forEach(f => {
        const opt = document.createElement('option');
        opt.value = f;
        opt.textContent = _floorLabel(f);
        sel.appendChild(opt);
    });

    // Auto-select the only floor if there's just one
    if (floors.length === 1) {
        sel.value = floors[0];
        _floorFilter = floors[0];
    }
}

function frsSearchRooms() {
    _frsRoomSearch = (document.getElementById('frsRoomSearch')?.value || '').trim().toLowerCase();
    if (_frsRoomSearch) { _renderRoomSearchResults(); return; }   // keeps the shown room until a pick
    _activeRoom = null;
    _renderRoomList();
}
/* Room search spans EVERY building (ignores the building tab + floor filter). Results show
   "Building · Floor"; clicking one switches to that room's building tab and opens it. */
function _renderRoomSearchResults() {
    const list = document.getElementById('frsRoomList');
    const q = _frsRoomSearch;
    const bName = bid => (_buildings.find(b => String(b.id) === String(bid)) || {}).name || '';
    const hits = _rooms.filter(r => String(r.name).toLowerCase().includes(q) ||
                                    bName(r.bid).toLowerCase().includes(q))
        .sort((a, b) => String(a.name).localeCompare(String(b.name), undefined, { numeric: true }));
    const countEl = document.getElementById('frsRoomCount');
    if (countEl) countEl.textContent = `${hits.length} room${hits.length === 1 ? '' : 's'}`;
    list.innerHTML = '';
    if (!hits.length) {
        list.innerHTML = '<div class="frs-room-empty">No rooms found.</div>';
        return;
    }
    hits.forEach(r => {
        const item = document.createElement('div');
        item.className   = 'frs-room-item' + (String(r.id) === String(_activeRoom) ? ' active' : '');
        item.innerHTML   = `<span class="frs-room-name">${_esc(r.name)}</span>` +
                           `<span class="frs-room-floor">${_esc([bName(r.bid), _floorLabel(r.floor)].filter(Boolean).join(' · '))}</span>`;
        item.dataset.rid = r.id;
        item.onclick     = () => frsOpenRoomFromSearch(r.id, r.bid);
        list.appendChild(item);
    });
}

function frsOpenRoomFromSearch(rid, bid) {
    const input = document.getElementById('frsRoomSearch');
    if (input) input.value = '';
    _frsRoomSearch = '';
    frsSelectBuilding(bid, rid);
    const tab = document.querySelector(`.frs-bldg-tab[data-bid="${bid}"]`);
    if (tab && tab.scrollIntoView) tab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
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
function _renderRoomList(focusRoomId = null) {
    if (_frsRoomSearch) { _renderRoomSearchResults(); return; }
    const list = document.getElementById('frsRoomList');
    list.innerHTML = '';
    _setViewedRoom(null);


    if (_activeBldg === null) {
        list.innerHTML = '<div class="frs-room-empty">Select a building</div>';
        return;
    }


    let filtered = _rooms.filter(r => String(r.bid) === String(_activeBldg));
    if (_floorFilter) {
        filtered = filtered.filter(r => (r.floor || 'other') === _floorFilter);
    }
    const countEl = document.getElementById('frsRoomCount');
    if (countEl) countEl.textContent = `${filtered.length} room${filtered.length === 1 ? '' : 's'}`;


    if (!filtered.length) {
        list.innerHTML = '<div class="frs-room-empty">No rooms found.</div>';
        return;
    }


    filtered.forEach(r => {
        const item = document.createElement('div');
        item.className   = 'frs-room-item';
        item.innerHTML   = `<span class="frs-room-name">${_esc(r.name)}</span>` +
                           `<span class="frs-room-floor">${_esc(_floorLabel(r.floor))}</span>`;
        item.dataset.rid = r.id;
        item.onclick     = () => frsSelectRoom(r.id, r.name, r.bid);
        list.appendChild(item);
    });
    // Show the requested room (e.g. clicked from Available Rooms), else the first one.
    const target = filtered.find(r => String(r.id) === String(focusRoomId)) || filtered[0];
    frsSelectRoom(target.id, target.name, target.bid);
    const el = list.querySelector(`.frs-room-item[data-rid="${target.id}"]`);
    if (el && focusRoomId) el.scrollIntoView({ block: 'nearest' });
}


/* ═══════════════════════════════════════════
   ROOM SELECTION & CALENDAR
═══════════════════════════════════════════ */
async function frsSelectRoom(rid, rname, bid) {
    const seq = ++_roomLoadSeq;
    _activeRoom = rid;
    _setViewedRoom(null);   // no request until THIS room's schedule is on screen
    document.querySelectorAll('.frs-room-item').forEach(el =>
        el.classList.toggle('active', String(el.dataset.rid) === String(rid))
    );
    const bldgObj  = _buildings.find(b => String(b.id) === String(bid));
    const roomObj  = _rooms.find(r => String(r.id) === String(rid));
    document.getElementById('frsCalTitle').textContent = rname;
    const subEl = document.getElementById('frsCalSub');
    if (subEl) subEl.textContent = [bldgObj ? `${bldgObj.name} Building` : '',
                                    roomObj ? _floorLabel(roomObj.floor) : ''].filter(Boolean).join(' · ');
    _frsSessions = [];
    _clearGrid();
    try {
        const res  = await fetch(`/api/get_room_schedule/${rid}?scheduler_mode=local&include_makeups=1`);
        const data = await res.json();
        if (seq !== _roomLoadSeq) return;   // another room was selected meanwhile
        _renderRoomCalendar(Array.isArray(data) ? data : []);
        _setViewedRoom({ id: rid, name: rname, building: bldgObj ? bldgObj.name : '' });
    } catch (e) {
        console.error('[frs] room schedule error:', e);
    }
}


/* "Request This Room" is offered only for the room whose schedule is shown. */
function _setViewedRoom(room) {
    _viewedRoom = room;
    const btn = document.getElementById('frsRequestRoomBtn');
    if (!btn) return;
    btn.style.display = room ? '' : 'none';
    btn.title = room ? `Request room ${room.name} after checking its schedule` : '';
}


function frsRequestViewedRoom() {
    if (!_viewedRoom) return;
    if (typeof frsOpenRequestFromRoom === 'function') {
        frsOpenRequestFromRoom(_viewedRoom.id, _viewedRoom.name, _viewedRoom.building);
    }
}


/* Available Rooms → a room clicked with no filters: show that room's current
   schedule first (Schedule View), with Request available from there. */
function frsViewRoomSchedule(roomId) {
    const room = _rooms.find(r => String(r.id) === String(roomId));
    if (!room) return;
    frsSetMainTab('schedule', true);
    frsSelectBuilding(room.bid, room.id);
    document.getElementById('frsScheduleViewPanel').scrollIntoView({ behavior: 'smooth', block: 'start' });
}


function frsBackToAvailable() {
    frsSetMainTab('available');   // filters are kept in their inputs
}

/* ═══════════════════════════════════════════
   GRID BUILD + RENDER (30-min rows, 7:30 AM – 9:00 PM)
═══════════════════════════════════════════ */
const _FRS_MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

/* Monday of the week containing d. */
function _frsWeekStart(d) {
    const x = new Date(d); const dow = (x.getDay() + 6) % 7; x.setDate(x.getDate() - dow); return x;
}
/* The days (Date objects) currently shown: the whole week, or just the anchor day. */
function _frsShownDates() {
    if (_frsView === 'day') return [new Date(_frsAnchor)];
    const mon = _frsWeekStart(_frsAnchor);
    return FRS_DAYS.map((_, i) => { const d = new Date(mon); d.setDate(mon.getDate() + i); return d; });
}
function _frsUpdateRangeLabel(dates) {
    const el = document.getElementById('frsDateRange');
    if (!el) return;
    const a = dates[0], b = dates[dates.length - 1];
    el.textContent = dates.length === 1
        ? `${FRS_DAYS[(a.getDay() + 6) % 7]}, ${_FRS_MON[a.getMonth()]} ${a.getDate()}, ${a.getFullYear()}`
        : `${_FRS_MON[a.getMonth()]} ${a.getDate()} – ${_FRS_MON[b.getMonth()]} ${b.getDate()}, ${b.getFullYear()}`;
}

function frsSetView(view) {
    _frsView = view === 'day' ? 'day' : 'week';
    document.getElementById('frsViewDay')?.classList.toggle('active', _frsView === 'day');
    document.getElementById('frsViewWeek')?.classList.toggle('active', _frsView === 'week');
    _buildGrid();
    _renderRoomCalendar(_frsSessions);
}

function _buildGrid() {
    const grid = document.getElementById('frsGrid');
    grid.innerHTML = '';
    const dates = _frsShownDates();
    const today = _frsToday().getTime();
    grid.classList.toggle('frs-week', _frsView === 'week');
    grid.classList.toggle('frs-day',  _frsView === 'day');
    _frsUpdateRangeLabel(dates);

    // Header row: TIME + one column per shown day, with its date
    const th = document.createElement('div');
    th.className = 'frs-grid-header-cell';
    th.textContent = 'TIME';
    grid.appendChild(th);
    dates.forEach(d => {
        const h = document.createElement('div');
        h.className = 'frs-grid-header-cell' + (d.getTime() === today ? ' frs-hd-today' : '');
        h.innerHTML = `${FRS_DAYS[(d.getDay() + 6) % 7].toUpperCase()}` +
                      `<span class="frs-hd-date">${_FRS_MON[d.getMonth()]} ${d.getDate()}</span>`;
        grid.appendChild(h);
    });

    // 30-min slot rows from 7:30 AM to 9:00 PM
    for (let m = FRS_GRID_START; m <= FRS_GRID_END; m += 30) {
        const lbl = document.createElement('div');
        lbl.className   = 'frs-time-cell';
        lbl.textContent = _minsToLabel(m);
        grid.appendChild(lbl);

        dates.forEach(d => {
            const day  = FRS_DAYS[(d.getDay() + 6) % 7];
            const cell = document.createElement('div');
            cell.className    = 'frs-day-col-cell' + (d.getTime() === today ? ' frs-col-today' : '');
            cell.dataset.day  = day;
            cell.dataset.slot = m;   // absolute minutes value of this slot
            grid.appendChild(cell);
        });
    }
}

function _clearGrid() {
    document.querySelectorAll('.frs-pill').forEach(p => p.remove());
}

/* Approved Make-up classes (include_makeups=1) are one-date meetings: shown only in
   the week/day that contains their makeup_date. */
function _frsYmd(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function _frsMakeupVisible(s) {
    if (!s.makeup_date) return true;
    return _frsShownDates().some(d => _frsYmd(d) === s.makeup_date);
}
function _frsMakeupDateLabel(s) {
    const m = String(s.makeup_date || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return s.daydesc || '—';
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    return `${s.daydesc}, ${_FRS_MON[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
}

function _renderRoomCalendar(sessions) {
    _frsSessions = sessions || [];
    _clearGrid();
    _frsSessions.forEach(s => {
        if (!_frsMakeupVisible(s)) return;
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
        // Every class in this calendar is in the same room, so cards don't repeat the room.
        // A 4px gap at the bottom keeps back-to-back classes visibly separate.
        const heightPx = Math.max((visEnd - visStart) * (FRS_SLOT_H / 30) - 4, 20);
        if (heightPx <= 0) return;

        const isLocal  = (s.status || '').toLowerCase() === 'local';
        const isMakeup = !!s.makeup_date;
        const pill = document.createElement('div');
        const color = _subjectColor(s.subjectcode || '');
        pill.className        = 'frs-pill' + (isLocal ? ' frs-pill-local' : '') + (isMakeup ? ' frs-pill-makeup' : '')
                              + (heightPx < 70 ? ' frs-pill-compact' : '');
        pill.style.top        = offsetPx + 'px';
        pill.style.height     = heightPx + 'px';
        pill.style.background = color;
        if (!isLocal) pill.style.borderLeftColor = _frsShade(color, -0.42);
        pill.style.cursor     = 'pointer';
        pill.title            = isMakeup ? `Make-up class (${_frsMakeupDateLabel(s)}) — Click to view details`
                              : isLocal ? 'Local Arrangement — Click to view details' : 'Click to view details';
        pill.dataset.sess     = JSON.stringify(s);
        pill.addEventListener('click', () => frsShowDetail(s));

        const instrName = (s.instructor || 'TBA').split(',')[0].trim();
        pill.innerHTML =
            (isLocal ? `<span class="frs-pill-la-badge">LA</span>` : '') +
            (isMakeup ? `<span class="frs-pill-la-badge frs-pill-mu-badge">MAKE-UP</span>` : '') +
            `<span class="frs-pill-code">${_esc(s.subjectcode || '')}</span>` +
            `<span class="frs-pill-name">${_esc(s.subjectname || '')}</span>` +
            `<div class="frs-pill-meta"><i class="fas fa-user"></i> ${_esc(instrName)}</div>` +
            `<div class="frs-pill-time">${_minsToLabel(startM)} - ${_minsToLabel(endM)}</div>`;
        cell.appendChild(pill);
    });
}

/* ─── Schedule Detail Modal ─── */
function frsShowDetail(s) {
    const startLabel = _minsToLabel(_timeidToMins(s.starttimeid) || FRS_GRID_START);
    const endLabel   = _minsToLabel(_timeidToMins(s.endtimeid)   || FRS_GRID_START);
    const timeRange  = `${startLabel} – ${endLabel}`;
    const _st = (s.status || '').toLowerCase();
    const statusCls  = s.makeup_date       ? 'frs-detail-badge-makeup'
                     : _st === 'published' ? 'frs-detail-badge-pub'
                     : _st === 'local'     ? 'frs-detail-badge-local'
                     : 'frs-detail-badge-draft';
    const statusTxt  = s.makeup_date ? 'Make-up Class' : _st === 'local' ? 'Local Arrangement' : (s.status || 'Draft');
    const yrLabel    = s.year_level ? `${s.year_level}${_ordSuffix(s.year_level)} Year` : '—';

    document.getElementById('frsDetailSubjCode').textContent  = s.subjectcode  || '—';
    document.getElementById('frsDetailSubjName').textContent  = s.subjectname  || '—';
    document.getElementById('frsDetailInstr').textContent     = s.instructor   || 'TBA';
    document.getElementById('frsDetailDay').textContent       = (s.makeup_date ? _frsMakeupDateLabel(s) : s.daydesc)      || '—';
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
/* Building and Room Number are typeable, custom-styled searchable combos (a text
   input + our own dropdown list) rather than plain <select>s, so faculty can type to
   filter instead of scrolling a long list. A native <input list=...>+<datalist> was
   tried first but renders the browser's own unstyled popup — completely out of place
   next to the rest of this page — so this draws its own menu instead (see .frs-combo
   CSS). Room Number's option list is rebuilt every time the Building field resolves to
   a different building (see frsBuildingChanged/_rebuildAvailRoomNumOptions) so it only
   ever offers rooms that actually belong to whichever building is currently typed in —
   not every room in every building. */
let _frsRoomNumOptions = [];   // current allowed room names — reset per selected building

/* Every combo's options() returns [{label, value}, ...] — label is what's shown/typed/
   filtered on, value is what actually gets used (identical to label for Building/Room
   Number, but a 24h "HH:MM" for Start/End Time versus their "7:30 AM"-style label —
   same visible-input/hidden-value split the Manual Editor's own time pickers use).
   Start/End Time were previously plain <select>s that nothing ever populated with
   options — this both fixes that and matches the Manual Editor's picker design. */
const _frsCombos = {
    frsBuildingCombo: { inputId: 'frsAvailBuilding',    menuId: 'frsBuildingComboMenu', options: () => _buildings.map(b => ({ label: b.name, value: b.name })) },
    frsRoomNumCombo:  { inputId: 'frsAvailRoomNum',     menuId: 'frsRoomNumComboMenu',  options: () => _frsRoomNumOptions.map(n => ({ label: n, value: n })) },
    frsStartCombo:    { inputId: 'frsAvailStartTxt',    menuId: 'frsStartComboMenu',    hiddenId: 'frsAvailStart', options: () => _frsTimeOptions() },
    frsEndCombo:      { inputId: 'frsAvailEndTxt',      menuId: 'frsEndComboMenu',      hiddenId: 'frsAvailEnd',   options: () => _frsTimeOptions() },
};

/* 30-min slots across the same grid the room-schedule calendar itself uses (7:30 AM–
   9:00 PM), formatted the same way results already are (_fmt12h). */
function _frsTimeOptions() {
    const out = [];
    for (let mins = FRS_GRID_START; mins <= FRS_GRID_END; mins += 30) {
        const value = `${String(Math.floor(mins / 60)).padStart(2, '0')}:${String(mins % 60).padStart(2, '0')}`;
        out.push({ label: _fmt12h(value), value });
    }
    return out;
}

function _frsComboFilter(comboKey) {
    const cfg   = _frsCombos[comboKey];
    const input = document.getElementById(cfg.inputId);
    const menu  = document.getElementById(cfg.menuId);
    // Time combos are read-only (click-to-open, like the Manual Editor's) — always
    // show the full list rather than filtering on a value that's never typed into.
    const q     = cfg.hiddenId ? '' : (input.value || '').trim().toLowerCase();
    const all   = cfg.options();
    const filtered = !q ? all : all.filter(o => o.label.toLowerCase().includes(q));
    menu.innerHTML = filtered.length
        ? filtered.slice(0, 50).map(o => `<div class="frs-combo-option" data-val="${_esc(o.value)}">${_esc(o.label)}</div>`).join('')
        : '<div class="frs-combo-empty">No matches</div>';
    menu.classList.add('open');
}

document.addEventListener('click', (e) => {
    const optEl = e.target.closest('.frs-combo-option');
    if (optEl) {
        const wrap    = optEl.closest('.frs-combo');
        const cfg     = _frsCombos[wrap.id];
        const chosen  = cfg.options().find(o => o.value === optEl.dataset.val);
        document.getElementById(cfg.inputId).value = chosen ? chosen.label : optEl.dataset.val;
        if (cfg.hiddenId) document.getElementById(cfg.hiddenId).value = optEl.dataset.val;
        document.getElementById(cfg.menuId).classList.remove('open');
        if (wrap.id === 'frsBuildingCombo') frsBuildingChanged();
        else if (wrap.id === 'frsRoomNumCombo') frsRoomNumTyped();
        else frsFindAvailable();
        return;
    }
    if (!e.target.closest('.frs-combo')) {
        document.querySelectorAll('.frs-combo-menu.open').forEach(m => m.classList.remove('open'));
    }
});

function _buildAvailBuildingFilter() {
    _rebuildAvailRoomNumOptions(null);
}

/* Resolves the Building input's typed text to a building id via an exact,
   case-insensitive name match. Returns null when it doesn't match anything yet
   (still typing, blank, or a typo) — callers treat that as "all buildings". */
function _resolveAvailBuildingId() {
    const typed = (document.getElementById('frsAvailBuilding').value || '').trim().toLowerCase();
    if (!typed) return null;
    const match = _buildings.find(b => b.name.toLowerCase() === typed);
    return match ? match.id : null;
}

function _rebuildAvailRoomNumOptions(bid) {
    const scoped = bid ? _rooms.filter(r => String(r.bid) === String(bid)) : _rooms;
    _frsRoomNumOptions = scoped.map(r => r.name);
    // A previously-typed room number that no longer belongs to the newly-selected
    // building would silently filter everything out — clear it instead.
    const roomInput = document.getElementById('frsAvailRoomNum');
    const curRoom = (roomInput.value || '').trim();
    if (curRoom && !scoped.some(r => r.name.toLowerCase() === curRoom.toLowerCase())) {
        roomInput.value = '';
    }
}

/* Typing now fires on every keystroke (oninput) rather than only on blur/select
   (onchange) — debounce so typing a full name doesn't fire a fetch per character. */
let _frsAvailTypingTimer = null;
function _frsFindAvailableDebounced() {
    clearTimeout(_frsAvailTypingTimer);
    _frsAvailTypingTimer = setTimeout(frsFindAvailable, 300);
}

function frsBuildingChanged() {
    _frsComboFilter('frsBuildingCombo');
    _rebuildAvailRoomNumOptions(_resolveAvailBuildingId());
    _frsFindAvailableDebounced();
}

function frsRoomNumTyped() {
    _frsComboFilter('frsRoomNumCombo');
    _frsFindAvailableDebounced();
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

/* Day picked first → fill the Date with the nearest upcoming date on that weekday
   (today counts). A Date the user already chose is kept when it is on that weekday and
   not in the past. Clearing the Day clears the Date. Local-time math (no toISOString,
   which is UTC and can shift the date by one day). */
function _frsYmd(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function frsDayChanged() {
    const dateEl = document.getElementById('frsAvailDate');
    const day    = document.getElementById('frsAvailDay').value;
    if (dateEl) {
        if (!day) {
            dateEl.value = '';
        } else {
            const days  = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
            const want  = days.indexOf(day);
            const today = new Date(); today.setHours(0, 0, 0, 0);
            const cur   = dateEl.value ? new Date(dateEl.value + 'T00:00:00') : null;
            const keep  = cur && !isNaN(cur) && cur.getDay() === want && cur >= today;
            if (!keep && want >= 0) {
                const next = new Date(today);
                next.setDate(today.getDate() + ((want - today.getDay() + 7) % 7));
                dateEl.value = _frsYmd(next);
            }
        }
    }
    frsFindAvailable();
}

async function frsFindAvailable() {
    const day   = document.getElementById('frsAvailDay').value;
    const start = document.getElementById('frsAvailStart').value;
    const end   = document.getElementById('frsAvailEnd').value;
    const type  = document.getElementById('frsAvailType').value;
    const bid   = _resolveAvailBuildingId();
    const rnum  = document.getElementById('frsAvailRoomNum').value;
    const selDate = document.getElementById('frsAvailDate') ? document.getElementById('frsAvailDate').value : '';

    // Availability is only computed when all three time criteria are provided
    const hasTimeFilter = !!(day && start && end);
    if (!hasTimeFilter) _currentConflict = null;  // no time selected → no conflict possible

    const content = document.getElementById('frsAvailContent');
    content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-spinner fa-spin"></i> Loading rooms…</div>`;

    const bldgName = bid
        ? (_buildings.find(b => String(b.id) === String(bid))?.name || 'Selected building')
        : 'All buildings';
    document.getElementById('frsAvailHeader').textContent =
        `Here are the available rooms for the selected filters (${bldgName}).`;
    const countEl = document.getElementById('frsAvailCount');
    if (countEl) countEl.textContent = '';

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

// Last results from the server; search / sort / view are applied on top client-side.
let _availLast = null;
let _availView = 'card';
const _availCollapsed = new Set();

function frsAvailRerender() {
    if (_availLast) _renderAvailRooms(..._availLast);
}
function frsAvailSetView(v) {
    _availView = v === 'list' ? 'list' : 'card';
    document.getElementById('frsAvailCardBtn')?.classList.toggle('active', _availView === 'card');
    document.getElementById('frsAvailListBtn')?.classList.toggle('active', _availView === 'list');
    frsAvailRerender();
}
function frsAvailToggleGroup(el) {
    const g = el.closest('.frs-avail-group');
    if (!g) return;
    g.classList.toggle('collapsed');
    const key = g.dataset.bname;
    g.classList.contains('collapsed') ? _availCollapsed.add(key) : _availCollapsed.delete(key);
}
function frsClearAvailFilters() {
    ['frsAvailDate', 'frsAvailDay', 'frsAvailType', 'frsAvailBuilding', 'frsAvailRoomNum',
     'frsAvailStart', 'frsAvailStartTxt', 'frsAvailEnd', 'frsAvailEndTxt', 'frsAvailSearch']
        .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    _rebuildAvailRoomNumOptions(null);
    frsFindAvailable();
}

function _renderAvailRooms(rooms, start, end, hasTimeFilter, conflicts) {
    _availLast = [rooms, start, end, hasTimeFilter, conflicts];
    const content = document.getElementById('frsAvailContent');

    // Store whether there is an active scheduling conflict — used by frsRoomCardClick to block requests
    const hasConflict = !!(conflicts && hasTimeFilter &&
        (conflicts.faculty_conflict || conflicts.section_conflict));
    _currentConflict = hasConflict ? conflicts : null;
    _availHasTimeFilter = hasTimeFilter;

    // Toolbar search + sort (client-side, on the loaded results)
    const q = (document.getElementById('frsAvailSearch')?.value || '').trim().toLowerCase();
    let shown = (rooms || []).filter(r => !q ||
        String(r.roomname || '').toLowerCase().includes(q) || String(r.buildingname || '').toLowerCase().includes(q));
    const sort = document.getElementById('frsAvailSort')?.value || 'name-asc';
    const byName = (a, b) => String(a.roomname || '').localeCompare(String(b.roomname || ''), undefined, { numeric: true });
    shown.sort(sort === 'name-desc' ? (a, b) => byName(b, a)
             : sort === 'cap-desc' ? (a, b) => (b.roomcapacity || 0) - (a.roomcapacity || 0) || byName(a, b)
             : sort === 'cap-asc'  ? (a, b) => (a.roomcapacity || 0) - (b.roomcapacity || 0) || byName(a, b)
             : byName);
    const countEl = document.getElementById('frsAvailCount');
    if (countEl) countEl.textContent = `Showing ${shown.length} room${shown.length === 1 ? '' : 's'}`;

    if (!shown.length) {
        const msg = q ? 'No rooms match your search.'
            : hasTimeFilter ? 'No available rooms found for the selected criteria.' : 'No rooms found.';
        content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-door-closed"></i> ${msg}</div>`;
        return;
    }

    // Group by building name (in the chosen sort order)
    const byBldg = {};
    shown.forEach(r => {
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
            conflictBanner = `<div style="background:#fff8e1;border:1.5px solid #f0a500;border-radius:10px;padding:12px 15px;margin-bottom:14px;font-size:0.78rem;color:#7a5200;">
                <div style="font-weight:800;margin-bottom:5px;display:flex;align-items:center;gap:7px;"><i class="fas fa-triangle-exclamation" style="color:#f0a500;"></i> SCHEDULE CONFLICT AT THIS TIME</div>
                ${warnings.join('<br>')}
                <div style="margin-top:8px;font-size:0.71rem;opacity:.8;">Rooms below are physically available, but you already have a commitment at this time.</div>
            </div>`;
        }
    }

    const cardTitle = hasConflict ? 'You have a scheduling conflict at this time'
        : hasTimeFilter ? 'Request this room' : "View this room's current schedule";
    const clickAttr = r => `onclick="frsRoomCardClick(${r.roomid}, '${_esc(r.roomname || '')}', '${_esc(r.buildingname || '')}')"`;
    const badgeOf = r => ((r.roomtype || '').toLowerCase() === 'laboratory'
        ? '<span class="frs-avail-badge frs-badge-lab">LAB</span>'
        : '<span class="frs-avail-badge frs-badge-lec">LECTURE</span>');

    let html = conflictBanner;
    // Buildings A–Z; the chosen sort applies to the rooms inside each building.
    Object.entries(byBldg).sort(([a], [b]) => a.localeCompare(b)).forEach(([bname, bRooms]) => {
        const collapsed = _availCollapsed.has(bname);
        html += `<div class="frs-avail-group${collapsed ? ' collapsed' : ''}" data-bname="${_esc(bname)}">
            <div class="frs-avail-group-head" onclick="frsAvailToggleGroup(this)">
                <i class="fas fa-building-columns"></i>
                <span class="frs-avail-group-name">${_esc(String(bname).toUpperCase())}</span>
                <span class="frs-avail-group-pill">${bRooms.length} room${bRooms.length === 1 ? '' : 's'}</span>
                <i class="fas fa-chevron-up frs-avail-chev"></i>
            </div>
            <div class="frs-avail-group-body">`;
        if (_availView === 'list') {
            html += `<table class="frs-avail-list"><thead><tr><th>Room</th><th>Type</th><th>Capacity</th>${timeLabel ? '<th>Time</th>' : ''}<th></th></tr></thead><tbody>`;
            bRooms.forEach(r => {
                html += `<tr class="frs-avail-row" title="${cardTitle}" ${clickAttr(r)}>
                    <td class="frs-avail-row-name">${_esc(r.roomname || '')}</td>
                    <td>${badgeOf(r)}</td>
                    <td>${r.roomcapacity ? _esc(String(r.roomcapacity)) : '—'}</td>
                    ${timeLabel ? `<td>${_esc(timeLabel)}</td>` : ''}
                    <td style="text-align:right;color:#800000;font-weight:700;font-size:.72rem;">
                        ${hasConflict ? 'Conflict' : hasTimeFilter ? 'Request' : 'View Schedule'}</td>
                </tr>`;
            });
            html += `</tbody></table>`;
        } else {
            html += `<div class="frs-avail-cards">`;
            bRooms.forEach(r => {
                const cap  = r.roomcapacity
                    ? `<div class="frs-avail-detail"><i class="fas fa-users"></i> Capacity: ${_esc(String(r.roomcapacity))}</div>`
                    : '';
                const timeLbl    = timeLabel ? `<div class="frs-avail-time">${_esc(timeLabel)}</div>` : '';
                const statusChip = hasTimeFilter ? `<div class="frs-avail-status-chip">&#10003; Available</div>` : '';
                // When there is a scheduling conflict, style the card as blocked and show a conflict hint
                const cardStyle   = hasConflict ? ' style="opacity:.7;cursor:not-allowed;"' : '';
                // No Day/Time filter yet → the card first shows the room's schedule;
                // Request is offered from there, after the schedule has been seen.
                const requestHint = hasConflict
                    ? `<div class="frs-avail-request-hint" style="color:#f0a500;"><i class="fas fa-triangle-exclamation"></i> Conflict — Cannot Request</div>`
                    : hasTimeFilter
                        ? `<div class="frs-avail-request-hint"><i class="fas fa-plus-circle"></i> Request</div>`
                        : `<div class="frs-avail-request-hint"><i class="fas fa-calendar-alt"></i> View Schedule</div>`;
                html += `
                <div class="frs-avail-card frs-avail-card-clickable"${cardStyle} title="${cardTitle}" ${clickAttr(r)}>
                    ${hasTimeFilter ? `<div class="frs-avail-dot"${hasConflict ? ' style="background:#f0a500;"' : ''}></div>` : ''}
                    <div class="frs-avail-card-top">
                        <div class="frs-avail-room-name">${_esc(r.roomname || '')}</div>
                        ${badgeOf(r)}
                    </div>
                    ${cap}
                    ${timeLbl}
                    ${statusChip}
                    ${requestHint}
                </div>`;
            });
            html += `</div>`;
        }
        html += `</div></div>`;
    });
    content.innerHTML = html;
}
function frsRoomCardClick(roomId, roomName, buildingName) {
    if (_currentConflict) {
        _showConflictBlock();
        return;
    }
    if (!_availHasTimeFilter) {
        // No filters chosen: show the room's weekly schedule in a popup (with a Request
        // button there) — no jump to Schedule View and back.
        frsShowRoomSchedulePopup(roomId, roomName, buildingName);
        return;
    }
    if (typeof frsOpenRequestFromRoom === 'function') {
        frsOpenRequestFromRoom(roomId, roomName, buildingName);
    }
}

/* ═══════════════════════════════════════════
   ROOM SCHEDULE POPUP (Available Rooms, no filters)
   A compact Mon–Sun grid of the room's current schedule + "Request this room".
═══════════════════════════════════════════ */
let _frsPopupRoom = null;
let _frsPopupSeq  = 0;
const FRS_POP_ROW = 22;   // px per 30-min row in the popup grid

function _frsPopupEl() {
    let el = document.getElementById('frsRoomPopup');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'frsRoomPopup';
    el.className = 'frs-rp-overlay';
    el.innerHTML = `
        <div class="frs-rp-box" role="dialog" aria-modal="true">
            <div class="frs-rp-head">
                <div class="frs-rp-icon"><i class="fas fa-building-columns"></i></div>
                <div class="frs-rp-titles">
                    <div class="frs-rp-title" id="frsRpTitle"></div>
                    <div class="frs-rp-sub" id="frsRpSub"></div>
                </div>
                <button type="button" class="frs-rp-close" onclick="frsCloseRoomSchedulePopup()" title="Close">&times;</button>
            </div>
            <div class="frs-rp-body" id="frsRpBody"></div>
            <div class="frs-rp-foot">
                <span class="frs-rp-note"><i class="fas fa-circle-info"></i> Shows the room's regular weekly classes.</span>
                <button type="button" class="frs-rp-cancel" onclick="frsCloseRoomSchedulePopup()">Close</button>
                <button type="button" class="btn-frs-request" id="frsRpRequestBtn" onclick="frsRequestFromPopup()">
                    <i class="fas fa-plus-circle"></i> REQUEST THIS ROOM
                </button>
            </div>
        </div>`;
    el.addEventListener('click', e => { if (e.target === el) frsCloseRoomSchedulePopup(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && el.classList.contains('open')) frsCloseRoomSchedulePopup(); });
    document.body.appendChild(el);
    return el;
}

async function frsShowRoomSchedulePopup(roomId, roomName, buildingName) {
    const seq = ++_frsPopupSeq;
    _frsPopupRoom = { id: roomId, name: roomName, building: buildingName };
    const el = _frsPopupEl();
    const room = _rooms.find(r => String(r.id) === String(roomId));
    document.getElementById('frsRpTitle').textContent = roomName;
    document.getElementById('frsRpSub').textContent = [buildingName ? `${buildingName} Building` : '',
        room ? _floorLabel(room.floor) : '', room && room.capacity ? `Capacity: ${room.capacity}` : '']
        .filter(Boolean).join(' · ');
    const body = document.getElementById('frsRpBody');
    body.innerHTML = '<div class="frs-cal-loading"><i class="fas fa-spinner fa-spin"></i> Loading schedule…</div>';
    el.classList.add('open');
    try {
        const res  = await fetch(`/api/get_room_schedule/${roomId}?scheduler_mode=local`);
        const data = await res.json();
        if (seq !== _frsPopupSeq) return;   // another room was opened meanwhile
        body.innerHTML = _frsPopupGrid(Array.isArray(data) ? data : []);
    } catch (e) {
        if (seq === _frsPopupSeq) body.innerHTML = '<div class="frs-cal-loading"><i class="fas fa-exclamation-circle"></i> Could not load the schedule.</div>';
    }
}

function _frsPopupGrid(sessions) {
    const rows = (FRS_GRID_END - FRS_GRID_START) / 30;
    const colH = rows * FRS_POP_ROW;
    let times = '';
    for (let m = FRS_GRID_START; m < FRS_GRID_END; m += 30) {
        times += `<div class="frs-rp-time" style="height:${FRS_POP_ROW}px">${_minsToLabel(m)}</div>`;
    }
    const cols = FRS_DAYS.map(day => {
        const blocks = sessions.filter(s => _normalizeDay(s.daydesc) === day).map(s => {
            const st = _timeidToMins(s.starttimeid), en = _timeidToMins(s.endtimeid);
            if (!st || !en || en <= st) return '';
            const top = (Math.max(st, FRS_GRID_START) - FRS_GRID_START) / 30 * FRS_POP_ROW;
            const h   = Math.max((Math.min(en, FRS_GRID_END) - Math.max(st, FRS_GRID_START)) / 30 * FRS_POP_ROW - 3, 16);
            const color = _subjectColor(s.subjectcode || '');
            const instr = (s.instructor || 'TBA').split(',')[0].trim();
            return `<div class="frs-rp-block" style="top:${top}px;height:${h}px;background:${color};border-left-color:${_frsShade(color, -0.42)}"
                         title="${_esc(s.subjectcode || '')} — ${_esc(s.subjectname || '')}\n${_esc(instr)}\n${_minsToLabel(st)} - ${_minsToLabel(en)}">
                        <b>${_esc(s.subjectcode || '')}</b>${h >= 40 ? `<span>${_esc(instr)}</span>` : ''}
                        ${h >= 56 ? `<span>${_minsToLabel(st)} - ${_minsToLabel(en)}</span>` : ''}
                    </div>`;
        }).join('');
        return `<div class="frs-rp-col"><div class="frs-rp-dayhead">${day.slice(0, 3).toUpperCase()}</div>
                    <div class="frs-rp-colbody" style="height:${colH}px">${blocks}</div></div>`;
    }).join('');
    const empty = sessions.length ? '' : '<div class="frs-rp-empty">No classes scheduled in this room — it is free all week.</div>';
    return `${empty}<div class="frs-rp-grid">
                <div class="frs-rp-col frs-rp-timecol"><div class="frs-rp-dayhead">TIME</div><div>${times}</div></div>
                ${cols}
            </div>`;
}

function frsCloseRoomSchedulePopup() {
    document.getElementById('frsRoomPopup')?.classList.remove('open');
}

function frsRequestFromPopup() {
    const r = _frsPopupRoom;
    frsCloseRoomSchedulePopup();
    if (r && typeof frsOpenRequestFromRoom === 'function') frsOpenRequestFromRoom(r.id, r.name, r.building);
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


/* Darken (amount < 0) or lighten a #rrggbb color — the card's left accent border. */
function _frsShade(hex, amount) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ''));
    if (!m) return '#999';
    const n = parseInt(m[1], 16);
    const ch = v => Math.max(0, Math.min(255, Math.round(amount < 0 ? v * (1 + amount) : v + (255 - v) * amount)));
    const r = ch(n >> 16), g = ch((n >> 8) & 255), b = ch(n & 255);
    return '#' + [r, g, b].map(v => v.toString(16).padStart(2, '0')).join('');
}
