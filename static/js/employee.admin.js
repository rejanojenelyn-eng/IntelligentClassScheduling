let selectedIdsForArchive = [];

function updateBulkActions() {
    const checkboxes = document.querySelectorAll('.row-check:checked');
    const archiveBtn = document.getElementById('btnBulkArchive');
    archiveBtn.disabled = (checkboxes.length === 0);
    archiveBtn.style.opacity = archiveBtn.disabled ? "0.5" : "1";
    archiveBtn.style.cursor  = archiveBtn.disabled ? "not-allowed" : "pointer";
}

document.getElementById("selectAll").addEventListener("change", function() {
    document.querySelectorAll('.row-check').forEach(cb => cb.checked = this.checked);
    updateBulkActions();
});

document.addEventListener('change', (e) => {
    if (e.target.classList.contains('row-check')) updateBulkActions();
});

// --- MODAL CONTROLS ---
function openAddModal() { document.getElementById("addEmployeeModal").style.display = "block"; }
function closeAddModal() { document.getElementById("addEmployeeModal").style.display = "none"; }
function openImportModal() { document.getElementById("importEmployeeModal").style.display = "block"; }
function closeImportModal() { document.getElementById("importEmployeeModal").style.display = "none"; }

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
    let nameInput   = document.getElementById("searchInput").value.toUpperCase();
    let typeInput   = document.getElementById("typeFilter").value.toUpperCase();
    let statusInput = document.getElementById("statusFilter").value.toUpperCase();
    let specInput   = document.getElementById("specFilter").value.toUpperCase();

    let tbody = document.getElementById("instructorTable").getElementsByTagName("tbody")[0];
    let tr    = tbody.getElementsByTagName("tr");

    for (let i = 0; i < tr.length; i++) {
        let nameCol   = tr[i].querySelector(".emp-name");
        let specCol   = tr[i].querySelector(".emp-spec");
        let typeCol   = tr[i].querySelector(".emp-type");
        let statusCol = tr[i].querySelector(".emp-status");

        if (nameCol && specCol && typeCol && statusCol) {
            let matchSearch = nameCol.textContent.toUpperCase().indexOf(nameInput) > -1;
            let matchType   = typeInput   === "" || typeCol.textContent.toUpperCase().trim()   === typeInput;
            let matchStatus = statusInput === "" || statusCol.textContent.toUpperCase().trim() === statusInput;
            let matchSpec   = specInput   === "" || specCol.textContent.toUpperCase().trim()   === specInput;
            tr[i].style.display = (matchSearch && matchType && matchStatus && matchSpec) ? "" : "none";
        }
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

// --- EXPORT ---
function exportToExcel() {
    const table = document.querySelector(".employee-table");
    if (!table) return;

    const checkedBoxes = table.querySelectorAll(".row-check:checked");
    let rowsToExport = checkedBoxes.length > 0
        ? Array.from(checkedBoxes).map(cb => cb.closest("tr"))
        : Array.from(table.querySelectorAll("tbody tr")).filter(r => r.style.display !== 'none');

    const excelData = [["Employee Number", "Name", "Specialization", "Type", "Status"]];

    rowsToExport.forEach(row => {
        excelData.push([
            row.cells[1] ? row.cells[1].innerText.trim() : "",
            row.querySelector(".emp-name")   ? row.querySelector(".emp-name").innerText.trim()   : "",
            row.querySelector(".emp-spec")   ? row.querySelector(".emp-spec").innerText.trim()   : "",
            row.querySelector(".emp-type")   ? row.querySelector(".emp-type").innerText.trim()   : "",
            row.querySelector(".emp-status") ? row.querySelector(".emp-status").innerText.trim() : "",
        ]);
    });

    try {
        const wb = XLSX.utils.book_new();
        const ws = XLSX.utils.aoa_to_sheet(excelData);
        XLSX.utils.book_append_sheet(wb, ws, "Sheet1");
        XLSX.writeFile(wb, checkedBoxes.length > 0 ? "Selected_Employees.xlsx" : "Employee_Records.xlsx");
    } catch (e) {
        console.error("Export Error:", e);
        alert("Excel library not loaded. Please refresh the page.");
    }
}
