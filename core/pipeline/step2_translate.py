"""
Step 2 — Translate transcript segments with context awareness.

Backends:
  google   → Chunk mode (free, no key) — gộp nhiều segment, ít lỗi hơn
  gemini   → Gemini Flash (FREE, 1500 req/day) — context-aware, tốt nhất free
  openai   → GPT-4o-mini — context-aware + verify pass, tốt nhất overall

Context-aware modes (Gemini/OpenAI):
  - Gửi N segment trước/sau làm context
  - Tự extract character summary từ 20 segment đầu
  - Optional: verify pass để fix đại từ/tên không nhất quán
"""

import os
import re
import time
from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.pipeline.base import BaseStep

LANGUAGES = {
    "Vietnamese": "vi",
    "English": "en",
    "Japanese": "ja",
    "Korean": "ko",
    "Chinese (Simplified)": "zh-CN",
    "French": "fr",
    "German": "de",
    "Spanish": "es",
    "Thai": "th",
    "Indonesian": "id",
}
LANG_NAMES = {v: k for k, v in LANGUAGES.items()}

CHUNK_SEP = "|||"  # separator cho Google chunk mode


@dataclass
class TranslatedSegment:
    start: float
    end: float
    original: str
    translated: str


# ── SmartFixer — rule-based + context logic ───────────────────────────────────


class SmartFixer:
    """
    Fix common translation errors without any API.

    Problems it solves:
    1. Pronoun-gender mismatch: "她是我爸爸" → "cô ấy là bố tôi" → "Đây là bố tôi"
    2. Context-based pronoun: use surrounding lines to infer correct pronoun
    3. Common literal errors in ZH→VI, EN→VI, JA→VI
    """

    # (pattern_in_translated, pattern_in_original) → replacement
    # Order matters — more specific rules first
    ZH_VI_RULES = [
        # Female pronoun + male family member → "đây là"
        (["cô ấy là bố", "bà ấy là bố", "cô ta là bố"], "爸爸", "Đây là bố"),
        (["cô ấy là anh", "bà ấy là anh"], "哥哥", "Đây là anh"),
        (["cô ấy là ông", "bà ấy là ông"], "爷爷", "Đây là ông"),
        (["cô ấy là chú", "bà ấy là chú"], "叔叔", "Đây là chú"),
        # Male pronoun + female family member
        (["anh ấy là mẹ", "ông ấy là mẹ"], "妈妈", "Đây là mẹ"),
        (["anh ấy là chị", "ông ấy là chị"], "姐姐", "Đây là chị"),
        (["anh ấy là bà", "ông ấy là bà"], "奶奶", "Đây là bà"),
        # Literal "this is my X" patterns
        (["đây là của tôi bố"], "爸爸", "Đây là bố tôi"),
        (["đây là của tôi mẹ"], "妈妈", "Đây là mẹ tôi"),
    ]

    # Common word-level fixes (translated → corrected)
    WORD_FIXES_VI = {
        "cô ấy là bố tôi": "Đây là bố tôi",
        "cô ấy là anh tôi": "Đây là anh tôi",
        "cô ấy là ông tôi": "Đây là ông tôi",
        "bà ấy là bố tôi": "Đây là bố tôi",
        "bà ấy là anh tôi": "Đây là anh tôi",
        "anh ấy là mẹ tôi": "Đây là mẹ tôi",
        "anh ấy là chị tôi": "Đây là chị tôi",
        "ông ấy là mẹ tôi": "Đây là mẹ tôi",
    }

    def __init__(self, src_lang="zh", tgt_lang="vi"):
        self.src_lang = src_lang.lower()
        self.tgt_lang = tgt_lang.lower()

    def fix(
        self, original: str, translated: str, prev_segs=None, next_segs=None
    ) -> str:
        """Apply all fix strategies, return corrected translation."""
        t = translated.strip()
        if not t:
            return translated

        # 1. Direct word-level fix (fastest)
        t = self._word_fix(t)

        # 2. Rule-based: pronoun-gender mismatch
        if self.src_lang in ("zh", "zh-cn", "zh-tw"):
            t = self._zh_pronoun_fix(original, t)

        # 3. Context inference: use neighbours to fix pronoun
        if prev_segs or next_segs:
            t = self._context_pronoun_fix(original, t, prev_segs, next_segs)

        return t

    def _word_fix(self, text: str) -> str:
        lower = text.lower()
        for wrong, correct in self.WORD_FIXES_VI.items():
            if wrong in lower:
                # Preserve original capitalisation style
                return text[: len(text) - len(text.lstrip())] + correct
        return text

    def _zh_pronoun_fix(self, original: str, translated: str) -> str:
        """Fix pronoun-gender mismatch using original Chinese."""
        tl = translated.lower()
        for patterns, zh_keyword, replacement in self.ZH_VI_RULES:
            if zh_keyword in original:
                for p in patterns:
                    if p in tl:
                        return (
                            replacement
                            + translated[translated.lower().index(p) + len(p) :]
                        )
        return translated

    def _context_pronoun_fix(
        self, original: str, translated: str, prev_segs, next_segs
    ) -> str:
        """
        Use context to infer correct pronoun.
        E.g. if surrounding lines establish "bố" as the subject,
        and current line has wrong pronoun, fix it.
        """
        tl = translated.lower()

        # Collect context clues from neighbour originals
        all_neighbours = list(prev_segs or []) + list(next_segs or [])
        neighbour_originals = " ".join(
            s.original for s in all_neighbours if hasattr(s, "original")
        )
        neighbour_translated = " ".join(
            s.translated for s in all_neighbours if hasattr(s, "translated")
        )

        # If context establishes father/male → fix "cô ấy" → right
        if "爸爸" in neighbour_originals or "bố" in neighbour_translated:
            if "cô ấy" in tl or "bà ấy" in tl:
                fixed = (
                    translated.replace("Cô ấy", "Ông ấy")
                    .replace("cô ấy", "ông ấy")
                    .replace("Bà ấy", "Ông ấy")
                    .replace("bà ấy", "ông ấy")
                )
                if fixed != translated:
                    return fixed

        # If context establishes mother/female → fix "anh ấy" → right
        if "妈妈" in neighbour_originals or "mẹ" in neighbour_translated:
            if "anh ấy" in tl or "ông ấy" in tl:
                fixed = (
                    translated.replace("Anh ấy", "Cô ấy")
                    .replace("anh ấy", "cô ấy")
                    .replace("Ông ấy", "Cô ấy")
                    .replace("ông ấy", "cô ấy")
                )
                if fixed != translated:
                    return fixed

        return translated


class TranslateStep(BaseStep):
    STEP_ID = "step2_translate"
    LABEL = "② Translate"
    COLOR = "#1a6a48"
    ENABLED_BY_DEFAULT = True

    def __init__(self):
        self._backend_combo = None
        self._lang_combo = None
        self._api_lbl = None
        self._api_edit = None
        self._chunk_spin = None
        self._ctx_spin = None
        self._verify_chk = None
        self._verify_combo = None
        self._ollama_model_combo = None
        self._ollama_opts = None
        self._google_opts = None
        self._ai_opts = None
        self._rule_fix_chk = None

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(self, session, config, log, cancel):
        transcript = session.load_transcript()
        backend = config["backend"]
        target = config["target_lang"]
        api_key = config.get("api_key")
        chunk_size = config.get("chunk_size", 15)
        ctx_window = config.get("ctx_window", 5)
        do_verify = config.get("verify", False)
        verify_backend = config.get("verify_backend", "none")
        verify_model = config.get("verify_model", "llama3")
        do_rule_fix = config.get("rule_fix", True)
        segments = transcript.segments
        total = len(segments)
        src_lang = transcript.language

        log(f"🌏 Translating {total} segments → {target}")
        log(
            f"   Backend: {backend}"
            + (
                f" | chunks: {chunk_size}"
                if backend == "google"
                else f" | context: ±{ctx_window} segs"
            )
        )

        if cancel.is_set():
            from core.pipeline.base import CancelledError

            raise CancelledError()

        if backend == "google":
            out = self._translate_chunks(
                segments, target, chunk_size, log, cancel, src_lang=src_lang
            )
        elif backend == "gemini":
            out = self._translate_gemini(
                segments, target, api_key, ctx_window, log, cancel, src_lang=src_lang
            )
        elif backend == "openai":
            out = self._translate_openai(
                segments, target, api_key, ctx_window, log, cancel
            )
        else:
            raise RuntimeError(f"Unknown backend: {backend}")

        # ── Rule-based smart fix (always fast, no API needed) ──
        if do_rule_fix:
            fixer = SmartFixer(src_lang=src_lang, tgt_lang=target)
            fixed_count = 0
            for i, seg in enumerate(out):
                fixed = fixer.fix(
                    original=seg.original,
                    translated=seg.translated,
                    prev_segs=out[max(0, i - 3) : i],
                    next_segs=out[i + 1 : min(len(out), i + 3)],
                )
                if fixed != seg.translated:
                    out[i] = TranslatedSegment(seg.start, seg.end, seg.original, fixed)
                    fixed_count += 1
            if fixed_count:
                log(f"🔧 Rule-fix: corrected {fixed_count} segments")

        # ── AI verify pass ──
        if do_verify and verify_backend != "none":
            log(f"🔍 Running verify pass via {verify_backend}…")
            out = self._verify_pass(
                out, target, backend, api_key, verify_backend, verify_model, log
            )

        log(f"✅ Done — {total} segments")
        session.save_translated(out)
        return out

    # ── Google: Chunk mode ────────────────────────────────────────────────────

    def _translate_chunks(
        self, segments, target, chunk_size, log, cancel, src_lang="auto"
    ):
        try:
            from deep_translator import GoogleTranslator
        except ImportError:
            raise RuntimeError("Run: pip install deep-translator")
        import concurrent.futures

        LANG_MAP = {
            "zh": "zh-TW",
            "zh-cn": "zh-CN",
            "zh-tw": "zh-TW",
            "ja": "ja",
            "ko": "ko",
            "en": "en",
            "vi": "vi",
            "fr": "fr",
            "de": "de",
        }
        google_src = LANG_MAP.get(src_lang.lower(), "auto")
        log(f"   Source: {src_lang} → Google: {google_src}")

        chunks = [
            segments[i : i + chunk_size] for i in range(0, len(segments), chunk_size)
        ]
        total = len(segments)
        log(f"   {total} segs → {len(chunks)} chunks | ⚡ parallel (3 workers)")

        results = [None] * len(chunks)

        def translate_chunk(ci_chunk):
            ci, chunk = ci_chunk
            texts = [s.text.strip() or " " for s in chunk]
            joined = CHUNK_SEP.join(texts)
            try:
                translated = GoogleTranslator(
                    source=google_src, target=target
                ).translate(joined)
                parts = translated.split(CHUNK_SEP)
                if len(parts) != len(chunk):
                    parts = self._google_individual(texts, target, google_src)
                untranslated = sum(
                    1
                    for o, t in zip(texts, parts)
                    if o.strip() and t.strip() == o.strip()
                )
                if untranslated > len(chunk) * 0.5:
                    parts = self._google_individual(texts, target, google_src)
                return ci, parts
            except Exception:
                return ci, self._google_individual(texts, target, google_src)

        done = [0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futures = {
                ex.submit(translate_chunk, (ci, chunk)): ci
                for ci, chunk in enumerate(chunks)
            }
            for fut in concurrent.futures.as_completed(futures):
                if cancel.is_set():
                    from core.pipeline.base import CancelledError

                    raise CancelledError()
                ci, parts = fut.result()
                results[ci] = (chunks[ci], parts)
                done[0] += 1
                segs_done = min(done[0] * chunk_size, total)
                log(f"   [{segs_done}/{total}] translated")

        # Assemble in order
        out = []
        for chunk, parts in results:
            for seg, trans in zip(chunk, parts):
                out.append(
                    TranslatedSegment(
                        seg.start,
                        seg.end,
                        seg.text.strip(),
                        trans.strip() or seg.text.strip(),
                    )
                )
        return out

    def _google_individual(self, texts, target, source="auto"):
        from deep_translator import GoogleTranslator

        results = []
        for txt in texts:
            try:
                t = GoogleTranslator(source=source, target=target).translate(
                    txt.strip() or " "
                )
                # If result same as input, try with auto
                if t.strip() == txt.strip() and source != "auto":
                    t = GoogleTranslator(source="auto", target=target).translate(
                        txt.strip() or " "
                    )
                results.append(t)
                time.sleep(0.1)
            except Exception:
                results.append(txt)
        return results

    # ── Gemini: Context-aware (FREE) ──────────────────────────────────────────

    def _gemini_client(self, api_key):
        """Return Gemini client using new google.genai SDK."""
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            return client, types
        except ImportError:
            raise RuntimeError(
                "Run: pip install google-genai\n"
                "Get free API key: aistudio.google.com"
            )

    def _gemini_generate(self, client, types, prompt, retries=3, log=None):
        """Generate content with auto-retry on 429 rate limit."""
        import time as _time

        _log = log or (lambda m: print(m))
        for attempt in range(retries):
            try:
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=prompt,
                )
                return response.text
            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    import re as _re

                    m = _re.search(r"retryDelay.*?(\d+)s", err)
                    wait = int(m.group(1)) + 5 if m else 65
                    if attempt < retries - 1:
                        _log(
                            f"   ⏳ Rate limited — waiting {wait}s… (attempt {attempt+1}/{retries})"
                        )
                        _time.sleep(wait)
                        continue
                raise
        raise RuntimeError("Gemini: max retries exceeded")

    def _translate_gemini(
        self, segments, target, api_key, ctx_window, log, cancel, src_lang="unknown"
    ):
        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError(
                "Gemini API key required.\n"
                "Get FREE key at: aistudio.google.com → Get API Key\n"
                "Then enter it in the UI or set GEMINI_API_KEY env var."
            )

        client, types = self._gemini_client(key)
        lang_name = LANG_NAMES.get(target, target)
        total = len(segments)
        log(f"   Source lang: {src_lang} → Target: {lang_name} ({target})")

        # Extract context summary
        preview = " ".join(s.text for s in segments[:20])
        summary = self._extract_summary_gemini(
            client, types, preview, lang_name, src_lang
        )
        log(f"   📖 Context: {summary[:100]}…")

        # Few-shot examples
        examples = self._build_examples(src_lang, target)

        system_ctx = (
            f"You are a subtitle translator.\n"
            f"Translate {src_lang.upper()} subtitles into {lang_name}.\n\n"
            f"IMPORTANT: Input is {src_lang.upper()}. Output MUST be {lang_name}.\n"
            f"DO NOT copy the original text. ALWAYS translate.\n\n"
            f"EXAMPLES ({src_lang} → {lang_name}):\n{examples}\n\n"
            f"CONTENT CONTEXT:\n{summary}\n\n"
            f"OUTPUT FORMAT: numbered list in {lang_name} only.\n"
            f"No explanations. No original text. {lang_name} only."
        )

        batch_size = 20
        out = []

        for bi in range(0, total, batch_size):
            if cancel.is_set():
                from core.pipeline.base import CancelledError

                raise CancelledError()

            batch = segments[bi : bi + batch_size]
            ctx_before = segments[max(0, bi - ctx_window) : bi]
            ctx_after = segments[
                bi + len(batch) : min(total, bi + len(batch) + ctx_window)
            ]

            prompt = self._build_batch_prompt(
                batch, ctx_before, ctx_after, lang_name, system_ctx
            )

            try:
                text = self._gemini_generate(client, types, prompt, log=log)
                parts = self._parse_numbered_response(text, len(batch))

                if len(parts) != len(batch):
                    log(
                        f"   ⚠️  Batch {bi//batch_size+1}: "
                        f"got {len(parts)}/{len(batch)} — fallback individual"
                    )
                    parts = self._gemini_individual(
                        client, types, batch, system_ctx, log
                    )

            except Exception as e:
                err = str(e)
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    # Extract wait time and retry once more
                    import re as _re

                    m = _re.search(r"retryDelay.*?(\d+)s", err)
                    wait = int(m.group(1)) + 5 if m else 65
                    log(f"   ⏳ Rate limited — waiting {wait}s then retrying…")
                    time.sleep(wait)
                    try:
                        text = self._gemini_generate(client, types, prompt, log=log)
                        parts = self._parse_numbered_response(text, len(batch))
                        if len(parts) != len(batch):
                            parts = self._gemini_individual(
                                client, types, batch, system_ctx, log
                            )
                    except Exception as e2:
                        log(f"   ❌ Retry failed: {e2} — keeping originals")
                        parts = [s.text for s in batch]
                else:
                    log(f"   ⚠️  Batch error: {e}")
                    parts = [s.text for s in batch]

            for seg, trans in zip(batch, parts):
                out.append(
                    TranslatedSegment(
                        seg.start,
                        seg.end,
                        seg.text.strip(),
                        trans.strip() or seg.text.strip(),
                    )
                )

            log(f"   [{min(bi + batch_size, total)}/{total}] translated")
            time.sleep(4)  # gemini-2.0-flash free: 15 req/min = 1 req/4s

        return out

    def _build_batch_prompt(self, batch, ctx_before, ctx_after, lang_name, system_ctx):
        lines = [system_ctx, ""] if system_ctx else []
        if ctx_before:
            lines.append("=== Previous lines (context only, do NOT translate) ===")
            lines.extend(f"  {s.text.strip()}" for s in ctx_before)
            lines.append("")
        lines.append(
            f"=== TRANSLATE THESE LINES INTO {lang_name.upper()} ===\n"
            f"Each line below must be translated into {lang_name}.\n"
            f"Return ONLY a numbered list in {lang_name}. No original text.\n"
        )
        for i, seg in enumerate(batch, 1):
            lines.append(f"{i}. {seg.text.strip()}")
        if ctx_after:
            lines.append("")
            lines.append("=== Next lines (context only, do NOT translate) ===")
            lines.extend(f"  {s.text.strip()}" for s in ctx_after)
        lines.append(f"\nOUTPUT: numbered list in {lang_name} ONLY.")
        return "\n".join(lines)

    def _parse_numbered_response(self, text, expected):
        """Parse '1. translation\n2. translation...' response."""
        lines = re.findall(r"^\d+\.\s*(.+)$", text, re.MULTILINE)
        return lines if len(lines) == expected else []

    def _gemini_individual(self, client, types, batch, system_ctx, log):
        """Fallback: translate one by one."""
        results = []
        for seg in batch:
            try:
                prompt = (
                    f"{system_ctx}\n\n"
                    f"Translate this single subtitle line:\n{seg.text.strip()}"
                )
                text = self._gemini_generate(client, types, prompt, log=log)
                results.append(text.strip())
                time.sleep(0.5)
            except Exception as e:
                log(f"   ⚠️  Individual fallback failed: {e}")
                results.append(seg.text)
        return results

    def _build_examples(self, src_lang: str, target: str) -> str:
        """Return few-shot translation examples for common language pairs."""
        examples_map = {
            ("zh", "vi"): [
                ("我的家", "Nhà của tôi"),
                ("她是我爸爸", "Cô ấy là bố tôi"),
                ("我爱你", "Tôi yêu bạn"),
                ("你好吗", "Bạn có khỏe không?"),
            ],
            ("zh-cn", "vi"): [
                ("我的家", "Nhà của tôi"),
                ("她是我爸爸", "Cô ấy là bố tôi"),
            ],
            ("ja", "vi"): [
                ("私の家", "Nhà của tôi"),
                ("ありがとう", "Cảm ơn bạn"),
            ],
            ("en", "vi"): [
                ("My name is Anna", "Tên tôi là Anna"),
                ("How are you?", "Bạn có khỏe không?"),
            ],
            ("ko", "vi"): [
                ("안녕하세요", "Xin chào"),
                ("감사합니다", "Cảm ơn bạn"),
            ],
        }
        key = (src_lang.lower(), target.lower())
        # Try exact match, then partial src match
        exs = examples_map.get(key, [])
        if not exs:
            for (s, t), v in examples_map.items():
                if src_lang.lower().startswith(s) and t == target.lower():
                    exs = v
                    break
        if not exs:
            lang_name = LANG_NAMES.get(target, target)
            return f"Input: [original text] → Output: [{lang_name} translation]"

        lang_name = LANG_NAMES.get(target, target)
        lines = []
        for orig, trans in exs:
            lines.append(f"  Input:  {orig}")
            lines.append(f"  Output: {trans}")
        return "\n".join(lines)

    def _extract_summary_gemini(
        self, client, types, preview_text, lang_name, src_lang="unknown"
    ):
        try:
            prompt = (
                f"This is a subtitle excerpt in {src_lang}.\n"
                f"Briefly identify in 2-3 sentences (write in English):\n"
                f"1. Main characters and their relationships\n"
                f"2. Topic/setting\n"
                f"3. What pronouns to use when translating to {lang_name}\n\n"
                f"Subtitle text:\n{preview_text}"
            )
            return self._gemini_generate(client, types, prompt).strip()
        except Exception:
            return (
                f"Subtitle content in {src_lang}. "
                f"Translate all text to {lang_name}."
            )

    # ── OpenAI: Context-aware ─────────────────────────────────────────────────

    def _translate_openai(self, segments, target, api_key, ctx_window, log, cancel):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("Run: pip install openai")

        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("Set OPENAI_API_KEY env var or enter key in UI.")

        client = OpenAI(api_key=key)
        lang_name = LANG_NAMES.get(target, target)
        total = len(segments)

        # Context summary
        preview = " ".join(s.text for s in segments[:20])
        summary = self._extract_summary_openai(client, preview, lang_name)
        log(f"   📖 Context: {summary[:100]}…")

        system_prompt = (
            f"You are a professional subtitle translator.\n"
            f"Translate subtitles to {lang_name}.\n\n"
            f"CONTENT CONTEXT:\n{summary}\n\n"
            f"RULES:\n"
            f"- Keep tone consistent\n"
            f"- Use correct pronouns based on context\n"
            f"- Return ONLY the translated line\n"
            f"- Keep [music], [laughter] etc. as-is\n"
            f"- Do NOT translate proper nouns/names"
        )

        # Batch mode: 20 segments per request (faster + cheaper)
        batch_size = 20
        out = []

        for bi in range(0, total, batch_size):
            if cancel.is_set():
                from core.pipeline.base import CancelledError

                raise CancelledError()

            batch = segments[bi : bi + batch_size]
            ctx_before = segments[max(0, bi - ctx_window) : bi]
            ctx_after = segments[
                bi + len(batch) : min(total, bi + len(batch) + ctx_window)
            ]

            user_msg = self._build_batch_prompt(
                batch, ctx_before, ctx_after, lang_name, ""
            )

            try:
                r = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.2,
                    max_tokens=1000,
                )
                text = r.choices[0].message.content.strip()
                parts = self._parse_numbered_response(text, len(batch))

                if len(parts) != len(batch):
                    log("   ⚠️  Batch parse mismatch — fallback individual")
                    parts = self._openai_individual(batch, system_prompt, client, log)

            except Exception as e:
                log(f"   ⚠️  Batch error: {e}")
                parts = [s.text for s in batch]

            for seg, trans in zip(batch, parts):
                out.append(
                    TranslatedSegment(
                        seg.start,
                        seg.end,
                        seg.text.strip(),
                        trans.strip() or seg.text.strip(),
                    )
                )

            log(f"   [{min(bi + batch_size, total)}/{total}] translated")

        return out

    def _openai_individual(self, batch, system_prompt, client, log):
        results = []
        for seg in batch:
            try:
                r = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": seg.text.strip()},
                    ],
                    temperature=0.2,
                    max_tokens=200,
                )
                results.append(r.choices[0].message.content.strip())
            except Exception as e:
                log(f"   ⚠️  Individual failed: {e}")
                results.append(seg.text)
        return results

    def _extract_summary_openai(self, client, preview_text, lang_name):
        try:
            r = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"From this subtitle excerpt, briefly identify in 2-3 sentences:\n"
                            f"1. Main characters and relationships\n"
                            f"2. Topic/setting\n"
                            f"3. Correct pronouns to use in {lang_name}\n\n"
                            f"Text:\n{preview_text}"
                        ),
                    }
                ],
                temperature=0.3,
                max_tokens=150,
            )
            return r.choices[0].message.content.strip()
        except Exception:
            return f"General subtitle. Translate naturally to {lang_name}."

    # ── Verify pass ───────────────────────────────────────────────────────────

    def _verify_pass(
        self, segments, target, backend, api_key, verify_backend, verify_model, log
    ):
        """
        Second pass — fix pronouns, names, logic errors.
        Key insight: send BOTH original + translation so model can
        detect contradictions like 她(female)=爸爸(male).
        """
        if verify_backend == "none":
            return segments

        lang_name = LANG_NAMES.get(target, target)
        src_lang = "the source language"
        batch_size = 15  # smaller batches = better accuracy for local models
        out = []
        log(f"🔍 Verify pass via {verify_backend} ({verify_model})…")
        log("   Sending bilingual pairs (original + translation) for context")

        for bi in range(0, len(segments), batch_size):
            batch = segments[bi : bi + batch_size]

            # Build bilingual numbered list: original + translation side by side
            bilingual = "\n".join(
                f"{j+1}. [{s.original}] → {s.translated}" for j, s in enumerate(batch)
            )

            prompt = self._build_verify_prompt(bilingual, lang_name, verify_backend)

            try:
                if verify_backend == "ollama":
                    fixed_text = self._ollama_generate(prompt, verify_model, log)
                elif verify_backend == "gemini":
                    client, types = self._gemini_client(
                        api_key or os.environ.get("GEMINI_API_KEY", "")
                    )
                    fixed_text = self._gemini_generate(
                        client, types, prompt, log=log
                    ).strip()
                    time.sleep(0.5)
                elif verify_backend == "openai":
                    from openai import OpenAI

                    client = OpenAI(
                        api_key=api_key or os.environ.get("OPENAI_API_KEY", "")
                    )
                    r = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=2000,
                    )
                    fixed_text = r.choices[0].message.content.strip()
                else:
                    out.extend(batch)
                    continue

                fixed_lines = self._parse_numbered_response(fixed_text, len(batch))

                if len(fixed_lines) == len(batch):
                    for seg, fixed in zip(batch, fixed_lines):
                        out.append(
                            TranslatedSegment(
                                seg.start, seg.end, seg.original, fixed.strip()
                            )
                        )
                    log(f"   ✅ Fixed [{bi+len(batch)}/{len(segments)}]")
                else:
                    log(
                        f"   ⚠️  Got {len(fixed_lines)}/{len(batch)} — keeping originals"
                    )
                    out.extend(batch)

            except Exception as e:
                log(f"   ⚠️  Verify error: {e}")
                out.extend(batch)

        return out

    def _build_verify_prompt(self, bilingual: str, lang_name: str, backend: str) -> str:
        """
        Build a bilingual verify prompt.
        Shows [ORIGINAL] → translation so model can spot contradictions.
        """
        # Shorter prompt for local models (less VRAM)
        if backend == "ollama":
            return (
                f"You are a subtitle editor fixing {lang_name} translations.\n\n"
                f"FORMAT: [original_text] → current_translation\n"
                f"Each line is a subtitle. The original may have errors "
                f"(e.g. wrong pronoun: 她=she used for a male character).\n\n"
                f"TASK:\n"
                f"- Look at [original] to understand who/what is being described\n"
                f"- Fix the translation if it contradicts the original\n"
                f"- Common error: female pronoun (她/cô ấy) for male (爸爸/bố)\n"
                f"  Fix: 'cô ấy là bố tôi' → 'Đây là bố tôi'\n"
                f"- Keep meaning, fix ONLY clear errors\n"
                f"- Return ONLY numbered list of {lang_name} translations\n\n"
                f"Subtitles:\n{bilingual}\n\n"
                f"Return numbered list (1. ... 2. ... etc):"
            )
        else:
            # Longer, more detailed prompt for API models
            return (
                f"You are a professional {lang_name} subtitle editor.\n\n"
                f"I will give you subtitles in format: [ORIGINAL] → current_translation\n"
                f"The original text may contain errors (e.g. wrong pronouns like\n"
                f"她=she used for a male character 爸爸=father).\n\n"
                f"YOUR TASK:\n"
                f"1. Read the [ORIGINAL] to understand the true meaning\n"
                f"2. Check if the translation correctly reflects the original\n"
                f"3. Fix ONLY logical errors, especially:\n"
                f"   - Gender/pronoun mismatch: '她是我爸爸' should be 'Đây là bố tôi'\n"
                f"     NOT 'Cô ấy là bố tôi' (contradicts itself)\n"
                f"   - Inconsistent character names across lines\n"
                f"   - Broken sentences that miss the original meaning\n"
                f"4. Do NOT change correct translations\n"
                f"5. Return ONLY a numbered list in {lang_name}\n\n"
                f"Subtitles to review:\n{bilingual}\n\n"
                f"Return numbered list only:"
            )

    # ── Ollama (local, free) ──────────────────────────────────────────────────

    def _ollama_generate(self, prompt, model="llama3", log=None):
        """
        Call local Ollama API.
        Install: https://ollama.com
        Pull model: ollama pull llama3
                    ollama pull mistral
                    ollama pull gemma2
        """
        import json as _json
        import urllib.request

        _log = log or (lambda m: None)

        payload = _json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 2048},
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
                return data.get("response", "")
        except Exception as e:
            if "Connection refused" in str(e) or "refused" in str(e).lower():
                raise RuntimeError(
                    "Ollama not running.\n"
                    "Start it with: ollama serve\n"
                    "Then pull a model: ollama pull llama3"
                )
            raise

    # ── Config widget ─────────────────────────────────────────────────────────

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # ── Translation backend ──
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Backend:"))
        self._backend_combo = QComboBox()
        self._backend_combo.addItems(
            [
                "Gemini Flash (free ⭐)",
                "Google Translate (free)",
                "OpenAI GPT-4o-mini",
            ]
        )
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        r1.addWidget(self._backend_combo)
        r1.addStretch()
        v.addLayout(r1)

        # Target lang
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Target lang:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(LANGUAGES.keys())
        self._lang_combo.setCurrentText("Vietnamese")
        r2.addWidget(self._lang_combo)
        r2.addStretch()
        v.addLayout(r2)

        # API key
        self._api_lbl = QLabel("API Key:")
        self._api_edit = QLineEdit()
        self._api_edit.setPlaceholderText("Gemini: aistudio.google.com")
        self._api_edit.setEchoMode(QLineEdit.EchoMode.Password)
        v.addWidget(self._api_lbl)
        v.addWidget(self._api_edit)

        # Google chunk opts
        self._google_opts = QWidget()
        go = QHBoxLayout(self._google_opts)
        go.setContentsMargins(0, 0, 0, 0)
        go.addWidget(QLabel("Chunk size:"))
        self._chunk_spin = QSpinBox()
        self._chunk_spin.setRange(5, 50)
        self._chunk_spin.setValue(15)
        self._chunk_spin.setFixedWidth(60)
        self._chunk_spin.setToolTip("Segments per request — larger = better context")
        go.addWidget(self._chunk_spin)
        go.addWidget(QLabel("segs/req"))
        go.addStretch()
        self._google_opts.setVisible(False)
        v.addWidget(self._google_opts)

        # AI context opts (Gemini / OpenAI)
        self._ai_opts = QWidget()
        ao = QHBoxLayout(self._ai_opts)
        ao.setContentsMargins(0, 0, 0, 0)
        ao.addWidget(QLabel("Context:"))
        self._ctx_spin = QSpinBox()
        self._ctx_spin.setRange(1, 10)
        self._ctx_spin.setValue(5)
        self._ctx_spin.setFixedWidth(55)
        ao.addWidget(self._ctx_spin)
        ao.addWidget(QLabel("segs"))
        ao.addStretch()
        v.addWidget(self._ai_opts)

        # ── Separator ──
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#2d2d4e;margin:4px 0;")
        v.addWidget(sep)

        # ── Smart fix ──
        fix_lbl = QLabel("🔧 Smart Fix (rule-based, always free)")
        fix_lbl.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;")
        v.addWidget(fix_lbl)

        self._rule_fix_chk = QCheckBox("Auto-fix pronoun/gender errors")
        self._rule_fix_chk.setChecked(True)
        self._rule_fix_chk.setToolTip(
            "Fix common errors like:\n"
            "  '她是我爸爸' → 'Đây là bố tôi' (not 'Cô ấy là bố tôi')\n"
            "Uses rule-based logic + context from nearby lines.\n"
            "No API needed, instant."
        )
        v.addWidget(self._rule_fix_chk)

        # ── Verify pass section ──
        verify_lbl = QLabel("🔍 Verify & Fix Pass")
        verify_lbl.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;")
        v.addWidget(verify_lbl)

        rv = QHBoxLayout()
        rv.addWidget(QLabel("Verify via:"))
        self._verify_combo = QComboBox()
        self._verify_combo.addItems(
            [
                "None (skip)",
                "Ollama — local free ⭐",
                "Gemini Flash — free",
                "OpenAI GPT-4o-mini",
            ]
        )
        self._verify_combo.setToolTip(
            "Run a 2nd AI pass to fix pronouns, names, grammar"
        )
        self._verify_combo.currentIndexChanged.connect(self._on_verify_changed)
        rv.addWidget(self._verify_combo)
        rv.addStretch()
        v.addLayout(rv)

        # Ollama model opts
        self._ollama_opts = QWidget()
        ov2 = QVBoxLayout(self._ollama_opts)
        ov2.setContentsMargins(0, 0, 0, 0)
        ov2.setSpacing(2)

        ov = QHBoxLayout()
        ov.setContentsMargins(0, 0, 0, 0)
        ov.addWidget(QLabel("Model:"))
        self._ollama_model_combo = QComboBox()
        self._ollama_model_combo.addItems(
            [
                "qwen2   — 🇨🇳 Tiếng Trung → VI (recommended)",
                "llama3  — 🇬🇧 Tiếng Anh → VI (best English)",
                "llama3.1 — 🇬🇧 Tiếng Anh → VI (newer)",
                "mistral — 💾 RAM thấp <8GB (nhẹ nhất, 4.1GB)",
                "gemma2  — ⚖️  Cân bằng (Google, 5.4GB)",
            ]
        )
        self._ollama_model_combo.setCurrentIndex(0)
        self._ollama_model_combo.setToolTip(
            "Video tiếng Trung  →  qwen2\n"
            "Video tiếng Anh    →  llama3\n"
            "RAM thấp (<8GB)    →  mistral\n\n"
            "Cài model:\n"
            "  ollama pull qwen2\n"
            "  ollama pull llama3\n"
            "  ollama pull mistral"
        )
        ov.addWidget(self._ollama_model_combo)
        ov.addStretch()
        ov2.addLayout(ov)

        hint = QLabel("Install: ollama.com  |  Pull: ollama pull qwen2")
        hint.setStyleSheet("color:#555;font-size:10px;")
        ov2.addWidget(hint)
        self._ollama_opts.setVisible(False)
        v.addWidget(self._ollama_opts)

        # Default: Google Translate, skip verify
        self._backend_combo.setCurrentIndex(1)  # Google Translate
        self._verify_combo.setCurrentIndex(0)  # None (skip)
        self._rule_fix_chk.setChecked(True)
        self._on_backend_changed(1)
        return w

    def _on_backend_changed(self, idx):
        is_google = idx == 1
        needs_key = idx != 1
        self._api_lbl.setVisible(needs_key)
        self._api_edit.setVisible(needs_key)
        self._google_opts.setVisible(is_google)
        self._ai_opts.setVisible(not is_google)
        if idx == 0:
            self._api_edit.setPlaceholderText(
                "Gemini API key — free at aistudio.google.com"
            )
        elif idx == 2:
            self._api_edit.setPlaceholderText("OpenAI API key — platform.openai.com")

    def _on_verify_changed(self, idx):
        # 0=None, 1=Ollama, 2=Gemini, 3=OpenAI
        self._ollama_opts.setVisible(idx == 1)

    def collect_config(self):
        idx = self._backend_combo.currentIndex() if self._backend_combo else 0
        backend_map = {0: "gemini", 1: "google", 2: "openai"}
        backend = backend_map.get(idx, "gemini")

        v_idx = self._verify_combo.currentIndex() if self._verify_combo else 0
        verify_map = {0: "none", 1: "ollama", 2: "gemini", 3: "openai"}
        verify_backend = verify_map.get(v_idx, "none")

        ollama_model = "qwen2"
        if self._ollama_model_combo:
            # Extract model name — format: "qwen2   — description"
            raw = self._ollama_model_combo.currentText()
            ollama_model = raw.split("—")[0].strip().split()[0].strip()

        return {
            "backend": backend,
            "target_lang": LANGUAGES.get(
                self._lang_combo.currentText() if self._lang_combo else "Vietnamese",
                "vi",
            ),
            "api_key": self._api_edit.text().strip() or None,
            "chunk_size": self._chunk_spin.value() if self._chunk_spin else 15,
            "ctx_window": self._ctx_spin.value() if self._ctx_spin else 5,
            "verify": verify_backend != "none",
            "verify_backend": verify_backend,
            "verify_model": ollama_model,
            "rule_fix": self._rule_fix_chk.isChecked() if self._rule_fix_chk else True,
        }
