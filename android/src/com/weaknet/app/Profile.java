package com.weaknet.app;

import java.util.Locale;

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

    public static final String[] KEYS = {
            "direct", "wifi", "4g", "3g", "edge", "gprs", "dialup",
            "wifi_weak", "congestion", "satellite",
            "highspeed_rail", "elevator", "flapping", "blackhole", "dns_fail", "airplane"
    };

    public static String catOf(String key) {
        if ("direct".equals(key) || "wifi".equals(key) || "4g".equals(key) || "3g".equals(key)
                || "edge".equals(key) || "gprs".equals(key) || "dialup".equals(key)) return "常规网络";
        if ("wifi_weak".equals(key) || "congestion".equals(key) || "satellite".equals(key)) return "弱网环境";
        return "极端环境";
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
        } else if ("3g".equals(key)) {
            p.name = "3G";
            p.latencyUpMs = 150; p.latencyDownMs = 200; p.jitterMs = 50;
            p.downKbps = 2000; p.upKbps = 1000;
        } else if ("edge".equals(key)) {
            p.name = "2G EDGE";
            p.latencyUpMs = 400; p.latencyDownMs = 600; p.jitterMs = 200;
            p.downKbps = 250; p.upKbps = 100; p.lossPct = 2;
        } else if ("gprs".equals(key)) {
            p.name = "2G GPRS（极慢）";
            p.latencyUpMs = 600; p.latencyDownMs = 900; p.jitterMs = 300;
            p.downKbps = 60; p.upKbps = 40; p.lossPct = 5;
        } else if ("dialup".equals(key)) {
            p.name = "56K 拨号";
            p.latencyUpMs = 150; p.latencyDownMs = 250; p.jitterMs = 40;
            p.downKbps = 56; p.upKbps = 33;
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

    /** 从自定义参数 JSON 构建（字段缺失则保持默认） */
    public static Profile custom(JSONObject o) {
        Profile p = new Profile();
        p.name = "自定义";
        p.latencyUpMs = o.optInt("latency_up_ms", 0);
        p.latencyDownMs = o.optInt("latency_down_ms", 0);
        p.jitterMs = o.optInt("jitter_ms", 0);
        p.downKbps = o.optInt("down_kbps", 0);
        p.upKbps = o.optInt("up_kbps", 0);
        p.stallMs = o.optInt("stall_ms", 600);
        p.flapSec = o.optInt("flap_sec", 0);
        p.flapBlackoutSec = o.optInt("flap_blackout_sec", 2);
        p.lossPct = o.optDouble("loss_pct", 0);
        p.disconnectPct = o.optDouble("disconnect_pct", 0);
        p.timeoutPct = o.optDouble("timeout_pct", 0);
        p.dnsFailPct = o.optDouble("dns_fail_pct", 0);
        p.rejectPct = o.optDouble("reject_pct", 0);
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

    public String summary() {
        return String.format(Locale.getDefault(),
                "延迟 %d/%dms 带宽 %s/%s kbps 丢包 %.0f%%",
                latencyUpMs, latencyDownMs,
                downKbps == 0 ? "∞" : String.valueOf(downKbps),
                upKbps == 0 ? "∞" : String.valueOf(upKbps), lossPct);
    }
}
