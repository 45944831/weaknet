# -*- coding: utf-8 -*-
"""探针：5G 档（设定 100 Mbps）在不同载荷长度下的实测吞吐，判断是令牌桶突发额度还是持续超发。
另测 highspeed_rail 的 RTT 异常（出现 0ms 与 >1s）。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _w_lib as L

SRV = ("127.0.0.1", 9125)


def api(path, obj):
    import json
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request("http://127.0.0.1:8899" + path,
                                 data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with op.open(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")


def down(n, timeout=300):
    t = L.Tunnel(host=SRV[0], port=SRV[1], timeout=timeout)
    try:
        return t.down(n)
    finally:
        t.close()


api("/api/preset", {"key": "5g"})
api("/api/run", {"enable": True})
print("=== 5g 设定 100 Mbps，不同载荷 ===")
for n in (256 * 1024, 1024 * 1024, 2 * 1024 * 1024, 4 * 1024 * 1024,
          8 * 1024 * 1024, 16 * 1024 * 1024, 32 * 1024 * 1024):
    got, sec, _ = down(n)
    print("%5.1f MB  %6.3fs  %8.2f Mbps  %5.2fx" % (n / 1048576.0, sec,
                                                   got * 8 / sec / 1e6,
                                                   (got * 8 / sec / 1e6) / 100.0), flush=True)

api("/api/preset", {"key": "4g"})
print("=== 4g 设定 20 Mbps ===")
for n in (256 * 1024, 1024 * 1024, 2 * 1024 * 1024, 8 * 1024 * 1024, 16 * 1024 * 1024):
    got, sec, _ = down(n)
    print("%5.1f MB  %6.3fs  %8.2f Mbps  %5.2fx" % (n / 1048576.0, sec,
                                                   got * 8 / sec / 1e6,
                                                   (got * 8 / sec / 1e6) / 20.0), flush=True)

api("/api/preset", {"key": "direct"})
api("/api/run", {"enable": False})

print("=== highspeed_rail 单连接 10 次 PING ===")
api("/api/preset", {"key": "highspeed_rail"})
api("/api/run", {"enable": True})
for round_ in range(2):
    t = L.Tunnel(host=SRV[0], port=SRV[1], timeout=60)
    try:
        out = []
        for i in range(10):
            rtt, srv = t.ping(i)
            out.append(round(rtt, 1))
        print("round%d: %s" % (round_, out), flush=True)
    finally:
        t.close()
api("/api/run", {"enable": False})
