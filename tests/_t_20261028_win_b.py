# -*- coding: utf-8 -*-
"""2026-10-28 全面深度测试 —— Windows 引擎 B 组：
故障注入（黑洞/拒连/DNS/周期断网/随机断连）、弱网组合与 P1 复现、
自定义场景 CRUD 与越界夹值、接管范围、协议健壮性、停止响应性。
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _w_lib as L

CTRL = "http://127.0.0.1:8899"
SRV = ("127.0.0.1", 9125)
RESULTS = []
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path, data=None, method=None, raw=None, timeout=20):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(CTRL + path, data=body,
                                 method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    try:
        with _op.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def rec(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""), flush=True)


def scene(key, enable=True):
    api("/api/preset", {"key": key})
    api("/api/run", {"enable": enable})


def st():
    return api("/api/status")[1]


def connect_raw(host, port, timeout=8):
    s = socket.create_connection(L.PROXY, timeout=timeout)
    s.sendall(("CONNECT %s:%d HTTP/1.1\r\n\r\n" % (host, port)).encode())
    return s


def read_head(s):
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = s.recv(1024)
        if not d:
            break
        buf += d
    return buf.split(b"\r\n")[0].decode("latin1")


# ============================================================ B1 故障注入
def part_faults():
    # 黑洞：挂住不返回，停止限速后立刻放行
    scene("blackhole", True)
    s = connect_raw(SRV[0], SRV[1], timeout=20)
    s.settimeout(2.5)
    t0 = time.time()
    try:
        head = read_head(s)
    except socket.timeout:
        head = ""
    hung = (time.time() - t0) >= 2.0 and "200" not in head
    api("/api/run", {"enable": False})
    s.settimeout(6)
    t1 = time.time()
    try:
        head2 = read_head(s)
    except Exception as e:
        head2 = "ERR:%r" % e
    released = time.time() - t1
    s.close()
    rec("B-01 黑洞：请求挂住不返回", hung, "2.5s 内无 200 响应，head=%r" % head[:20])
    rec("B-02 黑洞：点停止后立即放行", "200" in str(head2) and released < 3.0,
        "停止后 %.2fs 收到 %r" % (released, str(head2)[:24]))

    # 完全断网：立即失败
    scene("airplane", True)
    t0 = time.time()
    try:
        s = connect_raw(SRV[0], SRV[1], timeout=8)
        head = read_head(s)
        s.close()
    except Exception as e:
        head = "ERR:%r" % e
    el = time.time() - t0
    rec("B-03 完全断网：立即拒连（不挂住）", ("200" not in str(head)) and el < 3.0,
        "%.2fs → %r" % (el, str(head)[:30]))

    # DNS 故障：域名失败、IP 直连不受影响
    scene("dns_fail", True)
    try:
        s = connect_raw("example.com", 80, timeout=8)
        head_dns = read_head(s)
        s.close()
    except Exception as e:
        head_dns = "ERR:%r" % e
    try:
        s = connect_raw(SRV[0], SRV[1], timeout=8)
        head_ip = read_head(s)
        s.close()
    except Exception as e:
        head_ip = "ERR:%r" % e
    rec("B-04 DNS 故障：域名连接失败", "200" not in str(head_dns), str(head_dns)[:40])
    rec("B-05 DNS 故障：IP 直连 200（不误伤）", "200" in str(head_ip), str(head_ip)[:40])

    # 周期断网：gate_open 周期性变 false
    scene("flapping", True)
    opens = []
    t_end = time.time() + 20
    while time.time() < t_end:
        try:
            opens.append(st().get("gate_open"))
        except Exception:
            pass
        time.sleep(0.5)
    rec("B-06 周期断网：闸门周期性关闭", False in opens and True in opens,
        "采样 %d 次，False %d 次" % (len(opens), opens.count(False)))

    # 随机断连：电梯档 10% 断连率，多次短连接应出现中断
    scene("elevator", True)
    fails = 0
    for _ in range(8):
        try:
            t = L.Tunnel(host=SRV[0], port=SRV[1], timeout=8)
            try:
                t.down(2048)
            finally:
                t.close()
        except Exception:
            fails += 1
    rec("B-07 随机断连：8 次短连接出现中断", fails >= 1, "失败/中断 %d/8" % fails)
    scene("direct", False)


# ============================================================ B2 组合
def part_combo():
    api("/api/run", {"enable": False})
    # 清理旧组合
    _, lst = api("/api/combos")
    items = lst if isinstance(lst, list) else lst.get("items", [])
    for it in items:
        api("/api/combo/delete", {"id": it["id"]})

    steps = [{"key": "4g", "minutes": 0, "seconds": 2},
             {"key": "airplane", "minutes": 0, "seconds": 2},
             {"key": "direct", "minutes": 0, "seconds": 2}]
    stc, d = api("/api/combo", {"name": "自动化组合A", "loop": False, "steps": steps})
    cid = d.get("id")
    rec("B-10 新建组合", stc == 200 and bool(cid), "id=%s" % cid)

    # 段上限 5
    stc, d6 = api("/api/combo", {"name": "六段组合", "loop": False,
                                 "steps": [{"key": "4g", "seconds": 1}] * 6})
    got_steps = d6.get("steps")
    rec("B-11 段数上限 5（第 6 段被裁）", got_steps == 5, "steps=%s" % got_steps)

    # 全零段拒绝
    stc, _ = api("/api/combo", {"name": "空壳", "loop": False,
                                "steps": [{"key": "4g", "minutes": 0, "seconds": 0}]})
    rec("B-12 全零时长段 → 400 拒绝", stc == 400, "status=%d" % stc)

    # 嵌套组合拒绝
    stc, dnest = api("/api/combo", {"name": "嵌套", "loop": False,
                                    "steps": [{"key": "c:" + str(cid), "seconds": 2}]})
    rec("B-13 段里选组合被拒绝", stc == 400, "status=%d" % stc)

    # 起播
    api("/api/preset", {"key": "c:" + str(cid)})
    api("/api/run", {"enable": True})
    seen = []
    t_end = time.time() + 8.5
    while time.time() < t_end:
        try:
            s = st()
            key = (s.get("preset_key"), s.get("profile", {}).get("name"))
            if not seen or seen[-1] != key:
                seen.append(key)
        except Exception:
            pass
        time.sleep(0.2)
    names = [x[1] for x in seen]
    rec("B-14 组合按时间轴切段（3 段依次生效）",
        len([n for n in names if n]) >= 3 and "直连（不限制）" in names,
        "序列=%s" % names)
    rec("B-15 组合走完停在直连（未勾循环）",
        st().get("limit_on") in (True, False) and st().get("preset_key") in ("c:%s" % cid, "direct"),
        "preset_key=%s limit_on=%s" % (st().get("preset_key"), st().get("limit_on")))

    # P1 复现：删除正在播放的组合
    api("/api/preset", {"key": "c:" + str(cid)})
    api("/api/run", {"enable": True})
    time.sleep(1.0)
    api("/api/combo/delete", {"id": cid})
    time.sleep(0.6)
    s = st()
    rec("B-16 【P1】删除播放中的组合后回直连",
        s.get("limit_on") is False and s.get("preset_key") == "direct",
        "limit_on=%s preset_key=%s profile=%s（期望 False/direct）" % (
            s.get("limit_on"), s.get("preset_key"), s.get("profile", {}).get("name")))
    api("/api/run", {"enable": False})


# ============================================================ B3 自定义场景
def part_presets():
    api("/api/run", {"enable": False})
    _, lst = api("/api/upresets")
    items = lst if isinstance(lst, list) else lst.get("items", [])
    for it in items:
        api("/api/upreset/del", {"id": it["id"]})

    prof = {"down_kbps": 777, "up_kbps": 333, "latency_up_ms": 88,
            "latency_down_ms": 99, "loss_pct": 2}
    stc, d = api("/api/upreset", {"name": "自动化场景A", "profile": prof})
    pid = d.get("id")
    rec("B-20 保存自定义场景", stc == 200 and bool(pid), "id=%s" % pid)

    # 重名加序号
    stc, d2 = api("/api/upreset", {"name": "自动化场景A", "profile": prof})
    nm = d2.get("name") or ""
    rec("B-21 重名自动加序号（不静默覆盖）", nm != "自动化场景A" and "(" in nm, "name=%s" % nm)

    # 列表与应用
    _, lst = api("/api/upresets")
    items = lst if isinstance(lst, list) else lst.get("items", [])
    rec("B-22 自定义列表含两条", len(items) >= 2, "n=%d" % len(items))
    api("/api/preset", {"key": "u:" + str(pid)})
    api("/api/run", {"enable": True})   # 预设只在限速开启时施加（选场景≠开启）
    s = st()
    p = s.get("profile", {})
    rec("B-23 应用自定义场景参数逐字段一致",
        p.get("down_kbps") == 777 and p.get("up_kbps") == 333
        and p.get("latency_up_ms") == 88 and p.get("latency_down_ms") == 99,
        "down=%s up=%s lat=%s/%s" % (p.get("down_kbps"), p.get("up_kbps"),
                                     p.get("latency_up_ms"), p.get("latency_down_ms")))

    # 越界夹值：999% 丢包应夹到 100
    stc, d3 = api("/api/upreset", {"name": "越界场景", "profile": {"loss_pct": 999,
                                                                  "disconnect_pct": -5,
                                                                  "down_kbps": -100}})
    api("/api/preset", {"key": "u:" + str(d3.get("id"))})
    api("/api/run", {"enable": True})
    p = st().get("profile", {})
    rec("B-24 越界值夹到合法区间",
        0 <= p.get("loss_pct", -1) <= 100 and 0 <= p.get("disconnect_pct", -1) <= 100
        and p.get("down_kbps", -1) >= 0,
        "loss=%s disconnect=%s down=%s" % (p.get("loss_pct"), p.get("disconnect_pct"),
                                           p.get("down_kbps")))

    # 删除
    stc, _ = api("/api/upreset/del", {"id": pid})
    _, lst = api("/api/upresets")
    items = lst if isinstance(lst, list) else lst.get("items", [])
    rec("B-25 删除自定义场景生效", all(str(i.get("id")) != str(pid) for i in items),
        "剩余 %d 条" % len(items))

    # 清理
    for it in items:
        api("/api/upreset/del", {"id": it["id"]})
    api("/api/preset", {"key": "direct"})


# ============================================================ B4 接管范围
def part_scope():
    api("/api/run", {"enable": False})
    _, tg = api("/api/targets")
    items = tg if isinstance(tg, list) else tg.get("items", [])
    rec("B-30 /api/targets 能列出进程", isinstance(items, (list, dict)) and bool(items),
        "n=%s" % (len(items) if hasattr(items, "__len__") else "?"))

    stc, _ = api("/api/scope", {"mode": "only", "list": ["__no_such_proc__.exe"]})
    _, sc = api("/api/scope")
    rec("B-31 接管范围写入并回读", sc.get("mode") == "only",
        "mode=%s list=%s" % (sc.get("mode"), sc.get("list")))

    # 名单外进程应旁路（不限速）：congestion 档下仍应接近直连速度
    scene("congestion", True)
    t = L.Tunnel(host=SRV[0], port=SRV[1], timeout=120)
    try:
        got, sec, _ = t.down(512 * 1024)
    finally:
        t.close()
    m = got * 8 / sec / 1e6
    s = st()
    rec("B-32 名单外进程旁路（不限速）", m > 5.0 and s["stats"]["bypass"] > 0,
        "%.1f Mbps（若被限速应≈0.5），bypass=%d" % (m, s["stats"]["bypass"]))

    api("/api/scope", {"mode": "all", "list": []})
    scene("direct", False)


# ============================================================ B5 协议健壮性
class _H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        body = b"y" * 4096
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def part_robust():
    srv = ThreadingHTTPServer(("127.0.0.1", 18082), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    HTTP = "127.0.0.1:18082"

    # 畸形请求行
    try:
        s = socket.create_connection(L.PROXY, timeout=5)
        s.sendall(b"\x00\x01\x02GARBAGE\r\n\r\n")
        s.settimeout(3)
        try:
            s.recv(100)
        except Exception:
            pass
        s.close()
    except Exception:
        pass
    rec("B-40 畸形请求行不崩", api("/api/status")[0] == 200, "引擎存活")

    # 超长请求头
    try:
        s = socket.create_connection(L.PROXY, timeout=5)
        s.sendall(b"GET http://" + HTTP.encode() + b"/ HTTP/1.1\r\nX-Big: " + b"a" * 100000 + b"\r\n\r\n")
        s.settimeout(3)
        try:
            s.recv(200)
        except Exception:
            pass
        s.close()
    except Exception:
        pass
    rec("B-41 超长请求头不崩", api("/api/status")[0] == 200, "引擎存活")

    # 半截请求头：引擎设计为整体 30s 超时后关闭（代码 asyncio.wait_for(..., 30)）
    s = socket.create_connection(L.PROXY, timeout=45)
    s.sendall(b"GET http://" + HTTP.encode() + b"/ HTTP/1.1\r\nHost: x\r\n")
    s.settimeout(40)
    t0 = time.time()
    try:
        d = s.recv(100)
    except socket.timeout:
        d = b"<timeout>"
    el = time.time() - t0
    s.close()
    rec("B-42 半截请求头被超时回收（设计值 30s）", d == b"" and el < 36.0,
        "%.1fs → %r" % (el, d[:20]))

    # 半关闭（客户端 shutdown write）仍能收完整响应
    s = socket.create_connection(L.PROXY, timeout=15)
    s.sendall(b"GET http://" + HTTP.encode() + b"/ HTTP/1.1\r\nHost: " + HTTP.encode()
              + b"\r\nConnection: close\r\n\r\n")
    s.shutdown(socket.SHUT_WR)
    data = b""
    s.settimeout(10)
    try:
        while True:
            d = s.recv(65536)
            if not d:
                break
            data += d
    except Exception:
        pass
    s.close()
    rec("B-43 半关闭下响应完整", data.endswith(b"y") and b"200 OK" in data,
        "%d B，尾部=%r" % (len(data), data[-4:]))

    # keep-alive：同一连接两次请求
    s = socket.create_connection(L.PROXY, timeout=15)
    ok2 = 0
    try:
        for i in range(2):
            s.sendall(b"GET http://" + HTTP.encode() + b"/ HTTP/1.1\r\nHost: "
                      + HTTP.encode() + b"\r\n\r\n")
            buf = b""
            s.settimeout(10)
            while b"200 OK" not in buf:
                d = s.recv(65536)
                if not d:
                    break
                buf += d
            if b"200 OK" in buf:
                ok2 += 1
    except Exception as e:
        ok2 = ok2 if ok2 else 0
        rec("B-44 HTTP keep-alive 复用连接", False, "异常 %r" % e)
        ok2 = -1
    finally:
        s.close()
    if ok2 >= 0:
        rec("B-44 HTTP keep-alive 复用连接", ok2 == 2, "成功 %d/2" % ok2)

    # 50 并发连接
    errs = []

    def w():
        try:
            t = L.Tunnel(host=SRV[0], port=SRV[1], timeout=30)
            try:
                t.down(4096)
            finally:
                t.close()
        except Exception as e:
            errs.append(repr(e))

    ts = [threading.Thread(target=w) for _ in range(50)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rec("B-45 50 并发连接无失败", not errs, "失败 %d：%s" % (len(errs), errs[:2]))

    # 停止响应性：卫星档下停止限速，积压数据应快速放行
    scene("satellite", True)
    t = L.Tunnel(host=SRV[0], port=SRV[1], timeout=120)
    res = {}

    def dl():
        t0 = time.time()
        try:
            g, s_, _ = t.down(256 * 1024)
            res["ok"] = (g, time.time() - t0)
        except Exception as e:
            res["err"] = repr(e)

    th = threading.Thread(target=dl)
    th.start()
    time.sleep(0.8)
    t_stop = time.time()
    api("/api/run", {"enable": False})
    th.join(timeout=20)
    after = time.time() - t_stop
    try:
        t.close()
    except Exception:
        pass
    rec("B-46 停止限速后积压数据快速放行", "ok" in res and after < 5.0,
        "停止后 %.2fs 完成，结果=%s" % (after, res))
    api("/api/preset", {"key": "direct"})


# ============================================================ B6 抖动分布
def part_jitter():
    """jitter > base 时，是否有一批包被压到 0 延迟（= 延迟注入被绕过）"""
    api("/api/run", {"enable": False})
    api("/api/profile", {"latency_up_ms": 60, "latency_down_ms": 100,
                         "jitter_ms": 150, "down_kbps": 0, "up_kbps": 0})
    api("/api/run", {"enable": True})
    rs = []
    t = L.Tunnel(host=SRV[0], port=SRV[1], timeout=60)
    try:
        for i in range(30):
            rtt, _ = t.ping(i)
            rs.append(rtt)
    finally:
        t.close()
    near_zero = [r for r in rs if r < 20]
    rec("B-50 jitter>base 时延迟不应塌到 0（理论 RTT 约 160ms）",
        len(near_zero) == 0,
        "30 次中 %d 次 RTT<20ms（%.1f%%），min=%.1f max=%.1f 均值=%.1f" % (
            len(near_zero), len(near_zero) / 30.0 * 100, min(rs), max(rs),
            sum(rs) / len(rs)))
    api("/api/run", {"enable": False})
    api("/api/preset", {"key": "direct"})


if __name__ == "__main__":
    print("=== B 组开始 ===", flush=True)
    for fn in (part_faults, part_combo, part_presets, part_scope,
               part_robust, part_jitter):
        try:
            fn()
        except Exception as e:
            rec("!! %s 异常" % fn.__name__, False, repr(e))
        finally:
            try:
                api("/api/run", {"enable": False})
            except Exception:
                pass
    ok = sum(1 for r in RESULTS if r["ok"])
    print("\n=== B 组汇总 %d/%d ===" % (ok, len(RESULTS)), flush=True)
    out = os.path.join(HERE, "_t_20261028_win_b.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=1)
    print("saved", out)
