"""
Local Scheduler Manual Editor actions — frontend behaviour.

Loads the REAL functions from manualScheduleEditor.html (plus getTimeSlotIndex from
manualEditor.acad2.js) into Node with a stubbed DOM and scripted fetch(), then checks
what each button actually sends and which message it shows:
  - Save → Draft-save only (never the publish endpoint), backend-shaped payload.
  - Publish → save, then explicit publish carrying the updated_at the save returned.
  - Version History Publish / Restore → send the stored updated_at, then refresh it.
  - Only a rejected fetch() is labelled "Network Error".
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'templates' / 'academic' / 'manualScheduleEditor.html').read_text(encoding='utf-8')
ACAD2 = (ROOT / 'static' / 'js' / 'ACAD HEAD' / 'manualEditor.acad2.js').read_text(encoding='utf-8')
LA_JS = (ROOT / 'static' / 'js' / 'ACAD HEAD' / 'localArrangements.acad.js').read_text(encoding='utf-8')

NODE = shutil.which('node')


def _slice(text, start, end):
    s = text.index(start)
    return text[s:text.index(end, s)]


def _editor_code():
    helpers_and_save = _slice(HTML, '// Latest server-issued updated_at per Local Arrangement',
                              'function _showLocalReasonModal()')
    publish_restore = _slice(HTML, 'async function publishLocalArrangementFromEditor()',
                             '/* ══════')
    slice_edit = _slice(HTML, 'function _syncSliceToPending(id)',
                        '/* ── Auto-load existing sessions into slice rows ── */')
    time_slots = re.search(r'const timeSlots = \[.*?\];', ACAD2).group(0)
    get_idx = _slice(ACAD2, 'function getTimeSlotIndex(timeStr)', 'async function renderGrid')
    return '\n'.join([time_slots, get_idx, slice_edit, helpers_and_save, publish_restore])


HARNESS = r"""
const T0 = '2026-09-28T09:00:00.000001', T1 = '2026-09-28T09:15:30.123456',
      T2 = '2026-09-28T09:20:01.654321', T3 = '2026-09-28T09:25:07.111111';
const calls = [], modals = [], errors = [];
const els = {};
function el(id, value = '') { return els[id] || (els[id] = { id, value, innerHTML: 'LABEL:' + id, disabled: false }); }
Object.entries({ sel_section: '552', sel_subj: 'COMP 001', sel_prog: 'DIT', sel_year: '1',
                 sel_ay: 'AY2627', sel_sem: 'A', sel_room: '' }).forEach(([k, v]) => el(k, v));
el('btnSaveLocalArr'); el('btnPublishLocalArr');
var document = { getElementById: id => els[id] || null, querySelectorAll: () => [] };
var window = {};
var formAyFilter = () => 'AY2627', formSemFilter = () => 'A';
var _currentSubjectCode = 'COMP 001', _skipExistingCheck = true;
var hiddenDbSchedules = new Set();
var pendingManualSchedule = [{
    fromExisting: true, section_id: '552', subject_code: 'COMP 001', day: 'Monday',
    start_time: '04:30 PM', end_time: '06:30 PM', room_id: '5', official_sessionid: 900,
    faculty_id: 'E1', ay: 'AY2627', sem: 'A'
}];
var showValidationModal = async (title, msg) => { modals.push([title, msg]); };
var showConfirmModal = async () => true;
var _showLocalReasonModal = async () => 'Room swap';
var _presentLocalEffectiveConflicts = () => {}, _presentLocalPublishConflicts = () => modals.push(['Conflicts', '']);
var _loadVersionHistoryPanel = () => {}, refreshScheduleGrid = async () => {};
var renderGrid = () => { if (SCENARIO.renderThrows) throw new Error('render boom'); };
var allRooms = [{ id: '5', name: 'LQ104' }, { id: '7', name: 'LQ207' }];
var _updateWorkflowBar = () => {}, _updateUnitProgress = () => {};

// Official occurrences as the Local-mode loader builds them (official_sessionid from
// /api/manual/existing_sessions' ss.sessionid). ACCO 202 meets twice a week.
function officialEntry(tempId, sessionid, code, day, start, end, room) {
    // As built by _loadExistingSessionsIntoSlices: Official versionid + as-loaded snapshot.
    return { temp_id: tempId, official_sessionid: sessionid, versionid: 1830, fromExisting: true,
             section_id: '477', subject_code: code, day, start_time: start, end_time: end,
             room_id: room, faculty_id: 'E1', ay: 'AY2627', sem: 'A',
             _loaded: { day, start_time: start, end_time: end, room_id: String(room) } };
}
function localEntry(tempId, sessionid, code, day, start, end, room) {
    // Loaded from an existing Local Arrangement: no Official versionid.
    return Object.assign(officialEntry(tempId, sessionid, code, day, start, end, room), { versionid: null });
}
function acco() {
    el('sel_section').value = '477';
    pendingManualSchedule = [
        officialEntry('DRAFT_A', 2272, 'ACCO 202', 'Monday',    '04:30 PM', '06:30 PM', '5'),
        officialEntry('DRAFT_B', 2273, 'ACCO 202', 'Thursday',  '04:30 PM', '06:30 PM', '5'),
        officialEntry('DRAFT_C', 2271, 'ACCO 201', 'Wednesday', '09:00 AM', '12:00 PM', '5'),
    ];
}
function sliceRow(tempId, day, start, end, room) {
    const fields = { '.ts-day-sel': { value: day }, '.ts-start-hidden': { value: start },
                     '.ts-end-hidden': { value: end }, '.ts-room-hidden': { value: room } };
    return { dataset: { existingJson: JSON.stringify({ _localTempId: tempId }) },
             querySelector: sel => fields[sel] || null };
}
console.error = (...a) => errors.push(a.map(String).join(' '));

function reply(status, body) {
    return { ok: status >= 200 && status < 300, status,
             json: async () => { if (body === '__HTML__') throw new SyntaxError('Unexpected token <'); return body; } };
}
var fetch = async (url, opts = {}) => {
    const body = opts.body ? JSON.parse(opts.body) : null;
    calls.push({ url, method: opts.method || 'GET', body });
    const r = SCENARIO.routes.find(([frag]) => url.includes(frag));
    if (!r) throw new Error('unexpected url ' + url);
    const out = r[1];
    if (out === 'REJECT') throw new TypeError('Failed to fetch');
    return reply(out[0], out[1]);
};

const OK_CONFLICTS = ['check_room_conflicts', [200, { success: true, conflicts: [] }]];
const OK_SAVE      = ['save_arrangement', [200, { success: true, arrangement_id: 41, status: 'Draft', updated_at: T1 }]];
const OK_PUBLISH   = ['/publish', [200, { success: true, arrangement_id: 41, status: 'Published', updated_at: T2 }]];
const OK_RESTORE   = ['/restore', [200, { success: true, new_arrangementid: 42, updated_at: T3 }]];

const SCENARIOS = {
    save_only:            { routes: [OK_CONFLICTS, OK_SAVE, OK_PUBLISH], run: () => saveLocalArrangement() },
    save_render_throws:   { routes: [OK_CONFLICTS, OK_SAVE], renderThrows: true, run: () => saveLocalArrangement() },
    save_409:             { routes: [OK_CONFLICTS, ['save_arrangement', [409, { success: false, error: 'Selected section does not belong to this Program.' }]]], run: () => saveLocalArrangement() },
    save_500_json:        { routes: [OK_CONFLICTS, ['save_arrangement', [500, { success: false, error: 'The Local Scheduler request could not be completed.' }]]], run: () => saveLocalArrangement() },
    save_500_html:        { routes: [OK_CONFLICTS, ['save_arrangement', [500, '__HTML__']]], run: () => saveLocalArrangement() },
    save_network:         { routes: [OK_CONFLICTS, ['save_arrangement', 'REJECT']], run: () => saveLocalArrangement() },
    conflict_network:     { routes: [['check_room_conflicts', 'REJECT'], OK_SAVE], run: () => saveLocalArrangement() },
    editor_publish:       { routes: [OK_CONFLICTS, OK_SAVE, OK_PUBLISH], run: () => publishLocalArrangementFromEditor() },
    editor_publish_stale: { routes: [OK_CONFLICTS, OK_SAVE, ['/publish', [409, { success: false, code: 'STALE_LOCAL_ARRANGEMENT', error: 'This Local Arrangement changed after you opened it.' }]]], run: () => publishLocalArrangementFromEditor() },
    history_publish:      { routes: [OK_PUBLISH], run: () => { _rememberLocalUpdatedAt(41, T1); return publishLocalArrangement(41, el('vhpBtn')); } },
    history_restore:      { routes: [OK_RESTORE], run: () => { _rememberLocalUpdatedAt(30, T0); return restoreLocalArrangement(30, el('vhpRestore')); } },
    room_only:            { routes: [OK_CONFLICTS, OK_SAVE], run: () => {
        acco();
        els['ts-row-1'] = sliceRow('DRAFT_C', 'Wednesday', '09:00 AM', '12:00 PM', '7');
        _syncSliceToPending(1);
        return saveLocalArrangement();
    } },
    both_meetings:        { routes: [OK_CONFLICTS, OK_SAVE], run: () => {
        acco();
        els['ts-row-1'] = sliceRow('DRAFT_A', 'Tuesday', '04:30 PM', '06:30 PM', '5');
        els['ts-row-2'] = sliceRow('DRAFT_B', 'Friday',  '04:30 PM', '06:30 PM', '5');
        _syncSliceToPending(1); _syncSliceToPending(2);
        return saveLocalArrangement();
    } },
    keeps_local_rows:     { routes: [OK_CONFLICTS, OK_SAVE], run: () => {
        // 2272 is already Local (loaded from an arrangement) and unchanged; 2271 edited now.
        acco();
        pendingManualSchedule[0] = localEntry('DRAFT_A', 2272, 'ACCO 202', 'Tuesday', '04:30 PM', '06:30 PM', '5');
        els['ts-row-1'] = sliceRow('DRAFT_C', 'Thursday', '09:00 AM', '12:00 PM', '5');
        _syncSliceToPending(1);
        return saveLocalArrangement();
    } },
    no_changes_save:      { routes: [OK_CONFLICTS, OK_SAVE], run: () => { acco(); return saveLocalArrangement(); } },
    no_changes_publish:   { routes: [
            ['/api/local/arrangements?', [200, { success: true, arrangements: [
                { arrangementid: 40, status: 'Draft', created_at: '2026-09-28T08:00:00', updated_at: T0 },
                { arrangementid: 41, status: 'Draft', created_at: '2026-09-28T09:00:00', updated_at: T1 }] }]],
            OK_PUBLISH], run: () => { acco(); return publishLocalArrangementFromEditor(); } },
    no_changes_no_draft:  { routes: [['/api/local/arrangements?', [200, { success: true, arrangements: [] }]], OK_PUBLISH],
                            run: () => { acco(); return publishLocalArrangementFromEditor(); } },
    edit_then_save:       { routes: [OK_CONFLICTS, OK_SAVE], run: () => {
        acco();
        // User moves ACCO 202's Monday meeting: day, start/end time AND room all change.
        els['ts-row-1'] = sliceRow('DRAFT_A', 'Friday', '06:00 PM', '07:30 PM', '7');
        _syncSliceToPending(1);
        return saveLocalArrangement();
    } },
};
const SCENARIO = SCENARIOS[process.argv[2]];

(async () => {
    let returned;
    try { returned = await SCENARIO.run(); }
    catch (e) { errors.push('UNCAUGHT ' + e); }
    console.log(JSON.stringify({ calls, modals, returned: returned || null,
                                 stored: _localArrUpdatedAt, errors,
                                 buttons: { save: els.btnSaveLocalArr, publish: els.btnPublishLocalArr } }));
})();
"""


@pytest.fixture(scope='module')
def harness(tmp_path_factory):
    if not NODE:
        pytest.skip('node is required for the frontend behaviour tests')
    path = tmp_path_factory.mktemp('local_editor') / 'harness.js'
    path.write_text(_editor_code() + '\n' + HARNESS, encoding='utf-8')

    def run(scenario):
        out = subprocess.run([NODE, str(path), scenario], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        result = json.loads(out.stdout.strip().splitlines()[-1])
        assert not any(e.startswith('UNCAUGHT') for e in result['errors']), result['errors']
        return result
    return run


def _urls(result):
    return [c['url'] for c in result['calls']]


def _titles(result):
    return [t for t, _ in result['modals']]


# ── Button binding (markup) ────────────────────────────────────────────────

def _button(id_):
    m = re.search(r'<button[^>]*id="%s"[^>]*>' % id_, HTML)
    assert m, id_
    return m.group(0)


def test_buttons_have_unique_ids_and_separate_handlers():
    assert HTML.count('id="btnSaveLocalArr"') == 1
    assert HTML.count('id="btnPublishLocalArr"') == 1
    assert 'onclick="saveLocalArrangement()"' in _button('btnSaveLocalArr')
    assert 'onclick="publishLocalArrangementFromEditor()"' in _button('btnPublishLocalArr')
    assert HTML.count('onclick="publishLocalArrangementFromEditor()"') == 1


def test_publish_button_uses_existing_action_button_styling():
    tag = _button('btnPublishLocalArr')
    assert 'class="btn-save-outlined btn-approve"' in tag
    assert 'btn-success' not in tag and 'class="btn ' not in tag


def test_editor_no_longer_depends_on_missing_selectors_or_show_toast():
    save = _slice(HTML, 'async function saveLocalArrangement(', 'function _showLocalReasonModal()')
    assert "getElementById('section')" not in save
    assert "getElementById('subject')" not in save
    assert 'showToast(' not in save
    assert "getElementById('sel_section')" in save and "getElementById('sel_subj')" in save


# ── Save ────────────────────────────────────────────────────────────────────

def test_save_button_saves_a_draft_only(harness):
    r = harness('save_only')
    assert not any('/publish' in u for u in _urls(r))
    assert _urls(r) == ['/api/local/check_room_conflicts', '/api/local/save_arrangement']
    assert r['returned']['status'] == 'Draft'
    assert 'Arrangement Saved' in _titles(r)


def test_save_payload_matches_backend_contract(harness):
    r = harness('save_only')
    body = next(c['body'] for c in r['calls'] if c['url'].endswith('save_arrangement'))
    assert 'context' not in body
    assert body['program'] == 'DIT' and body['year_level'] == 1
    assert body['ay_id'] == 'AY2627' and body['term'] == 'A'
    assert body['section_id'] == 552
    sess = body['sessions'][0]
    assert sess['starttimeid'] == 19 and sess['endtimeid'] == 23      # '04:30 PM' / '06:30 PM'
    assert sess['roomid'] == 5
    assert sess['official_sessionid'] == 900
    assert sess['subject_code'] == 'COMP 001' and sess['day'] == 'Monday'


def test_save_stores_returned_updated_at(harness):
    assert harness('save_only')['stored'] == {'41': '2026-09-28T09:15:30.123456'}


def test_js_error_after_successful_save_is_not_a_network_error(harness):
    r = harness('save_render_throws')
    assert 'Network Error' not in _titles(r)
    assert 'Arrangement Saved' in _titles(r)
    assert any('render boom' in e for e in r['errors'])     # logged for developers
    assert r['returned']['success'] is True


# ── Error classification ───────────────────────────────────────────────────

def test_http_4xx_shows_backend_message_not_network_error(harness):
    r = harness('save_409')
    assert r['modals'] == [['Save Failed', 'Selected section does not belong to this Program.']]


@pytest.mark.parametrize('scenario', ['save_500_json', 'save_500_html'])
def test_http_5xx_or_invalid_body_is_a_server_error(harness, scenario):
    r = harness(scenario)
    assert _titles(r) == ['Server Error']
    assert 'Network Error' not in _titles(r)


def test_fetch_rejection_on_save_is_a_network_error(harness):
    r = harness('save_network')
    assert r['modals'] == [['Network Error', 'Could not reach the server. Please try again.']]


def test_fetch_rejection_on_conflict_check_is_a_network_error_and_blocks_save(harness):
    r = harness('conflict_network')
    assert _titles(r) == ['Network Error']
    assert _urls(r) == ['/api/local/check_room_conflicts']      # still fail-closed


# ── Publish / Restore concurrency contract ─────────────────────────────────

def test_editor_publish_saves_then_publishes_with_fresh_updated_at(harness):
    r = harness('editor_publish')
    assert _urls(r) == ['/api/local/check_room_conflicts', '/api/local/save_arrangement',
                        '/api/local/arrangement/41/publish']
    publish = r['calls'][2]['body']
    assert publish == {'reason': 'Room swap', 'expected_updated_at': '2026-09-28T09:15:30.123456'}
    assert r['stored']['41'] == '2026-09-28T09:20:01.654321'   # replaced after publish
    assert 'Published' in _titles(r)
    assert r['buttons']['publish']['disabled'] is False
    assert r['buttons']['publish']['innerHTML'] == 'LABEL:btnPublishLocalArr'
    assert r['buttons']['save']['innerHTML'] == 'LABEL:btnSaveLocalArr'   # untouched by publish


def test_editor_publish_stale_shows_backend_message(harness):
    r = harness('editor_publish_stale')
    assert ['Cannot Publish', 'This Local Arrangement changed after you opened it.'] in r['modals']
    assert 'Network Error' not in _titles(r)
    assert r['stored']['41'] == '2026-09-28T09:15:30.123456'   # not advanced on failure


def test_version_history_publish_sends_and_refreshes_updated_at(harness):
    r = harness('history_publish')
    assert r['calls'][0]['body']['expected_updated_at'] == '2026-09-28T09:15:30.123456'
    assert r['stored']['41'] == '2026-09-28T09:20:01.654321'


def test_restore_sends_and_stores_updated_at(harness):
    r = harness('history_restore')
    assert r['calls'][0]['url'] == '/api/local/arrangement/30/restore'
    assert r['calls'][0]['body'] == {'expected_updated_at': '2026-09-28T09:00:00.000001'}
    assert r['stored']['42'] == '2026-09-28T09:25:07.111111'


def test_history_lists_refresh_stored_updated_at():
    panel = _slice(HTML, 'async function _loadVersionHistoryPanel()', 'function renderLocalCard')
    assert 'arr.forEach(a => _rememberLocalUpdatedAt(a.arrangementid, a.updated_at));' in panel
    latest = _slice(HTML, 'async function publishLatestLocalDraft()', 'function saveAsMakeupClass()')
    assert '_rememberLocalUpdatedAt(a.arrangementid, a.updated_at)' in latest


def test_local_arrangements_page_deactivate_sends_expected_updated_at():
    assert "_laUpdatedAt[String(a.arrangementid)] = a.updated_at" in LA_JS
    deactivate = _slice(LA_JS, 'async function laDeactivate(arrId)', '/* ── Helpers ── */')
    assert "expected_updated_at: _laUpdatedAt[String(arrId)] || null" in deactivate
    assert "_laUpdatedAt[String(arrId)] = data.updated_at" in deactivate
    assert 'Server error.' in deactivate


# ── Official occurrence identity (official_sessionid) ──────────────────────

APP_SRC = (ROOT / 'app.py').read_text(encoding='utf-8')


def test_official_api_exposes_the_schedule_sessions_id():
    api = _slice(APP_SRC, 'def api_manual_existing_sessions', 'def api_manual_section_schedule')
    official_sql = api[:api.index('draft_rows = query_db(')]
    assert 'SELECT ss.sessionid, ss.starttimeid' in official_sql
    # Local-mode rows expose the Official occurrence they replace, and never their own
    # local_arrangement_sessions.sessionid under the name the loader falls back to.
    local_sql = api[api.index('la_rows = query_db('):api.index('if la_rows:')]
    assert 'las.official_sessionid' in local_sql
    assert 'las.sessionid' not in local_sql


def test_loader_normalizes_the_occurrence_id_to_official_sessionid():
    loader = _slice(HTML, 'window._loadExistingSessionsIntoSlices = async function',
                    '/* ══════')
    assert 'official_sessionid: sess.official_sessionid || sess.sessionid || null,' in loader
    assert 'versionid:    sess.versionid || null,' in loader   # kept separate, not confused


def _payload_by_id(result):
    body = next(c['body'] for c in result['calls'] if c['url'].endswith('save_arrangement'))
    return {s['official_sessionid']: s for s in body['sessions']}


def test_editing_day_time_and_room_keeps_the_official_occurrence_id(harness):
    sessions = _payload_by_id(harness('edit_then_save'))
    moved = sessions[2272]
    assert moved['day'] == 'Friday'
    assert moved['starttimeid'] == 22 and moved['endtimeid'] == 25     # 06:00 PM – 07:30 PM
    assert moved['roomid'] == 7
    assert moved['subject_code'] == 'ACCO 202'


def test_only_the_changed_occurrence_is_sent(harness):
    # ACCO 202 Monday moved; Thursday (2273) and ACCO 201 (2271) stay Official.
    assert set(_payload_by_id(harness('edit_then_save'))) == {2272}


def test_room_only_change_keeps_the_occurrence_id(harness):
    sessions = _payload_by_id(harness('room_only'))
    assert set(sessions) == {2271}
    assert sessions[2271]['roomid'] == 7 and sessions[2271]['day'] == 'Wednesday'
    assert sessions[2271]['starttimeid'] == 4 and sessions[2271]['endtimeid'] == 10


def test_multi_occurrence_subject_keeps_distinct_ids(harness):
    sessions = _payload_by_id(harness('both_meetings'))
    assert set(sessions) == {2272, 2273}
    assert sessions[2272]['day'] == 'Tuesday' and sessions[2273]['day'] == 'Friday'


def test_rows_already_local_are_kept_with_new_edits(harness):
    sessions = _payload_by_id(harness('keeps_local_rows'))
    assert set(sessions) == {2271, 2272}
    assert sessions[2272]['day'] == 'Tuesday'


def test_save_with_no_changes_sends_nothing(harness):
    r = harness('no_changes_save')
    assert r['calls'] == []
    assert _titles(r) == ['No Changes']


def test_publish_without_changes_publishes_the_latest_saved_draft(harness):
    r = harness('no_changes_publish')
    assert 'status=Draft' in _urls(r)[0] and 'section_id=477' in _urls(r)[0]
    assert _urls(r)[1] == '/api/local/arrangement/41/publish'           # newest Draft
    assert r['calls'][1]['body']['expected_updated_at'] == '2026-09-28T09:15:30.123456'
    assert not any('save_arrangement' in u for u in _urls(r))
    assert ['Published', 'Local arrangement published successfully.'] in r['modals']


def test_publish_without_changes_or_draft_explains(harness):
    r = harness('no_changes_no_draft')
    assert _titles(r) == ['No Draft to Publish']
    assert not any('/publish' in u for u in _urls(r))
