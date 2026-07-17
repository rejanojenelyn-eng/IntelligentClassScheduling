let selectedIdsForArchive = [];

// ── Contact Number: numeric-only input ────────────────────────────────────────
function _digitsOnly(el) {
    if (!el) return;
    el.addEventListener('input', () => {
        const cleaned = el.value.replace(/\D/g, '');
        if (cleaned !== el.value) el.value = cleaned;
    });
}
document.addEventListener('DOMContentLoaded', () => {
    _digitsOnly(document.getElementById('contact'));
    _digitsOnly(document.getElementById('edit_contact'));
});

// ── Employee CSV/XLSX/PDF/DOCX analyze & review ───────────────────────────────
let _empExtracted = [];
let _empRawRows   = [];

async function analyzeEmpCsv() {
    const fileInput = document.getElementById('empCsvFileInput');
    const btn       = document.getElementById('empCsvAnalyzeBtn');
    document.getElementById('empCsvError').style.display = 'none';
    if (!fileInput.files.length) { _showEmpError('empCsvError', 'Please select a CSV file.'); return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';
    showLoading('Reading CSV file…');

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    try {
        const res  = await fetch('/admin/faculty/import/csv/analyze', { method: 'POST', body: fd });
        const data = await res.json();
        hideLoading();
        if (!res.ok || data.error) { _showEmpError('empCsvError', data.error || 'Server error.'); return; }
        _empRawRows = data.raw_rows || [];
        const _csvPre = _getEmpPreColMap('csv');
        _empExtracted = _csvPre ? _applyEmpColMapToRows(_empRawRows, _csvPre) : (data.employees || []);
        closeEmpCsvModal();
        _renderEmpReviewModal(data);
    } catch (err) {
        hideLoading();
        _showEmpError('empCsvError', 'Network error: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-search"></i> Analyze CSV';
    }
}

async function analyzeEmpXlsx() {
    const fileInput = document.getElementById('empXlsxFileInput');
    const btn       = document.getElementById('empXlsxAnalyzeBtn');
    document.getElementById('empXlsxError').style.display = 'none';
    if (!fileInput.files.length) { _showEmpError('empXlsxError', 'Please select an Excel (.xlsx) file.'); return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';
    showLoading('Reading Excel file…');

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    try {
        const res  = await fetch('/admin/faculty/import/xlsx/analyze', { method: 'POST', body: fd });
        const data = await res.json();
        hideLoading();
        if (!res.ok || data.error) { _showEmpError('empXlsxError', data.error || 'Server error.'); return; }
        _empRawRows = data.raw_rows || [];
        const _xlsxPre = _getEmpPreColMap('xlsx');
        _empExtracted = _xlsxPre ? _applyEmpColMapToRows(_empRawRows, _xlsxPre) : (data.employees || []);
        closeEmpXlsxModal();
        _renderEmpReviewModal(data);
    } catch (err) {
        hideLoading();
        _showEmpError('empXlsxError', 'Network error: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-search"></i> Analyze Excel';
    }
}

function _showEmpError(elId, msg) {
    const box = document.getElementById(elId);
    box.textContent = msg;
    box.style.display = 'block';
}

// ── Pre-selection column order helpers ────────────────────────────────────────
const _empColOrderDef = ['emp_num','last_name','first_name','middle_name','email','contact','specialization','emp_type','status','designation'];
const _empColOrderLabels = [
    {val:'emp_num',text:'Employee Number'},{val:'last_name',text:'Last Name'},
    {val:'first_name',text:'First Name'},{val:'middle_name',text:'Middle Name'},
    {val:'email',text:'Email'},{val:'contact',text:'Contact Number'},
    {val:'specialization',text:'Specialization'},{val:'emp_type',text:'Employee Type'},
    {val:'status',text:'Employee Status'},{val:'designation',text:'Designation'},
    {val:'skip',text:'— Skip —'},
];

function toggleEmpColCustomization(fmt) {
    const cap = fmt.charAt(0).toUpperCase() + fmt.slice(1);
    document.getElementById(`emp${cap}DefaultColView`).style.display = 'none';
    document.getElementById(`emp${cap}CustomColView`).style.display = 'block';
    const grid = document.getElementById(`emp${cap}MappingGrid`);
    grid.innerHTML = '';
    for (let i = 0; i < 10; i++) {
        const cur = document.getElementById(`emp_${fmt}_col_${i}`).value;
        let html = `<div class="map-item-vert"><label>Col ${i + 1}</label><select onchange="updateEmpHiddenCol('${fmt}',${i},this.value)">`;
        _empColOrderLabels.forEach(opt => {
            html += `<option value="${opt.val}"${cur === opt.val ? ' selected' : ''}>${opt.text}</option>`;
        });
        grid.innerHTML += html + `</select></div>`;
    }
}
function resetEmpColCustomization(fmt) {
    const cap = fmt.charAt(0).toUpperCase() + fmt.slice(1);
    document.getElementById(`emp${cap}DefaultColView`).style.display = 'block';
    document.getElementById(`emp${cap}CustomColView`).style.display = 'none';
    for (let i = 0; i < 10; i++) {
        const el = document.getElementById(`emp_${fmt}_col_${i}`);
        if (el) el.value = _empColOrderDef[i];
    }
}
function updateEmpHiddenCol(fmt, idx, val) {
    document.getElementById(`emp_${fmt}_col_${idx}`).value = val;
}
function _getEmpPreColMap(fmt) {
    const cap = fmt.charAt(0).toUpperCase() + fmt.slice(1);
    if (document.getElementById(`emp${cap}CustomColView`).style.display === 'none') return null;
    const cm = {};
    for (let i = 0; i < 10; i++) {
        const v = document.getElementById(`emp_${fmt}_col_${i}`).value;
        if (v && v !== 'skip') cm[v] = i;
    }
    return Object.keys(cm).length ? cm : null;
}

// ── Column Mapping Modal ──────────────────────────────────────────────────────
const _EMP_FIELD_LABELS = {
    emp_num:        'Employee Number',
    last_name:      'Last Name',
    first_name:     'First Name',
    middle_name:    'Middle Name',
    email:          'Email',
    contact:        'Contact Number',
    specialization: 'Specialization',
    emp_type:       'Employee Type',
    status:         'Employee Status',
    designation:    'Designation',
};

function _applyEmpColMapToRows(rawRows, colMap) {
    const fields = ['emp_num','last_name','first_name','middle_name','email','contact','specialization','emp_type','status','designation'];
    const result = [];
    for (const row of rawRows) {
        if (!row.some(c => (c || '').trim())) continue;
        const emp = {};
        for (const f of fields) {
            const idx = colMap[f];
            const raw = (idx !== undefined && idx < row.length) ? (row[idx] || '') : '';
            emp[f] = _normCell(raw);
        }
        if (!emp.emp_num && !emp.last_name) continue;
        result.push(emp);
    }
    return result;
}

async function analyzeEmpPdf() {
    const fileInput = document.getElementById('empPdfFileInput');
    const btn       = document.getElementById('empPdfAnalyzeBtn');
    document.getElementById('empPdfError').style.display = 'none';
    if (!fileInput.files.length) { _showEmpError('empPdfError', 'Please select a PDF file.'); return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';
    showLoading('Reading PDF file…');

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    try {
        const res  = await fetch('/admin/faculty/import/pdf/analyze', { method: 'POST', body: fd });
        const data = await res.json();
        hideLoading();
        if (!res.ok || data.error) { _showEmpError('empPdfError', data.error || 'Server error.'); return; }
        _empRawRows = data.raw_rows || [];
        const _pdfPre = _getEmpPreColMap('pdf');
        _empExtracted = _pdfPre ? _applyEmpColMapToRows(_empRawRows, _pdfPre) : (data.employees || []);
        closeEmpPdfModal();
        _renderEmpReviewModal(data);
    } catch (err) {
        hideLoading();
        _showEmpError('empPdfError', 'Network error: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-search"></i> Analyze PDF';
    }
}

async function analyzeEmpDocx() {
    const fileInput = document.getElementById('empDocxFileInput');
    const btn       = document.getElementById('empDocxAnalyzeBtn');
    document.getElementById('empDocxError').style.display = 'none';
    if (!fileInput.files.length) { _showEmpError('empDocxError', 'Please select a Word (.docx) file.'); return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analyzing...';
    showLoading('Reading Word document…');

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    try {
        const res  = await fetch('/admin/faculty/import/docx/analyze', { method: 'POST', body: fd });
        const data = await res.json();
        hideLoading();
        if (!res.ok || data.error) { _showEmpError('empDocxError', data.error || 'Server error.'); return; }
        _empRawRows = data.raw_rows || [];
        const _docxPre = _getEmpPreColMap('docx');
        _empExtracted = _docxPre ? _applyEmpColMapToRows(_empRawRows, _docxPre) : (data.employees || []);
        closeEmpDocxModal();
        _renderEmpReviewModal(data);
    } catch (err) {
        hideLoading();
        _showEmpError('empDocxError', 'Network error: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-search"></i> Analyze Document';
    }
}

function _renderEmpReviewModal(data) {
    const confidence = data.confidence || 0;
    const warnings   = data.warnings   || [];
    const rawText    = data.raw_text   || '';
    const banner     = document.getElementById('empReviewBanner');
    const badgeClass = confidence >= 75 ? 'conf-high' : confidence >= 40 ? 'conf-medium' : 'conf-low';

    let warnHtml = warnings.length
        ? '<ul class="pdf-warn-list">' + warnings.map(w => `<li>${w}</li>`).join('') + '</ul>'
        : '';

    let rawHtml = '';
    if (confidence === 0 && rawText.trim()) {
        const escaped = rawText.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        rawHtml = `<details class="pdf-raw-details">
            <summary>Show raw text from document (use this to check column headers)</summary>
            <pre class="pdf-raw-pre">${escaped}</pre>
        </details>`;
    } else if (confidence === 0 && !rawText.trim()) {
        rawHtml = `<div class="pdf-scanned-warn">
            <i class="fas fa-exclamation-triangle"></i>
            No text could be read. The PDF may be a scanned image.
        </div>`;
    }

    banner.innerHTML = `
        <div class="pdf-conf-row">
            <span class="conf-badge ${badgeClass}">Extraction Confidence: ${confidence}%</span>
            ${confidence < 75 ? '<span class="conf-note">Please review the data below before importing.</span>' : ''}
        </div>${warnHtml}${rawHtml}`;

    _rebuildEmpReviewTable();
    document.getElementById('empReviewModal').style.display = 'flex';
}

function _esc(v) {
    return String(v || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Mirrors the Python _fix_run_splits logic: removes spurious mid-word spaces
// caused by DOCX run splits (e.g. "Tempora ry" → "Temporary").
const _NORM_SUFFIX_RE = /([A-Za-z]) (tion|sion|ment|ness|ary|ery|ory|ive|ful|ism|ist|ity|age|ogy|ics|ies|ees|ers|ons|ings|ry|ty|ny|gy|cy|dy|py|nt|nd|ng|ct|pt|lt|st|xt|ss|ff|ll|al|el|er|or|ar|ed|en|es|rs|ts|ns|ls|ds|ee|oo)(?=[ \t,;.:()\-\/']|$)/gi;
function _normCell(text) {
    if (!text) return '';
    let s = String(text).replace(/\s+/g, ' ').trim();
    for (let i = 0; i < 6; i++) {
        const prev = s;
        s = s.replace(_NORM_SUFFIX_RE, (_, a, b) => a + b);
        if (s === prev) break;
    }
    return s.replace(/\s+/g, ' ').trim();
}

function _rebuildEmpReviewTable() {
    const tbody = document.getElementById('empReviewTableBody');
    tbody.innerHTML = '';
    _empExtracted.forEach((emp, i) => tbody.appendChild(_buildEmpRow(emp, i)));
    document.getElementById('empReviewCount').textContent =
        `${_empExtracted.length} employee${_empExtracted.length !== 1 ? 's' : ''} extracted`;
}

function _buildEmpRow(emp, idx) {
    const tr = document.createElement('tr');
    const fields = ['emp_num','last_name','first_name','middle_name','email','contact','specialization','emp_type','status','designation'];
    const missing = !emp.emp_num && !emp.last_name;
    if (missing) tr.classList.add('row-warning');

    tr.innerHTML = fields.map(f => {
        const raw = _normCell(emp[f]);
        return `<td><input class="rev-input ${(!emp.emp_num && f==='emp_num') ? 'rev-missing' : ''}" type="text"
            value="${_esc(raw)}" placeholder="${f.replace(/_/g,' ')}"
            onchange="_updateEmpField(${idx},'${f}',_normCell(this.value))"></td>`;
    }).join('') + `
        <td><button type="button" class="btn-row-delete" onclick="_deleteEmpRow(${idx})" title="Remove">
            <i class="fas fa-times"></i></button></td>`;
    return tr;
}

function _updateEmpField(idx, field, value) {
    if (_empExtracted[idx]) _empExtracted[idx][field] = value;
}

function _deleteEmpRow(idx) {
    _empExtracted.splice(idx, 1);
    _rebuildEmpReviewTable();
}

function addEmpReviewRow() {
    _empExtracted.push({ emp_num:'', last_name:'', first_name:'', middle_name:'',
        email:'', contact:'', specialization:'', emp_type:'', status:'Permanent', designation:'' });
    _rebuildEmpReviewTable();
    const rows = document.querySelectorAll('#empReviewTableBody tr');
    if (rows.length) rows[rows.length - 1].scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// ── Conflict modal state ──────────────────────────────────────────────────────
let _conflictData       = { active: [], archived: [] };
let _archivedDecisions  = {};   // emp_num → { action: 'restore'|'skip', archiveid }

async function confirmEmpImport() {
    const valid = _empExtracted.filter(e => e.emp_num && e.emp_num.trim());
    if (!valid.length) {
        _showImportAlert('No valid employees to import. Each row must have an Employee Number.');
        return;
    }
    const empNums = valid.map(e => e.emp_num.trim());
    try {
        const resp = await fetch('/admin/faculty/import/check-duplicates', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ emp_nums: empNums }),
        });
        const dup = await resp.json();
        if ((dup.archived || []).length > 0 || (dup.active || []).length > 0) {
            _conflictData      = dup;
            _archivedDecisions = {};
            (dup.archived || []).forEach(e => {
                _archivedDecisions[e.emp_num] = { action: 'skip', archiveid: e.archiveid };
            });
            _openConflictModal();
            return;
        }
    } catch (e) { console.warn('Duplicate check failed, proceeding:', e); }
    _submitImport(_empExtracted.filter(e => e.emp_num && e.emp_num.trim()));
}

function _openConflictModal() {
    const archived = _conflictData.archived || [];
    const active   = _conflictData.active   || [];
    const total    = archived.length + active.length;

    document.getElementById('conflictSummaryText').textContent =
        `${total} conflict${total !== 1 ? 's' : ''} found — review each before continuing.`;

    const list = document.getElementById('conflictCardList');
    list.innerHTML = '';

    archived.forEach(emp => {
        const div = document.createElement('div');
        div.className = 'conflict-card archived-conflict decided-skip';
        div.id = `ccard-${emp.emp_num}`;
        div.innerHTML = `
            <div class="conflict-card-info">
                <span class="conflict-card-badge badge-archived-emp">IN ARCHIVE</span>
                <div class="conflict-card-name">${_esc(emp.name || emp.emp_num)}</div>
                <div class="conflict-card-meta">
                    <span><i class="fas fa-id-badge"></i>${_esc(emp.emp_num)}</span>
                    ${emp.typename ? `<span><i class="fas fa-briefcase"></i>${_esc(emp.typename)}</span>` : ''}
                    ${emp.status   ? `<span><i class="fas fa-circle"></i>${_esc(emp.status)}</span>`   : ''}
                </div>
                <div class="conflict-card-note">This employee exists in the archive. Choose an action:</div>
            </div>
            <div class="conflict-card-actions" id="cact-${emp.emp_num}">
                <button class="btn-conflict-restore" onclick="_markRestore('${emp.emp_num}',${emp.archiveid})">
                    <i class="fas fa-undo-alt"></i> Restore Employee
                </button>
                <button class="btn-conflict-skip" onclick="_markSkip('${emp.emp_num}',${emp.archiveid})">
                    <i class="fas fa-forward"></i> Skip Import
                </button>
            </div>`;
        list.appendChild(div);
    });

    active.forEach(emp => {
        const div = document.createElement('div');
        div.className = 'conflict-card active-conflict';
        div.innerHTML = `
            <div class="conflict-card-info">
                <span class="conflict-card-badge badge-active-emp">ACTIVE EMPLOYEE</span>
                <div class="conflict-card-name">${_esc(emp.name || emp.emp_num)}</div>
                <div class="conflict-card-meta">
                    <span><i class="fas fa-id-badge"></i>${_esc(emp.emp_num)}</span>
                    ${emp.typename ? `<span><i class="fas fa-briefcase"></i>${_esc(emp.typename)}</span>` : ''}
                    ${emp.status   ? `<span><i class="fas fa-circle"></i>${_esc(emp.status)}</span>`   : ''}
                </div>
                <div class="conflict-card-note">
                    This employee already exists in the active employee list. Import will be skipped automatically.
                </div>
            </div>
            <div class="conflict-auto-skip"><i class="fas fa-ban"></i> Will be skipped</div>`;
        list.appendChild(div);
    });

    const btn = document.getElementById('conflictContinueBtn');
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-arrow-right"></i> Continue Import';
    document.getElementById('importConflictModal').style.display = 'flex';
}

function _markRestore(empNum, archiveid) {
    _archivedDecisions[empNum] = { action: 'restore', archiveid };
    const card = document.getElementById(`ccard-${empNum}`);
    const acts = document.getElementById(`cact-${empNum}`);
    if (card) card.className = 'conflict-card archived-conflict decided-restore';
    if (acts) acts.innerHTML = `
        <span class="conflict-card-badge badge-restored-emp"><i class="fas fa-check"></i> Will be Restored</span>
        <button class="btn-conflict-skip" style="margin-top:5px;" onclick="_markSkip('${empNum}',${archiveid})">
            Undo — Skip Instead
        </button>`;
}

function _markSkip(empNum, archiveid) {
    _archivedDecisions[empNum] = { action: 'skip', archiveid };
    const card = document.getElementById(`ccard-${empNum}`);
    const acts = document.getElementById(`cact-${empNum}`);
    if (card) card.className = 'conflict-card archived-conflict decided-skip';
    if (acts) acts.innerHTML = `
        <span class="conflict-card-badge badge-skipped-emp"><i class="fas fa-forward"></i> Will be Skipped</span>
        <button class="btn-conflict-restore" style="margin-top:5px;" onclick="_markRestore('${empNum}',${archiveid})">
            Undo — Restore Instead
        </button>`;
}

function _cancelConflict() {
    document.getElementById('importConflictModal').style.display = 'none';
}

async function _proceedAfterConflict() {
    const btn = document.getElementById('conflictContinueBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing…';

    const allConflictNums = new Set([
        ...(_conflictData.archived || []).map(e => e.emp_num),
        ...(_conflictData.active   || []).map(e => e.emp_num),
    ]);

    // Restore archived employees that were marked for restore
    const toRestore = Object.entries(_archivedDecisions)
        .filter(([, v]) => v.action === 'restore' && v.archiveid);

    for (const [, v] of toRestore) {
        try {
            await fetch('/admin/faculty/import/restore-archived', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ archive_id: v.archiveid }),
            });
        } catch (e) { console.warn('Restore failed:', e); }
    }

    _cancelConflict();
    const remaining = _empExtracted.filter(e => !allConflictNums.has((e.emp_num || '').trim()));
    if (!remaining.length) {
        _showImportAlert('All employees in the import list are conflicts. Nothing new to import.');
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-arrow-right"></i> Continue Import';
        return;
    }
    _submitImport(remaining);
}

function _submitImport(employees) {
    const _fields = ['emp_num','last_name','first_name','middle_name',
                     'email','contact','specialization','emp_type','status','designation'];
    const cleaned = employees.map(emp => {
        const out = {};
        _fields.forEach(f => { out[f] = _normCell(emp[f] || ''); });
        return out;
    });
    document.getElementById('empConfirmData').value = JSON.stringify(cleaned);
    showLoading('Saving employees to database…');
    document.getElementById('empConfirmForm').submit();
}

function _showImportAlert(msg) {
    const banner = document.getElementById('empReviewBanner');
    if (!banner) { alert(msg); return; }
    const el = document.createElement('div');
    el.className = 'pdf-analyze-error';
    el.style.marginTop = '8px';
    el.textContent = msg;
    banner.appendChild(el);
    setTimeout(() => el.remove(), 6000);
}

function updateBulkActions() {
    const checked = document.querySelectorAll('.row-check:checked');
    const n = checked.length;
    const countEl = document.getElementById('selectedCount');
    const labelEl = document.getElementById('selectedLabel');
    const row = document.getElementById('bulkActionsRow');
    if (countEl) countEl.textContent = n;
    if (labelEl) labelEl.textContent = n === 1 ? 'employee' : 'employees';
    if (row) row.style.display = n > 0 ? 'flex' : 'none';
    const all = document.querySelectorAll('.row-check');
    const sa = document.getElementById('selectAll');
    if (sa) {
        sa.checked = n === all.length && all.length > 0;
        sa.indeterminate = n > 0 && n < all.length;
    }
}

function clearSelection() {
    document.querySelectorAll('.row-check').forEach(cb => cb.checked = false);
    const sa = document.getElementById('selectAll');
    if (sa) { sa.checked = false; sa.indeterminate = false; }
    updateBulkActions();
}

document.getElementById("selectAll").addEventListener("change", function() {
    document.querySelectorAll('.row-check').forEach(cb => cb.checked = this.checked);
    updateBulkActions();
});

document.addEventListener('change', (e) => {
    if (e.target.classList.contains('row-check')) updateBulkActions();
});

// --- LOADING OVERLAY ---
const _LOA_STEPS = [
    'Reading file…',
    'Detecting columns…',
    'Extracting records…',
    'Validating data…',
    'Preparing results…',
];
const _LOA_PROGRESS = [12, 32, 58, 78, 92];
let _loaTimer = null, _loaStep = 0;

function showLoading(firstMsg) {
    const ov  = document.getElementById('loadingOverlay');
    if (!ov) return;
    ov.style.display = 'flex';
    _loaStep = 0;
    _setLoaStep(firstMsg || _LOA_STEPS[0], _LOA_PROGRESS[0]);
    _loaTimer = setInterval(() => {
        _loaStep = Math.min(_loaStep + 1, _LOA_STEPS.length - 1);
        _setLoaStep(_LOA_STEPS[_loaStep], _LOA_PROGRESS[_loaStep]);
    }, 1600);
}
function _setLoaStep(msg, pct) {
    const txt = document.getElementById('loadingStatusText');
    const bar = document.getElementById('loadingBarFill');
    if (txt) { txt.style.opacity = '0'; setTimeout(() => { txt.textContent = msg; txt.style.opacity = '1'; }, 220); }
    if (bar) bar.style.width = (pct || 0) + '%';
}
function hideLoading() {
    const ov = document.getElementById('loadingOverlay');
    if (ov) ov.style.display = 'none';
    if (_loaTimer) { clearInterval(_loaTimer); _loaTimer = null; }
    const bar = document.getElementById('loadingBarFill');
    if (bar) bar.style.width = '0%';
}
window.addEventListener('pageshow', hideLoading);

// --- IMPORT TYPE SELECTOR ---
let _currentImportFmt = null;  // tracks which format modal opened the review

function openImportTypeModal()  { document.getElementById('importTypeModal').style.display = 'flex'; }
function closeImportTypeModal() { document.getElementById('importTypeModal').style.display = 'none'; }
function openImportModal() { openImportTypeModal(); }  // legacy alias

// CSV
function openEmpCsvModal()  {
    _currentImportFmt = 'csv';
    closeImportTypeModal();
    resetEmpColCustomization('csv');
    document.getElementById('empCsvError').style.display = 'none';
    document.getElementById('empCsvModal').style.display = 'flex';
}
function closeEmpCsvModal() { document.getElementById('empCsvModal').style.display = 'none'; }

// XLSX
function openEmpXlsxModal()  {
    _currentImportFmt = 'xlsx';
    closeImportTypeModal();
    resetEmpColCustomization('xlsx');
    document.getElementById('empXlsxError').style.display = 'none';
    document.getElementById('empXlsxModal').style.display = 'flex';
}
function closeEmpXlsxModal() { document.getElementById('empXlsxModal').style.display = 'none'; }

// PDF
function openEmpPdfModal()  {
    _currentImportFmt = 'pdf';
    closeImportTypeModal();
    resetEmpColCustomization('pdf');
    document.getElementById('empPdfError').style.display = 'none';
    document.getElementById('empPdfModal').style.display = 'flex';
}
function closeEmpPdfModal() { document.getElementById('empPdfModal').style.display = 'none'; }

// DOCX
function openEmpDocxModal()  {
    _currentImportFmt = 'docx';
    closeImportTypeModal();
    resetEmpColCustomization('docx');
    document.getElementById('empDocxError').style.display = 'none';
    document.getElementById('empDocxModal').style.display = 'flex';
}
function closeEmpDocxModal() { document.getElementById('empDocxModal').style.display = 'none'; }

// Review — Back button returns to the format modal that was open before
function closeEmpReviewModal() {
    document.getElementById('empReviewModal').style.display = 'none';
    const prevModal = { csv: 'empCsvModal', xlsx: 'empXlsxModal', pdf: 'empPdfModal', docx: 'empDocxModal' };
    const mid = _currentImportFmt && prevModal[_currentImportFmt];
    if (mid) document.getElementById(mid).style.display = 'flex';
}

// --- MODAL CONTROLS ---
function openAddModal() { document.getElementById("addEmployeeModal").style.display = "block"; }
function closeAddModal() { document.getElementById("addEmployeeModal").style.display = "none"; }
function closeImportModal() { closeImportTypeModal(); }

function openEditFromEl(el) {
    const emp = JSON.parse(el.dataset.emp);
    openEditModal(emp.employeenumber, emp.firstname, emp.middlename || '', emp.lastname,
                  emp.email, emp.contactnumber, emp.specializationid,
                  emp.employeetypeid, emp.designationid || '', emp.employeestatus);
}

function openEditModal(empNum, fName, mName, lName, email, contact, specId, typeId, desigId, status) {
    document.getElementById("edit_emp_num").value = empNum;
    document.getElementById("edit_f_name").value = fName;
    document.getElementById("edit_m_name").value = mName;
    document.getElementById("edit_l_name").value = lName;
    document.getElementById("edit_email").value = email;
    document.getElementById("edit_contact").value = contact;
    document.getElementById("edit_spec").value = specId;
    document.getElementById("edit_type").value = typeId;
    document.getElementById("edit_designation").value = desigId;
    document.getElementById("edit_status").value = status;
    toggleDesignation('edit');
    document.getElementById("editEmployeeModal").style.display = "block";
}
function closeEditModal() { document.getElementById("editEmployeeModal").style.display = "none"; }

function toggleDesignation(modalType) {
    const typeSelect = document.getElementById(`${modalType}_type`);
    const designationSelect = document.getElementById(`${modalType}_designation`);
    const selectedOption = typeSelect.options[typeSelect.selectedIndex];
    const employeeTypeName = selectedOption.getAttribute('data-typename');
    designationSelect.disabled = (employeeTypeName !== 'Designee');
    if (designationSelect.disabled) designationSelect.value = "";
}

// --- SINGLE / BULK ARCHIVE ---
function openArchiveModal(empNum) {
    document.getElementById("confirmArchiveBtn").href = `/admin/archive_employee/${empNum}`;
    document.getElementById("archiveConfirmModal").style.display = "block";
}
function closeArchiveModal() { document.getElementById("archiveConfirmModal").style.display = "none"; }

function applyBulkArchive() {
    selectedIdsForArchive = Array.from(document.querySelectorAll('.row-check:checked')).map(cb => cb.value);
    document.getElementById('bulkArchiveCount').textContent = selectedIdsForArchive.length;
    document.getElementById('bulkArchiveConfirmModal').style.display = 'block';
}
function closeBulkArchiveModal() { document.getElementById('bulkArchiveConfirmModal').style.display = 'none'; }

document.getElementById('confirmBulkArchiveBtn').addEventListener('click', function() {
    fetch('/admin/bulk_archive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ employee_ids: selectedIdsForArchive })
    }).then(res => res.json()).then(data => {
        if (data.success) window.location.reload();
        else alert("Error archiving");
    });
});

// --- FILTER ---
function filterTable() {
    const searchInput  = document.getElementById("searchInput").value.toUpperCase();
    const typeInput    = document.getElementById("typeFilter").value.toUpperCase();
    const statusInput  = document.getElementById("statusFilter").value.toUpperCase();
    const specInput    = document.getElementById("specFilter").value.toUpperCase();

    const tbody = document.getElementById("instructorTable").getElementsByTagName("tbody")[0];
    const tr    = tbody.getElementsByTagName("tr");

    for (let i = 0; i < tr.length; i++) {
        const nameTxt   = (tr[i].querySelector(".emp-name")?.textContent   || '').toUpperCase();
        const empNumTxt = (tr[i].cells[1]?.textContent                     || '').toUpperCase();
        const specTxt   = (tr[i].querySelector(".emp-spec")?.textContent   || '').toUpperCase();
        const typeTxt   = (tr[i].querySelector(".emp-type")?.textContent   || '').toUpperCase();
        const statusTxt = (tr[i].querySelector(".emp-status")?.textContent || '').toUpperCase();

        const matchSearch = nameTxt.includes(searchInput) || empNumTxt.includes(searchInput);
        const matchType   = typeInput   === "" || typeTxt.trim()   === typeInput;
        const matchStatus = statusInput === "" || statusTxt.trim() === statusInput;
        const matchSpec   = specInput   === "" || specTxt.trim()   === specInput;

        tr[i].style.display = (matchSearch && matchType && matchStatus && matchSpec) ? "" : "none";
    }
}

// --- SORT ---
function sortTable() {
    const sortDirectionSpan = document.getElementById("sortDirection");
    const icon  = document.getElementById("sortIcon");
    const tbody = document.getElementById("instructorTable").querySelector("tbody");
    const rows  = Array.from(tbody.querySelectorAll("tr"));
    const currentText = sortDirectionSpan.innerText.trim();
    const direction = (currentText === 'Ascending') ? -1 : 1;

    rows.sort((a, b) => {
        const nameA = a.querySelector(".emp-name").innerText.toLowerCase();
        const nameB = b.querySelector(".emp-name").innerText.toLowerCase();
        return nameA.localeCompare(nameB) * direction;
    });

    if (direction === -1) {
        sortDirectionSpan.innerText = 'Descending';
        icon.className = 'fas fa-sort-amount-up';
    } else {
        sortDirectionSpan.innerText = 'Ascending';
        icon.className = 'fas fa-sort-amount-down-alt';
    }

    rows.forEach(row => tbody.appendChild(row));
}

// ── EXPORT ENGINE ────────────────────────────────────────────────────────────
const _EMP_DOCX_ROUTE = '/admin/employee/export/docx';

function _getExportRows() {
    const table = document.querySelector('.employee-table');
    if (!table) return [];
    const checked = Array.from(table.querySelectorAll('.row-check:checked'));
    if (checked.length > 0) return checked.map(cb => cb.closest('tr'));
    return Array.from(table.querySelectorAll('tbody tr')).filter(r => r.style.display !== 'none');
}

function _rowsToEmpData(rows) {
    // Admin table has Contact column (9 cells); detect by cell count
    const hasContact = rows.length > 0 && rows[0].cells.length >= 9;
    return rows.map(row => ({
        emp_num: row.cells[1]?.innerText.trim() || '',
        name:    row.querySelector('.emp-name')?.innerText.trim()   || '',
        spec:    row.querySelector('.emp-spec')?.innerText.trim()   || '',
        email:   row.cells[4]?.innerText.trim() || '',
        contact: hasContact ? (row.cells[5]?.innerText.trim() || '') : '',
        type:    row.querySelector('.emp-type')?.innerText.trim()   || '',
        status:  row.querySelector('.emp-status')?.innerText.trim() || '',
    }));
}

function _buildExportFilename() {
    const raw = (document.getElementById('exportFilenameInput').value.trim() || 'Employee_Records')
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

function openExportModal() {
    const rows = _getExportRows();
    const checked = document.querySelector('.employee-table')?.querySelectorAll('.row-check:checked') || [];
    document.getElementById('exportEmpCount').textContent = rows.length;
    document.getElementById('exportScopeLabel').textContent = checked.length > 0 ? 'Selected employees' : 'All visible employees';
    document.getElementById('exportFormatError').style.display = 'none';
    document.getElementById('exportModal').style.display = 'flex';
}
function closeExportModal() { document.getElementById('exportModal').style.display = 'none'; }

function toggleFormatCard(el) {
    el.classList.toggle('selected');
    const all = document.querySelectorAll('.emp-export-format-card');
    const allSel = Array.from(all).every(c => c.classList.contains('selected'));
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
    document.getElementById('exportLoadingText').textContent = text || 'Exporting...';
    document.getElementById('exportLoadingSubText').textContent = sub || 'Please wait';
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

function _exportCSV(data, filename) {
    const hasContact = data.some(e => e.contact);
    const now = new Date().toLocaleString();
    const headers = ['Employee Number', 'Employee Name', 'Specialization', 'Email'];
    if (hasContact) headers.push('Contact');
    headers.push('Employment Type', 'Status');
    const dataRows = data.map(e => {
        const r = [e.emp_num, e.name, e.spec, e.email];
        if (hasContact) r.push(e.contact);
        r.push(e.type, e.status);
        return r;
    });
    const q = v => `"${String(v).replace(/"/g, '""')}"`;
    const lines = [
        `"Employee Records"`,
        `"Generated: ${now}"`,
        `"Total: ${data.length} employee(s)"`,
        '',
        headers.map(q).join(','),
        ...dataRows.map(r => r.map(q).join(',')),
    ];
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.csv' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function _exportXLSX(data, filename) {
    const payload = {
        employees: data,
        title: 'Employee Records',
        timestamp: 'Generated: ' + new Date().toLocaleString() + '  |  Total: ' + data.length + ' employee(s)',
    };
    const res = await fetch('/admin/employee/export/xlsx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
    const hasContact = data.some(e => e.contact);
    const now = new Date();
    const genStr = 'Generated: ' + now.toLocaleString();

    doc.setTextColor(0, 0, 0);
    doc.setFont('helvetica', 'bold'); doc.setFontSize(17);
    doc.text('Employee Records', 148.5, 14, { align: 'center' });
    doc.setFont('helvetica', 'normal'); doc.setFontSize(9);
    doc.text(genStr, 148.5, 21, { align: 'center' });

    const columns = [
        { header: '#',               dataKey: 'no'      },
        { header: 'Employee Number', dataKey: 'emp_num' },
        { header: 'Employee Name',   dataKey: 'name'    },
        { header: 'Specialization',  dataKey: 'spec'    },
        { header: 'Email',           dataKey: 'email'   },
    ];
    if (hasContact) columns.push({ header: 'Contact', dataKey: 'contact' });
    columns.push({ header: 'Type', dataKey: 'type' }, { header: 'Status', dataKey: 'status' });

    const body = data.map((e, i) => {
        const row = { no: i + 1, emp_num: e.emp_num, name: e.name, spec: e.spec, email: e.email };
        if (hasContact) row.contact = e.contact;
        row.type = e.type; row.status = e.status;
        return row;
    });

    doc.autoTable({
        columns, body, startY: 27,
        styles: { fontSize: 8, cellPadding: 3, overflow: 'linebreak', lineColor: [0, 0, 0], lineWidth: 0.2, textColor: [0, 0, 0] },
        headStyles: { fillColor: [255, 255, 255], textColor: [0, 0, 0], fontStyle: 'bold', lineColor: [0, 0, 0], lineWidth: 0.2 },
        alternateRowStyles: { fillColor: [255, 255, 255] },
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
        title: 'Employee Records',
        timestamp: 'Generated: ' + new Date().toLocaleString(),
    };
    const res = await fetch(_EMP_DOCX_ROUTE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await res.text() || 'Server error generating DOCX');
    const blob = await res.blob();
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: filename + '.docx' });
    a.click(); URL.revokeObjectURL(a.href);
}

async function executeExport() {
    const selected = Array.from(document.querySelectorAll('.emp-export-format-card.selected'))
        .map(c => c.dataset.format);
    if (selected.length === 0) {
        document.getElementById('exportFormatError').style.display = 'block'; return;
    }
    document.getElementById('exportFormatError').style.display = 'none';

    const rows = _getExportRows();
    if (rows.length === 0) {
        _showExportToast('error', 'No Data', 'There are no employees to export.'); return;
    }
    const data     = _rowsToEmpData(rows);
    const filename = _buildExportFilename();
    const btn      = document.getElementById('exportConfirmBtn');
    btn.disabled   = true;

    closeExportModal();
    _showExportLoading('Preparing Export', `Generating ${selected.length} file(s)…`);

    const errors = [];
    for (const fmt of selected) {
        _showExportLoading(`Exporting ${fmt.toUpperCase()}`, `Processing ${data.length} employees…`);
        try {
            if (fmt === 'csv')  _exportCSV(data, filename);
            if (fmt === 'xlsx') await _exportXLSX(data, filename);
            if (fmt === 'pdf')  await _exportPDF(data, filename);
            if (fmt === 'docx') await _exportDOCX(data, filename);
            await new Promise(r => setTimeout(r, 400));
        } catch (e) {
            console.error(`Export ${fmt} error:`, e);
            errors.push(fmt.toUpperCase() + ': ' + e.message);
        }
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

// ── Faculty Schedule / Faculty Load popup (eye icon) ──────────────────────────
function openFacultyPopup(empNum, name) {
    const modal = document.getElementById('facultyPopupModal');
    if (!modal) return;
    document.getElementById('facPopupName').textContent = name || 'Faculty';
    modal.dataset.empNum = empNum;
    _facSwitchTab('schedule');
    modal.style.display = 'block';
    _loadFacultySchedule(empNum);
    _loadFacultyLoad(empNum);
}

function closeFacultyPopup() {
    const modal = document.getElementById('facultyPopupModal');
    if (modal) modal.style.display = 'none';
}

function _facSwitchTab(tab) {
    document.querySelectorAll('.fac-tab-btn').forEach(b => b.classList.toggle('active', b.dataset.facTab === tab));
    document.getElementById('facTabSchedule').style.display = tab === 'schedule' ? '' : 'none';
    document.getElementById('facTabLoad').style.display     = tab === 'load'     ? '' : 'none';
}

const _FAC_DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

async function _loadFacultySchedule(empNum) {
    const el = document.getElementById('facScheduleContent');
    el.innerHTML = '<div class="fac-loading">Loading schedule...</div>';
    try {
        const params = new URLSearchParams({ emp_num: empNum });
        if (ADMIN_ACTIVE_AY_ID) params.set('ay_id', ADMIN_ACTIVE_AY_ID);
        if (ADMIN_ACTIVE_SEM)   params.set('semester', ADMIN_ACTIVE_SEM);
        const res  = await fetch(`/api/manual/faculty_schedule?${params}`);
        const rows = await res.json();
        if (!Array.isArray(rows) || rows.length === 0) {
            el.innerHTML = '<div class="fac-empty">No published or draft sessions for the current semester.</div>';
            return;
        }
        rows.sort((a, b) => _FAC_DAY_ORDER.indexOf(a.daydesc) - _FAC_DAY_ORDER.indexOf(b.daydesc));
        el.innerHTML = `
            <table class="fac-sched-table">
                <thead><tr><th>Day</th><th>Time</th><th>Subject</th><th>Section</th><th>Room</th><th>Status</th></tr></thead>
                <tbody>
                    ${rows.map(r => `
                        <tr>
                            <td>${r.daydesc || '-'}</td>
                            <td>${r.starttime || '-'} - ${r.endtime || '-'}</td>
                            <td>${r.subjectcode || ''} ${r.subjectname ? '&mdash; ' + r.subjectname : ''}</td>
                            <td>${r.sectionname || '-'}</td>
                            <td>${r.roomname || '-'}</td>
                            <td><span class="fac-status-badge fac-status-${(r.status || '').toLowerCase()}">${r.status || ''}</span></td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>`;
    } catch (e) {
        el.innerHTML = '<div class="fac-empty">Failed to load schedule.</div>';
    }
}

async function _loadFacultyLoad(empNum) {
    const el = document.getElementById('facLoadContent');
    el.innerHTML = '<div class="fac-loading">Loading load summary...</div>';
    try {
        const params = new URLSearchParams({ emp_num: empNum });
        if (ADMIN_ACTIVE_AY_ID) params.set('ay_id', ADMIN_ACTIVE_AY_ID);
        if (ADMIN_ACTIVE_SEM)   params.set('sem', ADMIN_ACTIVE_SEM);
        const res  = await fetch(`/api/manual/faculty_load?${params}`);
        const data = await res.json();
        if (!data.success) {
            el.innerHTML = '<div class="fac-empty">No load data available for this faculty member.</div>';
            return;
        }
        const subjects = [...(data.assigned_subjects || []), ...(data.pending_subjects || [])];
        el.innerHTML = `
            <div class="fac-load-summary">
                <div class="fac-load-stat"><span class="fac-load-num">${data.total_units ?? 0}</span><span class="fac-load-label">Max Load</span></div>
                <div class="fac-load-stat"><span class="fac-load-num">${data.scheduled_units ?? 0}</span><span class="fac-load-label">Scheduled</span></div>
                <div class="fac-load-stat"><span class="fac-load-num">${data.pending_units ?? 0}</span><span class="fac-load-label">Pending</span></div>
                <div class="fac-load-stat"><span class="fac-load-num">${data.available_units ?? 0}</span><span class="fac-load-label">Available</span></div>
            </div>
            ${subjects.length ? `
                <table class="fac-sched-table">
                    <thead><tr><th>Subject</th><th>Program / Year</th><th>Units</th><th></th></tr></thead>
                    <tbody>
                        ${subjects.map(s => `
                            <tr>
                                <td>${s.subjectcode || ''} ${s.subjectname ? '&mdash; ' + s.subjectname : ''}</td>
                                <td>${s.programcode ? s.programcode + (s.yearlevel ? ' Y' + s.yearlevel : '') : (s.sectionname || '-')}</td>
                                <td>${s.creditunits ?? 0}</td>
                                <td>${s.is_pending ? '<span class="fac-status-badge fac-status-pending">Pending</span>' : ''}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>` : '<div class="fac-empty">No subjects assigned for the current semester.</div>'}
        `;
    } catch (e) {
        el.innerHTML = '<div class="fac-empty">Failed to load load summary.</div>';
    }
}

/* ═══════════════════════════════════════════════════════════
   SUBJECT/FACULTY ASSIGNMENT EXPORT
═══════════════════════════════════════════════════════════ */
const _FACSUB_COUNT_ROUTE  = '/admin/faculty/subject-export/count';
const _FACSUB_EXPORT_ROUTE = '/admin/faculty/subject-export';
let _facSubCountTmr = null;
let _facSubLastFacultyCount = 0;

function openFacSubExportModal() {
    _facSubLastFacultyCount = 0;
    document.querySelectorAll('#facSubExportModal .fse-ay-cb, #facSubExportModal .fse-sem-cb, #facSubExportModal .fse-type-cb').forEach(cb => cb.checked = false);
    document.querySelectorAll('#facSubExportModal .emp-cb-item').forEach(el => el.classList.remove('selected'));
    document.querySelectorAll('#facSubExportModal .emp-export-format-card').forEach(c => c.classList.remove('selected'));
    document.getElementById('facSubFilenameInput').value = 'Faculty_Subject_Assignment';
    document.getElementById('facSubCount').textContent = '0';
    document.getElementById('facSubScopeLabel').textContent = 'Select filters to begin';
    document.getElementById('facSubFmtError').style.display = 'none';
    document.getElementById('facSubConfirmBtn').disabled = true;
    document.getElementById('facSubTypeAllBtn').textContent = 'All';
    document.getElementById('facSubFmtAllBtn').textContent = 'Select All';
    document.getElementById('facSubExportModal').style.display = 'flex';
}
function closeFacSubExportModal() {
    document.getElementById('facSubExportModal').style.display = 'none';
}

function _toggleFacSubCb(itemEl) {
    const cb = itemEl.querySelector('input[type=checkbox]');
    cb.checked = !cb.checked;
    _onFacSubCbChange(cb);
}
function _onFacSubCbChange(cb) {
    cb.closest('.emp-cb-item').classList.toggle('selected', cb.checked);
    _syncFacSubTypeAllBtn();
    _updateFacSubFooter();
    clearTimeout(_facSubCountTmr);
    _facSubCountTmr = setTimeout(_fetchFacSubCount, 400);
}

function _toggleFacSubAllTypes() {
    const cbs    = document.querySelectorAll('#facSubExportModal .fse-type-cb');
    const allSel = Array.from(cbs).every(cb => cb.checked);
    cbs.forEach(cb => { cb.checked = !allSel; cb.closest('.emp-cb-item').classList.toggle('selected', !allSel); });
    _syncFacSubTypeAllBtn();
    _updateFacSubFooter();
    clearTimeout(_facSubCountTmr);
    _facSubCountTmr = setTimeout(_fetchFacSubCount, 400);
}
function _syncFacSubTypeAllBtn() {
    const cbs    = document.querySelectorAll('#facSubExportModal .fse-type-cb');
    const allSel = cbs.length > 0 && Array.from(cbs).every(cb => cb.checked);
    document.getElementById('facSubTypeAllBtn').textContent = allSel ? 'Clear' : 'All';
}

function _toggleFacSubFmtCard(el) {
    el.classList.toggle('selected');
    const all    = document.querySelectorAll('#facSubExportModal .emp-export-format-card');
    const allSel = Array.from(all).every(c => c.classList.contains('selected'));
    document.getElementById('facSubFmtAllBtn').textContent = allSel ? 'Deselect All' : 'Select All';
    _updateFacSubFooter();
}
function _toggleFacSubAllFmts() {
    const cards  = document.querySelectorAll('#facSubExportModal .emp-export-format-card');
    const allSel = Array.from(cards).every(c => c.classList.contains('selected'));
    cards.forEach(c => allSel ? c.classList.remove('selected') : c.classList.add('selected'));
    document.getElementById('facSubFmtAllBtn').textContent = !allSel ? 'Deselect All' : 'Select All';
    _updateFacSubFooter();
}

function _facSubFilters() {
    return {
        ay_ids:        Array.from(document.querySelectorAll('#facSubExportModal .fse-ay-cb:checked')).map(c => c.value),
        sem_types:     Array.from(document.querySelectorAll('#facSubExportModal .fse-sem-cb:checked')).map(c => c.value),
        faculty_types: Array.from(document.querySelectorAll('#facSubExportModal .fse-type-cb:checked')).map(c => c.value),
    };
}

async function _fetchFacSubCount() {
    const f = _facSubFilters();
    if (!f.ay_ids.length || !f.sem_types.length || !f.faculty_types.length) {
        document.getElementById('facSubCount').textContent = '0';
        _facSubLastFacultyCount = 0;
        document.getElementById('facSubScopeLabel').textContent = 'Select filters to begin';
        _updateFacSubFooter();
        return;
    }
    try {
        const res  = await fetch(_FACSUB_COUNT_ROUTE, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(f) });
        const data = await res.json();
        if (data.error) { document.getElementById('facSubScopeLabel').textContent = 'Error: ' + data.error; return; }
        document.getElementById('facSubCount').textContent = data.count || 0;
        const nFac = data.faculty || 0;
        _facSubLastFacultyCount = nFac;
        // All roster faculty are always included (even with no assigned load
        // this period), so the button is enabled whenever the roster is
        // non-empty — not gated on there being actual assignment rows.
        document.getElementById('facSubScopeLabel').textContent = nFac
            ? `${nFac} faculty member${nFac === 1 ? '' : 's'} (${data.count || 0} assignment${(data.count || 0) === 1 ? '' : 's'})`
            : 'No faculty match the selected type(s).';
    } catch (e) {
        document.getElementById('facSubScopeLabel').textContent = 'Network error — check server connection.';
    }
    _updateFacSubFooter();
}

function _updateFacSubFooter() {
    const f    = _facSubFilters();
    const fmts = document.querySelectorAll('#facSubExportModal .emp-export-format-card.selected');
    const hasFilters = f.ay_ids.length > 0 && f.sem_types.length > 0 && f.faculty_types.length > 0;
    const ok = hasFilters && fmts.length > 0 && _facSubLastFacultyCount > 0;
    document.getElementById('facSubConfirmBtn').disabled = !ok;
}

async function executeFacSubExport() {
    const f      = _facSubFilters();
    const fmts   = Array.from(document.querySelectorAll('#facSubExportModal .emp-export-format-card.selected')).map(c => c.dataset.format);
    const fmtErr = document.getElementById('facSubFmtError');
    if (!fmts.length) { fmtErr.style.display = 'block'; return; }
    fmtErr.style.display = 'none';
    if (!f.ay_ids.length || !f.sem_types.length || !f.faculty_types.length) {
        _showExportToast('error', 'Missing Selection', 'Select at least one Academic Year, Semester, and Faculty Type.');
        return;
    }
    const filename = (document.getElementById('facSubFilenameInput').value || 'Faculty_Subject_Assignment').trim().replace(/[\/\\:*?"<>|]/g, '_');
    const btn = document.getElementById('facSubConfirmBtn');
    btn.disabled = true;
    closeFacSubExportModal();
    _showExportLoading('Generating Export', 'Building faculty/subject assignment report...');

    try {
        const resp = await fetch(_FACSUB_EXPORT_ROUTE, {
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
        _showExportToast('success', 'Export Complete', 'Faculty/subject assignment export downloaded.');
    } catch (e) {
        _showExportToast('error', 'Export Failed', e.message);
    } finally {
        _hideExportLoading();
        btn.disabled = false;
    }
}