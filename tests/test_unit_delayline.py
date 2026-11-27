# -*- coding: utf-8 -*-
"""DelayLine 的死锁回归用例。

历史故障：两种情形都会让连接永久挂住：
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
        self.drains = 0

    def write(self, data):
        self.chunks.append(bytes(data))

    async def drain(self):
        self.drains += 1
        await asyncio.sleep(0)

    def data(self):
        return b"".join(self.chunks)


def test_every_block_waits_for_drain():
    """现状：每块都等 drain，不再按写缓冲水位跳过。

    水位版（缓冲 > 128KB 才 drain）经两轮受控 A/B 实测无收益
    （8000 kbps −1.52% / 50000 kbps +0.43%），已删除实现与 A/B 脚本。
    这条用例守住「不要再凭直觉把按水位跳过 drain 加回来」。
    """
    async def go():
        w = _Writer()
        dl = W.DelayLine(w, "drain").start()
        for i in range(3):
            await dl.submit(b"x" * 8, 0, 0)
        await dl.aclose(timeout=2)
        return w.drains

    assert run_async(go()) == 3
    assert not hasattr(W.DelayLine, "DRAIN_HIGH")


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
    """队列满 + 消费者不存在时，abort 必须立刻放行生产者。"""
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
    """消费者已死时的 aclose 不能等满超时。"""
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


class _DeadWriter(_Writer):
    """模拟 asyncio StreamWriter：连接已被 RST / close，再 write 只会打警告。"""

    class _Transport:
        _conn_lost = 1

    def __init__(self, closed=True):
        super().__init__()
        self.transport = self._Transport()
        self._closed = closed

    def is_closing(self):
        return self._closed

    def write(self, data):
        raise AssertionError("对端已断开时不应再 write()")


def test_dead_writer_fast_path_stops_without_writing():
    """iperf3 收尾断开后不得再对着死 socket 投递。"""
    async def go():
        w = _DeadWriter()
        dl = W.DelayLine(w, "dead-peer").start()
        try:
            await dl.submit(b"payload", 0, 0)
        except ConnectionError:
            pass
        else:
            raise AssertionError("submit 应当抛出 ConnectionError")
        await dl.aclose(timeout=2)
        return dl.dead

    assert run_async(go()) is True


def test_dead_writer_wakes_producer_blocked_on_full_queue(monkeypatch):
    """消费者因对端断开退出时，卡在满队列 q.put 的生产者必须被唤醒并停止。"""
    monkeypatch.setattr(W.DelayLine, "MAX_BLOCKS", 2)

    class _FlipWriter(_DeadWriter):
        def write(self, data):
            self._closed = True          # 首次真正投递时连接被 RST
            raise ConnectionResetError("peer reset")

    async def go():
        w = _FlipWriter(closed=False)
        dl = W.DelayLine(w, "dying").start()
        tasks = [asyncio.create_task(dl.submit(b"x" * 16, 0.01, 0))
                 for _ in range(4)]
        # 旧实现：消费者不停、生产者静默丢弃/永久卡在 put 上，2s 内必然超时
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), 2.0)
        return dl.dead

    assert run_async(go()) is True


def test_pipe_immediate_eof_does_not_raise_or_leak(monkeypatch):
    """连接首次 read() 就 EOF 时，finally 不能再炸 UnboundLocalError。

    旧实现把 rate_key 赋值放在 while 循环体内，首次读就 EOF 的话 finally
    里的 `prof.get(rate_key, 0)` 直接抛异常，aclose() 没机会跑，投递协程
    被整条遗弃（iperf3 -P 8 收尾成片 `Task was destroyed but it is pending!`）。
    """
    consumers = []

    class _Recorder(W.DelayLine):
        def start(self):
            super().start()
            consumers.append(self.task)
            return self

    monkeypatch.setattr(W, "DelayLine", _Recorder)

    class _Reader:
        async def read(self, n):
            return b""

    class _Ctx:
        fixed = False
        label = "immediate-eof"
        prof = {"down_kbps": 0}

        def touch(self):
            pass

        def abort_all(self):
            pass

    async def go():
        await W.pipe(_Reader(), _Writer(), {"down_kbps": 0}, "down", _Ctx())
        await asyncio.sleep(0.05)
        return [t.done() for t in consumers]

    assert run_async(go()) == [True]


def test_aclose_reaps_cancelled_consumer():
    """aclose 返回时消费者任务必须已结束，不能留下 pending Task。

    旧实现只 cancel 不 await，iperf3 -P 8 每轮收尾都会打
    `Task was destroyed but it is pending!`，属于连接级任务泄漏。
    """
    async def go():
        dl = W.DelayLine(_Writer(), "reap").start()
        t = dl.task
        await asyncio.sleep(0)
        dl.abort()
        await asyncio.wait_for(dl.aclose(timeout=2), 2.0)
        return t

    assert run_async(go()).done() is True


def test_write_eof_style_writer_is_still_writable():
    """半关闭（shutdown SHUT_WR）不置 _closing，不能被误判成对端已死。"""
    class _HalfClosed(_Writer):
        class _Transport:
            _conn_lost = 0

        transport = _Transport()

        def is_closing(self):
            return False

    async def go():
        w = _HalfClosed()
        dl = W.DelayLine(w, "half").start()
        await dl.submit(b"tail", 0, 0)
        await dl.aclose(timeout=2)
        return w.data()

    assert run_async(go()) == b"tail"


def test_pipe_uses_current_gen_for_later_blocks(monkeypatch):
    """P0（从零审查）：切场景后，在途连接的新块必须带当代 gen。

    旧实现在 pipe() 入口 `gen = G.gen` 捕获一次就不再更新。切场景使
    G.gen +1 后，新块仍带旧 gen，DelayLine._run 里 nap(wait, 旧gen) 见
    代数不符就跳过等待直接投递 —— 延迟/丢包停顿对在途连接的新块全部失效。
    本用例让 reader 在第 2 块前切一次场景，断言第 2 块 submit 传的是新 gen。
    """
    seen = []

    class _RecordingSubmit(W.DelayLine):
        async def submit(self, data, delay, gen):
            seen.append(gen)
            return await super().submit(data, delay, gen)

    monkeypatch.setattr(W, "DelayLine", _RecordingSubmit)

    class _SwitchReader:
        def __init__(self):
            self.n = 0

        async def read(self, size):
            self.n += 1
            if self.n == 1:
                return b"first"
            if self.n == 2:
                # 第 2 块读出前切场景：set_profile 的核心动作就是 G.gen += 1
                W.G.gen += 1
                return b"second"
            return b""

    class _Ctx:
        fixed = False
        label = "gen-switch"
        prof = W.default_profile()

        def touch(self):
            pass

        def abort_all(self):
            pass

    def profile_get(key, default=None):
        return W.G.profile.get(key, default)

    W.G.gen = 10
    run_async(W.pipe(_SwitchReader(), _Writer(), {}, "down", _Ctx()))
    assert len(seen) >= 2, "应至少提交两块，实际 %d" % len(seen)
    assert seen[0] == 10, "第 1 块应带入队时的 gen=10"
    assert seen[1] == 11, "第 2 块应带切场景后的当代 gen=11，实际 %d" % seen[1]
