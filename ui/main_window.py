"""
MainWindow — orchestrates the pipeline.

All business logic lives in core/pipeline/step*.py
MainWindow only:
  1. Shows StepCards in a scrollable row
  2. Manages session
  3. Routes worker signals to UI
"""

import os
from pathlib import Path

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.pipeline.step1_transcribe import TranscribeStep
from core.pipeline.step2_translate import TranslateStep
from core.pipeline.step3_burn import BurnStep
from core.pipeline.step4_separate import SeparateStep
from core.pipeline.step5_tts import TTSStep
from core.session import Session
from ui.widgets.drop_zone import SUPPORTED, DropZone
from ui.widgets.step_card import StepCard

STYLESHEET = """
QMainWindow,QWidget{
    background:#1a1a2e;color:#e0e0e0;
    font-family:'SF Pro Display','Segoe UI',Arial,sans-serif;font-size:13px;
}
QPushButton{
    background:#2d2d4e;color:#e0e0e0;border:1px solid #3d3d6e;
    border-radius:6px;padding:5px 12px;
}
QPushButton:hover{background:#3d3d6e;border-color:#6c63ff;}
QPushButton:disabled{color:#444;background:#1e1e38;border-color:#252540;}
QPushButton#cancel_btn{
    background:#3a1a1a;color:#ff7070;border:1px solid #6e2d2d;
    font-weight:bold;padding:7px 14px;
}
QPushButton#cancel_btn:hover{background:#5a2020;}
QLineEdit{
    background:#16213e;border:1px solid #2d2d4e;
    border-radius:5px;padding:5px 10px;color:#e0e0e0;
}
QLineEdit:focus{border-color:#6c63ff;}
QLineEdit:read-only{color:#aaa;background:#111828;}
QComboBox{
    background:#16213e;border:1px solid #2d2d4e;
    border-radius:5px;padding:4px 10px;color:#e0e0e0;
}
QComboBox:hover{border-color:#6c63ff;}
QComboBox QAbstractItemView{
    background:#16213e;border:1px solid #6c63ff;
    color:#e0e0e0;selection-background-color:#6c63ff;
}
QComboBox::drop-down{border:none;}
QTextEdit{
    background:#0f0f23;border:1px solid #2d2d4e;border-radius:6px;
    padding:8px;color:#d0d0d0;
    font-family:'SF Mono','Consolas',monospace;font-size:12px;
}
QProgressBar{
    border:1px solid #2d2d4e;border-radius:4px;background:#16213e;
    text-align:center;color:white;height:16px;
}
QProgressBar::chunk{background:#6c63ff;border-radius:3px;}
QStatusBar{background:#0f0f23;border-top:1px solid #2d2d4e;color:#666;}
QSplitter::handle{background:#2d2d4e;}
QScrollArea{border:none;background:transparent;}
QCheckBox{spacing:6px;}
QCheckBox::indicator{
    width:14px;height:14px;border:1px solid #3d3d6e;
    border-radius:3px;background:#16213e;
}
QCheckBox::indicator:checked{background:#6c63ff;border-color:#6c63ff;}
QRadioButton{spacing:6px;}
QRadioButton::indicator{
    width:14px;height:14px;border-radius:7px;
    border:1px solid #3d3d6e;background:#16213e;
}
QRadioButton::indicator:checked{background:#6c63ff;border-color:#6c63ff;}
QSpinBox{
    background:#16213e;border:1px solid #2d2d4e;
    border-radius:5px;padding:4px 6px;color:#e0e0e0;min-width:70px;
}
QSpinBox:focus{border-color:#6c63ff;}
QSpinBox::up-button,QSpinBox::down-button{
    width:18px;background:#2d2d4e;border:none;border-radius:3px;}
QSpinBox::up-button:hover,QSpinBox::down-button:hover{background:#3d3d6e;}
QFrame#session_bar{background:#111828;border:1px solid #2d2d4e;border-radius:6px;}
QSlider::groove:horizontal{
    height:4px;background:#2d2d4e;border-radius:2px;}
QSlider::handle:horizontal{
    width:14px;height:14px;margin:-5px 0;
    background:#6c63ff;border-radius:7px;}
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SubSync  —  AI Video Pipeline")
        self.setMinimumSize(1100, 820)
        self.setStyleSheet(STYLESHEET)

        self._file = None
        self._session = None
        self._worker = None
        self._pool = QThreadPool.globalInstance()
        self._queue: list = []  # list of steps to run sequentially
        self._stop_queue = False

        # Instantiate all steps — easy to add more here
        self._steps = [
            TranscribeStep(),
            TranslateStep(),
            BurnStep(),
            SeparateStep(),
            TTSStep(),
        ]
        self._cards: list[StepCard] = []

        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        cw = QWidget()
        self.setCentralWidget(cw)
        root = QVBoxLayout(cw)
        root.setContentsMargins(18, 14, 18, 10)
        root.setSpacing(10)

        # Title
        title_row = QHBoxLayout()
        t = QLabel("SubSync")
        t.setStyleSheet(
            "font-size:26px;font-weight:700;color:#a0a8ff;letter-spacing:2px;"
        )
        sub = QLabel(
            "AI Video Pipeline  ·  Transcribe → Translate → Burn → Separate → Voice"
        )
        sub.setStyleSheet("font-size:11px;color:#444;margin-left:12px;margin-top:8px;")
        title_row.addWidget(t)
        title_row.addWidget(sub)
        title_row.addStretch()
        root.addLayout(title_row)

        # Session bar
        sf = QFrame()
        sf.setObjectName("session_bar")
        sh = QHBoxLayout(sf)
        sh.setContentsMargins(12, 8, 12, 8)
        sh.addWidget(self._lbl("Session folder:", bold=True))
        self._sess_dir_edit = QLineEdit()
        self._sess_dir_edit.setPlaceholderText(
            "Choose base folder — sessions created as  <name>_YYYYMMDD_HHMMSS/"
        )
        self._sess_dir_edit.setReadOnly(True)
        sh.addWidget(self._sess_dir_edit, stretch=1)
        btn_base = QPushButton("Browse…")
        btn_base.setFixedWidth(76)
        btn_base.clicked.connect(self._pick_base_dir)
        sh.addWidget(btn_base)
        sh.addSpacing(16)
        sh.addWidget(self._lbl("Session:", bold=True))
        self._sess_name_lbl = QLabel("—")
        self._sess_name_lbl.setStyleSheet(
            "color:#ffaa55;font-size:11px;font-family:'SF Mono','Consolas',monospace;"
        )
        sh.addWidget(self._sess_name_lbl)
        btn_open = QPushButton("Open folder")
        btn_open.setFixedWidth(90)
        btn_open.clicked.connect(self._open_session_folder)
        sh.addWidget(btn_open)
        root.addWidget(sf)

        # File input
        fi = QHBoxLayout()
        self._drop = DropZone(self._set_file)
        fi.addWidget(self._drop, stretch=1)
        self._file_edit = QLineEdit()
        self._file_edit.setPlaceholderText("File path…")
        self._file_edit.setReadOnly(True)
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse_file)
        fi.addWidget(self._file_edit, stretch=2)
        fi.addWidget(btn_browse)
        root.addLayout(fi)

        # ── Step cards in horizontal scrollable area ──
        cards_container = QWidget()
        cards_h = QHBoxLayout(cards_container)
        cards_h.setSpacing(12)
        cards_h.setContentsMargins(4, 4, 4, 4)

        for step in self._steps:
            card = StepCard(step)
            card.on_run = lambda s=step, c=None: self._run_step(s)
            # store card ref by step id
            self._cards.append(card)
            cards_h.addWidget(card)

        cards_h.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(cards_container)
        scroll.setMinimumHeight(340)
        scroll.setMaximumHeight(420)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollBar:horizontal{height:8px;background:#111828;border-radius:4px;}"
            "QScrollBar::handle:horizontal{background:#3d3d6e;border-radius:4px;}"
            "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}"
        )
        root.addWidget(scroll)

        # ── Run All / Stop Queue / Cancel / progress ──
        ctrl = QFrame()
        ctrl.setStyleSheet(
            "QFrame{background:#111828;border:1px solid #2d2d4e;border-radius:8px;}"
        )
        ctrl_h = QHBoxLayout(ctrl)
        ctrl_h.setContentsMargins(12, 8, 12, 8)
        ctrl_h.setSpacing(8)

        self._btn_run_all = QPushButton("▶▶  Run All Enabled Steps")
        self._btn_run_all.setObjectName("run_all_btn")
        self._btn_run_all.setStyleSheet(
            "QPushButton#run_all_btn{"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #6c63ff,stop:1 #a855f7);"
            "color:white;font-weight:bold;font-size:14px;"
            "border:none;border-radius:7px;padding:9px 24px;}"
            "QPushButton#run_all_btn:hover{"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #5a52d5,stop:1 #9333ea);}"
            "QPushButton#run_all_btn:disabled{"
            "background:#2a2a4a;color:#555;}"
        )
        self._btn_run_all.clicked.connect(self._run_all)
        ctrl_h.addWidget(self._btn_run_all)

        self._btn_stop = QPushButton("⏹  Stop Queue")
        self._btn_stop.setStyleSheet(
            "QPushButton{background:#2a1a0a;color:#ffaa55;"
            "border:1px solid #6e4a1a;font-weight:bold;"
            "border-radius:6px;padding:8px 16px;}"
            "QPushButton:hover{background:#4a2a0a;border-color:#ffaa55;}"
            "QPushButton:disabled{color:#554422;border-color:#3a2a1a;}"
        )
        self._btn_stop.setVisible(False)
        self._btn_stop.clicked.connect(self._request_stop_queue)
        ctrl_h.addWidget(self._btn_stop)

        self._btn_cancel = QPushButton("✕  Cancel Job")
        self._btn_cancel.setObjectName("cancel_btn")
        self._btn_cancel.setVisible(False)
        self._btn_cancel.clicked.connect(self._cancel)
        ctrl_h.addWidget(self._btn_cancel)

        ctrl_h.addSpacing(8)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setFixedWidth(180)
        ctrl_h.addWidget(self._progress)

        self._prog_lbl = QLabel("")
        self._prog_lbl.setStyleSheet("color:#888;font-size:11px;")
        self._queue_lbl = QLabel("")
        self._queue_lbl.setStyleSheet("color:#ffaa55;font-size:12px;font-weight:600;")
        ctrl_h.addWidget(self._queue_lbl)
        ctrl_h.addWidget(self._prog_lbl)
        ctrl_h.addStretch()
        root.addWidget(ctrl)

        # Log + preview
        vsplit = QSplitter(Qt.Orientation.Vertical)
        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setMaximumHeight(130)
        self._log_edit.setPlaceholderText("Pipeline log…")
        vsplit.addWidget(self._wrap("Log", self._log_edit))

        hsplit = QSplitter(Qt.Orientation.Horizontal)
        self._orig_edit = QTextEdit()
        self._orig_edit.setReadOnly(True)
        self._orig_edit.setPlaceholderText("Original transcript…")
        hsplit.addWidget(self._wrap("Original", self._orig_edit))
        self._trans_edit = QTextEdit()
        self._trans_edit.setReadOnly(True)
        self._trans_edit.setPlaceholderText("Translated subtitles…")
        hsplit.addWidget(self._wrap("Translated", self._trans_edit))
        vsplit.addWidget(hsplit)

        root.addWidget(vsplit, stretch=1)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready — choose session folder then drop a video")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _lbl(self, text, color="#a0a8ff", size=11, bold=False):
        l = QLabel(text)
        l.setStyleSheet(
            f"color:{color};font-size:{size}px;"
            f"font-weight:{'600' if bold else '400'};"
        )
        return l

    def _wrap(self, label, widget):
        c = QWidget()
        v = QVBoxLayout(c)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        v.addWidget(self._lbl(label, bold=True))
        v.addWidget(widget)
        return c

    def _card_for(self, step) -> StepCard:
        return self._cards[self._steps.index(step)]

    def _log(self, msg):
        self._log_edit.append(msg)
        self._prog_lbl.setText(msg.strip()[:90])
        self._status_bar.showMessage(msg.strip()[:120])

    def _set_busy(self, busy: bool):
        self._btn_cancel.setVisible(busy)
        self._progress.setVisible(busy)
        if not busy:
            self._prog_lbl.setText("")
            self._btn_cancel.setEnabled(True)
            self._btn_cancel.setText("✕  Cancel Job")

    def _set_queue_running(self, running: bool):
        self._btn_run_all.setEnabled(not running)
        self._btn_stop.setVisible(running)
        self._btn_stop.setEnabled(running)
        self._btn_stop.setText("⏹  Stop Queue")

    # ── Run All ───────────────────────────────────────────────────────────────

    def _run_all(self):
        if not self._file:
            QMessageBox.warning(self, "No file", "Select a video/audio file first.")
            return
        if not self._ensure_session():
            return
        if self._worker is not None:
            QMessageBox.warning(self, "Busy", "A step is already running.")
            return

        # Build queue from enabled cards only
        self._queue = [s for s, c in zip(self._steps, self._cards) if c.is_enabled()]
        if not self._queue:
            QMessageBox.information(
                self, "Nothing to run", "Enable at least one step first."
            )
            return

        self._stop_queue = False
        self._log_edit.clear()
        self._log(
            f"🚀 Run All — {len(self._queue)} steps: "
            + "  →  ".join(s.LABEL for s in self._queue)
        )
        self._set_queue_running(True)
        self._run_next_in_queue()

    def _run_next_in_queue(self):
        if self._stop_queue:
            self._stop_queue = False
            self._set_queue_running(False)
            self._set_busy(False)
            remaining = len(self._queue)
            self._queue = []
            self._queue_lbl.setText("")
            self._log(f"⏹  Queue stopped — {remaining} step(s) skipped.")
            self._status_bar.showMessage("Queue stopped.")
            return

        if not self._queue:
            # All done
            self._set_queue_running(False)
            self._set_busy(False)
            self._queue_lbl.setText("")
            self._log("🎉 All steps complete!")
            self._status_bar.showMessage("✅ All steps complete!")
            return

        step = self._queue[0]
        total_remaining = len(self._queue)
        self._queue_lbl.setText(f"Queue: {step.LABEL}  ({total_remaining} left)")

        card = self._card_for(step)
        config = step.collect_config()
        card.set_status("▶ Running…", "running")
        card.set_running(True)
        self._set_busy(True)

        worker = step.make_worker(self._session, config)
        self._worker = worker
        worker.signals.progress.connect(self._log)
        worker.signals.finished.connect(lambda r, s=step: self._done_queue(s, r))
        worker.signals.error.connect(lambda e, s=step: self._error_queue(s, e))
        worker.signals.cancelled.connect(lambda s=step: self._cancelled_queue(s))
        self._pool.start(worker)

    def _done_queue(self, step, result):
        """Called when a queued step finishes — auto-advance."""
        self._worker = None
        self._set_busy(False)
        card = self._card_for(step)
        card.set_running(False)

        out_path = result if isinstance(result, str) else ""
        if not out_path and self._session:
            attr = {
                "step1_transcribe": "step1_json",
                "step2_translate": "step2_srt",
                "step3_burn": "step3_video",
                "step4_separate": "step4_vocals",
                "step5_tts": "step5_video",
            }.get(step.STEP_ID, "")
            out_path = str(getattr(self._session, attr, "")) if attr else ""
        card.set_status("✅ Done", "done", out_path)
        self._update_previews(step, result)

        # Remove completed step and run next
        if self._queue and self._queue[0] is step:
            self._queue.pop(0)
        self._run_next_in_queue()

    def _error_queue(self, step, msg):
        """Error in queue — stop queue, show error."""
        self._worker = None
        self._set_busy(False)
        self._set_queue_running(False)
        self._queue = []
        self._queue_lbl.setText("")
        card = self._card_for(step)
        card.set_running(False)
        card.set_status("❌ Error", "error")
        self._log(f"❌ ERROR [{step.LABEL}]: {msg}")
        QMessageBox.critical(self, f"Error — {step.LABEL}", f"{msg}\n\nQueue stopped.")

    def _cancelled_queue(self, step):
        """Cancel in queue — stop entire queue."""
        self._worker = None
        self._set_busy(False)
        self._set_queue_running(False)
        self._queue = []
        self._queue_lbl.setText("")
        card = self._card_for(step)
        card.set_running(False)
        card.set_status("🚫 Cancelled", "idle")
        self._log("🚫 Queue cancelled.")
        self._status_bar.showMessage("Queue cancelled.")

    def _request_stop_queue(self):
        """Stop after current job — don't kill current worker."""
        self._stop_queue = True
        self._btn_stop.setEnabled(False)
        self._btn_stop.setText("Stopping…")
        self._log("⏹  Stop requested — finishing current step then stopping.")

    # ── Run single step ───────────────────────────────────────────────────────

    def _pick_base_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose base folder for sessions")
        if d:
            self._sess_dir_edit.setText(d)
            self._status_bar.showMessage(f"Base folder: {d}")

    def _ensure_session(self) -> bool:
        if self._session and self._session.source_file == self._file:
            return True
        base = self._sess_dir_edit.text().strip()
        if not base:
            QMessageBox.warning(
                self, "No folder", "Choose a session base folder first."
            )
            return False
        if not self._file:
            QMessageBox.warning(self, "No file", "Select a video/audio file first.")
            return False
        self._session = Session(base, self._file)
        self._sess_name_lbl.setText(self._session.folder.name)
        self._log(f"📁 Session: {self._session.folder}")
        return True

    def _open_session_folder(self):
        if not self._session:
            QMessageBox.information(self, "No session", "Run a step first.")
            return
        import subprocess
        import sys

        p = str(self._session.folder)
        if sys.platform == "darwin":
            subprocess.run(["open", p])
        elif sys.platform == "win32":
            os.startfile(p)
        else:
            subprocess.run(["xdg-open", p])

    # ── File ──────────────────────────────────────────────────────────────────

    def _browse_file(self):
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED))
        p, _ = QFileDialog.getOpenFileName(
            self, "Select Video / Audio", "", f"Media ({exts})"
        )
        if p:
            self._set_file(p)

    def _set_file(self, path):
        self._file = path
        self._session = None  # new file = new session
        self._file_edit.setText(path)
        self._drop.set_file(Path(path).name)
        self._sess_name_lbl.setText("—  (created on first Run)")
        self._orig_edit.clear()
        self._trans_edit.clear()
        for card in self._cards:
            card.reset()
        self._status_bar.showMessage(f"File: {Path(path).name}")

    # ── Run a step ────────────────────────────────────────────────────────────

    def _run_step(self, step):
        if not self._ensure_session():
            return
        if self._worker is not None:
            QMessageBox.warning(self, "Busy", "Another step is already running.")
            return

        card = self._card_for(step)
        config = step.collect_config()

        card.set_status("▶ Running…", "running")
        card.set_running(True)
        self._set_busy(True)

        worker = step.make_worker(self._session, config)
        self._worker = worker
        worker.signals.progress.connect(self._log)
        worker.signals.finished.connect(lambda r, s=step: self._done(s, r))
        worker.signals.error.connect(lambda e, s=step: self._error(s, e))
        worker.signals.cancelled.connect(lambda s=step: self._cancelled(s))
        self._pool.start(worker)

    def _update_previews(self, step, result):
        if step.STEP_ID == "step1_transcribe" and hasattr(result, "segments"):
            self._orig_edit.setPlainText(
                "\n".join(f"[{s.start}s–{s.end}s]  {s.text}" for s in result.segments)
            )
        if step.STEP_ID == "step2_translate" and isinstance(result, list):
            lines = []
            for s in result:
                lines += [
                    f"[{s.start}s–{s.end}s]",
                    f"  {s.original}",
                    f"  → {s.translated}",
                    "",
                ]
            self._trans_edit.setPlainText("\n".join(lines))

    def _done(self, step, result):
        card = self._card_for(step)
        self._worker = None
        self._set_busy(False)
        card.set_running(False)
        out_path = result if isinstance(result, str) else ""
        if not out_path and self._session:
            attr = {
                "step1_transcribe": "step1_json",
                "step2_translate": "step2_srt",
                "step3_burn": "step3_video",
                "step4_separate": "step4_vocals",
                "step5_tts": "step5_video",
            }.get(step.STEP_ID, "")
            out_path = str(getattr(self._session, attr, "")) if attr else ""
        card.set_status("✅ Done", "done", out_path)
        self._update_previews(step, result)
        self._status_bar.showMessage(
            f"✅ {step.LABEL} complete"
            + (f" → {Path(out_path).name}" if out_path else "")
        )

    def _error(self, step, msg):
        card = self._card_for(step)
        self._worker = None
        self._set_busy(False)
        card.set_running(False)
        card.set_status("❌ Error", "error")
        self._log(f"❌ ERROR [{step.LABEL}]: {msg}")
        QMessageBox.critical(self, f"Error — {step.LABEL}", msg)

    def _cancelled(self, step):
        card = self._card_for(step)
        self._worker = None
        self._set_busy(False)
        card.set_running(False)
        card.set_status("🚫 Cancelled", "idle")

    def _cancel(self):
        if self._worker and hasattr(self._worker, "cancel"):
            self._worker.cancel()
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setText("Cancelling…")
