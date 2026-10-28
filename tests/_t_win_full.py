# -*- coding: utf-8 -*-
"""WeakNet Windows 引擎全量测试（真机测试的对照基线 + 引擎回归）。

前提：weaknet.py 已启动（proxy 127.0.0.1:8888 / ctrl 127.0.0.1:8899），
      _w_srv.py 已监听 192.168.1.49:9126。
结果同时写 _t_win_full.json 与 stdout。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import http.server
import json
import os
import socket
import socketserver
import threading
import time
import urllib.request

CTRL = "http://127.0.0.1:8899"
PROXY = ("127.0.0.1", 8888)
SRV = ("192.168.1.49", 9125)
RESULTS = []
BODY = b"y" * (200 * 1024)
HTTP_PORT = 18082


def api(path, data=None, method=None):
    url = CTRL + path
    body = None
    if data is not None:
        body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method=method or ("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=8) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def rec(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""), flush=True)


def set_scene(key, enable=True):
    api("/api/preset", {"key": key})
    api("/api/run", {"enable": enable})


def tunnel(timeout=10):
    s = socket.create_connection(PROXY, timeout=timeout)
    s.sendall(("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n\r\n" % (SRV[0], SRV[1], SRV[0], SRV[1])).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = s.recv(1)
        if not d:
            raise OSError("proxy closed during CONNECT: %r" % buf)
        buf += d
    if b" 200 " not in buf.split(b"\r\n")[0]:
        raise OSError("CONNECT failed: %r" % buf.split(b"\r\n")[0])
    return s


def read_line(s):
    buf = b""
    while not buf.endswith(b"\n"):
        d = s.recv(1)
        if not d:
            break
        buf += d
    return buf


def ping_seq(n=5, timeout=20):
    s = tunnel(timeout)
    out = []
    try:
        for i in range(n):
            t0 = time.time()
            s.sendall(("PING %d\n" % i).encode())
            line = read_line(s)
            if not line:
                out.append(None)
                break
            out.append((time.time() - t0) * 1000.0)
    finally:
        s.close()
    return out


def download(nbytes, timeout=90):
    s = tunnel(timeout)
    t0 = time.time()
    got = 0
    try:
        s.sendall(("DOWN %d\n" % nbytes).encode())
        while got < nbytes:
            d = s.recv(min(65536, nbytes - got))
            if not d:
                break
            got += len(d)
    finally:
        s.close()
    el = max(time.time() - t0, 1e-6)
    return got, el


def http_get_via_proxy(path, host, port, extra=""):
    s = socket.create_connection(PROXY, timeout=15)
    req = ("GET http://%s:%d%s HTTP/1.1\r\nHost: %s:%d\r\n%sConnection: close\r\n\r\n"
           % (host, port, path, host, port, extra))
    s.sendall(req.encode())
    s.shutdown(socket.SHUT_WR)   # 半关闭：只关本方向
    data = b""
    try:
        while True:
            d = s.recv(65536)
            if not d:
                break
            data += d
    finally:
        s.close()
    return data


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *a):
        pass


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    # ---------- 0. 本地 HTTP 服务（半关闭 / 请求头延迟用） ----------
    srv = S(("127.0.0.1", HTTP_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # ---------- 1. API 契约 ----------
    st, d = api("/api/status")
    need = ["profile", "preset_key", "limit_on", "gate_open", "listen", "lan_ip", "sysproxy", "stats", "events"]
    rec("API /api/status 结构完整", st == 200 and all(k in d for k in need),
        "missing=" + ",".join(k for k in need if k not in d))
    rec("启动后默认未限速", d.get("limit_on") is False and d.get("preset_key") == "direct",
        "limit_on=%s preset=%s" % (d.get("limit_on"), d.get("preset_key")))

    st, pl = api("/api/presets")
    rec("预设 17 支", st == 200 and len(pl) == 17, "n=%d" % len(pl))

    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(CTRL + "/", timeout=5) as r:
        txt = r.read().decode("utf-8", "ignore")
    rec("根路径返回说明而非空白", "控制台已改为纯桌面界面" in txt, txt.splitlines()[0][:40])

    st, _ = api("/api/preset", {"key": "not_exist"})
    rec("非法 preset key → 400", st == 400, "status=%d" % st)
    st, _ = api("/api/profile", {"down_kbps": "abc"})
    rec("profile 非数字被忽略不崩", st == 200, "status=%d" % st)
    st, _ = api("/api/run", {"enable": "x"})
    rec("run 非法 body → 400", st == 400, "status=%d" % st)
    st, _ = api("/api/upreset", {"name": "x"})
    rec("upreset 缺 profile → 400", st == 400, "status=%d" % st)
    st, _ = api("/api/upreset/del", {})
    rec("upreset/del 缺 id → 400", st == 400, "status=%d" % st)
    st, _ = api("/api/scope", {"mode": "bogus", "list": []})
    rec("scope 非法 mode 不崩", st in (200, 400), "status=%d" % st)
    st, _ = api("/api/listen", {"host": "1.2.3.4"})
    rec("listen 非法 host → 400", st == 400, "status=%d" % st)

    # ---------- 2. 直连基线 ----------
    api("/api/preset", {"key": "direct"})
    api("/api/run", {"enable": False})
    got, el = download(1024 * 1024)
    base_kbps = got * 8 / 1024 / el
    rec("直连基线 1MB 完整收满", got == 1024 * 1024, "%.0f kbps" % base_kbps)
    rtt_direct = ping_seq(3)
    rec("直连 RTT 基线", all(r is not None for r in rtt_direct),
        "rtt=%s" % [round(x, 1) if x else None for x in rtt_direct])

    # ---------- 3. 17 档矩阵 ----------
    PLAN = {  # key: (下载字节数, ping 次数)
        "direct": (1024 * 1024, 3), "wifi": (1024 * 1024, 3), "4g": (1024 * 1024, 3),
        "5g": (1024 * 1024, 3), "5g_weak": (512 * 1024, 3), "wifi_weak": (512 * 1024, 3),
        "4g_weak": (256 * 1024, 3), "congestion": (128 * 1024, 3),
        "satellite": (256 * 1024, 3), "high_loss": (512 * 1024, 3),
        "high_latency": (512 * 1024, 3), "highspeed_rail": (256 * 1024, 3),
        "elevator": (64 * 1024, 3),
    }
    names = {p["key"]: p["name"] for p in pl}
    matrix = []
    for key, (size, nping) in PLAN.items():
        set_scene(key)
        st, d = api("/api/status")
        prof = d["profile"]
        got, el = download(size, timeout=120)
        kbps = got * 8 / 1024 / el
        rtts = ping_seq(nping)
        rtts_v = [round(x, 1) for x in rtts if x is not None]
        exp_lat = prof["latency_up_ms"] + prof["latency_down_ms"]
        exp_kbps = prof["down_kbps"]
        # 吞吐判定：设定 0 表示不限，只要求接近基线；否则允许 0.5~1.6 倍（TCP 慢启动/窗口影响）
        if exp_kbps == 0:
            thr_ok = kbps > base_kbps * 0.5
        else:
            thr_ok = 0.45 * exp_kbps <= kbps <= 1.6 * exp_kbps
        lat_ok = bool(rtts_v) and (min(rtts_v) >= exp_lat * 0.55 if exp_lat else True)
        ok = got == size and thr_ok and lat_ok
        detail = "设定 %s kbps / 实测 %.0f kbps；延迟设定 %d ms / 实测 %s ms" % (
            exp_kbps or "∞", kbps, exp_lat, rtts_v)
        rec("场景 %-12s %s" % (key, names.get(key, "")), ok, detail)
        matrix.append({"key": key, "name": names.get(key), "exp_kbps": exp_kbps,
                       "got_kbps": round(kbps, 1), "exp_lat": exp_lat, "rtt": rtts_v,
                       "ok": ok})

    # ---------- 4. 极端场景 ----------
    set_scene("airplane")
    fails = 0
    for _ in range(5):
        try:
            s = tunnel(6)
            s.sendall(b"PING x\n")
            s.settimeout(3)
            if not read_line(s):
                fails += 1
            s.close()
        except Exception:
            fails += 1
    rec("完全断网：5/5 连不上", fails == 5, "失败 %d/5" % fails)

    set_scene("blackhole")
    fails = 0
    t0 = time.time()
    for _ in range(5):
        try:
            s = tunnel(6)
            s.sendall(b"PING x\n")
            s.settimeout(2.5)
            if not read_line(s):
                fails += 1
            s.close()
        except Exception:
            fails += 1
    rec("黑洞：5/5 无响应", fails == 5, "失败 %d/5，耗时 %.1fs" % (fails, time.time() - t0))

    # 黑洞中点停止应立即放行
    api("/api/run", {"enable": True})
    hang = {}

    def _hang():
        try:
            s = tunnel(20)
            s.sendall(b"PING late\n")
            s.settimeout(15)
            t = time.time()
            line = read_line(s)
            hang["released"] = (time.time() - t, bool(line))
            s.close()
        except Exception as e:
            hang["released"] = (None, False)

    th = threading.Thread(target=_hang, daemon=True)
    th.start()
    time.sleep(1.2)
    api("/api/run", {"enable": False})
    th.join(6)
    rel = hang.get("released")
    rec("黑洞中停止限速立即放行", rel is not None and rel[1] and rel[0] is not None and rel[0] < 4,
        "放行耗时 %s s" % (round(rel[0], 2) if rel and rel[0] else rel))

    set_scene("dns_fail")
    rand = "weaknet-%d.example.com" % int(time.time())
    try:
        data = http_get_via_proxy("/", rand, 80)
        dns_blocked = (b"200 OK" not in data.split(b"\r\n")[0])
    except Exception:
        dns_blocked = True
    data_ip = http_get_via_proxy("/", "127.0.0.1", HTTP_PORT)
    ip_ok = b"200 OK" in data_ip.split(b"\r\n")[0]
    rec("DNS 故障：域名失败 / IP 直连正常", dns_blocked and ip_ok,
        "域名被拦=%s，IP 直连=%s" % (dns_blocked, ip_ok))

    set_scene("flapping")
    st, d = api("/api/status")
    gates = []
    for _ in range(11):
        st, d = api("/api/status")
        gates.append(d.get("gate_open"))
        time.sleep(1)
    rec("周期断网：闸门有开有关", len(set(gates)) == 2, "gate 序列=%s" % "".join("1" if g else "0" for g in gates))

    # ---------- 5. 自定义参数 ----------
    api("/api/profile", {"down_kbps": 300, "latency_up_ms": 0, "latency_down_ms": 0})
    api("/api/run", {"enable": True})
    got, el = download(128 * 1024, timeout=60)
    kbps = got * 8 / 1024 / el
    rec("自定义 300 kbps 限速", got == 128 * 1024 and 200 <= kbps <= 420,
        "实测 %.1f kbps（理论 300）" % kbps)

    st, d = api("/api/upreset", {"name": "测试-越界", "profile": {"loss_pct": 999, "down_kbps": -5}})
    item = next((i for i in d.get("items", []) if i["name"] == "测试-越界"), None)
    clamped = item and item["profile"]["loss_pct"] <= 100 and item["profile"]["down_kbps"] >= 0
    rec("自定义保存越界值夹到 0~100", clamped,
        "loss=%s down=%s" % (item and item["profile"]["loss_pct"], item and item["profile"]["down_kbps"]))

    st, d = api("/api/upreset", {"name": "测试-越界", "profile": {"down_kbps": 1000}})
    names_now = [i["name"] for i in d.get("items", [])]
    rec("自定义重名自动加序号", any(n.startswith("测试-越界") and n != "测试-越界" for n in names_now),
        str(names_now))

    uid = next(i["id"] for i in d["items"] if i["name"] == "测试-越界")
    api("/api/preset", {"key": "u:" + uid})
    st, d2 = api("/api/status")
    rec("自定义场景可应用", d2.get("preset_key") == "u:" + uid, d2.get("preset_key"))
    api("/api/upreset/del", {"id": uid})
    st, d3 = api("/api/upresets")
    rec("自定义场景可删除", all(i["id"] != uid for i in d3.get("items", [])),
        "剩余 %d 条" % len(d3.get("items", [])))
    # 清理所有测试条目
    for i in d3.get("items", []):
        if i["name"].startswith("测试-越界"):
            api("/api/upreset/del", {"id": i["id"]})

    # ---------- 6. 并发带宽（全局令牌桶） ----------
    set_scene("4g")   # 20000 kbps
    per = 512 * 1024
    res = []

    def _dl():
        try:
            g, e = download(per, timeout=60)
            res.append((g, e))
        except Exception:
            res.append((0, 1))

    t0 = time.time()
    ths = [threading.Thread(target=_dl) for _ in range(4)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    wall = time.time() - t0
    total_kbps = sum(g for g, _ in res) * 8 / 1024 / wall
    full = all(g == per for g, _ in res)
    rec("4 并发总带宽不超标", full and total_kbps <= 20000 * 1.25,
        "合计 %.0f kbps（设定 20000，倍率 %.2fx）" % (total_kbps, total_kbps / 20000))

    # ---------- 7. 半关闭 + 请求头延迟（v1.1.4 修复） ----------
    set_scene("direct")
    data = http_get_via_proxy("/", "127.0.0.1", HTTP_PORT)
    hdr, _, body = data.partition(b"\r\n\r\n")
    cl = 0
    for line in hdr.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            cl = int(line.split(b":")[1])
    rec("半关闭后响应体完整", b"200 OK" in hdr.split(b"\r\n")[0] and len(body) == cl and cl == len(BODY),
        "收到 %d / 声明 %d" % (len(body), cl))

    set_scene("high_latency")   # 400+400
    t0 = time.time()
    data = http_get_via_proxy("/", "127.0.0.1", HTTP_PORT)
    el = (time.time() - t0) * 1000
    rec("请求头也吃延迟（v1.1.4）", el >= 700, "整请求耗时 %.0f ms（设定 RTT 800）" % el)

    # ---------- 8. 长连接延迟（v1.1.3 修复） ----------
    rtts = ping_seq(5, timeout=25)
    vals = [round(x, 1) for x in rtts if x]
    ok = len(vals) == 5 and min(vals) >= 700
    rec("长连接内每包都吃延迟", ok, "RTT=%s（设定 800）" % vals)

    # ---------- 9. 限速总开关 ----------
    api("/api/run", {"enable": False})
    st, d = api("/api/status")
    got, el = download(1024 * 1024, timeout=60)
    kbps_off = got * 8 / 1024 / el
    rec("停止限速后恢复直连（场景保留）",
        kbps_off > 5000 and d.get("preset_key") == "high_latency",
        "%.0f kbps，preset=%s" % (kbps_off, d.get("preset_key")))

    # ---------- 10. 接管范围（Windows 按进程名） ----------
    set_scene("congestion")   # 512 kbps
    api("/api/scope", {"mode": "only", "list": ["notepad.exe"]})
    got, el = download(256 * 1024, timeout=60)
    bypass_kbps = got * 8 / 1024 / el
    rec("范围外进程不受限（only notepad）", bypass_kbps > 2000,
        "%.0f kbps（设定 512，应旁路）" % bypass_kbps)

    api("/api/scope", {"mode": "only", "list": ["python.exe"]})
    got, el = download(128 * 1024, timeout=60)
    in_kbps = got * 8 / 1024 / el
    rec("范围内进程受限（only python.exe）", 300 <= in_kbps <= 800,
        "%.0f kbps（设定 512）" % in_kbps)
    api("/api/scope", {"mode": "all", "list": []})
    st, d = api("/api/scope")
    rec("接管范围可回到全部", d.get("mode") == "all", json.dumps(d, ensure_ascii=False)[:80])

    api("/api/run", {"enable": False})
    api("/api/preset", {"key": "direct"})

    # ---------- 汇总 ----------
    fails = [r for r in RESULTS if not r["ok"]]
    print("\n==== 汇总：%d 项，通过 %d，失败 %d ====" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)), flush=True)
    for f in fails:
        print("FAILED:", f["name"], "|", f["detail"], flush=True)
    with open("_t_win_full.json", "w", encoding="utf-8") as fp:
        json.dump({"results": RESULTS, "matrix": matrix,
                   "base_kbps": round(base_kbps, 1)}, fp, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
