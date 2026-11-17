# -*- coding: utf-8 -*-
"""时延档位专项复核（换真机后 44/47 中 3 项失败的定性）。

背景
----
2026-11-17 SM-G9730 全量复测：限速/MTU/前提/完整性/资源全 PASS，
唯独 delay 的 50+50 / 150+150 / 100+20 三项失败。原始样本显示：
  * 基线 770~800ms，极稳定（波动 30ms）；
  * 150+150→+380ms、300+300→+920ms 单调生效（引擎注入正常）；
  * 50+50=[970,960,800,780,800] 出现两簇，100+20 甚至有 600ms 离群值。
假设：公网 TUNA 链路 + 2KB 极小下载 + 5 样本中位数，对 100ms 级增量
信噪比不足；并非引擎缺陷。本脚本用「每档 12 样本 + 逐次回读 profile」
同时验证两点：
  1) profile 在测量过程中是否被重置（解释两簇现象）；
  2) 加大样本后，各档增量是否随注入量线性单调、接近理论 2×(up+down)。
只读不改产品代码。
"""
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t_20261116_param as P  # noqa: E402

OUT = {}


def trimmed(vals, k=2):
    """去掉首尾各 k 个极端值后的均值（抗离群，但保留 majority 信号）。"""
    s = sorted(vals)
    cut = s[k:len(s) - k] if len(s) > 2 * k else s
    return sum(cut) / len(cut) if cut else None


def run():
    subprocess.run([P.T.ADB, "shell", "am start -n %s/.MainActivity" % P.PKG],
                   capture_output=True, timeout=20)
    subprocess.run([P.T.ADB, "forward", "tcp:18899", "tcp:8899"],
                   capture_output=True, timeout=20)
    time.sleep(2.5)
    if not P.push_probe():
        raise SystemExit("探针推送失败")
    P.ensure_vpn(True)
    ok, ms = P.wait_ready()
    print("vpn_ready=%s (%d ms)" % (ok, ms), flush=True)
    if not ok:
        raise SystemExit("VPN 未就绪")

    cases = [(0, 0), (30, 30), (50, 50), (100, 100),
             (100, 20), (150, 150), (300, 300)]
    res = {}
    for up, down in cases:
        st = P.set_profile(latency_up_ms=up, latency_down_ms=down)
        p = st.get("profile") or {}
        print("\n== up=%d down=%d | profile lat=%s/%s jit=%s limit_on=%s =="
              % (up, down, p.get("latency_up_ms"), p.get("latency_down_ms"),
                 p.get("jitter_ms"), st.get("limit_on")), flush=True)
        vals, profiles = [], []
        for i in range(12):
            m = P.measure(2048, tag="rv%d_%d_%d" % (up, down, i))
            if m["first_byte_ms"] is not None:
                vals.append(m["first_byte_ms"])
            # 每 3 次回读一次 profile，看测量期间是否被重置
            if i % 3 == 0:
                s2 = P.api("/api/status")
                p2 = s2.get("profile") or {}
                profiles.append((p2.get("latency_up_ms"), p2.get("latency_down_ms")))
            time.sleep(0.25)
        med = statistics.median(vals) if vals else None
        tm = trimmed(vals)
        res["%d_%d" % (up, down)] = {"vals": vals, "median": med,
                                     "trimmed": tm, "profiles": profiles}
        print("  n=%d vals=%s" % (len(vals), vals), flush=True)
        print("  median=%s trimmed=%s profiles_during=%s"
              % (med, round(tm, 1) if tm else None, profiles), flush=True)

    base_vals = res["0_0"]["vals"]
    base = min(base_vals)
    base_med = statistics.median(base_vals)
    print("\n\n===== 结论表（基线 min=%s / median=%s）====" % (base, base_med), flush=True)
    for k, v in res.items():
        if k == "0_0":
            continue
        parts = [k]
        for label, val in (("minbase", base), ("medbase", base_med)):
            for stat in ("median", "trimmed"):
                cur = v[stat]
                if cur is None:
                    continue
                delta = cur - val
                got = delta / 2.0
                up, down = (int(x) for x in k.split("_"))
                want = up + down
                err = abs(got - want)
                parts.append("%s/%s: Δ%.0f→%.0fms(want%d,err%.0f,%s)"
                             % (label, stat, delta, got, want, err,
                                "OK" if err <= max(40, want * .25) else "NG"))
        print("  " + " | ".join(parts), flush=True)

    P.api("/api/vpn", {"enable": False})
    P.api("/api/preset", {"key": "direct"})
    OUT["delay"] = res
    OUT["base"] = {"min": base, "median": base_med}
    fn = "delay_reverify_%s.json" % time.strftime("%H%M%S")
    P.dump(fn, OUT)
    print("写入 " + fn, flush=True)


if __name__ == "__main__":
    run()
