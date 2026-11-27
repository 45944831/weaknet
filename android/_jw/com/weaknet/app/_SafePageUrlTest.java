package com.weaknet.app;

/** SafePageUrl 的纯 JDK 回归：只有 https + 官方域名 + 443 才放行。 */
public class _SafePageUrlTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, Object extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + (extra == null ? "" : "  <- " + extra)); }
    }

    /** 模拟调用方：非法地址回退到硬编码发布页 */
    static String page(String url) {
        String s = SafePageUrl.safe(url);
        return s != null ? s : "https://github.com/45944831/weaknet/releases/latest";
    }

    public static void main(String[] args) {
        final String latest = "https://github.com/45944831/weaknet/releases/latest";

        System.out.println("== 合法 URL 原样放行 ==");
        ck("github.com 带路径", "https://github.com/45944831/weaknet/releases/tag/v1".equals(
                SafePageUrl.safe("https://github.com/45944831/weaknet/releases/tag/v1")), null);
        ck("www.github.com", "https://www.github.com/a/b".equals(
                SafePageUrl.safe("https://www.github.com/a/b")), null);
        ck("显式 443 端口", "https://github.com:443/a".equals(
                SafePageUrl.safe("https://github.com:443/a")), null);
        ck("带 query / fragment", "https://github.com/a?x=1#z".equals(
                SafePageUrl.safe("https://github.com/a?x=1#z")), null);
        ck("大小写不敏感", "HTTPS://GITHUB.COM/a".equals(
                SafePageUrl.safe("HTTPS://GITHUB.COM/a")), null);
        ck("尾点规范化", "https://github.com./a".equals(
                SafePageUrl.safe("https://github.com./a")), null);
        ck("尾点 + 443", "https://github.com.:443/a".equals(
                SafePageUrl.safe("https://github.com.:443/a")), null);

        System.out.println("== 非法域名 ==");
        ck("github.com.evil.com 拒绝", SafePageUrl.safe("https://github.com.evil.com/a") == null, null);
        ck("evilgithub.com 拒绝", SafePageUrl.safe("https://evilgithub.com/a") == null, null);
        ck("github.com.attacker.example 拒绝",
                SafePageUrl.safe("https://github.com.attacker.example/a") == null, null);
        ck("伪装成子路径的 evil 域名拒绝",
                SafePageUrl.safe("https://evil.com/github.com/a") == null, null);
        ck("IPv6 字面量拒绝", SafePageUrl.safe("https://[::1]/a") == null, null);

        System.out.println("== userinfo / scheme / 端口绕过 ==");
        ck("user@github.com 拒绝", SafePageUrl.safe("https://user@github.com/a") == null, null);
        ck("evil.com@github.com 拒绝", SafePageUrl.safe("https://evil.com@github.com/a") == null, null);
        ck("github.com@evil.com 拒绝", SafePageUrl.safe("https://github.com@evil.com/a") == null, null);
        ck("http 协议拒绝", SafePageUrl.safe("http://github.com/a") == null, null);
        ck("file 协议拒绝", SafePageUrl.safe("file://github.com/a") == null, null);
        ck("协议相对 URL 拒绝", SafePageUrl.safe("//github.com/a") == null, null);
        ck("javascript 伪协议拒绝", SafePageUrl.safe("javascript:alert(1)") == null, null);
        ck("非 443 端口拒绝", SafePageUrl.safe("https://github.com:8443/a") == null, null);
        ck("畸形端口拒绝", SafePageUrl.safe("https://github.com:443.evil.com/a") == null, null);
        ck("空端口拒绝", SafePageUrl.safe("https://github.com:/a") == null, null);
        ck("前导空白拒绝", SafePageUrl.safe(" https://github.com/a") == null, null);
        ck("反斜杠混淆拒绝", SafePageUrl.safe("https://github.com\\@evil.com/a") == null, null);

        System.out.println("== 空值与调用方回退 ==");
        ck("null 拒绝", SafePageUrl.safe(null) == null, null);
        ck("空串拒绝", SafePageUrl.safe("") == null, null);
        ck("非法 URL 回退到 PAGE_LATEST",
                latest.equals(page("https://github.com.evil.com/a")), page("https://github.com.evil.com/a"));
        ck("合法 URL 不回退",
                "https://www.github.com/a".equals(page("https://www.github.com/a")), null);

        System.out.println("\n结果: " + pass + " 通过 / " + fail + " 失败");
        if (fail > 0) System.exit(1);
    }
}
