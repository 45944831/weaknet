# -*- coding: utf-8 -*-
"""WeakNet 测试辅助服务（常驻）。

9126 原始 socket：响应头固定为
        HTTP/1.1 200 OK\\r\\nContent-Length: <N>\\r\\n\\r\\n
      头长 = 37 + len(str(N))，探针可据此精确判定「收完」，
      不依赖 nc 的 stdin EOF 行为（toybox nc 在 stdin EOF 时立即退出，会丢响应）。
9129 http.server：返回 HTTP-OK-PROXY-PATH（USB 代理路径标记）
9130 http.server：回显
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import http.server
import socketserver
import socket
import threading


def raw_big():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 9126))
    s.listen(64)
    print("LISTEN 9126 (raw big)", flush=True)

    def handle(c):
        try:
            c.settimeout(60)
            buf = b""
            while b"\r\n\r\n" not in buf and len(buf) < 8192:
                d = c.recv(4096)
                if not d:
                    break
                buf += d
            n = 0
            try:
                line = buf.split(b"\r\n")[0]
                target = line.split(b" ")[1]
                qs = target.split(b"?")[-1]
                for kv in qs.split(b"&"):
                    if kv.startswith(b"n="):
                        n = int(kv[2:] or 0)
            except Exception:
                n = 0
            n = max(0, min(n, 8 * 1024 * 1024))
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % n)
            chunk = b"z" * 16384
            left = n
            while left > 0:
                k = min(left, len(chunk))
                c.sendall(chunk[:k])
                left -= k
        except Exception:
            pass
        finally:
            try:
                c.close()
            except Exception:
                pass

    while True:
        try:
            c, _ = s.accept()
            threading.Thread(target=handle, args=(c,), daemon=True).start()
        except Exception:
            break


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        body = (b"HTTP-OK-PROXY-PATH" if self.server.server_tag == "proxy"
                else b"ECHO:" + self.path.encode())
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        ln = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(ln)
        body = b"POST-OK:%d" % ln
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_http(port, tag):
    s = S(("127.0.0.1", port), H)
    s.server_tag = tag
    print("LISTEN %d (%s)" % (port, tag), flush=True)
    s.serve_forever()


threading.Thread(target=raw_big, daemon=True).start()
threading.Thread(target=run_http, args=(9129, "proxy"), daemon=True).start()
threading.Thread(target=run_http, args=(9130, "echo"), daemon=True).start()
threading.Event().wait()
