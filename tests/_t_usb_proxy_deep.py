#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""USB 代理路径深度测试（PC Windows 引擎 8888，经 adb reverse 18888 暴露给手机）。

与 _t_usb_proxy_e2e.py 的区别：这里用与 tun E2E 相同的探针口径
（服务端固定头长 + 轮询到预期字节数），因此限速/延迟是**可量化**的，
而不是只看「能不能通」。

拓扑：
  手机 nc 127.0.0.1:18888 -> adb reverse -> PC 127.0.0.1:8888 (WeakNet.exe)
    -> PC 127.0.0.1:9126 (raw big，头长 37+len(N))
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import os
import subprocess
import sys
import time

ADB = r"C:/Program Files (x86)/Android/android-sdk/platform-tools/adb.exe"
PROBE = "/data/local/tmp/wn_tun.sh"
CTRL = "http://127.0.0.1:8899"
R = []


def sh(cmd, t=180):
    p = subprocess.run([ADB, "shell", cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", timeout=t)
    return (p.stdout or "").strip()


def api(path, data=None):
    import urllib.request
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(CTRL + path, data=body,
                                 method=("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": str(e)}


def rec(name, ok, detail=""):
    R.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""), flush=True)


def probe(nbytes=0):
    """返回 (总字节, 毫秒)。"""
    txt = sh("%s 127.0.0.1 18888 %d" % (PROBE, nbytes))
    for line in txt.splitlines():
        p = line.split()
        if len(p) >= 3 and p[0] == "BYTES":
            return int(p[1]), int(p[2])
    return None, None


def scene(key, enable=True):
    api("/api/preset", {"key": key})
    api("/api/run", {"enable": enable})


def profile(d):
    api("/api/profile", d)
    api("/api/run", {"enable": True})


def main():
    st = api("/api/status")
    rec("PC 引擎控制接口可达", "error" not in st,
        "listen=%s limit_on=%s" % (st.get("listen"), st.get("limit_on")))

    scene("direct")
    b0, t0 = probe(0)
    rec("USB 代理路径基线可达", b0 and b0 > 0, "%s B / %s ms" % (b0, t0))
    if not b0:
        print("基线不通，终止")
        return 1

    # ---- 下行延迟精度 ----
    profile({"name": "USB延迟1200", "latency_down_ms": 1200})
    time.sleep(0.5)
    b, t = probe(0)
    inj = max(0, (t or 0) - (t0 or 0))
    rec("USB 路径 下行延迟 1200ms", inj >= 1200 * 0.7,
        "往返 %s ms，注入 %d ms（设定 1200）" % (t, inj))

    # ---- 下行限速精度：512kbps ----
    profile({"name": "USB限速512", "down_kbps": 512})
    time.sleep(0.5)
    b, t = probe(512 * 1024)
    kbps = b * 8 / 1024 / (t / 1000.0) if (b and t) else 0
    rec("USB 路径 下行限速 512kbps", b >= 512 * 1024 and 0.5 * 512 <= kbps <= 1.5 * 512,
        "%.0f kbps（设定 512）%s B / %s ms" % (kbps, b, t))

    # ---- 下行限速精度：2000kbps（大样本避开桶突发）----
    profile({"name": "USB限速2000", "down_kbps": 2000})
    time.sleep(0.5)
    b, t = probe(2 * 1024 * 1024)
    kbps = b * 8 / 1024 / (t / 1000.0) if (b and t) else 0
    rec("USB 路径 下行限速 2000kbps", b >= 2 * 1024 * 1024 and 0.5 * 2000 <= kbps <= 1.5 * 2000,
        "%.0f kbps（设定 2000）%s B / %s ms" % (kbps, b, t))

    # ---- 上行限速精度：256kbps ----
    # 上行必须用 wn_up.sh：它把 stdin 撑住并轮询服务端响应，nc 自己退出会把
    # 内核缓冲里没发完的数据丢掉（裸 nc 实测 1MB/0.03s 的假象）。
    profile({"name": "USB上行256", "up_kbps": 256})
    time.sleep(0.5)
    up = sh("sh /data/local/tmp/wn_up.sh 127.0.0.1 18888 524288")
    parts = up.split()
    if len(parts) >= 3 and parts[0] == "UP":
        got, ms = int(parts[1]), int(parts[2])
        kbps = 524288 * 8 / 1024 / (ms / 1000.0) if ms else 0
        rec("USB 路径 上行限速 256kbps", got > 0 and 0.5 * 256 <= kbps <= 1.6 * 256,
            "%.0f kbps（设定 256）512KB / %d ms" % (kbps, ms))
    else:
        rec("USB 路径 上行限速 256kbps", False, "探针输出异常：%r" % up[:80])

    # ---- 完全断网档 ----
    scene("airplane")
    time.sleep(0.5)
    b, t = probe(0)
    rec("USB 路径 完全断网档拒连", not b or b < 200, "bytes=%s" % b)

    # ---- 恢复直连 ----
    scene("direct")
    time.sleep(0.5)
    b, t = probe(1024 * 1024)
    rec("USB 路径 恢复直连", b >= 1024 * 1024 and t < 3000, "1MB 耗时 %s ms" % t)

    api("/api/run", {"enable": False})
    bad = [x for x in R if not x[1]]
    print("\n===== USB 代理路径深度：%d/%d 通过 =====" % (len(R) - len(bad), len(R)))
    for n, _o, d in bad:
        print("FAILED: %s | %s" % (n, d))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
