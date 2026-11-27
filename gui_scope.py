# -*- coding: utf-8 -*-
"""接管范围（从主界面拆分的独立面板）

从 weaknet_gui.py 整段搬出（方法体与原文件逐字一致，只补了 import）。
App 通过多继承复用：面板之间不互相调用，只依赖引擎状态与主题层。
"""
import tkinter as tk
from tkinter import messagebox

import weaknet as W
import gui_state as S

from gui_theme import (ACCENT, BG, BTN2, CARD, FONT, FONT_B, FONT_T, LINE, MUTED, SELECT, TEXT, _scrollbar)

class ScopeMixin:
    """Scope。仅供 App 多继承使用，不单独实例化。"""

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

    def _scope_sync_btns(self):
        """主界面两个并排入口的当前态：生效中的那个是实心主按钮，另一个是浅底。"""
        if not hasattr(self, "scope_all_btn"):
            return
        v = S.scope_btn_views(W.G.scope_mode == "all")
        for btn, item in ((self.scope_all_btn, v["all"]),
                          (self.scope_open_btn, v["open"])):
            self._set(btn, text=item["text"], bg=self._tone(item["bg"]),
                      fg=self._tone(item["fg"]))

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
        # 不放「返回」按钮：标题栏右上角的 ✕ 就能关这扇窗，
        # 两个入口干的是同一件事，看的人只会犯迷糊
        tk.Label(head, text="接管范围", font=FONT_T, bg=CARD, fg=TEXT).pack(
            side="left")
        self.lab_scope_count = tk.Label(head, text="", font=FONT, bg=CARD, fg=MUTED)
        self.lab_scope_count.pack(side="right")

        modes = tk.Frame(win, bg=BG)
        modes.pack(fill="x", padx=12)
        for val, text in [("all", "全部接管"),
                          ("only", "仅接管选定程序"),
                          ("exclude", "排除选定程序")]:
            tk.Radiobutton(modes, text=text, variable=self.scope_mode, value=val,
                           bg=BG, fg=TEXT, font=FONT, selectcolor=SELECT,
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
        tk.Button(bar, text="全选", font=FONT, bg=BTN2, fg=TEXT, relief="flat",
                  padx=12, pady=4, cursor="hand2",
                  command=lambda: self._scope_all(True)).pack(side="left")
        tk.Button(bar, text="清空", font=FONT, bg=BTN2, fg=TEXT, relief="flat",
                  padx=12, pady=4, cursor="hand2",
                  command=lambda: self._scope_all(False)).pack(side="left", padx=(8, 0))
        tk.Button(bar, text="刷新列表", font=FONT, bg=BTN2, fg=TEXT, relief="flat",
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
        sb = _scrollbar(wrap, command=self.scope_box.yview)
        self.scope_box.configure(yscrollcommand=sb.set)
        self.scope_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.scope_box.bind("<<ListboxSelect>>", self._scope_on_select)

        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=12, pady=10)
        # 底部只留「保存」。取消用标题栏的 ✕ —— 它走的是同一条关闭逻辑
        # （未保存还会先问一句「放弃改动？」），摆两个入口纯属重复
        tk.Button(foot, text="保存", font=FONT_B, bg=ACCENT, fg="white", relief="flat",
                  padx=20, pady=7, cursor="hand2",
                  command=self.apply_scope).pack(side="left", expand=True, fill="x")
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
        self._set(self.lab_scope, text=S.scope_tip(g.scope_mode, g.scope_list))
        self._scope_sync_btns()

