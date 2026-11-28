"""
WeakNet —— App 弱网测试工具（桌面 GUI 版）

用 tkinter 写的原生窗口界面，不依赖任何第三方库。
GUI 通过 weaknet.py 提供的引擎与 HTTP 控制接口工作：界面线程负责显示，后台线程跑 asyncio 服务（网页控制台已下线，不要找前端代码）。

用法：
    python weaknet_gui.py [--proxy-port 8888] [--ctrl-port 8899]
    python weaknet_gui.py --headless [--ready-file 路径]   # 只跑引擎，界面由宿主进程（Perfara）驱动
打包（--icon 写 EXE 文件图标；窗口图标由本文件内嵌的 base64 提供，见 _apply_window_icon）：
    python -m PyInstaller --onefile --windowed --clean --noconfirm --name WeakNet \\
        --icon assets/weaknet.ico --distpath . --workpath _build_gui \\
        --specpath _build_gui weaknet_gui.py
"""

import argparse
import asyncio
import atexit
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import weakref
import webbrowser
from tkinter import filedialog, font as tkfont, messagebox, ttk

# 以 --windowed 打包时没有控制台，sys.stdout / sys.stderr 为 None。
# 必须给它们一个去处，否则任何 print / traceback 输出都会异常导致闪退。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import weaknet as W
import update_check as UC

# 由 main() 在解析完参数后置位：无窗口模式下出错不能弹 messagebox（没人点，宿主会一直等）
_HEADLESS = False

# _set() 的「缓存里没有这个选项」哨兵：不能用 None，None 是合法的选项值
_UNSET = object()

# 窗口/任务栏图标：与 assets/weaknet.ico、APK 的 launcher 图标是同一套图形
# （图形与配色由 assets/make_icon.py 生成，改那边再重跑即可）。
# EXE 的**文件**图标由 PyInstaller --icon 写进资源，但 Tk 会给窗口挂自己的羽毛图标，
# 所以这里再显式设一次。图片以 base64 内嵌：单文件打包不必额外带资源，也不会因为
# 解压目录变化而找不到文件。
_ICON_PNG_B64 = (
    "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"
    "AABJRU5ErkJggg=="
)


def _apply_window_icon(root):
    """给主窗口装图标，并设为之后所有二级窗口的默认图标。"""
    try:
        img = tk.PhotoImage(data=_ICON_PNG_B64)
        root.iconphoto(True, img)   # default=True：之后 new 出来的 Toplevel 都跟着用
        root._wn_icon = img         # 必须留引用，被 GC 回收后图标会变空白
    except Exception:
        pass                        # 图标只是门面，任何异常都不能挡住启动

# ---------------------------------------------------------------- 主题
#
# 宿主（Perfara 采集端）是深色界面。随包拉起的 WeakNet 必须跟着深色，
# 否则二级窗口一眼就不像同一个产品；独立运行（发布包/源码直跑）保持原浅色。
# 判定优先级：环境变量 WEAKNET_THEME > --theme dark / --dark > 目录位置
# （<...>/resources/weaknet/ 是宿主打包后的固定布局，独立发布包不会长这样）。
# ---------------------------------------------------------------- 主题层
# 配色 / 字体 / 标题栏 / ttk 主题移到 gui_theme.py：面板模块要独立成文件，
# 不能被主模块反向 import（会循环）。这里显式导入，名字保持不变。
from gui_theme import (BG, CARD, LINE, TEXT, MUTED, ACCENT,
                       OK, WARN, ERR, BTN2, BTN2_HOVER, FIELD, SELECT,
                       FONT, FONT_B, FONT_T, _darken_titlebar,
                       _scrollbar, _apply_theme)
from gui_notes import NotesMixin
from gui_scope import ScopeMixin
from gui_share import ShareMixin
from gui_usb import UsbMixin
# ---------------------------------------------------------------- 纯文本层已拆出
# 说明小窗排版常量 / 参数摘要 / 格式化函数移到 gui_text.py：它们不依赖 App 状态，
# 单独成模块后可以直接跑单测（tests/test_gui_text.py），也把主文件从 3,616 行
# 降下来。这里用显式导入而不是 import *，避免把 tk / weaknet 悄悄带进命名空间。
from gui_text import (SHORT, FIELDS, fmt_rate, fmt_bytes, summarize,  # noqa: F401
                      detail, _cap_name_var)
# 视图模型层：refresh 里「该显示什么」是纯函数，这里只负责「怎么改控件」。
# 纯逻辑单独成模块后可以脱离 Tk 单测（tests/test_gui_state.py）。
import gui_state as S
# 刷新帧统计：口径与 Android FrameStats 一致，纯逻辑可单测。
import gui_perf as P
# 测试报告：把状态快照渲染成开发可读的 Markdown，纯逻辑可单测。
import report_gen as R


class App(NotesMixin, ScopeMixin, ShareMixin, UsbMixin):
    def __init__(self, root, loop, ctrl_port):
        self.root = root
        self.loop = loop
        self.ctrl_port = ctrl_port
        self.preset_btns = {}
        self.vars = {}
        self._last = (time.time(), W.G.bytes_down, W.G.bytes_up)
        self._rate = (0.0, 0.0)
        # 控件上次下发的选项缓存（weakref：按钮重建后旧条目自动消失）
        self._wcache = weakref.WeakKeyDictionary()
        self._frames = None                   # 刷新耗时统计，首次 refresh 时创建
        self._ev_seq = -1
        self._closing = False
        self.share_win = None                 # 环境共享二级窗口，None = 当前未打开
        self.share_after = None               # 窗口内的定时刷新（关闭时要取消）
        self.notes_win = None                 # 场景说明二级窗口
        self.notes_txt = None                 # 场景说明的正文控件（切场景时重画）
        self._notes_key = None                # 上次画的时候选中的场景
        self.param_win = None                 # 单个参数的「！」说明小窗
        self._param_key = None                # 小窗当前讲的是哪个参数
        self.param_btns = {}                  # 参数 key -> 「！」按钮
        self.combo_win = None                 # 弱网组合编辑窗口，None = 当前未打开
        self._combo_rows = []                 # 编辑器里的每一行（场景 + 分/秒）
        self._combo_editing = None            # 正在编辑的组合 id（新建为 None）
        self.combo_notes_win = None           # 组合说明二级窗口（组合区的「！」）
        self.combo_notes_txt = None           # 组合说明的正文控件
        # USB 手机弱网：adb 命令最长 20 秒，一律丢后台线程，
        # 结果经这个队列回到主线程（tkinter 不是线程安全的，绝不能在子线程里直接碰控件）
        self.usb_q = queue.Queue()
        # GitHub 新版本检查同理：查完只往队列里丢结果，控件由 refresh() 在主线程改
        self.update_q = queue.Queue()
        self._update_state = None          # 启动那一次 check() 的结果，None = 还没查/查失败
        self._update_done = False          # 每次启动只查一次，运行中不再复查

        root.title("WeakNet %s —— App 弱网测试工具" % W.VERSION)
        _apply_window_icon(root)
        root.configure(bg=BG)
        _apply_theme(root)
        _darken_titlebar(root)
        # 二级窗口（接管范围 / 场景说明 / 编辑组合…）各自有系统标题栏，
        # 一律在首次显示时补一次深色，不用逐个窗口调用。
        root.bind_all("<Map>", lambda e: _darken_titlebar(e.widget.winfo_toplevel()), add="+")
        # 按屏幕自适应窗口高度并居中，避免小屏幕上底部内容被任务栏/屏幕边缘裁掉
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        wh = min(860, sh - 90)
        root.geometry(f"880x{wh}+{max(0, (sw - 880) // 2)}+{max(0, (sh - 60 - wh) // 2)}")
        # 最小高度不再卡在 780：左栏现在能滚动，窗口做矮一点也能把参数全部翻到。
        # 但也不能压太低 —— 右栏是「USB 卡（约 220px，不可压缩）+ 日志（吃剩余）」，
        # 窗口矮到 620 时 body 只剩 281px，日志会被挤成 1px 完全看不见。
        # 700 是让日志至少还剩 4~5 行的下限。
        root.minsize(820, min(700, sh - 120))

        self._build_header()
        # 接管范围通栏放在最顶部：它是「这次测谁」的开关，范围不对下面所有参数都白调，
        # 塞在下面的分区里要滚动才看得到，容易测完才发现接管的不是目标程序。
        self._build_scope()
        # 下方分区：左边是要动手的设置（场景预设 / 自定义参数），右边是只看不动的日志。
        # 右栏固定宽度：日志按行显示，太宽反而每行都剩一大截空白。
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True)
        # 左栏放进可滚动容器。预设 + 自定义参数两张卡叠起来约 780px 高，而窗口
        # 在小屏上留给 body 的只有 400 出头 —— 原来这里是裸 Frame，没有滚动条，
        # 底部几个参数（断网间隔 / 断网时长 / 保存栏）被窗口下边缘直接裁掉，
        # 滚轮和拖拽都翻不出来，用户看到的就是「参数显示不全、没法下滑」。
        left_wrap = tk.Frame(body, bg=BG)
        left_wrap.pack(side="left", fill="both", expand=True)
        self._left_sb_busy = False
        self.left_canvas = tk.Canvas(left_wrap, bg=BG, bd=0, highlightthickness=0,
                                     takefocus=0, yscrollincrement=1)
        self.left_sb = _scrollbar(left_wrap, orient="vertical", width=12,
                                    command=self.left_canvas.yview)
        self.left_canvas.configure(yscrollcommand=self._left_scroll_set)
        self.left_canvas.pack(side="left", fill="both", expand=True)
        self.left_sb.pack(side="right", fill="y")
        self.col_left = tk.Frame(self.left_canvas, bg=BG)
        self.left_win = self.left_canvas.create_window((0, 0), window=self.col_left,
                                                       anchor="nw")
        self.col_left.bind("<Configure>", self._left_inner_resize)
        self.left_canvas.bind("<Configure>", self._left_canvas_resize)
        # 滚轮：用 bind_all 是因为左栏里的控件是动态生成的（预设按钮、自定义场景
        # 每次刷新都重画），逐个绑定会漏。bind_all 在控件自身的绑定之后才触发，
        # 处理前先看事件源是不是左栏的后代，右栏日志和范围列表的滚动不受影响。
        self.left_canvas.bind_all("<MouseWheel>", self._left_wheel, add="+")
        self.col_right = tk.Frame(body, bg=BG, width=340)
        self.col_right.pack(side="left", fill="y")
        self.col_right.pack_propagate(False)      # 锁住宽度，窗口变宽时只让左栏长
        self._build_presets()
        self._build_custom()
        self._build_usb()        # 右栏：手机是一路独立动作（刷新→连→断），与左栏的参数区分开
        self._build_log()        # 右栏：日志吃掉剩下的高度

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh()
        # 开着界面就先把设备列出来：插上手机进来看一眼就知道有没有被认到。
        # 用 quiet —— 没装 adb 是很常见的情况，启动时弹窗只会打扰人
        root.after(600, lambda: self.usb_refresh(quiet=True))
        # 版本号更新提示：本次启动只查这一次，运行中不再复查（不轮询、点标签也不重查）。
        # 等窗口稳定后再查，别跟启动阶段的端口/USB 初始化抢时间；
        # 查不到（断网、GitHub 限流）就安安静静保持灰色，不弹任何窗。
        root.after(1500, self._queue_update_check)

    # ---------------- 界面搭建 ----------------

    def _card(self, parent, title, expand=False):
        return self._card_x(parent, title, expand=expand)[0]

    def _card_x(self, parent, title, expand=False, action=None):
        """卡片骨架。返回 (内容容器, 标题行右侧的动作按钮或 None)。

        action=(文案, 回调)：有些卡片标题行需要一个附属入口（比如场景预设的「！」），
        单独拉一行按钮会白占高度，放在标题右侧最省地方。
        """
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="both" if expand else "x", padx=14, pady=(0, 10))
        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x", pady=(0, 4))
        tk.Label(head, text=title, font=FONT_B, bg=BG, fg=TEXT).pack(side="left")
        btn = None
        if action is not None:
            text, cmd = action
            btn = tk.Button(head, text=text, font=FONT_B, bg=BTN2, fg=ACCENT,
                            relief="flat", padx=8, pady=0, cursor="hand2",
                            activebackground=BTN2_HOVER, activeforeground=ACCENT,
                            bd=0, highlightthickness=1, highlightbackground=LINE,
                            command=cmd)
            btn.pack(side="right")
        card = tk.Frame(outer, bg=CARD, highlightbackground=LINE,
                        highlightthickness=1, bd=0)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=12, pady=10)
        return inner, btn

    # ---------------- 左栏滚动 ----------------
    #
    # 左栏是 Canvas + 内嵌 Frame 的可滚动区。三个必须处理的点：
    #   1. 内容尺寸变了要重算 scrollregion，否则折叠/展开卡片后范围还是旧的；
    #   2. 窗口变宽时内层要跟着变宽，否则卡片右侧留一条空白；
    #   3. 内容装得下就把滚动条收起来，装不下才出现（常驻滚动条在小屏上很扎眼）。

    def _left_scroll_set(self, first, last):
        """滚动条位置 + 按需显隐。显隐本身会触发一次 Configure，
        所以用一个重入标记挡住，避免自己调自己。"""
        try:
            self.left_sb.set(first, last)
        except Exception:
            return
        if self._left_sb_busy:
            return
        need = not (float(first) <= 0.001 and float(last) >= 0.999)
        shown = bool(self.left_sb.winfo_manager())
        if need == shown:
            return
        self._left_sb_busy = True
        try:
            if need:
                self.left_sb.pack(side="right", fill="y")
            else:
                # 只收起滚动条，不主动 yview_moveto(0)：内容缩到装得下时
                # Tk 本来就会把视图夹回顶部，这里再拉一次反而危险 ——
                # 内容高度短暂波动（按钮重画）会让视图被莫名甩回开头。
                self.left_sb.pack_forget()
        except Exception:
            pass
        finally:
            self._left_sb_busy = False

    def _left_inner_resize(self, _event=None):
        """内容尺寸变化 → 更新可滚动范围（折叠卡片、增删自定义场景都会触发）"""
        try:
            self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all"))
        except Exception:
            pass

    def _left_canvas_resize(self, event):
        """可视区变宽 → 内层同步变宽，卡片横向撑满"""
        try:
            self.left_canvas.itemconfigure(self.left_win, width=event.width)
        except Exception:
            pass

    def _left_hit(self, w, allow_canvas=True):
        """w 是否属于左栏滚动区。

        按「控件是不是左栏的后代」判断，而不是比对屏幕坐标：
        坐标法在弹窗盖住主窗口时会误判，而且测试里没法模拟指针位置。
        """
        try:
            while w is not None:
                if w is self.col_left:
                    return True
                if allow_canvas and w is self.left_canvas:
                    return True
                w = w.master
        except Exception:
            return False
        return False

    def _left_wheel(self, event):
        """滚轮翻左栏。

        只有指针真的落在左栏才处理：右栏的日志（Text）和接管范围列表
        （Listbox）都有各自的滚动，被这里抢走就没法滚了。
        """
        if not self._left_hit(getattr(event, "widget", None)):
            return
        try:
            if not self.left_canvas.winfo_ismapped():
                return
        except Exception:
            return
        delta = getattr(event, "delta", 0) or 0
        # Windows 一格 120；触控板 / 高精度滚轮会给更小的值，按比例折算成像素
        # （canvas 的 yscrollincrement 设成 1，"units" 就等于像素）
        step = int(-delta / 120.0 * 60)
        if not step:
            return
        try:
            self.left_canvas.yview_scroll(step, "units")
        except Exception:
            pass

    def _left_wheel_break(self, event):
        """给数字框用：滚轮落在 Spinbox 上时不要改数值，改成翻页。

        ttk.Spinbox 自带「滚轮 ±1」的类绑定，翻页时手一滑就把参数改了，
        而且改完没提示 —— 这类静默改参数比翻不动页更难受。
        """
        self._left_wheel(event)
        return "break"

    def _in_left_col(self, w):
        """w 是否在左栏内容里（不含 canvas 本身）—— 只有它们才需要滚进可视区"""
        return self._left_hit(w, allow_canvas=False)

    def _left_ensure_visible(self, w):
        """把左栏里的某个控件滚进可视区。

        展开「自定义参数」时内容是在折叠条下方长出来的，如果正好贴着窗口
        下边缘，点完看起来毫无反应 —— 所以展开后把它整块带进视野。
        """
        if not self._in_left_col(w):
            return
        try:
            self.left_canvas.update_idletasks()
            top = w.winfo_rooty() - self.col_left.winfo_rooty()
            bottom = top + w.winfo_height()
            view_h = self.left_canvas.winfo_height()
            total = self.col_left.winfo_height()
            if total <= view_h or view_h <= 1:
                return
            first, last = self.left_canvas.yview()
            cur_top, cur_bottom = first * total, last * total
            if top >= cur_top and bottom <= cur_bottom:
                return                              # 已经整块可见
            if bottom - top > view_h:
                # 比可视区还高（展开后的自定义参数就是这种）：对齐顶部，
                # 让用户从第一行参数开始看，而不是一展开就跳到最底下的保存栏
                target = top
            elif bottom > cur_bottom:
                target = bottom - view_h
            else:
                target = top
            target = max(0, min(target, total - view_h))
            self.left_canvas.yview_moveto(target / float(total))
        except Exception:
            pass

    def _card_fold(self, parent, title, collapsed=True, on_toggle=None):
        """可折叠卡片：标题行放在卡片里，收起时是一张只有标题的卡片（标准折叠条样式）。
        返回内容容器（与 _card 一致）。

        on_toggle：展开 / 收起时先回调一次。给「参数说明」小窗用 —— 卡片一收起，
        里面那些「！」按钮就跟着消失，飘在外面的说明窗得一起关掉。

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
            if on_toggle is not None:
                try:
                    on_toggle()
                except Exception:
                    pass
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
                # 展开后的内容是在折叠条下方长出来的，贴着窗口下边缘时点完
                # 看不出任何变化 —— 等布局算完再把它整块滚进视野
                self.root.after(20, lambda: self._left_ensure_visible(card))

        for w in [head] + list(head.winfo_children()):
            w.bind("<Button-1>", toggle)
            try:
                w.configure(cursor="hand2")
            except Exception:
                pass
        self._last_fold_toggle = toggle
        # 记下内容容器：外部要判断这张卡片是收起还是展开（见 goto_new_scene）
        self._last_fold_inner = inner

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
        # 版本号：默认灰色；线上有更新时整串变红并附上新版本号，点一下打开下载页
        self.lab_version = tk.Label(head, text="v%s" % W.VERSION, font=FONT,
                                    bg=CARD, fg=MUTED, cursor="hand2")
        self.lab_version.pack(side="left", padx=(8, 0))
        self.lab_version.bind("<Button-1>", lambda _e: self._on_version_click())
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
        self.btn_lan = tk.Button(btns, text="开启局域网访问", font=FONT, bg=BTN2,
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

    # ---------------- GitHub 新版本提示 ----------------

    def _queue_update_check(self):
        """本次启动唯一的一次后台检查；不阻塞界面，重复调用直接忽略。

        结果只往队列里丢，由 refresh() → _update_pump() 在主线程落到标签上。
        没有定时器、没有点击重查：想看新版本就重启程序。
        """
        if self._closing or self._update_done:
            return
        self._update_done = True

        def worker():
            state = UC.check(W.VERSION)
            try:
                self.update_q.put(state)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _update_pump(self):
        """主线程：只负责把启动那一次检查的结果落到版本号标签上。"""
        try:
            while True:
                state = self.update_q.get_nowait()
                if state is not None:      # None = 断网/异常，保持默认灰色，不打扰用户
                    self._update_state = state
                    self._apply_update_state()
        except queue.Empty:
            pass

    def _apply_update_state(self):
        if not hasattr(self, "lab_version"):
            return
        text, fg, bold = UC.label_state(W.VERSION, self._update_state, MUTED, ERR)
        try:
            self.lab_version.configure(text=text, fg=fg, font=FONT_B if bold else FONT)
        except Exception:
            pass

    def _on_version_click(self):
        """有新版本 → 打开该 Release 的下载页；没有 → 打开最新版发布页。"""
        state = self._update_state or {}
        url = state.get("page") if state.get("has_new") else UC.PAGE_LATEST
        try:
            if not webbrowser.open(url):
                raise RuntimeError("no browser")
        except Exception:
            # EXE 无控制台，浏览器起不来时至少让用户看到链接
            messagebox.showinfo("WeakNet 新版本", "请用浏览器打开：\n%s" % url)
        # 点标签只负责跳转，不重新联网检查（运行中一律不复查）

    def _build_stats(self):
        pass   # 实时状态已并入顶栏（见 _build_header），保留方法避免旧调用报错

    def _build_presets(self):
        # 挂在左栏：接管范围已升到顶部通栏，预设留在左栏与自定义参数同侧，
        # 右栏整列留给日志，两栏高度才不至于一边空一大块
        # 标题右侧的「！」：17 支档位光看名字分不清差别（4G 弱网 vs 弱 Wi-Fi 差在哪），
        # 点开是每支的场景说明 + 参数摘要，不占卡片高度
        box, self.preset_help_btn = self._card_x(
            self.col_left, "场景预设（弱网开启后点一下即生效）",
            action=("！", self.open_preset_notes))
        self.preset_help_btn.configure(text="！", width=2,
                                       font=("Microsoft YaHei UI", 10, "bold"))
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
                b = tk.Button(grid, text=SHORT.get(key, name), font=FONT, bg=BTN2,
                              fg=TEXT, relief="flat", padx=9, pady=3, cursor="hand2",
                              command=lambda k=key: self.apply_preset(k))
                # 4 列：左栏比整窗窄，按 5 列排会顶到卡片外
                b.grid(row=i // 4, column=i % 4, padx=(0, 5), pady=2, sticky="w")
                self.preset_btns[key] = b
            row += 1

        # 「我的自定义场景」与「我的弱网组合」合成一张卡片，用顶部标签切换
        self._build_mine_combo(box)

    def _build_custom(self):
        # 预设场景已覆盖绝大多数用例，自定义参数默认收起，需要细调时点标题展开
        # on_toggle：卡片收起时把「参数说明」小窗一起关掉 —— 说明的对象都看不见了，
        # 还留一个窗口飘在旁边没有意义
        box = self._card_fold(self.col_left,
                              "自定义参数（默认收起 · 点「应用」生效 · 0 表示不限制/关闭）",
                              collapsed=True, on_toggle=self.close_param_note)
        # 记下卡片本体，便于自检与外部展开/收起
        self.custom_card = self.root.nametowidget(box.winfo_parent())
        self.custom_toggle = self._last_fold_toggle
        # 内容容器：用来判断这张卡片当前是展开还是收起（见 goto_new_scene）
        self.custom_fold_inner = self._last_fold_inner

        grid = tk.Frame(box, bg=CARD)
        grid.pack(fill="x")
        # 两列：卡片进左栏后宽度只有一半，三列会顶出卡片外。
        # 名字列收窄到 56px（最长的「断网间隔」只占 4 个字）、列间距从 18 收到 10：
        # 原来那套在窗口缩到最小宽度 820 时两列合计 444px 而可用只有 414px，
        # 右侧单位（kbps / 秒）被裁掉；现在每行还要塞一个「！」，不收窄就必然溢出。
        #
        # 「！」要紧跟在参数名后面（挂在单位后面读起来像是数字框的附属品），
        # 但各行的数字框又必须对齐 —— 两个要求冲突时，把空白留在「！」右边：
        # 名字按实际宽度排，按钮紧跟其后，右侧补足到统一列宽。
        # 列宽与每行的空白都用字体实测宽度算，不写死：改字体 / 改文案时不会错位。
        f_name = tkfont.Font(font=FONT)
        btn_w = f_name.measure("！") + 2      # 左右各 1px padding
        name_col = max(f_name.measure(lb) + 2 for _k, lb, _u, _t in FIELDS) + 1 + btn_w + 2
        for i, (key, label, unit, top) in enumerate(FIELDS):
            r, c = i // 2, i % 2
            cell = tk.Frame(grid, bg=CARD)
            cell.grid(row=r, column=c, sticky="w", padx=(0, 10), pady=1)
            tk.Label(cell, text=label, font=FONT, bg=CARD, fg=TEXT,
                     anchor="w").pack(side="left")
            gap = name_col - (f_name.measure(label) + 2) - 1 - btn_w
            self.param_btns[key] = self._param_help_btn(cell, key, gap)
            v = tk.StringVar(value="0")
            # 宽度 7：够放最大的 1000000（kbps 上限），再加「！」后两列才装得下
            sp = ttk.Spinbox(cell, from_=0, to=top, width=7, textvariable=v,
                             font=FONT, format="%.1f")
            sp.pack(side="left")
            # 滚轮落在数字框上时翻页而不是改数值：ttk.Spinbox 默认滚轮 ±1，
            # 翻页时手一滑就把参数静默改了，比翻不动更坑
            sp.bind("<MouseWheel>", self._left_wheel_break)
            tk.Label(cell, text=unit, font=FONT, bg=CARD, fg=MUTED).pack(side="left", padx=(4, 0))
            self.vars[key] = v

        bar = tk.Frame(box, bg=CARD)
        bar.pack(fill="x", pady=(10, 0))
        tk.Button(bar, text="应用自定义参数", font=FONT_B, bg=ACCENT, fg="white",
                  relief="flat", padx=14, pady=5, cursor="hand2",
                  command=self.apply_custom).pack(side="left")
        tk.Button(bar, text="读取当前场景", font=FONT, bg=BTN2, fg=TEXT,
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
        self.mine_name = _cap_name_var(tk.StringVar())
        ent = tk.Entry(row, textvariable=self.mine_name, font=FONT, width=20,
                       relief="flat", highlightthickness=1, highlightbackground=LINE,
                       highlightcolor=ACCENT, bg=FIELD, fg=TEXT)
        ent.pack(side="left", padx=(6, 8))
        # 记下输入框：「➕ 新建场景」要把光标送过来
        self.mine_name_ent = ent
        ent.bind("<Return>", lambda _e: self.save_mine())
        tk.Button(row, text="保存当前参数", font=FONT, bg=BTN2, fg=TEXT,
                  relief="flat", padx=12, pady=4, cursor="hand2",
                  command=self.save_mine).pack(side="left")
        tk.Label(row, text="最多 %d 字" % W.NAME_MAX, font=FONT, bg=CARD,
                 fg=MUTED).pack(side="left", padx=(8, 0))
        tk.Label(box, text="保存后与内置场景并排显示、点一下即切换；选中它后"
                           "「删除当前场景」按钮才会出现（存在该程序目录的 "
                           "weaknet_presets.json，重装保留）。",
                 font=FONT, bg=CARD, fg=MUTED, anchor="w", justify="left",
                 wraplength=430).pack(fill="x", pady=(6, 0))

    def _build_mine_combo(self, box):
        """我的自定义场景 + 我的弱网组合：合并成一张卡片，用标签切换。

        这两块本来就是一类东西（都是我自己攒下来的、点一下即切换的环境），
        原来却各占一块纵向空间，加起来比内置档位那一整片还长；
        合成一张卡片后用「场景 / 组合」两个标签切换，一次只显示当前那一页。
        """
        tk.Frame(box, bg=LINE, height=1).pack(fill="x", pady=(10, 8))
        head = tk.Frame(box, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text="⭐ 我的场景与组合", font=FONT_B, bg=CARD,
                 fg=TEXT).pack(side="left")

        # 环境共享：自定义场景与弱网组合在同一个窗口里共享，入口跟着这张卡片走
        self.btn_share = tk.Button(head, text="🔗 环境共享", font=FONT, bg=BTN2,
                                   fg=TEXT, relief="flat", padx=10, pady=3,
                                   cursor="hand2", command=self.open_share)
        self.btn_share.pack(side="right")

        # 标签（场景 / 组合）单独占一行：紧贴标题会被看成标题的一部分，
        # 看不出这里有两页可切。做成两段按钮，选中的那段落成主色。
        tabs = tk.Frame(box, bg=CARD)
        tabs.pack(fill="x", pady=(7, 0))
        self.tab_btns = {}
        for key, label in (("scene", "场景"), ("combo", "组合")):
            b = tk.Button(tabs, text=label, font=FONT_B, bg=BTN2, fg=MUTED,
                          activebackground=BTN2, activeforeground=MUTED,
                          relief="flat", padx=16, pady=3, cursor="hand2",
                          command=lambda k=key: self.switch_tab(k, manual=True))
            b.pack(side="left", padx=(0, 4))
            self.tab_btns[key] = b

        # 两个页面容器：同一时刻只显示其中一个
        self.tab_pages = {}
        for key in ("scene", "combo"):
            self.tab_pages[key] = tk.Frame(box, bg=CARD)

        self._build_mine(self.tab_pages["scene"])
        self._build_combo(self.tab_pages["combo"])
        # 初始停在当前选中的那一页：选中的正是组合，就别让用户再点一次标签
        cur = W.G.preset_key or ""
        self.switch_tab("combo" if cur.startswith(W.COMBO_KEY_PREFIX) else "scene")

    def _paint_tabs(self, key):
        """标签配色：连鼠标悬停态一起刷。

        只改 bg/fg 的话，鼠标还停在刚点过的按钮上时看到的是 hover 底色，
        会误以为「选中特效没刷新」——tkinter 的 activebackground 优先级更高。
        """
        for k, b in getattr(self, "tab_btns", {}).items():
            on = (k == key)
            bg = ACCENT if on else BTN2
            fg = "white" if on else MUTED
            # 走 _set 而不是直写：标签页没变时这一轮就不必再动控件。
            # activebackground 一起下发，鼠标悬停色才跟当前选中态一致。
            self._set(b, bg=bg, fg=fg, activebackground=bg, activeforeground=fg)

    def switch_tab(self, key, manual=False):
        """切换「场景 / 组合」标签页。

        只换显示，不重建内容：两个页面的按钮都是活的，切换回来还在原位。
        manual=True 表示用户亲手点的标签：把当前选中项记为已处理，
        免得下一次自动同步又把这一页翻回去。
        """
        pages = getattr(self, "tab_pages", None)
        if not pages:
            return
        for k, page in pages.items():
            if k == key:
                if not page.winfo_manager():
                    page.pack(fill="x")
            elif page.winfo_manager():
                page.pack_forget()
        self._paint_tabs(key)
        self.cur_tab = key
        if manual:
            self._last_tab_key = W.G.preset_key or ""

    def _build_mine(self, page):
        """自定义场景：与内置预设同处一张卡片、用同样的按钮样式。

        不再单独拉一个带「删除选中」的列表——保存后就跟内置档位长得一样，
        点一下即切换；删除入口跟着「当前选中的场景」出现（见 _sync_mine_del）。
        """
        # 动作行：「➕ 新建场景」直接跳到下方的自定义参数区起名保存
        # （Windows 端保存场景要填名字，名字框在「自定义参数」卡片里，
        #  所以这里不是弹窗，而是把那块展开并把光标送过去）
        row = tk.Frame(page, bg=CARD)
        row.pack(fill="x")
        tk.Button(row, text="➕ 新建场景", font=FONT, bg=BTN2, fg=TEXT,
                  relief="flat", padx=10, pady=3, cursor="hand2",
                  command=self.goto_new_scene).pack(side="left")

        self.mine_grid = tk.Frame(page, bg=CARD)
        self.mine_grid.pack(fill="x", pady=(6, 0))
        self.mine_btns = {}

        bar = tk.Frame(page, bg=CARD)
        bar.pack(fill="x", pady=(8, 0))
        self.mine_del_bar = bar
        self.btn_mine_del = tk.Button(bar, text="🗑 删除当前场景", font=FONT,
                                      bg=BTN2, fg=TEXT, relief="flat",
                                      padx=12, pady=4, cursor="hand2",
                                      command=self.del_mine)
        self.btn_mine_del.pack(side="left")
        self.lab_mine = tk.Label(page, text="", font=FONT, bg=CARD, fg=MUTED,
                                 anchor="w", justify="left", wraplength=430)
        self.lab_mine.pack(fill="x", pady=(6, 0))
        self.reload_mine()

    def goto_new_scene(self):
        """「➕ 新建场景」：展开自定义参数卡片，把光标送到名称输入框。

        保存一条自定义场景必须先给参数起个名字，而名字框在「自定义参数」卡片里
        （那张卡片默认是收起的）。这里一步到位：展开 → 滚进视野 → 聚焦。
        """
        try:
            inner = getattr(self, "custom_fold_inner", None)
            # 卡片是收起状态时才展开：toggle 是「切换」语义，已经展开着再点一下
            # 反而会把它收起来
            if inner is not None and not inner.winfo_manager():
                self.custom_toggle()
            self.root.after(30, lambda: self._left_ensure_visible(self.custom_card))
            self.root.after(60, lambda: self._focus_mine_name())
        except Exception:
            pass

    def _focus_mine_name(self):
        """把焦点放到自定义场景的名称输入框（窗口没显示时忽略）"""
        try:
            ent = getattr(self, "mine_name_ent", None)
            if ent is None:
                return
            self._left_ensure_visible(ent)
            ent.focus_set()
        except Exception:
            pass

    # ---------------- 场景说明（二级窗口） ----------------




    # ---------------- 参数说明（每个参数自己的「！」） ----------------






    # ---------------- 自定义栏 ----------------

    def reload_mine(self):
        """重画自定义场景按钮。其它端经 /api 增删的场景，refresh 会自动同步"""
        self.mine_items = W.get_user_presets()
        self.mine_sig = ",".join(it["id"] for it in self.mine_items)
        for b in self.mine_btns.values():
            b.destroy()
        self.mine_btns = {}
        self._sel_mine = None      # 按钮是新建的，下次 refresh 必须重新上色
        for i, it in enumerate(self.mine_items):
            key = "u:" + it["id"]
            b = tk.Button(self.mine_grid, text=it["name"], font=FONT, bg=BTN2,
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
        v = S.mine_hint(len(getattr(self, "mine_items", [])),
                        it["name"] if it else None)
        if v["del_text"] is None:
            # 已经收起时不必再 pack_forget：那也是几何管理器的一次调用
            if self.btn_mine_del.winfo_manager():
                self.btn_mine_del.pack_forget()
        else:
            if not self.btn_mine_del.winfo_manager():
                self.btn_mine_del.pack(side="left")
            self._set(self.btn_mine_del, text=v["del_text"])
        self._set(self.lab_mine, text=v["lab_text"])

    def save_mine(self):
        name = W.limit_item_name(self.mine_name.get())
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

    # ---------------- 弱网组合 ----------------



    def _scene_options(self):
        """编辑器下拉的候选：内置预设 + 自定义场景。返回 (显示名列表, key 列表)

        已保存的「弱网组合」不在这里出现 —— 组合是按时长线性播的一组场景，
        嵌一层它不会被展开，运行时只会把那一段跳过，用户排好了却发现少一段最难查。
        下面这层过滤是界面侧的显式保证，引擎侧 norm_combo_steps 还会再兜一道。
        """
        labels, keys = [], []
        for key, (name, _cat, _extra) in W.PRESETS.items():
            if key.startswith(W.COMBO_KEY_PREFIX):
                continue
            labels.append(SHORT.get(key, name))
            keys.append(key)
        for it in W.get_user_presets():
            k = "u:" + it["id"]
            if k.startswith(W.COMBO_KEY_PREFIX):
                continue
            labels.append(it["name"])
            keys.append(k)
        return labels, keys

    def _build_combo(self, page):
        """弱网组合区：把几支场景排队自动播放，按钮样式与上面两组保持一致"""
        # 动作行：只有「＋ 新建组合」。
        # 组合说明的「！」挪进了编辑器（标题右侧）——说明是给「要动手排组合的人」
        # 看的，摆在组合列表顶上反而像跟哪支组合都有关似的
        head = tk.Frame(page, bg=CARD)
        head.pack(fill="x")
        tk.Button(head, text="＋ 新建组合", font=FONT, bg=BTN2, fg=ACCENT,
                  relief="flat", padx=10, pady=3, cursor="hand2",
                  command=lambda: self.open_combo_editor()).pack(side="left")
        self.combo_grid = tk.Frame(page, bg=CARD)
        self.combo_grid.pack(fill="x", pady=(6, 0))
        self.combo_btns = {}
        self.combo_edit_bar = tk.Frame(page, bg=CARD)
        self.combo_edit_bar.pack(fill="x", pady=(8, 0))
        self.btn_combo_edit = tk.Button(self.combo_edit_bar, text="✏️ 编辑", font=FONT,
                                        bg=BTN2, fg=TEXT, relief="flat", padx=12,
                                        pady=3, cursor="hand2", command=self.edit_combo)
        self.btn_combo_del = tk.Button(self.combo_edit_bar, text="🗑 删除", font=FONT,
                                       bg=BTN2, fg=TEXT, relief="flat", padx=12,
                                       pady=3, cursor="hand2", command=self.del_combo)
        self.lab_combo = tk.Label(page, text="", font=FONT, bg=CARD, fg=MUTED,
                                  anchor="w", justify="left", wraplength=430)
        self.lab_combo.pack(fill="x", pady=(6, 0))
        self.reload_combo()

    def reload_combo(self):
        """重画组合按钮（自己新建的、别处删掉的都要跟上）"""
        self.combo_items = W.get_combos()
        self.combo_sig = ",".join(c["id"] for c in self.combo_items)
        for b in self.combo_btns.values():
            b.destroy()
        self.combo_btns = {}
        self._sel_combo = None     # 同上：新建按钮的配色要重刷一遍
        for i, c in enumerate(self.combo_items):
            key = W.COMBO_KEY_PREFIX + c["id"]
            # 名字统一左对齐、不掺彩色 emoji：和 Android 组合列表保持同一观感
            b = tk.Button(self.combo_grid, text=c["name"], font=FONT, bg=BTN2,
                          fg=TEXT, relief="flat", padx=9, pady=3, anchor="w",
                          cursor="hand2", command=lambda k=key: self.apply_preset(k))
            # 单击 = 选中（已开限速就立即按序播），双击 = 打开查看/编辑（里面有删除按钮）
            b.bind("<Double-Button-1>", lambda _e, cid=c["id"]: self.open_combo_editor(cid))
            b.grid(row=i // 4, column=i % 4, padx=(0, 5), pady=2, sticky="w")
            self.combo_btns[key] = b
        self._sync_combo_bar()

    def _sync_combo_bar(self):
        """编辑/删除只跟着「当前选中的组合」出现；下面那行再写播放进度"""
        if not hasattr(self, "combo_edit_bar"):
            return
        key = W.G.preset_key or ""
        combo = None
        if key.startswith(W.COMBO_KEY_PREFIX):
            cid = key[len(W.COMBO_KEY_PREFIX):]
            combo = next((c for c in getattr(self, "combo_items", [])
                          if c["id"] == cid), None)
        st = W.combo_state() if combo is not None else None
        text = S.combo_bar(combo["name"] if combo else None,
                           W.combo_summary(combo) if combo else None,
                           st, W.G.limit_on, W.MAX_COMBO_STEPS,
                           bool(getattr(self, "combo_items", [])))
        if combo is None:
            for b in (self.btn_combo_edit, self.btn_combo_del):
                try:
                    if b.winfo_manager():
                        b.pack_forget()
                except Exception:
                    pass
        else:
            for b in (self.btn_combo_edit, self.btn_combo_del):
                if not b.winfo_manager():
                    b.pack(side="left", padx=(0 if b is self.btn_combo_edit else 8, 0))
            self._set(self.btn_combo_del, text="🗑 删除「%s」" % combo["name"])
        self._set(self.lab_combo, text=text)

    def edit_combo(self):
        key = W.G.preset_key or ""
        if not key.startswith(W.COMBO_KEY_PREFIX):
            return
        self.open_combo_editor(key[len(W.COMBO_KEY_PREFIX):])

    def del_combo(self):
        """删除当前选中的组合（正在播放的先停，免得后台继续改参数）"""
        key = W.G.preset_key or ""
        if not key.startswith(W.COMBO_KEY_PREFIX):
            return
        self.delete_combo_by_id(key[len(W.COMBO_KEY_PREFIX):])

    def delete_combo_by_id(self, cid, ask=True):
        """删掉一支组合，并把所有看到它的地方都刷新一遍。

        入口有两处：主界面选中后的「🗑 删除」，以及查看/编辑那扇窗里的删除按钮 ——
        同一套删除逻辑，避免两处行为不一致（一个停播放另一个不停之类的）。
        """
        combo = W.find_combo(cid)
        if combo is None:
            self.reload_combo()
            return False
        if ask and not messagebox.askyesno(
                "删除弱网组合", "确定删除「%s」？此操作不可撤销。" % combo["name"]):
            return False
        self._run(W.stop_combo())
        ok, _left = W.delete_combo(cid)
        if ok:
            W.log_event("config", "删除弱网组合：" + combo["name"])
            # 删除后 preset_key 指向已不存在的组合：切回直连最干净
            self._run(W.set_profile(W.default_profile(), "direct"))
            W.log_event("config", "删除组合后切回直连")
        self.reload_combo()
        self.load_current()
        self.refresh()
        # 共享窗口开着的话，它那份列表也列着组合，得跟着少一行
        if getattr(self, "share_win", None) is not None:
            self.reload_share()
        return ok

    # ---- 组合编辑器 ----

    def open_combo_editor(self, cid=None):
        """新建 / 编辑组合：名字 + 若干段（场景 + 分/秒）+ 是否循环"""
        if self.combo_win is not None:
            # 窗口还开着时，只有「同一支组合」才直接提前台；要看别的组合就得重建，
            # 否则「保存后接着新建」的模式会把别人点的组合挡住（界面上还是新建那份）
            same = (self._combo_editing or "") == (cid or "")
            if same:
                # 已经处于新建模式时再点「＋ 新建组合」：得把界面上填了一半的内容清掉。
                # 否则用户以为在排新组合，实际上一支没填完的还留在那儿，一不留神就存成两支。
                if not cid:
                    try:
                        self._combo_reset_to_new("新建一支组合：填好名字和各段时间后保存。")
                    except Exception:
                        pass
                try:                            # 已经开着就提到前台，不重复建
                    self.combo_win.deiconify()
                    self.combo_win.lift()
                    return
                except Exception:
                    self.combo_win = None
            else:
                # 换看别的组合：旧编辑器要整个重建（界面上还填着上一支的内容）。
                # 走 close_combo_editor 而不是直接 destroy —— 它会顺带把从旧
                # 编辑器里点开的组合说明窗也关掉，否则会剩一个孤儿窗口
                try:
                    self.close_combo_editor()
                except Exception:
                    pass
        combo = W.find_combo(cid) if cid else None
        win = tk.Toplevel(self.root)
        win.title("编辑弱网组合" if combo else "新建弱网组合")
        win.configure(bg=BG)
        win.geometry("520x520")
        try:
            win.transient(self.root)
        except Exception:
            pass
        try:                                    # 居中到主窗口上
            self.root.update_idletasks()
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw, rh = self.root.winfo_width(), self.root.winfo_height()
            win.geometry("+%d+%d" % (max(0, rx + (rw - 520) // 2),
                                     max(0, ry + (rh - 520) // 2)))
        except Exception:
            pass
        win.protocol("WM_DELETE_WINDOW", self.close_combo_editor)
        self.combo_win = win
        self._combo_editing = combo["id"] if combo else None
        self._combo_rows = []

        card = tk.Frame(win, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="both", expand=True, padx=12, pady=12)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=12, pady=10)

        # 标题行：左边写清现在是新建还是编辑，右边挂「组合说明」入口。
        # 说明入口跟着编辑器走：组合怎么播、有哪些例子，只有真正动手排组合的
        # 时候才需要知道，摆在组合列表顶上反倒像跟每一支组合都有关
        thead = tk.Frame(inner, bg=CARD)
        thead.pack(fill="x", pady=(0, 8))
        self.lab_combo_editor_title = tk.Label(
            thead, text="编辑弱网组合" if combo else "新建弱网组合",
            font=("Microsoft YaHei UI", 11, "bold"), bg=CARD, fg=TEXT)
        self.lab_combo_editor_title.pack(side="left")
        cb = tk.Button(thead, text="！", font=("Microsoft YaHei UI", 10, "bold"),
                       bg=CARD, fg=MUTED, relief="flat", bd=0, padx=3, pady=0,
                       cursor="hand2", takefocus=0, activebackground=CARD,
                       activeforeground=ACCENT, command=self._combo_open_notes)
        cb.pack(side="right")
        cb.bind("<Enter>", lambda _e, w=cb: w.configure(fg=ACCENT))
        cb.bind("<Leave>", lambda _e, w=cb: w.configure(fg=MUTED))

        row0 = tk.Frame(inner, bg=CARD)
        row0.pack(fill="x")
        tk.Label(row0, text="组合名称", font=FONT_B, bg=CARD, fg=TEXT).pack(side="left")
        self.combo_name = _cap_name_var(
            tk.StringVar(value=(combo["name"] if combo else "")))
        ent = tk.Entry(row0, textvariable=self.combo_name, font=FONT, width=16,
                       relief="flat", highlightthickness=1, highlightbackground=LINE,
                       highlightcolor=ACCENT, bg=FIELD, fg=TEXT)
        ent.pack(side="left", padx=(8, 0))
        tk.Label(row0, text="最多 %d 字" % W.NAME_MAX, font=FONT, bg=CARD,
                 fg=MUTED).pack(side="left", padx=(8, 0))
        ent.focus_set()

        self.combo_loop = tk.BooleanVar(value=bool(combo and combo.get("loop")))
        tk.Checkbutton(inner, text="播完后循环（不勾则停在最后一段）", font=FONT,
                       bg=CARD, fg=TEXT, variable=self.combo_loop,
                       activebackground=CARD, selectcolor=SELECT).pack(
            anchor="w", pady=(6, 0))

        tk.Label(inner, text="按顺序播放（最多 %d 段，每段都要填时间）"
                 % W.MAX_COMBO_STEPS, font=FONT_B, bg=CARD, fg=TEXT).pack(
            anchor="w", pady=(12, 4))
        self.combo_steps_box = tk.Frame(inner, bg=CARD)
        self.combo_steps_box.pack(fill="x")
        # 历史存档里万一混进了嵌套段（下拉早就不列了，但旧文件可能带着）：
        # 直接不显示它，并告诉用户被去掉了几段，别让他对着少了一段的列表发懵
        raw_steps = (combo["steps"] if combo
                     else [{"key": "5g", "minutes": 0, "seconds": 30}])
        steps, nested = [], 0
        for st in raw_steps[:W.MAX_COMBO_STEPS]:
            if str(st.get("key") or "").startswith(W.COMBO_KEY_PREFIX):
                nested += 1
                continue
            steps.append(st)
        if not steps:
            steps = [{"key": "5g", "minutes": 0, "seconds": 30}]
        for st in steps:
            self._combo_add_row(st)
        if nested:
            win.after(80, lambda n=nested: messagebox.showinfo(
                "弱网组合",
                "这个组合里有 %d 段选的是别的组合，已帮你去掉（组合不能嵌套）。\n"
                "重新选好场景后保存即可。" % n))

        self.btn_combo_add = tk.Button(inner, text="＋ 添加一段", font=FONT,
                                       bg=BTN2, fg=ACCENT, relief="flat",
                                       padx=10, pady=3, cursor="hand2",
                                       command=lambda: self._combo_add_row())
        self.btn_combo_add.pack(anchor="w", pady=(6, 0))
        self._combo_sync_add()

        bar = tk.Frame(inner, bg=CARD)
        bar.pack(fill="x", pady=(14, 0))
        tk.Button(bar, text="保存组合", font=FONT_B, bg=ACCENT, fg="white",
                  relief="flat", padx=14, pady=5, cursor="hand2",
                  command=self.save_combo).pack(side="left")
        # 查看已有组合时，删除入口就在这扇窗里：不用先切回主界面再找删除
        self.btn_combo_del_in = tk.Button(bar, text="🗑 删除该组合", font=FONT,
                                           bg=BTN2, fg=ERR, relief="flat",
                                           padx=12, pady=5, cursor="hand2",
                                           command=self._combo_delete_in_editor)
        # 不放「取消」按钮：标题栏右上角的 ✕ 干的是同一件事（都是不保存直接关），
        # 两个入口摆在一起只会让人犹豫该点哪个
        if combo:
            self.btn_combo_del_in.pack(side="left", padx=(8, 0))
        self.lab_combo_tip = tk.Label(inner, text="", font=FONT, bg=CARD, fg=MUTED,
                                      anchor="w", justify="left", wraplength=430)
        self.lab_combo_tip.pack(fill="x", pady=(8, 0))
        tk.Label(inner, text="每段的「分」和「秒」至少填一个且大于 0；时间到点自动切下一段，"
                             "上限 %d 段。保存后可以接着排下一支，不用关窗。" % W.MAX_COMBO_STEPS,
                 font=FONT, bg=CARD, fg=MUTED, anchor="w", justify="left",
                 wraplength=430).pack(fill="x", pady=(6, 0))

    def _combo_open_notes(self):
        """编辑器里的「！」：打开组合说明，挂在编辑器下面。

        传 parent=self.combo_win，说明窗就压在编辑器之上；编辑器一关，
        说明窗跟着消失（Tk 的 transient 关系），不会剩个孤儿窗口。
        """
        try:
            self.open_combo_notes(self.combo_win)
        except Exception:
            self.open_combo_notes()

    def _combo_add_row(self, st=None):
        """编辑器里的一行：场景 + 分 + 秒 + 删除本行"""
        # 硬上限写在这里而不是只靠按钮禁用：否则任何绕过按钮的路径（编辑旧组合、
        # 手抖多按一次）都能排到 6 段，最后被 norm_combo_steps 悄悄砍掉第 6 段
        if len(self._combo_rows) >= W.MAX_COMBO_STEPS:
            return
        row = tk.Frame(self.combo_steps_box, bg=CARD)
        row.pack(fill="x", pady=2)
        labels, keys = self._scene_options()
        if not keys:
            tk.Label(row, text="（暂无可用场景）", font=FONT, bg=CARD, fg=MUTED).pack(side="left")
            self._combo_rows.append({"frame": row, "cb": None, "keys": [],
                                     "mv": tk.StringVar(), "sv": tk.StringVar()})
            self._combo_sync_add()
            return
        cb = ttk.Combobox(row, values=labels, width=16, state="readonly", font=FONT)
        cb.pack(side="left")
        idx = keys.index(st["key"]) if st and st["key"] in keys else 0
        cb.current(idx)
        mv = tk.StringVar(value=str(int(st.get("minutes", 0) or 0)) if st else "0")
        sv = tk.StringVar(value=str(int(st.get("seconds", 0) or 0)) if st else "30")
        ttk.Spinbox(row, from_=0, to=W.STEP_MAX_MINUTES, width=4,
                    textvariable=mv, font=FONT).pack(side="left", padx=(10, 2))
        tk.Label(row, text="分", font=FONT, bg=CARD, fg=MUTED).pack(side="left")
        ttk.Spinbox(row, from_=0, to=W.STEP_MAX_SECONDS, width=4,
                    textvariable=sv, font=FONT).pack(side="left", padx=(10, 2))
        tk.Label(row, text="秒", font=FONT, bg=CARD, fg=MUTED).pack(side="left")
        item = {"frame": row, "cb": cb, "keys": keys, "mv": mv, "sv": sv}
        self._combo_rows.append(item)
        tk.Button(row, text="✕", font=FONT, bg=CARD, fg=MUTED, relief="flat",
                  padx=6, pady=0, cursor="hand2",
                  command=lambda it=item: self._combo_del_row(it)).pack(side="left", padx=(10, 0))
        self._combo_sync_add()

    def _combo_del_row(self, item):
        try:
            item["frame"].destroy()
        except Exception:
            pass
        try:
            self._combo_rows.remove(item)
        except ValueError:
            pass
        self._combo_sync_add()

    def _combo_sync_add(self):
        """到上限就把「添加一段」禁用：与其让用户排第 6 段时被静默裁掉，不如直接说清楚"""
        if not hasattr(self, "btn_combo_add"):
            return
        full = len(self._combo_rows) >= W.MAX_COMBO_STEPS
        try:
            self.btn_combo_add.config(state="disabled" if full else "normal",
                                      text=("已达上限（%d 段）" % W.MAX_COMBO_STEPS)
                                      if full else "＋ 添加一段")
        except Exception:
            pass

    def save_combo(self):
        name = W.limit_item_name(self.combo_name.get())
        if not name:
            messagebox.showinfo("弱网组合", "请先给这个组合起个名字。")
            return
        steps = []
        for i, r in enumerate(self._combo_rows, 1):
            cb = r.get("cb")
            if cb is None:
                continue
            idx = cb.current()
            if idx < 0 or idx >= len(r["keys"]):
                messagebox.showinfo("弱网组合", "第 %d 段还没选场景。" % i)
                return
            try:
                mi = max(0, int(float(r["mv"].get() or 0)))
            except Exception:
                mi = 0
            try:
                se = max(0, int(float(r["sv"].get() or 0)))
            except Exception:
                se = 0
            if mi <= 0 and se <= 0:
                messagebox.showinfo(
                    "弱网组合",
                    "第 %d 段没有填时间：分和秒至少要有一个大于 0。" % i)
                return
            key = r["keys"][idx]
            if key.startswith(W.COMBO_KEY_PREFIX):
                # 界面下拉本来就不该出现组合；真走到这儿说明是旧存档带进来的脏数据，
                # 直接拒绝保存并指明段落，而不是让它被 norm_combo_steps 悄悄丢掉
                messagebox.showinfo(
                    "弱网组合",
                    "第 %d 段选的是另一个组合「%s」。\n\n组合里不能再套组合，"
                    "请改选一个预设或自定义场景。" % (i, W.scene_name(key)))
                return
            steps.append({"key": key, "minutes": mi, "seconds": se})
        if not steps:
            messagebox.showinfo("弱网组合", "至少要排一段场景。")
            return
        combo, _combos = W.upsert_combo(name, steps, loop=self.combo_loop.get(),
                                        cid=self._combo_editing)
        if combo is None:
            # 一段都留不下（全是嵌套组合之类的脏数据）：不存空壳，原来的组合也不动
            messagebox.showinfo(
                "弱网组合",
                "这个组合里没有一段能用的场景，没有保存。\n\n"
                "组合里不能再套组合，请改选一个预设或自定义场景。")
            return
        W.log_event("config", "保存弱网组合：" + combo["name"])
        self.reload_combo()
        self.refresh()
        # 保存完不关窗：直接切成「新建」模式，接着排下一支（用户连着建几支时最省事）
        self._combo_reset_to_new("已保存「%s」，可以接着排下一支。" % combo["name"])

    def _combo_reset_to_new(self, tip=""):
        """把编辑器清成「新建」状态：名字、循环开关、段列表都回到初始值"""
        self._combo_editing = None
        try:
            self.combo_name.set("")
            self.combo_loop.set(False)
        except Exception:
            pass
        for r in list(getattr(self, "_combo_rows", [])):
            try:
                r["frame"].destroy()
            except Exception:
                pass
        self._combo_rows = []
        self._combo_add_row({"key": "5g", "minutes": 0, "seconds": 30})
        try:
            self.combo_win.title("新建弱网组合")
        except Exception:
            pass
        # 编辑器内部的标题行也要跟着改：保存后它会切成「新建」模式，
        # 只改窗口标题栏的话，界面里那行大字会一直写着「编辑弱网组合」
        try:
            self.lab_combo_editor_title.config(text="新建弱网组合")
        except Exception:
            pass
        # 删除按钮只对「已有组合」有意义，新建模式下收起来
        try:
            self.btn_combo_del_in.pack_forget()
        except Exception:
            pass
        try:
            self.lab_combo_tip.config(text=tip)
        except Exception:
            pass

    def _combo_delete_in_editor(self):
        """在查看/编辑窗口里删掉当前这支组合：删完关窗，并把主界面、共享窗口刷新"""
        cid = self._combo_editing
        if not cid:
            return
        if self.delete_combo_by_id(cid):
            self.close_combo_editor()

    def close_combo_editor(self):
        # 说明窗是从编辑器里点开的（transient 挂在编辑器下面）。
        # Tk 不保证父窗口销毁时连带销毁 transient 子窗口，这里显式关一次，
        # 否则关掉编辑器后会剩一个孤儿说明窗飘在主界面上
        try:
            self.close_combo_notes()
        except Exception:
            pass
        try:
            self.combo_win.destroy()
        except Exception:
            pass
        self.combo_win = None
        self._combo_editing = None
        self._combo_rows = []

    def _build_stats(self):
        pass   # 实时状态已并入顶栏（见 _build_header）


    # ---------------- 接管范围：二级窗口 ----------------














    # ---------------- 环境共享：二级窗口 ----------------












    def _build_log(self):
        # 标题右侧挂「导出报告」：日志只是流水账，开发要的是能读的结论。
        # 点一下把当前状态快照渲染成 Markdown 落成文件（见 export_report）。
        box = self._card_x(self.col_right, "事件日志", expand=True,
                           action=("导出报告", self.export_report))[0]
        wrap = tk.Frame(box, bg=CARD)
        wrap.pack(fill="both", expand=True)
        # 右栏一整列都给它，所以不写死行数，跟着窗口高度长。
        # 用 Text 而不是 Listbox：Listbox 不能换行，长日志只能被截掉右半截，
        # 也没有横向滚动可拖，等于永远看不全。
        self.logbox = tk.Text(wrap, font=("Consolas", 9), bg=FIELD,
                              fg=TEXT, relief="flat", highlightthickness=1,
                              highlightbackground=LINE, bd=0,
                              wrap="char",          # 中文没有空格，按字符换行才不会溢出
                              spacing1=1, spacing3=2, padx=6, pady=4,
                              takefocus=0, cursor="arrow")
        sb = _scrollbar(wrap, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=sb.set)
        self.logbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # 只读：不许编辑，但允许选中复制
        self.logbox.configure(state="disabled")

    def export_report(self):
        """把当前状态快照导出成 Markdown 报告（供开发阅读）。

        取数走 W.status_payload()——与 /api/status 同一份口径，不另起一套。
        帧统计是 GUI 独有的，单独从 self._frames 取；没有就写 None，不影响报告。
        写盘失败要弹窗说清楚，不能让「点了没反应」看起来像功能不存在。
        """
        payload = W.status_payload()
        frames = self._frames.snapshot() if self._frames is not None else None
        # 报告会被转发给开发/测试甚至贴进 issue，完整 IP 等于把「被测 App
        # 访问了哪些服务」一起交出去。默认脱敏，只有明确选「否」才留原始地址。
        redact = messagebox.askyesnocancel(
            "导出报告",
            "是否对报告中的 IP 地址脱敏？\n\n"
            "「是」（推荐）：末两段显示为 x.x，例如 64.233.x.x，"
            "仍能看出网段与服务商。\n"
            "「否」：保留完整 IP，仅限内部排障使用。")
        if redact is None:          # 取消导出
            return
        text = R.build(payload, param_specs=W.PARAM_SPECS,
                       param_notes=W.PARAM_NOTES, frames=frames, redact=redact)
        initial = R.default_filename()
        path = filedialog.asksaveasfilename(
            title="导出弱网测试报告",
            defaultextension=".md",
            initialfile=initial,
            filetypes=[("Markdown 报告", "*.md"), ("文本文件", "*.txt"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            # newline=""：不让 Python 在 Windows 把 \n 改写成 \r\n，
            # 报告在任意平台 diff / 归档都是同一份字节。
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        except Exception as e:
            messagebox.showerror("导出报告", "保存失败：\n%s\n\n%r" % (path, e))
            return
        W.log_event("export", "已导出测试报告：" + path)
        if messagebox.askyesno(
                "导出报告",
                "报告已保存：\n%s\n\n是否现在打开？" % path):
            try:
                if sys.platform == "win32":
                    os.startfile(path)
                else:
                    webbrowser.open("file://" + path)
            except Exception as e:
                messagebox.showerror("导出报告", "打开失败：%r" % e)

    # ---------------- USB 手机弱网 ----------------
    #
    # 手机不装 APK，靠 adb 走 USB 线把手机流量引到本机的 WeakNet 代理：
    #   adb reverse 把手机的 localhost:端口 映射到电脑的端口
    #   + settings put global http_proxy 让手机用这个代理
    # 两条命令任何一条不成功，手机都不能上网 —— 所以失败必须弹窗说清楚，
    # 而且退出 / 停止限速时一定要把手机还原（见 usb_disconnect 的调用点）。


    # ---- adb 位置 ----



    # ---- 设备列表 ----




    # ---- 连接 / 断开 ----






    # ---- 线程与状态 ----





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
        # "c:<id>" = 弱网组合：选中它就开始按顺序播放若干段场景
        if str(key).startswith(W.COMBO_KEY_PREFIX):
            cid = key[len(W.COMBO_KEY_PREFIX):]
            combo = W.find_combo(cid)
            if combo is None:
                messagebox.showerror("我的弱网组合", "这个组合已不存在，已刷新列表。")
                self.reload_combo()
                return
            playable = W.combo_playable_steps(combo)
            if not playable:
                # 整支组合的场景都被删了：不能开一个什么都不播的播放器
                messagebox.showerror(
                    "我的弱网组合",
                    "「%s」里引用的场景都已不可用（可能已被删除），无法播放。\n\n"
                    "请打开组合编辑器，把这些段换成现有场景后再试。" % combo["name"])
                W.log_event("config", "组合「%s」的场景都已不可用，未播放" % combo["name"])
                self.reload_combo()
                return
            dead = len(combo.get("steps") or []) - len(playable)
            if dead:
                # 部分可用：照播，但要说清楚有几段会被跳过，否则用户会以为全程都测了
                W.log_event("config", "组合「%s」有 %d 段场景已不可用，播放时会跳过"
                            % (combo["name"], dead))
            applied = self._run(W.select_combo(cid))
            name = combo["name"]
            W.log_event("config", ("开始播放组合：" if applied
                                   else "选择组合（未开启限速，未生效）：") + name)
            self.load_current()
            self.refresh()
            return
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
        # 点击即生效的前提是弱网已经开着：开着就直接切换，没开则只把这支场景
        # 记下来（不能替用户开始测试，否则流量会在他没点头时被限住）
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
        # 与场景按钮同一套规则：弱网开着才真的落到流量上，没开则只记下这组参数
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
        # 判断依据是「本实例有没有接管」，不是「系统代理开没开」：
        # 用户本来就开着公司代理时，用后者会一进一出就把公司代理关掉。
        cur = W.sysproxy_owned()
        ok = W.set_sysproxy(not cur)
        if ok:
            W.log_event("config", "已" + ("开启" if not cur else "解除") + "本机流量接管（系统代理）")
            if cur:                       # 刚解除接管：手机那边也一并还原
                self.usb_disconnect("解除接管")
        else:
            messagebox.showerror(
                "失败",
                "修改系统代理失败。\n\n"
                "常见原因有两个：\n"
                "1. 权限不足 —— 以管理员身份重新运行本程序\n"
                "2. 系统代理被组策略或安全软件锁定 —— 打开"
                "「Internet 选项 → 连接 → 局域网设置」，"
                "看能否手动勾选「使用代理服务器」")
        self.refresh_header()

    def toggle_lan(self):
        """局域网访问开关。

        绑 0.0.0.0 等于把代理端口暴露给整个网段，开启前确认一次。
        这里刻意不做代理层鉴权：Android 原生 Wi-Fi 代理设置只有主机名、
        端口、绕过列表，没有凭据输入口，一旦要求 Proxy-Authorization，
        「手机 Wi-Fi 直连」这条路就彻底不可用。
        """
        if W.G.proxy_host == "127.0.0.1":
            ip = W.G.lan_ip or "本机局域网 IP"
            port = W.G.proxy_port
            if not messagebox.askyesno(
                    "开启局域网访问",
                    "开启后，本机代理端口会对同一网段的设备开放。\n\n"
                    "同一网络下的其它设备可直接把 HTTP 代理指向：\n\n"
                    "    %s:%d\n\n"
                    "请只在可信任的网络（家里 / 公司内网）使用。\n\n"
                    "是否继续？" % (ip, port)):
                return
            # restart_proxy 返回**实际**监听地址；失败时它会把旧监听恢复并抛错，
            # _run 返回 None。不按返回值判断的话，绑失败也会提示「已开启」，
            # 而界面上显示的是新地址、实际只有旧地址可用。
            got = self._run(W.restart_proxy("0.0.0.0"))
            self.refresh_header()
            if got != "0.0.0.0":
                W.log_event("error", "局域网监听开启失败，仍保持 %s" % (W.G.proxy_host,))
                return
            W.log_event("config", "代理监听切换为 0.0.0.0（同网段可访问）")
            messagebox.showinfo("局域网访问已开启",
                                "被测设备的 HTTP 代理填：\n\n    %s:%d"
                                % (ip, port))
        else:
            got = self._run(W.restart_proxy("127.0.0.1"))
            self.refresh_header()
            if got != "127.0.0.1":
                W.log_event("error", "切回仅本机失败，仍保持 %s" % (W.G.proxy_host,))
                return
            W.log_event("config", "代理监听切换为 127.0.0.1（仅本机）")

    def _sync_run_btn(self):
        """限速总开关的按钮态：未开启时是实心主按钮（邀请点开），运行中变成浅底的停止。"""
        if not hasattr(self, "btn_run"):
            return
        v = S.run_btn_view(W.G.limit_on)
        self._set(self.btn_run, text=v["text"], bg=self._tone(v["bg"]),
                  fg=self._tone(v["fg"]))

    def toggle_run(self):
        """开启 / 关闭限速。关闭不是丢场景：preset_key 保留，再开就复原。"""
        want = not W.G.limit_on
        self._run(W.set_limit(want))
        W.log_event("config", "开启限速" if want else "停止限速，恢复直连")
        # 关掉限速后手机上的代理就没意义了，留着只会让人以为手机坏了 —— 顺手还原
        if not want:
            self.usb_disconnect("停止限速")
        self.load_current()
        self.refresh()

    def stop_all(self):
        """保留旧入口名：等价于关掉限速总开关"""
        self._run(W.set_limit(False))
        W.log_event("config", "停止限速，恢复直连")
        self.usb_disconnect("停止限速")
        self.load_current()
        self.refresh()

    def on_close(self):
        if self._closing:
            return
        self._closing = True
        try:
            self.close_share()          # 先关二级窗口：它上面挂着定时刷新，别等主窗销毁才取消
            self.close_preset_notes()   # 场景说明只是只读参考，直接关掉即可
            self.close_param_note()     # 单个参数的「！」小窗同理
            self.close_combo_editor()   # 组合编辑器可能还开着（没保存的内容随之丢弃）
            try:
                # 刚点「连接」就关窗：先让在途 adb 操作落地（最多 3 秒），
                # 否则可能在进程退出后才把代理写进手机，没人再清
                deadline = time.time() + 3.0
                while self.usb_busy and time.time() < deadline:
                    self._usb_pump()
                    time.sleep(0.05)
                self._usb_pump()
            except Exception as e:
                W.log("退出时收尾 USB 操作失败：%r" % e)
            # 手机上还挂着代理就退出 = 手机直接断网，用户根本想不到是这边造成的。
            # 还原放到后台线程并限时等待：adb 卡住时同步跑会让窗口关不掉。
            if getattr(self, "usb_on", False):
                try:
                    self._usb_restore_on_close("退出程序")
                except Exception as e:
                    W.log("退出时还原手机失败：%r" % e)
            try:
                # 只还原「自己接管的那份」。用户自己的代理开着时退出，不动注册表。
                if W.sysproxy_owned():
                    W.set_sysproxy(False)
                    W.log_event("config", "退出：已解除本机流量接管")
            except Exception:
                pass
        finally:
            try:
                # 撤掉定时刷新再销毁窗口：否则 after 回调会在控件已销毁后触发，
                # 在控制台里刷一行 invalid command name（窗口版看不到，但一样是错）
                if getattr(self, "_after_id", None):
                    self.root.after_cancel(self._after_id)
            except Exception:
                pass
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass
            try:
                self.root.destroy()
            finally:
                # 日志是攒批落盘的：不在这里冲一次，最后几秒（含退出还原结果）会丢
                try:
                    W.flush_logs()
                except Exception:
                    pass


    # ---------------- 刷新：只在值真的变了才动控件 ----------------

    def _tone(self, tone):
        """语义色调 → 当前主题色值；None 表示这一项不该改颜色。"""
        if tone is None:
            return None
        return {"ok": OK, "warn": WARN, "muted": MUTED, "text": TEXT,
                "accent": ACCENT, "btn2": BTN2, "white": "white"}.get(tone)

    def _set(self, w, **opts):
        """给控件下发选项，但**只下发变化的那些**。

        500ms 一轮是常驻路径，绝大多数轮次的值与上一轮完全相同；Tk 的 config()
        即使写同值也要走一遍选项解析并安排重绘。实测原来每轮无条件下发 20 次，
        等于每分钟 2400 次空转。这里按控件记住上次下发的值，没变就直接返回。

        缓存用 WeakKeyDictionary：预设/自定义/组合按钮每次改动都会重画，
        旧控件被回收后缓存条目自动消失，不会把新控件认成旧的。
        """
        cache = self._wcache.get(w)
        if cache is None:
            cache = {}
            self._wcache[w] = cache
        delta = {k: v for k, v in opts.items() if cache.get(k, _UNSET) != v}
        if not delta:
            return False
        cache.update(delta)
        w.config(**delta)
        return True

    def refresh_header(self):
        try:
            on = W.sysproxy_owned()
        except Exception:
            on = False
        v = S.header_view(W.G.proxy_host, W.G.proxy_port, W.G.lan_ip,
                          self.ctrl_port, on)
        self._set(self.lab_proxy, text=v["proxy"])
        self._set(self.lab_ctrl, text=v["ctrl"])
        self._set(self.btn_lan, text=v["lan_text"])
        self._set(self.btn_sys, text=v["sys_text"],
                  bg=OK if v["sys_on"] else ACCENT)

    def _paint_selected(self, btns, sel_key, attr):
        """给选中按钮上色；选中项没变就整轮跳过。

        500ms 一轮对每个按钮无条件 config(bg=..., fg=...) 会让 Tk 反复重排
        按钮区，空闲时也在做无用功。按钮重建时调用方需把缓存位重置为 None。
        """
        if getattr(self, attr, None) == sel_key:
            return
        setattr(self, attr, sel_key)
        for key, btn in btns.items():
            btn.config(bg=ACCENT if key == sel_key else BTN2,
                       fg="white" if key == sel_key else TEXT)

    def refresh(self):
        if self._closing:
            return
        _t_frame = time.perf_counter()
        self._usb_pump()      # 后台 adb 的结果在这里回到主线程
        self._update_pump()   # GitHub 新版本检查结果也在主线程落到版本号标签
        g = W.G
        now = time.time()
        dt = now - self._last[0]
        if dt >= 0.4:
            self._rate = ((g.bytes_down - self._last[1]) / dt,
                          (g.bytes_up - self._last[2]) / dt)
            self._last = (now, g.bytes_down, g.bytes_up)

        self.refresh_header()
        self._sync_run_btn()      # 其它端（控制接口/共享推送）也可能开关了限速
        # 标签跟着选中项走：组合与自定义场景合成一张卡片后，
        # 选中的东西不在当前这一页时用户会以为「没生效」——自动翻到对应的那页
        key = g.preset_key or ""
        if key != getattr(self, "_last_tab_key", None):
            self._last_tab_key = key
            if key.startswith(W.COMBO_KEY_PREFIX):
                self.switch_tab("combo")
            elif key.startswith("u:"):
                self.switch_tab("scene")
        # 每次循环都把标签配色和当前页对齐（幂等），任何路径改过背景都能立刻刷回来
        self._paint_tabs(getattr(self, "cur_tab", "scene"))
        try:
            n = len(W.G.inbox)    # 收到的同名环境，没人管就别让按钮看着平平无奇
            self._set(self.btn_share, text=S.share_view(n))
        except Exception:
            pass
        # 顶栏那颗状态灯说的是「限速有没有在跑」，不是「服务在不在跑」：
        # 启动后默认未开启，此时流量原样直通，必须让用户一眼看出来
        light, light_tone = S.light_view(g.limit_on, g.gate_open)
        self._set(self.dot, text=light, fg=self._tone(light_tone))
        # 未开启限速时 profile 还是直连（选场景不会灌进引擎），
        # 直接显示 profile 的名字会永远是「直连（不限制）」——按 preset_key 显示选中的那支
        scene = S.scene_label(g.profile.get("name", "-"), g.preset_key, g.limit_on,
                              lambda k: W.build_preset_profile(k).get("name", "-"))
        for key, text, tone in S.stats_view(g.conns_total, g.conns_active,
                                            self._rate, g.gate_open, scene):
            if tone is None:
                self._set(self.stat[key], text=text)
            else:
                self._set(self.stat[key], text=text, fg=self._tone(tone))

        self._paint_selected(self.preset_btns, g.preset_key, "_sel_preset")
        # 自定义场景按钮与内置档位同样高亮当前生效的那一个
        self._paint_selected(getattr(self, "mine_btns", {}), g.preset_key, "_sel_mine")
        # 组合按钮同理；下面再同步它的进度行（播放到第几段、还剩多久）
        self._paint_selected(getattr(self, "combo_btns", {}), g.preset_key, "_sel_combo")
        # 场景说明窗口开着时，切场景要同步正文里的「当前选中」标记，
        # 否则窗口里标的是打开那一刻的档位，跟界面上的高亮对不上
        try:
            if self.notes_win is not None and self._notes_key != g.preset_key:
                self._fill_preset_notes()
        except Exception:
            pass

        # 接管范围可能被其它端经控制接口改过，提示行同步显示最新的生效值
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
        # 组合同理：别处增删了要重画；没变则只刷播放进度与按钮行
        try:
            if ",".join(c["id"] for c in W.get_combos()) != self.combo_sig:
                self.reload_combo()
            else:
                self._sync_combo_bar()
        except Exception:
            pass

        ev = list(g.events)
        # 用事件序号而不是 len：deque 到 300 条后 len 恒定，界面会从此不再刷新，
        # 看起来像「程序卡住」——实际事件一直在产生
        if g.event_seq != self._ev_seq:
            self._ev_seq = g.event_seq
            self.logbox.configure(state="normal")
            self.logbox.delete("1.0", "end")
            for e in list(ev)[-60:][::-1]:
                self.logbox.insert("end", f"[{e['t']}] {e['msg']}\n")
            self.logbox.configure(state="disabled")

        # 帧统计：间隔包含定时器调度延迟，单帧耗时是本轮 refresh 自身的开销。
        # 每 60 帧（约 30 秒）落一条 perf 日志，长时间卡顿事后能定位到时间点。
        if self._frames is None:
            self._frames = P.FrameStats()
        if self._frames.record(_t_frame * 1000.0,
                              (time.perf_counter() - _t_frame) * 1000.0):
            W.log("[perf] GUI 刷新 " + self._frames.summary())

        self._after_id = self.root.after(500, self.refresh)


def pick_ports(proxy_port, ctrl_port):
    """端口被占用时自动顺延（与 CLI 行为一致）"""
    if not W.can_bind(ctrl_port, "127.0.0.1"):
        if W.is_weaknet_running(ctrl_port):
            return None, None
        ctrl_port = W.find_free_port(ctrl_port, "127.0.0.1")
    if not W.can_bind(proxy_port, "127.0.0.1"):
        proxy_port = W.find_free_port(proxy_port, "127.0.0.1")
    return proxy_port, ctrl_port


def _announce(tag, payload, ready_file=None):
    """向宿主报告启动结果：stdout 打一行 WEAKNET_<TAG> <json>；给了 --ready-file 再写一份。

    --windowed 打包出来没有控制台（sys.stdout 为 devnull），所以文件通道才是可靠的那条；
    stdout 那条留给源码运行 / 调试。
    """
    line = "WEAKNET_%s %s" % (tag, json.dumps(payload, ensure_ascii=False))
    try:
        print(line, flush=True)
    except Exception:
        pass
    if not ready_file:
        return
    try:
        tmp = ready_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ready_file)
    except Exception:
        pass


def _headless_ready(proxy_port, ctrl_port, ready_file, timeout=20.0):
    """等引擎真正能应答 /api/status 再报告端口 —— 端口顺延后的实际值必须让宿主知道。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if W.is_weaknet_running(ctrl_port):
            _announce("READY", {"status": "ready", "proxy_port": proxy_port,
                                "ctrl_port": ctrl_port, "pid": os.getpid(),
                                "version": getattr(W, "VERSION", "")}, ready_file)
            return True
        time.sleep(0.3)
    _announce("FAILED", {"status": "failed", "reason": "控制接口 %d 未在 %.0f 秒内就绪"
                         % (ctrl_port, timeout), "pid": os.getpid()}, ready_file)
    return False


def main():
    global _HEADLESS
    ap = argparse.ArgumentParser(description="WeakNet")
    ap.add_argument("--proxy-port", type=int, default=8888)
    ap.add_argument("--ctrl-port", type=int, default=8899)
    ap.add_argument("--theme", choices=("light", "dark"), default=None,
                    help="界面配色；默认浅色，随宿主（resources/weaknet）运行时自动深色")
    ap.add_argument("--headless", action="store_true",
                    help="只跑引擎、不建窗口（由宿主进程驱动界面）；就绪后报告实际端口")
    ap.add_argument("--ready-file", default=None,
                    help="把就绪信息写进该文件（无控制台打包时用这个，而不是 stdout）")
    args = ap.parse_args()

    _HEADLESS = bool(args.headless)
    # 日志攒批落盘；GUI 没有 CLI 那条 atexit 链，进程异常退出时最后几秒会丢
    atexit.register(W.flush_logs)

    W.G.lan_ip = W.detect_lan_ip()
    proxy_port, ctrl_port = pick_ports(args.proxy_port, args.ctrl_port)
    if proxy_port is None:
        _announce("ALREADY", {"status": "already", "ctrl_port": args.ctrl_port,
                              "pid": os.getpid(),
                              "version": getattr(W, "VERSION", "")}, args.ready_file)
        if not args.headless:
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

    if args.headless:
        if not _headless_ready(proxy_port, ctrl_port, args.ready_file):
            raise SystemExit(1)
        # 主线程只负责「活着」：窗口没了，进程不能跟着退，否则宿主一碰接口就断
        threading.Event().wait()
        return

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
        # 无控制台时 raise 只是静默闪退，改用弹窗把错误报出来；
        # headless 下没人点那个弹窗，宿主只会一直等，所以退化成 stdout + 退出码
        if _HEADLESS:
            sys.exit(1)
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("WeakNet 启动失败", err[-2000:])
            root.destroy()
        except Exception:
            pass
        sys.exit(1)
