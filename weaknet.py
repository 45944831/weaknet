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
  - 内置 Wi-Fi / 4G / 3G / 2G / 拨号 / 高铁 / 电梯等 16 种预设场景
  - Web 控制台实时配置与监控（默认 http://127.0.0.1:8899）

用法：
  python weaknet.py [--host 127.0.0.1] [--proxy-port 8888] [--ctrl-port 8899]
  测试设备（手机/电脑/App）将 HTTP(S) 代理指向 本机IP:代理端口 即可生效。
"""

import argparse
import asyncio
import atexit
import json
import os
import random
import signal
import socket
import sys
import time
import traceback
import urllib.parse
import webbrowser
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


DASH_FILE = resource_path("dashboard.html")


# --------------------------------------------------------------------------
# 网络参数模型
# --------------------------------------------------------------------------

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


# key: (名称, 分类, 参数覆盖)
PRESETS = {
    "direct": ("直连（不限制）", "常规网络", {}),
    "wifi": ("Wi-Fi 良好", "常规网络",
             {"latency_up_ms": 10, "latency_down_ms": 30, "jitter_ms": 5,
              "down_kbps": 50000, "up_kbps": 20000}),
    "4g": ("4G LTE", "常规网络",
           {"latency_up_ms": 40, "latency_down_ms": 60, "jitter_ms": 15,
            "down_kbps": 20000, "up_kbps": 10000}),
    "3g": ("3G", "常规网络",
           {"latency_up_ms": 150, "latency_down_ms": 200, "jitter_ms": 50,
            "down_kbps": 2000, "up_kbps": 1000}),
    "edge": ("2G EDGE", "常规网络",
             {"latency_up_ms": 400, "latency_down_ms": 600, "jitter_ms": 200,
              "down_kbps": 250, "up_kbps": 100, "loss_pct": 2}),
    "gprs": ("2G GPRS（极慢）", "常规网络",
             {"latency_up_ms": 600, "latency_down_ms": 900, "jitter_ms": 300,
              "down_kbps": 60, "up_kbps": 40, "loss_pct": 5}),
    "dialup": ("56K 拨号", "常规网络",
               {"latency_up_ms": 150, "latency_down_ms": 250, "jitter_ms": 40,
                "down_kbps": 56, "up_kbps": 33}),
    "wifi_weak": ("弱 Wi-Fi（隔墙/远端）", "弱网环境",
                  {"latency_up_ms": 60, "latency_down_ms": 120, "jitter_ms": 60,
                   "down_kbps": 5000, "up_kbps": 2000, "loss_pct": 3}),
    "congestion": ("网络拥塞", "弱网环境",
                   {"latency_up_ms": 200, "latency_down_ms": 400, "jitter_ms": 100,
                    "down_kbps": 512, "up_kbps": 256, "loss_pct": 5}),
    "satellite": ("卫星链路（高延迟）", "弱网环境",
                  {"latency_up_ms": 700, "latency_down_ms": 700, "jitter_ms": 80,
                   "down_kbps": 2000, "up_kbps": 512}),
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
    name, _cat, extra = PRESETS[key]
    prof = default_profile(name)
    prof.update(extra)
    return prof


# --------------------------------------------------------------------------
# 全局状态
# --------------------------------------------------------------------------

class G:
    profile = default_profile()
    preset_key = "direct"
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
        if direction == "up":
            G.bytes_up += n
        else:
            G.bytes_down += n
        prof = G.profile                       # 实时参数（切场景后立即生效）
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
        # 周期断网黑窗期：直接掐断新连接
        if not G.gate_open:
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

        prof = G.profile

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
            log_event("connect", "建立隧道 " + label)
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
    if prof["flap_sec"] > 0:
        G.flap_task = asyncio.create_task(flap_loop())


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

def status_payload():
    ev = list(G.events)[-60:]
    ev.reverse()
    return {
        "profile": G.profile,
        "preset_key": G.preset_key,
        "gate_open": G.gate_open,
        "listen": {"host": G.proxy_host, "port": G.proxy_port},
        "lan_ip": G.lan_ip,
        "sysproxy": sysproxy_enabled(),
        "stats": {
            "total": G.conns_total,
            "active": G.conns_active,
            "up": G.bytes_up,
            "down": G.bytes_down,
            "uptime": int(time.time() - G.started_at),
        },
        "events": ev,
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
            send_response(writer, 200, "text/html; charset=utf-8", DASH_FILE.read_bytes())
        elif path == "/api/status":
            send_response(writer, 200, JSON_CT,
                          json.dumps(status_payload(), ensure_ascii=False).encode())
        elif path == "/api/presets":
            data = [{"key": k, "name": v[0], "cat": v[1]} for k, v in PRESETS.items()]
            send_response(writer, 200, JSON_CT,
                          json.dumps(data, ensure_ascii=False).encode())
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
                await set_profile(prof, key)
                log_event("config", "切换场景：" + PRESETS[key][0])
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
        elif path == "/api/stop":
            # 不限方法：页面若以 GET 发出也要能停（历史上前端漏带 body 导致 GET 404、按钮点了没反应）
            await set_profile(default_profile(), "direct")
            log_event("config", "停止模拟，恢复直连")
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
    proxy = await asyncio.start_server(handle_proxy, host, proxy_port, limit=1 << 20)
    ctrl = await asyncio.start_server(handle_ctrl, "127.0.0.1", ctrl_port)
    G.proxy_host = host
    G.proxy_port = proxy_port
    G.proxy_server = proxy
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
            log(f"[WeakNet] 已经有一个实例在运行：{url}")
            log("[WeakNet] 本实例不再重复启动，直接复用它即可。")
            if FROZEN:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
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
    log(f"  控制台   :  {url}")
    log(f"  代理地址 :  {G.proxy_host}:{G.proxy_port}")
    log(f"  局域网 IP:  {G.lan_ip}  (手机测试请在控制台开启局域网访问)")
    log("  关闭本窗口即停止工具（会自动解除系统代理接管）")
    log("=" * 58)

    if FROZEN:      # EXE 模式：直接帮用户打开控制台
        try:
            webbrowser.open(url)
        except Exception:
            pass

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
