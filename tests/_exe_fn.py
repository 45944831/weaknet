# -*- coding: utf-8 -*-
"""EXE 冒烟：打包后的二进制 -> 限速 / 停止 / 空闲回收 全链路。"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json, socket, time, urllib.request
CTRL = "http://127.0.0.1:8899"; PROXY = ("127.0.0.1", 8888); SRV = ("192.168.1.49", 9125)
R = []
def api(p, d=None):
    b = json.dumps(d).encode() if d is not None else None
    r = urllib.request.Request(CTRL + p, data=b, method=("POST" if b else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(r, timeout=10) as x: return x.status, json.loads(x.read() or b"{}")
    except Exception as e: return -1, {"error": str(e)}
def rec(n, ok, d=""):
    R.append((n, bool(ok), d)); print(("PASS " if ok else "FAIL ") + n + ("  | " + d if d else ""), flush=True)
def tun(t=30):
    s = socket.create_connection(PROXY, timeout=t)
    s.sendall(("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n\r\n" % (SRV[0], SRV[1], SRV[0], SRV[1])).encode())
    b = b""
    while b"\r\n\r\n" not in b: b += s.recv(1)
    return s
def dl(n, t=120):
    s = tun(t); t0 = time.time(); g = 0
    try:
        s.sendall(("DOWN %d\n" % n).encode())
        while g < n:
            d = s.recv(min(65536, n - g))
            if not d: break
            g += len(d)
    finally: s.close()
    return g, max(time.time() - t0, 1e-6)

st, d = api("/api/status"); rec("EXE /api/status", st == 200 and "profile" in d, "status=%s" % st)
rec("EXE 启动默认未限速", d.get("limit_on") is False and d.get("preset_key") == "direct",
    "limit_on=%s preset=%s" % (d.get("limit_on"), d.get("preset_key")))
api("/api/preset", {"key": "4g"}); api("/api/run", {"enable": True})
g, el = dl(4 * 1024 * 1024); k = g * 8 / 1024 / el
rec("EXE 4g 20000kbps 限速生效", g == 4 * 1024 * 1024 and 16000 <= k <= 24000,
    "%.0f kbps（设定 20000）el=%.2fs" % (k, el))
t0 = time.time(); api("/api/run", {"enable": False}); g, el = dl(2 * 1024 * 1024)
rec("EXE 停止后恢复直连", g == 2 * 1024 * 1024 and el < 1.0, "2MB 耗时 %.2fs" % el)
api("/api/preset", {"key": "satellite"}); api("/api/run", {"enable": True})
# 卫星档 RTT 1400ms：512KB 连慢启动都没跑完（实测仅 ~0.6x），必须用大样本。
g, el = dl(2 * 1024 * 1024); k = g * 8 / 1024 / el
rec("EXE 卫星档 2000kbps", g == 2 * 1024 * 1024 and 1400 <= k <= 2400,
    "%.0f kbps（设定 2000，2MB 大样本）el=%.2fs" % (k, el))
api("/api/run", {"enable": False})
bad = [x for x in R if not x[1]]
print("\n===== EXE 冒烟：%d/%d 通过 =====" % (len(R) - len(bad), len(R)))
for n, _o, dd in bad: print("FAILED: %s | %s" % (n, dd))
raise SystemExit(1 if bad else 0)
