"""static/js/safe_html.js (C4): escapeHtml / jsArg / safeUrl behave correctly for hostile
values. Runs tests/js/safe_html_harness.js in Node."""
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / 'js' / 'safe_html_harness.js'


def test_safe_html_helpers():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    out = subprocess.run([node, str(HARNESS)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr or out.stdout
    assert 'all checks passed' in out.stdout
