"""
SubtitleEditor — widget hiển thị Original (read-only) + Translated (editable).

Khi user edit text trong Translated panel và nhấn Save (hoặc Ctrl+S),
widget tự parse lại các segment, ghi đè step2_translated.json và
step2_translated.srt trong session folder.

Usage:
    editor = SubtitleEditor()
    editor.load_session(session)   # load từ Session object
    editor.clear()                 # reset

Tích hợp:
    - MainWindow: thay thế self._trans_edit bằng SubtitleEditor
    - MultiSessionWindow: dùng trong preview panel
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def _srt_time(s: float) -> str:
    h, r = divmod(int(s), 3600)
    m, sec = divmod(r, 60)
    ms = int((s - int(s)) * 1000)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"


def _write_srt(segments: list[dict], path: str):
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(
            f"{i}\n"
            f"{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}\n"
            f"{seg['translated'].strip()}\n"
        )
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def _parse_translated_block(text: str) -> list[dict]:
    """
    Parse nội dung Translated panel về list of dicts.

    Format hiện tại trong panel:
        [0.0s–1.28s]
          original text
          → translated text

    Trả về list[{start, end, original, translated}]
    Nếu parse fail trả về [] để caller xử lý.
    """
    segments = []
    # Match blocks: [start–end]\n  original\n  → translated
    pattern = re.compile(
        r"\[(\d+(?:\.\d+)?)s[–-](\d+(?:\.\d+)?)s\]\s*\n"  # [start–end]
        r"[ \t]+(.*?)\s*\n"  # original (indented)
        r"[ \t]+→\s*(.*?)(?=\n\s*\[|\Z)",  # → translated
        re.DOTALL,
    )
    for m in pattern.finditer(text):
        start = float(m.group(1))
        end = float(m.group(2))
        original = m.group(3).strip()
        translated = m.group(4).strip()
        segments.append(
            {
                "start": start,
                "end": end,
                "original": original,
                "translated": translated,
            }
        )
    return segments


class SubtitleEditor(QWidget):
    """
    Side-by-side Original (read-only) + Translated (editable) panels.
    Emits saved(session_folder) after a successful save.
    """

    saved = pyqtSignal(str)  # emits session folder path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session = None
        self._dirty = False
        self._setup_ui()

    # ── Public API ────────────────────────────────────────────────────────────

    def load_session(self, session) -> bool:
        """
        Load translated segments from a Session object.
        Returns True if step2 data was found, False otherwise.
        """
        self._session = session
        self._dirty = False
        self._orig_edit.clear()
        self._trans_edit.clear()
        self._update_title()

        if not session.step1_done:
            self._orig_edit.setPlaceholderText("(Step 1 not done yet)")
        else:
            try:
                tr = session.load_transcript()
                self._orig_edit.setPlainText(
                    "\n".join(f"[{s.start}s–{s.end}s]  {s.text}" for s in tr.segments)
                )
            except Exception as e:
                self._orig_edit.setPlainText(f"(Cannot load transcript: {e})")

        if not session.step2_done:
            self._trans_edit.setPlaceholderText(
                "(Step 2 not done yet — run Translate first)"
            )
            self._btn_save.setEnabled(False)
            return False

        try:
            segs = session.load_translated()
            lines = []
            for s in segs:
                lines += [
                    f"[{s.start}s–{s.end}s]",
                    f"  {s.original}",
                    f"  → {s.translated}",
                    "",
                ]
            self._trans_edit.setPlainText("\n".join(lines))
            self._trans_edit.setPlaceholderText("")
            self._btn_save.setEnabled(True)
            self._dirty = False
            self._update_title()
            return True
        except Exception as e:
            self._trans_edit.setPlainText(f"(Cannot load translation: {e})")
            self._btn_save.setEnabled(False)
            return False

    def load_from_text(self, orig_text: str, trans_text: str):
        """Load raw text (used by MainWindow after step runs)."""
        self._session = None
        self._dirty = False
        self._orig_edit.setPlainText(orig_text)
        self._trans_edit.setPlainText(trans_text)
        self._btn_save.setEnabled(False)
        self._update_title()

    def set_session_for_save(self, session):
        """Call this after load_from_text to enable saving."""
        self._session = session
        self._btn_save.setEnabled(session is not None and session.step2_done)
        self._update_title()

    def clear(self):
        self._session = None
        self._dirty = False
        self._orig_edit.clear()
        self._trans_edit.clear()
        self._btn_save.setEnabled(False)
        self._update_title()

    def get_translated_text(self) -> str:
        return self._trans_edit.toPlainText()

    def set_orig_placeholder(self, text: str):
        self._orig_edit.setPlaceholderText(text)

    def set_trans_placeholder(self, text: str):
        self._trans_edit.setPlaceholderText(text)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Header bar
        hdr = QHBoxLayout()
        self._title_lbl = QLabel("Subtitles")
        self._title_lbl.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;")
        hdr.addWidget(self._title_lbl)
        hdr.addStretch()

        self._dirty_lbl = QLabel("")
        self._dirty_lbl.setStyleSheet("color:#ffaa55;font-size:10px;")
        hdr.addWidget(self._dirty_lbl)

        self._btn_save = QPushButton("💾  Save")
        self._btn_save.setFixedHeight(26)
        self._btn_save.setStyleSheet(
            "QPushButton{background:#1a3a2a;color:#5dca8e;border:1px solid #2a6a4a;"
            "font-weight:bold;border-radius:5px;padding:2px 12px;font-size:12px;}"
            "QPushButton:hover{background:#2a5a3a;border-color:#5dca8e;}"
            "QPushButton:disabled{color:#444;background:#1a1a2e;border-color:#252540;}"
        )
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._save)
        self._btn_save.setToolTip(
            "Save edits to step2_translated.json + .srt  (Ctrl+S)"
        )
        hdr.addWidget(self._btn_save)

        root.addLayout(hdr)

        # Side-by-side panels
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Original (read-only)
        orig_w = QWidget()
        orig_v = QVBoxLayout(orig_w)
        orig_v.setContentsMargins(0, 0, 0, 0)
        orig_v.setSpacing(2)
        orig_lbl = QLabel("Original")
        orig_lbl.setStyleSheet("color:#666;font-size:10px;font-weight:600;")
        orig_v.addWidget(orig_lbl)
        self._orig_edit = QTextEdit()
        self._orig_edit.setReadOnly(True)
        self._orig_edit.setPlaceholderText("Original transcript…")
        self._orig_edit.setStyleSheet(
            "background:#0a0a1a;border:1px solid #1e1e38;border-radius:5px;"
            "padding:6px;color:#b0b0b0;font-family:'SF Mono','Consolas',monospace;font-size:11px;"
        )
        orig_v.addWidget(self._orig_edit, stretch=1)
        splitter.addWidget(orig_w)

        # Translated (editable)
        trans_w = QWidget()
        trans_v = QVBoxLayout(trans_w)
        trans_v.setContentsMargins(0, 0, 0, 0)
        trans_v.setSpacing(2)
        trans_hdr = QHBoxLayout()
        trans_lbl = QLabel("Translated  (editable)")
        trans_lbl.setStyleSheet("color:#5dca8e;font-size:10px;font-weight:600;")
        trans_hdr.addWidget(trans_lbl)
        hint = QLabel("Edit → lines  ·  Ctrl+S to save")
        hint.setStyleSheet("color:#444;font-size:10px;")
        trans_hdr.addStretch()
        trans_hdr.addWidget(hint)
        trans_v.addLayout(trans_hdr)

        self._trans_edit = QTextEdit()
        self._trans_edit.setPlaceholderText("Translated subtitles…")
        self._trans_edit.setStyleSheet(
            "background:#0c0c1f;border:1px solid #2a3a5a;border-radius:5px;"
            "padding:6px;color:#e0e0e0;font-family:'SF Mono','Consolas',monospace;font-size:11px;"
        )
        self._trans_edit.textChanged.connect(self._on_text_changed)
        trans_v.addWidget(self._trans_edit, stretch=1)
        splitter.addWidget(trans_w)

        root.addWidget(splitter, stretch=1)

        # Ctrl+S shortcut
        shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        shortcut.activated.connect(self._save)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _on_text_changed(self):
        if not self._dirty:
            self._dirty = True
            self._update_title()

    def _update_title(self):
        if self._session:
            name = self._session.folder.name
            self._title_lbl.setText(f"Subtitles — {name}")
        else:
            self._title_lbl.setText("Subtitles")

        if self._dirty:
            self._dirty_lbl.setText("● unsaved")
            self._btn_save.setEnabled(self._session is not None)
        else:
            self._dirty_lbl.setText("")

    def _save(self):
        if self._session is None:
            QMessageBox.warning(self, "No session", "Load a session first.")
            return

        text = self._trans_edit.toPlainText()
        segments = _parse_translated_block(text)

        if not segments:
            QMessageBox.warning(
                self,
                "Parse error",
                "Could not parse subtitle blocks.\n\n"
                "Make sure each block has the format:\n"
                "[start–end]\n"
                "  original\n"
                "  → translated",
            )
            return

        # Write JSON
        json_path = self._session.step2_json
        srt_path = self._session.step2_srt

        try:
            json_path.write_text(
                json.dumps(segments, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _write_srt(segments, str(srt_path))
        except Exception as e:
            QMessageBox.critical(self, "Save failed", f"Could not write files:\n{e}")
            return

        self._dirty = False
        self._update_title()
        self._dirty_lbl.setText(f"✅ Saved {len(segments)} segments")
        self.saved.emit(str(self._session.folder))
