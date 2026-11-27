# -*- coding: utf-8 -*-
"""USB 手机弱网面板（从主界面拆分的独立面板）

从 weaknet_gui.py 整段搬出（方法体与原文件逐字一致，只补了 import）。
App 通过多继承复用：面板之间不互相调用，只依赖引擎状态与主题层。
"""
import os
import queue
import threading

import tkinter as tk
from tkinter import ttk
from tkinter import filedialog
from tkinter import messagebox

import weaknet as W
import usb_phone as UP

from gui_theme import (ACCENT, BTN2, CARD, FONT, FONT_B, MUTED, OK, TEXT, WARN)

class UsbMixin:
    """Usb。仅供 App 多继承使用，不单独实例化。"""

    def _build_usb(self):
        box = self._card(self.col_right, "📱 USB 手机弱网（免装 APK）")
        self.usb_adb = None          # adb.exe 路径
        self.usb_devices = []        # 最近一次刷新拿到的可用设备
        self.usb_serial = None       # 下拉框当前选中的设备
        self.usb_on = False          # 手机代理是否已生效
        # 已接管的那台 + 用的端口。下拉框随时能改选中项，断开必须按这本账走，
        # 否则连上 A 之后选一下 B 再点「断开」，清的是 B，A 一直挂着代理。
        self.usb_conn_serial = None
        self.usb_conn_port = None
        self.usb_busy = False        # 有 adb 命令在跑，按钮先锁住

        row1 = tk.Frame(box, bg=CARD)
        row1.pack(fill="x")
        self.usb_dev = tk.StringVar()
        self.usb_combo = ttk.Combobox(row1, textvariable=self.usb_dev, font=FONT,
                                      width=20, state="readonly")
        self.usb_combo.pack(side="left", fill="x", expand=True)
        self.usb_combo.bind("<<ComboboxSelected>>", self._usb_pick)
        self.btn_usb_scan = tk.Button(row1, text="🔄 刷新设备", font=FONT, bg=BTN2,
                                      fg=TEXT, relief="flat", padx=8, pady=3,
                                      cursor="hand2", command=lambda: self.usb_refresh())
        self.btn_usb_scan.pack(side="left", padx=(6, 0))

        row2 = tk.Frame(box, bg=CARD)
        row2.pack(fill="x", pady=(8, 0))
        self.btn_usb_conn = tk.Button(row2, text="连接手机", font=FONT_B, bg=ACCENT,
                                      fg="white", relief="flat", padx=12, pady=4,
                                      cursor="hand2", command=self.usb_connect)
        self.btn_usb_conn.pack(side="left")
        self.btn_usb_cut = tk.Button(row2, text="断开", font=FONT, bg=BTN2, fg=TEXT,
                                     relief="flat", padx=12, pady=4, cursor="hand2",
                                     command=lambda: self.usb_disconnect("手动断开"))
        self.btn_usb_cut.pack(side="left", padx=(6, 0))
        self.btn_usb_adb = tk.Button(row2, text="选择 adb", font=FONT, bg=BTN2,
                                     fg=TEXT, relief="flat", padx=8, pady=4,
                                     cursor="hand2", command=self.usb_pick_adb)
        self.btn_usb_adb.pack(side="right")

        self.usb_state = tk.Label(box, text="未连接", font=FONT, bg=CARD, fg=MUTED,
                                  anchor="w", justify="left", wraplength=276)
        self.usb_state.pack(fill="x", pady=(8, 0))
        self.usb_adb_lab = tk.Label(box, text="", font=FONT, bg=CARD, fg=MUTED,
                                    anchor="w", justify="left", wraplength=276)
        self.usb_adb_lab.pack(fill="x", pady=(3, 0))
        self._usb_load_adb()

    def _usb_load_adb(self):
        """按「用户选过的 → 环境变量 → PATH → 常见安装目录」找 adb.exe"""
        try:
            saved = (W.load_settings() or {}).get("adb_path") or ""
        except Exception:
            saved = ""
        adb = UP.find_adb(saved)
        self.usb_adb = adb
        if hasattr(self, "usb_adb_lab"):
            if adb:
                self.usb_adb_lab.config(text="adb：" + adb, fg=MUTED)
            else:
                self.usb_adb_lab.config(text="未找到 adb.exe —— 点「选择 adb」手动指定", fg=WARN)
        return adb

    def usb_pick_adb(self):
        p = filedialog.askopenfilename(
            title="选择 adb.exe（Android SDK 的 platform-tools 目录里）",
            filetypes=[("adb", "adb.exe"), ("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if not p:
            return
        if not os.path.isfile(p):
            messagebox.showerror("选择 adb", "文件不存在：\n" + p)
            return
        try:
            d = W.load_settings() or {}
            d["adb_path"] = p
            saved = W.save_settings(d)
        except Exception as e:
            messagebox.showerror("选择 adb", "保存失败：%r" % e)
            return
        if not saved:
            messagebox.showerror("选择 adb", "保存失败：写不了 weaknet_settings.json")
            return
        self.usb_adb = p
        self.usb_adb_lab.config(text="adb：" + p, fg=MUTED)
        W.log_event("usb", "已指定 adb：" + p)
        self.usb_refresh(quiet=True)

    def usb_refresh(self, quiet=False):
        adb = self.usb_adb or self._usb_load_adb()
        if not adb:
            self._usb_state("未找到 adb.exe —— 点「选择 adb」指定它的位置。", WARN)
            if not quiet:
                messagebox.showerror(
                    "USB 手机弱网",
                    "找不到 adb.exe。\n\n请点「选择 adb」指定它的位置 —— 它通常在 "
                    "Android SDK 的 platform-tools 目录下。")
            return
        self._usb_state("正在读取设备列表…", MUTED)
        self._usb_run(lambda: UP.list_devices(adb),
                      lambda res: self._usb_on_devices(res, quiet))

    def _usb_on_devices(self, res, quiet=False):
        if "devices" not in res:
            err = res.get("msg") or res.get("error") or "未知错误"
            # 最常见的：adb server 起不来 / 被安全软件拦了
            self._usb_state("读取设备失败：" + err[:70], WARN)
            W.log_event("usb", "刷新设备失败：" + err[:150])
            if not quiet:
                messagebox.showerror("刷新设备", "adb devices 执行失败：\n\n" + err)
            return

        devs = res.get("devices") or []
        blocked = res.get("blocked") or []
        self.usb_devices = devs
        labels = [("%s  " % d["model"] if d["model"] else "") + d["serial"] for d in devs]
        self.usb_combo.configure(values=labels)

        serials = [d["serial"] for d in devs]
        if labels:
            if self.usb_serial in serials:
                self.usb_combo.current(serials.index(self.usb_serial))
            else:
                self.usb_serial = serials[0]
                self.usb_combo.current(0)
        else:
            self.usb_serial = None
            self.usb_combo.set("")

        if blocked:
            b = blocked[0]
            self._usb_state("有 %d 台设备不可用：%s（%s）" % (len(blocked), b["serial"],
                                                            b["state"]), WARN)
            if not quiet:
                messagebox.showwarning(
                    "设备未授权",
                    "检测到设备 %s 的状态是 %s。\n\n请在手机上确认「允许 USB 调试」"
                    "（必要时先在开发者选项里撤销授权、重新插线），再点「🔄 刷新设备」。"
                    % (b["serial"], b["state"]))
        elif not devs:
            self._usb_state("没有检测到手机 —— 插好 USB 线（要能传数据）、打开"
                            "「开发者选项 → USB 调试」并允许本机调试后刷新。", MUTED)
            if not quiet:
                messagebox.showinfo(
                    "没有可用设备",
                    "adb 没列出可用设备。\n\n检查三件事：\n"
                    "1. USB 线能传数据（不是纯充电线）\n"
                    "2. 手机上「开发者选项 → USB 调试」已打开\n"
                    "3. 手机上已点过「允许 USB 调试」（本机）")
        else:
            self._usb_state("已发现 %d 台设备，选一台点「连接手机」。" % len(devs), MUTED)
            W.log_event("usb", "刷新设备：发现 %d 台" % len(devs))

    def _usb_pick(self, _event=None):
        i = self.usb_combo.current()
        if 0 <= i < len(self.usb_devices):
            self.usb_serial = self.usb_devices[i]["serial"]

    def usb_connect(self):
        adb = self.usb_adb or self._usb_load_adb()
        if not adb:
            messagebox.showerror("连接手机", "找不到 adb.exe，请先点「选择 adb」指定它。")
            return
        if not self.usb_serial:
            self._usb_state("还没选设备 —— 先点「🔄 刷新设备」。", WARN)
            messagebox.showinfo("连接手机", "还没选设备。\n\n先点「🔄 刷新设备」，"
                                            "再从下拉框里选一台。")
            return
        serial, port = self.usb_serial, W.G.proxy_port
        prev = (self.usb_conn_serial, self.usb_conn_port) if self.usb_on else (None, None)
        self._usb_state("正在连接 %s …" % serial, MUTED)

        def job():
            # 换机 / 换端口前先把上一台还原：账本记的是「已接管的那台」，不是下拉框
            # 当前选项。不这么做的话，旧机、旧端口的映射都会留到拔线为止。
            if prev[0]:
                UP.disconnect(adb, prev[0], prev[1] or port)
            return UP.connect(adb, serial, port)

        self._usb_run(job, lambda res: self._usb_on_connect(res, serial, port))

    def _usb_on_connect(self, res, serial=None, port=None):
        serial = serial or self.usb_serial or "（未知设备）"
        port = port or W.G.proxy_port
        self.usb_on = False                  # 连接前可能刚把上一台断开了
        self.usb_conn_serial = None
        self.usb_conn_port = None
        if not res.get("ok"):
            stage = res.get("stage", "")
            msg = res.get("msg", "未知错误")
            if stage in ("settings", "verify"):
                # 端口映射已经建好了但代理没写上，留着就是残骸 —— 撤掉
                self._usb_run(lambda: UP.disconnect(self.usb_adb, serial, port), None)
                title = "手机拒绝了代理设置"
                body = ("写入失败：\n\n" + msg + "\n\n" + self._usb_alt_help(port))
            elif stage == "reverse":
                title = "端口映射失败"
                body = (msg + "\n\n请确认：手机已允许 USB 调试、USB 线能传数据"
                              "（不是纯充电线）、且没被其它程序占用 adb。")
            else:
                title = "连接手机失败"
                body = msg
            self._usb_state("连接失败（%s）" % (stage or "adb"), WARN)
            W.log_event("usb", "USB 手机连接失败（%s）：%s" % (stage, msg[:150]))
            # 关窗过程中不能再弹模态框：窗口马上销毁，没人点它，进程会一直挂着
            if not self._closing:
                messagebox.showerror(title, body)
            return

        self.usb_on = True
        self.usb_conn_serial = res.get("serial") or serial
        self.usb_conn_port = port
        W.log_event("usb", "USB 手机已接管：%s → %s" % (serial, res.get("proxy")))
        if res.get("watch"):
            W.log_event("usb", "已装拔线守护：反向端口消失后手机自动恢复直连")
        else:
            W.log_event("usb", "拔线守护未装上（%s）：拔线后手机可能仍指向本地代理，需插回清理"
                        % (res.get("watch_msg") or "未知原因"))
        if W.G.limit_on:
            self._usb_state("已接管 %s → %s" % (serial, res.get("proxy")), OK)
        else:
            self._usb_state("已接管 %s → %s，但限速未开启（当前等同直连）"
                            % (serial, res.get("proxy")), WARN)
            W.log_event("usb", "提示：限速未开启，手机流量只是经过代理，还没施加弱网")
        self._usb_sync_btns()

    def _usb_alt_help(self, port):
        """写不进全局设置时的替代途径。这条限制很常见，说明白比只报个错有用得多。"""
        lan = W.G.lan_ip or "电脑的局域网 IP"
        return ("这是 Android 的 ROM 限制：adb 不能改写全局设置"
                "（需要 WRITE_SECURE_SETTINGS 权限），国产 ROM 上尤其常见。\n\n"
                "可以试试这几条：\n"
                "1. 手机「开发者选项」里打开「USB 调试（安全设置）」或"
                "「禁止权限监控」，拔插 USB 后重试\n"
                "2. 手机已 root：先执行 adb root 再连\n"
                "3. 改用 Wi-Fi 手动代理：手机连上与电脑同一个 Wi-Fi，在 WLAN 的"
                "代理里填 %s:%d（电脑这边要先开「开启局域网访问」）\n"
                "4. 或在手机上安装 WeakNet.apk —— VpnService 直接接管整机流量，"
                "不需要这些权限" % (lan, port))

    def usb_disconnect(self, reason="", wait=False):
        """还原手机直连。wait=True 时同步跑（退出程序时用，没机会等回调）。"""
        if self.usb_on and self.usb_conn_serial:
            serial, port = self.usb_conn_serial, self.usb_conn_port or W.G.proxy_port
        else:
            serial, port = self.usb_serial, W.G.proxy_port
        if not serial:
            return
        adb = self.usb_adb or self._usb_load_adb()
        if not adb:
            # adb 都没了就清不掉，只能让用户自己动手 —— 这时候必须说清楚，
            # 否则用户会以为已经还原了
            self._usb_state("adb 不见了，无法自动还原！请手动检查手机代理。", WARN)
            W.log_event("usb", "找不到 adb.exe，无法自动还原手机代理")
            # 关窗时没机会让人点确认框，弹出来就是永远关不掉
            if not self._closing:
                messagebox.showwarning(
                    "无法还原手机", "找不到 adb.exe，没法自动清除手机上的代理。\n\n"
                    "请手动在手机上执行：\n"
                    "  设置 → 开发者选项 → 撤销 USB 调试授权\n"
                    "或在 WLAN 里把代理改回「无」，否则手机会一直上不了网。")
            return
        if wait:
            self._usb_on_disconnect(UP.disconnect(adb, serial, port, timeout=8), reason)
        else:
            self._usb_run(lambda: UP.disconnect(adb, serial, port),
                          lambda res: self._usb_on_disconnect(res, reason))

    def _usb_on_disconnect(self, res, reason=""):
        prefix = (reason + "：") if reason else ""
        if res.get("ok"):
            self.usb_on = False
            self.usb_conn_serial = None
            self.usb_conn_port = None
            self._usb_state("已断开，手机恢复直连", MUTED)
            W.log_event("usb", prefix + "已断开手机代理，恢复直连")
        else:
            msg = res.get("msg") or "未知错误"
            self._usb_state("还原手机失败：" + msg[:70] + "（请手动检查手机代理！）", WARN)
            W.log_event("usb", prefix + "还原手机失败：" + msg[:150])
            if not self._closing:
                messagebox.showwarning(
                    "还原手机失败",
                    "没能自动清除手机上的代理：\n\n" + msg +
                    "\n\n请手动处理，否则手机可能上不了网：\n"
                    "  手机「设置 → WLAN」把代理改回「无」，或\n"
                    # 只 delete 在 MIUI 上不够：系统会把代理镜像进当前 Wi-Fi 的网络
                    # 配置，删全局行清不掉那一份 —— 必须写 :0 让它同步一次
                    "  adb shell settings put global http_proxy :0")
        self._usb_sync_btns()

    def _usb_run(self, fn, done):
        """把 adb 命令丢到后台线程。结果进队列，由主线程的 refresh() 取。"""
        if self.usb_busy:
            return
        self.usb_busy = True
        self._usb_sync_btns()

        def worker():
            try:
                res = fn()
                if not isinstance(res, dict):
                    res = {"ok": False, "msg": "adb 返回了意外的结果：%r" % (res,)}
            except Exception as e:
                res = {"ok": False, "stage": "adb",
                       "msg": "%s: %s" % (type(e).__name__, e)}
            try:
                self.usb_q.put((res, done))
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _usb_pump(self):
        """主线程里取出后台 adb 的结果（tkinter 只能在主线程碰控件）"""
        try:
            while True:
                res, done = self.usb_q.get_nowait()
                self.usb_busy = False
                self._usb_sync_btns()
                if done is not None:
                    done(res)
        except queue.Empty:
            pass
        except Exception as e:
            W.log("USB 回调异常：%r" % e)

    def _usb_state(self, text, color=MUTED):
        try:
            self.usb_state.config(text=text, fg=color)
        except Exception:
            pass

    def _usb_sync_btns(self):
        try:
            busy = self.usb_busy
        except Exception:
            return
        for name in ("btn_usb_scan", "btn_usb_conn", "btn_usb_cut", "btn_usb_adb"):
            btn = getattr(self, name, None)
            if btn is not None:
                self._set(btn, state="disabled" if busy else "normal")
        conn = getattr(self, "btn_usb_conn", None)
        if conn is not None:
            self._set(conn, text="连接中…" if busy else
                      ("重新连接" if self.usb_on else "连接手机"))

    def _usb_restore_on_close(self, reason="退出程序", budget=10.0):
        """退出时还原手机：后台跑、最多等 budget 秒，绝不弹模态框。

        adb 服务重启/拔线后，一串 adb 命令可能各卡到超时；同步跑完再销毁
        窗口，用户看到的就是「点了关闭进程也不消失」。
        """
        if self.usb_on and self.usb_conn_serial:
            serial, port = self.usb_conn_serial, self.usb_conn_port or W.G.proxy_port
        else:
            serial, port = self.usb_serial, W.G.proxy_port
        if not serial:
            return
        adb = self.usb_adb or self._usb_load_adb()
        if not adb:
            self._usb_state("adb 不见了，无法自动还原！请手动检查手机代理。", WARN)
            W.log_event("usb", "退出：找不到 adb.exe，未能自动还原手机代理")
            return
        box = {}

        def job():
            try:
                box["res"] = UP.disconnect(adb, serial, port, timeout=4)
            except Exception as e:
                box["res"] = {"ok": False, "stage": "cleanup",
                              "msg": "%s: %s" % (type(e).__name__, e)}

        th = threading.Thread(target=job, daemon=True)
        th.start()
        th.join(budget)
        if th.is_alive():
            W.log_event("usb", "退出：还原手机超过 %.0f 秒未完成，先退出；"
                        "若手机仍上不了网，请插回后点「断开」" % budget)
            return
        self._usb_on_disconnect(box.get("res") or {"ok": False, "msg": "没有返回结果"}, reason)

