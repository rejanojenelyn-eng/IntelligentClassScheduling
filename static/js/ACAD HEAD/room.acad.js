let activeSidebarBuilding = "";

function filterBySidebar(btn, buildingName) {
    activeSidebarBuilding = buildingName.toUpperCase();
    document.querySelectorAll('.btn-building').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById("filterBuilding").value = "";
    filterRoomTable();
}

function filterRoomTable() {
    let search   = document.getElementById("searchRoom").value.toUpperCase();
    let type     = document.getElementById("filterType").value.toUpperCase();
    let dropdown = document.getElementById("filterBuilding").value.toUpperCase();
    let bldgFilter = dropdown !== "" ? dropdown : activeSidebarBuilding;

    let rows = document.getElementById("roomTable").getElementsByTagName("tr");
    for (let i = 1; i < rows.length; i++) {
        let roomName = rows[i].querySelector(".td-room-name").textContent.toUpperCase();
        let roomType = rows[i].querySelector(".td-room-type").textContent.toUpperCase();
        let building = rows[i].querySelector(".td-building").textContent.toUpperCase();

        let ok = roomName.indexOf(search) > -1
              && (type === "" || roomType === type)
              && (bldgFilter === "" || building === bldgFilter);
        rows[i].style.display = ok ? "" : "none";
    }
}

function toggleAvailabilityRow() {
    const row    = document.getElementById('availabilityRow');
    const banner = document.getElementById('availableBanner');
    row.classList.toggle('active');
    banner.style.display = row.classList.contains('active') ? 'block' : 'none';
}

let currentRoomOrder = 'asc';
function sortRoomTable() {
    let table = document.getElementById("roomTable");
    let rows  = Array.from(table.rows).slice(1);
    let dir   = currentRoomOrder === 'asc' ? 1 : -1;
    rows.sort((a, b) => {
        let na = a.querySelector(".td-room-name").textContent.trim();
        let nb = b.querySelector(".td-room-name").textContent.trim();
        return na.localeCompare(nb, undefined, {numeric: true}) * dir;
    });
    rows.forEach(r => table.appendChild(r));
    currentRoomOrder = currentRoomOrder === 'asc' ? 'desc' : 'asc';
    document.getElementById("roomSortText").innerText = currentRoomOrder === 'asc' ? 'Ascending' : 'Descending';
}

function exportRoomExcel() {
    let orig  = document.getElementById("roomTable");
    let clone = document.createElement("table");
    clone.appendChild(orig.querySelector("thead").cloneNode(true));
    let tbody = document.createElement("tbody");
    orig.querySelectorAll("tbody tr").forEach(row => {
        if (window.getComputedStyle(row).display !== "none") {
            let r = row.cloneNode(true);
            if (r.lastElementChild) r.removeChild(r.lastElementChild);
            tbody.appendChild(r);
        }
    });
    clone.appendChild(tbody);
    let wb = XLSX.utils.table_to_book(clone, {sheet: "PUPLC Rooms"});
    XLSX.writeFile(wb, "PUPLopez_Rooms_List.xlsx");
}
