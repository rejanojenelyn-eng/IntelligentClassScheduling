"""
docx_faculty_parser.py
Extracts employee/faculty records from Word (.docx) files.
Returns { employees, confidence, warnings, employee_count }.
Each employee: emp_num, last_name, first_name, middle_name, email,
               contact, specialization, emp_type, status, designation
"""
import io
import re

_COL_KEYWORDS = {
    'emp_num': [
        'employeenumber', 'employeeno',
        'employee number', 'employee no.', 'employee no',
        'emp. number', 'emp number', 'emp. no.', 'emp. no',
        'emp no.', 'emp no', 'emp num', 'employee id',
        'faculty no.', 'faculty no', 'faculty number',
        'id no.', 'id no', 'id number', 'empno',
    ],
    'last_name':   ['lastname', 'last name', 'last_name', 'surname', 'family name'],
    'first_name':  ['firstname', 'first name', 'first_name', 'given name', 'fname'],
    'middle_name': ['middlename', 'middle name', 'middle_name', 'middle initial', 'm.i.', 'mi'],
    'full_name':   ['employee name', 'faculty name', 'full name', 'name of employee',
                    'name of faculty', 'instructor name', 'instructor', 'name'],
    'email':       ['emailaddress', 'email address', 'e-mail address', 'e-mail', 'email'],
    'contact':     ['contactnumber', 'contact number', 'contact no.', 'contact no',
                    'telephone number', 'telephone', 'mobile number', 'mobile',
                    'phone number', 'phone', 'contact'],
    'specialization': ['specialization', 'speciality', 'specialty',
                       'department', 'dept.', 'dept', 'college', 'program', 'field'],
    'emp_type':    ['employeetype', 'employee type', 'employment type',
                    'type of employment', 'appointment type', 'category', 'type'],
    'status':      ['employeestatus', 'employment status', 'emp. status',
                    'emp status', 'appointment status', 'status'],
    'designation': ['designation', 'academic rank', 'rank', 'position title',
                    'position', 'job title', 'title'],
}


# Word XML namespace prefix for the main wordprocessingml namespace
_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

# Regex that removes spaces before common English word-ending suffixes.
# These 2-5 char lowercase fragments appear after mid-word run splits caused by
# Word font/formatting changes (e.g. "Tempora ry" → "Temporary").
_RUN_SPLIT_RE = re.compile(
    r'([A-Za-z]) '
    r'(tion|sion|ment|ness|ary|ery|ory|ive|ful|ism|ist|ity|age|ogy|ics|'
    r'ies|ees|ers|ons|ings|'
    r'ry|ty|ny|gy|cy|dy|py|'
    r'nt|nd|ng|ct|pt|lt|st|xt|'
    r'ss|ff|ll|'
    r'al|el|'
    r'er|or|ar|'
    r'ed|en|'
    r'es|rs|ts|ns|ls|ds|'
    r'ee|oo'
    r')(?=[ \t,;.:()\-\/\']|$)',
    re.IGNORECASE,
)


def _fix_run_splits(text):
    """
    Remove spurious mid-word spaces left after DOCX run-split extraction,
    e.g. 'Tempora ry' → 'Temporary', 'Educa tion' → 'Education'.
    Applied repeatedly until stable.
    """
    for _ in range(6):
        prev = text
        text = _RUN_SPLIT_RE.sub(lambda m: m.group(1) + m.group(2), text)
        if text == prev:
            break
    return text


def _el_text(el):
    """
    Extract clean text from a DOCX table cell element.

    Strategy
    --------
    1. Walk paragraph-by-paragraph so we can join paragraphs with a single
       space rather than smashing everything together.
    2. Within each paragraph, walk run-by-run.  If a run ends with a
       trailing space *and* the very next run in the same paragraph starts
       with a lowercase letter *and* the run (stripped) ends with a letter,
       the trailing space is a Word formatting artifact — strip it.
       (This handles "Designe " + "es" → "Designees".)
    3. Apply _fix_run_splits() as a second pass for any 2-5 char suffix
       fragments that still slipped through.
    4. Fall back to the original flat extraction if no paragraphs are found.
    """
    W_P  = _W + 'p'
    W_R  = _W + 'r'
    W_T  = _W + 't'
    W_BR = _W + 'br'

    para_texts = []
    for p_el in el.iter(W_P):
        runs = []
        for r_el in p_el.iter(W_R):
            t = ''
            for ch in r_el:
                if ch.tag == W_T:
                    t += (ch.text or '')
                elif ch.tag == W_BR:
                    t += '\n'
            runs.append(t)

        buf = []
        for i, run in enumerate(runs):
            if i < len(runs) - 1:
                next_run = runs[i + 1]
                stripped = run.rstrip(' ')
                # Strip trailing space when it's a run-formatting artifact:
                # current run ends with a letter, next starts with lowercase
                if (run.endswith(' ')
                        and next_run and next_run[0].islower()
                        and stripped and stripped[-1].isalpha()):
                    run = stripped
            buf.append(run)

        para_text = ''.join(buf)
        para_text = re.sub(r'[^\S\n]+', ' ', para_text).strip()
        if para_text:
            para_texts.append(para_text)

    if not para_texts:
        # Fallback: original flat extraction
        parts = []
        for n in el.iter():
            if n.tag.endswith('}t'):
                parts.append(n.text or '')
            elif n.tag.endswith('}br'):
                parts.append('\n')
        raw = ''.join(parts)
    else:
        raw = ' '.join(para_texts)

    # Handle hard line-break (\n) continuation patterns (unchanged from original)
    raw = re.sub(r' +\n', '\n', raw)
    raw = re.sub(r'([A-Za-z])\n([a-z])', r'\1\2', raw)           # lowercase suffix
    raw = re.sub(r'([A-Z])\n([A-Z]{1,2})(?=\s|,|$)', r'\1\2', raw)  # short ALL-CAPS
    raw = re.sub(r'(\d)\n(\d)', r'\1\2', raw)                    # digit continuation
    raw = re.sub(r'\n', ' ', raw)
    raw = re.sub(r'\s+', ' ', raw).strip()

    # Second-pass: suffix-based cleanup for any remaining artifacts
    return _fix_run_splits(raw)


def _detect_cols(headers):
    mapping = {}
    for i, cell in enumerate(headers):
        cell_l  = re.sub(r'\s+', ' ', str(cell or '')).lower().strip()
        cell_ns = re.sub(r'\s+', '', cell_l)  # spaces removed
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
    has_name = ('last_name' in mapping or 'first_name' in mapping or 'full_name' in mapping)
    return mapping if ('emp_num' in mapping or has_name) else None


def parse_faculty_docx(file_bytes):
    from docx import Document
    from docx.oxml.ns import qn

    doc          = Document(io.BytesIO(file_bytes))
    employees    = []
    warnings     = []
    col_map      = None
    raw_header   = None
    all_raw_rows = []

    for element in doc.element.body:
        tag = element.tag.split('}')[-1] if '}' in element.tag else element.tag
        if tag != 'tbl':
            continue

        for row_el in element.findall('.//' + qn('w:tr')):
            cells = row_el.findall('.//' + qn('w:tc'))
            row   = [_el_text(c) for c in cells]

            new_map = _detect_cols(row)
            if new_map is not None:
                col_map = new_map
                if raw_header is None:
                    raw_header = row[:]  # capture header strings
                continue
            if col_map is None:
                continue

            all_raw_rows.append(row[:])  # capture raw data row

            def gcol(field, _r=row, _m=col_map):
                idx = _m.get(field)
                return _r[idx].strip() if idx is not None and idx < len(_r) else ''

            emp_num    = gcol('emp_num')
            last_name  = gcol('last_name')
            first_name = gcol('first_name')

            # Handle combined "Name" column  e.g. "DELA CRUZ, Juan"
            if 'full_name' in col_map:
                full = gcol('full_name')
                if full and ',' in full:
                    parts = full.split(',', 1)
                    if not last_name:  last_name  = parts[0].strip()
                    if not first_name: first_name = parts[1].strip()
                elif full and not last_name:
                    last_name = full

            if not emp_num and not last_name and not first_name:
                continue

            employees.append({
                'emp_num':        emp_num,
                'last_name':      last_name,
                'first_name':     first_name,
                'middle_name':    gcol('middle_name'),
                'email':          gcol('email'),
                'contact':        gcol('contact'),
                'specialization': gcol('specialization'),
                'emp_type':       gcol('emp_type'),
                'status':         gcol('status'),
                'designation':    gcol('designation'),
            })

    if not employees:
        warnings.append(
            "No employees could be extracted. Make sure the document contains a table "
            "with an 'Employee Number' or 'Last Name' column header."
        )

    return {
        'employees':      employees,
        'confidence':     85 if employees else 0,
        'warnings':       warnings,
        'employee_count': len(employees),
        'raw_rows':       all_raw_rows,
        'raw_headers':    raw_header or [],
        'col_map':        col_map or {},
        'has_header':     bool(col_map),
    }
