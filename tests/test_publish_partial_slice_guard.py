"""Approve must not silently drop a half-filled slice.

EETE 101 (DEET-1): slice 1 was a Draft, slice 2 had Time + Room but no Day. Approve only
collects fully filled slices, so it published slice 1 alone and slice 2 vanished when the
panel reloaded. _partlyFilledSlices (used to stop Approve with a clear message) must list
exactly the partly filled slices — never complete or completely blank ones."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'static' / 'js' / 'ACAD HEAD' / 'manualEditor.acad2.js'

HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const s = src.indexOf('function _partlyFilledSlices(');
let i = src.indexOf(') {', s) + 2, d = 0;
for (; i < src.length; i++) { if (src[i] === '{') d++; else if (src[i] === '}' && --d === 0) break; }
const mkRow = (num, day, st, en, room) => ({
    querySelector: q => ({ '.ts-day-sel': { value: day }, '.ts-start-hidden': { value: st },
                           '.ts-end-hidden': { value: en }, '.ts-room-hidden': { value: room },
                           '.ts-row-num span': { textContent: 'SLICE ' + num } })[q] || null });
const rows = [
    mkRow(1, 'Thursday', '01:30 PM', '04:30 PM', '39'),   // complete
    mkRow(2, '', '01:30 PM', '04:30 PM', '10'),           // no day  -> listed
    mkRow(3, '', '', '', ''),                             // blank   -> ignored
    mkRow(4, 'Monday', '', '', 'TBA'),                    // no time -> listed
];
global.document = { querySelectorAll: () => rows };
global.window = global;
eval(src.slice(s, i + 1));
console.log(JSON.stringify(_partlyFilledSlices()));
"""


def test_only_partly_filled_slices_are_reported():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    out = subprocess.run([node, '-e', HARNESS, str(SRC)], capture_output=True, text=True,
                         encoding='utf-8', timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [
        {'label': 'Slice 2', 'missing': ['Day']},
        {'label': 'Slice 4', 'missing': ['Start time', 'End time']},
    ]
