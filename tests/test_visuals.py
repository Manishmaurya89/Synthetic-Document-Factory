import asyncio
import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline_architecture import PipelineContext, GenerationConstraints, DocumentType
from stages.stage_visuals import VisualCreationStage

# Define a mock document structure for testing
MOCK_TOPIC = "The Rise of Artificial Intelligence in Healthcare"
MOCK_SECTION_TITLE = "Adoption Rates and Financial Impact"
MOCK_CONTENT = """
The adoption of artificial intelligence in the healthcare sector has accelerated dramatically over the past five years. In 2019, only 15% of major hospital networks had integrated AI diagnostics into their primary workflows. By 2024, this figure had surged to 68%. This rapid integration is primarily driven by significant cost reductions and improved patient outcomes. 

Financially, hospitals utilizing AI-driven triage systems report an average cost savings of $2.4 million annually. Wait times in emergency departments have decreased by an average of 45 minutes, leading to higher patient satisfaction scores. Conversely, institutions relying on traditional triage methods have seen operational costs increase by 12% over the same period, with wait times remaining stagnant. 

Furthermore, diagnostic accuracy for early-stage oncology screening has improved significantly. AI models demonstrate a 94.2% accuracy rate in detecting micro-tumors, compared to the 81.5% accuracy rate of human radiologists unaided by algorithmic tools. This disparity highlights the growing necessity of machine learning integrations in standard medical practices.
"""


async def test_visual_generation():
    print("=" * 60)
    print("🧪 Testing Visual Generation (Charts & Tables)")
    print("=" * 60)

    # Initialize the stage
    visual_stage = VisualCreationStage(
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        model="llama3",  # Or whatever model is running locally
    )

    print(f"\n[1] Testing Professional Table Generation...")
    table_data = await visual_stage._create_table_for_section(
        section_title=MOCK_SECTION_TITLE, section_content=MOCK_CONTENT, topic=MOCK_TOPIC
    )

    if table_data:
        print("✅ Table generated successfully.")
        print(json.dumps(table_data, indent=2))

        # Validate table structure
        if "data" in table_data and "headers" in table_data["data"]:
            print("✅ Table structure is valid.")
        else:
            print("❌ Table structure is invalid.")
    else:
        print("❌ Table generation failed or returned None.")

    print(f"\n[2] Testing Matplotlib Chart Generation...")
    chart_data = await visual_stage._create_chart_for_section(
        section_title=MOCK_SECTION_TITLE, section_content=MOCK_CONTENT, topic=MOCK_TOPIC
    )

    if chart_data:
        print("✅ Chart data generated successfully.")
        print(json.dumps(chart_data, indent=2))

        # Validate chart image file
        chart_path = chart_data.get("path", "")
        if chart_path and Path(chart_path).exists():
            file_size = Path(chart_path).stat().st_size
            print(
                f"✅ Chart image saved successfully at: {chart_path} ({file_size} bytes)"
            )
        else:
            print(f"❌ Chart image was not saved properly. Path: {chart_path}")
    else:
        print("❌ Chart generation failed or returned None.")

    print("\n" + "=" * 60)
    print("🏁 Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_visual_generation())
