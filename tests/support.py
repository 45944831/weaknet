# -*- coding: utf-8 -*-
"""pytest 共用小工具：事件循环、HTTP 读取、经代理的隧道、控制接口客户端。

不依赖 pytest-asyncio：夹具与用例各自用 run_async() 起一个独立事件循环，
这样「进程内引擎线程」和「单条链路的白盒用例」互不共享 loop，也就不会
出现「任务挂在别人的循环上，销毁时报警告」这类难查的串扰。
"""
import asyncio
import http.client
import json
import socket
import time
from collections import namedtuple


def run_async(coro, timeout=30.0):
    """在新事件循环里跑完一个协程，退出前取消所有残留任务。

    残留任务必须显式取消：DelayLine 的投递协程、nap 的等待者若还挂着，
    关闭循环时会留下「Task was destroyed but it is pending」噪音，
    而这些噪音恰好会盖住真正的失败信息。
    """
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(asyncio.wait_for(coro, timeout))
    finally:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def wait_until(pred, timeout=5.0, interval=0.05):
    """轮询直到 pred() 为真，返回它的值；超时返回最后一次的结果"""
    end = time.monotonic() + timeout
    value = pred()
    while not value and time.monotonic() < end:
        time.sleep(interval)
        value = pred()
    return value


def connect_via_proxy(proxy, host, port, timeout=20.0):
    """向代理发 CONNECT 建隧道，返回 (socket, 已读到的剩余字节)。"""
    s = socket.create_connection(proxy, timeout=timeout)
    req = "CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n\r\n" % (host, port, host, port)
    s.sendall(req.encode("latin1"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        part = s.recv(4096)
        if not part:
            s.close()
            raise RuntimeError("代理在 CONNECT 阶段断开")
        buf += part
    head, _, rest = buf.partition(b"\r\n\r\n")
    if b" 200" not in head.split(b"\r\n")[0]:
        s.close()
        raise RuntimeError("CONNECT 失败：%r" % head[:120])
    return s, rest


class HttpReader:
    """按 Content-Length 读响应，支持同一条连接连续读多个（keep-alive）。"""

    def __init__(self, sock, pre=b""):
        self.sock = sock
        self.buf = pre

    def _fill(self):
        part = self.sock.recv(65536)
        if not part:
            raise EOFError("连接已关闭（还差 %d 字节）" % len(self.buf))
        self.buf += part

    def _line(self):
        while b"\r\n" not in self.buf:
            self._fill()
        line, _, self.buf = self.buf.partition(b"\r\n")
        return line

    def response(self):
        first = self._line().decode("latin1")
        try:
            status = int(first.split(" ")[1])
        except Exception:
            raise RuntimeError("响应行不合法：%r" % first)
        headers = {}
        while True:
            line = self._line()
            if not line:
                break
            k, _, v = line.partition(b":")
            headers[k.strip().lower().decode("latin1")] = v.strip().decode("latin1")
        n = int(headers.get("content-length") or 0)
        while len(self.buf) < n:
            self._fill()
        body, self.buf = self.buf[:n], self.buf[n:]
        return status, headers, body


Response = namedtuple("Response", "status body headers")


class CtrlClient:
    """控制接口客户端。用 http.client 而不是 urllib：后者会读取环境里的
    http_proxy，把请求绕到别的代理上去（本机开发环境常被注入）。"""

    def __init__(self, host="127.0.0.1", port=8899, timeout=10.0):
        self.host = host
        self.port = int(port)
        self.timeout = timeout

    def request(self, method, path, body=None, timeout=None):
        conn = http.client.HTTPConnection(self.host, self.port,
                                          timeout=timeout or self.timeout)
        try:
            data = None
            headers = {}
            if body is not None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=headers)
            r = conn.getresponse()
            raw = r.read()
            ctype = r.getheader("Content-Type") or ""
            if "json" in ctype:
                obj = json.loads(raw.decode("utf-8")) if raw else None
            else:
                obj = raw.decode("utf-8", "replace")
            return Response(r.status, obj, dict(r.getheaders()))
        finally:
            conn.close()

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body, **kw)
