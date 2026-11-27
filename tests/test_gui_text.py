# -*- coding: utf-8 -*-
"""gui_text.py 的回归：纯文本层不需要 Tk 窗口也能验证。

这些断言以前只能靠 `_t_gui_notes.py` / `_t_gui_params.py` 这类需要 EXE 或
Tk 的脚本覆盖，拆分后进了默认 pytest 套件。任何格式化/文案改动都会立刻
被 148 项常规回归挡住，而不是等真机 UI 才发现。
"""
import pytest

import weaknet as W
import weaknet_gui as G
from gui_text import (NOTE_PAD, NOTE_TEXT_W, NOTE_TIP_INDENT, NOTE_WIN_W,
                      SHORT, FIELDS, fmt_rate, fmt_bytes, summarize, detail,
                      _cap_name_var)

pytestmark = pytest.mark.unit


def test_main_module_still_reexports_text_layer():
    """存量脚本用 `G.detail(...)`，抽走实现后必须仍然可用。"""
    assert G.detail is detail
    assert G.summarize is summarize
    assert G.fmt_rate is fmt_rate
    assert G.FIELDS is FIELDS
    assert G.SHORT is SHORT


def test_spec_constants_stay_single_source():
    """短名 / 参数表来自 spec，不允许 GUI 自己抄一份。"""
    assert SHORT == W.PRESET_SHORT
    assert FIELDS == [(p["key"], p["label"], p["unit"], p["max"])
                      for p in W.PARAM_SPECS]


def test_note_layout_constants_are_consistent():
    assert NOTE_WIN_W > NOTE_TEXT_W + 2 * NOTE_PAD
    assert NOTE_TIP_INDENT > 0


def test_fmt_rate_and_bytes_boundaries():
    assert fmt_rate(0) == "0 B/s"
    assert fmt_rate(1023) == "1023 B/s"
    assert fmt_rate(1024) == "1.0 KB/s"
    assert fmt_rate(1024 * 1024) == "1.00 MB/s"
    assert fmt_bytes(0) == "0 B"
    assert fmt_bytes(1023) == "1023 B"
    assert fmt_bytes(1024) == "1 KB"
    assert fmt_bytes(1024 ** 2) == "1.0 MB"
    assert fmt_bytes(1024 ** 3) == "1.00 GB"


def test_summarize_empty_and_mixed():
    assert summarize({}) == "（不限制）"
    s = summarize({"down_kbps": 8000, "up_kbps": 0,
                   "latency_down_ms": 200, "latency_up_ms": 200,
                   "loss_pct": 5})
    assert "↓8000/↑∞ kbps" in s
    assert "延迟 200/200 ms" in s
    assert "丢包 5%" in s


def test_detail_stall_only_after_loss():
    """卡顿时长只在丢包后生效，单独给出不该出现在摘要里。"""
    assert detail({}) == "（不限制：所有流量原样直通）"
    assert "丢包后停顿" not in detail({"stall_ms": 800})
    d = detail({"loss_pct": 5, "stall_ms": 800, "jitter_ms": 30,
                "flap_sec": 10, "flap_blackout_sec": 3})
    assert "丢包后停顿 800 ms" in d
    assert "抖动 ±30 ms" in d
    assert "每 10s 断网 3s" in d


class _FakeVar:
    """只实现 _cap_name_var 用到的三个方法，不创建 Tk root。"""

    def __init__(self, value=""):
        self.value = value
        self.traces = []

    def get(self):
        return self.value

    def set(self, v):
        self.value = v

    def trace_add(self, mode, cb):
        self.traces.append((mode, cb))
        return "id"


def test_cap_name_var_truncates_on_write():
    v = _FakeVar()
    _cap_name_var(v)
    assert v.traces and v.traces[0][0] == "write"
    cb = v.traces[0][1]
    v.set("x" * (W.NAME_MAX + 5))
    cb()
    assert len(v.get()) == W.NAME_MAX
    v.set("短名")
    cb()
    assert v.get() == "短名"
