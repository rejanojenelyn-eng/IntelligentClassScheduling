// ── Import offerings data (lazy-loaded from embedded JSON) ────────────────────
let _IMPORT_OFFERINGS = null;
function _getImportOfferings() {
    if (_IMPORT_OFFERINGS === null) {
        try { _IMPORT_OFFERINGS = JSON.parse(document.getElementById('import-offerings-data').textContent); }
        catch(e) { _IMPORT_OFFERINGS = []; }
    }
    return _IMPORT_OFFERINGS;
}

function populateOfferingDropdown(progSelectId, offerSelectId) {
    const progCode = document.getElementById(progSelectId)?.value;
    const sel      = document.getElementById(offerSelectId);
    if (!sel) return;
    sel.innerHTML = '';
    const matches = _getImportOfferings().filter(o => o.programcode === progCode);
    if (!matches.length) {
        sel.innerHTML = '<option value="" disabled selected>No offerings found</option>';
        return;
    }
    matches.forEach(o => {
        const lbl = o.trackname
            ? `${o.offeringcode} — ${o.trackname}`
            : `${o.offeringcode} (Base / General)`;
        const opt = document.createElement('option');
        opt.value = o.offeringcode;
        opt.textContent = lbl;
        sel.appendChild(opt);
    });
    if (matches.length === 1) sel.selectedIndex = 0;
    else { const ph = document.createElement('option'); ph.disabled = ph.selected = true; ph.textContent = 'Select offering'; sel.insertBefore(ph, sel.firstChild); }
}

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
    const search     = (document.getElementById('searchAssign')?.value    || '').toLowerCase();
    const currFilter = (document.getElementById('filterAssignCurr')?.value || '').toLowerCase();
    const progFilter = (document.getElementById('filterAssignProg')?.value || '').toLowerCase();
    document.querySelectorAll('#assignmentTable tbody tr').forEach(row => {
        const currText = (row.dataset.curr || row.cells[0]?.textContent || '').toLowerCase().trim();
        const progCode = (row.dataset.prog || '').toLowerCase().trim();
        const progText = (row.cells[1]?.textContent || '').toLowerCase().trim();
        const matchSearch = !search     || currText.includes(search) || progText.includes(search);
        const matchCurr   = !currFilter || currText === currFilter;
        const matchProg   = !progFilter || progCode.startsWith(progFilter);
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

function editAssignment(id, prog, curr, year, progName) {
    document.getElementById('assignModalTitle').innerHTML = '<i class="fas fa-edit"></i> Edit Assignment';
    document.getElementById('assignPylId').value = id;
    document.getElementById('hiddenProgramCode').value = prog;
    document.getElementById('hiddenStartYear').value = year;
    document.getElementById('assignStartYear').value = year;
    document.getElementById('assignProgramCode').value = progName || prog;

    const select = document.getElementById('assignCurriculumId');
    select.querySelectorAll('option').forEach(opt => {
        if (opt.value === "") return;
        const match = opt.getAttribute('data-program') === prog;
        opt.style.display = match ? 'block' : 'none';
        opt.disabled = !match;
    });

    select.value = curr;
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

let _currExpPrograms    = [];
let _currExpSelectedIds = new Set();
let _currExpLoaded      = false;
let _pendingFilterProgCode = null;

// ── Modal open / close ────────────────────────────────────────────────────────
async function openCurrExportModal(filterProgCode) {
    _pendingFilterProgCode = (filterProgCode && filterProgCode !== 'All') ? filterProgCode : null;
    // Pre-fill filename with program code when a specific program is filtered
    if (_pendingFilterProgCode) {
        const fnInput = document.getElementById('currExpFilename');
        if (fnInput) fnInput.value = _pendingFilterProgCode + '_Curriculum';
    } else {
        const fnInput = document.getElementById('currExpFilename');
        if (fnInput && !fnInput.value.trim()) fnInput.value = 'Curriculum_Export';
    }
    document.getElementById('currExportModal').style.display = 'flex';
    _updateCurrExpFooter();
    if (!_currExpLoaded) {
        await _loadCurrExpList();
    } else {
        _renderCurrExpList();
        if (_pendingFilterProgCode) {
            _applyProgramFilter(_pendingFilterProgCode);
            _pendingFilterProgCode = null;
        }
    }
}

function _applyProgramFilter(progCode) {
    _currExpSelectedIds.clear();
    const prog = _currExpPrograms.find(p => p.program_code === progCode);
    if (prog) {
        prog.curricula.forEach(c => _currExpSelectedIds.add(c.curriculum_id));
    }
    _renderCurrExpList();
    _updateCurrExpFooter();
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
        if (_pendingFilterProgCode) {
            _applyProgramFilter(_pendingFilterProgCode);
            _pendingFilterProgCode = null;
        }
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
              <span class="curr-exp-curr-label">Curriculum Year ${c.curriculum_year}</span>
              <span class="curr-exp-curr-code">${prog.program_code}</span>
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

// ── Blob-returning format generators ──────────────────────────────────────────

function _currCSVBlob(curricula) {
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
    return new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
}

async function _currXLSXBlob(curricula) {
    const res = await fetch(_CURR_XLSX_ROUTE, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ curricula, title: 'Curriculum Export', timestamp: 'Generated: ' + new Date().toLocaleString() }),
    });
    if (!res.ok) throw new Error('Server error: ' + (await res.text() || 'XLSX failed'));
    return await res.blob();
}

function _currPDFBlob(curricula) {
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
    return doc.output('blob');
}

async function _currDOCXBlob(curricula) {
    const res = await fetch(_CURR_DOCX_ROUTE, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ curricula, title: 'Curriculum Export', timestamp: 'Generated: ' + new Date().toLocaleString() }),
    });
    if (!res.ok) throw new Error('Server error: ' + (await res.text() || 'DOCX failed'));
    return await res.blob();
}

function _currTriggerDownload(blob, filename) {
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename });
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

    // Single file = direct download; multiple curricula or multiple formats = ZIP
    const useZip = curricula.length > 1 || selectedFmts.length > 1;
    const errors = [];

    if (!useZip) {
        // Single curriculum × single format → direct download
        const fmt = selectedFmts[0];
        _showCurrExpLoading(`Exporting ${fmt.toUpperCase()}`, curricula[0].program_name + '...');
        try {
            let blob;
            if (fmt === 'csv')  blob = _currCSVBlob(curricula);
            if (fmt === 'xlsx') blob = await _currXLSXBlob(curricula);
            if (fmt === 'pdf')  blob = _currPDFBlob(curricula);
            if (fmt === 'docx') blob = await _currDOCXBlob(curricula);
            if (blob) _currTriggerDownload(blob, filename + '.' + fmt);
        } catch (e) {
            errors.push(fmt.toUpperCase() + ': ' + e.message);
        }
    } else {
        // Multiple → ZIP; one file per curriculum per format
        const zip = new JSZip();
        for (const curr of curricula) {
            const safeCode = (curr.program_code || curr.curriculum_code || 'CURR')
                .replace(/[^a-zA-Z0-9_-]/g, '_');
            const safeYear = (curr.curriculum_year || '').replace(/[^a-zA-Z0-9_-]/g, '-');
            const currFile = `${safeCode}_CY${safeYear}`;

            for (const fmt of selectedFmts) {
                _showCurrExpLoading(`Packaging ${fmt.toUpperCase()}`,
                    `${curr.program_name} – ${curr.curriculum_year}…`);
                try {
                    let blob;
                    if (fmt === 'csv')  blob = _currCSVBlob([curr]);
                    if (fmt === 'xlsx') blob = await _currXLSXBlob([curr]);
                    if (fmt === 'pdf')  blob = _currPDFBlob([curr]);
                    if (fmt === 'docx') blob = await _currDOCXBlob([curr]);
                    if (blob) zip.file(`${currFile}.${fmt}`, blob);
                } catch (e) {
                    errors.push(`${curr.program_name} ${fmt.toUpperCase()}: ` + e.message);
                }
                await new Promise(r => setTimeout(r, 150));
            }
        }
        _showCurrExpLoading('Creating ZIP', 'Compressing all files…');
        try {
            const zipBlob = await zip.generateAsync({ type: 'blob' });
            _currTriggerDownload(zipBlob, filename + '.zip');
        } catch (e) {
            errors.push('ZIP: ' + e.message);
        }
    }

    _hideCurrExpLoading(); btn.disabled = false;
    if (errors.length === 0) {
        const msg = useZip
            ? `${curricula.length} curricul${curricula.length===1?'um':'a'} × ${selectedFmts.length} format${selectedFmts.length===1?'':'s'} packaged into ZIP.`
            : 'File downloaded successfully.';
        _showCurrExpToast('success', 'Export Complete', msg);
    } else if (errors.length < curricula.length * selectedFmts.length) {
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
let _preselectedProgram = '';   // captures the syllabi program filter when opening import

function openImportTypeModal() {
    const filter = document.getElementById('syllabiProgramFilter');
    _preselectedProgram = (filter && filter.value && filter.value !== 'All') ? filter.value : '';
    document.getElementById('importTypeModal').style.display = 'flex';
}
function closeImportTypeModal() { document.getElementById('importTypeModal').style.display = 'none'; }

function _applyPreselectedProgram(selectId) {
    if (!_preselectedProgram) return;
    const sel = document.getElementById(selectId);
    if (sel) sel.value = _preselectedProgram;
}

function openCsvImportModal() {
    closeImportTypeModal();
    document.getElementById('importModal').style.display = 'flex';
    resetColumnCustomization();
    _applyPreselectedProgram('modalProgramCode');
}

function openImportModal() { openImportTypeModal(); }   // legacy alias
function closeImportModal() {
    document.getElementById('importModal').style.display = 'none';
}
function backFromCsvModal() {
    closeImportModal();
    openImportTypeModal();
}

// ── Excel (.xlsx) import modal ──────────────────────────────────────────────
function openXlsxImportModal() {
    closeImportTypeModal();
    resetXlsxColumnCustomization();
    document.getElementById('xlsxImportModal').style.display = 'flex';
    _applyPreselectedProgram('xlsxModalProgramCode');
}
function closeXlsxImportModal() {
    document.getElementById('xlsxImportModal').style.display = 'none';
}
function backFromXlsxModal() {
    closeXlsxImportModal();
    openImportTypeModal();
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
    _hideAnalyzeError('xlsxAnalyzeError');

    if (!progCode)  { _showAnalyzeError('xlsxAnalyzeError', 'Please select a target program.'); return; }
    if (!currYear || !/^\d{4}-\d{4}$/.test(currYear)) { _showAnalyzeError('xlsxAnalyzeError', 'Please enter a valid curriculum year (e.g. 2024-2025).'); return; }
    if (!fileInput.files.length) { _showAnalyzeError('xlsxAnalyzeError', 'Please select an Excel file.'); return; }

    const validation = await _validateCurriculumFile(fileInput.files[0], 'xlsx');
    if (!validation.valid) { _showAnalyzeError('xlsxAnalyzeError', validation.error); return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';
    _importSource = 'Excel (XLSX)';

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    for (let i = 0; i < 11; i++) fd.append(`col_${i}`, document.getElementById(`x_col_${i}`).value);
    fd.append('year_level', document.getElementById('xlsxModalYearLevel').value);
    fd.append('semester',   document.getElementById('xlsxModalSemester').value);

    try {
        const res  = await fetch('/admin/curriculum/import/xlsx/analyze', { method: 'POST', body: fd });
        const data = await res.json();
        if (!res.ok || data.error) { _showAnalyzeError('xlsxAnalyzeError', data.error || 'Server error.'); return; }
        const xlsxCheck = _validateExtractedCurriculum(data.subjects || []);
        if (!xlsxCheck.valid) { _showAnalyzeError('xlsxAnalyzeError', xlsxCheck.error); return; }
        _pdfExtractedSubjects = data.subjects || [];
        _pdfProgCode = progCode;
        _pdfCurrYear = currYear;
        closeXlsxImportModal();
        _renderPdfReviewModal(data);
    } catch (err) {
        _showAnalyzeError('xlsxAnalyzeError', 'Network error: ' + err.message);
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
    _applyPreselectedProgram('pdfProgramCode');
}
function closePdfImportModal() {
    document.getElementById('pdfImportModal').style.display = 'none';
}
function backFromPdfModal() {
    closePdfImportModal();
    openImportTypeModal();
}

// ── Word (.docx) upload modal ───────────────────────────────────────────────
function openDocxImportModal() {
    closeImportTypeModal();
    resetDocxColumnCustomization();
    document.getElementById('docxAnalyzeError').style.display = 'none';
    document.getElementById('docxImportModal').style.display = 'flex';
    _applyPreselectedProgram('docxProgramCode');
}
function closeDocxImportModal() {
    document.getElementById('docxImportModal').style.display = 'none';
}
function backFromDocxModal() {
    closeDocxImportModal();
    openImportTypeModal();
}

async function analyzeCsvCurriculum() {
    const progCode  = document.getElementById('modalProgramCode').value;
    const currYear  = document.getElementById('modalCurriculumYear').value.trim();
    const fileInput = document.getElementById('modalFileInput');
    const btn       = document.getElementById('csvAnalyzeBtn');
    _hideAnalyzeError('csvAnalyzeError');

    if (!progCode) { _showAnalyzeError('csvAnalyzeError', 'Please select a target program.'); return; }
    if (!currYear || !/^\d{4}-\d{4}$/.test(currYear)) { _showAnalyzeError('csvAnalyzeError', 'Please enter a valid curriculum year (e.g. 2024-2025).'); return; }
    if (!fileInput.files.length) { _showAnalyzeError('csvAnalyzeError', 'Please select a CSV file.'); return; }

    const validation = await _validateCurriculumFile(fileInput.files[0], 'csv');
    if (!validation.valid) { _showAnalyzeError('csvAnalyzeError', validation.error); return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';
    _importSource = 'CSV';

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    for (let i = 0; i < 11; i++) fd.append(`col_${i}`, document.getElementById(`h_col_${i}`).value);
    fd.append('year_level', document.getElementById('modalYearLevel').value);
    fd.append('semester',   document.getElementById('modalSemester').value);

    try {
        const res  = await fetch('/admin/curriculum/import/csv/analyze', { method: 'POST', body: fd });
        const data = await res.json();
        if (!res.ok || data.error) { _showAnalyzeError('csvAnalyzeError', data.error || 'Server error.'); return; }
        const csvCheck = _validateExtractedCurriculum(data.subjects || []);
        if (!csvCheck.valid) { _showAnalyzeError('csvAnalyzeError', csvCheck.error); return; }
        _pdfExtractedSubjects = data.subjects || [];
        _pdfProgCode = progCode;
        _pdfCurrYear = currYear;
        closeImportModal();
        _renderPdfReviewModal(data);
    } catch (err) {
        _showAnalyzeError('csvAnalyzeError', 'Network error: ' + err.message);
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

// ── Pre-analysis file validation ─────────────────────────────────────────────

const _MAGIC_PDF = [0x25, 0x50, 0x44, 0x46];   // %PDF
const _MAGIC_ZIP = [0x50, 0x4B, 0x03, 0x04];   // PK\x03\x04 — XLSX and DOCX are ZIP-based

const _MAX_IMPORT_FILE_BYTES = 50 * 1024 * 1024;  // 50 MB

function _readFileHeader(file, n) {
    return new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload  = e => resolve(new Uint8Array(e.target.result));
        r.onerror = () => reject(new Error('Cannot read file.'));
        r.readAsArrayBuffer(file.slice(0, n));
    });
}

function _readFileSample(file, n) {
    return new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload  = e => resolve(e.target.result);
        r.onerror = () => reject(new Error('Cannot read file.'));
        r.readAsText(file.slice(0, n));
    });
}

function _magicMatch(bytes, magic) {
    for (let i = 0; i < magic.length; i++) {
        if ((bytes[i] ?? -1) !== magic[i]) return false;
    }
    return true;
}

// Returns { valid: true } or { valid: false, error: '<message>' }
async function _validateCurriculumFile(file, type) {
    if (!file) return { valid: false, error: 'No file selected.' };

    if (file.size === 0)
        return { valid: false, error: 'Invalid curriculum file. The selected file is empty.' };

    if (file.size > _MAX_IMPORT_FILE_BYTES)
        return { valid: false, error: `File too large (${(file.size / 1048576).toFixed(1)} MB). Maximum is 50 MB.` };

    const ext = file.name.toLowerCase().split('.').pop();

    // Extension check
    const extMap = { csv: 'csv', xlsx: 'xlsx', pdf: 'pdf', docx: 'docx' };
    if (ext !== extMap[type])
        return { valid: false, error: `Invalid curriculum file. Please select a valid .${extMap[type]} file.` };

    try {
        // Magic bytes — confirms actual binary format, not just a renamed file
        if (type === 'pdf') {
            const hdr = await _readFileHeader(file, 4);
            if (!_magicMatch(hdr, _MAGIC_PDF))
                return { valid: false, error: 'Invalid curriculum file. The file does not appear to be a real PDF document. Please upload the actual PDF file.' };
        }

        if (type === 'xlsx') {
            const hdr = await _readFileHeader(file, 4);
            if (!_magicMatch(hdr, _MAGIC_ZIP))
                return { valid: false, error: 'Invalid curriculum file. The file does not appear to be a real Excel (.xlsx) document. Please upload the actual .xlsx file.' };
        }

        if (type === 'docx') {
            const hdr = await _readFileHeader(file, 4);
            if (!_magicMatch(hdr, _MAGIC_ZIP))
                return { valid: false, error: 'Invalid curriculum file. The file does not appear to be a real Word (.docx) document. Please upload the actual .docx file.' };
        }

        if (type === 'csv') {
            const sample = await _readFileSample(file, 2048);
            if (!sample.trim())
                return { valid: false, error: 'Invalid curriculum file. The CSV file is empty or contains no readable text.' };
            const rows = sample.split('\n').filter(l => l.trim());
            if (rows.length < 2)
                return { valid: false, error: 'Invalid curriculum file. The CSV must contain a header row and at least one data row.' };
        }
    } catch (_e) {
        return { valid: false, error: 'Could not read the file. It may be corrupted or locked by another application.' };
    }

    return { valid: true };
}

// Unified error display — shows icon + message; used by all four analyze functions
function _showAnalyzeError(boxId, msg) {
    const box = document.getElementById(boxId);
    if (!box) return;
    box.innerHTML = '';
    const icon = document.createElement('i');
    icon.className = 'fas fa-exclamation-circle';
    box.appendChild(icon);
    const txt = document.createElement('span');
    txt.textContent = msg;
    box.appendChild(txt);
    box.style.display = 'flex';
}

function _hideAnalyzeError(boxId) {
    const box = document.getElementById(boxId);
    if (box) { box.innerHTML = ''; box.style.display = 'none'; }
}

// ── Post-extraction content validation ───────────────────────────────────────
// Checks whether data returned by the server actually looks like curriculum subjects.
// Catches "wrong file" situations (e.g. uploading an employee list as a curriculum).

const _EMAIL_RE = /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/;
const _PHONE_RE = /\b09\d{2}[-\s]?\d{3}[-\s]?\d{4}\b|\b\+63\d/;

function _validateExtractedCurriculum(subjects) {
    if (!subjects || subjects.length === 0)
        return {
            valid: false,
            error: 'Invalid curriculum file. No curriculum subjects could be found in this file. Please select the correct curriculum document.'
        };

    // Email addresses anywhere in subject code / name / pre-req / co-req
    // — strong signal that this is a contacts or employee file
    const hasEmail = subjects.some(s =>
        _EMAIL_RE.test(s.sc || '') || _EMAIL_RE.test(s.sn || '') ||
        _EMAIL_RE.test(s.pre || '') || _EMAIL_RE.test(s.co || '')
    );
    if (hasEmail)
        return {
            valid: false,
            error: 'Invalid curriculum file. The file appears to contain employee or personal records, not curriculum subjects. Please upload the correct curriculum document.'
        };

    // Phone numbers in any field — another employee-file signal
    const hasPhone = subjects.some(s =>
        _PHONE_RE.test(s.sc || '') || _PHONE_RE.test(s.sn || '') ||
        _PHONE_RE.test(s.pre || '') || _PHONE_RE.test(s.co || '')
    );
    if (hasPhone)
        return {
            valid: false,
            error: 'Invalid curriculum file. The file appears to contain employee or personal records, not curriculum subjects. Please upload the correct curriculum document.'
        };

    // Year levels must be 1–6; anything higher means the parser picked up
    // a non-year-level number (e.g. a phone number fragment, an ID, a date)
    const badYl = subjects.find(s => s.yl && (s.yl < 0 || s.yl > 6));
    if (badYl)
        return {
            valid: false,
            error: `Invalid curriculum file. An unrecognized year-level value (${badYl.yl}) was detected. This file does not appear to be a curriculum document. Please upload the correct file.`
        };

    return { valid: true };
}

// ── PDF Analysis & Review ───────────────────────────────────────────────────

let _pdfExtractedSubjects = [];
let _pdfProgCode = '';
let _pdfCurrYear = '';
let _importSource = 'File';   // tracks which file type was used for import

async function analyzePdf() {
    const progCode  = document.getElementById('pdfProgramCode').value;
    const currYear  = document.getElementById('pdfCurriculumYear').value.trim();
    const fileInput = document.getElementById('pdfFileInput');
    const btn       = document.getElementById('pdfAnalyzeBtn');
    _hideAnalyzeError('pdfAnalyzeError');

    if (!progCode) { _showPdfError('Please select a target academic offering.'); return; }
    if (!currYear || !/^\d{4}-\d{4}$/.test(currYear)) {
        _showPdfError('Please enter a valid curriculum year (e.g. 2024-2025).');
        return;
    }
    if (!fileInput.files.length) { _showPdfError('Please select a PDF file to upload.'); return; }

    const validation = await _validateCurriculumFile(fileInput.files[0], 'pdf');
    if (!validation.valid) { _showPdfError(validation.error); return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';
    _importSource = 'PDF';

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

        const pdfCheck = _validateExtractedCurriculum(data.subjects || []);
        if (!pdfCheck.valid) { _showPdfError(pdfCheck.error); return; }

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

function _showPdfError(msg) { _showAnalyzeError('pdfAnalyzeError', msg); }

async function analyzeDocx() {
    const progCode  = document.getElementById('docxProgramCode').value;
    const currYear  = document.getElementById('docxCurriculumYear').value.trim();
    const fileInput = document.getElementById('docxFileInput');
    const btn       = document.getElementById('docxAnalyzeBtn');
    _hideAnalyzeError('docxAnalyzeError');

    if (!progCode) { _showDocxError('Please select a target academic offering.'); return; }
    if (!currYear || !/^\d{4}-\d{4}$/.test(currYear)) {
        _showDocxError('Please enter a valid curriculum year (e.g. 2024-2025).');
        return;
    }
    if (!fileInput.files.length) { _showDocxError('Please select a Word (.docx) file to upload.'); return; }

    const validation = await _validateCurriculumFile(fileInput.files[0], 'docx');
    if (!validation.valid) { _showDocxError(validation.error); return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';
    _importSource = 'Word (DOCX)';

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

        const docxCheck = _validateExtractedCurriculum(data.subjects || []);
        if (!docxCheck.valid) { _showDocxError(docxCheck.error); return; }

        _pdfExtractedSubjects = data.subjects || [];
        _pdfProgCode = progCode;
        _pdfCurrYear = currYear;

        closeDocxImportModal();
        _renderPdfReviewModal(data);
    } catch (err) {
        _showDocxError('Network error: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-search"></i> Analyze Document';
    }
}

function _showDocxError(msg) { _showAnalyzeError('docxAnalyzeError', msg); }

function _renderPdfReviewModal(data) {
    const confidence   = data.confidence    || 0;
    const warnings     = data.warnings      || [];
    const rawText      = data.raw_text      || '';
    const skippedRows  = data.skipped_rows   || [];
    const autoExcluded = data.auto_excluded  || [];
    const banner       = document.getElementById('pdfReviewBanner');

    // Populate header meta bar (program / year / source)
    const meta = document.getElementById('pdfReviewMeta');
    if (meta) {
        const progLabel = (function() {
            const sel = document.getElementById('syllabiProgramFilter');
            if (!sel) return _pdfProgCode;
            const opt = Array.from(sel.options).find(o => o.value === _pdfProgCode);
            return opt ? opt.textContent.trim() : _pdfProgCode;
        })();
        meta.innerHTML = `
            <span class="rev-meta-item"><i class="fas fa-graduation-cap"></i> ${_esc(progLabel)}</span>
            <span class="rev-meta-sep">|</span>
            <span class="rev-meta-item"><i class="fas fa-calendar-alt"></i> C.Y ${_esc(_pdfCurrYear)}</span>
            <span class="rev-meta-sep">|</span>
            <span class="rev-meta-item"><i class="fas fa-file-import"></i> ${_esc(_importSource)}</span>`;
    }

    let warnHtml = warnings.length
        ? '<ul class="pdf-warn-list">' + warnings.map(w => `<li>${w}</li>`).join('') + '</ul>'
        : '';

    // Show raw-text panel when nothing was extracted so admin can see what the PDF contains
    let rawHtml = '';
    if (confidence === 0 && rawText.trim()) {
        rawHtml = `
        <details class="pdf-raw-details">
            <summary>Show raw text extracted from file (use this to manually enter subjects)</summary>
            <pre class="pdf-raw-pre">${rawText.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</pre>
        </details>`;
    } else if (confidence === 0 && !rawText.trim()) {
        rawHtml = `<div class="pdf-scanned-warn">
            <i class="fas fa-exclamation-triangle"></i>
            No text could be read from this file. It may be a scanned image.
            Please use a text-based PDF or export the curriculum as CSV instead.
        </div>`;
    }

    const reviewNote = confidence < 75
        ? '<div class="conf-note" style="margin-bottom:6px;"><i class="fas fa-exclamation-circle" style="margin-right:5px;"></i>Please review and correct the extracted data below before importing.</div>'
        : '';

    // Skipped-rows panel: rows that look like real subjects but couldn't be parsed (warning)
    let skippedHtml = '';
    if (skippedRows.length) {
        const rows = skippedRows.map(sk => {
            const cells = (sk.cells || []).map(c => _esc(c)).join(' | ');
            return `<tr><td style="font-family:monospace;font-size:12px;">${cells}</td>`
                 + `<td style="font-size:12px;color:#888;">${_esc(sk.reason)}</td></tr>`;
        }).join('');
        skippedHtml = `
        <details class="pdf-skipped-details" style="margin-top:8px;">
            <summary style="cursor:pointer;font-weight:600;color:#b45309;">
                <i class="fas fa-exclamation-triangle" style="margin-right:5px;"></i>
                ${skippedRows.length} row(s) were detected but not imported — click to review
            </summary>
            <table style="width:100%;margin-top:6px;border-collapse:collapse;font-size:12px;">
                <thead><tr>
                    <th style="text-align:left;padding:4px 6px;background:#fef3c7;">Row content</th>
                    <th style="text-align:left;padding:4px 6px;background:#fef3c7;">Reason skipped</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </details>`;
    }

    // Auto-excluded panel: header/label rows the parser correctly ignored (informational only)
    let autoExcludedHtml = '';
    if (autoExcluded.length) {
        const rows = autoExcluded.map(sk => {
            const cells = (sk.cells || []).map(c => _esc(c)).join(' | ');
            return `<tr><td style="font-family:monospace;font-size:12px;">${cells}</td>`
                 + `<td style="font-size:12px;color:#888;">${_esc(sk.reason)}</td></tr>`;
        }).join('');
        autoExcludedHtml = `
        <details class="pdf-skipped-details" style="margin-top:6px;">
            <summary style="cursor:pointer;font-weight:500;color:#6b7280;font-size:13px;">
                <i class="fas fa-info-circle" style="margin-right:5px;color:#6b7280;"></i>
                ${autoExcluded.length} row(s) were automatically excluded (column headers / semester labels) — click to view
            </summary>
            <table style="width:100%;margin-top:6px;border-collapse:collapse;font-size:12px;">
                <thead><tr>
                    <th style="text-align:left;padding:4px 6px;background:#f3f4f6;">Row content</th>
                    <th style="text-align:left;padding:4px 6px;background:#f3f4f6;">Reason</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </details>`;
    }

    banner.innerHTML = `${reviewNote}${warnHtml}${skippedHtml}${autoExcludedHtml}${rawHtml}`;

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
    const wrapper = document.getElementById('pdfReviewTableWrapper');
    wrapper.innerHTML = '';
    _groupIdxMap = {};

    const total = _pdfExtractedSubjects.length;
    document.getElementById('pdfReviewSubjectCount').textContent =
        `${total} subject${total !== 1 ? 's' : ''} extracted`;

    if (!total) {
        wrapper.innerHTML = '<div class="rev-empty-state"><i class="fas fa-inbox"></i><p>No subjects extracted. Click <strong>Add Row</strong> to add manually.</p></div>';
        return;
    }

    const sorted = _sortedWithIdx();

    // Group: byYear[yl][sem] = [{s, origIdx}, ...]
    const byYear = {};
    sorted.forEach(({ s, origIdx }) => {
        const yl  = s.yl  || 0;
        const sem = s.sem || 'A';
        if (!byYear[yl])      byYear[yl]      = {};
        if (!byYear[yl][sem]) byYear[yl][sem] = [];
        byYear[yl][sem].push({ s, origIdx });
        const key = `${yl}_${sem}`;
        if (!_groupIdxMap[key]) _groupIdxMap[key] = [];
        _groupIdxMap[key].push(origIdx);
    });

    const yearLevels = Object.keys(byYear).map(Number).sort((a, b) => a - b);

    for (const yl of yearLevels) {
        const ylLabel = yl ? (_YL_LABELS[yl] || `Year ${yl}`) : 'Unassigned';
        const sems    = Object.keys(byYear[yl]).sort((a, b) => (_SEM_ORDER[a] ?? 0) - (_SEM_ORDER[b] ?? 0));
        const ylTotal = sems.reduce((n, s) => n + byYear[yl][s].length, 0);

        // ── Year-level banner ──
        const yearSection = document.createElement('div');
        yearSection.className = 'rev-year-section';
        yearSection.innerHTML = `
            <div class="rev-year-banner">
                <span class="rev-year-icon"><i class="fas fa-layer-group"></i></span>
                <span class="rev-year-title">${ylLabel.toUpperCase()}</span>
                <span class="rev-year-total">${ylTotal} subject${ylTotal !== 1 ? 's' : ''}</span>
            </div>`;

        for (const sem of sems) {
            const semLabel = _SEM_LABELS[sem] || sem;
            const rows     = byYear[yl][sem];
            const key      = `${yl}_${sem}`;

            // ── Semester sub-section ──
            const semSection = document.createElement('div');
            semSection.className = 'rev-sem-section';

            // Semester header
            const semHdr = document.createElement('div');
            semHdr.className = 'rev-sem-header';
            semHdr.innerHTML = `
                <span class="rev-sem-title"><i class="fas fa-book-open"></i> ${semLabel}</span>
                <div class="rev-sem-controls">
                    <span class="rev-sem-count">${rows.length} subject${rows.length !== 1 ? 's' : ''}</span>
                    <div class="rev-group-move">
                        <span class="rev-group-move-label">Move all to:</span>
                        <select class="rev-group-sel" id="gYl_${key}">
                            ${[1,2,3,4,5].map(n => `<option value="${n}" ${n == yl ? 'selected' : ''}>${n}</option>`).join('')}
                        </select>
                        <select class="rev-group-sel" id="gSem_${key}">
                            <option value="A" ${sem === 'A' ? 'selected' : ''}>1st</option>
                            <option value="B" ${sem === 'B' ? 'selected' : ''}>2nd</option>
                            <option value="C" ${sem === 'C' ? 'selected' : ''}>Sum</option>
                        </select>
                        <button type="button" class="btn-group-apply" onclick="_applyGroupMove('${key}')">Apply</button>
                    </div>
                </div>`;
            semSection.appendChild(semHdr);

            // Per-semester table with its own header row
            const table  = document.createElement('table');
            table.className = 'pdf-review-table rev-sem-table';
            table.innerHTML = `
                <thead>
                    <tr>
                        <th class="th-code">Subject Code</th>
                        <th class="th-name">Description</th>
                        <th class="th-req">Pre-requisite</th>
                        <th class="th-req">Co-requisite</th>
                        <th class="th-num">Lec</th>
                        <th class="th-num">Lab</th>
                        <th class="th-num">Units</th>
                        <th class="th-num">TH</th>
                        <th class="th-yl">YL</th>
                        <th class="th-sem">Sem</th>
                        <th class="th-del"></th>
                    </tr>
                </thead>`;
            const tbody = document.createElement('tbody');
            rows.forEach(({ s, origIdx }) => tbody.appendChild(_buildReviewRow(s, origIdx)));
            table.appendChild(tbody);
            semSection.appendChild(table);
            yearSection.appendChild(semSection);
        }

        wrapper.appendChild(yearSection);
    }
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

function backFromReviewModal() {
    closePdfReviewModal();
    // Re-open the originating import modal (file input still populated — user can re-analyze or adjust)
    switch (_importSource) {
        case 'CSV':
            document.getElementById('importModal').style.display = 'flex';
            break;
        case 'Excel (XLSX)':
            document.getElementById('xlsxImportModal').style.display = 'flex';
            break;
        case 'PDF':
            document.getElementById('pdfImportModal').style.display = 'flex';
            break;
        case 'Word (DOCX)':
            document.getElementById('docxImportModal').style.display = 'flex';
            break;
        default:
            openImportTypeModal();
    }
}

async function confirmPdfImport() {
    const valid = _pdfExtractedSubjects.filter(s => s.sc && s.sc.trim());
    if (!valid.length) {
        alert('No valid subjects to import. Each row must have a Subject Code.');
        return;
    }

    try {
        const res  = await fetch(`/admin/curriculum/check-duplicate?program_code=${encodeURIComponent(_pdfProgCode)}&curriculum_year=${encodeURIComponent(_pdfCurrYear)}`);
        const data = await res.json();
        if (data.exists) {
            const msg = document.getElementById('duplicateCurrMsg');
            if (msg) msg.textContent = `A curriculum for ${_pdfProgCode} C.Y ${_pdfCurrYear} already exists in the system.`;
            document.getElementById('duplicateCurrModal').style.display = 'flex';
            return;
        }
    } catch (_e) {
        // Network error — proceed anyway; server will catch duplicates
    }

    _doImportConfirm(false);
}

function closeDuplicateCurrModal() {
    document.getElementById('duplicateCurrModal').style.display = 'none';
}

function proceedWithCurrOverride() {
    closeDuplicateCurrModal();
    _doImportConfirm(true);
}

function _doImportConfirm(override) {
    const valid = _pdfExtractedSubjects.filter(s => s.sc && s.sc.trim());
    document.getElementById('pdfConfirmProg').value     = _pdfProgCode;
    document.getElementById('pdfConfirmYear').value     = _pdfCurrYear;
    document.getElementById('pdfConfirmData').value     = JSON.stringify(valid);
    document.getElementById('pdfConfirmSource').value   = _importSource;
    document.getElementById('pdfConfirmOverride').value = override ? '1' : '0';
    document.getElementById('pdfConfirmForm').submit();
}
