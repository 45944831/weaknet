# -*- coding: utf-8 -*-
"""修复验证：P0 中文载荷共享 / P1 组合删除复位 / P2 抖动塌零 / P3 方法校验。

对「已修复」的版本断言正确行为（原 _t_20261028_p0.py 断言的是缺陷存在，已不适用）。
两端都验：Android 真机（18899/18821）+ Windows 源码引擎（8899/8888）。
"""
import json
import os
import socket
import statistics
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t_dev_lib as D

WIN = "http://127.0.0.1:8899"
SRV = ("127.0.0.1", 9125)
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wapi(path, data=None, timeout=40):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(WIN + path, data=body,
                                 method=("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    try:
        with _op.open(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"_err": repr(e)}


def raw(path, method="GET", base=WIN, timeout=10):
    """返回 (状态码, 响应头, 正文)"""
    req = urllib.request.Request(base + path, method=method)
    try:
        with _op.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:
        return 0, {}, repr(e).encode()


def ulist(x):
    return x if isinstance(x, list) else (x.get("items", []) if isinstance(x, dict) else [])


def tunnel(proxy_port, timeout=60):
    s = socket.create_connection(("127.0.0.1", proxy_port), timeout=timeout)
    s.sendall(("CONNECT %s:%d HTTP/1.1\r\n\r\n" % SRV).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = s.recv(1024)
        if not d:
            raise RuntimeError("proxy closed")
        buf += d
    if b" 200 " not in buf.split(b"\r\n")[0]:
        raise RuntimeError("CONNECT failed: %r" % buf[:60])
    s.settimeout(timeout)
    return s


def ping_seq(proxy_port, n=40):
    s = tunnel(proxy_port)
    rs = []
    buf = b""
    try:
        for i in range(n):
            t0 = time.time()
            s.sendall(("PING %d\n" % i).encode())
            while b"\n" not in buf:
                d = s.recv(4096)
                if not d:
                    raise RuntimeError("closed")
                buf += d
            _, _, buf = buf.partition(b"\n")
            rs.append((time.time() - t0) * 1000.0)
    finally:
        s.close()
    return rs


# ================================================================ P0
def verify_p0():
    D.section("P0 中文载荷推送（修复后应全部成功）")
    # 清理 PC 侧探针
    for it in ulist(wapi("/api/upresets")):
        if str(it.get("name", "")).startswith("P0probe"):
            wapi("/api/upreset/del", {"id": it.get("id")})

    cases = [
        ("纯 ASCII 120 字", "A" * 120),
        ("中英混合 90 字", "探针A" * 30),
        ("纯中文 40 字", "中" * 40),
        ("纯中文 10 字", "中" * 10),
    ]
    for label, name in cases:
        rc = wapi("/api/upreset", {"name": "P0probe_" + name,
                                   "profile": {"down_kbps": 700, "up_kbps": 300}})
        t0 = time.time()
        res = wapi("/api/push", {"peer": "127.0.0.1:18890",
                                 "ids": [rc.get("id")], "combo_ids": []})
        el = time.time() - t0
        wapi("/api/upreset/del", {"id": rc.get("id")})
        D.rec("P0 %s 推送成功" % label,
              res.get("ok") is True and el < 3.0,
              "%.2fs → %s" % (el, json.dumps(res, ensure_ascii=False)[:110]))

    # 手机侧确实入库且参数一致（接收端会把名字截到 24 字，按前缀匹配）
    ups = ulist(D.api("/api/upresets"))
    hit = [x for x in ups if str(x.get("name", "")).startswith("P0probe_中")]
    D.rec("P0 中文环境在手机侧入库且参数一致",
          bool(hit) and any(x.get("profile", {}).get("down_kbps") == 700 for x in hit),
          "命中 %d 条，示例=%s" % (len(hit), hit[0].get("name") if hit else "未找到"))

    # 中文名组合（段里是中文场景名）
    rc = wapi("/api/combo", {"name": "P0探针中文组合", "loop": False,
                             "steps": [{"key": "airplane", "minutes": 0, "seconds": 1}]})
    res = wapi("/api/push", {"peer": "127.0.0.1:18890", "ids": [],
                             "combo_ids": [rc.get("id")]})
    D.rec("P0 中文名组合推送成功", res.get("ok") is True,
          json.dumps(res, ensure_ascii=False)[:110])
    wapi("/api/combo/delete", {"id": rc.get("id")})

    # 清理手机侧
    for it in ulist(D.api("/api/upresets")):
        if str(it.get("name", "")).startswith("P0probe"):
            D.api("/api/upreset/del", {"id": it.get("id")})
    for it in ulist(D.api("/api/combos")):
        if str(it.get("name", "")).startswith("P0探针"):
            D.api("/api/combo/delete", {"id": it.get("id")})

    # 手机端不再报错日志（失败才打日志；成功走正常 share 日志）
    out = D.sh("logcat -d -t 600 2>/dev/null | grep -c '共享服务处理失败'")
    D.rec("P0 手机端无「共享服务处理失败」日志", out.strip() in ("0", ""), "计数=%s" % out.strip())


# ================================================================ P1
def verify_p1():
    D.section("P1 删除正在播放的组合应回直连（两端）")

    for label, api, base in (("Android", D.api, "device"), ("Windows", wapi, "pc")):
        api("/api/run", {"enable": False})
        rc = api("/api/combo", {"name": "P1验证组合" + label, "loop": False,
                                "steps": [{"key": "4g", "minutes": 0, "seconds": 5},
                                          {"key": "direct", "minutes": 0, "seconds": 5}]})
        cid = rc.get("id")
        api("/api/preset", {"key": "c:%s" % cid})
        api("/api/run", {"enable": True})
        time.sleep(1.2)
        before = api("/api/status")
        api("/api/combo/delete", {"id": cid})
        time.sleep(0.8)
        s = api("/api/status")
        D.rec("P1 %s：删除播放中组合后回直连" % label,
              s.get("limit_on") is False and s.get("preset_key") == "direct"
              and s.get("profile", {}).get("name", "").startswith("直连"),
              "删前 limit=%s，删后 limit=%s preset=%s profile=%s" % (
                  before.get("limit_on"), s.get("limit_on"), s.get("preset_key"),
                  s.get("profile", {}).get("name")))
        api("/api/run", {"enable": False})

    # 删除「没在播放」的组合不应打断当前场景
    D.section("P1 附带：删除未播放的组合不打断当前场景")
    api = D.api
    other = api("/api/combo", {"name": "P1旁观组合", "loop": False,
                               "steps": [{"key": "5g", "minutes": 0, "seconds": 5}]})
    api("/api/preset", {"key": "congestion"})
    api("/api/run", {"enable": True})
    time.sleep(0.8)
    api("/api/combo/delete", {"id": other.get("id")})
    time.sleep(0.5)
    s = api("/api/status")
    D.rec("P1 删别的组合不影响当前场景",
          s.get("preset_key") == "congestion" and s.get("limit_on") is True,
          "preset=%s limit=%s" % (s.get("preset_key"), s.get("limit_on")))
    api("/api/run", {"enable": False})
    api("/api/preset", {"key": "direct"})


# ================================================================ P2
def verify_p2():
    D.section("P2 抖动大于基础延迟时不再出现零延迟包（两端）")
    for label, api, proxy_port in (("Android", D.api, 18821), ("Windows", wapi, 8888)):
        api("/api/run", {"enable": False})
        api("/api/profile", {"latency_up_ms": 60, "latency_down_ms": 100,
                             "jitter_ms": 150, "down_kbps": 0, "up_kbps": 0})
        api("/api/run", {"enable": True})
        time.sleep(0.3)
        rs = ping_seq(proxy_port, 40)
        zero = [r for r in rs if r < 20]
        mean = statistics.mean(rs)
        D.rec("P2 %s：40 次 PING 无零延迟包" % label, len(zero) == 0,
              "RTT<20ms %d 次，min=%.1f p50=%.1f mean=%.1f（理论均值 160）" % (
                  len(zero), min(rs), statistics.median(rs), mean))
        D.rec("P2 %s：均值贴近理论 160ms" % label, 130 <= mean <= 190,
              "mean=%.1f" % mean)
        api("/api/run", {"enable": False})
        api("/api/preset", {"key": "direct"})


# ================================================================ P3
def verify_p3():
    D.section("P3 控制接口方法校验（两端）")
    st, hd, _ = raw("/api/status", "DELETE")
    D.rec("P3 Windows：DELETE /api/status → 405",
          st == 405 and "GET" in str(hd.get("Allow", "")),
          "status=%s Allow=%s" % (st, hd.get("Allow")))
    st2, _, _ = raw("/api/status", "GET")
    D.rec("P3 Windows：GET 仍正常", st2 == 200, "status=%s" % st2)
    st3, _, _ = raw("/api/status", "DELETE", base="http://127.0.0.1:18899")
    D.rec("P3 Android：DELETE /api/status → 405", st3 == 405, "status=%s" % st3)
    st4, _, _ = raw("/api/status", "GET", base="http://127.0.0.1:18899")
    D.rec("P3 Android：GET 仍正常", st4 == 200, "status=%s" % st4)
    # 根路径不受影响
    st5, _, body = raw("/", "DELETE")
    D.rec("P3 非 /api 路径不受方法校验影响", st5 == 200, "status=%s" % st5)


if __name__ == "__main__":
    D.setup_forward()
    try:
        verify_p0()
        verify_p1()
        verify_p2()
        verify_p3()
    finally:
        try:
            D.api("/api/run", {"enable": False})
            D.api("/api/preset", {"key": "direct"})
            wapi("/api/run", {"enable": False})
            wapi("/api/preset", {"key": "direct"})
        except Exception:
            pass
    D.summary("修复验证")
