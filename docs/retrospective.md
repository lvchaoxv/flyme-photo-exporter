# Flyme 云相册导出工具 — 完整复盘

> 目标:备份本人魅族云相册(photos.flyme.cn)的全部照片与视频到本地。
> 结果:成功导出 **1220 个文件**(1149 张原图 JPEG + 71 个 MP4 视频),约 2.78 GB,0 失败。

---

## 一、需求与目标

| 项 | 值 |
| --- | --- |
| 数据源 | https://photos.flyme.cn(魅族云相册) |
| 相册 | DCIM 707 / Camera 285 / Screenshots 228,共 1220 项 |
| 产出 | 本地 `photos/<相册名>/` 目录,原图 + 视频 |
| 约束 | 仅本人账号、不破 sign、不解析 STS、断点续传 |

## 二、技术方案演进

整个项目经历了三个阶段,方向性选择是最终成功的关键。

### Phase 1-2(已废弃):Python 协议逆向

最初尝试用 Python 直接逆向 HTTP 协议。**卡点**:`mzstorage.meizu.com` 接口有自研 `sign` 签名算法(密钥不明),图片存阿里云 OSS 需要 STS 临时凭证,两者都难以在协议层破解。

**教训**:过早陷入「逆向加密算法」的深水区,ROI 极低。

### Phase 3:Playwright 浏览器自动化

换思路:不破解,直接复用浏览器内核。Node.js + Playwright 启动 headless Chromium,注入登录 cookie,让 flyme 前端 SDK 在浏览器里自动算 `sign`、自动处理 STS。Node.js 只负责把浏览器 fetch 到的图片写到磁盘。

**这个方向对了**,但 Phase 3 有个致命缺陷(见坑 #3):依赖 SDK 预加载的**缩略图**缓存,下载的是 256px 缩略图,不是原图。

### Phase 4(最终方案):原图下载

逆向 SDK 源码,彻底搞清 OSS 签名机制,做到「浏览器只做一次解密,Node 端算签名 + 下载原图」。

最终架构:

```
浏览器端(一次性)                          Node 端(下载主体)
─────────────────────                    ─────────────────────
1. 打开 photos.flyme.cn 加载 SDK          1. 翻页调 album/list 拿照片元数据
2. 解密 file/get_sig/v2 返回的           2. crypto 模块按 OSS SigV1 算签名
   RSA 加密 STS 凭证                         (HMAC-SHA1 + base64)
   (复用 SDK 暴露的 window.JSEncrypt      3. fetch 签名 URL 下载原图
    + 内置 RSA 私钥)                      4. 断点续传 + STS 自动刷新
```

## 三、关键机制(逆向结论)

这是整个项目最核心、最耗时的一部分,记录如下:

1. **STS 凭证获取**:`POST mzstorage.meizu.com/file/get_sig/v2`,body `type=2&cts=...&token=...`。返回的 `value` 是 **RSA 加密的 JSON**。
2. **解密**:SDK 前端内置 RSA 私钥(明文,可直接从 JS 提取),用 `window.JSEncrypt`(SDK 已暴露到全局)的 `decryptLong()` 解密,得到:
   ```json
   { "bucket": "meizu-storage", "region": "oss-cn-shanghai",
     "accessKeyId": "STS.xxx", "accessKeySecret": "xxx",
     "securityToken": "xxx", "expiredTime": "2026-09-23T08:10:00Z" }
   ```
3. **签名算法**(阿里云 OSS SigV1,复刻 SDK `signUrl`):
   ```
   StringToSign = "GET\n\n\n{Expires}\n/{bucket}/{objectKey}?response-content-disposition=attachment;filename={objectKey}&security-token={securityToken}"
   Signature    = base64( HMAC-SHA1(accessKeySecret, StringToSign) )
   ```
4. **原图 URL**:`photo.url` 字段就是原图 object key(如 `d2/24/59/4a/xxx.jpg`),而 `thumb256`/`thumb1024` 带 `@256w_2o.webp` 后缀是缩略图。签名对 object key(含 style 后缀)敏感,不能复用。

## 四、踩坑复盘

### 坑 #1:token 过期 → 页面被重定向,误报 CORS

**现象**:`user/info` 报「Failed to fetch」,一度以为是 CORS 问题。

**诊断**:打印 `page.url()` 发现实际落在 `login.flyme.cn` 而非 `photos.flyme.cn`;解码 token 里的 `cts` 时间戳,发现 token 是 32 天前抓的。

**根因**:`_utoken` 失效 → flyme 前端把页面 302 到登录域 → 登录域没有对 mzstorage 的 CORS 授权 → fetch 失败。

**解决**:
1. 重新登录、重新导出 cookie。
2. 脚本加「导航后重定向检测」:落地页不是 `photos.flyme.cn` 立即清晰报错,而非走到错误域再报误导性的 CORS。

**教训**:「Failed to fetch」往往是 CORS 的表象,真因可能是登录态失效导致的重定向。先看 `page.url()` 再下结论。

### 坑 #2:cookie 字段名搞错

**现象**:反复确认 `token`,但 SDK 实际用的是 `_utoken`。

**解决**:统一用 `_utoken`;同时发现 SDK 会因 token 过期主动清空 cookie 值,加了「等待 SDK 自动 refresh 后重读」的兜底逻辑。

### 坑 #3:下载的是缩略图,不是原图(最致命)

**现象**:下载的"照片"只有 1~26 KB,而元数据 `photo.size` 是几百 KB ~ 几 MB;文件头是 `RIFF...WEBP` 而非 `FFD8` JPEG。

**根因**:Phase 3 脚本依赖 SDK 预加载的缩略图缓存(`ossCache` 里全是 `@256w_2o.webp`),下载的自然就是 256px 缩略图。

**解决**:放弃缩略图缓存路线,改为逆向签名机制、直接下载 `photo.url` 指向的原图(见坑 #4)。

**教训**:「链路通了」≠「产物正确」。必须用文件 magic bytes 和 size 校验产物,而不是只看 HTTP 200。

### 坑 #4:OSS 私有桶签名(核心难点)

**现象**:直接 `fetch` 原图 URL 返回 403(OSS 私有桶需签名)。

**探索路径**(记录踩坑轨迹):
1. 复用缩略图的签名参数、只改 object key → 403。**结论**:签名绑定 object key(含 `@256w_2o.webp` 后缀)。
2. 抓 `file/get_sig/v2` 响应 → 是一串 base64 长串,不是明文 STS。
3. 搜索 SDK JS 源码(`app.5b54c2747.js`,webpack 压缩单行)→ 找到:
   - `setPrivateKey("MIIC...")` RSA 私钥明文
   - `t.value = JSON.parse(i.decryptLong(t.value))` 解密逻辑
   - `signUrl(t)` 签名函数 + 完整 OSS SigV1 公式
4. 确认 `window.JSEncrypt` 已被 SDK 暴露到全局,可直接复用。

**解决**:浏览器端解密 STS(一次性),Node 端用 `crypto` 模块复刻签名 + 下载。**最终方案验证**:下载文件 `size` 与 `photo.size` 完全一致,`contentType=image/jpeg`,magic `ffd8ffe0`。

**教训**:
- 逆向不走「破解加密」,走「复用 SDK 已加载的能力」(JSEncrypt + 内置私钥)。
- 压缩的 webpack 代码也能 grep,关键是搜对关键词(`security-token`、`OSSAccessKeyId`、`setPrivateKey`、`decryptLong`)。

### 坑 #5:`album/dir/list` 返回 400

**现象**:全相册模式调 `album/dir/list` 报 HTTP 400。

**诊断**:抓 SDK 实际请求,响应里明确写 `Required Integer parameter 'limit' is not present`。

**解决**:加 `limit=100`。同时发现真正的相册列表接口就是 `album/dir/list`(返回 `dir[]`,字段是 `id`/`dirName`/`fileNum`),而 `album/group` 是时间轴分组视图,不是相册目录。

**教训**:接口报 400 时,看响应体里的具体报错信息(而不是只记 HTTP 状态码),往往直接告诉缺什么参数。

### 坑 #6:单相册模式目录名不一致 → 断点续传失效

**现象**:单相册模式 `--album 76284` 把文件下到 `Dir_76284/`,而全相册模式用真实名 `DCIM/`,导致同一批照片重复下载到两个目录。

**根因**:单相册模式直接用 dirId 拼目录名,没查真实 `dirName`。

**解决**:单相册模式也先调 `album/dir/list` 查到真实 `dirName`,保证目录名与全相册一致。

**教训**:断点续传的「跳过」依赖**目录 + 文件名 + size** 三者一致,任何一者漂移都会导致重复下载。目录名要单一来源(dirName),不要用临时拼凑的标识。

### 坑 #7:STS 凭证过期(1 小时)

**现象**:STS 约 1 小时过期,1220 个文件长任务可能中途 403。

**解决**:加模块级缓存 + `expiredTime` 检查(提前 5 分钟刷新)+ 并发锁(避免多个 worker 同时触发解密)。

### 坑 #8:视频被静默跳过

**现象**:DCIM 显示 707 项,只下了 636,差额 71。

**根因**:71 项是 `isVideo=true` 的视频,脚本默认过滤。

**解决**:加 `--video` 参数。视频的 `url` 字段同样是 mp4 的 object key,签名下载逻辑与照片完全一致,无需额外处理。

### 坑 #9(工程):PowerShell 转义

**现象**:在 Windows PowerShell 里内联 `node -e` 脚本时,`$_`、`$变量` 被吞,单引号嵌套冲突。

**解决**:需要内联脚本时,一律写成临时 `.js` 文件再 `node 文件.js`,避免 shell 转义。

## 五、最终成果

| 相册 | 照片 | 视频 | 合计 | 大小 |
| --- | --- | --- | --- | --- |
| DCIM | 636 | 71 | 707 | 2607.26 MB |
| Camera | 285 | 0 | 285 | 109.13 MB |
| Screenshots | 228 | 0 | 228 | 63.55 MB |
| **总计** | **1149** | **71** | **1220** | **约 2.78 GB** |

- 照片:全部原图 JPEG(magic `FFD8FF`,size 与元数据一致)
- 视频:全部有效 MP4(文件头 `ftyp`,单个 0.44 MB ~ 107.41 MB)
- 断点续传:重跑时 706/707 自动 skip,验证通过

## 六、经验总结

1. **方向比努力重要**:从「破解 sign/STS」转向「复用浏览器内核」,是整个项目能走通的分水岭。
2. **「链路通」≠「结果对」**:一定要校验产物(文件头、size),缩略图 vs 原图这种错误,HTTP 200 看不出来。
3. **报错要看响应体**:400 的错误信息直接告诉缺 `limit`;`Failed to fetch` 要先查 `page.url()` 看是否被重定向。
4. **逆向优先复用 SDK 能力**:SDK 已经暴露 `window.JSEncrypt`、内置 RSA 私钥,直接复用比从零实现 RSA 解密高效得多。
5. **断点续传要单一事实来源**:目录名、文件名、size 三者必须稳定,否则重复下载。
6. **长任务必须处理凭证刷新**:STS 1 小时过期,必须带缓存 + 过期检查 + 并发锁。
7. **工程细节**:Windows 下内联脚本写临时 `.js` 文件,绕开 shell 转义。

## 七、使用方法(当前)

```bash
# 只备份照片(默认)
node scripts/flyme_export.js --cookie-file cookies.json

# 照片 + 视频
node scripts/flyme_export.js --cookie-file cookies.json --video

# 单相册 + 快速验证
node scripts/flyme_export.js --cookie-file cookies.json --album 76227 --limit 1
```

cookie 失效后:重新登录 photos.flyme.cn → 重新导出 `cookies.json` → 重跑同命令,已下载文件自动跳过。
