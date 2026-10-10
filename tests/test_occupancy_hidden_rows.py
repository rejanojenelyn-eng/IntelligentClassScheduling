"""Manual Editor time/room lists vs. saved bookings the editor already replaced.

BSCE1's GEED 005 slice 2 (Saturday 12:30-2:00, LQ212) is loaded into the editor: its
saved row is hidden (s:<sessionid>) and the editor copy stands in for it. Clearing the
slice's room to search used to drop LQ212 from the list (its own saved booking "occupied"
it), and a class moved away but not yet saved kept blocking its old time.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / 'js' / 'occupancy_hidden_rows_harness.js'


@pytest.fixture(scope='module')
def data():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    out = subprocess.run([node, str(HARNESS)], capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_replaced_saved_rows_do_not_block_times(data):
    assert data['ranges'] == [[15, 17]]          # only the untouched ENSC 011 class


def test_own_saved_booking_does_not_hide_its_room(data):
    assert data['unavailable'] == ['40']         # LQ212 (39) stays searchable


def test_saved_rows_still_block_when_not_replaced(data):
    assert data['ranges_unhidden'] == [[10, 13], [11, 13], [15, 17]]
    assert data['unavailable_unhidden'] == ['39', '40']
