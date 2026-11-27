package com.weaknet.app;

/** CtrlHostCheck 的纯 JDK 回归：控制口只认本机 Host，任何非空 Origin 一律拒绝。 */
public class _CtrlHostCheckTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, Object extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + (extra == null ? "" : "  <- " + extra)); }
    }

    public static void main(String[] args) {
        int port = 8899;

        System.out.println("== Host 校验 ==");
        ck("adb curl 默认 Host 可用", CtrlHostCheck.ok("127.0.0.1:8899", null, port), null);
        ck("localhost:8899 可用", CtrlHostCheck.ok("localhost:8899", null, port), null);
        ck("不带端口的本机 Host 可用", CtrlHostCheck.ok("127.0.0.1", null, port), null);
        ck("IPv6 回环可用", CtrlHostCheck.ok("[::1]:8899", null, port), null);
        ck("缺 Host 拒绝", !CtrlHostCheck.ok(null, null, port), null);
        ck("空 Host 拒绝", !CtrlHostCheck.ok("  ", null, port), null);
        ck("外部域名拒绝（DNS rebinding）", !CtrlHostCheck.ok("evil.example:8899", null, port), null);
        ck("0.0.0.0 不是本机控制口", !CtrlHostCheck.ok("0.0.0.0:8899", null, port), null);
        // adb forward 会把 Host 端口改写成转发端口（18899），与监听端口不同；
        // 要求端口一致会把官方调试通道和所有真机脚本挡成 403（2026-11-26 实测）。
        ck("adb forward 改写端口仍可用", CtrlHostCheck.ok("127.0.0.1:18899", null, port), null);
        ck("畸形端口不影响主机判定", CtrlHostCheck.ok("localhost:abc", null, port), null);
        ck("大小写不敏感", CtrlHostCheck.ok("LOCALHOST:8899", null, port), null);

        System.out.println("== Origin 校验 ==");
        ck("无 Origin 放行", CtrlHostCheck.ok("127.0.0.1:8899", null, port), null);
        // sandbox iframe 的 opaque origin 就是 "null"：旧代码在这里放行，
        // 攻击者可用 <iframe sandbox> + no-cors 打通控制口（CSRF 绕过）。
        // 对齐 Windows ctrl_host_ok：任何非空 Origin 一律拒绝。
        ck("Origin: null 拒绝（sandbox iframe 绕过）",
                !CtrlHostCheck.ok("127.0.0.1:8899", "null", port), null);
        ck("同源 Origin 也拒绝", !CtrlHostCheck.ok("127.0.0.1:8899",
                "http://127.0.0.1:8899", port), null);
        ck("同源 localhost 也拒绝", !CtrlHostCheck.ok("localhost:8899",
                "http://localhost:8899/settings", port), null);
        ck("跨站 Origin 拒绝", !CtrlHostCheck.ok("127.0.0.1:8899",
                "https://evil.example", port), null);
        ck("带错端口的 Origin 拒绝", !CtrlHostCheck.ok("127.0.0.1:8899",
                "http://127.0.0.1:9999", port), null);
        ck("无端口 Origin 拒绝", !CtrlHostCheck.ok("127.0.0.1:8899",
                "http://localhost", port), null);
        ck("非 http(s) scheme 拒绝", !CtrlHostCheck.ok("127.0.0.1:8899",
                "file://127.0.0.1:8899", port), null);

        System.out.println("\n结果: " + pass + " 通过 / " + fail + " 失败");
        if (fail > 0) System.exit(1);
    }
}
