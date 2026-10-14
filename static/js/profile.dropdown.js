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
    // The avatar while a panel is open acts like the panel's Back.
    if (_upActivePanel()) return upBackToMenu();
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
   PANEL NAVIGATION (in-panel Back / browser Back)
   The menu and each panel open in the same spot under the avatar
   (templates/shared/_user_menu.html). A panel's Back returns to the menu;
   ✕ / Cancel / a successful save close everything.
   Opening a panel adds ONE history entry, so the browser's Back button (or a
   mouse/phone back gesture) also returns to the menu and stays on the page
   instead of leaving it — previously, Back right after login walked onto
   /login, which ended the session. Switching between panels replaces that
   entry rather than stacking new ones, and closing a panel by any means
   removes it again, so no duplicate entries pile up.
══════════════════════════════════════════════════════ */
const _UP_PANELS = ['profilePanel', 'passwordPanel', 'emailPanel'];
// Per-panel cleanup when it is hidden: unsaved input is discarded.
const _UP_PANEL_RESET = {
    profilePanel:  () => { _pendingPhoto = null; _collapseMyCorrections(); },
    passwordPanel: () => _pwStop(),
    emailPanel:    () => _emStop(),
};


function _upActivePanel() {
    return _UP_PANELS.find(p => {
        const el = document.getElementById(p);
        return el && el.style.display !== 'none';
    }) || null;
}


function _upHidePanels(except) {
    _UP_PANELS.forEach(p => {
        const el = document.getElementById(p);
        if (!el || p === except || el.style.display === 'none') return;
        el.style.display = 'none';
        _UP_PANEL_RESET[p]();
    });
}


function _upOpenPanel(id) {
    closeUpDropdown();
    _upHidePanels(id);
    const panel = document.getElementById(id);
    if (panel) panel.style.display = 'block';
    const state = { upPanel: id };
    if (history.state && history.state.upPanel) history.replaceState(state, '');
    else history.pushState(state, '');
}


function _upClosePanel(toMenu = false) {
    _upHidePanels();
    if (toMenu) openUpDropdown();
    // Drop the entry the panel added; the popstate that follows finds nothing open.
    if (history.state && history.state.upPanel) history.back();
}


/* Back: leave the panel and show the menu of what can be changed. */
function upBackToMenu() { _upClosePanel(true); }


window.addEventListener('popstate', e => {
    const want = e.state && e.state.upPanel;
    const wasOpen = _upActivePanel();
    _upHidePanels(want);
    // Browser Forward onto a panel entry: reopen it fresh.
    if (want === 'profilePanel') openProfilePanel();
    else if (want === 'passwordPanel') openPasswordPanel();
    else if (want === 'emailPanel') openEmailPanel();
    // Browser Back out of a panel: same as the panel's Back.
    else if (wasOpen) openUpDropdown();
});


window.addEventListener('pageshow', e => {
    // Restored from the back/forward cache (e.g. Back after Sign out): reload so
    // the server decides — a signed-out user is sent to the login page instead of
    // seeing the old logged-in page. Pages are also sent no-store (app.py).
    if (e.persisted) { _upHidePanels(); closeUpDropdown(); location.reload(); }
});


/* ══════════════════════════════════════════════════════
   PROFILE SETTINGS PANEL
══════════════════════════════════════════════════════ */
let _profileOptions = null;
let _profileEditable = false;
let _pendingPhoto = null;   // cropped photo waiting for Save Changes; dropped on Cancel / close


async function openProfilePanel() {
    _upOpenPanel('profilePanel');
    if (!_profileData) await _loadUserCard();
    if (!_profileOptions) {
        try {
            const res = await fetch('/api/user/options');
            _profileOptions = await res.json();
        } catch(e) { _profileOptions = { designations: [], specializations: [] }; }
    }


    const d = _profileData || {};
    const setText = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v || '—'; };
    setText('pfFullName',   d.fullname || d.role);
    setText('pfRole',       d.role);
    setText('pfEmpId',      d.emp_num);
    setText('pfFirstName',  d.firstname);
    setText('pfMiddleName', d.middlename);
    setText('pfSurname',    d.lastname);
    setText('pfDesignationText',    d.designation    || 'Not set');
    setText('pfSpecializationText', d.specialization || 'Not set');
    setText('pfStatusText',         d.employment_status || 'Not set');
    setText('pfEmail',      d.email || 'No email on file');
    const pending = document.getElementById('pfPhotoPending');
    if (pending) pending.hidden = true;


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


    _pendingPhoto = null;
    _applyProfileEditLock(d.profile_editable !== false, d.photo_editable !== false);
}


/* Profile Settings (Designation/Specialization/Employment Status/Photo) is temporarily
   locked for every role while the feature is reworked — see _PROFILE_SELF_EDIT_ENABLED
   in app.py, which api_user_me reflects here as profile_editable. Employee ID and the
   name fields are always read-only regardless (they come from HR/import data, not
   self-edit). Re-enabling the backend flag alone won't restore the UI — this still
   needs to see profile_editable: true from the API. */
function _applyProfileEditLock(editable, photoEditable = editable) {
    _profileEditable = editable;
    // Locked: show the values as plain text. Editable: show the dropdowns instead.
    ['pfDesignation', 'pfSpecialization', 'pfStatus'].forEach(id => {
        const sel  = document.getElementById(id);
        const text = document.getElementById(id + 'Text');
        if (sel)  { sel.disabled = !editable; sel.hidden = !editable; }
        if (text) text.hidden = editable;
    });
    const lockTag = document.getElementById('pfLockTag');
    if (lockTag) lockTag.hidden = editable;


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


    _syncProfileSaveBtn();


    const notice = document.getElementById('pfNotice');
    if (notice) {
        notice.textContent = editable
            ? 'Changes will be reflected across the system.'
            : (photoEditable
                ? (document.getElementById('pfRequestCorrectionBtn')
                    ? 'Official information is kept by the Administrator. If something is wrong, use Request Correction above.'
                    : 'Official information is kept by the Administrator.')
                : 'Profile editing is temporarily unavailable — this section is being reworked.');
    }
}


/* Save Changes is usable when the profile fields are editable, or when there is a
   cropped photo waiting to be saved (the photo stays changeable while the fields are locked). */
function _syncProfileSaveBtn() {
    const saveBtn = document.getElementById('pfSaveBtn');
    if (!saveBtn) return;
    const enabled = _profileEditable || !!_pendingPhoto;
    saveBtn.disabled = !enabled;
    saveBtn.style.opacity = enabled ? '' : '0.5';
    saveBtn.style.cursor  = enabled ? '' : 'not-allowed';
}


function closeProfilePanel() {
    _upClosePanel();   // an unsaved photo is discarded (_UP_PANEL_RESET); the saved one shows next time
}


async function saveProfile() {
    const btn  = document.querySelector('#profilePanel .up-btn-save');
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = 'Saving…';
    try {
        if (_pendingPhoto) {
            const pfd = new FormData(); pfd.append('photo', _pendingPhoto, 'profile.jpg');
            const res  = await fetch('/api/user/photo/upload', { method: 'POST', body: pfd });
            const data = await res.json();
            if (!data.success) { alert(data.error || 'Photo upload failed.'); return; }
            _pendingPhoto = null;
        }
        if (_profileEditable) {
            const fd = new FormData();
            fd.append('designation_id',    document.getElementById('pfDesignation')?.value    || '');
            fd.append('specialization_id', document.getElementById('pfSpecialization')?.value || '');
            fd.append('employment_status', document.getElementById('pfStatus')?.value         || '');
            const res  = await fetch('/api/user/profile/update', { method: 'POST', body: fd });
            const data = await res.json();
            if (!data.success) { alert(data.error || 'Failed to save profile.'); return; }
        }
        _profileData = null;
        await _loadUserCard();
        closeProfilePanel();
        _showUpToast('Profile updated successfully.');
    } catch(e) { alert('Error: ' + e.message); }
    finally { btn.textContent = orig; _syncProfileSaveBtn(); }
}


async function handlePhotoUpload(input) {
    const file = input.files[0];
    if (!file) return;
    input.value = '';   // so picking the same file again still fires onchange
    if (file.size > 2 * 1024 * 1024) { alert('File too large. Max 2MB.'); return; }
    // Let the user fit the picture to the avatar circle before it is uploaded.
    const cropped = await openPhotoCropper(file);
    if (!cropped) return;
    // Preview only — nothing is uploaded until the user clicks Save Changes.
    _pendingPhoto = cropped;
    const photoImg  = document.getElementById('profilePhotoImg');
    const photoIcon = document.getElementById('profilePhotoIcon');
    photoImg.src = URL.createObjectURL(cropped); photoImg.style.display = 'block';
    if (photoIcon) photoIcon.style.display = 'none';
    const pending = document.getElementById('pfPhotoPending');
    if (pending) pending.hidden = false;
    _syncProfileSaveBtn();
}


/* ══════════════════════════════════════════════════════
   SHARED PANEL HELPERS
══════════════════════════════════════════════════════ */
const _pwEl = id => document.getElementById(id);


async function _upPost(url, fields) {
    const fd = new FormData();
    Object.entries(fields || {}).forEach(([k, v]) => fd.append(k, v));
    const res = await fetch(url, { method: 'POST', body: fd });
    let data = {};
    try { data = await res.json(); } catch (e) { data = { success: false }; }
    data._status = res.status;
    return data;
}


function _upShowError(id, msg) {
    const el = _pwEl(id);
    if (!el) return;
    el.textContent = msg || '';
    el.style.display = msg ? 'block' : 'none';
}


// Buttons keep their icon; only the text in their <span> changes.
function _upBtnLabel(btn, text) {
    const span = btn && btn.querySelector('span');
    if (span) span.textContent = text; else if (btn) btn.textContent = text;
}


const _fmtCountdown = s => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;


function togglePwVisibility(btn) {
    const input = btn.parentElement.querySelector('input');
    const show  = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.querySelector('i').className = show ? 'fas fa-eye-slash' : 'fas fa-eye';
    btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
}


/* ══════════════════════════════════════════════════════
   PASSWORD PANEL
══════════════════════════════════════════════════════ */
/* One form: Send Code → type the 6-digit code and the passwords → Update.
   On Update the code is verified first (once), then the password is changed.
   The server enforces every step (once a month, code expiry/attempts, the
   verified window); this only drives the form. */
let _pwResendTimer = null;
let _pwCodeSent    = false;
let _pwVerified    = false;   // code accepted; a retry (e.g. wrong current password) needn't re-verify


const _pwOtpInputs = () => [...document.querySelectorAll('#pwOtp input')];
const _pwOtpValue  = () => _pwOtpInputs().map(i => i.value).join('');
const _pwError     = msg => _upShowError('pwError', msg);


function _pwClearOtp() { _pwOtpInputs().forEach(i => { i.value = ''; }); }


function _pwStop() {
    if (_pwResendTimer) { clearInterval(_pwResendTimer); _pwResendTimer = null; }
    _pwCodeSent = false; _pwVerified = false;
    _pwClearOtp();
    ['pwCurrent', 'pwNew', 'pwConfirm'].forEach(id => {
        const el = _pwEl(id);
        if (!el) return;
        el.value = ''; el.type = 'password';
        const eye = el.parentElement.querySelector('.up-pw-eye i');
        if (eye) eye.className = 'fas fa-eye';
    });
    _pwError('');
}


/* Enable or lock the whole form (locked while the password can't be changed). */
function _pwSetFormEnabled(on) {
    document.querySelectorAll('#pwForm input').forEach(i => { i.disabled = !on; });
    const send = _pwEl('pwSendBtn'), save = _pwEl('pwSaveBtn');
    if (send) send.disabled = !on;
    if (save) { save.disabled = !on; save.style.opacity = on ? '' : '0.5'; }
}


function _pwBlocked(msg, showAddEmail) {
    _pwEl('pwBlocked').style.display = msg ? '' : 'none';
    _pwEl('pwBlockedMsg').textContent = msg || '';
    _pwEl('pwAddEmailBtn').style.display = showAddEmail ? '' : 'none';
}


async function openPasswordPanel() {
    _upOpenPanel('passwordPanel');
    _pwStop();
    _pwBlocked('');
    _pwEl('pwResendBox').style.display = 'none';
    _upBtnLabel(_pwEl('pwSendBtn'), 'Send Code');
    _pwEl('pwCodeHint').textContent = 'A 6-digit verification code will be sent to your email.';
    _pwSetFormEnabled(false);
    if (!_profileData) await _loadUserCard();
    _pwEl('pwEmail').textContent = _profileData?.email || '—';
    try {
        const res  = await fetch('/api/user/password/eligibility');
        const data = await res.json();
        if (!data.success) throw new Error(data.message);
        if (!data.eligible) return _pwBlocked(data.message);
        if (!data.has_email) {
            _pwEl('pwEmail').textContent = 'No email address on file';
            return _pwBlocked('A verification code must be sent to your email, but your account has no email address yet.', true);
        }
        if (!_profileData?.email) _pwEl('pwEmail').textContent = data.email_masked;
        _pwSetFormEnabled(true);
    } catch (e) {
        _pwBlocked('Could not check whether you can change your password right now. Please try again.');
    }
}


function _pwStartResendCooldown(seconds) {
    const resend = _pwEl('pwResendBtn'), send = _pwEl('pwSendBtn');
    if (_pwResendTimer) clearInterval(_pwResendTimer);
    let left = Math.max(0, Math.ceil(seconds || 0));
    const tick = () => {
        if (left <= 0) {
            clearInterval(_pwResendTimer); _pwResendTimer = null;
            resend.disabled = false; resend.textContent = 'Resend Code';
            send.disabled = false; _upBtnLabel(send, 'Resend');
            return;
        }
        resend.disabled = true; resend.textContent = `Resend Code (${_fmtCountdown(left)})`;
        send.disabled = true;
        left -= 1;
    };
    tick();
    _pwResendTimer = setInterval(tick, 1000);
}


async function requestPasswordCode(isResend) {
    const btn = isResend ? _pwEl('pwResendBtn') : _pwEl('pwSendBtn');
    _pwError('');
    btn.disabled = true;
    if (!isResend) _upBtnLabel(btn, 'Sending…');
    try {
        const data = await _upPost('/api/user/password/request-code');
        if (data.success) {
            _pwCodeSent = true; _pwVerified = false;
            _pwClearOtp();
            _pwEl('pwCodeHint').textContent =
                `We sent a 6-digit code to ${data.email_masked}. It expires in ${data.expires_in_minutes} minutes and can only be used once.`;
            _pwEl('pwResendBox').style.display = '';
            _pwStartResendCooldown(data.resend_after);
            _pwOtpInputs()[0]?.focus();
            if (isResend) _showUpToast('A new verification code was sent.');
            return;
        }
        if (data.error === 'not_eligible') return openPasswordPanel();
        _pwError(data.message || 'Could not send the verification code.');
        if (data.error === 'too_soon') return _pwStartResendCooldown(data.retry_after);
    } catch (e) {
        _pwError('Network error. Please try again.');
    }
    if (!_pwResendTimer) {
        btn.disabled = false;
        if (!isResend) _upBtnLabel(btn, _pwCodeSent ? 'Resend' : 'Send Code');
    }
}


async function savePassword() {
    const code = _pwOtpValue();
    const cur = _pwEl('pwCurrent').value, nw = _pwEl('pwNew').value, cf = _pwEl('pwConfirm').value;
    _pwError('');
    if (!_pwCodeSent && !_pwVerified) return _pwError('Click Send Code to get a verification code first.');
    if (!_pwVerified && !/^\d{6}$/.test(code)) return _pwError('Enter the 6-digit code from the email.');
    if (!cur || !nw || !cf)  return _pwError('Please fill in all password fields.');
    if (nw !== cf)           return _pwError('New Password and Confirm New Password do not match.');
    if (nw.length < 8)       return _pwError('New password must be at least 8 characters.');
    if (!/[A-Za-z]/.test(nw) || !/\d/.test(nw))
                             return _pwError('New password must contain both letters and numbers.');
    if (nw === cur)          return _pwError('New password must be different from your current password.');
    const btn = _pwEl('pwSaveBtn'), orig = btn.textContent;
    btn.disabled = true; btn.textContent = 'Updating…';
    try {
        if (!_pwVerified) {
            const v = await _upPost('/api/user/password/verify-code', { code });
            if (!v.success) {
                if (v.error === 'not_eligible') return openPasswordPanel();
                if (['too_many_attempts', 'code_expired', 'no_code'].includes(v.error)) { _pwClearOtp(); _pwCodeSent = false; }
                return _pwError(v.message || 'Verification failed.');
            }
            _pwVerified = true;
        }
        const data = await _upPost('/api/user/password/change',
            { current_password: cur, new_password: nw, confirm_password: cf });
        if (data.success) {
            closePasswordPanel();
            _showUpToast(`Password updated successfully. You can change it again on ${data.next_allowed_date}.`);
            return;
        }
        if (data.error === 'not_verified') { _pwVerified = false; _pwCodeSent = false; _pwClearOtp(); }
        if (data.error === 'not_eligible') return openPasswordPanel();
        _pwError(data.message || 'Failed to update password.');
    } catch (e) {
        _pwError('Network error. Please try again.');
    } finally { btn.disabled = false; btn.textContent = orig; }
}


function closePasswordPanel() { _upClosePanel(); }


/* Six single-digit boxes: typing moves forward, Backspace moves back, and a
   pasted / autofilled code spreads across all boxes. */
function _initPwOtp() {
    const boxes = _pwOtpInputs();
    boxes.forEach((box, i) => {
        box.addEventListener('input', () => {
            const digits = box.value.replace(/\D/g, '');
            if (digits.length > 1) {          // paste or one-time-code autofill
                digits.slice(0, boxes.length - i).split('').forEach((d, k) => { boxes[i + k].value = d; });
                boxes[Math.min(i + digits.length, boxes.length - 1)].focus();
                return;
            }
            box.value = digits;
            if (digits && boxes[i + 1]) boxes[i + 1].focus();
        });
        box.addEventListener('keydown', e => {
            if (e.key === 'Backspace' && !box.value && boxes[i - 1]) { boxes[i - 1].focus(); boxes[i - 1].value = ''; e.preventDefault(); }
            else if (e.key === 'ArrowLeft'  && boxes[i - 1]) boxes[i - 1].focus();
            else if (e.key === 'ArrowRight' && boxes[i + 1]) boxes[i + 1].focus();
        });
        box.addEventListener('paste', e => {
            const digits = (e.clipboardData?.getData('text') || '').replace(/\D/g, '');
            if (!digits) return;
            e.preventDefault();
            boxes.forEach((b, k) => { b.value = digits[k] || ''; });
            boxes[Math.min(digits.length, boxes.length) - 1].focus();
        });
    });
}
document.addEventListener('DOMContentLoaded', _initPwOtp);


/* ══════════════════════════════════════════════════════
   EMAIL PANEL
══════════════════════════════════════════════════════ */
/* Send Code mails a 6-digit code to the NEW address; Save Email checks it and
   saves. The code only works for the address it was sent to. */
let _emResendTimer = null;
let _emSentTo      = '';
const _emError     = msg => _upShowError('emailError', msg);
const _EMAIL_RE    = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;


function _emStop() {
    if (_emResendTimer) { clearInterval(_emResendTimer); _emResendTimer = null; }
    _emSentTo = '';
    ['emNew', 'emCode'].forEach(id => { const el = _pwEl(id); if (el) el.value = ''; });
    const send = _pwEl('emSendBtn');
    if (send) { send.disabled = false; _upBtnLabel(send, 'Send Code'); }
    const hint = _pwEl('emHint');
    if (hint) hint.textContent = 'A 6-digit verification code will be sent to your new email address.';
    _emError('');
}


async function openEmailPanel() {
    _upOpenPanel('emailPanel');
    _emStop();
    if (!_profileData) await _loadUserCard();
    const current = _profileData?.email || '';
    _pwEl('emCurrent').value = current || 'No email address on file';
    _pwEl('emCurrentTag').style.display = current ? '' : 'none';
}


function _emStartResendCooldown(seconds) {
    const btn = _pwEl('emSendBtn');
    if (_emResendTimer) clearInterval(_emResendTimer);
    let left = Math.max(0, Math.ceil(seconds || 0));
    const tick = () => {
        if (left <= 0) {
            clearInterval(_emResendTimer); _emResendTimer = null;
            btn.disabled = false; _upBtnLabel(btn, 'Resend Code');
            return;
        }
        btn.disabled = true; _upBtnLabel(btn, `Resend (${_fmtCountdown(left)})`);
        left -= 1;
    };
    tick();
    _emResendTimer = setInterval(tick, 1000);
}


async function requestEmailCode() {
    const email = _pwEl('emNew').value.trim();
    const btn = _pwEl('emSendBtn');
    _emError('');
    if (!_EMAIL_RE.test(email)) return _emError('Please enter a valid new email address first.');
    btn.disabled = true; _upBtnLabel(btn, 'Sending…');
    try {
        const data = await _upPost('/api/user/email/request-code', { email });
        if (data.success) {
            _emSentTo = email;
            _pwEl('emHint').textContent =
                `We sent a 6-digit code to ${data.email_masked}. It expires in ${data.expires_in_minutes} minutes and can only be used once.`;
            _emStartResendCooldown(data.resend_after);
            _pwEl('emCode').value = '';
            _pwEl('emCode').focus();
            return;
        }
        _emError(data.message || 'Could not send the verification code.');
        if (data.error === 'too_soon') return _emStartResendCooldown(data.retry_after);
    } catch (e) {
        _emError('Network error. Please try again.');
    }
    btn.disabled = false; _upBtnLabel(btn, _emSentTo ? 'Resend Code' : 'Send Code');
}


async function saveEmail() {
    const email = _pwEl('emNew').value.trim();
    const code  = _pwEl('emCode').value.trim();
    _emError('');
    if (!_EMAIL_RE.test(email)) return _emError('Please enter a valid new email address.');
    if (!_emSentTo) return _emError('Click Send Code to get a verification code for this address first.');
    if (email.toLowerCase() !== _emSentTo.toLowerCase())
        return _emError(`The code was sent to ${_emSentTo}. Send a new code to use this address instead.`);
    if (!/^\d{6}$/.test(code)) return _emError('Enter the 6-digit code from the email.');
    const btn = _pwEl('emSaveBtn'), orig = btn.textContent;
    btn.disabled = true; btn.textContent = 'Saving…';
    try {
        const data = await _upPost('/api/user/email/update', { email, code });
        if (data.success) {
            if (_profileData) _profileData.email = data.email;
            closeEmailPanel();
            _showUpToast('Email updated successfully.');
            return;
        }
        if (['too_many_attempts', 'code_expired', 'no_code'].includes(data.error)) {
            _pwEl('emCode').value = ''; _emSentTo = '';
        }
        _emError(data.message || 'Failed to update email.');
    } catch (e) {
        _emError('Network error. Please try again.');
    } finally { btn.disabled = false; btn.textContent = orig; }
}


function closeEmailPanel() { _upClosePanel(); }


/* ══════════════════════════════════════════════════════
   MY CORRECTION REQUESTS — expandable section inside Profile Settings.
   Read-only: status, values and remarks all come from the Admin's decision.
══════════════════════════════════════════════════════ */
function _crStatusText(r) {
    if (r.status === 'Pending')  return 'Awaiting Administrator review.';
    if (r.status === 'Rejected') return 'Rejected by the Administrator.';
    return r.field === 'Employee ID'
        ? 'Approved. The Administrator updates your Employee ID in Faculty Management.'
        : 'Approved. Your official record was updated.';
}

function _collapseMyCorrections() {
    const list = document.getElementById('crList');
    const btn  = document.getElementById('crToggle');
    if (!list || !btn) return;
    list.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
    btn.classList.remove('open');
}

async function toggleMyCorrections() {
    const list = document.getElementById('crList');
    const btn  = document.getElementById('crToggle');
    if (!list.hidden) { _collapseMyCorrections(); return; }
    list.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    btn.classList.add('open');
    list.innerHTML = '<div class="cr-empty">Loading…</div>';
    try {
        // Fetched fresh on every open, so an Admin decision shows up without a reload.
        const res = await fetch('/api/user/correction-requests', { credentials: 'same-origin' });
        const d = await res.json();
        if (!d.success) throw new Error();
        if (!d.requests.length) {
            list.innerHTML = '<div class="cr-empty">You have not submitted any correction requests.</div>';
            return;
        }
        list.innerHTML = d.requests.map(r => `
            <div class="cr-item">
                <div class="cr-item-top">
                    <span class="cr-field">${_esc(r.field)}</span>
                    <span class="cr-status cr-${_esc(r.status)}">${_esc(r.status)}</span>
                </div>
                <div class="cr-note">${_esc(_crStatusText(r))}</div>
                <div class="cr-row"><span>Original</span>${_esc(r.current) || '—'}</div>
                <div class="cr-row"><span>Proposed</span><strong>${r.proposed ? _esc(r.proposed) : '<em>(blank)</em>'}</strong></div>
                ${r.reason ? `<div class="cr-row"><span>Reason</span>${_esc(r.reason)}</div>` : ''}
                <div class="cr-row"><span>Submitted</span>${_esc(r.submitted)}</div>
                ${r.decided ? `<div class="cr-row"><span>Reviewed</span>${_esc(r.decided)}</div>` : ''}
                ${r.admin_remarks ? `<div class="cr-row cr-admin"><span>${r.status === 'Rejected' ? 'Reason for rejection' : 'Admin remarks'}</span>${_esc(r.admin_remarks)}</div>` : ''}
            </div>`).join('');
    } catch (e) {
        list.innerHTML = '<div class="cr-empty">Your requests could not be loaded. Please try again.</div>';
    }
}


/* ══════════════════════════════════════════════════════
   REQUEST CORRECTION — pop-up opened from Profile Settings > Official Information.
   Same request (and server validation) as the account-setup Review step; the
   Administrator reviews it and the record changes only once it is approved.
══════════════════════════════════════════════════════ */
const _CRM_SUFFIXES = ['', 'Jr.', 'Sr.', 'II', 'III', 'IV', 'V'];
const _escAttr = s => _esc(s).replace(/"/g, '&quot;');
let _crmCurrent = {};

function _crmFieldsFor(key) {
    return { empid: ['Employee ID'], name: ['First Name', 'Middle Name', 'Last Name', 'Suffix'],
             designation: ['Designation'], specialization: ['Specialization'] }[key];
}

function _crmItemHtml(key, label, icon, currentText, pending) {
    const allPending = _crmFieldsFor(key).every(f => pending.has(f));
    let proposed = '';
    if (key === 'name') {
        const sfx = _CRM_SUFFIXES.includes(_crmCurrent['Suffix']) ? _CRM_SUFFIXES : [..._CRM_SUFFIXES, _crmCurrent['Suffix']];
        proposed = `<div class="crm-proposed-label">Proposed Full Name</div><div class="crm-grid">` +
            ['First Name', 'Middle Name', 'Last Name'].map(f =>
                `<input type="text" data-field="${f}" maxlength="60" placeholder="${f}" aria-label="Proposed ${f}"
                        value="${_escAttr(_crmCurrent[f])}" ${pending.has(f) ? 'disabled' : ''}>`).join('') +
            `<select data-field="Suffix" aria-label="Proposed suffix (optional)" ${pending.has('Suffix') ? 'disabled' : ''}>` +
            sfx.map(s => `<option value="${_escAttr(s)}" ${s === _crmCurrent['Suffix'] ? 'selected' : ''}>${s ? _esc(s) : 'No suffix'}</option>`).join('') +
            `</select></div>`;
    } else if (key === 'empid') {
        proposed = `<div class="crm-proposed-label">Proposed Employee ID</div>
            <input type="text" data-field="Employee ID" maxlength="30" placeholder="Enter correct Employee ID">
            <div class="crm-warn"><i class="fas fa-triangle-exclamation"></i> Employee ID is a sensitive identifier. The Administrator checks and updates it by hand.</div>`;
    } else {
        const opts = key === 'designation'
            ? (_profileOptions?.designations || []).map(o => o.designationname)
            : (_profileOptions?.specializations || []).filter(o => o.isactive !== false).map(o => o.specializationname);
        proposed = `<div class="crm-proposed-label">Proposed ${label}</div>
            <select data-field="${label}"><option value="">Select correct ${label.toLowerCase()}</option>` +
            opts.map(n => `<option value="${_escAttr(n)}">${_esc(n)}</option>`).join('') + `</select>`;
    }
    return `<div class="crm-item" data-key="${key}">
        <span class="crm-item-icon"><i class="fas ${icon}"></i></span>
        <div class="crm-item-main">
            <div class="crm-item-top">
                <div><div class="crm-item-name">${label}</div><div class="crm-item-cur">Current ${label}</div>
                     <div class="crm-current">${_esc(currentText) || 'Not set'}</div></div>
                ${allPending ? '<span class="cr-status cr-Pending">Pending review</span>'
                    : `<label class="crm-ok"><input type="checkbox" value="${key}" checked> This information is correct</label>`}
            </div>
            ${allPending ? '' : `<div class="crm-proposed">${proposed}</div>`}
        </div>
    </div>`;
}

const _crmChanging = () => [...document.querySelectorAll('#crmItems .crm-ok input')].filter(c => !c.checked);

function _crmSync() {
    const n = _crmChanging().length;
    document.getElementById('crmForm').classList.toggle('has-changes', n > 0);
    document.getElementById('crmSubmit').disabled = n === 0;
    document.getElementById('crmReasonCount').textContent = document.getElementById('crmReason').value.length;
}

async function openCorrectionModal() {
    const modal = document.getElementById('crModal');
    if (!modal) return;
    // Lives under the header; move it to <body> so it overlays the whole page.
    if (modal.parentElement !== document.body) document.body.appendChild(modal);
    _upShowError('crmError', '');
    document.getElementById('crmReason').value = '';
    document.getElementById('crmItems').innerHTML = '<div class="cr-empty">Loading…</div>';
    modal.hidden = false;
    document.body.style.overflow = 'hidden';

    if (!_profileData) await _loadUserCard();
    if (!_profileOptions) {
        try { _profileOptions = await (await fetch('/api/user/options')).json(); }
        catch (e) { _profileOptions = { designations: [], specializations: [] }; }
    }
    let pending = new Set();
    try {
        const d = await (await fetch('/api/user/correction-requests', { credentials: 'same-origin' })).json();
        if (d.success) pending = new Set(d.requests.filter(r => r.status === 'Pending').map(r => r.field));
    } catch (e) { /* the server still refuses a duplicate pending field */ }

    const p = _profileData || {};
    _crmCurrent = { 'Employee ID': p.emp_num || '', 'First Name': p.firstname || '', 'Middle Name': p.middlename || '',
                    'Last Name': p.lastname || '', 'Suffix': p.suffix || '',
                    'Designation': p.designation || '', 'Specialization': p.specialization || '' };
    const fullName = [p.firstname, p.middlename, p.lastname, p.suffix].filter(Boolean).join(' ');
    document.getElementById('crmItems').innerHTML =
        _crmItemHtml('empid', 'Employee ID', 'fa-id-card', p.emp_num, pending) +
        _crmItemHtml('name', 'Full Name', 'fa-user', fullName, pending) +
        _crmItemHtml('designation', 'Designation', 'fa-briefcase', p.designation, pending) +
        _crmItemHtml('specialization', 'Specialization', 'fa-graduation-cap', p.specialization, pending);
    document.querySelectorAll('#crmItems .crm-ok input').forEach(box => box.addEventListener('change', () => {
        const item = box.closest('.crm-item');
        item.classList.toggle('changing', !box.checked);
        if (!box.checked) item.querySelector('.crm-proposed [data-field]:not(:disabled)')?.focus();
        _upShowError('crmError', '');
        _crmSync();
    }));
    _crmSync();
    (document.querySelector('#crmItems .crm-ok input') || document.querySelector('#crModal .crm-close')).focus();
}

function closeCorrectionModal() {
    const modal = document.getElementById('crModal');
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    document.body.style.overflow = '';
    document.getElementById('pfRequestCorrectionBtn')?.focus();
}

// Same checks as the setup Review step; returns [items, error].
function _crmCollect() {
    const items = [];
    const boxes = _crmChanging();
    if (!boxes.length) return [null, 'Uncheck at least one item that needs correcting.'];
    for (const box of boxes) {
        const item  = box.closest('.crm-item');
        const label = item.querySelector('.crm-item-name').textContent.trim();
        let changed = 0;
        for (const el of item.querySelectorAll('.crm-proposed [data-field]')) {
            if (el.disabled) continue;
            const field = el.dataset.field, value = el.value.trim();
            if (value === (_crmCurrent[field] || '')) continue;
            if (!value && !['Middle Name', 'Suffix'].includes(field)) return [null, `Please enter the correct ${field}.`];
            items.push({ field_name: field, proposed_value: value });
            changed++;
        }
        if (!changed) return [null, `The proposed ${label} is the same as what is on file. Change it, or tick "This information is correct".`];
    }
    return [items, null];
}

async function submitCorrectionModal() {
    const [items, err] = _crmCollect();
    if (err) return _upShowError('crmError', err);
    const remarks = document.getElementById('crmReason').value.trim();
    if (!remarks) {
        _upShowError('crmError', 'Please explain the reason for your correction request.');
        return document.getElementById('crmReason').focus();
    }
    const btn = document.getElementById('crmSubmit'), orig = btn.innerHTML;
    btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Submitting…';
    try {
        const res  = await fetch('/api/user/correction-requests', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ items, remarks }) });
        const data = await res.json().catch(() => ({ success: false }));
        if (!data.success) return _upShowError('crmError', data.error || 'Your request could not be sent. Please try again.');
        closeCorrectionModal();
        _showUpToast(data.message || 'Correction request submitted for Admin review.');
        await _refreshPendingCorrections();
    } catch (e) {
        _upShowError('crmError', 'Network error. Please try again.');
    } finally {
        btn.innerHTML = orig; _crmSync();
    }
}

// Pending badge on "My Correction Requests" (and its list, if open) after a new request.
async function _refreshPendingCorrections() {
    try {
        const d = await (await fetch('/api/user/correction-requests', { credentials: 'same-origin' })).json();
        if (!d.success) return;
        const n = d.requests.filter(r => r.status === 'Pending').length;
        const head = document.getElementById('crToggle');
        let badge = head?.querySelector('.cr-status');
        if (head && n && !badge) {
            badge = document.createElement('span');
            head.insertBefore(badge, head.querySelector('.cr-caret'));
        }
        if (badge) { badge.className = 'cr-status cr-Pending'; badge.textContent = `${n} pending`; badge.hidden = !n; }
        const list = document.getElementById('crList');
        if (list && !list.hidden) { _collapseMyCorrections(); toggleMyCorrections(); }
    } catch (e) { /* the badge refreshes on the next page load */ }
}

document.addEventListener('input', e => {
    if (e.target.closest && e.target.closest('#crModal')) { _upShowError('crmError', ''); _crmSync(); }
});
document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !document.getElementById('crModal')?.hidden) closeCorrectionModal();
});


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
