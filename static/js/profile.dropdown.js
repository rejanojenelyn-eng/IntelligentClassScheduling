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
   PROFILE SETTINGS PANEL
══════════════════════════════════════════════════════ */
let _profileOptions = null;

async function openProfilePanel() {
    closeUpDropdown();
    document.getElementById('profilePanel').style.display = 'flex';
    if (!_profileData) await _loadUserCard();
    if (!_profileOptions) {
        try {
            const res = await fetch('/api/user/options');
            _profileOptions = await res.json();
        } catch(e) { _profileOptions = { designations: [], specializations: [] }; }
    }

    const d = _profileData || {};
    document.getElementById('pfEmpId').value    = d.emp_num  || '—';
    document.getElementById('pfFullName').value = d.fullname || d.role || '—';

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
    if (statusSel) statusSel.value = d.employment_status || 'Active';
}

function closeProfilePanel() {
    document.getElementById('profilePanel').style.display = 'none';
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
function openPasswordPanel() {
    closeUpDropdown();
    ['pwCurrent','pwNew','pwConfirm'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    document.getElementById('pwError').style.display = 'none';
    document.getElementById('passwordPanel').style.display = 'flex';
}
function closePasswordPanel() { document.getElementById('passwordPanel').style.display = 'none'; }

async function savePassword() {
    const btn   = document.querySelector('#passwordPanel .up-btn-save');
    const errEl = document.getElementById('pwError');
    errEl.style.display = 'none';
    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = 'Updating…';
    const fd = new FormData();
    fd.append('current_password', document.getElementById('pwCurrent').value);
    fd.append('new_password',     document.getElementById('pwNew').value);
    fd.append('confirm_password', document.getElementById('pwConfirm').value);
    try {
        const res  = await fetch('/api/user/password/change', { method: 'POST', body: fd });
        const data = await res.json();
        if (data.success) {
            closePasswordPanel();
            _showUpToast('Password updated successfully.');
        } else {
            errEl.textContent = data.error || 'Failed to update password.';
            errEl.style.display = 'block';
        }
    } catch(e) { errEl.textContent = 'Error: ' + e.message; errEl.style.display = 'block'; }
    finally { btn.disabled = false; btn.textContent = orig; }
}

/* ══════════════════════════════════════════════════════
   EMAIL PANEL
══════════════════════════════════════════════════════ */
function openEmailPanel() {
    closeUpDropdown();
    const emailEl = document.getElementById('pfEmail');
    if (emailEl && _profileData?.email) emailEl.value = _profileData.email;
    document.getElementById('emailError').style.display = 'none';
    document.getElementById('emailPanel').style.display = 'flex';
}
function closeEmailPanel() { document.getElementById('emailPanel').style.display = 'none'; }

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
        if (data.success) { closeEmailPanel(); _showUpToast('Email updated successfully.'); }
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
