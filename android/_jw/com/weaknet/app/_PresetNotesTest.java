package com.weaknet.app;

import java.util.HashSet;
import java.util.Set;

/**
 * 场景说明（「！」入口）数据层单测（纯 JDK 可跑）。
 *
 * 回归背景：17 支内置档位原来只有名字，界面上分不清「4G 弱网」和「弱 Wi-Fi」
 * 差在哪、「黑洞」和「完全断网」机制有什么不同。新增「！」入口后，说明文案与
 * 参数摘要成了用户理解档位的唯一来源 —— 漏一支、文案重复、参数摘要为空，
 * 界面上都会表现成「点开一片空白」，所以在这里把数据完整性钉死。
 */
public class _PresetNotesTest {
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
        // ---- N1 每一支档位都有注释 ----
        StringBuilder miss = new StringBuilder();
        for (String k : Profile.KEYS) {
            if (Profile.noteOf(k).length() == 0) miss.append(k).append(',');
        }
        ck("N1 每支档位都有注释", miss.length() == 0,
                miss.length() == 0 ? Profile.KEYS.length + " 支全覆盖" : "缺：" + miss);

        // ---- N2 注释互不重复（复制粘贴漏改会在这里露出来）----
        Set<String> seen = new HashSet<String>();
        StringBuilder dup = new StringBuilder();
        for (String k : Profile.KEYS) {
            String n = Profile.noteOf(k);
            if (!seen.add(n)) dup.append(k).append(',');
        }
        ck("N2 注释互不重复", dup.length() == 0,
                dup.length() == 0 ? "全部唯一" : "重复：" + dup);

        // ---- N3 注释长度合理（太短说明没写完）----
        StringBuilder brief = new StringBuilder();
        for (String k : Profile.KEYS) {
            int len = Profile.noteOf(k).length();
            if (len < 12) brief.append(k).append("(").append(len).append("),");
        }
        ck("N3 注释长度都够说明问题（>=12 字）", brief.length() == 0,
                brief.length() == 0 ? "最短 " + minNoteLen() + " 字" : "过短：" + brief);

        // ---- N4 每支档位都有参数摘要，且不含占位符 ----
        StringBuilder noDetail = new StringBuilder();
        for (String k : Profile.KEYS) {
            String d = Profile.build(k).detail();
            // "%%" 说明格式化时多写了一个转义；"null" 说明拼串时漏了值
            if (d.length() == 0 || d.contains("%%") || d.contains("null")) {
                noDetail.append(k).append(',');
            }
        }
        ck("N4 每支档位都有参数摘要", noDetail.length() == 0,
                noDetail.length() == 0 ? "全部非空" : "异常：" + noDetail);

        // ---- N5 参数摘要里的关键数字对得上档位定义 ----
        ck("N5a Wi-Fi 档位摘要含带宽", Profile.build("wifi").detail().contains("50000"));
        ck("N5b 高丢包档位摘要含 30% 丢包",
                Profile.build("high_loss").detail().contains("丢包 30%"),
                Profile.build("high_loss").detail());
        ck("N5c 频繁断连档位摘要含周期断网",
                Profile.build("flapping").detail().contains("每 8s 断网 3s"),
                Profile.build("flapping").detail());
        ck("N5d 黑洞档位摘要含黑洞比例",
                Profile.build("blackhole").detail().contains("黑洞 100%"),
                Profile.build("blackhole").detail());
        ck("N5e 完全断网档位摘要含拒连比例",
                Profile.build("airplane").detail().contains("拒连 100%"),
                Profile.build("airplane").detail());
        ck("N5f 直连档位摘要标出不限制",
                Profile.build("direct").detail().contains("不限制"),
                Profile.build("direct").detail());
        ck("N5g 卡顿只在有丢包的档位出现",
                !Profile.build("satellite").detail().contains("停顿")
                        && Profile.build("high_loss").detail().contains("停顿"),
                "satellite=" + Profile.build("satellite").detail());

        // ---- N6 未知 / 空 key 安全返回空串（界面据此跳过该行）----
        ck("N6a noteOf(null) 返回空串", Profile.noteOf(null).length() == 0);
        ck("N6b noteOf(未知 key) 返回空串", Profile.noteOf("no_such_scene").length() == 0);

        // ---- N7 三个分类都有档位（说明窗口按分类分组，空分类会留一个孤立标题）----
        Set<String> cats = new HashSet<String>();
        for (String k : Profile.KEYS) cats.add(Profile.catOf(k));
        ck("N7 三个分类都有档位", cats.size() == 3, "实际分类：" + cats);

        // ---- N8 每支档位都能拿到按钮短名（说明窗口的标题用它）----
        StringBuilder noShort = new StringBuilder();
        for (String k : Profile.KEYS) {
            if (Profile.shortOf(k).length() == 0) noShort.append(k).append(',');
        }
        ck("N8 每支档位都有短名", noShort.length() == 0,
                noShort.length() == 0 ? "全部有" : "缺：" + noShort);

        // ---- N9 注释与参数语义一致：不能出现「说是黑洞、参数却没有黑洞」----
        ck("N9a 黑洞档位注释提到挂住不返回",
                Profile.noteOf("blackhole").contains("挂住"));
        ck("N9b 完全断网档位注释强调「立刻失败」",
                Profile.noteOf("airplane").contains("立即") && Profile.noteOf("airplane").contains("拒绝"));
        ck("N9c DNS 档位注释说明 IP 直连仍通",
                Profile.noteOf("dns_fail").contains("IP"));

        System.out.println();
        System.out.println("  " + pass + " pass, " + fail + " fail");
        if (fail > 0) System.exit(1);
    }

    private static int minNoteLen() {
        int m = Integer.MAX_VALUE;
        for (String k : Profile.KEYS) m = Math.min(m, Profile.noteOf(k).length());
        return m;
    }
}
