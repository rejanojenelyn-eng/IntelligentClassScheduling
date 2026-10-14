"""
The Local Scheduler has no Version History: no tab and no panel. The Official
Scheduler keeps both. Local drafts stay publishable from the editor button
(SAVE AS LOCAL ARRANGEMENT publishes the latest saved Draft when nothing changed).
"""
import pytest

import app as app_module
from conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def head():
    from database import query_db
    row = query_db("SELECT username FROM accounts WHERE role = 'Academic Head' AND isactive LIMIT 1", one=True)
    if not row:
        pytest.skip('no active Academic Head account')
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, username=row['username'], role='Academic Head',
                 must_change_password=False, account_setup_complete=True)
    return client


def _page(client, mode):
    html = client.get(f'/schedule/manual-editor?scheduler={mode}').get_data(as_text=True)
    if 'manual-nav-tabs' not in html:
        pytest.skip('editor route not reachable for this account')
    return html


def test_local_scheduler_has_no_version_history(head):
    html = _page(head, 'local')
    assert 'id="tab-history"' not in html
    assert 'id="versionHistoryPanel"' not in html
    assert '>VERSION HISTORY<' not in html
    # Everything else stays.
    for tab in ('tab-room', 'tab-main', 'tab-faculty'):
        assert f'id="{tab}"' in html
    assert 'id="btnPublishLocalArr"' in html


def test_official_scheduler_keeps_version_history(head):
    html = _page(head, 'official')
    assert 'id="tab-history"' in html and 'id="versionHistoryPanel"' in html
    assert 'id="tab-drafts"' in html


def test_history_loader_is_a_no_op_without_the_panel():
    src = (app_module.app.root_path + '/templates/academic/manualScheduleEditor.html')
    text = open(src, encoding='utf-8').read()
    body = text.split('async function _loadVersionHistoryPanel() {', 1)[1]
    assert body.lstrip().startswith('// The Local Scheduler has no Version History panel')
    assert "if (!document.getElementById('versionHistoryPanel')) return;" in body.split('\n', 3)[2]
