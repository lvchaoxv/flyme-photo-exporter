/**
 * Flyme 云相册导出工具 (Phase 4 — 原图下载)
 *
 * 架构:Node.js + Playwright(headless Chromium)
 *   - 浏览器端仅做一件事:用 SDK 暴露的 JSEncrypt + 内置 RSA 私钥解密
 *     file/get_sig/v2 返回的加密 STS 凭证(得到 accessKeyId/accessKeySecret/
 *     securityToken/bucket/region)。
 *   - 其余全部在 Node 端完成:用 crypto 模块按阿里云 OSS SigV1 算法对每张
 *     照片的原图 object key 计算签名 URL,再直接 fetch 下载原图。
 *   - 不破 sign / 不解析 STS / 不下载视频 / 不依赖 SDK 预加载的缩略图。
 *
 * 用法:
 *   node scripts/flyme_export.js --cookie-file cookies.json
 *   node scripts/flyme_export.js --cookie "name1=value1; name2=value2"
 *   node scripts/flyme_export.js --album 76227              # 单相册(断点续传友好)
 *   node scripts/flyme_export.js --out ./photos             # 输出目录
 *   node scripts/flyme_export.js --limit 1                  # 每相册前 N 页(快速验证)
 */

'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const process = require('node:process');
const crypto = require('node:crypto');

// ============ 配置常量 ============
const PHOTOS_ORIGIN = 'https://photos.flyme.cn';
const DEFAULT_OUT   = path.resolve(process.cwd(), 'photos');
const PAGE_LIMIT    = 34;                    // album/list 单页默认数量
const CONCURRENCY   = 5;                     // Node 端并发下载(原图,不受浏览器限制)
const RETRY_MAX     = 3;                     // 单文件下载重试次数
const NAV_TIMEOUT_MS    = 60000;

// SDK 内置 RSA 私钥(前端明文,用于解密 file/get_sig/v2 返回的加密 STS 凭证)
const PRIVATE_KEY =
  'MIICdgIBADANBgkqhkiG9w0BAQEFAASCAmAwggJcAgEAAoGBAJpODQgsoTzXkDDx9x1TZ8UZu70YxTH' +
  'gt+mEVxho8b57p9h8WaELWzSp43DVuxl60ral2Ri4jieUlZoioy+f6zqK8ng8QgvDzDGOlEjPj0kV' +
  '35oVHouZvY5bc9bhPsqVpVummQDOgGM6pf7YWAx9lasKK/TMPvzDtqMVwlXsZXLdAgMBAAECgYBzG' +
  '5B7LZfmZERLTuVyOfrqPOUhDi5ko+duSuwR6I+V8nbmdvUBvw/9vFJPpREa09YGrLfDykE5Y40qW3' +
  'Zym5CEgajLvZHTopVCBPpK/xLJjcw/tPnE5ky++ytXZ6QFAVQg41lGuC6qBqSdnB8JRsnN+XDXn/p' +
  'UIqBlBX1pb43I4QJBAOvfd/cT9pBHcROcFjAQchQHrihA3tm0iQBkgCy+o6AYuaC+PWY1mCDGaXHr' +
  'c2+F4/Tv4tEt1FnXZ2sZytxRWwUCQQCneMR4HGy9jKJ5jdejtjy4Y5E0TDhXJyjukk1VBMvK31RYJ' +
  'e+e6RkAK4UUa2g6E/Z3JRkXtyDxB6oviKTMti/5AkEAzLc3N4psBOz8hziBSVX8rMW9sdIbmHfIMD' +
  '8Jv8v1142eDpUOVRdO4aNTATyJA9IA9yT8hvBvzUnWyG2qU22IwQJAJ0QTnK3deRvuRF3Tf5kM55b' +
  'AxuhQFW8jE7zN0O9M8QYn+nr6keHJcNbDXyRHzcY8dXcHSR4w5RKM/pQlP7I/0QJAUsKSNIC9Bc1S' +
  'zfghocg+G2PtmXoJkSftbm3fkRbIWkCGxH36XZmKcULMSs6OQZAmcv7Q5Gj6X/0W25rTN/G5UQ==';

// ============ CLI 解析 ============
function parseArgs(argv) {
  const out = {
    cookieFile: null,
    cookieStr: null,
    album: null,       // 单相册模式:仅导这个 dirId
    outDir: DEFAULT_OUT,
    limit: null,       // 限制每个相册前 N 页(快速测试)
    headless: true,
    concurrency: CONCURRENCY,
    retry: RETRY_MAX,
    video: false,      // 是否同时下载视频(isVideo=true)
    help: false,
  };

  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    switch (a) {
      case '--cookie':         out.cookieStr  = next(); break;
      case '--cookie-file':    out.cookieFile = next(); break;
      case '--album':          out.album      = Number(next()); break;
      case '--out':            out.outDir     = path.resolve(next()); break;
      case '--limit':          out.limit      = Number(next()); break;
      case '--concurrency':    out.concurrency = Number(next()); break;
      case '--retry':          out.retry      = Number(next()); break;
      case '--video':          out.video      = true; break;
      case '--headless': {
        const v = String(next()).toLowerCase();
        out.headless = !(v === 'false' || v === '0' || v === 'no');
        break;
      }
      case '-h':
      case '--help':           out.help = true; break;
      default:
        if (a.startsWith('-')) {
          console.error(`未知参数: ${a}`);
          process.exit(2);
        }
    }
  }
  return out;
}

function printHelp() {
  console.log(`用法: node scripts/flyme_export.js [选项]

必选(提供 cookie):
  --cookie "name=val; name2=val2"     直接粘贴 cookie 字符串
  --cookie-file path/to/cookies.json  从文件读取 cookie(JSON 数组)

可选:
  --album <dirId>     仅导一个相册(配合断点续传)
  --out <dir>         输出目录(默认 ./photos)
  --limit <pages>     每个相册前 N 页(默认全部;快速测试用 --limit 1)
  --concurrency <n>   并发下载数(默认 5)
  --retry <n>         单文件重试次数(默认 3)
  --video             同时下载视频(isVideo=true,默认跳过)
  --headless false    有头模式(便于调试)

示例:
  node scripts/flyme_export.js --cookie-file cookies.json
  node scripts/flyme_export.js --cookie-file cookies.json --album 76227 --limit 1
  node scripts/flyme_export.js --cookie-file cookies.json --video
`);
}

// ============ Cookie 加载 ============
async function loadCookies(args) {
  if (!args.cookieFile && !args.cookieStr) {
    throw new Error('必须提供 --cookie 或 --cookie-file');
  }
  if (args.cookieFile) {
    const raw = await fs.readFile(args.cookieFile, 'utf8');
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed.map(normalizeCookie);
    if (Array.isArray(parsed.cookies)) return parsed.cookies.map(normalizeCookie);
    throw new Error('cookie 文件格式:期望 JSON 数组,或含 cookies 字段的对象');
  }
  return args.cookieStr.split(/;\s*/).filter(Boolean).map(pair => {
    const idx = pair.indexOf('=');
    if (idx < 0) throw new Error(`非法 cookie: ${pair}`);
    return normalizeCookie({ name: pair.slice(0, idx).trim(), value: pair.slice(idx + 1).trim() });
  });
}

function normalizeCookie(c) {
  const domain = c.domain || '.flyme.cn';
  const out = {
    name: c.name,
    value: c.value,
    domain: domain.startsWith('.') ? domain : '.' + domain,
    path: c.path || '/',
  };
  if (c.expires && c.expires > 0) out.expires = c.expires;
  if (c.httpOnly) out.httpOnly = true;
  if (c.secure)   out.secure   = true;
  if (c.sameSite) out.sameSite = c.sameSite;
  return out;
}

// ============ OSS 签名(阿里云 SigV1,复刻 flyme SDK signUrl) ============
function signUrl(objectKey, sig) {
  const H = Math.floor(Date.now() / 1000) + 3600;  // Expires = now + 1h
  const stringToSign =
    'GET\n\n\n' + H + '\n/' + sig.bucket + '/' + objectKey +
    '?response-content-disposition=attachment;filename=' + objectKey +
    '&security-token=' + sig.securityToken;
  const signature = crypto.createHmac('sha1', sig.accessKeySecret)
    .update(stringToSign)
    .digest('base64');
  return 'https://' + sig.bucket + '.' + sig.region + '.aliyuncs.com/' + objectKey +
    '?OSSAccessKeyId=' + encodeURIComponent(sig.accessKeyId) +
    '&Expires=' + H +
    '&Signature=' + encodeURIComponent(signature) +
    '&security-token=' + encodeURIComponent(sig.securityToken) +
    '&response-content-disposition=attachment;filename=' + encodeURIComponent(objectKey);
}

// ============ STS 凭证缓存 + 自动刷新 ============
const sigState = { sig: null, expiry: 0, inflight: null };

async function getFreshSig(page) {
  const now = Date.now();
  // 还有 5 分钟以上余量,直接复用
  if (sigState.sig && now < sigState.expiry - 5 * 60 * 1000) {
    return sigState.sig;
  }
  // 已有在途刷新请求,复用它(避免并发重复解密)
  if (sigState.inflight) return sigState.inflight;
  sigState.inflight = (async () => {
    const sig = await page.evaluate(() => window.__flymeHelpers.getSig());
    sigState.sig = sig;
    sigState.expiry = new Date(sig.expiredTime || sig.expireTime).getTime();
    console.log(`  STS 凭证已刷新, 过期=${new Date(sigState.expiry).toLocaleString()}`);
    return sig;
  })();
  const sig = await sigState.inflight;
  sigState.inflight = null;
  return sig;
}

// ============ 浏览器内 helper(仅 getSig 解密 + 相册接口) ============
const BROWSER_HELPERS = `
window.__flymeHelpers = {
  async postJSON(url, body) {
    const search = new URLSearchParams(body).toString();
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'content-type': 'application/x-www-form-urlencoded',
        'accept': 'application/json, text/plain, */*',
        'x-requested-with': 'XMLHttpRequest',
      },
      body: search,
      credentials: 'include',
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return resp.json();
  },

  async getSig() {
    const token = getCookie('_utoken') || '';
    const r = await this.postJSON('https://mzstorage.meizu.com/file/get_sig/v2', {
      type: '2',
      cts: String(Date.now()),
      token,
    });
    if (r.code !== 200) throw new Error('file/get_sig/v2 failed: ' + (r.message || ''));
    const crypt = new window.JSEncrypt();
    crypt.setPrivateKey('${PRIVATE_KEY}');
    return JSON.parse(crypt.decryptLong(r.value));
  },

  async checkLogin() {
    const token = getCookie('_utoken') || '';
    const r = await this.postJSON('https://mzstorage.meizu.com/user/info', {
      type: '0',
      cts: String(Date.now()),
      token,
    });
    return r;
  },

  async getAlbumDirs() {
    const token = getCookie('_utoken') || '';
    const r = await this.postJSON('https://mzstorage.meizu.com/album/dir/list', {
      limit: '100',
      order: '1',
      cts: String(Date.now()),
      token,
    });
    if (r.code !== 200) throw new Error('album/dir/list failed: ' + (r.message || ''));
    return (r.value && r.value.dir) ? r.value.dir : [];
  },

  async getAlbumPhotos(dirId, offset, limit) {
    const token = getCookie('_utoken') || '';
    const r = await this.postJSON('https://mzstorage.meizu.com/album/list', {
      limit: String(limit),
      offset: String(offset),
      order: '1',
      isWebp: 'true',
      dirId: String(dirId),
      cts: String(Date.now()),
      token,
    });
    if (r.code !== 200) throw new Error('album/list failed: ' + (r.message || ''));
    return r.value || { file: [], count: 0, end: 0 };
  },
};

function getCookie(name) {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : '';
}
`;

// ============ 主流程 ============
async function main() {
  const args = parseArgs(process.argv);
  if (args.help) { printHelp(); return; }

  console.log('===== Flyme 相册导出工具 (Phase 4 — 原图下载) =====');
  console.log(`输出目录: ${args.outDir}`);
  console.log(`并发数:   ${args.concurrency}`);
  console.log(`重试次数: ${args.retry}`);
  console.log(`模式:     ${args.headless ? 'headless' : 'headed'}${args.album ? `, 单相册 dirId=${args.album}` : ', 全相册'}`);
  if (args.limit) console.log(`每相册仅前 ${args.limit} 页(快速测试)`);
  if (args.video) console.log('包含视频: 是(照片 + 视频)');

  await fs.mkdir(args.outDir, { recursive: true });

  const cookies = await loadCookies(args);
  console.log(`已加载 ${cookies.length} 个 cookie`);

  console.log('启动 Chromium ...');
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ headless: args.headless });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    viewport: { width: 1440, height: 900 },
  });
  await context.addCookies(cookies);
  const page = await context.newPage();
  page.setDefaultNavigationTimeout(NAV_TIMEOUT_MS);

  try {
    // 1. 打开站点,加载前端 SDK(拿到 window.JSEncrypt)
    console.log(`打开 ${PHOTOS_ORIGIN} 加载前端 SDK ...`);
    try {
      await page.goto(PHOTOS_ORIGIN, { waitUntil: 'networkidle', timeout: NAV_TIMEOUT_MS });
    } catch (e) {
      console.warn('导航警告:', e.message);
    }
    const landedAt = page.url();
    if (!landedAt.startsWith(PHOTOS_ORIGIN)) {
      throw new Error(`导航后跳到 ${landedAt} 而非 ${PHOTOS_ORIGIN}\n` +
        `  cookie 已过期或不全。请在 Chrome 重新登录 photos.flyme.cn,刷新页面后立刻复制 cookie。`);
    }

    // 注入 helper
    await page.addScriptTag({ content: BROWSER_HELPERS });

    // 2. 验证登录态
    console.log('验证登录态 ...');
    const loginResp = await page.evaluate(() => window.__flymeHelpers.checkLogin());
    if (loginResp.code !== 200) {
      throw new Error(`登录态验证失败:user/info 返回 code=${loginResp.code} message="${loginResp.message || ''}"`);
    }
    const userInfo = loginResp.value || {};
    console.log(`登录态 OK (fileNum=${userInfo.fileNum}, vip=${userInfo.vipName})`);

    // 3. 解密 STS 凭证(浏览器端,带缓存 + 自动刷新)
    console.log('获取 OSS STS 凭证 ...');
    const sig = await getFreshSig(page);
    console.log(`STS 凭证就绪: bucket=${sig.bucket} region=${sig.region}`);

    // 4. 获取相册列表
    let dirs;
    if (args.album) {
      // 单相册模式:查相册列表拿真实 dirName,保证目录名与全相册一致(断点续传生效)
      const allDirs = await page.evaluate(() => window.__flymeHelpers.getAlbumDirs());
      const found = allDirs.find(d => (d.id ?? d.dirId) === args.album);
      dirs = [found || { dirId: args.album, dirName: `Dir_${args.album}` }];
      console.log(`单相册模式: dirId=${args.album}${found ? ` dirName=${found.dirName}` : ''}`);
    } else {
      console.log('获取相册列表 album/dir/list ...');
      dirs = await page.evaluate(() => window.__flymeHelpers.getAlbumDirs());
      console.log(`共 ${dirs.length} 个相册`);
      for (const d of dirs) {
        const cnt = d.fileNum ?? d.fileCount ?? d.count ?? '?';
        console.log(`  - id=${d.id ?? d.dirId}  dirName=${d.dirName}  count=${cnt}`);
      }
    }

    // 5. 逐相册导出(签名 + 下载在 Node 端)
    const stats = { total: 0, downloaded: 0, skipped: 0, failed: 0, bytes: 0 };
    for (const dir of dirs) {
      const dirStats = await exportAlbum(page, args, dir);
      stats.total      += dirStats.total;
      stats.downloaded += dirStats.downloaded;
      stats.skipped    += dirStats.skipped;
      stats.failed     += dirStats.failed;
      stats.bytes      += dirStats.bytes;
    }

    // 6. 收尾统计
    console.log('\n===== 完成 =====');
    console.log(`总照片:     ${stats.total}`);
    console.log(`已下载:     ${stats.downloaded}`);
    console.log(`跳过(已存在): ${stats.skipped}`);
    console.log(`失败:       ${stats.failed}`);
    console.log(`总字节:     ${(stats.bytes / 1024 / 1024).toFixed(2)} MB`);
  } finally {
    await context.close();
    await browser.close();
  }
}

// ============ 单相册导出 ============
async function exportAlbum(page, args, dir) {
  const dirId = dir.id ?? dir.dirId;
  const dirName = sanitizeFilename(dir.dirName || `dir_${dirId}`);
  const albumDir = path.join(args.outDir, dirName);
  await fs.mkdir(albumDir, { recursive: true });

  console.log(`\n--- 相册 ${dirName} (dirId=${dirId}) ---`);

  // 翻页拿照片列表
  const photos = [];
  let offset = 0;
  let pageCount = 0;
  const maxPages = args.limit;

  while (true) {
    pageCount++;
    const value = await page.evaluate(
      ({ dirId, offset, limit }) => window.__flymeHelpers.getAlbumPhotos(dirId, offset, limit),
      { dirId, offset, limit: PAGE_LIMIT }
    );
    const files = value.file || [];
    const items = args.video ? files : files.filter(p => !p.isVideo);
    photos.push(...items);
    console.log(`  第 ${pageCount} 页: 本页 ${files.length} 项, 累计 ${photos.length} / ${value.count}`);
    if (value.end === 1 || files.length === 0) break;
    if (maxPages && pageCount >= maxPages) {
      console.log(`  已达 --limit 上限`);
      break;
    }
    offset += PAGE_LIMIT;
  }

  console.log(`  共 ${photos.length} 张待下载,开始并发拉取(并发=${args.concurrency})`);

  // 并发下载(签名 + 下载都在 Node 端)
  const st = { total: photos.length, downloaded: 0, skipped: 0, failed: 0, bytes: 0 };
  let cursor = 0;
  async function worker() {
    while (true) {
      const idx = cursor++;
      if (idx >= photos.length) return;
      const p = photos[idx];
      const sig = await getFreshSig(page);   // 每次检查(便宜),过期自动刷新
      const r = await downloadOne(p, albumDir, sig, args.retry);
      if (r === 'ok')        { st.downloaded++; st.bytes += p.size || 0; }
      else if (r === 'skip') { st.skipped++; }
      else                   { st.failed++; }
      const done = st.downloaded + st.skipped + st.failed;
      if (done % 10 === 0 || done === st.total) {
        console.log(`    进度: ${done}/${st.total}  ok=${st.downloaded}  skip=${st.skipped}  fail=${st.failed}`);
      }
    }
  }
  const workers = Array.from({ length: args.concurrency }, worker);
  await Promise.all(workers);

  console.log(`  相册完成: ok=${st.downloaded}  skip=${st.skipped}  fail=${st.failed}`);
  return st;
}

// ============ 单文件下载(断点续传 + 指数退避重试) ============
async function downloadOne(photo, albumDir, sig, retryMax) {
  const fileName = photo.fileName || `${photo.id}.jpg`;
  const filePath = path.join(albumDir, fileName);

  // 断点续传:同名 + size 一致则跳过
  try {
    const existing = await fs.stat(filePath);
    if (existing.size === photo.size) return 'skip';
  } catch { /* 不存在 */ }

  for (let attempt = 1; attempt <= retryMax; attempt++) {
    try {
      const url = signUrl(photo.url, sig);   // Node 端签名
      const resp = await fetch(url);          // Node 端下载
      if (!resp.ok) throw new Error('OSS HTTP ' + resp.status);
      const buf = Buffer.from(await resp.arrayBuffer());
      if (buf.length === 0) throw new Error('empty response');
      await fs.writeFile(filePath, buf);
      return 'ok';
    } catch (e) {
      if (attempt === retryMax) {
        console.error(`    [fail] ${fileName}: ${e.message}`);
        return 'fail';
      }
      const delay = 1000 * Math.pow(2, attempt - 1);
      await new Promise(r => setTimeout(r, delay));
    }
  }
  return 'fail';
}

// ============ 工具 ============
function sanitizeFilename(s) {
  return String(s).replace(/[\\/:*?"<>|]/g, '_').trim() || 'untitled';
}

// ============ 入口 ============
main().catch(err => {
  console.error('\n[ERROR]', err.message);
  if (process.env.DEBUG) console.error(err.stack);
  process.exit(1);
});
