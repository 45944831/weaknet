package com.weaknet.app;

/**
 * FlowMatch 边界穷举单测（纯 JDK 可跑）。
 * 回归背景：sameFlow 早期只核对 IP 不核对端口，端口对槽位折叠后
 * 同 dstPort 的新连接撞上残留会话，SYN 被当重传吞掉 → 连接挂死。
 */
public class _FlowMatchTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + "  <- " + extra); }
    }

    /** 造一个最小 IPv4+TCP 包（20 IP + 20 TCP） */
    static byte[] tcpPkt(String srcIp, int srcPort, String dstIp, int dstPort) {
        byte[] p = new byte[40];
        p[0] = 0x45; p[2] = 0; p[3] = 40;               // totalLen=40
        byte[] s = IpUtil.ipToBytes(srcIp), d = IpUtil.ipToBytes(dstIp);
        System.arraycopy(s, 0, p, 12, 4);
        System.arraycopy(d, 0, p, 16, 4);
        p[9] = 6;
        p[20] = (byte) (srcPort >> 8); p[21] = (byte) srcPort;
        p[22] = (byte) (dstPort >> 8); p[23] = (byte) dstPort;
        p[32] = (byte) 0x50;                             // dataOff=5
        return p;
    }

    public static void main(String[] a) {
        byte[] cip = IpUtil.ipToBytes("10.8.0.2");
        byte[] rip = IpUtil.ipToBytes("192.168.1.49");

        // 1) 四元组全中 -> true
        ck("同四元组命中", FlowMatch.same(tcpPkt("10.8.0.2", 40702, "192.168.1.49", 9127),
           40702, 9127, cip, rip), "should match");

        // 2) 同 IP 同 dstPort、仅 srcPort 不同 -> 必须 false（本次回归核心）
        ck("仅 srcPort 不同必须不命中", !FlowMatch.same(tcpPkt("10.8.0.2", 40703, "192.168.1.49", 9127),
           40702, 9127, cip, rip), "stale session swallowed new SYN");

        // 3) 同 IP 同 srcPort、仅 dstPort 不同 -> false
        ck("仅 dstPort 不同必须不命中", !FlowMatch.same(tcpPkt("10.8.0.2", 40702, "192.168.1.49", 9128),
           40702, 9127, cip, rip), "wrong dst port");

        // 4) IP 不同 -> false
        ck("clientIp 不同不命中", !FlowMatch.same(tcpPkt("10.8.0.9", 40702, "192.168.1.49", 9127),
           40702, 9127, cip, rip), "wrong client ip");
        ck("remoteIp 不同不命中", !FlowMatch.same(tcpPkt("10.8.0.2", 40702, "192.168.1.50", 9127),
           40702, 9127, cip, rip), "wrong remote ip");

        // 5) 全端口组合扫荡：每个 (cp, rp) 下，同四元组必中、任一字段变动必不中
        boolean ok = true; String bad = "";
        int[] ports = {0, 1, 80, 1024, 0x7FFF, 0x8000, 0x8001, 0xFFFE, 0xFFFF, 40702, 59786};
        for (int cp : ports) {
            for (int rp : ports) {
                if (!FlowMatch.same(tcpPkt("10.8.0.2", cp, "192.168.1.49", rp), cp, rp, cip, rip)) {
                    ok = false; bad = "miss " + cp + "->" + rp; break;
                }
                // srcPort+1
                if (FlowMatch.same(tcpPkt("10.8.0.2", (cp + 1) & 0xFFFF, "192.168.1.49", rp), cp, rp, cip, rip)) {
                    ok = false; bad = "false-hit srcPort " + cp + "->" + rp; break;
                }
                // dstPort+1
                if (FlowMatch.same(tcpPkt("10.8.0.2", cp, "192.168.1.49", (rp + 1) & 0xFFFF), cp, rp, cip, rip)) {
                    ok = false; bad = "false-hit dstPort " + cp + "->" + rp; break;
                }
            }
            if (!ok) break;
        }
        ck("端口组合扫荡（" + (ports.length * ports.length * 3) + " 例）", ok, bad);

        // 6) 端口 0 与 0xFFFF 极端值不混淆
        ck("端口 0 vs 0xFFFF 不混淆",
           !FlowMatch.same(tcpPkt("10.8.0.2", 0, "192.168.1.49", 0), 0xFFFF, 0xFFFF, cip, rip), "");

        System.out.println("\n_FlowMatchTest: " + pass + " pass, " + fail + " fail");
        if (fail > 0) System.exit(1);
    }
}
