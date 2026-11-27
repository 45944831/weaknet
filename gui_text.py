# -*- coding: utf-8 -*-
"""GUI 纯文本与排版层。

从 weaknet_gui.py 抽出的与窗口状态无关的部分：说明小窗的像素常量、
参数摘要/详情文案、带宽字节格式化、名字输入限长。抽出来不是为了少几行，
而是让这些纯逻辑可以被 pytest 直接覆盖（见 tests/test_gui_text.py），
不必起 Tk / 不必有显示器。

约束：这里不许引用 App 实例、不许碰 tk 组件树；唯一外部依赖是 weaknet
的规格常量（PRESET_SHORT / PARAM_SPECS / NAME_MAX）与字体测量。
"""
from tkinter import font as tkfont

import weaknet as W

# ---------------------------------------------------------------- 参数说明小窗排版
#
# 说明窗的文字折行不在 Tk 里做，交给引擎层 weaknet.wrap_note_lines：
# Tk 的 Label 只按「空格」断行，中文正文里为排版留的空格（→、≈、+ 两侧）
# 全成了断点 —— 于是「·」条目符被甩到单独一行，「「本机 → 服务器」」
# 这种引号短语被从中间劈开。折行下沉到引擎层后，两端（桌面 / 安卓）
# 用同一套规则，同一个参数在电脑上和手机上不会长得不一样。
#
# 宽度（像素）：
NOTE_PAD = 14            # 卡片内左右留白
NOTE_TEXT_W = 330        # 正文一行最多多宽
NOTE_TIP_INDENT = 12     # 「·」条目符占的宽度：符号停在左边，正文悬挂缩进
NOTE_WIN_W = 380         # 说明窗固定宽度（正文区 330 + 左右留白 28 + 边框 + 窗口留白）

# 引擎层折行需要知道「一段文字有多宽」，这里用 Tk 字体实测。
# 不自己按字数估：中文一个字 13px、英文一个字母 7px、箭头 13px，
# 估出来的宽度跟真实差几十像素，断行位置就全错了。
_NOTE_FONT = None


def _note_measure(text):
    """量一段文字的像素宽（供 weaknet.wrap_note_lines 折行用）。"""
    global _NOTE_FONT
    if _NOTE_FONT is None:
        _NOTE_FONT = tkfont.Font(family="Microsoft YaHei UI", size=10)
    return _NOTE_FONT.measure(text)

# GUI 里的短名称（控制接口/报告仍用全名）：与 Android / 规格同源
SHORT = W.PRESET_SHORT


def _cap_name_var(var):
    """自定义场景 / 弱网组合的名字输入框统一限 10 个字符。

    用 StringVar 的 write trace 而不是 Entry 的 validate：中文输入法组字过程中
    validate 容易把候选串误判成超限、导致打不出字；trace 里就地截断对键盘、
    粘贴、输入法上屏、代码 set() 一视同仁。真正的兜底在引擎的
    limit_item_name（API / 共享 / 老存档也走那条路），这里只是少打几个字。
    """
    def _on_write(*_args):
        s = var.get() or ""
        if len(s) > W.NAME_MAX:
            var.set(s[:W.NAME_MAX])
    var.trace_add("write", _on_write)
    return var

# key, 标签, 单位, 上限 —— 顺序与标签都来自 spec/spec.json
FIELDS = [(p["key"], p["label"], p["unit"], p["max"]) for p in W.PARAM_SPECS]


def fmt_rate(bps):
    """字节/秒 → 人类可读"""
    if bps >= 1024 * 1024:
        return f"{bps / 1024 / 1024:.2f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def fmt_bytes(n):
    """GUI 里的字节格式化：KB 取整、MB 一位、GB 两位。

    与 report_gen.human_bytes 刻意保留两套：报告用 KB 一位 / MB 两位（给开发
    看精度），GUI 表格里 KB 取整、MB 一位（窄列放得下），已有回归断言钉住了
    这套显示格式。合并会改变 GUI 显示，因此这里只注明差异，不强行复用。
    """
    if n >= 1024 * 1024 * 1024:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 * 1024:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def summarize(prof):
    """一行简述参数要点 —— 列表里只看名字分不清两条场景的差别"""
    bits = []
    if prof.get("down_kbps") or prof.get("up_kbps"):
        bits.append("↓%s/↑%s kbps" % (prof.get("down_kbps") or "∞",
                                      prof.get("up_kbps") or "∞"))
    if prof.get("latency_down_ms") or prof.get("latency_up_ms"):
        bits.append("延迟 %s/%s ms" % (prof.get("latency_down_ms", 0),
                                       prof.get("latency_up_ms", 0)))
    if prof.get("loss_pct"):
        bits.append("丢包 %g%%" % prof["loss_pct"])
    if prof.get("disconnect_pct"):
        bits.append("断连 %g%%" % prof["disconnect_pct"])
    if prof.get("timeout_pct"):
        bits.append("黑洞 %g%%" % prof["timeout_pct"])
    if prof.get("dns_fail_pct"):
        bits.append("DNS %g%%" % prof["dns_fail_pct"])
    if prof.get("reject_pct"):
        bits.append("拒连 %g%%" % prof["reject_pct"])
    if prof.get("flap_sec"):
        bits.append("周期断网 %gs" % prof["flap_sec"])
    return " · ".join(bits) or "（不限制）"


def detail(prof):
    """参数全量摘要（含抖动 / 卡顿 / 周期断网），给「场景说明」窗口用。

    与 summarize 分开：那个是列表里的一行简述，越长越挤；这里是详情页，
    用户点进来就是想搞清「这支档位到底设了什么」，宁可全一点。
    """
    bits = []
    if prof.get("down_kbps") or prof.get("up_kbps"):
        bits.append("下行 %s / 上行 %s kbps" % (prof.get("down_kbps") or "∞",
                                               prof.get("up_kbps") or "∞"))
    if prof.get("latency_down_ms") or prof.get("latency_up_ms"):
        bits.append("延迟 %s/%s ms（下/上）" % (prof.get("latency_down_ms", 0),
                                              prof.get("latency_up_ms", 0)))
    if prof.get("jitter_ms"):
        bits.append("抖动 ±%g ms" % prof["jitter_ms"])
    if prof.get("loss_pct"):
        bits.append("丢包 %g%%" % prof["loss_pct"])
        # 卡顿时长只在丢包后生效（模拟 TCP 重传等待），单独列出来没意义
        if prof.get("stall_ms"):
            bits.append("丢包后停顿 %g ms" % prof["stall_ms"])
    if prof.get("disconnect_pct"):
        bits.append("随机断连 %g%%" % prof["disconnect_pct"])
    if prof.get("timeout_pct"):
        bits.append("黑洞 %g%%" % prof["timeout_pct"])
    if prof.get("dns_fail_pct"):
        bits.append("DNS 故障 %g%%" % prof["dns_fail_pct"])
    if prof.get("reject_pct"):
        bits.append("拒连 %g%%" % prof["reject_pct"])
    if prof.get("flap_sec"):
        bits.append("每 %gs 断网 %gs" % (prof["flap_sec"],
                                        prof.get("flap_blackout_sec", 2)))
    return " · ".join(bits) or "（不限制：所有流量原样直通）"

