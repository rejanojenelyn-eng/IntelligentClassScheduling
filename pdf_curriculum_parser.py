"""
pdf_curriculum_parser.py
Extracts curriculum data from PDF files (text-based and Word-exported).

Strategy order (per-page):
  1. pdfplumber table extraction – 6 different settings combinations
  2. Word-position layout reconstruction – handles Word PDFs with thin/no borders
  3. Line-by-line raw text parsing – last resort

Returns subjects with keys: yl, sem, sc, sn, u, lc, lb, th, pre, co
"""

import io
import re
import pdfplumber

# ---------------------------------------------------------------------------
# Year-level / semester context markers
# ---------------------------------------------------------------------------

_YEAR_MARKERS = [
    # 5th year – put higher numbers first so position-based ties break correctly
    ('FIFTH YEAR',    5), ('5TH YEAR',    5), ('YEAR 5',        5),
    ('YEAR V',        5), ('YEAR LEVEL 5',5), ('YEAR LEVEL V',  5),
    # 4th year
    ('FOURTH YEAR',   4), ('4TH YEAR',    4), ('YEAR 4',        4),
    ('YEAR IV',       4), ('YEAR LEVEL 4',4), ('YEAR LEVEL IV', 4),
    # 3rd year
    ('THIRD YEAR',    3), ('3RD YEAR',    3), ('YEAR 3',        3),
    ('YEAR III',      3), ('YEAR LEVEL 3',3), ('YEAR LEVEL III',3),
    # 2nd year
    ('SECOND YEAR',   2), ('2ND YEAR',    2), ('YEAR 2',        2),
    ('YEAR II',       2), ('YEAR LEVEL 2',2), ('YEAR LEVEL II', 2),
    # 1st year  ← must come LAST so "YEAR I" doesn't shadow "YEAR II/III/IV"
    ('FIRST YEAR',    1), ('1ST YEAR',    1), ('YEAR 1',        1),
    ('YEAR I',        1), ('YEAR LEVEL 1',1), ('YEAR LEVEL I',  1),
]

_SEM_MARKERS = [
    ('SUMMER TERM',     'C'), ('SUMMER SESSION', 'C'), ('MIDYEAR', 'C'),
    ('MID-YEAR',        'C'), ('SUMMER',         'C'),
    ('SECOND SEMESTER', 'B'), ('2ND SEMESTER',   'B'), ('2ND SEM', 'B'),
    ('SEMESTER 2',      'B'), ('SEM 2',          'B'), ('SEM II',  'B'),
    ('FIRST SEMESTER',  'A'), ('1ST SEMESTER',   'A'), ('1ST SEM', 'A'),
    ('SEMESTER 1',      'A'), ('SEM 1',          'A'), ('SEM I',   'A'),
]

# ---------------------------------------------------------------------------
# Subject-code regex
# ---------------------------------------------------------------------------

# Accepts: CC101, CC 101, MATH101, NSTP 1, PE 1, GEC-101, CS101L, GEED 032 …
_CODE_RE = re.compile(r'^[A-Z]{1,8}[\s\-]?\d{1,4}[A-Z0-9]?$', re.IGNORECASE)
# Loose check used for inferred column detection (anchored at start only)
_CODE_LOOSE_RE = re.compile(r'^[A-Z]{2,}[\s\-]?\d+', re.IGNORECASE)
# Compound codes: "ELEC HM-E1", "ELEC IT-FE2", "ELEC CS-E2" …
# Pattern: 2-8 letters  SPACE  1-6 alphanumeric  DASH  one or more alphanumeric
# (suffix is unrestricted alphanumeric to handle "FE2", "E1", "FE1", etc.)
_CODE_COMPOUND_RE = re.compile(r'^[A-Z]{2,8}\s[A-Z0-9]{1,6}-[A-Z0-9]+$', re.IGNORECASE)

# ---------------------------------------------------------------------------
# Words that should NEVER be treated as a subject code even if they happen
# to be 2-6 uppercase letters sitting in the code column position.
# ---------------------------------------------------------------------------
_NON_CODE_UPPER = {
    'YEAR', 'SEMESTER', 'SEM', 'TOTAL', 'UNITS', 'UNIT',
    'SUBJECT', 'COURSE', 'CREDIT', 'LECTURE', 'LABORATORY',
    'TUITION', 'HOURS', 'HOUR', 'DESCRIPTION', 'TITLE', 'NAME',
    'CODE', 'NUMBER', 'FIRST', 'SECOND', 'THIRD', 'FOURTH', 'FIFTH',
    'SUMMER', 'ELECTIVES', 'NOTES', 'REMARKS', 'NOTE',
    'LEC', 'LAB', 'PRE', 'REQ', 'COREQ', 'PREREQ',
    'AND', 'THE', 'FOR', 'WITH', 'FROM', 'INTO', 'UPON',
}

# ---------------------------------------------------------------------------
# Column-header patterns and matching
# ---------------------------------------------------------------------------

# Each pattern list goes from most-specific to least-specific.
# Short patterns (≤ 3 chars) are matched as whole words only.
_COL_PATTERNS = {
    # ── Year level & semester columns (per-row extraction) ────────────────
    'yl':  ['year level', 'yr. level', 'yr level', 'year lvl', 'year lev',
            'yr lev', 'year', 'yr'],
    'sem': ['semester', 'sem'],
    # ── Subject fields ────────────────────────────────────────────────────
    'sc':  ['subject code', 'subj code', 'subj. code', 'course code',
            'course no', 'code no', 'course num', 'subj no', 'course'],
    'sn':  ['descriptive title', 'subject title', 'course title',
            'subject name', 'course name', 'description', 'title', 'desc'],
    'pre': ['pre-req', 'prerequisite', 'pre req', 'pre-requisite',
            'pre requisite', 'prereq'],
    'co':  ['co-req', 'corequisite', 'co req', 'co-requisite',
            'co requisite', 'coreq'],
    'lc':  ['lec hrs', 'lec. hrs', 'lec hours', 'lecture hours',
            'lecture hrs', 'lec hr', 'lec.', 'lec'],
    'lb':  ['lab hrs', 'lab. hrs', 'lab hours', 'laboratory hours',
            'laboratory hrs', 'lab hr', 'lab.', 'lab'],
    'th':  ['tuition hrs', 'tuition hours', 'tuition hr', 'tut hrs',
            'tuition', 't.h.', 'th'],
    'u':   ['credit units', 'credited units', 'credit unit', 'units',
            'cu', 'credit', 'unit'],
}

_TABLE_STRATEGIES = [
    {"vertical_strategy": "lines",        "horizontal_strategy": "lines",
     "snap_tolerance": 5,  "join_tolerance": 3,  "edge_min_length": 3},
    {"vertical_strategy": "lines",        "horizontal_strategy": "lines",
     "snap_tolerance": 10, "join_tolerance": 5},
    {"vertical_strategy": "lines_strict", "horizontal_strategy": "lines_strict"},
    {"vertical_strategy": "text",         "horizontal_strategy": "lines",
     "snap_tolerance": 5},
    {"vertical_strategy": "text",         "horizontal_strategy": "text",
     "snap_tolerance": 3},
    {"vertical_strategy": "text",         "horizontal_strategy": "text",
     "snap_tolerance": 8},
]

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _clean(val):
    if val is None:
        return ''
    # Collapse all whitespace / newlines to a single space
    return re.sub(r'\s+', ' ', str(val)).strip()

def _parse_int(val):
    try:
        v = _clean(val).replace(',', '')
        return int(float(v)) if v else 0
    except Exception:
        return 0

def _is_whole_word(text, pos, marker):
    """
    True if the marker at position pos in text is surrounded by non-alphanumeric
    characters (or string boundaries).  Prevents short patterns like 'YEAR I' from
    matching inside 'YEAR IN' or 'YEAR II'.
    """
    end = pos + len(marker)
    before = text[pos - 1] if pos > 0 else ' '
    after  = text[end]     if end < len(text) else ' '
    return not (before.isalpha() or before.isdigit()) and \
           not (after.isalpha()  or after.isdigit())


def _detect_year_sem(text):
    """
    Return (year_int_or_None, sem_str_or_None) based on the marker that appears
    EARLIEST in the text, not the first one in the priority list.
    This prevents 'SECOND' being chosen over 'FIRST' when First Year appears earlier.
    Whole-word matching prevents 'YEAR I' from falsely matching 'YEAR IN', 'YEAR II', etc.
    """
    upper = text.upper()

    year = None
    year_pos = len(upper) + 1
    for marker, num in _YEAR_MARKERS:
        pos = upper.find(marker)
        if 0 <= pos < year_pos and _is_whole_word(upper, pos, marker):
            year_pos = pos
            year = num

    sem = None
    sem_pos = len(upper) + 1
    for marker, code in _SEM_MARKERS:
        pos = upper.find(marker)
        if 0 <= pos < sem_pos and _is_whole_word(upper, pos, marker):
            sem_pos = pos
            sem = code

    return year, sem

def _looks_like_code(text):
    t = _clean(text)
    return bool(_CODE_RE.match(t) or _CODE_LOOSE_RE.match(t) or _CODE_COMPOUND_RE.match(t))

def _parse_year_cell(text):
    """
    Parse a year-level cell value → int 1-5, or 0 if unrecognised.
    Handles: "1", "1st", "First", "FIRST YEAR", "YEAR LEVEL 1", etc.
    """
    t = _clean(text)
    if not t:
        return 0
    # Try the full marker list first (handles "FIRST YEAR", "YEAR LEVEL 1", etc.)
    yl, _ = _detect_year_sem(t)
    if yl:
        return yl
    # Bare digit 1-5
    m = re.search(r'\b([1-5])\b', t)
    if m:
        return int(m.group(1))
    return 0

def _parse_sem_cell(text):
    """
    Parse a semester cell value → 'A', 'B', 'C', or '' if unrecognised.
    Handles: "1", "1st", "First", "1st Semester", "SUMMER", etc.
    """
    t = _clean(text).upper()
    if not t:
        return ''
    # Full marker detection
    _, sem = _detect_year_sem(t)
    if sem:
        return sem
    # Bare tokens
    if t in ('1', 'I', '1ST', 'FIRST', 'A'):
        return 'A'
    if t in ('2', 'II', '2ND', 'SECOND', 'B'):
        return 'B'
    if t in ('3', 'III', '3RD', 'SUMMER', 'SUM', 'MID', 'C'):
        return 'C'
    return ''

def _is_skip_row(row):
    cells = [_clean(c) for c in row if _clean(c)]
    if not cells:
        return True
    # pdfplumber occasionally merges the last subject row with the TOTAL UNITS
    # footer into one row.  If the first non-empty cell is a valid subject code
    # we must keep the row even if "TOTAL UNITS" appears later in that same row.
    if _looks_like_code(cells[0]):
        return False
    joined = ' '.join(cells).upper()
    return any(tok in joined for tok in (
        'TOTAL UNITS', 'GRAND TOTAL', 'SUBTOTAL', 'TOTAL:', 'SUM:',
        'TOTAL UNIT'
    ))

# ---------------------------------------------------------------------------
# Column-header matching (whole-word for short patterns)
# ---------------------------------------------------------------------------

def _cell_matches(cell_lower, patterns):
    """
    Return True if any pattern matches cell_lower.
    Short patterns (≤ 3 chars) require whole-word match to avoid false positives
    like 'th' inside 'other' or 'with'.
    """
    for p in patterns:
        if len(p) <= 3:
            # Whole-word boundary match
            if re.search(r'(?<![a-z0-9])' + re.escape(p) + r'(?![a-z0-9])', cell_lower):
                return True
        else:
            if p in cell_lower:
                return True
    return False


def _detect_col_map(header_rows):
    """
    Merge up to 3 header rows column-by-column, then map field → column index.
    This handles tables whose headers span two rows (e.g. 'Hours' over 'Lec Lab').
    """
    if not header_rows:
        return {}
    n_cols = max((len(r) for r in header_rows[:3]), default=0)
    merged = []
    for j in range(n_cols):
        parts = []
        for row in header_rows[:3]:
            if j < len(row):
                c = _clean(row[j]).lower()
                if c:
                    parts.append(c)
        merged.append(' '.join(parts))

    col_map = {}
    for field, patterns in _COL_PATTERNS.items():
        for j, cell in enumerate(merged):
            if _cell_matches(cell, patterns) and field not in col_map:
                col_map[field] = j
                break
    return col_map


def _infer_col_map(rows):
    """
    When no header row is detected, infer column roles from data patterns.
    Philippine curriculum tables typically read left-to-right as:
      SubjectCode | Description | Pre-req | [Co-req] | Lec | Lab | Units | TH
    """
    if not rows:
        return {}
    num_cols = max((len(r) for r in rows), default=0)
    sample = [r for r in rows[:15] if r and any(_clean(c) for c in r)]
    if not sample:
        return {}

    # ── Detect code column (first column whose values mostly look like codes) ─
    code_col = -1
    for j in range(min(4, num_cols)):
        hits = sum(1 for r in sample if j < len(r) and _looks_like_code(_clean(r[j])))
        if hits >= max(1, len(sample) * 0.4):
            code_col = j
            break
    if code_col < 0:
        return {}

    # ── Detect name column (longest average non-numeric text after code col) ─
    name_col = -1
    best_avg = 0
    for j in range(code_col + 1, num_cols):
        vals = [_clean(r[j]) for r in sample if j < len(r) and _clean(r[j])]
        if not vals:
            continue
        avg = sum(len(v) for v in vals) / len(vals)
        non_num = sum(1 for v in vals if not re.match(r'^\d+\.?\d*$', v))
        if avg > best_avg and non_num > len(vals) * 0.4 and avg > 5:
            best_avg = avg
            name_col = j

    col_map = {'sc': code_col}
    if name_col >= 0:
        col_map['sn'] = name_col

    # ── Detect numeric columns (mostly digits) sorted left-to-right ───────
    after = max(code_col + 1, (name_col + 1 if name_col >= 0 else code_col + 1))
    numeric_cols = []
    for j in range(after, num_cols):
        vals = [_clean(r[j]) for r in sample if j < len(r) and _clean(r[j])]
        if not vals:
            continue
        ratio = sum(1 for v in vals if re.match(r'^\d+\.?\d*$', v)) / len(vals)
        if ratio >= 0.5:
            numeric_cols.append(j)

    numeric_cols.sort()  # left-to-right
    n = len(numeric_cols)

    # Philippine layout left-to-right: Lec | Lab | Units | TH
    if n >= 4:
        col_map['lc'] = numeric_cols[-4]
        col_map['lb'] = numeric_cols[-3]
        col_map['u']  = numeric_cols[-2]
        col_map['th'] = numeric_cols[-1]
    elif n == 3:
        col_map['lc'] = numeric_cols[-3]
        col_map['lb'] = numeric_cols[-2]
        col_map['u']  = numeric_cols[-1]
    elif n == 2:
        col_map['lc'] = numeric_cols[-2]
        col_map['u']  = numeric_cols[-1]
    elif n == 1:
        col_map['u']  = numeric_cols[-1]

    return col_map

# ---------------------------------------------------------------------------
# Strategy 1 – pdfplumber table extraction
# ---------------------------------------------------------------------------

def _extract_via_tables(pdf, override_col_map=None):
    """
    Strategy 1: pdfplumber table extraction.

    Crucially, year/sem context is detected from the text BETWEEN tables (section
    headings like "THIRD YEAR – First Semester"), NOT from a full-page scan.
    Full-page scanning finds the *earliest* marker on the page, which is always the
    first year/sem mentioned (e.g. "FIRST YEAR" on a page that also has "FOURTH YEAR"
    lower down).  Per-table section-text scanning gives every table exactly the
    heading that precedes it.
    """
    subjects, warnings, all_skipped = [], [], []
    current_year, current_sem = 0, 'A'
    last_col_map = None   # reused as fallback for headerless mini-tables

    for page in pdf.pages:
        table_objs = []
        for strategy in _TABLE_STRATEGIES:
            try:
                found = page.find_tables(strategy) or []
                if found:
                    table_objs = found
                    break
            except Exception:
                continue

        prev_bottom = 0   # y-coordinate bottom of the previous table on this page

        for tbl_obj in table_objs:
            tbl_top = tbl_obj.bbox[1]

            # ── Scan the text that sits between the previous table and this one ──
            # This band contains the section heading (FIRST YEAR / Second Semester …).
            if tbl_top > prev_bottom:
                try:
                    band = page.crop((0, prev_bottom, page.width, tbl_top))
                    band_text = band.extract_text() or ''
                    dy, ds = _detect_year_sem(band_text)
                    if dy: current_year = dy
                    if ds: current_sem  = ds
                except Exception:
                    pass

            prev_bottom = tbl_obj.bbox[3]   # bottom of this table

            table = tbl_obj.extract()
            if not table:
                continue
            result = _process_table(table, current_year, current_sem, override_col_map,
                                    fallback_col_map=last_col_map)
            subjects.extend(result['subjects'])
            all_skipped.extend(result.get('skipped', []))
            if result.get('col_map'):
                last_col_map = result['col_map']
            current_year = result['year']
            current_sem  = result['sem']

    return subjects, warnings, all_skipped

# ---------------------------------------------------------------------------
# Strategy 2 – Word-position layout reconstruction
# ---------------------------------------------------------------------------

_Y_TOL = 4
_X_TOL = 15


def _words_to_rows(page):
    try:
        words = page.extract_words(
            x_tolerance=3, y_tolerance=_Y_TOL,
            keep_blank_chars=False, use_text_flow=False,
        )
    except Exception:
        return []
    if not words:
        return []

    buckets = {}
    for w in words:
        matched = None
        for ey in buckets:
            if abs(ey - w['top']) <= _Y_TOL:
                matched = ey
                break
        key = matched if matched is not None else w['top']
        buckets.setdefault(key, []).append(w)

    rows = []
    for y in sorted(buckets):
        rows.append(sorted(buckets[y], key=lambda w: w['x0']))
    return rows


def _detect_col_boundaries(word_rows):
    from collections import Counter
    x_counts = Counter()
    for row in word_rows:
        for w in row:
            bucket = round(w['x0'] / _X_TOL) * _X_TOL
            x_counts[bucket] += 1
    min_freq = max(2, len(word_rows) * 0.15)
    return sorted(x for x, cnt in x_counts.items() if cnt >= min_freq)


def _assign_cols(word_rows, boundaries):
    if not boundaries:
        return [[' '.join(w['text'] for w in row)] for row in word_rows]
    result = []
    for row in word_rows:
        cols = [''] * len(boundaries)
        for w in row:
            col_idx = 0
            for i, bx in enumerate(boundaries):
                if w['x0'] >= bx - _X_TOL:
                    col_idx = i
            sep = ' ' if cols[col_idx] else ''
            cols[col_idx] += sep + w['text']
        result.append([c.strip() for c in cols])
    return result


def _extract_via_word_layout(pdf, override_col_map=None):
    """
    Strategy 2: word-position layout reconstruction.

    No page-level year/sem scan here: section headers ("THIRD YEAR", "First Semester")
    appear as ordinary word-rows and are detected inside _process_table's per-row
    scanner.  A page-level scan would find the EARLIEST year on the page (often
    "FIRST YEAR" from earlier content) and incorrectly overwrite the context.
    """
    subjects, warnings, all_skipped = [], [], []
    current_year, current_sem = 0, 'A'
    last_col_map = None   # carry column map across pages for headerless continuations

    for page in pdf.pages:
        word_rows = _words_to_rows(page)
        if not word_rows:
            continue
        boundaries = _detect_col_boundaries(word_rows)
        table = _assign_cols(word_rows, boundaries)
        if not table:
            continue

        result = _process_table(table, current_year, current_sem, override_col_map,
                                fallback_col_map=last_col_map)
        subjects.extend(result['subjects'])
        all_skipped.extend(result.get('skipped', []))
        if result.get('col_map'):
            last_col_map = result['col_map']
        current_year = result['year']
        current_sem  = result['sem']

    return subjects, warnings, all_skipped

# ---------------------------------------------------------------------------
# Strategy 3 – Raw line-by-line text parsing
# ---------------------------------------------------------------------------

def _extract_via_text(full_text):
    subjects, warnings = [], []
    warnings.append('No table structure detected — using line-by-line text scan.')
    current_year, current_sem = 0, 'A'

    for line in full_text.splitlines():
        line = line.strip()
        if not line:
            continue

        dy, ds = _detect_year_sem(line)
        if dy: current_year = dy
        if ds: current_sem = ds

        parts = line.split()
        if not parts:
            continue

        # Try first token alone, then merged, then two-token compound form
        candidate = parts[0]
        if not _CODE_RE.match(candidate) and len(parts) >= 2:
            merged = parts[0] + parts[1]
            if _CODE_RE.match(merged):
                candidate = merged
                parts = [candidate] + parts[2:]
            else:
                # e.g. "ELEC IT-FE2 BSIT Free Elective 2" — first two tokens are the code
                compound = parts[0] + ' ' + parts[1]
                if _CODE_COMPOUND_RE.match(compound.upper()):
                    candidate = compound
                    parts = [candidate] + parts[2:]

        if not _looks_like_code(candidate):
            continue

        # Scan right-to-left for trailing numbers
        num_start = len(parts)
        for i in range(len(parts) - 1, 0, -1):
            try:
                float(parts[i])
                num_start = i
            except ValueError:
                break

        sn      = ' '.join(parts[1:num_start]) if num_start > 1 else candidate
        numbers = parts[num_start:]

        # Expect order: lc, lb, u, th  (left to right)
        n = len(numbers)
        lc = _parse_int(numbers[0]) if n >= 1 else 0
        lb = _parse_int(numbers[1]) if n >= 2 else 0
        u  = _parse_int(numbers[2]) if n >= 3 else (_parse_int(numbers[-1]) if n else 0)
        th = _parse_int(numbers[3]) if n >= 4 else 0

        subjects.append(_make_subject(
            candidate.upper(), sn, u, lc, lb, th, '', '',
            current_year, current_sem,
        ))

    return subjects, warnings

# ---------------------------------------------------------------------------
# Shared table-row processor (used by strategies 1 & 2)
# ---------------------------------------------------------------------------

def _process_table(table, init_year, init_sem, override_col_map=None, fallback_col_map=None):
    current_year, current_sem = init_year, init_sem
    subjects = []

    # ── Find header row (or use user-specified override) ──────────────────
    if override_col_map is not None:
        col_map = override_col_map
        # Still detect and skip any header row present in the table
        data_start = 0
        for i in range(min(4, len(table))):
            window = table[max(0, i - 1):i + 2]
            if _detect_col_map(window):
                data_start = i + 1
                break
    else:
        col_map = {}
        data_start = 0
        for i in range(min(6, len(table))):
            window = table[max(0, i - 1):i + 2]
            cm = _detect_col_map(window)
            if 'sc' in cm or 'sn' in cm:
                col_map = cm
                data_start = i + 1
                break
        # ── Fallback 1: infer column roles from data patterns ─────────────
        if not col_map:
            col_map = _infer_col_map(table)
            data_start = 0
        # ── Fallback 2: reuse the previous table's column map ─────────────
        # This handles mini-tables that pdfplumber splits off at page boundaries
        # (e.g. the very last subject row before TOTAL UNITS on the final page).
        if not col_map and fallback_col_map:
            col_map = fallback_col_map
            data_start = 0

    if not col_map or 'sc' not in col_map:
        return {'subjects': subjects, 'year': current_year, 'sem': current_sem,
                'skipped': [], 'col_map': None}

    # ── Scan skipped header rows for year/sem context ────────────────────
    for row in table[:data_start]:
        if not row:
            continue
        row_text = ' '.join(_clean(c) for c in row if _clean(c))
        dy, ds = _detect_year_sem(row_text)
        if dy: current_year = dy
        if ds: current_sem  = ds

    skipped_rows = []   # rows rejected during extraction, with reason

    # ── Extract data rows ────────────────────────────────────────────────
    for row in table[data_start:]:
        if not row:
            continue
        if _is_skip_row(row):
            cells = [_clean(c) for c in row if _clean(c)]
            if cells:
                skipped_rows.append({'cells': cells[:8], 'reason': 'total/summary row'})
            continue

        # gcell defined first so it's available for all field reads below
        def gcell(key, _row=row):
            idx = col_map.get(key)
            if idx is None or idx >= len(_row):
                return ''
            return _clean(_row[idx])

        # ── Update contextual year/sem from embedded section headers ──────
        row_text = ' '.join(_clean(c) for c in row if _clean(c))
        dy, ds = _detect_year_sem(row_text)
        if dy: current_year = dy
        if ds: current_sem  = ds

        # ── Per-row Year Level column (highest priority) ──────────────────
        row_year = current_year
        row_sem  = current_sem
        if 'yl' in col_map:
            v = _parse_year_cell(gcell('yl'))
            if 1 <= v <= 5:
                row_year = v
        if 'sem' in col_map:
            v = _parse_sem_cell(gcell('sem'))
            if v:
                row_sem = v

        # ── Subject code ──────────────────────────────────────────────────
        sc_idx = col_map.get('sc', 0)
        sc = _clean(row[sc_idx]) if sc_idx < len(row) else ''

        # Handle merged "CODE Description" single cells.
        # Three cases:
        #   "CC101 Introduction to Computing"  → single-token code + description
        #   "GEED 032 Understanding the Self"  → two-token code ("GEED 032") + description
        #   "GEED 032"                         → pure two-token code, keep as-is
        overflow_name = ''
        if sc and ' ' in sc:
            parts = sc.split(None, 1)
            first = parts[0]
            rest  = parts[1] if len(parts) > 1 else ''
            first_nospace = re.sub(r'\s+', '', first).upper()
            if rest and (_CODE_RE.match(first_nospace) or _CODE_LOOSE_RE.match(first_nospace)):
                # "CC101 Introduction…"  — first token alone is a complete code
                sc, overflow_name = first, rest
            elif rest and _CODE_COMPOUND_RE.match((first + ' ' + rest.split()[0]).upper() if rest.split() else ''):
                # "ELEC HM-E1 Elective 1"  — first two tokens form a compound code
                second = rest.split(None, 1)
                sc = first + ' ' + second[0]
                overflow_name = second[1] if len(second) > 1 else ''
            elif rest:
                # Try "GEED 032 Understanding…"  — first TWO tokens form the code
                rest_parts = rest.split(None, 1)
                if len(rest_parts) >= 2:
                    combined_nospace = (first + rest_parts[0]).upper()
                    if _CODE_RE.match(combined_nospace) or _CODE_LOOSE_RE.match(combined_nospace):
                        sc = first + ' ' + rest_parts[0]   # preserve "GEED 032"
                        overflow_name = rest_parts[1]

        # Keep meaningful internal spacing (e.g. "GEED 032" → "GEED 032", not "GEED032")
        sc_clean = _clean(sc).upper()

        if not sc_clean:
            # Continuation row – no code; try to append description to previous subject.
            # Only merge when: previous subject exists, row has a description, no numeric
            # values (which would indicate a new subject), and this row isn't a year/sem marker.
            if subjects and 'sn' in col_map and not dy and not ds:
                cont_desc = gcell('sn')
                has_nums  = any(
                    _parse_int(gcell(k)) > 0
                    for k in ('u', 'lc', 'lb', 'th') if k in col_map
                )
                if cont_desc and not has_nums:
                    subjects[-1]['sn'] = (
                        subjects[-1]['sn'] + ' ' + cont_desc
                    ).strip()[:200]
            continue

        # Skip obvious column-header keywords that ended up in the code cell
        if sc_clean in ('SUBJECTCODE', 'COURSECODE', 'CODE', 'SUBJECT', 'COURSE',
                        'DESCRIPTIVE', 'TITLE', 'DESCRIPTION'):
            skipped_rows.append({'cells': [sc_clean], 'reason': 'column-header keyword in code cell'})
            continue

        # Accept standard alphanumeric codes (e.g. CC101, DCIT23A, NSTP1)
        # or short pure-alpha codes like OJT, ITP, PRAC – as long as they're
        # not one of the common non-code header/marker words.
        if not _looks_like_code(sc_clean):
            is_short_alpha = (
                re.match(r'^[A-Z]{2,7}$', sc_clean)
                and sc_clean not in _NON_CODE_UPPER
            )
            if not is_short_alpha:
                skipped_rows.append({'cells': [sc_clean], 'reason': f'unrecognized subject-code format: {sc_clean!r}'})
                continue

        # ── Subject name ──────────────────────────────────────────────────
        sn = gcell('sn') or overflow_name
        sn = re.sub(r'\s+', ' ', sn).strip()
        sn = re.sub(r'\s*\([A-Z]{1,8}[\s\-]?\d{1,4}\)\s*$', '', sn).strip()
        sn = sn[:200] or sc_clean

        # ── Numeric fields ────────────────────────────────────────────────
        u  = _parse_int(gcell('u'))
        lc = _parse_int(gcell('lc'))
        lb = _parse_int(gcell('lb'))
        th = _parse_int(gcell('th'))

        # ── Pre-req / Co-req ──────────────────────────────────────────────
        pre = gcell('pre')
        co  = gcell('co')
        if pre.upper() in ('NONE', 'N/A', '-', '', 'NO PREREQUISITE'):
            pre = ''
        if co.upper()  in ('NONE', 'N/A', '-', '', 'NO COREQUISITE'):
            co  = ''

        subjects.append(_make_subject(
            sc_clean, sn, u, lc, lb, th, pre, co,
            row_year, row_sem,          # ← use per-row values, not stale context
        ))

    return {'subjects': subjects, 'year': current_year, 'sem': current_sem,
            'skipped': skipped_rows, 'col_map': col_map}

# ---------------------------------------------------------------------------
# Subject dict factory & utilities
# ---------------------------------------------------------------------------

def _make_subject(sc, sn, u, lc, lb, th, pre, co, yl, sem):
    return {
        'yl': yl, 'sem': sem,
        'sc': sc,  'sn': sn or sc,
        'u':  u,   'lc': lc, 'lb': lb, 'th': th,
        'pre': pre, 'co': co,
    }

def _normalize_code_key(sc):
    """Canonical dedup key – ignore spacing so 'GEED 032' == 'GEED032'."""
    return re.sub(r'\s+', '', sc).upper()

def _deduplicate(subjects):
    seen, out = set(), []
    for s in subjects:
        key = _normalize_code_key(s['sc'])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out

def _score(subjects):
    if not subjects:
        return 0
    req = ['sc', 'sn', 'u']
    bon = ['yl', 'sem', 'lc']
    rf = sum(1 for s in subjects for f in req if s.get(f))
    bf = sum(1 for s in subjects for f in bon if s.get(f))
    base  = int((rf / (len(subjects) * len(req))) * 75)
    extra = int((bf / (len(subjects) * len(bon))) * 25)
    return base + extra

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_curriculum_pdf(file_bytes, override_col_map=None):
    """
    Parse a curriculum PDF and return structured subject data.

    Returns dict with keys:
        subjects      – list of subject dicts (yl, sem, sc, sn, u, lc, lb, th, pre, co)
        confidence    – int 0-100
        warnings      – list of strings
        subject_count – int
        skipped_rows  – list of {cells, reason} dicts for rows the parser rejected
        raw_text      – first 3000 chars of extracted text (for debugging)
    """
    subjects, warnings, skipped_rows = [], [], []
    raw_text = ''

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            # Collect raw text for fallback and debugging
            all_text = []
            for page in pdf.pages:
                t = page.extract_text() or ''
                all_text.append(t)
            raw_text = '\n'.join(all_text)

            # Strategy 1: explicit table detection
            s1, w1, sk1 = _extract_via_tables(pdf, override_col_map)

            # Strategy 2: word-position layout – always run as a supplementary pass.
            # Pages where pdfplumber's table detector fails (e.g. invisible-border Word
            # tables) are still processed, filling in subjects that S1 missed.
            s2, w2, sk2 = _extract_via_word_layout(pdf, override_col_map)

            # Merge: S1 is authoritative; S2 adds any subject codes S1 did not find.
            # Use normalized keys so "GEED 032" and "GEED032" are treated as the same.
            if s1 or s2:
                s1_codes  = {_normalize_code_key(s['sc']) for s in s1}
                s2_extra  = [s for s in s2 if _normalize_code_key(s['sc']) not in s1_codes]
                subjects  = s1 + s2_extra
                # Skipped rows: union from both strategies (deduplicate by cell content)
                seen_skip = set()
                for sk in sk1 + sk2:
                    key = tuple(sk['cells'])
                    if key not in seen_skip:
                        seen_skip.add(key)
                        skipped_rows.append(sk)

                if s1 and s2_extra:
                    warnings = list(w1)
                    warnings.append(
                        f'Word-position layout recovered {len(s2_extra)} additional subject(s) '
                        f'from sections that the table detector missed.'
                    )
                elif s1:
                    warnings = list(w1)
                else:
                    warnings = list(w2)

            # Strategy 3: raw text lines (last resort – nothing extracted at all)
            if not subjects and raw_text.strip():
                s3, w3 = _extract_via_text(raw_text)
                if s3:
                    subjects, warnings = s3, w3

    except Exception as exc:
        warnings.append(f'pdfplumber error: {exc}')
        try:
            import fitz
            doc = fitz.open(stream=file_bytes, filetype='pdf')
            raw_text = '\n'.join(page.get_text() for page in doc)
            subjects, w = _extract_via_text(raw_text)
            warnings.extend(w)
        except Exception as exc2:
            warnings.append(f'PyMuPDF fallback error: {exc2}')

    subjects = _deduplicate(subjects)

    # ── Year-level forward-fill ──────────────────────────────────────────────
    # If some subjects have yl=0 (no marker detected), inherit the year level
    # from the nearest preceding subject that has one.  This handles PDFs where
    # the year heading is only printed once per section and sits in a merged cell
    # that pdfplumber cannot always read as part of the table.
    unassigned_count = sum(1 for s in subjects if not s.get('yl'))
    if unassigned_count and len(subjects) > unassigned_count:
        # Forward pass
        last_yl = 0
        for s in subjects:
            if s.get('yl') and s['yl'] > 0:
                last_yl = s['yl']
            elif last_yl > 0:
                s['yl'] = last_yl
        # Backward pass — fill any subjects before the first detected year marker
        first_yl = next((s['yl'] for s in subjects if s.get('yl') and s['yl'] > 0), 0)
        if first_yl:
            for s in subjects:
                if not s.get('yl') or s['yl'] == 0:
                    s['yl'] = first_yl
                else:
                    break
        auto_filled = unassigned_count - sum(1 for s in subjects if not s.get('yl'))
        if auto_filled:
            warnings.append(
                f'{auto_filled} subject(s) had no year-level marker in the PDF — '
                f'year level was inferred from surrounding subjects. '
                f'Please verify in the review screen.'
            )

    # ── Post-extraction warnings ─────────────────────────────────────────────
    if not subjects:
        if not raw_text.strip():
            warnings.append(
                'This PDF appears to be a scanned image (no text layer found). '
                'Please use a text-based PDF, or export the curriculum as CSV instead.'
            )
        else:
            warnings.append(
                'No subjects could be extracted automatically. '
                'Review the raw text below — you can manually enter subjects in the table.'
            )
    else:
        no_yl  = sum(1 for s in subjects if not s.get('yl'))
        no_sem = sum(1 for s in subjects if not s.get('sem'))
        no_th  = sum(1 for s in subjects if not s.get('th'))
        if no_yl:
            warnings.append(
                f'{no_yl} subject(s) have no detected year level — please set in the review screen.'
            )
        if no_sem:
            warnings.append(
                f'{no_sem} subject(s) have no detected semester — please set in the review screen.'
            )

    # Filter out pure summary/total skip entries from the skipped list so only
    # rows that look like they *could* have been subjects are surfaced.
    meaningful_skipped = [
        sk for sk in skipped_rows
        if sk.get('reason') != 'total/summary row'
    ]

    if meaningful_skipped:
        warnings.append(
            f'{len(meaningful_skipped)} row(s) were detected but not imported — '
            f'see "skipped_rows" in the response for details.'
        )

    return {
        'subjects':      subjects,
        'confidence':    _score(subjects),
        'warnings':      warnings,
        'subject_count': len(subjects),
        'skipped_rows':  meaningful_skipped,
        'raw_text':      raw_text[:3000],
    }
