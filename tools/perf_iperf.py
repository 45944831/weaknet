# -*- coding: utf-8 -*-
"""P0-3 外部真值：iperf3 标准化测量（Windows 侧直连 / 经 WeakNet 代理）。

为什么要这个脚本
----------------
项目里所有性能数字此前都来自「自写 HTTP 下载计时」，连客户端都在仓库里
（`tests/support.py:HttpReader`）。客户端一旦有 O(n^2) 拼接，测出来的就是
客户端的开销而不是产品数据面（2026-11-13 实测 5.8 vs 178.3 MiB/s）。
iperf3 是独立进程、独立计时、行业标准输出，能把「测量工具」与「被测对象」
彻底分开。`-J` 输出原始 JSON，便于归档与复算。

链路
----
    direct :  iperf3 -c 127.0.0.1:<srv>           ──────────► iperf3 -s
    weaknet:  iperf3 -c 127.0.0.1:<tunnel>  ─► CONNECT 隧道转发器
                                             ─► WeakNet 代理（进程内引擎）
                                             ─► iperf3 -s

iperf3 没有 HTTP CONNECT 代理选项，所以这里内置一个**只做字节透传**的
转发器：它不解析、不缓存、不计时，只负责把裸 TCP 流塞进 CONNECT 隧道。
引擎用 `W.serve()` 在独立线程里起（与 pytest 夹具同一条真实管道），
不启动弱网 GUI、不接管系统代理，测完不残留任何系统设置。

单位口径
--------
iperf3 的 `bits_per_second` 是 SI（1 kbit = 1000 bit）；本项目的 kbps 是
1024 bit/s（见 tests/perf/baseline.py:UNIT_CONVENTION）。脚本同时记录原始
bps 与换算后的 kbps(1024)，避免把 2.4% 的单位差当成产品偏差。

统计口径：取**接收端**（sum_received）
----------------------------------------
多流共享同一个瓶颈时，iperf3 客户端本地的发送计数（sum_sent）会系统性
落后于真正发出去并被对端收到的字节。2026-11-13 实测（8000 kbps、-P 8）：

    sum_sent     = 17,170,432 B = 6708 kbps   ← 客户端本地计数
    sum_received = 20,512,768 B = 8009 kbps   ← 服务端真正收到
    代理 bytes_up                = 8083 kbps   ← 代理侧自计

两个独立计数器都落在设定值上，只有客户端本地计数低 16%。用发送端当主口径
会凭空造出「限速低 36%」的假缺陷。详见 `_extract()` 的注释。

用法
----
    py -3.10-64 tools/perf_iperf.py --quick                 # 链路自检（2 次 × 3s）
    py -3.10-64 tools/perf_iperf.py                        # 正式：7 次 × 20s，全档
    py -3.10-64 tools/perf_iperf.py --curve                # 带宽偏差曲线（P2-1）
    py -3.10-64 tools/perf_iperf.py --scenarios direct_p1,weaknet_p1

iperf3.exe 的定位顺序：`--iperf3` > `WN_IPERF3` > PATH > 项目外
`../_tmp/iperf3/iperf3.22/iperf3.exe`。二进制不放进仓库（体积大、平台相关）。
"""
import argparse
import ctypes
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import weaknet as W                     # noqa: E402
from support import CtrlClient          # noqa: E402
from perf import baseline as B          # noqa: E402

# ctypes.wintypes 在非 Windows 上会抛异常；_SysLoad 只在 Windows 上生效，
# 其它平台让 load 采样返回 None，测量本身照跑（不因为"测不了负载"而罢工）。
try:
    import ctypes.wintypes              # noqa: F401
    _HAS_WINTYPES = True
except Exception:                       # pragma: no cover - 非 Windows
    _HAS_WINTYPES = False

DEFAULT_IPERF = ROOT.parent / "_tmp" / "iperf3" / "iperf3.22" / "iperf3.exe"


# ------------------------------------------------------------------ 引擎

class _Engine(threading.Thread):
    """独立事件循环里跑真实 W.serve()，不碰系统代理、不启 GUI。"""

    def __init__(self):
        super().__init__(daemon=True, name="iperf-engine")
        import asyncio
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.error = None
        self.proxy_port = 0
        self.ctrl_port = 0
        self._servers = ()

    def run(self):
        import asyncio
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._boot())
            self.ready.set()
            self.loop.run_forever()
        except BaseException as e:
            self.error = e
            self.ready.set()
        finally:
            self.loop.close()

    async def _boot(self):
        proxy, ctrl = await W.serve("127.0.0.1", 0, 0)
        self._servers = (proxy, ctrl)
        self.proxy_port = proxy.sockets[0].getsockname()[1]
        self.ctrl_port = ctrl.sockets[0].getsockname()[1]

    def stop(self):
        import asyncio
        if self.ready.is_set() and self.loop.is_running():
            async def _shutdown():
                for s in self._servers:
                    s.close()
                for s in self._servers:
                    await s.wait_closed()
                for w in list(W.G.active):
                    try:
                        w.close()
                    except Exception:
                        pass
                # 必须显式取消 DelayLine 投递协程：否则 loop 关掉后协程的
                # finally/_mark_dead 会在「无运行中的 loop」里炸出一片
                # RuntimeError 噪音，盖住真正的测量结果。
                me = asyncio.current_task()
                pending = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                await asyncio.sleep(0)
            try:
                asyncio.run_coroutine_threadsafe(_shutdown(), self.loop).result(timeout=8)
            except Exception:
                pass
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.join(timeout=10)


# ------------------------------------------------------- CONNECT 隧道转发器

class _Tunnel(threading.Thread):
    """把裸 TCP 流经 HTTP CONNECT 塞进 WeakNet 代理；纯字节转发，不计时。"""

    def __init__(self, proxy_port, target_port, relay_timeout=120):
        super().__init__(daemon=True, name="iperf-tunnel")
        self.proxy = ("127.0.0.1", proxy_port)
        self.target = ("127.0.0.1", target_port)
        self.relay_timeout = relay_timeout
        ls = socket.socket()
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind(("127.0.0.1", 0))
        ls.listen(64)
        self.port = ls.getsockname()[1]
        self.sock = ls
        self.stopped = threading.Event()
        self.workers = []

    def _relay(self, c, s, rest):
        """双向字节透传，**正确处理半关闭**。

        早期实现在一侧 recv 到 EOF 时直接对两个 socket 都 shutdown(SHUT_RDWR)。
        那会在 iperf3 每轮收尾把 WeakNet 延迟队列里还没投递的数据一并掐断，
        触发 ConnectionReset / `socket.send() raised exception.` 刷屏，并把
        `-P 8` 的部分流提前打断（实测中位 2253 kbps、512~5141 剧烈波动）。
        正确做法是只把 FIN 传给对端（shutdown SHUT_WR），另一个方向继续跑到
        EOF；relay_timeout 只作为兜底，防止对端既不发 FIN 也不关连接时泄漏线程。
        """
        peer = {c: s, s: c}
        try:
            if rest:
                c.sendall(rest)
            fds = [c, s]
            deadline = time.monotonic() + self.relay_timeout
            while fds and not self.stopped.is_set() and time.monotonic() < deadline:
                r, _, _ = select.select(fds, [], [], 1.0)
                for fd in r:
                    try:
                        data = fd.recv(65536)
                    except OSError:
                        data = b""
                    other = peer[fd]
                    if data:
                        try:
                            other.sendall(data)
                        except OSError:
                            return
                    else:
                        # 半关闭：只关对端的写方向，另一个方向仍可继续回数据
                        try:
                            other.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                        fds.remove(fd)
        finally:
            for x in (c, s):
                try:
                    x.close()
                except OSError:
                    pass

    def _handle(self, c):
        s = None
        try:
            s = socket.create_connection(self.proxy, timeout=10)
            req = ("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n\r\n"
                   % (self.target[0], self.target[1],
                      self.target[0], self.target[1]))
            s.sendall(req.encode("latin1"))
            buf = b""
            while b"\r\n\r\n" not in buf:
                part = s.recv(4096)
                if not part:
                    return
                buf += part
            head, _, rest = buf.partition(b"\r\n\r\n")
            if b" 200" not in head.split(b"\r\n")[0]:
                sys.stderr.write("CONNECT 失败：%r\n" % head[:120])
                return
            self._relay(c, s, rest)
            c = None
        except Exception as e:
            sys.stderr.write("tunnel: %r\n" % (e,))
        finally:
            if c is not None:
                c.close()
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass

    def run(self):
        while not self.stopped.is_set():
            try:
                c, _addr = self.sock.accept()
            except OSError:
                break
            t = threading.Thread(target=self._handle, args=(c,), daemon=True)
            t.start()
            self.workers.append(t)

    def stop(self):
        self.stopped.set()
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------ 测量

def _find_iperf(explicit):
    if explicit:
        return explicit
    env = os.environ.get("WN_IPERF3")
    if env and Path(env).exists():
        return env
    p = shutil.which("iperf3") or shutil.which("iperf3.exe")
    if p:
        return p
    if DEFAULT_IPERF.exists():
        return str(DEFAULT_IPERF)
    raise SystemExit("找不到 iperf3.exe。用 --iperf3 指定，或设置 WN_IPERF3。")


def _iperf_version(exe):
    """取 iperf3 的真实版本串，取不到才退回占位符。

    以前归档里硬写 "iperf3 3.22"：换一版 iperf3 之后归档仍然自称 3.22，
    等于让元数据说谎。iperf3 的统计口径在 3.x 内有过变化，版本必须自证。
    """
    try:
        p = subprocess.run([exe, "--version"], capture_output=True, timeout=10,
                           encoding="utf-8", errors="replace")
        line = (p.stdout or p.stderr or "").strip().splitlines()
        if line:
            return line[0].strip()
    except Exception:
        pass
    return "iperf3 (版本未知)"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ------------------------------------------------------ 采集环境自证（零依赖）

class _SysLoad:
    """采系统 CPU 忙闲与可用内存，纯 ctypes，不依赖 psutil。

    为什么测量时必须同时记负载
    --------------------------
    2026-11-13 有一次重测同时跑了 Java 编译与 pytest，`direct_p1` 从 16 M
    掉到 13.5 M。当时只能靠人回忆判定「这批作废」——因为归档里除了数字
    什么都没有，事后无从分辨是产品退化还是机器被占。

    把每轮的 CPU 忙率随数字一起落盘，异常轮就能被自动标出来。这是「采集
    数据的准确性」里唯一无法靠事后计算补上的维度：它必须在采集时发生。

    `GetSystemTimes` 的 kernel 时间**已包含 idle**，所以忙率是
    `(kernel + user - idle) / (kernel + user)`，不是 `user / (kernel + user)`。
    """

    class _FILETIME(ctypes.Structure):
        # DWORD == c_uint32，直接用基础类型可以避开 ctypes.wintypes 的平台依赖
        _fields_ = [("lo", ctypes.c_uint32),
                    ("hi", ctypes.c_uint32)]

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_uint32),
                    ("dwMemoryLoad", ctypes.c_uint32),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    @classmethod
    def cpu_times(cls):
        """(idle, kernel, user) 累计 100ns 计数；非 Windows / 调用失败返回 None。"""
        if not hasattr(ctypes, "windll"):
            return None
        try:
            f = cls._FILETIME
            i, k, u = f(), f(), f()
            if not ctypes.windll.kernel32.GetSystemTimes(
                    ctypes.byref(i), ctypes.byref(k), ctypes.byref(u)):
                return None
            val = lambda ft: (ft.hi << 32) | ft.lo      # noqa: E731
            return val(i), val(k), val(u)
        except Exception:
            return None

    @classmethod
    def busy_pct(cls, t0, t1):
        """两采样点之间的**平均** CPU 忙率（含所有核）。"""
        if not t0 or not t1:
            return None
        di = t1[0] - t0[0]
        dk = t1[1] - t0[1]
        du = t1[2] - t0[2]
        total = dk + du                    # kernel 已含 idle
        if total <= 0:
            return None
        busy = total - di
        return max(0.0, min(100.0, busy / float(total) * 100.0))

    @classmethod
    def mem_avail_mb(cls):
        if not hasattr(ctypes, "windll"):
            return None
        try:
            m = cls._MEMORYSTATUSEX()
            m.dwLength = ctypes.sizeof(cls._MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return None
            return round(m.ullAvailPhys / 1048576.0, 1)
        except Exception:
            return None


def _engine_bytes(api):
    """读引擎累计字节 (up, down)。取不到返回 None，绝不写 0。

    这是与 iperf3 **完全独立**的第三个计数器：iperf3 在两端各算一次，
    引擎在代理层自己数一次。三方对账能把「口径错」与「产品慢」彻底分开
    （2026-11-13 正是靠它证明了 P8 的 -36% 是客户端计数假象）。
    """
    try:
        r = api.get("/api/status")
        # CtrlClient 返回 namedtuple(status, body, headers)，body 才是 JSON；
        # 早期直接对返回值下标会静默拿到 None，对账字段全部变成 -1。
        body = getattr(r, "body", r)
        st = (body or {}).get("stats") or {}
        return int(st["up"]), int(st["down"])
    except Exception:
        return None


def _suspect_rounds(cpu_series, floor_pct=15.0, k=3.0, min_delta_pp=15.0):
    """标出「明显被外部负载污染」的轮次下标。

    判据（三条同时成立）：该轮 CPU 忙率
      * > 同批中位 + k×MAD（相对离群）
      * > floor_pct（绝对下限，避免空闲批次的 1% 抖动被放大）
      * 比中位高出至少 min_delta_pp 个百分点

    第三条是必要的：回环高速档（`direct_p8`）本身就把 CPU 打到 60%+，
    这时 MAD 很小，只看「中位 + 3×MAD」会把 65% 的正常轮也标成异常。
    要求「绝对增量足够大」才能区分「本来就很忙」与「突然被别的进程抢了」。

    返回 (下标列表, 该批 cpu 中位, MAD)。数据不足时返回 ([], 中位或 None, None)。
    """
    xs = [x for x in cpu_series if x is not None]
    if len(xs) < 3:
        return [], (xs[0] if xs else None), None
    med = B._median(xs)
    mad = B._median([abs(x - med) for x in xs])
    out = []
    for i, x in enumerate(cpu_series):
        if x is None or x <= floor_pct or (x - med) < min_delta_pp:
            continue
        # MAD 为 0 表示整批几乎一致：此时只用「绝对下限 + 增量」两条，避免除零式放大
        if mad and x <= med + k * mad:
            continue
        out.append(i)
    return out, med, mad


def _quality(st, rounds, via, cv_limit=5.0, bias_pct=2.0):
    """把「这批数字能不能拿来下结论」写成结构化结论。

    以前这一步靠人看数字猜：中位数好看就当达标，离散大就"下次再测"。
    判定标准不落盘，等于每批数据都要重新主观解释一遍。这里把它固化：

    * `usable` —— cv 超限、或存在**既被污染又真的偏了**的轮次时为 False
    * `suspect_rounds` —— 哪几轮 CPU 明显被外部抢占（1-based 轮号）
    * `suspect_biased_rounds` —— 上述轮次里，**吞吐也真的偏离中位 > bias_pct** 的
    * `engine_vs_received_pct` —— 引擎自计 vs iperf3 接收端的相对差，
      两个独立计数器应当吻合；差得多说明测量链路串了或口径错了

    为什么「环境异常」不直接等于「整批作废」
    --------------------------------------
    实测 2026-11-14：`weaknet_p8` 第 1 轮 CPU 47.6%（其余 20~27%，是上一档刚结束
    的余温），但该轮吞吐 8,032 kbps 就在中位 8,007 旁边，整批 cv 仅 0.73%。
    若把这种轮当成"污染即作废"，会把一批完全干净的数据判死——判据一旦频繁误报，
    读的人就会整体忽略这个字段，护栏等于没有。

    正确判据是**污染有没有真的改变结果**：CPU 异常 **且** 该轮吞吐偏离中位
    超过 `bias_pct` 才算数。只异常不偏离的轮仍列在 `suspect_rounds` 里供追溯。
    """
    ok, why = B.is_unstable(st, cv_limit)
    cpu = [r.get("cpu_busy_pct") for r in rounds]
    sus, cpu_med, cpu_mad = _suspect_rounds(cpu)
    med = st.get("median")
    biased = []
    for i in sus:
        v = rounds[i].get("kbps_1024")
        if v and med and abs(v - med) / float(med) * 100.0 > bias_pct:
            biased.append(i)
    eng = [r.get("engine_kbps") for r in rounds if r.get("engine_kbps") is not None]
    eng_med = B._median(eng) if eng else None
    eng_gap = None
    if via and eng_med is not None and st.get("median"):
        eng_gap = (eng_med - st["median"]) / st["median"] * 100.0
    q = {
        "usable": bool(ok and not biased),
        "reason": why if ok else why,
        "cv_pct": None if st.get("cv_pct") is None else round(st["cv_pct"], 2),
        "cv_limit_pct": cv_limit,
        "cpu_busy_median_pct": None if cpu_med is None else round(cpu_med, 1),
        "cpu_busy_mad": None if cpu_mad is None else round(cpu_mad, 2),
        "suspect_rounds": [i + 1 for i in sus],
        "suspect_biased_rounds": [i + 1 for i in biased],
        "bias_pct": bias_pct,
        "engine_median_kbps": None if eng_med is None else round(eng_med, 1),
        "engine_vs_received_pct": None if eng_gap is None else round(eng_gap, 1),
        # 两套计数器的覆盖区间不同（引擎含 CONNECT 握手与重传、按墙钟算；
        # iperf3 按剔除预热后的有效窗口算），所以正常值应落在 0~+8%，
        # 略微为正才是对的。负值或超过 +12% 说明链路串了或口径被改坏。
        "engine_crosscheck_ok": (None if eng_gap is None
                                 else (-5.0 <= eng_gap <= 12.0)),
        "note": ("engine_* 是代理层自计，与 iperf3 接收端互相独立；正常应比接收端"
                 "略高 0~8%（含握手与重传、按墙钟计），显著偏离说明链路或口径有问题。"
                 "直连档引擎未参与，读数应≈0，明显非 0 说明测量链路串了"),
    }
    if biased:
        q["reason"] = "%s；且第 %s 轮 CPU 被外部抢占、吞吐同时偏离中位 >%.0f%%，该批不可用" % (
            why, "、".join(str(i + 1) for i in biased), bias_pct)
    elif sus:
        q["reason"] = "%s；第 %s 轮 CPU 偏高但吞吐仍在中位 ±%.0f%% 内，不影响结论" % (
            why, "、".join(str(i + 1) for i in sus), bias_pct)
    return q


def _iperf_cmd(port, par, time_s, omit, reverse=False, udp=False, kbps=0,
               block_kb=None, affinity=None):
    """拼 iperf3 命令行（纯函数，便于单测）。

    `-l` 与 `-A` 是官方 Known Issue 的规避参数（#125 高丢包区间失真、
    #55 高 UDP 假丢包）。默认不传，只有调用方明确要求时才出现——
    不能让「默认值」悄悄改变历史口径。
    """
    a = ["-c", "127.0.0.1", "-p", str(port), "-J",
         "-t", str(time_s), "-O", str(omit), "-P", str(par)]
    if block_kb:
        a += ["-l", str(int(block_kb) * 1024)]
    if affinity is not None and affinity >= 0:
        a += ["-A", str(affinity)]
    if reverse:
        a += ["-R"]
    if udp:
        a += ["-u", "-b", "%dK" % kbps]
    return a


def _run_iperf(exe, args, timeout):
    """返回解析后的 JSON；失败返回 {"error": ...}，不让单轮噪声中断整批。"""
    cmd = [exe] + args
    try:
        p = subprocess.run(cmd, cwd=os.path.dirname(exe), capture_output=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    out = (p.stdout or "").strip()
    i = out.find("{")
    if p.returncode != 0 or i < 0:
        return {"error": (p.stderr or out or "exit %d" % p.returncode)[:300]}
    try:
        return json.loads(out[i:])
    except Exception as e:
        return {"error": "bad json: %s" % e}


def _extract(d):
    """从 iperf3 -J 抽取关注指标。

    口径：**以接收端 sum_received 为准，发送端 sum_sent 只作参考。**

    为什么不能用发送端
    ------------------
    `sum_sent` 是 iperf3 **客户端本地**的发送计数。多流共享一个瓶颈时，
    客户端的 8 条流几乎全程阻塞在 send() 里，本地计数会系统性落后于
    真正发出去（并被对端收到）的字节。2026-11-13 实测（限速 8000 kbps、
    -P 8、20s）：

        sum_sent     = 17,170,432 B = 6708 kbps
        sum_received = 20,512,768 B = 8009 kbps   ← 服务端真正收到的
        引擎 bytes_up              = 8083 kbps     ← 代理侧自计

    两个互相独立的计数器（对端接收、代理自计）都落在 8000 kbps（= 设定值），
    只有客户端本地发送计数低 16%。个别流更极端：sender=0 而 receiver=2.7 MB。
    这是 iperf3 客户端侧计数假象，不是产品限速不准。

    接收端计数是「真正抵达链路终点」的字节，对弱网链路来说才是有效吞吐；
    发送端数字低只说明客户端侧统计滞后，拿它当限速精度结论会凭空造出
    36% 的「产品缺陷」。

    双向都记录：sender_* 保留下来，是为了在归档里能自证口径，而不是只留结论。
    """
    if "error" in d:
        return None
    end = d.get("end") or {}
    ss = end.get("sum_sent") or end.get("sum") or {}
    su = end.get("sum_received") or {}
    sm = end.get("sum") or {}
    snd_bps = ss.get("bits_per_second")
    rcv_bps = su.get("bits_per_second")
    bps = rcv_bps or snd_bps
    if bps is None:
        return None
    snd_b = ss.get("bytes")
    rcv_b = su.get("bytes")
    gap = None
    if isinstance(snd_b, (int, float)) and isinstance(rcv_b, (int, float)) and rcv_b:
        gap = (snd_b - rcv_b) / float(rcv_b) * 100.0
    return {"bps": bps,
            "kbps_1024": bps / 1024.0,
            "sender_bps": snd_bps,
            "sender_kbps_1024": (snd_bps / 1024.0) if snd_bps else None,
            "sender_bytes": snd_b,
            "receiver_bps": rcv_bps,
            "receiver_kbps_1024": (rcv_bps / 1024.0) if rcv_bps else None,
            "receiver_bytes": rcv_b,
            # 发送端相对接收端的偏差：负值=客户端侧少计（多流共享瓶颈时的正常假象）
            "sender_receiver_gap_pct": gap,
            "jitter_ms": sm.get("jitter_ms"),
            "lost_percent": sm.get("lost_percent"),
            "retransmits": ss.get("retransmits"),
            # iperf3 自报的有效传输窗口（秒）。引擎侧对账必须用同一个分母：
            # 用墙钟会把 iperf3 进程启动/握手的时间也算进去，凭空压低引擎吞吐
            # （实测 8s 的轮次墙钟 10.2s，分母大 27%）。
            "window_s": (su.get("seconds") or sm.get("seconds") or ss.get("seconds")),
            # -P 8 时用来判断「是不是只有部分流真正跑完」：流数不足或
            # 各流速率悬殊都说明连接在中途被掐断，而不是产品限速精度问题。
            "streams": len(end.get("streams") or []),
            "stream_bps": [x.get("sender", {}).get("bits_per_second")
                           for x in (end.get("streams") or [])],
            "stream_recv_bps": [x.get("receiver", {}).get("bits_per_second")
                                for x in (end.get("streams") or [])],
            "cpu_utilization_percent": (end.get("cpu_utilization_percent") or {}).get("host_total")}


SCENARIOS = {
    # key: (是否经代理, 并发数, UDP, 设定带宽 kbps)
    "direct_p1":        (False, 1, False, 0),
    "direct_p4":        (False, 4, False, 0),
    "direct_p8":        (False, 8, False, 0),
    "direct_udp":       (False, 1, True, 10000),
    "weaknet_p1":       (True, 1, False, 8000),
    "weaknet_p4":       (True, 4, False, 8000),
    "weaknet_p8":       (True, 8, False, 8000),
    "weaknet_p1_rev":   (True, 1, False, 8000),     # -R：反向（下行）也过闸
}

# UDP 数据报不会走 HTTP CONNECT 隧道：隧道只转发裸 TCP 字节流，而 iperf3
# 的 -u 是在控制连接之外另发 UDP 流。这不是脚本缺陷，是产品架构边界 ——
# Windows 侧是「应用层 TCP 代理」，UDP 承载只在 Android VpnService 的 TUN 里
# （见 docs/precision.md §1/§3）。因此 UDP 抖动/丢包真值只能做直连对照，
# 不能声称测到了「经 WeakNet 的 UDP」。自检时实测 weaknet_udp 两轮全 timeout。
UDP_VIA_PROXY = "不支持：Windows 侧为 TCP 应用层代理，UDP 数据报不经 CONNECT 隧道"

CURVE_KBPS = [500, 1000, 4000, 8000, 20000, 50000]

# P2-1 要求偏差曲线覆盖「延迟 / 丢包 / 带宽」三个维度，而不只是带宽。
# 下面两张表分别是「高延迟 + 低带宽」组合与丢包（停顿）组合。
# 每项 = (标签, 剖面增量, 设定带宽 kbps)；剖面增量会并入 up/down 双向。
CURVE_LATENCY = [
    ("lat200_kbps500",   {"latency_ms": 200}, 500),
    ("lat200_kbps1000",  {"latency_ms": 200}, 1000),
    ("lat200_kbps4000",  {"latency_ms": 200}, 4000),
    ("lat400_kbps1000",  {"latency_ms": 400}, 1000),
]
CURVE_LOSS = [
    ("loss5_stall100_kbps8000",  {"loss_pct": 5,  "stall_ms": 100}, 8000),
    ("loss10_stall200_kbps8000", {"loss_pct": 10, "stall_ms": 200}, 8000),
    ("loss5_stall100_kbps1000",  {"loss_pct": 5,  "stall_ms": 100}, 1000),
]


def _profile(kbps, extra=None):
    """上下行都设同一档：iperf3 的控制/数据两个方向都要过闸，避免只测到半个链路。

    extra 用来叠加延迟 / 抖动 / 丢包（停顿）参数，字段名与 /api/profile 一致。
    """
    p = {"up_kbps": kbps, "down_kbps": kbps}
    for k, v in (extra or {}).items():
        if k == "latency_ms":
            p["latency_up_ms"] = v
            p["latency_down_ms"] = v
        else:
            p[k] = v
    return p


def main():
    ap = argparse.ArgumentParser(description="WeakNet 外部真值测量（iperf3）")
    ap.add_argument("--iperf3", default="")
    ap.add_argument("--repeats", type=int, default=7)
    ap.add_argument("--time", type=int, default=20, help="每轮 -t 秒数")
    ap.add_argument("--omit", type=int, default=3, help="-O 忽略慢启动秒数")
    ap.add_argument("--gap", type=float, default=1.5,
                    help="轮间冷却秒数：让 TIME_WAIT / 内核缓冲 / 令牌桶余量散掉，"
                         "降低样本自相关（0 = 背靠背，不建议）")
    ap.add_argument("--quick", action="store_true", help="链路自检：2 次 × 3s")
    ap.add_argument("--curve", action="store_true", help="带宽偏差曲线（P2-1）")
    ap.add_argument("--curve-latency", action="store_true",
                    help="高延迟 + 低带宽组合偏差曲线（P2-1）")
    ap.add_argument("--curve-loss", action="store_true",
                    help="丢包（停顿）组合偏差曲线（P2-1）")
    ap.add_argument("--scenarios", default="", help="逗号分隔，覆盖默认档位")
    ap.add_argument("--json", default="", help="结果归档路径")
    ap.add_argument("--block-kb", type=int, default=0,
                    help="iperf3 -l 的块大小（KB，0=不指定）。高丢包档官方建议 4："
                         "Known Issue #125 下 sender write 会阻塞到整块发完，"
                         "区间报告时长会剧烈波动（iperf.fr/issues）")
    ap.add_argument("--affinity", type=int, default=-1,
                    help="iperf3 -A 绑定的 CPU 核号（-1=不指定）。高 UDP 速率下"
                         "接收端可能恒定报 ~20%% 假丢包（Known Issue #55），"
                         "绑核可缓解；绑定后归档里会记下来供横向比较")
    args = ap.parse_args()

    if args.quick:
        args.repeats, args.time, args.omit = 2, 3, 1
        args.gap = min(args.gap, 0.3)
    exe = _find_iperf(args.iperf3)
    print("iperf3: %s" % exe)

    srv_port = _free_port()
    srv = subprocess.Popen([exe, "-s", "-p", str(srv_port)],
                           cwd=os.path.dirname(exe),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    eng = _Engine()
    eng.start()
    if not eng.ready.wait(20) or eng.error is not None:
        srv.terminate()
        raise SystemExit("引擎启动失败：%r" % (eng.error,))
    api = CtrlClient("127.0.0.1", eng.ctrl_port)
    tun = _Tunnel(eng.proxy_port, srv_port, relay_timeout=args.time + 40)
    tun.start()

    out = {
        # 顶层 schema 与 context.schema 必须同源：以前这里硬写 1、context 写 2，
        # 同一份归档里两个 schema 号打架，读的人无法判断该按哪套字段解释。
        "schema": B.SCHEMA,
        "tool": _iperf_version(exe),
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "context": B.context(),
        # 测量参数必须随归档走：-l / -A 会改变读数，换参数后的批次不能与
        # 历史默认参数批次直接横比（iperf 官方 Known Issue #125/#55）
        "iperf_args": {"block_kb": args.block_kb or None,
                       "cpu_affinity": args.affinity if args.affinity >= 0 else None,
                       "time": args.time, "omit": args.omit,
                       "repeats": args.repeats, "gap": args.gap},
        # 采集开始时的机器状态：数字要能和"当时机器什么样"一起解释
        "env": {"cpu_count": os.cpu_count(),
                "mem_avail_mb": _SysLoad.mem_avail_mb(),
                "gap_s": args.gap,
                "sampling": "每轮记录 CPU 忙率（GetSystemTimes）与引擎累计字节，"
                            "用于事后判定该轮是否被外部负载污染"},
        "unit_convention": {
            "iperf3_bps": "SI，1 kbit = 1000 bit",
            "weaknet_kbps": "1 kbps = 1024 bit/s",
            "kbps_1024": "由 bps/1024 换算，与产品口径一致",
            "primary_metric": "sum_received（接收端累计字节）：真正抵达链路终点的量",
            "secondary_metric": "sum_sent（iperf3 客户端本地发送计数）：多流共享瓶颈时"
                                "会系统性少计，仅作口径对照，不用于限速精度结论",
        },
        "setup": {"iperf_server_port": srv_port, "tunnel_port": tun.port,
                  "engine_proxy_port": eng.proxy_port,
                  "repeats": args.repeats, "time_s": args.time, "omit_s": args.omit},
        "limitations": {"udp_via_proxy": UDP_VIA_PROXY,
                        "android_vpn": "Android 侧 VPN on/off 需设备端 iperf3 客户端，"
                                       "本次设备未安装，改由真机 HTTP 吞吐脚本覆盖"},
        "scenarios": {}, "curve": {}, "curve_latency": {}, "curve_loss": {},
    }

    def one(name, via, par, udp, kbps, reverse=False, budget_kbps=None,
            profile_extra=None, block_kb=None):
        if via and udp:
            print("  [%s] 跳过：%s" % (name, UDP_VIA_PROXY))
            return {"skipped": UDP_VIA_PROXY, "n": 0, "errors": []}
        if via:
            api.post("/api/profile", _profile(kbps, profile_extra))
            port = tun.port
        else:
            api.get("/api/stop")            # 直连档：关掉限速（与产品语义一致）
            port = srv_port
        a = ["-c", "127.0.0.1", "-p", str(port), "-J",
             "-t", str(args.time), "-O", str(args.omit), "-P", str(par)]
        # 生效块大小：显式 --block-kb 优先，其次调用方按场景传入。
        # 不指定时保持 iperf3 默认，归档里记 null（而不是假装用了 4K）。
        eff_block = block_kb if block_kb else (args.block_kb or None)
        a = _iperf_cmd(port, par, args.time, args.omit, reverse=reverse,
                       udp=udp, kbps=kbps, block_kb=eff_block,
                       affinity=args.affinity)
        samples, errors, rounds = [], [], []
        for i in range(args.repeats):
            if i and args.gap > 0:
                # 轮间冷却：上一轮的 TIME_WAIT、内核收发缓冲、令牌桶余量都会
                # 带进下一轮。连续背靠背跑会让样本自相关，中位数的置信区间
                # 被人为压窄（看起来"很稳"，其实是同一个状态重复测了 N 次）。
                time.sleep(args.gap)
            cpu0 = _SysLoad.cpu_times()
            mem0 = _SysLoad.mem_avail_mb()
            eb0 = _engine_bytes(api)
            t0 = time.monotonic()
            d = _run_iperf(exe, a, args.time + 25)
            elapsed = time.monotonic() - t0
            cpu1 = _SysLoad.cpu_times()
            eb1 = _engine_bytes(api)
            v = _extract(d)
            cpu_busy = _SysLoad.busy_pct(cpu0, cpu1)
            # 引擎自计吞吐：与 iperf3 完全独立的第三个计数器。
            # 正向（client→server）走 up，-R 反向走 down；直连档引擎未参与，
            # 读数应≈0，正好当"引擎确实没插手"的阴性对照。
            #
            # 分母必须用**墙钟**：引擎字节增量覆盖的是整个进程生命周期（含
            # `-O` 预热段），而 iperf3 的 `seconds` 是剔除预热后的有效窗口。
            # 拿 6s 的窗口去除非 10s 的字节会凭空多算 70%（2026-11-13 实测
            # 8s/omit2 档：引擎 10470 vs 真值 7936）。两套计数器的覆盖区间
            # 不同，这一点无法靠"换个分母"消除，只能在结论里注明。
            eng_kbps = None
            if eb0 and eb1 and elapsed > 0:
                idx = 1 if reverse else 0
                delta = eb1[idx] - eb0[idx]
                if delta >= 0:
                    eng_kbps = delta * 8.0 / 1024.0 / elapsed
            win = (v or {}).get("window_s")
            rd = {"i": i + 1, "elapsed_s": round(elapsed, 3),
                  "window_s": None if win is None else round(float(win), 3),
                  "cpu_busy_pct": None if cpu_busy is None else round(cpu_busy, 1),
                  "mem_avail_mb": mem0,
                  "engine_kbps": None if eng_kbps is None else round(eng_kbps, 1)}
            if v:
                samples.append(v)
                rd.update({"kbps_1024": round(v["kbps_1024"], 1),
                           "sender_kbps_1024": (None if v.get("sender_kbps_1024") is None
                                                else round(v["sender_kbps_1024"], 1)),
                           "streams": v["streams"]})
                print("  [%s] %d/%d %.0f kbps(1024) 流=%d ｜ 发送端 %.0f ｜ "
                      "引擎 %.0f ｜ CPU %.0f%%"
                      % (name, i + 1, args.repeats, v["kbps_1024"], v["streams"],
                         v.get("sender_kbps_1024") or -1,
                         # 不能用 `or -1`：直连档引擎读数合法为 0.0，
                         # 0.0 是 falsy，会被误显示成 -1（采集失败的样子）
                         -1 if eng_kbps is None else eng_kbps,
                         cpu_busy if cpu_busy is not None else -1))
            else:
                errors.append(d.get("error", "no end.sum"))
                rd["error"] = errors[-1]
                print("  [%s] %d/%d 失败：%s" % (name, i + 1, args.repeats,
                                                errors[-1]))
            rounds.append(rd)
        st = B.stats([x["kbps_1024"] for x in samples])
        jit = B.stats([x["jitter_ms"] for x in samples if x["jitter_ms"] is not None])
        loss = B.stats([x["lost_percent"] for x in samples if x["lost_percent"] is not None])
        snd = B.stats([x["sender_kbps_1024"] for x in samples
                       if x.get("sender_kbps_1024") is not None])
        gap = B.stats([x["sender_receiver_gap_pct"] for x in samples
                       if x.get("sender_receiver_gap_pct") is not None])
        res = {"via_weaknet": via, "parallel": par, "udp": udp, "reverse": reverse,
               "set_kbps": kbps, "profile_extra": profile_extra or {},
               # 测量参数自证：换了 -l / -A 的批次不能和历史默认参数批次直接横比，
               # 所以把「实际生效值」写进每条结果，而不是只写在命令行里
               "iperf_args": {"block_bytes": (int(eff_block) * 1024) if eff_block else None,
                              "cpu_affinity": args.affinity if args.affinity >= 0 else None},
               # 主口径 = 接收端（真正抵达链路终点的字节）
               "median_kbps_1024": st["median"],
               "p95_kbps_1024": st["p95"], "min_kbps_1024": st["min"],
               "max_kbps_1024": st["max"], "n": st["n"],
               # 离散度：只有中位数时，"稳定 7946" 与 "双峰 2459/7946" 长得一样
               "stdev_kbps_1024": st["stdev"], "cv_pct": st["cv_pct"],
               "mad_kbps_1024": st["mad"], "iqr_kbps_1024": st["iqr"],
               "p95_is_extreme": st["p95_is_extreme"],
               # 发送端（iperf3 客户端本地计数）并列留存，供口径自证
               "median_sender_kbps_1024": snd["median"],
               "median_sender_receiver_gap_pct": gap["median"],
               "median_jitter_ms": jit["median"], "median_lost_percent": loss["median"],
               "streams_per_round": [x["streams"] for x in samples],
               "rounds": rounds,
               "errors": errors}
        # 采集质量自证：这批数字能不能拿来下结论，由数据自己回答
        res["quality"] = _quality(st, rounds, via)
        if st["median"] and budget_kbps:
            res["deviation_pct"] = (st["median"] - budget_kbps) / budget_kbps * 100.0
        return res

    try:
        if args.curve or args.curve_latency or args.curve_loss:
            if args.curve:
                print("\n== 带宽偏差曲线（每档 %d 次 × %ds）==" % (args.repeats, args.time))
                for k in CURVE_KBPS:
                    r = one("curve_%d" % k, True, 1, False, k, budget_kbps=k)
                    out["curve"][str(k)] = r
                    print("  设定 %6d → 实测 %.0f kbps（偏差 %+.1f%%）" %
                          (k, r["median_kbps_1024"] or -1, r.get("deviation_pct") or 0))
            if args.curve_latency:
                print("\n== 高延迟 + 低带宽组合曲线（每档 %d 次 × %ds）=="
                      % (args.repeats, args.time))
                for label, extra, k in CURVE_LATENCY:
                    r = one("curve_%s" % label, True, 1, False, k,
                            budget_kbps=k, profile_extra=extra)
                    out["curve_latency"][label] = r
                    print("  %-22s 设定 %6d → 实测 %.0f kbps（偏差 %+.1f%%）" %
                          (label, k, r["median_kbps_1024"] or -1,
                           r.get("deviation_pct") or 0))
            if args.curve_loss:
                print("\n== 丢包（停顿）组合曲线（每档 %d 次 × %ds）=="
                      % (args.repeats, args.time))
                for label, extra, k in CURVE_LOSS:
                    # 丢包档按 iperf 官方 Known Issue #125 用 4KB 块：默认块大小下
                    # sender 会阻塞到整块发完，区间报告时长被拉长，读数是「发送受阻」
                    # 而不是链路能力。显式 --block-kb 可覆盖。
                    r = one("curve_%s" % label, True, 1, False, k,
                            budget_kbps=k, profile_extra=extra,
                            block_kb=(args.block_kb or 4))
                    out["curve_loss"][label] = r
                    print("  %-26s 设定 %6d → 实测 %.0f kbps（偏差 %+.1f%%）" %
                          (label, k, r["median_kbps_1024"] or -1,
                           r.get("deviation_pct") or 0))
        else:
            keys = (args.scenarios.split(",") if args.scenarios
                    else list(SCENARIOS))
            print("\n== 外部真值：%s ==" % ", ".join(keys))
            for k in keys:
                via, par, udp, kbps = SCENARIOS[k]
                r = one(k, via, par, udp, kbps, reverse=k.endswith("_rev"),
                        budget_kbps=kbps if via else None)
                out["scenarios"][k] = r
                print("  %-18s 中位 %.0f kbps  p95 %.0f  n=%d  错误=%d ｜ 发送端 %.0f kbps"
                      % (k, r["median_kbps_1024"] or -1, r["p95_kbps_1024"] or -1,
                         r["n"], len(r["errors"]),
                         r.get("median_sender_kbps_1024") or -1))
    finally:
        tun.stop()
        eng.stop()
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()

    p = Path(args.json) if args.json else (
        ROOT / "tests" / "perf" / "archive" / "v1.1.16" /
        ("iperf3_curve_latency_loss_2026-11-13.json"
         if (args.curve_latency or args.curve_loss)
         else ("iperf3_curve_2026-11-13.json" if args.curve
               else "iperf3_external_truth_2026-11-13.json")))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n已归档：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
