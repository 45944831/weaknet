# -*- coding: utf-8 -*-
"""DelayLine 的死锁回归用例。

历史故障（P0-1）两条路都会让连接永久挂住：
  1. 消费者先退出，生产者卡在满队列的 q.put 上 —— 没人再取，永远等；
  2. 消费者已死时调 aclose()，哨兵 put 同样等不到空位。
修复点：_mark_dead 先 _drain（get_nowait 会唤醒 putter），aclose 走 dead 分支直接返回。
这些用例必须在修复被删掉时稳定失败，而不是「跑十分钟超时」。
"""
import asyncio

import pytest

import weaknet as W
from support import run_async

pytestmark = pytest.mark.unit


class _Writer:
    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(bytes(data))

    async def drain(self):
        await asyncio.sleep(0)

    def data(self):
        return b"".join(self.chunks)


def test_zero_delay_fast_path_writes_immediately():
    async def go():
        w = _Writer()
        dl = W.DelayLine(w, "test").start()
        for i in range(3):
            await dl.submit(b"chunk%d" % i, 0, 0)
        wrote = list(w.chunks)
        await dl.aclose(timeout=2)
        return wrote

    wrote = run_async(go())
    assert wrote == [b"chunk0", b"chunk1", b"chunk2"]


def test_abort_unblocks_producer_stuck_on_full_queue(monkeypatch):
    """队列满 + 消费者不存在时，abort 必须立刻放行生产者（P0-1 路径 1）。"""
    monkeypatch.setattr(W.DelayLine, "MAX_BLOCKS", 2)

    async def go():
        dl = W.DelayLine(_Writer(), "stuck")     # 故意不 start：没有消费者
        tasks = [asyncio.create_task(dl.submit(b"x" * 16, 10, 0))
                 for _ in range(4)]
        await asyncio.sleep(0.05)                # 后两个应卡在 q.put 上
        dl.abort()
        await asyncio.wait_for(asyncio.gather(*tasks), 2.0)
        return dl.dead

    assert run_async(go()) is True


def test_aclose_after_consumer_death_returns(monkeypatch):
    """消费者已死时的 aclose 不能等满超时（P0-1 路径 2）。"""
    async def go():
        dl = W.DelayLine(_Writer(), "dead").start()
        await asyncio.sleep(0)
        dl.abort()
        t0 = asyncio.get_running_loop().time()
        await asyncio.wait_for(dl.aclose(timeout=10), 2.0)
        return asyncio.get_running_loop().time() - t0

    el = run_async(go())
    assert el < 0.5, "aclose 在消费者已死时等了 %.2fs" % el


def test_aclose_delivers_queued_tail():
    """结束前必须把队列里最后几块投递完，短连接的响应尾部不能丢。"""
    async def go():
        w = _Writer()
        dl = W.DelayLine(w, "tail").start()
        await dl.submit(b"tail-bytes", 0.05, 0)
        await dl.aclose(timeout=5)
        return w.data()

    assert run_async(go()) == b"tail-bytes"


def test_submit_after_abort_is_dropped():
    async def go():
        w = _Writer()
        dl = W.DelayLine(w, "closed")
        dl.abort()
        await dl.submit(b"late", 0, 0)
        return w.data()

    assert run_async(go()) == b""
