package com.weaknet.app;

/**
 * 控制口的 Host / Origin 校验（防 DNS rebinding 与浏览器跨站请求）。
 *
 * 控制口只绑回环，但网页仍能把请求发到 127.0.0.1:port；Windows 端用 ctrl_host_ok
 * 挡这一层，Android 端此前缺失。与 Windows 端同语义：Host 必须指向回环，
 * 且任何非空 Origin 一律拒绝（包括 sandbox iframe 的 opaque origin "null"）。
 * 无 Origin 的 curl / adb 请求照常通行，不需要 token 或特殊 Content-Type。
 *
 * 纯 JDK 类：不碰 Android / org.json，_jw 下可直接单测（见 _CtrlHostCheckTest）。
 */
public final class CtrlHostCheck {
    private CtrlHostCheck() {
    }

    /**
     * @param hostHeader Host 头原始值（HttpIn.Req.header("host")）
     * @param originHeader Origin 头原始值，可为 null
     * @param port 控制口实际监听端口
     */
    public static boolean ok(String hostHeader, String originHeader, int port) {
        if (!hostIsLoopback(hostHeader)) return false;
        // 对齐 Windows ctrl_host_ok（`if headers.get("origin"): return False`）：
        // 任何非空 Origin 一律拒绝，包括 sandbox iframe 的 opaque origin "null"
        // （旧代码专门放行它，攻击者可借 <iframe sandbox> + no-cors 打通控制口）。
        // 无 Origin 的 curl / adb 请求照常通行；不要求 Content-Type、不加 token。
        // port 保留在签名里仅为兼容调用方：Host 不校验端口（见 hostIsLoopback）。
        if (originHeader != null && originHeader.trim().length() > 0) return false;
        return true;
    }

    /** Host 只校验主机名，**不校验端口**。
     *
     *  为什么：`adb forward tcp:18899 tcp:8899` 会把 Host 改写成
     *  `127.0.0.1:18899`，与实际监听端口不同——要求端口一致会把官方调试
     *  通道、以及所有真机自动化脚本挡在门外（实测 403）。
     *  端口校验也挡不住攻击：攻击者本来就能把请求发到正确端口、写正确的
     *  Host。真正起作用的是「Host 必须是回环字面量」（防 DNS rebinding）
     *  与「拒绝一切非空 Origin」（防跨站）。Windows 端 ctrl_host_ok 同样不看端口。
     */
    private static boolean hostIsLoopback(String raw) {
        if (raw == null) return false;
        String h = raw.trim().toLowerCase();
        if (h.length() == 0) return false;
        String hostPart;
        if (h.startsWith("[")) {                  // [::1]:18899
            int end = h.indexOf(']');
            if (end < 0) return false;
            hostPart = h.substring(0, end + 1);
        } else {
            int colon = h.lastIndexOf(':');
            hostPart = colon >= 0 ? h.substring(0, colon) : h;
        }
        return "127.0.0.1".equals(hostPart) || "localhost".equals(hostPart)
                || "[::1]".equals(hostPart);
    }

}
