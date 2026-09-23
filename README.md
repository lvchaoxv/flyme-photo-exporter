# Flyme Photo Exporter (Phase 4 — 原图下载)

备份本人魅族 Flyme 云相册（https://photos.flyme.cn）的工具，**下载原图**（非缩略图）。

> 提供两个版本：
> - **命令行版**：`scripts/flyme_export.js`（Node.js + Playwright，无浏览器依赖）
> - **图形界面版**：`scripts/flyme_export_gui.py`（Python + Tkinter，选相册、看进度、点按钮导出）

> **合规声明**：本工具仅供个人备份**本人账号**内的相册内容，不内置任何账号共享、群控、撞库能力。使用本工具需自行承担违反 Flyme 用户协议或触发风控封号的风险。请勿用于他人账号或商业用途。

## 这是什么

用 **Node.js + Playwright headless Chromium** 把浏览器自动化当作"已登录态 API 客户端"：

- 你在正常 Chrome 登录 photos.flyme.cn（含滑块/二次验证）
- 把 cookie 复制给本工具
- 工具启动无头 Chromium，注入 cookie，解密 OSS 临时凭证，按阿里云 OSS 签名算法生成原图下载 URL，把图片二进制写到本地 `./photos/`

## 技术方案

魅族云相册图片存阿里云 OSS 私有桶，需要 STS 临时凭证 + SigV1 签名。本工具的做法：

1. **浏览器端（一次性）**：flyme 前端 SDK 会把 `file/get_sig/v2` 返回的加密 STS 凭证用内置 RSA 私钥解密。工具复用 SDK 暴露的 `window.JSEncrypt` + 内置私钥，在浏览器里完成解密，拿到 `{bucket, region, accessKeyId, accessKeySecret, securityToken}`。
2. **Node 端（下载主体）**：用 `crypto` 模块按阿里云 OSS SigV1 算法，对每张照片的原图 object key（`photo.url` 字段）计算签名 URL，然后直接 `fetch` 下载原图。

这样**完全绕开 sign 破解与 STS 解析**，且下载的是**原图**（`image/jpeg`，`photo.size` 字节数完全一致），而非缩略图。

## 功能

- ✅ 启动 headless Chromium，加载 flyme 前端 SDK
- ✅ 注入登录 cookie，自动调 `album/dir/list` 拿相册列表
- ✅ 调 `album/list` 翻页拿每张照片元数据
- ✅ 解密 STS 凭证，Node 端计算签名 URL，下载**原图** JPEG
- ✅ 断点续传：同名文件 + size 一致自动跳过
- ✅ STS 凭证过期自动刷新（`expiredTime` 前 5 分钟触发）
- ✅ 单相册模式（`--album <dirId>`）便于按相册分批备份
- ✅ 单文件重试 3 次，指数退避
- ✅ 并发下载，默认 5 线程
- ✅ 视频支持：默认跳过，`--video` 开启下载照片 + 视频

## 系统要求

- Windows 10/11 x64 / macOS / Linux
- **Node.js 18+**（依赖全局 `fetch`、`crypto` 模块）
- 网络：能访问 `photos.flyme.cn`、`mzstorage.meizu.com`、`meizu-storage.oss-cn-shanghai.aliyuncs.com`
- ~150 MB 磁盘：Playwright chromium 二进制

## 安装

```bash
cd flyme-photo-exporter
npm install
npx playwright install chromium   # 若 npm install 未自动装浏览器
```

## GUI 版（图形界面，推荐新手）

无需命令行操作，界面化选相册、看进度、点按钮导出：

```bash
# 安装依赖（Tkinter 是 Python 自带，无需装）
pip install requests cryptography

# 启动图形界面
python scripts/flyme_export_gui.py
```

操作流程：

1. 在输入框粘贴 `_utoken`（或点「读取 cookies.json」自动读取）
2. 点「连接验证」，界面会列出所有相册
3. 勾选要导出的相册，选择是否「同时下载视频」
4. 点「开始导出」，进度条和日志实时显示

> 图形界面版**不需要浏览器、不需要 Node.js**，直接 `requests` 调接口 + Python 解密 STS + 下载原图。适合不熟悉命令行的用户。

## 使用（命令行版）

### 1. 导出 cookie

在已登录 photos.flyme.cn 的 Chrome 里，导出当前域名 + `.flyme.cn` + `.meizu.com` 的**全部** cookie。

**方式 A：F12 → Application → Cookies**

依次选中 `https://photos.flyme.cn`、`.flyme.cn`、`.meizu.com`、`login.flyme.cn` 各域，把 cookie 手敲成 `--cookie "name1=value1; name2=value2; ..."`。

**方式 B：JSON 文件（推荐）**

用 `EditThisCookie` / `Cookie Editor` 扩展导出 JSON，保存为 `cookies.json`：

```json
[
  { "name": "_utoken", "value": "xxx...", "domain": ".flyme.cn", "path": "/" },
  { "name": "DSESSIONID", "value": "xxx...", "domain": ".flyme.cn", "path": "/", "httpOnly": true },
  ...
]
```

**关键 cookie**：

| cookie 名 | 用途 |
| --- | --- |
| `_utoken` | 主要鉴权 token（**必须**，短期有效，几小时到一天） |
| `DSESSIONID` | 会话（`.flyme.cn` 与 `.meizu.com` 都要） |
| `JSESSIONID` | login.flyme.cn 会话 |

> 提示：`_utoken` 有效期较短（约 8 小时）。如果中途报登录态失败，重新登录后重新导出即可，已下载的文件会自动跳过（断点续传）。

### 2. 试运行（最快验证）

```bash
# 单相册 + 仅 1 页（~34 张原图）验证流程
node scripts/flyme_export.js --cookie-file cookies.json --album 76227 --limit 1
```

成功的话 `./photos/Screenshots/` 下会有几十张 **JPEG 原图**（每张几百 KB 到几 MB，不是几十 KB 的缩略图）。

### 3. 完整导出

```bash
# 全部相册（DCIM + Camera + Screenshots）
node scripts/flyme_export.js --cookie-file cookies.json

# 仅 Screenshots 相册（dirId=76227），配合断点续传可重复执行
node scripts/flyme_export.js --cookie-file cookies.json --album 76227

# 自定义输出路径 + 并发数
node scripts/flyme_export.js --cookie-file cookies.json --out D:\backup\flyme --concurrency 5

# 同时下载视频（照片 + 视频）
node scripts/flyme_export.js --cookie-file cookies.json --video
```

### 4. 续传

如果中途中断（token 过期 / 网络抖动）：

1. 重新登录
2. 重新导出 cookie
3. 重跑同一条命令，已存在且 size 一致的文件自动跳过

## CLI 参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--cookie "..."` | 直接传 cookie 字符串 | - |
| `--cookie-file path` | 从 JSON 文件读 cookie | - |
| `--album dirId` | 仅导一个相册（推荐用于大相册分批） | 全部 |
| `--out dir` | 输出目录 | `./photos` |
| `--limit pages` | 每相册前 N 页（快速测试用） | 全部 |
| `--concurrency n` | 并发下载数 | 5 |
| `--retry n` | 单文件重试次数 | 3 |
| `--video` | 同时下载视频（`isVideo=true`），默认跳过 | 关闭 |
| `--headless false` | 有头模式（调试用，能看到浏览器窗口） | headless |

## 目录结构

```
flyme-photo-exporter/
├── scripts/
│   └── flyme_export.js      # 主脚本(单文件)
├── docs/                    # 协议文档(旧 Phase 资料,保留参考)
├── app/                     # 旧 Phase 1 Python 骨架(已废弃,保留)
├── main.py                  # 旧 PyQt6 GUI 入口(已废弃,保留)
├── requirements.txt         # 旧 Python 依赖(已废弃,保留)
├── package.json             # Node.js 项目配置
└── README.md                # 本文件
```

输出目录示例（相册目录名 = `dirName`）：

```
photos/
├── DCIM/            # 707 张(含 71 视频,实际 636 张照片)
│   ├── IMG_20200101_120000.jpg
│   └── ...
├── Camera/          # 285 张
└── Screenshots/     # 228 张
```

## 安全与风险

- ⚠️ **仅限本人账号**：你承担一切使用风险
- ⚠️ **token 安全**：cookie 字符串包含登录态，不要提交到 git（已加入 `.gitignore`）
- ⚠️ **频率限制**：建议并发 ≤ 5，过高可能触发 flyme 风控
- ⚠️ **视频体积**：视频单个可达 100MB+，加 `--video` 会额外占用磁盘空间

## 常见问题

**Q: 报错 `登录态验证失败`？**
A: cookie 已过期（`_utoken` 有效期较短）。重新登录后重新导出。

**Q: 报错 `导航后跳到 login.flyme.cn`？**
A: cookie 已过期或被浏览器清掉。重新登录 photos.flyme.cn，刷新页面后立刻复制 cookie。

**Q: 下载的是原图还是缩略图？**
A: 原图。文件 magic bytes 为 `FF D8 FF`（JPEG），大小与相册元数据 `photo.size` 完全一致。缩略图是 `@256w_2o.webp`，本工具不会下载。

**Q: 视频怎么下载？**
A: 加 `--video` 参数即可同时下载视频（`isVideo=true` 的 mp4）。默认跳过是为了避免误下大文件。

**Q: 旧 Python 代码还能用吗？**
A: 旧 `app/`、`main.py`、`build.spec` 已废弃，仅作协议逆向参考保留。GUI/打包功能未实现。

## License

MIT
