"""
Two Academic Heads working at the same time.

* Official writers (Save Draft, Approve/Publish, Restore) take one per-semester advisory
  lock BEFORE their cross-section checks and version numbering, so two simultaneous
  Publishes can never both pass the room/faculty check (double booking) and two saves of
  one section can never draw the same version_number.
* scheduler.refresh_time_blocks never empties the shared HC6 sets another generation
  thread is reading.
"""
import inspect
import threading
import time

import pytest

import app as app_module
import scheduler
from conftest import requires_db


def _src(fn):
    return inspect.getsource(fn)


@pytest.mark.parametrize('route, checks', [
    (app_module.api_save_draft, ['_next_version_number(']),
    (app_module.api_approve_schedule, ['_check_cross_schedule_conflicts(',
                                       '_next_version_number(',
                                       '_publish_carried_forward_conflicts(']),
    (app_module.api_restore_version, ['_next_version_number(']),
])
def test_official_writers_lock_before_checks_and_numbering(route, checks):
    src = _src(route)
    lock_at = src.find('_lock_official_semester(cur, sem_id)')
    assert lock_at != -1, f'{route.__name__} does not take the per-semester lock'
    for needle in checks:
        at = src.find(needle)
        assert at != -1, needle
        assert lock_at < at, f'{route.__name__}: {needle} runs before the lock'


def test_approve_rechecks_faculty_load_after_lock():
    src = _src(app_module.api_approve_schedule)
    assert src.find('_lock_official_semester(cur, sem_id)') < src.find('_existing_load_now')


@requires_db
def test_second_writer_waits_for_first_to_commit():
    from database import get_db_connection
    a, b = get_db_connection(), get_db_connection()
    sem = 987654321          # any key: nothing else locks this semester id
    acquired = threading.Event()
    try:
        app_module._lock_official_semester(a.cursor(), sem)

        def other_head():
            app_module._lock_official_semester(b.cursor(), sem)
            acquired.set()
            b.rollback()

        t = threading.Thread(target=other_head, daemon=True)
        t.start()
        assert not acquired.wait(0.5), 'second writer was not blocked by the first'
        a.rollback()         # first Academic Head's transaction ends
        assert acquired.wait(5), 'second writer never got the lock after the first finished'
        t.join(5)
    finally:
        a.close(); b.close()


@requires_db
def test_different_semesters_do_not_block_each_other():
    from database import get_db_connection
    a, b = get_db_connection(), get_db_connection()
    try:
        app_module._lock_official_semester(a.cursor(), 987654321)
        cur = b.cursor()
        cur.execute('SELECT pg_try_advisory_xact_lock(%s, %s)',
                    (app_module._OFFICIAL_WRITE_LOCK_NS, 987654322))
        assert cur.fetchone()[0] is True
    finally:
        a.rollback(); b.rollback(); a.close(); b.close()


def _restore(blocks):
    # Put back whatever an earlier test module had loaded.
    scheduler.STANDARD_BLOCKS[:] = blocks
    scheduler.VALID_START_TIMES.clear(); scheduler.VALID_START_TIMES.update(s for s, _ in blocks)
    scheduler.VALID_END_TIMES.clear(); scheduler.VALID_END_TIMES.update(e for _, e in blocks)
    scheduler._ALL_STANDARD_SLOTS[:] = [(d, s, e) for d in scheduler.ALL_DAYS for s, e in blocks]
    scheduler._TOTAL_STANDARD_CAPACITY = len(scheduler._ALL_STANDARD_SLOTS)
    scheduler._VALID_BLOCK_SET.clear(); scheduler._VALID_BLOCK_SET.update(blocks)


def test_refresh_time_blocks_is_safe_with_concurrent_readers():
    original = list(scheduler.STANDARD_BLOCKS)
    cfg = {}
    scheduler.refresh_time_blocks(cfg)
    errors, stop = [], threading.Event()

    def reader():
        try:
            while not stop.is_set():
                assert scheduler.VALID_START_TIMES and scheduler._VALID_BLOCK_SET
                for _ in scheduler.VALID_START_TIMES:
                    pass
                for _ in scheduler._VALID_BLOCK_SET:
                    pass
        except Exception as e:      # RuntimeError: Set changed size / emptied set
            errors.append(e)

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
    for r in readers:
        r.start()
    end = time.time() + 1.0
    while time.time() < end:
        scheduler.refresh_time_blocks(cfg)   # what each concurrent generation does
    stop.set()
    for r in readers:
        r.join(5)
    assert not errors, errors
    _restore(original)


def test_refresh_time_blocks_still_applies_a_real_change():
    before = list(scheduler.STANDARD_BLOCKS)
    try:
        scheduler.STANDARD_BLOCKS[:] = before[:1]
        scheduler._VALID_BLOCK_SET.clear(); scheduler._VALID_BLOCK_SET.update(before[:1])
        out = scheduler.refresh_time_blocks({})
        assert set(out) == set(scheduler._VALID_BLOCK_SET)
        assert scheduler.VALID_START_TIMES == {s for s, _ in out}
        assert scheduler.VALID_END_TIMES == {e for _, e in out}
        assert len(out) > 1
    finally:
        _restore(before)
