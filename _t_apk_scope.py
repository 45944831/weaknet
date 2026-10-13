"""真机验证「指定 App 接管」：
用 com.android.chrome 做对照 —— 名单包含它时，它的流量应被 VPN 捕获（连接数增长）；
名单不含它时，它的流量应完全绕过 VPN（连接数几乎不动）。
"""
import json
import subprocess
import sys
import time
import urllib.request

API = "http://127.0.0.1:18950"
CHROME = "com.android.chrome"
OTHER = "com.heytap.browser"
URLS = ["http://example.com/?weaknet=%d" % i for i in range(1, 4)]

checks = []


def ck(name, ok, extra=""):
    checks.append((name, bool(ok), extra))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  <- " + extra) if (extra and not ok) else ""))


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)


def api(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data,
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def conn_total():
    return api("/api/status")["stats"]["total"]


def open_in_chrome():
    for u in URLS:
        sh('adb shell am start -a android.intent.action.VIEW -d "%s" %s' % (u, CHROME))
        time.sleep(1.2)


def main():
    print("=== 安装并启动 ===")
    print(sh("adb install -r android/WeakNet.apk").stdout.strip().splitlines()[-1])
    sh("adb shell am force-stop com.weaknet.app")
    time.sleep(1)
    sh("adb shell am start -n com.weaknet.app/.MainActivity")
    time.sleep(9)
    sh("adb forward tcp:18950 tcp:8899")
    time.sleep(1)

    print("\n=== 1. 目标列表接口 ===")
    t = api("/api/targets")
    ids = [it["id"] for it in t.get("items", [])]
    ck("type = package", t.get("type") == "package", str(t.get("type")))
    ck("列表非空", len(ids) > 5, "共 %d 个" % len(ids))
    ck("包含 %s" % CHROME, CHROME in ids, "前5: %s" % ids[:5])
    ck("不含自身包名", "com.weaknet.app" not in ids)

    print("\n=== 2. 默认范围 ===")
    s0 = api("/api/scope")
    ck("默认 all", s0["mode"] == "all", str(s0))

    print("\n=== 3. 设为「仅 %s」===" % CHROME)
    api("/api/scope", {"mode": "only", "list": [CHROME]})
    time.sleep(5)                     # 等服务重建 tun
    st = api("/api/status")
    ck("VPN 重建后仍接管", st.get("vpn_active") is True, "vpn_active=%s" % st.get("vpn_active"))
    ck("scope 已生效", st["scope"]["mode"] == "only" and st["scope"]["list"] == [CHROME],
       str(st.get("scope")))

    print("\n=== 4. Chrome 访问网络（名单内含它 → 流量应被捕获）===")
    c0 = conn_total()
    open_in_chrome()
    time.sleep(8)
    c1 = conn_total()
    hit = c1 - c0
    print("    连接数 %d -> %d（+%d）" % (c0, c1, hit))
    ck("Chrome 流量被 VPN 捕获", hit >= 2, "增量 %d" % hit)

    print("\n=== 5. 改为「仅 %s」，再让 Chrome 访问（流量应绕过 VPN）===" % OTHER)
    api("/api/scope", {"mode": "only", "list": [OTHER]})
    time.sleep(5)
    st = api("/api/status")
    ck("VPN 仍接管", st.get("vpn_active") is True, "vpn_active=%s" % st.get("vpn_active"))
    c2 = conn_total()
    # 换 URL，避免 Chrome 复用缓存连接
    global URLS
    URLS = ["http://example.org/?weaknet=%d" % i for i in range(1, 4)]
    open_in_chrome()
    time.sleep(8)
    c3 = conn_total()
    bypass = c3 - c2
    print("    连接数 %d -> %d（+%d）" % (c2, c3, bypass))
    ck("Chrome 流量绕过 VPN", bypass < max(2, hit // 2),
       "本次增量 %d vs 名单内时 %d" % (bypass, hit))

    print("\n=== 6. 恢复全部接管 ===")
    api("/api/scope", {"mode": "all", "list": []})
    time.sleep(4)
    st = api("/api/status")
    ck("恢复 all", st["scope"]["mode"] == "all", str(st.get("scope")))
    ck("VPN 正常", st.get("vpn_active") is True, "vpn_active=%s" % st.get("vpn_active"))

    print("\n=== 7. 崩溃检查 ===")
    lg = sh("adb logcat -d -t 600").stdout
    fatal = [l for l in lg.splitlines() if "FATAL EXCEPTION" in l or "AndroidRuntime" in l]
    ck("无崩溃", not fatal, (fatal[0][:120] if fatal else ""))

    print("\n===== 结论 =====")
    ok = all(v for _n, v, _e in checks)
    for n, v, e in checks:
        if not v:
            print("  失败：" + n + "  " + e)
    print("总体:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
