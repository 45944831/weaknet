# -*- coding: utf-8 -*-
"""2026-10-28 全面深度测试 —— Windows 引擎 A 组：API 契约 + 吞吐 / 延迟 / 丢包 / 并发。

前提：weaknet.py 已起在 8888/8899，tests/_w_srv.py 已监听 127.0.0.1:9125。
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _w_lib as L

CTRL = "http://127.0.0.1:8899"
SRV = ("127.0.0.1", 9125)
RESULTS = []
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path, data=None, method=None, raw=None, timeout=15):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(CTRL + path, data=body,
                                 method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    try:
        with _op.open(req, timeout=timeout) as r:
            txt = r.read()
            try:
                return r.status, json.loads(txt or b"{}")
            except Exception:
                return r.status, {"_raw": txt[:120].decode("utf-8", "ignore")}
    except urllib.error.HTTPError as e:
        return e.code, {}


def rec(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""), flush=True)


def scene(key, enable=True):
    api("/api/preset", {"key": key})
    api("/api/run", {"enable": enable})


def tun(timeout=60):
    return L.Tunnel(host=SRV[0], port=SRV[1], timeout=timeout)


def down(nbytes, timeout=120):
    t = tun(timeout)
    try:
        got, sec, _bps = t.down(nbytes)
        return got, sec
    finally:
        t.close()


def up(nbytes, timeout=120):
    t = tun(timeout)
    try:
        got, sec, _bps = t.up(nbytes)
        return got, sec
    finally:
        t.close()


def pings_same_conn(n=5, timeout=40):
    """一条长连接上连发 n 个 PING，返回各自 RTT（验证延迟是否每包都注入）"""
    t = tun(timeout)
    out = []
    try:
        for i in range(n):
            rtt, _ = t.ping(i)
            out.append(rtt)
    finally:
        t.close()
    return out


def mbps(nbytes, sec):
    return nbytes * 8 / sec / 1e6


# ============================================================ A1 API 契约
def part_api():
    st, d = api("/api/status")
    need = ["profile", "preset_key", "limit_on", "gate_open", "listen", "lan_ip",
            "sysproxy", "stats", "events"]
    rec("A-01 /api/status 结构完整", st == 200 and all(k in d for k in need),
        "missing=" + ",".join(k for k in need if k not in d))
    rec("A-02 启动后默认未限速且为 direct",
        d.get("limit_on") is False and d.get("preset_key") == "direct",
        "limit_on=%s preset=%s" % (d.get("limit_on"), d.get("preset_key")))
    rec("A-03 统计字段齐全", all(k in d.get("stats", {}) for k in
                             ("total", "active", "up", "down", "bypass", "uptime")),
        str(d.get("stats")))

    st, pl = api("/api/presets")
    rec("A-04 内置预设 17 支", st == 200 and isinstance(pl, list) and len(pl) == 17,
        "n=%s" % (len(pl) if isinstance(pl, list) else "?"))

    with _op.open(CTRL + "/", timeout=5) as r:
        txt = r.read().decode("utf-8", "ignore")
    rec("A-05 根路径返回说明（控制台已下线）", "控制台" in txt, txt.strip().splitlines()[0][:40])

    st, _ = api("/api/preset", {"key": "not_exist"})
    rec("A-06 非法 preset key → 400", st == 400, "status=%d" % st)

    st, _ = api("/api/profile", {"down_kbps": "abc"})
    rec("A-07 profile 非数字被忽略不崩", st == 200, "status=%d" % st)

    st, _ = api("/api/run", {"enable": "x"})
    rec("A-08 run 非法 body → 400", st == 400, "status=%d" % st)

    # v1.1.5 修复项：bool("false") == True 的历史缺陷
    st, _ = api("/api/run", {"enable": "false"})
    st2, d2 = api("/api/status")
    rec("A-09 run \"false\" 字符串不误开限速", st == 400 and d2.get("limit_on") is False,
        "status=%d limit_on=%s" % (st, d2.get("limit_on")))

    st, _ = api("/api/upreset", {"name": "x"})
    rec("A-10 upreset 缺 profile → 400", st == 400, "status=%d" % st)

    st, _ = api("/api/upreset/del", {})
    rec("A-11 upreset/del 缺 id → 400", st == 400, "status=%d" % st)

    st, _ = api("/api/scope", {"mode": "bogus", "list": []})
    rec("A-12 scope 非法 mode 不崩", st in (200, 400), "status=%d" % st)

    st, _ = api("/api/listen", {"host": "1.2.3.4"})
    rec("A-13 listen 非法 host → 400", st == 400, "status=%d" % st)

    st, _ = api("/api/nonexistent")
    rec("A-14 未知路径 404", st == 404, "status=%d" % st)

    # 已知偏差（v1.1.7 记录）：控制接口不校验 HTTP 方法
    st, _ = api("/api/status", method="DELETE")
    rec("A-15 控制接口校验 HTTP 方法（已知偏差）", st == 405,
        "DELETE /api/status → %d（v1.1.7 起已知偏差，期望 405）" % st)


# ============================================================ A2 吞吐
def part_throughput():
    scene("direct", False)
    n = 4 * 1024 * 1024
    got, sec = down(n)
    base = mbps(got, sec)
    rec("A-20 直连基线 4MB 完整收满", got == n, "%.1f Mbps (%.3fs)" % (base, sec))

    # 取样量必须 ≥4× 桶容量（BURST_SEC=0.25）且 ≥2× BDP，否则测的是突发额度不是稳态速率。
    # 带 disconnect_pct 的档位（5g_weak / 4g_weak / 电梯…）不适合测带宽精度：随机断连会
    # 直接掐掉传输，结果取决于运气；它们的丢包/断连行为由 B 组与真机用例单独覆盖。
    cases = [
        ("5g", 100.0, 8 * 1024 * 1024, (0.80, 1.25)),
        ("4g", 20.0, 2 * 1024 * 1024, (0.80, 1.25)),
        ("wifi_weak", 5.0, 4 * 1024 * 1024, (0.75, 1.30)),
        ("congestion", 0.512, 256 * 1024, (0.75, 1.40)),
    ]
    for key, limit, nbytes, (lo, hi) in cases:
        scene(key, True)
        # 预热：令牌桶空闲时会攒满 BURST_SEC(0.25s) 的配额，直接测会把「突发额度」
        # 算进速率（100 Mbps 档可攒 3.1MB，8MB 样本仍能测出 1.4x）。先传掉一份
        # 约 0.35s 的配额把桶排空，再测稳态。
        warm = max(64 * 1024, min(6 * 1024 * 1024, int(limit * 1e6 / 8 * 0.35)))
        try:
            down(warm, timeout=180)
        except Exception:
            pass
        got, sec = down(nbytes, timeout=180)
        m = mbps(got, sec)
        ratio = m / limit
        rec("A-21 下行限速 %s（设定 %.3g Mbps）" % (key, limit),
            got == nbytes and lo <= ratio <= hi,
            "实测 %.3f Mbps = %.2fx（允许 %.2f~%.2f，预热 %dKB）" % (
                m, ratio, lo, hi, warm // 1024))

    # 上行
    scene("congestion", True)   # up 256 kbps
    got, sec = up(128 * 1024, timeout=120)
    m = mbps(got, sec)
    rec("A-22 上行限速 congestion（设定 0.256 Mbps）",
        0.75 <= m / 0.256 <= 1.50, "实测 %.3f Mbps = %.2fx" % (m, m / 0.256))

    # 并发共享：8 条连接总吞吐应贴近单份配额，而不是 8 倍
    scene("congestion", True)
    out = []

    def worker():
        try:
            g, s = down(32 * 1024, timeout=180)
            out.append((g, s))
        except Exception as e:
            out.append((0, 1.0, str(e)))

    ts = [threading.Thread(target=worker) for _ in range(8)]
    t0 = time.time()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    total = sum(o[0] for o in out)
    el = time.time() - t0
    agg = mbps(total, el)
    rec("A-23 8 并发共享同一份带宽（设定 0.512 Mbps）",
        0.65 <= agg / 0.512 <= 1.60 and len([o for o in out if o[0] > 0]) == 8,
        "聚合 %.3f Mbps = %.2fx，成功 %d/8" % (agg, agg / 0.512,
                                          len([o for o in out if o[0] > 0])))
    scene("direct", False)


# ============================================================ A3 延迟
def part_latency():
    scene("direct", False)
    base = pings_same_conn(3)
    b = min(base)
    rec("A-30 直连 RTT 基线 < 50ms", b < 50, "%.1f ms（3 次 min）" % b)

    cases = [
        ("4g", 100.0),        # 40+60
        ("high_latency", 800.0),
        ("satellite", 1400.0),
    ]
    for key, expect in cases:
        scene(key, True)
        rs = pings_same_conn(5, timeout=90)
        lo, hi = expect * 0.75, expect * 1.9
        # 关键：第 2 次起不能是 0（历史 v1.1.3 缺陷：延迟只在建连注入一次）
        later_ok = all(r > expect * 0.6 for r in rs[1:])
        rec("A-31 延迟逐包注入 %s（理论 +%.0fms）" % (key, expect),
            all(lo <= r <= hi for r in rs) and later_ok,
            "RTT=%s（第 2 次起仍需 >%.0f）" % ([round(x) for x in rs], expect * 0.6))

    # 抖动：同一档位多次 RTT 应有离散
    scene("highspeed_rail", True)
    rs = pings_same_conn(8, timeout=60)
    spread = max(rs) - min(rs)
    rec("A-32 抖动可观测 highspeed_rail（jitter 150ms）", spread > 40,
        "RTT 极差 %.0f ms：%s" % (spread, [round(x) for x in rs]))
    scene("direct", False)


# ============================================================ A4 丢包
def part_loss():
    scene("direct", False)
    g0, s0 = down(1024 * 1024, timeout=120)
    t0 = mbps(g0, s0)

    scene("high_loss", True)   # 30% 丢包 + 800ms 卡顿，带宽 8Mbps
    t_start = time.time()
    g1, s1 = down(1024 * 1024, timeout=240)
    t1 = mbps(g1, s1)
    rec("A-40 丢包 30% 显著压低吞吐", g1 == 1024 * 1024 and t1 < t0 * 0.55,
        "直连 %.1f → 高丢包 %.2f Mbps（%.0f%%），耗时 %.1fs" % (t0, t1, t1 / t0 * 100, s1))

    # 丢包为 0 时「卡顿时长」不应生效（参数说明承诺）
    scene("direct", False)
    api("/api/profile", {"stall_ms": 5000, "loss_pct": 0})
    g2, s2 = down(512 * 1024, timeout=120)
    rec("A-41 丢包率 0 时卡顿时长不生效", mbps(g2, s2) > t0 * 0.5,
        "%.1f Mbps（若生效会明显变慢）" % mbps(g2, s2))
    scene("direct", False)


# ============================================================ A5 统计
def part_stats():
    scene("4g", True)
    st, d0 = api("/api/status")
    down(512 * 1024, timeout=120)
    time.sleep(0.4)
    st, d1 = api("/api/status")
    dd = d1["stats"]["down"] - d0["stats"]["down"]
    rec("A-50 统计 down 字节随传输增长", dd >= 512 * 1024,
        "增量 %d B（传输 524288）" % dd)
    rec("A-51 stats.total 计数增长", d1["stats"]["total"] > d0["stats"]["total"],
        "%s → %s" % (d0["stats"]["total"], d1["stats"]["total"]))
    scene("direct", False)


if __name__ == "__main__":
    print("=== A 组开始 ===", flush=True)
    for fn in (part_api, part_throughput, part_latency, part_loss, part_stats):
        try:
            fn()
        except Exception as e:
            rec("!! %s 异常" % fn.__name__, False, repr(e))
        finally:
            try:
                api("/api/run", {"enable": False})
            except Exception:
                pass
    ok = sum(1 for r in RESULTS if r["ok"])
    print("\n=== A 组汇总 %d/%d ===" % (ok, len(RESULTS)), flush=True)
    out = os.path.join(HERE, "_t_20261028_win_a.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=1)
    print("saved", out)
