"""
Regression: Generation page → Approve showed "Published!", yet Manual Editor and
Local Scheduler found no Published schedule; Draft List → Approve worked.

Both buttons call the SAME endpoint (/api/schedule/approve → api_approve_schedule)
with the same section scope, so they write the same Published snapshot. The
difference was what happened AFTER success (confirmed in the dev DB, BSA Y3
section 478: Published R1 03:37:15 → Draft R2 03:37:23 with R1 now 'Archive'):

  - Draft List redirects straight to the Manual Editor program view.
  - Generation page stayed on the generated result; its "Go to Manual Editor"
    handoff called check-existing (which now found the just-Published version),
    and the Override step (archive-draft-for-editor) archived Draft AND Published
    before re-saving the same rows as a Draft — retiring the Official schedule.

Fix: after a successful publish the Generation page does what Draft List does.
"""
import re
from pathlib import Path

import app

ROOT = Path(__file__).resolve().parents[1]
GEN_JS = (ROOT / 'static' / 'js' / 'ACAD HEAD' / 'scheduleGeneration.acad.js').read_text(encoding='utf-8')
DRAFT_JS = (ROOT / 'static' / 'js' / 'ACAD HEAD' / 'draftView.acad.js').read_text(encoding='utf-8')
APP_SRC = (ROOT / 'app.py').read_text(encoding='utf-8')


def _gen_publish_success_branch():
    fn = GEN_JS[GEN_JS.index('async function _submitApproval'):]
    start = fn.index('if (data.success)')
    return fn[start:fn.index('} else if (data.needs_confirmation)', start)]


# ── One canonical publishing operation ─────────────────────────────────────

def test_both_paths_publish_through_the_same_endpoint():
    assert "fetch('/api/schedule/approve'" in GEN_JS
    assert "fetch('/api/schedule/approve'" in DRAFT_JS
    assert APP_SRC.count("@app.route('/api/schedule/approve'") == 1


def test_both_paths_resolve_to_the_same_section_scope():
    # Generation sends context.section = sectionFilter.value, whose options are sec.id;
    # Draft List sends load-draft's context.sectionId. The server accepts both keys.
    assert re.search(r"section:\s*sectionFilter\.value", GEN_JS)
    assert "opt.value = sec.id;" in GEN_JS
    assert ("ctx.get('sectionId') or ctx.get('section_id') or ctx.get('section')"
            in APP_SRC[APP_SRC.index('def api_approve_schedule'):])


# ── Post-publish behaviour must match Draft List ───────────────────────────

def test_generation_publish_success_opens_manual_editor_like_draft_list():
    b = _gen_publish_success_branch()
    assert 'localStorage.removeItem(_LS_KEY)' in b
    assert 'MANUAL_EDITOR_URL' in b and 'mode=program' in b
    assert '&scheduler=official' in b
    assert '&sect=${encodeURIComponent(ctx.section)}' in b
    assert '&sect_name=' in b
    assert re.search(r'window\.location\.href\s*=', b)
    assert 'return;' in b


def test_generation_publish_success_never_runs_the_editor_handoff():
    b = re.sub(r'//[^\n]*', '', _gen_publish_success_branch())
    for forbidden in ('archive-draft-for-editor', 'save-draft', 'check-existing',
                      'from_generator', 'btnManualEditor.click'):
        assert forbidden not in b, forbidden


def test_draft_list_reference_flow_unchanged():
    approve = DRAFT_JS[DRAFT_JS.index('window.approveDraft'):]
    assert 'window.location.href = _buildManualEditorProgramUrl();' in approve
    assert 'archive-draft-for-editor' not in DRAFT_JS


# ── Why the handoff must not follow a publish (documents the hazard) ───────

class _Cur:
    def __init__(self, log):
        self.log = log
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.log.append(' '.join(sql.split()))

    def fetchone(self):
        return {'semesterid': 1}

    def close(self):
        pass


class _Conn:
    def __init__(self, log):
        self.log = log

    def cursor(self, cursor_factory=None):
        return _Cur(self.log)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_editor_override_archives_draft_only(monkeypatch):
    # Save Draft / Go to Manual Editor replace the section's Draft; the Published schedule
    # stays live until a successful Publish replaces it.
    log = []
    monkeypatch.setattr(app, 'get_db_connection', lambda: _Conn(log))
    resp = app.app.test_client().post('/api/schedule/archive-draft-for-editor', json={
        'program': 'BSA', 'yearLevel': 3, 'term': 'A', 'acadYear': 'AY2627', 'section': '478',
    })
    assert resp.get_json()['success'] is True
    update = next(s for s in log if s.startswith('UPDATE schedule_version'))
    assert "sv.status = 'Draft'" in update
    assert 'Published' not in update
    assert 's.sectionid = %s' in update
