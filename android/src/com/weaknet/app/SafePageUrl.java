package com.weaknet.app;

/**
 * 更新页跳转地址白名单（与 Windows 端 update_check.py:safe_page_url 等价的纯 JDK 实现）。
 *
 * API 响应里的 html_url 不可信：假 API / 被污染的响应能把用户导到钓鱼站。
 * 只有 https + hostname 精确等于 github.com / www.github.com + 端口为默认 443
 * （或未显式给出）的链接才放行；其余一律回退到调用方给的硬编码发布页。
 *
 * 用精确匹配而不是 endswith，是为了挡住 github.com.evil.com、
 * evilgithub.com、github.com.attacker.example 这类「看起来像官方」的域名；
 * 拒绝 userinfo（user@host）避免权威部分被混淆。
 *
 * 纯 JDK 类：不碰 Android / org.json，_jw 下可直接单测（见 _SafePageUrlTest）。
 */
public final class SafePageUrl {
    private static final String SCHEME = "https";
    private static final String H_GITHUB = "github.com";
    private static final String H_WWW = "www.github.com";
    private static final int DEFAULT_PORT = 443;

    private SafePageUrl() {
    }

    /**
     * @return 原样返回通过校验的 URL；不合法返回 null，由调用方回退到 PAGE_LATEST。
     */
    public static String safe(String url) {
        // 不 trim：前导空白会让 scheme 匹配失败（与 Python urlparse 一致）
        String s = url;
        if (s == null) return null;
        int sc = s.indexOf("://");
        if (sc != SCHEME.length() || !s.substring(0, sc).equalsIgnoreCase(SCHEME)) return null;

        String rest = s.substring(sc + 3);
        if (rest.length() == 0) return null;
        // authority 止于第一个 / ? #
        int end = rest.length();
        for (int i = 0; i < rest.length(); i++) {
            char c = rest.charAt(i);
            if (c == '/' || c == '?' || c == '#') { end = i; break; }
        }
        String authority = rest.substring(0, end);
        if (authority.length() == 0) return null;
        // userinfo 一律拒绝：https://user@github.com 的 host 虽是官方域名，
        // 但把「@ 前是权威」与「@ 后是权威」两种解析混在一起，容易被钓鱼页利用。
        if (authority.indexOf('@') >= 0) return null;

        String host;
        int port = -1;                       // -1 = 未显式给出，按 443 处理
        if (authority.startsWith("[")) {
            // IPv6 字面量不在白名单内
            return null;
        } else {
            int colon = authority.lastIndexOf(':');
            if (colon >= 0) {
                host = authority.substring(0, colon);
                String ps = authority.substring(colon + 1);
                if (ps.length() == 0) return null;
                try {
                    port = Integer.parseInt(ps);
                } catch (NumberFormatException e) {
                    return null;
                }
            } else {
                host = authority;
            }
        }
        // 去尾点：Python urlparse().hostname 也做 rstrip(".")
        while (host.length() > 0 && host.charAt(host.length() - 1) == '.') {
            host = host.substring(0, host.length() - 1);
        }
        host = host.toLowerCase();
        if (!H_GITHUB.equals(host) && !H_WWW.equals(host)) return null;
        if (port != -1 && port != DEFAULT_PORT) return null;
        return s;
    }
}
