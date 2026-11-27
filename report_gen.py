# -*- coding: utf-8 -*-
"""弱网测试报告生成器（纯逻辑，不依赖 tkinter，也不写文件）。

GUI 点「导出报告」时把 ``W.status_payload()`` 的快照交给 :func:`build`，
落成 Markdown 文本，由 GUI 层负责选路径并写盘。

设计目标：开发拿到的是「结论 + 数据 + 口径」，不是一堆事件流水账。
结构对齐 docs/verification.md：一句话结论 / 解读总结（给开发） / 测试概况 /
流量统计 / 参数快照 / 事件汇总 / 帧统计 / 口径说明 / 产物指纹。

本模块只读传入的 dict，不 import weaknet、不碰全局状态、不改任何文件——
这样单测可以直接喂构造好的快照，覆盖空数据、极端值等边界。
"""
from datetime import datetime
import re
import uuid

# 事件文案里会带对端地址（「建立隧道 host:443」「模拟拒连 host」），而报告
# 的使用场景就是转发给开发/测试同事，甚至贴进 issue。完整 IP 等于把
# 「被测 App 访问了哪些服务」的画像一起交出去，所以默认脱敏。
# IPv4 只保留前两段：仍能看出网段/服务商（CDN、云厂商），但无法还原主机。
# 句尾的「.」不是 IP 的一部分，不能让句号把掩码挡掉（"host 10.0.0.1."
# 曾被原样外发）。「.数字」也不再让整串失配：10.0.0.1.2 至少要脱敏前四段，
# 第五段按独立串保留。后顾只排数字——不能排点号，否则 host.10.0.0.1 会被
# 整串放过；(?!\d) 继续挡住 10.0.0.1234 这类长数字从中间被拆开匹配。
_IPV4_RE = re.compile(
    r"(?<!\d)(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\d)")
# IPv6 只处理带 :: 或 4 组以上的形式；`14:44:18` 这类时间不会被当成地址。
_IPV6_RE = re.compile(
    r"(?<![0-9A-Za-z:])([0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{0,4}){2,7})(?![0-9A-Za-z:])")
# 版本号不是主机地址：`v 1.2.3.4` / `version1.2.3.4` / `版本 1.2.3.4` 里的
# 四段数字要在脱敏前占位保护，否则会被 IPv4 规则误伤成 1.2.x.x。
# 但不能见前缀就整串放过：`v 10.0.0.1` 是真实 IP，过去被占位保护后原样外发。
# 先按前缀取出数字串，再由 _hold_version 判定是否疑似主机地址。
# 同样不挡「.数字」续接：否则 v1.2.3.4.5 这类五段版本号会被误伤成 v1.2.x.x.5。
_VERSION_TOKEN_RE = re.compile(
    r"(?<![0-9A-Za-z_.])(?:v|version|版本)[\s:：]*(\d{1,3}(?:\.\d{1,3}){2,3})"
    r"(?!\d)", re.IGNORECASE)
# 首段落在保留/私网/常见公网段才当疑似主机地址；1~9 这类小首段多是版本号。
_HOST_IP_HEADS = frozenset((0, 10, 127, 172, 192))

# 全角数字（０-９）与全角句点（．）先归一成半角再脱敏：地址常从聊天工具、
# 邮件、输入法里复制过来，「10．0．0．1」这种形态下正则只认半角，整串会被
# 原样外发。只映射这两类字符，其他全角标点不动。
_FULLWIDTH_TO_ASCII = {0xFF10 + i: 0x30 + i for i in range(10)}
_FULLWIDTH_TO_ASCII[0xFF0E] = 0x2E


def _looks_like_host_ip(digits):
    """四段且每段 0-255、首段是网络地址段 → 疑似 IP，不能当版本号放过。"""
    parts = digits.split(".")
    if len(parts) != 4 or any(int(p) > 255 for p in parts):
        return False
    head = int(parts[0])
    return head in _HOST_IP_HEADS or head > 223


def _mask_v4(m):
    a, b, c, d = m.groups()
    if any(int(x) > 255 for x in (a, b, c, d)):
        return m.group(0)          # 形似而非 IP（版本号等），别动
    # 监听地址/本机回环不含隐私，保留了读起来才正常：
    # 把「监听地址：0.0.x.x」印在报告里只会让人以为数据坏了。
    if (a, b, c, d) in (("0", "0", "0", "0"), ("127", "0", "0", "1")):
        return m.group(0)
    return "%s.%s.x.x" % (a, b)


def _mask_v6(m):
    s = m.group(1)
    # 含 :: 或含 a-f 字母的一定是 IPv6；剩下纯数字串才可能是编号/时间。
    if "::" not in s and not any(c in "abcdefABCDEF" for c in s):
        parts = [p for p in s.split(":") if p]
        if len(parts) < 4:
            return s                      # 14:44:18 这类时间，不足 4 组
        # 8 组齐全就是标准 IPv6（如 1111:2222:...:8888），哪怕全 4 位数字也要脱敏；
        # 只有不足 8 组、每组都是短纯数字时才像编号串，保守放过。
        if len(parts) < 8 and all(len(p) <= 3 and p.isdigit() for p in parts):
            return s
    return s.split(":")[0] + ":****"


def redact_text(text):
    """把文本里的 IP 末段掩码。纯函数，单测直接喂字符串。

    IPv4 → `a.b.x.x`；IPv6 → 保留首组，其余 `****`。
    域名不动：`mirrors.tuna.tsinghua.edu.cn` 这类公开服务不构成隐私。
    带 v / version / 版本 前缀的版本号先占位保护，还原时原样写回；但前缀紧贴
    真实 IP（v 10.0.0.1）时不保护，交给 IPv4 规则脱敏。
    """
    if not text:
        return text
    # 全角数字 / 全角句点先归一（见 _FULLWIDTH_TO_ASCII），否则全角写法的
    # 地址会绕过下面的匹配原样外发。
    text = text.translate(_FULLWIDTH_TO_ASCII)
    # 哨兵前缀每次调用随机，原文无法预知：固定 `\x00VER_0\x00` 会被原文自带
    # 的同名串在还原时一起替换，把无关文案改写成版本号。
    nonce = uuid.uuid4().hex
    saved = []

    def _hold_version(m):
        # 疑似主机地址不占位，否则 _IPV4_RE 根本看不到它，整串原样外发。
        if _looks_like_host_ip(m.group(1)):
            return m.group(0)
        saved.append(m.group(0))
        return "\x00%s_%d\x00" % (nonce, len(saved) - 1)

    text = _VERSION_TOKEN_RE.sub(_hold_version, text)
    text = _IPV6_RE.sub(_mask_v6, _IPV4_RE.sub(_mask_v4, text))
    if not saved:
        return text
    # 单遍还原：旧写法对每个占位符做一次全串 replace，是 O(n·m)。
    restore_re = re.compile("\x00" + re.escape(nonce) + r"_(\d+)\x00")
    return restore_re.sub(lambda m: saved[int(m.group(1))], text)

# 事件类型 -> 中文名。未列出的类型原样显示（新事件加进来不会丢）。
EVENT_LABELS = {
    "config": "配置变更",
    "connect": "建连",
    "disconnect": "模拟断连",
    "dns": "DNS 故障",
    "timeout": "黑洞超时",
    "reject": "模拟拒连",
    "flap": "网络切换",
    "error": "错误",
    "idle": "空闲断开",
    "share": "环境共享",
}

# 这些事件算「主动注入的故障」——一句话结论里要点出来，
# 否则开发只看场景名不知道这次到底测了哪种异常。
FAULT_TYPES = ("disconnect", "dns", "timeout", "reject", "flap")

# 固定口径说明。这些是项目里反复踩过的误读点，写进报告避免每个开发都踩一遍。
CAVEATS = [
    "延迟是**逐包注入**的，不是只在建连时加一次；一次往返约等于上行延迟 + 下行延迟。",
    "丢包由停顿（`stall_ms`）实现，**不是 TCP 重传语义**——别按重传模型去推断吞吐。",
    "上下行字节是引擎对**已接管连接**的计数，不含直通（未接管）流量。",
    "事件日志是内存环形缓冲（上限 300 条），GUI 只展示最近 60 条；故障计数取全量缓冲（fault_counts_full），本报告导出的是点击那一刻的快照。",
    "运行时长是**引擎本次启动**至今的时间，不是单个场景的持续时间。",
]


def human_bytes(n):
    """字节数转人读单位。外部工具多用 SI 1000，这里按二进制 1024，与 GUI 一致。"""
    v = float(n or 0)
    if v < 1024:
        return "%d B" % int(v)
    if v < 1024 ** 2:
        return "%.1f KB" % (v / 1024.0)
    if v < 1024 ** 3:
        return "%.2f MB" % (v / (1024.0 ** 2))
    return "%.2f GB" % (v / (1024.0 ** 3))


def human_duration(sec):
    v = int(sec or 0)
    if v < 60:
        return "%d 秒" % v
    if v < 3600:
        return "%d 分 %d 秒" % (v // 60, v % 60)
    return "%d 小时 %d 分" % (v // 3600, (v % 3600) // 60)


def _cell(text):
    """Markdown 表格单元格：转义竖线与换行，避免撑破表格。"""
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def summarize_events(events):
    """按类型聚合事件计数。events 里非 dict 项按 '?' 计，不抛异常。"""
    counts = {}
    for e in events or []:
        t = e.get("type", "?") if isinstance(e, dict) else "?"
        counts[t] = counts.get(t, 0) + 1
    return counts


def merge_fault_counts(payload, counts):
    """故障计数用引擎的全量快照整体替换，缺失时回退到「最近 60 条」的统计。

    双端同构：Android 端只要在同一份 payload 里多带一个 fault_counts_full
    字段就能得到相同结论；老版本 APK 没这个字段时本函数原样返回 counts，
    行为与改之前完全一致，不需要两端同步发版才能上线。
    """
    full = payload.get("fault_counts_full") if isinstance(payload, dict) else None
    if not isinstance(full, dict) or not full:
        return counts
    # full 是全量缓冲按 type 聚合的结果，整体覆盖「最近 60 条」的 counts，
    # 与 Android summarizeEvents 对齐；connect/config/error 等键也用全量值。
    # FAULT_TYPES 只用于结论/解读里判断「主动注入故障」，不参与合并口径。
    merged = {}
    for k, raw in full.items():
        try:
            v = int(raw or 0)
        except (TypeError, ValueError):
            continue             # 脏值跳过，与 Android 一致
        merged[k] = max(0, v)
    return merged


def tested_profile(payload):
    """本次真正测过的场景参数。

    停止限速后 ``profile`` 会回到直连，只用它写报告会把测过的场景抹掉。
    优先取 ``last_profile``（引擎记的最后一次生效的弱网场景）——没测过任何
    带内容的场景时它为 None，此时才回退到当前 profile（多半就是直连）。
    """
    return payload.get("last_profile") or payload.get("profile") or {}


# 场景「真正落地」的事件文案前缀。两端都有多个入口能施加场景，只认老的
# 「切换场景：/开启限速：」会漏掉主界面的「▶ 开始弱网测试」和组合播放，
# 导致报告结论说测了 5G、概况表却写「仅直连」（真机报告实测暴露）。
# 注意：带「（未开启限速，未生效）」的"选择"类文案不算落地，不在此列。
SCENE_PREFIXES = ("切换场景：", "开启限速：", "开始弱网测试：", "飘窗切换场景：")
COMBO_PREFIXES = ("开始播放组合：", "继续播放组合：", "播放组合：", "飘窗播放组合：")


def scene_name(msg):
    """从事件文案里取出落地场景名；不是场景落地事件返回空串。

    组合有两套写法：App/界面「开始播放组合：组合名」、引擎「播放组合「名」：摘要」，
    另一种「开始播放组合「名」：摘要」（引擎首段）。组合名可能含「」，统一截取。
    """
    if not msg:
        return ""
    for prefix in SCENE_PREFIXES:
        if msg.startswith(prefix):
            return msg[len(prefix):].strip()
    for prefix in COMBO_PREFIXES:
        if msg.startswith(prefix):
            rest = msg[len(prefix):].strip()
            # 组合名若在「」里，只取括号内的名字，别把段落摘要也塞进场景名
            if rest.startswith("「") and "」" in rest:
                return rest[1:rest.index("」")].strip()
            return rest
    if msg.startswith(("开始播放组合「", "继续播放组合「")):
        return msg[msg.index("「") + 1: msg.index("」")].strip() if "」" in msg else ""
    return ""


def scene_sequence(events):
    """从事件流提取本次测过的场景名序列（按出现顺序，去重）。

    场景由多个入口落地：切场景、开启限速、主界面「开始弱网测试」、组合播放；
    停止限速不算。报告里列这个，是为了让开发看到「这次测过哪几档」，不只是导出
    瞬间那一档。
    """
    names = []
    # status_payload() 把 events 反转成「最新在前」，而这里要按测试发生的
    # 先后顺序呈现（「依次测过 A → B → C」），所以先翻回正序。
    ordered = list(reversed(events or []))
    for e in ordered:
        if not isinstance(e, dict):
            continue
        name = scene_name(e.get("msg", "") or "")
        if name and name not in names:
            names.append(name)
    return names


def conclusion(payload, counts, frames=None):
    """一句话结论：场景 + 注入的故障 + 连接与流量，或明确说明没有数据。"""
    prof = tested_profile(payload)
    name = prof.get("name") or "未命名场景"
    stats = payload.get("stats") or {}
    total = int(stats.get("total") or 0)
    active = int(stats.get("active") or 0)
    scenes = scene_sequence(payload.get("events"))

    # 有没有「正在跑的弱网」与「本次测过弱网」是两回事：
    # 测完停了限速再导出，limit_on=False，但 last_profile/场景序列还在，
    # 不能因此说「未开启限速（直连）」——那会误导成全程没测。
    injected = [EVENT_LABELS.get(k, k) for k in FAULT_TYPES if counts.get(k)]
    fault_phrase = ("注入了%s，" % "、".join(injected)) if injected else ""
    if scenes and len(scenes) > 1:
        head = "本次依次测过 %s，" % " → ".join(scenes[:5])
        if fault_phrase:
            head += fault_phrase
    elif scenes:
        head = "在「%s」下%s" % (_cell(scenes[0]), fault_phrase or "运行，")
    elif fault_phrase:
        # 有故障事件但没解析出场景名（如事件 msg 不带「切换场景：」前缀）
        where = "在「%s」下" % _cell(name) if prof.get("name") else "本次"
        head = where + fault_phrase
    elif payload.get("limit_on"):
        head = "在「%s」下运行，" % _cell(name)
    else:
        head = "当前**未开启限速（直连）**，"

    if total <= 0:
        return head + "本次尚未捕获到经过代理的连接，报告仅含配置与状态快照。"
    return (head + "共捕获 %d 条连接（活跃 %d 条），下行 %s / 上行 %s，引擎运行 %s。" %
            (total, active,
             human_bytes(stats.get("down")), human_bytes(stats.get("up")),
             human_duration(stats.get("uptime"))))


def interpretation(payload, counts, frames=None, param_specs=()):
    """给开发的解读总结：一眼判定数据能否用、测了什么、关注什么、下一步怎么做。

    与「一句话结论」分工：结论复述「场景+故障+流量」的事实，这里做**诊断**——
    有效性判定、异常关注点、行动建议。返回 Markdown 行列表；纯逻辑，不写文件，
    单测可以直接喂构造好的快照。
    """
    stats = payload.get("stats") or {}
    total = int(stats.get("total") or 0)
    active = int(stats.get("active") or 0)
    bypass = int(stats.get("bypass") or 0)
    scenes = scene_sequence(payload.get("events"))
    prof = tested_profile(payload)
    limit_on = bool(payload.get("limit_on"))
    injected = [(EVENT_LABELS.get(k, k), int(counts.get(k) or 0))
                for k in FAULT_TYPES if counts.get(k)]
    errors = int(counts.get("error") or 0)

    # 是否真的施加过弱网：不能只看场景序列——它会因环形缓冲（300 条）被连接/错误
    # 事件挤出而变空（真机 121 连接 + 24 错误就把开头的「开始弱网测试」挤掉了）。
    # last_profile 有非默认参数也是确凿的弱网证据，与 conclusion 的口径一致。
    prof_name = prof.get("name") or ""
    nondefault = any(prof.get(p.get("key")) not in (None, p.get("default", 0))
                     for p in (param_specs or ()) if p.get("key"))
    has_weaknet = bool(scenes or injected or nondefault or
                       (prof_name and "直连" not in prof_name))

    lines = []

    # 1. 有效性判定——开发最想知道的「这份报告能不能用」
    if total <= 0:
        verdict = "empty"
        lines.append("**数据有效性**：本次未捕获到经过代理的连接，报告仅含配置与状态快照，"
                     "**不能用于评估弱网效果**——需让被测 App 的流量真正经过代理后再导出。")
    elif not has_weaknet:
        verdict = "direct"
        lines.append("**数据有效性**：捕获 %d 条连接，但未识别到弱网场景或故障注入，"
                     "可能全程直连；请确认已施加非默认场景后再测。" % total)
    else:
        verdict = "valid"
        fault = ("，注入 " + "、".join("%s×%d" % (n, c) for n, c in injected)) if injected else ""
        lines.append("**数据有效性**：**有效**——捕获 %d 条连接%s，可用于评估该场景下的表现。" %
                     (total, fault))

    # 2. 测过哪些场景
    if scenes:
        tail = "；导出时已停限速，下列参数取最后一次生效的场景" if not limit_on else ""
        lines.append("**测过场景**：%s%s。" % (" → ".join(scenes[:5]), tail))
    elif prof_name and "直连" not in prof_name:
        # 场景事件被环形缓冲挤出时用 last_profile 兜底（与一句话结论同源）
        lines.append("**测过场景**：%s（事件记录已被环形缓冲覆盖，取最后一次生效场景）。" %
                     _cell(prof_name))
    elif injected:
        lines.append("**测过场景**：未识别到具体场景名，但记录到故障注入，参数见快照。")

    # 3. 非默认参数摘要（开发一眼看到这次改了什么）
    changed = []
    for p in (param_specs or ()):
        key = p.get("key")
        if key is None:
            continue
        val = prof.get(key, p.get("default", 0))
        if val != p.get("default", 0):
            changed.append("%s=%s%s" %
                           (_cell(p.get("label", key)), _cell(val), _cell(p.get("unit", ""))))
    if changed:
        lines.append("**参数变更**：%s。" % "、".join(changed[:6]))

    # 4. 关注点——异常与易误读的地方
    notes = []
    if errors:
        notes.append("出现 %d 次错误事件，建议查「事件汇总」中的具体报错" % errors)
    if injected:
        notes.append("含 %d 类故障注入（属预期，非崩溃）" % len(injected))
    if has_weaknet and not limit_on:
        notes.append("导出时限速已停止，概况表显示直连属正常")
    if active:
        notes.append("导出时仍有 %d 条活跃连接未结束" % active)
    if bypass:
        notes.append("有 %s 直通流量未计入上下行" % human_bytes(bypass))
    lines.append("**关注点**：%s。" % ("；".join(notes) if notes else "无异常信号"))

    # 5. 下一步行动
    if verdict == "empty":
        nxt = "开启接管 → 选场景 → 点开始 → 跑真实请求 → 再导出"
    elif verdict == "direct":
        nxt = "确认已施加非默认场景，避免只跑直连"
    else:
        nxt = "结合「参数快照」与「事件时间线」定位具体表现"
    lines.append("**下一步**：%s。" % nxt)

    return lines


def build(payload, param_specs=(), param_notes=None, frames=None, generated_at=None,
          redact=True):
    """把状态快照渲染成 Markdown 报告。

    payload      : W.status_payload() 的返回（dict）
    param_specs  : W.PARAM_SPECS（每项含 key/label/unit/default）
    param_notes  : W.PARAM_NOTES（key -> 人话解释）
    frames       : FrameStats.snapshot() 或 None
    generated_at : 生成时间字符串；None 时取当前时间（单测传固定值）
    redact       : 是否脱敏 IP（默认 True）。关掉只在内部排障时用，
                   报告里会显式标注「未脱敏」提醒不要外发。
    """
    param_notes = param_notes or {}
    now = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prof = tested_profile(payload)
    stats = payload.get("stats") or {}
    events = payload.get("events") or []
    counts = merge_fault_counts(payload, summarize_events(events))
    scope = payload.get("scope") or {}
    scenes = scene_sequence(events)

    out = []
    out.append("# WeakNet 弱网测试报告")
    out.append("")
    out.append("> 生成时间：%s" % now)
    out.append("")

    # 1. 一句话结论
    out.append("## 一句话结论")
    out.append("")
    out.append(conclusion(payload, counts, frames))
    out.append("")

    # 2. 解读总结（给开发的一眼定位：有效性 / 测了什么 / 关注点 / 下一步）
    out.append("## 解读总结（给开发）")
    out.append("")
    out.extend(interpretation(payload, counts, frames, param_specs))
    out.append("")

    # 3. 测试概况
    out.append("## 测试概况")
    out.append("")
    out.append("| 项 | 值 |")
    out.append("|---|---|")
    out.append("| 版本 | v%s（build %s） |" %
               (_cell(payload.get("version", "-")), _cell(payload.get("build", "-"))))
    out.append("| 规格哈希 | `%s` |" % _cell(payload.get("spec_hash", "-")))
    # 场景序列可能被环形缓冲挤出：有非默认 last_profile 时用它兜底，别误报「仅直连」
    _prof_name = prof.get("name") or ""
    _scenes_cell = (" → ".join(scenes) if scenes else
                    ("%s（事件记录已覆盖，取最后生效场景）" % _cell(_prof_name)
                     if _prof_name and "直连" not in _prof_name else "(仅直连，未施加弱网)"))
    out.append("| 本次测过的场景 | %s |" % _scenes_cell)
    out.append("| 导出时生效场景 | %s%s |" %
               (_cell((payload.get("profile") or {}).get("name", "-")),
                "（已停止限速，参数见上）" if scenes and not payload.get("limit_on") else ""))
    out.append("| 场景 key | `%s` |" % _cell(payload.get("preset_key", "-")))
    out.append("| 限速开关 | %s |" % ("开启" if payload.get("limit_on") else "关闭（已停止）"))
    out.append("| 限速闸门 | %s |" % ("打开" if payload.get("gate_open") else "关闭"))
    out.append("| 系统代理 | %s |" % ("已接管" if payload.get("sysproxy") else "未接管"))
    listen = payload.get("listen") or {}
    out.append("| 监听地址 | %s:%s |" %
               (_cell(listen.get("host", "-")), _cell(listen.get("port", "-"))))
    mode = scope.get("mode", "-")
    out.append("| 接管范围 | %s%s |" %
               (_cell(mode),
                "（%s）" % _cell(", ".join(scope.get("list") or []))
                if scope.get("list") else ""))
    out.append("| 运行时长 | %s |" % human_duration(stats.get("uptime")))
    out.append("")

    # 4. 流量统计
    out.append("## 流量统计")
    out.append("")
    out.append("| 指标 | 数值 |")
    out.append("|---|---:|")
    out.append("| 连接总数 | %d |" % int(stats.get("total") or 0))
    out.append("| 当前活跃 | %d |" % int(stats.get("active") or 0))
    out.append("| 下行字节 | %s（%d B） |" %
               (human_bytes(stats.get("down")), int(stats.get("down") or 0)))
    out.append("| 上行字节 | %s（%d B） |" %
               (human_bytes(stats.get("up")), int(stats.get("up") or 0)))
    out.append("| 直通字节 | %s（%d B） |" %
               (human_bytes(stats.get("bypass")), int(stats.get("bypass") or 0)))
    out.append("")

    # 5. 参数快照：只列非默认项，附一句人话解释——开发要看的是「这次改了什么」
    if param_specs:
        out.append("## 参数快照（非默认项）")
        out.append("")
        if payload.get("last_profile"):
            out.append("> 以下是**最后一次生效的弱网场景**参数；若本次测过多档，"
                       "完整切换顺序见「测试概况 → 本次测过的场景」。")
            out.append("")
        rows = []
        for p in param_specs:
            key = p.get("key")
            val = prof.get(key, p.get("default", 0))
            if val == p.get("default", 0):
                continue
            note = (param_notes.get(key) or "").split("\n")[0]
            rows.append("| %s | %s %s | %s |" %
                        (_cell(p.get("label", key)), _cell(val),
                         _cell(p.get("unit", "")), _cell(note)))
        if rows:
            out.append("| 参数 | 设定值 | 说明 |")
            out.append("|---|---:|---|")
            out.extend(rows)
        else:
            out.append("本次所有参数均为默认值（直连配置）。")
        out.append("")

    # 6. 事件汇总 + 时间线
    out.append("## 事件汇总")
    out.append("")
    # 引擎的 fault_counts_full 是「稠密快照」（10 类全给，没出现的填 0），
    # 直接打表会让空运行也印出十行 0 值。这里只列真的发生过的事件；
    # 引擎侧保持稠密（status 兼容 Android），显示侧过滤。
    rows = [(t, c) for t, c in counts.items() if c > 0]
    if rows:
        out.append("| 事件 | 次数 |")
        out.append("|---|---:|")
        for t, c in sorted(rows, key=lambda kv: (-kv[1], kv[0])):
            out.append("| %s | %d |" % (_cell(EVENT_LABELS.get(t, t)), c))
    else:
        out.append("本次运行没有记录到事件。")
    out.append("")
    if events:
        out.append("最近事件（最多 30 条，最新在上）：")
        out.append("")
        for e in events[:30]:
            if isinstance(e, dict):
                out.append("- `[%s]` %s" % (_cell(e.get("t", "")), _cell(e.get("msg", ""))))
            else:
                out.append("- %s" % _cell(e))
        out.append("")

    # 7. GUI 帧统计（可选）
    if frames:
        out.append("## GUI 刷新帧统计")
        out.append("")
        out.append("| 指标 | 值 |")
        out.append("|---|---:|")
        for label, key, suffix in (
                ("累计帧数", "frames", ""), ("窗口内帧数", "windowed", ""),
                ("间隔中位", "gap_median_ms", " ms"), ("间隔 p95", "gap_p95_ms", " ms"),
                ("间隔最大", "gap_max_ms", " ms"), ("单帧耗时均值", "cost_avg_ms", " ms"),
                ("单帧耗时最大", "cost_max_ms", " ms"),
                ("超 16ms 帧数", "over_frame", ""), ("超 100ms 帧数", "over_stall", ""),
                ("约刷新频率", "per_sec", " 次/秒")):
            v = frames.get(key, 0)
            out.append("| %s | %s%s |" %
                       (label, ("%.1f" % v) if isinstance(v, float) else v, suffix))
        out.append("")

    # 8. 口径说明（固定，防止每个开发重复踩坑）
    out.append("## 口径说明")
    out.append("")
    caveats = list(CAVEATS)
    if redact:
        caveats.append("报告已脱敏：IPv4 只保留前两段（如 `64.233.x.x`）。"
                       "需要原始地址时重新导出并选择不脱敏。")
    else:
        caveats.append("**本报告未脱敏，含完整对端 IP**——仅限内部排障使用，"
                       "对外分享前请重新导出脱敏版本。")
    for c in caveats:
        out.append("- %s" % c)
    out.append("")

    # 9. 产物指纹：报告必须能对应到具体构建与规格
    out.append("## 产物指纹")
    out.append("")
    out.append("- 版本：`v%s`，build `%s`" %
               (_cell(payload.get("version", "-")), _cell(payload.get("build", "-"))))
    out.append("- 规格哈希（spec.json sha256 前 8 位）：`%s`" %
               _cell(payload.get("spec_hash", "-")))
    out.append("")

    md = "\n".join(out)
    return redact_text(md) if redact else md


def default_filename(payload=None, at=None):
    """报告默认文件名，带时间戳避免覆盖上一次的测试。"""
    t = at or datetime.now()
    return "WeakNet测试报告_%s.md" % t.strftime("%Y%m%d_%H%M%S")
