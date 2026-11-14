# -*- coding: utf-8 -*-
"""配色与 ttk 主题层（P2-3 面板拆分的公共底座）。

面板模块（gui_notes / gui_scope / gui_share / gui_usb）都需要同一套颜色
与滚动条工厂。颜色原来定义在 weaknet_gui.py 顶层，面板一旦独立出去就会
形成「面板 import 主模块、主模块又 import 面板」的循环。这里把颜色、字体、
标题栏与 ttk 主题整体下沉，主模块与各面板都只依赖本模块。
"""
import os
import sys

import tkinter as tk
from tkinter import ttk

def _detect_dark():
    v = (os.environ.get("WEAKNET_THEME") or "").strip().lower()
    if v in ("dark", "1", "true", "yes"):
        return True
    if v in ("light", "0", "false", "no"):
        return False
    argv = [a.lower() for a in sys.argv[1:]]
    for i, a in enumerate(argv):
        if a == "--theme" and i + 1 < len(argv):
            return argv[i + 1] == "dark"
        if a.startswith("--theme="):
            return a.split("=", 1)[1] == "dark"
    if "--dark" in argv:
        return True
    if "--light" in argv:
        return False
    try:
        # 打包后 sys.argv[0] 是 exe 自己的路径，onefile 解包目录不参与判定
        base = sys.executable if getattr(sys, "frozen", False) else (sys.argv[0] or __file__)
        here = os.path.dirname(os.path.abspath(base))
        return (os.path.basename(here).lower() == "weaknet"
                and os.path.basename(os.path.dirname(here)).lower() == "resources")
    except Exception:
        return False


DARK = _detect_dark()

if DARK:
    # 与采集端 style.css 同一套色：背景/卡片/描边/文字/强调色
    BG = "#0f1117"
    CARD = "#161922"
    LINE = "#2a2f3d"
    TEXT = "#e0e4ef"
    MUTED = "#8a90a0"
    ACCENT = "#4f8ef7"
    ACCENT_D = "#3f7ae0"
    OK = "#3ecf8e"
    WARN = "#f5a623"
    ERR = "#f85151"
    BTN2 = "#1e2230"        # 次要按钮底
    BTN2_HOVER = "#2a2f3d"
    FIELD = "#0f1117"       # 输入框底：比卡片更暗一档，才有「凹进去」的层次
    CHIP = "#1e2230"        # 信息小标签底
    SELECT = "#22304d"      # 勾选/单选底
else:
    BG = "#f4f6f9"
    CARD = "#ffffff"
    LINE = "#e3e7ee"
    TEXT = "#1f2430"
    MUTED = "#6b7280"
    ACCENT = "#2d7ff9"
    ACCENT_D = "#1a63d8"
    OK = "#16a34a"
    WARN = "#d97706"
    ERR = "#c0392b"
    BTN2 = "#eef2f7"
    BTN2_HOVER = "#e2e9f6"
    FIELD = "#fbfcfe"
    CHIP = "#f1f4fa"
    SELECT = "#eef3ff"


def _darken_titlebar(win):
    """让系统标题栏也变深：不处理的话，深色窗口顶着一圈亮白边框很跳。"""
    if not DARK:
        return
    try:
        import ctypes
        hwnd = win.winfo_id()
        parent = ctypes.windll.user32.GetParent(hwnd)
        if parent:
            hwnd = parent
        for attr in (20, 19):           # 20: Win10 1903+ / 19: 更老的 Win10
            flag = ctypes.c_int(1)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(flag), ctypes.sizeof(flag)) == 0:
                break
    except Exception:
        pass


def _scrollbar(parent, **kw):
    """深色下换 ttk 滚动条。

    Windows 的 tk.Scrollbar 走原生绘制，显式设了 -background/-troughcolor
    也不生效（实测），深色窗口边上会留一条白条；clam 主题的 ttk 滚动条
    认配色。浅色保持原来的 tk.Scrollbar，观感不动。
    """
    if DARK:
        kw.pop("width", None)
        return ttk.Scrollbar(parent, **kw)
    return tk.Scrollbar(parent, **kw)


def _apply_theme(root):
    """深色主题的全局兜底。

    tk 的默认色是系统浅色，几十个按钮/输入框里只要有一个没写底色，
    深色窗口上就会闪出一块白（按下时的 activebackground 尤其明显）。
    统一走 option 数据库给未显式指定的控件补默认值，比逐个控件改更稳。
    """
    if not DARK:
        return
    defaults = {
        "*Button.activeBackground": BTN2_HOVER,
        "*Button.activeForeground": TEXT,
        "*Button.disabledForeground": MUTED,
        "*Button.highlightBackground": BG,
        "*Button.highlightColor": BG,
        "*Checkbutton.background": CARD,
        "*Checkbutton.activeBackground": CARD,
        "*Checkbutton.activeForeground": TEXT,
        "*Checkbutton.selectColor": SELECT,
        "*Radiobutton.background": BG,
        "*Radiobutton.activeBackground": BG,
        "*Radiobutton.activeForeground": TEXT,
        "*Radiobutton.selectColor": SELECT,
        "*Entry.background": FIELD,
        "*Entry.foreground": TEXT,
        "*Entry.insertBackground": TEXT,
        "*Entry.highlightBackground": LINE,
        "*Entry.highlightColor": ACCENT,
        "*Text.background": FIELD,
        "*Text.foreground": TEXT,
        "*Text.insertBackground": TEXT,
        "*Text.selectBackground": ACCENT,
        "*Text.selectForeground": "#ffffff",
        "*Listbox.background": CARD,
        "*Listbox.foreground": TEXT,
        "*Listbox.selectBackground": ACCENT,
        "*Listbox.selectForeground": "#ffffff",
        "*Scrollbar.background": BTN2,
        "*Scrollbar.troughColor": BG,
        "*Scrollbar.activeBackground": LINE,
        "*Scrollbar.highlightBackground": BG,
        "*TCombobox*Listbox.background": CARD,
        "*TCombobox*Listbox.foreground": TEXT,
        "*TCombobox*Listbox.selectBackground": ACCENT,
        "*TCombobox*Listbox.selectForeground": "#ffffff",
    }
    for key, val in defaults.items():
        try:
            root.option_add(key, val)
        except tk.TclError:
            pass
    st = ttk.Style(root)
    try:
        # vista 主题不认 fieldbackground/arrowcolor，深色下必须是 clam
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure("TCombobox", fieldbackground=FIELD, background=BTN2, foreground=TEXT,
                 arrowcolor=TEXT, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                 selectbackground=FIELD, selectforeground=TEXT, padding=2)
    st.map("TCombobox",
           fieldbackground=[("readonly", FIELD)],
           foreground=[("readonly", TEXT), ("disabled", MUTED)],
           background=[("active", BTN2_HOVER), ("pressed", BTN2_HOVER)],
           arrowcolor=[("active", TEXT), ("disabled", MUTED)],
           bordercolor=[("focus", ACCENT)],
           lightcolor=[("focus", ACCENT)])
    st.configure("TSpinbox", fieldbackground=FIELD, background=BTN2, foreground=TEXT,
                 arrowcolor=TEXT, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                 insertcolor=TEXT)
    st.map("TSpinbox",
           foreground=[("disabled", MUTED)],
           background=[("active", BTN2_HOVER)],
           arrowcolor=[("active", TEXT), ("disabled", MUTED)],
           bordercolor=[("focus", ACCENT)])
    for orient in ("Vertical", "Horizontal"):
        st.configure("%s.TScrollbar" % orient, background=BTN2, troughcolor=BG,
                     bordercolor=BG, arrowcolor=MUTED, lightcolor=BTN2,
                     darkcolor=BTN2, gripcount=0)
        st.map("%s.TScrollbar" % orient,
               background=[("active", LINE), ("pressed", LINE)],
               arrowcolor=[("active", TEXT)])

FONT = ("Microsoft YaHei UI", 9)
FONT_B = ("Microsoft YaHei UI", 9, "bold")
FONT_T = ("Microsoft YaHei UI", 13, "bold")

