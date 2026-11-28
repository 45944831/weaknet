# -*- coding: utf-8 -*-
"""测试会话基线：报告只统计「本次测试」的增量。

背景（外部审查）：connTotal / bytes 是引擎生命周期累计值。直接拿它出报告，
会导致「开始测试前跑过一条连接 → 开着场景但本次零流量」也判有效，历史故障
也会算进本次样本。这里锁住三件事：
  * 会话基线只打一次，同一次测试内切场景不重置；
  * 增量 = 当前累计 - 基线，故障计数取累计值（不受 300 条环形缓冲影响）；
  * 停止态编辑的参数在开启时原样生效（草稿），不被旧场景覆盖。
"""
import asyncio

import pytest

import weaknet as W
from conftest import reset_engine_state
from support import run_async

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh():
    reset_engine_state()
    try:
        yield
    finally:
        reset_engine_state()


def test_session_delta_is_baseline_not_cumulative():
    W.G.conns_total = 11
    W.G.bytes_up = 500
    W.G.bytes_down = 900
    W.G.fault_cum["timeout"] = 3

    W.begin_test_session()
    seq = W.G.test_seq
    assert seq >= 1

    W.G.conns_total = 14
    W.G.bytes_up = 800
    W.G.bytes_down = 1200
    W.G.fault_cum["timeout"] = 5

    s = W.test_session_payload()
    assert s["seq"] == seq
    assert s["total"] == 3, s
    assert s["up"] == 300, s
    assert s["down"] == 300, s
    assert s["faults"]["timeout"] == 2, s


def test_ensure_session_is_idempotent_within_one_test():
    W.ensure_test_session()
    first = W.G.test_seq
    W.G.conns_total = 4
    W.ensure_test_session()          # 测试中切场景 / 重复 enable
    assert W.G.test_seq == first
    # 基线没被重置：4 条连接全部算本次
    assert W.test_session_payload()["total"] == 4


def test_stopped_edit_keeps_draft_until_start():
    prof = W.default_profile()
    prof["latency_down_ms"] = 480
    prof["down_kbps"] = 256

    run_async(W.select_scene(prof, "custom"))
    assert W.G.limit_on is False
    assert W.G.pending_profile["latency_down_ms"] == 480

    run_async(W.set_limit(True))
    assert W.G.limit_on is True
    assert W.G.profile["latency_down_ms"] == 480
    assert W.G.profile["down_kbps"] == 256
    # 草稿落地后清空，避免停止→再开启时拿旧草稿覆盖测试中新调的值
    assert W.G.pending_profile is None


def test_restart_proxy_restores_old_host_when_bind_fails(engine, monkeypatch):
    """换绑失败不能把旧监听带走：界面地址与可用性必须一致。"""
    real = asyncio.start_server

    async def fake(handler, host, port, **kw):
        if host == "0.0.0.0":
            raise OSError(10048, "address already in use")
        return await real(handler, host, port, **kw)

    monkeypatch.setattr(asyncio, "start_server", fake)
    fut = asyncio.run_coroutine_threadsafe(W.restart_proxy("0.0.0.0"), engine.loop)
    with pytest.raises(OSError):
        fut.result(timeout=10)

    assert W.G.proxy_host == "127.0.0.1"
    assert W.G.proxy_server is not None
    names = [s.getsockname()[0] for s in W.G.proxy_server.sockets]
    assert names and names[0] == "127.0.0.1", names
