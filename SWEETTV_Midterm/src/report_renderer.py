"""Render a structured block document to Markdown and to a styled PDF.

The same block list feeds both outputs, so the editable Markdown source and the
PDF can never drift apart.

Block forms
-----------
("h1", text) ("h2", text) ("h3", text)
("p", text) ("bullets", [text, ...]) ("code", [line, ...])
("table", {"header": [...], "rows": [[...]], "widths": [...],
           "align": ["L"/"R"/"C", ...]})
("callout", text) ("placeholder", text) ("spacer", points) ("pagebreak", None)
("image", {"path": ..., "width": 1.0, "caption": ...})

Inline markup: **bold** and `code` are supported in both renderers.
"""

import os
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, PageBreak,
                                PageTemplate, Paragraph, Preformatted, Spacer,
                                Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.lib.utils import ImageReader
from xml.sax.saxutils import escape

FONT_DIR = "/System/Library/Fonts/Supplemental"
BODY_FONT = os.path.join(FONT_DIR, "Arial Unicode.ttf")
BOLD_FONT = os.path.join(FONT_DIR, "Arial Bold.ttf")
ITALIC_FONT = os.path.join(FONT_DIR, "Arial Italic.ttf")

NAVY = colors.HexColor("#17365D")
BLUE = colors.HexColor("#1F4E79")
ACCENT = colors.HexColor("#2E75B6")
GRID = colors.HexColor("#B7C4D8")
ZEBRA = colors.HexColor("#F2F5FA")
PALE_BLUE = colors.HexColor("#EAF1F9")
AMBER = colors.HexColor("#FFF4D6")
AMBER_EDGE = colors.HexColor("#C9A227")
GREY = colors.HexColor("#5A5A5A")

PAGE_W, PAGE_H = A4
LM = RM = 50
TM = 58
BM = 50
AVAIL = PAGE_W - LM - RM


def register_fonts():
    pdfmetrics.registerFont(TTFont("Body", BODY_FONT))
    pdfmetrics.registerFont(TTFont("Body-Bold", BOLD_FONT))
    pdfmetrics.registerFont(TTFont("Body-Italic", ITALIC_FONT))
    for bold in (0, 1):
        for italic in (0, 1):
            target = "Body"
            if bold and italic:
                target = "Body-Bold"
            elif bold:
                target = "Body-Bold"
            elif italic:
                target = "Body-Italic"
            addMapping("Body", bold, italic, target)


# ---------------------------------------------------------------------------
# Inline markup
# ---------------------------------------------------------------------------

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def rich(text):
    """Escape XML and apply the tiny inline markup used by both renderers."""
    out = escape(text)
    out = _INLINE_CODE.sub(
        lambda m: '<font name="Courier" size="7.6">%s</font>' % m.group(1), out)
    out = _BOLD.sub(lambda m: "<b>%s</b>" % m.group(1), out)
    return out


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _md_cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(blocks, path, title=None):
    lines = []
    if title:
        lines += ["# " + title, ""]
    toc = [(b[0], b[1]) for b in blocks if b[0] in ("h1", "h2")]
    if toc:
        lines += ["## Contents", ""]
        for kind, text in toc:
            indent = "" if kind == "h1" else "  "
            lines.append("%s- %s" % (indent, text))
        lines.append("")
    for kind, value in blocks:
        if kind == "h1":
            lines += ["", "# " + value, ""]
        elif kind == "h2":
            lines += ["", "## " + value, ""]
        elif kind == "h3":
            lines += ["", "### " + value, ""]
        elif kind == "p":
            lines += [value, ""]
        elif kind == "bullets":
            lines += ["- " + item for item in value] + [""]
        elif kind == "code":
            lines += ["```bash"] + list(value) + ["```", ""]
        elif kind == "table":
            header = value["header"]
            rows = value["rows"]
            lines.append("| " + " | ".join(_md_cell(h) for h in header) + " |")
            align = value.get("align") or ["L"] * len(header)
            marks = {"L": ":---", "R": "---:", "C": ":---:"}
            lines.append("| " + " | ".join(marks.get(a, ":---") for a in align) + " |")
            for row in rows:
                lines.append("| " + " | ".join(_md_cell(c) for c in row) + " |")
            lines.append("")
        elif kind == "callout":
            lines += ["> " + value, ""]
        elif kind == "image":
            alt = value.get("caption", "image")
            lines += ["![%s](%s)" % (alt, value["path"]), ""]
            if value.get("caption"):
                lines += ["*" + value["caption"] + "*", ""]
        elif kind == "placeholder":
            for line in value.split("\n"):
                lines.append("> " + line)
            lines.append("")
        elif kind == "spacer":
            continue
        elif kind == "pagebreak":
            lines += ["", "---", ""]
        else:
            raise ValueError("unknown block: %r" % (kind,))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def build_styles():
    return {
        "title": ParagraphStyle(
            "Title", fontName="Body-Bold", fontSize=25, leading=30,
            textColor=colors.white, spaceAfter=6),
        "subtitle": ParagraphStyle(
            "Subtitle", fontName="Body", fontSize=12.5, leading=18,
            textColor=colors.white),
        "cover_meta": ParagraphStyle(
            "CoverMeta", fontName="Body", fontSize=9.5, leading=16,
            textColor=NAVY),
        "cover_label": ParagraphStyle(
            "CoverLabel", fontName="Body-Bold", fontSize=8.5, leading=12,
            textColor=ACCENT),
        "toc_title": ParagraphStyle(
            "TocTitle", fontName="Body-Bold", fontSize=16, leading=20,
            textColor=NAVY, spaceAfter=10),
        "h1": ParagraphStyle(
            "H1", fontName="Body-Bold", fontSize=15, leading=19,
            textColor=NAVY, spaceBefore=10, spaceAfter=7, keepWithNext=1),
        "h2": ParagraphStyle(
            "H2", fontName="Body-Bold", fontSize=11.4, leading=15,
            textColor=BLUE, spaceBefore=9, spaceAfter=4, keepWithNext=1),
        "h3": ParagraphStyle(
            "H3", fontName="Body-Bold", fontSize=9.9, leading=13,
            textColor=colors.HexColor("#333333"), spaceBefore=7,
            spaceAfter=2, keepWithNext=1),
        "body": ParagraphStyle(
            "Body", fontName="Body", fontSize=9.4, leading=13.6,
            alignment=TA_JUSTIFY, spaceAfter=5.5),
        "bullet": ParagraphStyle(
            "Bullet", fontName="Body", fontSize=9.4, leading=13.2,
            leftIndent=14, bulletIndent=3, spaceAfter=2.5,
            alignment=TA_LEFT),
        "callout": ParagraphStyle(
            "Callout", fontName="Body", fontSize=9.1, leading=13,
            alignment=TA_LEFT),
        "placeholder": ParagraphStyle(
            "Placeholder", fontName="Body", fontSize=9.1, leading=13,
            alignment=TA_LEFT),
        "cell": ParagraphStyle(
            "Cell", fontName="Body", fontSize=7.7, leading=9.9,
            alignment=TA_LEFT),
        "cell_r": ParagraphStyle(
            "CellR", fontName="Body", fontSize=7.7, leading=9.9,
            alignment=2),
        "cell_c": ParagraphStyle(
            "CellC", fontName="Body", fontSize=7.7, leading=9.9,
            alignment=1),
        "head": ParagraphStyle(
            "Head", fontName="Body-Bold", fontSize=7.7, leading=9.9,
            textColor=colors.white),
        "head_r": ParagraphStyle(
            "HeadR", fontName="Body-Bold", fontSize=7.7, leading=9.9,
            textColor=colors.white, alignment=2),
        "head_c": ParagraphStyle(
            "HeadC", fontName="Body-Bold", fontSize=7.7, leading=9.9,
            textColor=colors.white, alignment=1),
        "code": ParagraphStyle(
            "Code", fontName="Courier", fontSize=7.4, leading=10,
            textColor=colors.HexColor("#1A1A1A")),
        "caption": ParagraphStyle(
            "Caption", fontName="Body-Italic", fontSize=8.2, leading=11,
            textColor=GREY, alignment=TA_LEFT, spaceAfter=8),
    }


def _pdf_table(spec, styles):
    header = spec["header"]
    rows = spec["rows"]
    widths = spec.get("widths") or [1.0] * len(header)
    align = spec.get("align") or ["L"] * len(header)
    total = float(sum(widths))
    col_widths = [AVAIL * w / total for w in widths]

    head_style = {"L": "head", "R": "head_r", "C": "head_c"}
    cell_style = {"L": "cell", "R": "cell_r", "C": "cell_c"}
    data = [[Paragraph(rich(str(c)), styles[head_style[align[i]]])
             for i, c in enumerate(header)]]
    for row in rows:
        data.append([Paragraph(rich(str(c)), styles[cell_style[align[i]]])
                     for i, c in enumerate(row)])
    table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    cmd = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            cmd.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    table.setStyle(TableStyle(cmd))
    return table


class ReportDoc(BaseDocTemplate):
    def __init__(self, filename, header_left, header_right, **kw):
        BaseDocTemplate.__init__(self, filename, pagesize=A4,
                                 leftMargin=LM, rightMargin=RM,
                                 topMargin=TM, bottomMargin=BM,
                                 title=kw.pop("doc_title", ""),
                                 author=kw.pop("doc_author", ""),
                                 subject=kw.pop("doc_subject", ""),
                                 **kw)
        self.header_left = header_left
        self.header_right = header_right
        frame = Frame(LM, BM, AVAIL, PAGE_H - TM - BM, id="body",
                      leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0)
        self.addPageTemplates([
            PageTemplate(id="main", frames=[frame],
                         onPage=self._decorate),
        ])

    def _decorate(self, canvas, doc):
        if doc.page == 1:
            draw_cover(canvas, doc)
            return
        canvas.saveState()
        canvas.setStrokeColor(GRID)
        canvas.setLineWidth(0.5)
        canvas.line(LM, PAGE_H - 40, PAGE_W - RM, PAGE_H - 40)
        canvas.setFont("Body", 7.4)
        canvas.setFillColor(GREY)
        canvas.drawString(LM, PAGE_H - 35, self.header_left)
        canvas.drawRightString(PAGE_W - RM, PAGE_H - 35, self.header_right)
        canvas.line(LM, 36, PAGE_W - RM, 36)
        canvas.drawString(LM, 24, "SWEET.TV TV Program Recommendation — Final Exam Report")
        canvas.drawRightString(PAGE_W - RM, 24, "Page %d" % doc.page)
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            name = flowable.style.name
            if name in ("H1", "H2"):
                level = 0 if name == "H1" else 1
                self.notify("TOCEntry",
                            (level, flowable.getPlainText(), self.page))


def draw_cover(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_H - 250, PAGE_W, 250, stroke=0, fill=1)
    canvas.setFillColor(ACCENT)
    canvas.rect(0, PAGE_H - 258, PAGE_W, 8, stroke=0, fill=1)
    canvas.restoreState()


def _cover_story(meta, styles):
    story = [Spacer(1, 58)]
    story.append(Paragraph(rich(meta["title"]), styles["title"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(rich(meta["subtitle"]), styles["subtitle"]))
    story.append(Spacer(1, 150))
    rows = [
        ("Competition", meta["competition"]),
        ("Competition URL", meta["url"]),
        ("Author", meta["author"]),
        ("Course / track", meta["course"]),
        ("Date", meta["date"]),
        ("Report version", meta["version"]),
    ]
    data = []
    for label, value in rows:
        data.append([Paragraph(rich(label), styles["cover_label"]),
                     Paragraph(rich(value), styles["cover_meta"])])
    table = Table(data, colWidths=[95, AVAIL - 95], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, colors.HexColor("#D6DEE9")),
    ]))
    story.append(table)
    story.append(Spacer(1, 26))
    box = Table([[Paragraph(
        rich(meta["status"]), styles["callout"])]],
        colWidths=[AVAIL], hAlign="LEFT")
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
        ("BOX", (0, 0), (-1, -1), 0.5, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(box)
    return story


def render_pdf(blocks, path, meta):
    register_fonts()
    styles = build_styles()
    doc = ReportDoc(
        path,
        header_left="SWEET.TV TV Program Recommender — LLM4Rec Final Exam",
        header_right="Stage 3 · Recommendation Modelling",
        doc_title=meta["title"],
        doc_author=meta["author"],
        doc_subject="SWEET.TV TV program recommendation — final exam report",
    )

    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(name="TOC0", fontName="Body-Bold", fontSize=9.6,
                       leading=15, textColor=NAVY, spaceBefore=3),
        ParagraphStyle(name="TOC1", fontName="Body", fontSize=8.7,
                       leading=12.6, leftIndent=16,
                       textColor=colors.HexColor("#333333")),
    ]
    toc.dotsMinLevel = 0

    story = _cover_story(meta, styles)
    story.append(PageBreak())
    story.append(Paragraph("Contents", styles["toc_title"]))
    story.append(toc)
    story.append(PageBreak())

    for kind, value in blocks:
        if kind == "h1":
            story.append(Paragraph(rich(value), styles["h1"]))
        elif kind == "h2":
            story.append(Paragraph(rich(value), styles["h2"]))
        elif kind == "h3":
            story.append(Paragraph(rich(value), styles["h3"]))
        elif kind == "p":
            story.append(Paragraph(rich(value), styles["body"]))
        elif kind == "bullets":
            for item in value:
                story.append(Paragraph(rich(item), styles["bullet"],
                                       bulletText="\u2022"))
            story.append(Spacer(1, 3))
        elif kind == "code":
            text = "<br/>".join(escape(line).replace(" ", "&nbsp;")
                                for line in value)
            box = Table([[Paragraph(text, styles["code"])]],
                        colWidths=[AVAIL], hAlign="LEFT")
            box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F4F4")),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(box)
            story.append(Spacer(1, 7))
        elif kind == "table":
            story.append(Spacer(1, 2))
            story.append(_pdf_table(value, styles))
            story.append(Spacer(1, 8))
        elif kind == "callout":
            box = Table([[Paragraph(rich(value), styles["callout"])]],
                        colWidths=[AVAIL], hAlign="LEFT")
            box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
                ("BOX", (0, 0), (-1, -1), 0.5, ACCENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            story.append(box)
            story.append(Spacer(1, 7))
        elif kind == "image":
            src = value["path"]
            if not os.path.isfile(src):
                raise FileNotFoundError("image block references a missing "
                                        "file: %s" % src)
            iw, ih = ImageReader(src).getSize()
            width = AVAIL * float(value.get("width", 1.0))
            height = width * ih / float(iw)
            max_height = PAGE_H - TM - BM - 60
            if height > max_height:
                height = max_height
                width = height * iw / float(ih)
            story.append(Spacer(1, 2))
            story.append(Image(src, width=width, height=height))
            if value.get("caption"):
                story.append(Spacer(1, 4))
                story.append(Paragraph(rich(value["caption"]),
                                       styles["caption"]))
            else:
                story.append(Spacer(1, 8))
        elif kind == "placeholder":
            box = Table([[Paragraph(rich(value), styles["placeholder"])]],
                        colWidths=[AVAIL], hAlign="LEFT")
            box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), AMBER),
                ("BOX", (0, 0), (-1, -1), 0.9, AMBER_EDGE),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]))
            story.append(box)
            story.append(Spacer(1, 8))
        elif kind == "spacer":
            story.append(Spacer(1, value))
        elif kind == "pagebreak":
            story.append(PageBreak())
        else:
            raise ValueError("unknown block: %r" % (kind,))

    doc.multiBuild(story)
    return path
