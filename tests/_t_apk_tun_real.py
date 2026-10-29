# -*- coding: utf-8 -*-
"""真机 tun 数据面（VpnNetService -> TcpSession）实测。

为什么不能用 10.8.0.2:8888 探针：
  10.8.0.2 是 tun0 自己的地址，内核在 local 表就把连接交付给本机，
  根本不会进 tun0。1.1.8 之前代理绑 0.0.0.0，这条探针实际打到的是
  ProxyServer（代理路径），却被当成 tun 路径统计；1.1.9 起代理只绑回环
  （P0-2 安全加固），连 local 地址也被内核按「回环之外无监听」拒绝。

所以本脚本改用一条**非本机**目的地址（公网 HTTP 镜像）压在 tun 上：
  手机 nc -> tun0 -> VpnNetService -> TcpSession（protected socket）
    -> 真实 wlan -> 镜像站 -> 原路返回并注入弱网参数
对照组关掉 VPN（/api/vpn enable=false），同一 URL 直接走 wlan。

约束：
  * 手机与 PC 不在同一三层网络，PC 上的服务手机到不了，只能借公网镜像；
    因此吞吐容差放宽到 0.55~1.35x，完整性/延迟注入/拒连/黑洞仍按严格口径判定。
  * 镜像对 UA 有 ACL，必须带 User-Agent；用 Range 精确锁定 N 字节，
    正文长度 = Content-Length，与直连结果逐字节比对。
  * 选 37MB 的 ls-lR.gz 而不是 Release（后者只有 269KB，Range 超过文件大小时
    服务端回 200 整个文件，1MB 限速样本会被"提前收满"误判成限速不足）。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ADB = os.environ.get("WEAKNET_ADB") or r"C:/Program Files (x86)/Android/android-sdk/platform-tools/adb.exe"
CTRL = "http://127.0.0.1:18899"
PHONE_SH = "/data/local/tmp/wn_real.sh"
URL_PATH = "/ubuntu/ls-lR.gz"
HOST = "mirrors.tuna.tsinghua.edu.cn"
PORT = 80
R = []

PROBE = r"""#!/system/bin/sh
# 用法: wn_real.sh <host> <port> <nbytes> [tag]
# 输出: BYTES <总字节> <起始ms> <结束ms> <首字节ms(0=没收到)>
H=$1; P=$2; N=${3:-262144}; TAG=${4:-0}
O=/data/local/tmp/wn_real_${TAG}.bin
rm -f $O
now() { read U _ < /proc/uptime; echo "$U" | awk -F. '{printf "%d", $1*1000 + substr($2 "00", 1, 3)}'; }
S=$(now)
# stdin 用带 PID 的 sleep 撑住：裸 sleep 会被并发探针的 pkill 误杀
SL="sleep 300.$$"
{ printf 'GET @PATH@ HTTP/1.0\r\nHost: @HOST@\r\nUser-Agent: curl/8.0\r\nRange: bytes=0-%s\r\n\r\n' "$((N-1))"; $SL; } \
  | timeout 120 nc $H $P > $O 2>/dev/null &
BG=$!
i=0; SZ=0; TF=0; TN=0; last=0; stable=0
while [ $i -lt 2400 ]; do
  sleep 0.05
  SZ=$(wc -c < $O 2>/dev/null); [ -z "$SZ" ] && SZ=0
  if [ "$SZ" -gt 0 ] && [ "$TF" = 0 ]; then TF=$(now); fi
  # 没收到任何字节时别干等：连接被 RST / 黑洞时 12s 无数据即判定结束
  if [ "$SZ" = 0 ] && [ $i -ge 240 ]; then break; fi
  if [ "$SZ" -ge "$N" ]; then
    if [ "$TN" = 0 ]; then TN=$(now); fi
    if [ "$SZ" -eq "$last" ]; then stable=$((stable+1)); else stable=0; fi
    [ "$stable" -ge 10 ] && break
  fi
  # nc 自己退出（RST/建连失败）就别再等
  kill -0 $BG 2>/dev/null || break
  last=$SZ; i=$((i+1))
done
E=$(now)
kill $BG 2>/dev/null
pkill -f "$SL" 2>/dev/null
echo "BYTES $SZ $S $E $TF $TN"
""".replace("@PATH@", URL_PATH).replace("@HOST@", HOST)


def sh(cmd, t=180):
    p = subprocess.run([ADB, "shell", cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", timeout=t)
    return (p.stdout or "").strip()


def api(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(CTRL + path, data=body,
                                 method=("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": str(e)}


def rec(name, ok, detail=""):
    R.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""), flush=True)


def push_probe():
    # 直接 stdin 落盘，避开 Git Bash 对 /data/... 的路径改写
    p = subprocess.run([ADB, "shell", "cat > %s" % PHONE_SH], input=PROBE.encode(),
                       capture_output=True, timeout=30)
    sh("chmod 755 %s" % PHONE_SH, t=15)
    return p.returncode == 0


def probe(nbytes=262144, tag=0, t=180):
    """返回 (总字节, 收满耗时ms, 首字节ms, 墙钟ms)；失败返回 (None,)*4。"""
    txt = sh("sh %s %s %d %d %d" % (PHONE_SH, HOST, PORT, nbytes, tag), t=t)
    for line in txt.splitlines():
        p = line.split()
        if len(p) >= 6 and p[0] == "BYTES":
            sz, s0, e0, tf, tn = (int(p[1]), int(p[2]), int(p[3]),
                                  int(p[4]), int(p[5]))
            done = (tn - s0) if tn else (e0 - s0)
            return sz, done, (tf - s0 if tf else 0), (e0 - s0)
    return None, None, None, None


def pull_body(tag=0, nbytes=262144):
    """把探针输出拉回 PC，拆掉响应头，返回 (正文, 正文sha256, 头长)。"""
    local = os.path.join(HERE, "_tun_body_%d.bin" % tag)
    subprocess.run([ADB, "pull", "/data/local/tmp/wn_real_%d.bin" % tag, local],
                   capture_output=True, timeout=60)
    if not os.path.exists(local):
        return None, None, 0
    with open(local, "rb") as f:
        raw = f.read()
    idx = raw.find(b"\r\n\r\n")
    body = raw[idx + 4:] if idx >= 0 else b""
    os.remove(local)
    return body, hashlib.sha256(body).hexdigest(), (idx + 4 if idx >= 0 else 0)


def scene(key):
    api("/api/preset", {"key": key})
    api("/api/run", {"enable": True})


def vpn(on):
    api("/api/vpn", {"enable": bool(on)})
    time.sleep(2.0)


def main():
    if not push_probe():
        print("探针推送失败", flush=True)
        return 1
    st = api("/api/status")
    if "error" in st:
        print("控制接口不可达：%s" % st.get("error"), flush=True)
        return 1
    rec("控制接口可达", True, "vpn_active=%s" % st.get("vpn_active"))

    # ---- T1 对照组：关 VPN，直连镜像 ----
    vpn(False)
    s0 = (api("/api/status").get("stats") or {})
    b0, t0, f0, w0 = probe(262144, tag=1)
    body0, sha0, hlen = pull_body(1, 262144)
    rec("直连基线收到完整响应", b0 and body0 is not None and len(body0) == 262144,
        "%s B（正文 %s，头 %d）%s ms 首字节 %s ms"
        % (b0, len(body0) if body0 is not None else "-", hlen, t0, f0))
    if not body0 or len(body0) != 262144:
        print("基线不完整，终止", flush=True)
        return 1

    # ---- T2 开 VPN 直连档：透明转发完整性 ----
    vpn(True)
    st = api("/api/status")
    rec("VPN 已建立", st.get("vpn_active") is True, "vpn_active=%s" % st.get("vpn_active"))
    scene("direct")
    s1 = (api("/api/status").get("stats") or {})
    b1, t1, f1, w1 = probe(262144, tag=2)
    body1, sha1, _ = pull_body(2, 262144)
    rec("tun 透明转发正文逐字节一致", body1 is not None and len(body1) == 262144 and sha1 == sha0,
        "%s B / sha %s… vs %s…" % (len(body1) if body1 else 0, (sha1 or "")[:12], sha0[:12]))
    s2 = (api("/api/status").get("stats") or {})
    rec("tun 路径计入会话与流量", (s2.get("total", 0) or 0) > (s1.get("total", 0) or 0),
        "total %s -> %s，down %s -> %s"
        % (s1.get("total"), s2.get("total"), s1.get("down"), s2.get("down")))

    # ---- T3 限速 4000kbps（1MB 样本，避开 0.25s 桶突发）----
    api("/api/profile", {"name": "tun限速4000", "down_kbps": 4000})
    api("/api/run", {"enable": True})
    time.sleep(0.5)
    b2, t2, _f2, _w2 = probe(1024 * 1024, tag=3)
    body2, sha2, _ = pull_body(3, 1024 * 1024)
    kbps = len(body2) * 8 / 1024 / (t2 / 1000.0) if (body2 and t2) else 0
    rec("tun 限速 4000kbps（0.55~1.35x）",
        body2 is not None and len(body2) == 1024 * 1024 and 0.55 * 4000 <= kbps <= 1.35 * 4000,
        "%.0f kbps（设定 4000）%d B / %.2fs" % (kbps, len(body2) if body2 else 0, (t2 or 0) / 1000.0))

    # ---- T4 延迟 400+400ms：首字节显著后移 ----
    api("/api/profile", {"name": "tun延迟800", "latency_up_ms": 400, "latency_down_ms": 400})
    api("/api/run", {"enable": True})
    time.sleep(0.5)
    b3, t3, f3, w3 = probe(65536, tag=4)
    rec("tun 注入 800ms RTT（首字节 ≥0.6s）", b3 and f3 >= 600,
        "首字节 %d ms（直连基线 %s ms），总 %d ms" % (f3 or 0, f0, t3 or 0))

    # ---- T5 5% 丢包：正文仍完整 ----
    api("/api/profile", {"name": "tun丢包5", "loss_pct": 5})
    api("/api/run", {"enable": True})
    time.sleep(0.5)
    b4, t4, _f4, _w4 = probe(262144, tag=5)
    body4, sha4, _ = pull_body(5, 262144)
    rec("tun 5% 丢包正文完整", body4 is not None and len(body4) == 262144,
        "%s B / %.2fs" % (len(body4) if body4 else 0, (t4 or 0) / 1000.0))

    # ---- T6 100% 拒连：应快速失败 ----
    api("/api/profile", {"name": "tun拒连", "reject_pct": 100})
    api("/api/run", {"enable": True})
    time.sleep(0.5)
    b5, t5, f5, w5 = probe(65536, tag=6, t=40)
    rec("tun 100% 拒连快速失败", (b5 or 0) == 0 and (t5 or 99999) < 10000,
        "%s B / %s ms" % (b5, t5))

    # ---- T7 100% 黑洞：无数据、不崩溃 ----
    api("/api/profile", {"name": "tun黑洞", "timeout_pct": 100})
    api("/api/run", {"enable": True})
    time.sleep(0.5)
    b6, t6, f6, w6 = probe(65536, tag=7, t=40)
    st = api("/api/status")
    rec("tun 100% 黑洞无数据且进程存活", (b6 or 0) == 0 and "error" not in st,
        "%s B / %s ms，vpn_active=%s" % (b6, t6, st.get("vpn_active")))

    # ---- T8 回直连：恢复可用 ----
    scene("direct")
    b7, t7, _f7, _w7 = probe(262144, tag=8)
    body7, sha7, _ = pull_body(8, 262144)
    rec("撤掉弱网后恢复且正文一致", body7 is not None and sha7 == sha0,
        "%s B / %s ms" % (len(body7) if body7 else 0, t7))
    api("/api/run", {"enable": False})

    bad = [x for x in R if not x[1]]
    print("\n===== tun 数据面实测：%d/%d 通过 =====" % (len(R) - len(bad), len(R)), flush=True)
    for n, _o, d in bad:
        print("FAILED: %s | %s" % (n, d), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
