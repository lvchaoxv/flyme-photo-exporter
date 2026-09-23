"""签名算法适配器。

Flyme 后端对每个 form-urlencoded POST 请求都要求 `sign` 字段。
签名密钥来自前端 JS，工具不直接获取，而是让用户在
`docs/protocol.json` 的 `sign.key_or_secret` 字段填入。

支持的算法（`algorithm` 字段）：
- md5_concat  : MD5(按发送顺序拼接业务参数 + &key={secret})
- md5_prefix  : MD5({secret} + 拼接参数)
- md5_no_key  : MD5(拼接参数)（某些版本的 Flyme 用此方式）

`key_position`（仅 md5_concat 生效）：
- end : secret 拼接到 body 末尾（默认）
- head: secret 拼接到 body 开头

`field_order`：
- as_sent: 按调用方传入顺序（推荐，符合抓包观察）
- sorted : 按 key 字典序排序后拼接
"""
from __future__ import annotations

import hashlib
from typing import Iterable


def compute_sign(
    ordered_params: Iterable[tuple[str, str]],
    *,
    algorithm: str,
    secret: str,
    key_position: str = "end",
    field_order: str = "as_sent",
) -> str:
    """计算 Flyme 接口签名。

    ordered_params: 业务参数的有序列表 [(k, v), ...]，不含 cts/token/sign。
                   （签名通常在 cts + token 注入后再算，所以传入所有 body 字段）
    """
    items = list(ordered_params)
    if field_order == "sorted":
        items.sort(key=lambda kv: kv[0])
    body = "&".join(f"{k}={v}" for k, v in items)

    if algorithm == "md5_no_key":
        s = body
    elif algorithm == "md5_prefix":
        s = f"{secret}{body}"
    elif algorithm == "md5_concat":
        if key_position == "head":
            s = f"key={secret}&{body}"
        else:
            s = f"{body}&key={secret}"
    else:
        raise ValueError(f"未知签名算法：{algorithm}")

    return hashlib.md5(s.encode("utf-8")).hexdigest()