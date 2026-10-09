// Manual Editor — Merge Group UX (HC16 group model; P5, reworked for P7).
//
// P7 "allowed" model: sections MAY merge. A slot where another section has the same subject
// with the same faculty (or TBA) stays available in this section's day/time/room pickers;
// placing a slice exactly there is confirmed by the merge notice on Save Draft / Publish
// (server-side detection) and recorded in Settings. Merged subjects are never locked.
//
// Active only when the server renders data-merge-model="groups" (internal switch, never an
// admin option) and only in the Official editor. Under the legacy model nothing here runs
// and the legacy merge code (_getMergeMode / MERGE_SCOPE / NSTP flexible rule / "Merge Class
// Detected") behaves exactly as before.
//
// Boundaries: everything shown comes from /api/manual/merge_context — this file never reads
// the Merge Group tables, never decides merged-event identity, and never infers another
// section's faculty. It only compares the opaque `merge_event` keys the server stamps on
// occupancy rows and on this section's slices. Server-side HC16 (Save Draft / Publish)
// stays authoritative; nothing here writes outside the current section's editor state.

let _mgCtx = null;            // last /api/manual/merge_context response for the open subject
let _mgCtxSeq = 0;            // drops stale responses (subject/section switched meanwhile)
let _mgRefreshTimer = null;
window._mgApplying = false;          // kept for callers (no editor action applies a group slot any more)
window._mgPlacingEventKey = '';      // event key of the slice confirmAndPlace is placing

function _mgActive() {
    return typeof MERGE_MODEL !== 'undefined' && MERGE_MODEL === 'groups' &&
        !(typeof _IS_LOCAL_MODE !== 'undefined' && _IS_LOCAL_MODE);
}

function _mgEsc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _mgVal(id) { return document.getElementById(id)?.value || ''; }

function _mgCode() {
    return String((typeof _currentSubjectCode !== 'undefined' && _currentSubjectCode) || _mgVal('sel_subj') || '');
}

function _mgRowId(row) { return parseInt(String(row.id).replace('ts-row-', ''), 10); }

function _mgRowEntry(row) {
    if (!row.dataset.existingJson || typeof pendingManualSchedule === 'undefined') return null;
    let tid = null;
    try { tid = JSON.parse(row.dataset.existingJson)._localTempId; } catch (e) { return null; }
    return pendingManualSchedule.find(c => c.temp_id === tid) || null;
}

// One editor slice as the server classifies it (current, possibly unsaved, values).
function _mgRowPayload(row) {
    const entry = _mgRowEntry(row);
    return {
        key:          String(_mgRowId(row)),
        subject_code: _mgCode(),
        day:          row.querySelector('.ts-day-sel')?.value      || '',
        start_time:   row.querySelector('.ts-start-hidden')?.value || '',
        end_time:     row.querySelector('.ts-end-hidden')?.value   || '',
        room_id:      row.querySelector('.ts-room-hidden')?.value  || '',
        faculty_id:   (entry && entry.faculty_id) || _mgVal('sel_faculty') || '',
    };
}

// The open subject's server entry (null: not a Merge Group member in this section).
function _mgCurrent() {
    if (!_mgActive() || !_mgCtx || !_mgCtx.current || _mgCtx.error) return null;
    if (_mgCtx._code !== _mgCode() || _mgCtx._section !== _mgVal('sel_section')) return null;
    return _mgCtx.current;
}

// FAIL-CLOSED: the open subject's Merge Group context could not be loaded. Whether it is
// a merged class is then unknown, so its slices, faculty and Save/Publish stay
// disabled until a Retry succeeds.
function _mgContextBroken() {
    return !!(_mgActive() && _mgCtx && _mgCtx.error && _mgCtx._code === _mgCode() &&
              _mgCtx._section === _mgVal('sel_section'));
}

// P7 "allowed" model: a merged subject is never locked — its slices stay editable (moving
// one off the merged slot simply makes it a separate class again). Only a BROKEN context
// (unknown whether the class is merged) keeps editing and saving closed until a Retry.
function _mgControlled() {
    return _mgContextBroken();
}

function _mgRowLocked(id) {
    if (window._mgApplying || !_mgActive()) return false;
    const row = document.getElementById(`ts-row-${id}`);
    return !!(row && row.dataset.mergeControlled === '1');
}

function _mgFacultyLocked() {
    return _mgContextBroken();
}

function _mgPlacingEvent() {
    return _mgActive() ? (window._mgPlacingEventKey || '') : '';
}

// Same merged class of ANOTHER member section: both carry the same server event key.
// Outsiders (no key / another key) and this section's own rows never match.
function _mgSameEvent(other, myKey, mySection) {
    return !!(_mgActive() && myKey && other && other.merge_event && other.merge_event === myKey &&
              other.section_id != null && String(other.section_id) !== String(mySection));
}

// ── P7 merge candidates ("allowed" model) ─────────────────────────────────────
// An occupied slot of ANOTHER section stays available to this subject when it is the
// same subject taught by the same faculty (or TBA on either side): placing this slice
// exactly there merges the two classes. Nothing is merged here — Save Draft / Publish
// detect the exact match on the server and show the merge notice before recording it.
// A different subject, or a different faculty, still blocks as before.
function _mgTba(v) {
    const t = String(v == null ? '' : v).trim();
    return !t || t.toUpperCase() === 'TBA';
}

// `mine`: {section, subject, faculty} of the slice being placed (defaults: open subject).
function _mgMergeCandidate(other, mine) {
    if (!_mgActive() || !other) return false;
    const me = mine || { section: _mgVal('sel_section'), subject: _mgCode(), faculty: _mgVal('sel_faculty') };
    const sid = other.section_id ?? other.sectionid;
    if (sid == null || String(sid) === String(me.section)) return false;
    const code = String(other.subjectcode || other.subject_code || '').trim().toUpperCase();
    if (!code || code !== String(me.subject || '').trim().toUpperCase()) return false;
    const of = other.employee_number ?? other.faculty_id;
    return _mgTba(of) || _mgTba(me.faculty) || String(of).trim() === String(me.faculty).trim();
}

// Does occupied range `r` block a slice [s, e)? A merge-candidate range only allows the
// EXACT same start and end (anything else overlapping it is an ordinary conflict).
function _mgRangeBlocks(r, s, e) {
    if (!(s < r.endIdx && e > r.startIdx)) return false;
    return !(r.merge && s === r.startIdx && e === r.endIdx);
}

let _mgPending = 0;   // merge_context requests in flight

async function _mgRefresh() {
    if (!_mgActive()) { _mgCtx = null; return null; }
    const code = _mgCode(), sect = _mgVal('sel_section'), ay = _mgVal('sel_ay'), sem = _mgVal('sel_sem');
    const seq = ++_mgCtxSeq;
    if (!code || !sect || !ay || !sem) { _mgCtx = null; _mgClearUi(); return null; }
    const rows = Array.from(document.querySelectorAll('.ts-row')).map(_mgRowPayload);
    let data = null;
    _mgPending++;
    try {
        const r = await fetch('/api/manual/merge_context', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ay_id: ay, semester: sem, section_id: sect, subject_code: code, rows }),
        });
        data = await r.json();
        if (r.ok === false && data && data.success !== false) data = { success: false, error: `HTTP ${r.status}` };
    } catch (e) {
        data = null;
    } finally {
        _mgPending--;
    }
    if (seq !== _mgCtxSeq) return _mgCtx;                       // superseded meanwhile
    if (!data || data.success === false || data.model !== 'groups') {
        _mgCtx = { _code: code, _section: sect,
                   error: (data && data.error) || 'Merge Group details could not be loaded.' };
        _mgRender();
        return null;
    }
    _mgCtx = Object.assign(data, { _code: code, _section: sect });
    _mgRender();
    return _mgCtx;
}

async function _mgRetry() {
    return _mgRefresh();
}

// Save Draft / Publish / slice save guard (group model only). Blocks while the open
// subject's context is broken or still loading — never under the legacy model.
async function _mgGuardSave(action) {
    if (!_mgActive()) return false;
    if (_mgContextBroken()) {
        await showValidationModal('Merge Group Details Unavailable',
            `Cannot ${action}: the Merge Group details for ${_mgCode()} could not be loaded, so it is not ` +
            `known whether this class is merged. Use Retry above the time slices, then try again.`);
        return true;
    }
    if (_mgPending > 0) {
        await showValidationModal('Merge Group Details Loading',
            `Cannot ${action} yet: the Merge Group details for ${_mgCode()} are still loading. Try again in a moment.`);
        return true;
    }
    return false;
}

function _mgScheduleRefresh() {
    if (!_mgActive()) return;
    clearTimeout(_mgRefreshTimer);
    _mgRefreshTimer = setTimeout(() => { _mgRefresh(); }, 200);
}

// ── View model (pure: what to show for the current context) ───────────────────
function _mgList(arr) {
    const a = (arr || []).filter(Boolean);
    return a.length ? a.join(', ') : '';
}

function _mgViewModel(ctx) {
    const s = ctx && ctx.current;
    if (!s) return ctx && ctx.error ? { error: ctx.error } : null;
    if (s.advisory_only) {
        return { advisory: true, badge: `MERGE GROUP · ${s.group_name}`, status: 'advisory',
                 banner: `Merge Group "${s.group_name}" is not applied: ${s.reason || 'invalid configuration'}. ` +
                         `This subject is scheduled normally until it is fixed in Settings.` };
    }
    const sect = _mgVal('sel_section');
    const mine = document.getElementById('bc-sect-text')?.textContent?.trim() || '';
    const others = (s.sections || []).filter(l => l && l !== mine);
    const meetings = s.meetings || [];
    const tooltip = [
        ['Merge Group', s.group_name],
        ['Subject', s.subject_name ? `${s.subject_code} — ${s.subject_name}` : s.subject_code],
        ['Sections', (s.sections || []).join(', ')],
        ['Merged slots', meetings.map(m => m.text + (m.sections.length ? ` (${m.sections.join(', ')})` : '')).join('; ')
            || 'None yet'],
    ];
    if (s.status === 'merged') {
        const rows = Object.values(ctx.rows || {}).filter(r => r.mergegroupid === s.mergegroupid && r.sync === 'in_sync');
        const withWho = _mgList([...new Set(rows.flatMap(r => r.merged_with || []))]) || _mgList(others);
        return { status: 'merged', tooltip,
                 badge: `MERGED${withWho ? ' · with ' + withWho : ''}`,
                 banner: `This class is merged${withWho ? ' with ' + withWho : ''} (same subject, faculty, day, time ` +
                         `and room). Moving a merged slice makes it a separate class again.` };
    }
    return { status: 'not_merged', tooltip,
             badge: `CAN MERGE${others.length ? ' · with ' + _mgList(others) : ''}`,
             banner: `Allowed to merge with ${_mgList(others) || 'other sections'}. Pick the same faculty, then the ` +
                     `other section's day, time and room stay available — Save Draft / Publish will ask before merging.` +
                     (meetings.length ? ` Merged slot${meetings.length === 1 ? '' : 's'}: ` +
                         meetings.map(m => m.text).join('; ') + '.' : '') };
}

function _mgRowNote(meta) {
    if (!meta || meta.sync !== 'in_sync') return '';
    let html = `<div class="mg-note-line"><i class="fas fa-object-group"></i> MERGED` +
               (meta.merged_with && meta.merged_with.length ? ` with ${_mgEsc(meta.merged_with.join(', '))}` : '') +
               `</div>`;
    if (meta.faculty_issue) html += `<div class="mg-note-sub mg-note-err">Faculty: ${_mgEsc(meta.faculty_issue)}</div>`;
    else if (meta.faculty_advisory) html += `<div class="mg-note-sub">${_mgEsc(meta.faculty_advisory)}</div>`;
    return html;
}

// ── Rendering ─────────────────────────────────────────────────────────────────
const _MG_FIELD_IDS = ['tsd-txt-', 'tsst-txt-', 'tset-txt-', 'tsr-txt-', 'ts-day-'];

function _mgSetRowLocked(row, locked) {
    const id = _mgRowId(row);
    _MG_FIELD_IDS.forEach(p => {
        const el = document.getElementById(p + id);
        if (el) el.disabled = !!locked;
    });
    row.classList.toggle('ts-merge-locked', !!locked);
    if (locked) row.dataset.mergeControlled = '1';
    else delete row.dataset.mergeControlled;
}

function _mgShow(id, html) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = html || '';
    el.hidden = !html;
    if (el.style) el.style.display = html ? '' : 'none';
}

function _mgClearUi() {
    ['mg-badge', 'mg-banner', 'mg-fac-note'].forEach(id => _mgShow(id, ''));
    document.querySelectorAll('.ts-row').forEach(row => {
        if (row.dataset.mergeControlled === '1') _mgSetRowLocked(row, false);
        delete row.dataset.mergeEvent;
        delete row.dataset.mergeSync;
        _mgShow(`ts-mg-${_mgRowId(row)}`, '');
    });
    const add = document.getElementById('btnAddTimeSlot');
    if (add && add.dataset.mgHidden) { add.style.display = ''; delete add.dataset.mgHidden; }
    ['fac_wrapper', 'fac-stats-panel'].forEach(id => document.getElementById(id)?.classList.remove('mg-fac-locked'));
}

function _mgRender() {
    _mgClearUi();
    if (!_mgActive() || !_mgCtx || _mgCtx._code !== _mgCode()) return;
    const vm = _mgViewModel(_mgCtx);
    if (!vm) return;
    if (vm.error) {
        // Fail closed: unknown whether this subject is merged — nothing is editable or
        // savable until a Retry loads its Merge Group details.
        document.querySelectorAll('.ts-row').forEach(row => {
            _mgSetRowLocked(row, true);
            _mgShow(`ts-mg-${_mgRowId(row)}`, `<div class="mg-note-line mg-note-warn"><i class="fas fa-exclamation-circle"></i> ` +
                `Merge Group details unavailable — editing disabled</div>`);
        });
        const addBtn = document.getElementById('btnAddTimeSlot');
        if (addBtn) { addBtn.style.display = 'none'; addBtn.dataset.mgHidden = '1'; }
        ['fac_wrapper', 'fac-stats-panel'].forEach(id => document.getElementById(id)?.classList.add('mg-fac-locked'));
        _mgShow('mg-banner', `<div class="mg-banner mg-banner-warn"><div class="mg-banner-head">` +
            `<i class="fas fa-exclamation-circle"></i> MERGE GROUP DETAILS UNAVAILABLE</div>` +
            `<div class="mg-banner-body">${_mgEsc(vm.error)} Editing, Save Draft and Publish are disabled ` +
            `for this subject until the details load.</div>` +
            `<button type="button" class="mg-reset-btn mg-retry-btn" onclick="_mgRetry()">` +
            `<i class="fas fa-redo"></i> RETRY</button></div>`);
        return;
    }
    const tip = (vm.tooltip || []).map(([k, v]) =>
        `<div class="mg-tip-row"><span>${_mgEsc(k)}</span><b>${_mgEsc(v)}</b></div>`).join('');
    _mgShow('mg-badge',
        `<span class="mg-badge${vm.advisory ? ' mg-badge-muted' : ''}" tabindex="0">` +
        `<i class="fas fa-object-group"></i> ${_mgEsc(vm.badge)}` +
        (tip ? `<span class="mg-tip" role="tooltip">${tip}</span>` : '') + `</span>`);
    // "Can merge" needs no banner under Time Allotment — the blue CAN MERGE badge (with
    // its tooltip) says it, and Save Draft / Publish still ask before merging.
    if (vm.status !== 'not_merged') {
        _mgShow('mg-banner', `<div class="mg-banner"><i class="fas fa-info-circle"></i> ${_mgEsc(vm.banner)}</div>`);
    }
    if (vm.advisory) return;

    // Slices stay editable; merged ones carry their server event key + a MERGED note.
    document.querySelectorAll('.ts-row').forEach(row => {
        const id = _mgRowId(row);
        const meta = (_mgCtx.rows || {})[String(id)];
        if (!meta) return;
        if (meta.event_key) row.dataset.mergeEvent = meta.event_key;
        row.dataset.mergeSync = meta.sync;
        _mgShow(`ts-mg-${id}`, _mgRowNote(meta));
    });
}

// ── Guards used by the editor's edit paths (closed only while details are broken) ──
async function _mgBlockFreeSlot(action) {
    if (!_mgContextBroken() || window._mgApplying) return false;
    await showValidationModal('Merge Group Details Unavailable',
        `A slice cannot be ${action} while the Merge Group details for ${_mgCode()} are unavailable. Use Retry.`);
    return true;
}

async function _mgFacultyGuard() {
    if (!_mgContextBroken()) return false;
    await showValidationModal('Merge Group Details Unavailable',
        `The faculty cannot be changed while the Merge Group details for ${_mgCode()} are unavailable. Use Retry.`);
    return true;
}

// After a version restore: reload the open subject's slices from the server (as the
// Publish path does) so the merge notes reflect what was restored. Read-only fetch.
async function _mgReloadOpenSubject() {
    const code = _mgCode(), sect = _mgVal('sel_section'), ay = _mgVal('sel_ay'), sem = _mgVal('sel_sem');
    if (!code || !ay || !sem) return;
    let data = null;
    try {
        const q = new URLSearchParams({ subject_code: code, program: _mgVal('sel_prog'), year_level: _mgVal('sel_year'),
                                        ay_id: ay, semester: sem, scheduler_mode: 'official', section_id: sect });
        data = await (await fetch(`/api/manual/existing_sessions?${q.toString()}`)).json();
    } catch (e) { data = null; }
    const box = document.getElementById('time-slots-container');
    if (box) box.innerHTML = '';
    if (typeof _dirtySliceIds !== 'undefined') _dirtySliceIds.clear();
    if (data && data.success && (data.sessions || []).length && typeof _loadExistingSessionsIntoSlices === 'function') {
        await _loadExistingSessionsIntoSlices(data.sessions);   // ends with _mgRefresh()
    } else {
        await _mgRefresh();
    }
}

// ── Conflict panel: HC16 "Merged-Class Consistency" details ───────────────────
function _mgViolationDetails(v) {
    // Group-model findings only (they carry an HC16_* code); legacy HC16 text is untouched.
    if (!v || v.rule !== 'HC16' || !/^HC16_/.test(String(v.code || ''))) return null;
    const fac = v.faculty || null;
    let facIssue = '';
    if (fac) {
        facIssue = fac.actual
            ? `Has ${fac.actual}; requires ${fac.expected || 'the same faculty as the other sections'}` +
              (fac.other_section ? ` (${fac.other_section})` : '')
            : `TBA — the merged class needs ${fac.expected || 'one faculty for every section'}`;
    }
    return {
        category: 'Merged-Class Consistency',
        state: v.merge_state === 'incomplete' ? 'Incomplete' : 'Invalid',
        rows: [
            ['Group', v.group || '—'],
            ['Section', v.section_name || '—'],
            ['Expected slot', (v.code === 'HC16_FACULTY' || v.code === 'HC16_FACULTY_TBA')
                ? '—' : (v.expected || []).join('; ') || 'No group meeting yet'],
            ['Actual slot', (v.actual || []).join('; ') || '—'],
            ['Faculty issue', facIssue || '—'],
        ],
    };
}

(function _mgInjectStyle() {
    if (typeof document === 'undefined' || !document.head || document.getElementById('mg-style')) return;
    const st = document.createElement('style');
    st.id = 'mg-style';
    st.textContent = `
.mg-badge{position:relative;display:inline-flex;align-items:center;gap:5px;margin-top:6px;padding:3px 8px;border-radius:4px;background:#e8f1fb;color:#1f4f82;border:1px solid #9cc0e6;font-size:.68rem;font-weight:800;letter-spacing:.02em;cursor:help;}
.mg-badge-muted{background:#f1f2f4;color:#636e72;border-color:#ccd1d5;}
.mg-tip{display:none;position:absolute;z-index:50;top:calc(100% + 6px);left:0;min-width:260px;max-width:340px;padding:8px 10px;background:#fff;color:#2c3e50;border:1px solid #9cc0e6;border-radius:6px;box-shadow:0 4px 14px rgba(0,0,0,.15);font-weight:600;letter-spacing:0;}
.mg-badge:hover .mg-tip,.mg-badge:focus .mg-tip{display:block;}
.mg-tip-row{display:flex;gap:8px;padding:2px 0;font-size:.7rem;}
.mg-tip-row span{flex:0 0 86px;color:#7f8c8d;}
.mg-tip-row b{flex:1;font-weight:700;}
.mg-chip{display:inline-block;margin-top:6px;padding:3px 7px;border-radius:4px;background:#fdecea;color:#a93226;border:1px solid #f1a9a0;font-size:.66rem;font-weight:800;}
.mg-chip-inc{background:#fef5e7;color:#9a6200;border-color:#f5cf87;}
.mg-banner{margin:6px 0;padding:8px 10px;border-radius:6px;background:#eef5fc;border:1px solid #b9d3ee;color:#1f4f82;font-size:.72rem;}
.mg-banner-warn{background:#fdf2f0;border-color:#f1a9a0;color:#922b21;}
.mg-banner-inc{background:#fef9ee;border-color:#f5cf87;color:#7d5300;}
.mg-banner-head{font-weight:800;}
.mg-banner-body{margin-top:3px;font-weight:600;}
.mg-reset-btn{margin-top:6px;padding:5px 10px;border:none;border-radius:4px;background:#1f4f82;color:#fff;font-size:.7rem;font-weight:800;cursor:pointer;}
.ts-merge-note{margin:4px 0 2px;font-size:.68rem;color:#1f4f82;}
.mg-note-line{font-weight:800;}
.mg-note-warn{color:#922b21;}
.mg-note-sub{font-weight:600;color:#566573;}
.mg-note-err{color:#922b21;}
.ts-merge-locked .ts-dup-icon{display:none;}
.ts-merge-locked .ts-ss-input:disabled{background:#f4f7fa;color:#2c3e50;cursor:not-allowed;opacity:1;}
.mg-fac-locked{pointer-events:none;}
.mg-fac-note{margin-top:6px;padding:5px 8px;border-radius:4px;background:#eef5fc;color:#1f4f82;font-size:.68rem;font-weight:700;}
.viol-mg{margin-top:8px;border-top:1px dashed #e0b4ae;padding-top:6px;}
.viol-mg-row{display:flex;gap:8px;font-size:.78rem;padding:1px 0;}
.viol-mg-row span{flex:0 0 104px;color:#7f8c8d;font-weight:700;}
`;
    document.head.appendChild(st);
})();
