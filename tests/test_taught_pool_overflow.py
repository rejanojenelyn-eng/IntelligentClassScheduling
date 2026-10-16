"""Teaching-history pool overflow: when everyone who has taught a subject is already at
their load limit, the builder gives the class to a faculty member with the SAME
specialization who still has room — instead of overloading the same person on every
run (a guaranteed HC9 that Re-generate could never fix, e.g. DCPET-3's five subjects
all going to the one instructor who had taught them)."""
import random
from datetime import time

from scheduler import CSPValidator, IntelligentScheduler

NO_GRID = {'hc_time_blocks_enabled': 0, 'hc_day_pairing_enabled': 0}
ROOMS = [{'roomid': i, 'roomname': f'R{i}', 'roomtype': 'Lecture'} for i in range(1, 4)]


def _fac(emp, spec, load=40):
    return {'employeenumber': emp, 'fullname': emp, 'specializationname': spec,
            'employeestatus': 'Permanent', 'designationid': None, 'nightteachingservice': None,
            'employeetype': {'regular_start': time(7, 30), 'regular_end': time(16, 30),
                             'regularload': load, 'parttimeload': 0, 'teachingsubstitution': 0}}


def _subject(code):
    return {'subjectcode': code, 'subjectname': code, 'lecturehours': 3,
            'laboratoryhours': 0, 'creditunits': 3, 'offeringcode': 'DCPET'}


def _sched():
    s = IntelligentScheduler()
    s.csp = CSPValidator(config=dict(NO_GRID))
    s._hc_cfg = dict(NO_GRID)
    return s


def _build(fmap, existing_load, seed):
    random.seed(seed)
    subjects = [_subject(c) for c in ('CMPE 305', 'CMPE 308', 'CMPE 311')]
    pool = {s['subjectcode']: {'HIST'} for s in subjects}
    ind = _sched()._build_individual(subjects, list(fmap.values()), fmap, ROOMS,
                                     existing_load=existing_load, subject_taught_pool=pool)
    return {g['subject_code']: g['faculty_id'] for g in ind}


def test_full_history_instructor_overflows_to_same_specialization_only():
    fmap = {'HIST': _fac('HIST', 'Computer Engineering', load=6),
            'CPE2': _fac('CPE2', 'Computer Engineering'),
            'PSYC': _fac('PSYC', 'Psychology')}
    for seed in range(20):
        who = _build(fmap, {}, seed)
        assert list(who.values()).count('HIST') <= 2, (seed, who)   # 6 h cap = two 3 h classes
        assert 'PSYC' not in who.values(), (seed, who)
        assert 'CPE2' in who.values(), (seed, who)


def test_history_instructor_kept_when_no_same_specialization_faculty_has_room():
    fmap = {'HIST': _fac('HIST', 'Computer Engineering', load=6),
            'PSYC': _fac('PSYC', 'Psychology')}
    for seed in range(10):
        assert set(_build(fmap, {}, seed).values()) == {'HIST'}     # unchanged strict boundary
