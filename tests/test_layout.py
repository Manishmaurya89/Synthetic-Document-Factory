"""Layout and dataset tests. They use fixed sample content, so no Ollama is needed."""
import json
import os
import re

import pypdfium2 as pdfium
import pytest

from core.assembler import DocumentAssembler, LayoutStyle
from core.dataset import CATEGORIES, export_dataset
from core.llm import ContentGenerator

WORDS = ("railway signal track freight passenger station electric corridor network "
         "speed coach engine timetable gauge junction platform").split()


def para(n, start=0):
    return " ".join(WORDS[(start + i) % len(WORDS)] for i in range(n)).capitalize() + "."


TABLE = {
    "type": "table", "title": "Zones & <HQs>", "caption": "Main zones", "insert_after_paragraph": 1,
    "data": {"headers": ["Zone", "HQ", "Route km"],
             "rows": [["Northern", "Delhi", "6,968"], ["Western", "Mumbai", ""], ["Southern", "Chennai"]]},
}
CHART = {
    "type": "chart", "title": "Passengers", "chart_type": "bar", "caption": "Billions", "insert_after_paragraph": 2,
    "data": {"labels": ["2000", "2010", "2020"], "datasets": [{"label": "Billions", "values": ["4.8", 7.6, 8.1]}]},
}
SECTIONS = [
    {"title": "Origins of the Network",
     "content": "\n\n".join([para(120), "Key facts with **bold** text:\n- First train in 1853\n- 68,000 km of track",
                             para(150, 3), "### A closer look", para(140, 5)]),
     "visuals": [TABLE, CHART]},
    {"title": "Modern Electrification", "content": "\n\n".join(para(160, i) for i in range(5)), "visuals": []},
]


def render(tmp_path, style, name="doc"):
    assembler = DocumentAssembler(style)
    assembler.set_content("Indian Railways", SECTIONS)
    pdf = str(tmp_path / f"{name}.pdf")
    assembler.export_pdf(pdf)
    return assembler, pdf


@pytest.mark.parametrize("style", [
    LayoutStyle(columns=1),
    LayoutStyle(columns=2, font="Helvetica", font_size=10, page_size="Letter", margin=54,
                numbered_headings=False, justify=False, title_align="left", page_number_align="right"),
])
def test_every_element_is_labelled_inside_the_page(tmp_path, style):
    assembler, pdf = render(tmp_path, style)
    page_w, page_h = assembler.page_size
    labels = {b["label"] for b in assembler.boxes}
    assert {"Title", "Section-header", "Text", "List-item", "Table", "Picture", "Caption", "Page-footer"} <= labels
    for b in assembler.boxes:
        assert b["label"] in CATEGORIES
        assert 0 <= b["x0"] < b["x1"] <= page_w + 0.01
        assert 0 <= b["y0"] < b["y1"] <= page_h + 0.01

    page_count = len(pdfium.PdfDocument(pdf))
    footers = [b["page"] for b in assembler.boxes if b["label"] == "Page-footer"]
    assert sorted(footers) == list(range(1, page_count + 1))


@pytest.mark.parametrize("columns", [1, 2])
def test_text_boxes_contain_their_own_text(tmp_path, columns):
    """Read the PDF text back from inside each box: it must be the labelled text."""
    assembler, pdf = render(tmp_path, LayoutStyle(columns=columns))
    doc = pdfium.PdfDocument(pdf)
    checked = 0
    for b in assembler.boxes:
        if b["label"] not in ("Title", "Section-header", "Text", "List-item", "Caption"):
            continue
        textpage = doc[b["page"] - 1].get_textpage()
        inside = textpage.get_text_bounded(b["x0"] - 1, b["y0"] - 1, b["x1"] + 1, b["y1"] + 1)
        squash = lambda s: re.sub(r"\s+", "", s)
        words = b["text"].split()
        # The box must hold the first and last words (not shifted or cut off)...
        assert squash("".join(words[:4])) in squash(inside), (b["label"], b["text"][:60], inside[:120])
        assert squash("".join(words[-4:])) in squash(inside), (b["label"], b["text"][-60:], inside[-120:])
        # ...and nothing much beyond its own text (not too big)
        assert len(squash(inside)) <= len(squash(b["text"])) + 3, (b["label"], b["text"][:60], inside[:120])
        checked += 1
    assert checked > 10


def test_paragraph_split_across_pages_gets_a_box_on_each_page(tmp_path):
    assembler, _ = render(tmp_path, LayoutStyle(columns=1, font_size=12))
    text_pages = {b["page"] for b in assembler.boxes if b["label"] == "Text"}
    assert len(text_pages) >= 2


def test_export_writes_coco_yolo_and_images(tmp_path):
    a1, pdf1 = render(tmp_path, LayoutStyle(columns=1), "doc_1")
    a2, pdf2 = render(tmp_path, LayoutStyle(columns=2), "doc_2")
    out = tmp_path / "dataset"
    stats = export_dataset(str(out), [a1.render_info("doc_1", pdf1), a2.render_info("doc_2", pdf2)],
                           dpi=72, preview=True)

    coco = json.loads((out / "annotations.json").read_text())
    assert [c["name"] for c in coco["categories"]] == CATEGORIES
    assert len(coco["images"]) == stats["pages"] == len(os.listdir(out / "images"))
    assert len(coco["annotations"]) == stats["boxes"] > 0
    image_ids = {img["id"] for img in coco["images"]}
    for ann in coco["annotations"]:
        assert ann["image_id"] in image_ids
        x, y, w, h = ann["bbox"]
        assert w > 0 and h > 0

    label_lines = 0
    for name in os.listdir(out / "labels"):
        assert (out / "images" / name.replace(".txt", ".png")).exists()
        for line in (out / "labels" / name).read_text().splitlines():
            cls, *box = line.split()
            assert 0 <= int(cls) < len(CATEGORIES)
            assert all(0 <= float(v) <= 1 for v in box)
            label_lines += 1
    assert label_lines == stats["boxes"]
    assert len(os.listdir(out / "preview")) == stats["pages"]
    assert "names:" in (out / "data.yaml").read_text()


def test_random_layouts_are_repeatable_with_a_seed():
    import random
    a = [LayoutStyle.random(random.Random(7)) for _ in range(3)]
    b = [LayoutStyle.random(random.Random(7)) for _ in range(3)]
    assert a == b


def test_label_stripping_keeps_real_words():
    strip = ContentGenerator.strip_labels
    assert strip("Photosynthesis feeds plants.") == "Photosynthesis feeds plants."
    assert strip("Parallel lines.") == "Parallel lines."
    assert strip("Section 3 of the Act applies.") == "Section 3 of the Act applies."
    assert strip("Paragraph 2: real text") == "real text"
    assert strip("**Paragraph 3:** bold label") == "bold label"
