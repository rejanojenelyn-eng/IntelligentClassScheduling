"""static/js/csrf.js adds the CSRF token to same-site state-changing requests only
(fetch, XMLHttpRequest, form posts, csrfPost). Runs tests/js/csrf_harness.js in Node."""
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / 'js' / 'csrf_harness.js'


def test_csrf_js_adds_the_token_where_it_belongs():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    out = subprocess.run([node, str(HARNESS)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr or out.stdout
    assert 'all checks passed' in out.stdout
