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

    // Phones/tablets: mobile browsers use a double-tap to zoom and often never fire
    // `dblclick`, so detect the double-tap ourselves — two taps on the same block within
    // 350 ms and without the finger moving (a scroll/drag is not a tap).
    const TAP_MS = 350, TAP_MOVE = 12;
    let lastTap = null, down = null;
    document.addEventListener('pointerdown', e => {
        if (e.pointerType !== 'touch') return;
        down = { x: e.clientX, y: e.clientY };
    }, { passive: true });
    document.addEventListener('pointerup', e => {
        if (e.pointerType !== 'touch' || !down) return;
        const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y) > TAP_MOVE;
        down = null;
        const pill = e.target.closest?.('.schedule-pill');
        if (moved || !pill || !pill._detail) { lastTap = null; return; }
        const now = Date.now();
        if (lastTap && lastTap.pill === pill && now - lastTap.t < TAP_MS) {
            lastTap = null;
            e.preventDefault();
            open(pill._detail, pill.style.backgroundColor);
        } else {
            lastTap = { pill, t: now };
        }
    });

    // Stop the browser's own double-tap zoom on schedule blocks so the second tap
    // reaches the handler above (scrolling and pinch-zoom still work).
    const st = document.createElement('style');
    st.textContent = '.schedule-pill{touch-action:manipulation;-webkit-tap-highlight-color:transparent;}';
    document.head.appendChild(st);
})();
