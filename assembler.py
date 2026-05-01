import markdown
import re
import json
import io
import hashlib
from pathlib import Path
from xhtml2pdf import pisa
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.pagesizes import A4
from pypdf import PdfReader, PdfWriter


class DocumentAssembler:
    def __init__(self, template_type: str = "article"):
        self.document_title = ""
        self.sections_data = []
        self.template_type = template_type

    def set_content(self, title: str, sections_data: list):
        self.document_title = title
        self.sections_data = sections_data

    def _char_script(self, ch: str) -> str:
        """Return a script identifier for a character."""
        o = ord(ch)
        if 0x0900 <= o <= 0x097F:  # Devanagari (Hindi)
            return "devanagari"
        if 0x0600 <= o <= 0x06FF:  # Arabic / Urdu
            return "arabic"
        if 0x0C00 <= o <= 0x0C7F:  # Telugu
            return "telugu"
        return "latin"

    def _is_hindi(self, text):
        return any(0x0900 <= ord(c) <= 0x097F for c in str(text))

    def _is_urdu(self, text):
        return any(0x0600 <= ord(c) <= 0x06FF for c in str(text))

    def _is_telugu(self, text):
        return any(0x0C00 <= ord(c) <= 0x0C7F for c in str(text))

    def _wrap_scripts(self, text):
        """Wrap each script-run in the correct font span (character-level)."""
        if not text:
            return text

        # Check if text has ANY non-latin characters at all
        has_native = any(self._char_script(c) != "latin" for c in text if c.strip())
        if not has_native:
            return text  # pure English — no wrapping needed

        # Build runs of consecutive same-script characters
        runs = []
        current_script = None
        current_buf = []

        for ch in text:
            if ch in (' ', '\n', '\r', '\t'):
                # Whitespace — attach to current run
                current_buf.append(ch)
                continue
            script = self._char_script(ch)
            if script != current_script:
                if current_buf:
                    runs.append((current_script, ''.join(current_buf)))
                current_script = script
                current_buf = [ch]
            else:
                current_buf.append(ch)
        if current_buf:
            runs.append((current_script, ''.join(current_buf)))

        # Build HTML with per-run span tags
        html_parts = []
        for script, run_text in runs:
            escaped = run_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            if script == "latin" or script is None:
                html_parts.append(escaped)
            else:
                html_parts.append(f'<span class="{script}">{escaped}</span>')

        return ''.join(html_parts)


    @staticmethod
    def _clean(text: str) -> str:
        if not isinstance(text, str):
            text = str(text)
        text = re.sub(r"```[a-zA-Z]*\n", "", text)
        text = re.sub(r"```", "", text)
        text = text.replace("\r\n", "\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _build_toc(self) -> str:
        """Simple TOC without page numbers."""
        lines = ['<div class="toc-page">']
        lines.append(f'<div class="toc-doc-title">{self.document_title}</div>')
        lines.append(f'<div class="toc-doc-subtitle">— Table of Contents —</div>')
        lines.append('<hr class="toc-divider"/>')
        lines.append('<ul class="toc-list">')
        for i, sec in enumerate(self.sections_data, start=1):
            title = sec.get("title", f"Section {i}")
            # Wrap title if multilingual
            title = self._wrap_scripts(title)
            lines.append(f'<li class="toc-item"><b>{i}.</b> {title}</li>')
        lines.append("</ul>")
        lines.append("</div>")
        return "\n".join(lines)

    def _render_table(self, table_data: dict) -> str:
        size_class = table_data.get("size", "medium") or "medium"
        title = table_data.get("title", "")
        caption = table_data.get("caption", "")
        data = table_data.get("data", {})
        headers = data.get("headers", [])
        rows = data.get("rows", [])
        html = '\n\n<div class="table-container">\n'
        if title:
            html += f'<div class="table-title"><b>{title}</b></div>\n'
        html += f'<table class="{size_class}" style="width: 100%; table-layout: fixed;">\n<tr>\n'
        header_len = len(headers)
        col_width = int(100 / header_len) if header_len > 0 else 100
        for h in headers:
            html += (
                f'  <th style="width: {col_width}%; word-wrap: break-word;">{h}</th>\n'
            )
        html += "</tr>\n"
        for row in rows:
            html += "<tr>\n"
            # Ensure row length matches header length to prevent xhtml2pdf crashes
            row_cells = list(row)
            while len(row_cells) < header_len:
                row_cells.append("")
            row_cells = row_cells[:header_len]

            for cell in row_cells:
                cell_val = str(cell) if cell is not None else ""
                # Escape HTML characters to prevent breaking xhtml2pdf parser
                import html as html_lib

                cell_val = html_lib.escape(cell_val)
                html += f'  <td style="width: {col_width}%; word-wrap: break-word;">{cell_val}</td>\n'
            html += "</tr>\n"
        html += "</table>\n"
        if caption:
            html += f'<div class="table-caption">{caption}</div>\n'
        html += "</div>\n\n"
        return html

    def _render_chart_image(self, chart_data: dict) -> str:
        path = chart_data.get("path", "")
        title = chart_data.get("title", "")
        caption = chart_data.get("caption", "")

        if not path:
            return ""

        html = f'\n\n<div class="chart-container">\n'
        if title:
            html += f'  <div class="chart-title"><b>{title}</b></div>\n'
        html += f'  <img src="{path}" width="500" />\n'
        if caption:
            html += f'  <div class="chart-caption">{caption}</div>\n'
        html += "</div>\n\n"
        return html

    def _render_image(self, image_data: dict) -> str:
        path = image_data.get("path", "")
        caption = image_data.get("caption", "")
        if not caption:
            caption = f"Illustration of {image_data.get('keyword', 'subject')}"

        # xhtml2pdf works best with absolute paths directly
        html = f'\n\n<div class="image-container">\n'
        html += f'  <img src="{path}" width="500" />\n'
        html += f'  <div class="image-caption">{caption}</div>\n'
        html += "</div>\n\n"
        return html

    def _insert_visuals_in_content(self, content: str, visuals: list) -> str:
        if not visuals:
            return content
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        clean_visuals = []
        for v in visuals:
            if not isinstance(v, dict):
                continue
            pos = v.get("insert_after_paragraph")
            if pos is None or not isinstance(pos, (int, float)):
                pos = len(paragraphs)
            v["insert_after_paragraph"] = int(pos)
            clean_visuals.append(v)
        sorted_visuals = sorted(
            clean_visuals, key=lambda v: v["insert_after_paragraph"], reverse=True
        )
        for visual in sorted_visuals:
            insert_pos = min(visual["insert_after_paragraph"], len(paragraphs))
            v_type = visual.get("type")
            if v_type == "table":
                v_html = self._render_table(visual)
            elif v_type == "chart":
                v_html = self._render_chart_image(visual)
            elif v_type == "image":
                v_html = self._render_image(visual)
            else:
                continue

            if insert_pos < len(paragraphs):
                paragraphs.insert(insert_pos + 1, v_html)
            else:
                paragraphs.append(v_html)
        return "\n\n".join(paragraphs)

    def _build_cover_page(self) -> str:
        display_type = self.template_type.replace("_", " ").title()

        lines = ['<div class="cover-page">']
        if display_type:
            lines.append(f'<div class="doc-type-label">{display_type}</div>')
        lines.append(f'<div class="cover-doc-title">{self.document_title}</div>')
        lines.append("</div>")
        return "\n".join(lines)

    def _build_markdown(self) -> str:
        # We don't need the title here anymore because it's on the cover page
        parts = []
        for i, sec in enumerate(self.sections_data):
            if i > 0:
                parts.append("\n<div style='page-break-before: always;'></div>\n")
            # Ensure section titles are Title Cased
            sec_title = sec["title"].title()
            parts.append(f"\n## {self._wrap_scripts(sec_title)}\n")
            content = self._clean(sec.get("content", ""))
            visuals = sec.get("visuals", [])

            if content:
                # Wrap each paragraph if it contains multilingual characters
                paragraphs = content.split("\n\n")
                wrapped_paras = [self._wrap_scripts(p) for p in paragraphs]
                content = "\n\n".join(wrapped_paras)
                parts.append(f"\n{self._insert_visuals_in_content(content, visuals)}\n")
        return "\n".join(parts)

    @staticmethod
    def _stamp_page_numbers(pdf_bytes: bytes) -> bytes:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        num_pages = len(reader.pages)
        page_w, page_h = A4
        overlay_buf = io.BytesIO()
        c = rl_canvas.Canvas(overlay_buf, pagesize=A4)
        for page_index in range(num_pages):
            if page_index > 0:
                page_label = str(page_index)
                c.setFont("Helvetica", 10)
                c.setFillColorRGB(0.33, 0.33, 0.33)
                c.drawRightString(page_w - 43, 32, page_label)
            c.showPage()
        c.save()
        overlay_buf.seek(0)
        overlay_reader = PdfReader(overlay_buf)
        writer = PdfWriter()
        for i, page in enumerate(reader.pages):
            page.merge_page(overlay_reader.pages[i])
            writer.add_page(page)
        out_buf = io.BytesIO()
        writer.write(out_buf)
        return out_buf.getvalue()

    def export_pdf(self, output_path: str):
        """Simplified single-pass render without TOC page numbers."""
        fonts_dir = Path(__file__).parent / "fonts"

        self.css = f"""
        @font-face {{
            font-family: 'Devanagari';
            src: url('{fonts_dir}/NotoSansDevanagari.ttf');
        }}
        @font-face {{
            font-family: 'Arabic';
            src: url('{fonts_dir}/NotoNaskhArabic-Regular.ttf');
        }}
        @font-face {{
            font-family: 'Telugu';
            src: url('{fonts_dir}/NotoSansTelugu-Regular.ttf');
        }}
        
        @page {{ size: A4; margin: 1in; }}
        body {{ 
            font-family: Georgia, serif; 
            font-size: 12pt; 
            line-height: 1.5; 
            color: #111; 
        }}
        .devanagari, span.devanagari {{ font-family: 'Devanagari', serif; }}
        .arabic, span.arabic {{ font-family: 'Arabic', serif; direction: rtl; }}
        .telugu, span.telugu {{ font-family: 'Telugu', serif; }}
        
        h1 {{ font-size: 24pt; text-align: center; margin-top: 60px; }}
        h2 {{ font-size: 16pt; margin-top: 22px; font-weight: bold; }}
        /* Cover Page Styling */
        .cover-page {{ text-align: center; margin-top: 150px; page-break-after: always; }}
        .doc-type-label {{ font-size: 16pt; color: #555; margin-bottom: 30px; }}
        .cover-doc-title {{ font-size: 32pt; font-weight: bold; line-height: 1.2; color: #111; }}
        
        .toc-page {{ page-break-after: always; padding-top: 30px; }}
        .toc-doc-title {{ font-size: 26pt; font-weight: bold; text-align: center; }}
        .toc-doc-subtitle {{ font-size: 14pt; text-align: center; color: #666; margin-bottom: 20px; }}
        .toc-list {{ list-style: none; padding: 0; }}
        table {{ width: 95%; margin: 20px 0; table-layout: fixed; word-wrap: break-word; }}
        th {{ background-color: #f0f0f0; padding: 4px; border: 0.5px solid #ddd; word-wrap: break-word; }}
        td {{ padding: 4px; border: 0.5px solid #ddd; word-wrap: break-word; }}
        .image-container {{ text-align: center; margin: 20px 0; page-break-inside: avoid; }}
        .image-caption {{ font-size: 10pt; font-style: italic; margin-top: 5px; color: #555; }}
        
        /* Research Paper Specific Styling */
        .research-paper {{ font-family: "Times New Roman", Times, serif, 'Devanagari', 'Arabic', 'Telugu'; font-size: 11pt; }}
        .research-paper .cover-page {{ margin-top: 100px; }}
        .research-paper h2 {{ font-size: 13pt; border-bottom: 1px solid #333; padding-bottom: 5px; }}
        .research-paper .abstract {{ font-style: italic; margin: 0 40px 20px 40px; padding: 10px; background-color: #f9f9f9; border: 0.5pt solid #ccc; }}
        .research-paper p {{ text-align: justify; text-indent: 0.5in; margin-bottom: 0; }}
        .research-paper .toc-page {{ display: none; }} /* Real research papers often skip TOC */
        """
        cover_html = self._build_cover_page()
        toc_html = self._build_toc()
        body_md = self._build_markdown()
        body_html = markdown.markdown(body_md, extensions=["extra"])

        full_html = f"<html><head><style>{self.css}</style></head><body>{cover_html}{toc_html}{body_html}</body></html>"

        # Apply template-specific classes
        if self.template_type == "research_paper":
            full_html = full_html.replace("<body>", '<body class="research-paper">')
            # Specialized abstract styling
            full_html = full_html.replace("## Abstract", "## ABSTRACT")
            full_html = full_html.replace(
                "ABSTRACT</h2>\n<p>", 'ABSTRACT</h2>\n<div class="abstract"><p>'
            )
            # Close the div after the abstract paragraph(s)
            # This is a bit hacky but works for the current structure
            full_html = re.sub(
                r'(ABSTRACT</h2>\n<div class="abstract">.*?)</p>',
                r"\1</p></div>",
                full_html,
                flags=re.DOTALL,
            )

        try:
            pdf_buf = io.BytesIO()
            pisa.CreatePDF(full_html, dest=pdf_buf)

            # Stamp pages and write PDF
            pdf_bytes = self._stamp_page_numbers(pdf_buf.getvalue())
            with open(output_path, "w+b") as out_pdf:
                out_pdf.write(pdf_bytes)
        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f"[Assembler] PDF generation failed: {e}")

    def export_markdown(self, output_path: str):
        """Export document as raw Markdown."""
        parts = [f"# {self.document_title}\n"]
        for sec in self.sections_data:
            sec_title = sec["title"].title()
            parts.append(f"## {sec_title}\n")
            content = self._clean(sec.get("content", ""))
            if content:
                parts.append(f"{content}\n")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))

    def export_txt(self, output_path: str):
        """Export document as plain text."""
        parts = [f"{self.document_title}\n{'='*len(self.document_title)}\n"]
        for sec in self.sections_data:
            sec_title = sec["title"].title()
            parts.append(f"\n{sec_title}\n{'-'*len(sec_title)}\n")
            content = self._clean(sec.get("content", ""))
            if content:
                parts.append(f"{content}\n")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
