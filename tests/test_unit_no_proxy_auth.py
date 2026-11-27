# -*- coding: utf-8 -*-
"""回归：代理层不得有任何鉴权（手机 Wi-Fi 原生代理无凭据输入口）。

历史：2026-11-26 曾给非回环监听加 Proxy-Authorization 访问码，结果 Android
原生 Wi-Fi 代理设置（只有主机名/端口/绕过列表三个字段）无法提供凭据，
「手机 Wi-Fi 直连」这条主路径全部返回 407，客户端侧无法补救。已整段删除
（Python 的 proxy_token/407 分支、Android 的 token/407 分支）。

这些断言守住「不要再无意中把鉴权加回来」：误加时 CI 必须失败，
而不是等真机用户报告「连不上」。
"""
import pytest

import weaknet as W

pytestmark = pytest.mark.unit


def test_engine_has_no_proxy_token_state():
    """引擎状态里不应再存在任何代理访问码字段。"""
    assert not hasattr(W.G, "proxy_token")


def test_no_proxy_407_constant():
    """不应存在 407 响应常量（有它就意味着某处仍在拒未授权请求）。"""
    assert not hasattr(W, "PROXY_407")


def test_status_payload_has_no_token():
    """/api/status 载荷不得下发 token 字段（老客户端会据此去填凭据）。"""
    payload = W.status_payload()
    listen = payload.get("listen", {})
    assert "token" not in listen
    assert "token" not in payload


def test_source_has_no_proxy_auth_branch():
    """源码级兜底：可执行代码里不再出现 Proxy-Authorization 头。

    行为测试测不到「根本没被触发的分支」，这里直接扫源码，防止有人只加了
    代码没加测试、又恰好绕过前面几条状态断言（例如用局部变量存 token）。
    只扫 Proxy-Authorization：407 响应常量由 test_no_proxy_407_constant 单独守。
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "weaknet.py").read_text(
        encoding="utf-8")
    # 注释里允许提及历史（修复说明会写到），但可执行代码不得出现
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "Proxy-Authorization" not in stripped, stripped
