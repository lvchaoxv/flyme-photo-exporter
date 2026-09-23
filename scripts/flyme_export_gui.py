# -*- coding: utf-8 -*-
"""
Flyme 云相册导出工具 — GUI 版 (Tkinter)

功能:
  - 输入 _utoken(或读取 cookies.json)
  - 连接验证登录态,列出相册
  - 勾选相册、选择是否下载视频
  - 后台线程下载原图/视频,进度条 + 日志实时更新
  - 断点续传(同名 + size 一致跳过)

依赖: requests, cryptography (均已通过 requirements 安装)
运行: python scripts/flyme_export_gui.py
"""

import json
import base64
import time
import hmac
import hashlib
import os
import threading
import queue

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ============ 常量 ============
API_BASE = 'https://mzstorage.meizu.com'
PAGE_LIMIT = 34
CONCURRENCY = 5

# flyme 前端 SDK 内置 RSA 私钥(用于解密 file/get_sig/v2 返回的加密 STS 凭证)
PRIVATE_KEY_B64 = (
    'MIICdgIBADANBgkqhkiG9w0BAQEFAASCAmAwggJcAgEAAoGBAJpODQgsoTzXkDDx9x1TZ8UZu70YxTH'
    'gt+mEVxho8b57p9h8WaELWzSp43DVuxl60ral2Ri4jieUlZoioy+f6zqK8ng8QgvDzDGOlEjPj0kV'
    '35oVHouZvY5bc9bhPsqVpVummQDOgGM6pf7YWAx9lasKK/TMPvzDtqMVwlXsZXLdAgMBAAECgYBzG'
    '5B7LZfmZERLTuVyOfrqPOUhDi5ko+duSuwR6I+V8nbmdvUBvw/9vFJPpREa09YGrLfDykE5Y40qW3'
    'Zym5CEgajLvZHTopVCBPpK/xLJjcw/tPnE5ky++ytXZ6QFAVQg41lGuC6qBqSdnB8JRsnN+XDXn/p'
    'UIqBlBX1pb43I4QJBAOvfd/cT9pBHcROcFjAQchQHrihA3tm0iQBkgCy+o6AYuaC+PWY1mCDGaXHr'
    'c2+F4/Tv4tEt1FnXZ2sZytxRWwUCQQCneMR4HGy9jKJ5jdejtjy4Y5E0TDhXJyjukk1VBMvK31RYJ'
    'e+e6RkAK4UUa2g6E/Z3JRkXtyDxB6oviKTMti/5AkEAzLc3N4psBOz8hziBSVX8rMW9sdIbmHfIMD'
    '8Jv8v1142eDpUOVRdO4aNTATyJA9IA9yT8hvBvzUnWyG2qU22IwQJAJ0QTnK3deRvuRF3Tf5kM55b'
    'AxuhQFW8jE7zN0O9M8QYn+nr6keHJcNbDXyRHzcY8dXcHSR4w5RKM/pQlP7I/0QJAUsKSNIC9Bc1S'
    'zfghocg+G2PtmXoJkSftbm3fkRbIWkCGxH36XZmKcULMSs6OQZAmcv7Q5Gj6X/0W25rTN/G5UQ=='
)
PEM = '-----BEGIN PRIVATE KEY-----\n' + PRIVATE_KEY_B64 + '\n-----END PRIVATE KEY-----'

HEADERS = {
    'content-type': 'application/x-www-form-urlencoded',
    'accept': 'application/json, text/plain, */*',
    'x-requested-with': 'XMLHttpRequest',
}


# ============ 核心客户端 ============
class FlymeClient:
    """封装接口调用、STS 解密、签名、下载"""

    def __init__(self, token, log):
        self.token = token
        self.log = log
        self.sig = None
        self.sig_expiry = 0
        self._key = serialization.load_pem_private_key(PEM.encode(), password=None)

    def _now_ms(self):
        return str(int(time.time() * 1000))

    def _post(self, path, data):
        r = requests.post(API_BASE + path, data=data, headers=HEADERS, timeout=30)
        j = r.json()
        return r.status_code, j

    # ---------- 登录态 ----------
    def get_user_info(self):
        _, j = self._post('/user/info', {'type': '0', 'cts': self._now_ms(), 'token': self.token})
        if j.get('code') != 200:
            raise RuntimeError('登录态失效:user/info code=%s message=%s' % (j.get('code'), j.get('message')))
        return j['value']

    # ---------- STS 凭证 ----------
    def get_sig(self, force=False):
        if not force and self.sig and time.time() < self.sig_expiry - 300:
            return self.sig
        _, j = self._post('/file/get_sig/v2', {'type': '2', 'cts': self._now_ms(), 'token': self.token})
        if j.get('code') != 200:
            raise RuntimeError('获取 STS 失败:code=%s' % j.get('code'))
        enc = j['value']
        raw = base64.b64decode(enc)
        block_size = self._key.key_size // 8   # RSA 块大小(1024 bit -> 128 字节)
        blocks = [raw[i:i + block_size] for i in range(0, len(raw), block_size)]
        plain = b''.join(self._key.decrypt(b, padding.PKCS1v15()) for b in blocks)
        sig = json.loads(plain.decode('utf-8'))
        self.sig = sig
        exp = sig.get('expiredTime') or sig.get('expireTime')
        self.sig_expiry = self._parse_expiry(exp)
        return sig

    @staticmethod
    def _parse_expiry(exp):
        if not exp:
            return time.time() + 3600
        if isinstance(exp, (int, float)):
            return exp / 1000 if exp > 1e12 else exp
        # ISO 字符串
        try:
            from datetime import datetime
            s = exp.replace('Z', '+00:00')
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            return time.time() + 3600

    # ---------- 相册列表 ----------
    def get_dirs(self):
        _, j = self._post('/album/dir/list',
                          {'limit': '100', 'order': '1', 'cts': self._now_ms(), 'token': self.token})
        if j.get('code') != 200:
            raise RuntimeError('获取相册列表失败:code=%s' % j.get('code'))
        return j.get('value', {}).get('dir', [])

    # ---------- 翻页取文件 ----------
    def get_files(self, dir_id):
        files = []
        offset = 0
        while True:
            _, j = self._post('/album/list', {
                'limit': str(PAGE_LIMIT), 'offset': str(offset), 'order': '1',
                'isWebp': 'true', 'dirId': str(dir_id), 'cts': self._now_ms(), 'token': self.token,
            })
            if j.get('code') != 200:
                raise RuntimeError('获取文件列表失败:code=%s' % j.get('code'))
            value = j.get('value', {})
            batch = value.get('file', [])
            files.extend(batch)
            if value.get('end') == 1 or not batch:
                break
            offset += PAGE_LIMIT
        return files

    # ---------- 签名 ----------
    def sign_url(self, object_key, sig):
        expires = int(time.time()) + 3600
        sts = sig['securityToken']
        string_to_sign = (
            'GET\n\n\n%d\n/%s/%s?response-content-disposition=attachment;filename=%s&security-token=%s'
            % (expires, sig['bucket'], object_key, object_key, sts)
        )
        signature = base64.b64encode(
            hmac.new(sig['accessKeySecret'].encode(), string_to_sign.encode(), hashlib.sha1).digest()
        ).decode()
        q = requests.utils.quote
        return ('https://%s.%s.aliyuncs.com/%s?OSSAccessKeyId=%s&Expires=%d&Signature=%s&security-token=%s'
                '&response-content-disposition=attachment;filename=%s') % (
            sig['bucket'], sig['region'], object_key, q(sig['accessKeyId'], safe=''),
            expires, q(signature, safe=''), q(sts, safe=''), q(object_key, safe=''))

    # ---------- 下载(断点续传) ----------
    def download(self, object_key, dest_path, expected_size):
        if os.path.exists(dest_path) and os.path.getsize(dest_path) == expected_size:
            return 'skip'
        sig = self.get_sig()
        url = self.sign_url(object_key, sig)
        with requests.get(url, stream=True, timeout=120) as r:
            if r.status_code != 200:
                raise RuntimeError('OSS HTTP %s' % r.status_code)
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return 'ok'


# ============ GUI ============
class App:
    def __init__(self, root):
        self.root = root
        self.client = None
        self.msg_q = queue.Queue()
        root.title('Flyme 云相册导出工具')
        root.geometry('760x640')
        self._build_ui()
        self.root.after(100, self._poll_queue)

    def _build_ui(self):
        pad = {'padx': 8, 'pady': 4}

        # ---- token ----
        frm = ttk.LabelFrame(self.root, text='登录凭证')
        frm.pack(fill='x', padx=10, pady=6)
        ttk.Label(frm, text='_utoken:').grid(row=0, column=0, sticky='w', **pad)
        self.token_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.token_var, width=60).grid(row=0, column=1, sticky='we', **pad)
        ttk.Button(frm, text='读取 cookies.json', command=self._load_cookie).grid(row=0, column=2, **pad)
        ttk.Button(frm, text='连接验证', command=self._connect).grid(row=0, column=3, **pad)
        frm.columnconfigure(1, weight=1)

        # ---- 输出目录 + 视频 ----
        frm2 = ttk.LabelFrame(self.root, text='选项')
        frm2.pack(fill='x', padx=10, pady=6)
        ttk.Label(frm2, text='输出目录:').grid(row=0, column=0, sticky='w', **pad)
        self.out_var = tk.StringVar(value=os.path.join(os.getcwd(), 'photos'))
        ttk.Entry(frm2, textvariable=self.out_var, width=50).grid(row=0, column=1, sticky='we', **pad)
        ttk.Button(frm2, text='浏览...', command=self._browse).grid(row=0, column=2, **pad)
        self.video_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm2, text='同时下载视频', variable=self.video_var).grid(row=1, column=1, sticky='w', **pad)
        frm2.columnconfigure(1, weight=1)

        # ---- 底部:进度条 + 状态 + 开始导出按钮(先 pack,保证始终可见) ----
        bottom = ttk.Frame(self.root)
        bottom.pack(side='bottom', fill='x', padx=10, pady=6)
        self.progress = ttk.Progressbar(bottom, mode='determinate')
        self.progress.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.status_var = tk.StringVar(value='就绪')
        ttk.Label(bottom, textvariable=self.status_var, width=28).pack(side='left')
        self.start_btn = ttk.Button(bottom, text='开始导出', command=self._start, state='disabled')
        self.start_btn.pack(side='left', padx=(8, 0))

        # ---- 日志(固定高度,不抢占按钮空间) ----
        frm4 = ttk.LabelFrame(self.root, text='日志')
        frm4.pack(side='bottom', fill='x', padx=10, pady=6)
        self.log_text = tk.Text(frm4, height=8, state='disabled', wrap='word')
        self.log_text.pack(fill='x', padx=4, pady=4)

        # ---- 相册列表(占据剩余空间) ----
        frm3 = ttk.LabelFrame(self.root, text='相册(勾选要导出的)')
        frm3.pack(fill='both', expand=True, padx=10, pady=6)
        canvas = tk.Canvas(frm3)
        scroll = ttk.Scrollbar(frm3, orient='vertical', command=canvas.yview)
        self.album_frame = ttk.Frame(canvas)
        self.album_frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=self.album_frame, anchor='nw')
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.album_vars = []

    # ---------- 事件 ----------
    def _load_cookie(self):
        path = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            arr = cookies if isinstance(cookies, list) else cookies.get('cookies', [])
            token = next((c['value'] for c in arr if c.get('name') == '_utoken'), None)
            if not token:
                raise ValueError('未找到 _utoken')
            self.token_var.set(token)
            self._log('已从 %s 读取 _utoken' % path)
        except Exception as e:
            messagebox.showerror('错误', '读取失败: %s' % e)

    def _browse(self):
        d = filedialog.askdirectory()
        if d:
            self.out_var.set(d)

    def _log(self, msg):
        self.msg_q.put(('log', msg))

    def _connect(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning('提示', '请先输入 _utoken 或读取 cookies.json')
            return
        self._log('正在验证登录态 ...')
        threading.Thread(target=self._connect_worker, args=(token,), daemon=True).start()

    def _connect_worker(self, token):
        try:
            client = FlymeClient(token, self._log)
            info = client.get_user_info()
            dirs = client.get_dirs()
            self.msg_q.put(('connected', (client, info, dirs)))
        except Exception as e:
            self.msg_q.put(('error', str(e)))

    def _start(self):
        selected = [(var.get(), album) for var, album in self.album_vars if var.get()]
        if not selected:
            messagebox.showwarning('提示', '请至少勾选一个相册')
            return
        out_dir = self.out_var.get().strip()
        include_video = self.video_var.get()
        self.start_btn.config(state='disabled')
        self.progress['value'] = 0
        threading.Thread(target=self._export_worker,
                         args=(selected, out_dir, include_video), daemon=True).start()

    def _export_worker(self, selected, out_dir, include_video):
        client = self.client
        total_ok = total_skip = total_fail = 0
        try:
            for _, album in selected:
                dir_id = album['id']
                dir_name = album['dirName']
                album_dir = os.path.join(out_dir, dir_name)
                os.makedirs(album_dir, exist_ok=True)
                self._log('--- 相册 %s (id=%s) ---' % (dir_name, dir_id))
                files = client.get_files(dir_id)
                targets = [f for f in files if include_video or not f.get('isVideo')]
                self._log('共 %d 项(过滤视频后 %d 项)' % (len(files), len(targets)))

                total = len(targets)
                done = 0
                ok = skip = fail = 0
                lock = threading.Lock()

                def worker():
                    nonlocal done, ok, skip, fail
                    while True:
                        with lock:
                            if done >= total:
                                return
                            idx = done
                            done += 1
                        f = targets[idx]
                        name = f.get('fileName') or ('%s.jpg' % f['id'])
                        dest = os.path.join(album_dir, name)
                        try:
                            r = client.download(f['url'], dest, f['size'])
                            with lock:
                                if r == 'skip':
                                    skip += 1
                                else:
                                    ok += 1
                        except Exception as e:
                            with lock:
                                fail += 1
                                self._log('[失败] %s: %s' % (name, e))
                        self.msg_q.put(('progress', done, total, ok, skip, fail, dir_name))

                threads = [threading.Thread(target=worker) for _ in range(CONCURRENCY)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

                self._log('相册 %s 完成: ok=%d skip=%d fail=%d' % (dir_name, ok, skip, fail))
                total_ok += ok
                total_skip += skip
                total_fail += fail

            self.msg_q.put(('done', (total_ok, total_skip, total_fail)))
        except Exception as e:
            self.msg_q.put(('error', str(e)))

    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_q.get_nowait()
                kind = msg[0]
                if kind == 'log':
                    self._append_log(msg[1])
                elif kind == 'connected':
                    client, info, dirs = msg[1]
                    self.client = client
                    self._append_log('登录成功: fileNum=%s, vip=%s' % (info.get('fileNum'), info.get('vipName')))
                    self._populate_albums(dirs)
                    self.start_btn.config(state='normal')
                elif kind == 'progress':
                    _, done, total, ok, skip, fail, dir_name = msg
                    if total:
                        self.progress['value'] = done / total * 100
                    self.status_var.set('%s: %d/%d' % (dir_name, done, total))
                elif kind == 'done':
                    ok, skip, fail = msg[1]
                    self._append_log('===== 全部完成 ===== 下载 %d, 跳过 %d, 失败 %d' % (ok, skip, fail))
                    self.status_var.set('完成')
                    self.progress['value'] = 100
                    self.start_btn.config(state='normal')
                    messagebox.showinfo('完成', '导出完成!\n下载 %d, 跳过 %d, 失败 %d' % (ok, skip, fail))
                elif kind == 'error':
                    self._append_log('[错误] %s' % msg[1])
                    self.status_var.set('出错')
                    self.start_btn.config(state='normal')
                    messagebox.showerror('错误', msg[1])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _append_log(self, text):
        self.log_text.config(state='normal')
        self.log_text.insert('end', text + '\n')
        self.log_text.see('end')
        self.log_text.config(state='disabled')

    def _populate_albums(self, dirs):
        for w in self.album_frame.winfo_children():
            w.destroy()
        self.album_vars = []
        for d in dirs:
            var = tk.BooleanVar(value=True)
            self.album_vars.append((var, d))
            ttk.Checkbutton(
                self.album_frame,
                text='%s (%s 项)' % (d['dirName'], d.get('fileNum', '?')),
                variable=var,
            ).pack(anchor='w', padx=8, pady=2)


# ============ 入口 ============
def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
