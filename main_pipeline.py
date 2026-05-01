import asyncio
import argparse
import sys
import hashlib
import os
from pathlib import Path
from typing import Optional, List, Dict

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, IntPrompt, Confirm

# Add current directory to path for existing modules
sys.path.insert(0, str(Path(__file__).parent))

from pipeline_architecture import (
    Pipeline,
    PipelineContext,
    GenerationConstraints,
    DocumentType,
    StageStatus,
)
from templates import get_template, list_templates
from stages.stage_planning import PlanningStage
from stages.stage_content import ContentGenerationStage
from stages.stage_visuals import VisualCreationStage
from stages.stage_rendering import RenderingStage
from stages.stage_validation import ContentValidationStage

console = Console()

class DocumentGenerator:
    """
    Main document generation orchestrator.
    Simplified logging to avoid Rich rendering crashes.
    """
    
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        primary_model: str = "llama3",
        pexels_key: Optional[str] = None,
    ):
        self.ollama_url = ollama_url
        self.primary_model = primary_model
        self.pexels_key = pexels_key
        
    async def generate(
        self,
        topic: str,
        target_pages: int,
        template_type: DocumentType = DocumentType.ARTICLE,
        total_tables: int = 1,
        total_charts: int = 1,
        total_images: int = 1,
        require_quality_check: bool = False,
        languages: List[str] = None,
        mixing_ratio: Dict[str, int] = None,
        format_type: str = "pdf",
    ) -> dict:
        """
        Generate a document with simple logging.
        """
        try:
            # 1. Setup
            template = get_template(template_type)
            
            # Dynamic calibration: long documents need more words per page
            calibration = 1.6 if target_pages > 10 else 1.2
            
            constraints = GenerationConstraints(
                topic=topic,
                target_pages=target_pages,
                template_type=template_type,
                total_tables=total_tables,
                total_charts=total_charts,
                total_images=total_images,
                primary_model=self.primary_model,
                require_fact_checking=require_quality_check,
                languages=languages or ["english"],
                mixing_ratio=mixing_ratio or {"english": 100},
                format_type=format_type,
                words_per_page_calibration=calibration
            )
            # Generate creative title
            console.print("[bold cyan]>[/] Generating creative title...")
            try:
                import requests
                import re
                title_prompt = f"Write a single, highly creative and professional document title for a {template_type.value} about '{topic}'. DO NOT use prefixes like 'Title:' or 'Here is'. Output ONLY the title."
                loop = asyncio.get_event_loop()
                title_resp = await loop.run_in_executor(
                    None,
                    lambda: requests.post(
                        f"{self.ollama_url}/api/generate",
                        json={"model": self.primary_model, "prompt": title_prompt, "stream": False},
                        timeout=15
                    )
                )
                title_data = title_resp.json()
                generated_title = title_data.get("response", "").strip().strip('"')
                generated_title = re.sub(r'^(Title:|Here is.*?title:|Output:).*?\s*', '', generated_title, flags=re.IGNORECASE).strip()
                if not generated_title: generated_title = topic.title()
            except:
                generated_title = topic.title()
                
            context = PipelineContext(constraints, template, [])
            context.document_title = generated_title
            # 2. Build stages
            stages = [
                PlanningStage(),
                ContentGenerationStage(ollama_url=self.ollama_url, model=self.primary_model),
                ContentValidationStage(),
                VisualCreationStage(pexels_api_key=self.pexels_key, ollama_url=self.ollama_url, model=self.primary_model),
                RenderingStage()
            ]
            
            # 3. Initial Execution
            results = {}
            for stage in stages:
                console.print(f"[bold cyan]>[/] Executing {stage.name}...")
                result = await stage.execute(context)
                results[stage.name] = result
                if result.status == StageStatus.FAILED:
                    return {'success': False, 'errors': result.errors or ["Stage failed"]}

            render_result = results.get('Rendering')
            actual_pages = render_result.data.get('page_count', 0)
            
            # 4. Page Calibration (Aggressive expansion for long documents)
            render_stage = RenderingStage()
            content_stage = stages[1]
            
            # Limit total iterations to prevent infinite loops
            for iteration in range(5):
                if actual_pages >= target_pages:
                    break
                    
                console.print(f"[bold yellow]![/] Calibrating: {actual_pages} → {target_pages} pages (Pass {iteration+1})...")
                
                # Calculate word deficit
                words_per_page = context.template.words_per_page
                current_words = sum(s['word_count'] for s in context.generated_sections)
                target_words_total = target_pages * words_per_page * calibration
                deficit = target_words_total - current_words
                
                if deficit <= 0:
                    deficit = 800 # Add a bit more if pages are still short
                
                # Expand the shortest sections
                sorted_sections = sorted(context.generated_sections, key=lambda s: s['word_count'])
                sections_to_expand = sorted_sections[:min(3, len(sorted_sections))]
                words_per_expansion = deficit // len(sections_to_expand)
                
                for section in sections_to_expand:
                    paras_needed = max(2, words_per_expansion // 150)
                    prompt = (
                        f"Expand the section '{section['title']}' with deeper analysis of {topic}. "
                        f"Provide {paras_needed} new, highly detailed paragraphs. "
                        f"Start immediately with professional prose."
                    )
                    
                    try:
                        tokens = max(1500, int(words_per_expansion * 1.5))
                        new_content = await content_stage._call_ollama(prompt, max_tokens=tokens)
                        if new_content:
                            section['content'] += "\n\n" + new_content.strip()
                            section['word_count'] += len(new_content.split())
                            console.print(f"    Expanded '{section['title']}': +{len(new_content.split())} words")
                    except Exception as e:
                        console.print(f"    Calibration error: {e}")
                
                # Re-render and check progress
                render_res = await render_stage.execute(context)
                actual_pages = render_res.data.get('page_count', actual_pages)
            
            # Trimming Phase (fast, no LLM calls needed)
            if actual_pages > target_pages:
                console.print(f"[bold yellow]![/] Trimming {actual_pages} → {target_pages} pages...")
                
                # Estimate how many words to remove
                words_per_page = context.template.words_per_page
                current_words = sum(s['word_count'] for s in context.generated_sections)
                target_words = target_pages * words_per_page
                words_to_remove = current_words - target_words
                
                # Remove paragraphs from the longest sections until deficit is met
                removed_words = 0
                for _ in range(30):
                    if removed_words >= words_to_remove:
                        break
                    longest = max(context.generated_sections, key=lambda s: s['word_count'])
                    paragraphs = [p for p in longest['content'].split("\n\n") if p.strip()]
                    if len(paragraphs) > 2:
                        removed = paragraphs.pop()
                        longest['content'] = "\n\n".join(paragraphs)
                        rw = len(removed.split())
                        longest['word_count'] -= rw
                        removed_words += rw
                    else:
                        break
                
                render_res = await render_stage.execute(context)
                actual_pages = render_res.data.get('page_count', actual_pages)
            
            return {
                'success': True,
                'output_file': render_result.data.get('filename', "output/document.pdf"),
                'page_count': actual_pages,
                'word_count': sum(s.get('word_count', 0) for s in context.generated_sections),
            }
            
        except Exception as e:
            return {'success': False, 'errors': [f"Critical error: {str(e)}"]}

async def main_async():
    """Simple but robust CLI"""
    console.print("[bold blue]SYNTHETIC DOCUMENT FACTORY v3.0[/]")
    
    topic = Prompt.ask("Topic")
    pages = IntPrompt.ask("Target Pages", default=5)
    
    # List templates
    all_templates = list_templates()
    console.print("\nAvailable Templates:")
    for i, (tid, info) in enumerate(all_templates.items(), 1):
        console.print(f"  {i}. {info['name']}")
    
    choice = IntPrompt.ask("Select ID", default=1)
    template_key = list(all_templates.keys())[choice-1]
    template_type = DocumentType(template_key)
    
    # ASK USER about visuals
    console.print("\n[bold cyan]Visual Elements (optional):[/]")
    console.print("Tables are useful for: comparisons, specifications, data")
    console.print("Images are useful for: illustrations, examples, context")
    
    # Check visual recommendations if any
    template_info = all_templates[template_key]
    # No direct access to visual_recommendations in list_templates, but we can assume user knows what they want.
    
    tables = IntPrompt.ask("Number of tables (0 for none)", default=0)
    charts = 0  # Removed prompt as per user request
    images = IntPrompt.ask("Number of images (0 for none)", default=0)

    # Multilingual & Output Format
    console.print("\n[bold cyan]Output Options:[/]")
    
    LANG_MAP = {
        "eng": "English",
        "hin": "Hindi",
        "urd": "Urdu",
        "tel": "Telugu",
        "spa": "Spanish",
        "fre": "French",
        "ger": "German"
    }
    
    console.print(f"Supported languages: {', '.join(LANG_MAP.keys())}")
    lang_input = Prompt.ask("Enter language code(s) (e.g. eng or eng,hin)", default="eng")
    selected_langs = [l.strip().lower() for l in lang_input.split(",") if l.strip().lower() in LANG_MAP]
    
    if not selected_langs:
        selected_langs = ["eng"]
        
    mixing_ratio = {}
    if len(selected_langs) > 1:
        console.print("\nEnter percentage for each language (must sum to 100):")
        total = 0
        for i, code in enumerate(selected_langs):
            if i == len(selected_langs) - 1:
                percent = 100 - total
                console.print(f"  {code} (%): {percent} (auto-calculated)")
            else:
                percent = IntPrompt.ask(f"  {code} (%)", default=50)
                total += percent
            mixing_ratio[code] = percent
    else:
        mixing_ratio[selected_langs[0]] = 100

    format_type = Prompt.ask("Format (pdf/txt/markdown)", default="pdf").lower()

    generator = DocumentGenerator(pexels_key="zhP1SVMYmEiVse6kpN6dmX1bfCO0uKFFSjdk2srgo6g")
    
    result = await generator.generate(
        topic=topic,
        target_pages=pages,
        template_type=template_type,
        total_tables=tables,
        total_charts=charts,
        total_images=images,
        languages=selected_langs,
        mixing_ratio=mixing_ratio,
        format_type=format_type
    )

    if result['success']:
        console.print(f"\n[bold green]Success![/] File saved: {result['output_file']}")
        console.print(f"Pages: {result['page_count']} | Words: {result['word_count']}")
    else:
        console.print(f"\n[bold red]Failed:[/] {', '.join(result['errors'])}")

def main():
    try:
        asyncio.run(main_async())
    except Exception as e:
        print(f"FATAL ERROR: {e}")

if __name__ == "__main__":
    main()
