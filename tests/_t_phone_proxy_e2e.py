# -*- coding: utf-8 -*-
"""真机 ProxyServer 数据面实测（adb reverse 回连环，不依赖跨网路由）。

拓扑：
  PC ──adb forward 18888──> 手机 ProxyServer:8888 ──> 127.0.0.1:9126
  PC ──adb reverse 9126────> 手机 127.0.0.1:9126 ──(USB)──> PC HTTP 服务

VPN 路径（tun0）需要手机与 PC 三层可达，当前手机在隔离 Wi-Fi 上做不到，
故本脚本专测 ProxyServer -> Pipe 这条数据面（延迟/限速/丢包/切场景立即生效）。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import http.server
import json
import socketserver
import socket
import subprocess
import sys
import threading
import time
import urllib.request

ADB = r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe"
CTRL = "http://127.0.0.1:18899"          # -> 手机 8899
PROXY = ("127.0.0.1", 18888)             # -> 手机 8888
SRV_PORT = 9126                          # PC  HTTP 服务（adb reverse 回环到手机）
RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""),
          flush=True)


def sh(cmd, t=30):
    p = subprocess.run([ADB, "shell", cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", timeout=t)
    return p.stdout.strip()


def api(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(CTRL + path, data=body,
                                 method=("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": str(e)}


def set_scene(key, enable=True):
    api("/api/preset", {"key": key})
    api("/api/run", {"enable": enable})


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        n = 0
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if kv.startswith("n="):
                    n = int(kv[2:] or 0)
        n = max(0, min(n, 64 * 1024 * 1024))
        body = b"z" * n
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def via_proxy(nbytes, timeout=180):
    """经手机代理下载 nbytes，返回 (实收字节, 耗时秒)。"""
    s = socket.create_connection(PROXY, timeout=timeout)
    try:
        req = ("GET http://127.0.0.1:%d/big?n=%d HTTP/1.1\r\n"
               "Host: 127.0.0.1:%d\r\nConnection: close\r\n\r\n"
               % (SRV_PORT, nbytes, SRV_PORT))
        s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            d = s.recv(1)
            if not d:
                raise OSError("proxy closed during headers")
            buf += d
        head, rest = buf.split(b"\r\n\r\n", 1)
        cl = 0
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"content-length:"):
                cl = int(line.split(b":", 1)[1])
        t0 = time.time()
        body = rest
        while len(body) < cl:
            d = s.recv(65536)
            if not d:
                break
            body += d
        return len(body), time.time() - t0
    finally:
        s.close()


def rtt_via_proxy(timeout=60):
    """一次极短请求的往返（代理自身开销 + 注入延迟）。"""
    s = socket.create_connection(PROXY, timeout=timeout)
    try:
        req = ("GET http://127.0.0.1:%d/big?n=0 HTTP/1.1\r\n"
               "Host: 127.0.0.1:%d\r\nConnection: close\r\n\r\n"
               % (SRV_PORT, SRV_PORT))
        t0 = time.time()
        s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            d = s.recv(1)
            if not d:
                break
            buf += d
        return (time.time() - t0) * 1000.0
    finally:
        s.close()


def main():
    srv = S(("127.0.0.1", SRV_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("PC 服务 127.0.0.1:%d 就绪" % SRV_PORT, flush=True)

    subprocess.run([ADB, "reverse", "tcp:%d" % SRV_PORT, "tcp:%d" % SRV_PORT],
                   capture_output=True, timeout=20)
    subprocess.run([ADB, "forward", "tcp:18888", "tcp:8888"],
                   capture_output=True, timeout=20)
    sh("ls /data/local/tmp", t=10)

    st = api("/api/status")
    rec("控制接口可达", "error" not in st, "vpn_active=%s listen=%s"
        % (st.get("vpn_active"), st.get("listen")))
    rec("ProxyServer 在 8888 监听", (st.get("listen") or {}).get("port") == 8888,
        json.dumps(st.get("listen"), ensure_ascii=False))

    # 回环连通性：直连档拿基线
    set_scene("direct")
    got, el = via_proxy(1024 * 1024)
    base_kbps = got * 8 / 1024 / el if el > 0 else 0
    rec("代理路径回环连通（1MB 收满）", got == 1024 * 1024,
        "%.0f kbps / %.2fs" % (base_kbps, el))
    base_rtt = rtt_via_proxy()
    print("    基线 RTT（代理自身开销）%.0f ms" % base_rtt, flush=True)

    # ---- 限速：4g 20000 kbps ----
    set_scene("4g")
    got, el = via_proxy(4 * 1024 * 1024)
    kbps = got * 8 / 1024 / el
    rec("代理路径 4g 20000kbps 限速", got == 4 * 1024 * 1024 and 0.80 * 20000 <= kbps <= 1.30 * 20000,
        "%.0f kbps（设定 20000）el=%.2fs" % (kbps, el))

    # ---- 低带宽：拥塞 512 kbps（大样本，避开桶突发）----
    set_scene("congestion")
    got, el = via_proxy(1024 * 1024)
    kbps = got * 8 / 1024 / el
    rec("代理路径 拥塞 512kbps 限速", got == 1024 * 1024 and 0.75 * 512 <= kbps <= 1.40 * 512,
        "%.0f kbps（设定 512）el=%.2fs" % (kbps, el))

    # ---- 延迟：卫星 1400ms RTT，短请求 ----
    set_scene("satellite")
    r = rtt_via_proxy()
    inj = max(0.0, r - base_rtt)
    rec("代理路径 卫星档注入延迟", inj >= 1400 * 0.7,
        "往返 %.0f ms，注入 %.0f ms（设定 1400）" % (r, inj))

    # ---- 延迟：高延迟 800ms ----
    set_scene("high_latency")
    r = rtt_via_proxy()
    inj = max(0.0, r - base_rtt)
    rec("代理路径 高延迟档注入延迟", inj >= 800 * 0.7,
        "往返 %.0f ms，注入 %.0f ms（设定 800）" % (r, inj))

    # ---- 切场景立即生效：拥塞档下载中切直连，应立即放行 ----
    set_scene("congestion")
    res = {}

    def worker():
        try:
            res["r"] = via_proxy(8 * 1024 * 1024, timeout=200)
        except Exception as e:
            res["e"] = repr(e)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(2.0)
    t0 = time.time()
    set_scene("direct")
    t.join(timeout=200)
    sw = time.time() - t0
    g2, e2 = res.get("r", (0, 0))
    rec("代理路径 限速中切直连立即放行", g2 == 8 * 1024 * 1024 and sw < 5.0,
        "切换后 %.2fs 收完 8MB（512kbps 下需 ~125s）got=%d" % (sw, g2))

    # ---- 停止限速后恢复直连 ----
    set_scene("congestion")
    api("/api/run", {"enable": False})
    got, el = via_proxy(2 * 1024 * 1024)
    rec("代理路径 停止后恢复直连", got == 2 * 1024 * 1024 and el < 3.0,
        "2MB 耗时 %.2fs" % el)

    # ---- 丢包：高丢包档 512KB 应仍能收完（停顿但不死）----
    set_scene("high_loss")
    got, el = via_proxy(512 * 1024, timeout=120)
    rec("代理路径 高丢包档传输不停死", got > 0,
        "收到 %d / 524288 B，%.2fs" % (got, el))
    api("/api/run", {"enable": False})

    st = api("/api/status")
    s = st.get("stats") or {}
    print("    引擎统计: %s" % json.dumps(s, ensure_ascii=False), flush=True)
    rec("引擎统计有流量记账", (s.get("up", 0) + s.get("down", 0)) > 0,
        "up=%s down=%s total=%s active=%s" % (s.get("up"), s.get("down"),
                                              s.get("total"), s.get("active")))

    srv.shutdown()
    bad = [x for x in RESULTS if not x[1]]
    print("\n===== 真机 ProxyServer 数据面：%d/%d 通过 ====="
          % (len(RESULTS) - len(bad), len(RESULTS)))
    for n, _o, d in bad:
        print("FAILED: %s | %s" % (n, d))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
