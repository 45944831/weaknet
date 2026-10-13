"""自定义栏端到端验证：HTTP 接口的保存 / 列表 / 应用 / 删除 / 落盘与重启后仍在"""
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PROXY_PORT = 8960
CTRL_PORT = 8961
STORE = os.path.join(BASE, "weaknet_presets.json")

checks = []


def ck(name, cond, extra=""):
    checks.append((name, bool(cond), extra))


def api(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    m = method or ("POST" if data else "GET")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (CTRL_PORT, path),
                                 data=data, method=m)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def start_engine():
    p = subprocess.Popen([PY, os.path.join(BASE, "weaknet.py"),
                          "--proxy-port", str(PROXY_PORT),
                          "--ctrl-port", str(CTRL_PORT)],
                         cwd=BASE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % CTRL_PORT,
                                        timeout=1):
                return p
        except Exception:
            time.sleep(0.2)
    return p


def main():
    if os.path.exists(STORE):
        os.remove(STORE)

    eng = start_engine()
    try:
        # ---- 1. 初始为空 ----
        code, j = api("/api/upresets")
        ck("GET /api/upresets 可用", code == 200, str(code))
        ck("初始列表为空", j.get("items") == [], str(j))

        # ---- 2. 保存（带名字）----
        prof = {"latency_down_ms": 120, "latency_up_ms": 80, "jitter_ms": 30,
                "down_kbps": 1800, "up_kbps": 600, "loss_pct": 4, "stall_ms": 900,
                "disconnect_pct": 0, "timeout_pct": 0, "dns_fail_pct": 0,
                "reject_pct": 0, "flap_sec": 0, "flap_blackout_sec": 2}
        code, j = api("/api/upreset", {"name": "地铁 3G 抖动", "profile": prof})
        ck("POST /api/upreset 保存成功", code == 200 and j.get("ok"), str(j))
        ck("返回 id", bool(j.get("id")))
        pid1 = j.get("id")
        ck("列表返回 1 条", len(j.get("items", [])) == 1, str(j.get("items")))
        ck("保存后已落盘", os.path.exists(STORE))

        # ---- 3. 再存一条 + 非法请求 ----
        code, _j = api("/api/upreset", {"name": "电梯", "profile": {"down_kbps": 256}})
        ck("第二条保存成功", code == 200)
        code, _j = api("/api/upreset", {"name": "没参数"})
        ck("缺 profile 返回 400", code == 400, str(code))
        code, _j = api("/api/upreset", {"profile": prof})
        ck("缺名字也能存（后台自动命名）", code == 200)
        code, j = api("/api/upresets")
        names = [it["name"] for it in j["items"]]
        ck("三条都在", len(names) == 3, str(names))
        ck("自动命名生效", any("自定义" in n for n in names), str(names))

        # ---- 4. 应用自定义场景 ----
        code, j = api("/api/preset", {"key": "u:" + pid1})
        ck("切换自定义场景返回 200", code == 200, str(code))
        code, st = api("/api/status")
        ck("当前场景名为自定义名", st["profile"]["name"] == "地铁 3G 抖动",
           st["profile"]["name"])
        ck("参数为所存的值", st["profile"]["down_kbps"] == 1800
           and st["profile"]["latency_down_ms"] == 120, str(st["profile"]))
        ck("preset_key 形如 u:<id>", st["preset_key"] == "u:" + pid1, st["preset_key"])
        code, j = api("/api/preset", {"key": "u:不存在"})
        ck("未知自定义 key 返回 400", code == 400, str(code))

        # ---- 5. 覆盖更新同名 id ----
        code, j = api("/api/upreset", {"id": pid1, "name": "地铁 3G（加强）",
                                       "profile": {"down_kbps": 900}})
        ck("按 id 覆盖更新", code == 200 and len(j.get("items", [])) == 3, str(j))
        item = [x for x in j["items"] if x["id"] == pid1][0]
        ck("覆盖后参数为新值", item["profile"]["down_kbps"] == 900, str(item["profile"]))
        item2 = [x for x in j["items"] if x["id"] != pid1][0]

        # ---- 6. 删除 ----
        code, j = api("/api/upreset/del", {"id": item2["id"]})
        ck("删除返回 200", code == 200 and j.get("ok"), str((code, j)))
        ck("删除后剩 2 条", len(j.get("items", [])) == 2, str(len(j.get("items", []))))
        code, j = api("/api/upresets")
        ck("列表接口同步", len(j["items"]) == 2, str(len(j["items"])))
        code, _j = api("/api/upreset/del", {"id": "不存在的id"})
        ck("删不存在的返回 ok=false", code == 200 and _j.get("ok") is False, str(_j))
        code, _j = api("/api/upreset/del", {})
        ck("删除缺 id 返回 400", code == 400, str(code))

        # ---- 7. 已删的场景不能再应用 ----
        code, _j = api("/api/preset", {"key": "u:" + item2["id"]})
        ck("已删除的场景无法切换", code == 400, str(code))

        # ---- 8. 错误 “污染”：非法值被收敛 ----
        code, j = api("/api/upreset", {"name": "负数测试",
                                       "profile": {"down_kbps": -5, "loss_pct": "x",
                                                   "unknown_key": 1}})
        ck("非法值被清洗", code == 200 and all(x["name"] != "负数测试"
                                           or x["profile"]["down_kbps"] == 0
                                           for x in j["items"]), str(j["items"][-1]))
    finally:
        eng.terminate()
        eng.wait(timeout=10)

    # ---- 9. 重启后仍在（持久化）----
    eng = start_engine()
    try:
        code, j = api("/api/upresets")
        names = [it["name"] for it in j.get("items", [])]
        ck("重启后列表已恢复", len(names) == 3, str(names))
        ck("重启后名字仍在", "地铁 3G（加强）" in names, str(names))
        pid = [x["id"] for x in j["items"] if x["name"] == "地铁 3G（加强）"][0]
        code, st = api("/api/status")
        api("/api/preset", {"key": "u:" + pid})
        code, st = api("/api/status")
        ck("重启后可正常应用", st["profile"]["name"] == "地铁 3G（加强）"
           and st["profile"]["down_kbps"] == 900, str(st["profile"]))
        for it in j["items"]:
            api("/api/upreset/del", {"id": it["id"]})
        code, j = api("/api/upresets")
        ck("清理后列表为空", j["items"] == [], str(j))
    finally:
        eng.terminate()
        eng.wait(timeout=10)

    print("=== 自定义栏接口端到端 ===")
    ok = True
    for n, v, e in checks:
        print(("  PASS  " if v else "  FAIL  ") + n + (("  <- " + e) if (e and not v) else ""))
        ok = ok and v
    print("总体:", "PASS" if ok else "FAIL")
    if os.path.exists(STORE):
        os.remove(STORE)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
