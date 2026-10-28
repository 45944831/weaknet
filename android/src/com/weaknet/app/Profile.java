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

    /** key 是不是本机认识的某一档内置预设（别人推来的组合里可能有我们没有的档位） */
    public static boolean known(String key) {
        if (key == null) return false;
        for (String k : KEYS) {
            if (k.equals(key)) return true;
        }
        return false;
    }

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
                "单程 700ms（一次往返约 1.4 秒）、带宽 2Mbps。典型表现是「每个请求都要等好几秒才回来」，"
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

    /**
     * 每个参数的注释：参数编辑界面上每行末尾的「！」把它们显示出来。
     *
     * 为什么需要：13 个参数里有一半光看名字猜不出行为 —— 「丢包率」填 30 到底会让
     * 请求报错还是变慢？「黑洞率」和「拒连率」都是连不上，差别在哪？「卡顿时长」
     * 单独填为什么没效果？这些都得翻引擎源码才能确定，写在这里让用户点一下就看到。
     * 文案与桌面版 weaknet.py 的 PARAM_NOTES 保持一致（由 _t_param_notes.py 逐条比对）。
     */
    private static final Map<String, String> PARAM_NOTES = new HashMap<String, String>();

    static {
        PARAM_NOTES.put("latency_down_ms",
                "给「服务器 → 本机」方向每块数据加的固定延迟，用来拉长响应回来的时间。\n"
                        + "每块数据都会加，不是只在建连时加一次 —— 否则 keep-alive / WebSocket 这类长连接里"
                        + "只有第一个请求慢，后面的全是零延迟。一次往返 ≈ 上行延迟 + 下行延迟。");
        PARAM_NOTES.put("latency_up_ms",
                "给「本机 → 服务器」方向加的固定延迟，影响请求发出去要多久才到服务器。\n"
                        + "「点了按钮迟迟不发请求」看这项，「请求回来了但界面不刷新」看下行延迟。"
                        + "一次往返 ≈ 上行 + 下行，所以两项都填 400 时 RTT 是 800ms。");
        PARAM_NOTES.put("jitter_ms",
                "每个数据块的延迟在基础值上随机浮动 ±N 毫秒，让延迟忽快忽慢。\n"
                        + "实际延迟 = 基础延迟 + (-N ~ +N) 的随机值；抖动幅度不会超过基础延迟"
                        + "（否则会算出负延迟，变成比直连还快的「零延迟包」），超出时按基础延迟封顶。"
                        + "抖动主要影响实时音视频、游戏、VoIP；纯下载几乎感觉不到。");
        PARAM_NOTES.put("down_kbps",
                "下行总带宽上限，单位 kbps（1 Mbps = 1000 kbps，1000 kbps ≈ 125 KB/s）。0 = 不限。\n"
                        + "注意是全局共享的一份带宽：同时开 5 条连接是 5 条一起分摊，不是每条各拿一份。");
        PARAM_NOTES.put("up_kbps",
                "上行总带宽上限（本机 → 服务器），单位 kbps。0 = 不限。\n"
                        + "上传大文件、发图片、提交大请求体时先卡在这里；只测「页面加载慢」时通常不用改它。");
        PARAM_NOTES.put("loss_pct",
                "每块数据按这个概率「丢掉」，表现不是报错，而是一段停顿（模拟 TCP 等重传）。\n"
                        + "停多久由下面的「卡顿时长」决定，丢包率本身不决定停多久。填 100 会表现为完全传不动。");
        PARAM_NOTES.put("stall_ms",
                "每次「丢包」命中后停顿多久，实际会在 0.5~1.5 倍之间随机。\n"
                        + "只在丢包率 > 0 时生效 —— 丢包率为 0 时这项填什么都不影响结果。默认 600。");
        PARAM_NOTES.put("disconnect_pct",
                "每块数据按概率直接掐断整条连接（两侧 RST/FIN），App 看到的是「连接被重置」。\n"
                        + "和丢包的区别：丢包是慢，断连是断，必须重连才能继续。只影响已建立的连接；"
                        + "能不能建连由「拒连率」管。");
        PARAM_NOTES.put("timeout_pct",
                "新连接按概率「发出去没有回应」，先挂住不返回，过一会儿才继续处理。\n"
                        + "最容易触发「请求卡死、转圈不停」。和「拒连率」的区别：这个是挂住等，那个是立刻失败。");
        PARAM_NOTES.put("dns_fail_pct",
                "按概率让域名解析失败（返回 502 DNS resolution failed）。\n"
                        + "只拦「需要解析域名」的连接：用 IP 直连的（HTTPDNS、写死 IP 的 App）不受影响 —— "
                        + "所以填 100% 也可能看到部分请求正常。");
        PARAM_NOTES.put("reject_pct",
                "新连接按概率立刻被拒绝（直接返回 502），是「马上失败」而不是挂住。\n"
                        + "模拟飞行模式 / 完全没网，用来验证 App 的快速失败与降级路径。");
        PARAM_NOTES.put("flap_sec",
                "每隔 N 秒把当前所有连接全部掐断，持续「断网时长」后恢复。0 = 关闭。\n"
                        + "这是周期性的整体断网，跟「断连率」随机掐单条不是一回事；两项可以叠加。");
        PARAM_NOTES.put("flap_blackout_sec",
                "每次周期断网持续多久，到点自动恢复。\n"
                        + "只在「断网间隔」> 0 时生效，单独填没有作用。");
    }

    /** 单个参数的说明；未知 key 返回空串（界面据此不显示「！」） */
    public static String noteOfParam(String key) {
        String s = key == null ? null : PARAM_NOTES.get(key);
        return s == null ? "" : s;
    }

    // ---------------------------------------------------------------- 弱网组合说明
    //
    // 与桌面版 weaknet.py 的 COMBO_DOC 逐字一致（由 _t_combo_notes.py 两端比对）。
    // 文案放引擎里而不是各写一份：两端各写必然漂移 ——
    // 改了一端忘了另一端，同一个功能说出来的话就不一样了。
    //
    // 三块内容（机制 / 例子 / 提醒）拼成一份文档串，用 \u001F 分隔：
    // 拆成 String[][] 的话，测试要解析 Java 源码来比对，
    // 在「多行拼接的字符串」上极易出错 —— 表现是两边明明一样、测试却说不一样。

    /** 块与块之间的分隔符（U+001F 单元分隔符，正常文本里不会出现） */
    public static final String COMBO_DOC_SEP = "\u001F";

    /** 例子条数（分界用：前 N 块是例子，之后是提醒） */
    public static final int COMBO_EXAMPLE_COUNT = 5;

    /** 完整文档：第 1 块是机制，之后每 4 行一个例子，剩下的是提醒 */
    public static final String COMBO_DOC =
                        "弱网组合 = 把几支场景按时长排成一条链，选中后自动一段段播过去。\n"
            +             "· 顺序播放：一段跑完自动切下一段，中间不用你操作。\n"
            +             "· 可循环：末尾再回到第一段，一直循环下去（不勾就播完一遍停住）。\n"
            +             "· 最多 5 段：再多就不好在界面上按顺序核对了。\n"
            +             "· 组合里不能再套组合：段只能选预设或自定义场景。\n"
            +             "· 弱网没开着时点组合只「选中」，不会替你偷偷开始限速；开着才会立刻播。\u001F通勤路上\n"
            +             "从有信号走进信号差的地方，再彻底没信号，最后恢复。\n"
            +             "复现「地铁进隧道」这类真实链路：先正常加载，走到弱覆盖区开始卡，进隧道后请求挂住，出站后恢复。适合测弱网下的加载中状态与恢复逻辑。\n"
            +             "5G 良好 30 秒 → 4G 弱网 1 分 → 完全断网 30 秒 → 5G 良好 30 秒\u001F抢购秒杀\n"
            +             "平时流畅，一到高峰就抢不到，失败后快速重试。\n"
            +             "高并发下服务端限流或网络拥塞的典型表现：请求变慢、部分直接失败。适合验证 App 的失败提示、重试与降级路径。\n"
            +             "5G 良好 20 秒 → 弱 Wi-Fi 1 分 → 黑洞（无响应）15 秒 → 弱 Wi-Fi 1 分\u001F网络抖动\n"
            +             "信号时好时坏，延迟忽大忽小。\n"
            +             "模拟电梯、隧道口、拥挤场所的信号抖动。适合测实时音视频、语音通话、在线协作这类对延迟敏感的链路。\n"
            +             "5G 良好 20 秒 → 弱 5G 20 秒 → 5G 良好 20 秒 → 弱 5G 20 秒（循环）\u001F定时断网\n"
            +             "每隔一会儿断一次，过一会儿自己恢复。\n"
            +             "模拟周期性网络中断（如基站切换、家庭路由重启）。适合验证长连接断了能不能自己重连、心跳与超时逻辑。\n"
            +             "4G 良好 1 分 → 黑洞（无响应）20 秒（循环）\u001F加载渐变\n"
            +             "从慢到快再到慢，模拟带宽被逐步放开。\n"
            +             "测大文件 / 长列表的渐进加载表现：首屏慢、之后提速、快结束时又慢下来。适合验证进度条与分片下载逻辑。\n"
            +             "极慢（100kbps）20 秒 → 4G 弱网 20 秒 → 5G 良好 20 秒 → 极慢（100kbps）20 秒\u001F组合播到一半可以随时切走或暂停：切换后原组合停在原地，不会偷偷继续改参数。\u001F每一段都是完整的一支场景，段与段之间参数是整套替换的 —— 不会把上一段的丢包率带进下一段。\u001F循环组合会一直播下去，测完记得切走或暂停，别忘了它在后台改着参数。";

    /**
     * 把文档串拆成三块：说明、例子列表、提醒列表。
     *
     * 例子每条 4 行：名字 / 一句话说明 / 效果 / 怎么排。
     * 规则与桌面版 combo_doc_parts 一致 —— 两端拆法必须一样，
     * 不然同一个组合在电脑上和在手机上看到的排版会不同。
     */
    public static Object[] comboDocParts() {
        String[] blocks = COMBO_DOC.split(COMBO_DOC_SEP);
        java.util.List<String> examples = new java.util.ArrayList<String>();
        java.util.List<String> tips = new java.util.ArrayList<String>();
        String intro = blocks.length > 0 ? blocks[0] : "";
        for (int i = 1; i < blocks.length; i++) {
            if (i <= COMBO_EXAMPLE_COUNT) {
                String[] lines = blocks[i].split("\n", -1);
                if (lines.length >= 4) {
                    examples.add(lines[0] + "\n" + lines[1] + "\n"
                            + lines[2] + "\n" + lines[3]);
                }
            } else {
                tips.add(blocks[i]);
            }
        }
        return new Object[]{intro, examples, tips};
    }


    /**
     * 把说明拆成「作用」与若干条「注意」。
     *
     * 规则与桌面版 weaknet.py 的 split_param_note 完全一致（两端文案逐字相同，
     * 切分规则也必须一致，不然同一个参数在两端会长得不一样）：
     * 第一段讲这项干什么，第二段按句号切成一条条提醒。
     *
     * @return 第一个元素是作用段，第二个是提醒条目（没有提醒时为空数组）
     */
    public static Object[] splitNote(String key) {
        String note = noteOfParam(key);
        String[] out = new String[1];
        java.util.List<String> tips = new java.util.ArrayList<String>();
        if (note == null || note.length() == 0) {
            out[0] = "";
            return new Object[]{out[0], tips};
        }
        String[] parts = note.split("\n");
        java.util.List<String> segs = new java.util.ArrayList<String>();
        for (int i = 0; i < parts.length; i++) {
            String p = parts[i].trim();
            if (p.length() > 0) segs.add(p);
        }
        if (segs.isEmpty()) {
            out[0] = "";
            return new Object[]{out[0], tips};
        }
        if (segs.size() == 1) {
            java.util.List<String> sents = splitSentences(segs.get(0));
            out[0] = sents.isEmpty() ? segs.get(0) : sents.get(0);
            for (int i = 1; i < sents.size(); i++) tips.add(sents.get(i));
            return new Object[]{out[0], tips};
        }
        out[0] = segs.get(0);
        StringBuilder rest = new StringBuilder();
        for (int i = 1; i < segs.size(); i++) {
            if (rest.length() > 0) rest.append(" ");
            rest.append(segs.get(i));
        }
        tips.addAll(splitSentences(rest.toString()));
        return new Object[]{out[0], tips};
    }

    /** 按中文句号 / 问号 / 感叹号切句，保留标点 */
    private static java.util.List<String> splitSentences(String text) {
        java.util.List<String> out = new java.util.ArrayList<String>();
        if (text == null) return out;
        StringBuilder buf = new StringBuilder();
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            buf.append(ch);
            if (ch == '。' || ch == '！' || ch == '？') {
                String s = buf.toString().trim();
                if (s.length() > 0) out.add(s);
                buf.setLength(0);
            }
        }
        String tail = buf.toString().trim();
        if (tail.length() > 0) out.add(tail);
        return out;
    }

    /** 参数取值上限：与桌面版 weaknet.py 的 PROF_LIMITS 保持一致 */
    public static int limitOfParam(String key) {
        if (key == null) return 0;
        if ("latency_up_ms".equals(key) || "latency_down_ms".equals(key)) return 10000;
        if ("jitter_ms".equals(key)) return 5000;
        if ("down_kbps".equals(key) || "up_kbps".equals(key)) return 1000000;
        if ("stall_ms".equals(key)) return 60000;
        if ("flap_sec".equals(key)) return 3600;
        if ("flap_blackout_sec".equals(key)) return 600;
        return 100;                       // 各项百分比
    }

    /** 参数单位：从显示名里括起来的那段取（如「下行带宽 (kbps)」-> kbps） */
    public static String unitOfParam(String key) {
        if (key == null) return "";
        for (String[] f : MainPanels.FIELDS) {
            if (!f[0].equals(key)) continue;
            String name = f[1] == null ? "" : f[1];
            int a = name.indexOf('(');
            int b = name.lastIndexOf(')');
            if (a >= 0 && b > a) return name.substring(a + 1, b).trim();
            return "";
        }
        return "";
    }

    // ---------------------------------------------------------------- 说明文案的排版
    //
    // 与桌面版 weaknet.py 的同名逻辑逐条对应（split_note_atoms /
    // _note_group_units / wrap_note_lines）。两端必须一模一样：
    // 同一个参数在电脑上和手机上会长得不一样，是最难解释的一类 bug。
    //
    // 为什么不能直接用 TextView 的自动换行：安卓的断行规则跟桌面端不同，
    // 同一个文案在两端会被断在不同的位置；而且「·」这种条目符会被甩到单独一行，
    // 「「本机 → 服务器」」这种引号短语会被从中间劈开。
    // 所以两端都把文字先切成「不可再分的原子」，再按像素宽贪心拼行。
    //
    // 原子的四种：
    //   c  单个字符（汉字 / 中文标点 / 独立符号），可以在它前后断行
    //   q  引号短语（「」「『』“”《》整段不可断）
    //   w  英文单词 / 数字带单位（800ms、0.5~1.5、RTT 整段不可断）
    //   s  空白（只用来标记「这里原本有空格」，断开时丢掉）
    //
    // 断行时还做两件中文排版的事：
    //   避头：收尾标点（，。、）」等）不另起一行 —— 把它留在上一行
    //   避尾：起头标点（（《等）不留在行末 —— 把它带到下一行

    /** 引号配对（开 -> 闭） */
    private static final String NOTE_QUOTE_OPEN = "「『“《";
    private static final String NOTE_QUOTE_CLOSE = "」』”》";

    /** 行首禁则：这些标点不能出现在一行的开头 */
    public static final String NOTE_NO_START = "，。、；：？！）」』】”’%‰°′″℃…";

    /** 行尾禁则：这些标点不能出现在一行的末尾 */
    public static final String NOTE_NO_END = "（「『【《“";

    /** 不能孤零零出现在行首的符号：数学 / 箭头符号 */
    public static final String NOTE_NO_START_GLUE = "≈→←±×÷·〜～";

    /** 数字 / 单词内部的连接符（0.5~1.5 里的 . ~ ，800ms% 里的 %） */
    private static final String NOTE_WORD_CONNECT = ".-~_%+/";

    /** 视为「空白」的字符（断行时空格丢弃，行内保留一个） */
    private static final String NOTE_SPACE_CHARS = " \t　";

    /** 一个排版原子 */
    static final class NoteAtom {
        final String kind;   // c / q / w / s
        final String text;
        NoteAtom(String kind, String text) {
            this.kind = kind;
            this.text = text;
        }
    }

    /** 一个排版单元：行内不可再分的一段文字 */
    static final class NoteUnit {
        final String text;
        final boolean sep;   // 前面有没有空格
        NoteUnit(String text, boolean sep) {
            this.text = text;
            this.sep = sep;
        }
    }

    /** 是不是中日韩字符 / 中文标点（含全角形式） */
    private static boolean isNoteCjk(char ch) {
        int code = ch;
        return (code >= 0x2E80 && code <= 0x9FFF)
                || (code >= 0xF900 && code <= 0xFAFF)
                || (code >= 0xFF00 && code <= 0xFFEF);
    }

    /** 是不是 ASCII 的字母 / 数字 */
    private static boolean isAsciiAlnum(char ch) {
        return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z')
                || (ch >= '0' && ch <= '9');
    }

    /**
     * s.charAt(i) 能不能算「英文单词 / 数字」的一部分。
     * 字母数字当然算；连接符只在两边都是字母数字时才算，
     * 这样 0.5~1.5 是一个整体，而「上行 + 下行」里的 + 是独立符号。
     */
    private static boolean isNoteWordChar(String s, int i) {
        char ch = s.charAt(i);
        if (isAsciiAlnum(ch)) return true;
        if (NOTE_WORD_CONNECT.indexOf(ch) >= 0) {
            boolean prevAlnum = i > 0 && isAsciiAlnum(s.charAt(i - 1));
            boolean nextAlnum = i + 1 < s.length() && isAsciiAlnum(s.charAt(i + 1));
            return prevAlnum && nextAlnum;
        }
        return false;
    }

    /**
     * 把一段说明文字切成不可再分的排版原子。
     * 返回 [kind, text] 的列表，kind 取值 c / q / w / s。
     * 折行只能发生在原子与原子之间，绝不会把一个原子劈成两半 ——
     * 「「本机 → 服务器」」里的箭头和空格因此永远跟参数名待在一起。
     */
    static java.util.List<NoteAtom> splitNoteAtoms(String text) {
        java.util.List<NoteAtom> out = new java.util.ArrayList<NoteAtom>();
        if (text == null) return out;
        int i = 0;
        int n = text.length();
        while (i < n) {
            char ch = text.charAt(i);
            if (NOTE_SPACE_CHARS.indexOf(ch) >= 0) {
                int j = i;
                while (j < n && NOTE_SPACE_CHARS.indexOf(text.charAt(j)) >= 0) j++;
                out.add(new NoteAtom("s", text.substring(i, j)));
                i = j;
                continue;
            }
            int oi = NOTE_QUOTE_OPEN.indexOf(ch);
            if (oi >= 0) {
                char close = NOTE_QUOTE_CLOSE.charAt(oi);
                int j = text.indexOf(close, i + 1);
                if (j != -1) {
                    out.add(new NoteAtom("q", text.substring(i, j + 1)));
                    i = j + 1;
                    continue;
                }
            }
            if (isNoteCjk(ch)) {
                out.add(new NoteAtom("c", String.valueOf(ch)));
                i++;
                continue;
            }
            if (isAsciiAlnum(ch)) {
                int j = i;
                while (j < n && isNoteWordChar(text, j)) j++;
                // 百分号跟在词尾（100%）也算这个词的一部分
                if (j < n && text.charAt(j) == '%') j++;
                out.add(new NoteAtom("w", text.substring(i, j)));
                i = j;
                continue;
            }
            // 其它 ASCII 符号（= + % 等）单独成原子
            out.add(new NoteAtom("c", String.valueOf(ch)));
            i++;
        }
        return out;
    }

    /**
     * 原子表 -> 排版单元表。
     * 这里顺手做了中文排版的两条「避头尾」规则 —— 不做的话，断行处会出现
     * 孤零零的标点（比如一行的开头是「，」）：
     *   行首禁则（，。、；：？！）等）不能起行 -> 粘到前一个单元末尾
     *   行尾禁则（（《 等）不能收行 -> 粘到后一个单元开头
     * 合成之后，断行只发生在单元之间，标点自然跟着前面的字走。
     */
    static java.util.List<NoteUnit> noteGroupUnits(java.util.List<NoteAtom> atoms) {
        java.util.List<NoteUnit> units = new java.util.ArrayList<NoteUnit>();
        boolean sep = false;              // 上一段和这一段之间有没有空格
        StringBuilder hold = new StringBuilder();   // 待与下一个单元合并的起头标点
        boolean holdSep = false;
        for (NoteAtom a : atoms) {
            String kind = a.kind;
            String t = a.text;
            if ("s".equals(kind)) {
                sep = true;
                continue;
            }
            if ("c".equals(kind) && t.length() == 1) {
                char ch = t.charAt(0);
                if (NOTE_NO_END.indexOf(ch) >= 0) {
                    // 起头标点不能收在行末 —— 先攒着，跟下一个单元走
                    hold.append(ch);
                    holdSep = sep;
                    sep = false;
                    continue;
                }
                if (hold.length() > 0) {
                    t = hold.toString() + ch;
                    sep = holdSep;
                    hold.setLength(0);
                    holdSep = false;
                }
                if (NOTE_NO_START.indexOf(ch) >= 0 || NOTE_NO_START_GLUE.indexOf(ch) >= 0) {
                    if (!units.isEmpty()) {
                        // 收尾标点不能起行 —— 粘到上一个单元末尾
                        NoteUnit last = units.get(units.size() - 1);
                        units.set(units.size() - 1,
                                new NoteUnit(last.text + (sep ? " " : "") + ch, last.sep));
                        sep = false;
                        continue;
                    }
                }
                units.add(new NoteUnit(t, sep));
                sep = false;
                continue;
            }
            // 引号短语 / 英文单词：整体不可断
            if (hold.length() > 0) {
                t = hold.toString() + t;
                sep = holdSep;
                hold.setLength(0);
                holdSep = false;
            }
            units.add(new NoteUnit(t, sep));
            sep = false;
        }
        if (hold.length() > 0) {
            units.add(new NoteUnit(hold.toString(), holdSep));
        }
        return units;
    }

    /** 一行的宽度：行首单元不算它前面的空格 */
    private static int noteUnitsWidth(java.util.List<NoteUnit> units, NoteMeasurer measurer) {
        int total = 0;
        for (int i = 0; i < units.size(); i++) {
            NoteUnit u = units.get(i);
            if (i > 0 && u.sep) total += measurer.measure(" ");
            total += measurer.measure(u.text);
        }
        return total;
    }

    /** 单元表 -> 一行文字（行首不留空格，行内空格保留） */
    private static String noteUnitsText(java.util.List<NoteUnit> units) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < units.size(); i++) {
            NoteUnit u = units.get(i);
            if (i > 0 && u.sep) sb.append(' ');
            sb.append(u.text);
        }
        return sb.toString();
    }

    /**
     * 把一个单元切成「行内安全的最小块」。
     * 单元宽过整行时只能硬切，但切之前先按避头尾规则重新分块：
     *   · 收尾标点（，。、；：？！）等）粘到前一块末尾 —— 它不能起行
     *   · 起头标点（（《 等）粘到后一块开头 —— 它不能收行
     * 切完之后，断行只发生在块与块之间，标点自然跟着前面的字走。
     * 少了这一步，窄屏上就会出现「」。」这种一行开头的怪事
     * （实测：引号短语被硬切后，收尾引号正好落在下一行开头）。
     */
    private static java.util.List<String> noteSafeChunks(String text) {
        java.util.List<String> chunks = new java.util.ArrayList<String>();
        StringBuilder hold = new StringBuilder();   // 待与下一块合并的起头标点
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (NOTE_NO_END.indexOf(ch) >= 0) {
                hold.append(ch);
                continue;
            }
            String piece;
            if (hold.length() > 0) {
                piece = hold.toString() + ch;
                hold.setLength(0);
            } else {
                piece = String.valueOf(ch);
            }
            char last = piece.charAt(piece.length() - 1);
            if (NOTE_NO_START.indexOf(last) >= 0 && !chunks.isEmpty()) {
                chunks.set(chunks.size() - 1, chunks.get(chunks.size() - 1) + piece);
                continue;
            }
            chunks.add(piece);
        }
        if (hold.length() > 0) {
            if (!chunks.isEmpty()) {
                chunks.set(chunks.size() - 1, chunks.get(chunks.size() - 1) + hold);
            } else {
                chunks.add(hold.toString());
            }
        }
        return chunks;
    }

    /**
     * 把宽过整行的单元按「安全块」硬切。
     * 超长英文词 / 超长引号短语在正常文案里不会出现，但真出现了也不能把
     * 版式撑破 —— 宁可切碎，也不能让文字溢出卡片。
     */
    private static java.util.List<NoteUnit> noteHardSplit(java.util.List<NoteUnit> units,
                                                          NoteMeasurer measurer, int maxWidth) {
        java.util.List<NoteUnit> out = new java.util.ArrayList<NoteUnit>();
        for (NoteUnit u : units) {
            if (measurer.measure(u.text) <= maxWidth) {
                out.add(u);
                continue;
            }
            boolean sp = u.sep;
            StringBuilder cur = new StringBuilder();
            for (String ch : noteSafeChunks(u.text)) {
                if (cur.length() > 0 && measurer.measure(cur.toString() + ch) > maxWidth) {
                    out.add(new NoteUnit(cur.toString(), sp));
                    sp = false;
                    cur.setLength(0);
                    cur.append(ch);
                } else {
                    cur.append(ch);
                }
            }
            if (cur.length() > 0) out.add(new NoteUnit(cur.toString(), sp));
        }
        return out;
    }

    /**
     * 把一段说明文字按宽度折成若干行。
     *
     * measurer : 能量出一段文字有多宽（安卓端用 Paint 测量）
     * maxWidth : 每行最多允许多宽（像素）
     *
     * 规则（桌面版 weaknet.wrap_note_lines 完全一致）：
     *   · 引号短语、英文单词、数字带单位整体不可拆开
     *   · 断行只发生在排版单元之间；行首不留空格
     *   · 收尾标点不另起一行，起头标点不留在行末（避头尾）
     *   · 单个单元宽过整行时按字符硬切，不撑破版式
     */
    static java.util.List<String> wrapNoteLines(String text, NoteMeasurer measurer, int maxWidth) {
        java.util.List<String> lines = new java.util.ArrayList<String>();
        if (text == null || text.length() == 0) return lines;
        java.util.List<NoteUnit> units = noteHardSplit(noteGroupUnits(splitNoteAtoms(text)),
                measurer, maxWidth);
        if (units.isEmpty()) return lines;
        java.util.List<NoteUnit> cur = new java.util.ArrayList<NoteUnit>();
        int curW = 0;
        for (NoteUnit u : units) {
            int w = measurer.measure(u.text);
            int add = ((u.sep && !cur.isEmpty()) ? measurer.measure(" ") : 0) + w;
            if (!cur.isEmpty() && curW + add > maxWidth) {
                lines.add(noteUnitsText(cur));
                cur = new java.util.ArrayList<NoteUnit>();
                curW = 0;
                add = w;                 // 断行后行首不留空格
            }
            cur.add(u);
            curW += add;
        }
        if (!cur.isEmpty()) lines.add(noteUnitsText(cur));
        return lines;
    }

    /** 文字测宽器：安卓端用 Paint 实现，纯逻辑测试里可以自己实现一个 */
    interface NoteMeasurer {
        int measure(String text);
    }

    /**
     * 文字测宽器：按「汉字算 1 个单位、ASCII 算 0.5 个单位」估算。
     * 纯逻辑测试用（不依赖 Android 的 Paint，能在 JVM 上跑）。
     * 界面上要精确排版时由 ParamSheet 传入一个基于 Paint 的实现。
     */
    static class NoteRoughMeasurer implements NoteMeasurer {
        private final int unit;

        NoteRoughMeasurer(int unit) {
            this.unit = unit;
        }

        public int measure(String text) {
            if (text == null) return 0;
            int w = 0;
            for (int i = 0; i < text.length(); i++) {
                char ch = text.charAt(i);
                if (ch == ' ') w += unit / 4;
                else if (isNoteCjk(ch)) w += unit;
                else w += unit / 2;
            }
            return w;
        }
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
