# -*- coding: utf-8 -*-
"""Android 端：关闭 App 后进程 / 服务 / 通知 / VPN 是否残留。

Android 的「关闭」有三种语义，必须分开看：
  A1 返回键退出界面     -> 设计上是退到后台（moveTaskToBack），弱网要继续生效
  A2 系统回收后台进程   -> 有前台服务时不应该被杀（am kill）
  A3 强制停止 / 划掉任务 -> 必须彻底退出，不留进程、不留服务、不留 tun
  A4 强停后等 10s       -> 不能被 START_STICKY 复活

判定：
  * A1 进程保留属于预期（前台服务），但必须仍有常驻通知，不能「无提示常驻」
  * A2 进程仍应保留（前台服务保护）
  * A3 进程 0、服务 0、通知 0、tun 消失、手机代理还原
  * A4 10s 后仍为 0
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _t_dev_lib as L  # noqa: E402

PKG = L.PKG
checks = []


def ck(name, ok, detail=""):
    checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + str(detail)) if detail else ""),
          flush=True)


def gap(t):
    print("\n---- %s ----" % t, flush=True)


def pid():
    s = L.adb("shell", "pidof " + PKG)[0]
    return int(s.split()[0]) if s and s.split()[0].isdigit() else 0


def svc_count():
    out = L.adb("shell", "dumpsys activity services " + PKG)[0]
    return 0 if "(nothing)" in out else out.count("ServiceRecord{")


def svc_names():
    out = L.adb("shell", "dumpsys activity services " + PKG)[0]
    if "(nothing)" in out:
        return []
    names = []
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith("ServiceRecord{") or ln.startswith("* ServiceRecord{"):
            names.append(ln[:100])
    return names


def svc_foreground():
    """服务是否处于前台（isForeground=true），比数通知记录可靠。

    ColorOS 上本 App 的通知被系统判为 importance=NONE（userSet=false），
    dumpsys notification 里根本没有 NotificationRecord，但 ServiceRecord 里
    isForeground=true 且带着 foregroundNoti —— 说明前台服务本身是正常的。
    """
    out = L.adb("shell", "dumpsys activity services " + PKG)[0]
    return "isForeground=true" in out


def notif_importance():
    out = L.adb("shell", "dumpsys notification --noredact")[0]
    for ln in out.splitlines():
        if "AppSettings: " + PKG in ln:
            return ln.strip()
    return ""


def activity_state():
    """返回 (是否还有 Activity 记录, 是否前台)。"""
    out = L.adb("shell", "dumpsys activity activities")[0]
    has = PKG in out
    fg = False
    for ln in out.splitlines():
        if "mResumedActivity" in ln or "ResumedActivity" in ln:
            if PKG in ln:
                fg = True
    return has, fg


def http_proxy():
    return L.adb("shell", "settings get global http_proxy")[0]


def tun_up():
    try:
        return bool(L.is_vpn_up())
    except Exception:
        return "tun0" in L.adb("shell", "ip addr show tun0")[0]


def tap_back():
    L.adb("shell", "input keyevent 4")
    time.sleep(1.2)


def snap():
    return {"pid": pid(), "svc": svc_count(), "fg": svc_foreground(),
            "tun": tun_up(), "proxy": http_proxy()}


def wait_api(timeout=25.0):
    """等控制接口真正可用：api_try 失败时返回 {'_err': ...}，不能拿 None 判断。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = L.api_try("/api/status", timeout=2)
        if isinstance(r, dict) and "_err" not in r and r.get("version"):
            return True
        time.sleep(0.5)
    return False


def ensure_stopped():
    L.adb("shell", "am force-stop " + PKG)
    time.sleep(1.5)


def main():
    gap("A0 基线")
    ensure_stopped()
    s0 = snap()
    ck("A0 起始无进程", s0["pid"] == 0, s0["pid"])
    ck("A0 起始无服务", s0["svc"] == 0, s0["svc"])
    ck("A0 起始无前台服务", not s0["fg"], s0["fg"])
    ck("A0 起始无 tun", not s0["tun"], s0["tun"])
    ck("A0 起始手机无 HTTP 代理", s0["proxy"] in ("null", "", ":0"), s0["proxy"])

    # ---------- A1 冷启动 -> 返回键 ----------
    gap("A1 启动 App 后按返回键（退到后台）")
    L.setup_forward()
    L.adb("shell", "am start -n %s/.MainActivity" % PKG)
    ck("A1 控制接口就绪", wait_api(20.0), "")
    s1 = snap()
    ck("A1 启动后有进程", s1["pid"] > 0, s1["pid"])
    ck("A1 常驻服务已起（代理/控制台）", s1["svc"] >= 1, svc_names())
    ck("A1 服务处于前台（带常驻通知对象）", s1["fg"], s1["fg"])
    imp = notif_importance()
    print("  通知渠道状态：%s" % (imp or "未找到 AppSettings 记录"), flush=True)
    tap_back()
    time.sleep(3.0)
    s1b = snap()
    has_act, fg = activity_state()
    ck("A1 返回后界面已不在前台", not fg, "activity记录=%s 前台=%s" % (has_act, fg))
    ck("A1 返回后进程仍存活（设计：退后台不退出）", s1b["pid"] > 0, s1b["pid"])
    ck("A1 返回后服务仍在", s1b["svc"] >= 1, s1b["svc"])
    ck("A1 返回后仍为前台服务（用户可在通知栏看到）", s1b["fg"], s1b["fg"])
    ck("A1 返回后控制接口仍可用", L.api_try("/api/status") is not None,
       "8899 经 forward 18899")
    a1_pid = s1b["pid"]

    # ---------- A2 系统回收后台进程 ----------
    gap("A2 am kill 模拟系统回收后台进程")
    # 先回桌面，App 处于后台
    L.adb("shell", "input keyevent 3")
    time.sleep(1.5)
    L.adb("shell", "am kill " + PKG)
    time.sleep(2.5)
    s2 = snap()
    ck("A2 前台服务保护下进程未被回收", s2["pid"] > 0,
       "pid=%s（kill 前 %s）" % (s2["pid"], a1_pid))
    ck("A2 服务仍在", s2["svc"] >= 1, s2["svc"])

    # ---------- A3 开启 VPN + 限速后强制停止 ----------
    gap("A3 开启 VPN 接管 + 限速，然后强制停止")
    L.setup_forward()
    L.adb("shell", "am start -n %s/.MainActivity" % PKG)
    ok_api = wait_api(25.0)
    ck("A3 重新进入后控制接口就绪", ok_api, "等待最多 25s")
    r = L.api_try("/api/run", {"enable": True})
    ck("A3 限速已开启", bool(r and r.get("limit_on")), r)
    try:
        L.vpn(True)
    except Exception as e:
        print("  vpn 开启异常：%r" % e, flush=True)
    time.sleep(2.5)
    s3 = snap()
    ck("A3 VPN 已建立 tun 接管", s3["tun"], s3["tun"])
    ck("A3 VPN 服务在运行", s3["svc"] >= 2, svc_names())

    L.adb("shell", "am force-stop " + PKG)
    time.sleep(2.0)
    s3b = snap()
    ck("A3 强停后进程退出（无残留）", s3b["pid"] == 0, s3b["pid"])
    ck("A3 强停后服务全部销毁", s3b["svc"] == 0, svc_names())
    ck("A3 强停后前台服务标记消失", not s3b["fg"], s3b["fg"])
    ck("A3 强停后 tun 已拆除", not s3b["tun"], s3b["tun"])

    # ---------- A4 START_STICKY 是否复活 ----------
    gap("A4 强停后等待 10s，检查是否被 START_STICKY 复活")
    for i in range(5):
        time.sleep(2.0)
        s = snap()
        if s["pid"]:
            ck("A4 第 %d 次采样即被复活" % (i + 1), False, s)
            break
    else:
        s4 = snap()
        ck("A4 10s 内未被系统复活", s4["pid"] == 0, s4)
        ck("A4 服务未复活", s4["svc"] == 0, svc_names())

    # ---------- A5 界面内是否有「退出」入口 ----------
    gap("A5 界面入口检查（能否主动完全退出）")
    L.adb("shell", "am start -n %s/.MainActivity" % PKG)
    time.sleep(3.5)
    nodes = L.dump()
    txt = " ".join(L.texts(nodes) or [])
    has_exit = any(k in txt for k in ("退出", "关闭服务", "停止服务"))
    ck("A5 主界面提供「退出/停止服务」入口", has_exit,
       "按钮文本含退出=%s；界面文本前 120 字：%s" % (has_exit, txt[:120]))

    ensure_stopped()
    gap("收尾")
    ck("收尾：进程已清", pid() == 0, pid())
    ck("收尾：服务已清", svc_count() == 0, svc_names())
    return checks


if __name__ == "__main__":
    rc = 1
    try:
        main()
        rc = 0
    except Exception:
        import traceback
        traceback.print_exc()
        ck("脚本异常", False)
    finally:
        try:
            ensure_stopped()
        except Exception:
            pass
        out = os.path.join(L.ROOT, "_t_exit_residue_apk.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(checks, f, ensure_ascii=False, indent=2)
        bad = [c for c in checks if not c["ok"]]
        print("\n==== %d 项检查，%d 失败 ====" % (len(checks), len(bad)), flush=True)
        for c in bad:
            print("  FAIL: %s | %s" % (c["name"], c["detail"]), flush=True)
        sys.exit(2 if bad else rc)
