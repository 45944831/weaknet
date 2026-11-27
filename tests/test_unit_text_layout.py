# -*- coding: utf-8 -*-
"""text_layout 独立单测（从 weaknet.py 抽出后的回归锚点）。

这些规则以前只能通过 `W.wrap_note_lines` 间接测，现在直接测纯函数：
断行位置错一位、标点被甩到行首这类问题，会立刻在这里暴露。
"""
import pytest

import weaknet as W
import text_layout as T

pytestmark = pytest.mark.unit

# 与 weaknet.py 的 `from text_layout import (...)` re-export 清单同源：
# 新增/改名漏同步时，存量脚本里的 W.xxx 会在运行期 AttributeError。
REEXPORTED = (
    "split_note_atoms", "wrap_note_lines", "_is_note_cjk", "_is_note_word_char",
    "_note_group_units", "_note_units_text", "_note_safe_chunks", "_note_hard_split",
    "_NOTE_QUOTE_PAIRS", "_NOTE_NO_START", "_NOTE_NO_END", "_NOTE_NO_START_GLUE",
    "_NOTE_WORD_CONNECT", "_NOTE_SPACE_CHARS",
)


def test_weaknet_reexports_every_text_layout_symbol():
    assert all(hasattr(W, name) for name in REEXPORTED), \
        [name for name in REEXPORTED if not hasattr(W, name)]


def _measure_fixed(width=1.0):
    """每个字符按固定宽度计，便于精确断言断行位置。"""
    return lambda s: len(s) * width


def test_split_atoms_keeps_quote_phrase_together():
    """「本机 → 服务器」是引号短语，不能在中间被劈开。

    实现上它已经整段成为一个原子（不是逐字切），所以断言 len==1；
    这正是「引号短语不被从中间劈开」的底层保证。
    """
    text = "「本机 → 服务器」"
    atoms = T.split_note_atoms(text)
    assert len(atoms) == 1
    assert atoms[0][1] == text


def test_wrap_never_starts_line_with_closing_punctuation():
    text = "这是一段用来测试折行的中文说明，逗号不能出现在行首。"
    lines = T.wrap_note_lines(text, _measure_fixed(1.0), 10)
    assert lines and all(ln for ln in lines)
    for ln in lines:
        assert ln[0] not in T._NOTE_NO_START, "行首出现了禁则标点：%r" % ln
        assert ln[-1] not in T._NOTE_NO_END, "行尾出现了禁则开引号：%r" % ln


def test_wrap_fits_single_line_when_wide_enough():
    text = "短说明"
    assert T.wrap_note_lines(text, _measure_fixed(1.0), 100) == [text]


def test_wrap_handles_empty_and_none():
    assert T.wrap_note_lines("", _measure_fixed(), 10) == []
    assert T.wrap_note_lines(None, _measure_fixed(), 10) == []


def test_hard_split_breaks_overlong_word():
    """没有空格的超长英文/URL 必须硬切，否则会溢出容器。"""
    word = "a" * 40
    lines = T.wrap_note_lines(word, _measure_fixed(1.0), 10)
    assert len(lines) >= 4
    assert "".join(lines) == word


def test_cjk_detection():
    assert T._is_note_cjk("中")
    assert not T._is_note_cjk("a")
    assert not T._is_note_cjk(" ")


def test_wrap_normalizes_runs_of_spaces():
    """连续空格/制表符归一为单个分隔，且任何一行都不以空格开头。

    二次审查指出的边界：跨行空格若没归一，会出现「行首空格」把版式顶歪。
    """
    lines = T.wrap_note_lines("a  b\t\tc   d", _measure_fixed(1.0), 3)
    assert lines
    for ln in lines:
        assert not ln.startswith(" "), "行首出现空格：%r" % ln
        assert "  " not in ln and "\t" not in ln, "行内残留连续空白：%r" % ln
    assert "".join(lines).replace(" ", "") == "abcd"


def test_wrap_consecutive_closing_punctuation_stays_off_line_start():
    """连续禁则标点（，，。）不能因为逐字折行被甩到下一行行首。

    二次审查指出的边界：单标点用例过不了「连续标点」这种组合。
    """
    lines = T.wrap_note_lines("中文测试，，。结束", _measure_fixed(1.0), 4)
    assert lines
    for ln in lines:
        assert ln[0] not in T._NOTE_NO_START, "行首出现禁则标点：%r" % ln


def test_wrap_survives_zero_or_negative_measure():
    """measure 返回 0 或负数时不能死循环/除零，必须仍能给出结果。

    二次审查指出的边界：非正宽度是调用方可能传错的输入，排版层要兜住。
    """
    for m in (lambda s: 0, lambda s: -1):
        lines = T.wrap_note_lines("abc", m, 10)
        assert lines and "".join(lines) == "abc"
