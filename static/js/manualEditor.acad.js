// allRooms and allFaculty are defined inline in the HTML template above this script

const timeSlots = ['07:30 AM', '08:00 AM', '08:30 AM', '09:00 AM', '09:30 AM', '10:00 AM', '10:30 AM', '11:00 AM', '11:30 AM', '12:00 PM', '12:30 PM', '01:00 PM', '01:30 PM', '02:00 PM', '02:30 PM', '03:00 PM', '03:30 PM', '04:00 PM', '04:30 PM', '05:00 PM', '05:30 PM', '06:00 PM', '06:30 PM', '07:00 PM', '07:30 PM', '08:00 PM', '08:30 PM', '09:00 PM'];

let pendingManualSchedule = [];
let hiddenDbSchedules = new Set();
let currentBldgId = 'ALL';
window.currentEditSession = null;
let _splitMode = null;

let _subjInfo         = null;
let _facInfo          = null;
let _existingDays     = [];
let _skipExistingCheck    = false;
let _hasExistingSchedule  = false;
let _pendingExistingSessions = [];

const DAY_PAIR_MAP = {
    Monday: 'Thursday', Thursday: 'Monday',
    Tuesday: 'Friday',  Friday: 'Tuesday',
    Wednesday: 'Saturday', Saturday: 'Wednesday'
};
const MAN_WEEKDAYS = new Set(['Monday','Tuesday','Wednesday','Thursday','Friday']);

function showSplitChoiceModal(totalHours) {
    return new Promise(resolve => {
        document.getElementById('splitChoiceHours').innerHTML = `<strong>${totalHours} hours</strong>`;
        document.getElementById('splitChoiceModal').classList.add('active');
        window._splitChoiceResolve = resolve;
    });
}

function setSplitMode(mode) {
    _splitMode = mode;
    document.getElementById('splitChoiceModal').classList.remove('active');
    if (window._splitChoiceResolve) { window._splitChoiceResolve(mode); window._splitChoiceResolve = null; }
}

function updateSundayOption() {
    const daySelect = document.getElementById('sel_day');
    const isAllowed = _subjInfo && _subjInfo.is_sunday_allowed;
    const existing  = Array.from(daySelect.options).find(o => o.value === 'Sunday' || o.textContent === 'Sunday');
    if (isAllowed && !existing) {
        const opt = document.createElement('option');
        opt.value = 'Sunday';
        opt.textContent = 'Sunday';
        daySelect.appendChild(opt);
    } else if (!isAllowed && existing) {
        if (daySelect.value === existing.value) daySelect.value = '';
        existing.remove();
    }
}

function getScheduledHoursForSubject() {
    const subj = document.getElementById('sel_subj').value;
    const ay   = document.getElementById('sel_ay').value;
    const sem  = document.getElementById('sel_sem').value;
    if (!subj) return 0;
    let hours = 0;
    for (const c of pendingManualSchedule) {
        if (c.subject_code !== subj || c.ay !== ay || c.sem !== sem) continue;
        if (window.currentEditSession && c.temp_id === window.currentEditSession.temp_id) continue;
        const si = timeSlots.indexOf(c.start_time), ei = timeSlots.indexOf(c.end_time);
        if (si >= 0 && ei > si) hours += (ei - si) * 0.5;
    }
    for (const s of _pendingExistingSessions) {
        if (window.currentEditSession &&
            s.subjectcode === window.currentEditSession.subjectcode &&
            s.daydesc     === window.currentEditSession.daydesc &&
            s.starttimeid === window.currentEditSession.starttimeid) continue;
        hours += (s.endtimeid - s.starttimeid) * 0.5;
    }
    return hours;
}

function getRemainingHours() {
    if (!_subjInfo) return null;
    return Math.max(0, _subjInfo.total_hours - getScheduledHoursForSubject());
}

function updateHoursProgressNote() {
    const note = document.getElementById('hours_progress_note');
    if (!note) return;
    if (!_subjInfo || _splitMode !== true) {
        note.className = 'constraint-note';
        note.textContent = '';
        return;
    }
    const total = _subjInfo.total_hours;
    const remaining = getRemainingHours();
    const scheduled = total - remaining;
    if (scheduled === 0) {
        note.className = 'constraint-note info';
        note.innerHTML = `⚡ Split mode: ${total}h total — select day and time for the first session.`;
    } else if (remaining <= 0) {
        note.className = 'constraint-note info';
        note.innerHTML = `✓ All ${total}h scheduled across sessions.`;
    } else {
        note.className = 'constraint-note warn';
        note.innerHTML = `⚠ ${scheduled}h placed — <strong>${remaining}h remaining</strong>. Add another session to complete.`;
    }
}

const REG_END_IDX        = 18;
const DES_START_IDX      =  1;
const DES_END_IDX        = 19;
const DES_NIGHT_END_IDX  = 21;

function _getMaxEndIdx() {
    const day  = document.getElementById('sel_day').value;
    const isWkd = MAN_WEEKDAYS.has(day);
    const tn   = _facInfo ? _facInfo.typename : null;
    if (tn === 'Designee' && isWkd && day) {
        return (_facInfo.night_service > 0) ? DES_NIGHT_END_IDX : DES_END_IDX;
    }
    return timeSlots.length - 1;
}

function _populateEndTimes(maxEndIdx) {
    const startSel = document.getElementById('sel_start_time');
    const endSel   = document.getElementById('sel_end_time');
    const prevEnd  = endSel.value;
    const startIdx = timeSlots.indexOf(startSel.value);
    let maxIdx     = (maxEndIdx !== undefined) ? maxEndIdx : _getMaxEndIdx();

    if (_subjInfo && startIdx >= 0) {
        const remaining = getRemainingHours();
        if (remaining !== null && remaining > 0) {
            const unitMax = startIdx + Math.round(remaining / 0.5);
            if (unitMax < maxIdx) maxIdx = unitMax;
        }
    }

    endSel.innerHTML = '<option value="">— Select End Time —</option>';
    const from = startIdx >= 0 ? startIdx + 1 : 0;
    for (let i = from; i <= maxIdx; i++) {
        const opt = document.createElement('option');
        opt.value = timeSlots[i];
        opt.textContent = timeSlots[i];
        endSel.appendChild(opt);
    }
    if (prevEnd && endSel.querySelector(`option[value="${prevEnd}"]`)) {
        endSel.value = prevEnd;
    }
    updateSummary();
}

async function updateTimeDropdowns() {
    const startSel = document.getElementById('sel_start_time');
    const note     = document.getElementById('time_block_note');
    note.className = 'constraint-note';
    note.textContent = '';

    const day    = document.getElementById('sel_day').value;
    const isWkd  = MAN_WEEKDAYS.has(day);
    const tn     = _facInfo ? _facInfo.typename : null;
    const hasNS  = _facInfo && _facInfo.night_service > 0;

    let minStartIdx = 0;
    let maxEndIdx   = timeSlots.length - 1;

    if (tn && day) {
        if (tn === 'Part-Time' && isWkd) {
            minStartIdx = REG_END_IDX;
        } else if (tn === 'Designee' && isWkd) {
            minStartIdx = DES_START_IDX;
            maxEndIdx   = hasNS ? DES_NIGHT_END_IDX : DES_END_IDX;
        }
    }
    const maxStartIdx = maxEndIdx - 1;

    const prevStart = startSel.value;
    startSel.innerHTML = '<option value="">— Select Start Time —</option>';
    for (let i = minStartIdx; i <= maxStartIdx; i++) {
        const opt = document.createElement('option');
        opt.value = timeSlots[i];
        opt.textContent = timeSlots[i];
        startSel.appendChild(opt);
    }
    if (prevStart && startSel.querySelector(`option[value="${prevStart}"]`)) {
        startSel.value = prevStart;
    }

    _populateEndTimes(maxEndIdx);

    if (!day) {
        note.className = 'constraint-note info';
        note.textContent = 'ℹ Select a day to filter available times by faculty type.';
    } else if (tn === 'Part-Time' && isWkd) {
        note.className = 'constraint-note info';
        note.textContent = 'ℹ Part-Time faculty on weekdays: 4:30 PM onwards only.';
    } else if (tn === 'Designee' && isWkd) {
        const cutoff = hasNS ? '6:00 PM' : '5:00 PM';
        note.className = 'constraint-note info';
        note.textContent = `ℹ Designee faculty weekdays: 8:00 AM – ${cutoff} only.`;
    }
}

function onStartTimeChange() { _populateEndTimes(); updateSummary(); }
function onEndTimeChange() { updateSummary(); }

function updateTimeBlockFromTimes(start, end) {
    const startSel = document.getElementById('sel_start_time');
    const endSel   = document.getElementById('sel_end_time');
    if (startSel.querySelector(`option[value="${start}"]`)) startSel.value = start;
    _populateEndTimes();
    if (endSel.querySelector(`option[value="${end}"]`)) endSel.value = end;
    updateSummary();
}

async function fetchExistingDays() {
    const subj = document.getElementById('sel_subj').value;
    const ay   = document.getElementById('sel_ay').value;
    const sem  = document.getElementById('sel_sem').value;
    const prog = document.getElementById('sel_prog').value;
    const yl   = document.getElementById('sel_year').value;
    _existingDays = [];
    if (!subj || !ay || !sem) return;
    try {
        const url = `/api/manual/existing_days?subject_code=${encodeURIComponent(subj)}&ay_id=${encodeURIComponent(ay)}&semester=${encodeURIComponent(sem)}&program=${encodeURIComponent(prog||'')}&year_level=${encodeURIComponent(yl||'')}`;
        const d = await fetch(url).then(r => r.json());
        _existingDays = d.success ? d.days : [];
    } catch(e) { _existingDays = []; }
}

async function onFacultySelect(empNum) {
    _facInfo = null;
    if (empNum) {
        try {
            const d = await fetch(`/api/manual/faculty_info?emp_num=${encodeURIComponent(empNum)}`).then(r => r.json());
            if (d.success) _facInfo = d;
        } catch(e) { _facInfo = null; }
    }
    await updateTimeDropdowns();
}

async function onDayChange() {
    const day  = document.getElementById('sel_day').value;
    const note = document.getElementById('day_constraint_note');
    note.className = 'constraint-note';
    note.textContent = '';

    if (day === 'Sunday' && _subjInfo && !_subjInfo.is_sunday_allowed) {
        note.className = 'constraint-note error';
        note.textContent = '⛔ Sunday is only allowed for OU and NSTP subjects.';
    }

    if (_subjInfo && Math.abs(_subjInfo.total_hours - 1.5) < 0.1 && day && day !== 'Sunday') {
        if (_existingDays.length > 0 && !_existingDays.includes(day)) {
            const req = DAY_PAIR_MAP[_existingDays[0]];
            if (req && day !== req) {
                note.className = 'constraint-note warn';
                note.textContent = `⚠ This subject already has a ${_existingDays[0]} session — paired day must be ${req}.`;
            } else if (req && day === req) {
                note.className = 'constraint-note info';
                note.textContent = `✓ Correct paired day for the existing ${_existingDays[0]} session.`;
            }
        } else if (_existingDays.length === 0 && DAY_PAIR_MAP[day]) {
            note.className = 'constraint-note info';
            note.textContent = `ℹ First session on ${day}. Second session must be on ${DAY_PAIR_MAP[day]}.`;
        }
    }

    await updateTimeDropdowns();
}

function showExistingSchedModal(sessions) {
    _pendingExistingSessions = sessions;
    const subj = document.getElementById('sel_subj');
    const subjText = subj.options[subj.selectedIndex]?.text || subj.value;

    const sessHours   = sessions.reduce((sum, s) => sum + (s.endtimeid - s.starttimeid) * 0.5, 0);
    const totalHours  = _subjInfo ? _subjInfo.total_hours : 0;
    const remainHours = totalHours > 0 ? Math.max(0, totalHours - sessHours) : 0;

    const addBtn = document.getElementById('btnAddNewSession');
    if (addBtn) {
        if (_splitMode === true && remainHours > 0.1) {
            addBtn.style.display = 'inline-block';
            addBtn.textContent = `+ Add Another Session (${remainHours}h remaining)`;
            document.getElementById('existingSchedDesc').textContent =
                `"${subjText}" has ${sessHours}h of ${totalHours}h scheduled. Edit an existing session or add another to complete.`;
        } else {
            addBtn.style.display = 'none';
            document.getElementById('existingSchedDesc').textContent =
                `"${subjText}" already has ${sessions.length} scheduled session${sessions.length > 1 ? 's' : ''} for the selected term. Select a session below to edit it.`;
        }
    } else {
        document.getElementById('existingSchedDesc').textContent =
            `"${subjText}" already has ${sessions.length} scheduled session${sessions.length > 1 ? 's' : ''} for the selected term. Select a session below to edit it.`;
    }

    const tbody = document.getElementById('existingSchedList');
    tbody.innerHTML = '';
    sessions.forEach((s, i) => {
        const statusCls = (s.status || '').toLowerCase() === 'published'
            ? 'badge-status-pub' : 'badge-status-draft';
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${s.daydesc || '—'}</strong></td>
            <td>${s.start_fmt || ''} – ${s.end_fmt || ''}</td>
            <td>${s.roomname || 'TBA'}</td>
            <td>${s.instructor || '—'}</td>
            <td><span class="${statusCls}">${s.status || ''}</span></td>
            <td><button class="btn-edit-sess" onclick="editExistingSession(${i})">Edit</button></td>`;
        tbody.appendChild(tr);
    });
    document.getElementById('existingSchedModal').classList.add('active');
}

function closeExistingSchedModal() {
    document.getElementById('existingSchedModal').classList.remove('active');
    if (_hasExistingSchedule) {
        document.getElementById('sel_subj').value = '';
        _hasExistingSchedule = false;
        initDSSMenus();
    }
}

function addAnotherSession() {
    document.getElementById('existingSchedModal').classList.remove('active');
    _hasExistingSchedule = false;
    updateHoursProgressNote();
}

async function editExistingSession(idx) {
    closeExistingSchedModal();
    _skipExistingCheck = true;
    _hasExistingSchedule = false;
    const sess = _pendingExistingSessions[idx];
    await window.handlePillClick(encodeURIComponent(JSON.stringify(sess)));
}

let pendingLeaveUrl = null;
window.isLeavingIntentionally = false;

function hasUnsavedChanges() {
    return pendingManualSchedule.length > 0 || window.currentEditSession !== null;
}

document.addEventListener('click', function(e) {
    if (window.isLeavingIntentionally) return;
    const link = e.target.closest('a');
    if (link && link.href && !link.target && !link.href.startsWith('javascript:')) {
        if (hasUnsavedChanges()) {
            e.preventDefault();
            showLeaveModal(link.href);
        }
    }
});

function showLeaveModal(url) {
    pendingLeaveUrl = url;
    document.getElementById('leaveModal').classList.add('active');
}

function closeLeaveModal() {
    document.getElementById('leaveModal').classList.remove('active');
    pendingLeaveUrl = null;
}

function confirmLeave() {
    window.isLeavingIntentionally = true;
    window.location.href = pendingLeaveUrl;
}

window.addEventListener('beforeunload', function (e) {
    if (window.isLeavingIntentionally) return;
    if (hasUnsavedChanges()) {
        e.preventDefault();
        e.returnValue = '';
    }
});

function formAyFilter()  { return document.getElementById('sel_ay').value  || ''; }
function formSemFilter() { return document.getElementById('sel_sem').value || ''; }

function toggleDSSMenu(type, event) {
    event.stopPropagation();
    const menu = document.getElementById(`${type}_menu`);
    const opening = menu.style.display !== 'block';
    ['fac', 'room'].forEach(t => document.getElementById(`${t}_menu`).style.display = 'none');
    if (opening) menu.style.display = 'block';
}

async function selectDSSOption(type, value, displayText) {
    const hiddenId = type === 'fac' ? 'sel_faculty' : 'sel_room';
    const nameId   = type === 'fac' ? 'fac_display_name' : 'room_display_name';

    document.getElementById(hiddenId).value   = value;
    document.getElementById(nameId).value     = displayText;
    document.getElementById(`${type}_trigger_text`).textContent = displayText;
    document.getElementById(`${type}_menu`).style.display = 'none';

    if (type === 'room') selectRoom(value, displayText);
    if (type === 'fac')  await onFacultySelect(value);
    updateSummary();
}

function buildDSSMenu(type, sections) {
    const menu = document.getElementById(`${type}_menu`);
    menu.innerHTML = '';
    sections.forEach(sec => {
        if (!sec.items.length) return;
        const hdr = document.createElement('div');
        hdr.className = `dss-section-header ${sec.cls}`;
        hdr.textContent = sec.label;
        menu.appendChild(hdr);
        sec.items.forEach(item => {
            const div = document.createElement('div');
            div.className = 'dss-option' + (sec.cls === 'recommended' ? ' recommended-item' : '');
            div.textContent = item.text;
            div.onclick = () => selectDSSOption(type, item.value, item.text);
            menu.appendChild(div);
        });
    });
}

function initDSSMenus() {
    buildDSSMenu('fac', [{
        cls: 'others', label: 'ALL FACULTY',
        items: allFaculty.map(f => ({ value: f.id, text: f.name }))
    }]);
    buildDSSMenu('room', [{
        cls: 'others', label: 'ALL ROOMS',
        items: allRooms.map(r => ({ value: String(r.id), text: r.name }))
    }]);
}

window.addEventListener('DOMContentLoaded', () => {
    try { initDSSMenus(); } catch(e) { console.error('Menu Init Error:', e); }
    try { renderRooms(); } catch(e) { console.error('Room Render Error:', e); }
    updateTimeDropdowns();
    updateSundayOption();
});

function toggleProgMenu(event) {
    event.stopPropagation();
    const menu = document.getElementById('prog_menu');
    menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
}

function selectProg(code) {
    document.getElementById('sel_prog').value = code;
    document.getElementById('prog_trigger_text').innerText = code;
    document.getElementById('prog_menu').style.display = 'none';
    triggerCascade();
}

document.addEventListener('click', function(e) {
    ['prog_wrapper', 'fac_wrapper', 'room_wrapper'].forEach(wrapperId => {
        const wrapper = document.getElementById(wrapperId);
        if (!wrapper || wrapper.contains(e.target)) return;
        const menuId = wrapperId.replace('_wrapper', '_menu');
        const menu = document.getElementById(menuId);
        if (menu) menu.style.display = 'none';
    });
});

async function triggerDSSLogic() {
    const subjSel = document.getElementById('sel_subj');
    const labWarn = document.getElementById('room_dss_warning');
    updateSummary();

    _subjInfo = null;
    _hasExistingSchedule = false;
    _splitMode = null;
    if (subjSel.selectedIndex <= 0) {
        _skipExistingCheck = false;
        labWarn.style.display = 'none';
        initDSSMenus();
        await updateTimeDropdowns();
        updateSundayOption();
        updateHoursProgressNote();
        return;
    }

    const subjCode = subjSel.value;

    try {
        const siResp = await fetch(`/api/manual/subject_info?subject_code=${encodeURIComponent(subjCode)}`).then(r => r.json());
        if (siResp.success) _subjInfo = siResp;
    } catch(e) { _subjInfo = null; }

    if (_subjInfo && _subjInfo.total_hours >= 5 && !window.currentEditSession) {
        await showSplitChoiceModal(_subjInfo.total_hours);
    }

    await fetchExistingDays();
    await updateTimeDropdowns();
    updateSundayOption();
    updateHoursProgressNote();

    if (!_skipExistingCheck) {
        const ay   = document.getElementById('sel_ay').value;
        const sem  = document.getElementById('sel_sem').value;
        const prog = document.getElementById('sel_prog').value;
        const yl   = document.getElementById('sel_year').value;
        if (ay && sem && prog && yl) {
            try {
                const url = `/api/manual/existing_sessions?subject_code=${encodeURIComponent(subjCode)}&ay_id=${encodeURIComponent(ay)}&semester=${encodeURIComponent(sem)}&program=${encodeURIComponent(prog)}&year_level=${encodeURIComponent(yl)}`;
                const er = await fetch(url).then(r => r.json());
                if (er.success && er.sessions && er.sessions.length > 0) {
                    _hasExistingSchedule = true;
                    showExistingSchedModal(er.sessions);
                    return;
                }
            } catch(e) { /* silent — fall through to normal DSS */ }
        }
    }
    _skipExistingCheck = false;

    try {
        const data = await fetch(`/api/dss/suggest?subject_code=${encodeURIComponent(subjCode)}`).then(r => r.json());
        if (!data.success) { initDSSMenus(); return; }

        buildDSSMenu('fac', [
            { cls: 'recommended', label: 'RECOMMENDATIONS', items: data.faculty.recommended.map(f => ({ value: f.id, text: f.name })) },
            { cls: 'others',      label: 'OTHERS',          items: data.faculty.others.map(f => ({ value: f.id, text: f.name })) }
        ]);

        const isPreferredType = (r) => data.is_lab ? r.type === 'Laboratory' : r.type !== 'Laboratory';
        const recommendedRooms = [
            ...data.rooms.recommended.filter(isPreferredType),
            ...data.rooms.others.filter(isPreferredType)
        ];
        const otherRooms = [
            ...data.rooms.recommended.filter(r => !isPreferredType(r)),
            ...data.rooms.others.filter(r => !isPreferredType(r))
        ];

        buildDSSMenu('room', [
            { cls: 'recommended', label: 'RECOMMENDATIONS', items: recommendedRooms.map(r => ({ value: String(r.id), text: r.name })) },
            { cls: 'others',      label: 'OTHERS',          items: otherRooms.map(r => ({ value: String(r.id), text: r.name })) }
        ]);

        labWarn.style.display = data.is_lab ? 'block' : 'none';

    } catch (e) {
        console.error('DSS suggest error:', e);
        initDSSMenus();
    }
}

window.handlePillClick = async function(sessJson) {
    const sess = JSON.parse(decodeURIComponent(sessJson));
    window.currentEditSession = sess;

    let progStr = (sess.programcode || sess.course || sess.program || '').toString().trim();
    let ylStr   = (sess.year_level  || sess.yearlevel || sess.yearLevel || '').toString().trim();

    if (!progStr) progStr = document.getElementById('sel_prog').value;
    if (!ylStr)   ylStr   = document.getElementById('sel_year').value;

    if (progStr) {
        document.getElementById('sel_prog').value = progStr;
        document.getElementById('prog_trigger_text').innerText = progStr;
    }
    if (ylStr) document.getElementById('sel_year').value = ylStr;

    const progTrigger = document.getElementById('prog_trigger');
    progTrigger.style.pointerEvents = 'none';
    progTrigger.style.opacity = '0.7';
    document.getElementById('sel_year').disabled = true;

    if (progStr) await triggerCascade(true);

    const subjSel = document.getElementById('sel_subj');
    let optExists = Array.from(subjSel.options).some(opt => opt.value === sess.subjectcode);
    if (!optExists) {
        const newOpt = document.createElement('option');
        newOpt.value = sess.subjectcode;
        newOpt.textContent = sess.subjectname || sess.subjectcode;
        subjSel.appendChild(newOpt);
    }
    subjSel.value = sess.subjectcode;
    subjSel.disabled = true;

    await triggerDSSLogic();

    const roomNameFromSess = sess.roomname || sess.room || '';
    const room = allRooms.find(r => r.name === roomNameFromSess);
    if (room) {
        document.getElementById('sel_room').value = String(room.id);
        document.getElementById('room_display_name').value = room.name;
        document.getElementById('room_trigger_text').textContent = room.name;
        document.getElementById('sum_room').innerText = room.name;
    } else if (roomNameFromSess) {
        document.getElementById('room_trigger_text').textContent = roomNameFromSess;
        document.getElementById('room_display_name').value = roomNameFromSess;
        document.getElementById('sum_room').innerText = roomNameFromSess;
    }

    const facStr = sess.instructor ? sess.instructor.split(',')[0].trim() : '';
    const fac = allFaculty.find(f => f.name.includes(facStr));
    if (fac) {
        document.getElementById('sel_faculty').value = fac.id;
        document.getElementById('fac_display_name').value = fac.name;
        document.getElementById('fac_trigger_text').textContent = fac.name;
        await onFacultySelect(fac.id);
    } else {
        document.getElementById('sel_faculty').value = '';
        document.getElementById('fac_display_name').value = '';
        document.getElementById('fac_trigger_text').textContent = '-Select Faculty-';
        _facInfo = null;
    }

    document.getElementById('sel_day').value = sess.daydesc || sess.day;
    await onDayChange();

    const st = timeSlots[sess.starttimeid - 1];
    const et = timeSlots[sess.endtimeid - 1];
    if (st && et) updateTimeBlockFromTimes(st, et);

    updateSummary();
};

function unlockFormFields() {
    window.currentEditSession = null;
    document.getElementById('sel_subj').disabled = false;
    document.getElementById('sel_year').disabled = false;
    const progTrigger = document.getElementById('prog_trigger');
    progTrigger.style.pointerEvents = 'auto';
    progTrigger.style.opacity = '1';
}

async function confirmAndPlace() {
    if (_hasExistingSchedule && !window.currentEditSession) {
        const remHrs = getRemainingHours();
        if (_splitMode === true && remHrs !== null && remHrs > 0.1) {
            // split mode with remaining hours — allow
        } else {
            await showValidationModal(
                'Edit Required',
                remHrs !== null && remHrs <= 0.1
                    ? 'This subject already has its full schedule. Use "Edit" from the existing sessions list to make changes.'
                    : 'This subject already has a schedule. Use "Edit" from the existing sessions list, or choose "Split into Multiple Sessions" to add more.'
            );
            return;
        }
    }

    const ay   = document.getElementById('sel_ay').value;
    const sem  = document.getElementById('sel_sem').value;
    const prog = document.getElementById('sel_prog').value;
    const yl   = document.getElementById('sel_year').value;

    if (!ay || !sem || !prog || !yl) {
        await showValidationModal('Incomplete Form', 'Please set the Academic Year, Semester, Program, and Year Level before confirming.');
        return;
    }

    const subjSel  = document.getElementById('sel_subj');
    const facVal   = document.getElementById('sel_faculty').value;
    const facName  = document.getElementById('fac_display_name').value;
    const roomVal  = document.getElementById('sel_room').value;
    const roomName = document.getElementById('room_display_name').value;
    const dayVal   = document.getElementById('sel_day').value;
    const startVal = document.getElementById('sel_start_time').value;
    const endVal   = document.getElementById('sel_end_time').value;

    if (!subjSel.value || !facVal || !roomVal || !dayVal || !startVal || !endVal) {
        await showValidationModal('Incomplete Form', 'Please complete all selections — Subject, Faculty, Room, Day, and Time Block are all required.');
        return;
    }

    const newStartIdx = getTimeSlotIndex(startVal);
    const newEndIdx   = getTimeSlotIndex(endVal);

    if (newEndIdx <= newStartIdx) {
        await showValidationModal('Invalid Time', 'End time must be after start time. Please adjust the time selection.');
        return;
    }

    if (dayVal === 'Sunday' && _subjInfo && !_subjInfo.is_sunday_allowed) {
        await showValidationModal('Sunday Restriction',
            `Only NSTP and OU subjects may be scheduled on Sunday. "${subjSel.value}" is not allowed on Sunday.`);
        return;
    }

    if (_subjInfo && Math.abs(_subjInfo.total_hours - 1.5) < 0.1 && _existingDays.length > 0) {
        const firstDay  = _existingDays[0];
        const pairedDay = DAY_PAIR_MAP[firstDay];
        if (pairedDay && dayVal !== firstDay && dayVal !== pairedDay) {
            await showValidationModal('Day Pairing Violation',
                `This subject already has a "${firstDay}" session. The second session must be on "${pairedDay}", not "${dayVal}".`);
            return;
        }
    }

    if (_facInfo && dayVal) {
        const isWkd    = MAN_WEEKDAYS.has(dayVal);
        const startIdx = getTimeSlotIndex(startVal) - 1;
        const endIdx   = getTimeSlotIndex(endVal) - 1;
        const tn       = _facInfo.typename;
        let violation  = null;
        if (tn === 'Part-Time' && isWkd && startIdx < REG_END_IDX) {
            violation = 'Part-Time faculty may only teach from 4:30 PM onwards on weekdays.';
        } else if (tn === 'Designee' && isWkd) {
            if (startIdx < DES_START_IDX) {
                violation = 'Designee faculty weekday sessions must start at 8:00 AM or later.';
            } else if (_facInfo.night_service > 0 && endIdx > DES_NIGHT_END_IDX) {
                violation = 'Designee faculty with night teaching service may only teach until 6:00 PM on weekdays.';
            } else if (_facInfo.night_service <= 0 && endIdx > DES_END_IDX) {
                violation = 'Designee faculty without night teaching service may only teach until 5:00 PM on weekdays.';
            }
        }
        if (violation) { await showValidationModal('Faculty Schedule Restriction', violation); return; }
    }

    try {
        const resp = await fetch(`/api/get_room_schedule/${roomVal}?ay_id=${encodeURIComponent(ay)}&semester=${encodeURIComponent(sem)}`);
        const dbSessions = await resp.json();

        for (const s of dbSessions) {
            if (s.daydesc !== dayVal) continue;
            if (window.currentEditSession &&
                s.subjectcode === window.currentEditSession.subjectcode &&
                s.daydesc    === window.currentEditSession.daydesc &&
                s.starttimeid === window.currentEditSession.starttimeid) continue;

            if (newStartIdx < s.endtimeid && newEndIdx > s.starttimeid) {
                await showConflictModal(
                    `"${s.subjectname}" (${s.instructor || 'TBA'}) is already scheduled in ${roomName} on ${dayVal} — ` +
                    `${timeSlots[s.starttimeid - 1]} to ${timeSlots[s.endtimeid - 1]}.`
                );
                return;
            }
        }
    } catch(e) { console.error('Conflict check failed:', e); }

    for (const c of pendingManualSchedule) {
        if (window.currentEditSession && c.temp_id === window.currentEditSession.temp_id) continue;
        if (String(c.room_id) !== String(roomVal)) continue;
        if (c.day !== dayVal) continue;
        const cStart = getTimeSlotIndex(c.start_time);
        const cEnd   = getTimeSlotIndex(c.end_time);
        if (newStartIdx < cEnd && newEndIdx > cStart) {
            await showConflictModal(`"${c.subject_name}" is already placed in ${roomName} on ${dayVal} — ${c.start_time} to ${c.end_time}.`);
            return;
        }
    }

    if (facVal) {
        try {
            const fResp = await fetch(`/api/manual/faculty_schedule?emp_num=${encodeURIComponent(facVal)}&ay_id=${encodeURIComponent(ay)}&semester=${encodeURIComponent(sem)}`);
            const fSessions = await fResp.json();
            for (const s of fSessions) {
                if (s.daydesc !== dayVal) continue;
                if (window.currentEditSession &&
                    s.subjectcode === window.currentEditSession.subjectcode &&
                    s.daydesc     === window.currentEditSession.daydesc &&
                    s.starttimeid === window.currentEditSession.starttimeid) continue;
                if (newStartIdx < s.endtimeid && newEndIdx > s.starttimeid) {
                    await showConflictModal(
                        `This faculty already has "${s.subjectname}" on ${dayVal} — ` +
                        `${timeSlots[s.starttimeid - 1]} to ${timeSlots[s.endtimeid - 1]}. ` +
                        `Faculty cannot have overlapping schedules.`
                    );
                    return;
                }
            }
        } catch(e) { console.error('Faculty conflict check failed:', e); }
    }

    for (const c of pendingManualSchedule) {
        if (window.currentEditSession && c.temp_id === window.currentEditSession.temp_id) continue;
        if (String(c.faculty_id) !== String(facVal)) continue;
        if (c.day !== dayVal) continue;
        const cStart = getTimeSlotIndex(c.start_time);
        const cEnd   = getTimeSlotIndex(c.end_time);
        if (newStartIdx < cEnd && newEndIdx > cStart) {
            await showConflictModal(
                `This faculty is already placed in "${c.subject_name}" on ${dayVal} — ${c.start_time} to ${c.end_time}. ` +
                `Faculty cannot have overlapping schedules.`
            );
            return;
        }
    }

    if (window.currentEditSession) {
        const oldRoom   = window.currentEditSession.roomname || window.currentEditSession.room;
        const oldFacStr = (window.currentEditSession.instructor || '').split(',')[0].trim();
        const oldDay    = window.currentEditSession.daydesc || window.currentEditSession.day;
        const oldStart  = timeSlots[window.currentEditSession.starttimeid - 1];
        const oldEnd    = timeSlots[window.currentEditSession.endtimeid - 1];

        const isRoomChanged = roomName !== oldRoom;
        const isFacChanged  = oldFacStr && !facName.includes(oldFacStr);
        const isTimeChanged = dayVal !== oldDay || startVal !== oldStart || endVal !== oldEnd;

        if (!isRoomChanged && !isFacChanged && !isTimeChanged) {
            await showNochangeModal();
            return;
        }

        if (!confirm("Are you sure you want to apply these changes?")) return;

        if (window.currentEditSession.isLocal) {
            pendingManualSchedule = pendingManualSchedule.filter(c => c.temp_id !== window.currentEditSession.temp_id);
        } else {
            const dbKey = `${window.currentEditSession.subjectcode}_${window.currentEditSession.daydesc}_${window.currentEditSession.starttimeid}`;
            hiddenDbSchedules.add(dbKey);
        }
    }

    const newClass = {
        temp_id:      "DRAFT_" + Date.now(),
        ay, sem,
        subject_code: subjSel.value,
        subject_name: subjSel.options[subjSel.selectedIndex].text,
        faculty_id:   facVal,
        instructor:   facName,
        room_id:      roomVal,
        room:         roomName,
        day:          dayVal,
        days:         dayVal.substring(0, 3).toUpperCase(),
        days_list:    [dayVal],
        start_time:   startVal,
        end_time:     endVal,
        time:         `${startVal} - ${endVal}`,
        course:       prog,
        year_level:   yl,
        units:        _subjInfo ? _subjInfo.total_hours : 3
    };

    pendingManualSchedule.push(newClass);
    unlockFormFields();
    updateHoursProgressNote();
    renderGrid(newClass.room_id, formAyFilter(), formSemFilter());
}

window.dropLocalClass = function(tempId, event) {
    event.stopPropagation();
    pendingManualSchedule = pendingManualSchedule.filter(c => c.temp_id !== tempId);
    if (window.currentEditSession && window.currentEditSession.temp_id === tempId) unlockFormFields();
    const currentRoom = document.getElementById('sel_room').value;
    renderGrid(currentRoom, formAyFilter(), formSemFilter());
};

function resetFormState() {
    document.getElementById('sel_subj').value = '';
    document.getElementById('sel_faculty').value = '';
    document.getElementById('fac_display_name').value = '';
    document.getElementById('fac_trigger_text').textContent = '-Select Faculty-';
    _hasExistingSchedule = false;
    _pendingExistingSessions = [];
    _subjInfo = null;
    _splitMode = null;
    updateSundayOption();
    updateHoursProgressNote();
    unlockFormFields();
    hiddenDbSchedules.clear();
    updateSummary();
}

document.getElementById('btnManualSaveDraft').addEventListener('click', async () => {
    const ay = formAyFilter();
    const sem = formSemFilter();
    const contextDrafts = pendingManualSchedule.filter(c => c.ay === ay && c.sem === sem);

    if (contextDrafts.length === 0) {
        await showValidationModal('Nothing to Save', 'There are no unsaved classes for the currently selected Academic Year and Semester.');
        return;
    }

    const context = {
        program:   document.getElementById('sel_prog').value,
        yearLevel: parseInt(document.getElementById('sel_year').value),
        term:      sem,
        acadYear:  ay
    };

    const btn = document.getElementById('btnManualSaveDraft');
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/schedule/save-draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ schedule_data: contextDrafts, context })
        });
        const data = await res.json();

        if (data.success) {
            window.isLeavingIntentionally = true;
            alert(`Success! Saved as Draft.`);
            pendingManualSchedule = pendingManualSchedule.filter(c => !(c.ay === ay && c.sem === sem));
            resetFormState();
            renderGrid(document.getElementById('sel_room').value, ay, sem);
            setTimeout(() => window.isLeavingIntentionally = false, 100);
        } else {
            let errorMsg = `Failed: ${data.error || 'Unknown Error'}\n`;
            if (data.violations && data.violations.length > 0) {
                errorMsg += "\nViolations detected:\n";
                data.violations.forEach(v => errorMsg += `- ${v.detail}\n`);
            }
            alert(errorMsg);
        }
    } catch (e) {
        alert("Connection error while saving draft.");
    } finally {
        btn.innerHTML = '<i class="fas fa-save"></i> Save as Draft';
        btn.disabled = false;
    }
});

document.getElementById('btnManualApprove').addEventListener('click', async () => {
    const ay = formAyFilter();
    const sem = formSemFilter();
    const contextDrafts = pendingManualSchedule.filter(c => c.ay === ay && c.sem === sem);

    if (contextDrafts.length === 0) {
        await showValidationModal('Nothing to Publish', 'There are no unsaved classes for the currently selected Academic Year and Semester.');
        return;
    }

    const context = {
        program:   document.getElementById('sel_prog').value,
        yearLevel: parseInt(document.getElementById('sel_year').value),
        term:      sem,
        acadYear:  ay
    };

    const btn = document.getElementById('btnManualApprove');
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/schedule/approve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ schedule_data: contextDrafts, context })
        });
        const data = await res.json();

        if (data.success) {
            window.isLeavingIntentionally = true;
            alert(`Success! Schedule Published.`);
            pendingManualSchedule = pendingManualSchedule.filter(c => !(c.ay === ay && c.sem === sem));
            resetFormState();
            renderGrid(document.getElementById('sel_room').value, ay, sem);
            setTimeout(() => window.isLeavingIntentionally = false, 100);
        } else {
            let errorMsg = `Failed to Publish due to Constraints:\n\n`;
            (data.violations || []).forEach(v => errorMsg += `- ${v.detail}\n`);
            alert(errorMsg);
        }
    } catch (e) {
        alert("Connection error while approving schedule.");
    } finally {
        btn.innerHTML = '<i class="fas fa-check-circle"></i> Save & Publish';
        btn.disabled = false;
    }
});

function getTimeSlotIndex(timeStr) {
    const idx = timeSlots.indexOf(timeStr);
    return idx >= 0 ? idx + 1 : 1;
}

async function renderGrid(roomId, ayFilter = '', semFilter = '') {
    const wrapper = document.getElementById('gridWrapper');
    const table = document.getElementById('mainTimetable');
    wrapper.querySelectorAll('.schedule-pill').forEach(p => p.remove());

    if (!ayFilter || !semFilter || !roomId) return;

    try {
        const url = `/api/get_room_schedule/${roomId}?ay_id=${ayFilter}&semester=${semFilter}&_t=${new Date().getTime()}`;
        const resp = await fetch(url);
        let sessions = await resp.json();

        sessions = sessions.filter(s => {
            const dbKey = `${s.subjectcode}_${s.daydesc}_${s.starttimeid}`;
            return !hiddenDbSchedules.has(dbKey);
        });

        const localForRoom = pendingManualSchedule.filter(c =>
            String(c.room_id) === String(roomId) &&
            c.ay === ayFilter &&
            c.sem === semFilter
        ).map(c => ({
            temp_id:     c.temp_id,
            subjectcode: c.subject_code,
            subjectname: c.subject_name,
            instructor:  c.instructor,
            daydesc:     c.day,
            starttimeid: getTimeSlotIndex(c.start_time),
            endtimeid:   getTimeSlotIndex(c.end_time),
            roomname:    c.room,
            course:      c.course,
            year_level:  c.year_level,
            isLocal:     true,
            status:      'Draft'
        }));

        sessions = sessions.concat(localForRoom);
        sessions.sort((a, b) => {
            const statA = a.status || '';
            const statB = b.status || '';
            if (statA === 'Published' && statB !== 'Published') return -1;
            if (statB === 'Published' && statA !== 'Published') return 1;
            return 0;
        });

        const seen = new Set();
        const uniq = sessions.filter(s => {
            const key = `${s.subjectcode}|${s.daydesc}|${s.starttimeid}`;
            if (seen.has(key)) return false;
            seen.add(key); return true;
        });

        const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
        const firstCell = table.querySelector('tbody td:nth-child(2)');
        const timeCol = table.querySelector('.time-col');
        const thead = table.querySelector('thead');

        if (!firstCell || firstCell.offsetWidth === 0) {
            requestAnimationFrame(() => renderGrid(roomId, ayFilter, semFilter));
            return;
        }

        const colWidth   = firstCell.offsetWidth;
        const rowHeight  = firstCell.offsetHeight;
        const leftOffset = timeCol.offsetWidth;
        const topOffset  = thead.offsetHeight;

        const dayGroups = {};
        uniq.forEach(s => { (dayGroups[s.daydesc] = dayGroups[s.daydesc] || []).push(s); });

        Object.keys(dayGroups).forEach(dayName => {
            const dayIdx = days.indexOf(dayName);
            if (dayIdx < 0) return;
            const daySessions = dayGroups[dayName];
            daySessions.sort((a, b) => a.starttimeid - b.starttimeid);

            daySessions.forEach((sess, idx) => {
                const start = sess.starttimeid, end = sess.endtimeid;
                let overlapCount = 0, overlapIndex = 0;
                daySessions.forEach((other, oIdx) => {
                    if (start < other.endtimeid && end > other.starttimeid) {
                        overlapCount++;
                        if (idx > oIdx) overlapIndex++;
                    }
                });

                const pill = document.createElement('div');
                pill.className = 'schedule-pill';

                const isDraft = sess.isLocal === true || (sess.status && String(sess.status).toLowerCase() === 'draft');
                pill.style.backgroundColor = isDraft ? '#8e9ca0' : getSubjectColor(sess.subjectcode);
                if (isDraft) pill.style.border = "2px dashed #2c3e50";

                if (window.currentEditSession) {
                    const editKey = `${window.currentEditSession.subjectcode}_${window.currentEditSession.daydesc}_${window.currentEditSession.starttimeid}`;
                    const sessKey = `${sess.subjectcode}_${sess.daydesc}_${sess.starttimeid}`;
                    if (editKey === sessKey) pill.classList.add('pill-editing');
                }

                const w = (colWidth - 6) / (overlapCount || 1);
                const pillH = (end - start) * rowHeight - 6;
                pill.style.width  = (w - 2) + 'px';
                pill.style.height = pillH + 'px';
                pill.style.left   = (leftOffset + (dayIdx * colWidth) + (overlapIndex * w) + 3) + 'px';
                pill.style.top    = (topOffset + ((start - 1) * rowHeight) + 3) + 'px';
                pill.style.cursor = 'pointer';

                const instrLast = (sess.instructor || 'TBA').split(',')[0].trim();
                pill.title = `${sess.subjectcode}\n${sess.subjectname || ''}\n${sess.instructor || ''}`;

                let dropBtn = '';
                if (sess.isLocal) {
                    dropBtn = `<button class="pill-drop-btn" onclick="dropLocalClass('${sess.temp_id}', event)" title="Remove"><i class="fas fa-times"></i></button>`;
                }

                pill.innerHTML = `
                    ${dropBtn}
                    <div class="pill-subject" style="margin-top: ${sess.isLocal ? '8px' : '0'};">${sess.subjectcode}</div>
                    <div style="font-size:0.6rem;">${instrLast}</div>`;

                pill.onclick = (e) => {
                    if (e.target.closest('.pill-drop-btn')) return;
                    window.handlePillClick(encodeURIComponent(JSON.stringify(sess)));
                };

                wrapper.appendChild(pill);
            });
        });
    } catch (err) { console.error(err); }
}

function filterByBuilding(bldgId, btnElement) {
    currentBldgId = String(bldgId);
    document.querySelectorAll('.bldg-tab').forEach(btn => btn.classList.remove('active'));
    btnElement.classList.add('active');
    renderRooms();
}

function renderRooms() {
    const selectedFloor = document.getElementById('sel_floor').value;
    const container = document.getElementById('room_list_container');
    container.innerHTML = '';

    allRooms.forEach(room => {
        const bldgMatch  = (currentBldgId === 'ALL' || String(room.bldg_id) === String(currentBldgId));
        const floorMatch = (selectedFloor === 'ALL' || String(room.floor) === String(selectedFloor));

        if (bldgMatch && floorMatch) {
            container.innerHTML += `<div class="room-pill" onclick="selectRoom('${room.id}', '${room.name}')">${room.name}</div>`;
        }
    });

    const currentSelectedRoomName = document.getElementById('room_display_name').value;
    if (currentSelectedRoomName) {
        document.querySelectorAll('.room-pill').forEach(el => {
            if (el.textContent === currentSelectedRoomName) el.classList.add('active-room');
        });
    }
}

const _SEM_LABELS = { A: '1ST SEMESTER', B: '2ND SEMESTER', C: 'SUMMER' };

async function updateSemesterDropdown(ay, prog) {
    const semSel  = document.getElementById('sel_sem');
    const prevVal = semSel.value;

    semSel.innerHTML = '<option value="" disabled>-Select-</option>';

    if (!ay) {
        ['A','B','C'].forEach(code => semSel.innerHTML += `<option value="${code}">${_SEM_LABELS[code]}</option>`);
        if (prevVal) semSel.value = prevVal;
        return;
    }

    try {
        const url = `/api/get_existing_schedule_periods?ay_id=${encodeURIComponent(ay)}` +
                    (prog ? `&program=${encodeURIComponent(prog)}` : '');
        const data = await fetch(url).then(r => r.json());

        if (!data.success || !data.valid_sems || data.valid_sems.length === 0) {
            ['A','B','C'].forEach(code => semSel.innerHTML += `<option value="${code}">${_SEM_LABELS[code]}</option>`);
            if (prevVal) semSel.value = prevVal;
            return;
        }

        const { valid_sems } = data;
        ['A','B','C'].forEach(code => {
            if (valid_sems.includes(code)) {
                const opt = document.createElement('option');
                opt.value = code;
                opt.textContent = _SEM_LABELS[code];
                if (code === prevVal) opt.selected = true;
                semSel.appendChild(opt);
            }
        });

        if (prevVal && semSel.value !== prevVal) semSel.value = '';
    } catch (e) {
        console.error('updateSemesterDropdown error:', e);
        ['A','B','C'].forEach(code => semSel.innerHTML += `<option value="${code}">${_SEM_LABELS[code]}</option>`);
    }
}

async function triggerCascade(skipGridRender = false) {
    const ay   = document.getElementById('sel_ay').value || '';
    const prog = document.getElementById('sel_prog').value || '';
    const year = document.getElementById('sel_year').value || '';

    await updateSemesterDropdown(ay, prog);

    const sem = document.getElementById('sel_sem').value || '';

    const currDisplay = document.getElementById('display_curr');
    const currHidden  = document.getElementById('curr_id_hidden');
    const subjSelect  = document.getElementById('sel_subj');

    currDisplay.innerText = '---';
    currHidden.value = '';

    if (!window.currentEditSession) {
        subjSelect.innerHTML = '<option value="">-- Select Subject --</option>';
    }

    document.getElementById('sum_course').innerText = '-';
    document.getElementById('sum_prog').innerText   = prog ? document.getElementById('prog_trigger_text').innerText : '-';

    if (prog) {
        try {
            const resp = await fetch(`/api/get_curriculum?program=${prog}`);
            const data = await resp.json();

            if (data.success) {
                currDisplay.innerText = data.curriculum_code;
                currHidden.value = data.curriculum_id;

                if (year && sem && !window.currentEditSession) {
                    const sResp = await fetch(`/api/get_subjects?curriculum_id=${data.curriculum_id}&year_level=${year}&semester=${sem}`);
                    const sData = await sResp.json();
                    subjSelect.innerHTML = '<option value="">-- Select Subject --</option>';
                    if (sData.subjects && sData.subjects.length > 0) {
                        sData.subjects.forEach(s => {
                            subjSelect.innerHTML += `<option value="${s.subjectcode}">${s.subjectname}</option>`;
                        });
                    } else {
                        subjSelect.innerHTML = '<option value="">No subjects found</option>';
                    }
                }
            } else {
                currDisplay.innerText = 'No Curriculum';
                if (!window.currentEditSession) subjSelect.innerHTML = '<option value="">-- No Subjects --</option>';
            }
        } catch (e) {
            console.error('Cascade fetch error:', e);
            currDisplay.innerText = 'Error loading';
        }
    }

    if (typeof updateSummary === 'function') updateSummary();

    if (skipGridRender !== true) {
        const currentRoomId = document.getElementById('sel_room').value;
        renderGrid(currentRoomId, ay, sem);
    }
}

async function selectRoom(roomId, roomName) {
    document.getElementById('sel_room').value          = roomId;
    document.getElementById('room_display_name').value = roomName;
    document.getElementById('room_trigger_text').textContent = roomName;
    document.getElementById('sum_room').innerText      = roomName;

    const room = allRooms.find(r => String(r.id) === String(roomId));
    if (room) {
        document.querySelectorAll('.bldg-tab').forEach(btn => btn.classList.remove('active'));
        const bldgTab = document.querySelector(`.bldg-tab[data-bldg="${room.bldg_id}"]`);
        if (bldgTab) bldgTab.classList.add('active');
    }

    document.querySelectorAll('.room-pill').forEach(el => {
        el.classList.remove('active-room');
        if (el.textContent === roomName) el.classList.add('active-room');
    });

    await renderGrid(roomId, formAyFilter(), formSemFilter());
}

const colorPalette = ['#16a085', '#27ae60', '#2980b9', '#8e44ad', '#2c3e50', '#f39c12', '#d35400', '#c0392b'];
function getSubjectColor(code) {
    let hash = 0;
    for (let i = 0; i < code.length; i++) hash = code.charCodeAt(i) + ((hash << 5) - hash);
    return colorPalette[Math.abs(hash) % colorPalette.length];
}

function updateSummary() {
    const progCode   = document.getElementById('sel_prog').value || '';
    const progName   = document.getElementById('prog_trigger_text').innerText || progCode;
    const yearVal    = document.getElementById('sel_year').value || '';
    const ayVal      = document.getElementById('sel_ay').value || '';
    const semVal     = document.getElementById('sel_sem').value || '';
    const semLabels  = { A: '1ST SEMESTER', B: '2ND SEMESTER', C: 'SUMMER' };
    const subjSelect = document.getElementById('sel_subj');

    const yrLabels = {'1':'1st Year','2':'2nd Year','3':'3rd Year','4':'4th Year','5':'5th Year'};
    document.getElementById('sum_prog').innerText   = (progName && yearVal) ? `${progName} - ${yrLabels[yearVal] || 'Yr '+yearVal}` : '-';
    document.getElementById('sum_course').innerText = subjSelect.selectedIndex > 0 ? subjSelect.options[subjSelect.selectedIndex].text : '-';
    document.getElementById('sum_inst').innerText   = document.getElementById('fac_display_name').value || '-';
    document.getElementById('summary_period').innerText = `A.Y ${ayVal || '----'} | ${semLabels[semVal] || '-'}`;
}

document.getElementById('sel_year').addEventListener('change', triggerCascade);
document.getElementById('sel_subj').addEventListener('change', updateSummary);
document.getElementById('sel_faculty').addEventListener('change', updateSummary);

function showValidationModal(title, message) {
    return new Promise(resolve => {
        document.getElementById('validationModalTitle').textContent   = title;
        document.getElementById('validationModalMessage').textContent = message;
        document.getElementById('validationModal').classList.add('active');
        window._validationResolve = resolve;
    });
}
function closeValidationModal() {
    document.getElementById('validationModal').classList.remove('active');
    if (window._validationResolve) { window._validationResolve(); window._validationResolve = null; }
}

function showNochangeModal() {
    return new Promise(resolve => {
        document.getElementById('nochangeModal').classList.add('active');
        window._nochangeResolve = resolve;
    });
}
function closeNochangeModal() {
    document.getElementById('nochangeModal').classList.remove('active');
    if (window._nochangeResolve) { window._nochangeResolve(); window._nochangeResolve = null; }
}

function showConflictModal(detail) {
    return new Promise(resolve => {
        const detailEl = document.getElementById('conflictModalDetail');
        if (detail) {
            detailEl.textContent = detail;
            detailEl.style.display = 'block';
        } else {
            detailEl.style.display = 'none';
        }
        document.getElementById('conflictModal').classList.add('active');
        window._conflictResolve = resolve;
    });
}

function closeConflictModal() {
    document.getElementById('conflictModal').classList.remove('active');
    if (window._conflictResolve) { window._conflictResolve(); window._conflictResolve = null; }
}
