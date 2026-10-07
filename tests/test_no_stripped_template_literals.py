"""Guard against JS template literals that lost their `$` (e.g. a shell edit that
expanded `${name}` away), which silently render literal text like "name [{typ}]"
or send broken URLs like "?date=encodeURIComponent(date){roomParam}"."""
import glob
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
# A backtick string on one line containing "{identifier...}" NOT preceded by "$",
# outside Jinja ({{ }} / {% %}) and not an object/regex literal.
_SUSPECT = re.compile(r"`[^`\n]*(?<![$\{])\{[A-Za-z_][A-Za-z0-9_.()]*\}[^`\n]*`")


def test_no_template_literal_lost_its_dollar_sign():
    hits = []
    files = glob.glob(str(ROOT / "templates" / "**" / "*.html"), recursive=True) + \
            glob.glob(str(ROOT / "static" / "js" / "**" / "*.js"), recursive=True)
    for f in files:
        for n, line in enumerate(Path(f).read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if "{{" in line or "{%" in line:
                continue
            if _SUSPECT.search(line):
                hits.append(f"{Path(f).relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    assert not hits, "\n".join(hits)
