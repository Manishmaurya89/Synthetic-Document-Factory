import os
import re
import json
import hashlib
import tempfile
import textwrap
import unicodedata
from dataclasses import dataclass, asdict
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import getAscentDescent, stringWidth
from reportlab.platypus import (
    BaseDocTemplate, Frame, FrameBreak, Image, KeepTogether,
    PageTemplate, Paragraph, Table, TableStyle,
)

try:
    import matplotlib
    matplotlib.use("Agg")  # no GUI window, render straight to PNG
    import matplotlib.pyplot as plt
except ImportError:  # charts fall back to a data table
    plt = None


FONTS = {
    # family: (regular, bold, italic). Built into every PDF reader, so no font files needed
    "Times": ("Times-Roman", "Times-Bold", "Times-Italic"),
    "Helvetica": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique"),
}
PAGE_SIZES = {"A4": A4, "Letter": LETTER}


@dataclass
class LayoutStyle:
    """How one rendering of a document looks. Rendering the same content with
    different styles gives many differently laid-out pages for free."""
    columns: int = 1
    font: str = "Times"
    font_size: float = 11.0
    margin: float = 72.0
    page_size: str = "A4"
    running_header: bool = True
    numbered_headings: bool = True
    justify: bool = True
    title_align: str = "center"
    page_number_align: str = "center"

    @classmethod
    def random(cls, rng, columns=None):
        columns = columns or rng.choice([1, 2])
        return cls(
            columns=columns,
            font=rng.choice(list(FONTS)),
            # Narrow columns need smaller type, like real two-column papers
            font_size=rng.choice([10.0, 10.5, 11.0, 12.0] if columns == 1 else [9.0, 9.5, 10.0]),
            margin=rng.choice([54.0, 63.0, 72.0]),
            page_size=rng.choice(list(PAGE_SIZES)),
            running_header=rng.random() < 0.6,
            numbered_headings=rng.random() < 0.5,
            justify=rng.random() < 0.7,
            title_align=rng.choice(["center", "left"]),
            page_number_align=rng.choice(["center", "right"]),
        )


# Text helpers

# The built-in PDF fonts only cover Latin-1 (cp1252) characters
_ASCII_MAP = str.maketrans({
    "→": "->", "←": "<-", "≈": "~", "≤": "<=", "≥": ">=", "−": "-", "₹": "Rs ",
    "​": "", " ": " ",
})


def _safe(text) -> str:
    text = str(text).translate(_ASCII_MAP)
    out = []
    for ch in text:
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            out.append(unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode())
    return "".join(out)


def _inline(text) -> str:
    """Escape text for a reportlab Paragraph and keep **bold** / *italic*."""
    t = escape(_safe(text), quote=False).replace("`", "")
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    return t


_LIST_RE = re.compile(r"^\s*([-*•]|\d+[.)])\s+(.+)$")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+)$")


# Layout tracking

def _flowable_size(f):
    if isinstance(f, Table):
        return f._width, f._height
    if isinstance(f, Image):
        return f.drawWidth, f.drawHeight
    return f.width, f.height


def _paragraph_extent(p: Paragraph, x: float, w: float):
    """Tight left/right edges of the text actually drawn by a paragraph."""
    st = p.style
    try:
        widths = p.getActualLineWidths0()
    except Exception:
        return x, x + w
    if not widths:
        return x, x + w
    li, ri = st.leftIndent, st.rightIndent
    avail = w - li - ri
    # Justified lines are stretched to the full width when drawn
    if st.alignment == TA_JUSTIFY and len(widths) > 1:
        text_w = avail
    else:
        text_w = min(avail, max(widths) - li - ri)
    if st.alignment == TA_CENTER:
        x0 = x + li + (avail - text_w) / 2
    elif st.alignment == TA_RIGHT:
        x0 = x + li + avail - text_w
    else:
        x0 = x + li
    x1 = x0 + text_w
    if p.bulletText:
        x0 = min(x0, x + st.bulletIndent)
    return x0, x1


def _paragraph_text(p: Paragraph) -> str:
    # Read the laid-out lines: pieces of a paragraph split across pages keep
    # their words there, while getPlainText() returns "" for them.
    bl = getattr(p, "blPara", None)
    if bl is None:
        return p.getPlainText()
    if bl.kind == 0:
        lines = [" ".join(words) for _, words in bl.lines]
    else:
        lines = ["".join(getattr(w, "text", "") for w in line.words) for line in bl.lines]
    return " ".join(l.strip() for l in lines if l.strip())


def _flowable_text(f) -> str:
    if isinstance(f, Paragraph):
        return _paragraph_text(f)
    if isinstance(f, Table):
        rows = []
        for row in f._cellvalues:
            rows.append(" | ".join(
                c.getPlainText() if isinstance(c, Paragraph) else str(c) for c in row
            ))
        return "\n".join(rows)
    return getattr(f, "_layout_text", "")


class _TrackingFrame(Frame):
    """A Frame that records where every labelled flowable is drawn.
    Paragraphs and tables that split across pages are recorded once per piece,
    which is exactly how layout datasets such as DocLayNet label them."""

    def __init__(self, *args, boxes, **kwargs):
        super().__init__(*args, **kwargs)
        self._boxes = boxes

    def add(self, flowable, canv, trySplit=0):
        label = getattr(flowable, "_layout_label", None)
        if not label:
            return Frame._add(self, flowable, canv, trySplit)

        boxes = self._boxes
        original = flowable.drawOn

        def drawOn(c, x, y, _sW=0):
            x = flowable._hAlignAdjust(x, _sW)
            w, h = _flowable_size(flowable)
            x0, x1 = _paragraph_extent(flowable, x, w) if isinstance(flowable, Paragraph) else (x, x + w)
            boxes.append({
                "page": c.getPageNumber(), "label": label,
                "x0": x0, "y0": y, "x1": x1, "y1": y + h,
                "text": _flowable_text(flowable),
            })
            return original(c, x, y)

        flowable.drawOn = drawOn
        try:
            return Frame._add(self, flowable, canv, trySplit)
        finally:
            del flowable.drawOn

    def split(self, flowable, canv):
        parts = Frame.split(self, flowable, canv)
        label = getattr(flowable, "_layout_label", None)
        if label:
            for part in parts:
                if isinstance(part, (Paragraph, Table, Image)) and not hasattr(part, "_layout_label"):
                    part._layout_label = label
        return parts


class DocumentAssembler:
    def __init__(self, style: LayoutStyle = None):
        self.style = style or LayoutStyle()
        self.document_title = ""
        self.sections_data = []
        # Filled by export_pdf: one dict per labelled element, in PDF points
        # (origin bottom-left, like the PDF itself)
        self.boxes = []
        self.page_size = PAGE_SIZES[self.style.page_size]
        # Chart PNGs live here for the whole run (the PDF is re-rendered many
        # times by the page-control loop, so each chart is drawn only once)
        self._chart_dir = tempfile.TemporaryDirectory(prefix="docfactory_charts_")
        self.styles = self._make_styles()

    def set_content(self, title: str, sections_data: list):
        self.document_title = title
        self.sections_data = sections_data

    # Styles
    def _make_styles(self) -> dict:
        s = self.style
        regular, bold, italic = FONTS[s.font]
        fs = s.font_size
        body = ParagraphStyle(
            "body", fontName=regular, fontSize=fs, leading=fs * 1.4,
            alignment=TA_JUSTIFY if s.justify else TA_LEFT, spaceAfter=fs * 0.6,
        )
        return {
            "body": body,
            "title": ParagraphStyle(
                "title", fontName=bold, fontSize=fs * 2.0, leading=fs * 2.4,
                alignment=TA_CENTER if s.title_align == "center" else TA_LEFT, spaceAfter=fs * 1.2,
            ),
            "heading": ParagraphStyle(
                "heading", fontName=bold, fontSize=fs * 1.35, leading=fs * 1.7,
                spaceBefore=fs * 1.1, spaceAfter=fs * 0.5, keepWithNext=1,
            ),
            "subheading": ParagraphStyle(
                "subheading", fontName=bold, fontSize=fs * 1.1, leading=fs * 1.4,
                spaceBefore=fs * 0.6, spaceAfter=fs * 0.3, keepWithNext=1,
            ),
            "list": ParagraphStyle(
                "list", parent=body, alignment=TA_LEFT, leftIndent=fs * 1.6,
                bulletIndent=fs * 0.4, spaceAfter=fs * 0.25,
            ),
            "caption": ParagraphStyle(
                "caption", fontName=italic, fontSize=fs * 0.85, leading=fs * 1.1,
                alignment=TA_CENTER, spaceBefore=fs * 0.3, spaceAfter=fs * 0.9,
            ),
            "cell": ParagraphStyle("cell", fontName=regular, fontSize=fs * 0.8, leading=fs * 1.0),
            "cell_head": ParagraphStyle("cell_head", fontName=bold, fontSize=fs * 0.8, leading=fs * 1.0),
        }

    def _para(self, text, style_key, label, bullet=None):
        p = Paragraph(_inline(text), self.styles[style_key], bulletText=bullet)
        p._layout_label = label
        return p

    # Content to flowables
    def _block_flowables(self, block: str) -> list:
        """Split one '\\n\\n' block into paragraphs, list items and sub-headings."""
        out, lines = [], []

        def flush():
            if lines:
                out.append(self._para(" ".join(lines), "body", "Text"))
                lines.clear()

        for line in block.split("\n"):
            line = line.strip()
            if not line:
                continue
            m = _HEADING_RE.match(line)
            if m:
                flush()
                out.append(self._para(m.group(1).strip("*# "), "subheading", "Section-header"))
                continue
            m = _LIST_RE.match(line)
            if m:
                flush()
                marker = m.group(1)
                bullet = "•" if marker in "-*•" else marker
                out.append(self._para(m.group(2), "list", "List-item", bullet=bullet))
                continue
            lines.append(line)
        flush()
        return out

    def _table_flowables(self, visual: dict, number: int, width: float) -> list:
        data = visual.get("data", {})
        headers = [str(h) for h in data.get("headers", [])]
        rows = [list(r) for r in data.get("rows", []) if isinstance(r, list)]
        ncols = max([len(headers)] + [len(r) for r in rows])
        if ncols == 0:
            return []
        cells = []
        if headers:
            cells.append([Paragraph(_inline(h), self.styles["cell_head"]) for h in headers + [""] * (ncols - len(headers))])
        for r in rows:
            r = r + [""] * (ncols - len(r))
            cells.append([Paragraph(_inline(c), self.styles["cell"]) for c in r[:ncols]])
        table = Table(cells, colWidths=[width / ncols] * ncols, repeatRows=1 if headers else 0, hAlign="CENTER")
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9a9a9a")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ececec") if headers else colors.white),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        table._layout_label = "Table"

        text = f"Table {number}. {visual.get('title') or 'Summary'}"
        if visual.get("caption"):
            text += f" — {visual['caption']}"
        caption = self._para(text, "caption", "Caption")
        return [KeepTogether([caption, table])]

    def _chart_flowables(self, visual: dict, number: int, width: float) -> list:
        png = self._draw_chart_png(visual)
        if png is None:  # can't plot it, so show the numbers as a table
            data = visual.get("data", {})
            labels = data.get("labels", [])
            datasets = data.get("datasets", [])
            as_table = {
                "title": visual.get("title"), "caption": visual.get("caption"),
                "data": {
                    "headers": ["Category"] + [str(d.get("label", "")) for d in datasets],
                    "rows": [[l] + [d.get("values", [])[i] if i < len(d.get("values", [])) else ""
                                    for d in datasets] for i, l in enumerate(labels)],
                },
            }
            return self._table_flowables(as_table, number, width)

        img_w = min(width, 400.0)
        image = Image(png, width=img_w, height=img_w * 3.6 / 6.4)
        image.hAlign = "CENTER"
        image._layout_label = "Picture"
        image._layout_text = str(visual.get("title", ""))
        text = f"Figure {number}. {visual.get('title') or 'Chart'}"
        if visual.get("caption"):
            text += f" — {visual['caption']}"
        caption = self._para(text, "caption", "Caption")
        return [KeepTogether([image, caption])]

    def _section_flowables(self, sec: dict, index: int, width: float, counters: dict) -> list:
        heading = f"{index}. {sec['title']}" if self.style.numbered_headings else sec["title"]
        out = [self._para(heading, "heading", "Section-header")]
        blocks = [b.strip() for b in sec.get("content", "").split("\n\n") if b.strip()]

        # 0-indexed "after block N", clamped to the current content
        placed = {}
        for v in sec.get("visuals", []):
            if not isinstance(v, dict):
                continue
            try:
                pos = int(v.get("insert_after_paragraph"))
            except (TypeError, ValueError):
                pos = len(blocks) - 1
            placed.setdefault(max(-1, min(pos, len(blocks) - 1)), []).append(v)

        def add_visuals(pos):
            for v in placed.get(pos, []):
                if v.get("type") == "table":
                    counters["table"] += 1
                    out.extend(self._table_flowables(v, counters["table"], width))
                else:
                    counters["figure"] += 1
                    out.extend(self._chart_flowables(v, counters["figure"], width))

        add_visuals(-1)
        for i, block in enumerate(blocks):
            out.extend(self._block_flowables(block))
            add_visuals(i)
        return out

    # Charts
    @staticmethod
    def _to_number(value) -> float:
        return float(str(value).replace(",", "").replace("%", "").strip())

    def _draw_chart_png(self, chart_data: dict):
        """Draw the chart with matplotlib and return the PNG path, or None if
        the data can't be plotted (the caller then shows it as a table)."""
        if plt is None:
            return None
        chart_type = str(chart_data.get("chart_type", "bar")).lower()
        data       = chart_data.get("data", {})
        labels     = [str(l) for l in data.get("labels", [])]
        series = []
        try:
            for ds in data.get("datasets", []):
                values = [self._to_number(v) for v in ds.get("values", [])[:len(labels)]]
                values += [0.0] * (len(labels) - len(values))
                series.append((str(ds.get("label", "")), values))
        except (TypeError, ValueError):
            return None
        if not labels or not series:
            return None

        key = hashlib.md5(json.dumps([chart_type, labels, series]).encode()).hexdigest()[:12]
        path = os.path.join(self._chart_dir.name, f"chart_{key}.png")
        if os.path.exists(path):
            return path

        wrapped = ["\n".join(textwrap.wrap(l, 14)) or l for l in labels]
        x = range(len(labels))
        fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=150)
        try:
            if chart_type == "pie":
                ax.pie(series[0][1], labels=wrapped, autopct="%1.0f%%", startangle=90)
                ax.axis("equal")
            else:
                if chart_type in ("line", "area"):
                    for name, values in series:
                        ax.plot(x, values, marker="o", label=name)
                        if chart_type == "area":
                            ax.fill_between(x, values, alpha=0.2)
                else:
                    width = 0.8 / len(series)
                    for i, (name, values) in enumerate(series):
                        offset = (i - (len(series) - 1) / 2) * width
                        ax.bar([p + offset for p in x], values, width, label=name)
                ax.set_xticks(list(x))
                ax.set_xticklabels(wrapped, fontsize=8)
                ax.spines[["top", "right"]].set_visible(False)
                ax.grid(axis="y", alpha=0.3)
                if chart_data.get("x_axis"):
                    ax.set_xlabel(str(chart_data["x_axis"]))
                y_label = chart_data.get("y_axis") or (series[0][0] if len(series) == 1 else "")
                if y_label:
                    ax.set_ylabel(str(y_label), fontsize=9)
                if len(series) > 1:
                    ax.legend(fontsize=8)
            fig.tight_layout()
            fig.savefig(path)
        except Exception as e:
            print(f"[Assembler] Chart drawing failed, using table instead: {e}")
            return None
        finally:
            plt.close(fig)
        return path

    # Page decoration (running header + page number)
    def _decorate_page(self, canv, doc):
        s = self.style
        regular = FONTS[s.font][0]
        size = s.font_size * 0.8
        page_w, page_h = self.page_size
        ascent, descent = getAscentDescent(regular, size)
        page = canv.getPageNumber()
        canv.saveState()
        canv.setFont(regular, size)
        canv.setFillGray(0.35)

        number = str(page)
        nw = stringWidth(number, regular, size)
        nx = (page_w - nw) / 2 if s.page_number_align == "center" else page_w - s.margin - nw
        ny = s.margin * 0.45
        canv.drawString(nx, ny, number)
        self.boxes.append({"page": page, "label": "Page-footer", "x0": nx, "y0": ny + descent,
                           "x1": nx + nw, "y1": ny + ascent, "text": number})

        if s.running_header and page > 1:
            text = _safe(self.document_title)
            if len(text) > 70:
                text = text[:67].rstrip() + "..."
            hw = stringWidth(text, regular, size)
            hy = page_h - s.margin * 0.6
            canv.drawString(s.margin, hy, text)
            canv.setLineWidth(0.4)
            canv.line(s.margin, hy - 5, page_w - s.margin, hy - 5)
            self.boxes.append({"page": page, "label": "Page-header", "x0": s.margin, "y0": hy + descent,
                               "x1": s.margin + hw, "y1": hy + ascent, "text": text})
        canv.restoreState()

    # Render
    def export_pdf(self, output_path: str):
        """Render the document to PDF and record every element's box in
        self.boxes. Raises RuntimeError if rendering fails, so the page-control
        loop never measures a stale file from an earlier run."""
        s = self.style
        self.boxes = []
        self.page_size = page_w, page_h = PAGE_SIZES[s.page_size]
        m = s.margin
        gap = 18.0
        content_w = page_w - 2 * m
        col_w = content_w if s.columns == 1 else (content_w - gap) / 2

        def frame(x, y, w, h, fid):
            return _TrackingFrame(x, y, w, h, id=fid, boxes=self.boxes,
                                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

        def columns(top, fid):
            return [frame(m + i * (col_w + gap), m, col_w, top - m, f"{fid}{i}") for i in range(s.columns)]

        title = self._para(self.document_title, "title", "Title")
        story = [title]
        if s.columns == 1:
            templates = [PageTemplate("page", columns(page_h - m, "col"), onPage=self._decorate_page)]
        else:
            # First page: the title spans both columns, then the text flows in two
            _, title_h = title.wrap(content_w, page_h)
            title_h += self.styles["title"].spaceAfter + 2
            body_top = page_h - m - title_h
            templates = [
                PageTemplate("first", [frame(m, body_top, content_w, title_h, "title")] + columns(body_top, "first"),
                             onPage=self._decorate_page, autoNextPageTemplate="later"),
                PageTemplate("later", columns(page_h - m, "later"), onPage=self._decorate_page),
            ]
            story.append(FrameBreak())

        counters = {"table": 0, "figure": 0}
        for i, sec in enumerate(self.sections_data, start=1):
            story.extend(self._section_flowables(sec, i, col_w, counters))

        doc = BaseDocTemplate(
            output_path, pagesize=(page_w, page_h),
            leftMargin=m, rightMargin=m, topMargin=m, bottomMargin=m,
            title=_safe(self.document_title), author="Synthetic Document Factory",
        )
        doc.addPageTemplates(templates)
        try:
            doc.build(story)
        except Exception as e:
            raise RuntimeError(f"Could not render {output_path}: {e}") from e

    def estimated_words_per_page(self) -> int:
        """Rough body-text capacity of one page in this style, so the first
        draft is close to the target length before the page-control loop."""
        s = self.style
        page_w, page_h = PAGE_SIZES[s.page_size]
        text_w = page_w - 2 * s.margin - (18 if s.columns == 2 else 0)
        area = text_w * (page_h - 2 * s.margin)
        return int(0.19 * area / (s.font_size * s.font_size * 1.4))

    def render_info(self, name: str, pdf_path: str) -> dict:
        """Everything the dataset exporter needs about the last render."""
        return {"name": name, "pdf": pdf_path, "boxes": list(self.boxes), "style": asdict(self.style)}
