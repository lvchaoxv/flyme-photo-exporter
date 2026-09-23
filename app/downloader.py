"""并发下载器。

下载流程（基于抓包数据）：
1. 拉取 session 级的 sig（来自 /file/get_sig/v2）
2. 用 sig + photo.url_path 拼装真实下载 URL
3. 流式下载到本地 + 计算 MD5
4. 与服务端返回的 md5 校验
5. 写入 AlbumState（用于断点续传 / 增量）

API 客户端通过 `fetch_url` 回调注入，避免下载器与 API 实现耦合，
便于测试时用 mock 替换。
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

import aiohttp
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import DEFAULT_CHUNK_SIZE, MAX_RETRIES
from .state import AlbumState, StateManager


@dataclass
class DownloadResult:
    photo_id: str
    path: Path
    bytes_written: int
    md5: str = ""
    skipped: bool = False
    error: str | None = None


ProgressCallback = Callable[[str, int, int], Awaitable[None]]


_RETRYABLE = (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError)


class Downloader:
    """并发下载器。

    使用：
        async with Downloader(state, concurrency=4) as dl:
            results = await dl.download_album(
                album_id="2020-10",
                photos=[...],
                target_dir=Path("~/FlymePhotos/2020-10"),
                fetch_url=lambda p: api.build_download_url(p, sig, protocol),
                progress=cb,
            )
    """

    def __init__(
        self,
        state: StateManager,
        *,
        concurrency: int = 4,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        self.state = state
        self.concurrency = concurrency
        self.chunk_size = chunk_size
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._cancelled = False

    async def __aenter__(self) -> "Downloader":
        self._semaphore = asyncio.Semaphore(self.concurrency)
        return self

    async def __aexit__(self, *exc) -> None:
        self._semaphore = None

    def cancel(self) -> None:
        self._cancelled = True

    async def download_album(
        self,
        album_id: str,
        photos: list[dict],
        target_dir: Path,
        *,
        fetch_url: Callable[[dict], Awaitable[str]],
        progress: Optional[ProgressCallback] = None,
    ) -> list[DownloadResult]:
        """下载一个相册的所有照片。

        增量策略：
          1. 读取 AlbumState.downloaded；命中则跳过（返回 skipped=True）
          2. 已下载但本地文件不存在的，会重下（state.is_downloaded 不依赖文件存在）
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        album_state = self.state.load(album_id) or AlbumState(album_id=album_id)
        sem = self._semaphore or asyncio.Semaphore(self.concurrency)

        tasks = [
            asyncio.create_task(
                self._download_one(
                    album_state, photo, target_dir, fetch_url, progress, sem
                )
            )
            for photo in photos
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self.state.save(album_state)

        out: list[DownloadResult] = []
        for r in results:
            if isinstance(r, DownloadResult):
                out.append(r)
            elif isinstance(r, Exception):
                out.append(
                    DownloadResult(
                        photo_id="<unknown>",
                        path=Path(),
                        bytes_written=0,
                        error=str(r),
                    )
                )
        return out

    async def _download_one(
        self,
        album_state: AlbumState,
        photo: dict,
        target_dir: Path,
        fetch_url: Callable[[dict], Awaitable[str]],
        progress: Optional[ProgressCallback],
        sem: asyncio.Semaphore,
    ) -> DownloadResult:
        photo_id = str(photo.get("photo_id") or photo.get("id") or "")
        if not photo_id:
            return DownloadResult(
                photo_id="<missing-id>",
                path=Path(),
                bytes_written=0,
                error="photo missing id",
            )

        # 已下载且本地文件存在 → 跳过
        if self.state.is_downloaded(album_state, photo_id):
            rec = album_state.downloaded[photo_id]
            local = Path(rec.path)
            if local.exists() and local.stat().st_size == rec.size:
                return DownloadResult(
                    photo_id=photo_id,
                    path=local,
                    bytes_written=rec.size,
                    md5=rec.md5,
                    skipped=True,
                )
            # 文件不存在或大小不一致 → 重新下载

        if self._cancelled:
            return DownloadResult(
                photo_id=photo_id, path=Path(), bytes_written=0, error="cancelled"
            )

        async with sem:
            return await self._do_download(
                album_state, photo, photo_id, target_dir, fetch_url, progress
            )

    @retry(
        reraise=True,
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RETRYABLE),
    )
    async def _do_download(
        self,
        album_state: AlbumState,
        photo: dict,
        photo_id: str,
        target_dir: Path,
        fetch_url: Callable[[dict], Awaitable[str]],
        progress: Optional[ProgressCallback],
    ) -> DownloadResult:
        url = await fetch_url(photo)
        url_path = photo.get("url_path") or photo.get("url", "")
        filename = photo.get("filename", "")
        local_path = target_dir / f"{photo_id}{self._guess_ext(url_path, filename)}"

        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", 0))
                written = 0
                h = hashlib.md5()
                # 先写到临时文件，完成后原子改名
                tmp_path = local_path.with_suffix(local_path.suffix + ".part")
                with tmp_path.open("wb") as f:
                    async for chunk in resp.content.iter_chunked(self.chunk_size):
                        if not chunk:
                            break
                        if self._cancelled:
                            raise asyncio.CancelledError()
                        f.write(chunk)
                        h.update(chunk)
                        written += len(chunk)
                        if progress:
                            await progress(photo_id, written, total)
                tmp_path.replace(local_path)

        local_md5 = h.hexdigest()
        # 服务端 md5 形如 "b2e2fe9d1dd49d66ddd4718014464ac1_4674...s3014760u..."
        # 第一段（_前）是真正的 md5
        server_md5 = (photo.get("md5") or "").split("_", 1)[0]
        if server_md5 and local_md5 != server_md5:
            self.state.mark_failed(album_state, photo_id)
            return DownloadResult(
                photo_id=photo_id,
                path=local_path,
                bytes_written=written,
                md5=local_md5,
                error=f"md5 mismatch: server={server_md5[:8]}… local={local_md5[:8]}…",
            )

        self.state.mark_downloaded(
            album_state, photo_id, written, local_md5, str(local_path)
        )
        return DownloadResult(
            photo_id=photo_id,
            path=local_path,
            bytes_written=written,
            md5=local_md5,
        )

    @staticmethod
    def _guess_ext(url_path: str, filename: str) -> str:
        for src in (url_path, filename):
            if "." in src:
                ext = "." + src.rsplit(".", 1)[-1].lower()
                if ext in (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".heic", ".gif"):
                    return ext
        return ".jpg"