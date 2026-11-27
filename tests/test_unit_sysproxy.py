# -*- coding: utf-8 -*-
"""系统代理残留检测（崩溃 / taskkill 后注册表仍指向死端口）。

正常退出走 atexit 还原，被强杀时那段代码不会执行，整机就断网了。
这里用假 winreg 验证「认得出本程序的残留」与「不乱动别人的代理」，
不碰真实注册表。
"""
import sys
import types

import pytest

import weaknet as W

pytestmark = pytest.mark.unit


def _fake_winreg(values):
    class _Key:
        pass

    def _query(_k, name):
        if name not in values:
            raise OSError("no such value")
        return values[name]

    return types.SimpleNamespace(
        HKEY_CURRENT_USER=1, REG_DWORD=4, REG_SZ=1,
        OpenKey=lambda *a, **k: _Key(),
        CloseKey=lambda k: None,
        QueryValueEx=_query,
        SetValueEx=lambda *a, **k: None,
    )


def _patch_env(monkeypatch, values, listening=False):
    called = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(values))
    monkeypatch.setattr(W, "set_sysproxy", lambda e: called.append(e) or True)
    monkeypatch.setattr(W, "can_bind", lambda p, h: not listening)
    monkeypatch.setattr(W, "log", lambda *a, **k: None)
    monkeypatch.setattr(W, "log_event", lambda *a, **k: None)
    return called


def test_restores_residue_pointing_at_own_port(monkeypatch):
    called = _patch_env(monkeypatch, {
        "ProxyEnable": (1, 4), "ProxyServer": ("127.0.0.1:8888", 1)})
    assert W.release_stale_sysproxy(8888) is True
    assert called == [False], "残留没被还原"


def test_leaves_other_proxy_alone(monkeypatch):
    called = _patch_env(monkeypatch, {
        "ProxyEnable": (1, 4), "ProxyServer": ("10.0.0.9:3128", 1)})
    assert W.release_stale_sysproxy(8888) is False
    assert called == []


def test_skips_when_port_still_listening(monkeypatch):
    """端口还有人监听 = 另一个实例在跑，它的接管是有效的，不能动。"""
    called = _patch_env(monkeypatch, {
        "ProxyEnable": (1, 4), "ProxyServer": ("127.0.0.1:8888", 1)},
        listening=True)
    assert W.release_stale_sysproxy(8888) is False
    assert called == []


def test_skips_when_proxy_disabled_or_port_zero(monkeypatch):
    called = _patch_env(monkeypatch, {
        "ProxyEnable": (0, 4), "ProxyServer": ("127.0.0.1:8888", 1)})
    assert W.release_stale_sysproxy(8888) is False
    assert W.release_stale_sysproxy(0) is False     # 端口由系统分配，不认残留
    assert called == []
