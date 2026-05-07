// --- MODAL CONTROLS ---
function openRestoreModal(archiveId, name) {
    document.getElementById('restoreEmployeeName').textContent = name;
    document.getElementById('confirmRestoreBtn').href = `/admin/restore_employee/${archiveId}`;
    document.getElementById('restoreConfirmModal').style.display = 'block';
}
function closeRestoreModal() { document.getElementById('restoreConfirmModal').style.display = 'none'; }

function openBulkRestoreModal() {
    const selectedIds = Array.from(document.querySelectorAll('.row-check:checked')).map(cb => cb.value);
    document.getElementById('bulkRestoreCount').textContent = selectedIds.length;
    document.getElementById('bulkRestoreConfirmModal').style.display = 'block';
}
function closeBulkRestoreModal() { document.getElementById('bulkRestoreConfirmModal').style.display = 'none'; }

window.onclick = function(event) {
    const modals = ["restoreConfirmModal", "bulkRestoreConfirmModal"];
    modals.forEach(id => {
        if (event.target == document.getElementById(id)) document.getElementById(id).style.display = "none";
    });
};

// --- BULK ACTIONS ---
function updateBulkActions() {
    const checkboxes = document.querySelectorAll('.row-check:checked');
    document.getElementById('btnBulkRestore').disabled = (checkboxes.length === 0);
}

document.getElementById("selectAll").addEventListener("change", function() {
    document.querySelectorAll('.row-check').forEach(cb => {
        const row = cb.closest("tr");
        if (row.style.display !== 'none') cb.checked = this.checked;
    });
    updateBulkActions();
});

document.addEventListener('change', (e) => {
    if (e.target.classList.contains('row-check')) updateBulkActions();
});

document.getElementById('confirmBulkRestoreBtn').addEventListener('click', function() {
    const selectedIds = Array.from(document.querySelectorAll('.row-check:checked')).map(cb => cb.value);
    fetch('/admin/bulk_restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ archive_ids: selectedIds })
    }).then(res => res.json()).then(data => {
        if (data.success) window.location.reload();
        else alert("An error occurred while restoring employees.");
    });
});

// --- FILTERING ---
function filterTable() {
    let nameInput   = document.getElementById("searchInput").value.toUpperCase();
    let typeInput   = document.getElementById("typeFilter").value.toUpperCase();
    let statusInput = document.getElementById("statusFilter").value.toUpperCase();
    let specInput   = document.getElementById("specFilter").value.toUpperCase();
    let rows = document.querySelectorAll("#archiveTable tbody tr");

    rows.forEach(row => {
        let matchName   = row.querySelector(".emp-name").textContent.toUpperCase().includes(nameInput);
        let matchType   = typeInput   === "" || row.querySelector(".emp-type").textContent.toUpperCase().trim().includes(typeInput);
        let matchStatus = statusInput === "" || row.querySelector(".emp-status").textContent.toUpperCase().trim() === statusInput;
        let matchSpec   = specInput   === "" || row.querySelector(".emp-spec").textContent.toUpperCase().trim().includes(specInput);
        row.style.display = (matchName && matchType && matchStatus && matchSpec) ? "" : "none";
    });
}

// --- SORTING ---
let currentOrder = 'asc';

function sortTable() {
    const table = document.getElementById("archiveTable");
    const tbody = table.querySelector("tbody");
    const rows  = Array.from(tbody.querySelectorAll("tr"));
    const direction = currentOrder === 'asc' ? -1 : 1;

    rows.sort((a, b) => {
        const nameA = a.querySelector(".emp-name").innerText.toLowerCase().trim();
        const nameB = b.querySelector(".emp-name").innerText.toLowerCase().trim();
        return nameA.localeCompare(nameB) * direction;
    });

    currentOrder = (currentOrder === 'asc') ? 'desc' : 'asc';

    const sortLabel = document.getElementById("sortDirection");
    const sortIcon  = document.getElementById("sortIcon");
    sortLabel.innerText = currentOrder === 'asc' ? 'Ascending' : 'Descending';
    sortIcon.className  = currentOrder === 'asc' ? 'fas fa-sort-amount-up' : 'fas fa-sort-amount-down-alt';

    rows.forEach(row => tbody.appendChild(row));
}

// --- EXPORT ---
function exportToExcel() {
    const table = document.getElementById("archiveTable");
    const selectedCheckboxes = Array.from(table.querySelectorAll(".row-check:checked"));
    let rowsToExport = selectedCheckboxes.length > 0
        ? selectedCheckboxes.map(cb => cb.closest("tr"))
        : Array.from(table.querySelectorAll("tbody tr")).filter(r => r.style.display !== 'none');

    if (rowsToExport.length === 0) { alert("No data available to export."); return; }

    const data = [["Full Name", "Specialization", "Employment Type", "Status", "Date Archived"]];
    rowsToExport.forEach(row => {
        data.push([
            row.querySelector(".emp-name").innerText.trim(),
            row.querySelector(".emp-spec").innerText.trim(),
            row.querySelector(".emp-type .badge").innerText.trim(),
            row.querySelector(".emp-status").innerText.trim(),
            row.querySelector(".date-archived").innerText.trim(),
        ]);
    });

    const wb = XLSX.utils.book_new();
    const ws = XLSX.utils.aoa_to_sheet(data);
    XLSX.utils.book_append_sheet(wb, ws, "Archived_Employees");
    XLSX.writeFile(wb, "Archived_Employees_Export.xlsx");
}
