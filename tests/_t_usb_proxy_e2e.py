#!/usr/bin/env python3
"""USB 代理路径（任务 #5）：手机 -> adb reverse -> PC Windows 引擎(8888) -> PC HTTP 服务(9129)。

踩坑记录（都踩过，改这里前先读）：
1. curl 控制引擎必须用 env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY
   清掉沙箱代理变量。不能用 --noproxy '*'：它会连 -x 一起废掉，curl 直连目标，
   请求根本不过引擎，测了个寂寞。
2. 切场景只是「选中」：POST /api/preset 或 /api/profile 之后必须 POST
   /api/run {"enable":true} 开启限速，否则 G.profile 保持直连，弱网参数不施加
   （与 GUI 同规矩：没开启限速时不施加）。
3. 手机侧命令必须经 subprocess 参数列表传给 adb.exe；请求换行写 "\\r\\n"
   （python 字符串得到字面四字符，设备 printf 才转 CRLF）；写成 "\r\n" 会产出
   真实 CRLF 塞进 adb.exe 参数，命令被折断，手机静默无输出。
4. 经 adb reverse 的 nc 必须保持 stdin 打开（(printf ...; sleep N) | nc）：
   reverse 连接建立极快，printf|nc 的 stdin EOF 竞态会丢待发数据。
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
import subprocess
import sys
import time

ADB = r"C:/Program Files (x86)/Android/android-sdk/platform-tools/adb.exe"

# 清掉沙箱代理变量的 curl 环境
CURL_ENV = dict(os.environ)
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    CURL_ENV.pop(k, None)

passed, failed = 0, 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  [PASS] %s %s" % (name, detail), flush=True)
    else:
        failed += 1
        print("  [FAIL] %s %s" % (name, detail), flush=True)


def phone(cmd, timeout=60):
    p = subprocess.run([ADB, "shell", cmd], capture_output=True, text=True, timeout=timeout)
    return (p.stdout or "") + (p.stderr or "")


def curl(args, timeout=20):
    p = subprocess.run(["curl", "-s", "-m", "10"] + args,
                       capture_output=True, text=True, timeout=timeout, env=CURL_ENV)
    return p.stdout


def ctrl(path, body=None):
    if body is None:
        out = curl([curl_url(path)])
    else:
        out = curl(["-X", "POST", curl_url(path), "-H", "Content-Type: application/json",
                    "-d", json.dumps(body)])
    try:
        return json.loads(out)
    except Exception:
        return {"_raw": out}


def curl_url(path):
    return "http://127.0.0.1:8899" + path


def enable_limit():
    """开启限速总开关，否则 preset/profile 只是选中不施加"""
    r = ctrl("/api/run", {"enable": True})
    return r.get("limit_on") is True


# 请求模板：\\r\\n 在 python 字符串里是字面四字符，交给设备 printf 转 CRLF
REQ = ("printf 'GET http://127.0.0.1:9129/ HTTP/1.1\\r\\n"
       "Host: 127.0.0.1:9129\\r\\n"
       "Connection: close\\r\\n\\r\\n'; sleep 2")


print("== P0 开启限速总开关 ==", flush=True)
check("P0 limit_on=true", enable_limit())

print("== P1 reverse 隧道连通性（垃圾输入，代理必须应答）==", flush=True)
out = phone("(printf 'GARBAGE\\r\\n\\r\\n'; sleep 2) | timeout 8 nc 127.0.0.1 18888; echo EXIT=$?")
check("P1 隧道连通（有应答）", "EXIT=0" in out, repr(out[:100]))

print("== P2 显式 HTTP 代理请求经引擎转发到 9129 ==", flush=True)
out = phone("(%s) | timeout 15 nc 127.0.0.1 18888" % REQ)
check("P2 代理转发拿到底站内容", "HTTP-OK-PROXY-PATH" in out, repr(out[:160]))

print("== P3 引擎 stats 记录本次代理连接 ==", flush=True)
st = ctrl("/api/status")
total = st.get("stats", {}).get("total", 0)
check("P3 total 计数增长（当前 %s）" % total, total >= 1, "total=%s" % total)

print("== P4 限速场景（down_kbps=200）代理路径仍可用 ==", flush=True)
ctrl("/api/profile", {"name": "代理限速200k", "latency_up_ms": 0, "latency_down_ms": 0,
                      "jitter_ms": 0, "down_kbps": 200, "up_kbps": 0, "loss_pct": 0,
                      "stall_ms": 0, "disconnect_pct": 0, "timeout_pct": 0,
                      "dns_fail_pct": 0, "reject_pct": 0, "flap_sec": 0, "flap_blackout_sec": 0})
enable_limit()
time.sleep(0.5)
out2 = phone("(%s) | timeout 20 nc 127.0.0.1 18888" % REQ)
check("P4 限速下代理路径仍可用", "HTTP-OK-PROXY-PATH" in out2)

print("== P5 延迟注入（latency_down_ms=1500）==", flush=True)
ctrl("/api/profile", {"name": "代理延迟1.5s", "latency_up_ms": 0, "latency_down_ms": 1500,
                      "jitter_ms": 0, "down_kbps": 0, "up_kbps": 0, "loss_pct": 0,
                      "stall_ms": 0, "disconnect_pct": 0, "timeout_pct": 0,
                      "dns_fail_pct": 0, "reject_pct": 0, "flap_sec": 0, "flap_blackout_sec": 0})
enable_limit()
time.sleep(0.5)
t0 = time.time()
out = phone("time ((%s) | timeout 25 nc 127.0.0.1 18888 > /dev/null)" % REQ)
wall = time.time() - t0
m = re.search(r"real\s+(\d+)m([\d.]+)s", out)
dt = (int(m.group(1)) * 60 + float(m.group(2))) if m else -1
# 响应分两块（头+体）各吃一次 1500ms 下行延迟，实测约 3s；墙钟扣掉固定开销
check("P5 延迟注入生效（1500ms×2 块，实测 %.2fs / 墙钟 %.1fs）" % (dt, wall),
      dt >= 2.0 or wall >= 2.0, "elapsed=%.2fs" % dt)

print("== P6 恢复 direct（无附加延迟）==", flush=True)
ctrl("/api/preset", {"key": "direct"})
enable_limit()
time.sleep(0.5)
t0 = time.time()
out = phone("time ((%s) | timeout 25 nc 127.0.0.1 18888 > /dev/null)" % REQ)
wall = time.time() - t0
m = re.search(r"real\s+(\d+)m([\d.]+)s", out)
dt = (int(m.group(1)) * 60 + float(m.group(2))) if m else -1
check("P6 direct 场景恢复正常（实测 %.2fs）" % dt, dt < 2.0 and wall < 6.0, "elapsed=%.2fs" % dt)

print()
print("== USB 代理路径结果: %d 通过 / %d 失败 ==" % (passed, failed), flush=True)
sys.exit(1 if failed else 0)
