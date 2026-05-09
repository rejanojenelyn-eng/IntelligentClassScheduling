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

function handleProgramExport() {
    const selectEl = document.getElementById('mainProgramFilter');
    const programCode = selectEl ? selectEl.value : "";
    if (programCode && programCode !== "" && programCode !== "None") {
        window.location.href = "/admin/export/program/" + programCode;
    } else {
        document.getElementById('exportWarningModal').style.display = 'flex';
    }
}

function closeExportWarning() { document.getElementById('exportWarningModal').style.display = 'none'; }

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

function openImportModal() { document.getElementById('importModal').style.display = 'flex'; resetColumnCustomization(); }
function closeImportModal() { document.getElementById('importModal').style.display = 'none'; }

function confirmAdvancedImport() {
    const selectedCols = [];
    for (let i = 0; i < 11; i++) {
        const val = document.getElementById(`h_col_${i}`).value;
        if (val !== 'skip') {
            if (selectedCols.includes(val)) { alert("Error: Duplicate column detected."); return; }
            selectedCols.push(val);
        }
    }
    document.getElementById('advancedImportForm').submit();
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

