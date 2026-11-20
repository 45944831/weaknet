package com.weaknet.app;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

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
        "上下行字节是引擎对**已接管连接**的计数，不含直通（未接管）流量。",
        "事件日志是内存环形缓冲（上限 300 条），界面只展示最近 60 条；本报告导出的是点击那一刻的快照。",
        "运行时长是**引擎本次启动**至今的时间，不是单个场景的持续时间。"
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
    static final String[] COMBO_PREFIXES = {"开始播放组合：", "继续播放组合：", "播放组合：", "飘窗播放组合："};

    /** 从事件文案取落地场景名；组合名若在「」内只取括号内部分。 */
    static String sceneName(String msg) {
        if (msg == null || msg.length() == 0) return "";
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

    /** 事件按类型聚合计数 */
    static Map<String, Integer> summarizeEvents(Map<String, Object> status) {
        Map<String, Integer> counts = new LinkedHashMap<String, Integer>();
        List<Object> evs = asList(status == null ? null : status.get("events"));
        if (evs == null) return counts;
        for (Object item : evs) {
            Map<String, Object> e = asMap(item);
            String t = e == null ? "?" : s(e, "type", "?");
            Integer c = counts.get(t);
            counts.put(t, c == null ? 1 : c + 1);
        }
        return counts;
    }

    static String labelOf(String type) {
        String v = EVENT_LABELS.get(type);
        return v == null ? type : v;
    }

    static String conclusion(Map<String, Object> status, Map<String, Integer> counts,
                             List<String> scenes, Map<String, Object> prof) {
        Map<String, Object> stats = asMap(status == null ? null : status.get("stats"));
        long total = l(stats, "total"), active = l(stats, "active");
        String name = s(prof, "name", "未命名场景");

        List<String> injected = new ArrayList<String>();
        for (String t : FAULT_TYPES) if (counts.containsKey(t)) injected.add(labelOf(t));
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
        Map<String, Object> stats = asMap(status == null ? null : status.get("stats"));
        long total = l(stats, "total"), active = l(stats, "active"), bypass = l(stats, "bypass");
        boolean limitOn = b(status, "limit_on");

        List<String> injected = new ArrayList<String>();   // 「故障名×次数」
        for (String t : FAULT_TYPES)
            if (counts.containsKey(t)) injected.add(labelOf(t) + "×" + counts.get(t));
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
        o.append("| 运行时长 | ").append(humanDuration(l(stats, "uptime"))).append(" |\n\n");

        // 3. 流量统计
        o.append("## 流量统计\n\n| 指标 | 数值 |\n|---|---:|\n");
        o.append("| 连接总数 | ").append(l(stats, "total")).append(" |\n");
        o.append("| 当前活跃 | ").append(l(stats, "active")).append(" |\n");
        o.append("| 下行字节 | ").append(humanBytes(l(stats, "down")))
         .append("（").append(l(stats, "down")).append(" B） |\n");
        o.append("| 上行字节 | ").append(humanBytes(l(stats, "up")))
         .append("（").append(l(stats, "up")).append(" B） |\n");
        o.append("| 直通字节 | ").append(humanBytes(l(stats, "bypass")))
         .append("（").append(l(stats, "bypass")).append(" B） |\n\n");

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
        return o.toString();
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
        m.put("scope", scope);
        Map<String, Object> stats = new LinkedHashMap<String, Object>();
        stats.put("total", WeakNet.I.connTotal.get());
        stats.put("active", WeakNet.I.connActive.get());
        stats.put("up", WeakNet.I.bytesUp.get());
        stats.put("down", WeakNet.I.bytesDown.get());
        stats.put("uptime", (System.currentTimeMillis() - WeakNet.I.startedAt) / 1000);
        m.put("stats", stats);
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
