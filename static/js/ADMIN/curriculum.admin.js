function filterAssignments() {
    const currFilter = document.getElementById('filterAssignCurr').value.toLowerCase();
    const progFilter = document.getElementById('filterAssignProg').value.toLowerCase();
    const rows = document.querySelectorAll('#assignmentTable tbody tr');

    rows.forEach(row => {
        const currText = row.cells[0].textContent.toLowerCase().trim();
        const progText = row.cells[1].textContent.toLowerCase().trim();
        const matchesCurr = (currFilter === 'all' || currText === currFilter);
        const matchesProg = (progFilter === 'all' || progText === progFilter);
        row.style.display = (matchesCurr && matchesProg) ? '' : 'none';
    });
}

function editAssignment(id, prog, curr, year, sec) {
    document.getElementById('assignModalTitle').innerHTML = '<i class="fas fa-edit"></i> Edit Assignment';
    document.getElementById('assignCohortId').value = id;
    document.getElementById('hiddenProgramCode').value = prog;
    document.getElementById('hiddenStartYear').value = year;
    document.getElementById('assignStartYear').value = year;
    document.getElementById('assignProgramCode').value = prog;

    const select = document.getElementById('assignCurriculumId');
    select.querySelectorAll('option').forEach(opt => {
        if (opt.value === "") return;
        const match = opt.getAttribute('data-program') === prog;
        opt.style.display = match ? 'block' : 'none';
        opt.disabled = !match;
    });

    select.value = curr;
    document.getElementById('assignSections').value = sec;
    document.getElementById('assignModal').style.display = 'flex';
}

function closeAssignModal() { document.getElementById('assignModal').style.display = 'none'; }

function handleProgramExport() { openCurrExportModal(); }
function closeExportWarning() { document.getElementById('exportWarningModal').style.display = 'none'; }

// ── CURRICULUM EXPORT ENGINE ──────────────────────────────────────────────────
const _CURR_DOCX_ROUTE = '/admin/curriculum/export/docx';
const _CURR_XLSX_ROUTE = '/admin/curriculum/export/xlsx';
const _CURR_DATA_ROUTE = '/admin/curriculum/export/data';
const _CURR_LIST_ROUTE = '/admin/curriculum/export/list';

let _currExpPrograms   = [];
let _currExpSelectedIds = new Set();
let _currExpLoaded     = false;

// ── Modal open / close ────────────────────────────────────────────────────────
async function openCurrExportModal() {
    document.getElementById('currExportModal').style.display = 'flex';
    _updateCurrExpFooter();
    if (!_currExpLoaded) {
        await _loadCurrExpList();
    } else {
        _renderCurrExpList();
    }
}
function closeCurrExportModal() { document.getElementById('currExportModal').style.display = 'none'; }

// ── Load curriculum list from server ─────────────────────────────────────────
async function _loadCurrExpList() {
    const listEl = document.getElementById('currExpList');
    listEl.innerHTML = '<div class="curr-exp-list-loading"><i class="fas fa-spinner fa-spin"></i>&nbsp;Loading curricula...</div>';
    try {
        const res = await fetch(_CURR_LIST_ROUTE);
        if (!res.ok) throw new Error('Server error');
        _currExpPrograms = await res.json();
        _currExpLoaded   = true;
        _renderCurrExpList();
    } catch (e) {
        listEl.innerHTML = `<div class="curr-exp-list-empty"><i class="fas fa-exclamation-triangle" style="color:#c00;margin-right:6px;"></i>Failed to load: ${e.message}</div>`;
    }
}

// ── Render curriculum list ────────────────────────────────────────────────────
function _renderCurrExpList() {
    const query  = (document.getElementById('currExpSearch')?.value || '').toLowerCase().trim();
    const listEl = document.getElementById('currExpList');
    if (!_currExpPrograms.length) {
        listEl.innerHTML = '<div class="curr-exp-list-empty">No curricula found.</div>';
        return;
    }
    let html = ''; let anyVisible = false;
    _currExpPrograms.forEach(prog => {
        const matched = prog.curricula.filter(c =>
            !query ||
            c.curriculum_code.toLowerCase().includes(query) ||
            c.curriculum_year.toLowerCase().includes(query) ||
            prog.program_name.toLowerCase().includes(query) ||
            prog.program_code.toLowerCase().includes(query)
        );
        if (!matched.length) return;
        anyVisible = true;
        const selCount = matched.filter(c => _currExpSelectedIds.has(c.curriculum_id)).length;
        const allSel   = selCount === matched.length;
        const someSel  = selCount > 0 && !allSel;
        html += `
        <div class="curr-exp-prog-group" id="pg_${prog.program_code}">
          <div class="curr-exp-prog-header" onclick="_toggleCurrProgCollapse('${prog.program_code}')">
            <i class="fas fa-chevron-down curr-exp-prog-toggle" id="pg_tog_${prog.program_code}"></i>
            <input type="checkbox" class="curr-exp-prog-cb" id="pgcb_${prog.program_code}"
              ${allSel ? 'checked' : ''} ${someSel ? 'data-indet="1"' : ''}
              onclick="event.stopPropagation();_toggleProgSel('${prog.program_code}',this.checked)"
              title="Select all ${prog.program_name}">
            <span class="curr-exp-prog-name">${prog.program_name}</span>
            <span class="curr-exp-prog-code">${prog.program_code}</span>
            <span class="curr-exp-prog-count">${selCount}/${matched.length}</span>
          </div>
          <div class="curr-exp-prog-items" id="pg_items_${prog.program_code}">`;
        matched.forEach(c => {
            const isSel = _currExpSelectedIds.has(c.curriculum_id);
            html += `
            <div class="curr-exp-curr-item${isSel ? ' selected' : ''}" id="ci_${c.curriculum_id}"
                 onclick="_toggleCurrItem(${c.curriculum_id},'${prog.program_code}')">
              <input type="checkbox" class="curr-exp-curr-cb" id="cb_${c.curriculum_id}"
                ${isSel ? 'checked' : ''}
                onclick="event.stopPropagation();_toggleCurrItem(${c.curriculum_id},'${prog.program_code}')">
              <span class="curr-exp-curr-label">C.Y ${c.curriculum_year}</span>
              <span class="curr-exp-curr-code">${c.curriculum_code}</span>
            </div>`;
        });
        html += `</div></div>`;
    });
    if (!anyVisible) {
        listEl.innerHTML = '<div class="curr-exp-list-empty">No curricula match your search.</div>';
        return;
    }
    listEl.innerHTML = html;
    _currExpPrograms.forEach(prog => {
        const cb = document.getElementById(`pgcb_${prog.program_code}`);
        if (cb && cb.dataset.indet === '1') cb.indeterminate = true;
    });
    _updateCurrExpSelCount();
}

// ── Toggle program group collapse ─────────────────────────────────────────────
function _toggleCurrProgCollapse(progCode) {
    const grp = document.getElementById(`pg_${progCode}`);
    const tog = document.getElementById(`pg_tog_${progCode}`);
    if (!grp) return;
    grp.classList.toggle('collapsed');
    if (tog) tog.style.transform = grp.classList.contains('collapsed') ? 'rotate(-90deg)' : '';
}

// ── Selection handlers ────────────────────────────────────────────────────────
function _toggleCurrItem(id, progCode) {
    _currExpSelectedIds.has(id) ? _currExpSelectedIds.delete(id) : _currExpSelectedIds.add(id);
    const itemEl = document.getElementById(`ci_${id}`);
    const cbEl   = document.getElementById(`cb_${id}`);
    if (itemEl) itemEl.classList.toggle('selected', _currExpSelectedIds.has(id));
    if (cbEl)   cbEl.checked = _currExpSelectedIds.has(id);
    _updateProgCheckbox(progCode);
    _updateCurrExpSelCount();
    _updateCurrExpFooter();
}
function _toggleProgSel(progCode, checked) {
    const prog = _currExpPrograms.find(p => p.program_code === progCode);
    if (!prog) return;
    prog.curricula.forEach(c => checked ? _currExpSelectedIds.add(c.curriculum_id) : _currExpSelectedIds.delete(c.curriculum_id));
    _renderCurrExpList(); _updateCurrExpFooter();
}
function _toggleCurrExpSelectAll(checked) {
    _currExpPrograms.forEach(prog =>
        prog.curricula.forEach(c => checked ? _currExpSelectedIds.add(c.curriculum_id) : _currExpSelectedIds.delete(c.curriculum_id))
    );
    _renderCurrExpList(); _updateCurrExpFooter();
}
function _filterCurrExpList() { _renderCurrExpList(); }
function _updateProgCheckbox(progCode) {
    const prog = _currExpPrograms.find(p => p.program_code === progCode);
    const cb   = document.getElementById(`pgcb_${progCode}`);
    if (!prog || !cb) return;
    const n = prog.curricula.filter(c => _currExpSelectedIds.has(c.curriculum_id)).length;
    cb.checked       = n === prog.curricula.length;
    cb.indeterminate = n > 0 && n < prog.curricula.length;
}
function _updateCurrExpSelCount() {
    const countEl = document.getElementById('currExpSelCount');
    const n = _currExpSelectedIds.size;
    if (countEl) countEl.textContent = `${n} selected`;
    const allCb = document.getElementById('currExpSelectAll');
    if (allCb) {
        const total = _currExpPrograms.reduce((s, p) => s + p.curricula.length, 0);
        allCb.checked       = n === total && total > 0;
        allCb.indeterminate = n > 0 && n < total;
    }
}

// ── Format card toggling ──────────────────────────────────────────────────────
function _currToggleFmtCard(el) {
    el.classList.toggle('selected');
    _syncFmtAllBtn(); _updateCurrExpFooter();
}
function _currToggleAllFmts() {
    const cards  = document.querySelectorAll('#currExportModal .curr-exp-format-card');
    const allSel = Array.from(cards).every(c => c.classList.contains('selected'));
    cards.forEach(c => allSel ? c.classList.remove('selected') : c.classList.add('selected'));
    _syncFmtAllBtn(); _updateCurrExpFooter();
}
function _syncFmtAllBtn() {
    const btn  = document.getElementById('currExpFmtAllBtn');
    if (!btn) return;
    const allSel = Array.from(document.querySelectorAll('#currExportModal .curr-exp-format-card'))
        .every(c => c.classList.contains('selected'));
    btn.textContent = allSel ? 'Deselect All Formats' : 'Select All Formats';
}

// ── Summary / footer ──────────────────────────────────────────────────────────
function _updateCurrExpFooter() {
    const fmts = Array.from(document.querySelectorAll('#currExportModal .curr-exp-format-card.selected'))
        .map(c => c.dataset.format.toUpperCase());
    const n = _currExpSelectedIds.size; const nf = fmts.length;
    const btn = document.getElementById('currExpBtnLabel');
    if (btn) btn.textContent = n > 0 ? `Export ${n} Curricul${n === 1 ? 'um' : 'a'}` : 'Export';
    const sumEl = document.getElementById('currExpSummaryText');
    if (sumEl) {
        sumEl.innerHTML = (n === 0 || nf === 0)
            ? 'Select curricula and formats to see export summary'
            : `<strong>${n}</strong> curricul${n===1?'um':'a'} &times; <strong>${nf}</strong> format${nf===1?'':'s'} &rarr; <strong>${n*nf}</strong> file${n*nf===1?'':'s'} will be generated`;
    }
    const info = document.getElementById('currExpFooterInfo');
    if (info) info.textContent = fmts.join(', ');
}

// ── Filename builder ──────────────────────────────────────────────────────────
function _buildCurrFilename() {
    const raw = (document.getElementById('currExpFilename')?.value.trim() || 'Curriculum_Export')
        .replace(/[\/\\:*?"<>|]/g, '_');
    if (document.getElementById('currExpDateToggle')?.checked) {
        const n = new Date();
        const ts = n.getFullYear() + String(n.getMonth()+1).padStart(2,'0') + String(n.getDate()).padStart(2,'0')
            + '_' + String(n.getHours()).padStart(2,'0') + String(n.getMinutes()).padStart(2,'0') + String(n.getSeconds()).padStart(2,'0');
        return `${raw}_${ts}`;
    }
    return raw;
}

// ── Loading & toast ───────────────────────────────────────────────────────────
function _showCurrExpLoading(text, sub) {
    document.getElementById('currExpLoadingText').textContent    = text || 'Exporting...';
    document.getElementById('currExpLoadingSubText').textContent = sub  || 'Please wait';
    document.getElementById('currExpLoadingOverlay').style.display = 'flex';
}
function _hideCurrExpLoading() { document.getElementById('currExpLoadingOverlay').style.display = 'none'; }
function _showCurrExpToast(type, title, msg) {
    const toast = document.getElementById('currExpToast');
    toast.className = `curr-exp-toast ${type}`;
    document.getElementById('currExpToastIcon').innerHTML = type === 'success'
        ? '<i class="fas fa-check-circle"></i>' : '<i class="fas fa-times-circle"></i>';
    document.getElementById('currExpToastTitle').textContent = title;
    document.getElementById('currExpToastMsg').textContent   = msg;
    toast.style.display = 'flex';
    setTimeout(() => { toast.style.display = 'none'; }, 6000);
}

// ── Format generators ─────────────────────────────────────────────────────────
function _currExportCSV(curricula, filename) {
    const now = new Date().toLocaleString();
    const q   = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = [`"Curriculum Export"`, `"Generated: ${now}"`, `"Total Curricula: ${curricula.length}"`, ''];
    curricula.forEach(curr => {
        lines.push(q(`Program: ${curr.program_name}`));
        lines.push(q(`Curriculum: ${curr.curriculum_code} — C.Y ${curr.curriculum_year}`));
        lines.push('');
        curr.year_levels.forEach(yl => {
            yl.semesters.forEach(sem => {
                lines.push(q(`${yl.label} — ${sem.label}`));
                lines.push(['Subject Code','Prereq','Co-req','Description','Lec Hrs','Lab Hrs','Credited Units','Tuition Hrs'].map(q).join(','));
                sem.subjects.forEach(s => {
                    lines.push([s.subject_code,s.prerequisite,s.corequisite,s.subject_name,s.lecture_hours,s.lab_hours,s.credit_units,s.tuition_hours].map(q).join(','));
                });
                lines.push(['','','',q('TOTAL UNITS'),'','',(sem.total_units||0),(sem.total_tuition||0)].join(','));
                lines.push('');
            });
        });
        lines.push('', '');
    });
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.csv' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function _currExportXLSX(curricula, filename) {
    const res = await fetch(_CURR_XLSX_ROUTE, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ curricula, title: 'Curriculum Export', timestamp: 'Generated: ' + new Date().toLocaleString() }),
    });
    if (!res.ok) throw new Error('Server error: ' + (await res.text() || 'XLSX failed'));
    const blob = await res.blob();
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.xlsx' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function _currExportPDF(curricula, filename) {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4' });
    const W   = 297;
    curricula.forEach((curr, cidx) => {
        if (cidx > 0) doc.addPage();
        doc.setFillColor(128, 0, 0); doc.rect(0, 0, W, 34, 'F');
        doc.setTextColor(255,255,255);
        doc.setFont('helvetica','bold'); doc.setFontSize(15);
        doc.text(`${curr.curriculum_code}  —  ${curr.program_name}`, W/2, 13, { align:'center' });
        doc.setFont('helvetica','normal'); doc.setFontSize(10);
        doc.text(`Curriculum Year: ${curr.curriculum_year}`, W/2, 22, { align:'center' });
        doc.setFontSize(8);
        doc.text('Generated: ' + new Date().toLocaleString(), W/2, 30, { align:'center' });
        doc.setTextColor(0,0,0);
        let startY = 36;
        curr.year_levels.forEach(yl => {
            yl.semesters.forEach(sem => {
                if (startY > 192) { doc.addPage(); startY = 10; }
                doc.setFillColor(55,55,55); doc.rect(10, startY, W-20, 7, 'F');
                doc.setTextColor(255,255,255); doc.setFont('helvetica','bold'); doc.setFontSize(8.5);
                doc.text(`${yl.label.toUpperCase()}  ·  ${sem.label}`, 14, startY+5);
                doc.setTextColor(0,0,0); startY += 7;
                doc.autoTable({
                    columns: [
                        { header:'Subject Code',   dataKey:'subject_code'  },
                        { header:'Prereq',         dataKey:'prerequisite'  },
                        { header:'Co-req',         dataKey:'corequisite'   },
                        { header:'Description',    dataKey:'subject_name'  },
                        { header:'Lec Hrs',        dataKey:'lecture_hours' },
                        { header:'Lab Hrs',        dataKey:'lab_hours'     },
                        { header:'Credited Units', dataKey:'credit_units'  },
                        { header:'Tuition Hrs',    dataKey:'tuition_hours' },
                    ],
                    body: sem.subjects, startY,
                    styles: { fontSize:7.5, cellPadding:2, overflow:'linebreak' },
                    headStyles: { fillColor:[128,0,0], textColor:255, fontStyle:'bold', fontSize:8 },
                    alternateRowStyles: { fillColor:[253,245,245] },
                    tableWidth: W - 20,
                    columnStyles: {
                        0:{ cellWidth:32 },
                        1:{ cellWidth:26 }, 2:{ cellWidth:20 },
                        3:{ cellWidth:126 },
                        4:{ cellWidth:16,halign:'center' }, 5:{ cellWidth:16,halign:'center' },
                        6:{ cellWidth:22,halign:'center' }, 7:{ cellWidth:19,halign:'center' },
                    },
                    foot: [['','','','TOTAL UNITS','','',(sem.total_units||0),(sem.total_tuition||0)]],
                    footStyles: { fillColor:[240,230,230], fontStyle:'bold', fontSize:8, textColor:[80,0,0] },
                    showFoot: 'lastPage', margin: { left:10, right:10 },
                });
                startY = doc.lastAutoTable.finalY + 4;
            });
        });
    });
    const total = doc.internal.getNumberOfPages();
    for (let i = 1; i <= total; i++) {
        doc.setPage(i); doc.setFontSize(7); doc.setTextColor(150,150,150);
        doc.text(`Page ${i} of ${total}`, 287, 206, { align:'right' });
    }
    doc.save(filename + '.pdf');
}

async function _currExportDOCX(curricula, filename) {
    const res = await fetch(_CURR_DOCX_ROUTE, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ curricula, title: 'Curriculum Export', timestamp: 'Generated: ' + new Date().toLocaleString() }),
    });
    if (!res.ok) throw new Error('Server error: ' + (await res.text() || 'DOCX failed'));
    const blob = await res.blob();
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.docx' });
    a.click(); URL.revokeObjectURL(a.href);
}

// ── Main executor ─────────────────────────────────────────────────────────────
async function currExecuteExport() {
    const selectedFmts = Array.from(document.querySelectorAll('#currExportModal .curr-exp-format-card.selected'))
        .map(c => c.dataset.format);
    const fmtErr = document.getElementById('currExpFmtError');
    if (selectedFmts.length === 0) { if (fmtErr) fmtErr.style.display = 'block'; return; }
    if (fmtErr) fmtErr.style.display = 'none';
    if (_currExpSelectedIds.size === 0) {
        _showCurrExpToast('error', 'No Curricula Selected', 'Please select at least one curriculum to export.'); return;
    }
    const filename = _buildCurrFilename();
    const ids      = Array.from(_currExpSelectedIds);
    const btn      = document.getElementById('currExpConfirmBtn');
    btn.disabled   = true;
    closeCurrExportModal();
    _showCurrExpLoading('Fetching Curriculum Data', 'Loading subject details from database...');
    let curricula;
    try {
        const res = await fetch(_CURR_DATA_ROUTE, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ curriculum_ids: ids }),
        });
        if (!res.ok) throw new Error(await res.text() || 'Failed to fetch curriculum data');
        curricula = await res.json();
    } catch (e) {
        _hideCurrExpLoading(); btn.disabled = false;
        _showCurrExpToast('error', 'Data Fetch Failed', e.message); return;
    }
    const errors = [];
    for (const fmt of selectedFmts) {
        _showCurrExpLoading(`Exporting ${fmt.toUpperCase()}`, `Processing ${curricula.length} curricul${curricula.length===1?'um':'a'}...`);
        try {
            if (fmt === 'csv')  _currExportCSV(curricula, filename);
            if (fmt === 'xlsx') await _currExportXLSX(curricula, filename);
            if (fmt === 'pdf')  await _currExportPDF(curricula, filename);
            if (fmt === 'docx') await _currExportDOCX(curricula, filename);
            await new Promise(r => setTimeout(r, 400));
        } catch (e) {
            console.error(`Curriculum export ${fmt} error:`, e);
            errors.push(fmt.toUpperCase() + ': ' + e.message);
        }
    }
    _hideCurrExpLoading(); btn.disabled = false;
    if (errors.length === 0) {
        _showCurrExpToast('success', 'Export Complete', `${selectedFmts.length} file(s) downloaded successfully.`);
    } else if (errors.length < selectedFmts.length) {
        _showCurrExpToast('error', 'Partial Export', 'Some files failed: ' + errors.join('; '));
    } else {
        _showCurrExpToast('error', 'Export Failed', errors.join('; '));
    }
}

const defaultOrder = [
    { val: 'cc', text: 'Curriculum Code' }, { val: 'yl', text: 'Year Level' },
    { val: 'sem', text: 'Semester' }, { val: 'sc', text: 'Subject Code' },
    { val: 'pre', text: 'Pre-requisite' }, { val: 'co', text: 'Co-requisite' },
    { val: 'sn', text: 'Subject Name' }, { val: 'lc', text: 'Lec Hours' },
    { val: 'lb', text: 'Lab Hours' }, { val: 'u', text: 'Units' },
    { val: 'th', text: 'Tuition Hours' }, { val: 'skip', text: '-- Skip --' }
];

function toggleColumnCustomization() {
    document.getElementById('defaultColumnView').style.display = 'none';
    document.getElementById('customColumnView').style.display = 'block';
    const grid = document.getElementById('mappingGrid');
    grid.innerHTML = '';
    for (let i = 0; i < 11; i++) {
        let html = `<div class="map-item-vert"><label>Col ${i + 1}</label><select onchange="updateHiddenCol(${i}, this.value)">`;
        defaultOrder.forEach(opt => {
            let selected = (defaultOrder[i] && defaultOrder[i].val === opt.val) ? 'selected' : '';
            html += `<option value="${opt.val}" ${selected}>${opt.text}</option>`;
        });
        grid.innerHTML += html + `</select></div>`;
    }
}

function resetColumnCustomization() {
    document.getElementById('defaultColumnView').style.display = 'block';
    document.getElementById('customColumnView').style.display = 'none';
    const originalDefault = ['cc', 'yl', 'sem', 'sc', 'pre', 'co', 'sn', 'lc', 'lb', 'u', 'th'];
    for (let i = 0; i < 11; i++) {
        const hiddenCol = document.getElementById(`h_col_${i}`);
        if (hiddenCol) hiddenCol.value = originalDefault[i];
    }
}

function updateHiddenCol(index, val) { document.getElementById(`h_col_${index}`).value = val; }

// ── Column customization for XLSX ───────────────────────────────────────────
function toggleXlsxColumnCustomization() {
    document.getElementById('xlsxDefaultColumnView').style.display = 'none';
    document.getElementById('xlsxCustomColumnView').style.display = 'block';
    const grid = document.getElementById('xlsxMappingGrid');
    grid.innerHTML = '';
    for (let i = 0; i < 11; i++) {
        const cur = document.getElementById(`x_col_${i}`).value;
        let html = `<div class="map-item-vert"><label>Col ${i + 1}</label><select onchange="updateXlsxHiddenCol(${i}, this.value)">`;
        defaultOrder.forEach(opt => {
            let selected = cur === opt.val ? 'selected' : '';
            html += `<option value="${opt.val}" ${selected}>${opt.text}</option>`;
        });
        grid.innerHTML += html + `</select></div>`;
    }
}
function resetXlsxColumnCustomization() {
    document.getElementById('xlsxDefaultColumnView').style.display = 'block';
    document.getElementById('xlsxCustomColumnView').style.display = 'none';
    const def = ['cc','yl','sem','sc','pre','co','sn','lc','lb','u','th'];
    for (let i = 0; i < 11; i++) {
        const el = document.getElementById(`x_col_${i}`);
        if (el) el.value = def[i];
    }
}
function updateXlsxHiddenCol(index, val) { document.getElementById(`x_col_${index}`).value = val; }

// ── Column customization for PDF ────────────────────────────────────────────
function togglePdfColumnCustomization() {
    document.getElementById('pdfDefaultColumnView').style.display = 'none';
    document.getElementById('pdfCustomColumnView').style.display = 'block';
    const grid = document.getElementById('pdfMappingGrid');
    grid.innerHTML = '';
    for (let i = 0; i < 11; i++) {
        const cur = document.getElementById(`pdf_h_col_${i}`).value;
        let html = `<div class="map-item-vert"><label>Col ${i + 1}</label><select onchange="updatePdfHiddenCol(${i}, this.value)">`;
        defaultOrder.forEach(opt => {
            let selected = cur === opt.val ? 'selected' : '';
            html += `<option value="${opt.val}" ${selected}>${opt.text}</option>`;
        });
        grid.innerHTML += html + `</select></div>`;
    }
}
function resetPdfColumnCustomization() {
    document.getElementById('pdfDefaultColumnView').style.display = 'block';
    document.getElementById('pdfCustomColumnView').style.display = 'none';
    const def = ['cc','yl','sem','sc','pre','co','sn','lc','lb','u','th'];
    for (let i = 0; i < 11; i++) {
        const el = document.getElementById(`pdf_h_col_${i}`);
        if (el) el.value = def[i];
    }
}
function updatePdfHiddenCol(index, val) { document.getElementById(`pdf_h_col_${index}`).value = val; }

// ── Column customization for DOCX ───────────────────────────────────────────
function toggleDocxColumnCustomization() {
    document.getElementById('docxDefaultColumnView').style.display = 'none';
    document.getElementById('docxCustomColumnView').style.display = 'block';
    const grid = document.getElementById('docxMappingGrid');
    grid.innerHTML = '';
    for (let i = 0; i < 11; i++) {
        const cur = document.getElementById(`docx_h_col_${i}`).value;
        let html = `<div class="map-item-vert"><label>Col ${i + 1}</label><select onchange="updateDocxHiddenCol(${i}, this.value)">`;
        defaultOrder.forEach(opt => {
            let selected = cur === opt.val ? 'selected' : '';
            html += `<option value="${opt.val}" ${selected}>${opt.text}</option>`;
        });
        grid.innerHTML += html + `</select></div>`;
    }
}
function resetDocxColumnCustomization() {
    document.getElementById('docxDefaultColumnView').style.display = 'block';
    document.getElementById('docxCustomColumnView').style.display = 'none';
    const def = ['cc','yl','sem','sc','pre','co','sn','lc','lb','u','th'];
    for (let i = 0; i < 11; i++) {
        const el = document.getElementById(`docx_h_col_${i}`);
        if (el) el.value = def[i];
    }
}
function updateDocxHiddenCol(index, val) { document.getElementById(`docx_h_col_${index}`).value = val; }

// ── Import type selector ────────────────────────────────────────────────────
function openImportTypeModal() { document.getElementById('importTypeModal').style.display = 'flex'; }
function closeImportTypeModal() { document.getElementById('importTypeModal').style.display = 'none'; }

function openCsvImportModal() {
    closeImportTypeModal();
    document.getElementById('importModal').style.display = 'flex';
    resetColumnCustomization();
}

function openImportModal() { openImportTypeModal(); }   // legacy alias
function closeImportModal() {
    document.getElementById('importModal').style.display = 'none';
}

// ── Excel (.xlsx) import modal ──────────────────────────────────────────────
function openXlsxImportModal() {
    closeImportTypeModal();
    resetXlsxColumnCustomization();
    document.getElementById('xlsxImportModal').style.display = 'flex';
}
function closeXlsxImportModal() {
    document.getElementById('xlsxImportModal').style.display = 'none';
}
function updateXlsxYearLevelDropdown() {
    const progSelect = document.getElementById('xlsxModalProgramCode');
    const ylSelect   = document.getElementById('xlsxModalYearLevel');
    const selectedOption = progSelect.options[progSelect.selectedIndex];
    let maxYears = parseInt(selectedOption.getAttribute('data-years'));
    if (isNaN(maxYears) || maxYears <= 0) maxYears = 4;
    const currentVal = ylSelect.value;
    let html = '<option value="0">All Year Levels</option>';
    for (let i = 1; i <= maxYears; i++) {
        let suffix = i === 1 ? "st" : i === 2 ? "nd" : i === 3 ? "rd" : "th";
        html += `<option value="${i}">${i}${suffix} Year</option>`;
    }
    ylSelect.innerHTML = html;
    ylSelect.value = (currentVal <= maxYears) ? currentVal : "0";
}
async function analyzeXlsxCurriculum() {
    const progCode  = document.getElementById('xlsxModalProgramCode').value;
    const currYear  = document.getElementById('xlsxModalCurriculumYear').value.trim();
    const fileInput = document.getElementById('xlsxModalFileInput');
    const btn       = document.getElementById('xlsxAnalyzeBtn');
    const errorBox  = document.getElementById('xlsxAnalyzeError');
    errorBox.style.display = 'none';

    if (!progCode) { errorBox.textContent = 'Please select a target program.'; errorBox.style.display = 'block'; return; }
    if (!currYear || !/^\d{4}-\d{4}$/.test(currYear)) { errorBox.textContent = 'Please enter a valid curriculum year (e.g. 2024-2025).'; errorBox.style.display = 'block'; return; }
    if (!fileInput.files.length) { errorBox.textContent = 'Please select an Excel file.'; errorBox.style.display = 'block'; return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    for (let i = 0; i < 11; i++) fd.append(`col_${i}`, document.getElementById(`x_col_${i}`).value);
    fd.append('year_level', document.getElementById('xlsxModalYearLevel').value);
    fd.append('semester',   document.getElementById('xlsxModalSemester').value);

    try {
        const res  = await fetch('/admin/curriculum/import/xlsx/analyze', { method: 'POST', body: fd });
        const data = await res.json();
        if (!res.ok || data.error) { errorBox.textContent = data.error || 'Server error.'; errorBox.style.display = 'block'; return; }
        _pdfExtractedSubjects = data.subjects || [];
        _pdfProgCode = progCode;
        _pdfCurrYear = currYear;
        closeXlsxImportModal();
        _renderPdfReviewModal(data);
    } catch (err) {
        errorBox.textContent = 'Network error: ' + err.message;
        errorBox.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-search"></i> Analyze Excel';
    }
}

// ── PDF upload modal ────────────────────────────────────────────────────────
function openPdfImportModal() {
    closeImportTypeModal();
    resetPdfColumnCustomization();
    document.getElementById('pdfAnalyzeError').style.display = 'none';
    document.getElementById('pdfImportModal').style.display = 'flex';
}
function closePdfImportModal() {
    document.getElementById('pdfImportModal').style.display = 'none';
}

// ── Word (.docx) upload modal ───────────────────────────────────────────────
function openDocxImportModal() {
    closeImportTypeModal();
    resetDocxColumnCustomization();
    document.getElementById('docxAnalyzeError').style.display = 'none';
    document.getElementById('docxImportModal').style.display = 'flex';
}
function closeDocxImportModal() {
    document.getElementById('docxImportModal').style.display = 'none';
}

async function analyzeCsvCurriculum() {
    const progCode  = document.getElementById('modalProgramCode').value;
    const currYear  = document.getElementById('modalCurriculumYear').value.trim();
    const fileInput = document.getElementById('modalFileInput');
    const btn       = document.getElementById('csvAnalyzeBtn');
    const errorBox  = document.getElementById('csvAnalyzeError');
    errorBox.style.display = 'none';

    if (!progCode) { errorBox.textContent = 'Please select a target program.'; errorBox.style.display = 'block'; return; }
    if (!currYear || !/^\d{4}-\d{4}$/.test(currYear)) { errorBox.textContent = 'Please enter a valid curriculum year (e.g. 2024-2025).'; errorBox.style.display = 'block'; return; }
    if (!fileInput.files.length) { errorBox.textContent = 'Please select a CSV file.'; errorBox.style.display = 'block'; return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    for (let i = 0; i < 11; i++) fd.append(`col_${i}`, document.getElementById(`h_col_${i}`).value);
    fd.append('year_level', document.getElementById('modalYearLevel').value);
    fd.append('semester',   document.getElementById('modalSemester').value);

    try {
        const res  = await fetch('/admin/curriculum/import/csv/analyze', { method: 'POST', body: fd });
        const data = await res.json();
        if (!res.ok || data.error) { errorBox.textContent = data.error || 'Server error.'; errorBox.style.display = 'block'; return; }
        _pdfExtractedSubjects = data.subjects || [];
        _pdfProgCode = progCode;
        _pdfCurrYear = currYear;
        closeImportModal();
        _renderPdfReviewModal(data);
    } catch (err) {
        errorBox.textContent = 'Network error: ' + err.message;
        errorBox.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-search"></i> Analyze CSV';
    }
}

let deleteTargetId = null;

function confirmDeleteCurriculum(id, year) {
    deleteTargetId = id;
    document.getElementById('deleteYearText').innerText = "C.Y " + year;
    document.getElementById('deleteCurriculumModal').style.display = "flex";
    document.getElementById('confirmDeleteBtn').onclick = function () {
        window.location.href = "/admin/curriculum/delete/" + deleteTargetId;
    };
}

function closeDeleteModal() { document.getElementById('deleteCurriculumModal').style.display = "none"; }

function updateYearLevelDropdown() {
    const progSelect = document.getElementById('modalProgramCode');
    const ylSelect = document.getElementById('modalYearLevel');
    const selectedOption = progSelect.options[progSelect.selectedIndex];
    let maxYears = parseInt(selectedOption.getAttribute('data-years'));
    if (isNaN(maxYears) || maxYears <= 0) maxYears = 4;

    const currentVal = ylSelect.value;
    let html = '<option value="0">All Year Levels</option>';
    for (let i = 1; i <= maxYears; i++) {
        let suffix = i === 1 ? "st" : i === 2 ? "nd" : i === 3 ? "rd" : "th";
        html += `<option value="${i}">${i}${suffix} Year</option>`;
    }

    ylSelect.innerHTML = html;
    ylSelect.value = (currentVal <= maxYears) ? currentVal : "0";
}

// ── PDF Analysis & Review ───────────────────────────────────────────────────

let _pdfExtractedSubjects = [];
let _pdfProgCode = '';
let _pdfCurrYear = '';

async function analyzePdf() {
    const progCode  = document.getElementById('pdfProgramCode').value;
    const currYear  = document.getElementById('pdfCurriculumYear').value.trim();
    const fileInput = document.getElementById('pdfFileInput');
    const errorBox  = document.getElementById('pdfAnalyzeError');
    const btn       = document.getElementById('pdfAnalyzeBtn');

    errorBox.style.display = 'none';

    if (!progCode) { _showPdfError('Please select a target program.'); return; }
    if (!currYear || !/^\d{4}-\d{4}$/.test(currYear)) {
        _showPdfError('Please enter a valid curriculum year (e.g. 2024-2025).');
        return;
    }
    if (!fileInput.files.length) { _showPdfError('Please select a PDF file to upload.'); return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    if (document.getElementById('pdfCustomColumnView').style.display !== 'none') {
        for (let i = 0; i < 11; i++) {
            formData.append(`col_${i}`, document.getElementById(`pdf_h_col_${i}`).value);
        }
    }

    try {
        const res  = await fetch('/admin/curriculum/import/pdf/analyze', { method: 'POST', body: formData });
        const data = await res.json();

        if (!res.ok || data.error) {
            _showPdfError(data.error || 'Server error during PDF analysis.');
            return;
        }

        _pdfExtractedSubjects = data.subjects || [];
        _pdfProgCode = progCode;
        _pdfCurrYear = currYear;

        closePdfImportModal();
        _renderPdfReviewModal(data);
    } catch (err) {
        _showPdfError('Network error: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-search"></i> Analyze PDF';
    }
}

function _showPdfError(msg) {
    const box = document.getElementById('pdfAnalyzeError');
    box.textContent = msg;
    box.style.display = 'block';
}

async function analyzeDocx() {
    const progCode  = document.getElementById('docxProgramCode').value;
    const currYear  = document.getElementById('docxCurriculumYear').value.trim();
    const fileInput = document.getElementById('docxFileInput');
    const errorBox  = document.getElementById('docxAnalyzeError');
    const btn       = document.getElementById('docxAnalyzeBtn');

    errorBox.style.display = 'none';

    if (!progCode) { _showDocxError('Please select a target program.'); return; }
    if (!currYear || !/^\d{4}-\d{4}$/.test(currYear)) {
        _showDocxError('Please enter a valid curriculum year (e.g. 2024-2025).');
        return;
    }
    if (!fileInput.files.length) { _showDocxError('Please select a Word (.docx) file to upload.'); return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    if (document.getElementById('docxCustomColumnView').style.display !== 'none') {
        for (let i = 0; i < 11; i++) {
            formData.append(`col_${i}`, document.getElementById(`docx_h_col_${i}`).value);
        }
    }

    try {
        const res  = await fetch('/admin/curriculum/import/docx/analyze', { method: 'POST', body: formData });
        const data = await res.json();

        if (!res.ok || data.error) {
            _showDocxError(data.error || 'Server error during document analysis.');
            return;
        }

        _pdfExtractedSubjects = data.subjects || [];
        _pdfProgCode = progCode;
        _pdfCurrYear = currYear;

        closeDocxImportModal();
        _renderPdfReviewModal(data);   // reuse the same review modal
    } catch (err) {
        _showDocxError('Network error: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-search"></i> Analyze Document';
    }
}

function _showDocxError(msg) {
    const box = document.getElementById('docxAnalyzeError');
    box.textContent = msg;
    box.style.display = 'block';
}

function _renderPdfReviewModal(data) {
    const confidence = data.confidence || 0;
    const warnings   = data.warnings   || [];
    const rawText    = data.raw_text   || '';
    const banner     = document.getElementById('pdfReviewBanner');
    const badgeClass = confidence >= 75 ? 'conf-high' : confidence >= 40 ? 'conf-medium' : 'conf-low';

    let warnHtml = warnings.length
        ? '<ul class="pdf-warn-list">' + warnings.map(w => `<li>${w}</li>`).join('') + '</ul>'
        : '';

    // Show raw-text panel when nothing was extracted so admin can see what the PDF contains
    let rawHtml = '';
    if (confidence === 0 && rawText.trim()) {
        rawHtml = `
        <details class="pdf-raw-details">
            <summary>Show raw text extracted from PDF (use this to manually enter subjects)</summary>
            <pre class="pdf-raw-pre">${rawText.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</pre>
        </details>`;
    } else if (confidence === 0 && !rawText.trim()) {
        rawHtml = `<div class="pdf-scanned-warn">
            <i class="fas fa-exclamation-triangle"></i>
            No text could be read from this PDF. It may be a scanned image.
            Please use a text-based PDF or export the curriculum as CSV instead.
        </div>`;
    }

    banner.innerHTML = `
        <div class="pdf-conf-row">
            <span class="conf-badge ${badgeClass}">Extraction Confidence: ${confidence}%</span>
            ${confidence < 75 ? '<span class="conf-note">Please review and correct the data below before importing.</span>' : ''}
        </div>
        ${warnHtml}
        ${rawHtml}
    `;

    // Subject count
    document.getElementById('pdfReviewSubjectCount').textContent =
        `${_pdfExtractedSubjects.length} subjects extracted`;

    // Build table rows
    _rebuildReviewTable();

    document.getElementById('pdfReviewModal').style.display = 'flex';
}

// ── Constants for grouping ──────────────────────────────────────────────────
const _SEM_ORDER  = { A: 0, B: 1, C: 2 };
const _YL_LABELS  = ['Unassigned', '1st Year', '2nd Year', '3rd Year', '4th Year', '5th Year'];
const _SEM_LABELS = { A: '1st Semester', B: '2nd Semester', C: 'Summer' };

// Maps group key → array of origIdx values; rebuilt every render
let _groupIdxMap = {};

function _esc(val) {
    return String(val || '')
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

// Returns _pdfExtractedSubjects sorted by (yl, sem), each entry annotated with origIdx
function _sortedWithIdx() {
    return _pdfExtractedSubjects
        .map((s, i) => ({ s, origIdx: i }))
        .sort((a, b) => {
            const ylA = a.s.yl || 99, ylB = b.s.yl || 99;
            if (ylA !== ylB) return ylA - ylB;
            return (_SEM_ORDER[a.s.sem] ?? 0) - (_SEM_ORDER[b.s.sem] ?? 0);
        });
}

function _rebuildReviewTable() {
    const tbody = document.getElementById('pdfReviewTableBody');
    tbody.innerHTML = '';
    _groupIdxMap = {};

    const sorted = _sortedWithIdx();
    let currentKey = null;

    sorted.forEach(({ s, origIdx }) => {
        const key = `${s.yl || 0}_${s.sem || 'A'}`;   // e.g. "1_A", "2_B"

        // ── Insert group header when group changes ──────────────────────
        if (key !== currentKey) {
            currentKey = key;

            // Collect all origIdxs for this group for bulk-move
            const groupItems = sorted.filter(
                ({ s: g }) => `${g.yl || 0}_${g.sem || 'A'}` === key
            );
            _groupIdxMap[key] = groupItems.map(item => item.origIdx);

            const groupCount = groupItems.length;
            const ylLabel  = s.yl ? (_YL_LABELS[s.yl] || `Year ${s.yl}`) : 'Unassigned';
            const semLabel = _SEM_LABELS[s.sem] || s.sem || '';

            // Build year-level options for the move-all selector
            const ylOpts = [1, 2, 3, 4, 5]
                .map(n => `<option value="${n}" ${s.yl == n ? 'selected' : ''}>${_YL_LABELS[n]}</option>`)
                .join('');
            const semOpts = `
                <option value="A" ${s.sem === 'A' ? 'selected' : ''}>1st Semester</option>
                <option value="B" ${s.sem === 'B' ? 'selected' : ''}>2nd Semester</option>
                <option value="C" ${s.sem === 'C' ? 'selected' : ''}>Summer</option>`;

            const hdr = document.createElement('tr');
            hdr.className = 'rev-group-hdr';
            hdr.innerHTML = `
                <td colspan="11">
                    <div class="rev-group-hdr-inner">
                        <div class="rev-group-left">
                            <span class="rev-group-title">${ylLabel} &mdash; ${semLabel}</span>
                            <span class="rev-group-badge">${groupCount} subject${groupCount !== 1 ? 's' : ''}</span>
                        </div>
                        <div class="rev-group-move">
                            <span class="rev-group-move-label">Move all to:</span>
                            <select class="rev-group-sel" id="gYl_${key}">${ylOpts}</select>
                            <select class="rev-group-sel" id="gSem_${key}">${semOpts}</select>
                            <button type="button" class="btn-group-apply"
                                onclick="_applyGroupMove('${key}')">Apply</button>
                        </div>
                    </div>
                </td>`;
            tbody.appendChild(hdr);
        }

        tbody.appendChild(_buildReviewRow(s, origIdx));
    });

    document.getElementById('pdfReviewSubjectCount').textContent =
        `${_pdfExtractedSubjects.length} subject${_pdfExtractedSubjects.length !== 1 ? 's' : ''} extracted`;
}

// Bulk-move all subjects in a group to a new year level / semester
function _applyGroupMove(key) {
    const newYl  = parseInt(document.getElementById(`gYl_${key}`)?.value)  || 1;
    const newSem = document.getElementById(`gSem_${key}`)?.value || 'A';
    const idxs   = _groupIdxMap[key] || [];
    idxs.forEach(i => {
        if (_pdfExtractedSubjects[i]) {
            _pdfExtractedSubjects[i].yl  = newYl;
            _pdfExtractedSubjects[i].sem = newSem;
        }
    });
    _rebuildReviewTable();
}

function _buildReviewRow(s, origIdx) {
    const tr = document.createElement('tr');
    if (!s.sc) tr.classList.add('row-warning');

    const ylOpts = [1,2,3,4,5]
        .map(n => `<option value="${n}" ${s.yl == n ? 'selected' : ''}>${n}</option>`)
        .join('');

    tr.innerHTML = `
        <td><input class="rev-input rev-code ${!s.sc ? 'rev-missing' : ''}" type="text"
            value="${_esc(s.sc)}" placeholder="e.g. CC101"
            onchange="_updateField(${origIdx},'sc',this.value)"></td>
        <td><input class="rev-input rev-desc" type="text"
            value="${_esc(s.sn)}" placeholder="Subject Description"
            onchange="_updateField(${origIdx},'sn',this.value)"></td>
        <td><input class="rev-input rev-req" type="text"
            value="${_esc(s.pre || '')}" placeholder="None"
            onchange="_updateField(${origIdx},'pre',this.value)"></td>
        <td><input class="rev-input rev-req" type="text"
            value="${_esc(s.co || '')}" placeholder="None"
            onchange="_updateField(${origIdx},'co',this.value)"></td>
        <td><input class="rev-input rev-num" type="number" min="0" value="${s.lc || 0}"
            onchange="_updateField(${origIdx},'lc',this.value)"></td>
        <td><input class="rev-input rev-num" type="number" min="0" value="${s.lb || 0}"
            onchange="_updateField(${origIdx},'lb',this.value)"></td>
        <td><input class="rev-input rev-num" type="number" min="0" value="${s.u || 0}"
            onchange="_updateField(${origIdx},'u',this.value)"></td>
        <td><input class="rev-input rev-num" type="number" min="0" value="${s.th || 0}"
            onchange="_updateField(${origIdx},'th',this.value)"></td>
        <td><select class="rev-input rev-yl"
            onchange="_updateGroupField(${origIdx},'yl',this.value)">${ylOpts}</select></td>
        <td><select class="rev-input rev-sem"
            onchange="_updateGroupField(${origIdx},'sem',this.value)">
            <option value="A" ${s.sem === 'A' ? 'selected' : ''}>1st</option>
            <option value="B" ${s.sem === 'B' ? 'selected' : ''}>2nd</option>
            <option value="C" ${s.sem === 'C' ? 'selected' : ''}>Sum</option>
        </select></td>
        <td><button type="button" class="btn-row-delete"
            onclick="_deleteReviewRow(${origIdx})" title="Remove row">
            <i class="fas fa-times"></i>
        </button></td>`;
    return tr;
}

// Update a data field in-place (no re-sort needed)
function _updateField(origIdx, field, value) {
    if (!_pdfExtractedSubjects[origIdx]) return;
    const numFields = ['u', 'lc', 'lb', 'th'];
    _pdfExtractedSubjects[origIdx][field] =
        numFields.includes(field) ? (parseInt(value) || 0) : value;
}

// Update YL or Sem → requires re-sort to move the row to the correct group
function _updateGroupField(origIdx, field, value) {
    _updateField(origIdx, field, value);
    _rebuildReviewTable();
}

function _deleteReviewRow(origIdx) {
    _pdfExtractedSubjects.splice(origIdx, 1);
    _rebuildReviewTable();
}

function addPdfReviewRow() {
    _pdfExtractedSubjects.push({ yl: 1, sem: 'A', sc: '', sn: '', u: 0, lc: 0, lb: 0, th: 0, pre: '', co: '' });
    _rebuildReviewTable();
    // Scroll to the last data row
    const rows = document.querySelectorAll('#pdfReviewTableBody tr:not(.rev-group-hdr)');
    if (rows.length) rows[rows.length - 1].scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function closePdfReviewModal() {
    document.getElementById('pdfReviewModal').style.display = 'none';
}

function confirmPdfImport() {
    const valid = _pdfExtractedSubjects.filter(s => s.sc && s.sc.trim());
    if (!valid.length) {
        alert('No valid subjects to import. Each row must have a Subject Code.');
        return;
    }
    document.getElementById('pdfConfirmProg').value  = _pdfProgCode;
    document.getElementById('pdfConfirmYear').value  = _pdfCurrYear;
    document.getElementById('pdfConfirmData').value  = JSON.stringify(valid);
    document.getElementById('pdfConfirmForm').submit();
}
