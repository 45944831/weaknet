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
