# -*- coding: utf-8 -*-
"""参数说明（每个参数一个「！」）回归测试 —— 真开窗口，不需要真机 / 不需要引擎

需求背景
--------
自定义参数有 13 行，光看名字猜不出行为：「丢包率」填 30 是报错还是变慢？
「黑洞率」和「拒连率」都是连不上，差别在哪？「卡顿时长」单独填为什么没效果？
需要在每个参数旁加一个「！」入口，点开就是这个参数的作用与坑。

本测试覆盖
----------
P1  13 个「！」按钮都创建了，数量与 FIELDS 一致
P2  每个「！」都与对应的参数标签在同一行
P3  按钮文案是「！」，可点击
P4  点击后弹出说明小窗
P5  小窗标题带参数名
P6  正文与 PARAM_NOTES 逐条全文一致（13 个参数全比）
P7  小窗显示单位与取值范围
P8  再点同一个「！」收起（不会关不掉）
P9  换一个参数直接换成那一个，不会叠出第二个窗口
P10 ✕ / 关闭接口都能清掉引用
P11 收起「自定义参数」卡片时小窗一起关掉
P12 加了「！」之后两列不溢出（880 与最小宽度 820 都量）
P13 主窗口关闭流程不报错
P14 GUI 的 FIELDS 与注释表的 key 完全对应
"""
import os
import sys
import time
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import weaknet as W      # noqa: E402
import weaknet_gui as G  # noqa: E402

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

pass_n = fail_n = 0


def ck(name, ok, extra=""):
    global pass_n, fail_n
    if ok:
        pass_n += 1
        print("  PASS  %s%s" % (name, ("   " + extra) if extra else ""))
    else:
        fail_n += 1
        print("  FAIL  %s   <- %s" % (name, extra))


def settle(root, times=4, delay=0.03):
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


def labels_of(w):
    out = []
    for x in walk(w):
        if isinstance(x, tk.Label):
            try:
                out.append(str(x.cget("text")))
            except Exception:
                pass
    return out


def note_texts(app):
    """小窗里所有 Label 文本（正文是其中一个 Label）"""
    if app.param_win is None:
        return []
    return labels_of(app.param_win)


def cells_of(app):
    """参数行的容器（每个 cell 里有一个 Spinbox）"""
    spins = [w for w in walk(app.custom_card) if isinstance(w, ttk.Spinbox)]
    cells, seen = [], set()
    for s in spins:
        c = s.master
        if id(c) not in seen:
            seen.add(id(c))
            cells.append(c)
    return cells


def main():
    root = tk.Tk()
    app = G.App(root, None, 8899)
    try:
        root.after_cancel(app._after_id)
    except Exception:
        pass
    app._closing = True
    root.geometry("880x900")
    app.custom_toggle()                 # 展开自定义参数
    settle(root, 6)

    keys = [f[0] for f in G.FIELDS]

    # ---------- P1：13 个「！」都建出来了 ----------
    btns = getattr(app, "param_btns", {})
    ck("P1a 「！」按钮数量与参数行一致", len(btns) == len(keys),
       "%d / %d" % (len(btns), len(keys)))
    ck("P1b 每个参数都有「！」", sorted(btns) == sorted(keys),
       "缺：%r" % (sorted(set(keys) - set(btns)),))

    # ---------- P2：每个「！」在自己那一行 ----------
    cells = cells_of(app)
    ck("P2a 参数行数量正确", len(cells) == len(keys), "%d 行" % len(cells))
    bad_row = []
    for key, label, _u, _t in G.FIELDS:
        b = btns.get(key)
        if b is None:
            bad_row.append(key)
            continue
        cell = b.master
        texts = labels_of(cell)
        if label not in texts:
            bad_row.append("%s(找不到标签 %r)" % (key, label))
    ck("P2b 「！」与参数名在同一行", not bad_row,
       "全部同行" if not bad_row else "%r" % (bad_row,))

    # ---------- P2c/P2d：「！」要紧跟名字，同时数字框还得对齐 ----------
    # 这两条是冲突的：名字宽度不一样，按钮又必须贴名字，那空白只能留在按钮右边。
    # 之前把「！」挂在单位后面，读起来像数字框的附属品，用户反馈要改到这里。
    far, spin_x, missing = [], set(), []
    for key, label, _u, _t in G.FIELDS:
        b = btns[key]
        cell = b.master
        lab = next((w for w in cell.winfo_children()
                    if isinstance(w, tk.Label) and str(w.cget("text")) == label), None)
        sp = next((w for w in cell.winfo_children()
                   if isinstance(w, ttk.Spinbox)), None)
        if lab is None or sp is None:
            missing.append(key)
            continue
        gap = b.winfo_x() - (lab.winfo_x() + lab.winfo_width())
        if gap > 2:
            far.append("%s(间距 %dpx)" % (key, gap))
        spin_x.add(sp.winfo_x())
    ck("P2c 控件齐全（名字 / 按钮 / 数字框）", not missing,
       "" if not missing else "缺：%r" % (missing,))
    ck("P2d 「！」紧跟在参数名之后（<=2px）", not far,
       "全部贴住" if not far else "%r" % (far,))
    ck("P2e 各行数字框仍然对齐", len(spin_x) == 1,
       "左边界 %r" % (sorted(spin_x),))

    # ---------- P3：按钮文案与可点性 ----------
    ck("P3a 按钮文案都是「！」", all(b.cget("text") == "！" for b in btns.values()))
    ck("P3b 按钮都绑了命令", all(bool(b.cget("command")) for b in btns.values()))
    # 不显眼：无边框、无高亮框、底色与卡片一致、字是灰的（用户要求「不用那么明显」）
    b0 = btns["loss_pct"]
    ck("P3c 不显眼：无边框 + 底色同卡片 + 灰字",
       str(b0.cget("relief")) == "flat"
       and int(b0.cget("bd")) == 0
       and int(b0.cget("highlightthickness")) == 0
       and str(b0.cget("bg")).lower() == G.CARD.lower()
       and str(b0.cget("fg")).lower() == G.MUTED.lower(),
       "relief=%s bd=%s highlight=%s bg=%s fg=%s"
       % (b0.cget("relief"), b0.cget("bd"), b0.cget("highlightthickness"),
          b0.cget("bg"), b0.cget("fg")))

    # ---------- P4 / P5 / P6 / P7：逐个点开比对 ----------
    app.param_btns["loss_pct"].invoke()
    settle(root, 5)
    ck("P4 点击后弹出说明小窗", app.param_win is not None
       and app.param_win.winfo_exists(), "param_win=%r" % (app.param_win,))

    title = app.param_win.title() if app.param_win else ""
    ck("P5 小窗标题带参数名", "丢包率" in title, title)

    miss_note, miss_meta, miss_title = [], [], []
    for key, label, unit, top in G.FIELDS:
        app.open_param_note(key)
        settle(root, 3)
        texts = note_texts(app)
        # 说明窗排版改过一次：正文不再是「整段一坨」，而是拆成
        # 「作用」一段 + 「注意」若干条；单位与取值范围也拆成两枚小标签。
        # 这里按新的结构逐段比对：作用段在、每条提醒在、单位在、范围在。
        body, tips = W.split_param_note(W.PARAM_NOTES.get(key, ""))
        # 排版再改过一次：正文由引擎层预折行后用 \n 拼好，
        # 「·」拆成了独立控件（不再跟正文拼在同一个字符串里）。
        # 比对时把换行去掉；空格也要去掉 —— 断行点上的那个空格本来就不渲染
        # （「RTT 是 800ms」断成两行后，行末那个空格消失是正常的），
        # 真正要保证的是「一个字都不丢」。
        flat = [t.replace("\n", "").replace(" ", "") for t in texts]
        if body and body.replace(" ", "") not in flat:
            miss_note.append(key + "(作用段)")
        # 条目正文单独一格（去掉缩进后的内容），符号「·」是另一格
        for i, tip in enumerate(tips):
            if tip.replace(" ", "") not in flat:
                miss_note.append("%s(提醒%d)" % (key, i + 1))
        # 单位与取值范围是两枚 chip：标题与取值各占一个 Label
        if unit and unit not in texts:
            miss_meta.append(key + "(单位)")
        if ("0 ~ %s" % top) not in texts:
            miss_meta.append(key + "(范围)")
        if app.param_win is None or label not in app.param_win.title():
            miss_title.append(key)
    ck("P6 正文与 PARAM_NOTES 逐条一致", not miss_note,
       "%d/%d%s" % (len(keys) - len({m.split("(")[0] for m in miss_note}), len(keys),
                    "" if not miss_note else "  不一致：" + ",".join(miss_note)))
    ck("P7 显示单位与取值范围", not miss_meta,
       "%d/%d" % (len(keys) - len({m.split("(")[0] for m in miss_meta}), len(keys))
       + ("" if not miss_meta else "  缺：" + ",".join(miss_meta)))
    ck("P5b 13 个参数的标题都对得上", not miss_title,
       "" if not miss_title else "异常：" + ",".join(miss_title))

    # ---------- P8：再点同一个收起 ----------
    app.open_param_note("loss_pct")
    settle(root, 3)
    wid = app.param_win.winfo_id() if app.param_win else None
    app.open_param_note("loss_pct")          # 再点一次
    settle(root, 3)
    ck("P8 再点同一个「！」收起", app.param_win is None, "param_win=%r" % (app.param_win,))

    # ---------- P9：换参数不叠窗口 ----------
    app.open_param_note("loss_pct")
    settle(root, 3)
    app.open_param_note("jitter_ms")
    settle(root, 3)
    tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    ck("P9a 只有一个小窗", len(tops) == 1, "%d 个 Toplevel" % len(tops))
    ck("P9b 换成新参数的内容", app._param_key == "jitter_ms"
       and "抖动" in app.param_win.title(),
       "key=%r title=%r" % (app._param_key, app.param_win.title()))

    # ---------- P10：✕ 按钮与关闭接口 ----------
    close_btn = None
    for x in walk(app.param_win):
        if isinstance(x, tk.Button) and str(x.cget("text")) == "✕":
            close_btn = x
            break
    ck("P10a 小窗有关闭按钮", close_btn is not None)
    if close_btn is not None:
        close_btn.invoke()
        settle(root, 3)
        ck("P10b 点 ✕ 后引用清空",
           app.param_win is None and app._param_key is None,
           "param_win=%r key=%r" % (app.param_win, app._param_key))
    app.open_param_note("loss_pct")
    settle(root, 2)
    app.close_param_note()
    settle(root, 2)
    ck("P10c 关闭接口可重复调用", app.close_param_note() is None
       and app.param_win is None)

    # ---------- P11：收起卡片时小窗一起关 ----------
    app.open_param_note("loss_pct")
    settle(root, 3)
    opened = app.param_win is not None
    app.custom_toggle()                       # 收起
    settle(root, 4)
    ck("P11 收起自定义参数时小窗一起关", opened and app.param_win is None,
       "打开过=%s 现在=%r" % (opened, app.param_win))
    app.custom_toggle()                       # 再展开，后面还要用
    settle(root, 4)

    # ---------- P12：布局不溢出 ----------
    for geo in ("880x900", "820x900"):
        root.geometry(geo)
        settle(root, 5)
        cs = cells_of(app)
        if not cs:
            ck("P12 %s 找到参数行" % geo, False)
            continue
        grid = cs[0].master
        right = max(c.winfo_x() + c.winfo_reqwidth() for c in cs)
        ck("P12 %s 两列不溢出" % geo, grid.winfo_width() >= right
           and grid.winfo_width() >= grid.winfo_reqwidth(),
           "可用 %d / 内容需要 %d / 最右边界 %d"
           % (grid.winfo_width(), grid.winfo_reqwidth(), right))

    # ---------- P14：GUI 的 FIELDS 与注释表对应 ----------
    ck("P14 GUI 参数表与注释表 key 完全对应",
       sorted(keys) == sorted(W.PARAM_NOTES),
       "缺注释：%r  多余注释：%r"
       % (sorted(set(keys) - set(W.PARAM_NOTES)),
          sorted(set(W.PARAM_NOTES) - set(keys))))

    # ---------- P13：主窗口关闭流程 ----------
    app.open_param_note("flap_sec")
    settle(root, 3)
    try:
        app._closing = False
        app.on_close()
    except Exception as e:
        ck("P13 主窗口关闭流程不报错", False, repr(e))
    else:
        ck("P13 主窗口关闭流程不报错（小窗开着）", True)

    print()
    print("  %d pass, %d fail" % (pass_n, fail_n))
    try:
        root.destroy()
    except Exception:
        pass
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
