// ROOM_ID, ACTIVE_AY_ID, ACTIVE_SEM, CURRENT_BLDG_ID, ROOMS_BY_BLDG are defined inline in the HTML template

const TIME_SLOTS = [
    '7:30 AM','8:00 AM','8:30 AM','9:00 AM','9:30 AM','10:00 AM','10:30 AM',
    '11:00 AM','11:30 AM','12:00 PM','12:30 PM','1:00 PM','1:30 PM','2:00 PM',
    '2:30 PM','3:00 PM','3:30 PM','4:00 PM','4:30 PM','5:00 PM','5:30 PM',
    '6:00 PM','6:30 PM','7:00 PM','7:30 PM','8:00 PM','8:30 PM','9:00 PM'
];
const DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];

let currentRoomId = ROOM_ID;

function renderSchedule(sessions) {
    const TOTAL_SLOTS = TIME_SLOTS.length;
    const grid = {};
    for (let s = 1; s <= TOTAL_SLOTS; s++) {
        grid[s] = {};
        for (let d = 0; d < DAYS.length; d++) grid[s][d] = null;
    }

    sessions.forEach(sess => {
        const dayIdx = DAYS.indexOf(sess.daydesc);
        if (dayIdx === -1) return;
        const start = sess.starttimeid, end = sess.endtimeid;
        if (!start || !end || start >= end) return;
        if (grid[start] && grid[start][dayIdx] !== null) return;

        const span = end - start;
        if (grid[start]) grid[start][dayIdx] = { sess, span };
        for (let s = start + 1; s < end; s++) {
            if (grid[s]) grid[s][dayIdx] = 'skip';
        }
    });

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
                td.className = 'rd-cell';
            } else {
                td.className = 'rd-cell rd-pill-cell';
                td.rowSpan = cell.span;
                const pill = document.createElement('div');
                pill.className = 'rd-pill';
                const subj = (cell.sess.subjectname || cell.sess.subjectcode || '').toUpperCase();
                const instr = cell.sess.instructor || '';
                pill.innerHTML = `<span class="rd-pill-subj">${subj}</span><span class="rd-pill-instr">${instr}</span>`;
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
        renderSchedule(Array.isArray(data) ? data : []);
    } catch (e) {
        console.error('Failed to load room schedule:', e);
        renderSchedule([]);
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
        btn.className = 'rd-room-btn' + (r.roomid === currentRoomId ? ' active' : '');
        btn.textContent = r.roomname;
        btn.onclick = () => selectRoom(btn, r.roomid, r.roomname);
        list.appendChild(btn);
    });
}

function selectRoom(btn, roomId, roomName) {
    document.querySelectorAll('.rd-room-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentRoomId = roomId;
    document.getElementById('rdCalRoomLabel').textContent = roomName;
    loadRoomSchedule(roomId);
}

function switchBuilding(btn, buildingId) {
    document.querySelectorAll('.rd-tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    populateRoomSidebar(buildingId);
}

(function init() {
    renderSchedule([]);
    populateRoomSidebar(CURRENT_BLDG_ID);
    loadRoomSchedule(ROOM_ID);
})();
