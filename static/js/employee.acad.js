// --- MODAL CONTROLS ---
function openAddModal() { document.getElementById("addEmployeeModal").style.display = "block"; }
function closeAddModal() { document.getElementById("addEmployeeModal").style.display = "none"; }
function openImportModal() { document.getElementById("importEmployeeModal").style.display = "block"; }
function closeImportModal() { document.getElementById("importEmployeeModal").style.display = "none"; }

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

// --- FILTERING ---
function filterTable() {
    let nameInput = document.getElementById("searchInput").value.toUpperCase();
    let typeInput = document.getElementById("typeFilter").value.toUpperCase();
    let statusInput = document.getElementById("statusFilter").value.toUpperCase();
    let specInput = document.getElementById("specFilter").value.toUpperCase();

    let table = document.getElementById("instructorTable");
    let tr = table.getElementsByTagName("tbody")[0].getElementsByTagName("tr");

    for (let i = 0; i < tr.length; i++) {
        let nameTxt = tr[i].querySelector(".emp-name").textContent.toUpperCase();
        let specTxt = tr[i].querySelector(".emp-spec").textContent.toUpperCase();
        let typeTxt = tr[i].querySelector(".emp-type").textContent.toUpperCase();
        let statusTxt = tr[i].querySelector(".emp-status").textContent.toUpperCase();

        let matchName = nameTxt.includes(nameInput);
        let matchType = typeInput === "" || typeTxt.trim() === typeInput;
        let matchStatus = statusInput === "" || statusTxt.trim() === statusInput;
        let matchSpec = specInput === "" || specTxt.trim() === specInput;

        tr[i].style.display = (matchName && matchType && matchStatus && matchSpec) ? "" : "none";
    }
}

// --- SORTING ---
function sortTable() {
    const sortDirectionSpan = document.getElementById("sortDirection");
    const icon = document.getElementById("sortIcon");
    const table = document.getElementById("instructorTable");
    const tbody = table.querySelector("tbody");
    const rows = Array.from(tbody.querySelectorAll("tr"));

    const currentText = sortDirectionSpan.innerText.trim();
    let direction;

    if (currentText === 'Ascending') {
        direction = -1;
        sortDirectionSpan.innerText = 'Descending';
        icon.className = 'fas fa-sort-amount-up';
    } else {
        direction = 1;
        sortDirectionSpan.innerText = 'Ascending';
        icon.className = 'fas fa-sort-amount-down-alt';
    }

    rows.sort((a, b) => {
        const nameA = a.querySelector(".emp-name").innerText.toLowerCase();
        const nameB = b.querySelector(".emp-name").innerText.toLowerCase();
        return nameA.localeCompare(nameB) * direction;
    });

    rows.forEach(row => tbody.appendChild(row));
}

// --- EXPORT ---
function exportToExcel() {
    const table = document.querySelector(".employee-table");
    if (!table) return;

    const checkedBoxes = table.querySelectorAll(".row-check:checked");
    let rowsToExport = [];

    if (checkedBoxes.length > 0) {
        checkedBoxes.forEach(cb => { rowsToExport.push(cb.closest("tr")); });
    } else {
        const allRows = Array.from(table.querySelectorAll("tbody tr"));
        rowsToExport = allRows.filter(row => row.style.display !== 'none');
    }

    const excelData = [];
    const headers = ["Employee Number", "Name", "Specialization", "Type", "Status"];
    excelData.push(headers);

    rowsToExport.forEach(row => {
        const empNum = row.cells[1] ? row.cells[1].innerText.trim() : "";
        const name = row.querySelector(".emp-name") ? row.querySelector(".emp-name").innerText.trim() : "";
        const spec = row.querySelector(".emp-spec") ? row.querySelector(".emp-spec").innerText.trim() : "";
        const type = row.querySelector(".emp-type") ? row.querySelector(".emp-type").innerText.trim() : "";
        const status = row.querySelector(".emp-status") ? row.querySelector(".emp-status").innerText.trim() : "";
        excelData.push([empNum, name, spec, type, status]);
    });

    try {
        const wb = XLSX.utils.book_new();
        const ws = XLSX.utils.aoa_to_sheet(excelData);
        XLSX.utils.book_append_sheet(wb, ws, "Sheet1");
        const fileName = checkedBoxes.length > 0 ? "Selected_Employees.xlsx" : "Employee_Records.xlsx";
        XLSX.writeFile(wb, fileName);
    } catch (e) {
        console.error("Export Error:", e);
        alert("Excel library not loaded. Please refresh the page.");
    }
}

// --- CHECKBOX SELECT ALL ---
document.getElementById("selectAll").addEventListener("change", function () {
    document.querySelectorAll('.row-check').forEach(cb => cb.checked = this.checked);
});

// --- CLOSE MODALS ON BACKDROP CLICK ---
window.onclick = function (event) {
    ["addEmployeeModal", "editEmployeeModal", "importEmployeeModal"].forEach(id => {
        if (event.target == document.getElementById(id)) document.getElementById(id).style.display = "none";
    });
};
