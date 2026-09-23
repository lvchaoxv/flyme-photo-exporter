# Flyme 云相册导出工具 — 技术实现方案

> 一份可落地的技术实现方案,覆盖从环境搭建到下载完成的每一个步骤、关键算法与接口。

---

## 1. 方案概述

### 1.1 目标

把本人魅族云相册(https://photos.flyme.cn)的**全部照片(原图)和视频**备份到本地磁盘,支持断点续传。

### 1.2 技术栈

| 项 | 选型 | 理由 |
| --- | --- | --- |
| 运行时 | Node.js 18+ | 内置 `fetch`、`crypto`、`fs/promises` |
| 浏览器自动化 | Playwright 1.40 + headless Chromium | 复用浏览器内核,绕开 sign/STS 破解 |
| 依赖 | 仅 `playwright` | 不引入第三方库 |

### 1.3 核心思路

**不破解,复用浏览器内核。**

- flyme 的 `sign` 签名、STS 凭证解密,都由前端 SDK 在浏览器里完成。
- 脚本用 Playwright 打开相册网站,让 SDK 自己跑起来,暴露出 `window.JSEncrypt` 与内置 RSA 私钥。
- 浏览器端只做**一次**解密(拿 STS),其余(算签名、下载)全部在 Node 端用标准 `crypto` 完成。

---

## 2. 架构设计

### 2.1 职责划分

```
┌─────────────────────────────────────────────────────────────┐
│ 浏览器端 (Playwright headless Chromium) —— 仅做 3 件事       │
│   1. 打开 photos.flyme.cn,加载 flyme 前端 SDK               │
│   2. 解密 file/get_sig/v2 返回的 RSA 加密 STS 凭证          │
│      (复用 window.JSEncrypt + 内置私钥)                     │
│   3. 调相册接口(album/dir/list、album/list 翻页)            │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ 把 STS 凭证 + 照片元数据 传回 Node
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Node 端 —— 下载主体                                         │
│   1. 用 crypto 模块按 OSS SigV1 算法计算签名 URL             │
│   2. fetch 签名 URL 下载原图/视频                            │
│   3. fs.writeFile 写盘 + 断点续传 + 重试 + STS 自动刷新      │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
cookie ──▶ Playwright 启动 ──▶ 打开 photos.flyme.cn 加载 SDK
   │
   ├─▶ 解密 STS (浏览器端一次性)
   │      └─▶ sig = {bucket, region, accessKeyId, accessKeySecret, securityToken, expiredTime}
   │
   ├─▶ album/dir/list ──▶ dir[] (相册列表)
   │
   ├─▶ album/list (按 dirId + offset 翻页) ──▶ file[] (照片/视频元数据)
   │
   └─▶ 对每项: signUrl(photo.url, sig) ──▶ fetch 下载 ──▶ 写盘
```

---

## 3. 详细实现步骤

### 3.1 环境准备

```bash
cd flyme-photo-exporter
npm install                      # 安装 playwright
npx playwright install chromium  # 下载 chromium(~150MB)
```

### 3.2 获取登录 cookie

用户先在正常 Chrome 里登录 `photos.flyme.cn`,再导出 cookie(JSON 数组或 `name=value` 字符串)。

**必须包含的 cookie**:

| 名称 | 域 | 用途 |
| --- | --- | --- |
| `_utoken` | `.flyme.cn` | 主鉴权 token(短期有效,约 8 小时) |
| `DSESSIONID` | `.flyme.cn` / `.meizu.com` | 会话 |
| `JSESSIONID` | `login.flyme.cn` | 登录会话 |

### 3.3 启动浏览器并注入 cookie

```js
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  userAgent: 'Mozilla/5.0 ... Chrome/120.0.0.0 ...',
  viewport: { width: 1440, height: 900 },
});
await context.addCookies(cookies);   // 注入
const page = await context.newPage();
```

### 3.4 加载 SDK(关键前提)

```js
await page.goto('https://photos.flyme.cn', { waitUntil: 'networkidle' });
```

这一步让 flyme 前端 JS 执行起来,`window.JSEncrypt` 被暴露。**不加载 SDK,后续解密无从谈起。**

加载后要检查是否被重定向(坑 #1):

```js
const landedAt = page.url();
if (!landedAt.startsWith('https://photos.flyme.cn')) {
  throw new Error('cookie 过期,页面重定向到登录页');
}
```

### 3.5 验证登录态

```http
POST https://mzstorage.meizu.com/user/info
Content-Type: application/x-www-form-urlencoded

type=0&cts=<毫秒时间戳>&token=<_utoken>
```

响应 `code=200` 即登录有效,`value.fileNum` 即总文件数(1220)。

### 3.6 解密 STS 凭证(浏览器端,一次性)

**接口**:

```http
POST https://mzstorage.meizu.com/file/get_sig/v2
type=2&cts=<ts>&token=<_utoken>
```

**返回**:`value` 是一段 RSA 加密的 base64 串。

**解密**(注入到浏览器执行的 helper):

```js
async getSig() {
  const r = await postJSON('https://mzstorage.meizu.com/file/get_sig/v2', {
    type: '2', cts: String(Date.now()), token: getCookie('_utoken'),
  });
  const crypt = new window.JSEncrypt();
  crypt.setPrivateKey(PRIVATE_KEY);          // SDK 内置 RSA 私钥
  return JSON.parse(crypt.decryptLong(r.value));
}
```

**解密结果** `sig` 字段:

```json
{
  "bucket": "meizu-storage",
  "region": "oss-cn-shanghai",
  "accessKeyId": "STS.xxx",
  "accessKeySecret": "xxx",
  "securityToken": "xxx",
  "expiredTime": "2026-09-23T08:10:00Z"
}
```

### 3.7 获取相册列表

```http
POST https://mzstorage.meizu.com/album/dir/list
limit=100&order=1&cts=<ts>&token=<_utoken>
```

> 注意:`limit` 参数**必须带**,否则返回 400(坑 #5)。

返回 `value.dir[]`,每项字段:

| 字段 | 含义 |
| --- | --- |
| `id` | 相册 id(即 dirId) |
| `dirName` | 相册名(DCIM/Camera/Screenshots) |
| `fileNum` | 文件数(707/285/228) |

### 3.8 翻页获取照片/视频元数据

```http
POST https://mzstorage.meizu.com/album/list
limit=34&offset=<0,34,68...>&order=1&isWebp=true&dirId=<id>&cts=<ts>&token=<_utoken>
```

翻页终止条件:`value.end === 1` 或 `file[]` 为空。

每项 `file` 的关键字段:

| 字段 | 含义 |
| --- | --- |
| `fileName` | 文件名(如 `S00808-08451705.jpg`、`V00730-182650.mp4`) |
| `url` | **原图/视频的 object key**(如 `d2/24/59/4a/xxx.jpg`) |
| `size` | 原文件字节数(断点续传依据) |
| `isVideo` | 是否视频 |
| `thumb256` / `thumb1024` | 缩略图路径(带 `@256w_2o.webp` 后缀,不用) |

> 关键:下载用的是 `url`(原图),**不是** `thumb256/thumb1024`(缩略图,坑 #3)。

### 3.9 计算 OSS 签名(Node 端)

阿里云 OSS SigV1,复刻 SDK 的 `signUrl`:

```js
const crypto = require('node:crypto');

function signUrl(objectKey, sig) {
  const Expires = Math.floor(Date.now() / 1000) + 3600;   // 1 小时后过期
  const stringToSign =
    'GET\n\n\n' + Expires + '\n/' + sig.bucket + '/' + objectKey +
    '?response-content-disposition=attachment;filename=' + objectKey +
    '&security-token=' + sig.securityToken;
  const signature = crypto.createHmac('sha1', sig.accessKeySecret)
    .update(stringToSign)
    .digest('base64');
  return 'https://' + sig.bucket + '.' + sig.region + '.aliyuncs.com/' + objectKey +
    '?OSSAccessKeyId=' + encodeURIComponent(sig.accessKeyId) +
    '&Expires=' + Expires +
    '&Signature=' + encodeURIComponent(signature) +
    '&security-token=' + encodeURIComponent(sig.securityToken) +
    '&response-content-disposition=attachment;filename=' + encodeURIComponent(objectKey);
}
```

**签名公式要点**:

```
StringToSign = "GET\n\n\n{Expires}\n/{bucket}/{objectKey}?response-content-disposition=attachment;filename={objectKey}&security-token={securityToken}"
Signature    = base64( HMAC-SHA1(accessKeySecret, StringToSign) )
```

### 3.10 下载原图(Node 端)

签名 URL 是自包含的(含全部凭证),Node 端 `fetch` 直接下,无需浏览器、无需 cookie:

```js
const resp = await fetch(signUrl(photo.url, sig));
const buf = Buffer.from(await resp.arrayBuffer());   // 完整原图字节
await fs.writeFile(filePath, buf);
```

验证产物(坑 #3 教训):
- 照片:magic bytes `ffd8ffe0`(JPEG)
- 视频:offset 4-8 为 `ftyp`(MP4)
- `buf.length === photo.size`

### 3.11 断点续传

```js
try {
  const existing = await fs.stat(filePath);
  if (existing.size === photo.size) return 'skip';   // 已存在且大小一致
} catch { /* 不存在,继续下载 */ }
```

判断依据 = **目录名 + 文件名 + size** 三者一致(坑 #6 教训:目录名必须用真实 `dirName`)。

### 3.12 错误重试

单文件失败后指数退避重试 3 次:延迟 `1000 * 2^(attempt-1)` ms。

### 3.13 STS 自动刷新(坑 #7)

STS 约 1 小时过期,长任务需刷新。用「缓存 + 过期检查 + 并发锁」:

```js
const sigState = { sig: null, expiry: 0, inflight: null };

async function getFreshSig(page) {
  const now = Date.now();
  if (sigState.sig && now < sigState.expiry - 5 * 60 * 1000) return sigState.sig;  // 还有 5 分钟余量
  if (sigState.inflight) return sigState.inflight;                                  // 复用进行中的请求
  sigState.inflight = (async () => {
    const sig = await page.evaluate(() => window.__flymeHelpers.getSig());
    sigState.sig = sig;
    sigState.expiry = new Date(sig.expiredTime).getTime();
    return sig;
  })();
  const sig = await sigState.inflight;
  sigState.inflight = null;
  return sig;
}
```

---

## 4. 接口清单

| # | 接口 | 方法 | 关键参数 | 用途 |
| --- | --- | --- | --- | --- |
| 1 | `mzstorage.meizu.com/user/info` | POST | `type=0, cts, token` | 验证登录态 |
| 2 | `mzstorage.meizu.com/file/get_sig/v2` | POST | `type=2, cts, token` | 获取加密 STS 凭证 |
| 3 | `mzstorage.meizu.com/album/dir/list` | POST | `limit=100, order=1, cts, token` | 相册列表 |
| 4 | `mzstorage.meizu.com/album/list` | POST | `limit=34, offset, order=1, isWebp=true, dirId, cts, token` | 翻页取文件元数据 |
| 5 | `meizu-storage.oss-cn-shanghai.aliyuncs.com/{objectKey}` | GET | 签名 query 参数 | 下载原图/视频 |

> 接口 1-4 实测**不需要 `sign` 参数**(token 即鉴权依据);接口 3 必须带 `limit`。

## 5. 配置项(CLI 参数)

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--cookie-file` | cookie JSON 文件 | - |
| `--cookie` | cookie 字符串 | - |
| `--album <id>` | 单相册模式 | 全部 |
| `--out <dir>` | 输出目录 | `./photos` |
| `--limit <n>` | 每相册前 n 页(测试) | 全部 |
| `--concurrency <n>` | 并发下载数 | 5 |
| `--retry <n>` | 单文件重试次数 | 3 |
| `--video` | 同时下载视频 | 关闭 |
| `--headless false` | 有头模式 | headless |

## 6. 完整执行流程(汇总)

```
1. 解析 CLI 参数
2. 加载 cookie 文件
3. 启动 Chromium + 注入 cookie
4. 打开 photos.flyme.cn 加载 SDK (检查是否重定向)
5. 注入浏览器 helper (getSig/checkLogin/getAlbumDirs/getAlbumPhotos)
6. 验证登录态 (user/info)
7. 解密 STS 凭证 (get_sig + JSEncrypt)
8. 获取相册列表 (album/dir/list)
9. 对每个相册:
   a. 翻页拉取文件元数据 (album/list)
   b. 过滤 isVideo (除非 --video)
   c. 并发下载:
      - getFreshSig (过期自动刷新)
      - signUrl 算签名
      - fetch 下载
      - 断点续传 + 重试
10. 输出统计 (总数/下载/跳过/失败/字节)
```

## 7. 目录结构

```
flyme-photo-exporter/
├── scripts/flyme_export.js   # 主脚本(单文件,含全部逻辑)
├── docs/                     # 协议文档 + 复盘 + 本方案
├── app/、main.py 等          # 旧 Python 代码(废弃保留)
├── package.json
├── cookies.json              # 登录 cookie(已 gitignore)
└── photos/                   # 下载产物
    ├── DCIM/                 # 636 照片 + 71 视频
    ├── Camera/               # 285 照片
    └── Screenshots/          # 228 照片
```

---

## 8. 关键注意事项

1. **原图 vs 缩略图**:用 `photo.url` 下载,绝不用 `thumb256/thumb1024`;产物要校验文件头。
2. **签名绑定 object key**:签名含 style 后缀,不能拿缩略图签名去下原图。
3. **STS 有效期**:约 1 小时,必须做自动刷新。
4. **`_utoken` 有效期**:约 8 小时,过期需重新登录导 cookie。
5. **目录名单一来源**:用 `album/dir/list` 返回的 `dirName`,保证断点续传命中。
6. **合规**:仅限本人账号,风险自负。
