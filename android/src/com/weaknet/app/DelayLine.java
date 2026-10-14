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

    /** 空闲这么久就撤掉线程：手机上同时挂着的连接可能有上百条，
     *  每条都常驻一个投递线程太浪费 —— 反正闲着也是闲着，真有包进来再拉起来。 */
    private static final long IDLE_EXIT_MS = 30000;

    DelayLine(String name, int maxPending) {
        this.name = name;
        this.maxPending = maxPending;
    }

    /** 延迟 delayMs 后执行 job。delayMs<=0 且队列为空时直接同步执行，省掉一次线程切换。 */
    void post(long delayMs, Runnable job, int bytes) {
        if (delayMs <= 0) {
            // 不限延迟时直接同步跑：队列为空说明没有更早的包在等，不会打乱顺序。
            // 少了「入队 + 唤醒投递线程」这一来一回，直连档的吞吐能差出 30%。
            boolean inline = false;
            synchronized (lock) {
                inline = q.isEmpty();
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
        long due = System.currentTimeMillis() + Math.max(0, delayMs);
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
    }

    /**
     * UDP 用：积压满了就丢弃而不是阻塞。UDP 没有拥塞控制，对端不会因为你不收就降速，
     * 死等只会把队列撑爆；真实网络里路由器队列满了同样是丢包，行为一致。
     */
    boolean tryPost(long delayMs, Runnable job, int bytes) {
        synchronized (lock) {
            if (closed || pendingBytes > maxPending) return false;
        }
        post(delayMs, job, bytes);
        return true;
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
                        lock.wait(200);
                    } catch (InterruptedException e) {
                        return;
                    }
                    continue;
                }
                it = q.peek();
                long w = it.due - System.currentTimeMillis();
                if (w > 0) {
                    try {
                        lock.wait(Math.min(w, 200));
                    } catch (InterruptedException e) {
                        return;
                    }
                    continue;
                }
                q.poll();
                pendingBytes -= it.bytes;
                lastBusy = System.currentTimeMillis();
                lock.notifyAll();
            }
            if (it != null) {
                try {
                    it.job.run();
                } catch (Exception e) {
                    // ignore
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

    /** 按当前场景算单向延迟：基准 + 抖动（可正可负） */
    static long delayMs(boolean up) {
        Profile p = WeakNet.I.profile;
        int base = up ? p.latencyUpMs : p.latencyDownMs;
        int jit = p.jitterMs;
        if (base <= 0 && jit <= 0) return 0;
        long d = base + (jit > 0 ? (long) ((Math.random() * 2 - 1) * jit) : 0);
        return d < 0 ? 0 : d;
    }
}
