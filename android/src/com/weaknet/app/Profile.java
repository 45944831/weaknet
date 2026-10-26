package com.weaknet.app;

import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

import org.json.JSONObject;

/** 弱网参数模型 + 内置场景预设（与桌面版一致） */
public class Profile {
    public String name = "直连（不限制）";
    public int latencyUpMs = 0;
    public int latencyDownMs = 0;
    public int jitterMs = 0;
    public int downKbps = 0;   // 0 = 不限
    public int upKbps = 0;
    public double lossPct = 0;
    public int stallMs = 600;
    public double disconnectPct = 0;
    public double timeoutPct = 0;
    public double dnsFailPct = 0;
    public double rejectPct = 0;
    public int flapSec = 0;
    public int flapBlackoutSec = 2;

    // 2026-10 起去掉 3g / edge / gprs / dialup：这几种制式与拨号上网已退网，
    // 不再是有意义的测试档位；需要极慢网时直接用自定义参数压带宽。
    public static final String[] KEYS = {
            "direct", "wifi", "4g", "4g_weak", "5g",
            "5g_weak", "wifi_weak", "congestion", "satellite",
            "high_loss", "high_latency",
            "highspeed_rail", "elevator", "flapping", "blackhole", "dns_fail", "airplane"
    };

    /** 按钮上的短名（手机屏窄，全名会把按钮撑出卡片；与桌面版 SHORT 保持一致） */
    public static String shortOf(String key) {
        if ("direct".equals(key)) return "直连";
        if ("wifi".equals(key)) return "Wi-Fi 良好";
        if ("4g".equals(key)) return "4G LTE";
        if ("4g_weak".equals(key)) return "4G 弱网";
        if ("5g".equals(key)) return "5G 良好";
        if ("5g_weak".equals(key)) return "5G 弱覆盖";
        if ("wifi_weak".equals(key)) return "弱 Wi-Fi";
        if ("congestion".equals(key)) return "网络拥塞";
        if ("satellite".equals(key)) return "卫星链路";
        if ("highspeed_rail".equals(key)) return "高铁/地铁";
        if ("elevator".equals(key)) return "电梯/地下室";
        if ("flapping".equals(key)) return "频繁断连";
        if ("blackhole".equals(key)) return "黑洞无响应";
        if ("dns_fail".equals(key)) return "DNS 故障";
        if ("airplane".equals(key)) return "完全断网";
        if ("high_loss".equals(key)) return "高丢包";
        if ("high_latency".equals(key)) return "高延迟";
        return "";
    }

    public static String catOf(String key) {
        if ("direct".equals(key) || "wifi".equals(key) || "4g".equals(key)
                || "5g".equals(key)) return "常规网络";
        if ("4g_weak".equals(key) || "5g_weak".equals(key) || "wifi_weak".equals(key)
                || "congestion".equals(key) || "satellite".equals(key)
                || "high_loss".equals(key) || "high_latency".equals(key)) return "弱网环境";
        return "极端环境";
    }

    /**
     * 每支内置档位的注释：界面上的「！」入口把它们列出来（见 NotesSheet）。
     *
     * 为什么不复用参数值自动生成：光看数字（延迟 110ms / 3Mbps / 丢包 4%）分不清
     * 「4G 弱网」和「弱 Wi-Fi」到底差在哪，也看不出该用哪一支去复现某个 bug。
     * 这里写的是「模拟的什么现场 + 典型症状」，参数摘要由 {@link #detail()} 现算。
     * 文案与桌面版 weaknet.py 的 PRESET_NOTES 保持一致，两端别各说一套。
     */
    private static final Map<String, String> NOTES = new HashMap<String, String>();

    static {
        NOTES.put("direct",
                "不做任何处理，流量原样直通。用来确认「接管本身没问题」，也是跟其它场景对比的基线。");
        NOTES.put("wifi",
                "家用 / 办公 Wi-Fi 的典型值：延迟几十毫秒、带宽几十 Mbps。功能验证和性能对比的基准环境。");
        NOTES.put("4g",
                "移动网络常规体验：RTT 40~60ms、下行 20Mbps。模拟户外 / 地铁里信号正常的 4G。");
        NOTES.put("5g",
                "空口延迟比 4G 低一个量级（RTT 10~20ms），下行 100Mbps。能触发「秒开」级体验，"
                        + "也够跑满多数 App 的预加载。");
        NOTES.put("4g_weak",
                "地下车库 / 城中村 / 演唱会现场这类「有 4G 图标但速率塌了」的现场。"
                        + "速率比 5G 弱覆盖再低一档，丢包与偶发断流更明显 —— 4G 的重传比 5G 慢。");
        NOTES.put("5g_weak",
                "室内深处 / 小区边缘 / 地铁口：还能驻留，但速率塌到几 Mbps，伴随少量丢包与偶发断流。"
                        + "比弱 Wi-Fi 更容易出现「看着有信号、实际传不动」。");
        NOTES.put("wifi_weak",
                "隔墙 / 离路由器远：信号还在，但速率与稳定性一起掉，抖动明显（±60ms）。"
                        + "适合复现「Wi-Fi 满格却一直转圈」。");
        NOTES.put("congestion",
                "晚高峰 / 多人共享带宽：延迟翻倍、下行被压到 512kbps。复现「点一下要转半天圈」。");
        NOTES.put("satellite",
                "往返 700ms、带宽 2Mbps。典型表现是「每个请求都要等好几秒才回来」，"
                        + "用来验证加载态、超时与重试。");
        NOTES.put("high_loss",
                "带宽看着够（能起连、能握手），但每三个包丢一个。典型症状：视频卡成 PPT、"
                        + "图片加载一半、长连接反复重试。");
        NOTES.put("high_latency",
                "带宽正常、也不丢包，纯粹往返慢（跨境链路、卫星回传、代理中转）。"
                        + "复现的是「慢但有结果」，不是「传不动」。");
        NOTES.put("highspeed_rail",
                "高速移动下频繁切换基站：抖动极大（±150ms）、偶发断流、带宽低。"
                        + "适合验证重连与断点续传。");
        NOTES.put("elevator",
                "信号极差：延迟接近秒级、20% 丢包、10% 断连，常伴超时。"
                        + "最容易触发「请求卡死」的一档。");
        NOTES.put("flapping",
                "每 8 秒断网 3 秒，模拟网络反复切换 / 弱信号掉线。"
                        + "专门用来验证重连、重试与断线提示逻辑。");
        NOTES.put("blackhole",
                "请求发出去没有任何响应，连接挂住不返回 —— 不是报错，是一直等。"
                        + "用来验证超时、取消与兜底 UI。");
        NOTES.put("dns_fail",
                "域名解析全部失败，但直接用 IP 的连接仍然通 —— 只拦 DNS 查询，不拦已建立的数据通道。");
        NOTES.put("airplane",
                "所有新连接立即被拒绝（是立刻失败，不是挂住）。模拟飞行模式 / 完全无网络，"
                        + "验证快速失败与降级路径。");
    }

    /** 档位注释；没有注释时返回空串（界面据此跳过这一行） */
    public static String noteOf(String key) {
        String s = key == null ? null : NOTES.get(key);
        return s == null ? "" : s;
    }

    public static Profile build(String key) {
        Profile p = new Profile();
        if ("wifi".equals(key)) {
            p.name = "Wi-Fi 良好";
            p.latencyUpMs = 10; p.latencyDownMs = 30; p.jitterMs = 5;
            p.downKbps = 50000; p.upKbps = 20000;
        } else if ("4g".equals(key)) {
            p.name = "4G LTE";
            p.latencyUpMs = 40; p.latencyDownMs = 60; p.jitterMs = 15;
            p.downKbps = 20000; p.upKbps = 10000;
        } else if ("4g_weak".equals(key)) {
            // 4G 弱覆盖：地下车库 / 城中村 / 演唱会现场这类「有 4G 图标但速率塌了」的场景，
            // 速率比 5G 弱覆盖再低一档，丢包与偶发断流更明显（4G 的重传比 5G 慢）
            p.name = "4G 弱网（边缘/室内）";
            p.latencyUpMs = 70; p.latencyDownMs = 110; p.jitterMs = 45;
            p.downKbps = 3000; p.upKbps = 800; p.lossPct = 4;
            p.disconnectPct = 1; p.stallMs = 1200;
        } else if ("5g".equals(key)) {
            p.name = "5G（良好覆盖）";
            p.latencyUpMs = 12; p.latencyDownMs = 20; p.jitterMs = 8;
            p.downKbps = 100000; p.upKbps = 40000;
        } else if ("5g_weak".equals(key)) {
            p.name = "5G 弱覆盖（室内/边缘）";
            p.latencyUpMs = 50; p.latencyDownMs = 80; p.jitterMs = 35;
            p.downKbps = 6000; p.upKbps = 1500; p.lossPct = 2;
            p.disconnectPct = 1; p.stallMs = 1000;
        } else if ("wifi_weak".equals(key)) {
            p.name = "弱 Wi-Fi（隔墙/远端）";
            p.latencyUpMs = 60; p.latencyDownMs = 120; p.jitterMs = 60;
            p.downKbps = 5000; p.upKbps = 2000; p.lossPct = 3;
        } else if ("congestion".equals(key)) {
            p.name = "网络拥塞";
            p.latencyUpMs = 200; p.latencyDownMs = 400; p.jitterMs = 100;
            p.downKbps = 512; p.upKbps = 256; p.lossPct = 5;
        } else if ("satellite".equals(key)) {
            p.name = "卫星链路（高延迟）";
            p.latencyUpMs = 700; p.latencyDownMs = 700; p.jitterMs = 80;
            p.downKbps = 2000; p.upKbps = 512;
        } else if ("high_loss".equals(key)) {
            // 高丢包：带宽看着够（能起连、能握手），但每三个包就丢一个 ——
            // 典型症状是视频卡成 PPT、图片加载到一半、长连接反复重试
            p.name = "高丢包（30% 丢包）";
            p.latencyUpMs = 100; p.latencyDownMs = 150; p.jitterMs = 80;
            p.downKbps = 8000; p.upKbps = 3000; p.lossPct = 30; p.stallMs = 800;
        } else if ("high_latency".equals(key)) {
            // 高延迟：带宽正常、也不丢包，纯粹是往返慢（跨境链路、卫星回传、代理中转），
            // 用来复现「点一下要等半天才有反应」而不是「传不动」
            p.name = "高延迟（RTT 800ms）";
            p.latencyUpMs = 400; p.latencyDownMs = 400; p.jitterMs = 60;
            p.downKbps = 10000; p.upKbps = 5000;
        } else if ("highspeed_rail".equals(key)) {
            p.name = "高铁 / 地铁（高速移动）";
            p.latencyUpMs = 60; p.latencyDownMs = 100; p.jitterMs = 150;
            p.downKbps = 1500; p.upKbps = 512; p.lossPct = 8;
            p.disconnectPct = 2; p.stallMs = 1200;
        } else if ("elevator".equals(key)) {
            p.name = "电梯 / 地下室（信号极差）";
            p.latencyUpMs = 500; p.latencyDownMs = 800; p.jitterMs = 300;
            p.downKbps = 256; p.upKbps = 128; p.lossPct = 20;
            p.disconnectPct = 10; p.stallMs = 2000;
        } else if ("flapping".equals(key)) {
            p.name = "频繁断连（网络切换）";
            p.latencyUpMs = 80; p.latencyDownMs = 120; p.jitterMs = 50;
            p.downKbps = 8000; p.upKbps = 4000; p.disconnectPct = 5;
            p.flapSec = 8; p.flapBlackoutSec = 3;
        } else if ("blackhole".equals(key)) {
            p.name = "黑洞（请求无响应）"; p.timeoutPct = 100;
        } else if ("dns_fail".equals(key)) {
            p.name = "DNS 故障（无法解析域名）"; p.dnsFailPct = 100;
        } else if ("airplane".equals(key)) {
            p.name = "完全断网（立即拒连）"; p.rejectPct = 100;
        } else {
            p.name = "直连（不限制）";
        }
        return p;
    }

    /** 从自定义参数 JSON 构建（字段缺失则保持默认，越界值夹到合理区间） */
    public static Profile custom(JSONObject o) {
        Profile p = new Profile();
        p.name = "自定义";
        p.latencyUpMs = num(o.optInt("latency_up_ms", 0), 10000);
        p.latencyDownMs = num(o.optInt("latency_down_ms", 0), 10000);
        p.jitterMs = num(o.optInt("jitter_ms", 0), 5000);
        p.downKbps = num(o.optInt("down_kbps", 0), 1000000);
        p.upKbps = num(o.optInt("up_kbps", 0), 1000000);
        p.stallMs = num(o.optInt("stall_ms", 600), 60000);
        p.flapSec = num(o.optInt("flap_sec", 0), 3600);
        p.flapBlackoutSec = num(o.optInt("flap_blackout_sec", 2), 600);
        p.lossPct = pct(o.optDouble("loss_pct", 0));
        p.disconnectPct = pct(o.optDouble("disconnect_pct", 0));
        p.timeoutPct = pct(o.optDouble("timeout_pct", 0));
        p.dnsFailPct = pct(o.optDouble("dns_fail_pct", 0));
        p.rejectPct = pct(o.optDouble("reject_pct", 0));
        return p;
    }

    /**
     * 百分比字段夹到 0~100。999% 丢包实际就是「全丢」，和直连故障看不出区别，
     * 用户填的时候多半是想写 99% 或漏看了输入框上限。
     */
    private static double pct(double v) {
        if (v != v) return 0;                 // NaN
        return v < 0 ? 0 : (v > 100 ? 100 : v);
    }

    private static int num(int v, int max) {
        return v < 0 ? 0 : (v > max ? max : v);
    }

    /**
     * 深拷贝。自定义场景每次应用都要给出独立实例：
     * 直接复用存下来的那份，后续任何原地修改都会连带改掉「已保存的参数」。
     */
    public Profile copy() {
        Profile p = Profile.custom(this.toJson());
        p.name = name;
        return p;
    }

    public JSONObject toJson() {
        JSONObject o = new JSONObject();
        try {
            o.put("name", name);
            o.put("latency_up_ms", latencyUpMs);
            o.put("latency_down_ms", latencyDownMs);
            o.put("jitter_ms", jitterMs);
            o.put("down_kbps", downKbps);
            o.put("up_kbps", upKbps);
            o.put("loss_pct", lossPct);
            o.put("stall_ms", stallMs);
            o.put("disconnect_pct", disconnectPct);
            o.put("timeout_pct", timeoutPct);
            o.put("dns_fail_pct", dnsFailPct);
            o.put("reject_pct", rejectPct);
            o.put("flap_sec", flapSec);
            o.put("flap_blackout_sec", flapBlackoutSec);
        } catch (Exception e) {
            // ignore
        }
        return o;
    }

    /**
     * 与全默认（直连）相比，这组参数是否真的会「动」流量。
     * stallMs 默认 600ms，本身不构成限制，不参与比较。
     */
    public boolean hasLimits() {
        Profile d = new Profile();
        return latencyUpMs != d.latencyUpMs || latencyDownMs != d.latencyDownMs
                || jitterMs != d.jitterMs || downKbps != d.downKbps || upKbps != d.upKbps
                || lossPct != d.lossPct || disconnectPct != d.disconnectPct
                || timeoutPct != d.timeoutPct || dnsFailPct != d.dnsFailPct
                || rejectPct != d.rejectPct || flapSec != d.flapSec;
    }

    public String summary() {
        return String.format(Locale.getDefault(),
                "延迟 %d/%dms 带宽 %s/%s kbps 丢包 %.0f%%",
                latencyUpMs, latencyDownMs,
                downKbps == 0 ? "∞" : String.valueOf(downKbps),
                upKbps == 0 ? "∞" : String.valueOf(upKbps), lossPct);
    }

    /**
     * 参数全量摘要（含抖动 / 卡顿 / 周期断网），给「场景说明」用。
     *
     * 与 {@link #summary()} 分开：那个是列表里的一行简述，越长越挤；这里是详情页，
     * 用户点进来就是想搞清「这支档位到底设了什么」，宁可全一点。
     */
    public String detail() {
        StringBuilder sb = new StringBuilder();
        if (downKbps != 0 || upKbps != 0) {
            add(sb, "下行 " + (downKbps == 0 ? "∞" : String.valueOf(downKbps))
                    + " / 上行 " + (upKbps == 0 ? "∞" : String.valueOf(upKbps)) + " kbps");
        }
        if (latencyDownMs != 0 || latencyUpMs != 0) {
            add(sb, "延迟 " + latencyDownMs + "/" + latencyUpMs + " ms（下/上）");
        }
        if (jitterMs != 0) add(sb, "抖动 ±" + jitterMs + " ms");
        if (lossPct > 0) {
            add(sb, "丢包 " + pctText(lossPct) + "%");
            // 卡顿时长只在丢包后生效（模拟 TCP 重传等待），单独列出来没意义
            if (stallMs > 0) add(sb, "丢包后停顿 " + stallMs + " ms");
        }
        if (disconnectPct > 0) add(sb, "随机断连 " + pctText(disconnectPct) + "%");
        if (timeoutPct > 0) add(sb, "黑洞 " + pctText(timeoutPct) + "%");
        if (dnsFailPct > 0) add(sb, "DNS 故障 " + pctText(dnsFailPct) + "%");
        if (rejectPct > 0) add(sb, "拒连 " + pctText(rejectPct) + "%");
        if (flapSec > 0) add(sb, "每 " + flapSec + "s 断网 " + flapBlackoutSec + "s");
        return sb.length() == 0 ? "（不限制：所有流量原样直通）" : sb.toString();
    }

    private static void add(StringBuilder sb, String part) {
        if (sb.length() > 0) sb.append(" · ");
        sb.append(part);
    }

    /** 百分比去掉多余的小数位：4.0 → "4"，0.5 → "0.5" */
    private static String pctText(double v) {
        if (v == Math.rint(v)) return String.valueOf((int) v);
        return String.valueOf(Math.round(v * 10) / 10.0);
    }
}
