package com.weaknet.app;

import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * TUN 下行写队列（纯 JDK，不碰任何 Android API）。
 *
 * 抽成独立类不是为了多一层抽象，而是为了让三条硬约束能被纯 JDK 单测守住
 * （见 _jw/.../_TunWriteQueueTest.java）：
 *
 * 1. **有界**：满了 offer 会超时返回 false，压力沿链路回传，不会无限吃内存。
 * 2. **FIFO**：出队顺序 = 入队顺序，与旧 writeLock 的锁序等价。
 * 3. **可清空**：stopVpn / onProfileChange 时 clear() 后不得重放积压包。
 *
 * 生产容量 1024 × ~1.5KB ≈ 1.5MB 上限。
 */
public class TunWriteQueue {
    private final ArrayBlockingQueue<byte[]> q;

    public TunWriteQueue(int capacity) {
        if (capacity <= 0) throw new IllegalArgumentException("capacity must be > 0");
        this.q = new ArrayBlockingQueue<byte[]>(capacity);
    }

    /** 入队；队列满时最多等 timeoutMs，返回 false 表示背压生效（调用方会重试） */
    public boolean offer(byte[] packet, long timeoutMs) throws InterruptedException {
        return q.offer(packet, timeoutMs, TimeUnit.MILLISECONDS);
    }

    /** 出队；超时返回 null（让写线程有机会检查停止标志） */
    public byte[] poll(long timeoutMs) throws InterruptedException {
        return q.poll(timeoutMs, TimeUnit.MILLISECONDS);
    }

    /** 非阻塞出队，供写线程退出前排空 */
    public byte[] poll() {
        return q.poll();
    }

    /** 停止接管 / 切换场景：丢弃在途包，不再写回 tun */
    public void clear() {
        q.clear();
    }

    public int size() {
        return q.size();
    }

    public int capacity() {
        return q.size() + q.remainingCapacity();
    }
}
