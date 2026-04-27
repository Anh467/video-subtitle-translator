"""Step 2 — Translate transcript segments → target language."""

import os
import time
from dataclasses import dataclass

from core.pipeline.base import BaseStep
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

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

    def run(self, session, config, log, cancel):
        # Load transcript from session if not in memory
        transcript = session.load_transcript()
        backend = config["backend"]
        target = config["target_lang"]
        api_key = config.get("api_key")

        log(
            f"🌏 Translating {len(transcript.segments)} segments → {target} via {backend}…"
        )
        out = []
        total = len(transcript.segments)

        for i, seg in enumerate(transcript.segments, 1):
            if cancel.is_set():
                from core.pipeline.base import CancelledError

                raise CancelledError()
            txt = seg.text.strip()
            if not txt:
                out.append(TranslatedSegment(seg.start, seg.end, txt, txt))
                continue
            try:
                translated = self._translate(txt, target, backend, api_key)
            except Exception as e:
                log(f"⚠️  Seg {i} failed: {e}")
                translated = txt
            out.append(TranslatedSegment(seg.start, seg.end, txt, translated))
            if i % 10 == 0 or i == total:
                log(f"   [{i}/{total}] translated")
            if backend == "google":
                time.sleep(0.1)

        log(f"✅ Done — {total} segments")
        session.save_translated(out)
        return out

    def _translate(self, text, target, backend, api_key):
        if backend == "google":
            try:
                from deep_translator import GoogleTranslator
            except ImportError:
                raise RuntimeError("Run: pip install deep-translator")
            return GoogleTranslator(source="auto", target=target).translate(text)

        elif backend == "openai":
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError("Run: pip install openai")
            key = api_key or os.environ.get("OPENAI_API_KEY", "")
            if not key:
                raise RuntimeError("Set OPENAI_API_KEY env var or enter key in UI.")
            name = LANG_NAMES.get(target, target)
            client = OpenAI(api_key=key)
            r = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"You are a subtitle translator. Translate to {name}. "
                            "Return ONLY the translated text, no explanation, no quotes."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0.3,
            )
            return r.choices[0].message.content.strip()

        raise RuntimeError(f"Unknown backend: {backend}")

    def build_config_widget(self, parent=None):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Backend:"))
        self._backend_combo = QComboBox()
        self._backend_combo.addItems(["Google Translate (free)", "OpenAI GPT-4o-mini"])
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
        self._api_lbl.setVisible(idx == 1)
        self._api_edit.setVisible(idx == 1)

    def collect_config(self):
        backend = "google" if self._backend_combo.currentIndex() == 0 else "openai"
        return {
            "backend": backend,
            "target_lang": LANGUAGES.get(self._lang_combo.currentText(), "vi"),
            "api_key": self._api_edit.text().strip() or None,
        }
