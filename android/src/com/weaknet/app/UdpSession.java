package com.weaknet.app;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
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
                    ds = s;
                    remoteAddr = InetAddress.getByAddress(remoteIpB);
                    startReceive();
                }
                WeakNet.I.upBucket.consume(payloadLen);
                WeakNet.I.bytesUp.addAndGet(payloadLen);
                final InetAddress addr = remoteAddr;
                final byte[] data = payload;
                final int len = payloadLen;
                delay.tryPost(DelayLine.delayMs(true), new Runnable() {
                    public void run() {
                        try {
                            ds.send(new DatagramPacket(data, len, addr, remotePort));
                        } catch (Exception e) {
                            // ignore
                        }
                    }
                }, len);
            }
        } catch (Exception e) {
            // ignore
        }
    }

    private void startReceive() {
        final DatagramSocket s = ds;
        Thread t = new Thread(new Runnable() {
            public void run() {
                byte[] buf = new byte[2048];
                while (!closed.get()) {
                    try {
                        DatagramPacket dp = new DatagramPacket(buf, buf.length);
                        s.receive(dp);
                        lastActive = System.currentTimeMillis();
                        byte[] data = new byte[dp.getLength()];
                        System.arraycopy(dp.getData(), dp.getOffset(), data, 0, dp.getLength());
                        delay.tryPost(DelayLine.delayMs(false), new Runnable() {
                            public void run() {
                                vpn.write(IpUtil.buildUdp(remoteIpB, remotePort,
                                        clientIpB, clientPort, data, data.length));
                            }
                        }, data.length);
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

    void close() {
        if (closed.compareAndSet(false, true)) {
            vpn.clearUdpFast(fastIdx, this);
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
