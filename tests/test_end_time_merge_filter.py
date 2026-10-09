"""Manual Editor End-time list for a P7 merge slot.

Moving BSIT 1's COMP 002 onto DIT1's Monday 2:00-4:00 slot (same subject + faculty) may
merge, so after a 2:00 PM start the End list must offer exactly 4:00 PM. When the slot was
listed twice (the other section's DB row + its editor mirror) the End list came out EMPTY.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / 'js' / 'end_time_merge_harness.js'


def test_merge_slot_end_time_is_offered_even_when_listed_twice():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    out = subprocess.run([node, str(HARNESS)], capture_output=True, text=True, encoding='utf-8', timeout=60)
    data = json.loads(out.stdout)
    assert data['db_only'] == ['04:00 PM']
    assert data['db_and_mirror'] == ['04:00 PM']
