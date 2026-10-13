package com.weaknet.app;

/** IPv4 / TCP / UDP 报文解析与构造 */
public class IpUtil {
    public static final int FIN = 0x01, SYN = 0x02, RST = 0x04, PSH = 0x08, ACK = 0x10;

    public static int u16(byte[] b, int i) {
        return ((b[i] & 0xFF) << 8) | (b[i + 1] & 0xFF);
    }

    public static long u32(byte[] b, int i) {
        return ((long) (b[i] & 0xFF) << 24) | ((long) (b[i + 1] & 0xFF) << 16)
                | ((long) (b[i + 2] & 0xFF) << 8) | (b[i + 3] & 0xFF);
    }

    public static void put16(byte[] b, int i, int v) {
        b[i] = (byte) (v >> 8);
        b[i + 1] = (byte) v;
    }

    public static void put32(byte[] b, int i, long v) {
        b[i] = (byte) (v >> 24);
        b[i + 1] = (byte) (v >> 16);
        b[i + 2] = (byte) (v >> 8);
        b[i + 3] = (byte) v;
    }

    public static String ipToString(byte[] b, int off) {
        return (b[off] & 0xFF) + "." + (b[off + 1] & 0xFF) + "."
                + (b[off + 2] & 0xFF) + "." + (b[off + 3] & 0xFF);
    }

    public static byte[] ipToBytes(String ip) {
        String[] p = ip.split("\\.");
        byte[] r = new byte[4];
        for (int i = 0; i < 4; i++) {
            r[i] = (byte) Integer.parseInt(p[i]);
        }
        return r;
    }

    /** 通用网际校验和（seed 用于 TCP/UDP 伪首部） */
    public static int checksum(byte[] data, int off, int len, long seed) {
        long sum = seed;
        int i = off;
        int left = len;
        while (left > 1) {
            sum += ((data[i] & 0xFF) << 8) | (data[i + 1] & 0xFF);
            i += 2;
            left -= 2;
        }
        if (left == 1) sum += (data[i] & 0xFF) << 8;
        while ((sum >> 16) != 0) sum = (sum & 0xFFFF) + (sum >> 16);
        return (int) (~sum & 0xFFFF);
    }

    /** 构造 IPv4 + TCP 报文 */
    public static byte[] buildTcp(String src, int srcPort, String dst, int dstPort,
                                  long seq, long ack, int flags,
                                  byte[] payload, int payloadLen,
                                  int window, Integer mss) {
        int tcpHeaderLen = 20 + (mss != null ? 4 : 0);
        int total = 20 + tcpHeaderLen + payloadLen;
        byte[] pkt = new byte[total];
        byte[] s = ipToBytes(src);
        byte[] d = ipToBytes(dst);

        pkt[0] = 0x45;
        pkt[1] = 0x00;
        put16(pkt, 2, total);
        put16(pkt, 4, 0);
        put16(pkt, 6, 0x4000);          // Don't Fragment
        pkt[8] = 64;                    // TTL
        pkt[9] = 6;                     // TCP
        put16(pkt, 10, 0);
        System.arraycopy(s, 0, pkt, 12, 4);
        System.arraycopy(d, 0, pkt, 16, 4);

        int t = 20;
        put16(pkt, t, srcPort);
        put16(pkt, t + 2, dstPort);
        put32(pkt, t + 4, seq);
        put32(pkt, t + 8, ack);
        pkt[t + 12] = (byte) ((tcpHeaderLen / 4) << 4);
        pkt[t + 13] = (byte) flags;
        put16(pkt, t + 14, Math.min(window, 65535));
        put16(pkt, t + 16, 0);
        put16(pkt, t + 18, 0);
        if (mss != null) {
            pkt[t + 20] = 2;            // kind = MSS
            pkt[t + 21] = 4;            // len
            put16(pkt, t + 22, mss);
        }
        if (payload != null && payloadLen > 0) {
            System.arraycopy(payload, 0, pkt, 20 + tcpHeaderLen, payloadLen);
        }

        // TCP 校验和（伪首部 + 报文段）
        long pseudo = ((long) u16(s, 0)) + ((long) u16(s, 2))
                + ((long) u16(d, 0)) + ((long) u16(d, 2)) + 6L + (tcpHeaderLen + payloadLen);
        put16(pkt, t + 16, checksum(pkt, 20, tcpHeaderLen + payloadLen, pseudo));
        // IP 头校验和
        put16(pkt, 10, checksum(pkt, 0, 20, 0));
        return pkt;
    }

    /** 构造 IPv4 + UDP 报文（UDP 校验和置 0，IPv4 下合法） */
    public static byte[] buildUdp(String src, int srcPort, String dst, int dstPort,
                                  byte[] payload, int payloadLen) {
        int total = 20 + 8 + payloadLen;
        byte[] pkt = new byte[total];
        byte[] s = ipToBytes(src);
        byte[] d = ipToBytes(dst);

        pkt[0] = 0x45;
        pkt[1] = 0x00;
        put16(pkt, 2, total);
        put16(pkt, 4, 0);
        put16(pkt, 6, 0x4000);
        pkt[8] = 64;
        pkt[9] = 17;                    // UDP
        put16(pkt, 10, 0);
        System.arraycopy(s, 0, pkt, 12, 4);
        System.arraycopy(d, 0, pkt, 16, 4);
        put16(pkt, 20, srcPort);
        put16(pkt, 22, dstPort);
        put16(pkt, 24, 8 + payloadLen);
        put16(pkt, 26, 0);              // 不校验
        if (payload != null && payloadLen > 0) {
            System.arraycopy(payload, 0, pkt, 28, payloadLen);
        }
        put16(pkt, 10, checksum(pkt, 0, 20, 0));
        return pkt;
    }
}
