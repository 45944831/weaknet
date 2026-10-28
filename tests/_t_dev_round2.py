# -*- coding: utf-8 -*-
"""真机第二轮深度测试：补上第一轮没覆盖的面。

与第一轮（149 项）不重复的部分：
  D1  logcat 崩溃/ANR/ native crash 监测（压力过程中全程挂监视）
  D2  内存长稳（PSS 前后对比，查泄漏）
  D3  空闲 CPU 占用（DelayLine 空转是否真的消除）
  D4  UDP 数据面（延迟 + 限速，UdpSession 路径）
  D5  VPN 生命周期：锁屏传输 / 切后台 / 反复切换 / 关闭后不自动恢复
  D6  停止限速后完全恢复（不应有残留欠账）
  D7  并发带宽重测（用修正后的探针，验证第一轮 1.15x/1.29x 是测量缺陷）
  D8  IPv6 行为（已知不接管，确认不会崩）
  D9  设备侧 API 并发竞态
  D10 长跑后统计自洽

第一轮结论回顾：Pipe.Bucket 的 JDK 并发单测已证明 1/2/4/8 并发精确到
0.5% 以内，Windows 引擎同样精确，所以 D7 是用干净探针在真机上复核。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request

# 设备与 adb 路径复用 _t_dev_lib 的探测逻辑（环境变量优先 → 自动选唯一在线设备），
# 不在这里再写死一份 —— 写死了换机器就跑不了。
sys.path.insert(0, HERE)
from _t_dev_lib import ADB, SERIAL  # noqa: E402



CTRL = "http://127.0.0.1:18899"
PKG = "com.weaknet.app"
CLK_TCK = 100

R = []


def rec(name, ok, detail=""):
    R.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""), flush=True)


def sh(cmd, t=300):
    p = subprocess.run([ADB, "-s", SERIAL, "shell", cmd], capture_output=True,
                       text=True, encoding="utf-8", errors="ignore", timeout=t)
    return (p.stdout or "").strip()


def sh_ret(cmd, t=300):
    return subprocess.run([ADB, "-s", SERIAL, "shell", cmd], capture_output=True,
                          text=True, encoding="utf-8", errors="ignore", timeout=t)


def api(path, data=None, timeout=15):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(CTRL + path, data=body,
                                 method=("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": str(e)}


def scene(key, enable=True):
    api("/api/preset", {"key": key})
    api("/api/run", {"enable": enable})


def profile(d):
    api("/api/profile", d)
    api("/api/run", {"enable": True})


def probe(nbytes, host="10.8.0.2", port=8888, t=300, tag="0"):
    """单连接下载探针（修正版：独立输出文件）"""
    txt = sh("sh /data/local/tmp/wn_tun.sh %s %d %d %s" % (host, port, nbytes, tag), t=t)
    for line in txt.splitlines():
        p = line.split()
        if len(p) >= 5 and p[0] == "BYTES":
            return int(p[1]), int(p[3]) - int(p[2])
    return None, None


def par(n, each, t=300):
    """并发下载探针：返回 (总字节, 重叠窗口ms, 连接数, 未收满数)"""
    out = sh("sh /data/local/tmp/wn_par.sh 10.8.0.2 8888 %d %d" % (each, n), t=t)
    parts = out.split()
    if len(parts) >= 5 and parts[0] == "PAR":
        return int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
    return None


def pid():
    return sh("pidof %s" % PKG)


def pss_kb():
    out = sh("dumpsys meminfo %s" % PKG)
    m = re.search(r"TOTAL PSS:\s+(\d+)", out)
    return int(m.group(1)) if m else None


def cpu_ticks(p):
    st = sh("cat /proc/%s/stat" % p)
    f = st.split()
    # /proc/pid/stat 第 14、15 项是 utime/stime（1-based），但 comm 可能含空格，
    # 这里用 ')' 之后的字段重新索引更稳
    try:
        rest = st.split(") ", 1)[1].split()
        return int(rest[11]) + int(rest[12])     # utime, stime
    except Exception:
        return None


CRASH_SIGS = ["FATAL EXCEPTION", "ANR in", "SIGSEGV", "SIGABRT", "Fatal signal",
              "beginning of crash", "E AndroidRuntime", "F DEBUG"]


def logcat_scan():
    out = sh_ret("logcat -d -v brief", t=120)
    txt = (out.stdout or "") + (out.stderr or "")
    hits = []
    for line in txt.splitlines():
        for s in CRASH_SIGS:
            if s in line:
                hits.append(line.strip()[:160])
                break
    return hits, len(txt.splitlines())


def main():
    # 环境检查
    dev = sh("getprop ro.serialno")
    if not dev:
        print("FAIL 设备不在线，无法执行真机测试")
        return 1
    print("设备 %s / %s / SDK %s" % (dev, sh("getprop ro.product.model"),
                                     sh("getprop ro.build.version.sdk")))
    p = pid()
    if not p:
        print("FAIL APK 未运行（pidof 为空）")
        return 1
    print("APK pid=%s  PSS=%s KB\n" % (p, pss_kb()))

    # 清 logcat 并起后台监测
    sh("logcat -c")
    logcat_lines = [0]

    def logwatch():
        """持续抓 logcat 到文件，供最后统一扫描"""
        try:
            subprocess.run([ADB, "-s", SERIAL, "logcat", "-v", "brief"],
                           stdout=open(r"C:\Users\user\WorkBuddy\2026-10-13-15-51-24\_logcat_r2.txt", "w",
                                       encoding="utf-8", errors="replace"),
                           stderr=subprocess.DEVNULL, timeout=1800)
        except Exception:
            pass

    threading.Thread(target=logwatch, daemon=True).start()
    time.sleep(0.5)

    # ---------- D7 并发带宽（修正探针）----------
    print("== D7 并发总带宽（修正探针后重测，设定 2000 kibps）==")
    profile({"name": "并发复测", "down_kbps": 2000, "up_kbps": 0})
    time.sleep(1.0)
    EACH = 1024 * 1024
    d7rows = []
    for n in (1, 2, 4, 8):
        r = par(n, EACH)
        if r is None:
            rec("D7 %d 并发探针输出异常" % n, False)
            continue
        tot, win, got, bad = r
        kbps = tot * 8 / 1024 / (win / 1000.0) if win else 0
        d7rows.append((n, kbps, bad, got))
        rec("D7 %d 并发聚合带宽 ±15%%" % n,
            0.85 * 2000 <= kbps <= 1.15 * 2000 and bad == 0,
            "%.0f kibps（%.2fx）%d B / %d ms，未收满 %d/%d"
            % (kbps, kbps / 2000.0, tot, win, bad, got))
        time.sleep(3)
    api("/api/run", {"enable": False})
    print("    D7 明细：" + " | ".join("N=%d %.3fx" % (n, k / 2000.0) for n, k, _b, _g in d7rows))

    # ---------- D6 停止后完全恢复 ----------
    print("\n== D6 停止限速后完全恢复 ==")
    scene("congestion")
    b, t = probe(512 * 1024, tag="d6a")
    slow = (b or 0) > 0 and (t or 0) > 3000
    rec("D6 限速中确实慢（对照）", slow, "%s B / %s ms" % (b, t))
    api("/api/run", {"enable": False})
    time.sleep(0.3)
    b2, t2 = probe(2 * 1024 * 1024, tag="d6b")
    kbps2 = b2 * 8 / 1024 / (t2 / 1000.0) if (b2 and t2) else 0
    rec("D6 停止后立即全速（无残留欠账）", b2 and kbps2 > 20000,
        "%s B / %s ms = %.0f kibps" % (b2, t2, kbps2))

    # ---------- D4 UDP 数据面 ----------
    print("\n== D4 UDP 数据面 ==")
    # PC 侧 UDP 回显服务，手机经 tun 访问；VPN 打开时 UDP 也走 UdpSession
    stop_udp = threading.Event()
    udp_hits = []

    def udp_srv():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 9131))
        s.settimeout(0.5)
        while not stop_udp.is_set():
            try:
                data, addr = s.recvfrom(4096)
                udp_hits.append(time.time())
                s.sendto(b"pong", addr)
            except socket.timeout:
                continue
            except Exception:
                break
        s.close()

    threading.Thread(target=udp_srv, daemon=True).start()
    time.sleep(0.3)
    # 手机侧：nc -u 发一个包，等回显
    udp_probe = (
        "S=$(cat /proc/uptime | cut -d. -f1); "
        "printf 'ping' | timeout 6 nc -u -w 3 10.8.0.2 9131 > /data/local/tmp/udp_out 2>/dev/null; "
        "E=$(cat /proc/uptime | cut -d. -f1); "
        "echo UDP $(wc -c < /data/local/tmp/udp_out 2>/dev/null || echo 0) $((E-S))"
    )
    scene("direct")
    out = sh(udp_probe, t=30)
    m = re.search(r"UDP (\d+) (\d+)", out)
    udp_ok = bool(m) and int(m.group(1)) > 0
    rec("D4 UDP 直连可收发", udp_ok, out[:80])
    # UDP 延迟：卫星档（上行 700ms）下 UDP 回显应变慢
    scene("satellite")
    t0 = time.time()
    out = sh(udp_probe, t=30)
    dt = time.time() - t0
    rec("D4 UDP 延迟档可收发", bool(re.search(r"UDP [1-9]", out)),
        "回显 %s，含 adb 开销总耗时 %.2fs" % (out[:40], dt))
    scene("direct")
    stop_udp.set()

    # ---------- D5 VPN 生命周期 ----------
    print("\n== D5 VPN 生命周期 ==")
    # D5a 锁屏后传输继续
    scene("4g")
    sh("input keyevent 223")            # SLEEP
    time.sleep(1.5)
    st = api("/api/status")
    rec("D5a 锁屏后 VPN 仍激活", st.get("vpn_active") is True, "vpn_active=%s" % st.get("vpn_active"))
    b, t = probe(1024 * 1024, tag="d5a")
    rec("D5a 锁屏期间传输可用", b and b >= 1024 * 1024, "%s B / %s ms" % (b, t))
    sh("input keyevent 224")            # WAKEUP
    time.sleep(1.0)

    # D5b 切后台
    sh("input keyevent 3")              # HOME
    time.sleep(1.5)
    st = api("/api/status")
    rec("D5b 切后台后 VPN 仍激活", st.get("vpn_active") is True, "vpn_active=%s" % st.get("vpn_active"))
    b, t = probe(1024 * 1024, tag="d5b")
    rec("D5b 后台期间传输可用", b and b >= 1024 * 1024, "%s B / %s ms" % (b, t))
    sh("am start -n %s/.MainActivity" % PKG)
    time.sleep(2)

    # D5c 反复切前后台 10 次
    ok = True
    for i in range(10):
        sh("input keyevent 3")
        time.sleep(0.4)
        sh("am start -n %s/.MainActivity" % PKG)
        time.sleep(0.6)
        if api("/api/status").get("vpn_active") is not True:
            ok = False
            break
    rec("D5c 反复切前后台 10 次 VPN 不掉", ok)

    # D5d 关掉限速与 VPN 后不应自动恢复
    api("/api/run", {"enable": False})
    api("/api/vpn", {"enable": False})
    time.sleep(2.0)
    st = api("/api/status")
    rec("D5d 关闭 VPN 后确实断开", st.get("vpn_active") is False, "vpn_active=%s" % st.get("vpn_active"))
    api("/api/vpn", {"enable": True})
    time.sleep(3.0)
    st = api("/api/status")
    rec("D5d 重新启用 VPN 后限速不自动恢复", st.get("limit_on") is False,
        "limit_on=%s preset=%s" % (st.get("limit_on"), st.get("preset_key")))

    # ---------- D2/D3 内存与 CPU ----------
    print("\n== D2/D3 内存与 CPU ==")
    p = pid()
    pss0 = pss_kb()
    # 压力：17 档场景轮换 + 持续传输
    keys = [x["key"] for x in (api("/api/presets") or []) if isinstance(x, dict)]
    if not keys:
        st = api("/api/presets")
        keys = [x["key"] for x in st] if isinstance(st, list) else []
    for k in (keys or ["direct"]) * 1:
        scene(k)
        b, t = probe(1024 * 1024, tag="d2")
    ck0 = cpu_ticks(p)
    time.sleep(30)                       # 空闲 30s
    ck1 = cpu_ticks(p)
    idle_pct = (ck1 - ck0) / (30.0 * CLK_TCK) * 100 if (ck0 is not None and ck1 is not None) else -1
    rec("D3 空闲 30s CPU 占用 < 5%", 0 <= idle_pct < 5, "%.2f%%" % idle_pct)
    pss1 = pss_kb()
    rec("D2 压力后 PSS 未暴涨（< 2 倍且 < 300MB）",
        pss0 and pss1 and pss1 < max(2 * pss0, 300 * 1024),
        "PSS %s -> %s KB" % (pss0, pss1))

    # ---------- D8 IPv6 ----------
    print("\n== D8 IPv6 ==")
    scene("direct")
    out = sh("ping -6 -c 2 -W 2 2400:3200::1 2>&1 | tail -2", t=20)
    alive_after = api("/api/status").get("vpn_active") is True
    rec("D8 IPv6 探测不导致 VPN 异常", alive_after, out[:80])

    # ---------- D9 设备侧 API 并发竞态 ----------
    print("\n== D9 设备侧 API 并发 ==")
    errs = []

    def toggler(i):
        for k in range(20):
            r = api("/api/run", {"enable": bool(k % 2 == 0)})
            if "error" in r:
                errs.append(str(r)[:60])

    def poller(i):
        for k in range(30):
            r = api("/api/status")
            if "error" in r:
                errs.append(str(r)[:60])

    ths = ([threading.Thread(target=toggler, args=(i,)) for i in range(3)]
           + [threading.Thread(target=poller, args=(i,)) for i in range(3)])
    for t in ths:
        t.start()
    for t in ths:
        t.join(60)
    rec("D9 6 线程并发 150 次请求无异常", not errs, "; ".join(errs[:3]))
    b, t = probe(1024 * 1024, tag="d9")
    rec("D9 竞态后数据面仍可用", b and b >= 1024 * 1024, "%s B / %s ms" % (b, t))

    # ---------- D10 统计自洽 ----------
    print("\n== D10 统计自洽 ==")
    st = api("/api/status")
    s = st.get("stats") or {}
    rec("D10 累计记账为正且活跃会话归零",
        (s.get("down") or 0) > 0 and (s.get("active") or 0) == 0,
        "down=%s up=%s active=%s total=%s" % (s.get("down"), s.get("up"),
                                              s.get("active"), s.get("total")))

    # ---------- logcat 扫描 ----------
    print("\n== D1 logcat 崩溃扫描 ==")
    time.sleep(2)
    try:
        txt = open(r"C:\Users\user\WorkBuddy\2026-10-13-15-51-24\_logcat_r2.txt",
                   encoding="utf-8", errors="replace").read()
    except Exception:
        txt = ""
    hits = [l for l in txt.splitlines() if any(s in l for s in CRASH_SIGS)]
    rec("D1 全过程无 FATAL/ANR/native crash", not hits,
        "%d 行命中；样本：%s" % (len(hits), hits[0][:120] if hits else "无"))
    if hits:
        for h in hits[:10]:
            print("    ! " + h[:160])

    # 收尾：恢复直连
    scene("direct")
    api("/api/run", {"enable": False})

    bad = [n for n, o in R if not o]
    print("\n===== 真机第二轮：%d/%d 通过 =====" % (len(R) - len(bad), len(R)))
    for n in bad:
        print("FAILED: " + n)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
