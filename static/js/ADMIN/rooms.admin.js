// ── Delete confirmation modal ─────────────────────────────────────────────────
let _pendingDeleteFn = null;

function _showDeleteConfirm(title, bodyHtml, onConfirm) {
    document.getElementById('delConfirmTitle').textContent = title;
    document.getElementById('delConfirmMsg').innerHTML    = bodyHtml;
    _pendingDeleteFn = onConfirm;
    const btn = document.getElementById('btnDeleteConfirm');
    if (btn) btn.disabled = false;
    openRoomModal('modalDeleteConfirm');
}
function _closeDeleteConfirm() {
    closeRoomModal('modalDeleteConfirm');
    _pendingDeleteFn = null;
}
function _confirmDeleteAction() {
    const btn = document.getElementById('btnDeleteConfirm');
    if (btn) btn.disabled = true;
    const fn = _pendingDeleteFn;
    _pendingDeleteFn = null;
    closeRoomModal('modalDeleteConfirm');
    if (fn) fn();
}

// ── Modal helpers ─────────────────────────────────────────────────────────────
function openRoomModal(id) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'flex';
    // clear any previous errors
    el && el.querySelectorAll('.rm-error').forEach(e => { e.style.display = 'none'; e.textContent = ''; });
}
function closeRoomModal(id) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
    el && el.querySelectorAll('.rm-error').forEach(e => { e.style.display = 'none'; });
}

// legacy aliases (sidebar btn-dark-add still uses openModal in some places)
function openModal(id)  { openRoomModal(id); }
function closeModal(id) { closeRoomModal(id); }

// Room Schedule (eye icon) — opens as a popup so the Admin never navigates away
// from /admin/rooms; closing it just hides the modal, nothing to redirect.
function openRoomScheduleModal(roomId) {
    const frame = document.getElementById('roomScheduleFrame');
    if (frame) frame.src = `/room/view/${roomId}?modal=1`;
    openRoomModal('modalRoomSchedule');
}
function closeRoomScheduleModal() {
    closeRoomModal('modalRoomSchedule');
    const frame = document.getElementById('roomScheduleFrame');
    if (frame) frame.src = 'about:blank';
}

function _rmErr(errId, msg) {
    const el = document.getElementById(errId);
    if (!el) return;
    el.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${msg}`;
    el.style.display = 'flex';
}

// ── Toast ─────────────────────────────────────────────────────────────────────
let _toastTimer = null;
function _rmToast(type, title, msg) {
    const t  = document.getElementById('roomCrudToast');
    if (!t) return;
    t.className = `rm-toast rm-${type}`;
    document.getElementById('roomCrudToastIcon').innerHTML =
        type === 'success' ? '<i class="fas fa-check-circle"></i>' : '<i class="fas fa-times-circle"></i>';
    document.getElementById('roomCrudToastTitle').textContent = title;
    document.getElementById('roomCrudToastMsg').textContent   = msg;
    requestAnimationFrame(() => t.classList.add('rm-show'));
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => t.classList.remove('rm-show'), 4000);
}

// ── Sidebar filter ────────────────────────────────────────────────────────────
let _activeSidebarBldg = '';

function filterBySidebar(buildingName) {
    _activeSidebarBldg = buildingName.toUpperCase();
    document.querySelectorAll('.btn-building').forEach(b => b.classList.remove('active'));
    const target = buildingName === ''
        ? document.getElementById('bldgBtnAll')
        : document.querySelector(`.btn-building[data-bldg-name="${buildingName}"]`);
    if (target) target.classList.add('active');
    document.getElementById('filterBuilding').value = '';
    _applyRoomFilter();
}

function filterRoomTable() {
    _activeSidebarBldg = '';
    document.querySelectorAll('.btn-building').forEach(b => b.classList.remove('active'));
    document.getElementById('bldgBtnAll')?.classList.add('active');
    _applyRoomFilter();
}

function _applyRoomFilter() {
    const search   = (document.getElementById('searchRoom')?.value   || '').toUpperCase();
    const bldgSel  = (document.getElementById('filterBuilding')?.value || '').toUpperCase();
    const typeSel  = (document.getElementById('filterType')?.value    || '').toUpperCase();
    const bldgFilter = bldgSel !== '' ? bldgSel : _activeSidebarBldg;

    document.querySelectorAll('#roomTable tbody tr').forEach(tr => {
        const name  = (tr.querySelector('.td-room-name')?.textContent  || '').toUpperCase();
        const type  = (tr.querySelector('.td-room-type')?.textContent  || '').toUpperCase();
        const bldg  = (tr.querySelector('.td-building')?.textContent   || '').toUpperCase();
        const show  = name.includes(search)
            && (typeSel   === '' || type === typeSel)
            && (bldgFilter === '' || bldg === bldgFilter);
        tr.style.display = show ? '' : 'none';
    });
}

function toggleAvailabilityRow() {
    const row    = document.getElementById('availabilityRow');
    const banner = document.getElementById('availableBanner');
    row.classList.toggle('active');
    if (banner) banner.style.display = row.classList.contains('active') ? 'block' : 'none';
}

// ── Building right-click context menu ────────────────────────────────────────
function showBuildingMenu(e, id, name) {
    e.preventDefault();
    const menu = document.getElementById('buildingMenu');
    menu.style.display = 'block';
    menu.style.left    = e.pageX + 'px';
    menu.style.top     = e.pageY + 'px';

    document.getElementById('ctxEditBldg').onclick = () => {
        document.getElementById('edit_bldg_id').value   = id;
        document.getElementById('edit_bldg_name').value = name;
        menu.style.display = 'none';
        openRoomModal('modalEditBldg');
    };
    document.getElementById('ctxDeleteBldg').onclick = () => {
        menu.style.display = 'none';
        doDeleteBuilding(id, name);
    };
}
window.addEventListener('click', () => {
    const m = document.getElementById('buildingMenu');
    if (m) m.style.display = 'none';
});

// ── Open Edit Room Modal ──────────────────────────────────────────────────────
function openEditRoomModal(id, name, type, capacity, bldgId, bldgName) {
    document.getElementById('edit_room_id').value       = id;
    document.getElementById('edit_room_name').value     = name;
    document.getElementById('edit_room_type').value     = type;
    document.getElementById('edit_room_capacity').value = capacity;
    const bldgSel = document.getElementById('edit_room_bldg_id');
    if (bldgSel) {
        const bid = (bldgId != null && bldgId !== '' && String(bldgId) !== 'null' && String(bldgId) !== 'undefined')
                    ? String(bldgId) : '';
        if (bid) {
            bldgSel.value = bid;
            // If the option wasn't in the list (e.g. building is inactive), add it temporarily
            if (bldgSel.value !== bid && bldgName) {
                const opt = new Option(bldgName, bid, true, true);
                bldgSel.add(opt, 1); // insert after the placeholder
                bldgSel.value = bid;
            }
        } else {
            bldgSel.value = '';
        }
    }
    openRoomModal('modalEditRoom');
}
// legacy alias
function openEditModal(id, name, type, capacity, bldgId) {
    openEditRoomModal(id, name, type, capacity, bldgId);
}

// ── ADD BUILDING ──────────────────────────────────────────────────────────────
async function submitAddBuilding() {
    const name = (document.getElementById('inp_bldg_name')?.value || '').trim();
    if (!name) { _rmErr('errBldg', 'Building name is required.'); return; }

    const btn = document.getElementById('btnAddBldg');
    btn.disabled = true;
    try {
        const res  = await fetch('/admin/api/add_building', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        const data = await res.json();
        if (!data.success) { _rmErr('errBldg', data.error || 'Failed to add building.'); return; }
        closeRoomModal('modalBldg');
        document.getElementById('inp_bldg_name').value = '';
        _domAddBuildingBtn(data.buildingid, data.buildingname);
        _addBldgToDropdowns(data.buildingid, data.buildingname);
        _rmToast('success', 'Building Added', `"${data.buildingname}" added successfully.`);
    } catch { _rmErr('errBldg', 'Network error. Please try again.'); }
    finally { btn.disabled = false; }
}

// ── EDIT BUILDING ─────────────────────────────────────────────────────────────
async function submitEditBuilding() {
    const bldg_id = document.getElementById('edit_bldg_id')?.value;
    const name    = (document.getElementById('edit_bldg_name')?.value || '').trim();
    if (!name) { _rmErr('errEditBldg', 'Building name is required.'); return; }

    const btn = document.getElementById('btnEditBldg');
    btn.disabled = true;
    try {
        const res  = await fetch('/admin/api/edit_building', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bldg_id: parseInt(bldg_id), name }),
        });
        const data = await res.json();
        if (!data.success) { _rmErr('errEditBldg', data.error || 'Failed to rename.'); return; }
        closeRoomModal('modalEditBldg');
        _domUpdateBuildingName(data.buildingid, data.buildingname);
        _rmToast('success', 'Building Renamed', `Renamed to "${data.buildingname}".`);
    } catch { _rmErr('errEditBldg', 'Network error. Please try again.'); }
    finally { btn.disabled = false; }
}

// ── ADD ROOM ──────────────────────────────────────────────────────────────────
async function submitAddRoom() {
    const name     = (document.getElementById('inp_room_name')?.value  || '').trim();
    const rtype    =  document.getElementById('inp_room_type')?.value  || 'Lecture';
    const capacity =  document.getElementById('inp_room_cap')?.value;
    const bldg_id  =  document.getElementById('inp_room_bldg')?.value;

    if (!name)     { _rmErr('errRoom', 'Room number is required.'); return; }
    if (!capacity) { _rmErr('errRoom', 'Capacity is required.'); return; }
    const capNum = Number(capacity);
    if (!Number.isInteger(capNum) || capNum < 35) { _rmErr('errRoom', 'Capacity must be a whole number of at least 35.'); return; }
    if (!bldg_id)  { _rmErr('errRoom', 'Please select a building.'); return; }

    const btn = document.getElementById('btnAddRoom');
    btn.disabled = true;
    try {
        const res  = await fetch('/admin/api/add_room', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ room_name: name, room_type: rtype, capacity: parseInt(capacity), bldg_id: parseInt(bldg_id) }),
        });
        const data = await res.json();
        if (!data.success) { _rmErr('errRoom', data.error || 'Failed to add room.'); return; }
        closeRoomModal('modalRoom');
        document.getElementById('inp_room_name').value = '';
        document.getElementById('inp_room_cap').value  = '';
        _domAddRoomRow(data);
        _adjustStat('statRooms', 1);
        _adjustStat(data.roomtype === 'Laboratory' ? 'statLabs' : 'statLec', 1);
        _rmToast('success', 'Room Added', `Room "${data.roomname}" added successfully.`);
    } catch { _rmErr('errRoom', 'Network error. Please try again.'); }
    finally { btn.disabled = false; }
}

// ── EDIT ROOM ─────────────────────────────────────────────────────────────────
async function submitEditRoom() {
    const room_id  =  document.getElementById('edit_room_id')?.value;
    const name     = (document.getElementById('edit_room_name')?.value     || '').trim();
    const rtype    =  document.getElementById('edit_room_type')?.value     || 'Lecture';
    const capacity =  document.getElementById('edit_room_capacity')?.value;
    const bldg_id  =  document.getElementById('edit_room_bldg_id')?.value;

    if (!name) { _rmErr('errEditRoom', 'Room number is required.'); return; }
    if (!capacity) { _rmErr('errEditRoom', 'Capacity is required.'); return; }
    const capNum = Number(capacity);
    if (!Number.isInteger(capNum) || capNum < 35) { _rmErr('errEditRoom', 'Capacity must be a whole number of at least 35.'); return; }

    const btn = document.getElementById('btnEditRoom');
    btn.disabled = true;
    try {
        const res  = await fetch('/admin/api/edit_room', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ room_id: parseInt(room_id), room_name: name, room_type: rtype,
                                   capacity: parseInt(capacity), bldg_id: parseInt(bldg_id) }),
        });
        const data = await res.json();
        if (!data.success) { _rmErr('errEditRoom', data.error || 'Failed to update room.'); return; }
        closeRoomModal('modalEditRoom');
        _domUpdateRoomRow(data);
        _rmToast('success', 'Room Updated', `Room "${data.roomname}" updated.`);
    } catch { _rmErr('errEditRoom', 'Network error. Please try again.'); }
    finally { btn.disabled = false; }
}

// ── DELETE ROOM ───────────────────────────────────────────────────────────────
function doDeleteRoom(roomId, roomName) {
    _showDeleteConfirm(
        'Delete Room',
        `Are you sure you want to delete room <strong>${roomName}</strong>?<br>` +
        `This will permanently remove it from the system.`,
        async () => {
            try {
                const res  = await fetch('/admin/api/delete_room', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ room_id: roomId }),
                });
                const data = await res.json();
                if (!data.success) { _rmToast('error', 'Delete Failed', data.error || 'Could not delete room.'); return; }
                const tr    = document.querySelector(`#roomTable tr[data-room-id="${roomId}"]`);
                const rtype = tr?.querySelector('.td-room-type')?.textContent || '';
                tr?.remove();
                _adjustStat('statRooms', -1);
                _adjustStat(rtype === 'Laboratory' ? 'statLabs' : 'statLec', -1);
                _rmToast('success', 'Room Deleted', `"${roomName}" removed.`);
            } catch { _rmToast('error', 'Error', 'Network error. Please try again.'); }
        }
    );
}
// legacy alias
function confirmDelete(id, name) { doDeleteRoom(id, name); }

// ── DELETE BUILDING ───────────────────────────────────────────────────────────
function doDeleteBuilding(bldgId, bldgName) {
    _showDeleteConfirm(
        'Delete Building',
        `Are you sure you want to delete <strong>${bldgName}</strong>?<br>` +
        `All rooms in this building will be permanently removed as well.`,
        async () => {
            try {
                const res  = await fetch('/admin/api/delete_building', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ bldg_id: bldgId }),
                });
                const data = await res.json();
                if (!data.success) { _rmToast('error', 'Delete Failed', data.error || 'Could not delete building.'); return; }
                document.querySelector(`.btn-building[data-bldg-id="${bldgId}"]`)?.remove();
                document.querySelectorAll(`select option[value="${bldgId}"]`).forEach(o => o.remove());
                const removed = document.querySelectorAll(`#roomTable tr[data-building-id="${bldgId}"]`);
                let labs = 0, lec = 0;
                removed.forEach(tr => {
                    const t = tr.querySelector('.td-room-type')?.textContent || '';
                    t === 'Laboratory' ? labs++ : lec++;
                    tr.remove();
                });
                _adjustStat('statRooms', -(labs + lec));
                _adjustStat('statLabs', -labs);
                _adjustStat('statLec',  -lec);
                filterBySidebar('');
                _rmToast('success', 'Building Deleted', `"${bldgName}" and its rooms removed.`);
            } catch { _rmToast('error', 'Error', 'Network error. Please try again.'); }
        }
    );
}

// ── DOM helpers ───────────────────────────────────────────────────────────────
function _domAddBuildingBtn(id, name) {
    const scroll = document.getElementById('bldgListScroll');
    if (!scroll) return;
    const btn = document.createElement('button');
    btn.className          = 'btn-building';
    btn.dataset.bldgId     = id;
    btn.dataset.bldgName   = name;
    btn.textContent        = name;
    btn.onclick            = () => filterBySidebar(name);
    btn.setAttribute('oncontextmenu', `showBuildingMenu(event,${id},'${name.replace(/'/g,"\\'")}');return false;`);
    scroll.appendChild(btn);
}

function _domUpdateBuildingName(id, newName) {
    const btn = document.querySelector(`.btn-building[data-bldg-id="${id}"]`);
    if (btn) {
        btn.textContent      = newName;
        btn.dataset.bldgName = newName;
        btn.onclick          = () => filterBySidebar(newName);
        btn.setAttribute('oncontextmenu', `showBuildingMenu(event,${id},'${newName.replace(/'/g,"\\'")}');return false;`);
    }
    document.querySelectorAll(`select option[value="${id}"]`).forEach(o => o.textContent = newName);
    document.querySelectorAll(`#roomTable tr[data-building-id="${id}"] .td-building`).forEach(td => {
        td.textContent = newName;
    });
}

function _addBldgToDropdowns(id, name) {
    ['filterBuilding', 'inp_room_bldg', 'edit_room_bldg_id'].forEach(selId => {
        const sel = document.getElementById(selId);
        if (sel) sel.appendChild(new Option(name, id));
    });
}

function _domAddRoomRow(r) {
    const tbody = document.querySelector('#roomTable tbody');
    if (!tbody) return;
    const tr  = document.createElement('tr');
    tr.dataset.roomId     = r.roomid;
    tr.dataset.buildingId = r.buildingid;
    tr.innerHTML = `
        <td class="td-room-name" style="font-weight:700;">${r.roomname}</td>
        <td class="td-room-type">${r.roomtype}</td>
        <td class="td-room-cap">${r.roomcapacity}</td>
        <td class="td-building">${r.buildingname}</td>
        <td>
            <div class="action-btns-wrapper" style="justify-content:center;">
                <button class="btn-action view" title="View"
                    onclick="openRoomScheduleModal(${r.roomid})">
                    <i class="fas fa-eye"></i>
                </button>
                <button class="btn-action edit" title="Edit"
                    onclick="openEditRoomModal(${r.roomid},'${_esc(r.roomname)}','${r.roomtype}',${r.roomcapacity},${r.buildingid ?? null})">
                    <i class="fas fa-pen"></i>
                </button>
                <button class="btn-action delete" title="Delete"
                    onclick="doDeleteRoom(${r.roomid},'${_esc(r.roomname)}')">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        </td>`;
    tbody.appendChild(tr);
}

function _domUpdateRoomRow(r) {
    const tr = document.querySelector(`#roomTable tr[data-room-id="${r.roomid}"]`);
    if (!tr) return;
    tr.dataset.buildingId = r.buildingid;
    tr.querySelector('.td-room-name').textContent = r.roomname;
    tr.querySelector('.td-room-type').textContent = r.roomtype;
    tr.querySelector('.td-room-cap').textContent  = r.roomcapacity;
    tr.querySelector('.td-building').textContent  = r.buildingname;
    const editBtn = tr.querySelector('.btn-action.edit');
    if (editBtn) editBtn.setAttribute('onclick',
        `openEditRoomModal(${r.roomid},'${_esc(r.roomname)}','${r.roomtype}',${r.roomcapacity},${r.buildingid})`);
    const delBtn = tr.querySelector('.btn-action.delete');
    if (delBtn) delBtn.setAttribute('onclick', `doDeleteRoom(${r.roomid},'${_esc(r.roomname)}')`);
}

function _esc(s) { return String(s || '').replace(/'/g, "\\'").replace(/"/g, '\\"'); }

function _adjustStat(id, delta) {
    const el = document.getElementById(id);
    if (el) el.textContent = Math.max(0, parseInt(el.textContent || 0) + delta);
}

// ══ Room Export Engine (unchanged from original) ══════════════════════════════

const _ROOM_DOCX_ROUTE  = '/admin/rooms/export/docx';
const _ROOM_XLSX_ROUTE  = '/admin/rooms/export/xlsx';
const _ROOM_DATA_ROUTE  = '/admin/rooms/export/data';
const _ROOM_LIST_ROUTE  = '/admin/rooms/export/list';

let _roomExpBuildings   = [];
let _roomExpSelectedIds = new Set();
let _roomExpLoaded      = false;

function openRoomExportModal() {
    document.getElementById('roomExportModal').style.display = 'flex';
    if (!_roomExpLoaded) _loadRoomExpList();
    _updateRoomExpFooter();
}
function closeRoomExportModal() {
    document.getElementById('roomExportModal').style.display = 'none';
}

async function _loadRoomExpList() {
    try {
        const res = await fetch(_ROOM_LIST_ROUTE);
        if (!res.ok) throw new Error('Failed to load buildings');
        _roomExpBuildings = await res.json();
        _roomExpLoaded = true;
        _roomExpBuildings.forEach(b => _roomExpSelectedIds.add(b.buildingid));
        _renderRoomExpList();
        _updateRoomExpSelCount();
        _updateRoomExpFooter();
    } catch (e) {
        const el = document.getElementById('roomExpBldgList');
        if (el) el.innerHTML = `<div class="room-exp-bldg-empty">Error loading buildings: ${e.message}</div>`;
    }
}

function _filterRoomExpList() { _renderRoomExpList(); }

function _renderRoomExpList() {
    const search = (document.getElementById('roomExpSearch')?.value || '').toUpperCase();
    const listEl = document.getElementById('roomExpBldgList');
    if (!listEl) return;
    const matched = _roomExpBuildings.filter(b => !search || b.buildingname.toUpperCase().includes(search));
    if (!matched.length) {
        listEl.innerHTML = '<div class="room-exp-bldg-empty">No buildings match your search.</div>';
        return;
    }
    listEl.innerHTML = matched.map(b => {
        const isSel = _roomExpSelectedIds.has(b.buildingid);
        return `<div class="room-exp-bldg-item${isSel ? ' selected' : ''}" id="rb_${b.buildingid}"
             onclick="_toggleRoomBldg(${b.buildingid})">
          <input type="checkbox" ${isSel ? 'checked' : ''}
            onclick="event.stopPropagation();_toggleRoomBldg(${b.buildingid})">
          <span class="room-exp-bldg-name">${b.buildingname}</span>
          <span class="room-exp-bldg-count">${b.room_count} room${b.room_count !== 1 ? 's' : ''}</span>
        </div>`;
    }).join('');
    _updateRoomExpSelCount();
}

function _toggleRoomBldg(id) {
    _roomExpSelectedIds.has(id) ? _roomExpSelectedIds.delete(id) : _roomExpSelectedIds.add(id);
    _renderRoomExpList(); _updateRoomExpFooter();
}
function _toggleRoomExpSelectAll(checked) {
    _roomExpBuildings.forEach(b => checked ? _roomExpSelectedIds.add(b.buildingid) : _roomExpSelectedIds.delete(b.buildingid));
    _renderRoomExpList(); _updateRoomExpFooter();
}
function _updateRoomExpSelCount() {
    const n = _roomExpSelectedIds.size;
    const allCb = document.getElementById('roomExpSelectAll');
    if (allCb) {
        const total = _roomExpBuildings.length;
        allCb.checked       = n === total && total > 0;
        allCb.indeterminate = n > 0 && n < total;
    }
    const roomCount = _roomExpBuildings
        .filter(b => _roomExpSelectedIds.has(b.buildingid))
        .reduce((s, b) => s + (b.room_count || 0), 0);
    const selEl = document.getElementById('roomExpSelCount');  if (selEl) selEl.textContent = n;
    const rcEl  = document.getElementById('roomExpRoomCount'); if (rcEl)  rcEl.textContent  = roomCount;
}

function _roomToggleFmtCard(el) { el.classList.toggle('selected'); _syncRoomFmtAllBtn(); _updateRoomExpFooter(); }
function _roomToggleAllFmts() {
    const cards  = document.querySelectorAll('#roomExportModal .room-exp-fmt-card');
    const allSel = Array.from(cards).every(c => c.classList.contains('selected'));
    cards.forEach(c => allSel ? c.classList.remove('selected') : c.classList.add('selected'));
    _syncRoomFmtAllBtn(); _updateRoomExpFooter();
}
function _syncRoomFmtAllBtn() {
    const btn = document.getElementById('roomExpFmtAllBtn'); if (!btn) return;
    const allSel = Array.from(document.querySelectorAll('#roomExportModal .room-exp-fmt-card')).every(c => c.classList.contains('selected'));
    btn.textContent = allSel ? 'Deselect All Formats' : 'Select All Formats';
}

function _updateRoomExpFooter() {
    const fmts = Array.from(document.querySelectorAll('#roomExportModal .room-exp-fmt-card.selected')).map(c => c.dataset.format.toUpperCase());
    const n = _roomExpSelectedIds.size, nf = fmts.length;
    const btn = document.getElementById('roomExpBtnLabel');
    if (btn) btn.textContent = n > 0 ? `Export ${n} Building${n === 1 ? '' : 's'}` : 'Export';
    const sumEl = document.getElementById('roomExpSummaryText');
    if (sumEl) {
        const roomCount = _roomExpBuildings.filter(b => _roomExpSelectedIds.has(b.buildingid)).reduce((s, b) => s + (b.room_count || 0), 0);
        sumEl.innerHTML = (n === 0 || nf === 0)
            ? 'Select buildings and formats to see export summary'
            : `<strong>${n}</strong> building${n===1?'':'s'} &middot; <strong>${roomCount}</strong> rooms &times; <strong>${nf}</strong> format${nf===1?'':'s'} &rarr; <strong>${n*nf}</strong> file${n*nf===1?'':'s'} will be generated`;
    }
    const info = document.getElementById('roomExpFooterInfo');
    if (info) info.textContent = fmts.join(', ');
}

function _buildRoomFilename() {
    const raw = (document.getElementById('roomExpFilename')?.value.trim() || 'Rooms_Export').replace(/[\/\\:*?"<>|]/g, '_');
    if (document.getElementById('roomExpDateToggle')?.checked) {
        const n = new Date();
        return `${raw}_${n.getFullYear()}${String(n.getMonth()+1).padStart(2,'0')}${String(n.getDate()).padStart(2,'0')}_${String(n.getHours()).padStart(2,'0')}${String(n.getMinutes()).padStart(2,'0')}${String(n.getSeconds()).padStart(2,'0')}`;
    }
    return raw;
}

function _showRoomExpLoading(text, sub) {
    document.getElementById('rmExpLoadText').textContent = text || 'Exporting...';
    document.getElementById('rmExpLoadSub').textContent  = sub  || 'Please wait';
    document.getElementById('rmExpLoading').style.display = 'flex';
}
function _hideRoomExpLoading() { document.getElementById('rmExpLoading').style.display = 'none'; }
function _showRoomExpToast(type, title, msg) {
    const toast = document.getElementById('roomExpToast');
    toast.className = `room-exp-toast ${type}`;
    document.getElementById('roomExpToastIcon').innerHTML  = type === 'success' ? '<i class="fas fa-check-circle"></i>' : '<i class="fas fa-times-circle"></i>';
    document.getElementById('roomExpToastTitle').textContent = title;
    document.getElementById('roomExpToastMsg').textContent   = msg;
    toast.style.display = 'flex';
    setTimeout(() => { toast.style.display = 'none'; }, 6000);
}

function _roomExportCSV(buildings, filename) {
    const now = new Date().toLocaleString();
    const q   = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = [q('PUP LOPEZ CAMPUS — ROOMS AND BUILDINGS'), q(`Generated: ${now}`), ''];
    buildings.forEach(bldg => {
        lines.push(q(`Building: ${bldg.buildingname}`));
        lines.push(['Room Number', 'Room Type', 'Capacity'].map(q).join(','));
        (bldg.rooms || []).forEach(r => { lines.push([r.roomname, r.roomtype, r.roomcapacity].map(q).join(',')); });
        lines.push([q('TOTAL ROOMS'), q(''), (bldg.rooms || []).length].join(','));
        lines.push('');
    });
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.csv' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function _roomExportXLSX(buildings, filename) {
    const res = await fetch(_ROOM_XLSX_ROUTE, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ buildings, timestamp: 'Generated: ' + new Date().toLocaleString() }) });
    if (!res.ok) throw new Error('Server error: ' + (await res.text() || 'XLSX failed'));
    const blob = await res.blob();
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.xlsx' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function _roomExportPDF(buildings, filename) {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
    const W = 210, M = 10, CW = 190, HDR_H = 24, PAGE_H = 297;
    const now = new Date().toLocaleString();
    function drawBanner() {
        doc.setTextColor(0,0,0); doc.setFont('helvetica','bold'); doc.setFontSize(13);
        doc.text('PUP LOPEZ CAMPUS', W/2, 11, { align:'center' });
        doc.setFont('helvetica','normal'); doc.setFontSize(10);
        doc.text('ROOMS AND BUILDINGS', W/2, 18, { align:'center' });
        doc.setFontSize(7.5); doc.text('Generated: '+now, W/2, 24, { align:'center' });
    }
    drawBanner(); let y = HDR_H + 5;
    buildings.forEach((bldg, idx) => {
        const rooms = bldg.rooms || [];
        const minNeeded = 8 + 9 + Math.min(rooms.length,3)*8 + 8 + 5;
        if (idx > 0) { if (y + minNeeded > PAGE_H - 10) { doc.addPage(); drawBanner(); y = HDR_H+5; } else { y += 5; } }
        doc.setTextColor(0,0,0); doc.setFont('helvetica','bold'); doc.setFontSize(9);
        doc.text(bldg.buildingname.toUpperCase(), M, y+4);
        doc.setFont('helvetica','normal'); doc.setFontSize(7.5);
        doc.text(`Lecture: ${bldg.total_lecture}  |  Lab: ${bldg.total_lab}  |  Total: ${rooms.length}`, W-M, y+4, { align:'right' });
        y += 7;
        doc.autoTable({
            columns: [{ header:'Room Number',dataKey:'roomname' },{ header:'Room Type',dataKey:'roomtype' },{ header:'Capacity',dataKey:'roomcapacity' }],
            body: rooms.map(r => ({ ...r })), startY: y,
            styles:{ fontSize:8.5, cellPadding:2.5, lineColor:[0,0,0], lineWidth:0.2, textColor:[0,0,0] },
            headStyles:{ fillColor:[255,255,255], textColor:[0,0,0], fontStyle:'bold', fontSize:9, lineColor:[0,0,0], lineWidth:0.2 },
            alternateRowStyles:{ fillColor:[255,255,255] },
            columnStyles:{ 0:{ cellWidth:114 }, 1:{ cellWidth:50,halign:'center' }, 2:{ cellWidth:26,halign:'center' } },
            tableWidth: CW, foot:[['TOTAL ROOMS','',rooms.length]],
            footStyles:{ fillColor:[255,255,255], fontStyle:'bold', fontSize:9, textColor:[0,0,0], lineColor:[0,0,0], lineWidth:0.2 },
            showFoot:'lastPage', margin:{ top:HDR_H+4, left:M, right:M },
            didDrawPage: (d) => { if (d.pageNumber>1) { drawBanner(); } },
        });
        y = doc.lastAutoTable.finalY + 3;
    });
    const total = doc.internal.getNumberOfPages();
    for (let i=1;i<=total;i++) { doc.setPage(i); doc.setFontSize(7); doc.setTextColor(150,150,150); doc.text(`Page ${i} of ${total}`, W-M, PAGE_H-4, { align:'right' }); }
    doc.save(filename+'.pdf');
}

async function _roomExportDOCX(buildings, filename) {
    const res = await fetch(_ROOM_DOCX_ROUTE, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ buildings, timestamp:'Generated: '+new Date().toLocaleString() }) });
    if (!res.ok) throw new Error('Server error: '+(await res.text()||'DOCX failed'));
    const blob = await res.blob();
    const a = Object.assign(document.createElement('a'), { href:URL.createObjectURL(blob), download:filename+'.docx' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function roomExecuteExport() {
    const selectedFmts = Array.from(document.querySelectorAll('#roomExportModal .room-exp-fmt-card.selected')).map(c => c.dataset.format);
    const fmtErr = document.getElementById('roomExpFmtError');
    if (selectedFmts.length === 0) { if (fmtErr) fmtErr.style.display = 'block'; return; }
    if (fmtErr) fmtErr.style.display = 'none';
    if (_roomExpSelectedIds.size === 0) { _showRoomExpToast('error','No Buildings Selected','Select at least one building.'); return; }
    const filename = _buildRoomFilename();
    const ids      = Array.from(_roomExpSelectedIds);
    const btn      = document.getElementById('roomExpConfirmBtn');
    btn.disabled   = true;
    closeRoomExportModal();
    _showRoomExpLoading('Fetching Room Data','Loading room details from database...');
    let buildings;
    try {
        const res = await fetch(_ROOM_DATA_ROUTE, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ building_ids:ids }) });
        if (!res.ok) throw new Error(await res.text()||'Failed to fetch room data');
        buildings = await res.json();
    } catch(e) { _hideRoomExpLoading(); btn.disabled=false; _showRoomExpToast('error','Data Fetch Failed',e.message); return; }
    const errors = [];
    for (const fmt of selectedFmts) {
        _showRoomExpLoading(`Generating ${fmt.toUpperCase()}`,`Building ${fmt.toUpperCase()} file...`);
        try {
            if (fmt==='csv')  _roomExportCSV(buildings, filename);
            if (fmt==='xlsx') await _roomExportXLSX(buildings, filename);
            if (fmt==='pdf')  await _roomExportPDF(buildings, filename);
            if (fmt==='docx') await _roomExportDOCX(buildings, filename);
        } catch(e) { errors.push(`${fmt.toUpperCase()}: ${e.message}`); }
    }
    _hideRoomExpLoading(); btn.disabled=false;
    if (errors.length) _showRoomExpToast('error','Export Errors',errors.join(' | '));
    else { const n=selectedFmts.length; _showRoomExpToast('success','Export Complete',`${n} file${n===1?'':'s'} generated.`); }
}

/* ═══════════════════════════════════════════════════════════
   ROOM SCHEDULE EXPORT — Calendar View per room (AY + Semester only)
═══════════════════════════════════════════════════════════ */
const _ROOM_SCHED_COUNT_ROUTE  = '/admin/rooms/schedule-export/count';
const _ROOM_SCHED_EXPORT_ROUTE = '/admin/rooms/schedule-export';
let _roomSchedCountTmr = null;

function openRoomSchedExportModal() {
    document.querySelectorAll('#roomSchedExpModal .rse-ay-cb, #roomSchedExpModal .rse-sem-cb').forEach(cb => cb.checked = false);
    document.querySelectorAll('#roomSchedExpModal .room-exp-bldg-item').forEach(el => el.classList.remove('selected'));
    document.querySelectorAll('#roomSchedExpModal .room-exp-fmt-card').forEach(c => c.classList.remove('selected'));
    document.getElementById('roomSchedFilename').value = 'Room_Schedule_Export';
    document.getElementById('roomSchedCount').textContent = '0';
    document.getElementById('roomSchedRoomCount').textContent = '0';
    document.getElementById('roomSchedSummaryText').textContent = 'Select Academic Year, Semester, and formats to see export summary';
    document.getElementById('roomSchedFooterInfo').textContent = '';
    document.getElementById('roomSchedBtnLabel').textContent = 'Export';
    document.getElementById('roomSchedConfirmBtn').disabled = true;
    document.getElementById('roomSchedFmtError').style.display = 'none';
    document.getElementById('roomSchedExpModal').style.display = 'flex';
}
function closeRoomSchedExportModal() {
    document.getElementById('roomSchedExpModal').style.display = 'none';
}

function _toggleRoomSchedCb(itemEl) {
    const cb = itemEl.querySelector('input[type=checkbox]');
    cb.checked = !cb.checked;
    _onRoomSchedCbChange(cb);
}
function _onRoomSchedCbChange(cb) {
    cb.closest('.room-exp-bldg-item').classList.toggle('selected', cb.checked);
    _updateRoomSchedFooter();
    clearTimeout(_roomSchedCountTmr);
    _roomSchedCountTmr = setTimeout(_fetchRoomSchedCount, 400);
}
function _roomSchedToggleFmtCard(el) { el.classList.toggle('selected'); _updateRoomSchedFooter(); }

function _roomSchedFilters() {
    return {
        ay_ids:    Array.from(document.querySelectorAll('#roomSchedExpModal .rse-ay-cb:checked')).map(c => c.value),
        sem_types: Array.from(document.querySelectorAll('#roomSchedExpModal .rse-sem-cb:checked')).map(c => c.value),
    };
}

async function _fetchRoomSchedCount() {
    const f = _roomSchedFilters();
    if (!f.ay_ids.length || !f.sem_types.length) {
        document.getElementById('roomSchedCount').textContent = '0';
        document.getElementById('roomSchedRoomCount').textContent = '0';
        _updateRoomSchedFooter();
        return;
    }
    try {
        const res  = await fetch(_ROOM_SCHED_COUNT_ROUTE, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(f) });
        const data = await res.json();
        if (data.error) { document.getElementById('roomSchedSummaryText').textContent = 'Error: ' + data.error; return; }
        document.getElementById('roomSchedCount').textContent     = data.count || 0;
        document.getElementById('roomSchedRoomCount').textContent = data.rooms || 0;
    } catch (e) {
        document.getElementById('roomSchedSummaryText').textContent = 'Network error — check server connection.';
    }
    _updateRoomSchedFooter();
}

function _updateRoomSchedFooter() {
    const f     = _roomSchedFilters();
    const fmts  = Array.from(document.querySelectorAll('#roomSchedExpModal .room-exp-fmt-card.selected')).map(c => c.dataset.format.toUpperCase());
    const rooms = parseInt(document.getElementById('roomSchedRoomCount').textContent) || 0;
    const hasFilters = f.ay_ids.length > 0 && f.sem_types.length > 0;
    const ok = hasFilters && fmts.length > 0 && rooms > 0;

    document.getElementById('roomSchedConfirmBtn').disabled = !ok;
    const sumEl = document.getElementById('roomSchedSummaryText');
    if (!hasFilters) {
        sumEl.innerHTML = 'Select Academic Year, Semester, and formats to see export summary';
    } else if (rooms === 0) {
        sumEl.innerHTML = 'No scheduled rooms found for the selected Academic Year/Semester.';
    } else {
        sumEl.innerHTML = `<strong>${rooms}</strong> room${rooms===1?'':'s'} &times; <strong>${fmts.length}</strong> format${fmts.length===1?'':'s'} will be generated`;
    }
    const info = document.getElementById('roomSchedFooterInfo');
    if (info) info.textContent = fmts.join(', ');
    const btnLbl = document.getElementById('roomSchedBtnLabel');
    if (btnLbl) btnLbl.textContent = ok ? `Export ${rooms} Room${rooms===1?'':'s'}` : 'Export';
}

async function roomSchedExecuteExport() {
    const f      = _roomSchedFilters();
    const fmts   = Array.from(document.querySelectorAll('#roomSchedExpModal .room-exp-fmt-card.selected')).map(c => c.dataset.format);
    const fmtErr = document.getElementById('roomSchedFmtError');
    if (!fmts.length) { fmtErr.style.display = 'block'; return; }
    fmtErr.style.display = 'none';
    if (!f.ay_ids.length || !f.sem_types.length) {
        _showRoomExpToast('error', 'Missing Selection', 'Select at least one Academic Year and Semester.');
        return;
    }
    const filename = (document.getElementById('roomSchedFilename').value || 'Room_Schedule_Export').trim().replace(/[\/\\:*?"<>|]/g, '_');
    const btn = document.getElementById('roomSchedConfirmBtn');
    btn.disabled = true;

    const loadEl  = document.getElementById('roomSchedLoadingOverlay');
    const loadTxt = document.getElementById('roomSchedLoadingText');
    loadTxt.textContent = 'Generating room schedule export...';
    loadEl.style.display = 'flex';

    try {
        const resp = await fetch(_ROOM_SCHED_EXPORT_ROUTE, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ ...f, formats: fmts, filename }),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.error || resp.statusText);
        }
        const blob = await resp.blob();
        const ext  = fmts.length > 1 ? '.zip' : '.' + fmts[0];
        const a    = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + ext });
        a.click(); URL.revokeObjectURL(a.href);
        closeRoomSchedExportModal();
        _showRoomExpToast('success', 'Export Complete', 'Room schedule export downloaded.');
    } catch (e) {
        _showRoomExpToast('error', 'Export Failed', e.message);
    } finally {
        loadEl.style.display = 'none';
        btn.disabled = false;
    }
}