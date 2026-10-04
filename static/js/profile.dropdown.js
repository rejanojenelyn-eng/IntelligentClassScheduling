/* ============================================================
   SHARED PROFILE DROPDOWN & PANELS
   Included in: base.html (Acad), base_faculty.html, base_admin.html
   ============================================================ */

document.addEventListener('DOMContentLoaded', function() {
    /* Close user dropdown when clicking outside */
    document.addEventListener('click', e => {
        const wrap = document.getElementById('upWrap');
        if (wrap && !wrap.contains(e.target)) closeUpDropdown();
        /* Close search results when clicking outside */
        const sw = document.getElementById('globalSearchWrap');
        if (sw && !sw.contains(e.target)) _closeSearchResults();
    });
    _loadUserCard();
    _initGlobalSearch();
});

/* ══════════════════════════════════════════════════════
   USER DROPDOWN
══════════════════════════════════════════════════════ */
let _upOpen = false;
let _profileData = null;

function toggleUpDropdown(event) {
    event.stopPropagation();
    _upOpen ? closeUpDropdown() : openUpDropdown();
}
function openUpDropdown() {
    const dd    = document.getElementById('upDropdown');
    const caret = document.getElementById('upCaret');
    if (dd)    dd.style.display = 'block';
    if (caret) caret.classList.add('open');
    _upOpen = true;
}
function closeUpDropdown() {
    const dd    = document.getElementById('upDropdown');
    const caret = document.getElementById('upCaret');
    if (dd)    dd.style.display = 'none';
    if (caret) caret.classList.remove('open');
    _upOpen = false;
}

async function _loadUserCard() {
    try {
        const res  = await fetch('/api/user/me');
        const data = await res.json();
        if (!data.success) return;
        _profileData = data;
        _applyAvatar('navAvatar', 'navAvatarIcon', data.photo_url);
        _applyAvatar('updAvatar', null, data.photo_url);
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
        set('updName', data.fullname || data.role || '—');
        set('updRole', data.role);
        set('updDept', data.specialization ? data.specialization + ' Department' : (data.role === 'Admin' ? 'Administrator' : '—'));
        set('updLastLogin', data.last_login ? 'Last login: ' + data.last_login : 'Last login: —');
    } catch(e) { console.error('Profile load error', e); }
}

function _applyAvatar(circleId, iconId, photoUrl) {
    const circle = document.getElementById(circleId);
    if (!circle) return;
    let img  = circle.querySelector('img');
    const icon = iconId ? document.getElementById(iconId) : circle.querySelector('i');
    if (photoUrl) {
        if (!img) {
            img = document.createElement('img');
            img.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:50%;';
            circle.appendChild(img);
        }
        img.src = photoUrl; img.style.display = 'block';
        if (icon) icon.style.display = 'none';
    } else {
        if (img)  img.style.display = 'none';
        if (icon) icon.style.display = '';
    }
}

/* ══════════════════════════════════════════════════════
   PANEL NAVIGATION (browser Back / in-panel Back)
   Opening a panel adds ONE history entry, so the browser's Back button (or a
   mouse/phone back gesture) closes the panel and stays on the current page
   instead of leaving it — previously, Back right after login walked onto
   /login, which ended the session. Switching between panels replaces that
   entry rather than stacking new ones, and closing a panel by any means
   (Back, ✕, Cancel, Save) removes it again, so no duplicate entries pile up.
══════════════════════════════════════════════════════ */
const _UP_PANELS = ['profilePanel', 'passwordPanel', 'emailPanel'];

function _upOpenPanel(id) {
    _UP_PANELS.forEach(p => {
        const el = document.getElementById(p);
        if (el && p !== id) el.style.display = 'none';
    });
    const panel = document.getElementById(id);
    if (panel) panel.style.display = 'flex';
    const state = { upPanel: id };
    if (history.state && history.state.upPanel) history.replaceState(state, '');
    else history.pushState(state, '');
}

function _upClosePanel(id) {
    const panel = document.getElementById(id);
    if (panel) panel.style.display = 'none';
    // Drop the entry this panel added; the popstate that follows only hides panels.
    if (history.state && history.state.upPanel === id) history.back();
}

window.addEventListener('popstate', e => {
    const want = e.state && e.state.upPanel;
    _UP_PANELS.forEach(p => {
        const el = document.getElementById(p);
        if (el && p !== want) el.style.display = 'none';
    });
    if (want === 'passwordPanel') _pwStop();
    // Browser Forward onto a panel entry: reopen it fresh.
    if (want === 'profilePanel') openProfilePanel();
    else if (want === 'passwordPanel') openPasswordPanel();
    else if (want === 'emailPanel') openEmailPanel();
});

window.addEventListener('pageshow', e => {
    // Restored from the back/forward cache: never show a stale panel.
    if (e.persisted) _UP_PANELS.forEach(p => {
        const el = document.getElementById(p);
        if (el) el.style.display = 'none';
    });
});

/* ══════════════════════════════════════════════════════
   PROFILE SETTINGS PANEL
══════════════════════════════════════════════════════ */
let _profileOptions = null;

async function openProfilePanel() {
    closeUpDropdown();
    _upOpenPanel('profilePanel');
    if (!_profileData) await _loadUserCard();
    if (!_profileOptions) {
        try {
            const res = await fetch('/api/user/options');
            _profileOptions = await res.json();
        } catch(e) { _profileOptions = { designations: [], specializations: [] }; }
    }

    const d = _profileData || {};
    document.getElementById('pfEmpId').value = d.emp_num || '—';
    document.getElementById('pfFirstName').value  = d.firstname  || '—';
    document.getElementById('pfMiddleName').value = d.middlename || '—';
    document.getElementById('pfSurname').value    = d.lastname   || '—';

    /* Photo */
    const photoImg  = document.getElementById('profilePhotoImg');
    const photoIcon = document.getElementById('profilePhotoIcon');
    if (d.photo_url) {
        photoImg.src = d.photo_url; photoImg.style.display = 'block';
        if (photoIcon) photoIcon.style.display = 'none';
    } else {
        photoImg.style.display = 'none';
        if (photoIcon) photoIcon.style.display = '';
    }

    /* Designation dropdown — hide section if no faculty record */
    const desSec = document.getElementById('pfDesignationSection');
    const specSec = document.getElementById('pfSpecializationSection');
    const statusSec = document.getElementById('pfStatusSection');
    const hasFaculty = !!d.emp_num;

    if (desSec)    desSec.style.display    = hasFaculty ? '' : 'none';
    if (specSec)   specSec.style.display   = hasFaculty ? '' : 'none';
    if (statusSec) statusSec.style.display = hasFaculty ? '' : 'none';

    const desSel = document.getElementById('pfDesignation');
    if (desSel) {
        desSel.innerHTML = '<option value="">— None —</option>';
        (_profileOptions?.designations || []).forEach(des => {
            const opt = document.createElement('option');
            opt.value = des.designationid; opt.textContent = des.designationname;
            if (des.designationid === d.designation_id) opt.selected = true;
            desSel.appendChild(opt);
        });
    }

    const specSel = document.getElementById('pfSpecialization');
    if (specSel) {
        specSel.innerHTML = '<option value="">— None —</option>';
        (_profileOptions?.specializations || []).forEach(sp => {
            const opt = document.createElement('option');
            opt.value = sp.specializationid; opt.textContent = sp.specializationname;
            if (sp.specializationid === d.specialization_id) opt.selected = true;
            specSel.appendChild(opt);
        });
    }

    const statusSel = document.getElementById('pfStatus');
    if (statusSel) {
        statusSel.value = d.employment_status || 'Permanent';
        // If the DB value doesn't match any option (e.g. blank/unrecognized), fall back
        // to the first real option instead of leaving the select on nothing.
        if (!statusSel.value) statusSel.value = 'Permanent';
    }

    _applyProfileEditLock(d.profile_editable !== false, d.photo_editable !== false);
}

/* Profile Settings (Designation/Specialization/Employment Status/Photo) is temporarily
   locked for every role while the feature is reworked — see _PROFILE_SELF_EDIT_ENABLED
   in app.py, which api_user_me reflects here as profile_editable. Employee ID and the
   name fields are always read-only regardless (they come from HR/import data, not
   self-edit). Re-enabling the backend flag alone won't restore the UI — this still
   needs to see profile_editable: true from the API. */
function _applyProfileEditLock(editable, photoEditable = editable) {
    ['pfDesignation', 'pfSpecialization', 'pfStatus'].forEach(id => {
        const sel = document.getElementById(id);
        if (sel) sel.disabled = !editable;
    });

    // The profile picture is the user's own and stays changeable even while the
    // HR-sourced fields above are locked (server: api_user_me.photo_editable).
    const photoBtn   = document.getElementById('pfChangePhotoBtn');
    const photoInput = document.getElementById('profilePhotoInput');
    if (photoInput) photoInput.disabled = !photoEditable;
    if (photoBtn) {
        photoBtn.classList.toggle('disabled', !photoEditable);
        photoBtn.style.pointerEvents = photoEditable ? '' : 'none';
        photoBtn.style.opacity       = photoEditable ? '' : '0.5';
    }

    const saveBtn = document.getElementById('pfSaveBtn');
    if (saveBtn) {
        saveBtn.disabled = !editable;
        saveBtn.style.opacity = editable ? '' : '0.5';
        saveBtn.style.cursor  = editable ? '' : 'not-allowed';
    }

    const notice = document.getElementById('pfNotice');
    if (notice) {
        notice.textContent = editable
            ? 'Changes will be reflected across the system.'
            : (photoEditable
                ? 'You can change your profile picture. Designation, specialization and employment status can’t be edited here right now.'
                : 'Profile editing is temporarily unavailable — this section is being reworked.');
    }
}

function closeProfilePanel() {
    _upClosePanel('profilePanel');
}

async function saveProfile() {
    const btn  = document.querySelector('#profilePanel .up-btn-save');
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = 'Saving…';
    const fd = new FormData();
    fd.append('designation_id',    document.getElementById('pfDesignation')?.value    || '');
    fd.append('specialization_id', document.getElementById('pfSpecialization')?.value || '');
    fd.append('employment_status', document.getElementById('pfStatus')?.value         || '');
    try {
        const res  = await fetch('/api/user/profile/update', { method: 'POST', body: fd });
        const data = await res.json();
        if (data.success) {
            _profileData = null;
            await _loadUserCard();
            closeProfilePanel();
            _showUpToast('Profile updated successfully.');
        } else { alert(data.error || 'Failed to save profile.'); }
    } catch(e) { alert('Error: ' + e.message); }
    finally { btn.disabled = false; btn.textContent = orig; }
}

async function handlePhotoUpload(input) {
    const file = input.files[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { alert('File too large. Max 2MB.'); input.value = ''; return; }
    const fd = new FormData(); fd.append('photo', file);
    try {
        const res  = await fetch('/api/user/photo/upload', { method: 'POST', body: fd });
        const data = await res.json();
        if (data.success) {
            const photoImg  = document.getElementById('profilePhotoImg');
            const photoIcon = document.getElementById('profilePhotoIcon');
            photoImg.src = data.photo_url; photoImg.style.display = 'block';
            if (photoIcon) photoIcon.style.display = 'none';
            if (_profileData) _profileData.photo_url = data.photo_url;
            _applyAvatar('navAvatar', 'navAvatarIcon', data.photo_url);
            _applyAvatar('updAvatar', null, data.photo_url);
        } else { alert(data.error || 'Upload failed.'); }
    } catch(e) { alert('Upload error: ' + e.message); }
    input.value = '';
}

/* ══════════════════════════════════════════════════════
   PASSWORD PANEL
══════════════════════════════════════════════════════ */
/* Flow: eligibility (once per month) → send emailed code → verify code →
   current / new / confirm → update. The server enforces every step; this
   only drives the three views inside the one panel. */
let _pwResendTimer = null;
const _pwEl = id => document.getElementById(id);

function _pwShowStep(step) {
    ['Check', 'Code', 'Form'].forEach(s => {
        const el = _pwEl('pwStep' + s);
        if (el) el.style.display = s === step ? '' : 'none';
    });
}
function _pwError(id, msg) {
    const el = _pwEl(id);
    if (!el) return;
    el.textContent = msg || '';
    el.style.display = msg ? 'block' : 'none';
}
function _pwStop() {
    if (_pwResendTimer) { clearInterval(_pwResendTimer); _pwResendTimer = null; }
    ['pwCode', 'pwCurrent', 'pwNew', 'pwConfirm'].forEach(id => { const el = _pwEl(id); if (el) el.value = ''; });
}
async function _pwPost(url, fields) {
    const fd = new FormData();
    Object.entries(fields || {}).forEach(([k, v]) => fd.append(k, v));
    const res = await fetch(url, { method: 'POST', body: fd });
    let data = {};
    try { data = await res.json(); } catch (e) { data = { success: false }; }
    data._status = res.status;
    return data;
}

async function openPasswordPanel() {
    closeUpDropdown();
    _upOpenPanel('passwordPanel');
    await _pwStart();
}

async function _pwStart(errorMsg) {
    _pwStop();
    _pwShowStep('Check');
    ['pwCheckError', 'pwCodeError', 'pwError'].forEach(id => _pwError(id, ''));
    _pwError('pwCheckError', errorMsg || '');
    _pwEl('pwSub').textContent = 'Update your account password.';
    const notice = _pwEl('pwCheckNotice'), msg = _pwEl('pwCheckMsg');
    const sendBtn = _pwEl('pwSendBtn'), addBtn = _pwEl('pwAddEmailBtn');
    notice.classList.remove('up-notice-warn');
    msg.textContent = 'Checking eligibility…';
    sendBtn.style.display = 'none'; addBtn.style.display = 'none';
    try {
        const res  = await fetch('/api/user/password/eligibility');
        const data = await res.json();
        if (!data.success) throw new Error(data.message || 'Could not check eligibility.');
        if (!data.eligible) {
            notice.classList.add('up-notice-warn');
            msg.textContent = data.message;
        } else if (!data.has_email) {
            notice.classList.add('up-notice-warn');
            msg.textContent = 'A verification code must be sent to your email, but your account has no email address yet.';
            addBtn.style.display = '';
        } else {
            msg.textContent = `For your security, a 6-digit verification code will be sent to ${data.email_masked}. `
                            + 'You can change your password once per month.';
            sendBtn.style.display = '';
        }
    } catch (e) {
        msg.textContent = 'Could not check whether you can change your password right now.';
        _pwError('pwCheckError', e.message);
    }
}

function _pwStartResendCooldown(seconds) {
    const btn = _pwEl('pwResendBtn');
    if (_pwResendTimer) clearInterval(_pwResendTimer);
    let left = Math.max(0, Math.ceil(seconds || 0));
    const tick = () => {
        if (left <= 0) {
            clearInterval(_pwResendTimer); _pwResendTimer = null;
            btn.disabled = false; btn.textContent = 'Resend code';
            return;
        }
        btn.disabled = true; btn.textContent = `Resend code (${left}s)`;
        left -= 1;
    };
    tick();
    _pwResendTimer = setInterval(tick, 1000);
}

async function requestPasswordCode(isResend) {
    const btn = isResend ? _pwEl('pwResendBtn') : _pwEl('pwSendBtn');
    const errId = isResend ? 'pwCodeError' : 'pwCheckError';
    _pwError(errId, '');
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = 'Sending…';
    try {
        const data = await _pwPost('/api/user/password/request-code');
        if (data.success) {
            _pwShowStep('Code');
            _pwEl('pwCode').value = '';
            _pwError('pwCodeError', '');
            _pwEl('pwCodeHint').textContent =
                `We sent a 6-digit code to ${data.email_masked}. It expires in ${data.expires_in_minutes} minutes and can only be used once.`;
            _pwStartResendCooldown(data.resend_after);
            _pwEl('pwCode').focus();
            if (isResend) _showUpToast('A new verification code was sent.');
            return;
        }
        if (data.error === 'not_eligible') return _pwStart();
        _pwError(errId, data.message || 'Could not send the verification code.');
        if (data.error === 'too_soon' && isResend) return _pwStartResendCooldown(data.retry_after);
    } catch (e) {
        _pwError(errId, 'Network error. Please try again.');
    }
    if (!(isResend && _pwResendTimer)) { btn.disabled = false; btn.textContent = orig; }
}

async function verifyPasswordCode() {
    const code = (_pwEl('pwCode').value || '').trim();
    _pwError('pwCodeError', '');
    if (!/^\d{6}$/.test(code)) return _pwError('pwCodeError', 'Enter the 6-digit code from the email.');
    const btn = _pwEl('pwVerifyBtn'), orig = btn.textContent;
    btn.disabled = true; btn.textContent = 'Verifying…';
    try {
        const data = await _pwPost('/api/user/password/verify-code', { code });
        if (data.success) {
            if (_pwResendTimer) { clearInterval(_pwResendTimer); _pwResendTimer = null; }
            _pwShowStep('Form');
            _pwEl('pwSub').textContent = 'Code verified. Enter your current password and your new password.';
            _pwError('pwError', '');
            _pwEl('pwCurrent').focus();
            return;
        }
        if (data.error === 'not_eligible') return _pwStart();
        _pwError('pwCodeError', data.message || 'Verification failed.');
        if (['too_many_attempts', 'code_expired', 'no_code'].includes(data.error)) _pwEl('pwCode').value = '';
    } catch (e) {
        _pwError('pwCodeError', 'Network error. Please try again.');
    } finally { btn.disabled = false; btn.textContent = orig; }
}

async function savePassword() {
    const cur = _pwEl('pwCurrent').value, nw = _pwEl('pwNew').value, cf = _pwEl('pwConfirm').value;
    _pwError('pwError', '');
    if (!cur || !nw || !cf)  return _pwError('pwError', 'Please fill in all password fields.');
    if (nw !== cf)           return _pwError('pwError', 'New Password and Confirm New Password do not match.');
    if (nw.length < 8)       return _pwError('pwError', 'New password must be at least 8 characters.');
    if (!/[A-Za-z]/.test(nw) || !/\d/.test(nw))
                             return _pwError('pwError', 'New password must contain both letters and numbers.');
    if (nw === cur)          return _pwError('pwError', 'New password must be different from your current password.');
    const btn = _pwEl('pwSaveBtn'), orig = btn.textContent;
    btn.disabled = true; btn.textContent = 'Updating…';
    try {
        const data = await _pwPost('/api/user/password/change',
            { current_password: cur, new_password: nw, confirm_password: cf });
        if (data.success) {
            closePasswordPanel();
            _showUpToast(`Password updated successfully. You can change it again on ${data.next_allowed_date}.`);
            return;
        }
        if (data.error === 'not_verified' || data.error === 'not_eligible') return _pwStart(data.message);
        _pwError('pwError', data.message || 'Failed to update password.');
    } catch (e) {
        _pwError('pwError', 'Network error. Please try again.');
    } finally { btn.disabled = false; btn.textContent = orig; }
}

function closePasswordPanel() {
    _pwStop();
    _upClosePanel('passwordPanel');
}

/* ══════════════════════════════════════════════════════
   EMAIL PANEL
══════════════════════════════════════════════════════ */
function openEmailPanel() {
    closeUpDropdown();
    const emailEl = document.getElementById('pfEmail');
    if (emailEl && _profileData?.email) emailEl.value = _profileData.email;
    document.getElementById('emailError').style.display = 'none';
    _upOpenPanel('emailPanel');
}
function closeEmailPanel() { _upClosePanel('emailPanel'); }

async function saveEmail() {
    const btn   = document.querySelector('#emailPanel .up-btn-save');
    const errEl = document.getElementById('emailError');
    errEl.style.display = 'none';
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = 'Saving…';
    const fd = new FormData();
    fd.append('email', document.getElementById('pfEmail').value.trim());
    try {
        const res  = await fetch('/api/user/email/update', { method: 'POST', body: fd });
        const data = await res.json();
        if (data.success) {
            if (_profileData) _profileData.email = fd.get('email');
            closeEmailPanel(); _showUpToast('Email updated successfully.');
        }
        else { errEl.textContent = data.error || 'Failed to update email.'; errEl.style.display = 'block'; }
    } catch(e) { errEl.textContent = 'Error: ' + e.message; errEl.style.display = 'block'; }
    finally { btn.disabled = false; btn.textContent = orig; }
}

/* ── Toast ─────────────────────────────────────────── */
function _showUpToast(msg) {
    let wrap = document.getElementById('upToastWrap');
    if (!wrap) {
        wrap = document.createElement('div');
        wrap.id = 'upToastWrap';
        wrap.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:8px;';
        document.body.appendChild(wrap);
    }
    const t = document.createElement('div');
    t.style.cssText = 'background:#2e7d32;color:#fff;padding:12px 20px;border-radius:8px;font-size:.85rem;font-weight:600;box-shadow:0 4px 12px rgba(0,0,0,.25);opacity:0;transition:opacity .25s;';
    t.textContent = msg;
    wrap.appendChild(t);
    requestAnimationFrame(() => { t.style.opacity = '1'; });
    setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3500);
}

/* ══════════════════════════════════════════════════════
   GLOBAL SEARCH
══════════════════════════════════════════════════════ */
const _CATEGORY_ORDER = ['Faculty','Room','Program','Subject','Section'];

function _initGlobalSearch() {
    const input   = document.getElementById('globalSearch');
    const resultsEl = document.getElementById('searchResults');
    if (!input || !resultsEl) return;

    /* Belt-and-suspenders: clear any browser-autofilled value */
    input.value = '';
    setTimeout(() => { if (!input.matches(':focus')) input.value = ''; }, 200);

    let _debounce = null;

    input.addEventListener('input', () => {
        clearTimeout(_debounce);
        const q = input.value.trim();
        if (q.length < 2) { _closeSearchResults(); return; }
        _debounce = setTimeout(() => _runSearch(q), 280);
    });

    input.addEventListener('focus', () => {
        if (input.value.trim().length >= 2) resultsEl.classList.add('open');
    });

    input.addEventListener('keydown', e => {
        if (e.key === 'Escape') { _closeSearchResults(); input.blur(); }
    });
}

function _closeSearchResults() {
    const el = document.getElementById('searchResults');
    if (el) el.classList.remove('open');
}

async function _runSearch(q) {
    const resultsEl = document.getElementById('searchResults');
    if (!resultsEl) return;
    resultsEl.innerHTML = '<div class="sr-empty">Searching…</div>';
    resultsEl.classList.add('open');

    try {
        const res  = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        const items = data.results || [];

        if (!items.length) {
            resultsEl.innerHTML = '<div class="sr-empty">No results found.</div>';
            return;
        }

        /* Group by category in defined order */
        const groups = {};
        items.forEach(r => { (groups[r.category] = groups[r.category] || []).push(r); });

        let html = '';
        const order = [..._CATEGORY_ORDER, ...Object.keys(groups).filter(k => !_CATEGORY_ORDER.includes(k))];
        order.forEach(cat => {
            if (!groups[cat]) return;
            html += `<div class="sr-category">${cat}</div>`;
            groups[cat].forEach(r => {
                if (r.url) {
                    html += `<a class="sr-item" href="${r.url}">`;
                } else {
                    html += `<div class="sr-item sr-item--info">`;
                }
                html += `<div class="sr-item-icon"><i class="fas ${r.icon}"></i></div>
                    <div class="sr-item-text">
                        <span class="sr-item-label">${_esc(r.label)}</span>
                        ${r.sub ? `<span class="sr-item-sub">${_esc(r.sub)}</span>` : ''}
                    </div>`;
                html += r.url ? `</a>` : `</div>`;
            });
        });
        resultsEl.innerHTML = html;
    } catch(e) {
        resultsEl.innerHTML = '<div class="sr-empty">Search unavailable.</div>';
    }
}

function _esc(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
