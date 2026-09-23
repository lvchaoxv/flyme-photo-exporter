"""QThread 异步 worker：桥接业务协程与 GUI 线程。"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal


class AsyncWorker(QObject):
    """把任意 async 函数丢到后台线程执行。"""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)  # current, total

    def __init__(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
    ) -> None:
        super().__init__()
        self._coro_factory = coro_factory

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self._coro_factory())
            self.finished.emit(result)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
        finally:
            loop.close()


def run_async_in_thread(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    on_finished: Callable[[Any], None],
    on_failed: Callable[[str], None],
    parent: Optional[QObject] = None,
) -> QThread:
    """启动后台线程运行协程，绑定信号回调。

    返回的 QThread 由调用方持有，并在收到 finished 后 quit。
    """
    thread = QThread(parent)
    worker = AsyncWorker(coro_factory)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(on_finished)
    worker.failed.connect(on_failed)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread