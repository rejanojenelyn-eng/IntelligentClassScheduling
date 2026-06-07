let _allDrafts           = [];
let _currentDraftFilter  = 'official';   // 'official' | 'local'

/* ── Scheduler filter toggle ── */
window.setDraftSchedulerFilter = function(mode, btnEl) {
    _currentDraftFilter = mode;
    document.querySelectorAll('.drafts-mode-btn').forEach(b => b.classList.remove('active'));
    if (btnEl) btnEl.classList.add('active');
    _renderDrafts(_allDrafts);
};

/* ── Build one draft card ── */
function _buildDraftCard(d) {
    const dt      = d.datecreated ? new Date(d.datecreated) : null;
    const dateStr = (dt && !isNaN(dt.getTime()))
        ? dt.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
        : '—';

    const semLabel = d.term === 'A' ? '1st Semester'
                   : d.term === 'B' ? '2nd Semester'
                   : d.term === 'C' ? 'Summer'
                   : d.term || '—';

    const ayRaw     = String(d.acadyear || '');
    const ayDisplay = ayRaw.startsWith('AY') ? ayRaw.slice(2).trim() : ayRaw;
    const prog      = d.programcode || '—';
    const yr        = d.yearlevel   || '—';
    const label     = `${prog} – Year ${yr}`;
    const source    = d.source || 'official';
    const isLocal   = source === 'local';

    return `
    <div class="draft-card" data-vid="${d.versionid}" data-source="${source}">
        <div class="draft-card-icon" style="${isLocal ? 'background:#fff3e0;color:#e65100;' : ''}">
            <i class="fas ${isLocal ? 'fa-tools' : 'fa-calendar-alt'}"></i>
        </div>
        <div class="draft-card-info">
            <div class="draft-card-eyebrow">${prog} &bull; Year ${yr}
                ${isLocal ? '<span style="margin-left:6px;font-size:0.55rem;background:#fff3e0;color:#e65100;border:1px solid #ffcc80;border-radius:10px;padding:1px 7px;font-weight:900;letter-spacing:0.8px;">LOCAL</span>' : ''}
            </div>
            <div class="draft-card-title">${prog} &mdash; Year ${yr}</div>
            <div class="draft-card-badges">
                <span class="dc-badge dc-badge-draft"><i class="fas fa-file-alt"></i> Draft</span>
                <span class="dc-badge dc-badge-ay"><i class="fas fa-graduation-cap"></i> AY ${ayDisplay}</span>
                <span class="dc-badge dc-badge-sem"><i class="fas fa-book-open"></i> ${semLabel}</span>
            </div>
            <div class="draft-card-date"><i class="fas fa-clock" style="margin-right:4px;"></i>Saved ${dateStr}</div>
        </div>
        <div class="draft-card-actions">
            <a href="/schedule/drafts/${d.versionid}" class="btn-dc btn-dc-view">
                <i class="fas fa-eye"></i> View
            </a>
            <a href="${MANUAL_EDITOR_URL}?mode=program&prog=${encodeURIComponent(d.programcode||'')}&yl=${encodeURIComponent(d.yearlevel||'')}&ay=${encodeURIComponent(d.acadyear||'')}&sem=${encodeURIComponent(d.term||'')}&scheduler=${source}" class="btn-dc btn-dc-edit-editor">
                <i class="fas fa-edit"></i> Edit to Manual Editor
            </a>
            <a href="/schedule/drafts/${d.versionid}" class="btn-dc btn-dc-approve">
                <i class="fas fa-check-circle"></i> Approve
            </a>
            <button class="btn-dc btn-dc-delete"
                onclick="openDeleteModal(${d.versionid}, '${label.replace(/'/g, "\\'")}', this.closest('.draft-card'))">
                <i class="fas fa-trash-alt"></i>
            </button>
        </div>
    </div>`;
}

/* ── Render the filtered + deduplicated draft list ── */
function _renderDrafts(rawData) {
    const container = document.getElementById('draftsList');
    const subtitle  = document.getElementById('draftsSubtitle');
    const setSubtitle = t => { if (subtitle) subtitle.textContent = t; };

    // Normalise source: anything that is not explicitly 'local' is treated as 'official'.
    // This ensures old drafts (null/undefined source) always appear under Official Scheduler.
    const filtered = rawData.filter(d => (d.source === 'local' ? 'local' : 'official') === _currentDraftFilter);

    // Deduplicate within the filtered set: keep most recent per program+year
    const groups = {};
    filtered.forEach(d => {
        const key = `${d.programcode || ''}-${d.yearlevel || ''}`;
        if (!groups[key] || (d.datecreated || '') > (groups[key].datecreated || '')) groups[key] = d;
    });

    const sorted = Object.values(groups).sort((a, b) =>
        (b.datecreated || '').localeCompare(a.datecreated || ''));

    if (!sorted.length) {
        const modeLabel = _currentDraftFilter === 'local' ? 'Local Scheduler' : 'Official Scheduler';
        container.innerHTML = `
            <div class="drafts-empty">
                <div class="drafts-empty-icon"><i class="fas fa-folder-open"></i></div>
                <div class="drafts-empty-title">No Drafts Yet</div>
                <div class="drafts-empty-sub">No ${modeLabel} drafts saved yet.</div>
            </div>`;
        setSubtitle(`No ${modeLabel} drafts`);
        const viewAllBtn = document.getElementById('btnDraftsViewAll');
        if (viewAllBtn) viewAllBtn.style.display = 'none';
        return;
    }

    setSubtitle(`${sorted.length} saved draft${sorted.length !== 1 ? 's' : ''}`);

    const firstProg = sorted[0]?.programcode || '';
    window._draftPrograms  = sorted.map(d => d.programcode).filter(Boolean);
    window._draftFirstProg = firstProg;
    const viewAllBtn = document.getElementById('btnDraftsViewAll');
    if (viewAllBtn) viewAllBtn.style.display = 'flex';

    container.innerHTML = sorted.map(_buildDraftCard).join('');
}

/* ── Bootstrap ── */
document.addEventListener('DOMContentLoaded', async () => {
    const container = document.getElementById('draftsList');
    const subtitle  = document.getElementById('draftsSubtitle');

    // ── Delete modal state ──
    let pendingDeleteId   = null;
    let pendingDeleteCard = null;

    window.openDeleteModal = (versionid, label, cardEl) => {
        pendingDeleteId   = versionid;
        pendingDeleteCard = cardEl;
        document.getElementById('deleteModalBody').textContent =
            `This will permanently remove the draft for "${label}". This action cannot be undone.`;
        document.getElementById('deleteModal').classList.add('active');
    };

    window.closeDcModal = () => {
        document.getElementById('deleteModal').classList.remove('active');
        pendingDeleteId   = null;
        pendingDeleteCard = null;
    };

    document.getElementById('confirmDeleteBtn').addEventListener('click', async () => {
        if (!pendingDeleteId) return;
        const btn = document.getElementById('confirmDeleteBtn');
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Deleting…';
        try {
            const res  = await fetch(`/api/schedule/drafts/${pendingDeleteId}`, { method: 'DELETE' });
            const text = await res.text();
            let data;
            try { data = JSON.parse(text); }
            catch { data = { success: false, error: `Server returned HTTP ${res.status}.` }; }

            if (data.success) {
                // Remove from master list and re-render
                _allDrafts = _allDrafts.filter(d => d.versionid !== pendingDeleteId);
                closeDcModal();
                _renderDrafts(_allDrafts);
            } else {
                alert('Delete failed: ' + (data.error || 'Unknown error'));
            }
        } catch (e) {
            alert('Delete failed: ' + e.message);
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-trash-alt"></i> Delete';
        }
    });

    // ── Load drafts ──
    try {
        const res  = await fetch('/api/schedule/drafts');
        const data = await res.json();

        if (!res.ok || !Array.isArray(data)) {
            const errMsg = (data && data.error) ? data.error : `Server error (${res.status})`;
            if (subtitle) subtitle.textContent = 'Could not load drafts';
            container.innerHTML = `
                <div class="drafts-error">
                    <i class="fas fa-exclamation-circle"></i>
                    Failed to load drafts: ${errMsg}
                </div>`;
            return;
        }

        _allDrafts = data;
        _renderDrafts(_allDrafts);

    } catch (e) {
        console.error('Drafts load error:', e);
        if (subtitle) subtitle.textContent = 'Could not load drafts';
        container.innerHTML = `
            <div class="drafts-error">
                <i class="fas fa-exclamation-circle"></i>
                Failed to load drafts: ${e.message}
            </div>`;
    }
});

window.viewAllDraftsReport = function() {
    const prog = window._draftFirstProg || '';
    let url = '/reports?type=assignments';
    if (prog) url += '&program=' + encodeURIComponent(prog);
    window.location.href = url;
};
