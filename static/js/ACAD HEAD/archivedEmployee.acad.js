// --- FILTERING ---
function filterTable() {
    let nameInput = document.getElementById("searchInput").value.toUpperCase();
    let typeInput = document.getElementById("typeFilter").value.toUpperCase();
    let statusInput = document.getElementById("statusFilter").value.toUpperCase();
    let specInput = document.getElementById("specFilter").value.toUpperCase();
    let rows = document.querySelectorAll("#archiveTable tbody tr");

    rows.forEach(row => {
        let nameCol = row.querySelector(".emp-name").textContent.toUpperCase();
        let typeCol = row.querySelector(".emp-type").textContent.toUpperCase().trim();
        let statusCol = row.querySelector(".emp-status").textContent.toUpperCase().trim();
        let specCol = row.querySelector(".emp-spec").textContent.toUpperCase().trim();

        let matchName = nameCol.includes(nameInput);
        let matchType = (typeInput === "" || typeCol.includes(typeInput));
        let matchStatus = (statusInput === "" || statusCol === statusInput);
        let matchSpec = (specInput === "" || specCol.includes(specInput));

        row.style.display = (matchName && matchType && matchStatus && matchSpec) ? "" : "none";
    });
}

// --- SORTING ---
let currentOrder = 'asc';

function sortTable() {
    const table = document.getElementById("archiveTable");
    const tbody = table.querySelector("tbody");
    const rows = Array.from(tbody.querySelectorAll("tr"));
    const direction = currentOrder === 'asc' ? -1 : 1;

    rows.sort((a, b) => {
        const nameA = a.querySelector(".emp-name").innerText.toLowerCase().trim();
        const nameB = b.querySelector(".emp-name").innerText.toLowerCase().trim();
        return nameA.localeCompare(nameB) * direction;
    });

    currentOrder = (currentOrder === 'asc') ? 'desc' : 'asc';

    const sortLabel = document.getElementById("sortDirection");
    const sortIcon = document.getElementById("sortIcon");
    if (currentOrder === 'asc') {
        sortLabel.innerText = 'Ascending';
        sortIcon.className = 'fas fa-sort-amount-up';
    } else {
        sortLabel.innerText = 'Descending';
        sortIcon.className = 'fas fa-sort-amount-down-alt';
    }

    rows.forEach(row => tbody.appendChild(row));
}

// --- EXPORT TO EXCEL ---
function exportToExcel() {
    const table = document.getElementById("archiveTable");
    const rowsToExport = Array.from(table.querySelectorAll("tbody tr")).filter(row => row.style.display !== 'none');

    if (rowsToExport.length === 0) { alert("No data available to export."); return; }

    const data = [["Employee Number", "Full Name", "Specialization", "Employment Type", "Status", "Date Archived"]];

    rowsToExport.forEach(row => {
        data.push([
            row.cells[0].innerText.trim(),
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
