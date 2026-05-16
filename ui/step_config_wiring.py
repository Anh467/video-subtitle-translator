"""Connect step config widgets to workspace autosave callbacks."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QRadioButton,
    QSlider,
    QSpinBox,
    QTextEdit,
    QWidget,
)

_WIRED_PROP = "_subsync_config_wired"


def wire_widget_tree(root: QWidget | None, on_change: Callable[[], None]) -> None:
    """Attach on_change to common controls under root (once per widget)."""
    if root is None:
        return

    widgets = [root]
    widgets.extend(root.findChildren(QWidget))

    for w in widgets:
        if w.property(_WIRED_PROP):
            continue

        hooked = False
        if isinstance(w, QComboBox):
            w.currentIndexChanged.connect(lambda *_: on_change())
            w.currentTextChanged.connect(lambda *_: on_change())
            hooked = True
        elif isinstance(w, QLineEdit):
            w.textChanged.connect(lambda *_: on_change())
            hooked = True
        elif isinstance(w, QTextEdit):
            w.textChanged.connect(lambda *_: on_change())
            hooked = True
        elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
            w.valueChanged.connect(lambda *_: on_change())
            hooked = True
        elif isinstance(w, QSlider):
            w.valueChanged.connect(lambda *_: on_change())
            hooked = True
        elif isinstance(w, QCheckBox):
            w.stateChanged.connect(lambda *_: on_change())
            hooked = True
        elif isinstance(w, QRadioButton):
            w.toggled.connect(lambda *_: on_change())
            hooked = True

        if hooked:
            w.setProperty(_WIRED_PROP, True)
