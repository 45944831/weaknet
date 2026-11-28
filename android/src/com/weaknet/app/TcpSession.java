package com.weaknet.app;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/**
 * 一条 TCP 会话：tun 侧伪造 TCP 状态机 + 真实 Socket 转发，并注入弱网参数。
 * 说明：本实现不主动重传（丢包表现为"卡顿"而非真丢），因此不需要重传队列。
 */
public class TcpSession {
    private final VpnNetService vpn;
    final String clientIp;
    final int clientPort;
    final String remoteIp;
    final int remotePort;
    final String key;

    private long clientSeq;          // 期望收到的下一个客户端 seq
    private long mySeq;              // 我们下一个要发送的 seq
    private final int clientMss;

    private volatile boolean ready = false;    // 上游已连接且已发 SYN-ACK
    private volatile boolean closed = false;
    /** close() 只允许真正执行一次：它从下行循环 / cleanup / stopVpn / RST 多处
     *  并发进来，若两个线程都过了 closed 判断，connActive 会被减两次。 */
    private final java.util.concurrent.atomic.AtomicBoolean closeOnce =
            new java.util.concurrent.atomic.AtomicBoolean(false);
    private volatile long lastActive = System.currentTimeMillis();

    /**
     * 客户端最近一次通告的接收窗口（rwnd）。<=0 表示「不启用流控」。
     *
     * 旧实现完全不看它：下游按上游读多快就往 tun 写多快，客户端应用读得慢时
     * 这些包只能堆在 tun 写队列里 —— 表现是「内存涨、延迟涨、最后丢包」，
     * 而不是真实网络里那种「发送方被窗口挡住」。
     */
    private int clientWindow = 65535;
    /** 已发出但客户端还没确认的下行字节数（用于窗口判断） */
    private long unackedDown = 0;
    /** 两个方向是否已各自结束：TCP 允许半关闭，都结束了才真正拆会话 */
    private boolean upFin = false;
    private boolean downFin = false;

    private Socket socket;
    private OutputStream sout;
    private InputStream sin;

    /** IP 的字节形式：每条连接的 IP 固定，构造时算一次，
     *  免得每个下行包/每个 ACK 都 split + parseInt（IpUtil 的字符串重载） */
    private final byte[] clientIpB;
    private final byte[] remoteIpB;

    /** 端口对直接映射缓存的下标（见 VpnNetService.tcpFast）。
     *  关会话时按它清槽，避免持有已关闭会话的引用。 */
    private final int fastIdx;

    /**
     * 本会话专属的**单线程串行**执行器。
     *
     * 为什么不能用全局线程池：TCP 上行是严格有序的状态机 —— onClientPacket 里只要
     * seq 不等于期望值就判定为「乱序/重传」，只回一个重复 ACK 而不把数据交给上游。
     * 并发线程会让同一条连接的数据包乱序抵达，绝大多数包因此被误判丢弃，客户端
     * 收到一堆重复 ACK 触发快速重传，又再次乱序……实测上行会从 60MB/s 塌到 3~5KB/s。
     * 下行之所以一直正常，是因为它本来就是每条连接一个单线程顺序写。
     *
     * 空闲 30 秒回收线程：弱网测试可能同时挂着上百条连接，常驻线程会白白吃掉内存，
     * 而队列里的任务依然保留，下次有包进来会自动再拉起一个线程。
     */
    private final ThreadPoolExecutor serial;

    /**
     * 下行读缓冲池（缓冲复用）。
     *
     * 旧实现每块 `new byte[n]` + `arraycopy`：按 MSS 1460 算，1.4KB 就要一次分配、
     * 一次复制、一次跨线程投递。方案 §4.4(e) 指出池化的前提是「归还协议」——
     * 这里能安全做，是因为 `IpUtil.buildTcp()` 把 payload 复制进全新的报文数组
     * （由 _MtuAbTest 的「buildTcp 不持有 payload 引用」断言守住），投递 job
     * 一结束缓冲就不再被任何人引用，可以立即归还。池上限 4，避免高延迟档
     * 积压时把内存放大成第二条队列。
     */
    private final java.util.ArrayDeque<byte[]> bufPool =
            new java.util.ArrayDeque<byte[]>(4);

    private byte[] borrowBuf() {
        synchronized (bufPool) {
            byte[] b = bufPool.pollFirst();
            if (b != null) return b;
        }
        return new byte[clientMss];     // 随协商 MSS（进而随 TUN_MTU）派生
    }

    private void releaseBuf(byte[] b) {
        if (b == null) return;
        synchronized (bufPool) {
            if (bufPool.size() < 4) bufPool.addFirst(b);
        }
    }

    /** 每会话串行队列容量：512 个待执行任务，约几十 KB 引用。
     *  满了就丢（TCP 会重传），绝不无限排队 —— 无界队列是内存失控的经典入口。 */
    private static final int SERIAL_QUEUE_CAP = 512;
    /** 因队列满被丢弃的任务总数（全进程）：过载时的可见证据，进日志用 */
    static final java.util.concurrent.atomic.AtomicLong serialDropped =
            new java.util.concurrent.atomic.AtomicLong();

    /** 链路延迟队列：数据包按延迟到期后再投递，延迟与带宽解耦 */
    private final DelayLine delay;
    /** 同一方向的到期时间单调不减，保证抖动不会把包打乱 */
    private long nextUpDue = 0;
    private long nextDownDue = 0;

    TcpSession(VpnNetService vpn, String clientIp, int clientPort,
               String remoteIp, int remotePort, String key, long clientIsn, int mss) {
        this.vpn = vpn;
        this.clientIp = clientIp;
        this.clientPort = clientPort;
        this.remoteIp = remoteIp;
        this.remotePort = remotePort;
        this.key = key;
        this.clientSeq = (clientIsn + 1) & 0xFFFFFFFFL;
        // 上限必须跟着 tun 的 MTU 走：原来硬编码 1400，即使把 setMtu 调到 16KB
        // 也永远只发 1400 的段，MTU 提升等于白做（见 VpnNetService.TUN_MTU 注释）
        this.clientMss = VpnNetService.clampMss(mss);
        this.clientIpB = IpUtil.ipToBytes(clientIp);
        this.remoteIpB = IpUtil.ipToBytes(remoteIp);
        this.fastIdx = PortIdx.of(clientPort, remotePort);
        // 队列必须有界：无界队列在「上行远快于上游消费」时能把内存吃光，
        // 而且出问题时毫无痕迹。满了就丢任务（TCP 会重传），并计数以便观测。
        ThreadPoolExecutor e = new ThreadPoolExecutor(
                1, 1, 30L, TimeUnit.SECONDS,
                new LinkedBlockingQueue<Runnable>(SERIAL_QUEUE_CAP),
                new ThreadPoolExecutor.AbortPolicy());
        e.allowCoreThreadTimeOut(true);
        this.serial = e;
        this.delay = new DelayLine("tcp-delay", 1 << 20);   // 积压超过 1MB 就回压
        // 活跃连接计数：VPN 模式以前只加 connTotal 不减也不加 active，
        // 于是 /api/status 的 stats.active 恒 0、报告里的「当前活跃」失真。
        // 构造即活跃，close() 里成对递减（close 幂等，见 closed 守卫）。
        WeakNet.I.connActive.incrementAndGet();
    }

    /**
     * 这个包是否属于本会话（供端口对直接映射缓存校验）。
     * 端口对折叠进 16 位槽位后，同 dstPort 的新连接会撞上残留会话，
     * 所以 IP 和端口都必须核对得上才算数 —— 早期版本只核对 IP，
     * 结果旧会话把新 SYN 当重传吞掉，客户端永远等不到 SYN-ACK。
     */
    boolean sameFlow(byte[] pkt) {
        return FlowMatch.same(pkt, clientPort, remotePort, clientIpB, remoteIpB);
    }

    /**
     * 计算这个包应该在多少毫秒后投递。
     * 同一方向的到期时间强制单调不减：抖动是 ±jitter 的，放任的话后发的包可能
     * 先到期，投递顺序就乱了 —— 对 TCP 来说乱序等于重传，会被状态机直接丢掉。
     */
    private long dueDelay(boolean up) {
        long now = System.currentTimeMillis();
        long want = now + DelayLine.delayMs(up);
        if (up) {
            if (want < nextUpDue) want = nextUpDue;
            nextUpDue = want;
        } else {
            if (want < nextDownDue) want = nextDownDue;
            nextDownDue = want;
        }
        long d = want - now;
        return d < 0 ? 0 : d;
    }

    /** 提交一个需要按序执行的任务（建连、上行数据包、关闭）。已关闭时静默丢弃。 */
    void submit(Runnable r) {
        try {
            serial.execute(r);
        } catch (java.util.concurrent.RejectedExecutionException e) {
            // 队列满：丢任务（客户端会重传），但要留下可观测的痕迹，
            // 否则「偶尔卡一下」永远查不到原因
            long n = serialDropped.incrementAndGet();
            if (n == 1 || n % 200 == 0) {
                WeakNet.I.log("loss", "TCP 串行队列已满，累计丢弃 " + n + " 个任务（"
                        + remoteIp + ":" + remotePort + "）");
            }
        } catch (Exception e) {
            // 会话已关闭或线程池已停：丢掉即可，连接本来就要断了
        }
    }

    boolean isClosed() {
        return closed;
    }

    long lastActive() {
        return lastActive;
    }

    /** 是否已有一个 start() 在跑：SYN 重传会触发重试，不能让它并发建两条上游 */
    private final java.util.concurrent.atomic.AtomicBoolean starting =
            new java.util.concurrent.atomic.AtomicBoolean(false);
    /** 上次尝试建连的时刻（单调时钟）：给 SYN 重传的重试加个下限，别每来一个就重连一次 */
    private volatile long lastStartAt = 0;

    /** 建立上游连接并回复 SYN-ACK（此处注入连接 RTT、黑洞、拒连） */
    void start() {
        if (!starting.compareAndSet(false, true)) return;
        lastStartAt = android.os.SystemClock.elapsedRealtime();
        try {
            startInner();
        } finally {
            starting.set(false);
        }
    }

    private void startInner() {
        Profile p = WeakNet.I.profile;
        if (p.rejectPct > 0 && Math.random() * 100 < p.rejectPct) {
            sendRst();
            close();
            return;
        }
        if (!WeakNet.I.gateOpen || (p.timeoutPct > 0 && Math.random() * 100 < p.timeoutPct)) {
            WeakNet.I.log("timeout", "模拟黑洞：" + remoteIp + ":" + remotePort + " 无响应");
            vpn.scheduleRemove(key, this, 60000);
            return;
        }
        try {
            Socket s = new Socket();
            // 上游 TCP 关 Nagle（NetGuard tcp.c / Go 标准库 / gVisor 的共同默认）：
            // 不改峰值吞吐（写入都是包级 ≥ MSS），但消除小写入（尾段、握手、
            // 协议交接）撞上 Nagle+延迟 ACK 的 40~200ms 停顿，成本为零。
            // 缓冲区刻意不设：手动 setReceiveBufferSize/SendBufferSize 会关掉
            // Linux 的接收自调优（tcp_moderate_rcvbuf=1，Pixel 默认上限已 2~8MB）。
            s.setTcpNoDelay(true);
            vpn.protect(s);
            s.connect(new InetSocketAddress(remoteIp, remotePort), 15000);
            socket = s;
            sout = s.getOutputStream();
            sin = s.getInputStream();
        } catch (Exception e) {
            WeakNet.I.log("error", remoteIp + ":" + remotePort + " 连接失败");
            sendRst();
            close();
            return;
        }
        // 连接 RTT 延迟 + 抖动
        int rtt = p.latencyUpMs + p.latencyDownMs;
        int jit = p.jitterMs;
        long d = rtt + (jit > 0 ? (long) ((Math.random() * 2 - 1) * jit) : 0);
        if (d > 0) sleep(d);
        if (closed) {
            closeSocket();
            return;
        }
        long isn = (long) (Math.random() * 0xFFFFFFF) & 0xFFFFFFFFL;
        synchronized (this) {
            mySeq = (isn + 1) & 0xFFFFFFFFL;
            vpn.write(IpUtil.buildTcp(remoteIpB, remotePort, clientIpB, clientPort,
                    isn, clientSeq, IpUtil.SYN | IpUtil.ACK, null, 0, 65535, clientMss));
        }
        ready = true;
        WeakNet.I.log("connect", "TCP " + clientIp + ":" + clientPort + " → " + remoteIp + ":" + remotePort);
        startDownstream();
    }

    /** 处理来自 App（tun）方向的报文 */
    synchronized void onClientPacket(int flags, long seq, long ack, byte[] payload, int payloadLen, int window) {
        if (closed) return;
        lastActive = System.currentTimeMillis();

        if ((flags & IpUtil.RST) != 0) {
            close();
            return;
        }
        if ((flags & IpUtil.ACK) != 0) {
            // 用客户端的确认号与窗口调度下行：这是「发送方被接收方窗口挡住」的
            // 唯一信息来源，不看它就只能盲目往 tun 里灌。
            long diff = (mySeq - ack) & 0xFFFFFFFFL;
            if (diff <= (1L << 24)) {        // 超过 16MB 的差值只可能是异常包
                unackedDown = diff;
                if (window > 0) clientWindow = window;
                notifyAll();                 // 唤醒 waitWindow（持锁调用，合法）
            }
        }
        boolean syn = (flags & IpUtil.SYN) != 0;
        boolean ackFlag = (flags & IpUtil.ACK) != 0;
        if (syn && !ackFlag) {
            // SYN 重传。以前一律忽略：上一次被判成黑洞/拒连的会话会留在表里最多
            // 60s，同四元组的 SYN 全被它吞掉 —— 故障撤销后这条连接仍然连不上，
            // 看起来像「停止弱网也没恢复」。这里改成重新走一遍 start()，
            // 它会按当前参数重新判定（黑洞/拒连/正常），而不是沿用旧结论。
            if (!ready && !closed) {
                long now = android.os.SystemClock.elapsedRealtime();
                // 客户端 SYN 重传间隔约 1s/2s/4s，这里再加一道 500ms 下限：
                // 避免同一秒内的重复 SYN 触发多次真实 connect（每次最长 15s）
                if (now - lastStartAt > 500) {
                    submit(new Runnable() {
                        public void run() {
                            start();
                        }
                    });
                }
            }
            return;
        }
        if (payloadLen > 0) {
            if (seq != clientSeq) {
                sendAck(clientSeq); // 乱序/重传：回 ACK，等待重传补齐
                return;
            }
            if (!ready || sout == null) {
                return;             // 上游未就绪，不 ACK，等待客户端重传
            }
            try {
                Profile p = WeakNet.I.profile;
                if (p.lossPct > 0 && Math.random() * 100 < p.lossPct) {
                    sleep((long) (p.stallMs * (0.5 + Math.random())));
                }
                if (p.disconnectPct > 0 && Math.random() * 100 < p.disconnectPct) {
                    WeakNet.I.log("disconnect", "模拟断连：" + remoteIp + ":" + remotePort);
                    sendRst();
                    close();
                    return;
                }
                final byte[] data = payload;
                final int len = payloadLen;
                // 用 offer 而不是 post：本方法持有会话锁，既不能内联（job 里会
                // awaitGate，断网窗口最长等 120s，还会阻塞写 socket —— 跑在会话锁里
                // 等于把整条连接和 TUN 分发线程一起冻住），也不能阻塞等容量
                // （同样是在持锁等待）。返回 false = 延迟队列已满，按真实丢包处理：
                // 不推进 clientSeq、不回 ACK，让客户端自己重传。
                if (!delay.offer(DelayLine.DIR_UP, dueDelay(true), new Runnable() {
                    public void run() {
                        // 断网窗口：在真正写上游的地方等门闸。只查建连时那一次的话，
                        // 窗口内建立的连接会一路照常收发，「网络切换」等于没发生。
                        if (!WeakNet.I.awaitGate(WeakNet.GATE_WAIT_MAX_MS)) {
                            close();
                            return;
                        }
                        try {
                            sout.write(data, 0, len);
                            sout.flush();
                        } catch (Exception e) {
                            close();
                        }
                    }
                }, len)) {
                    WeakNet.I.log("loss", "上行延迟队列已满，丢弃 " + len + " 字节");
                    return;
                }
                WeakNet.I.upBucket.consume(payloadLen);
                WeakNet.I.bytesUp.addAndGet(payloadLen);
            } catch (Exception e) {
                close();
                return;
            }
            clientSeq = (clientSeq + payloadLen) & 0xFFFFFFFFL;
            // ACK 要走完整的一来一回：上行延迟 + 下行延迟，客户端的 RTT 才对得上
            sendAck(clientSeq);
        }
        if ((flags & IpUtil.FIN) != 0) {
            clientSeq = (clientSeq + 1) & 0xFFFFFFFFL;
            sendAck(clientSeq);
            // FIN 必须排进延迟队列、等前面的数据都投递完再关写端：
            // 否则「发完数据立刻 FIN」的短连接（curl / nc / HTTP 短连接都这样）
            // 会在数据还在队列里躺着的时候就被 shutdownOutput，对端一个字节都收不到。
            final Socket s = socket;
            final long dFin = dueDelay(true);
            Runnable finUp = new Runnable() {
                public void run() {
                    try {
                        if (s != null) s.shutdownOutput();
                    } catch (Exception e) {
                        // ignore
                    }
                }
            };
            // 与上行数据同理：持锁期间只入队。队列满时退到串行执行器上补一次，
            // 保证上游一定能收到 FIN（否则服务端会一直等这个半关闭）。
            if (!delay.offer(DelayLine.DIR_UP, dFin, finUp, 0)) {
                submit(finUp);
            }
            // 客户端只是半关闭：上行结束了，下行还可能继续（HTTP/1.0 请求、
            // `nc -N`、curl 的早期版本都这么发）。这里不能关整条会话。
            markUpFin();
        }
    }

    /** 上游 → App：注入下行限速/卡顿后写回 tun */
    private void startDownstream() {
        // 读循环放到服务的共享线程池：每条连接的读循环本来就是「一个任务跑到 EOF」，
        // 放共享池不破坏每连接顺序，还能省掉每连接一次的线程创建（短连接密集时差距明显）。
        // 空闲 keep-alive 连接仍会占住一个池线程（阻塞在读上），这是阻塞式 IO 的固有代价，
        // 由 cleanup() 的 300s 空闲回收兜底。
        //
        // 池与队列都满时（并发连接超限）：这条连接的下行永远没机会跑，留着只会
        // 让客户端挂死等数据。直接 RST + close，让对端立刻知道连不上。
        vpn.runOnPool(new Runnable() {
            public void run() {
                downstreamLoop();
            }
        }, "tcp-down", new Runnable() {
            public void run() {
                abort();
            }
        });
    }

    private void downstreamLoop() {
        // 读缓冲从会话池里借，大小随协商 MSS（进而随 TUN_MTU）走：MTU 1500 时
        // 为 1460（原为固定 1400），提到 16384 时自动变成 16344 —— 这是
        // 「MTU 提升能真正生效」的前提之一。每块不再 new + arraycopy。
        try {
            while (!closed) {
                final byte[] slot = borrowBuf();
                int n = sin.read(slot);
                if (n < 0) { releaseBuf(slot); break; }
                lastActive = System.currentTimeMillis();
                Profile p = WeakNet.I.profile;
                if (p.lossPct > 0 && Math.random() * 100 < p.lossPct) {
                    sleep((long) (p.stallMs * (0.5 + Math.random())));
                }
                if (p.disconnectPct > 0 && Math.random() * 100 < p.disconnectPct) {
                    releaseBuf(slot);
                    WeakNet.I.log("disconnect", "模拟断连：" + remoteIp + ":" + remotePort);
                    sendRst();
                    close();
                    return;
                }
                final int len = n;
                final long d = dueDelay(false);
                final long seqAt;
                synchronized (TcpSession.this) {
                    if (closed) { releaseBuf(slot); break; }
                    seqAt = mySeq;
                    mySeq = (mySeq + n) & 0xFFFFFFFFL;
                }
                // 流控必须在**下游读线程**里等，不能放进 DelayLine 的 job：
                // DelayLine 是每会话单 worker，等窗口要等的 ACK 却也要经过同一 worker
                // 投递 —— 有链路延迟时形成自锁（worker 卡在 waitWindow，ACK 进不了队列，
                // 窗口永远等不到），表现为「一开延迟就首字节 0、整条连接假死」。
                // 下游读线程阻塞只会暂停读上游，ACK 由 TUN 分发线程处理，不会死锁。
                waitWindow(len);
                if (closed) { releaseBuf(slot); break; }

                // 延迟到期后再写回 tun；队列积压时 post 会阻塞，
                // 于是这里停止读上游，背压自然传导到服务器，不会把内存撑爆。
                // buildTcp 会把 slot 的前 len 字节复制进新报文，job 结束即可归还。
                delay.post(DelayLine.DIR_DOWN, d, new Runnable() {
                    public void run() {
                        try {
                            // 断网窗口：下行同样在投递处等门闸（与上行同一套语义）
                            if (!WeakNet.I.awaitGate(WeakNet.GATE_WAIT_MAX_MS)) {
                                close();
                                return;
                            }
                            synchronized (TcpSession.this) {
                                if (closed) return;
                                vpn.write(IpUtil.buildTcp(remoteIpB, remotePort,
                                        clientIpB, clientPort, seqAt, clientSeq,
                                        IpUtil.PSH | IpUtil.ACK,
                                        slot, len, 65535, null));
                            }
                        } finally {
                            releaseBuf(slot);
                        }
                    }
                }, len);
            }
        } catch (Exception e) {
            // ignore
        }
        // FIN 同样要排在下行数据之后：上游关了之后，队列里可能还有数据没送达。
        // 直接发 FIN 再 close() 会把这些数据连同队列一起清掉，客户端收不全。
        delay.post(DelayLine.DIR_DOWN, dueDelay(false), new Runnable() {
            public void run() {
                synchronized (TcpSession.this) {
                    if (!closed) {
                        vpn.write(IpUtil.buildTcp(remoteIpB, remotePort, clientIpB, clientPort,
                                mySeq, clientSeq, IpUtil.FIN | IpUtil.ACK, null, 0, 65535, null));
                        mySeq = (mySeq + 1) & 0xFFFFFFFFL;
                    }
                }
                // 服务端先半关闭时不能顺手把整条会话拆掉：客户端可能还在上传
                // （HTTP 上传、双向协议都这样）。只有两个方向都结束了才真正关闭。
                markDownFin();
            }
        }, 0);
    }

    /**
     * 下行窗口控制：未确认字节占满客户端窗口时等 ACK。
     *
     * 必须在**会话锁外**等待：ACK 是在 onClientPacket（持锁）里更新的，
     * 持锁等 ACK 就是自锁。wait() 会释放监视器，所以这里可以安全地等。
     *
     * 同时必须在**下游读线程**而不是 DelayLine 的 worker 里等：
     *  ACK 的投递也走同一条 DelayLine，worker 里等 ACK = 等自己，必死锁。
     * 超时则关掉流控并记一条 —— 客户端长时间不 ACK 时宁可放任，
     * 也不要让整条连接永久停在这里。
     */
    private void waitWindow(int len) {
        long end = System.currentTimeMillis() + 30000;
        synchronized (this) {
            while (!closed && clientWindow > 0 && unackedDown + len > clientWindow) {
                long left = end - System.currentTimeMillis();
                if (left <= 0) {
                    clientWindow = 0;
                    WeakNet.I.log("loss", "下行流控等待超时，已关闭窗口控制："
                            + remoteIp + ":" + remotePort);
                    return;
                }
                try {
                    wait(Math.min(50, left));
                } catch (InterruptedException e) {
                    return;
                }
            }
        }
    }

    private void markUpFin() {
        synchronized (this) {
            upFin = true;
        }
        maybeClose();
    }

    private void markDownFin() {
        synchronized (this) {
            downFin = true;
        }
        maybeClose();
    }

    /** 两个方向都收到 FIN 才拆会话；单方向结束属于正常半关闭 */
    private void maybeClose() {
        boolean both;
        synchronized (this) {
            both = upFin && downFin;
        }
        if (both) close();
    }

    /**
     * 回 ACK。必须把 ack 号以参数传进来：ACK 是延迟投递的，等真正发出去的时候
     * clientSeq 早就被后面的包推进过了，那时再读就会确认还没收到的数据。
     */
    private void sendAck(final long ackSeq) {
        long d = DelayLine.delayMs(true) + DelayLine.delayMs(false);
        // offer：调用方可能持有会话锁（onClientPacket），入队即可，别在锁里跑 job。
        // ACK 因队列满被丢是可接受的：上行队列满时本来就没在确认新数据。
        delay.offer(DelayLine.DIR_UP, d, new Runnable() {
            public void run() {
                synchronized (TcpSession.this) {
                    if (closed) return;
                    vpn.write(IpUtil.buildTcp(remoteIpB, remotePort, clientIpB, clientPort,
                            mySeq, ackSeq, IpUtil.ACK, null, 0, 65535, null));
                }
            }
        }, 0);
    }

    private void sendRst() {
        synchronized (this) {
            if (closed) return;
            vpn.write(IpUtil.buildTcp(remoteIpB, remotePort, clientIpB, clientPort,
                    mySeq, clientSeq, IpUtil.RST | IpUtil.ACK, null, 0, 0, null));
        }
    }

    void abort() {
        sendRst();
        close();
    }

    void close() {
        if (!closeOnce.compareAndSet(false, true)) return;
        closed = true;
        WeakNet.I.connActive.decrementAndGet();
        vpn.clearTcpFast(fastIdx, this);   // 清掉端口对快路径槽位（身份校验，不会误清后来者）
        closeSocket();
        // 停掉串行队列：丢弃排队的上行包，并唤醒正在等令牌/延迟的线程
        try {
            serial.shutdownNow();
        } catch (Exception e) {
            // ignore
        }
        try {
            delay.close();
        } catch (Exception e) {
            // ignore
        }
    }

    private void closeSocket() {
        try {
            if (socket != null) socket.close();
        } catch (Exception e) {
            // ignore
        }
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            // ignore
        }
    }
}
