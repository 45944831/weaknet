# -*- coding: utf-8 -*-
"""P2-3 桌面侧帧统计 gui_perf.py 的回归：与 Android FrameStats.java 同口径。

这些断言故意对齐 Java 单测（android/_jw/.../_FrameStatsTest.java）里的几条：
窗口丢弃、p95 取位、超一帧/超 100ms 计数。双端同一套算法，数字才有可比性。
"""
import pytest

import gui_perf as P

pytestmark = pytest.mark.unit


def test_interval_and_cost_are_tracked():
    s = P.FrameStats(window=10)
    s.record(1000.0, 2.0)
    s.record(1500.0, 4.0)
    s.record(2000.0, 40.0)
    snap = s.snapshot()
    assert snap["frames"] == 3
    assert snap["gap_median_ms"] == 500.0
    assert snap["gap_max_ms"] == 500.0
    assert snap["cost_max_ms"] == 40.0
    assert snap["over_frame"] == 1      # 40ms > 16ms
    assert snap["over_stall"] == 0
    assert snap["per_sec"] == pytest.approx(2.0, abs=0.01)


def test_first_frame_has_no_interval():
    s = P.FrameStats()
    s.record(1000.0, 1.0)
    assert s.snapshot()["gap_median_ms"] == 0.0
    assert s.snapshot()["frames"] == 1


def test_window_drops_oldest_samples():
    s = P.FrameStats(window=3)
    for i in range(5):
        s.record(i * 100.0, 1.0)
    snap = s.snapshot()
    assert snap["windowed"] == 3
    assert snap["dropped"] == 2
    # 窗口里只剩 2/3/4 三帧 → 两个间隔，都是 100ms
    assert snap["gap_median_ms"] == 100.0


def test_p95_index_matches_java():
    """Java: ceil(n*0.95)-1；n=20 → 18，n=1 → 0。"""
    assert P._p95_index(20) == 18
    assert P._p95_index(1) == 0
    assert P._p95_index(3) == 2


def test_log_every_n_frames():
    s = P.FrameStats(window=10, log_every=3)
    assert [s.record(i * 500.0, 1.0) for i in range(4)] == [False, False, True, False]


def test_negative_cost_is_clamped():
    s = P.FrameStats()
    s.record(0.0, -5.0)
    assert s.snapshot()["cost_max_ms"] == 0.0


def test_summary_contains_all_sections():
    s = P.FrameStats()
    s.record(0.0, 1.0)
    s.record(500.0, 2.0)
    text = s.summary()
    for part in ("帧 2", "间隔 中位", "p95", "单帧耗时", "超一帧", "次/秒"):
        assert part in text
