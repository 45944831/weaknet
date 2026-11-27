# -*- coding: utf-8 -*-
"""环境共享二级窗口（从主界面拆分的独立面板）

从 weaknet_gui.py 整段搬出（方法体与原文件逐字一致，只补了 import）。
App 通过多继承复用：面板之间不互相调用，只依赖引擎状态与主题层。
"""
import time

import tkinter as tk
from tkinter import simpledialog

import weaknet as W

from gui_theme import (ACCENT, BG, BTN2, CARD, FONT, FONT_B, FONT_T, LINE, MUTED, SELECT, TEXT, _scrollbar)
from gui_text import summarize

class ShareMixin:
    """Share。仅供 App 多继承使用，不单独实例化。"""

    def open_share(self):
        """打开二级窗口：局域网互推自定义环境与弱网组合。

        网页控制台下线后，这里是 Windows 端唯一的环境共享入口：
        改名字 → 设备列表里能看到同局域网的其它 WeakNet → 选设备 + 勾选内容推送 →
        对方那边同名会进他的收件箱，我方这边的收件箱同样在里面处理。

        弱网组合与自定义环境共用这一个窗口：组合里引用的自定义场景在对方机器上
        通常不存在，连同参数快照一起推过去，落到对方那边才有意义。
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
        # 弱网组合那一栏加上后控件变多，高度不够会把推送按钮顶出窗口底部
        try:
            sh = win.winfo_screenheight()
        except Exception:
            sh = 800
        win.geometry("640x%d" % min(760, max(420, sh - 100)))
        win.configure(bg=BG)
        try:
            win.transient(self.root)
        except Exception:
            pass
        self.share_win = win

        head = tk.Frame(win, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        head.pack(fill="x", padx=12, pady=12)
        # 不放「返回」按钮：标题栏右上角的 ✕ 就能关这扇窗，
        # 两个入口干的是同一件事，看的人只会犯迷糊
        tk.Label(head, text="环境共享", font=FONT_T, bg=CARD, fg=TEXT).pack(
            side="left")
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
        tk.Button(row, text="保存名字", font=FONT, bg=BTN2, fg=TEXT,
                  relief="flat", padx=12, pady=4, cursor="hand2",
                  command=self.save_share_name).pack(side="left")

        # PIN：推送方必须带对才能入库。发现广播里会带上它，方便同网段的设备直接推；
        # 换 PIN 后对方需要重新发现（最多 3 秒一次广播）才能继续推。
        prow = tk.Frame(win, bg=BG)
        prow.pack(fill="x", padx=12, pady=(6, 0))
        tk.Label(prow, text="共享 PIN", font=FONT, bg=BG, fg=MUTED, width=8,
                 anchor="w").pack(side="left")
        self.share_pin = tk.StringVar(value=W.G.share_pin or "")
        pent = tk.Entry(prow, textvariable=self.share_pin, font=FONT, width=6,
                        relief="flat", highlightthickness=1, highlightbackground=LINE,
                        highlightcolor=ACCENT, bg=CARD, fg=TEXT, justify="center")
        pent.pack(side="left", padx=(6, 6))
        pent.bind("<Return>", lambda _e: self.save_share_pin())
        tk.Button(prow, text="保存 PIN", font=FONT, bg=BTN2, fg=TEXT,
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  command=self.save_share_pin).pack(side="left")
        tk.Button(prow, text="随机换一个", font=FONT, bg=BTN2, fg=TEXT,
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  command=self.refresh_share_pin).pack(side="left", padx=(8, 0))

        lrow = tk.Frame(win, bg=BG)
        lrow.pack(fill="x", padx=12, pady=(4, 0))
        self.share_legacy = tk.BooleanVar(value=bool(W.G.allow_legacy))
        tk.Checkbutton(lrow, text="允许旧版设备推送（无 PIN，内容只进收件箱）",
                       variable=self.share_legacy, font=FONT, bg=BG, fg=MUTED,
                       activebackground=BG, selectcolor=SELECT, anchor="w",
                       command=self.toggle_share_legacy).pack(side="left")

        self.share_peers = self._share_group(win, "局域网里的设备", 5)
        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=12, pady=(6, 0))
        tk.Button(bar, text="刷新", font=FONT, bg=BTN2, fg=TEXT, relief="flat",
                  padx=12, pady=4, cursor="hand2",
                  command=self.reload_share).pack(side="left")
        self.share_peers.bind("<<ListboxSelect>>", lambda _e: self._share_sync_btns())

        self.share_mine = self._share_group(win, "我的自定义环境（可多选）", 5)
        self.share_mine.configure(selectmode=tk.EXTENDED)
        self.share_mine.bind("<<ListboxSelect>>", lambda _e: self._share_sync_btns())

        # 弱网组合单独一栏：里面的段可能引用本机才有的自定义场景，连同参数一起推过去
        self.share_combo = self._share_group(win, "我的弱网组合（可多选）", 4)
        self.share_combo.configure(selectmode=tk.EXTENDED)
        self.share_combo.bind("<<ListboxSelect>>", lambda _e: self._share_sync_btns())

        bar2 = tk.Frame(win, bg=BG)
        bar2.pack(fill="x", padx=12, pady=(6, 0))
        self.btn_push = tk.Button(bar2, text="→ 推送所选内容", font=FONT, bg=ACCENT,
                                  fg="white", relief="flat", padx=14, pady=5,
                                  cursor="hand2", command=self.push_share)
        self.btn_push.pack(side="left")

        self.share_inbox = self._share_group(win, "收到的内容（同名待处理）", 4)
        bar3 = tk.Frame(win, bg=BG)
        bar3.pack(fill="x", padx=12, pady=(6, 0))
        self.btn_in_over = tk.Button(bar3, text="覆盖本地", font=FONT, bg=BTN2,
                                     fg=TEXT, relief="flat", padx=12, pady=4,
                                     cursor="hand2",
                                     command=lambda: self.resolve_share("overwrite"))
        self.btn_in_over.pack(side="left")
        self.btn_in_keep = tk.Button(bar3, text="保留本地", font=FONT, bg=BTN2,
                                     fg=TEXT, relief="flat", padx=12, pady=4,
                                     cursor="hand2",
                                     command=lambda: self.resolve_share("keep"))
        self.btn_in_keep.pack(side="left", padx=(8, 0))
        self.btn_in_save = tk.Button(bar3, text="另存为…", font=FONT, bg=BTN2,
                                     fg=TEXT, relief="flat", padx=12, pady=4,
                                     cursor="hand2",
                                     command=lambda: self.resolve_share("rename"))
        self.btn_in_save.pack(side="left", padx=(8, 0))
        self.share_inbox.bind("<<ListboxSelect>>", lambda _e: self._share_sync_btns())

        self.lab_share_tip = tk.Label(win, text="", font=FONT, bg=BG, fg=MUTED,
                                      anchor="w", justify="left", wraplength=560)
        self.lab_share_tip.pack(fill="x", padx=14, pady=(8, 0))

        # 底部原本只有一个「返回」，跟标题栏的 ✕ 是同一个功能。
        # 去掉以后这排按钮就没内容了，整个 foot 框一起删掉，
        # 免得在窗底留一条空白横杠
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
        sb = _scrollbar(wrap, command=box.yview)
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
        pick_combo = _picked(self.share_combo, getattr(self, "share_combo_list", []), "id")

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

        self.share_combo_list = W.get_combos()
        self.share_combo.delete(0, "end")
        for c in self.share_combo_list:
            self.share_combo.insert("end", "%-20s %s"
                                    % (c["name"][:20], W.combo_summary(c)))
        if not self.share_combo_list:
            self.share_combo.insert("end", "（还没有弱网组合：先在「🔀 弱网组合」里排一支）")
        for i, c in enumerate(self.share_combo_list):
            if c["id"] in pick_combo:
                self.share_combo.selection_set(i)

        self.share_inbox_list = sorted(W.G.inbox.values(), key=lambda x: x["at"])
        self.share_inbox.delete(0, "end")
        for it in self.share_inbox_list:
            kind = "组合" if (it.get("kind") or "preset") == "combo" else "环境"
            self.share_inbox.insert(
                "end", "[%s] %-12s 推送「%s」 · %ds前"
                % (kind, it["from"][:12], it["name"], int(now - it["at"])))
        if not self.share_inbox_list:
            self.share_inbox.insert("end", "（收件箱为空：别人推来同名内容时会出现在这里）")
        for i, it in enumerate(self.share_inbox_list):
            if it["id"] in pick_inbox:
                self.share_inbox.selection_set(i)

        self.lab_share_me.config(text="本机：%s · PIN %s · 端口 %d"
                                 % (W.G.user_name or "-", W.G.share_pin or "-",
                                    W.G.share_port or W.SHARE_PORT))
        self._share_sync_btns()
        # 设备会上下线、别人随时可能推环境过来，窗口开着就持续刷新
        self.share_after = self.share_win.after(3000, self.reload_share)

    def _share_sync_btns(self):
        """没有选中项时把对应按钮置灰，免得点了没反应还得猜为什么"""
        if getattr(self, "share_win", None) is None:
            return
        peers = [i for i in self.share_peers.curselection() if i < len(self.share_peer_list)]
        mine = [i for i in self.share_mine.curselection() if i < len(self.share_mine_list)]
        combos = [i for i in self.share_combo.curselection()
                  if i < len(getattr(self, "share_combo_list", []))]
        inbox = [i for i in self.share_inbox.curselection() if i < len(self.share_inbox_list)]
        self.btn_push.config(state="normal" if (peers and (mine or combos)) else "disabled")
        for b in (getattr(self, "btn_in_over", None), getattr(self, "btn_in_keep", None),
                  getattr(self, "btn_in_save", None)):
            if b is not None:
                b.config(state="normal" if inbox else "disabled")

    def save_share_name(self):
        nm = W.set_user_name(self.share_name.get() or "")
        self.share_name.set(nm)
        W.log_event("share", "局域网显示名改为：" + nm)
        self.reload_share()

    def save_share_pin(self):
        res = W.set_share_pin(self.share_pin.get())
        if res.get("ok"):
            self.share_pin.set(res["pin"])
            self.lab_share_tip.config(text="PIN 已保存：%s（对方推送时需要它）" % res["pin"])
        else:
            self.lab_share_tip.config(text="PIN 需为 4 位数字")

    def refresh_share_pin(self):
        res = W.set_share_pin(refresh=True)
        if res.get("ok"):
            self.share_pin.set(res["pin"])
            self.lab_share_tip.config(text="已换新 PIN：%s（发现广播最多 3 秒后同步给对方）"
                                      % res["pin"])
        self.reload_share()

    def toggle_share_legacy(self):
        on = bool(self.share_legacy.get())
        W.set_allow_legacy(on)
        self.lab_share_tip.config(
            text="已允许旧版设备推送：内容只会进收件箱" if on else "已拒绝旧版设备推送")

    def push_share(self):
        selp = [i for i in self.share_peers.curselection() if i < len(self.share_peer_list)]
        selm = [i for i in self.share_mine.curselection() if i < len(self.share_mine_list)]
        selc = [i for i in self.share_combo.curselection()
                if i < len(getattr(self, "share_combo_list", []))]
        if not selp or (not selm and not selc):
            return
        peer = self.share_peer_list[selp[0]]
        ids = [self.share_mine_list[i]["id"] for i in selm]
        cids = [self.share_combo_list[i]["id"] for i in selc]
        # 结果文案里把两类分开报：组合和环境同名判定各自一套，混在一起看不出推了啥
        what = [self.share_mine_list[i]["name"] for i in selm] + \
               ["🔀 " + self.share_combo_list[i]["name"] for i in selc]
        self.btn_push.config(state="disabled", text="推送中…")
        try:
            res = self._run(W.push_to_peer(peer, ids, cids))
        except Exception as e:
            res = {"ok": False, "error": str(e)[:120]}
        self.btn_push.config(text="→ 推送所选内容")
        # 结果文案：引擎已经写过事件日志，这里只补一句人能看懂的反馈
        if res and res.get("ok"):
            tip = "已推送给「%s」：%s" % (res.get("to") or peer.get("name") or "对方",
                                       "、".join(what))
            if res.get("pending"):
                tip += "（对方有同名内容，等他确认）"
            if res.get("dropped"):
                names = "、".join(str(d.get("name") or "") for d in res["dropped"])
                tip += "（对方那边收不下：%s）" % names
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
        kind = (it.get("kind") or "preset") == "combo"
        new_name = None
        if action == "rename":
            try:
                new_name = simpledialog.askstring(
                    "另存为", "把「%s」存成什么名字？（最多 %d 字）"
                    % (it["name"], W.NAME_MAX),
                    initialvalue=W.limit_item_name(
                        "%s（来自 %s）" % (it["name"], it["from"]),
                        W.limit_item_name(it["name"], "自定义")),
                    parent=self.share_win)
            except Exception:
                new_name = None
            if not new_name:
                return
            new_name = W.limit_item_name(new_name)
            if not new_name:
                return
        res = W.resolve_inbox(it["id"], action, new_name)
        if res.get("ok"):
            acts = {"keep": "保留本地版本",
                    "rename": "另存为新组合" if kind else "另存为新环境",
                    "overwrite": "覆盖本地版本"}
            self.lab_share_tip.config(text="已处理「%s%s」→ %s"
                                      % ("🔀 " if kind else "",
                                         res.get("name") or it["name"],
                                         acts.get(action, action)))
        else:
            self.lab_share_tip.config(text="处理失败：" + str(res.get("error")))
        self.reload_share()
        self.reload_mine()        # 覆盖 / 另存都会改变自定义场景列表
        self.reload_combo()       # 组合覆盖了 / 另存了，顺便也会新建一个自定义场景
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

