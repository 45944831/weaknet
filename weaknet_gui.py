"""
WeakNet —— App 弱网测试工具（桌面 GUI 版）

用 tkinter 写的原生窗口界面，不依赖任何第三方库。
引擎与 Web 控制台复用 weaknet.py：GUI 线程负责显示，后台线程跑 asyncio 服务。

用法：
    python weaknet_gui.py [--proxy-port 8888] [--ctrl-port 8899]
打包：
    pyinstaller --onefile --windowed --name WeakNet weaknet_gui.py
"""

import argparse
import asyncio
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

# 以 --windowed 打包时没有控制台，sys.stdout / sys.stderr 为 None。
# 必须给它们一个去处，否则任何 print / traceback 输出都会异常导致闪退。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

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
    "direct": "直连", "wifi": "Wi-Fi 良好", "4g": "4G LTE", "5g": "5G 良好",
    "4g_weak": "4G 弱网", "5g_weak": "5G 弱覆盖",
    "wifi_weak": "弱 Wi-Fi", "congestion": "网络拥塞", "satellite": "卫星链路",
    "highspeed_rail": "高铁/地铁", "elevator": "电梯/地下室",
    "flapping": "频繁断连", "blackhole": "黑洞（无响应）",
    "dns_fail": "DNS 故障", "airplane": "完全断网",
    "high_loss": "高丢包", "high_latency": "高延迟",
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
        self.share_win = None                 # 环境共享二级窗口，None = 当前未打开
        self.share_after = None               # 窗口内的定时刷新（关闭时要取消）

        root.title("WeakNet —— App 弱网测试工具")
        root.configure(bg=BG)
        # 按屏幕自适应窗口高度并居中，避免小屏幕上底部内容被任务栏/屏幕边缘裁掉
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        wh = min(860, sh - 90)
        root.geometry(f"880x{wh}+{max(0, (sw - 880) // 2)}+{max(0, (sh - 60 - wh) // 2)}")
        root.minsize(820, min(780, sh - 120))

        self._build_header()
        # 接管范围通栏放在最顶部：它是「这次测谁」的开关，范围不对下面所有参数都白调，
        # 塞在下面的分区里要滚动才看得到，容易测完才发现接管的不是目标程序。
        self._build_scope()
        # 下方分区：左边是要动手的设置（场景预设 / 自定义参数），右边是只看不动的日志。
        # 右栏固定宽度：日志按行显示，太宽反而每行都剩一大截空白。
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True)
        self.col_left = tk.Frame(body, bg=BG)
        self.col_left.pack(side="left", fill="both", expand=True)
        self.col_right = tk.Frame(body, bg=BG, width=340)
        self.col_right.pack(side="left", fill="y")
        self.col_right.pack_propagate(False)      # 锁住宽度，窗口变宽时只让左栏长
        self._build_presets()
        self._build_custom()
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
        """可折叠卡片：标题行放在卡片里，收起时是一张只有标题的卡片（标准折叠条样式）。
        返回内容容器（与 _card 一致）。

        注意：tkinter 的事件不会自动冒泡到父控件（bindtags 里没有父 widget），
        所以 head 和它每个子控件都要各自绑定一次；但也绝不能重复绑定同一控件，
        否则点一下会触发两次 toggle —— 展开后立刻收起，表现为「点了没反应」。
        """
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="x", padx=14, pady=(0, 10))

        card = tk.Frame(outer, bg=CARD, highlightbackground=LINE, highlightthickness=1, bd=0)
        card.pack(fill="x")
        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=12, pady=(9, 8))
        arrow = tk.Label(head, text="▸", font=FONT_B, bg=CARD, fg=MUTED)
        arrow.pack(side="left")
        tk.Label(head, text=title, font=FONT_B, bg=CARD, fg=TEXT).pack(side="left", padx=(4, 0))
        hint = tk.Label(head, text="（点击展开）", font=FONT, bg=CARD, fg=MUTED)
        hint.pack(side="left", padx=(6, 0))

        inner = tk.Frame(card, bg=CARD)

        def toggle(_event=None):
            # 用 winfo_manager() 而不是 winfo_ismapped()：前者只反映「有没有被 pack」，
            # 不受窗口当前是否可见影响，判断更准确（自检时窗口可能尚未显示）
            if inner.winfo_manager():
                inner.pack_forget()
                arrow.config(text="▸")
                hint.config(text="（点击展开）")
            else:
                inner.pack(fill="both", expand=True, padx=12, pady=(0, 10))
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
            inner.pack(fill="both", expand=True, padx=12, pady=(0, 10))
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
        # 环境共享入口不在这里了：它共享的就是「我的自定义」里的场景，
        # 按钮跟着那张卡片的标题走（见 _build_mine），点一下弹出共享窗口。
        # 限速总开关：启动后默认未开启，用户点了才生效（见 toggle_run）
        self.btn_run = tk.Button(btns, font=FONT_B, relief="flat", padx=14, pady=6,
                                 cursor="hand2", command=self.toggle_run)
        self.btn_run.pack(side="right")
        self._sync_run_btn()

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
        # 挂在左栏：接管范围已升到顶部通栏，预设留在左栏与自定义参数同侧，
        # 右栏整列留给日志，两栏高度才不至于一边空一大块
        box = self._card(self.col_left, "场景预设（点击即生效）")
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
                # 4 列：左栏比整窗窄，按 5 列排会顶到卡片外
                b.grid(row=i // 4, column=i % 4, padx=(0, 5), pady=2, sticky="w")
                self.preset_btns[key] = b
            row += 1

        # 自定义栏：保存过的场景与内置预设并排常驻，点一下即切换（保存入口在下方自定义参数里）
        self._build_mine(box)

    def _build_custom(self):
        # 预设场景已覆盖绝大多数用例，自定义参数默认收起，需要细调时点标题展开
        box = self._card_fold(self.col_left,
                              "自定义参数（默认收起 · 点「应用」生效 · 0 表示不限制/关闭）",
                              collapsed=True)
        # 记下卡片本体，便于自检与外部展开/收起
        self.custom_card = self.root.nametowidget(box.winfo_parent())
        self.custom_toggle = self._last_fold_toggle

        grid = tk.Frame(box, bg=CARD)
        grid.pack(fill="x")
        # 两列：卡片进左栏后宽度只有一半，三列会顶出卡片外
        for i, (key, label, unit, top) in enumerate(FIELDS):
            r, c = i // 2, i % 2
            cell = tk.Frame(grid, bg=CARD)
            cell.grid(row=r, column=c, sticky="w", padx=(0, 18), pady=1)
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
        tk.Label(box, text="保存后与内置场景并排显示、点一下即切换；选中它后"
                           "「删除当前场景」按钮才会出现（存在该程序目录的 "
                           "weaknet_presets.json，重装保留）。",
                 font=FONT, bg=CARD, fg=MUTED, anchor="w", justify="left",
                 wraplength=430).pack(fill="x", pady=(6, 0))

    def _build_mine(self, box):
        """自定义场景：与内置预设同处一张卡片、用同样的按钮样式。

        不再单独拉一个带「删除选中」的列表——保存后就跟内置档位长得一样，
        点一下即切换；删除入口跟着「当前选中的场景」出现（见 _sync_mine_del）。
        """
        tk.Frame(box, bg=LINE, height=1).pack(fill="x", pady=(10, 8))
        # 标题行：环境共享共享的就是下面这些自定义环境，按钮跟标题同行最顺手
        mhead = tk.Frame(box, bg=CARD)
        mhead.pack(fill="x")
        self.mine_head = tk.Label(mhead, text="⭐ 我的自定义", font=FONT_B,
                                  bg=CARD, fg=TEXT)
        self.mine_head.pack(side="left")
        self.btn_share = tk.Button(mhead, text="🔗 环境共享", font=FONT, bg="#eef2f7",
                                   fg=TEXT, relief="flat", padx=10, pady=3,
                                   cursor="hand2", command=self.open_share)
        self.btn_share.pack(side="left", padx=(10, 0))
        self.mine_grid = tk.Frame(box, bg=CARD)
        self.mine_grid.pack(fill="x", pady=(6, 0))
        self.mine_btns = {}

        bar = tk.Frame(box, bg=CARD)
        bar.pack(fill="x", pady=(8, 0))
        self.mine_del_bar = bar
        self.btn_mine_del = tk.Button(bar, text="🗑 删除当前场景", font=FONT,
                                      bg="#eef2f7", fg=TEXT, relief="flat",
                                      padx=12, pady=4, cursor="hand2",
                                      command=self.del_mine)
        self.btn_mine_del.pack(side="left")
        self.lab_mine = tk.Label(box, text="", font=FONT, bg=CARD, fg=MUTED,
                                 anchor="w", justify="left", wraplength=430)
        self.lab_mine.pack(fill="x", pady=(6, 0))
        self.reload_mine()

    # ---------------- 自定义栏 ----------------

    def reload_mine(self):
        """重画自定义场景按钮。Web 控制台那一端增删的场景，refresh 会自动同步"""
        self.mine_items = W.get_user_presets()
        self.mine_sig = ",".join(it["id"] for it in self.mine_items)
        for b in self.mine_btns.values():
            b.destroy()
        self.mine_btns = {}
        for i, it in enumerate(self.mine_items):
            key = "u:" + it["id"]
            b = tk.Button(self.mine_grid, text=it["name"], font=FONT, bg="#eef2f7",
                          fg=TEXT, relief="flat", padx=9, pady=3, cursor="hand2",
                          command=lambda k=key: self.apply_preset(k))
            b.grid(row=i // 4, column=i % 4, padx=(0, 5), pady=2, sticky="w")
            self.mine_btns[key] = b
        self._sync_mine_del()

    def _sync_mine_del(self):
        """删除按钮只跟着「当前选中的场景」走：选中内置场景时整个收起"""
        if not hasattr(self, "btn_mine_del"):
            return
        key = W.G.preset_key or ""
        it = None
        if key.startswith("u:"):
            pid = key[2:]
            it = next((x for x in getattr(self, "mine_items", [])
                       if x["id"] == pid), None)
        if it is None:
            self.btn_mine_del.pack_forget()
            self.lab_mine.config(
                text="保存后即可在这里点一下切换；选中某条自定义，"
                     "上方才会出现它的删除按钮。" if self.mine_items
                else "还没有保存的场景：展开「自定义参数」调好参数，"
                     "填个名字点「保存当前参数」。")
            return
        if not self.btn_mine_del.winfo_manager():
            self.btn_mine_del.pack(side="left")
        self.btn_mine_del.config(text="🗑 删除当前场景「%s」" % it["name"])
        self.lab_mine.config(text="只删除这一条保存的场景，不影响内置预设。")

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

    def del_mine(self):
        """删除当前生效的那条自定义场景（按钮只在选中自定义时出现）"""
        key = W.G.preset_key or ""
        if not key.startswith("u:"):
            return
        it = next((x for x in getattr(self, "mine_items", [])
                   if x["id"] == key[2:]), None)
        if it is None:                 # 场景已在别处被删掉，刷新一下即可
            self.reload_mine()
            return
        if not messagebox.askyesno("删除自定义场景",
                                   "确定删除「%s」？此操作不可撤销。" % it["name"]):
            return
        ok, _left = W.delete_user_preset(it["id"])
        if ok:
            W.log_event("config", "删除自定义场景：" + it["name"])
            # 删掉的正是当前生效场景，preset_key 会指向不存在的 id：切回直连最干净
            self._run(W.set_profile(W.default_profile(), "direct"))
            W.log_event("config", "删除后切回直连")
        self.reload_mine()
        self.load_current()
        self.refresh()

    def _build_stats(self):
        pass   # 实时状态已并入顶栏（见 _build_header）

    def _build_scope(self):
        """接管范围：指定哪些程序进入弱网模拟（可多选）。

        Windows 的系统代理是进程无关的，无法只对某个 exe 生效，因此这里改在
        代理层按来源进程过滤 —— 范围外的程序照样经过代理，但会被原样转发、
        不受任何弱网影响，效果等同没被接管。

        候选程序动辄几十上百个，直接摊在主窗口会把下面的面板挤没，
        所以主界面只放两个并排入口（接管一切 / 接管范围…），勾选放进二级窗口（open_scope）。
        """
        box = self._card(self.root, "接管范围")
        self.scope_card = box
        self.scope_win = None                 # 二级窗口，None = 当前未打开
        self.scope_items = []                 # 全部候选程序名
        self.scope_view = []                  # 当前筛选后显示的那部分
        self.scope_sel = set()                # 二级窗口里的勾选（未保存前不进引擎）
        self.scope_mode = tk.StringVar(value=W.G.scope_mode)

        row = tk.Frame(box, bg=CARD)
        row.pack(fill="x")
        tk.Label(row, text="当前：", font=FONT, bg=CARD, fg=MUTED).pack(side="left")
        self.lab_scope = tk.Label(row, text="全部程序", font=FONT_B, bg=CARD,
                                  fg=TEXT, anchor="w")
        self.lab_scope.pack(side="left")
        # 两个入口并排：[接管一切] [接管范围…]，当前生效的那个高亮成实心主按钮
        self.scope_open_btn = tk.Button(
            row, font=FONT_B, relief="flat", padx=14, pady=5,
            cursor="hand2", command=self.open_scope)
        self.scope_open_btn.pack(side="right")
        self.scope_all_btn = tk.Button(
            row, font=FONT_B, relief="flat", padx=14, pady=5,
            cursor="hand2", command=self.scope_all)
        self.scope_all_btn.pack(side="right", padx=(8, 0))
        tk.Label(box, text="范围外的程序仍会经过代理，但会被原样转发、不受弱网影响；"
                           "程序需先发起过网络连接才会出现在列表里。",
                 font=FONT, bg=CARD, fg=MUTED, anchor="w", justify="left",
                 wraplength=800).pack(fill="x", pady=(6, 0))
        self._scope_tip()

    # ---------------- 接管范围：二级窗口 ----------------

    def _scope_sync_btns(self):
        """主界面两个并排入口的当前态：生效中的那个是实心主按钮，另一个是浅底。"""
        if not hasattr(self, "scope_all_btn"):
            return
        allmode = (W.G.scope_mode == "all")
        self.scope_all_btn.config(
            text=("✔ " if allmode else "") + "接管一切",
            bg=ACCENT if allmode else "#eef2f7", fg="white" if allmode else TEXT)
        self.scope_open_btn.config(
            text=("✔ " if not allmode else "") + "接管范围…",
            bg=ACCENT if not allmode else "#eef2f7", fg="white" if not allmode else TEXT)

    def scope_all(self):
        """「接管一切」：一键回到全部接管（已是全部就不重复写，免得多一条无用日志）"""
        if W.G.scope_mode == "all":
            return
        W.set_scope("all", [])
        W.log_event("config", "接管范围：全部程序")
        self.scope_mode.set("all")
        self.scope_sel = set()
        if self.scope_win is not None:
            self._scope_fill()
        self._scope_tip()
        self.refresh()

    def open_scope(self):
        """打开二级窗口：罗列所有候选程序，勾选后保存 / 返回"""
        if self.scope_win is not None:
            try:                              # 已经开着就提到前台，不重复建
                self.scope_win.deiconify()
                self.scope_win.lift()
                return
            except Exception:
                self.scope_win = None

        win = tk.Toplevel(self.root)
        win.title("接管范围")
        win.geometry("520x560")
        win.configure(bg=BG)
        try:
            win.transient(self.root)
        except Exception:
            pass
        self.scope_win = win
        self.scope_mode.set(W.G.scope_mode)
        self.scope_sel = set(W.G.scope_list)      # 以引擎当前值为起点

        head = tk.Frame(win, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        head.pack(fill="x", padx=12, pady=12)
        tk.Button(head, text="← 返回", font=FONT, bg="#eef2f7", fg=TEXT,
                  relief="flat", padx=12, pady=5, cursor="hand2",
                  command=lambda: self.close_scope()).pack(side="left")
        tk.Label(head, text="接管范围", font=FONT_T, bg=CARD, fg=TEXT).pack(
            side="left", padx=(12, 0))
        self.lab_scope_count = tk.Label(head, text="", font=FONT, bg=CARD, fg=MUTED)
        self.lab_scope_count.pack(side="right")

        modes = tk.Frame(win, bg=BG)
        modes.pack(fill="x", padx=12)
        for val, text in [("all", "全部接管"),
                          ("only", "仅接管选定程序"),
                          ("exclude", "排除选定程序")]:
            tk.Radiobutton(modes, text=text, variable=self.scope_mode, value=val,
                           bg=BG, fg=TEXT, font=FONT, selectcolor="#eef3ff",
                           activebackground=BG, highlightthickness=0,
                           command=self._scope_sync_count).pack(side="left", padx=(0, 14))

        self.scope_search = tk.StringVar()
        ent = tk.Entry(win, textvariable=self.scope_search, font=FONT,
                       relief="flat", highlightthickness=1, highlightbackground=LINE,
                       highlightcolor=ACCENT, bg=CARD, fg=TEXT)
        ent.pack(fill="x", padx=12, pady=(8, 0))
        ent.bind("<KeyRelease>", lambda _e: self._scope_fill())

        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=12, pady=(8, 0))
        tk.Button(bar, text="全选", font=FONT, bg="#eef2f7", fg=TEXT, relief="flat",
                  padx=12, pady=4, cursor="hand2",
                  command=lambda: self._scope_all(True)).pack(side="left")
        tk.Button(bar, text="清空", font=FONT, bg="#eef2f7", fg=TEXT, relief="flat",
                  padx=12, pady=4, cursor="hand2",
                  command=lambda: self._scope_all(False)).pack(side="left", padx=(8, 0))
        tk.Button(bar, text="刷新列表", font=FONT, bg="#eef2f7", fg=TEXT, relief="flat",
                  padx=12, pady=4, cursor="hand2",
                  command=self.reload_scope).pack(side="left", padx=(8, 0))
        self.lab_scope_list = tk.Label(bar, text="", font=FONT, bg=BG, fg=MUTED)
        self.lab_scope_list.pack(side="right")

        wrap = tk.Frame(win, bg=BG)
        wrap.pack(fill="both", expand=True, padx=12, pady=(8, 0))
        # EXTENDED 支持 Ctrl/Shift 多选；exportselection=False 避免焦点切换时丢选择
        self.scope_box = tk.Listbox(wrap, selectmode=tk.EXTENDED, font=("Consolas", 9),
                                    bg=CARD, fg=TEXT, relief="flat",
                                    highlightthickness=1, highlightbackground=LINE,
                                    bd=0, exportselection=False)
        sb = tk.Scrollbar(wrap, command=self.scope_box.yview)
        self.scope_box.configure(yscrollcommand=sb.set)
        self.scope_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.scope_box.bind("<<ListboxSelect>>", self._scope_on_select)

        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=12, pady=10)
        tk.Button(foot, text="保存", font=FONT_B, bg=ACCENT, fg="white", relief="flat",
                  padx=20, pady=7, cursor="hand2",
                  command=self.apply_scope).pack(side="left", expand=True, fill="x")
        tk.Button(foot, text="返回", font=FONT, bg="#eef2f7", fg=TEXT, relief="flat",
                  padx=20, pady=7, cursor="hand2",
                  command=lambda: self.close_scope()).pack(side="left", expand=True,
                                                           fill="x", padx=(10, 0))
        win.protocol("WM_DELETE_WINDOW", lambda: self.close_scope())
        win.bind("<Escape>", lambda _e: self.close_scope())

        self.reload_scope()
        try:
            ent.focus_set()
        except Exception:
            pass

    def reload_scope(self):
        """刷新候选程序列表（二级窗口开着时同步重画）"""
        names = set(W.list_procs()) | set(W.G.scope_seen.keys()) | set(W.G.scope_list)
        names = sorted(n for n in names if n and n != "<未识别>" and n.lower() != "system")
        self.scope_items = names
        if self.scope_win is not None:
            self._scope_fill()
        self._scope_tip()

    def _scope_fill(self):
        """按搜索词重画列表，并把 self.scope_sel 回填成选中态"""
        q = (self.scope_search.get() if self.scope_win is not None else "").strip().lower()
        self.scope_view = [n for n in self.scope_items if not q or q in n.lower()]
        self.scope_box.delete(0, "end")
        for n in self.scope_view:
            self.scope_box.insert("end", n)
        for i, n in enumerate(self.scope_view):
            if n in self.scope_sel:
                self.scope_box.selection_set(i)
        self.lab_scope_list.config(
            text="共 %d 项" % len(self.scope_items) if len(self.scope_view) == len(self.scope_items)
            else "筛出 %d / 共 %d 项" % (len(self.scope_view), len(self.scope_items)))
        self._scope_sync_count()

    def _scope_on_select(self, _e=None):
        """把列表里的选择同步回 self.scope_sel。

        搜索会把列表筛短，直接以 curselection() 为准会丢掉被筛掉的那部分，
        所以只对「当前可见项」做增删，不可见项保留原样。
        """
        picked = set(self.scope_view[i] for i in self.scope_box.curselection())
        for n in self.scope_view:
            self.scope_sel.discard(n)
        self.scope_sel |= picked
        self._scope_sync_count()

    def _scope_all(self, on):
        """全选 / 清空，只作用于当前筛选出来的项"""
        for n in self.scope_view:
            if on:
                self.scope_sel.add(n)
            else:
                self.scope_sel.discard(n)
        self._scope_fill()

    def _scope_sync_count(self):
        if self.scope_win is None or not hasattr(self, "lab_scope_count"):
            return
        dirty = self._scope_dirty()
        self.lab_scope_count.config(
            text="已选 %d 项%s" % (len(self.scope_sel), " · 未保存" if dirty else ""))

    def _scope_dirty(self):
        return (self.scope_mode.get() != W.G.scope_mode
                or sorted(self.scope_sel) != sorted(W.G.scope_list))

    def clear_scope(self):
        self.scope_sel.clear()
        if self.scope_win is not None:
            self._scope_fill()

    def apply_scope(self):
        """保存：把二级窗口里的勾选写进引擎，然后关掉窗口回到主界面"""
        mode, lst = W.set_scope(self.scope_mode.get(), sorted(self.scope_sel))
        self.scope_sel = set(lst)
        if mode == "only":
            W.log_event("config", "接管范围：仅 " + (", ".join(lst) or "（空名单=不接管任何程序）"))
        elif mode == "exclude":
            W.log_event("config", "接管范围：排除 " + (", ".join(lst) or "（空名单=全部接管）"))
        else:
            W.log_event("config", "接管范围：全部程序")
        self.close_scope(force=True)
        self._scope_tip()
        self.refresh()

    def close_scope(self, force=False):
        """关闭二级窗口。有未保存改动先问一句 —— 勾了半天点错返回很恼人"""
        if self.scope_win is None:
            return False
        if not force and self._scope_dirty():
            try:
                if not messagebox.askyesno("放弃改动？",
                                           "勾选还没保存，返回将丢弃这些改动。确定返回吗？",
                                           parent=self.scope_win):
                    return False
            except Exception:
                pass
        try:
            self.scope_win.destroy()
        except Exception:
            pass
        self.scope_win = None
        # 丢弃改动，回到引擎当前值：mode 也要一起回滚，否则下次打开窗口
        # 会显示成上次没保存的模式，和引擎里真正生效的对不上
        self.scope_mode.set(W.G.scope_mode)
        self.scope_sel = set(W.G.scope_list)
        self._scope_tip()
        return True

    def _scope_tip(self):
        """主界面那一行摘要：只显示引擎里已生效的范围"""
        g = W.G
        if g.scope_mode == "all":
            eff = "全部程序"
        elif g.scope_mode == "only":
            eff = "仅接管 %d 个程序" % len(g.scope_list) + (
                "（" + ", ".join(g.scope_list[:4]) + ("…" if len(g.scope_list) > 4 else "") + "）"
                if g.scope_list else "（空名单 = 不接管任何程序）")
        else:
            eff = "排除 %d 个程序" % len(g.scope_list) + (
                "（" + ", ".join(g.scope_list[:4]) + ("…" if len(g.scope_list) > 4 else "") + "）"
                if g.scope_list else "（空名单 = 全部接管）")
        self.lab_scope.config(text=eff)
        self._scope_sync_btns()

    # ---------------- 环境共享：二级窗口 ----------------

    def open_share(self):
        """打开二级窗口：局域网互推自定义环境。

        网页控制台下线后，这里是 Windows 端唯一的环境共享入口：
        改名字 → 设备列表里能看到同局域网的其它 WeakNet → 选设备 + 选环境推送 →
        对方那边同名会进他的收件箱，我方这边的收件箱同样在里面处理。
        """
        if getattr(self, "share_win", None) is not None:
            try:                              # 已经开着就提到前台，不重复建
                self.share_win.deiconify()
                self.share_win.lift()
                return
            except Exception:
                self.share_win = None

        W.ensure_identity()          # 首次进来先把「我是谁」补齐
        win = tk.Toplevel(self.root)
        win.title("环境共享")
        win.geometry("640x680")
        win.configure(bg=BG)
        try:
            win.transient(self.root)
        except Exception:
            pass
        self.share_win = win

        head = tk.Frame(win, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        head.pack(fill="x", padx=12, pady=12)
        tk.Button(head, text="← 返回", font=FONT, bg="#eef2f7", fg=TEXT,
                  relief="flat", padx=12, pady=5, cursor="hand2",
                  command=lambda: self.close_share()).pack(side="left")
        tk.Label(head, text="环境共享", font=FONT_T, bg=CARD, fg=TEXT).pack(
            side="left", padx=(12, 0))
        self.lab_share_me = tk.Label(head, text="", font=FONT, bg=CARD, fg=MUTED)
        self.lab_share_me.pack(side="right")

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(row, text="我的名字", font=FONT, bg=BG, fg=MUTED, width=8,
                 anchor="w").pack(side="left")
        self.share_name = tk.StringVar(value=W.G.user_name or "")
        ent = tk.Entry(row, textvariable=self.share_name, font=FONT, relief="flat",
                       highlightthickness=1, highlightbackground=LINE,
                       highlightcolor=ACCENT, bg=CARD, fg=TEXT)
        ent.pack(side="left", fill="x", expand=True, padx=(6, 8))
        ent.bind("<Return>", lambda _e: self.save_share_name())
        tk.Button(row, text="保存名字", font=FONT, bg="#eef2f7", fg=TEXT,
                  relief="flat", padx=12, pady=4, cursor="hand2",
                  command=self.save_share_name).pack(side="left")

        self.share_peers = self._share_group(win, "局域网里的设备", 5)
        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=12, pady=(6, 0))
        tk.Button(bar, text="刷新", font=FONT, bg="#eef2f7", fg=TEXT, relief="flat",
                  padx=12, pady=4, cursor="hand2",
                  command=self.reload_share).pack(side="left")
        self.share_peers.bind("<<ListboxSelect>>", lambda _e: self._share_sync_btns())

        self.share_mine = self._share_group(win, "我的自定义环境（可多选）", 5)
        self.share_mine.configure(selectmode=tk.EXTENDED)
        bar2 = tk.Frame(win, bg=BG)
        bar2.pack(fill="x", padx=12, pady=(6, 0))
        self.btn_push = tk.Button(bar2, text="→ 推送所选环境", font=FONT, bg=ACCENT,
                                  fg="white", relief="flat", padx=14, pady=5,
                                  cursor="hand2", command=self.push_share)
        self.btn_push.pack(side="left")
        self.share_mine.bind("<<ListboxSelect>>", lambda _e: self._share_sync_btns())

        self.share_inbox = self._share_group(win, "收到的环境（同名待处理）", 4)
        bar3 = tk.Frame(win, bg=BG)
        bar3.pack(fill="x", padx=12, pady=(6, 0))
        self.btn_in_over = tk.Button(bar3, text="覆盖本地", font=FONT, bg="#eef2f7",
                                     fg=TEXT, relief="flat", padx=12, pady=4,
                                     cursor="hand2",
                                     command=lambda: self.resolve_share("overwrite"))
        self.btn_in_over.pack(side="left")
        self.btn_in_keep = tk.Button(bar3, text="保留本地", font=FONT, bg="#eef2f7",
                                     fg=TEXT, relief="flat", padx=12, pady=4,
                                     cursor="hand2",
                                     command=lambda: self.resolve_share("keep"))
        self.btn_in_keep.pack(side="left", padx=(8, 0))
        self.btn_in_save = tk.Button(bar3, text="另存为…", font=FONT, bg="#eef2f7",
                                     fg=TEXT, relief="flat", padx=12, pady=4,
                                     cursor="hand2",
                                     command=lambda: self.resolve_share("rename"))
        self.btn_in_save.pack(side="left", padx=(8, 0))
        self.share_inbox.bind("<<ListboxSelect>>", lambda _e: self._share_sync_btns())

        self.lab_share_tip = tk.Label(win, text="", font=FONT, bg=BG, fg=MUTED,
                                      anchor="w", justify="left", wraplength=560)
        self.lab_share_tip.pack(fill="x", padx=14, pady=(8, 0))

        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=12, pady=10)
        tk.Button(foot, text="返回", font=FONT, bg="#eef2f7", fg=TEXT, relief="flat",
                  padx=20, pady=7, cursor="hand2",
                  command=lambda: self.close_share()).pack(fill="x")
        win.protocol("WM_DELETE_WINDOW", lambda: self.close_share())
        win.bind("<Escape>", lambda _e: self.close_share())

        self.reload_share()
        try:
            ent.focus_set()
        except Exception:
            pass

    def _share_group(self, parent, title, height):
        """二级窗口里的「分组标题 + 列表」小组件"""
        tk.Label(parent, text=title, font=FONT_B, bg=BG, fg=TEXT).pack(
            anchor="w", padx=14, pady=(10, 2))
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", padx=12)
        box = tk.Listbox(wrap, height=height, font=("Consolas", 9), bg=CARD, fg=TEXT,
                         relief="flat", highlightthickness=1,
                         highlightbackground=LINE, bd=0, exportselection=False)
        sb = tk.Scrollbar(wrap, command=box.yview)
        box.configure(yscrollcommand=sb.set)
        box.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")
        return box

    def reload_share(self):
        """重画三个列表（设备 / 我的环境 / 收件箱）"""
        if getattr(self, "share_win", None) is None:
            return
        now = time.time()
        # 三个列表都会重建，而窗口里每 3 秒自动刷新一次 —— 不把「选了谁」按 id 存下来
        # 再回填，用户刚勾好的设备/环境就会被下一次刷新冲掉（表现为点了推送没反应）
        def _picked(box, items, key):
            out = set()
            for i in box.curselection():
                if i < len(items):
                    out.add(items[i][key])
            return out
        pick_peer = _picked(self.share_peers, getattr(self, "share_peer_list", []), "id")
        pick_inbox = _picked(self.share_inbox, getattr(self, "share_inbox_list", []), "id")
        pick_mine = _picked(self.share_mine, getattr(self, "share_mine_list", []), "id")

        self.share_peer_list = sorted(W.G.peers.values(), key=lambda x: x["name"].lower())
        self.share_peer_list = [p for p in self.share_peer_list
                                if now - p["at"] <= W.PEER_TTL]
        self.share_peers.delete(0, "end")
        for p in self.share_peer_list:
            self.share_peers.insert(
                "end", "%-16s %-15s %s · %ds前"
                % (p["name"][:16], p["ip"], p.get("platform") or "-", int(now - p["at"])))
        if not self.share_peer_list:
            self.share_peers.insert("end", "（暂未发现其它设备：对方也要开着 WeakNet）")
        for i, p in enumerate(self.share_peer_list):
            if p["id"] in pick_peer:
                self.share_peers.selection_set(i)

        self.share_mine_list = W.get_user_presets()
        self.share_mine.delete(0, "end")
        for it in self.share_mine_list:
            self.share_mine.insert("end", "%-20s %s"
                                   % (it["name"][:20], summarize(it["profile"])))
        for i, it in enumerate(self.share_mine_list):
            if it["id"] in pick_mine:
                self.share_mine.selection_set(i)

        self.share_inbox_list = sorted(W.G.inbox.values(), key=lambda x: x["at"])
        self.share_inbox.delete(0, "end")
        for it in self.share_inbox_list:
            self.share_inbox.insert(
                "end", "%-16s 推送「%s」 · %ds前"
                % (it["from"][:16], it["name"], int(now - it["at"])))
        if not self.share_inbox_list:
            self.share_inbox.insert("end", "（收件箱为空：别人推来同名环境时会出现在这里）")
        for i, it in enumerate(self.share_inbox_list):
            if it["id"] in pick_inbox:
                self.share_inbox.selection_set(i)

        self.lab_share_me.config(text="本机：%s · 共享端口 %d"
                                 % (W.G.user_name or "-", W.G.share_port or W.SHARE_PORT))
        self._share_sync_btns()
        # 设备会上下线、别人随时可能推环境过来，窗口开着就持续刷新
        self.share_after = self.share_win.after(3000, self.reload_share)

    def _share_sync_btns(self):
        """没有选中项时把对应按钮置灰，免得点了没反应还得猜为什么"""
        if getattr(self, "share_win", None) is None:
            return
        peers = [i for i in self.share_peers.curselection() if i < len(self.share_peer_list)]
        mine = [i for i in self.share_mine.curselection() if i < len(self.share_mine_list)]
        inbox = [i for i in self.share_inbox.curselection() if i < len(self.share_inbox_list)]
        self.btn_push.config(state="normal" if (peers and mine) else "disabled")
        for b in (getattr(self, "btn_in_over", None), getattr(self, "btn_in_keep", None),
                  getattr(self, "btn_in_save", None)):
            if b is not None:
                b.config(state="normal" if inbox else "disabled")

    def save_share_name(self):
        nm = W.set_user_name(self.share_name.get() or "")
        self.share_name.set(nm)
        W.log_event("share", "局域网显示名改为：" + nm)
        self.reload_share()

    def push_share(self):
        selp = [i for i in self.share_peers.curselection() if i < len(self.share_peer_list)]
        selm = [i for i in self.share_mine.curselection() if i < len(self.share_mine_list)]
        if not selp or not selm:
            return
        peer = self.share_peer_list[selp[0]]
        ids = [self.share_mine_list[i]["id"] for i in selm]
        self.btn_push.config(state="disabled", text="推送中…")
        try:
            res = self._run(W.push_to_peer(peer, ids))
        except Exception as e:
            res = {"ok": False, "error": str(e)[:120]}
        self.btn_push.config(text="→ 推送所选环境")
        # 结果文案：引擎已经写过事件日志，这里只补一句人能看懂的反馈
        if res and res.get("ok"):
            tip = "已推送给「%s」：%s" % (res.get("to") or peer.get("name") or "对方",
                                       "、".join(self.share_mine_list[i]["name"] for i in selm))
            if res.get("pending"):
                tip += "（对方有同名环境，等他确认）"
        else:
            tip = "推送失败：" + str((res or {}).get("error") or "未知错误")
        try:
            self.lab_share_tip.config(text=tip)
        except Exception:
            pass
        self.reload_share()
        self.refresh()

    def resolve_share(self, action):
        sel = [i for i in self.share_inbox.curselection() if i < len(self.share_inbox_list)]
        if not sel:
            return
        it = self.share_inbox_list[sel[0]]
        new_name = None
        if action == "rename":
            try:
                new_name = simpledialog.askstring(
                    "另存为", "把「%s」存成什么名字？" % it["name"],
                    initialvalue="%s（来自 %s）" % (it["name"], it["from"]),
                    parent=self.share_win)
            except Exception:
                new_name = None
            if not new_name:
                return
        res = W.resolve_inbox(it["id"], action, new_name)
        if res.get("ok"):
            self.lab_share_tip.config(text="已处理：「%s」→ %s"
                                      % (res.get("name") or it["name"],
                                         {"keep": "保留本地版本",
                                          "rename": "另存为新环境",
                                          "overwrite": "覆盖本地版本"}.get(action, action)))
        else:
            self.lab_share_tip.config(text="处理失败：" + str(res.get("error")))
        self.reload_share()
        self.reload_mine()        # 覆盖 / 另存都会改变自定义场景列表
        self.refresh()

    def close_share(self):
        win = getattr(self, "share_win", None)
        if win is None:
            return False
        try:
            if getattr(self, "share_after", None):
                win.after_cancel(self.share_after)
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass
        self.share_win = None
        self.share_after = None
        return True

    def _build_log(self):
        box = self._card(self.col_right, "事件日志", expand=True)
        wrap = tk.Frame(box, bg=CARD)
        wrap.pack(fill="both", expand=True)
        # 右栏一整列都给它，所以不写死行数，跟着窗口高度长
        self.logbox = tk.Listbox(wrap, font=("Consolas", 9), bg="#fbfcfe",
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
        # "u:<id>" = 自定义场景：与内置档位走同一条路径，只是名字要从存档里取
        if str(key).startswith("u:"):
            try:
                prof = W.build_user_profile(key)
            except Exception:
                messagebox.showerror("我的自定义", "这条场景已不存在，已刷新列表。")
                self.reload_mine()
                return
            name = prof.get("name") or "自定义"
        else:
            prof = W.build_preset_profile(key)
            name = W.PRESETS[key][0]
        # 选中 ≠ 开始：没开限速时只把这支场景记下来（按钮照旧高亮），
        # 必须用户点「▶ 开启限速」才真正落到流量上
        applied = self._run(W.select_scene(prof, key))
        W.log_event("config", ("切换场景：" if applied else "选择场景（未开启限速，未生效）：") + name)
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
        # 同上：没开限速时这组参数只是存下来并选中，点「▶ 开启限速」才生效
        applied = self._run(W.select_scene(prof, "custom"))
        W.log_event("config", ("应用自定义参数：" if applied
                               else "记下自定义参数（未开启限速，未生效）：") + prof["name"])
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

    def _sync_run_btn(self):
        """限速总开关的按钮态：未开启时是实心主按钮（邀请点开），运行中变成浅底的停止。"""
        if not hasattr(self, "btn_run"):
            return
        on = W.G.limit_on
        self.btn_run.config(text=("⏹ 停止限速（恢复直连）" if on else "▶ 开启限速"),
                            bg="#eef2f7" if on else ACCENT,
                            fg=TEXT if on else "white")

    def toggle_run(self):
        """开启 / 关闭限速。关闭不是丢场景：preset_key 保留，再开就复原。"""
        want = not W.G.limit_on
        self._run(W.set_limit(want))
        W.log_event("config", "开启限速" if want else "停止限速，恢复直连")
        self.load_current()
        self.refresh()

    def stop_all(self):
        """保留旧入口名：等价于关掉限速总开关"""
        self._run(W.set_limit(False))
        W.log_event("config", "停止限速，恢复直连")
        self.load_current()
        self.refresh()

    def on_close(self):
        if self._closing:
            return
        self._closing = True
        self.close_share()          # 先关二级窗口：它上面挂着定时刷新，别等主窗销毁才取消
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
        self.lab_ctrl.config(text=f"控制接口：http://127.0.0.1:{self.ctrl_port}/"
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
        self._sync_run_btn()      # 另一端（Web 控制台）也可能开关了限速
        try:
            n = len(W.G.inbox)    # 收到的同名环境，没人管就别让按钮看着平平无奇
            self.btn_share.config(text="🔗 环境共享" + ("（%d 条待处理）" % n if n else ""))
        except Exception:
            pass
        # 顶栏那颗状态灯说的是「限速有没有在跑」，不是「服务在不在跑」：
        # 启动后默认未开启，此时流量原样直通，必须让用户一眼看出来
        if not g.limit_on:
            self.dot.config(text="● 限速未开启", fg=MUTED)
        else:
            self.dot.config(text=("● 限速运行中" if g.gate_open else "● 周期断网中"),
                            fg=OK if g.gate_open else WARN)
        # 未开启限速时 profile 还是直连（选场景不会灌进引擎），
        # 直接显示 profile 的名字会永远是「直连（不限制）」——按 preset_key 显示选中的那支
        scene = g.profile.get("name", "-")
        if not g.limit_on and g.preset_key:
            try:
                scene = W.build_preset_profile(g.preset_key).get("name", scene) + "（未开启）"
            except Exception:
                pass
        self.stat["scene"].config(text=scene)
        self.stat["conns"].config(text=str(g.conns_total))
        self.stat["active"].config(text=str(g.conns_active))
        self.stat["down"].config(text=fmt_rate(self._rate[0]))
        self.stat["up"].config(text=fmt_rate(self._rate[1]))
        self.stat["gate"].config(text="畅通" if g.gate_open else "已断网",
                                 fg=OK if g.gate_open else WARN)

        for key, btn in self.preset_btns.items():
            btn.config(bg=ACCENT if key == g.preset_key else "#eef2f7",
                       fg="white" if key == g.preset_key else TEXT)
        # 自定义场景按钮与内置档位同样高亮当前生效的那一个
        for key, btn in getattr(self, "mine_btns", {}).items():
            btn.config(bg=ACCENT if key == g.preset_key else "#eef2f7",
                       fg="white" if key == g.preset_key else TEXT)

        # 接管范围可能被 Web 控制台那一端改过，提示行同步显示最新的生效值
        try:
            self._scope_tip()
        except Exception:
            pass
        # 自定义场景同理：另一端新增/删除了就自动重画按钮，删除入口跟着当前场景
        try:
            if ",".join(it["id"] for it in W.get_user_presets()) != self.mine_sig:
                self.reload_mine()
            else:
                self._sync_mine_del()
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
            f"已经有一个 WeakNet 实例在运行（控制接口 http://127.0.0.1:{args.ctrl_port}/）。\n\n"
            "本程序不再重复启动，请直接使用已经打开的那个界面。")
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
        W.log(f"GUI 启动：代理 127.0.0.1:{proxy_port} 控制接口 http://127.0.0.1:{ctrl_port}/")
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
        err = traceback.format_exc()
        W.log(err)
        # 无控制台时 raise 只是静默闪退，改用弹窗把错误报出来
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("WeakNet 启动失败", err[-2000:])
            root.destroy()
        except Exception:
            pass
        sys.exit(1)
