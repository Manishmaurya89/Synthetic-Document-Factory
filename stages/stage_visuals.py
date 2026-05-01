import os
import requests
import random
import hashlib
import json
import asyncio
from typing import List, Dict, Optional, Any
from pathlib import Path
from pipeline_architecture import (
    PipelineStage,
    StageResult,
    StageStatus,
    PipelineContext,
)
import re


class VisualCreationStage(PipelineStage):
  

    def __init__(
        self, pexels_api_key: str = None, ollama_url: str = None, model: str = "llama3"
    ):
        super().__init__("Visual Creation")
        self.pexels_key = pexels_api_key
        self.ollama_url = ollama_url or os.getenv(
            "OLLAMA_URL", "http://localhost:11434"
        )
        self.model = model
        self.image_cache_dir = Path(__file__).parent.parent / ".cache" / "images"
        self.image_cache_dir.mkdir(parents=True, exist_ok=True)

    async def execute(self, context: PipelineContext) -> StageResult:
        """Create all visual elements for document"""

        tables_created = 0
        charts_created = 0
        images_fetched = 0

        for section in context.generated_sections:
            plan = section["plan"]
            visuals = []

            print(f"\n  Section: {section['title']}")

            # Create tables
            if plan.tables_allocated > 0:
                print(f"    Creating {plan.tables_allocated} meaningful table(s)")
                table = await self._create_table_for_section(
                    section_title=section["title"],
                    section_content=section["content"],
                    topic=context.constraints.topic,
                )
                if table:
                    visuals.append(table)
                    tables_created += 1

            # Create charts
            if plan.charts_allocated > 0:
                print(f"    Creating {plan.charts_allocated} meaningful chart(s)")
                chart = await self._create_chart_for_section(
                    section_title=section["title"],
                    section_content=section["content"],
                    topic=context.constraints.topic,
                )
                if chart:
                    visuals.append(chart)
                    charts_created += 1

            # Fetch images
            if plan.images_allocated > 0 and self.pexels_key:
                print(f"    Fetching {plan.images_allocated} unique image(s)")
                for keyword in plan.image_keywords[: plan.images_allocated]:
                    result = await self._fetch_image(keyword, context)
                    if result:
                        image_path, raw_caption = result

                        # Generate professional contextual caption using LLM
                        caption_prompt = f"Rewrite this raw image description into a single, professional, academic sentence that fits an article about '{context.constraints.topic}' (Section: {section['title']}). DO NOT use personal language like 'I shot this'. Raw description: {raw_caption}\nOUTPUT ONLY THE SENTENCE. NO EXPLANATIONS. NO PREFIXES LIKE 'Here is the rewritten sentence:'."
                        loop = asyncio.get_event_loop()
                        try:
                            cap_resp = await loop.run_in_executor(
                                None,
                                lambda: requests.post(
                                    f"{self.ollama_url}/api/generate",
                                    json={
                                        "model": self.model,
                                        "prompt": caption_prompt,
                                        "stream": False,
                                    },
                                    timeout=15,
                                ),
                            )
                            cap_data = cap_resp.json()
                            caption = cap_data.get("response", "").strip()
                            # Aggressive cleanup of LLM conversational artifacts
                            import re

                            caption = re.sub(
                                r"^(Here is.*?sentence:|Output:|Caption:|Here is).*?\s*",
                                "",
                                caption,
                                flags=re.IGNORECASE,
                            ).strip()
                            caption = caption.strip('"')
                        except:
                            caption = f"Figure illustrating {keyword} in the context of {section['title'].lower()}."

                        max_pos = plan.target_paragraphs - 1
                        insert_pos = random.randint(1, max_pos) if max_pos >= 1 else 0
                        visuals.append(
                            {
                                "type": "image",
                                "path": image_path,
                                "keyword": keyword,
                                "caption": caption,
                                "insert_after_paragraph": insert_pos,
                            }
                        )
                        images_fetched += 1

            section["visuals"] = visuals

        return StageResult(
            stage_name=self.name,
            status=StageStatus.COMPLETED,
            duration=0.0,
            data={
                "tables": tables_created,
                "charts": charts_created,
                "images": images_fetched,
            },
        )

    async def _fetch_image(
        self, keyword: str, context: PipelineContext
    ) -> Optional[tuple[str, str]]:
        """
        Fetch a UNIQUE image from Unsplash API and cache locally.
        Returns tuple of (local file path, image description).
        """

        if (
            not self.pexels_key
        ):  # Note: Kept variable name for compatibility, but holds Unsplash key
            return None

        try:
            # 1. Search Unsplash for multiple candidates
            url = f"https://api.unsplash.com/search/photos"
            params = {
                "query": keyword,
                "per_page": 15,  # Get more to find a unique one
            }
            headers = {"Authorization": f"Client-ID {self.pexels_key}"}

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(url, params=params, headers=headers, timeout=10),
            )

            response.raise_for_status()
            data = response.json()

            if not data.get("results"):
                return None

            # 2. Pick the first one that hasn't been used in this document
            selected_photo = None
            for photo in data["results"]:
                photo_id = str(photo["id"])
                if photo_id not in context.used_image_ids:
                    selected_photo = photo
                    context.used_image_ids.add(photo_id)
                    break

            # Fallback to first if all used
            if not selected_photo:
                selected_photo = data["results"][0]

            photo_id = str(selected_photo["id"])
            image_url = selected_photo["urls"]["regular"]

            description = (
                selected_photo.get("description")
                or selected_photo.get("alt_description")
                or f"Illustration of {keyword}"
            )

            # 3. Check if THIS SPECIFIC PHOTO is in cache
            cache_path = self.image_cache_dir / f"unsplash_{photo_id}.jpg"
            if cache_path.exists():
                print(f"      Using cached unique image {photo_id}")
                return str(cache_path), description

            # 4. Download if not cached
            img_response = await loop.run_in_executor(
                None, lambda: requests.get(image_url, timeout=30)
            )
            img_response.raise_for_status()

            with open(cache_path, "wb") as f:
                f.write(img_response.content)

            print(f"      Fetched and cached new unique image {photo_id}")
            return str(cache_path), description

        except Exception as e:
            print(f"      Failed to fetch unique image for '{keyword}': {e}")

        return None

    async def _create_table_for_section(
        self,
        section_title: str,
        section_content: str,
        topic: str,
    ) -> Optional[Dict]:
        """Use LLM to generate a meaningful data table based on the content."""
        prompt = f"""You are an expert data analyst. Based on the following content about "{section_title}", extract ACTUAL entities, numbers, and comparisons to create a professional data table. 
If the text lacks explicit numbers, synthesize highly realistic and contextually accurate estimates. Do NOT use generic placeholders like "Variable A" or "Row1 Data".

CONTENT:
{section_content[:2000]}

OUTPUT FORMAT:
Provide ONLY valid JSON in this exact structure, with no markdown formatting or explanation:
{{
    "title": "A real, descriptive title based on the data",
    "caption": "Table 1: A brief explanatory caption",
    "data": {{
        "headers": ["Real Category 1", "Real Category 2", "Real Category 3"],
        "rows": [
            ["Real Data 1", "Real Data 2", "Real Data 3"],
            ["Real Data 4", "Real Data 5", "Real Data 6"]
        ]
    }}
}}
CRITICAL INSTRUCTION: DO NOT USE the placeholder text "Real Category 1" or "Real Data 1". You MUST replace them with ACTUAL data from the text.
"""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "format": "json",
                    },
                    timeout=30,
                ),
            )
            data = response.json()
            raw_text = data.get("response", "{}")

            # Clean markdown JSON formatting if present
            raw_text = re.sub(r"```[a-zA-Z]*\n", "", raw_text)
            raw_text = re.sub(r"```", "", raw_text).strip()

            table_data = json.loads(raw_text)

            if "data" in table_data and "headers" in table_data["data"]:
                table_data["type"] = "table"
                table_data["insert_after_paragraph"] = 2
                return table_data

        except Exception as e:
            print(f"      Failed to generate meaningful table: {e}")

        return None

    async def _create_chart_for_section(
        self,
        section_title: str,
        section_content: str,
        topic: str,
    ) -> Optional[Dict]:
        """Use LLM and Matplotlib to generate a real PNG chart based on the content."""
        prompt = f"""Extract ONLY real numeric data from the following text about "{section_title}".

Rules:
- Do NOT create or estimate values
- All values must represent the SAME metric
- Return JSON only

If valid dataset cannot be formed:
{{"skip": true}}

Valid example:
{{
  "labels": ["Mercury", "Venus", "Earth"],
  "values": [88, 225, 365],
  "unit": "days",
  "metric": "orbital period",
  "caption": "Figure 1: Explains what the chart illustrates."
}}

CONTENT:
{section_content[:2000]}
"""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "format": "json",
                    },
                    timeout=30,
                ),
            )
            data = response.json()
            raw_text = data.get("response", "{}")

            # Clean markdown JSON formatting if present
            raw_text = re.sub(r"```[a-zA-Z]*\n", "", raw_text)
            raw_text = re.sub(r"```", "", raw_text).strip()

            chart_data = json.loads(raw_text)

            if chart_data.get("skip"):
                return None

            if "labels" in chart_data and "values" in chart_data:
                labels = chart_data["labels"]
                raw_values = chart_data["values"]

                # Robustly sanitize values to ensure they are floats
                clean_values = []
                for v in raw_values:
                    if v is None:
                        continue
                    try:
                        # Strip any stray non-numeric characters if it came back as a string
                        if isinstance(v, str):
                            v_clean = re.sub(r"[^\d\.-]", "", v)
                            clean_values.append(float(v_clean) if v_clean else 0.0)
                        else:
                            clean_values.append(float(v))
                    except (ValueError, TypeError):
                        clean_values.append(0.0)

                if not clean_values or sum(clean_values) == 0:
                    return None  # Avoid rendering empty/broken charts

                chart_type = "bar"

                # Generate unique filename
                chart_id = hashlib.md5(
                    f"{section_title}_{chart_type}".encode()
                ).hexdigest()[:10]
                chart_path = self.image_cache_dir / f"chart_{chart_id}.png"

                # Render using matplotlib in a background thread
                def _render_mpl():
                    import matplotlib.pyplot as plt
                    import matplotlib
                    import textwrap

                    matplotlib.use("Agg")  # Non-interactive backend

                    # Ensure we have enough labels for the values
                    plot_labels = labels[: len(clean_values)]
                    while len(plot_labels) < len(clean_values):
                        plot_labels.append("Unknown")

                    # Wrap long labels
                    plot_labels = [
                        textwrap.fill(str(lbl), width=12) for lbl in plot_labels
                    ]

                    plt.figure(figsize=(8, 5))

                    if chart_type == "pie":
                        # Avoid pie chart errors with negative numbers
                        pie_values = [abs(v) for v in clean_values]
                        plt.pie(
                            pie_values,
                            labels=plot_labels,
                            autopct="%1.1f%%",
                            startangle=140,
                            colors=plt.cm.Paired.colors,
                        )
                    else:
                        bars = plt.bar(plot_labels, clean_values, color="#4A90E2")
                        metric = chart_data.get("metric", "Value")
                        unit = chart_data.get("unit", "")
                        y_label = f"{metric} ({unit})" if unit else metric
                        plt.ylabel(y_label)
                        plt.xticks(rotation=45, ha="right", fontsize=9)

                    metric_title = chart_data.get("metric", "").title()
                    plt.title(
                        f"{metric_title} Comparison" if metric_title else section_title
                    )
                    plt.tight_layout()
                    plt.savefig(chart_path, dpi=150, bbox_inches="tight")
                    plt.close()

                await loop.run_in_executor(None, _render_mpl)

                chart_data["type"] = "chart"
                chart_data["path"] = str(chart_path.absolute())
                chart_data["insert_after_paragraph"] = 3
                return chart_data

        except Exception as e:
            print(f"      Failed to generate professional chart: {e}")

        return None
