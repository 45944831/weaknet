#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WeakNet Windows 端测试服务器（被测流量的对端）。

协议（行协议，\n 结尾）：
  PING <tag>         -> PONG <tag> <srv_recv_ms>          服务端回时间戳
  DOWN <n>           -> 立刻写出 n 字节 payload
  UP <n>             -> 读满 n 字节后回 OK <n>
  ARR                -> 回 PING 到达时刻列表（相对首个 PING 的 ms）
  RESETARR           -> 清空到达记录
  QUIT               -> 关闭连接

用线程 + 阻塞 socket，避免和被测的 asyncio 引擎抢同一进程资源。
"""
import socket
import socketserver
import sys
import threading
import time

PAYLOAD = b"x"


class Handler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.arr = []          # PING 到达时刻（monotonic ms）
        self.arr_lock = threading.Lock()

    def _line(self):
        try:
            return self.rfile.readline()
        except Exception:
            return b""

    def handle(self):
        try:
            while True:
                line = self._line()
                if not line:
                    return
                parts = line.decode("latin1").strip().split()
                if not parts:
                    continue
                cmd = parts[0].upper()

                if cmd == "PING":
                    tag = parts[1] if len(parts) > 1 else "0"
                    t = time.monotonic() * 1000.0
                    with self.arr_lock:
                        self.arr.append(t)
                    self.wfile.write(("PONG %s %.1f\n" % (tag, t)).encode())
                    self.wfile.flush()

                elif cmd == "DOWN":
                    n = int(parts[1])
                    chunk = PAYLOAD * 65536
                    left = n
                    while left > 0:
                        k = min(left, len(chunk))
                        self.wfile.write(chunk[:k] if k != len(chunk) else chunk)
                        left -= k
                    self.wfile.flush()

                elif cmd == "UP":
                    n = int(parts[1])
                    got = 0
                    buf = bytearray()
                    while got < n:
                        d = self.rfile.read(min(65536, n - got))
                        if not d:
                            break
                        got += len(d)
                    self.wfile.write(("OK %d\n" % got).encode())
                    self.wfile.flush()

                elif cmd == "ARR":
                    with self.arr_lock:
                        arr = list(self.arr)
                    base = arr[0] if arr else 0.0
                    rel = [round(a - base, 1) for a in arr]
                    self.wfile.write(("ARR " + ",".join(str(x) for x in rel) + "\n").encode())
                    self.wfile.flush()

                elif cmd == "RESETARR":
                    with self.arr_lock:
                        self.arr = []
                    self.wfile.write(b"OK\n")
                    self.wfile.flush()

                elif cmd == "QUIT":
                    return
        except Exception:
            return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9121
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    srv = Server((host, port), Handler)
    print("SRV READY %s:%d" % (host, port), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
