# -*- coding: utf-8 -*-
"""性能门槛（默认不跑：pytest -m perf）。

判定方式（详见 tests/perf/baseline.py）
--------------------------------------
* **正确性优先**：每个样本都断言状态码与字节数 —— 快但错没有意义。
* **绝对区间**保留原有的产品语义（限速精度 ±15%、RTT ±20%、并发不超 1.15×），
  它回答的是「弱网参数有没有真的生效」。
* **相对基线**回答「这一版比上一版慢了没有」：同机指纹才比较，默认
  warn 5% / fail 15%。换机器只提示不判定，避免共享 runner 噪声把门禁变成噪音。
* 每项跑 WN_PERF_REPEATS 次（默认 7），报中位数与 p95，禁止用单次结果下结论。

环境变量
--------
WN_PERF_REPEATS          每项重复次数（默认 7）
WN_PERF_UPDATE_BASELINE  置 1 时把本次样本写成新基线（默认只读不写）

历史教训：`tun_ab.json` 两轮 VPN on/off 结论完全相反，就是因为单次采样。
"""
import os
import threading
import time

import pytest

from perf import baseline as B
from support import HttpReader, connect_via_proxy

pytestmark = pytest.mark.perf

REPEATS = B.repeats()              # 与基线文件里的 repeats 字段同源，不会各写各的
UPDATE = os.environ.get("WN_PERF_UPDATE_BASELINE") == "1"
SAMPLES = {}


def _workload(key):
    """固定输入统一从基线契约取，避免 URL 参数与归档的 inputs 悄悄漂移。"""
    return B.WORKLOAD[key]


def _url(key):
    w = _workload(key)
    return w["path"] + ("?kb=%d" % w["kb"] if "kb" in w else "")


@pytest.fixture(scope="session", autouse=True)
def _flush_baseline():
    """整个模块跑完后按需落盘（只有显式要求才更新基线）。"""
    yield
    if UPDATE and SAMPLES:
        p = B.save(SAMPLES)
        print("\n基线已更新：%s（%d 个指标）" % (p, len(SAMPLES)))


def _fp_line():
    fp = B.fingerprint()
    return ("%s | Python %s | CPU 核数 %s | %s"
            % (fp["platform"], fp["python"], fp["cpu_count"], fp["host"]))


def gate(key, values):
    """记录样本 → 与基线比较 → 返回统计。只有 fail 才让用例失败。"""
    st = B.stats(values)
    SAMPLES.setdefault(key, []).extend(values)
    v = B.compare(key, st["median"])
    print("\n[%s] n=%d 中位 %s p95 %s ｜ %s"
          % (key, st["n"], st["median"], st["p95"], v["msg"]))
    if v["level"] == "fail":
        pytest.fail(v["msg"])
    if v["level"] != "ok":
        print("     ↑ %s（不判定为失败）" % v["level"])
    return st


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
    print("\n机器指纹：" + _fp_line())
    w = _workload("bw_8000_kbps")
    engine.apply(**w["profile"])
    vals = []
    for _i in range(REPEATS):
        status, body, elapsed = fetch(engine, http_origin, _url("bw_8000_kbps"))
        assert status == 200 and len(body) == w["kb"] * 1024
        vals.append(len(body) * 8 / 1024.0 / elapsed)   # 1 kbps = 1024 bit/s
    st = gate("bw_8000_kbps", vals)
    ratio = st["median"] / 8000.0
    print("带宽 2MB@8000kbps：中位 %.0f kbps（%.3fx）" % (st["median"], ratio))
    assert 0.85 <= ratio <= 1.15, "限速精度超出 ±15%%：%.3fx" % ratio


def test_rtt_within_20_percent(engine, http_origin):
    engine.apply(**_workload("rtt_400ms_s")["profile"])
    vals = []
    for _i in range(REPEATS):
        status, _body, elapsed = fetch(engine, http_origin, _url("rtt_400ms_s"))
        assert status == 200
        vals.append(elapsed)
    st = gate("rtt_400ms_s", vals)
    print("RTT 200+200ms：中位 %.0f ms" % (st["median"] * 1000))
    assert 0.32 <= st["median"] <= 0.55, "RTT 精度超出 ±20%%：%.0f ms" % (st["median"] * 1000)


def test_eight_concurrent_stay_within_cap(engine, http_origin):
    w = _workload("bw_concurrent8_kbps")
    engine.apply(**w["profile"])
    vals = []
    for _round in range(REPEATS):
        results = []

        def one():
            results.append(fetch(engine, http_origin, _url("bw_concurrent8_kbps")))

        t0 = time.monotonic()
        threads = [threading.Thread(target=one, daemon=True)
                   for _ in range(w["concurrency"])]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        elapsed = time.monotonic() - t0
        assert len(results) == w["concurrency"]
        assert all(r[0] == 200 and len(r[1]) == w["kb"] * 1024 for r in results)
        total = sum(len(r[1]) for r in results)
        vals.append(total * 8 / 1024.0 / elapsed)   # 1 kbps = 1024 bit/s
    st = gate("bw_concurrent8_kbps", vals)
    ratio = st["median"] / 8000.0
    print("8 并发@8000kbps：中位合计 %.0f kbps（%.3fx）" % (st["median"], ratio))
    # 并发不得叠加超速：这是限速器的产品语义，与「快慢」无关
    assert ratio <= 1.15, "并发总速率超过设定值 1.15 倍：%.3fx" % ratio


def test_stop_releases_backlog_within_half_second(engine, http_origin):
    w = _workload("stop_release_s")
    vals = []
    for _i in range(REPEATS):
        # 每一轮都要重新下发参数：上一轮末尾的 /api/stop 会把限速关掉，
        # 第二轮如果不在限速状态下开始，1MB 会瞬间下完，这条用例就失去意义了。
        engine.apply(**w["profile"])
        state = {}
        done = threading.Event()

        def download():
            try:
                state["status"], state["body"], _el = fetch(
                    engine, http_origin, _url("stop_release_s"))
            except Exception as e:
                state["error"] = repr(e)
            finally:
                state["done_at"] = time.monotonic()
                done.set()

        th = threading.Thread(target=download, daemon=True)
        th.start()
        try:
            time.sleep(w["stop_after_s"])
            assert not done.is_set(), "1MB@300kbps 不可能 0.6s 下完"
            # 测量修正（外部真值报告 §3.8）：released 窗口原来含 /api/stop 的往返
            # （引擎忙时可变 15~60ms），把 API 延迟和排空量混在一个数里。现分开：stop_api_ms 单独记录，
            # released 从停止应答返回后起算——只量「积压排空」。口径变了，基线需重录。
            t_stop = time.monotonic()
            assert engine.api.get("/api/stop").status == 200
            stop_api_ms = (time.monotonic() - t_stop) * 1000.0
            t_drain = time.monotonic()
            assert done.wait(10), "停止后下载没有结束"
            released = state["done_at"] - t_drain
            assert "error" not in state, state.get("error")
            assert state["status"] == 200
            assert len(state["body"]) == w["kb"] * 1024
            vals.append(released)
        finally:
            if th.is_alive():
                engine.api.get("/api/stop")
                th.join(timeout=5)
    st = gate("stop_release_s", vals)
    print("停止后放行积压：中位 %.0f ms" % (st["median"] * 1000))
    assert st["median"] < 0.5, "积压放行超过 0.5s：%.0f ms" % (st["median"] * 1000)
