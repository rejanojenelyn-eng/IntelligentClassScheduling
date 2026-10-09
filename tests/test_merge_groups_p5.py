"""HC16 group model — P5 Manual Editor metadata (no real database), P7 semantics.

merge_groups.editor_context / annotate_event_keys are what /api/manual/merge_context
and the occupancy endpoints hand the Manual Editor. They must say exactly what the
server's HC16 check says (same MergeIndex), never write anything, and never decide
anything the JS has to recompute.
"""
import copy

import pytest

import merge_groups as mg
from test_merge_groups_p2 import GYM, LAB, QUAD, group, meeting, policy

CODE = 'NSTP 001'


def row(key, *, day='Sunday', start='09:00 AM', end='12:00 PM', room=GYM, fac='F1', code=CODE, ctype=None):
    r = {'key': key, 'subject_code': code, 'day': day, 'start_time': start, 'end_time': end,
         'room_id': room, 'faculty_id': fac}
    if ctype:
        r['class_type'] = ctype
    return r


def ctx(pol, section, rows=(), **kw):
    return mg.editor_context(pol, section, rows, **kw)


# ── clock / labels ──────────────────────────────────────────────────────────────

def test_editor_time_labels_round_trip():
    assert mg.clock('09:00 AM') == '09:00' and mg.clock('12:00 PM') == '12:00' and mg.clock('12:30 AM') == '00:30'
    assert mg.clock('01:30 PM') == '13:30' and mg.clock('13:30') == '13:30' and mg.clock('13:30:00') == '13:30'
    assert mg.label12('09:00') == '09:00 AM' and mg.label12('13:30') == '01:30 PM' and mg.label12('12:00') == '12:00 PM'


# ── Membership, badge data and meetings ────────────────────────────────────────

def test_non_member_section_gets_no_merge_metadata():
    out = ctx(policy(group(1, [1, 2])), 3, [row('1')])
    assert out['subjects'] == {} and out['rows'] == {}


def test_merged_member_gets_badge_data_meetings_and_event_key():
    g = group(1, [1, 2, 3])
    live = [{'sectionid': 2, 'subjectcode': CODE, 'day': 'Sunday', 'start_time': '09:00', 'end_time': '12:00',
             'roomid': GYM, 'faculty_id': 'F1', 'programcode': 'BSIT', 'sectionname': 'SEC-2'}]
    out = ctx(policy(g), 1, [row('7')], live_others=live)
    s = out['subjects'][CODE]
    assert (s['group_name'], s['section_count'], s['sections']) == ('Group 1', 3, ['SEC-1', 'SEC-2', 'SEC-3'])
    assert s['status'] == mg.EDITOR_MERGED and s['faculty_mode'] == 'MULTIPLE_FACULTY'
    # P7: nothing in the editor is controlled by the group any more
    assert not any(k in s for k in ('can_reset', 'reset_plan', 'faculty_controlled', 'reset_faculty'))
    (m,) = s['meetings']
    assert (m['day'], m['start_label'], m['end_label'], m['roomid'], m['class_type']) == \
        ('Sunday', '09:00 AM', '12:00 PM', GYM, 'Lecture')
    assert m['event_key'] == '1:11' and m['sections'] == ['SEC-2']
    r = out['rows']['7']
    assert (r['sync'], r['event_key'], r['merged_with']) == (mg.SYNC_IN, '1:11', ['SEC-2'])


def test_meeting_text_and_tooltip_material_carry_no_database_ids_as_primary_text():
    out = ctx(policy(group(1, [1, 2])), 1, [row('1')])
    s = out['subjects'][CODE]
    assert 'PUP GYM' in s['meetings'][0]['text'] and 'Sunday' in s['meetings'][0]['text']
    assert all(str(GYM) not in m['text'] for m in s['meetings'])


def test_disabled_policy_and_missing_section_give_nothing():
    pol = mg.GroupMergePolicy(mg.MergeIndex([group(1, [1, 2])]), enabled=False)
    assert ctx(pol, 1, [row('1')])['subjects'] == {}
    assert ctx(policy(group(1, [1, 2])), None, [row('1')])['subjects'] == {}
    assert ctx(None, 1, [row('1')])['subjects'] == {}


# ── P7: off the merged slot = an ordinary (separate) class, never locked ────────

@pytest.mark.parametrize('kw', [dict(room=QUAD), dict(day='Saturday'), dict(start='10:00 AM', end='01:00 PM')],
                         ids=['room', 'day', 'time'])
def test_member_off_the_slot_is_a_separate_class(kw):
    out = ctx(policy(group(1, [1, 2])), 1, [row('5', **kw)])
    s, r = out['subjects'][CODE], out['rows']['5']
    assert r['sync'] == mg.SYNC_SEPARATE and r['event_key'] is None and r['merged_with'] == []
    assert s['status'] == mg.EDITOR_NOT_MERGED


def test_editor_sync_agrees_with_server_hc16_for_the_same_rows():
    pol = policy(group(1, [1, 2]))
    gene_in = {'subject_code': CODE, 'section_id': 1, 'day': 'Sunday', 'days_list': ['Sunday'],
               'start_time': '09:00', 'end_time': '12:00', 'room_id': GYM}
    gene_out = dict(gene_in, room_id=QUAD)
    assert pol.index.classify(gene_in) == 'event' and pol.index.classify(gene_out) == 'separate'
    assert ctx(pol, 1, [row('1')])['rows']['1']['sync'] == mg.SYNC_IN
    assert ctx(pol, 1, [row('1', room=QUAD)])['rows']['1']['sync'] == mg.SYNC_SEPARATE


def test_allowed_sections_without_a_merged_slot_are_not_merged():
    out = ctx(policy(group(1, [1, 2], meetings=[], complete=())), 1, [row('1')])
    s = out['subjects'][CODE]
    assert s['status'] == mg.EDITOR_NOT_MERGED and s['meetings'] == [] and s['sections'] == ['SEC-1', 'SEC-2']
    assert out['rows']['1']['sync'] == mg.SYNC_SEPARATE


def test_a_partially_merged_subject_shows_only_the_merged_slice_as_merged():
    mts = [meeting(11, day='Monday', start='09:00', end='10:30')]
    pol = policy(group(1, [1, 2], meetings=mts, complete=()))
    out = ctx(pol, 1, [row('1', day='Monday', start='09:00 AM', end='10:30 AM'),
                       row('2', day='Thursday', start='09:00 AM', end='10:30 AM')])
    assert out['rows']['1']['sync'] == mg.SYNC_IN and out['rows']['2']['sync'] == mg.SYNC_SEPARATE
    assert out['subjects'][CODE]['status'] == mg.EDITOR_MERGED


def test_blank_slice_is_empty():
    out = ctx(policy(group(1, [1, 2])), 1, [row('1', day='', start='', end='')])
    assert out['rows']['1']['sync'] == mg.SYNC_EMPTY
    assert out['subjects'][CODE]['status'] == mg.EDITOR_NOT_MERGED


def test_each_meeting_maps_to_its_own_event_key():
    mts = [meeting(11, day='Monday', start='09:00', end='10:30'), meeting(12, day='Thursday', start='09:00', end='10:30')]
    out = ctx(policy(group(1, [1, 2], meetings=mts)), 1,
              [row('1', day='Monday', start='09:00 AM', end='10:30 AM'),
               row('2', day='Thursday', start='09:00 AM', end='10:30 AM')])
    assert (out['rows']['1']['event_key'], out['rows']['2']['event_key']) == ('1:11', '1:12')


def test_classification_ignores_any_client_class_type():
    pol = policy(group(1, [1, 2]))
    a = ctx(pol, 1, [row('1', ctype='Lab')])['rows']['1']
    b = ctx(pol, 1, [row('1')])['rows']['1']
    assert a['sync'] == b['sync'] == mg.SYNC_IN and a['event_key'] == b['event_key']


# ── Faculty: information only ───────────────────────────────────────────────────

def test_same_faculty_with_designee_mismatch_is_reported_not_locked():
    g = group(1, [1, 2], mode=mg.SAME_FACULTY, designated='F9')
    g['faculty_name'] = 'Reyes, Ana'
    out = ctx(policy(g), 1, [row('1', fac='F2')])
    s, r = out['subjects'][CODE], out['rows']['1']
    assert s['designated_faculty'] == {'id': 'F9', 'name': 'Reyes, Ana'} and 'faculty_controlled' not in s
    assert 'Reyes, Ana' in r['faculty_issue']
    ok = ctx(policy(g), 1, [row('1', fac='F9')])
    assert ok['rows']['1']['faculty_issue'] is None


def test_same_faculty_tba_is_an_advisory():
    out = ctx(policy(group(1, [1, 2], mode=mg.SAME_FACULTY)), 1, [row('1', fac='TBA')])
    s, r = out['subjects'][CODE], out['rows']['1']
    assert s['designated_faculty'] is None and r['faculty_issue'] is None and 'TBA' in r['faculty_advisory']
    assert s['status'] == mg.EDITOR_MERGED


def test_same_faculty_conflict_with_another_sections_live_faculty_comes_from_the_server():
    live = [{'sectionid': 2, 'subjectcode': CODE, 'day': 'Sunday', 'start_time': '09:00', 'end_time': '12:00',
             'roomid': GYM, 'faculty_id': 'F2'}]
    pol = policy(group(1, [1, 2], mode=mg.SAME_FACULTY))
    out = ctx(pol, 1, [row('1', fac='F1')], live_others=live)
    assert 'F2' in out['rows']['1']['faculty_issue']
    same = ctx(pol, 1, [row('1', fac='F2')], live_others=live)
    assert same['rows']['1']['faculty_issue'] is None


def test_multiple_faculty_never_flags_faculty():
    live = [{'sectionid': 2, 'subjectcode': CODE, 'day': 'Sunday', 'start_time': '09:00', 'end_time': '12:00',
             'roomid': GYM, 'faculty_id': 'F2'}]
    out = ctx(policy(group(1, [1, 2])), 1, [row('1', fac='F1')], live_others=live)
    assert out['rows']['1']['faculty_issue'] is None and out['rows']['1']['faculty_advisory'] is None


# ── Invalid configuration is advisory only ──────────────────────────────────────

def test_invalid_group_is_advisory_only_because_hc16_ignores_it():
    bad = group(1, [1, 2], errors=['Members teach different subjects.'])
    out = ctx(policy(bad), 1, [row('1', room=QUAD)], invalid_groups=[bad])
    s = out['subjects'][CODE]
    assert s['advisory_only'] is True and s['status'] == 'invalid_config'
    assert 'different subjects' in s['reason'] and out['rows'] == {}


# ── Occupancy annotation ────────────────────────────────────────────────────────

def _occ(section, *, room=GYM, day='Sunday', start='09:00', end='12:00', code=CODE):
    return {'section_id': section, 'subjectcode': code, 'daydesc': day, 'start_time': start,
            'end_time': end, 'roomid': room}


def test_occupancy_rows_carry_the_same_key_only_for_the_same_usable_event():
    pol = policy(group(1, [1, 2]))
    rows = [_occ(1), _occ(2), _occ(3), _occ(2, room=QUAD), _occ(2, code='MATH 1')]
    mg.annotate_event_keys(pol, rows)
    assert [r['merge_event'] for r in rows] == ['1:11', '1:11', None, None, None]


def test_occupancy_key_uses_the_route_room_and_time_ids():
    pol = policy(group(1, [1, 2], meetings=[meeting(11, stid=4, etid=10)]))
    rows = [{'section_id': 2, 'subjectcode': CODE, 'daydesc': 'Sunday', 'starttimeid': 4, 'endtimeid': 10}]
    mg.annotate_event_keys(pol, rows, room_id=GYM)
    assert rows[0]['merge_event'] == '1:11' and 'roomid' not in rows[0]


def test_incomplete_group_meeting_is_still_keyed_and_no_policy_gives_no_key():
    # P7: every stored meeting is a merged class, complete or not.
    g = group(1, [1, 2], complete=())
    rows = [_occ(2)]
    mg.annotate_event_keys(policy(g), rows)
    assert rows[0]['merge_event'] == '1:11'
    rows = [_occ(2)]
    mg.annotate_event_keys(None, rows)
    assert rows[0]['merge_event'] is None


def test_editor_context_never_mutates_its_inputs():
    g = group(1, [1, 2], mode=mg.SAME_FACULTY, designated='F9')
    rows = [row('1', room=QUAD), row('2')]
    g0, r0 = copy.deepcopy(g), copy.deepcopy(rows)
    ctx(policy(g), 1, rows)
    assert g == g0 and rows == r0


def test_load_editor_context_is_legacy_without_touching_the_database():
    assert mg.load_editor_context(None, {'hc_merge_model': 'legacy'}, 1, 1) == {'model': 'legacy'}
    assert mg.load_editor_context(None, {}, 1, 1) == {'model': 'legacy'}


# ── Group sizes, identity and boundaries ────────────────────────────────────────

import pathlib  # noqa: E402

import pytest  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.mark.parametrize('n', [2, 3, 4, 6])
def test_every_member_section_sees_the_same_event_for_any_group_size(n):
    pol = policy(group(1, list(range(1, n + 1))))
    keys = {ctx(pol, sec, [row('1')])['rows']['1']['event_key'] for sec in range(1, n + 1)}
    assert keys == {'1:11'} and ctx(pol, 1, [row('1')])['subjects'][CODE]['section_count'] == n


def test_event_keys_never_depend_on_session_or_version_ids():
    pol = policy(group(1, [1, 2]))
    a = [dict(_occ(2), sessionid=101, versionid=7)]
    b = [dict(_occ(2), sessionid=999, versionid=8)]
    mg.annotate_event_keys(pol, a)
    mg.annotate_event_keys(pol, b)
    assert a[0]['merge_event'] == b[0]['merge_event'] == '1:11'


def test_group_mode_editor_module_has_no_legacy_or_identity_logic():
    src = (ROOT / 'static' / 'js' / 'ACAD HEAD' / 'manualEditor.mergeGroups.js').read_text(encoding='utf-8')
    code = '\n'.join(l for l in src.splitlines() if not l.strip().startswith('//'))
    for banned in ('_getMergeMode', 'MERGE_SCOPE', 'section_pairs', 'hc_merge_section_pairs', "'NSTP'",
                   'startsWith(', 'merge_group_meeting', 'merge_group_member', 'MergeIndex'):
        assert banned not in code, banned
    assert "fetch('/api/manual/merge_context'" in code


def test_legacy_merge_code_is_kept_but_gated_off_in_group_mode():
    js = (ROOT / 'static' / 'js' / 'ACAD HEAD' / 'manualEditor.acad2.js').read_text(encoding='utf-8')
    tpl = (ROOT / 'templates' / 'academic' / 'manualScheduleEditor.html').read_text(encoding='utf-8')
    assert "if (MERGE_MODEL === 'groups') return 'none';" in js and 'Merge Class Detected' in js
    assert "MERGE_MODEL === 'groups') return 'none';" in tpl and 'Merge Class Detected' in tpl
