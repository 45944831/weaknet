import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import socket, threading, sys
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 9127)); s.listen(5)
print("LISTEN 9127", flush=True)
def handle(c, addr):
    print("CONN from %s" % (addr,), flush=True)
    c.settimeout(8)
    try:
        data = c.recv(4096)
        print("RECV %d bytes: %r" % (len(data), data[:40]), flush=True)
        if data: c.sendall(b"PONG-TUN-OK\r\n")
    except Exception as e:
        print("ERR %s" % e, flush=True)
    c.close()
while True:
    c, addr = s.accept()
    threading.Thread(target=handle, args=(c, addr), daemon=True).start()
