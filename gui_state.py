# -*- coding: utf-8 -*-
"""GUI 视图模型层：把引擎状态映射为界面该显示的文案与样式。

把 refresh() 里「该显示什么」与「怎么改控件」拆开：

* 本模块只做**纯计算** —— 输入是引擎状态与上次采样，输出是「文本 + 色调」，
  不 import tkinter、不碰控件树、不留任何全局状态；
* weaknet_gui.App 拿到结果后逐项与上次比较，**只有真的变了才 config()**。

为什么值得拆：refresh 是 500ms 一轮的常驻路径，原来每轮无条件给 20 个控件
下发 config —— 值一个字都没变也照样触发 Tk 的选项解析与重绘调度。实测
20 次/轮 × 每分钟 120 轮 = 每分钟 2400 次无谓重配。拆开之后「是否变化」
这件事可以被单测覆盖（tests/test_gui_state.py），不需要起窗口。

色调只返回语义 token（ok/warn/muted/text/accent/btn2），具体色值由 GUI 层
按明暗主题映射 —— 本模块因此对主题无感，换配色不用改这里。
"""

from gui_text import fmt_rate

# 状态灯/数值的语义色调
OK = "ok"
WARN = "warn"
MUTED = "muted"
TEXT = "text"
ACCENT = "accent"
BTN2 = "btn2"


def rate_of(prev_t, prev_down, prev_up, now, down, up, min_dt=0.4):
    """实时速率的采样值。

    `dt` 太小（refresh 被别处顺手多调一次）时不要重算：两个采样点挨得太近，
    差值除以极小的 dt 会抖成天文数字。原实现就是 `dt >= 0.4` 才更新，
    这里把门槛显式化成参数，便于测试。

    返回 (down_bps, up_bps)；不到门槛返回 None（调用方沿用上一次的值）。
    """
    dt = now - prev_t
    if dt < min_dt:
        return None
    return ((down - prev_down) / dt, (up - prev_up) / dt)


def header_view(proxy_host, proxy_port, lan_ip, ctrl_port, sysproxy_on):
    """顶栏四行文本：代理地址、控制接口、局域网按钮、系统代理按钮。

    局域网模式下直接给局域网 IP——手机把它填进 Wi-Fi 代理即可，
    不需要任何凭据（Android 原生代理设置也没有凭据输入口）。
    """
    lan = proxy_host == "0.0.0.0"
    addr = f"{lan_ip}:{proxy_port}" if lan else f"127.0.0.1:{proxy_port}"
    # 覆盖范围写在地址后面：代理只接 HTTP/HTTPS，IPv6 不经过 WeakNet。
    # 不写清楚的话，用户会拿 IPv6 直连或 QUIC 的测速结果当「弱网没生效」。
    proxy = f"代理地址：{addr}　（HTTP/HTTPS；IPv6 不接管）"
    return {
        "proxy": proxy,
        "ctrl": f"控制接口：http://127.0.0.1:{ctrl_port}/"
                f"　局域网 IP：{lan_ip}",
        "lan_text": "关闭局域网访问" if lan else "开启局域网访问",
        "sys_text": "解除接管" if sysproxy_on else "一键接管本机流量",
        "sys_on": bool(sysproxy_on),
    }


def scene_label(profile_name, preset_key, limit_on, name_of=None):
    """当前场景名。

    未开启限速时引擎里的 profile 是「直连」，直接显示它会永远是「直连（不限制）」，
    所以按 preset_key 显示用户选中的那一支，并注明「未开启」。
    `name_of(key)` 负责把 key 解析成名字（组合/自定义/内置三类都在引擎里），
    解析失败就退回 profile 自己的名字。
    """
    if limit_on or not preset_key:
        return profile_name
    if name_of is None:
        return profile_name
    try:
        return "%s（未开启）" % name_of(preset_key)
    except Exception:
        return profile_name


def stats_view(conns_total, conns_active, rate, gate_open, scene):
    """右上看板六项。返回 [(key, 文本, 色调), ...]，顺序即控件顺序。"""
    return [
        ("scene", scene, None),
        ("conns", str(conns_total), None),
        ("active", str(conns_active), None),
        ("down", fmt_rate(rate[0]), None),
        ("up", fmt_rate(rate[1]), None),
        ("gate", "畅通" if gate_open else "已断网", OK if gate_open else WARN),
    ]


def light_view(limit_on, gate_open):
    """顶栏状态灯：说的是「限速有没有在跑」，不是「服务在不在跑」。

    启动后默认未开启，此时流量原样直通，必须让用户一眼看出来。
    """
    if not limit_on:
        return ("● 限速未开启", MUTED)
    return (("● 限速运行中" if gate_open else "● 周期断网中"),
            OK if gate_open else WARN)


def share_view(pending):
    """环境共享按钮：有别人推来的同名环境时带上待处理条数。"""
    return "🔗 环境共享" + ("（%d 条待处理）" % pending if pending else "")


def run_btn_view(limit_on):
    """限速总开关：未开启是实心主按钮（邀请点开），运行中变成浅底的停止。"""
    if limit_on:
        return {"text": "⏹ 停止限速（恢复直连）", "bg": BTN2, "fg": TEXT}
    return {"text": "▶ 开启限速", "bg": ACCENT, "fg": "white"}


def scope_btn_views(allmode):
    """主界面两个并排入口：生效中的那个实心，另一个浅底。"""
    return {
        "all": {"text": ("✔ " if allmode else "") + "接管一切",
                "bg": ACCENT if allmode else BTN2,
                "fg": "white" if allmode else TEXT},
        "open": {"text": ("✔ " if not allmode else "") + "接管范围…",
                 "bg": ACCENT if not allmode else BTN2,
                 "fg": "white" if not allmode else TEXT},
    }


def scope_tip(scope_mode, scope_list):
    """主界面那一行摘要：只显示引擎里**已生效**的范围。

    名单只列前 4 个：这行是提示不是清单，列全了会把右上栏撑变形。
    """
    names = list(scope_list or [])
    more = "…" if len(names) > 4 else ""
    suffix = ("（" + ", ".join(names[:4]) + more + "）") if names else ""
    if scope_mode == "all":
        return "全部程序"
    if scope_mode == "only":
        if not names:
            return "仅接管 0 个程序（空名单 = 不接管任何程序）"
        return "仅接管 %d 个程序%s" % (len(names), suffix)
    if not names:
        return "排除 0 个程序（空名单 = 全部接管）"
    return "排除 %d 个程序%s" % (len(names), suffix)


def mine_hint(items, selected_name):
    """自定义场景区：提示行 + 删除按钮文案（None = 该收起）。

    「有没有库存」「选中的是不是自定义场景」这两件事决定了三种文案，
    原实现把它们写在一处 if/else 里，抽出来后可以逐条断言。
    """
    if not selected_name:
        lab = ("保存后即可在这里点一下切换；选中某条自定义，"
               "上方才会出现它的删除按钮。" if items
               else "还没有保存的场景：展开「自定义参数」调好参数，"
                    "填个名字点「保存当前参数」。")
        return {"del_text": None, "lab_text": lab}
    return {"del_text": "🗑 删除当前场景「%s」" % selected_name,
            "lab_text": "只删除这一条保存的场景，不影响内置预设。"}


def combo_bar(selected_name, summary, state, limit_on, max_steps, has_combos):
    """弱网组合区：提示行文案（编辑/删除按钮是否显示由调用方按 selected_name 判断）。

    state 是播放进度（step/total/left/loop），None 表示当前没在播放；
    has_combos 决定「一支都没建」时给的是引导语还是操作提示。
    """
    if not selected_name:
        return ("点一支组合先选中它，「开启限速」后按顺序播放；双击可查看/编辑并删除。"
                if has_combos else
                "还没有组合：点「＋ 新建组合」，把最多 %d 支场景排成一条链路。"
                % max_steps)
    if state:
        return "▶ 正在播放第 %d/%d 段，还剩 %d 秒%s" % (
            state["step"], state["total"], state["left"],
            "（循环）" if state.get("loop") else "")
    if not limit_on:
        return "已选中，点「开启限速」后按序播放：%s" % summary
    return summary
