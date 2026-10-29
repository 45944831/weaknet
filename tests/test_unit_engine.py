# -*- coding: utf-8 -*-
"""带宽令牌桶与可打断 nap 的白盒用例。

这两处是最容易「改一行就静默坏掉」的地方：
  * 令牌桶把 sleep 放进锁里 → 点「停止」要等当前睡眠者醒来（用户感知为卡住）；
  * 桶速率按连接各建一份 → 8 条并发跑出设定带宽的 2.6 倍。
"""
import asyncio
import time

import pytest

import weaknet as W
from support import run_async

pytestmark = pytest.mark.unit


def test_bucket_steady_rate():
    async def go():
        bucket = W.Bucket(8000)                 # 8000 kbps = 1 MB/s
        t0 = time.monotonic()
        await bucket.consume(1024 * 1024)
        return time.monotonic() - t0

    el = run_async(go())
    assert 0.85 <= el <= 1.45, "1MB@8000kbps 实测 %.2fs（理论 1.00s）" % el


def test_bucket_concurrent_total_is_capped():
    async def go():
        bucket = W.Bucket(8000)
        t0 = time.monotonic()
        await asyncio.gather(*[bucket.consume(256 * 1024) for _ in range(4)])
        return time.monotonic() - t0

    el = run_async(go())
    assert el >= 0.85, "4 并发合计 1MB 只用了 %.2fs，说明各自拿了一份配额" % el
    assert el <= 1.8, "4 并发合计 1MB 用了 %.2fs，明显低于设定速率" % el


def test_bucket_sleep_happens_outside_lock():
    async def go():
        bucket = W.Bucket(2000)                 # 256 KB/s：16MB 够它睡很久
        big = asyncio.create_task(bucket.consume(16 * 1024 * 1024))
        await asyncio.sleep(0.1)                # 让大额任务进入睡眠分支
        t0 = time.monotonic()
        async with bucket.lock:
            pass
        waited = time.monotonic() - t0
        big.cancel()
        try:
            await big
        except asyncio.CancelledError:
            pass
        return waited

    waited = run_async(go())
    assert waited < 0.05, "睡眠占着锁：他人获锁等了 %.0fms" % (waited * 1000)


def test_bucket_stop_releases_waiting_consumer():
    async def go():
        bucket = W.Bucket(500)                  # 64 KB/s
        task = asyncio.create_task(bucket.consume(8 * 1024 * 1024))
        await asyncio.sleep(0.1)
        t0 = time.monotonic()
        bucket.sync(0)                          # 相当于「停止限速」
        await asyncio.wait_for(task, 2.0)
        return time.monotonic() - t0

    el = run_async(go())
    assert el < 0.5, "停止限速后排队者 %.2fs 才放行" % el


def test_nap_wakes_immediately_on_gen_change():
    async def go():
        W.G.gen = 3
        W.G.wake = asyncio.Event()
        task = asyncio.create_task(W.nap(5.0, 3))
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        W.G.gen = 4
        W.G.wake.set()
        woke = await asyncio.wait_for(task, 2.0)
        return woke, time.monotonic() - t0

    woke, el = run_async(go())
    assert woke is True, "切场景没有打断 nap"
    assert el < 0.5, "切场景后 %.2fs 才醒来" % el


def test_nap_finishes_normally_when_not_interrupted():
    async def go():
        W.G.gen = 7
        W.G.wake = asyncio.Event()
        t0 = time.monotonic()
        woke = await W.nap(0.2, 7)
        return woke, time.monotonic() - t0

    woke, el = run_async(go())
    assert woke is False
    assert el >= 0.18, "nap(0.2) 只等了 %.3fs" % el
