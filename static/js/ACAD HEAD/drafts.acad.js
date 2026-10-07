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
    const prog      = d.programcode  || '—';
    const yr        = d.yearlevel    || '—';
    const sect      = d.sectionname  || '';
    const sectId    = d.sectionid    || '';
    const sectHasProg = sect && prog && sect.toUpperCase().startsWith(prog.toUpperCase());
    const label     = sect ? (sectHasProg ? sect : `${prog} ${sect}`) : `${prog} – Year ${yr}`;
    const source    = d.source || 'official';
    const isLocal   = source === 'local';


    const editorUrl = `${MANUAL_EDITOR_URL}?mode=program`
        + `&prog=${encodeURIComponent(d.programcode||'')}`
        + `&yl=${encodeURIComponent(d.yearlevel||'')}`
        + `&ay=${encodeURIComponent(d.acadyear||'')}`
        + `&sem=${encodeURIComponent(d.term||'')}`
        + `&scheduler=${source}`
        // Both sect (id) and sect_name are required together for the Manual Editor to
        // auto-select the section and load its data — sect_name alone was missing here,
        // which silently left the Section dropdown unselected.
        //
        // Kept as a plain URL string (real "&" separators) here — this value also needs to
        // work as an actual query string, not just HTML markup. The "&" -> "&amp;" escape
        // happens only where this gets embedded into the card's HTML below, since embedding
        // "&sect_name=..." unescaped in an href="..." attribute is its own bug: "&sect"
        // (without a semicolon) is a legacy HTML named entity for "§", so the browser's own
        // HTML parser silently rewrote "...&sect=1515&sect_name=BSARCH-1" into
        // "...&sect=1515§_name=BSARCH-1" before the link was ever clicked — Manual Editor
        // then never received real sect/sect_name params at all, however correct its own
        // handling of them was.
        + (sectId ? `&sect=${encodeURIComponent(sectId)}&sect_name=${encodeURIComponent(sect||'')}` : '');

    return `
    <div class="draft-card" data-vid="${d.versionid}" data-source="${source}">
        <div class="draft-card-icon" style="${isLocal ? 'background:#fff3e0;color:#e65100;' : ''}">
            <i class="fas ${isLocal ? 'fa-tools' : 'fa-calendar-alt'}"></i>
        </div>
        <div class="draft-card-info">
            <div class="draft-card-title">${label}</div>
            <div class="draft-card-badges">
                <span class="dc-badge dc-badge-ay"><i class="fas fa-graduation-cap"></i> AY ${ayDisplay}</span>
                <span class="dc-badge dc-badge-sem"><i class="fas fa-book-open"></i> ${semLabel}</span>
            </div>
        </div>
        <div class="draft-card-updated">
            <i class="far fa-calendar"></i>
            <div><small>Last updated</small><b>${dateStr}</b></div>
        </div>
        <div class="draft-card-actions">
            <a href="/schedule/drafts/${d.versionid}" class="btn-dc btn-dc-view">
                <i class="fas fa-eye"></i> View
            </a>
            <a href="${editorUrl.replace(/&/g, '&amp;')}" class="btn-dc btn-dc-edit-editor">
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

    // Deduplicate within the filtered set: keep most recent per program+year+section
    const groups = {};
    filtered.forEach(d => {
        const key = `${d.programcode || ''}-${d.yearlevel || ''}-${d.sectionid || ''}`;
        if (!groups[key] || (d.datecreated || '') > (groups[key].datecreated || '')) groups[key] = d;
    });

    let sorted = Object.values(groups).sort((a, b) =>
        (b.datecreated || '').localeCompare(a.datecreated || ''));
    const totalDrafts = sorted.length;
    _drfPopulateFilters(sorted);
    sorted = _drfFilterSort(sorted);

    if (!sorted.length && totalDrafts) {
        setSubtitle(`0 of ${totalDrafts} saved drafts`);
        container.innerHTML = `<div class="drafts-noresult"><i class="fas fa-filter"></i> No drafts match your filters.</div>`;
        return;
    }
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

    setSubtitle(sorted.length === totalDrafts
        ? `${totalDrafts} saved draft${totalDrafts !== 1 ? 's' : ''}`
        : `${sorted.length} of ${totalDrafts} saved drafts`);

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


/* ── Filter bar (search · Academic Year · Semester · Program · Sort) ── */
const _DRF_SEM = { A: '1st Semester', B: '2nd Semester', C: 'Summer' };
function _drfLabel(d) {
    const prog = d.programcode || '', sect = d.sectionname || '';
    return sect ? (sect.toUpperCase().startsWith(prog.toUpperCase()) ? sect : `${prog} ${sect}`) : `${prog} – Year ${d.yearlevel || ''}`;
}
function _drfFill(id, values, labelOf) {
    const sel = document.getElementById(id);
    if (!sel) return;
    const cur = sel.value;
    const first = sel.options[0] ? sel.options[0].outerHTML : '';
    sel.innerHTML = first + values.map(v => `<option value="${v}">${labelOf(v)}</option>`).join('');
    if (values.includes(cur)) sel.value = cur;
}
function _drfPopulateFilters(drafts) {
    const uniq = arr => [...new Set(arr.filter(Boolean))];
    _drfFill('drfAy', uniq(drafts.map(d => String(d.acadyear || ''))).sort().reverse(),
             v => `AY ${v.replace(/^AY/, '')}`);
    _drfFill('drfSem', uniq(drafts.map(d => d.term)).sort(), v => _DRF_SEM[v] || v);
    _drfFill('drfProg', uniq(drafts.map(d => d.programcode)).sort(), v => v);
}
function _drfFilterSort(drafts) {
    const q    = (document.getElementById('drfSearch')?.value || '').trim().toLowerCase();
    const ay   = document.getElementById('drfAy')?.value   || '';
    const sem  = document.getElementById('drfSem')?.value  || '';
    const prog = document.getElementById('drfProg')?.value || '';
    const sort = document.getElementById('drfSort')?.value || 'latest';
    const out = drafts.filter(d => {
        if (ay && String(d.acadyear || '') !== ay) return false;
        if (sem && d.term !== sem) return false;
        if (prog && d.programcode !== prog) return false;
        if (!q) return true;
        const hay = `${_drfLabel(d)} ${d.programcode || ''} year ${d.yearlevel || ''} ${d.sectionname || ''}`.toLowerCase();
        return q.split(/\s+/).every(t => hay.includes(t));
    });
    const byDate = (a, b) => (b.datecreated || '').localeCompare(a.datecreated || '');
    const byName = (a, b) => _drfLabel(a).localeCompare(_drfLabel(b), undefined, { numeric: true });
    return out.sort(sort === 'oldest' ? (a, b) => byDate(b, a)
                  : sort === 'name-asc' ? byName
                  : sort === 'name-desc' ? (a, b) => byName(b, a)
                  : byDate);
}
window.drfApply = function () { _renderDrafts(_allDrafts); };
