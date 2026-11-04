package com.weaknet.app;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * 自定义场景 / 弱网组合取名上限的纯 JDK 单测（不需要真机、不碰 Android 控件）。
 *
 * 与 Windows 端 tests/test_unit_name_limit.py 一一对应：
 * 两端都从 spec.json 的 name_max 取同一个值，中文 / emoji 都按 Unicode 码点算 1 字。
 */
public class _NameLimitTest {
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

    static int cp(String s) {
        return s.codePointCount(0, s.length());
    }

    public static void main(String[] args) {
        String ten = "\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341";
        String longName = ten + "\u591a\u4f59";

        ck("A1 上限来自 SpecGen，且是 10",
                NameLimit.MAX == 10 && SpecGen.NAME_MAX == NameLimit.MAX,
                "MAX=" + NameLimit.MAX);

        ck("A2 压空白 + 截断到 10 字",
                NameLimit.clean("  \u5730\u94c1 \n \u65e9\u9ad8\u5cf0 \t ", "").equals("\u5730\u94c1 \u65e9\u9ad8\u5cf0"));
        ck("A3 中文按字符算，一个不多",
                NameLimit.clean(longName, "").equals(ten) && cp(NameLimit.clean(longName, "")) == 10,
                NameLimit.clean(longName, ""));
        ck("A4 空名走 fallback",
                NameLimit.clean("   ", "\u515c\u5e95").equals("\u515c\u5e95"));
        ck("A5 null 走 fallback",
                NameLimit.clean(null, "\u515c\u5e95").equals("\u515c\u5e95"));

        StringBuilder emo = new StringBuilder();
        for (int i = 0; i < 13; i++) emo.append("\ud83d\ude00");
        String clipped = NameLimit.clean(emo.toString(), "");
        ck("A6 emoji 按码点算 1 字且不截出半个代理对",
                cp(clipped) == 10 && clipped.length() == 20,
                "codePoints=" + cp(clipped) + " utf16=" + clipped.length());

        ck("A7 重名序号不会把名字撑过上限",
                cp(NameLimit.unique(Arrays.asList(ten), longName)) <= 10,
                NameLimit.unique(Arrays.asList(ten), longName));
        String u2 = NameLimit.unique(Arrays.asList(ten, ten.substring(0, 6) + " (2)"), longName);
        ck("A8 序号撞车会继续往后找", cp(u2) <= 10 && u2.endsWith("(3)"), u2);
        ck("A9 没重名时原样返回", NameLimit.unique(new ArrayList<String>(), ten).equals(ten));

        ck("B1 输入框已打满时再打一个字被拦下",
                NameLimit.clipInsert(ten, "A").equals(""));
        ck("B2 输入框还剩 2 个字，粘贴 5 个字只收 2 个",
                NameLimit.clipInsert(ten.substring(0, 8), "ABCDE").equals("AB"));
        ck("B3 没超上限时原样放行",
                NameLimit.clipInsert("\u4e00", "\u4e8c").equals("\u4e8c"));
        String emoAdd = "\ud83d\ude00\ud83d\ude00\ud83d\ude00";
        ck("B4 粘贴 emoji 也按码点算",
                cp(NameLimit.clipInsert(ten.substring(0, 8), emoAdd)) == 2);

        // 内置预设名最长 14 字，绝不能被 10 字上限误伤（段标签要跨端反查）
        String wifi = SpecGen.presetName("wifi_weak");
        ck("C1 内置预设显示名不归取名上限管",
                wifi.length() > NameLimit.MAX && NameLimit.trim(wifi, 40).equals(wifi),
                wifi);

        System.out.println();
        System.out.println("  " + pass + " pass, " + fail + " fail");
        if (fail > 0) System.exit(1);
    }
}
