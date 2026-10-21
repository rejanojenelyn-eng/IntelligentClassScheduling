// ROOM_ID, ACTIVE_AY_ID, ACTIVE_SEM, CURRENT_BLDG_ID, ROOMS_BY_BLDG, ROOM_INFO are defined inline in the HTML template

const TIME_SLOTS = [
    '7:30 AM','8:00 AM','8:30 AM','9:00 AM','9:30 AM','10:00 AM','10:30 AM',
    '11:00 AM','11:30 AM','12:00 PM','12:30 PM','1:00 PM','1:30 PM','2:00 PM',
    '2:30 PM','3:00 PM','3:30 PM','4:00 PM','4:30 PM','5:00 PM','5:30 PM',
    '6:00 PM','6:30 PM','7:00 PM','7:30 PM','8:00 PM','8:30 PM','9:00 PM'
];
const DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];

let currentRoomId = ROOM_ID;
let currentRoom   = ROOM_INFO;

const esc = t => String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// Room info card + the "Unavailable" shading follow the selected room.
function showRoomInfo(room) {
    currentRoom = room;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('rdInfoName', room.roomname || '');
    set('rdInfoSub', [room.buildingname, room.roomtype].filter(Boolean).join(' • '));
    set('rdInfoCap', room.roomcapacity ?? '—');
    set('rdInfoBldg', room.buildingname || '—');
    set('rdCalRoomLabel', room.roomname || 'Select a room');
    const st = document.getElementById('rdInfoStatus');
    if (st) {
        const inactive = room.isactive === false;
        st.classList.toggle('rd-inactive', inactive);
        st.innerHTML = `<span class="rd-dot"></span> ${inactive ? 'Room Inactive' : 'Published Schedule'}`;
    }
}

function renderSchedule(sessions) {
    const TOTAL_SLOTS = TIME_SLOTS.length;
    const unavailable = currentRoom && currentRoom.isactive === false;
    const grid = {};
    for (let s = 1; s <= TOTAL_SLOTS; s++) {
        grid[s] = {};
        for (let d = 0; d < DAYS.length; d++) grid[s][d] = null;
    }

    // One block per day + start + subject: a merged class (several sections in one meeting)
    // shows all its sections in the one block.
    const blocks = {};
    sessions.forEach(sess => {
        const dayIdx = DAYS.indexOf(sess.daydesc);
        const start = sess.starttimeid, end = sess.endtimeid;
        if (dayIdx === -1 || !start || !end || start >= end) return;
        const key = `${dayIdx}|${start}|${sess.subjectcode}`;
        if (blocks[key]) {
            if (sess.sectionname && !blocks[key].sections.includes(sess.sectionname)) blocks[key].sections.push(sess.sectionname);
            return;
        }
        blocks[key] = { sess, dayIdx, start, end, sections: sess.sectionname ? [sess.sectionname] : [] };
    });
    Object.values(blocks).sort((a, b) => a.start - b.start).forEach(b => {
        if (!grid[b.start] || grid[b.start][b.dayIdx] !== null) return;
        grid[b.start][b.dayIdx] = { ...b, span: b.end - b.start };
        for (let s = b.start + 1; s < b.end; s++) if (grid[s]) grid[s][b.dayIdx] = 'skip';
    });
    const count = document.getElementById('rdInfoCount');
    if (count) count.textContent = Object.keys(blocks).length;

    const tbody = document.getElementById('rdCalBody');
    tbody.innerHTML = '';
    for (let slot = 1; slot <= TOTAL_SLOTS; slot++) {
        const tr = document.createElement('tr');
        const tdTime = document.createElement('td');
        tdTime.className = 'rd-time-col';
        tdTime.textContent = TIME_SLOTS[slot - 1];
        tr.appendChild(tdTime);

        for (let d = 0; d < DAYS.length; d++) {
            const cell = grid[slot][d];
            if (cell === 'skip') continue;
            const td = document.createElement('td');
            if (!cell) {
                td.className = 'rd-cell' + (unavailable ? ' rd-unavail' : '');
            } else {
                td.className = 'rd-cell rd-pill-cell';
                td.rowSpan = cell.span;
                const s = cell.sess;
                const time = `${TIME_SLOTS[cell.start - 1]} – ${TIME_SLOTS[cell.end - 1] || ''}`;
                const pill = document.createElement('div');
                pill.className = 'rd-pill';
                pill.title = [s.subjectname, s.instructor].filter(Boolean).join(' — ');
                pill.innerHTML = `<span class="rd-pill-code">${esc(s.subjectcode || s.subjectname)}</span>`
                    + (cell.sections.length ? `<span class="rd-pill-sect">${esc(cell.sections.join(' + '))}</span>` : '')
                    + `<span class="rd-pill-time">${esc(time)}</span>`
                    + (s.instructor ? `<span class="rd-pill-instr">${esc(s.instructor)}</span>` : '');
                td.appendChild(pill);
            }
            tr.appendChild(td);
        }
        tbody.appendChild(tr);
    }
}

async function loadRoomSchedule(roomId) {
    const loading = document.getElementById('rdLoading');
    loading.style.display = 'block';
    renderSchedule([]);
    try {
        const params = new URLSearchParams();
        if (ACTIVE_AY_ID) params.set('ay_id', ACTIVE_AY_ID);
        if (ACTIVE_SEM)   params.set('semester', ACTIVE_SEM);
        const res = await fetch(`/api/get_room_schedule/${roomId}?${params}`);
        const data = await res.json();
        if (roomId !== currentRoomId) return;
        renderSchedule(Array.isArray(data) ? data : []);
    } catch (e) {
        console.error('Failed to load room schedule:', e);
        if (roomId === currentRoomId) renderSchedule([]);
    } finally {
        loading.style.display = 'none';
    }
}

function populateRoomSidebar(buildingId) {
    const list = document.getElementById('rdRoomList');
    list.innerHTML = '';
    const rooms = ROOMS_BY_BLDG[buildingId] || [];
    if (!rooms.length) {
        list.innerHTML = '<div class="rd-no-rooms">No rooms</div>';
        return;
    }
    rooms.forEach(r => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'rd-room-btn' + (r.roomid === currentRoomId ? ' active' : '');
        btn.dataset.name = String(r.roomname || '').toLowerCase();
        btn.innerHTML = `<span>${esc(r.roomname)}</span><i class="fas fa-chevron-right"></i>`;
        btn.onclick = () => selectRoom(btn, r);
        list.appendChild(btn);
    });
    filterRooms(document.getElementById('rdRoomSearch')?.value || '');
}

function filterRooms(q) {
    const needle = String(q || '').trim().toLowerCase();
    let shown = 0;
    document.querySelectorAll('.rd-room-btn').forEach(b => {
        const hit = !needle || b.dataset.name.includes(needle);
        b.style.display = hit ? '' : 'none';
        if (hit) shown++;
    });
    let none = document.getElementById('rdNoMatch');
    if (!shown && document.querySelector('.rd-room-btn')) {
        if (!none) {
            none = document.createElement('div');
            none.id = 'rdNoMatch'; none.className = 'rd-no-rooms';
            document.getElementById('rdRoomList').appendChild(none);
        }
        none.textContent = 'No room matches your search.';
    } else if (none) none.remove();
}

function selectRoom(btn, room) {
    document.querySelectorAll('.rd-room-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentRoomId = room.roomid;
    showRoomInfo(room);
    loadRoomSchedule(room.roomid);
}

function switchBuilding(btn, buildingId) {
    document.querySelectorAll('.rd-tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    btn.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    currentRoomId = null;
    const label = document.getElementById('rdCalRoomLabel');
    if (label) label.textContent = 'Select a room';
    renderSchedule([]);
    populateRoomSidebar(buildingId);
    // Open the building's first room right away, as the design shows.
    const first = document.querySelector('.rd-room-btn');
    if (first) first.click();
}

(function init() {
    showRoomInfo(ROOM_INFO);
    renderSchedule([]);
    populateRoomSidebar(CURRENT_BLDG_ID);
    loadRoomSchedule(ROOM_ID);
})();
