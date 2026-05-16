"""MainWindow — orchestrates the pipeline with session management."""

import os
import time
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, Qt, QThreadPool
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
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
    QSizePolicy,
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
from core.pipeline.step6_add_voice import AddVoiceStep
from core.pipeline.step7_publish import PublishInfoStep
from core.session import Session
from ui.multi_session_window import MultiSessionWindow
from ui.widgets.drop_zone import SUPPORTED, DropZone
from ui.widgets.session_info_editor import SessionInfoEditor
from ui.widgets.step_card import StepCard
from ui.dialogs.api_keys_dialog import ApiKeysDialog
from ui.dialogs.session_picker_dialog import SessionPickerDialog
from ui.theme import STYLESHEET, apply_app_theme
from ui.widgets.subtitle_editor import SubtitleEditor


# ── MainWindow ────────────────────────────────────────────────────────────────


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SubSync  —  AI Video Pipeline")
        self.setMinimumSize(860, 580)
        app = QApplication.instance()
        if app and not app.styleSheet():
            apply_app_theme(app)
        else:
            self.setStyleSheet(STYLESHEET)

        self._file = None
        self._session = None
        self._worker = None
        self._pool = QThreadPool.globalInstance()
        self._queue: list = []
        self._stop_queue = False
        self._single_run_started_at = 0.0
        self._single_total_steps = 0
        self._single_done_steps = 0

        self._steps = [
            TranscribeStep(),
            TranslateStep(),
            BurnStep(),
            SeparateStep(),
            TTSStep(),
            AddVoiceStep(),
            PublishInfoStep(),
        ]
        self._cards: list[StepCard] = []
        self._multi_window: MultiSessionWindow | None = None
        self._setup_ui()
        self._restore_last_workspace()
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

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
            "AI Video Pipeline  ·  Transcribe → Translate → Burn → Separate → TTS → Add Voice"
        )
        sub.setWordWrap(True)
        sub.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        sub.setMinimumWidth(120)
        sub.setStyleSheet("font-size:11px;color:#444;margin-left:12px;margin-top:8px;")
        title_row.addWidget(t)
        title_row.addWidget(sub, stretch=1)
        title_row.addSpacing(10)

        self._btn_editor_default = QPushButton("Default")
        self._btn_editor_default.setCheckable(True)
        self._btn_editor_default.setChecked(True)
        self._btn_editor_default.setFixedHeight(26)
        self._btn_editor_default.clicked.connect(
            lambda: self._set_editor_mode("default")
        )
        title_row.addWidget(self._btn_editor_default)

        self._btn_editor_studio = QPushButton("Studio")
        self._btn_editor_studio.setCheckable(True)
        self._btn_editor_studio.setFixedHeight(26)
        self._btn_editor_studio.clicked.connect(lambda: self._set_editor_mode("studio"))
        title_row.addWidget(self._btn_editor_studio)
        title_row.addStretch()

        # Multi-session button
        self._btn_multi = QPushButton("⚡  Multi-Session")
        self._btn_multi.setStyleSheet(
            "QPushButton{background:#1a2a3a;color:#c084fc;border:1px solid #4a3a6a;"
            "font-weight:bold;border-radius:6px;padding:6px 14px;font-size:12px;}"
            "QPushButton:hover{background:#2a2a5a;border-color:#c084fc;}"
        )
        self._btn_multi.clicked.connect(self._open_multi_session)
        title_row.addWidget(self._btn_multi)

        root.addLayout(title_row)

        # Log + subtitle editor (built first — upper panel wraps in scroll + splitter below)
        self._editor_split = QSplitter(Qt.Orientation.Vertical)
        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setMaximumHeight(130)
        self._log_edit.setPlaceholderText("Pipeline log…")
        self._log_wrap = self._wrap("Log", self._log_edit)
        self._editor_split.addWidget(self._log_wrap)

        self._subtitle_editor = SubtitleEditor()
        self._subtitle_editor.set_orig_placeholder("Original transcript…")
        self._subtitle_editor.set_trans_placeholder("Translated subtitles…")
        self._subtitle_editor.saved.connect(self._on_subtitle_editor_saved)
        self._subtitle_editor.mode_changed.connect(self._on_editor_mode_changed)
        for step in self._steps:
            if getattr(step, "STEP_ID", "") == "step3_burn":
                self._subtitle_editor.set_step3_bridge(step)
                break
        self._editor_split.addWidget(self._subtitle_editor)

        self._top_panel = QWidget()
        top_l = QVBoxLayout(self._top_panel)
        top_l.setContentsMargins(0, 0, 0, 0)
        top_l.setSpacing(10)

        # Session bar
        self._session_bar = QFrame()
        self._session_bar.setObjectName("session_bar")
        sh = QHBoxLayout(self._session_bar)
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

        # Session picker button
        self._btn_sessions = QPushButton("📁  Sessions")
        self._btn_sessions.setStyleSheet(
            "QPushButton{background:#1a3a5a;color:#60aaff;border:1px solid #2a5a8a;"
            "font-weight:bold;border-radius:6px;padding:5px 12px;}"
            "QPushButton:hover{background:#2a5a8a;}"
        )
        self._btn_sessions.clicked.connect(self._open_session_picker)
        sh.addWidget(self._btn_sessions)

        sh.addSpacing(12)
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

        btn_keys = QPushButton("🔑  API Keys")
        btn_keys.setStyleSheet(
            "QPushButton{background:#1a3a1a;color:#5dca8e;border:1px solid #2a6a2a;"
            "font-weight:bold;border-radius:6px;padding:5px 12px;}"
            "QPushButton:hover{background:#2a5a2a;}"
        )
        btn_keys.clicked.connect(self._open_api_keys_dialog)
        sh.addWidget(btn_keys)

        btn_logs = QPushButton("📋  Logs")
        btn_logs.setStyleSheet(
            "QPushButton{background:#1a2a3a;color:#60c8ff;border:1px solid #2a4a6a;"
            "font-weight:bold;border-radius:6px;padding:5px 12px;}"
            "QPushButton:hover{background:#2a4a6a;}"
        )
        btn_logs.clicked.connect(self._open_logs_folder)
        sh.addWidget(btn_logs)
        top_l.addWidget(self._session_bar)

        # Session info editor (title + description)
        self._info_editor = SessionInfoEditor()
        self._info_editor.setMaximumHeight(120)
        top_l.addWidget(self._info_editor)

        # File input
        self._file_row = QWidget()
        fi = QHBoxLayout(self._file_row)
        fi.setContentsMargins(0, 0, 0, 0)
        self._drop = DropZone(self._set_file)
        fi.addWidget(self._drop, stretch=1)
        self._file_edit = QLineEdit()
        self._file_edit.setPlaceholderText("File path…")
        self._file_edit.setReadOnly(True)
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse_file)
        fi.addWidget(self._file_edit, stretch=2)
        fi.addWidget(btn_browse)
        top_l.addWidget(self._file_row)

        # Step cards
        cards_container = QWidget()
        cards_h = QHBoxLayout(cards_container)
        cards_h.setSpacing(12)
        cards_h.setContentsMargins(4, 4, 4, 4)
        for step in self._steps:
            card = StepCard(step)
            card.on_run = lambda s=step: self._run_step(s)
            self._cards.append(card)
            cards_h.addWidget(card)
            # When Step 2 backend changes, re-autofill the API key field
            if (
                getattr(step, "STEP_ID", "") == "step2_translate"
                and hasattr(step, "_backend_combo")
                and step._backend_combo
            ):
                step._backend_combo.currentIndexChanged.connect(
                    self._on_step2_backend_changed
                )
        cards_h.addStretch()
        cards_container.adjustSize()
        cards_container.setMinimumWidth(cards_container.sizeHint().width())
        self._cards_scroll = QScrollArea()
        self._cards_scroll.setWidgetResizable(False)
        self._cards_scroll.setWidget(cards_container)
        self._cards_scroll.setMinimumHeight(220)
        self._cards_scroll.setMaximumHeight(480)
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
        top_l.addWidget(self._cards_scroll)

        # Run All / Stop / Cancel
        self._run_ctrl = QFrame()
        self._run_ctrl.setStyleSheet(
            "QFrame{background:#111828;border:1px solid #2d2d4e;border-radius:8px;}"
        )
        ctrl_outer = QVBoxLayout(self._run_ctrl)
        ctrl_outer.setContentsMargins(12, 8, 12, 8)
        ctrl_outer.setSpacing(8)

        ctrl_h = QHBoxLayout()
        ctrl_h.setSpacing(8)

        self._btn_run_all = QPushButton("▶▶  Run all (enabled)")
        self._btn_run_all.setToolTip("Queue and run every step that has its checkbox enabled")
        self._btn_run_all.setObjectName("run_all_btn")
        self._btn_run_all.setStyleSheet(
            "QPushButton#run_all_btn{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #6c63ff,stop:1 #a855f7);color:white;font-weight:bold;font-size:14px;"
            "border:none;border-radius:7px;padding:9px 24px;}"
            "QPushButton#run_all_btn:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #5a52d5,stop:1 #9333ea);}"
            "QPushButton#run_all_btn:disabled{background:#2a2a4a;color:#555;}"
        )
        self._btn_run_all.setMinimumWidth(96)
        self._btn_run_all.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        self._btn_run_all.clicked.connect(self._run_all)
        ctrl_h.addWidget(self._btn_run_all)

        self._btn_stop = QPushButton("⏹  Stop Queue")
        self._btn_stop.setStyleSheet(
            "QPushButton{background:#2a1a0a;color:#ffaa55;border:1px solid #6e4a1a;"
            "font-weight:bold;border-radius:6px;padding:8px 16px;}"
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
        ctrl_h.addStretch()

        ctrl_outer.addLayout(ctrl_h)

        ctrl_meta = QHBoxLayout()
        ctrl_meta.setSpacing(8)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setMinimumWidth(100)
        self._progress.setMaximumWidth(220)
        ctrl_meta.addWidget(self._progress)
        self._prog_lbl = QLabel("")
        self._prog_lbl.setStyleSheet("color:#888;font-size:11px;")
        self._queue_lbl = QLabel("")
        self._queue_lbl.setWordWrap(True)
        self._queue_lbl.setStyleSheet("color:#ffaa55;font-size:12px;font-weight:600;")
        self._queue_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        ctrl_meta.addWidget(self._queue_lbl, stretch=1)
        ctrl_meta.addWidget(self._prog_lbl)
        ctrl_outer.addLayout(ctrl_meta)

        top_l.addWidget(self._run_ctrl)

        self._top_scroll = QScrollArea()
        self._top_scroll.setWidgetResizable(True)
        self._top_scroll.setWidget(self._top_panel)
        self._top_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._top_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._top_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._top_scroll.setMinimumHeight(200)

        self._body_split = QSplitter(Qt.Orientation.Vertical)
        self._body_split.addWidget(self._top_scroll)
        self._body_split.addWidget(self._editor_split)
        self._body_split.setStretchFactor(0, 1)
        self._body_split.setStretchFactor(1, 2)
        self._body_split.setSizes([340, 420])

        root.addWidget(self._body_split, stretch=1)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready — choose session folder then drop a video")

    def _set_editor_mode(self, mode: str):
        if hasattr(self, "_subtitle_editor") and self._subtitle_editor:
            self._subtitle_editor.set_mode(mode)
        is_studio = mode == "studio"
        # Switch the whole editing area focus.
        self._top_scroll.setVisible(not is_studio)
        self._log_wrap.setVisible(not is_studio)
        self._btn_editor_default.setChecked(not is_studio)
        self._btn_editor_studio.setChecked(is_studio)
        if is_studio:
            self._editor_split.setSizes([0, 1])
            self._body_split.setSizes([0, max(400, self.height())])
        else:
            self._body_split.setSizes([340, 420])

    def _on_editor_mode_changed(self, mode: str):
        # Keep top switch in sync even if mode changed inside SubtitleEditor.
        self._set_editor_mode(mode)

    # ── Multi-session ─────────────────────────────────────────────────────────

    def _open_multi_session(self):
        base = self._sess_dir_edit.text().strip()
        if not base:
            QMessageBox.warning(
                self, "No folder", "Choose a session base folder first."
            )
            return
        if self._multi_window is None:
            self._multi_window = MultiSessionWindow(
                steps=self._steps,
                base_dir=base,
                parent=self,
            )
        else:
            self._multi_window.update_base_dir(base)
        self._multi_window.show()
        self._multi_window.raise_()
        self._multi_window.activateWindow()

    # ── Session management ────────────────────────────────────────────────────

    def _open_logs_folder(self):
        from core.log_file import FileLogger
        from core.mac_utils import reveal_in_finder

        logger = FileLogger.get()
        if logger.log_path and logger.log_path.parent.exists():
            reveal_in_finder(logger.log_path.parent)
        else:
            base = self._sess_dir_edit.text().strip()
            if base:
                self._load_api_keys(base)
                self._open_logs_folder()
            else:
                QMessageBox.information(
                    self,
                    "No logs",
                    "Choose a session folder first to enable file logging.",
                )

    def _load_api_keys(self, base_dir: str):
        """Auto-load API keys from <base_dir>/.subsync_keys"""
        from pathlib import Path as _Path

        from core.api_keys import ENV_FILE, get_manager, load_keys
        from core.log_file import FileLogger

        # Init file logger
        FileLogger.get().init(base_dir)
        self._log(f"📋 Log file: {FileLogger.get().log_path}")
        load_keys(base_dir)
        keys_file = _Path(base_dir) / ENV_FILE
        mgr = get_manager()
        loaded = {k: v for k, v in mgr.get_all().items() if v}
        if keys_file.exists() and loaded:
            self._log(f"🔑 Loaded {len(loaded)} API keys from {keys_file.name}")
            self._autofill_api_keys(mgr)
        else:
            self._log(f"💡 Tip: Create {ENV_FILE} in this folder to auto-load API keys")

    def _autofill_api_keys(self, mgr):
        """Push loaded keys into step config widgets."""
        from core.pipeline.selection import (
            translate_key_candidates,
            tts_backend_from_label,
            tts_key_candidates,
        )

        service_keys = mgr.to_dict_by_service()
        for step, _card in zip(self._steps, self._cards):
            # Step 1 transcribe (Whisper API uses OpenAI key)
            if (
                getattr(step, "STEP_ID", "") == "step1_transcribe"
                and hasattr(step, "_api_key_edit")
                and step._api_key_edit
            ):
                key = service_keys.get("openai", "")
                if key:
                    # Always sync from manager → field so API Keys dialog updates Step 1
                    step._api_key_edit.blockSignals(True)
                    step._api_key_edit.setText(key)
                    step._api_key_edit.blockSignals(False)

            # Step 2 translate — always sync from manager so backend changes pick up correct key
            if (
                getattr(step, "STEP_ID", "") == "step2_translate"
                and hasattr(step, "_api_edit")
                and step._api_edit
                and hasattr(step, "_backend_combo")
                and step._backend_combo
            ):
                from core.pipeline.selection import translate_backend_from_index

                be_idx = step._backend_combo.currentIndex()
                backend_key = translate_backend_from_index(be_idx)
                candidates = translate_key_candidates(backend_key)
                for service in candidates:
                    key = service_keys.get(service, "")
                    if key:
                        step._api_edit.blockSignals(True)
                        step._api_edit.setText(key)
                        step._api_edit.blockSignals(False)
                        break

            # Step 5 TTS — always sync from manager so backend change triggers refill
            if (
                getattr(step, "STEP_ID", "") == "step5_tts"
                and hasattr(step, "_api_edit")
                and step._api_edit
                and hasattr(step, "_backend_combo")
                and step._backend_combo
            ):
                backend_key = tts_backend_from_label(step._backend_combo.currentText())
                candidates = tts_key_candidates(backend_key)
                for service in candidates:
                    key = service_keys.get(service, "")
                    if key:
                        step._api_edit.blockSignals(True)
                        step._api_edit.setText(key)
                        step._api_edit.blockSignals(False)
                        if hasattr(step, "_selected_api_key"):
                            step._selected_api_key = key.strip()
                        break
                else:
                    # No key found for this backend — clear field so user knows
                    if not step._api_edit.text().strip():
                        pass  # leave empty, don't clear user-typed keys

            # Step 7 Publish Info (Gemini) — keep in sync with API manager
            if (
                getattr(step, "STEP_ID", "") == "step7_publish_info"
                and hasattr(step, "_api_edit")
                and step._api_edit
            ):
                key = service_keys.get("gemini", "")
                if key:
                    step._api_edit.blockSignals(True)
                    step._api_edit.setText(key)
                    step._api_edit.blockSignals(False)
                    if hasattr(step, "_selected_api_key"):
                        step._selected_api_key = key.strip()

    def _on_step2_backend_changed(self, _idx: int = 0):
        """Re-autofill Step 2 API key when backend combo changes."""
        try:
            from core.api_keys import get_manager

            self._autofill_api_keys(get_manager())
        except Exception:
            pass

    def _open_api_keys_dialog(self):
        """Open dialog to manage API keys."""
        base = self._sess_dir_edit.text().strip()
        dlg = ApiKeysDialog(base, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if base:
                from core.api_keys import load_keys, save_keys

                save_keys(base)
                load_keys(base)
                from core.api_keys import get_manager

                self._autofill_api_keys(get_manager())
                self._log("🔑 API keys saved and applied")

    def _pick_base_dir(self):
        # Save current configs before switching workspace
        self._persist_step_configs()

        d = QFileDialog.getExistingDirectory(self, "Choose base folder for sessions")
        if d:
            self._sess_dir_edit.setText(d)
            self._set_steps_base_dir(d)
            self._load_api_keys(d)
            # Restore step configs saved for this workspace
            from core.config_store import load_step_configs

            if load_step_configs(d, self._steps):
                self._log("⚙️  Step settings restored from workspace")
            self._status_bar.showMessage(f"Base folder: {d}")
            if self._multi_window is not None:
                self._multi_window.update_base_dir(d)

    def _persist_step_configs(self):
        """Write step UI to workspace .subsync_step_configs.json (safe to call often)."""
        base = self._sess_dir_edit.text().strip()
        if not base or not os.path.isdir(base):
            return
        try:
            from core.config_store import save_step_configs

            save_step_configs(base, self._steps)
        except Exception:
            pass

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is QApplication.instance()
            and event.type() == QEvent.Type.ApplicationStateChange
        ):
            try:
                st = QApplication.applicationState()
            except Exception:
                st = None
            if (
                st is not None
                and st != Qt.ApplicationState.ApplicationActive
                and hasattr(self, "_sess_dir_edit")
            ):
                self._persist_step_configs()
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        self._persist_step_configs()
        base = self._sess_dir_edit.text().strip()
        if base:
            from PyQt6.QtCore import QSettings

            QSettings("SubSync", "SubSync").setValue("last_workspace", base)
        super().closeEvent(event)

    def _restore_last_workspace(self):
        """On app start, restore last workspace and its step configs."""
        import os

        from PyQt6.QtCore import QSettings

        last = QSettings("SubSync", "SubSync").value("last_workspace", "")
        if last and os.path.isdir(last):
            self._sess_dir_edit.setText(last)
            self._set_steps_base_dir(last)
            self._load_api_keys(last)
            from core.config_store import load_step_configs

            load_step_configs(last, self._steps)
            self._status_bar.showMessage(f"Restored workspace: {last}")

    def _set_steps_base_dir(self, base_dir: str):
        """Pass base directory to steps that keep shared assets/config."""
        for step in self._steps:
            setter = getattr(step, "set_base_dir", None)
            if callable(setter):
                setter(base_dir)

    def _set_step3_source_file(self, source_file: str | None):
        """Push current source file into Step 3 for accurate preview ratio/size."""
        if hasattr(self, "_subtitle_editor") and self._subtitle_editor:
            self._subtitle_editor.set_source_file(source_file)
        for step in self._steps:
            if getattr(step, "STEP_ID", "") != "step3_burn":
                continue
            setter = getattr(step, "set_source_file", None)
            if callable(setter):
                setter(source_file)
            break

    def _on_subtitle_editor_saved(self, folder: str):
        """Refresh session-bound widgets after subtitle/studio save."""
        if not self._session or str(self._session.folder) != str(folder):
            return
        try:
            self._session = Session.load(folder)
        except Exception:
            return
        self._info_editor.load_session(self._session)

    def _open_session_picker(self):
        base = self._sess_dir_edit.text().strip()
        if not base:
            QMessageBox.warning(
                self, "No folder", "Choose a session base folder first."
            )
            return
        self._set_steps_base_dir(base)
        dlg = SessionPickerDialog(base, parent=self)
        result = dlg.exec()

        if result == QDialog.DialogCode.Accepted and dlg.selected_folder:
            # Resume existing session
            self._load_session(dlg.selected_folder)
        # else: New session — do nothing, user will use file picker

    def _load_session(self, folder: str):
        """Load an existing session and restore UI state."""
        try:
            session = Session.load(folder)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Cannot load session:\n{e}")
            return

        self._session = session
        self._file = session.source_file
        self._set_step3_source_file(self._file)
        self._file_edit.setText(session.source_file)
        self._drop.set_file(Path(session.source_file).name)
        self._sess_name_lbl.setText(session.folder.name)

        self._log_edit.clear()
        self._log(f"📂 Resumed session: {session.folder.name}")
        self._log(f"📄 Source: {Path(session.source_file).name}")

        # Restore card states based on what's already done
        done_steps = session.done_steps()
        for step, card in zip(self._steps, self._cards):
            card.reset()
            if step.STEP_ID in done_steps:
                out_path = self._step_output_path(step, session)
                card.set_status("✅ Done (saved)", "loaded", out_path)
                self._log(
                    f"  ✅ {step.LABEL} — already done → {Path(out_path).name if out_path else ''}"
                )

        # Load subtitle editor
        self._subtitle_editor.load_session(session)

        # Populate Step 6 manifest picker
        for step in self._steps:
            if hasattr(step, "populate_manifest_picker"):
                step.populate_manifest_picker(session)

        # Load session info editor
        self._info_editor.load_session(session)

        # Update TTS char count
        for step in self._steps:
            if hasattr(step, "update_char_count"):
                step.update_char_count(session)

        done_labels = [s.LABEL for s in self._steps if s.STEP_ID in done_steps]
        self._log(
            f"💡 Completed steps: {', '.join(done_labels) if done_labels else 'None'}"
        )
        self._log(
            "💡 You can run any step from here — completed steps will be skipped or overwritten."
        )
        self._status_bar.showMessage(f"Session loaded — {len(done_steps)}/6 steps done")

    def _step_output_path(self, step, session) -> str:
        """Get the output file path for a completed step."""
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

    def _ensure_session(self) -> bool:
        """Create new session or reuse existing one."""
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
        self._set_step3_source_file(self._file)
        self._sess_name_lbl.setText(self._session.folder.name)
        self._info_editor.load_session(self._session)
        self._log(f"📁 New session: {self._session.folder}")
        return True

    def _open_session_folder(self):
        if not self._session:
            QMessageBox.information(self, "No session", "Run a step first.")
            return
        from core.mac_utils import reveal_in_finder

        reveal_in_finder(self._session.folder)

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
        self._set_step3_source_file(path)
        self._session = None
        self._file_edit.setText(path)
        self._drop.set_file(Path(path).name)
        self._sess_name_lbl.setText("—  (created on first Run)")
        self._subtitle_editor.clear()
        self._info_editor.clear()
        for step in self._steps:
            if hasattr(step, "update_char_count"):
                step.update_char_count(None)
        for card in self._cards:
            card.reset()
        self._status_bar.showMessage(f"File: {Path(path).name}")

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
        # Also write to daily log file
        from core.log_file import FileLogger

        FileLogger.get().write(msg)

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
        self._queue = [s for s, c in zip(self._steps, self._cards) if c.is_enabled()]
        if not self._queue:
            QMessageBox.information(
                self, "Nothing to run", "Enable at least one step first."
            )
            return
        self._stop_queue = False
        self._single_run_started_at = time.perf_counter()
        self._single_total_steps = len(self._queue)
        self._single_done_steps = 0
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
            processed = self._single_done_steps
            total = self._single_total_steps
            elapsed = time.perf_counter() - self._single_run_started_at
            apm = (processed * 60.0 / elapsed) if elapsed > 0 else 0.0
            self._queue = []
            self._queue_lbl.setText("")
            self._log(f"⏹  Queue stopped — {remaining} step(s) skipped.")
            self._log(
                f"📊 Single-session summary: {processed}/{total} actions done in {elapsed:.2f}s | throughput={apm:.2f} actions/min"
            )
            return
        if not self._queue:
            self._set_queue_running(False)
            self._set_busy(False)
            self._queue_lbl.setText("")
            total = self._single_total_steps
            elapsed = time.perf_counter() - self._single_run_started_at
            apm = (total * 60.0 / elapsed) if elapsed > 0 else 0.0
            self._log("🎉 All steps complete!")
            self._log(
                f"📊 Single-session summary: {total}/{total} actions done in {elapsed:.2f}s | throughput={apm:.2f} actions/min"
            )
            self._status_bar.showMessage("✅ All steps complete!")
            return
        step = self._queue[0]
        self._queue_lbl.setText(f"Queue: {step.LABEL}  ({len(self._queue)} left)")
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
        self._worker = None
        self._set_busy(False)
        self._single_done_steps += 1
        card = self._card_for(step)
        card.set_running(False)
        out_path = result if isinstance(result, str) else ""
        if not out_path and self._session:
            out_path = self._step_output_path(step, self._session)
        card.set_status("✅ Done", "done", out_path)
        self._log(
            f"📈 Queue progress: {self._single_done_steps}/{self._single_total_steps} actions"
        )
        self._update_previews(step, result)
        if self._queue and self._queue[0] is step:
            self._queue.pop(0)
        self._persist_step_configs()
        self._run_next_in_queue()

    def _error_queue(self, step, msg):
        self._worker = None
        self._set_busy(False)
        self._set_queue_running(False)
        elapsed = time.perf_counter() - self._single_run_started_at
        apm = (self._single_done_steps * 60.0 / elapsed) if elapsed > 0 else 0.0
        self._queue = []
        self._queue_lbl.setText("")
        card = self._card_for(step)
        card.set_running(False)
        card.set_status("❌ Error", "error")
        self._log(f"❌ ERROR [{step.LABEL}]: {msg}")
        self._log(
            f"📊 Single-session summary: {self._single_done_steps}/{self._single_total_steps} actions done in {elapsed:.2f}s | throughput={apm:.2f} actions/min"
        )
        QMessageBox.critical(self, f"Error — {step.LABEL}", f"{msg}\n\nQueue stopped.")

    def _cancelled_queue(self, step):
        self._worker = None
        self._set_busy(False)
        self._set_queue_running(False)
        self._queue = []
        self._queue_lbl.setText("")
        card = self._card_for(step)
        card.set_running(False)
        card.set_status("🚫 Cancelled", "idle")
        self._log("🚫 Queue cancelled.")

    def _request_stop_queue(self):
        self._stop_queue = True
        self._btn_stop.setEnabled(False)
        self._btn_stop.setText("Stopping…")
        self._log("⏹  Stop requested — finishing current step then stopping.")

    # ── Run single step ───────────────────────────────────────────────────────

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

        # Set step context for file logger
        from core.log_file import FileLogger

        FileLogger.get().set_step(step.STEP_ID)
        FileLogger.get().write_separator(step.LABEL)
        worker = step.make_worker(self._session, config)
        self._worker = worker
        worker.signals.progress.connect(self._log)
        worker.signals.finished.connect(lambda r, s=step: self._done(s, r))
        worker.signals.error.connect(lambda e, s=step: self._error(s, e))
        worker.signals.cancelled.connect(lambda s=step: self._cancelled(s))
        self._pool.start(worker)

    def _update_previews(self, step, result):
        # Refresh Step 6 manifest picker after Step 5 completes
        if step.STEP_ID == "step5_tts" and self._session:
            for s in self._steps:
                if hasattr(s, "populate_manifest_picker"):
                    s.populate_manifest_picker(self._session)
        # Refresh TTS char count whenever step2 finishes
        if step.STEP_ID == "step2_translate" and self._session:
            for s in self._steps:
                if hasattr(s, "update_char_count"):
                    s.update_char_count(self._session)
        if step.STEP_ID == "step1_transcribe" and hasattr(result, "segments"):
            self._subtitle_editor.set_orig_placeholder("")
            self._subtitle_editor._orig_edit.setPlainText(
                "\n".join(f"[{s.start}s–{s.end}s]  {s.text}" for s in result.segments)
            )
        if step.STEP_ID == "step2_translate" and isinstance(result, list):
            lines = []
            for i, s in enumerate(result, 1):
                lines += [
                    str(i),
                    f"[{s.start}s–{s.end}s] {s.original}",
                    s.translated,
                    "",
                ]
            self._subtitle_editor._trans_edit.setPlainText("\n".join(lines))
            self._subtitle_editor._dirty = False
            if self._session:
                self._subtitle_editor.set_session_for_save(self._session)

        if step.STEP_ID == "step7_publish_info" and self._session:
            try:
                self._session = Session.load(str(self._session.folder))
                self._info_editor.load_session(self._session)
            except Exception:
                pass

    def _done(self, step, result):
        card = self._card_for(step)
        self._worker = None
        self._set_busy(False)
        card.set_running(False)
        out_path = result if isinstance(result, str) else ""
        if not out_path and self._session:
            out_path = self._step_output_path(step, self._session)
        card.set_status("✅ Done", "done", out_path)
        self._update_previews(step, result)
        self._status_bar.showMessage(
            f"✅ {step.LABEL} complete"
            + (f" → {Path(out_path).name}" if out_path else "")
        )
        self._persist_step_configs()

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
