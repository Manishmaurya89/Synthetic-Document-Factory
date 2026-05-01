from pipeline_architecture import (
    PipelineStage,
    StageResult,
    StageStatus,
    PipelineContext,
    SectionPlan,
)
from typing import List
import re


class PlanningStage(PipelineStage):
    """
    Planning stage - deterministic calculation of:
    1. Number of sections (from template)
    2. Words per section (calculated from target pages)
    3. Visual allocation (distributed across sections)
    4. Section titles (template + topic substitution)
    """

    def __init__(self):
        super().__init__("Planning")

    async def execute(self, context: PipelineContext) -> StageResult:
        """Execute planning stage"""

        template = context.template
        constraints = context.constraints

        # 1. Calculate target words
        total_words_needed = int(
            constraints.target_pages
            * template.words_per_page
            * constraints.words_per_page_calibration
        )

        # 2. Filter sections based on page count and requirements
        sections = self._select_sections(template, constraints.target_pages)
        num_sections = len(sections)

        print(f"  Target pages: {constraints.target_pages}")
        print(f"  Total words needed: {total_words_needed}")
        print(f"  Sections selected: {num_sections}")

        # 3. Distribute words across sections
        words_per_section = total_words_needed // num_sections

        # 4. Allocate visuals across sections
        visual_allocation = self._allocate_visuals(
            sections=sections,
            total_tables=constraints.total_tables,
            total_charts=constraints.total_charts,
            total_images=constraints.total_images,
        )

        # 5. Create section plans
        section_plans = []
        for i, template_section in enumerate(sections):
            # Substitute {topic} in title template
            title = template_section.title_template.replace(
                "{topic}", constraints.topic
            )

            # Calculate paragraphs (roughly 150 words each)
            target_paragraphs = max(
                template_section.min_paragraphs, words_per_section // 150
            )

            # Extract keywords for image search
            keywords = self._extract_keywords(title, constraints.topic)

            plan = SectionPlan(
                index=i,
                title=title,
                target_paragraphs=target_paragraphs,
                target_words=words_per_section,
                tables_allocated=visual_allocation["tables"][i],
                charts_allocated=visual_allocation["charts"][i],
                images_allocated=visual_allocation["images"][i],
                image_keywords=keywords,
                content_constraints=template_section.content_constraints,
                word_limit=template_section.word_limit,
            )

            section_plans.append(plan)

            print(f"\n  Section {i+1}: {title}")
            print(
                f"    Target: {target_paragraphs} paragraphs ({words_per_section} words)"
            )

        # Store plans in context
        context.section_plans = section_plans

        return StageResult(
            stage_name=self.name,
            status=StageStatus.COMPLETED,
            duration=0.0,
            data={
                "num_sections": num_sections,
                "words_per_section": words_per_section,
                "section_plans": [self._plan_to_dict(p) for p in section_plans],
            },
            metadata={
                "total_words": total_words_needed,
                "total_visuals": constraints.total_tables
                + constraints.total_charts
                + constraints.total_images,
            },
        )

    def _select_sections(self, template, target_pages: int) -> List:
        """Select and dynamically scale template sections to meet the exact page count"""
        import copy

        # Base sections
        required = [s for s in template.sections if s.required]
        optional = [s for s in template.sections if not s.required]

        # If target pages is small, just use required
        min_words_required = sum(s.min_paragraphs * 150 for s in required)
        min_pages_required = min_words_required / template.words_per_page

        sections = required
        if target_pages > min_pages_required * 1.2:
            sections = required + optional

        # Smart scaling: each section covers ~4 pages of content
        # 10 pages -> 6 sections (template default), 30 pages -> 8 sections
        max_pages_per_section = 4
        needed_sections = max(len(sections), target_pages // max_pages_per_section)

        if needed_sections > len(sections):
            # Pick a core 'body' section to duplicate (usually in the middle)
            core_index = max(1, len(sections) // 2)
            core_template = sections[core_index]

            # Use expansion titles from the JSON template, or fallback to generic
            dynamic_titles = template.expansion_titles
            if not dynamic_titles:
                dynamic_titles = [
                    "Advanced Analysis of {topic}",
                    "Deep Dive into {topic}",
                    "Core Dynamics of {topic}",
                    "Critical Evaluation of {topic}",
                ]

            for i in range(needed_sections - len(sections)):
                new_section = copy.deepcopy(core_template)
                title_choice = dynamic_titles[i % len(dynamic_titles)]
                new_section.title_template = title_choice
                sections.insert(len(sections) - 1, new_section)

        return sections

    def _allocate_visuals(
        self, sections: List, total_tables: int, total_charts: int, total_images: int
    ) -> dict:
        """Deterministically allocate visuals across sections"""
        num_sections = len(sections)

        allocation = {
            "tables": [0] * num_sections,
            "charts": [0] * num_sections,
            "images": [0] * num_sections,
        }
        # Tables: prioritize Results, Analysis, Findings sections
        table_priority = [
            i
            for i, s in enumerate(sections)
            if s.allow_tables
            and any(
                k in s.title_template.lower()
                for k in ["result", "finding", "analysis", "data", "comparison"]
            )
        ]
        table_eligible = [i for i, s in enumerate(sections) if s.allow_tables]
        table_targets = table_priority if table_priority else table_eligible

        if table_targets and total_tables > 0:
            for i in range(total_tables):
                allocation["tables"][table_targets[i % len(table_targets)]] += 1

        # Charts: prioritize Results, Discussion, Analysis sections
        chart_priority = [
            i
            for i, s in enumerate(sections)
            if s.allow_charts
            and any(
                k in s.title_template.lower()
                for k in ["result", "finding", "trend", "performance", "discussion"]
            )
        ]
        chart_eligible = [i for i, s in enumerate(sections) if s.allow_charts]
        chart_targets = chart_priority if chart_priority else chart_eligible

        if chart_targets and total_charts > 0:
            for i in range(total_charts):
                allocation["charts"][chart_targets[i % len(chart_targets)]] += 1

        # Images: prioritize Introduction, Background, Examples sections
        image_priority = [
            i
            for i, s in enumerate(sections)
            if s.allow_images
            and any(
                k in s.title_template.lower()
                for k in ["introduction", "background", "example", "getting", "step"]
            )
        ]
        image_eligible = [i for i, s in enumerate(sections) if s.allow_images]
        image_targets = image_priority if image_priority else image_eligible

        if image_targets and total_images > 0:
            for i in range(total_images):
                allocation["images"][image_targets[i % len(image_targets)]] += 1

        return allocation

    def _extract_keywords(self, title: str, topic: str) -> List[str]:
        """Extract meaningful keywords for image search"""
        # Remove common words
        stop_words = {
            "the",
            "a",
            "an",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "with",
            "by",
            "from",
            "as",
            "into",
            "through",
            "during",
            "including",
            "about",
            "against",
            "between",
            "under",
            "over",
            "after",
            "before",
        }

        # Tokenize and filter
        words = re.findall(r"\b[a-zA-Z]{3,}\b", title.lower())
        keywords = [w for w in words if w not in stop_words]

        # Add topic as primary keyword
        keywords.insert(0, topic.lower())

        # Limit to top 5 keywords
        return keywords[:5]

    def _plan_to_dict(self, plan: SectionPlan) -> dict:
        """Convert SectionPlan to dict for serialization"""
        return {
            "index": plan.index,
            "title": plan.title,
            "target_paragraphs": plan.target_paragraphs,
            "target_words": plan.target_words,
            "tables": plan.tables_allocated,
            "charts": plan.charts_allocated,
            "images": plan.images_allocated,
            "keywords": plan.image_keywords,
        }
