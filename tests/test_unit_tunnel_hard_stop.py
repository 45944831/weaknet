# -*- coding: utf-8 -*-
"""run_tunnel 的 hard_stop 判定回归：纯 asyncio，不依赖网络、不起端口。

历史 bug：用 ``try: t.exception() except Exception`` 判断 pipe 是否失败，
但 Task.exception() 是**返回**异常对象而非抛出，只有 CancelledError /
InvalidStateError 才会抛。于是 pipe 抛 Abort / ConnectionError 时 hard_stop
恒为 False，代码继续对已经 abort 的 writer 做 write_eof()，挂起的
keep-alive 客户端最多多活 60~120s。
"""
import asyncio
import time
import types

import pytest

import weaknet as W

pytestmark = pytest.mark.unit


def test_task_failed_distinguishes_exception_ok_and_cancelled():
    """三种结束状态：异常=True，正常=False，取消=False（不能抛 CancelledError）。"""
    async def boom():
        raise RuntimeError("pipe aborted")

    async def ok():
        return None

    async def main():
        bad = asyncio.create_task(boom())
        good = asyncio.create_task(ok())
        done, _ = await asyncio.wait({bad, good})
        assert len(done) == 2
        assert W.task_failed(bad) is True
        assert W.task_failed(good) is False

        cancelled = asyncio.create_task(asyncio.sleep(60))
        cancelled.cancel()
        try:
            await cancelled
        except asyncio.CancelledError:
            pass
        assert W.task_failed(cancelled) is False
        await asyncio.gather(bad, good, return_exceptions=True)

    asyncio.run(main())


def test_run_tunnel_hard_stops_without_write_eof_on_failed_side(monkeypatch):
    """up 方向抛错时必须立刻 hard_stop，不得对任何一侧做无效的 write_eof。"""
    class FakeWriter:
        def __init__(self):
            self.eofs = 0

        def write_eof(self):
            self.eofs += 1

    async def fake_pipe(reader, writer, prof, direction, ctx):
        if direction == "up":
            raise RuntimeError("abort")
        await asyncio.Event().wait()       # 正常方向故意挂住，等 run_tunnel 取消

    monkeypatch.setattr(W, "pipe", fake_pipe)
    ctx = types.SimpleNamespace(last_active=time.monotonic())
    s_writer, c_writer = FakeWriter(), FakeWriter()

    async def main():
        await asyncio.wait_for(
            W.run_tunnel(ctx, asyncio.StreamReader(), s_writer,
                         asyncio.StreamReader(), c_writer, {}),
            timeout=5)

    asyncio.run(main())
    assert s_writer.eofs == 0
    assert c_writer.eofs == 0


def test_run_tunnel_uses_task_failed(monkeypatch):
    """生产路径必须调用 task_failed：否则上面的单测只是在测一个影子函数。"""
    calls = []
    real = W.task_failed

    def spy(t):
        calls.append(t)
        return real(t)

    monkeypatch.setattr(W, "task_failed", spy)

    async def fake_pipe(reader, writer, prof, direction, ctx):
        if direction == "up":
            raise RuntimeError("abort")
        await asyncio.Event().wait()

    monkeypatch.setattr(W, "pipe", fake_pipe)
    ctx = types.SimpleNamespace(last_active=time.monotonic())

    class FakeWriter:
        def write_eof(self):
            pass

    async def main():
        await asyncio.wait_for(
            W.run_tunnel(ctx, asyncio.StreamReader(), FakeWriter(),
                         asyncio.StreamReader(), FakeWriter(), {}),
            timeout=5)

    asyncio.run(main())
    assert calls, "run_tunnel 没有调用 task_failed，内联判定会绕过回归测试"
