# -*- coding: utf-8 -*-
"""P0 根因对照实验：同字节长度、不同字符数（中文一字 3 字节）的载荷推送。

若失败只与「字符数 < 字节数」相关，则证明 ShareNet.serve() 把 Content-Length（字节）
当成字符数去读，读不满 → 卡满 5s SoTimeout → 静默断连。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t_dev_lib as D

WIN = "http://127.0.0.1:8899"


def wapi(path, data=None, timeout=40):
    import urllib.request
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(WIN + path, data=body,
                                 method=("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"_err": repr(e)}


def push(name):
    r = wapi("/api/upreset", {"name": name, "profile": {"down_kbps": 700, "up_kbps": 300}})
    pid = r.get("id")
    t0 = time.time()
    res = wapi("/api/push", {"peer": "127.0.0.1:18890", "ids": [pid], "combo_ids": []})
    el = time.time() - t0
    wapi("/api/upreset/del", {"id": pid})
    return el, res


D.setup_forward()
D.section("P0 根因对照：同字节数、不同字符数")
print("载荷字节数固定约 120B，字符数依次递减（中文一字 3 字节）", flush=True)

cases = [
    ("纯 ASCII 120 字", "A" * 120, 120),
    ("中英混合 60 字（约 150B）", "探针A" * 30, 90),
    ("纯中文 40 字（120B）", "中" * 40, 120),
    ("纯中文 10 字（30B）", "中" * 10, 30),
]
for label, name, _n in cases:
    el, res = push("P0probe_" + name)
    ok = bool(res.get("ok"))
    print("  %-26s 字符=%-4d 字节=%-4d %5.1fs  ok=%-5s %s" % (
        label, len(name), len(name.encode("utf-8")), el, ok,
        json.dumps(res, ensure_ascii=False)[:80]), flush=True)
    D.rec("P0 对照：%s 推送%s" % (label, "成功" if ok else "失败"), True,
          "%.1fs" % el)

D.section("P0 失败时手机端是否有日志")
out = D.sh("logcat -d -t 400 2>/dev/null | grep -iE 'ShareNet|share|WeakNet.*(error|exception)' | tail -8")
print(out.strip()[:600] or "（无任何相关日志）", flush=True)
D.rec("P0 失败在手机端留下日志", bool(out.strip()), (out.strip()[:200] or "静默失败，无日志"))

D.summary("P0 对照")
