# -*- coding: utf-8 -*-
"""A-103 上行限速专项：修正后的 echo 服务（不再多余 recv 阻塞）。"""
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import _t_dev_lib as D

PC_IP = "192.168.1.49"
SRV_PORT = 18901
PROXY = "http://127.0.0.1:18821"
_op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY}))


def handle(c):
    c.settimeout(20)
    try:
        req = b""
        while b"\r\n\r\n" not in req:
            b2 = c.recv(4096)
            if not b2:
                return
            req += b2
        head = req.split(b"\r\n", 1)[0].decode("latin1")
        rest = req.split(b"\r\n\r\n", 1)[1]
        n = 0
        import re
        m = re.search(rb"content-length:\s*(\d+)", req, re.I)
        if m:
            n = int(m.group(1))
        while len(rest) < n:
            b2 = c.recv(65536)
            if not b2:
                break
            rest += len(b2) * b"x" if False else b2
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
    except Exception as e:
        pass
    finally:
        try:
            c.close()
        except Exception:
            pass


def serve():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", SRV_PORT))
    s.listen(16)
    while True:
        c, _ = s.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


def set_limit(**kw):
    base = dict(name="test", latency_up_ms=0, latency_down_ms=0, jitter_ms=0,
                down_kbps=0, up_kbps=0, loss_pct=0, stall_ms=600, disconnect_pct=0,
                timeout_pct=0, dns_fail_pct=0, reject_pct=0, flap_sec=0,
                flap_blackout_sec=2)
    base.update(kw)
    D.api("/api/profile", base)
    D.api("/api/run", {"enable": True})
    time.sleep(0.8)


def main():
    D.setup_forward()
    subprocess.run([D.ADB, "-s", D.SERIAL, "forward", "tcp:18821", "tcp:8888"], capture_output=True)
    threading.Thread(target=serve, daemon=True).start()
    time.sleep(0.5)

    url = "http://%s:%d/echo" % (PC_IP, SRV_PORT)

    def up(size_kb, timeout=90):
        body = b"u" * size_kb * 1024
        req = urllib.request.Request(url, data=body, method="POST")
        t0 = time.time()
        with _op.open(req, timeout=timeout) as r:
            r.read()
        dt = time.time() - t0
        return size_kb * 1024 * 8 / dt / 1e6, dt

    # 先确认上行通道本身通（不限速）
    D.api("/api/run", {"enable": False})
    time.sleep(0.6)
    try:
        got, dt = up(512, timeout=60)
        D.rec("A-103-0 上行通道本身可用（不限速）", got > 2, "%.2f Mbps（%.2fs）" % (got, dt))
    except Exception as e:
        D.rec("A-103-0 上行通道本身可用（不限速）", False, str(e)[:100])

    for size in (256, 512):
        set_limit(up_kbps=512)
        try:
            got, dt = up(size)
            ratio = got / 0.512
            D.rec("A-103 上行 512kbps（%dKB）落在 60%%~150%%" % size, 0.6 <= ratio <= 1.5,
                  "%.3f Mbps（%.0f%%）" % (got, ratio * 100))
        except Exception as e:
            D.rec("A-103 上行 512kbps（%dKB）" % size, False, str(e)[:100])

    # 上行限速 + 下行不限：确认只压上行
    set_limit(up_kbps=256)
    try:
        got, dt = up(256)
        ratio = got / 0.256
        D.rec("A-103-b 上行 256kbps 落在 60%~150%", 0.6 <= ratio <= 1.5,
              "%.3f Mbps（%.0f%%）" % (got, ratio * 100))
    except Exception as e:
        D.rec("A-103-b 上行 256kbps", False, str(e)[:100])

    st = D.status().get("stats", {})
    D.rec("A-110 统计字节数 > 0", sum(v for v in st.values() if isinstance(v, (int, float))) > 0,
          json.dumps(st, ensure_ascii=False)[:200])
    D.api("/api/run", {"enable": False})
    D.summary("A-103 上行限速专项")


if __name__ == "__main__":
    main()
