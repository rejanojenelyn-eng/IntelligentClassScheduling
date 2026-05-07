let activeSidebarBuilding = "";

function openModal(id) { document.getElementById(id).style.display = 'flex'; }
function closeModal(id) { document.getElementById(id).style.display = 'none'; }

function toggleAvailabilityRow() {
    const row = document.getElementById('availabilityRow');
    const banner = document.getElementById('availableBanner');
    row.classList.toggle('active');
    banner.style.display = row.classList.contains('active') ? 'block' : 'none';
}

function showBuildingMenu(e, id, name) {
    e.preventDefault();
    const menu = document.getElementById('buildingMenu');
    menu.style.display = 'block';
    menu.style.left = e.pageX + 'px';
    menu.style.top = e.pageY + 'px';

    document.getElementById('ctxEditBldg').onclick = function () {
        document.getElementById('edit_bldg_id').value = id;
        document.getElementById('edit_bldg_name').value = name;
        openModal('modalEditBldg');
    };

    document.getElementById('ctxDeleteBldg').onclick = function () {
        document.getElementById('del_bldg_name_display').innerText = name;
        document.getElementById('confirmBldgDeleteLink').href = "/admin/delete_building/" + id;
        openModal('modalDeleteBldg');
    };
}

window.addEventListener('click', () => { document.getElementById('buildingMenu').style.display = 'none'; });

function openEditModal(id, name, type, capacity, bldgId) {
    document.getElementById('edit_room_id').value = id;
    document.getElementById('edit_room_name').value = name;
    document.getElementById('edit_room_type').value = type;
    document.getElementById('edit_room_capacity').value = capacity;
    document.getElementById('edit_room_bldg_id').value = bldgId;
    openModal('modalEditRoom');
}

function confirmDelete(id, name) {
    document.getElementById('del_room_name').innerText = name;
    document.getElementById('confirmDeleteLink').href = "/admin/delete_room/" + id;
    openModal('modalDeleteRoom');
}

function filterBySidebar(buildingName) {
    activeSidebarBuilding = buildingName.toUpperCase();
    let buttons = document.querySelectorAll('.btn-building');
    buttons.forEach(btn => btn.classList.remove('active'));
    if (window.event) window.event.target.classList.add('active');
    document.getElementById("filterBuilding").value = "";
    filterRoomTable();
}

function filterRoomTable() {
    let search = document.getElementById("searchRoom").value.toUpperCase();
    let bldgSelect = document.getElementById("filterBuilding").value.toUpperCase();
    let typeSelect = document.getElementById("filterType").value.toUpperCase();
    let buildingFilter = bldgSelect !== "" ? bldgSelect : activeSidebarBuilding;
    let tr = document.getElementById("roomTable").getElementsByTagName("tr");

    for (let i = 1; i < tr.length; i++) {
        let roomName = tr[i].querySelector(".td-room-name").textContent.toUpperCase();
        let roomType = tr[i].querySelector(".td-room-type").textContent.toUpperCase();
        let building = tr[i].querySelector(".td-building").textContent.toUpperCase();
        let matchSearch = roomName.indexOf(search) > -1;
        let matchType = typeSelect === "" || roomType === typeSelect;
        let matchBldg = buildingFilter === "" || building === buildingFilter;
        tr[i].style.display = (matchSearch && matchType && matchBldg) ? "" : "none";
    }
}

function exportRoomExcel() {
    let originalTable = document.getElementById("roomTable");
    let cloneTable = document.createElement("table");
    let thead = originalTable.querySelector("thead").cloneNode(true);
    thead.rows[0].deleteCell(-1);
    cloneTable.appendChild(thead);
    let tbody = document.createElement("tbody");
    let allRows = originalTable.querySelectorAll("tbody tr");
    allRows.forEach((row) => {
        if (window.getComputedStyle(row).display !== "none") {
            let cloneRow = row.cloneNode(true);
            cloneRow.deleteCell(-1);
            tbody.appendChild(cloneRow);
        }
    });
    cloneTable.appendChild(tbody);
    let wb = XLSX.utils.table_to_book(cloneTable, { sheet: "Rooms" });
    XLSX.writeFile(wb, "PUPLopez_Rooms_Export.xlsx");
}
