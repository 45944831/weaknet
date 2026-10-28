# -*- coding: utf-8 -*-
"""用「粗糙测宽」（汉字 1 单位 / ASCII 0.5 / 空格 0.25）复现安卓端的折行行为

安卓端 _NoteWrapTest 用的是同一个粗糙测宽函数（unit=20）。
把这个函数搬到 Python 端，两端就可以在「同样的宽度」下逐行比对，
不用真机也能保证排版规则一致。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import os
import sys

sys.path.insert(0, ROOT)

import weaknet as W      # noqa: E402




UNIT = 20


def rough(text):
    if not text:
        return 0
    w = 0
    for ch in text:
        if ch == " ":
            w += UNIT // 4
        elif W._is_note_cjk(ch):
            w += UNIT
        else:
            w += UNIT // 2
    return w


def main():
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    bad_start, bad_end, split_q = [], [], []
    for key in W.PARAM_NOTES:
        body, tips = W.split_param_note(W.PARAM_NOTES[key])
        for tag, text in [("body", body)] + [("tip%d" % (i + 1), t)
                                             for i, t in enumerate(tips)]:
            if not text:
                continue
            for ln in W.wrap_note_lines(text, rough, width):
                if not ln:
                    continue
                if ln[0] in W._NOTE_NO_START:
                    bad_start.append("%s/%s %r" % (key, tag, ln))
                if ln[-1] in W._NOTE_NO_END:
                    bad_end.append("%s/%s %r" % (key, tag, ln))
                if ln.count("「") != ln.count("」"):
                    split_q.append("%s/%s %r" % (key, tag, ln))
    print("width=%d" % width)
    print("  起行标点 %d 处" % len(bad_start))
    for x in bad_start[:6]:
        print("    " + x)
    print("  收行标点 %d 处" % len(bad_end))
    for x in bad_end[:6]:
        print("    " + x)
    print("  引号被拆 %d 处" % len(split_q))
    for x in split_q[:6]:
        print("    " + x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
