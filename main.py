import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

try:
    from core.ffmpeg_utils import init_bundled_tools_path

    init_bundled_tools_path()
except Exception:
    pass

from PyQt6.QtWidgets import QApplication, QMessageBox


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SubSync")
    try:
        from ui.main_window import MainWindow
    except Exception as e:
        m = QMessageBox()
        m.setWindowTitle("Import Error")
        m.setText(f"Failed to start:\n\n{e}")
        m.exec()
        sys.exit(1)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
