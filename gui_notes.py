# -*- coding: utf-8 -*-
"""场景说明 / 参数说明 二级窗口（从主界面拆分的独立面板）

从 weaknet_gui.py 整段搬出（方法体与原文件逐字一致，只补了 import）。
App 通过多继承复用：面板之间不互相调用，只依赖引擎状态与主题层。
"""
import tkinter as tk

import weaknet as W

from gui_theme import (ACCENT, BG, BTN2, CARD, CHIP, FONT, FONT_B, FONT_T, LINE, MUTED, TEXT, WARN, _scrollbar)
from gui_text import FIELDS, NOTE_PAD, NOTE_TEXT_W, NOTE_TIP_INDENT, NOTE_WIN_W, _note_measure, detail

class NotesMixin:
    """Notes。仅供 App 多继承使用，不单独实例化。"""

    def _param_help_btn(self, parent, key, gap=2):
        """参数名后面的「！」小入口。

        刻意做得不显眼：无边框、无底色（和卡片同色）、灰色字，只有鼠标移上去
        才变蓝。参数区本来就密，13 个带边框的按钮会把参数名淹掉；但也不能
        做成「只有悬停才出现」—— 那样用键盘或第一次用的人根本不知道有这东西。

        gap：按钮右侧补的空白，由调用方按名字实际宽度算，用来把各行的数字框
        顶到同一列（见 _build_custom）。

        用 Button 而不是可点的 Label：Button 自带按下反馈。takefocus=0 是为了
        不让 Tab 顺序被 13 个「！」打断（调参数时按 Tab 应该直接跳下一个数字框）。
        """
        b = tk.Button(parent, text="！", font=FONT,
                      bg=CARD, fg=MUTED, relief="flat", bd=0,
                      highlightthickness=0, padx=1, pady=0, cursor="hand2",
                      takefocus=0, activebackground=CARD, activeforeground=ACCENT,
                      command=lambda k=key: self.open_param_note(k))
        b.pack(side="left", padx=(1, gap))
        # 悬停才变蓝：既是反馈，也让人确认这东西能点
        b.bind("<Enter>", lambda _e, w=b: w.configure(fg=ACCENT))
        b.bind("<Leave>", lambda _e, w=b: w.configure(fg=MUTED))
        return b

    def open_param_note(self, key):
        """点参数右侧的「！」→ 弹出这个参数的说明。

        再点同一个「！」就收起：小窗口飘在参数旁边，没有明显的关闭路径时
        用户会以为点不掉。换一个参数则直接换成那一个，不会叠出第二个窗口。
        """
        if self.param_win is not None and self._param_key == key:
            self.close_param_note()
            return
        meta = next((f for f in FIELDS if f[0] == key), None)
        if meta is None:
            return
        _k, label, unit, top = meta
        note = W.PARAM_NOTES.get(key, "")
        self.close_param_note()

        win = tk.Toplevel(self.root)
        win.withdraw()                        # 先藏起来，摆好位置再显示，免得闪一下
        win.title("参数说明 · " + label)
        win.configure(bg=BG)
        win.resizable(False, False)
        try:
            win.transient(self.root)          # 跟着主窗口走，不占任务栏
        except Exception:
            pass

        card = tk.Frame(win, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="both", expand=True, padx=10, pady=10)

        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=NOTE_PAD, pady=(12, 0))
        # 标题标记用半角「!」，不用全角「！」。
        # 全角感叹号在 Microsoft YaHei UI 里占 15px 宽，墨迹却只有 2px，
        # 还挤在格子左缘 —— 跟参数名之间凭空多出 20 多像素空隙，看起来像浮在半空。
        # 半角只有 5px 宽、墨迹居中，和参数名挨得紧。
        tk.Label(head, text="!", font=("Microsoft YaHei UI", 11, "bold"),
                 bg=CARD, fg=ACCENT, padx=0, bd=0).pack(side="left")
        tk.Label(head, text=label, font=("Microsoft YaHei UI", 11, "bold"),
                 bg=CARD, fg=TEXT, padx=0, bd=0).pack(side="left", padx=(6, 0))
        tk.Button(head, text="✕", font=("Microsoft YaHei UI", 10), bg=CARD, fg=MUTED,
                  relief="flat", bd=0, padx=4, pady=0, cursor="hand2", takefocus=0,
                  activebackground=CARD, activeforeground=TEXT,
                  command=self.close_param_note).pack(side="right")

        # 单位与取值范围做成两枚小标签：一眼分得清「这是单位」和「这是上限」，
        # 挤在一行文字里读起来像半句话
        chips = tk.Frame(card, bg=CARD)
        chips.pack(fill="x", padx=NOTE_PAD, pady=(8, 0))
        if unit:
            self._note_chip(chips, "单位", unit)
        self._note_chip(chips, "取值范围", "0 ~ %s" % top)

        tk.Frame(card, bg=LINE, height=1).pack(fill="x", padx=NOTE_PAD, pady=(11, 0))

        # 正文分两层：第一段讲「这项干什么」，后面的讲「坑在哪」。
        # 之前整段糊在一起，读者分不清哪句是提醒，最容易踩的那句反倒被略过
        body, tips = W.split_param_note(note)
        tk.Label(card, text="作用", font=("Microsoft YaHei UI", 9, "bold"),
                 bg=CARD, fg=MUTED, padx=0, bd=0).pack(
            anchor="w", padx=NOTE_PAD, pady=(11, 0))
        # 折行由引擎层做（见文件头 NOTE_* 常量的说明）：先把文字切成不可再分的
        # 原子，再按像素宽贪心拼行。Tk 自己的 wraplength 只按空格断行，
        # 中文里为排版留的空格全成了断点，会把「·」甩到单独一行、
        # 把「「本机 → 服务器」」从中间劈开。
        body_lines = W.wrap_note_lines(body or "（暂无说明）",
                                       _note_measure, NOTE_TEXT_W)
        tk.Label(card, text="\n".join(body_lines),
                 font=("Microsoft YaHei UI", 10), bg=CARD, fg=TEXT,
                 anchor="w", justify="left", padx=0, bd=0).pack(
            fill="x", padx=NOTE_PAD, pady=(4, 0))

        if tips:
            tk.Label(card, text="注意", font=("Microsoft YaHei UI", 9, "bold"),
                     bg=CARD, fg=WARN, padx=0, bd=0).pack(
                anchor="w", padx=NOTE_PAD, pady=(13, 0))
            for i, tip in enumerate(tips):
                # 「·」单独做一个控件，不再跟正文拼在同一个字符串里。
                # 拼在一起时 Tk 会把它当一个词，单独甩到一行（截图里的那行光秃秃的「·」）。
                # 拆开后它固定待在左边，右侧正文整体缩进 —— 悬挂缩进，
                # 换行后的第二行仍然和第一行正文对齐。
                row = tk.Frame(card, bg=CARD)
                row.pack(fill="x", padx=NOTE_PAD, pady=(5 if i == 0 else 4, 0))
                row.grid_columnconfigure(0, minsize=NOTE_TIP_INDENT)
                row.grid_columnconfigure(1, weight=1)
                tk.Label(row, text="·", font=("Microsoft YaHei UI", 10),
                         bg=CARD, fg=TEXT, padx=0, bd=0,
                         anchor="w").grid(row=0, column=0, sticky="w")
                tip_lines = W.wrap_note_lines(tip, _note_measure,
                                              NOTE_TEXT_W - NOTE_TIP_INDENT)
                tk.Label(row, text="\n".join(tip_lines),
                         font=("Microsoft YaHei UI", 10), bg=CARD, fg=TEXT,
                         anchor="w", justify="left", padx=0, bd=0).grid(
                    row=0, column=1, sticky="w")

        tk.Button(card, text="知道了", font=("Microsoft YaHei UI", 9), bg=BTN2,
                  fg=TEXT, relief="flat", padx=16, pady=5, cursor="hand2",
                  takefocus=0, bd=0, highlightthickness=1, highlightbackground=LINE,
                  command=self.close_param_note).pack(anchor="e", padx=NOTE_PAD,
                                                      pady=(16, 13))

        self.param_win = win
        self._param_key = key
        win.protocol("WM_DELETE_WINDOW", self.close_param_note)
        win.bind("<Escape>", lambda _e: self.close_param_note())
        self._place_param_note(win, key)
        win.deiconify()

    def _note_chip(self, parent, name, value):
        """说明窗里的一枚小标签：标题（浅底）+ 取值（等宽感）。"""
        chip = tk.Frame(parent, bg=CHIP, highlightbackground=LINE,
                        highlightthickness=1)
        chip.pack(side="left", padx=(0, 6))
        tk.Label(chip, text=name, font=("Microsoft YaHei UI", 9), bg=CHIP,
                 fg=MUTED, padx=6, pady=2).pack(side="left")
        tk.Label(chip, text=value, font=("Microsoft YaHei UI", 9, "bold"),
                 bg=CHIP, fg=TEXT, padx=6, pady=2).pack(side="left")

    def _place_param_note(self, win, key):
        """贴在被点的「！」旁边：右边放不下就翻到左边，最后夹在屏幕内。

        不挂在鼠标坐标上：键盘 / 脚本触发时没有鼠标位置，取按钮自己的位置更稳。

        宽度写死：正文已经按固定宽度折好行了，若让 Tk 按最长的一行自适应，
        换个参数窗口就忽宽忽窄 —— 看着像没做完。
        """
        try:
            win.update_idletasks()
            w, h = NOTE_WIN_W, win.winfo_reqheight()
            btn = self.param_btns.get(key)
            if btn is not None and btn.winfo_ismapped():
                bx, by = btn.winfo_rootx(), btn.winfo_rooty()
                bw = btn.winfo_width()
            else:
                bx = self.root.winfo_rootx() + 60
                by = self.root.winfo_rooty() + 60
                bw = 0
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            x = bx + bw + 6
            if x + w > sw - 8:                # 右边装不下 → 翻到按钮左侧
                x = bx - w - 6
            y = by - 6
            if y + h > sh - 48:               # 48 给任务栏留位置
                y = sh - 48 - h
            win.geometry("%dx%d+%d+%d" % (w, h, max(8, x), max(8, y)))
        except Exception:
            pass

    def close_param_note(self):
        if self.param_win is None:
            self._param_key = None
            return
        try:
            self.param_win.destroy()
        except Exception:
            pass
        self.param_win = None
        self._param_key = None

    def open_preset_notes(self):
        """场景说明：按分类列出每支内置档位的参数与适用症状。

        入口是「场景预设」标题右侧的「！」。17 支档位里有一半光看名字分不清
        ——「4G 弱网」和「弱 Wi-Fi」都是低速+丢包，「黑洞」和「完全断网」都是连不上
        但机制完全不同（挂住 vs 立刻失败）。这里把「模拟的什么现场 + 典型症状」
        和实际参数一起摊开，省得每次都去翻源码。
        """
        if self.notes_win is not None:
            try:                              # 已经开着就提到前台，不重复建
                self.notes_win.deiconify()
                self.notes_win.lift()
                return
            except Exception:
                self.notes_win = None

        win = tk.Toplevel(self.root)
        win.title("场景说明")
        # 高度按屏幕夹一下：小屏上 660 加上标题栏会顶出屏幕底部
        sh = win.winfo_screenheight()
        nh = min(660, max(360, sh - 120))
        win.geometry("580x%d" % nh)
        win.configure(bg=BG)
        try:
            win.transient(self.root)
        except Exception:
            pass
        # 居中到主窗口上（不指定位置时系统会随便放，经常跑到屏幕角落）
        try:
            self.root.update_idletasks()
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw, rh = self.root.winfo_width(), self.root.winfo_height()
            win.geometry("+%d+%d" % (max(0, rx + (rw - 580) // 2),
                                     max(0, ry + (rh - nh) // 2)))
        except Exception:
            pass
        self.notes_win = win

        head = tk.Frame(win, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        head.pack(fill="x", padx=12, pady=12)
        # 不放「返回」按钮：标题栏右上角的 ✕ 就能关这扇窗，
        # 两个入口干的是同一件事，看的人只会犯迷糊
        tk.Label(head, text="场景说明", font=FONT_T, bg=CARD, fg=TEXT).pack(
            side="left")
        tk.Label(head, text="共 %d 支" % len(W.PRESETS), font=FONT, bg=CARD,
                 fg=MUTED).pack(side="right")

        wrap = tk.Frame(win, bg=BG)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        txt = tk.Text(wrap, font=FONT, bg=CARD, fg=TEXT, relief="flat",
                      highlightthickness=1, highlightbackground=LINE, bd=0,
                      wrap="char",          # 中文没有空格，按字符换行才不会溢出
                      padx=14, pady=12, spacing1=1, spacing3=3,
                      takefocus=0, cursor="arrow")
        sb = _scrollbar(wrap, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        txt.tag_configure("cat", font=FONT_B, foreground=ACCENT,
                          spacing1=12, spacing3=3)
        txt.tag_configure("name", font=FONT_B, foreground=TEXT, spacing1=7)
        txt.tag_configure("param", font=FONT, foreground=MUTED,
                          lmargin1=14, lmargin2=14, spacing3=2)
        txt.tag_configure("note", font=FONT, foreground=TEXT,
                          lmargin1=14, lmargin2=14, spacing3=6)

        self.notes_txt = txt
        self._fill_preset_notes()
        win.bind("<Escape>", lambda _e: self.close_preset_notes())
        win.protocol("WM_DELETE_WINDOW", lambda: self.close_preset_notes())

    def _fill_preset_notes(self):
        """按当前选中的场景重画说明正文（切换场景时刷新「当前选中」标记）"""
        txt = getattr(self, "notes_txt", None)
        if txt is None:
            return
        cur = W.G.preset_key
        cats = {}
        for key, (name, cat, _extra) in W.PRESETS.items():
            cats.setdefault(cat, []).append((key, name))
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        for cat in ("常规网络", "弱网环境", "极端环境"):
            items = cats.get(cat) or []
            if not items:
                continue
            txt.insert("end", cat + "\n", "cat")
            for key, name in items:
                try:
                    prof = W.build_preset_profile(key)
                except Exception:
                    prof = {}
                mark = "　　← 当前选中" if key == cur else ""
                txt.insert("end", name + mark + "\n", "name")
                txt.insert("end", detail(prof) + "\n", "param")
                note = W.PRESET_NOTES.get(key, "")
                if note:
                    txt.insert("end", note + "\n", "note")
        txt.configure(state="disabled")
        self._notes_key = cur

    def close_preset_notes(self):
        if self.notes_win is None:
            return
        try:
            self.notes_win.destroy()
        except Exception:
            pass
        self.notes_win = None
        self.notes_txt = None

    def open_combo_notes(self, parent=None):
        """组合说明：组合怎么播 + 每支的段序 + 几个能照抄的例子。

        入口在「新建 / 编辑组合」界面里（标题右侧的「！」）。组合是把几支场景
        按时间排队播，按钮上只写着「通勤」「抢购」这类名字，猜不出它到底模拟了
        什么 —— 真正要动手排组合的时候才需要知道这些，所以入口跟着编辑器走。

        parent 给了就把说明窗挂在它下面（transient）：从编辑器里点开的说明窗
        应当压在编辑器上面，编辑器一关它也跟着消失，不会剩个孤儿窗口飘在主
        界面上。
        """
        if self.combo_notes_win is not None:
            try:                              # 已经开着就提到前台，不重复建
                self.combo_notes_win.deiconify()
                self.combo_notes_win.lift()
                return
            except Exception:
                self.combo_notes_win = None

        win = tk.Toplevel(self.root)
        win.title("弱网组合说明")
        sh = win.winfo_screenheight()
        nh = min(700, max(400, sh - 120))
        win.geometry("600x%d" % nh)
        win.configure(bg=BG)
        try:
            win.transient(parent if parent is not None else self.root)
        except Exception:
            pass
        # 居中：跟着 parent 走（从编辑器里打开就该压在编辑器中间），
        # 没有 parent 就居中到主窗口
        try:
            anchor = parent if parent is not None else self.root
            anchor.update_idletasks()
            rx, ry = anchor.winfo_rootx(), anchor.winfo_rooty()
            rw, rh = anchor.winfo_width(), anchor.winfo_height()
            win.geometry("+%d+%d" % (max(0, rx + (rw - 600) // 2),
                                     max(0, ry + (rh - nh) // 2)))
        except Exception:
            pass
        self.combo_notes_win = win

        head = tk.Frame(win, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        head.pack(fill="x", padx=12, pady=12)
        # 不放「返回」按钮：标题栏右上角的 ✕ 就能关这扇窗，
        # 两个入口干的是同一件事，看的人只会犯迷糊
        tk.Label(head, text="弱网组合说明", font=FONT_T, bg=CARD, fg=TEXT).pack(
            side="left")
        tk.Label(head, text="共 %d 支" % len(W.get_combos()), font=FONT, bg=CARD,
                 fg=MUTED).pack(side="right")

        wrap = tk.Frame(win, bg=BG)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        txt = tk.Text(wrap, font=FONT, bg=CARD, fg=TEXT, relief="flat",
                      highlightthickness=1, highlightbackground=LINE, bd=0,
                      wrap="char", padx=14, pady=12, spacing1=1, spacing3=3,
                      takefocus=0, cursor="arrow")
        sb = _scrollbar(wrap, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        txt.tag_configure("cat", font=FONT_B, foreground=ACCENT,
                          spacing1=12, spacing3=3)
        txt.tag_configure("name", font=FONT_B, foreground=TEXT, spacing1=7)
        txt.tag_configure("param", font=FONT, foreground=MUTED,
                          lmargin1=14, lmargin2=14, spacing3=2)
        txt.tag_configure("note", font=FONT, foreground=TEXT,
                          lmargin1=14, lmargin2=14, spacing3=6)
        txt.tag_configure("tip", font=FONT, foreground=WARN,
                          lmargin1=14, lmargin2=14, spacing3=4)

        # ---- 1. 机制 ----
        txt.insert("end", "组合怎么播\n", "cat")
        intro, examples, tips = W.combo_doc_parts()
        for line in intro.split("\n"):
            txt.insert("end", line + "\n", "note")

        # ---- 2. 我自己的组合 ----
        combos = W.get_combos()
        txt.insert("end", "我保存的组合\n", "cat")
        if not combos:
            txt.insert("end", "还没有组合。点「＋ 新建组合」把几支场景排成一条链。\n",
                       "note")
        else:
            cur = W.G.preset_key
            for c in combos:
                mark = "　　← 当前选中" if W.COMBO_KEY_PREFIX + c["id"] == cur else ""
                txt.insert("end", c["name"] + mark + "\n", "name")
                txt.insert("end", W.combo_summary(c) + "\n", "param")

        # ---- 3. 推荐例子 ----
        txt.insert("end", "推荐例子（照抄就能用）\n", "cat")
        for ex in examples:
            txt.insert("end", ex["name"] + "　" + ex["desc"] + "\n", "name")
            txt.insert("end", "怎么排：" + ex["steps"] + "\n", "param")
            txt.insert("end", ex["effect"] + "\n", "note")

        # ---- 4. 提醒 ----
        txt.insert("end", "注意\n", "cat")
        for tip in tips:
            txt.insert("end", "· " + tip + "\n", "tip")

        txt.configure(state="disabled")
        self.combo_notes_txt = txt
        win.bind("<Escape>", lambda _e: self.close_combo_notes())
        win.protocol("WM_DELETE_WINDOW", self.close_combo_notes)

    def close_combo_notes(self):
        if self.combo_notes_win is None:
            return
        try:
            self.combo_notes_win.destroy()
        except Exception:
            pass
        self.combo_notes_win = None
        self.combo_notes_txt = None

