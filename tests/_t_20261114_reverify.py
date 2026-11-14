# -*- coding: utf-8 -*-
"""2026-11-14 受控复测：判断「新修改」的收益是否超出测量噪声，并定位剩余优化空间。

为什么需要这一轮
----------------
11-13 那批真机 A/B 全是**单次采样**，而链路本底本身波动极大：
  * VPN 关、16 MB 直连：同一天测到 10.6 / 36.2 / 49.3 / 52.9 Mbps（4.6x）
  * cpu_ms/MB 三次连测：新包极差 16.2、旧包极差 44.4，而新旧中位差只有 6.7%
单次数据因此不能用来宣称「有提升」或「没提升」。本脚本用多轮重复 + 交替顺序，
让「效应」和「噪声」可分开度量。

四种模式（一次跑一种，输出 _tmp/reverify_<mode>_*.json）
-------------------------------------------------------
  cpu       单包 N 轮 16 MB（VPN 开 / direct），算中位数、极差、变异系数。
            由外层按「新 5 轮 → 旧 5 轮 → 新 3 轮」的 ABBA 顺序调用，
            第三段用于检测时间漂移；判定依据是中位数之差是否小于合并极差。
  threads   10 条空闲连接前后的**线程增量**（不是总线程数）。
            总线程里 70+ 是 JVM/Render/mali/binder 等系统线程，用「总数 < 40」
            当验收口径本身就是错的；真正该看的是每连接新增了几个。
  mtu       1500 / 16384 交替 3 轮（轮内顺序也交替），确认 16 KB 档是否真劣化。
  throttle  4000 kbps 档下「不预热 vs 先跑 2 MB 预热」对照，定位 1 MB 样本
            测出 1.575x 的偏差是令牌桶起始突发、TCP 慢启动，还是计时口径。
            **每次样本前必须重建并校验限速前提**：POST /api/run {enable:false}
            会把当前 profile 覆盖成直连（down=0），而 presetKey 仍是 custom，
            再 run true 时 limit_on=True 但限速其实没生效 —— 第一次实测整组作废。

不改产品代码，只测量。所有采样在设备内用 shell 内建 read 完成（不产生 adb 往返）。
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _t_apk_tun_real as T
import _t_20261113_ab as A

PKG = "com.weaknet.app"
HOST, PORT, PATH_ = T.HOST, T.PORT, T.URL_PATH
OUTDIR = os.path.abspath(os.path.join(HERE, "..", "_tmp"))
MB = 1024 * 1024

# 10 条只建连、不传大流量的连接；峰值采样 6 s，全过程在设备内完成。
HOLD_SH = r"""#!/system/bin/sh
# 用法: wn_rv_hold.sh <host> <port> <nconn> <tag>
H=$1; P=$2; C=${3:-10}; TAG=${4:-0}
PID=$(pidof @PKG@)
PIDS=""
i=0
while [ $i -lt $C ]; do
  { printf 'GET @PATH@ HTTP/1.0\r\nHost: @HOST@\r\nUser-Agent: curl/8.0\r\nRange: bytes=0-1023\r\n\r\n'; sleep 60; } \
    | timeout 60 nc $H $P > /dev/null 2>&1 &
  PIDS="$PIDS $!"
  i=$((i+1))
done
MAX=0; i=0
while [ $i -lt 60 ]; do
  N=0
  while read k v; do
    [ "$k" = "Threads:" ] && N="$v"
  done < /proc/$PID/status
  if [ "${N:-0}" -gt "$MAX" ]; then
    MAX=$N
    ps -T -p $PID > /data/local/tmp/rv_thr_${TAG}.txt 2>/dev/null
  fi
  sleep 0.1
  i=$((i+1))
done
kill $PIDS 2>/dev/null
echo "HOLD $MAX"
""".replace("@PKG@", PKG).replace("@PATH@", PATH_).replace("@HOST@", HOST)


def push_sh(script, remote):
    p = subprocess.run([T.ADB, "shell", "cat > %s" % remote], input=script.encode(),
                       capture_output=True, timeout=30)
    T.sh("chmod 755 %s" % remote, t=15)
    return p.returncode == 0


def threads_now():
    out = T.sh("cat /proc/%s/status | grep Threads" % A.pid())
    m = re.search(r"(\d+)", out or "")
    return int(m.group(1)) if m else None


def group_threads(tag):
    txt = T.sh("cat /data/local/tmp/rv_thr_%s.txt" % tag) or ""
    names = [ln.split()[-1] for ln in txt.splitlines()[1:] if ln.strip()]
    g = {}
    for n in names:
        k = re.sub(r"[-_]?\d+$", "", n) or n
        g[k] = g.get(k, 0) + 1
    return {"total_from_ps": len(names),
            "by_name": dict(sorted(g.items(), key=lambda x: (-x[1], x[0])))}


def dump(name, obj):
    os.makedirs(OUTDIR, exist_ok=True)
    p = os.path.join(OUTDIR, "reverify_%s.json" % name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print("写入 " + p, flush=True)


def spread(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return {}
    med = statistics.median(v)
    return {"n": len(v), "median": round(med, 2), "min": round(min(v), 2),
            "max": round(max(v), 2), "range": round(max(v) - min(v), 2),
            "cv_pct": round(100 * statistics.pstdev(v) / med, 1) if med else None}


def prepare():
    if not A.push(T.PROBE, T.PHONE_SH):
        raise RuntimeError("探针推送失败")
    st = T.api("/api/status")
    if "error" in st:
        raise RuntimeError("控制接口不可达：%s" % st["error"])
    A.reset()
    if not A.set_vpn(True):
        raise RuntimeError("VPN 未能开启")
    A.reset()                       # 只切 direct + 关限速，不动 VPN
    time.sleep(1.5)


def cleanup():
    A.reset()
    A.set_vpn(False)


def wait_active(want, timeout=20.0, mtu=None):
    """等待 VPN 达到目标状态。

    复测 §3.3：mtu 模式原来只等 vpn_active 再固定 sleep(1.2)——那是「靠时间
    猜」，rebuild 异步窗口下会产出前提错误的样本。现在改为轮询判据：
      want=True  → vpn_active 且 mtu_ready（effective_mtu == 静态档）
      want=False → not vpn_active（effective_mtu 归零）
    mtu 参数用于显式指定目标档（默认取 /api/vpn 响应里的静态档即可，这里
    直接读 status 的 mtu 字段，不另传）。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = T.api("/api/status")
        active = bool(st.get("vpn_active"))
        if want:
            if active and st.get("mtu_ready") and \
                    (mtu is None or st.get("mtu") == mtu):
                return True
        elif not active:
            return True
        time.sleep(0.4)
    return False


def mode_cpu(a):
    prepare()
    rows = []
    try:
        for i in range(a.rounds):
            r = A.one_transfer(a.mb * MB, tag=600 + i)
            rows.append(r)
            print("  #%d  kbps=%8.0f  cpu_ms/MB=%-7s sha=%s" %
                  (i + 1, r["kbps"], r["cpu_ms_per_mb"], (r["sha256"] or "")[:8]),
                  flush=True)
            time.sleep(1.0)
    finally:
        cleanup()
    obj = {"tag": a.tag, "build": a.build,
           "when": time.strftime("%Y-%m-%d %H:%M:%S"),
           "pkg_version": T.sh("dumpsys package %s | grep -m1 versionName" % PKG),
           "mb": a.mb, "mode": "VPN 开 / direct / 16MB", "rows": rows,
           "kbps": spread([r["kbps"] for r in rows]),
           "cpu_ms_per_mb": spread([r["cpu_ms_per_mb"] for r in rows]),
           "sha_unique": sorted(set(r["sha256"] for r in rows))}
    dump("cpu_%s" % a.tag, obj)
    return 0


def mode_threads(a):
    prepare()
    try:
        if not push_sh(HOLD_SH, "/data/local/tmp/wn_rv_hold.sh"):
            raise RuntimeError("hold 脚本推送失败")
        # 前提：前序轮次会留下 udp-recv / pool-N-thread 线程且不立刻回收。
        # 必须等基线稳定（连续 3 次极差 <=2）再取增量，否则 180 那种
        # 被污染的基线会让「+27」变成无意义的数字（11-14 第一次实测踩到过）。
        base_samples = []
        for _ in range(15):
            base_samples.append(threads_now())
            if len(base_samples) >= 3 and \
                    max(base_samples[-3:]) - min(base_samples[-3:]) <= 2:
                break
            time.sleep(1.0)
        i0 = statistics.median(base_samples[-3:])
        # 只看「最后 3 次极差小」会把 62→113 的平台期误判成稳态（第一次实测
        # 就踩到）；再加一条「整段不单调爬升」，否则前提不成立。
        stable = (max(base_samples[-3:]) - min(base_samples[-3:]) <= 2
                  and (base_samples[-1] - base_samples[0]) <= 5)
        if not stable or i0 > 100:
            print("  !! 基线不稳定/仍有残留（%s），本次增量标记 invalid" % base_samples,
                  flush=True)
        txt = T.sh("sh /data/local/tmp/wn_rv_hold.sh %s %d %d %s"
                   % (HOST, PORT, a.conn, a.tag), t=120)
        peak = None
        for ln in (txt or "").splitlines():
            p = ln.split()
            if len(p) >= 2 and p[0] == "HOLD":
                peak = int(p[1])
        time.sleep(2.0)
        i1 = threads_now()
        g = group_threads(a.tag)
    finally:
        cleanup()
    obj = {"tag": a.tag, "build": a.build,
           "when": time.strftime("%Y-%m-%d %H:%M:%S"),
           "conn": a.conn, "base_samples": base_samples, "base_stable": stable,
           "idle_before": i0, "peak": peak, "idle_after": i1,
           "delta_peak_vs_idle": (peak - i0) if (peak and i0) else None,
           "delta_after_vs_before": (i1 - i0) if (i1 and i0) else None,
           "valid": bool(stable and i0 <= 100 and peak),
           "threads": g}
    dump("threads_%s" % a.tag, obj)
    print(json.dumps({k: v for k, v in obj.items() if k != "threads"},
                     ensure_ascii=False, indent=2), flush=True)
    print("线程分组 top10: " +
          json.dumps(dict(list(g["by_name"].items())[:10]), ensure_ascii=False), flush=True)
    return 0


def mode_mtu(a):
    # 先确认控制接口与探针可用，然后按档位自行停/起 VPN
    prepare()
    cleanup()
    seq = []
    tag = 800
    try:
        for rnd in range(a.rounds):
            order = (1500, 16384) if rnd % 2 == 0 else (16384, 1500)
            for mtu in order:
                A.set_vpn(False)
                time.sleep(1.2)
                resp = T.api("/api/vpn", {"enable": True, "mtu": mtu})
                if not wait_active(True, 20.0):
                    seq.append({"round": rnd + 1, "mtu": mtu, "error": "VPN 未起来"})
                    continue
                A.reset()
                time.sleep(1.2)
                m = A.one_transfer(a.mb * MB, tag=tag)
                tag += 1
                seq.append({"round": rnd + 1, "mtu": mtu,
                            "resp_mtu": resp.get("mtu"), "resp_mss": resp.get("mss"),
                            "kbps": m["kbps"], "cpu_ms_per_mb": m["cpu_ms_per_mb"],
                            "sha256": (m["sha256"] or "")[:12],
                            "app_down_delta": m["app_down_delta"]})
                print("  轮%d mtu=%-5d mss=%-6s kbps=%8.0f cpu=%s" %
                      (rnd + 1, mtu, resp.get("mss"), m["kbps"], m["cpu_ms_per_mb"]),
                      flush=True)
        # 非法档位必须被拒，且不改变当前档
        bad = T.api("/api/vpn", {"enable": True, "mtu": 1501})
    finally:
        cleanup()
    by = {}
    for m in seq:
        if "kbps" in m:
            by.setdefault(m["mtu"], []).append(m["kbps"])
    obj = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "mb": a.mb,
           "rounds": a.rounds, "seq": seq,
           "kbps_1500": spread(by.get(1500, [])),
           "kbps_16384": spread(by.get(16384, [])),
           "reject_1501": bad}
    if by.get(1500) and by.get(16384):
        m15 = statistics.median(by[1500]); m16 = statistics.median(by[16384])
        obj["median_ratio_16384_over_1500"] = round(m16 / m15, 4) if m15 else None
    dump("mtu", obj)
    return 0


def mode_throttle(a):
    prepare()
    rows = []
    tag = 900
    rate = a.rate

    def ensure_limit():
        """重建并**校验**限速前提。

        11-14 第一次实测踩到：POST /api/run {enable:false} 会把当前 profile
        覆盖成「直连（不限制）」(down=0)，但 presetKey 仍是 custom；随后
        run true 时 buildKeyProfile("custom") 返回 null，代码就「原样保留」
        了这份已经被清空的直连档 —— limit_on=True 而限速其实没生效。
        所以每次样本前必须重新 setProfile，并读 /api/status 断言 down_kbps。
        """
        T.api("/api/run", {"enable": False})
        time.sleep(0.3)
        T.api("/api/profile", {"name": "rv%d" % rate,
                               "down_kbps": rate, "up_kbps": 0,
                               "latency_up_ms": 0, "latency_down_ms": 0,
                               "loss_pct": 0, "jitter_ms": 0})
        T.api("/api/run", {"enable": True})
        time.sleep(0.6)
        d = T.api("/api/status")
        return {"limit_on": d.get("limit_on"),
                "down_kbps": (d.get("profile") or {}).get("down_kbps")}

    try:
        def one(mb, warm_mb=0):
            nonlocal tag
            if warm_mb:
                T.probe(warm_mb * MB, tag=tag, t=300)
                T.pull_body(tag, warm_mb * MB)
                tag += 1
            sz, done_ms, first_ms, _w = T.probe(mb * MB, tag=tag, t=300)
            body, sha, _ = T.pull_body(tag, mb * MB)
            tag += 1
            got = len(body) if body else 0
            kbps = (got * 8 / 1024.0) / (done_ms / 1000.0) if got and done_ms else 0.0
            return {"kbps": round(kbps, 1), "ratio": round(kbps / rate, 3),
                    "done_ms": done_ms, "first_byte_ms": first_ms,
                    "bytes": got, "sha": (sha or "")[:8]}

        for phase, warm in (("cold", 0), ("warm", 2)):
            for i in range(a.rounds):
                pre = ensure_limit()          # 两组的区别只有「是否预热」
                ok = (pre.get("limit_on") is True and pre.get("down_kbps") == rate)
                r = one(1, warm_mb=warm) if ok else {"kbps": 0, "ratio": 0,
                                                    "done_ms": 0, "first_byte_ms": 0,
                                                    "bytes": 0, "sha": ""}
                r.update({"phase": phase, "round": i + 1, "warmup_mb": warm,
                          "precheck": pre, "valid": ok})
                rows.append(r)
                print("  %-5s #%d  pre=%s  1MB -> %6.0f kbps (%.3fx, %d ms) %s" %
                      (phase, i + 1, "OK" if ok else "BAD", r["kbps"], r["ratio"],
                       r["done_ms"], "" if ok else "<= 作废"), flush=True)
                time.sleep(0.5)
    finally:
        cleanup()
    okrows = [r for r in rows if r["valid"]]
    obj = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "rate_kbps": rate,
           "sample_mb": 1, "rows": rows, "valid_n": len(okrows),
           "cold_ratio": spread([r["ratio"] for r in okrows if r["phase"] == "cold"]),
           "warm_ratio": spread([r["ratio"] for r in okrows if r["phase"] == "warm"]),
           "note": "run:false 会清空当前 profile；每次样本前重建并校验 down_kbps"}
    dump("throttle", obj)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True,
                    choices=("cpu", "threads", "mtu", "throttle"))
    ap.add_argument("--tag", default="run", help="输出文件后缀 / 线程 dump 后缀")
    ap.add_argument("--build", default="", help="new / old，仅作标注")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--mb", type=int, default=16)
    ap.add_argument("--conn", type=int, default=10)
    ap.add_argument("--rate", type=int, default=4000)
    a = ap.parse_args()
    try:
        return {"cpu": mode_cpu, "threads": mode_threads,
                "mtu": mode_mtu, "throttle": mode_throttle}[a.mode](a)
    except Exception as e:
        print("复测失败：%r" % (e,), flush=True)
        try:
            cleanup()
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
