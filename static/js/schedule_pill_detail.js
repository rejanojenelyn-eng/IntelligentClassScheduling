/* Official Class Schedule — double-click a calendar block for its full details.
   The calendar renderers (schedule.faculty.js / schedule.acad.js) attach the session
   as `pill._detail`; this shows it in a readable popup (Esc, ✕ or the backdrop close it). */
(function () {
    const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function close() {
        document.getElementById('spdOverlay')?.remove();
        document.removeEventListener('keydown', onKey);
    }
    function onKey(e) { if (e.key === 'Escape') close(); }

    function open(d, color) {
        close();
        const sec  = d.sectionname || [d.programcode, d.yearlevel].filter(Boolean).join(' ');
        const room = [d.roomname || 'TBA', d.buildingname].filter(Boolean).join(' · ');
        const rows = [
            ['fa-user',          'Instructor', d.instructor || 'TBA'],
            ['fa-calendar-day',  'Day',        d.daydesc],
            ['fa-clock',         'Time',       d.time],
            ['fa-door-open',     'Room',       room],
            ['fa-users',         'Section',    sec],
            ['fa-book',          'Hours',      (d.lecturehours != null || d.laboratoryhours != null)
                ? `Lec ${d.lecturehours || 0} · Lab ${d.laboratoryhours || 0}`
                  + (d.creditunits != null ? ` · ${d.creditunits} unit(s)` : '') : ''],
        ].filter(r => r[2]);
        const ov = document.createElement('div');
        ov.id = 'spdOverlay';
        ov.className = 'spd-overlay';
        ov.innerHTML = `
            <div class="spd-box" role="dialog" aria-modal="true" aria-labelledby="spdTitle">
                <div class="spd-head" style="background:${esc(color || '#800000')}">
                    <div>
                        <div class="spd-code">${esc(d.subjectcode)}</div>
                        <div class="spd-title" id="spdTitle">${esc(d.subjectname)}</div>
                    </div>
                    <button type="button" class="spd-close" aria-label="Close">&times;</button>
                </div>
                <div class="spd-body">${rows.map(([ic, k, v]) => `
                    <div class="spd-row"><i class="fas ${ic}"></i><span class="spd-k">${k}</span>
                        <span class="spd-v">${esc(v)}</span></div>`).join('')}
                </div>
            </div>`;
        ov.addEventListener('click', e => {
            if (e.target === ov || e.target.closest('.spd-close')) close();
        });
        document.body.appendChild(ov);
        document.addEventListener('keydown', onKey);
        ov.querySelector('.spd-close').focus();
    }

    document.addEventListener('dblclick', e => {
        const pill = e.target.closest('.schedule-pill');
        if (!pill || !pill._detail) return;
        e.preventDefault();
        window.getSelection?.().removeAllRanges();   // a double-click also selects the text
        open(pill._detail, pill.style.backgroundColor);
    });
})();
