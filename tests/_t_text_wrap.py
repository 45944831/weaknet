# -*- coding: utf-8 -*-
"""Tk Text 控件会不会把「·」甩到单独一行（组合说明页用 Text，不是 Label）

Text 也有 wrap="word"，断行点跟 Label 一样是空格。
参数说明页已经把「·」拆成独立控件绕开了这个问题，
这里量一下组合说明页（还是 Text + "· " + tip）会不会踩同一个坑。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import os
import sys
import tkinter as tk
from tkinter import font as tkfont

sys.path.insert(0, ROOT)

import weaknet as W      # noqa: E402





def check(px, tips):
    root = tk.Tk()
    root.withdraw()
    f = tkfont.Font(family="Microsoft YaHei UI", size=9)
    txt = tk.Text(root, font=f, width=1, wrap="word")
    txt.pack()
    txt.configure(width=int(px / f.measure("0")))
    for tip in tips:
        txt.insert("end", "· " + tip + "\n")
    root.update_idletasks()
    alone = []
    idx = 1
    while idx < 400:
        s = txt.get("%d.0" % idx, "%d.0 lineend" % idx)
        if s.strip() == "·":
            alone.append(idx)
        idx += 1
    root.destroy()
    return alone


def main():
    tips = W.combo_doc_parts()[2]
    for px in (420, 300, 240):
        alone = check(px, tips)
        print("正文区 %dpx：条目 %d 条，「·」独占一行的有 %d 处 %r"
              % (px, len(tips), len(alone), alone[:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
