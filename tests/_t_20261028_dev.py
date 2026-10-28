# -*- coding: utf-8 -*-
"""2026-10-28 全面深度测试 —— Android 真机深度（数据面 / 组合 / 悬浮窗 / 稳定性 / P0 复现）。

通道：
  控制  adb forward 18899 → 设备 8899
  代理  adb forward 18821 → 设备 8888（App 内 ProxyServer）
  对端  adb reverse 9125  → PC 上 tests/_w_srv.py（行协议：PING / DOWN / UP）
所以被测的是手机上真实的 ProxyServer + 令牌桶 + 延迟/丢包引擎。
"""
import json
import os
import socket
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t_dev_lib as D

PROXY = ("127.0.0.1", 18821)
SRV = ("127.0.0.1", 9125)
WIN = "http://127.0.0.1:8899"


# ------------------------------------------------------------------ 工具
def wapi(path, data=None, timeout=30):
    import urllib.request
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(WIN + path, data=body,
                                 method=("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"_err": repr(e)}


def api(path, data=None, timeout=20):
    return D.api_try(path, data, timeout) if hasattr(D, "api_try") else D.api(path, data)


class Tunnel:
    def __init__(self, timeout=120):
        self.s = socket.create_connection(PROXY, timeout=timeout)
        self.s.sendall(("CONNECT %s:%d HTTP/1.1\r\n\r\n" % SRV).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            d = self.s.recv(4096)
            if not d:
                raise RuntimeError("proxy closed")
            buf += d
        if b" 200 " not in buf.split(b"\r\n")[0]:
            raise RuntimeError("CONNECT: %r" % buf[:60])
        self.s.settimeout(timeout)
        self.buf = b""

    def _line(self):
        while b"\n" not in self.buf:
            d = self.s.recv(65536)
            if not d:
                raise RuntimeError("closed")
            self.buf += d
        line, _, self.buf = self.buf.partition(b"\n")
        return line

    def _n(self, n):
        while len(self.buf) < n:
            d = self.s.recv(65536)
            if not d:
                raise RuntimeError("closed")
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def ping(self, tag=0):
        t0 = time.time()
        self.s.sendall(("PING %d\n" % tag).encode())
        self._line()
        return (time.time() - t0) * 1000.0

    def down(self, n):
        t0 = time.time()
        self.s.sendall(("DOWN %d\n" % n).encode())
        got = self._n(n)
        return len(got), time.time() - t0

    def up(self, n, chunk=32768):
        t0 = time.time()
        self.s.sendall(("UP %d\n" % n).encode())
        block = b"x" * chunk
        left = n
        while left > 0:
            k = min(left, chunk)
            self.s.sendall(block[:k])
            left -= k
        self._line()
        return n, time.time() - t0

    def close(self):
        try:
            self.s.sendall(b"QUIT\n")
        except Exception:
            pass
        try:
            self.s.close()
        except Exception:
            pass


def down(n, timeout=180):
    t = Tunnel(timeout)
    try:
        return t.down(n)
    finally:
        t.close()


def up(n, timeout=180):
    t = Tunnel(timeout)
    try:
        return t.up(n)
    finally:
        t.close()


def mbps(b, sec):
    return b * 8 / sec / 1e6


def scene(key, on=True):
    api("/api/preset", {"key": key})
    api("/api/run", {"enable": on})


# ------------------------------------------------------------------ 用例
def part_basic():
    D.section("D1 基线与接口")
    # 先复位：上一轮测试可能留下 limit_on=true 的自定义参数，基线会被限速污染
    api("/api/preset", {"key": "direct"})
    api("/api/run", {"enable": False})
    time.sleep(0.6)
    s = api("/api/status")
    D.rec("D-01 状态结构（含 vpn_active/float/scope）",
          all(k in s for k in ("profile", "preset_key", "limit_on", "vpn_active",
                               "float", "scope", "stats")),
          "keys=%s" % ",".join(sorted(s.keys()))[:80])
    D.rec("D-02 复位后未限速且直连",
          s.get("limit_on") is False and s.get("preset_key") == "direct",
          "limit_on=%s preset=%s" % (s.get("limit_on"), s.get("preset_key")))

    got, sec = down(4 * 1024 * 1024)
    base = mbps(got, sec)
    D.rec("D-03 直连基线 4MB 收满", got == 4 * 1024 * 1024 and base > 5,
          "%.1f Mbps（%.2fs，USB 通道）" % (base, sec))
    return base


def part_engine():
    D.section("D2 弱网引擎数据面")
    scene("4g", True)
    got, sec = down(4 * 1024 * 1024, timeout=240)
    m = mbps(got, sec)
    D.rec("D-04 下行限速 4g（设定 20 Mbps）", got == 4 * 1024 * 1024 and 0.70 <= m / 20 <= 1.40,
          "%.2f Mbps = %.2fx" % (m, m / 20))

    scene("congestion", True)
    got, sec = down(128 * 1024, timeout=180)
    m = mbps(got, sec)
    D.rec("D-05 下行限速 congestion（设定 0.512 Mbps）", 0.65 <= m / 0.512 <= 1.50,
          "%.3f Mbps = %.2fx" % (m, m / 0.512))

    got, sec = up(64 * 1024, timeout=180)
    m = mbps(got, sec)
    D.rec("D-06 上行限速 congestion（设定 0.256 Mbps）", 0.60 <= m / 0.256 <= 1.55,
          "%.3f Mbps = %.2fx" % (m, m / 0.256))

    # 并发共享
    out = []

    def w():
        try:
            g, s_ = down(32 * 1024, timeout=180)
            out.append(g)
        except Exception:
            out.append(0)

    ts = [threading.Thread(target=w) for _ in range(8)]
    t0 = time.time()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    agg = mbps(sum(out), time.time() - t0)
    D.rec("D-07 8 并发共享一份带宽（设定 0.512 Mbps）",
          0.60 <= agg / 0.512 <= 1.70 and out.count(0) == 0,
          "聚合 %.3f Mbps = %.2fx，成功 %d/8" % (agg, agg / 0.512, 8 - out.count(0)))

    # 延迟：逐包
    scene("4g", True)
    t = Tunnel(120)
    rs = []
    try:
        for i in range(5):
            rs.append(t.ping(i))
    finally:
        t.close()
    D.rec("D-08 延迟逐包注入 4g（理论 +100ms）",
          all(70 <= r <= 260 for r in rs),
          "RTT=%s" % [round(x) for x in rs])

    scene("high_latency", True)
    t = Tunnel(180)
    rs = []
    try:
        for i in range(4):
            rs.append(t.ping(i))
    finally:
        t.close()
    D.rec("D-09 延迟逐包注入 high_latency（理论 +800ms）",
          all(600 <= r <= 1600 for r in rs) and all(r > 400 for r in rs[1:]),
          "RTT=%s" % [round(x) for x in rs])

    # 丢包
    scene("direct", False)
    g0, s0 = down(1024 * 1024, timeout=120)
    scene("high_loss", True)
    g1, s1 = down(1024 * 1024, timeout=240)
    D.rec("D-10 丢包 30% 显著压低吞吐", g1 == 1024 * 1024 and mbps(g1, s1) < mbps(g0, s0) * 0.6,
          "直连 %.1f → 高丢包 %.2f Mbps" % (mbps(g0, s0), mbps(g1, s1)))

    # 统计
    s0 = api("/api/status")["stats"]
    scene("4g", True)
    down(256 * 1024, timeout=120)
    time.sleep(0.5)
    s1 = api("/api/status")["stats"]
    D.rec("D-11 统计 down 字节随传输增长", s1["down"] - s0["down"] >= 256 * 1024,
          "增量 %d B" % (s1["down"] - s0["down"]))
    scene("direct", False)


def part_faults():
    D.section("D3 故障注入")
    scene("blackhole", True)
    t0 = time.time()
    try:
        s = socket.create_connection(PROXY, timeout=15)
        s.sendall(("CONNECT %s:%d HTTP/1.1\r\n\r\n" % SRV).encode())
        s.settimeout(2.5)
        try:
            d = s.recv(100)
        except socket.timeout:
            d = b""
        hung = (time.time() - t0) > 2.0 and b"200" not in d
        api("/api/run", {"enable": False})
        s.settimeout(6)
        try:
            d2 = s.recv(100)
        except Exception:
            d2 = b""
        s.close()
        D.rec("D-12 黑洞：挂住不返回", hung, "2.5s 内无响应")
        D.rec("D-13 黑洞：停止限速后放行", b"200" in d2, "停止后收到 %r" % d2[:24])
    except Exception as e:
        D.rec("D-12 黑洞：挂住不返回", False, repr(e))
        D.rec("D-13 黑洞：停止限速后放行", False, "见上")

    scene("airplane", True)
    t0 = time.time()
    try:
        s = socket.create_connection(PROXY, timeout=10)
        s.sendall(("CONNECT %s:%d HTTP/1.1\r\n\r\n" % SRV).encode())
        s.settimeout(8)
        d = s.recv(200)
        s.close()
    except Exception as e:
        d = b"ERR:%r" % e
    D.rec("D-14 完全断网：立即失败", b"200" not in d and time.time() - t0 < 4.0,
          "%.2fs → %r" % (time.time() - t0, d[:40]))

    scene("dns_fail", True)
    try:
        s = socket.create_connection(PROXY, timeout=10)
        s.sendall(b"CONNECT example.com:80 HTTP/1.1\r\n\r\n")
        s.settimeout(8)
        d = s.recv(200)
        s.close()
    except Exception as e:
        d = b"ERR:%r" % e
    try:
        s = socket.create_connection(PROXY, timeout=10)
        s.sendall(("CONNECT %s:%d HTTP/1.1\r\n\r\n" % SRV).encode())
        s.settimeout(8)
        d2 = s.recv(200)
        s.close()
    except Exception as e:
        d2 = b"ERR:%r" % e
    D.rec("D-15 DNS 故障：域名失败", b"200" not in d, "%r" % d[:50])
    D.rec("D-16 DNS 故障：IP 直连 200", b"200" in d2, "%r" % d2[:50])
    scene("direct", False)


def part_combo():
    D.section("D4 弱网组合（真机）")
    api("/api/run", {"enable": False})
    combos = api("/api/combos")
    items = combos if isinstance(combos, list) else combos.get("items", [])
    for it in items:
        api("/api/combo/delete", {"id": it.get("id")})

    rc = api("/api/combo", {"name": "真机自动化组合", "loop": False, "steps": [
        {"key": "4g", "minutes": 0, "seconds": 2},
        {"key": "airplane", "minutes": 0, "seconds": 2},
        {"key": "direct", "minutes": 0, "seconds": 2}]})
    cid = rc.get("id")
    D.rec("D-20 真机新建组合", bool(cid), "id=%s" % cid)

    rc6 = api("/api/combo", {"name": "六段", "loop": False,
                             "steps": [{"key": "4g", "seconds": 1}] * 6})
    D.rec("D-21 段上限 5", rc6.get("steps") == 5, "steps=%s" % rc6.get("steps"))

    api("/api/preset", {"key": "c:%s" % cid})
    api("/api/run", {"enable": True})
    seen = []
    t_end = time.time() + 8.5
    while time.time() < t_end:
        s = api("/api/status")
        nm = s.get("profile", {}).get("name")
        if not seen or seen[-1] != nm:
            seen.append(nm)
        time.sleep(0.2)
    D.rec("D-22 组合按时间轴切段", len(seen) >= 3 and "直连（不限制）" in seen,
          "序列=%s" % seen)

    # P1：删除播放中的组合
    api("/api/preset", {"key": "c:%s" % cid})
    api("/api/run", {"enable": True})
    time.sleep(1.0)
    api("/api/combo/delete", {"id": cid})
    time.sleep(0.8)
    s = api("/api/status")
    D.rec("D-23 【P1】删除播放中的组合后回直连",
          s.get("limit_on") is False and s.get("preset_key") == "direct",
          "limit_on=%s preset_key=%s profile=%s（期望 False/direct）" % (
              s.get("limit_on"), s.get("preset_key"), s.get("profile", {}).get("name")))
    api("/api/run", {"enable": False})


def part_preset():
    D.section("D5 自定义场景（真机）")
    ups = api("/api/upresets")
    items = ups if isinstance(ups, list) else ups.get("items", [])
    for it in items:
        if str(it.get("name", "")).startswith("真机自动化"):
            api("/api/upreset/del", {"id": it.get("id")})

    rc = api("/api/upreset", {"name": "真机自动化A",
                              "profile": {"down_kbps": 777, "up_kbps": 333,
                                          "latency_up_ms": 88, "latency_down_ms": 99}})
    pid = rc.get("id")
    D.rec("D-30 保存自定义场景", bool(pid), "id=%s" % pid)
    api("/api/preset", {"key": "u:%s" % pid})
    api("/api/run", {"enable": True})
    p = api("/api/status").get("profile", {})
    D.rec("D-31 应用自定义参数逐字段一致",
          p.get("down_kbps") == 777 and p.get("up_kbps") == 333
          and p.get("latency_up_ms") == 88 and p.get("latency_down_ms") == 99,
          "down=%s up=%s lat=%s/%s" % (p.get("down_kbps"), p.get("up_kbps"),
                                       p.get("latency_up_ms"), p.get("latency_down_ms")))
    rc2 = api("/api/upreset", {"name": "真机自动化越界",
                               "profile": {"loss_pct": 999, "disconnect_pct": -5}})
    api("/api/preset", {"key": "u:%s" % rc2.get("id")})
    api("/api/run", {"enable": True})
    p = api("/api/status").get("profile", {})
    D.rec("D-32 越界值夹值", 0 <= p.get("loss_pct", -1) <= 100
          and 0 <= p.get("disconnect_pct", -1) <= 100,
          "loss=%s disconnect=%s" % (p.get("loss_pct"), p.get("disconnect_pct")))
    api("/api/upreset/del", {"id": pid})
    api("/api/upreset/del", {"id": rc2.get("id")})
    left = api("/api/upresets")
    left = left if isinstance(left, list) else left.get("items", [])
    D.rec("D-33 删除自定义场景生效",
          all(str(i.get("id")) not in (str(pid), str(rc2.get("id"))) for i in left),
          "剩余 %d 条" % len(left))
    scene("direct", False)


def part_float():
    D.section("D6 悬浮窗")
    rc = api("/api/float/show", {"show": True})
    D.rec("D-34 悬浮窗可显示", rc.get("shown") is True, str(rc)[:100])
    D.shot("20261028_float")
    rc = api("/api/float/show", {"show": False})
    D.rec("D-35 悬浮窗可收起", rc.get("shown") is False, str(rc)[:100])


def part_stability(base):
    D.section("D7 稳定性与资源")
    pss0 = D.pss_kb()
    cpu0 = D.cpu_ticks()
    t0 = time.time()
    keys = ["direct", "4g", "5g", "wifi_weak", "congestion", "high_loss",
            "high_latency", "satellite", "blackhole", "dns_fail", "airplane"]
    fails = 0
    for i in range(200):
        try:
            api("/api/preset", {"key": keys[i % len(keys)]}, timeout=8)
        except Exception:
            fails += 1
    el = time.time() - t0
    D.rec("D-40 连续切场景 200 次", fails == 0, "失败 %d，均 %.0f ms/次" % (fails, el / 200 * 1000))

    fails = 0
    t0 = time.time()
    for i in range(100):
        try:
            api("/api/run", {"enable": bool(i % 2)}, timeout=8)
        except Exception:
            fails += 1
    D.rec("D-41 反复开关限速 100 次", fails == 0, "失败 %d，%.1fs" % (fails, time.time() - t0))

    scene("direct", False)
    time.sleep(1.0)
    cpu1 = D.cpu_ticks()
    dt = time.time() - t0
    pss1 = D.pss_kb()
    D.rec("D-42 压力后进程存活且可读状态", api("/api/status").get("limit_on") is False,
          "PSS %s → %s KB" % (pss0, pss1))
    D.rec("D-43 PSS 增长 < 40MB（无显著泄漏）",
          pss0 > 0 and pss1 > 0 and (pss1 - pss0) < 40 * 1024,
          "增量 %d KB（%s → %s）" % ((pss1 - pss0) if (pss0 > 0 and pss1 > 0) else -1,
                                   pss0, pss1))

    # logcat 检查
    out = D.sh("logcat -d -t 800 2>/dev/null | grep -E 'FATAL|ANR in com.weaknet|tombstone' | head -5")
    D.rec("D-44 logcat 无 FATAL / ANR / tombstone", not out.strip(), (out.strip()[:160] or "干净"))


def part_share_p0():
    D.section("D8 跨端共享（P0 复现）")
    import subprocess
    ADB = os.path.join(HERE, "..", "android", "sdk", "platform-tools", "adb.exe")
    ADB = os.path.abspath(ADB)
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    subprocess.run([ADB, "forward", "tcp:18890", "tcp:8900"], env=env,
                   capture_output=True, timeout=60)

    # 清掉 PC 侧同名测试环境
    ups = wapi("/api/upresets")
    ups = ups if isinstance(ups, list) else ups.get("items", [])
    for it in ups:
        if str(it.get("name", "")).startswith("P0探针"):
            wapi("/api/upreset/del", {"id": it.get("id")})

    # 对照组：ASCII 名
    r_ascii = wapi("/api/upreset", {"name": "P0probe_ascii",
                                    "profile": {"down_kbps": 888, "up_kbps": 111}})
    res_ascii = wapi("/api/push", {"peer": "127.0.0.1:18890",
                                   "ids": [r_ascii.get("id")], "combo_ids": []})
    D.rec("D-50 对照组：ASCII 名环境推送成功", res_ascii.get("ok") is True,
          json.dumps(res_ascii, ensure_ascii=False)[:140])

    # 实验组：中文名
    r_cn = wapi("/api/upreset", {"name": "P0探针中文环境",
                                 "profile": {"down_kbps": 999, "up_kbps": 222}})
    t0 = time.time()
    res_cn = wapi("/api/push", {"peer": "127.0.0.1:18890",
                                "ids": [r_cn.get("id")], "combo_ids": []})
    el = time.time() - t0
    D.rec("D-51 【P0】中文名环境推送应成功", res_cn.get("ok") is True,
          "%.1fs → %s" % (el, json.dumps(res_cn, ensure_ascii=False)[:160]))

    # 手机侧是否真的入库
    ups2 = api("/api/upresets")
    ups2 = ups2 if isinstance(ups2, list) else ups2.get("items", [])
    hit = [x for x in ups2 if x.get("name") == "P0探针中文环境"]
    D.rec("D-52 中文环境在手机侧可见", bool(hit),
          "手机侧共 %d 条自定义" % len(ups2))

    # 清理
    for it in ups2:
        if str(it.get("name", "")).startswith("P0探针"):
            api("/api/upreset/del", {"id": it.get("id")})
    for it in (wapi("/api/upresets") or []):
        pass
    ups = wapi("/api/upresets")
    ups = ups if isinstance(ups, list) else ups.get("items", [])
    for it in ups:
        if str(it.get("name", "")).startswith("P0probe"):
            wapi("/api/upreset/del", {"id": it.get("id")})


if __name__ == "__main__":
    D.setup_forward()
    try:
        base = part_basic()
        for fn in (part_engine, part_faults, part_combo, part_preset,
                   part_float, part_share_p0):
            try:
                fn()
            except Exception as e:
                D.rec("!! %s 异常" % fn.__name__, False, repr(e))
            finally:
                try:
                    api("/api/run", {"enable": False})
                except Exception:
                    pass
        part_stability(base)
    finally:
        try:
            scene("direct", False)
        except Exception:
            pass
    D.summary("Android 真机深度")
