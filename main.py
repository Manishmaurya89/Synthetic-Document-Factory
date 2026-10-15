import os
import re
import sys
import json
import heapq
import random
import argparse
from pypdf import PdfReader
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv(override=True)

from core.llm import ContentGenerator, LABEL_RE
from core.assembler import DocumentAssembler, LayoutStyle
from core.dataset import export_dataset


def get_page_count(pdf_path: str) -> int:
    try:
        return len(PdfReader(pdf_path).pages)
    except Exception:
        return 0


def sanitize_topic(topic: str) -> str:
    slug = re.sub(r'[^a-z0-9\-]+', '_', str(topic).strip().lower())
    return slug.strip('_-') or "document"


# ─────────────────────────────────────────────────────────────────
# FIX 1: Central label-strip utility applied to ALL generated text.
# Previously only generate_section_content stripped labels, so
# expansion blocks from generate_new_content_block and
# generate_new_idea_paragraph leaked "Paragraph 1:", "Paragraph 2:"
# etc. directly into the PDF.
# ─────────────────────────────────────────────────────────────────
_FENCE_RE = re.compile(r'```[a-zA-Z]*\n?|```')

def clean_generated_text(text: str) -> str:
    """Strip code fences, paragraph labels, and excess blank lines."""
    if not isinstance(text, str):
        return ""
    text = _FENCE_RE.sub('', text)
    text = LABEL_RE.sub('', text)
    text = text.replace('\r\n', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def enforce_schema(sections: list) -> list:
    valid_sections = []
    if not isinstance(sections, list):
        return []
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
        failed_attempts = item.get("failed_attempts", 0)
        valid_sections.append({
            "title": title.strip(),
            "content": content.strip(),
            "visuals": visuals,
            "failed_attempts": failed_attempts,
        })
    return valid_sections


def render(assembler: DocumentAssembler, filename: str, title: str, sections: list):
    sections = enforce_schema(sections)
    assembler.set_content(title, sections)
    assembler.export_pdf(filename)
    print(f"   [Rendered Document Frame]")


def generate_all_content(
    llm: ContentGenerator,
    topic: str,
    sections: list,
    words_per_section: int,
) -> list:
    sections = enforce_schema(sections)
    new_sections = []
    context = ""

    for sec in sections:
        print(f"\n   [Phase 1] Generating '{sec['title']}' (~{words_per_section} words)...")
        if not sec["content"]:
            text = llm.generate_section_content(
                topic=topic,
                section_title=sec["title"],
                target_words=words_per_section,
                context=context,
            )
            # FIX 1: strip labels from Phase 1 output too
            text = clean_generated_text(text)
            sec["content"] = text
            words = text.split()
            context = ' '.join(words[-800:]) if len(words) > 800 else text
        new_sections.append(sec)

    return enforce_schema(new_sections)


def humanize_all_content(llm: ContentGenerator, sections: list) -> list:
    sections = enforce_schema(sections)
    new_sections = []
    for sec in sections:
        print(f"\n   [Phase 2] Quality check on '{sec['title']}'...")
        blocks = [p.strip() for p in sec["content"].split("\n\n") if p.strip()]
        improved_blocks = []
        context = ""
        for i, para in enumerate(blocks):
            is_good, reason = llm.evaluate_paragraph(para, context)
            if is_good:
                print(f"      ✓ Paragraph {i+1}/{len(blocks)} - OK")
                improved_blocks.append(para)
            else:
                print(f"      ✗ Paragraph {i+1}/{len(blocks)} - Rewriting ({reason})...")
                rewritten = llm.rewrite_weak_paragraph(para, context, reason)
                improved_blocks.append(rewritten)
            context = context[-400:] + " " + improved_blocks[-1]
        sec["content"] = "\n\n".join(improved_blocks)
        new_sections.append(sec)
    return enforce_schema(new_sections)


# ─────────────────────────────────────────────────────────────────
# FIX 2 + 3 + 5: Heap-based expansion with smarter strategy selection.
#
# Previous bugs:
#   • min(sections, key=len) always picked the same section each iter
#     because expanding its shortest paragraph barely changes total length.
#   • Small expansions (expand_paragraph) were tried first even when
#     the deficit was 1–2 pages, causing 9 iterations of micro-growth
#     that never moved the page counter.
#   • No awareness of per-section deficit vs target, so load was uneven.
#
# DSA fix: use a max-heap keyed by (target_words - current_words).
# After every expansion the heap is updated. The section with the
# largest remaining deficit always gets the next block. This is O(log n)
# per iteration vs O(n) scan, and — more importantly — it guarantees
# the same section is never selected twice in a row unless it truly
# remains the worst deficit.
#
# Strategy escalation:
#   diff > 2 pages  → generate_new_content_block (2-paragraph, 200+ words)
#   diff 1–2 pages  → generate_new_idea_paragraph DIRECTLY (160+ words)
#                     skipping expand_paragraph which only adds 30–75 words
#                     and cannot move the page counter reliably.
# ─────────────────────────────────────────────────────────────────
def _build_deficit_heap(sections: list, target_words: int) -> list:
    """
    Return a max-heap (implemented as min-heap with negated keys) of
    (deficit, section_index) pairs, where deficit = target_words - len(content).
    """
    heap = []
    for i, sec in enumerate(sections):
        deficit = target_words - len(sec["content"].split())
        # negate for max-heap behaviour with Python's min-heap
        heapq.heappush(heap, (-deficit, i))
    return heap


def expand_sections(
    llm: ContentGenerator,
    topic: str,
    sections: list,
    diff: int,
    target_words_per_section: int,
) -> list:
    sections = enforce_schema(sections)
    if not sections:
        return []

    # Build deficit heap — sections with most words still needed come first
    heap = _build_deficit_heap(sections, target_words_per_section)

    # Pop the highest-deficit, non-blocked section
    target = None
    skipped = []
    while heap:
        neg_deficit, idx = heapq.heappop(heap)
        candidate = sections[idx]
        if candidate.get("failed_attempts", 0) < 3:
            target = candidate
            break
        skipped.append((neg_deficit, idx))

    if target is None:
        # All sections blocked → reset and take the highest-deficit one
        print("\n   [!] All sections at attempt limit. Resetting...")
        for s in sections:
            s["failed_attempts"] = 0
        heap = _build_deficit_heap(sections, target_words_per_section)
        _, idx = heapq.heappop(heap)
        target = sections[idx]

    amount_short = abs(diff)

    if amount_short > 2:
        # ── LARGE EXPANSION: 2-paragraph block ──────────────────────────
        print(f"\n   [+] LARGE EXPANSION (Short by {amount_short} pages) -> '{target['title']}'...")
        new_block = llm.generate_new_content_block(
            topic=topic,
            section_title=target["title"],
            context=target["content"],
        )
        # FIX 1: strip labels from expansion blocks
        new_block = clean_generated_text(new_block)
        # FIX 6: threshold 200 → 120 (mistral typically returns 170-240 words)
        if len(new_block.split()) > 120:
            target["content"] = target["content"] + "\n\n" + new_block
            print(f"       Added {len(new_block.split())} words to '{target['title']}'")
            target["failed_attempts"] = 0
        else:
            target["failed_attempts"] = target.get("failed_attempts", 0) + 1
            print(f"       Block too short ({len(new_block.split())} words) - attempt {target['failed_attempts']}/3")

    else:
        # ── SMALL EXPANSION: skip expand_paragraph, go straight to injection ─
        # FIX 5: expand_paragraph adds only 30–75 words, which at 320 words/page
        # means ~0.1–0.2 page gain per LLM call. To reliably gain 1 page we need
        # ≥320 words. generate_new_idea_paragraph asks for 130–190 words; two
        # injections per "small" gap reliably moves the counter.
        print(f"\n   [+] SMALL EXPANSION (Short by {amount_short} pages) -> '{target['title']}'...")
        new_para = llm.generate_new_idea_paragraph(
            topic=topic,
            section_title=target["title"],
            context=target["content"],
        )
        # FIX 1: strip labels
        new_para = clean_generated_text(new_para)

        if len(new_para.split()) > 80:
            target["content"] = target["content"] + "\n\n" + new_para
            print(f"       Injected {len(new_para.split())} word paragraph into '{target['title']}'")
            target["failed_attempts"] = 0
        else:
            # Fallback: try expand_paragraph on the section's shortest block
            print(f"       Injection too short. Trying paragraph expansion...")
            blocks = [p.strip() for p in target["content"].split("\n\n") if p.strip()]
            if blocks:
                candidate_idx = min(range(len(blocks)), key=lambda i: len(blocks[i].split()))
                expanded = llm.expand_paragraph(blocks[candidate_idx])
                expanded = clean_generated_text(expanded)
                growth = len(expanded.split()) - len(blocks[candidate_idx].split())
                if growth > 25:
                    blocks[candidate_idx] = expanded
                    target["content"] = "\n\n".join(blocks)
                    print(f"       Paragraph expanded by {growth} words")
                    target["failed_attempts"] = 0
                else:
                    target["failed_attempts"] = target.get("failed_attempts", 0) + 1
                    print(f"       Expansion failed ({target['failed_attempts']}/3)")
            else:
                target["failed_attempts"] = target.get("failed_attempts", 0) + 1

    return enforce_schema(sections)


def trim_longest_section(sections: list, llm: ContentGenerator = None) -> list:
    sections = enforce_schema(sections)
    if not sections:
        return []

    longest_idx = max(range(len(sections)), key=lambda i: len(sections[i]["content"]))
    target = sections[longest_idx]
    blocks = [b for b in target["content"].split("\n\n") if b.strip()]

    if len(blocks) > 3:
        removed = blocks.pop()
        print(f"   [-] Removed paragraph from '{target['title']}' ({len(removed.split())} words)")
        target["content"] = "\n\n".join(blocks)
    elif len(blocks) > 1 and llm:
        longest_para_idx = max(range(len(blocks)), key=lambda i: len(blocks[i]))
        compressed = llm.compress_paragraph(blocks[longest_para_idx])
        saved = len(blocks[longest_para_idx].split()) - len(compressed.split())
        if saved > 20:
            blocks[longest_para_idx] = compressed
            target["content"] = "\n\n".join(blocks)
            print(f"   [-] Compressed paragraph in '{target['title']}' (saved {saved} words)")
        else:
            print(f"   [-] Compression ineffective - skipping")
    else:
        print(f"   [-] Section too short to trim")

    return enforce_schema(sections)


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
    print(f"\n[Building] {filename}")
    print(f"[Target] {target_pages} pages, {len(sections)} sections")

    sections = enforce_schema(sections)

    paragraphs_per_section, words_per_section = ContentGenerator.calculate_targets(
        target_pages, len(sections), assembler.estimated_words_per_page()
    )
    print(f"[Plan] ~{paragraphs_per_section} paragraphs per section (~{words_per_section} words)")

    # ── Phase 1: Content Generation ───────────────────────────────
    print("\n=== PHASE 1: CONTENT GENERATION ===")
    sections = generate_all_content(llm, title, sections, words_per_section)

    # ── Phase 2: Quality Check (rewrites weak paragraphs) ─────────
    print("\n=== PHASE 2: QUALITY CHECK ===")
    sections = humanize_all_content(llm, sections)

    # ── Phase 3: Visual Generation ────────────────────────────────
    if total_tables > 0 or total_charts > 0:
        print("\n=== PHASE 3: VISUAL GENERATION ===")
        tables_remaining = total_tables
        charts_remaining = total_charts

        for i, sec in enumerate(sections):
            print(f"   Section {i+1}: {sec['title']}")
            visuals = sec.get("visuals", [])

            if tables_remaining > 0:
                print(f"      → Generating table for section...")
                tables_generated = llm.generate_table_for_section(
                    title, sec["title"], sec["content"]
                )
                for table in tables_generated:
                    if tables_remaining > 0:
                        tables_remaining -= 1
                        # Always compute placement: the model just copies the
                        # example number from the JSON template in the prompt.
                        table["insert_after_paragraph"] = llm.determine_visual_placement(
                            sec["content"], "table"
                        )
                        table["type"] = "table"
                        visuals.append(table)
                        print(f"         [+] Table attached: '{table.get('title')}'")
                if not tables_generated:
                    print("         [-] Table generation returned empty for this section.")

            if charts_remaining > 0:
                print(f"      → Generating chart for section...")
                charts_generated = llm.generate_chart_for_section(
                    title, sec["title"], sec["content"]
                )
                for chart in charts_generated:
                    if charts_remaining > 0:
                        charts_remaining -= 1
                        chart["insert_after_paragraph"] = llm.determine_visual_placement(
                            sec["content"], "chart"
                        )
                        chart["type"] = "chart"
                        visuals.append(chart)
                        print(f"         [+] Chart attached: '{chart.get('title')}'")
                if not charts_generated:
                    print("         [-] Chart generation returned empty for this section.")

            sec["visuals"] = visuals

    # ── Initial Render ────────────────────────────────────────────
    print("\n=== INITIAL RENDER ===")
    render(assembler, filename, title, sections)
    # FIX 4: single read after render — store as prev_pages for loop start
    prev_pages = get_page_count(filename)
    print(f"[Initial] {prev_pages} pages generated")

    # ── Page Control Loop ─────────────────────────────────────────
    print("\n=== PAGE CONTROL LOOP ===")
    no_progress_streak = 0

    for iteration in range(1, max_iters + 1):
        # FIX 4: reuse prev_pages from last render — no extra PDF read at top of loop
        actual = prev_pages
        diff = actual - target_pages

        print(f"[Iteration {iteration}/{max_iters}] Pages: {actual} | Target: {target_pages} | Diff: {diff:+d}")

        # Tolerance logic:
        #   diff == 0   → exact hit, always stop
        #   diff == +1  → one page over, acceptable, stop
        #   diff == -1  → one page short, keep expanding (don't stop short)
        #   diff <= -2  → large expansion needed
        #   diff >= +2  → trim needed
        if diff == 0 or diff == 1:
            print("[✓] Target achieved!")
            break

        if diff < 0:
            sections = expand_sections(
                llm, title, sections, diff=diff,
                target_words_per_section=words_per_section,
            )
        elif diff > 1:
            sections = trim_longest_section(sections, llm)

        render(assembler, filename, title, sections)
        # FIX 4: one read per iteration, result stored for next iteration's `actual`
        new_pages = get_page_count(filename)

        # Stall detection — count consecutive iterations with zero page change
        if iteration > 3:
            if new_pages == prev_pages:
                no_progress_streak += 1
                print(f"   [~] No page change (streak: {no_progress_streak}/5)")
            else:
                no_progress_streak = 0

            if no_progress_streak >= 5:
                print("[!] No progress for 5 consecutive iterations. Stopping.")
                break

        prev_pages = new_pages

    final_pages = get_page_count(filename)
    print(f"\n[✓ COMPLETE] {final_pages} pages (you asked for {target_pages})")
    print(f"[✓ SAVED] {filename}\n")
    return sections


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

    topic = (args.topic or input("Topic >: ")).strip()
    if not topic:
        print("[Error] Topic required"); return

    try:
        target_pages = args.pages if args.pages is not None else int(input("Target Pages >: "))
    except ValueError:
        print("[Error] Invalid page count"); return

    if not (1 <= target_pages <= 200):
        print("Page count must be 1–200"); return

    total_tables = args.tables if args.tables is not None else -1
    total_charts = args.charts if args.charts is not None else -1

    if args.visuals and total_tables == -1 and total_charts == -1:
        total_tables, total_charts = 1, 0
    elif total_tables == -1 and total_charts == -1:
        print("\n[Visual Options]")
        try:
            t = input("Total Tables for document (0-5) [default 0]: ").strip()
            total_tables = int(t) if t else 0
            c = input("Total Charts for document (0-5) [default 0]: ").strip()
            total_charts = int(c) if c else 0
        except ValueError:
            print("[!] Invalid input — using defaults: 0 tables, 0 charts")
            total_tables = total_charts = 0

    total_tables = max(0, min(5, total_tables))
    total_charts = max(0, min(5, total_charts))

    variants = max(1, args.variants)

    print("=" * 70)
    print("  📐 SYNTHETIC DOCUMENT FACTORY — LAYOUT DATASET GENERATOR")
    print("=" * 70)
    print(f"  Topic               : {topic}")
    print(f"  Target Pages        : {target_pages}")
    print(f"  Global Table Limit  : {total_tables}")
    print(f"  Global Chart Limit  : {total_charts}")
    print(f"  Layout / Variants   : {args.layout} / {variants}")
    print("=" * 70)

    try:
        llm = ContentGenerator()
    except RuntimeError as e:
        print(f"[Error] {e}")
        sys.exit(1)

    rng = random.Random(args.seed)
    columns = {"single": 1, "double": 2}.get(args.layout)
    assembler = DocumentAssembler(LayoutStyle.random(rng, columns))
    slug = sanitize_topic(topic)
    out_dir = os.path.join("output", slug)
    pdf_dir = os.path.join(out_dir, "pdf")
    os.makedirs(pdf_dir, exist_ok=True)

    num_sections = ContentGenerator.plan_sections(target_pages)
    print(f"\n[Planning] {num_sections} sections for {target_pages} pages...")

    outline = enforce_schema(llm.generate_outline(topic, num_sections))
    print(f"\n[Outline Generated]")
    for i, sec in enumerate(outline):
        print(f"   {i+1}. {sec['title']}")

    try:
        first_pdf = os.path.join(pdf_dir, f"{slug}_1.pdf")
        sections = build_part(
            first_pdf,
            topic, outline, target_pages,
            llm, assembler,
            total_tables=total_tables,
            total_charts=total_charts,
            max_iters=25,
        )
        renders = [assembler.render_info(f"{slug}_1", first_pdf)]

        # Keep the generated text so it can be reused or inspected later
        with open(os.path.join(out_dir, "content.json"), "w", encoding="utf-8") as f:
            json.dump({"title": topic, "sections": sections}, f, ensure_ascii=False, indent=2)

        def describe(a, path):
            st = a.style
            return (f"{st.columns} column(s), {st.font} {st.font_size:g}pt, "
                    f"{st.page_size} → {get_page_count(path)} pages")

        print(f"   [Variant 1] {describe(assembler, first_pdf)}")

        # ── Layout variants: same text, new layout, no extra LLM calls ──
        for v in range(2, variants + 1):
            variant = DocumentAssembler(LayoutStyle.random(rng, columns))
            path = os.path.join(pdf_dir, f"{slug}_{v}.pdf")
            render(variant, path, topic, sections)
            renders.append(variant.render_info(f"{slug}_{v}", path))
            print(f"   [Variant {v}] {describe(variant, path)}")
    except RuntimeError as e:
        print(f"[Error] {e}")
        sys.exit(1)

    print("\n=== EXPORTING DATASET ===")
    stats = export_dataset(out_dir, renders, dpi=args.dpi, preview=args.preview)
    print(f"[✓ DATASET] {stats['pages']} page images, {stats['boxes']} labelled boxes → {out_dir}/")
    for label, count in sorted(stats["counts"].items(), key=lambda kv: -kv[1]):
        print(f"   {label:<15} {count}")


if __name__ == "__main__":
    main()