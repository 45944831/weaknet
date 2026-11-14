package com.weaknet.app;

/**
 * TUN 写队列语义回归（纯 JDK 可跑）。
 *
 * 方案 §4.4(a) 要求「stop / onProfileChange 时 outQ.clear() 语义要与现有
 * 「立即放行积压」一致，需单独回归」。原先 outQ 是 VpnNetService 的私有字段，
 * 依赖 Android API 无法在纯 JDK 里测；抽成 TunWriteQueue 后，有界 / FIFO /
 * 清空三条约束终于有了断言。
 */
public class _TunWriteQueueTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + "  <- " + extra); }
    }

    public static void main(String[] a) throws InterruptedException {
        TunWriteQueue q = new TunWriteQueue(3);

        ck("容量与构造参数一致", q.capacity() == 3, "cap=" + q.capacity());
        ck("初始为空", q.size() == 0 && q.poll() == null, "");

        ck("入队 3 块", q.offer(new byte[]{1}, 10)
                && q.offer(new byte[]{2}, 10)
                && q.offer(new byte[]{3}, 10), "size=" + q.size());
        ck("队满", q.size() == 3, "size=" + q.size());
        ck("有界：队满时 offer 超时返回 false（背压不丢、也不无限吃内存）",
           !q.offer(new byte[]{4}, 5), "size=" + q.size());

        ck("FIFO 顺序 1,2,3",
           q.poll()[0] == 1 && q.poll()[0] == 2 && q.poll()[0] == 3, "");
        ck("排空后 poll 超时返回 null", q.poll(5) == null, "");

        // 停止接管 / 切换弱网场景：清空在途包，绝不重放到新 tun
        q.offer(new byte[]{7}, 10);
        q.offer(new byte[]{8}, 10);
        q.clear();
        ck("clear 后不重放积压", q.size() == 0 && q.poll() == null, "");
        ck("clear 后队列仍可用", q.offer(new byte[]{9}, 10)
                && q.poll()[0] == 9, "");

        // 生产容量：1024 × ~1.5KB ≈ 1.5MB 上限
        TunWriteQueue prod = new TunWriteQueue(1024);
        ck("生产容量 1024", prod.capacity() == 1024, "cap=" + prod.capacity());

        boolean rejected = false;
        try {
            new TunWriteQueue(0);
        } catch (IllegalArgumentException e) {
            rejected = true;
        }
        ck("容量 <=0 被拒绝", rejected, "");

        System.out.println("\n_TunWriteQueueTest: pass=" + pass + " fail=" + fail);
        if (fail > 0) System.exit(1);
    }
}
