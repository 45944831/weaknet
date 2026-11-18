# -*- coding: utf-8 -*-
"""基线工具的单元测试（默认套件就跑，不需要网络与真实引擎）。

这些用例守住的是「判定规则本身」：中位数/p95 的口径、方向归一、
跨机不下结论、基线缺失不误报。规则错了，后面所有性能结论都是假的。
"""
import json

from perf import baseline as B


def _base(metrics, fp=None):
    return {"schema": B.SCHEMA,
            "fingerprint": fp if fp is not None else B.fingerprint(),
            "metrics": metrics}


def test_stats_odd_and_even_median():
    assert B.stats([3, 1, 2])["median"] == 2
    assert B.stats([4, 1, 3, 2])["median"] == 2.5
    st = B.stats([1, 2, 3, 4, 5])
    assert st["n"] == 5 and st["min"] == 1 and st["max"] == 5


def test_stats_p95_nearest_rank():
    xs = list(range(1, 21))          # 1..20
    assert B.percentile(xs, 0.95) == 19
    assert B.percentile(xs, 1.0) == 20
    assert B.percentile(xs, 0.0) == 1


def test_stats_empty_is_none_not_zero():
    st = B.stats([])
    assert st["n"] == 0
    assert st["median"] is None and st["p95"] is None
    assert st["ci95_low"] is None and st["ci95_high"] is None


def test_stats_ci95_uses_t_for_small_samples():
    """n=5 必须用 t(0.975,4)=2.776，不能拿 1.96 冒充——后者会把区间压窄 29%。

    区间是**均值**的，不是中位数的：这条测试同时锁住「别把两个口径混用」。
    """
    xs = [10, 12, 14, 16, 18]
    st = B.stats(xs)
    assert st["n"] == 5
    assert st["sem"] is not None and st["sem"] > 0
    half = 2.776 * st["sem"]
    assert abs(st["ci95_low"] - (14 - half)) < 1e-9
    assert abs(st["ci95_high"] - (14 + half)) < 1e-9
    assert st["ci95_low"] < st["median"] < st["ci95_high"]


def test_stats_ci95_single_sample_is_none():
    """单样本不能自证稳定：区间字段必须是 None，不是 0 宽度的假区间。"""
    st = B.stats([5])
    assert st["n"] == 1
    assert st["sem"] is None
    assert st["ci95_low"] is None and st["ci95_high"] is None


def test_compare_without_baseline_does_not_judge():
    v = B.compare("bw_8000_kbps", 8000, base=_base({}))
    assert v["level"] == "no-baseline"
    assert v["delta"] is None


def test_compare_different_machine_does_not_judge():
    fp = dict(B.fingerprint())
    fp["host"] = "another-runner"
    v = B.compare("bw_8000_kbps", 5000,
                  base=_base({"bw_8000_kbps": {"median": 8000}}, fp))
    assert v["level"] == "different-machine"


def test_compare_higher_better_levels():
    base = _base({"bw_8000_kbps": {"median": 8000}})
    assert B.compare("bw_8000_kbps", 8000, base)["level"] == "ok"
    assert B.compare("bw_8000_kbps", 8200, base)["level"] == "ok"      # 变快永远 ok
    assert B.compare("bw_8000_kbps", 7400, base)["level"] == "warn"    # -7.5%
    assert B.compare("bw_8000_kbps", 6000, base)["level"] == "fail"    # -25%


def test_compare_lower_better_levels():
    base = _base({"rtt_400ms_s": {"median": 0.40}})
    assert B.compare("rtt_400ms_s", 0.40, base)["level"] == "ok"
    assert B.compare("rtt_400ms_s", 0.30, base)["level"] == "ok"       # 变快永远 ok
    assert B.compare("rtt_400ms_s", 0.45, base)["level"] == "warn"     # +12.5%
    assert B.compare("rtt_400ms_s", 0.60, base)["level"] == "fail"     # +50%


def test_compare_exact_threshold_survives_float_error():
    """CI 把 bw 的 warn 放宽到 10%，注入 -10% 必须判 warn 而非 ok。

    8192 * 0.9 算出的 delta 是 -0.09999999999999998，直接和 -0.10 比较会漏判。
    这条用例锁住阈值边界的浮点容差，防止门禁自检再次在 CI 上误红。
    """
    base = _base({"bw_8000_kbps": {"median": 8192, "warn": 0.10, "fail": 0.15}})
    v = B.compare("bw_8000_kbps", 8192 * 0.9, base)
    assert v["level"] == "warn", v["msg"]
    assert B.compare("bw_8000_kbps", 8192 * 0.85, base)["level"] == "fail"


def test_compare_thresholds_from_baseline_entry():
    base = _base({"bw_8000_kbps": {"median": 8000, "warn": 0.20, "fail": 0.40}})
    assert B.compare("bw_8000_kbps", 7000, base)["level"] == "ok"      # -12.5% < 20%
    assert B.compare("bw_8000_kbps", 4000, base)["level"] == "fail"    # -50% > 40%


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "b.json"
    B.save({"bw_8000_kbps": [7900, 8000, 8100]}, path=p)
    d = B.load(p)
    m = d["metrics"]["bw_8000_kbps"]
    assert m["n"] == 3 and m["median"] == 8000
    assert d["spec_hash"] and d["fingerprint"]
    # 再存一个指标，必须保留已有指标
    B.save({"rtt_400ms_s": [0.4, 0.42]}, path=p)
    d = B.load(p)
    assert set(d["metrics"]) == {"bw_8000_kbps", "rtt_400ms_s"}
    assert json.loads(p.read_text(encoding="utf-8"))["metrics"]["rtt_400ms_s"]["n"] == 2


def test_context_has_comparability_fields():
    ctx = B.context()
    for k in ("spec_hash", "fingerprint", "version", "build"):
        assert k in ctx
    for k in ("platform", "python", "cpu_count", "machine", "host"):
        assert k in ctx["fingerprint"]


def test_context_has_v2_fields():
    """v1 缺的字段正是历史数字无法复现的根因，必须锁住。"""
    ctx = B.context()
    for k in ("dependencies", "inputs", "warmup", "repeats",
              "unit_convention", "ci"):
        assert k in ctx, k
    assert ctx["inputs"]["bw_8000_kbps"]["kb"] == 2048
    assert ctx["unit_convention"]["kbps"] == "1 kbps = 1024 bit/s"
    assert ctx["repeats"] >= 1


def test_ci_baseline_judges_regression(monkeypatch):
    """CI 专属基线必须真正判定退化，不能永远 different-machine。"""
    fp = dict(B.fingerprint())
    fp["ci"] = True
    fp["platform"] = "Windows-10-10.0.20348-SP0"   # runner 镜像与本机不同
    fp["cpu_count"] = 2
    fp["host"] = "fv-az-runner"
    base = _base({"bw_8000_kbps": {"median": 8000}}, fp)
    monkeypatch.setenv("WN_PERF_CI", "1")
    v = B.compare("bw_8000_kbps", 5000, base=base)  # 注入 -37.5%
    assert v["level"] == "fail", v["msg"]


def test_ci_relaxation_does_not_apply_to_local_baseline(monkeypatch):
    """本机基线（ci=false）在 CI 模式下也不能被拿去和 runner 硬比。"""
    fp = dict(B.fingerprint())
    fp["platform"] = "Windows-10-10.0.20348-SP0"
    fp["cpu_count"] = 2
    base = _base({"bw_8000_kbps": {"median": 8000}}, fp)
    monkeypatch.setenv("WN_PERF_CI", "1")
    assert B.compare("bw_8000_kbps", 5000, base=base)["level"] == "different-machine"


def test_baseline_path_env_override(monkeypatch, tmp_path):
    p = tmp_path / "ci-baseline.json"
    monkeypatch.setenv("WN_PERF_BASELINE", str(p))
    assert B.baseline_path() == p
    assert B.baseline_path(tmp_path / "explicit.json").name == "explicit.json"


def test_real_saved_ci_baseline_judges_regression(monkeypatch, tmp_path):
    """锁住「文件顶层 ci」与「判定函数读 ci」的一致性。

    之前手工构造 fp['ci']=True 的用例能过，但 save() 生成的真实基线把 ci
    放在顶层，fingerprint 里没有 —— CI 上注入退化仍返回 different-machine。
    这条用例必须走 save() → load() 的真实结构，不许手工拼字典。
    """
    p = tmp_path / "runner-baseline.json"
    monkeypatch.setenv("WN_PERF_CI", "1")
    monkeypatch.setenv("WN_PERF_BASELINE", str(p))
    B.save({"bw_8000_kbps": [8000] * 7})
    d = B.load(p)
    assert d["ci"] is True
    assert "ci" not in d["fingerprint"]

    # 模拟 GitHub runner：镜像、核数、主机名、Python 补丁版本全都与本机不同
    d["fingerprint"].update({"platform": "Windows-10-10.0.20348-SP0",
                             "cpu_count": 2, "host": "fv-az-runner",
                             "python": "3.10.13"})
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    v = B.compare("bw_8000_kbps", 5000)       # 注入 -37.5%
    assert v["level"] == "fail", v["msg"]


def test_ci_flag_alone_is_not_enough_for_local_baseline(monkeypatch):
    """本机基线即便顶层 ci=true，也不能在 CI 模式下被宽松判定。"""
    fp = dict(B.fingerprint())
    fp["platform"] = "Windows-10-10.0.20348-SP0"
    fp["cpu_count"] = 2
    base = _base({"bw_8000_kbps": {"median": 8000}}, fp)
    base["ci"] = False
    monkeypatch.setenv("WN_PERF_CI", "1")
    assert B.compare("bw_8000_kbps", 5000, base=base)["level"] == "different-machine"


def test_shipped_baseline_carries_full_v2_context():
    """P0-1：仓库里那份基线必须真的带齐 v2 契约，不能只把契约写在代码里。

    `baseline.py` 支持 SCHEMA=2 不等于产物升级过 —— 核查时发现默认基线
    仍是 schema 1、context 为空，于是「数字 + 依赖/输入/预热/重复次数/
    单位口径一起归档」这条要求实际上没有落地。这条用例把它锁住。
    """
    d = B.load()                       # 明确读默认基线文件，不用临时路径
    assert d.get("schema") == B.SCHEMA, (
        "默认基线 schema=%r，期望 %r；请用 WN_PERF_UPDATE_BASELINE=1 "
        "pytest -m perf 重新生成" % (d.get("schema"), B.SCHEMA))
    for key in ("spec_hash", "fingerprint", "version", "build",
                "dependencies", "inputs", "warmup", "repeats",
                "unit_convention", "ci"):
        assert key in d, "默认基线缺 v2 上下文字段 %r" % key
    assert set(d["inputs"]) == set(B.WORKLOAD)
    assert d["unit_convention"]["kbps"] == "1 kbps = 1024 bit/s"
    assert d["metrics"], "默认基线不能没有指标样本"
