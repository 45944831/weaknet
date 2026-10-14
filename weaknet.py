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
import json
import os
import random
import signal
import socket
import sys
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from collections import deque
from pathlib import Path

# 打包成 EXE 后 stdout 是块缓冲，窗口里看不到任何输出 —— 强制行缓冲
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

FROZEN = getattr(sys, "frozen", False)   # True = 以 EXE 方式运行


def log(*a):
    """立刻可见的输出（同时写一份日志到 EXE 同目录的 weaknet.log）
    注意：GUI（--windowed）模式下 sys.stdout 为 None，print 会抛异常，必须吞掉。"""
    msg = " ".join(str(x) for x in a)
    try:
        print(msg, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def resource_path(name):
    """支持 PyInstaller 打包（_MEIPASS）与源码运行两种模式的资源定位"""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / name if base else Path(__file__).with_name(name)


def _app_dir():
    """EXE 所在目录（源码运行时为脚本目录），用于放日志文件"""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


LOG_FILE = _app_dir() / "weaknet.log"


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

_PROC_NAME_CACHE = {"at": 0.0, "map": {}}    # pid -> exe 名，带 TTL，避免每次新连接都做进程快照
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
    c = _PROC_NAME_CACHE
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


def proc_name_of_peer(peer_port, local_port):
    """反查：连到本代理 local_port 的、来自 peer_port 的连接属于哪个进程。
    返回 exe 名（小写）；查不到或非 Windows 返回 None。

    为什么要重试：连接刚被 accept 的瞬间，Windows 的 TCP 连接表偶尔还没登记这一行，
    查不到就会把该连接当「未识别」放行（名单内的程序反而没被接管）。
    只在「表里压根找不到这行」时重试，找到行就立即返回，正常路径不增加任何延迟。
    """
    if not IS_WIN:
        return None
    for attempt in range(_TCP_LOOKUP_RETRIES):
        try:
            for la, lp, _ra, rp, pid in _tcp_rows():
                if lp == peer_port and rp == local_port:
                    name = _lookup_proc_name(pid)
                    return name.lower() if name else None
        except Exception:
            return None
        if attempt + 1 < _TCP_LOOKUP_RETRIES:
            time.sleep(_TCP_LOOKUP_GAP)
    return None


def list_procs():
    """列出候选进程名（供界面勾选）：当前有 TCP 连接的进程 + 常见浏览器/应用。
    非 Windows 返回空列表（此时只能「接管全部」）。"""
    if not IS_WIN:
        return []
    try:
        names = set(_snapshot_proc_names().values())
        rows = _tcp_rows()
        pid2n = {r[4]: _PROC_NAME_CACHE["map"].get(r[4]) for r in rows}
        live = {n.lower() for n in pid2n.values() if n and n.lower() != "system"}
        live.discard("")
        common = ["chrome.exe", "msedge.exe", "firefox.exe", "wechat.exe",
                  "weixin.exe", "qq.exe", "postman.exe", "fiddler.exe",
                  "python.exe", "node.exe", "code.exe"]
        out = sorted(live | set(common))
        return out
    except Exception:
        return []


# 未命中「接管范围」的程序走直通：原样转发，不注入任何弱网参数。
# 用一个固定的全零 profile，且不随 G.profile 变化（否则切换场景会误伤不该接管的程序）。
BYPASS_PROFILE = None   # 延迟到 default_profile 定义后初始化


def default_profile(name="直连（不限制）"):
    return {
        "name": name,
        "latency_up_ms": 0,        # 上行初始延迟
        "latency_down_ms": 0,      # 下行初始延迟
        "jitter_ms": 0,            # 随机抖动（±）
        "down_kbps": 0,            # 下行带宽（kbps，0=不限）
        "up_kbps": 0,              # 上行带宽（kbps，0=不限）
        "loss_pct": 0.0,           # 丢包率（表现为卡顿停顿）
        "stall_ms": 600,           # 单次卡顿时长
        "disconnect_pct": 0.0,     # 随机断连率（每数据块判定）
        "timeout_pct": 0.0,        # 黑洞超时率（每连接判定）
        "dns_fail_pct": 0.0,       # DNS 失败率（每连接判定）
        "reject_pct": 0.0,         # 立即拒连率（每连接判定）
        "flap_sec": 0,             # 周期断网间隔（0=关闭）
        "flap_blackout_sec": 2,    # 每次断网时长
    }


def has_limits(prof):
    """这组参数是否真的会「动」流量（与全默认的直连有没有区别）。

    用来推断限速总开关的语义：应用了带内容的场景就等于把限速打开，
    回到全零就等于是关掉。这样网页 / GUI / 接口三个入口不用各自维护一份状态。
    """
    d = default_profile()
    return any(prof.get(k) != d.get(k) for k in d if k != "name")


# key: (名称, 分类, 参数覆盖)
#
# 2026-10 清理过时的制式：2G EDGE / 2G GPRS / 56K 拨号 / 3G 已陆续退网
# （国内外运营商关停 2G/3G、拨号上网早已不存在），留着只会让测试同学误以为
# 现网还有这些档位。想要「极慢网」直接用自定义参数压带宽即可，不必挂靠已退网的制式。
PRESETS = {
    "direct": ("直连（不限制）", "常规网络", {}),
    "wifi": ("Wi-Fi 良好", "常规网络",
             {"latency_up_ms": 10, "latency_down_ms": 30, "jitter_ms": 5,
              "down_kbps": 50000, "up_kbps": 20000}),
    "4g": ("4G LTE", "常规网络",
           {"latency_up_ms": 40, "latency_down_ms": 60, "jitter_ms": 15,
            "down_kbps": 20000, "up_kbps": 10000}),
    # 5G：空口延迟比 4G 低一个量级（实测良好覆盖下 RTT 约 10~20ms），
    # 下行按 100Mbps 给，够触发「秒开」级别的体验，也能跑满多数 App 的预加载
    # 4G 弱网：地下车库 / 城中村 / 演唱会现场这类「有 4G 图标但速率塌了」的场景，
    # 速率比 5G 弱覆盖再低一档，丢包与偶发断流更明显（4G 的重传比 5G 慢）
    "4g_weak": ("4G 弱网（边缘/室内）", "弱网环境",
                {"latency_up_ms": 70, "latency_down_ms": 110, "jitter_ms": 45,
                 "down_kbps": 3000, "up_kbps": 800, "loss_pct": 4,
                 "disconnect_pct": 1, "stall_ms": 1200}),
    "5g": ("5G（良好覆盖）", "常规网络",
           {"latency_up_ms": 12, "latency_down_ms": 20, "jitter_ms": 8,
            "down_kbps": 100000, "up_kbps": 40000}),
    # 5G 弱覆盖：室内深处 / 小区边缘 / 地铁口，常见的是 SA 还能驻留但速率塌到几 Mbps，
    # 伴随少量丢包与偶发断流（比 Wi-Fi 弱信号更容易「看着有信号实际传不动」）
    "5g_weak": ("5G 弱覆盖（室内/边缘）", "弱网环境",
                {"latency_up_ms": 50, "latency_down_ms": 80, "jitter_ms": 35,
                 "down_kbps": 6000, "up_kbps": 1500, "loss_pct": 2,
                 "disconnect_pct": 1, "stall_ms": 1000}),
    "wifi_weak": ("弱 Wi-Fi（隔墙/远端）", "弱网环境",
                  {"latency_up_ms": 60, "latency_down_ms": 120, "jitter_ms": 60,
                   "down_kbps": 5000, "up_kbps": 2000, "loss_pct": 3}),
    "congestion": ("网络拥塞", "弱网环境",
                   {"latency_up_ms": 200, "latency_down_ms": 400, "jitter_ms": 100,
                    "down_kbps": 512, "up_kbps": 256, "loss_pct": 5}),
    "satellite": ("卫星链路（高延迟）", "弱网环境",
                  {"latency_up_ms": 700, "latency_down_ms": 700, "jitter_ms": 80,
                   "down_kbps": 2000, "up_kbps": 512}),
    # 高丢包：带宽看着够（能起连、能握手），但每三个包就丢一个 ——
    # 典型症状是视频卡成 PPT、图片加载到一半、长连接反复重试
    "high_loss": ("高丢包（30% 丢包）", "弱网环境",
                  {"latency_up_ms": 100, "latency_down_ms": 150, "jitter_ms": 80,
                   "down_kbps": 8000, "up_kbps": 3000, "loss_pct": 30,
                   "stall_ms": 800}),
    # 高延迟：带宽正常、也不丢包，纯粹是往返慢（跨境链路、卫星回传、代理中转），
    # 用来复现「点一下要等半天才有反应」而不是「传不动」
    "high_latency": ("高延迟（RTT 800ms）", "弱网环境",
                     {"latency_up_ms": 400, "latency_down_ms": 400, "jitter_ms": 60,
                      "down_kbps": 10000, "up_kbps": 5000}),
    "highspeed_rail": ("高铁 / 地铁（高速移动）", "极端环境",
                       {"latency_up_ms": 60, "latency_down_ms": 100, "jitter_ms": 150,
                        "down_kbps": 1500, "up_kbps": 512, "loss_pct": 8,
                        "disconnect_pct": 2, "stall_ms": 1200}),
    "elevator": ("电梯 / 地下室（信号极差）", "极端环境",
                 {"latency_up_ms": 500, "latency_down_ms": 800, "jitter_ms": 300,
                  "down_kbps": 256, "up_kbps": 128, "loss_pct": 20,
                  "disconnect_pct": 10, "stall_ms": 2000}),
    "flapping": ("频繁断连（网络切换）", "极端环境",
                 {"latency_up_ms": 80, "latency_down_ms": 120, "jitter_ms": 50,
                  "down_kbps": 8000, "up_kbps": 4000, "disconnect_pct": 5,
                  "flap_sec": 8, "flap_blackout_sec": 3}),
    "blackhole": ("黑洞（请求无响应）", "极端环境", {"timeout_pct": 100}),
    "dns_fail": ("DNS 故障（无法解析域名）", "极端环境", {"dns_fail_pct": 100}),
    "airplane": ("完全断网（立即拒连）", "极端环境", {"reject_pct": 100}),
}


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


# 自定义参数每个字段的上限。百分比类最多 100，时间/带宽类留足余量但别到荒谬的量级
PROF_LIMITS = {
    "latency_up_ms": 10000, "latency_down_ms": 10000, "jitter_ms": 5000,
    "down_kbps": 1000000, "up_kbps": 1000000,
    "loss_pct": 100, "disconnect_pct": 100, "timeout_pct": 100,
    "dns_fail_pct": 100, "reject_pct": 100,
    "stall_ms": 60000, "flap_sec": 3600, "flap_blackout_sec": 600,
}


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
        except Exception:
            continue
        raw = data.get("items") if isinstance(data, dict) else data
        out = []
        for it in (raw or []):
            try:
                name = str(it.get("name", "")).strip()
                if not name:
                    continue
                out.append({"id": str(it.get("id") or ""),
                            "name": name[:40],
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


def _dump_user_presets(items):
    """写回磁盘（挑第一个可写的位置）。失败返回 False"""
    payload = json.dumps({"version": 1, "items": _items_payload(items)},
                         ensure_ascii=False, indent=2).encode("utf-8")
    for f in _preset_files():
        try:
            if f.parent and not f.parent.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(payload)
            return True
        except Exception:
            continue
    return False


def get_user_presets():
    """内存缓存的自定义场景列表（首次调用时从磁盘加载）"""
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
    """同名自动加序号，避免列表里出现两个一样名字分不清"""
    others = {it["name"] for it in items if it["id"] != self_id}
    if name not in others:
        return name
    for i in range(2, 100):
        cand = f"{name} ({i})"
        if cand not in others:
            return cand
    return f"{name} {int(time.time())}"


def upsert_user_preset(name, prof, pid=None):
    """新增或覆盖一条自定义场景，返回 (item, items)"""
    items = get_user_presets()
    name = str(name or "").strip()[:40] or "自定义"
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
    conns_total = 0
    conns_active = 0
    bytes_up = 0
    bytes_down = 0
    active = set()            # 在途 writer，整体断网时掐断
    flap_task = None
    proxy_server = None
    proxy_host = "127.0.0.1"
    proxy_port = 8888
    lan_ip = "127.0.0.1"
    started_at = time.time()
    gen = 0                     # 参数代数：每次切换场景 +1，用于唤醒在途连接
    wake = None                 # asyncio.Event：参数切换时唤醒挂起（黑洞/卡顿）的连接
    # 接管范围：all=全部程序 / only=仅下列程序 / exclude=排除下列程序
    scope_mode = "all"
    scope_list = []             # exe 名（小写）列表
    scope_seen = {}             # 观测到的进程名 -> 次数（便于用户选择要接管谁）
    bytes_bypass = 0            # 未接管程序的直通流量（不计入弱网统计）
    user_presets = None         # 自定义栏（用户保存的场景），None = 尚未从磁盘加载
    # ---- 环境共享 ----
    self_id = ""                # 本机标识（区分广播是不是自己发的）
    user_name = ""              # 局域网上显示的名字
    peers = {}                  # peer id -> {id,name,ip,platform,share,at}
    inbox = {}                  # 收到的同名环境，等本地决定保留 / 覆盖
    share_port = 0              # 共享服务端口（0 = 未开启）
    share_server = None
    discover_listen = False     # 是否成功监听了 UDP 广播


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
    G.events.append({"t": time.strftime("%H:%M:%S"), "type": etype, "msg": msg})


async def nap(seconds, gen):
    """可被「切换场景 / 停止模拟」立即打断的 sleep。
    返回 True 表示被参数切换打断（调用方应立即按新参数继续）。"""
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
            await asyncio.sleep(min(remain, 0.25))
            continue
        try:
            await asyncio.wait_for(w.wait(), timeout=min(remain, 0.25))
        except (asyncio.TimeoutError, TimeoutError):
            continue
        except Exception:
            await asyncio.sleep(min(remain, 0.25))


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


_prev_proxy = None   # 首次接管前备份的原始值，解除时还原
_sysproxy_on = False # 当前是否是本工具开启的系统代理


def _auto_release_sysproxy():
    """退出时自动解除接管，避免工具被关掉后系统代理指向已失效的端口"""
    if _sysproxy_on:
        try:
            set_sysproxy(False)
            print("[WeakNet] 已自动解除系统代理接管")
        except Exception:
            pass


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
    global _prev_proxy, _sysproxy_on
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
        if enable and _prev_proxy is None:
            _prev_proxy = _reg_values(k, names)
        if enable:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ,
                              f"127.0.0.1:{G.proxy_port}")
            winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ,
                              "localhost;127.*;<local>")
        else:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            if _prev_proxy:                       # 还原接管前的配置
                for n in names:
                    saved = _prev_proxy.get(n)
                    if saved:
                        winreg.SetValueEx(k, n, 0, saved[1], saved[0])
            _prev_proxy = None
        winreg.CloseKey(k)
        ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)  # SETTINGS_CHANGED
        ctypes.windll.wininet.InternetSetOptionW(0, 37, 0, 0)  # REFRESH
        _sysproxy_on = bool(enable)
        return True
    except Exception:
        return False


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
    """令牌桶。速率随当前场景实时变化（切场景/停止后已在途的连接也会立即跟上）"""
    __slots__ = ("rate", "tokens", "last", "lock", "kbps")

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
        self.rate = kbps * 128.0 if kbps else 0.0   # kbps * 1024 / 8 = B/s
        self.tokens = min(self.tokens, self.rate) if self.rate else 0.0

    async def consume(self, n):
        """按字节消耗令牌。注意：单次数据量可能远大于 1 秒的配额（例如 2G 下读取
        64KB），必须分多次扣减，否则 'tokens >= n' 永不成立会死循环。"""
        if not self.rate:
            return
        async with self.lock:
            remain = n
            while remain > 0:
                if not self.rate:
                    # 限速被取消（切换直连 / 停止模拟）：立刻放行，不能再用 rate 做除法
                    self.tokens = 0.0
                    return
                now = time.monotonic()
                # 令牌上限 = 1 秒突发
                self.tokens = min(self.tokens + (now - self.last) * self.rate, self.rate)
                self.last = now
                take = min(remain, self.tokens)
                if take > 0:
                    self.tokens -= take
                    remain -= take
                    if remain <= 0:
                        return
                # 还要等剩下的字节补齐，单次最多等 0.25s，避免长时间独占
                await asyncio.sleep(min(max(remain / self.rate, 0.001), 0.25))


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
        self.prof = None       # fixed 时使用的固定 profile（BYPASS_PROFILE）

    def register(self, w):
        self.writers.append(w)
        G.active.add(w)

    def abort_all(self):
        for w in self.writers:
            G.active.discard(w)
            abort_writer(w)

    def cleanup(self):
        for w in self.writers:
            G.active.discard(w)
            try:
                w.close()
            except Exception:
                pass


def _latency_delay(prof, direction):
    base = prof["latency_up_ms"] if direction == "up" else prof["latency_down_ms"]
    jit = prof["jitter_ms"]
    return max(0.0, (base + random.uniform(-jit, jit)) / 1000.0)


# --------------------------------------------------------------------------
# 双向管道：延迟 + 抖动 + 限速 + 丢包卡顿 + 随机断连
# --------------------------------------------------------------------------

async def pipe(reader, writer, bucket, prof, direction, ctx):
    """注意：每块数据都重新读取 G.profile，保证「停止模拟 / 切换场景」对已在途的连接立即生效"""
    gen = G.gen
    d = _latency_delay(prof, direction)
    if d:
        await nap(d, gen)
    while True:
        try:
            data = await reader.read(65536)
        except (ConnectionError, OSError):
            return
        if not data:
            return
        n = len(data)
        if ctx.fixed:                          # 直通流量不计入弱网统计
            G.bytes_bypass += n
        elif direction == "up":
            G.bytes_up += n
        else:
            G.bytes_down += n
        # 直通连接用固定 profile，其余实时跟随 G.profile（切场景后立即生效）。
        # 必须用 ctx 而非全局：pipe 是并发的，多条连接同时跑，全局会互相串味。
        prof = ctx.prof if ctx.fixed else G.profile
        rate_key = "up_kbps" if direction == "up" else "down_kbps"
        bucket.sync(prof.get(rate_key, 0))     # 停止/放宽限速后立即恢复全速
        # 丢包 -> 表现为一段时间的传输停顿（TCP 重传效果）
        if prof["loss_pct"] and random.random() * 100 < prof["loss_pct"]:
            await nap(prof["stall_ms"] / 1000.0 * (0.5 + random.random()), gen)
            prof = G.profile
        # 随机断连 -> 立即掐断整条连接（两侧 RST/FIN）
        if prof["disconnect_pct"] and random.random() * 100 < prof["disconnect_pct"]:
            log_event("disconnect", "模拟断连：" + ctx.label)
            ctx.abort_all()
            raise Abort()
        await bucket.consume(n)
        try:
            writer.write(data)
            await writer.drain()
        except (ConnectionError, OSError):
            return


async def run_tunnel(ctx, c_reader, s_writer, s_reader, c_writer, prof):
    up = asyncio.create_task(pipe(c_reader, s_writer, Bucket(prof["up_kbps"]), prof, "up", ctx))
    down = asyncio.create_task(pipe(s_reader, c_writer, Bucket(prof["down_kbps"]), prof, "down", ctx))
    # 注：Bucket 的速率会在 pipe 内随当前场景实时同步（见 pipe/bucket.sync）
    done, pending = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for t in done:
        try:
            t.exception()
        except Exception:
            pass


# --------------------------------------------------------------------------
# 代理协议处理（CONNECT 隧道 + 普通 HTTP 转发）
# --------------------------------------------------------------------------

UPSTREAM_502 = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


async def handle_proxy(reader, writer):
    G.conns_total += 1
    G.conns_active += 1
    ctx = ConnCtx("?")
    try:
        # ---- 识别来源进程，决定这条连接是否被接管 ----
        pname = None
        try:
            peer = writer.get_extra_info("peername")
            if peer and len(peer) >= 2:
                pname = proc_name_of_peer(peer[1], G.proxy_port)
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
            ctx.prof = BYPASS_PROFILE

        # 周期断网黑窗期：只掐断被接管的连接（未接管的程序不受影响）
        if (not G.gate_open) and not bypass:
            log_event("flap", "黑窗期：拒绝新连接")
            abort_writer(writer)
            return

        try:
            line = await asyncio.wait_for(reader.readline(), timeout=600)
        except (TimeoutError, ConnectionError, OSError):
            return
        if not line:
            return
        try:
            method, target, _ver = line.decode("latin1").split()
        except ValueError:
            return
        method = method.upper()

        # 读完请求头
        headers = []
        while True:
            h = await reader.readline()
            if not h or h in (b"\r\n", b"\n"):
                break
            headers.append(h)

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
        if prof["dns_fail_pct"] and random.random() * 100 < prof["dns_fail_pct"]:
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
            log_event("connect", f"HTTP 代理 {label}{path[:60]}")
            # 重写为源站请求，去代理头，单请求短连接（简单可靠）
            out = [f"{method} {path} HTTP/1.1".encode()]
            for h in headers:
                k = h.split(b":", 1)[0].strip().lower()
                if k in (b"proxy-connection", b"connection", b"keep-alive"):
                    continue
                out.append(h.rstrip(b"\r\n"))
            out.append(b"Connection: close")
            s_writer.write(b"\r\n".join(out) + b"\r\n\r\n")
            await s_writer.drain()
            await run_tunnel(ctx, reader, s_writer, s_reader, writer, prof)
    except Abort:
        pass
    except (ConnectionError, OSError):
        pass
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
            for w in list(G.active):
                abort_writer(w)
            await asyncio.sleep(G.profile["flap_blackout_sec"])
            G.gate_open = True
            log_event("flap", "网络恢复")
    except asyncio.CancelledError:
        G.gate_open = True
        raise


async def set_profile(prof, key):
    """切换场景。

    关键：原地更新 G.profile（而不是替换成新 dict）。已在途的连接持有的是同一个
    dict 引用，这样「停止模拟 / 切换场景」能让正在传输的连接立刻生效，
    否则它们会一直拿着旧参数跑完（表现为点了停止还是慢、黑洞里的请求一直挂着）。
    """
    cur = G.profile
    cur.clear()
    cur.update(prof)
    G.profile = cur
    G.preset_key = key
    G.gen += 1                     # 代数 +1：唤醒所有挂起（黑洞/卡顿）的连接
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
        G.flap_task = None
    G.gate_open = True
    # 应用带内容的场景即视为「开启限速」，回到全零（直连）即视为「关闭」
    G.limit_on = has_limits(prof)
    if G.limit_on:
        G.last_profile = dict(prof)     # 记住最后一次生效的场景，供「再次开启」复原
    if prof["flap_sec"] > 0:
        G.flap_task = asyncio.create_task(flap_loop())


async def select_scene(prof, key):
    """只「选中」一支场景，不启动弱网。

    没点总开关之前不碰引擎参数（否则选一下场景流量就被限住了，等于偷偷开始测试）：
    只记下 preset_key，等用户点「开启限速」时由 set_limit(True) 按这个 key 复原。
    已经在测试中时，切场景照旧立即生效。
    """
    G.preset_key = key
    if has_limits(prof):
        G.last_profile = dict(prof)
    if not G.limit_on:
        return False
    await set_profile(prof, key)
    G.limit_on = True
    return True


async def set_limit(on):
    """限速总开关。

    关闭不是丢掉用户选的场景：preset_key 保留，下次开启直接复原，
    免得每次开关都要重选一遍。开启时若当前是「直连」，就按 preset_key 重建。
    """
    on = bool(on)
    key = G.preset_key
    if on:
        try:
            prof = build_preset_profile(key)
        except Exception:
            # key 是 "custom"（自定义参数）一类没有预设可查的情况：
            # 用最后一次生效过的场景复原，避免开启时把用户调好的参数冲掉
            prof = G.last_profile or default_profile()
        await set_profile(prof, key)
    else:
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
    except Exception:
        pass
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
    if changed:
        save_settings(d)
    G.self_id = d["id"]
    G.user_name = d["name"]
    return d


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
        G.peers[pid] = {
            "id": pid,
            "name": clean_name(msg.get("name"), "匿名"),
            "ip": addr[0],
            "platform": str(msg.get("platform") or "")[:16],
            "share": int(msg.get("share") or SHARE_PORT),
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
        "share": G.share_port or SHARE_PORT, "ver": 1,
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


def receive_share(payload):
    """别人推给我的环境：不同名直接入库；同名先进收件箱，等本地用户决定
    保留还是覆盖（同名静默覆盖会把人家调好的参数冲掉，不能替用户做主）。"""
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "没有内容"}
    from_name = clean_name(payload.get("from"), "某人")
    from_id = str(payload.get("from_id") or "")
    accepted, pending = [], []
    for it in items[:20]:
        if not isinstance(it, dict):
            continue
        name = clean_name(it.get("name"), "")
        if not name:
            continue
        prof = _norm_user_profile(name, it.get("profile") or {})
        if _find_preset_by_name(name) is None:
            newit, _all = upsert_user_preset(name, prof)   # 返回 (item, items)
            accepted.append(newit["name"])
            log_event("share", "收到 %s 推送的环境：%s" % (from_name, newit["name"]))
        else:
            iid = _new_pid(set(G.inbox))
            G.inbox[iid] = {"id": iid, "from": from_name, "from_id": from_id,
                            "name": name, "profile": prof, "at": time.time()}
            pending.append({"id": iid, "name": name})
            log_event("share", "%s 推送的「%s」与本地同名，等你决定" % (from_name, name))
    return {"ok": True, "accepted": accepted, "pending": pending, "name": G.user_name}


def resolve_inbox(iid, action, new_name=None):
    """处理收件箱里的一条：overwrite=覆盖本地 / keep=保留本地 / rename=另存为新名字"""
    item = G.inbox.pop(iid, None)
    if item is None:
        return {"ok": False, "error": "该请求已处理或不存在"}
    name = item["name"]
    if action == "keep":
        log_event("share", "保留本地的「%s」，丢弃 %s 推送的版本" % (name, item["from"]))
        return {"ok": True, "action": "keep", "name": name}
    if action == "rename":
        nm = clean_name(new_name, "")
        if not nm:
            nm = clean_name("%s（来自 %s）" % (name, item["from"]), "")
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
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


async def push_to_peer(peer, item_ids):
    """把本地的自定义环境推给一个 peer。同步 HTTP 放线程池，别卡住事件循环。"""
    by_id = {p["id"]: p for p in get_user_presets()}
    items = []
    for iid in (item_ids or []):
        p = by_id.get(iid)
        if p:
            items.append({"id": p["id"], "name": p["name"], "profile": p["profile"]})
    if not items:
        return {"ok": False, "error": "没找到要推送的环境"}
    payload = {"from": G.user_name, "from_id": G.self_id,
               "platform": "windows" if IS_WIN else os.name, "items": items}
    ip = str(peer.get("ip") or "")
    port = int(peer.get("share") or SHARE_PORT)
    who = peer.get("name") or ip
    if not ip:
        return {"ok": False, "error": "对方地址为空"}
    try:
        res = await asyncio.get_running_loop().run_in_executor(
            None, functools.partial(_post_share, ip, port, payload))
    except Exception as e:
        log_event("share", "推送给 %s 失败：%s" % (who, str(e)[:60]))
        return {"ok": False, "error": str(e)[:160]}
    accepted = [str(x) for x in (res.get("accepted") or [])]
    pending = res.get("pending") or []
    if accepted:
        log_event("share", "已推送给 %s：%s" % (res.get("name") or who, "、".join(accepted)))
    if pending:
        log_event("share", "%s 那边已有同名环境，等对方确认：%s"
                  % (res.get("name") or who,
                     "、".join(str(p.get("name")) for p in pending)))
    return {"ok": True, "to": res.get("name") or who,
            "accepted": accepted, "pending": pending}


async def handle_share(reader, writer):
    """共享端口上的小 HTTP 服务：只认 /api/hello 与 /api/share。
    刻意不复用控制台那一套 —— 这个端口是对局域网开放的，暴露面越小越好。"""
    try:
        line = await asyncio.wait_for(reader.readline(), 5)
        parts = line.decode("latin1").split()
        if len(parts) < 2:
            return
        method, path = parts[0].upper(), parts[1].split("?")[0]
        headers = []
        while True:
            h = await reader.readline()
            if not h or h in (b"\r\n", b"\n"):
                break
            headers.append(h)
        cl = 0
        for h in headers:
            if h.lower().startswith(b"content-length:"):
                try:
                    cl = int(h.split(b":", 1)[1])
                except Exception:
                    cl = 0
        body = await reader.readexactly(cl) if cl else b""

        if path == "/api/hello":
            send_response(writer, 200, JSON_CT, json.dumps({
                "app": "weaknet", "name": G.user_name, "id": G.self_id,
                "platform": "windows" if IS_WIN else os.name,
            }, ensure_ascii=False).encode())
        elif method == "POST" and path == "/api/share":
            try:
                d = json.loads(body.decode("utf-8"))
            except Exception:
                d = {}
            res = receive_share(d if isinstance(d, dict) else {})
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


def status_payload():
    ev = list(G.events)[-60:]
    ev.reverse()
    return {
        "profile": G.profile,
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
        },
        "inbox_count": len(G.inbox),
    }


REASON = {200: "OK", 400: "Bad Request", 404: "Not Found"}


def send_response(writer, code, ctype, body):
    head = (f"HTTP/1.1 {code} {REASON.get(code, 'OK')}\r\n"
            f"Content-Type: {ctype}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n").encode()
    writer.write(head + body)


JSON_CT = "application/json; charset=utf-8"


async def handle_ctrl(reader, writer):
    try:
        line = await reader.readline()
        parts = line.decode("latin1").split()
        if len(parts) < 2:
            return
        method, path = parts[0].upper(), parts[1].split("?")[0]

        headers = []
        while True:
            h = await reader.readline()
            if not h or h in (b"\r\n", b"\n"):
                break
            headers.append(h)
        cl = 0
        for h in headers:
            if h.lower().startswith(b"content-length:"):
                cl = int(h.split(b":", 1)[1])
        body = await reader.readexactly(cl) if cl else b""

        if path in ("/", "/index.html"):
            # 网页控制台已下线（只保留 GUI）。留一句说明，免得浏览器打开是空白页、看不出原因
            send_response(writer, 200, "text/plain; charset=utf-8",
                          ("WeakNet 控制台已改为纯桌面界面，网页端不再提供。\n"
                           "请运行 WeakNet.exe 使用图形界面；\n"
                           "本机 HTTP 控制接口仍可用，见 /api/status。\n").encode("utf-8"))
        elif path == "/api/status":
            send_response(writer, 200, JSON_CT,
                          json.dumps(status_payload(), ensure_ascii=False).encode())
        elif path == "/api/presets":
            data = [{"key": k, "name": v[0], "cat": v[1]} for k, v in PRESETS.items()]
            send_response(writer, 200, JSON_CT,
                          json.dumps(data, ensure_ascii=False).encode())
        elif path == "/api/upresets":
            # 自定义栏：用户命名保存的参数。老版本客户端没有这个接口也照常工作
            send_response(writer, 200, JSON_CT, json.dumps(
                {"items": _items_payload(get_user_presets())},
                ensure_ascii=False).encode())
        elif method == "POST" and path == "/api/upreset":
            # 保存（带 id 则覆盖更新那一条，否则新建）
            try:
                data = json.loads(body)
                prof = data.get("profile")
            except Exception:
                data, prof = None, None
            if not isinstance(data, dict) or not isinstance(prof, dict):
                send_response(writer, 400, JSON_CT, b'{"error":"bad request"}')
                return
            item, _items = upsert_user_preset(data.get("name"), prof, data.get("id"))
            log_event("config", "保存自定义场景：" + item["name"])
            send_response(writer, 200, JSON_CT, json.dumps(
                {"ok": True, "id": item["id"], "name": item["name"],
                 "items": _items_payload(get_user_presets())},
                ensure_ascii=False).encode())
        elif method == "POST" and path == "/api/upreset/del":
            try:
                pid = json.loads(body).get("id")
            except Exception:
                pid = None
            if not pid:
                send_response(writer, 400, JSON_CT, b'{"error":"bad request"}')
                return
            ok, left = delete_user_preset(pid)
            if ok:
                log_event("config", "删除自定义场景")
            send_response(writer, 200, JSON_CT, json.dumps(
                {"ok": ok, "items": _items_payload(left)}, ensure_ascii=False).encode())
        elif method == "POST" and path == "/api/preset":
            key = None
            try:
                key = json.loads(body).get("key")
                prof = build_preset_profile(key)
            except Exception:
                prof = None
            if prof is None:
                send_response(writer, 400, JSON_CT, b'{"error":"bad request"}')
            else:
                # 与 GUI 同一条规矩：切场景只是选中，没开启限速时不施加
                await select_scene(prof, key)
                log_event("config", "切换场景：" + prof["name"])
                send_response(writer, 200, JSON_CT, b'{"ok":true}')
        elif method == "POST" and path == "/api/profile":
            try:
                data = json.loads(body)
            except Exception:
                data = None
            if not isinstance(data, dict):
                send_response(writer, 400, JSON_CT, b'{"error":"bad request"}')
                return
            prof = default_profile("自定义")
            for k, v in data.items():
                if k in prof and k != "name" and isinstance(v, (int, float)):
                    prof[k] = max(0, v)
            await set_profile(prof, "custom")
            log_event("config", "应用自定义参数")
            send_response(writer, 200, JSON_CT, b'{"ok":true}')
        elif path == "/api/run":
            # 限速总开关。GET 视为查询，POST {enable:true/false} 为切换。
            if method == "POST" and body:
                try:
                    enable = bool(json.loads(body).get("enable"))
                except Exception:
                    enable = None
                if enable is None:
                    send_response(writer, 400, JSON_CT, b'{"error":"bad request"}')
                else:
                    await set_limit(enable)
                    log_event("config", ("开启限速：" + G.profile["name"]) if enable
                              else "停止限速（恢复直连，场景保留：" + G.preset_key + "）")
                    send_response(writer, 200, JSON_CT,
                                  ('{"ok":true,"limit_on":%s}' % ("true" if G.limit_on else "false")).encode())
            else:
                send_response(writer, 200, JSON_CT,
                              ('{"ok":true,"limit_on":%s}' % ("true" if G.limit_on else "false")).encode())
        elif path == "/api/stop":
            # 不限方法：页面若以 GET 发出也要能停（历史上前端漏带 body 导致 GET 404、按钮点了没反应）
            await set_limit(False)
            log_event("config", "停止限速，恢复直连")
            send_response(writer, 200, JSON_CT, b'{"ok":true}')
        elif method == "POST" and path == "/api/sysproxy":
            try:
                enable = bool(json.loads(body).get("enable"))
            except Exception:
                enable = None
            if enable is None:
                send_response(writer, 400, JSON_CT, b'{"error":"bad request"}')
            else:
                ok = set_sysproxy(enable)
                log_event("config", "一键" + ("接管本机流量（系统代理已开启）" if enable else "解除接管（系统代理已关闭）"))
                send_response(writer, 200, JSON_CT,
                              ('{"ok":%s}' % ("true" if ok else "false")).encode())
        elif path == "/api/scope":
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
                    send_response(writer, 400, JSON_CT, b'{"error":"bad request"}')
                    return
            send_response(writer, 200, JSON_CT, json.dumps({
                "mode": G.scope_mode, "list": G.scope_list,
                "supported": IS_WIN,
            }, ensure_ascii=False).encode())
        elif path == "/api/targets":
            # 可接管的目标。Windows 是进程名，Android 是包名 —— 用同一结构便于两端共用控制台。
            names = set(list_procs()) | set(G.scope_seen.keys()) | set(G.scope_list)
            names = {n for n in names if n and n.lower() != "system"
                     and n != "<未识别>"}
            send_response(writer, 200, JSON_CT, json.dumps({
                "type": "process",
                "unit": "程序（exe）",
                "items": [{"id": n, "name": n, "desc": ""} for n in sorted(names)],
            }, ensure_ascii=False).encode())
        elif method == "POST" and path == "/api/listen":
            try:
                host = json.loads(body).get("host")
            except Exception:
                host = None
            if host not in ("127.0.0.1", "0.0.0.0"):
                send_response(writer, 400, JSON_CT, b'{"error":"bad host"}')
            else:
                await restart_proxy(host)
                log_event("config", f"代理监听切换为 {host}:{G.proxy_port}")
                send_response(writer, 200, JSON_CT, b'{"ok":true}')
        # ---- 环境共享：用户名 / 局域网 peer / 推送 / 收件箱 ----
        elif path == "/api/me":
            if method == "POST":
                try:
                    nm = json.loads(body).get("name")
                except Exception:
                    nm = None
                if nm is not None:
                    set_user_name(nm)
                    log_event("share", "本机名称改为：" + G.user_name)
            send_response(writer, 200, JSON_CT, json.dumps({
                "name": G.user_name, "id": G.self_id, "share": G.share_port,
                "discover": G.discover_listen, "lan_ip": G.lan_ip,
                "platform": "windows" if IS_WIN else os.name,
            }, ensure_ascii=False).encode())
        elif path == "/api/peers":
            now = time.time()
            items = []
            for p in sorted(G.peers.values(), key=lambda x: x["name"].lower()):
                items.append({"id": p["id"], "name": p["name"], "ip": p["ip"],
                              "platform": p["platform"], "share": p["share"],
                              "ago": int(now - p["at"])})
            send_response(writer, 200, JSON_CT, json.dumps({
                "items": items,
                "self": {"name": G.user_name, "id": G.self_id,
                         "share": G.share_port, "lan_ip": G.lan_ip},
                "discover": G.discover_listen,
            }, ensure_ascii=False).encode())
        elif method == "POST" and path == "/api/push":
            try:
                d = json.loads(body)
            except Exception:
                d = {}
            ids = d.get("ids")
            if isinstance(ids, str):
                ids = [ids]
            if not isinstance(ids, list):
                ids = []
            peer = d.get("peer")
            if isinstance(peer, str) and ":" in peer:      # 兼容 "ip:port" 写法
                ip, _, pstr = peer.partition(":")
                peer = {"ip": ip, "share": int(pstr or SHARE_PORT), "name": ip}
            if not isinstance(peer, dict) or not peer.get("ip"):
                send_response(writer, 400, JSON_CT, b'{"error":"bad peer"}')
            else:
                res = await push_to_peer(peer, ids)
                send_response(writer, 200, JSON_CT,
                              json.dumps(res, ensure_ascii=False).encode())
        elif path == "/api/inbox":
            items = []
            for it in sorted(G.inbox.values(), key=lambda x: x["at"]):
                items.append({"id": it["id"], "from": it["from"], "name": it["name"],
                              "profile": it["profile"], "ago": int(time.time() - it["at"])})
            send_response(writer, 200, JSON_CT,
                          json.dumps({"items": items}, ensure_ascii=False).encode())
        elif method == "POST" and path == "/api/inbox/resolve":
            try:
                d = json.loads(body)
            except Exception:
                d = {}
            res = resolve_inbox(str(d.get("id") or ""), str(d.get("action") or ""),
                                d.get("name"))
            send_response(writer, 200, JSON_CT,
                          json.dumps(res, ensure_ascii=False).encode())
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
    init_identity()                   # 「我是谁」——局域网共享要用
    proxy = await asyncio.start_server(handle_proxy, host, proxy_port, limit=1 << 20)
    ctrl = await asyncio.start_server(handle_ctrl, "127.0.0.1", ctrl_port)
    G.proxy_host = host
    G.proxy_port = proxy_port
    G.proxy_server = proxy
    try:
        await start_sharing()         # 共享端口 + 局域网发现（失败不影响弱网本身）
    except Exception as e:
        log_event("share", "环境共享启动异常：" + str(e)[:80])
    return proxy, ctrl


async def main():
    ap = argparse.ArgumentParser(description="WeakNet —— App 弱网测试工具")
    ap.add_argument("--host", default="127.0.0.1",
                    help="代理监听地址，手机测试用 0.0.0.0（默认 127.0.0.1）")
    ap.add_argument("--proxy-port", type=int, default=8888, help="代理端口（默认 8888）")
    ap.add_argument("--ctrl-port", type=int, default=8899, help="控制台端口（默认 8899）")
    args = ap.parse_args()

    G.proxy_host = args.host
    G.proxy_port = args.proxy_port
    G.lan_ip = detect_lan_ip()

    # 退出（含 Ctrl+C、被终止）时自动解除系统代理接管
    atexit.register(_auto_release_sysproxy)

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
