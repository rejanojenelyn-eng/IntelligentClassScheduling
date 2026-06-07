"""
pdf_faculty_parser.py  v4
Robust extraction of employee/faculty records from PDF files.

Strategy order per page:
  1. pdfplumber table extraction (6 settings variants)
  2. Word-position layout reconstruction
  3. Raw text line parsing (last resort)

Header detection:
  Rows are accumulated one-by-one until _detect_cols() returns a match.
  This handles word-wrapped headers where "EmployeeNumber" is split into
  "Employe" / "eNumbe" / "r" across multiple visual rows in col-0.

Data extraction:
  After the header is located, continuation-row merging (col-0-based)
  reconstructs logical data rows from wrapped cell content.
"""
import io
import re
import pdfplumber

# ---------------------------------------------------------------------------
# Column keyword map
# ---------------------------------------------------------------------------

_COL_KEYWORDS = {
    'emp_num': [
        'employeenumber', 'employeeno',
        'employee number', 'employee no.', 'employee no',
        'emp. number', 'emp number', 'emp. no.', 'emp. no',
        'emp no.', 'emp no', 'emp num', 'employee id',
        'faculty no.', 'faculty no', 'faculty number',
        'id no.', 'id no', 'id number', 'empno',
    ],
    'last_name':  ['lastname', 'last name', 'last_name', 'surname', 'family name'],
    'first_name': ['firstname', 'first name', 'first_name', 'given name', 'fname'],
    'middle_name':['middlename', 'middle name', 'middle_name', 'middle initial', 'm.i.', 'mi'],
    'full_name':  [
        'employee name', 'faculty name', 'full name', 'name of employee',
        'name of faculty', 'instructor name', 'instructor', 'name',
    ],
    'email':   ['emailaddress', 'email address', 'e-mail address', 'e-mail', 'email'],
    'contact': [
        'contactnumber', 'contact number', 'contact no.', 'contact no',
        'telephone number', 'telephone', 'mobile number', 'mobile',
        'phone number', 'phone', 'contact',
    ],
    'specialization': [
        'specialization', 'speciality', 'specialty',
        'department', 'dept.', 'dept', 'college', 'program', 'field',
    ],
    'emp_type': [
        'employeetype',
        'employee type', 'employment type', 'type of employment',
        'appointment type', 'appointment', 'category',
        'employment category', 'type',
    ],
    'status': [
        'employeestatus',
        'employment status', 'emp. status', 'emp status',
        'appointment status', 'status',
    ],
    'designation': [
        'designation', 'academic rank', 'rank', 'position title',
        'position', 'job title', 'title',
    ],
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

_Y_TOL = 4   # points tolerance for grouping words into visual rows
_X_TOL = 8   # points tolerance for column boundary detection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(v):
    if v is None: return ''
    s = str(v)
    # Strip trailing spaces before visual line breaks.
    s = re.sub(r' +\n', '\n', s)
    # Only join WITHOUT a space for clear mid-word visual wraps:
    #   "Temporar\ny…"  → "Temporary…"  (lowercase suffix)
    #   "ALCANTA\nRA"   → "ALCANTARA"   (1-2 ALL-CAPS ending a word)
    #   "0917-55\n5-00" → "0917-555-00" (digit phone continuation)
    # All other \n become spaces so "DELA\nCRUZ" → "DELA CRUZ" not "DELACRUZ".
    s = re.sub(r'([A-Za-z])\n([a-z])', r'\1\2', s)          # lowercase suffix
    s = re.sub(r'([A-Z])\n([A-Z]{1,2})(?=\s|,|$)', r'\1\2', s)  # short ALL-CAPS fragment
    s = re.sub(r'(\d)\n(\d)', r'\1\2', s)                   # digit continuation
    s = re.sub(r'\n', ' ', s)   # remaining \n → space
    return re.sub(r'\s+', ' ', s).strip()


def _detect_cols(row):
    """
    Return column mapping dict if this row is a header, else None.
    Checks with and without spaces (handles word-wrapped header text
    e.g. "Employe eNumbe r" → space-stripped → "employeenumber").
    """
    mapping = {}
    for i, cell in enumerate(row):
        cell_l  = _clean(cell).lower()
        cell_ns = re.sub(r'\s+', '', cell_l)   # all spaces removed
        if not cell_l:
            continue
        for field, keywords in _COL_KEYWORDS.items():
            if field in mapping:
                continue
            for kw in keywords:
                kw_ns = re.sub(r'\s+', '', kw)
                if (kw == cell_l or kw in cell_l or
                        kw_ns == cell_ns or kw_ns in cell_ns):
                    mapping[field] = i
                    break
    has_name = any(f in mapping for f in ('last_name', 'first_name', 'full_name'))
    return mapping if ('emp_num' in mapping or has_name) else None


def _row_to_employee(row, col_map):
    def gcol(field):
        idx = col_map.get(field)
        return _clean(row[idx]) if idx is not None and idx < len(row) else ''

    emp = {k: gcol(k) for k in
           ('emp_num', 'last_name', 'first_name', 'middle_name',
            'email', 'contact', 'specialization', 'emp_type', 'status', 'designation')}

    if 'full_name' in col_map:
        full = gcol('full_name')
        if full and ',' in full:
            parts = full.split(',', 1)
            if not emp['last_name']:  emp['last_name']  = parts[0].strip()
            if not emp['first_name']: emp['first_name'] = parts[1].strip()
        elif full and not emp['last_name']:
            emp['last_name'] = full

    return emp


def _is_empty(emp):
    return not any(emp.get(k, '') for k in
                   ('emp_num', 'last_name', 'first_name'))


# ---------------------------------------------------------------------------
# Continuation-row merger
# ---------------------------------------------------------------------------

def _should_join_without_space(existing, new_frag):
    """True when new_frag is a mid-word continuation of existing (no space)."""
    if not existing or not new_frag:
        return False
    last_word  = existing.rsplit(None, 1)[-1]
    if not last_word:
        return False
    last_char  = last_word[-1]
    first_char = new_frag[0]

    # Single alpha char — stray mid-word letter ("HERNANDE"+"Z" → "HERNANDEZ")
    if len(new_frag) == 1 and first_char.isalpha():
        return True

    # Lowercase-start suffix ("ry","ing","ment","me","nt","ring","tion" …)
    if first_char.islower() and last_char.isalpha():
        return True

    # Short ALL-CAPS fragment ≤ 3 chars continuing an ALL-CAPS word
    # "ALCANTA"+"RA" → "ALCANTARA"   "BERINGU"+"ELA" → "BERINGUÉLA"
    # Does NOT fire for "DELA"+"CRUZ" (len 4) or "Mary"+"ANN" (last_char lowercase)
    if (first_char.isupper()
            and len(new_frag) <= 3
            and new_frag.isupper()
            and last_char.isupper()):
        return True

    # Digit/hyphen continuation: "0917-55"+"5-2431" → "0917-555-2431"
    if first_char.isdigit() and (last_char.isdigit() or last_char == '-'):
        return True

    return False


def _merge_continuation_rows(rows, n_cols):
    """
    Many PDFs wrap cell text across multiple visual rows.  Merge them back:
    a new logical row begins when col-0 is non-empty; following rows whose
    col-0 is empty are continuation lines that belong to the previous row.
    """
    if not rows:
        return []

    merged, current = [], None
    for row in rows:
        padded = list(row) + [''] * max(0, n_cols - len(row))
        col0 = padded[0].strip() if padded else ''

        if col0:                                  # new logical row
            if current is not None:
                merged.append(current)
            current = padded[:]
        else:                                     # continuation of previous row
            if current is None:
                current = padded[:]
                continue
            for i, val in enumerate(padded):
                if val.strip() and i < len(current):
                    existing = current[i]
                    new_frag = val.strip()
                    sep = '' if _should_join_without_space(existing, new_frag) \
                          else (' ' if existing else '')
                    current[i] = existing + sep + new_frag

    if current is not None:
        merged.append(current)

    return merged


# ---------------------------------------------------------------------------
# Strategy 1 – pdfplumber table extraction
# ---------------------------------------------------------------------------

def _scan_for_header(cleaned_rows, n_cols):
    """
    Scan rows accumulating their content until _detect_cols() succeeds.
    Returns (col_map, data_start_index, header_strings).

    Handles word-wrapped headers where "EmployeeNumber" is split across
    multiple rows as "Employe" / "eNumbe" / "r" in col-0.

    Continues accumulating even after a partial match until emp_num is
    found or adding more rows stops improving the mapping (max 10 rows).
    """
    best_map    = None
    best_start  = 0
    best_header = None
    accumulated = None

    for idx, row in enumerate(cleaned_rows):
        if idx >= 10:
            break
        if accumulated is None:
            accumulated = row[:]
        else:
            for i, val in enumerate(row):
                if val.strip() and i < len(accumulated):
                    sep = ' ' if accumulated[i] else ''
                    accumulated[i] = accumulated[i] + sep + val.strip()

        new_map = _detect_cols(accumulated)
        if new_map is not None:
            if best_map is None or len(new_map) > len(best_map):
                best_map    = new_map
                best_start  = idx + 1
                best_header = accumulated[:]
            if 'emp_num' in new_map:
                break

    return best_map, best_start, best_header


def _extract_via_tables(page, prev_col_map):
    col_map    = prev_col_map
    employees  = []
    pg_header  = None
    pg_rows    = []

    # Try each strategy; stop at the first one that returns any rows
    raw_rows = []
    for strat in _TABLE_STRATEGIES:
        try:
            tbls = page.extract_tables(table_settings=strat)
            if tbls:
                best = max(tbls, key=lambda t: max((len(r) for r in t), default=0))
                if best:
                    raw_rows = best
                    break
        except Exception:
            continue

    if not raw_rows:
        return employees, col_map, pg_header, pg_rows

    n_cols = max((len(r) for r in raw_rows), default=0)
    cleaned_rows = [
        [_clean(c) for c in (list(r) + [''] * max(0, n_cols - len(r)))]
        for r in raw_rows
    ]

    # Phase 1: locate header (accumulate rows if it is word-wrapped)
    data_start = 0
    if col_map is None:
        col_map, data_start, pg_header = _scan_for_header(cleaned_rows, n_cols)
    if col_map is None:
        return employees, col_map, pg_header, pg_rows

    # Phase 2: merge continuation data rows then extract employees
    data_rows = [list(r) for r in cleaned_rows[data_start:]]
    merged    = _merge_continuation_rows(data_rows, n_cols)
    pg_rows   = merged  # capture for column-mapping feature

    for row in merged:
        if _detect_cols(row) is not None:
            continue  # skip repeated headers
        emp = _row_to_employee(row, col_map)
        if _is_empty(emp):
            continue
        combo = ' '.join(v.lower() for v in [emp['emp_num'], emp['last_name']])
        if any(kw in combo for kw in ('total', 'subtotal', 'grand total')):
            continue
        employees.append(emp)

    return employees, col_map, pg_header, pg_rows


# ---------------------------------------------------------------------------
# Strategy 2 – Word-position layout reconstruction
# ---------------------------------------------------------------------------

def _words_to_visual_rows(page):
    """Extract words and group them by their y-position into visual rows."""
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
        key = matched if matched is not None else round(w['top'])
        buckets.setdefault(key, []).append(w)

    return [sorted(buckets[y], key=lambda w: w['x0']) for y in sorted(buckets)]


def _detect_col_boundaries(visual_rows):
    """Find x-positions that appear frequently → likely column starts."""
    from collections import Counter
    x_counts = Counter()
    for row in visual_rows:
        for w in row:
            bucket = round(w['x0'] / _X_TOL) * _X_TOL
            x_counts[bucket] += 1
    min_freq = max(2, len(visual_rows) * 0.10)
    return sorted(x for x, cnt in x_counts.items() if cnt >= min_freq)


def _assign_to_cols(visual_rows, boundaries):
    """Convert word rows to text-cell rows using column boundaries."""
    if not boundaries:
        return [[' '.join(w['text'] for w in row)] for row in visual_rows]

    result = []
    for row in visual_rows:
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


def _extract_via_word_layout(page, prev_col_map):
    col_map   = prev_col_map
    employees = []
    pg_header = None
    pg_rows   = []

    visual_rows = _words_to_visual_rows(page)
    if not visual_rows:
        return employees, col_map, pg_header, pg_rows

    boundaries = _detect_col_boundaries(visual_rows)
    grid       = _assign_to_cols(visual_rows, boundaries)
    n_cols     = len(boundaries) if boundaries else 1

    # Phase 1: locate header (accumulate rows if it is word-wrapped)
    data_start = 0
    if col_map is None:
        col_map, data_start, pg_header = _scan_for_header(grid, n_cols)
    if col_map is None:
        return employees, col_map, pg_header, pg_rows

    # Phase 2: merge continuation data rows then extract employees
    data_rows = [list(r) for r in grid[data_start:]]
    merged    = _merge_continuation_rows(data_rows, n_cols)
    pg_rows   = merged  # capture for column-mapping feature

    for row in merged:
        if _detect_cols(row) is not None:
            continue  # skip repeated headers
        emp = _row_to_employee(row, col_map)
        if _is_empty(emp):
            continue
        combo = ' '.join(v.lower() for v in [emp['emp_num'], emp['last_name']])
        if any(kw in combo for kw in ('total', 'subtotal', 'grand total')):
            continue
        employees.append(emp)

    return employees, col_map, pg_header, pg_rows


# ---------------------------------------------------------------------------
# Strategy 3 – Raw text fallback
# ---------------------------------------------------------------------------

def _parse_raw_text(raw_text):
    """Split by large gaps / tab separation when no table structure is found."""
    col_map   = None
    employees = []

    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r'\t|  {2,}', line)
        if len(parts) >= 2:
            new_map = _detect_cols(parts)
            if new_map is not None and col_map is None:
                col_map = new_map
                continue
        if col_map is None:
            continue
        emp = _row_to_employee(parts, col_map)
        if not _is_empty(emp):
            employees.append(emp)

    return employees, col_map


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_faculty_pdf(file_bytes):
    """Extract employee records from a PDF faculty list."""
    employees       = []
    warnings        = []
    raw_pages       = []
    col_map         = None
    all_raw_rows    = []
    captured_header = None

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            raw_pages.append(page.extract_text() or '')

            # Strategy 1 – table extraction with continuation-row merging
            page_emps, col_map, pg_hdr, pg_rows = _extract_via_tables(page, col_map)
            if pg_hdr is not None and captured_header is None:
                captured_header = pg_hdr
            all_raw_rows.extend(pg_rows)
            if page_emps:
                employees.extend(page_emps)
                continue

            # Strategy 2 – word-position reconstruction
            page_emps, col_map, pg_hdr, pg_rows = _extract_via_word_layout(page, col_map)
            if pg_hdr is not None and captured_header is None:
                captured_header = pg_hdr
            all_raw_rows.extend(pg_rows)
            if page_emps:
                employees.extend(page_emps)

    raw_text = '\n\n--- PAGE ---\n\n'.join(raw_pages)

    # Strategy 3 – raw text fallback
    if not employees and raw_text.strip():
        employees, fallback_map = _parse_raw_text(raw_text)
        if fallback_map and col_map is None:
            col_map = fallback_map

    if not employees:
        if not raw_text.strip():
            warnings.append(
                "No text could be extracted from the PDF. "
                "It may be a scanned image. Please use a text-based PDF."
            )
        else:
            warnings.append(
                "No employees could be extracted. "
                "Make sure the PDF contains a table with column headers such as "
                "'Employee Number', 'Last Name', 'First Name', or 'Name'."
            )

    return {
        'employees':      employees,
        'confidence':     85 if employees else 0,
        'warnings':       warnings,
        'employee_count': len(employees),
        'raw_text':       raw_text,
        'raw_rows':       all_raw_rows,
        'raw_headers':    captured_header or [],
        'col_map':        col_map or {},
        'has_header':     bool(col_map),
    }
