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
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.tab;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + target)?.classList.add('active');
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
    modalEditEmpType: { sub: 'Employee Faculty Hours',      field: 'et' },
    modalEditDesig:   { sub: 'Designee Faculty Hours',      field: 'desig' },
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
  for (let h = 6; h <= 22; h++) {
    for (let m = 0; m < 60; m += 30) {
      const hh   = h % 12 || 12;
      const ampm = h < 12 ? 'AM' : 'PM';
      const mm   = m === 0 ? '00' : '30';
      const val  = `${String(h).padStart(2,'0')}:${mm}`;
      list.push({ val, label: `${hh}:${mm} ${ampm}` });
    }
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
  document.querySelectorAll('#modalAY input[type="date"]').forEach(i => i.value = '');
  document.getElementById('ay_year_start').value = '';
  document.getElementById('ay_year_end').value   = '';
  document.getElementById('ay_tag_display').textContent = 'AY __ - __';
  openEditModal('modalAY');
}

document.getElementById('ay_s1s')?.addEventListener('change', function() {
  const yr = new Date(this.value).getFullYear();
  if (!yr || isNaN(yr)) return;
  const yy1 = String(yr).slice(2);
  const yy2 = String(yr + 1).slice(2);
  document.getElementById('ay_tag_display').textContent = `AY ${yy1}-${yy2}`;
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
    document.getElementById('ay_tag_display').textContent = `AY ${yy1}-${yy2}`;
    document.getElementById('ay_s1s').value = clean(d.s1s);
    document.getElementById('ay_s1e').value = clean(d.s1e);
    document.getElementById('ay_s2s').value = clean(d.s2s);
    document.getElementById('ay_s2e').value = clean(d.s2e);
    document.getElementById('ay_s3s').value = clean(d.s3s);
    document.getElementById('ay_s3e').value = clean(d.s3e);
  };

  if (isFinalized) {
    populate();
    openViewOnlyModal('modalAY');
    const text = document.getElementById('ay-view-notice-text');
    if (text) text.textContent = 'This Academic Year is finalized and cannot be edited.';
    return;
  }

  if (computedStatus === 'past') {
    /* Past AY — warn before allowing edits */
    _pendingAYPopulate = populate;
    openSModal('modalAYPast');
    return;
  }

  if (hasPub) {
    /* Current/upcoming AY with published schedules — show active-schedule warning */
    _pendingAYPopulate = populate;
    openSModal('modalAYWarning');
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
  const populate = () => {
    document.getElementById('et_name_label').innerText = d.name;
    document.getElementById('modal_et_id').value       = d.id;
    document.getElementById('modal_et_reg').value      = clean(d.rl)  || '';
    document.getElementById('modal_et_pt').value       = clean(d.ptl) || '';
    document.getElementById('modal_et_sub').value      = clean(d.sub) || '';
    buildTimeSelect(document.getElementById('modal_et_rs'), clean(d.rs) || null);
    buildTimeSelect(document.getElementById('modal_et_re'), clean(d.re) || null);
    buildTimeSelect(document.getElementById('modal_et_ps'), clean(d.ps) || null);
    buildTimeSelect(document.getElementById('modal_et_pe'), clean(d.pe) || null);
  };
  validateAndOpen('modalEditEmpType', populate);
}

/* ── DESIGNEE MODALS ────────────────────────────────────── */

/* Pre-build time selects */
['desig_reg_from','desig_reg_to','desig_pt_from','desig_pt_to'].forEach(id => {
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
  buildTimeSelect(document.getElementById(prefix + '_pt_from'),  nt > 0 ? '16:30' : '08:00');
  buildTimeSelect(document.getElementById(prefix + '_pt_to'),    nt > 0 ? '18:00' : '17:00');
}

function updateAddDesigHours() {
  const nt = parseInt(document.getElementById('add_des_night').value) || 0;
  document.getElementById('add_des_reg_band').value = nt > 0 ? '07:30 AM - 04:30 PM' : '08:00 AM - 05:00 PM';
  document.getElementById('add_des_pt_band').value  = nt > 0 ? '04:30 PM - 06:00 PM' : 'None (Weekdays)';
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

let PM_DATA      = { programs: [], curricula: [], sections: [], offerings: [] };
let PM_SELECTED  = null;   /* currently selected programcode */

function initProgramPanel() {
  const raw = document.getElementById('pm-data');
  if (!raw) return;
  try { PM_DATA = JSON.parse(raw.textContent); } catch { return; }
  /* Auto-select first program if available */
  if (PM_DATA.programs.length > 0) selectProgram(PM_DATA.programs[0].programcode);
}

/* ── Search / filter ────────────────────────────────── */
function filterProgramList() {
  const q = (document.getElementById('pmSearch')?.value || '').toLowerCase();
  document.querySelectorAll('.pm-list-item').forEach(item => {
    const code = (item.dataset.code || '').toLowerCase();
    const name = item.querySelector('.pm-item-name')?.textContent.toLowerCase() || '';
    item.style.display = (code.includes(q) || name.includes(q)) ? '' : 'none';
  });
}

/* Returns all academic_offering rows for a given programcode */
function _offeringsFor(programcode) {
  return (PM_DATA.offerings || []).filter(o => o.programcode === programcode);
}

/* ── Pagination + current section cache ─────────────── */
let _PM_CURRENT_SECTIONS = [];
const _PM_SEC_PER_PAGE   = 4;

/* ── Select program → render new right panel ─────── */
function selectProgram(code) {
  PM_SELECTED = code;
  document.querySelectorAll('.pm-list-item').forEach(el => {
    el.classList.toggle('active', el.dataset.code === code);
  });
  const prog = PM_DATA.programs.find(p => p.programcode === code);
  if (!prog) return;

  document.getElementById('pmEmpty').style.display         = 'none';
  document.getElementById('pmDetailContent').style.display = 'block';

  const progOfferings = _offeringsFor(code);
  const offeringCodes = progOfferings.map(o => o.offeringcode);
  const progSections  = (PM_DATA.sections || []).filter(s => offeringCodes.includes(s.programcode));

  _renderHeader(prog);
  _renderProgInfo(prog);
  _renderOfferingsTable(progOfferings);
  _PM_CURRENT_SECTIONS = progSections;
  const fEl = document.getElementById('pmdSecFilter');
  if (fEl) fEl.value = '';
  _renderSectionsPage(1);
}

function _renderHeader(prog) {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set('pdCode',           prog.programcode);
  set('pdName',           prog.programname);
  set('pdCodeBadge',      prog.programcode);
  set('pdStatOfferings',  prog.offering_count  ?? '0');
  set('pdStatYearLevels', prog.yearlevel_count ?? '0');
  set('pdStatSections',   prog.section_count   ?? '0');

  const statusEl = document.getElementById('pdStatusBadge');
  if (statusEl) {
    statusEl.className = `pmd-status-badge ${prog.isactive ? 'pmd-status-active' : 'pmd-status-inactive'}`;
    statusEl.innerHTML = `<span class="pmd-dot"></span> ${prog.isactive ? 'Active' : 'Inactive'}`;
  }
}

function _renderProgInfo(prog) {
  const tbody = document.getElementById('pmdInfoBody');
  if (!tbody) return;
  const sBadge = `<span class="pmd-status-badge ${prog.isactive ? 'pmd-status-active' : 'pmd-status-inactive'}"><span class="pmd-dot"></span>${prog.isactive ? 'Active' : 'Inactive'}</span>`;
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

function _renderOfferingsTable(offerings) {
  const tbody  = document.getElementById('pmdOfferingsBody');
  const footer = document.getElementById('pmdOfferingsFooter');
  if (!tbody) return;

  if (offerings.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" class="pmd-empty-row">No academic offerings yet. Click + ADD OFFERING to create one.</td></tr>`;
    if (footer) footer.textContent = '';
    return;
  }

  tbody.innerHTML = offerings.map(o => {
    const isTrack  = !!o.trackcode;
    const typeLbl  = isTrack ? (o.trackname || o.trackcode) : 'Base (General)';
    const typeCls  = isTrack ? 'pmd-type-track' : 'pmd-type-base';
    const sCls     = o.isactive ? 'pmd-status-active' : 'pmd-status-inactive';
    const sLbl     = o.isactive ? 'Active' : 'Inactive';
    const editFn   = isTrack
      ? `openEditOffering(${o.academicofferingid},'${escHtml(o.offeringcode)}','${escHtml(o.trackname||'')}','${escHtml(o.trackcode||'')}',${!!o.isactive})`
      : `openEditOffering(${o.academicofferingid},'${escHtml(o.offeringcode)}','','',${!!o.isactive})`;
    return `
    <tr>
      <td><strong>${escHtml(o.offeringcode)}</strong><span class="pmd-type-chip ${typeCls}">${escHtml(typeLbl)}</span></td>
      <td>${o.section_count || 0}</td>
      <td><span class="pmd-status-badge ${sCls}"><span class="pmd-dot"></span>${sLbl}</span></td>
      <td><button class="pmd-edit-btn" onclick="${editFn}"><i class="fas fa-edit"></i> Edit</button></td>
    </tr>`;
  }).join('');

  if (footer) footer.textContent = `Showing 1 to ${offerings.length} of ${offerings.length} offering${offerings.length !== 1 ? 's' : ''}`;
}

function _renderSectionsPage(page) {
  const tbody   = document.getElementById('pmdSectionsBody');
  const paginEl = document.getElementById('pmdSecPagination');
  const titleEl = document.getElementById('pmdSectionsTitle');
  if (!tbody) return;

  const fv   = document.getElementById('pmdSecFilter')?.value ?? '';
  let secs   = _PM_CURRENT_SECTIONS;
  if (fv === 'true')  secs = secs.filter(s => s.isactive === true);
  if (fv === 'false') secs = secs.filter(s => s.isactive === false);

  const total = secs.length;
  const pages = Math.max(1, Math.ceil(total / _PM_SEC_PER_PAGE));
  page = Math.max(1, Math.min(page, pages));
  const start = (page - 1) * _PM_SEC_PER_PAGE;
  const items = secs.slice(start, start + _PM_SEC_PER_PAGE);

  if (titleEl) titleEl.textContent = `${total}. Sections`;

  if (total === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="pmd-empty-row">No sections found.</td></tr>`;
    if (paginEl) paginEl.innerHTML = '';
    return;
  }

  tbody.innerHTML = items.map(s => {
    const yr   = _ordinalYear(s.yearlevel);
    const sCls = s.isactive ? 'pmd-status-active' : 'pmd-status-inactive';
    const sLbl = s.isactive ? 'Active' : 'Inactive';
    return `
    <tr>
      <td><strong>${escHtml(s.sectionname)}</strong></td>
      <td>${escHtml(s.programcode)}</td>
      <td>${yr}</td>
      <td><span class="pmd-status-badge ${sCls}"><span class="pmd-dot"></span>${sLbl}</span></td>
      <td class="pmd-sec-actions">
        <button class="pmd-icon-sm" title="Edit" onclick="openEditSection(${s.sectionid},'${escHtml(s.sectionname)}')"><i class="fas fa-edit"></i></button>
        <button class="pmd-icon-sm pmd-icon-del" title="Deactivate" onclick="deactivateSection(${s.sectionid},'${escHtml(s.sectionname)}')"><i class="fas fa-trash"></i></button>
      </td>
    </tr>`;
  }).join('');

  if (paginEl) {
    const end = Math.min(start + _PM_SEC_PER_PAGE, total);
    let h = `<span class="pmd-page-info">Showing ${start+1} to ${end} of ${total} sections</span><div class="pmd-page-btns">`;
    h += `<button class="pmd-page-btn" onclick="_renderSectionsPage(${page-1})" ${page===1?'disabled':''}>&#8249;</button>`;
    let ps = Math.max(1, page-2), pe = Math.min(pages, ps+4); ps = Math.max(1, pe-4);
    if (ps > 1)     h += `<button class="pmd-page-btn" onclick="_renderSectionsPage(1)">1</button>`;
    if (ps > 2)     h += `<span class="pmd-page-ellipsis">&hellip;</span>`;
    for (let p=ps; p<=pe; p++) h += `<button class="pmd-page-btn${p===page?' active':''}" onclick="_renderSectionsPage(${p})">${p}</button>`;
    if (pe < pages-1) h += `<span class="pmd-page-ellipsis">&hellip;</span>`;
    if (pe < pages)   h += `<button class="pmd-page-btn" onclick="_renderSectionsPage(${pages})">${pages}</button>`;
    h += `<button class="pmd-page-btn" onclick="_renderSectionsPage(${page+1})" ${page===pages?'disabled':''}>&#8250;</button></div>`;
    paginEl.innerHTML = h;
  }
}

function _ordinalYear(num) {
  const n = parseInt(num);
  if (isNaN(n)) return String(num) + ' Year';
  const sfx = ['th','st','nd','rd'];
  const v = n % 100;
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

function formatCY(year) {
  /* "2025-2026" → "25-26" */
  if (!year) return '—';
  const parts = year.split('-');
  if (parts.length === 2) return parts[0].slice(-2) + '-' + parts[1].slice(-2);
  return year;
}

function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ── Sections rendering ─────────────────────────────── */
function renderSections(sections, numYearLevels) {
  const container = document.getElementById('pdSections');
  if (!container) return;

  if (sections.length === 0) {
    container.innerHTML = `<div class="pm-no-tracks">No sections added yet. Click + ADD SECTION to begin.</div>`;
    return;
  }

  // Group by year level
  const byYear = {};
  sections.forEach(s => {
    const yl = s.yearlevel || '?';
    if (!byYear[yl]) byYear[yl] = [];
    byYear[yl].push(s);
  });

  container.innerHTML = Object.keys(byYear).sort((a, b) => +a - +b).map(yl => `
    <div class="pm-sec-year-group">
      <div class="pm-sec-year-hd">YEAR ${escHtml(yl)}</div>
      <div class="pm-sec-rows">
        ${byYear[yl].map(s => `
          <div class="pm-sec-row">
            <span class="pm-sec-name">${escHtml(s.sectionname)}</span>
            <button type="button" class="pm-icon-btn pm-del" title="Deactivate"
              onclick="deactivateSection(${s.sectionid},'${escHtml(s.sectionname)}')">
              <i class="fas fa-trash"></i>
            </button>
          </div>`).join('')}
      </div>
    </div>`).join('');
}

function renderEditModalSections(sections) {
  const container = document.getElementById('editProgSectionsList');
  if (!container) return;

  if (sections.length === 0) {
    container.innerHTML = `<div class="ep-no-sections">No sections yet. Use + ADD SECTION above.</div>`;
    return;
  }

  const byYear = {};
  sections.forEach(s => {
    const yl = s.yearlevel || '?';
    if (!byYear[yl]) byYear[yl] = [];
    byYear[yl].push(s);
  });

  container.innerHTML = Object.keys(byYear).sort((a, b) => +a - +b).map(yl => `
    <div class="ep-sec-group">
      <span class="ep-yr-badge">YR ${escHtml(yl)}</span>
      <div class="ep-sec-chips">
        ${byYear[yl].map(s => `
          <span class="ep-sec-chip">
            ${escHtml(s.sectionname)}
            <button type="button" class="ep-sec-del"
              onclick="deactivateSection(${s.sectionid},'${escHtml(s.sectionname)}')">
              <i class="fas fa-times"></i>
            </button>
          </span>`).join('')}
      </div>
    </div>`).join('');
}

function deactivateSection(id, name) {
  if (!confirm(`Deactivate section "${name}"? It will be removed from scheduling.`)) return;
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = '/admin/settings/section/deactivate';
  const inp = document.createElement('input');
  inp.type  = 'hidden'; inp.name = 'section_id'; inp.value = id;
  form.appendChild(inp);
  document.body.appendChild(form);
  form.submit();
}

function openAddSection() {
  if (!PM_SELECTED) return;
  const prog = PM_DATA.programs.find(p => p.programcode === PM_SELECTED);
  const offerings = _offeringsFor(PM_SELECTED);

  document.getElementById('addSecProgLabel').textContent = PM_SELECTED;

  const wrap = document.getElementById('addSecOfferingWrap');
  const sel  = document.getElementById('addSecOfferingSelect');

  if (offerings.length <= 1) {
    /* Single / no track — use offeringcode directly (same as programcode for base programs) */
    const oc = offerings.length === 1 ? offerings[0].offeringcode : PM_SELECTED;
    document.getElementById('addSecProgCode').value = oc;
    if (wrap) wrap.style.display = 'none';
  } else {
    /* Multiple offerings — show selector */
    sel.innerHTML = offerings.map(o =>
      `<option value="${escHtml(o.offeringcode)}">${escHtml(o.offeringcode)}${o.trackname ? ' — ' + escHtml(o.trackname) : ''}</option>`
    ).join('');
    document.getElementById('addSecProgCode').value = offerings[0].offeringcode;
    if (wrap) wrap.style.display = '';
  }

  /* Year level options up to program's numyearlevel */
  const yrSel = document.getElementById('addSecYearLevel');
  yrSel.innerHTML = '';
  const maxYr = prog ? (prog.numyearlevel || 4) : 4;
  for (let i = 1; i <= maxYr; i++) {
    const opt = document.createElement('option');
    opt.value = i; opt.textContent = `Year ${i}`;
    yrSel.appendChild(opt);
  }
  openSModal('modalAddSection');
}

/* ── Program CRUD openers ───────────────────────────── */
function openEditProgram() {
  const prog = PM_DATA.programs.find(p => p.programcode === PM_SELECTED);
  if (!prog) return;
  document.getElementById('editProgCode').textContent     = prog.programcode;
  document.getElementById('editProgCodeInput').value      = prog.programcode;
  document.getElementById('editProgName').value           = prog.programname;
  document.getElementById('editProgType').value           = prog.programtype || 'Undergraduate';
  document.getElementById('editProgYrs').value            = prog.numyearlevel || 4;

  /* Filter sections: match any offeringcode that belongs to this program */
  const offeringCodes = _offeringsFor(PM_SELECTED).map(o => o.offeringcode);
  /* Fall back to base programcode match if offerings list is empty */
  const sections = (PM_DATA.sections || []).filter(s =>
    offeringCodes.length > 0 ? offeringCodes.includes(s.programcode) : s.programcode === PM_SELECTED
  );
  renderEditModalSections(sections);

  openSModal('modalEditProgram');
}

function confirmDeleteProgram() {
  if (!PM_SELECTED) return;
  document.getElementById('delProgCode').value     = PM_SELECTED;
  document.getElementById('delProgLabel').textContent = `Program: ${PM_SELECTED}`;
  openSModal('modalDeleteProgram');
}

function confirmActivateProgram() {
  if (!PM_SELECTED) return;
  document.getElementById('actProgCode').value       = PM_SELECTED;
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

/* ── Track / Curriculum CRUD openers ────────────────── */
function openAddTrack() {
  if (!PM_SELECTED) return;
  const offerings = _offeringsFor(PM_SELECTED);

  document.getElementById('addTrackProgLabel').textContent = PM_SELECTED;
  document.getElementById('addTrackYear').value = '';
  document.getElementById('addTrackCode').value = '';

  const wrap = document.getElementById('addTrackOfferingWrap');
  const sel  = document.getElementById('addTrackOfferingSelect');

  if (offerings.length <= 1) {
    /* Single / no track — use offeringcode (equals programcode for base programs) */
    const oc = offerings.length === 1 ? offerings[0].offeringcode : PM_SELECTED;
    document.getElementById('addTrackProgCode').value = oc;
    if (wrap) wrap.style.display = 'none';
  } else {
    /* Multiple offerings — let user pick which offering this curriculum is for */
    sel.innerHTML = offerings.map(o =>
      `<option value="${escHtml(o.offeringcode)}">${escHtml(o.offeringcode)}${o.trackname ? ' — ' + escHtml(o.trackname) : ''}</option>`
    ).join('');
    document.getElementById('addTrackProgCode').value = offerings[0].offeringcode;
    if (wrap) wrap.style.display = '';
  }

  openSModal('modalAddTrack');
}

function openAddMajor() {
  /* Opens the Add Program modal pre-filled with parent prefix */
  document.querySelector('#modalAddProgram input[name="program_code"]').value = PM_SELECTED + '-';
  openSModal('modalAddProgram');
}

function openEditTrack(id, code, year) {
  document.getElementById('editTrackId').value   = id;
  document.getElementById('editTrackCode').value = code;
  document.getElementById('editTrackYear').value = year;
  openSModal('modalEditTrack');
}

function openDeleteTrack(id) {
  document.getElementById('delTrackId').value = id;
  openSModal('modalDeleteTrack');
}

/* ── Ordinal helper ──────────────────────────────────── */
function _ordinal(n) {
  const s = ['th','st','nd','rd'], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]) + ' Year';
}

/* ── Build year-level rows for add/edit modals ───────── */
function _buildYLRows(tbodyId, numYears, existingYLs) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  tbody.innerHTML = '';
  for (let yr = 1; yr <= numYears; yr++) {
    const existing   = existingYLs.find(y => y.yearlevel === yr);
    const secCount   = existing ? (existing.numberofsections || 1) : 1;
    const isActive   = existing ? !!existing.isactive : true;
    const toggleId   = `${tbodyId}_tog_${yr}`;
    const labelId    = `${tbodyId}_lbl_${yr}`;
    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${_ordinal(yr)}</td>
      <td><input type="number" name="sections_yr_${yr}" class="m-input pmd-yl-input"
           value="${secCount}" min="0" max="99" required></td>
      <td>
        <label class="pmd-toggle-switch pmd-toggle-sm">
          <input type="checkbox" name="active_yr_${yr}" id="${toggleId}" value="1"
                 ${isActive ? 'checked' : ''}
                 onchange="document.getElementById('${labelId}').textContent=this.checked?'Active':'Inactive'">
          <span class="pmd-toggle-slider"></span>
          <span class="pmd-toggle-label" id="${labelId}">${isActive ? 'Active' : 'Inactive'}</span>
        </label>
      </td>`;
    tbody.appendChild(row);
  }
}

/* ── New offering / section modal openers ────────────── */
function openAddOffering() {
  if (!PM_SELECTED) return;
  const prog = PM_DATA.programs.find(p => p.programcode === PM_SELECTED);
  if (!prog) return;

  document.getElementById('addOfferProgCode').value = PM_SELECTED;

  // Reset status toggle
  const statusChk = document.getElementById('addOfferStatus');
  const statusLbl = document.getElementById('addOfferStatusLabel');
  if (statusChk) { statusChk.checked = true; }
  if (statusLbl) { statusLbl.textContent = 'Active'; }

  _buildYLRows('addOfferYLBody', prog.numyearlevel || 4, []);
  openSModal('modalAddOffering');
}

function openEditOffering(aoId, code, trackName, trackCode, isActive) {
  document.getElementById('editOfferAoId').value      = aoId;
  document.getElementById('editOfferTrackName').value = trackName;
  document.getElementById('editOfferTrackCode').value = trackCode;

  const statusChk = document.getElementById('editOfferStatus');
  const statusLbl = document.getElementById('editOfferStatusLabel');
  if (statusChk) { statusChk.checked = !!isActive; }
  if (statusLbl) { statusLbl.textContent = isActive ? 'Active' : 'Inactive'; }

  // Load year levels for this offering
  const existingYLs = (PM_DATA.yearlevels || []).filter(y => y.academicofferingid === aoId);
  const prog = PM_DATA.programs.find(p =>
    (PM_DATA.offerings || []).some(o => o.academicofferingid === aoId && o.programcode === p.programcode)
  );
  const numYears = prog ? (prog.numyearlevel || 4) : existingYLs.length || 4;
  _buildYLRows('editOfferYLBody', numYears, existingYLs);

  openSModal('modalEditOffering');
}

/* Sync status toggle label for add-offer modal */
document.addEventListener('change', e => {
  if (e.target.id === 'addOfferStatus') {
    const lbl = document.getElementById('addOfferStatusLabel');
    if (lbl) lbl.textContent = e.target.checked ? 'Active' : 'Inactive';
  }
  if (e.target.id === 'editOfferStatus') {
    const lbl = document.getElementById('editOfferStatusLabel');
    if (lbl) lbl.textContent = e.target.checked ? 'Active' : 'Inactive';
  }
});

function openEditSection(sectionId, sectionName) {
  document.getElementById('editSecId').value   = sectionId;
  document.getElementById('editSecName').value = sectionName;
  openSModal('modalEditSection');
}

function deactivateSection(sectionId, sectionName) {
  if (!confirm(`Deactivate section "${sectionName}"?`)) return;
  const fd = new FormData();
  fd.append('section_id', sectionId);
  fd.append('sectionname', sectionName);
  fd.append('action', 'deactivate');
  fetch('/admin/settings/section/edit', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(d => { if (d.success) location.reload(); else alert(d.error || 'Error'); });
}

/* ── Sections filter change ──────────────────────────── */
document.addEventListener('change', e => {
  if (e.target.id === 'pmdSecFilter') _renderSectionsPage(1);
});

/* ── Init ───────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', async () => {
  markTableActionsLocked();
  /* Load HC config from DB first, then populate UI */
  await initHCToggles();
  initDayPairs();
  initTimeSlots();
  initProgramPanel();
});
