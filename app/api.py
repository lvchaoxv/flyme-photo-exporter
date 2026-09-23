"""Flyme 相册 API 客户端。

实际接口域名：mzstorage.meizu.com（与前端页面 photos.flyme.cn 不同源，
通过 CORS 跨域请求访问）。

每个请求都是 form-urlencoded POST，包含：
    业务字段 + cts(毫秒时间戳) + token + sign

sign 算法在 docs/protocol.json.sign 中配置。
"""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .auth import Credentials
from .config import PROTOCOL_JSON
from .sign import compute_sign


class ProtocolNotConfigured(Exception):
    """协议未配置或密钥未填。"""


class APIError(Exception):
    """服务端业务错误。"""

    def __init__(self, code: int, message: str, value: Any = None) -> None:
        super().__init__(f"API {code}: {message}")
        self.code = code
        self.message = message
        self.value = value


class FlymeAPI:
    """Flyme 相册 API 客户端。

    使用：
        api = FlymeAPI(creds)
        async with api:
            info = await api.user_info()
            years = await api.list_album_groups()
            photos = await api.list_photos_by_dates(["2020-10-04"])
            sig = await api.fetch_download_sig()
    """

    def __init__(self, creds: Credentials) -> None:
        self.creds = creds
        self._protocol: dict = self._load_protocol()
        self._base_url: str = self._protocol.get(
            "base_url", "https://mzstorage.meizu.com"
        ).rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    def _load_protocol(self) -> dict:
        if not PROTOCOL_JSON.exists():
            raise ProtocolNotConfigured(
                f"未找到协议配置：{PROTOCOL_JSON}\n"
                f"请按 docs/protocol.md 指引完成抓包。"
            )
        try:
            data = json.loads(PROTOCOL_JSON.read_text("utf-8"))
        except json.JSONDecodeError as e:
            raise ProtocolNotConfigured(f"protocol.json 解析失败：{e}")

        sign_cfg = data.get("sign", {})
        secret = sign_cfg.get("key_or_secret", "")
        if not secret or secret.startswith("<PLACEHOLDER"):
            raise ProtocolNotConfigured(
                "protocol.json 的 sign.key_or_secret 未填。\n\n"
                "请按以下步骤获取签名密钥：\n"
                "  1. 在浏览器中打开 https://photos.flyme.cn/photo/index 并登录\n"
                "  2. 按 F12 → Sources 面板，搜索 'sign' 关键字\n"
                "  3. 找到生成 sign 的函数与密钥字符串\n"
                "  4. 把密钥填到 docs/protocol.json 的 sign.key_or_secret 字段\n\n"
                "填好后重启工具即可。"
            )
        return data

    async def __aenter__(self) -> "FlymeAPI":
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("FlymeAPI 必须通过 `async with` 使用")
        return self._session

    def base_url(self) -> str:
        return self._base_url

    # ---------- 核心请求 ----------

    def _build_headers(self) -> dict[str, str]:
        common = self._protocol.get("common", {})
        headers = dict(common.get("headers", {}))
        if self.creds.cookies:
            headers["Cookie"] = "; ".join(
                f"{k}={v}" for k, v in self.creds.cookies.items()
            )
        return headers

    async def _post_form(self, endpoint_key: str, body: dict[str, str]) -> dict:
        """统一 form-urlencoded POST，自动注入 cts/token/sign。"""
        endpoint = self._protocol[endpoint_key]
        url = endpoint["url"]
        sign_cfg = self._protocol["sign"]
        common = self._protocol["common"]

        # 合并：模板默认 + 调用方覆盖
        merged: dict[str, str] = {
            str(k): str(v) for k, v in endpoint.get("body_template", {}).items()
        }
        for k, v in body.items():
            if v is None:
                continue
            merged[str(k)] = str(v)

        # 注入 cts + token
        merged["cts"] = str(int(time.time() * 1000))
        merged["token"] = self.creds.token

        # 计算 sign（按发送顺序）
        ordered_pairs = list(merged.items())
        sign_value = compute_sign(
            ordered_pairs,
            algorithm=sign_cfg["algorithm"],
            secret=sign_cfg["key_or_secret"],
            key_position=sign_cfg.get("key_position", "end"),
            field_order=sign_cfg.get("field_order", "as_sent"),
        )
        merged["sign"] = sign_value

        form_body = urlencode(merged)
        headers = self._build_headers()
        headers["content-type"] = common.get(
            "content_type", "application/x-www-form-urlencoded"
        )

        async with self.session.post(url, data=form_body, headers=headers) as resp:
            text = await resp.text()
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                raise APIError(
                    resp.status, f"非 JSON 响应：{text[:200]}"
                )
            code = payload.get("code", 0)
            if code != 200:
                raise APIError(
                    code,
                    payload.get("message", ""),
                    payload.get("value"),
                )
            return payload

    # ---------- 业务接口 ----------

    async def user_info(self) -> dict:
        """当前账号信息：fileNum、maxVolume、usedVolume、vip 等。"""
        payload = await self._post_form("user_info", {})
        return self._extract(payload, "user_info")

    async def list_album_groups(self) -> list[dict]:
        """获取相册按年/月/日分组的统计。

        返回结构：
        [
          {
            "year": 2020, "count": 201,
            "months": [{"month": 10, "count": 2, "days": [...]}, ...]
          },
          ...
        ]
        """
        payload = await self._post_form("album_group", {})
        return self._extract(payload, "album_group")

    async def list_photos_by_dates(
        self, dates: list[str], limit: int = 100
    ) -> dict[str, list[dict]]:
        """获取指定日期对应的照片列表。

        dates: ["2020-10-04", "2020-10-02", ...]
        返回: {"2020-10-04": [{...photo meta...}, ...], ...}
        """
        body = {"days": ",".join(dates), "limit": str(limit)}
        payload = await self._post_form("album_list_topn", body)
        return payload.get("value") or {}

    async def fetch_download_sig(self) -> str:
        """调用 /file/get_sig/v2 获取 sig 值。

        返回 base64-ish 长串。完整下载 URL 拼装方式见 docs/protocol.json.download。
        """
        payload = await self._post_form("get_sig", {})
        return payload.get("value", "")

    # ---------- 工具方法 ----------

    def _extract(self, payload: dict, endpoint_key: str) -> Any:
        path = self._protocol[endpoint_key].get("response_path") or []
        value: Any = payload
        for k in path:
            if isinstance(value, dict):
                value = value.get(k, {})
            else:
                value = {}
        return value

    @staticmethod
    def normalize_photo(raw: dict) -> dict:
        """把服务端 photo 字段转成工具内部统一字段。"""
        return {
            "photo_id": str(raw.get("id", "")),
            "filename": raw.get("fileName", ""),
            "dir": raw.get("dirName", ""),
            "dir_id": raw.get("dirId"),
            "user_id": raw.get("userId"),
            "size": raw.get("size", 0),
            "url_path": raw.get("url", ""),
            "thumb256_path": raw.get("thumb256", ""),
            "thumb1024_path": raw.get("thumb1024", ""),
            "shoot_time": raw.get("shootTime", 0),
            "md5": raw.get("md5", ""),
            "width": raw.get("width", 0),
            "height": raw.get("height", 0),
            "is_video": bool(raw.get("isVideo", False)),
            "create_time": raw.get("createTime", 0),
            "modify_time": raw.get("modifyTime", 0),
        }

    @staticmethod
    def build_download_url(photo: dict, sig: str, protocol: dict) -> str:
        """拼装完整下载 URL。

        基于 photo.url_path + sig，使用 protocol.download.url_template。
        """
        url_path = photo.get("url_path") or photo.get("url", "")
        base = protocol.get("download", {}).get(
            "base_url_for_relative_path", "https://flyme-oss.meizu.com/"
        )
        # 模板默认：<base>{url_path}?sig=<sig_value>
        return f"{base}{url_path}?sig={sig}"

    def protocol(self) -> dict:
        return dict(self._protocol)