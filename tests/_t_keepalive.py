# -*- coding: utf-8 -*-
"""WeakNet keep-alive 转发定向测试（自包含：自带源站 + 引擎，不依赖局域网）。

覆盖：
  1. 一条客户端连接上多个请求（客户端 keep-alive + 上游连接复用）
  2. chunked 响应完整性
  3. 带体 POST 仍走单请求路径（回退）
  4. 上游主动 Connection: close 后的重连与幂等重试
  5. keep-alive 路径上延迟/带宽注入仍然生效（第 2+ 个请求也吃）
  6. CONNECT 隧道回归
  7. 客户端 Connection: close → 响应后关闭
  8. 半截请求头超时断开
  9. 连接级事件落盘（weaknet.log）
"""
import http.server
import json
import socket
import socketserver
import threading
import time
import urllib.request

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PROXY = ("127.0.0.1", 18888)
CTRL = "http://127.0.0.1:18899"
SRV_PORT = 18099
RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""), flush=True)


# ---------------------------------------------------------------- 源站
class Origin(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"     # 支持 keep-alive

    def log_message(self, *a):
        pass

    def _track(self):
        if self.path.startswith("/stats"):
            return          # 统计查询自身不计数，否则数字对不上
        st = self.server.state
        st["requests"] += 1
        port = self.connection.getpeername()[1]
        if port not in st["conns"]:
            st["conns"][port] = 0
        st["conns"][port] += 1

    def do_GET(self):
        self._track()
        route = self.path.split("?", 1)[0]      # 精确匹配路由：
        # "/close" 也以 "/cl" 开头，用 startswith 会被 /cl 分支抢走
        if route == "/cl":
            n = 1024
            if "n=" in self.path:
                n = int(self.path.split("n=")[1].split("&")[0]) * 1024
            body = b"y" * n
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif route == "/chunked":
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for part in (b"aaaa", b"bbbbbb", b"c" * 100):
                self.wfile.write(("%x\r\n" % len(part)).encode() + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        elif route == "/close":
            body = b"bye"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
        elif route == "/stats":
            st = self.server.state
            body = json.dumps({"requests": st["requests"],
                               "conns": st["conns"]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self):
        self._track()
        n = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    state = {"requests": 0, "conns": {}}


def http_get(url, timeout=15):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(url, timeout=timeout) as r:
        return r.status, r.read()


def http_json(url, timeout=15):
    st, raw = http_get(url, timeout)
    return st, json.loads(raw)


def api(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(CTRL + path, data=body,
                                 method="POST" if body else "GET")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=8) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def proxy_request_lines(sock, lines, timeout=15):
    """经代理发一个请求，按响应 framing 精确读完整条响应后返回。
    （原来是读到超时，每个请求白等 15s，还会干扰时序测量）"""
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    sock.settimeout(timeout)
    head = b""
    while b"\r\n\r\n" not in head:
        d = sock.recv(1)
        if not d:
            return head
        head += d
    hdr_lines = head.split(b"\r\n")
    clen = 0
    chunked = False
    for h in hdr_lines[1:]:
        k, _, v = h.partition(b":")
        k = k.strip().lower()
        if k == b"content-length":
            clen = int(v.strip())
        elif k == b"transfer-encoding" and b"chunked" in v.lower():
            chunked = True
    body = b""
    if chunked:
        while True:
            line = b""
            while not line.endswith(b"\r\n"):
                d = sock.recv(1)
                if not d:
                    return head + body
                line += d
            body += line
            sz = int(line.split(b";")[0].strip() or b"0", 16)
            if sz == 0:
                while True:
                    t = b""
                    while not t.endswith(b"\r\n"):
                        d = sock.recv(1)
                        if not d:
                            return head + body
                        t += d
                    body += t
                    if t == b"\r\n":
                        break
                break
            remain = sz + 2          # 数据 + 结尾 CRLF
            while remain > 0:
                d = sock.recv(min(65536, remain))
                if not d:
                    return head + body
                body += d
                remain -= len(d)
    else:
        remain = clen
        while remain > 0:
            d = sock.recv(min(65536, remain))
            if not d:
                break
            body += d
            remain -= len(d)
    return head + body


def read_head(sock, timeout=15):
    sock.settimeout(timeout)
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = sock.recv(1)
        if not d:
            break
        buf += d
    return buf


def main():
    import os
    import sys
    sys.path.insert(0, ROOT)
    srv = Server(("127.0.0.1", SRV_PORT), Origin)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    import weaknet as W
    loop = __import__("asyncio").new_event_loop()

    def engine():
        import asyncio
        asyncio.set_event_loop(loop)
        loop.run_until_complete(W.serve("127.0.0.1", PROXY[1], 18899))
        loop.run_forever()

    threading.Thread(target=engine, daemon=True).start()
    time.sleep(1.0)
    api("/api/preset", {"key": "direct"})
    api("/api/run", {"enable": False})

    base = f"http://127.0.0.1:{SRV_PORT}"

    # ---- 1. 客户端 keep-alive：一条连接 3 个请求，上游只建 1 条 ----
    srv.state["requests"] = 0
    srv.state["conns"] = {}
    s = socket.create_connection(PROXY, timeout=15)
    ok_all = True
    for i in range(3):
        buf = proxy_request_lines(s, [f"GET {base}/cl?n=4 HTTP/1.1",
                                      f"Host: 127.0.0.1:{SRV_PORT}"])
        if b"200" not in buf.split(b"\r\n", 1)[0] or buf.count(b"y") < 4096:
            ok_all = False
    st = http_json(base + "/stats")[1]
    rec("KA-1 客户端 keep-alive 三请求全 200", ok_all,
        f"origin 见 {st['requests']} 请求 / {len(st['conns'])} 条上游连接")
    rec("KA-2 上游连接复用（3 请求只 1 条上游）",
        st["requests"] == 3 and len(st["conns"]) == 1,
        f"conns={list(st['conns'].values())}")
    s.close()

    # ---- 2. chunked 响应 ----
    s = socket.create_connection(PROXY, timeout=15)
    buf = proxy_request_lines(s, [f"GET {base}/chunked HTTP/1.1",
                                  f"Host: 127.0.0.1:{SRV_PORT}"])
    ok = (b"200" in buf.split(b"\r\n", 1)[0]
          and b"chunked" in buf.lower()
          and b"aaaa" in buf and b"bbbbbb" in buf and b"c" * 100 in buf
          and buf.rstrip().endswith(b"0"))
    rec("KA-3 chunked 响应完整（含终止块）", ok, repr(buf[-40:]))
    s.close()

    # ---- 3. 带体 POST 回退单请求路径 ----
    s = socket.create_connection(PROXY, timeout=15)
    body = b"hello-post-body"
    s.sendall((f"POST {base}/post HTTP/1.1\r\nHost: 127.0.0.1:{SRV_PORT}\r\n"
               f"Content-Length: {len(body)}\r\n\r\n").encode() + body)
    buf = read_head(s)
    while True:
        d = s.recv(65536)
        if not d:
            break
        buf += d
    rec("KA-4 POST 带体回退路径回声正确", body in buf and b"200" in buf,
        f"{len(buf)}B")
    s.close()

    # ---- 4. 上游 Connection: close → 重连后继续 ----
    srv.state["requests"] = 0
    srv.state["conns"] = {}
    s = socket.create_connection(PROXY, timeout=15)
    r1 = proxy_request_lines(s, [f"GET {base}/close HTTP/1.1",
                                 f"Host: 127.0.0.1:{SRV_PORT}"])
    ok1 = b"200" in r1.split(b"\r\n", 1)[0] and b"bye" in r1
    buf = proxy_request_lines(s, [f"GET {base}/cl?n=2 HTTP/1.1",
                                  f"Host: 127.0.0.1:{SRV_PORT}"])
    ok2 = b"200" in buf.split(b"\r\n", 1)[0] and buf.count(b"y") >= 2048
    rec("KA-5 上游 close 后同连接重连成功", ok1 and ok2,
        f"ok1={ok1} ok2={ok2} r1={r1[:60]!r} r2={buf[:60]!r}")
    s.close()

    # ---- 5. keep-alive 路径上延迟注入（第 2 个请求也吃）----
    api("/api/preset", {"key": "congestion"})     # 200up/400down ms, 512kbps
    api("/api/run", {"enable": True})
    s = socket.create_connection(PROXY, timeout=20)
    t0 = time.monotonic()
    proxy_request_lines(s, [f"GET {base}/cl?n=4 HTTP/1.1",
                            f"Host: 127.0.0.1:{SRV_PORT}"])
    t1 = time.monotonic()
    proxy_request_lines(s, [f"GET {base}/cl?n=4 HTTP/1.1",
                            f"Host: 127.0.0.1:{SRV_PORT}"])
    t2 = time.monotonic()
    rtt1, rtt2 = t1 - t0, t2 - t1
    rec("KA-6 keep-alive 第 2 请求仍吃满延迟（≥0.55s）",
        rtt2 >= 0.55, f"第1个 {rtt1*1000:.0f}ms 第2个 {rtt2*1000:.0f}ms")

    # ---- 6. keep-alive 路径上限速（512kbps：4KB 应 ≥60ms；换个 100KB 量出来）----
    t0 = time.monotonic()
    buf = proxy_request_lines(s, [f"GET {base}/cl?n=100 HTTP/1.1",
                                  f"Host: 127.0.0.1:{SRV_PORT}"])
    dt = time.monotonic() - t0
    need = 100 * 1024 * 8 / 512000.0
    rec("KA-7 keep-alive 路径下行限速生效",
        dt >= need * 0.7 and buf.count(b"y") >= 100 * 1024,
        f"100KB 用时 {dt:.2f}s（理论 {need:.2f}s）")
    s.close()

    # ---- 7. CONNECT 隧道回归 ----
    s = socket.create_connection(PROXY, timeout=15)
    s.sendall((f"CONNECT 127.0.0.1:{SRV_PORT} HTTP/1.1\r\n"
               f"Host: 127.0.0.1:{SRV_PORT}\r\n\r\n").encode())
    head = read_head(s)
    ok = b"200" in head
    if ok:
        s.sendall((f"GET /cl?n=2 HTTP/1.1\r\nHost: 127.0.0.1:{SRV_PORT}\r\n"
                   f"Connection: close\r\n\r\n").encode())
        buf = b""
        while True:
            d = s.recv(65536)
            if not d:
                break
            buf += d
        ok = b"200" in buf.split(b"\r\n", 1)[0] and buf.count(b"y") >= 2048
    rec("KA-8 CONNECT 隧道回归（含隧道内 HTTP）", ok)
    s.close()

    # ---- 8. 客户端 Connection: close → 响应后关闭 ----
    s = socket.create_connection(PROXY, timeout=15)
    buf = proxy_request_lines(s, [f"GET {base}/cl?n=1 HTTP/1.1",
                                  f"Host: 127.0.0.1:{SRV_PORT}",
                                  "Connection: close"])
    s.settimeout(5)
    closed = False
    try:
        while True:
            if not s.recv(65536):
                closed = True
                break
    except socket.timeout:
        pass
    rec("KA-9 客户端声明 close 后代理关闭连接", closed)
    s.close()

    # ---- 9. 半截请求头超时（30s）----
    api("/api/preset", {"key": "direct"})
    api("/api/run", {"enable": False})
    s = socket.create_connection(PROXY, timeout=45)
    s.sendall(b"GET / HTTP/1.1\r\n")            # 只有请求行，没有头结束
    t0 = time.monotonic()
    s.settimeout(45)
    closed = False
    try:
        while True:
            if not s.recv(4096):
                closed = True
                break
    except socket.timeout:
        pass
    dt = time.monotonic() - t0
    rec("KA-10 半截请求头 30s 超时断开", closed and 25 <= dt <= 45,
        f"{dt:.1f}s 后关闭={closed}")
    s.close()

    # ---- 10. 事件落盘 ----
    time.sleep(2.5)                             # 等 flush loop（2s）
    import pathlib

    logf = pathlib.Path(W.__file__).with_name("weaknet.log")
    txt = logf.read_text(encoding="utf-8", errors="ignore") if logf.exists() else ""
    rec("KA-11 连接级事件落盘 weaknet.log",
        "[connect] HTTP 代理" in txt, f"{len(txt)}B")

    n_ok = sum(1 for r in RESULTS if r["ok"])
    print(f"\n===== keep-alive 定向：{n_ok}/{len(RESULTS)} 通过 =====", flush=True)
    with open("_t_keepalive.json", "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
