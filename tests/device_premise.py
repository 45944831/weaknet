# -*- coding: utf-8 -*-
"""Android 侧采集前提快照：TUN 是否接管了 IPv6、Private DNS 模式、MTU。

为什么必须单独做这一层
----------------------
`VpnNetService` 的 Builder 只有 `addRoute("0.0.0.0", 0)`，转发解析器也明确
`if ((pkt[0] >> 4) != 4) return`（仅 IPv4）。这意味着：

* 双栈 App 若走 IPv6，**流量根本不经过 tun0**，弱网参数对它无效；
* Private DNS（DoT）与浏览器 DoH 也可能绕过 tun 的 DNS 设置。

如果采集数据不带这两项，「弱网没生效」和「流量没被接管」在事后完全无法区分——
两者都会表现为「测出来很快」。所以每个 Android 样本前先落一份前提快照。

注意：这里**只做观测，不改路由**。给 Builder 直接加 `::/0` 会让 IPv6 包进 tun
后被解析器丢弃，双栈 App 反而断网；真支持 IPv6 需要完整的 v6 头/ICMPv6/NAT 实现。
"""
import json
import os
import re
import subprocess
import sys
import time

ADB_BIN = os.environ.get("WEAKNET_ADB") or "adb"


def _adb(serial=None):
    return [ADB_BIN] + (["-s", serial] if serial else [])


def sh(cmd, timeout=10, serial=None):
    """执行一条 adb shell 命令；失败返回空串（前提观测不该让采集崩溃）。"""
    try:
        p = subprocess.run(_adb(serial) + ["shell", cmd], capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8",
                           errors="replace", stdin=subprocess.DEVNULL)
    except Exception:
        return ""
    return (p.stdout or "").strip()


def _first_int(pattern, text):
    m = re.search(pattern, text or "")
    return int(m.group(1)) if m else None


def parse_premise(tun_addr="", route6="", dns_mode="", sdk="", tun_name="tun0"):
    """把 adb 原始输出解析成前提结论（纯函数，便于单测，不碰设备）。

    参数都是 `ip` / `settings` 的原样输出，空串表示命令失败或没有该项。
    """
    tun_addr = tun_addr or ""
    route6 = route6 or ""
    dns_mode = (dns_mode or "").strip().lower()
    # Android 的 settings 在未设置时返回字面量 "null"（不是空串），
    # 直接当模式名会写出 private_dns_mode="null" 这种既非 off 也非 DoT 的假值。
    if dns_mode in ("", "null", "unknown"):
        dns_mode = None
    sdk_i = None
    try:
        sdk_i = int((sdk or "").strip())
    except (TypeError, ValueError):
        pass

    # `ip -o addr show tun0` 一行里可能同时有 inet 与 inet6。
    # 关键区分：任何接口（含 tun）内核都会自动配一个 fe80::/64 的 link-local，
    # 它**不能出网**。早期版本把 inet6 一律当成「tun 已接管 IPv6」，于是：
    #   设备有 v6 默认路由但 tun 只有 fe80 → ipv6_bypass 被误判为 False，
    #   真实存在的「v6 走物理网、绕过弱网」被掩盖成「已覆盖」。
    # 真机 PEYM00 上正是 fe80 这一种，故按 global / link-local 分开判定。
    tun_present = bool(tun_addr.strip()) and tun_name in tun_addr
    has_v4 = bool(re.search(r"\binet\s+\d", tun_addr))
    v6_addrs = re.findall(r"\binet6\s+([0-9a-fA-F:]+)", tun_addr)
    v6_global = [a for a in v6_addrs
                 if not a.lower().startswith("fe80")
                 and a.lower() not in ("::1", "::")]
    has_v6 = bool(v6_addrs)
    has_v6_global = bool(v6_global)
    mtu = _first_int(r"\bmtu\s+(\d+)", tun_addr)

    # 有 IPv6 默认路由但 tun0 没有**全局** IPv6 地址 → v6 走物理网，绕过弱网
    v6_default = bool(re.search(r"\bdefault\b", route6))
    bypass = bool(tun_present and v6_default and not has_v6_global)

    if not tun_present:
        scope = "no-tun"
    elif has_v6_global:
        scope = "ipv4+ipv6"
    elif v6_default:
        scope = "ipv4-only(ipv6-bypass)"
    else:
        scope = "ipv4-only(no-ipv6-route)"

    return {
        "tun_present": tun_present,
        "tun_ipv4": has_v4,
        "tun_ipv6": has_v6,
        "tun_ipv6_global": has_v6_global,
        "tun_mtu": mtu,
        "ipv6_default_route": v6_default,
        # True = 设备有 v6 出口但 tun 未接管，样本只覆盖 IPv4 子集
        "ipv6_bypass": bypass,
        "private_dns_mode": dns_mode or None,
        # opportunistic / hostname 都可能启用 DoT：DNS 阶段未必经过 tun
        "dns_may_bypass_tun": dns_mode in ("opportunistic", "hostname"),
        "sdk": sdk_i,
        "scope": scope,
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def snapshot(serial=None):
    """读设备当前状态。命令失败时对应字段为空，不抛异常。"""
    # 用 `ip addr show`（非 -o）：只有这个格式同时带首行的 mtu 和后续 inet/inet6
    tun_addr = sh("ip addr show tun0 2>/dev/null", serial=serial)
    route6 = sh("ip -6 route show default 2>/dev/null", serial=serial)
    dns_mode = sh("settings get global private_dns_mode 2>/dev/null", serial=serial)
    sdk = sh("getprop ro.build.version.sdk 2>/dev/null", serial=serial)
    return parse_premise(tun_addr, route6, dns_mode, sdk)


def main():
    serial = sys.argv[1] if len(sys.argv) > 1 else None
    snap = snapshot(serial)
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    # 退出码用于采集前门禁：只在「确实存在 v6 出口但没接管」时判失败，
    # 设备本身没有 IPv6 不算缺陷。
    return 1 if snap.get("ipv6_bypass") else 0


if __name__ == "__main__":
    sys.exit(main())
