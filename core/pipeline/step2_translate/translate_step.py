"""
Step 2 — Translate transcript segments (UI + backends).
"""

import concurrent.futures
import os
import re
import threading
import time

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
from core.pipeline.selection import (
    ollama_model_from_combo_text,
    translate_backend_from_index,
    verify_backend_from_index,
)
from core.pipeline.step2_translate.constants import (
    CHUNK_SEP,
    LANGUAGES,
    LANG_NAMES,
    TRANSLATION_COST_PER_1M_CHARS,
)
from core.pipeline.step2_translate.segment import TranslatedSegment
from core.pipeline.step2_translate.smart_fixer import SmartFixer

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
            openai_model = config.get("openai_model", "gpt-4o-mini")
            out = self._translate_openai(
                segments, target, api_key, ctx_window, log, cancel, model=openai_model
            )
        else:
            raise RuntimeError(f"Unknown backend: {backend}")

        # ── Rule-based smart fix ──
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

    # ── Google: Chunk mode (already parallel, unchanged) ──────────────────────

    def _translate_chunks(
        self, segments, target, chunk_size, log, cancel, src_lang="auto"
    ):
        try:
            from deep_translator import GoogleTranslator
        except ImportError:
            raise RuntimeError("Run: pip install deep-translator")

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
                if t.strip() == txt.strip() and source != "auto":
                    t = GoogleTranslator(source="auto", target=target).translate(
                        txt.strip() or " "
                    )
                results.append(t)
                time.sleep(0.1)
            except Exception:
                results.append(txt)
        return results

    # ── Gemini: Concurrent (optimized) ───────────────────────────────────────

    def _translate_gemini(
        self, segments, target, api_key, ctx_window, log, cancel, src_lang="unknown"
    ):
        """
        Concurrent Gemini translation với adaptive rate limiting.

        Thay vì sleep(4) cứng sequential, gửi tất cả batches qua
        ThreadPoolExecutor với semaphore kiểm soát concurrent requests:
          - 2 workers (free tier: 15 RPM → ~7.5 RPM actual, safe)
          - Retry với exponential backoff khi gặp 429
          - Kết quả reassemble theo đúng thứ tự ban đầu
        """
        key = api_key or ""
        if not key:
            try:
                from core.api_keys import get_key

                key = get_key("gemini") or os.environ.get("GEMINI_API_KEY", "")
            except Exception:
                key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError(
                "Gemini API key required.\n"
                "Get FREE key at: aistudio.google.com → Get API Key\n"
                "Then enter via API Keys Manager."
            )

        client, types = self._gemini_client(key)
        lang_name = LANG_NAMES.get(target, target)
        total = len(segments)
        log(f"   Source lang: {src_lang} → Target: {lang_name} ({target})")

        log(f"   📖 Building full-script context summary ({len(segments)} segs)…")
        script_sample = self._sample_script_for_summary(segments, max_chars=5000)
        summary = self._extract_summary_gemini(
            client, types, script_sample, lang_name, src_lang
        )
        log(f"   📖 Context brief: {summary[:120]}…")

        examples = self._build_examples(src_lang, target)
        system_ctx = (
            f"You are a professional subtitle translator.\n"
            f"Translate {src_lang.upper()} subtitles into {lang_name}.\n\n"
            f"IMPORTANT: Input is {src_lang.upper()}. Output MUST be {lang_name}.\n"
            f"DO NOT copy the original text. ALWAYS translate.\n\n"
            f"EXAMPLES ({src_lang} → {lang_name}):\n{examples}\n\n"
            f"=== FULL SCRIPT CONTEXT BRIEF ===\n"
            f"{summary}\n"
            f"=== END CONTEXT BRIEF ===\n\n"
            f"Use EXACTLY the character names and pronouns from the context brief above.\n"
            f"OUTPUT FORMAT: numbered list in {lang_name} only.\n"
            f"No explanations. No original text. {lang_name} only."
        )

        batch_size = 20
        batches = []
        for bi in range(0, total, batch_size):
            batch = segments[bi : bi + batch_size]
            ctx_before = segments[max(0, bi - ctx_window) : bi]
            ctx_after = segments[
                bi + len(batch) : min(total, bi + len(batch) + ctx_window)
            ]
            batches.append((bi, batch, ctx_before, ctx_after))

        log(
            f"   ⚡ Concurrent Gemini: {len(batches)} batches | 2 workers "
            f"(free tier safe — ~{len(batches) * batch_size // max(len(batches), 1)} segs/batch)"
        )

        # Semaphore: 2 concurrent để tránh 429 burst trên free tier
        # Gemini free = 15 RPM → với 2 workers mỗi req ~3-5s = ~12-15 RPM, an toàn
        sem = threading.Semaphore(2)
        results: dict[int, list[str]] = {}
        errors: dict[int, str] = {}

        def translate_batch(args):
            bi, batch, ctx_before, ctx_after = args
            if cancel.is_set():
                return

            prompt = self._build_batch_prompt(
                batch, ctx_before, ctx_after, lang_name, system_ctx
            )

            with sem:
                for attempt in range(4):
                    if cancel.is_set():
                        return
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

                        results[bi] = parts
                        return

                    except Exception as e:
                        err = str(e)
                        if "429" in err or "RESOURCE_EXHAUSTED" in err:
                            m = re.search(r"retryDelay.*?(\d+)s", err)
                            wait = int(m.group(1)) + 2 if m else (30 * (attempt + 1))
                            wait = min(wait, 90)
                            if attempt < 3:
                                log(
                                    f"   ⏳ Batch {bi//batch_size+1} rate-limited — "
                                    f"retry in {wait}s ({attempt+1}/4)"
                                )
                                time.sleep(wait)
                                continue
                        log(f"   ⚠️  Batch {bi//batch_size+1} error: {e}")
                        errors[bi] = str(e)
                        results[bi] = [s.text for s in batch]
                        return

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(translate_batch, args): args for args in batches}
            for fut in concurrent.futures.as_completed(futures):
                if cancel.is_set():
                    from core.pipeline.base import CancelledError

                    raise CancelledError()
                bi = futures[fut][0]
                segs_done = min(bi + batch_size, total)
                log(f"   [{segs_done}/{total}] translated")
                try:
                    fut.result()
                except Exception as e:
                    log(f"   ⚠️  Batch future error: {e}")

        if errors:
            log(
                f"   ⚠️  {len(errors)}/{len(batches)} batches had errors (kept originals)"
            )

        # Reassemble in original segment order
        out = []
        for bi, batch, _, _ in batches:
            parts = results.get(bi, [s.text for s in batch])
            for seg, trans in zip(batch, parts):
                out.append(
                    TranslatedSegment(
                        seg.start,
                        seg.end,
                        seg.text.strip(),
                        trans.strip() or seg.text.strip(),
                    )
                )
        return out

    # ── OpenAI: Concurrent (optimized) ───────────────────────────────────────

    def _translate_openai(
        self,
        segments,
        target,
        api_key,
        ctx_window,
        log,
        cancel,
        model: str = "gpt-4o-mini",
    ):
        """
        Concurrent OpenAI translation.

        model: "gpt-4o" (higher quality) or "gpt-4o-mini" (cheaper, faster)
        GPT-4o-mini rate limit: ~500 RPM tier 1
        GPT-4o rate limit: ~500 RPM tier 1 (same RPM, higher quality per request)
        5 workers × batch_size=20 in-flight, ~25 RPM — safe for both models.
        """
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("Run: pip install openai")

        key = api_key or ""
        if not key:
            try:
                from core.api_keys import get_key

                key = get_key("openai") or os.environ.get("OPENAI_API_KEY", "")
            except Exception:
                key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("Enter OpenAI API key via API Keys Manager.")

        client = OpenAI(api_key=key)
        lang_name = LANG_NAMES.get(target, target)
        total = len(segments)

        # Build full-script summary — sample beginning+middle+end for context
        log(f"   📖 Building full-script context summary ({len(segments)} segs)…")
        script_sample = self._sample_script_for_summary(segments, max_chars=6000)
        summary = self._extract_summary_openai(
            client, script_sample, lang_name, model=model
        )
        log(f"   📖 Context brief: {summary[:120]}…")

        system_prompt = (
            f"You are a professional subtitle translator.\n"
            f"Translate subtitles to {lang_name}.\n\n"
            f"=== FULL SCRIPT CONTEXT BRIEF ===\n"
            f"{summary}\n"
            f"=== END CONTEXT BRIEF ===\n\n"
            f"TRANSLATION RULES:\n"
            f"- Use EXACTLY the character names and pronouns listed in the context brief above\n"
            f"- Keep tone and register consistent throughout\n"
            f"- Return ONLY the translated text — no explanations\n"
            f"- Keep [music], [laughter], [applause] etc. as-is\n"
            f"- Do NOT translate proper nouns, brand names, or character names\n"
            f"- Maintain the same sentence count as the input"
        )

        batch_size = 20
        batches = []
        for bi in range(0, total, batch_size):
            batch = segments[bi : bi + batch_size]
            ctx_before = segments[max(0, bi - ctx_window) : bi]
            ctx_after = segments[
                bi + len(batch) : min(total, bi + len(batch) + ctx_window)
            ]
            batches.append((bi, batch, ctx_before, ctx_after))

        log(f"   ⚡ Concurrent OpenAI [{model}]: {len(batches)} batches | 5 workers")

        results: dict[int, list[str]] = {}

        def translate_batch(args):
            bi, batch, ctx_before, ctx_after = args
            if cancel.is_set():
                return

            user_msg = self._build_batch_prompt(
                batch, ctx_before, ctx_after, lang_name, ""
            )

            for attempt in range(3):
                if cancel.is_set():
                    return
                try:
                    r = client.chat.completions.create(
                        model=model,
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
                        log(
                            f"   ⚠️  Batch {bi//batch_size+1} parse mismatch — fallback individual"
                        )
                        parts = self._openai_individual(
                            batch, system_prompt, client, log
                        )

                    results[bi] = parts
                    return

                except Exception as e:
                    err = str(e)
                    if "429" in err or "rate_limit" in err.lower():
                        wait = min(20 * (attempt + 1), 60)
                        log(
                            f"   ⏳ Batch {bi//batch_size+1} rate-limited — wait {wait}s"
                        )
                        time.sleep(wait)
                        continue
                    log(f"   ⚠️  Batch {bi//batch_size+1} error: {e}")
                    results[bi] = [s.text for s in batch]
                    return

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(translate_batch, args): args for args in batches}
            for fut in concurrent.futures.as_completed(futures):
                if cancel.is_set():
                    from core.pipeline.base import CancelledError

                    raise CancelledError()
                bi = futures[fut][0]
                segs_done = min(bi + batch_size, total)
                log(f"   [{segs_done}/{total}] translated")
                try:
                    fut.result()
                except Exception as e:
                    log(f"   ⚠️  Batch future error: {e}")

        # Reassemble in order
        out = []
        for bi, batch, _, _ in batches:
            parts = results.get(bi, [s.text for s in batch])
            for seg, trans in zip(batch, parts):
                out.append(
                    TranslatedSegment(
                        seg.start,
                        seg.end,
                        seg.text.strip(),
                        trans.strip() or seg.text.strip(),
                    )
                )
        return out

    # ── Gemini helpers ────────────────────────────────────────────────────────

    def _gemini_client(self, api_key):
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
                    m = re.search(r"retryDelay.*?(\d+)s", err)
                    wait = int(m.group(1)) + 5 if m else 65
                    if attempt < retries - 1:
                        _log(
                            f"   ⏳ Rate limited — waiting {wait}s… (attempt {attempt+1}/{retries})"
                        )
                        time.sleep(wait)
                        continue
                raise
        raise RuntimeError("Gemini: max retries exceeded")

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
        lines = re.findall(r"^\d+\.\s*(.+)$", text, re.MULTILINE)
        return lines if len(lines) == expected else []

    def _gemini_individual(self, client, types, batch, system_ctx, log):
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

    def _build_examples(self, src_lang, target):
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
            ("ja", "vi"): [("私の家", "Nhà của tôi"), ("ありがとう", "Cảm ơn bạn")],
            ("en", "vi"): [
                ("My name is Anna", "Tên tôi là Anna"),
                ("How are you?", "Bạn có khỏe không?"),
            ],
            ("ko", "vi"): [("안녕하세요", "Xin chào"), ("감사합니다", "Cảm ơn bạn")],
        }
        key = (src_lang.lower(), target.lower())
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
        self, client, types, script_text: str, lang_name: str, src_lang: str = "unknown"
    ) -> str:
        """Full-script context summary for Gemini — same approach as OpenAI version."""
        try:
            prompt = (
                f"You are a translation consultant analyzing a FULL subtitle script "
                f"in {src_lang.upper()} to be translated into {lang_name}.\n\n"
                f"Produce a structured translation brief:\n"
                f"1. CHARACTERS: Name + role + Vietnamese pronoun to use (anh/chị/em/ông/bà/tôi etc.)\n"
                f"2. SETTING: Genre, location, time period, formality\n"
                f"3. TONE: Emotional register, formal/informal, humor\n"
                f"4. KEY TERMS: Domain terms, catchphrases, names to keep consistent\n"
                f"5. REGISTER: How characters address each other in Vietnamese\n\n"
                f"SUBTITLE SCRIPT ({src_lang.upper()}):\n{script_text}"
            )
            return self._gemini_generate(client, types, prompt).strip()
        except Exception:
            return f"Subtitle content in {src_lang}. Translate all text to {lang_name}."

    # ── OpenAI helpers ────────────────────────────────────────────────────────

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

    def _extract_summary_openai(
        self, client, script_text: str, lang_name: str, model: str = "gpt-4o-mini"
    ) -> str:
        """
        Build a rich context summary from the FULL script.

        Reads the entire subtitle script (sampled if very long) and extracts:
        - Character names, relationships, pronouns
        - Setting / genre / tone
        - Domain-specific terms to keep consistent
        - Vietnamese-specific guidance (xưng hô, register)

        This summary is injected into the system_prompt of EVERY batch call,
        giving the model full-script awareness even when translating 20 segs at a time.
        """
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a translation consultant. Analyze this subtitle script "
                            "and produce a structured context brief for the translator. "
                            "Be specific and concrete — names, terms, relationships matter."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Analyze this FULL subtitle script and produce a translation brief "
                            f"for translating into {lang_name}.\n\n"
                            f"Return a structured brief with these sections:\n"
                            f"1. CHARACTERS: List each character name + their role/relationship + "
                            f"which Vietnamese pronoun to use for them (anh/chị/em/ông/bà/tôi etc.)\n"
                            f"2. SETTING: Genre, time period, location, formality level\n"
                            f"3. TONE: Formal/informal, emotional register, humor level\n"
                            f"4. KEY TERMS: Domain-specific words, names, catchphrases to keep consistent\n"
                            f"5. REGISTER: How characters address each other "
                            f"(e.g. bạn/tôi for casual, anh/em for family, etc.)\n\n"
                            f"SUBTITLE SCRIPT:\n{script_text}"
                        ),
                    },
                ],
                temperature=0.2,
                max_tokens=600,
            )
            return r.choices[0].message.content.strip()
        except Exception:
            return f"General subtitle content. Translate naturally to {lang_name}."

    def _sample_script_for_summary(self, segments, max_chars: int = 6000) -> str:
        """
        Build a representative script sample for the summary call.
        Strategy: take beginning (40%), middle (30%), end (30%) of script.
        This gives the model character/setting intro + development + conclusion.
        """
        total = len(segments)
        if total == 0:
            return ""

        all_text = " | ".join(
            f"[{s.start:.0f}s] {s.text.strip()}" for s in segments if s.text.strip()
        )

        # If short enough, use full script
        if len(all_text) <= max_chars:
            return all_text

        # Sample: beginning + middle + end
        n_begin = int(total * 0.40)
        n_mid_start = int(total * 0.45)
        n_mid_end = int(total * 0.60)
        n_end_start = int(total * 0.75)

        parts = [
            "=== BEGINNING ===\n"
            + " | ".join(
                f"[{s.start:.0f}s] {s.text.strip()}" for s in segments[:n_begin]
            ),
            "\n=== MIDDLE ===\n"
            + " | ".join(
                f"[{s.start:.0f}s] {s.text.strip()}"
                for s in segments[n_mid_start:n_mid_end]
            ),
            "\n=== END ===\n"
            + " | ".join(
                f"[{s.start:.0f}s] {s.text.strip()}" for s in segments[n_end_start:]
            ),
        ]
        sampled = "\n".join(parts)

        # Trim if still too long
        if len(sampled) > max_chars:
            sampled = sampled[:max_chars] + "\n...(truncated)"
        return sampled

    # ── Verify pass ───────────────────────────────────────────────────────────

    def _verify_pass(
        self, segments, target, backend, api_key, verify_backend, verify_model, log
    ):
        if verify_backend == "none":
            return segments

        lang_name = LANG_NAMES.get(target, target)
        batch_size = 15
        out = []
        log(f"🔍 Verify pass via {verify_backend} ({verify_model})…")
        log("   Sending bilingual pairs (original + translation) for context")

        for bi in range(0, len(segments), batch_size):
            batch = segments[bi : bi + batch_size]
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

    def _build_verify_prompt(self, bilingual, lang_name, backend):
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

    # ── Ollama ────────────────────────────────────────────────────────────────

    def _ollama_generate(self, prompt, model="llama3", log=None):
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

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Backend:"))
        self._backend_combo = QComboBox()
        self._backend_combo.addItems(
            [
                "Gemini Flash (free ⭐)",
                "Google Translate (free)",
                "OpenAI GPT-4o",
                "OpenAI GPT-4o-mini",
            ]
        )
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        r1.addWidget(self._backend_combo)
        r1.addStretch()
        v.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Target lang:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(LANGUAGES.keys())
        self._lang_combo.setCurrentText("Vietnamese")
        r2.addWidget(self._lang_combo)
        r2.addStretch()
        v.addLayout(r2)

        self._api_lbl = QLabel("API Key:")
        self._api_edit = QLineEdit()
        self._api_edit.setPlaceholderText("Gemini: aistudio.google.com")
        self._api_edit.setEchoMode(QLineEdit.EchoMode.Password)
        v.addWidget(self._api_lbl)
        v.addWidget(self._api_edit)

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

        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#2d2d4e;margin:4px 0;")
        v.addWidget(sep)

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
        ov.addWidget(self._ollama_model_combo)
        ov.addStretch()
        ov2.addLayout(ov)

        hint = QLabel("Install: ollama.com  |  Pull: ollama pull qwen2")
        hint.setStyleSheet("color:#555;font-size:10px;")
        ov2.addWidget(hint)
        self._ollama_opts.setVisible(False)
        v.addWidget(self._ollama_opts)

        self._backend_combo.setCurrentIndex(2)  # Default: GPT-4o
        self._verify_combo.setCurrentIndex(0)
        self._rule_fix_chk.setChecked(True)
        self._on_backend_changed(2)
        return w

    def _on_backend_changed(self, idx):
        backend = translate_backend_from_index(idx)
        is_google = backend == "google"
        needs_key = backend != "google"
        self._api_lbl.setVisible(needs_key)
        self._api_edit.setVisible(needs_key)
        self._google_opts.setVisible(is_google)
        self._ai_opts.setVisible(not is_google)
        if backend == "gemini":
            self._api_edit.setPlaceholderText(
                "Gemini API key — free at aistudio.google.com"
            )
        elif backend in ("openai", "openai_gpt4o"):
            self._api_edit.setPlaceholderText("OpenAI API key — platform.openai.com")

    def _on_verify_changed(self, idx):
        self._ollama_opts.setVisible(idx == 1)

    def apply_config(self, config: dict) -> None:
        if not config:
            return
        from core.pipeline.selection import VERIFY_BACKEND_BY_INDEX

        _LANG_BY_CODE = {v: k for k, v in LANGUAGES.items()}
        # backend index
        be = config.get("backend", "openai")
        om = config.get("openai_model", "gpt-4o")
        # re-map to combo choice (openai+gpt-4o→2, openai+gpt-4o-mini→3, gemini→0, google→1)
        _BE_TO_IDX = {"gemini": 0, "google": 1}
        if be == "openai" and om == "gpt-4o":
            be_idx = 2
        elif be == "openai":
            be_idx = 3
        else:
            be_idx = _BE_TO_IDX.get(be, 2)
        if self._backend_combo:
            self._backend_combo.setCurrentIndex(be_idx)
        if self._lang_combo:
            label = _LANG_BY_CODE.get(config.get("target_lang", "vi"), "Vietnamese")
            self._lang_combo.setCurrentText(label)
        if self._chunk_spin and config.get("chunk_size") is not None:
            self._chunk_spin.setValue(int(config["chunk_size"]))
        if self._ctx_spin and config.get("ctx_window") is not None:
            self._ctx_spin.setValue(int(config["ctx_window"]))
        vb = config.get("verify_backend", "none")
        _VB_TO_IDX = {v: k for k, v in VERIFY_BACKEND_BY_INDEX.items()}
        if self._verify_combo:
            self._verify_combo.setCurrentIndex(_VB_TO_IDX.get(vb, 0))
        if self._ollama_model_combo and config.get("verify_model"):
            self._ollama_model_combo.setCurrentText(config["verify_model"])
        if self._rule_fix_chk and config.get("rule_fix") is not None:
            self._rule_fix_chk.setChecked(bool(config["rule_fix"]))

    def collect_config(self):
        idx = self._backend_combo.currentIndex() if self._backend_combo else 0
        backend = translate_backend_from_index(idx)
        # openai_gpt4o uses the same openai backend but with gpt-4o model
        openai_model = "gpt-4o" if backend == "openai_gpt4o" else "gpt-4o-mini"
        if backend == "openai_gpt4o":
            backend = "openai"

        v_idx = self._verify_combo.currentIndex() if self._verify_combo else 0
        verify_backend = verify_backend_from_index(v_idx)

        ollama_model = "qwen2"
        if self._ollama_model_combo:
            ollama_model = ollama_model_from_combo_text(
                self._ollama_model_combo.currentText()
            )

        return {
            "backend": backend,
            "target_lang": LANGUAGES.get(
                self._lang_combo.currentText() if self._lang_combo else "Vietnamese",
                "vi",
            ),
            "api_key": self._api_edit.text().strip() or None,
            "openai_model": openai_model,
            "chunk_size": self._chunk_spin.value() if self._chunk_spin else 15,
            "ctx_window": self._ctx_spin.value() if self._ctx_spin else 5,
            "verify": verify_backend != "none",
            "verify_backend": verify_backend,
            "verify_model": ollama_model,
            "rule_fix": self._rule_fix_chk.isChecked() if self._rule_fix_chk else True,
        }
