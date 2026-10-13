# -*- coding: utf-8 -*-
"""测试用：记录发送速率的服务端（用于校验弱网限速是否真实生效）"""
import http.server
import os
import socketserver
import time

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "serve.bin")
PORT = 18082


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        size = os.path.getsize(FILE)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        sent = 0
        t0 = time.time()
        with open(FILE, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except Exception:
                    return
                sent += len(chunk)
                el = time.time() - t0
                print(f"[{el:5.1f}s] sent={sent/1024:8.0f}KB rate={sent/el/1024:7.1f}KB/s", flush=True)
        print("DONE", flush=True)

    def log_message(self, *a):
        pass


class S(socketserver.TCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    with open(FILE, "wb") as f:
        f.write(b"x" * (20 * 1024 * 1024))
    print("serving", FILE, "on", PORT, flush=True)
    S(("127.0.0.1", PORT), H).serve_forever()
