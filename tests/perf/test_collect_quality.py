# -*- coding: utf-8 -*-
"""采集质量自证的单测：离散度、污染轮次、三方对账。

为什么这些也要单测
------------------
2026-11-13 的两次事故都不是「代码算错」，而是「采集时没人知道数据已经不可用」：

1. 重测时并行跑了 Java 编译与 pytest，`direct_p1` 从 16 M 掉到 13.5 M，
   事后靠人回忆才判定该批作废；
2. `-P 8` 的样本里混着 2459 / 2918 kbps 的离群轮，但中位数与 p95 看起来
   和稳定批次**一模一样**，差点被当成达标写进文档。

这两个坑的共同点是：旧统计只有「中位数 + 极值」，无法表达「这批能不能用」。
下面的用例把新增的判据钉死，防止有人为了「让数字好看」把阈值放宽或删掉。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from perf import baseline as B                      # noqa: E402
from perf_iperf import _quality, _suspect_rounds    # noqa: E402

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------ 离散度

def test_stable_and_bimodal_share_median_but_differ_in_cv():
    """核心用例：中位数相同、极值相同，但一个是稳定批一个是双峰批。

    旧统计（median/p95/min/max）对这两组给出**完全相同**的结论，
    这正是当年把 2459 kbps 的离群轮当成正常的原因。
    """
    stable = B.stats([7946, 7946, 7946, 7946, 7946])
    bimodal = B.stats([7946, 2459, 7946, 2918, 7946])

    assert stable["median"] == bimodal["median"] == 7946
    assert stable["p95"] == bimodal["p95"] == 7946      # 都是 max，看不出区别
    assert stable["cv_pct"] == 0.0
    assert bimodal["cv_pct"] > 30                        # 一眼看出不可用

    assert B.is_unstable(stable)[0] is True
    assert B.is_unstable(bimodal)[0] is False


def test_p95_is_flagged_as_extreme_for_small_samples():
    """n<20 时最近秩 p95 就是 max，必须显式标注，否则会被读成分位估计。"""
    assert B.stats([1, 2, 3, 4, 5])["p95_is_extreme"] is True
    assert B.stats(list(range(25)))["p95_is_extreme"] is False


def test_single_sample_reports_unknown_not_zero():
    """单样本的离散度是「未知」，不是「完全稳定」。"""
    st = B.stats([8000])
    assert st["n"] == 1
    assert st["stdev"] is None and st["cv_pct"] is None and st["mad"] is None
    assert B.is_unstable(st)[0] is False


def test_empty_stats_never_fabricates_zero():
    st = B.stats([])
    assert st["n"] == 0
    assert st["median"] is None and st["stdev"] is None


def test_cv_uses_median_not_mean():
    """cv 用中位数作分母：均值会被单个离群轮拉高，掩盖不稳定性。"""
    xs = [8000, 8000, 8000, 8000, 800]
    st = B.stats(xs)
    assert st["cv_pct"] == pytest.approx(st["stdev"] / 8000 * 100, rel=1e-9)


# -------------------------------------------------------------- 污染轮次

def test_suspect_detects_injected_load():
    """某一轮 CPU 突然被抢（30% vs 基线 8%）→ 必须被标出来。"""
    sus, med, mad = _suspect_rounds([8.0, 9.0, 30.0, 7.5, 8.5])
    assert sus == [2]
    assert med == pytest.approx(8.5)


def test_suspect_does_not_flag_high_but_even_load():
    """回环高速档本来就把 CPU 打到 60%+：整批都高不算污染。

    只看「中位 + 3×MAD」会把 65% 的正常轮误判成异常（MAD 很小时尤甚），
    所以判据里必须有「比中位高出足够多」这一条。
    """
    sus, _med, _mad = _suspect_rounds([62.0, 64.0, 63.0, 66.0, 61.0])
    assert sus == []


def test_suspect_respects_absolute_floor():
    """整批都很空闲时，1%→3% 的抖动不该被放大成「污染」。"""
    sus, _med, _mad = _suspect_rounds([1.0, 1.5, 3.0, 1.2, 1.1])
    assert sus == []


def test_suspect_needs_enough_samples():
    """两轮无法判断谁是离群点，宁可不下结论。"""
    sus, med, mad = _suspect_rounds([8.0, 90.0])
    assert sus == [] and med == 8.0 and mad is None


# ------------------------------------------------------------ 质量结论

def _rounds(cpu, eng=None, kbps=None):
    eng = eng or [None] * len(cpu)
    kbps = kbps or [8000] * len(cpu)
    return [{"i": i + 1, "cpu_busy_pct": c, "engine_kbps": e, "kbps_1024": k}
            for i, (c, e, k) in enumerate(zip(cpu, eng, kbps))]


def test_quality_marks_contaminated_batch_unusable():
    """CPU 被抢**且**该轮吞吐也跟着掉了 → 整批不可用。"""
    st = B.stats([8000, 8000, 5000, 8000, 8000])
    q = _quality(st, _rounds([8, 9, 30, 7, 8], kbps=[8000, 8000, 5000, 8000, 8000]),
                 via=True)
    assert q["usable"] is False
    assert q["suspect_rounds"] == [3]                    # 1-based 轮号
    assert q["suspect_biased_rounds"] == [3]
    assert "外部抢占" in q["reason"]


def test_quality_keeps_batch_when_load_did_not_bias_result():
    """CPU 偏高但吞吐仍在中位附近 → 不作废整批。

    实测 2026-11-14：`weaknet_p8` 第 1 轮 CPU 47.6%（其余 20~27%，上一档余温），
    该轮吞吐 8,032 就在中位 8,007 旁边，整批 cv 0.73%。若按「污染即作废」，
    一批完全干净的数据会被判死；判据频繁误报的后果是字段被整体忽略。
    """
    st = B.stats([8032, 8007, 8090, 7927, 8007, 7933, 8042])
    q = _quality(st, _rounds([47.6, 26.9, 27.1, 21.7, 20.3, 23.1, 24.1]),
                 via=True)
    assert q["usable"] is True
    assert q["suspect_rounds"] == [1]        # 仍记录，供追溯
    assert q["suspect_biased_rounds"] == []  # 但没有真的偏
    assert "不影响结论" in q["reason"]


def test_quality_unusable_when_cv_exceeds_limit():
    """cv 超限独立于污染轮：整批都在抖，同样不能用。"""
    st = B.stats([8000, 3000, 8000, 3200])
    q = _quality(st, _rounds([8, 8, 8, 8]), via=True)
    assert q["usable"] is False
    assert "变异系数" in q["reason"]


def test_quality_reports_engine_crosscheck():
    """引擎自计与 iperf3 接收端应当吻合；差太多说明链路或口径出了问题。

    正常值略微为正（引擎含 CONNECT 握手与重传、按墙钟算），所以判定区间
    是 -5% ~ +12%，不是「越接近 0 越好」。
    """
    st = B.stats([8000, 8000, 8000])
    q = _quality(st, _rounds([8, 8, 8], eng=[8050, 8040, 8060]), via=True)
    assert q["usable"] is True
    assert q["engine_median_kbps"] == pytest.approx(8050, abs=20)
    assert abs(q["engine_vs_received_pct"]) < 2
    assert q["engine_crosscheck_ok"] is True


def test_quality_flags_engine_mismatch():
    """引擎比接收端低 30%：两套计数器对不上，必须标出来。"""
    st = B.stats([8000, 8000, 8000])
    q = _quality(st, _rounds([8, 8, 8], eng=[5600, 5600, 5600]), via=True)
    assert q["engine_crosscheck_ok"] is False


def test_quality_skips_engine_gap_for_direct_scenarios():
    """直连档引擎未参与，不该拿 0 去算「偏差 100%」。"""
    st = B.stats([19000000, 19100000])
    q = _quality(st, _rounds([60, 61], eng=[0.0, 0.0]), via=False)
    assert q["engine_vs_received_pct"] is None
