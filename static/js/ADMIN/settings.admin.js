/* ============================================================
   SETTINGS MODULE — ADMIN JS
   PUPLC Scheduling System
   ============================================================ */

const settingsWrapper  = document.getElementById('settingsWrapper');
const IS_LOCKED        = settingsWrapper?.getAttribute('data-locked') === 'true';
const LOCKED_DETAIL    = settingsWrapper?.getAttribute('data-locked-detail') || '';

/* Per-AY status: { [ay_id]: { isfinalized, has_published } } */
let AY_STATUS = {};
try {
  const raw = document.getElementById('ay-status-full');
  if (raw) AY_STATUS = JSON.parse(raw.textContent);
} catch(e) { AY_STATUS = {}; }

/* ── Tabs ───────────────────────────────────────────────── */
function _activateTab(target) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  const btn = document.querySelector(`.tab-btn[data-tab="${target}"]`);
  if (btn) btn.classList.add('active');
  document.getElementById('tab-' + target)?.classList.add('active');
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.tab;
    _activateTab(target);
    localStorage.setItem('settings_active_tab', target);
  });
});

/* ── Modal helpers ──────────────────────────────────────── */
function openSModal(id) {
  const el = document.getElementById(id);
  if (el) { el.style.display = 'flex'; }
}
function closeSModal(id) {
  const el = document.getElementById(id);
  if (el) { el.style.display = 'none'; }
}

/* Legacy aliases kept for backward compatibility */
function openModal(id)  { openSModal(id); }
function closeModal(id) { closeSModal(id); }

/* Close on overlay click */
document.querySelectorAll('.s-modal').forEach(m => {
  m.addEventListener('click', e => { if (e.target === m) closeSModal(m.id); });
});

/* ── Lock-state UI helpers ──────────────────────────────── */

/*
 * markTableActionsLocked — applies lock styling to non-AY td-action cells
 * (emp/desig) when the global lock is active.
 */
function markTableActionsLocked() {
  if (!IS_LOCKED) return;
  /* Skip AY rows — those use ay-row-btn, not td-action */
  document.querySelectorAll('.td-action:not(.ay-row-cell)').forEach(td => {
    td.classList.add('locked');
    td.title = `Settings locked: ${LOCKED_DETAIL}. Click to view in read-only mode.`;
    td.setAttribute('data-originally-locked', 'true');
  });
}

/*
 * validateAndOpen — called instead of directly opening an edit modal.
 * If locked → shows the lock-warning modal first.
 * If not locked → opens the target modal in edit mode.
 */
let _pendingModalId   = null;
let _pendingViewFn    = null;  /* fn to call once modal is open, to populate fields */

function validateAndOpen(modalId, populateFn) {
  if (!IS_LOCKED) {
    populateFn();
    openEditModal(modalId);
    return;
  }
  /* Locked: queue and show warning */
  _pendingModalId = modalId;
  _pendingViewFn  = populateFn;
  showLockWarning(modalId);
}

function showLockWarning(modalId) {
  const labels = {
    modalAY:          { sub: 'Academic Year configuration', field: 'ay' },
    modalEditEmpType: { sub: 'Faculty Hours',      field: 'et' },
    modalEditDesig:   { sub: 'Designee Hours',      field: 'desig' },
  };
  const info = labels[modalId] || { sub: 'this section', field: null };
  document.getElementById('lockWarnSub').textContent  = info.sub;
  document.getElementById('lockWarnBody').innerHTML   =
    `A <strong>Published</strong> schedule is active for the current term`
    + (LOCKED_DETAIL ? ` <strong>(${LOCKED_DETAIL})</strong>` : '')
    + `.<br><br>Editing this section is restricted to prevent accidental changes to a live schedule.<br><br>`
    + `You may <strong>view the current settings in read-only mode</strong>, or cancel.`;
  /* Store which field prefix the view-only mode should apply to */
  document.getElementById('modalLockWarning').dataset.targetField = info.field;
  openSModal('modalLockWarning');
}

/* Called when user chooses "View Only" in the lock warning modal */
function openViewOnly() {
  closeSModal('modalLockWarning');
  if (!_pendingViewFn || !_pendingModalId) return;
  _pendingViewFn();
  openViewOnlyModal(_pendingModalId);
  _pendingModalId = _pendingViewFn = null;
}

/* Called when admin chooses "Edit Anyway" — bypasses the lock for constraint-hour edits */
function openEditAnyway() {
  closeSModal('modalLockWarning');
  if (!_pendingViewFn || !_pendingModalId) return;
  _pendingViewFn();
  openEditModal(_pendingModalId);
  _pendingModalId = _pendingViewFn = null;
}

function openEditModal(id) {
  /* Ensure view-only mode is cleared */
  setViewOnlyMode(id, false);
  openSModal(id);
}

function openViewOnlyModal(id) {
  setViewOnlyMode(id, true);
  openSModal(id);
}

function setViewOnlyMode(modalId, isViewOnly) {
  const box = document.querySelector(`#${modalId} .s-modal-box`);
  if (!box) return;

  /* Toggle CSS class that hides save button + disables inputs */
  box.classList.toggle('modal-viewonly', isViewOnly);

  /* Disable / enable all inputs inside the modal */
  box.querySelectorAll('input, select, textarea').forEach(el => {
    if (el.type === 'hidden') return;
    el.disabled = isViewOnly;
  });

  /* Derive prefix from modal ID */
  const prefixMap = {
    modalAY:          'ay',
    modalEditEmpType: 'et',
    modalEditDesig:   'desig',
  };
  const prefix = prefixMap[modalId];

  /* Show / hide view-only badge and notice */
  if (prefix) {
    const badge  = document.getElementById(`${prefix}-view-badge`);
    const notice = document.getElementById(`${prefix}-view-notice`);
    const text   = document.getElementById(`${prefix}-view-notice-text`);
    if (badge)  badge.style.display  = isViewOnly ? 'inline-flex' : 'none';
    if (notice) notice.style.display = isViewOnly ? 'flex' : 'none';
    if (text && isViewOnly) {
      text.textContent = LOCKED_DETAIL
        ? `Editing locked — Published schedule exists for ${LOCKED_DETAIL}. Showing current values in read-only mode.`
        : 'Editing locked — a Published schedule is active. Showing read-only values.';
    }
  }
}

/* ── Time helpers ───────────────────────────────────────── */
const TIMES = (() => {
  const list = [];
  for (let total = 7 * 60 + 30; total <= 21 * 60; total += 30) {
    const h    = Math.floor(total / 60);
    const m    = total % 60;
    const hh   = h % 12 || 12;
    const ampm = h < 12 ? 'AM' : 'PM';
    const mm   = m === 0 ? '00' : '30';
    const val  = `${String(h).padStart(2,'0')}:${mm}`;
    list.push({ val, label: `${hh}:${mm} ${ampm}` });
  }
  return list;
})();

function buildTimeSelect(el, selectedVal) {
  if (!el) return;
  el.innerHTML = '';
  TIMES.forEach(t => {
    const opt      = document.createElement('option');
    opt.value      = t.val;
    opt.textContent = t.label;
    if (selectedVal && t.val === selectedVal) opt.selected = true;
    el.appendChild(opt);
  });
}

function clean(v) { return (v && v !== 'None') ? v : ''; }

/* ── ACADEMIC YEAR MODAL ────────────────────────────────── */

function _ayTagError(msg) {
  const wrap = document.getElementById('ay-tag-error');
  const el   = document.getElementById('ay-tag-error-msg');
  if (el)   el.textContent = msg || '';
  if (wrap) wrap.style.display = msg ? '' : 'none';
}

/* Validates completed year values — returns null if valid, error string if invalid.
   Returns null (no error) for empty/incomplete inputs so we don't fire while typing. */
function _validateAYYears(ys, ye) {
  if (!ys || !ye) return null; // Incomplete — caller decides
  const y1 = parseInt(ys), y2 = parseInt(ye);
  if (isNaN(y1) || isNaN(y2)) return null;
  if (y1 >= y2) return `Invalid: ${y1}–${y2}. The start year must come before the end year (e.g. 2025–2026).`;
  if (y2 - y1 !== 1) return `Invalid: ${y1}–${y2}. An Academic Year must span exactly one year (e.g. 2025–2026, not 2025–2027).`;

  const currentYear    = new Date().getFullYear();
  const maxExistingEnd = parseInt(document.getElementById('ay_max_year_end')?.value || '0');
  const isAddMode      = (document.getElementById('ay_form_action')?.value || 'add') === 'add';

  // Block AYs that are already in the past
  if (y2 <= currentYear) return `Cannot create AY ${y1}–${y2}. That Academic Year is already in the past.`;

  // In add mode: block duplicates (if y2 ≤ maxExistingEnd, this AY is already in the sequence)
  if (isAddMode && maxExistingEnd > 0 && y2 <= maxExistingEnd) {
    return `AY ${y1}–${y2} already exists. Use the edit (pencil) button on that row to modify it.`;
  }

  // Block forward gaps (must extend the sequence by exactly 1 year)
  if (isAddMode && maxExistingEnd > 0 && y1 > maxExistingEnd + 1) {
    return `Cannot skip years. The next Academic Year to add must be AY ${maxExistingEnd}–${maxExistingEnd + 1}, not ${y1}–${y2}.`;
  }

  return null;
}

/* Used on form submit — also catches fully missing values */
function _requireAYYears(ys, ye) {
  if (!ys || !ye || ys.length < 4 || ye.length < 4) return 'Academic Year tag is required (e.g. AY 2025-2026).';
  return _validateAYYears(ys, ye);
}

/* Builds the AY dropdown options for Add mode. Offers a range of years so the
   admin has a real choice — the first (sequential-next) option is pre-selected
   since it's normally the valid one, but _validateAYYears (wired to the select's
   change event) still catches and explains any invalid pick rather than hiding
   the options outright. */
function _buildAYOptions() {
  const sel = document.getElementById('ay_tag_display');
  if (!sel) return;
  sel.innerHTML = '';
  const maxEnd = parseInt(document.getElementById('ay_max_year_end')?.value || '0');
  const cy   = new Date().getFullYear();
  const base = maxEnd > 0 ? maxEnd : (cy - 1);
  const opts = [];
  for (let y1 = base; y1 <= base + 5; y1++) opts.push([y1, y1 + 1]);
  opts.forEach(([y1, y2]) => {
    const opt = document.createElement('option');
    opt.value = `${y1}-${y2}`;
    opt.textContent = `AY ${String(y1).slice(2)}-${String(y2).slice(2)}`;
    sel.appendChild(opt);
  });
  if (opts.length) {
    sel.value = `${opts[0][0]}-${opts[0][1]}`;
    document.getElementById('ay_year_start').value = opts[0][0];
    document.getElementById('ay_year_end').value   = opts[0][1];
  }
}

function openAYModal() {
  document.getElementById('ay_modal_title').innerText    = 'ADD NEW ACADEMIC YEAR';
  document.getElementById('ay_submit_btn').textContent   = 'ADD CALENDAR';
  document.querySelectorAll('#modalAY input[type="date"]').forEach(i => { i.value = ''; i.removeAttribute('min'); });
  document.getElementById('ay_form_action').value = 'add';
  _ayTagError(null);
  _buildAYOptions();
  openEditModal('modalAY');
}

/* Selecting a year in the dropdown sets the hidden start/end year fields */
document.getElementById('ay_tag_display')?.addEventListener('change', function() {
  const val = this.value;
  if (!val) {
    document.getElementById('ay_year_start').value = '';
    document.getElementById('ay_year_end').value   = '';
    _ayTagError(null);
    return;
  }
  const [y1, y2] = val.split('-');
  const err = _validateAYYears(y1, y2);
  if (err) {
    _ayTagError(err);
    document.getElementById('ay_year_start').value = '';
    document.getElementById('ay_year_end').value   = '';
    return;
  }
  _ayTagError(null);
  document.getElementById('ay_year_start').value = y1;
  document.getElementById('ay_year_end').value   = y2;
});

/* Returns an error string if the semester duration is less than 15 weeks, else null. */
function _checkSemDuration(startId, endId, label) {
  const s = document.getElementById(startId)?.value;
  const e = document.getElementById(endId)?.value;
  if (!s || !e) return null; // optional semesters (e.g. summer) may be blank
  const start = new Date(s), end = new Date(e);
  if (isNaN(start) || isNaN(end)) return null;
  if (end <= start) return `${label}: end date must be after start date.`;
  // Difference in whole weeks
  const weeks = Math.floor((end - start) / (7 * 24 * 60 * 60 * 1000));
  if (weeks < 15) return `${label}: must span at least 15 weeks.`;
  return null;
}

/* Block form submission if AY years are missing or invalid.
   Re-parses the visible tag input so the correct error shows even when
   hidden year fields were cleared by a prior inline validation. */
document.querySelector('#modalAY form')?.addEventListener('submit', function(e) {
  const tagVal = (document.getElementById('ay_tag_display')?.value || '').trim();
  const [y1, y2] = tagVal.split('-');

  const err = _requireAYYears(y1, y2);
  if (err) {
    e.preventDefault();
    _ayTagError(err);
    document.getElementById('ay_tag_display')?.focus();
    return false;
  }

  // Validate each semester spans at least 2 months
  const semErrors = [
    _checkSemDuration('ay_s1s', 'ay_s1e', '1st Semester'),
    _checkSemDuration('ay_s2s', 'ay_s2e', '2nd Semester'),
    _checkSemDuration('ay_s3s', 'ay_s3e', 'Summer'),
  ].filter(Boolean);
  if (semErrors.length) {
    e.preventDefault();
    _ayTagError(semErrors[0]);
    return false;
  }

  // Sync hidden fields before the form posts (they may have been cleared)
  document.getElementById('ay_year_start').value = y1;
  document.getElementById('ay_year_end').value   = y2;
});

/* Populated once the user resolves the warning modal, then the edit modal is opened */
let _pendingAYPopulate = null;

function prepareAY(el) {
  const d               = el.dataset;
  const ayId            = d.id;
  const isFinalized     = d.finalized === 'true';
  const hasPub          = d.haspub    === 'true';
  const computedStatus  = d.status    || 'current';

  const populate = () => {
    _ayTagError(null);
    document.getElementById('ay_modal_title').innerText  = isFinalized ? 'VIEW ACADEMIC YEAR' : 'EDIT ACADEMIC YEAR';
    document.getElementById('ay_submit_btn').textContent = 'SAVE CALENDAR';
    document.getElementById('ay_form_action').value      = 'edit';
    document.getElementById('ay_year_start').value = d.start;
    document.getElementById('ay_year_end').value   = d.end;
    const sel = document.getElementById('ay_tag_display');
    if (sel) {
      sel.innerHTML = '';
      const opt = document.createElement('option');
      opt.value = `${d.start}-${d.end}`;
      opt.textContent = `AY ${d.start?.slice(2)}-${d.end?.slice(2)}`;
      sel.appendChild(opt);
      sel.value = opt.value;
    }
    /* Forward-only rule: only enforce min=today on fields that are already today or future.
       Past-dated fields (already elapsed sems) get no min so they pass browser validation
       unchanged — the backend handles it if the admin actually edits them. */
    const today = new Date().toISOString().slice(0, 10);
    const setDateForward = (id, raw) => {
      const el = document.getElementById(id);
      if (!el) return;
      const val = clean(raw);
      el.value = val;
      if (!val || val < today) el.removeAttribute('min');
      else el.min = today;
    };
    setDateForward('ay_s1s', d.s1s);
    setDateForward('ay_s1e', d.s1e);
    setDateForward('ay_s2s', d.s2s);
    setDateForward('ay_s2e', d.s2e);
    setDateForward('ay_s3s', d.s3s);
    setDateForward('ay_s3e', d.s3e);
  };

  if (isFinalized) {
    populate();
    openViewOnlyModal('modalAY');
    const notice = document.getElementById('ay-view-notice');
    const text   = document.getElementById('ay-view-notice-text');
    const icon   = notice?.querySelector('i');
    if (icon) icon.className = 'fas fa-lock';
    if (text) text.textContent = 'This Academic Year is finalized and cannot be edited.';
    return;
  }

  if (hasPub) {
    /* Has a published schedule — allow edits but dates can only move forward (min enforced) */
    populate();
    openEditModal('modalAY');
    const notice  = document.getElementById('ay-view-notice');
    const text    = document.getElementById('ay-view-notice-text');
    const icon    = notice?.querySelector('i');
    if (notice) notice.style.display = 'flex';
    if (icon)   { icon.className = 'fas fa-forward'; }
    if (text)   text.textContent = 'This Academic Year has a Published schedule. Dates can only be adjusted forward, not backward.';
    return;
  }

  if (computedStatus === 'past') {
    /* Past AY — warn before allowing edits */
    _pendingAYPopulate = populate;
    openSModal('modalAYPast');
    return;
  }

  populate();
  openEditModal('modalAY');
}

/* Called when user chooses "Continue Changes" in the AY warning modal */
function ayWarnContinue() {
  closeSModal('modalAYWarning');
  if (_pendingAYPopulate) {
    _pendingAYPopulate();
    _pendingAYPopulate = null;
    openEditModal('modalAY');
  }
}

/* Called when user chooses "Apply to Future Schedules Only" */
function ayWarnFuture() {
  closeSModal('modalAYWarning');
  if (_pendingAYPopulate) {
    _pendingAYPopulate();
    _pendingAYPopulate = null;
    openEditModal('modalAY');
  }
}

/* Called when user chooses "Edit Anyway" from the Past AY warning modal */
function ayPastContinue() {
  closeSModal('modalAYPast');
  if (_pendingAYPopulate) {
    _pendingAYPopulate();
    _pendingAYPopulate = null;
    openEditModal('modalAY');
  }
}

/* ── FINALIZE ACADEMIC YEAR ─────────────────────────────── */
function openFinalizeModal(ayId) {
  document.getElementById('finalizeAYId').value = ayId;
  const yy1 = ayId.slice(2, 4), yy2 = ayId.slice(4);
  document.getElementById('finalizeAYLabel').textContent = `AY ${yy1}-${yy2}`;
  openSModal('modalFinalizeAY');
}

/* ── DELETE ACADEMIC YEAR ───────────────────────────────── */
function openDeleteAYModal(ayId) {
  document.getElementById('deleteAYId').value = ayId;
  const yy1 = ayId.slice(2, 4), yy2 = ayId.slice(4);
  document.getElementById('deleteAYLabel').textContent = `AY ${yy1}-${yy2}`;
  openSModal('modalDeleteAY');
}

/* ── EMPLOYEE TYPE MODAL ────────────────────────────────── */

/* Pre-build time selects */
['modal_et_rs','modal_et_re','modal_et_ps','modal_et_pe'].forEach(id => {
  buildTimeSelect(document.getElementById(id), null);
});

function prepareEmp(el) {
  const d = el.dataset;
  const isPartTime = d.name === 'Part-time';
  const populate = () => {
    document.getElementById('et_name_label').innerText = d.name;
    document.getElementById('modal_et_id').value       = d.id;
    document.getElementById('modal_et_reg').value      = clean(d.rl)  || '';
    document.getElementById('modal_et_pt').value       = clean(d.ptl) || '';
    document.getElementById('modal_et_sub').value      = clean(d.sub) || '';

    const regRow = document.getElementById('et_reg_hours_row');
    if (regRow) regRow.style.display = isPartTime ? 'none' : '';

    // Clear regular hours for Part-time so they aren't saved (disabled fields aren't submitted)
    const rsEl = document.getElementById('modal_et_rs');
    const reEl = document.getElementById('modal_et_re');
    if (isPartTime) {
      buildTimeSelect(rsEl, null);
      buildTimeSelect(reEl, null);
      if (rsEl) rsEl.disabled = true;
      if (reEl) reEl.disabled = true;
    } else {
      buildTimeSelect(rsEl, clean(d.rs) || null);
      buildTimeSelect(reEl, clean(d.re) || null);
      if (rsEl) rsEl.disabled = false;
      if (reEl) reEl.disabled = false;
    }

    buildTimeSelect(document.getElementById('modal_et_ps'), clean(d.ps) || null);
    buildTimeSelect(document.getElementById('modal_et_pe'), clean(d.pe) || null);
    const restrict = d.restrict !== 'false';
    const cb  = document.getElementById('modal_et_restrict');
    const hid = document.getElementById('modal_et_restrict_hidden');
    const lbl = document.getElementById('modal_et_restrict_label');
    if (cb)  cb.checked = restrict;
    if (hid) hid.value  = restrict ? 'true' : 'false';
    if (lbl) lbl.textContent = restrict
      ? 'ON — schedules must fall within the configured PT Hours range'
      : 'OFF — only load limits are enforced; PT Hours are informational';
  };
  validateAndOpen('modalEditEmpType', populate);
}

/* ── DESIGNEE MODALS ────────────────────────────────────── */

/* Pre-build time selects */
['desig_reg_from','desig_reg_to'].forEach(id => {
  buildTimeSelect(document.getElementById(id), null);
});

function prepareDesig(el) {
  const d = el.dataset;
  const populate = () => {
    document.getElementById('des_name_title').innerText = d.name;
    document.getElementById('modal_des_id').value       = d.id;
    document.getElementById('modal_des_reg').value      = d.rl;
    document.getElementById('modal_des_night').value    = d.nt;
    applyDesigHours(parseInt(d.nt) || 0, 'desig');
  };
  validateAndOpen('modalEditDesig', populate);
}

function updateDesigHours() {
  const nt = parseInt(document.getElementById('modal_des_night').value) || 0;
  applyDesigHours(nt, 'desig');
}

function applyDesigHours(nt, prefix) {
  buildTimeSelect(document.getElementById(prefix + '_reg_from'), nt > 0 ? '07:30' : '08:00');
  buildTimeSelect(document.getElementById(prefix + '_reg_to'),   nt > 0 ? '16:30' : '17:00');
}

function updateAddDesigHours() {
  const nt = parseInt(document.getElementById('add_des_night').value) || 0;
  document.getElementById('add_des_reg_band').value = nt > 0 ? '07:30 AM - 04:30 PM' : '08:00 AM - 05:00 PM';
}

function openAddDesigModal() { openSModal('modalAddDesig'); }

/* ── HARD CONSTRAINTS — DB-backed state ─────────────────── */

const DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];

const DEFAULT_PAIRS = [
  { from:'Monday', to:'Thursday' },
  { from:'Tuesday', to:'Friday' },
  { from:'Wednesday', to:'Saturday' },
];

const DEFAULT_SLOTS = [
  { from:'07:30', to:'09:00' },
  { from:'09:00', to:'10:30' },
  { from:'10:30', to:'12:00' },
  { from:'12:00', to:'13:30' },
  { from:'13:30', to:'15:00' },
  { from:'15:00', to:'16:30' },
  { from:'16:30', to:'18:00' },
  { from:'18:00', to:'19:30' },
  { from:'19:30', to:'21:00' },
];

const TOGGLE_MAP = [
  { id:'tog-weekend',     key:'hc_weekend_enabled'       },
  { id:'tog-day-pair',    key:'hc_day_pairing_enabled'   },
  { id:'tog-lab',         key:'hc_lab_session_enabled'   },
  { id:'tog-faculty-spec',key:'hc_faculty_spec_enabled'  },
  { id:'tog-merge',       key:'hc_merge_enabled'         },
];

/* In-memory cache so saves batch nicely */
let _hcState = {};

/* ── Debounced backend save ──────────────────────────────── */
let _hcSaveTimer = null;
function _scheduleHCSave() {
  clearTimeout(_hcSaveTimer);
  _hcSaveTimer = setTimeout(_saveHCToBackend, 600);
  _setHCSaveStatus('saving');
}

function _setHCSaveStatus(state) {
  const ind = document.getElementById('hc-save-status');
  if (!ind) return;
  if (state === 'saving') {
    ind.textContent = 'Saving…';
    ind.className   = 'hc-save-status saving';
  } else if (state === 'saved') {
    ind.textContent = '✓ All constraints saved';
    ind.className   = 'hc-save-status saved';
  } else if (state === 'error') {
    ind.textContent = '⚠ Save failed — check console';
    ind.className   = 'hc-save-status error';
  }
}

async function _saveHCToBackend() {
  try {
    const res = await fetch('/admin/settings/hard_constraints', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(_hcState),
    });
    const json = await res.json();
    _setHCSaveStatus(json.success ? 'saved' : 'error');
  } catch {
    _setHCSaveStatus('error');
  }
}

async function _loadHCFromBackend() {
  try {
    const res  = await fetch('/admin/settings/hard_constraints');
    const data = await res.json();
    _hcState   = data;
    return data;
  } catch {
    return {};
  }
}

/* ── Public API (called by HTML onchange) ────────────────── */
function saveConstraint(key, val) {
  const dbKey = 'hc_' + key + '_enabled';
  _hcState[dbKey] = val ? 1 : 0;
  _scheduleHCSave();
}

function saveConstraintParam(key, val) {
  const dbKey = 'hc_' + key;
  _hcState[dbKey] = val;
  _scheduleHCSave();
}

/* Merge toggle — also shows/dims the scope selector */
function saveMergeConstraint(checked) {
  _hcState['hc_merge_enabled'] = checked ? 1 : 0;
  _scheduleHCSave();
  _applyMergeBodyState(checked);
}

function _applyMergeBodyState(enabled) {
  const body = document.getElementById('merge-body');
  if (!body) return;
  body.style.opacity      = enabled ? '1'        : '0.4';
  body.style.pointerEvents = enabled ? 'auto'    : 'none';
  const scopeSel = document.getElementById('param-merge-scope');
  if (scopeSel) scopeSel.disabled = !enabled;
}

async function initHCToggles() {
  const data = await _loadHCFromBackend();

  TOGGLE_MAP.forEach(({ id, key }) => {
    const el = document.getElementById(id);
    if (!el) return;
    /* Default to 1 (enabled) if key not yet in DB */
    el.checked = (key in data) ? (Number(data[key]) !== 0) : true;
  });

  /* Weekend params */
  const ws = document.getElementById('param-weekend-subj');
  const wd = document.getElementById('param-weekend-day');
  if (ws && data.hc_weekend_subject) ws.value = data.hc_weekend_subject;
  if (wd && data.hc_weekend_day)     wd.value = data.hc_weekend_day;

  /* Merge params */
  const mergeEnabled = ('hc_merge_enabled' in data) ? (Number(data.hc_merge_enabled) !== 0) : true;
  const ms = document.getElementById('param-merge-scope');
  if (ms && data.hc_merge_scope) ms.value = data.hc_merge_scope;
  _applyMergeBodyState(mergeEnabled);
}

/* ── Day Pairs ──────────────────────────────────────────── */
function loadPairsFromState() {
  try {
    const raw = _hcState.hc_day_pairs;
    if (!raw) return null;
    const arr = JSON.parse(raw);
    return arr.map(p => ({ from: p[0], to: p[1] }));
  } catch { return null; }
}
function savePairs() {
  const data = [];
  document.querySelectorAll('#day-pairs-container .pair-row').forEach(row => {
    const sels = row.querySelectorAll('.pair-day-sel');
    if (sels.length === 2) data.push([sels[0].value, sels[1].value]);
  });
  _hcState.hc_day_pairs = JSON.stringify(data);
  _scheduleHCSave();
}

function renderPair(pair, container) {
  const row = document.createElement('div');
  row.className = 'pair-row';
  row.innerHTML = `
    <select class="pair-day-sel" onchange="savePairs()">
      ${DAYS.map(d => `<option${d===pair.from?' selected':''}>${d}</option>`).join('')}
    </select>
    <span class="pair-badge">paired to</span>
    <select class="pair-day-sel" onchange="savePairs()">
      ${DAYS.map(d => `<option${d===pair.to?' selected':''}>${d}</option>`).join('')}
    </select>
    <button type="button" class="btn-del" onclick="this.closest('.pair-row').remove(); savePairs();">
      <i class="fas fa-trash-alt"></i>
    </button>`;
  container.appendChild(row);
}

function initDayPairs() {
  const container = document.getElementById('day-pairs-container');
  if (!container) return;
  const pairs = loadPairsFromState() || DEFAULT_PAIRS;
  pairs.forEach(p => renderPair(p, container));
}

function addDayPair() {
  const c = document.getElementById('day-pairs-container');
  renderPair({ from:'Monday', to:'Thursday' }, c);
  savePairs();
}

/* ── Merge Class — Allowed Section Pairings ──────────────── */
/* Section "value" is a stable "PROGRAMCODE-SECTIONNAME" label — matched against
   schedule rows at merge-validation time, independent of any one academic year's
   sectionid so a configured pairing keeps working after sections are recreated
   for a new AY. */
function _getMergeSectionOptions() {
  const raw = document.getElementById('pm-data');
  if (!raw) return [];
  let data;
  try { data = JSON.parse(raw.textContent); } catch { return []; }
  const sections = Array.isArray(data.sections) ? data.sections : [];
  const seen = new Map();
  sections.forEach(s => {
    const prog = (s.programcode || '').toUpperCase();
    const name = s.sectionname || '';
    if (!prog || !name) return;
    const value = `${prog}-${name}`;
    if (seen.has(value)) return;
    seen.set(value, { value, label: `${value}${s.yearlevel ? ' (Year ' + s.yearlevel + ')' : ''}` });
  });
  return Array.from(seen.values()).sort((a, b) => a.value.localeCompare(b.value));
}

function loadSectionPairsFromState() {
  try {
    const raw = _hcState.hc_merge_section_pairs;
    if (!raw) return null;
    const arr = JSON.parse(raw);
    return arr.map(p => ({ from: p[0], to: p[1] }));
  } catch { return null; }
}

function saveSectionPairs() {
  const data = [];
  document.querySelectorAll('#merge-section-pairs-container .pair-row').forEach(row => {
    const sels = row.querySelectorAll('.pair-section-sel');
    if (sels.length === 2 && sels[0].value && sels[1].value) data.push([sels[0].value, sels[1].value]);
  });
  _hcState.hc_merge_section_pairs = JSON.stringify(data);
  _scheduleHCSave();
}

function renderSectionPair(pair, container, options) {
  const opts = options || _getMergeSectionOptions();
  const optHtml = sel =>
    (opts.length ? opts : [{ value: sel, label: sel || '—' }])
      .map(o => `<option value="${o.value}"${o.value===sel?' selected':''}>${o.label}</option>`).join('');
  const row = document.createElement('div');
  row.className = 'pair-row';
  row.innerHTML = `
    <select class="pair-section-sel pair-day-sel" onchange="saveSectionPairs()">
      ${optHtml(pair.from)}
    </select>
    <span class="pair-badge">paired to</span>
    <select class="pair-section-sel pair-day-sel" onchange="saveSectionPairs()">
      ${optHtml(pair.to)}
    </select>
    <button type="button" class="btn-del" onclick="this.closest('.pair-row').remove(); saveSectionPairs();">
      <i class="fas fa-trash-alt"></i>
    </button>`;
  container.appendChild(row);
}

function initSectionPairs() {
  const container = document.getElementById('merge-section-pairs-container');
  if (!container) return;
  const options = _getMergeSectionOptions();
  const pairs = loadSectionPairsFromState() || [];
  pairs.forEach(p => renderSectionPair(p, container, options));
}

function addSectionPair() {
  const c = document.getElementById('merge-section-pairs-container');
  const options = _getMergeSectionOptions();
  if (!options.length) { _showToast('error', 'No sections available to pair. Add sections under Program Management first.'); return; }
  renderSectionPair({ from: options[0].value, to: (options[1] || options[0]).value }, c, options);
  saveSectionPairs();
}

/* ── Merged Class Faculty Load Policy ────────────────────── */
let _MLP_SUBJECTS = null;   // [{subjectcode, subjectname}] — fetched once, cached
let _MLP_POLICIES = [];     // current rule list from the server

async function _getMlpSubjects() {
  if (_MLP_SUBJECTS) return _MLP_SUBJECTS;
  try {
    const res  = await fetch('/api/subjects/all');
    const data = await res.json();
    _MLP_SUBJECTS = Array.isArray(data.subjects) ? data.subjects : [];
  } catch { _MLP_SUBJECTS = []; }
  return _MLP_SUBJECTS;
}

async function loadMergeLoadPolicies() {
  const body = document.getElementById('mergeLoadPolicyBody');
  if (!body) return;
  try {
    const res  = await fetch('/admin/settings/merge_load_policies');
    const data = await res.json();
    _MLP_POLICIES = data.success ? (data.policies || []) : [];
  } catch { _MLP_POLICIES = []; }
  await _getMlpSubjects();
  _renderMergeLoadPolicyTable();
}

function _renderMergeLoadPolicyTable() {
  const body = document.getElementById('mergeLoadPolicyBody');
  if (!body) return;
  if (!_MLP_POLICIES.length) {
    body.innerHTML = '<tr><td colspan="5" class="mlp-empty">No rules configured — merged classes use the subject\'s real units/duration by default.</td></tr>';
    return;
  }
  body.innerHTML = _MLP_POLICIES.map(p => _mlpReadRowHtml(p)).join('');
}

function _mlpReadRowHtml(p) {
  return `
    <tr data-policyid="${p.policyid}">
        <td>${escHtml(p.subjectcode)}${p.subjectname ? `<span class="mlp-sub-name">${escHtml(p.subjectname)}</span>` : ''}</td>
        <td>${p.min_sections}–${p.max_sections} sections</td>
        <td>${p.creditunits ?? '—'}</td>
        <td>${p.tuitionhours ?? '—'}</td>
        <td class="mlp-actions">
            <button class="mlp-edit" title="Edit" onclick="editMergeLoadPolicyRow(${p.policyid})"><i class="fas fa-pen"></i></button>
            <button class="mlp-del" title="Delete" onclick="deleteMergeLoadPolicyRow(${p.policyid})"><i class="fas fa-trash-alt"></i></button>
        </td>
    </tr>`;
}

function _mlpSubjectOptionsHtml(selected) {
  const subs = _MLP_SUBJECTS || [];
  if (!subs.length) return `<option value="${selected||''}">${selected||'—'}</option>`;
  return subs.map(s =>
    `<option value="${s.subjectcode}"${s.subjectcode===selected?' selected':''}>${escHtml(s.subjectcode)}${s.subjectname ? ' — ' + escHtml(s.subjectname) : ''}</option>`
  ).join('');
}

/* Credited units / tuition hours are read-only previews only — always derived
   server-side from the subject's own record, never editable here. */
function _mlpEditRowHtml(p) {
  const id = p.policyid ?? '';
  return `
    <tr data-policyid="${id}" data-editing="1">
        <td><select class="mlp-input mlp-f-subject" onchange="_mlpRefreshPreview(this)">${_mlpSubjectOptionsHtml(p.subjectcode)}</select></td>
        <td>
            <div class="mlp-range-inputs">
                <input type="number" class="mlp-input mlp-f-min" min="2" value="${p.min_sections ?? 2}">
                <span>–</span>
                <input type="number" class="mlp-input mlp-f-max" min="2" value="${p.max_sections ?? 2}">
            </div>
        </td>
        <td class="mlp-preview-units">${p.creditunits ?? '…'}</td>
        <td class="mlp-preview-hours">${p.tuitionhours ?? '…'}</td>
        <td class="mlp-actions">
            <button class="mlp-save" title="Save" onclick="saveMergeLoadPolicyRow(${id ? id : 'null'}, this)"><i class="fas fa-check"></i></button>
            <button class="mlp-cancel" title="Cancel" onclick="cancelMergeLoadPolicyRow(${id ? id : 'null'}, this)"><i class="fas fa-times"></i></button>
        </td>
    </tr>`;
}

/* Live-preview the credited units/tuition hours that will apply once the selected
   subject in an edit row is changed — purely informational, computed client-side from
   the same _MLP_SUBJECTS list is not possible (creditunits/hours aren't in that list),
   so this re-fetches the authoritative value from the server for just that subject. */
async function _mlpRefreshPreview(selectEl) {
  const row = selectEl.closest('tr');
  const unitsCell = row.querySelector('.mlp-preview-units');
  const hoursCell = row.querySelector('.mlp-preview-hours');
  unitsCell.textContent = '…'; hoursCell.textContent = '…';
  try {
    const res  = await fetch(`/admin/settings/merge_load_policies/subject_preview?subjectcode=${encodeURIComponent(selectEl.value)}`);
    const data = await res.json();
    unitsCell.textContent = data.creditunits ?? '—';
    hoursCell.textContent = data.tuitionhours ?? '—';
  } catch {
    unitsCell.textContent = '—'; hoursCell.textContent = '—';
  }
}

async function addMergeLoadPolicyRow() {
  const body = document.getElementById('mergeLoadPolicyBody');
  if (!body) return;
  await _getMlpSubjects();
  if (!(_MLP_SUBJECTS && _MLP_SUBJECTS.length)) {
    _showToast('error', 'No subjects found. Import a curriculum first.');
    return;
  }
  if (body.querySelector('[data-editing="1"]')) return;   // one edit at a time
  if (body.querySelector('.mlp-empty')) body.innerHTML = '';
  body.insertAdjacentHTML('afterbegin', _mlpEditRowHtml({
    subjectcode: _MLP_SUBJECTS[0].subjectcode, min_sections: 2, max_sections: 2,
  }));
  const firstRow = body.querySelector('tr[data-editing="1"]');
  if (firstRow) _mlpRefreshPreview(firstRow.querySelector('.mlp-f-subject'));
}

function editMergeLoadPolicyRow(policyid) {
  const body = document.getElementById('mergeLoadPolicyBody');
  if (!body || body.querySelector('[data-editing="1"]')) return;
  const p = _MLP_POLICIES.find(x => x.policyid === policyid);
  if (!p) return;
  const row = body.querySelector(`tr[data-policyid="${policyid}"]`);
  if (row) row.outerHTML = _mlpEditRowHtml(p);
}

function cancelMergeLoadPolicyRow(policyid, btn) {
  const row = btn.closest('tr');
  const existing = _MLP_POLICIES.find(x => x.policyid === policyid);
  if (existing) {
    row.outerHTML = _mlpReadRowHtml(existing);
  } else {
    row.remove();
    if (!_MLP_POLICIES.length) _renderMergeLoadPolicyTable();
  }
}

async function saveMergeLoadPolicyRow(policyid, btn) {
  const row = btn.closest('tr');
  const payload = {
    subjectcode:  row.querySelector('.mlp-f-subject').value,
    min_sections: row.querySelector('.mlp-f-min').value,
    max_sections: row.querySelector('.mlp-f-max').value,
  };
  const url    = policyid ? `/admin/settings/merge_load_policies/${policyid}` : '/admin/settings/merge_load_policies';
  const method = policyid ? 'PUT' : 'POST';
  try {
    const res  = await fetch(url, { method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!data.success) { _showToast('error', data.error || 'Could not save rule.'); return; }
    _showToast('success', policyid ? 'Rule updated.' : 'Rule added.');
    await loadMergeLoadPolicies();
  } catch {
    _showToast('error', 'Network error — could not save rule.');
  }
}

async function deleteMergeLoadPolicyRow(policyid) {
  if (!confirm('Delete this merged class load policy rule?')) return;
  try {
    const res  = await fetch(`/admin/settings/merge_load_policies/${policyid}`, { method: 'DELETE' });
    const data = await res.json();
    if (!data.success) { _showToast('error', data.error || 'Could not delete rule.'); return; }
    _showToast('success', 'Rule deleted.');
    await loadMergeLoadPolicies();
  } catch {
    _showToast('error', 'Network error — could not delete rule.');
  }
}

/* ── Time Slots ─────────────────────────────────────────── */
function loadSlotsFromState() {
  try {
    const raw = _hcState.hc_time_slots;
    if (!raw) return null;
    const arr = JSON.parse(raw);
    /* Expecting [[h,m,h,m],...] — convert to {from, to} */
    return arr.map(s => ({
      from: `${String(s[0]).padStart(2,'0')}:${String(s[1]).padStart(2,'0')}`,
      to:   `${String(s[2]).padStart(2,'0')}:${String(s[3]).padStart(2,'0')}`,
    }));
  } catch { return null; }
}
function saveSlots() {
  const data = [];
  document.querySelectorAll('#time-slots-container .slot-row').forEach(row => {
    const sels = row.querySelectorAll('.slot-sel');
    if (sels.length !== 2) return;
    const [fh, fm] = sels[0].value.split(':').map(Number);
    const [th, tm] = sels[1].value.split(':').map(Number);
    data.push([fh, fm, th, tm]);
  });
  _hcState.hc_time_slots = JSON.stringify(data);
  _scheduleHCSave();
}

function renderSlot(slot, container) {
  const row    = document.createElement('div');
  row.className = 'slot-row';
  const fSel   = document.createElement('select'); fSel.className = 'slot-sel'; fSel.addEventListener('change', saveSlots); buildTimeSelect(fSel, slot.from);
  const sep    = document.createElement('span');   sep.className  = 'slot-sep'; sep.textContent = 'to';
  const tSel   = document.createElement('select'); tSel.className = 'slot-sel'; tSel.addEventListener('change', saveSlots); buildTimeSelect(tSel, slot.to);
  const del    = document.createElement('button'); del.type = 'button'; del.className = 'btn-del'; del.innerHTML = '<i class="fas fa-times"></i>';
  del.addEventListener('click', () => { row.remove(); saveSlots(); });
  row.appendChild(fSel); row.appendChild(sep); row.appendChild(tSel); row.appendChild(del);
  container.appendChild(row);
}

function initTimeSlots() {
  const container = document.getElementById('time-slots-container');
  if (!container) return;
  const slots = loadSlotsFromState() || DEFAULT_SLOTS;
  slots.forEach(s => renderSlot(s, container));
}

function addTimeSlot() {
  renderSlot({ from:'07:30', to:'09:00' }, document.getElementById('time-slots-container'));
  saveSlots();
}

/* ── Activity Logs ──────────────────────────────────────── */
let _logSortDir = 'desc';

function setLogSort(dir) {
  _logSortDir = dir;
  document.getElementById('logSortDesc')?.classList.toggle('active', dir === 'desc');
  document.getElementById('logSortAsc')?.classList.toggle('active', dir === 'asc');

  const tbody = document.getElementById('logsTableBody');
  if (!tbody) return;
  const rows = Array.from(tbody.querySelectorAll('tr.log-row'));
  rows.sort((a, b) => {
    const ta = a.querySelector('.log-ts')?.textContent.trim() || '';
    const tb = b.querySelector('.log-ts')?.textContent.trim() || '';
    return dir === 'desc' ? tb.localeCompare(ta) : ta.localeCompare(tb);
  });
  rows.forEach(r => tbody.appendChild(r));
}

function filterLogs() {
  const q    = (document.getElementById('logSearch')?.value  || '').toLowerCase();
  const cat  = (document.getElementById('logFilterCat')?.value || '').toLowerCase();
  const user = (document.getElementById('logFilterUser')?.value || '').toLowerCase();

  document.querySelectorAll('#logsTableBody tr.log-row').forEach(row => {
    const text    = row.dataset.text  || '';
    const rowCat  = row.dataset.cat   || '';
    const rowUser = row.dataset.user  || '';

    const matchQ    = !q    || text.includes(q);
    const matchCat  = !cat  || rowCat  === cat;
    const matchUser = !user || rowUser === user;

    row.classList.toggle('hidden', !(matchQ && matchCat && matchUser));
  });
}

function exportLogs() {
  /* Build CSV from visible rows */
  const rows = Array.from(document.querySelectorAll('#logsTableBody tr.log-row:not(.hidden)'));
  if (rows.length === 0) { alert('No log entries to export.'); return; }

  const headers = ['Timestamp','Action','Details','Initiated By','Category'];
  const lines   = [headers.join(',')];
  rows.forEach(row => {
    const cells = row.querySelectorAll('td');
    const ts     = cells[0]?.textContent.trim() || '';
    const action = row.querySelector('.log-action')?.textContent.trim() || '';
    const detail = cells[2]?.textContent.trim().replace(/,/g, ';') || '';
    const user   = cells[3]?.textContent.trim() || '';
    const cat    = cells[4]?.textContent.trim() || '';
    lines.push([`"${ts}"`, `"${action}"`, `"${detail}"`, `"${user}"`, `"${cat}"`].join(','));
  });

  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `activity_logs_${new Date().toISOString().slice(0,10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

/* ════════════════════════════════════════════════════════
   PROGRAM MANAGEMENT PANEL
   ════════════════════════════════════════════════════════ */

let PM_DATA     = { programs: [], sections: [], yearlevels: [] };
let PM_SELECTED = null;

// Reloads Settings with the Program Management panel scoped to a different AY
// (Current or Next). Selected tab/program survive via localStorage (see below).
function _switchMgmtAy(ayId) {
  const url = new URL(window.location.href);
  url.searchParams.set('mgmt_ay', ayId);
  window.location.href = url.toString();
}

function initProgramPanel() {
  const raw = document.getElementById('pm-data');
  if (!raw) return;
  try { PM_DATA = JSON.parse(raw.textContent); } catch { return; }
  if (!PM_DATA.programs.length) return;
  const saved = localStorage.getItem('settings_pm_selected');
  const found = saved && PM_DATA.programs.find(p => p.programcode === saved);
  selectProgram(found ? saved : PM_DATA.programs[0].programcode);
}

/* ── Helpers ────────────────────────────────────────── */
function filterProgramList() {
  const q = (document.getElementById('pmSearch')?.value || '').toLowerCase();
  document.querySelectorAll('.pm-list-item').forEach(item => {
    const code = (item.dataset.code || '').toLowerCase();
    const name = item.querySelector('.pm-item-name')?.textContent.toLowerCase() || '';
    item.style.display = (code.includes(q) || name.includes(q)) ? '' : 'none';
  });
}

function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function _ordinalYear(num) {
  const n = parseInt(num);
  if (isNaN(n)) return String(num) + ' Year';
  const sfx = ['th','st','nd','rd'], v = n % 100;
  return n + (sfx[(v-20)%10] || sfx[v] || sfx[0]) + ' Year';
}

function togglePmdSection(bodyId, chevronId) {
  const body = document.getElementById(bodyId);
  const chev = document.getElementById(chevronId);
  if (!body) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : '';
  if (chev) chev.style.transform = open ? 'rotate(-90deg)' : '';
}

/* Returns year level rows for this program */
function _ylsFor(progCode) {
  return (PM_DATA.yearlevels || []).filter(y => y.programcode === progCode);
}

/* Returns all section rows for the selected program */
function _deriveSections(progCode) {
  return (PM_DATA.sections || []).filter(s => s.programcode === progCode);
}

/* ── Sections cache + pagination ────────────────────── */
let _PM_CURRENT_SECTIONS = [];
const _PM_SEC_PER_PAGE   = 8;

/* ── Select program ─────────────────────────────────── */
function selectProgram(code) {
  PM_SELECTED = code;
  localStorage.setItem('settings_pm_selected', code);
  document.querySelectorAll('.pm-list-item').forEach(el =>
    el.classList.toggle('active', el.dataset.code === code)
  );
  const prog = PM_DATA.programs.find(p => p.programcode === code);
  if (!prog) return;

  document.getElementById('pmEmpty').style.display         = 'none';
  document.getElementById('pmDetailContent').style.display = 'block';

  _renderHeader(prog);
  _renderProgInfo(prog);
  _renderYearLevelsGrid(prog);

  _PM_CURRENT_SECTIONS = _deriveSections(code);
  _populateYLFilter(code, prog.numyearlevel);
  document.getElementById('pmdSecFilter')?.setAttribute('value', '');
  if (document.getElementById('pmdSecFilter')) document.getElementById('pmdSecFilter').value = '';
  if (document.getElementById('pmdSecYLFilter')) document.getElementById('pmdSecYLFilter').value = '';
  _renderSectionsPage(1);
}

function _renderHeader(prog) {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  const yls      = _ylsFor(prog.programcode);
  const activeYL = yls.filter(y => y.isactive).length;
  /* Derive active section count from live data instead of stale backend field */
  const liveSections = (PM_DATA.sections || []).filter(
    s => s.programcode === prog.programcode && s.isactive
  ).length;
  set('pdCode',          prog.programcode);
  set('pdName',          prog.programname);
  set('pdCodeBadge',     prog.programcode);
  set('pdStatActiveYL',  activeYL);
  set('pdStatYearLevels',prog.numyearlevel ?? '—');
  set('pdStatSections',  liveSections);
  const statusEl = document.getElementById('pdStatusBadge');
  if (statusEl) {
    statusEl.className = `pmd-status-badge ${prog.isactive ? 'pmd-status-active' : 'pmd-status-inactive'}`;
    statusEl.innerHTML = `<span class="pmd-dot"></span> ${prog.isactive ? 'Active' : 'Inactive'}`;
  }
}

function _renderProgInfo(prog) {
  const tbody = document.getElementById('pmdInfoBody');
  if (!tbody) return;
  const sBadge = `<span class="pmd-status-badge ${prog.isactive ? 'pmd-status-active' : 'pmd-status-inactive'}">
    <span class="pmd-dot"></span>${prog.isactive ? 'Active' : 'Inactive'}</span>`;
  tbody.innerHTML = `
    <tr>
      <td><strong>${escHtml(prog.programcode)}</strong></td>
      <td>${escHtml(prog.programname)}</td>
      <td>${escHtml(prog.programtype || 'Undergraduate')}</td>
      <td>${prog.numyearlevel || '—'}</td>
      <td>${sBadge}</td>
      <td class="pmd-info-actions-cell">
        <button class="pmd-btn-edit" onclick="openEditProgram()"><i class="fas fa-edit"></i> Edit Program</button>
        ${prog.isactive
          ? `<button class="pmd-btn-deactivate" onclick="confirmDeleteProgram()"><i class="fas fa-power-off"></i> Deactivate</button>`
          : `<button class="pmd-btn-edit" onclick="confirmActivateProgram()"><i class="fas fa-power-off"></i> Activate</button>`}
      </td>
    </tr>`;
}

/* ── Live section count from PM_DATA.sections (program + year level) ── */
function _secCountForYL(progCode, yl) {
  return (PM_DATA.sections || []).filter(
    s => s.programcode === progCode && Number(s.yearlevel) === Number(yl) && s.isactive
  ).length;
}

/* ── Year Level Management Grid (inline-edit rows) ─── */
function _renderYearLevelsGrid(prog) {
  const tbody = document.getElementById('pmdYearLevelsBody');
  if (!tbody) return;
  const numYL  = parseInt(prog.numyearlevel) || 0;
  const ylRows = _ylsFor(prog.programcode);
  const ylMap  = {};
  ylRows.forEach(y => { ylMap[y.yearlevel] = y; });

  if (numYL === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="pmd-empty-row">No year levels configured for this program.</td></tr>`;
    return;
  }

  const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

  tbody.innerHTML = Array.from({length: numYL}, (_, i) => {
    const yl       = i + 1;
    const row      = ylMap[yl];
    const isActive = row ? Boolean(row.isactive) : false;
    const secCount = _secCountForYL(prog.programcode, yl);
    const savedFmt = row?.section_naming_format || '';
    const pylId    = row?.programyearlevelid ?? 'null';
    const defPfx   = escHtml(prog.programcode + yl);
    /* build inline preview for initial render */
    const pfx      = savedFmt || (prog.programcode + yl);
    const n        = Math.max(1, secCount);
    const initPrev = [pfx, ...Array.from({length: n - 1}, (_, j) => pfx + LETTERS[j])].join(', ');
    return `
    <tr class="pyl-edit-row" id="pylRow_${yl}">
      <td><strong>${_ordinalYear(yl)}</strong></td>
      <td>
        <div style="display:flex;align-items:center;gap:8px;">
          <label class="ep-yl-toggle">
            <input type="checkbox" id="pylActive_${yl}" ${isActive ? 'checked' : ''}
              onchange="_pylStatusChange(${yl})">
            <span class="ep-yl-slider"></span>
          </label>
          <span id="pylStatusLbl_${yl}"
            style="font-size:.74rem;font-weight:700;color:${isActive ? '#2e7d32' : '#c62828'};">
            ${isActive ? 'Active' : 'Inactive'}
          </span>
        </div>
      </td>
      <td>
        <input type="number" id="pylNumSec_${yl}" class="pyl-inline-input"
          value="${n}" min="1" max="26" style="width:60px;"
          oninput="_pylPreview(${yl})">
      </td>
      <td>
        <input type="text" id="pylPrefix_${yl}" class="pyl-inline-input"
          value="${savedFmt}" placeholder="${defPfx}"
          style="width:100px;text-transform:uppercase;"
          oninput="_pylPreview(${yl})"
          data-default="${defPfx}">
      </td>
      <td class="pyl-preview-cell">
        <span id="pylPreview_${yl}" class="pyl-preview-text">${escHtml(initPrev)}</span>
      </td>
      <td style="white-space:nowrap;">
        <button class="pmd-edit-btn pyl-save-btn" id="pylSaveBtn_${yl}"
          onclick="_savePylRow(${pylId},${yl},'${escHtml(prog.programcode)}')">
          <i class="fas fa-save"></i> Save
        </button>
      </td>
    </tr>`;
  }).join('');
}

function _pylStatusChange(yl) {
  const cb  = document.getElementById(`pylActive_${yl}`);
  const lbl = document.getElementById(`pylStatusLbl_${yl}`);
  if (!cb || !lbl) return;
  lbl.textContent = cb.checked ? 'Active' : 'Inactive';
  lbl.style.color = cb.checked ? '#2e7d32' : '#c62828';
}

function _pylPreview(yl) {
  const pfxEl = document.getElementById(`pylPrefix_${yl}`);
  const numEl = document.getElementById(`pylNumSec_${yl}`);
  const prvEl = document.getElementById(`pylPreview_${yl}`);
  if (!pfxEl || !numEl || !prvEl) return;
  const defPfx = pfxEl.getAttribute('data-default') || '';
  const pfx    = (pfxEl.value || '').trim().toUpperCase() || defPfx;
  const n      = Math.max(1, parseInt(numEl.value) || 1);
  const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  const names  = [pfx, ...Array.from({length: n - 1}, (_, i) => pfx + LETTERS[i])];
  prvEl.textContent = names.join(', ');
}

async function _savePylRow(pylId, yl, progCode) {
  const btn  = document.getElementById(`pylSaveBtn_${yl}`);
  const orig = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; }

  if (pylId === null || pylId === 'null') {
    _showToast('error', `Year ${yl} is not configured in the database yet. Re-save the program to generate it.`);
    if (btn) { btn.disabled = false; btn.innerHTML = orig; }
    return;
  }

  const active = document.getElementById(`pylActive_${yl}`)?.checked ?? false;
  const numSec = document.getElementById(`pylNumSec_${yl}`)?.value  || '1';
  const pfxEl  = document.getElementById(`pylPrefix_${yl}`);
  const fmt    = (pfxEl?.value || '').trim().toUpperCase();

  const fd = new FormData();
  fd.append('pyl_id',               pylId);
  fd.append('isactive',             active ? 'true' : 'false');
  fd.append('num_sections',         numSec);
  fd.append('section_naming_format', fmt);

  try {
    const resp = await fetch('/admin/settings/program/yearlevel/update', {
      method: 'POST', body: fd,
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    });
    const data = await resp.json();
    if (data.success) {
      /* Update yearlevels cache */
      const ylRow = (PM_DATA.yearlevels || []).find(y => y.programyearlevelid === Number(pylId));
      if (ylRow) {
        ylRow.isactive              = data.isactive;
        ylRow.active_section_count  = data.active_section_count;
        ylRow.section_naming_format = data.section_naming_format || '';
      }
      /* Sync sections cache for this pyl */
      const newSecs = data.sections || [];
      PM_DATA.sections = [
        ...(PM_DATA.sections || []).filter(s => s.programyearlevelid !== Number(pylId)),
        ...newSecs.map(s => ({
          ...s,
          programcode:        data.prog_code,
          programyearlevelid: Number(pylId),
          yearlevel:          yl,
          curriculumcode:     '',
        })),
      ];
      /* Sync program status */
      const prog = PM_DATA.programs.find(p => p.programcode === data.prog_code);
      if (prog) {
        prog.isactive      = data.prog_isactive;
        prog.section_count = (PM_DATA.sections || []).filter(
          s => s.programcode === data.prog_code && s.isactive
        ).length;
      }
      _syncProgramStatusBadge(data.prog_code, data.prog_isactive);
      _renderHeader(prog || {});
      _renderYearLevelsGrid(prog);
      _PM_CURRENT_SECTIONS = _deriveSections(PM_SELECTED);
      _renderSectionsPage(1);
      _showToast('success',
        `${_ordinalYear(yl)} saved — ${data.active_section_count} section(s)${data.isactive ? ', Active' : ', Inactive'}.`);
    } else {
      _showToast('error', data.error || 'Failed to save year level.');
      if (btn) { btn.disabled = false; btn.innerHTML = orig; }
    }
  } catch (err) {
    _showToast('error', 'Error: ' + err.message);
    if (btn) { btn.disabled = false; btn.innerHTML = orig; }
  }
}

/* ── Year Level filter in Sections panel ─────────────── */
function _populateYLFilter(progCode, numYL) {
  const sel = document.getElementById('pmdSecYLFilter');
  if (!sel) return;
  sel.innerHTML = '<option value="">All Year Levels</option>';
  const count = parseInt(numYL) || 0;
  for (let i = 1; i <= count; i++) {
    const opt = document.createElement('option');
    opt.value = i; opt.textContent = `Year ${i}`;
    sel.appendChild(opt);
  }
}

/* ── Sections table ─────────────────────────────────── */
function _renderSectionsPage(page) {
  const tbody   = document.getElementById('pmdSectionsBody');
  const paginEl = document.getElementById('pmdSecPagination');
  const titleEl = document.getElementById('pmdSectionsTitle');
  if (!tbody) return;

  const fv  = document.getElementById('pmdSecFilter')?.value ?? '';
  const fyl = document.getElementById('pmdSecYLFilter')?.value ?? '';
  let secs  = _PM_CURRENT_SECTIONS;
  if (fyl)          secs = secs.filter(s => String(s.yearlevel) === String(fyl));
  if (fv === 'true')  secs = secs.filter(s => s.has_schedule === true);
  if (fv === 'false') secs = secs.filter(s => s.has_schedule === false);

  const total = secs.length;
  const pages = Math.max(1, Math.ceil(total / _PM_SEC_PER_PAGE));
  page = Math.max(1, Math.min(page, pages));
  const start = (page - 1) * _PM_SEC_PER_PAGE;
  const items = secs.slice(start, start + _PM_SEC_PER_PAGE);

  const _ayLabel = PM_DATA.active_ay_id ? ` · ${PM_DATA.active_ay_id}` : '';
  if (titleEl) titleEl.textContent = `${total} Section${total !== 1 ? 's' : ''}${_ayLabel}`;

  if (total === 0) {
    tbody.innerHTML = `<tr><td colspan="4" class="pmd-empty-row">No sections found.</td></tr>`;
    if (paginEl) paginEl.innerHTML = '';
    return;
  }

  tbody.innerHTML = items.map(s => {
    const sCls  = s.has_schedule ? 'pmd-status-active' : 'pmd-status-inactive';
    const sLbl  = s.has_schedule ? 'Active' : 'Inactive';
    const secId = s.sectionid || '';
    const pylId = s.programyearlevelid || '';
    const sName = s.sectionname.replace(/'/g, "\\'");
    return `
    <tr>
      <td><strong>${escHtml(s.sectionname)}</strong></td>
      <td>${_ordinalYear(s.yearlevel)}</td>
      <td><span class="pmd-status-badge ${sCls}"><span class="pmd-dot"></span>${sLbl}</span></td>
      <td style="white-space:nowrap;">
        <button class="pmd-edit-btn" onclick="openEditSection('${secId}','${sName}',${pylId})">
          <i class="fas fa-edit"></i> Edit</button>
        <button class="pmd-del-btn"  onclick="openDeleteSection('${secId}','${sName}',${pylId})" style="margin-left:6px;">
          <i class="fas fa-trash"></i> Delete</button>
      </td>
    </tr>`;
  }).join('');

  if (paginEl) {
    const end = Math.min(start + _PM_SEC_PER_PAGE, total);
    let h = `<span class="pmd-page-info">Showing ${start+1}–${end} of ${total}</span><div class="pmd-page-btns">`;
    h += `<button class="pmd-page-btn" onclick="_renderSectionsPage(${page-1})" ${page===1?'disabled':''}>&#8249;</button>`;
    let ps = Math.max(1, page-2), pe = Math.min(pages, ps+4); ps = Math.max(1, pe-4);
    if (ps > 1)    h += `<button class="pmd-page-btn" onclick="_renderSectionsPage(1)">1</button>`;
    if (ps > 2)    h += `<span class="pmd-page-ellipsis">&hellip;</span>`;
    for (let p=ps; p<=pe; p++) h += `<button class="pmd-page-btn${p===page?' active':''}" onclick="_renderSectionsPage(${p})">${p}</button>`;
    if (pe < pages-1) h += `<span class="pmd-page-ellipsis">&hellip;</span>`;
    if (pe < pages)   h += `<button class="pmd-page-btn" onclick="_renderSectionsPage(${pages})">${pages}</button>`;
    h += `<button class="pmd-page-btn" onclick="_renderSectionsPage(${page+1})" ${page===pages?'disabled':''}>&#8250;</button></div>`;
    paginEl.innerHTML = h;
  }
}

/* ── Program CRUD ───────────────────────────────────── */
function openEditProgram() {
  const prog = PM_DATA.programs.find(p => p.programcode === PM_SELECTED);
  if (!prog) return;
  document.getElementById('editProgCode').textContent    = prog.programcode;
  document.getElementById('editProgCodeInput').value     = prog.programcode;
  document.getElementById('editProgName').value          = prog.programname;
  document.getElementById('editProgType').value          = prog.programtype || 'Undergraduate';
  document.getElementById('editProgYrs').value           = prog.numyearlevel || 4;
  openSModal('modalEditProgram');
}

function confirmDeleteProgram() {
  if (!PM_SELECTED) return;
  document.getElementById('delProgCode').value        = PM_SELECTED;
  document.getElementById('delProgLabel').textContent = `Program: ${PM_SELECTED}`;
  openSModal('modalDeleteProgram');
}

function confirmActivateProgram() {
  if (!PM_SELECTED) return;
  document.getElementById('actProgCode').value        = PM_SELECTED;
  document.getElementById('actProgLabel').textContent = `Program: ${PM_SELECTED}`;
  openSModal('modalActivateProgram');
}

function doActivateProgram() {
  const code = document.getElementById('actProgCode').value;
  if (!code) return;
  const fd = new FormData();
  fd.append('program_code', code);
  fd.append('isactive', 'true');
  fetch('/admin/settings/program/toggle-active', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(d => {
      closeSModal('modalActivateProgram');
      if (d.success) location.reload();
      else alert(d.error || 'Failed to activate program.');
    });
}

/* ── Add Section (standalone) ───────────────────────── */
function openAddSection() {
  if (!PM_SELECTED) return;
  const prog = PM_DATA.programs.find(p => p.programcode === PM_SELECTED);
  document.getElementById('addSecProgLabel').textContent = PM_SELECTED;
  document.getElementById('addSecProgCode').value        = PM_SELECTED;

  /* Populate year level select */
  const ylSel = document.getElementById('addSecYearLevel');
  if (ylSel) {
    const numYL = parseInt(prog?.numyearlevel) || 4;
    ylSel.innerHTML = Array.from({length: numYL}, (_, i) => {
      const yl = i + 1;
      return `<option value="${yl}">${_ordinalYear(yl)}</option>`;
    }).join('');
    ylSel.value = '1';
  }

  if (document.getElementById('addSecName')) document.getElementById('addSecName').value = '';
  if (document.getElementById('addSecError')) document.getElementById('addSecError').style.display = 'none';
  openSModal('modalAddSection');
}

/* ── Edit section modal ─────────────────────────────── */
function openEditSection(sectionId, sectionName, pylId) {
  document.getElementById('editSecId').value    = sectionId  || '';
  document.getElementById('editSecName').value  = sectionName;
  document.getElementById('editSecPylId').value = pylId || '';
  const errWrap = document.getElementById('editSecErrorWrap');
  if (errWrap) errWrap.style.display = 'none';
  openSModal('modalEditSection');
}

async function _submitEditSection(e) {
  e.preventDefault();
  const secId   = document.getElementById('editSecId').value.trim();
  const pylId   = document.getElementById('editSecPylId').value.trim();
  const newName = document.getElementById('editSecName').value.trim();
  const errWrap = document.getElementById('editSecErrorWrap');
  const errEl   = document.getElementById('editSecError');
  if (errWrap) errWrap.style.display = 'none';

  const btn  = document.querySelector('#formEditSection [type="submit"]');
  const orig = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; }

  try {
    const fd = new FormData();
    if (secId)  fd.append('section_id', secId);
    if (pylId)  fd.append('pyl_id', pylId);
    fd.append('section_name', newName);

    const resp = await fetch('/admin/settings/section/edit', {
      method: 'POST', body: fd,
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    });
    const ct = resp.headers.get('content-type') || '';
    if (!ct.includes('application/json')) throw new Error(`Server returned status ${resp.status}.`);
    const data = await resp.json();

    if (data.success) {
      const match = _PM_CURRENT_SECTIONS.find(s => String(s.sectionid) === String(secId));
      if (match) { match.sectionname = data.sectionname; match.sectionid = data.sectionid; }
      const pm = (PM_DATA.sections || []).find(s => String(s.sectionid) === String(secId));
      if (pm)    { pm.sectionname = data.sectionname; }
      _renderSectionsPage(1);
      closeSModal('modalEditSection');
      _showToast('success', `Section renamed to "${data.sectionname}".`);
    } else {
      if (errWrap) errWrap.style.display = '';
      if (errEl)   errEl.textContent = data.error || 'Failed to rename section.';
    }
  } catch (err) {
    _showToast('error', 'Error: ' + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = orig; }
  }
}

/* ── Delete section modal ───────────────────────────── */
let _DELETE_SEC_ID   = null;
let _DELETE_SEC_NAME = null;
let _DELETE_SEC_PYL  = null;

function openDeleteSection(sectionId, sectionName, pylId) {
  _DELETE_SEC_ID   = sectionId || null;
  _DELETE_SEC_NAME = sectionName;
  _DELETE_SEC_PYL  = pylId;
  const nameEl = document.getElementById('delSecName');
  if (nameEl) nameEl.textContent = sectionName;
  const errWrap = document.getElementById('delSecErrorWrap');
  if (errWrap) errWrap.style.display = 'none';
  const btn = document.getElementById('btnConfirmSecDelete');
  if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-trash"></i> Delete'; }
  openSModal('modalDeleteSection');
}

async function _confirmDeleteSection() {
  const btn  = document.getElementById('btnConfirmSecDelete');
  const orig = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Deleting…'; }

  try {
    const fd = new FormData();
    if (_DELETE_SEC_ID) fd.append('section_id', _DELETE_SEC_ID);

    const resp = await fetch('/admin/settings/section/delete', {
      method: 'POST', body: fd,
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    });
    const ct = resp.headers.get('content-type') || '';
    if (!ct.includes('application/json')) throw new Error(`Server returned status ${resp.status}.`);
    const data = await resp.json();

    if (data.success) {
      const sid = Number(data.sectionid);
      if (data.mode === 'hard') {
        PM_DATA.sections = (PM_DATA.sections || []).filter(s => s.sectionid !== sid);
      } else {
        const sec = (PM_DATA.sections || []).find(s => s.sectionid === sid);
        if (sec) sec.isactive = false;
      }
      // Update program
      const prog = PM_DATA.programs.find(p => p.programcode === data.prog_code);
      if (prog) {
        prog.isactive      = data.prog_isactive;
        prog.section_count = (PM_DATA.sections || []).filter(s => s.programcode === data.prog_code && s.isactive).length;
      }
      _syncProgramStatusBadge(data.prog_code, data.prog_isactive);
      _renderHeader(prog || {});
      _renderYearLevelsGrid(prog);  /* uses live _secCountForYL — auto-reflects */
      _PM_CURRENT_SECTIONS = _deriveSections(PM_SELECTED);
      _renderSectionsPage(1);
      closeSModal('modalDeleteSection');
      _showToast('success', `Section "${_DELETE_SEC_NAME}" deleted.`);
    } else {
      const errWrap = document.getElementById('delSecErrorWrap');
      const errEl   = document.getElementById('delSecError');
      if (errWrap) errWrap.style.display = '';
      if (errEl)   errEl.textContent = data.error || 'Delete failed.';
      if (btn) { btn.disabled = false; btn.innerHTML = orig; }
    }
  } catch (err) {
    _showToast('error', 'Delete failed: ' + err.message);
    if (btn) { btn.disabled = false; btn.innerHTML = orig; }
  }
}

/* ── Sync left-panel program status badge ───────────── */
function _syncProgramStatusBadge(progCode, isActive) {
  const item = document.querySelector(`.pm-list-item[data-code="${CSS.escape(progCode)}"]`);
  const span = item?.querySelector('.pm-item-status');
  if (span) {
    span.className = `pm-item-status ${isActive ? 'pm-item-active' : 'pm-item-inactive'}`;
    span.innerHTML = `<span class="pm-dot-sm"></span> ${isActive ? 'Active' : 'Inactive'}`;
  }
}

/* ── Toast notification ─────────────────────────────── */
function _showToast(type, msg) {
  let wrap = document.getElementById('pmToastWrap');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.id = 'pmToastWrap';
    wrap.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:8px;';
    document.body.appendChild(wrap);
  }
  const t  = document.createElement('div');
  const bg = type === 'success' ? '#2e7d32' : '#c62828';
  t.style.cssText = `background:${bg};color:#fff;padding:12px 20px;border-radius:8px;font-size:.85rem;font-weight:600;
    box-shadow:0 4px 12px rgba(0,0,0,.25);max-width:340px;opacity:0;transition:opacity .25s;`;
  t.textContent = msg;
  wrap.appendChild(t);
  requestAnimationFrame(() => { t.style.opacity = '1'; });
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 4000);
}

/* ── Init ───────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', async () => {
  const savedTab = localStorage.getItem('settings_active_tab');
  if (savedTab && document.getElementById('tab-' + savedTab)) _activateTab(savedTab);

  markTableActionsLocked();
  await initHCToggles();
  initDayPairs();
  initTimeSlots();
  initSectionPairs();
  loadMergeLoadPolicies();
  initProgramPanel();

  /* Wire AJAX submit for Edit Section rename form */
  const editSecForm = document.getElementById('formEditSection');
  if (editSecForm) editSecForm.addEventListener('submit', _submitEditSection);
});
