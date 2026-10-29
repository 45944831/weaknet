package com.weaknet.app;

import java.util.Comparator;
import java.util.PriorityQueue;

/**
 * 延迟投递队列：给数据包加上「链路延迟」，同时不阻塞发送方。
 *
 * 为什么不能直接 sleep 再发：那样同一时刻只有一个包在途，吞吐会退化成
 * 「1 个包 / RTT」—— 700ms 延迟下只有 2KB/s。而真实弱网是靠拥塞窗口让多个包
 * 同时在途的，延迟和带宽本来就是两回事，必须解耦。
 *
 * 投递顺序与入队顺序保持一致：提交方保证同一方向的到期时间单调不减，
 * 这样即使带抖动也不会把包打乱（乱序对 TCP 是灾难，会被当成重传丢掉）。
 *
 * 背压：待投递字节超过 maxPending 时，post() 会阻塞提交方，压力沿链路往回传
 * —— 上行表现为不再回 ACK、下行表现为不再读 socket，让对端自己降速。
 * 没有这个保护的话，服务器全速发 + 大延迟 = 队列无限堆积直到 OOM。
 */
public class DelayLine {
    static class Item {
        final long due;
        final long seq;        // 入队序号：到期时间相同时按它排，保证先进先出
        final Runnable job;
        final int bytes;

        Item(long due, long seq, Runnable job, int bytes) {
            this.due = due;
            this.seq = seq;
            this.job = job;
            this.bytes = bytes;
        }
    }

    private final Object lock = new Object();
    private final PriorityQueue<Item> q = new PriorityQueue<Item>(16, new Comparator<Item>() {
        public int compare(Item a, Item b) {
            if (a.due != b.due) return a.due < b.due ? -1 : 1;
            return a.seq < b.seq ? -1 : (a.seq > b.seq ? 1 : 0);
        }
    });
    private long seqGen = 0;
    private final String name;
    private final int maxPending;
    private Thread worker;          // 空闲久了会退出，下次 post 再拉起来
    private long lastBusy = System.currentTimeMillis();
    private volatile boolean closed = false;
    private volatile int pendingBytes = 0;
    /** 已出队、job 还在锁外执行的数量。>0 时禁止内联快路径——
     *  否则后发的包可能插到前面那个包的 job 之前写完（tun 写入顺序即乱序）。 */
    private int inFlight = 0;
    /** 已入队项的最大到期时间：新项的到期时间不得早于它，保证抖动不把包打乱。
     *  （队列空且无在途 job 时重置——那时的到期时间都已过去，不该再压制新包） */
    private long lastDue = 0;

    /** 空闲这么久就撤掉线程：手机上同时挂着的连接可能有上百条，
     *  每条都常驻一个投递线程太浪费 —— 反正闲着也是闲着，真有包进来再拉起来。 */
    private static final long IDLE_EXIT_MS = 30000;

    DelayLine(String name, int maxPending) {
        this.name = name;
        this.maxPending = maxPending;
    }

    /** 延迟 delayMs 后执行 job。delayMs<=0 且无在途数据时直接同步执行，省掉一次线程切换。 */
    void post(long delayMs, Runnable job, int bytes) {
        if (delayMs <= 0) {
            // 不限延迟时直接同步跑：队列为空说明没有更早的包在等，不会打乱顺序。
            // 少了「入队 + 唤醒投递线程」这一来一回，直连档的吞吐能差出 30%。
            boolean inline = false;
            synchronized (lock) {
                // inFlight>0 = 已出队的包 job 还在锁外跑，此时内联会让顺序颠倒，必须入队
                inline = !closed && q.isEmpty() && inFlight == 0;
                if (inline) lastDue = 0;
            }
            if (inline) {
                // 必须出了锁再跑：job 里会去拿 TcpSession 的锁，而提交方是拿着
                // TcpSession 的锁来 post 的 —— 在锁里跑就是 AB-BA 死锁，整条连接卡死。
                try {
                    job.run();
                } catch (Exception e) {
                    // ignore
                }
                return;
            }
        }
        synchronized (lock) {
            if (closed) return;
            while (maxPending > 0 && pendingBytes > maxPending) {
                try {
                    lock.wait(50);
                } catch (InterruptedException e) {
                    return;
                }
                if (closed) return;
            }
            enqueueLocked(delayMs, job, bytes);
        }
    }

    /**
     * UDP 用：积压满了就丢弃而不是阻塞。UDP 没有拥塞控制，对端不会因为你不收就降速，
     * 死等只会把队列撑爆；真实网络里路由器队列满了同样是丢包，行为一致。
     *
     * 容量判断与入队必须在同一个 synchronized 内完成。旧实现先在锁内查、出锁后再调
     * post()，两个发送线程可以同时通过检查，后到的那个会掉进 post() 的 wait 循环被
     * 卡住 —— 与「满了就丢、绝不阻塞」的约定正好相反。
     */
    boolean tryPost(long delayMs, Runnable job, int bytes) {
        boolean inline;
        synchronized (lock) {
            if (closed) return false;
            if (maxPending > 0 && pendingBytes + bytes > maxPending) return false;
            inline = delayMs <= 0 && q.isEmpty() && inFlight == 0;
            if (inline) {
                lastDue = 0;
            } else {
                enqueueLocked(delayMs, job, bytes);
            }
        }
        if (inline) {
            // 与 post() 同理：job 必须在锁外跑，否则会和会话锁形成 AB-BA 死锁
            try {
                job.run();
            } catch (Exception e) {
                // ignore
            }
        }
        return true;
    }

    /** 调用方必须持有 lock */
    private void enqueueLocked(long delayMs, Runnable job, int bytes) {
        long due = System.currentTimeMillis() + Math.max(0, delayMs);
        // 单调保序：新包不得早于已在队/在途的包到期（抖动是 ±jitter 的，
        // 放任的话后发的包可能先到期，投递顺序乱了 TCP 会当成重传全丢掉）
        if (q.isEmpty() && inFlight == 0) lastDue = 0;   // 基准已过去，重置
        if (due < lastDue) due = lastDue;
        lastDue = due;
        q.add(new Item(due, seqGen++, job, bytes));
        pendingBytes += bytes;
        if (worker == null || !worker.isAlive()) {
            worker = new Thread(new Runnable() {
                public void run() {
                    loop();
                }
            }, name);
            worker.setDaemon(true);
            worker.start();
        }
        lock.notifyAll();
    }

    int pendingBytes() {
        return pendingBytes;
    }

    private void loop() {
        while (!closed) {
            Item it = null;
            synchronized (lock) {
                if (q.isEmpty()) {
                    if (System.currentTimeMillis() - lastBusy > IDLE_EXIT_MS) {
                        worker = null;      // 交还线程，下次 post 会重新拉起
                        return;
                    }
                    try {
                        lock.wait(200);     // 空队列：保留轮询，承担空闲退线程的职责
                    } catch (InterruptedException e) {
                        return;
                    }
                    continue;
                }
                it = q.peek();
                long w = it.due - System.currentTimeMillis();
                if (w > 0) {
                    // 精确等到期：post/close 都会 notifyAll，不需要 200ms 轮询兜底
                    // （原来每 200ms 醒一次，100 条延迟连接就是 500 次/秒空转，纯耗电）
                    try {
                        lock.wait(w);
                    } catch (InterruptedException e) {
                        return;
                    }
                    continue;
                }
                q.poll();
                pendingBytes -= it.bytes;
                inFlight++;
                lastBusy = System.currentTimeMillis();
                lock.notifyAll();
            }
            if (it != null) {
                try {
                    it.job.run();
                } catch (Exception e) {
                    // ignore
                }
                synchronized (lock) {
                    inFlight--;
                    lock.notifyAll();
                }
            }
        }
    }

    void close() {
        closed = true;
        synchronized (lock) {
            q.clear();
            pendingBytes = 0;
            lock.notifyAll();
        }
        try {
            worker.interrupt();
        } catch (Exception e) {
            // ignore
        }
    }

    /** 按当前场景算单向延迟：基准 + 抖动（可正可负）
     *
     * 抖动幅度不能超过基准：base + (-jit) 会算出负延迟，物理上不存在「比 0 还快」。
     * 旧实现直接夹到 0，于是 jitter > base 时会出现一批零延迟包（比直连还快），
     * 均值也被抬高。把幅度夹到 base 后分布落在 [0, 2·base]，均值仍等于 base。
     */
    static long delayMs(boolean up) {
        Profile p = WeakNet.I.profile;
        int base = up ? p.latencyUpMs : p.latencyDownMs;
        int jit = p.jitterMs;
        if (base <= 0) {
            // 没有基础延迟时，抖动只能表现为 0~jit 的正向离散
            return jit > 0 ? (long) (Math.random() * jit) : 0;
        }
        if (jit > base) jit = base;
        long d = base + (jit > 0 ? (long) ((Math.random() * 2 - 1) * jit) : 0);
        return d < 0 ? 0 : d;
    }
}
