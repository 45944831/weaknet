package com.weaknet.app;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;

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
                sendAck();          // 乱序/重传：回 ACK，等待重传补齐
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
                sout.write(payload, 0, payloadLen);
                sout.flush();
            } catch (Exception e) {
                close();
                return;
            }
            clientSeq = (clientSeq + payloadLen) & 0xFFFFFFFFL;
            sendAck();
        }
        if ((flags & IpUtil.FIN) != 0) {
            clientSeq = (clientSeq + 1) & 0xFFFFFFFFL;
            sendAck();
            try {
                if (socket != null) socket.shutdownOutput();
            } catch (Exception e) {
                // ignore
            }
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
                        synchronized (TcpSession.this) {
                            if (closed) break;
                            vpn.write(IpUtil.buildTcp(remoteIp, remotePort, clientIp, clientPort,
                                    mySeq, clientSeq, IpUtil.PSH | IpUtil.ACK, buf, n, 65535, null));
                            mySeq = (mySeq + n) & 0xFFFFFFFFL;
                        }
                    }
                } catch (Exception e) {
                    // ignore
                }
                synchronized (TcpSession.this) {
                    if (!closed) {
                        vpn.write(IpUtil.buildTcp(remoteIp, remotePort, clientIp, clientPort,
                                mySeq, clientSeq, IpUtil.FIN | IpUtil.ACK, null, 0, 65535, null));
                        mySeq = (mySeq + 1) & 0xFFFFFFFFL;
                    }
                }
                close();
            }
        }, "tcp-down");
        t.setDaemon(true);
        t.start();
    }

    private void sendAck() {
        synchronized (this) {
            if (closed) return;
            vpn.write(IpUtil.buildTcp(remoteIp, remotePort, clientIp, clientPort,
                    mySeq, clientSeq, IpUtil.ACK, null, 0, 65535, null));
        }
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
