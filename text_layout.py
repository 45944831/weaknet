# -*- coding: utf-8 -*-
"""说明文案的中文折行排版（从 weaknet.py 抽出，行为不变）。

为什么单独成模块：这套规则和代理引擎没有关系，但却是「参数说明在电脑和
手机上长得不一样」的根因所在。抽出来之后：
  * 引擎文件不再被 260 行排版逻辑占着；
  * 排版规则可以脱离 weaknet 单独测试（tests/test_unit_text_layout.py）；
  * GUI 仍然通过 `W.wrap_note_lines` 调用（weaknet 里 re-export），
    调用方零改动。

规则本身没变：先把文字切成不可再分的原子，再按像素宽度贪心拼行。
"""

# ---------------------------------------------------------------- 说明文案的排版
#
# 为什么不能直接用 Label 的 wraplength 折行：
#   Tk 的 Label 只在「空格」处断行。中文正文里为了排版留的空格
#   （→、≈、+ 两侧）于是全都成了断点 —— 「·」条目符被单独甩到一行，
#   「「本机 → 服务器」」这种引号短语被从中间劈开。换成 U+00A0 也一样断
#   （实测行数不变），Tk 层面救不回来。
#   安卓 TextView 的断行规则又跟 Tk 不一样，同一段文字在两端会长得不同。
# 所以折行规则必须下沉到引擎层：先把文字切成「不可再分的原子」，
# 再按像素宽度贪心拼行。两端（桌面 / 安卓）共用同一套规则，
# 改了一端忘了另一端的话，同一个参数在电脑上和手机上会长得不一样。
#
# 原子有四种：
#   c  单个字符（汉字 / 中文标点 / 独立符号），可以在它前后断行
#   q  引号短语（「」「『』“”《》整段不可断）
#   w  英文单词 / 数字带单位（800ms、0.5~1.5、RTT 整段不可断）
#   s  空白（只用来标记「这里原本有空格」，断开时丢掉）
#
# 断行时还会做两件中文排版的事：
#   避头：收尾标点（，。、）」等）不另起一行 —— 把它留在上一行
#   避尾：起头标点（（「等）不留在行末 —— 把它带到下一行

_NOTE_QUOTE_PAIRS = {"「": "」", "『": "』", "“": "”", "《": "》"}

# 行首禁则：这些标点不能出现在一行的开头
_NOTE_NO_START = "，。、；：？！）」』】”’%‰°′″℃…"
# 行尾禁则：这些标点不能出现在一行的末尾
_NOTE_NO_END = "（「『【《“"

# 不能孤零零出现在行首的符号：数学 / 箭头符号
_NOTE_NO_START_GLUE = "≈→←±×÷·〜～"

# 数字 / 单词内部的连接符（0.5~1.5 里的 . ~ ，800ms% 里的 %）
_NOTE_WORD_CONNECT = ".-~_%+/"

# 视为「空白」的字符（断行时空格丢弃，行内保留一个）
_NOTE_SPACE_CHARS = " \t　"


def _is_note_cjk(ch):
    """是不是中日韩字符 / 中文标点（含全角形式）。"""
    code = ord(ch)
    return (0x2E80 <= code <= 0x9FFF) or (0xF900 <= code <= 0xFAFF) \
        or (0xFF00 <= code <= 0xFFEF)


def _is_note_word_char(text, i):
    """text[i] 能不能算「英文单词 / 数字」的一部分。

    字母数字当然算；连接符只在两边都是字母数字时才算，
    这样 0.5~1.5 是一个整体，而「上行 + 下行」里的 + 是独立符号。
    """
    ch = text[i]
    if ch.isascii() and ch.isalnum():
        return True
    if ch in _NOTE_WORD_CONNECT:
        prev_alnum = i > 0 and text[i - 1].isascii() and text[i - 1].isalnum()
        nxt = text[i + 1] if i + 1 < len(text) else ""
        next_alnum = bool(nxt) and nxt.isascii() and nxt.isalnum()
        return prev_alnum and next_alnum
    return False


def split_note_atoms(text):
    """把一段说明文字切成不可再分的排版原子。

    返回 [[kind, text], ...]，kind 取值 c / q / w / s。
    折行只能发生在原子与原子之间，绝不会把一个原子劈成两半 ——
    「「本机 → 服务器」」里的箭头和空格因此永远跟参数名待在一起。
    """
    s = str(text or "")
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in _NOTE_SPACE_CHARS:
            j = i
            while j < n and s[j] in _NOTE_SPACE_CHARS:
                j += 1
            out.append(["s", s[i:j]])
            i = j
            continue
        if ch in _NOTE_QUOTE_PAIRS:
            close = _NOTE_QUOTE_PAIRS[ch]
            j = s.find(close, i + 1)
            if j != -1:
                out.append(["q", s[i:j + 1]])
                i = j + 1
                continue
        if _is_note_cjk(ch):
            out.append(["c", ch])
            i += 1
            continue
        if ch.isascii() and ch.isalnum():
            j = i
            while j < n and _is_note_word_char(s, j):
                j += 1
            # 百分号跟在词尾（100%）也算这个词的一部分
            if j < n and s[j] == "%":
                j += 1
            out.append(["w", s[i:j]])
            i = j
            continue
        # 其它 ASCII 符号（= + % 等）单独成原子
        out.append(["c", ch])
        i += 1
    return out


def _note_group_units(atoms):
    """原子表 -> 排版单元表。

    排版单元 = 行内不可再分的一段文字。返回 [[文字, 前面有没有空格], ...]。

    这里顺手做了中文排版的两条「避头尾」规则 —— 不做的话，断行处会出现
    孤零零的标点（比如一行的开头是「，」）：
      · 行首禁则（，。、；：？！）等）不能起行 → 粘到前一个单元末尾
      · 行尾禁则（（《 等）不能收行 → 粘到后一个单元开头
    合成之后，断行只发生在单元之间，标点自然跟着前面的字走。
    """
    units = []
    sep = False             # 上一段和这一段之间有没有空格
    hold_txt = ""           # 待与下一个单元合并的起头标点
    hold_sep = False
    for kind, t in atoms:
        if kind == "s":
            sep = True
            continue
        if kind == "c" and len(t) == 1:
            ch = t
            if ch in _NOTE_NO_END:
                # 起头标点不能收在行末 —— 先攒着，跟下一个单元走
                hold_txt += ch
                hold_sep = sep
                sep = False
                continue
            if hold_txt:
                t = hold_txt + ch
                sep = hold_sep
                hold_txt = ""
                hold_sep = False
            if ch in _NOTE_NO_START or ch in _NOTE_NO_START_GLUE:
                if units:
                    # 收尾标点不能起行 —— 粘到上一个单元末尾
                    units[-1][0] += (" " if sep else "") + ch
                    sep = False
                    continue
            units.append([t, sep])
            sep = False
            continue
        # 引号短语 / 英文单词：整体不可断
        if hold_txt:
            t = hold_txt + t
            sep = hold_sep
            hold_txt = ""
            hold_sep = False
        units.append([t, sep])
        sep = False
    if hold_txt:
        units.append([hold_txt, hold_sep])
    return units



def _note_units_text(units):
    """单元表 -> 一行文字（行首不留空格，行内空格保留）。"""
    parts = []
    for idx, u in enumerate(units):
        if idx > 0 and u[1]:
            parts.append(" ")
        parts.append(u[0])
    return "".join(parts)


def _note_safe_chunks(text):
    """把一个单元切成「行内安全的最小块」。

    单元宽过整行时只能硬切，但切之前先按避头尾规则重新分块：
      · 收尾标点（，。、；：？！）等）粘到前一块末尾 —— 它不能起行
      · 起头标点（（《 等）粘到后一块开头 —— 它不能收行
    切完之后，断行只发生在块与块之间，标点自然跟着前面的字走。
    少了这一步，窄屏上就会出现「」。」这种一行开头的怪事
    （实测：引号短语被硬切后，收尾引号正好落在下一行开头）。
    """
    chunks = []
    hold = ""                     # 待与下一块合并的起头标点
    for ch in text:
        if ch in _NOTE_NO_END:
            hold += ch
            continue
        if hold:
            ch = hold + ch
            hold = ""
        # 只看末字符：拼上起头标点之后，末字符仍是原来那个
        # （安卓端 Profile.noteSafeChunks 用的是同一套判断）
        if ch[-1] in _NOTE_NO_START and chunks:
            chunks[-1] += ch
            continue
        chunks.append(ch)
    if hold:
        if chunks:
            chunks[-1] += hold
        else:
            chunks.append(hold)
    return chunks


def _note_hard_split(units, measure, max_width):
    """把宽过整行的单元按「安全块」硬切。

    超长英文词 / 超长引号短语在正常文案里不会出现，但真出现了也不能把
    版式撑破 —— 宁可切碎，也不能让文字溢出卡片。
    """
    out = []
    for text, sp in units:
        if measure(text) <= max_width:
            out.append([text, sp])
            continue
        cur = ""
        for ch in _note_safe_chunks(text):
            if cur and measure(cur + ch) > max_width:
                out.append([cur, sp])
                sp = False
                cur = ch
            else:
                cur += ch
        if cur:
            out.append([cur, sp])
    return out


def wrap_note_lines(text, measure, max_width):
    """把一段说明文字按宽度折成若干行。

    measure : callable(str) -> 数值，量一段文字有多宽
    max_width : 每行最多允许多宽

    规则（安卓端 Profile.wrapNoteLines 完全一致）：
      · 引号短语、英文单词、数字带单位整体不可拆开
      · 断行只发生在排版单元之间；行首不留空格
      · 收尾标点不另起一行，起头标点不留在行末（避头尾）
      · 单个单元宽过整行时按字符硬切，不撑破版式
    """
    units = _note_hard_split(_note_group_units(split_note_atoms(text)),
                             measure, max_width)
    if not units:
        return []
    lines = []
    cur = []
    cur_w = 0
    for text, sp in units:
        w = measure(text)
        add = (measure(" ") if (sp and cur) else 0) + w
        if cur and cur_w + add > max_width:
            lines.append(_note_units_text(cur))
            cur = []
            cur_w = 0
            add = w                 # 断行后行首不留空格
        cur.append([text, sp])
        cur_w += add
    if cur:
        lines.append(_note_units_text(cur))
    return lines
