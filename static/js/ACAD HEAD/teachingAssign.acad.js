// OWN_EMP_NUM, AY_ID, SEM are defined inline in the HTML template above this script

const DAYS           = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
const PALETTE        = ['#16a085','#27ae60','#2980b9','#8e44ad','#2c3e50','#f39c12','#d35400','#c0392b'];
const WEEKDAYS_SET   = new Set(['Monday','Tuesday','Wednesday','Thursday','Friday']);
const REGULAR_END_SLOT = 19;

let _sessions      = [];   // official published schedule sessions
let _localSessions = [];   // published local-arrangement sessions for this user
let _activeTab     = 'weekly';
let _scheduleMode  = 'official'; // 'official' | 'local'

function subjectColor(code) {
    let h = 0;
    for (let i = 0; i < code.length; i++) h = code.charCodeAt(i) + ((h << 5) - h);
    return PALETTE[Math.abs(h) % PALETTE.length];
}

// ── Tab switching ────────────────────────────────────────────────────────────
function showTab(name, el) {
    _activeTab = name;
    document.getElementById('tab-weekly').style.display     = name === 'weekly'     ? '' : 'none';
    document.getElementById('tab-assignment').style.display = name === 'assignment' ? '' : 'none';
    document.querySelectorAll('.tab-link').forEach(t => t.classList.remove('active'));
    if (el) el.classList.add('active');
    if (name === 'weekly')     renderCalendar();
    if (name === 'assignment') renderTables();
}

// ── Schedule mode toggle (weekly view only) ──────────────────────────────────
function onScheduleToggle(isOfficial) {
    _scheduleMode = isOfficial ? 'official' : 'local';

    const lblLocal    = document.getElementById('lbl-local');
    const lblOfficial = document.getElementById('lbl-official');
    if (isOfficial) {
        lblLocal.classList.remove('active-label');
        lblOfficial.classList.add('active-label');
    } else {
        lblOfficial.classList.remove('active-label');
        lblLocal.classList.add('active-label');
    }
    renderCalendar();
}

// ── Data loading ─────────────────────────────────────────────────────────────
async function loadData() {
    if (!OWN_EMP_NUM) return;
    await Promise.all([loadOfficialData(), loadLocalData()]);
    if (_activeTab === 'weekly')     renderCalendar();
    if (_activeTab === 'assignment') renderTables();
}

async function loadOfficialData() {
    try {
        const url = `/api/get_faculty_schedule?emp_num=${encodeURIComponent(OWN_EMP_NUM)}&ay_id=${encodeURIComponent(AY_ID)}&semester=${encodeURIComponent(SEM)}`;
        const resp = await fetch(url);
        _sessions = await resp.json();
    } catch(e) {
        console.error('loadOfficialData error:', e);
        _sessions = [];
    }
}

async function loadLocalData() {
    try {
        const url = `/api/faculty/my_local_schedule?ay_id=${encodeURIComponent(AY_ID)}&semester=${encodeURIComponent(SEM)}`;
        const resp = await fetch(url);
        if (!resp.ok) { _localSessions = []; return; }
        _localSessions = await resp.json();
    } catch(e) {
        console.error('loadLocalData error:', e);
        _localSessions = [];
    }
}

// ── Calendar renderer ────────────────────────────────────────────────────────
function renderCalendar() {
    const sessions = _scheduleMode === 'local' ? _localSessions : _sessions;

    const inner   = document.getElementById('calInner');
    const table   = document.getElementById('calTable');
    const emptyEl = document.getElementById('calEmptyMsg');

    inner.querySelectorAll('.cal-pill').forEach(p => p.remove());

    if (!sessions.length) {
        emptyEl.style.display = 'block';
        return;
    }
    emptyEl.style.display = 'none';

    const firstCell = table.querySelector('tbody td:nth-child(2)');
    const tcCol     = table.querySelector('.tc');
    const thead     = table.querySelector('thead');

    if (!firstCell || firstCell.offsetWidth === 0) {
        requestAnimationFrame(renderCalendar);
        return;
    }

    const colW    = firstCell.offsetWidth;
    const rowH    = firstCell.offsetHeight;
    const leftOff = tcCol.offsetWidth;
    const topOff  = thead.offsetHeight;

    const byDay = {};
    sessions.forEach(s => {
        if (!s.starttimeid || !s.endtimeid) return;
        (byDay[s.daydesc] = byDay[s.daydesc] || []).push(s);
    });

    Object.keys(byDay).forEach(day => {
        const dayIdx = DAYS.indexOf(day);
        if (dayIdx < 0) return;
        const daySessions = byDay[day].slice().sort((a,b) => a.starttimeid - b.starttimeid);

        daySessions.forEach((sess, idx) => {
            const s = sess.starttimeid, e = sess.endtimeid;
            let overlapCount = 0, overlapIndex = 0;
            daySessions.forEach((other, oIdx) => {
                if (s < other.endtimeid && e > other.starttimeid) {
                    overlapCount++;
                    if (idx > oIdx) overlapIndex++;
                }
            });

            const pill = document.createElement('div');
            pill.className = 'cal-pill' + (_scheduleMode === 'local' ? ' local-pill' : '');
            pill.style.backgroundColor = subjectColor(sess.subjectcode);

            const w = (colW - 6) / (overlapCount || 1);
            pill.style.width  = (w - 3) + 'px';
            pill.style.height = Math.max((e - s) * rowH - 4, 18) + 'px';
            pill.style.left   = (leftOff + dayIdx * colW + overlapIndex * w + 3) + 'px';
            pill.style.top    = (topOff + (s - 1) * rowH + 2) + 'px';

            const yearSec = sess.yearlevel ? `${sess.yearlevel} - ${sess.programcode || ''}` : (sess.programcode || '');
            pill.innerHTML = `
                <div class="cal-pill-code">${sess.subjectcode}</div>
                <div class="cal-pill-sub">${yearSec || sess.roomname || ''}</div>`;
            pill.title = `${sess.subjectcode} – ${sess.subjectname}\n${sess.time_range || ''}\nRoom: ${sess.roomname || 'TBA'}\nYear/Section: ${yearSec || '—'}`;
            pill.onclick = () => openDetail(sess);
            inner.appendChild(pill);
        });
    });
}

// ── Teaching Assignment table renderer (official schedule only) ──────────────
function renderTables() {
    // TA always uses official (published) schedule – local arrangements are excluded
    const regular = _sessions.filter(s => WEEKDAYS_SET.has(s.daydesc) && s.endtimeid <= REGULAR_END_SLOT);
    const pt      = _sessions.filter(s => !WEEKDAYS_SET.has(s.daydesc) || s.endtimeid > REGULAR_END_SLOT);

    const totalReg = regular.reduce((sum, s) => sum + (parseFloat(s.creditunits) || 0), 0);
    const totalPT  = pt.reduce((sum, s) => sum + (parseFloat(s.creditunits) || 0), 0);
    const totalAll = totalReg + totalPT;

    document.getElementById('total-regular-units').textContent = totalReg || '—';
    document.getElementById('total-pt-units').textContent      = totalPT  || '—';
    document.getElementById('total-overall-units').textContent = totalAll || '—';

    const makeRow = row => {
        const ys = row.yearlevel ? `${row.yearlevel} - ${row.programcode || ''}` : (row.programcode || '');
        return `<tr>
            <td style="font-weight:800;color:#630100;">${row.subjectcode}</td>
            <td>${row.subjectname}</td>
            <td>${row.creditunits || '—'}</td>
            <td>${ys || '—'}</td>
            <td>${row.time_range || ''}</td>
            <td>${row.daydesc}</td>
            <td>${row.roomname || 'TBA'}</td>
            <td>${row.effectivity || '—'}</td>
        </tr>`;
    };

    document.getElementById('tbl-regular').innerHTML = regular.length
        ? regular.map(makeRow).join('')
        : `<tr><td colspan="8" style="text-align:center;color:#999;padding:16px;">No regular load found.</td></tr>`;

    document.getElementById('tbl-pt').innerHTML = pt.length
        ? pt.map(makeRow).join('')
        : `<tr><td colspan="8" style="text-align:center;color:#999;padding:16px;">No part-time load found.</td></tr>`;
}

// ── Detail modal ─────────────────────────────────────────────────────────────
function openDetail(sess) {
    document.getElementById('det-code').textContent    = sess.subjectcode;
    document.getElementById('det-name').textContent    = sess.subjectname;
    document.getElementById('det-day').textContent     = sess.daydesc;
    document.getElementById('det-time').textContent    = sess.time_range;
    document.getElementById('det-room').textContent    = sess.roomname || 'TBA';
    document.getElementById('det-section').textContent = sess.yearlevel ? `${sess.yearlevel} - ${sess.programcode || ''}` : (sess.programcode || '—');
    document.getElementById('det-units').textContent   = sess.creditunits;
    document.getElementById('det-status').innerHTML    =
        sess.status === 'Published'
            ? '<span class="badge-pub">Published</span>'
            : '<span class="badge-draft">Draft</span>';
    document.getElementById('detailModal').classList.add('active');
}

function closeDetail() {
    document.getElementById('detailModal').classList.remove('active');
}

document.getElementById('detailModal').addEventListener('click', function(e) {
    if (e.target === this) closeDetail();
});

window.addEventListener('DOMContentLoaded', loadData);
