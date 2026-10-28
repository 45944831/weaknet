#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""弱网组合说明文案的两端一致性校验（纯文本 / 纯数据，不需要 tkinter、不需要真机）

需求背景
--------
组合说明（组合区的「！」）在 Windows 与 APK 各有一份界面，但文案只能有一份 ——
两边各写必然漂移：改了一端忘了另一端，同一个功能在电脑上和在手机上
说出来的话就不一样了，用户会以为这是两个功能。

文案位置：
  Python 侧  weaknet.py 的 COMBO_INTRO / COMBO_EXAMPLES / COMBO_TIPS
  Java  侧  Profile.java 的同名三个常量

本测试覆盖
----------
A  Python 侧数据：结构完整、例子够多、字段齐全、无空串
B  Java 侧数据：能从源码解析出三份，且与 Python 逐条一字不差
C  两端覆盖的语义关键词都在（防止文案被改到丢信息）
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
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


def read(path):
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


# ---------------------------------------------------------------- Java 源码解析


def java_unescape(s):
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == "u" and i + 5 < len(s):
                # \uXXXX：Java 的 unicode 转义（注意它在词法分析阶段就生效，
                # 写成 "\u001f" 与实际字符等价）
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"',
                        "\\": "\\", "'": "'"}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_java_string(src, const_name):
    """从 Java 源码里取一个 String 常量的字面量内容。

    只认 `public static final String NAME = "..." + "...";` 这种写法 ——
    写成别的形式（拼接调用、常量引用）这里会解析不到，测试直接失败，
    比悄悄放过一条不一致的文案要好。
    """
    m = re.search(
        r'public\s+static\s+final\s+String\s+' + re.escape(const_name)
        + r'\s*=\s*((?:"(?:[^"\\]|\\.)*"\s*\+\s*)*"(?:[^"\\]|\\.)*")\s*;',
        src, re.S)
    if not m:
        return None
    lits = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
    return java_unescape("".join(lits))


def parse_java_string_array(src, const_name):
    """取一个 String 常量的字面量内容（用于合并后的 COMBO_DOC）。

    组合说明的三块内容（机制 / 例子 / 提醒）在两端各存一份大字符串，
    用固定分隔符切开 —— 这样解析只需处理「一个 String 常量」，
    不用去数嵌套的数组括号（那层解析在多行拼接的字符串上极易出错，
    错误的表现是「两边看着一样但测试说不一样」，比没有测试更坏）。
    """
    return parse_java_string(src, const_name)


def parse_java_blocks(text, sep):
    """按分隔符把大字符串切成若干块"""
    if text is None:
        return None
    return text.split(sep)



def main():
    intro = W.COMBO_INTRO
    examples = W.COMBO_EXAMPLES
    tips = W.COMBO_TIPS

    # ================= A：Python 侧数据 =================
    ck("A1 组合说明有内容", len(intro) > 50, "%d 字" % len(intro))
    ck("A2 说明了怎么播（顺序 / 循环 / 上限）",
       all(k in intro for k in ("顺序播放", "可循环", "最多 5 段")),
       "")
    ck("A3 说清了不能嵌套组合", "不能再套组合" in intro, "")
    ck("A4 说清了未开启时不会偷偷限速", "不会替你偷偷开始限速" in intro, "")
    ck("A5 至少给了 3 个例子", len(examples) >= 3, "%d 个" % len(examples))
    ck("A6 每个例子四个字段齐全",
       all(len(e) == 4 and all(isinstance(v, str) and v.strip()
                               for v in e.values()) for e in examples),
       "")
    ck("A7 每个例子都有「怎么排」", all(e.get("steps") for e in examples), "")
    ck("A8 每个例子都有效果说明", all(e.get("effect") for e in examples), "")
    ck("A9 例子名字互不重复",
       len({e["name"] for e in examples}) == len(examples), "")
    ck("A10 至少两条提醒", len(tips) >= 2, "%d 条" % len(tips))
    ck("A11 提醒里提到暂停", any("暂停" in t for t in tips), "")

    # ================= B：Java 侧与 Python 一致 =================
    prof_src = read(os.path.join(ROOT, "android/src/com/weaknet/app/Profile.java"))
    j_doc = parse_java_string(prof_src, "COMBO_DOC")
    ck("B1 能从 Profile.java 解析出 COMBO_DOC", j_doc is not None, "")
    if j_doc is not None:
        ck("B2 两端文档串逐字一致", j_doc == W.COMBO_DOC,
           "一致" if j_doc == W.COMBO_DOC else "不一致")

    # 两端按同样的规则拆开，拆出来也要一致
    j_sep = parse_java_string(prof_src, "COMBO_DOC_SEP")
    ck("B3 两端分隔符一致", j_sep == W.COMBO_DOC_SEP,
       "一致" if j_sep == W.COMBO_DOC_SEP else "不一致")
    if j_doc is not None and j_sep:
        j_blocks = j_doc.split(j_sep)
        py_blocks = W.COMBO_DOC.split(W.COMBO_DOC_SEP)
        ck("B4 两端块数一致", len(j_blocks) == len(py_blocks),
           "%d vs %d" % (len(j_blocks), len(py_blocks)))
        ck("B5 每块逐字一致", j_blocks == py_blocks,
           "全部一致" if j_blocks == py_blocks else "有差异")

    # ================= C：语义关键词 =================
    sem = [
        ("C1 例子覆盖「弱覆盖 → 断网」链路", "地铁进隧道"),
        ("C2 例子提到重试 / 降级", "降级"),
        ("C3 例子提到实时音视频这类延迟敏感场景", "音视频"),
        ("C4 例子提到长连接重连", "重连"),
        ("C5 例子提到进度条 / 分片下载", "进度条"),
    ]
    alltext = W.COMBO_DOC
    for name, word in sem:
        ck(name, word in alltext, "")

    print()
    print("%d 项通过 / %d 项失败" % (PASS, FAIL))
    print("全部通过 ✅" if FAIL == 0 else "有失败项 ❌")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
