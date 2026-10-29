# -*- coding: utf-8 -*-
"""弱网测试报告的数据采集脚本（一次性，可复跑）。

被测对象：weaknet 引擎（进程内、独立线程），客户端是「HTTP 应用行为模型」：
经代理 CONNECT 建隧道后跑大文件下载 / keep-alive 小请求 / 上传 / 并发下载。
重点采集「弱网结束（/api/stop）之后」的恢复数据。

不打包、不联网、不碰仓库里的设置与场景文件：存储与日志全部指向临时目录。
"""
import asyncio
import json
import platform
import socket
import statistics
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import weaknet as W                     # noqa: E402
from support import connect_via_proxy   # noqa: E402
import conftest                         # noqa: E402

OUT_JSON = REPO / "_弱网测试数据_2026-10-28.json"


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def now():
    # 必须用 perf_counter：这台 Windows 上 time.monotonic() 只有 15.6ms 分辨率，
    # 小请求会被量化成 0.0ms，毫秒级恢复时延也测不准。
    return time.perf_counter()


def stat(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {}
    s = sorted(vals)

    def q(p):
        if len(s) == 1:
            return s[0]
        i = (len(s) - 1) * p
        lo = int(i)
        hi = min(lo + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (i - lo)

    return {"n": len(s), "min": round(s[0], 1), "p50": round(q(.5), 1),
            "p95": round(q(.95), 1), "max": round(s[-1], 1),
            "mean": round(sum(s) / len(s), 1)}


# ---------------------------------------------------------------- 本地源站

class _OriginHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/small":
            body = b'{"ok":true,"n":1}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == "/big":
            total = max(1, int(q.get("kb", "512"))) * 1024
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(total))
            self.end_headers()
            left = total
            chunk = b"x" * 65536
            try:
                while left > 0:
                    n = min(len(chunk), left)
                    self.wfile.write(chunk[:n])
                    self.wfile.flush()
                    left -= n
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        left = n
        try:
            while left > 0:
                d = self.rfile.read(min(65536, left))
                if not d:
                    break
                left -= len(d)
        except (BrokenPipeError, ConnectionResetError):
            return
        body = ('{"n":%d}' % (n - left)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Origin(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    @property
    def port(self):
        return self.server_address[1]


class RawConn:
    """经代理 CONNECT 建立的隧道；按 Content-Length 读响应，支持 keep-alive。"""

    def __init__(self, origin_port, timeout=60.0, proxy=None):
        t = now()
        self.sock, self.buf = connect_via_proxy(
            proxy or ENGINE.proxy, "127.0.0.1", origin_port, timeout=timeout)
        self.connect_ms = (now() - t) * 1000.0
        self.sock.settimeout(timeout)
        self.origin_port = origin_port

    def send(self, data):
        self.sock.sendall(data)

    def _fill(self):
        d = self.sock.recv(65536)
        if not d:
            raise EOFError("连接被关闭")
        self.buf += d

    def line(self):
        while b"\r\n" not in self.buf:
            self._fill()
        line, _, self.buf = self.buf.partition(b"\r\n")
        return line

    def head(self):
        first = self.line().decode("latin1")
        status = int(first.split()[1])
        hdr = {}
        while True:
            line = self.line()
            if not line:
                break
            k, _, v = line.partition(b":")
            hdr[k.strip().lower().decode("latin1")] = v.strip().decode("latin1")
        return status, hdr

    def response(self):
        status, hdr = self.head()
        n = int(hdr.get("content-length") or 0)
        while len(self.buf) < n:
            self._fill()
        body, self.buf = self.buf[:n], self.buf[n:]
        return status, hdr, body

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def _rate(tl, t_a, t_b):
    """时间窗内的速率（kbps，1 kbps = 1024 bit/s，与工具口径一致）。"""
    if not tl or t_b - t_a < 0.05:
        return None

    def at(t):
        lo, hi = 0, len(tl)
        while lo < hi:
            mid = (lo + hi) // 2
            if tl[mid][0] <= t:
                lo = mid + 1
            else:
                hi = mid
        return tl[lo - 1][1] if lo else 0

    return round((at(t_b) - at(t_a)) * 8.0 / 1024.0 / (t_b - t_a), 1)


def download(path, timeout=90.0, out=None):
    """完整下载并记录到达时间线；out 可让调用方在下载进行中观察进度。"""
    out = out if out is not None else {}
    t0 = now()
    out["t0"] = t0
    got = 0

    def tl_append(t):
        out.setdefault("timeline", []).append((round(t - t0, 6), got))

    try:
        c = RawConn(ORIGIN.port, timeout)
        out["connect_ms"] = round(c.connect_ms, 1)
        t_req = now()
        req = ("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n"
               % (path, ORIGIN.port))
        c.send(req.encode("latin1"))
        status, hdr = c.head()
        out["ttfb_ms"] = round((now() - t_req) * 1000.0, 1)
        out["status"] = status
        n = int(hdr.get("content-length") or 0)
        rest = c.buf[:n]
        c.buf = b""
        got = len(rest)
        tl_append(now())
        while got < n:
            d = c.sock.recv(min(65536, n - got))
            if not d:
                raise EOFError("只收到 %d/%d 字节" % (got, n))
            got += len(d)
            tl_append(now())
        out["total_ms"] = round((now() - t0) * 1000.0, 1)
        out["bytes"] = got
        out["ok"] = True
        tl = out.get("timeline") or []
        out["avg_kbps"] = round(got * 8.0 / 1024.0 / (out["total_ms"] / 1000.0), 1)
        out["steady_kbps"] = _rate(tl, out["total_ms"] / 1000.0 * 0.30,
                                   out["total_ms"] / 1000.0 * 0.95)
        c.close()
    except Exception as e:
        out["total_ms"] = round((now() - t0) * 1000.0, 1)
        out["bytes"] = got
        out["ok"] = False
        out["error"] = type(e).__name__ + ": " + str(e)[:90]
        try:
            c.close()
        except Exception:
            pass
    return out


def keepalive(n, timeout=60.0, reconnect=False):
    """同一条隧道上连续发 n 个小请求。

    reconnect=True 时，连接被弱网掐断后自动重连并把剩下的请求发完 ——
    模拟「应用自己重试」；断开次数单独记在 disconnects 里。
    """
    res = {"n": n, "lat_ms": [], "errors": [], "disconnects": 0}
    c = None
    done = 0
    req = ("GET /small HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: keep-alive\r\n\r\n"
           % ORIGIN.port).encode("latin1")
    try:
        while done < n:
            try:
                if c is None:
                    c = RawConn(ORIGIN.port, timeout)
                    res.setdefault("connect_ms", round(c.connect_ms, 1))
                t = now()
                c.send(req)
                status, _hdr, _body = c.response()
                res["lat_ms"].append(round((now() - t) * 1000.0, 2))
                done += 1
                if status != 200:
                    res["errors"].append("第 %d 个请求状态码 %d" % (done, status))
                    break
            except Exception as e:
                res["errors"].append("第 %d 个请求中断：%s: %s"
                                     % (done + 1, type(e).__name__, str(e)[:60]))
                try:
                    c.close()
                except Exception:
                    pass
                c = None
                if not reconnect:
                    break
                res["disconnects"] += 1
                if res["disconnects"] > n + 2:      # 连重连都失败：不再无限重试
                    res["errors"].append("重连失败，放弃")
                    break
    finally:
        if c is not None:
            c.close()
    res["stats"] = stat(res["lat_ms"])
    return res


def upload(kb, timeout=90.0):
    res = {"kb": kb}
    payload = b"u" * (kb * 1024)
    t0 = now()
    c = None
    try:
        c = RawConn(ORIGIN.port, timeout)
        res["connect_ms"] = round(c.connect_ms, 1)
        t = now()
        head = ("POST /upload HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                "Content-Type: application/octet-stream\r\nContent-Length: %d\r\n"
                "Connection: close\r\n\r\n" % (ORIGIN.port, len(payload)))
        c.send(head.encode("latin1"))
        for off in range(0, len(payload), 65536):
            c.send(payload[off:off + 65536])
        status, _hdr, body = c.response()
        dt = now() - t
        res["total_ms"] = round(dt * 1000.0, 2)
        res["kbps"] = round(len(payload) * 8.0 / 1024.0 / dt, 1) if dt > 0 else None
        res["ok"] = status == 200 and ('"n":%d' % len(payload)) in body.decode("utf-8", "replace")
        res["body"] = body.decode("utf-8", "replace")[:60]
    except Exception as e:
        res["total_ms"] = round((now() - t0) * 1000.0, 1)
        res["ok"] = False
        res["error"] = type(e).__name__ + ": " + str(e)[:90]
    finally:
        if c is not None:
            c.close()
    return res


def run_parallel(kb, n, timeout=90.0):
    outs = [{} for _ in range(n)]
    ths = [threading.Thread(target=download, args=("/big?kb=%d" % kb, timeout, outs[i]),
                            daemon=True) for i in range(n)]
    for t in ths:
        t.start()
    return outs, ths


# ---------------------------------------------------------------- 引擎启动

TMP = Path(tempfile.mkdtemp(prefix="weakrep_"))
W._PRESET_FILES = [TMP / "weaknet_presets.json"]
W._settings_file = lambda: TMP / "weaknet_settings.json"
W.LOG_FILE = TMP / "weaknet.log"
W._LOG_SINK = None
W.G.user_presets = None
W.G.combos = None
W.ensure_identity()


async def _skip_sharing():
    return None


W.start_sharing = _skip_sharing
conftest.reset_engine_state()
TH = conftest._EngineThread()
TH.start()
if not TH.ready.wait(20) or TH.error is not None:
    raise SystemExit("引擎启动失败：%r" % (TH.error,))
ENGINE = conftest.Engine(TH.proxy_port, TH.ctrl_port)
ORIGIN = _Origin(("127.0.0.1", 0), _OriginHandler)
threading.Thread(target=ORIGIN.serve_forever, daemon=True, name="origin").start()


def preset(key):
    return dict(W.PRESETS[key][2])


def apply(**params):
    r = ENGINE.apply(**params)
    assert r.status == 200, r.body
    time.sleep(0.15)


def stop_timed():
    t0 = now()
    r = ENGINE.api.post("/api/stop", timeout=15)
    t1 = now()
    return {"api_ms": round((t1 - t0) * 1000.0, 2), "t_req": t0, "t_resp": t1,
            "status": r.status}


def stop():
    return stop_timed()


R = {"meta": {
    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "spec_hash": W.SPEC_HASH,
    "version": getattr(W, "VERSION", ""),
    "python": platform.python_version(),
    "platform": platform.platform(),
    "proxy": "127.0.0.1:%d" % TH.proxy_port,
    "ctrl": "127.0.0.1:%d" % TH.ctrl_port,
    "origin": "127.0.0.1:%d" % ORIGIN.server_address[1],
    "note": "客户端与源站同机环回；kbps 按 1 kbps = 1024 bit/s 折算",
}, "presets": {k: W.PRESETS[k][2] for k in
               ("4g", "4g_weak", "wifi_weak", "congestion", "satellite",
                "high_loss", "high_latency", "flapping", "blackhole")}}


def baseline():
    log("基线（直连）")
    dl = [download("/big?kb=4096") for _ in range(2)]
    ka = keepalive(20)
    up = upload(2048)
    outs, ths = run_parallel(1024, 4)
    for t in ths:
        t.join(60)
    R["baseline"] = {"download": dl, "keepalive": ka, "upload": up, "parallel": outs}
    log("  直连 4MB 稳态 %.1f kbps / 小请求 p50 %.1f ms / 上传 %.1f kbps"
        % (dl[-1].get("steady_kbps") or 0, ka["stats"].get("p50") or 0,
           up.get("kbps") or 0))


def accuracy():
    log("带宽/时延精度（每个场景约 3s 载荷 × 2）")
    rows = [("4G LTE", "4g"), ("4G 弱网", "4g_weak"), ("弱 Wi-Fi", "wifi_weak"),
            ("网络拥塞", "congestion"), ("卫星链路", "satellite")]
    for name, key in rows:
        p = preset(key)
        apply(**p)
        kbps = p.get("down_kbps") or 0
        kb = max(32, int(kbps * 0.375))
        runs = []
        for _ in range(2):
            runs.append(download("/big?kb=%d" % kb, timeout=120))
            time.sleep(0.25)
        small = keepalive(5, timeout=60, reconnect=True)
        p50 = small["stats"].get("p50")
        log("  %s：设定 %d kbps，实测稳态 %s / %s kbps；小请求 p50 %s ms"
            % (name, kbps, runs[0].get("steady_kbps"), runs[1].get("steady_kbps"), p50))
        R.setdefault("accuracy", []).append(
            {"name": name, "key": key, "params": p, "kb": kb,
             "runs": runs, "small": small})
    # 高丢包 / 电梯：症状型场景，只跑一轮
    for name, key, kb in (("高丢包 30%", "high_loss", 1024),
                          ("电梯 / 地下车库", "elevator", 128)):
        p = preset(key)
        apply(**p)
        run = download("/big?kb=%d" % kb, timeout=120)
        small = keepalive(4, timeout=60, reconnect=True)
        log("  %s：ok=%s 用时 %.1f ms 平均 %.1f kbps；症状=%s"
            % (name, run.get("ok"), run.get("total_ms") or 0,
               run.get("avg_kbps") or 0, (run.get("error") or "-")))
        R.setdefault("accuracy", []).append(
            {"name": name, "key": key, "params": p, "kb": kb,
             "runs": [run], "small": small})
    # 高时延单独一轮小请求
    p = preset("high_latency")
    apply(**p)
    small = keepalive(4, timeout=60, reconnect=True)
    R.setdefault("accuracy", []).append(
        {"name": "高时延 RTT800ms", "key": "high_latency", "params": p, "kb": 0,
         "runs": [], "small": small})
    log("  高时延：小请求 p50 %s ms" % (small["stats"].get("p50"),))


def mid_stop(name, key, kb, wait_s, note=""):
    """弱网中途停止：下载进行到 wait_s 时点「停止」，观察恢复与连接存活。"""
    apply(**preset(key))
    out = {}
    th = threading.Thread(target=download, args=("/big?kb=%d" % kb, 90, out), daemon=True)
    th.start()
    while "t0" not in out:
        time.sleep(0.002)
    time.sleep(wait_s)
    st = stop_timed()
    # 停止后立刻用一条新连接取数据：验证「新连接是否马上恢复正常」
    fresh = {}
    fresh_th = threading.Thread(target=download, args=("/big?kb=512", 60, fresh),
                                daemon=True)
    fresh_th.start()
    th.join(90)
    fresh_th.join(60)

    t_req_rel = st["t_req"] - out["t0"]
    t_resp_rel = st["t_resp"] - out["t0"]
    tl = out.get("timeline") or []

    def at(t):
        b = 0
        for tt, bb in tl:
            if tt <= t:
                b = bb
            else:
                break
        return b

    before = at(t_req_rel)
    by_resp = at(t_resp_rel)
    nxt = next((t for t, _b in tl if t > t_resp_rel), None)
    pre = _rate(tl, max(0.0, t_req_rel - 1.0), t_req_rel)
    post_end = min(t_resp_rel + 0.5, (out.get("total_ms") or 0) / 1000.0)
    post = _rate(tl, t_resp_rel, post_end)
    flush_ms = (out["total_ms"] - t_resp_rel * 1000.0) if out.get("ok") else None
    flush_kbps = (round((out["bytes"] - by_resp) * 8.0 / 1024.0 / (flush_ms / 1000.0), 1)
                  if out.get("ok") and flush_ms and flush_ms > 0 else None)

    row = {
        "name": name, "key": key, "kb": kb, "wait_s": wait_s, "note": note,
        "stop_api_ms": st["api_ms"],
        "before_stop_kbps": pre,
        "after_stop_window_kbps": post,
        "bytes_before_stop": before,
        "pct_before_stop": round(before * 100.0 / (kb * 1024), 1),
        "bytes_after_stop": (out["bytes"] - by_resp) if out.get("ok") else None,
        "flush_kbps": flush_kbps,
        "resume_after_stop_ms": round((nxt - t_resp_rel) * 1000.0, 2) if nxt is not None else None,
        "done_after_stop_ms": (round(flush_ms, 2) if flush_ms is not None else None),
        "ok": out.get("ok"), "total_ms": out.get("total_ms"),
        "bytes": out.get("bytes"), "error": out.get("error"),
        "connect_ms": out.get("connect_ms"), "ttfb_ms": out.get("ttfb_ms"),
        "fresh": {k: fresh.get(k) for k in ("ok", "connect_ms", "ttfb_ms", "total_ms",
                                            "avg_kbps", "error")},
        "timeline": tl,
    }
    log("  %s：停前 %.1f kbps，%.1f%% 已收到；停后下一字节 %s ms，"
        "%s ms 内放完全部积压（%s kbps）；新连接 ttfb %s / 完成 %s ms"
        % (name, pre or 0, row["pct_before_stop"] or 0,
           row["resume_after_stop_ms"], row["done_after_stop_ms"],
           flush_kbps, row["fresh"].get("ttfb_ms"),
           row["fresh"].get("total_ms")))
    R.setdefault("mid_stop", []).append(row)
    return row


def blackhole():
    log("黑洞（timeout 100%）：客户端超时 vs 停止后放行")
    apply(timeout_pct=100)
    # A. 客户端 2.5s 超时且无人点停止：应用侧只能靠自己的超时脱身
    client = {"timeout_s": 2.5, "ok": False, "elapsed_ms": None, "error": None}
    c = None
    t0 = now()
    try:
        c = RawConn(ORIGIN.port, 2.5)
        c.send(("GET /small HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n"
                % ORIGIN.port).encode("latin1"))
        c.response()
        client.update(ok=True, elapsed_ms=round((now() - t0) * 1000.0, 1))
    except Exception as e:
        client["elapsed_ms"] = round((now() - t0) * 1000.0, 1)
        client["error"] = type(e).__name__ + ": " + str(e)[:60]
    finally:
        if c is not None:
            c.close()
    # B. 挂起 → 用户点停止 → 观察放行与完成耗时
    out = {}
    th = threading.Thread(target=download, args=("/big?kb=32", 30, out), daemon=True)
    th.start()
    while "t0" not in out:
        time.sleep(0.002)
    time.sleep(1.5)
    st = stop_timed()
    t_stop = st["t_resp"]
    th.join(30)
    resume = round((now() - t_stop) * 1000.0, 1) if out.get("ok") else None
    stop()   # 收尾，确保回到直连
    R["blackhole"] = {
        "client_timeout": client,
        "stop_api_ms": st["api_ms"],
        "resume_after_stop_ms": resume,
        "download_ok": out.get("ok"),
        "download_total_ms": out.get("total_ms"),
        "download_connect_ms": out.get("connect_ms"),
    }
    log("  停止后 %.1f ms 放行；2.5s 客户端超时：%s（%.1f ms）"
        % (resume or -1, client["error"] or "成功", client["elapsed_ms"] or 0))


def flap():
    log("频繁断网：断网期间连接被掐断，停止后重连")
    p = preset("flapping")
    p["flap_sec"] = 3
    p["flap_blackout_sec"] = 1
    # 只保留「周期断网」：预设里 5% 随机断连会在黑窗之前就把连接掐掉，
    # 混在一起说不清是谁干的。随机断连另由 4G 弱网的小请求用例覆盖。
    p["disconnect_pct"] = 0
    apply(**p)
    out = {}
    th = threading.Thread(target=download, args=("/big?kb=8192", 60, out), daemon=True)
    th.start()
    while "t0" not in out:
        time.sleep(0.002)
    time.sleep(3.25)                      # 第一次黑窗 3.0~4.0s
    during = {}
    try:
        c = RawConn(ORIGIN.port, 5)
        during["connect_ms"] = round(c.connect_ms, 1)
        c.close()
        during["result"] = "连接成功（黑窗未生效或已过）"
    except Exception as e:
        during["result"] = type(e).__name__ + ": " + str(e)[:70]
    th.join(30)
    first_error = out.get("error")
    first_bytes = out.get("bytes")
    first_ms = out.get("total_ms")
    st = stop_timed()
    t_stop = st["t_resp"]
    retry = download("/big?kb=8192", 90)
    recover_ms = round((now() - t_stop) * 1000.0, 1)
    R["flap"] = {
        "params": p,
        "during_blackout": during,
        "first_ok": out.get("ok"), "first_error": first_error,
        "first_bytes": first_bytes, "first_total_ms": first_ms,
        "stop_api_ms": st["api_ms"],
        "retry_ok": retry.get("ok"), "retry_total_ms": retry.get("total_ms"),
        "retry_kbps": retry.get("avg_kbps"),
        "recover_from_stop_ms": recover_ms,
    }
    log("  断网中新建连接：%s；首传 %s（%s 字节后 %s）；停止后重传=%s，%.1f ms"
        % (during["result"], out.get("ok"), first_bytes, first_error,
           retry.get("ok"), retry.get("total_ms") or -1))


def concurrency():
    log("并发 4 路下载，中途停止")
    apply(**preset("4g"))
    t0 = now()
    outs, ths = run_parallel(3072, 4)
    time.sleep(1.5)
    st = stop_timed()
    fresh = {}
    fresh_th = threading.Thread(target=download, args=("/big?kb=1024", 60, fresh),
                                daemon=True)
    fresh_th.start()
    for t in ths:
        t.join(60)
    fresh_th.join(60)
    rows = []
    for i, o in enumerate(outs):
        tl = o.get("timeline") or []
        rel_req = st["t_req"] - o["t0"]
        rel_resp = st["t_resp"] - o["t0"]

        def at(t):
            b = 0
            for tt, bb in tl:
                if tt <= t:
                    b = bb
                else:
                    break
            return b

        nxt = next((t for t, _b in tl if t > rel_resp), None)
        rows.append({
            "i": i, "ok": o.get("ok"), "bytes": o.get("bytes"),
            "total_ms": o.get("total_ms"),
            "bytes_before_stop": at(rel_req),
            "pct_before_stop": round(at(rel_req) * 100.0 / (3072 * 1024), 1),
            "resume_ms": (round((nxt - rel_resp) * 1000.0, 2)
                          if nxt is not None else None),
            "done_after_stop_ms": (round(o["total_ms"] - rel_resp * 1000.0, 2)
                                   if o.get("ok") else None),
            "error": o.get("error"),
        })
    R["concurrency"] = {
        "kb": 3072, "streams": 4, "stop_at_s": 1.5,
        "stop_api_ms": st["api_ms"],
        "all_ok": all(o.get("ok") for o in outs),
        "streams_detail": rows,
        "fresh": {k: fresh.get(k) for k in ("ok", "connect_ms", "ttfb_ms", "total_ms",
                                            "avg_kbps", "error")},
    }
    log("  停止命令 %.1f ms；%d/4 完成；停后完成 %s ms；新连接 %s ms"
        % (st["api_ms"], sum(1 for o in outs if o.get("ok")),
           [r["done_after_stop_ms"] for r in rows], fresh.get("total_ms")))


def post_stop_small():
    log("弱网结束后的小请求/轮询恢复")
    apply(**preset("4g_weak"))
    before = keepalive(5, timeout=60, reconnect=True)
    st = stop_timed()
    time.sleep(0.1)
    after = keepalive(10, timeout=30)
    R["post_stop_small"] = {
        "preset": "4g_weak",
        "before": before,
        "after": after,
        "stop_api_ms": st["api_ms"],
    }
    log("  停止前 p50 %s ms（掉线重连 %d 次）；停止后 p50 %s ms；错误 %s"
        % (before["stats"].get("p50"), before["disconnects"],
           after["stats"].get("p50"), after["errors"] or "无"))


def connect_cost():
    """新建一条代理连接（CONNECT）的固定开销：进程名反查只在 scope != all 时才需要。"""
    log("新建连接开销（进程名反查）")
    with_lookup = []
    for _ in range(3):
        c = RawConn(ORIGIN.port, 20)
        with_lookup.append(round(c.connect_ms, 2))
        c.close()
        time.sleep(0.05)
    orig = W.proc_name_of_peer
    W.proc_name_of_peer = lambda *a, **k: "python.exe"
    without = []
    try:
        for _ in range(3):
            c = RawConn(ORIGIN.port, 20)
            without.append(round(c.connect_ms, 2))
            c.close()
            time.sleep(0.05)
    finally:
        W.proc_name_of_peer = orig
    R["connect_cost"] = {"with_lookup_ms": with_lookup, "without_lookup_ms": without}
    log("  默认 %s ms；跳过反查 %s ms" % (with_lookup, without))


def fairness():
    log("并发公平性（4 路同起同跑，不停止）")
    apply(**preset("4g"))
    outs, ths = run_parallel(3072, 4)
    snaps = []
    for i in range(6):
        time.sleep(0.5)
        snaps.append({
            "t": round((i + 1) * 0.5, 1),
            "bytes": [(o.get("timeline") or [[0, 0]])[-1][1] for o in outs],
            "connect_ms": [round(o.get("connect_ms", -1), 1) for o in outs],
        })
    for t in ths:
        t.join(120)
    rows = []
    for i, o in enumerate(outs):
        rows.append({"i": i, "ok": o.get("ok"), "connect_ms": o.get("connect_ms"),
                     "total_ms": o.get("total_ms"), "avg_kbps": o.get("avg_kbps")})
    R["fairness"] = {"streams": rows, "snapshots": snaps,
                     "all_ok": all(o.get("ok") for o in outs)}
    log("  进度 %s；建连 %s ms；完成耗时 %s ms"
        % ([s["bytes"] for s in snaps],
           [r["connect_ms"] for r in rows],
           [r["total_ms"] for r in rows]))


def uploads():
    log("上传（上行带宽）")
    rows = []
    for name, key, kb in (("4G LTE", "4g", 2048), ("网络拥塞", "congestion", 64)):
        apply(**preset(key))
        u = upload(kb)
        rows.append({"name": name, "key": key, "result": u})
        log("  %s 上行设定 %d kbps：%dKB 用时 %.2f s，实测 %.1f kbps，ok=%s"
            % (name, preset(key).get("up_kbps"), kb, (u.get("total_ms") or 0) / 1000.0,
               u.get("kbps") or 0, u.get("ok")))
    stop()
    R["uploads"] = rows


def main():
    try:
        baseline()
        connect_cost()
        accuracy()
        uploads()
        log("弱网结束（停止限速）后的恢复")
        mid_stop("4G 中断下载", "4g", 8192, 1.5)
        mid_stop("4G 弱网中断下载", "4g_weak", 2048, 1.5)
        mid_stop("拥塞中断下载", "congestion", 256, 1.5)
        mid_stop("卫星链路中断下载", "satellite", 1024, 2.5)
        mid_stop("高时延中断下载", "high_latency", 2048, 2.0)
        blackhole()
        flap()
        concurrency()
        fairness()
        post_stop_small()
        R["meta"]["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        OUT_JSON.write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
        log("原始数据 -> %s" % OUT_JSON)
    finally:
        stop()
        try:
            ORIGIN.shutdown()
            ORIGIN.server_close()
        except Exception:
            pass
        TH.stop()


if __name__ == "__main__":
    main()
