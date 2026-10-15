import time
import requests
import os
import re
import json
from typing import List
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

# "Paragraph 1:", "**Section 2.**", "P3)" etc. A number AND a separator are
# required, otherwise ordinary words like "Photosynthesis" or "Parallel" at
# the start of a paragraph lose their first letters.
LABEL_RE = re.compile(
    r'^\**\s*(?:Paragraph|Section|Para|P)\s*\d+\s*[:.)]\**\s*',
    re.IGNORECASE | re.MULTILINE,
)

_OUTLINE_RE = re.compile(r'^\W*SECTION[_\s]*\d+\W*?:\s*(.+)$', re.IGNORECASE)


class ContentGenerator:
    def __init__(self):
        self.ollama_base_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        self.ollama_model    = os.getenv("OLLAMA_MODEL", "mistral:latest")
        try:
            r = requests.get(f"{self.ollama_base_url}/api/tags", timeout=5)
            r.raise_for_status()
        except Exception as e:
            raise RuntimeError(
                f"Ollama is not reachable at {self.ollama_base_url} ({e}). "
                f"Start it with: ollama serve"
            ) from e

        installed = {m.get("name") for m in r.json().get("models", [])}
        wanted = self.ollama_model if ":" in self.ollama_model else f"{self.ollama_model}:latest"
        if installed and wanted not in installed:
            raise RuntimeError(
                f"Model '{self.ollama_model}' is not installed in Ollama. "
                f"Run: ollama pull {self.ollama_model}"
            )
        print(f"[LLM] Ollama ready — model: {self.ollama_model}")

    @staticmethod
    def strip_labels(text: str) -> str:
        return LABEL_RE.sub('', text).strip()

    def _generate(self, prompt: str, max_tokens: int = 2500, temperature: float = 0.7) -> str:
        for attempt in range(3):
            try:
                time.sleep(0.5)
                resp = requests.post(
                    f"{self.ollama_base_url}/api/generate",
                    json={
                        "model": self.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        # Thinking models (gemma4, qwen3...) otherwise spend the
                        # num_predict budget on hidden reasoning and can return
                        # an empty response. Ignored by non-thinking models.
                        "think": False,
                        "options": {
                            "num_predict": max_tokens,
                            "temperature": temperature,
                            "top_p": 0.9,
                            "top_k": 40,
                            "repeat_penalty": 1.1,
                            # FIX 5 — Original num_ctx=4096 caused token starvation.
                            # The section generation prompt alone consumes ~600–800 tokens
                            # (prompt text + context block), leaving only ~3200 tokens for
                            # the response inside a 4096 window. mistral stops generating
                            # before reaching the word target. 8192 gives the model a full
                            # 3500-token response budget even with a large context block.
                            # If your hardware cannot run 8192, set OLLAMA_NUM_CTX=4096
                            # in your environment — the code will use that value instead.
                            "num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "8192")),
                        },
                    },
                    timeout=200,
                )
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, dict):
                    continue
                text = str(data.get("response", ""))
                return self._strip_fences(text).strip()
            except Exception as e:
                print(f"[LLM] Attempt {attempt+1} failed: {e}")
                time.sleep(2 ** attempt)
        return ""

    @staticmethod
    def _strip_fences(text: str) -> str:
        if not isinstance(text, str):
            return ""
        text = re.sub(r'```[a-zA-Z]*\n', '', text)
        text = re.sub(r'```', '', text)
        return text.strip()

    def _clean_json_response(self, text: str) -> str:
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        start = text.find('{')
        end = text.rfind('}') + 1
        if start != -1 and end > start:
            return text[start:end]
        return text.strip()

    @staticmethod
    def plan_sections(target_pages: int) -> int:
        if not isinstance(target_pages, int):
            target_pages = 2
        if target_pages <= 3:
            return 2
        elif target_pages <= 6:
            return 3
        elif target_pages <= 10:
            return 4
        elif target_pages <= 15:
            return 5
        elif target_pages <= 25:
            return 6
        else:
            return max(6, (target_pages + 2) // 4)

    @staticmethod
    def calculate_targets(target_pages: int, num_sections: int, words_per_page: int = 320):
        total_words = target_pages * words_per_page
        words_per_section = total_words // num_sections
        # paragraphs_per_section is returned for logging only —
        # generation no longer uses it as a hard ceiling
        paragraphs_per_section = max(2, words_per_section // 150)
        return paragraphs_per_section, words_per_section

    def generate_outline(self, topic: str, num_sections: int) -> list[dict]:
        prompt = f"""You are a professional document architect. Create section titles for a comprehensive document.

TOPIC: {topic}
NUMBER OF SECTIONS REQUIRED: {num_sections}

STRICT RULES:
1. Output EXACTLY {num_sections} section titles
2. Each title must be specific, descriptive, and professional
3. NO generic titles like "Introduction", "Overview", "Conclusion"
4. NO subsections or bullet points under titles
5. Each title should cover a distinct major aspect of the topic

OUTPUT FORMAT (follow exactly):
SECTION_1: [Your first specific title here]
SECTION_2: [Your second specific title here]
SECTION_3: [Your third specific title here]

Now generate {num_sections} sections for topic: {topic}

OUTPUT (start with SECTION_1):"""

        raw = self._generate(prompt, max_tokens=400, temperature=0.8)

        sections = []
        seen = set()
        for line in raw.strip().split('\n'):
            m = _OUTLINE_RE.match(line.strip())
            if not m:
                continue
            # Models often copy the template's [brackets] or add **bold** / quotes
            title = m.group(1).strip().strip('[]*"\'` ').strip()
            if len(title) > 5 and title.lower() not in seen:
                seen.add(title.lower())
                sections.append({"title": title, "content": "", "visuals": []})

        # Only fill the missing slots instead of throwing away good titles
        for i in range(len(sections), num_sections):
            sections.append({"title": f"Key Aspect {i+1} of {topic}", "content": "", "visuals": []})

        return sections[:num_sections]

    def generate_section_content(
        self,
        topic: str,
        section_title: str,
        target_words: int = 800,
        context: str = "",
    ) -> str:
        """
        TWO-CALL SPLIT GENERATION — solving mistral's 4096 context ceiling.

        Root cause of 5-page initial renders on a 10-page target:
        mistral 7B on Ollama (Mac) loads with num_ctx=4096 by default.
        The per-request num_ctx override is silently ignored if the model
        was loaded with a smaller window. With ~700 prompt tokens consumed
        by instructions + cross-section context, only ~1400-1600 response
        tokens remain — enough for ~350-400 words, not 800.

        Fix: split each section into TWO calls of ~half the target each.
        Each call has a short, focused prompt (~300 tokens) leaving ~1700
        tokens for the response (~425 words). Two calls = ~850 words total,
        reliably hitting the 800-word target regardless of num_ctx setting.
        The second call receives the first half as its context so ideas flow
        naturally without repetition.
        """
        half_target = max(200, target_words // 2)

        # Cross-section context block (kept short to preserve response budget)
        prior_block = ""
        if context and len(context) > 50:
            prior_block = (
                f"IDEAS ALREADY COVERED IN THIS DOCUMENT — do NOT repeat:\n"
                f"{context[-400:]}\n\n"
            )

        # ── CALL 1: First half of the section ─────────────────────────
        prompt_1 = f"""Write the first part of a section for a professional document.

TOPIC: {topic}
SECTION: {section_title}
{prior_block}Write approximately {half_target} words of substantive prose.
Start with the most specific, concrete, or counterintuitive aspect of this section.
Each paragraph covers ONE idea fully with a real example before moving on.
Do NOT open with generic phrases. Do NOT summarize or conclude — this is the FIRST HALF only.
Write only paragraphs, no labels or headers:"""

        part1 = self._generate(prompt_1, max_tokens=1800, temperature=0.72)
        part1 = self.strip_labels(self._strip_fences(part1))

        # ── CALL 2: Second half, continuing from part1 ─────────────────
        # We pass the tail of part1 as context so the model continues naturally
        part1_tail = ' '.join(part1.split()[-300:]) if len(part1.split()) > 300 else part1

        prompt_2 = f"""Continue writing the same document section. This is the SECOND HALF.

TOPIC: {topic}
SECTION: {section_title}

FIRST HALF ALREADY WRITTEN (do NOT repeat these ideas):
{part1_tail}

Write approximately {half_target} words continuing from above.
Introduce new sub-topics, examples, or implications NOT already covered.
Do NOT restate what was written. Do NOT add a conclusion or summary.
Each paragraph covers ONE new idea with a concrete example.
Write only paragraphs, no labels or headers:"""

        part2 = self._generate(prompt_2, max_tokens=1800, temperature=0.72)
        part2 = self.strip_labels(self._strip_fences(part2))

        # Remove any conclusion-language that leaked into part2's end
        bad_closers = ["in conclusion,", "to summarize,", "in summary,", "to conclude,",
                       "as we have seen,", "overall,"]
        if any(p in part2.lower()[-120:] for p in bad_closers):
            sentences = part2.rstrip('.').split('. ')
            last = sentences[-1].lower()
            if any(p in last for p in bad_closers) and len(sentences) > 1:
                sentences = sentences[:-1]
            part2 = '. '.join(sentences).strip()
            if part2 and not part2.endswith('.'):
                part2 += '.'

        combined = (part1 + "\n\n" + part2).strip() if part2 else part1
        return combined

    def evaluate_paragraph(self, paragraph: str, context: str = "") -> tuple[bool, str]:
        """
        ANTIGRAVITY PROMPT — QUALITY EVALUATION
        ----------------------------------------
        Original was a trivial word-count gate that marked almost everything
        as passing (rewrite_weak_paragraph was also a no-op). This version
        actually evaluates content quality using three fast heuristics that
        a small LLM can reliably answer.
        """
        word_count = len(paragraph.split())
        if word_count < 60:
            return False, "too short — under 60 words"

        # Fast structural smell-tests that don't need an LLM call
        bad_openers = [
            "in today's world", "it is important to note", "it is widely known",
            "needless to say", "as previously mentioned", "as we have seen",
        ]
        bad_closers = [
            "in conclusion", "to summarize", "in summary", "to conclude",
            "overall, it is", "as discussed above", "as mentioned above",
        ]
        lower = paragraph.lower()
        for phrase in bad_openers:
            if lower.startswith(phrase):
                return False, f"generic opener: '{phrase}'"
        for phrase in bad_closers:
            if lower.endswith(phrase) or phrase in lower[-80:]:
                return False, f"conclusion filler detected: '{phrase}'"

        # Repetition check against recent context
        if context and len(context) > 100:
            para_words = set(paragraph.lower().split())
            ctx_words  = set(context.lower().split())
            overlap = len(para_words & ctx_words) / max(len(para_words), 1)
            if overlap > 0.72:
                return False, "too repetitive versus surrounding content"

        return True, ""

    def rewrite_weak_paragraph(self, paragraph: str, context: str, reason: str) -> str:
        """
        ANTIGRAVITY PROMPT — REWRITE WEAK PARAGRAPH
        ---------------------------------------------
        Original returned the paragraph unchanged. This version actually
        rewrites — but only on targeted grounds matching the failure reason,
        so the LLM has a clear, narrow instruction rather than a vague
        'improve this' prompt.
        """
        if "generic opener" in reason:
            instruction = (
                "The paragraph starts with a weak, generic phrase. "
                "Rewrite it so the FIRST sentence immediately states a "
                "specific, surprising, or concrete fact about the topic. "
                "Cut the original opening entirely. Keep everything else."
            )
        elif "conclusion filler" in reason:
            instruction = (
                "The paragraph ends with a summary or conclusion phrase. "
                "Cut that ending sentence entirely. Replace it with one "
                "sentence that pushes the idea one step further — a "
                "consequence, an implication, or a forward-looking detail. "
                "Do NOT summarize. Do NOT use the word 'conclusion'."
            )
        elif "repetitive" in reason:
            instruction = (
                "This paragraph repeats ideas that were already covered. "
                "Identify the ONE genuinely new thing this paragraph adds "
                "and expand only that. Strip or rewrite every sentence that "
                "restates something already said. The revised paragraph must "
                "add net-new information."
            )
        elif "too short" in reason:
            instruction = (
                "This paragraph is too short to develop its idea properly. "
                "Expand it by adding a concrete real-world example, a "
                "specific statistic or study, or a practical implication. "
                "Do not pad — add substance. Target 120–180 words."
            )
        else:
            instruction = (
                "This paragraph has a quality issue. Rewrite it to be more "
                "specific, concrete, and substantive. Add a real example or "
                "evidence. Remove any vague or filler language."
            )

        prompt = f"""You are editing a paragraph in a professional document. Apply exactly the instruction below — nothing more, nothing less.

INSTRUCTION:
{instruction}

ORIGINAL PARAGRAPH:
{paragraph}

RECENT CONTEXT (for reference — do not repeat these ideas):
{context[-400:] if context else "(none)"}

Write ONLY the rewritten paragraph. No labels, no preamble, no explanation:"""

        result = self._generate(prompt, max_tokens=600, temperature=0.65).strip()
        result = self.strip_labels(self._strip_fences(result))

        # Safety: only accept rewrite if it's not shorter than original by >30%
        if len(result.split()) > len(paragraph.split()) * 0.7 and len(result) > 60:
            return result
        return paragraph

    def expand_paragraph(self, paragraph: str) -> str:
        """
        ANTIGRAVITY PROMPT — PARAGRAPH EXPANSION
        -----------------------------------------
        Original: asked for 50-100 more words, accepted any growth >30 words.
        Problem: small models would pad with filler or just repeat.

        New approach: ask specifically for a new EXAMPLE or EVIDENCE sentence
        to be woven in. This gives the model a concrete task (find an example)
        rather than an abstract task (be longer), which small models handle far better.
        """
        prompt = f"""You are deepening a paragraph in a professional document.

ORIGINAL PARAGRAPH:
{paragraph}

YOUR TASK:
This paragraph makes a claim or describes a phenomenon. Find ONE specific,
concrete piece of evidence, example, case study, or real-world application
that supports or illustrates its central point, and weave it naturally into
the paragraph. The example should be precise — name a real place, institution,
species, measurement, date, or person where plausible.

RULES:
- Integrate the addition smoothly so the paragraph reads as a single continuous piece
- Do NOT add a summary or conclusion sentence at the end
- Do NOT use phrases like "For example," or "For instance," as a crutch more than once
- The result should be 40–90 words longer than the original
- Write ONLY the expanded paragraph (no labels, no explanation):"""

        result = self._generate(prompt, max_tokens=900, temperature=0.68).strip()
        result = self.strip_labels(self._strip_fences(result))

        growth = len(result.split()) - len(paragraph.split())
        if growth > 25 and len(result.split()) > 90:
            return result
        return paragraph

    def generate_new_content_block(self, topic: str, section_title: str, context: str) -> str:
        """
        ANTIGRAVITY PROMPT — LARGE EXPANSION BLOCK
        -------------------------------------------
        Original: asked for 2-3 paragraphs but then rejected the output if it
        contained ANY conclusion language — which small models default to when
        they sense they're near the end of a response. The result was constant
        rejection loops.

        New approach: we ask for exactly 2 paragraphs and frame them as a
        "mid-section continuation", not an ending. We explicitly tell the model
        it is writing the MIDDLE of a section that continues after this block.
        This suppresses closing-language instincts.
        """
        prompt = f"""You are writing two paragraphs that appear in the MIDDLE of a longer section. The section continues after your contribution — you are not concluding anything.

DOCUMENT TOPIC: {topic}
SECTION: {section_title}

WHAT HAS BEEN WRITTEN SO FAR (the paragraphs before yours):
{context[-1200:]}

YOUR TASK:
Write exactly 2 paragraphs that extend this section's argument or narrative
into territory it has NOT yet covered. Each paragraph should introduce a
genuinely new angle — a different case study, a different mechanism, a
different geographic or historical example, or a different implication.

CONTENT STANDARDS:
- Each paragraph: 140–200 words
- Must introduce ideas not present in the existing content above
- Use specific names, dates, measurements, institutions, or locations
- Active voice, expert but readable tone
- No bullet points or numbered lists

ABSOLUTE PROHIBITIONS:
- Do NOT write "In conclusion", "To summarize", "Overall", "As discussed",
  "As we have seen", or any closing/summary language whatsoever
- Do NOT reference that you are "continuing" or "extending" — just write the content
- Do NOT restate or paraphrase what is already in the section above

Write the 2 paragraphs now (no labels, no headers):"""

        result = self._generate(prompt, max_tokens=1800, temperature=0.73).strip()
        # Strip any "Paragraph N:" labels the model may have emitted
        result = self.strip_labels(self._strip_fences(result))

        # Reject hard-conclusion signals but allow softer forward-looking language
        hard_closers = ["in conclusion,", "to summarize,", "to conclude,", "in summary,"]
        if any(phrase in result.lower() for phrase in hard_closers):
            # Try to recover by stripping the offending sentence
            sentences = result.split('. ')
            sentences = [s for s in sentences if not any(p in s.lower() for p in hard_closers)]
            result = '. '.join(sentences).strip()

        # FIX 6: threshold lowered 200 → 120. mistral returns 170-240 words
        # for 2-paragraph requests; the 200 floor was rejecting valid blocks.
        if len(result.split()) < 120:
            return ""

        return result

    def generate_new_idea_paragraph(self, topic: str, section_title: str, context: str) -> str:
        """
        ANTIGRAVITY PROMPT — SINGLE IDEA PARAGRAPH INJECTION
        ------------------------------------------------------
        Original: asked for 150-200 words then rejected if conclusion language
        appeared anywhere. High rejection rate made the expansion loop stall.

        New approach: frame the paragraph explicitly as an interior paragraph
        (not the last one), and ask for a specific TYPE of content — a case
        study — which gives the model a concrete task and naturally avoids
        summary language.
        """
        prompt = f"""Write one interior paragraph for an ongoing section of a professional document. This paragraph is NOT the last one in the section.

DOCUMENT TOPIC: {topic}
SECTION: {section_title}

RECENT CONTENT (last portion of what has been written):
{context[-900:]}

YOUR TASK:
Write one paragraph (130–190 words) that introduces a NEW case study,
geographic example, historical instance, or real-world application that
the existing content has not yet mentioned. The paragraph should begin
by naming the example directly — do not build up to it with a vague opener.

RULES:
- Be specific: name a real location, organization, species, study, or person
- Do NOT open with "In today's world", "It is important to", or similar phrases
- Do NOT end with any form of summary or conclusion language
- The paragraph ends on a forward-looking detail or implication, not a wrap-up
- 130–190 words. No labels. No preamble. Just the paragraph:"""

        result = self._generate(prompt, max_tokens=900, temperature=0.74).strip()
        # Strip any "Paragraph N:" labels
        result = self.strip_labels(self._strip_fences(result))

        word_count = len(result.split())
        if word_count < 80:
            return ""

        hard_closers = ["in conclusion,", "to summarize,", "in summary,", "to conclude,"]
        if any(phrase in result.lower() for phrase in hard_closers):
            # Strip the closing sentence rather than discarding the whole paragraph
            sentences = result.rstrip('.').split('. ')
            if len(sentences) > 1:
                last = sentences[-1].lower()
                if any(p in last for p in hard_closers):
                    sentences = sentences[:-1]
                result = '. '.join(sentences).strip()
                if not result.endswith('.'):
                    result += '.'

        if len(result.split()) < 80:
            return ""

        return result

    def compress_paragraph(self, paragraph: str) -> str:
        """
        ANTIGRAVITY PROMPT — PARAGRAPH COMPRESSION
        -------------------------------------------
        Unchanged in structure but tightened: we now ask for SPECIFIC cuts
        (filler adverbs, throat-clearing, redundant examples) rather than
        a vague percentage reduction, which gives small models clearer targets.
        """
        prompt = f"""Tighten this paragraph without losing any of its meaning or specific details.

ORIGINAL:
{paragraph}

EDITING INSTRUCTIONS:
1. Remove filler adverbs and intensifiers ("very", "quite", "extremely", "really", "certainly", "importantly")
2. Cut any sentence that merely restates a point already made in the same paragraph
3. If there are two examples where one would suffice, remove the weaker one
4. Convert any passive constructions to active voice
5. Do NOT remove concrete facts, names, numbers, or specific examples
6. Result should be 25–40% shorter than the original

Write ONLY the tightened paragraph (no labels):"""

        result = self._generate(prompt, max_tokens=700, temperature=0.5).strip()
        result = self.strip_labels(self._strip_fences(result))

        original_words = len(paragraph.split())
        new_words = len(result.split())

        if new_words < original_words * 0.85 and new_words > 50:
            return result
        return paragraph

    def determine_visual_placement(self, section_content: str, visual_type: str) -> int:
        paragraphs = [p.strip() for p in section_content.split("\n\n") if p.strip()]
        num_paragraphs = len(paragraphs)

        if visual_type == "table":
            if num_paragraphs <= 3:
                return num_paragraphs - 1
            elif num_paragraphs <= 6:
                return 2
            else:
                return num_paragraphs // 2
        elif visual_type == "chart":
            if num_paragraphs <= 4:
                return num_paragraphs - 1
            else:
                return num_paragraphs - 2
        return num_paragraphs - 1

    def generate_table_for_section(self, topic: str, section_title: str, section_content: str, table_count: int = 1) -> List[dict]:
        """
        TABLE GENERATION — TWO-STAGE APPROACH
        ──────────────────────────────────────
        Root cause of consistent empty returns:
        The old prompt asked "does this section deserve a table?" — a binary
        gating question that mistral answers "no" for all narrative/prose
        content (animals, history, social science) because there is no
        pre-existing numerical data to tabulate.

        Fix — Stage 1: Directed extraction prompt.
        Instead of asking "should there be a table?", we tell the model
        EXACTLY what type of table to build and from what material:
        a comparison table of the key named entities (species, events,
        places, strategies) mentioned in the section, with columns that
        are meaningful for that content type. Every section has at least
        3-4 nameable subjects — this prompt surfaces them.

        Fix — Stage 2: Entity-synthesis fallback.
        If Stage 1 still returns empty (mistral truncated, malformed JSON,
        or genuinely no entities found), Stage 2 sends a simpler, shorter
        prompt asking only for a 3-row × 3-column summary table about the
        section's topic directly — bypassing content analysis entirely.
        """

        # ── Stage 1: Directed extraction ──────────────────────────────
        prompt_1 = f"""You are building a comparison table for a professional document section.

DOCUMENT TOPIC: {topic}
SECTION: {section_title}

SECTION CONTENT (extract table data from this):
{section_content[:2000]}

YOUR TASK:
Identify the 3–5 most significant named entities in this section (these could be
species, countries, battles, strategies, time periods, people, or locations).
Build a comparison table with those entities as rows.

Choose 3–4 columns that are most meaningful for comparing these entities.
Good column types: Key Characteristic, Geographic Range, Notable Feature,
Time Period, Impact/Outcome, Conservation Status, Population, Cause/Effect.

RULES:
- Exactly 3–5 data rows (no header row in rows array)
- Exactly 3–4 columns
- All cell values must be concise (2–6 words max per cell)
- Table must add information value beyond what is obvious from the text
- size: use "medium" for 4 columns, "small" for 3 columns

Respond with ONLY this JSON, no preamble, no explanation:
{{
  "tables": [
    {{
      "title": "Specific descriptive title for this table",
      "size": "medium",
      "insert_after_paragraph": 2,
      "data": {{
        "headers": ["Entity", "Column 2", "Column 3", "Column 4"],
        "rows": [
          ["Row 1 entity", "value", "value", "value"],
          ["Row 2 entity", "value", "value", "value"],
          ["Row 3 entity", "value", "value", "value"]
        ]
      }},
      "caption": "One sentence describing what this table shows"
    }}
  ]
}}"""

        response_1 = self._generate(prompt_1, max_tokens=1000, temperature=0.5)
        tables = self._parse_tables_json(response_1)

        if tables:
            for t in tables:
                t["type"] = "table"
            return tables

        # ── Stage 2: Entity-synthesis fallback ────────────────────────
        # Simpler prompt, no content analysis — just build from topic+section name
        prompt_2 = f"""Create a concise comparison table for a document section about "{section_title}" in a document about "{topic}".

Build a table with 3 rows and 3 columns comparing the 3 most relevant aspects,
examples, or entities related to this section title.

Respond with ONLY this JSON:
{{
  "tables": [
    {{
      "title": "Comparison: {section_title[:50]}",
      "size": "small",
      "insert_after_paragraph": 2,
      "data": {{
        "headers": ["Aspect", "Description", "Significance"],
        "rows": [
          ["Item 1", "brief description", "why it matters"],
          ["Item 2", "brief description", "why it matters"],
          ["Item 3", "brief description", "why it matters"]
        ]
      }},
      "caption": "Key aspects of {section_title[:40]}"
    }}
  ]
}}"""

        response_2 = self._generate(prompt_2, max_tokens=700, temperature=0.5)
        tables_2 = self._parse_tables_json(response_2)

        if tables_2:
            for t in tables_2:
                t["type"] = "table"
            return tables_2

        return []

    def _parse_tables_json(self, response: str) -> list:
        """
        Robust JSON extractor for table responses.
        Handles: preamble text, trailing commentary, partial fences,
        and mistral's habit of adding explanation after the closing brace.
        """
        valid = []
        for t in self._load_json_list(response, "tables"):
            d = t.get("data")
            if not isinstance(d, dict):
                continue
            headers = d.get("headers")
            rows = d.get("rows")
            if not isinstance(headers, list) or not isinstance(rows, list):
                continue
            rows = [r for r in rows if isinstance(r, list)]
            if len(headers) >= 2 and len(rows) >= 2:
                d["rows"] = rows
                valid.append(t)
        return valid

    def _load_json_list(self, response: str, key: str) -> list:
        """Parse the model's JSON and return data[key] as a list of dicts.
        Models sometimes return a bare object or a dict instead of a list —
        treat anything unexpected as 'no result' instead of crashing."""
        if not response:
            return []
        try:
            data = json.loads(self._clean_json_response(response))
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(data, dict):
            return []
        items = data.get(key, [])
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return []
        return [i for i in items if isinstance(i, dict)]

    def generate_chart_for_section(self, topic: str, section_title: str, section_content: str, chart_count: int = 1) -> List[dict]:
        """
        CHART GENERATION — DIRECTED SYNTHESIS
        ──────────────────────────────────────
        Same root problem as generate_table_for_section: the old prompt
        asked "does this section have chart-worthy data?" — mistral answers
        "no" for all narrative content because there is no pre-existing
        quantitative dataset in the prose.

        Fix: instruct the model to SYNTHESISE plausible comparative data
        for the 3–4 key entities/concepts in the section, creating a bar
        chart that illustrates relative differences. For narrative content
        (animal populations, battle casualties, conservation impact), this
        produces meaningful charts that genuinely illustrate the section.
        """
        prompt = f"""Create a bar chart for a professional document section.

DOCUMENT TOPIC: {topic}
SECTION: {section_title}

SECTION CONTENT:
{section_content[:1800]}

YOUR TASK:
Identify 3–5 named entities, events, or categories from this section.
Build a bar chart showing a meaningful quantitative comparison between them.
Use realistic values based on the content — approximate figures are fine if
specific numbers are not stated (e.g., relative population sizes, impact scores,
time periods, casualty ranges, species counts, or conservation status rankings).

RULES:
- chart_type: use "bar" 
- 3–5 labels (the entities/categories being compared)
- One dataset with a meaningful metric label
- Values must be numbers (integers or floats)
- size: "medium"

Respond with ONLY this JSON, no preamble:
{{
  "charts": [
    {{
      "title": "Descriptive chart title",
      "chart_type": "bar",
      "size": "medium",
      "insert_after_paragraph": 3,
      "data": {{
        "labels": ["Entity 1", "Entity 2", "Entity 3", "Entity 4"],
        "datasets": [
          {{
            "label": "Metric name (unit)",
            "values": [100, 75, 50, 25]
          }}
        ]
      }},
      "caption": "One sentence explaining what this chart shows"
    }}
  ]
}}"""

        response = self._generate(prompt, max_tokens=900, temperature=0.5)
        # Validate minimum chart structure
        valid = []
        for c in self._load_json_list(response, "charts"):
            d = c.get("data")
            if not isinstance(d, dict):
                continue
            labels = d.get("labels")
            datasets = d.get("datasets")
            if not isinstance(labels, list) or not isinstance(datasets, list):
                continue
            datasets = [ds for ds in datasets
                        if isinstance(ds, dict) and isinstance(ds.get("values"), list)]
            if len(labels) >= 2 and datasets and len(datasets[0]["values"]) >= 2:
                d["datasets"] = datasets
                c["type"] = "chart"
                valid.append(c)
        return valid
