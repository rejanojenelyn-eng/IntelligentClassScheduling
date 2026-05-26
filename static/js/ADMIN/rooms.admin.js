let activeSidebarBuilding = "";

function openModal(id) { document.getElementById(id).style.display = 'flex'; }
function closeModal(id) { document.getElementById(id).style.display = 'none'; }

function toggleAvailabilityRow() {
    const row = document.getElementById('availabilityRow');
    const banner = document.getElementById('availableBanner');
    row.classList.toggle('active');
    banner.style.display = row.classList.contains('active') ? 'block' : 'none';
}

function showBuildingMenu(e, id, name) {
    e.preventDefault();
    const menu = document.getElementById('buildingMenu');
    menu.style.display = 'block';
    menu.style.left = e.pageX + 'px';
    menu.style.top = e.pageY + 'px';

    document.getElementById('ctxEditBldg').onclick = function () {
        document.getElementById('edit_bldg_id').value = id;
        document.getElementById('edit_bldg_name').value = name;
        openModal('modalEditBldg');
    };

    document.getElementById('ctxDeleteBldg').onclick = function () {
        document.getElementById('del_bldg_name_display').innerText = name;
        document.getElementById('confirmBldgDeleteLink').href = "/admin/delete_building/" + id;
        openModal('modalDeleteBldg');
    };
}

window.addEventListener('click', () => { document.getElementById('buildingMenu').style.display = 'none'; });

function openEditModal(id, name, type, capacity, bldgId) {
    document.getElementById('edit_room_id').value = id;
    document.getElementById('edit_room_name').value = name;
    document.getElementById('edit_room_type').value = type;
    document.getElementById('edit_room_capacity').value = capacity;
    document.getElementById('edit_room_bldg_id').value = bldgId;
    openModal('modalEditRoom');
}

function confirmDelete(id, name) {
    document.getElementById('del_room_name').innerText = name;
    document.getElementById('confirmDeleteLink').href = "/admin/delete_room/" + id;
    openModal('modalDeleteRoom');
}

function filterBySidebar(buildingName) {
    activeSidebarBuilding = buildingName.toUpperCase();
    let buttons = document.querySelectorAll('.btn-building');
    buttons.forEach(btn => btn.classList.remove('active'));
    if (window.event) window.event.target.classList.add('active');
    document.getElementById("filterBuilding").value = "";
    filterRoomTable();
}

function filterRoomTable() {
    let search = document.getElementById("searchRoom").value.toUpperCase();
    let bldgSelect = document.getElementById("filterBuilding").value.toUpperCase();
    let typeSelect = document.getElementById("filterType").value.toUpperCase();
    let buildingFilter = bldgSelect !== "" ? bldgSelect : activeSidebarBuilding;
    let tr = document.getElementById("roomTable").getElementsByTagName("tr");

    for (let i = 1; i < tr.length; i++) {
        let roomName = tr[i].querySelector(".td-room-name").textContent.toUpperCase();
        let roomType = tr[i].querySelector(".td-room-type").textContent.toUpperCase();
        let building = tr[i].querySelector(".td-building").textContent.toUpperCase();
        let matchSearch = roomName.indexOf(search) > -1;
        let matchType = typeSelect === "" || roomType === typeSelect;
        let matchBldg = buildingFilter === "" || building === buildingFilter;
        tr[i].style.display = (matchSearch && matchType && matchBldg) ? "" : "none";
    }
}

// ══ Room Export Engine ══════════════════════════════════════════════════════

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
    const matched = _roomExpBuildings.filter(b =>
        !search || b.buildingname.toUpperCase().includes(search));
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
    _renderRoomExpList();
    _updateRoomExpFooter();
}
function _toggleRoomExpSelectAll(checked) {
    _roomExpBuildings.forEach(b => checked ? _roomExpSelectedIds.add(b.buildingid) : _roomExpSelectedIds.delete(b.buildingid));
    _renderRoomExpList();
    _updateRoomExpFooter();
}
function _updateRoomExpSelCount() {
    const n  = _roomExpSelectedIds.size;
    const allCb = document.getElementById('roomExpSelectAll');
    if (allCb) {
        const total = _roomExpBuildings.length;
        allCb.checked       = n === total && total > 0;
        allCb.indeterminate = n > 0 && n < total;
    }
    const roomCount = _roomExpBuildings
        .filter(b => _roomExpSelectedIds.has(b.buildingid))
        .reduce((s, b) => s + (b.room_count || 0), 0);
    const selEl = document.getElementById('roomExpSelCount');
    if (selEl) selEl.textContent = n;
    const rcEl = document.getElementById('roomExpRoomCount');
    if (rcEl) rcEl.textContent = roomCount;
}

function _roomToggleFmtCard(el) {
    el.classList.toggle('selected');
    _syncRoomFmtAllBtn();
    _updateRoomExpFooter();
}
function _roomToggleAllFmts() {
    const cards  = document.querySelectorAll('#roomExportModal .room-exp-fmt-card');
    const allSel = Array.from(cards).every(c => c.classList.contains('selected'));
    cards.forEach(c => allSel ? c.classList.remove('selected') : c.classList.add('selected'));
    _syncRoomFmtAllBtn();
    _updateRoomExpFooter();
}
function _syncRoomFmtAllBtn() {
    const btn = document.getElementById('roomExpFmtAllBtn');
    if (!btn) return;
    const allSel = Array.from(document.querySelectorAll('#roomExportModal .room-exp-fmt-card'))
        .every(c => c.classList.contains('selected'));
    btn.textContent = allSel ? 'Deselect All Formats' : 'Select All Formats';
}

function _updateRoomExpFooter() {
    const fmts = Array.from(document.querySelectorAll('#roomExportModal .room-exp-fmt-card.selected'))
        .map(c => c.dataset.format.toUpperCase());
    const n  = _roomExpSelectedIds.size;
    const nf = fmts.length;
    const btn = document.getElementById('roomExpBtnLabel');
    if (btn) btn.textContent = n > 0 ? `Export ${n} Building${n === 1 ? '' : 's'}` : 'Export';
    const sumEl = document.getElementById('roomExpSummaryText');
    if (sumEl) {
        const roomCount = _roomExpBuildings
            .filter(b => _roomExpSelectedIds.has(b.buildingid))
            .reduce((s, b) => s + (b.room_count || 0), 0);
        sumEl.innerHTML = (n === 0 || nf === 0)
            ? 'Select buildings and formats to see export summary'
            : `<strong>${n}</strong> building${n===1?'':'s'} &middot; <strong>${roomCount}</strong> rooms &times; <strong>${nf}</strong> format${nf===1?'':'s'} &rarr; <strong>${n*nf}</strong> file${n*nf===1?'':'s'} will be generated`;
    }
    const info = document.getElementById('roomExpFooterInfo');
    if (info) info.textContent = fmts.join(', ');
}

function _buildRoomFilename() {
    const raw = (document.getElementById('roomExpFilename')?.value.trim() || 'Rooms_Export')
        .replace(/[\/\\:*?"<>|]/g, '_');
    if (document.getElementById('roomExpDateToggle')?.checked) {
        const n = new Date();
        const ts = n.getFullYear() + String(n.getMonth()+1).padStart(2,'0') + String(n.getDate()).padStart(2,'0')
            + '_' + String(n.getHours()).padStart(2,'0') + String(n.getMinutes()).padStart(2,'0') + String(n.getSeconds()).padStart(2,'0');
        return `${raw}_${ts}`;
    }
    return raw;
}

function _showRoomExpLoading(text, sub) {
    document.getElementById('roomExpLoadingText').textContent    = text || 'Exporting...';
    document.getElementById('roomExpLoadingSubText').textContent = sub  || 'Please wait';
    document.getElementById('roomExpLoadingOverlay').style.display = 'flex';
}
function _hideRoomExpLoading() {
    document.getElementById('roomExpLoadingOverlay').style.display = 'none';
}
function _showRoomExpToast(type, title, msg) {
    const toast = document.getElementById('roomExpToast');
    toast.className = `room-exp-toast ${type}`;
    document.getElementById('roomExpToastIcon').innerHTML = type === 'success'
        ? '<i class="fas fa-check-circle"></i>' : '<i class="fas fa-times-circle"></i>';
    document.getElementById('roomExpToastTitle').textContent = title;
    document.getElementById('roomExpToastMsg').textContent   = msg;
    toast.style.display = 'flex';
    setTimeout(() => { toast.style.display = 'none'; }, 6000);
}

// ── Generators ───────────────────────────────────────────────────────────────
function _roomExportCSV(buildings, filename) {
    const now = new Date().toLocaleString();
    const q   = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = [q('PUP LOPEZ CAMPUS — ROOMS AND BUILDINGS'), q(`Generated: ${now}`), ''];
    buildings.forEach(bldg => {
        lines.push(q(`Building: ${bldg.buildingname}`));
        lines.push(['Room Number', 'Room Type', 'Capacity'].map(q).join(','));
        (bldg.rooms || []).forEach(r => {
            lines.push([r.roomname, r.roomtype, r.roomcapacity].map(q).join(','));
        });
        lines.push([q('TOTAL ROOMS'), q(''), (bldg.rooms || []).length].join(','));
        lines.push('');
    });
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.csv' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function _roomExportXLSX(buildings, filename) {
    const res = await fetch(_ROOM_XLSX_ROUTE, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ buildings, timestamp: 'Generated: ' + new Date().toLocaleString() }),
    });
    if (!res.ok) throw new Error('Server error: ' + (await res.text() || 'XLSX failed'));
    const blob = await res.blob();
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.xlsx' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function _roomExportPDF(buildings, filename) {
    const { jsPDF } = window.jspdf;
    const doc   = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
    const W     = 210;
    const M     = 10;   // left/right margin
    const CW    = W - 2 * M;  // 190mm content width
    const HDR_H = 32;          // maroon banner height
    const PAGE_H = 297;
    const now   = new Date().toLocaleString();

    function drawBanner() {
        doc.setFillColor(128, 0, 0); doc.rect(0, 0, W, HDR_H, 'F');
        doc.setTextColor(255, 255, 255);
        doc.setFont('helvetica', 'bold');  doc.setFontSize(13);
        doc.text('PUP LOPEZ CAMPUS', W / 2, 11, { align: 'center' });
        doc.setFont('helvetica', 'normal'); doc.setFontSize(10);
        doc.text('ROOMS AND BUILDINGS', W / 2, 18, { align: 'center' });
        doc.setFontSize(7.5);
        doc.text('Generated: ' + now, W / 2, 26, { align: 'center' });
        doc.setTextColor(0, 0, 0);
    }

    drawBanner();
    let y = HDR_H + 5;

    buildings.forEach((bldg, idx) => {
        const rooms = bldg.rooms || [];
        // Estimate: bar(8) + table-header(9) + rows(~8 each) + foot(8) + gap(5)
        const minNeeded = 8 + 9 + Math.min(rooms.length, 3) * 8 + 8 + 5;

        if (idx > 0) {
            if (y + minNeeded > PAGE_H - 10) {
                doc.addPage(); drawBanner(); y = HDR_H + 5;
            } else {
                y += 5; // gap between buildings on the same page
            }
        }

        // Building heading bar — fills full content width
        doc.setFillColor(55, 55, 55); doc.rect(M, y, CW, 8, 'F');
        doc.setTextColor(255, 255, 255);
        doc.setFont('helvetica', 'bold'); doc.setFontSize(9);
        doc.text(bldg.buildingname.toUpperCase(), M + 4, y + 5.5);
        const info = `Lecture: ${bldg.total_lecture}  |  Lab: ${bldg.total_lab}  |  Total: ${rooms.length}`;
        doc.setFont('helvetica', 'normal'); doc.setFontSize(7.5);
        doc.text(info, W - M - 3, y + 5.5, { align: 'right' });
        doc.setTextColor(0, 0, 0);
        y += 9;

        doc.autoTable({
            columns: [
                { header: '#',           dataKey: '_num'         },
                { header: 'Room Number', dataKey: 'roomname'     },
                { header: 'Room Type',   dataKey: 'roomtype'     },
                { header: 'Capacity',    dataKey: 'roomcapacity' },
            ],
            body: rooms.map((r, i) => ({ ...r, _num: i + 1 })),
            startY: y,
            styles:            { fontSize: 8.5, cellPadding: 2.5 },
            headStyles:        { fillColor: [128,0,0], textColor: 255, fontStyle: 'bold', fontSize: 9 },
            alternateRowStyles:{ fillColor: [253,245,245] },
            columnStyles: {
                0: { cellWidth: 12,  halign: 'center' },
                1: { cellWidth: 102 },
                2: { cellWidth: 50,  halign: 'center' },
                3: { cellWidth: 26,  halign: 'center' },
            },
            tableWidth: CW,
            foot:       [['', 'TOTAL ROOMS', '', rooms.length]],
            footStyles: { fillColor: [240,230,230], fontStyle: 'bold', fontSize: 9, textColor: [80,0,0] },
            showFoot: 'lastPage',
            margin: { top: HDR_H + 4, left: M, right: M },
            didDrawPage: (data) => {
                if (data.pageNumber > 1) { drawBanner(); }
            },
        });
        y = doc.lastAutoTable.finalY + 3;
    });

    const total = doc.internal.getNumberOfPages();
    for (let i = 1; i <= total; i++) {
        doc.setPage(i); doc.setFontSize(7); doc.setTextColor(150, 150, 150);
        doc.text(`Page ${i} of ${total}`, W - M, PAGE_H - 4, { align: 'right' });
    }
    doc.save(filename + '.pdf');
}

async function _roomExportDOCX(buildings, filename) {
    const res = await fetch(_ROOM_DOCX_ROUTE, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ buildings, timestamp: 'Generated: ' + new Date().toLocaleString() }),
    });
    if (!res.ok) throw new Error('Server error: ' + (await res.text() || 'DOCX failed'));
    const blob = await res.blob();
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.docx' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function roomExecuteExport() {
    const selectedFmts = Array.from(document.querySelectorAll('#roomExportModal .room-exp-fmt-card.selected'))
        .map(c => c.dataset.format);
    const fmtErr = document.getElementById('roomExpFmtError');
    if (selectedFmts.length === 0) { if (fmtErr) fmtErr.style.display = 'block'; return; }
    if (fmtErr) fmtErr.style.display = 'none';
    if (_roomExpSelectedIds.size === 0) {
        _showRoomExpToast('error', 'No Buildings Selected', 'Please select at least one building to export.');
        return;
    }
    const filename = _buildRoomFilename();
    const ids      = Array.from(_roomExpSelectedIds);
    const btn      = document.getElementById('roomExpConfirmBtn');
    btn.disabled   = true;
    closeRoomExportModal();
    _showRoomExpLoading('Fetching Room Data', 'Loading room details from database...');
    let buildings;
    try {
        const res = await fetch(_ROOM_DATA_ROUTE, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ building_ids: ids }),
        });
        if (!res.ok) throw new Error(await res.text() || 'Failed to fetch room data');
        buildings = await res.json();
    } catch (e) {
        _hideRoomExpLoading(); btn.disabled = false;
        _showRoomExpToast('error', 'Data Fetch Failed', e.message); return;
    }
    const errors = [];
    for (const fmt of selectedFmts) {
        _showRoomExpLoading(`Generating ${fmt.toUpperCase()}`, `Building ${fmt.toUpperCase()} file...`);
        try {
            if (fmt === 'csv')  _roomExportCSV(buildings, filename);
            if (fmt === 'xlsx') await _roomExportXLSX(buildings, filename);
            if (fmt === 'pdf')  await _roomExportPDF(buildings, filename);
            if (fmt === 'docx') await _roomExportDOCX(buildings, filename);
        } catch (e) { errors.push(`${fmt.toUpperCase()}: ${e.message}`); }
    }
    _hideRoomExpLoading();
    btn.disabled = false;
    if (errors.length) {
        _showRoomExpToast('error', 'Export Errors', errors.join(' | '));
    } else {
        const n = selectedFmts.length;
        _showRoomExpToast('success', 'Export Complete', `${n} file${n===1?'':'s'} generated successfully.`);
    }
}
