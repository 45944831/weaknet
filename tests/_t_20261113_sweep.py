# -*- coding: utf-8 -*-
"""限速精度 vs 样本长度：确认 tun 路 4000 kbps 档「1 MB 测出 1.45x」是启动突发
还是真超发。

做法：同一档位下把样本从 1 MB 拉到 8 MB，看比值是否随样本变长收敛到 1.0。
如果收敛，说明短样本被令牌桶的突发额度（0.25 s × 速率）与 TCP 慢启动放大；
如果不收敛，才是限速器本身偏快。
"""
import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t_apk_tun_real as T

RATES = [4000]
SIZES = [1, 2, 4, 8]


def main():
    T.api("/api/preset", {"key": "direct"})
    T.api("/api/run", {"enable": False})
    time.sleep(0.5)
    T.api("/api/vpn", {"enable": True})
    for _ in range(20):
        if T.api("/api/status").get("vpn_active"):
            break
        time.sleep(0.4)

    out = {}
    tag = 10
    for rate in RATES:
        T.api("/api/profile", {"name": "sweep%d" % rate,
                               "down_kbps": rate, "up_kbps": 0,
                               "latency_up_ms": 0, "latency_down_ms": 0,
                               "loss_pct": 0, "jitter_ms": 0})
        T.api("/api/run", {"enable": True})
        time.sleep(0.8)
        rows = []
        for mb in SIZES:
            n = mb * 1024 * 1024
            sz, done_ms, first_ms, _w = T.probe(n, tag=tag, t=300)
            tag += 1
            body, sha, _ = T.pull_body(tag - 1, n)
            got = len(body) if body else 0
            kbps = (got * 8 / 1024.0) / (done_ms / 1000.0) if got and done_ms else 0
            rows.append({"mb": mb, "bytes": got, "done_ms": done_ms,
                         "kbps": round(kbps, 1),
                         "ratio": round(kbps / rate, 3) if kbps else 0})
            print("  %d MB -> %.0f kbps (%.2fx, %d ms)" % (mb, kbps, kbps / rate if kbps else 0, done_ms),
                  flush=True)
            time.sleep(0.6)
        out[str(rate)] = rows

    T.api("/api/run", {"enable": False})
    T.api("/api/vpn", {"enable": False})
    p = os.path.join(HERE, "..", "_tmp", "throttle_sweep_20261113.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n写入 " + os.path.abspath(p), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
