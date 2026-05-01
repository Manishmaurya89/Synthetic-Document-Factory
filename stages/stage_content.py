from pipeline_architecture import (
    PipelineStage,
    StageResult,
    StageStatus,
    PipelineContext,
)
import requests
from typing import Optional, List, Dict
import os
import re
import hashlib
import time


class ContentGenerationStage(PipelineStage):
    def __init__(self, ollama_url: str = None, model: str = None):
        super().__init__("Content Generation")
        self.ollama_url = ollama_url or os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.model = model or os.getenv("OLLAMA_MODEL", "llama3")
        self.optional = False

    async def execute(self, context: PipelineContext) -> StageResult:
        """Generate content for all planned sections sequentially."""

        generated_sections = []
        topic = context.constraints.topic
        
        total_words = 0
        # Rolling summary of what has already been written (to prevent cross-section repetition)
        written_context = ""

        for i, plan in enumerate(context.section_plans):
            print(f"  Generating section {i+1}/{len(context.section_plans)}: {plan.title}")
            
            content = await self._generate_section_content(
                topic=topic,
                section_title=plan.title,
                target_paragraphs=plan.target_paragraphs,
                context=written_context,
                template_tone=context.template.tone,
                template_level=context.template.technical_level,
                target_emotion=context.template.target_emotion,
                desired_action=context.template.desired_action,
                constraints=plan.content_constraints if hasattr(plan, 'content_constraints') else {},
                target_words=plan.target_words,
                languages=context.constraints.languages,
                mixing_ratio=context.constraints.mixing_ratio
            )
            
            content = self._clean_content(content)
            content = self._split_mixed_paragraphs(content)
            content = self._deduplicate_sentences(content, written_context)

            # Append last 400 words of this section to the rolling context
            section_words = content.split()
            written_context += " ".join(section_words[-400:]) + "\n"

            section_data = {
                "title": plan.title,
                "content": content,
                "visuals": [],
                "word_count": len(content.split()),
                "plan": plan
            }
            
            generated_sections.append(section_data)
            total_words += section_data["word_count"]
            
        context.generated_sections = generated_sections

        return StageResult(
            stage_name=self.name,
            status=StageStatus.COMPLETED,
            duration=0.0,
            data={
                "sections": len(generated_sections),
                "total_words": total_words,
            },
            metadata={
                "model": self.model,
            },
        )

    async def _generate_section_content(
        self,
        topic: str,
        section_title: str,
        target_paragraphs: int,
        context: str,
        template_tone: str,
        template_level: str,
        target_emotion: str = "informed",
        desired_action: str = "explore further",
        constraints: dict = None,
        word_limit: int = None,
        target_words: int = 500,
        languages: List[str] = None,
        mixing_ratio: Dict[str, int] = None,
    ) -> str:
        """Generate each paragraph individually to guarantee exact language ratio."""

        lang_map = {
            "eng": "English", "en": "English",
            "hin": "Hindi — write ONLY in Devanagari script (हिंदी). No English words.",
            "hi":  "Hindi — write ONLY in Devanagari script (हिंदी). No English words.",
            "urd": "Urdu — write ONLY in Nastaliq/Perso-Arabic script (اردو). No English words.",
            "ur":  "Urdu — write ONLY in Nastaliq/Perso-Arabic script (اردو). No English words.",
            "tel": "Telugu — write ONLY in Telugu script (తెలుగు). No English words.",
            "te":  "Telugu — write ONLY in Telugu script (తెలుగు). No English words.",
            "spa": "Spanish — write ONLY in Spanish (Español). No English words.",
            "es":  "Spanish — write ONLY in Spanish (Español). No English words.",
            "fre": "French — write ONLY in French (Français). No English words.",
            "fr":  "French — write ONLY in French (Français). No English words.",
            "ger": "German — write ONLY in German (Deutsch). No English words.",
            "de":  "German — write ONLY in German (Deutsch). No English words.",
        }

        clean_langs = [l.lower().strip() for l in (languages or ["eng"])]

        # Build per-paragraph language assignment list
        if len(clean_langs) == 1:
            lang_schedule = [clean_langs[0]] * target_paragraphs
        else:
            # Distribute paragraphs by ratio
            if mixing_ratio:
                total = sum(mixing_ratio.values())
                lang_schedule = []
                for lang in clean_langs:
                    ratio = mixing_ratio.get(lang, 0)
                    count = round(target_paragraphs * ratio / total)
                    lang_schedule.extend([lang] * count)
                # Trim or pad to exactly target_paragraphs
                lang_schedule = lang_schedule[:target_paragraphs]
                while len(lang_schedule) < target_paragraphs:
                    lang_schedule.append(clean_langs[0])
            else:
                lang_schedule = [
                    clean_langs[i % len(clean_langs)]
                    for i in range(target_paragraphs)
                ]

        # Generate each paragraph one at a time
        paragraphs = []
        para_context = context or ""

        for p_idx, lang_code in enumerate(lang_schedule):
            lang_name = lang_map.get(lang_code, "English")
            para_prompt = f"""You are a professional author writing paragraph {p_idx+1} of a section in a document about {topic}.

SECTION: {section_title}
LANGUAGE: {lang_name}

{f'PREVIOUS CONTENT (do NOT repeat any idea from this):{chr(10)}{para_context[-600:].strip()}{chr(10)}' if para_context.strip() else ''}
WRITE EXACTLY ONE PARAGRAPH of 130-150 words.

RULES:
1. Write ONLY in the specified language. Zero mixing.
2. Introduce a completely NEW idea not covered in previous content.
3. Natural, humanized professional prose.
4. No labels, no preamble, no meta-text.
5. Begin immediately with the first word of content.

OUTPUT: One paragraph only. Start writing immediately."""

            para = ""
            for attempt in range(3):
                para = await self._call_ollama(para_prompt, max_tokens=400)
                para = self._clean_content(para.strip())
                if para and len(para.split()) >= 50:
                    break
                if attempt < 2:
                    print(f"    [Retry {attempt+2}/3] Para {p_idx+1} ({lang_code}) empty, retrying...")

            if para:
                paragraphs.append(para)
                # Update rolling context with this paragraph
                para_context += " " + para

        if not paragraphs:
            return ""

        return "\n\n".join(paragraphs)


    def _build_prompt(
        self,
        topic: str,
        section_title: str,
        target_paragraphs: int,
        context: str,
        tone: str,
        level: str,
        target_emotion: str,
        desired_action: str,
        section_constraints: dict,
        word_limit: int = None,
        target_words: int = 500,
        languages: List[str] = None,
        mixing_ratio: Dict[str, int] = None,
    ) -> str:
        """Build the expert prompt for the LLM with strict constraints."""

        # Full language code → display name map (covering all supported codes)
        lang_map = {
            "eng": "English",
            "en":  "English",
            "hin": "Hindi — write entirely in Devanagari script (हिंदी)",
            "hi":  "Hindi — write entirely in Devanagari script (हिंदी)",
            "urd": "Urdu — write entirely in Nastaliq/Perso-Arabic script (اردو)",
            "ur":  "Urdu — write entirely in Nastaliq/Perso-Arabic script (اردو)",
            "tel": "Telugu — write entirely in Telugu script (తెలుగు)",
            "te":  "Telugu — write entirely in Telugu script (తెలుగు)",
        }
        clean_langs = [l.lower().strip() for l in (languages or ["eng"])]

        if len(clean_langs) == 1:
            lang_name = lang_map.get(clean_langs[0], clean_langs[0])
            lang_instruction = (
                f"LANGUAGE: {lang_name}.\n"
                "  - Do NOT include any language name or label anywhere in the output."
            )
        else:
            # Build schedule using ordinal positions ONLY — never echo language names as prefixes
            ordinals = ["first", "second", "third", "fourth",
                        "fifth", "sixth", "seventh", "eighth",
                        "ninth", "tenth", "eleventh", "twelfth"]
            positions = []
            for p in range(target_paragraphs):
                lang_code = clean_langs[p % len(clean_langs)]
                lang_name = lang_map.get(lang_code, lang_code)
                ordinal = ordinals[p] if p < len(ordinals) else f"{p+1}th"
                positions.append(f"  - {ordinal.capitalize()} paragraph → {lang_name}")
            lang_instruction = (
                "LANGUAGE SCHEDULE (follow exactly, output NO labels):\n"
                + "\n".join(positions)
                + "\n\n  CRITICAL: Do NOT print any language name, script name, or label "
                  "before, inside, or after any paragraph. Labels are STRICTLY FORBIDDEN."
            )

        return f"""You are a professional author writing a section of a published document on {topic}.

SECTION: {section_title}

{lang_instruction}

{f'ALREADY COVERED (DO NOT REPEAT these ideas or phrases):{chr(10)}{context[-1200:].strip()}{chr(10)}' if context and context.strip() else ''}
WRITE EXACTLY {target_paragraphs} PARAGRAPHS. Each paragraph: 130-150 words.

RULES:
1. Write naturally - like a seasoned journalist or academic author.
2. Every paragraph MUST advance the topic with FRESH, SPECIFIC information not yet covered above.
3. Zero repetition of any idea, phrase, or sentence from this section OR prior sections.
4. Maintain logical flow: each paragraph builds on the previous.
5. Begin the very first word of output immediately with content - no preamble.
6. ABSOLUTELY FORBIDDEN in output:
   - Language labels: "English", "Hindi", "Urdu", "Telugu", "Spanish", "French", "German", "हिंदी", "اردو", "తెలుగు", "Español", "Français", "Deutsch"
   - Paragraph markers: "Paragraph 1", "Block 1", "Section:"
   - Meta-text: "Here is", "This section", "Below is", "Expanded version"
   - Any explanation of what you are doing.

OUTPUT: Only the final prose. Start writing immediately.
"""

    async def _call_ollama(self, prompt: str, max_tokens: int = 2048) -> str:
        """Call Ollama API synchronously with custom token limits."""
        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.4,
                        "num_ctx": 8192,
                        "num_predict": max_tokens,
                        "repeat_penalty": 1.2,
                        "top_p": 0.9,
                        "stop": ["━━━"]
                    }
                },
                timeout=600
            )
            response.raise_for_status()
            return response.json().get("response", "")
        except Exception as e:
            print(f"    Error calling Ollama: {e}")
            return ""

    def _clean_content(self, text: str) -> str:
        """Aggressively scrub AI output of all labels, meta-text, and artifacts."""
        if not text:
            return ""

        # 1. Remove markdown code fences
        text = re.sub(r"```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```", "", text)

        # 2. Nuke entire lines that are AI meta-talk
        meta_line_patterns = [
            r"^Here is\b.*$",
            r"^Please find\b.*$",
            r"^This section\b.*$",
            r"^The section\b.*$",
            r"^Below is\b.*$",
            r"^The following\b.*$",
            r"^I have\b.*$",
            r"^Note:\s*.*$",
            r"^Expanded version\b.*$",
            r"^Output:\s*$",
            r"^---+\s*$",         # horizontal rules
            r"^===+\s*$",
        ]
        for pattern in meta_line_patterns:
            text = re.sub(pattern, "", text, flags=re.MULTILINE | re.IGNORECASE)

        # 3. Strip inline language / paragraph labels at the START of any line.
        #    Covers: plain text, bold (**X**), italic (*X*), with colon or space.
        inline_label_patterns = [
            # Latin / romanised language names
            r"^\*{0,2}English\*{0,2}[:\s]+",
            r"^\*{0,2}Hindi\*{0,2}[:\s]+",
            r"^\*{0,2}Urdu\*{0,2}[:\s]+",
            r"^\*{0,2}Telugu\*{0,2}[:\s]+",
            r"^\*{0,2}Spanish\*{0,2}[:\s]+",
            r"^\*{0,2}Español\*{0,2}[:\s]+",
            r"^\*{0,2}French\*{0,2}[:\s]+",
            r"^\*{0,2}Français\*{0,2}[:\s]+",
            r"^\*{0,2}German\*{0,2}[:\s]+",
            r"^\*{0,2}Deutsch\*{0,2}[:\s]+",
            # Native-script language names used as labels
            r"^हिन्दी[:\s]+",
            r"^हिंदी[:\s]+",
            r"^اردو[:\s]+",
            r"^تلگو[:\s]+",
            r"^తెలుగు[:\s]+",
            # Paragraph / section / block markers (any numbering)
            r"^\*{0,2}Paragraph\s*\d*[:\.]?\*{0,2}\s*",
            r"^\*{0,2}Block\s*\d*[:\.]?\*{0,2}\s*",
            r"^\*{0,2}Section\s*\d*[:\.]?\*{0,2}\s*",
            r"^\(\d+\)\s*",      # (1), (2) …
            r"^\d+[\.\)\:]\s+",  # 1. 1) 1:
        ]
        for pattern in inline_label_patterns:
            text = re.sub(pattern, "", text, flags=re.MULTILINE | re.IGNORECASE)

        # 4. Collapse whitespace artifacts
        text = re.sub(r" {4,}", " ", text)       # multiple spaces
        text = re.sub(r"\n{3,}", "\n\n", text)   # triple+ newlines → double
        text = re.sub(r"\.{4,}", "...", text)    # long dot chains

        return text.strip()

    def _deduplicate_sentences(self, new_content: str, previous_content: str, threshold: float = 0.75) -> str:
        """Remove sentences from new_content that are highly similar to previous_content."""
        if not previous_content.strip():
            return new_content

        def sentence_words(s: str) -> set:
            return set(re.sub(r'[^\w\s]', '', s.lower()).split())

        # Build set of sentences already written
        prev_sentences = re.split(r'(?<=[.!?।])\s+', previous_content)
        prev_word_sets = [sentence_words(s) for s in prev_sentences if len(s.strip()) > 20]

        # Split new content into sentences and filter
        new_sentences = re.split(r'(?<=[.!?।])\s+', new_content)
        kept = []
        for sentence in new_sentences:
            s_words = sentence_words(sentence)
            if len(s_words) < 5:
                kept.append(sentence)
                continue
            is_duplicate = False
            for prev_words in prev_word_sets:
                if not prev_words:
                    continue
                overlap = len(s_words & prev_words) / max(len(s_words), len(prev_words))
                if overlap >= threshold:
                    is_duplicate = True
                    break
            if not is_duplicate:
                kept.append(sentence)

        return ' '.join(kept).strip()

    def _split_mixed_paragraphs(self, text: str) -> str:
        """Split paragraphs that mix languages into separate single-language paragraphs."""
        def _sentence_lang(sentence: str) -> str:
            devanagari = sum(1 for c in sentence if 0x0900 <= ord(c) <= 0x097F)
            arabic     = sum(1 for c in sentence if 0x0600 <= ord(c) <= 0x06FF)
            telugu     = sum(1 for c in sentence if 0x0C00 <= ord(c) <= 0x0C7F)
            latin      = sum(1 for c in sentence if c.isalpha() and ord(c) < 0x0300)
            counts = {
                'devanagari': devanagari,
                'arabic':     arabic,
                'telugu':     telugu,
                'latin':      latin,
            }
            dominant = max(counts, key=counts.get)
            return dominant if counts[dominant] > 0 else 'latin'

        paragraphs = text.split('\n\n')
        result = []
        for para in paragraphs:
            if not para.strip():
                continue
            # Split paragraph into sentences
            sentences = re.split(r'(?<=[.!?।])\s+', para.strip())
            groups, current_lang, current = [], None, []
            for sent in sentences:
                if not sent.strip():
                    continue
                lang = _sentence_lang(sent)
                if lang != current_lang:
                    if current:
                        groups.append((current_lang, current))
                    current_lang, current = lang, [sent]
                else:
                    current.append(sent)
            if current:
                groups.append((current_lang, current))

            # Re-join each language group as its own paragraph
            for _, sents in groups:
                result.append(' '.join(sents))

        return '\n\n'.join(result)
