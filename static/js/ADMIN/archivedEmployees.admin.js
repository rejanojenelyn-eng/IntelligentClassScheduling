// ── Export routes ─────────────────────────────────────────────────────────────
const _ARCH_DOCX_ROUTE = '/admin/archived-employee/export/docx';
const _ARCH_XLSX_ROUTE = '/admin/archived-employee/export/xlsx';

// ── Selection UI ──────────────────────────────────────────────────────────────
function _updateSelectionUI() {
    const n = document.querySelectorAll('.row-check:checked').length;
    const bar = document.getElementById('selectionBar');
    const countEl = document.getElementById('selectedCount');
    if (bar) bar.style.display = n > 0 ? 'flex' : 'none';
    if (countEl) countEl.textContent = n;
}

document.getElementById('selectAll').addEventListener('change', function () {
    document.querySelectorAll('#archiveTable tbody tr').forEach(tr => {
        if (tr.style.display !== 'none') {
            const cb = tr.querySelector('.row-check');
            if (cb) cb.checked = this.checked;
        }
    });
    _updateSelectionUI();
});

document.querySelector('#archiveTable tbody').addEventListener('change', function (e) {
    if (!e.target.classList.contains('row-check')) return;
    _updateSelectionUI();
    if (!e.target.checked) {
        document.getElementById('selectAll').checked = false;
    } else {
        const allVisible = Array.from(document.querySelectorAll('#archiveTable tbody tr'))
            .filter(tr => tr.style.display !== 'none')
            .map(tr => tr.querySelector('.row-check'))
            .filter(Boolean);
        if (allVisible.every(cb => cb.checked)) document.getElementById('selectAll').checked = true;
    }
});

function deselectAll() {
    document.querySelectorAll('.row-check').forEach(cb => cb.checked = false);
    const sa = document.getElementById('selectAll');
    if (sa) sa.checked = false;
    _updateSelectionUI();
}

// ── Restore modals ────────────────────────────────────────────────────────────
function openRestoreModal(archiveId, name) {
    document.getElementById('restoreEmployeeName').textContent = name;
    document.getElementById('confirmRestoreBtn').href = `/admin/restore_employee/${archiveId}`;
    document.getElementById('restoreConfirmModal').style.display = 'block';
}
function closeRestoreModal() { document.getElementById('restoreConfirmModal').style.display = 'none'; }

function openBulkRestoreModal() {
    const ids = Array.from(document.querySelectorAll('.row-check:checked')).map(cb => cb.value);
    document.getElementById('bulkRestoreCount').textContent = ids.length;
    document.getElementById('bulkRestoreConfirmModal').style.display = 'block';
}
function closeBulkRestoreModal() { document.getElementById('bulkRestoreConfirmModal').style.display = 'none'; }

window.onclick = function (e) {
    ['restoreConfirmModal', 'bulkRestoreConfirmModal'].forEach(id => {
        const el = document.getElementById(id);
        if (el && e.target === el) el.style.display = 'none';
    });
};

document.getElementById('confirmBulkRestoreBtn').addEventListener('click', function () {
    const ids = Array.from(document.querySelectorAll('.row-check:checked')).map(cb => cb.value);
    fetch('/admin/bulk_restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ archive_ids: ids }),
    }).then(r => r.json()).then(data => {
        if (data.success) window.location.reload();
        else alert('An error occurred while restoring employees.');
    });
});

// ── Filtering ─────────────────────────────────────────────────────────────────
function filterTable() {
    const name   = document.getElementById('searchInput').value.toUpperCase();
    const type   = document.getElementById('typeFilter').value.toUpperCase();
    const status = document.getElementById('statusFilter').value.toUpperCase();
    const spec   = document.getElementById('specFilter').value.toUpperCase();

    document.querySelectorAll('#archiveTable tbody tr').forEach(row => {
        const matchName   = (row.querySelector('.emp-name')?.textContent   || '').toUpperCase().includes(name);
        const matchType   = !type   || (row.querySelector('.emp-type')?.textContent   || '').toUpperCase().trim().includes(type);
        const matchStatus = !status || (row.querySelector('.emp-status')?.textContent || '').toUpperCase().trim() === status;
        const matchSpec   = !spec   || (row.querySelector('.emp-spec')?.textContent   || '').toUpperCase().trim().includes(spec);
        const show = matchName && matchType && matchStatus && matchSpec;
        row.style.display = show ? '' : 'none';
        if (!show) {
            const cb = row.querySelector('.row-check');
            if (cb) cb.checked = false;
        }
    });
    _updateSelectionUI();
}

// ── Sorting ───────────────────────────────────────────────────────────────────
let _sortAsc = true;

function sortTable() {
    const tbody = document.querySelector('#archiveTable tbody');
    const rows  = Array.from(tbody.querySelectorAll('tr'));
    rows.sort((a, b) => {
        const na = (a.querySelector('.emp-name')?.textContent || '').trim().toLowerCase();
        const nb = (b.querySelector('.emp-name')?.textContent || '').trim().toLowerCase();
        return _sortAsc ? na.localeCompare(nb) : nb.localeCompare(na);
    });
    _sortAsc = !_sortAsc;
    rows.forEach(r => tbody.appendChild(r));
}

// ── Export data helpers ───────────────────────────────────────────────────────
function _getArchiveExportRows() {
    const checked = Array.from(document.querySelectorAll('.row-check:checked'));
    if (checked.length > 0) return checked.map(cb => cb.closest('tr'));
    return Array.from(document.querySelectorAll('#archiveTable tbody tr')).filter(r => r.style.display !== 'none');
}

function _rowsToArchiveData(rows) {
    return rows.map(row => ({
        name:          (row.querySelector('.emp-name')?.innerText     || '').trim(),
        spec:          (row.querySelector('.emp-spec')?.innerText     || '').trim(),
        email:         (row.querySelector('.emp-email')?.innerText    || '').trim(),
        contact:       (row.querySelector('.emp-contact')?.innerText  || '').trim(),
        type:          (row.querySelector('.emp-type')?.innerText     || '').trim(),
        status:        (row.querySelector('.emp-status')?.innerText   || '').trim(),
        date_archived: (row.querySelector('.date-archived')?.innerText|| '').trim(),
    }));
}

function _buildArchiveExportFilename() {
    const raw = (document.getElementById('exportFilenameInput').value.trim() || 'Archived_Employees')
        .replace(/[\/\\:*?"<>|]/g, '_');
    if (document.getElementById('exportDateToggle').checked) {
        const n = new Date();
        const ts = n.getFullYear()
            + String(n.getMonth() + 1).padStart(2, '0')
            + String(n.getDate()).padStart(2, '0')
            + '_'
            + String(n.getHours()).padStart(2, '0')
            + String(n.getMinutes()).padStart(2, '0')
            + String(n.getSeconds()).padStart(2, '0');
        return `${raw}_${ts}`;
    }
    return raw;
}

// ── Export modal ──────────────────────────────────────────────────────────────
function openExportModal() {
    const rows = _getArchiveExportRows();
    const checked = document.querySelectorAll('.row-check:checked');
    document.getElementById('exportEmpCount').textContent = rows.length;
    document.getElementById('exportScopeLabel').textContent = checked.length > 0 ? 'Selected employees' : 'All visible employees';
    document.getElementById('exportFormatError').style.display = 'none';
    document.getElementById('exportModal').style.display = 'flex';
}
function closeExportModal() { document.getElementById('exportModal').style.display = 'none'; }

function toggleFormatCard(el) {
    el.classList.toggle('selected');
    const allSel = Array.from(document.querySelectorAll('.emp-export-format-card')).every(c => c.classList.contains('selected'));
    const btn = document.getElementById('selectAllFormatsBtn');
    btn.textContent = allSel ? 'Deselect All' : 'Select All';
    btn.classList.toggle('all-selected', allSel);
}

function toggleSelectAllFormats() {
    const cards = document.querySelectorAll('.emp-export-format-card');
    const allSel = Array.from(cards).every(c => c.classList.contains('selected'));
    cards.forEach(c => allSel ? c.classList.remove('selected') : c.classList.add('selected'));
    const btn = document.getElementById('selectAllFormatsBtn');
    btn.textContent = !allSel ? 'Deselect All' : 'Select All';
    btn.classList.toggle('all-selected', !allSel);
}

function _showExportLoading(text, sub) {
    document.getElementById('exportLoadingText').textContent    = text || 'Exporting...';
    document.getElementById('exportLoadingSubText').textContent = sub  || 'Please wait';
    document.getElementById('exportLoadingOverlay').style.display = 'flex';
}
function _hideExportLoading() { document.getElementById('exportLoadingOverlay').style.display = 'none'; }

function _showExportToast(type, title, msg) {
    const toast = document.getElementById('exportToast');
    toast.className = `emp-export-toast ${type}`;
    document.getElementById('exportToastIcon').innerHTML = type === 'success'
        ? '<i class="fas fa-check-circle"></i>' : '<i class="fas fa-times-circle"></i>';
    document.getElementById('exportToastTitle').textContent = title;
    document.getElementById('exportToastMsg').textContent   = msg;
    toast.style.display = 'flex';
    setTimeout(() => { toast.style.display = 'none'; }, 5000);
}
function closeExportToast() { document.getElementById('exportToast').style.display = 'none'; }

// ── Format exporters ──────────────────────────────────────────────────────────
function _exportCSV(data, filename) {
    const now = new Date().toLocaleString();
    const headers = ['Employee Name', 'Specialization', 'Email', 'Contact', 'Employment Type', 'Status', 'Date Archived'];
    const q = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = [
        `"Archived Employee Records"`,
        `"Generated: ${now}"`,
        `"Total: ${data.length} record(s)"`,
        '',
        headers.map(q).join(','),
        ...data.map(e => [e.name, e.spec, e.email, e.contact, e.type, e.status, e.date_archived].map(q).join(',')),
    ];
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.csv' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function _exportXLSX(data, filename) {
    const payload = {
        employees: data,
        title: 'Archived Employee Records',
        timestamp: 'Generated: ' + new Date().toLocaleString() + '  |  Total: ' + data.length + ' record(s)',
    };
    const res = await fetch(_ARCH_XLSX_ROUTE, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await res.text() || 'Server error generating XLSX');
    const blob = await res.blob();
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.xlsx' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function _exportPDF(data, filename) {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4' });
    const now = new Date().toLocaleString();

    doc.setFillColor(128, 0, 0);
    doc.rect(0, 0, 297, 32, 'F');
    doc.setTextColor(255, 255, 255);
    doc.setFont('helvetica', 'bold'); doc.setFontSize(17);
    doc.text('Archived Employee Records', 148.5, 16, { align: 'center' });
    doc.setFont('helvetica', 'normal'); doc.setFontSize(9);
    doc.text('Generated: ' + now, 148.5, 25, { align: 'center' });
    doc.setTextColor(0, 0, 0);

    doc.autoTable({
        columns: [
            { header: '#',              dataKey: 'no'           },
            { header: 'Employee Name',  dataKey: 'name'         },
            { header: 'Specialization', dataKey: 'spec'         },
            { header: 'Email',          dataKey: 'email'        },
            { header: 'Contact',        dataKey: 'contact'      },
            { header: 'Type',           dataKey: 'type'         },
            { header: 'Status',         dataKey: 'status'       },
            { header: 'Date Archived',  dataKey: 'date_archived'},
        ],
        body: data.map((e, i) => ({ no: i + 1, ...e })),
        startY: 37,
        styles: { fontSize: 8, cellPadding: 2.5, overflow: 'linebreak' },
        headStyles: { fillColor: [128, 0, 0], textColor: 255, fontStyle: 'bold' },
        alternateRowStyles: { fillColor: [253, 245, 245] },
        margin: { left: 10, right: 10 },
    });

    const total = doc.internal.getNumberOfPages();
    for (let i = 1; i <= total; i++) {
        doc.setPage(i);
        doc.setFontSize(8); doc.setTextColor(150, 150, 150);
        doc.text(`Page ${i} of ${total}`, 287, 205, { align: 'right' });
    }
    doc.save(filename + '.pdf');
}

async function _exportDOCX(data, filename) {
    const payload = {
        employees: data,
        title: 'Archived Employee Records',
        timestamp: 'Generated: ' + new Date().toLocaleString(),
    };
    const res = await fetch(_ARCH_DOCX_ROUTE, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await res.text() || 'Server error generating DOCX');
    const blob = await res.blob();
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.docx' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function executeExport() {
    const selected = Array.from(document.querySelectorAll('.emp-export-format-card.selected')).map(c => c.dataset.format);
    if (selected.length === 0) {
        document.getElementById('exportFormatError').style.display = 'block'; return;
    }
    document.getElementById('exportFormatError').style.display = 'none';

    const rows = _getArchiveExportRows();
    if (rows.length === 0) {
        _showExportToast('error', 'No Data', 'There are no records to export.'); return;
    }
    const data     = _rowsToArchiveData(rows);
    const filename = _buildArchiveExportFilename();
    const btn      = document.getElementById('exportConfirmBtn');
    btn.disabled   = true;
    closeExportModal();
    _showExportLoading('Preparing Export', `Generating ${selected.length} file(s)…`);

    const errors = [];
    for (const fmt of selected) {
        _showExportLoading(`Exporting ${fmt.toUpperCase()}`, `Processing ${data.length} records…`);
        try {
            if (fmt === 'csv')  _exportCSV(data, filename);
            if (fmt === 'xlsx') await _exportXLSX(data, filename);
            if (fmt === 'pdf')  await _exportPDF(data, filename);
            if (fmt === 'docx') await _exportDOCX(data, filename);
            await new Promise(r => setTimeout(r, 400));
        } catch (e) { errors.push(fmt.toUpperCase() + ': ' + e.message); }
    }

    _hideExportLoading();
    btn.disabled = false;
    if (errors.length === 0) {
        _showExportToast('success', 'Export Complete', `${selected.length} file(s) downloaded successfully.`);
    } else if (errors.length < selected.length) {
        _showExportToast('error', 'Partial Export', 'Some files failed: ' + errors.join('; '));
    } else {
        _showExportToast('error', 'Export Failed', errors.join('; '));
    }
}
