import argparse
import json
import os
import random
import re
import sys

from pypdf import PdfReader

from core.assembler import DocumentAssembler, LayoutStyle
from core.dataset import export_dataset
from core.llm import LABEL_RE, ContentGenerator


def get_page_count(pdf_path: str) -> int:
    try:
        return len(PdfReader(pdf_path).pages)
    except Exception:
        return 0


def sanitize_topic(topic: str) -> str:
    slug = re.sub(r'[^a-z0-9\-]+', '_', str(topic).strip().lower())
    return slug.strip('_-') or "document"


_FENCE_RE = re.compile(r'```[a-zA-Z]*\n?|```')


def clean_generated_text(text: str) -> str:
    # Run every piece of model output through this, not only the first draft,
    # or "Paragraph 2:" style labels end up printed in the PDF.
    if not isinstance(text, str):
        return ""
    text = _FENCE_RE.sub('', text)
    text = LABEL_RE.sub('', text)
    text = text.replace('\r\n', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def enforce_schema(sections: list) -> list:
    """Drop sections without a title and fill in any missing fields."""
    if not isinstance(sections, list):
        return []
    valid_sections = []
    for item in sections:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        content = item.get("content", "")
        if not isinstance(content, str):
            content = ""
        visuals = item.get("visuals", [])
        if not isinstance(visuals, list):
            visuals = []
        valid_sections.append({
            "title": title.strip(),
            "content": content.strip(),
            "visuals": visuals,
            "failed_attempts": item.get("failed_attempts", 0),
        })
    return valid_sections


def render(assembler: DocumentAssembler, filename: str, title: str, sections: list):
    assembler.set_content(title, enforce_schema(sections))
    assembler.export_pdf(filename)


def generate_all_content(llm: ContentGenerator, topic: str, sections: list, words_per_section: int) -> list:
    context = ""
    for sec in sections:
        print(f"\n   Writing '{sec['title']}' (~{words_per_section} words)...")
        if sec["content"]:
            continue
        text = clean_generated_text(llm.generate_section_content(
            topic=topic,
            section_title=sec["title"],
            target_words=words_per_section,
            context=context,
        ))
        sec["content"] = text
        # Hand the end of this section to the next one so it doesn't repeat it
        words = text.split()
        context = ' '.join(words[-800:]) if len(words) > 800 else text
    return sections


def polish_content(llm: ContentGenerator, sections: list) -> list:
    """Rewrite the paragraphs that fail the quick checks in evaluate_paragraph."""
    for sec in sections:
        print(f"\n   Checking '{sec['title']}'...")
        blocks = [p.strip() for p in sec["content"].split("\n\n") if p.strip()]
        kept = []
        context = ""
        for i, para in enumerate(blocks, 1):
            ok, reason = llm.evaluate_paragraph(para, context)
            if ok:
                print(f"      paragraph {i}/{len(blocks)} ok")
                kept.append(para)
            else:
                print(f"      paragraph {i}/{len(blocks)} rewriting ({reason})")
                kept.append(llm.rewrite_weak_paragraph(para, context, reason))
            context = context[-400:] + " " + kept[-1]
        sec["content"] = "\n\n".join(kept)
    return sections


def add_visuals(llm: ContentGenerator, topic: str, sections: list, tables_left: int, charts_left: int):
    """Give sections a table and/or a chart, in order, until the limits run out."""
    for i, sec in enumerate(sections, 1):
        print(f"   Section {i}: {sec['title']}")
        visuals = sec.get("visuals", [])

        if tables_left > 0:
            print("      generating a table...")
            tables = llm.generate_table_for_section(topic, sec["title"], sec["content"])
            for table in tables:
                if tables_left > 0:
                    tables_left -= 1
                    # Work out the position here: the model just copies the
                    # example number from the JSON template in the prompt.
                    table["insert_after_paragraph"] = llm.determine_visual_placement(sec["content"], "table")
                    table["type"] = "table"
                    visuals.append(table)
                    print(f"      added table '{table.get('title')}'")
            if not tables:
                print("      no usable table for this section")

        if charts_left > 0:
            print("      generating a chart...")
            charts = llm.generate_chart_for_section(topic, sec["title"], sec["content"])
            for chart in charts:
                if charts_left > 0:
                    charts_left -= 1
                    chart["insert_after_paragraph"] = llm.determine_visual_placement(sec["content"], "chart")
                    chart["type"] = "chart"
                    visuals.append(chart)
                    print(f"      added chart '{chart.get('title')}'")
            if not charts:
                print("      no usable chart for this section")

        sec["visuals"] = visuals


def expand_sections(llm: ContentGenerator, topic: str, sections: list, pages_short: int) -> list:
    """Add text to the section that is furthest behind.

    More than 2 pages short: add a new two-paragraph block. Otherwise add one
    new paragraph. expand_paragraph only adds 30-75 words, which is not enough
    to move the page count, so it is just the fallback here. A section that
    fails 3 times in a row is skipped until every section has failed.
    """
    if not sections:
        return sections

    # Every section has the same word target, so the shortest is the furthest behind
    by_length = sorted(sections, key=lambda s: len(s["content"].split()))
    target = next((s for s in by_length if s["failed_attempts"] < 3), None)
    if target is None:
        print("\n   Every section failed 3 times, resetting the counters.")
        for s in sections:
            s["failed_attempts"] = 0
        target = by_length[0]

    if pages_short > 2:
        print(f"\n   {pages_short} pages short, adding a block to '{target['title']}'")
        block = clean_generated_text(llm.generate_new_content_block(
            topic=topic,
            section_title=target["title"],
            context=target["content"],
        ))
        # mistral usually gives 170-240 words here
        if len(block.split()) > 120:
            target["content"] = f"{target['content']}\n\n{block}".strip()
            target["failed_attempts"] = 0
            print(f"       added {len(block.split())} words")
        else:
            target["failed_attempts"] += 1
            print(f"       block too short ({len(block.split())} words), attempt {target['failed_attempts']}/3")
        return sections

    print(f"\n   {pages_short} page(s) short, adding a paragraph to '{target['title']}'")
    para = clean_generated_text(llm.generate_new_idea_paragraph(
        topic=topic,
        section_title=target["title"],
        context=target["content"],
    ))
    if len(para.split()) > 80:
        target["content"] = f"{target['content']}\n\n{para}".strip()
        target["failed_attempts"] = 0
        print(f"       added {len(para.split())} words")
        return sections

    print("       too short, lengthening a paragraph instead")
    blocks = [p.strip() for p in target["content"].split("\n\n") if p.strip()]
    if not blocks:
        target["failed_attempts"] += 1
        return sections

    i = min(range(len(blocks)), key=lambda j: len(blocks[j].split()))
    expanded = clean_generated_text(llm.expand_paragraph(blocks[i]))
    growth = len(expanded.split()) - len(blocks[i].split())
    if growth > 25:
        blocks[i] = expanded
        target["content"] = "\n\n".join(blocks)
        target["failed_attempts"] = 0
        print(f"       paragraph grew by {growth} words")
    else:
        target["failed_attempts"] += 1
        print(f"       didn't grow enough ({target['failed_attempts']}/3)")
    return sections


def trim_longest_section(sections: list, llm: ContentGenerator = None) -> list:
    """Drop the last paragraph of the longest section, or shorten one if it is already short."""
    if not sections:
        return sections

    target = max(sections, key=lambda s: len(s["content"]))
    blocks = [b for b in target["content"].split("\n\n") if b.strip()]

    if len(blocks) > 3:
        removed = blocks.pop()
        target["content"] = "\n\n".join(blocks).strip()
        print(f"   removed a paragraph from '{target['title']}' ({len(removed.split())} words)")
    elif len(blocks) > 1 and llm:
        i = max(range(len(blocks)), key=lambda j: len(blocks[j]))
        compressed = llm.compress_paragraph(blocks[i])
        saved = len(blocks[i].split()) - len(compressed.split())
        if saved > 20:
            blocks[i] = compressed
            target["content"] = "\n\n".join(blocks).strip()
            print(f"   shortened a paragraph in '{target['title']}' (saved {saved} words)")
        else:
            print("   shortening didn't save enough, skipping")
    else:
        print("   section too short to trim")

    return sections


def build_part(
    filename: str,
    title: str,
    sections: list,
    target_pages: int,
    llm: ContentGenerator,
    assembler: DocumentAssembler,
    total_tables: int = 0,
    total_charts: int = 0,
    max_iters: int = 25,
):
    """Write the document, then add or cut text until it has about target_pages pages."""
    sections = enforce_schema(sections)
    print(f"\nBuilding {filename}: {target_pages} pages, {len(sections)} sections")

    paragraphs_per_section, words_per_section = ContentGenerator.calculate_targets(
        target_pages, len(sections), assembler.estimated_words_per_page()
    )
    print(f"Aiming for ~{paragraphs_per_section} paragraphs (~{words_per_section} words) per section")

    print("\nWriting sections")
    sections = generate_all_content(llm, title, sections, words_per_section)

    print("\nChecking paragraphs")
    sections = polish_content(llm, sections)

    if total_tables > 0 or total_charts > 0:
        print("\nAdding tables and charts")
        add_visuals(llm, title, sections, total_tables, total_charts)

    render(assembler, filename, title, sections)
    pages = get_page_count(filename)
    print(f"\nFirst render: {pages} pages")

    stalled = 0
    for iteration in range(1, max_iters + 1):
        diff = pages - target_pages
        print(f"[{iteration}/{max_iters}] {pages} pages, target {target_pages} ({diff:+d})")

        # One page over is close enough, one page short is not
        if diff in (0, 1):
            print("Page count reached.")
            break

        if diff < 0:
            sections = expand_sections(llm, title, sections, -diff)
        else:
            sections = trim_longest_section(sections, llm)

        render(assembler, filename, title, sections)
        new_pages = get_page_count(filename)

        # The first few rounds often don't move the page count, so only start counting after them
        if iteration > 3:
            stalled = stalled + 1 if new_pages == pages else 0
            if stalled:
                print(f"   page count unchanged ({stalled}/5)")
            if stalled >= 5:
                print("No progress in 5 rounds, stopping.")
                break

        pages = new_pages

    print(f"\nDone: {get_page_count(filename)} pages (asked for {target_pages})")
    print(f"Saved {filename}\n")
    return enforce_schema(sections)


def main():
    parser = argparse.ArgumentParser(
        description="Generate labelled document-layout datasets (PDF + page images + COCO/YOLO boxes) with a local LLM"
    )
    parser.add_argument("--topic",    type=str, help="Document topic")
    parser.add_argument("--pages",    type=int, help="Target PDF pages")
    parser.add_argument("--tables",   type=int, default=None, help="Tables in the document (0-5)")
    parser.add_argument("--charts",   type=int, default=None, help="Charts in the document (0-5)")
    parser.add_argument("--visuals",  action="store_true", help="Shortcut for 1 table, 0 charts")
    parser.add_argument("--layout",   choices=["single", "double", "random"], default="random",
                        help="One column, two columns, or pick per variant (default: random)")
    parser.add_argument("--variants", type=int, default=1,
                        help="Render the same text in this many different layouts (default: 1)")
    parser.add_argument("--seed",     type=int, default=None, help="Seed for repeatable layouts")
    parser.add_argument("--dpi",      type=int, default=150, help="Page image resolution (default: 150)")
    parser.add_argument("--preview",  action="store_true", help="Also save pages with the boxes drawn on")
    args = parser.parse_args()

    topic = (args.topic or input("Topic: ")).strip()
    if not topic:
        print("A topic is required.")
        return

    try:
        target_pages = args.pages if args.pages is not None else int(input("Pages: "))
    except ValueError:
        print("Pages must be a number.")
        return

    if not (1 <= target_pages <= 200):
        print("Pages must be between 1 and 200.")
        return

    total_tables = args.tables if args.tables is not None else -1
    total_charts = args.charts if args.charts is not None else -1

    if args.visuals and total_tables == -1 and total_charts == -1:
        total_tables, total_charts = 1, 0
    elif total_tables == -1 and total_charts == -1:
        try:
            t = input("Tables (0-5) [0]: ").strip()
            total_tables = int(t) if t else 0
            c = input("Charts (0-5) [0]: ").strip()
            total_charts = int(c) if c else 0
        except ValueError:
            print("Not a number, using 0 tables and 0 charts.")
            total_tables = total_charts = 0

    total_tables = max(0, min(5, total_tables))
    total_charts = max(0, min(5, total_charts))
    variants = max(1, args.variants)

    print(f"\nTopic: {topic}")
    print(f"Pages: {target_pages}, tables: {total_tables}, charts: {total_charts}, "
          f"layout: {args.layout}, variants: {variants}")

    try:
        llm = ContentGenerator()
    except RuntimeError as e:
        print(e)
        sys.exit(1)

    rng = random.Random(args.seed)
    columns = {"single": 1, "double": 2}.get(args.layout)
    assembler = DocumentAssembler(LayoutStyle.random(rng, columns))
    slug = sanitize_topic(topic)
    out_dir = os.path.join("output", slug)
    pdf_dir = os.path.join(out_dir, "pdf")
    os.makedirs(pdf_dir, exist_ok=True)

    num_sections = ContentGenerator.plan_sections(target_pages)
    outline = enforce_schema(llm.generate_outline(topic, num_sections))
    print("\nOutline:")
    for i, sec in enumerate(outline, 1):
        print(f"   {i}. {sec['title']}")

    try:
        first_pdf = os.path.join(pdf_dir, f"{slug}_1.pdf")
        sections = build_part(
            first_pdf, topic, outline, target_pages, llm, assembler,
            total_tables=total_tables,
            total_charts=total_charts,
        )
        renders = [assembler.render_info(f"{slug}_1", first_pdf)]

        # Keep the generated text so it can be reused or inspected later
        with open(os.path.join(out_dir, "content.json"), "w", encoding="utf-8") as f:
            json.dump({"title": topic, "sections": sections}, f, ensure_ascii=False, indent=2)

        def describe(a, path):
            st = a.style
            return (f"{st.columns} column(s), {st.font} {st.font_size:g}pt, "
                    f"{st.page_size}, {get_page_count(path)} pages")

        print(f"   Variant 1: {describe(assembler, first_pdf)}")

        # Same text in a new layout, so no extra LLM calls
        for v in range(2, variants + 1):
            variant = DocumentAssembler(LayoutStyle.random(rng, columns))
            path = os.path.join(pdf_dir, f"{slug}_{v}.pdf")
            render(variant, path, topic, sections)
            renders.append(variant.render_info(f"{slug}_{v}", path))
            print(f"   Variant {v}: {describe(variant, path)}")
    except RuntimeError as e:
        print(e)
        sys.exit(1)

    print("\nExporting dataset")
    stats = export_dataset(out_dir, renders, dpi=args.dpi, preview=args.preview)
    print(f"{stats['pages']} page images, {stats['boxes']} labelled boxes in {out_dir}/")
    for label, count in sorted(stats["counts"].items(), key=lambda kv: -kv[1]):
        print(f"   {label:<15} {count}")


if __name__ == "__main__":
    main()
