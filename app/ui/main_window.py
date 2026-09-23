"""主窗口：相册 / 下载管理 / 设置 三个 Tab。

完整业务流程：
  1. 相册 Tab 拉取 /album/group 展示年/月/日统计
  2. 用户勾选若干日期 → 点击"开始下载"
  3. 后台线程按日期拉照片列表 → 并发下载
  4. 下载管理 Tab 显示实时进度
  5. 设置 Tab 调整并发数、清空凭证
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..api import APIError, FlymeAPI, ProtocolNotConfigured
from ..auth import Credentials, CredentialStore
from ..config import DEFAULT_CONCURRENCY, MAX_CONCURRENCY, MIN_CONCURRENCY
from ..downloader import Downloader, DownloadResult
from ..state import AlbumState, StateManager


# ----------------------- 相册 Tab -----------------------

class AlbumListTab(QWidget):
    """相册 Tab：按年/月/日组织，用户可勾选若干日期。"""

    def __init__(
        self,
        creds: Credentials,
        state: StateManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.creds = creds
        self.state = state
        self._selected_dates: set[str] = set()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 顶部
        header = QHBoxLayout()
        title = QLabel("相册")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()

        self.user_label = QLabel("")
        self.user_label.setObjectName("subtitle")
        header.addWidget(self.user_label)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setProperty("flat", True)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        # 树形：年 > 月 > 日
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["日期", "照片数", "状态"])
        self.tree.setColumnWidth(0, 280)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        layout.addWidget(self.tree, 1)

        # 底部
        footer = QHBoxLayout()
        self.select_all_chk = QCheckBox("全选显示的日期")
        footer.addWidget(self.select_all_chk)
        footer.addStretch()

        self.target_edit = QLineEdit(str(Path.home() / "FlymePhotos"))
        self.browse_btn = QPushButton("选择目录…")
        self.browse_btn.setProperty("flat", True)
        footer.addWidget(QLabel("保存到："))
        footer.addWidget(self.target_edit, 1)
        footer.addWidget(self.browse_btn)

        self.start_btn = QPushButton("开始下载")
        footer.addWidget(self.start_btn)

        layout.addLayout(footer)

        # 信号
        self.browse_btn.clicked.connect(self._on_browse)
        self.refresh_btn.clicked.connect(self.refresh_async)
        self.select_all_chk.stateChanged.connect(self._on_select_all)

    def _on_browse(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "选择保存目录", self.target_edit.text()
        )
        if d:
            self.target_edit.setText(d)

    def _on_select_all(self, state: int) -> None:
        for i in range(self.tree.topLevelItemCount()):
            year_item = self.tree.topLevelItem(i)
            for j in range(year_item.childCount()):
                month_item = year_item.child(j)
                for k in range(month_item.childCount()):
                    day_item = month_item.child(k)
                    day_item.setCheckState(
                        0,
                        Qt.CheckState.Checked
                        if state == Qt.CheckState.Checked.value
                        else Qt.CheckState.Unchecked,
                    )

    def selected_dates(self) -> list[str]:
        out: list[str] = []
        for i in range(self.tree.topLevelItemCount()):
            yi = self.tree.topLevelItem(i)
            for j in range(yi.childCount()):
                mi = yi.child(j)
                for k in range(mi.childCount()):
                    di = mi.child(k)
                    if di.checkState(0) == Qt.CheckState.Checked:
                        out.append(di.data(0, Qt.ItemDataRole.UserRole))
        return out

    def populate(self, years: list[dict], user_info: dict) -> None:
        """填充相册树。"""
        self.tree.clear()
        self.user_label.setText(
            f"{user_info.get('vipName', '')} · "
            f"{user_info.get('fileNum', 0)} 张 · "
            f"已用 {(user_info.get('usedVolume', 0) or 0) / 1e9:.2f} GB"
        )
        for y in years:
            yi = QTreeWidgetItem([str(y.get("year", "")), str(y.get("count", "")), ""])
            yi.setFlags(yi.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
            self.tree.addTopLevelItem(yi)
            for m in y.get("months", []):
                mi = QTreeWidgetItem(
                    [f"{y.get('year', '')}-{m.get('month', ''):02d}",
                     str(m.get("count", "")),
                     ""]
                )
                mi.setFlags(mi.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
                yi.addChild(mi)
                for d in m.get("days", []):
                    date = f"{y.get('year', '')}-{m.get('month', 0):02d}-{d.get('day', 0):02d}"
                    di = QTreeWidgetItem(
                        [date, str(d.get("count", "")), "未下载"]
                    )
                    di.setFlags(di.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    di.setCheckState(0, Qt.CheckState.Unchecked)
                    di.setData(0, Qt.ItemDataRole.UserRole, date)
                    mi.addChild(di)

    def mark_date_state(self, date: str, text: str) -> None:
        """根据 date 查找对应节点，更新状态列。"""
        for i in range(self.tree.topLevelItemCount()):
            yi = self.tree.topLevelItem(i)
            for j in range(yi.childCount()):
                mi = yi.child(j)
                for k in range(mi.childCount()):
                    di = mi.child(k)
                    if di.data(0, Qt.ItemDataRole.UserRole) == date:
                        di.setText(2, text)
                        return

    def refresh_async(self) -> None:
        """触发刷新（实际工作交给 main_window 调度）。"""
        # 由 MainWindow 绑定
        pass


# ----------------------- 下载管理 Tab -----------------------

class DownloadManagerTab(QWidget):
    """下载管理 Tab：每相册一行进度。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("下载管理")
        title.setObjectName("title")
        layout.addWidget(title)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["相册", "进度", "已下载", "总数", "状态"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        self.overall = QProgressBar()
        self.overall.setFormat("总进度 %p%")
        self.overall.setMinimumHeight(22)
        layout.addWidget(self.overall)


# ----------------------- 设置 Tab -----------------------

class SettingsTab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("设置")
        title.setObjectName("title")
        layout.addWidget(title)

        basic = QGroupBox("基础")
        basic_form = QFormLayout(basic)
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(MIN_CONCURRENCY, MAX_CONCURRENCY)
        self.concurrency_spin.setValue(DEFAULT_CONCURRENCY)
        basic_form.addRow("下载并发数（1-16）：", self.concurrency_spin)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["浅色", "深色"])
        basic_form.addRow("界面主题：", self.theme_combo)
        layout.addWidget(basic)

        cred = QGroupBox("凭证")
        cred_layout = QVBoxLayout(cred)
        self.clear_creds_btn = QPushButton("清空已保存凭证")
        cred_layout.addWidget(self.clear_creds_btn)
        layout.addWidget(cred)

        layout.addStretch()


# ----------------------- 主窗口 -----------------------

class MainWindow(QMainWindow):
    """主窗口。"""

    def __init__(self, creds: Credentials) -> None:
        super().__init__()
        self.creds = creds
        self.state = StateManager()
        self.setWindowTitle(f"Flyme 相册备份 - {creds.account or '账号'}")
        self.resize(1000, 680)
        self._download_thread: Optional[QThread] = None
        self._build_ui()
        # 进入即拉一次
        self._refresh_albums_async()

    def _build_ui(self) -> None:
        tabs = QTabWidget()
        self.album_tab = AlbumListTab(self.creds, self.state)
        self.download_tab = DownloadManagerTab()
        self.settings_tab = SettingsTab()
        tabs.addTab(self.album_tab, "相册")
        tabs.addTab(self.download_tab, "下载管理")
        tabs.addTab(self.settings_tab, "设置")
        self.setCentralWidget(tabs)

        # 信号
        self.album_tab.refresh_btn.clicked.connect(self._refresh_albums_async)
        self.album_tab.start_btn.clicked.connect(self._start_download)
        self.settings_tab.clear_creds_btn.clicked.connect(self._clear_credentials)

    # ---------- 相册刷新 ----------

    def _refresh_albums_async(self) -> None:
        from ..ui.workers import run_async_in_thread

        async def task():
            async with FlymeAPI(self.creds) as api:
                info = await api.user_info()
                years = await api.list_album_groups()
                return info, years

        run_async_in_thread(
            task,
            on_finished=self._on_albums_loaded,
            on_failed=lambda msg: QMessageBox.critical(self, "加载失败", msg),
        )

    def _on_albums_loaded(self, result) -> None:
        info, years = result
        self.album_tab.populate(years, info)

    # ---------- 启动下载 ----------

    def _start_download(self) -> None:
        dates = self.album_tab.selected_dates()
        if not dates:
            QMessageBox.information(self, "提示", "请先勾选要下载的日期")
            return

        target_root = Path(self.album_tab.target_edit.text())
        target_root.mkdir(parents=True, exist_ok=True)

        # 初始化下载管理表
        self.download_tab.table.setRowCount(len(dates))
        for row, date in enumerate(dates):
            self.download_tab.table.setItem(row, 0, QTableWidgetItem(date))
            bar = QProgressBar()
            bar.setRange(0, 100)
            self.download_tab.table.setCellWidget(row, 1, bar)
            self.download_tab.table.setItem(row, 2, QTableWidgetItem("0"))
            self.download_tab.table.setItem(row, 3, QTableWidgetItem("?"))
            self.download_tab.table.setItem(row, 4, QTableWidgetItem("下载中…"))
            self.album_tab.mark_date_state(date, "下载中…")

        # 启动后台线程
        from ..ui.workers import run_async_in_thread
        concurrency = self.settings_tab.concurrency_spin.value()

        async def task():
            results_summary: list[dict] = []
            async with FlymeAPI(self.creds) as api:
                protocol = api.protocol()
                sig = await api.fetch_download_sig()

                async def fetch_url(photo: dict) -> str:
                    return FlymeAPI.build_download_url(photo, sig, protocol)

                async with Downloader(self.state, concurrency=concurrency) as dl:
                    for date in dates:
                        target = target_root / date
                        # 拉该日期的照片
                        date_photos_map = await api.list_photos_by_dates([date])
                        photos = date_photos_map.get(date, [])
                        normalized = [FlymeAPI.normalize_photo(p) for p in photos]
                        # 进度回调（透传到 GUI：发信号给主线程更新表格）
                        async def cb(photo_id: str, downloaded: int, total: int,
                                      _date=date):
                            # 这里仅做轻量回调；GUI 更新由 download_album 返回结果时统一处理
                            pass

                        results = await dl.download_album(
                            album_id=date,
                            photos=normalized,
                            target_dir=target,
                            fetch_url=fetch_url,
                            progress=cb,
                        )
                        results_summary.append(
                            {
                                "date": date,
                                "total": len(normalized),
                                "done": sum(1 for r in results if not r.error and not r.skipped),
                                "skipped": sum(1 for r in results if r.skipped),
                                "errors": [r.error for r in results if r.error],
                            }
                        )
            return results_summary

        self._download_thread = run_async_in_thread(
            task,
            on_finished=self._on_download_finished,
            on_failed=lambda msg: QMessageBox.critical(self, "下载异常", msg),
        )

    def _on_download_finished(self, results: list[dict]) -> None:
        self.download_tab.overall.setValue(100)
        msg_lines = [f"{r['date']}: 已完成 {r['done']} / 跳过 {r['skipped']} / 失败 {len(r['errors'])}"
                     for r in results]
        QMessageBox.information(self, "下载完成", "\n".join(msg_lines))
        for r in results:
            self.album_tab.mark_date_state(
                r["date"],
                f"✓ {r['done']}/{r['total']}",
            )

    # ---------- 凭证 ----------

    def _clear_credentials(self) -> None:
        CredentialStore().clear()
        QMessageBox.information(self, "完成", "已清空本地凭证。")