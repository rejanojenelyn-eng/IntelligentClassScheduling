"""The read-only occupancy endpoints (room schedule, rooms-by-day, faculty schedule)
reuse the term's HC16 group policy while its source data is unchanged, instead of
rebuilding it (every group + the whole offered curriculum) on every request — the
Manual Editor fires these many times per action. Any change to the source tables,
the scheduler config, or the date rebuilds it."""
import pytest

import app as app_module
import merge_groups as mg
from conftest import requires_db

GROUPS_CFG = {mg.MODEL_KEY: mg.MODEL_GROUPS}


@pytest.fixture
def loads(monkeypatch):
    state = {'cfg': dict(GROUPS_CFG), 'sig': ('a',), 'calls': 0}

    def fake_policy_for(cfg, **kw):
        state['calls'] += 1
        return ('policy', state['calls'])

    monkeypatch.setattr(app_module, '_merge_occ_policy_cache', {})
    monkeypatch.setattr(app_module, 'load_scheduler_config', lambda: dict(state['cfg']))
    monkeypatch.setattr(app_module, '_merge_policy_for', fake_policy_for)
    monkeypatch.setattr(app_module, '_merge_policy_data_signature', lambda: state['sig'])
    return state


def test_unchanged_data_reuses_the_policy(loads):
    first = app_module._merge_occupancy_policy('AY2627', 'A')
    assert app_module._merge_occupancy_policy('AY2627', 'A') is first
    assert loads['calls'] == 1


def test_changed_data_rebuilds_the_policy(loads):
    first = app_module._merge_occupancy_policy('AY2627', 'A')
    loads['sig'] = ('b',)
    second = app_module._merge_occupancy_policy('AY2627', 'A')
    assert second is not first and loads['calls'] == 2


def test_changed_config_rebuilds_the_policy(loads):
    app_module._merge_occupancy_policy('AY2627', 'A')
    loads['cfg']['hc_merge_enabled'] = 0
    app_module._merge_occupancy_policy('AY2627', 'A')
    assert loads['calls'] == 2


def test_each_term_has_its_own_policy(loads):
    a = app_module._merge_occupancy_policy('AY2627', 'A')
    b = app_module._merge_occupancy_policy('AY2627', 'B')
    assert a is not b and loads['calls'] == 2
    assert app_module._merge_occupancy_policy('AY2627', 'A') is a


def test_signature_failure_falls_back_to_a_fresh_load(loads, monkeypatch):
    def boom():
        raise RuntimeError('db hiccup')
    monkeypatch.setattr(app_module, '_merge_policy_data_signature', boom)
    app_module._merge_occupancy_policy('AY2627', 'A')
    app_module._merge_occupancy_policy('AY2627', 'A')
    assert loads['calls'] == 2


def test_legacy_model_never_loads(loads):
    loads['cfg'] = {mg.MODEL_KEY: mg.MODEL_LEGACY}
    assert app_module._merge_occupancy_policy('AY2627', 'A') is None
    assert loads['calls'] == 0


@pytest.fixture
def tx(monkeypatch):
    from _shared_tx import SharedTx, install
    t = SharedTx()
    install(monkeypatch, t)
    try:
        yield t
    finally:
        t.close()


@requires_db
def test_signature_sees_uncommitted_and_repeated_updates(tx):
    """Content-hashed: even a second UPDATE of the same row inside one transaction
    (same row count, same xmin) changes the signature."""
    cur = tx.raw.cursor()
    cur.execute("SELECT MIN(roomid) FROM public.room")
    room_id = cur.fetchone()[0]
    if room_id is None:
        pytest.skip('no rooms')
    s0 = app_module._merge_policy_data_signature()
    cur.execute("UPDATE public.room SET roomname = roomname || 'x' WHERE roomid = %s", (room_id,))
    s1 = app_module._merge_policy_data_signature()
    cur.execute("UPDATE public.room SET roomname = roomname || 'y' WHERE roomid = %s", (room_id,))
    s2 = app_module._merge_policy_data_signature()
    assert len({s0, s1, s2}) == 3
    assert app_module._merge_policy_data_signature() == s2
