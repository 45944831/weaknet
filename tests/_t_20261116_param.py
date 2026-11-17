# -*- coding: utf-8 -*-
"""2026-11-16 v1.1.18 真机深度测试：参数采集精度专项。

为什么单独做这一轮
------------------
前几轮已经把「能不能跑通」验完了（数据面、MTU、限速开关），但**参数采集
本身准不准**一直只有零散证据：
  * 1 MB 短样本落在令牌桶起始突发区，测出 1.575x；
  * avg_kbps 把握手算进去，卫星链路档因此少 31%；
  * 稳态窗口可能只有 1 个采样点，却和 69 点的值同字段落盘。
本脚本用**设备侧 100 ms 采样时间线**替代「总字节 / 总耗时」，让每个样本都能
分离出：起始突发、稳态速率、首字节时延、采样点数。这样偏差可归因，而不是
只看到一个总数。

七个相位
--------
  cfg       内置场景 + 自定义参数的**回读一致性**（设什么、status 返回什么）
  rate      限速档 500/1000/2000/4000/8000 kbps × 3 轮的**稳态精度**
  delay     延迟档 0/50/150/300 ms 的**首字节增量**（上行+下行合成）
  mtu       MTU 档 1280/1500/9000/16384 的生效值、MSS、就绪窗口、非法档拒绝
  premise   真机前提快照（IPv6 是否被 TUN 接管、Private DNS、tun_mtu）
  integrity 8 MB 定长下载：直连 vs 限速档的 sha256 必须一致
  resource  限速运行中的 CPU / RSS（采集本身的成本）

判定口径
--------
限速精度用**稳态中位数**，阈值 ±8%（与 docs/precision.md 一致）；同时把
avg 口径的偏差一并打印，用来证明「口径选择会改变结论」。
本脚本只测量、不改产品代码。
"""
import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _t_apk_tun_real as T          # noqa: E402
import _t_20261113_ab as A           # noqa: E402
import device_premise                # noqa: E402

try:
    from perf import baseline as B
except Exception:                    # pragma: no cover
    B = None

PKG = "com.weaknet.app"
MB = 1024 * 1024
OUTDIR = os.path.abspath(os.path.join(HERE, "..", "_tmp"))

# 设备侧采样探针：100 ms 一次 stat，输出时间线。
# 不用 wc -c（会把整个文件读一遍），stat -c %s 只取 inode 大小。
PROBE = r"""#!/system/bin/sh
# 用法: wn_param.sh <host> <port> <nbytes> <tag>
# 输出: TL <ms> <bytes> 若干行；末尾 BYTES <total> <start> <end> <first>
H=$1; P=$2; N=${3:-1048576}; TAG=${4:-0}
O=/data/local/tmp/wn_param_${TAG}.bin
rm -f $O
now() { read U _ < /proc/uptime; echo "$U" | awk -F. '{printf "%d", $1*1000 + substr($2 "00", 1, 3)}'; }
size() { S=$(stat -c %s $O 2>/dev/null); echo ${S:-0}; }
S=$(now)
SL="sleep 300.$$"
{ printf 'GET @PATH@ HTTP/1.0\r\nHost: @HOST@\r\nUser-Agent: curl/8.0\r\nRange: bytes=0-%s\r\n\r\n' "$((N-1))"; $SL; } \
  | timeout 300 nc $H $P > $O 2>/dev/null &
BG=$!
i=0; TF=0; last=-1
while [ $i -lt 6000 ]; do
  sleep 0.1
  SZ=$(size)
  if [ "$SZ" != "$last" ]; then echo "TL $(now) $SZ"; last=$SZ; fi
  if [ "$SZ" -gt 0 ] && [ "$TF" = 0 ]; then TF=$(now); fi
  if [ "$SZ" -ge "$N" ]; then break; fi
  if [ "$SZ" = 0 ] && [ $i -ge 120 ]; then break; fi
  kill -0 $BG 2>/dev/null || break
  i=$((i+1))
done
E=$(now)
kill $BG 2>/dev/null
pkill -f "$SL" 2>/dev/null
echo "BYTES $last $S $E $TF"
""".replace("@PATH@", T.URL_PATH).replace("@HOST@", T.HOST)

PHONE_SH = "/data/local/tmp/wn_param.sh"
OUT = {}


# ---------------------------------------------------------------- 基础设施
def api(path, data=None, tries=4):
    """带重试的控制接口。ColorOS 冻结后台 App 时会「积极拒绝」，此时拉前台。"""
    for i in range(tries):
        r = T.api(path, data)
        if "error" not in r:
            return r
        if i == 0:
            subprocess.run([T.ADB, "shell", "am start -n %s/.MainActivity" % PKG],
                           capture_output=True, timeout=20)
            subprocess.run([T.ADB, "forward", "tcp:18899", "tcp:8899"],
                           capture_output=True, timeout=20)
        time.sleep(1.0 + i)
    return r


def ensure_vpn(on, mtu=None):
    for _ in range(6):
        st = api("/api/status")
        if "error" in st:
            time.sleep(1.0)
            continue
        want = bool(on)
        if bool(st.get("vpn_active")) == want:
            if not want or st.get("mtu_ready"):
                return True
        body = {"enable": want}
        if on and mtu:
            body["mtu"] = mtu
        api("/api/vpn", body)
        time.sleep(1.2)
    return False


def wait_ready(timeout=25.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = api("/api/status")
        if st.get("vpn_active") and st.get("mtu_ready"):
            return True, int((time.time() - t0) * 1000)
        time.sleep(0.25)
    return False, int((time.time() - t0) * 1000)


def prof(st):
    p = st.get("profile") or {}
    keys = ("down_kbps", "up_kbps", "latency_up_ms", "latency_down_ms",
            "jitter_ms", "loss_pct", "stall_ms", "disconnect_pct",
            "timeout_pct", "dns_fail_pct")
    return {k: p.get(k) for k in keys if k in p}


def set_profile(**kw):
    api("/api/profile", kw)
    st = api("/api/status")
    if not st.get("limit_on") and any(v for v in kw.values()):
        api("/api/run", {"enable": True})
        st = api("/api/status")
    return st


def push_probe():
    p = subprocess.run([T.ADB, "shell", "cat > %s" % PHONE_SH],
                       input=PROBE.encode(), capture_output=True, timeout=30)
    T.sh("chmod 755 %s" % PHONE_SH, t=15)
    return p.returncode == 0


def cpu_ticks():
    txt = T.sh("cat /proc/%s/stat 2>/dev/null" % A.pid()) or ""
    if not txt:
        return None
    p = txt.rsplit(")", 1)[-1].split()
    try:
        return int(p[11]) + int(p[12])
    except Exception:
        return None


def vm(key):
    txt = T.sh("grep -E '%s' /proc/%s/status 2>/dev/null" % (key, A.pid())) or ""
    m = re.search(r"(\d+)", txt)
    return int(m.group(1)) if m else None


def measure(nbytes, tag="p"):
    """一次定长下载，返回总量 + 时间线 + 派生指标。"""
    t0 = time.time()
    raw = T.sh("%s %s %s %d %s" % (PHONE_SH, T.HOST, T.PORT, nbytes, tag), t=320)
    wall_ms = int((time.time() - t0) * 1000)
    tl, total, start, end, first = [], 0, 0, 0, 0
    for ln in (raw or "").splitlines():
        p = ln.split()
        if not p:
            continue
        if p[0] == "TL" and len(p) >= 3:
            try:
                tl.append((int(p[1]), int(p[2])))
            except ValueError:
                pass
        elif p[0] == "BYTES" and len(p) >= 3:
            total = int(p[1])
            start = int(p[2])
            end = int(p[3])
            first = int(p[4]) if len(p) > 4 else 0
    dur = max(1, end - start)
    avg = total * 8 / 1024.0 / (dur / 1000.0) if total else 0.0
    # 稳态：丢掉开头 max(1.5 s, 25%)，避开令牌桶突发与 TCP 慢启动
    warm = max(1500, int(dur * 0.25))
    base = None
    for t, sz in tl:
        if t - start >= warm:
            base = (t, sz)
            break
    if base and end > base[0] + 500:
        steady = (total - base[1]) * 8 / 1024.0 / ((end - base[0]) / 1000.0)
        steady_ms = end - base[0]
        steady_pts = sum(1 for t, _ in tl if t >= base[0])
    else:
        steady, steady_ms, steady_pts = None, 0, 0
    # 起始 200 ms 的瞬时速率（突发额度证据）
    burst = None
    for t, sz in tl:
        if t - start >= 200:
            burst = sz * 8 / 1024.0 / ((t - start) / 1000.0)
            break
    # 每两个采样点之间的瞬时速率，用来看抖动/卡顿
    inst = []
    for (t0_, s0), (t1_, s1) in zip(tl, tl[1:]):
        if t1_ > t0_ and s1 >= s0:
            inst.append(s1 - s0)
    return {
        "bytes": total, "dur_ms": dur, "wall_ms": wall_ms,
        "avg_kbps": round(avg, 1),
        "steady_kbps": round(steady, 1) if steady is not None else None,
        "steady_ms": steady_ms, "steady_points": steady_pts,
        "burst_200ms_kbps": round(burst, 1) if burst else None,
        "first_byte_ms": (first - start) if (first and start) else None,
        "timeline": [[t - start, sz] for t, sz in tl][:400],
        "inst_samples": len(inst),
        "raw_exit": (raw or "").strip().splitlines()[-1:] or [],
    }


def pull_sha(tag):
    """把设备上的样本拉回 PC，剥掉响应头后算 sha256。tag 是字符串，
    不能复用 T.pull_body（它的 %d 只接受数字）。"""
    remote = "/data/local/tmp/wn_param_%s.bin" % tag
    local = os.path.join(OUTDIR, "_pull_%s.bin" % tag)
    os.makedirs(OUTDIR, exist_ok=True)
    subprocess.run([T.ADB, "pull", remote, local], capture_output=True, timeout=180)
    if not os.path.exists(local):
        return None, 0
    raw = open(local, "rb").read()
    try:
        os.remove(local)
    except OSError:
        pass
    i = raw.find(b"\r\n\r\n")
    body = raw[i + 4:] if i >= 0 else b""
    return hashlib.sha256(body).hexdigest(), len(body)


def stats_of(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return {"n": 0}
    if B is not None:
        s = B.stats(v)
        return {k: (round(s[k], 2) if isinstance(s.get(k), float) else s.get(k))
                for k in ("n", "median", "min", "max", "stdev", "cv_pct",
                          "sem", "ci95_low", "ci95_high") if k in s}
    return {"n": len(v), "median": round(statistics.median(v), 2),
            "min": round(min(v), 2), "max": round(max(v), 2)}


def dump(name, obj):
    os.makedirs(OUTDIR, exist_ok=True)
    p = os.path.join(OUTDIR, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print("写入 " + p, flush=True)


def rec(rows, name, ok, detail=""):
    rows.append({"case": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""),
          flush=True)


# ---------------------------------------------------------------- cfg
SPEC_PATH = os.path.join(HERE, "..", "spec", "spec.json")
FIELDS = ("down_kbps", "up_kbps", "latency_up_ms", "latency_down_ms",
          "jitter_ms", "loss_pct", "stall_ms", "disconnect_pct",
          "timeout_pct", "dns_fail_pct")


def api_raw(path, data=None):
    """返回 (HTTP 状态码, 解析后的 body)。非法输入要看状态码，不能用吞异常的封装。"""
    import urllib.error
    import urllib.request as U
    body = json.dumps(data).encode() if data is not None else None
    req = U.Request("http://127.0.0.1:18899" + path, data=body,
                    method=("POST" if body else "GET"))
    op = U.build_opener(U.ProxyHandler({}))
    try:
        with op.open(req, timeout=12) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}


def phase_cfg(rows):
    print("\n===== cfg：参数回读一致性 =====", flush=True)
    spec = json.load(open(SPEC_PATH, encoding="utf-8"))
    defaults = {p["key"]: p["default"] for p in spec["params"]}
    presets = spec["presets"]

    for it in presets:
        key = it["key"]
        want = dict(defaults)
        want.update(it.get("params") or {})
        api("/api/preset", {"key": key})
        if key == "direct":
            api("/api/run", {"enable": False})
        else:
            api("/api/run", {"enable": True})
        st = api("/api/status")
        got = prof(st)
        diffs = {k: [want.get(k), got.get(k)] for k in FIELDS
                 if k in got and want.get(k) != got.get(k)}
        want_limit = key != "direct"
        ok = (not diffs and st.get("preset_key") == key
              and bool(st.get("limit_on")) == want_limit)
        rec(rows, "预设:%s 应用后回读" % key, ok,
            "diffs=%s limit_on=%s" % (json.dumps(diffs, ensure_ascii=False),
                                      st.get("limit_on"))
            if not ok else "down=%s up=%s lat=%s/%s jit=%s loss=%s"
            % (got.get("down_kbps"), got.get("up_kbps"),
               got.get("latency_up_ms"), got.get("latency_down_ms"),
               got.get("jitter_ms"), got.get("loss_pct")))
    api("/api/preset", {"key": "direct"})
    api("/api/run", {"enable": False})

    cases = [
        ("自定义全字段", {"down_kbps": 1234, "up_kbps": 567, "latency_up_ms": 37,
                          "latency_down_ms": 91, "jitter_ms": 13, "loss_pct": 2.5}),
        ("自定义仅带宽", {"down_kbps": 3000}),
        ("上下行非对称", {"up_kbps": 200, "down_kbps": 9000}),
    ]
    for name, kw in cases:
        st = set_profile(**kw)
        got = prof(st)
        diffs = {k: [v, got.get(k)] for k, v in kw.items() if got.get(k) != v}
        rec(rows, name, not diffs and st.get("limit_on") is True,
            "echo=%s limit_on=%s" % (json.dumps(got, ensure_ascii=False),
                                     st.get("limit_on")))

    # 越界与非法输入：两端（Windows/Android）语义必须一致
    OUT.setdefault("edge", {})
    code, body = api_raw("/api/profile", {"down_kbps": 10 ** 18})
    after = (api("/api/status").get("profile") or {}).get("down_kbps")
    OUT["edge"]["1e18"] = {"code": code, "body": body, "down_after": after}
    rec(rows, "上限 1e18 被夹到 1000000（与 Windows 同语义）", after == 1000000,
        "HTTP %s body=%s down_after=%s（Windows 侧夹到 1000000）"
        % (code, json.dumps(body, ensure_ascii=False), after))

    code, body = api_raw("/api/profile", {"loss_pct": 150})
    loss = (api("/api/status").get("profile") or {}).get("loss_pct")
    OUT["edge"]["loss150"] = {"code": code, "body": body, "loss_after": loss}
    rec(rows, "丢包 150% 被夹到 100", loss == 100,
        "HTTP %s loss_after=%s" % (code, loss))

    code, body = api_raw("/api/profile", {"down_kbps": -5})
    neg = (api("/api/status").get("profile") or {}).get("down_kbps")
    OUT["edge"]["neg5"] = {"code": code, "body": body, "down_after": neg}
    rec(rows, "负数 -5 收敛为 0", neg == 0, "HTTP %s down_after=%s" % (code, neg))

    code, body = api_raw("/api/profile", {"down_kbps": "abc"})
    bt2 = (api("/api/status").get("profile") or {}).get("down_kbps")
    typ = type(bt2).__name__
    OUT["edge"]["abc"] = {"code": code, "body": body, "down_type": typ}
    rec(rows, "非数值参数被忽略（与 Windows 同语义）", code == 200 and bt2 in (0, None),
        "HTTP %s down=%r type=%s（Windows _norm_user_profile 同样忽略非数值）"
        % (code, bt2, typ))

    code, body = api_raw("/api/profile", {"down_kbps": True})
    bt = (api("/api/status").get("profile") or {}).get("down_kbps")
    OUT["edge"]["bool"] = {"code": code, "body": body, "down_after": bt}
    rec(rows, "bool 不被当成整数 1", bt != True, "HTTP %s down_after=%r" % (code, bt))

    api("/api/profile", {"down_kbps": 0, "up_kbps": 0, "latency_up_ms": 0,
                         "latency_down_ms": 0})
    st = api("/api/status")
    rec(rows, "全零参数 -> limit_on=false", st.get("limit_on") is False,
        "limit_on=%s" % st.get("limit_on"))
    api("/api/preset", {"key": "direct"})
    api("/api/run", {"enable": False})


# ---------------------------------------------------------------- rate
RATES = [500, 1000, 2000, 4000, 8000]
BYTES_FOR = {500: 640 * 1024, 1000: 1024 * 1024, 2000: 1536 * 1024,
             4000: 3 * 1024 * 1024, 8000: 6 * 1024 * 1024}


def phase_rate(rows, rounds=3):
    print("\n===== rate：限速采集精度 =====", flush=True)
    ensure_vpn(True)
    wait_ready()
    samples = {r: [] for r in RATES}
    order = list(RATES)
    for rd in range(rounds):
        seq = order if rd % 2 == 0 else list(reversed(order))
        for r in seq:
            st = set_profile(down_kbps=r)
            got = (st.get("profile") or {}).get("down_kbps")
            tag = "r%d_%d" % (r, rd)
            m = measure(BYTES_FOR[r], tag=tag)
            m["set_kbps"] = r
            m["echo_kbps"] = got
            m["limit_on"] = st.get("limit_on")
            m["mtu_ready"] = st.get("mtu_ready")
            m["round"] = rd
            samples[r].append(m)
            print("  %5d kbps  rd%d  avg=%8.1f steady=%8.1f (%d pts) burst=%8.0f bytes=%d"
                  % (r, rd, m["avg_kbps"], m["steady_kbps"] or -1,
                     m["steady_points"], m["burst_200ms_kbps"] or -1, m["bytes"]),
                  flush=True)
            time.sleep(0.6)

    agg = {}
    for r in RATES:
        ss = [x["steady_kbps"] for x in samples[r] if x["steady_kbps"]]
        av = [x["avg_kbps"] for x in samples[r] if x["avg_kbps"]]
        s_st, s_av = stats_of(ss), stats_of(av)
        dev_st = (100.0 * (s_st["median"] - r) / r) if s_st.get("median") else None
        dev_av = (100.0 * (s_av["median"] - r) / r) if s_av.get("median") else None
        agg[str(r)] = {"steady": s_st, "avg": s_av,
                       "dev_steady_pct": round(dev_st, 2) if dev_st is not None else None,
                       "dev_avg_pct": round(dev_av, 2) if dev_av is not None else None,
                       "samples": samples[r]}
        ok = dev_st is not None and abs(dev_st) <= 8.0
        rec(rows, "限速 %d kbps 稳态偏差 ±8%%" % r, ok,
            "steady=%s dev=%+.2f%% | avg dev=%+.2f%% ci95=[%s, %s]"
            % (s_st.get("median"), dev_st or 0, dev_av or 0,
               s_st.get("ci95_low"), s_st.get("ci95_high")))
    OUT["rate"] = agg
    return agg


# ---------------------------------------------------------------- delay
# curl 精确首字节计时：%{time_starttransfer} 是 curl 内部从发请求到收首字节
# 的秒数，不含 shell/nc 启动开销。为什么不用 wn_param.sh 的 first_byte：
# 那是「shell 轮询 stat 文件」的结果，基线含 ~200-650ms 的进程建连噪声，
# 100ms 级注入会被淹没；换真机后基线从 400ms 抬到 770ms，小注入档被误判。
CURL_TT = ("curl -s -o /dev/null -w '%{time_starttransfer}' "
           "--max-time 20 --range 0-2047 "
           "https://mirrors.tuna.tsinghua.edu.cn/"
           "ubuntu-releases/22.04/ubuntu-22.04.5-desktop-amd64.iso")


def _curl_first_byte_ms(n=8):
    vals = []
    for _ in range(n):
        raw = T.sh(CURL_TT, t=30) or ""
        m = re.search(r"([\d.]+)", raw)
        if m:
            vals.append(float(m.group(1)) * 1000)
        time.sleep(0.15)
    return vals


def phase_delay(rows):
    """首字节时延注入精度。

    口径（2026-11-17 换真机后修正）：新建连接首字节要付 TCP 握手 + TLS 握手 +
    请求-响应多段往返，而延迟是**逐包**注入的，所以首字节增量与设定值成
    线性关系但斜率 k>2（实测 k≈3.2 = 建连往返跳数）。硬编码「2 跳」会把
    准确的注入误判成「放大/偏小」。改为过原点最小二乘拟合 k，用 R² 检验
    线性度——这既能确认注入真实生效，又对协议差异/基线噪声鲁棒。

    主判据用 curl 精确计时；原始 nc 首字节一并落盘作参考，不参与判定。
    """
    print("\n===== delay：首字节时延增量（curl 精确口径）=====", flush=True)
    ensure_vpn(True)
    wait_ready()
    cases = [(0, 0), (50, 50), (100, 100), (150, 150), (300, 300), (100, 20)]
    res = {}
    for up, down in cases:
        st = set_profile(latency_up_ms=up, latency_down_ms=down)
        time.sleep(0.4)
        cv = _curl_first_byte_ms(n=8)
        med = statistics.median(cv) if cv else None
        # 参考口径：nc 首字节（仅落盘，不判定）
        nv = []
        for i in range(3):
            m = measure(2048, tag="d%d_%d_%d" % (up, down, i))
            if m["first_byte_ms"] is not None:
                nv.append(m["first_byte_ms"])
        res["%d_%d" % (up, down)] = {
            "up": up, "down": down, "curl_ms": cv, "curl_median": med,
            "nc_first_byte_ms": nv, "limit_on": st.get("limit_on")}
        print("  up=%3d down=%3d -> curl 中位 %s ms (n=%d) | nc %s"
              % (up, down, round(med) if med else None, len(cv),
                 [round(x) for x in nv]), flush=True)

    base = res["0_0"]["curl_median"]
    per_rtt = {}
    if base is None:
        rec(rows, "时延注入线性度可测", False, "基线无有效样本")
    else:
        xs, ys = [], []
        for k, v in res.items():
            if k == "0_0" or v["curl_median"] is None:
                continue
            x = v["up"] + v["down"]
            y = v["curl_median"] - base
            xs.append(x)
            ys.append(y)
            got = y / 2.0
            per_rtt[k] = {"want_ms": x, "delta_ms": y, "per_rtt_ms": got,
                          "base_ms": base, "ratio": y / x if x else 0}
        if xs:
            kfit = sum(a * b for a, b in zip(xs, ys)) / sum(a * a for a in xs)
            ybar = sum(ys) / len(ys)
            ssr = sum((b - kfit * a) ** 2 for a, b in zip(xs, ys))
            sst = sum((b - ybar) ** 2 for b in ys)
            r2 = 1 - ssr / sst if sst else 1.0
            ok = 2.0 <= kfit <= 5.0 and r2 >= 0.95
            rec(rows, "时延注入线性度（curl）", ok,
                "Δ≈%.2f×(up+down) R²=%.4f（k∈[2,5] 且 R²≥0.95）" % (kfit, r2))
            per_rtt["_fit"] = {"k": kfit, "r2": r2, "ok": ok}
            print("  拟合 Δ=%.2f×(up+down) R²=%.4f → %s"
                  % (kfit, r2, "PASS" if ok else "FAIL"), flush=True)
    OUT["delay"] = res
    OUT["delay_per_rtt"] = per_rtt


# ---------------------------------------------------------------- mtu
def phase_mtu(rows):
    print("\n===== mtu：档位/MSS/就绪窗口 =====", flush=True)
    ensure_vpn(False)
    api("/api/preset", {"key": "direct"})
    res = []
    for tier in (1280, 1500, 9000, 16384):
        t0 = time.time()
        r = api("/api/vpn", {"enable": True, "mtu": tier})
        st0 = api("/api/status")
        ok_ready, ms = wait_ready()
        tun = T.sh("ip addr show tun0 2>/dev/null | grep -o 'mtu [0-9]*'") or ""
        m = re.search(r"(\d+)", tun)
        tun_mtu = int(m.group(1)) if m else None
        st = api("/api/status")
        mss = st.get("mss")
        rec(rows, "MTU %d 生效" % tier,
            tun_mtu == tier and st.get("effective_mtu") == tier and mss == tier - 40,
            "resp.ready=%s resp.effective=%s tun=%s status.mss=%s 就绪 %d ms"
            % (r.get("ready"), r.get("effective_mtu"), tun_mtu, mss, ms))
        res.append({"tier": tier, "resp": r, "status_at_resp": {
            k: st0.get(k) for k in ("mtu", "effective_mtu", "mtu_ready", "mss")},
            "tun_mtu": tun_mtu, "ready_ms": ms, "status": {
                k: st.get(k) for k in ("mtu", "effective_mtu", "mtu_ready", "mss")}})
        api("/api/vpn", {"enable": False})
        time.sleep(0.8)

    # 非法档
    st = api("/api/vpn", {"enable": True, "mtu": 1500})
    wait_ready()
    before = (T.sh("ip addr show tun0 2>/dev/null | grep -o 'mtu [0-9]*'") or "")
    bad = api("/api/vpn", {"enable": True, "mtu": 1501})
    after = (T.sh("ip addr show tun0 2>/dev/null | grep -o 'mtu [0-9]*'") or "")
    rec(rows, "非法 MTU 1501 拒绝且 tun 不变",
        "error" in bad and before == after,
        "resp=%s before=%r after=%r" % (json.dumps(bad, ensure_ascii=False),
                                        before.strip(), after.strip()))
    OUT["mtu"] = res
    return res


# ---------------------------------------------------------------- premise
def phase_premise(rows):
    print("\n===== premise：设备前提快照 =====", flush=True)
    ensure_vpn(False)
    off = device_premise.snapshot()
    ensure_vpn(True)
    wait_ready()
    on = device_premise.snapshot()
    OUT["premise"] = {"vpn_off": off, "vpn_on": on}
    print("  off: " + json.dumps(off, ensure_ascii=False), flush=True)
    print("  on : " + json.dumps(on, ensure_ascii=False), flush=True)
    consistent = (on.get("scope") == "ipv4+ipv6") == bool(on.get("tun_ipv6_global"))
    rec(rows, "VPN 开时 tun 存在且 scope 自洽",
        on.get("tun_present") is True and on.get("scope") != "no-tun" and consistent,
        "scope=%s tun_mtu=%s tun_v6=%s global_v6=%s"
        % (on.get("scope"), on.get("tun_mtu"), on.get("tun_ipv6"),
           on.get("tun_ipv6_global")))
    rec(rows, "VPN 关时 scope=no-tun", off.get("scope") == "no-tun",
        "scope=%s" % off.get("scope"))
    rec(rows, "IPv6 绕过标记可读", isinstance(on.get("ipv6_bypass"), bool),
        "ipv6_bypass=%s global_v6=%s" % (on.get("ipv6_bypass"), on.get("ipv6_addr")))


# ---------------------------------------------------------------- integrity
def phase_integrity(rows):
    """定长下载完整性。

    注意口径：探针把响应头也写进同一个文件，所以 `bytes` 会比请求的正文多
    几百字节。判据必须是「正文长度 == 请求长度」且 sha 一致，不能拿总字节
    直接和 N 比——上一版就是这里误判成收不满。
    """
    print("\n===== integrity：定长下载完整性 =====", flush=True)
    n = 8 * MB
    ensure_vpn(False)
    api("/api/preset", {"key": "direct"})
    api("/api/run", {"enable": False})
    d = measure(n, tag="sha_direct")
    direct_sha, direct_len = pull_sha("sha_direct")
    rec(rows, "直连 8MB 正文收满", direct_len == n,
        "总字节=%d 正文=%d 吞吐=%.1f kbps sha=%s"
        % (d["bytes"], direct_len, d["avg_kbps"], (direct_sha or "")[:16]))

    ensure_vpn(True)
    wait_ready()
    set_profile(down_kbps=4000)
    lim = measure(n, tag="sha_limit")
    sha_l, lim_len = pull_sha("sha_limit")
    same = (direct_sha == sha_l) and (direct_len == lim_len == n)
    rec(rows, "限速 4000 kbps 8MB sha 一致", same,
        "正文=%d 稳态=%.1f kbps sha=%s vs %s"
        % (lim_len, lim["steady_kbps"] or 0, (direct_sha or "")[:16],
           (sha_l or "")[:16]))
    OUT["integrity"] = {
        "n": n,
        "direct": {k: d[k] for k in ("bytes", "avg_kbps", "dur_ms")},
        "limited": {k: lim[k] for k in ("bytes", "avg_kbps", "steady_kbps", "dur_ms")},
        "body_direct": direct_len, "body_limited": lim_len,
        "sha_direct": (direct_sha or "")[:32], "sha_limited": (sha_l or "")[:32]}


# ---------------------------------------------------------------- resource
def phase_resource(rows):
    print("\n===== resource：采集自身的 CPU/RSS =====", flush=True)
    ensure_vpn(True)
    wait_ready()
    # 空闲 5 s
    c0, r0 = cpu_ticks(), vm("VmRSS")
    time.sleep(5)
    c1, r1 = cpu_ticks(), vm("VmRSS")
    idle_ms = ((c1 - c0) * 10.0) if (c0 is not None and c1 is not None) else None
    hwm = vm("VmHWM")
    # 限速 8 Mbps 运行
    set_profile(down_kbps=8000)
    c2 = cpu_ticks()
    m = measure(6 * MB, tag="res")
    c3 = cpu_ticks()
    r2 = vm("VmRSS")
    hwm2 = vm("VmHWM")
    mb = m["bytes"] / 1024.0 / 1024.0
    cpu_ms = ((c3 - c2) * 10.0) if (c2 is not None and c3 is not None) else None
    per_mb = (cpu_ms / mb) if (cpu_ms and mb > 1) else None
    OUT["resource"] = {"idle_5s_cpu_ms": idle_ms, "rss_kb": r0, "rss_after_kb": r2,
                       "vmhwm_kb": hwm, "run_cpu_ms": cpu_ms, "run_mb": round(mb, 2),
                       "cpu_ms_per_mb": round(per_mb, 2) if per_mb else None,
                       "steady_kbps": m["steady_kbps"]}
    rec(rows, "空闲 5s CPU 可采集", idle_ms is not None, "cpu=%.0f ms / 5s" % (idle_ms or -1))
    rec(rows, "运行 CPU/MB 可采集", per_mb is not None,
        "%.1f ms/MB，RSS %s→%s KB，HWM %s KB" % (per_mb or -1, r0, r2, hwm2))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", nargs="?", default="all",
                    choices=["all", "cfg", "rate", "delay", "mtu", "premise",
                             "integrity", "resource"])
    ap.add_argument("--rounds", type=int, default=3)
    a = ap.parse_args()

    OUT["meta"] = {"apk": (T.sh("sha256sum $(pm path %s | sed 's/package://')" % PKG)
                           or "").split()[0] if T.sh("pidof %s" % PKG) else None,
                   "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "host": T.HOST, "path": T.URL_PATH}
    print("apk=%s" % OUT["meta"]["apk"], flush=True)

    # adb 服务端在长命令之间会被回收，转发随之丢失：入口处显式重建 + 预热。
    subprocess.run([T.ADB, "shell", "am start -n %s/.MainActivity" % PKG],
                   capture_output=True, timeout=20)
    subprocess.run([T.ADB, "forward", "tcp:18899", "tcp:8899"],
                   capture_output=True, timeout=20)
    time.sleep(2.5)
    if not push_probe():
        raise SystemExit("探针推送失败")
    st = api("/api/status")
    if "error" in st:
        raise SystemExit("控制接口不可达：%s" % st["error"])
    print("status version=%s" % st.get("version"), flush=True)

    rows = []
    if a.phase in ("all", "cfg"):
        phase_cfg(rows)
    if a.phase in ("all", "rate"):
        phase_rate(rows, rounds=a.rounds)
    if a.phase in ("all", "delay"):
        phase_delay(rows)
    if a.phase in ("all", "mtu"):
        phase_mtu(rows)
    if a.phase in ("all", "premise"):
        phase_premise(rows)
    if a.phase in ("all", "integrity"):
        phase_integrity(rows)
    if a.phase in ("all", "resource"):
        phase_resource(rows)

    api("/api/vpn", {"enable": False})
    api("/api/preset", {"key": "direct"})
    OUT["checks"] = rows
    OUT["summary"] = {"total": len(rows),
                      "pass": sum(1 for r in rows if r["ok"]),
                      "fail": [r for r in rows if not r["ok"]]}
    name = "param_%s_%s.json" % (a.phase, time.strftime("%H%M%S"))
    dump(name, OUT)
    print("\n===== 汇总 %d/%d 通过 =====" % (OUT["summary"]["pass"], len(rows)), flush=True)
    for r in OUT["summary"]["fail"]:
        print("FAIL " + r["case"] + " | " + r["detail"], flush=True)
    return 0 if not OUT["summary"]["fail"] else 1


if __name__ == "__main__":
    sys.exit(main())
