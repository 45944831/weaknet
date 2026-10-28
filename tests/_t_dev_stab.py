# -*- coding: utf-8 -*-
"""真机批次 6：稳定性与资源（L6）。

S-01 连续切场景 200 次 / S-03 反复开关限速 100 次 / S-02 组合循环（缩短版 60 轮）
P-05 内存前后对比（PSS）/ P-01 空闲与负载 CPU / 全程 logcat 崩溃扫描。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import re
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import _t_dev_lib as D

SCENES = ["wifi", "4g", "4g_weak", "5g", "5g_weak", "wifi_weak", "congestion",
          "satellite", "high_loss", "high_latency", "highspeed_rail", "elevator"]


def main():
    D.setup_forward()
    D.app_restart()
    D.api("/api/vpn", {"enable": True})
    time.sleep(2.5)

    D.clear_logcat()
    t_start = time.time()
    pid0 = D._pid()
    pss0 = D.pss_kb()
    ticks0 = D.cpu_ticks()
    print("起始 PSS=%s KB pid=%s" % (pss0, pid0))

    D.section("S-01 连续切场景 200 次（VPN 开启）")
    D.api("/api/run", {"enable": True})
    time.sleep(1.0)
    t0 = time.time()
    errs = 0
    for i in range(200):
        try:
            D.api("/api/preset", {"key": SCENES[i % len(SCENES)]}, timeout=8)
        except Exception:
            errs += 1
    dt = time.time() - t0
    s = D.status()
    alive = isinstance(s.get("preset_key"), str)
    st = s.get("stats", {}) or {}
    active = st.get("active", st.get("total", "?")) if isinstance(st, dict) else "?"
    D.rec("S-01 200 次切换无失败、引擎存活", errs == 0 and alive,
          "耗时 %.1fs（均 %.0fms）失败=%d active=%s" % (dt, dt * 1000 / 200, errs, active))

    D.section("S-03 反复开关限速 100 次")
    errs2 = 0
    final_on = None
    t0 = time.time()
    for i in range(100):
        want = (i % 2 == 0)
        try:
            D.api("/api/run", {"enable": want}, timeout=8)
        except Exception:
            errs2 += 1
    time.sleep(1.0)
    s = D.status()
    D.rec("S-03 100 次开关无失败、状态与最后一次一致",
          errs2 == 0 and s.get("limit_on") == False, "失败=%d limit_on=%s" % (errs2, s.get("limit_on")))

    D.section("S-02 缩短版：组合循环 60 轮（2 段 ×1s）")
    r = D.api("/api/combo", {"name": "loop60", "loop": True, "steps": [
        {"key": "5g", "minutes": 0, "seconds": 1}, {"key": "wifi", "minutes": 0, "seconds": 1}]})
    combos = D.api("/api/combos")
    items = combos.get("items", combos if isinstance(combos, list) else [])
    cid = ""
    for c in items:
        if c.get("name") == "loop60":
            cid = c.get("id")
    D.api("/api/preset", {"key": "c:%s" % cid})
    D.api("/api/run", {"enable": True})
    time.sleep(125)   # 60 轮 = 120s，留 5s 余量
    s = D.status()
    ev = [e.get("msg", "") for e in s.get("events", [])[:30]]
    playing = s.get("limit_on") is True and s.get("preset_key", "").startswith("c:")
    combo_ev = [m for m in ev if "loop60" in m or "5G" in m or "Wi-Fi" in m]
    D.rec("S-02 组合循环 2 分钟仍在推进、未跳段未中断", playing and len(combo_ev) >= 2,
          "limit_on=%s key=%s 近期事件=%d" % (s.get("limit_on"), s.get("preset_key", "")[:8], len(combo_ev)))
    D.api("/api/run", {"enable": False})
    D.api("/api/combo/delete", {"id": cid})

    D.section("P-05/P-01 资源：内存、CPU、崩溃扫描")
    pss1 = D.pss_kb()
    ticks1 = D.cpu_ticks()
    up = D.status().get("stats", {}).get("uptime", 0)
    D.rec("P-05 压力后 PSS 增长 < 15MB", pss0 > 0 and pss1 > 0 and pss1 - pss0 < 15000,
          "PSS %s → %s KB（+%s KB）" % (pss0, pss1, (pss1 - pss0) if pss0 > 0 else "?"))
    # 空闲 CPU：10 秒采样
    t0 = time.time()
    tk0 = D.cpu_ticks()
    time.sleep(10)
    tk1 = D.cpu_ticks()
    el = time.time() - t0
    if tk0 and tk1 and el > 0:
        cpu_pct = (tk1 - tk0) / el / 10.0 * 100 / 1.0   # ticks≈10ms
        D.rec("P-01 空闲（未限速）CPU < 15%", cpu_pct < 15, "%.1f%%" % cpu_pct)
    else:
        D.rec("P-01 空闲 CPU 采样", False, "ticks 不可用")
    crash = D.sh("logcat -d -v time | grep -iE 'FATAL|AndroidRuntime.*com.weaknet|SIGSEGV|SIGABRT|tombstone' | tail -8")
    D.rec("全程无崩溃 / 无 native 信号", len(crash.strip()) == 0,
          crash.replace("\n", " | ")[:200] if crash.strip() else "干净")
    anr = D.sh("logcat -d | grep -iE 'ANR in com.weaknet' | tail -3")
    D.rec("无 ANR", len(anr.strip()) == 0, anr[:150] if anr.strip() else "干净")

    print("\n总耗时 %.1f 分钟" % ((time.time() - t_start) / 60))
    D.summary("批次6：稳定性与资源")


if __name__ == "__main__":
    main()
