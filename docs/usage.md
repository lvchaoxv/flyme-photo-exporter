# Flyme 云相册导出工具 — 使用指南

> 面向使用者的完整操作手册。即使你不懂技术,跟着步骤走也能完成备份。

---

## 一、这个工具能做什么

把你魅族云相册(https://photos.flyme.cn)里的**全部照片(原图)和视频**下载到本地电脑,保存成普通文件,方便随时查看、备份、转移。

**特点**:
- 下载的是**原图**(不是缩略图),清晰度和你手机里的一样
- 支持**断点续传**:中途断了,再跑一次会自动跳过已下载的,不用从头再来
- 按相册分文件夹,文件名保持原样

**不支持 / 注意**:
- 仅限备份**你自己的账号**(合规要求)
- 需要你能正常登录 photos.flyme.cn

---

## 二、准备工作(一次性)

### 2.1 安装 Node.js

1. 打开 https://nodejs.org
2. 下载 **LTS 版本**(如 18.x 或 20.x),Windows 选 `.msi` 安装包
3. 一路「下一步」装完

验证安装成功:按 `Win + R`,输入 `cmd` 回车,在黑色窗口输入:

```bash
node --version
```

能打印出版本号(如 `v20.11.0`)就说明装好了。

### 2.2 安装工具依赖

在项目目录打开命令行(见 2.3),执行:

```bash
npm install
npx playwright install chromium
```

第二条命令会下载一个 Chromium 浏览器(约 150MB),**耐心等待**,这是正常现象。

### 2.3 打开命令行到项目目录

- 方法一:文件资源管理器打开 `flyme-photo-exporter` 文件夹,在地址栏输入 `cmd` 回车
- 方法二:右键文件夹空白处 →「在终端中打开」

后续所有命令都在这个目录下执行。

---

## 三、获取登录 cookie(最关键的一步)

cookie 是你的登录凭证,工具靠它才能访问你的相册。**这步做对了,后面就顺了。**

### 3.1 先登录

用 Chrome 浏览器打开 https://photos.flyme.cn 并**登录成功**(看到相册列表页面才算成功)。

### 3.2 导出 cookie

推荐用 **EditThisCookie 扩展**(最简单):

1. Chrome 应用商店搜索安装 **EditThisCookie**
2. 在已登录的相册页面,点工具栏的饼干图标
3. 点右上角的 **导出(Export)** 按钮
4. 保存为 JSON 文件,放到项目目录下,命名为 `cookies.json`

> 也可以用「Cookie Editor」等同类扩展,只要能导出 JSON 即可。

**没有扩展?用 DevTools 手动导出**:

1. 在相册页面按 `F12` 打开开发者工具
2. 点 **Application(应用)** 标签 → 左侧 **Cookies** → 展开各域名
3. 依次查看 `https://photos.flyme.cn`、`.flyme.cn`、`.meizu.com` 三个域
4. 记录下所有 cookie 的 `名称=值`

### 3.3 必须包含的 cookie

导出后检查一下,至少要包含这几个:

| cookie 名 | 说明 |
| --- | --- |
| `_utoken` | **最关键**,主登录凭证 |
| `DSESSIONID` | 会话凭证(可能有两个域) |
| `JSESSIONID` | 登录会话 |

> ⚠️ 注意是 `_utoken`(带下划线),不是 `token`。少了它工具一定跑不通。

### 3.4 cookie 有效期

`_utoken` 有效期**约 8 小时**。所以:
- 导出后**尽快**运行工具
- 如果隔天再跑,大概率要**重新登录、重新导出**

---

## 四、运行工具

### 4.1 先小范围测试(强烈建议)

只下载 Screenshots 相册的前 1 页(约 34 张),验证流程能跑通:

```bash
node scripts/flyme_export.js --cookie-file cookies.json --album 76227 --limit 1
```

看到类似输出就说明成功了:

```
登录态 OK (fileNum=1220, vip=体验版)
STS 凭证就绪: bucket=meizu-storage region=oss-cn-shanghai
相册完成: ok=34  skip=0  fail=0
```

### 4.2 正式备份全部照片

```bash
node scripts/flyme_export.js --cookie-file cookies.json
```

会依次下载 3 个相册(按字母序:DCIM → Camera → Screenshots)。

### 4.3 照片 + 视频一起备份

```bash
node scripts/flyme_export.js --cookie-file cookies.json --video
```

> 视频文件较大(单个可达 100MB+),会明显增加下载时间和磁盘占用。

### 4.4 只备份某一个相册

```bash
# 只备份 DCIM
node scripts/flyme_export.js --cookie-file cookies.json --album 76284

# 只备份 Camera
node scripts/flyme_export.js --cookie-file cookies.json --album 76228

# 只备份 Screenshots
node scripts/flyme_export.js --cookie-file cookies.json --album 76227
```

相册对应的 id:

| 相册 | dirId |
| --- | --- |
| DCIM | 76284 |
| Camera | 76228 |
| Screenshots | 76227 |

### 4.5 指定输出目录

```bash
node scripts/flyme_export.js --cookie-file cookies.json --out D:\backup\flyme
```

### 4.6 调整下载速度

```bash
# 并发 3(更保守,降低风控风险)
node scripts/flyme_export.js --cookie-file cookies.json --concurrency 3

# 并发 8(更快,但可能触发限流)
node scripts/flyme_export.js --cookie-file cookies.json --concurrency 8
```

默认并发 5,一般无需调整。

---

## 五、输出结果在哪

默认下载到项目目录下的 `photos/` 文件夹:

```
photos/
├── DCIM/            # 636 张照片 + 71 个视频
│   ├── IMG_20200101_120000.jpg
│   └── V00730-182650.mp4
├── Camera/          # 285 张照片
└── Screenshots/     # 228 张照片
```

- 照片是 `.jpg` 原图,视频是 `.mp4`
- 文件名保持云相册里的原名

---

## 六、断点续传

工具自动支持:下载时,如果发现同名文件且大小一致,会**自动跳过**。

所以如果中途中断(网络断了、token 过期、电脑重启),只要:

1. 重新登录 photos.flyme.cn
2. 重新导出 cookie 覆盖 `cookies.json`
3. **重跑同一条命令**

已下载的文件会被跳过,只补下缺的部分。

---

## 七、完整参数表

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `--cookie-file <文件>` | 从 JSON 文件读 cookie | - |
| `--cookie "<字符串>"` | 直接传 cookie 字符串 | - |
| `--album <id>` | 只备份一个相册 | 全部 |
| `--out <目录>` | 输出目录 | `./photos` |
| `--limit <页数>` | 每相册只下前 N 页(测试用) | 全部 |
| `--concurrency <n>` | 并发下载数 | 5 |
| `--retry <n>` | 单文件重试次数 | 3 |
| `--video` | 同时下载视频 | 关闭 |
| `--headless false` | 显示浏览器窗口(调试用) | 隐藏 |

---

## 八、常见问题排查

### Q1:报错「导航后跳到 login.flyme.cn」

**原因**:cookie 过期了(最常见)。

**解决**:重新登录 → 重新导出 cookie → 重跑。

### Q2:报错「登录态验证失败:user/info 返回 code=401」

**原因**:`_utoken` 过期或被浏览器清掉。

**解决**:同上,重新登录导出。特别注意 cookie 里有没有 `_utoken`。

### Q3:下载的照片打不开 / 只有几 KB

**原因**:下载到了缩略图(异常情况)。

**解决**:联系工具维护者;正常情况下工具下载的是原图,单张照片应有几百 KB ~ 几 MB。

### Q4:下载速度慢

- 原图 + 视频本身就大(总量约 2.8GB),属正常
- 可适当提高 `--concurrency`,但别超过 8,否则可能触发限流

### Q5:中途报「OSS HTTP 403」

**原因**:STS 临时凭证过期(约 1 小时)。

**解决**:工具会自动刷新,一般不用管。若反复出现,重跑一次即可(断点续传会跳过已下载的)。

### Q6:视频为什么没下载?

**原因**:默认只下载照片。视频需要加 `--video` 参数。

---

## 九、安全与合规

- ⚠️ **仅限备份本人账号**,不要用于他人账号或商业用途
- ⚠️ cookie 包含登录凭证,`cookies.json` 已加入 `.gitignore`,**不要上传到 git 或发给别人**
- ⚠️ 下载频率过高可能触发 flyme 风控,建议用默认并发
- ⚠️ 使用本工具的一切后果由使用者自行承担

---

## 十、快速上手速查

```bash
# 1. 装依赖(首次)
npm install && npx playwright install chromium

# 2. 登录 photos.flyme.cn 后,导出 cookies.json 放到项目目录

# 3. 测试(1 页)
node scripts/flyme_export.js --cookie-file cookies.json --album 76227 --limit 1

# 4. 全量备份照片
node scripts/flyme_export.js --cookie-file cookies.json

# 5. 照片 + 视频
node scripts/flyme_export.js --cookie-file cookies.json --video
```
