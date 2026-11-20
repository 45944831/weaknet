package com.weaknet.app;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * ReportGen.java 的纯 JDK 回归。对齐桌面版 tests/test_report_gen.py 的关键断言：
 *   1) 8 段结构齐全
 *   2) stopped-limit（测完停限速）不误报「全程直连」，且列出测过的场景序列
 *   3) 场景序列按发生先后（events 是最新在前，要翻回正序）
 *   4) 参数快照只列非默认项、带中文 label 与说明
 *   5) 脏数据/空快照不崩
 * 输入用 Map/List（android.jar 的 org.json 是 Stub，纯 JDK 跑不了）。
 */
public class _ReportGenTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name + (extra == null ? "" : "  | " + extra)); }
        else { fail++; System.out.println("  FAIL  " + name + (extra == null ? "" : "  <- " + extra)); }
    }

    static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        for (int i = 0; i + 1 < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    static Map<String, Object> profile(String name, Object... kv) {
        Map<String, Object> p = map(kv);
        p.put("name", name);
        return p;
    }

    static Map<String, Object> status() {
        Map<String, Object> s = map(
            "version", "1.1.19", "build", 129, "spec_hash", "9990e03d",
            "profile", profile("直连（不限制）"),
            "preset_key", "direct", "limit_on", false, "gate_open", true,
            "vpn_active", false,
            "listen", map("host", "127.0.0.1", "port", 8888),
            "scope", map("mode", "all", "list", new ArrayList<Object>()),
            "stats", map("total", 0L, "active", 0L, "up", 0L, "down", 0L,
                         "bypass", 0L, "uptime", 0L),
            "events", new ArrayList<Object>(),
            "generated_at", "2026-11-19 19:00:00");
        return s;
    }

    static List<Object> events(String... msgs) {
        List<Object> a = new ArrayList<Object>();
        for (String m : msgs) {
            if (m.startsWith("@")) {
                String[] p = m.substring(1).split(":", 2);
                a.add(map("type", p[0], "msg", p.length > 1 ? p[1] : "", "t", "19:00:00"));
            } else {
                a.add(map("type", "config", "msg", m, "t", "19:00:00"));
            }
        }
        return a;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> statsOf(Map<String, Object> s) {
        return (Map<String, Object>) s.get("stats");
    }

    static boolean has(String text, String... parts) {
        for (String p : parts) if (text.indexOf(p) < 0) return false;
        return true;
    }

    public static void main(String[] args) {
        System.out.println("== ReportGen 报告生成器 ==");

        // 1) 结构完整
        String r0 = ReportGen.build(status());
        ck("8 段结构齐全", has(r0, "一句话结论", "测试概况", "流量统计", "参数快照",
                "事件汇总", "口径说明", "产物指纹"), null);
        ck("表头与平台", r0.startsWith("# WeakNet 弱网测试报告") && r0.contains("| 平台 | Android |"), null);
        ck("口径说明含丢包停顿", r0.contains("不是 TCP 重传语义"), null);
        ck("指纹含 spec_hash", r0.contains("9990e03d"), null);

        // 2) stopped-limit 不误报直连（events 最新在前）
        Map<String, Object> s1 = status();
        s1.put("last_profile", profile("高丢包（30% 丢包）", "loss_pct", 30.0));
        s1.put("events", events(
                "停止限速（恢复直连）",
                "切换场景：高丢包（30% 丢包）",
                "切换场景：4G LTE",
                "切换场景：直连（不限制）"));
        statsOf(s1).put("total", 3L); statsOf(s1).put("down", 4096L);
        statsOf(s1).put("uptime", 30L);
        String r1 = ReportGen.build(s1);
        ck("停限速后不误报全程直连",
                r1.contains("本次依次测过") && !r1.contains("当前**未开启限速（直连）**，共"), null);
        ck("场景序列正序", r1.contains("直连（不限制） → 4G LTE → 高丢包（30% 丢包）"), null);
        ck("最后一次生效参数入表", r1.contains("丢包率") && r1.contains("30"), null);

        // 3) 非默认参数只列改动项、带 label 与单位
        Map<String, Object> s2 = status();
        s2.put("profile", profile("自定义", "latency_down_ms", 150, "down_kbps", 8000));
        s2.put("limit_on", true);
        String r2 = ReportGen.build(s2);
        ck("非默认参数带单位", has(r2, "下行延迟", "150 ms", "下行带宽", "8000 kbps"), null);
        ck("默认参数不占篇幅", r2.indexOf("DNS失败") < 0, null);

        // 4) 故障注入要点名（config 不算故障）
        Map<String, Object> s3 = status();
        s3.put("limit_on", true);
        s3.put("events", events("@dns:模拟 DNS 故障", "@disconnect:模拟断连", "切换场景"));
        String r3 = ReportGen.build(s3);
        ck("故障事件点名", has(r3, "DNS 故障", "模拟断连"), null);

        // 5) 主界面「开始弱网测试」与各入口组合文案都得认（真机报告暴露的 bug）
        Map<String, Object> s5 = status();
        s5.put("last_profile", profile("5G（良好覆盖）", "latency_down_ms", 20.0));
        s5.put("events", events(
                "停止弱网测试（场景保留：5g）",
                "@disconnect:模拟断连：内网网关",
                "开始弱网测试：5G（良好覆盖）"));
        statsOf(s5).put("total", 80L); statsOf(s5).put("down", 326756L);
        statsOf(s5).put("up", 153763L); statsOf(s5).put("uptime", 99L);
        String r5 = ReportGen.build(s5);
        ck("主入口场景入序列", r5.contains("5G（良好覆盖）")
                && !r5.contains("(仅直连，未施加弱网)"),
                r5.contains("(仅直连，未施加弱网)") ? "仍误报仅直连" : null);
        ck("未生效选择不算落地", ReportGen.sceneName("选择场景（未开启限速，未生效）：4G LTE").length() == 0
                && ReportGen.sceneName("开始弱网测试：5G（良好覆盖）").equals("5G（良好覆盖）"), null);
        List<String> seq = ReportGen.sceneSequence(map("events", events(
                "开始播放组合「弱网三连」：3 段",
                "飘窗切换场景：高丢包（30% 丢包）",
                "开始弱网测试：5G（良好覆盖）",
                "开始弱网测试：5G（良好覆盖）")));
        ck("主入口+组合文案正序去重", seq.size() == 3
                && seq.get(0).equals("5G（良好覆盖）")
                && seq.get(2).equals("弱网三连"), String.valueOf(seq));

        // 6) 脏数据/空快照不崩
        String r4 = ReportGen.build(new LinkedHashMap<String, Object>());
        ck("空快照不崩", r4.contains("测试报告") && r4.contains("没有记录到事件"), null);
        ck("默认文件名", ReportGen.defaultFilename("20261119_190000")
                .equals("WeakNet测试报告_20261119_190000.md"), null);

        // 7) 解读总结（给开发的一眼定位）
        ck("解读总结段存在", r0.contains("## 解读总结（给开发）")
                && has(r0, "**数据有效性**", "**关注点**", "**下一步**"), null);
        Map<String, Object> s6 = status();
        List<String> i6 = ReportGen.interpretation(s6, new LinkedHashMap<String, Integer>(),
                new ArrayList<String>(), profile("直连（不限制）"));
        String i6s = ReportGen.join(i6, "\n");
        ck("空跑判为不可用并给补测步骤", i6s.contains("不能用于评估弱网效果")
                && i6s.contains("开启接管"), null);

        Map<String, Object> s7 = status();
        s7.put("limit_on", false);
        s7.put("last_profile", profile("4G 弱网", "latency_down_ms", 150, "down_kbps", 8000));
        s7.put("events", events("开始弱网测试：4G 弱网", "@disconnect:模拟断连",
                "@error:连接重置", "@error:超时"));
        statsOf(s7).put("total", 12L); statsOf(s7).put("active", 2L);
        statsOf(s7).put("bypass", 512L); statsOf(s7).put("uptime", 30L);
        Map<String, Integer> c7 = ReportGen.summarizeEvents(s7);
        List<String> i7 = ReportGen.interpretation(s7, c7,
                ReportGen.sceneSequence(s7), ReportGen.testedProfile(s7));
        String i7s = ReportGen.join(i7, "\n");
        ck("有效判定+故障次数+参数变更+停限速提醒", i7s.contains("**有效**")
                && i7s.contains("模拟断连×1") && i7s.contains("4G 弱网")
                && i7s.contains("下行延迟=150ms") && i7s.contains("导出时限速已停止"), null);
        ck("错误事件与活跃/直通流量进关注点", i7s.contains("出现 2 次错误事件")
                && i7s.contains("活跃连接未结束") && i7s.contains("直通流量未计入"), null);

        // 8) 真机回归：场景事件被环形缓冲挤出，靠 last_profile 兜底判有效
        Map<String, Object> s8 = status();
        s8.put("limit_on", true);
        s8.put("preset_key", "high_loss");
        s8.put("last_profile", profile("高丢包（30% 丢包）", "latency_down_ms", 150.0, "loss_pct", 30.0));
        List<Object> ev8 = new ArrayList<Object>();
        for (int i = 0; i < 5; i++) ev8.add(map("type", "connect", "msg", "TCP ...", "t", "11:00:00"));
        for (int i = 0; i < 3; i++) ev8.add(map("type", "error", "msg", "连接失败", "t", "11:00:01"));
        s8.put("events", ev8);
        statsOf(s8).put("total", 121L); statsOf(s8).put("down", 2000L);
        statsOf(s8).put("up", 1000L); statsOf(s8).put("uptime", 300L);
        String r8 = ReportGen.build(s8);
        ck("事件挤出时仍判有效且用 last_profile 兜底", r8.contains("**有效**")
                && !r8.contains("可能全程直连") && r8.contains("高丢包")
                && r8.contains("环形缓冲覆盖"), null);
        ck("概况表场景不误报仅直连", !r8.contains("(仅直连，未施加弱网)"), null);

        // 9) 工具函数
        ck("humanBytes", ReportGen.humanBytes(1024).equals("1.0 KB")
                && ReportGen.humanBytes(1048576).equals("1.00 MB"), null);
        ck("humanDuration", ReportGen.humanDuration(125).equals("2 分 5 秒"), null);
        ck("cell 转义竖线", ReportGen.cell("a|b").equals("a\\|b"), null);

        System.out.println("\n结果: " + pass + " 通过 / " + fail + " 失败");
        if (fail > 0) System.exit(1);
    }
}
