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


def _el_text(el):
    parts = []
    for n in el.iter():
        if n.tag.endswith('}t'):
            parts.append(n.text or '')
        elif n.tag.endswith('}br'):
            parts.append('\n')   # hard line break inside cell
    raw = ''.join(parts)
    # PDF-to-DOCX converters often append a trailing space before <w:br/>.
    # Strip those spaces so the join rules below can see the real last char.
    # e.g. "Patrici \na"  → "Patrici\na"   "MONDRAG \nON" → "MONDRAG\nON"
    raw = re.sub(r' +\n', '\n', raw)
    # Join \n when preceded by word/email/phone chars (mid-token wrap artifact).
    # Covers: alphanumeric, period (email), hyphen (phone/hyphenated words), @
    # "Tempora\nry"→"Temporary"  "ALCANTA\nRA"→"ALCANTARA"  "0917-55\n5-0033"→"0917-555-0033"
    # "enrico.\nsuinan"→"enrico.suinan"  "Patrici\na Anne"→"Patricia Anne"
    raw = re.sub(r'([A-Za-z0-9.@\-])\n', r'\1', raw)
    raw = re.sub(r'\n', ' ', raw)   # any remaining \n → space
    return re.sub(r'\s+', ' ', raw).strip()


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
