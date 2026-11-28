package com.weaknet.app;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/** 一条 UDP 会话（DNS 等）：转发到真实网络并回写 tun */
public class UdpSession {
    private final VpnNetService vpn;
    final String clientIp;
    final int clientPort;
    final String remoteIp;
    final int remotePort;
    final String key;

    /** IP 字节形式 + 解析后的 InetAddress：一条流里它们是固定的，
     *  原来每个包都 getByName(remoteIp)（字符串解析）是白做工 */
    private final byte[] clientIpB;
    private final byte[] remoteIpB;
    private volatile InetAddress remoteAddr;

    private volatile DatagramSocket ds;
    private final AtomicBoolean closed = new AtomicBoolean(false);
    private volatile long lastActive = System.currentTimeMillis();
    /** UDP 也要吃链路延迟，否则 DNS 查询、QUIC 这类流量完全测不到延迟 */
    private final DelayLine delay;
    /** 建连/发送的锁：读循环会把同一个流的包派发给线程池，两个线程可能同时进 send()，
     *  早年会各自 new 一个 DatagramSocket（一个被赋值、另一个泄漏且永不关闭） */
    private final Object sendLock = new Object();
    private final int fastIdx;

    /**
     * 本会话专属的串行发送队列。
     *
     * 旧实现把每个 UDP 包都丢给服务的共享 cached 线程池：DNS/QUIC 这类高包率流量
     * 会让线程池反复建销线程，而且同一会话的包序无保证。改成每会话单线程后，
     * 线程数按会话算（空闲 30s 自动回收），包序与到达顺序一致。
     */
    private final ThreadPoolExecutor serial;

    UdpSession(VpnNetService vpn, String clientIp, int clientPort,
               String remoteIp, int remotePort, String key) {
        this.vpn = vpn;
        this.clientIp = clientIp;
        this.clientPort = clientPort;
        this.remoteIp = remoteIp;
        this.remotePort = remotePort;
        this.key = key;
        this.clientIpB = IpUtil.ipToBytes(clientIp);
        this.remoteIpB = IpUtil.ipToBytes(remoteIp);
        this.delay = new DelayLine("udp-delay", 256 << 10);
        this.fastIdx = PortIdx.of(clientPort, remotePort);
        // 有界队列：无界队列在 DNS/QUIC 突发时会把内存吃光
        ThreadPoolExecutor e = new ThreadPoolExecutor(1, 1, 30L, TimeUnit.SECONDS,
                new LinkedBlockingQueue<Runnable>(UDP_QUEUE_CAP),
                new ThreadPoolExecutor.AbortPolicy());
        e.allowCoreThreadTimeOut(true);
        this.serial = e;
        // 与 TcpSession 同口径：构造即活跃，close() 里成对递减。
        // 竞争失败（putIfAbsent 已有同 key 会话）的那条由调用方立刻 close()，
        // 计数不会泄漏。
        WeakNet.I.connActive.incrementAndGet();
    }

    /** 每会话串行队列容量：满则丢（UDP 本来就是可丢的），不无限排队 */
    private static final int UDP_QUEUE_CAP = 512;

    /** 提交一个发送任务（同一会话内严格串行）；会话已关闭时静默丢弃 */
    void submit(Runnable r) {
        try {
            serial.execute(r);
        } catch (Exception e) {
            // 已关闭或池已停：丢弃
        }
    }

    boolean isClosed() {
        return closed.get();
    }

    long lastActive() {
        return lastActive;
    }

    boolean sameFlow(byte[] pkt) {
        return FlowMatch.same(pkt, clientPort, remotePort, clientIpB, remoteIpB);
    }

    void send(byte[] payload, int payloadLen) {
        lastActive = System.currentTimeMillis();
        Profile p = WeakNet.I.profile;
        if (p.rejectPct > 0 && Math.random() * 100 < p.rejectPct) return;
        if (!WeakNet.I.gateOpen) return;
        if (remotePort == 53 && p.dnsFailPct > 0 && Math.random() * 100 < p.dnsFailPct) {
            WeakNet.I.log("dns", "模拟 DNS 故障：" + remoteIp);
            return;
        }
        if (p.timeoutPct > 0 && Math.random() * 100 < p.timeoutPct) {
            WeakNet.I.log("timeout", "模拟黑洞：UDP " + remoteIp + ":" + remotePort);
            return;
        }
        try {
            if (p.lossPct > 0 && Math.random() * 100 < p.lossPct) {
                Thread.sleep((long) (p.stallMs * (0.5 + Math.random())));
            }
            synchronized (sendLock) {
                if (closed.get()) return;
                if (ds == null) {
                    DatagramSocket s = new DatagramSocket();
                    vpn.protect(s);
                    s.setSoTimeout(30000);
                    // UDP 没有接收自调优：512KB 收缓冲对齐 hev-socks5-core 的做法
                    //（hev-socks5-misc.c:31），弱网高丢包下突发到达时不白丢内核队列
                    try {
                        s.setReceiveBufferSize(512 * 1024);
                    } catch (Exception ignore) {
                        // 个别 ROM 限制缓冲上限：默认值也能工作
                    }
                    ds = s;
                    remoteAddr = InetAddress.getByAddress(remoteIpB);
                    startReceive();
                }
                WeakNet.I.upBucket.consume(payloadLen);
                WeakNet.I.bytesUp.addAndGet(payloadLen);
                final InetAddress addr = remoteAddr;
                final byte[] data = payload;
                final int len = payloadLen;
                if (!delay.tryPost(DelayLine.DIR_UP, DelayLine.delayMs(true), new Runnable() {
                    public void run() {
                        // 断网窗口：UDP 直接丢，与真实断网一致（没有重传，丢了就是丢了）。
                        // 只在 send() 入口查一次不够 —— 入队到真正发出之间可能正好进窗口。
                        if (!WeakNet.I.gateOpen) return;
                        try {
                            ds.send(new DatagramPacket(data, len, addr, remotePort));
                        } catch (Exception e) {
                            // ignore
                        }
                    }
                }, len)) noteDrop();
            }
        } catch (Exception e) {
            // ignore
        }
    }

    private void startReceive() {
        final DatagramSocket s = ds;
        Thread t = new Thread(new Runnable() {
            public void run() {
                // 收缓冲必须按 tun MTU 派生。固定 2048 时，MTU 9000/16384 下
                // 更大的 UDP 报文会被 DatagramSocket.receive 静默截断，再以「新长度」
                // 写回 tun —— 应用收到的内容与服务器发的不是同一份（校验必然失败）。
                // 多留 1 字节当「溢出探针」：正常报文不可能超过 MTU-28，
                // 读满这个长度就说明确实被截断了。
                final int bufSize = Math.max(2048, VpnNetService.tunMtu() - 28) + 1;
                byte[] buf = new byte[bufSize];
                while (!closed.get()) {
                    try {
                        DatagramPacket dp = new DatagramPacket(buf, buf.length);
                        s.receive(dp);
                        lastActive = System.currentTimeMillis();
                        if (dp.getLength() >= bufSize) {
                            // 明确拒绝而不是把半截数据当完整报文送回去
                            noteTruncated();
                            continue;
                        }
                        byte[] data = new byte[dp.getLength()];
                        System.arraycopy(dp.getData(), dp.getOffset(), data, 0, dp.getLength());
                        if (!delay.tryPost(DelayLine.DIR_DOWN, DelayLine.delayMs(false), new Runnable() {
                            public void run() {
                                // 断网窗口：下行也丢。旧实现只在上行查门闸，
                                // 断网期间上游的回包照样被写回 tun —— 「断网」对
                                // DNS/QUIC 这类流量等于没发生。
                                if (!WeakNet.I.gateOpen) return;
                                vpn.write(IpUtil.buildUdp(remoteIpB, remotePort,
                                        clientIpB, clientPort, data, data.length));
                            }
                        }, data.length)) noteDrop();
                    } catch (Exception e) {
                        if (System.currentTimeMillis() - lastActive > 60000) break;
                    }
                }
                close();
            }
        }, "udp-recv");
        t.setDaemon(true);
        t.start();
    }

    /** 队列满丢包：计总并做指数退避提示（第 1/2/4/8… 次各记一条），避免刷屏 */
    private void noteDrop() {
        long n = WeakNet.I.udpDropped.incrementAndGet();
        if ((n & (n - 1)) == 0) {
            WeakNet.I.log("loss", "UDP 队列已满，累计丢弃 " + n + " 个包");
        }
    }

    /**
     * 超出收缓冲的报文：明确丢弃并计数。
     *
     * 以前是「静默截断后照常写回 tun」—— 应用拿到一个长度变短、内容被腰斩的
     * 数据报，看起来像服务器发了错数据，实际是我们在中间切了一刀。
     */
    private void noteTruncated() {
        long n = WeakNet.I.udpTruncated.incrementAndGet();
        if ((n & (n - 1)) == 0) {
            WeakNet.I.log("loss", "UDP 报文超过 MTU 收缓冲，已整包丢弃（累计 " + n + "）");
        }
    }

    void close() {
        if (closed.compareAndSet(false, true)) {
            WeakNet.I.connActive.decrementAndGet();
            // 立刻从会话表和快表摘掉：等到 120s 定时清理才摘的话，
            // 这段时间里同四元组的新首包会被派给已关闭的会话，任务静默丢弃。
            vpn.removeUdp(this);
            vpn.clearUdpFast(fastIdx, this);
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
            try {
                if (ds != null) ds.close();
            } catch (Exception e) {
                // ignore
            }
        }
    }
}
