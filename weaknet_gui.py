"""
WeakNet —— App 弱网测试工具（桌面 GUI 版）

用 tkinter 写的原生窗口界面，不依赖任何第三方库。
引擎与 Web 控制台复用 weaknet.py：GUI 线程负责显示，后台线程跑 asyncio 服务。

用法：
    python weaknet_gui.py [--proxy-port 8888] [--ctrl-port 8899]
打包：
    pyinstaller --onefile --windowed --name WeakNet --add-data "dashboard.html;." weaknet_gui.py
"""

import argparse
import asyncio
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import weaknet as W

BG = "#f4f6f9"
CARD = "#ffffff"
ACCENT = "#2d7ff9"
ACCENT_D = "#1a63d8"
TEXT = "#1f2430"
MUTED = "#6b7280"
LINE = "#e3e7ee"
OK = "#16a34a"
WARN = "#d97706"

FONT = ("Microsoft YaHei UI", 9)
FONT_B = ("Microsoft YaHei UI", 9, "bold")
FONT_T = ("Microsoft YaHei UI", 13, "bold")

# GUI 里的短名称（Web 控制台仍用全名）
SHORT = {
    "direct": "直连", "wifi": "Wi-Fi 良好", "4g": "4G LTE", "3g": "3G",
    "edge": "2G EDGE", "gprs": "2G GPRS", "dialup": "56K 拨号",
    "wifi_weak": "弱 Wi-Fi", "congestion": "网络拥塞", "satellite": "卫星链路",
    "highspeed_rail": "高铁/地铁", "elevator": "电梯/地下室",
    "flapping": "频繁断连", "blackhole": "黑洞（无响应）",
    "dns_fail": "DNS 故障", "airplane": "完全断网",
}

# key, 标签, 单位, 上限
FIELDS = [
    ("latency_down_ms", "下行延迟", "ms", 10000),
    ("latency_up_ms", "上行延迟", "ms", 10000),
    ("jitter_ms", "抖动 ±", "ms", 5000),
    ("down_kbps", "下行带宽", "kbps", 1000000),
    ("up_kbps", "上行带宽", "kbps", 1000000),
    ("loss_pct", "丢包率", "%", 100),
    ("stall_ms", "卡顿", "ms", 10000),
    ("disconnect_pct", "断连率", "%", 100),
    ("timeout_pct", "黑洞率", "%", 100),
    ("dns_fail_pct", "DNS失败", "%", 100),
    ("reject_pct", "拒连率", "%", 100),
    ("flap_sec", "断网间隔", "秒", 600),
    ("flap_blackout_sec", "断网时长", "秒", 600),
]


def fmt_rate(bps):
    """字节/秒 → 人类可读"""
    if bps >= 1024 * 1024:
        return f"{bps / 1024 / 1024:.2f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def fmt_bytes(n):
    if n >= 1024 * 1024 * 1024:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 * 1024:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


class App:
    def __init__(self, root, loop, ctrl_port):
        self.root = root
        self.loop = loop
        self.ctrl_port = ctrl_port
        self.preset_btns = {}
        self.vars = {}
        self._last = (time.time(), W.G.bytes_down, W.G.bytes_up)
        self._rate = (0.0, 0.0)
        self._ev_shown = 0
        self._closing = False

        root.title("WeakNet —— App 弱网测试工具")
        root.configure(bg=BG)
        # 按屏幕自适应窗口高度并居中，避免小屏幕上底部内容被任务栏/屏幕边缘裁掉
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        wh = min(960, sh - 90)
        root.geometry(f"880x{wh}+{max(0, (sw - 880) // 2)}+{max(0, (sh - 60 - wh) // 2)}")
        root.minsize(760, min(880, sh - 120))

        self._build_header()
        self._build_presets()
        self._build_custom()
        self._build_stats()
        self._build_log()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh()

    # ---------------- 界面搭建 ----------------

    def _card(self, parent, title, expand=False):
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="both" if expand else "x", padx=14, pady=(0, 10))
        tk.Label(outer, text=title, font=FONT_B, bg=BG, fg=TEXT).pack(anchor="w", pady=(0, 4))
        card = tk.Frame(outer, bg=CARD, highlightbackground=LINE,
                        highlightthickness=1, bd=0)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=12, pady=10)
        return inner

    def _build_header(self):
        top = tk.Frame(self.root, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        top.pack(fill="x", padx=14, pady=(12, 10))

        head = tk.Frame(top, bg=CARD)
        head.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(head, text="WeakNet", font=FONT_T, bg=CARD, fg=ACCENT).pack(side="left")
        tk.Label(head, text="App 弱网测试工具", font=FONT, bg=CARD, fg=MUTED).pack(side="left", padx=(8, 0))
        self.dot = tk.Label(head, text="● 服务运行中", font=FONT_B, bg=CARD, fg=OK)
        self.dot.pack(side="right")

        info = tk.Frame(top, bg=CARD)
        info.pack(fill="x", padx=14)
        self.lab_proxy = tk.Label(info, text="", font=FONT, bg=CARD, fg=TEXT)
        self.lab_proxy.pack(anchor="w")
        self.lab_ctrl = tk.Label(info, text="", font=FONT, bg=CARD, fg=MUTED)
        self.lab_ctrl.pack(anchor="w")

        btns = tk.Frame(top, bg=CARD)
        btns.pack(fill="x", padx=14, pady=10)
        self.btn_sys = tk.Button(btns, text="一键接管本机流量", font=FONT_B, bg=ACCENT,
                                 fg="white", relief="flat", padx=14, pady=6,
                                 cursor="hand2", command=self.toggle_sysproxy)
        self.btn_sys.pack(side="left")
        self.btn_lan = tk.Button(btns, text="开启局域网访问", font=FONT, bg="#eef2f7",
                                 fg=TEXT, relief="flat", padx=12, pady=6,
                                 cursor="hand2", command=self.toggle_lan)
        self.btn_lan.pack(side="left", padx=(8, 0))
        tk.Button(btns, text="打开 Web 控制台", font=FONT, bg="#eef2f7", fg=TEXT,
                  relief="flat", padx=12, pady=6, cursor="hand2",
                  command=self.open_dash).pack(side="left", padx=(8, 0))
        tk.Button(btns, text="停止模拟（恢复直连）", font=FONT, bg="#eef2f7", fg=TEXT,
                  relief="flat", padx=12, pady=6, cursor="hand2",
                  command=self.stop_all).pack(side="right")

        # 实时状态（并入顶栏，节省纵向空间）
        srow = tk.Frame(top, bg=CARD)
        srow.pack(fill="x", padx=14, pady=(0, 12))
        self.stat = {}
        for k, label in [("scene", "当前场景"), ("conns", "连接"), ("active", "活跃"),
                         ("down", "下行"), ("up", "上行"), ("gate", "闸门")]:
            cell = tk.Frame(srow, bg=CARD)
            cell.pack(side="left", padx=(0, 18))
            tk.Label(cell, text=label, font=FONT, bg=CARD, fg=MUTED).pack(side="left")
            v = tk.Label(cell, text="-", font=FONT_B, bg=CARD, fg=TEXT)
            v.pack(side="left", padx=(4, 0))
            self.stat[k] = v

        self.refresh_header()

    def _build_stats(self):
        pass   # 实时状态已并入顶栏（见 _build_header），保留方法避免旧调用报错

    def _build_presets(self):
        box = self._card(self.root, "场景预设（点击即生效）")
        cats = {}
        for key, (name, cat, _extra) in W.PRESETS.items():
            cats.setdefault(cat, []).append((key, name))

        row = 0
        for cat in ("常规网络", "弱网环境", "极端环境"):
            items = cats.get(cat)
            if not items:
                continue
            group = tk.Frame(box, bg=CARD)
            group.pack(fill="x", pady=(0, 4))
            tk.Label(group, text=cat, font=FONT, bg=CARD, fg=MUTED).pack(side="left", padx=(0, 10))
            grid = tk.Frame(group, bg=CARD)
            grid.pack(side="left", fill="x")
            for i, (key, name) in enumerate(items):
                b = tk.Button(grid, text=SHORT.get(key, name), font=FONT, bg="#eef2f7",
                              fg=TEXT, relief="flat", padx=9, pady=3, cursor="hand2",
                              command=lambda k=key: self.apply_preset(k))
                b.grid(row=i // 5, column=i % 5, padx=(0, 5), pady=2, sticky="w")
                self.preset_btns[key] = b
            row += 1

    def _build_custom(self):
        box = self._card(self.root, "自定义参数（点「应用」生效，0 表示不限制/关闭）")
        grid = tk.Frame(box, bg=CARD)
        grid.pack(fill="x")
        for i, (key, label, unit, top) in enumerate(FIELDS):
            r, c = i // 3, i % 3
            cell = tk.Frame(grid, bg=CARD)
            cell.grid(row=r, column=c, sticky="w", padx=(0, 22), pady=1)
            tk.Label(cell, text=label, font=FONT, bg=CARD, fg=TEXT, width=12,
                     anchor="w").pack(side="left")
            v = tk.StringVar(value="0")
            sp = ttk.Spinbox(cell, from_=0, to=top, width=8, textvariable=v,
                             font=FONT, format="%.1f")
            sp.pack(side="left")
            tk.Label(cell, text=unit, font=FONT, bg=CARD, fg=MUTED).pack(side="left", padx=(4, 0))
            self.vars[key] = v

        bar = tk.Frame(box, bg=CARD)
        bar.pack(fill="x", pady=(10, 0))
        tk.Button(bar, text="应用自定义参数", font=FONT_B, bg=ACCENT, fg="white",
                  relief="flat", padx=14, pady=5, cursor="hand2",
                  command=self.apply_custom).pack(side="left")
        tk.Button(bar, text="读取当前场景", font=FONT, bg="#eef2f7", fg=TEXT,
                  relief="flat", padx=12, pady=5, cursor="hand2",
                  command=self.load_current).pack(side="left", padx=(8, 0))
        self.load_current()

    def _build_stats(self):
        pass   # 实时状态已并入顶栏（见 _build_header）

    def _build_log(self):
        box = self._card(self.root, "事件日志", expand=True)
        wrap = tk.Frame(box, bg=CARD)
        wrap.pack(fill="both", expand=True)
        self.logbox = tk.Listbox(wrap, height=6, font=("Consolas", 9), bg="#fbfcfe",
                                 fg=TEXT, relief="flat", highlightthickness=1,
                                 highlightbackground=LINE, bd=0)
        sb = tk.Scrollbar(wrap, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=sb.set)
        self.logbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---------------- 交互 ----------------

    def _run(self, coro):
        """在引擎线程的事件循环里执行协程。
        出错必须弹窗提示 —— 否则窗口版（--windowed）没有控制台，用户只会觉得「点了没反应」。"""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout=10)
        except Exception as e:
            W.log("GUI 操作失败：%r" % e)
            try:
                messagebox.showerror("操作失败", f"{type(e).__name__}: {e}")
            except Exception:
                pass
            return None

    def apply_preset(self, key):
        name = W.PRESETS[key][0]
        self._run(W.set_profile(W.build_preset_profile(key), key))
        W.log_event("config", "切换场景：" + name)
        self.load_current()
        self.refresh()

    def apply_custom(self):
        prof = W.default_profile("自定义")
        for key, _l, _u, _t in FIELDS:
            try:
                val = float(self.vars[key].get())
            except Exception:
                val = 0.0
            prof[key] = max(0, val)
        if prof["down_kbps"] <= 0 and prof["up_kbps"] <= 0 and prof["latency_down_ms"] <= 0 \
                and prof["latency_up_ms"] <= 0 and prof["loss_pct"] <= 0 \
                and prof["disconnect_pct"] <= 0 and prof["timeout_pct"] <= 0 \
                and prof["dns_fail_pct"] <= 0 and prof["reject_pct"] <= 0 \
                and prof["flap_sec"] <= 0:
            prof["name"] = "直连（不限制）"
        self._run(W.set_profile(prof, "custom"))
        W.log_event("config", "应用自定义参数：" + prof["name"])
        self.refresh()

    def load_current(self):
        p = W.G.profile
        for key, _l, _u, _t in FIELDS:
            v = p.get(key, 0)
            self.vars[key].set(str(int(v)) if float(v).is_integer() else str(v))

    def toggle_sysproxy(self):
        if sys.platform != "win32":
            messagebox.showinfo("提示", "一键接管仅支持 Windows（其它系统请在系统设置里填代理）。")
            return
        cur = W.sysproxy_enabled()
        ok = W.set_sysproxy(not cur)
        if ok:
            W.log_event("config", "已" + ("开启" if not cur else "解除") + "本机流量接管（系统代理）")
        else:
            messagebox.showerror("失败", "修改系统代理失败，请以管理员身份运行。")
        self.refresh_header()

    def toggle_lan(self):
        host = "0.0.0.0" if W.G.proxy_host == "127.0.0.1" else "127.0.0.1"
        self._run(W.restart_proxy(host))
        W.log_event("config", "代理监听切换为 " + host)
        self.refresh_header()

    def open_dash(self):
        webbrowser.open(f"http://127.0.0.1:{self.ctrl_port}/")

    def stop_all(self):
        self._run(W.set_profile(W.default_profile(), "direct"))
        W.log_event("config", "停止模拟，恢复直连")
        self.load_current()
        self.refresh()

    def on_close(self):
        if self._closing:
            return
        self._closing = True
        try:
            if W.sysproxy_enabled():
                W.set_sysproxy(False)
                W.log_event("config", "退出：已解除本机流量接管")
        except Exception:
            pass
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass
        self.root.destroy()

    # ---------------- 刷新 ----------------

    def refresh_header(self):
        lan = W.G.proxy_host == "0.0.0.0"
        addr = f"{W.G.lan_ip}:{W.G.proxy_port}" if lan else f"127.0.0.1:{W.G.proxy_port}"
        self.lab_proxy.config(text=f"代理地址：{addr}　（App / 设备把代理指向它）")
        self.lab_ctrl.config(text=f"Web 控制台：http://127.0.0.1:{self.ctrl_port}/"
                                  f"　局域网 IP：{W.G.lan_ip}")
        self.btn_lan.config(text="关闭局域网访问" if lan else "开启局域网访问")
        try:
            on = W.sysproxy_enabled()
        except Exception:
            on = False
        self.btn_sys.config(text="解除接管" if on else "一键接管本机流量",
                            bg=OK if on else ACCENT)

    def refresh(self):
        if self._closing:
            return
        g = W.G
        now = time.time()
        dt = now - self._last[0]
        if dt >= 0.4:
            self._rate = ((g.bytes_down - self._last[1]) / dt,
                          (g.bytes_up - self._last[2]) / dt)
            self._last = (now, g.bytes_down, g.bytes_up)

        self.refresh_header()
        self.dot.config(text=("● 服务运行中" if g.gate_open else "● 周期断网中"),
                        fg=OK if g.gate_open else WARN)
        self.stat["scene"].config(text=g.profile.get("name", "-"))
        self.stat["conns"].config(text=str(g.conns_total))
        self.stat["active"].config(text=str(g.conns_active))
        self.stat["down"].config(text=fmt_rate(self._rate[0]))
        self.stat["up"].config(text=fmt_rate(self._rate[1]))
        self.stat["gate"].config(text="畅通" if g.gate_open else "已断网",
                                 fg=OK if g.gate_open else WARN)

        for key, btn in self.preset_btns.items():
            if key == g.preset_key:
                btn.config(bg=ACCENT, fg="white")
            else:
                btn.config(bg="#eef2f7", fg=TEXT)

        ev = list(g.events)
        if len(ev) != self._ev_shown:
            self._ev_shown = len(ev)
            self.logbox.delete(0, "end")
            for e in list(ev)[-60:][::-1]:
                self.logbox.insert("end", f"[{e['t']}] {e['msg']}")

        self.root.after(500, self.refresh)


def pick_ports(proxy_port, ctrl_port):
    """端口被占用时自动顺延（与 CLI 行为一致）"""
    if not W.can_bind(ctrl_port, "127.0.0.1"):
        if W.is_weaknet_running(ctrl_port):
            return None, None
        ctrl_port = W.find_free_port(ctrl_port, "127.0.0.1")
    if not W.can_bind(proxy_port, "127.0.0.1"):
        proxy_port = W.find_free_port(proxy_port, "127.0.0.1")
    return proxy_port, ctrl_port


def main():
    ap = argparse.ArgumentParser(description="WeakNet GUI")
    ap.add_argument("--proxy-port", type=int, default=8888)
    ap.add_argument("--ctrl-port", type=int, default=8899)
    args = ap.parse_args()

    W.G.lan_ip = W.detect_lan_ip()
    proxy_port, ctrl_port = pick_ports(args.proxy_port, args.ctrl_port)
    if proxy_port is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "WeakNet",
            f"已经有一个 WeakNet 实例在运行：\nhttp://127.0.0.1:{args.ctrl_port}/\n\n"
            "本程序不再重复启动，请直接使用已有的实例。")
        webbrowser.open(f"http://127.0.0.1:{args.ctrl_port}/")
        root.destroy()
        return

    ready = threading.Event()
    loop = asyncio.new_event_loop()

    def engine():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(W.serve("127.0.0.1", proxy_port, ctrl_port))
        except Exception as e:
            W.log("服务启动失败：%r" % e)
            ready.set()
            return
        W.log(f"GUI 启动：代理 127.0.0.1:{proxy_port} 控制台 http://127.0.0.1:{ctrl_port}/")
        ready.set()
        loop.run_forever()

    threading.Thread(target=engine, daemon=True).start()
    if not ready.wait(15):
        W.log("服务启动超时")

    root = tk.Tk()
    App(root, loop, ctrl_port)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        W.log(traceback.format_exc())
        raise
