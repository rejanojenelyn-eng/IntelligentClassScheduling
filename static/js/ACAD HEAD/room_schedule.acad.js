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
let _frsView       = 'week';   // 'week' | 'day' (Day = today)
let _frsSessions   = [];       // last loaded room sessions (redrawn on view change)
let _frsRoomSearch = '';
function _frsToday() { const d = new Date(); d.setHours(0, 0, 0, 0); return d; }

const _initEl = document.getElementById('frs-init-data');
_buildings    = JSON.parse(_initEl.dataset.buildings || '[]');
_rooms        = JSON.parse(_initEl.dataset.rooms     || '[]');
const _activeAyId = _initEl.dataset.activeAyId || '';
const _activeSem  = _initEl.dataset.activeSem  || '';

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
    _buildFloorDropdown();
    _clearGrid();
    _renderRoomList(focusRoomId);
}

/* ═══════════════════════════════════════════
   FLOOR DROPDOWN
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
   ROOM LIST
═══════════════════════════════════════════ */
function _renderRoomList(focusRoomId = null) {
    if (_frsRoomSearch) { _renderRoomSearchResults(); return; }
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
    // Show the requested room (e.g. picked from the room search), else the first one.
    const target = filtered.find(r => String(r.id) === String(focusRoomId)) || filtered[0];
    frsSelectRoom(target.id, target.name, target.bid);
    const el = list.querySelector(`.frs-room-item[data-rid="${target.id}"]`);
    if (el && focusRoomId) el.scrollIntoView({ block: 'nearest' });
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
    const roomObj  = _rooms.find(r => String(r.id) === String(rid));
    document.getElementById('frsCalTitle').textContent = rname;
    const subEl = document.getElementById('frsCalSub');
    if (subEl) subEl.textContent = [bldgObj ? `${bldgObj.name} Building` : '',
                                    roomObj ? _floorLabel(roomObj.floor) : ''].filter(Boolean).join(' · ');
    _frsSessions = [];
    _clearGrid();
    try {
        const params = new URLSearchParams({ scheduler_mode: 'local', include_makeups: '1' });
        if (_activeAyId) params.set('ay_id', _activeAyId);
        if (_activeSem)  params.set('semester', _activeSem);
        const res  = await fetch(`/api/get_room_schedule/${rid}?` + params.toString());
        const data = await res.json();
        _renderRoomCalendar(Array.isArray(data) ? data : []);
    } catch (e) {
        console.error('[ars] room schedule error:', e);
    }
}

/* ═══════════════════════════════════════════
   GRID BUILD + RENDER
═══════════════════════════════════════════ */
const _FRS_MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function _frsShownDates() {
    const today = _frsToday();
    if (_frsView === 'day') return [today];
    const mon = new Date(today); mon.setDate(today.getDate() - ((today.getDay() + 6) % 7));
    return FRS_DAYS.map((_, i) => { const d = new Date(mon); d.setDate(mon.getDate() + i); return d; });
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
            cell.dataset.slot = m;
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
        const day = _normalizeDay(s.daydesc);
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
        // 4px gap at the bottom keeps back-to-back classes visibly separate.
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

    document.getElementById('frsDetailSubjCode').textContent = s.subjectcode  || '—';
    document.getElementById('frsDetailSubjName').textContent = s.subjectname  || '—';
    document.getElementById('frsDetailInstr').textContent    = s.instructor   || 'TBA';
    document.getElementById('frsDetailDay').textContent      = (s.makeup_date ? _frsMakeupDateLabel(s) : s.daydesc)      || '—';
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
/* Building and Room Number are typeable, custom-styled searchable combos (a text
   input + our own dropdown list) rather than plain <select>s, so the admin can type
   to filter instead of scrolling a long list. A native <input list=...>+<datalist>
   was tried first but renders the browser's own unstyled popup — completely out of
   place next to the rest of this page — so this draws its own menu instead (see
   .frs-combo CSS). Room Number's option list is rebuilt every time the Building field
   resolves to a different building (see frsBuildingChanged/_rebuildAvailRoomNumOptions)
   so it only ever offers rooms that actually belong to whichever building is currently
   typed in — not every room in every building. */
let _frsRoomNumOptions = [];   // current allowed room names — reset per selected building

/* Every combo's options() returns [{label, value}, ...] — label is what's shown/typed/
   filtered on, value is what actually gets used (identical to label for Building/Room
   Number, but a 24h "HH:MM" for Start/End Time versus their "7:30 AM"-style label —
   same visible-input/hidden-value split the Manual Editor's own time pickers use). */
const _frsCombos = {
    frsBuildingCombo: { inputId: 'frsAvailBuilding',    menuId: 'frsBuildingComboMenu', options: () => _buildings.map(b => ({ label: b.name, value: b.name })) },
    frsRoomNumCombo:  { inputId: 'frsAvailRoomNum',     menuId: 'frsRoomNumComboMenu',  options: () => _frsRoomNumOptions.map(n => ({ label: n, value: n })) },
    frsStartCombo:    { inputId: 'frsAvailStartTxt',    menuId: 'frsStartComboMenu',    hiddenId: 'frsAvailStart', options: () => _frsTimeOptions() },
    frsEndCombo:      { inputId: 'frsAvailEndTxt',      menuId: 'frsEndComboMenu',      hiddenId: 'frsAvailEnd',   options: () => _frsTimeOptions() },
};

/* 30-min slots across the same grid the room-schedule calendar itself uses (7:30 AM–
   9:00 PM), formatted the same way results already are (_fmt12h) — matches the Manual
   Editor's Start/End Time picker design. */
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

/* Building/Room Number now fire on every keystroke (oninput) rather than only on
   blur/select (onchange) — debounce so typing a full name doesn't fire a fetch per
   character. */
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
    const bid   = _resolveAvailBuildingId();
    const rnum  = document.getElementById('frsAvailRoomNum').value;
    const selDate = document.getElementById('frsAvailDate')?.value || '';
    const hasTimeFilter = !!(day && start && end);

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

let _availLast = null;
let _availView = 'card';
const _availCollapsed = new Set();
function frsAvailRerender() { if (_availLast) _renderAvailRooms(..._availLast); }
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
    g.classList.contains('collapsed') ? _availCollapsed.add(g.dataset.bname) : _availCollapsed.delete(g.dataset.bname);
}
function frsClearAvailFilters() {
    ['frsAvailDay', 'frsAvailType', 'frsAvailBuilding', 'frsAvailRoomNum', 'frsAvailStart',
     'frsAvailStartTxt', 'frsAvailEnd', 'frsAvailEndTxt', 'frsAvailSearch']
        .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    _rebuildAvailRoomNumOptions(null);
    frsFindAvailable();
}

function _renderAvailRooms(rooms, start, end, hasTimeFilter) {
    _availLast = [rooms, start, end, hasTimeFilter];
    const content = document.getElementById('frsAvailContent');
    const q = (document.getElementById('frsAvailSearch')?.value || '').trim().toLowerCase();
    const shown = (rooms || []).filter(r => !q ||
        String(r.roomname || '').toLowerCase().includes(q) || String(r.buildingname || '').toLowerCase().includes(q));
    const byName = (a, b) => String(a.roomname || '').localeCompare(String(b.roomname || ''), undefined, { numeric: true });
    shown.sort((document.getElementById('frsAvailSort')?.value || 'name-asc') === 'name-desc' ? (a, b) => byName(b, a) : byName);
    const countEl = document.getElementById('frsAvailCount');
    if (countEl) countEl.textContent = `Showing ${shown.length} room${shown.length === 1 ? '' : 's'}`;
    if (!shown.length) {
        const msg = q ? 'No rooms match your search.'
            : hasTimeFilter ? 'No available rooms found for the selected criteria.' : 'No rooms found.';
        content.innerHTML = `<div class="frs-cal-loading"><i class="fas fa-door-closed"></i> ${msg}</div>`;
        return;
    }
    const byBldg = {};
    shown.forEach(r => { const k = r.buildingname || 'Other'; (byBldg[k] = byBldg[k] || []).push(r); });
    const timeLabel = hasTimeFilter ? `${_fmt12h(start)} – ${_fmt12h(end)}` : '';
    const badgeOf = r => ((r.roomtype || '').toLowerCase() === 'laboratory'
        ? '<span class="frs-avail-badge frs-badge-lab">LAB</span>'
        : '<span class="frs-avail-badge frs-badge-lec">LECTURE</span>');
    const clickAttr = r => `onclick="frsShowRoomSchedulePopup(${r.roomid}, '${_esc(r.roomname || '')}', '${_esc(r.buildingname || '')}')"`;
    let html = '';
    Object.entries(byBldg).sort(([a], [b]) => a.localeCompare(b)).forEach(([bname, bRooms]) => {
        html += `<div class="frs-avail-group${_availCollapsed.has(bname) ? ' collapsed' : ''}" data-bname="${_esc(bname)}">
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
                html += `<tr class="frs-avail-row" title="View this room's schedule" ${clickAttr(r)}>
                    <td class="frs-avail-row-name">${_esc(r.roomname || '')}</td>
                    <td>${badgeOf(r)}</td>
                    <td>${r.roomcapacity ? _esc(String(r.roomcapacity)) : '—'}</td>
                    ${timeLabel ? `<td>${_esc(timeLabel)}</td>` : ''}
                    <td style="text-align:right;color:#800000;font-weight:700;font-size:.72rem;">View Schedule</td>
                </tr>`;
            });
            html += `</tbody></table>`;
        } else {
            html += `<div class="frs-avail-cards">`;
            bRooms.forEach(r => {
                const cap = r.roomcapacity
                    ? `<div class="frs-avail-detail"><i class="fas fa-users"></i> Capacity: ${_esc(String(r.roomcapacity))}</div>` : '';
                html += `
                <div class="frs-avail-card frs-avail-card-clickable" title="View this room's schedule" ${clickAttr(r)}>
                    ${hasTimeFilter ? '<div class="frs-avail-dot"></div>' : ''}
                    <div class="frs-avail-card-top">
                        <div class="frs-avail-room-name">${_esc(r.roomname || '')}</div>
                        ${badgeOf(r)}
                    </div>
                    ${cap}
                    ${timeLabel ? `<div class="frs-avail-time">${_esc(timeLabel)}</div>` : ''}
                    ${hasTimeFilter ? `<div class="frs-avail-status-chip">&#10003; Available</div>` : ''}
                    <div class="frs-avail-request-hint"><i class="fas fa-calendar-alt"></i> View Schedule</div>
                </div>`;
            });
            html += `</div>`;
        }
        html += `</div></div>`;
    });
    content.innerHTML = html;
}

/* ═══════════════════════════════════════════
   ROOM SCHEDULE POPUP (Available Rooms → room card)
═══════════════════════════════════════════ */
let _frsPopupSeq = 0;
const FRS_POP_ROW = 22;
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
            </div>
        </div>`;
    el.addEventListener('click', e => { if (e.target === el) frsCloseRoomSchedulePopup(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && el.classList.contains('open')) frsCloseRoomSchedulePopup(); });
    document.body.appendChild(el);
    return el;
}
async function frsShowRoomSchedulePopup(roomId, roomName, buildingName) {
    const seq = ++_frsPopupSeq;
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
        const params = new URLSearchParams({ scheduler_mode: 'local' });
        if (_activeAyId) params.set('ay_id', _activeAyId);
        if (_activeSem)  params.set('semester', _activeSem);
        const res  = await fetch(`/api/get_room_schedule/${roomId}?` + params.toString());
        const data = await res.json();
        if (seq !== _frsPopupSeq) return;
        body.innerHTML = _frsPopupGrid(Array.isArray(data) ? data : []);
    } catch (e) {
        if (seq === _frsPopupSeq) body.innerHTML = '<div class="frs-cal-loading"><i class="fas fa-exclamation-circle"></i> Could not load the schedule.</div>';
    }
}
function _frsPopupGrid(sessions) {
    const colH = (FRS_GRID_END - FRS_GRID_START) / 30 * FRS_POP_ROW;
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


/* Darken (amount < 0) or lighten a #rrggbb color — the card's left accent border. */
function _frsShade(hex, amount) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ''));
    if (!m) return '#999';
    const n = parseInt(m[1], 16);
    const ch = v => Math.max(0, Math.min(255, Math.round(amount < 0 ? v * (1 + amount) : v + (255 - v) * amount)));
    return '#' + [ch(n >> 16), ch((n >> 8) & 255), ch(n & 255)].map(v => v.toString(16).padStart(2, '0')).join('');
}
