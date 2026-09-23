"""QSS 样式：魅族蓝主题，浅色为主，预留深色扩展。"""
from __future__ import annotations

from PyQt6.QtWidgets import QApplication


QSS_LIGHT = """
QWidget {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
    color: #2C3E50;
    background: #F5F7FA;
}

QMainWindow, QDialog {
    background: #FFFFFF;
}

QLineEdit, QTextEdit, QPlainTextEdit {
    border: 1px solid #DCDFE6;
    border-radius: 6px;
    padding: 6px 10px;
    background: #FFFFFF;
    selection-background-color: #1E88E5;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border: 1px solid #1E88E5;
}

QPushButton {
    background: #1E88E5;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    padding: 8px 18px;
    font-weight: 500;
}

QPushButton:hover {
    background: #1976D2;
}

QPushButton:pressed {
    background: #0D47A1;
}

QPushButton:disabled {
    background: #C0C4CC;
}

QPushButton[flat="true"] {
    background: transparent;
    color: #1E88E5;
    padding: 6px 12px;
}

QPushButton[flat="true"]:hover {
    background: rgba(30, 136, 229, 0.08);
}

QProgressBar {
    border: 1px solid #DCDFE6;
    border-radius: 6px;
    background: #F5F7FA;
    text-align: center;
    height: 18px;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1E88E5, stop:1 #42A5F5);
    border-radius: 5px;
}

QTabBar::tab {
    background: transparent;
    color: #5A6B7B;
    padding: 10px 22px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}

QTabBar::tab:selected {
    color: #1E88E5;
    background: #FFFFFF;
    border-bottom: 2px solid #1E88E5;
}

QTabWidget::pane {
    border: none;
    background: #FFFFFF;
}

QListWidget, QTreeView, QTableView {
    background: #FFFFFF;
    border: 1px solid #EBEEF5;
    border-radius: 6px;
    alternate-background-color: #FAFAFA;
    gridline-color: #EBEEF5;
}

QListWidget::item:selected,
QTreeView::item:selected,
QTableView::item:selected {
    background: rgba(30, 136, 229, 0.10);
    color: #1E88E5;
}

QLabel#title {
    font-size: 22px;
    font-weight: 600;
    color: #0D47A1;
}

QLabel#subtitle {
    font-size: 14px;
    color: #5A6B7B;
}

QLabel#status {
    color: #67C23A;
}

QLabel#error {
    color: #F56C6C;
}

QGroupBox {
    border: 1px solid #EBEEF5;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 16px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: #1E88E5;
}

QSpinBox, QComboBox {
    border: 1px solid #DCDFE6;
    border-radius: 6px;
    padding: 4px 8px;
    background: #FFFFFF;
}

QSpinBox:focus, QComboBox:focus {
    border: 1px solid #1E88E5;
}
"""


def apply_app_style(app: QApplication) -> None:
    app.setStyleSheet(QSS_LIGHT)