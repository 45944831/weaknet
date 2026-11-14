package com.weaknet.app;

/** 浮窗帧率统计（P2-3）的纯 JDK 回归：不需要设备、不需要 Android API。 */
public class _FrameStatsTest {

    private static int failed = 0;
    private static int passed = 0;

    private static void ck(String name, boolean ok, String detail) {
        if (ok) {
            passed++;
            System.out.println("  PASS  " + name);
        } else {
            failed++;
            System.out.println("  FAIL  " + name + (detail == null ? "" : (" :: " + detail)));
        }
    }

    public static void main(String[] args) {
        // --- 间隔统计：4 帧，间隔 500 / 500 / 900，单帧耗时 20 / 22 / 25 / 30 ---
        FrameStats s = new FrameStats();
        s.record(1000L, 20L);
        s.record(1500L, 22L);
        s.record(2000L, 25L);
        s.record(2900L, 30L);
        FrameStats.Snapshot snap = s.snapshot();
        ck("累计 4 帧", snap.frames == 4, "frames=" + snap.frames);
        ck("间隔最小 = 500ms", snap.gapMinMs == 500, "min=" + snap.gapMinMs);
        ck("间隔中位 = 500ms", snap.gapMedianMs == 500, "median=" + snap.gapMedianMs);
        ck("间隔最大 = 900ms", snap.gapMaxMs == 900, "max=" + snap.gapMaxMs);
        ck("间隔均值 = 633ms", snap.gapAvgMs == 633, "avg=" + snap.gapAvgMs);

        // --- 单帧耗时超预算计数（20/22/25/30 都超过 16ms 一帧预算）---
        ck("超一帧(>16ms) 计 4 次", snap.overFrame == 4,
                "overFrame=" + snap.overFrame);
        ck("超 100ms 计 0 次", snap.overStall == 0, "overStall=" + snap.overStall);

        // --- 耗时够小的不该被算成掉帧 ---
        FrameStats s0 = new FrameStats();
        s0.record(0L, 5L);
        s0.record(500L, 9L);
        ck("5/9ms 不算超一帧", s0.snapshot().overFrame == 0,
                "overFrame=" + s0.snapshot().overFrame);

        // --- 明显卡顿 ---
        FrameStats s2 = new FrameStats();
        s2.record(0L, 10L);
        s2.record(100L, 250L);
        FrameStats.Snapshot k2 = s2.snapshot();
        ck("超 100ms 计 1 次", k2.overStall == 1, "overStall=" + k2.overStall);
        ck("超一帧计 1 次", k2.overFrame == 1, "overFrame=" + k2.overFrame);

        // --- 窗口淘汰 ---
        FrameStats s3 = new FrameStats(3);
        for (int i = 0; i < 10; i++) s3.record(i * 100L, 1L);
        FrameStats.Snapshot k3 = s3.snapshot();
        ck("窗口内样本 = 3", k3.windowed == 3, "windowed=" + k3.windowed);
        ck("累计帧数仍为 10", k3.frames == 10, "frames=" + k3.frames);
        ck("丢弃计数 = 7", k3.dropped == 7, "dropped=" + k3.dropped);

        // --- 折算频率 ---
        FrameStats s4 = new FrameStats();
        for (int i = 0; i < 5; i++) s4.record(i * 500L, 2L);
        FrameStats.Snapshot k4 = s4.snapshot();
        ck("约 2 次/秒", k4.fps > 1.9 && k4.fps < 2.1, "fps=" + k4.fps);

        // --- 摘要可读且含关键字段 ---
        String line = snap.summary();
        ck("摘要含帧数", line.contains("帧 4"), line);
        ck("摘要含 p95", line.contains("p95"), line);

        System.out.println("_FrameStatsTest: pass=" + passed + " fail=" + failed);
        if (failed > 0) System.exit(1);
    }
}
