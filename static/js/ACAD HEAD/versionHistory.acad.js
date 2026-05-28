(function () {
    let allVersions = [];
    let pendingRestoreId = null;
    let pendingRestoreLabel = '';

    // ── label helpers ──────────────────────────────────────────────────────────
    const semLabel = t =>
        t === 'A' ? '1st Semester' : t === 'B' ? '2nd Semester' : t === 'C' ? 'Summer' : (t || '—');

    const yrLabel = y => {
        const s = ['1st', '2nd', '3rd', '4th', '5th'];
        return (s[y - 1] || `Year ${y}`) + ' Year';
    };

    const ayDisplay = s =>
        s && s.startsWith('AY') ? s.slice(2).trim() : (s || '—');

    // ── version pill helpers ───────────────────────────────────────────────────
    function pillClass(status) {
        const s = (status || '').toLowerCase();
        if (s === 'published') return 'vh-pill-published';
        if (s === 'draft')     return 'vh-pill-draft';
        if (s === 'approved')  return 'vh-pill-approved';
        return 'vh-pill-archive';
    }

    function pillIcon(status) {
        const s = (status || '').toLowerCase();
        if (s === 'published') return 'fa-check-circle';
        if (s === 'draft')     return 'fa-file-alt';
        if (s === 'approved')  return 'fa-check-double';
        return 'fa-box-archive';
    }

    // ── grouping ───────────────────────────────────────────────────────────────
    // Returns: { programcode → { "AY2526-A" → { acadyear, term, levels: { yearlevel → version[] } } } }
    function groupVersions(versions) {
        const groups = {};
        versions.forEach(v => {
            const prog   = v.programcode || '—';
            const semKey = `${v.acadyear || ''}-${v.term || ''}`;
            if (!groups[prog])                              groups[prog] = {};
            if (!groups[prog][semKey])                      groups[prog][semKey] = { acadyear: v.acadyear, term: v.term, levels: {} };
            const yl = v.yearlevel || 0;
            if (!groups[prog][semKey].levels[yl])           groups[prog][semKey].levels[yl] = [];
            groups[prog][semKey].levels[yl].push(v);
        });
        // sort version chain oldest → newest
        for (const prog in groups)
            for (const k in groups[prog])
                for (const yl in groups[prog][k].levels)
                    groups[prog][k].levels[yl].sort((a, b) => a.version_number - b.version_number);
        return groups;
    }

    // ── render one typed version chain (Draft or Published) ──────────────────
    // versions: pre-filtered array for one chain type, sorted oldest→newest.
    // chainType: 'Draft' | 'Published'
    function renderChain(versions, chainType) {
        if (!versions.length)
            return '<span style="color:#bbb;font-size:0.7rem;font-style:italic;">None yet</span>';

        const getVNum  = v => chainType === 'Draft'
            ? (v.draft_version_number || 0)
            : (v.published_version_number || 0);
        const maxNum   = Math.max(...versions.map(getVNum));

        let html = '<div class="vh-version-chain">';
        versions.forEach((v, i) => {
            if (i > 0) html += '<span class="vh-version-arrow"><i class="fas fa-chevron-right"></i></span>';

            const vNum      = getVNum(v);
            const isCurrent = vNum > 0 && vNum === maxNum;
            const progSafe  = (v.programcode || '').replace(/'/g, "\\'");
            const label     = `${chainType} R${vNum}`;
            const badge     = isCurrent ? ` &bull; Current ${chainType}` : '';
            const pillCls   = isCurrent
                ? (chainType === 'Published' ? 'vh-pill-published' : 'vh-pill-draft')
                : 'vh-pill-archive';

            const overlayUrl = !isCurrent
                ? `/schedule?overlay_id=${v.versionid}` +
                  `&prog=${encodeURIComponent(v.programcode || '')}` +
                  `&yl=${v.yearlevel}` +
                  `&sem=${encodeURIComponent(v.term || '')}` +
                  `&ay=${encodeURIComponent(v.acadyear || '')}` +
                  `&vnum=${v.version_number}`
                : null;

            const labelSafe = label.replace(/'/g, "\\'");

            html += `
            <div class="vh-version-unit">
                <div style="display:flex;flex-direction:column;align-items:flex-start;gap:4px;">
                    <span class="vh-version-pill ${pillCls}">
                        ${label}${badge}
                    </span>
                </div>
                <div style="display:flex;gap:4px;align-items:center;">
                    ${!isCurrent ? `<a class="btn-vh-view" href="${overlayUrl}"
                        title="Compare this revision against the current schedule">
                        <i class="fas fa-eye"></i> View
                    </a>` : ''}
                    ${!isCurrent ? `<button class="btn-vh-restore"
                        onclick="vhOpenRestoreModal(${v.versionid},'${labelSafe}','${progSafe}',${v.yearlevel})"
                        title="Restore this revision">
                        <i class="fas fa-undo"></i> Restore
                    </button>` : ''}
                </div>
            </div>`;
        });
        html += '</div>';
        return html;
    }

    // Small inline type-label badge matching the pill palette
    function chainTypeLabel(text, bg, fg, bd) {
        return `<span style="display:inline-flex;align-items:center;min-width:62px;padding:3px 8px;` +
               `border-radius:4px;font-size:0.6rem;font-weight:800;letter-spacing:0.7px;` +
               `text-transform:uppercase;background:${bg};color:${fg};border:1px solid ${bd};` +
               `white-space:nowrap;flex-shrink:0;">${text}</span>`;
    }

    // ── main render ────────────────────────────────────────────────────────────
    function renderGroups(groups) {
        const content  = document.getElementById('vhContent');
        const progList = Object.keys(groups).sort();

        if (!progList.length) {
            content.innerHTML = `
            <div class="vh-empty">
                <div class="vh-empty-icon"><i class="fas fa-history"></i></div>
                <div class="vh-empty-title">No Version History Found</div>
                <div class="vh-empty-sub">Schedule versions will appear here once generated and saved.</div>
            </div>`;
            return;
        }

        let html = '';
        progList.forEach(prog => {
            // sort semester keys newest AY first, then A→B→C within same AY
            const semKeys = Object.keys(groups[prog]).sort((a, b) => {
                const [ayA, tA] = a.split('-');
                const [ayB, tB] = b.split('-');
                if (ayA !== ayB) return ayB.localeCompare(ayA);
                return tA.localeCompare(tB);
            });

            const totalVersions = semKeys.reduce((acc, k) =>
                acc + Object.values(groups[prog][k].levels).reduce((a, lvs) => a + lvs.length, 0), 0);

            html += `
            <div class="vh-program-section" data-prog="${prog}">
                <div class="vh-program-header" onclick="vhToggle(this)">
                    <div class="vh-program-left">
                        <div class="vh-program-icon"><i class="fas fa-graduation-cap"></i></div>
                        <div>
                            <div class="vh-program-name">${prog}</div>
                            <div class="vh-program-meta">${totalVersions} version${totalVersions !== 1 ? 's' : ''}</div>
                        </div>
                    </div>
                    <i class="fas fa-chevron-down vh-chevron"></i>
                </div>
                <div class="vh-program-body">`;

            semKeys.forEach(semKey => {
                const sg      = groups[prog][semKey];
                const ayDisp  = ayDisplay(sg.acadyear);
                const semLbl  = semLabel(sg.term);
                const ylKeys  = Object.keys(sg.levels).map(Number).sort();

                html += `
                    <div class="vh-sem-group">
                        <div class="vh-sem-header">
                            <i class="fas fa-calendar-alt"></i>
                            AY ${ayDisp} &mdash; ${semLbl}
                        </div>
                        <table class="vh-table">
                            <thead>
                                <tr>
                                    <th style="width:110px;">Year Level</th>
                                    <th>Version History</th>
                                </tr>
                            </thead>
                            <tbody>`;

                ylKeys.forEach(yl => {
                    const allVers = sg.levels[yl];

                    // Split by original_status (set by backend); fall back to computed counters
                    const draftVers = allVers
                        .filter(v => v.draft_version_number)
                        .sort((a, b) => a.draft_version_number - b.draft_version_number);
                    const publishedVers = allVers
                        .filter(v => v.published_version_number)
                        .sort((a, b) => a.published_version_number - b.published_version_number);

                    const draftRow = draftVers.length ? `
                        <div style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;">
                            ${chainTypeLabel('Draft','#fff8e1','#946300','#ffe082')}
                            ${renderChain(draftVers, 'Draft')}
                        </div>` : '';

                    const publishedRow = publishedVers.length ? `
                        <div style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;${draftVers.length ? 'margin-top:7px;' : ''}">
                            ${chainTypeLabel('Published','#e8f8ee','#1a7a2e','#b2dbb5')}
                            ${renderChain(publishedVers, 'Published')}
                        </div>` : '';

                    html += `
                                <tr>
                                    <td class="vh-year-cell">${yrLabel(yl)}</td>
                                    <td>${draftRow}${publishedRow}</td>
                                </tr>`;
                });

                html += `
                            </tbody>
                        </table>
                    </div>`;
            });

            html += `
                </div>
            </div>`;
        });

        content.innerHTML = html;
    }

    // ── toggle accordion ──────────────────────────────────────────────────────
    window.vhToggle = function (header) {
        const chevron = header.querySelector('.vh-chevron');
        const body    = header.nextElementSibling;
        header.classList.toggle('open');
        chevron.classList.toggle('open');
        body.classList.toggle('open');
    };

    // ── filter & re-render ────────────────────────────────────────────────────
    window.vhRenderFiltered = function () {
        const ay   = document.getElementById('vhFilterAY').value.trim();
        const sem  = document.getElementById('vhFilterSem').value.trim();
        const prog = document.getElementById('vhFilterProg').value.trim().toUpperCase();

        let filtered = allVersions;
        if (ay)   filtered = filtered.filter(v => v.acadyear === ay);
        if (sem)  filtered = filtered.filter(v => v.term === sem);
        if (prog) filtered = filtered.filter(v => (v.programcode || '').toUpperCase().includes(prog));

        renderGroups(groupVersions(filtered));
    };

    // ── restore modal ─────────────────────────────────────────────────────────
    window.vhOpenRestoreModal = function (versionId, vLabel, prog, yl) {
        pendingRestoreId    = versionId;
        pendingRestoreLabel = `${vLabel} of ${prog} — Year ${yl}`;
        document.getElementById('vhRestoreModalBody').textContent =
            `This will restore ${pendingRestoreLabel} to Draft status. ` +
            `Any existing Draft for this program/year/semester will be archived first.`;
        document.getElementById('vhRestoreModal').classList.add('active');
    };

    window.vhCloseRestoreModal = function () {
        document.getElementById('vhRestoreModal').classList.remove('active');
        pendingRestoreId    = null;
        pendingRestoreLabel = '';
    };

    document.getElementById('vhConfirmRestoreBtn').addEventListener('click', async () => {
        if (!pendingRestoreId) return;
        const btn = document.getElementById('vhConfirmRestoreBtn');
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Restoring&hellip;';

        try {
            const res  = await fetch(`/api/schedule/versions/${pendingRestoreId}/restore`, { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                vhCloseRestoreModal();
                await loadVersions();
            } else {
                alert('Restore failed: ' + (data.error || 'Unknown error'));
            }
        } catch (e) {
            alert('Restore failed: ' + e.message);
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-undo-alt"></i> Restore';
        }
    });

    // ── data load ─────────────────────────────────────────────────────────────
    async function loadVersions() {
        const content   = document.getElementById('vhContent');
        const subtitle  = document.getElementById('vhSubtitle');
        const filterBar = document.getElementById('vhFilterBar');

        content.innerHTML = '<div class="vh-loading"><i class="fas fa-spinner fa-spin"></i>&nbsp; Loading version history&hellip;</div>';

        try {
            const res  = await fetch('/api/schedule/versions');
            const data = await res.json();

            if (!res.ok || !Array.isArray(data)) {
                const err = (data && data.error) ? data.error : `Server error (${res.status})`;
                content.innerHTML = `<div class="vh-error"><i class="fas fa-exclamation-circle"></i> Failed to load: ${err}</div>`;
                subtitle.textContent = 'Could not load version history';
                return;
            }

            allVersions = data;

            // populate AY filter from actual data
            const aySet = [...new Set(data.map(v => v.acadyear).filter(Boolean))].sort().reverse();
            const aySelect = document.getElementById('vhFilterAY');
            aySelect.innerHTML = '<option value="">All Years</option>' +
                aySet.map(ay => `<option value="${ay}">AY ${ayDisplay(ay)}</option>`).join('');

            filterBar.style.display = 'flex';

            const groups     = groupVersions(data);
            const progCount  = Object.keys(groups).length;
            subtitle.textContent =
                `${data.length} version${data.length !== 1 ? 's' : ''} across ${progCount} program${progCount !== 1 ? 's' : ''}`;

            renderGroups(groups);

        } catch (e) {
            console.error('Version history load error:', e);
            content.innerHTML = `<div class="vh-error"><i class="fas fa-exclamation-circle"></i> Failed to load: ${e.message}</div>`;
            subtitle.textContent = 'Could not load version history';
        }
    }

    document.addEventListener('DOMContentLoaded', loadVersions);
})();
