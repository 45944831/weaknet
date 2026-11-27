package com.weaknet.app;

/** ShareNet 推送鉴权的纯 JDK 回归：409/403/放行三档与 Windows 侧对齐。 */
public class _ShareAuthTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, Object extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + (extra == null ? "" : "  <- " + extra)); }
    }

    static int code(Integer v) {
        return v == null ? 0 : v.intValue();
    }

    public static void main(String[] args) {
        int cur = ShareNet.SHARE_VER;
        int old = cur - 1;

        System.out.println("== 版本判定 ==");
        ck("旧版未开 legacy → 409",
                code(ShareNet.authVerdict(old, "1234", "1234", false)) == 409, null);
        ck("旧版开了 legacy → 放行",
                ShareNet.authVerdict(old, "", "1234", true) == null, null);
        ck("新版 → 不看 legacy 标志",
                ShareNet.authVerdict(cur, "1234", "1234", false) == null, null);

        System.out.println("== PIN 判定 ==");
        ck("PIN 正确 → 放行",
                ShareNet.authVerdict(cur, "1234", "1234", false) == null, null);
        ck("PIN 错误 → 403",
                code(ShareNet.authVerdict(cur, "0000", "1234", false)) == 403, null);
        ck("空 PIN → 403",
                code(ShareNet.authVerdict(cur, "", "1234", false)) == 403, null);
        ck("非数字字符被 normPin 剔除后匹配 → 放行",
                ShareNet.authVerdict(cur, "12ab34", "1234", false) == null, null);
        ck("数字不足 4 位 → 403",
                code(ShareNet.authVerdict(cur, "123", "1234", false)) == 403, null);
        ck("带空格 PIN 规范化后匹配 → 放行",
                ShareNet.authVerdict(cur, " 1 2 3 4 ", "1234", false) == null, null);

        System.out.println("== fail-closed ==");
        ck("本机未设 PIN → 409（不得 fail-open）",
                code(ShareNet.authVerdict(cur, "1234", "", false)) == 409, null);
        ck("本机 PIN 为 null → 409",
                code(ShareNet.authVerdict(cur, "1234", null, false)) == 409, null);
        // 旧版分支即使本机没 PIN 也只看 legacy：保持与 Windows 侧一致
        ck("旧版+legacy+本机无PIN → 放行（只进收件箱）",
                ShareNet.authVerdict(old, "", "", true) == null, null);

        System.out.println("== 错误文案 ==");
        ck("409 文案提到旧版/未设 PIN",
                ShareNet.authErrorText(409).length() > 0, null);
        ck("403 文案是 PIN 不正确",
                "PIN 不正确".equals(ShareNet.authErrorText(403)), null);

        System.out.println("\n结果: " + pass + " 通过 / " + fail + " 失败");
        if (fail > 0) System.exit(1);
    }
}
