package com.weaknet.app;

import java.util.HashSet;
import java.util.Set;

/**
 * 参数说明（参数行「！」入口）数据层单测（纯 JDK 可跑）。
 *
 * 回归背景：参数编辑界面有 13 行参数，每行末尾一个「！」。用户点开看到的
 * 就是 {@link Profile#noteOfParam} 里的文案 —— 漏一个参数、文案重复、
 * 或者界面上的参数行跟注释表的 key 对不上，表现都是「点开一片空白」，
 * 所以在这里把「界面行 ↔ 注释表」的一致性钉死。
 *
 * 与桌面版的对应关系：文案必须与 weaknet.py 的 PARAM_NOTES 逐条一致，
 * 那条校验在 Python 侧（_t_param_notes.py）做，因为这里读不到 Python 源码。
 */
public class _ParamNotesTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok) {
        ck(name, ok, "");
    }

    static void ck(String name, boolean ok, String extra) {
        if (ok) {
            pass++;
            System.out.println("  PASS  " + name + (extra.length() > 0 ? "   " + extra : ""));
        } else {
            fail++;
            System.out.println("  FAIL  " + name + "  <- " + extra);
        }
    }

    public static void main(String[] a) {
        // ---- P1 界面上每一行参数都有注释（点「！」不会是空白）----
        StringBuilder miss = new StringBuilder();
        for (String[] f : MainPanels.FIELDS) {
            if (Profile.noteOfParam(f[0]).length() == 0) miss.append(f[0]).append(',');
        }
        ck("P1 每个参数行都有注释", miss.length() == 0,
                miss.length() == 0 ? MainPanels.FIELDS.length + " 行全覆盖" : "缺：" + miss);

        // ---- P2 参数行 key 互不重复（复制粘贴时漏改会在这里露出来）----
        Set<String> keys = new HashSet<String>();
        StringBuilder dupKey = new StringBuilder();
        for (String[] f : MainPanels.FIELDS) {
            if (!keys.add(f[0])) dupKey.append(f[0]).append(',');
        }
        ck("P2 参数行 key 互不重复", dupKey.length() == 0,
                dupKey.length() == 0 ? keys.size() + " 个" : "重复：" + dupKey);

        // ---- P3 注释文案互不重复 ----
        Set<String> notes = new HashSet<String>();
        StringBuilder dupNote = new StringBuilder();
        for (String[] f : MainPanels.FIELDS) {
            if (!notes.add(Profile.noteOfParam(f[0]))) dupNote.append(f[0]).append(',');
        }
        ck("P3 注释文案互不重复", dupNote.length() == 0,
                dupNote.length() == 0 ? "全部唯一" : "重复：" + dupNote);

        // ---- P4 注释长度够说明问题（太短说明没写完）----
        StringBuilder brief = new StringBuilder();
        for (String[] f : MainPanels.FIELDS) {
            int len = Profile.noteOfParam(f[0]).length();
            if (len < 30) brief.append(f[0]).append("(").append(len).append("),");
        }
        ck("P4 注释长度都够说明问题（>=30 字）", brief.length() == 0,
                brief.length() == 0 ? "最短 " + minNoteLen() + " 字" : "过短：" + brief);

        // ---- P5 每行都有显示名，且带上单位（浮层标题用它）----
        StringBuilder noLabel = new StringBuilder();
        for (String[] f : MainPanels.FIELDS) {
            if (f[1] == null || f[1].length() == 0) noLabel.append(f[0]).append(',');
        }
        ck("P5 每个参数行都有显示名", noLabel.length() == 0,
                noLabel.length() == 0 ? "全部有" : "缺：" + noLabel);

        // ---- P6 未知 / 空 key 安全返回空串 ----
        ck("P6a noteOfParam(null) 返回空串", Profile.noteOfParam(null).length() == 0);
        ck("P6b noteOfParam(未知 key) 返回空串",
                Profile.noteOfParam("no_such_param").length() == 0);

        // ---- P7 注释与参数语义一致（写错参数会在这里露出来）----
        ck("P7a 卡顿时长说明「只在丢包后生效」",
                Profile.noteOfParam("stall_ms").contains("丢包"),
                Profile.noteOfParam("stall_ms"));
        ck("P7b 丢包率说明停顿由卡顿时长决定",
                Profile.noteOfParam("loss_pct").contains("卡顿时长"));
        ck("P7c 断连率强调「必须重连」",
                Profile.noteOfParam("disconnect_pct").contains("重连"));
        ck("P7d 黑洞率强调「挂住」",
                Profile.noteOfParam("timeout_pct").contains("挂住"));
        ck("P7e 拒连率强调「立刻」失败",
                Profile.noteOfParam("reject_pct").contains("立刻"));
        ck("P7f DNS 说明 IP 直连不受影响",
                Profile.noteOfParam("dns_fail_pct").contains("IP"));
        ck("P7g 断网时长说明只在间隔 > 0 时生效",
                Profile.noteOfParam("flap_blackout_sec").contains("断网间隔"));
        ck("P7h 带宽说明 0 表示不限",
                Profile.noteOfParam("down_kbps").contains("0 = 不限")
                        && Profile.noteOfParam("up_kbps").contains("0 = 不限"));
        ck("P7i 抖动说明是 ± 浮动",
                Profile.noteOfParam("jitter_ms").contains("±"));
        ck("P7j 延迟说明往返 = 上行 + 下行",
                Profile.noteOfParam("latency_up_ms").contains("往返"));

        // ---- P8 注释里不能出现占位符 / 转义残留 ----
        StringBuilder dirty = new StringBuilder();
        for (String[] f : MainPanels.FIELDS) {
            String n = Profile.noteOfParam(f[0]);
            if (n.contains("%%") || n.contains("null") || n.contains("\\n")) {
                dirty.append(f[0]).append(',');
            }
        }
        ck("P8 注释无占位符与转义残留", dirty.length() == 0,
                dirty.length() == 0 ? "干净" : "异常：" + dirty);

        // ---- P9 多段文案用换行分段（单段长文在手机上很难读）----
        int multi = 0;
        for (String[] f : MainPanels.FIELDS) {
            if (Profile.noteOfParam(f[0]).indexOf('\n') >= 0) multi++;
        }
        ck("P9 注释都分成两段（作用 + 注意）", multi == MainPanels.FIELDS.length,
                multi + "/" + MainPanels.FIELDS.length);

        System.out.println();
        System.out.println("  " + pass + " pass, " + fail + " fail");
        if (fail > 0) System.exit(1);
    }

    private static int minNoteLen() {
        int m = Integer.MAX_VALUE;
        for (String[] f : MainPanels.FIELDS) {
            m = Math.min(m, Profile.noteOfParam(f[0]).length());
        }
        return m;
    }
}
