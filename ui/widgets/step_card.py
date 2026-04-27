"""
StepCard — reusable scrollable card for any pipeline step.

Usage:
    card = StepCard(step_instance)
    card.on_run = lambda: ...    # called when Run button clicked
    layout.addWidget(card)
"""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

STATE_COLORS = {
    "idle": "#555555",
    "running": "#a0a8ff",
    "done": "#5dca8e",
    "error": "#ff7070",
    "loaded": "#ffaa55",
    "skipped": "#444466",
}


class StepCard(QWidget):
    def __init__(self, step, parent=None):
        super().__init__(parent)
        self.step = step
        self.on_run = None  # callable — set by MainWindow

        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── Header row: checkbox + label ──
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 4)
        self._enable_chk = QCheckBox(self.step.LABEL)
        self._enable_chk.setChecked(self.step.ENABLED_BY_DEFAULT)
        self._enable_chk.setStyleSheet(
            f"font-size:13px;font-weight:600;color:{self.step.COLOR};"
        )
        self._enable_chk.stateChanged.connect(self._on_toggle)
        hdr.addWidget(self._enable_chk)
        hdr.addStretch()
        outer.addLayout(hdr)

        # ── Card frame ──
        self._frame = QFrame()
        self._frame.setStyleSheet(
            "QFrame{border:1px solid #2d2d4e;border-radius:8px;" "background:#131324;}"
        )
        frame_v = QVBoxLayout(self._frame)
        frame_v.setContentsMargins(12, 10, 12, 10)
        frame_v.setSpacing(8)
        frame_v.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Config widget (scrollable)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent;")
        scroll.setMinimumHeight(60)
        scroll.setMaximumHeight(180)

        config_w = self.step.build_config_widget()
        config_w.setSizePolicy(
            config_w.sizePolicy().horizontalPolicy(),
            __import__(
                "PyQt6.QtWidgets", fromlist=["QSizePolicy"]
            ).QSizePolicy.Policy.Preferred,
        )
        scroll.setWidget(config_w)
        frame_v.addWidget(scroll, stretch=0)

        # Run button
        self._run_btn = QPushButton(f"▶  Run {self.step.LABEL}")
        self._run_btn.setStyleSheet(
            f"QPushButton{{background:{self.step.COLOR};color:white;"
            f"font-weight:bold;border:none;border-radius:6px;padding:7px 16px;}}"
            f"QPushButton:hover{{background:{self._darken(self.step.COLOR)};}}"
            f"QPushButton:disabled{{background:#2a2a4a;color:#555;}}"
        )
        self._run_btn.clicked.connect(self._clicked_run)
        frame_v.addWidget(self._run_btn)

        # Status + file label
        self._status_lbl = QLabel("Waiting…")
        self._status_lbl.setStyleSheet(f"color:{STATE_COLORS['idle']};font-size:11px;")
        frame_v.addWidget(self._status_lbl)

        self._file_lbl = QLabel("")
        self._file_lbl.setStyleSheet(
            "color:#3a6a5a;font-size:10px;"
            "font-family:'SF Mono','Consolas',monospace;"
        )
        self._file_lbl.setWordWrap(True)
        frame_v.addWidget(self._file_lbl)

        outer.addWidget(self._frame)
        self._on_toggle()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_status(self, text: str, state: str = "idle", file_path: str = ""):
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(
            f"color:{STATE_COLORS.get(state,'#e0e0e0')};font-size:11px;"
        )
        if file_path:
            self._file_lbl.setText(f"→ {Path(file_path).name}")
        else:
            self._file_lbl.setText("")

    def set_running(self, busy: bool):
        self._run_btn.setEnabled(not busy)
        self._run_btn.setText("⏳ Running…" if busy else f"▶  Run {self.step.LABEL}")

    def is_enabled(self) -> bool:
        return self._enable_chk.isChecked()

    def reset(self):
        self.set_status("Waiting…", "idle")
        self.set_running(False)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _clicked_run(self):
        if self.on_run:
            self.on_run()

    def _on_toggle(self):
        enabled = self._enable_chk.isChecked()
        self._frame.setEnabled(enabled)
        self._frame.setStyleSheet(
            f"QFrame{{border:1px solid {'#3d3d6e' if enabled else '#1e1e38'};"
            f"border-radius:8px;"
            f"background:{'#131324' if enabled else '#0e0e1e'};}}"
        )

    @staticmethod
    def _darken(hex_color: str) -> str:
        """Darken a hex color by ~15%."""
        c = hex_color.lstrip("#")
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        r, g, b = max(0, r - 40), max(0, g - 40), max(0, b - 40)
        return f"#{r:02x}{g:02x}{b:02x}"
