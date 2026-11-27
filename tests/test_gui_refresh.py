# -*- coding: utf-8 -*-
"""GUI 刷新回归：refresh 只在值真的变化时才动控件。

为什么非要起真窗口：脏标记最容易出的错不是「算错」而是「该刷新时没刷新」——
纯函数测试再全也发现不了。这里用真实 Tk 窗口跑 refresh，直接数
`Tk.configure()` 的调用次数：

* 空闲轮（状态没变）必须是 **0 次**，否则就是又退回到「每 500ms 空转」；
* 状态变了必须真的落到控件上，否则就是漏刷。

无显示器的环境（CI / Linux 无 X）自动跳过，不算失败。
"""
import tkinter as tk

import pytest

import weaknet as W
import weaknet_gui as G

pytestmark = pytest.mark.unit

_G_KEYS = ("limit_on", "gate_open", "scope_mode", "preset_key", "proxy_host",
           "proxy_port", "lan_ip", "bytes_down", "bytes_up")


@pytest.fixture(scope="module")
def tk_root():
    """整个模块共用一个 Tk 根窗口。

    每个用例各自 Tk()/destroy() 在本机会偶发 "no display name" 之类的
    TclError（前后窗口销毁与初始化撞车），于是同一个用例有时跳过有时通过。
    共用根窗口、每例只清控件，结果才稳定。
    """
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("没有可用显示，跳过 GUI 冒烟")
    root.withdraw()
    try:
        yield root
    finally:
        try:
            root.destroy()
        except Exception:
            pass


@pytest.fixture
def app(tk_root):
    root = tk_root
    for w in list(root.winfo_children()):
        w.destroy()
    snap = {k: getattr(W.G, k) for k in _G_KEYS}
    snap_scope = list(W.G.scope_list)
    # inbox 是 dict（id → 记录），必须原地清空/回填：换成 list 会让后续用例
    # 在 _inbox_put 里报 "list indices must be integers"
    snap_inbox = dict(W.G.inbox)
    W.G.limit_on = False
    W.G.gate_open = True
    W.G.scope_mode = "all"
    W.G.scope_list = []
    W.G.preset_key = "direct"
    W.G.inbox.clear()
    a = G.App(root, None, 8899)
    try:
        yield a, root
    finally:
        a._closing = True
        for w in list(root.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        for k, v in snap.items():
            setattr(W.G, k, v)
        W.G.scope_list = snap_scope
        W.G.inbox.clear()
        W.G.inbox.update(snap_inbox)


def _tick(a, root, n=1):
    """手动驱动 n 轮 refresh，并把每轮登记的定时器摘掉（不进 mainloop）。"""
    for _ in range(n):
        a.refresh()
        if getattr(a, "_after_id", None):
            try:
                root.after_cancel(a._after_id)
            except Exception:
                pass


@pytest.fixture
def counter(monkeypatch):
    """统计 Tk 真正被下发的 configure 次数。config 与 configure 是两个名字，都要拦。"""
    box = {"n": 0}
    orig = tk.Misc.configure

    def counting(self, cnf=None, **kw):
        box["n"] += 1
        return orig(self, cnf, **kw)

    monkeypatch.setattr(tk.Misc, "configure", counting)
    monkeypatch.setattr(tk.Misc, "config", counting)
    return box


def test_idle_refresh_touches_nothing(app, counter):
    a, root = app
    _tick(a, root, 1)          # 预热：首轮把每项都下发一次，填满缓存
    counter["n"] = 0
    _tick(a, root, 5)
    assert counter["n"] == 0, "空闲轮仍在重配控件，脏标记失效"


def test_limit_switch_is_applied(app):
    a, root = app
    _tick(a, root, 1)
    assert a.btn_run.cget("text") == "▶ 开启限速"

    W.G.limit_on = True
    _tick(a, root, 1)
    assert "停止限速" in a.btn_run.cget("text")
    assert a.dot.cget("text") == "● 限速运行中"

    W.G.gate_open = False
    _tick(a, root, 1)
    assert a.dot.cget("text") == "● 周期断网中"

    W.G.limit_on = False
    _tick(a, root, 1)
    assert a.btn_run.cget("text") == "▶ 开启限速"
    assert a.dot.cget("text") == "● 限速未开启"


def test_scope_summary_and_buttons_follow_state(app):
    a, root = app
    _tick(a, root, 1)
    assert a.lab_scope.cget("text") == "全部程序"

    W.G.scope_mode = "only"
    W.G.scope_list = ["chrome.exe", "wechat.exe"]
    _tick(a, root, 1)
    assert a.lab_scope.cget("text") == "仅接管 2 个程序（chrome.exe, wechat.exe）"
    assert a.scope_open_btn.cget("bg") == G.ACCENT
    assert a.scope_all_btn.cget("bg") == G.BTN2


def test_pending_share_count_is_applied(app):
    a, root = app
    _tick(a, root, 1)
    assert a.btn_share.cget("text") == "🔗 环境共享"

    W.G.inbox["a"] = {"name": "a"}
    W.G.inbox["b"] = {"name": "b"}
    _tick(a, root, 1)
    assert a.btn_share.cget("text") == "🔗 环境共享（2 条待处理）"


def test_tab_paint_follows_switch(app):
    a, root = app
    a.switch_tab("scene")
    assert a.tab_btns["scene"].cget("bg") == G.ACCENT
    assert a.tab_btns["combo"].cget("bg") == G.BTN2
    a.switch_tab("combo")
    assert a.tab_btns["combo"].cget("bg") == G.ACCENT
    assert a.tab_btns["scene"].cget("bg") == G.BTN2


def test_panel_windows_open_and_close(app):
    """面板拆成 mixin 之后的存活检查：方法要能被 App 正常调到，窗口能开能关。"""
    a, _root = app
    a.open_preset_notes()
    assert a.notes_win is not None
    a.close_preset_notes()
    assert a.notes_win is None

    a.open_scope()
    assert a.scope_win is not None
    assert a.close_scope(force=True) is True
    assert a.scope_win is None


def test_set_reports_whether_it_changed(app):
    a, _root = app
    lab = tk.Label(a.root, text="x")
    assert a._set(lab, text="x") is True      # 首次下发：缓存里没有，算变化
    assert a._set(lab, text="x") is False
    assert a._set(lab, text="y") is True
    assert a._set(lab, text="y", fg=G.TEXT) is True   # 新增选项也算变化
    assert a._set(lab, text="y", fg=G.TEXT) is False
