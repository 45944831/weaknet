# -*- coding: utf-8 -*-
"""说明文案折行规则的引擎层单测（不需要界面）

覆盖 weaknet.py 里新加的排版算法：
  · 原子切分：引号短语、英文单词 / 数字带单位整体不可断
  · 避头尾：收尾标点不落行首，起头标点不留在行末
  · 贪心拼行：行首不留空格，任何一行都不超宽
  · 超宽单元按「安全块」硬切，不撑破版式，也不产生起行标点

与安卓端的对应关系：算法逐条对应 Profile.java 的同名函数，
断点逐行对应 _t_note_golden.py 生成的黄金文件
（安卓端由 _NoteWrapTest 的 P12 逐行核对）。
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




PASS = FAIL = 0


def ck(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  %s%s" % (name, ("   " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  FAIL  %s   <- %s" % (name, extra))


def rough(text):
    """与安卓端 Profile.NoteRoughMeasurer 等价的测宽函数。

    真实字体两端不一样（桌面 YaHei UI / 安卓 Roboto），
    断点本来就不可能逐行相同 —— 那是字体差异，不是规则差异。
    规则层面的一致性靠两端跑同一套规则 + 同一把「尺子」来验证。
    """
    if not text:
        return 0
    unit = 20
    w = 0
    for ch in text:
        if ch == " ":
            w += unit // 4
        elif W._is_note_cjk(ch):
            w += unit
        else:
            w += unit // 2
    return w


def main():
    # ---------- N1 原子切分 ----------
    atoms = W.split_note_atoms("给「本机 → 服务器」方向")
    kinds = "".join(k for k, _ in atoms)
    ck("N1a 引号短语整体是一个 q 原子", kinds == "cqcc",
       "kind=%s text=%r" % (kinds, [t for _, t in atoms]))

    atoms = W.split_note_atoms("填 800ms 就行")
    kinds = "".join(k for k, _ in atoms)
    ck("N1b 数字带单位是一个 w 原子", kinds == "cswscc",
       "kind=%s text=%r" % (kinds, [t for _, t in atoms]))

    atoms = W.split_note_atoms("0.5~1.5 秒")
    ck("N1c 小数与波浪号连成一个词",
       any(t == "0.5~1.5" for _, t in atoms),
       repr([t for _, t in atoms]))

    # ---------- N2 避头尾 ----------
    text = "一次往返 ≈ 上行 + 下行，所以两项都填 400 时 RTT 是 800ms。"
    lines = W.wrap_note_lines(text, rough, 200)
    bad_start = [ln for ln in lines if ln and ln[0] in W._NOTE_NO_START]
    bad_end = [ln for ln in lines if ln and ln[-1] in W._NOTE_NO_END]
    ck("N2a 没有行以禁则标点开头", not bad_start, repr(bad_start[:3]))
    ck("N2b 没有行以起头标点结尾", not bad_end, repr(bad_end[:3]))

    # ---------- N3 行首不留空格 / 不超宽 ----------
    all_lines = []
    for key in W.PARAM_NOTES:
        body, tips = W.split_param_note(W.PARAM_NOTES[key])
        for seg in [body] + tips:
            if not seg:
                continue
            all_lines.extend(W.wrap_note_lines(seg, rough, 200))
    lead = [ln for ln in all_lines if ln.startswith(" ")]
    over = [ln for ln in all_lines if rough(ln) > 200]
    ck("N3a 断行后行首不留空格", not lead, "%d 行有空格" % len(lead))
    ck("N3b 没有任何一行超宽", not over, "%d 行超宽" % len(over))

    # ---------- N4 折行后一个字都不丢 ----------
    lost = []
    for key in W.PARAM_NOTES:
        body, tips = W.split_param_note(W.PARAM_NOTES[key])
        for tag, seg in [("body", body)] + [("tip%d" % (i + 1), t)
                                            for i, t in enumerate(tips)]:
            if not seg:
                continue
            joined = "".join(W.wrap_note_lines(seg, rough, 200))
            if joined.replace(" ", "") != seg.replace(" ", ""):
                lost.append("%s/%s" % (key, tag))
    ck("N4a 折行后文字完整（无丢字）", not lost,
       "13 个参数全部完整" if not lost else repr(lost[:5]))

    # ---------- N5 超宽单元硬切 ----------
    lines = W.wrap_note_lines("A" * 200, rough, 100)
    ck("N5a 超长单词被硬切且不超宽",
       len(lines) > 1 and all(rough(ln) <= 100 for ln in lines),
       "%d 行" % len(lines))

    # 超宽的引号短语：切点不能落在引号上（不能出现起行标点）
    q = "「" + "字" * 40 + "」"
    lines = W.wrap_note_lines(q, rough, 100)
    bad = [ln for ln in lines if ln and ln[0] in W._NOTE_NO_START]
    ck("N5b 超宽引号短语硬切后不产生起行标点", not bad,
       repr(bad[:3]))
    ck("N5c 超宽引号短语硬切后文字完整",
       "".join(lines).replace(" ", "") == q.replace(" ", ""),
       "%d 行" % len(lines))

    # ---------- N6 空输入不炸 ----------
    ck("N6a 空文本返回空列表", W.wrap_note_lines("", rough, 200) == [])
    ck("N6b None 返回空列表", W.wrap_note_lines(None, rough, 200) == [])

    # ---------- N7 放得下的引号短语绝不能被劈开 ----------
    split = []
    for key in W.PARAM_NOTES:
        body, tips = W.split_param_note(W.PARAM_NOTES[key])
        for tag, seg in [("body", body)] + [("tip%d" % (i + 1), t)
                                            for i, t in enumerate(tips)]:
            if not seg:
                continue
            for ln in W.wrap_note_lines(seg, rough, 200):
                if ln.count("「") != ln.count("」"):
                    # 只有当整句放得下这一行时才允许拆；这里用宽行测
                    continue
            wide = W.wrap_note_lines(seg, rough, 400)
            for ln in wide:
                if ln.count("「") != ln.count("」"):
                    split.append("%s/%s %r" % (key, tag, ln))
    ck("N7a 行宽足够时引号短语不会被劈开", not split,
       "全部完整" if not split else repr(split[:3]))

    print()
    print("  %d pass, %d fail" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
