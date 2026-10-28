# -*- coding: utf-8 -*-
"""真机批次 2：弱网引擎数据面（L3）——真实字节测量。

通道：PC HTTP 服务(18900) ←—Wi-Fi—→ 手机代理(8888) ←—adb forward 18821—→ PC 客户端。
即被测数据面：手机的 ProxyServer + 引擎令牌桶/延迟/丢包全部真实生效。
对应方案：A-101/102/104/105/106/107/110、W-111（并发不超限）。
"""
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
SRV_PORT = 18900
PROXY = "http://127.0.0.1:18821"
BIG_BYTES = 4 * 1024 * 1024          # 限速用文件 4MB
BASE_BYTES = 16 * 1024 * 1024        # 基线用 16MB

_op = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


def http_get(url, timeout=30):
    t0 = time.time()
    with _op.open(url, timeout=timeout) as r:
        data = r.read()
    return data, time.time() - t0


# ---------- PC 侧 HTTP 服务 ----------
class Srv(threading.Thread):
    daemon = True

    def run(self):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", SRV_PORT))
        s.listen(16)
        global _sock
        _sock = s
        while True:
            c, _ = s.accept()
            threading.Thread(target=self.handle, args=(c,), daemon=True).start()

    def handle(self, c):
        try:
            req = b""
            while b"\r\n\r\n" not in req:
                b2 = c.recv(4096)
                if not b2:
                    return
                req += b2
            head = req.split(b"\r\n", 1)[0].decode("latin1")
            parts = head.split(" ")
            path = parts[1] if len(parts) > 1 else "/"
            # 支持 POST（上行测试）
            n = 0
            low = req.lower()
            if b"content-length:" in low:
                n = int(re_search(req))
                # 已收到的 body 部分
                body = req.split(b"\r\n\r\n", 1)[1]
                while len(body) < n:
                    b2 = c.recv(65536)
                    if not b2:
                        break
                    body += b2
                c.recv(65536)  # 忽略剩余
            if path.startswith("/big"):
                size = BASE_BYTES if "base" in path else BIG_BYTES
                hdr = ("HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % size).encode()
                c.sendall(hdr)
                chunk = b"x" * 65536
                sent = 0
                while sent < size:
                    try:
                        c.sendall(chunk)
                    except Exception:
                        break
                    sent += len(chunk)
            elif path.startswith("/echo") or "POST" in head:
                hdr = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"
                c.sendall(hdr)
            else:
                hdr = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"
                c.sendall(hdr)
        except Exception:
            pass
        finally:
            try:
                c.shutdown(socket.SHUT_WR)
            except Exception:
                pass
            c.close()


def re_search(req):
    import re
    m = re.search(rb"content-length:\s*(\d+)", req, re.I)
    return m.group(1).decode() if m else "0"


def mbps(nbytes, dt):
    return nbytes * 8 / dt / 1e6 if dt > 0 else 0.0


def down_test(path="/big", timeout=120):
    t0 = time.time()
    with _op.open("http://%s:%d%s" % (PC_IP, SRV_PORT, path), timeout=timeout) as r:
        n = 0
        while True:
            b2 = r.read(65536)
            if not b2:
                break
            n += len(b2)
    dt = time.time() - t0
    return n, dt


def set_limit(**kw):
    """直接铺一份 profile 并打开限速（VPN 已开时数据面=代理面同一引擎）。"""
    base = dict(name="test", latency_up_ms=0, latency_down_ms=0, jitter_ms=0,
                down_kbps=0, up_kbps=0, loss_pct=0, stall_ms=600, disconnect_pct=0,
                timeout_pct=0, dns_fail_pct=0, reject_pct=0, flap_sec=0,
                flap_blackout_sec=2)
    base.update(kw)
    D.api("/api/profile", base)
    D.api("/api/run", {"enable": True})
    time.sleep(1.0)


def preset(key):
    D.api("/api/preset", {"key": key})
    D.api("/api/run", {"enable": True})
    time.sleep(1.0)


def off():
    D.api("/api/run", {"enable": False})
    time.sleep(1.0)


def main():
    D.setup_forward()
    subprocess.run([D.ADB, "-s", D.SERIAL, "forward", "tcp:18821", "tcp:8888"], capture_output=True)
    srv = Srv()
    srv.start()
    time.sleep(0.5)

    D.section("A-101 直连基线（不开限速）")
    off()
    n, dt = down_test("/bigbase", timeout=180)
    base_mbps = mbps(n, dt)
    D.rec("A-101 直连基线可跑大流量（>2 Mbps 即认为通道健康）",
          n == BASE_BYTES and base_mbps > 2, "%.2f Mbps, %d B" % (base_mbps, n))

    D.section("A-102 下行限速 512kbps")
    set_limit(down_kbps=512)
    n, dt = down_test()
    got = mbps(n, dt)
    ratio = got / 0.512
    D.rec("A-102 下行 512kbps 实测在 60%~140% 区间", 0.6 <= ratio <= 1.4,
          "%.3f Mbps（%.0f%%）" % (got, ratio * 100))

    D.section("A-103 上行限速 512kbps")
    set_limit(up_kbps=512)
    body = b"u" * (1 * 1024 * 1024)
    req = urllib.request.Request("http://%s:%d/echo" % (PC_IP, SRV_PORT), data=body, method="POST")
    t0 = time.time()
    try:
        with _op.open(req, timeout=120) as r:
            r.read()
        dt = time.time() - t0
        got = mbps(len(body), dt)
        ratio = got / 0.512
        D.rec("A-103 上行 512kbps 实测在 60%~140% 区间", 0.6 <= ratio <= 1.4,
              "%.3f Mbps（%.0f%%）" % (got, ratio * 100))
    except Exception as e:
        D.rec("A-103 上行限速", False, str(e)[:120])

    D.section("A-104 延迟注入 300ms（TTFB 对比）")
    off()
    _, t_base = http_get("http://%s:%d/ok" % (PC_IP, SRV_PORT))
    set_limit(latency_up_ms=300, latency_down_ms=300)
    _, t_lat = http_get("http://%s:%d/ok" % (PC_IP, SRV_PORT))
    delta = t_lat - t_base
    D.rec("A-104 双向 300ms 延迟使 TTFB 增加 ≈0.6s（±0.35s）",
          0.25 <= delta <= 0.95, "基线 %.3fs → 注入后 %.3fs（Δ=%.3fs）" % (t_base, t_lat, delta))

    D.section("A-105 丢包 20%（30 次小请求）")
    set_limit(loss_pct=20)
    fail = 0
    for i in range(30):
        try:
            http_get("http://%s:%d/ok" % (PC_IP, SRV_PORT), timeout=6)
        except Exception:
            fail += 1
    D.rec("A-105 丢包 20% 造成可观测失败/超时（≥2 次失败即算显著）", fail >= 2,
          "30 次中失败 %d" % fail)

    D.section("A-106 并发 8 路，总带宽不超限（历史 bug 2.6x）")
    set_limit(down_kbps=512)
    results = []

    def worker():
        try:
            n, dt = down_test()
            results.append(n / dt)
        except Exception as e:
            results.append(-1)

    ths = [threading.Thread(target=worker) for _ in range(8)]
    t0 = time.time()
    for t in ths:
        t.start()
    for t in ths:
        t.join(180)
    total_dt = time.time() - t0
    total_mb = sum(max(r, 0) for r in results) * 1.0
    agg = BIG_BYTES * 8 * 8 / total_dt / 1e6   # 8 路全下完的聚合速率
    ratio = agg / 0.512
    okall = all(r > 0 for r in results)
    D.rec("A-106 8 路并发总速率 ≤ 限速的 1.4 倍（无每连接配额翻倍）",
          okall and ratio <= 1.4, "聚合 %.3f Mbps（%.0f%%），8 路完成=%s" % (agg, ratio * 100, okall))

    D.section("A-107 黑洞 / 完全断网 / DNS 故障 三档")
    preset("blackhole")
    t0 = time.time()
    try:
        http_get("http://%s:%d/ok" % (PC_IP, SRV_PORT), timeout=8)
        hung = False
    except Exception:
        hung = True
    D.rec("A-107-a 黑洞：连接挂起直至超时", hung and time.time() - t0 >= 7.5,
          "耗时 %.1fs" % (time.time() - t0))

    preset("offline")
    try:
        http_get("http://%s:%d/ok" % (PC_IP, SRV_PORT), timeout=8)
        refused = False
    except Exception as e:
        refused = True
        emsg = str(e)[:80]
    D.rec("A-107-b 完全断网：立即失败", refused, emsg if refused else "竟然成功了")

    preset("dnsfail")
    try:
        http_get("http://www.baidu.com/favicon.ico", timeout=10)
        dnsok = True
    except Exception as e:
        dnsok = False
    preset("direct")
    try:
        http_get("http://www.baidu.com/favicon.ico", timeout=10)
        ok_now = True
    except Exception:
        ok_now = False
    D.rec("A-107-c DNS 故障：域名请求失败，切回直连后同样请求成功", (not dnsok) and ok_now,
          "DNS 故障时成功=%s，直连时成功=%s" % (dnsok, ok_now))

    D.section("A-110 统计自洽")
    st = D.status().get("stats", {})
    D.rec("A-110 统计接口有字节数且 > 0", isinstance(st, dict) and
          (st.get("down_bytes", 0) or 0) + (st.get("up_bytes", 0) or 0) > 0,
          json.dumps(st, ensure_ascii=False)[:200])

    off()
    D.summary("批次2：引擎数据面（真机代理通道）")


if __name__ == "__main__":
    main()
