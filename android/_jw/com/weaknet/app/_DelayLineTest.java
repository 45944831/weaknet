package com.weaknet.app;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * DelayLine.tryPost 原子性回归（纯 JDK 可跑）。
 *
 * 回归背景：旧版 tryPost 先在锁内检查 pendingBytes，出锁后再调 post()，
 * 而 post() 在超限时会 wait(50) 阻塞。并发提交时两个线程都能通过检查，
 * 后到者被卡住 —— UDP「满了就丢、绝不阻塞」的约定被打破。
 */
public class _DelayLineTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + "  <- " + extra); }
    }

    static Runnable noop() {
        return new Runnable() {
            public void run() {
            }
        };
    }

    public static void main(String[] a) throws Exception {
        // 1) 容量上限：maxPending=1000，每包 200 字节，只收得下 5 个
        DelayLine dl = new DelayLine("t-cap", 1000);
        int ok = 0, dropped = 0;
        long worst = 0;
        for (int i = 0; i < 10; i++) {
            long t0 = System.nanoTime();
            boolean r = dl.tryPost(5000, noop(), 200);
            long ms = (System.nanoTime() - t0) / 1000000;
            if (ms > worst) worst = ms;
            if (r) ok++; else dropped++;
        }
        ck("容量内全部入队", ok == 5, "ok=" + ok);
        ck("超限立即丢弃", dropped == 5, "dropped=" + dropped);
        ck("pendingBytes 不超过上限", dl.pendingBytes() <= 1000, "pending=" + dl.pendingBytes());
        ck("单次调用不阻塞（<50ms）", worst < 50, "worst=" + worst + "ms");
        dl.close();

        // 2) 并发压测：4 线程 × 200 次，任何一次都不得被容量等待卡住
        final DelayLine cd = new DelayLine("t-conc", 8192);
        final int threads = 4, per = 200, size = 128;
        final AtomicInteger accepted = new AtomicInteger();
        final AtomicInteger rejected = new AtomicInteger();
        final AtomicLong worstMs = new AtomicLong();
        final CountDownLatch start = new CountDownLatch(1);
        final CountDownLatch done = new CountDownLatch(threads);
        for (int t = 0; t < threads; t++) {
            Thread th = new Thread(new Runnable() {
                public void run() {
                    try {
                        start.await();
                    } catch (InterruptedException e) {
                        return;
                    }
                    for (int i = 0; i < per; i++) {
                        long t0 = System.nanoTime();
                        boolean r = cd.tryPost(5000, noop(), size);
                        long ms = (System.nanoTime() - t0) / 1000000;
                        long prev;
                        while (ms > (prev = worstMs.get()) && !worstMs.compareAndSet(prev, ms)) {
                            // CAS 重试
                        }
                        if (r) accepted.incrementAndGet(); else rejected.incrementAndGet();
                    }
                    done.countDown();
                }
            }, "try-" + t);
            th.setDaemon(true);
            th.start();
        }
        start.countDown();
        done.await();
        int total = threads * per;
        ck("提交次数守恒", accepted.get() + rejected.get() == total,
           "acc=" + accepted.get() + " rej=" + rejected.get());
        ck("确实发生了丢弃", rejected.get() > 0, "rej=" + rejected.get());
        ck("并发下单次调用不阻塞（<50ms）", worstMs.get() < 50, "worst=" + worstMs.get() + "ms");
        ck("并发下不超上限", cd.pendingBytes() <= 8192, "pending=" + cd.pendingBytes());
        cd.close();

        // 3) close 之后一律拒绝
        DelayLine cl = new DelayLine("t-closed", 1024);
        cl.close();
        ck("closed 后 tryPost 返回 false", !cl.tryPost(0, noop(), 10), "should reject");

        // 4) 零延迟且队列空时内联执行（提交返回前就已经跑完）
        DelayLine il = new DelayLine("t-inline", 1024);
        final AtomicInteger ran = new AtomicInteger();
        boolean posted = il.tryPost(0, new Runnable() {
            public void run() {
                ran.incrementAndGet();
            }
        }, 10);
        ck("零延迟内联执行", posted && ran.get() == 1, "posted=" + posted + " ran=" + ran.get());
        il.close();

        // 5) 延迟投递确实延后执行
        DelayLine d2 = new DelayLine("t-delay", 1024);
        final AtomicInteger late = new AtomicInteger();
        d2.tryPost(300, new Runnable() {
            public void run() {
                late.incrementAndGet();
            }
        }, 10);
        ck("延迟项未立即执行", late.get() == 0, "ran=" + late.get());
        Thread.sleep(800);
        ck("延迟到期后执行", late.get() == 1, "ran=" + late.get());
        d2.close();

        System.out.println();
        System.out.println(pass + " 项通过 / " + fail + " 项失败");
        if (fail > 0) System.exit(1);
    }
}
