package com.weaknet.app;

import com.weaknet.app.WeakNet;
import java.util.Arrays;
import java.util.List;

/** 接管范围设置的规范化逻辑单测（纯 JDK 可跑，不依赖 Android 框架） */
public class _ScopeTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + "  <- " + extra); }
    }

    static List<String> L(String... s) { return Arrays.asList(s); }

    public static void main(String[] a) {
        WeakNet w = WeakNet.I;

        ck("默认 all", "all".equals(w.scopeMode) && w.scopePkgs.isEmpty(),
           w.scopeMode + " / " + w.scopePkgs);

        String m = w.setScope("only", L("com.a", "com.b"));
        ck("only 模式生效", "only".equals(m) && "only".equals(w.scopeMode), w.scopeMode);
        ck("only 名单正确", w.scopePkgs.equals(L("com.a", "com.b")), w.scopePkgs.toString());

        w.setScope("only", L("com.a", " com.a ", "com.b", "", null, "  "));
        ck("去重 / 去首尾空格 / 丢弃空值与 null",
           w.scopePkgs.equals(L("com.a", "com.b")), w.scopePkgs.toString());

        m = w.setScope("exclude", L("com.x"));
        ck("exclude 模式生效", "exclude".equals(m) && "exclude".equals(w.scopeMode), w.scopeMode);
        ck("切模式会清空旧名单", w.scopePkgs.equals(L("com.x")), w.scopePkgs.toString());

        m = w.setScope("bogus", L("com.y"));
        ck("非法模式回落 all", "all".equals(m) && "all".equals(w.scopeMode), w.scopeMode);

        w.setScope("only", null);
        ck("名单传 null 不崩且为空", "only".equals(w.scopeMode) && w.scopePkgs.isEmpty(),
           w.scopeMode + " / " + w.scopePkgs);

        w.setScope("all", L("com.z"));
        ck("切回 all", "all".equals(w.scopeMode), w.scopeMode);

        System.out.println("\n通过 " + pass + " / 失败 " + fail);
        System.out.println(fail == 0 ? "总体: PASS" : "总体: FAIL");
        if (fail > 0) System.exit(1);
    }
}
