"""
Step 2 — Translate transcript segments with context awareness.

Strategies (free-first):
  Google  → Chunk mode: gộp nhiều segment thành 1 request với separator
             → giữ ngữ cảnh trong chunk, ít request hơn, ít lỗi hơn
  OpenAI  → Context window mode: gửi N segment trước/sau + character summary
             → hiểu đại từ, nhân vật, quan hệ

Tại sao chunk mode tốt hơn dịch từng dòng:
  BAD : "她是我的" → "cô ấy là của tôi"  (không biết câu tiếp)
        "爸爸"     → "bố"
  GOOD: "她是我的\n爸爸" → "Cô ấy là bố của tôi"  (biết cả câu)
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

# Separator used to split chunks — must be unlikely in subtitle text
CHUNK_SEP = "\n<<<SEP>>>\n"
CHUNK_SEP_SIMPLE = "|||"  # simpler fallback for Google


@dataclass
class TranslatedSegment:
    start: float
    end: float
    original: str
    translated: str


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
        self._chunk_spin = None  # Google chunk size
        self._ctx_spin = None  # OpenAI context window
        self._verify_chk = None  # OpenAI verify pass

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(self, session, config, log, cancel):
        transcript = session.load_transcript()
        backend = config["backend"]
        target = config["target_lang"]
        api_key = config.get("api_key")
        chunk_size = config.get("chunk_size", 15)
        ctx_window = config.get("ctx_window", 3)
        do_verify = config.get("verify", False)
        segments = transcript.segments
        total = len(segments)

        log(f"🌏 Translating {total} segments → {target} via {backend}")
        log(
            f"   Mode: {'chunk (context-aware)' if backend=='google' else 'context-window'}"
        )

        if backend == "google":
            out = self._translate_chunks(segments, target, chunk_size, log, cancel)
        else:
            out = self._translate_context_window(
                segments, target, api_key, ctx_window, log, cancel
            )

        # Optional: OpenAI verify/fix pass
        if do_verify and backend == "openai" and api_key:
            log("🔍 Running verification pass…")
            out = self._verify_pass(out, target, api_key, log)

        log(f"✅ Done — {total} segments translated")
        session.save_translated(out)
        return out

    # ── Google: Chunk mode ────────────────────────────────────────────────────

    def _translate_chunks(self, segments, target, chunk_size, log, cancel):
        """
        Group segments into chunks of chunk_size, translate each chunk as one
        request using a separator, then split back by separator.
        Preserves context within each chunk.
        """
        try:
            from deep_translator import GoogleTranslator
        except ImportError:
            raise RuntimeError("Run: pip install deep-translator")

        out = []
        total = len(segments)
        chunks = [segments[i : i + chunk_size] for i in range(0, total, chunk_size)]

        log(f"   {total} segments → {len(chunks)} chunks of ~{chunk_size}")

        for ci, chunk in enumerate(chunks, 1):
            if cancel.is_set():
                from core.pipeline.base import CancelledError

                raise CancelledError()

            # Filter empty segments
            texts = [s.text.strip() for s in chunk]

            # Join with separator — Google usually preserves it
            joined = CHUNK_SEP_SIMPLE.join(t if t else " " for t in texts)

            try:
                translated_joined = GoogleTranslator(
                    source="auto", target=target
                ).translate(joined)
                translated_parts = translated_joined.split(CHUNK_SEP_SIMPLE)

                # Pad if split count doesn't match (Google sometimes merges)
                if len(translated_parts) != len(chunk):
                    log(
                        f"   ⚠️  Chunk {ci}: split mismatch "
                        f"({len(translated_parts)} vs {len(chunk)}), "
                        f"falling back to individual…"
                    )
                    translated_parts = self._fallback_individual(texts, target, log)

            except Exception as e:
                log(f"   ⚠️  Chunk {ci} failed: {e} — fallback individual")
                translated_parts = self._fallback_individual(texts, target, log)

            for seg, trans in zip(chunk, translated_parts):
                out.append(
                    TranslatedSegment(
                        seg.start,
                        seg.end,
                        seg.text.strip(),
                        trans.strip() or seg.text.strip(),
                    )
                )

            done = min(ci * chunk_size, total)
            log(f"   [{done}/{total}] chunks translated")
            time.sleep(0.2)  # rate limit

        return out

    def _fallback_individual(self, texts, target, log):
        """Translate one by one as fallback when chunk split fails."""
        from deep_translator import GoogleTranslator

        results = []
        for txt in texts:
            if not txt.strip():
                results.append(txt)
                continue
            try:
                results.append(
                    GoogleTranslator(source="auto", target=target).translate(txt)
                )
                time.sleep(0.1)
            except Exception as e:
                log(f"   ⚠️  Fallback seg failed: {e}")
                results.append(txt)
        return results

    # ── OpenAI: Context window mode ───────────────────────────────────────────

    def _translate_context_window(
        self, segments, target, api_key, ctx_window, log, cancel
    ):
        """
        For each segment, send ctx_window segments before + after as context.
        System prompt includes a character/topic summary extracted from full text.
        """
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

        # Build a quick character/context summary from first 20 segments
        preview_text = " ".join(s.text for s in segments[:20])
        context_summary = self._extract_context_summary(
            client, preview_text, lang_name, log
        )
        log(f"   📖 Context: {context_summary[:120]}…")

        system_prompt = (
            f"You are a professional subtitle translator. "
            f"Translate subtitles to {lang_name}.\n\n"
            f"CONTEXT about this content:\n{context_summary}\n\n"
            f"RULES:\n"
            f"- Keep the same tone and register as the original\n"
            f"- Use consistent names and pronouns throughout\n"
            f"- Return ONLY the translated subtitle line\n"
            f"- No explanation, no quotes, no extra text\n"
            f"- If a line is music/sound effect like [music], keep as-is"
        )

        out = []
        for i, seg in enumerate(segments):
            if cancel.is_set():
                from core.pipeline.base import CancelledError

                raise CancelledError()

            txt = seg.text.strip()
            if not txt:
                out.append(TranslatedSegment(seg.start, seg.end, txt, txt))
                continue

            # Build context window
            before = segments[max(0, i - ctx_window) : i]
            after = segments[i + 1 : min(total, i + ctx_window + 1)]

            ctx_lines = []
            if before:
                ctx_lines.append("Previous lines:")
                ctx_lines.extend(f"  {s.text.strip()}" for s in before)
            ctx_lines.append(f"\nTranslate this line:\n  {txt}")
            if after:
                ctx_lines.append("\nNext lines (for context only, do NOT translate):")
                ctx_lines.extend(f"  {s.text.strip()}" for s in after)

            user_msg = "\n".join(ctx_lines)

            try:
                r = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.2,
                    max_tokens=200,
                )
                translated = r.choices[0].message.content.strip()
            except Exception as e:
                log(f"   ⚠️  Seg {i+1} failed: {e}")
                translated = txt

            out.append(TranslatedSegment(seg.start, seg.end, txt, translated))

            if (i + 1) % 10 == 0 or (i + 1) == total:
                log(f"   [{i+1}/{total}] translated")

        return out

    def _extract_context_summary(self, client, preview_text, lang_name, log):
        """Ask GPT to summarize characters and context from first N segments."""
        try:
            r = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"From this subtitle excerpt, briefly identify:\n"
                            f"1. Main characters and their relationships\n"
                            f"2. Setting/topic (e.g. family drama, news, lecture)\n"
                            f"3. Pronouns to use in {lang_name} translation\n\n"
                            f"Keep it under 3 sentences. Subtitle text:\n{preview_text}"
                        ),
                    }
                ],
                temperature=0.3,
                max_tokens=150,
            )
            return r.choices[0].message.content.strip()
        except Exception:
            return f"General subtitle content. Translate naturally to {lang_name}."

    # ── OpenAI: Verify pass ───────────────────────────────────────────────────

    def _verify_pass(self, segments, target, api_key, log):
        """
        Send full translated subtitle to GPT and ask it to fix inconsistencies:
        - Wrong pronouns
        - Inconsistent character names
        - Broken sentences across segments
        Returns corrected segments.
        """
        try:
            from openai import OpenAI
        except ImportError:
            return segments

        client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY", ""))
        lang_name = LANG_NAMES.get(target, target)

        # Send in batches of 50 to stay within token limits
        batch_size = 50
        out = []

        for bi in range(0, len(segments), batch_size):
            batch = segments[bi : bi + batch_size]
            numbered = "\n".join(f"{j+1}. {s.translated}" for j, s in enumerate(batch))

            try:
                r = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                f"You are a {lang_name} subtitle editor. "
                                f"Fix pronoun consistency, character names, and "
                                f"broken sentences. Keep numbering. "
                                f"Return ONLY numbered lines, no explanation."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Fix these subtitles:\n{numbered}",
                        },
                    ],
                    temperature=0.1,
                    max_tokens=2000,
                )
                fixed_text = r.choices[0].message.content.strip()
                fixed_lines = re.findall(r"^\d+\.\s*(.+)$", fixed_text, re.MULTILINE)

                if len(fixed_lines) == len(batch):
                    for seg, fixed in zip(batch, fixed_lines):
                        out.append(
                            TranslatedSegment(
                                seg.start, seg.end, seg.original, fixed.strip()
                            )
                        )
                else:
                    log("   ⚠️  Verify batch mismatch, keeping original")
                    out.extend(batch)

            except Exception as e:
                log(f"   ⚠️  Verify batch failed: {e}")
                out.extend(batch)

            log(f"   Verified [{min(bi+batch_size, len(segments))}/{len(segments)}]")

        return out

    # ── Config widget ─────────────────────────────────────────────────────────

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # Backend
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Backend:"))
        self._backend_combo = QComboBox()
        self._backend_combo.addItems(["Google Translate (free)", "OpenAI GPT-4o-mini"])
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

        # Google chunk size
        self._google_opts = QWidget()
        go = QHBoxLayout(self._google_opts)
        go.setContentsMargins(0, 0, 0, 0)
        go.addWidget(QLabel("Chunk size:"))
        self._chunk_spin = QSpinBox()
        self._chunk_spin.setRange(5, 50)
        self._chunk_spin.setValue(15)
        self._chunk_spin.setFixedWidth(60)
        self._chunk_spin.setToolTip(
            "Segments per request — larger = better context, slower"
        )
        go.addWidget(self._chunk_spin)
        go.addWidget(QLabel("segs/request"))
        go.addStretch()
        v.addWidget(self._google_opts)

        # OpenAI options
        self._openai_opts = QWidget()
        oo = QVBoxLayout(self._openai_opts)
        oo.setContentsMargins(0, 0, 0, 0)
        oo.setSpacing(4)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Context window:"))
        self._ctx_spin = QSpinBox()
        self._ctx_spin.setRange(1, 10)
        self._ctx_spin.setValue(3)
        self._ctx_spin.setFixedWidth(55)
        self._ctx_spin.setToolTip(
            "Segments before/after sent as context — more = better accuracy, slower"
        )
        r3.addWidget(self._ctx_spin)
        r3.addWidget(QLabel("segs"))
        r3.addStretch()
        oo.addLayout(r3)

        self._verify_chk = QCheckBox("Verify & fix pass (2nd GPT review)")
        self._verify_chk.setToolTip(
            "After translating, run a second pass to fix pronoun/name inconsistencies"
        )
        oo.addWidget(self._verify_chk)
        v.addWidget(self._openai_opts)
        self._openai_opts.setVisible(False)

        # API key
        self._api_lbl = QLabel("OpenAI API Key:")
        self._api_edit = QLineEdit()
        self._api_edit.setPlaceholderText("sk-…  (or OPENAI_API_KEY env var)")
        self._api_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_lbl.setVisible(False)
        self._api_edit.setVisible(False)
        v.addWidget(self._api_lbl)
        v.addWidget(self._api_edit)

        return w

    def _on_backend_changed(self, idx):
        is_openai = idx == 1
        self._google_opts.setVisible(not is_openai)
        self._openai_opts.setVisible(is_openai)
        self._api_lbl.setVisible(is_openai)
        self._api_edit.setVisible(is_openai)

    def collect_config(self):
        backend = "google" if self._backend_combo.currentIndex() == 0 else "openai"
        return {
            "backend": backend,
            "target_lang": LANGUAGES.get(self._lang_combo.currentText(), "vi"),
            "api_key": self._api_edit.text().strip() or None,
            "chunk_size": self._chunk_spin.value() if self._chunk_spin else 15,
            "ctx_window": self._ctx_spin.value() if self._ctx_spin else 3,
            "verify": self._verify_chk.isChecked() if self._verify_chk else False,
        }
