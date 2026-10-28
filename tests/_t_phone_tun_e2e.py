# -*- coding: utf-8 -*-
"""真机 tun 数据面实测（VpnNetService -> TcpSession -> DelayLine）。

难点：手机与 PC 不在同一三层网络（手机在隔离 Wi-Fi 172.16.3.10/16，
PC 在 192.168.1.49/24，且该 Wi-Fi 不路由到 192.168.1.0/24），
所以「PC 架服务 + 手机过 VPN 访问」的标准路子走不通。

解法：让流量打进 tun0 再回环。
  - PC 起 HTTP 服务在 127.0.0.1:9126，adb reverse 把它暴露给手机的 127.0.0.1:9126
  - 手机客户端连 **10.8.0.2:8888**（tun0 地址）-> 包进 tun0 -> VpnNetService
    -> TcpSession（上游 socket 已 protect，走真实网络）-> 10.8.0.2:8888
    -> 应用自己的 ProxyServer -> 127.0.0.1:9126 -> adb reverse -> PC
  - 对照组：客户端直连 **127.0.0.1:8888**（127.0.0.0/8 走 lo，不进 tun0）
  - 两者唯一差别就是「有没有过 tun」，差额即 tun 数据面注入的效果
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import http.server
import json
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request

ADB = r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe"
CTRL = "http://127.0.0.1:18899"
SRV_PORT = 9126
PHONE_SH = "/data/local/tmp/wn_tun.sh"
PHONE_UP = "/data/local/tmp/wn_up.sh"
RESULTS = []

TUN_SH = r"""#!/system/bin/sh
# 用法: wn_tun.sh <host> <port> [nbytes]
# 计时口径：从发起连接到「收满预期字节」的墙钟，不含任何人为 sleep 等待。
# 为什么不用 wc -c 收尾：toybox nc 在 stdin EOF 时立即退出，printf|nc 会丢掉
# 尚未读到的响应（实测 BYTES 0 / 20ms）。改成后台跑 nc + 轮询输出文件大小，
# 服务端 9126 的响应头固定 37+len(N) 字节，故 EXPECT 可精确算出来。
H=$1; P=$2; N=${3:-0}
O=/data/local/tmp/wn_out
rm -f $O
HDR=$((37 + ${#N}))
EXP=$((HDR + N))
now() { read U _ < /proc/uptime; echo "$U" | awk -F. '{printf "%d", $1*1000 + substr($2 "00", 1, 3)}'; }
if [ "$N" -gt 0 ]; then LIM=1400; else LIM=200; fi
S=$(now)
# 撑住 nc 的 stdin 用「带 PID 的 sleep」，而不是裸 sleep 300：
#   * 裸 sleep 只能靠 pkill -f 'sleep 300' 收尾，并发跑多个探针时先结束的那个
#     会把别人的 sleep 一起杀掉，对端 nc 提前 EOF、连接被掐，测量全乱
#     （实测同一配置 4 并发出现 11.4s / 18.4s 两种结果）；
#   * 完全不 pkill 又会让 sleep 活 300s 并拖着 adb shell 一起等，探针算完了
#     却卡到 90s/180s 超时；
#   * 用 "sleep 300.<本脚本PID>" 让每个探针的 sleep 命令行唯一，pkill -f 精确
#     只杀自己那个，两边问题一起解决。
#   （试过 fifo 方案更干净，但 /data/local/tmp 上 mkfifo 被 SELinux 拒绝：
#     mkfifo: Permission denied）
SL="sleep 300.$$"
{ printf 'GET http://127.0.0.1:@PORT@/big?n=%s HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n' "$N"; $SL; } \
  | timeout 75 nc $H $P > $O 2>/dev/null &
BG=$!
i=0; SZ=0
while [ $i -lt $LIM ]; do
  sleep 0.05
  SZ=$(wc -c < $O 2>/dev/null)
  [ -z "$SZ" ] && SZ=0
  [ "$SZ" -ge "$EXP" ] && break
  i=$((i+1))
done
E=$(now)
kill $BG 2>/dev/null
pkill -f "$SL" 2>/dev/null
echo "BYTES $SZ $((E-S)) $EXP"
""".replace("@PORT@", str(SRV_PORT))


def rec(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""),
          flush=True)


def sh(cmd, t=90):
    p = subprocess.run([ADB, "shell", cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", timeout=t)
    return p.stdout.strip()


def push_script():
    with open("_wn_tun.sh", "w", encoding="utf-8", newline="\n") as f:
        f.write(TUN_SH)
    subprocess.run([ADB, "push", "_wn_tun.sh", PHONE_SH],
                   capture_output=True, timeout=30)
    sh("chmod 755 %s" % PHONE_SH, t=15)
    # 上行探针（POST 大 body 到 9130，靠服务端响应判定完成）
    subprocess.run([ADB, "push", "_wn_up.sh", PHONE_UP],
                   capture_output=True, timeout=30)
    sh("chmod 755 %s" % PHONE_UP, t=15)


def api(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(CTRL + path, data=body,
                                 method=("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": str(e)}


def set_scene(key, enable=True):
    api("/api/preset", {"key": key})
    api("/api/run", {"enable": enable})


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        n = 0
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if kv.startswith("n="):
                    n = int(kv[2:] or 0)
        body = b"z" * max(0, min(n, 8 * 1024 * 1024))
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def probe(host, port, nbytes=0):
    """在手机上跑探针，返回 (bytes, ms)。"""
    txt = sh("%s %s %d %d" % (PHONE_SH, host, port, nbytes), t=90)
    for line in txt.splitlines():
        p = line.split()
        if len(p) >= 3 and p[0] == "BYTES":
            return int(p[1]), int(p[2])
    return None, None


def main():
    try:
        srv = S(("127.0.0.1", SRV_PORT), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    except OSError:
        print("复用已在运行的 127.0.0.1:%d 服务" % SRV_PORT, flush=True)
    subprocess.run([ADB, "reverse", "tcp:%d" % SRV_PORT, "tcp:%d" % SRV_PORT],
                   capture_output=True, timeout=20)
    # 9130 是上行测试的 POST 目标（回显服务）
    subprocess.run([ADB, "reverse", "tcp:9130", "tcp:9130"],
                   capture_output=True, timeout=20)
    push_script()
    print("PC 服务 127.0.0.1:%d + adb reverse 就绪" % SRV_PORT, flush=True)

    st = api("/api/status")
    rec("控制接口可达", "error" not in st, "vpn_active=%s" % st.get("vpn_active"))

    # 回环自检：直连档下两条路都应通
    set_scene("direct")
    b1, t1 = probe("127.0.0.1", 8888, 0)
    rec("对照组：127.0.0.1:8888 直连代理可达", b1 and b1 > 0, "%s B / %s ms" % (b1, t1))
    b2, t2 = probe("10.8.0.2", 8888, 0)
    rec("实验组：10.8.0.2:8888 过 tun0 可达", b2 and b2 > 0, "%s B / %s ms" % (b2, t2))
    if not (b1 and b2):
        print("    回环不通，终止")
        return 1

    # tun0 地址是否真的是 tun 设备
    tun_addr = sh("ip -o addr show tun0 2>/dev/null | grep -o 'inet [0-9.]*'").strip()
    rec("tun0 地址确认为 10.8.0.2", "10.8.0.2" in tun_addr, tun_addr or "(取不到)")

    base_tun = t2
    print("    tun 路径基线 %d ms（直连档）" % base_tun, flush=True)

    # ---- 延迟注入：卫星 1400ms RTT ----
    set_scene("satellite")
    b, t = probe("10.8.0.2", 8888, 0)
    inj = max(0, t - base_tun)
    rec("tun 路径 卫星档注入延迟", inj >= 1400 * 0.6,
        "往返 %d ms，注入 %d ms（设定 1400）" % (t, inj))

    # ---- 延迟注入：高延迟 800ms ----
    set_scene("high_latency")
    b, t = probe("10.8.0.2", 8888, 0)
    inj = max(0, t - base_tun)
    rec("tun 路径 高延迟档注入延迟", inj >= 800 * 0.6,
        "往返 %d ms，注入 %d ms（设定 800）" % (t, inj))

    # ---- 限速：4g 20000 kbps ----
    # 样本必须显著大于令牌桶容量，否则测的是突发不是稳态：
    # 桶上限 = rate = kbps*128 字节/秒，4g 档即 2.56MB；2MB 样本能整个塞进
    # 空闲时已攒满的桶里瞬间放行（实测 290ms 收完 2MB ≈ 57Mbps 假象）。
    set_scene("4g")
    b, t = probe("10.8.0.2", 8888, 8 * 1024 * 1024)
    kbps = b * 8 / 1024 / (t / 1000.0) if t else 0
    rec("tun 路径 4g 20000kbps 限速", b >= 8 * 1024 * 1024 and 0.5 * 20000 <= kbps <= 1.35 * 20000,
        "%.0f kbps（设定 20000）%d B / %d ms" % (kbps, b, t))

    # ---- 限速：拥塞 512 kbps（大样本避开桶突发）----
    set_scene("congestion")
    b, t = probe("10.8.0.2", 8888, 512 * 1024)
    kbps = b * 8 / 1024 / (t / 1000.0) if t else 0
    rec("tun 路径 拥塞 512kbps 限速", b >= 512 * 1024 and 0.5 * 512 <= kbps <= 1.5 * 512,
        "%.0f kbps（设定 512）%d B / %d ms" % (kbps, b, t))

    # ---- 上行限速：256 kbps（512KB POST，理论 ~16s）----
    # 上行方向与下行是两条独立通路（VpnNetService 读 tun0 后写上游），必须单独验。
    api("/api/profile", {"name": "tun上行256", "up_kbps": 256})
    api("/api/run", {"enable": True})
    time.sleep(0.5)
    up = sh("sh %s 10.8.0.2 8888 524288" % PHONE_UP, t=220)
    parts = up.split()
    if len(parts) >= 3 and parts[0] == "UP":
        got, ms = int(parts[1]), int(parts[2])
        kbps = 524288 * 8 / 1024 / (ms / 1000.0) if ms else 0
        rec("tun 路径 上行限速 256kbps", got > 0 and 0.5 * 256 <= kbps <= 1.6 * 256,
            "%.0f kbps（设定 256）512KB / %d ms" % (kbps, ms))
    else:
        rec("tun 路径 上行限速 256kbps", False, "探针输出异常：%r" % up[:80])

    # ---- 会话记账：tun 路径确实建了 TcpSession ----
    s = (api("/api/status").get("stats") or {})
    rec("tun 路径产生连接记账", (s.get("total", 0) or 0) > 0,
        "total=%s active=%s up=%s down=%s"
        % (s.get("total"), s.get("active"), s.get("up"), s.get("down")))

    # ---- 切场景立即生效 ----
    set_scene("congestion")
    r = {}

    def worker():
        r["x"] = probe("10.8.0.2", 8888, 4 * 1024 * 1024)

    th = threading.Thread(target=worker)
    th.start()
    time.sleep(2.5)
    t0 = time.time()
    set_scene("direct")
    th.join(timeout=180)
    sw = time.time() - t0
    b, t = r.get("x", (None, None))
    rec("tun 路径 限速中切直连立即放行", b >= 4 * 1024 * 1024 and sw < 8.0,
        "切换后 %.2fs 收完 4MB（512kbps 下需 ~64s）bytes=%s" % (sw, b))

    # ---- 停止限速 ----
    set_scene("congestion")
    api("/api/run", {"enable": False})
    b, t = probe("10.8.0.2", 8888, 1024 * 1024)
    rec("tun 路径 停止后恢复直连", b >= 1024 * 1024 and t < 4000,
        "1MB 耗时 %s ms" % t)

    # ---- 完全断网：airplane 档应连不上 ----
    # reject_pct=100 的语义是「立即拒连」：代理拒绝建上游连接，回 502 后关闭。
    # 客户端拿不到任何正常内容即为生效，不能要求字节数为 0（502 响应头本身就有 66B）。
    set_scene("airplane")
    b, t = probe("10.8.0.2", 8888, 0)
    rec("tun 路径 完全断网档拒连", not b or b < 200,
        "bytes=%s（502 拒连响应 66B / 无响应 0B 均算生效）" % b)
    api("/api/run", {"enable": False})

    try:
        srv.shutdown()
    except Exception:
        pass
    bad = [x for x in RESULTS if not x[1]]
    print("\n===== 真机 tun 数据面：%d/%d 通过 ====="
          % (len(RESULTS) - len(bad), len(RESULTS)))
    for n, _o, d in bad:
        print("FAILED: %s | %s" % (n, d))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
