"""
Regression: Official Publish failed with
    psycopg2.errors.UndefinedColumn: column "updated_at" of relation "local_arrangement" does not exist
raised from _archive_local_for_official_republish (called by api_approve_schedule).

local_arrangement tables created from the 2026-09-16 catch-up migration / ASDBv10
dump have neither updated_at nor reason, and _ensure_local_tables never added
them — the same compatibility class as the earlier sectionid migration-order bug.

These tests run the REAL _ensure_local_tables against a fake connection that models
table columns the way PostgreSQL resolves them (CREATE TABLE IF NOT EXISTS is a
no-op on an existing table; an index or UPDATE naming a missing column raises),
starting from the legacy schema, then run the REAL archive helper against the result.
"""
import re

import pytest


class UndefinedColumn(Exception):
    pass


# Columns as defined by migrations/2026-09-16_schema_catchup.sql (pre section-scope).
LEGACY_SCHEMA = {
    'local_arrangement': {
        'arrangementid', 'description', 'programcode', 'yearlevel', 'semesterid',
        'ref_versionid', 'has_hc_violation', 'violated_rules', 'override_reason',
        'is_active', 'created_by', 'created_at', 'status',
    },
    'local_arrangement_sessions': {
        'sessionid', 'arrangementid', 'subjectcode', 'daydesc', 'starttimeid',
        'endtimeid', 'roomid', 'faculty_employeenumber',
    },
    'local_displaced_subjects': {
        'id', 'programcode', 'yearlevel', 'semesterid', 'subjectcode',
        'displaced_by', 'is_active', 'displaced_at',
    },
}

_TABLE_KEYWORDS = {'UNIQUE', 'PRIMARY', 'CONSTRAINT', 'FOREIGN', 'CHECK'}
_NON_COLUMNS = {'upper', 'lower', 'and', 'or', 'not', 'is', 'null', 'true', 'false', 'in'}


def _strip_literals(sql):
    return re.sub(r"'[^']*'", "''", sql)


def _split_top_level(body):
    parts, depth, cur = [], 0, ''
    for ch in body:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(cur); cur = ''
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


class _SchemaCursor:
    """Tracks columns per table; raises UndefinedColumn like PostgreSQL would."""

    def __init__(self, schema):
        self.schema = schema
        self.rowcount = 0

    def _require(self, table, cols, sql):
        known = self.schema.get(table)
        if known is None:
            raise AssertionError(f'relation "{table}" does not exist: {sql[:80]}')
        for c in cols:
            if c not in known:
                raise UndefinedColumn(f'column "{c}" of relation "{table}" does not exist')

    def execute(self, sql, params=None):
        s = ' '.join(_strip_literals(sql).split())

        m = re.match(r'CREATE TABLE IF NOT EXISTS public\.(\w+) \((.*)\)$', s)
        if m:
            table, body = m.groups()
            if table not in self.schema:
                self.schema[table] = {
                    item.split()[0] for item in _split_top_level(body)
                    if item.split()[0].upper() not in _TABLE_KEYWORDS
                }
            return

        m = re.match(r'ALTER TABLE public\.(\w+) ADD COLUMN IF NOT EXISTS (\w+)', s)
        if m:
            self.schema[m.group(1)].add(m.group(2))
            return

        m = re.match(r'CREATE (?:UNIQUE )?INDEX IF NOT EXISTS \w+ ON public\.(\w+) \((.*?)\)(?: WHERE (.*))?$', s)
        if m:
            table, cols, where = m.groups()
            words = re.findall(r'[a-z_]+', cols + ' ' + (where or ''))
            self._require(table, [w for w in words if w not in _NON_COLUMNS], s)
            return

        m = re.match(r'UPDATE public\.(\w+)(?: (\w+))? SET (.*?) WHERE (.*)$', s)
        if m:
            table, _alias, set_part, where = m.groups()
            targets = re.findall(r'(\w+) =', set_part)
            filters = re.findall(r'\b([a-z_]+)\)? (?:=|<>|IN\b|IS\b)', where)
            self._require(table, targets + [f for f in filters if f not in _NON_COLUMNS], s)
            return
        # Functions, triggers, other statements: irrelevant to column resolution.

    def fetchone(self):
        return None

    def close(self):
        pass


class _SchemaConn:
    def __init__(self, schema):
        self.schema = schema

    def cursor(self, cursor_factory=None):
        return _SchemaCursor(self.schema)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _run_ensure(monkeypatch, schema):
    import app
    monkeypatch.setattr(app, '_local_tables_ensured', False)
    monkeypatch.setattr(app, 'get_db_connection', lambda: _SchemaConn(schema))
    app._ensure_local_tables()
    assert app._local_tables_ensured is True
    return schema


def _legacy_schema():
    return {t: set(cols) for t, cols in LEGACY_SCHEMA.items()}


def test_simulator_reproduces_the_publish_failure_on_a_legacy_table():
    """Control: a table upgraded only by the pre-fix _ensure_local_tables (every
    column except reason/updated_at) fails the archive step exactly as in production."""
    import app
    schema = _legacy_schema()
    schema['local_arrangement'] |= {'sectionid', 'archive_reason', 'archived_at',
                                    'draft_fingerprint', 'restored_from_arrangementid'}
    schema['local_displaced_subjects'].add('sectionid')
    cur = _SchemaCursor(schema)
    with pytest.raises(UndefinedColumn, match='updated_at'):
        app._archive_local_for_official_republish(cur, 'DIT', 1, 5, 12)


def test_ensure_local_tables_upgrades_legacy_table_before_archive_uses_it(monkeypatch):
    import app
    schema = _run_ensure(monkeypatch, _legacy_schema())

    la = schema['local_arrangement']
    for col in ('sectionid', 'status', 'archive_reason', 'archived_at',
                'draft_fingerprint', 'reason', 'updated_at', 'restored_from_arrangementid'):
        assert col in la, col

    # The exact step api_approve_schedule runs after _ensure_local_tables.
    app._archive_local_for_official_republish(_SchemaCursor(schema), 'DIT', 1, 5, 12)


def test_ensure_local_tables_on_fresh_database_supports_archive(monkeypatch):
    import app
    schema = _run_ensure(monkeypatch, {})
    app._archive_local_for_official_republish(_SchemaCursor(schema), 'DIT', 1, 5, 12)


def test_ensure_local_tables_is_idempotent_on_upgraded_table(monkeypatch):
    schema = _run_ensure(monkeypatch, _legacy_schema())
    before = {t: set(c) for t, c in schema.items()}
    _run_ensure(monkeypatch, schema)
    assert schema == before


def test_every_local_arrangement_write_column_is_guaranteed(monkeypatch):
    """Guards against the next UndefinedColumn: every column that app.py INSERTs or
    SETs on local_arrangement must exist after upgrading a legacy table."""
    import app
    from pathlib import Path
    schema = _run_ensure(monkeypatch, _legacy_schema())
    src = Path(app.__file__).read_text(encoding='utf-8')

    used = set()
    for m in re.finditer(r'"""(.*?)"""', src, re.S):
        sql = _strip_literals(m.group(1))
        for im in re.finditer(r'INSERT INTO public\.local_arrangement\s*\((.*?)\)', sql, re.S):
            used |= {c.strip() for c in im.group(1).split(',')}
        for um in re.finditer(r'UPDATE public\.local_arrangement\b(?:\s+la)?\s+SET(.*?)WHERE', sql, re.S):
            used |= set(re.findall(r'(\w+)\s*=', um.group(1)))
    assert {'updated_at', 'reason'} <= used  # the scan actually sees the new columns
    assert used - schema['local_arrangement'] == set()
