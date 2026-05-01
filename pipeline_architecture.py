import asyncio
import json
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Callable
from enum import Enum
from pathlib import Path
import time


class StageStatus(Enum):
    """Pipeline stage execution status"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class DocumentType(Enum):
    """Template types with predefined structure"""
    RESEARCH_PAPER = "research_paper"
    TECHNICAL_REPORT = "technical_report"
    ARTICLE = "article"
    WHITEPAPER = "whitepaper"
    TUTORIAL = "tutorial"
    CASE_STUDY = "case_study"


@dataclass
class TemplateSection:
    """Section definition in a template"""
    title_template: str  # Template string: "The Origins of {topic}"
    required: bool = True
    min_paragraphs: int = 3
    max_paragraphs: int = 8
    allow_tables: bool = True
    allow_charts: bool = True
    allow_images: bool = True
    word_limit: Optional[int] = None
    content_constraints: Dict[str, Any] = field(default_factory=dict)
    

@dataclass
class DocumentTemplate:
    """Template defining document structure"""
    name: str
    type: DocumentType
    description: str
    sections: List[TemplateSection]
    words_per_page: int = 320
    min_pages: int = 2
    max_pages: int = 200
    expansion_titles: List[str] = field(default_factory=list)
    
    # Visual placement rules
    tables_per_section_max: int = 1
    charts_per_section_max: int = 1
    images_per_section_max: int = 2
    visual_recommendations: Dict[str, Any] = field(default_factory=dict)
    
    # Style constraints
    tone: str = "professional"
    target_emotion: str = "informed"
    desired_action: str = "explore further"
    technical_level: str = "intermediate"
    citation_style: Optional[str] = None
    

@dataclass
class GenerationConstraints:
    """User-provided constraints for generation"""
    topic: str
    target_pages: int
    template_type: DocumentType
    
    # Optional overrides
    total_tables: int = 0
    total_charts: int = 0
    total_images: int = 0
    
    # Multilingual & Output options
    language: str = "english"
    languages: List[str] = field(default_factory=lambda: ["english"])
    mixing_ratio: Dict[str, int] = field(default_factory=dict)
    format_type: str = "pdf"
    
    # Quality constraints
    max_hallucination_rate: float = 0.1
    require_fact_checking: bool = False
    min_coherence_score: float = 0.7
    
    # Model constraints
    primary_model: str = "llama3"  # Local model for generation
    
    # Calibration
    words_per_page_calibration: float = 1.0
    
    
@dataclass
class SectionPlan:
    """Plan for generating a single section"""
    index: int
    title: str
    target_paragraphs: int
    target_words: int
    context_from_previous: str = ""
    
    # Visual allocations
    tables_allocated: int = 0
    charts_allocated: int = 0
    images_allocated: int = 0
    
    # Keywords for image search
    image_keywords: List[str] = field(default_factory=list)
    
    # Template constraints
    content_constraints: Dict[str, Any] = field(default_factory=dict)
    word_limit: Optional[int] = None

@dataclass
class StageResult:
    """Result from a pipeline stage"""
    stage_name: str
    status: StageStatus
    duration: float
    data: Any
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    

@dataclass
class PipelineContext:
    """Shared context across pipeline stages"""
    constraints: GenerationConstraints
    template: DocumentTemplate
    section_plans: List[SectionPlan]
    document_title: str = ""
    
    # Cache
    cache_dir: Path = Path(__file__).parent / ".cache"
    
    # State
    stage_results: Dict[str, StageResult] = field(default_factory=dict)
    generated_sections: List[Dict] = field(default_factory=list)
    visual_cache: Dict[str, Any] = field(default_factory=dict)
    used_image_ids: set = field(default_factory=set)
    
    def cache_key(self, stage: str, *args) -> str:
        """Generate cache key for a stage with arguments"""
        content = f"{stage}:{json.dumps([str(a) for a in args])}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def get_cached(self, stage: str, *args) -> Optional[Any]:
        """Retrieve cached result for a stage"""
        key = self.cache_key(stage, *args)
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    return json.load(f)
            except Exception:
                return None
        return None
    
    def set_cached(self, stage: str, data: Any, *args):
        """Store result in cache"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = self.cache_key(stage, *args)
        cache_file = self.cache_dir / f"{key}.json"
        try:
            with open(cache_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[Cache] Failed to cache {stage}: {e}")


class PipelineStage(ABC):
    """Abstract base for pipeline stages"""
    
    def __init__(self, name: str):
        self.name = name
        
    @abstractmethod
    async def execute(self, context: PipelineContext) -> StageResult:
        """Execute this stage and return result"""
        pass
    
    def should_skip(self, context: PipelineContext) -> bool:
        """Determine if stage should be skipped"""
        return False
    
    async def run(self, context: PipelineContext) -> StageResult:
        """Run stage with error handling and timing"""
        if self.should_skip(context):
            return StageResult(
                stage_name=self.name,
                status=StageStatus.SKIPPED,
                duration=0.0,
                data=None
            )
        
        start = time.time()
        try:
            print(f"\n{'='*60}")
            print(f"STAGE: {self.name}")
            print(f"{'='*60}")
            
            result = await self.execute(context)
            result.duration = time.time() - start
            
            print(f"✓ {self.name} completed in {result.duration:.2f}s")
            return result
            
        except Exception as e:
            duration = time.time() - start
            print(f"✗ {self.name} failed after {duration:.2f}s: {e}")
            return StageResult(
                stage_name=self.name,
                status=StageStatus.FAILED,
                duration=duration,
                data=None,
                errors=[str(e)]
            )


class Pipeline:
    """Main orchestrator for document generation pipeline"""
    
    def __init__(self, stages: List[PipelineStage]):
        self.stages = stages
        
    async def execute(self, context: PipelineContext) -> Dict[str, StageResult]:
        """Execute all stages in sequence, storing results in context"""
        results = {}
        
        for stage in self.stages:
            result = await stage.run(context)
            results[stage.name] = result
            context.stage_results[stage.name] = result
            
            # Stop pipeline if stage failed and it was critical
            if result.status == StageStatus.FAILED:
                if not getattr(stage, 'optional', False):
                    print(f"\n[Pipeline] Critical stage {stage.name} failed. Stopping.")
                    break
                    
        return results
    
    def summary(self, results: Dict[str, StageResult]) -> str:
        """Generate execution summary"""
        total_time = sum(r.duration for r in results.values())
        completed = sum(1 for r in results.values() if r.status == StageStatus.COMPLETED)
        failed = sum(1 for r in results.values() if r.status == StageStatus.FAILED)
        skipped = sum(1 for r in results.values() if r.status == StageStatus.SKIPPED)
        
        summary = f"""
{'='*60}
PIPELINE EXECUTION SUMMARY
{'='*60}
Total Time: {total_time:.2f}s
Stages: {len(results)} total
  ✓ Completed: {completed}
  ✗ Failed: {failed}
  ⊘ Skipped: {skipped}

Stage Breakdown:
"""
        for name, result in results.items():
            status_symbol = {
                StageStatus.COMPLETED: "✓",
                StageStatus.FAILED: "✗",
                StageStatus.SKIPPED: "⊘",
            }.get(result.status, "?")
            
            summary += f"  {status_symbol} {name}: {result.duration:.2f}s"
            if result.errors:
                summary += f" ({len(result.errors)} errors)"
            summary += "\n"
            
        return summary