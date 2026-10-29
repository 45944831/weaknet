# -*- coding: utf-8 -*-
"""性能门槛（默认不跑：pytest -m perf）。

判定标准来自优化方案的验收项：
  * 带宽实测与设定值偏差 ≤ 15%
  * RTT 偏差 ≤ 20%
  * 8 并发总速率不超过设定的 1.15 倍
  * 点「停止」后积压数据在 0.5s 内放行

这些阈值与机器相关，跨机比较没有意义，所以先打印机器指纹再断言；
CI 上默认排除 perf，只在夜间/手动触发。
"""
import os
import platform
import socket
import sys
import threading
import time

import pytest

from support import HttpReader, connect_via_proxy

pytestmark = pytest.mark.perf


def machine_fingerprint():
    return "%s | Python %s | CPU 核数 %s | %s" % (
        platform.platform(), sys.version.split()[0],
        os.cpu_count(), socket.gethostname())


def fetch(engine, origin, path, timeout=120):
    sock, pre = connect_via_proxy(engine.proxy, origin.host, origin.port,
                                  timeout=timeout)
    try:
        reader = HttpReader(sock, pre)
        t0 = time.monotonic()
        sock.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\n\r\n"
                      % (path, origin.host, origin.port)).encode("latin1"))
        status, _h, body = reader.response()
        return status, body, time.monotonic() - t0
    finally:
        sock.close()


def test_bandwidth_within_15_percent(engine, http_origin):
    print("\n机器指纹：" + machine_fingerprint())
    engine.apply(down_kbps=8000)
    status, body, elapsed = fetch(engine, http_origin, "/big?kb=2048")
    assert status == 200 and len(body) == 2048 * 1024
    kbps = len(body) * 8 / 1000.0 / elapsed
    print("带宽 2MB@8000kbps：实测 %.0f kbps（%.3fx）" % (kbps, kbps / 8000.0))
    assert 0.85 <= kbps / 8000.0 <= 1.15


def test_rtt_within_20_percent(engine, http_origin):
    engine.apply(latency_up_ms=200, latency_down_ms=200)
    status, _body, elapsed = fetch(engine, http_origin, "/small")
    assert status == 200
    print("RTT 200+200ms：实测 %.0f ms" % (elapsed * 1000))
    assert 0.32 <= elapsed <= 0.55


def test_eight_concurrent_stay_within_cap(engine, http_origin):
    engine.apply(down_kbps=8000)
    results = []

    def one():
        results.append(fetch(engine, http_origin, "/big?kb=128"))

    t0 = time.monotonic()
    threads = [threading.Thread(target=one, daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    elapsed = time.monotonic() - t0
    assert len(results) == 8
    assert all(r[0] == 200 and len(r[1]) == 128 * 1024 for r in results)
    total = sum(len(r[1]) for r in results)
    kbps = total * 8 / 1000.0 / elapsed
    print("8 并发合计 %dB@8000kbps：实测 %.0f kbps（%.3fx）"
          % (total, kbps, kbps / 8000.0))
    assert kbps / 8000.0 <= 1.15


def test_stop_releases_backlog_within_half_second(engine, http_origin):
    engine.apply(down_kbps=300)
    state = {}
    done = threading.Event()

    def download():
        try:
            state["status"], state["body"], _el = fetch(engine, http_origin,
                                                        "/big?kb=1024")
        except Exception as e:
            state["error"] = repr(e)
        finally:
            state["done_at"] = time.monotonic()
            done.set()

    th = threading.Thread(target=download, daemon=True)
    th.start()
    try:
        time.sleep(0.6)
        assert not done.is_set(), "1MB@300kbps 不可能 0.6s 下完"
        t_stop = time.monotonic()
        assert engine.api.get("/api/stop").status == 200
        assert done.wait(10), "停止后下载没有结束"
        released = state["done_at"] - t_stop
        print("停止后放行积压：%.0f ms" % (released * 1000))
        assert "error" not in state, state.get("error")
        assert state["status"] == 200
        assert len(state["body"]) == 1024 * 1024
        assert released < 0.5
    finally:
        if th.is_alive():
            engine.api.get("/api/stop")
            th.join(timeout=5)
