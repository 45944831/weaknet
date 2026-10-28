# -*- coding: utf-8 -*-
"""W1~W6 优化项 · 进程内单元测试（精确、无网络抖动）。

直接 import weaknet 模块，对改动点做白盒断言：
  A1  W1  _tcp_pid_map 300ms TTL 快照复用
  A2  W2  Bucket 稳态速率
  A3  W2  Bucket 并发聚合不超标（预扣语义）
  A4  W2  Bucket 睡眠在锁外（停止/切场景不被睡眠者挡住）
  A5  W3  nap 等满 remain（不再 200ms 轮询空转）
  A6  W3  nap 被 gen 切换立即唤醒
  A7  W6  _LogSink 攒批落盘
  A8  W6  _LogSink 超限轮转
  A9  W6  flush_logs 把缓冲写下去
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, ROOT)

import weaknet as W  # noqa: E402




RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""),
          flush=True)


# ---------------------------------------------------------------- A1 W1
def a1():
    c = W._TCP_PID_MAP_CACHE
    calls = []
    real = W._tcp_rows
    W._tcp_rows = lambda: (calls.append(1), real())[1]
    try:
        c["at"] = 0.0
        c["map"] = {}
        W._tcp_pid_map()
        n1 = len(calls)
        W._tcp_pid_map()
        n2 = len(calls)          # TTL 内：不应再扫
        time.sleep(0.35)
        W._tcp_pid_map()
        n3 = len(calls)          # TTL 过期：应再扫
        W._tcp_pid_map(force=True)
        n4 = len(calls)          # force：强制刷新
        ok = (n1 == 1 and n2 == 1 and n3 == 2 and n4 == 3)
        rec("W1 PID 快照 300ms TTL", ok,
            "扫描次数 首次=%d TTL内=%d 过期后=%d force后=%d（期望 1/1/2/3）"
            % (n1, n2, n3, n4))
    finally:
        W._tcp_rows = real
        c["at"] = 0.0
        c["map"] = {}


# ---------------------------------------------------------------- A2~A4 W2
async def a2():
    b = W.Bucket(20000)                       # 20000 kbps = 2560000 B/s
    n = 8 * 1024 * 1024
    t0 = time.monotonic()
    await b.consume(n)
    el = time.monotonic() - t0
    kbps = n * 8 / 1024 / el
    ok = 0.90 * 20000 <= kbps <= 1.10 * 20000
    rec("W2 令牌桶稳态速率 8MB@20000kbps", ok,
        "实测 %.0f kbps（%.3fx）el=%.2fs 理论 %.2fs" % (kbps, kbps / 20000, el, n / 2560000.0))


async def a3():
    b = W.Bucket(20000)
    per = 512 * 1024
    nconn = 8
    t0 = time.monotonic()
    await asyncio.gather(*[b.consume(per) for _ in range(nconn)])
    el = time.monotonic() - t0
    total = per * nconn
    kbps = total * 8 / 1024 / el
    ok = kbps <= 20000 * 1.10
    rec("W2 8 并发聚合总速率不超标", ok,
        "合计 %.0f kbps（%.2fx）el=%.2fs 理论 %.2fs"
        % (kbps, kbps / 20000, el, total / 2560000.0))


async def a4():
    """睡眠必须在锁外：大额 consume 睡着时，别人要能立刻拿到锁。

    旧实现把 sleep 放在 async with self.lock 里，asyncio.Lock 不像 Java
    监视器那样等待时释放 —— 别的连接的 consume 全堵在这把锁上，
    点「停止」也要等当前睡眠者（最多 0.25s）醒来才看得到 rate == 0。
    """
    b = W.Bucket(2000)                        # 256000 B/s
    big = asyncio.create_task(b.consume(16 * 1024 * 1024))
    await asyncio.sleep(0.10)                 # 让大额任务进入睡眠分支
    t0 = time.monotonic()
    async with b.lock:                        # 能立刻进去 => 锁确实已释放
        pass
    el = time.monotonic() - t0
    big.cancel()
    try:
        await big
    except (asyncio.CancelledError, Exception):
        pass
    ok = el < 0.05
    rec("W2 睡眠在锁外（他人可立即获锁）", ok, "获锁耗时 %.1f ms" % (el * 1000))


async def a4b():
    """限速被取消时，排队的 consume 要立刻放行（不等当前睡眠者）。"""
    b = W.Bucket(2000)
    t = asyncio.create_task(b.consume(16 * 1024 * 1024))
    await asyncio.sleep(0.10)
    b.sync(0)                                 # 相当于「停止限速」
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(t, timeout=1.0)
        el = time.monotonic() - t0
        ok = el < 0.3
        rec("W2 取消限速后排队任务立即放行", ok, "放行耗时 %.1f ms" % (el * 1000))
    except (asyncio.TimeoutError, TimeoutError):
        rec("W2 取消限速后排队任务立即放行", False, "1s 内未放行")
        t.cancel()


# ---------------------------------------------------------------- A5~A6 W3
async def a5():
    W.G.wake = asyncio.Event()
    gen = W.G.gen
    t0 = time.monotonic()
    r = await W.nap(1.0, gen)
    el = time.monotonic() - t0
    ok = (r is False) and 0.95 <= el <= 1.15
    rec("W3 nap 等满 remain", ok,
        "返回 %s，耗时 %.3fs（期望 ~1.00s，旧实现每 200ms 空转一次）" % (r, el))


async def a6():
    W.G.wake = asyncio.Event()
    gen = W.G.gen

    async def kicker():
        await asyncio.sleep(0.20)
        W.G.gen += 1
        W.G.wake.set()

    kt = asyncio.create_task(kicker())
    t0 = time.monotonic()
    r = await W.nap(5.0, gen)
    el = time.monotonic() - t0
    await kt
    ok = (r is True) and el < 1.0
    rec("W3 nap 被切换场景立即唤醒", ok,
        "返回 %s，%.0fms 内唤醒（挂起时长 5s）" % (r, el * 1000))


# ---------------------------------------------------------------- A7~A9 W6
def a7():
    d = tempfile.mkdtemp(prefix="wn_log_")
    p = os.path.join(d, "t.log")
    s = W._LogSink(p)
    for i in range(63):
        s.write("line %d" % i)
    rec("W6 日志攒批（63 条不落盘）", not os.path.exists(p),
        "文件存在=%s（期望 False）" % os.path.exists(p))
    s.write("line 63")
    rec("W6 第 64 条触发落盘", os.path.exists(p),
        "文件存在=%s" % os.path.exists(p))
    s.flush()
    n = sum(1 for _ in open(p, encoding="utf-8"))
    rec("W6 flush 后条数完整", n == 64, "落盘 %d 条（期望 64）" % n)


def a8():
    d = tempfile.mkdtemp(prefix="wn_log_")
    p = os.path.join(d, "t.log")
    old = os.path.join(d, "t.old")
    with open(p, "w", encoding="utf-8") as f:
        f.write("x" * (5 * 1024 * 1024))       # 预写 5MB，超过 MAX_BYTES=4MB
    s = W._LogSink(p)
    s.write("rotate me")
    s.flush()
    rec("W6 超 4MB 自动轮转出 .old", os.path.exists(old),
        ".old 存在=%s，主文件 %d B（期望已被替换清空）"
        % (os.path.exists(old), os.path.getsize(p)))


def a9():
    d = tempfile.mkdtemp(prefix="wn_log_")
    p = os.path.join(d, "t.log")
    W._LOG_SINK = W._LogSink(p)
    try:
        W._LOG_SINK.write("buffered line")
        rec("W6 flush_logs 前未落盘", not os.path.exists(p), "")
        W.flush_logs()
        ok = os.path.exists(p) and "buffered line" in open(p, encoding="utf-8").read()
        rec("W6 flush_logs 把缓冲写下去", ok, "")
    finally:
        W._LOG_SINK = None


async def main():
    a1()
    await a2()
    await a3()
    await a4()
    await a4b()
    await a5()
    await a6()
    a7()
    a8()
    a9()
    bad = [r for r in RESULTS if not r[1]]
    print("\n===== W1~W6 进程内单测：%d/%d 通过 =====" % (len(RESULTS) - len(bad), len(RESULTS)))
    for n, _ok, d in bad:
        print("FAILED: %s | %s" % (n, d))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
