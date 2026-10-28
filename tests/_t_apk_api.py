# -*- coding: utf-8 -*-
"""APK 控制接口（adb forward → 127.0.0.1:8899）功能测试。"""
import json
import urllib.request

B = "http://127.0.0.1:18899"
R = []


def call(path, data=None, method=None, timeout=8):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(B + path, data=body,
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
    R.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""), flush=True)


st, raw = call("/api/status")
rec("APK /api/status 返回 JSON", st == 200 and raw[:1] == b"{", "status=%s body=%r" % (st, raw[:40]))

st, raw = call("/api/presets")
rec("APK /api/presets 17 支", st == 200 and len(json.loads(raw)) == 17, "n=%d" % len(json.loads(raw)))

# ---- 自定义场景 增/列/应用/删 ----
st, raw = call("/api/upreset", {"name": "真机测试-300k", "profile": {"down_kbps": 300}})
uid = None
if st == 200 and raw[:1] == b"{":
    d = json.loads(raw)
    uid = d.get("id")
rec("APK 保存自定义场景", st == 200 and uid, "%s" % raw[:80])

st, raw = call("/api/upresets")
items = json.loads(raw).get("items", []) if raw[:1] == b"{" else []
rec("APK 自定义列表可见", any(i.get("id") == uid for i in items), "%d 条" % len(items))

st, raw = call("/api/preset", {"key": "u:" + uid}) if uid else (0, b"")
rec("APK 应用自定义场景 u:<id>", st == 200, "status=%s" % st)

st, raw = call("/api/scope")
scope = json.loads(raw) if raw[:1] == b"{" else {}
rec("APK /api/scope 读取", st == 200 and scope.get("mode") == "all", json.dumps(scope, ensure_ascii=False))

st, raw = call("/api/targets")
targets = json.loads(raw).get("items", []) if raw[:1] == b"{" else []
rec("APK /api/targets 列出已安装应用", len(targets) > 20, "%d 个" % len(targets))

# ---- 接管范围：仅选定包 ----
st, raw = call("/api/scope", {"mode": "only", "list": ["com.android.chrome"]})
st2, raw2 = call("/api/scope")
got = json.loads(raw2) if raw2[:1] == b"{" else {}
rec("APK 接管范围写入并回读", st == 200 and got.get("mode") == "only"
    and got.get("list") == ["com.android.chrome"], json.dumps(got, ensure_ascii=False))

st, raw = call("/api/scope", {"mode": "all", "list": []})
st2, raw2 = call("/api/scope")
got = json.loads(raw2) if raw2[:1] == b"{" else {}
rec("APK 接管范围恢复全部", got.get("mode") == "all", json.dumps(got, ensure_ascii=False))

# ---- 限速总开关 ----
st, raw = call("/api/run", {"enable": False})
st2, raw2 = call("/api/run")
run = json.loads(raw2) if raw2[:1] == b"{" else {}
rec("APK 停止限速", run.get("limit_on") is False, str(run))
st, raw = call("/api/run", {"enable": True})
st2, raw2 = call("/api/run")
run = json.loads(raw2) if raw2[:1] == b"{" else {}
rec("APK 开启限速", run.get("limit_on") is True, str(run))

# ---- 删除自定义 ----
if uid:
    st, raw = call("/api/upreset/del", {"id": uid})
    st2, raw2 = call("/api/upresets")
    left = json.loads(raw2).get("items", []) if raw2[:1] == b"{" else []
    rec("APK 删除自定义场景", all(i.get("id") != uid for i in left), "剩余 %d 条" % len(left))

# ---- D4/D5 复测 ----
st, raw = call("/api/run")
run = json.loads(raw) if raw[:1] == b"{" else {}
rec("APK GET /api/run 查询开关（D4）", st == 200 and "limit_on" in run, str(run))
st, raw = call("/api/me")
me = json.loads(raw) if raw[:1] == b"{" else {}
rec("APK GET /api/me 身份（D5）", st == 200 and me.get("platform") == "android" and me.get("id"),
    json.dumps(me, ensure_ascii=False))

# ---- 畸形输入 ----
st, raw = call("/api/preset", {"key": "nope"})
rec("APK 非法 preset → 400", st == 400, "status=%s" % st)
st, raw = call("/api/upreset/del", {})
rec("APK 缺 id 删除 → 400", st == 400, "status=%s" % st)

fails = [x for x in R if not x[1]]
print("\n==== APK API：%d 项，通过 %d，失败 %d ====" % (len(R), len(R) - len(fails), len(fails)))
with open("_t_apk_api.json", "w", encoding="utf-8") as f:
    json.dump([{"name": n, "ok": ok, "detail": d} for n, ok, d in R],
              f, ensure_ascii=False, indent=1)
