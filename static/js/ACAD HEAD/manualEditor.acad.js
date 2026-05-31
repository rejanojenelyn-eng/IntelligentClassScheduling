const _initData  = document.getElementById('app-init-data');
const allRooms   = JSON.parse(_initData.dataset.rooms);
const allFaculty = JSON.parse(_initData.dataset.faculty);

const timeSlots = ['07:30 AM', '08:00 AM', '08:30 AM', '09:00 AM', '09:30 AM', '10:00 AM', '10:30 AM', '11:00 AM', '11:30 AM', '12:00 PM', '12:30 PM', '01:00 PM', '01:30 PM', '02:00 PM', '02:30 PM', '03:00 PM', '03:30 PM', '04:00 PM', '04:30 PM', '05:00 PM', '05:30 PM', '06:00 PM', '06:30 PM', '07:00 PM', '07:30 PM', '08:00 PM', '08:30 PM', '09:00 PM'];

let pendingManualSchedule = [];
let hiddenDbSchedules = new Set();
let currentBldgId = 'ALL';
window.currentEditSession = null;
let _splitMode = null;
let currentMode = 'room'; // 'room' (Room View tab) | 'program' (Program View tab)

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

// Fallback indices used only when DB time values are absent
const REG_END_IDX        = 18;   // 04:30 PM fallback
const DES_START_IDX      =  1;   // 08:00 AM fallback
const DES_END_IDX        = 19;   // 05:00 PM fallback
const DES_NIGHT_END_IDX  = 21;   // 06:00 PM fallback
const NIGHT_START_IDX    = 21;   // 06:00 PM — threshold for HC7 (no DB override needed)

// Converts a 'HH:MM' DB time string to a timeSlots array index.
// timeSlots starts at 07:30 AM (450 min) in 30-min steps.
// Returns fallback_idx when hhmm is null/undefined or out of range.
function _hhmm_to_slot_idx(hhmm, fallback_idx) {
    if (!hhmm) return (fallback_idx !== undefined) ? fallback_idx : 0;
    const parts = String(hhmm).split(':');
    const h = parseInt(parts[0], 10);
    const m = parseInt(parts[1] || '0', 10);
    const idx = Math.round((h * 60 + m - 450) / 30);
    if (isNaN(idx) || idx < 0 || idx >= timeSlots.length) {
        return (fallback_idx !== undefined) ? fallback_idx : 0;
    }
    return idx;
}

// HC — subject-code prefix → required specialization (null = any faculty allowed)
const _SUBJ_SPEC_MAP = [
    [['COMP', 'INTE', 'ICTE', 'ITEC', 'ELEC IT', 'ELECT IT'], 'Computer and Information Sciences'],
    [['ARCH', 'ARCHS'],                                        'Architecture, Design and the Built Environment'],
    [['CIEN', 'ENSC'],                                         'Engineering'],
    [['ACCO'],                                                  'Accountancy and Finance'],
    [['BUMA', 'HRMA'],                                         'Business Administration'],
    // GEED, NSTP, PATHFIT, PHED, ROTC, MATH → null (unrestricted)
];

function _getSubjectSpecGroup(subjectCode) {
    const upper = (subjectCode || '').toUpperCase();
    for (const [prefixes, specName] of _SUBJ_SPEC_MAP) {
        for (const pfx of prefixes) {
            if (upper.startsWith(pfx)) return specName;
        }
    }
    return null;
}

let _facLoadData = null;             // cached from /api/manual/faculty_load
let _roomInfo    = null;             // cached from allRooms on room select
let _dssRecommendedFacultyIds = new Set(); // faculty IDs in DSS recommended list

function _getMaxEndIdx() {
    const day   = document.getElementById('sel_day').value;
    const isWkd = MAN_WEEKDAYS.has(day);
    const tn    = _facInfo ? _facInfo.typename : null;
    if (tn === 'Designee' && isWkd && day) {
        const hasNS = _facInfo && _facInfo.night_service > 0;
        if (hasNS) return _hhmm_to_slot_idx(_facInfo.parttime_end, DES_NIGHT_END_IDX);
        return Math.max(_hhmm_to_slot_idx(_facInfo.regular_end, REG_END_IDX), REG_END_IDX);
    }
    if (tn === 'Regular' && isWkd && day) {
        return _hhmm_to_slot_idx(_facInfo.regular_end, REG_END_IDX);
    }
    // HC3: Part-Time faculty on weekdays are restricted to their allowed end time
    if (tn === 'Part-Time' && isWkd && day) {
        return _hhmm_to_slot_idx(_facInfo.parttime_end, 27); // default 21:00 = index 27
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
        if (_splitMode === true) {
            // Split mode: cap each session by remaining hours
            const remaining = getRemainingHours();
            if (remaining !== null && remaining > 0) {
                const remMax = startIdx + Math.round(remaining / 0.5);
                if (remMax < maxIdx) maxIdx = remMax;
            }
        } else {
            // Single session: cap to exactly total_hours
            const unitMax = startIdx + Math.round(_subjInfo.total_hours / 0.5);
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

    // HC3: Part-Time faculty on weekdays are restricted to parttime_start–parttime_end
    const isPT        = tn === 'Part-Time' && isWkd && day;
    const minStartIdx = isPT ? _hhmm_to_slot_idx(_facInfo.parttime_start, 19) : 0; // 19 = 04:30 PM
    const maxEndIdx   = _getMaxEndIdx();
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

    if (isPT) {
        note.className = 'constraint-note warn';
        note.textContent = `⚠ Part-time faculty: weekday slots restricted to ${timeSlots[minStartIdx]}–${timeSlots[maxEndIdx]}.`;
    }
    if (!day) {
        note.className = 'constraint-note info';
        note.textContent = 'ℹ Select a day to filter available times.';
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
    // Only count genuinely new/modified entries — do NOT count currentEditSession alone
    // (clicking a pill to view it sets currentEditSession but is not a real change)
    return pendingManualSchedule.some(s => !s.fromExisting) || !!window._pendingFacultyAssignment;
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

// Show a banner when the user switches away from this tab with unsaved changes
let _tabWarnBanner = null;
document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') return; // tab hidden — nothing to show
    // Tab just became visible again
    if (_tabWarnBanner) { _tabWarnBanner.remove(); _tabWarnBanner = null; }
    if (!window.isLeavingIntentionally && hasUnsavedChanges()) {
        _tabWarnBanner = document.createElement('div');
        _tabWarnBanner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#8b1a1a;color:#fff;font-size:0.82rem;font-weight:700;padding:10px 20px;text-align:center;letter-spacing:0.5px;cursor:pointer;';
        _tabWarnBanner.textContent = '⚠ You have unsaved schedule changes. Click here to dismiss.';
        _tabWarnBanner.onclick = () => { _tabWarnBanner.remove(); _tabWarnBanner = null; };
        document.body.appendChild(_tabWarnBanner);
        setTimeout(() => { if (_tabWarnBanner) { _tabWarnBanner.remove(); _tabWarnBanner = null; } }, 8000);
    }
});

function formAyFilter()  { return document.getElementById('sel_ay').value  || ''; }
function formSemFilter() { return document.getElementById('sel_sem').value || ''; }

function toggleDSSMenu(type, event) {
    event.stopPropagation();
    const menu = document.getElementById(`${type}_menu`);
    const opening = menu.style.display !== 'block';
    ['fac', 'room'].forEach(t => document.getElementById(`${t}_menu`).style.display = 'none');
    if (opening) {
        menu.style.display = 'block';
        const searchInput = menu.querySelector('.dss-search-input');
        if (searchInput) setTimeout(() => searchInput.focus(), 40);
    }
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
    if (typeof _updateWorkflowBar === 'function') _updateWorkflowBar();
}

/* Faculty helper utilities */
function _facInitials(name) {
    return (name.split(',')[0] || '').trim().substring(0, 2).toUpperCase() || '??';
}
function _facTypeCls(typename) {
    const t = (typename || '').toLowerCase();
    if (t.includes('part')) return 'fac-pill-pt';
    if (t.includes('design')) return 'fac-pill-des';
    return 'fac-pill-reg';
}
function _facTypeLabel(typename) {
    const t = (typename || '').toLowerCase();
    if (t.includes('part')) return 'Part-Time';
    if (t.includes('design')) return 'Designee';
    return 'Regular';
}

function buildDSSMenu(type, sections) {
    const menu = document.getElementById(`${type}_menu`);
    menu.innerHTML = '';

    // Search box
    const searchBox = document.createElement('div');
    searchBox.className = 'dss-search-box';
    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.className = 'dss-search-input';
    searchInput.placeholder = 'Ex., Dela Cruz, Juan';
    searchInput.addEventListener('input', function() {
        const q = this.value.toLowerCase();
        menu.querySelectorAll('.dss-option').forEach(opt => {
            const nameEl = opt.querySelector('.fac-opt-name');
            const text   = nameEl ? nameEl.textContent : opt.textContent;
            opt.style.display = text.toLowerCase().includes(q) ? '' : 'none';
        });
    });
    searchBox.appendChild(searchInput);
    menu.appendChild(searchBox);

    sections.forEach(sec => {
        if (!sec.items.length) return;
        const hdr = document.createElement('div');
        hdr.className = `dss-section-header ${sec.cls}`;
        hdr.textContent = sec.label;
        menu.appendChild(hdr);
        const isRec = sec.cls === 'recommended';
        sec.items.forEach(item => {
            const div = document.createElement('div');
            div.className = 'dss-option' + (isRec ? ' recommended-item' : '');
            if (type === 'fac') {
                const initials  = _facInitials(item.text);
                const typCls    = _facTypeCls(item.typename);
                const typLabel  = _facTypeLabel(item.typename);
                let unitsHtml   = '';
                if (item.max_units != null) {
                    const assigned  = item.assigned_units || 0;
                    const remaining = item.max_units - assigned;
                    const remCls    = remaining <= 0 ? 'over' : remaining <= 3 ? 'warn' : 'good';
                    unitsHtml = `<span class="fac-opt-units">${assigned}/${item.max_units}u</span>
                                 <span class="fac-opt-remaining ${remCls}">${remaining}u left</span>`;
                }
                div.innerHTML = `
                    <div class="fac-opt-inner">
                        <div class="fac-opt-avatar${isRec ? ' rec' : ''}">${initials}</div>
                        <div class="fac-opt-info">
                            <span class="fac-opt-name">${item.text}</span>
                            <div class="fac-opt-sub">
                                <span class="fac-type-pill ${typCls}">${typLabel}</span>
                                ${unitsHtml}
                            </div>
                        </div>
                    </div>`;
            } else {
                div.textContent = item.text;
            }
            div.onclick = () => selectDSSOption(type, item.value, item.text);
            menu.appendChild(div);
        });
    });
}

function initDSSMenus() {
    buildDSSMenu('fac', [{
        cls: 'others', label: 'ALL FACULTY',
        items: allFaculty.map(f => ({ value: f.id, text: f.name, typename: f.typename }))
    }]);
    buildDSSMenu('room', [{
        cls: 'others', label: 'ALL ROOMS',
        items: allRooms.map(r => ({ value: String(r.id), text: r.name }))
    }]);
}

/* ─────────────────────────────────────────────
   BY SUBJECT / BY PROGRAM MODE
───────────────────────────────────────────── */

function switchMode(mode) {
    if (currentMode === mode) return;
    currentMode = mode;
    const isByProg = mode === 'program';

    // Legacy mode-toggle buttons (still update their CSS for Room View's internal toggle)
    const msBtnS = document.getElementById('btn-mode-subject');
    const msBtnP = document.getElementById('btn-mode-program');
    if (msBtnS) msBtnS.classList.toggle('active', !isByProg);
    if (msBtnP) msBtnP.classList.toggle('active', isByProg);

    // In Room View, show/hide legacy sidebar label
    const sidebar  = document.querySelector('.rooms-sidebar');
    const progLbl  = document.getElementById('prog-view-label');
    if (sidebar)  sidebar.style.display  = isByProg ? 'none' : '';
    if (progLbl)  progLbl.style.display  = isByProg ? 'flex'  : 'none';

    if (isByProg) {
        renderProgramTimetable();
    } else {
        const roomId = document.getElementById('sel_room').value;
        renderGrid(roomId, formAyFilter(), formSemFilter());
    }
}

/* Convert "07:30:00" / "07:30" / "07:30 AM" to 1-based timeSlot index */
function timeStrToSlotIdx(timeStr) {
    if (!timeStr) return 0;
    const s = String(timeStr);
    const parts = s.split(':');
    if (parts.length < 2) return 0;
    let h = parseInt(parts[0]), m = parseInt(parts[1]);
    if (isNaN(h) || isNaN(m)) return 0;
    if (s.toLowerCase().includes('pm') && h !== 12) h += 12;
    if (s.toLowerCase().includes('am') && h === 12) h = 0;
    return Math.round((h * 60 + m - 450) / 30) + 1; // 7:30 AM = slot 1
}

async function renderProgramTimetable() {
    const prog = document.getElementById('sel_prog').value;
    const yl   = document.getElementById('sel_year').value;
    const sem  = document.getElementById('sel_sem').value;
    const ay   = document.getElementById('sel_ay').value;

    const wrapper = document.getElementById('gridWrapper');
    wrapper.querySelectorAll('.schedule-pill').forEach(p => p.remove());

    // Update the standalone Program View header label (shown when in PROGRAM VIEW tab)
    const pvLabelEl = document.getElementById('pvTimetableLabel');
    // Legacy in-grid label (for Room View program mode, if still used)
    const labelEl   = document.getElementById('prog-view-label-text');

    if (!prog || !yl || !sem || !ay) {
        const noSelMsg = '— Select Program, Year Level &amp; Semester —';
        if (pvLabelEl) pvLabelEl.innerHTML = noSelMsg;
        if (labelEl)   labelEl.textContent  = 'SELECT PROGRAM, YEAR LEVEL, AND SEMESTER TO VIEW';
        return;
    }

    const semLabels = { A: '1ST SEMESTER', B: '2ND SEMESTER', C: 'SUMMER' };
    const yrLabels  = { '1':'1ST YEAR','2':'2ND YEAR','3':'3RD YEAR','4':'4TH YEAR','5':'5TH YEAR' };
    const progName  = document.getElementById('prog_trigger_text').innerText || prog;
    const headerTxt = `${progName.toUpperCase()} &mdash; ${yrLabels[yl] || yl} &nbsp;|&nbsp; A.Y ${ay} &nbsp;|&nbsp; ${semLabels[sem] || sem}`;
    if (pvLabelEl) pvLabelEl.innerHTML  = headerTxt;
    if (labelEl)   labelEl.textContent  = `${progName.toUpperCase()}  —  ${yrLabels[yl] || yl}  |  A.Y ${ay}  |  ${semLabels[sem] || sem}`;

    try {
        const url = `/api/get_offerings_schedule?program=${encodeURIComponent(prog)}&year_level=${yl}&semester=${sem}&ay=${encodeURIComponent(ay)}&status=active&_t=${Date.now()}`;
        const resp = await fetch(url, { cache: 'no-store' });
        if (!resp.ok) return;
        const raw = await resp.json();

        // Program View is a READ-ONLY section schedule visualizer.
        // It shows only what is actually saved in the DB (Published + active Draft).
        // Unsaved/pending entries from pendingManualSchedule are intentionally excluded:
        // they are incomplete, may belong to a different subject's edit session, and cause
        // phantom pills for sessions that were never saved. Users see their unsaved work
        // in Room View while they are actively editing.
        const sessByKey = new Map();
        (raw || []).forEach(s => {
            const key = `${s.subjectcode}|${s.daydesc}|${s.start_time}`;
            const existing = sessByKey.get(key);
            // Prefer Published over Draft for the same slot; otherwise keep first seen
            if (!existing || s.status === 'Published') sessByKey.set(key, s);
        });

        _renderProgPills(Array.from(sessByKey.values()), prog, yl);
    } catch (e) { console.error('[renderProgramTimetable]', e); }
}

// Render-token guard: each call to _renderProgPills increments this counter.
// A deferred RAF callback checks if its token still matches — if not, a newer
// call already ran and the stale RAF is cancelled. Prevents duplicate pills
// from stacked RAF calls when switching tabs rapidly.
let _progPillsToken = 0;

function _renderProgPills(sessions, prog, yl) {
    const token = ++_progPillsToken;

    const wrapper = document.getElementById('gridWrapper');
    const table   = document.getElementById('mainTimetable');
    wrapper.querySelectorAll('.schedule-pill').forEach(p => p.remove());

    const days = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
    const firstCell = table.querySelector('tbody td:nth-child(2)');
    const timeCol   = table.querySelector('.time-col');
    const thead     = table.querySelector('thead');

    if (!firstCell || firstCell.offsetWidth === 0) {
        // Defer until the grid is laid out; abort if a newer render has been requested
        requestAnimationFrame(() => {
            if (token !== _progPillsToken) return;
            _renderProgPills(sessions, prog, yl);
        });
        return;
    }

    const colWidth  = firstCell.offsetWidth;
    const rowHeight = firstCell.offsetHeight;
    const leftOff   = timeCol.offsetWidth;
    const topOff    = thead.offsetHeight;

    const dayGroups = {};
    sessions.forEach(s => { if (s.daydesc) (dayGroups[s.daydesc] = dayGroups[s.daydesc] || []).push(s); });

    Object.keys(dayGroups).forEach(dayName => {
        const dayIdx = days.indexOf(dayName);
        if (dayIdx < 0) return;
        const daySessions = dayGroups[dayName];

        daySessions.forEach((sess, idx) => {
            const startIdx = sess.starttimeid || timeStrToSlotIdx(sess.start_time);
            const endIdx   = sess.endtimeid   || timeStrToSlotIdx(sess.end_time);
            if (!startIdx || !endIdx || endIdx <= startIdx) return;

            let overlapCount = 0, overlapIndex = 0;
            daySessions.forEach((other, oIdx) => {
                const oS = other.starttimeid || timeStrToSlotIdx(other.start_time);
                const oE = other.endtimeid   || timeStrToSlotIdx(other.end_time);
                if (startIdx < oE && endIdx > oS) { overlapCount++; if (idx > oIdx) overlapIndex++; }
            });

            const pill = document.createElement('div');
            pill.className = 'schedule-pill';

            const isDraft = !sess.status || sess.status.toLowerCase() !== 'published';
            pill.style.backgroundColor = isDraft ? (sess.isPreview ? '#c8d6da' : '#8e9ca0') : getSubjectColor(sess.subjectcode);
            if (isDraft) pill.style.border = '2px dashed #2c3e50';

            if (window.currentEditSession) {
                const editKey = `${window.currentEditSession.subjectcode}_${window.currentEditSession.daydesc}_${window.currentEditSession.starttimeid}`;
                const sessKey = `${sess.subjectcode}_${sess.daydesc}_${startIdx}`;
                if (editKey === sessKey) pill.classList.add('pill-editing');
            }

            const w = (colWidth - 6) / (overlapCount || 1);
            const pillH = (endIdx - startIdx) * rowHeight - 6;
            pill.style.width  = (w - 2) + 'px';
            pill.style.height = pillH + 'px';
            pill.style.left   = (leftOff + dayIdx * colWidth + overlapIndex * w + 3) + 'px';
            pill.style.top    = (topOff  + (startIdx - 1) * rowHeight + 3) + 'px';
            pill.style.cursor = 'pointer';
            pill.dataset.pillKey = `${sess.subjectcode}_${sess.daydesc}_${startIdx}`;

            const instrLast = (sess.instructor || 'TBA').split(',')[0].trim();
            const roomDisp  = sess.roomname || 'TBA';
            const subjName  = sess.subjectname || sess.subjectcode;
            pill.title = `${sess.subjectcode} — ${subjName}\n${sess.instructor || 'TBA'}\n${roomDisp}`;

            // Pill height thresholds for progressive info density
            const compact   = pillH < 42;   // code only
            const medium    = pillH < 70;   // code + room
            const _pDbKey = `${sess.subjectcode}_${sess.daydesc}_${startIdx}`;
            const _pLabel = `${sess.subjectcode} — ${sess.daydesc} | ${roomDisp}`;
            const _pSd    = encodeURIComponent(JSON.stringify({ temp_id: sess.temp_id || null, versionid: sess.versionid || null, dbKey: _pDbKey, label: _pLabel, subjectcode: sess.subjectcode || null }));
            const dropBtn = `<button class="pill-drop-btn" onclick="_dropSession('${_pSd}', event)" title="Remove"><i class="fas fa-times"></i></button>`;
            pill.innerHTML = compact
                ? `${dropBtn}<div class="pill-subject" style="margin-top:6px;font-size:0.65rem;">${sess.subjectcode}</div>`
                : medium
                    ? `${dropBtn}
                       <div class="pill-subject" style="margin-top:6px;">${sess.subjectcode}</div>
                       <div style="font-size:0.55rem;opacity:0.85;margin-top:2px;"><i class="fas fa-door-open" style="margin-right:2px;"></i>${roomDisp}</div>`
                    : `${dropBtn}
                       <div class="pill-subject" style="margin-top:6px;">${sess.subjectcode}</div>
                       <div style="font-size:0.6rem;margin-top:1px;opacity:0.9;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${instrLast}</div>
                       <div style="font-size:0.55rem;opacity:0.8;margin-top:2px;"><i class="fas fa-door-open" style="margin-right:2px;"></i>${roomDisp}</div>`;

            pill.onclick = (e) => {
                if (e.target.closest('.pill-drop-btn')) return;
                const forEdit = {
                    ...sess,
                    starttimeid: startIdx,
                    endtimeid:   endIdx,
                    programcode: sess.programcode || prog,
                    year_level:  sess.year_level  || yl
                };
                window.handlePillClick(encodeURIComponent(JSON.stringify(forEdit)));
            };

            wrapper.appendChild(pill);
        });
    });
}

window.addEventListener('DOMContentLoaded', async () => {
    try { initDSSMenus(); } catch(e) { console.error('Menu Init Error:', e); }
    try { renderRooms(); } catch(e) { console.error('Room Render Error:', e); }
    updateTimeDropdowns();
    updateSundayOption();

    const initMode = _initData.dataset.initMode || 'subject';
    const initProg = _initData.dataset.initProg || '';
    const initYl   = _initData.dataset.initYl   || '';
    const initAy   = _initData.dataset.initAy   || '';
    const initSem  = _initData.dataset.initSem  || '';

    if (initAy)   document.getElementById('sel_ay').value = initAy;
    if (initSem)  document.getElementById('sel_sem').value = initSem;
    if (initYl)   document.getElementById('sel_year').value = initYl;
    if (initProg) {
        document.getElementById('sel_prog').value = initProg;
        document.getElementById('prog_trigger_text').innerText = initProg;
    }

    if (initProg || initAy) await triggerCascade(true);
    if (initMode === 'program') switchMode('program');
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
    // Enforce year level range based on program's NumYearLevel
    const progOpt = document.querySelector(`#prog_menu .prog-option[data-code="${CSS.escape(code)}"]`);
    const maxYl = progOpt ? parseInt(progOpt.dataset.maxYl || 4) : 4;
    if (typeof _updateYearLevelDropdown === 'function') _updateYearLevelDropdown(maxYl);
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

    // Set lab warning immediately from subject info — don't wait for DSS (prevents stale warnings)
    labWarn.style.display = (_subjInfo && _subjInfo.laboratoryhours > 0) ? 'block' : 'none';

    // Split choice modal removed — default to split mode for multi-hour subjects
    if (_subjInfo && _subjInfo.total_hours >= 3 && !window.currentEditSession) {
        _splitMode = true;
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
                    // Exclude any version the user deleted this session
                    const filteredSessions = er.sessions.filter(
                        s => !window._deletedVersionIds?.has(String(s.versionid))
                    );
                    if (filteredSessions.length > 0) {
                        _hasExistingSchedule = true;
                        if (typeof window._loadExistingSessionsIntoSlices === 'function') {
                            await window._loadExistingSessionsIntoSlices(filteredSessions);
                        } else {
                            showExistingSchedModal(filteredSessions);
                        }
                        return;
                    }
                }
            } catch(e) { /* silent — fall through to normal DSS */ }
        }
    }
    _skipExistingCheck = false;

    try {
        const _ay  = document.getElementById('sel_ay').value;
        const _sem = document.getElementById('sel_sem').value;
        const data = await fetch(`/api/dss/suggest?subject_code=${encodeURIComponent(subjCode)}&ay_id=${encodeURIComponent(_ay)}&sem=${encodeURIComponent(_sem)}`).then(r => r.json());
        if (!data.success) { initDSSMenus(); return; }

        _dssRecommendedFacultyIds = new Set(data.faculty.recommended.map(f => f.id));
        buildDSSMenu('fac', [
            { cls: 'recommended', label: 'RECOMMENDATIONS', items: data.faculty.recommended.map(f => ({ value: f.id, text: f.name, typename: f.typename, max_units: f.max_units, assigned_units: f.assigned_units })) },
            { cls: 'others',      label: 'OTHERS',          items: data.faculty.others.map(f => ({ value: f.id, text: f.name, typename: f.typename, max_units: f.max_units, assigned_units: f.assigned_units })) }
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
        labWarn.style.display = 'none';
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

    // Skip the "load existing sessions" check — the pill was already rendered from existing data.
    // Without this, every pill click calls _loadExistingSessionsIntoSlices again, duplicating
    // pendingManualSchedule entries and resetting the time slice UI unexpectedly.
    _skipExistingCheck = true;
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
    const empFromSess = sess.faculty_id || sess.employeenumber || sess.emp_num || '';
    const fac = empFromSess
        ? (allFaculty.find(f => String(f.id) === String(empFromSess)) ||
           (facStr ? allFaculty.find(f => f.name && f.name.toLowerCase().includes(facStr.toLowerCase())) : null))
        : (facStr ? allFaculty.find(f => f.name && f.name.toLowerCase().includes(facStr.toLowerCase())) : null);
    if (fac) {
        document.getElementById('sel_faculty').value = fac.id;
        document.getElementById('fac_display_name').value = fac.name;
        document.getElementById('fac_trigger_text').textContent = fac.name;
        // Lock BEFORE onFacultySelect so the safety-net inside window.onFacultySelect
        // can restore the panel after triggerDSSLogic resets it mid-chain.
        if (typeof _setFacultyLock === 'function') _setFacultyLock(String(fac.id), fac.name);
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

    // Deferred re-stamp at 200ms — outlasts any chained async resets from triggerDSSLogic
    if (fac) {
        const _stampFac = fac;
        const _stampEmpId = String(fac.id);
        setTimeout(() => {
            const avatarEl  = document.getElementById('fac-stats-avatar');
            const nameEl    = document.getElementById('fac-stats-name');
            const typeEl    = document.getElementById('fac-stats-type');
            const chevron   = document.getElementById('fac-stats-chevron');
            const statsBody = document.getElementById('fac-stats-body');
            if (avatarEl) { avatarEl.style.background = '#546e7a'; avatarEl.textContent = (typeof _facInitialsFromName === 'function' ? _facInitialsFromName(_stampFac.name) : (_stampFac.name || '').slice(0, 2).toUpperCase()); }
            if (nameEl)   { nameEl.textContent = _stampFac.name; nameEl.style.color = '#2c3e50'; nameEl.style.fontWeight = '900'; }
            if (typeEl)   { typeEl.style.color = ''; }
            if (chevron)  { chevron.className = 'fas fa-chevron-down fac-stats-chevron'; chevron.style.color = ''; }
            if (statsBody) statsBody.style.display = '';
            if (typeof _updateFacultyUnitDisplay === 'function') {
                document.getElementById('fac_display_name').value = _stampFac.name;
                _updateFacultyUnitDisplay(_stampEmpId);
            }
        }, 200);
    }
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

    // ── Duration: session must not exceed remaining hours ──
    if (_subjInfo && _subjInfo.total_hours > 0) {
        const sessionHours = (newEndIdx - newStartIdx) * 0.5;
        if (_splitMode === true) {
            const remaining = getRemainingHours();
            if (remaining !== null && remaining > 0.01) {
                if (sessionHours > remaining + 0.01) {
                    await showValidationModal('Duration Exceeded',
                        `This session is ${sessionHours}h but only ${remaining}h remain to be scheduled for this subject. ` +
                        `Please shorten the session accordingly.`);
                    return;
                }
            }
        } else {
            // Single session — must be exactly total_hours
            if (Math.abs(sessionHours - _subjInfo.total_hours) > 0.1) {
                await showValidationModal('Duration Mismatch',
                    `"${subjSel.value}" requires a ${_subjInfo.total_hours}-hour session, ` +
                    `but the selected time block is ${sessionHours}h. ` +
                    `Please adjust the end time to cover exactly ${_subjInfo.total_hours} hour${_subjInfo.total_hours !== 1 ? 's' : ''}.`);
                return;
            }
        }
    }

    if (dayVal === 'Sunday' && _subjInfo && !_subjInfo.is_sunday_allowed) {
        await showValidationModal('Sunday Restriction',
            `Only NSTP and OU subjects may be scheduled on Sunday. "${subjSel.value}" is not allowed on Sunday.`);
        return;
    }

    if (_facInfo && dayVal) {
        const isWkd    = MAN_WEEKDAYS.has(dayVal);
        const startIdx = getTimeSlotIndex(startVal) - 1;

        // ── HC7: Night-class cap for Designee faculty ──
        if (_facInfo.has_designation && isWkd && startIdx >= NIGHT_START_IDX) {
            const nightCap = _facInfo.night_service;
            let pendingNight = 0;
            for (const c of pendingManualSchedule) {
                if (String(c.faculty_id) !== String(facVal)) continue;
                if (c.ay !== ay || c.sem !== sem) continue;
                if (window.currentEditSession && c.temp_id === window.currentEditSession.temp_id) continue;
                if (!MAN_WEEKDAYS.has(c.day)) continue;
                if (getTimeSlotIndex(c.start_time) - 1 >= NIGHT_START_IDX) pendingNight++;
            }
            const dbNight  = (_facLoadData && _facLoadData.night_classes) ? _facLoadData.night_classes : 0;
            const totalNight = dbNight + pendingNight;
            if (nightCap === 0) {
                await showValidationModal('Night Class Restriction',
                    `${facName}'s designation does not allow weekday night classes.`);
                return;
            } else if (totalNight >= nightCap) {
                await showValidationModal('Night Class Limit Reached',
                    `${facName} already has ${totalNight} night class${totalNight !== 1 ? 'es' : ''} ` +
                    `(maximum: ${nightCap} for their designation).`);
                return;
            }
        }
    }

    // ── HC8: Maximum teaching load ──
    if (_facInfo && _facLoadData) {
        const subjectUnits = _subjInfo ? (_subjInfo.creditunits || 0) :
                             ((_subjectMeta && _subjectMeta[subjSel.value]) ? parseFloat(_subjectMeta[subjSel.value].units || 0) : 0);
        let pendingUnits = 0;
        for (const c of pendingManualSchedule) {
            if (String(c.faculty_id) !== String(facVal)) continue;
            if (c.ay !== ay || c.sem !== sem) continue;
            if (window.currentEditSession && c.temp_id === window.currentEditSession.temp_id) continue;
            const uMeta = _subjectMeta && _subjectMeta[c.subject_code];
            pendingUnits += uMeta ? parseFloat(uMeta.units || 0) : 0;
        }
        const scheduledUnits = _facLoadData.scheduled_units || 0;
        const totalAfter     = scheduledUnits + pendingUnits + subjectUnits;
        const maxLoad        = _facLoadData.total_units || 0;
        if (maxLoad > 0 && totalAfter > maxLoad) {
            await showValidationModal('Maximum Load Exceeded',
                `This assignment would bring ${facName}'s total load to ${totalAfter} unit${totalAfter !== 1 ? 's' : ''}, ` +
                `exceeding the allowed maximum of ${maxLoad} units.`);
            return;
        }
    }

    // ── Specialization: Faculty must match subject's required specialization ──
    if (_facInfo && _facInfo.specializationname) {
        const expectedSpec = _getSubjectSpecGroup(subjSel.value);
        if (expectedSpec !== null) {
            const facSpec = _facInfo.specializationname || '';
            if (facSpec !== expectedSpec) {
                await showValidationModal('Specialization Mismatch',
                    `${facName}'s specialization (${facSpec}) does not match what is required ` +
                    `for "${subjSel.value}" (${expectedSpec}). ` +
                    `Only faculty with the matching specialization may be assigned.`);
                return;
            }
        }
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

    // ── Section conflict: a section (program + year level) cannot have overlapping classes ──
    if (prog && yl) {
        try {
            const sResp = await fetch(
                `/api/manual/section_schedule?program=${encodeURIComponent(prog)}&year_level=${encodeURIComponent(yl)}&ay_id=${encodeURIComponent(ay)}&semester=${encodeURIComponent(sem)}`
            );
            const sSessions = await sResp.json();
            for (const s of sSessions) {
                if (s.daydesc !== dayVal) continue;
                if (window.currentEditSession &&
                    s.subjectcode === window.currentEditSession.subjectcode &&
                    s.daydesc     === window.currentEditSession.daydesc &&
                    s.starttimeid === window.currentEditSession.starttimeid) continue;
                if (newStartIdx < s.endtimeid && newEndIdx > s.starttimeid) {
                    await showConflictModal(
                        `Section conflict: "${s.subjectname || s.subjectcode}" is already scheduled ` +
                        `for ${prog} Year ${yl} on ${dayVal} — ` +
                        `${timeSlots[s.starttimeid - 1]} to ${timeSlots[s.endtimeid - 1]}. ` +
                        `A section cannot have two classes at the same time.`
                    );
                    return;
                }
            }
        } catch(e) { console.error('Section conflict check failed:', e); }

        for (const c of pendingManualSchedule) {
            if (window.currentEditSession && c.temp_id === window.currentEditSession.temp_id) continue;
            if (c.course !== prog || String(c.year_level) !== String(yl)) continue;
            if (c.ay !== ay || c.sem !== sem) continue;
            if (c.day !== dayVal) continue;
            const cStart = getTimeSlotIndex(c.start_time);
            const cEnd   = getTimeSlotIndex(c.end_time);
            if (newStartIdx < cEnd && newEndIdx > cStart) {
                await showConflictModal(
                    `Section conflict: "${c.subject_name}" is already placed for ` +
                    `${prog} Year ${yl} on ${dayVal} — ${c.start_time} to ${c.end_time}. ` +
                    `A section cannot have two classes at the same time.`
                );
                return;
            }
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
    if (currentMode === 'program') {
        renderProgramTimetable();
    } else {
        renderGrid(newClass.room_id, formAyFilter(), formSemFilter());
    }
}

/* ── Two-step session deletion (works for local pending AND saved DB sessions) ── */
window._dropSession = async function(sessDataEncoded, event) {
    event.stopPropagation();
    let sd;
    try { sd = JSON.parse(decodeURIComponent(sessDataEncoded)); } catch(e) { return; }

    // Step 1
    const ok1 = await (typeof showConfirmModal === 'function'
        ? showConfirmModal(`You are about to delete this schedule:\n\n${sd.label}`, 'Delete Schedule')
        : Promise.resolve(window.confirm(`You are about to delete:\n${sd.label}`)));
    if (!ok1) return;

    // Step 2
    const ok2 = await (typeof showConfirmModal === 'function'
        ? showConfirmModal('Are you sure you want to delete this schedule?\nThis action cannot be undone.', 'Final Confirmation')
        : Promise.resolve(window.confirm('Are you sure? This action cannot be undone.')));
    if (!ok2) return;

    // Call DELETE API when a saved version_id is present
    if (sd.versionid) {
        try {
            const resp = await fetch('/api/schedule/delete_session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ version_id: sd.versionid })
            });
            const data = await resp.json();
            if (!data.success) {
                if (typeof showValidationModal === 'function')
                    await showValidationModal('Delete Failed', data.error || 'Could not delete session.');
                return;
            }
            // Track deleted version so it won't be reloaded into slices this session
            if (window._deletedVersionIds) window._deletedVersionIds.add(String(sd.versionid));

            // Immediately clear the subject's saved-status badge and dbScheduled so the
            // curriculum guide updates before the async _updateSubjectList fetch returns.
            const _deletedCode = sd.subjectcode || (sd.dbKey || '').split('_')[0] || null;
            if (_deletedCode) {
                const _opt = Array.from(document.getElementById('sel_subj')?.options || [])
                    .find(o => o.value === _deletedCode || (o.value || '').toUpperCase() === _deletedCode.toUpperCase());
                if (_opt) { _opt.dataset.savedStatus = ''; _opt.dataset.dbScheduled = '0'; }
                const _card = document.querySelector(`.subj-item[data-code="${_deletedCode}"]`);
                if (_card) {
                    const _badge = _card.querySelector('.subj-badge');
                    if (_badge) _badge.remove();
                    const _txt = _card.querySelector('.subj-hrs-text');
                    if (_txt) _txt.textContent = `0/${_opt ? (_opt.dataset.hours || '?') : '?'}h`;
                    const _fill = _card.querySelector('.subj-hrs-fill');
                    if (_fill) { _fill.style.width = '0%'; _fill.className = 'subj-hrs-fill incomplete'; }
                }
            }
        } catch(e) {
            if (typeof showValidationModal === 'function')
                await showValidationModal('Connection Error', 'Could not reach the server.');
            return;
        }
    }

    // Remove from pendingManualSchedule if it was a local entry
    if (sd.temp_id) {
        pendingManualSchedule = pendingManualSchedule.filter(c => c.temp_id !== sd.temp_id);
        if (window.currentEditSession && window.currentEditSession.temp_id === sd.temp_id) unlockFormFields();
    }

    // Mask the DB row so it doesn't reappear before page refresh
    if (sd.dbKey) hiddenDbSchedules.add(sd.dbKey);

    // Remove the matching time-slice row from the UI
    document.querySelectorAll('.ts-row').forEach(row => {
        try {
            const parsed = JSON.parse(row.dataset.existingJson || '{}');
            if ((parsed._localTempId && parsed._localTempId === sd.temp_id) ||
                (parsed.versionid    && parsed.versionid    === sd.versionid)) {
                row.remove();
            }
        } catch(e) {}
    });
    if (typeof _renumberSlices === 'function') _renumberSlices();

    const currentRoom = document.getElementById('sel_room').value;
    if (currentMode === 'program') renderProgramTimetable();
    else renderGrid(currentRoom, formAyFilter(), formSemFilter());

    if (typeof _updateSubjectList === 'function') await _updateSubjectList();
    if (typeof _refreshFlIfVisible === 'function') _refreshFlIfVisible();
};

window.dropLocalClass = function(tempId, event) {
    event.stopPropagation();
    // Delegate to _dropSession for new (unsaved) local sessions — no versionid needed
    const sess = pendingManualSchedule.find(c => c.temp_id === tempId);
    if (sess) {
        const label = `${sess.subject_code} on ${sess.day} at ${sess.start_time} – ${sess.end_time} in ${sess.room || 'TBA'}`;
        const sd = encodeURIComponent(JSON.stringify({ temp_id: tempId, versionid: null, dbKey: null, label }));
        window._dropSession(sd, event);
        return;
    }
    // Fallback: direct removal (no DB record)
    pendingManualSchedule = pendingManualSchedule.filter(c => c.temp_id !== tempId);
    if (window.currentEditSession && window.currentEditSession.temp_id === tempId) unlockFormFields();

    // Remove the corresponding time slice row
    document.querySelectorAll('.ts-row').forEach(row => {
        try {
            if (row.dataset.existingJson) {
                const parsed = JSON.parse(row.dataset.existingJson);
                if (parsed._localTempId === tempId) { row.remove(); return; }
            }
            // Rows added via confirmAndPlace store temp_id directly
            if (row.dataset.tempId === tempId) row.remove();
        } catch(e) {}
    });

    if (currentMode === 'program') {
        renderProgramTimetable();
    } else {
        const currentRoom = document.getElementById('sel_room').value;
        renderGrid(currentRoom, formAyFilter(), formSemFilter());
    }
};

function resetFormState() {
    document.getElementById('sel_subj').value = '';
    document.getElementById('sel_faculty').value = '';
    document.getElementById('fac_display_name').value = '';
    document.getElementById('fac_trigger_text').textContent = '-Select Faculty-';
    _hasExistingSchedule = false;
    _pendingExistingSessions = [];
    _subjInfo = null;
    _facInfo = null;
    _facLoadData = null;
    _splitMode = null;
    updateSundayOption();
    updateHoursProgressNote();
    unlockFormFields();
    hiddenDbSchedules.clear();
    updateSummary();
    if (typeof _updateFacultyUnitDisplay === 'function') _updateFacultyUnitDisplay(null);
    if (typeof _updateWorkflowBar === 'function') _updateWorkflowBar();
}

// btnManualSaveDraft removed from HTML — guard kept for safety
const _btnSaveDraftCenter = document.getElementById('btnManualSaveDraft');
if (_btnSaveDraftCenter) {
    _btnSaveDraftCenter.addEventListener('click', async () => {
        if (typeof window._triggerSaveDraft === 'function') await window._triggerSaveDraft();
    });
}

document.getElementById('btnManualApprove').addEventListener('click', async () => {
    const ay   = formAyFilter();
    const sem  = formSemFilter();
    const prog = document.getElementById('sel_prog').value;
    const yl   = document.getElementById('sel_year').value;

    if (!ay || !sem || !prog || !yl) {
        await showValidationModal('Missing Context', 'Please select Academic Year, Semester, Program, and Year Level before publishing.');
        return;
    }

    let contextDrafts = pendingManualSchedule.filter(c => c.ay === ay && c.sem === sem);

    if (contextDrafts.length === 0) {
        // Try loading saved Draft sessions from DB
        try {
            const dbResp = await fetch(`/api/schedule/draft_sessions?program=${encodeURIComponent(prog)}&year_level=${encodeURIComponent(yl)}&ay_id=${encodeURIComponent(ay)}&sem=${encodeURIComponent(sem)}`);
            const dbData = await dbResp.json();
            if (dbData.success && dbData.sessions && dbData.sessions.length > 0) {
                contextDrafts = dbData.sessions;
            }
        } catch(e) {}
    }

    if (contextDrafts.length === 0) {
        // Direct publish: collect from filled UI time slices (no draft save required)
        const filledRows = Array.from(document.querySelectorAll('.ts-row')).filter(row => {
            const day   = row.querySelector('.ts-day-sel')?.value || '';
            const start = row.querySelector('.ts-start-hidden')?.value || '';
            const end   = row.querySelector('.ts-end-hidden')?.value  || '';
            const room  = row.querySelector('.ts-room-hidden')?.value  || '';
            return day && start && end && room;
        });

        if (!filledRows.length) {
            await showValidationModal('Nothing to Publish', 'No sessions are scheduled for the selected context. Fill in time slices or save a draft first.');
            return;
        }

        for (const row of filledRows) {
            const day    = row.querySelector('.ts-day-sel').value;
            const start  = row.querySelector('.ts-start-hidden').value;
            const end    = row.querySelector('.ts-end-hidden').value;
            const roomId = row.querySelector('.ts-room-hidden').value;
            const roomName = (typeof allRooms !== 'undefined' && allRooms.find(r => String(r.id) === String(roomId))?.name)
                          || row.querySelector('.ts-room-field .ts-ss-input')?.value.trim() || '';

            document.getElementById('sel_day').value = day;
            if (typeof _injectTimeOpt === 'function') { _injectTimeOpt('sel_start_time', start); _injectTimeOpt('sel_end_time', end); }
            document.getElementById('sel_room').value = roomId;
            document.getElementById('room_display_name').value = roomName;
            if (typeof allRooms !== 'undefined') _roomInfo = allRooms.find(r => String(r.id) === String(roomId)) || null;
            window.currentEditSession = null;

            const prevLen = pendingManualSchedule.length;
            await confirmAndPlace();
            if (pendingManualSchedule.length <= prevLen) return; // validation failed in confirmAndPlace
        }
        contextDrafts = pendingManualSchedule.filter(c => c.ay === ay && c.sem === sem);
    }

    if (contextDrafts.length === 0) {
        await showValidationModal('Nothing to Publish', 'No sessions to publish for the selected context.');
        return;
    }

    // Show publish slice-selection modal
    const publishCandidates = contextDrafts.map(c => ({
        subject:  c.subject_code || c.subjectcode || '—',
        day:      c.day || c.daydesc || '—',
        start:    c.start_time || (c.time ? c.time.split(' - ')[0] : '') || '—',
        end:      c.end_time   || (c.time ? c.time.split(' - ')[1] : '') || '—',
        roomName: c.room || c.roomname || 'TBA',
        _raw:     c
    }));

    const selectedCandidates = await _showPublishSelectModal(publishCandidates);
    if (!selectedCandidates.length) return;

    const selectedDrafts = selectedCandidates.map(c => c._raw);

    const context = { program: prog, yearLevel: parseInt(yl), term: sem, acadYear: ay };

    // Two-step publish confirmation
    const ok1 = await showConfirmModal('You are about to publish this schedule.', 'Publish Schedule');
    if (!ok1) return;
    const ok2 = await showConfirmModal('Are you sure you want to publish this schedule? This action will make the schedule visible to all users.', 'Final Confirmation');
    if (!ok2) return;

    const btn = document.getElementById('btnManualApprove');
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Publishing...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/schedule/approve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ schedule_data: selectedDrafts, context })
        });
        const data = await res.json();

        if (data.success) {
            window.isLeavingIntentionally = true;
            await showValidationModal('Schedule Published', 'The schedule has been published successfully.');
            pendingManualSchedule = pendingManualSchedule.filter(c => !(c.ay === ay && c.sem === sem));
            hiddenDbSchedules.clear();
            const currentSubj2 = document.getElementById('sel_subj').value;
            if (currentSubj2) {
                try {
                    // Use existing_sessions (returns both Draft and Published with correct statuses)
                    // so the editor immediately shows the correct Published/remaining-Draft state.
                    const exResp2 = await fetch(`/api/manual/existing_sessions?subject_code=${encodeURIComponent(currentSubj2)}&program=${encodeURIComponent(prog)}&year_level=${encodeURIComponent(yl)}&ay_id=${encodeURIComponent(ay)}&semester=${encodeURIComponent(sem)}`);
                    const exData2 = await exResp2.json();
                    if (exData2.success && exData2.sessions && exData2.sessions.length) {
                        if (typeof _loadExistingSessionsIntoSlices === 'function') {
                            await _loadExistingSessionsIntoSlices(exData2.sessions);
                        }
                    } else {
                        document.getElementById('time-slots-container').innerHTML = '';
                        if (typeof addNewTimeSlot === 'function') addNewTimeSlot('','','','','',true);
                    }
                } catch(e) {
                    document.getElementById('time-slots-container').innerHTML = '';
                    if (typeof addNewTimeSlot === 'function') addNewTimeSlot('','','','','',true);
                }
            } else {
                resetFormState();
            }
            if (typeof _updateSubjectList === 'function') await _updateSubjectList();
            if (currentMode === 'program') { renderProgramTimetable(); } else { renderGrid(document.getElementById('sel_room').value, ay, sem); }
            // Rebuild FL dropdown (newly published faculty now confirmed) and refresh if visible
            if (typeof _initFlFacultyMenu === 'function') await _initFlFacultyMenu();
            if (typeof _refreshFlIfVisible === 'function') _refreshFlIfVisible();
            setTimeout(() => window.isLeavingIntentionally = false, 100);
        } else if (data.violations && data.violations.length) {
            let errorMsg = `Cannot publish — constraint violation(s):\n\n`;
            data.violations.forEach(v => errorMsg += `• ${v.detail}\n`);
            await showValidationModal('Constraint Violations', errorMsg);
        } else {
            await showValidationModal('Publish Failed', data.error || 'An unknown error occurred. Please try again.');
        }
    } catch (e) {
        await showValidationModal('Connection Error', 'Could not reach the server. Please try again.');
    } finally {
        btn.innerHTML = '<i class="fas fa-check-circle"></i> APPROVE SCHEDULE';
        btn.disabled = false;
    }
});

function getTimeSlotIndex(timeStr) {
    const idx = timeSlots.indexOf(timeStr);
    return idx >= 0 ? idx + 1 : 1;
}

async function renderGrid(roomId, ayFilter = '', semFilter = '') {
    if (currentMode === 'program') return;
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
            temp_id:      c.temp_id,
            versionid:    c.versionid || null,
            subjectcode:  c.subject_code,
            subjectname:  c.subject_name,
            instructor:   c.instructor,
            daydesc:      c.day,
            starttimeid:  getTimeSlotIndex(c.start_time),
            endtimeid:    getTimeSlotIndex(c.end_time),
            start_fmt:    c.start_time,
            end_fmt:      c.end_time,
            roomname:     c.room,
            course:       c.course,
            year_level:   c.year_level,
            isLocal:      true,
            fromExisting: c.fromExisting || false,
            status:       c.status || 'Draft',
            isPreview:    c.isPreview || false
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

                const isDraft = !sess.status || String(sess.status).toLowerCase() !== 'published';
                pill.style.backgroundColor = isDraft ? (sess.isPreview ? '#c8d6da' : '#8e9ca0') : getSubjectColor(sess.subjectcode);
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
                pill.dataset.pillKey = `${sess.subjectcode}_${sess.daydesc}_${sess.starttimeid}`;

                const instrLast = (sess.instructor || 'TBA').split(',')[0].trim();
                pill.title = `${sess.subjectcode}\n${sess.subjectname || ''}\n${sess.instructor || ''}`;

                const _dbKey = `${sess.subjectcode}_${sess.daydesc}_${sess.starttimeid}`;
                const _label = `${sess.subjectcode} — ${sess.daydesc} ${sess.start_fmt || ''} – ${sess.end_fmt || ''} in ${sess.roomname || 'TBA'}`;
                const _sd    = encodeURIComponent(JSON.stringify({ temp_id: sess.temp_id || null, versionid: sess.versionid || null, dbKey: _dbKey, label: _label, subjectcode: sess.subjectcode || null }));
                const dropBtn = `<button class="pill-drop-btn" onclick="_dropSession('${_sd}', event)" title="Remove"><i class="fas fa-times"></i></button>`;

                pill.innerHTML = `
                    ${dropBtn}
                    <div class="pill-subject" style="margin-top:8px;">${sess.subjectcode}</div>
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

    // If the currently selected room doesn't belong to this building, clear the selection
    const currentRoomId = document.getElementById('sel_room').value;
    if (currentRoomId) {
        const currentRoom = allRooms.find(r => String(r.id) === String(currentRoomId));
        const stillVisible = currentRoom &&
            (bldgId === 'ALL' || String(currentRoom.bldg_id) === String(bldgId));
        if (!stillVisible) {
            document.getElementById('sel_room').value          = '';
            document.getElementById('room_display_name').value = '';
            const trigger = document.getElementById('room_trigger_text');
            if (trigger) trigger.textContent = '-- Select Room --';
            _roomInfo = null;
            // Clear pills — renderGrid returns early when roomId is empty
            renderGrid('', formAyFilter(), formSemFilter());
        }
    }

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
                // --- FIX: Ipadala ang ay_id at year_level para makuha ang tamang Cohort Curriculum ---
                const resp = await fetch(`/api/get_curriculum?program=${encodeURIComponent(prog)}&ay_id=${encodeURIComponent(ay)}&year_level=${encodeURIComponent(year)}`);
                const data = await resp.json();

            if (data.success) {
                currDisplay.innerText = data.curriculum_code;
                currHidden.value = data.curriculum_id;

                if (year && sem && !window.currentEditSession) {
                    const sResp = await fetch(`/api/get_subjects?curriculum_id=${data.curriculum_id}&year_level=${year}&semester=${sem}&ay_id=${encodeURIComponent(ay)}`);
                    const sData = await sResp.json();
                    subjSelect.innerHTML = '<option value="">-- Select Subject --</option>';
                    if (sData.subjects && sData.subjects.length > 0) {
                        sData.subjects.forEach(s => {
                            const opt = document.createElement('option');
                            opt.value = s.subjectcode;
                            opt.textContent = s.subjectname;
                            opt.dataset.units    = s.creditunits    || 0;
                            opt.dataset.hours    = s.total_hours    || 0;
                            opt.dataset.labhours = s.laboratoryhours || 0;
                            opt.dataset.dbScheduled = s.scheduled_hours || 0;
                            subjSelect.appendChild(opt);
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

    // Refresh subject list panel with status badges (Draft/Published) after every cascade
    if (typeof _updateSubjectList === 'function') {
        _updateSubjectList().catch(() => {});
    }

    if (skipGridRender !== true) {
        if (currentMode === 'program') {
            renderProgramTimetable();
        } else {
            const currentRoomId = document.getElementById('sel_room').value;
            renderGrid(currentRoomId, ay, sem);
        }
    }
}

async function selectRoom(roomId, roomName) {
    document.getElementById('sel_room').value          = roomId;
    document.getElementById('room_display_name').value = roomName;
    document.getElementById('room_trigger_text').textContent = roomName;
    document.getElementById('sum_room').innerText      = roomName;

    const room = allRooms.find(r => String(r.id) === String(roomId));
    _roomInfo = room || null;
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
