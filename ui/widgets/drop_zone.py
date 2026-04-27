"""Reusable drag-and-drop file zone."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import QLabel

SUPPORTED = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".flv",
    ".wmv",
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
    ".aac",
    ".wma",
}


class DropZone(QLabel):
    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._cb = callback
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(68)
        self._idle()

    def _idle(self):
        self.setText("🎬  Drop video / audio here   or   Browse")
        self.setStyleSheet(
            "border:2px dashed #3d3d6e;border-radius:10px;"
            "background:#16213e;color:#555;font-size:13px;padding:14px;"
        )

    def set_file(self, name):
        self.setText(f"🎬  {name}")
        self.setStyleSheet(
            "border:2px solid #5dca8e;border-radius:10px;"
            "background:#0d1f17;color:#5dca8e;font-size:13px;padding:14px;"
        )

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setStyleSheet(
                "border:2px dashed #6c63ff;border-radius:10px;"
                "background:#16213e;color:#a0a8ff;font-size:13px;padding:14px;"
            )

    def dragLeaveEvent(self, e):
        self._idle()

    def dropEvent(self, e: QDropEvent):
        self._idle()
        urls = e.mimeData().urls()
        if urls:
            self._cb(urls[0].toLocalFile())
