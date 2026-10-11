// End-time filter (real page code + merge-group helpers): a P7 merge slot listed twice
// (DB row + editor mirror) must still offer its exact end time.
// Real code: _filterEndTimeItems override + _getOccupiedRanges (page) + merge-group helpers.
const fs = require('fs');
const page = fs.readFileSync(require('path').join(__dirname, '..', '..', 'templates', 'academic', 'manualScheduleEditor.html'), 'utf8');
const mg = fs.readFileSync(require('path').join(__dirname, '..', '..', 'static', 'js', 'ACAD HEAD', 'manualEditor.mergeGroups.js'), 'utf8');
const grabFrom = (src, name) => { const s = src.indexOf('function ' + name + '('); if (s < 0) throw new Error('missing ' + name);
    let i = src.indexOf(') {', s) + 2, d = 0;
    for (; i < src.length; i++) { if (src[i] === '{') d++; else if (src[i] === '}' && --d === 0) break; } return src.slice(s, i + 1); };
const block = (src, a, b) => src.slice(src.indexOf(a), src.indexOf(b, src.indexOf(a)));
const _TS_TIMES = ['07:30 AM','08:00 AM','08:30 AM','09:00 AM','09:30 AM','10:00 AM','10:30 AM','11:00 AM','11:30 AM','12:00 PM','12:30 PM','01:00 PM','01:30 PM',
    '02:00 PM','02:30 PM','03:00 PM','03:30 PM','04:00 PM','04:30 PM','05:00 PM','05:30 PM','06:00 PM'];
function mkItem(val) { const cls = new Set(['ts-ss-item']); return { dataset: { val }, style: {},
    classList: { add: (...c) => c.forEach(x => cls.add(x)), remove: (...c) => c.forEach(x => cls.delete(x)),
                 toggle: (c, on) => on ? cls.add(c) : cls.delete(c), contains: c => cls.has(c) }, _cls: cls }; }
const endItems = _TS_TIMES.map(mkItem);
const endList = { querySelectorAll: sel => sel.includes('occ-dim') ? endItems.filter(i => i._cls.has('occ-dim')) : endItems };
const row = { id: 'ts-row-1', dataset: { existingJson: JSON.stringify({ _localTempId: 'DB_1', versionid: 13104 }) },
    querySelector: q => q === '.ts-room-hidden' ? { value: '33' } : q === '.ts-day-sel' ? { value: 'Monday' } : null };
const els = { 'tset-list-1': endList, 'ts-row-1': row, sel_ay: { value: 'AY2627' }, sel_sem: { value: 'A' },
    sel_section: { value: '525' }, sel_subj: { value: 'COMP 002' }, sel_faculty: { value: '12079' }, app_init_data: { dataset: {} } };
global.document = { getElementById: id => els[id] || null, querySelector: () => null, querySelectorAll: () => [] };
global.window = global;
window._roomDbCache = { '33|AY2627|A': [
    { daydesc: 'Monday', startIdx: 5, endIdx: 9, subjectcode: 'COMP 002', employee_number: '12079', versionid: 13104, merge_event: null, section_id: 525 },
    { daydesc: 'Monday', startIdx: 13, endIdx: 17, subjectcode: 'COMP 002', employee_number: '12079', versionid: 15897, merge_event: null, section_id: 552 },
]};
let pendingManualSchedule = [{ temp_id: 'DB_1', subject_code: 'COMP 002', ay: 'AY2627', sem: 'A', section_id: '525',
    room_id: '33', day: 'Monday', start_time: '02:00 PM', end_time: '04:00 PM', fromExisting: true }];
let _currentSubjectCode = 'COMP 002', _subjInfo = { total_hours: 5 };
const MERGE_MODEL = 'groups';
eval(mg.split('\n').filter(l => !/^\s*document\.addEventListener|^\s*window\.addEventListener/.test(l)).join('\n')
     .replace(/function _mgActive\(\)\s*\{[\s\S]*?\n\}/, 'function _mgActive() { return true; }'));
eval(grabFrom(page, '_filterEndTimeItems').replace('function _filterEndTimeItems', 'var _filterEndTimeItems = function') + ';'
     + grabFrom(page, '_getOccupiedRanges') + ';'
     + block(page, 'const _origFilterEndTimeItems', 'const _origTsOpen'));
_filterEndTimeItems(1, '02:00 PM');
let visible = endItems.filter(i => !i._cls.has('dim') && !i._cls.has('before-start')).map(i => i.dataset.val);
const out = { db_only: visible };
// DIT1's COMP 002 also kept in the editor (you had DIT1 open earlier in Room View):
pendingManualSchedule.push({ temp_id: 'DB_DIT1', subject_code: 'COMP 002', ay: 'AY2627', sem: 'A', section_id: '552', faculty_id: '12079',
    room_id: '33', day: 'Monday', start_time: '02:00 PM', end_time: '04:00 PM', fromExisting: true });
endItems.forEach(i => i._cls.forEach(c => c !== 'ts-ss-item' && i._cls.delete(c)));
_filterEndTimeItems(1, '02:00 PM');
const visible2 = endItems.filter(i => !i._cls.has('dim') && !i._cls.has('before-start')).map(i => i.dataset.val);
out.db_and_mirror = visible2; process.stdout.write(JSON.stringify(out));
