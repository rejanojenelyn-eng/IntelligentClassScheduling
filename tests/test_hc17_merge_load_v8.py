import faculty_load as fl


def _s(section, hrs=3.0):
    return {
        'subjectcode': 'NSTP 001', 'year_section': section,
        'sectionid': section, 'days': 'Saturday', 'time_code': '0811',
        'time_range': '08:00 AM - 11:00 AM', 'hrs': hrs,
    }


def _cfg(pairs):
    import json
    return {
        'hc_merge_enabled': 1, 'hc_merge_scope': 'nstp_only',
        'hc_merge_scope_subjects': None,
        'hc_merge_section_pairs': json.dumps(pairs),
    }


def test_hc17_same_faculty_three_section_valid_clique_counts_once():
    cfg = _cfg([['BSIT1A','BSCS1A'], ['BSIT1A','BSBA1A'], ['BSCS1A','BSBA1A']])
    grouped = fl.group_assignments([_s('BSIT1A'), _s('BSCS1A'), _s('BSBA1A')], config=cfg)
    assert sum(x['hrs'] for x in grouped) == 3.0


def test_hc17_incomplete_three_section_pairing_does_not_deduplicate():
    cfg = _cfg([['BSIT1A','BSCS1A'], ['BSIT1A','BSBA1A']])
    grouped = fl.group_assignments([_s('BSIT1A'), _s('BSCS1A'), _s('BSBA1A')], config=cfg)
    assert sum(x['hrs'] for x in grouped) == 9.0


def test_hc17_two_section_merge_counts_once():
    cfg = _cfg([['BSIT1A','BSCS1A']])
    grouped = fl.group_assignments([_s('BSIT1A'), _s('BSCS1A')], config=cfg)
    assert sum(x['hrs'] for x in grouped) == 3.0
