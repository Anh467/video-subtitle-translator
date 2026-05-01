"""Multi-session runner window (standalone QMainWindow)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QThreadPool, QTimer
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
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

from core.log_file import FileLogger
from core.pipeline.step1_transcribe import WHISPER_API_COST_PER_MINUTE
from core.pipeline.step2_translate import TRANSLATION_COST_PER_1M_CHARS
from core.pipeline.step5_budget import COST_PER_1M as TTS_COST_PER_1M
from core.session import Session
from ui.multi_session.cost_worker import SelectedCostWorker
from ui.multi_session.session_list_panel import SessionListPanel
from ui.widgets.session_info_editor import SessionInfoEditor
from ui.widgets.step_card import StepCard
from ui.widgets.subtitle_editor import SubtitleEditor

class MultiSessionWindow(QMainWindow):
    """
    Standalone window để xử lý nhiều session tuần tự.

    Nhận steps[] và base_dir từ MainWindow — dùng chung config,
    không tạo session mới, không đụng vào single-session state.
    """

    def __init__(self, steps: list, base_dir: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("SubSync — Multi-Session Runner")
        self.setMinimumSize(1200, 780)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        self._steps = steps
        self._base_dir = base_dir
        self._pool = QThreadPool.globalInstance()

        # Queue state
        self._job_queue: list[tuple[dict, object]] = []  # (sess_data, step)
        self._worker = None
        self._stop_requested = False
        self._current_session: Session | None = None
        self._multi_started_at = 0.0
        self._multi_total_jobs = 0
        self._multi_done_jobs = 0
        self._multi_total_sessions = 0
        self._multi_failed_sessions: set[str] = set()
        self._multi_session_stats: dict[str, dict] = {}

        # Cards — create fresh cards that wrap the SAME step instances
        # so config edits here and in main window stay in sync
        self._cards: list[StepCard] = []

        self._cost_request_id = 0
        self._cost_timer = QTimer(self)
        self._cost_timer.setSingleShot(True)
        self._cost_timer.setInterval(120)
        self._cost_timer.timeout.connect(self._run_selected_cost_worker)

        self._setup_ui()
        if base_dir:
            self._session_panel.set_base_dir(base_dir)
        # Autofill API keys into newly created step widgets
        self._autofill_keys()

        self._session_panel.selection_changed.connect(
            self._schedule_selected_cost_summary
        )
        self._session_panel.session_added.connect(self._schedule_selected_cost_summary)
        self._schedule_selected_cost_summary()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        cw = QWidget()
        self.setCentralWidget(cw)
        root = QVBoxLayout(cw)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(10)

        # Title
        title_row = QHBoxLayout()
        t = QLabel("Multi-Session Runner")
        t.setStyleSheet(
            "font-size:22px;font-weight:700;color:#c084fc;letter-spacing:1px;"
        )
        sub = QLabel("Process multiple sessions sequentially with the same step config")
        sub.setStyleSheet("font-size:11px;color:#555;margin-left:12px;margin-top:6px;")
        title_row.addWidget(t)
        title_row.addWidget(sub)
        title_row.addSpacing(10)

        # self._btn_editor_default = QPushButton("Default")
        # self._btn_editor_default.setCheckable(True)
        # self._btn_editor_default.setChecked(True)
        # self._btn_editor_default.setFixedHeight(26)
        # self._btn_editor_default.clicked.connect(
        #     lambda: self._set_editor_mode("default")
        # )
        # title_row.addWidget(self._btn_editor_default)

        # self._btn_editor_studio = QPushButton("Studio")
        # self._btn_editor_studio.setCheckable(True)
        # self._btn_editor_studio.setFixedHeight(26)
        # self._btn_editor_studio.clicked.connect(lambda: self._set_editor_mode("studio"))
        # title_row.addWidget(self._btn_editor_studio)
        # title_row.addStretch()
        # root.addLayout(title_row)

        # ── Top area: session list (left) + preview (right) ───────────────
        self._top_split = QSplitter(Qt.Orientation.Horizontal)

        # Left: session list only
        self._session_panel = SessionListPanel()
        self._session_panel.session_clicked.connect(self._on_session_clicked)
        self._session_panel.setMinimumWidth(340)
        self._session_panel.setMaximumWidth(500)
        self._top_split.addWidget(self._session_panel)

        # Right: session info editor (top) + subtitle editor (bottom)
        self._right_split = QSplitter(Qt.Orientation.Vertical)

        self._info_editor = SessionInfoEditor()
        self._info_editor.setMaximumHeight(130)
        self._right_split.addWidget(self._info_editor)

        self._subtitle_editor = SubtitleEditor()
        self._preview_title = self._subtitle_editor._title_lbl
        self._subtitle_editor.saved.connect(self._on_subtitle_editor_saved)
        self._subtitle_editor.mode_changed.connect(self._on_editor_mode_changed)
        for step in self._steps:
            if getattr(step, "STEP_ID", "") == "step3_burn":
                self._subtitle_editor.set_step3_bridge(step)
                break
        self._right_split.addWidget(self._subtitle_editor)

        self._right_split.setStretchFactor(0, 0)
        self._right_split.setStretchFactor(1, 1)
        self._top_split.addWidget(self._right_split)
        self._top_split.setStretchFactor(0, 0)
        self._top_split.setStretchFactor(1, 1)
        root.addWidget(self._top_split, stretch=1)

        # ── Bottom: step cards full width ──────────────────────────────────
        cards_container = QWidget()
        cards_h = QHBoxLayout(cards_container)
        cards_h.setSpacing(12)
        cards_h.setContentsMargins(4, 4, 4, 4)

        for step in self._steps:
            card = StepCard(step)
            self._cards.append(card)
            cards_h.addWidget(card)
        cards_h.addStretch()

        cards_container.adjustSize()
        cards_container.setMinimumWidth(cards_container.sizeHint().width())

        self._cards_scroll = QScrollArea()
        self._cards_scroll.setWidgetResizable(False)
        self._cards_scroll.setWidget(cards_container)
        self._cards_scroll.setFixedHeight(220)
        self._cards_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._cards_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._cards_scroll.setStyleSheet(
            "QScrollBar:horizontal{height:8px;background:#111828;border-radius:4px;}"
            "QScrollBar::handle:horizontal{background:#3d3d6e;border-radius:4px;}"
            "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}"
        )
        root.addWidget(self._cards_scroll)

        # ── Run controls ───────────────────────────────────────────────────
        self._run_ctrl = QFrame()
        self._run_ctrl.setStyleSheet(
            "QFrame{background:#111828;border:1px solid #2d2d4e;border-radius:8px;}"
        )
        ctrl_h = QHBoxLayout(self._run_ctrl)
        ctrl_h.setContentsMargins(12, 8, 12, 8)
        ctrl_h.setSpacing(8)

        self._btn_run_selected = QPushButton("▶  Run Selected Sessions")
        self._btn_run_selected.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #6c63ff,stop:1 #a855f7);color:white;font-weight:bold;font-size:14px;"
            "border:none;border-radius:7px;padding:9px 24px;}"
            "QPushButton:hover{background:#5a52d5;}"
            "QPushButton:disabled{background:#2a2a4a;color:#555;}"
        )
        self._btn_run_selected.clicked.connect(self._run_selected)
        ctrl_h.addWidget(self._btn_run_selected)

        self._btn_run_all_sessions = QPushButton("▶▶  Run All Sessions")
        self._btn_run_all_sessions.setStyleSheet(
            "QPushButton{background:#1a3a2a;color:#5dca8e;border:1px solid #2a6a4a;"
            "font-weight:bold;font-size:13px;border-radius:7px;padding:9px 20px;}"
            "QPushButton:hover{background:#2a5a3a;}"
            "QPushButton:disabled{color:#444;background:#1a2a1a;border-color:#252530;}"
        )
        self._btn_run_all_sessions.clicked.connect(self._run_all_sessions)
        ctrl_h.addWidget(self._btn_run_all_sessions)

        self._btn_stop = QPushButton("⏹  Stop After Current")
        self._btn_stop.setStyleSheet(
            "QPushButton{background:#2a1a0a;color:#ffaa55;border:1px solid #6e4a1a;"
            "font-weight:bold;border-radius:6px;padding:8px 16px;}"
            "QPushButton:hover{background:#4a2a0a;border-color:#ffaa55;}"
            "QPushButton:disabled{color:#554422;border-color:#3a2a1a;}"
        )
        self._btn_stop.setEnabled(False)
        self._btn_stop.setVisible(False)
        self._btn_stop.clicked.connect(self._request_stop)
        ctrl_h.addWidget(self._btn_stop)

        self._btn_cancel = QPushButton("✕  Cancel Job")
        self._btn_cancel.setStyleSheet(
            "QPushButton{background:#3a1a1a;color:#ff7070;border:1px solid #6e2d2d;"
            "font-weight:bold;padding:7px 14px;border-radius:6px;}"
            "QPushButton:hover{background:#5a2020;}"
        )
        self._btn_cancel.setVisible(False)
        self._btn_cancel.clicked.connect(self._cancel_job)
        ctrl_h.addWidget(self._btn_cancel)

        ctrl_h.addSpacing(8)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setFixedWidth(160)
        ctrl_h.addWidget(self._progress)

        self._queue_lbl = QLabel("")
        self._queue_lbl.setStyleSheet("color:#ffaa55;font-size:12px;font-weight:600;")
        ctrl_h.addWidget(self._queue_lbl)

        self._prog_lbl = QLabel("")
        self._prog_lbl.setStyleSheet("color:#888;font-size:11px;")
        ctrl_h.addWidget(self._prog_lbl)

        self._cost_summary_lbl = QLabel("No sessions selected")
        self._cost_summary_lbl.setStyleSheet(
            "color:#a0c8ff;font-size:11px;font-weight:600;"
        )
        ctrl_h.addWidget(self._cost_summary_lbl)
        ctrl_h.addStretch()
        root.addWidget(self._run_ctrl)

        # ── Log panel ──────────────────────────────────────────────────────
        self._log_wrap = QWidget()
        log_v = QVBoxLayout(self._log_wrap)
        log_v.setContentsMargins(0, 0, 0, 0)
        log_v.setSpacing(3)
        log_lbl = QLabel("Log")
        log_lbl.setStyleSheet("color:#a0a8ff;font-size:11px;font-weight:600;")
        log_v.addWidget(log_lbl)
        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setMaximumHeight(160)
        self._log_edit.setPlaceholderText("Multi-session pipeline log…")
        self._log_edit.setStyleSheet(
            "background:#0f0f23;border:1px solid #2d2d4e;border-radius:6px;"
            "padding:8px;color:#d0d0d0;font-family:'SF Mono','Consolas',monospace;font-size:12px;"
        )
        log_v.addWidget(self._log_edit)
        root.addWidget(self._log_wrap)

        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(
            "background:#0f0f23;border-top:1px solid #2d2d4e;color:#666;"
        )
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready — select sessions and steps, then Run")

    def _set_editor_mode(self, mode: str):
        if hasattr(self, "_subtitle_editor") and self._subtitle_editor:
            self._subtitle_editor.set_mode(mode)
        is_studio = mode == "studio"
        self._session_panel.setVisible(not is_studio)
        self._info_editor.setVisible(not is_studio)
        self._cards_scroll.setVisible(not is_studio)
        self._run_ctrl.setVisible(not is_studio)
        self._log_wrap.setVisible(not is_studio)
        self._btn_editor_default.setChecked(not is_studio)
        self._btn_editor_studio.setChecked(is_studio)
        if is_studio:
            self._top_split.setSizes([0, 1])
            self._right_split.setSizes([0, 1])

    def _on_editor_mode_changed(self, mode: str):
        self._set_editor_mode(mode)

    # ── Public: called by MainWindow when base_dir changes ────────────────────

    def update_base_dir(self, base_dir: str):
        self._base_dir = base_dir
        self._session_panel.set_base_dir(base_dir)
        self._autofill_keys()

    def _autofill_keys(self):
        """Push API keys from manager into step widgets.
        Called on init and whenever base_dir changes.
        Step cards create new widgets via build_config_widget() which clears
        previous values — this restores them from the persistent manager.
        """
        try:
            from core.api_keys import get_manager
            from core.pipeline.selection import (
                translate_key_candidates,
                tts_backend_from_label,
                tts_key_candidates,
            )

            mgr = get_manager()
            service_keys = mgr.to_dict_by_service()

            for step in self._steps:
                sid = getattr(step, "STEP_ID", "")

                # Step 1: Whisper API key
                if sid == "step1_transcribe":
                    if hasattr(step, "_api_key_edit") and step._api_key_edit:
                        key = service_keys.get("openai", "")
                        if key:
                            step._api_key_edit.blockSignals(True)
                            step._api_key_edit.setText(key)
                            step._api_key_edit.blockSignals(False)

                # Step 2: translate API key
                elif sid == "step2_translate":
                    if (
                        hasattr(step, "_api_edit")
                        and step._api_edit
                        and hasattr(step, "_backend_combo")
                        and step._backend_combo
                    ):
                        backend_text = step._backend_combo.currentText().lower()
                        bk = (
                            "gemini"
                            if "gemini" in backend_text
                            else "openai" if "openai" in backend_text else "google"
                        )
                        for svc in translate_key_candidates(bk):
                            key = service_keys.get(svc, "")
                            if key:
                                step._api_edit.blockSignals(True)
                                step._api_edit.setText(key)
                                step._api_edit.blockSignals(False)
                                break

                # Step 5: TTS API key
                elif sid == "step5_tts":
                    if (
                        hasattr(step, "_api_edit")
                        and step._api_edit
                        and hasattr(step, "_backend_combo")
                        and step._backend_combo
                    ):
                        bk = tts_backend_from_label(step._backend_combo.currentText())
                        for svc in tts_key_candidates(bk):
                            key = service_keys.get(svc, "")
                            if key:
                                step._api_edit.blockSignals(True)
                                step._api_edit.setText(key)
                                step._api_edit.blockSignals(False)
                                if hasattr(step, "_selected_api_key"):
                                    step._selected_api_key = key.strip()
                                break

                # Step 7: Publish Info (Gemini API key)
                elif sid == "step7_publish_info":
                    if hasattr(step, "_api_edit") and step._api_edit:
                        key = service_keys.get("gemini", "")
                        if key:
                            step._api_edit.blockSignals(True)
                            step._api_edit.setText(key)
                            step._api_edit.blockSignals(False)
                            if hasattr(step, "_selected_api_key"):
                                step._selected_api_key = key.strip()
        except Exception:
            pass  # Never block UI due to autofill failure

    # ── Session preview ───────────────────────────────────────────────────────

    def _small_lbl(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet("color:#666;font-size:10px;font-weight:600;")
        return l

    def _set_step3_source_file(self, source_file: str | None):
        if hasattr(self, "_subtitle_editor") and self._subtitle_editor:
            self._subtitle_editor.set_source_file(source_file)
        for step in self._steps:
            if getattr(step, "STEP_ID", "") != "step3_burn":
                continue
            setter = getattr(step, "set_source_file", None)
            if callable(setter):
                setter(source_file)
            break

    def _apply_session_studio_to_step3(self, session: Session):
        """Load per-session studio style and push into Step 3 controls."""
        try:
            studio = session.load_subtitle_studio()
        except Exception:
            studio = {}
        if not studio:
            return

        for step in self._steps:
            if getattr(step, "STEP_ID", "") != "step3_burn":
                continue
            ff = getattr(step, "_font_family_combo", None)
            if ff and studio.get("font_family"):
                ff.setCurrentText(str(studio.get("font_family")))
            fs = getattr(step, "_font_pct_spin", None)
            if fs and studio.get("font_pct") is not None:
                fs.setValue(float(studio.get("font_pct")))
            pos = getattr(step, "_pos_combo", None)
            if pos and studio.get("position"):
                pos.setCurrentText(str(studio.get("position")))
            refresh = getattr(step, "_refresh_preview", None)
            if callable(refresh):
                refresh()
            break

    def _on_session_clicked(self, sess_data: dict):
        """Load session into info editor, subtitle editor + restore step card statuses."""
        folder = sess_data["folder"]
        try:
            session = Session.load(folder)
        except Exception as e:
            self._subtitle_editor.clear()
            self._info_editor.clear()
            self._subtitle_editor.set_orig_placeholder(f"Cannot load session:\n{e}")
            return

        # Load info editor
        self._info_editor.load_session(session)
        self._set_step3_source_file(session.source_file)

        # Load subtitle preview + editor
        self._subtitle_editor.load_session(session)

        # Update TTS char count for clicked session
        for step in self._steps:
            if hasattr(step, "update_char_count"):
                step.update_char_count(session)

        # Restore step card statuses
        done_steps = session.done_steps()
        for step, card in zip(self._steps, self._cards):
            card.reset()
            if step.STEP_ID in done_steps:
                out_path = self._step_output_path(step, session)
                card.set_status("✅ Done (saved)", "loaded", out_path)
            else:
                card.set_status("Waiting…", "idle")

    def _on_subtitle_editor_saved(self, folder: str):
        """Reload active session and refresh session list row after studio/text save."""
        try:
            session = Session.load(folder)
        except Exception:
            return
        self._current_session = session
        self._info_editor.load_session(session)
        self._session_panel.refresh()

    def _format_usd(self, value: float | None) -> str:
        if value is None:
            return "?"
        return f"${value:.3f}"

    def _schedule_selected_cost_summary(self):
        selected = self._session_panel.get_selected_sessions()
        if not selected:
            self._cost_summary_lbl.setText("No sessions selected")
            self._cost_timer.stop()
            return

        self._cost_summary_lbl.setText("Calculating cost…")
        self._cost_request_id += 1
        self._cost_timer.start()

    def _run_selected_cost_worker(self):
        selected = self._session_panel.get_selected_sessions()
        if not selected:
            self._cost_summary_lbl.setText("No sessions selected")
            return

        request_id = self._cost_request_id
        translate_backend = self._step2_backend_key()
        tts_backend = self._step5_backend_key()
        worker = SelectedCostWorker(
            request_id, list(selected), translate_backend, tts_backend
        )
        worker.signals.finished.connect(self._on_selected_costs_ready)
        self._pool.start(worker)

    def _on_selected_costs_ready(
        self,
        request_id: int,
        step1: float,
        step2: float,
        step5: float,
        step1_unknown: bool,
    ):
        if request_id != self._cost_request_id:
            return

        total = step1 + step2 + step5
        step1_text = self._format_usd(step1)
        if step1_unknown:
            step1_text += "*"

        self._cost_summary_lbl.setText(
            f"{len(self._session_panel.get_selected_sessions())} selected | Step1: {step1_text} | Step2: {self._format_usd(step2)} | Step5: {self._format_usd(step5)} | Total: {self._format_usd(total)}"
        )

    def _step2_backend_key(self) -> str:
        for step in self._steps:
            if getattr(step, "STEP_ID", "") == "step2_translate":
                config = step.collect_config()
                return config.get("backend", "openai")
        return "openai"

    def _step5_backend_key(self) -> str:
        for step in self._steps:
            if getattr(step, "STEP_ID", "") == "step5_tts":
                config = step.collect_config()
                backend_label = config.get("backend", "gtts")
                return backend_label
        return "gtts"

    def _compute_selected_costs(self) -> tuple[float, float, float, bool]:
        selected = self._session_panel.get_selected_sessions()
        if not selected:
            return 0.0, 0.0, 0.0, False

        total_step1 = 0.0
        total_step2 = 0.0
        total_step5 = 0.0
        step1_unknown = False

        translation_backend = self._step2_backend_key()
        step5_backend = self._step5_backend_key()
        if step5_backend == "all":
            step5_rate = sum(TTS_COST_PER_1M.values())
        else:
            step5_rate = TTS_COST_PER_1M.get(step5_backend, 0.0)
        step2_rate = TRANSLATION_COST_PER_1M_CHARS.get(translation_backend, 0.0)

        for sess_data in selected:
            try:
                session = Session.load(sess_data["folder"])
            except Exception:
                continue

            duration_minutes = session.step1_duration_minutes()
            if duration_minutes is None:
                step1_unknown = True
            else:
                total_step1 += duration_minutes * WHISPER_API_COST_PER_MINUTE

            if session.step1_done:
                total_step2 += session.step1_transcript_chars() / 1_000_000 * step2_rate

            if session.step2_done:
                total_step5 += session.step2_translated_chars() / 1_000_000 * step5_rate

        return total_step1, total_step2, total_step5, step1_unknown

    def _update_selected_cost_summary(self):
        self._schedule_selected_cost_summary()

    # ── Run logic ─────────────────────────────────────────────────────────────

    def _run_selected(self):
        self._update_selected_cost_summary()
        sessions = self._session_panel.get_selected_sessions()
        if not sessions:
            QMessageBox.warning(self, "Nothing selected", "Check at least one session.")
            return
        self._start_queue(sessions)

    def _run_all_sessions(self):
        # Run ALL sessions regardless of checkbox state — do NOT call _select_all()
        self._update_selected_cost_summary()
        all_sessions = self._session_panel._sessions
        if not all_sessions:
            QMessageBox.information(
                self, "No sessions", "No sessions found in base folder."
            )
            return
        self._start_queue(all_sessions)

    def _start_queue(self, sessions: list[dict]):
        if self._worker is not None:
            QMessageBox.warning(self, "Busy", "A job is already running.")
            return

        enabled_steps = [s for s, c in zip(self._steps, self._cards) if c.is_enabled()]
        if not enabled_steps:
            QMessageBox.information(self, "Nothing to run", "Enable at least one step.")
            return

        # Build flat sequential job list
        self._job_queue = [
            (sess_data, step) for sess_data in sessions for step in enabled_steps
        ]

        total = len(self._job_queue)
        self._multi_started_at = time.perf_counter()
        self._multi_total_jobs = total
        self._multi_done_jobs = 0
        self._multi_total_sessions = len(sessions)
        self._multi_failed_sessions = set()
        self._multi_session_stats = {
            s["folder"]: {
                "name": s["name"],
                "total_steps": len(enabled_steps),
                "done_steps": 0,
                "failed_steps": 0,
                "start_at": 0.0,
                "end_at": 0.0,
            }
            for s in sessions
        }
        self._stop_requested = False

        self._log_edit.clear()
        self._session_panel.reset_all_status()

        self._log(
            f"🚀 Multi-session queue: {len(sessions)} sessions × "
            f"{len(enabled_steps)} steps = {total} jobs"
        )
        self._log("   Sessions: " + ",  ".join(s["name"] for s in sessions))
        self._log("   Steps:    " + "  →  ".join(s.LABEL for s in enabled_steps))

        # Mark all as queued
        self._session_panel.mark_queued([s["folder"] for s in sessions])

        self._set_running(True)
        self._run_next()

    def _run_next(self):
        if self._stop_requested:
            self._stop_requested = False
            remaining = len(self._job_queue)
            elapsed = time.perf_counter() - self._multi_started_at
            processed_sessions = sum(
                1
                for st in self._multi_session_stats.values()
                if st.get("start_at", 0) > 0
            )
            actions_per_min = (
                (self._multi_done_jobs * 60.0 / elapsed) if elapsed > 0 else 0.0
            )
            sessions_per_min = (
                (processed_sessions * 60.0 / elapsed) if elapsed > 0 else 0.0
            )
            self._job_queue.clear()
            self._set_running(False)
            self._queue_lbl.setText("")
            self._log(f"⏹  Stopped — {remaining} job(s) skipped.")
            self._log(
                f"📊 Multi summary: sessions={processed_sessions}/{self._multi_total_sessions} processed, "
                f"actions={self._multi_done_jobs}/{self._multi_total_jobs}, elapsed={elapsed:.2f}s, "
                f"throughput={actions_per_min:.2f} actions/min, {sessions_per_min:.2f} sessions/min"
            )
            return

        if not self._job_queue:
            self._set_running(False)
            self._queue_lbl.setText("")
            elapsed = time.perf_counter() - self._multi_started_at
            ok_sessions = self._multi_total_sessions - len(self._multi_failed_sessions)
            actions_per_min = (
                (self._multi_done_jobs * 60.0 / elapsed) if elapsed > 0 else 0.0
            )
            sessions_per_min = (
                (self._multi_total_sessions * 60.0 / elapsed) if elapsed > 0 else 0.0
            )
            self._log("🎉 All sessions processed!")
            self._log(
                f"📊 Multi summary: sessions ok={ok_sessions}, failed={len(self._multi_failed_sessions)}, "
                f"total={self._multi_total_sessions}; actions done={self._multi_done_jobs}/{self._multi_total_jobs}; elapsed={elapsed:.2f}s; "
                f"throughput={actions_per_min:.2f} actions/min, {sessions_per_min:.2f} sessions/min"
            )
            for folder, st in self._multi_session_stats.items():
                sess_elapsed = (st["end_at"] or time.perf_counter()) - (
                    st["start_at"] or self._multi_started_at
                )
                session_apm = (
                    (st["done_steps"] * 60.0 / sess_elapsed)
                    if sess_elapsed > 0
                    else 0.0
                )
                self._log(
                    f"   • [{st['name']}] actions={st['done_steps']}/{st['total_steps']} "
                    f"failed_steps={st['failed_steps']} time={sess_elapsed:.2f}s throughput={session_apm:.2f} actions/min"
                )
            self._status_bar.showMessage("✅ All sessions complete!")
            self._session_panel.refresh()
            return

        sess_data, step = self._job_queue[0]
        folder = sess_data["folder"]
        remaining = len(self._job_queue)

        # Load session object
        try:
            session = Session.load(folder)
        except Exception as e:
            self._log(f"❌ Cannot load session [{sess_data['name']}]: {e}")
            self._job_queue.pop(0)
            self._run_next()
            return

        self._current_session = session
        self._set_step3_source_file(session.source_file)
        self._apply_session_studio_to_step3(session)
        st = self._multi_session_stats.get(folder)
        if st and not st["start_at"]:
            st["start_at"] = time.perf_counter()

        # Update UI
        self._session_panel.set_session_status(folder, "running", step.STEP_ID)
        self._queue_lbl.setText(
            f"[{sess_data['name']}]  {step.LABEL}  ({remaining} left)"
        )
        self._log(f"\n▶  [{sess_data['name']}]  {step.LABEL}")

        # Update base dir for steps with set_base_dir (e.g. BurnStep channel profiles)
        for s in self._steps:
            setter = getattr(s, "set_base_dir", None)
            if callable(setter):
                setter(self._base_dir)

        # Ensure API keys are filled before collecting config
        self._autofill_keys()

        config = step.collect_config()
        card = self._card_for(step)
        card.set_status("▶ Running…", "running")
        card.set_running(True)
        self._progress.setVisible(True)

        worker = step.make_worker(session, config)
        self._worker = worker
        worker.signals.progress.connect(self._log)
        worker.signals.finished.connect(
            lambda r, s=step, sd=sess_data: self._on_done(s, sd, r)
        )
        worker.signals.error.connect(
            lambda e, s=step, sd=sess_data: self._on_error(s, sd, e)
        )
        worker.signals.cancelled.connect(
            lambda s=step, sd=sess_data: self._on_cancelled(s, sd)
        )
        self._pool.start(worker)

    def _on_done(self, step, sess_data: dict, result):
        self._worker = None
        folder = sess_data["folder"]
        self._multi_done_jobs += 1
        st = self._multi_session_stats.get(folder)
        if st:
            st["done_steps"] += 1

        card = self._card_for(step)
        card.set_running(False)
        out_path = result if isinstance(result, str) else ""
        if not out_path and self._current_session:
            out_path = self._step_output_path(step, self._current_session)
        card.set_status("✅ Done", "done", out_path)

        self._session_panel.set_session_status(folder, "done", step.STEP_ID)

        if step.STEP_ID == "step7_publish_info":
            try:
                self._current_session = Session.load(folder)
                self._info_editor.load_session(self._current_session)
                self._session_panel.refresh()
            except Exception:
                pass

        # Check if this session has no more jobs after this one
        more_for_session = any(sd["folder"] == folder for sd, _ in self._job_queue[1:])
        if not more_for_session:
            self._session_panel.set_session_status(folder, "done")
            st = self._multi_session_stats.get(folder)
            if st:
                st["end_at"] = time.perf_counter()
                sess_elapsed = st["end_at"] - st["start_at"]
                session_apm = (
                    (st["done_steps"] * 60.0 / sess_elapsed)
                    if sess_elapsed > 0
                    else 0.0
                )
                self._log(
                    f"✅ Session [{sess_data['name']}] complete — actions {st['done_steps']}/{st['total_steps']} in {sess_elapsed:.2f}s | throughput={session_apm:.2f} actions/min"
                )
            else:
                self._log(f"✅ Session [{sess_data['name']}] complete")

        elapsed = time.perf_counter() - self._multi_started_at
        actions_per_min = (
            (self._multi_done_jobs * 60.0 / elapsed) if elapsed > 0 else 0.0
        )
        self._log(
            f"📈 Multi progress: actions {self._multi_done_jobs}/{self._multi_total_jobs} | avg throughput={actions_per_min:.2f} actions/min"
        )

        if self._job_queue and self._job_queue[0][1] is step:
            self._job_queue.pop(0)

        self._run_next()

    def _on_error(self, step, sess_data: dict, msg: str):
        self._worker = None
        folder = sess_data["folder"]
        self._multi_failed_sessions.add(folder)
        st = self._multi_session_stats.get(folder)
        if st:
            st["failed_steps"] += 1
            st["end_at"] = time.perf_counter()

        card = self._card_for(step)
        card.set_running(False)
        card.set_status("❌ Error", "error")

        self._session_panel.set_session_status(folder, "error", step.STEP_ID)
        self._log(f"❌ [{sess_data['name']}] [{step.LABEL}]: {msg}")
        if st:
            sess_elapsed = st["end_at"] - (st["start_at"] or st["end_at"])
            session_apm = (
                (st["done_steps"] * 60.0 / sess_elapsed) if sess_elapsed > 0 else 0.0
            )
            self._log(
                f"📊 Session [{sess_data['name']}] failed — actions {st['done_steps']}/{st['total_steps']} in {sess_elapsed:.2f}s | throughput={session_apm:.2f} actions/min"
            )

        # Skip remaining jobs for this session, continue with others
        self._job_queue = [
            (sd, s) for sd, s in self._job_queue if sd["folder"] != folder
        ]
        self._log(f"⏭️  Skipping remaining steps for [{sess_data['name']}]")
        self._run_next()

    def _on_cancelled(self, step, sess_data: dict):
        self._worker = None
        self._set_running(False)
        self._job_queue.clear()
        self._queue_lbl.setText("")
        card = self._card_for(step)
        card.set_running(False)
        card.set_status("🚫 Cancelled", "idle")
        self._session_panel.set_session_status(sess_data["folder"], "idle")
        self._log("🚫 Queue cancelled.")

    def _request_stop(self):
        self._stop_requested = True
        self._btn_stop.setEnabled(False)
        self._btn_stop.setText("Stopping…")
        self._log("⏹  Stop after current job — waiting…")

    def _cancel_job(self):
        if self._worker and hasattr(self._worker, "cancel"):
            self._worker.cancel()
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setText("Cancelling…")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_running(self, running: bool):
        self._btn_run_selected.setEnabled(not running)
        self._btn_run_all_sessions.setEnabled(not running)
        self._btn_stop.setVisible(running)
        self._btn_stop.setEnabled(running)
        self._btn_stop.setText("⏹  Stop After Current")
        self._btn_cancel.setVisible(running)
        self._btn_cancel.setEnabled(True)
        self._btn_cancel.setText("✕  Cancel Job")
        self._progress.setVisible(running)
        if not running:
            self._prog_lbl.setText("")

    def _card_for(self, step) -> StepCard:
        return self._cards[self._steps.index(step)]

    def _step_output_path(self, step, session) -> str:
        attr = {
            "step1_transcribe": "step1_json",
            "step2_translate": "step2_srt",
            "step3_burn": "step3_video",
            "step4_separate": "step4_vocals",
            "step5_tts": "step5_tts",
            "step6_add_voice": "step6_video",
            "step7_publish_info": "step7_info",
        }.get(step.STEP_ID, "")
        if attr:
            p = getattr(session, attr, None)
            if p and Path(str(p)).exists():
                return str(p)
        return ""

    def _log(self, msg: str):
        self._log_edit.append(msg)
        self._prog_lbl.setText(msg.strip()[:80])
        self._status_bar.showMessage(msg.strip()[:120])
        FileLogger.get().write(msg)
