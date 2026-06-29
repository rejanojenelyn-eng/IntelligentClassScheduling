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
function openAYModal() {
  document.getElementById('ay_modal_title').innerText    = 'ADD NEW ACADEMIC YEAR';
  document.getElementById('ay_submit_btn').textContent   = 'ADD CALENDAR';
  document.querySelectorAll('#modalAY input[type="date"]').forEach(i => { i.value = ''; i.removeAttribute('min'); });
  document.getElementById('ay_year_start').value = '';
  document.getElementById('ay_year_end').value   = '';
  document.getElementById('ay_tag_display').value = '';
  openEditModal('modalAY');
}

/* Parse the typed tag (e.g. "AY 25-26", "25-26", "2025-2026") into hidden year fields */
document.getElementById('ay_tag_display')?.addEventListener('input', function() {
  const raw = this.value.replace(/AY\s*/i, '').trim();
  const parts = raw.split(/[-\s]+/);
  if (parts.length < 2) return;
  let y1 = parts[0].trim(), y2 = parts[1].trim();
  if (y1.length === 2) y1 = '20' + y1;
  if (y2.length === 2) y2 = '20' + y2;
  if (y1.length === 4 && y2.length === 4 && !isNaN(y1) && !isNaN(y2)) {
    document.getElementById('ay_year_start').value = y1;
    document.getElementById('ay_year_end').value   = y2;
  }
});

/* Auto-fill tag from 1st sem start date (overrides manual input only when date changes) */
document.getElementById('ay_s1s')?.addEventListener('change', function() {
  const yr = new Date(this.value).getFullYear();
  if (!yr || isNaN(yr)) return;
  const yy1 = String(yr).slice(2);
  const yy2 = String(yr + 1).slice(2);
  document.getElementById('ay_tag_display').value = `AY ${yy1}-${yy2}`;
  document.getElementById('ay_year_start').value = yr;
  document.getElementById('ay_year_end').value   = yr + 1;
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
    document.getElementById('ay_modal_title').innerText  = isFinalized ? 'VIEW ACADEMIC YEAR' : 'EDIT ACADEMIC YEAR';
    document.getElementById('ay_submit_btn').textContent = 'SAVE CALENDAR';
    document.getElementById('ay_year_start').value = d.start;
    document.getElementById('ay_year_end').value   = d.end;
    const yy1 = d.start?.slice(2), yy2 = d.end?.slice(2);
    document.getElementById('ay_tag_display').value = `AY ${yy1}-${yy2}`;
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
  if (fv === 'true')  secs = secs.filter(s => s.isactive === true);
  if (fv === 'false') secs = secs.filter(s => s.isactive === false);

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
    const sCls  = s.isactive ? 'pmd-status-active' : 'pmd-status-inactive';
    const sLbl  = s.isactive ? 'Active' : 'Inactive';
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
  initProgramPanel();

  /* Wire AJAX submit for Edit Section rename form */
  const editSecForm = document.getElementById('formEditSection');
  if (editSecForm) editSecForm.addEventListener('submit', _submitEditSection);
});
