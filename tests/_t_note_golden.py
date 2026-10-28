# -*- coding: utf-8 -*-
"""生成「说明文案折行」黄金文件（桌面端算，安卓端核对）

为什么要黄金文件
----------------
说明文案的折行规则必须两端一模一样：同一个参数在电脑上和手机上
不能被断在不同的位置。规则写在两处（weaknet.py / Profile.java），
光靠各写各的测试，两边同时改错一点点也照样全绿。

所以这里用**同一个测宽函数**（粗糙测宽：汉字 1 单位、ASCII 0.5、空格 0.25）
在桌面端算出每一行的断点，写进 note_wrap_golden.txt；
安卓端的 _NoteWrapTest 读同一个文件逐行核对。
改了一端忘了另一端，这个测试立刻红。

为什么用「粗糙测宽」而不是真实字体
----------------------------------
真实字体两端不一样（桌面 YaHei UI / 安卓 Roboto），
量出来的宽度自然不同，断点也就不可能逐行相同 ——
那就只能比「规则」，没法比「断点」。
粗糙测宽把字体差异抹平，两端用同一把尺子，断点才能对齐。
真实排版的正确性由各端的界面测试（_t_note_layout.py / 真机）保证。

用法：python _t_note_golden.py [输出路径]
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import io
import os
import sys

sys.path.insert(0, ROOT)

import weaknet as W      # noqa: E402




# 粗糙测宽的单位：与安卓端 Profile.NoteRoughMeasurer 的用法一致
UNIT = 20
# 每行宽度（单位）：与安卓端测试里用的同一个值
WIDTH = 200


def rough(text):
    """与安卓端 Profile.NoteRoughMeasurer 完全等价的测宽函数。"""
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
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "android", "_jw", "note_wrap_golden.txt")

    lines_out = []
    lines_out.append("# 说明文案折行黄金文件（由 _t_note_golden.py 生成，别手改）")
    lines_out.append("# 测宽：粗糙测宽，汉字 1 单位 / ASCII 0.5 / 空格 0.25，UNIT=%d" % UNIT)
    lines_out.append("# 行宽：%d 单位" % WIDTH)
    lines_out.append("# 格式：key<TAB>段号<TAB>行号<TAB>这一行的文字")
    lines_out.append("#")

    total = 0
    for key in sorted(W.PARAM_NOTES):
        body, tips = W.split_param_note(W.PARAM_NOTES[key])
        segs = [("body", body)] + [("tip%d" % (i + 1), t)
                                   for i, t in enumerate(tips)]
        for tag, text in segs:
            if not text:
                continue
            for i, ln in enumerate(W.wrap_note_lines(text, rough, WIDTH)):
                lines_out.append("%s\t%s\t%d\t%s" % (key, tag, i, ln))
                total += 1

    io.open(out, "w", encoding="utf-8", newline="\n").write(
        "\n".join(lines_out) + "\n")
    print("已生成 %s" % out)
    print("  %d 个参数 / %d 行折行" % (len(W.PARAM_NOTES), total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
