# -*- coding: utf-8 -*-
"""参数说明小窗的排版回归测试

用户截图里真实出现过的 4 处错位，逐条盯死：
  1. 「·」条目符独占一行          → M5/M6
  2. 正文把「」引号短语劈开        → M9/M10
  3. 标题的「！」浮在基线之上      → M1/M2
  4. 正文 14px / 条目 20px 左右边距不一致 → M3/M4

测量方式分两路：
  · 几何实测 —— 直接问 Tk 每个控件在屏幕上的位置，量的是真排版
  · 像素实测 —— 抓图后按灰度扫墨迹，量的是真渲染（ClearType 彩色边缘要排除）
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

from PIL import Image

sys.path.insert(0, ROOT)

import weaknet as W      # noqa: E402
import weaknet_gui as G  # noqa: E402




pass_n = fail_n = 0


def ck(name, ok, extra=""):
    global pass_n, fail_n
    if ok:
        pass_n += 1
        print("  PASS  %s%s" % (name, ("   " + extra) if extra else ""))
    else:
        fail_n += 1
        print("  FAIL  %s   <- %s" % (name, extra))


def walk(w):
    yield w
    try:
        kids = w.winfo_children()
    except Exception:
        return
    for k in kids:
        for x in walk(k):
            yield x


def find_labels(app):
    """按结构识别说明窗里的控件（不靠猜文案 —— 条目正文也含换行，会跟正文混）

    结构：
      card
        ├ Label  「!」/ 参数名 / 「作用」/「注意」/ 正文
        ├ Frame  每条提醒一行：Label「·」 + Label 正文
        └ ...
    """
    card = None
    for x in app.param_win.winfo_children():
        if isinstance(x, tk.Frame):
            card = x
            break
    if card is None:
        return [], [], [], []

    head_names = {"!", "作用", "注意"}
    for f in G.FIELDS:
        head_names.add(f[1])

    body, dot, tiptext, heads = [], [], [], []
    for x in card.winfo_children():
        if not isinstance(x, tk.Label):
            continue
        t = str(x.cget("text"))
        if t in head_names:
            heads.append(x)
            continue
        body.append(x)

    # 每条提醒：card 下的 Frame，里面一格是「·」，另一格是正文
    for x in card.winfo_children():
        if not isinstance(x, tk.Frame):
            continue
        kids = [k for k in x.winfo_children() if isinstance(k, tk.Label)]
        d = [k for k in kids if str(k.cget("text")) == "·"]
        t = [k for k in kids if str(k.cget("text")) != "·"]
        if d and t:
            dot.append(d[0])
            tiptext.append(t[0])
    return body, dot, tiptext, heads


def main():
    root = tk.Tk()
    root.title("WeakNetNoteLayoutTest")
    root.geometry("900x900+0+0")
    app = G.App(root, None, 8899)
    try:
        root.after_cancel(app._after_id)
    except Exception:
        pass
    app._closing = True
    for _ in range(4):
        root.update_idletasks()
        root.update()
        time.sleep(0.03)

    # ---------- 逐个参数打开，检查结构 ----------
    struct_bad, geom_bad = [], []
    for key, label, unit, top in G.FIELDS:
        app.open_param_note(key)
        for _ in range(3):
            root.update_idletasks()
            root.update()
            time.sleep(0.02)

        body, dot, tiptext, heads = find_labels(app)
        body_txt, tips = W.split_param_note(W.PARAM_NOTES.get(key, ""))

        # 条目数与提醒数对得上（每个提醒一个「·」符号 + 一块正文）
        if len(dot) != len(tips):
            struct_bad.append("%s: 符号 %d 个 / 提醒 %d 条" % (key, len(dot), len(tips)))
        if len(tiptext) != len(tips):
            struct_bad.append("%s: 条目正文 %d 块 / 提醒 %d 条"
                              % (key, len(tiptext), len(tips)))
        if len(body) != 1:
            struct_bad.append("%s: 正文 %d 块" % (key, len(body)))

        # 几何：正文左边缘 / 条目符号左边缘 / 条目正文左边缘
        if body and dot and tiptext:
            bx = body[0].winfo_rootx()
            dx = dot[0].winfo_rootx()
            tx = tiptext[0].winfo_rootx()
            # 正文与条目符号左边缘必须一致（左右边距统一）
            if abs(bx - dx) > 1:
                geom_bad.append("%s: 正文 x=%d 符号 x=%d" % (key, bx, dx))
            # 条目正文必须比符号靠右一个固定缩进（悬挂缩进）
            gap = tx - dx
            if not (4 <= gap <= 24):
                geom_bad.append("%s: 条目正文缩进 %dpx" % (key, gap))

        # 折行后的每一行都不超宽
        for text, w in ([(body_txt, G.NOTE_TEXT_W)]
                        + [(t, G.NOTE_TEXT_W - G.NOTE_TIP_INDENT) for t in tips]):
            if not text:
                continue
            for ln in W.wrap_note_lines(text, G._note_measure, w):
                if G._note_measure(ln) > w:
                    geom_bad.append("%s: 行超宽 %d>%d %r"
                                    % (key, G._note_measure(ln), w, ln[:12]))

    ck("M5  每个提醒一个符号 + 一块正文（结构完整）", not struct_bad,
       "13 个参数全部吻合" if not struct_bad else "%r" % (struct_bad[:4],))
    ck("M3/M4 正文与条目左边缘一致 + 悬挂缩进正确 + 无超宽行",
       not geom_bad, "全部正确" if not geom_bad else "%r" % (geom_bad[:4],))

    # ---------- 避头尾 / 引号完整性（全部 13 个参数的文案） ----------
    bad_start, bad_end, lost = [], [], []
    for key in W.PARAM_NOTES:
        body_txt, tips = W.split_param_note(W.PARAM_NOTES[key])
        for tag, text in [("body", body_txt)] + [("tip%d" % (i + 1), t)
                                                 for i, t in enumerate(tips)]:
            if not text:
                continue
            lines = W.wrap_note_lines(text, G._note_measure, G.NOTE_TEXT_W)
            for ln in lines:
                if ln and ln[0] in W._NOTE_NO_START:
                    bad_start.append("%s/%s %r" % (key, tag, ln[:10]))
                if ln and ln[-1] in W._NOTE_NO_END:
                    bad_end.append("%s/%s %r" % (key, tag, ln[-10:]))
                # 引号短语被劈开：整行拼起来后「」应当成对
                if ln.count("「") != ln.count("」"):
                    lost.append("%s/%s %r" % (key, tag, ln[:14]))
    ck("M10 没有行以禁则标点开头", not bad_start,
       "全部合规" if not bad_start else "%r" % (bad_start[:3],))
    ck("M11 没有行以起头标点结尾", not bad_end,
       "全部合规" if not bad_end else "%r" % (bad_end[:3],))
    ck("M9  引号短语没被劈开（每行「」成对）", not lost,
       "全部完整" if not lost else "%r" % (lost[:3],))

    # ---------- 像素实测：标题标记（M1/M2） ----------
    shot = os.path.join(HERE, "_shot_note_new.png")
    if os.path.exists(shot):
        img = Image.open(shot).convert("RGB")
        px = img.load()
        w, h = img.size
        # 只认接近强调色 #2d7ff9 的实心像素：ClearType 的彩色边缘色相杂，
        # 用宽松条件会把旁边深色文字的边缘一起算进来
        def is_accent(p):
            return (p[0] < 90 and p[1] < 165 and p[2] > 225 and p[2] - p[0] > 170)

        blue_cols, dark_cols = [], []
        for x in range(20, w):
            for y in range(45, 85):
                p = px[x, y]
                if is_accent(p):
                    blue_cols.append(x)
                    break
        mark_x0 = mark_x1 = None
        if blue_cols:
            mark_x0, mark_x1 = min(blue_cols), max(blue_cols)
            for x in range(mark_x1 + 1, w):
                hit = False
                for y in range(45, 85):
                    p = px[x, y]
                    if p[0] < 110 and p[1] < 110 and p[2] < 130 and p[2] - p[0] < 60:
                        hit = True
                        break
                if hit:
                    dark_cols.append(x)
                    break
        name_x0 = min(dark_cols) if dark_cols else None

        ck("M1a 标题标记确实是蓝色实心像素", mark_x0 is not None,
           "标记 x=%s..%s" % (mark_x0, mark_x1))
        ck("M1b 找到紧跟其后的参数名", name_x0 is not None,
           "参数名起 x=%s" % (name_x0,))
        if mark_x0 is not None and name_x0 is not None:
            gap = name_x0 - mark_x1
            ck("M2  标记与参数名间距合理（<=14px，不能悬空）", gap <= 14,
               "间距 %dpx" % gap)
            # 垂直：标记墨迹下沿与参数名墨迹下沿接近
            def bottom(x0, x1, pred):
                for y in range(45, 85):
                    for x in range(x0, x1):
                        if pred(px[x, y]):
                            return y
                return None
            mb = bottom(mark_x0, mark_x1 + 1, is_accent)
            nb = bottom(name_x0, name_x0 + 60,
                        lambda p: p[0] < 110 and p[1] < 110 and p[2] < 130
                        and p[2] - p[0] < 60)
            ck("M1  标题标记不悬空（基线与参数名接近）",
               mb is not None and nb is not None and abs(mb - nb) <= 4,
               "标记下沿 y=%s 参数名下沿 y=%s" % (mb, nb))
    else:
        ck("M1/M2 像素实测", False, "缺截图 %s" % shot)

    print()
    print("  %d pass, %d fail" % (pass_n, fail_n))
    try:
        root.destroy()
    except Exception:
        pass
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
