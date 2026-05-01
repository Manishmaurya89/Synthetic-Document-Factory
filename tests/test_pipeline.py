#!/usr/bin/env python3
"""
Test Script for Document Generation Pipeline
Runs a series of tests to verify all components work correctly
"""

import asyncio
import sys
from pathlib import Path

# Add project directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline_architecture import (
    Pipeline,
    PipelineContext,
    GenerationConstraints,
    DocumentType,
)
from templates import get_template, list_templates
from stages.stage_planning import PlanningStage
from stages.stage_content import ContentGenerationStage
from stages.stage_visuals import VisualCreationStage
from stages.stage_rendering import RenderingStage


async def test_planning_stage():
    """Test planning stage in isolation"""
    print("\n" + "=" * 70)
    print("TEST 1: Planning Stage")
    print("=" * 70)

    template = get_template(DocumentType.ARTICLE)
    constraints = GenerationConstraints(
        topic="Artificial Intelligence",
        target_pages=10,
        template_type=DocumentType.ARTICLE,
        total_tables=2,
        total_charts=1,
        total_images=0,
        primary_model="llama3",
    )

    context = PipelineContext(
        constraints=constraints,
        template=template,
        section_plans=[],
    )

    stage = PlanningStage()
    result = await stage.run(context)

    assert result.status.value == "completed", "Planning stage failed"
    assert len(context.section_plans) > 0, "No section plans created"

    print(f"✓ Planning stage passed")
    print(f"  - Created {len(context.section_plans)} section plans")
    print(
        f"  - Total words allocated: {sum(p.target_words for p in context.section_plans)}"
    )

    return True


async def test_template_system():
    """Test template registry"""
    print("\n" + "=" * 70)
    print("TEST 2: Template System")
    print("=" * 70)

    templates = list_templates()

    assert len(templates) >= 6, "Missing templates"

    for template_type in DocumentType:
        template = get_template(template_type)
        assert template is not None, f"Template {template_type} not found"
        assert len(template.sections) > 0, f"Template {template_type} has no sections"

    print(f"✓ Template system passed")
    print(f"  - {len(templates)} templates available")
    print(f"  - All templates have sections")

    return True


async def test_visual_creation():
    """Test visual creation stage"""
    print("\n" + "=" * 70)
    print("TEST 3: Visual Creation")
    print("=" * 70)

    from stages.stage_visuals import VisualCreationStage

    # Create mock section
    mock_section = {
        "title": "Test Section",
        "content": """
        The American Civil War was a major conflict. Abraham Lincoln led the Union forces
        while Jefferson Davis commanded the Confederate forces. The Battle of Gettysburg
        was a turning point. General Ulysses Grant played a crucial role. The war lasted
        from 1861 to 1865 and resulted in significant casualties.
        """,
        "plan": type(
            "Plan",
            (),
            {
                "tables_allocated": 1,
                "charts_allocated": 1,
                "images_allocated": 0,
                "target_paragraphs": 3,
                "image_keywords": [],
            },
        )(),
        "visuals": [],
    }

    context = PipelineContext(
        constraints=GenerationConstraints(
            topic="Civil War",
            target_pages=5,
            template_type=DocumentType.ARTICLE,
            primary_model="llama3",
        ),
        template=get_template(DocumentType.ARTICLE),
        section_plans=[],
        generated_sections=[mock_section],
    )

    stage = VisualCreationStage(unsplash_access_key=None)
    result = await stage.run(context)

    assert result.status.value == "completed", "Visual creation failed"

    visuals = mock_section["visuals"]
    tables = [v for v in visuals if v["type"] == "table"]
    charts = [v for v in visuals if v["type"] == "chart"]

    print(f"✓ Visual creation passed")
    print(f"  - Created {len(tables)} table(s)")
    print(f"  - Created {len(charts)} chart(s)")

    if tables:
        print(f"  - Table has {len(tables[0]['data']['headers'])} columns")
        print(f"  - Table has {len(tables[0]['data']['rows'])} rows")

    return True


async def test_minimal_pipeline():
    """Test minimal end-to-end pipeline (no LLM)"""
    print("\n" + "=" * 70)
    print("TEST 4: Minimal Pipeline (Planning + Visuals + Rendering)")
    print("=" * 70)

    template = get_template(DocumentType.ARTICLE)
    constraints = GenerationConstraints(
        topic="Test Document",
        target_pages=3,
        template_type=DocumentType.ARTICLE,
        total_tables=1,
        total_charts=0,
        total_images=0,
        primary_model="llama3",
    )

    context = PipelineContext(
        constraints=constraints,
        template=template,
        section_plans=[],
    )

    # Stage 1: Planning
    planning = PlanningStage()
    await planning.run(context)

    # Stage 2: Mock content generation (skip LLM)
    for i, plan in enumerate(context.section_plans):
        mock_content = f"""
        This is paragraph one for {plan.title}. It discusses the fundamental aspects
        of the topic with specific examples and detailed explanations that provide
        comprehensive coverage of the subject matter.
        
        This is paragraph two continuing the discussion. It builds upon the previous
        points and introduces new perspectives that enhance understanding of the
        complex topics being addressed in this section.
        
        This is paragraph three concluding this section. It synthesizes the key points
        and provides actionable insights for readers who want to learn more about
        this fascinating subject area.
        """

        context.generated_sections.append(
            {
                "title": plan.title,
                "content": mock_content.strip(),
                "visuals": [],
                "word_count": len(mock_content.split()),
                "plan": plan,
            }
        )

    # Stage 3: Visual creation
    visuals = VisualCreationStage()
    await visuals.run(context)

    # Stage 4: Rendering
    rendering = RenderingStage(output_dir=Path(__file__).parent / "test_output")
    result = await rendering.run(context)

    assert result.status.value == "completed", "Rendering failed"

    output_file = Path(result.data["filename"])
    assert output_file.exists(), "Output PDF not created"

    print(f"✓ Minimal pipeline passed")
    print(f"  - Output: {output_file}")
    print(f"  - File size: {output_file.stat().st_size:,} bytes")
    print(f"  - Pages: {result.data['page_count']}")

    return True


async def test_cache_system():
    """Test caching functionality"""
    print("\n" + "=" * 70)
    print("TEST 5: Cache System")
    print("=" * 70)

    context = PipelineContext(
        constraints=GenerationConstraints(
            topic="Test",
            target_pages=5,
            template_type=DocumentType.ARTICLE,
            primary_model="llama3",
        ),
        template=get_template(DocumentType.ARTICLE),
        section_plans=[],
    )

    # Test cache write
    test_data = {"key": "value", "number": 123}
    context.set_cached("test_stage", test_data, "arg1", "arg2")

    # Test cache read
    retrieved = context.get_cached("test_stage", "arg1", "arg2")

    assert retrieved is not None, "Cache read failed"
    assert retrieved == test_data, "Cache data mismatch"

    # Test cache miss
    missing = context.get_cached("test_stage", "different", "args")
    assert missing is None, "Cache should miss for different args"

    print(f"✓ Cache system passed")
    print(f"  - Cache write: OK")
    print(f"  - Cache read: OK")
    print(f"  - Cache miss: OK")

    return True


async def run_all_tests():
    """Run all tests"""
    print("\n" + "=" * 70)
    print("DOCUMENT GENERATION PIPELINE - TEST SUITE")
    print("=" * 70)

    tests = [
        ("Template System", test_template_system),
        ("Planning Stage", test_planning_stage),
        ("Visual Creation", test_visual_creation),
        ("Cache System", test_cache_system),
        ("Minimal Pipeline", test_minimal_pipeline),
    ]

    results = []

    for name, test_func in tests:
        try:
            success = await test_func()
            results.append((name, success, None))
        except Exception as e:
            print(f"\n✗ {name} failed: {e}")
            results.append((name, False, str(e)))

    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, success, _ in results if success)
    failed = len(results) - passed

    for name, success, error in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status}: {name}")
        if error:
            print(f"       Error: {error}")

    print(f"\nTotal: {len(results)} tests")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print("=" * 70)

    return failed == 0


def main():
    """Main entry point"""
    success = asyncio.run(run_all_tests())

    if success:
        print("\n🎉 All tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
