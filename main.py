"""Flyme Photo Exporter GUI 入口。

使用：
    python main.py
"""
from __future__ import annotations

import sys

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from app.config import APP_VERSION
from app.ui.login_window import LoginWindow
from app.ui.main_window import MainWindow
from app.ui.styles import apply_app_style


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Flyme Photo Exporter")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("flyme-exporter")

    font = QFont("Microsoft YaHei UI", 10)
    app.setFont(font)
    apply_app_style(app)

    # 登录成功后切换
    main_window: MainWindow | None = None

    def on_logged_in(creds) -> None:
        nonlocal main_window
        login.close()
        main_window = MainWindow(creds)
        main_window.show()

    login = LoginWindow()
    login.logged_in.connect(on_logged_in)
    login.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())