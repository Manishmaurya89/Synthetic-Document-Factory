import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline_architecture import (
    PipelineContext,
    DocumentTemplate,
    GenerationConstraints,
    DocumentType,
    SectionPlan,
)
from stages.stage_rendering import RenderingStage
import asyncio


async def test_assembler():
    context = PipelineContext(
        constraints=GenerationConstraints(
            topic="Test Topic",
            target_pages=1,
            template_type=DocumentType.ARTICLE,
            total_tables=1,
            total_charts=0,
            total_images=0,
        ),
        template=DocumentTemplate(
            name="Test Template",
            type=DocumentType.ARTICLE,
            description="Test",
            sections=[],
        ),
        section_plans=[],
    )

    plan = SectionPlan(
        index=0, title="Test Section", target_paragraphs=2, target_words=200
    )

    # Mock table with asymmetrical rows
    mock_table = {
        "type": "table",
        "title": "Test Table",
        "insert_after_paragraph": 1,
        "data": {
            "headers": ["Col 1", "Col 2", "Col 3"],
            "rows": [
                ["A", "B", "C"],
                ["Short Row 1", "Short Row 2"],  # Shorter than header
                [
                    "Long Row 1",
                    "Long Row 2",
                    "Long Row 3",
                    "Extra",
                ],  # Longer than header
            ],
        },
    }

    context.generated_sections = [
        {
            "title": "Test Section",
            "content": "Paragraph 1.\n\nParagraph 2.",
            "visuals": [mock_table],
            "word_count": 200,
            "plan": plan,
        }
    ]

    context.output_dir = Path("output")
    os.makedirs(context.output_dir, exist_ok=True)

    stage = RenderingStage()
    result = await stage.execute(context)

    print("Assembler Result Status:", result.status)
    if result.errors:
        print("Assembler Errors:", result.errors)
    else:
        print("Success! Created:", result.data.get("filename"))


if __name__ == "__main__":
    asyncio.run(test_assembler())
