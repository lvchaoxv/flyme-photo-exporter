"""增量状态管理：每相册一份 JSON 状态，记录 last_modified_at 与已下载文件清单。

状态文件位于 ~/.flyme-exporter/state/{album_id}.json：
{
  "album_id": "...",
  "album_name": "...",
  "last_modified_at": 1716000000000,
  "downloaded": {
     "<photo_id>": {"photo_id": "...", "size": 12345, "md5": "...", "path": "..."}
  },
  "total": 1000,
  "failed": []
}
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .config import STATE_DIR, ensure_dirs


@dataclass
class PhotoRecord:
    photo_id: str
    size: int = 0
    md5: str = ""
    path: str = ""


@dataclass
class AlbumState:
    album_id: str
    album_name: str = ""
    last_modified_at: int = 0
    downloaded: dict[str, PhotoRecord] = field(default_factory=dict)
    total: int = 0
    failed: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "album_id": self.album_id,
            "album_name": self.album_name,
            "last_modified_at": self.last_modified_at,
            "total": self.total,
            "failed": self.failed,
            "downloaded": {
                pid: asdict(rec) for pid, rec in self.downloaded.items()
            },
        }

    @classmethod
    def from_json(cls, data: dict) -> "AlbumState":
        downloaded = {
            pid: PhotoRecord(**rec) for pid, rec in data.get("downloaded", {}).items()
        }
        return cls(
            album_id=data["album_id"],
            album_name=data.get("album_name", ""),
            last_modified_at=data.get("last_modified_at", 0),
            downloaded=downloaded,
            total=data.get("total", 0),
            failed=data.get("failed", []),
        )

    def progress(self) -> tuple[int, int]:
        """返回 (已下载, 总数)。"""
        done = sum(1 for r in self.downloaded.values() if r.size > 0)
        return done, self.total


class StateManager:
    """相册状态读写。"""

    def __init__(self, state_dir: Path = STATE_DIR) -> None:
        ensure_dirs()
        self.state_dir = state_dir

    def _path(self, album_id: str) -> Path:
        safe_id = "".join(c for c in album_id if c.isalnum() or c in "-_")
        return self.state_dir / f"{safe_id}.json"

    def load(self, album_id: str) -> Optional[AlbumState]:
        path = self._path(album_id)
        if not path.exists():
            return None
        try:
            return AlbumState.from_json(json.loads(path.read_text("utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def save(self, state: AlbumState) -> None:
        path = self._path(state.album_id)
        # 原子替换，避免崩溃产生残缺文件
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(state.to_json(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def list_all(self) -> list[AlbumState]:
        return [
            AlbumState.from_json(json.loads(p.read_text("utf-8")))
            for p in self.state_dir.glob("*.json")
            if not p.name.endswith(".tmp")
        ]

    def is_downloaded(self, state: AlbumState, photo_id: str) -> bool:
        return photo_id in state.downloaded

    def mark_downloaded(
        self,
        state: AlbumState,
        photo_id: str,
        size: int,
        md5: str,
        path: str,
    ) -> None:
        state.downloaded[photo_id] = PhotoRecord(
            photo_id=photo_id, size=size, md5=md5, path=path
        )
        self.save(state)

    def mark_failed(self, state: AlbumState, photo_id: str) -> None:
        if photo_id not in state.failed:
            state.failed.append(photo_id)
        self.save(state)

    def update_meta(
        self,
        state: AlbumState,
        *,
        album_name: str | None = None,
        last_modified_at: int | None = None,
        total: int | None = None,
    ) -> None:
        if album_name is not None:
            state.album_name = album_name
        if last_modified_at is not None:
            state.last_modified_at = max(state.last_modified_at, last_modified_at)
        if total is not None:
            state.total = total
        self.save(state)


def file_md5(path: Path, chunk: int = 64 * 1024) -> str:
    """计算本地文件 MD5，用于完整性校验。"""
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()