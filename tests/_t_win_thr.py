# -*- coding: utf-8 -*-
"""Windows 引擎高速档 / 高延迟档吞吐复测。

背景：_t_win_full.py 的 17 档矩阵为控制总时长，样本量按档位递减（64KB~1MB）。
这对高速档和高延迟档不够，会测出两种假象：
  1) 高速档样本 < 令牌桶容量（BURST_SEC=0.25 × 速率）：桶空闲时已攒满，
     1MB 能整个塞进 1.6MB(50Mbps) / 3.2MB(100Mbps) 的桶里瞬间放行，
     实测速率反而远超设定值（158661 vs 50000、208521 vs 100000）。
  2) 高延迟档受 BDP 限制：可跑满的样本量 ≈ 速率/8 × RTT。
     10000kbps × 800ms ≈ 1MB，用 512KB 只跑到 4310kbps。
本脚本用「≥4× 桶容量且 ≥2× BDP」的样本重测，判定稳态速率是否真的落在设定区间。
"""
import json
import socket
import time
import urllib.request

CTRL = "http://127.0.0.1:8899"
PROXY = ("127.0.0.1", 8888)
SRV = ("192.168.1.49", 9125)
R = []


def api(path, data=None):
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


def tunnel(timeout=15):
    s = socket.create_connection(PROXY, timeout=timeout)
    s.sendall(("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n\r\n"
               % (SRV[0], SRV[1], SRV[0], SRV[1])).encode())
    b = b""
    while b"\r\n\r\n" not in b:
        d = s.recv(1)
        if not d:
            raise RuntimeError("CONNECT 被拒")
        b += d
    return s


def download(nbytes, timeout=180):
    s = tunnel(timeout)
    t0 = time.time()
    got = 0
    try:
        s.sendall(("DOWN %d\n" % nbytes).encode())
        while got < nbytes:
            d = s.recv(min(262144, nbytes - got))
            if not d:
                break
            got += len(d)
    finally:
        s.close()
    return got, max(time.time() - t0, 1e-6)


def scene(key):
    api("/api/preset", {"key": key})
    api("/api/run", {"enable": True})
    time.sleep(0.6)


# key: (样本字节, 判定区间下界倍率, 上界倍率, 说明)
PLAN = {
    "wifi":        (8 * 1024 * 1024, 0.55, 1.30, "桶 1.6MB，样本 8MB"),
    "5g":          (16 * 1024 * 1024, 0.55, 1.30, "桶 3.2MB，样本 16MB"),
    "satellite":   (2 * 1024 * 1024, 0.55, 1.35, "BDP≈350KB，样本 2MB"),
    "high_latency": (4 * 1024 * 1024, 0.55, 1.35, "BDP≈1MB，样本 4MB"),
    "high_loss":   (4 * 1024 * 1024, 0.30, 1.30, "30% 丢包，吞吐本就会塌"),
}

for key, (size, lo, hi, note) in PLAN.items():
    scene(key)
    st = api("/api/status")
    exp = (st.get("profile") or {}).get("down_kbps", 0)
    got, el = download(size)
    kbps = got * 8 / 1024 / el if el else 0
    ok = got == size and lo * exp <= kbps <= hi * exp
    rec("%-12s 设定 %d kbps" % (key, exp), ok,
        "实测 %.0f kbps（%.2fx）%d/%d B / %.2fs | %s"
        % (kbps, kbps / exp if exp else 0, got, size, el, note))

api("/api/run", {"enable": False})
bad = [x for x in R if not x[1]]
print("\n===== Windows 高速/高延迟档复测：%d/%d 通过 =====" % (len(R) - len(bad), len(R)))
for n, _o, d in bad:
    print("FAILED: %s | %s" % (n, d))
