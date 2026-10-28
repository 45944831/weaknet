# -*- coding: utf-8 -*-
"""GUI 左栏滚动回归测试（真开窗口，不需要真机 / 不需要引擎）

缺陷背景
--------
EXE 窗口模式下左栏是裸 Frame 直接 pack 堆叠，整个主窗口没有任何滚动容器。
窗口高度按 min(860, 屏高-90) 定，1366×768 这类笔记本上留给 body 的只有
400 出头，而左栏（场景预设 + 自定义参数）约 780px 高 —— 底部几个参数
（断网间隔 / 断网时长）和保存栏被窗口下边缘直接裁掉，滚轮和拖拽都翻不出来。
用户看到的就是「参数显示不全、没法下滑」。

本测试覆盖
----------
C1  小窗口 + 展开自定义参数后，左栏内容确实超出可视区
C2  内容溢出时滚动条出现
C3  滚到底部后，最后一个参数（断网时长）完整可见
C4  左栏内滚轮向下：视图真的往下走
C5  滚轮落在右栏日志上：左栏不动
C6  滚轮落在数字框上：返回 "break"（不改数值）且左栏翻页
C7  点「展开」后，展开出来的内容自动滚进视野（比视口高时对齐顶部）
C8  内容装得下时滚动条自动收起
C9  13 个参数全部可到达
C10 右栏日志自身滚动不受影响
C11 接管范围弹窗里的列表不受左栏滚轮影响
C12 窗口最小高度不超过小屏可用高度
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import os
import sys
import time
import tkinter as tk

sys.path.insert(0, ROOT)

import weaknet_gui as G   # noqa: E402




pass_n = fail_n = 0


def ck(name, ok, extra=""):
    global pass_n, fail_n
    if ok:
        pass_n += 1
        print("  PASS  %s%s" % (name, ("   " + extra) if extra else ""))
    else:
        fail_n += 1
        print("  FAIL  %s   <- %s" % (name, extra))


def settle(root, times=3, delay=0.03):
    for _ in range(times):
        root.update_idletasks()
        root.update()
        time.sleep(delay)


def walk(w):
    yield w
    try:
        kids = w.winfo_children()
    except Exception:
        return
    for k in kids:
        for x in walk(k):
            yield x


def find_label(root, text):
    for w in walk(root):
        try:
            if isinstance(w, tk.Label) and w.cget("text") == text:
                return w
        except Exception:
            continue
    return None


def find_btn(root, text):
    for w in walk(root):
        try:
            if isinstance(w, tk.Button) and w.cget("text") == text:
                return w
        except Exception:
            continue
    return None


class Ev(object):
    """假滚轮事件：只需要 widget 和 delta 两个属性"""

    def __init__(self, widget, delta):
        self.widget = widget
        self.delta = delta


def vy(app, w):
    """控件顶部在「视口坐标系」里的 y：0 = 可视区顶边。

    注意不能用 col_left 当基准 —— col_left 本身随滚动一起移动，
    减出来的值恒定不变，滚动与否都「看起来没动」。
    """
    return w.winfo_rooty() - app.left_canvas.winfo_rooty()


def visible_in_view(app, w):
    """控件是否完整落在左栏可视区内"""
    top = vy(app, w)
    bottom = top + w.winfo_height()
    h = app.left_canvas.winfo_height()
    return top >= -1 and bottom <= h + 1


def main():
    root = tk.Tk()
    app = G.App(root, None, 8899)
    # 停掉 500ms 的周期刷新：它会把日志框内容整体重画，
    # 测 C10 时刚塞进去的行会被清掉，结果不可判
    try:
        root.after_cancel(app._after_id)
    except Exception:
        pass
    app._closing = True

    # 压到窗口允许的最小高度（1366×768 笔记本上的最坏情况）
    root.geometry("880x700")
    settle(root, 6)

    view = app.left_canvas.winfo_height()
    print("  布局：窗口 700 高 → 左栏可视 %dpx，右栏日志 %dpx"
          % (view, app.logbox.winfo_height()))

    # ---------- C1 / C2 ----------
    app.custom_toggle()            # 展开自定义参数
    settle(root, 6)
    total = app.col_left.winfo_height()
    ck("C1 小窗口下左栏内容溢出可视区", total > view,
       "内容 %dpx / 可视 %dpx（溢出 %dpx）" % (total, view, total - view))
    ck("C2 溢出时滚动条出现", bool(app.left_sb.winfo_manager()),
       "scrollbar manager=%r" % app.left_sb.winfo_manager())

    # ---------- C3 ----------
    last_lab = find_label(app.col_left, G.FIELDS[-1][1])     # 「断网时长」
    ck("C3a 找到最后一个参数控件", last_lab is not None, G.FIELDS[-1][1])
    if last_lab is not None:
        app.left_canvas.yview_moveto(0.0)     # 展开时的自动滚动会改视图，先归零
        settle(root, 4)
        ck("C3b 未滚动时最后一个参数不可见", not visible_in_view(app, last_lab),
           "视口 y=%.0f（可视高 %d）" % (vy(app, last_lab), view))
        app.left_canvas.yview_moveto(1.0)
        settle(root, 4)
        ck("C3c 滚到底后最后一个参数完整可见", visible_in_view(app, last_lab),
           "视口 y=%.0f，控件高 %d" % (vy(app, last_lab), last_lab.winfo_height()))

    # ---------- C9：13 个参数都能到达 ----------
    reach = []
    for _k, lab, _u, _t in G.FIELDS:
        w = find_label(app.col_left, lab)
        if w is None:
            continue
        app._left_ensure_visible(w.master)
        settle(root, 2)
        if visible_in_view(app, w):
            reach.append(lab)
    ck("C9 全部 13 个参数都能滚动到可见", len(reach) == len(G.FIELDS),
       "%d / %d%s" % (len(reach), len(G.FIELDS),
                      "" if len(reach) == len(G.FIELDS)
                      else "  缺：" + ",".join(x[1] for x in G.FIELDS
                                               if x[1] not in reach)))

    # ---------- C4：左栏内滚轮 ----------
    app.left_canvas.yview_moveto(0.0)
    settle(root, 4)
    before = app.left_canvas.yview()[0]
    src = find_btn(app.col_left, "直连") or app.col_left
    for _ in range(3):
        app._left_wheel(Ev(src, -120))       # 向下滚 3 格
    settle(root, 3)
    after = app.left_canvas.yview()[0]
    ck("C4 左栏内滚轮向下，视图下移", after > before,
       "yview %.3f -> %.3f" % (before, after))

    # ---------- C5 / C10：右栏日志 ----------
    app.logbox.configure(state="normal")
    for i in range(120):
        app.logbox.insert("end", "[12:00:%02d] 测试日志第 %d 行，用来撑出滚动范围\n"
                          % (i % 60, i))
    app.logbox.configure(state="disabled")
    settle(root, 4)
    app.left_canvas.yview_moveto(0.0)
    app.logbox.yview_moveto(0.0)
    settle(root, 3)
    left_before = app.left_canvas.yview()[0]
    log_before = app.logbox.yview()[0]
    app._left_wheel(Ev(app.logbox, -120))     # 滚轮落在日志上
    settle(root, 3)
    ck("C5 滚轮在右栏日志上，左栏不动",
       abs(app.left_canvas.yview()[0] - left_before) < 1e-6,
       "左栏 %.3f -> %.3f" % (left_before, app.left_canvas.yview()[0]))
    app.logbox.yview_scroll(3, "units")
    settle(root, 3)
    ck("C10 右栏日志自身仍可滚动", app.logbox.yview()[0] > log_before,
       "日志 %.3f -> %.3f" % (log_before, app.logbox.yview()[0]))

    # ---------- C6：数字框滚轮 ----------
    sp = None
    for w in walk(app.col_left):
        if isinstance(w, G.ttk.Spinbox):
            sp = w
            break
    ck("C6a 找到参数数字框", sp is not None)
    if sp is not None:
        app.left_canvas.yview_moveto(0.0)
        settle(root, 4)
        y_before = app.left_canvas.yview()[0]
        val_before = sp.get()
        ret = app._left_wheel_break(Ev(sp, -120))
        settle(root, 3)
        ck("C6b 数字框滚轮返回 break（拦掉默认加减）", ret == "break", "return=%r" % ret)
        ck("C6c 数字框数值未被滚轮改动", sp.get() == val_before,
           "%r -> %r" % (val_before, sp.get()))
        ck("C6d 数字框上滚轮改为翻页", app.left_canvas.yview()[0] > y_before,
           "yview %.3f -> %.3f" % (y_before, app.left_canvas.yview()[0]))

    # ---------- C7：展开后自动滚进视野 ----------
    app.custom_toggle()            # 收起
    settle(root, 4)
    app.left_canvas.yview_moveto(0.0)
    settle(root, 4)
    card = app.custom_card
    ck("C7a 折叠卡收起后内容变少", not card.winfo_ismapped()
       or card.winfo_height() < view, "card h=%d" % card.winfo_height())
    app.custom_toggle()            # 再展开
    settle(root, 10)               # 等 after(20) 的滚动回调执行
    ch = card.winfo_height()
    top_now = vy(app, card)
    if ch > view:
        # 比视口高：应当对齐顶部，让用户从第一行参数看起
        ok = -1 <= top_now <= 2
        detail = "卡高 %d > 可视 %d，视口 y=%.0f（应对齐顶部 0）" % (ch, view, top_now)
    else:
        ok = visible_in_view(app, card)
        detail = "卡高 %d，视口 y=%.0f，可视 %d" % (ch, top_now, view)
    ck("C7b 展开后内容自动滚进视野", ok, detail)

    # ---------- C8：装得下时滚动条收起 ----------
    app.custom_toggle()            # 收起，内容缩到能装下
    root.geometry("880x1000")
    settle(root, 8)
    t2 = app.col_left.winfo_height()
    v2 = app.left_canvas.winfo_height()
    ck("C8 内容装得下时滚动条自动收起",
       t2 <= v2 and not app.left_sb.winfo_manager(),
       "内容 %dpx / 可视 %dpx，manager=%r" % (t2, v2, app.left_sb.winfo_manager()))
    # 再展开，确认滚动条能重新出现（隐藏/显示来回切换不能卡死）
    root.geometry("880x700")
    settle(root, 5)
    app.custom_toggle()
    settle(root, 6)
    ck("C8b 再次溢出时滚动条重新出现", bool(app.left_sb.winfo_manager()),
       "内容 %dpx / 可视 %dpx" % (app.col_left.winfo_height(),
                                  app.left_canvas.winfo_height()))

    # ---------- C11：接管范围弹窗不受影响 ----------
    app.open_scope()
    settle(root, 5)
    ck("C11a 接管范围弹窗已打开", app.scope_win is not None)
    if app.scope_win is not None:
        app.left_canvas.yview_moveto(0.0)
        settle(root, 3)
        y0 = app.left_canvas.yview()[0]
        app._left_wheel(Ev(app.scope_box, -120))
        settle(root, 3)
        ck("C11b 弹窗列表上的滚轮不带动左栏",
           abs(app.left_canvas.yview()[0] - y0) < 1e-6,
           "yview %.3f -> %.3f" % (y0, app.left_canvas.yview()[0]))
        app.close_scope(force=True)
        settle(root, 3)

    # ---------- C12：最小高度 ----------
    sh = root.winfo_screenheight()
    ck("C12 最小高度不超过小屏可用高度",
       root.minsize()[1] <= max(1, sh - 120),
       "minsize 高 %d，屏高 %d" % (root.minsize()[1], sh))

    print()
    print("  %d pass, %d fail" % (pass_n, fail_n))
    try:
        root.destroy()
    except Exception:
        pass
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
