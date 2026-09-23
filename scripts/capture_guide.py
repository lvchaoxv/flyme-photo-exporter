"""抓包辅助脚本：用 Playwright 打开登录页并打印所有 XHR/Fetch 请求。

用法：
    pip install playwright
    playwright install chromium
    python scripts/capture_guide.py

输出：
    把登录页加载阶段的所有 XHR/Fetch 请求的 URL + 关键 Header + Body 写入
    docs/capture-snapshot.json，便于快速对接到 docs/protocol.json。

⚠️ 本脚本不绕过任何风控；仅用于辅助人工抓包。
"""
from __future__ import annotations

import json
from pathlib import Path

OUTPUT_FILE = (
    Path(__file__).resolve().parent.parent / "docs" / "capture-snapshot.json"
)


async def main() -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("请先安装：pip install playwright && playwright install chromium")
        return

    snapshot: dict = {"requests": []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        def on_request(req) -> None:
            if req.resource_type in ("xhr", "fetch", "document"):
                snapshot["requests"].append(
                    {
                        "url": req.url,
                        "method": req.method,
                        "headers": dict(req.headers),
                        "post_data": req.post_data,
                        "resource_type": req.resource_type,
                    }
                )

        page.on("request", on_request)
        await page.goto("https://photos.flyme.cn/photo/index")
        print("已在浏览器中打开登录页，请在浏览器中完成登录。")
        print("登录成功后回到终端按 Enter 结束并保存快照…")
        await _wait_for_enter()

        await browser.close()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已保存 {len(snapshot['requests'])} 条请求到 {OUTPUT_FILE}")


async def _wait_for_enter() -> None:
    loop = asyncio_get_event_loop()
    await loop.run_in_executor(None, input)


def asyncio_get_event_loop():
    import asyncio
    return asyncio.get_event_loop()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())