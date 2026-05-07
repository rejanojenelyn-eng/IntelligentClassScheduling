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

function handleProgramExport() {
    const selectEl = document.getElementById('mainProgramFilter');
    const programCode = selectEl ? selectEl.value : "";
    if (programCode && programCode !== "" && programCode !== "None") {
        window.location.href = "/admin/export/program/" + programCode;
    } else {
        alert("Please select a specific program or 'All Programs' before clicking Export.");
    }
}
