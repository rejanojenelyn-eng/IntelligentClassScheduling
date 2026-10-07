"""Generated sessions must total exactly each subject part's required hours.

Bugs fixed:
  * a 3-hour lecture kept TWO days on a 3-hour block (6h) when Day Pairing is off
    (the time mutation re-synced days only when pairing was on; a historical/CBR
    re-apply could also restore the extra day);
  * one historical time slot was reused for EVERY part of a subject, so a 1-2 hour
    lecture got a 3-hour block (e.g. STAT 203's 2h lecture on 10:30-1:30);
  * lectures longer than one block (4/5/6/12h) got a single meeting (under-scheduled).
"""
import inspect
import io
import contextlib
from collections import defaultdict
from datetime import datetime, date

import pytest

import scheduler
from scheduler import get_blocks_for_hours, duration_hours


def _gen():
    import app
    return app.scheduler_engine


def test_meetings_needed():
    m = scheduler.ScheduleGenerator._meetings_needed if hasattr(scheduler, 'ScheduleGenerator') \
        else type(_gen())._meetings_needed
    assert m(3, 3) == 1 and m(3, 1.5) == 2 and m(6, 3) == 2 and m(12, 3) == 4 and m(4, 2) == 2
    assert m(4, 3) is None and m(0, 3) is None and m(3, 0) is None


@pytest.mark.parametrize('hours,block', [(6, 3.0), (12, 3.0), (4, 2.0), (5, 1.0), (3, 3.0), (2, 2.0), (1, 1.0)])
def test_long_lectures_use_a_block_that_divides_their_hours(hours, block):
    blks = get_blocks_for_hours(hours, is_lab=False)
    assert blks and {round(duration_hours(s, e), 2) for s, e in blks} == {block}


def test_cbr_time_is_checked_per_part():
    src = inspect.getsource(scheduler)
    assert 'part_time_ok' in src and "'schedule': part_time_ok and" in src


def test_hours_guard_runs_after_the_last_reapply():
    src = inspect.getsource(scheduler)
    gen = src[src.index('def generate_draft'):]
    assert gen.index('self._enforce_user_locks(best_schedule_global, locked_parts)') \
        < gen.index('Hours guard (last word on days)')


def _hrs(st, et):
    return (datetime.combine(date.today(), et) - datetime.combine(date.today(), st)).seconds / 3600


def test_generated_hours_equal_required_hours_dcvet_y1():
    from app import query_db
    req = {r['code']: float(r['h']) for r in (query_db("""
        SELECT UPPER(cs.subjectcode) code, COALESCE(cs.lecturehours,0)+COALESCE(cs.laboratoryhours,0) h
        FROM curriculumsubject cs JOIN curriculum c ON c.curriculumid=cs.curriculumid
        WHERE c.programcode='DCVET' AND c.curriculumyear='2022-2023' AND c.curriculumtype='REGULAR'
          AND cs.yearlevel=1 AND cs.semester='A'""") or [])}
    if not req:
        pytest.skip('DCVET 2022-2023 Year 1 curriculum not present')
    for seed in (1, 2, 3):
        with contextlib.redirect_stdout(io.StringIO()):
            res = _gen().generate_draft('DCVET', 1, 'A', '2022-2023', False, acad_year_id='AY2627', seed=seed)
        got = defaultdict(float)
        for g in res.get('schedule_data') or []:
            if g.get('start_time') and g.get('end_time') and not g.get('incomplete'):
                got[(g['subject_code'] or '').upper()] += _hrs(g['start_time'], g['end_time']) * len(g.get('days_list') or [])
        over = {k: (v, req.get(k)) for k, v in got.items() if req.get(k) is not None and v > req[k] + 0.01}
        assert not over, (seed, over)
