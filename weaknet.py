# -*- coding: utf-8 -*-
"""
WeakNet —— App 弱网测试工具（本地代理式弱网模拟器）

能力：
  - 延迟 / 抖动（上行、下行独立控制）
  - 带宽限速（令牌桶，上行、下行独立）
  - 丢包（表现为传输卡顿 / 停顿）
  - 随机断连（传输中途连接被掐断）
  - 黑洞超时（请求发出后无任何响应）
  - DNS 故障（域名解析失败）
  - 立即拒连（模拟完全断网）
  - 周期性断网（模拟基站切换 / Wi-Fi 与 4G 切换）
  - 内置 Wi-Fi / 4G / 4G 弱网 / 5G / 高铁 / 电梯 / 高丢包 / 高延迟等 17 种预设场景
  - 桌面 GUI（weaknet_gui.py）实时配置与监控；控制端口默认 http://127.0.0.1:8899（仅提供 /api）

用法：
  python weaknet.py [--host 127.0.0.1] [--proxy-port 8888] [--ctrl-port 8899]
  测试设备（手机/电脑/App）将 HTTP(S) 代理指向 本机IP:代理端口 即可生效。
"""

import argparse
import asyncio
import atexit
import functools
import hashlib
import ipaddress
import json
import os
import random
import secrets
import signal
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from collections import deque
from pathlib import Path

# 单位口径的**单一真值**：1 kbps = 1024 bit/s（与 docs/precision.md、
# 采集数据集 note、性能门禁的测量公式一致）。历史上令牌桶用 1024、
# 超时估算和 test_perf.py 用 1000，同一档位在测量侧被系统性低估 2.4%，
# 现在统一到这里，改口径只需改这一个常量。
BITS_PER_KBPS = 1024.0

# 默认端口只在这里定义一次：CLI 参数默认值、G 的初始值、系统代理还原
# 的匹配串、控制口/共享口的「禁止转发」判定都引用它，避免四处硬编码漂移。
DEFAULT_PROXY_PORT = 8888
DEFAULT_CTRL_PORT = 8899

try:
    from version import BUILD, VERSION
except ImportError:                 # 被当成包导入时的兜底（脚本方式运行走上面）
    from .version import BUILD, VERSION

# 打包成 EXE 后 stdout 是块缓冲，窗口里看不到任何输出 —— 强制行缓冲
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

FROZEN = getattr(sys, "frozen", False)   # True = 以 EXE 方式运行

# Windows 定时器分辨率（钉在 Python 3.10 的关键坑）：系统默认 15.625ms 一个 tick，
# asyncio.sleep / wait_for 的实际粒度就是这个值 —— 本机实测 sleep(1ms) 睡 15.6ms、
# sleep(16ms) 睡 31ms、sleep(64ms) 睡 78ms。令牌桶的节奏睡眠与延迟注入全部被量化，
# 是带宽中位数偏高（8590 vs 8000，+7.4%）、RTT 偏高（422 vs 400ms，+5.5%）的
# 重要成分。3.11 起 time.sleep 已换高精度定时器（GH-89592），本项目钉在 3.10，
# 只能用 winmm 的 timeBeginPeriod(1) 补；Win10 2004+ 起该请求只影响本进程，
# 代价可控。WN_TIMER_RES=0 可关（省电优先时）。
_TIMER_RES_MS = 0
if sys.platform == "win32" and os.environ.get("WN_TIMER_RES", "1") != "0":
    try:
        import ctypes
        _winmm = ctypes.WinDLL("winmm")
        if _winmm.timeBeginPeriod(1) == 0:          # TIMERR_NOERROR
            _TIMER_RES_MS = 1
            atexit.register(_winmm.timeEndPeriod, 1)
    except Exception:
        pass


def _tcp_nodelay(writer):
    """给被动 accept 侧的连接补 TCP_NODELAY。

    asyncio 只在 `_set_nodelay()` 里设 NODELAY，而它的条件要求
    `sock.proto == IPPROTO_TCP`：出站（open_connection）的套接字是显式创建的、
    proto=6，满足；accept 进来的套接字 proto 被推断成 0，条件不成立 ——
    本机实测 start_server 接受侧 NODELAY=0。Nagle + 延迟 ACK 的组合会让
    「小请求-小响应」的交互路径多等 40~200ms，控制接口与代理入口正是这种
    小包高频路径。放在 handler 入口统一补；对 64KB 的大块转发无副作用。
    """
    try:
        s = writer.get_extra_info("socket")
        if s is not None:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass


def _tune_gc():
    """延迟敏感长跑进程的 GC 参数：降低 gen0 回收频率，消除随机毫秒级停顿。

    gen0 阈值默认 700，代理这类对象流转密集的负载下每秒触发多次全链回收
    （Close.com 实测：阈值提到 2 万后 p95 少 80~100ms、GC CPU 3%→0.5%）。
    freeze() 把启动期常驻对象（导入的模块、配置表）钉在老年代，不再陪跑。
    不做「关 GC」那种激进方案：asyncio 流与异常链确实产生循环引用。
    """
    try:
        import gc
        gc.collect(2)
        if hasattr(gc, "freeze"):
            gc.freeze()
        gc.set_threshold(20000, 50, 100)
        return gc.get_threshold()
    except Exception:
        return None


def log(*a):
    """立刻可见的输出（同时进缓冲日志，由 _log_flush_loop / atexit 落盘）
    注意：GUI（--windowed）模式下 sys.stdout 为 None，print 会抛异常，必须吞掉。"""
    msg = " ".join(str(x) for x in a)
    try:
        print(msg, flush=True)
    except Exception:
        pass
    global _LOG_SINK
    try:
        if _LOG_SINK is None:
            _LOG_SINK = _LogSink(LOG_FILE)
        _LOG_SINK.write(msg)
    except Exception:
        pass



def _app_dir():
    """EXE 所在目录（源码运行时为脚本目录），用于放日志文件"""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


LOG_FILE = _app_dir() / "weaknet.log"


def _warn_stderr(msg):
    """日志系统自身的失败：不能再走 log()（会递归），只打 stderr。

    完全静默是更坏的选择——历史上「日志不轮转」就是被 `except: pass`
    藏了整整一个版本，直到有人发现 weaknet.log 涨到几百 MB。
    """
    try:
        sys.stderr.write("[log] " + msg + "\n")
    except Exception:
        pass


class _LogSink:
    """缓冲日志：攒够一批再落盘（原来是每条一次 open/write/close），超限自动轮转。

    log_event 也走这里。以前连接级事件（建连 / 模拟断连 / DNS 故障）只进内存
    deque，日志文件里什么都没有 —— 事后想排查「哪条连接被掐了」只能重跑。
    轮转只保留一份 .old：排查最近一次问题足够，又不让日志无限涨。
    """
    MAX_BYTES = 4 << 20      # 超过 4MB 轮转
    BATCH = 64               # 攒够 64 条落一次盘
    FLUSH_BYTES = 256 << 10  # 或攒够 256KB 就落

    def __init__(self, path):
        # 归一成 Path：轮转要用 with_suffix()，传 str 会 AttributeError，
        # 而外层 except Exception: pass 会把它吞掉 —— 结果是日志静默不轮转。
        self.path = path if isinstance(path, Path) else Path(path)
        self.buf = []
        self.pending_bytes = 0
        self.lock = threading.Lock()

    def write(self, msg):
        line = time.strftime("%H:%M:%S ") + msg + "\n"
        with self.lock:
            self.buf.append(line)
            self.pending_bytes += len(line)
            if len(self.buf) >= self.BATCH or self.pending_bytes >= self.FLUSH_BYTES:
                self._flush_locked()

    def flush(self):
        with self.lock:
            self._flush_locked()

    def _flush_locked(self):
        if not self.buf:
            return
        try:
            try:
                if self.path.exists() and self.path.stat().st_size > self.MAX_BYTES:
                    old = self.path.with_suffix(".old")
                    if old.exists():
                        old.unlink()
                    self.path.replace(old)
            except Exception as e:
                # 轮转失败（占用/权限）不该让整批日志一起丢，也不该静默：
                # 日志无限增长的根因往往就是这里，却从来没有任何痕迹。
                _warn_stderr("日志轮转失败：%r" % (e,))
            with open(self.path, "a", encoding="utf-8") as f:
                f.writelines(self.buf)
        except Exception as e:
            # 日志本身失败不能再走 log()（会递归），但也不能完全静默：
            # 打到 stderr，--console 与调试场景才看得到。
            _warn_stderr("日志写入失败：%r" % (e,))
        self.buf = []
        self.pending_bytes = 0


_LOG_SINK = None


def flush_logs():
    """把缓冲日志落盘（定时任务与 atexit 调用）；从未记过日志时是空操作"""
    global _LOG_SINK
    if _LOG_SINK is not None:
        try:
            _LOG_SINK.flush()
        except Exception:
            pass


async def _log_flush_loop():
    while True:
        await asyncio.sleep(2)
        flush_logs()


def can_bind(port, host="127.0.0.1"):
    """端口是否可监听（被占用返回 False）"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def find_free_port(start, host="127.0.0.1", limit=200):
    """从 start 开始找到第一个可监听的端口"""
    p = int(start)
    for _ in range(limit):
        if can_bind(p, host):
            return p
        p += 1
    return int(start)


def is_weaknet_running(port):
    """目标端口上是否已经在跑一个 WeakNet 控制台"""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/status", method="GET")
        urllib.request.urlopen(req, timeout=1.5)   # 系统代理可能指向自身，见下方说明
    except Exception:
        # 连不上也可能是因为本机系统代理指向了失效端口，用直连再试一次
        try:
            import urllib.request as u2
            op = u2.build_opener(u2.ProxyHandler({}))
            with op.open(f"http://127.0.0.1:{port}/api/status", timeout=1.5) as r:
                body = r.read(200).decode("utf-8", "ignore")
                return "profile" in body or "vpn_active" in body
        except Exception:
            return False
    else:
        return True
    return False


# 网页控制台（dashboard.html）已下线：控制界面只剩桌面 GUI（weaknet_gui.py）。
# 本机 HTTP 服务保留用于自动化调用（/api/...），不再提供可视化页面。


# --------------------------------------------------------------------------
# 网络参数模型
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# 进程识别：让「接管范围」精确到单个程序（仅 Windows）
#
# 背景：Windows 的系统代理是进程无关的 —— 一旦写入注册表，所有遵循代理的程序
# 都会被接管，没有办法只对某个 exe 生效。因此「只接管指定程序」只能在代理层实现：
#   accept 拿到对端端口 -> 查 TCP 连接表得 PID -> 查进程快照得 exe 名 -> 决定是否注入弱网。
# 未命中的程序走「直通」（原样转发，不受任何弱网影响），效果等同没被接管。
#
# 只用 ctypes 调系统 API，不引入任何第三方依赖（PyInstaller 打包后同样可用）。
# --------------------------------------------------------------------------

IS_WIN = (os.name == "nt")

# 进程名缓存（pid -> exe 名，带 TTL）现在挂在 G 上，见 G.proc_name_cache。
_PROC_CACHE_TTL = 5.0                        # 定期刷新间隔
_PROC_MISS_GAP = 0.25                        # 因「查不到」触发的刷新最小间隔（防抖）
_TCP_LOOKUP_RETRIES = 4                      # 连接表里查不到这行时最多重试几次
_TCP_LOOKUP_GAP = 0.015                      # 每次重试的间隔（总计最多约 45ms）

if IS_WIN:
    import ctypes
    import ctypes.wintypes as wt
    import struct

    _AF_INET = 2
    _TCP_TABLE_OWNER_PID_ALL = 5
    _ERR_INSUFFICIENT_BUFFER = 122
    _TH32CS_SNAPPROCESS = 0x2
    _MAX_PATH = 260

    _iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    # 句柄在 64 位上是 8 字节，不显式声明 restype 会被 ctypes 截成 32 位 int，
    # 高 32 位丢失后 CloseHandle/QueryFullProcessImageName 会失败（表现为「查不到进程名」）。
    _kernel32.OpenProcess.restype = wt.HANDLE
    _kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    _kernel32.CloseHandle.restype = wt.BOOL
    _kernel32.CloseHandle.argtypes = [wt.HANDLE]

    class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [("dwState", wt.DWORD), ("dwLocalAddr", wt.DWORD),
                    ("dwLocalPort", wt.DWORD), ("dwRemoteAddr", wt.DWORD),
                    ("dwRemotePort", wt.DWORD), ("dwOwningPid", wt.DWORD)]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * _MAX_PATH)]

    def _tcp_rows():
        """返回 [(local_ip, local_port, remote_ip, remote_port, pid), ...]"""
        size = wt.DWORD(0)
        # 注意：GetExtendedTcpTable 返回的是 DWORD 错误码而非 BOOL。
        # 首次传空缓冲会得到 122(ERROR_INSUFFICIENT_BUFFER) 并把所需大小写进 size，属预期行为。
        rc = _iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), True,
                                           _AF_INET, _TCP_TABLE_OWNER_PID_ALL, 0)
        if rc != 0 and rc != _ERR_INSUFFICIENT_BUFFER:
            return []
        buf = ctypes.create_string_buffer(size.value)
        rc = _iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), True,
                                           _AF_INET, _TCP_TABLE_OWNER_PID_ALL, 0)
        if rc != 0:
            return []
        n = struct.unpack_from("<I", buf, 0)[0]
        rows = (_MIB_TCPROW_OWNER_PID * n).from_buffer_copy(buf, 4)
        out = []
        for r in rows:
            # 端口在结构体里是网络字节序，必须 ntohs 转回主机序
            out.append((socket.inet_ntoa(struct.pack("<I", r.dwLocalAddr)),
                        socket.ntohs(r.dwLocalPort & 0xFFFF),
                        socket.inet_ntoa(struct.pack("<I", r.dwRemoteAddr)),
                        socket.ntohs(r.dwRemotePort & 0xFFFF),
                        r.dwOwningPid))
        return out

    def _snapshot_proc_names():
        """进程快照 {pid: exe 名}。约 10ms，因此带缓存，不要每条连接都调。"""
        snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return {}
        try:
            pe = _PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(pe)
            m = {}
            ok = _kernel32.Process32FirstW(snap, ctypes.byref(pe))
            while ok:
                m[pe.th32ProcessID] = pe.szExeFile
                ok = _kernel32.Process32NextW(snap, ctypes.byref(pe))
            return m
        finally:
            _kernel32.CloseHandle(snap)


def _pid_exe_name(pid):
    """只查一个 PID 的 exe 名（OpenProcess + QueryFullProcessImageNameW）。

    比全量快照便宜得多（不需要遍历进程表），所以可以每次 miss 都调、不必限频。
    这正是修下面那个 bug 的关键：两个新程序前后脚发起连接时，
    第一个触发了全量快照刷新，第二个若只能等限频窗口过去才会被认出来，
    于是它就被当成「未识别」放行了 —— 用户看到的现象就是
    「明明加进了接管名单，偏偏这一次没生效」。
    """
    try:
        h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except Exception:
        return None
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(_MAX_PATH)
        size = wt.DWORD(_MAX_PATH)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return (buf.value or "").rsplit("\\", 1)[-1]
    except Exception:
        return None
    finally:
        try:
            _kernel32.CloseHandle(h)
        except Exception:
            pass
    return None


def _lookup_proc_name(pid):
    """pid -> exe 名。缓存未命中时先单点查这个 PID，查不到再按限频刷全量快照。

    为什么必须「miss 就查」：进程快照是定时缓存的，而用户往往是
    「先开 WeakNet、再启动要测试的 App」—— 那个 App 是快照之后才出现的新 PID，
    只靠 TTL 定期刷新的话，它在接下来几秒内都会被判成「未识别」，
    表现就是名单外的程序照样被限速 / 名单内的却没被接管。
    全量快照仍需限频（_PROC_MISS_GAP），它约 10ms，不能每条连接都做。
    """
    c = G.proc_name_cache
    now = time.monotonic()
    name = c["map"].get(pid)
    if name is None:
        name = _pid_exe_name(pid)        # 单点查：廉价，不限频
        if name:
            c["map"][pid] = name
            return name
    if name is None and (now - c["at"]) >= _PROC_MISS_GAP:
        c["map"] = _snapshot_proc_names()
        c["at"] = now
        name = c["map"].get(pid)
    elif (now - c["at"]) > _PROC_CACHE_TTL:
        c["map"] = _snapshot_proc_names()
        c["at"] = now
        name = c["map"].get(pid) or name
    return name


_TCP_PID_MAP_CACHE = {"at": 0.0, "map": {}}   # (local_port, remote_port) -> pid
_TCP_PID_MAP_TTL = 0.3          # 快照复用窗口：300ms 内的并发连接共享一次全表扫描


def _tcp_pid_map(force=False):
    """(local_port, remote_port) -> pid 的索引。

    为什么要缓存：每条新代理连接都要反查「是谁连上来的」，原来的做法是每次
    现扫全张 TCP 表（GetExtendedTcpTable 整表拷贝 + Python 逐行循环）——
    忙时表里有上千行，浏览器开一个 50 资源的页面就是 50 次全表扫描。
    300ms 内的连接共享一份快照；TTL 过期或强制刷新时才真扫。
    扫描结果只影响「是否接管」的判定，短暂陈旧可接受：查不到就实时刷新。
    """
    now = time.monotonic()
    c = _TCP_PID_MAP_CACHE
    if not force and c["map"] and (now - c["at"]) < _TCP_PID_MAP_TTL:
        return c["map"]
    rows = _tcp_rows()
    m = {}
    for _la, lp, _ra, rp, pid in rows:
        m[(lp, rp)] = pid
    c["map"] = m
    c["at"] = now
    return m


def proc_name_of_peer(peer_port, local_port):
    """反查：连到本代理 local_port 的、来自 peer_port 的连接属于哪个进程。
    返回 exe 名（小写）；查不到或非 Windows 返回 None。

    为什么要重试：连接刚被 accept 的瞬间，Windows 的 TCP 连接表偶尔还没登记这一行，
    查不到就会把该连接当「未识别」放行（名单内的程序反而没被接管）。
    只在「表里压根找不到这行」时重试（重试强制刷新快照），找到行就立即返回，
    正常路径不增加任何延迟。

    注意：本函数是**阻塞**的（同步 sleep + GetExtendedTcpTable 全表扫描），
    调用方必须走 `_proc_name_of_peer_async()`，不要在事件循环里直接调。
    """
    if not IS_WIN:
        return None
    for attempt in range(_TCP_LOOKUP_RETRIES):
        pid = _tcp_pid_map(force=(attempt > 0)).get((peer_port, local_port))
        if pid is not None:
            name = _lookup_proc_name(pid)
            return name.lower() if name else None
        if attempt + 1 < _TCP_LOOKUP_RETRIES:
            time.sleep(_TCP_LOOKUP_GAP)
    return None


async def _proc_name_of_peer_async(peer_port, local_port):
    """把「反查连接属于哪个进程」挪出事件循环。

    实测（tests/perf/test_slow_callbacks.py，阈值 50 ms）：每条新连接会触发
    最多 4 次重试，每次 15 ms 同步 sleep + 一次 GetExtendedTcpTable 全表扫描，
    合计约 94 ms —— 全部发生在事件循环里。也就是说**每来一条连接就把整个代理
    卡住 94 ms**，8 条并发就是将近 0.75 s 的事件循环停顿（这与 P8 并发档
    吞吐只有设定值 0.58 倍的现象高度相关）。

    这里整体丢进执行器线程：函数、参数、返回语义完全不变（还是同一份缓存、
    同一套重试逻辑），只是不再占住事件循环。ctypes 调用与 time.sleep 都会
    释放 GIL，放到线程里跑得动，也不会影响其他连接的转发。
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, proc_name_of_peer,
                                      peer_port, local_port)


def list_procs():
    """列出候选进程名（供界面勾选）：当前有 TCP 连接的进程 + 常见浏览器/应用。
    非 Windows 返回空列表（此时只能「接管全部」）。"""
    if not IS_WIN:
        return []
    try:
        rows = _tcp_rows()
        pid2n = {r[4]: G.proc_name_cache["map"].get(r[4]) for r in rows}
        live = {n.lower() for n in pid2n.values() if n and n.lower() != "system"}
        live.discard("")
        common = ["chrome.exe", "msedge.exe", "firefox.exe", "wechat.exe",
                  "weixin.exe", "qq.exe", "postman.exe", "fiddler.exe",
                  "python.exe", "node.exe", "code.exe"]
        out = sorted(live | set(common))
        return out
    except Exception:
        return []


# --------------------------------------------------------------------------
# 规格单一真源
#
# 预置场景、参数上下限/默认值、场景与参数文案、组合规则全部来自
# spec/spec.json；Android 端由 tools/gen_spec.py 生成 SpecGen.java。
# 文件缺失或解析失败直接报错：静默回退到硬编码会让「唯一真源」变成两份，
# 漂移只会更隐蔽（v1.1.x 就出现过两端参数上限各写一套）。
# --------------------------------------------------------------------------
SPEC_PATH = Path(__file__).resolve().parent / "spec" / "spec.json"
try:
    _SPEC_BYTES = SPEC_PATH.read_bytes()
    SPEC = json.loads(_SPEC_BYTES.decode("utf-8"))
except Exception as e:
    raise RuntimeError("规格文件读取失败：%s（%s）" % (SPEC_PATH, e))

SPEC_HASH = hashlib.sha256(_SPEC_BYTES).hexdigest()[:8]
PRESETS = {p["key"]: (p["name"], p["cat"], dict(p.get("params") or {}))
           for p in SPEC["presets"]}
PRESET_SHORT = {p["key"]: p.get("short") or p["name"] for p in SPEC["presets"]}
PRESET_NOTES = dict(SPEC["notes"])
PARAM_SPECS = [dict(p) for p in SPEC["params"]]
PARAM_DEFAULTS = {p["key"]: p["default"] for p in PARAM_SPECS}
PARAM_NOTES = dict(SPEC["param_notes"])
PROF_LIMITS = {p["key"]: int(p["max"]) for p in PARAM_SPECS}
DEFAULT_PROFILE_NAME = PRESETS.get("direct", ("直连（不限制）",))[0]

# 出厂默认：首次启动时一次性写进「我的自定义」与「弱网组合」的示例条目。
# 与内置预设同在 spec.json 里，Windows 直读、Android 由 gen_spec.py 生成常量，
# 两端拿到的名字和参数永远不会漂移。seed 改一次才会重新注入一轮（见 _ensure_seeded）。
DEFAULTS = SPEC["defaults"]
DEFAULT_SEED = str(DEFAULTS["seed"])
DEFAULT_SCENES = DEFAULTS["scenes"]
DEFAULT_COMBOS = DEFAULTS["combos"]

COMBO_SPEC = SPEC["combo"]
MAX_COMBO_STEPS = int(COMBO_SPEC["max_steps"])
STEP_MAX_MINUTES = int(COMBO_SPEC["max_minutes"])
STEP_MAX_SECONDS = int(COMBO_SPEC["max_seconds"])

SHARE_SPEC = SPEC["share"]
SHARE_VER = int(SHARE_SPEC["ver"])
PIN_LEN = int(SHARE_SPEC["pin_len"])
MAX_INBOX = int(SHARE_SPEC["inbox_max"])
MAX_SHARED_STEPS = int(SHARE_SPEC["max_shared_steps"])

# 自定义场景 / 弱网组合的「取名」上限（字符数，中文一个算一个）。
# 只管用户起的这两类名字：内置预设的显示名最长 14 字（如「弱 Wi-Fi（隔墙/远端）」），
# 设备昵称走 clean_name（24 字），都不归这里管。
NAME_MAX = int(SPEC["name_max"])


def limit_item_name(name, fallback=""):
    """自定义场景 / 弱网组合的名字：压空白 + 截到 NAME_MAX 个字符。

    所有会写出名字的入口（GUI / API / 共享 / 出厂默认 / 读老存档）都过这里，
    保证落库的名字一律不超上限；输入侧的 maxlength 只是少打几个字的体验优化。
    """
    s = " ".join(str(name or "").split())[:NAME_MAX].rstrip()
    if s:
        return s
    return " ".join(str(fallback or "").split())[:NAME_MAX].rstrip()


# 未命中「接管范围」的程序走直通：原样转发，不注入任何弱网参数。
# 用一个固定的全零 profile，且不随 G.profile 变化（否则切换场景会误伤不该接管的程序）。
BYPASS_PROFILE = None   # 延迟到 default_profile 定义后初始化


def default_profile(name=None):
    prof = dict(PARAM_DEFAULTS)
    prof["name"] = name or DEFAULT_PROFILE_NAME
    return prof


def has_limits(prof):
    """这组参数是否真的会「动」流量（与全默认的直连有没有区别）。

    用来推断限速总开关的语义：应用了带内容的场景就等于把限速打开，
    回到全零就等于是关掉。这样网页 / GUI / 接口三个入口不用各自维护一份状态。

    ``stall_ms`` 与 ``flap_blackout_sec`` 不算：前者只在 loss_pct>0 时才被
    用到，后者只在 flap_sec>0 时才被用到 —— 单独改这两项不会改变任何流量，
    与 Java 端 Profile.hasLimits() 的口径保持一致（否则同一份参数在两端
    会得出「有没有在限速」的不同结论）。
    """
    d = default_profile()
    for k, dv in d.items():
        if k in ("name", "stall_ms", "flap_blackout_sec"):
            continue
        if prof.get(k, dv) != dv:      # 缺项按默认值算，部分字典也能判定
            return True
    return False


# 预置场景：名称 / 分类 / 参数覆盖全部来自 spec/spec.json。
# 已退网制式（2G / 3G / 拨号）不再单列：要「极慢网」直接用自定义参数压带宽。


# 每个内置场景的注释来自 spec 的 notes。为什么不按参数值自动生成：
# 光看数字（110ms / 3Mbps / 4% 丢包）分不清「4G 弱网」和「弱 Wi-Fi」差在哪，
# 也看不出该用哪一档复现某个 bug。


# 弱网组合的说明 / 例子 / 提醒来自 spec 的 combo：两端说同一句话。
COMBO_DOC_SEP = "\x1f"
COMBO_INTRO = COMBO_SPEC["intro"]
COMBO_EXAMPLES = [dict(e) for e in COMBO_SPEC["examples"]]
COMBO_TIPS = list(COMBO_SPEC["tips"])


# 三块内容拼成的完整文档串（两端逐字一致，由 _t_combo_notes.py 比对）
#
# 为什么拼成一份大字符串而不是留三个独立结构：Java 那边要写成 String[][]，
# 解析它的源码来比对极易在「多行拼接的字符串」上出错 ——
# 表现是两边明明一样、测试却说不一样，比没有测试更坏。
# 拼成一份后，解析只需处理「一个 String 常量」，稳得多。
COMBO_DOC = COMBO_DOC_SEP.join(
    [COMBO_INTRO]
    + ["\n".join([e["name"], e["desc"], e["effect"], e["steps"]])
       for e in COMBO_EXAMPLES]
    + list(COMBO_TIPS))


def combo_doc_parts(doc=None):
    """把文档串拆回 (说明, 例子列表, 提醒列表)。例子每条 4 行。"""
    text = COMBO_DOC if doc is None else doc
    blocks = text.split(COMBO_DOC_SEP)
    if not blocks:
        return "", [], []
    intro = blocks[0]
    examples = []
    for b in blocks[1:len(COMBO_EXAMPLES) + 1]:
        lines = b.split("\n")
        if len(lines) < 4:
            continue
        examples.append({"name": lines[0], "desc": lines[1],
                         "effect": lines[2], "steps": lines[3]})
    tips = blocks[len(COMBO_EXAMPLES) + 1:]
    return intro, examples, tips


def build_preset_profile(key):
    # "u:<id>" = 用户自定义场景（自定义栏），其余为内置预设
    if isinstance(key, str) and key.startswith("u:"):
        return build_user_profile(key)
    name, _cat, extra = PRESETS[key]
    prof = default_profile(name)
    prof.update(extra)
    return prof


# --------------------------------------------------------------------------
# 用户自定义场景（自定义栏）
#
# 用户把自己调好的一组参数命名保存后，会像内置预设一样出现在界面上，
# 点一下即可切换；不需要了可以直接删除。参数存 JSON：
#   源码运行 → 脚本目录；EXE 运行 → EXE 同目录（便于备份与随包携带）；
#   目录不可写（例如装在 Program Files）→ 自动退回 ~/.weaknet/。
# --------------------------------------------------------------------------

_PRESET_FILES = None
USER_PRESET_KEY_PREFIX = "u:"


def _preset_files():
    """候选存储位置，按优先级排列"""
    global _PRESET_FILES
    if _PRESET_FILES is None:
        cands = []
        try:
            cands.append(_app_dir() / "weaknet_presets.json")
        except Exception:
            pass
        try:
            cands.append(Path(os.path.expanduser("~")) / ".weaknet" / "weaknet_presets.json")
        except Exception:
            pass
        _PRESET_FILES = cands or [Path("weaknet_presets.json")]
    return _PRESET_FILES


def _new_pid(used):
    """生成一个未占用的短 id"""
    while True:
        pid = "%08x" % random.getrandbits(32)
        if pid not in used:
            return pid


# 自定义参数的上下限来自 spec 的 params（PROF_LIMITS 在上面统一构建）。

# 每个参数的注释来自 spec 的 param_notes。


# 说明文案的固定结构：第一段讲「这项是干什么的」，第二段讲「坑在哪」。
# 界面上必须分开显示 —— 糊成一坨谁也分不清哪句是提醒
#（_t_param_notes.py 断言每条都是两段，文案两端逐字比对，所以结构不能随意改）。
def split_param_note(note):
    """把一段说明拆成 (作用段, 提醒条目列表)。

    第一段原样作为「作用」；第二段按句号切成一条条「提醒」。
    第二段常含并列的多个提醒（"和…的区别；两项可以叠加"），
    分条列出来比挤成一整行好读。

    只有一段时（老存档 / 自定义文案）退回按句切：首句当作用，其余当提醒。
    """
    text = str(note or "").strip()
    if not text:
        return "", []
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    if not parts:
        return "", []
    if len(parts) == 1:
        sents = _split_sentences(parts[0])
        if not sents:
            return parts[0], []
        return sents[0], sents[1:]
    return parts[0], _split_sentences(" ".join(parts[1:]))


def _split_sentences(text):
    """按中文句号 / 问号 / 感叹号切句，保留标点。"""
    out, buf = [], ""
    for ch in str(text or ""):
        buf += ch
        if ch in "。！？":
            s = buf.strip()
            if s:
                out.append(s)
            buf = ""
    tail = buf.strip()
    if tail:
        out.append(tail)
    return out


# ---------------------------------------------------------------- 说明文案的排版
# 具体规则搬到 text_layout.py（纯函数、可单测）；这里 re-export 保持
# `W.wrap_note_lines` / `W.split_note_atoms` 等旧路径可用，调用方零改动。
from text_layout import (                       # noqa: E402
    split_note_atoms, wrap_note_lines, _is_note_cjk, _is_note_word_char,  # noqa: F401
    _note_group_units, _note_units_text, _note_safe_chunks, _note_hard_split,  # noqa: F401
    _NOTE_QUOTE_PAIRS, _NOTE_NO_START, _NOTE_NO_END, _NOTE_NO_START_GLUE,  # noqa: F401
    _NOTE_WORD_CONNECT, _NOTE_SPACE_CHARS)  # noqa: F401


def _norm_user_profile(name, prof):
    """把外部传入的参数收敛成完整 profile：只接受已知数值字段，并夹到合理区间。

    光做「负值归零」是不够的 —— 丢包率 999、下单比延迟 99999 这类值虽然「非负」，
    落到引擎里会变成「每个包都丢 / 睡死几十秒」，看起来和真断网没区别，
    用户却以为自己保存的是 30% 丢包。这里统一夹一次。
    """
    p = default_profile(name)
    for k, v in (prof or {}).items():
        if k in p and k != "name" and isinstance(v, (int, float)) and not isinstance(v, bool):
            p[k] = min(max(0, v), PROF_LIMITS.get(k, v))
    return p


def load_user_presets():
    """读取磁盘上的自定义场景 → [{"id","name","profile"}]。文件不存在/损坏返回 []"""
    for f in _preset_files():
        try:
            if not f.exists():
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            # 静默 continue 会让「自定义场景全没了」变成一个查不出来的现象：
            # 文件损坏、权限不足、磁盘满都长一个样。至少留一行可检索的日志。
            log("自定义场景读取失败（%s）：%r" % (f, e))
            continue
        raw = data.get("items") if isinstance(data, dict) else data
        out = []
        for it in (raw or []):
            try:
                name = limit_item_name(it.get("name"))
                if not name:
                    continue
                # 老存档里可能有超长名字：截断后撞名的按重名规则加序号，
                # 免得列表出现两条一模一样的名字分不清
                name = _unique_name(out, name)
                out.append({"id": str(it.get("id") or ""),
                            "name": name,
                            "profile": _norm_user_profile(name, it.get("profile"))})
            except Exception:
                continue
        # 历史脏数据可能缺 id，补上再回写一次
        ids = {x["id"] for x in out if x["id"]}
        for x in out:
            if not x["id"]:
                x["id"] = _new_pid(ids)
                ids.add(x["id"])
        return out
    return []


def _items_payload(items):
    return [{"id": it["id"], "name": it["name"], "profile": it["profile"]} for it in items]


def _combos_payload(combos=None):
    """弱网组合列表的对外形态：id/name/loop/summary/steps。

    字段名与 Android 端 MyCombos.toJsonArray 对齐 —— 同一套脚本在两端都能跑，
    不用为平台各写一份解析。summary 是一行人类可读的段序说明。
    """
    if combos is None:
        combos = get_combos()
    return [{"id": c["id"], "name": c["name"], "loop": bool(c.get("loop")),
             "summary": combo_summary(c),
             "steps": [{"key": s["key"], "minutes": int(s.get("minutes", 0) or 0),
                        "seconds": int(s.get("seconds", 0) or 0)}
                       for s in c.get("steps", [])]}
            for c in combos]


def _dump_store(items=None, combos=None):
    """把自定义场景与弱网组合一起写回磁盘（两者共用一个文件）"""
    if items is None:
        items = get_user_presets()
    if combos is None:
        combos = get_combos()
    # seed 是「这轮出厂默认已经注入过」的凭据：用户把默认项全删了也不该在下次
    # 启动时复活，所以标记必须跟着每次写盘一起落下去。
    payload = json.dumps({"version": 1,
                          "seed": DEFAULT_SEED,
                          "items": _items_payload(items),
                          "combos": _combos_payload(combos)},
                         ensure_ascii=False, indent=2).encode("utf-8")
    for f in _preset_files():
        try:
            if f.parent and not f.parent.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(payload)
            return True
        except Exception as e:
            log("写入 %s 失败：%r" % (f, e))
            continue
    return False


def _dump_user_presets(items):
    """写回磁盘（挑第一个可写的位置）。失败返回 False"""
    return _dump_store(items=items)


# --------------------------------------------------------------------------
# 出厂默认（5 支自定义场景 + 5 支弱网组合）
#
# 只在「这台机器第一次跑这个 seed」时注入一次，写进的仍是普通用户条目：
# 能改、能删、能共享，删掉后不会在下次启动复活。判定靠存储文件里的 seed 字段，
# 不靠「列表是不是空的」——否则用户把自己建的全删了，默认项就会自己长回来。
#
# 已有存档一律保留（升级上来的用户也有自己的场景与组合），只在末尾追加，
# 同名跳过。默认组合只引用内置预设 key：自定义场景 id 是各端现编的，
# 跨端对不上，写进组合会变成「已删除的场景」。
# --------------------------------------------------------------------------

_seeded = False


def _store_seeded():
    """磁盘上的存档是否已经打过本轮 seed 标记（损坏的文件跳过，与 load_* 一致）"""
    for f in _preset_files():
        try:
            if not f.exists():
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        return isinstance(data, dict) and data.get("seed") == DEFAULT_SEED
    return False


def _ensure_seeded():
    """首次访问用户数据时注入出厂默认；get_user_presets / get_combos 都会先过这里。

    本函数里的 load_combos / norm_combo_steps / _dump_store 定义在文件更下方，
    但调用发生在模块加载完毕之后，所以顺序不影响。
    """
    global _seeded
    if _seeded:
        return
    _seeded = True
    if _store_seeded():
        return

    items = load_user_presets()
    combos = load_combos()
    used = {x["id"] for x in items} | {c["id"] for c in combos}
    names = {x["name"] for x in items}
    cnames = {c["name"] for c in combos}
    added = [0, 0]

    for d in DEFAULT_SCENES:
        name = limit_item_name(d.get("name"))
        if not name or name in names:
            continue
        pid = _new_pid(used)
        used.add(pid)
        items.append({"id": pid, "name": name,
                      "profile": _norm_user_profile(name, d.get("params"))})
        names.add(name)
        added[0] += 1

    for d in DEFAULT_COMBOS:
        name = limit_item_name(d.get("name"))
        if not name or name in cnames:
            continue
        steps = norm_combo_steps(d.get("steps"))
        if not steps:
            continue
        cid = _new_pid(used)
        used.add(cid)
        combos.append({"id": cid, "name": name,
                       "loop": bool(d.get("loop")), "steps": steps})
        cnames.add(name)
        added[1] += 1

    # 先落内存再写盘：_dump_store → combo_summary → scene_name 会反查自定义场景，
    # 缓存没就绪会绕回 get_user_presets 读到空列表。
    G.user_presets = items
    G.combos = combos
    _dump_store(items, combos)        # 无论有没有追加都要写标记，避免每次启动重扫
    if added[0] or added[1]:
        log("已载入出厂默认：%d 支自定义场景、%d 支弱网组合" % (added[0], added[1]))


def get_user_presets():
    """内存缓存的自定义场景列表（首次调用时从磁盘加载并注入出厂默认）"""
    _ensure_seeded()
    if G.user_presets is None:
        G.user_presets = load_user_presets()
    return G.user_presets


def find_user_preset(pid):
    pid = str(pid)
    for it in get_user_presets():
        if it["id"] == pid:
            return it
    return None


def build_user_profile(key):
    """key（'u:<id>' 或裸 id）→ 完整 profile"""
    it = find_user_preset(key[2:] if str(key).startswith(USER_PRESET_KEY_PREFIX) else key)
    if it is None:
        raise KeyError(key)
    prof = default_profile(it["name"])
    prof.update(it["profile"])
    prof["name"] = it["name"]
    return prof


def _unique_name(items, name, self_id=None):
    """同名自动加序号，避免列表里出现两个一样名字分不清。

    加后缀也要守住 NAME_MAX：10 字满了就先截短基础名再拼 " (2)"，
    否则自动去重反而造出一条超长名字。
    """
    name = limit_item_name(name)
    others = {it["name"] for it in items if it["id"] != self_id}
    if name not in others:
        return name
    for i in range(2, 100):
        suffix = f" ({i})"
        cand = name[:NAME_MAX - len(suffix)].rstrip() + suffix
        if cand not in others:
            return cand
    return limit_item_name(f"{name} {int(time.time())}")


def upsert_user_preset(name, prof, pid=None):
    """新增或覆盖一条自定义场景，返回 (item, items)"""
    items = get_user_presets()
    name = limit_item_name(name, "自定义")
    item = find_user_preset(pid) if pid else None
    if item is None:
        item = {"id": _new_pid({it["id"] for it in items}), "name": "", "profile": None}
        items.append(item)
    item["name"] = _unique_name(items, name, item["id"])
    item["profile"] = _norm_user_profile(item["name"], prof)
    ok = _dump_user_presets(items)
    if not ok:
        log("保存自定义场景失败（存储位置均不可写）")
    return item, items


def delete_user_preset(pid):
    """删除一条自定义场景，返回 (是否删掉, 剩余列表)"""
    items = get_user_presets()
    pid = str(pid)
    left = [it for it in items if it["id"] != pid]
    if len(left) == len(items):
        return False, items
    G.user_presets = left
    ok = _dump_user_presets(left)
    if not ok:
        log("删除自定义场景后写盘失败（改动仅存在于本次运行）")
    return True, left


# --------------------------------------------------------------------------
# 弱网组合：多支场景按顺序自动播放
#
# 一次「地铁通勤」通常是好几段 Different 网络（进站 5G → 隧道掉到 4G 弱网 → 站间
# 断一下），手动跟着点不现实，所以让用户把几支场景排好队、每段给个时长存下来，
# 点一下就自动按序播放。存在自定义场景同一个文件里（combos 段），重装跟着留着。
# --------------------------------------------------------------------------

COMBO_KEY_PREFIX = "c:"
# 组合上限（段数 / 单段分秒）来自 spec 的 combo。


def step_seconds(st):
    """一段跑多久（秒）。分秒都空时返回 0 —— 这种段在入库时会被丢掉"""
    try:
        mi = max(0, int(st.get("minutes", 0) or 0))
    except Exception:
        mi = 0
    try:
        se = max(0, int(st.get("seconds", 0) or 0))
    except Exception:
        se = 0
    return min(mi, STEP_MAX_MINUTES) * 60 + min(se, STEP_MAX_SECONDS)


def step_text(st):
    """界面上这段时长怎么写：「2 分 30 秒」"""
    mi = max(0, int(st.get("minutes", 0) or 0))
    se = max(0, int(st.get("seconds", 0) or 0))
    if mi and se:
        return "%d 分 %d 秒" % (mi, se)
    if mi:
        return "%d 分" % mi
    return "%d 秒" % se


def norm_combo_steps(steps, rejected=None):
    """把外部传入的步骤收敛成合法列表：丢掉没有场景 / 时长全空的段，超上限的裁掉。

    rejected 传入一个 list 时，被丢掉的段会以 {"index": 原序号(从 1 起),
    "reason": str, "key": str} 记进去，调用方据此提示用户哪一段有问题。

    组合里不允许再选另一支组合（COMBO_KEY_PREFIX 开头的 key）：播放器是按段/steps
    线性推进的，套一层嵌套它既不会展开也不会报错，只会把这一段静默跳过 —— 用户排的
    第 2 段凭空不见最难排查，所以在入库这一层就掐死。
    """
    out = []
    for i, s in enumerate(steps or [], 1):
        try:
            key = str(s.get("key") or "").strip()
        except Exception:
            continue
        if key.startswith(COMBO_KEY_PREFIX):
            # 嵌套组合一律不要：见上面 docstring 的说明
            if isinstance(rejected, list):
                rejected.append({"index": i, "key": key, "reason": "combo"})
            continue
        if not key:
            continue
        mi = 0
        se = 0
        try:
            mi = max(0, min(STEP_MAX_MINUTES, int(s.get("minutes", 0) or 0)))
        except Exception:
            pass
        try:
            se = max(0, min(STEP_MAX_SECONDS, int(s.get("seconds", 0) or 0)))
        except Exception:
            pass
        if mi <= 0 and se <= 0:
            continue                       # 分秒都填 0 的段没法定时，等于没排
        out.append({"key": key, "minutes": mi, "seconds": se})
        if len(out) >= MAX_COMBO_STEPS:
            break
    return out


def load_combos():
    """读取磁盘上的弱网组合 → [{"id","name","loop","steps"}]。文件不存在/损坏返回 []"""
    for f in _preset_files():
        try:
            if not f.exists():
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log("弱网组合读取失败（%s）：%r" % (f, e))
            continue
        raw = data.get("combos") if isinstance(data, dict) else None
        out = []
        for c in (raw or []):
            try:
                name = limit_item_name(c.get("name"))
                if not name:
                    continue
                steps = norm_combo_steps(c.get("steps"))
                if not steps:              # 全被拦掉的空壳组合没必要显示
                    continue
                name = _unique_name(out, name)
                out.append({"id": str(c.get("id") or ""), "name": name,
                            "loop": bool(c.get("loop")), "steps": steps})
            except Exception:
                continue
        ids = {x["id"] for x in out if x["id"]}
        for x in out:
            if not x["id"]:
                x["id"] = _new_pid(ids)
                ids.add(x["id"])
        return out
    return []


def get_combos():
    """内存缓存的弱网组合（首次调用时从磁盘加载并注入出厂默认）"""
    _ensure_seeded()
    if G.combos is None:
        G.combos = load_combos()
    return G.combos


def find_combo(cid):
    cid = str(cid)
    for c in get_combos():
        if c["id"] == cid:
            return c
    return None


def scene_profile(key):
    """任意场景 key（内置 / 'u:<id>' 自定义）→ 完整 profile"""
    key = str(key)
    if key.startswith(USER_PRESET_KEY_PREFIX):
        return build_user_profile(key)
    return build_preset_profile(key)


def scene_name(key):
    """key → 显示名；查不到返回 key 本身（界面至少还能看出是哪一支）"""
    key = str(key)
    try:
        if key.startswith(COMBO_KEY_PREFIX):
            c = find_combo(key[len(COMBO_KEY_PREFIX):])
            return c["name"] if c else "已删除的组合"
        if key.startswith(USER_PRESET_KEY_PREFIX):
            return build_user_profile(key).get("name") or key
        return build_preset_profile(key).get("name") or key
    except Exception:
        return key


def upsert_combo(name, steps, loop=False, cid=None):
    """新增或覆盖一个组合，返回 (combo, combos)

    归一化后一段都不剩时（全是嵌套组合 / 全是空时长）**不落库**，返回
    (None, combos) 让调用方自己决定怎么提示。存一个 0 段的空壳组合毫无意义：
    播不了、点开是空的，还会在列表里占一行让人以为排好了。
    覆盖已有组合时原组合保持不变 —— 传了个空壳不该把人家排好的那支抹掉。
    """
    combos = get_combos()
    name = limit_item_name(name, "弱网组合")
    combo = find_combo(cid) if cid else None
    bad = []
    steps = norm_combo_steps(steps, bad)
    if bad:
        log("组合「%s」丢掉 %d 段：里面选的是别的组合（不允许嵌套）→ %s"
            % (name, len(bad), ", ".join("第 %d 段" % b["index"] for b in bad)))
    if not steps:
        log("组合「%s」没有一段可用，未保存" % name)
        return None, combos
    if combo is None:
        combo = {"id": _new_pid({c["id"] for c in combos}), "name": "",
                 "loop": False, "steps": []}
        combos.append(combo)
    combo["name"] = _unique_name(combos, name, combo["id"])
    combo["loop"] = bool(loop)
    combo["steps"] = steps
    ok = _dump_store(combos=combos)
    if not ok:
        log("保存弱网组合失败（存储位置均不可写）")
    return combo, combos


def delete_combo(cid):
    """删除一个组合，返回 (是否删掉, 剩余列表)"""
    combos = get_combos()
    cid = str(cid)
    left = [c for c in combos if c["id"] != cid]
    if len(left) == len(combos):
        return False, combos
    G.combos = left
    ok = _dump_store(combos=left)
    if not ok:
        log("删除弱网组合后写盘失败（改动仅存在于本次运行）")
    return True, left


def combo_summary(combo):
    """一行说明：5G 0:30 → 4G 弱网 1:00 → …（界面列表/日志用）"""
    parts = []
    for s in combo.get("steps", []):
        parts.append("%s %s" % (scene_name(s["key"]), step_text(s)))
    tail = "（循环）" if combo.get("loop") else ""
    return " → ".join(parts) + tail


# --------------------------------------------------------------------------
# 共享契约：一段里引用的场景，对方机器上未必存在
#
# 组合里的每一段记的是「场景 key + 时长」。自定义 key 长这样 u:8f3a —— 这个 id 是
# 我这台机器存盘时现编的，推给别人时对方那一栏里根本没有这个 id，直接照搬过去会
# 解析成「已删除的场景」，组合看着收下了、播起来每段都是空的。所以：
#   自定义段 → 连同名字与整份参数快照一起推（与 items 里的自定义环境一个道理）；
#   内置段   → 带上名字，对方版本里没这一档时能把话说清楚而不是静默丢掉。
# --------------------------------------------------------------------------

def share_steps(combo):
    """组合内的段 → 可推送的形态（每段补齐 scene 名，自定义段额外带 profile）"""
    out = []
    for s in (combo or {}).get("steps", []):
        key = str(s.get("key") or "").strip()
        if not key:
            continue
        item = {"key": key,
                "minutes": max(0, int(s.get("minutes", 0) or 0)),
                "seconds": max(0, int(s.get("seconds", 0) or 0))}
        try:
            prof = scene_profile(key)
        except Exception:
            prof = None
        # 这是「段里引用的场景显示名」，不是用户给组合起的名字：内置预设
        # 最长 14 字（弱 Wi-Fi（隔墙/远端）），不能按 NAME_MAX 截，否则共享
        # 到对端按名字反查自定义场景时会对不上。只防脏数据撑爆协议包。
        item["scene"] = str((prof or {}).get("name") or key)[:40]
        if key.startswith(USER_PRESET_KEY_PREFIX):
            item["profile"] = dict(prof) if prof else None
        out.append(item)
    return out


def _share_clean(name, fallback=""):
    """共享段里的场景标签：压空白 + 协议限长（40）。

    注意不是「组合取名」——内置预设显示名最长 14 字，这里必须留够；
    用户给场景/组合起的名字一律走 limit_item_name（NAME_MAX）。
    """
    s = " ".join(str(name or "").split())[:40]
    return s or fallback


def _find_combo_by_name(name):
    target = str(name or "").strip().lower()
    for c in get_combos():
        if c["name"].strip().lower() == target:
            return c
    return None


def _resolve_shared_step(step):
    """把别人推来的一段映射到本机记录。（key, None）表示可用，丢段返回 (None, 原因)。

    这里会发生写库：对面用到的自定义场景我们这边没有时，按快照在本地建一条同名
    自定义场景 —— 组合是「一段引用某场景」，场景本体不落地这一段就无从播放。
    """
    key = str((step or {}).get("key") or "").strip()
    if not key or key.startswith(COMBO_KEY_PREFIX):
        return None, "嵌套组合"
    nm = _share_clean((step or {}).get("scene") or (step or {}).get("name"))
    snapshot = step.get("profile") if isinstance(step.get("profile"), dict) else None
    if key.startswith(USER_PRESET_KEY_PREFIX):
        if nm:
            it = _find_preset_by_name(nm)
            if it is not None:
                return USER_PRESET_KEY_PREFIX + it["id"], None
        if snapshot:
            nm = nm or "对方的环境"
            newit, _all = upsert_user_preset(nm, _norm_user_profile(nm, snapshot))
            return USER_PRESET_KEY_PREFIX + newit["id"], None
        return None, "缺场景参数"
    try:
        scene_profile(key)          # 本版本是否有这一档内置预设
        return key, None
    except Exception:
        pass
    # 版本差异（对方比我们新）：退一步按名字找本地同类场景，还找不到才丢
    if nm:
        it = _find_preset_by_name(nm)
        if it is not None:
            return USER_PRESET_KEY_PREFIX + it["id"], None
    return None, "本机没有「%s」这一档" % (nm or key)


def _resolve_shared_steps(steps):
    """整份组合的段做本机映射，返回 (可用段, 被丢掉的说明列表)"""
    out, dropped = [], []
    for i, s in enumerate(steps or [], 1):
        if not isinstance(s, dict):
            continue
        key, why = _resolve_shared_step(s)
        if key is None:
            dropped.append("第%d段（%s）" % (i, why))
            continue
        out.append({"key": key,
                    "minutes": max(0, min(STEP_MAX_MINUTES, int(s.get("minutes", 0) or 0))),
                    "seconds": max(0, min(STEP_MAX_SECONDS, int(s.get("seconds", 0) or 0)))})
    return out, dropped


# --------------------------------------------------------------------------
# 全局状态
# --------------------------------------------------------------------------

class G:
    profile = default_profile()
    preset_key = "direct"
    # 限速总开关：启动后默认关闭（False）——应用只是待命，不施加任何弱网参数，
    # 流量原样转发。用户必须显式开启（点「开启限速」或选一个场景）才生效。
    limit_on = False
    last_profile = None                # 最后一次生效过的场景（「再次开启」时复原用）
    gate_open = True
    events = deque(maxlen=300)
    event_seq = 0               # 事件序号：deque 满了以后 len 不再变化，界面靠它判断有没有新事件
    conns_total = 0
    conns_active = 0
    bytes_up = 0
    bytes_down = 0
    active = set()            # 在途 writer，整体断网时掐断
    contexts = set()          # 在途连接上下文，空闲回收用（ConnCtx.register/cleanup 维护）
    flap_task = None
    proxy_server = None
    proxy_host = "127.0.0.1"
    proxy_port = DEFAULT_PROXY_PORT
    ctrl_port = DEFAULT_CTRL_PORT
    lan_ip = "127.0.0.1"
    started_at = time.time()
    gen = 0                     # 参数代数：每次切换场景 +1，用于唤醒在途连接
    wake = None                 # asyncio.Event：参数切换时唤醒挂起（黑洞/卡顿）的连接
    # set_profile 的调用代数：await G.flap_task 期间若别的请求又换了场景，
    # 旧调用凭这个计数器发现自己过期，不能用旧 prof 覆盖新的状态收尾。
    profile_gen = 0
    # 原先散落在模块级的几个可变状态收进 G，随应用生命周期统一管理/复位。
    proc_name_cache = {"at": 0.0, "map": {}}   # pid -> exe 名，带 TTL
    prev_proxy = None           # 首次接管前备份的原始系统代理值，解除时还原
    sysproxy_on = False         # 当前是否是本工具开启的系统代理
    # 接管范围：all=全部程序 / only=仅下列程序 / exclude=排除下列程序
    scope_mode = "all"
    scope_list = []             # exe 名（小写）列表
    scope_seen = {}             # 观测到的进程名 -> 次数（便于用户选择要接管谁）
    # 共享 PIN 失败退避：peer_ip -> {"n": 次数, "until": monotonic 解锁时刻}。
    # 4 位 PIN 只有 1 万种组合，不限速的话同网段几分钟就能穷举完。
    share_fail = {}
    bytes_bypass = 0            # 未接管程序的直通流量（不计入弱网统计）
    # 全局令牌桶：上下行各一份，所有连接共享。
    # ——必须是全局的。早先每条连接各建一个桶，结果开 8 条并发下载能跑出设定带宽的
    # 2.6 倍：带宽是链路级的资源，8 条连接只是分同一条链路，不该各自拿一份配额。
    bucket_up = None
    bucket_down = None
    user_presets = None         # 自定义栏（用户保存的场景），None = 尚未从磁盘加载
    combos = None               # 弱网组合（多场景按序播放），None = 尚未从磁盘加载
    combo_task = None           # 正在播放的组合（asyncio.Task），没有则为 None
    combo_id = None             # 正在播放/暂停在手的组合 id
    combo_step = -1             # 当前播到第几段（0 起）
    combo_deadline = 0          # 当前段什么时候结束（time.time()，界面倒计时用）
    # ---- 环境共享 ----
    self_id = ""                # 本机标识（区分广播是不是自己发的）
    user_name = ""              # 局域网上显示的名字
    peers = {}                  # peer id -> {id,name,ip,platform,share,at}
    inbox = {}                  # 收到的同名环境，等本地决定保留 / 覆盖
    share_port = 0              # 共享服务端口（0 = 未开启）
    share_server = None
    discover_listen = False     # 是否成功监听了 UDP 广播
    share_pin = ""              # 4 位共享 PIN：推送方必须带对，否则 403
    allow_legacy = False        # 是否接受 ver=1（无 PIN）旧版推送，且只进收件箱


BYPASS_PROFILE = default_profile("（未接管 · 直通）")


def scope_hit(pname):
    """判断某个进程是否在接管范围内。
    pname 为 None（反查不到进程名）时的策略：
      - only 模式：不接管（用户明确指定了名单，查不到就不该算进去）
      - exclude 模式：接管（只排除明确列出的，查不到的一律照常接管）"""
    mode = G.scope_mode
    if mode == "all":
        return True
    if pname is None:
        return mode == "exclude"
    in_list = pname in G.scope_list
    return in_list if mode == "only" else (not in_list)


def set_scope(mode, names):
    """更新接管范围。返回规范化后的 (mode, list)。"""
    if mode not in ("all", "only", "exclude"):
        mode = "all"
    lst = []
    for n in (names or []):
        n = str(n).strip().lower()
        if n and n not in lst:
            lst.append(n)
    G.scope_mode = mode
    G.scope_list = lst
    return G.scope_mode, G.scope_list


def log_event(etype, msg):
    """记一条事件：进内存 deque（GUI/接口实时看）+ 缓冲日志（落盘，事后排查用）"""
    G.event_seq += 1
    G.events.append({"seq": G.event_seq, "t": time.strftime("%H:%M:%S"),
                     "type": etype, "msg": msg})
    try:
        if _LOG_SINK is None:
            globals()["_LOG_SINK"] = _LogSink(LOG_FILE)
        _LOG_SINK.write("[" + etype + "] " + msg)
    except Exception:
        pass


async def nap(seconds, gen):
    """可被「切换场景 / 停止模拟」立即打断的 sleep。
    返回 True 表示被参数切换打断（调用方应立即按新参数继续）。

    这里等满 remain 而不是每 0.25s 轮询一次：唯一的 gen 递增点
    （select_scene）紧接着就会 G.wake.set()，事件本身就把挂起的连接唤醒了，
    轮询帽纯属空转 —— 100 个黑洞连接挂 600s 时，它每秒白醒 400 次。
    """
    if G.gen != gen:
        return True
    end = time.monotonic() + max(0.0, seconds)
    while True:
        if G.gen != gen:
            return True
        remain = end - time.monotonic()
        if remain <= 0:
            return False
        w = G.wake
        if w is None:                       # 服务尚未初始化，退化为普通 sleep
            await asyncio.sleep(remain)
            continue
        try:
            await asyncio.wait_for(w.wait(), timeout=remain)
        except (asyncio.TimeoutError, TimeoutError):
            continue
        except Exception:
            await asyncio.sleep(remain)


# --------------------------------------------------------------------------
# Windows 系统代理一键接管（免手动设置）
# --------------------------------------------------------------------------

def sysproxy_enabled():
    if sys.platform != "win32":
        return False
    try:
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        v, _ = winreg.QueryValueEx(k, "ProxyEnable")
        return bool(v)
    except Exception:
        return False


def _auto_release_sysproxy():
    """退出时自动解除接管，避免工具被关掉后系统代理指向已失效的端口"""
    if G.sysproxy_on:
        try:
            set_sysproxy(False)
            log("[WeakNet] 已自动解除系统代理接管")
        except Exception as e:
            log("[WeakNet] 自动解除系统代理接管失败：%r" % (e,))


def _reg_values(k, names):
    import winreg
    out = {}
    for n in names:
        try:
            v, _t = winreg.QueryValueEx(k, n)
            out[n] = (v, _t)
        except Exception:
            out[n] = None
    return out


def set_sysproxy(enable):
    """开启/关闭本机系统代理（写入注册表并通知系统立即生效，解除时还原原始配置）"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        import ctypes
        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        names = ("ProxyEnable", "ProxyServer", "ProxyOverride")
        # 需要同时具备读(备份原值)与写(写入新值)权限
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                           winreg.KEY_READ | winreg.KEY_WRITE)
        if enable and G.prev_proxy is None:
            G.prev_proxy = _reg_values(k, names)
        if enable:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ,
                              f"127.0.0.1:{G.proxy_port}")
            winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ,
                              "localhost;127.*;<local>")
        else:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            if G.prev_proxy:                       # 还原接管前的配置
                for n in names:
                    saved = G.prev_proxy.get(n)
                    if saved:
                        winreg.SetValueEx(k, n, 0, saved[1], saved[0])
            G.prev_proxy = None
        winreg.CloseKey(k)
        ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)  # SETTINGS_CHANGED
        ctypes.windll.wininet.InternetSetOptionW(0, 37, 0, 0)  # REFRESH
        G.sysproxy_on = bool(enable)
        return True
    except Exception as e:
        # 原来这里裸 except 直接 return False：注册表写失败时用户只看到
        # 「接管没生效」，日志里一点线索都没有。保留 False 的返回语义，只补日志。
        log("[WeakNet] %s系统代理失败：%r" % ("开启" if enable else "解除", e))
        return False


def release_stale_sysproxy(proxy_port=None):
    """启动时清掉「上次崩溃 / 被 taskkill 留下的系统代理」。

    正常退出走 atexit（CLI）或窗口关闭（GUI）还原；被强杀时那段代码根本不会
    执行，注册表里就留下 ProxyEnable=1 + ProxyServer=127.0.0.1:8888 —— 指向
    一个已经没有进程在听的端口，表现为整机断网，用户很难看出原因。
    启动时发现它指向本程序的端口就先还原，并留一行日志说明。

    两道保险避免误伤正在运行的实例：端口上还有人监听时不动（那是活着的
    WeakNet），端口传 0（由系统分配，测试/嵌入式用法）时也不动。
    非 Windows 直接跳过，不 import winreg，也不新增任何依赖。
    """
    if sys.platform != "win32":
        return False
    try:
        port = int(proxy_port or 0)
    except (TypeError, ValueError):
        return False
    if port <= 0:
        return False
    try:
        import winreg
        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        try:
            enabled, _t = winreg.QueryValueEx(k, "ProxyEnable")
            try:
                server, _t = winreg.QueryValueEx(k, "ProxyServer")
            except OSError:            # 有 ProxyEnable 但没有 ProxyServer
                server = ""
        finally:
            winreg.CloseKey(k)
    except Exception:
        return False
    if not enabled or ("127.0.0.1:%d" % port) not in str(server or ""):
        return False
    if not can_bind(port, "127.0.0.1"):
        # 端口仍被占用：多半是另一个实例在跑，它的接管是有效的，别动
        return False
    ok = set_sysproxy(False)
    log("[WeakNet] 检测到上次异常退出残留的系统代理（%s），已还原" % server)
    log_event("config", "检测到上次异常退出残留的系统代理，已还原")
    return ok


def detect_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# --------------------------------------------------------------------------
# 限速令牌桶
# --------------------------------------------------------------------------

class Bucket:
    """令牌桶。速率随当前场景实时变化（切场景/停止后已在途的连接也会立即跟上）

    桶容量（允许的最大突发）取 BURST_SEC 秒的配额。不能给到 1 秒：桶是全局共享的，
    空闲一段时间后会攒满，一个 4MB 的下载能一口气吃掉整秒配额冲出去，
    测出来的速率就变成设定值的两倍多 —— 看着像限速失效。真实链路也不会这样，
    TCP 自己的初始突发也就几十 KB。
    """
    __slots__ = ("rate", "tokens", "last", "lock", "kbps")
    BURST_SEC = 0.25

    def __init__(self, kbps=0):
        self.rate = 0.0
        self.tokens = 0.0
        self.last = time.monotonic()
        self.lock = asyncio.Lock()
        self.kbps = -1
        self.sync(kbps)

    def sync(self, kbps):
        """按当前场景带宽更新速率（0 = 不限）"""
        if kbps == self.kbps:
            return
        self.kbps = kbps
        self.rate = kbps * BITS_PER_KBPS / 8.0 if kbps else 0.0   # kbps → B/s
        self.tokens = min(self.tokens, self.cap()) if self.rate else 0.0

    def cap(self):
        return self.rate * self.BURST_SEC if self.rate else 0.0

    async def consume(self, n):
        """按字节消耗令牌。注意：单次数据量可能远大于 1 秒的配额（例如 2G 下读取
        64KB），必须分多次扣减，否则 'tokens >= n' 永不成立会死循环。

        睡眠必须在锁外：asyncio.Lock 不像 Java 监视器那样在等待时释放，
        在锁里 sleep 会让其他连接的 consume 全部堵住（慢档位下尤其明显），
        用户点「停止」时排队的连接也要等当前睡眠者醒来才能看到 rate == 0。
        锁内只做「补 token + 预扣」，预扣语义保留 —— 并发总速率不超标靠的就是它。
        """
        if not self.rate:
            return
        remain = n
        while remain > 0:
            async with self.lock:
                if not self.rate:
                    # 限速被取消（切换直连 / 停止模拟）：立刻放行
                    self.tokens = 0.0
                    return
                now = time.monotonic()
                # 令牌上限 = 桶容量（短暂突发可以，攒到整秒不行）
                self.tokens = min(self.tokens + (now - self.last) * self.rate, self.cap())
                self.last = now
                take = min(remain, self.tokens)
                if take > 0:
                    self.tokens -= take
                    remain -= take
                    if remain <= 0:
                        return
                # 还要等剩下的字节补齐，单次最多等 0.25s，避免长时间独占
                wait_s = remain / self.rate
            await asyncio.sleep(min(max(wait_s, 0.001), 0.25))


# --------------------------------------------------------------------------
# 连接上下文
# --------------------------------------------------------------------------

class Abort(Exception):
    """连接被主动掐断"""


def abort_writer(w):
    try:
        w.transport.abort()
    except Exception:
        pass
    try:
        w.close()
    except Exception:
        pass


class ConnCtx:
    def __init__(self, label="?"):
        self.label = label
        self.writers = []
        self.fixed = False     # True = 直通连接：参数固定，不随场景切换变化
        self.bypassed = False  # True = 未接管（直通）：flap 断网不掐它
        # 出生时还没做完进程名反查（那里有 await），此时既不能算「接管」
        # 也不能算「直通」。flap 黑窗必须跳过未判定的 ctx，否则会把马上要被
        # 判为直通的连接误掐掉。handle_proxy 判定完成后置 False。
        self.bypass_pending = True
        self.prof = None       # fixed 时使用的固定 profile（BYPASS_PROFILE）
        self.last_active = time.monotonic()   # 最后一次有数据流动，供空闲回收
        # 一出生就登记：否则「请求头读到一半就晾着」的连接在 register() 之前
        # 不存在于 G.contexts，空闲回收看不见它，30s 头超时到了也不会被关掉。
        G.contexts.add(self)

    def touch(self):
        self.last_active = time.monotonic()

    def register(self, w):
        if w not in self.writers:      # 同一 writer 可能被客户端侧/上游侧各登记一次
            self.writers.append(w)
        G.active.add(w)
        G.contexts.add(self)

    def unregister(self, w):
        """重连时摘除旧的上游 writer。

        keep-alive 会多次重连，只 register 不摘除的话 ctx.writers / G.active
        会一直累积已死的 writer，直到连接结束才 cleanup —— 长时间打开的
        浏览器页面能让这个列表涨到几十项。
        """
        if w in self.writers:
            self.writers.remove(w)
        G.active.discard(w)

    def abort_all(self):
        for w in self.writers:
            G.active.discard(w)
            abort_writer(w)
        # 已经拆了就别再占着回收集合：否则空闲回收会每 60s 反复 abort 同一批死连接
        G.contexts.discard(self)

    def cleanup(self):
        for w in self.writers:
            G.active.discard(w)
            try:
                w.close()
            except Exception:
                pass
        G.contexts.discard(self)


def _latency_delay(prof, direction):
    base = prof["latency_up_ms"] if direction == "up" else prof["latency_down_ms"]
    jit = prof["jitter_ms"]
    if base <= 0:
        # 没有基础延迟时，「抖动」只能表现为 0~jit 的正向离散（不存在负延迟）
        return (random.uniform(0, jit) / 1000.0) if jit > 0 else 0.0
    # 抖动幅度不能超过基础延迟：base + (-jit) 会算出负延迟，而物理上不存在「比 0 还快」。
    # 旧实现直接 max(0, ...) 压成 0，于是 jitter > base 时会有一批包变成零延迟
    # （比直连还快，实测占 10%），同时把均值抬高（160ms 的场景均值到 198ms）。
    # 把幅度夹到 base 后：分布落在 [0, 2·base]，均值仍等于 base，零延迟只剩测度 0 的端点。
    jit = min(jit, base)
    return max(0.0, (base + random.uniform(-jit, jit)) / 1000.0)


class DelayLine:
    """延迟投递队列：把「延迟」和「带宽」解耦。

    最省事的写法是在转发每块数据前 sleep 一下，但那样同一时刻只有一块数据在途，
    吞吐会退化成「1 块 / RTT」——64KB 的块配 700ms 延迟只剩 0.7Mbps，而真实弱网
    是靠拥塞窗口让多个包同时在途的。所以这里改成：数据块按到期时间入队，由独立的
    投递协程按期写出，读端立刻回去读下一块。

    队列有上限，积压满了就卡住读端，压力沿 TCP 窗口传回发送方，不会把内存撑爆。
    投递等待用的是可打断的 nap()：点了停止 / 切场景，积压的数据立刻放行。
    """

    MAX_BLOCKS = 64             # 在途块数上限（64KB × 64 ≈ 4MB），兼作背压阈值

    __slots__ = ("writer", "q", "task", "pending", "label", "dead")

    def __init__(self, writer, label=""):
        self.writer = writer
        self.q = asyncio.Queue(maxsize=self.MAX_BLOCKS)
        self.task = None
        self.pending = 0
        self.label = label
        self.dead = False       # 投递协程已退出：此后 submit 直接丢弃，绝不再阻塞

    def start(self):
        self.task = asyncio.create_task(self._run())
        return self

    def _idle(self):
        """没有「排在自己前面、但还没真正写出」的数据 —— 此时才配走零延迟快路径"""
        return self.pending == 0 and self.q.empty()

    def _peer_alive(self):
        """写目标是否已经不可写。

        asyncio 的 transport 在连接丢失后并不禁止 `write()`：它把数据静默丢掉，
        并在累计超过 LOG_THRESHOLD_FOR_CONNLOST_WRITES 后每调用一次就打一条
        `socket.send() raised exception.` 警告。iperf3 / 下载客户端提前断开时，
        投递协程若不管，会对着死连接把整个积压队列空转完 —— 实测一轮 20s 的
        测量能刷出上百条警告，同时白占限速桶和 CPU。

        `write_eof()`（半关闭）在 Windows proactor / selector 上都只做
        `shutdown(SHUT_WR)`，不会置 `_closing`，所以这里不会误伤「一端发完
        FIN、另一端还要继续回数据」的正常场景；只有 close()/abort()/连接被
        RST 触发的 _force_close() 才会置 _closing 并累加 _conn_lost。

        测试替身（tests/test_unit_delayline.py 的 _Writer）没有这两个属性，
        此时按「可写」处理，保持原有单测语义。
        """
        w = self.writer
        closing = getattr(w, "is_closing", None)
        if closing is not None:
            try:
                if closing():
                    return False
            except Exception:
                return False
        t = getattr(w, "transport", None)
        if t is not None and getattr(t, "_conn_lost", 0):
            return False
        return True

    async def _flush(self, data):
        """写出一块，然后等 drain。

        这里曾经改成「写缓冲超过 DRAIN_HIGH 才 drain」，理由是每块 drain() 会多
        一次协程调度。受控 A/B 实测没有收益（8000 / 50000 kbps 两档交替顺序：
        −1.52% / +0.43%，64MB 环回 B/A=1.000×），而 drain() 在缓冲没堆积时本来就
        立即返回 —— 省下的调度开销小到测不出来，反而多了一次
        get_write_buffer_size() 与一个分支。证据不支持就退回无条件 drain：
        语义更简单，背压行为也和 asyncio 文档一致。
        """
        if not self._peer_alive():
            raise ConnectionError("writer already closed")
        self.writer.write(data)
        await self.writer.drain()

    def _drain(self):
        """丢弃排队中的数据并把 pending 归零计数。

        队列满时生产者阻塞在 q.put 上；get_nowait 会唤醒等待中的 putter，
        所以「消费者退出后生产者永久挂起」这条路在这里被拆掉。
        """
        while True:
            try:
                item = self.q.get_nowait()
            except asyncio.QueueEmpty:
                return
            if item is not None:
                self.pending = max(0, self.pending - len(item[0]))

    @staticmethod
    def _schedule_reap(t):
        """同步上下文里取消任务后，排队一个独立协程把它「等完」。

        `_mark_dead()` 是同步方法，没法 await；只 cancel 不 await 会留下
        pending Task，事件循环一关就打 `Task was destroyed but it is pending!`。
        这里用 create_task 补上收尸，且不吞掉调用方自己的取消语义。
        """
        if t is None or t.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _swallow():
            try:
                await DelayLine._reap(t)
            except BaseException:
                pass

        loop.create_task(_swallow())

    def _mark_dead(self):
        """所有退出路径的唯一出口：置位、清队列、唤醒生产者、取消投递协程"""
        if self.dead:
            return
        self.dead = True
        self._drain()
        t = self.task
        if t is not None and not t.done() and t is not asyncio.current_task():
            t.cancel()
            self._schedule_reap(t)

    def abort(self):
        """连接已被掐断，积压数据不必再投递。

        必须先 mark_dead 再 cancel：只 cancel 的话，正卡在 q.put 上的生产者
        不会被唤醒，aclose 里那句 put 也就永远等不到空位。
        """
        self._mark_dead()

    async def submit(self, data, delay, gen):
        # 生产端也要检查：消费者死掉后若只靠「submit 直接丢弃」，pipe 仍会
        # 一直从上游读、一路丢到 EOF，限速桶被无意义占用。这里收敛成一次
        # ConnectionError，让 run_tunnel 走 hard_stop 把整条连接拆掉。
        # abort() 必须在 raise 之前，确保卡在满队列 q.put 上的生产者被唤醒。
        # 检查放在 dead 之前：消费者因对端断开而退出时 dead 已置位，也必须
        # 让生产者停下来，而不是继续静默丢弃。
        if not self._peer_alive():
            self.abort()
            raise ConnectionError("writer already closed")
        if self.dead:
            return
        if delay <= 0 and self._idle():
            # 零延迟且无在途数据：直接写。直连档靠这条快路径保持满速，不被队列调度拖慢
            await self._flush(data)
            return
        self.pending += len(data)
        await self.q.put((data, time.monotonic() + delay, gen))
        if self.dead:
            # 入队期间消费者退出：本块不会再有人投递，立即回收计数
            self._drain()

    async def _run(self):
        try:
            while True:
                item = await self.q.get()
                if item is None:                 # 结束哨兵
                    return
                data, due, gen = item
                wait = due - time.monotonic()
                if wait > 0:
                    await nap(wait, gen)         # 切场景 / 停止时立刻放行
                try:
                    await self._flush(data)
                except (ConnectionError, OSError):
                    return
                finally:
                    self.pending = max(0, self.pending - len(data))
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        finally:
            # 包括 CancelledError 在内的所有退出路径都要收敛到这里，
            # 否则生产者会卡在满队列的 put 上、aclose 会卡在哨兵 put 上。
            self._mark_dead()

    @staticmethod
    async def _reap(t):
        """取消消费者任务并**等它真正结束**。

        只 `cancel()` 不 `await` 会留下一个 pending 的 Task；事件循环随后一关
        （或 GC 一回收）就打 `Task was destroyed but it is pending!`。iperf3
        `-P 8` 每轮收尾必现，这是连接级任务泄漏而不是无害的日志噪音。
        限时 1s 兜底，避免卡在 drain() 上的消费者把连接拆除流程拖死。
        """
        if t.done():
            return
        t.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(t, return_exceptions=True), 1.0)
        except (asyncio.TimeoutError, TimeoutError):
            return
        except asyncio.CancelledError:
            # 调用方自己正在被取消：不吞掉取消语义，直接向上传播
            raise

    async def aclose(self, timeout=None):
        """排空队列后再返回。

        少了这一步，连接结束时最后几块数据会被丢掉 —— 短连接（HTTP、curl）尤其明显：
        响应尾部还在队列里，读端已经 EOF 返回，对端一个字节都收不到。

        timeout=None 时按积压量估算：慢档位（如电梯 256kbps）下 4MB 积压需要
        125s 才能交付完，用固定 30s 会把响应尾部整段丢掉（下载总是差最后一段）。
        """
        t = self.task
        if t is None:
            return
        self.task = None
        if self.dead or t.done():
            # 消费者已经没了：没有排空的可能，立刻返回（旧实现在这里会永久阻塞）
            self._drain()
            await self._reap(t)
            return
        if timeout is None:
            # 64KB 一块、每块至少留 30s 余量；速率信息由 pipe 通过 rate_kbps 传入更准，
            # 这里用保守的块数估算兜底（调用方一般会显式传 timeout）
            timeout = max(30.0, self.pending / 32768.0 + 30.0)
        deadline = time.monotonic() + timeout
        try:
            # 哨兵入队也必须限时：消费者若正好在这期间死掉，put 会一直等空位
            await asyncio.wait_for(self.q.put(None), timeout=min(15.0, timeout))
            await asyncio.wait_for(t, timeout=max(0.01, deadline - time.monotonic()))
        except (asyncio.TimeoutError, TimeoutError):
            self._mark_dead()
            await self._reap(t)
        except BaseException:
            # 调用方（pipe）本身正被取消，这里 await 会立刻再抛 CancelledError
            # 并掩盖原异常。_mark_dead() 会用 create_task 排队收尸，既保住取消
            # 语义，也不会留下 pending Task。
            self._mark_dead()
            raise


# --------------------------------------------------------------------------
# 双向管道：延迟 + 抖动 + 限速 + 丢包卡顿 + 随机断连
# --------------------------------------------------------------------------

async def pipe(reader, writer, prof, direction, ctx):
    """注意：每块数据都重新读取 G.profile，保证「停止模拟 / 切换场景」对已在途的连接立即生效"""
    # 代数不能在连接建立时捕获一次就不变：那样切场景后新数据块仍带旧 gen，
    # DelayLine._run 里 nap(wait, 旧gen) 见 G.gen 已变就立即放行，延迟/丢包
    # 停顿对新块全部失效（表现为「界面说慢、正文全速」）。改为每次调用
    # nap/dl.submit 时读当代 G.gen：旧块仍按入队时的代数被切场景放行，
    # 新块带当代代数按新参数注入。
    # 下面 rate_key 必须在循环外定义：finally 里要用它估算排空超时。旧实现放在循环体内，
    # 连接若在首次 read() 就 EOF / 抛异常，finally 会触发 UnboundLocalError，
    # 于是 aclose() 根本没机会执行，DelayLine 的投递协程被整体遗弃
    # （iperf3 -P 8 收尾时成片 `Task was destroyed but it is pending!`）。
    rate_key = "up_kbps" if direction == "up" else "down_kbps"
    dl = DelayLine(writer, ctx.label).start()
    try:
        while True:
            try:
                data = await reader.read(65536)
            except (ConnectionError, OSError):
                return
            if not data:
                return
            n = len(data)
            ctx.touch()                     # 有数据流动：刷新空闲计时
            if ctx.fixed:                          # 直通流量不计入弱网统计
                G.bytes_bypass += n
            elif direction == "up":
                G.bytes_up += n
            else:
                G.bytes_down += n
            # 直通连接用固定 profile，其余实时跟随 G.profile（切场景后立即生效）。
            # 必须用 ctx 而非全局：pipe 是并发的，多条连接同时跑，全局会互相串味。
            prof = ctx.prof if ctx.fixed else G.profile
            if not ctx.fixed:
                # 全局共享令牌桶：一条链路一份带宽，并发连接分摊而不是各拿一份。
                # 直通连接不参与（否则它会把全局桶的速率刷成 0，等于给所有人解限速）。
                bucket = G.bucket_up if direction == "up" else G.bucket_down
                if bucket is None:                 # 服务未初始化时的兜底
                    bucket = Bucket(prof.get(rate_key, 0))
                else:
                    bucket.sync(prof.get(rate_key, 0))   # 停止/放宽限速后立即恢复全速
                await bucket.consume(n)
            # 丢包 -> 表现为一段时间的传输停顿（TCP 重传效果）
            if prof["loss_pct"] and random.random() * 100 < prof["loss_pct"]:
                await nap(prof["stall_ms"] / 1000.0 * (0.5 + random.random()), G.gen)
                prof = G.profile
            # 随机断连 -> 立即掐断整条连接（两侧 RST/FIN）
            if prof["disconnect_pct"] and random.random() * 100 < prof["disconnect_pct"]:
                log_event("disconnect", "模拟断连：" + ctx.label)
                ctx.abort_all()
                raise Abort()
            # 延迟：每块数据都注入，而不是只在建连时注入一次。
            # 只在建连时等一次的话，HTTP keep-alive / 视频流 / WebSocket 这类
            # 长连接里除了第一个请求，后面的数据全是零延迟，延迟参数等于没设。
            await dl.submit(data, _latency_delay(prof, direction), G.gen)
    except Abort:
        dl.abort()
        raise
    finally:
        # 离开前必须排空延迟队列，否则响应尾部的最后几块数据会被丢。
        # 超时必须按「积压字节 / 当前速率」算：固定 30s 在慢档位（256kbps 下
        # 4MB 积压需 125s）会把尾部整段丢掉。直连/未限速时给 30s 兜底即可。
        rate_kbps = prof.get(rate_key, 0) if not ctx.fixed else 0
        if rate_kbps and dl.pending:
            need = dl.pending * 8.0 / (rate_kbps * BITS_PER_KBPS) + 30.0
        else:
            need = 30.0
        await dl.aclose(timeout=max(30.0, min(need, 600.0)))


def task_failed(t):
    """已结束的 pipe task 是否因异常失败；被取消不算失败（那是正常收尾）。

    ``Task.exception()`` 是**返回**异常对象而不是抛出它，只有取消/未完成时
    才会抛 CancelledError / InvalidStateError。以前用 try/except 包住它判断
    失败，导致 pipe 抛 Abort / ConnectionError 时一律被当成正常 EOF。这里
    单独成纯函数，便于无网络单测直接验证三种结束状态。
    """
    if t.cancelled():                 # CancelledError 是 BaseException，必须先判
        return False
    return t.exception() is not None


async def run_tunnel(ctx, c_reader, s_writer, s_reader, c_writer, prof):
    """双向转发。

    一端收到 FIN 只代表「这个方向的数据发完了」：把 FIN 转给对端，另一个方向
    必须继续跑到 EOF。早期实现是 FIRST_COMPLETED 后直接 cancel 另一端，客户端
    一发完请求就半关闭（curl / nc / 带 body 的 POST 都会这样）时响应会被整条
    砍掉——实测 0 字节、2ms 内 EOF，普通 HTTP 转发完全不可用。
    """
    up = asyncio.create_task(pipe(c_reader, s_writer, prof, "up", ctx))
    down = asyncio.create_task(pipe(s_reader, c_writer, prof, "down", ctx))
    # task -> 该方向结束时应当半关闭的对端 writer
    peer_of = {up: s_writer, down: c_writer}
    pending = {up, down}
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED)
            hard_stop = False
            for t in done:
                if t.cancelled():
                    continue
                # 与单测共用同一份判定：内联复制过一份时，改了测试锚点也不会
                # 触发生产逻辑的回归（影子测试）。done 里的 task 均已结束，
                # task_failed 调 exception() 不会抛 InvalidStateError。
                if task_failed(t):
                    hard_stop = True        # Abort / 连接错误：拆掉整条连接
                else:
                    # 正常 EOF：FIN 转给对端。此时 pipe 的 finally 已排空延迟队列，
                    # FIN 不会插到数据前面。
                    try:
                        peer_of[t].write_eof()
                    except Exception:
                        pass
            if hard_stop or not pending:
                break
            # 另一端通常会跟着结束；挂死的 keep-alive 客户端到点也拆，防连接泄漏
            # 但不能无条件拆：源站持续发数据、只是 60s 内没 EOF 时（如 256kbps
            # 下 2MB 响应约 64s），无条件 break 会把响应尾部整段吞掉。仅在
            # 这个方向上确实没有数据流动、空闲超过 60s 时才当挂死连接处理。
            done2, pending = await asyncio.wait(
                pending, timeout=60, return_when=asyncio.FIRST_COMPLETED)
            if not done2:
                if time.monotonic() - ctx.last_active > 60:
                    break
                continue
    finally:
        for t in (up, down):
            if not t.done():
                t.cancel()
        await asyncio.gather(up, down, return_exceptions=True)


# --------------------------------------------------------------------------
# 明文 HTTP 的 keep-alive 转发
# --------------------------------------------------------------------------

def _rewrite_origin_request(method, path, headers):
    """把代理格式请求改写成源站格式（去代理头，声明 keep-alive）"""
    out = [f"{method} {path} HTTP/1.1".encode()]
    for h in headers:
        k = h.split(b":", 1)[0].strip().lower()
        if k in (b"proxy-connection", b"connection", b"keep-alive"):
            continue
        out.append(h.rstrip(b"\r\n"))
    out.append(b"Connection: keep-alive")
    return b"\r\n".join(out) + b"\r\n\r\n"


def _hdr_get(headers, name):
    """取请求/响应头（bytes，小写比较）。多个同名取第一个。"""
    for h in headers:
        if h.split(b":", 1)[0].strip().lower() == name:
            return h.split(b":", 1)[1].strip()
    return None


def _resp_frame(status, resp_headers, method):
    """响应体怎么算读完。返回 (mode, length)：
    mode: 'nobody' 无体 / 'cl' 定长 / 'chunked' 分块 / 'eof' 读到 EOF 为止。
    只有边界明确才能复用上游连接 —— 'eof' 意味着这一响应读完上游就该关。"""
    if method == "HEAD" or status in (204, 304) or 100 <= status < 200:
        return "nobody", 0
    te = (_hdr_get(resp_headers, b"transfer-encoding") or b"").lower()
    if b"chunked" in te:
        return "chunked", 0
    cl = _hdr_get(resp_headers, b"content-length")
    if cl is not None:
        try:
            return "cl", max(0, int(cl.split(b",")[0]))
        except ValueError:
            return "eof", 0
    return "eof", 0


def _resp_keepalive(status_line, resp_headers, mode):
    """这个响应读完后，上游连接还能给下一个请求用吗"""
    if mode == "eof":
        return False
    conn = (_hdr_get(resp_headers, b"connection") or b"").lower()
    if b"close" in conn:
        return False
    if status_line.startswith(b"HTTP/1.0") and b"keep-alive" not in conn:
        return False
    return True


def _client_keepalive(headers):
    """客户端是否希望这条连接继续用（决定响应发完是否关客户端侧）。
    HTTP/1.1 默认保持，除非显式 Connection: close；HTTP/1.0 默认不保持。"""
    conn = (_hdr_get(headers, b"connection") or b"").lower()
    if b"close" in conn:
        return False
    return True


async def _read_msg_head(reader, timeout):
    """读一条消息头（状态行/请求行 + 头行），返回原始行列表；超时/断开返回 None"""
    lines = []
    while True:
        try:
            h = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError):
            return None
        if not h:
            return lines if lines else None
        lines.append(h)
        if h in (b"\r\n", b"\n"):
            return lines


async def http_keepalive_relay(ctx, c_reader, c_writer, s_reader, s_writer,
                               prof, method, target, headers):
    """明文 HTTP 的 keep-alive 转发。

    原来的实现是「单请求短连接」：客户端每请求一个资源就重开一条上游 TCP，
    浏览器开一个 50 资源的页面就是 50 次握手，每次还吃满连接延迟 ——
    比真实网络更残，页面级测试的曲线整个失真。

    这里在一条客户端连接上串行处理多个请求：响应边界明确
    （Content-Length / chunked / 无体）就复用上游连接，否则这一请求
    结束后关掉上游、下个请求重连。只接「无请求体的 GET/HEAD/DELETE」——
    带体的 POST 请求体边界要另一个状态机，不冒险，继续走单请求路径。

    延迟/限速/丢包的注入与 pipe() 同规则：请求头吃上行延迟，响应按块吃
    下行延迟 + 下行带宽，切场景对已在途的连接立即生效（每块重读 G.profile）。
    """
    dl = DelayLine(c_writer, ctx.label).start()
    cur_target = None

    async def _reconnect(host, port):
        """换一条上游连接（返回 False 表示失败、已回 502）"""
        nonlocal s_reader, s_writer
        try:
            s_writer.close()
        except Exception:
            pass
        # 摘掉旧 writer 的登记：多次重连不能让 ctx.writers / G.active 累积死引用
        old_writer = s_writer
        ctx.unregister(old_writer)
        try:
            s_reader, s_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=15)
            ctx.register(s_writer)
            log_event("connect", f"HTTP 代理 {host}:{port}（重连）")
            return True
        except Exception as e:
            log_event("error", f"{host}:{port} 上游连接失败：{type(e).__name__}")
            try:
                c_writer.write(UPSTREAM_502)
                await c_writer.drain()
            except Exception:
                pass
            return False

    async def _send_down(block):
        """一块响应数据：丢包停顿 + 断连 + 下行带宽 + 下行延迟（与 pipe() 同规则）"""
        if ctx.fixed:
            await dl.submit(block, 0, G.gen)
            return
        p = G.profile
        # 与 pipe() 相同的丢包/断连判定：明文 keep-alive 的 GET/HEAD/DELETE
        # 过去只吃带宽+延迟，完全不吃 loss_pct/disconnect_pct，故障计数与
        # 实际表现不一致。每块重读 G.profile，切场景立即生效。
        if p["loss_pct"] and random.random() * 100 < p["loss_pct"]:
            await nap(p["stall_ms"] / 1000.0 * (0.5 + random.random()), G.gen)
            p = G.profile
        if p["disconnect_pct"] and random.random() * 100 < p["disconnect_pct"]:
            log_event("disconnect", "模拟断连：" + ctx.label)
            ctx.abort_all()
            raise Abort()
        bucket = G.bucket_down
        if bucket is not None:
            bucket.sync(p.get("down_kbps", 0))
            await bucket.consume(len(block))
        G.bytes_down += len(block)
        await dl.submit(block, _latency_delay(p, "down"), G.gen)

    try:
        first = True
        while True:
            # ---------- 取一个请求（第一个由调用方读好传入） ----------
            if first:
                first = False
                u = urllib.parse.urlsplit(target)
                path = (u.path or "/") + (("?" + u.query) if u.query else "")
                cur_target = (u.hostname or "", u.port or 80)
            else:
                head = await _read_msg_head(c_reader, 65)
                if not head:
                    return
                ctx.touch()
                try:
                    method, target, _ver = head[0].decode("latin1").split()
                except ValueError:
                    return
                method = method.upper()
                u = urllib.parse.urlsplit(target)
                if not u.scheme or not u.hostname:
                    return
                headers = [h for h in head[1:] if h.strip()]   # 去掉结尾空行
                path = (u.path or "/") + (("?" + u.query) if u.query else "")
                new_target = (u.hostname, u.port or (443 if u.scheme == "https" else 80))
                if s_writer.is_closing() or new_target != cur_target:
                    if not await _reconnect(new_target[0], new_target[1]):
                        return
                    cur_target = new_target
                # 后续请求也要吃每请求的故障注入（拒连/黑洞/DNS）
                p2 = ctx.prof if ctx.fixed else G.profile
                if not ctx.fixed:
                    if p2["reject_pct"] and random.random() * 100 < p2["reject_pct"]:
                        c_writer.write(UPSTREAM_502)
                        await c_writer.drain()
                        return
                    if p2["timeout_pct"] and random.random() * 100 < p2["timeout_pct"]:
                        if not await nap(600, G.gen):
                            return
                    if (p2["dns_fail_pct"] and not _is_ip_literal(_target_host(target))
                            and random.random() * 100 < p2["dns_fail_pct"]):
                        c_writer.write(b"HTTP/1.1 502 DNS resolution failed (simulated)"
                                       b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                        await c_writer.drain()
                        return

            ctx.label = f"{cur_target[0]}:{cur_target[1]}"

            # ---------- 转发请求头（吃上行延迟） ----------
            prof = ctx.prof if ctx.fixed else G.profile
            hdr = _rewrite_origin_request(method, path, headers)
            if not ctx.fixed:
                d = _latency_delay(prof, "up")
                while d > 0 and await nap(d, G.gen):
                    prof = G.profile        # 期间切换了场景/停止：按新参数重算
                    d = _latency_delay(prof, "up")

            # ---------- 读响应头（上游可能在写后断开：幂等方法重试一次） ----------
            head = None
            for attempt in (0, 1):
                try:
                    s_writer.write(hdr)
                    await s_writer.drain()
                except (ConnectionError, OSError):
                    if attempt == 0 and not ctx.fixed:
                        # 连接已死：必须真正重连再重试。旧实现直接 continue，
                        # 用的是同一个已死的 s_writer，重试必然再次抛错后 return，
                        # 注释承诺的「重连」从未发生。
                        if not await _reconnect(cur_target[0], cur_target[1]):
                            return
                        continue
                    return
                head = await _read_msg_head(s_reader, 60)
                if head:
                    break
                # 服务端可能刚关掉这条空闲 keep-alive：GET/HEAD/DELETE 幂等，
                # 重连后重试一次是安全的（这也正是复用上游必须处理的竞态）
                if attempt == 0 and not ctx.fixed:
                    if not await _reconnect(cur_target[0], cur_target[1]):
                        return
                    continue
                return
            log_event("connect", f"HTTP 代理 {cur_target[0]}:{cur_target[1]}{path[:60]}")

            status = 0
            try:
                status = int(head[0].split(b" ", 2)[1])
            except Exception:
                pass
            resp_headers = [h for h in head[1:] if h.strip()]
            mode, length = _resp_frame(status, resp_headers, method)
            upstream_keep = _resp_keepalive(head[0], resp_headers, mode)

            # ---------- 转发响应体 ----------
            if mode != "nobody":
                # 响应头本身也走下行通道（和 pipe() 一致：每块都吃延迟）
                await _send_down(b"".join(head))
                if mode == "cl":
                    remain = length
                    while remain > 0:
                        blk = await s_reader.read(min(65536, remain))
                        if not blk:
                            return
                        remain -= len(blk)
                        await _send_down(blk)
                elif mode == "chunked":
                    while True:
                        line = await s_reader.readline()
                        if not line:
                            return
                        await _send_down(line)
                        try:
                            sz = int(line.split(b";")[0].strip() or b"0", 16)
                        except ValueError:
                            return
                        if sz == 0:
                            # 最后一个 chunk：把 trailer 读到空行为止
                            while True:
                                t = await s_reader.readline()
                                if not t:
                                    return
                                await _send_down(t)
                                if t in (b"\r\n", b"\n"):
                                    break
                            break
                        remain = sz
                        while remain > 0:
                            blk = await s_reader.read(min(65536, remain))
                            if not blk:
                                return
                            remain -= len(blk)
                            await _send_down(blk)
                        crlf = await s_reader.readline()   # chunk 数据后的 CRLF
                        if not crlf:
                            return
                        await _send_down(crlf)
                else:  # eof：读到上游 EOF 为止，之后必须关上游
                    while True:
                        blk = await s_reader.read(65536)
                        if not blk:
                            break
                        await _send_down(blk)
                    upstream_keep = False

            # ---------- 决定连接去向 ----------
            if not upstream_keep:
                try:
                    s_writer.close()
                except Exception:
                    pass
            if not _client_keepalive(headers):
                return
    except Abort:
        dl.abort()
        raise
    except (ConnectionError, OSError):
        return
    finally:
        # 排空延迟队列再收尾，否则响应尾部的最后几块会被丢（与 pipe() 同规则）
        rate_kbps = 0 if ctx.fixed else G.profile.get("down_kbps", 0)
        if rate_kbps and dl.pending:
            need = dl.pending * 8.0 / (rate_kbps * BITS_PER_KBPS) + 30.0
        else:
            need = 30.0
        await dl.aclose(timeout=max(30.0, min(need, 600.0)))
        try:
            s_writer.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# 代理协议处理（CONNECT 隧道 + 普通 HTTP 转发）
# --------------------------------------------------------------------------

UPSTREAM_502 = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
# 拒绝转发到本机/控制口目标时用 403：与「上游连不上」的 502 区分开，
# 脚本一眼能看出是被策略挡下的，不是目标服务挂了。
UPSTREAM_403 = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"

# 代理绝不转发的端口：控制口 + 共享口。它们只服务本机 / 局域网内的
# WeakNet 自己，正常转发永远不会以它们为目标。
def _forbidden_target_ports():
    ports = {DEFAULT_CTRL_PORT, SHARE_PORT}
    for p in (G.ctrl_port, G.share_port):
        try:
            if int(p or 0) > 0:
                ports.add(int(p))
        except (TypeError, ValueError):
            pass
    return ports


def _target_forbidden(host, port):
    """这个转发目标是否属于「绝不该被代理转发」的一类（纯函数，便于单测）。

    两类目标：
      * 回环 / 本机地址（localhost、*.localhost、127.*、::1、0.0.0.0、::）
        —— 经代理隧道访问它们会绕过控制面的 Host 校验，把局域网请求直接
        送进只服务本机的端口；
      * 控制口 / 共享口（8899 / 8900 及实际占用的那两个端口）。
    弱网转发本身不需要这两种目标，所以拒绝它们不增加任何使用门槛。

    只看精确值与后缀：`localhost.example.com` 是普通域名，必须放行。
    """
    h = (host or "").strip().strip("[]").strip().lower().rstrip(".")
    if h == "localhost" or h.endswith(".localhost"):
        return True
    if h in ("::1", "::", "0.0.0.0"):
        return True
    if h.startswith("127."):
        return True
    try:
        p = int(port)
    except (TypeError, ValueError):
        return False
    return p in _forbidden_target_ports()


def _proxy_exposed():
    """代理是否绑在回环之外（--proxy-host 0.0.0.0，手机 Wi-Fi 直连场景）。"""
    return (G.proxy_host or "").strip().lower() not in (
        "", "127.0.0.1", "localhost", "::1", "[::1]")


def _proxy_target_blocked(host, port):
    """运行时判定：这条转发要不要拒。

    控制口/共享口一律拒（本机、局域网都不转发）；回环目标只在代理对局域网
    开放时才拒 —— 绑 127.0.0.1 时代理本来就只有本机进程能用，而本机进程
    直接连 127.0.0.1:8899 根本不需要绕代理，封掉只会误伤「用弱网测本机
    服务」这条常用路径。

    取舍：远端主机上恰好也用 8899/8900 的服务无法经本代理做弱网测试。
    这是有意的——控制口是本工具的本地管理面，不能因为「可能有人要测远端
    同端口服务」就留一条可被利用的隧道。
    """
    try:
        p = int(port)
    except (TypeError, ValueError):
        return False
    if p in _forbidden_target_ports():
        return True
    return _proxy_exposed() and _target_forbidden(host, p)


def _target_host(target):
    """从请求目标里取出主机部分（兼容 CONNECT 的 host:port 与绝对 URI）"""
    t = target
    if "://" in t:
        t = t.split("://", 1)[1]
    t = t.split("/", 1)[0]
    if t.startswith("["):                       # IPv6 字面量 [::1]:443
        return t[1:].split("]", 1)[0]
    return t.rsplit(":", 1)[0] if ":" in t else t


def _is_ip_literal(host):
    """是否 IP 字面量（v4/v6）。IP 直连不经过 DNS，DNS 故障档不应误伤它"""
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


# 请求行/请求头的读取超时。正常客户端毫秒级就发完请求头，30s 只掐
# 「连上来就晾着」的半开连接；已建立的长连接早过了读头阶段，完全不受影响。
REQ_HEADER_TIMEOUT = 30

# 请求头上限：控制口与代理共用同一组口径。代理侧原来只靠上面的超时，
# 客户端只要每行都在超时前到达，就能一行接一行地把内存顶上去。
MAX_HEADER_BYTES = 16 * 1024     # 请求行 + 全部头部
MAX_HEADER_LINES = 100


async def read_proxy_headers(reader, max_bytes=MAX_HEADER_BYTES,
                             max_lines=MAX_HEADER_LINES):
    """读代理请求头，返回原始行列表；超限返回 None（调用方回 431 并断开）。

    行数与总字节同时限制：只限字节挡不住「十万行空头」，只限行数挡不住
    「一行 16MB」。
    """
    hs = []
    total = 0
    while True:
        h = await reader.readline()
        if not h or h in (b"\r\n", b"\n"):
            return hs
        total += len(h)
        if total > max_bytes or len(hs) >= max_lines:
            return None
        hs.append(h)


async def handle_proxy(reader, writer):
    _tcp_nodelay(writer)               # accept 侧默认开着 Nagle，见 _tcp_nodelay 注释
    G.conns_total += 1
    G.conns_active += 1
    ctx = ConnCtx("?")
    # 客户端侧的 writer 立刻登记：这样 finally 里的 ctx.cleanup() 在任何
    # 返回路径上都能把客户端 socket 关掉。以前只有走到 register() 之后才登记，
    # 「请求头读到一半超时」这类早退路径会把客户端连接漏掉 —— 连接一直挂着，
    # 客户端要等自己的超时，服务端这边则一直占着 fd。
    ctx.register(writer)
    try:
        # ---- 识别来源进程，决定这条连接是否被接管 ----
        pname = None
        try:
            peer = writer.get_extra_info("peername")
            if peer and len(peer) >= 2:
                # 必须走 async 版本：进程名反查是阻塞的（≈94 ms/连接），
                # 放在事件循环里会卡住所有其他连接。
                pname = await _proc_name_of_peer_async(peer[1], G.proxy_port)
        except Exception:
            pname = None
        if pname:
            G.scope_seen[pname] = G.scope_seen.get(pname, 0) + 1
        else:
            # 反查失败也要记账：否则用户看到「仅接管 A」却仍有流量被放行时无从判断原因
            G.scope_seen["<未识别>"] = G.scope_seen.get("<未识别>", 0) + 1
        bypass = not scope_hit(pname)
        if bypass:
            ctx.fixed = True
            ctx.bypassed = True
            ctx.prof = BYPASS_PROFILE
        # 判定完成。此前 ctx 已在 G.contexts 里（空闲回收看得见），
        # 但 flap 会跳过 pending 的它；到这里才纳入 flap 的掐断范围。
        ctx.bypass_pending = False

        # 周期断网黑窗期：只掐断被接管的连接（未接管的程序不受影响）
        if (not G.gate_open) and not bypass:
            log_event("flap", "黑窗期：拒绝新连接")
            abort_writer(writer)
            return

        try:
            line = await asyncio.wait_for(reader.readline(), timeout=REQ_HEADER_TIMEOUT)
        except (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError):
            return
        if not line:
            return
        try:
            method, target, _ver = line.decode("latin1").split()
        except ValueError:
            return
        method = method.upper()

        # 读完请求头（整体 30s 超时）。原来逐行裸 readline：客户端发个请求行就
        # 晾着的话，连接会无限期挂起，占着 ctx/writer/G.active 条目。
        # 超限（16KB / 100 行）时 read_proxy_headers 返回 None，回 431 断开。
        try:
            headers = await asyncio.wait_for(read_proxy_headers(reader),
                                             timeout=REQ_HEADER_TIMEOUT)
        except (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError):
            return
        if headers is None:
            log_event("error", "代理请求头超限（>%d 字节或 >%d 行），已断开"
                      % (MAX_HEADER_BYTES, MAX_HEADER_LINES))
            try:
                writer.write(b"HTTP/1.1 431 Request Header Fields Too Large"
                             b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
            except Exception:
                pass
            return

        # 绑定 0.0.0.0 时本端口对整个网段开放，这是「手机 Wi-Fi 代理直连」
        # 的必要前提——Android 原生代理设置没有凭据输入口，任何鉴权都会让
        # 这条路径直接不可用。风险改由开启时的一次确认与顶栏提示承担。

        # 直通连接用固定全零参数：不注入任何弱网效果，也不随场景切换而变化
        prof = ctx.prof if ctx.fixed else G.profile
        if ctx.fixed:
            ctx.label = "直通·" + (pname or "未知进程")

        # ---- 故障模拟（每连接判定）----
        if prof["reject_pct"] and random.random() * 100 < prof["reject_pct"]:
            writer.write(UPSTREAM_502)
            await writer.drain()
            log_event("reject", "模拟拒连（完全断网）：" + target[:80])
            return
        if prof["timeout_pct"] and random.random() * 100 < prof["timeout_pct"]:
            log_event("timeout", "模拟黑洞（无响应）：" + target[:80])
            # 挂起期间若用户切换场景 / 点停止，立即放行，按新参数继续处理
            gen0 = G.gen
            if not await nap(600, gen0):
                return
            prof = G.profile
            log_event("config", "黑洞解除，恢复处理：" + target[:40])
        # DNS 故障只影响「需要解析域名」的连接；IP 字面量（含 HTTPDNS / 固定 IP 的 App）
        # 不经过 DNS，与真机上的表现保持一致，不会被误判成完全断网
        if (prof["dns_fail_pct"] and not _is_ip_literal(_target_host(target))
                and random.random() * 100 < prof["dns_fail_pct"]):
            writer.write(b"HTTP/1.1 502 DNS resolution failed (simulated)"
                         b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            log_event("dns", "模拟 DNS 故障：" + target[:80])
            return

        # ---- CONNECT 隧道（HTTPS 主流量）----
        if method == "CONNECT":
            host, _, port_s = target.partition(":")
            try:
                port = int(port_s or 443)
            except ValueError:
                return
            label = f"{host}:{port}"
            ctx.label = label
            if _proxy_target_blocked(host, port):
                # 绑 0.0.0.0 时本端口对整个网段开放，同网段任何人发
                # `CONNECT 127.0.0.1:8899` 都能把请求塞进只服务本机的控制口。
                log_event("reject", "拒绝转发到本机/控制口目标：" + label)
                try:
                    writer.write(UPSTREAM_403)
                    await writer.drain()
                except Exception:
                    pass
                return
            try:
                s_reader, s_writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=15)
            except Exception as e:
                try:
                    writer.write(UPSTREAM_502)
                    await writer.drain()
                except Exception:
                    pass
                log_event("error", f"{label} 上游连接失败：{type(e).__name__}")
                return
            ctx.register(writer)
            ctx.register(s_writer)
            log_event("connect", (label + " 直通（未接管）") if ctx.fixed
                      else ("建立隧道 " + label))
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await run_tunnel(ctx, reader, s_writer, s_reader, writer, prof)
            return

        # ---- 普通 HTTP 绝对地址代理 ----
        if target.startswith("http://") or target.startswith("https://"):
            u = urllib.parse.urlsplit(target)
            host = u.hostname or ""
            port = u.port or (443 if u.scheme == "https" else 80)
            label = f"{host}:{port}"
            ctx.label = label
            path = (u.path or "/") + ("?" + u.query if u.query else "")
            if _proxy_target_blocked(host, port):
                # 与 CONNECT 同一套判据：普通 HTTP 绝对地址同样能打到本机端口
                log_event("reject", "拒绝转发到本机/控制口目标：" + label)
                try:
                    writer.write(UPSTREAM_403)
                    await writer.drain()
                except Exception:
                    pass
                return
            try:
                s_reader, s_writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=15)
            except Exception as e:
                try:
                    writer.write(UPSTREAM_502)
                    await writer.drain()
                except Exception:
                    pass
                log_event("error", f"{label} 上游连接失败：{type(e).__name__}")
                return
            ctx.register(writer)
            ctx.register(s_writer)
            # 无请求体的 GET/HEAD/DELETE 走 keep-alive 转发（一条客户端连接
            # 串行多个请求，上游能复用就复用）；带体的请求走单请求短连接 ——
            # 请求体边界是另一个状态机，不在这条路径上冒险。
            req_has_body = False
            cl = _hdr_get(headers, b"content-length")
            if cl is not None:
                try:
                    req_has_body = int(cl.split(b",")[0]) > 0
                except ValueError:
                    req_has_body = True
            if _hdr_get(headers, b"transfer-encoding") is not None:
                req_has_body = True
            if method in ("GET", "HEAD", "DELETE") and not req_has_body:
                await http_keepalive_relay(ctx, reader, writer, s_reader,
                                           s_writer, prof, method, target, headers)
                return
            log_event("connect", f"HTTP 代理 {label}{path[:60]}")
            # 重写为源站请求，去代理头，单请求短连接（简单可靠）
            out = [f"{method} {path} HTTP/1.1".encode()]
            for h in headers:
                k = h.split(b":", 1)[0].strip().lower()
                if k in (b"proxy-connection", b"connection", b"keep-alive"):
                    continue
                out.append(h.rstrip(b"\r\n"))
            out.append(b"Connection: close")
            hdr = b"\r\n".join(out) + b"\r\n\r\n"
            # 请求头也要吃上行延迟。请求行/请求头是 readline() 逐行读的，读到的
            # 瞬间就离开管道，不经过 DelayLine——正文为空的 GET 因此只能吃到单向
            # 延迟，实测 RTT 只有设定值的一半（各 400ms 设定实测 430ms）。
            # 头只有一块、体量极小，按建连的方式直接等一次即可，不影响吞吐
            # （吞吐走 pipe 里的延迟队列，多块同时在途）。
            d = _latency_delay(prof, "up")
            while d > 0 and await nap(d, G.gen):
                prof = G.profile        # 期间切换了场景/停止：按新参数重算
                d = _latency_delay(prof, "up")
            s_writer.write(hdr)
            await s_writer.drain()
            await run_tunnel(ctx, reader, s_writer, s_reader, writer, prof)
    except Abort:
        pass
    except (ConnectionError, OSError) as e:
        # 客户端取消、App 切后台、上游 reset 都是常态，不该打印栈；但完全
        # 静默会让「这条连接为什么失败」在事件环里查不到任何线索。
        log_event("error", "%s 转发中断：%s" % (ctx.label, type(e).__name__))
    finally:
        G.conns_active -= 1
        ctx.cleanup()


# --------------------------------------------------------------------------
# 周期性断网（网络切换模拟）
# --------------------------------------------------------------------------

async def flap_loop():
    try:
        while G.profile["flap_sec"] > 0:
            await asyncio.sleep(G.profile["flap_sec"])
            if G.profile["flap_sec"] <= 0:
                break
            G.gate_open = False
            log_event("flap", f"网络切换：全部连接中断 {G.profile['flap_blackout_sec']}s")
            # 未接管（bypass）连接虽然也登记进 G.active，但承诺是「不受弱网影响」。
            # 按 ConnCtx 逐个筛，只掐真正被我们接管的那批。
            for ctx in list(G.contexts):
                # 未判定完的（卡在进程名反查的 await 上）先放过，
                # 它可能是直通连接；真被接管的话后面的黑窗入口检查会拒掉它。
                if ctx.bypass_pending or ctx.bypassed:
                    continue
                ctx.abort_all()
            await asyncio.sleep(G.profile["flap_blackout_sec"])
            G.gate_open = True
            log_event("flap", "网络恢复")
    except asyncio.CancelledError:
        G.gate_open = True
        raise


# --------------------------------------------------------------------------
# 空闲连接回收
# --------------------------------------------------------------------------

# 超过这么久没有任何数据流动的连接就拆掉。要远大于正常 keep-alive 空闲
# （浏览器一般 <60s），免得误杀长轮询 / WebSocket / 挂起下载。
TUNNEL_IDLE_SEC = 600
# 多久扫一次。和 TUNNEL_IDLE_SEC 分开：扫描本身很便宜（遍历一个 dict），
# 但真拆连接的时机精度由它决定，测试里把它压到 1s 才能快速观测回收行为。
IDLE_REAP_INTERVAL = 60


async def idle_reap_loop():
    """定期回收「双方都不说话」的连接。

    没有这个机制时：客户端发个请求行就晾着（请求头读取无超时），或 CONNECT 隧道
    建好后双方都不发数据 —— 这些连接会一直挂着，占 ctx/writer/G.active 条目，
    Android 端有 300s cleanup，Windows 端原来没有对应物。
    """
    while True:
        await asyncio.sleep(IDLE_REAP_INTERVAL)
        now = time.monotonic()
        for ctx in list(G.contexts):
            try:
                if now - ctx.last_active > TUNNEL_IDLE_SEC:
                    log_event("idle", f"空闲超时，断开：{ctx.label}")
                    ctx.abort_all()
            except Exception:
                pass


async def set_profile(prof, key):
    """切换场景。

    关键：原地更新 G.profile（而不是替换成新 dict）。已在途的连接持有的是同一个
    dict 引用，这样「停止模拟 / 切换场景」能让正在传输的连接立刻生效，
    否则它们会一直拿着旧参数跑完（表现为点了停止还是慢、黑洞里的请求一直挂着）。
    """
    # 本调用拿到的代数。下面 cancel+await flap_task 会让出事件循环，
    # 期间并发的 /api/preset 可能又换了一次场景；收尾时必须认代数，
    # 否则旧调用会用自己那份 prof 把新场景的开关/参数覆盖掉。
    G.profile_gen += 1
    my_gen = G.profile_gen
    cur = G.profile
    cur.clear()
    cur.update(prof)
    G.profile = cur
    G.preset_key = key
    G.gen += 1                     # 代数 +1：唤醒所有挂起（黑洞/卡顿）的连接
    # 令牌桶必须在这里同步新速率。等待中的 consume 只在下次醒来时才检查 rate，
    # 若等 pipe 下一轮再 sync，点「停止」要等当前 64KB 块按旧速率走完
    # （300kbps 下约 1.7s），用户看到的是「点了停止还在慢慢下」。
    for _k, _b in (("up_kbps", G.bucket_up), ("down_kbps", G.bucket_down)):
        if _b is not None:
            try:
                _b.sync(cur.get(_k, 0))
            except Exception:
                pass
    try:
        if G.wake is not None:
            G.wake.set()
        G.wake = asyncio.Event()
    except Exception:
        pass
    if G.flap_task:
        G.flap_task.cancel()
        try:
            await G.flap_task
        except (asyncio.CancelledError, Exception):
            pass
        # 先认代数再动共享句柄。await 期间可能被更新的 set_profile 接手，
        # 它已经建好了自己的 flap_task；本调用若在这里写 None 就会抹掉新句柄，
        # 新任务失去引用后再也停不掉，表现为「双 flap」。
        if G.profile_gen != my_gen:
            # 已经有更新的 set_profile 接手：参数、桶、开关、flap_task 都由它收尾，
            # 这次旧调用到此为止，一个共享字段都不能写。
            return
        G.flap_task = None
    G.gate_open = True
    # 应用带内容的场景即视为「开启限速」，回到全零（直连）即视为「关闭」
    G.limit_on = has_limits(prof)
    if G.limit_on:
        G.last_profile = dict(prof)     # 记住最后一次生效的场景，供「再次开启」复原
    if prof["flap_sec"] > 0:
        G.flap_task = asyncio.create_task(flap_loop())


async def select_scene(prof, key):
    """选中一支场景：弱网已开启就立即生效，没开启就只记下、不碰流量。

    这是界面上「点场景 = 立即生效」的真正含义：**生效的前提是弱网已经开着**。
      - 已在测试中：切场景立刻落到引擎参数上（已在途的连接也跟着变）
      - 没开始：只更新 preset_key，等用户点「▶ 开启限速」时按这个 key 复原。
        反过来不行——选一下场景就把流量限住，等于偷偷替用户开始了测试。
    """
    G.preset_key = key
    if G.combo_task is not None and key != COMBO_KEY_PREFIX + str(G.combo_id):
        # 用户手动点了别的场景：正在播放的组合必须停下，否则它会到点把场景抢回去
        await stop_combo()
    # 没开启限速时只改 preset_key，不能把「只是选中」的 prof 记成
    # last_profile（报告兜底会据此谎报测过）。真正生效时由 set_profile
    # 里的 has_limits 分支统一记录，语义就是「最后一次生效的场景」。
    if not G.limit_on:
        return False
    await set_profile(prof, key)
    G.limit_on = True
    return True


# ---------------------------------------------------------------- 弱网组合播放


async def _combo_runner(combo):
    """按 steps 顺序换场景：每段跑满它的时长后自动切下一段。

    循环由 combo['loop'] 决定；不循环就停在最后一段（用户既然排了队，播完保持末段
    比自动回到直连更符合预期 —— 他还在用这个网络环境看 App 的反应）。
    """
    key = COMBO_KEY_PREFIX + combo["id"]
    steps = combo["steps"]
    try:
        while True:
            for i, st in enumerate(steps):
                try:
                    prof = scene_profile(st["key"])
                except Exception:
                    # 这一段的场景被删了 / key 不认识：跳过它，别把整个组合带崩
                    log("组合「%s」第 %d 段的场景不可用，已跳过" % (combo["name"], i + 1))
                    continue
                dur = step_seconds(st)
                G.combo_step = i
                G.combo_deadline = time.time() + dur
                await set_profile(prof, key)     # 不经 select_scene：那是手动切场景的入口
                G.limit_on = True
                await _sleep_step(dur, combo["id"])
                if find_combo(combo["id"]) is None:
                    # 播放途中被删掉了：到这里就停，不能再替它改参数
                    log("组合「%s」已被删除，停止播放" % combo["name"])
                    return
            if not combo.get("loop"):
                G.combo_deadline = 0             # 播完了：界面上的倒计时收掉
                log_event("config", "组合「%s」播放完毕，停在最后一段" % combo["name"])
                return
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log("组合播放异常：" + repr(e))
    finally:
        # 正常播完 / 出错退出后要把「正在播放」的状态收起来；
        # stop_combo 已经把 G.combo_task 置空了，那时这里不该再动别的字段
        if G.combo_task is asyncio.current_task():
            G.combo_task = None
            G.combo_id = None
            G.combo_step = -1
            G.combo_deadline = 0


async def _sleep_step(dur, cid):
    """睡满一段，但每 0.5s 醒一次看组合还在不在：删掉后不该傻等到这段跑完"""
    end = time.time() + max(0, dur)
    while True:
        rest = end - time.time()
        if rest <= 0:
            return
        await asyncio.sleep(min(0.5, rest))
        if find_combo(str(cid)) is None:
            return


async def start_combo(cid):
    """立刻按这个组合开始播放（弱网必须已经开着，否则给组合对象会让流量被偷偷限住）"""
    combo = find_combo(cid)
    if combo is None:
        raise KeyError(cid)
    await stop_combo()                    # 换组合时先掐掉上一支，避免两个 Task 抢着改参数
    G.combo_id = combo["id"]
    G.combo_step = -1
    G.combo_task = asyncio.create_task(_combo_runner(combo))
    log_event("config", "开始播放组合「%s」：%s" % (combo["name"], combo_summary(combo)))
    return combo


async def select_combo(cid):
    """选中一个组合：弱网已开启就从第一段开始播，没开启只记下来（同 select_scene 规则）"""
    combo = find_combo(cid)
    if combo is None:
        raise KeyError(cid)
    G.preset_key = COMBO_KEY_PREFIX + combo["id"]
    if not G.limit_on:
        # 还没开始测试：把组合的末段参数记下来没意义，这里只记「选了它」，
        # 等用户点「开启限速」时 set_limit 会认出 c: 前缀并开始播放
        G.combo_id = combo["id"]
        G.combo_step = -1
        return False
    await start_combo(combo["id"])
    return True


async def stop_combo():
    """停掉正在播放的组合（换场景 / 关限速 / 删组合时都要走这里）"""
    task = G.combo_task
    G.combo_task = None
    G.combo_id = None
    G.combo_step = -1
    G.combo_deadline = 0
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


def combo_state():
    """给界面看的播放进度：None=没在播；否则 {name, step, total, left}"""
    if G.combo_task is None or G.combo_id is None:
        return None
    combo = find_combo(G.combo_id)
    if combo is None:
        return None
    return {"id": combo["id"], "name": combo["name"],
            "step": G.combo_step + 1, "total": len(combo["steps"]),
            "left": max(0, int(G.combo_deadline - time.time())),
            "loop": bool(combo.get("loop"))}


async def set_limit(on):
    """限速总开关。

    关闭不是丢掉用户选的场景：preset_key 保留，下次开启直接复原，
    免得每次开关都要重选一遍。开启时若当前是「直连」，就按 preset_key 重建。
    """
    on = bool(on)
    key = G.preset_key
    if on:
        if str(key).startswith(COMBO_KEY_PREFIX):
            # 选中组合又没开始播放的情形（典型：选了组合后点了停止，再点开启）
            try:
                await start_combo(key[len(COMBO_KEY_PREFIX):])
            except KeyError:
                log("选中的组合已不存在，已切回上一次的场景")
                await set_profile(G.last_profile or default_profile(), "custom")
                G.limit_on = True
                return G.limit_on
        else:
            try:
                prof = build_preset_profile(key)
            except Exception:
                # key 是 "custom"（自定义参数）一类没有预设可查的情况：
                # 用最后一次生效过的场景复原，避免开启时把用户调好的参数冲掉
                prof = G.last_profile or default_profile()
            await set_profile(prof, key)
    else:
        await stop_combo()                 # 关掉限速时组合不能继续在后台换场景
        await set_profile(default_profile(), key)
    G.limit_on = on                     # 覆盖 set_profile 的推断：自定义全零也算「已开启」
    return G.limit_on


async def restart_proxy(host):
    old = G.proxy_server
    if old:
        old.close()
        await old.wait_closed()
    G.proxy_host = host
    G.proxy_server = await asyncio.start_server(
        handle_proxy, host, G.proxy_port, limit=1 << 20)
    if host not in ("127.0.0.1", "localhost", "::1"):
        log_event("config", "局域网代理已开启（同网段设备可直连 %s:%d）"
                  % (host, G.proxy_port))


# --------------------------------------------------------------------------
# Web 控制台 / API
# --------------------------------------------------------------------------

# ==========================================================================
# 环境共享：发现同一个局域网里的其他 WeakNet 用户，互相推送自定义测试环境
#
# 发现走 UDP 广播：每 3 秒喊一句「我是谁、我的共享端口是多少」，同时听别人的。
# 推送则是本机主动 HTTP POST 到对方的共享端口 —— 这样对方不必把整个控制台
# 暴露到局域网上，只开一个只认 /api/share 和 /api/hello 的小服务。
# ==========================================================================
DISCOVER_PORT = 18899          # UDP 广播起始端口（发现用）
DISCOVER_SPAN = 8              # 同机多实例时各占其后的一个端口，广播时逐个都发一份
SHARE_PORT = 8900              # TCP 共享端口（接收别人推送用）
DISCOVER_TAG = "weaknet-discover-v1"
PEER_TTL = 12.0                # 超过这么久没再收到广播，就当对方下线了
# 共享协议版本 / PIN 长度 / 收件箱上限来自 spec 的 share。


def _settings_file():
    """用户名的落盘位置（与自定义场景同一套候选目录）"""
    try:
        return _preset_files()[0].with_name("weaknet_settings.json")
    except Exception:
        return Path("weaknet_settings.json")


def _default_user_name():
    """默认名：优先取系统用户名，取不到退回主机名"""
    for key in ("USERNAME", "USER"):
        v = os.environ.get(key)
        if v and v.strip():
            return v.strip()[:24]
    try:
        return (socket.gethostname() or "WeakNet")[:24]
    except Exception:
        return "WeakNet"


def load_settings():
    """纯读，不做补全。

    补全不能放在这里：读一次就现编一个 id 的话，同一次运行里前后两次读会拿到
    两个不同的 id —— 表现就是「改个名字，局域网里就多出一个同名的幽灵设备」
    （对方按 id 区分 peer，旧 id 要过 TTL 才消失）。补全统一交给 ensure_identity。
    """
    try:
        f = _settings_file()
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception as e:
        # 设置文件损坏会让用户名/id/PIN 全部回到默认值，而界面上看不出来
        log("设置读取失败（%s）：%r" % (_settings_file(), e))
    return {}


def save_settings(d):
    try:
        f = _settings_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def clean_name(name, fallback="WeakNet"):
    """用户名/场景名清洗：压掉多余空白，限长"""
    s = " ".join(str(name or "").split())[:24]
    return s or fallback


def ensure_identity():
    """保证「我是谁」有值且已落盘：缺 id / 名字时才生成并保存一次。

    id 用来区分局域网广播是不是自己发的，必须稳定 —— 否则改名或重启都会
    换一个身份，对方会看到多个同名设备。
    """
    d = load_settings()
    changed = False
    if not d.get("id"):
        d["id"] = uuid.uuid4().hex[:12]
        changed = True
    if not d.get("name"):
        d["name"] = _default_user_name()
        changed = True
    if "share_pin" not in d:
        d["share_pin"] = new_share_pin()
        changed = True
    if changed:
        save_settings(d)
    G.self_id = d["id"]
    G.user_name = d["name"]
    G.share_pin = normalize_pin(d.get("share_pin"))
    G.allow_legacy = bool(d.get("allow_legacy", False))
    return d


def normalize_pin(v):
    """PIN 只接受 4 位数字；其他形态一律视为空（不匹配）"""
    s = "".join(ch for ch in str(v or "") if ch.isdigit())[:PIN_LEN]
    return s if len(s) == PIN_LEN else ""


def new_share_pin():
    # PIN 是局域网共享的唯一凭据，必须用密码学随机源：random 的 Mersenne
    # Twister 状态可被几个输出反推，4 位 PIN 会被预测。randbelow 不引入新依赖。
    return "%04d" % secrets.randbelow(10000)


def set_share_pin(pin=None, refresh=False):
    """设置/刷新共享 PIN。refresh=True 时忽略 pin 参数直接换一个随机的。"""
    d = ensure_identity()
    p = new_share_pin() if refresh else normalize_pin(pin)
    if not p:
        return {"ok": False, "error": "PIN 需为 4 位数字"}
    d["share_pin"] = p
    save_settings(d)
    G.share_pin = p
    log_event("share", "共享 PIN 已" + ("刷新" if refresh else "更新"))
    return {"ok": True, "pin": p}


def set_allow_legacy(v):
    d = ensure_identity()
    d["allow_legacy"] = bool(v)
    save_settings(d)
    G.allow_legacy = bool(d["allow_legacy"])
    log_event("share", "旧版设备推送：" + ("允许（仅进收件箱）" if G.allow_legacy else "已拒绝"))
    return {"ok": True, "allow_legacy": G.allow_legacy}


def share_proto_ver(payload):
    try:
        return int(payload.get("ver") or 1)
    except Exception:
        return 1


# PIN 退避参数：前 5 次不锁（允许手误），之后 60s 起、按次数翻倍、上限 15 分钟。
SHARE_FAIL_FREE = 5
SHARE_FAIL_LOCK = 60.0
SHARE_FAIL_MAX = 900.0


def _share_fail_cleanup(now):
    """失败表按来源 IP 记。局域网最多个位数设备，但伪造源地址可以刷表，
    所以超过 512 条时清掉已过期的，避免长期运行内存缓慢增长。"""
    if len(G.share_fail) <= 512:
        return
    for k in [k for k, v in G.share_fail.items() if v.get("until", 0.0) <= now]:
        G.share_fail.pop(k, None)


def share_throttle_left(peer_ip):
    """距离解锁还剩几秒；0 = 未锁定。"""
    if not peer_ip:
        return 0.0
    st = G.share_fail.get(peer_ip)
    if not st:
        return 0.0
    left = float(st.get("until", 0.0)) - time.monotonic()
    return left if left > 0 else 0.0


def note_share_fail(peer_ip):
    if not peer_ip:
        return
    now = time.monotonic()
    _share_fail_cleanup(now)
    st = G.share_fail.setdefault(peer_ip, {"n": 0, "until": 0.0})
    st["n"] = int(st.get("n", 0)) + 1
    if st["n"] >= SHARE_FAIL_FREE:
        over = st["n"] - SHARE_FAIL_FREE
        lock = min(SHARE_FAIL_MAX, SHARE_FAIL_LOCK * (2 ** min(over, 4)))
        st["until"] = now + lock
        log_event("share", "PIN 连续错误 %d 次，锁定 %.0f 秒（%s）"
                  % (st["n"], lock, peer_ip))


def note_share_ok(peer_ip):
    if peer_ip:
        G.share_fail.pop(peer_ip, None)


def share_auth_error(payload, target_pin=None, peer_ip=None):
    """共享推送的鉴权判定：None = 放行，否则返回 (HTTP 状态码, 原因)。

    默认安全：ver<2（旧版、无 PIN）直接 409 拒收；用户在面板里显式打开
    allow_legacy 后才接受，且调用方会把内容强制降级为「只进收件箱」。
    连续 PIN 错误按来源 IP 退避：4 位 PIN 无退避时可被快速穷举。
    """
    left = share_throttle_left(peer_ip)
    if left > 0:
        return 429, "PIN 连续错误，请 %.0f 秒后再试" % left
    if share_proto_ver(payload) < SHARE_VER:
        if not G.allow_legacy:
            return 409, "对方是旧版 WeakNet（无 PIN），已拒收；可在共享面板允许旧版设备"
        return None
    want = G.share_pin if target_pin is None else normalize_pin(target_pin)
    if not want:
        # 原来这里 fail-open 放行，等于本机没设 PIN 时任何人都能推送内容进来。
        # 没有可校验的口令就必须拒收，并明确提示去共享面板设置 PIN。
        return 409, "本机尚未设置共享 PIN，无法校验推送；请先在共享面板设置 4 位 PIN"
    if normalize_pin(payload.get("pin")) != want:
        note_share_fail(peer_ip)
        left = share_throttle_left(peer_ip)
        if left > 0:
            return 429, "PIN 连续错误，已锁定 %.0f 秒" % left
        return 403, "PIN 不正确"
    note_share_ok(peer_ip)
    return None


def set_user_name(name):
    d = ensure_identity()          # 先补齐 id 再改名，避免改完名字 id 还是空的
    d["name"] = clean_name(name)
    save_settings(d)
    G.user_name = d["name"]
    return G.user_name


def init_identity():
    """启动时装载「我是谁」；没设置过就用系统用户名"""
    ensure_identity()


class _DiscoverProtocol(asyncio.DatagramProtocol):
    """收到别人的广播就登记一下；自己的广播不算（靠 id 区分）"""

    def datagram_received(self, data, addr):
        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception:
            return
        if not isinstance(msg, dict) or msg.get("tag") != DISCOVER_TAG:
            return
        pid = str(msg.get("id") or "")
        if not pid or pid == G.self_id:
            return
        try:
            ver = int(msg.get("ver") or 1)
        except Exception:
            ver = 1
        try:
            share_port = int(msg.get("share") or SHARE_PORT)
        except Exception:
            share_port = SHARE_PORT
        if not (0 < share_port < 65536):
            share_port = SHARE_PORT
        G.peers[pid] = {
            "id": pid,
            "name": clean_name(msg.get("name"), "匿名"),
            "ip": addr[0],
            "platform": str(msg.get("platform") or "")[:16],
            "share": share_port,
            "ver": ver,
            "pin": normalize_pin(msg.get("pin")),
            "at": time.time(),
        }

    def error_received(self, exc):
        pass


def _bind_discover():
    """在 DISCOVER_PORT 起的一段端口里挑一个能 bind 的，返回 (socket, port)。

    为什么不是一个固定端口：UDP 端口只能被一个进程占用，同机跑多个实例
    （调试时常见，用户也可能开着两个窗口）时只有第一个绑得上，其余实例
    收不到任何广播 —— 表现为「单向发现」：A 看得到 B，B 看不到 A。
    各占一个端口 + 广播时整段都发一份，两边就都能收到。
    """
    for i in range(DISCOVER_SPAN):
        p = DISCOVER_PORT + i
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind(("0.0.0.0", p))
            return s, p
        except OSError:
            try:
                s.close()
            except Exception:
                pass
    return None, 0


def _broadcast_presence():
    """向本网段喊一声：255.255.255.255 与子网广播地址都发，
    后者能绕开部分路由器/虚拟网卡对全广播地址的过滤；端口整段逐个发。"""
    payload = json.dumps({
        "tag": DISCOVER_TAG, "id": G.self_id, "name": G.user_name,
        "platform": "windows" if IS_WIN else os.name,
        "share": G.share_port or SHARE_PORT, "ver": SHARE_VER,
        "pin": G.share_pin,
    }, ensure_ascii=False).encode("utf-8")
    addrs = ["255.255.255.255"]
    try:
        ip = G.lan_ip
        if ip and ip != "127.0.0.1":
            addrs.append(ip.rsplit(".", 1)[0] + ".255")
    except Exception:
        pass
    for a in addrs:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.settimeout(0.5)
            for i in range(DISCOVER_SPAN):
                s.sendto(payload, (a, DISCOVER_PORT + i))
            s.close()
        except Exception:
            pass


async def discovery_loop():
    """广播 + 收听，并剔除已下线的 peer"""
    sock, port = _bind_discover()
    if sock is None:
        # 整段都被占了：不收听，但仍能向外广播让别人发现自己
        G.discover_listen = False
        log_event("share", "局域网发现未能监听（UDP %d-%d 均被占用）"
                  % (DISCOVER_PORT, DISCOVER_PORT + DISCOVER_SPAN - 1))
    else:
        try:
            # 传 sock= 时不能再带 allow_broadcast 之类的 socket 修饰参数
            #（Python 3.13 起会直接抛 "socket modifier keyword arguments..."），
            # 广播开关在 _bind_discover 里已经用 setsockopt 设好了
            await asyncio.get_running_loop().create_datagram_endpoint(
                _DiscoverProtocol, sock=sock)
            G.discover_listen = True
            log_event("share", "局域网发现已开启（UDP %d）" % port)
        except Exception as e:
            try:
                sock.close()
            except Exception:
                pass
            G.discover_listen = False
            log_event("share", "局域网发现未能监听（%s）" % str(e)[:40])
    bc = 0
    while True:
        try:
            _broadcast_presence()
            bc += 1
            if bc == 1:
                log_event("share", "已向局域网广播本机名称：" + G.user_name)
        except Exception as e:
            log_event("share", "广播失败：" + str(e)[:60])
        now = time.time()
        for pid in [k for k, v in list(G.peers.items()) if now - v["at"] > PEER_TTL]:
            G.peers.pop(pid, None)
        await asyncio.sleep(3)


def _find_preset_by_name(name):
    for it in get_user_presets():
        if it["name"].strip().lower() == str(name or "").strip().lower():
            return it
    return None


COMBO_SHARE_MARK = "🔀 "          # 推文 / 日志里标出「这是个组合」的前缀


def _inbox_put(rec):
    """收件箱入队并返回 id；满了返回 None（调用方必须给推送方回执）。"""
    if len(G.inbox) >= MAX_INBOX:
        return None
    iid = _new_pid(set(G.inbox))
    rec["id"] = iid
    G.inbox[iid] = rec
    return iid


def receive_share(payload, only_inbox=False):
    """别人推给我的东西（自定义环境 + 弱网组合）：不同名直接入库；同名先进收件箱。

    两类走完全同一套规则 —— 组合跟自定义环境一样，同名就是两条不同内容撞了名字，
    静默覆盖会把对面排好的段序冲掉，所以一样进收件箱等本机用户决定。

    only_inbox=True 用于「允许旧版设备」的兼容模式：没有 PIN 的推送不可信，
    连重名判断都不做，一律只进收件箱、不自动入库。
    """
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    combos = payload.get("combos") if isinstance(payload.get("combos"), list) else []
    if not items and not combos:
        return {"ok": False, "error": "没有内容"}
    from_name = clean_name(payload.get("from"), "某人")
    from_id = str(payload.get("from_id") or "")[:32]
    accepted, pending, dropped = [], [], []
    for it in items[:20]:
        if not isinstance(it, dict):
            continue
        name = limit_item_name(it.get("name"))
        if not name:
            continue
        prof = _norm_user_profile(name, it.get("profile") or {})
        if not only_inbox and _find_preset_by_name(name) is None:
            newit, _all = upsert_user_preset(name, prof)   # 返回 (item, items)
            accepted.append(newit["name"])
            log_event("share", "收到 %s 推送的环境：%s" % (from_name, newit["name"]))
        else:
            iid = _inbox_put({"from": from_name, "from_id": from_id,
                              "kind": "preset", "name": name, "profile": prof,
                              "at": time.time()})
            if iid is None:
                dropped.append({"name": name, "reason": "收件箱已满"})
                log_event("share", "收件箱已满，丢弃 %s 推送的「%s」" % (from_name, name))
                continue
            pending.append({"id": iid, "name": name, "kind": "preset"})
            why = "（旧版无 PIN，只进收件箱）" if only_inbox else "与本地同名"
            log_event("share", "%s 推送的「%s」%s，等你决定" % (from_name, name, why))
    for c in combos[:20]:
        if not isinstance(c, dict):
            continue
        name = limit_item_name(c.get("name"))
        if not name:
            continue
        raw_steps = c.get("steps") if isinstance(c.get("steps"), list) else []
        raw_steps = raw_steps[:MAX_SHARED_STEPS]
        loop = bool(c.get("loop"))
        if not only_inbox and _find_combo_by_name(name) is None:
            steps, dropped_steps = _resolve_shared_steps(raw_steps)
            if not steps:
                # 一段都落不下来：对方这台机器上没法播这个组合。
                # 关键是要回执给推送方 —— 静默丢弃的话，发送的人会以为推成功了。
                why = "、".join(dropped_steps) if dropped_steps else "没有可用的段"
                dropped.append({"name": name, "reason": why})
                log_event("share", "%s 推送的组合「%s」没有一段能落在本机，已丢弃：%s"
                          % (from_name, name, why))
                continue
            newc, _all = upsert_combo(name, steps, loop)
            if newc is None:
                # 引擎再兜一道：归一化后没段可用就不落库（不能收下一支空壳组合）
                dropped.append({"name": name, "reason": "没有可用的段"})
                log_event("share", "%s 推送的组合「%s」没有一段能落在本机，已丢弃"
                          % (from_name, name))
                continue
            accepted.append(COMBO_SHARE_MARK + newc["name"])
            log_event("share", "收到 %s 推送的弱网组合：%s" % (from_name, newc["name"]))
            if dropped_steps:
                log_event("share", "组合「%s」丢掉 %s" % (newc["name"], "、".join(dropped_steps)))
        else:
            iid = _inbox_put({"from": from_name, "from_id": from_id,
                              "kind": "combo", "name": name,
                              "combo": {"loop": loop, "steps": raw_steps},
                              "at": time.time()})
            if iid is None:
                dropped.append({"name": name, "reason": "收件箱已满"})
                log_event("share", "收件箱已满，丢弃 %s 推送的组合「%s」" % (from_name, name))
                continue
            pending.append({"id": iid, "name": name, "kind": "combo"})
            why = "（旧版无 PIN，只进收件箱）" if only_inbox else "与本地同名"
            log_event("share", "%s 推送的组合「%s」%s，等你决定" % (from_name, name, why))
    return {"ok": True, "accepted": accepted, "pending": pending,
            "dropped": dropped, "name": G.user_name}


def resolve_inbox(iid, action, new_name=None):
    """处理收件箱里的一条：overwrite=覆盖本地 / keep=保留本地 / rename=另存为新名字。

    自定义环境与弱网组合共用这套处理（kind 区分）：组合要在这一步才把段映射到本机
    场景 —— 收件箱里的条子随时可能被「保留本地」丢掉，提前写库会白留一堆自定义场景。
    """
    item = G.inbox.pop(iid, None)
    if item is None:
        return {"ok": False, "error": "该请求已处理或不存在"}
    name = item["name"]
    kind = item.get("kind") or "preset"
    if action == "keep":
        log_event("share", "保留本地的「%s」，丢弃 %s 推送的版本" % (name, item["from"]))
        return {"ok": True, "action": "keep", "name": name, "kind": kind}
    if kind == "combo":
        raw = item.get("combo") or {}
        steps, dropped = _resolve_shared_steps(raw.get("steps"))
        if not steps:
            log_event("share", "%s 推送的组合「%s」没有一段能落在本机，已丢弃"
                      % (item["from"], name))
            return {"ok": False, "error": "这个组合里没有任何一段在本机可用"}
        loop = bool(raw.get("loop"))
        if action == "rename":
            nm = limit_item_name(new_name) or limit_item_name(
                "%s（来自 %s）" % (name, item["from"]), "收到的组合")
            combo, _all = upsert_combo(nm, steps, loop)   # 另存 = 新开一条，不动本地那条
            if combo is None:
                return {"ok": False, "error": "这个组合里没有任何一段在本机可用"}
            log_event("share", "已把 %s 推送的组合另存为「%s」" % (item["from"], combo["name"]))
            if dropped:
                log_event("share", "组合「%s」丢掉 %s" % (combo["name"], "、".join(dropped)))
            return {"ok": True, "action": "rename", "name": combo["name"], "kind": kind}
        cur = _find_combo_by_name(name)
        combo, _all = upsert_combo(name, steps, loop, cid=(cur or {}).get("id"))
        if combo is None:
            # 引擎兜底：不能拿一段都用不上的组合去覆盖本地那支排好的
            return {"ok": False, "error": "这个组合里没有任何一段在本机可用"}
        log_event("share", "已用 %s 推送的版本覆盖组合「%s」" % (item["from"], combo["name"]))
        if dropped:
            log_event("share", "组合「%s」丢掉 %s" % (combo["name"], "、".join(dropped)))
        return {"ok": True, "action": "overwrite", "name": combo["name"], "kind": kind}
    if action == "rename":
        nm = limit_item_name(new_name)
        if not nm:
            nm = limit_item_name("%s（来自 %s）" % (name, item["from"]), "自定义")
        it, _all = upsert_user_preset(nm, item["profile"])
        log_event("share", "已把 %s 推送的环境另存为「%s」" % (item["from"], it["name"]))
        return {"ok": True, "action": "rename", "name": it["name"]}
    exists = _find_preset_by_name(name)
    it, _all = upsert_user_preset(name, item["profile"], pid=(exists or {}).get("id"))
    log_event("share", "已用 %s 推送的版本覆盖「%s」" % (item["from"], it["name"]))
    return {"ok": True, "action": "overwrite", "name": it["name"]}


def _post_share(ip, port, payload, timeout=6):
    req = urllib.request.Request(
        "http://%s:%d/api/share" % (ip, port),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(1 << 20).decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 403/409/413 也带 JSON 原因：不能让调用方只看到「HTTP Error 403」
        try:
            d = json.loads(e.read(1 << 20).decode("utf-8"))
        except Exception:
            d = {}
        if not isinstance(d, dict):
            d = {}
        d.setdefault("ok", False)
        d.setdefault("error", "HTTP %d" % e.code)
        d["http"] = e.code
        return d


async def push_to_peer(peer, item_ids, combo_ids=None):
    """把选中的自定义环境 / 弱网组合推给一个 peer。同步 HTTP 放线程池，别卡住事件循环。

    组合随环境在同一次请求里过去：同类内容在对方那边各自判同名，但整体只是一次
    推送、事件日志上也只留一条「谁推来了什么」，事后回溯两边对得上。
    """
    by_id = {p["id"]: p for p in get_user_presets()}
    items = []
    for iid in (item_ids or []):
        p = by_id.get(iid)
        if p:
            items.append({"id": p["id"], "name": p["name"], "profile": p["profile"]})
    by_cid = {c["id"]: c for c in get_combos()}
    combos = []
    for cid in (combo_ids or []):
        c = by_cid.get(cid)
        if c:
            combos.append({"id": c["id"], "name": c["name"], "loop": bool(c.get("loop")),
                           "steps": share_steps(c)})
    if not items and not combos:
        return {"ok": False, "error": "没找到要推送的内容"}
    ip = str(peer.get("ip") or "")
    try:
        port = int(peer.get("share") or SHARE_PORT)
    except Exception:
        port = SHARE_PORT
    if not (0 < port < 65536):
        port = SHARE_PORT
    if not normalize_pin(peer.get("pin")):
        # 调用方（控制 API / 脚本）通常只传 ip:port：PIN 从发现结果里补，
        # 否则每次推送都得把 PIN 抄一遍，抄错就是 403。
        for p in G.peers.values():
            if (peer.get("id") and p["id"] == peer.get("id")) or p["ip"] == ip:
                peer = dict(peer, pin=p.get("pin") or "", ver=p.get("ver") or 1)
                break
    payload = {"from": G.user_name, "from_id": G.self_id,
               "platform": "windows" if IS_WIN else os.name,
               "ver": SHARE_VER, "pin": normalize_pin(peer.get("pin")),
               "items": items, "combos": combos}
    who = peer.get("name") or ip
    if not ip:
        return {"ok": False, "error": "对方地址为空"}
    try:
        res = await asyncio.get_running_loop().run_in_executor(
            None, functools.partial(_post_share, ip, port, payload))
    except Exception as e:
        log_event("share", "推送给 %s 失败：%s" % (who, str(e)[:60]))
        return {"ok": False, "error": str(e)[:160]}
    if res.get("ok") is False:
        msg = str(res.get("error") or "对方拒收")[:160]
        log_event("share", "推送给 %s 被拒：%s" % (who, msg))
        return {"ok": False, "error": msg}
    accepted = [str(x) for x in (res.get("accepted") or [])]
    pending = res.get("pending") or []
    dropped = res.get("dropped") or []
    if accepted:
        log_event("share", "已推送给 %s：%s" % (res.get("name") or who, "、".join(accepted)))
    if pending:
        log_event("share", "%s 那边已有同名内容，等对方确认：%s"
                  % (res.get("name") or who,
                     "、".join(str(p.get("name")) for p in pending)))
    if dropped:
        # 对方那边一段都用不上，组合被丢了 —— 必须回执，不然发送方以为推成功了
        for d in dropped:
            log_event("share", "%s 那边收不下组合「%s」：%s"
                      % (res.get("name") or who, d.get("name") or "",
                         d.get("reason") or "没有可用的段"))
    return {"ok": True, "to": res.get("name") or who,
            "accepted": accepted, "pending": pending, "dropped": dropped}


async def handle_share(reader, writer):
    """共享端口上的小 HTTP 服务：只认 /api/hello 与 /api/share。
    刻意不复用控制台那一套 —— 这个端口是对局域网开放的，暴露面越小越好。"""
    _tcp_nodelay(writer)
    try:
        try:
            method, path, headers, body = await read_http_request(
                reader, MAX_BODY_SHARE, header_timeout=5.0)
        except HttpError as e:
            send_response(writer, e.code, JSON_CT, json.dumps(
                {"ok": False, "error": e.msg}, ensure_ascii=False).encode())
            return

        if path == "/api/hello":
            send_response(writer, 200, JSON_CT, json.dumps({
                "app": "weaknet", "name": G.user_name, "id": G.self_id,
                "platform": "windows" if IS_WIN else os.name,
                "ver": SHARE_VER, "need_pin": True,
            }, ensure_ascii=False).encode())
        elif method == "POST" and path == "/api/share":
            try:
                d = json.loads(body.decode("utf-8"))
            except Exception:
                d = {}
            if not isinstance(d, dict):
                d = {}
            _peer = writer.get_extra_info("peername")
            _pip = _peer[0] if _peer else None
            err = share_auth_error(d, peer_ip=_pip)
            if err:
                code, msg = err
                log_event("share", "拒收 %s 的推送（HTTP %d）：%s"
                          % (clean_name(d.get("from"), "未知设备"), code, msg))
                send_response(writer, code, JSON_CT, json.dumps(
                    {"ok": False, "error": msg}, ensure_ascii=False).encode())
                return
            legacy = share_proto_ver(d) < SHARE_VER
            res = receive_share(d, only_inbox=legacy)
            send_response(writer, 200, JSON_CT,
                          json.dumps(res, ensure_ascii=False).encode())
        else:
            send_response(writer, 404, JSON_CT, b'{"error":"not found"}')
    except Exception as e:
        # 不静默吞掉：这里一旦出错，推送方只会看到「连接被关闭」，
        # 而接收方其实已经把环境收下了，两边对不上最难排查
        log_event("share", "共享服务处理出错：%s" % str(e)[:100])
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def start_sharing():
    """开启共享服务与局域网发现。任何一步失败都不影响弱网功能本身。"""
    port = SHARE_PORT
    try:
        if not can_bind(port, "0.0.0.0"):
            port = find_free_port(port, "0.0.0.0")
        G.share_server = await asyncio.start_server(handle_share, "0.0.0.0", port)
        G.share_port = port
        log_event("share", "环境共享已开启（端口 %d）" % port)
    except Exception as e:
        G.share_port = 0
        log_event("share", "环境共享未能开启：%s" % str(e)[:60])
    try:
        asyncio.get_running_loop().create_task(discovery_loop())
    except Exception:
        pass


# 故障类型：与 report_gen.FAULT_TYPES 对齐（本模块不 import 报告层，避免反向依赖）。
FAULT_EVENT_TYPES = ("disconnect", "dns", "timeout", "reject", "flap")
# Android ReportGen.summarizeEvents 拿到 fault_counts_full 后是「全量整体替换」，
# 不是只取关心的五类。这里也按全类型出快照（至少含 config/connect/error），
# 否则 Android 报告里的其他事件计数会被这份五类快照抹成 0。
ALL_EVENT_TYPES = FAULT_EVENT_TYPES + ("config", "connect", "error", "idle", "share")


def full_fault_counts():
    """从全量环形缓冲（300 条）统计所有事件类型，不受 GUI 只看最近 60 条影响。

    未知类型（含老快照里的 '?'）也照实计入；report_gen 仍只取 FAULT_TYPES，
    不会被多出来的键影响。
    """
    counts = dict.fromkeys(ALL_EVENT_TYPES, 0)
    for e in G.events:
        t = e.get("type") if isinstance(e, dict) else None
        if t is None:              # 与 report_gen.summarize_events 的 '?' 口径一致
            t = "?"
        counts[t] = counts.get(t, 0) + 1
    return counts


def status_payload():
    ev = list(G.events)[-60:]
    ev.reverse()
    return {
        "version": VERSION,
        "build": BUILD,
        # 必须快照：GUI 导出报告时先取这个字典，再弹两个模态框；期间引擎在
        # 后台线程继续跑，set_profile 原地 clear/update G.profile。若这里直接
        # 给活引用，报告会把「导出后才切换」的参数记成测过的参数。
        "profile": dict(G.profile),
        # 最后一次真正生效过的弱网场景（停止限速后 G.profile 会回到直连，
        # 但 last_profile 保留着测过的内容）——报告据此还原「测了什么」，
        # 否则测完点导出只会看到直连，把真实测过的场景丢掉。
        "last_profile": dict(G.last_profile) if G.last_profile else None,
        "preset_key": G.preset_key,
        "limit_on": G.limit_on,
        "gate_open": G.gate_open,
        "listen": {"host": G.proxy_host, "port": G.proxy_port},
        "lan_ip": G.lan_ip,
        "sysproxy": sysproxy_enabled(),
        "stats": {
            "total": G.conns_total,
            "active": G.conns_active,
            "up": G.bytes_up,
            "down": G.bytes_down,
            "bypass": G.bytes_bypass,
            "uptime": int(time.time() - G.started_at),
        },
        "events": ev,
        # 事件时间线仍是最近 60 条，但故障计数单独从全量缓冲算，
        # report_gen 优先读它，避免早期故障被后来的连接/错误事件挤出后漏报。
        "fault_counts_full": full_fault_counts(),
        "scope": {
            "mode": G.scope_mode,
            "list": G.scope_list,
            "supported": IS_WIN,          # 非 Windows 无法按进程识别，只能接管全部
            "seen": sorted(G.scope_seen.keys(),
                           key=lambda k: -G.scope_seen[k])[:20],
        },
        "me": {
            "name": G.user_name,
            "id": G.self_id,
            "share": G.share_port,
            "discover": G.discover_listen,
            "share_ver": SHARE_VER,
            "allow_legacy": G.allow_legacy,
        },
        "inbox_count": len(G.inbox),
        "spec_hash": SPEC_HASH,
    }


def _share_peer_rows():
    """共享对端列表：/api/peers 与 /api/share/status 共用一份，避免字段漂移。"""
    now = time.time()
    return [{"id": p["id"], "name": p["name"], "ip": p["ip"],
             "platform": p["platform"], "share": p["share"],
             "ver": p.get("ver") or 1,
             "pin_ok": bool(normalize_pin(p.get("pin"))),
             "ago": int(now - p["at"])}
            for p in sorted(G.peers.values(), key=lambda x: x["name"].lower())]


def _share_status_payload():
    """共享只读状态：与 Android /api/share/status 同名同构（Android 旧路径 GET /api/share 为别名）"""
    now = time.time()
    return {
        "name": G.user_name,
        "id": G.self_id,
        "share_port": G.share_port,
        "discover": G.discover_listen,
        "lan_ip": G.lan_ip,
        "inbox_count": len(G.inbox),
        "share_ver": SHARE_VER,
        "allow_legacy": G.allow_legacy,
        "inbox_max": MAX_INBOX,
        "peers": _share_peer_rows(),
        "inbox": [{"id": it["id"], "from": it["from"], "name": it["name"],
                   "ago": int(now - it["at"])}
                  for it in G.inbox.values()],
    }


REASON = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
          405: "Method Not Allowed", 408: "Request Timeout", 409: "Conflict",
          413: "Payload Too Large", 431: "Request Header Fields Too Large",
          501: "Not Implemented"}


class HttpError(Exception):
    """请求读取阶段的错误：handler 直接按 code 回一个 JSON 响应"""

    def __init__(self, code, msg=""):
        Exception.__init__(self, msg or str(code))
        self.code = code
        self.msg = msg


MAX_BODY_SHARE = 256 * 1024      # 共享推送：20 条环境 + 20 支组合远用不到 256KB
MAX_BODY_CTRL = 1 << 20          # 控制台：留足余量，但仍必须有上限


async def read_http_request(reader, max_body, header_timeout=10.0, body_timeout=30.0):
    """读取并校验一个 HTTP/1.1 请求，返回 (method, path, headers, body)。

    超限 / 畸形 / 超时一律抛 HttpError，由 handler 转成具体状态码并关闭连接。
    明确不支持 chunked：猜测长度比拒绝更危险，调用方都是自家客户端，没有兼容包袱。
    """
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), header_timeout)
    except asyncio.IncompleteReadError:
        raise HttpError(400, "incomplete request")
    except asyncio.LimitOverrunError:
        raise HttpError(431, "header too large")
    except (asyncio.TimeoutError, TimeoutError):
        raise HttpError(408, "header timeout")
    if len(head) > MAX_HEADER_BYTES:
        raise HttpError(431, "header too large")
    lines = head.decode("latin1").split("\r\n")
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        raise HttpError(400, "empty request")
    parts = lines[0].split()
    if len(parts) < 2:
        raise HttpError(400, "bad request line")
    method, path = parts[0].upper(), parts[1].split("?")[0]
    if len(lines) - 1 > MAX_HEADER_LINES:
        raise HttpError(431, "too many headers")
    headers = {}
    for h in lines[1:]:
        if ":" not in h:
            raise HttpError(400, "bad header")
        k, v = h.split(":", 1)
        k = k.strip().lower()
        if k in headers:
            if k == "content-length":
                raise HttpError(400, "duplicate content-length")
            headers[k] = headers[k] + ", " + v.strip()
        else:
            headers[k] = v.strip()
    if headers.get("transfer-encoding"):
        raise HttpError(400, "chunked not supported")
    raw_cl = headers.get("content-length", "0").strip()
    if not raw_cl.isdigit():          # 含负数、非数字；这两种都按坏请求处理
        raise HttpError(400, "bad content-length")
    cl = int(raw_cl)
    if cl > max_body:
        raise HttpError(413, "body too large")
    body = b""
    if cl:
        try:
            body = await asyncio.wait_for(reader.readexactly(cl), body_timeout)
        except asyncio.IncompleteReadError:
            raise HttpError(400, "incomplete body")
        except (asyncio.TimeoutError, TimeoutError):
            raise HttpError(408, "body timeout")
    return method, path, headers, body


def _ctrl_host_is_loopback(host):
    """Host 头是否指向回环。只做判定，不返回/记录原值（原值会被展示并随报告导出）。"""
    h = (host or "").strip().lower()
    if not h:
        return False
    if h.startswith("["):                 # [::1]:8899
        h = h.split("]")[0] + "]"
    elif ":" in h:
        h = h.rsplit(":", 1)[0]
    return h in ("127.0.0.1", "localhost", "[::1]")


def ctrl_host_ok(headers):
    """控制口只服务本机：Host 必须指向回环，且不允许浏览器带来的 Origin。

    控制端口本来就绑 127.0.0.1，但浏览器里的任意网页也能向它发简单请求
    （以及 DNS rebinding 把域名解析到 127.0.0.1），所以按 Host/Origin 再挡一层。
    """
    if headers.get("origin"):
        return False
    return _ctrl_host_is_loopback(headers.get("host"))


def send_response(writer, code, ctype, body, extra=""):
    head = (f"HTTP/1.1 {code} {REASON.get(code, 'OK')}\r\n"
            f"Content-Type: {ctype}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"{extra}"
            f"Connection: close\r\n\r\n").encode()
    writer.write(head + body)


JSON_CT = "application/json; charset=utf-8"


def _json_ok(writer, obj):
    """回 200 + JSON（紧凑格式，与历史响应逐字节一致）。"""
    send_response(writer, 200, JSON_CT,
                  json.dumps(obj, ensure_ascii=False).encode())


def _json_err(writer, code, msg, extra=""):
    """回错误 JSON：`{"error":"<msg>"}`。手写格式而非 json.dumps，
    是为了保住历史上没有空格的紧凑写法（脚本按字面串匹配时不会突然失配）。"""
    send_response(writer, code, JSON_CT,
                  ('{"error":%s}' % json.dumps(msg, ensure_ascii=False)).encode(),
                  extra=extra)


def _parse_json(body):
    """解析请求体 JSON，失败一律返回 None（调用方各自判定类型）。"""
    try:
        return json.loads(body)
    except Exception:
        return None


# 只有 GET 就能改状态的端点。浏览器里的任意网页可以用
# `<img src="http://127.0.0.1:8899/api/stop">` 静默触发它（简单请求，无 Origin，
# 也拿不到响应），所以这层必须单独挡：浏览器发出的请求一定带 Sec-Fetch-Site，
# 直接导航是 none，页面里的 img/script/fetch 是 cross-site / same-origin /
# same-site；curl / adb / 脚本不带这个头，照常可用（不加任何鉴权门槛）。
_GET_WRITE_PATHS = frozenset(("/api/stop",))


def _browser_get_write_blocked(method, path, headers):
    """GET 写操作是否来自浏览器（含 Sec-Fetch-Site 且不是直接导航）。"""
    if method != "GET" or path not in _GET_WRITE_PATHS:
        return False
    site = headers.get("sec-fetch-site")
    if site is None:
        return False
    return site.strip().lower() != "none"


# ---- 控制接口路由表 ------------------------------------------------------
#
# 原先 handle_ctrl 是一条 300 多行的 if/elif：新增端点得在中间找位置，方法不对时
# 又只会落到末尾的 404，读起来很难确认「这个路径到底支持哪些方法」。现在按
# 「(方法, 路径) → 处理函数」建表：method 用 "*" 表示不限方法（与原 if/elif 的
# 判定顺序等价）；同一路径 GET/POST 语义不同时分别注册；未注册的 /api/* 一律回
# JSON 404，与旧行为一致。
_CTRL_ROUTES = {}


def _route(path, method="*"):
    def _reg(fn):
        _CTRL_ROUTES[(method, path)] = fn
        return fn
    return _reg


@_route("/")
@_route("/index.html")
async def _h_root(writer, method, body):
    # 网页控制台已下线（只保留 GUI）。留一句说明，免得浏览器打开是空白页、看不出原因
    send_response(writer, 200, "text/plain; charset=utf-8",
                  ("WeakNet 控制台已改为纯桌面界面，网页端不再提供。\n"
                   "请运行 WeakNet.exe 使用图形界面；\n"
                   "本机 HTTP 控制接口仍可用，见 /api/status。\n").encode("utf-8"))


@_route("/api/status")
async def _h_status(writer, method, body):
    _json_ok(writer, status_payload())


@_route("/api/share/status")
async def _h_share_status(writer, method, body):
    # 共享只读状态：与 Android 同名同构；POST /api/share 只在共享端口表示「提交推送」
    if method == "POST":
        send_response(writer, 405, JSON_CT,
                      b'{"error":"method not allowed, use POST /api/push"}',
                      extra="Allow: GET\r\n")
    else:
        _json_ok(writer, _share_status_payload())


@_route("/api/presets")
async def _h_presets(writer, method, body):
    data = [{"key": k, "name": v[0], "cat": v[1]} for k, v in PRESETS.items()]
    _json_ok(writer, data)


@_route("/api/params")
async def _h_params(writer, method, body):
    # 参数规格：两端由同一份 spec.json 生成，脚本/CI 用它做深度比对
    _json_ok(writer, {"spec_hash": SPEC_HASH, "items": PARAM_SPECS})


@_route("/api/upresets")
async def _h_upresets(writer, method, body):
    # 自定义栏：用户命名保存的参数。老版本客户端没有这个接口也照常工作
    _json_ok(writer, {"items": _items_payload(get_user_presets())})


@_route("/api/combos")
async def _h_combos(writer, method, body):
    # 弱网组合列表（每支带 steps 与一行摘要）。字段与 Android 端同名，
    # 同一套脚本在两端都能跑，不用为平台各写一份
    _json_ok(writer, {"items": _combos_payload()})


@_route("/api/combo", "POST")
async def _h_combo_save(writer, method, body):
    # 新增或覆盖一支组合：{id?, name, loop, steps:[{key,minutes,seconds}]}
    data = _parse_json(body)
    if not isinstance(data, dict):
        _json_err(writer, 400, "bad request")
        return
    raw_steps = data.get("steps") if isinstance(data.get("steps"), list) else []
    steps = []
    for s in raw_steps:
        if not isinstance(s, dict):
            continue
        steps.append({"key": str(s.get("key") or ""),
                      "minutes": max(0, int(s.get("minutes", 0) or 0)),
                      "seconds": max(0, int(s.get("seconds", 0) or 0))})
    combo, _all = upsert_combo(data.get("name") or "", steps,
                               loop=bool(data.get("loop")),
                               cid=data.get("id"))
    if combo is None:
        # 一段都留不下：不当成保存成功，否则调用方会以为库里多了一支空组合
        send_response(writer, 400, JSON_CT, json.dumps(
            {"error": "没有一段能用的场景"}, ensure_ascii=False).encode())
        return
    log_event("config", "保存弱网组合：" + combo["name"])
    _json_ok(writer, {"ok": True, "id": combo["id"], "name": combo["name"],
                      "steps": len(combo["steps"])})


@_route("/api/combo/delete", "POST")
async def _h_combo_delete(writer, method, body):
    try:
        cid = json.loads(body).get("id")
    except Exception:
        cid = None
    if not cid:
        _json_err(writer, 400, "bad request")
        return
    combo = find_combo(cid)
    # 删的这支是不是「当前这支」：正在播放（combo_id）或被选中（preset_key）
    # 都要在删除后复位，否则 preset_key 悬挂指向已删除的组合、弱网参数无限期残留。
    active = (str(G.combo_id) == str(cid)
              or G.preset_key == COMBO_KEY_PREFIX + str(cid))
    ok = bool(combo) and delete_combo(cid)[0]
    if ok:
        if active:
            # 与界面删除同一条路径：停播放 + 回直连（set_profile 会同时把
            # preset_key 置 direct、limit_on 置 False）
            await stop_combo()
            await set_profile(default_profile(), "direct")
            log_event("config", "删除组合后切回直连")
        log_event("config", "删除弱网组合：" + combo["name"])
    _json_ok(writer, {"ok": ok})


@_route("/api/upreset", "POST")
async def _h_upreset_save(writer, method, body):
    # 保存（带 id 则覆盖更新那一条，否则新建）
    try:
        data = json.loads(body)
        prof = data.get("profile")
    except Exception:
        data, prof = None, None
    if not isinstance(data, dict) or not isinstance(prof, dict):
        _json_err(writer, 400, "bad request")
        return
    item, _items = upsert_user_preset(data.get("name"), prof, data.get("id"))
    log_event("config", "保存自定义场景：" + item["name"])
    _json_ok(writer, {"ok": True, "id": item["id"], "name": item["name"],
                      "items": _items_payload(get_user_presets())})


@_route("/api/upreset/del", "POST")
async def _h_upreset_del(writer, method, body):
    try:
        pid = json.loads(body).get("id")
    except Exception:
        pid = None
    if not pid:
        _json_err(writer, 400, "bad request")
        return
    ok, left = delete_user_preset(pid)
    if ok:
        log_event("config", "删除自定义场景")
    _json_ok(writer, {"ok": ok, "items": _items_payload(left)})


@_route("/api/preset", "POST")
async def _h_preset_select(writer, method, body):
    key = None
    try:
        key = json.loads(body).get("key")
    except Exception:
        key = None
    if str(key or "").startswith(COMBO_KEY_PREFIX):
        # "c:<id>" = 弱网组合：与 GUI 点组合按钮走同一条路
        # （已开限速就立刻按序播放，没开只选中，不替用户偷偷开始限速）
        cid = str(key)[len(COMBO_KEY_PREFIX):]
        if find_combo(cid) is None:
            _json_err(writer, 400, "bad request")
            return
        applied = await select_combo(cid)
        _json_ok(writer, {"ok": True, "applied": bool(applied),
                          "kind": "combo"})
        return
    try:
        prof = build_preset_profile(key)
    except Exception:
        prof = None
    if prof is None:
        _json_err(writer, 400, "bad request")
    else:
        # 与 GUI 同一条规矩：切场景只是选中，没开启限速时不施加。
        # select_scene 的返回值就是「是否真正生效」，据此区分文案，
        # 别让「选择场景：」被 report_gen 当成已落地场景。
        applied = await select_scene(prof, key)
        log_event("config", ("切换场景：" if applied else "选择场景：") + prof["name"])
        # 与 combo 分支同形——ok 表示「点选中这个操作成功」，applied 才表示
        # 「真的落到引擎参数上」。没开限速时 applied=false 仍是 200。
        _json_ok(writer, {"ok": True, "applied": bool(applied),
                          "kind": "preset"})


@_route("/api/profile", "POST")
async def _h_profile(writer, method, body):
    data = _parse_json(body)
    if not isinstance(data, dict):
        _json_err(writer, 400, "bad request")
        return
    # 与「保存/应用用户预设」走同一套收敛逻辑（_norm_user_profile）：
    # 排除布尔、按 spec 的 params.max 夹上限、忽略未知键。
    #
    # 早期这里自己写了一份 `max(0, v)`，与 _norm_user_profile 不一致，后果：
    #   {"down_kbps": true}  → True 落库（isinstance(True, int) 为真），
    #                          /api/status 返回 "down_kbps": true，前端与令牌桶都读不懂；
    #   {"down_kbps": 1e18}  → 原样落库，而 Android 侧同一请求会被夹到 spec 上限，
    #                          两端对同一份配置给出不同状态 —— 违反「双端同构」。
    # 自定义参数是**唯一直接接收外部任意 JSON 的入口**，恰恰最该用最强的校验。
    prof = _norm_user_profile("自定义", data)
    await set_profile(prof, "custom")
    log_event("config", "应用自定义参数")
    send_response(writer, 200, JSON_CT, b'{"ok":true}')


@_route("/api/run")
async def _h_run(writer, method, body):
    # 限速总开关。GET 视为查询，POST {enable:true/false} 为切换。
    if method == "POST" and body:
        try:
            enable_raw = json.loads(body).get("enable")
        except Exception:
            enable_raw = None
        # 只认真正的布尔值：字符串 "false"、数字 1 等一律视为非法，
        # 否则 bool("false") == True 会把「停止」误执行为「开启限速」
        enable = enable_raw if isinstance(enable_raw, bool) else None
        if enable is None:
            _json_err(writer, 400, "bad request")
        else:
            await set_limit(enable)
            log_event("config", ("开启限速：" + G.profile["name"]) if enable
                      else "停止限速（恢复直连，场景保留：" + G.preset_key + "）")
            send_response(writer, 200, JSON_CT,
                          ('{"ok":true,"limit_on":%s}' % ("true" if G.limit_on else "false")).encode())
    else:
        send_response(writer, 200, JSON_CT,
                      ('{"ok":true,"limit_on":%s}' % ("true" if G.limit_on else "false")).encode())


@_route("/api/stop")
async def _h_stop(writer, method, body):
    # 不限方法：页面若以 GET 发出也要能停（历史上前端漏带 body 导致 GET 404、按钮点了没反应）
    await set_limit(False)
    log_event("config", "停止限速，恢复直连")
    send_response(writer, 200, JSON_CT, b'{"ok":true}')


@_route("/api/sysproxy", "POST")
async def _h_sysproxy(writer, method, body):
    data = _parse_json(body)
    # 只认真布尔：bool("false") == True 会把「关闭」误执行成「接管」；
    # 缺字段/类型不对一律 400，不再静默当作 False 去关别人的系统代理。
    enable = data.get("enable") if isinstance(data, dict) else None
    if not isinstance(enable, bool):
        _json_err(writer, 400, "bad request")
        return
    ok = set_sysproxy(enable)
    log_event("config", "一键" + ("接管本机流量（系统代理已开启）" if enable else "解除接管（系统代理已关闭）"))
    send_response(writer, 200, JSON_CT,
                  ('{"ok":%s}' % ("true" if ok else "false")).encode())


@_route("/api/scope")
async def _h_scope(writer, method, body):
    # 不限方法：GET 取当前设置，POST 更新
    if method == "POST" and body:
        try:
            data = json.loads(body)
            mode, lst = set_scope(data.get("mode"), data.get("list"))
            if mode == "all":
                log_event("config", "接管范围：全部程序")
            elif mode == "only":
                log_event("config", "接管范围：仅 " + (", ".join(lst) or "（空名单=不接管任何程序）"))
            else:
                log_event("config", "接管范围：排除 " + (", ".join(lst) or "（空名单=全部接管）"))
        except Exception:
            _json_err(writer, 400, "bad request")
            return
    _json_ok(writer, {
        "mode": G.scope_mode, "list": G.scope_list,
        "supported": IS_WIN,
    })


@_route("/api/targets")
async def _h_targets(writer, method, body):
    # 可接管的目标。Windows 是进程名，Android 是包名 —— 用同一结构便于两端共用控制台。
    names = set(list_procs()) | set(G.scope_seen.keys()) | set(G.scope_list)
    names = {n for n in names if n and n.lower() != "system"
             and n != "<未识别>"}
    _json_ok(writer, {
        "type": "process",
        "unit": "程序（exe）",
        "items": [{"id": n, "name": n, "desc": ""} for n in sorted(names)],
    })


@_route("/api/listen", "POST")
async def _h_listen(writer, method, body):
    try:
        req = json.loads(body)
        if not isinstance(req, dict):
            req = {}
    except Exception:
        req = {}
    host = req.get("host")
    if host not in ("127.0.0.1", "0.0.0.0"):
        _json_err(writer, 400, "bad host")
    else:
        await restart_proxy(host)
        log_event("config", f"代理监听切换为 {host}:{G.proxy_port}")
        out = {"ok": True,
               "listen": {"host": G.proxy_host, "port": G.proxy_port}}
        _json_ok(writer, out)


# ---- 平台专属能力：Android 有 VPN / 悬浮窗，桌面端没有 ----
# 回 501 而不是 404，脚本才能区分「路径写错」与「该平台没这个能力」
@_route("/api/vpn")
@_route("/api/float")
@_route("/api/float/show")
async def _h_platform_unsupported(writer, method, body):
    send_response(writer, 501, JSON_CT,
                  b'{"error":"not supported on this platform"}')


# ---- 环境共享：用户名 / 局域网 peer / 推送 / 收件箱 ----

@_route("/api/me")
async def _h_me(writer, method, body):
    if method == "POST":
        try:
            nm = json.loads(body).get("name")
        except Exception:
            nm = None
        if nm is not None:
            set_user_name(nm)
            log_event("share", "本机名称改为：" + G.user_name)
    _json_ok(writer, {
        "name": G.user_name, "id": G.self_id, "share": G.share_port,
        "discover": G.discover_listen, "lan_ip": G.lan_ip,
        "platform": "windows" if IS_WIN else os.name,
        "share_ver": SHARE_VER, "pin_len": PIN_LEN,
        "allow_legacy": G.allow_legacy,
    })


@_route("/api/share/pin")
async def _h_share_pin(writer, method, body):
    # 与 Android CtrlServer 同名同语义：GET 看状态，POST 改 PIN / 兼容开关
    if method == "POST":
        try:
            d = json.loads(body)
        except Exception:
            d = {}
        if not isinstance(d, dict):
            d = {}
        if "allow_legacy" in d:
            res = set_allow_legacy(d.get("allow_legacy"))
        else:
            res = set_share_pin(d.get("pin"), bool(d.get("refresh")))
        send_response(writer, 200 if res.get("ok") else 400, JSON_CT,
                      json.dumps(res, ensure_ascii=False).encode())
    else:
        _json_ok(writer, {
            "ok": True, "pin": G.share_pin, "len": PIN_LEN,
            "allow_legacy": G.allow_legacy, "ver": SHARE_VER,
        })


@_route("/api/peers")
async def _h_peers(writer, method, body):
    _json_ok(writer, {
        "items": _share_peer_rows(),
        "self": {"name": G.user_name, "id": G.self_id,
                 "share": G.share_port, "lan_ip": G.lan_ip},
        "discover": G.discover_listen,
    })


@_route("/api/push", "POST")
async def _h_push(writer, method, body):
    try:
        d = json.loads(body)
    except Exception:
        d = {}
    ids = d.get("ids")
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list):
        ids = []
    cids = d.get("combo_ids")
    if isinstance(cids, str):
        cids = [cids]
    if not isinstance(cids, list):
        cids = []
    peer = d.get("peer")
    if isinstance(peer, str) and ":" in peer:      # 兼容 "ip:port" 写法
        ip, _, pstr = peer.partition(":")
        peer = {"ip": ip, "share": int(pstr or SHARE_PORT), "name": ip}
    if not isinstance(peer, dict) or not peer.get("ip"):
        _json_err(writer, 400, "bad peer")
    else:
        res = await push_to_peer(peer, ids, cids)
        _json_ok(writer, res)


@_route("/api/inbox")
async def _h_inbox(writer, method, body):
    items = []
    for it in sorted(G.inbox.values(), key=lambda x: x["at"]):
        row = {"id": it["id"], "from": it["from"], "name": it["name"],
               "kind": it.get("kind") or "preset",
               "ago": int(time.time() - it["at"])}
        if row["kind"] == "combo":
            row["combo"] = it.get("combo") or {}
        else:
            row["profile"] = it.get("profile") or {}
        items.append(row)
    _json_ok(writer, {"items": items})


@_route("/api/inbox/resolve", "POST")
async def _h_inbox_resolve(writer, method, body):
    try:
        d = json.loads(body)
    except Exception:
        d = {}
    res = resolve_inbox(str(d.get("id") or ""), str(d.get("action") or ""),
                        d.get("name"))
    _json_ok(writer, res)


async def handle_ctrl(reader, writer):
    _tcp_nodelay(writer)               # 控制接口是小包高频路径，见 _tcp_nodelay 注释
    try:
        try:
            method, path, headers, body = await read_http_request(
                reader, MAX_BODY_CTRL, header_timeout=10.0)
        except HttpError as e:
            send_response(writer, e.code, JSON_CT, json.dumps(
                {"error": e.msg}, ensure_ascii=False).encode())
            return

        if not ctrl_host_ok(headers):
            # Host/Origin 是攻击者可控的，绝不把原值写进日志或事件环
            # （两者会被 GUI 展示并随报告导出）。只留固定原因 + 脱敏摘要。
            host = headers.get("host") or ""
            origin = headers.get("origin") or ""
            loopback = _ctrl_host_is_loopback(host)
            if origin:
                reason = "带 Origin"
            elif not host.strip():
                reason = "缺少 Host"
            elif not loopback:
                reason = "Host 非回环"
            else:
                reason = "校验未通过"
            log_event("share", "控制接口拒绝（%s，回环=%s，host_len=%d，origin_len=%d）"
                      % (reason, "是" if loopback else "否", len(host), len(origin)))
            send_response(writer, 403, JSON_CT, b'{"error":"forbidden"}')
            return

        # 控制接口只接受 GET / POST。以前完全不看方法，DELETE /api/status 也会返回 200，
        # 脚本里写错方法时会静默「成功」，很难发现。
        if method not in ("GET", "POST") and path.startswith("/api/"):
            send_response(writer, 405, JSON_CT,
                          b'{"error":"method not allowed"}',
                          extra="Allow: GET, POST\r\n")
            return

        # 浏览器发起的 GET 写操作一律拒绝（见 _GET_WRITE_PATHS 的说明）。
        # 放在 Host/Origin 校验之后、分发之前：拒绝时记一条不含原值的日志。
        if _browser_get_write_blocked(method, path, headers):
            log_event("share", "控制接口拒绝：浏览器发起的 GET 写操作（%s）" % path)
            send_response(writer, 403, JSON_CT, b'{"error":"forbidden"}')
            return

        handler = _CTRL_ROUTES.get((method, path)) or _CTRL_ROUTES.get(("*", path))
        if handler is not None:
            await handler(writer, method, body)
        elif path.startswith("/api/"):
            # 未实现的 API 也回 JSON：脚本按 Content-Type 解析时不会拿到一段
            # 纯文本而崩在「不是 dict」上（Android 端同形）
            send_response(writer, 404, JSON_CT, b'{"error":"not found"}')
        else:
            send_response(writer, 404, "text/plain; charset=utf-8", b"not found")
        await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

async def serve(host, proxy_port, ctrl_port):
    """启动代理 + 控制台服务（CLI 与 GUI 共用），返回 (proxy_server, ctrl_server)"""
    G.wake = asyncio.Event()          # 供 nap() 打断挂起连接使用
    # 全局令牌桶必须在事件循环里创建（内部有 asyncio.Lock），且要先于任何连接建立
    G.bucket_up = Bucket(0)
    G.bucket_down = Bucket(0)
    init_identity()                   # 「我是谁」——局域网共享要用
    # 慢回调探针放在这里而不是只在 CLI 入口：GUI、测试夹具、嵌入式调用
    # 都走 serve()，探针才真正覆盖到运行中的那条事件循环。
    _enable_slow_callback_probe()
    # GC 参数与慢回调探针同理：谁启动服务谁生效。启动期一次性调用，
    # 之后进程内的 gen0 阈值/freeze 状态固定。
    _gc_thr = _tune_gc()
    if _gc_thr:
        log("GC 参数：gen0 阈值 %s（默认 700）" % (_gc_thr,))
    # 上次异常退出（崩溃 / taskkill）不会走 atexit，注册表里的系统代理会
    # 一直指向已经没人监听的端口 —— 启动时先还原，别让用户带着断网去排查。
    # 必须放在 start_server 之前：端口还被别人占着时函数内部会跳过。
    release_stale_sysproxy(proxy_port)
    proxy = await asyncio.start_server(handle_proxy, host, proxy_port, limit=1 << 20)
    ctrl = await asyncio.start_server(handle_ctrl, "127.0.0.1", ctrl_port)
    G.proxy_host = host
    # port=0 由系统分配：必须回读实际端口，否则 G.proxy_port 停在 0，
    # 后续 restart_proxy 会绑到一个随机端口，调用方却仍以为在原端口。
    G.proxy_port = (proxy_port if proxy_port
                    else proxy.sockets[0].getsockname()[1])
    G.ctrl_port = (ctrl_port if ctrl_port
                   else ctrl.sockets[0].getsockname()[1])
    G.proxy_server = proxy
    if host not in ("127.0.0.1", "localhost", "::1"):
        log_event("config", "局域网代理已开启（同网段设备可直连 %s:%d）"
                  % (host, G.proxy_port))
    asyncio.get_running_loop().create_task(idle_reap_loop())   # 空闲连接回收
    asyncio.get_running_loop().create_task(_log_flush_loop())  # 缓冲日志定期落盘
    try:
        await start_sharing()         # 共享端口 + 局域网发现（失败不影响弱网本身）
    except Exception as e:
        log_event("share", "环境共享启动异常：" + str(e)[:80])
    return proxy, ctrl


def _enable_slow_callback_probe():
    """诊断用：把事件循环里「堵了超过 N 毫秒」的回调打印出来（默认关闭）。

    用法：`WN_SLOW_CB_MS=50`（配合 PYTHONASYNCIODEBUG=1），_perf_profile.py
    的 --slow-cb 会自动设置。这是「切场景不即时 / 界面卡顿」最直接的证据 ——
    py-spy 只能看到 CPU 热点，看不到「谁把 200ms 的同步调用塞进了事件循环」。

    为什么不常开：asyncio 的 debug 模式要记录每个协程的来源栈，吞吐会明显下降。
    """
    raw = (os.environ.get("WN_SLOW_CB_MS") or "").strip()
    if not raw:
        return
    try:
        sec = max(1, int(raw)) / 1000.0
    except ValueError:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    # asyncio 只有在 debug 模式下才会把「慢回调」打印出来，光设阈值没有用。
    # 这里一并打开 debug（仅在显式设置 WN_SLOW_CB_MS 时，默认为关闭）。
    try:
        loop.set_debug(True)
    except Exception:
        pass
    loop.slow_callback_duration = sec
    try:
        log("[perf] 慢回调告警阈值 %.0f ms（仅诊断使用，勿用于正式运行）" % (sec * 1000))
    except Exception:
        pass


async def main():
    ap = argparse.ArgumentParser(description="WeakNet —— App 弱网测试工具")
    ap.add_argument("--host", default="127.0.0.1",
                    help="代理监听地址，手机测试用 0.0.0.0（默认 127.0.0.1）")
    ap.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT,
                    help="代理端口（默认 %d）" % DEFAULT_PROXY_PORT)
    ap.add_argument("--ctrl-port", type=int, default=DEFAULT_CTRL_PORT,
                    help="控制台端口（默认 %d）" % DEFAULT_CTRL_PORT)
    args = ap.parse_args()

    _enable_slow_callback_probe()

    G.proxy_host = args.host
    G.proxy_port = args.proxy_port
    G.lan_ip = detect_lan_ip()

    # 退出（含 Ctrl+C、被终止）时自动解除系统代理接管
    atexit.register(_auto_release_sysproxy)
    atexit.register(flush_logs)          # 把缓冲里还没落盘的事件写下去

    # ---- 端口冲突处理：避免双击 EXE 时因为端口被占而直接崩溃退出 ----
    ctrl_port = args.ctrl_port
    if not can_bind(ctrl_port, "127.0.0.1"):
        if is_weaknet_running(ctrl_port):
            url = f"http://127.0.0.1:{ctrl_port}/"
            log(f"[WeakNet] 已经有一个实例在运行（控制接口 {url}）")
            log("[WeakNet] 本实例不再重复启动，请直接使用已有的那个界面。")
            if FROZEN:
                try:
                    input("  按回车关闭本窗口...")
                except Exception:
                    pass
            return
        ctrl_port = find_free_port(ctrl_port, "127.0.0.1")
        log(f"[WeakNet] 控制台端口 {args.ctrl_port} 被其它程序占用，自动改用 {ctrl_port}")

    if not can_bind(G.proxy_port, G.proxy_host):
        newp = find_free_port(G.proxy_port, G.proxy_host)
        log(f"[WeakNet] 代理端口 {G.proxy_port} 被占用，自动改用 {newp}")
        G.proxy_port = newp

    def _bye(signum, frame):
        sys.exit(0)

    for _sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if _sig is not None:
            try:
                signal.signal(_sig, _bye)
            except Exception:
                pass

    G.proxy_server, ctrl = await serve(G.proxy_host, G.proxy_port, ctrl_port)

    url = f"http://127.0.0.1:{ctrl_port}/"
    log("=" * 58)
    log("  WeakNet 弱网模拟工具已启动")
    log(f"  控制接口 :  {url}  (HTTP API，供脚本调用；界面请用 WeakNet.exe)")
    log(f"  代理地址 :  {G.proxy_host}:{G.proxy_port}")
    log(f"  局域网 IP:  {G.lan_ip}  (手机测试请在界面里点「开启局域网访问」)")
    log("  限速状态 :  未开启 —— 启动后默认不限制任何流量，请在界面点「▶ 开启限速」")
    log("  关闭本窗口即停止工具（会自动解除系统代理接管）")
    log("=" * 58)

    async with G.proxy_server, ctrl:
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Bye.")
    except Exception:
        traceback.print_exc()
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        if FROZEN:
            try:
                input("\n启动失败，详情见 weaknet.log。按回车关闭窗口...")
            except Exception:
                pass
        sys.exit(1)
