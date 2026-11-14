# -*- coding: utf-8 -*-
"""P0-2 的另一半产物：慢回调清单。

py-spy 只能看到 CPU 热点，看不到「谁把一段同步阻塞塞进了事件循环」——
后者正是「切场景不即时 / 界面卡顿」的根因。asyncio 自己会在 debug 模式下
把超过 `loop.slow_callback_duration` 的回调打印成
`Executing <Handle ...> took 0.213 seconds`，本文件把这些输出收集成**清单**：

* 开探针（`WN_SLOW_CB_MS`，默认关）→ 引擎那条事件循环进入 debug 模式；
* 打一轮真实流量（单连接大包 ×3）；
* 抓 asyncio 的告警日志，解析出「耗时 / 回调类型 / 出现次数」；
* 归档 `tests/perf/archive/v1.1.16/slow_callbacks_2026-11-13.json`。

这是**测量**不是门禁：慢回调出现与否取决于机器负载，快慢一律不断言。
唯一断言是「探针确实装上了」——否则清单会是空的假象。

注意：debug 模式会拖慢事件循环，所以本用例标记 perf，默认不跑。
"""
import json
import logging
import re
import time
from pathlib import Path

import pytest

from support import HttpReader, connect_via_proxy

pytestmark = pytest.mark.perf

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE / "archive" / "v1.1.16" / "slow_callbacks_2026-11-13.json"

THRESHOLD_MS = 50
KB = 512
# asyncio 的告警格式：Executing <Handle Task-123 ...> took 0.213 seconds
_SLOW_RE = re.compile(r"Executing\s+(?P<handle>.+?)\s+took\s+(?P<sec>[\d.]+)\s+seconds")


@pytest.fixture
def slow_cb_env(monkeypatch):
    """必须在 engine 夹具之前把环境变量装上。

    探针是在 `serve()` 启动那一刻读环境变量的，而 engine 夹具一创建就把
    引擎拉起来了 —— 放在用例体里设就晚了，探针根本不会生效。
    夹具签名里排在 engine 前面即可保证顺序。
    """
    monkeypatch.setenv("WN_SLOW_CB_MS", str(THRESHOLD_MS))
    return THRESHOLD_MS


def _drive(engine, origin, rounds=3):
    """打一轮真实流量：单连接大包。"""
    total = 0
    for _ in range(rounds):
        sock, pre = connect_via_proxy(engine.proxy, origin.host, origin.port,
                                      timeout=60)
        try:
            reader = HttpReader(sock, pre)
            sock.sendall(("GET /big?kb=%d HTTP/1.1\r\nHost: %s:%d\r\n"
                          "Connection: close\r\n\r\n"
                          % (KB, origin.host, origin.port)).encode("latin1"))
            status, _h, body = reader.response()
            assert status == 200, status
            total += len(body)
        finally:
            sock.close()
    return total


def test_slow_callback_list(slow_cb_env, engine, http_origin, caplog):
    caplog.set_level(logging.WARNING, logger="asyncio")

    engine.apply(down_kbps=8000)
    bytes_moved = _drive(engine, http_origin)
    time.sleep(0.3)

    # 探针是否真的装上了：debug 模式 + 阈值
    loop = engine.loop
    assert loop is not None, "拿不到引擎的事件循环"
    assert loop.get_debug(), "慢回调探针未生效：事件循环没进入 debug 模式"
    assert abs(loop.slow_callback_duration - THRESHOLD_MS / 1000.0) < 1e-6, \
        "慢回调阈值没设成 %d ms" % THRESHOLD_MS

    entries = []
    for rec in caplog.records:
        m = _SLOW_RE.search(rec.getMessage())
        if not m:
            continue
        entries.append({
            "seconds": float(m.group("sec")),
            "ms": round(float(m.group("sec")) * 1000.0, 3),
            "handle": m.group("handle")[:300],
            "logger": rec.name,
        })
    entries.sort(key=lambda x: x["ms"], reverse=True)

    by_kind = {}
    for e in entries:
        # 回调种类：把 task/handle 名字归一，便于聚类
        kind = re.sub(r"Task-\d+", "Task-N", e["handle"])
        kind = re.sub(r"\b\d+\b", "N", kind)[:120]
        by_kind.setdefault(kind, {"count": 0, "max_ms": 0.0})
        by_kind[kind]["count"] += 1
        by_kind[kind]["max_ms"] = max(by_kind[kind]["max_ms"], e["ms"])

    out = {
        "schema": 1,
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": "P0-2 慢回调清单：事件循环里超过阈值的同步阻塞回调",
        "threshold_ms": THRESHOLD_MS,
        "workload": {"kb": KB, "rounds": 3, "rate_kbps": 8000,
                     "bytes_moved": bytes_moved},
        "probe_armed": True,
        "total_slow_callbacks": len(entries),
        "max_ms": max([e["ms"] for e in entries], default=0.0),
        "by_kind": by_kind,
        "entries": entries[:200],
        "limitations": [
            "进程内引擎 + 本机回环源站，与真机/EXE 的线程画像不同",
            "asyncio debug 模式会拖慢事件循环，本清单只用于定位阻塞点，不用于性能结论",
            "阈值 50 ms 是「用户能感知的卡顿」量级，不是通用标准",
        ],
    }
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print("\n[慢回调] 阈值 %d ms，命中 %d 条，最长 %.1f ms；已归档：%s"
          % (THRESHOLD_MS, len(entries), out["max_ms"], ARCHIVE))
