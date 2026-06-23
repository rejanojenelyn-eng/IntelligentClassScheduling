// VERSION_ID, SCHEDULE_GEN_URL, MANUAL_EDITOR_URL are defined inline in the HTML template above this script

const _DAY_SORT_ORDER = {
    'MON':0,'TUE':1,'WED':2,'THU':3,'FRI':4,'SAT':5,'SUN':6,
    'Monday':0,'Tuesday':1,'Wednesday':2,'Thursday':3,'Friday':4,'Saturday':5,'Sunday':6
};
function _sortDays(arr) {
    return [...arr].sort((a, b) => (_DAY_SORT_ORDER[a.trim()] ?? 99) - (_DAY_SORT_ORDER[b.trim()] ?? 99));
}
function _sortDaysStr(daysStr) {
    if (!daysStr) return daysStr;
    const parts = daysStr.split(/[\/,]+/).filter(Boolean);
    return _sortDays(parts).join('/');
}

(async function() {
    let scheduleData = [];
    let draftContext = {};
    let draftVersion = null;

    const DAYS_FULL = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
    const GRID_ORIGIN = 7 * 60 + 30;
    const COLORS = ['#16a085','#27ae60','#2980b9','#8e44ad','#2c3e50','#d35400','#c0392b','#f39c12','#1abc9c','#8e44ad'];

    function subjColor(code) {
        let h = 0;
        for (let i = 0; i < (code||'').length; i++) h = (code||'').charCodeAt(i) + ((h << 5) - h);
        return COLORS[Math.abs(h) % COLORS.length];
    }

    function parseMins(t) {
        if (!t) return null;
        t = t.trim();
        let m = t.match(/^(\d{1,2}):(\d{2})\s*(AM|PM)$/i);
        if (m) {
            let h = parseInt(m[1]), mn = parseInt(m[2]);
            if (m[3].toUpperCase() === 'PM' && h !== 12) h += 12;
            if (m[3].toUpperCase() === 'AM' && h === 12) h = 0;
            return h * 60 + mn;
        }
        m = t.match(/^(\d{1,2}):(\d{2})$/);
        if (m) return parseInt(m[1]) * 60 + parseInt(m[2]);
        return null;
    }

    function parseRange(timeStr) {
        if (!timeStr) return { s: null, e: null };
        const parts = timeStr.split(/\s*[-–]\s*/);
        return parts.length >= 2 ? { s: parseMins(parts[0]), e: parseMins(parts[parts.length-1]) } : { s: null, e: null };
    }

    const DAY_MAP = {
        'MONDAY':'Monday','TUESDAY':'Tuesday','WEDNESDAY':'Wednesday','THURSDAY':'Thursday',
        'FRIDAY':'Friday','SATURDAY':'Saturday','SUNDAY':'Sunday',
        'MON':'Monday','TUE':'Tuesday','WED':'Wednesday','THU':'Thursday',
        'FRI':'Friday','SAT':'Saturday','SUN':'Sunday'
    };

    function parseDays(daysStr) {
        if (!daysStr) return [];
        const result = [];
        const add = d => { if (!result.includes(d)) result.push(d); };
        daysStr.split(/[\/,\s]+/).filter(p=>p).forEach(part => {
            const up = part.toUpperCase();
            if (DAY_MAP[up]) { add(DAY_MAP[up]); return; }
            if (up === 'MTH') { add('Monday'); add('Thursday'); }
            else if (up === 'TF')  { add('Tuesday'); add('Friday'); }
            else if (up === 'WS')  { add('Wednesday'); add('Saturday'); }
            else if (up === 'TTH') { add('Tuesday'); add('Thursday'); }
            else if (up === 'MWF') { add('Monday'); add('Wednesday'); add('Friday'); }
        });
        return result;
    }

    function renderCalendar() {
        const wrapper = document.getElementById('dvGridWrapper');
        const table   = document.getElementById('dvTimetable');
        wrapper.querySelectorAll('.dv-pill').forEach(p => p.remove());

        if (!scheduleData.length) return;

        const sessions = [];
        scheduleData.forEach(cls => {
            const { s, e } = parseRange(cls.time);
            const days = cls.day ? [cls.day] : parseDays(cls.days);
            days.forEach(day => sessions.push({
                day, startMins: s, endMins: e,
                code:  cls.subject_code || '',
                name:  cls.description || cls.subject_name || cls.subjectname || cls.subject_code || '',
                instr: cls.instructor || 'TBA',
                room:  cls.room || 'TBA'
            }));
        });

        const firstCell = table.querySelector('tbody td:nth-child(2)');
        const timeCol   = table.querySelector('.dv-time-cell');
        const thead     = table.querySelector('thead');
        if (!firstCell || firstCell.offsetWidth === 0) {
            requestAnimationFrame(renderCalendar); return;
        }

        const colW   = firstCell.offsetWidth;
        const pxMin  = firstCell.offsetHeight / 30;
        const leftOff = timeCol.offsetWidth;
        const topOff  = thead.offsetHeight;

        const groups = {};
        sessions.forEach(s => (groups[s.day] = groups[s.day] || []).push(s));

        Object.entries(groups).forEach(([dayName, daySessions]) => {
            const dayIdx = DAYS_FULL.indexOf(dayName);
            if (dayIdx < 0) return;
            daySessions.sort((a,b) => a.startMins - b.startMins);

            daySessions.forEach((sess, idx) => {
                let { startMins: st, endMins: en } = sess;
                if (st === null) return;
                if (st < GRID_ORIGIN) st += 12 * 60;
                if (en <= st) en += 12 * 60;

                let oCount = 0, oIdx = 0;
                daySessions.forEach((o, oi) => {
                    let oS = o.startMins, oE = o.endMins;
                    if (oS < GRID_ORIGIN) oS += 12*60;
                    if (oE <= oS) oE += 12*60;
                    if (st < oE && en > oS) { oCount++; if (idx > oi) oIdx++; }
                });

                const pill = document.createElement('div');
                pill.className = 'dv-pill';
                pill.style.backgroundColor = subjColor(sess.code);
                const w = (colW - 6) / (oCount || 1);
                pill.style.width  = (w - 2) + 'px';
                pill.style.height = ((en - st) * pxMin - 2) + 'px';
                pill.style.left   = (leftOff + dayIdx * colW + oIdx * w + 3) + 'px';
                pill.style.top    = (topOff + (st - GRID_ORIGIN) * pxMin + 1) + 'px';
                pill.title = `${sess.code}\n${sess.name}\n${sess.instr}\n${sess.room}`;
                pill.innerHTML = `
                    <div class="dv-pill-subj">${sess.name}</div>
                    <div class="dv-pill-instr">${sess.instr}</div>
                    <div class="dv-pill-room">${sess.room}</div>`;
                wrapper.appendChild(pill);
            });
        });
    }

    function renderTable() {
        const tbody = document.getElementById('dvTableBody');
        if (!scheduleData.length) {
            tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:30px;color:#888;">No sessions found.</td></tr>';
            return;
        }
        tbody.innerHTML = scheduleData.map(r => `
            <tr>
                <td>${r.instructor || '—'}</td>
                <td style="font-weight:800;color:#630100;">${r.subject_code || '—'}</td>
                <td>${r.description || r.subject_name || '—'}</td>
                <td>${r.lec_hours || 0}</td>
                <td>${r.lab_hours || 0}</td>
                <td>${r.units || r.credit_units || 0}</td>
                <td>${r.time || '—'}</td>
                <td>${_sortDaysStr(r.days || r.day) || '—'}</td>
                <td>${r.room || 'TBA'}</td>
            </tr>`).join('');
    }

    function showCalView() {
        document.getElementById('dvCalWrapper').classList.remove('hidden');
        document.getElementById('dvTblWrapper').classList.add('hidden');
        document.getElementById('dvBtnCal').className = 'active-btn';
        document.getElementById('dvBtnTbl').className = 'outline-btn';
        renderCalendar();
    }

    function showTblView() {
        document.getElementById('dvTblWrapper').classList.remove('hidden');
        document.getElementById('dvCalWrapper').classList.add('hidden');
        document.getElementById('dvBtnTbl').className = 'active-btn';
        document.getElementById('dvBtnCal').className = 'outline-btn';
    }

    window.showCalView = showCalView;
    window.showTblView = showTblView;

    window.regenDraft = function() {
        window.location.href = SCHEDULE_GEN_URL;
    };

    window.saveDraft = async function() {
        const btn = document.getElementById('dvBtnSave');
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
        try {
            const res = await fetch('/api/schedule/save-draft', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ schedule_data: scheduleData, context: draftContext })
            });
            const data = await res.json();
            if (data.success) {
                alert(`Saved as Draft V${data.draft_version}`);
            } else {
                alert('Save failed: ' + (data.error || 'Unknown error'));
            }
        } catch(e) {
            alert('Connection error. Please try again.');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-save"></i> Save changes';
        }
    };

    window.goManual = function() {
        const ctx  = draftContext;
        const prog = encodeURIComponent(ctx.program   || '');
        const yl   = encodeURIComponent(ctx.yearLevel || '');
        const ay   = encodeURIComponent(ctx.acadYear  || '');
        const sem  = encodeURIComponent(ctx.term      || '');
        const src  = ctx.source === 'local' ? 'local' : 'official';
        window.location.href = `${MANUAL_EDITOR_URL}?mode=program&prog=${prog}&yl=${yl}&ay=${ay}&sem=${sem}&scheduler=${src}`;
    };

    window.approveDraft = function() {
        if (!scheduleData.length) { alert('No sessions loaded.'); return; }
        // Build publish selection modal
        const list = document.getElementById('publishSessionList');
        if (!list) { alert('Publish modal not found.'); return; }

        // Group sessions by subject
        const groups = {};
        scheduleData.forEach((s, idx) => {
            const code = s.subject_code || s.subjectcode || '?';
            if (!groups[code]) groups[code] = { name: s.description || code, sessions: [] };
            groups[code].sessions.push({ ...s, _idx: idx });
        });

        list.innerHTML = Object.entries(groups).map(([code, g]) => `
            <div style="margin-bottom:14px;">
                <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:800;font-size:0.8rem;color:#630100;margin-bottom:4px;">
                    <input type="checkbox" class="pub-subj-chk" data-code="${code}" checked
                        onchange="this.closest('div').querySelectorAll('.pub-sess-chk').forEach(c=>c.checked=this.checked)">
                    ${code} — ${g.name}
                </label>
                ${g.sessions.map(s => `
                    <label style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:6px 10px;border-radius:6px;background:#f9f6f6;margin-bottom:3px;font-size:0.76rem;color:#333;">
                        <input type="checkbox" class="pub-sess-chk" data-idx="${s._idx}" checked
                            onchange="(function(el){const all=el.closest('div').querySelectorAll('.pub-sess-chk');const subjChk=el.closest('div').querySelector('.pub-subj-chk');if(subjChk)subjChk.checked=[...all].every(c=>c.checked);})(this)">
                        <span style="color:#555;"><b>${s.days || (s.day||'').substring(0,3).toUpperCase()}</b> · ${s.time || ''}</span>
                        <span style="margin-left:auto;color:#888;">${s.room || '—'}</span>
                        <span style="color:#630100;font-weight:700;">${s.instructor || '—'}</span>
                    </label>`).join('')}
            </div>`).join('');

        document.getElementById('publishModal').style.display = 'flex';
    };

    function _dvConfirm(message, title) {
        return new Promise(resolve => {
            const overlay = document.createElement('div');
            overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:11000;display:flex;align-items:center;justify-content:center;';
            const box = document.createElement('div');
            box.style.cssText = 'background:#fff;border-radius:8px;padding:26px 28px;max-width:440px;width:92%;box-shadow:0 6px 32px rgba(0,0,0,0.22);font-family:inherit;';
            const t = document.createElement('div');
            t.style.cssText = 'font-size:0.82rem;font-weight:900;color:#630100;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:10px;';
            t.textContent = title || 'Confirm';
            const m = document.createElement('div');
            m.style.cssText = 'font-size:0.83rem;color:#444;margin-bottom:20px;line-height:1.6;white-space:pre-wrap;';
            m.textContent = message;
            const row = document.createElement('div');
            row.style.cssText = 'display:flex;gap:10px;justify-content:flex-end;';
            const cancel = document.createElement('button');
            cancel.textContent = 'Cancel';
            cancel.style.cssText = 'padding:8px 20px;background:#fff;border:1.5px solid #ccc;border-radius:6px;cursor:pointer;font-size:0.82rem;color:#555;font-family:inherit;';
            const ok = document.createElement('button');
            ok.textContent = 'Confirm';
            ok.style.cssText = 'padding:8px 22px;background:#630100;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:0.82rem;font-weight:700;font-family:inherit;';
            cancel.onclick = () => { overlay.remove(); resolve(false); };
            ok.onclick     = () => { overlay.remove(); resolve(true);  };
            row.appendChild(cancel); row.appendChild(ok);
            box.appendChild(t); box.appendChild(m); box.appendChild(row);
            overlay.appendChild(box);
            document.body.appendChild(overlay);
        });
    }

    window.confirmPublishSelected = async function() {
        const modal = document.getElementById('publishModal');
        const checked = [...document.querySelectorAll('.pub-sess-chk:checked')];
        const selectedIdx = new Set(checked.map(c => parseInt(c.dataset.idx)));
        const selectedData = scheduleData.filter((_, i) => selectedIdx.has(i));

        if (!selectedData.length) { alert('No sessions selected.'); return; }

        // Build summary of selected sessions
        const sessionLines = selectedData.map(s => {
            const code = s.subject_code || s.subjectcode || '—';
            const day  = s.day || s.daydesc || '—';
            const st   = s.start_time || (s.time ? s.time.split(' - ')[0] : '') || '—';
            const et   = s.end_time   || (s.time ? s.time.split(' - ')[1] : '') || '—';
            const room = s.room || s.roomname || 'TBA';
            return `• ${code} | ${day} | ${st} – ${et} | ${room}`;
        }).join('\n');

        modal.style.display = 'none';

        // Two-step confirmation
        const ok1 = await _dvConfirm(
            `You are about to publish ${selectedData.length} schedule${selectedData.length !== 1 ? 's' : ''}:\n\n${sessionLines}`,
            'Publish Schedule'
        );
        if (!ok1) { modal.style.display = 'flex'; return; }

        const ok2 = await _dvConfirm(
            'Are you sure you want to publish this schedule? This action will make the schedule visible to all users.',
            'Final Confirmation'
        );
        if (!ok2) { modal.style.display = 'flex'; return; }

        const btn = document.getElementById('dvBtnApprove');
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Publishing...';
        try {
            const res = await fetch('/api/schedule/approve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ schedule_data: selectedData, context: draftContext })
            });
            const data = await res.json();
            if (data.success) {
                btn.innerHTML = '<i class="fas fa-check-circle"></i> Published';
                await _dvConfirm(`${selectedData.length} session${selectedData.length !== 1 ? 's' : ''} published successfully!`, 'Schedule Published');
                window.location.reload();
            } else {
                let msg = data.error || 'Unknown error';
                if (data.violations && data.violations.length) {
                    msg = `Cannot publish — ${data.violations.length} constraint violation(s):\n\n` +
                          data.violations.map(v => `• [${v.rule}] ${v.subject}: ${v.detail}`).join('\n') +
                          '\n\nOpen the Manual Editor to fix these before approving.';
                }
                await _dvConfirm(msg, 'Publish Failed');
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-check-circle"></i> Approve Schedule';
                modal.style.display = 'flex';
            }
        } catch(e) {
            await _dvConfirm('Connection error. Please try again.', 'Error');
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-check-circle"></i> Approve Schedule';
            modal.style.display = 'flex';
        }
    };

    window.exportDraft = function() {
        alert('Export feature coming soon.');
    };

    // ── Load the draft ──────────────────────────────────────────────────
    try {
        const res  = await fetch(`/api/schedule/load-draft/${VERSION_ID}`);
        const data = await res.json();

        if (!data.success) {
            document.getElementById('dvLoading').innerHTML =
                `<i class="fas fa-exclamation-circle" style="color:#c0392b;font-size:2rem;display:block;margin-bottom:10px;"></i>
                 Failed to load draft: ${data.error || 'Not found'}.
                 <br><small style="color:#888;font-size:0.75rem;">Version ID: ${VERSION_ID}</small>`;
            return;
        }

        scheduleData = data.schedule_data || [];
        draftContext = data.context || {};
        draftVersion = data.version;

        const ctx = draftContext;
        const termLabel = ctx.term === 'A' ? '1st Semester' : ctx.term === 'B' ? '2nd Semester' : ctx.term === 'C' ? 'Summer' : ctx.term || '';
        const yearLabel = ctx.yearLevel ? `Year ${ctx.yearLevel}` : '';
        const dateStr   = new Date().toLocaleDateString('en-US', { year:'numeric', month:'long', day:'numeric' }).toUpperCase();

        document.getElementById('dvTitle').textContent =
            `${ctx.program || ''} ${yearLabel} SCHEDULE`;
        document.getElementById('dvDate').textContent = `SAVED: ${dateStr}`;

        const ayRaw = String(ctx.acadYear || '');
        const ayDigits = ayRaw.startsWith('AY') ? ayRaw.slice(2) : ayRaw;
        const ayDisplay = ayDigits.length >= 4
            ? `AY 20${ayDigits.slice(0,2)}-20${ayDigits.slice(2,4)}`
            : ayRaw || '—';
        document.getElementById('dvAcadYear').textContent  = ayDisplay;
        document.getElementById('dvTerm').textContent      = termLabel || '—';
        document.getElementById('dvProgram').textContent   = ctx.program    || '—';
        document.getElementById('dvYearLevel').textContent = yearLabel       || '—';
        document.getElementById('dvVersionBadge').textContent = 'DRAFT';

        // Show scheduler type badge
        const srcLabel = (draftContext.source === 'local') ? 'LOCAL SCHEDULER' : 'OFFICIAL SCHEDULER';
        const srcColor = (draftContext.source === 'local') ? '#e65100' : '#1a56db';
        const srcBg    = (draftContext.source === 'local') ? '#fff3e0' : '#e8f0fe';
        const srcBorder= (draftContext.source === 'local') ? '#ffcc80' : '#b6ccfc';
        const badge = document.getElementById('dvVersionBadge');
        badge.insertAdjacentHTML('beforebegin',
            `<span style="display:inline-block;background:${srcBg};color:${srcColor};border:1.5px solid ${srcBorder};border-radius:12px;padding:3px 12px;font-size:0.68rem;font-weight:900;letter-spacing:1px;text-transform:uppercase;align-self:center;">${srcLabel}</span>`
        );

        document.getElementById('dvContextBar').style.display = 'flex';

        document.getElementById('dvLoading').classList.add('hidden');
        document.getElementById('dvContent').classList.remove('hidden');

        renderTable();
        renderCalendar();

    } catch(e) {
        console.error('Draft load error:', e);
        document.getElementById('dvLoading').innerHTML =
            `<i class="fas fa-exclamation-circle" style="color:#c0392b;font-size:2rem;display:block;margin-bottom:10px;"></i>
             Connection error: ${e.message}`;
    }
})();
