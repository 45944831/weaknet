# -*- coding: utf-8 -*-
"""APK 参数边界与状态机健壮性测试（真机，经 adb forward 18899）。

关注点不是「弱网效果对不对」，而是「喂进去脏数据会不会崩/会不会静默失效」：
  - 负值、超大值、非数字、缺字段、类型错
  - 参数夹取后回读是否落在合法区间
  - 非法请求必须 4xx，不能 500 也不能把服务打死
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import sys
import urllib.request

CTRL = "http://127.0.0.1:18899"
R = []


def call(path, data=None, method=None, raw=None, timeout=10):
    if raw is not None:
        body = raw
    elif data is not None:
        body = json.dumps(data).encode()
    else:
        body = None
    req = urllib.request.Request(CTRL + path, data=body,
                                 method=method or ("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


def rec(name, ok, detail=""):
    R.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""), flush=True)


def prof():
    st, raw = call("/api/status")
    try:
        return json.loads(raw).get("profile") or {}
    except Exception:
        return {}


def set_profile(d):
    call("/api/profile", d)
    return prof()


def alive():
    """服务是否还活着（任何合法响应都算）"""
    st, raw = call("/api/status")
    return st == 200 and raw[:1] == b"{"


print("== E1 负值参数应夹到 0 ==", flush=True)
p = set_profile({"name": "负值", "down_kbps": -100, "up_kbps": -5,
                 "latency_up_ms": -50, "latency_down_ms": -1,
                 "loss_pct": -20, "jitter_ms": -3})
rec("负值全部夹到 0",
    p.get("down_kbps") == 0 and p.get("up_kbps") == 0
    and p.get("latency_up_ms") == 0 and p.get("latency_down_ms") == 0
    and p.get("loss_pct") == 0 and p.get("jitter_ms") == 0,
    "down=%s up=%s lat=%s/%s loss=%s jit=%s"
    % (p.get("down_kbps"), p.get("up_kbps"), p.get("latency_up_ms"),
       p.get("latency_down_ms"), p.get("loss_pct"), p.get("jitter_ms")))

print("== E2 超大值应夹到上限 ==", flush=True)
p = set_profile({"name": "超大", "down_kbps": 99999999, "up_kbps": 99999999,
                 "latency_up_ms": 999999, "latency_down_ms": 999999,
                 "loss_pct": 150, "jitter_ms": 99999})
rec("down/up 夹到 1000000", p.get("down_kbps") == 1000000 and p.get("up_kbps") == 1000000,
    "down=%s up=%s" % (p.get("down_kbps"), p.get("up_kbps")))
rec("延迟夹到 10000", p.get("latency_up_ms") == 10000 and p.get("latency_down_ms") == 10000,
    "lat=%s/%s" % (p.get("latency_up_ms"), p.get("latency_down_ms")))
rec("loss 夹到 100", p.get("loss_pct") == 100, "loss=%s" % p.get("loss_pct"))
rec("jitter 夹到 5000", p.get("jitter_ms") == 5000, "jit=%s" % p.get("jitter_ms"))

print("== E2b 整数型超 int 上限（v1.1.19 回归） ==", flush=True)
# 旧实现用 optInt：2147483648 截成负数后被判为非法，回落 0 → 静默不限速，仍回 ok:true。
# Windows 同一请求会夹到 1000000，采集脚本按 10 Gbps 下发时两端样本前提完全不同。
for v in (2147483648, 3000000000, 10000000000, 1e18):
    p = set_profile({"name": "超int", "down_kbps": v})
    rec("down_kbps=%s 夹到 1000000" % v, p.get("down_kbps") == 1000000,
        "down=%s" % p.get("down_kbps"))


print("== E3 非数字 / 类型错应被忽略而不是崩 ==", flush=True)
call("/api/profile", {"name": "脏数据", "down_kbps": "abc",
                      "latency_up_ms": None, "loss_pct": [1, 2]})
rec("非数字参数不影响服务存活", alive())
p = prof()
rec("非数字被当作缺省 0", p.get("down_kbps") == 0, "down=%s" % p.get("down_kbps"))

print("== E4 空 body / 畸形 JSON ==", flush=True)
st, _ = call("/api/profile", raw=b"", method="POST")
rec("空 body 不 500", st in (200, 400), "status=%s" % st)
st, _ = call("/api/profile", raw=b"{not json", method="POST")
rec("畸形 JSON 不 500", st in (200, 400), "status=%s" % st)
st, _ = call("/api/run", raw=b"[1,2,3]", method="POST")
rec("数组 body 不 500", st in (200, 400), "status=%s" % st)
rec("以上之后服务仍存活", alive())

print("== E5 未知路径 / 非法方法 ==", flush=True)
st, _ = call("/api/nope")
rec("未知路径 → 404", st == 404, "status=%s" % st)
# 控制接口对 method 是宽松的（非 GET 落到同一分支也能拿到结果），
# 只要不 5xx、不把服务打死即可，不苛求 405。
st, _ = call("/api/status", method="DELETE")
rec("不支持的方法不 5xx", st < 500, "status=%s" % st)

print("== E6 全 0 profile 与开关复位 ==", flush=True)
# 两端语义（WeakNet.resolveEnable / CtrlServer /api/profile）：
#   ① 应用「不含任何限制」的参数时，若限速开着，开关必须立刻复位为关 ——
#      否则 limit_on=true 会对应「零整形」，采集侧会把直连样本当成弱网样本；
#   ② 关→开 的复原取 lastProfile，所以「全 0 + 有历史」会被上一次生效过的场景复原，
#      这是为避免 4000 → 关 → 再开 变 0 而**有意**付出的代价（见 WeakNet.java setProfile 注释）。
# 旧用例只断言 ①，却把 ② 当成回归，必然误报；这里分开验。
def limit_on():
    st, raw = call("/api/run")
    try:
        return json.loads(raw).get("limit_on")
    except Exception:
        return None


call("/api/profile", {"name": "有参数", "down_kbps": 4000})
call("/api/run", {"enable": True})
rec("有参数时开启生效", limit_on() is True, "limit_on=%s down=%s"
    % (limit_on(), prof().get("down_kbps")))
call("/api/profile", {"name": "全0", "down_kbps": 0, "up_kbps": 0,
                      "latency_up_ms": 0, "latency_down_ms": 0,
                      "jitter_ms": 0, "loss_pct": 0})
p = prof()
rec("全 0 应用后开关复位为关", limit_on() is False, "limit_on=%s" % limit_on())
rec("全 0 应用后参数全 0",
    all(p.get(k) == 0 for k in ("down_kbps", "up_kbps", "latency_up_ms",
                                "latency_down_ms", "jitter_ms", "loss_pct")),
    "down=%s lat=%s loss=%s"
    % (p.get("down_kbps"), p.get("latency_down_ms"), p.get("loss_pct")))
call("/api/run", {"enable": True})
rec("再开启按设计回落最近一次生效参数", prof().get("down_kbps") == 4000,
    "down=%s（期望 4000，不是更早的 1000000）" % prof().get("down_kbps"))
call("/api/run", {"enable": False})

print("== E7 非法 preset / 缺 key ==", flush=True)
st, _ = call("/api/preset", {"key": "no_such_preset"})
rec("非法 preset → 400", st == 400, "status=%s" % st)
st, _ = call("/api/preset", {})
rec("缺 key → 400", st == 400, "status=%s" % st)
st, _ = call("/api/run", {"enable": "yes"})
rec("run enable 非布尔被拒或安全忽略", st in (200, 400), "status=%s" % st)
rec("最终服务存活", alive())

print("== E8 自定义场景边界 ==", flush=True)
call("/api/preset", {"key": "direct"})
st, raw = call("/api/upreset", {"name": "", "profile": {}})
rec("空名字保存不崩", st in (200, 400), "status=%s" % st)
st, raw = call("/api/upreset", {"name": "边界场景", "profile": {"down_kbps": -1, "loss_pct": 999}})
uid = None
if st == 200 and raw[:1] == b"{":
    uid = json.loads(raw).get("id")
    # 必须先开启限速：/api/preset 的语义是「选中」，未开启时只记 presetKey 不施加参数
    call("/api/run", {"enable": True})
    st2, _ = call("/api/preset", {"key": "u:" + uid})
    p = prof()
    rec("越界自定义值被夹取", st2 == 200 and p.get("down_kbps") == 0 and p.get("loss_pct") == 100,
        "apply=%s down=%s loss=%s" % (st2, p.get("down_kbps"), p.get("loss_pct")))
    call("/api/delpreset", {"id": uid})
else:
    rec("越界自定义值被夹取", False, "保存失败 status=%s" % st)

# 清掉本脚本自己造的场景，避免污染后续测试的自定义列表
st, raw = call("/api/upresets")
for it in (json.loads(raw).get("items", []) if raw[:1] == b"{" else []):
    if it.get("name", "").startswith("边界场景") or it.get("name", "").startswith("自定义"):
        call("/api/delpreset", {"id": it["id"]})

call("/api/run", {"enable": False})
call("/api/preset", {"key": "direct"})

bad = [x for x in R if not x[1]]
print("\n===== APK 边界与健壮性：%d/%d 通过 =====" % (len(R) - len(bad), len(R)))
for n, _o, d in bad:
    print("FAILED: %s | %s" % (n, d))
sys.exit(1 if bad else 0)
