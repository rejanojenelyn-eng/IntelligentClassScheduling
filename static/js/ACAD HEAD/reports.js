const SEM_LABELS = { A: '1st Semester', B: '2nd Semester', C: 'Summer' };
const ALL_SUB = [
    'grpAY', 'grpSem', 'sgProgram', 'sgYearLevel', 'sgInstructor',
    'sgFacultyType', 'sgFacultyStatus', 'sgSpecialization',
    'sgBuilding', 'sgRoomType', 'sgCurriculum'
];

// Map the UI cards to the backend API report logic
function openReportModal(cardType) {
    const modal = document.getElementById('reportModal');
    const title = document.getElementById('modalTitle');
    const rptTypeInput = document.getElementById('rptType');
    
    // Hide all filters and preview area first
    ALL_SUB.forEach(id => document.getElementById(id).classList.add('hidden'));
    document.getElementById('previewArea').style.display = 'none';
    document.getElementById('previewArea').innerHTML = '';

    // Route logic mapping
    if (cardType === 'class_schedule' || cardType === 'offerings') {
        title.innerText = cardType === 'class_schedule' ? "CLASS SCHEDULE" : "ACADEMIC OFFERINGS";
        rptTypeInput.value = 'offerings';
        ['grpAY', 'grpSem', 'sgProgram', 'sgYearLevel'].forEach(id => document.getElementById(id).classList.remove('hidden'));
    } 
    else if (cardType === 'room_schedule') {
        title.innerText = "ROOM SCHEDULE";
        rptTypeInput.value = 'room_schedule';
        ['grpAY', 'grpSem', 'sgBuilding', 'sgRoomType'].forEach(id => document.getElementById(id).classList.remove('hidden'));
    }
    else if (cardType === 'faculty') {
        title.innerText = "EMPLOYEE LIST";
        rptTypeInput.value = 'faculty';
        ['sgFacultyType', 'sgFacultyStatus', 'sgSpecialization'].forEach(id => document.getElementById(id).classList.remove('hidden'));
    }
    else if (cardType === 'curriculum') {
        title.innerText = "CURRICULUM LIST";
        rptTypeInput.value = 'curriculum';
        ['sgProgram', 'sgCurriculum'].forEach(id => document.getElementById(id).classList.remove('hidden'));
    }
    else if (cardType === 'rooms') {
        title.innerText = "ROOM AND BUILDING LIST";
        rptTypeInput.value = 'rooms';
        ['sgBuilding', 'sgRoomType'].forEach(id => document.getElementById(id).classList.remove('hidden'));
    }
    else if (cardType === 'assignments') {
        title.innerText = "TEACHING ASSIGNMENT";
        rptTypeInput.value = 'assignments';
        ['grpAY', 'grpSem', 'sgProgram', 'sgInstructor'].forEach(id => document.getElementById(id).classList.remove('hidden'));
    }

    modal.style.display = "flex";
}

function closeReportModal() {
    document.getElementById('reportModal').style.display = 'none';
}

function onProgramChange() {
    const prog = document.getElementById('rptProgram').value;
    const currSel = document.getElementById('rptCurriculum');
    if (!currSel) return;
    [...currSel.options].forEach(opt => {
        if (opt.value === 'All') { opt.style.display = ''; return; }
        opt.style.display = (prog === 'All' || opt.dataset.prog === prog) ? '' : 'none';
    });
    currSel.value = 'All';
}

function getFilters() {
    return {
        report_type:    document.getElementById('rptType').value,
        ay:             document.getElementById('rptAY').value,
        semester:       document.getElementById('rptSem').value,
        program:        document.getElementById('rptProgram').value,
        year_level:     document.getElementById('rptYearLevel').value,
        instructor:     document.getElementById('rptInstructor').value,
        curriculum:     document.getElementById('rptCurriculum').value,
        faculty_type:   document.getElementById('rptFacultyType').value,
        faculty_status: document.getElementById('rptFacultyStatus').value,
        specialization: document.getElementById('rptSpecialization').value,
        building:       document.getElementById('rptBuilding').value,
        room_type:      document.getElementById('rptRoomType').value,
    };
}

async function applyFilter() {
    const area = document.getElementById('previewArea');
    area.style.display = 'block';
    area.innerHTML = `<div style="text-align:center; padding:20px; color:#800000; font-weight:bold;"><i class="fas fa-spinner fa-spin"></i> Generating Preview...</div>`;
    try {
        const res = await fetch('/admin/reports/data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getFilters()),
        });
        const data = await res.json();
        if (data.error) {
            area.innerHTML = `<p style="color:red; text-align:center;">${data.error}</p>`;
            return;
        }
        renderPreview(data);
    } catch(e) {
        area.innerHTML = `<p style="color:red; text-align:center;">Network error: ${e.message}</p>`;
    }
}

function buildTableHtml(columns, rows, tableId) {
    const thead = '<tr>' + columns.map(c => `<th>${c}</th>`).join('') + '</tr>';
    const tbody = rows.map(row => '<tr>' + row.map(cell => `<td>${cell ?? ''}</td>`).join('') + '</tr>').join('');
    return `<table class="rpt-table" id="${tableId}"><thead>${thead}</thead><tbody>${tbody}</tbody></table>`;
}

function renderPreview(data) {
    const area = document.getElementById('previewArea');
    
    if (data.grouped) {
        if (!data.sections || data.sections.length === 0) {
            area.innerHTML = `<p style="text-align:center; color:#6c757d; margin-top:20px;">No records found.</p>`; return;
        }
        let inner = '';
        data.sections.forEach((sec, idx) => {
            inner += `<div class="section-label">${sec.title}</div>
                      <div>${buildTableHtml(sec.columns, sec.rows, `reportTable_${idx}`)}</div>`;
        });
        area.innerHTML = inner;
        return;
    }

    if (!data.rows || data.rows.length === 0) {
        area.innerHTML = `<p style="text-align:center; color:#6c757d; margin-top:20px;">No records found.</p>`; return;
    }
    area.innerHTML = buildTableHtml(data.columns, data.rows, 'reportTable_0');
}

function exportExcel() {
    const tables = document.querySelectorAll('[id^="reportTable_"]');
    if (!tables.length) { alert('Click "Preview Data" first before exporting.'); return; }
    
    const title = document.getElementById('modalTitle').innerText;
    const rows = [];
    const grouped = tables.length > 1;
    const secLabels = document.querySelectorAll('.section-label');
    
    tables.forEach((table, idx) => {
        if (grouped && secLabels[idx]) { rows.push(secLabels[idx].innerText.trim()); rows.push(''); }
        table.querySelectorAll('tr').forEach(tr => {
            rows.push([...tr.querySelectorAll('th,td')].map(c => c.innerText.trim()).join('\t'));
        });
        if (grouped) rows.push('');
    });
    const blob = new Blob([rows.join('\n')], { type: 'application/vnd.ms-excel' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: `${title.replace(/\s+/g, '_')}.xls` });
    a.click(); URL.revokeObjectURL(a.href);
}

function exportPdf() {
    const tables = document.querySelectorAll('[id^="reportTable_"]');
    if (!tables.length) { alert('Click "Preview Data" first before exporting.'); return; }
    
    const title = document.getElementById('modalTitle').innerText;
    const grouped = tables.length > 1;
    const secLabels = document.querySelectorAll('.section-label');
    let bodyContent = '';
    
    tables.forEach((table, idx) => {
        if (grouped && secLabels[idx]) bodyContent += `<h3 class="sec-title">${secLabels[idx].innerText.trim()}</h3>`;
        bodyContent += table.outerHTML + '<br>';
    });
    
    const win = window.open('', '_blank');
    if (!win) { alert('Popup blocked. Please allow popups for this page.'); return; }
    win.document.write(`<html><head><title>${title}</title>
    <style>
        body{font-family:Arial,sans-serif;font-size:11px;margin:20px;}
        h2{text-align:center;color:#800000;margin-bottom:15px;font-size:16px;}
        h3.sec-title{color:#800000;font-size:12px;margin:20px 0 5px;border-left:3px solid #800000;padding-left:8px;}
        table{width:100%;border-collapse:collapse;margin-bottom:10px;}
        th{background:#800000;color:#fff;padding:6px 8px;font-size:10px;text-align:left;}
        td{padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:10px;color:#333;}
    </style></head><body>
    <h2>${title}</h2>
    ${bodyContent}
    <script>window.print();<\/script>
    </body></html>`);
    win.document.close();
}

// Close modal when clicking outside
window.onclick = function(event) {
    if (event.target == document.getElementById('reportModal')) {
        closeReportModal();
    }
}