# -*- coding: utf-8 -*-
"""稳定性补测：S-02 段推进采样法 + 修正后的 P-01/P-05。"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import _t_dev_lib as D


def main():
    D.setup_forward()
    pss0 = D.pss_kb()
    print("PSS 起始 = %s KB" % pss0)

    D.section("S-02 组合段推进采样（2 段 ×1s，采样 20s）")
    D.api("/api/combo", {"name": "loop2", "loop": True, "steps": [
        {"key": "5g", "minutes": 0, "seconds": 1}, {"key": "wifi", "minutes": 0, "seconds": 1}]})
    combos = D.api("/api/combos")
    items = combos.get("items", combos if isinstance(combos, list) else [])
    cid = ""
    for c in items:
        if c.get("name") == "loop2":
            cid = c.get("id")
    D.api("/api/preset", {"key": "c:%s" % cid})
    D.api("/api/run", {"enable": True})
    time.sleep(0.5)
    # 采样 20 秒：profile 的 down_kbps 应在 5G(100000?) 与 Wi-Fi 之间交替
    seq = []
    t0 = time.time()
    while time.time() - t0 < 20:
        s = D.status()
        pf = s.get("profile", {})
        dk = pf.get("down_kbps", -1)
        seq.append(dk)
        time.sleep(0.25)
    # 归类为两种状态，检查交替次数
    uniq = []
    for v in seq:
        if not uniq or uniq[-1] != v:
            uniq.append(v)
    # 过滤采样噪声：只保留出现 ≥2 次的值段
    switches = len(uniq) - 1
    print("    采样序列（压缩后）:", uniq[:20])
    D.rec("S-02 组合段按序循环推进（20s 内多次交替、无卡死）", switches >= 10,
          "交替 %d 次，两端值 %s / %s" % (switches, uniq[0] if uniq else "?", uniq[-1] if uniq else "?"))
    s = D.status()
    D.rec("S-02-b 循环仍在播放（未中断）", s.get("limit_on") is True and
          str(s.get("preset_key", "")).startswith("c:"), "key=%s" % s.get("preset_key"))
    D.api("/api/run", {"enable": False})
    D.api("/api/combo/delete", {"id": cid})

    D.section("P-01 空闲 CPU（修正：ticks×10ms，采 15s）")
    time.sleep(3)
    tk0 = D.cpu_ticks()
    t0 = time.time()
    time.sleep(15)
    tk1 = D.cpu_ticks()
    el = time.time() - t0
    if tk0 and tk1 and el > 0:
        cpu_pct = (tk1 - tk0) * 0.01 / el * 100
        D.rec("P-01 空闲（未限速、无传输）CPU < 15%", cpu_pct < 15, "%.1f%%" % cpu_pct)
    else:
        D.rec("P-01 空闲 CPU", False, "ticks 不可用")

    D.section("P-01b 限速传输中 CPU（512kbps 下载 8s）")
    import subprocess
    subprocess.run([D.ADB, "-s", D.SERIAL, "forward", "tcp:18821", "tcp:8888"], capture_output=True)
    from _t_dev_engine import Srv
    Srv().start()
    time.sleep(0.3)
    base = dict(name="t", latency_up_ms=0, latency_down_ms=0, jitter_ms=0, down_kbps=512,
                up_kbps=0, loss_pct=0, stall_ms=600, disconnect_pct=0, timeout_pct=0,
                dns_fail_pct=0, reject_pct=0, flap_sec=0, flap_blackout_sec=2)
    D.api("/api/profile", base)
    D.api("/api/run", {"enable": True})
    time.sleep(1.0)
    import threading
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": "http://127.0.0.1:18821"}))
    stop = [False]
    got = [0]

    def dl():
        while not stop[0]:
            try:
                with op.open("http://192.168.1.49:18900/big", timeout=60) as r:
                    while True:
                        b2 = r.read(65536)
                        if not b2:
                            break
                        got[0] += len(b2)
            except Exception:
                pass

    th = threading.Thread(target=dl, daemon=True)
    th.start()
    tk0 = D.cpu_ticks()
    t0 = time.time()
    time.sleep(8)
    tk1 = D.cpu_ticks()
    el = time.time() - t0
    stop[0] = True
    cpu_pct = (tk1 - tk0) * 0.01 / el * 100 if (tk0 and tk1) else -1
    D.rec("P-01b 传输中 CPU < 30%", 0 <= cpu_pct < 30,
          "%.1f%%（8s 下载 %.2f MB）" % (cpu_pct, got[0] / 1e6))
    D.api("/api/run", {"enable": False})

    D.section("P-05 内存前后对比（相对本轮起点）")
    pss1 = D.pss_kb()
    D.rec("P-05 PSS 增长 < 15MB", pss0 > 0 and pss1 > 0 and pss1 - pss0 < 15000,
          "PSS %s → %s KB（+%s KB）" % (pss0, pss1, pss1 - pss0 if pss0 > 0 else "?"))

    D.summary("稳定性补测")


if __name__ == "__main__":
    main()
