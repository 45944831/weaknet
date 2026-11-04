# -*- coding: utf-8 -*-
"""「场景 / 组合」标签选中特效的即时刷新（Windows 端）。

回归背景：用户反馈「标签选择后特效不会马上刷新」。
tkinter 的 Button 在鼠标悬停时显示的是 activebackground / activeforeground，
只改 bg/fg 的话，鼠标还停在刚点过的按钮上，看到的仍是旧底色 ——
看起来就像「点了没生效」，移开鼠标才变色。

这里用模拟控件直接调 App 的真实方法，不建 Tk 根窗口：
本机 D:/python 的 Tcl 在根窗口销毁后无法再次创建解释器，
pytest 里反复 tk.Tk() 会连带打挂别的 GUI 用例。
"""
from types import SimpleNamespace
from pathlib import Path

import pytest

import weaknet as W
import weaknet_gui as G

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
GUI = (ROOT / "weaknet_gui.py").read_text(encoding="utf-8")


class _Btn:
    def __init__(self):
        self.cfg = {}

    def configure(self, **kw):
        self.cfg.update(kw)


class _Page:
    def __init__(self):
        self.packed = False

    def winfo_manager(self):
        return "pack" if self.packed else ""

    def pack(self, **kw):
        self.packed = True

    def pack_forget(self):
        self.packed = False


@pytest.fixture
def app():
    obj = SimpleNamespace(
        tab_btns={"scene": _Btn(), "combo": _Btn()},
        tab_pages={"scene": _Page(), "combo": _Page()},
        cur_tab="scene", _last_tab_key=None)
    obj._paint_tabs = lambda k: G.App._paint_tabs(obj, k)
    return obj


def test_paint_tabs_sets_normal_and_hover_colors(app):
    G.App._paint_tabs(app, "combo")
    assert app.tab_btns["combo"].cfg == {
        "bg": G.ACCENT, "fg": "white",
        "activebackground": G.ACCENT, "activeforeground": "white"}
    assert app.tab_btns["scene"].cfg == {
        "bg": G.BTN2, "fg": G.MUTED,
        "activebackground": G.BTN2, "activeforeground": G.MUTED}


def test_paint_tabs_is_idempotent_and_reversible(app):
    G.App._paint_tabs(app, "combo")
    G.App._paint_tabs(app, "combo")
    assert app.tab_btns["combo"].cfg["bg"] == G.ACCENT
    G.App._paint_tabs(app, "scene")
    assert app.tab_btns["scene"].cfg["activebackground"] == G.ACCENT
    assert app.tab_btns["combo"].cfg["activeforeground"] == G.MUTED


def test_switch_tab_manual_marks_selection(app, monkeypatch):
    monkeypatch.setattr(W.G, "preset_key", "c:abc")
    G.App.switch_tab(app, "scene", manual=True)
    assert app.cur_tab == "scene"
    assert app._last_tab_key == "c:abc"       # 定时同步看到同 key 就不翻页
    assert app.tab_pages["scene"].packed is True
    assert app.tab_pages["combo"].packed is False
    assert app.tab_btns["scene"].cfg["bg"] == G.ACCENT
    assert app.tab_btns["scene"].cfg["activebackground"] == G.ACCENT


def test_switch_tab_auto_does_not_pin(app, monkeypatch):
    monkeypatch.setattr(W.G, "preset_key", "c:abc")
    app._last_tab_key = "u:old"
    G.App.switch_tab(app, "combo")
    assert app.cur_tab == "combo"
    assert app._last_tab_key == "u:old"       # 自动翻页不改写去重标记
    assert app.tab_pages["combo"].packed is True
    assert app.tab_pages["scene"].packed is False


def test_manual_page_survives_same_key_refresh(app, monkeypatch):
    """手动点过的页，在下一次定时刷新看到同一个选中项时不能被翻回去"""
    monkeypatch.setattr(W.G, "preset_key", "c:abc")
    G.App.switch_tab(app, "scene", manual=True)
    key = W.G.preset_key or ""
    # refresh() 里标签同步的判定：key 没变就不调用 switch_tab，只补一次配色
    if key != app._last_tab_key:
        G.App.switch_tab(app, "combo")
    G.App._paint_tabs(app, app.cur_tab)
    assert app.cur_tab == "scene"
    assert app.tab_btns["scene"].cfg["bg"] == G.ACCENT


def test_refresh_paints_tabs_every_cycle():
    assert "def _paint_tabs" in GUI
    assert "activebackground=bg, activeforeground=fg" in GUI
    assert 'self._paint_tabs(getattr(self, "cur_tab", "scene"))' in GUI
    assert "manual=True" in GUI
    assert "if manual:" in GUI
