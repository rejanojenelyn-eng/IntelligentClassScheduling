/* localArrangements.acad.js — Local Arrangements list page */

const _AY_SEM_MAP = {};   // populated from embedded server data

function laInit() {
    _laPopulateFilters();
    laLoadArrangements();
}

/* ── Populate filter dropdowns from server-embedded JSON ── */
function _laPopulateFilters() {
    const initEl   = document.getElementById('la-init-data');
    const ayData   = JSON.parse(initEl?.dataset.acadYears  || '[]');
    const progData = JSON.parse(initEl?.dataset.programs   || '[]');

    const aySel   = document.getElementById('laFilterAy');
    const progSel = document.getElementById('laFilterProg');

    ayData.forEach(ay => {
        const opt       = document.createElement('option');
        opt.value       = ay.id;
        opt.textContent = ay.label;
        aySel.appendChild(opt);
        if (ay.sems) _AY_SEM_MAP[ay.id] = ay.sems;
    });

    progData.forEach(p => {
        const opt       = document.createElement('option');
        opt.value       = p.code;
        opt.textContent = p.name;
        progSel.appendChild(opt);
    });
}

/* Rebuild the semester dropdown whenever AY changes */
function laRebuildSemFilter() {
    const ayId = document.getElementById('laFilterAy').value;
    const sel  = document.getElementById('laFilterSem');
    // Remove all except the placeholder
    Array.from(sel.options).forEach(o => { if (o.value) o.remove(); });

    const sems = _AY_SEM_MAP[ayId] || [];
    const labels = { A: '1st Semester', B: '2nd Semester', C: 'Summer' };
    sems.forEach(s => {
        const type = s.type || s;
        const opt  = document.createElement('option');
        opt.value       = type;
        opt.textContent = labels[type] || type;
        sel.appendChild(opt);
    });
}

/* Reset all filters */
function laResetFilters() {
    document.getElementById('laFilterAy').value   = '';
    document.getElementById('laFilterSem').value  = '';
    document.getElementById('laFilterProg').value = '';
    document.getElementById('laFilterYl').value   = '';
    // Clear dynamically added sem options
    const semSel = document.getElementById('laFilterSem');
    Array.from(semSel.options).forEach(o => { if (o.value) o.remove(); });
    laLoadArrangements();
}

/* ── Fetch and render arrangements ── */
async function laLoadArrangements() {
    const ay   = document.getElementById('laFilterAy').value;
    const sem  = document.getElementById('laFilterSem').value;
    const prog = document.getElementById('laFilterProg').value;
    const yl   = document.getElementById('laFilterYl').value;

    _laShowLoading(true);

    const params = new URLSearchParams();
    if (ay   && sem) { params.set('ay_id', ay); params.set('sem', sem); }
    if (prog) params.set('program', prog);
    if (yl)   params.set('year_level', yl);

    try {
        const res  = await fetch('/api/local/arrangements?' + params.toString());
        const data = await res.json();

        if (!data.success) {
            _laShowLoading(false);
            _laShowEmpty(true);
            return;
        }

        _laRenderCards(data.arrangements || []);
    } catch (e) {
        console.error('[localArrangements] load failed:', e);
        _laShowLoading(false);
        _laShowEmpty(true);
    }
}

function _laRenderCards(arrangements) {
    _laShowLoading(false);
    const list = document.getElementById('laCardList');
    const empty = document.getElementById('laEmpty');

    if (!arrangements.length) {
        list.classList.add('hidden');
        empty.classList.remove('hidden');
        return;
    }

    empty.classList.add('hidden');
    list.classList.remove('hidden');
    list.innerHTML = arrangements.map(a => _laCardHtml(a)).join('');
}

function _laCardHtml(a) {
    const ayLabel  = a.yearstart && a.yearend
        ? `A.Y ${a.yearstart}–${a.yearend}`
        : (a.acadyear || '—');
    const semLabel = a.sem_label || '—';
    const hcClass  = a.has_hc_violation ? ' la-card-hc' : '';
    const hcIcon   = a.has_hc_violation
        ? '<i class="fas fa-exclamation-triangle"></i>'
        : '<i class="fas fa-layer-group"></i>';
    const hcBadge  = a.has_hc_violation
        ? '<span class="la-badge la-badge-red"><i class="fas fa-exclamation-triangle"></i> HC Violation</span>'
        : '';
    const reason   = a.override_reason
        ? `<div class="la-card-reason"><i class="fas fa-quote-left" style="font-size:0.6rem;margin-right:4px;"></i>${_laEsc(a.override_reason)}</div>`
        : '';
    const createdAt = a.created_at
        ? new Date(a.created_at).toLocaleDateString('en-US', { year:'numeric', month:'short', day:'numeric' })
        : '—';

    return `
    <div class="la-card${hcClass}" id="la-card-${a.arrangementid}">
        <div class="la-card-icon">${hcIcon}</div>
        <div class="la-card-info">
            <div class="la-card-eyebrow">Local Arrangement #${a.arrangementid}</div>
            <div class="la-card-title">${_laEsc(a.programcode || '—')} — Year ${a.yearlevel || '—'}</div>
            <div class="la-card-badges">
                <span class="la-badge la-badge-green">${_laEsc(ayLabel)}</span>
                <span class="la-badge la-badge-blue">${_laEsc(semLabel)}</span>
                <span class="la-badge la-badge-gray">${a.session_count || 0} session${a.session_count === 1 ? '' : 's'}</span>
                <span class="la-badge la-badge-gray"><i class="fas fa-user" style="font-size:0.55rem;"></i> ${_laEsc(a.created_by || '—')}</span>
                <span class="la-badge la-badge-gray">${createdAt}</span>
                ${hcBadge}
            </div>
            ${reason}
        </div>
        <div class="la-card-actions">
            <button class="btn-la-view" onclick="laViewArrangement(${a.arrangementid})">
                <i class="fas fa-eye"></i> View
            </button>
            <button class="btn-la-deactivate" onclick="laDeactivate(${a.arrangementid})">
                <i class="fas fa-trash-alt"></i>
            </button>
        </div>
    </div>`;
}

/* ── View modal ── */
async function laViewArrangement(arrId) {
    try {
        const res  = await fetch(`/api/local/arrangement/${arrId}`);
        const data = await res.json();
        if (!data.success) { alert('Could not load arrangement.'); return; }

        const a    = data.arrangement;
        const sess = data.sessions || [];

        document.getElementById('laModalTitle').textContent =
            `${a.programcode || '—'} — Year ${a.yearlevel || '—'}`;

        const ayLabel = a.yearstart && a.yearend
            ? `A.Y ${a.yearstart}–${a.yearend}` : (a.acadyear || '—');
        document.getElementById('laModalMeta').textContent =
            `${ayLabel}  ·  ${a.sem_label || '—'}  ·  Arrangement #${a.arrangementid}`;

        // Info bar
        const hcHtml = a.has_hc_violation
            ? '<span style="background:#ffebee;color:#c62828;font-weight:900;border-radius:4px;padding:2px 8px;font-size:0.65rem;">⚠ HC VIOLATION</span>'
            : '<span style="background:#e8f5e9;color:#1b5e20;font-weight:900;border-radius:4px;padding:2px 8px;font-size:0.65rem;">✓ CLEAN</span>';
        const createdAt = a.created_at
            ? new Date(a.created_at).toLocaleDateString('en-US', { year:'numeric', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit' })
            : '—';
        document.getElementById('laModalInfoBar').innerHTML = `
            <div class="la-modal-info-item">
                <span class="la-modal-info-label">Program</span>
                <span class="la-modal-info-val">${_laEsc(a.programcode || '—')}</span>
            </div>
            <div class="la-modal-info-item">
                <span class="la-modal-info-label">Year Level</span>
                <span class="la-modal-info-val">${a.yearlevel || '—'}</span>
            </div>
            <div class="la-modal-info-item">
                <span class="la-modal-info-label">Period</span>
                <span class="la-modal-info-val">${_laEsc(ayLabel)} · ${_laEsc(a.sem_label || '—')}</span>
            </div>
            <div class="la-modal-info-item">
                <span class="la-modal-info-label">Constraint Status</span>
                ${hcHtml}
            </div>
            <div class="la-modal-info-item">
                <span class="la-modal-info-label">Created By</span>
                <span class="la-modal-info-val">${_laEsc(a.created_by || '—')}</span>
            </div>
            <div class="la-modal-info-item">
                <span class="la-modal-info-label">Saved On</span>
                <span class="la-modal-info-val">${createdAt}</span>
            </div>
            ${a.override_reason ? `
            <div class="la-modal-info-item" style="flex-basis:100%;">
                <span class="la-modal-info-label">Reason</span>
                <span style="font-size:0.78rem;color:#333;font-style:italic;">${_laEsc(a.override_reason)}</span>
            </div>` : ''}
        `;

        // Sessions table
        const tbody   = document.getElementById('laModalSessions');
        const noSess  = document.getElementById('laModalEmpty');
        if (!sess.length) {
            tbody.innerHTML = '';
            noSess.classList.remove('hidden');
        } else {
            noSess.classList.add('hidden');
            tbody.innerHTML = sess.map(s => `
                <tr>
                    <td style="font-weight:700;">${_laEsc(s.subjectcode || '—')}</td>
                    <td>${_laEsc(s.subjectname || '—')}</td>
                    <td>${_laEsc(s.instructor || '—')}</td>
                    <td>${_laEsc(s.daydesc || '—')}</td>
                    <td style="white-space:nowrap;">${_laEsc(s.start_time || '—')} – ${_laEsc(s.end_time || '—')}</td>
                    <td>${_laEsc(s.roomname || '—')}</td>
                </tr>`).join('');
        }

        const overlay = document.getElementById('laDetailModal');
        overlay.style.display = 'flex';
    } catch (e) {
        alert('Failed to load arrangement details.');
    }
}

function laCloseModal() {
    document.getElementById('laDetailModal').style.display = 'none';
}

/* ── Deactivate ── */
async function laDeactivate(arrId) {
    if (!confirm(`Remove Local Arrangement #${arrId}? This cannot be undone.`)) return;
    try {
        const res  = await fetch(`/api/local/arrangement/${arrId}/deactivate`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            const card = document.getElementById(`la-card-${arrId}`);
            if (card) {
                card.style.transition = 'opacity 0.3s, transform 0.3s';
                card.style.opacity    = '0';
                card.style.transform  = 'translateX(30px)';
                setTimeout(() => card.remove(), 310);
            }
            // Show empty state if no cards remain
            setTimeout(() => {
                if (!document.querySelector('.la-card')) {
                    document.getElementById('laCardList').classList.add('hidden');
                    document.getElementById('laEmpty').classList.remove('hidden');
                }
            }, 350);
        } else {
            alert(data.error || 'Could not remove arrangement.');
        }
    } catch (e) {
        alert('Network error. Please try again.');
    }
}

/* ── Helpers ── */
function _laShowLoading(show) {
    document.getElementById('laLoading').classList.toggle('hidden', !show);
    document.getElementById('laCardList').classList.toggle('hidden', show);
    document.getElementById('laEmpty').classList.add('hidden');
}

function _laShowEmpty(show) {
    document.getElementById('laEmpty').classList.toggle('hidden', !show);
    document.getElementById('laCardList').classList.add('hidden');
}

function _laEsc(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g,'&amp;')
        .replace(/</g,'&lt;')
        .replace(/>/g,'&gt;')
        .replace(/"/g,'&quot;');
}

/* Close modal on overlay click */
document.getElementById('laDetailModal').addEventListener('click', function(e) {
    if (e.target === this) laCloseModal();
});

/* Boot — synchronous init (filter data is embedded), then async load */
document.addEventListener('DOMContentLoaded', () => laInit());
