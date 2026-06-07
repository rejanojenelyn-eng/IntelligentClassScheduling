import io
import re


def parse_curriculum_docx(file_bytes, override_col_map=None):
    """
    Parse a curriculum Word (.docx) file and extract subjects.
    Returns { subjects, confidence, warnings, subject_count, raw_text }.

    Handles:
    - Year/semester headings as body paragraphs  ("FIRST YEAR", "Second Semester")
    - Year/semester headings as merged rows INSIDE tables
    - Tables split across pages (continuation uses last known column mapping)
    - Repeated header rows on each page of a split table
    """
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(io.BytesIO(file_bytes))
    subjects = []
    warnings = []

    current_yl  = 1
    current_sem = 'A'

    _YL_WORDS = {
        'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5,
        '1st':   1, '2nd':    2, '3rd':    3, '4th':    4, '5th':    5,
    }

    # ── helpers ──────────────────────────────────────────────────────────────

    def _el_text(el):
        return ''.join(n.text or '' for n in el.iter() if n.tag.endswith('}t')).strip()

    def _parse_int(val):
        try:
            return int(float(str(val).strip()))
        except Exception:
            return 0

    def _col_idx(headers, *keywords):
        for i, h in enumerate(headers):
            hl = str(h).lower()
            for kw in keywords:
                if kw in hl:
                    return i
        return None

    def _detect_yl_and_sem(text):
        """
        Return (year_level_or_None, semester_or_None).

        Key rule: only derive semester from text when 'year' is absent,
        OR when an explicit 'X semester' phrase is present.
        This prevents 'SECOND YEAR' from being misread as semester B.
        """
        t = text.lower().strip()
        yl  = None
        sem = None

        # ── year level ──────────────────────────────────────────────────────
        for word, num in _YL_WORDS.items():
            if f'{word} year' in t:
                yl = num
                break
        if yl is None:
            m = re.search(r'(\d)[stndrh]* year', t)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 5:
                    yl = n

        # ── semester ─────────────────────────────────────────────────────────
        # Priority 1: explicit "X semester" phrases  (safe even when "year" present)
        if   re.search(r'summer\s+sem', t) or 'midyear' in t or 'mid-year' in t:
            sem = 'C'
        elif re.search(r'(second|2nd)\s+sem', t) or re.search(r'sem\w*\s+2\b', t):
            sem = 'B'
        elif re.search(r'(first|1st)\s+sem',  t) or re.search(r'sem\w*\s+1\b', t):
            sem = 'A'
        # Priority 2: standalone keyword only when "year" is NOT also in the text
        elif 'year' not in t:
            if   'summer' in t:                         sem = 'C'
            elif 'second' in t or '2nd' in t:           sem = 'B'
            elif 'first'  in t or '1st' in t:           sem = 'A'

        return yl, sem

    def _detect_header_cols(headers):
        """Return column-index list if row is a subject-table header, else None."""
        sc_i = _col_idx(headers,
                        'subject code', 'course code', 'subj code', 'sub code',
                        'course no', 'course num', 'subj.', 'course #', 'subjectcode')
        # Fallback: bare 'code' only when a description-like or units column is also present
        if sc_i is None:
            code_i  = _col_idx(headers, 'code')
            anchor  = (_col_idx(headers, 'title', 'description', 'descriptive') is not None
                       or _col_idx(headers, 'units', 'credited', 'credit') is not None)
            if code_i is not None and anchor:
                sc_i = code_i
        if sc_i is None:
            return None
        return [
            sc_i,
            _col_idx(headers, 'descriptive title', 'description',
                     'subject name', 'course title', 'title',
                     'subject description', 'course description', 'subject title'),  # sn
            _col_idx(headers, 'prereq', 'pre-req', 'prerequisite', 'pre req',
                     'pre-requisite', 'co-requisite pre'),                            # pre
            _col_idx(headers, 'co-req', 'coreq', 'co req', 'corequisite',
                     'co-requisite'),                                                  # co
            _col_idx(headers, 'lec hrs', 'lec hours', 'lecture hrs',
                     'lecture hours', 'lec', 'lecture'),                              # lc
            _col_idx(headers, 'lab hrs', 'lab hours', 'laboratory hrs',
                     'laboratory hours', 'lab', 'laboratory'),                        # lb
            _col_idx(headers, 'credited units', 'credit units',
                     'units', 'credited', 'cu', 'credit'),                            # u
            _col_idx(headers, 'tuition hrs', 'tuition hours', 'tuition', 'tth'),     # th
        ]

    _SKIP_SC = {
        'total', 'total units', 'no subject',
        'subject code', 'course code', 'code', 'subjectcode',
        'subj code', 'sub code',
    }

    # Remembered column mapping so page-break table continuations work
    if override_col_map is not None:
        _last_cols = [
            override_col_map.get('sc'),
            override_col_map.get('sn'),
            override_col_map.get('pre'),
            override_col_map.get('co'),
            override_col_map.get('lc'),
            override_col_map.get('lb'),
            override_col_map.get('u'),
            override_col_map.get('th'),
        ]
    else:
        _last_cols = [None] * 8   # [sc_i, sn_i, pre_i, co_i, lc_i, lb_i, u_i, th_i]

    # ── walk body elements in document order ─────────────────────────────────

    for element in doc.element.body:
        tag = element.tag.split('}')[-1] if '}' in element.tag else element.tag

        # ── paragraph ────────────────────────────────────────────────────────
        if tag == 'p':
            text = _el_text(element)
            yl, sem = _detect_yl_and_sem(text)
            if yl:  current_yl  = yl
            if sem: current_sem = sem

        # ── table ────────────────────────────────────────────────────────────
        elif tag == 'tbl':
            tbl_rows = element.findall('.//' + qn('w:tr'))
            if not tbl_rows:
                continue

            # Start with last known column mapping (handles page-break continuations)
            sc_i, sn_i, pre_i, co_i, lc_i, lb_i, u_i, th_i = _last_cols

            for row_el in tbl_rows:
                cells = row_el.findall('.//' + qn('w:tc'))
                if not cells:
                    continue

                def gcell(idx, _cells=cells):
                    if idx is None or idx >= len(_cells): return ''
                    return _el_text(_cells[idx])

                headers = [_el_text(c) for c in cells]

                # ── 1. Is this a column header row? ──────────────────────────
                new_cols = _detect_header_cols(headers)
                if new_cols is not None:
                    if override_col_map is None:
                        sc_i, sn_i, pre_i, co_i, lc_i, lb_i, u_i, th_i = new_cols
                        _last_cols[:] = new_cols
                    continue  # header row — skip to next row

                # ── 2. Is this a year/semester heading row? ───────────────────
                row_text = ' '.join(headers)
                yl, sem = _detect_yl_and_sem(row_text)
                if yl or sem:
                    sc_val = gcell(sc_i).lower().strip() if sc_i is not None else ''
                    # Decide whether this row is really a heading (not subject data)
                    non_empty = [h for h in headers if h.strip()]
                    is_heading = (
                        not sc_val                                         # empty sc cell
                        or sc_val in _SKIP_SC
                        or sc_val.startswith('total')
                        or len(non_empty) <= 2                             # merged heading
                        or any(kw in sc_val for kw in
                               ('year', 'semester', 'summer', 'midyear'))
                    )
                    if is_heading:
                        if yl:  current_yl  = yl
                        if sem: current_sem = sem
                        continue

                # ── 3. No column mapping yet — skip ──────────────────────────
                if sc_i is None:
                    continue

                # ── 4. Data row ───────────────────────────────────────────────
                sc = gcell(sc_i).strip()
                if not sc:
                    continue
                sc_l = sc.lower()
                if sc_l in _SKIP_SC or sc_l.startswith('total'):
                    continue

                subjects.append({
                    'sc':  sc,
                    'sn':  gcell(sn_i),
                    'pre': gcell(pre_i),
                    'co':  gcell(co_i),
                    'lc':  _parse_int(gcell(lc_i)),
                    'lb':  _parse_int(gcell(lb_i)),
                    'u':   _parse_int(gcell(u_i)),
                    'th':  _parse_int(gcell(th_i)),
                    'yl':  current_yl,
                    'sem': current_sem,
                })

    if not subjects:
        warnings.append(
            "No subjects could be extracted. Make sure the document uses standard "
            "table formatting with a 'Subject Code' column header."
        )
        confidence = 0
    else:
        confidence = 85

    return {
        'subjects':      subjects,
        'confidence':    confidence,
        'warnings':      warnings,
        'subject_count': len(subjects),
        'raw_text':      '',
    }
