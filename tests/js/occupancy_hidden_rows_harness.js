// Room/time occupancy (real page code): a saved DB booking that an editor copy replaces
// (hiddenDbSchedules, incl. the per-session `s:<id>` key) must not block its old slot —
// neither the time lists (_getOccupiedRanges) nor the room list (_applyRoomAvailabilityFilter).
const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '..', '..');
const page = fs.readFileSync(path.join(root, 'templates', 'academic', 'manualScheduleEditor.html'), 'utf8');
const acad2 = fs.readFileSync(path.join(root, 'static', 'js', 'ACAD HEAD', 'manualEditor.acad2.js'), 'utf8');
const grabFrom = (src, name) => { const s = src.indexOf('function ' + name + '('); if (s < 0) throw new Error('missing ' + name);
    let i = src.indexOf(') {', s) + 2, d = 0;
    for (; i < src.length; i++) { if (src[i] === '{') d++; else if (src[i] === '}' && --d === 0) break; } return src.slice(s, i + 1); };

const _TS_TIMES = ['07:30 AM','08:00 AM','08:30 AM','09:00 AM','09:30 AM','10:00 AM','10:30 AM','11:00 AM','11:30 AM','12:00 PM','12:30 PM','01:00 PM','01:30 PM',
    '02:00 PM','02:30 PM','03:00 PM','03:30 PM','04:00 PM','04:30 PM','05:00 PM','05:30 PM','06:00 PM'];
const getTimeSlotIndex = t => _TS_TIMES.indexOf(t) + 1;

function mkItem(val) { const cls = new Set(['ts-ss-item']); return { dataset: { val }, style: {},
    classList: { add: (...c) => c.forEach(x => cls.add(x)), remove: (...c) => c.forEach(x => cls.delete(x)),
                 contains: c => cls.has(c) }, _cls: cls }; }

// BSCE1 GEED 005 slice 2: Saturday 12:30-2:00 in LQ212 (room 39), saved as session 16055.
// The user cleared the slice's room to search for one.
const roomItems = ['39', '40'].map(mkItem);
const roomList = { querySelectorAll: sel => sel.includes('room-unavailable') ? roomItems.filter(i => i._cls.has('room-unavailable')) : roomItems };
const fields = { '.ts-day-sel': 'Saturday', '.ts-start-hidden': '12:30 PM', '.ts-end-hidden': '02:00 PM', '.ts-room-hidden': '' };
const row = { id: 'ts-row-2', dataset: { existingJson: JSON.stringify({ _localTempId: 'DRAFT_2', versionid: 14485 }) },
    querySelector: q => (q in fields ? { value: fields[q] } : null) };
const els = { 'ts-row-2': row, 'tsr-list-2': roomList, sel_ay: { value: 'AY2627' }, sel_sem: { value: 'A' },
    sel_section: { value: '59' }, sel_subj: { value: 'GEED 005' }, sel_faculty: { value: '94018' } };
global.document = { getElementById: id => els[id] || null, querySelector: () => null, querySelectorAll: () => [] };
global.window = global;

var hiddenDbSchedules = new Set(['s:16055', 's:900']);
var pendingManualSchedule = [
    // this slice's own editor copy (excluded as "self") ...
    { temp_id: 'DRAFT_2', subject_code: 'GEED 005', ay: 'AY2627', sem: 'A', section_id: '59', room_id: '',
      day: 'Saturday', start_time: '12:30 PM', end_time: '02:00 PM', fromExisting: true, sessionid: 16055 },
    // ... and another class moved away from LQ212 Saturday 1:00-2:00 to Monday (not saved yet)
    { temp_id: 'DRAFT_9', subject_code: 'MATH 101', ay: 'AY2627', sem: 'A', section_id: '60', room_id: '39',
      day: 'Monday', start_time: '01:00 PM', end_time: '02:00 PM', fromExisting: true, sessionid: 900 },
];
window._roomDbCache = { '39|AY2627|A': [
    { daydesc: 'Saturday', startIdx: 10, endIdx: 13, subjectcode: 'GEED 005', versionid: 14485, sessionid: 16055, starttimeid: 11 },
    { daydesc: 'Saturday', startIdx: 11, endIdx: 13, subjectcode: 'MATH 101', versionid: 777, sessionid: 900, starttimeid: 12 },
    { daydesc: 'Saturday', startIdx: 15, endIdx: 17, subjectcode: 'ENSC 011', versionid: 888, sessionid: 901, starttimeid: 16 },
]};
window._roomsByDayCache = { 'Saturday|AY2627|A': {
    '39': [{ startIdx: 10, endIdx: 13, sessionid: 16055, versionid: 14485 }, { startIdx: 11, endIdx: 13, sessionid: 900, versionid: 777 }],
    '40': [{ startIdx: 11, endIdx: 12, sessionid: 950, versionid: 999 }],
}};

eval(grabFrom(acad2, '_isDbRowHidden') + ';' + grabFrom(page, '_getOccupiedRanges') + ';'
     + grabFrom(page, '_applyRoomAvailabilityFilter'));

const out = {};
// Time list: from LQ212 Saturday only ENSC 011 (2:30-3:30) still blocks; GEED 005's own row
// and MATH 101's moved-away row don't.
out.ranges = _getOccupiedRanges('39', 'Saturday', 'AY2627', 'A', 'DRAFT_2', null).map(r => [r.startIdx, r.endIdx]);
_applyRoomAvailabilityFilter(2);
out.unavailable = roomItems.filter(i => i._cls.has('room-unavailable')).map(i => i.dataset.val);
// With the hide-set empty (nothing loaded into the editor), the saved rows block again.
hiddenDbSchedules = new Set();
pendingManualSchedule = [];
out.ranges_unhidden = _getOccupiedRanges('39', 'Saturday', 'AY2627', 'A', 'PREVIEW_2', null).map(r => [r.startIdx, r.endIdx]);
_applyRoomAvailabilityFilter(2);
out.unavailable_unhidden = roomItems.filter(i => i._cls.has('room-unavailable')).map(i => i.dataset.val);
console.log(JSON.stringify(out));
