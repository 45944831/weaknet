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
    private volatile long lastActive = System.currentTimeMillis();

    private Socket socket;
    private OutputStream sout;
    private InputStream sin;

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
        this.clientMss = Math.max(536, Math.min(mss, 1400));
        ThreadPoolExecutor e = new ThreadPoolExecutor(
                1, 1, 30L, TimeUnit.SECONDS, new LinkedBlockingQueue<Runnable>());
        e.allowCoreThreadTimeOut(true);
        this.serial = e;
        this.delay = new DelayLine("tcp-delay", 1 << 20);   // 积压超过 1MB 就回压
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

    /** 建立上游连接并回复 SYN-ACK（此处注入连接 RTT、黑洞、拒连） */
    void start() {
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
            vpn.write(IpUtil.buildTcp(remoteIp, remotePort, clientIp, clientPort,
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
        boolean syn = (flags & IpUtil.SYN) != 0;
        boolean ackFlag = (flags & IpUtil.ACK) != 0;
        if (syn && !ackFlag) {
            return;   // SYN 重传，忽略
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
                WeakNet.I.upBucket.consume(payloadLen);
                WeakNet.I.bytesUp.addAndGet(payloadLen);
                final byte[] data = payload;
                final int len = payloadLen;
                delay.post(dueDelay(true), new Runnable() {
                    public void run() {
                        try {
                            sout.write(data, 0, len);
                            sout.flush();
                        } catch (Exception e) {
                            close();
                        }
                    }
                }, len);
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
            delay.post(dueDelay(true), new Runnable() {
                public void run() {
                    try {
                        if (s != null) s.shutdownOutput();
                    } catch (Exception e) {
                        // ignore
                    }
                }
            }, 0);
        }
    }

    /** 上游 → App：注入下行限速/卡顿后写回 tun */
    private void startDownstream() {
        Thread t = new Thread(new Runnable() {
            public void run() {
                byte[] buf = new byte[1400];
                try {
                    while (!closed) {
                        int n = sin.read(buf);
                        if (n < 0) break;
                        lastActive = System.currentTimeMillis();
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
                        final byte[] data = new byte[n];
                        System.arraycopy(buf, 0, data, 0, n);
                        final long d = dueDelay(false);
                        final long seqAt;
                        synchronized (TcpSession.this) {
                            if (closed) break;
                            seqAt = mySeq;
                            mySeq = (mySeq + n) & 0xFFFFFFFFL;
                        }
                        // 延迟到期后再写回 tun；队列积压时 post 会阻塞，
                        // 于是这里停止读上游，背压自然传导到服务器，不会把内存撑爆
                        delay.post(d, new Runnable() {
                            public void run() {
                                synchronized (TcpSession.this) {
                                    if (closed) return;
                                    vpn.write(IpUtil.buildTcp(remoteIp, remotePort, clientIp, clientPort,
                                            seqAt, clientSeq, IpUtil.PSH | IpUtil.ACK,
                                            data, data.length, 65535, null));
                                }
                            }
                        }, n);
                    }
                } catch (Exception e) {
                    // ignore
                }
                // FIN 同样要排在下行数据之后：上游关了之后，队列里可能还有数据没送达。
                // 直接发 FIN 再 close() 会把这些数据连同队列一起清掉，客户端收不全。
                delay.post(dueDelay(false), new Runnable() {
                    public void run() {
                        synchronized (TcpSession.this) {
                            if (!closed) {
                                vpn.write(IpUtil.buildTcp(remoteIp, remotePort, clientIp, clientPort,
                                        mySeq, clientSeq, IpUtil.FIN | IpUtil.ACK, null, 0, 65535, null));
                                mySeq = (mySeq + 1) & 0xFFFFFFFFL;
                            }
                        }
                        close();
                    }
                }, 0);
            }
        }, "tcp-down");
        t.setDaemon(true);
        t.start();
    }

    /**
     * 回 ACK。必须把 ack 号以参数传进来：ACK 是延迟投递的，等真正发出去的时候
     * clientSeq 早就被后面的包推进过了，那时再读就会确认还没收到的数据。
     */
    private void sendAck(final long ackSeq) {
        long d = DelayLine.delayMs(true) + DelayLine.delayMs(false);
        delay.post(d, new Runnable() {
            public void run() {
                synchronized (TcpSession.this) {
                    if (closed) return;
                    vpn.write(IpUtil.buildTcp(remoteIp, remotePort, clientIp, clientPort,
                            mySeq, ackSeq, IpUtil.ACK, null, 0, 65535, null));
                }
            }
        }, 0);
    }

    private void sendRst() {
        synchronized (this) {
            if (closed) return;
            vpn.write(IpUtil.buildTcp(remoteIp, remotePort, clientIp, clientPort,
                    mySeq, clientSeq, IpUtil.RST | IpUtil.ACK, null, 0, 0, null));
        }
    }

    void abort() {
        sendRst();
        close();
    }

    void close() {
        if (closed) return;
        closed = true;
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
