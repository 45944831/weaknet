# -*- coding: utf-8 -*-
"""设备前提解析的单元测试（纯函数，不需要真机）。

守住的口径：
  * 有 IPv6 默认路由但 tun0 只有 inet → 必须判 ipv6_bypass；
  * tun0 只有 fe80:: link-local → **仍算绕过**（link-local 不能出网）；
  * tun0 有全局 IPv6（2xxx/3xxx）→ 才算接管，不判绕过；
  * 没有 tun → no-tun，不能误报成「绕过」；
  * DoT（opportunistic/hostname）必须标 dns_may_bypass_tun。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import device_premise as D  # noqa: E402

TUN_V4 = """5: tun0: <POINTOPOINT,MULTICAST,NOARP,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UNKNOWN group default qlen 500
    link/none
    inet 10.8.0.2/24 scope global tun0
       valid_lft forever preferred_lft forever
"""

# 真机 PEYM00 的实际形态：只有内核自动配的 link-local，没有全局地址
TUN_V4_LINKLOCAL = TUN_V4 + """    inet6 fe80::9864:2349:491:5818/64 scope link stable-privacy
       valid_lft forever preferred_lft forever
"""

TUN_V4_GLOBAL6 = TUN_V4 + """    inet6 2408:8207:1234:5678::2/64 scope global
       valid_lft forever preferred_lft forever
    inet6 fe80::1/64 scope link
       valid_lft forever preferred_lft forever
"""

V6_DEFAULT = "default via fe80::1 dev wlan0 proto ra metric 1024 pref medium"


def test_ipv6_bypass_when_tun_has_only_v4():
    p = D.parse_premise(TUN_V4, V6_DEFAULT, "off", "34")
    assert p["tun_present"] is True
    assert p["tun_ipv4"] is True and p["tun_ipv6"] is False
    assert p["tun_mtu"] == 1500
    assert p["ipv6_default_route"] is True
    assert p["ipv6_bypass"] is True
    assert p["scope"] == "ipv4-only(ipv6-bypass)"
    assert p["sdk"] == 34


def test_link_local_does_not_mask_bypass():
    """fe80:: 只是内核给接口自动配的，不能出网：有 v6 出口时仍必须判绕过。

    早期实现把 `inet6` 一律当成「tun 已接管 v6」，这条会失败（假阴性）。
    """
    p = D.parse_premise(TUN_V4_LINKLOCAL, V6_DEFAULT, "off", "34")
    assert p["tun_ipv6"] is True          # 确实有 inet6 行
    assert p["tun_ipv6_global"] is False  # 但不是全局地址
    assert p["ipv6_bypass"] is True
    assert p["scope"] == "ipv4-only(ipv6-bypass)"


def test_no_bypass_when_tun_carries_global_v6():
    p = D.parse_premise(TUN_V4_GLOBAL6, V6_DEFAULT, "off", "34")
    assert p["tun_ipv6_global"] is True
    assert p["ipv6_bypass"] is False
    assert p["scope"] == "ipv4+ipv6"


def test_link_local_without_v6_route_is_ipv4_only():
    """没有 v6 默认路由时，link-local 不该把 scope 说成 ipv4+ipv6。"""
    p = D.parse_premise(TUN_V4_LINKLOCAL, "", "off", "33")
    assert p["ipv6_bypass"] is False
    assert p["scope"] == "ipv4-only(no-ipv6-route)"


def test_no_tun_is_not_bypass():
    """没有 tun 时「绕过」不成立：此时样本根本不该被采集。"""
    p = D.parse_premise("", V6_DEFAULT, "off", "34")
    assert p["tun_present"] is False
    assert p["ipv6_bypass"] is False
    assert p["scope"] == "no-tun"


def test_no_v6_route_is_ipv4_only_not_bypass():
    p = D.parse_premise(TUN_V4, "", "off", "34")
    assert p["ipv6_default_route"] is False
    assert p["ipv6_bypass"] is False
    assert p["scope"] == "ipv4-only(no-ipv6-route)"


def test_private_dns_modes():
    assert D.parse_premise(TUN_V4, "", "off", "34")["dns_may_bypass_tun"] is False
    assert D.parse_premise(TUN_V4, "", "opportunistic", "34")["dns_may_bypass_tun"] is True
    assert D.parse_premise(TUN_V4, "", "hostname", "34")["dns_may_bypass_tun"] is True


def test_private_dns_null_literal_is_none():
    """Android 未设置时返回字面量 "null"，不能当成一个模式名写进前提。"""
    p = D.parse_premise(TUN_V4, "", "null", "34")
    assert p["private_dns_mode"] is None
    assert p["dns_may_bypass_tun"] is False
