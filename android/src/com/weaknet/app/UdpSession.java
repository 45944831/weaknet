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

    private volatile DatagramSocket ds;
    private final AtomicBoolean closed = new AtomicBoolean(false);
    private volatile long lastActive = System.currentTimeMillis();

    UdpSession(VpnNetService vpn, String clientIp, int clientPort,
               String remoteIp, int remotePort, String key) {
        this.vpn = vpn;
        this.clientIp = clientIp;
        this.clientPort = clientPort;
        this.remoteIp = remoteIp;
        this.remotePort = remotePort;
        this.key = key;
    }

    boolean isClosed() {
        return closed.get();
    }

    long lastActive() {
        return lastActive;
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
            if (ds == null) {
                DatagramSocket s = new DatagramSocket();
                vpn.protect(s);
                s.setSoTimeout(30000);
                ds = s;
                startReceive();
            }
            WeakNet.I.upBucket.consume(payloadLen);
            WeakNet.I.bytesUp.addAndGet(payloadLen);
            ds.send(new DatagramPacket(payload, payloadLen,
                    InetAddress.getByName(remoteIp), remotePort));
        } catch (Exception e) {
            // ignore
        }
    }

    private void startReceive() {
        Thread t = new Thread(new Runnable() {
            public void run() {
                byte[] buf = new byte[2048];
                while (!closed.get()) {
                    try {
                        DatagramPacket dp = new DatagramPacket(buf, buf.length);
                        ds.receive(dp);
                        lastActive = System.currentTimeMillis();
                        byte[] data = new byte[dp.getLength()];
                        System.arraycopy(dp.getData(), dp.getOffset(), data, 0, dp.getLength());
                        vpn.write(IpUtil.buildUdp(remoteIp, remotePort,
                                clientIp, clientPort, data, data.length));
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
            try {
                if (ds != null) ds.close();
            } catch (Exception e) {
                // ignore
            }
        }
    }
}
