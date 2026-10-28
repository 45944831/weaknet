# -*- coding: utf-8 -*-
"""参数注释表的一致性校验（纯文本 / 纯数据，不需要 tkinter、不需要真机）

需求背景
--------
参数编辑界面有 13 行参数，每行末尾一个「！」。用户点开看到的就是这张注释表 ——
漏一个参数、文案重复、两端文案不一致，表现都是「点开一片空白」或者
「电脑上说是挂住，手机上说是立刻失败」。

两端各存一份文案（weaknet.py 的 PARAM_NOTES / Profile.java 的 PARAM_NOTES），
没法共享代码，只能靠这里逐条比对把它们钉在一起。

本测试覆盖
----------
A  Python 侧数据：覆盖完整、长度够、无重复、分段、语义关键词
B  Java 侧数据：能从源码解析出 13 条，且与 Python 逐条一字不差
C  界面接线：安卓参数行（MainPanels.FIELDS）与注释表的 key 完全对应
D  预设注释里被顺手修正的一处数字（卫星链路的往返时间）
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import io
import os
import re
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


def read(path):
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


# ---------------------------------------------------------------- Java 源码解析

JAVA_PUT = re.compile(
    r'PARAM_NOTES\.put\(\s*"([A-Za-z_]+)"\s*,\s*((?:"(?:[^"\\]|\\.)*"\s*\+?\s*)+)\)\s*;')
JAVA_LIT = re.compile(r'"((?:[^"\\]|\\.)*)"')

JAVA_UNESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'"}


def java_unescape(s):
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append(JAVA_UNESC.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_java_param_notes(src):
    """从 Profile.java 抽出 {key: 文案}。

    只认「一个 put 一个 key、文案由若干字符串字面量用 + 拼接」这种写法 ——
    写成别的形式（循环拼、常量引用）这里会解析不到，测试直接失败，
    比悄悄放过一条不一致的文案要好。
    """
    notes = {}
    for m in JAVA_PUT.finditer(src):
        key = m.group(1)
        lits = JAVA_LIT.findall(m.group(2))
        notes[key] = java_unescape("".join(lits))
    return notes


def parse_java_fields(src):
    """从 MainPanels.java 抽出参数行定义 [key, 显示名, 默认值]"""
    m = re.search(r"FIELDS\s*=\s*\{(.*?)\n\s*\};", src, re.S)
    if not m:
        return []
    rows = re.findall(r'\{\s*"([A-Za-z_]+)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\}', m.group(1))
    return [list(r) for r in rows]


def strip_java_comments(src):
    """去掉 // 与 /* */ 注释。

    校验「代码里有没有调用某个方法」必须先剥注释：ParamSheet 的类注释里
    专门写了「不要在这里调 suspendFloat / restoreFloat」，直接搜字符串
    会把这段说明当成调用，测试自己骗自己。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def main():
    py_notes = W.PARAM_NOTES
    limits = W.PROF_LIMITS

    # ================= A：Python 侧数据 =================
    missing = [k for k in limits if k not in py_notes]
    extra = [k for k in py_notes if k not in limits]
    ck("A1 注释表覆盖全部参数（与 PROF_LIMITS 同集合）",
       not missing and not extra,
       "%d 条%s%s" % (len(py_notes),
                      "" if not missing else "  缺：" + ",".join(missing),
                      "" if not extra else "  多：" + ",".join(extra)))

    brief = [(k, len(v)) for k, v in py_notes.items() if len(v) < 30]
    ck("A2 每条注释都够说明问题（>=30 字）", not brief,
       "最短 %d 字" % min(len(v) for v in py_notes.values()) if not brief
       else "过短：%r" % (brief,))

    dup = [k for k, v in py_notes.items()
           if list(py_notes.values()).count(v) > 1]
    ck("A3 注释文案互不重复", not dup, "全部唯一" if not dup else "重复：%r" % (dup,))

    single = [k for k, v in py_notes.items() if "\n" not in v]
    ck("A4 都分成两段（作用 / 注意）", not single,
       "全部两段" if not single else "单段：%r" % (single,))

    dirty = [k for k, v in py_notes.items()
             if "%%" in v or "null" in v or "\\n" in v]
    ck("A5 无占位符与转义残留", not dirty,
       "干净" if not dirty else "异常：%r" % (dirty,))

    # 语义关键词：写错参数（复制粘贴没改）会在这里露出来
    sem = [
        ("A6a 卡顿时长提到「丢包」", "stall_ms", "丢包"),
        ("A6b 丢包率指向「卡顿时长」", "loss_pct", "卡顿时长"),
        ("A6c 断连率强调「重连」", "disconnect_pct", "重连"),
        ("A6d 黑洞率强调「挂住」", "timeout_pct", "挂住"),
        ("A6e 拒连率强调「立刻」", "reject_pct", "立刻"),
        ("A6f DNS 提到 IP 直连不受影响", "dns_fail_pct", "IP"),
        ("A6g 断网时长只在间隔 > 0 时生效", "flap_blackout_sec", "断网间隔"),
        ("A6h 抖动说明是 ± 浮动", "jitter_ms", "±"),
        ("A6i 延迟说明往返 = 上行 + 下行", "latency_up_ms", "往返"),
        ("A6j 下行带宽说明 0 = 不限", "down_kbps", "0 = 不限"),
        ("A6k 上行带宽说明 0 = 不限", "up_kbps", "0 = 不限"),
    ]
    for name, key, word in sem:
        ck(name, word in py_notes.get(key, ""), py_notes.get(key, "")[:40])

    # ================= B：Java 侧与 Python 一致 =================
    prof_src = read(os.path.join(ROOT, "android/src/com/weaknet/app/Profile.java"))
    j_notes = parse_java_param_notes(prof_src)
    ck("B1 能从 Profile.java 解析出 13 条注释", len(j_notes) == len(py_notes),
       "解析到 %d 条 / 期望 %d 条" % (len(j_notes), len(py_notes)))

    only_py = sorted(set(py_notes) - set(j_notes))
    only_j = sorted(set(j_notes) - set(py_notes))
    ck("B2 两端 key 集合一致", not only_py and not only_j,
       "一致" if not only_py and not only_j
       else "只在 Python：%r  只在 Java：%r" % (only_py, only_j))

    diff = []
    for k in py_notes:
        if k in j_notes and j_notes[k] != py_notes[k]:
            diff.append(k)
    ck("B3 两端文案逐条一字不差", not diff,
       "%d 条全部一致" % len(py_notes) if not diff else "不一致：%r" % (diff,))

    ck("B4 Profile 暴露了 noteOfParam 且对未知 key 返回空串",
       "public static String noteOfParam(String key)" in prof_src
       and "return s == null ? \"\" : s;" in prof_src)

    # ================= C：安卓参数行与注释表对应 =================
    mp_src = read(os.path.join(ROOT, "android/src/com/weaknet/app/MainPanels.java"))
    fields = parse_java_fields(mp_src)
    fkeys = [r[0] for r in fields]
    ck("C1 解析出 13 行参数定义", len(fields) == 13, "%d 行" % len(fields))
    ck("C2 参数行的 key 与注释表完全对应",
       set(fkeys) == set(py_notes) and len(set(fkeys)) == len(fkeys),
       "一致" if set(fkeys) == set(py_notes) else
       "只在界面：%r  只在注释：%r"
       % (sorted(set(fkeys) - set(py_notes)), sorted(set(py_notes) - set(fkeys))))

    ck("C3 参数行确实接了「！」入口",
       mp_src.count("openParamNote(") >= 2 and 'setText("！")' in mp_src,
       "openParamNote 出现 %d 次" % mp_src.count("openParamNote("))

    # 「！」要排在参数名之后、输入框之前 —— 挂在输入框前面会被当成输入框的前缀，
    # 用户反馈过一次（第一版就是那样），所以把顺序钉死。
    i_lb = mp_src.find("row.addView(lb")
    i_help = mp_src.find("row.addView(help")
    i_sp = mp_src.find("row.addView(spacer")
    i_ed = mp_src.find("row.addView(ed")
    ck("C3b 「！」排在参数名之后、输入框之前",
       -1 < i_lb < i_help < i_sp < i_ed,
       "lb=%d help=%d spacer=%d ed=%d" % (i_lb, i_help, i_sp, i_ed))

    # 名字不能被 weight=1 撑满：撑满后「！」会被顶到最右边，又变回「输入框前缀」
    ck("C3c 参数名不再用 weight 撑满整行",
       not re.search(r"row\.addView\(lb,\s*new LinearLayout\.LayoutParams\(\s*0,", mp_src))

    # 不显眼：不加背景框、只用灰字（用户要求「！号不用那么明显」）
    ck("C3d 「！」不显眼（无背景框 + 灰字）",
       "help.setBackground" not in mp_src and "0xFF9AA4B2" in mp_src)

    ck("C4 参数行都有显示名（浮层标题用）",
       all(r[1].strip() for r in fields),
       ", ".join(r[1] for r in fields))

    ps_src = read(os.path.join(ROOT, "android/src/com/weaknet/app/ParamSheet.java"))
    ck("C5 ParamSheet 文案取自 Profile.noteOfParam",
       "Profile.noteOfParam(" in ps_src)
    # 参数说明浮层叠在参数编辑浮层上，这时飘窗已被编辑浮层收起；
    # 这里再 restoreFloat 会把飘窗放回来盖住编辑浮层（suspendFloat 不支持嵌套）。
    # 注释里专门写了这条禁令，所以要剥掉注释再看代码。
    ps_code = strip_java_comments(ps_src)
    ck("C6 ParamSheet 不碰飘窗的 suspend/restore",
       "suspendFloat" not in ps_code and "restoreFloat" not in ps_code,
       "干净" if "restoreFloat" not in ps_code else "仍在调用")

    # ================= D：预设注释里修正过的一处数字 =================
    # 卫星链路 上行 700 / 下行 700，一次往返其实是 1.4s，原文写「往返 700ms」是错的
    ck("D1 卫星链路注释不再写成「往返 700ms」",
       "往返 700ms" not in W.PRESET_NOTES.get("satellite", "")
       and "1.4" in W.PRESET_NOTES.get("satellite", ""),
       W.PRESET_NOTES.get("satellite", "")[:40])
    ck("D2 安卓端同一处也改了",
       "往返 700ms" not in prof_src
       and "单程 700ms（一次往返约 1.4 秒）" in prof_src)

    print()
    print("  %d pass, %d fail" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
