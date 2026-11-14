# -*- coding: utf-8 -*-
"""桌面 GUI 刷新帧统计（P2-3 的 Windows 侧）。

Android 浮窗有 `FrameStats.java`，桌面侧一直是空白：刷新是 500ms 定时器驱动的，
「一帧卡了多久」被 500ms 的粒度完全盖住 —— 用户只看到数字不动，分不清是弱网本身
慢、还是界面线程被某个同步调用堵住了。

这里与 Java 版保持同一套口径（同样的窗口、同样的 16ms / 100ms 阈值、同样的
中位/p95/最大 + 计数），只是换成 Python 的单调时钟与浮点毫秒：

* `record(at_ms, cost_ms)`：记一帧的**开始时刻**与**本轮自身耗时**；
  「间隔」由相邻两帧的开始时刻算出，包含定时器调度延迟 —— 那正是用户感知的卡。
* `snapshot()`：返回窗口内的统计（dict），可序列化，便于归档成 JSON。
* `summary()`：一行日志。

纯逻辑、不 import tkinter，因此可以脱离窗口单测（tests/test_gui_perf.py）。
"""
import math
from collections import deque

# 一帧的预算（60fps）。超过它算「掉帧」。
FRAME_BUDGET_MS = 16.0
# 明显卡顿的阈值。
STALL_MS = 100.0
# 默认窗口：500ms 一次刷新 × 120 次 ≈ 60 秒。
DEFAULT_WINDOW = 120


def _p95_index(n):
    """与 FrameStats.java 完全一致：ceil(n*0.95)-1，夹在 [0, n-1]。"""
    i = int(math.ceil(n * 0.95)) - 1
    return min(max(i, 0), n - 1)


class FrameStats:
    """滑动窗口内的刷新间隔 / 单帧耗时统计。"""

    def __init__(self, window=DEFAULT_WINDOW, log_every=60):
        self.window = max(1, int(window))
        self.log_every = max(0, int(log_every))
        self._ats = deque()
        self.frames = 0          # 累计帧数
        self.dropped = 0         # 因超出窗口被丢弃的样本数
        self._last = None
        self._gap_sum = 0.0
        self._gap_count = 0
        self._gap_max = 0.0
        self._gap_min = float("inf")
        self._cost_sum = 0.0
        self._cost_max = 0.0
        self.over_frame = 0      # 单帧耗时 > 16ms
        self.over_stall = 0      # 单帧耗时 > 100ms

    def record(self, at_ms, cost_ms):
        """记一帧。返回 True 表示「该打一条日志了」（每 log_every 帧一次）。"""
        if self._last is not None:
            gap = at_ms - self._last
            if gap >= 0:
                self._gap_sum += gap
                self._gap_count += 1
                self._gap_max = max(self._gap_max, gap)
                self._gap_min = min(self._gap_min, gap)
        self._last = at_ms
        self.frames += 1

        cost = max(0.0, float(cost_ms))
        self._cost_sum += cost
        self._cost_max = max(self._cost_max, cost)
        if cost > FRAME_BUDGET_MS:
            self.over_frame += 1
        if cost > STALL_MS:
            self.over_stall += 1

        self._ats.append(at_ms)
        while len(self._ats) > self.window:
            self._ats.popleft()
            self.dropped += 1

        return bool(self.log_every) and self.frames % self.log_every == 0

    def snapshot(self):
        """窗口内统计。字段名与 FrameStats.Snapshot 对齐，便于双端对照。"""
        ats = list(self._ats)
        gaps = sorted(b - a for a, b in zip(ats, ats[1:]))
        avg_gap = self._gap_sum / self._gap_count if self._gap_count else 0.0
        cost_avg = self._cost_sum / self.frames if self.frames else 0.0
        return {
            "frames": self.frames,
            "dropped": self.dropped,
            "windowed": len(ats),
            "gap_min_ms": gaps[0] if gaps else 0.0,
            "gap_median_ms": gaps[len(gaps) // 2] if gaps else 0.0,
            "gap_p95_ms": gaps[_p95_index(len(gaps))] if gaps else 0.0,
            "gap_max_ms": self._gap_max,
            "gap_avg_ms": avg_gap,
            "cost_avg_ms": cost_avg,
            "cost_max_ms": self._cost_max,
            "over_frame": self.over_frame,
            "over_stall": self.over_stall,
            # 按平均间隔折算的刷新频率（次/秒）
            "per_sec": (1000.0 / avg_gap) if avg_gap > 0 else 0.0,
        }

    def summary(self):
        s = self.snapshot()
        return ("帧 %d（窗口 %d，丢弃 %d）｜间隔 中位 %.0fms / p95 %.0fms / 最大 %.0fms"
                "｜单帧耗时 均 %.1fms / 最大 %.1fms｜超一帧 %d 次 / 超 %.0fms %d 次"
                "｜约 %.1f 次/秒" % (
                    s["frames"], s["windowed"], s["dropped"],
                    s["gap_median_ms"], s["gap_p95_ms"], s["gap_max_ms"],
                    s["cost_avg_ms"], s["cost_max_ms"],
                    s["over_frame"], STALL_MS, s["over_stall"], s["per_sec"]))
