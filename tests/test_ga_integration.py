"""
Phase B checkpoint 4: GA integration tests (selection, crossover, mutation,
repair, and final-candidate acceptance) against the finalized CSP hard
constraints and SC1-SC9 fitness model from checkpoints 1-3.

Letters A-N below map to the checkpoint 4 task's required test list.

Most tests are pure unit tests against IntelligentScheduler's GA-operator
methods directly (_fitness, _mutate, _repair_overlaps, _crossover,
_is_better_candidate, _select_survivors) using small hand-built gene
fixtures -- no database required, same convention as
test_final_soft_constraints.py / test_csp_validator.py. Two tests
(J/N, K-adjacent) exercise the real generate_draft() production path against
the local database and are skipped automatically when it isn't reachable.
"""
import random
from datetime import time

from conftest import requires_db
from scheduler import IntelligentScheduler, CSPValidator


def _sched(config=None):
    s = IntelligentScheduler()
    cfg = config if config is not None else {}
    s.csp = CSPValidator(config=cfg)
    s._hc_cfg = cfg
    return s


def _gene(**over):
    g = {
        'subject_code': 'IT101', 'class_type': 'Lecture', 'course': 'BSIT',
        'faculty_id': 'F1', 'room_id': 1, 'room_type': 'Lecture',
        'day': 'Monday', 'days_list': ['Monday'],
        'start_time': time(7, 30), 'end_time': time(9, 0),
        'duration_hrs': 1.5, 'lec_hours': 3, 'lab_hours': 0, 'units': 3,
    }
    g.update(over)
    return g


def _faculty(emp_id, **over):
    f = {
        'employeenumber': emp_id,
        'fullname': f'Faculty {emp_id}',
        'specializationname': '',
        'employeestatus': 'Permanent',
        'designationid': None,
        'nightteachingservice': 0,
        'employeetype': {
            'regular_start': time(7, 30), 'regular_end': time(16, 30),
            'regularload': 24, 'parttimeload': 6, 'teachingsubstitution': 0,
        },
    }
    f.update(over)
    return f


def _room(room_id, roomtype='Lecture'):
    return {'roomid': room_id, 'roomname': f'Room {room_id}', 'roomtype': roomtype}


def _subject(code, lec=3, lab=0):
    return {
        'subjectcode': code, 'subjectname': code, 'lecturehours': lec,
        'laboratoryhours': lab, 'creditunits': lec + lab, 'offeringcode': 'BSIT',
    }


# ── A. Valid candidate survives GA processing ──────────────────────────────

def test_a_valid_candidate_survives_mutation_and_repair_cycles():
    sched = _sched()
    faculty_map = {'F1': _faculty('F1'), 'F2': _faculty('F2')}
    faculty_list = list(faculty_map.values())
    rooms = [_room(1), _room(2)]
    subjects_by_code = {'IT101': _subject('IT101'), 'IT102': _subject('IT102')}

    for seed in range(10):
        random.seed(1000 + seed)
        individual = [
            _gene(subject_code='IT101', faculty_id='F1', room_id=1,
                  day='Monday', days_list=['Monday'],
                  start_time=time(7, 30), end_time=time(9, 0)),
            _gene(subject_code='IT102', faculty_id='F2', room_id=2,
                  day='Tuesday', days_list=['Tuesday'],
                  start_time=time(9, 0), end_time=time(10, 30)),
        ]
        assert sched._fitness(individual, faculty_map)[1] == 0  # starts feasible

        child = [{**g, 'days_list': list(g['days_list'])} for g in individual]
        if random.random() < 0.30:
            child = sched._mutate(child, subjects_by_code, faculty_list, faculty_map, rooms)
        sched._repair_overlaps(child, faculty_map)

        _, n_viol = sched._fitness(child, faculty_map)
        assert n_viol == 0, f'seed {seed}: valid candidate became hard-infeasible after GA ops'


# ── B. Hard-invalid candidate cannot win selection over high soft score ────

def test_b_is_better_candidate_never_prefers_more_violations_over_fewer():
    # A candidate with hard violations and an artificially huge soft score
    # must never be judged "better" than an already-feasible one.
    assert IntelligentScheduler._is_better_candidate(
        score=999999, n_violations=3, best_score=500, least_violations=0
    ) is False
    # Among two candidates with the SAME violation count, the higher score wins
    # (this is the checkpoint 4 fix -- the old code only compared scores when
    # n_violations was exactly 0, silently keeping a worse-scoring candidate
    # at any nonzero tie).
    assert IntelligentScheduler._is_better_candidate(
        score=600, n_violations=2, best_score=500, least_violations=2
    ) is True
    # Fewer violations always wins even with a much lower score.
    assert IntelligentScheduler._is_better_candidate(
        score=100, n_violations=1, best_score=999, least_violations=5
    ) is True


def test_b_select_survivors_ranks_feasible_above_high_scoring_infeasible():
    scored = [
        (999999, 3, 'hard_invalid_but_high_score'),
        (10, 0, 'feasible_low_score'),
        (500, 0, 'feasible_high_score'),
    ]
    survivors = IntelligentScheduler._select_survivors(scored, elite_n=2)
    assert survivors == ['feasible_high_score', 'feasible_low_score']


# ── C. Crossover-created conflict is repaired ───────────────────────────────

def test_c_crossover_created_conflict_is_repaired():
    """Force a 2-gene crossover (split is always 1 for len==2 parents) where
    parent1's gene[0] and parent2's gene[1] collide on faculty+room+time --
    a conflict that exists only in the CHILD, not in either parent alone."""
    sched = _sched()
    faculty_map = {'F1': _faculty('F1')}
    p1 = [
        _gene(subject_code='IT101', faculty_id='F1', room_id=1,
              day='Monday', days_list=['Monday'],
              start_time=time(7, 30), end_time=time(9, 0)),
        _gene(subject_code='IT102', faculty_id='F1', room_id=1,
              day='Tuesday', days_list=['Tuesday'],
              start_time=time(9, 0), end_time=time(10, 30)),
    ]
    p2 = [
        _gene(subject_code='IT101', faculty_id='F1', room_id=2,
              day='Wednesday', days_list=['Wednesday'],
              start_time=time(9, 0), end_time=time(10, 30)),
        _gene(subject_code='IT102', faculty_id='F1', room_id=1,
              day='Monday', days_list=['Monday'],
              start_time=time(7, 30), end_time=time(9, 0)),
    ]
    child = IntelligentScheduler._crossover(p1, p2)
    # child = [p1[0], p2[1]]: both land on faculty F1, room 1, Monday 7:30-9:00
    assert child[0]['subject_code'] == 'IT101' and child[1]['subject_code'] == 'IT102'
    _, n_before = sched._fitness(child, faculty_map)
    assert n_before > 0   # the crossover really did create a conflict

    sched._repair_overlaps(child, faculty_map)
    _, n_after = sched._fitness(child, faculty_map)
    assert n_after == 0
    # repair must not have dropped either gene
    assert {g['subject_code'] for g in child} == {'IT101', 'IT102'}


# ── D. Mutation-created conflict is repaired ────────────────────────────────

def test_d_mutation_created_conflict_is_repaired_across_many_seeds():
    """_mutate picks a new time/day/room/faculty for one gene WITHOUT
    checking the individual's other genes (only Published/Draft slots are
    checked, for rollback) -- an intra-individual collision is an expected,
    legitimate output of _mutate, always caught by the _repair_overlaps call
    generate_draft runs unconditionally right after every child. Try many
    seeds so at least some of them actually exercise a mutation that
    collides with the fixed sibling gene, and confirm the invariant holds
    in every case, not just the colliding ones."""
    sched = _sched()
    faculty_map = {'F1': _faculty('F1')}
    faculty_list = list(faculty_map.values())
    rooms = [_room(1)]
    # lec=1.5 matches the genes' actual 1.5h blocks below -- so a 'time'
    # mutation draws from the same block pool the anchor already uses,
    # and a 'day' mutation (both already share the SAME time block, just
    # different days) has a real chance of relanding on the sibling's day,
    # producing a genuine same-day/same-time/same-faculty/same-room clash.
    subjects_by_code = {'IT101': _subject('IT101', lec=1.5), 'IT102': _subject('IT102', lec=1.5)}

    conflicts_seen = 0
    for seed in range(300):
        random.seed(4000 + seed)
        anchor = _gene(subject_code='IT101', faculty_id='F1', room_id=1,
                        day='Monday', days_list=['Monday'],
                        start_time=time(7, 30), end_time=time(9, 0))
        target = _gene(subject_code='IT102', faculty_id='F1', room_id=1,
                        day='Tuesday', days_list=['Tuesday'],
                        start_time=time(7, 30), end_time=time(9, 0))
        child = sched._mutate([anchor, target], subjects_by_code, faculty_list,
                               faculty_map, rooms)

        _, n_before = sched._fitness(child, faculty_map)
        if n_before > 0:
            conflicts_seen += 1

        sched._repair_overlaps(child, faculty_map)
        _, n_after = sched._fitness(child, faculty_map)
        assert n_after == 0, f'seed {seed}: repair failed to resolve a mutation-created conflict'

    assert conflicts_seen > 0, (
        'no seed in this range exercised a mutation-created conflict -- '
        'widen the seed range so this test keeps proving something'
    )


# ── E. HC_SPEC/specialization mismatch does not hard-reject a candidate ────

def test_e_specialization_mismatch_is_selected_as_feasible_not_hard_rejected():
    sched = _sched()
    mismatched_fac = {'F1': _faculty('F1', specializationname='Accountancy and Finance')}
    matched_fac = {'F1': _faculty('F1', specializationname='Information Technology')}
    cls = _gene(subject_code='COMP101')

    score_bad, n_bad = sched._fitness([cls], mismatched_fac)
    score_good, n_good = sched._fitness([cls], matched_fac)
    assert n_bad == 0 and n_good == 0   # HC_SPEC never counts as a hard violation

    scored = [(score_bad, n_bad, 'mismatched'), (score_good, n_good, 'matched')]
    survivors = IntelligentScheduler._select_survivors(scored, elite_n=1)
    assert survivors == ['matched']   # both feasible -> the higher SC9 score wins


# ── F. SC9 still affects fitness ────────────────────────────────────────────

def test_f_sc9_weight_still_differentiates_fitness_after_ga_wiring_change():
    # Guards against a regression from this checkpoint's _fitness edit
    # (passing rooms_by_id through to the internal csp.validate() call).
    sched = _sched()
    matched_fac = {'F1': _faculty('F1', specializationname='Information Technology')}
    mismatched_fac = {'F1': _faculty('F1', specializationname='Accountancy and Finance')}
    cls = _gene(subject_code='COMP101')
    matched_score, _ = sched._fitness([cls], matched_fac)
    mismatched_score, _ = sched._fitness([cls], mismatched_fac)
    assert matched_score > mismatched_score


# ── G. Valid merged NSTP/OU assignment stays correctly deduplicated ────────

def test_g_locked_merge_gene_survives_mutation_and_repair_unchanged():
    """A CBR/manually-locked gene representing one side of a valid NSTP
    merge must come out of _mutate/_repair_overlaps/_reapply_locks
    byte-for-byte identical -- any drift here would desync it from the
    OTHER section's already-published NSTP session it's meant to stay
    aligned with, breaking the merge (and its load dedup) the moment the
    two are compared at final validation / Faculty Load time."""
    sched = _sched()
    faculty_map = {'F1': _faculty('F1')}
    faculty_list = list(faculty_map.values())
    rooms = [_room(1)]
    subjects_by_code = {'NSTP101': _subject('NSTP101', lec=1.5, lab=0)}

    lock_entry = {
        'subject_code': 'NSTP101', 'class_type': 'Lecture', 'course': 'BSIT',
        'lock': {'faculty': True, 'room': True, 'schedule': True},
        'faculty_id': 'F1', 'instructor': 'Faculty F1',
        'room_id': 1, 'room': 'Room 1', 'room_type': 'Lecture',
        'day': 'Sunday', 'days_list': ['Sunday'],
        'start_time': time(7, 30), 'end_time': time(9, 0),
        'source_case': 'historical-nstp-merge',
    }
    locked_parts = {('NSTP101', 'Lecture', 'BSIT'): lock_entry}
    locked_gene = _gene(
        subject_code='NSTP101', course='BSIT', section_name='A',
        faculty_id='F1', room_id=1,
        day='Sunday', days_list=['Sunday'],
        start_time=time(7, 30), end_time=time(9, 0),
        _lock=lock_entry,
    )

    # The other section's already-published half of the same merge -- fixed,
    # external to this individual, exactly like a real cross-section merge.
    other_section_gene = _gene(
        subject_code='NSTP101', course='BSEd', section_name='B',
        faculty_id='F1', room_id=1,
        day='Sunday', days_list=['Sunday'],
        start_time=time(7, 30), end_time=time(9, 0),
    )
    assert sched.csp.is_valid_merge(locked_gene, other_section_gene) is True

    child = [locked_gene]
    for seed in range(20):
        random.seed(2000 + seed)
        child = sched._mutate(child, subjects_by_code, faculty_list, faculty_map, rooms,
                               locked_parts=locked_parts)
        sched._repair_overlaps(child, faculty_map, locked_parts=locked_parts)
        sched._reapply_locks(child, locked_parts)

    g = child[0]
    assert g['faculty_id'] == 'F1' and g['room_id'] == 1
    assert g['day'] == 'Sunday' and g['start_time'] == time(7, 30) and g['end_time'] == time(9, 0)
    assert sched.csp.is_valid_merge(g, other_section_gene) is True


def test_h_mutation_never_synchronizes_unrelated_genes_into_a_false_merge():
    """Mutation touches exactly one gene per call and never reads or writes
    another gene's subject_code -- an invalid attempted merge (different
    subjects, non-NSTP) must never spontaneously become a valid one just
    because GA operations ran on the individual."""
    sched = _sched()
    faculty_map = {'F1': _faculty('F1'), 'F2': _faculty('F2')}
    faculty_list = list(faculty_map.values())
    rooms = [_room(1), _room(2)]
    subjects_by_code = {'IT101': _subject('IT101'), 'IT102': _subject('IT102')}

    gene_a = _gene(subject_code='IT101', course='BSIT', section_name='A',
                    faculty_id='F1', room_id=1,
                    day='Monday', days_list=['Monday'])
    gene_b = _gene(subject_code='IT102', course='BSIT', section_name='B',
                    faculty_id='F2', room_id=2,
                    day='Monday', days_list=['Monday'])
    child = [gene_a, gene_b]
    assert sched.csp.is_valid_merge(gene_a, gene_b) is False

    for seed in range(20):
        random.seed(3000 + seed)
        child = sched._mutate(child, subjects_by_code, faculty_list, faculty_map, rooms)
        sched._repair_overlaps(child, faculty_map)
        assert child[0]['subject_code'] == 'IT101' and child[1]['subject_code'] == 'IT102'
        assert sched.csp.is_valid_merge(child[0], child[1]) is False


# ── I. Zero-weight SC configuration remains respected through GA ───────────

def test_i_zero_weight_sc9_configuration_is_respected_through_fitness():
    sched = _sched({'sc9_specialization': 0})
    matched_fac = {'F1': _faculty('F1', specializationname='Information Technology')}
    mismatched_fac = {'F1': _faculty('F1', specializationname='Accountancy and Finance')}
    cls = _gene(subject_code='COMP101')
    matched_score, _ = sched._fitness([cls], matched_fac)
    mismatched_score, _ = sched._fitness([cls], mismatched_fac)
    assert matched_score == mismatched_score


# ── J & N. Seeded determinism + final complete schedule is hard-violation-free ──

@requires_db
def test_j_n_seeded_generation_is_deterministic_and_hard_violation_free():
    import app

    res1 = app.scheduler_engine.generate_draft(
        "BSIT", 4, "B", "2025-2026", acad_year_id="AY2526", seed=42,
    )
    res2 = app.scheduler_engine.generate_draft(
        "BSIT", 4, "B", "2025-2026", acad_year_id="AY2526", seed=42,
    )
    assert res1["result_status"] == res2["result_status"]
    assert res1["final_fitness"] == res2["final_fitness"]

    def _key(cls):
        return (cls.get("subject_code"), cls.get("class_type"), cls.get("faculty_id"),
                cls.get("room_id"), tuple(cls.get("days_list") or []),
                cls.get("start_time"), cls.get("end_time"))

    keys1 = sorted(_key(c) for c in res1["schedule_data"])
    keys2 = sorted(_key(c) for c in res2["schedule_data"])
    assert keys1 == keys2, "the same seed must produce the same schedule"

    if res1["result_status"] == "COMPLETE_VALID":
        _subjects, _faculty_list, faculty_map, rooms = app.scheduler_engine.fetch_data(
            "BSIT", 4, "B", "2025-2026"
        )
        rooms_by_id = {r["roomid"]: r for r in rooms}
        hard = [
            v for v in app.scheduler_engine.csp.validate(
                res1["schedule_data"], faculty_map, rooms_by_id=rooms_by_id
            )
            if v.get("severity") != "warning"
        ]
        assert hard == [], f"COMPLETE_VALID result must have zero hard violations, got: {hard}"


# ── K. GA terminates safely when no feasible placement exists ──────────────

def test_k_repair_overlaps_terminates_under_maximal_conflict():
    """_repair_overlaps is documented as bounded to 8 passes. Even when
    every gene starts out double-booked against every other gene (the
    worst case, and one an over-subscribed room/faculty pool can make
    genuinely unsolvable), it must return promptly instead of looping
    until a solution that may not exist is found."""
    sched = _sched()
    faculty_map = {'F1': _faculty('F1')}
    individual = [
        _gene(subject_code=f'IT10{i}', faculty_id='F1', room_id=1,
              day='Monday', days_list=['Monday'],
              start_time=time(7, 30), end_time=time(9, 0))
        for i in range(5)
    ]
    random.seed(1)
    result = sched._repair_overlaps(individual, faculty_map)
    assert result is individual   # returns the same list, promptly
    assert len(result) == 5


# ── L. Partial/incomplete generation representation ─────────────────────────
# Covered by the pre-existing
# test_generation_integration.test_generate_draft_partial_valid_preserves_every_valid_component
# regression test (BSARCH/1/B/2022-2023 seed=7) -- re-run as part of the full
# suite below and unaffected by this checkpoint's changes.


# ── M. CBR/historical metadata needed by SC8 survives GA operations ────────

def test_m_crossover_preserves_cbr_and_preference_metadata():
    """Crossover shallow-copies every gene wholesale -- subject, faculty,
    room, day, time, section, load classification, and SC8-relevant
    preference/_lock metadata must all ride along unchanged for whichever
    parent's gene ends up in the child, and days_list must be an
    independent copy (not aliased to the parent's own list)."""
    lock_p1 = {'source_case': 'case-77'}
    p1 = [
        _gene(subject_code='IT101', is_preferred_faculty=True, is_preferred_room=True,
              _lock=lock_p1, units=3, course='BSIT'),
        _gene(subject_code='IT102'),
    ]
    p2 = [
        _gene(subject_code='IT101'),
        _gene(subject_code='IT102', is_preferred_faculty=True, _lock=None),
    ]
    child = IntelligentScheduler._crossover(p1, p2)
    # len(p1) == 2 -> split is always 1 -> child = [p1[0], p2[1]]
    assert child[0]['subject_code'] == 'IT101'
    assert child[0]['is_preferred_faculty'] is True
    assert child[0]['is_preferred_room'] is True
    assert child[0]['_lock'] == lock_p1
    assert child[0]['units'] == 3
    assert child[1]['subject_code'] == 'IT102'
    assert child[1]['is_preferred_faculty'] is True

    child[0]['days_list'].append('Extra')
    assert 'Extra' not in p1[0]['days_list']
