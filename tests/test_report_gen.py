# -*- coding: utf-8 -*-
"""report_gen.py 的回归：报告是「给开发读的结论」，不是数字堆砌。

覆盖三条线：
1. 结构完整（8 个段落 + 口径说明 + 产物指纹），缺一不可；
2. 结论真的可解读（会点出注入的故障、连接数、直连态）；
3. 脏数据不崩（空事件、非 dict、缺字段、极端字节数）。

单测喂构造好的快照，不 import weaknet、不碰 GUI、不写任何文件。
"""
import pytest

import report_gen as R

pytestmark = pytest.mark.unit


def base_payload(**over):
    p = {
        "version": "1.1.19", "build": 129, "spec_hash": "9990e03d",
        "profile": {"name": "直连（不限制）"}, "preset_key": "direct",
        "limit_on": False, "gate_open": True, "sysproxy": False,
        "listen": {"host": "127.0.0.1", "port": 8888},
        "scope": {"mode": "all", "list": []},
        "stats": {"total": 0, "active": 0, "up": 0, "down": 0,
                  "bypass": 0, "uptime": 0},
        "events": [],
    }
    p.update(over)
    return p


SPECS = [
    {"key": "latency_down_ms", "label": "下行延迟", "unit": "ms", "default": 0},
    {"key": "down_kbps", "label": "下行带宽", "unit": "kbps", "default": 0},
    {"key": "loss_pct", "label": "丢包率", "unit": "%", "default": 0},
]
NOTES = {
    "latency_down_ms": "给服务器 → 本机方向每块数据加的固定延迟。",
    "down_kbps": "下行带宽上限。",
}


def test_report_has_all_sections():
    text = R.build(base_payload(), SPECS, NOTES, generated_at="t")
    for section in ("一句话结论", "测试概况", "流量统计", "参数快照",
                    "事件汇总", "口径说明", "产物指纹"):
        assert section in text
    assert text.startswith("# WeakNet 弱网测试报告")
    assert "生成时间：t" in text


def test_conclusion_mentions_injected_faults():
    p = base_payload(limit_on=True, profile={"name": "4G 弱网（边缘/室内）"},
                     events=[{"type": "dns", "msg": "模拟 DNS 故障"},
                             {"type": "disconnect", "msg": "模拟断连"},
                             {"type": "config", "msg": "切换场景"}])
    c = R.conclusion(p, R.summarize_events(p["events"]))
    assert "4G 弱网" in c
    assert "DNS 故障" in c and "模拟断连" in c
    assert "配置变更" not in c       # 配置不算故障，不该混进结论


def test_conclusion_says_direct_when_limit_off():
    c = R.conclusion(base_payload(), {})
    assert "未开启限速" in c
    assert "尚未捕获到经过代理的连接" in c


def test_conclusion_reports_traffic_and_duration():
    p = base_payload(limit_on=True, stats={"total": 7, "active": 2,
                                           "up": 2048, "down": 1048576,
                                           "bypass": 0, "uptime": 125})
    c = R.conclusion(p, {})
    assert "7 条连接" in c and "活跃 2 条" in c
    assert "1.00 MB" in c and "2.0 KB" in c
    assert "2 分 5 秒" in c


def test_nondefault_params_listed_with_values():
    p = base_payload(profile={"name": "自定义", "latency_down_ms": 150,
                              "down_kbps": 8000, "loss_pct": 0})
    text = R.build(p, SPECS, NOTES, generated_at="t")
    assert "下行延迟" in text and "150 ms" in text
    assert "下行带宽" in text and "8000 kbps" in text
    assert "丢包率" not in text       # 默认值不该占报告篇幅
    assert "固定延迟" in text         # 带一句人话解释


def test_all_default_params_says_so():
    text = R.build(base_payload(), SPECS, NOTES, generated_at="t")
    assert "本次所有参数均为默认值" in text


def test_event_summary_aggregates_and_escapes_pipe():
    ev = [{"t": "1", "type": "config", "msg": "a|b"},
          {"t": "2", "type": "config", "msg": "切换"},
          {"t": "3", "type": "flap", "msg": "网络切换"}]
    text = R.build(base_payload(events=ev), SPECS, NOTES, generated_at="t")
    assert "| 配置变更 | 2 |" in text
    assert "| 网络切换 | 1 |" in text
    assert "a\\|b" in text           # 竖线被转义，不撑破表格


def test_frame_stats_section_when_present():
    frames = {"frames": 100, "windowed": 60, "gap_median_ms": 500.0,
              "gap_p95_ms": 600.0, "gap_max_ms": 900.0,
              "cost_avg_ms": 2.5, "cost_max_ms": 40.0,
              "over_frame": 3, "over_stall": 0, "per_sec": 2.0}
    text = R.build(base_payload(), SPECS, NOTES, frames=frames, generated_at="t")
    assert "GUI 刷新帧统计" in text
    assert "间隔 p95" in text and "600.0 ms" in text
    assert "超 16ms 帧数" in text


def test_caveats_present():
    text = R.build(base_payload(), SPECS, NOTES, generated_at="t")
    assert "逐包注入" in text
    assert "不是 TCP 重传语义" in text
    assert "不含直通" in text


def test_fingerprint_present():
    text = R.build(base_payload(), SPECS, NOTES, generated_at="t")
    assert "9990e03d" in text
    assert "v1.1.19" in text and "129" in text


def test_dirty_events_do_not_crash():
    ev = [{}, {"type": "config"}, "not a dict", None,
          {"type": "unknown_new", "msg": "未来事件"}]
    counts = R.summarize_events(ev)
    assert counts["?"] == 3           # 空 dict 缺 type + 字符串 + None
    assert counts["config"] == 1
    assert counts["unknown_new"] == 1
    text = R.build(base_payload(events=ev), SPECS, NOTES, generated_at="t")
    assert "未来事件" in text


def test_empty_payload_does_not_crash():
    text = R.build({})
    assert "测试报告" in text
    assert "本次运行没有记录到事件" in text


def test_human_bytes_and_duration():
    assert R.human_bytes(0) == "0 B"
    assert R.human_bytes(1024) == "1.0 KB"
    assert R.human_bytes(1048576) == "1.00 MB"
    assert R.human_duration(0) == "0 秒"
    assert R.human_duration(59) == "59 秒"
    assert R.human_duration(60) == "1 分 0 秒"
    assert R.human_duration(3661) == "1 小时 1 分"


def test_stopped_limit_does_not_misreport_as_direct():
    """测完停限速再导出：不能说「未开启限速（直连）」，那是误导。"""
    p = base_payload(
        limit_on=False,
        last_profile={"name": "高丢包（30% 丢包）", "loss_pct": 30},
        events=[{"type": "config", "msg": "切换场景：直连（不限制）"},
                {"type": "config", "msg": "切换场景：4G LTE"},
                {"type": "config", "msg": "切换场景：高丢包（30% 丢包）"},
                {"type": "config", "msg": "停止限速（恢复直连）"}],
        stats={"total": 3, "active": 0, "up": 0, "down": 4096,
               "bypass": 0, "uptime": 30})
    c = R.conclusion(p, R.summarize_events(p["events"]))
    assert "未开启限速" not in c           # 不能误报全程直连
    assert "4G LTE" in c and "高丢包" in c  # 要点出测过的档位


def test_tested_profile_prefers_last_profile():
    p = base_payload(profile={"name": "直连（不限制）"},
                     last_profile={"name": "4G LTE", "down_kbps": 9000})
    assert R.tested_profile(p)["name"] == "4G LTE"


def test_scene_sequence_dedup_and_order():
    # 按 status_payload 的口径：最新在前。函数内部翻回正序，
    # 让报告里「依次测过」按真实发生顺序呈现。
    ev = [{"msg": "切换场景：4G LTE"}, {"msg": "停止限速（恢复直连）"},
          {"msg": "切换场景：DNS 故障"}, {"msg": "开启限速：4G LTE"},
          {"msg": "切换场景：4G LTE"}]
    assert R.scene_sequence(ev) == ["4G LTE", "DNS 故障"]


def test_scene_sequence_recognizes_main_entry_and_combos():
    """真机报告暴露：主界面「开始弱网测试：X」和各入口组合文案都得认。

    只认「切换场景/开启限速」会让结论说测了 5G、概况表却写「仅直连」。
    """
    ev = [
        {"msg": "开始播放组合「弱网三连」：3 段"},
        {"msg": "开始弱网测试：5G（良好覆盖）"},
        {"msg": "飘窗切换场景：高丢包（30% 丢包）"},
        {"msg": "开始弱网测试：5G（良好覆盖）"},  # 去重
    ]
    # 最新在前，翻回正序：5G → 高丢包 → 组合
    assert R.scene_sequence(ev) == [
        "5G（良好覆盖）", "高丢包（30% 丢包）", "弱网三连"]


def test_scene_name_rejects_unapplied_selection():
    """带「未开启限速，未生效」的"选择"文案不算场景落地。"""
    assert R.scene_name("选择场景（未开启限速，未生效）：4G LTE") == ""
    assert R.scene_name("选择组合（未开启限速，未生效）：三连") == ""
    assert R.scene_name("停止弱网测试（场景保留：5g）") == ""
    assert R.scene_name("开始弱网测试：5G（良好覆盖）") == "5G（良好覆盖）"


def test_main_entry_report_no_longer_says_only_direct():
    """复现真机那份报告：结论说 5G，概况表不该再写「仅直连，未施加弱网」。"""
    p = base_payload(
        limit_on=False,
        last_profile={"name": "5G（良好覆盖）", "latency_down_ms": 20},
        events=[{"type": "config", "msg": "开始弱网测试：5G（良好覆盖）"},
                {"type": "disconnect", "msg": "模拟断连：内网网关"},
                {"type": "config", "msg": "停止弱网测试（场景保留：5g）"}],
        stats={"total": 80, "active": 0, "up": 153763, "down": 326756,
               "bypass": 0, "uptime": 99})
    text = R.build(p, SPECS, NOTES, generated_at="t")
    assert "本次测过的场景" in text
    assert "5G（良好覆盖）" in text
    assert "(仅直连，未施加弱网)" not in text


def test_report_shows_scene_sequence_and_last_params():
    p = base_payload(
        limit_on=False,
        last_profile={"name": "高丢包（30% 丢包）", "loss_pct": 30},
        events=[{"type": "config", "msg": "切换场景：高丢包（30% 丢包）"},
                {"type": "config", "msg": "切换场景：4G LTE"}],  # 最新在前
        stats={"total": 3, "active": 0, "up": 0, "down": 4096,
               "bypass": 0, "uptime": 30})
    SPECS2 = SPECS + [{"key": "loss_pct", "label": "下行丢包率", "unit": "%", "default": 0}]
    text = R.build(p, SPECS2, NOTES, generated_at="t")
    assert "本次测过的场景" in text
    assert "4G LTE → 高丢包（30% 丢包）" in text
    assert "下行丢包率" in text and "30 %" in text
    assert "最后一次生效的弱网场景" in text


def test_interpretation_section_present_in_report():
    text = R.build(base_payload(), SPECS, NOTES, generated_at="t")
    assert "## 解读总结（给开发）" in text
    for tag in ("数据有效性", "关注点", "下一步"):
        assert "**%s**" % tag in text


def test_interpretation_flags_empty_run_as_unusable():
    lines = R.interpretation(base_payload(), {})
    joined = "\n".join(lines)
    assert "不能用于评估弱网效果" in joined
    assert "开启接管" in joined          # 下一步：空跑要给补测指引


def test_interpretation_flags_possible_direct():
    """有连接但没场景没故障 → 提醒可能全程直连。"""
    p = base_payload(stats={"total": 5, "active": 0, "up": 100, "down": 100,
                            "bypass": 0, "uptime": 10})
    joined = "\n".join(R.interpretation(p, {}))
    assert "可能全程直连" in joined


def test_interpretation_valid_fault_and_params():
    p = base_payload(
        limit_on=False,
        last_profile={"name": "4G 弱网", "latency_down_ms": 150, "down_kbps": 8000},
        events=[{"type": "config", "msg": "开始弱网测试：4G 弱网"},
                {"type": "disconnect", "msg": "模拟断连"}],
        stats={"total": 12, "active": 2, "up": 1024, "down": 2048,
               "bypass": 512, "uptime": 30})
    lines = "\n".join(R.interpretation(p, R.summarize_events(p["events"]), param_specs=SPECS))
    assert "**有效**" in lines
    assert "模拟断连×1" in lines
    assert "4G 弱网" in lines
    assert "下行延迟=150ms" in lines and "下行带宽=8000kbps" in lines
    assert "导出时限速已停止" in lines
    assert "活跃连接未结束" in lines
    assert "直通流量未计入" in lines


def test_interpretation_reports_errors_as_concern():
    p = base_payload(
        last_profile={"name": "5G", "latency_down_ms": 20},
        events=[{"type": "config", "msg": "开始弱网测试：5G"},
                {"type": "error", "msg": "连接重置"},
                {"type": "error", "msg": "超时"}],
        stats={"total": 8, "active": 0, "up": 500, "down": 900,
               "bypass": 0, "uptime": 20})
    lines = "\n".join(R.interpretation(p, R.summarize_events(p["events"])))
    assert "出现 2 次错误事件" in lines


def test_interpretation_uses_last_profile_when_scene_events_evicted():
    """真机回归：121 连接 + 24 错误把开头场景事件挤出环形缓冲，
    但 last_profile 有非默认参数——不能误判成「全程直连」。"""
    p = base_payload(
        limit_on=True, preset_key="high_loss",
        last_profile={"name": "高丢包（30% 丢包）", "latency_down_ms": 150, "loss_pct": 30},
        events=[{"type": "connect", "msg": "TCP ..."} for _ in range(5)] +
               [{"type": "error", "msg": "连接失败"} for _ in range(3)],
        stats={"total": 121, "active": 0, "up": 1000, "down": 2000,
               "bypass": 0, "uptime": 300})
    lines = "\n".join(R.interpretation(p, R.summarize_events(p["events"]), param_specs=SPECS))
    assert "**有效**" in lines
    assert "全程直连" not in lines
    assert "高丢包" in lines and "环形缓冲覆盖" in lines
    # 概况表也不能写「仅直连」
    text = R.build(p, SPECS, NOTES, generated_at="t")
    assert "(仅直连，未施加弱网)" not in text
    assert "高丢包（30% 丢包）" in text


def test_default_filename_has_timestamp():
    from datetime import datetime
    name = R.default_filename(at=datetime(2026, 11, 19, 18, 40, 5))
    assert name == "WeakNet测试报告_20261119_184005.md"


# ---------------------------------------------------------------- C4 回归

def test_fault_counts_full_recovers_early_faults():
    """前 240 条有故障、后 60 条只有连接事件：全量计数不能漏报早期故障。"""
    full = ([{"type": "disconnect", "msg": "模拟断连：早期"} for _ in range(3)] +
            [{"type": "connect", "msg": "建连"} for _ in range(240)])
    recent = list(full[-60:])               # status_payload 给事件时间窗口的口径
    recent.reverse()
    p = base_payload(
        limit_on=True,
        profile={"name": "4G 弱网", "down_kbps": 8000},
        events=recent,
        fault_counts_full={"disconnect": 3, "dns": 2, "timeout": 0,
                           "reject": 0, "flap": 1},
        stats={"total": 20, "active": 0, "up": 100, "down": 200,
               "bypass": 0, "uptime": 60})
    counts = R.merge_fault_counts(p, R.summarize_events(p["events"]))
    assert counts["disconnect"] == 3
    assert counts["dns"] == 2 and counts["flap"] == 1
    assert "模拟断连" in R.conclusion(p, counts)
    lines = "\n".join(R.interpretation(p, counts))
    assert "模拟断连×3" in lines and "DNS 故障×2" in lines
    text = R.build(p, SPECS, NOTES, generated_at="t")
    assert "| 模拟断连 | 3 |" in text       # 事件汇总也用全量故障计数


def test_fault_counts_falls_back_when_field_missing():
    """老版本 payload 没有 fault_counts_full：行为必须与改动前一致。"""
    p = base_payload(events=[{"type": "dns", "msg": "模拟 DNS 故障"}])
    counts = R.summarize_events(p["events"])
    assert R.merge_fault_counts(p, counts) == counts
    assert R.merge_fault_counts({}, counts) == counts


def test_selection_prefix_is_not_a_tested_scene():
    """API 未开限速时记「选择场景：X」，报告不能把它当成已落地场景。"""
    assert R.scene_name("选择场景：4G LTE") == ""
    assert R.scene_sequence([{"msg": "选择场景：4G LTE"}]) == []
    text = R.build(base_payload(events=[{"type": "config", "msg": "选择场景：4G LTE"}]),
                   SPECS, NOTES, generated_at="t")
    assert "(仅直连，未施加弱网)" in text


# ---------------------------------------------------------------- 脱敏（S2）

def test_redact_masks_last_two_octets_and_keeps_prefix():
    """保留前两段：还能看出网段/服务商，但还原不到具体主机。"""
    assert R.redact_text("10.8.0.2 → 64.233.188.188:5228") == \
        "10.8.x.x → 64.233.x.x:5228"


def test_redact_does_not_touch_versions_or_times():
    """误伤版本号/时间比不脱敏更糟——开发会以为报告被改坏了。"""
    assert R.redact_text("v1.1.20 build 130 at 14:44:18") == "v1.1.20 build 130 at 14:44:18"


def test_redact_handles_ipv6():
    got = R.redact_text("隧道 [2001:db8::1]:443")
    assert "2001:****" in got and "db8::1" not in got


def test_build_redacts_by_default_and_marks_raw_when_off():
    ev = [{"type": "connect", "msg": "建立隧道 64.233.188.188:443"}]
    masked = R.build(base_payload(events=ev), SPECS, NOTES, generated_at="t")
    assert "64.233.x.x" in masked and "64.233.188.188" not in masked
    assert "报告已脱敏" in masked

    raw = R.build(base_payload(events=ev), SPECS, NOTES,
                  generated_at="t", redact=False)
    assert "64.233.188.188" in raw
    assert "未脱敏" in raw


def test_redact_keeps_listen_and_loopback_addresses():
    """0.0.0.0 / 127.0.0.1 不是隐私，掩码后反而像数据坏了。"""
    assert R.redact_text("监听地址 0.0.0.0:8888 → 127.0.0.1:8899") == \
        "监听地址 0.0.0.0:8888 → 127.0.0.1:8899"


@pytest.mark.parametrize("raw, want", [
    # 第三轮审查实测的两处绕过 + 版本号误伤
    ("host 10.0.0.1.", "host 10.0.x.x."),
    ("ip 1111:2222:3333:4444:5555:6666:7777:8888", "ip 1111:****"),
    ("v 1.2.3.4", "v 1.2.3.4"),
    # 正常地址 / 非地址串不得误伤
    ("192.168.1.100", "192.168.x.x"),
    ("127.0.0.1", "127.0.0.1"),
    ("0.0.0.0", "0.0.0.0"),
    ("14:44:18", "14:44:18"),
    ("mirrors.tuna.tsinghua.edu.cn", "mirrors.tuna.tsinghua.edu.cn"),
    ("v1.1.20 build 130 at 14:44:18", "v1.1.20 build 130 at 14:44:18"),
    ("10.8.0.2 → 64.233.188.188:5228", "10.8.x.x → 64.233.x.x:5228"),
])
def test_redact_text_ten_reviewed_samples(raw, want):
    """脱敏是外发报告的隐私红线：审查列出的 10 个样例逐个实测。"""
    assert R.redact_text(raw) == want


def test_redact_version_prefix_forms():
    """版本前缀的三种写法都要原样保留，且不能顺手放过真实 IP。"""
    assert R.redact_text("version 1.2.3.4") == "version 1.2.3.4"
    assert R.redact_text("版本1.2.3.4") == "版本1.2.3.4"
    assert R.redact_text("版本：1.2.3.4") == "版本：1.2.3.4"
    assert R.redact_text("V1.2.3.4.") == "V1.2.3.4."
    assert R.redact_text("v1.2.3.4.5") == "v1.2.3.4.5"  # 版本号占位保护，不被 IPv4 规则误伤


# ---------------------------------------------------------------- 第四次审查回归

@pytest.mark.parametrize("raw, want", [
    ("10.0.0.1.2", "10.0.x.x.2"),              # 五段数字串：前四段必须脱敏
    ("10.0.0.1.5.6", "10.0.x.x.5.6"),          # 六段数字串：同样不能整串漏过
    ("host 10.0.0.1.", "host 10.0.x.x."),      # 句尾句号不是 IP 的一部分
    ("192.168.1.100", "192.168.x.x"),          # 正常 IP 口径不变
    ("v 1.2.3.4", "v 1.2.3.4"),                # 版本号不受影响
    ("version 1.2.3.4.5.6", "version 1.2.3.4.5.6"),
])
def test_redact_long_number_chains_without_leaking_host(raw, want):
    assert R.redact_text(raw) == want


def test_redact_literal_sentinel_is_not_restored():
    """原文自带旧哨兵串时不得被误当成占位符改写（随机 token 消除碰撞）。"""
    raw = "v 1.2.3.4 \x00VER_0\x00"
    assert R.redact_text(raw) == raw
    raw2 = "v 1.2.3.4 \x00VER_0\x00 version 5.6.7.8 \x00VER_1\x00"
    assert R.redact_text(raw2) == raw2


def test_full_fault_counts_replace_all_types_not_only_five():
    """全量快照整体覆盖最近 60 条：connect/config/error 也用全量值（对齐 Android）。"""
    p = base_payload(
        events=([{"type": "connect"} for _ in range(4)] +
                [{"type": "config"}]),
        fault_counts_full={
            "connect": 41, "config": 7, "error": 2,
            "disconnect": 3, "dns": 1, "timeout": 0, "reject": 2, "flap": 0,
        })
    counts = R.merge_fault_counts(p, R.summarize_events(p["events"]))
    assert counts == {"connect": 41, "config": 7, "error": 2,
                      "disconnect": 3, "dns": 1, "timeout": 0,
                      "reject": 2, "flap": 0}
    text = R.build(p, SPECS, NOTES, generated_at="t")
    assert "| 建连 | 41 |" in text
    assert "| 配置变更 | 7 |" in text
    assert "| 错误 | 2 |" in text
    assert "| 模拟断连 | 3 |" in text
    assert "| 建连 | 4 |" not in text


# ---------------------------------------------------------------- 第五次审查回归（N1）

@pytest.mark.parametrize("raw, want", [
    # 版本前缀紧贴真实 IP：一律脱敏，不能因「前缀保护」整串放过
    ("v 10.0.0.1", "v 10.0.x.x"),
    ("V 10.0.0.1", "V 10.0.x.x"),
    ("version 10.0.0.1", "version 10.0.x.x"),
    ("版本 10.0.0.1", "版本 10.0.x.x"),
    ("版本：10.0.0.1", "版本：10.0.x.x"),
    ("v 192.168.1.100", "v 192.168.x.x"),
    ("version 172.16.0.1", "version 172.16.x.x"),
    ("version 10.0.0.1.2", "version 10.0.x.x.2"),  # 五段串也要脱敏前四段
    # 首段 1~9 的小数字是版本号，不是主机地址：原样保留
    ("v 1.2.3.4", "v 1.2.3.4"),
    ("v1.2.3.4", "v1.2.3.4"),
    ("version 2.3.4.5", "version 2.3.4.5"),
    ("V1.2.3.4.", "V1.2.3.4."),
    ("v1.1.20", "v1.1.20"),                       # 三段版本号
    # 非合法 IP 形态（五段 / 某段 >255）占位保护，不产出半截掩码
    ("v 10.0.0.1234", "v 10.0.0.1234"),
    ("version 65535.65535.65535.65535", "version 65535.65535.65535.65535"),
    # host. 点号紧贴：后顾只排数字，点号不能挡住 IP
    ("host.10.0.0.1", "host.10.0.x.x"),
    ("host.10.0.0.1.", "host.10.0.x.x."),
    # 长数字串 / 尾随句点 / 正常地址 / 监听 / 回环 / 全数字 IPv6 / 时间
    ("10.0.0.1.2", "10.0.x.x.2"),
    ("1.2.3.4.5", "1.2.x.x.5"),
    ("10.0.0.1", "10.0.x.x"),
    ("host 10.0.0.1.", "host 10.0.x.x."),
    ("0.0.0.0", "0.0.0.0"),
    ("127.0.0.1", "127.0.0.1"),
    ("监听地址 0.0.0.0:8888 → 127.0.0.1:8899",
     "监听地址 0.0.0.0:8888 → 127.0.0.1:8899"),
    ("ip 1111:2222:3333:4444:5555:6666:7777:8888", "ip 1111:****"),
    ("14:44:18", "14:44:18"),
])
def test_redact_n1_version_prefix_does_not_leak_real_ip(raw, want):
    assert R.redact_text(raw) == want


def test_redact_n1_leak_samples_never_contain_full_host():
    """隐私断言独立于掩码格式：完整末两段不得出现在外发文本里。"""
    for raw in ("v 10.0.0.1", "V 10.0.0.1", "version 10.0.0.1.2",
                "版本 10.0.0.1", "v 192.168.1.100", "version 172.16.0.1",
                "host.10.0.0.1", "host 10.0.0.1."):
        out = R.redact_text(raw)
        assert ".0.1" not in out and ".1.100" not in out and ".0.0.1" not in out, (raw, out)


def test_redact_n1_sentinel_with_real_ip_prefix():
    """原文自带旧哨兵串时既要防碰撞，也不能顺手放过前缀后面的真实 IP。"""
    raw = "v 10.0.0.1 \x00VER_0\x00"
    assert R.redact_text(raw) == "v 10.0.x.x \x00VER_0\x00"


# ---------------------------------------------------------------- 事件汇总过滤

ALL_TYPES = ("disconnect", "dns", "timeout", "reject", "flap",
             "config", "connect", "error", "idle", "share")


def _summary_section(text):
    return text.split("## 事件汇总", 1)[1].split("\n## ", 1)[0]


def test_empty_run_summary_has_no_zero_rows():
    """引擎给的是稠密快照（10 类全 0），报告不能把十行 0 值印出来。"""
    p = base_payload(fault_counts_full=dict.fromkeys(ALL_TYPES, 0))
    text = R.build(p, SPECS, NOTES, generated_at="t")
    section = _summary_section(text)
    assert "本次运行没有记录到事件" in section
    assert "| 0 |" not in section


def test_summary_only_lists_types_that_happened():
    full = dict.fromkeys(ALL_TYPES, 0)
    full["disconnect"] = 3
    p = base_payload(fault_counts_full=full)
    section = _summary_section(R.build(p, SPECS, NOTES, generated_at="t"))
    assert "| 模拟断连 | 3 |" in section
    assert "DNS 故障" not in section
    assert "| 0 |" not in section


# ---------------------------------------------------------------- 全角脱敏

def test_redact_fullwidth_period_and_digits():
    """全角句点 / 全角数字写法的地址也必须脱敏（实测曾原样外发）。"""
    assert R.redact_text("host 10．0．0．1") == "host 10.0.x.x"
    assert R.redact_text("host １０．０．０．１") == "host 10.0.x.x"
    assert R.redact_text("host １０.0．0.1:8080") == "host 10.0.x.x:8080"


def test_redact_fullwidth_normal_text_untouched():
    assert R.redact_text("正常文本，没有地址") == "正常文本，没有地址"
    assert R.redact_text("v1.2.3.4 与 14:44:18") == "v1.2.3.4 与 14:44:18"
