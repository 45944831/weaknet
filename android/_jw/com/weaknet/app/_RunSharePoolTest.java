package com.weaknet.app;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/**
 * 共享服务线程池：必须**有界 + 满了拒绝**（纯 JDK 可跑）。
 *
 * 背景：原来用 {@code Executors.newFixedThreadPool(4)}，它内部是无界
 * {@code LinkedBlockingQueue}。慢客户端一多，任务就在队列里无限堆积 ——
 * accept 循环看着没堵，实际把堆吃光。这一组断言钉住三条：
 *   1. 线程数固定；
 *   2. 队列容量 = SERVE_QUEUE（不是 Integer.MAX_VALUE）；
 *   3. 队满时抛 RejectedExecutionException，由调用方关连接。
 */
public class _RunSharePoolTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + "  <- " + extra); }
    }

    public static void main(String[] a) throws Exception {
        final ThreadPoolExecutor p = ShareNet.newServePool();
        ck("线程数固定为 SERVE_THREADS",
           p.getCorePoolSize() == ShareNet.SERVE_THREADS
                   && p.getMaximumPoolSize() == ShareNet.SERVE_THREADS,
           p.getCorePoolSize() + "/" + p.getMaximumPoolSize());
        ck("队列有界（容量 = SERVE_QUEUE，而不是无界）",
           p.getQueue().remainingCapacity() == ShareNet.SERVE_QUEUE,
           "remaining=" + p.getQueue().remainingCapacity());
        ck("拒绝策略是 AbortPolicy（满了抛异常，由调用方关连接）",
           p.getRejectedExecutionHandler() instanceof ThreadPoolExecutor.AbortPolicy,
           String.valueOf(p.getRejectedExecutionHandler()));

        // 占住全部线程，让后续任务只能进队列
        final CountDownLatch gate = new CountDownLatch(1);
        final CountDownLatch started = new CountDownLatch(ShareNet.SERVE_THREADS);
        Runnable block = new Runnable() {
            public void run() {
                started.countDown();
                try {
                    gate.await();
                } catch (InterruptedException ignore) {
                    // 退出
                }
            }
        };
        for (int i = 0; i < ShareNet.SERVE_THREADS; i++) p.execute(block);
        ck("线程已全部启动", started.await(3, TimeUnit.SECONDS),
           "started=" + (ShareNet.SERVE_THREADS - started.getCount()));

        Runnable noop = new Runnable() {
            public void run() {
                // 什么也不做
            }
        };
        for (int i = 0; i < ShareNet.SERVE_QUEUE; i++) p.execute(noop);
        ck("队列可以排满到 SERVE_QUEUE",
           p.getQueue().remainingCapacity() == 0,
           "remaining=" + p.getQueue().remainingCapacity());

        boolean rejected = false;
        try {
            p.execute(noop);
        } catch (RejectedExecutionException e) {
            rejected = true;
        }
        ck("队满后再提交必须被拒绝", rejected, "没有抛 RejectedExecutionException");

        gate.countDown();
        p.shutdownNow();

        System.out.println("  -- " + pass + " PASS / " + fail + " FAIL");
        if (fail > 0) System.exit(1);
    }
}
