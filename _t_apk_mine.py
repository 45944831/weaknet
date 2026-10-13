"""真机验证 Android 端「⭐ 我的自定义」栏：
保存 / 列表 / 以 u:<id> 应用 / 删除，以及 /api/targets 能否列出已安装包。

前一次 `/api/targets` 返回 0 个，是 Android 11+ 的包可见性限制（缺 <queries>）；
这一项专门盯着它，避免又提交一个「看起来能用、真机上选不出 App」的版本。
"""
import json
import subprocess
import sys
import time
import urllib.request

API = "http://127.0.0.1:18951"
CHROME = "com.android.chrome"
checks = []


def ck(name, ok, extra=""):
    checks.append((name, bool(ok), extra))
    print(("  PASS  " if ok else "  FAIL  ") + name +
          (("  <- " + str(extra)) if (extra and not ok) else ""))


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=90)


def api(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data,
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def dump_ui():
    sh("adb shell uiautomator dump /sdcard/wb_ui.xml")
    return sh("adb shell cat /sdcard/wb_ui.xml").stdout


def main():
    print("=== 安装并启动 ===")
    out = sh("adb install -r android/WeakNet.apk").stdout.strip().splitlines()
    print("   ", out[-1] if out else "(无输出)")
    sh("adb shell am force-stop com.weaknet.app")
    time.sleep(1)
    sh("adb shell input keyevent 224")
    time.sleep(0.6)
    sh("adb shell am start -n com.weaknet.app/.MainActivity")
    time.sleep(3)
    xml = dump_ui()
    if "允许" in xml or "确定" in xml:
        sh('adb shell uiautomator dump /sdcard/wb_ui.xml')
        # 用文本点击授权按钮（部分 ROM 显示为「确定」）
        sh('adb shell input tap 720 1400')       # 兜底：屏幕中下方的确认按钮
        print("    已尝试点击授权弹窗")
    time.sleep(2)
    sh("adb forward tcp:18951 tcp:8899")
    time.sleep(1)

    print("\n=== 1. 接管目标列表（依赖 <queries>）===")
    try:
        t = api("/api/targets")
    except Exception as e:
        ck("接口可用", False, str(e))
        return 1
    ids = [it["id"] for it in t.get("items", [])]
    ck("type = package", t.get("type") == "package", t.get("type"))
    print("    共 %d 个应用" % len(ids))
    ck("能列出已安装包（>5 个）", len(ids) > 5, "共 %d 个" % len(ids))
    ck("列表含 %s" % CHROME, CHROME in ids, "前5: %s" % ids[:5])
    # 只排除本 App 自己（com.weaknet.app）。设备上可能还残留旧包名的构建（com.awu.weaknet），
    # 那是另一个已安装应用，出现在列表里是正常的。
    ck("不含自身包名", "com.weaknet.app" not in ids)

    print("\n=== 2. 自定义栏：保存 ===")
    for it in api("/api/upresets").get("items", []):
        api("/api/upreset/del", {"id": it["id"]})
    r = api("/api/upreset", {"name": "真机验证",
                             "profile": {"down_kbps": 800, "up_kbps": 200,
                                         "loss_pct": 3}})
    ck("保存返回 ok", r.get("ok") is True, str(r)[:120])
    pid = r.get("id")
    ck("返回了 id", bool(pid), str(pid))
    items = api("/api/upresets").get("items", [])
    ck("列表能查到", any(i["id"] == pid for i in items),
       "共 %d 条" % len(items))
    p = [i for i in items if i["id"] == pid]
    if p:
        prof = p[0]["profile"]
        ck("参数原样保存", prof.get("down_kbps") == 800 and
           prof.get("loss_pct") == 3, json.dumps(prof, ensure_ascii=False)[:120])

    print("\n=== 3. 应用（u:<id>）===")
    okr = api("/api/preset", {"key": "u:" + pid})
    ck("应用返回 ok", okr.get("ok") is True, str(okr)[:120])
    time.sleep(1)
    st = api("/api/status")
    pr = st.get("profile", {})
    ck("当前场景已切换", pr.get("name") == "真机验证", pr.get("name"))
    ck("限速值已生效", pr.get("down_kbps") == 800, pr.get("down_kbps"))

    print("\n=== 4. 越界值被夹住 ===")
    bad = api("/api/upreset", {"name": "越界", "profile": {"loss_pct": 999}})
    bprof = [i for i in bad.get("items", []) if i["id"] == bad.get("id")]
    if bprof:
        v = bprof[0]["profile"].get("loss_pct")
        ck("丢包率 999 被夹到 100", v == 100, v)
    api("/api/upreset/del", {"id": bad.get("id")})

    print("\n=== 5. 删除 ===")
    d = api("/api/upreset/del", {"id": pid})
    ck("删除返回 ok", d.get("ok") is True, str(d)[:120])
    ck("删除后列表不含它",
       not any(i["id"] == pid for i in api("/api/upresets").get("items", [])))
    ck("删除不存在的返回 false",
       api("/api/upreset/del", {"id": pid}).get("ok") is False)

    print("\n=== 6. 崩溃检查 ===")
    # 只认真正的崩溃。"AndroidRuntime: Shutting down VM" 是进程被杀/正常退出，
    # 我们前面 force-stop 过，会留下这种行，不能当成崩溃。
    lg = sh("adb logcat -d -t 800").stdout
    fatal = [l for l in lg.splitlines()
             if "FATAL EXCEPTION" in l or ("AndroidRuntime" in l and "FATAL" in l)]
    ck("无崩溃", not fatal, (fatal[0][:120] if fatal else ""))

    print("\n===== 结论 =====")
    ok = all(v for _n, v, _e in checks)
    for n, v, e in checks:
        if not v:
            print("  失败：" + n + "  " + str(e))
    print("总体:", "PASS" if ok else "FAIL",
          "（%d/%d）" % (sum(1 for _n, v, _e in checks if v), len(checks)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
