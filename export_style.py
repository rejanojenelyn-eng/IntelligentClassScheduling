"""House style for every exported report (PDF / Word / Excel): Arial, never smaller
than 12 pt, and the letterhead logo centered.

Each generator keeps its own layout code; its finished document passes through one
of these before it is written, so the rule holds for every report in one place:

    rl_apply(flowables)   ReportLab story, before doc.build()   (via app._rl_doc)
    docx_apply(doc)       python-docx Document, before save      (via app._docx_apply_orientation)
    xlsx_apply(wb)        openpyxl Workbook, before save         (app._xlsx_finalize)

PDF needs the font file itself (Word/Excel only store the name "Arial"). The real
Arial is used when the machine has it (Windows / msttcorefonts); otherwise the
bundled Arimo (static/fonts/arimo, SIL OFL 1.1) — Arial's metric-compatible twin —
is registered under the same "Arial" name, so layouts are identical either way.
"""
import copy
import os

FONT = 'Arial'
MIN_PT = 12
# The letterhead logo, set by the app. A document that has no picture of its own gets
# it centered at the top, so every export carries the same letterhead.
LOGO_PATH = None


def _logo_ok():
    return bool(LOGO_PATH) and os.path.isfile(LOGO_PATH)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ARIMO = os.path.join(_HERE, 'static', 'fonts', 'arimo')
_CANDIDATES = {
    # face: [system Arial paths ..., bundled Arimo]
    '':           ['C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/msttcorefonts/Arial.ttf',
                   os.path.join(_ARIMO, 'Arimo-Regular.ttf')],
    '-Bold':      ['C:/Windows/Fonts/arialbd.ttf', '/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf',
                   os.path.join(_ARIMO, 'Arimo-Bold.ttf')],
    '-Italic':    ['C:/Windows/Fonts/ariali.ttf', '/usr/share/fonts/truetype/msttcorefonts/Arial_Italic.ttf',
                   os.path.join(_ARIMO, 'Arimo-Italic.ttf')],
    '-BoldItalic': ['C:/Windows/Fonts/arialbi.ttf', '/usr/share/fonts/truetype/msttcorefonts/Arial_Bold_Italic.ttf',
                    os.path.join(_ARIMO, 'Arimo-BoldItalic.ttf')],
}

# ReportLab base-14 names the generators use -> the Arial face that replaces them.
_RL_FACE = {
    'helvetica': '', 'helvetica-bold': '-Bold', 'helvetica-oblique': '-Italic',
    'helvetica-boldoblique': '-BoldItalic',
    'times-roman': '', 'times-bold': '-Bold', 'times-italic': '-Italic', 'times-bolditalic': '-BoldItalic',
    'courier': '', 'courier-bold': '-Bold', 'courier-oblique': '-Italic', 'courier-boldoblique': '-BoldItalic',
}

_registered = False


def register_pdf_fonts():
    """Register Arial (or Arimo as "Arial") with ReportLab once. Returns True when done."""
    global _registered
    if _registered:
        return True
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.fonts import addMapping
    for face, paths in _CANDIDATES.items():
        path = next((p for p in paths if os.path.isfile(p)), None)
        if not path:
            return False
        pdfmetrics.registerFont(TTFont(FONT + face, path))
    # <b>/<i> inside a Paragraph resolve through the family mapping.
    addMapping(FONT, 0, 0, FONT)
    addMapping(FONT, 1, 0, FONT + '-Bold')
    addMapping(FONT, 0, 1, FONT + '-Italic')
    addMapping(FONT, 1, 1, FONT + '-BoldItalic')
    _registered = True
    return True


def _rl_font(name):
    name = name or 'Helvetica'
    if name.startswith(FONT):
        return name
    face = _RL_FACE.get(name.lower())
    if face is None:                       # an unknown font: keep its weight if we can tell
        low = name.lower()
        face = ('-BoldItalic' if 'bold' in low and ('italic' in low or 'oblique' in low)
                else '-Bold' if 'bold' in low else '-Italic' if ('italic' in low or 'oblique' in low) else '')
    return FONT + face


# ── PDF (ReportLab) ─────────────────────────────────────────────────────────────

def _rl_style(st, cache):
    """A copy of a ParagraphStyle in Arial, at least MIN_PT, leading scaled with it.
    Returns (new style, growth ratio of its line height)."""
    key = id(st)
    if key in cache:
        return cache[key]
    new = copy.copy(st)
    old_size = float(getattr(st, 'fontSize', 10) or 10)
    old_lead = float(getattr(st, 'leading', old_size * 1.2) or old_size * 1.2)
    size = max(MIN_PT, old_size)
    new.fontName = _rl_font(getattr(st, 'fontName', 'Helvetica'))
    new.fontSize = size
    new.leading = max(old_lead * size / old_size, size * 1.15)
    if getattr(st, 'bulletFontName', None):
        new.bulletFontName = _rl_font(st.bulletFontName)
        new.bulletFontSize = max(MIN_PT, float(getattr(st, 'bulletFontSize', size) or size))
    cache[key] = (new, new.leading / old_lead if old_lead else 1.0)
    return cache[key]


def _rl_walk(obj, cache):
    """Rebuild obj (a flowable, list or cell value) in the house style.
    Returns (new obj, largest line-height growth inside it)."""
    from reportlab.platypus import Paragraph, Table
    from reportlab.platypus.flowables import KeepTogether
    if isinstance(obj, (list, tuple)):
        out, grow = [], 1.0
        for x in obj:
            nx, g = _rl_walk(x, cache)
            out.append(nx); grow = max(grow, g)
        return (out if isinstance(obj, list) else tuple(out)), grow
    if isinstance(obj, Paragraph):
        st, g = _rl_style(obj.style, cache)
        p = Paragraph(obj.text, st, bulletText=getattr(obj, 'bulletText', None))
        for a in ('hAlign', 'vAlign', 'keepWithNext'):
            if a in vars(obj):
                setattr(p, a, getattr(obj, a))
        return p, g
    if isinstance(obj, Table):
        _rl_table(obj, cache)
        return obj, 1.0
    if isinstance(obj, KeepTogether):
        obj._content, _ = _rl_walk(list(obj._content), cache)
        if type(obj) is KeepTogether:
            obj.__class__ = _keep_class()
        return obj, 1.0
    return obj, 1.0


_KEEP_TALL = 300     # pt: a kept-together group taller than this may start on the current page
_KEEP_ROOM = 150     # pt: ... when at least this much of the page is left
_KeepCls = None


def _keep_class():
    """KeepTogether that lets a TALL group (a long table / calendar now set in 12 pt)
    start on the current page and continue on the next, instead of jumping whole to a
    new page and leaving one that holds only the letterhead. Short groups (a heading
    with a few rows) still move together, as their generators intended."""
    global _KeepCls
    if _KeepCls is None:
        from reportlab.platypus.flowables import KeepTogether

        class _HouseKeepTogether(KeepTogether):
            def split(self, aW, aH):
                if getattr(self, '_wrapInfo', None) != (aW, aH):
                    self.wrap(aW, aH)
                if self._H > aH and self._H > _KEEP_TALL and aH >= _KEEP_ROOM:
                    return list(self._content)
                return KeepTogether.split(self, aW, aH)
        _KeepCls = _HouseKeepTogether
    return _KeepCls


def _rl_table(t, cache):
    grow = 1.0
    for r, row in enumerate(t._cellvalues):
        for c, val in enumerate(row):
            nv, g = _rl_walk(val, cache)
            row[c] = nv; grow = max(grow, g)
    for row in t._cellStyles:
        for cs in row:
            old_size = float(cs.fontsize or 10)
            old_lead = float(cs.leading or old_size * 1.2)
            size = max(MIN_PT, old_size)
            cs.fontname = _rl_font(cs.fontname)
            cs.fontsize = size
            cs.leading = max(old_lead * size / old_size, size * 1.15)
            if old_lead:
                grow = max(grow, cs.leading / old_lead)
    # Fixed row heights (calendar grids) grow with their text so nothing spills out:
    # only the rows a too-small cell spans get taller (like Word's auto-height rows), so
    # the grid stays compact. The table can then break across pages between rows.
    if grow > 1.0 and t._argH and any(h is not None for h in t._argH):
        before = sum(h for h in t._argH if h is not None)
        if not _rl_grow_fixed_rows(t):
            t._argH = [None if h is None else h * grow for h in t._argH]
        t._rowHeights = list(t._argH)
        if sum(h for h in t._argH if h is not None) > before + 0.5 and not t.repeatRows:
            t.repeatRows = 1                       # the day header again on the next page


def _rl_grow_fixed_rows(t):
    """Grow t's fixed row heights so each cell's content fits the box it spans (its
    column widths are final by now). Returns False when it can't be measured."""
    from reportlab.platypus import Flowable
    W, H = list(t._argW or []), list(t._argH or [])
    nr, nc = len(t._cellvalues), (len(t._cellvalues[0]) if t._cellvalues else 0)
    if len(W) != nc or len(H) != nr or any(not isinstance(w, (int, float)) for w in W):
        return False
    norm = lambda i, n: i + n if i < 0 else i
    box = {}                                   # (r, c) -> (c0, r0, c1, r1) of its span
    for cmd in getattr(t, '_spanCmds', []):
        (c0, r0), (c1, r1) = cmd[1], cmd[2]
        c0, c1, r0, r1 = norm(c0, nc), norm(c1, nc), norm(r0, nr), norm(r1, nr)
        box[(min(r0, r1), min(c0, c1))] = (min(c0, c1), min(r0, r1), max(c0, c1), max(r0, r1))
    cells = []                                 # (rows spanned, r0, r1, needed content height)
    for r, row in enumerate(t._cellvalues):
        for c, val in enumerate(row):
            if val in (None, '') or (isinstance(val, (list, tuple)) and not val):
                continue
            c0, r0, c1, r1 = box.get((r, c), (c, r, c, r))
            if any(h is None for h in H[r0:r1 + 1]):
                continue
            cs = t._cellStyles[r][c]
            width = sum(W[c0:c1 + 1]) - (cs.leftPadding or 0) - (cs.rightPadding or 0)
            if width <= 0:
                continue
            needed = (cs.topPadding or 0) + (cs.bottomPadding or 0)
            for it in (val if isinstance(val, (list, tuple)) else [val]):
                if isinstance(it, Flowable):
                    needed += it.wrap(width, 1e6)[1]
                else:
                    needed += len(str(it).splitlines() or ['']) * (cs.leading or cs.fontsize * 1.2)
            cells.append((r1 - r0, r0, r1, needed))
    # Single rows first, then wider spans: a span only adds what its rows still lack,
    # spread over them in proportion to their height.
    for _ in range(2):
        for _, r0, r1, needed in sorted(cells):
            have = sum(H[r0:r1 + 1])
            if needed > have + 0.5:
                extra = needed - have
                for i in range(r0, r1 + 1):
                    H[i] += extra * (H[i] / have if have else 1.0 / (r1 - r0 + 1))
    t._argH = H
    return True


def rl_apply(flowables):
    """Restyle a ReportLab story in place (Arial, >= MIN_PT). No-op if the font can't load."""
    if not register_pdf_fonts():
        return flowables
    cache, out = {}, []
    for f in flowables:
        nf, _ = _rl_walk(f, cache)
        out.append(nf)
    # No letterhead logo in this report: put it, centered, above everything.
    from reportlab.platypus import Image as _RLImage
    if _logo_ok() and not any(isinstance(f, _RLImage) for f in out[:6]):
        from reportlab.lib.units import cm
        logo = _RLImage(LOGO_PATH, width=1.4 * cm, height=1.4 * cm)
        logo.hAlign = 'CENTER'
        out.insert(0, logo)
    for f in out:                                  # any letterhead image: centered
        if isinstance(f, _RLImage):
            f.hAlign = 'CENTER'
    flowables[:] = out
    return flowables


# ── Word (python-docx) ──────────────────────────────────────────────────────────

def _docx_font(font, ensure_size=True):
    from docx.shared import Pt
    from docx.oxml.ns import qn
    font.name = FONT
    rpr = font.element.get_or_add_rPr() if hasattr(font, 'element') else None
    if rpr is not None:
        rfonts = rpr.find(qn('w:rFonts'))
        if rfonts is None:
            rfonts = rpr.makeelement(qn('w:rFonts'), {})
            rpr.insert(0, rfonts)
        for a in ('w:ascii', 'w:hAnsi', 'w:cs', 'w:eastAsia'):
            rfonts.set(qn(a), FONT)
    if ensure_size and (font.size is None or font.size.pt < MIN_PT):
        font.size = Pt(MIN_PT)


def _docx_paragraphs(container):
    for p in container.paragraphs:
        yield p
    for t in getattr(container, 'tables', []):
        for row in t.rows:
            for cell in row.cells:
                yield from _docx_paragraphs(cell)


def docx_apply(doc):
    """Every style and run of a Document in Arial, >= MIN_PT (body, tables, headers/footers)."""
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.table import WD_ROW_HEIGHT_RULE
    for st in doc.styles:
        if st.type in (WD_STYLE_TYPE.PARAGRAPH, WD_STYLE_TYPE.CHARACTER, WD_STYLE_TYPE.TABLE):
            try:
                _docx_font(st.font, ensure_size=st.type == WD_STYLE_TYPE.PARAGRAPH)
            except Exception:
                pass
    parts = [doc]
    for sec in doc.sections:
        for hf in (sec.header, sec.footer, sec.first_page_header, sec.first_page_footer):
            try:
                if not hf.is_linked_to_previous:
                    parts.append(hf)
            except Exception:
                pass
    seen = set()
    for part in parts:
        for p in _docx_paragraphs(part):
            if p._p in seen:              # merged cells repeat the same paragraph
                continue
            seen.add(p._p)                # (the element itself: keeps it alive, so no id reuse)
            for run in p.runs:
                _docx_font(run.font)
        for t in getattr(part, 'tables', []):
            _docx_rows_grow(t, WD_ROW_HEIGHT_RULE)
    _docx_logo(doc)
    return doc


def _docx_logo(doc):
    """No picture in the document: add the logo, centered, as its first paragraph.
    A picture already alone in its paragraph (the letterhead) is centered."""
    from docx.oxml.ns import qn
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    body = doc.element.body
    if next(body.iter(qn('w:drawing')), None) is not None:
        for p in doc.paragraphs[:6]:
            if p._p.find('.//' + qn('w:drawing')) is not None and not p.text.strip():
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        return
    if not _logo_ok():
        return
    from docx.shared import Cm
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(LOGO_PATH, width=Cm(1.6))
    body.remove(p._p)
    body.insert(0, p._p)


def _docx_rows_grow(table, rule):
    """An 'exact' row height can't grow with bigger text — let it (at least the same)."""
    for row in table.rows:
        try:
            if row.height_rule == rule.EXACTLY:
                row.height_rule = rule.AT_LEAST
        except Exception:
            pass
        for cell in row.cells:
            for t in cell.tables:
                _docx_rows_grow(t, rule)


# ── Excel (openpyxl) ────────────────────────────────────────────────────────────

def _xl_col_px(ws, idx):
    from openpyxl.utils import get_column_letter
    dim = ws.column_dimensions.get(get_column_letter(idx))
    w = dim.width if (dim is not None and dim.width) else 8.43
    if dim is not None and dim.hidden:
        return 0
    return int(w * 7 + 5)


def _xl_center_images(ws):
    """Center each picture horizontally over the sheet's used columns (same row)."""
    from openpyxl.drawing.spreadsheet_drawing import OneCellAnchor, AnchorMarker
    from openpyxl.drawing.xdr import XDRPositiveSize2D
    from openpyxl.utils.units import pixels_to_EMU
    ncols = max(ws.max_column or 1, 1)
    widths = [_xl_col_px(ws, i) for i in range(1, ncols + 1)]
    total = sum(widths)
    for img in getattr(ws, '_images', []):
        anchor = img.anchor
        if isinstance(anchor, str):
            from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
            col, row = coordinate_from_string(anchor)
            row0, rowoff = row - 1, 0
        else:
            row0, rowoff = anchor._from.row, anchor._from.rowOff
        w, h = int(img.width), int(img.height)
        x = max(0, (total - w) // 2)
        col0 = 0
        while col0 < ncols - 1 and x >= widths[col0]:
            x -= widths[col0]; col0 += 1
        img.anchor = OneCellAnchor(
            _from=AnchorMarker(col=col0, colOff=pixels_to_EMU(x), row=row0, rowOff=rowoff),
            ext=XDRPositiveSize2D(pixels_to_EMU(w), pixels_to_EMU(h)))


def xlsx_apply(wb):
    """Every cell of a Workbook in Arial, >= MIN_PT; logos centered; columns/rows grown to fit."""
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    import warnings
    try:                                           # the workbook's default cell font, too
        normal = next(st for st in wb._named_styles if st.name == 'Normal')
        normal.font = Font(name=FONT, sz=MIN_PT)
    except Exception:
        pass
    for ws in wb.worksheets:
        grow_col, grow_row = {}, {}
        for row in ws.iter_rows():
            for cell in row:
                f = cell.font
                old = float(f.sz or 11) if f is not None else 11.0
                size = max(MIN_PT, old)
                if f is None or f.name != FONT or old < MIN_PT:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')      # Font.copy is "deprecated" but exact
                        cell.font = f.copy(name=FONT, sz=size) if f is not None else Font(name=FONT, sz=size)
                if cell.value not in (None, '') and size > old:
                    g = size / old
                    grow_col[cell.column] = max(grow_col.get(cell.column, 1.0), g)
                    grow_row[cell.row] = max(grow_row.get(cell.row, 1.0), g)
        # Text got bigger: widen / heighten the columns and fixed-height rows that hold it.
        for c, g in grow_col.items():
            dim = ws.column_dimensions[get_column_letter(c)]
            if dim.width:
                dim.width = round(dim.width * g, 2)
        for r, g in grow_row.items():
            rd = ws.row_dimensions.get(r)
            if rd is not None and rd.height:
                rd.height = round(rd.height * g, 2)
        _xl_fit_wrapped_rows(ws)
        if not getattr(ws, '_images', None) and _logo_ok() and ws.max_row > 1:
            _xl_prepend_logo(ws)
        _xl_center_images(ws)
    return wb


def _xl_fit_wrapped_rows(ws):
    """A fixed-height row whose wrapped text now needs more lines (bigger font) grows to
    show them. Estimated from Arial's average glyph width — Excel can't measure for us."""
    import math
    from openpyxl.utils import get_column_letter
    span = {}                                      # top-left (row, col) -> (first col, last col, rows)
    for rng in ws.merged_cells.ranges:
        span[(rng.min_row, rng.min_col)] = (rng.min_col, rng.max_col, rng.max_row - rng.min_row + 1)
    need = {}
    for row in ws.iter_rows():
        for cell in row:
            v = cell.value
            if v in (None, ''):
                continue
            c0, c1, nrows = span.get((cell.row, cell.column), (cell.column, cell.column, 1))
            if nrows > 1:
                continue                           # a block over several rows: leave it
            size = float(cell.font.sz or MIN_PT)
            parts = str(v).split('\n')
            if cell.alignment and cell.alignment.wrap_text:
                px = sum(_xl_col_px(ws, c) for c in range(c0, c1 + 1)) - 6
                glyph = size * 0.55 * 96 / 72      # average Arial glyph, px
                lines = sum(max(1, math.ceil(len(part) * glyph / max(px, 1))) for part in parts)
            else:
                lines = len(parts)
            need[cell.row] = max(need.get(cell.row, 0), lines * size * 1.25 + 3)
    for r, h in need.items():
        rd = ws.row_dimensions[r]
        # A row without a height gets Excel's default (15 pt, sized for Calibri 11): too
        # short for 12 pt text — Excel doesn't re-measure a file it opens.
        if not rd.height or rd.height < h:
            rd.height = round(h, 2)


def _xl_prepend_logo(ws):
    """Shift the sheet down one row and put the logo in the new first row."""
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.worksheet.dimensions import RowDimension
    merged = [str(r) for r in ws.merged_cells.ranges]
    for rng in merged:
        ws.unmerge_cells(rng)
    heights = {r: d.height for r, d in ws.row_dimensions.items() if d.height}
    ws.insert_rows(1)
    from openpyxl.worksheet.cell_range import CellRange
    for rng in merged:
        cr = CellRange(rng); cr.shift(row_shift=1)
        ws.merge_cells(cr.coord)
    for r in list(ws.row_dimensions.keys()):
        ws.row_dimensions[r].height = None
    for r, h in heights.items():
        ws.row_dimensions[r + 1].height = h
    if ws.freeze_panes:
        cr = CellRange(f'{ws.freeze_panes}:{ws.freeze_panes}'); cr.shift(row_shift=1)
        ws.freeze_panes = cr.coord.split(':')[0]
    if ws.auto_filter and ws.auto_filter.ref:
        cr = CellRange(ws.auto_filter.ref); cr.shift(row_shift=1); ws.auto_filter.ref = cr.coord
    if ws.print_title_rows:
        try:
            a, b = [int(x) for x in str(ws.print_title_rows).replace('$', '').split(':')]
            ws.print_title_rows = f'{a + 1}:{b + 1}'
        except ValueError:
            pass
    img = XLImage(LOGO_PATH); img.width = img.height = 46
    ws.row_dimensions[1].height = 38
    ws.add_image(img, 'A1')
