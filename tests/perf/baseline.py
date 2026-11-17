# -*- coding: utf-8 -*-
"""性能基线契约：指纹、统计、相对比较、落盘。

为什么不用绝对阈值
------------------
同一段代码在「本机」与「共享 CI runner」上能差 15%~30%，把「8000kbps 必须落在
6.8~9.2kbps」这种绝对区间写死，结果是本机永远绿、CI 随机红，最后大家都学会
无视告警。这里改成三层：

1. **指纹**：平台 / Python / CPU 核数 / 主机名 / 架构，任一不同就只做提示，
   不下结论（`different-machine`）。跨机比较没有意义，这一点必须显式写出来。
2. **相对基线**：拿当前值与同机同版本的基线中位数比，默认 warn 5% / fail 15%。
   恶化超过 fail 才算失败，小幅波动不误报。
3. **正确性断言**：性能用例自己必须同时断言「结果是对的」（状态码、字节数），
   否则会出现「快但错」。

基线文件格式见 `_perf_baseline.json`。`direction` 决定「变大」是变好还是变坏：
吞吐类 `higher_better`，延迟类 `lower_better`。
"""
import hashlib
import json
import os
import platform
import socket
import sys
from pathlib import Path

SCHEMA = 2                         # v2：补齐依赖 / 输入 / 预热 / 重复次数 / 单位口径

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent          # weaknet/
BASELINE_PATH = HERE / "_perf_baseline.json"

# 门禁的**固定输入**。tests/test_perf.py 从这里取 URL 参数，两处不会各自漂移；
# 换输入规模必须同步改这里，否则历史中位数的比较对象就变了。
WORKLOAD = {
    "bw_8000_kbps":        {"path": "/big", "kb": 2048, "profile": {"down_kbps": 8000}},
    "rtt_400ms_s":         {"path": "/small", "profile": {"latency_up_ms": 200,
                                                          "latency_down_ms": 200}},
    "bw_concurrent8_kbps": {"path": "/big", "kb": 128, "concurrency": 8,
                            "profile": {"down_kbps": 8000}},
    "stop_release_s":      {"path": "/big", "kb": 1024,
                            "profile": {"down_kbps": 300}, "stop_after_s": 0.6},
}
WARMUP = 0                         # 本套件不预热；每项首轮也计入样本（口径固定即可比）
UNIT_CONVENTION = {
    "kbps": "1 kbps = 1024 bit/s",
    "MiB": "1 MiB = 1048576 B",
    "steady_window": "整段响应从首字节到末字节，不剔除慢启动；计时从 sendall 开始",
}

# 指标的默认方向与阈值；基线文件里的同名字段可以覆盖
METRICS = {
    "bw_8000_kbps":        {"unit": "kbps", "direction": "higher_better",
                            "warn": 0.05, "fail": 0.15},
    "rtt_400ms_s":         {"unit": "s", "direction": "lower_better",
                            "warn": 0.10, "fail": 0.20},
    "bw_concurrent8_kbps": {"unit": "kbps", "direction": "higher_better",
                            "warn": 0.05, "fail": 0.15},
    "stop_release_s":      {"unit": "s", "direction": "lower_better",
                            "warn": 0.10, "fail": 0.20},
}

LEVELS = ("ok", "warn", "fail", "no-baseline", "different-machine")

# 共享 runner 的噪声可达 15%~30%（方案 §2.5 / P0-4）。CI 基线把 warn 放宽到
# 10%，fail 仍守 15%：正常波动只提示，真正的退化仍然红。
CI_WARN_FLOOR = 0.10


# ------------------------------------------------------------------ 指纹

def fingerprint():
    """机器指纹。字段少而稳：多一个字段就多一种「跨机」误判。"""
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count() or 0,
        "machine": platform.machine(),
        "host": host,
    }


def _py_mm(v):
    """Python 版本只取 major.minor：runner 的补丁版本会随镜像升级。"""
    parts = str(v or "").split(".")
    return ".".join(parts[:2])


def same_machine(fp, ci=None):
    """指纹是否与本机（或同一 CI runner 组）一致。

    三条规则，按宽松度递增：

    * 默认连 host 一起比：换一台机器只提示不判定，跨机比较没有意义。
    * `WN_PERF_SAME_MACHINE=1`：忽略 host，但仍要求平台 / Python / 核数 / 架构一致。
    * `WN_PERF_CI=1` 且基线带 `ci=true`：只比架构与 Python 主版本。CI 基线由
      **同一工作流、同一 runner 组**首次运行时生成并由 actions/cache 复用，
      镜像小版本、主机名、核数变化不应让门禁永远停在「不判定」。
      若不放宽这一条，runner 与本机基线的 platform/cpu_count 必然不同，
      任何注入的退化都会返回 different-machine —— 门禁等于没开（2026-11-13 实测）。

    `ci` 由 compare() 从**基线文件顶层**传入。`save()` 把 context() 展开到顶层，
    所以真实基线里是 `{"ci": true, "fingerprint": {...}}`，fingerprint 内部没有 ci
    字段。早期实现只在 fp 里找 ci，导致手工构造的单元测试全绿、真实 CI 基线却
    永远 different-machine（2026-11-13 二次核查发现并修复）。两种来源都兼容。
    """
    if not isinstance(fp, dict):
        return False
    cur = fingerprint()
    ci_flag = bool(ci) or bool(fp.get("ci"))
    if ci_flag and os.environ.get("WN_PERF_CI") == "1":
        return (fp.get("machine") == cur.get("machine")
                and _py_mm(fp.get("python")) == _py_mm(cur.get("python")))
    keys = ["platform", "python", "cpu_count", "machine"]
    if os.environ.get("WN_PERF_SAME_MACHINE") != "1":
        keys.append("host")
    return all(fp.get(k) == cur.get(k) for k in keys)


# ------------------------------------------------------------------ 版本

def spec_hash():
    """spec/spec.json 的短哈希：参数定义变了，历史吞吐数字就不可比。"""
    p = ROOT / "spec" / "spec.json"
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:8]
    except Exception:
        return "unknown"


def version_info():
    ver, build = "unknown", 0
    try:
        sys.path.insert(0, str(ROOT))
        import version as v          # noqa: WPS433（延迟导入，避免测试收集期副作用）
        ver = getattr(v, "VERSION", "unknown")
        build = getattr(v, "BUILD", 0)
    except Exception:
        pass
    return {"version": ver, "build": build}


def dependencies():
    """已安装依赖版本。性能结论离不开解释器与测试框架版本。"""
    out = {"python": sys.version.split()[0]}
    for name in ("pytest", "psutil", "certifi"):
        try:
            from importlib.metadata import version
            out[name] = version(name)
        except Exception:
            out[name] = "unavailable"
    return out


def repeats():
    try:
        return max(1, int(os.environ.get("WN_PERF_REPEATS", "7")))
    except ValueError:
        return 7


def context():
    """一次测量应该带上的全部上下文。缺任何一项，数字都无法与别的批次比较。

    v2 相对 v1 补齐的字段正是 2026-11-13 核查里发现缺失的那几项：依赖版本、
    输入规模、预热次数、重复次数、单位口径与稳态窗定义。历史 `214.2 MiB/s`
    换一套客户端就复现不出来（178.3），根因就是这些字段没有随数字一起归档。
    """
    ctx = {"schema": SCHEMA, "spec_hash": spec_hash(), "fingerprint": fingerprint()}
    ctx.update(version_info())
    ctx["dependencies"] = dependencies()
    ctx["inputs"] = WORKLOAD
    ctx["warmup"] = WARMUP
    ctx["repeats"] = repeats()
    ctx["unit_convention"] = UNIT_CONVENTION
    ctx["ci"] = os.environ.get("WN_PERF_CI") == "1"
    return ctx


# ------------------------------------------------------------------ 统计

def percentile(values, q):
    """最近秩法（nearest-rank）：小样本下比线性插值更保守，也不会插出不存在的值。"""
    if not values:
        return None
    xs = sorted(values)
    if q <= 0:
        return xs[0]
    if q >= 1:
        return xs[-1]
    import math
    idx = max(0, min(len(xs) - 1, int(math.ceil(q * len(xs))) - 1))
    return xs[idx]


def stats(values):
    """样本 → 中位数 / p95 / 极值 / **离散度**。

    为什么必须给离散度
    ------------------
    只有中位数 + 极值时，「中位 7946、单轮却 2459」这种双峰分布看起来和
    「稳定 7946」完全一样，而前者根本不能当作产品能力来宣称。更糟的是
    `percentile()` 用最近秩法，**n=5/7 时 p95 恒等于 max**——把 max 写成
    p95，会让人以为看到了分布尾部，其实只是最大值换了个名字。

    因此额外给出：

    * `stdev` 样本标准差（n-1 分母）
    * `cv_pct` 变异系数 = stdev / median × 100。>5% 说明该批次环境不稳，
      **不该拿中位数下结论**；>15% 基本等于测不出结论
    * `mad` 中位绝对偏差：对离群点稳健，小样本比 stdev 更可信
    * `iqr` p75 − p25：四分位距，同样抗离群
    * `p95_is_extreme` n < 20 时为 True（此时 p95 就是 max，不是分位估计）
    * `sem` 均值的标准误 = stdev / √n
    * `ci95_low` / `ci95_high` **均值的 95% 置信区间**（小样本用 t 分布临界值，
      df≤30 查表、更大用 1.96）。注意这是**均值**的区间，不是中位数的；
      偏态分布下中位数可能落在区间外，报告里必须写清是哪一个。

    `n < 2` 时离散度与区间字段一律 None，**不写 0**——0 会被读成「完全稳定」，
    而单样本恰恰是「完全不知道」。
    """
    xs = [v for v in values if v is not None]
    if not xs:
        return {"n": 0, "median": None, "p95": None, "min": None, "max": None,
                "stdev": None, "cv_pct": None, "mad": None, "iqr": None,
                "p95_is_extreme": None, "sem": None,
                "ci95_low": None, "ci95_high": None}
    s = sorted(xs)
    n = len(s)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    out = {"n": n, "median": med, "p95": percentile(s, 0.95),
           "min": s[0], "max": s[-1],
           # n < 20 时最近秩 p95 落在最后一个样本上，等于 max
           "p95_is_extreme": n < 20}
    if n >= 2:
        mean = sum(s) / float(n)
        out["stdev"] = (sum((x - mean) ** 2 for x in s) / (n - 1)) ** 0.5
        out["cv_pct"] = (out["stdev"] / med * 100.0) if med else None
        out["mad"] = _median([abs(x - med) for x in s])
        q1, q3 = percentile(s, 0.25), percentile(s, 0.75)
        out["iqr"] = (q3 - q1) if (q1 is not None and q3 is not None) else None
        # 不确定度：没有区间时，「中位 7946」会被当成一个精确值引用。
        # 小样本（回归门禁 n=7）必须用 t 而不是 1.96，否则区间偏窄、假精确。
        out["sem"] = out["stdev"] / (n ** 0.5)
        half = _t95(n) * out["sem"]
        out["ci95_low"] = mean - half
        out["ci95_high"] = mean + half
    else:
        out.update({"stdev": None, "cv_pct": None, "mad": None, "iqr": None,
                    "sem": None, "ci95_low": None, "ci95_high": None})
    return out


# 双侧 95% t 临界值（df=1..30）。n=7 的回归门禁落在 df=6→2.447，
# 用 1.96 会把区间压窄约 20%，正好是「看起来更稳」的假象。
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
    7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
    13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101,
    19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064,
    25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


def _t95(n):
    """样本量 n 对应的双侧 95% t 临界值；df>30 用正态近似 1.96。"""
    return _T95.get(max(1, int(n) - 1), 1.96)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def is_unstable(st, limit_pct=5.0):
    """这批样本能不能拿来下结论。

    返回 (ok, 原因)。判据只有一条但很硬：变异系数超过 `limit_pct` 就说明
    轮间波动已经和要测的效应同量级，此时中位数的置信区间会宽到没有意义。
    没有 cv（n<2）同样判为不可用——单样本无法自证稳定。
    """
    if not st or st.get("n", 0) < 2:
        return False, "样本不足（n=%s），无法判定稳定性" % (st or {}).get("n")
    cv = st.get("cv_pct")
    if cv is None:
        return False, "中位数为 0，变异系数无定义"
    if cv > limit_pct:
        return False, "变异系数 %.1f%% > %.1f%%（轮间波动与待测效应同量级）" % (
            cv, limit_pct)
    return True, "变异系数 %.1f%% ≤ %.1f%%" % (cv, limit_pct)


# ------------------------------------------------------------------ 比较

def _spec_of(name, entry):
    spec = dict(METRICS.get(name, {}))
    spec.update({k: entry[k] for k in ("direction", "warn", "fail", "unit")
                 if k in entry})
    spec.setdefault("direction", "higher_better")
    spec.setdefault("warn", 0.05)
    spec.setdefault("fail", 0.15)
    return spec


def compare(name, value, base=None):
    """当前值 vs 基线中位数。

    返回 {"level", "msg", "delta"}：
      level ∈ ok / warn / fail / no-baseline / different-machine
      delta > 0 表示比基线「更好」，< 0 表示更差（已按 direction 归一）。
    """
    base = load() if base is None else base
    metric = (base.get("metrics") or {}).get(name)
    if not metric or metric.get("median") in (None, 0):
        return {"level": "no-baseline", "delta": None,
                "msg": "%s 无基线（首次运行，先记录不下结论）" % name}
    if not same_machine(base.get("fingerprint"), ci=base.get("ci")):
        return {"level": "different-machine", "delta": None,
                "msg": "%s 基线与本机指纹不同，只记录不判定" % name}
    spec = _spec_of(name, metric)
    ref = float(metric["median"])
    cur = float(value)
    if spec["direction"] == "higher_better":
        delta = (cur - ref) / ref
        worse_word = "下降"
    else:
        delta = (ref - cur) / ref
        worse_word = "上升"
    pct = abs(delta) * 100.0
    sign = "+" if delta >= 0 else "-"
    msg = ("%s: 当前 %.3f%s / 基线 %.3f%s（%s%.1f%%）"
           % (name, cur, spec.get("unit", ""), ref, spec.get("unit", ""),
              sign, pct))
    if delta <= -spec["fail"]:
        return {"level": "fail", "delta": delta,
                "msg": msg + "，不达标：%s超过 %.0f%%" % (worse_word, spec["fail"] * 100)}
    if delta <= -spec["warn"]:
        return {"level": "warn", "delta": delta,
                "msg": msg + "，注意：%s超过 %.0f%%" % (worse_word, spec["warn"] * 100)}
    return {"level": "ok", "delta": delta, "msg": msg}


# ------------------------------------------------------------------ 落盘

def baseline_path(path=None):
    """显式参数 > `WN_PERF_BASELINE` > 仓库默认基线。

    CI 用环境变量指向 runner 专属基线（首次生成后由 actions/cache 复用）：
    既不会误读本机基线导致「永远 different-machine」，也不会把 runner 的
    数字写回仓库里那份本机基线。
    """
    if path:
        return Path(path)
    env = os.environ.get("WN_PERF_BASELINE")
    return Path(env) if env else BASELINE_PATH


def load(path=None):
    p = baseline_path(path)
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("metrics"), dict):
            return d
    except Exception:
        pass
    return {"schema": SCHEMA, "metrics": {}}


def save(samples, path=None, extra=None):
    """把 {指标名: [样本...]} 汇总后写入基线文件（覆盖同名字段，保留其他字段）。

    只在显式要求（WN_PERF_UPDATE_BASELINE=1）时调用 —— 自动更新基线等于
    把回归也一起「承认」成新标准。
    """
    p = baseline_path(path)
    base = load(p)
    ctx = context()
    buf = dict(base)
    buf.update(ctx)
    buf["metrics"] = dict(base.get("metrics") or {})
    for name, values in samples.items():
        st = stats(values)
        if st["n"] == 0:
            continue
        spec = _spec_of(name, {})
        warn = spec["warn"]
        if ctx.get("ci"):
            # 共享 runner 噪声大，warn 留出余量；fail 不放宽。
            warn = max(warn, CI_WARN_FLOOR)
        entry = {"n": st["n"], "median": st["median"], "p95": st["p95"],
                 "min": st["min"], "max": st["max"],
                 "unit": spec["unit"], "direction": spec["direction"],
                 "warn": warn, "fail": spec["fail"]}
        buf["metrics"][name] = entry
    if extra:
        buf.update(extra)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(buf, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, p)
    return p
