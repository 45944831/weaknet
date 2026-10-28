# -*- coding: utf-8 -*-
"""定向探针：复核全量回归中 5 个 FAIL 场景是「测量伪影」还是「限速真实回退」。

与 _t_win_full.py 完全同源：CONNECT 隧道 + 行协议（DOWN <n> / PING）。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import socket
import time
import urllib.request

CTRL = "http://127.0.0.1:8899"
PROXY = ("127.0.0.1", 8888)
SRV = ("192.168.1.49", 9126)


def api(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(CTRL + path, data=body,
                                 method=("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def set_scene(key, enable=True):
    api("/api/preset", {"key": key})
    api("/api/run", {"enable": enable})


def read_line(s):
    buf = b""
    while not buf.endswith(b"\n"):
        d = s.recv(1)
        if not d:
            return None
        buf += d
    return buf.decode("latin1").strip()


def tunnel(timeout=30):
    s = socket.create_connection(PROXY, timeout=timeout)
    s.sendall(("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n\r\n"
               % (SRV[0], SRV[1], SRV[0], SRV[1])).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = s.recv(1)
        if not d:
            raise OSError("proxy closed during CONNECT: %r" % buf)
        buf += d
    return s


def download(nbytes, timeout=120):
    s = tunnel(timeout)
    t0 = time.time()
    got = 0
    try:
        s.sendall(("DOWN %d\n" % nbytes).encode())
        while got < nbytes:
            d = s.recv(min(65536, nbytes - got))
            if not d:
                break
            got += len(d)
    finally:
        s.close()
    el = max(time.time() - t0, 1e-6)
    return got, el


def ping_seq(n):
    s = tunnel(60)
    out = []
    try:
        for i in range(n):
            t0 = time.time()
            s.sendall(("PING %d\n" % i).encode())
            line = read_line(s)
            if not line:
                out.append(None)
                break
            out.append((time.time() - t0) * 1000.0)
    finally:
        s.close()
    return out


def run(label, key, sizes, npings=3):
    set_scene(key)
    st, d = api("/api/status")
    prof = d["profile"]
    exp_kbps = prof["down_kbps"]
    exp_lat = prof["latency_up_ms"] + prof["latency_down_ms"]
    print("--- %s (%s) 设定 %s kbps / RTT %d ms ---"
          % (label, key, exp_kbps or "inf", exp_lat), flush=True)
    for size in sizes:
        got, el = download(size)
        kbps = got * 8 / 1024 / el
        rtts = [round(x, 1) for x in ping_seq(npings) if x is not None]
        ok = (got == size and 0.45 * exp_kbps <= kbps <= 1.6 * exp_kbps
              and bool(rtts) and min(rtts) >= exp_lat * 0.55)
        print("    %8d B -> %9.0f kbps (%.2fx) el=%.2fs rtt=%s %s"
              % (size, kbps, kbps / exp_kbps if exp_kbps else 0, el, rtts,
                 "OK" if ok else "out-of-band"), flush=True)


run("高带宽(1MB 会被突发额度吃掉)", "5g",
    [1024 * 1024, 4 * 1024 * 1024, 8 * 1024 * 1024])
run("高带宽(1MB 会被突发额度吃掉)", "wifi", [1024 * 1024, 4 * 1024 * 1024])
run("高丢包 TCP 抖动", "high_loss", [512 * 1024] * 4)
run("长肥管道(慢启动主导)", "high_latency", [512 * 1024, 2 * 1024 * 1024])
run("长肥管道(慢启动主导)", "satellite", [256 * 1024, 1024 * 1024])
run("极低带宽+极高延迟", "elevator", [64 * 1024, 256 * 1024])
api("/api/run", {"enable": False})
print("done")
