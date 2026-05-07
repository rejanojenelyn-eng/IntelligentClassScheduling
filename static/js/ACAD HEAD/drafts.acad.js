document.addEventListener('DOMContentLoaded', async () => {
    const container = document.getElementById('draftsList');
    try {
        const res  = await fetch('/api/schedule/drafts');
        const data = await res.json();

        if (!res.ok || !Array.isArray(data)) {
            const errMsg = (data && data.error) ? data.error : `Server error (${res.status})`;
            container.innerHTML = `
                <div class="drafts-empty">
                    <i class="fas fa-exclamation-circle" style="color:#c0392b;"></i>
                    Failed to load drafts: ${errMsg}
                </div>`;
            return;
        }

        if (!data.length) {
            container.innerHTML = `
                <div class="drafts-empty">
                    <i class="fas fa-folder-open"></i>
                    No saved drafts yet. Generate a schedule and save it as a draft.
                </div>`;
            return;
        }

        // Group by programcode + yearlevel — keep the most recent per group
        const groups = {};
        data.forEach(d => {
            const key = `${d.programcode || ''}-${d.yearlevel || ''}`;
            if (!groups[key] || (d.datecreated || '') > (groups[key].datecreated || '')) {
                groups[key] = d;
            }
        });

        const sorted = Object.values(groups).sort((a, b) =>
            (b.datecreated || '').localeCompare(a.datecreated || ''));

        container.innerHTML = sorted.map(d => {
            const dt = d.datecreated ? new Date(d.datecreated) : null;
            const dateStr = (dt && !isNaN(dt.getTime()))
                ? dt.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' }).toUpperCase()
                : '—';
            const termLabel = d.term === 'A' ? '1ST SEM' : d.term === 'B' ? '2ND SEM' : d.term === 'C' ? 'SUMMER' : d.term || '—';
            const ayRaw = String(d.acadyear || '');
            const ayDisplay = ayRaw.startsWith('AY') ? ayRaw.slice(2) : ayRaw;
            return `
                <div class="draft-card">
                    <div class="draft-card-info">
                        <div class="draft-card-title">${d.programcode || '—'} — YEAR ${d.yearlevel || '—'}</div>
                        <div class="draft-card-meta">SAVED LAST ${dateStr}</div>
                        <span class="draft-version-badge">DRAFT V${d.version_number} &bull; ${termLabel} &bull; AY ${ayDisplay}</span>
                    </div>
                    <a href="/schedule/drafts/${d.versionid}" class="btn-draft-view">VIEW</a>
                </div>`;
        }).join('');

    } catch(e) {
        console.error('Drafts load error:', e);
        container.innerHTML = `
            <div class="drafts-empty">
                <i class="fas fa-exclamation-circle" style="color:#c0392b;"></i>
                Failed to load drafts: ${e.message}
            </div>`;
    }
});
