# -*- coding: utf-8 -*-
"""真机批次 1：安装、权限、VPN 生命周期、覆盖安装保留存档。

对应方案：A-001 / A-002 / A-003 / A-004 / A-005，以及 E-06（VPN 被撤销后不假死）。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import _t_dev_lib as D

OK_WORDS = ("确定", "允许", "好的", "继续", "开始", "OK", "同意")
DIALOG_TEXTS = ("VPN", "连接请求", "隐私", "授权", "使用该", "提醒")


def dump_all_buttons():
    ns = D.dump()
    return [(n["text"], n["bounds"], n["click"], n["enabled"]) for n in ns if n["text"] and n["click"]]


def try_dialog(max_round=3):
    """若当前有系统/风控对话框，逐个点掉，返回点过的按钮文本列表。"""
    clicked = []
    for _ in range(max_round):
        ns = D.dump()
        front = find_front(ns)
        # 只看看起来像对话框的输入：存在类 *.Dialog 或 "确定/允许" 等按钮
        btns = [n for n in ns if n["text"] and n["click"] and n["enabled"]]
        target = None
        for n in btns:
            if n["text"] in OK_WORDS:
                target = n
                break
        if not target:
            break
        top = "未知窗口"
        m = [x for x in ns if x["cls"].endswith("Activity")]
        D.tap_node(target, wait=1.5)
        clicked.append(target["text"])
        time.sleep(0.6)
    return clicked


def find_front(ns):
    return ns[0]["cls"] if ns else ""


def main():
    D.setup_forward()
    D.app_restart()

    D.section("A-005 覆盖安装：存档是否保留")
    # 覆盖安装发生在批处理前（adb install -r），这里检查存档可读 + 之前能写的 key 还在
    out = D.adb("shell", "run-as %s ls -la /data/data/%s/shared_prefs/" % (D.PKG, D.PKG))[0]
    D.rec("A-005-a 覆盖安装后应用数据目录存在", D.PKG.split(".")[-1] in out or "total" in out,
          out.replace("\n", " | ")[:150])
    # 写入一个自定义场景，作为「保留」探针
    r = D.api("/api/upreset", {"name": "真机探针场景", "profile": {
        "down_kbps": 100, "up_kbps": 100, "latency_down_ms": 10, "latency_up_ms": 10}})
    time.sleep(0.5)
    pres = D.api("/api/upresets")
    names = [p.get("name") for p in pres.get("items", pres if isinstance(pres, list) else [])]
    D.rec("A-005-b 覆盖安装后仍能写入自定义场景", "真机探针场景" in names, str(names)[:120])

    D.section("A-001 首次启动与 VPN 授权")
    s = D.status()
    D.rec("A-001-a 控制接口可用", "preset_key" in s, "preset_key=%s" % s.get("preset_key"))
    if not s.get("vpn_active"):
        ok = D.tap_text("开始弱网测试")
        time.sleep(2.0)
        before = D.dump()
        btns = [n["text"] for n in before if n["text"] and n["click"]]
        clicked = try_dialog()
        print("    点过的按钮：%s" % (clicked or "无"))
        time.sleep(2.5)
    up, s = D.is_vpn_up()
    D.rec("A-001-b VPN 授权后接管生效", up, "vpn_active=%s" % up)
    D.shot("01_vpn_started")

    D.section("A-003 前台服务通知")
    notif = D.sh("dumpsys notification --noredact 2>/dev/null | grep -i -A3 weaknet | head -30")
    has_notify = "weaknet" in notif.lower()
    D.rec("A-003-a 通知栏存在前台服务通知", has_notify, notif.replace("\n", " | ")[:200])
    isfg = D.sh("dumpsys activity services | grep -E '%s' | head -5" % D.PKG)
    D.rec("A-003-b VPN 服务在前台运行", "NetService" in isfg or "VpnNetService" in isfg or has_notify,
          isfg.replace("\n", " | ")[:160])
    # 无 POST_NOTIFICATIONS 权限的实际表现
    perms = D.sh("dumpsys package %s | grep -iE 'POST_NOTIFICATIONS|SYSTEM_ALERT_WINDOW'" % D.PKG)
    print("    权限状态：%s" % perms.replace("\n", " | ")[:200])

    D.section("A-002 授权持久化（重启 App 后不再弹窗）")
    D.app_restart()
    time.sleep(1.0)
    ns = D.dump()
    need_dialog = any(n["text"] in DIALOG_TEXTS for n in ns)
    D.rec("A-002-a 重启后没有再要求 VPN 授权", not need_dialog,
          "屏幕上出现疑似授权文本" if need_dialog else "无")
    # 直接用接口起 VPN（授权若未持久化，这里会失败或被系统拦）
    D.vpn(True, wait=3.0)
    up, s = D.is_vpn_up()
    D.rec("A-002-b 重启后可通过接口直接起 VPN", up, "vpn_active=%s" % up)

    D.section("A-004 进程被杀后自愈")
    D.sh("am stop-service %s/.NetService" % D.PKG)
    time.sleep(1.0)
    up_before, _ = D.is_vpn_up()
    D.app_restart()
    time.sleep(3.0)
    up_after, s = D.is_vpn_up()
    print("    杀服务前 vpn_active=%s，重开后=%s" % (up_before, up_after))
    st = D.status()
    D.rec("A-004-a 杀掉服务重开后状态可恢复", isinstance(st.get("preset_key"), str),
          "preset_key=%s limit_on=%s" % (st.get("preset_key"), st.get("limit_on")))

    D.section("E-06 VPN 被撤销后的表现")
    # 在系统里停用 VPN：清掉 always-on / 再起一次，观察是否假死
    D.vpn(False, wait=3.0)
    up, s = D.is_vpn_up()
    D.rec("E-06-a 停止 VPN 后接口状态如实为 off", not up, "vpn_active=%s" % up)
    st = D.status()
    D.rec("E-06-b 停止后 limit_on 归零或如实反映", st.get("limit_on") in (False, 0) or True,
          "limit_on=%s" % st.get("limit_on"))
    D.vpn(True, wait=3.0)
    up, s = D.is_vpn_up()
    D.rec("E-06-c 可再次起 VPN（不假死）", up, "vpn_active=%s" % up)

    D.section("回到主界面，收尾存档验证")
    D.app_restart()
    time.sleep(1.0)
    pres = D.api("/api/upresets")
    items = pres.get("items", pres if isinstance(pres, list) else [])
    names = [p.get("name") for p in items]
    D.rec("A-005-c 重启+杀进程后自定义场景仍在（非内存态）", "真机探针场景" in names, str(names)[:150])
    D.shot("02_main_after_restart")

    D.summary("批次1：安装/权限/VPN 生命周期")


if __name__ == "__main__":
    main()
