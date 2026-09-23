"""全局配置常量。

路径与运行时参数集中在这里，方便 GUI 与服务层共享。
"""
from __future__ import annotations

import os
from pathlib import Path

APP_VERSION = "0.1.0"

# 用户数据目录（凭证、状态、日志）
DATA_HOME: Path = Path(
    os.environ.get("FLYME_EXPORTER_HOME", str(Path.home() / ".flyme-exporter"))
)

CREDENTIALS_FILE: Path = DATA_HOME / "credentials.enc"
SALT_FILE: Path = DATA_HOME / "salt.bin"
STATE_DIR: Path = DATA_HOME / "state"
LOG_DIR: Path = DATA_HOME / "logs"
COOKIES_DIR: Path = DATA_HOME / "cookies"

# 协议配置
PROTOCOL_JSON: Path = Path(__file__).resolve().parent.parent / "docs" / "protocol.json"
PROTOCOL_TEMPLATE: Path = Path(__file__).resolve().parent.parent / "docs" / "protocol.template.json"

# 下载参数
DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 16
MIN_CONCURRENCY = 1
DEFAULT_CHUNK_SIZE = 64 * 1024  # 64KB
MAX_RETRIES = 3

# 风控
COOLDOWN_AFTER_5XX = 60  # 秒

# UI 主题
THEME_LIGHT = "light"
THEME_DARK = "dark"


def ensure_dirs() -> None:
    """确保所有运行时目录已创建。"""
    for d in (DATA_HOME, STATE_DIR, LOG_DIR, COOKIES_DIR):
        d.mkdir(parents=True, exist_ok=True)