package com.weaknet.app;

import java.util.Arrays;

/**
 * 四档 MTU/MSS 联动 + 缓冲池化安全性（纯 JDK 可跑）。
 *
 * 四档吞吐 A/B 必须在真机上测（改 setMtu 会影响分片与 PMTUD，默认仍 1500）；
 * 这里锁住的是「实验一旦开跑，三处参数必然一起变」和「复用缓冲不会串包」——
 * 这两条错了，真机实验会得出「提 MTU 没用」或数据损坏的错误结论。
 */
public class _MtuAbTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + "  <- " + extra); }
    }

    static final int[] MTUS = {1500, 4096, 8192, 16384};
    static final byte[] SRC = {10, 8, 0, 2};
    static final byte[] DST = {10, 8, 0, 1};

    static byte[] packet(byte[] payload, int len) {
        return IpUtil.buildTcp(SRC, 80, DST, 12345, 1L, 2L,
                IpUtil.PSH | IpUtil.ACK, payload, len, 65535, null);
    }

    public static void main(String[] a) {
        // 1) 四档：MSS 默认值、下游缓冲、对端 MSS 上限全部随 MTU 派生
        for (int mtu : MTUS) {
            int expected = mtu - 40;
            ck("MTU " + mtu + " 默认 MSS = MTU-40",
               VpnNetService.mssForMtu(mtu, 0) == expected,
               "" + VpnNetService.mssForMtu(mtu, 0));
            ck("MTU " + mtu + " 下游缓冲同步派生",
               VpnNetService.downstreamBufferForMtu(mtu) == expected,
               "" + VpnNetService.downstreamBufferForMtu(mtu));
            ck("MTU " + mtu + " 对端超大 MSS 被裁到 MTU-40",
               VpnNetService.mssForMtu(mtu, 90000) == expected,
               "" + VpnNetService.mssForMtu(mtu, 90000));
            ck("MTU " + mtu + " 对端较小 MSS 原样保留",
               VpnNetService.mssForMtu(mtu, 1200) == 1200,
               "" + VpnNetService.mssForMtu(mtu, 1200));
        }
        boolean mono = true;
        int prev = -1;
        for (int mtu = 576; mtu <= 20000; mtu += 137) {
            int v = VpnNetService.downstreamBufferForMtu(mtu);
            if (v < prev) { mono = false; break; }
            prev = v;
        }
        ck("下游缓冲随 MTU 单调不减", mono, "");

        // 2) 池化的前提：buildTcp 必须把 payload 复制进新报文，不能持有引用。
        //    若这条不成立，归还后的缓冲被下一次 read 覆写会串包。
        byte[] payload = new byte[]{11, 22, 33, 44, 55};
        byte[] pkt = packet(payload, payload.length);
        ck("报文长度 = 40B 头 + payload", pkt.length == 40 + payload.length,
           "len=" + pkt.length);
        ck("报文尾部与 payload 一致",
           Arrays.equals(Arrays.copyOfRange(pkt, 40, pkt.length), payload), "");
        byte[] snapshot = Arrays.copyOfRange(pkt, 40, pkt.length);
        payload[0] = 99;
        ck("buildTcp 不持有 payload 引用（改源数组不污染报文）",
           Arrays.equals(Arrays.copyOfRange(pkt, 40, pkt.length), snapshot),
           Arrays.toString(Arrays.copyOfRange(pkt, 40, pkt.length)));

        // 3) 模拟「借出 → 构包 → 归还 → 覆写 → 再构包」，验证复用不串包
        byte[] slot = new byte[1460];
        System.arraycopy(new byte[]{1, 1, 1, 1}, 0, slot, 0, 4);
        byte[] first = packet(slot, 4);
        Arrays.fill(slot, (byte) 0);                 // 归还后被下一次 read 覆写
        System.arraycopy(new byte[]{2, 2, 2, 2}, 0, slot, 0, 4);
        byte[] second = packet(slot, 4);
        ck("复用缓冲后第一个报文未被污染",
           Arrays.equals(Arrays.copyOfRange(first, 40, 44), new byte[]{1, 1, 1, 1}),
           Arrays.toString(Arrays.copyOfRange(first, 40, 44)));
        ck("复用缓冲后第二个报文内容正确",
           Arrays.equals(Arrays.copyOfRange(second, 40, 44), new byte[]{2, 2, 2, 2}),
           Arrays.toString(Arrays.copyOfRange(second, 40, 44)));

        // 4) 默认档仍是 1500，实验不能悄悄改掉产品默认值
        ck("生产默认 MTU 保持 1500", VpnNetService.tunMtu() == 1500,
           "" + VpnNetService.tunMtu());
        // 5) 16 KB 档只在显式选择时生效，切完必须能切回
        ck("16384 档可选中", VpnNetService.applyMtuTier(16384)
           && VpnNetService.tunMtu() == 16384, "" + VpnNetService.tunMtu());
        ck("切回 1500 生效", VpnNetService.applyMtuTier(1500)
           && VpnNetService.tunMtu() == 1500, "" + VpnNetService.tunMtu());

        System.out.println("\n_MtuAbTest: pass=" + pass + " fail=" + fail);
        if (fail > 0) System.exit(1);
    }
}
