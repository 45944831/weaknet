# -*- coding: utf-8 -*-
"""时延档位定性验证（curl time_starttransfer 精确计时）。

为什么要这个脚本
----------------
HTTP 首字节探针（wn_param.sh）的 first_byte 含 shell 启动 + nc 建连开销，
在新真机上基线高达 770ms（直连 ping 仅 114ms），100ms 级注入被淹没，
导致 30/50/150 档在 5 样本中位数下误判。本脚本改用设备自带 curl 的
%{time_starttransfer}——它是 curl 内部从发起请求到收到首字节的精确秒数，
不含脚本启动开销，能干净地反映 TUN 逐包注入的延迟。

判定：新建 TCP 连接首字节 ≈ 2×(up+down)（握手+请求各一次往返）。
只读不改产品代码。
"""
import math
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t_20261116_param as P  # noqa: E402

# 用 https + 域名（设备 curl 7.73 实测可用），time_starttransfer 为内部精确计时。
# 首字节含 TLS 握手，基线偏高，但比较的是相对增量，不影响定性。
CURL = ("curl -s -o /dev/null -w '%{time_starttransfer}' "
        "--max-time 20 --range 0-2047 "
        "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-releases/22.04/ubuntu-22.04.5-desktop-amd64.iso")


def run():
    subprocess.run([P.T.ADB, "forward", "tcp:18899", "tcp:8899"],
                   capture_output=True, timeout=20)
    P.api("/api/vpn", {"enable": True})
    ok, ms = P.wait_ready()
    print("vpn_ready=%s %dms" % (ok, ms), flush=True)
    if not ok:
        raise SystemExit("VPN 未就绪")

    cases = [(0, 0), (50, 50), (100, 100), (150, 150), (300, 300), (100, 20)]
    res = {}
    for up, down in cases:
        P.set_profile(latency_up_ms=up, latency_down_ms=down)
        time.sleep(0.5)
        vals = []
        for i in range(8):
            r = P.T.sh(CURL, t=30) or ""
            m = re.search(r"([\d.]+)", r)
            if m:
                vals.append(float(m.group(1)) * 1000)
            time.sleep(0.15)
        med = statistics.median(vals) if vals else None
        res["%d_%d" % (up, down)] = {"vals": [round(x) for x in vals],
                                     "median": med}
        print("up=%3d down=%3d n=%d med=%sms vals=%s"
              % (up, down, len(vals), round(med) if med else None,
                 [round(x) for x in vals]), flush=True)

    base = res["0_0"]["median"]
    if base is None:
        raise SystemExit("基线无有效样本，无法定性")
    print("\n===== 结论（基线 %.0fms）====" % base, flush=True)
    print("口径：curl 首字节含 TCP/TLS 握手，逐包注入使增量 ≈ k×(up+down)，"
          "k=建连往返跳数（实测约 3）。检验线性度而非硬编码 k。", flush=True)

    xs, ys, rows = [], [], []
    for k3, v in res.items():
        if k3 == "0_0" or v["median"] is None:
            continue
        up, down = (int(x) for x in k3.split("_"))
        x = up + down
        y = v["median"] - base
        xs.append(x)
        ys.append(y)
        rows.append((k3, x, y))
        print("  %s Δ%.0fms / (up+down=%d) = %.2f×"
              % (k3, y, x, y / x if x else 0), flush=True)

    # 过原点最小二乘：k = Σxy / Σx²，R² 检验线性度
    k = sum(a * b for a, b in zip(xs, ys)) / sum(a * a for a in xs)
    ybar = sum(ys) / len(ys)
    ss_res = sum((b - k * a) ** 2 for a, b in zip(xs, ys))
    ss_tot = sum((b - ybar) ** 2 for b in ys)
    r2 = 1 - ss_res / ss_tot if ss_tot else 1.0
    print("\n  线性拟合 Δ = k×(up+down): k=%.2f, R²=%.4f" % (k, r2), flush=True)
    verdict = (2.0 <= k <= 5.0 and r2 >= 0.95)
    print("  判定: k∈[2,5] 且 R²≥0.95 → %s" % ("注入准确(线性)" if verdict else "异常"),
          flush=True)

    P.api("/api/vpn", {"enable": False})
    P.api("/api/preset", {"key": "direct"})
    import json
    fn = "delay_curl_%s.json" % time.strftime("%H%M%S")
    P.dump(fn, {"res": res, "base": base, "fit_k": k, "fit_r2": r2,
                "verdict": "linear_ok" if verdict else "anomaly"})
    print("写入 " + fn, flush=True)


if __name__ == "__main__":
    run()
