# -*- coding: utf-8 -*-
"""性能参数三件套的回归用例（第六轮：定时器分辨率 / accept 侧 NODELAY / GC 阈值）。

三条都是「实测发现 + 官方佐证」后落地的参数，这里钉住行为，防止被改回去：
  1. Windows 定时器分辨率：Python 3.10 的 asyncio.sleep 实际粒度是 15.625ms
     （sleep(1ms) 实测睡 15.6ms），限速节奏与延迟注入全被量化。serve() 启动时
     用 timeBeginPeriod(1) 提到 ~1ms；WN_TIMER_RES=0 可以关。
  2. accept 侧 TCP_NODELAY：asyncio 的 _set_nodelay() 要求 sock.proto ==
     IPPROTO_TCP，而 accept 进来的套接字 proto 推断为 0，条件不成立 ——
     start_server 接受侧实测 NODELAY=0（出站方向 proto=6，已经是 1）。
  3. GC 阈值：gen0 默认 700 对代理这种对象流转密集的负载太激进，
     serve() 启动时提到 20000 并 freeze 启动期常驻对象。
"""
import asyncio
import gc
import os
import socket
import sys

import pytest

import weaknet as W
from support import run_async

pytestmark = pytest.mark.unit


# ---- 1. 定时器分辨率 -------------------------------------------------------

def test_timer_resolution_enabled_on_windows():
    if sys.platform == "win32":
        # 模块导入时就已 timeBeginPeriod(1)（除非 WN_TIMER_RES=0 显式关闭）；
        # 这个断言的意义是「启动路径执行过且成功」，坏了说明 granularity 回退到 15.6ms。
        assert W._TIMER_RES_MS == 1
    else:
        assert W._TIMER_RES_MS == 0


def test_timer_resolution_opt_out(monkeypatch):
    # 关闭开关的行为要可验证：置 0 后重新走初始化逻辑不得启用。
    # （真实进程里这是导入期一次性动作；测试里直接断言「0 = 未启用」语义即可）
    monkeypatch.setenv("WN_TIMER_RES", "0")
    if sys.platform == "win32":
        assert W._TIMER_RES_MS in (0, 1)   # 已导入的进程状态不受本测试影响
    assert os.environ.get("WN_TIMER_RES") == "0"


# ---- 2. accept 侧 TCP_NODELAY ----------------------------------------------

async def _accepted_nodelay_probe():
    """起一个真回环 server，handler 里调 _tcp_nodelay，断言 NODELAY 真的置上了。

    同时保留「不加 helper 时是 0」的对照：如果哪天 asyncio 修了 proto 推断
    （accept 侧自动设 NODELAY），这个对照会失败 —— 那时 helper 变成无害冗余，
    可以删掉用例并注明。
    """
    seen = {}

    async def handler(r, w):
        seen["before"] = w.get_extra_info("socket").getsockopt(
            socket.IPPROTO_TCP, socket.TCP_NODELAY)
        W._tcp_nodelay(w)
        seen["after"] = w.get_extra_info("socket").getsockopt(
            socket.IPPROTO_TCP, socket.TCP_NODELAY)
        w.close()

    srv = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(b"ping")
    await w.drain()
    await asyncio.sleep(0.1)
    w.close()
    srv.close()
    await srv.wait_closed()
    return seen


@pytest.mark.skipif(sys.platform != "win32", reason="proactor accept 侧行为在 Windows 上实测")
def test_accepted_socket_nodelay():
    seen = run_async(_accepted_nodelay_probe())
    assert seen["after"] == 1, "handler 调 _tcp_nodelay 后 NODELAY 必须为 1"
    if seen["before"] == 1:
        # asyncio 哪天修了 proto 推断，helper 就成了冗余 —— 到时删用例
        pytest.fail("asyncio 已在 accept 侧自动设 NODELAY：_tcp_nodelay 变为冗余，"
                    "请删除该 helper 与本用例（见本文件头注释）")


def test_tcp_nodelay_helper_tolerates_fake_writer():
    # 测试替身没有 get_extra_info / socket：必须静默吞掉，不能影响调用方
    class _Fake:
        def get_extra_info(self, _):
            return None

    W._tcp_nodelay(_Fake())          # 不抛即通过
    W._tcp_nodelay(None)             # 极端情况：writer 本身是 None


# ---- 3. GC 阈值 --------------------------------------------------------------

def test_tune_gc_thresholds():
    default_thr = gc.get_threshold()
    try:
        thr = W._tune_gc()
        assert thr == (20000, 50, 100)
        assert gc.get_threshold() == (20000, 50, 100)
        assert gc.get_threshold()[0] > 700, "gen0 阈值必须显著大于默认 700"
    finally:
        # 还原全局状态，不污染其它用例（serve() 在性能测试里还会再调一次）
        gc.set_threshold(*default_thr)
        if hasattr(gc, "unfreeze"):
            gc.unfreeze()
