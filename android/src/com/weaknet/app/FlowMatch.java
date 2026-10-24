package com.weaknet.app;

/**
 * 会话归属核对：包是否属于某个 (clientIp, clientPort, remoteIp, remotePort) 流。
 * 纯函数、无 Android 依赖，可单测穷举验证。
 *
 * 背景：端口对快查槽位只有 65536 个，同 dstPort 的新连接会撞上残留会话，
 * 命中后必须完整核对四元组。早期版本只核对 IP 不核对端口，导致旧会话
 * 把新 SYN 当重传吞掉（客户端永远等不到 SYN-ACK）。
 */
final class FlowMatch {
    private FlowMatch() {}

    /** pkt 是否属于 (clientPort, remotePort, clientIpB, remoteIpB) 这个流 */
    static boolean same(byte[] pkt, int clientPort, int remotePort,
                        byte[] clientIpB, byte[] remoteIpB) {
        int ihl = (pkt[0] & 0xF) * 4;
        if (IpUtil.u16(pkt, ihl) != clientPort) return false;
        if (IpUtil.u16(pkt, ihl + 2) != remotePort) return false;
        for (int i = 0; i < 4; i++) {
            if (pkt[12 + i] != clientIpB[i] || pkt[16 + i] != remoteIpB[i]) return false;
        }
        return true;
    }
}
