"""Run real endpoint code against the real database inside ONE transaction that is
always rolled back.

Every get_db_connection() call (app, database.query_db, scheduler, rf_dss) returns a
handle on the same psycopg2 connection:
  - commit()   -> RELEASE + new SAVEPOINT (a new "committed" baseline inside the test)
  - rollback() -> ROLLBACK TO SAVEPOINT  (undo back to the last commit())
  - close()    -> no-op (recovers an aborted statement so the next handle works)
The outer transaction is rolled back when the test ends, so nothing persists.

All handles share one backend, so a code path that would block on its own lock from a
second connection cannot be observed through this harness; use a separate real
connection for that (see the Approve lock-probe test).
"""
import psycopg2
import psycopg2.extensions as _ext

from config import Config

_SP = 'shared_tx_baseline'


def _connect():
    return psycopg2.connect(
        dbname=Config.DB_NAME, user=Config.DB_USER, password=Config.DB_PASS,
        host=Config.DB_HOST, port=Config.DB_PORT, connect_timeout=5,
    )


class SharedTx:
    def __init__(self):
        self.raw = _connect()
        cur = self.raw.cursor()
        cur.execute("SET lock_timeout = '5s'")
        cur.execute("SET statement_timeout = '60s'")
        cur.execute("SAVEPOINT " + _SP)
        cur.close()

    def handle(self):
        return _TxHandle(self)

    def close(self):
        try:
            self.raw.rollback()
        finally:
            self.raw.close()


class _TxHandle:
    __slots__ = ('_tx',)

    def __init__(self, tx):
        object.__setattr__(self, '_tx', tx)

    def cursor(self, *args, **kwargs):
        return self._tx.raw.cursor(*args, **kwargs)

    def commit(self):
        cur = self._tx.raw.cursor()
        cur.execute("RELEASE SAVEPOINT " + _SP)
        cur.execute("SAVEPOINT " + _SP)
        cur.close()

    def rollback(self):
        cur = self._tx.raw.cursor()
        cur.execute("ROLLBACK TO SAVEPOINT " + _SP)
        cur.close()

    def close(self):
        if self._tx.raw.info.transaction_status == _ext.TRANSACTION_STATUS_INERROR:
            self.rollback()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __getattr__(self, name):
        return getattr(self._tx.raw, name)

    def __setattr__(self, name, value):
        # Never let endpoint code switch the shared connection to autocommit.
        if name == 'autocommit':
            return
        setattr(self._tx.raw, name, value)


def install(monkeypatch, tx):
    """Route every module's get_db_connection to the shared transaction."""
    import sys
    factory = tx.handle
    for mod_name in ('app', 'database', 'scheduler', 'rf_dss'):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, 'get_db_connection'):
            monkeypatch.setattr(mod, 'get_db_connection', factory)
    return factory
