"""登录窗口：账号 + Token 输入。

由于 Flyme 登录页含滑块验证码，工具不实现自动登录。
用户在浏览器完成登录（含滑块）后，从任一 mzstorage 接口的
form body 中复制 `token=...` 粘贴到本工具即可。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..api import FlymeAPI, ProtocolNotConfigured
from ..auth import CredentialStore, Credentials


HELP_TEXT = """\
使用步骤：
  1. 在浏览器中打开 https://photos.flyme.cn/photo/index 并完成登录（含滑块）
  2. 按 F12 打开开发者工具 → Network 面板
  3. 找到任一 mzstorage.meizu.com 的请求（如 album/group）
  4. 在 Payload / Form Data 中复制 token= 后面的完整字符串
  5. 粘贴到下方 Token 输入框

提示：token 通常长达数百字符，包含字母数字与特殊字符。
"""


class LoginWindow(QMainWindow):
    logged_in = pyqtSignal(Credentials)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Flyme 相册备份 - 登录")
        self.setFixedSize(540, 540)
        self.creds: Optional[Credentials] = None
        self.store = CredentialStore()
        self._build_ui()
        self._try_auto_fill()

    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)

        title = QLabel("Flyme 相册备份")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("粘贴 Flyme Token 以连接你的相册")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 输入区
        self.account_edit = QLineEdit()
        self.account_edit.setPlaceholderText("账号（仅显示用）")
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("Token（从浏览器请求 body 中复制）")
        # 等宽字体方便看清 token
        mono = QFont("Consolas", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.token_edit.setFont(mono)

        form_layout = QVBoxLayout()
        form_layout.setSpacing(6)
        form_layout.addWidget(QLabel("账号："))
        form_layout.addWidget(self.account_edit)
        form_layout.addWidget(QLabel("Token："))
        form_layout.addWidget(self.token_edit)

        # 帮助说明
        help_label = QLabel("如何获取 Token：")
        help_label.setObjectName("subtitle")
        self.help_text = QTextEdit()
        self.help_text.setReadOnly(True)
        self.help_text.setPlainText(HELP_TEXT)
        self.help_text.setMaximumHeight(180)

        self.status_label = QLabel("")
        self.status_label.setObjectName("status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setMinimumHeight(20)

        self.login_btn = QPushButton("登 录")
        self.login_btn.setFixedHeight(38)
        self.login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_btn.clicked.connect(self._on_login)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(4)
        layout.addLayout(form_layout)
        layout.addWidget(help_label)
        layout.addWidget(self.help_text)
        layout.addWidget(self.status_label)
        layout.addWidget(self.login_btn)

        self.setCentralWidget(central)

    def _try_auto_fill(self) -> None:
        saved = self.store.load()
        if saved:
            self.account_edit.setText(saved.account)
            self.token_edit.setText(saved.token)

    def _on_login(self) -> None:
        account = self.account_edit.text().strip()
        token = self.token_edit.text().strip()
        if not token:
            self._set_status("Token 不能为空", error=True)
            return
        if len(token) < 200:
            self._set_status(
                f"Token 长度异常（{len(token)} 字符），请确认是否复制完整",
                error=True,
            )

        self.login_btn.setEnabled(False)
        self._set_status("校验协议配置…")

        creds = Credentials(account=account, token=token)
        try:
            FlymeAPI(creds)  # 仅触发协议配置校验
        except ProtocolNotConfigured as e:
            QMessageBox.warning(self, "协议未配置", str(e))
            self._set_status("请补全 protocol.json 后重试", error=True)
            self.login_btn.setEnabled(True)
            return

        self.store.save(creds)
        self.creds = creds
        self._set_status("登录成功")
        self.logged_in.emit(creds)
        self.close()

    def _set_status(self, msg: str, error: bool = False) -> None:
        self.status_label.setText(msg)
        self.status_label.setObjectName("error" if error else "status")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)