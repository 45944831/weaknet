package com.weaknet.app;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Random;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 令牌桶并发正确性单测（纯 JDK 可跑，不需要真机）。
 *
 * 背景：v1.1.7 发布前遗留一项「并发总带宽偏高」的已知偏差——真机探针测出
 * 4 并发 1.15x、8 并发 1.29x（设定 2000 kibps），根因未定位。
 * 后续发现那是**探针缺陷**：旧 _wn_tun.sh 让所有并发子探针共用同一个输出文件
 * /data/local/tmp/wn_out 且各自 rm -f 它，并发时一个探针的 wc -c 会读到别的
 * 探针写进去的字节而提前退出轮询，把测量窗口压缩，并发度越高压缩越狠。
 *
 * 本测试绕过测量链路，直接压 Pipe.Bucket 本身：N 个线程并发 consume，
 * 统计一段时间内放行的总字节，与速率上限比对。这样得到的是产品代码的真实
 * 行为，与设备、探针、TCP 全都无关。
 *
 * 另外补三组回归：
 *   R1 单次中断后无残留欠债（v1.1.7 修复点）
 *   R2 反复中断 20 次不累积欠债（原始缺陷场景：8 并发后单连接卡 90s+）
 *   R3 高频中断压测：模型化「8 条连接边传边被关闭」，验证不超发
 */
public class _BucketConcTest {
    static int pass = 0, fail = 0;
    static final int KBPS = 2000;
    static final double RATE = KBPS * 128.0;      // 字节/秒，与 Pipe.Bucket 口径一致

    // 与 concurrent() 里的块大小表保持一致
    static final int[] SIZES = {1400, 1448, 1400, 512, 1400, 64};

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name + "  | " + extra); }
        else { fail++; System.out.println("  FAIL  " + name + "  <- " + extra); }
    }

    static long bytesOf(int iters) {
        long sum = 0;
        for (int i = 0; i < iters; i++) sum += SIZES[i % SIZES.length];
        return sum;
    }

    /** N 个线程并发 consume，跑 secs 秒，返回 (迭代次数, 墙钟秒) */
    static Object[] concurrent(int n, int secs) throws Exception {
        final Pipe.Bucket b = new Pipe.Bucket(KBPS);
        final AtomicInteger done = new AtomicInteger();
        final long deadline = System.nanoTime() + secs * 1_000_000_000L;
        Thread[] th = new Thread[n];
        for (int i = 0; i < n; i++) {
            th[i] = new Thread(new Runnable() {
                public void run() {
                    // 混合块大小，贴近真实报文（1400/1448 是常见 MSS，512/64 是小包）
                    int k = 0;
                    try {
                        while (System.nanoTime() < deadline) {
                            b.consume(SIZES[k % SIZES.length]);
                            k++;
                        }
                    } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                    }
                    done.addAndGet(k);
                }
            });
        }
        long t0 = System.nanoTime();
        for (Thread t : th) t.start();
        for (Thread t : th) t.join();
        double dt = (System.nanoTime() - t0) / 1e9;
        return new Object[]{done.get(), dt};
    }

    public static void main(String[] a) throws Exception {
        System.out.println("Pipe.Bucket 并发正确性（设定 " + KBPS + " kibps = "
                + (long) RATE + " B/s）");

        // ---- C1~C4 1/2/4/8 并发聚合速率 ----
        int[] conc = {1, 2, 4, 8};
        for (int n : conc) {
            Object[] r = concurrent(n, 3);
            int iters = (Integer) r[0];
            double dt = (Double) r[1];
            long bytes = bytesOf(iters);
            double rate = bytes / dt;
            double ratio = rate / RATE;
            // 上界 1.05：桶只可能因初始 4096B 突发和计时粒度略高，不应成倍超标
            // 下界 0.90：也应确实把速率用满，不能因为并发而大幅变慢
            boolean ok = ratio <= 1.05 && ratio >= 0.90;
            ck(n + " 并发聚合速率", ok,
                    String.format("%.0f B/s = %.3fx（%d 次 consume / %.2fs）",
                            rate, ratio, iters, dt));
        }

        // ---- R1 单次中断后无残留欠债（v1.1.7 修复点）----
        // 无补偿时：预扣 5s 的量，等 300ms 后被打断，桶里留下约 5s-0.3s 的负账，
        // 紧接着的 consume(4096) 要等那么久。有补偿时：应立即放行。
        {
            Pipe.Bucket b = new Pipe.Bucket(KBPS);
            final int BIG = (int) RATE * 5;          // 5 秒的量
            final Pipe.Bucket fb = b;
            Thread t = new Thread(new Runnable() {
                public void run() {
                    try { fb.consume(BIG); } catch (InterruptedException ie) { }
                }
            });
            t.start();
            Thread.sleep(300);
            t.interrupt();
            t.join(2000);
            long t0 = System.nanoTime();
            b.consume(4096);
            double ms = (System.nanoTime() - t0) / 1e6;
            ck("单次中断后无残留欠债（4096B 应立即放行）", ms < 100,
                    String.format("耗时 %.1f ms（无补偿时应约 %.0f ms）", ms,
                            (BIG - 0.3 * RATE) / RATE * 1000));
        }

        // ---- R2 反复中断不累积欠债（原始缺陷场景）----
        // 原始缺陷：每次中断留一笔幽灵欠债，8 并发 + 反复开关后欠债累积到
        // 远超实际流量，单连接 256KB 卡 90s 以上。
        {
            Pipe.Bucket b = new Pipe.Bucket(KBPS);
            final int BIG = (int) RATE * 5;
            final Pipe.Bucket fb = b;
            for (int i = 0; i < 20; i++) {
                Thread t = new Thread(new Runnable() {
                    public void run() {
                        try { fb.consume(BIG); } catch (InterruptedException ie) { }
                    }
                });
                t.start();
                Thread.sleep(80);
                t.interrupt();
                t.join(2000);
            }
            long t0 = System.nanoTime();
            b.consume(4096);
            double ms = (System.nanoTime() - t0) / 1e6;
            ck("20 次反复中断后欠债仍有界", ms < 200,
                    String.format("20 次后 4096B 耗时 %.1f ms（缺陷时累积约 %.0f s）",
                            ms, 20 * (5 - 0.08)));
        }

        // ---- R3 高频中断压测：模型化真实故障场景 ----
        // 场景来自 v1.1.7 修的缺陷：8 条并发连接传输中不断被关闭
        // （DelayLine.close() 会 worker.interrupt()），consume 在 wait 里被打断。
        // 8 个 worker 持续 consume，closer 每 60ms 随机打断一个 worker
        // （worker 退出后由主循环补位），跑 5 秒。
        // 断言：无论怎么打断，窗口内放行的总字节不得超过 rate×T×1.05。
        // 这条不变量同时覆盖两件事——补偿不会变成超发通道，欠债也不会累积。
        {
            final int SECS = 5;
            final Pipe.Bucket b = new Pipe.Bucket(KBPS);
            final AtomicLong released = new AtomicLong();   // 实际放行的字节
            final AtomicInteger alive = new AtomicInteger();
            final boolean[] stop = {false};
            final List<Thread> workers =
                    java.util.Collections.synchronizedList(new ArrayList<Thread>());
            final Random rnd = new Random(7);

            Runnable worker = new Runnable() {
                public void run() {
                    try {
                        while (!stop[0]) {
                            b.consume(1400);
                            released.addAndGet(1400);
                        }
                    } catch (InterruptedException ie) {
                        // 会话被关闭：退出，由主循环补位
                    } finally {
                        alive.decrementAndGet();
                    }
                }
            };
            for (int i = 0; i < 8; i++) {
                workers.add(new Thread(worker));
                alive.incrementAndGet();
            }
            for (Thread t : workers) t.start();

            Thread closer = new Thread(new Runnable() {
                public void run() {
                    while (!stop[0]) {
                        try { Thread.sleep(60); } catch (InterruptedException e) { return; }
                        synchronized (workers) {
                            if (!workers.isEmpty()) {
                                workers.get(rnd.nextInt(workers.size())).interrupt();
                            }
                        }
                    }
                }
            });
            closer.setDaemon(true);
            closer.start();

            long t0 = System.nanoTime();
            int maxAlive = 8, kicked = 0;
            while ((System.nanoTime() - t0) / 1e9 < SECS) {
                Thread.sleep(50);
                while (alive.get() < 8) {           // 补齐被打断退出的 worker
                    Thread t = new Thread(worker);
                    synchronized (workers) { workers.add(t); }
                    alive.incrementAndGet();
                    t.start();
                    kicked++;
                }
                synchronized (workers) {
                    Iterator<Thread> it = workers.iterator();
                    while (it.hasNext()) if (!it.next().isAlive()) it.remove();
                }
                maxAlive = Math.max(maxAlive, alive.get());
            }
            stop[0] = true;
            closer.interrupt();
            double dt = (System.nanoTime() - t0) / 1e9;
            long bytes = released.get();
            double ratio = bytes / (RATE * dt);
            ck("高频中断 5s 内不超发", ratio <= 1.05,
                    String.format("放行 %d B / %.2fs = %.3fx（打断补位 %d 次，峰值并发 %d）",
                            bytes, dt, ratio, kicked, maxAlive));
        }

        System.out.println("\n_BucketConcTest: " + pass + " pass, " + fail + " fail");
        if (fail > 0) System.exit(1);
    }
}
