package com.weaknet.app;

/**
 * MSS 与 MTU 联动单测（纯 JDK 可跑）。
 *
 * 回归背景：TcpSession 曾把 MSS 硬编码钳在 1400，而 SYN-ACK 通告 1460、tun MTU 是
 * 1500。三者不一致时，即使把 setMtu 调到 16KB，对端仍按 ≤1400 分段，TUN 侧永远
 * 收不到大包 —— 实测会得出「提 MTU 没有收益」的错误结论。
 * 现在 MTU 是单一真值（VpnNetService.tunMtu()），MSS 上限从它派生，这里锁住这个约束。
 *
 * 2026-11-13 补充：MTU 从「编译期常量」变成「默认 1500 + 可验证实验档」，
 * 因此这里同时锁住档位白名单 —— 16384 档必须让下游缓冲落到 16 KB 级，
 * 非法档位必须被拒绝，否则真机 A/B 的结论无法复现。
 */
public class _MssClampTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + "  <- " + extra); }
    }

    public static void main(String[] a) {
        // 1) 常量自洽：默认 MSS = MTU - 40（IPv4 20B + TCP 20B）
        ck("默认 MTU 仍是 1500（默认不轻易改）",
           VpnNetService.tunMtu() == 1500, "mtu=" + VpnNetService.tunMtu());
        ck("defaultMss = MTU - 40",
           VpnNetService.defaultMss() == VpnNetService.tunMtu() - 40,
           "mss=" + VpnNetService.defaultMss());

        // 2) 常规值不被无谓压低：1460 必须原样通过（旧实现会给 1400）
        ck("1460 原样保留（不被压到 1400）",
           VpnNetService.clampMss(1460) == 1460, "" + VpnNetService.clampMss(1460));

        // 3) 上限随 MTU：超过 MTU-40 的值必须被裁到 MTU-40
        int over = VpnNetService.clampMss(9000);
        ck("9000 被裁到 MTU-40",
           over == VpnNetService.tunMtu() - 40, "" + over);

        // 4) 下限 536（RFC 879 的保守下限），非法值回落到默认
        ck("100 抬到 536", VpnNetService.clampMss(100) == 536,
           "" + VpnNetService.clampMss(100));
        ck("0 回落到默认 MSS",
           VpnNetService.clampMss(0) == VpnNetService.defaultMss(),
           "" + VpnNetService.clampMss(0));
        ck("-1 回落到默认 MSS",
           VpnNetService.clampMss(-1) == VpnNetService.defaultMss(),
           "" + VpnNetService.clampMss(-1));

        // 5) 单调性：输入越大，结果不得越小（避免出现负向映射）。
        //    注意从 1 开始：0/负数走的是「未知 MSS → 默认值」的特例（上面单独校验），
        //    它与数值大小的映射不是同一回事，混进来会让单调性判断假失败。
        boolean mono = true;
        String bad = "";
        int prev = -1;
        for (int m = 1; m <= 10000; m += 37) {
            int v = VpnNetService.clampMss(m);
            if (v < prev) { mono = false; bad = m + " -> " + v + " < " + prev; break; }
            prev = v;
        }
        ck("clampMss 单调不减", mono, bad);

        // 6) 档位白名单：合法档位生效，非法档位不改变当前值
        ck("16384 在档位白名单里", VpnNetService.applyMtuTier(16384), "");
        ck("切到 16384 后 tunMtu 生效", VpnNetService.tunMtu() == 16384,
           "" + VpnNetService.tunMtu());
        ck("16384 档 MSS = 16344", VpnNetService.defaultMss() == 16344,
           "" + VpnNetService.defaultMss());
        ck("16384 档下游缓冲 = 16344（16 KB 级）",
           VpnNetService.downstreamBufferForMtu(VpnNetService.tunMtu()) == 16344,
           "" + VpnNetService.downstreamBufferForMtu(VpnNetService.tunMtu()));
        ck("16384 档 clampMss 上限跟着抬",
           VpnNetService.clampMss(65535) == 16344,
           "" + VpnNetService.clampMss(65535));
        ck("非法档位 1501 被拒绝", !VpnNetService.applyMtuTier(1501), "");
        ck("被拒后档位不变", VpnNetService.tunMtu() == 16384,
           "" + VpnNetService.tunMtu());
        ck("1280 档 MSS = 1240", VpnNetService.applyMtuTier(1280)
           && VpnNetService.defaultMss() == 1240, "" + VpnNetService.defaultMss());
        // 复位：后面的用例/同进程其他测试都按默认档跑
        VpnNetService.applyMtuTier(1500);
        ck("复位回 1500", VpnNetService.tunMtu() == 1500
           && VpnNetService.defaultMss() == 1460, "" + VpnNetService.tunMtu());

        System.out.println("\n_MssClampTest: pass=" + pass + " fail=" + fail);
        if (fail > 0) System.exit(1);
    }
}
