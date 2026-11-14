# -*- coding: utf-8 -*-
"""P1-6 视图模型层 gui_state.py 的回归。

这里锁的是「该显示什么」：文本、色调、以及最容易写错的边界（没选中 / 名单为空 /
播放进度不存在）。因为不 import tkinter，这些断言在无显示器的环境里也能跑。
"""
import pytest

from gui_state import (ACCENT, BTN2, MUTED, OK, TEXT, WARN, combo_bar,
                       header_view, light_view, mine_hint, rate_of,
                       run_btn_view, scene_label, scope_btn_views, scope_tip,
                       share_view, stats_view)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- 速率采样

def test_rate_below_threshold_keeps_previous_value():
    """两个采样点挨得太近不要重算：除以极小的 dt 会抖成天文数字。"""
    assert rate_of(100.0, 0, 0, 100.1, 5000, 5000) is None
    assert rate_of(100.0, 0, 0, 100.3, 5000, 5000) is None      # 差 0.3s
    # 刚好到门槛就开始算（浮点误差按 >= 处理，不必让它卡在边界上反复跳）
    assert rate_of(100.0, 0, 0, 100.4, 5000, 5000) is not None


def test_rate_computes_bytes_per_second():
    assert rate_of(100.0, 1000, 2000, 101.0, 1124, 2000) == (124.0, 0.0)


# ---------------------------------------------------------------- 顶栏

def test_header_shows_loopback_when_lan_off():
    v = header_view("127.0.0.1", 8888, "192.168.1.5", 8899, False)
    assert "127.0.0.1:8888" in v["proxy"]
    assert "192.168.1.5:8888" not in v["proxy"]
    assert v["lan_text"] == "开启局域网访问"
    assert v["ctrl"] == "控制接口：http://127.0.0.1:8899/　局域网 IP：192.168.1.5"
    assert v["sys_text"] == "一键接管本机流量" and v["sys_on"] is False


def test_header_shows_lan_ip_when_lan_on():
    v = header_view("0.0.0.0", 8888, "192.168.1.5", 8899, True)
    assert "192.168.1.5:8888" in v["proxy"]
    assert v["lan_text"] == "关闭局域网访问"
    assert v["sys_text"] == "解除接管" and v["sys_on"] is True


# ---------------------------------------------------------------- 看板

def test_scene_label_marks_unstarted_preset():
    assert scene_label("直连（不限制）", "4g", False,
                       lambda k: "4G") == "4G（未开启）"
    assert scene_label("4G", "4g", True, lambda k: "4G") == "4G"
    # 没有任何选中项时按引擎里的 profile 名字显示
    assert scene_label("直连（不限制）", "", False, lambda k: "4G") == "直连（不限制）"


def test_scene_label_falls_back_when_resolver_fails():
    def boom(_k):
        raise KeyError("未知场景")
    assert scene_label("直连（不限制）", "u:xxx", False, boom) == "直连（不限制）"


def test_stats_view_order_and_gate_tone():
    rows = stats_view(12, 3, (2048.0, 0.0), True, "4G")
    assert [r[0] for r in rows] == ["scene", "conns", "active", "down", "up", "gate"]
    d = {r[0]: r[1:] for r in rows}
    assert d["conns"] == ("12", None)
    assert d["down"] == ("2.0 KB/s", None)
    assert d["gate"] == ("畅通", OK)
    assert stats_view(0, 0, (0, 0), False, "-")[-1][1:] == ("已断网", WARN)


# ---------------------------------------------------------------- 状态灯

def test_light_reflects_limit_switch_not_service():
    assert light_view(False, True) == ("● 限速未开启", MUTED)
    assert light_view(True, True) == ("● 限速运行中", OK)
    assert light_view(True, False) == ("● 周期断网中", WARN)


# ---------------------------------------------------------------- 按钮

def test_share_button_shows_pending_count():
    assert share_view(0) == "🔗 环境共享"
    assert share_view(3) == "🔗 环境共享（3 条待处理）"


def test_run_button_is_primary_when_off():
    off = run_btn_view(False)
    assert off == {"text": "▶ 开启限速", "bg": ACCENT, "fg": "white"}
    on = run_btn_view(True)
    assert on["bg"] == BTN2 and on["fg"] == TEXT and "停止限速" in on["text"]


def test_scope_buttons_mark_exactly_one_active():
    v = scope_btn_views(True)
    assert v["all"]["bg"] == ACCENT and v["open"]["bg"] == BTN2
    assert v["all"]["text"].startswith("✔") and not v["open"]["text"].startswith("✔")
    v = scope_btn_views(False)
    assert v["open"]["bg"] == ACCENT and v["all"]["bg"] == BTN2


# ---------------------------------------------------------------- 接管范围摘要

def test_scope_tip_modes():
    assert scope_tip("all", []) == "全部程序"
    assert scope_tip("only", ["a.exe"]) == "仅接管 1 个程序（a.exe）"
    assert scope_tip("only", []) == "仅接管 0 个程序（空名单 = 不接管任何程序）"
    assert scope_tip("exclude", []) == "排除 0 个程序（空名单 = 全部接管）"


def test_scope_tip_truncates_long_list():
    names = ["a.exe", "b.exe", "c.exe", "d.exe", "e.exe"]
    tip = scope_tip("exclude", names)
    assert "排除 5 个程序" in tip and "e.exe" not in tip and tip.endswith("…）")


# ---------------------------------------------------------------- 自定义场景

def test_mine_hint_three_states():
    empty = mine_hint(0, None)
    assert empty["del_text"] is None and "还没有保存的场景" in empty["lab_text"]
    has = mine_hint(2, None)
    assert has["del_text"] is None and "保存后即可在这里点一下切换" in has["lab_text"]
    sel = mine_hint(2, "我的场景")
    assert sel["del_text"] == "🗑 删除当前场景「我的场景」"
    assert "不影响内置预设" in sel["lab_text"]


# ---------------------------------------------------------------- 组合区

def test_combo_bar_four_states():
    assert "还没有组合" in combo_bar(None, None, None, False, 6, has_combos=False)
    assert "点一支组合先选中它" in combo_bar(None, None, None, False, 6, has_combos=True)
    playing = combo_bar("三段", "A→B", {"step": 2, "total": 3, "left": 7,
                                        "loop": True}, True, 6, True)
    assert playing == "▶ 正在播放第 2/3 段，还剩 7 秒（循环）"
    assert combo_bar("三段", "A→B", None, False, 6, True) == "已选中，点「开启限速」后按序播放：A→B"
    assert combo_bar("三段", "A→B", None, True, 6, True) == "A→B"
