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


def test_bucket_charges_single_byte(monkeypatch):
    """尾字节也要计费，且等待时长按 `remain / rate` 算。

    两个变异体靠这条用例杀死：
      * `while remain > 0` 的 `0` 改成 `1` → 1 字节直接放行，不等待；
      * `wait_s = remain / rate` 的 `/` 改成 `*` → 1 字节等 0.25s（高 256 倍）。
    现有用例都是 64KB 级块，看不出尾字节不扣令牌、也算不出等待公式错了。
    """
    clock = [1000.0]
    calls = []

    async def fake_sleep(s):
        calls.append(s)
        clock[0] += s

    monkeypatch.setattr(W.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(W.asyncio, "sleep", fake_sleep)

    async def go():
        bucket = W.Bucket(8)               # 1024 B/s，桶容量 256B
        bucket.tokens = 0.0
        bucket.last = clock[0]
        await bucket.consume(1)

    run_async(go())
    assert calls, "1 字节没有等待令牌，说明尾字节被白送"
    assert calls[0] == pytest.approx(1 / 1024.0, rel=0.05), \
        "等待时长不是 remain/rate：%.6fs" % calls[0]


def test_bucket_exact_fit_returns_without_sleeping(monkeypatch):
    """令牌刚好扣完时立即返回，不能再睡一个最小间隔。

    `if remain <= 0: return` 的 `<=` 改成 `<` 时，remain 归零不返回，会掉到
    `wait_s = 0` 被夹成 0.001s 再睡一次 —— 「令牌够用」的写全部多等 1ms。
    这是最常见路径（大块数据里每一次 take == remain），不是边角。
    """
    calls = []

    async def fake_sleep(s):
        calls.append(s)

    monkeypatch.setattr(W.asyncio, "sleep", fake_sleep)

    async def go():
        bucket = W.Bucket(8)               # 1024 B/s
        bucket.tokens = 2.0
        bucket.last = W.time.monotonic()
        await bucket.consume(2)

    run_async(go())
    assert calls == [], "令牌刚好够用仍睡了 %r" % (calls,)


def test_bucket_last_byte_must_wait(monkeypatch):
    """差 1 字节也必须等：扣到 1 字节就返回等于白送最后一个字节。

    `if remain <= 0` 的 `0` 改成 `1` 时，上一条用例（remain 归零）不经过这里，
    单字节用例（take == 0）也不经过 —— 只有「take 用尽令牌、仍剩 1 字节」
    这一格能把它区分出来。
    """
    clock = [1000.0]
    calls = []

    async def fake_sleep(s):
        calls.append(s)
        clock[0] += s

    monkeypatch.setattr(W.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(W.asyncio, "sleep", fake_sleep)

    async def go():
        bucket = W.Bucket(8)               # 1024 B/s
        bucket.tokens = 2.0
        bucket.last = clock[0]
        await bucket.consume(3)

    run_async(go())
    assert calls, "差 1 字节却直接放行"
    assert calls[0] == pytest.approx(1 / 1024.0, rel=0.05), \
        "补最后 1 字节的等待时长不是 1/rate：%.6fs" % calls[0]


def test_burst_credit_is_time_based_not_byte_based():
    """突发额度是「一段时间的配额」，不是「一批固定字节」。

    这条用例为什么存在：2026-11-14 的复核报告把 1MB 短样本超发 +25~29% 归因为
    「固定 0.42 MB 免费额度」，并据此提出按 `ratio = 1 + B/mb` 修正 —— 该模型只在
    单一速率下成立，换个速率就错。实测（固定 1MB、只变速率）：

        2000 / 4000 / 8000 / 16000 kbps → B = 0.034 / 0.092 / 0.184 / 0.372 MB

    B 随速率线性增长、B/rate 恒定 —— 因为 cap() = rate × BURST_SEC，空闲攒满后放行的
    是「0.25 秒的流量」。写成固定字节额度（cap 与 rate 脱钩）会让下面第二条断言失败。
    """
    b8, b16 = W.Bucket(8000), W.Bucket(16000)
    assert b8.cap() == pytest.approx(8000 * W.BITS_PER_KBPS / 8.0 * W.Bucket.BURST_SEC)
    assert b16.cap() == pytest.approx(b8.cap() * 2), "容量没随速率翻倍，说明不是时间额度"
    # 折算成秒必须与速率无关，且等于 BURST_SEC
    for b in (b8, b16):
        assert b.cap() / b.rate == pytest.approx(W.Bucket.BURST_SEC)


def test_burst_sec_is_a_quarter_second():
    """BURST_SEC = 0.25s 是实测决策，不是随手写的常数。

    1.0s 时 1MB@4000kbps 冷测超发 1.463×，对齐到 0.25s 后为 1.014×
    （真机 A/B，见 docs/precision.md §4.6）。这里写死数值：改容量必须有人来
    解释，并同步 Android 的 Pipe.Bucket.BURST_SEC —— 只改一端会被
    tests/_t_spec_parity.py 拦住。
    """
    assert W.Bucket.BURST_SEC == 0.25


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


def test_set_profile_skips_stale_finalization_on_concurrent_switch():
    """C6：await flap_task 期间又被切了一次场景，旧调用不许用旧 prof 收尾。"""
    async def go():
        release = asyncio.Event()

        async def stubborn():
            # 吞掉 cancel，保证两个 set_profile 都停在 await G.flap_task 上
            while not release.is_set():
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    continue

        prof_a = W.default_profile("A")
        prof_a.update({"flap_sec": 1, "flap_blackout_sec": 1, "down_kbps": 100})
        prof_b = W.default_profile("B")
        prof_b.update({"down_kbps": 200})
        W.G.flap_task = asyncio.create_task(stubborn())
        old = asyncio.create_task(W.set_profile(prof_a, "a"))
        await asyncio.sleep(0.03)             # 旧调用已进入 await
        new = asyncio.create_task(W.set_profile(prof_b, "b"))
        await asyncio.sleep(0.03)             # 新调用也进入 await
        release.set()
        await asyncio.gather(old, new)
        try:
            assert W.G.profile["name"] == "B"
            assert W.G.preset_key == "b"
            assert W.G.flap_task is None, "旧调用拿 A 的 flap_sec 覆盖了新场景"
        finally:
            if W.G.flap_task is not None:
                W.G.flap_task.cancel()

    run_async(go())


def test_stale_set_profile_does_not_wipe_newer_flap_task():
    """C1：旧调用最后恢复时，不许抹掉新调用刚建好的 flap_task。"""
    async def go():
        profile_bak = dict(W.G.profile)
        gen_bak = W.G.profile_gen
        flap_bak = W.G.flap_task
        gate_bak = W.G.gate_open
        try:
            ev_a, ev_b = asyncio.Event(), asyncio.Event()

            async def waiter(ev):
                while not ev.is_set():
                    try:
                        await ev.wait()
                    except asyncio.CancelledError:
                        continue

            # T0 = A 等待的旧 flap；T1 = 新调用接手后 B 等待的句柄。
            # 手动把 G.flap_task 换成 T1，才能让 B 先于 A 收尾（旧代数反而最后恢复）。
            t0 = asyncio.create_task(waiter(ev_a))
            t1 = asyncio.create_task(waiter(ev_b))
            W.G.flap_task = t0
            prof_a = W.default_profile("A")
            prof_a.update({"flap_sec": 1, "flap_blackout_sec": 1})
            prof_b = W.default_profile("B")
            prof_b.update({"flap_sec": 3, "flap_blackout_sec": 1})
            old = asyncio.create_task(W.set_profile(prof_a, "a"))
            await asyncio.sleep(0)
            W.G.flap_task = t1
            new = asyncio.create_task(W.set_profile(prof_b, "b"))
            await asyncio.sleep(0)

            ev_b.set()
            await new
            made = W.G.flap_task
            assert made is not None and made is not t1

            ev_a.set()
            await old
            assert W.G.flap_task is made, "旧调用把新调用的 flap_task 抹成了 None"

            for t in (t0, t1, made):
                t.cancel()
            await asyncio.gather(t0, t1, made, return_exceptions=True)
        finally:
            W.G.profile.clear()
            W.G.profile.update(profile_bak)
            W.G.profile_gen = gen_bak
            W.G.flap_task = flap_bak
            W.G.gate_open = gate_bak

    run_async(go())


def test_flap_skips_unresolved_and_bypassed_contexts():
    """C2：进程名反查未返回的 ctx 不能被 flap 黑窗误掐。"""
    class FakeTransport:
        def __init__(self):
            self.aborted = False

        def abort(self):
            self.aborted = True

    class FakeWriter:
        def __init__(self):
            self.transport = FakeTransport()
            self.closed = False

        def close(self):
            self.closed = True

    async def go():
        profile_bak = dict(W.G.profile)
        gate_bak = W.G.gate_open
        ctx_bak = set(W.G.contexts)
        active_bak = set(W.G.active)
        try:
            prof = W.default_profile("flap")
            prof.update({"flap_sec": 0.05, "flap_blackout_sec": 0.05})
            W.G.profile.clear()
            W.G.profile.update(prof)
            W.G.gate_open = True
            W.G.contexts.clear()
            W.G.active.clear()

            pending = W.ConnCtx("pending")
            pending.bypass_pending = True
            bypassed = W.ConnCtx("bypassed")
            bypassed.bypass_pending = False
            bypassed.bypassed = True
            managed = W.ConnCtx("managed")
            managed.bypass_pending = False
            writers = {}
            for ctx in (pending, bypassed, managed):
                w = FakeWriter()
                ctx.register(w)
                writers[ctx.label] = w

            task = asyncio.create_task(W.flap_loop())
            await asyncio.sleep(0.14)          # 至少经历一次黑窗
            assert not writers["pending"].transport.aborted, "未判定的直通连接被误掐"
            assert not writers["bypassed"].transport.aborted, "已判定的直通连接被误掐"
            assert writers["managed"].transport.aborted, "被接管连接应被掐断"
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            W.G.profile.clear()
            W.G.profile.update(profile_bak)
            W.G.gate_open = gate_bak
            W.G.contexts.clear()
            W.G.contexts.update(ctx_bak)
            W.G.active.clear()
            W.G.active.update(active_bak)

    run_async(go())


def test_dev_cross_script_has_no_hardcoded_lan_ips():
    """S6：真机地址只从环境变量取，仓库里不留内网 IP。"""
    from pathlib import Path

    src = (Path(__file__).parent / "_t_dev_cross.py").read_text(encoding="utf-8")
    assert "192.168.1.49" not in src
    assert "172.16.3.82" not in src
    assert "WEAKNET_PHONE_IP" in src and "<PHONE_IP>" in src


# ---------------------------------------------------------------- 代理目标过滤

FORBIDDEN_TARGETS = [
    ("127.0.0.1", 443, True),          # 回环：绕过控制面 Host 校验
    ("127.0.0.1", 22, True),           # 回环任意端口都算
    ("localhost", 80, True),
    ("LOCALHOST", 80, True),
    ("[::1]", 8080, True),             # IPv6 字面量带方括号
    ("0.0.0.0", 80, True),
    ("::", 80, True),
    ("app.localhost", 80, True),       # 子域也是本机
    ("localhost.example.com", 443, False),   # 普通域名，不能误伤
    ("example.com", 8899, True),       # 控制口：任何主机都不转发
    ("192.168.1.10", 8900, True),      # 共享口
    ("example.com", 443, False),
]


@pytest.mark.parametrize("host,port,want", FORBIDDEN_TARGETS,
                         ids=["%s:%s" % (h, p) for h, p, _w in FORBIDDEN_TARGETS])
def test_target_forbidden(host, port, want):
    assert W._target_forbidden(host, port) is want


# ---------------------------------------------------------------- 请求头上限

class _FakeReader:
    """只实现 readline 的假 StreamReader：用来测读头上限，不建真连接。"""

    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def readline(self):
        return self.chunks.pop(0) if self.chunks else b""


def test_proxy_header_bytes_are_capped():
    """超过 16KB 必须停下：以前只靠 30s 超时，客户端可以一行行把内存顶上去。"""
    chunks = [b"X-Pad: " + b"a" * 200 + b"\r\n"] * 200
    assert run_async(W.read_proxy_headers(_FakeReader(chunks))) is None


def test_proxy_header_lines_are_capped():
    chunks = [b"X-N%d: 1\r\n" % i for i in range(W.MAX_HEADER_LINES + 1)]
    assert run_async(W.read_proxy_headers(_FakeReader(chunks))) is None


def test_proxy_headers_under_limit_are_returned():
    chunks = [b"Host: a.example\r\n", b"X: 1\r\n", b"\r\n"]
    got = run_async(W.read_proxy_headers(_FakeReader(chunks)))
    assert got == chunks[:2]
