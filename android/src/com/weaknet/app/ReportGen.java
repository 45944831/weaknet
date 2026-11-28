package com.weaknet.app;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 弱网测试报告生成器（Android 端）。
 *
 * 与桌面版 report_gen.py 对齐：同样的段落结构（含「解读总结（给开发）」诊断段）、
 * 同样的「一句话结论会点出测过哪几档」、同样的 stopped-limit 修正
 * （测完停限速再导出，不能误报「全程直连」）。
 *
 * 纯逻辑类：输入是 Map/List（不是 JSONObject——android.jar 的 org.json 是 Stub，
 * 纯 JDK 单测跑不了），不碰任何 Android UI / 全局状态，因此纯 JDK 就能单测
 * （见 _ReportGenTest.java）。UI 层负责把 WeakNet.I 状态组装成 Map 传进来。
 *
 * 快照结构（与 /api/status 同构；缺失项按默认值容错，不抛异常）：
 *   version, build, spec_hash, profile, last_profile, preset_key,
 *   limit_on, gate_open, vpn_active, listen{host,port}, scope{mode,list},
 *   stats{total,active,up,down,bypass,uptime}, events[{t,type,msg}...]
 */
public final class ReportGen {
    private ReportGen() {}

    /** 事件类型 -> 中文名；未列出的原样显示 */
    private static final Map<String, String> EVENT_LABELS = new LinkedHashMap<String, String>();
    static {
        EVENT_LABELS.put("config", "配置变更");
        EVENT_LABELS.put("connect", "建连");
        EVENT_LABELS.put("disconnect", "模拟断连");
        EVENT_LABELS.put("dns", "DNS 故障");
        EVENT_LABELS.put("timeout", "黑洞超时");
        EVENT_LABELS.put("reject", "模拟拒连");
        EVENT_LABELS.put("flap", "网络切换");
        EVENT_LABELS.put("error", "错误");
        EVENT_LABELS.put("idle", "空闲断开");
        EVENT_LABELS.put("share", "环境共享");
    }

    /** 这些事件算「主动注入的故障」，一句话结论要点出来 */
    private static final String[] FAULT_TYPES =
            {"disconnect", "dns", "timeout", "reject", "flap"};

    /** 固定口径说明——与桌面版逐字一致，避免两端解读口径漂移 */
    private static final String[] CAVEATS = {
        "延迟是**逐包注入**的，不是只在建连时加一次；一次往返约等于上行延迟 + 下行延迟。",
        "丢包由停顿（`stall_ms`）实现，**不是 TCP 重传语义**——别按重传模型去推断吞吐。",
        "上下行字节是引擎对**已接管连接**的计数，不含直通（未接管）流量；有会话基线时只统计本次测试的增量。",
        "五类故障（断连 / DNS / 超时 / 拒连 / 网络切换）取**累计计数**，不受 300 条事件环形缓冲影响；其余事件仍是最新 300 条的快照。",
        "运行时长是**引擎本次启动**至今的时间；「测试会话」的已运行时间才是本次测试的时长。"
    };

    // ------------------------------------------------------------------ 工具

    @SuppressWarnings("unchecked")
    static Map<String, Object> asMap(Object v) {
        return (v instanceof Map) ? (Map<String, Object>) v : null;
    }

    @SuppressWarnings("unchecked")
    static List<Object> asList(Object v) {
        return (v instanceof List) ? (List<Object>) v : null;
    }

    static String humanBytes(long n) {
        if (n < 1024) return n + " B";
        if (n < 1024L * 1024) return String.format(Locale.US, "%.1f KB", n / 1024.0);
        if (n < 1024L * 1024 * 1024)
            return String.format(Locale.US, "%.2f MB", n / (1024.0 * 1024.0));
        return String.format(Locale.US, "%.2f GB", n / (1024.0 * 1024.0 * 1024.0));
    }

    static String humanDuration(long sec) {
        if (sec < 60) return sec + " 秒";
        if (sec < 3600) return (sec / 60) + " 分 " + (sec % 60) + " 秒";
        return (sec / 3600) + " 小时 " + ((sec % 3600) / 60) + " 分";
    }

    /** 秒级时间戳 -> HH:mm:ss（报告里展示会话开始时刻）；非法值返回 "-"。 */
    static String clock(Map<String, Object> m, String key) {
        long sec = l(m, key);
        if (sec <= 0) return "-";
        return new java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault())
                .format(new java.util.Date(sec * 1000L));
    }

    /** Markdown 单元格：转义竖线与换行，避免撑破表格 */
    static String cell(Object v) {
        if (v == null) return "";
        return String.valueOf(v).replace("|", "\\|").replace("\n", " ").trim();
    }

    static long l(Map<String, Object> o, String key) {
        if (o == null) return 0;
        Object v = o.get(key);
        return (v instanceof Number) ? ((Number) v).longValue() : 0;
    }

    static boolean b(Map<String, Object> o, String key) {
        if (o == null) return false;
        Object v = o.get(key);
        return (v instanceof Boolean) ? (Boolean) v : false;
    }

    static String s(Map<String, Object> o, String key, String dflt) {
        if (o == null) return dflt;
        Object v = o.get(key);
        return v == null ? dflt : String.valueOf(v);
    }

    // ------------------------------------------------------------------ 快照语义

    /** 本次真正测过的场景参数：优先 last_profile（停止限速后 profile 会回直连） */
    static Map<String, Object> testedProfile(Map<String, Object> status) {
        Map<String, Object> last = asMap(status == null ? null : status.get("last_profile"));
        if (last != null && !last.isEmpty()) return last;
        Map<String, Object> cur = asMap(status == null ? null : status.get("profile"));
        return cur == null ? new LinkedHashMap<String, Object>() : cur;
    }

    /** 场景「真正落地」的事件文案前缀：切场景、开限速、主界面「▶ 开始弱网测试」、
     *  飘窗切场景。只认老两个前缀会漏掉主入口，导致结论说测了 5G、概况却写仅直连。
     *  带「（未开启限速，未生效）」的选择类文案不算落地，不在此列。 */
    static final String[] SCENE_PREFIXES = {"切换场景：", "开启限速：", "开始弱网测试：", "飘窗切换场景："};
    /** 只选中、没生效的文案（CtrlServer /api/preset 未开限速时记这个）。
     *  显式排除，避免以后有人把它加进 SCENE_PREFIXES 造成「没测也算测过」。 */
    static final String[] NON_LANDED_PREFIXES = {"选择场景："};
    static final String[] COMBO_PREFIXES = {"开始播放组合：", "继续播放组合：", "播放组合：", "飘窗播放组合："};

    /** 从事件文案取落地场景名；组合名若在「」内只取括号内部分。 */
    static String sceneName(String msg) {
        if (msg == null || msg.length() == 0) return "";
        for (String prefix : NON_LANDED_PREFIXES)
            if (msg.startsWith(prefix)) return "";
        for (String prefix : SCENE_PREFIXES)
            if (msg.startsWith(prefix)) return msg.substring(prefix.length()).trim();
        for (String prefix : COMBO_PREFIXES) {
            if (msg.startsWith(prefix)) {
                String rest = msg.substring(prefix.length()).trim();
                if (rest.startsWith("「") && rest.indexOf('」') > 0)
                    return rest.substring(1, rest.indexOf('」')).trim();
                return rest;
            }
        }
        if (msg.startsWith("开始播放组合「") || msg.startsWith("继续播放组合「")) {
            int a = msg.indexOf('「'), b = msg.indexOf('」');
            if (a >= 0 && b > a) return msg.substring(a + 1, b).trim();
        }
        return "";
    }

    /** 从事件流提取测过的场景序列（去重、按发生先后）。
     *  /api/status 的 events 是「最新在前」，这里翻回正序。 */
    static List<String> sceneSequence(Map<String, Object> status) {
        List<String> names = new ArrayList<String>();
        List<Object> evs = asList(status == null ? null : status.get("events"));
        if (evs == null) return names;
        for (int i = evs.size() - 1; i >= 0; i--) {
            Map<String, Object> e = asMap(evs.get(i));
            if (e == null) continue;
            String name = sceneName(s(e, "msg", ""));
            if (name.length() > 0 && !names.contains(name)) names.add(name);
        }
        return names;
    }

    /**
     * 事件按类型聚合计数。
     *
     * /api/status 的 events 只给最近 60 条，故障计数会被截断（C4）。CtrlServer 与
     * 桌面端会在 status 里带 fault_counts_full（全量 300 条按 type 聚合）：有就用
     * 它，没有（老快照/旧服务端）才回退到 events 统计。
     */
    static Map<String, Integer> summarizeEvents(Map<String, Object> status) {
        Map<String, Integer> counts = new LinkedHashMap<String, Integer>();
        Map<String, Object> full = asMap(status == null ? null : status.get("fault_counts_full"));
        if (full != null && !full.isEmpty()) {
            for (Map.Entry<String, Object> e : full.entrySet()) {
                Object v = e.getValue();
                if (v instanceof Number) {
                    counts.put(e.getKey(), ((Number) v).intValue());
                } else if (v != null) {
                    try {
                        counts.put(e.getKey(), Integer.parseInt(String.valueOf(v)));
                    } catch (NumberFormatException ignore) {
                        // 脏值跳过，不让报告生成失败
                    }
                }
            }
        } else {
            List<Object> evs = asList(status == null ? null : status.get("events"));
            if (evs != null) {
                for (Object item : evs) {
                    Map<String, Object> e = asMap(item);
                    String t = e == null ? "?" : s(e, "type", "?");
                    Integer c = counts.get(t);
                    counts.put(t, c == null ? 1 : c + 1);
                }
            }
        }
        // 有测试会话时，五类故障覆盖成「本次」的次数：报告是给这次测试看的，
        // 把上一次测试留下的故障算进来会直接改变有效性结论。
        Map<String, Object> session = asMap(status == null ? null : status.get("session"));
        if (session != null && l(session, "seq") > 0) {
            Map<String, Object> faults = asMap(session.get("faults"));
            if (faults != null) {
                for (String t : FAULT_TYPES) {
                    Object v = faults.get(t);
                    if (v instanceof Number) {
                        counts.put(t, Math.max(0, ((Number) v).intValue()));
                    }
                }
            }
        }
        return counts;
    }

    /** 本次测试会话快照；没有（老快照 / 未开始测试）返回 null。 */
    static Map<String, Object> sessionOf(Map<String, Object> status) {
        Map<String, Object> s = asMap(status == null ? null : status.get("session"));
        return (s != null && l(s, "seq") > 0) ? s : null;
    }

    /**
     * 报告统计口径：有会话增量就用增量，否则退回引擎累计值。
     * 键与 stats 一致，调用处不需要区分两种快照。
     */
    static Map<String, Object> countedStats(Map<String, Object> status) {
        Map<String, Object> raw = asMap(status == null ? null : status.get("stats"));
        Map<String, Object> s = sessionOf(status);
        if (s == null) return raw == null ? new LinkedHashMap<String, Object>() : raw;
        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("total", Long.valueOf(l(s, "total")));
        out.put("up", Long.valueOf(l(s, "up")));
        out.put("down", Long.valueOf(l(s, "down")));
        out.put("bypass", Long.valueOf(l(s, "bypass")));
        out.put("active", Long.valueOf(raw == null ? 0L : l(raw, "active")));
        // 引擎运行时长保持原值；测试会话时长单独在「测试会话」一行展示。
        out.put("uptime", Long.valueOf(raw == null ? 0L : l(raw, "uptime")));
        return out;
    }

    static String labelOf(String type) {
        String v = EVENT_LABELS.get(type);
        return v == null ? type : v;
    }

    static String conclusion(Map<String, Object> status, Map<String, Integer> counts,
                             List<String> scenes, Map<String, Object> prof) {
        Map<String, Object> stats = countedStats(status);
        long total = l(stats, "total"), active = l(stats, "active");
        String name = s(prof, "name", "未命名场景");

        List<String> injected = new ArrayList<String>();
        // 桌面版用真值判定：fault_counts_full 预填的 0 值键不代表注入过故障。
        for (String t : FAULT_TYPES) {
            Integer v = counts.get(t);
            if (v != null && v > 0) injected.add(labelOf(t));
        }
        String faultPhrase = injected.isEmpty() ? "" : ("注入了" + join(injected, "、") + "，");

        String head;
        if (scenes.size() > 1) {
            head = "本次依次测过 " + join(scenes.subList(0, Math.min(5, scenes.size())), " → ") + "，";
            if (faultPhrase.length() > 0) head += faultPhrase;
        } else if (scenes.size() == 1) {
            head = "在「" + cell(scenes.get(0)) + "」下" +
                   (faultPhrase.length() > 0 ? faultPhrase : "运行，");
        } else if (faultPhrase.length() > 0) {
            head = (name.length() > 0 ? "在「" + cell(name) + "」下" : "本次") + faultPhrase;
        } else if (b(status, "limit_on")) {
            head = "在「" + cell(name) + "」下运行，";
        } else {
            head = "当前**未开启限速（直连）**，";
        }

        if (total <= 0)
            return head + "本次尚未捕获到经过代理的连接，报告仅含配置与状态快照。";
        return head + "共捕获 " + total + " 条连接（活跃 " + active + " 条），下行 " +
               humanBytes(l(stats, "down")) + " / 上行 " + humanBytes(l(stats, "up")) +
               "，引擎运行 " + humanDuration(l(stats, "uptime")) + "。";
    }

    static String join(List<String> parts, String sep) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < parts.size(); i++) {
            if (i > 0) sb.append(sep);
            sb.append(parts.get(i));
        }
        return sb.toString();
    }

    /**
     * 给开发的解读总结（与桌面版 report_gen.interpretation 对齐）：
     * 一句话结论复述事实，这里做诊断——有效性判定 / 测了什么 / 关注点 / 下一步。
     * 返回 Markdown 行列表；纯逻辑，不碰 Android UI，纯 JDK 可单测。
     */
    static List<String> interpretation(Map<String, Object> status, Map<String, Integer> counts,
                                       List<String> scenes, Map<String, Object> prof) {
        Map<String, Object> stats = countedStats(status);
        long total = l(stats, "total"), active = l(stats, "active"), bypass = l(stats, "bypass");
        boolean limitOn = b(status, "limit_on");

        List<String> injected = new ArrayList<String>();   // 「故障名×次数」
        // 与桌面版一致按真值判定：0 值键不能混进「注入了 N 类故障」。
        for (String t : FAULT_TYPES) {
            Integer v = counts.get(t);
            if (v != null && v > 0) injected.add(labelOf(t) + "×" + v);
        }
        int errors = counts.containsKey("error") ? counts.get("error") : 0;

        List<String> lines = new ArrayList<String>();

        // 非默认参数摘要（与参数快照同口径：SpecGen 的 label/unit/default）
        List<String> changed = new ArrayList<String>();
        for (String key : SpecGen.PARAM_KEYS) {
            if (!prof.containsKey(key)) continue;
            Object val = prof.get(key);
            if (sameValue(val, Integer.valueOf(SpecGen.paramDefault(key)))) continue;
            changed.add(cell(SpecGen.paramLabel(key)) + "=" + cell(val) + cell(SpecGen.paramUnit(key)));
        }

        // 是否真施加过弱网：不能只看场景序列——环形缓冲（300 条）会被连接/错误挤出
        // （真机 121 连接 + 24 错误把开头的「开始弱网测试」挤掉了）。有非默认参数
        // 或非直连的 last_profile 也是确凿证据，与 conclusion 口径一致。
        String profName = s(prof, "name", "");
        boolean hasWeaknet = !scenes.isEmpty() || !injected.isEmpty() || !changed.isEmpty()
                || (profName.length() > 0 && profName.indexOf("直连") < 0);

        // 1. 有效性判定
        String verdict;
        if (total <= 0) {
            verdict = "empty";
            lines.add("**数据有效性**：本次未捕获到经过代理的连接，报告仅含配置与状态快照，"
                    + "**不能用于评估弱网效果**——需让被测 App 的流量真正经过代理后再导出。");
            if (sessionOf(status) != null) {
                long history = l(asMap(status.get("stats")), "total");
                if (history > 0) {
                    // 旧流量冒充本次样本的典型场景：累计有连接，但都是开始测试前的。
                    lines.add("> 引擎累计有 " + history + " 条历史连接，但**本次测试开始后没有新流量**；"
                            + "历史连接不计入本次样本。");
                }
            }
        } else if (!hasWeaknet) {
            verdict = "direct";
            lines.add("**数据有效性**：捕获 " + total + " 条连接，但未识别到弱网场景或故障注入，"
                    + "可能全程直连；请确认已施加非默认场景后再测。");
        } else {
            verdict = "valid";
            lines.add("**数据有效性**：**有效**——捕获 " + total + " 条连接"
                    + (injected.isEmpty() ? "" : "，注入 " + join(injected, "、"))
                    + "，可用于评估该场景下的表现。");
        }
        Map<String, Object> sess = sessionOf(status);
        if (sess != null) {
            lines.add("> 统计口径：自测试会话 #" + l(sess, "seq") + " 开始（"
                    + clock(sess, "started_at") + "）的**增量**，同一次测试内切场景不重置；"
                    + "引擎累计值仅作参考。");
        }

        // 2. 测过哪些场景
        if (!scenes.isEmpty()) {
            String tail = limitOn ? "" : "；导出时已停限速，下列参数取最后一次生效的场景";
            lines.add("**测过场景**：" + cell(join(scenes.subList(0, Math.min(5, scenes.size())), " → "))
                    + tail + "。");
        } else if (profName.length() > 0 && profName.indexOf("直连") < 0) {
            lines.add("**测过场景**：" + cell(profName) + "（事件记录已被环形缓冲覆盖，取最后一次生效场景）。");
        } else if (!injected.isEmpty()) {
            lines.add("**测过场景**：未识别到具体场景名，但记录到故障注入，参数见快照。");
        }

        // 3. 参数变更摘要
        if (!changed.isEmpty())
            lines.add("**参数变更**：" + cell(join(changed.subList(0, Math.min(6, changed.size())), "、")) + "。");

        // 4. 关注点
        List<String> notes = new ArrayList<String>();
        if (errors > 0) notes.add("出现 " + errors + " 次错误事件，建议查「事件汇总」中的具体报错");
        if (!injected.isEmpty()) notes.add("含 " + injected.size() + " 类故障注入（属预期，非崩溃）");
        if (hasWeaknet && !limitOn) notes.add("导出时限速已停止，概况表显示直连属正常");
        if (active > 0) notes.add("导出时仍有 " + active + " 条活跃连接未结束");
        if (bypass > 0) notes.add("有 " + humanBytes(bypass) + " 直通流量未计入上下行");
        lines.add("**关注点**：" + (notes.isEmpty() ? "无异常信号" : join(notes, "；")) + "。");

        // 5. 下一步
        String nxt;
        if ("empty".equals(verdict)) nxt = "开启接管 → 选场景 → 点开始 → 跑真实请求 → 再导出";
        else if ("direct".equals(verdict)) nxt = "确认已施加非默认场景，避免只跑直连";
        else nxt = "结合「参数快照」与「事件时间线」定位具体表现";
        lines.add("**下一步**：" + nxt + "。");

        return lines;
    }

    // ------------------------------------------------------------------ 主入口

    /** 把状态快照渲染成 Markdown 报告。任何字段缺失都容错，不抛异常。 */
    public static String build(Map<String, Object> status) {
        if (status == null) status = new LinkedHashMap<String, Object>();
        Map<String, Object> stats = asMap(status.get("stats"));
        Map<String, Object> scope = asMap(status.get("scope"));
        Map<String, Object> listen = asMap(status.get("listen"));
        Map<String, Object> prof = testedProfile(status);
        List<String> scenes = sceneSequence(status);
        Map<String, Integer> counts = summarizeEvents(status);

        StringBuilder o = new StringBuilder();
        o.append("# WeakNet 弱网测试报告\n\n");
        o.append("> 生成时间：").append(cell(s(status, "generated_at", ""))).append("\n\n");

        // 1. 一句话结论
        o.append("## 一句话结论\n\n")
         .append(conclusion(status, counts, scenes, prof)).append("\n\n");

        // 2. 解读总结（给开发的一眼定位：有效性 / 测了什么 / 关注点 / 下一步）
        o.append("## 解读总结（给开发）\n\n");
        for (String ln : interpretation(status, counts, scenes, prof)) o.append(ln).append("\n");
        o.append("\n");

        // 3. 测试概况
        o.append("## 测试概况\n\n| 项 | 值 |\n|---|---|\n");
        o.append("| 平台 | Android |\n");
        o.append("| 版本 | v").append(cell(s(status, "version", "-")))
         .append("（build ").append(cell(s(status, "build", "-"))).append("） |\n");
        o.append("| 规格哈希 | `").append(cell(s(status, "spec_hash", "-"))).append("` |\n");
        String pn = s(prof, "name", "");
        String scenesCell = !scenes.isEmpty() ? cell(join(scenes, " → "))
                : (pn.length() > 0 && pn.indexOf("直连") < 0
                    ? cell(pn) + "（事件记录已覆盖，取最后生效场景）" : "(仅直连，未施加弱网)");
        o.append("| 本次测过的场景 | ").append(scenesCell).append(" |\n");
        String curName = s(asMap(status.get("profile")), "name", "-");
        o.append("| 导出时生效场景 | ").append(cell(curName));
        if (!scenes.isEmpty() && !b(status, "limit_on")) o.append("（已停止限速，参数见下）");
        o.append(" |\n");
        o.append("| 场景 key | `").append(cell(s(status, "preset_key", "-"))).append("` |\n");
        o.append("| 限速开关 | ").append(b(status, "limit_on") ? "开启" : "关闭（已停止）").append(" |\n");
        o.append("| 限速闸门 | ").append(b(status, "gate_open") ? "打开" : "关闭").append(" |\n");
        o.append("| 覆盖协议 | IPv4 TCP/UDP（TUN 用户态转发）；IPv6 不接管、不施加弱网 |\n");
        o.append("| VPN 接管 | ").append(b(status, "vpn_active") ? "已接管" : "未接管").append(" |\n");
        o.append("| 监听地址 | ").append(cell(s(listen, "host", "-"))).append(":")
         .append(cell(s(listen, "port", "-"))).append(" |\n");
        o.append("| 接管范围 | ").append(cell(s(scope, "mode", "-")));
        List<Object> sl = asList(scope == null ? null : scope.get("list"));
        if (sl != null && !sl.isEmpty()) {
            List<String> pkgs = new ArrayList<String>();
            for (Object x : sl) pkgs.add(String.valueOf(x));
            o.append("（").append(cell(join(pkgs, ", "))).append("）");
        }
        o.append(" |\n");
        o.append("| 运行时长 | ").append(humanDuration(l(stats, "uptime"))).append(" |\n");
        Map<String, Object> sess = sessionOf(status);
        if (sess != null) {
            o.append("| 测试会话 | #").append(l(sess, "seq")).append("，开始于 ")
             .append(cell(clock(sess, "started_at"))).append("，已运行 ")
             .append(humanDuration(l(sess, "elapsed"))).append(" |\n");
        } else {
            o.append("| 测试会话 | 老快照（无会话基线，以下统计为引擎累计口径） |\n");
        }
        o.append("\n");

        // 3. 流量统计
        o.append("## 流量统计\n\n");
        if (sess != null) {
            o.append("> 下表为**本次测试增量**（自会话 #").append(l(sess, "seq"))
             .append(" 开始）；引擎累计值另列，避免把开始测试前的历史流量算进本次样本。\n\n");
        }
        o.append("| 指标 | 数值 |\n|---|---:|\n");
        o.append("| 本次连接数 | ").append(l(stats, "total")).append(" |\n");
        o.append("| 当前活跃 | ").append(l(stats, "active")).append(" |\n");
        o.append("| 本次下行字节 | ").append(humanBytes(l(stats, "down")))
         .append("（").append(l(stats, "down")).append(" B） |\n");
        o.append("| 本次上行字节 | ").append(humanBytes(l(stats, "up")))
         .append("（").append(l(stats, "up")).append(" B） |\n");
        o.append("| 本次直通字节 | ").append(humanBytes(l(stats, "bypass")))
         .append("（").append(l(stats, "bypass")).append(" B） |\n");
        if (sess != null) {
            Map<String, Object> raw = asMap(status.get("stats"));
            o.append("| 引擎累计连接（参考） | ").append(l(raw, "total")).append(" |\n");
            o.append("| 引擎累计下行（参考） | ").append(humanBytes(l(raw, "down")))
             .append("（").append(l(raw, "down")).append(" B） |\n");
            o.append("| 引擎累计上行（参考） | ").append(humanBytes(l(raw, "up")))
             .append("（").append(l(raw, "up")).append(" B） |\n");
        }
        o.append("\n");

        // 4. 参数快照（非默认项），元数据直接取 SpecGen（与 spec.json 同源）
        o.append("## 参数快照（非默认项）\n\n");
        if (status.get("last_profile") instanceof Map)
            o.append("> 以下是**最后一次生效的弱网场景**参数；若本次测过多档，"
                     + "完整切换顺序见「测试概况 → 本次测过的场景」。\n\n");
        List<String> rows = new ArrayList<String>();
        for (String key : SpecGen.PARAM_KEYS) {
            Object dflt = Integer.valueOf(SpecGen.paramDefault(key));
            // 快照缺这个 key（构造的测试快照、或旧版本 profile）视为未设置=默认，
            // 不能列成「非默认项」——否则空值会占满整张表。
            if (!prof.containsKey(key)) continue;
            Object val = prof.get(key);
            if (sameValue(val, dflt)) continue;
            String note = Profile.noteOfParam(key);
            if (note.indexOf('\n') >= 0) note = note.substring(0, note.indexOf('\n'));
            rows.add("| " + cell(SpecGen.paramLabel(key)) + " | " + cell(val) + " " +
                     cell(SpecGen.paramUnit(key)) + " | " + cell(note) + " |");
        }
        if (rows.isEmpty()) {
            o.append("本次所有参数均为默认值（直连配置）。\n\n");
        } else {
            o.append("| 参数 | 设定值 | 说明 |\n|---|---:|---|\n");
            for (String r : rows) o.append(r).append("\n");
            o.append("\n");
        }

        // 5. 事件汇总 + 时间线
        o.append("## 事件汇总\n\n");
        if (counts.isEmpty()) {
            o.append("本次运行没有记录到事件。\n\n");
        } else {
            o.append("| 事件 | 次数 |\n|---|---:|\n");
            List<Map.Entry<String, Integer>> sorted =
                    new ArrayList<Map.Entry<String, Integer>>(counts.entrySet());
            Collections.sort(sorted, new Comparator<Map.Entry<String, Integer>>() {
                public int compare(Map.Entry<String, Integer> a, Map.Entry<String, Integer> b) {
                    int c = b.getValue().compareTo(a.getValue());
                    return c != 0 ? c : a.getKey().compareTo(b.getKey());
                }
            });
            for (Map.Entry<String, Integer> e : sorted)
                o.append("| ").append(cell(labelOf(e.getKey()))).append(" | ")
                 .append(e.getValue()).append(" |\n");
            o.append("\n");
        }
        List<Object> evs = asList(status.get("events"));
        if (evs != null && !evs.isEmpty()) {
            o.append("最近事件（最多 30 条，最新在上）：\n\n");
            int n = Math.min(30, evs.size());
            for (int i = 0; i < n; i++) {
                Map<String, Object> e = asMap(evs.get(i));
                if (e == null) { o.append("- ").append(cell(evs.get(i))).append("\n"); continue; }
                o.append("- `[").append(cell(s(e, "t", ""))).append("]` ")
                 .append(cell(s(e, "msg", ""))).append("\n");
            }
            o.append("\n");
        }

        // 6. 帧统计（仅当调用方传入 frames）
        Map<String, Object> frames = asMap(status.get("frames"));
        if (frames != null && !frames.isEmpty()) {
            o.append("## 界面刷新帧统计\n\n| 指标 | 值 |\n|---|---:|\n");
            String[][] fs = {{"累计帧数","frames",""}, {"窗口内帧数","windowed",""},
                {"间隔中位","gap_median_ms"," ms"}, {"间隔 p95","gap_p95_ms"," ms"},
                {"间隔最大","gap_max_ms"," ms"}, {"单帧耗时均值","cost_avg_ms"," ms"},
                {"单帧耗时最大","cost_max_ms"," ms"}, {"超 16ms 帧数","over_frame",""},
                {"超 100ms 帧数","over_stall",""}, {"约刷新频率","per_sec"," 次/秒"}};
            for (String[] row : fs)
                o.append("| ").append(row[0]).append(" | ")
                 .append(cell(frames.get(row[1]))).append(row[2]).append(" |\n");
            o.append("\n");
        }

        // 7. 口径说明
        o.append("## 口径说明\n\n");
        for (String c : CAVEATS) o.append("- ").append(c).append("\n");
        o.append("\n");

        // 8. 产物指纹
        o.append("## 产物指纹\n\n");
        o.append("- 版本：`v").append(cell(s(status, "version", "-"))).append("`，build `")
         .append(cell(s(status, "build", "-"))).append("`\n");
        o.append("- 规格哈希（spec.json sha256 前 8 位）：`")
         .append(cell(s(status, "spec_hash", "-"))).append("`\n");
        // 整份 Markdown 统一脱敏：事件日志里有对端 IP（「建连 10.0.0.1:443」），
        // 报告的使用场景就是转发给开发/贴 issue，完整地址等于把「被测 App 访问了
        // 哪些服务」的画像一起交出去。规则与桌面版 report_gen.redact_text 一致。
        return redactText(o.toString());
    }

    // ------------------------------------------------------------------ 脱敏
    //
    // 对齐桌面版 report_gen.py：
    //   IPv4 → 前两段 + .x.x（0.0.0.0 / 127.0.0.1 不含隐私，原样保留）；
    //   IPv6 → 保留首组 + :****；
    //   v / version / 版本 前缀的四段数字是版本号，先占位保护再还原；
    //   域名不动（mirrors.tuna.tsinghua.edu.cn 这类公开服务不构成隐私）；
    //   任一段 > 255 的「形似 IP」不动。
    // 纯 JDK 静态方法，_ReportGenTest 直接喂字符串。

    private static final Pattern IPV4_RE = Pattern.compile(
            "(?<!\\d)(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})(?!\\d)");
    private static final Pattern IPV6_RE = Pattern.compile(
            "(?<![0-9A-Za-z:])([0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{0,4}){2,7})(?![0-9A-Za-z:])");
    private static final Pattern VERSION_RE = Pattern.compile(
            "(?<![0-9A-Za-z_.])(?:v|version|版本)[\\s:：]*(\\d{1,3}(?:\\.\\d{1,3}){2,3})(?!\\d)",
            Pattern.CASE_INSENSITIVE);

    /** 把文本里的 IP 末段掩码；null / 空串原样返回。 */
    public static String redactText(String text) {
        if (text == null || text.length() == 0) return text;
        // 全角数字/句点先归一：从聊天工具复制来的「10．0．0．1」否则会绕过匹配
        String s = normalizeFullwidth(text);
        // 哨兵前缀带随机 nonce：原文自带的同名串不会在还原时被一起替换
        final String nonce = "\u0000WN" + UUID.randomUUID().toString().replace("-", "") + "_";
        final List<String> saved = new ArrayList<String>();

        Matcher vm = VERSION_RE.matcher(s);
        StringBuffer vb = new StringBuffer();
        while (vm.find()) {
            if (looksLikeVersion(vm.group(1))) {
                saved.add(vm.group(0));
                vm.appendReplacement(vb,
                        Matcher.quoteReplacement(nonce + (saved.size() - 1) + "\u0000"));
            } else {
                // v10.0.0.1 是真实 IP：不占位，交给下面的 IPv4 规则脱敏
                vm.appendReplacement(vb, Matcher.quoteReplacement(vm.group(0)));
            }
        }
        vm.appendTail(vb);
        s = vb.toString();

        Matcher m4 = IPV4_RE.matcher(s);
        StringBuffer b4 = new StringBuffer();
        while (m4.find()) {
            m4.appendReplacement(b4, Matcher.quoteReplacement(maskV4(m4)));
        }
        m4.appendTail(b4);

        Matcher m6 = IPV6_RE.matcher(b4.toString());
        StringBuffer b6 = new StringBuffer();
        while (m6.find()) {
            m6.appendReplacement(b6, Matcher.quoteReplacement(maskV6(m6.group(1))));
        }
        m6.appendTail(b6);
        s = b6.toString();

        for (int i = 0; i < saved.size(); i++) {
            s = s.replace(nonce + i + "\u0000", saved.get(i));
        }
        return s;
    }

    /** 四段、每段 0~255、首段 1~9 → 版本号（首段 0/10/127/172/192 等更像网段） */
    private static boolean looksLikeVersion(String digits) {
        String[] p = digits.split("\\.");
        if (p.length < 3 || p.length > 4) return false;
        for (String x : p) {
            if (x.length() == 0 || Integer.parseInt(x) > 255) return false;
        }
        int head = Integer.parseInt(p[0]);
        return head >= 1 && head <= 9;
    }

    private static String maskV4(Matcher m) {
        String a = m.group(1), b = m.group(2), c = m.group(3), d = m.group(4);
        int ia = Integer.parseInt(a), ib = Integer.parseInt(b);
        int ic = Integer.parseInt(c), id = Integer.parseInt(d);
        if (ia > 255 || ib > 255 || ic > 255 || id > 255) return m.group(0);   // 形似而非 IP
        if ((ia == 0 && ib == 0 && ic == 0 && id == 0)
                || (ia == 127 && ib == 0 && ic == 0 && id == 1)) {
            return m.group(0);            // 监听地址 / 本机回环不含隐私
        }
        return a + "." + b + ".x.x";
    }

    private static String maskV6(String s) {
        // 含 :: 或含 a-f 字母的一定是 IPv6；纯数字串才可能是编号/时间
        if (s.indexOf("::") < 0 && !s.matches(".*[a-fA-F].*")) {
            String[] all = s.split(":");
            int n = 0;
            boolean shortDigits = true;
            for (String p : all) {
                if (p.length() == 0) continue;
                n++;
                if (p.length() > 3 || !p.matches("\\d+")) shortDigits = false;
            }
            if (n < 4) return s;                              // 14:44:18 这类时间
            if (n < 8 && shortDigits) return s;               // 不足 8 组的短编号串
        }
        int cut = s.indexOf(':');
        return cut < 0 ? s : s.substring(0, cut) + ":****";
    }

    private static String normalizeFullwidth(String s) {
        StringBuilder b = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c >= '\uFF10' && c <= '\uFF19') b.append((char) (c - '\uFF10' + '0'));
            else if (c == '\uFF0E') b.append('.');
            else b.append(c);
        }
        return b.toString();
    }

    static boolean sameValue(Object a, Object b) {
        if (a == null && b == null) return true;
        if (a == null || b == null) return false;
        if (a instanceof Number && b instanceof Number)
            return ((Number) a).doubleValue() == ((Number) b).doubleValue();
        return String.valueOf(a).equals(String.valueOf(b));
    }

    /** 默认文件名，带时间戳避免覆盖上一次 */
    public static String defaultFilename(String ymdHms) {
        return "WeakNet测试报告_" + (ymdHms == null ? "" : ymdHms) + ".md";
    }

    // ------------------------------------------------------------------ 快照组装
    // 从全局状态收集 ReportGen.build 需要的 Map（与 /api/status 同构）。

    /** Profile -> 以 spec key 为键的 Map（报告/快照用；字段名是驼峰，需显式映射）。 */
    static Map<String, Object> profileMap(Profile p) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        if (p == null) return m;
        m.put("name", p.name);
        m.put("latency_down_ms", p.latencyDownMs);
        m.put("latency_up_ms", p.latencyUpMs);
        m.put("jitter_ms", p.jitterMs);
        m.put("down_kbps", p.downKbps);
        m.put("up_kbps", p.upKbps);
        m.put("loss_pct", p.lossPct);
        m.put("stall_ms", p.stallMs);
        m.put("disconnect_pct", p.disconnectPct);
        m.put("timeout_pct", p.timeoutPct);
        m.put("dns_fail_pct", p.dnsFailPct);
        m.put("reject_pct", p.rejectPct);
        m.put("flap_sec", p.flapSec);
        m.put("flap_blackout_sec", p.flapBlackoutSec);
        return m;
    }

    /** 组装报告快照。version/build 由 UI 层从 PackageManager 取得后传入，
     *  让本类只依赖纯 JDK（android.jar 的 org.json 是 Stub，纯 JDK 跑不了）。
     *  frameSnapshot 可为 null。 */
    public static Map<String, Object> collect(String version, int build,
                                              Map<String, Object> frames) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("version", version == null ? "?" : version);
        m.put("build", build);
        m.put("spec_hash", SpecGen.SPEC_HASH);
        m.put("profile", profileMap(WeakNet.I.profile));
        m.put("last_profile", WeakNet.I.lastProfile == null ? null
                : profileMap(WeakNet.I.lastProfile));
        m.put("preset_key", WeakNet.I.presetKey);
        m.put("limit_on", WeakNet.I.limitOn);
        m.put("gate_open", WeakNet.I.gateOpen);
        m.put("vpn_active", VpnNetService.active);
        Map<String, Object> listen = new LinkedHashMap<String, Object>();
        listen.put("host", "127.0.0.1");
        listen.put("port", NetService.PROXY_PORT);
        m.put("listen", listen);
        Map<String, Object> scope = new LinkedHashMap<String, Object>();
        scope.put("mode", WeakNet.I.scopeMode);
        scope.put("list", new ArrayList<Object>(WeakNet.I.scopePkgs));
        scope.put("effective_note", VpnNetService.scopeNote());
        m.put("scope", scope);
        Map<String, Object> stats = new LinkedHashMap<String, Object>();
        stats.put("total", WeakNet.I.connTotal.get());
        stats.put("active", WeakNet.I.connActive.get());
        stats.put("up", WeakNet.I.bytesUp.get());
        stats.put("down", WeakNet.I.bytesDown.get());
        stats.put("uptime", (System.currentTimeMillis() - WeakNet.I.startedAt) / 1000);
        m.put("stats", stats);
        // 本次测试增量：报告优先按它判定有效性。没有会话（还没开始过测试）时
        // 写 null，老逻辑按 stats 累计口径兜底。
        m.put("session", WeakNet.I.sessionMap());
        // 与 /api/status.fault_counts_full 同语义：全量事件按 type 聚合，
        // 报告不再因为界面只留最近 60 条而漏算故障次数。
        Map<String, Object> full = new LinkedHashMap<String, Object>();
        synchronized (WeakNet.I.events) {
            for (String[] e : WeakNet.I.events) {
                String type = e[1];
                Object old = full.get(type);
                full.put(type, old == null ? Integer.valueOf(1)
                        : Integer.valueOf(((Integer) old).intValue() + 1));
            }
        }
        m.put("fault_counts_full", full);
        List<Object> evs = new ArrayList<Object>();
        synchronized (WeakNet.I.events) {
            List<String[]> src = WeakNet.I.events;
            int from = Math.max(0, src.size() - 60);
            for (int i = src.size() - 1; i >= from; i--) {
                String[] e = src.get(i);
                Map<String, Object> j = new LinkedHashMap<String, Object>();
                j.put("t", e[0]); j.put("type", e[1]); j.put("msg", e[2]);
                evs.add(j);
            }
        }
        m.put("events", evs);
        if (frames != null) m.put("frames", frames);
        m.put("generated_at", java.text.DateFormat.getDateTimeInstance().format(new java.util.Date()));
        return m;
    }
}
