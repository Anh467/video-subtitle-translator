"""Shared Qt stylesheet for SubSync."""

STYLESHEET = """
QMainWindow,QWidget{background:#1a1a2e;color:#e0e0e0;
font-family:'SF Pro Display','Segoe UI',Arial,sans-serif;font-size:13px;}
QPushButton{background:#2d2d4e;color:#e0e0e0;border:1px solid #3d3d6e;
border-radius:6px;padding:5px 12px;}
QPushButton:hover{background:#3d3d6e;border-color:#6c63ff;}
QPushButton:disabled{color:#444;background:#1e1e38;border-color:#252540;}
QPushButton#cancel_btn{background:#3a1a1a;color:#ff7070;border:1px solid #6e2d2d;
font-weight:bold;padding:7px 14px;}
QPushButton#cancel_btn:hover{background:#5a2020;}
QLineEdit{background:#16213e;border:1px solid #2d2d4e;border-radius:5px;
padding:5px 10px;color:#e0e0e0;}
QLineEdit:focus{border-color:#6c63ff;}
QLineEdit:read-only{color:#aaa;background:#111828;}
QComboBox{background:#16213e;border:1px solid #2d2d4e;border-radius:5px;
padding:4px 10px;color:#e0e0e0;}
QComboBox:hover{border-color:#6c63ff;}
QComboBox QAbstractItemView{background:#16213e;border:1px solid #6c63ff;
color:#e0e0e0;selection-background-color:#6c63ff;}
QComboBox::drop-down{border:none;}
QTextEdit{background:#0f0f23;border:1px solid #2d2d4e;border-radius:6px;
padding:8px;color:#d0d0d0;font-family:'SF Mono','Consolas',monospace;font-size:12px;}
QProgressBar{border:1px solid #2d2d4e;border-radius:4px;background:#16213e;
text-align:center;color:white;height:16px;}
QProgressBar::chunk{background:#6c63ff;border-radius:3px;}
QStatusBar{background:#0f0f23;border-top:1px solid #2d2d4e;color:#666;}
QSplitter::handle{background:#2d2d4e;}
QScrollArea{border:none;background:transparent;}
QCheckBox{spacing:6px;}
QCheckBox::indicator{width:14px;height:14px;border:1px solid #3d3d6e;
border-radius:3px;background:#16213e;}
QCheckBox::indicator:checked{background:#6c63ff;border-color:#6c63ff;}
QRadioButton{spacing:6px;}
QRadioButton::indicator{width:14px;height:14px;border-radius:7px;
border:1px solid #3d3d6e;background:#16213e;}
QRadioButton::indicator:checked{background:#6c63ff;border-color:#6c63ff;}
QSpinBox{background:#16213e;border:1px solid #2d2d4e;border-radius:5px;
padding:4px 6px;color:#e0e0e0;min-width:70px;}
QSpinBox:focus{border-color:#6c63ff;}
QSpinBox::up-button,QSpinBox::down-button{width:18px;background:#2d2d4e;
border:none;border-radius:3px;}
QSpinBox::up-button:hover,QSpinBox::down-button:hover{background:#3d3d6e;}
QFrame#session_bar{background:#111828;border:1px solid #2d2d4e;border-radius:6px;}
QSlider::groove:horizontal{height:4px;background:#2d2d4e;border-radius:2px;}
QSlider::handle:horizontal{width:14px;height:14px;margin:-5px 0;
background:#6c63ff;border-radius:7px;}
QListWidget{background:#111828;border:1px solid #2d2d4e;border-radius:6px;
color:#e0e0e0;outline:none;}
QListWidget::item{padding:8px 12px;border-bottom:1px solid #1e1e38;}
QListWidget::item:selected{background:#2d2d4e;border-left:3px solid #6c63ff;}
QListWidget::item:hover{background:#1e1e38;}
QDoubleSpinBox{background:#16213e;border:1px solid #2d2d4e;border-radius:5px;
padding:4px 6px;color:#e0e0e0;min-width:70px;}
QDoubleSpinBox:focus{border-color:#6c63ff;}
"""
