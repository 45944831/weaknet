# -*- coding: utf-8 -*-
"""关窗收尾：进程必须能退、手机必须能还原、关窗时绝不弹模态框。

背景（1.1.13）：用户反馈「点了关闭，进程一直不消失」。原因是退出时在
主线程同步跑一整串 adb 命令，adb 服务重启/拔线时每条都卡到超时；另外
找不到 adb 时还会在 on_close 里弹确认框 —— 窗口已经在拆，没人点它，
进程就永远挂在那里。这里把几条收尾路径都钉死：

  C1 正常退出：手机被还原，且很快返回
  C2 adb 卡死：最多等预算时间就退出，不无限挂起
  C3 找不到 adb：不弹模态框，立即返回
  C4 正在连接时关窗：最多等 3 秒收尾，不留半途状态
  C5 退出时把缓冲日志落盘（最后几秒不丢）
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import tkinter as tk          # noqa: E402
import usb_phone as UP        # noqa: E402
import weaknet as W           # noqa: E402
import weaknet_gui as G       # noqa: E402

pass_n = 0
fail_n = 0


def ck(name, ok, detail=""):
    global pass_n, fail_n
    if ok:
        pass_n += 1
        print("  PASS  %s" % name)
    else:
        fail_n += 1
        print("  FAIL  %s   %s" % (name, detail))


class Dialogs:
    """把 messagebox 拦下来：关窗过程中任何一次弹出都算失败。"""
    def __init__(self):
        self.calls = []

    def showwarning(self, *a, **k):
        self.calls.append(("warning", a))

    def showerror(self, *a, **k):
        self.calls.append(("error", a))

    def showinfo(self, *a, **k):
        self.calls.append(("info", a))


def make_app(root):
    app = G.App(root, None, None)
    app.usb_serial = "TESTSERIAL"
    app.usb_adb = "fake-adb"
    app.usb_on = True
    app.usb_conn_serial = "TESTSERIAL"
    app.usb_conn_port = 8888
    return app


def run_case(title, patch_run, usb_on=True, busy=False, load_adb=None,
             adb_path="fake-adb", budget=15):
    root = tk.Tk()
    root.withdraw()
    app = make_app(root)
    app.usb_adb = adb_path
    app.usb_on = usb_on
    if not usb_on:
        app.usb_conn_serial = None
        app.usb_conn_port = None
    app.usb_busy = busy

    dlg = Dialogs()
    old_mb = G.messagebox
    old_run, old_load, old_flush = UP.run, app._usb_load_adb, W.flush_logs
    flushes = []
    G.messagebox = dlg
    UP.run = patch_run
    if load_adb is not None:
        app._usb_load_adb = lambda: load_adb
    W.flush_logs = lambda: flushes.append(1)

    t0 = time.time()
    try:
        app.on_close()
        ok_return = True
    except Exception as e:
        ok_return = False
        err = e
    dt = time.time() - t0

    G.messagebox = old_mb
    UP.run = old_run
    app._usb_load_adb = old_load
    W.flush_logs = old_flush
    print("  [%s] 用时 %.1fs" % (title, dt))
    return app, dlg, flushes, dt, (ok_return, None if ok_return else err)


def case_ok():
    def fake_run(adb, args, timeout=20):
        if "get" in args:            # 回读代理字段
            return 0, "null", ""
        return 0, "", ""

    app, dlg, flushes, dt, ret = run_case("正常", fake_run)
    ck("C1 正常退出不抛异常", ret[0], repr(ret[1]))
    ck("C1 正常退出后认为已断开", app.usb_on is False, "usb_on=%r" % app.usb_on)
    ck("C1 正常退出 3 秒内返回", dt < 3.0, "用时 %.1fs" % dt)
    ck("C1 正常退出不弹框", not dlg.calls, repr(dlg.calls))
    ck("C1 退出时日志落盘一次", len(flushes) >= 1, repr(flushes))


def case_slow():
    def slow_run(adb, args, timeout=20):
        time.sleep(timeout)          # 模拟每条 adb 都卡到超时
        raise UP.AdbError("adb 命令超时（>%ss）" % timeout)

    app, dlg, flushes, dt, ret = run_case("adb 卡死", slow_run, budget=10)
    ck("C2 adb 卡死时退出不抛异常", ret[0], repr(ret[1]))
    ck("C2 最多等 10 秒就退出", dt < 13.0, "用时 %.1fs" % dt)
    ck("C2 卡死时也不弹框", not dlg.calls, repr(dlg.calls))
    ck("C2 有「先退出」日志", any("先退出" in e.get("msg", "") for e in W.G.events),
       "最后一条：%r" % (W.G.events[-1] if W.G.events else None))


def case_no_adb():
    def never_run(adb, args, timeout=20):
        raise AssertionError("不该走到 adb")

    app, dlg, flushes, dt, ret = run_case("没有 adb", never_run,
                                          load_adb=False, adb_path="")
    ck("C3 找不到 adb 时退出不抛异常", ret[0], repr(ret[1]))
    ck("C3 找不到 adb 时立即返回", dt < 1.0, "用时 %.1fs" % dt)
    ck("C3 找不到 adb 时不弹模态框", not dlg.calls, repr(dlg.calls))


def case_busy():
    app, dlg, flushes, dt, ret = run_case("连接中",
                                          lambda adb, args, timeout=20: (0, "", ""),
                                          usb_on=False, busy=True)
    ck("C4 连接中关窗不抛异常", ret[0], repr(ret[1]))
    ck("C4 连接中最多等 3 秒", 2.5 < dt < 5.0, "用时 %.1fs" % dt)
    ck("C4 连接中关窗不弹框", not dlg.calls, repr(dlg.calls))


def main():
    print("== 关窗收尾 ==")
    case_ok()
    case_slow()
    case_no_adb()
    case_busy()
    print("\n合计 %d 项通过 / %d 项失败" % (pass_n, fail_n))
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
