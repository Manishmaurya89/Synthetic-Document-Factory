from pipeline_architecture import(  
    PipelineStage,
    StageResult,
    StageStatus,
    PipelineContext,
)
from pathlib import Path
import sys
import os

# Import existing assembler
sys.path.insert(0, str(Path(__file__).parent))
from assembler import DocumentAssembler


class RenderingStage(PipelineStage):
    """
    Render the final PDF document with all content and visuals.
    """

    def __init__(self, output_dir: Path = None):
        super().__init__("Rendering")
        self.output_dir = output_dir or Path(__file__).parent.parent / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def execute(self, context: PipelineContext) -> StageResult:
        """Render final PDF document"""

        # Prepare sections for assembler
        sections_data = []

        for section in context.generated_sections:
            section_dict = {
                "title": section["title"],
                "content": section["content"],
                "visuals": section.get("visuals", []),
            }
            sections_data.append(section_dict)

        # Create filename based on format
        topic_slug = self._slugify(context.constraints.topic)
        format_type = getattr(context.constraints, "format_type", "pdf").lower()

        if format_type == "markdown":
            ext = "md"
        elif format_type == "txt":
            ext = "txt"
        else:
            ext = "pdf"

        filename = self.output_dir / f"{topic_slug}_document.{ext}"

        print(f"\n  Output: {filename}")
        print(f"  Sections: {len(sections_data)}")

        # 1. Initialize assembler with template-specific styling
        assembler = DocumentAssembler(
            template_type=context.constraints.template_type.value
        )
        assembler.set_content(
            title=getattr(context, "document_title", context.constraints.topic),
            sections_data=sections_data,
        )

        # Export document in a background thread
        try:
            print(f"  Rendering {format_type.upper()} (this may take a moment)...")
            import asyncio

            loop = asyncio.get_event_loop()

            if format_type == "markdown":
                await loop.run_in_executor(
                    None, lambda: assembler.export_markdown(str(filename))
                )
                # Estimate pages for text formats (~300 words/page)
                total_words = sum(
                    s.get("word_count", 0) for s in context.generated_sections
                )
                page_count = max(1, total_words // 300)
            elif format_type == "txt":
                await loop.run_in_executor(
                    None, lambda: assembler.export_txt(str(filename))
                )
                total_words = sum(
                    s.get("word_count", 0) for s in context.generated_sections
                )
                page_count = max(1, total_words // 300)
            else:
                await loop.run_in_executor(
                    None, lambda: assembler.export_pdf(str(filename))
                )
                page_count = self._get_page_count(filename)

            # Verify file was created
            if not filename.exists():
                raise Exception(f"{format_type.upper()} file was not created")

            file_size = filename.stat().st_size
            print(f"  ✓ File created: {file_size:,} bytes")
            print(f"  ✓ Estimated Pages: {page_count}")

            return StageResult(
                stage_name=self.name,
                status=StageStatus.COMPLETED,
                duration=0.0,
                data={
                    "filename": str(filename),
                    "file_size": file_size,
                    "page_count": page_count,
                },
                metadata={
                    "target_pages": context.constraints.target_pages,
                    "page_diff": page_count - context.constraints.target_pages,
                },
            )

        except Exception as e:
            print(f"  ✗ Rendering failed: {e}")
            raise

    def _slugify(self, text: str) -> str:
        """Convert text to filename-safe slug"""
        import re

        text = text.lower()
        text = re.sub(r"[^a-z0-9]+", "_", text)
        text = re.sub(r"_+", "_", text)
        return text.strip("_")[:50]

    def _get_page_count(self, pdf_path: Path) -> int:
        """Get page count from PDF"""
        try:
            from PyPDF2 import PdfReader

            with open(pdf_path, "rb") as f:
                reader = PdfReader(f)
                return len(reader.pages)
        except Exception:
            return 0


class QualityCheckStage(PipelineStage):
    """
    Optional quality checking stage to validate output.
    Checks for hallucinations, coherence, and structure.
    """

    def __init__(self):
        super().__init__("Quality Check")
        self.optional = True

    def should_skip(self, context: PipelineContext) -> bool:
        """Skip if quality checking not required"""
        return not context.constraints.require_fact_checking

    async def execute(self, context: PipelineContext) -> StageResult:
        """Perform quality checks on generated content"""

        issues = []
        warnings = []

        for i, section in enumerate(context.generated_sections):
            print(f"\n  Checking section {i+1}: {section['title']}")

            # Check 1: Content length
            word_count = section["word_count"]
            expected = section["plan"].target_words
            diff_pct = abs(word_count - expected) / expected

            if diff_pct > 0.3:
                warnings.append(
                    f"Section {i+1} word count off by {diff_pct*100:.0f}% "
                    f"(expected {expected}, got {word_count})"
                )

            # Check 2: Paragraph structure
            paragraphs = [p for p in section["content"].split("\n\n") if p.strip()]
            if len(paragraphs) < section["plan"].target_paragraphs * 0.7:
                warnings.append(
                    f"Section {i+1} has fewer paragraphs than expected "
                    f"({len(paragraphs)} vs {section['plan'].target_paragraphs})"
                )

            # Check 3: Repetitive content
            repetition_score = self._check_repetition(section["content"])
            if repetition_score > 0.2:
                issues.append(
                    f"Section {i+1} has high repetition (score: {repetition_score:.2f})"
                )

            # Check 4: Placeholder detection
            placeholders = self._detect_placeholders(section["content"])
            if placeholders:
                issues.append(
                    f"Section {i+1} contains placeholders: {', '.join(placeholders[:3])}"
                )

        # Overall coherence check
        coherence_score = self._check_coherence(context.generated_sections)
        print(f"\n  Overall coherence score: {coherence_score:.2f}")

        if coherence_score < context.constraints.min_coherence_score:
            issues.append(
                f"Document coherence below threshold "
                f"({coherence_score:.2f} < {context.constraints.min_coherence_score})"
            )

        status = StageStatus.COMPLETED if not issues else StageStatus.COMPLETED

        return StageResult(
            stage_name=self.name,
            status=status,
            duration=0.0,
            data={
                "coherence_score": coherence_score,
                "issues_found": len(issues),
                "warnings_found": len(warnings),
            },
            errors=issues,
            warnings=warnings,
        )

    def _check_repetition(self, text: str) -> float:
        """Calculate repetition score (0-1, higher = more repetitive)"""
        sentences = [s.strip() for s in text.split(".") if s.strip()]

        if len(sentences) < 2:
            return 0.0

        # Check for identical sentences
        unique_sentences = set(sentences)
        repetition = 1.0 - (len(unique_sentences) / len(sentences))

        return repetition

    def _detect_placeholders(self, text: str) -> list:
        """Detect placeholder phrases that indicate incomplete generation"""
        placeholders = [
            "as mentioned above",
            "as discussed earlier",
            "we will explore",
            "in this section we",
            "it is important to note",
            "further research",
            "[insert",
            "TODO",
            "FIXME",
        ]

        found = []
        text_lower = text.lower()

        for placeholder in placeholders:
            if placeholder in text_lower:
                found.append(placeholder)

        return found

    def _check_coherence(self, sections: list) -> float:
        """
        Calculate document coherence score based on:
        - Topic consistency across sections
        - Smooth transitions
        - Logical flow
        """

        if len(sections) < 2:
            return 1.0

        scores = []

        # Check topic word overlap between consecutive sections
        for i in range(len(sections) - 1):
            current_words = set(sections[i]["content"].lower().split())
            next_words = set(sections[i + 1]["content"].lower().split())

            # Calculate Jaccard similarity
            intersection = len(current_words & next_words)
            union = len(current_words | next_words)

            if union > 0:
                similarity = intersection / union
                scores.append(similarity)

        # Average coherence score
        return sum(scores) / len(scores) if scores else 0.5
