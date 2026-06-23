const _initData  = document.getElementById('app-init-data');
const allRooms   = JSON.parse(_initData.dataset.rooms);
const allFaculty = JSON.parse(_initData.dataset.faculty);
const LAB_CONSTRAINT_ENABLED  = _initData.dataset.labConstraint !== 'false';
const WEEKEND_ENABLED         = _initData.dataset.weekendEnabled !== 'false';
const WEEKEND_DAY_SCOPE       = _initData.dataset.weekendDay     || 'sunday_only';
const WEEKEND_SUBJECT_SCOPE   = _initData.dataset.weekendSubject || 'nstp_only';
const SPEC_CONSTRAINT_ENABLED = _initData.dataset.specEnabled    !== 'false';
const MERGE_ENABLED           = _initData.dataset.mergeEnabled   !== 'false';
const MERGE_SCOPE             = _initData.dataset.mergeScope      || 'nstp_only';
// Read at file-load time from the data attribute — never depends on inline script order
const _IS_LOCAL_MODE          = (_initData.dataset.schedulerMode || '') === 'local';

// Read scheduler mode from the template-injected constant (defined in inline <script> after this file loads).
// Safe to call inside any function — SCHED_MODE will be defined before any UI event fires.
function _sm() { return typeof SCHED_MODE !== 'undefined' ? SCHED_MODE : 'official'; }

// Returns the merge mode for a subject under the active policy:
//   'flexible' — NSTP/OU: same subject is enough; different faculty allowed (large combined session)
//   'strict'   — non-NSTP: same subject AND same faculty required
//   'none'     — merging not permitted for this subject under current policy
function _getMergeMode(subjectCode) {
    if (!MERGE_ENABLED) return 'none';
    const upper  = (subjectCode || '').toUpperCase();
    const isNstp = upper.startsWith('NSTP') || upper.startsWith('OU');
    if (MERGE_SCOPE === 'nstp_only')    return isNstp  ? 'flexible' : 'none';
    if (MERGE_SCOPE === 'non_nstp')     return !isNstp ? 'strict'   : 'none';
    if (MERGE_SCOPE === 'all_subjects') return isNstp  ? 'flexible' : 'strict';
    return 'none';
}

function _isSubjectMergeEligible(subjectCode) {
    return _getMergeMode(subjectCode) !== 'none';
}

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

// Day pairing alignment settings — loaded from DB-configured HC settings
const DAY_PAIRING_ENABLED = _initData.dataset.dayPairingEnabled !== 'false';
const VALID_DAY_PAIRS = (() => {
    try {
        return JSON.parse(_initData.dataset.dayPairs || '[]');
    } catch(e) {
        return [['Monday','Thursday'],['Tuesday','Friday'],['Wednesday','Saturday']];
    }
})();
// Fast lookup: for any day, what is its valid partner in the configured pairs?
const DAY_PAIR_MAP = (() => {
    const map = {};
    for (const pair of VALID_DAY_PAIRS) {
        if (pair.length === 2) {
            map[pair[0]] = pair[1];
            map[pair[1]] = pair[0];
        }
    }
    return map;
})();
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

// Multi-tier specialization mapping: exact match + related fields per subject group.
// 'exact'  → confirmed compatible; 'related' → acceptable, soft note only.
// Prefixes not listed here → unrestricted (GEED, NSTP, PATHFIT, PHED, ROTC, MATH, etc.)
const _SUBJ_SPEC_GROUPS = [
    {
        prefixes: ['COMP', 'INTE', 'ICTE', 'ITEC', 'ELEC IT', 'ELECT IT'],
        primary:  'Computer and Information Sciences',
        exact:    ['Computer and Information Sciences', 'Computer Science',
                   'Information Technology', 'Information Systems',
                   'Information and Communications Technology'],
        related:  ['Software Engineering', 'Computer Engineering', 'Cybersecurity',
                   'Information Security', 'Networking', 'Network Technology',
                   'Data Science', 'Data Analytics', 'Artificial Intelligence',
                   'Electronics and Communications Engineering'],
    },
    {
        prefixes: ['ARCH', 'ARCHS'],
        primary:  'Architecture, Design and the Built Environment',
        exact:    ['Architecture, Design and the Built Environment', 'Architecture'],
        related:  ['Interior Design', 'Urban Planning', 'Fine Arts', 'Industrial Design'],
    },
    {
        prefixes: ['CIEN', 'ENSC'],
        primary:  'Engineering',
        exact:    ['Engineering', 'Civil Engineering', 'Mechanical Engineering'],
        related:  ['Electrical Engineering', 'Electronics Engineering',
                   'Chemical Engineering', 'Industrial Engineering',
                   'Environmental Engineering'],
    },
    {
        prefixes: ['ACCO'],
        primary:  'Accountancy and Finance',
        exact:    ['Accountancy and Finance', 'Accountancy', 'Accounting', 'Auditing'],
        related:  ['Finance', 'Business Administration', 'Management', 'Economics'],
    },
    {
        prefixes: ['BUMA', 'HRMA'],
        primary:  'Business Administration',
        exact:    ['Business Administration', 'Management', 'Human Resource Management',
                   'Business Management'],
        related:  ['Entrepreneurship', 'Marketing', 'Office Administration',
                   'Public Administration', 'Accountancy and Finance', 'Economics'],
    },
];

// Returns { level: 'exact'|'related'|'mismatch'|'unrestricted', label: string }
function _getSpecCompatibility(facSpec, subjectCode) {
    const upper = (subjectCode || '').toUpperCase();
    const spec  = (facSpec || '').trim();

    for (const group of _SUBJ_SPEC_GROUPS) {
        if (!group.prefixes.some(pfx => upper.startsWith(pfx))) continue;

        if (!spec) return { level: 'unrestricted', label: 'No Specialization on File' };

        const specLower = spec.toLowerCase();
        const inList = (list) => list.some(a =>
            a.toLowerCase() === specLower ||
            specLower.includes(a.toLowerCase()) ||
            a.toLowerCase().includes(specLower)
        );

        if (inList(group.exact))   return { level: 'exact',   label: 'Highly Matched' };
        if (inList(group.related)) return { level: 'related', label: 'Related Field'  };
        return { level: 'mismatch', label: 'Potential Mismatch' };
    }
    return { level: 'unrestricted', label: 'No Restriction' };
}

let _facLoadData = null;             // cached from /api/manual/faculty_load
let _roomInfo    = null;             // cached from allRooms on room select
let _dssRecommendedFacultyIds = new Set(); // faculty IDs in DSS recommended list

function _updateLabWarning(el, isLab, constraintEnabled) {
    if (!el) return;
    const textEl = document.getElementById('room_dss_warning_text');
    if (!isLab) { el.style.display = 'none'; return; }
    if (constraintEnabled) {
        el.style.color = '#c0392b';
        if (textEl) textEl.textContent = 'This subject has lab hours.';
    } else {
        el.style.color = '#b06000';
        if (textEl) textEl.textContent = 'This subject has lab hours.';
    }
    el.style.display = 'block';
}

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

    // HC3: Part-Time time window — enforced in Official mode, relaxed (warning only) in Local mode
    const isPT        = _sm() !== 'local' && tn === 'Part-Time' && isWkd && day;
    const minStartIdx = isPT ? _hhmm_to_slot_idx(_facInfo.parttime_start, 19) : 0;
    const maxEndIdx   = _sm() === 'local' ? timeSlots.length - 1 : _getMaxEndIdx();
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
    _checkFacultySpecWarning();
    await updateTimeDropdowns();
}

function _checkFacultySpecWarning() {
    const container = document.getElementById('fac-spec-warning');
    if (!container) return;
    container.innerHTML = '';

    if (!SPEC_CONSTRAINT_ENABLED || !_facInfo || !_subjInfo) return;

    const spec     = (_facInfo.specializationname || '').trim();
    const subjCode = (document.getElementById('sel_subj')?.value || '').trim();
    const compat   = _getSpecCompatibility(spec, subjCode);

    if (compat.level === 'unrestricted' || compat.level === 'exact') return;

    const facName  = (_facInfo.fullname || 'This faculty member').trim();
    const isImported = !!window.currentEditSession;

    if (compat.level === 'related') {
        // Soft informational note — green-tinted
        container.innerHTML =
            `<div style="background:#eafaf1;border:1.5px solid #27ae60;border-radius:8px;` +
            `padding:10px 14px;margin-top:8px;font-size:13px;color:#1a6b3c;` +
            `display:flex;gap:8px;align-items:flex-start;">` +
            `<span style="font-size:15px;margin-top:1px">&#10003;</span>` +
            `<span><strong>${facName}</strong> (${spec}) — <strong>Related Field</strong> for ${subjCode}. ` +
            `This specialization is acceptable for this subject.</span></div>`;
        return;
    }

    if (compat.level === 'mismatch') {
        if (isImported) {
            // Imported/existing session — informational only, never blocking
            const group   = _SUBJ_SPEC_GROUPS.find(g => g.prefixes.some(pfx => subjCode.toUpperCase().startsWith(pfx)));
            const primary = group ? group.primary : 'the required field';
            container.innerHTML =
                `<div style="background:#fdf3e7;border:1.5px solid #e67e22;border-radius:8px;` +
                `padding:10px 14px;margin-top:8px;font-size:13px;color:#7d4000;` +
                `display:flex;gap:8px;align-items:flex-start;">` +
                `<span style="font-size:15px;margin-top:1px">&#8505;</span>` +
                `<span>This assignment was imported from an approved official schedule. ` +
                `Specialization analysis: <strong>Potential Mismatch</strong> — ` +
                `${facName} (${spec}) vs. expected ${primary}. ` +
                `No action required unless reviewed by the scheduler.</span></div>`;
        } else {
            // New manual assignment — informational note only (no longer a hard block)
            const group   = _SUBJ_SPEC_GROUPS.find(g => g.prefixes.some(pfx => subjCode.toUpperCase().startsWith(pfx)));
            const primary = group ? group.primary : 'the required field';
            container.innerHTML =
                `<div style="background:#fdf3e7;border:1.5px solid #e67e22;border-radius:8px;` +
                `padding:10px 14px;margin-top:8px;font-size:13px;color:#7d4000;` +
                `display:flex;gap:8px;align-items:flex-start;">` +
                `<span style="font-size:15px;margin-top:1px">&#8505;</span>` +
                `<span>Specialization note: <strong>${facName}</strong> (${spec}) may not be the ideal fit ` +
                `for <strong>${subjCode}</strong> (expected: ${primary}). This is for reference only.</span></div>`;
        }
    }
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
    // Only count confirmed new/modified entries — exclude fromExisting (already in DB)
    // and isPreview (tentative UI state not yet committed by the user).
    return pendingManualSchedule.some(s => !s.fromExisting && !s.isPreview) || !!window._pendingFacultyAssignment;
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
    searchInput.setAttribute('autocomplete', 'one-time-code');
    searchInput.setAttribute('autocorrect', 'off');
    searchInput.setAttribute('autocapitalize', 'off');
    searchInput.setAttribute('spellcheck', 'false');
    searchInput.setAttribute('data-lpignore', 'true');
    searchInput.setAttribute('data-form-type', 'other');
    searchInput.readOnly = true;
    searchInput.addEventListener('focus', function() { this.readOnly = false; }, { once: false });
    searchInput.placeholder = type === 'room' ? 'Search room code or name…' : 'Ex., Dela Cruz, Juan';
    searchInput.addEventListener('input', function() {
        const q = this.value.toLowerCase();
        // Track which section headers have any visible items
        const headerVisibility = new Map();
        menu.querySelectorAll('.dss-section-header').forEach(hdr => headerVisibility.set(hdr, false));

        let lastHeader = null;
        menu.querySelectorAll('.dss-section-header, .dss-option').forEach(el => {
            if (el.classList.contains('dss-section-header')) {
                lastHeader = el;
            } else {
                // Support both faculty (.fac-opt-name) and room (.room-opt-name) name elements
                const nameEl = el.querySelector('.fac-opt-name') || el.querySelector('.room-opt-name');
                const text   = nameEl ? nameEl.textContent : el.textContent;
                const visible = !q || text.toLowerCase().includes(q);
                el.style.display = visible ? '' : 'none';
                if (visible && lastHeader) headerVisibility.set(lastHeader, true);
            }
        });
        // Hide section headers that have no visible items
        headerVisibility.forEach((hasVisible, hdr) => {
            hdr.style.display = hasVisible ? '' : 'none';
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
        const isOpt = sec.cls === 'optional';
        sec.items.forEach(item => {
            const div = document.createElement('div');
            div.className = 'dss-option' + (isRec ? ' recommended-item' : isOpt ? ' optional-item' : '');
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
                if (item.type) {
                    const isLab = item.type === 'Laboratory';
                    div.className += ' room-dss-opt';
                    div.innerHTML = `<span class="room-opt-name">${item.text}</span>`
                        + `<span class="room-type-tag ${isLab ? 'lab' : 'lec'}">${isLab ? 'LAB' : 'LEC'}</span>`;
                } else {
                    div.textContent = item.text;
                }
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

/* Convert "07:30:00" / "07:30" / "07:30 AM" / "7:30 PM" to 1-based timeSlot index.
   Grid origin: 07:30 AM = slot 1. Steps: 30-minute intervals. */
function timeStrToSlotIdx(timeStr) {
    if (!timeStr) return 0;
    const s   = String(timeStr).trim();
    const low = s.toLowerCase();

    // Split on ':' — take only first two parts for h/m (ignore seconds)
    const colonParts = s.split(':');
    if (colonParts.length < 2) return 0;

    let h = parseInt(colonParts[0], 10);
    // minutes: strip any trailing am/pm/space before parsing
    let m = parseInt(colonParts[1].replace(/[^0-9]/g, ''), 10);
    if (isNaN(h) || isNaN(m)) return 0;

    const hasPm = low.includes('pm');
    const hasAm = low.includes('am');

    if (hasPm && h !== 12) h += 12;      // 1:00 PM → 13, 11:30 PM → 23
    if (hasAm && h === 12) h = 0;        // 12:00 AM → 0 (midnight)
    // 12:00 PM → stays 12 (noon) — correct already

    const totalMins = h * 60 + m;
    const idx = Math.round((totalMins - 450) / 30) + 1; // 7:30 AM = 450 min = slot 1
    if (idx < 1 || idx > timeSlots.length + 1) return 0;
    return idx;
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
    const _pvSectId = document.getElementById('sel_section')?.value || '';
    if (!_pvSectId) {
        const noSectMsg = '— Select a Section to view its schedule —';
        if (pvLabelEl) pvLabelEl.innerHTML = noSectMsg;
        wrapper.querySelectorAll('.schedule-pill').forEach(p => p.remove());
        return;
    }

    const semLabels = { A: '1ST SEMESTER', B: '2ND SEMESTER', C: 'SUMMER' };
    const yrLabels  = { '1':'1ST YEAR','2':'2ND YEAR','3':'3RD YEAR','4':'4TH YEAR','5':'5TH YEAR' };
    const progName  = document.getElementById('prog_trigger_text').innerText || prog;
    const _sectNameHdr = document.getElementById('bc-sect-text')?.textContent?.trim() || '';
    const sectSuffix   = (_sectNameHdr && _sectNameHdr !== '-Select Section-') ? ` &nbsp;|&nbsp; ${_sectNameHdr}` : '';
    const headerTxt = `${progName.toUpperCase()} &mdash; ${yrLabels[yl] || yl} &nbsp;|&nbsp; A.Y ${ay} &nbsp;|&nbsp; ${semLabels[sem] || sem}${sectSuffix}`;
    if (pvLabelEl) pvLabelEl.innerHTML  = headerTxt;
    if (labelEl)   labelEl.textContent  = `${progName.toUpperCase()}  —  ${yrLabels[yl] || yl}  |  A.Y ${ay}  |  ${semLabels[sem] || sem}`;

    try {
        const url = `/api/get_offerings_schedule?program=${encodeURIComponent(prog)}&year_level=${yl}&semester=${sem}&ay=${encodeURIComponent(ay)}&status=active&section_id=${encodeURIComponent(_pvSectId)}&_t=${Date.now()}`;
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
                if (!oS || !oE || oE <= oS) return; // skip invalid siblings
                if (startIdx < oE && endIdx > oS) { overlapCount++; if (idx > oIdx) overlapIndex++; }
            });

            const pill = document.createElement('div');
            pill.className = 'schedule-pill';

            const isDraft = !sess.status || sess.status.toLowerCase() !== 'published';
            // Local mode: always use subject color so pills are varied; draft gets dashed border only
            pill.style.backgroundColor = (isDraft && sess.isPreview) ? '#c8d6da'
                : (isDraft && !_IS_LOCAL_MODE) ? '#8e9ca0'
                : getSubjectColor(sess.subjectcode);
            if (isDraft) pill.style.border = '2px dashed #2c3e50';

            // Mark as a merge-candidate pill when the active policy covers this subject.
            // Program View shows a single section's schedule, so we use the policy heuristic
            // rather than a count (Room View has the definitive merged-section count).
            const _pvMergeMode = _getMergeMode(sess.subjectcode || '');
            if (_pvMergeMode !== 'none') pill.classList.add('pill-merged');

            if (window.currentEditSession) {
                const editKey = `${window.currentEditSession.subjectcode}_${window.currentEditSession.daydesc}_${window.currentEditSession.starttimeid}`;
                const sessKey = `${sess.subjectcode}_${sess.daydesc}_${startIdx}`;
                if (editKey === sessKey) pill.classList.add('pill-editing');
            }

            const w     = (colWidth - 6) / (overlapCount || 1);
            const pillH = Math.max((endIdx - startIdx) * rowHeight - 6, 20); // min 20px to remain visible
            pill.style.width  = (w - 2) + 'px';
            pill.style.height = pillH + 'px';
            pill.style.left   = (leftOff + dayIdx * colWidth + overlapIndex * w + 3) + 'px';
            pill.style.top    = (topOff  + (startIdx - 1) * rowHeight + 3) + 'px';
            pill.style.cursor = 'pointer';
            pill.dataset.pillKey = `${sess.subjectcode}_${sess.daydesc}_${startIdx}`;

            const instrLast = (sess.instructor || 'TBA').split(',')[0].trim();
            const roomDisp  = sess.roomname || 'TBA';
            const subjName  = sess.subjectname || sess.subjectcode;
            const _pvMergeLabel = _pvMergeMode === 'flexible' ? ' · MERGED (multi-section)' : _pvMergeMode === 'strict' ? ' · MERGE-ELIGIBLE' : '';
            pill.title = `${sess.subjectcode} — ${subjName}\n${sess.instructor || 'TBA'}\n${roomDisp}${_pvMergeLabel}`;

            // Pill height thresholds for progressive info density
            const compact   = pillH < 42;   // code only
            const medium    = pillH < 70;   // code + room
            const _pvMergeBadge = (!compact && _pvMergeMode !== 'none')
                ? `<div class="pill-merge-badge"><i class="fas fa-layer-group"></i> ${_pvMergeMode === 'flexible' ? 'MERGED' : 'MERGE'}</div>`
                : '';
            const _pDbKey = `${sess.subjectcode}_${sess.daydesc}_${startIdx}`;
            const _pLabel = `${sess.subjectcode} — ${sess.daydesc} | ${roomDisp}`;
            const _pSd    = encodeURIComponent(JSON.stringify({ temp_id: sess.temp_id || null, versionid: sess.versionid || null, dbKey: _pDbKey, label: _pLabel, subjectcode: sess.subjectcode || null }));
            const dropBtn = `<button class="pill-drop-btn" onclick="_dropSession('${_pSd}', event)" title="Remove"><i class="fas fa-times"></i></button>`;
            pill.innerHTML = compact
                ? `${dropBtn}<div class="pill-subject" style="margin-top:6px;font-size:0.65rem;">${sess.subjectcode}</div>`
                : medium
                    ? `${dropBtn}
                       <div class="pill-subject" style="margin-top:6px;">${sess.subjectcode}</div>
                       ${_pvMergeBadge}
                       <div style="font-size:0.55rem;opacity:0.85;margin-top:2px;"><i class="fas fa-door-open" style="margin-right:2px;"></i>${roomDisp}</div>`
                    : `${dropBtn}
                       <div class="pill-subject" style="margin-top:6px;">${sess.subjectcode}</div>
                       <div style="font-size:0.6rem;margin-top:1px;opacity:0.9;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${instrLast}</div>
                       ${_pvMergeBadge}
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

    // Re-evaluate specialization warning whenever subject changes
    _checkFacultySpecWarning();

    // Set lab warning immediately from subject info — don't wait for DSS (prevents stale warnings)
    _updateLabWarning(labWarn, _subjInfo && _subjInfo.laboratoryhours > 0, LAB_CONSTRAINT_ENABLED);

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
                const _sectId1 = document.getElementById('sel_section')?.value || '';
                const url = `/api/manual/existing_sessions?subject_code=${encodeURIComponent(subjCode)}&ay_id=${encodeURIComponent(ay)}&semester=${encodeURIComponent(sem)}&program=${encodeURIComponent(prog)}&year_level=${encodeURIComponent(yl)}&scheduler_mode=${_sm()}&section_id=${encodeURIComponent(_sectId1)}`;
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

        console.log('[DSS] recommended faculty:', data.faculty.recommended.map(f => `${f.name} (src:${f._src||'?'}, hist:"${f._hist_name||''}", count:${f.count||0})`));

        // When no history exists for this subject and spec constraint is ON,
        // use specialization to populate RECOMMENDATIONS from the others pool.
        let facRec = data.faculty.recommended;
        let facOth = data.faculty.others;
        if (SPEC_CONSTRAINT_ENABLED && facRec.length === 0) {
            const _subj = (document.getElementById('sel_subj')?.value || '').trim();
            facRec = facOth.filter(f => {
                const c = _getSpecCompatibility(f.specialization || '', _subj);
                return c.level === 'exact' || c.level === 'related';
            });
            facOth = facOth.filter(f => {
                const c = _getSpecCompatibility(f.specialization || '', _subj);
                return c.level !== 'exact' && c.level !== 'related';
            });
            if (facRec.length > 0) console.log('[DSS] spec fallback: no history, using specialization for recommendations');
        }

        _dssRecommendedFacultyIds = new Set(facRec.map(f => f.id));
        buildDSSMenu('fac', [
            { cls: 'recommended', label: 'RECOMMENDATIONS', items: facRec.map(f => ({ value: f.id, text: f.name, typename: f.typename, max_units: f.max_units, assigned_units: f.assigned_units })) },
            { cls: 'others',      label: 'OTHERS',          items: facOth.map(f => ({ value: f.id, text: f.name, typename: f.typename, max_units: f.max_units, assigned_units: f.assigned_units })) }
        ]);

        const isPreferredType = (r) => data.is_lab ? r.type === 'Laboratory' : r.type !== 'Laboratory';

        // Historical rooms always come first (they were actually used for this subject)
        // then type-compatible non-historical rooms, then everything else
        const historicalRooms = data.rooms.recommended;
        const suitableRooms   = data.rooms.others.filter(isPreferredType);
        const remainingRooms  = data.rooms.others.filter(r => !isPreferredType(r));

        buildDSSMenu('room', [
            { cls: 'recommended', label: 'MOST USED', items: historicalRooms.map(r => ({ value: String(r.id), text: r.name, type: r.type })) },
            { cls: 'optional',    label: 'RECOMMENDATIONS',    items: suitableRooms.map(r => ({ value: String(r.id), text: r.name, type: r.type })) },
            { cls: 'others',      label: 'OTHERS',            items: remainingRooms.map(r => ({ value: String(r.id), text: r.name, type: r.type })) }
        ]);

        const labCon = data.lab_constraint_enabled !== undefined ? data.lab_constraint_enabled : LAB_CONSTRAINT_ENABLED;
        _updateLabWarning(labWarn, data.is_lab, labCon);

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
        // Use selectRoom so the grid renders for the clicked session's room and the
        // call goes through the full wrapper chain (context save, per-slice sync, etc.)
        if (typeof selectRoom === 'function') {
            await selectRoom(String(room.id), room.name);
        } else {
            document.getElementById('sel_room').value = String(room.id);
            document.getElementById('room_display_name').value = room.name;
            document.getElementById('room_trigger_text').textContent = room.name;
            document.getElementById('sum_room').innerText = room.name;
        }
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

    // ── HC4: Saturday restriction (when "All Weekends" + NSTP-only is configured) ──
    if (dayVal === 'Saturday' && WEEKEND_ENABLED && WEEKEND_DAY_SCOPE === 'all_weekends' && WEEKEND_SUBJECT_SCOPE === 'nstp_only') {
        const subjCode = (subjSel.value || '').toUpperCase();
        const isNstpOu = subjCode.startsWith('NSTP') || subjCode.startsWith('OU');
        if (!isNstpOu) {
            await showValidationModal('Saturday Restriction',
                `"${subjSel.value}" cannot be scheduled on Saturday. ` +
                `The current Weekend Restriction setting only allows NSTP/OU subjects on weekends. ` +
                `Please choose a weekday, or update the Weekend Restriction in Settings.`);
            return;
        }
    }

    // ── HC6: Day Pairing Alignment ─────────────────────────────────────────────
    // When day pairing is enabled, a subject may only span valid day pairs
    // (e.g. Mon-Thu, Tue-Fri, Wed-Sat). Collect all days already assigned to
    // this subject (DB-saved and pending) and block invalid combinations.
    if (DAY_PAIRING_ENABLED) {
        const subj      = subjSel.value;
        const otherDays = new Set();

        // Exclude the original day when editing an existing session
        const editOrigDay = window.currentEditSession
            ? (window.currentEditSession.daydesc || window.currentEditSession.day || null)
            : null;

        // Days from already-saved DB sessions for this subject
        for (const d of _existingDays) {
            if (d === dayVal)      continue;   // same as selected — fine
            if (d === editOrigDay) continue;   // original day being replaced
            otherDays.add(d);
        }

        // Days from in-memory pending sessions for the same subject/AY/sem
        for (const c of pendingManualSchedule) {
            if ((c.subject_code || '') !== subj) continue;
            if (c.ay !== ay || c.sem !== sem)   continue;
            if (window.currentEditSession && c.temp_id === window.currentEditSession.temp_id) continue;
            if (c.day === dayVal) continue;
            otherDays.add(c.day);
        }

        if (otherDays.size > 0) {
            const allDays    = [dayVal, ...otherDays];
            const sortedDays = [...allDays].sort();
            let pairingValid = false;

            if (allDays.length === 2) {
                // Exactly 2 days — check if they form a configured valid pair
                pairingValid = VALID_DAY_PAIRS.some(pair =>
                    [...pair].sort().join(',') === sortedDays.join(',')
                );
            }
            // 3+ days always invalid under day pairing

            if (!pairingValid) {
                const partner  = DAY_PAIR_MAP[dayVal] || 'its configured partner';
                const pairStr  = VALID_DAY_PAIRS.map(p => p.join(' & ')).join(', ');
                const existing = [...otherDays].join(', ');
                if (_sm() === 'local') {
                    const proceed = await showConfirmModal(
                        `[Local Override] "${subj}" is already on ${existing}. ` +
                        `"${dayVal}" should be paired with "${partner}" per Day Pairing Alignment. ` +
                        `Valid pairs: ${pairStr}.\n\nLocal Scheduler allows this override. Proceed?`,
                        'HC Override — Day Pairing Alignment'
                    );
                    if (!proceed) return;
                } else {
                    await showValidationModal(
                        'Day Pairing Alignment Violation',
                        `"${subj}" is already scheduled on ${existing}.\n\n` +
                        `With Day Pairing Alignment enabled, "${dayVal}" must be paired with "${partner}".\n\n` +
                        `Valid pairings are: ${pairStr}.\n\n` +
                        `Please select a day that forms a valid pair, or disable Day Pairing Alignment in Settings.`
                    );
                    return;
                }
            }
        }
    }

    // Lab room constraint: subjects with lab hours must use a Laboratory room
    if (LAB_CONSTRAINT_ENABLED && _subjInfo && _subjInfo.laboratoryhours > 0 && roomVal) {
        const chosenRoom = allRooms.find(r => String(r.id) === String(roomVal));
        if (chosenRoom && (chosenRoom.type || '').toLowerCase() !== 'laboratory') {
            await showValidationModal('Laboratory Room Required',
                `"${subjSel.value}" has ${_subjInfo.laboratoryhours} lab hour(s) and must be assigned to a Laboratory room. ` +
                `"${roomName}" is a ${chosenRoom.type || 'non-laboratory'} room.\n\n` +
                `Please select a Laboratory room, or disable the Laboratory Session Constraint in Settings.`);
            return;
        }
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
            if (_sm() === 'local') {
                // Local mode: soft constraint — warn but allow override
                if (nightCap === 0 || totalNight >= nightCap) {
                    const proceed = await showConfirmModal(
                        `[Local Override] ${facName} has a night class restriction ` +
                        `(allowed: ${nightCap}, current: ${totalNight}). ` +
                        `Local Scheduler allows this override. Proceed?`,
                        'HC Override — Night Class'
                    );
                    if (!proceed) return;
                }
            } else {
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
            if (_sm() === 'local') {
                // Local mode: warn, allow controlled override
                const proceed = await showConfirmModal(
                    `[Local Override] ${facName}'s load would reach ${totalAfter}/${maxLoad} units. ` +
                    `Local Scheduler allows this override. Proceed?`,
                    'HC Override — Maximum Load'
                );
                if (!proceed) return;
            } else {
                await showValidationModal('Maximum Load Exceeded',
                    `This assignment would bring ${facName}'s total load to ${totalAfter} unit${totalAfter !== 1 ? 's' : ''}, ` +
                    `exceeding the allowed maximum of ${maxLoad} units.`);
                return;
            }
        }
    }

    // ── Specialization check — skipped in local mode (intentional overrides allowed) ──
    if (_sm() !== 'local' && _facInfo) {
        const facSpec = (_facInfo.specializationname || '').trim();
        const compat  = _getSpecCompatibility(facSpec, subjSel.value);
        // 'exact' and 'related' are both acceptable; only true 'mismatch' blocks
        if (compat.level === 'mismatch') {
            const group   = _SUBJ_SPEC_GROUPS.find(g => g.prefixes.some(pfx => subjSel.value.toUpperCase().startsWith(pfx)));
            const primary = group ? group.primary : 'the required field';
            await showValidationModal('Specialization Mismatch',
                `${facName}'s specialization (${facSpec}) does not match what is required ` +
                `for "${subjSel.value}" (${primary}). ` +
                `Please select a faculty member with a compatible specialization.`);
            return;
        }
    }

    const currentSubjCode = (subjSel.value || '').toUpperCase();

    try {
        const resp = await fetch(`/api/get_room_schedule/${roomVal}?ay_id=${encodeURIComponent(ay)}&semester=${encodeURIComponent(sem)}&scheduler_mode=${_sm()}`);
        const dbSessions = await resp.json();

        for (const s of dbSessions) {
            if (s.daydesc !== dayVal) continue;
            if (window.currentEditSession &&
                s.subjectcode === window.currentEditSession.subjectcode &&
                s.daydesc    === window.currentEditSession.daydesc &&
                s.starttimeid === window.currentEditSession.starttimeid) continue;

            if (newStartIdx < s.endtimeid && newEndIdx > s.starttimeid) {
                const _mergeMode = _getMergeMode(currentSubjCode);
                const _sameSubj  = (s.subjectcode || '').toUpperCase() === currentSubjCode;
                const _sameFac   = String(s.employee_number) === String(facVal);
                // flexible (NSTP): same subject is enough — different faculty allowed
                // strict (non-NSTP): same subject AND same faculty required
                if (_mergeMode !== 'none' && _sameSubj &&
                    (_mergeMode === 'flexible' || _sameFac)) {
                    const mergeSection = (s.programcode && s.year_level)
                        ? `${s.programcode} Year ${s.year_level}`
                        : 'another section';
                    const _facNote = _mergeMode === 'flexible' && !_sameFac
                        ? `\nExisting Faculty: ${s.instructor || 'TBA'}\nNew Faculty: ${facName}`
                        : `\nFaculty: ${facName}`;
                    const proceed = await showConfirmModal(
                        `Merge Class Detected\n\n` +
                        `"${subjSel.options[subjSel.selectedIndex]?.text || currentSubjCode}" is already scheduled ` +
                        `in ${roomName} on ${dayVal} (${timeSlots[newStartIdx]} – ${timeSlots[newEndIdx - 1]}) ` +
                        `for ${mergeSection}.${_facNote}\n` +
                        `Room: ${roomName}\n\n` +
                        `Merging will allow multiple sections to share this class slot. Do you want to proceed?`,
                        'Merge Class'
                    );
                    if (!proceed) return;
                    break; // merge confirmed — stop checking further room sessions
                }
                const _sSection = (s.programcode && s.year_level) ? `${s.programcode}-${s.year_level}` : (s.instructor || 'TBA');
                await showConflictModal(
                    `Room Conflict: ${roomName} is already occupied by "${s.subjectname}" (${_sSection}) ` +
                    `on ${dayVal} from ${timeSlots[s.starttimeid - 1]} to ${timeSlots[s.endtimeid - 1]}. ` +
                    `Please select another room or time slot.`
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
            const _mergeMode = _getMergeMode(currentSubjCode);
            const _sameSubj  = (c.subject_code || '').toUpperCase() === currentSubjCode;
            const _sameFac   = String(c.faculty_id) === String(facVal);
            if (_mergeMode !== 'none' && _sameSubj &&
                (_mergeMode === 'flexible' || _sameFac)) {
                const _facNote = _mergeMode === 'flexible' && !_sameFac
                    ? `\nExisting Faculty: ${c.instructor || 'TBA'}\nNew Faculty: ${facName}`
                    : `\nFaculty: ${facName}`;
                const proceed = await showConfirmModal(
                    `Merge Class Detected\n\n` +
                    `"${c.subject_name}" is already queued for ${roomName} on ${dayVal} — ${c.start_time} to ${c.end_time}.${_facNote}\n` +
                    `Room: ${roomName}\n\n` +
                    `Merging will allow multiple sections to share this class slot. Do you want to proceed?`,
                    'Merge Class'
                );
                if (!proceed) return;
                break; // merge confirmed
            }
            const _cSection = (c.course && c.year_level) ? `${c.course}-${c.year_level}` : 'another section';
            await showConflictModal(
                `Room Conflict: ${roomName} is already occupied by "${c.subject_name}" (${_cSection}) ` +
                `on ${dayVal} from ${c.start_time} to ${c.end_time}. ` +
                `Please select another room or time slot.`
            );
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
                    // Skip when merge policy allows this subject and it's the same course —
                    // faculty teaching the same subject to merged sections is valid.
                    if (_isSubjectMergeEligible(currentSubjCode) &&
                        (s.subjectcode || '').toUpperCase() === currentSubjCode) {
                        continue;
                    }
                    await showConflictModal(
                        `Faculty Conflict: ${facName} is already assigned to "${s.subjectname}" ` +
                        `on ${dayVal} from ${timeSlots[s.starttimeid - 1]} to ${timeSlots[s.endtimeid - 1]}. ` +
                        `Please select a different time slot or faculty member.`
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
            // Skip when merge policy allows this subject and it's the same course —
            // faculty teaching the same subject to merged sections is valid.
            if (_isSubjectMergeEligible(currentSubjCode) &&
                (c.subject_code || '').toUpperCase() === currentSubjCode) {
                continue;
            }
            const _fSection = (c.course && c.year_level) ? `${c.course}-${c.year_level}` : 'another section';
            await showConflictModal(
                `Faculty Conflict: ${facName} is already assigned to "${c.subject_name}" (${_fSection}) ` +
                `on ${dayVal} from ${c.start_time} to ${c.end_time}. ` +
                `Please select a different time slot or faculty member.`
            );
            return;
        }
    }

    // ── Section conflict: a section (program + year level) cannot have overlapping classes ──
    if (prog && yl) {
        try {
            const sResp = await fetch(
                `/api/manual/section_schedule?program=${encodeURIComponent(prog)}&year_level=${encodeURIComponent(yl)}&ay_id=${encodeURIComponent(ay)}&semester=${encodeURIComponent(sem)}&scheduler_mode=${_sm()}`
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
                        `Section Conflict: ${prog}-${yl} already has "${s.subjectname || s.subjectcode}" scheduled ` +
                        `on ${dayVal} from ${timeSlots[s.starttimeid - 1]} to ${timeSlots[s.endtimeid - 1]}. ` +
                        `Please select a different time slot.`
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
                    `Section Conflict: ${prog}-${yl} already has "${c.subject_name}" scheduled ` +
                    `on ${dayVal} from ${c.start_time} to ${c.end_time}. ` +
                    `Please select a different time slot.`
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
            // Scope hide to THIS section's versionid so other merged sections stay visible
            const vid = window.currentEditSession.versionid;
            if (vid) {
                hiddenDbSchedules.add(`v:${vid}`);
            } else {
                hiddenDbSchedules.add(`${window.currentEditSession.subjectcode}_${window.currentEditSession.daydesc}_${window.currentEditSession.starttimeid}`);
            }
        }
    }

    const newClass = {
        temp_id:          "DRAFT_" + Date.now(),
        ay, sem,
        subject_code:     subjSel.value,
        subject_name:     subjSel.options[subjSel.selectedIndex].text,
        faculty_id:       facVal,
        instructor:       facName,
        room_id:          roomVal,
        room:             roomName,
        day:              dayVal,
        days:             dayVal.substring(0, 3).toUpperCase(),
        days_list:        [dayVal],
        start_time:       startVal,
        end_time:         endVal,
        time:             `${startVal} - ${endVal}`,
        course:           prog,
        year_level:       yl,
        sectionname:      document.getElementById('bc-sect-text')?.textContent?.trim() || '',
        units:            _subjInfo ? _subjInfo.total_hours : 3,
        total_subject_hrs: _subjInfo ? parseFloat(_subjInfo.total_hours || 0) : 0
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

    // Mask only this section's DB row — scope by versionid to keep merged sections visible
    if (sd.versionid) {
        hiddenDbSchedules.add(`v:${sd.versionid}`);
    } else if (sd.dbKey) {
        hiddenDbSchedules.add(sd.dbKey);
    }

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

    // Scope to the currently selected subject only — fromExisting sessions are
    // already in the DB (Published/Draft) and must not be re-published here.
    const currentSubj = document.getElementById('sel_subj').value;
    let contextDrafts = pendingManualSchedule.filter(c =>
        c.ay === ay && c.sem === sem && !c.isPreview && !c.fromExisting &&
        (!currentSubj || (c.subject_code || c.subjectcode) === currentSubj)
    );

    if (contextDrafts.length === 0) {
        // Try loading saved Draft sessions from DB (scoped to current subject)
        try {
            const dbResp = await fetch(`/api/schedule/draft_sessions?program=${encodeURIComponent(prog)}&year_level=${encodeURIComponent(yl)}&ay_id=${encodeURIComponent(ay)}&sem=${encodeURIComponent(sem)}`);
            const dbData = await dbResp.json();
            if (dbData.success && dbData.sessions && dbData.sessions.length > 0) {
                contextDrafts = currentSubj
                    ? dbData.sessions.filter(s => (s.subject_code || s.subjectcode) === currentSubj)
                    : dbData.sessions;
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

        // Snapshot before processing slices — roll back on any failure so no partial data lingers.
        const _pmBeforeLoop = [...pendingManualSchedule];

        for (const row of filledRows) {
            const day    = row.querySelector('.ts-day-sel').value;
            const start  = row.querySelector('.ts-start-hidden').value;
            const end    = row.querySelector('.ts-end-hidden').value;
            const roomId = row.querySelector('.ts-room-hidden').value;
            const roomName = (typeof allRooms !== 'undefined' && allRooms.find(r => String(r.id) === String(roomId))?.name)
                          || row.querySelector('.ts-room-field .ts-ss-input')?.value.trim() || '';

            // Read existingJson (if any) for DB conflict exclusion in confirmAndPlace.
            let _sessData = null;
            try { if (row.dataset.existingJson) _sessData = JSON.parse(row.dataset.existingJson); } catch(e) {}

            // Remove any matching pending entry (e.g. fromExisting loaded from DB) before
            // confirmAndPlace so it isn't double-counted in scheduledAlready.
            const _sc   = document.getElementById('sel_subj').value;
            const _ayV  = document.getElementById('sel_ay').value;
            const _semV = document.getElementById('sel_sem').value;
            const _dup  = pendingManualSchedule.find(c =>
                (c.subject_code || c.subjectcode) === _sc && c.ay === _ayV && c.sem === _semV &&
                (c.day || c.daydesc) === day && c.start_time === start && c.end_time === end
            );
            if (_dup) pendingManualSchedule = pendingManualSchedule.filter(c => c.temp_id !== _dup.temp_id);

            document.getElementById('sel_day').value = day;
            if (typeof _injectTimeOpt === 'function') { _injectTimeOpt('sel_start_time', start); _injectTimeOpt('sel_end_time', end); }
            document.getElementById('sel_room').value = roomId;
            document.getElementById('room_display_name').value = roomName;
            if (typeof allRooms !== 'undefined') _roomInfo = allRooms.find(r => String(r.id) === String(roomId)) || null;
            // Use sessData so confirmAndPlace can exclude this DB session from conflict checks.
            // For newly added rows (no existingJson), null is correct — they're not in the DB yet.
            window.currentEditSession = _sessData;

            const prevLen = pendingManualSchedule.length;
            await confirmAndPlace();
            if (pendingManualSchedule.length <= prevLen) {
                // Validation failed — roll back everything added in this loop so a
                // partial failure cannot be silently published on the next attempt.
                pendingManualSchedule = _pmBeforeLoop;
                return;
            }
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
                    const _sectId2 = document.getElementById('sel_section')?.value || '';
                    const exResp2 = await fetch(`/api/manual/existing_sessions?subject_code=${encodeURIComponent(currentSubj2)}&program=${encodeURIComponent(prog)}&year_level=${encodeURIComponent(yl)}&ay_id=${encodeURIComponent(ay)}&semester=${encodeURIComponent(sem)}&scheduler_mode=${_sm()}&section_id=${encodeURIComponent(_sectId2)}`);
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
    return idx >= 0 ? idx + 1 : 0;
}

async function renderGrid(roomId, ayFilter = '', semFilter = '') {
    if (currentMode === 'program') return;
    const wrapper = document.getElementById('gridWrapper');
    const table = document.getElementById('mainTimetable');
    wrapper.querySelectorAll('.schedule-pill').forEach(p => p.remove());

    if (!ayFilter || !semFilter || !roomId) return;

    try {
        const url = `/api/get_room_schedule/${roomId}?ay_id=${ayFilter}&semester=${semFilter}&scheduler_mode=${_sm()}&_t=${new Date().getTime()}`;
        const resp = await fetch(url);
        let sessions = await resp.json();

        sessions = sessions.filter(s => {
            // Versionid-scoped hide (only hides this specific section's row)
            if (s.versionid && hiddenDbSchedules.has(`v:${s.versionid}`)) return false;
            // Legacy slot-based hide (fallback for sessions without versionid)
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
            programcode:  c.course || '',         // expose for badge rendering
            sectionname:  c.sectionname || '',    // expose for badge rendering
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

        // Group sessions by slot — merged classes (same subject+time+room) share one pill
        const slotMap = new Map();
        sessions.forEach(s => {
            const key = `${s.subjectcode}|${s.daydesc}|${s.starttimeid}`;
            if (!slotMap.has(key)) slotMap.set(key, []);
            slotMap.get(key).push(s);
        });
        // Build representative list: one entry per slot, carrying all merged sections
        const uniq = Array.from(slotMap.values()).map(group => {
            const rep = { ...group[0] };
            rep._mergedSections = group.map(s => ({
                sectionname: s.sectionname || '',
                programcode: s.programcode || '',
                year_level:  s.year_level  || '',
                status:      s.status      || 'Draft',
            }));
            return rep;
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
                // Skip sessions with missing or invalid time indices to prevent misplaced pills
                if (!start || !end || end <= start || start < 1 || end > timeSlots.length + 1) return;

                let overlapCount = 0, overlapIndex = 0;
                daySessions.forEach((other, oIdx) => {
                    const oS = other.starttimeid, oE = other.endtimeid;
                    if (!oS || !oE || oE <= oS) return;
                    if (start < oE && end > oS) {
                        overlapCount++;
                        if (idx > oIdx) overlapIndex++;
                    }
                });

                const pill = document.createElement('div');
                pill.className = 'schedule-pill';

                const isDraft = !sess.status || String(sess.status).toLowerCase() !== 'published';
                // Local mode: always use subject color so pills are varied; draft gets dashed border only
                pill.style.backgroundColor = (isDraft && sess.isPreview) ? '#c8d6da'
                    : (isDraft && !_IS_LOCAL_MODE) ? '#8e9ca0'
                    : getSubjectColor(sess.subjectcode);
                if (isDraft) pill.style.border = "2px dashed #2c3e50";

                const _mergedSects = sess._mergedSections || [];
                const isMerged = _mergedSects.length > 1;
                if (isMerged) pill.classList.add('pill-merged');

                if (window.currentEditSession) {
                    const editKey = `${window.currentEditSession.subjectcode}_${window.currentEditSession.daydesc}_${window.currentEditSession.starttimeid}`;
                    const sessKey = `${sess.subjectcode}_${sess.daydesc}_${sess.starttimeid}`;
                    if (editKey === sessKey) pill.classList.add('pill-editing');
                }

                const w     = (colWidth - 6) / (overlapCount || 1);
                const pillH = Math.max((end - start) * rowHeight - 6, 20); // min 20px to remain visible
                pill.style.width  = (w - 2) + 'px';
                pill.style.height = pillH + 'px';
                pill.style.left   = (leftOffset + (dayIdx * colWidth) + (overlapIndex * w) + 3) + 'px';
                pill.style.top    = (topOffset + ((start - 1) * rowHeight) + 3) + 'px';
                pill.style.cursor = 'pointer';
                pill.dataset.pillKey = `${sess.subjectcode}_${sess.daydesc}_${sess.starttimeid}`;

                const instrLast = (sess.instructor || 'TBA').split(',')[0].trim();

                // Tooltip lists all sections
                const _sectLines = _mergedSects.map(ms => {
                    const lbl = ms.sectionname || `${ms.programcode} Yr${ms.year_level}`;
                    return `${lbl} (${ms.status})`;
                }).join('\n');
                pill.title = `${sess.subjectcode}\n${sess.subjectname || ''}\n${instrLast}${isMerged ? `\n\n⟨MERGED · ${_mergedSects.length} sections⟩` : ''}${_sectLines ? '\n' + _sectLines : ''}`;

                const _dbKey = `${sess.subjectcode}_${sess.daydesc}_${sess.starttimeid}`;
                const _label = `${sess.subjectcode} — ${sess.daydesc} ${sess.start_fmt || ''} – ${sess.end_fmt || ''} in ${sess.roomname || 'TBA'}`;
                const _sd    = encodeURIComponent(JSON.stringify({ temp_id: sess.temp_id || null, versionid: sess.versionid || null, dbKey: _dbKey, label: _label, subjectcode: sess.subjectcode || null }));
                const dropBtn = `<button class="pill-drop-btn" onclick="_dropSession('${_sd}', event)" title="Remove"><i class="fas fa-times"></i></button>`;

                // Section badge(s) — same colored style for both single and merged sections
                const _sectHtml = _mergedSects.map(ms => {
                    const lbl = ms.sectionname || (ms.programcode ? `${ms.programcode}-${ms.year_level}` : '');
                    if (!lbl) return '';
                    const isPub = (ms.status || '').toLowerCase() === 'published';
                    const clr  = isPub ? '#a5d6a7' : '#ffe082';
                    return `<div style="font-size:0.52rem;background:${clr};color:#222;border-radius:2px;padding:1px 3px;margin-top:2px;font-weight:700;">${lbl} · ${isPub ? 'PUB' : 'DRAFT'}</div>`;
                }).join('');

                const mergeBadge = isMerged
                    ? `<div class="pill-merge-badge"><i class="fas fa-layer-group"></i> MERGED &times;${_mergedSects.length}</div>`
                    : '';

                pill.innerHTML = `
                    ${dropBtn}
                    <div class="pill-subject" style="margin-top:8px;">${sess.subjectcode}</div>
                    <div style="font-size:0.6rem;">${instrLast}</div>
                    ${mergeBadge}
                    ${_sectHtml}`;

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

   if (prog && year) {
            try {
                // --- FIX: Ipadala ang ay_id at year_level para makuha ang tamang Cohort Curriculum ---
                const resp = await fetch(`/api/get_curriculum?program=${encodeURIComponent(prog)}&ay_id=${encodeURIComponent(ay)}&year_level=${encodeURIComponent(year)}`);
                const data = await resp.json();

            if (data.success) {
                currDisplay.innerText = data.curriculum_code;
                currHidden.value = data.curriculum_id;
                const _cyHid = document.getElementById('curr_year_hidden');
                if (_cyHid) _cyHid.value = data.curriculum_year || '';

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
                const _cyHidNo = document.getElementById('curr_year_hidden');
                if (_cyHidNo) _cyHidNo.value = '';
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

    // Re-evaluate lab warning now that a room is selected
    const labWarnEl = document.getElementById('room_dss_warning');
    const isLabSubj = _subjInfo && _subjInfo.laboratoryhours > 0;
    if (labWarnEl && isLabSubj && LAB_CONSTRAINT_ENABLED) {
        const roomType = (room && room.type) ? room.type.toLowerCase() : '';
        const isLabRoom = roomType === 'laboratory';
        const textEl = document.getElementById('room_dss_warning_text');
        if (!isLabRoom) {
            // Count how many lab hours are already covered by pending sessions in lab rooms
            const ay  = document.getElementById('sel_ay').value;
            const sem = document.getElementById('sel_sem').value;
            const subjCode = document.getElementById('sel_subj').value;
            let coveredLabHrs = 0;
            for (const c of pendingManualSchedule) {
                if ((c.subject_code || c.subjectcode) !== subjCode) continue;
                if (c.ay !== ay || c.sem !== sem) continue;
                if (window.currentEditSession && c.temp_id === window.currentEditSession.temp_id) continue;
                const cRoom = allRooms.find(r => String(r.id) === String(c.room_id));
                if (cRoom && (cRoom.type || '').toLowerCase() === 'laboratory') {
                    const si = timeSlots.indexOf(c.start_time), ei = timeSlots.indexOf(c.end_time);
                    if (si >= 0 && ei > si) coveredLabHrs += (ei - si) * 0.5;
                }
            }
            const needed = _subjInfo.laboratoryhours - coveredLabHrs;
            labWarnEl.style.color = '#c0392b';
            labWarnEl.style.display = 'block';
            if (textEl) {
                if (needed > 0) {
                    textEl.textContent = `"${roomName}" is not a Laboratory room. ${needed}h of lab hours still need a Laboratory room.`;
                } else {
                    textEl.textContent = `"${roomName}" is not a Laboratory room. Lab hours are already covered by another session.`;
                    labWarnEl.style.color = '#c8860a';
                }
            }
        } else {
            _updateLabWarning(labWarnEl, isLabSubj, LAB_CONSTRAINT_ENABLED);
        }
    } else if (labWarnEl) {
        _updateLabWarning(labWarnEl, isLabSubj, LAB_CONSTRAINT_ENABLED);
    }

    document.querySelectorAll('.room-pill').forEach(el => {
        el.classList.remove('active-room');
        if (el.textContent === roomName) el.classList.add('active-room');
    });

    await renderGrid(roomId, formAyFilter(), formSemFilter());
}

// Official scheduler: vivid saturated palette (white text, clearly brighter than local pastels)
const colorPalette = [
    '#1976D2',  // vivid blue
    '#388E3C',  // vivid green
    '#7B1FA2',  // vivid purple
    '#C62828',  // vivid red
    '#E65100',  // vivid deep orange
    '#F9A825',  // vivid amber
    '#00838F',  // vivid teal
    '#AD1457',  // vivid pink
];

// Local scheduler: pastel palette matching the Room Schedule view
const localColorPalette = [
    '#A9C2F0',  // light blue
    '#FDE08B',  // light yellow
    '#F9A17A',  // light peach
    '#F07C7C',  // light coral
    '#B5E5CF',  // light mint
    '#D2A6E8',  // light lavender
];
function getSubjectColor(code) {
    const str = (code || '').toUpperCase();
    let h = 0;
    for (let i = 0; i < str.length; i++) h = str.charCodeAt(i) + ((h << 5) - h);
    // Local scheduler: hash into FRS pastel palette (same logic as Room Schedule view)
    if (_IS_LOCAL_MODE) return localColorPalette[Math.abs(h) % localColorPalette.length];
    // Official scheduler: hash into saturated palette
    return colorPalette[Math.abs(h) % colorPalette.length];
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

// Shows violations one at a time with prev/next navigation.
async function _showFirstViolation(violations) {
    if (!violations || !violations.length) return;
    const total = violations.length;
    let current = 0;

    function _renderViol(idx) {
        const v      = violations[idx];
        const rule   = v.rule    || '';
        const subj   = v.subject || '';
        const detail = v.detail  || '';

        const counter = total > 1
            ? `<p style="font-size:12px;color:#888;margin:0 0 10px;">Issue ${idx + 1} of ${total} — fix all issues to save successfully.</p>`
            : '';

        const badge = rule
            ? `<span style="display:inline-block;background:#7b1a1a;color:#fff;font-size:10px;font-weight:700;` +
              `letter-spacing:0.5px;padding:2px 7px;border-radius:4px;margin-bottom:6px;">${rule}</span> `
            : '';

        const subjLine = subj
            ? `<span style="font-weight:600;font-size:13px;color:#222;">${subj}</span><br>`
            : '';

        const prevStyle = idx === 0
            ? 'background:#ddd;color:#999;cursor:default;'
            : 'background:#7b1a1a;color:#fff;cursor:pointer;';
        const nextStyle = idx === total - 1
            ? 'background:#ddd;color:#999;cursor:default;'
            : 'background:#7b1a1a;color:#fff;cursor:pointer;';

        const nav = total > 1 ? `
            <div style="display:flex;gap:8px;margin-top:12px;">
                <button ${idx === 0 ? 'disabled' : ''} onclick="window._violNavPrev()"
                    style="flex:1;padding:6px 10px;border:none;border-radius:6px;font-weight:700;font-size:12px;${prevStyle}">
                    ← Previous
                </button>
                <button ${idx === total - 1 ? 'disabled' : ''} onclick="window._violNavNext()"
                    style="flex:1;padding:6px 10px;border:none;border-radius:6px;font-weight:700;font-size:12px;${nextStyle}">
                    Next →
                </button>
            </div>` : '';

        document.getElementById('validationModalMessage').innerHTML =
            counter +
            `<div style="background:#fafafa;border:1px solid #e0e0e0;border-radius:8px;padding:12px 14px;">` +
            badge + subjLine +
            `<span style="font-size:13px;color:#333;line-height:1.5;">${detail}</span>` +
            `</div>` + nav;
    }

    window._violNavPrev = () => { if (current > 0) { current--; _renderViol(current); } };
    window._violNavNext = () => { if (current < total - 1) { current++; _renderViol(current); } };

    const p = showValidationModal('Cannot Save', '');
    _renderViol(0);
    await p;
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
