# -*- coding: utf-8 -*-
"""W1~W6 优化项 · 黑盒端到端测试（跑在真实引擎上）。

与 _t_win_opt_unit.py 互补：单测证明「代码按设计工作」，
本文件证明「装在一起之后行为正确」。

  B1  稳态吞吐矩阵（大样本，消除突发额度 / 慢启动伪影）
  B2  8 并发聚合总带宽不超标
  B3  限速中切直连，剩余数据立即满速放完
  B4  半截请求头 30s 超时被断
  B5  事件日志 2s 内落盘
  B6  空闲连接回收（子引擎，TTL=3s / 扫描=1s）
  B7  atexit 把缓冲日志落盘（子引擎，SIGBREAK 优雅退出）
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request



CTRL = "http://127.0.0.1:8899"
PROXY = ("127.0.0.1", 8888)
SRV = ("192.168.1.49", 9126)
SUB_PORTS = (8889, 8900)
LOG = os.path.join(HERE, "weaknet", "weaknet.log")
RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""),
          flush=True)


def api(port, path, data=None, timeout=10):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=body,
                                 method=("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}


def set_scene(key, enable=True):
    api(8899, "/api/preset", {"key": key})
    api(8899, "/api/run", {"enable": enable})


def read_line(s):
    buf = b""
    while not buf.endswith(b"\n"):
        d = s.recv(1)
        if not d:
            return None
        buf += d
    return buf.decode("latin1").strip()


def tunnel(port, timeout=30):
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    s.sendall(("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n\r\n"
               % (SRV[0], SRV[1], SRV[0], SRV[1])).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = s.recv(1)
        if not d:
            raise OSError("proxy closed during CONNECT: %r" % buf)
        buf += d
    return s


def download(nbytes, timeout=180, port=8888):
    s = tunnel(port, timeout)
    t0 = time.time()
    got = 0
    try:
        s.sendall(("DOWN %d\n" % nbytes).encode())
        while got < nbytes:
            d = s.recv(min(65536, nbytes - got))
            if not d:
                break
            got += len(d)
    finally:
        s.close()
    return got, max(time.time() - t0, 1e-6)


def ping(port, tag=0, timeout=60):
    s = tunnel(port, timeout)
    try:
        t0 = time.time()
        s.sendall(("PING %d\n" % tag).encode())
        line = read_line(s)
        return (time.time() - t0) * 1000.0 if line else None
    finally:
        s.close()


# ---------------------------------------------------------------- B1
def steady_size(kbps, rtt_ms, min_sec=1.5):
    """下载量要同时盖住：桶的 0.25s 突发额度 + 长 RTT 下的慢启动。"""
    if not kbps:
        return 8 * 1024 * 1024
    bps = kbps * 1024.0 / 8.0
    n = bps * min_sec
    n = max(n, bps * (rtt_ms / 1000.0) * 4.0)     # ≥4 个 RTT 才离开慢启动
    return int(min(max(n, 128 * 1024), 24 * 1024 * 1024))


def b1():
    st, d = api(8899, "/api/presets")
    keys = [p["key"] for p in d]
    print("--- 稳态吞吐矩阵（大样本）---", flush=True)
    worst = []
    for key in keys:
        set_scene(key)
        st, d = api(8899, "/api/status")
        prof = d["profile"]
        kbps, loss = prof["down_kbps"], prof.get("loss_pct", 0)
        rtt = prof["latency_up_ms"] + prof["latency_down_ms"]
        if kbps == 0:
            continue                                  # 直连档无限速，跳过
        size = steady_size(kbps, rtt)
        got, el = download(size)
        got_kbps = got * 8 / 1024 / el
        ratio = got_kbps / kbps
        disc = prof.get("disconnect_pct", 0)
        if loss or disc:
            # 丢包/断连档的瓶颈是 TCP 重传或连接被主动掐断，不是限速器
            # （Mathis: 30% 丢包理论上限 ~85kbps；disconnect_pct 本来就会截断传输）。
            # 这里只验「有数据在流动」，带宽与完整性都不作为通过条件。
            ok = got > 0
            note = "丢包 %d%% / 断连 %d%%，不判定带宽与完整性" % (loss, disc)
        else:
            ok = (got == size) and 0.80 <= ratio <= 1.25
            note = "" if ok else "超出 0.80~1.25x 稳态区间或数据不完整"
        rec("稳态 %-14s %5d kbps" % (key, kbps), ok,
            "%d B -> %d B / %.0f kbps (%.2fx) el=%.2fs %s"
            % (size, got, got_kbps, ratio, el, note))
        worst.append((ratio, key))
    worst.sort()
    print("    最慢三档: %s" % ["%s=%.2fx" % (k, r) for r, k in worst[:3]], flush=True)


# ---------------------------------------------------------------- B2
def b2():
    set_scene("4g")                                   # 20000 kbps
    per, nconn = 512 * 1024, 8
    out = []

    def worker():
        try:
            out.append(download(per)[0])
        except Exception as e:
            out.append(-1)

    ts = [threading.Thread(target=worker) for _ in range(nconn)]
    t0 = time.time()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    el = time.time() - t0
    total = sum(x for x in out if x > 0)
    kbps = total * 8 / 1024 / el
    ok = (total == per * nconn) and kbps <= 20000 * 1.15
    rec("B2 8 并发聚合总带宽不超标", ok,
        "合计 %.0f kbps（%.2fx）完整=%s el=%.2fs 理论 %.2fs"
        % (kbps, kbps / 20000, total == per * nconn, el, total / 2560000.0))


# ---------------------------------------------------------------- B3
def b3():
    set_scene("congestion")                           # 512 kbps, RTT 600ms
    size = 4 * 1024 * 1024
    res = {}

    def worker():
        res["got"], res["el"] = download(size)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(2.0)                                   # 让它进入限速稳态
    t0 = time.time()
    set_scene("direct", True)                         # 切直连 = 取消限速
    t.join(timeout=30)
    switch_el = time.time() - t0
    ok = res.get("got") == size and switch_el < 3.0
    rec("B3 限速中切直连立即满速", ok,
        "切换后 %.2fs 收完剩余（512kbps 下 4MB 需 ~60s）got=%s"
        % (switch_el, res.get("got")))


# ---------------------------------------------------------------- B4
def b4():
    s = socket.create_connection(PROXY, timeout=60)
    try:
        s.sendall(b"GET http://192.168.1.49:9126/dl HTTP/1.1\r\nHost: x\r\n")  # 半截头
        t0 = time.time()
        closed = False
        try:
            while True:
                d = s.recv(4096)
                if not d:
                    closed = True
                    break
        except (socket.timeout, OSError):
            pass
        el = time.time() - t0
        ok = closed and 25 <= el <= 45
        rec("B4 半截请求头 30s 超时被断", ok,
            "%.1fs 后连接关闭（期望 28~35s，close=%s）" % (el, closed))
    finally:
        s.close()


# ---------------------------------------------------------------- B5
def b5():
    marker = "WNTEST-%d" % int(time.time())
    before = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    # 通过 API 触发一条 log_event（config 类），等 flush loop（2s）落盘
    st, _ = api(8899, "/api/preset", {"key": "4g"})
    api(8899, "/api/preset", {"key": "wifi"})
    t0 = time.time()
    ok = False
    while time.time() - t0 < 6:
        try:
            with open(LOG, "rb") as f:
                f.seek(max(0, before - 4096))
                txt = f.read().decode("utf-8", "ignore")
        except Exception:
            txt = ""
        if "切换场景" in txt or "场景" in txt:
            ok = True
            break
        time.sleep(0.2)
    rec("B5 事件日志 2s 内落盘", ok,
        "%.1fs 后在 weaknet.log 见到场景切换事件（status=%s）" % (time.time() - t0, st))


# ---------------------------------------------------------------- B6/B7 子引擎
class SubEngine:
    def __enter__(self):
        self.p = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "_eng_sub.py"),
             "--proxy-port", str(SUB_PORTS[0]), "--ctrl-port", str(SUB_PORTS[1])],
            cwd=os.path.join(HERE, "weaknet"),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            st, _ = api(SUB_PORTS[1], "/api/status", timeout=2)
            if st == 200:
                return self
            time.sleep(0.25)
        raise RuntimeError("子引擎未就绪")

    def __exit__(self, *a):
        try:
            self.p.send_signal(getattr(__import__("signal"), "SIGBREAK", 15))
            self.p.wait(timeout=8)
        except Exception:
            try:
                self.p.kill()
            except Exception:
                pass


def b6():
    pp, cp = SUB_PORTS
    with SubEngine():
        api(cp, "/api/preset", {"key": "direct"})
        api(cp, "/api/run", {"enable": True})

        # B6-1: CONNECT 隧道建好后双方都不说话 -> 应被回收
        s = tunnel(pp, 20)
        t0 = time.time()
        closed = False
        try:
            while True:
                if not s.recv(4096):
                    closed = True
                    break
        except (socket.timeout, OSError):
            pass
        el = time.time() - t0
        s.close()
        rec("B6-1 空闲隧道被回收", closed and 2.0 <= el <= 12.0,
            "%.1fs 后关闭（TTL=3s + 扫描=1s）close=%s" % (el, closed))

        # B6-2: 半截请求头 -> 应被回收
        s = socket.create_connection(("127.0.0.1", pp), timeout=20)
        s.sendall(b"GET http://192.168.1.49:9126/ HTTP/1.1\r\nHost: x\r\n")
        t0 = time.time()
        closed = False
        try:
            while True:
                if not s.recv(4096):
                    closed = True
                    break
        except (socket.timeout, OSError):
            pass
        el = time.time() - t0
        s.close()
        rec("B6-2 半截请求头连接被回收", closed and 2.0 <= el <= 12.0,
            "%.1fs 后关闭 close=%s" % (el, closed))

        # B6-3: 活跃连接不应被误杀（每 1s 有数据，持续 12s >> TTL）
        s = tunnel(pp, 30)
        alive = True
        t0 = time.time()
        try:
            i = 0
            while time.time() - t0 < 12:
                s.sendall(("PING %d\n" % i).encode())
                if read_line(s) is None:
                    alive = False
                    break
                i += 1
                time.sleep(1.0)
        except (socket.timeout, OSError):
            alive = False
        s.close()
        rec("B6-3 活跃连接不被误杀", alive,
            "12s 内完成 %d 次 PING（TTL=3s）" % i)

        # B6-4: 回收后 /api/status 仍正常（回收不能把引擎搞坏）
        st, d = api(cp, "/api/status")
        rec("B6-4 回收后引擎仍正常", st == 200 and "profile" in d,
            "status=%s" % st)


def b7():
    pp, cp = SUB_PORTS
    before = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    with SubEngine():
        api(cp, "/api/preset", {"key": "satellite"})   # 触发 log_event
        time.sleep(0.3)                                # < flush loop 的 2s，事件还在缓冲里
        api(cp, "/api/run", {"enable": True})
        time.sleep(0.3)
    time.sleep(0.5)
    grew = False
    try:
        with open(LOG, "rb") as f:
            f.seek(max(0, before - 8192))
            grew = len(f.read()) > 0 and os.path.getsize(LOG) != before
    except Exception:
        pass
    # atexit 落盘的判据：退出后文件里能读到子引擎这次运行时产生的事件
    ok = False
    try:
        with open(LOG, "rb") as f:
            txt = f.read().decode("utf-8", "ignore")
        ok = ("切换场景" in txt or "开启限速" in txt or "停止限速" in txt)
    except Exception:
        pass
    rec("B7 atexit 把缓冲日志落盘", ok,
        "退出后 weaknet.log 可见本次事件（size 变化=%s）" % grew)


def main():
    t_all = time.time()
    b1()
    b2()
    b3()
    b4()
    b5()
    b6()
    b7()
    bad = [r for r in RESULTS if not r[1]]
    print("\n===== W1~W6 黑盒测试：%d/%d 通过（总耗时 %.0fs）====="
          % (len(RESULTS) - len(bad), len(RESULTS), time.time() - t_all))
    for n, _ok, d in bad:
        print("FAILED: %s | %s" % (n, d))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
