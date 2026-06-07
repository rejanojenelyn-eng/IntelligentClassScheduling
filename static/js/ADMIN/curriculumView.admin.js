// CURRICULUM_ID is defined inline in the HTML template above this script

function updateFilters(type, val) {
    const urlParams = new URLSearchParams(window.location.search);
    urlParams.set(type, val);
    window.location.search = urlParams.toString();
}

// ── View Curriculum Export ────────────────────────────────────────────────────

async function viewCurrExport(format) {
    const modal  = document.getElementById('viewExpModal');
    const status = document.getElementById('viewExpStatus');
    if (status) status.style.display = 'block';

    try {
        const res = await fetch('/admin/curriculum/export/data', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ curriculum_ids: [CURRICULUM_ID] }),
        });
        if (!res.ok) throw new Error(await res.text() || 'Failed to fetch curriculum data');
        const curricula = await res.json();
        if (!curricula.length) throw new Error('No curriculum data returned.');

        const curr     = curricula[0];
        const safe     = s => (s || '').replace(/[^a-zA-Z0-9_-]/g, '_');
        const filename = `${safe(curr.program_code || curr.curriculum_code)}_CY${safe(curr.curriculum_year)}`;

        if (format === 'csv') {
            _viewExpCSV(curricula, filename);
        } else if (format === 'pdf') {
            _viewExpPDF(curricula, filename);
        } else {
            const route = format === 'xlsx'
                ? '/admin/curriculum/export/xlsx'
                : '/admin/curriculum/export/docx';
            const r2 = await fetch(route, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ curricula, timestamp: 'Generated: ' + new Date().toLocaleString() }),
            });
            if (!r2.ok) throw new Error(`${format.toUpperCase()} export failed.`);
            const blob = await r2.blob();
            _viewExpDownload(blob, `${filename}.${format}`);
        }
        if (modal) modal.style.display = 'none';
    } catch (e) {
        alert('Export failed: ' + e.message);
    } finally {
        if (status) status.style.display = 'none';
    }
}

function _viewExpDownload(blob, filename) {
    const a = Object.assign(document.createElement('a'), {
        href: URL.createObjectURL(blob), download: filename,
    });
    a.click(); URL.revokeObjectURL(a.href);
}

function _viewExpCSV(curricula, filename) {
    const q = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = [`"Curriculum Export"`, `"Generated: ${new Date().toLocaleString()}"`, ''];
    curricula.forEach(curr => {
        lines.push(q(`Program: ${curr.program_name}`));
        lines.push(q(`Curriculum: ${curr.curriculum_code} — C.Y ${curr.curriculum_year}`));
        lines.push('');
        curr.year_levels.forEach(yl => {
            yl.semesters.forEach(sem => {
                lines.push(q(`${yl.label} — ${sem.label}`));
                lines.push(['Subject Code','Prereq','Co-req','Description','Lec Hrs','Lab Hrs','Credited Units','Tuition Hrs'].map(q).join(','));
                sem.subjects.forEach(s => {
                    lines.push([s.subject_code, s.prerequisite, s.corequisite, s.subject_name,
                                s.lecture_hours, s.lab_hours, s.credit_units, s.tuition_hours].map(q).join(','));
                });
                lines.push(['','','', q('TOTAL UNITS'),'','',(sem.total_units||0),(sem.total_tuition||0)].join(','));
                lines.push('');
            });
        });
    });
    _viewExpDownload(new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' }), filename + '.csv');
}

function _viewExpPDF(curricula, filename) {
    if (!window.jspdf) { alert('PDF library not loaded. Please try a different format.'); return; }
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4' });
    const W = 297;
    curricula.forEach((curr, cidx) => {
        if (cidx > 0) doc.addPage();
        doc.setFillColor(128, 0, 0); doc.rect(0, 0, W, 34, 'F');
        doc.setTextColor(255, 255, 255);
        doc.setFont('helvetica', 'bold'); doc.setFontSize(15);
        doc.text(`${curr.curriculum_code}  —  ${curr.program_name}`, W / 2, 13, { align: 'center' });
        doc.setFont('helvetica', 'normal'); doc.setFontSize(10);
        doc.text(`Curriculum Year: ${curr.curriculum_year}`, W / 2, 22, { align: 'center' });
        doc.setFontSize(8);
        doc.text('Generated: ' + new Date().toLocaleString(), W / 2, 30, { align: 'center' });
        doc.setTextColor(0, 0, 0);
        let startY = 36;
        curr.year_levels.forEach(yl => {
            yl.semesters.forEach(sem => {
                if (startY > 192) { doc.addPage(); startY = 10; }
                doc.setFillColor(55, 55, 55); doc.rect(10, startY, W - 20, 7, 'F');
                doc.setTextColor(255, 255, 255); doc.setFont('helvetica', 'bold'); doc.setFontSize(8.5);
                doc.text(`${yl.label.toUpperCase()}  ·  ${sem.label}`, 14, startY + 5);
                doc.setTextColor(0, 0, 0); startY += 7;
                doc.autoTable({
                    columns: [
                        { header: 'Subject Code',    dataKey: 'subject_code'   },
                        { header: 'Pre-requisite',   dataKey: 'prerequisite'   },
                        { header: 'Co-requisite',    dataKey: 'corequisite'    },
                        { header: 'Description',     dataKey: 'subject_name'   },
                        { header: 'Lec Hrs',         dataKey: 'lecture_hours'  },
                        { header: 'Lab Hrs',         dataKey: 'lab_hours'      },
                        { header: 'Credited Units',  dataKey: 'credit_units'   },
                        { header: 'Tuition Hrs',     dataKey: 'tuition_hours'  },
                    ],
                    body: sem.subjects, startY,
                    styles: { fontSize: 7.5, cellPadding: 2, overflow: 'linebreak' },
                    headStyles: { fillColor: [128, 0, 0], textColor: 255, fontStyle: 'bold', fontSize: 8 },
                    alternateRowStyles: { fillColor: [253, 245, 245] },
                    tableWidth: W - 20,
                    columnStyles: {
                        0: { cellWidth: 32 }, 1: { cellWidth: 26 }, 2: { cellWidth: 20 }, 3: { cellWidth: 126 },
                        4: { cellWidth: 16, halign: 'center' }, 5: { cellWidth: 16, halign: 'center' },
                        6: { cellWidth: 22, halign: 'center' }, 7: { cellWidth: 19, halign: 'center' },
                    },
                    foot: [['', '', '', 'TOTAL UNITS', '', '', (sem.total_units || 0), (sem.total_tuition || 0)]],
                    footStyles: { fillColor: [240, 230, 230], fontStyle: 'bold', fontSize: 8, textColor: [80, 0, 0] },
                    showFoot: 'lastPage', margin: { left: 10, right: 10 },
                });
                startY = doc.lastAutoTable.finalY + 4;
            });
        });
    });
    const total = doc.internal.getNumberOfPages();
    for (let i = 1; i <= total; i++) {
        doc.setPage(i); doc.setFontSize(7); doc.setTextColor(150, 150, 150);
        doc.text(`Page ${i} of ${total}`, 287, 206, { align: 'right' });
    }
    doc.save(filename + '.pdf');
}
