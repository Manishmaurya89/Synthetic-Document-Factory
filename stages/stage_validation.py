from pipeline_architecture import (
    PipelineStage,
    StageResult,
    StageStatus,
    PipelineContext,
)
import re
from typing import List, Dict, Tuple


class ContentValidationStage(PipelineStage):
    def __init__(self):
        super().__init__("Content Validation")
        self.optional = False

    async def execute(self, context: PipelineContext) -> StageResult:
        """Validate all generated content."""
        
        issues_found = []
        
        topic = context.constraints.topic
        topic_keywords = self._extract_topic_keywords(topic)
        
        for i, section in enumerate(context.generated_sections):
            # Simple off-topic check
            off_topic = self._detect_off_topic_content(
                section['content'],
                topic,
                topic_keywords
            )
            if off_topic:
                issues_found.extend(off_topic)
                
        return StageResult(
            stage_name=self.name,
            status=StageStatus.COMPLETED,
            duration=0.0,
            data={
                "issues": len(issues_found)
            }
        )

    def _extract_topic_keywords(self, topic: str) -> List[str]:
        """Extract key terms from topic."""
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of'}
        words = re.findall(r'\b[a-zA-Z]{3,}\b', topic.lower())
        return [w for w in words if w not in stop_words]

    def _detect_off_topic_content(
        self, 
        content: str, 
        topic: str,
        topic_keywords: List[str]
    ) -> List[str]:
        """Detect if content is off-topic."""
        issues = []
        # Simplified check
        if not any(kw in content.lower() for kw in topic_keywords):
            issues.append(f"Content might be off-topic for {topic}")
        return issues
