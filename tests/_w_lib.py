#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WeakNet Windows 端测试客户端库：控制 API + 走代理的隧道连接。

注意：本机环境被注入了 http_proxy，所有 HTTP 请求必须禁用代理，否则会绕到
沙箱代理上超时。这里统一用 ProxyHandler({})。
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

# 目标可用环境变量切换：默认 EXE 实例(8888/8899)，源码回归实例用 8890/8891
import os

CTRL = os.environ.get("WN_CTRL", "http://127.0.0.1:8899")
PROXY = ("127.0.0.1", int(os.environ.get("WN_PROXY", "8888")))

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api_get(path, timeout=10):
    with _opener.open(CTRL + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def api_post(path, obj, timeout=30):
    req = urllib.request.Request(CTRL + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with _opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def status():
    return api_get("/api/status")


def set_scene(key):
    """选中场景（不自动开启）"""
    return api_post("/api/preset", {"key": key})


def set_limit(on):
    return api_post("/api/run", {"enable": bool(on)})


def set_custom(prof):
    """直接下发自定义参数（/api/profile）"""
    return api_post("/api/profile", prof)


def wait_status(pred, timeout=10):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = status()
            if pred(last):
                return last
        except Exception:
            pass
        time.sleep(0.2)
    return last


class Tunnel:
    """一条经过 WeakNet 代理的 CONNECT 隧道"""

    def __init__(self, host="127.0.0.1", port=9121, proxy=None, timeout=60):
        # proxy 不写成默认参数：测试脚本会改模块级 PROXY 指向另一个实例（源码版 vs EXE 版）
        self.s = socket.create_connection(proxy or PROXY, timeout=timeout)
        self.s.sendall(("CONNECT %s:%d HTTP/1.1\r\n\r\n" % (host, port)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            d = self.s.recv(4096)
            if not d:
                raise RuntimeError("proxy closed")
            buf += d
        if b"200" not in buf.split(b"\r\n")[0]:
            raise RuntimeError("proxy reply: %r" % buf[:80])
        self.s.settimeout(timeout)
        self.buf = b""

    def _readline(self):
        while b"\n" not in self.buf:
            d = self.s.recv(65536)
            if not d:
                raise RuntimeError("closed")
            self.buf += d
        line, _, rest = self.buf.partition(b"\n")
        self.buf = rest
        return line

    def _readn(self, n):
        while len(self.buf) < n:
            d = self.s.recv(65536)
            if not d:
                raise RuntimeError("closed")
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def cmd(self, s):
        self.s.sendall((s + "\n").encode())

    def ping(self, tag=0):
        """返回 (rtt_ms, srv_recv_ms)"""
        t0 = time.monotonic()
        self.cmd("PING %d" % tag)
        line = self._readline().decode()
        t1 = time.monotonic()
        parts = line.split()
        return (t1 - t0) * 1000.0, float(parts[2]) if len(parts) > 2 else 0.0

    def down(self, n):
        """测下行吞吐：返回 (bytes, seconds, bps)"""
        t0 = time.monotonic()
        self.cmd("DOWN %d" % n)
        got = self._readn(n)
        t1 = time.monotonic()
        dt = t1 - t0
        return len(got), dt, (len(got) * 8 / dt if dt > 0 else 0)

    def up(self, n, chunk=65536):
        """测上行吞吐：返回 (bytes, seconds, bps)"""
        t0 = time.monotonic()
        self.cmd("UP %d" % n)
        left = n
        block = b"x" * chunk
        while left > 0:
            k = min(left, chunk)
            self.s.sendall(block[:k])
            left -= k
        line = self._readline()
        t1 = time.monotonic()
        dt = t1 - t0
        return n, dt, (n * 8 / dt if dt > 0 else 0)

    def arrivals(self):
        self.cmd("ARR")
        line = self._readline().decode()
        body = line.split(" ", 1)[1].strip()
        return [float(x) for x in body.split(",")] if body else []

    def reset_arrivals(self):
        self.cmd("RESETARR")
        self._readline()

    def close(self):
        try:
            self.cmd("QUIT")
        except Exception:
            pass
        try:
            self.s.close()
        except Exception:
            pass


def kb(bps):
    return bps / 1000.0
