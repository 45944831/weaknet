package com.weaknet.app;

/**
 * PortIdx 边界穷举单测（纯 JDK 可跑）。
 * 回归背景：旧实现 portIdx 直接返回 (src<<16)|dst 的 32 位组合，
 * 既在 srcPort>=0x8000 时溢出为负，也在 srcPort 较小时远超 65536 槽数组长度，
 * 导致每个进隧道的包都在建会话前 ArrayIndexOutOfBounds 被 catch 静默吞掉——
 * 真机 tun 路径表现为「完全不处理任何报文」。
 */
public class _PortIdxTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + "  <- " + extra); }
    }

    public static void main(String[] a) {
        // 1) 关键边界端口对：srcPort 跨 0x7FFF/0x8000 符号位边界 + 0/0xFFFF
        int[] edge = {0, 1, 2, 0x7FFE, 0x7FFF, 0x8000, 0x8001, 0xFFFE, 0xFFFF, 80, 443, 59786, 45000};
        boolean allInRange = true;
        String bad = "";
        for (int s : edge) {
            for (int d = 0; d <= 0xFFFF; d++) {
                int idx = PortIdx.of(s, d);
                if (idx < 0 || idx > 0xFFFF) { allInRange = false; bad = s + "->" + d + "=" + idx; break; }
            }
            if (!allInRange) break;
        }
        ck("边界 srcPort × 全 dstPort 恒在 [0,65535]", allInRange, bad);

        // 2) 全 dstPort × 全 srcPort（质数步进 251，覆盖高低位各种组合）≈ 1700 万次
        allInRange = true;
        long checked = 0;
        for (int d = 0; d <= 0xFFFF; d++) {
            for (int s = 0; s <= 0xFFFF; s += 251) {
                int idx = PortIdx.of(s, d);
                checked++;
                if (idx < 0 || idx > 0xFFFF) { allInRange = false; bad = s + "->" + d + "=" + idx; break; }
            }
            if (!allInRange) break;
        }
        ck("全 dst × src(步进251) 共 " + checked + " 组恒在 [0,65535]", allInRange, bad);

        // 3) 负端口（防御性入参）：u16 之外的脏值也不能越界
        allInRange = true;
        int[] dirty = {-1, -2, -256, -32768, Integer.MIN_VALUE, Integer.MAX_VALUE, 65536, 65537, 131072};
        for (int s : dirty) {
            for (int d : dirty) {
                int idx = PortIdx.of(s, d);
                if (idx < 0 || idx > 0xFFFF) { allInRange = false; bad = s + "->" + d + "=" + idx; break; }
            }
            if (!allInRange) break;
        }
        ck("脏入参（负数/超范围）也不越界", allInRange, bad);

        // 4) 确定性：同输入同输出
        boolean stable = true;
        for (int i = 0; i < 20000; i++) {
            int s = (i * 7919) & 0xFFFF, d = (i * 104729) & 0xFFFF;
            if (PortIdx.of(s, d) != PortIdx.of(s, d)) { stable = false; break; }
        }
        ck("确定性：同输入同输出", stable, "");

        // 5) 折叠语义：低 16 位即 dstPort（同 dst 不同 src 允许同槽，由 sameFlow 兜底）
        boolean folding = true;
        for (int d : new int[]{0, 1, 80, 443, 0x8000, 0xFFFF}) {
            for (int s : new int[]{0, 1, 0x8000, 0xFFFF, 59786}) {
                if (PortIdx.of(s, d) != (d & 0xFFFF)) { folding = false; break; }
            }
            if (!folding) break;
        }
        ck("折叠语义：idx == dstPort & 0xFFFF", folding, "");

        System.out.println("\n_PortIdxTest: " + pass + " pass, " + fail + " fail");
        if (fail > 0) System.exit(1);
    }
}
