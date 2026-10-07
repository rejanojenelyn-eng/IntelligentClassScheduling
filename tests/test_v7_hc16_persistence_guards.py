from pathlib import Path


def test_sync_mergedclass_uses_authoritative_hc16():
    src = Path('app.py').read_text(encoding='utf-8')
    start = src.index('def _sync_mergedclass_for_semester')
    end = src.index('\ndef _not_approvable', start)
    body = src[start:end]
    assert 'faculty_load.is_valid_merge' in body
    assert 'merge_pairs' in body
    assert "len(rows) < 2" in body


def test_sync_does_not_group_persistence_without_faculty_identity():
    src = Path('app.py').read_text(encoding='utf-8')
    start = src.index('def _sync_mergedclass_for_semester')
    end = src.index('\ndef _not_approvable', start)
    body = src[start:end]
    assert "r['employeenumber']" in body
    assert 'Multi-faculty NSTP/OU' in body
