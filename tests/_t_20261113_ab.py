# -*- coding: utf-8 -*-
"""2026-11-13 A/B 取证：同一台真机上「旧包 vs 新包」的四项硬指标。

补的是核对矩阵里 4 个 ⚠️ 中三个的量化空缺：

  Android 侧 VPN 开/关对照（关掉时同一 URL 直连，作为链路本底）
  §5   Android 16 MB 直连吞吐不降 + cpu_ms/MB
  10 并发连接时的线程数（总数 + 按线程名分组）

为什么必须在装新包之前先跑一遍
------------------------------
设备上现装的是 11-06 的 v1.1.16 旧包（versionCode 126），它就是「改动前」。
一旦 adb install -r 覆盖掉，这份基线就再也没有了 —— 所以本脚本带 `--tag`，
要求先后各跑一次，两次的 --tag 不同，结果各写一份 JSON。

测量口径
--------
* 吞吐：设备侧 nc 拉公网镜像的定长 Range 段（37 MB 的 ls-lR.gz），
  按「收满 N 字节所用毫秒」算 kbps（1024 进制，与项目其它门禁一致）。
  手机与 PC 不在同一三层网络，只能借公网镜像，因此容差按 ±35% 看趋势，
  逐字节完整性另由 sha256 判。
* CPU：/proc/<pid>/stat 的 utime+stime（CLK_TCK=100 → 1 tick = 10 ms），
  取传输前后的差值，除以本次传输的 MB 数。关 VPN 时该值≈0，正是
  「流量有没有真的过 App」的旁证。
* 线程：/proc/<pid>/status 的 Threads；峰值同时 dump `ps -T` 用于按名字分组。
  采样在设备内用 shell 内建 read 完成（不 fork），避免旧 measure_dev.py
  那种「每次采样一次 adb 往返、把被测设备自己拖慢」的错误。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _t_apk_tun_real as T          # 复用它的探针推送 / probe / api / sh

ADB = T.ADB
PKG = "com.weaknet.app"
HOST, PORT, PATH_ = T.HOST, T.PORT, T.URL_PATH

CONC_SH = r"""#!/system/bin/sh
# 用法: wn_conc.sh <host> <port> <nbytes> <nconn> <tag>
# 输出: CONC <总字节> <峰值线程数> <非空连接数>
H=$1; P=$2; N=${3:-1048576}; C=${4:-10}; TAG=${5:-0}
PID=$(pidof @PKG@)
O=/data/local/tmp/wn_conc_${TAG}
rm -rf $O; mkdir -p $O
PIDS=""
i=0
while [ $i -lt $C ]; do
  SL="sleep 120.$$.$i"
  { printf 'GET @PATH@ HTTP/1.0\r\nHost: @HOST@\r\nUser-Agent: curl/8.0\r\nRange: bytes=0-%s\r\n\r\n' "$((N-1))"; $SL; } \
    | timeout 120 nc $H $P > $O/$i.bin 2>/dev/null &
  PIDS="$PIDS $!"
  i=$((i+1))
done
MAX=0; i=0
while [ $i -lt 90 ]; do
  T=0
  while read k v; do
    [ "$k" = "Threads:" ] && T="$v"
  done < /proc/$PID/status
  if [ "${T:-0}" -gt "$MAX" ]; then
    MAX=$T
    ps -T -p $PID > /data/local/tmp/wn_thr_${TAG}.txt 2>/dev/null
  fi
  sleep 0.1
  i=$((i+1))
done
kill $PIDS 2>/dev/null
pkill -f "sleep 120.$$" 2>/dev/null
NE=0
for f in $O/*.bin; do
  [ -s "$f" ] && NE=$((NE+1))
done
SZ=$(cat $O/*.bin 2>/dev/null | wc -c)
echo "CONC $SZ $MAX $NE"
""".replace("@PKG@", PKG).replace("@PATH@", PATH_).replace("@HOST@", HOST)


def push(script, remote):
    p = subprocess.run([ADB, "shell", "cat > %s" % remote], input=script.encode(),
                       capture_output=True, timeout=30)
    T.sh("chmod 755 %s" % remote, t=15)
    return p.returncode == 0


def pid():
    out = T.sh("pidof %s" % PKG) or ""
    m = re.search(r"(\d+)", out)
    return m.group(1) if m else "0"


def cpu_ticks():
    out = T.sh("cat /proc/%s/stat 2>/dev/null" % pid())
    if not out or ")" not in out:
        return None
    parts = out.rsplit(")", 1)[-1].split()
    try:
        return int(parts[11]) + int(parts[12])
    except Exception:
        return None


def stats():
    return (T.api("/api/status").get("stats") or {})


def _active():
    return bool(T.api("/api/status").get("vpn_active"))


def _wait_active(want, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _active() == want:
            return True
        time.sleep(0.5)
    return False


def set_vpn(on):
    """开/关 VPN，带轮询与界面兜底。

    为什么不能只发一次 /api/vpn 再睡 2 秒：
      1. 11-06 那版包里**只有界面按钮**会调 VpnService.prepare()，控制接口不调，
         于是 establish() 一律返回 null（日志 "VPN 未获得授权"）。
      2. 更糟的是已经起来时再发一次 enable：CtrlServer 用 startForegroundService()
         拉起服务，而 running 已经是 true，代码走不到 startForeground()，
         5 秒后系统按「foregroundServiceDidNotStartInTime」直接杀进程。
         所以开之前先查状态，已经是开就什么都不做。
    新包里这两条都修了（startVpn() 先 prepare()、startVpnGuarded() 先进前台），
    /api/vpn 可直接用；旧包则靠界面点「开始弱网测试」兜底。
    两条路都真实把 VPN 建起来，前后两次测量的操作路径才可比。
    """
    cur = _active()
    if on and cur:
        return True
    if not on and not cur:
        return True

    T.api("/api/vpn", {"enable": bool(on)})
    if _wait_active(on, 5.0):
        return True
    if not on:
        return _active()

    # 兜底：唤醒屏幕 -> 前台 -> 点「开始弱网测试」（prepare -> 授权 -> 起服务）
    T.sh("input keyevent KEYCODE_WAKEUP")
    T.sh("wm dismiss-keyguard")
    try:
        import _t_dev_lib as D
        D.ensure_foreground()
        D.tap_text("开始弱网测试", wait=1.0)
    except Exception as e:
        print("  界面点击失败：%r" % (e,), flush=True)
    return _wait_active(True, 20.0)


def reset():
    """回直连、关限速：任何残留的自定义参数都会污染吞吐。"""
    T.api("/api/preset", {"key": "direct"})
    T.api("/api/run", {"enable": False})
    time.sleep(0.8)


def one_transfer(nbytes, tag):
    """一次定长传输，返回 dict（不含判定）。"""
    s0 = stats()
    c0 = cpu_ticks()
    t0 = time.time()
    sz, done_ms, first_ms, wall_ms = T.probe(nbytes, tag=tag, t=240)
    wall = time.time() - t0
    c1 = cpu_ticks()
    s1 = stats()
    body, sha, _hlen = T.pull_body(tag, nbytes)
    got = len(body) if body else 0
    kbps = (got * 8 / 1024.0) / (done_ms / 1000.0) if got and done_ms else 0.0
    mb = got / 1024.0 / 1024.0
    cpu_ms = ((c1 - c0) * 10.0) if (c0 is not None and c1 is not None) else None
    return {
        "bytes_report": sz, "bytes_body": got, "sha256": sha,
        "done_ms": done_ms, "first_byte_ms": first_ms, "wall_ms": int(wall * 1000),
        "kbps": round(kbps, 1), "mb": round(mb, 3),
        "cpu_ticks": (c1 - c0) if (c0 is not None and c1 is not None) else None,
        "cpu_ms": cpu_ms,
        "cpu_ms_per_mb": round(cpu_ms / mb, 2) if (cpu_ms is not None and mb > 0.5) else None,
        "app_down_delta": (s1.get("down", 0) or 0) - (s0.get("down", 0) or 0),
        "app_total_delta": (s1.get("total", 0) or 0) - (s0.get("total", 0) or 0),
    }


def group_threads(tag):
    """把 ps -T 的线程名按前缀归类，用于回答「每连接多开了几个线程」。"""
    txt = T.sh("cat /data/local/tmp/wn_thr_%s.txt" % tag) or ""
    names = []
    for line in txt.splitlines()[1:]:
        p = line.split()
        if len(p) >= 2:
            names.append(p[-1])
    groups = {}
    for n in names:
        key = re.sub(r"[-_]?\d+$", "", n) or n
        groups[key] = groups.get(key, 0) + 1
    return {"total_from_ps": len(names), "by_name": groups}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="old / new（决定输出文件名与临时文件后缀）")
    ap.add_argument("--mb", type=int, default=16, help="单流传输量（MB）")
    ap.add_argument("--conn", type=int, default=10, help="并发连接数")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    nbytes = a.mb * 1024 * 1024
    out = a.out or os.path.join(HERE, "..", "_tmp", "ab_android_%s.json" % a.tag)
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    if not push(T.PROBE, T.PHONE_SH):
        print("探针推送失败", flush=True)
        return 2
    push(CONC_SH, "/data/local/tmp/wn_conc.sh")

    st = T.api("/api/status")
    if "error" in st:
        print("控制接口不可达：%s（先 adb forward tcp:18899 tcp:8899）" % st.get("error"))
        return 2

    res = {"tag": a.tag, "when": time.strftime("%Y-%m-%d %H:%M:%S"),
           "pkg_version": T.sh("dumpsys package %s | grep -m1 versionName" % PKG),
           "mb": a.mb, "conn": a.conn}

    try:
        # ---- 1. VPN 关：链路本底 ----
        reset()
        res["vpn_off"] = set_vpn(False)
        reset()
        print("[1/4] VPN 关，%d MB 直连…" % a.mb, flush=True)
        res["off"] = one_transfer(nbytes, tag=1)

        # ---- 2. VPN 开：经 tun 透明转发 ----
        res["vpn_on"] = set_vpn(True)
        reset()
        print("[2/4] VPN 开，%d MB 经 tun…" % a.mb, flush=True)
        res["on"] = one_transfer(nbytes, tag=2)

        # ---- 3. 10 并发：线程数 ----
        print("[3/4] %d 并发连接，采样线程数…" % a.conn, flush=True)
        T.api("/api/run", {"enable": False})
        s0 = stats()
        t0 = time.time()
        txt = T.sh("sh /data/local/tmp/wn_conc.sh %s %d %d %d %s"
                   % (HOST, PORT, 1024 * 1024, a.conn, 3), t=180)
        wall = time.time() - t0
        conc = {"raw": txt.strip().splitlines()[-1] if txt.strip() else ""}
        p = conc["raw"].split()
        if len(p) >= 4 and p[0] == "CONC":
            conc.update({"bytes": int(p[1]), "peak_threads": int(p[2]),
                         "nonempty_conns": int(p[3]),
                         "agg_kbps": round((int(p[1]) * 8 / 1024.0) / wall, 1),
                         "wall_ms": int(wall * 1000)})
        s1 = stats()
        conc["app_session_delta"] = (s1.get("total", 0) or 0) - (s0.get("total", 0) or 0)
        conc["threads"] = group_threads(3)
        conc["idle_threads"] = None
        time.sleep(1.5)
        idle = T.sh("cat /proc/%s/status | grep Threads" % pid())
        m = re.search(r"(\d+)", idle or "")
        conc["idle_threads"] = int(m.group(1)) if m else None
        res["concurrent"] = conc

        # ---- 4. 复位 ----
        reset()
        T.api("/api/vpn", {"enable": False})
        print("[4/4] 复位完成", flush=True)
    finally:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print("\n写入 %s" % out, flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
