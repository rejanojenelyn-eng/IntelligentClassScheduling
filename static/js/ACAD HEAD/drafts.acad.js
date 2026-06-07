document.addEventListener('DOMContentLoaded', async () => {
    const container = document.getElementById('draftsList');
    const subtitle  = document.getElementById('draftsSubtitle');

    const setSubtitle = (text) => { if (subtitle) subtitle.textContent = text; };

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
            const res = await fetch(`/api/schedule/drafts/${pendingDeleteId}`, { method: 'DELETE' });
            const text = await res.text();
            let data;
            try { data = JSON.parse(text); }
            catch { data = { success: false, error: `Server returned HTTP ${res.status}. Try restarting the server.` }; }
            if (data.success) {
                pendingDeleteCard && pendingDeleteCard.remove();
                closeDcModal();
                // Update subtitle count
                const remaining = document.querySelectorAll('.draft-card').length;
                setSubtitle(`${remaining} saved draft${remaining !== 1 ? 's' : ''}`);
                if (!remaining) renderEmpty();
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

    function renderEmpty() {
        container.innerHTML = `
            <div class="drafts-empty">
                <div class="drafts-empty-icon"><i class="fas fa-folder-open"></i></div>
                <div class="drafts-empty-title">No Drafts Yet</div>
                <div class="drafts-empty-sub">Generate a schedule and save it as a draft to see it here.</div>
            </div>`;
        setSubtitle('No drafts saved yet');
    }

    // ── Load drafts ──
    try {
        const res  = await fetch('/api/schedule/drafts');
        const data = await res.json();

        if (!res.ok || !Array.isArray(data)) {
            const errMsg = (data && data.error) ? data.error : `Server error (${res.status})`;
            setSubtitle('Could not load drafts');
            container.innerHTML = `
                <div class="drafts-error">
                    <i class="fas fa-exclamation-circle"></i>
                    Failed to load drafts: ${errMsg}
                </div>`;
            return;
        }

        if (!data.length) {
            renderEmpty();
            return;
        }

        // Keep the most recent draft per program + year level
        const groups = {};
        data.forEach(d => {
            const key = `${d.programcode || ''}-${d.yearlevel || ''}`;
            if (!groups[key] || (d.datecreated || '') > (groups[key].datecreated || '')) {
                groups[key] = d;
            }
        });

        const sorted = Object.values(groups).sort((a, b) =>
            (b.datecreated || '').localeCompare(a.datecreated || ''));

        const count = sorted.length;
        setSubtitle(`${count} saved draft${count !== 1 ? 's' : ''}`);

        // Show VIEW ALL button — uses the first (most recent) draft's program as default filter
        const firstProg = sorted[0]?.programcode || '';
        window._draftPrograms = sorted.map(d => d.programcode).filter(Boolean);
        window._draftFirstProg = firstProg;
        const viewAllBtn = document.getElementById('btnDraftsViewAll');
        if (viewAllBtn) viewAllBtn.style.display = 'flex';

        container.innerHTML = sorted.map(d => {
            const dt = d.datecreated ? new Date(d.datecreated) : null;
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

            return `
            <div class="draft-card" data-vid="${d.versionid}">
                <div class="draft-card-icon">
                    <i class="fas fa-calendar-alt"></i>
                </div>
                <div class="draft-card-info">
                    <div class="draft-card-eyebrow">${prog} &bull; Year ${yr}</div>
                    <div class="draft-card-title">${prog} &mdash; Year ${yr}</div>
                    <div class="draft-card-badges">
                        <span class="dc-badge dc-badge-draft"><i class="fas fa-file-alt"></i> Draft v${d.version_number || 1}</span>
                        <span class="dc-badge dc-badge-ay"><i class="fas fa-graduation-cap"></i> AY ${ayDisplay}</span>
                        <span class="dc-badge dc-badge-sem"><i class="fas fa-book-open"></i> ${semLabel}</span>
                    </div>
                    <div class="draft-card-date"><i class="fas fa-clock" style="margin-right:4px;"></i>Saved ${dateStr}</div>
                </div>
                <div class="draft-card-actions">
                    <a href="/schedule/drafts/${d.versionid}" class="btn-dc btn-dc-approve">
                        <i class="fas fa-check-circle"></i> Approve
                    </a>
                    <a href="/schedule/drafts/${d.versionid}" class="btn-dc btn-dc-view">
                        <i class="fas fa-eye"></i> View
                    </a>
                    <button class="btn-dc btn-dc-delete"
                        onclick="openDeleteModal(${d.versionid}, '${label.replace(/'/g, "\\'")}', this.closest('.draft-card'))">
                        <i class="fas fa-trash-alt"></i>
                    </button>
                </div>
            </div>`;
        }).join('');

    } catch (e) {
        console.error('Drafts load error:', e);
        setSubtitle('Could not load drafts');
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
