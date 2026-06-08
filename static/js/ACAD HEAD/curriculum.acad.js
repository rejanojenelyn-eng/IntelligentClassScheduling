// ── Tab switching with localStorage persistence ───────────────────────────────
function switchCurrTab(tab) {
    const panels = { assignments: 'currPanelAssignments', syllabi: 'currPanelSyllabi' };
    const btns   = { assignments: 'tabBtnAssignments',    syllabi: 'tabBtnSyllabi'    };
    Object.keys(panels).forEach(k => {
        document.getElementById(panels[k]).style.display = k === tab ? '' : 'none';
        document.getElementById(btns[k]).classList.toggle('active', k === tab);
    });
    try { localStorage.setItem('currTab', tab); } catch(e) {}
}

document.addEventListener('DOMContentLoaded', function () {
    try {
        const saved = localStorage.getItem('currTab');
        if (saved === 'syllabi') switchCurrTab('syllabi');
    } catch(e) {}
});

// ── Assignment table filter + sort ────────────────────────────────────────────
function filterAssignments() {
    const search     = (document.getElementById('searchAssign')?.value     || '').toLowerCase();
    const currFilter = (document.getElementById('filterAssignCurr')?.value || '').toLowerCase();
    const progFilter = (document.getElementById('filterAssignProg')?.value || '').toLowerCase();
    document.querySelectorAll('#assignmentTable tbody tr').forEach(row => {
        const currText = (row.dataset.curr || row.cells[0]?.textContent || '').toLowerCase().trim();
        const progText = (row.dataset.prog || row.cells[1]?.textContent || '').toLowerCase().trim();
        const matchSearch = !search     || currText.includes(search) || progText.includes(search);
        const matchCurr   = !currFilter || currText === currFilter;
        const matchProg   = !progFilter || progText === progFilter;
        row.style.display = (matchSearch && matchCurr && matchProg) ? '' : 'none';
    });
}

let _sortAssignAsc = true;
function sortAssignTable() {
    const tbody = document.querySelector('#assignmentTable tbody');
    if (!tbody) return;
    const rows = Array.from(tbody.querySelectorAll('tr'));
    rows.sort((a, b) => {
        const pa = (a.dataset.prog || a.cells[1]?.textContent || '').trim().toLowerCase();
        const pb = (b.dataset.prog || b.cells[1]?.textContent || '').trim().toLowerCase();
        return _sortAssignAsc ? pa.localeCompare(pb) : pb.localeCompare(pa);
    });
    _sortAssignAsc = !_sortAssignAsc;
    rows.forEach(r => tbody.appendChild(r));
    const btn = document.getElementById('sortAssignBtn');
    if (btn) btn.innerHTML = `<i class="fas fa-sort-amount-${_sortAssignAsc ? 'down' : 'up'}-alt"></i> SORT: ${_sortAssignAsc ? 'A-Z' : 'Z-A'}`;
}

// ── Syllabi program filter ────────────────────────────────────────────────────
function filterSyllabiByProgram(code) {
    let anyVisible = false;
    document.querySelectorAll('#syllabiList .curr-prog-section').forEach(sec => {
        const show = (!code || code === 'All' || sec.dataset.programCode === code);
        sec.style.display = show ? '' : 'none';
        if (show) anyVisible = true;
    });
    const msg = document.getElementById('syllabiEmptyMsg');
    if (msg) msg.style.display = (!code || code === 'All' || anyVisible) ? 'none' : 'block';
}

// ── CURRICULUM EXPORT ENGINE (uses same routes as admin — acad head access opened in app.py) ──
const _CURR_DOCX_ROUTE = '/admin/curriculum/export/docx';
const _CURR_XLSX_ROUTE = '/admin/curriculum/export/xlsx';
const _CURR_DATA_ROUTE = '/admin/curriculum/export/data';
const _CURR_LIST_ROUTE = '/admin/curriculum/export/list';

let _currExpPrograms    = [];
let _currExpSelectedIds = new Set();
let _currExpLoaded      = false;

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

function _toggleCurrProgCollapse(progCode) {
    const grp = document.getElementById(`pg_${progCode}`);
    const tog = document.getElementById(`pg_tog_${progCode}`);
    if (!grp) return;
    grp.classList.toggle('collapsed');
    if (tog) tog.style.transform = grp.classList.contains('collapsed') ? 'rotate(-90deg)' : '';
}

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
                        0:{ cellWidth:32 }, 1:{ cellWidth:26 }, 2:{ cellWidth:20 },
                        3:{ cellWidth:126 }, 4:{ cellWidth:16,halign:'center' },
                        5:{ cellWidth:16,halign:'center' }, 6:{ cellWidth:22,halign:'center' },
                        7:{ cellWidth:19,halign:'center' },
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
        } catch (e) { errors.push(fmt.toUpperCase() + ': ' + e.message); }
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
