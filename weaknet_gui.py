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


def summarize(prof):
    """一行简述参数要点 —— 列表里只看名字分不清两条场景的差别"""
    bits = []
    if prof.get("down_kbps") or prof.get("up_kbps"):
        bits.append("↓%s/↑%s kbps" % (prof.get("down_kbps") or "∞",
                                      prof.get("up_kbps") or "∞"))
    if prof.get("latency_down_ms") or prof.get("latency_up_ms"):
        bits.append("延迟 %s/%s ms" % (prof.get("latency_down_ms", 0),
                                       prof.get("latency_up_ms", 0)))
    if prof.get("loss_pct"):
        bits.append("丢包 %g%%" % prof["loss_pct"])
    if prof.get("disconnect_pct"):
        bits.append("断连 %g%%" % prof["disconnect_pct"])
    if prof.get("timeout_pct"):
        bits.append("黑洞 %g%%" % prof["timeout_pct"])
    if prof.get("dns_fail_pct"):
        bits.append("DNS %g%%" % prof["dns_fail_pct"])
    if prof.get("reject_pct"):
        bits.append("拒连 %g%%" % prof["reject_pct"])
    if prof.get("flap_sec"):
        bits.append("周期断网 %gs" % prof["flap_sec"])
    return " · ".join(bits) or "（不限制）"


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
        self._build_scope()
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

    def _card_fold(self, parent, title, collapsed=True):
        """可折叠卡片：点标题栏展开 / 收起，默认收起。返回内容容器（与 _card 一致）。

        注意：tkinter 的事件不会自动冒泡到父控件（bindtags 里没有父 widget），
        所以 head 和它每个子控件都要各自绑定一次；但也绝不能重复绑定同一控件，
        否则点一下会触发两次 toggle —— 展开后立刻收起，表现为「点了没反应」。
        """
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="x", padx=14, pady=(0, 10))

        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x")
        arrow = tk.Label(head, text="▸", font=FONT_B, bg=BG, fg=MUTED)
        arrow.pack(side="left")
        tk.Label(head, text=title, font=FONT_B, bg=BG, fg=TEXT).pack(side="left", padx=(4, 0))
        hint = tk.Label(head, text="（点击展开）", font=FONT, bg=BG, fg=MUTED)
        hint.pack(side="left", padx=(6, 0))

        card = tk.Frame(outer, bg=CARD, highlightbackground=LINE, highlightthickness=1, bd=0)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=12, pady=10)

        def toggle(_event=None):
            # 用 winfo_manager() 而不是 winfo_ismapped()：前者只反映「有没有被 pack」，
            # 不受窗口当前是否可见影响，判断更准确（自检时窗口可能尚未显示）
            if card.winfo_manager():
                card.pack_forget()
                arrow.config(text="▸")
                hint.config(text="（点击展开）")
            else:
                card.pack(fill="x")
                arrow.config(text="▾")
                hint.config(text="（点击收起）")

        for w in [head] + list(head.winfo_children()):
            w.bind("<Button-1>", toggle)
            try:
                w.configure(cursor="hand2")
            except Exception:
                pass
        self._last_fold_toggle = toggle

        if collapsed:
            arrow.config(text="▸")
            hint.config(text="（点击展开）")
        else:
            card.pack(fill="x")
            arrow.config(text="▾")
            hint.config(text="（点击收起）")
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

        # 自定义栏：保存过的场景与内置预设并排常驻，点一下即切换（保存入口在下方自定义参数里）
        self._build_mine(box)

    def _build_custom(self):
        # 预设场景已覆盖绝大多数用例，自定义参数默认收起，需要细调时点标题展开
        box = self._card_fold(self.root,
                              "自定义参数（默认收起 · 点「应用」生效 · 0 表示不限制/关闭）",
                              collapsed=True)
        # 记下卡片本体，便于自检与外部展开/收起
        self.custom_card = self.root.nametowidget(box.winfo_parent())
        self.custom_toggle = self._last_fold_toggle

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
        self._build_mine_save(box)

    def _build_mine_save(self, box):
        """自定义栏的保存入口：给上面这组参数起个名字存下来"""
        tk.Label(box, text="保存为自定义场景", font=FONT_B, bg=CARD, fg=TEXT).pack(
            anchor="w", pady=(12, 4))
        row = tk.Frame(box, bg=CARD)
        row.pack(fill="x")
        tk.Label(row, text="名称", font=FONT, bg=CARD, fg=MUTED, width=6,
                 anchor="w").pack(side="left")
        self.mine_name = tk.StringVar()
        ent = tk.Entry(row, textvariable=self.mine_name, font=FONT, width=26,
                       relief="flat", highlightthickness=1, highlightbackground=LINE,
                       highlightcolor=ACCENT, bg="#fbfcfe", fg=TEXT)
        ent.pack(side="left", padx=(6, 8))
        ent.bind("<Return>", lambda _e: self.save_mine())
        tk.Button(row, text="保存当前参数", font=FONT, bg="#eef2f7", fg=TEXT,
                  relief="flat", padx=12, pady=4, cursor="hand2",
                  command=self.save_mine).pack(side="left")
        tk.Label(box, text="保存后会出现在上方「⭐ 我的自定义」里（存在该程序目录的 "
                           "weaknet_presets.json，重装保留）。",
                 font=FONT, bg=CARD, fg=MUTED, anchor="w",
                 justify="left").pack(fill="x", pady=(6, 0))

    def _build_mine(self, box):
        """自定义栏：已保存的场景列表（与内置预设同处一张卡片，始终可见）"""
        tk.Frame(box, bg=LINE, height=1).pack(fill="x", pady=(10, 8))
        tk.Label(box, text="⭐ 我的自定义", font=FONT_B, bg=CARD, fg=TEXT).pack(anchor="w")

        wrap = tk.Frame(box, bg=CARD)
        wrap.pack(fill="x", pady=(6, 0))
        self.mine_box = tk.Listbox(wrap, height=4, selectmode=tk.SINGLE,
                                   font=("Consolas", 9), bg="#fbfcfe",
                                   fg=TEXT, relief="flat", highlightthickness=1,
                                   highlightbackground=LINE, bd=0,
                                   exportselection=False)
        sb = tk.Scrollbar(wrap, command=self.mine_box.yview)
        self.mine_box.configure(yscrollcommand=sb.set)
        self.mine_box.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")
        self.mine_box.bind("<Double-Button-1>", lambda _e: self.apply_mine())

        bar = tk.Frame(box, bg=CARD)
        bar.pack(fill="x", pady=(8, 0))
        tk.Button(bar, text="应用选中", font=FONT, bg=ACCENT, fg="white",
                  relief="flat", padx=14, pady=4, cursor="hand2",
                  command=self.apply_mine).pack(side="left")
        tk.Button(bar, text="删除选中", font=FONT, bg="#eef2f7", fg=TEXT,
                  relief="flat", padx=12, pady=4, cursor="hand2",
                  command=self.del_mine).pack(side="left", padx=(8, 0))
        tk.Button(bar, text="刷新列表", font=FONT, bg="#eef2f7", fg=TEXT,
                  relief="flat", padx=12, pady=4, cursor="hand2",
                  command=self.reload_mine).pack(side="left", padx=(8, 0))
        self.lab_mine = tk.Label(box, text="", font=FONT, bg=CARD, fg=MUTED,
                                 anchor="w", justify="left", wraplength=780)
        self.lab_mine.pack(fill="x", pady=(6, 0))
        self.reload_mine()

    # ---------------- 自定义栏 ----------------

    def reload_mine(self):
        """重画列表。Web 控制台那一端增删的场景，点「刷新列表」即可同步过来"""
        self.mine_items = W.get_user_presets()
        self.mine_sig = ",".join(it["id"] for it in self.mine_items)
        self.mine_box.delete(0, "end")
        for it in self.mine_items:
            self.mine_box.insert("end", "%s    %s" % (it["name"], summarize(it["profile"])))
        if self.mine_items:
            self.lab_mine.config(text="双击或选中后点「应用选中」即切换到该场景；"
                                      "在「自定义参数」里调好后命名即可新增。")
        else:
            self.lab_mine.config(text="还没有保存的场景：展开「自定义参数」调好参数，"
                                      "填个名字点「保存当前参数」。")

    def _sel_mine(self):
        sel = self.mine_box.curselection()
        if not sel:
            messagebox.showinfo("我的自定义", "请先在列表里选中一个场景。")
            return None
        i = int(sel[0])
        return self.mine_items[i] if i < len(self.mine_items) else None

    def save_mine(self):
        name = (self.mine_name.get() or "").strip()
        if not name:
            messagebox.showinfo("我的自定义", "请先给这组参数起个名字，再点保存。")
            return
        prof = {}
        for key, _l, _u, _t in FIELDS:
            try:
                prof[key] = max(0, float(self.vars[key].get()))
            except Exception:
                prof[key] = 0
        item, _items = W.upsert_user_preset(name, prof)
        W.log_event("config", "保存自定义场景：" + item["name"])
        self.mine_name.set("")
        self.reload_mine()
        self.refresh()

    def apply_mine(self):
        it = self._sel_mine()
        if it is None:
            return
        try:
            prof = W.build_user_profile("u:" + it["id"])
        except Exception:
            messagebox.showerror("我的自定义", "这条场景已不存在，请点「刷新列表」。")
            self.reload_mine()
            return
        self._run(W.set_profile(prof, "u:" + it["id"]))
        W.log_event("config", "切换场景：" + it["name"])
        self.load_current()
        self.refresh()

    def del_mine(self):
        it = self._sel_mine()
        if it is None:
            return
        if not messagebox.askyesno("删除自定义场景",
                                   "确定删除「%s」？此操作不可撤销。" % it["name"]):
            return
        ok, _left = W.delete_user_preset(it["id"])
        if ok:
            W.log_event("config", "删除自定义场景：" + it["name"])
        self.reload_mine()
        self.refresh()

    def _build_stats(self):
        pass   # 实时状态已并入顶栏（见 _build_header）

    def _build_scope(self):
        """接管范围：指定哪些程序进入弱网模拟（可多选）。

        Windows 的系统代理是进程无关的，无法只对某个 exe 生效，因此这里改在
        代理层按来源进程过滤 —— 范围外的程序照样经过代理，但会被原样转发、
        不受任何弱网影响，效果等同没被接管。
        """
        box = self._card_fold(self.root,
                              "接管范围（默认收起 · 指定哪些程序进入弱网 · 可多选）",
                              collapsed=True)
        self.scope_card = self.root.nametowidget(box.winfo_parent())
        self.scope_toggle = self._last_fold_toggle

        self.scope_mode = tk.StringVar(value="all")
        modes = tk.Frame(box, bg=CARD)
        modes.pack(fill="x")
        for val, text in [("all", "全部接管"),
                          ("only", "仅接管选定程序"),
                          ("exclude", "排除选定程序")]:
            tk.Radiobutton(modes, text=text, variable=self.scope_mode, value=val,
                           bg=CARD, fg=TEXT, font=FONT, selectcolor="#eef3ff",
                           activebackground=CARD, highlightthickness=0,
                           command=self._scope_tip).pack(side="left", padx=(0, 14))

        self.lab_scope = tk.Label(box, text="", font=FONT, bg=CARD, fg=MUTED,
                                  justify="left", anchor="w", wraplength=780)
        self.lab_scope.pack(fill="x", pady=(6, 4))

        wrap = tk.Frame(box, bg=CARD)
        wrap.pack(fill="x")
        # EXTENDED 支持 Ctrl/Shift 多选；exportselection=False 避免焦点切换时丢选择
        self.scope_box = tk.Listbox(wrap, height=7, selectmode=tk.EXTENDED,
                                    font=("Consolas", 9), bg="#fbfcfe", fg=TEXT,
                                    relief="flat", highlightthickness=1,
                                    highlightbackground=LINE, bd=0,
                                    exportselection=False)
        sb = tk.Scrollbar(wrap, command=self.scope_box.yview)
        self.scope_box.configure(yscrollcommand=sb.set)
        self.scope_box.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")

        bar = tk.Frame(box, bg=CARD)
        bar.pack(fill="x", pady=(8, 0))
        tk.Button(bar, text="应用接管范围", font=FONT_B, bg=ACCENT, fg="white",
                  relief="flat", padx=14, pady=5, cursor="hand2",
                  command=self.apply_scope).pack(side="left")
        tk.Button(bar, text="刷新程序列表", font=FONT, bg="#eef2f7", fg=TEXT,
                  relief="flat", padx=12, pady=5, cursor="hand2",
                  command=self.reload_scope).pack(side="left", padx=(8, 0))
        tk.Button(bar, text="清空选择", font=FONT, bg="#eef2f7", fg=TEXT,
                  relief="flat", padx=12, pady=5, cursor="hand2",
                  command=self.clear_scope).pack(side="left", padx=(8, 0))
        self.reload_scope()

    def reload_scope(self):
        """刷新候选程序列表，并把当前已生效的选择回填到列表里"""
        names = set(W.list_procs()) | set(W.G.scope_seen.keys()) | set(W.G.scope_list)
        names = sorted(n for n in names if n and n != "<未识别>" and n.lower() != "system")
        self.scope_items = names
        self.scope_box.delete(0, "end")
        for n in names:
            self.scope_box.insert("end", n)
        for n in W.G.scope_list:
            if n in names:
                self.scope_box.selection_set(names.index(n))
        self.scope_mode.set(W.G.scope_mode)
        self._scope_tip()

    def apply_scope(self):
        sel = [self.scope_items[i] for i in self.scope_box.curselection()]
        mode, lst = W.set_scope(self.scope_mode.get(), sel)
        if mode == "only":
            W.log_event("config", "接管范围：仅 " + (", ".join(lst) or "（空名单=不接管任何程序）"))
        elif mode == "exclude":
            W.log_event("config", "接管范围：排除 " + (", ".join(lst) or "（空名单=全部接管）"))
        else:
            W.log_event("config", "接管范围：全部程序")
        self._scope_tip()
        self.refresh()

    def clear_scope(self):
        self.scope_box.selection_clear(0, "end")

    def _scope_tip(self):
        """标题下方提示：显示当前已生效的范围 + 未应用的本地改动"""
        g = W.G
        if g.scope_mode == "all":
            eff = "全部程序"
        elif g.scope_mode == "only":
            eff = "仅 %d 个程序" % len(g.scope_list) + (
                "（" + ", ".join(g.scope_list[:4]) + ("…" if len(g.scope_list) > 4 else "") + "）"
                if g.scope_list else "（空名单 = 不接管任何程序）")
        else:
            eff = "排除 %d 个程序" % len(g.scope_list) + (
                "（" + ", ".join(g.scope_list[:4]) + ("…" if len(g.scope_list) > 4 else "") + "）"
                if g.scope_list else "（空名单 = 全部接管）")
        local = [self.scope_items[i] for i in self.scope_box.curselection()]
        dirty = (self.scope_mode.get() != g.scope_mode) or (sorted(local) != sorted(g.scope_list))
        tip = "当前生效：" + eff
        if dirty:
            tip += "　｜　未应用：%s + %d 项" % (
                {"all": "全部", "only": "仅选定", "exclude": "排除选定"}[self.scope_mode.get()],
                len(local))
        tip += "\n范围外的程序仍会经过代理，但会被原样转发、不受弱网影响。" \
               "程序需先发起过网络连接才会出现在列表中；若识别不到进程名，仅选定模式下会按未接管处理。"
        self.lab_scope.config(text=tip)

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

        # 接管范围可能被 Web 控制台那一端改过，提示行同步显示最新的生效值
        try:
            self._scope_tip()
        except Exception:
            pass
        # 自定义栏同理：另一端新增/删除了场景就自动重画列表
        try:
            if ",".join(it["id"] for it in W.get_user_presets()) != self.mine_sig:
                self.reload_mine()
        except Exception:
            pass

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
