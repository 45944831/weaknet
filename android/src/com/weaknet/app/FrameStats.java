package com.weaknet.app;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * 浮窗刷新帧率统计（P2-3）。
 *
 * <p>为什么需要它：浮窗靠 {@code Handler.postDelayed(..., 500)} 轮询刷新，
 * 500ms 的粒度会把「一帧卡了多久」完全盖住 —— 用户只看到「数字不动」，
 * 分不清是弱网本身慢、还是 UI 线程被阻塞。JankStats 需要 API 31+ 且只能在
 * 真机上生效，这里做的是**不依赖 Android API 的纯逻辑**：记录每次刷新之间
 * 的真实间隔与本次刷新自身耗时，给出中位数 / p95 / 最大值与「超过一帧
 * (16ms) / 超过 100ms」的计数。
 *
 * <p>刻意做成纯 JDK（不 import 任何 android.*），这样能在
 * {@code android/_jw/} 的纯 JDK 单测里验证，不必连真机。
 */
public final class FrameStats {

    /** 默认窗口：500ms 一次刷新 × 120 次 ≈ 60 秒。 */
    public static final int DEFAULT_WINDOW = 120;

    /** 一帧的预算（60fps）。超过它算「掉帧」。 */
    public static final long FRAME_BUDGET_MS = 16;

    /** 明显卡顿的阈值。 */
    public static final long STALL_MS = 100;

    private final int window;
    private final ArrayDeque<Long> ats = new ArrayDeque<Long>();

    private long count = 0;          // 累计帧数
    private long dropped = 0;        // 因超出窗口被丢弃的样本数
    private long lastAt = -1;
    private long gapSum = 0, gapCount = 0, gapMax = 0, gapMin = Long.MAX_VALUE;
    private long costSum = 0, costMax = 0;
    private long overFrame = 0, overStall = 0;

    public FrameStats() {
        this(DEFAULT_WINDOW);
    }

    public FrameStats(int window) {
        this.window = Math.max(1, window);
    }

    /**
     * 记一帧。
     *
     * @param atMs   本帧开始时刻（毫秒，单调递增即可）
     * @param costMs 本帧自身耗时（毫秒）
     */
    public synchronized void record(long atMs, long costMs) {
        if (lastAt >= 0) {
            long gap = atMs - lastAt;
            if (gap >= 0) {
                gapSum += gap;
                gapCount++;
                if (gap > gapMax) gapMax = gap;
                if (gap < gapMin) gapMin = gap;
            }
        }
        lastAt = atMs;
        count++;

        if (costMs < 0) costMs = 0;
        costSum += costMs;
        if (costMs > costMax) costMax = costMs;
        if (costMs > FRAME_BUDGET_MS) overFrame++;
        if (costMs > STALL_MS) overStall++;

        ats.addLast(Long.valueOf(atMs));
        while (ats.size() > window) {
            ats.removeFirst();
            dropped++;
        }
    }

    /** 快照。返回的对象不可变，可安全跨线程读。 */
    public synchronized Snapshot snapshot() {
        List<Long> gaps = new ArrayList<Long>();
        if (ats.size() >= 2) {
            Long prev = null;
            for (Long t : ats) {
                if (prev != null) gaps.add(Long.valueOf(t.longValue() - prev.longValue()));
                prev = t;
            }
        }
        Collections.sort(gaps);
        long avgGap = gapCount > 0 ? gapSum / gapCount : 0;
        long avgCost = count > 0 ? costSum / count : 0;
        double fps = avgGap > 0 ? 1000.0 / (double) avgGap : 0.0;
        return new Snapshot(
                count, dropped, ats.size(),
                gaps.isEmpty() ? 0 : gaps.get(0).longValue(),
                gaps.isEmpty() ? 0 : gaps.get(gaps.size() / 2).longValue(),
                gaps.isEmpty() ? 0 : gaps.get(p95Index(gaps.size())).longValue(),
                gapMax, avgGap,
                avgCost, costMax, overFrame, overStall, fps);
    }

    private static int p95Index(int n) {
        int i = (int) Math.ceil(n * 0.95) - 1;
        if (i < 0) i = 0;
        if (i >= n) i = n - 1;
        return i;
    }

    /** 不可变快照。 */
    public static final class Snapshot {
        public final long frames;          // 累计帧数
        public final long dropped;        // 超出窗口丢弃的样本
        public final int windowed;         // 窗口内样本数（间隔数 = windowed - 1）
        public final long gapMinMs;
        public final long gapMedianMs;
        public final long gapP95Ms;
        public final long gapMaxMs;
        public final long gapAvgMs;
        public final long costAvgMs;
        public final long costMaxMs;
        public final long overFrame;       // 单帧耗时 > 16ms 的次数
        public final long overStall;       // 单帧耗时 > 100ms 的次数
        public final double fps;           // 按平均间隔折算的刷新频率

        Snapshot(long frames, long dropped, int windowed,
                 long gapMinMs, long gapMedianMs, long gapP95Ms, long gapMaxMs,
                 long gapAvgMs, long costAvgMs, long costMaxMs,
                 long overFrame, long overStall, double fps) {
            this.frames = frames;
            this.dropped = dropped;
            this.windowed = windowed;
            this.gapMinMs = gapMinMs;
            this.gapMedianMs = gapMedianMs;
            this.gapP95Ms = gapP95Ms;
            this.gapMaxMs = gapMaxMs;
            this.gapAvgMs = gapAvgMs;
            this.costAvgMs = costAvgMs;
            this.costMaxMs = costMaxMs;
            this.overFrame = overFrame;
            this.overStall = overStall;
            this.fps = fps;
        }

        /** 一行摘要，给日志用。 */
        public String summary() {
            return String.format(
                    "帧 %d（窗口 %d，丢弃 %d）｜间隔 中位 %dms / p95 %dms / 最大 %dms"
                            + "｜单帧耗时 均 %dms / 最大 %dms｜超一帧 %d 次 / 超 %dms %d 次｜约 %.1f 次/秒",
                    frames, windowed, dropped, gapMedianMs, gapP95Ms, gapMaxMs,
                    costAvgMs, costMaxMs, overFrame, STALL_MS, overStall, fps);
        }
    }
}
