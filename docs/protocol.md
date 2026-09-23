# Flyme 相册协议抓包指南

本指南用于辅助抓取 `photos.flyme.cn` 的关键 API，把结果填入 `protocol.json`。

## 工具准备

任选其一：

| 工具 | 平台 | 优势 |
|------|------|------|
| Chrome DevTools (F12 → Network) | 全平台 | 最轻量，HTTP 抓包足够 |
| Charles | 全平台 | 支持 HTTPS 解密、请求断点 |
| Fiddler Everywhere | Windows | 体验最好，适合反复调试 |
| mitmproxy | 全平台 | 可脚本化抓包 |

> **关键操作**：安装抓包工具的根证书并启用 HTTPS 解密（Charles / mitmproxy / Fiddler 都需要）。
>
> 也可以使用本仓库提供的辅助脚本：
>
> ```bash
> pip install playwright
> playwright install chromium
> python scripts/capture_guide.py
> ```
>
> 脚本会打开登录页，记录所有 XHR / Fetch 到 `docs/capture-snapshot.json`。

## 步骤 1：登录请求

1. 打开浏览器（建议无痕模式避免缓存干扰）
2. 启动抓包
3. 访问 `https://photos.flyme.cn/photo/index`
4. 输入账号密码，点击登录
5. 在 Network 面板找到登录接口（通常 `POST /account/login` 或类似）

需要提取以下信息填入 `protocol.json`：

```json
{
  "login": {
    "url": "https://photos.flyme.cn/...",
    "method": "POST",
    "headers": {
      "X-Sign": "<签名算法输出的值>",
      "X-Timestamp": "<毫秒时间戳>",
      "X-Device-Id": "<设备指纹>",
      "User-Agent": "...",
      "Referer": "https://photos.flyme.cn/photo/index"
    },
    "request_body": {
      "username": "<账号>",
      "password": "<加密后的密码>",
      "...": "其他字段"
    },
    "response": {
      "code": 200,
      "data": {
        "token": "...",
        "userId": "..."
      }
    }
  }
}
```

### 1.1 密码加密算法

观察登录请求中 `password` 字段的值，反推加密算法：

- **RSA**：通常 `password` 是 Base64 编码的密文，公钥在登录页 JS 里硬编码
- **SM2（国密）**：与 RSA 类似，但密文以 `04` 开头
- **AES + 固定密钥**：密文长度是 16 的倍数
- **AES + 动态密钥**：密钥从登录页 / 服务器下发

> 把找到的密钥与编码方式（Base64 / HEX）记录到 `protocol.json` 的 `crypto` 字段。

### 1.2 签名算法

观察 `X-Sign` 字段。把签名 JS 源码（通常叫 `sign.js` 或类似）从 Sources 面板找出来，记录：

```json
{
  "sign": {
    "location": "<sign.js URL 或行号>",
    "algorithm": "HmacSHA256 / RSA / MD5...",
    "input_fields": ["username", "timestamp", "nonce"],
    "key_or_secret": "<密钥/盐>"
  }
}
```

## 步骤 2：相册列表接口

登录成功后，刷新相册页面，提取相册列表请求：

```json
{
  "album_list": {
    "url": "...",
    "method": "GET / POST",
    "headers": { "<登录后所有 Cookies + 必要 Header>" },
    "query_or_body": {},
    "response_path": ["data", "albums"]
  }
}
```

## 步骤 3：相册内照片列表

进入某个相册，分页查看照片，提取请求：

```json
{
  "photo_list": {
    "url": "https://photos.flyme.cn/album/{album_id}/photos?page=1&size=100",
    "method": "GET",
    "pagination": { "field": "page", "page_size": 100 },
    "response_path": ["data", "photos"],
    "photo_fields": {
      "photoId": "xxx",
      "updateTime": 1716000000000,
      "downloadUrl": "https://...?Expires=...&Signature=...",
      "size": 12345,
      "type": "image/jpeg"
    }
  }
}
```

## 步骤 4：下载 URL 验证

把步骤 3 中 `downloadUrl` 复制到浏览器地址栏直接访问，应能下载到原图。验证：

- 是否需要特定 Header（如 `Range`、`Referer`）
- 是否带过期参数（如 `Expires`、`Signature`）
- 过期后是否能通过 GET 步骤 3 重新拿到新 URL

## 步骤 5：风控 / 验证码

观察登录失败、连续失败、被限制时：

- 是否弹出滑块（**如果弹出，记录滑块 iframe URL**）
- 是否返回特定 code（如 `-1001`、`403`）

更新 `protocol.json` 的 `rwinders` 字段：

```json
{
  "rwinders": {
    "captcha_iframe_url": "...",
    "lock_error_codes": [-1001, 403],
    "max_retries": 3,
    "cooldown_seconds": 60
  }
}
```

## 步骤 6：填入 protocol.json

所有抓包结果按 `docs/protocol.template.json` 的结构填入 `docs/protocol.json`。完成后启动工具即可正常登录。

## 抓包常见问题

| 问题 | 排查 |
|------|------|
| 抓不到 HTTPS | 安装抓包工具的 CA 证书并信任 |
| 看不到登录接口 | 检查是否走 SSO（`passport.flyme.cn`） |
| 加密密钥找不到 | 在 Sources 面板全局搜索 `encrypt`、`rsa`、密钥变量名 |
| 签名值每次都变 | 确认签名算法及输入字段，可能需要模拟 JS |
| 滑块不能跳过 | 工具支持手动登录后回填 Cookie，详见 README |

## 填好后请同步更新以下常量

- `protocol.json` 的 `version` 字段（递增版本号）
- 把抓包结果摘要（登录 URL、相册 URL、签名算法）补充到本文件末尾"抓包记录"段

---

## 已抓取接口记录（2026-09-23）

> 以下抓包由用户提供。基于这些数据已写入 `docs/protocol.json` 的接口模板。
> 工具运行时仍需用户在 `sign.key_or_secret` 中填入签名密钥才能正常工作。

### 接口域名

实际接口位于 **`https://mzstorage.meizu.com`**（与前端页面 `photos.flyme.cn` 不同源，通过 CORS 跨域调用）。

### 已确认接口（4 个）

| # | URL | 方法 | 请求字段 | 响应关键字段 |
|---|---|---|---|---|
| 1 | `/file/get_sig/v2` | POST | `type=2` | `value`（base64-ish 长串，下载 URL 签名） |
| 2 | `/user/info` | POST | `type=0` | `value.fileNum/maxVolume/usedVolume/vip` |
| 3 | `/album/group` | POST | `order=1` | `value.years[].count` + 月/日明细 |
| 4 | `/album/listTopN` | POST | `days=...&limit=17&order=1&isWebp=true` | `value[date]` → 照片详情数组 |

### 通用请求模式

所有 POST 请求都是 `application/x-www-form-urlencoded`，统一格式：

```
<business_fields>&cts=<milliseconds>&token=<token>&sign=<md5_hex>
```

业务字段按特定顺序（不一定是字典序）。`cts` = 13 位毫秒时间戳，`token` = 浏览器登录后由 Flyme 下发的会话凭据（数百字符），`sign` = 32 位十六进制（推测 MD5）。

### 已抓取的三个 sign（用于反推算法）

```
GET /file/get_sig/v2:  sign=717e8185f98d10663022253924f586c8
GET /user/info:        sign=5a3d13168763cb3b9447dd9dc9efeca2
GET /album/group:      sign=7f8690fee296fb1eb41c8443366d5d5f
GET /album/listTopN:   sign=8a9570a495a12be8d27d81c11efa545f
```

**反推思路**：把每个接口的所有字段按发送顺序拼接 + 末尾追加密钥 → MD5。这是最常见的同款适配模式。如果验证失败，请尝试：

- 把密钥放在开头（`key_position=head`）
- 按字典序排序字段（`field_order=sorted`）

### 用户信息响应（实际样本）

```json
{
  "code": 200,
  "value": {
    "fileNum": 1220,
    "maxFileNum": 1000,
    "meltTime": 0,
    "vipId": 1,
    "maxVolume": 5368709120,
    "usedVolumePhotos": 2914980477,
    "usedVolume": 2914980477,
    "vipName": "体验版",
    "vip": 1,
    "rights": {"memberId": 1, "maxFileNum": 1000, "maxStorage": 5}
  }
}
```

### 相册分组响应结构

```json
{
  "code": 200,
  "value": {
    "count": 1220,
    "years": [
      {
        "year": 2020, "count": 201,
        "months": [{"month": 10, "count": 2, "days": [{"day": 4, "count": 1}]}]
      }
    ]
  }
}
```

> **重要**：该接口的"count"是照片数，**不是相册数**。魅族云相册的"相册"实际是按拍摄时间组织的视图，没有传统意义上的"用户相册"概念（部分老版本有"dirName"区分：DCIM 截图等）。

### 单张照片字段（album/listTopN 响应）

```json
{
  "id": 25595161,
  "fileName": "P01004-084055.jpg",
  "dirName": "DCIM",
  "dirId": 76284,
  "userId": 132461569,
  "size": 3014760,
  "url": "b2/e2/fe/9d/b2e2fe9d1dd49d66ddd4718014464ac1_...s3014760u...jpg",
  "thumb256": "...@256w_2o.webp",
  "thumb1024": "...@1024w_2o.webp",
  "shootTime": 1601772055000,
  "md5": "b2e2fe9d1dd49d66ddd4718014464ac1_...s3014760u...",
  "status": 1,
  "isVideo": false,
  "height": 3120, "width": 4208,
  "createTime": 1602341509000,
  "modifyTime": 1602341509000
}
```

**关键观察**：
- `url` 是**相对路径**，不是完整 URL。完整 URL 需要拼上 CDN 前缀 + `sig` 参数。
- `md5` 字段形如 `b2e2fe9d..._<hash>_<dim>s<size>u<userId>`，前 32 个字符才是真正的 md5 哈希（用 `_` 切第一段）。
- 缩略图 URL 后缀 `@256w_2o.webp` / `@1024w_2o.webp` 表示 256px/1024px 宽的 WebP 缩略图。

### 仍需补全的部分

| 项目 | 状态 | 说明 |
|---|---|---|
| **签名密钥** | ❌ 未填 | 必须从登录页 JS 中找到 `sign(...)` 函数和密钥字符串 |
| **下载 URL 模板** | ⚠️ 推测 | 当前用 `<base><url_path>?sig=<sig>` 占位；真实模板待 `sig` 返回后确认 |
| **分页参数** | ⚠️ 推测 | `days=` 是 `逗号分隔` 还是 `重复 days=` 待验证 |
| **下载 URL 前缀** | ⚠️ 推测 | `flyme-oss.meizu.com` 是猜测，需要在浏览器实际下载一张图验证 |

### 验证清单（首次部署必做）

1. ✅ `docs/protocol.json` 已写入抓包数据
2. ❌ 在 `docs/protocol.json` 的 `sign.key_or_secret` 填入签名密钥
3. ❌ 启动工具 → 登录（粘贴 token）→ 测试 `用户信息` 接口（`/user/info`）是否返回 200
4. ❌ 测试 `相册分组` 接口是否返回 years
5. ❌ 测试单张照片是否能下载到本地（验证 CDN 前缀）
6. ❌ 如果不通过，根据 `app/cli.md` 调整 `sign` 算法配置（key_position / field_order）