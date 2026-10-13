package com.weaknet.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.VpnService;
import android.os.Build;
import android.os.ParcelFileDescriptor;

import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.util.Arrays;
import java.util.Iterator;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * VPN 接管服务：把设备全部流量导入 tun，在 IP 层重放 TCP/UDP 并注入弱网参数。
 * 这样无需在 Wi-Fi 里手动配置代理，App 内一键开启即可。
 */
public class VpnNetService extends VpnService {
    public static final String ACTION_STOP = "com.weaknet.app.STOP_VPN";
    public static final String ACTION_START = "com.weaknet.app.START_VPN";

    public static volatile boolean active = false;
    /** 用户是否主动停止过接管。START_STICKY 会让系统在 stopService 后重启本服务，
     *  若不记住这个意图，服务会「复活」重新建 tun —— 表现为点了停止却停不下来。
     *  注意：该标志只对「系统重启」生效；用户显式启动（ACTION_START）时必须清除，
     *  否则停止一次后就再也启不来了。 */
    public static volatile boolean userStopped = false;

    /** 当前存活的服务实例。App 与 Service 同进程，停止时可直接拿到实例调用 stopSelf()，
     *  不必绕道 startService —— 后者在服务已停止时会重新拉起一个前台实例，
     *  而它 5 秒内没调用 startForeground，Android 8+ 会直接报错。 */
    private static volatile VpnNetService instance = null;

    private ParcelFileDescriptor tun;
    private FileInputStream tunIn;
    private FileOutputStream tunOut;
    private final Object writeLock = new Object();
    private volatile boolean running = false;

    private final ConcurrentHashMap<String, TcpSession> tcpSessions =
            new ConcurrentHashMap<String, TcpSession>();
    private final ConcurrentHashMap<String, UdpSession> udpSessions =
            new ConcurrentHashMap<String, UdpSession>();
    private ExecutorService exec;
    private Thread cleanThread;

    public void onCreate() {
        super.onCreate();
        instance = this;
    }

    /**
     * 停止接管的唯一入口。Activity、控制台、服务自身都调这里，避免各写一套导致行为不一致。
     *
     * 注意不能只靠 ctx.stopService()：VpnService 会被系统 bind，处于 bound 状态的服务
     * stopService 无效（实测 ServiceRecord 与 tun0 都还在，流量仍被接管）。
     * 必须拿到实例自己 stopSelf()，同时先 stopForeground 撤掉前台通知。
     */
    public static void stop(Context ctx) {
        userStopped = true;
        VpnNetService s = instance;
        if (s != null) {
            try {
                s.stopForeground(true);
            } catch (Exception e) {
                // ignore
            }
            try {
                s.stopVpn();
            } catch (Exception e) {
                // ignore
            }
            try {
                s.stopSelf();
            } catch (Exception e) {
                // ignore
            }
        }
        // 兜底：若实例为空（服务已不在），仍能清掉可能残留的记录
        try {
            ctx.stopService(new Intent(ctx, VpnNetService.class));
        } catch (Exception e) {
            // ignore
        }
    }

    public int onStartCommand(Intent intent, int flags, int startId) {
        String act = (intent == null) ? null : intent.getAction();
        if (ACTION_STOP.equals(act)) {
            userStopped = true;
            try {
                stopForeground(true);
            } catch (Exception e) {
                // ignore
            }
            stopVpn();
            stopSelf();
            return START_NOT_STICKY;
        }
        if (ACTION_START.equals(act)) {
            // 用户显式启动：清除「已停止」意图，保证停止后能再次接管
            userStopped = false;
            if (!running) startVpn();
            return START_STICKY;
        }
        // 走到这里说明 intent 为 null 或没有 action —— 典型情况是系统因 START_STICKY 重启本服务。
        // 此时才尊重用户「已停止」的意图，避免服务复活重建 tun。
        if (userStopped) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!running) startVpn();
        return START_STICKY;
    }

    public void onDestroy() {
        instance = null;
        stopVpn();
        super.onDestroy();
    }

    /** 用户从系统 VPN 设置里撤销授权 */
    public void onRevoke() {
        stopVpn();
        super.onRevoke();
    }

    private void startVpn() {
        Builder b = new Builder()
                .setSession("WeakNet 弱网模拟")
                .addAddress("10.8.0.2", 24)
                .addRoute("0.0.0.0", 0)
                .addDnsServer("8.8.8.8")
                .addDnsServer("114.114.114.114")
                .setMtu(1500)
                .setBlocking(true);
        // ---- 按接管范围决定哪些 App 的流量进 tun ----
        // 注意：addAllowedApplication 与 addDisallowedApplication 互斥，二者只能取其一，
        // 同一个 Builder 上同时调用会抛异常，所以这里必须走 if/else 而不是都调用。
        String mode = WeakNet.I.scopeMode;
        java.util.List<String> pkgs = new java.util.ArrayList<String>(WeakNet.I.scopePkgs);
        if ("only".equals(mode)) {
            if (pkgs.isEmpty()) {
                // 「仅接管」却没选任何 App：不建立 tun，让全部流量走真实网络。
                // 若照样建 tun，allowed 集合为空等于没有任何 App 能出网，表现是全网断网，
                // 与用户「没选 = 还没开始接管」的预期相反。
                WeakNet.I.log("error", "接管范围=仅选定，但未选择任何 App，暂不接管");
                return;
            }
            for (String p : pkgs) {
                try {
                    b.addAllowedApplication(p);
                } catch (Exception e) {
                    WeakNet.I.log("error", "无法接管 " + p + "：" + e.getMessage());
                }
            }
        } else {
            // 排除自身，保证控制台页面（127.0.0.1:8899）与代理通道不被自己捕获
            try {
                b.addDisallowedApplication(getPackageName());
            } catch (Exception e) {
                // ignore
            }
            if ("exclude".equals(mode)) {
                for (String p : pkgs) {
                    if (getPackageName().equals(p)) continue;
                    try {
                        b.addDisallowedApplication(p);
                    } catch (Exception e) {
                        WeakNet.I.log("error", "无法排除 " + p + "：" + e.getMessage());
                    }
                }
            }
        }
        try {
            tun = b.establish();
        } catch (Exception e) {
            WeakNet.I.log("error", "VPN 建立失败：" + e.getMessage());
            return;
        }
        if (tun == null) {
            WeakNet.I.log("error", "VPN 未获得授权");
            return;
        }
        tunIn = new FileInputStream(tun.getFileDescriptor());
        tunOut = new FileOutputStream(tun.getFileDescriptor());
        running = true;
        active = true;
        userStopped = false;
        exec = Executors.newCachedThreadPool();
        WeakNet.onProfileChange = new Runnable() {
            public void run() {
                // 恢复直连 / 停止模拟时不要掐断：连接会自然按新参数继续（限速桶已归零）
                if ("direct".equals(WeakNet.I.presetKey)) return;
                // 切到弱网场景：让已有连接按新参数重建
                for (TcpSession s : tcpSessions.values()) s.abort();
                tcpSessions.clear();
                for (UdpSession s : udpSessions.values()) s.close();
                udpSessions.clear();
            }
        };
        startForegroundCompat();
        WeakNet.I.log("config", "VPN 接管已开启：全部 App 流量进入弱网模拟");
        // 限速飘窗（悬浮显示当前场景与实时速率）；未授予悬浮窗权限时自动跳过
        try {
            FloatWindow.get(this).show();
        } catch (Exception e) {
            // ignore
        }

        Thread readThread = new Thread(new Runnable() {
            public void run() {
                readLoop();
            }
        }, "tun-read");
        readThread.setDaemon(true);
        readThread.start();

        cleanThread = new Thread(new Runnable() {
            public void run() {
                while (running) {
                    try {
                        Thread.sleep(30000);
                    } catch (InterruptedException e) {
                        break;
                    }
                    cleanup();
                }
            }
        }, "tun-clean");
        cleanThread.setDaemon(true);
        cleanThread.start();
    }

    private void stopVpn() {
        running = false;
        active = false;
        for (TcpSession s : tcpSessions.values()) s.close();
        tcpSessions.clear();
        for (UdpSession s : udpSessions.values()) s.close();
        udpSessions.clear();
        try {
            if (tunIn != null) tunIn.close();
            if (tunOut != null) tunOut.close();
            if (tun != null) tun.close();
        } catch (Exception e) {
            // ignore
        }
        if (exec != null) exec.shutdownNow();
        try {
            FloatWindow.get(this).hide();      // 停止接管时收起飘窗
        } catch (Exception e) {
            // ignore
        }
        WeakNet.I.log("config", "VPN 接管已停止，流量恢复正常");
    }

    private void readLoop() {
        byte[] buf = new byte[32767];
        while (running) {
            try {
                int n = tunIn.read(buf);
                if (n <= 0) continue;
                final byte[] pkt = Arrays.copyOf(buf, n);
                handlePacket(pkt, n);
            } catch (Exception e) {
                if (!running) break;
            }
        }
    }

    private void handlePacket(final byte[] pkt, int len) {
        try {
            if ((pkt[0] >> 4) != 4) return;                     // 仅处理 IPv4
            int ihl = (pkt[0] & 0xF) * 4;
            int totalLen = IpUtil.u16(pkt, 2);
            if (totalLen > len || totalLen <= ihl) return;
            int frag = IpUtil.u16(pkt, 6);
            if ((frag & 0x1FFF) != 0) return;                   // 分片包：丢弃
            int proto = pkt[9] & 0xFF;
            final String src = IpUtil.ipToString(pkt, 12);
            final String dst = IpUtil.ipToString(pkt, 16);

            if (proto == 6) {
                int off = ihl;
                if (off + 20 > totalLen) return;
                int srcPort = IpUtil.u16(pkt, off);
                int dstPort = IpUtil.u16(pkt, off + 2);
                long seq = IpUtil.u32(pkt, off + 4);
                long ack = IpUtil.u32(pkt, off + 8);
                int dataOff = ((pkt[off + 12] & 0xFF) >> 4) * 4;
                if (dataOff < 20) return;
                int flags = pkt[off + 13] & 0x3F;
                int window = IpUtil.u16(pkt, off + 14);
                int payloadLen = totalLen - ihl - dataOff;
                byte[] payload = null;
                if (payloadLen > 0) {
                    payload = new byte[payloadLen];
                    System.arraycopy(pkt, ihl + dataOff, payload, 0, payloadLen);
                }
                final String key = src + ":" + srcPort + ">" + dst + ":" + dstPort;
                boolean syn = (flags & IpUtil.SYN) != 0;
                boolean ackFlag = (flags & IpUtil.ACK) != 0;
                TcpSession s = tcpSessions.get(key);
                if (s == null) {
                    if (syn && !ackFlag) {
                        int mss = 1460;
                        int i = off + 20;
                        while (i + 1 < off + dataOff) {
                            int kind = pkt[i] & 0xFF;
                            if (kind == 0) break;
                            int optLen = (kind == 1) ? 1 : (pkt[i + 1] & 0xFF);
                            if (optLen <= 0) break;
                            if (kind == 2 && optLen == 4) mss = IpUtil.u16(pkt, i + 2);
                            i += optLen;
                        }
                        final TcpSession ns = new TcpSession(this, src, srcPort, dst, dstPort,
                                key, seq, mss);
                        tcpSessions.put(key, ns);
                        WeakNet.I.connTotal.incrementAndGet();
                        exec.execute(new Runnable() {
                            public void run() {
                                ns.start();
                                if (ns.isClosed()) tcpSessions.remove(key);
                            }
                        });
                    } else {
                        // 未知会话：回 RST
                        write(IpUtil.buildTcp(dst, dstPort, src, srcPort,
                                0, (seq + 1) & 0xFFFFFFFFL, IpUtil.RST | IpUtil.ACK,
                                null, 0, 0, null));
                    }
                    return;
                }
                final TcpSession fs = s;
                final byte[] fp = payload;
                final int fpl = payloadLen;
                final int ff = flags;
                final long fseq = seq;
                final long lack = ack;
                final int fw = window;
                exec.execute(new Runnable() {
                    public void run() {
                        fs.onClientPacket(ff, fseq, lack, fp, fpl, fw);
                        if (fs.isClosed()) tcpSessions.remove(key);
                        else if ((ff & IpUtil.RST) != 0) tcpSessions.remove(key);
                    }
                });
            } else if (proto == 17) {
                int off = ihl;
                if (off + 8 > totalLen) return;
                int srcPort = IpUtil.u16(pkt, off);
                int dstPort = IpUtil.u16(pkt, off + 2);
                int ulen = IpUtil.u16(pkt, off + 4);
                int payloadLen = ulen - 8;
                if (payloadLen < 0 || off + ulen > totalLen) return;
                byte[] payload = new byte[payloadLen];
                System.arraycopy(pkt, off + 8, payload, 0, payloadLen);
                final String key = src + ":" + srcPort + ">" + dst + ":" + dstPort;
                UdpSession us = udpSessions.get(key);
                if (us == null) {
                    us = new UdpSession(this, src, srcPort, dst, dstPort, key);
                    udpSessions.put(key, us);
                    WeakNet.I.connTotal.incrementAndGet();
                }
                final UdpSession fus = us;
                final byte[] fp = payload;
                exec.execute(new Runnable() {
                    public void run() {
                        fus.send(fp, fp.length);
                    }
                });
            }
        } catch (Exception e) {
            // ignore
        }
    }

    /**
     * 写回 tun —— 下行流量的唯一出口，限速在这里统一执行，
     * 保证送给 App 的每一个字节都过闸（包括 TCP/UDP/控制包）。
     */
    public void write(byte[] packet) {
        int payload = payloadLength(packet);
        if (payload > 0) {
            try {
                WeakNet.I.downBucket.consume(payload);
            } catch (Exception e) {
                // ignore
            }
            WeakNet.I.bytesDown.addAndGet(payload);
        }
        synchronized (writeLock) {
            try {
                tunOut.write(packet);
            } catch (Exception e) {
                // ignore
            }
        }
    }

    private static int payloadLength(byte[] packet) {
        try {
            if ((packet[0] >> 4) != 4) return 0;
            int ihl = (packet[0] & 0xF) * 4;
            int proto = packet[9] & 0xFF;
            if (proto == 6) {
                int doff = ((packet[ihl + 12] & 0xFF) >> 4) * 4;
                return Math.max(0, packet.length - ihl - doff);
            }
            if (proto == 17) {
                return Math.max(0, packet.length - ihl - 8);
            }
        } catch (Exception e) {
            // ignore
        }
        return 0;
    }

    public void scheduleRemove(final String key, final TcpSession s, long delayMs) {
        if (exec == null || exec.isShutdown()) return;
        exec.execute(new Runnable() {
            public void run() {
                try {
                    Thread.sleep(60000);
                } catch (InterruptedException e) {
                    // ignore
                }
                s.close();
                tcpSessions.remove(key);
            }
        });
    }

    /** 清理长时间无流量的会话 */
    private void cleanup() {
        long now = System.currentTimeMillis();
        Iterator<Map.Entry<String, TcpSession>> it = tcpSessions.entrySet().iterator();
        while (it.hasNext()) {
            Map.Entry<String, TcpSession> e = it.next();
            if (now - e.getValue().lastActive() > 300000 || e.getValue().isClosed()) {
                e.getValue().close();
                it.remove();
            }
        }
        Iterator<Map.Entry<String, UdpSession>> iu = udpSessions.entrySet().iterator();
        while (iu.hasNext()) {
            Map.Entry<String, UdpSession> e = iu.next();
            if (now - e.getValue().lastActive() > 120000 || e.getValue().isClosed()) {
                e.getValue().close();
                iu.remove();
            }
        }
    }

    private void startForegroundCompat() {
        Notification.Builder nb;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel("weaknet_vpn", "弱网接管",
                    NotificationManager.IMPORTANCE_LOW);
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) nm.createNotificationChannel(ch);
            nb = new Notification.Builder(this, "weaknet_vpn");
        } else {
            nb = new Notification.Builder(this);
        }
        Intent i = new Intent(this, MainActivity.class);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pi = PendingIntent.getActivity(this, 0, i, flags);
        nb.setContentTitle("WeakNet 正在接管流量")
                .setContentText("全部 App 已进入弱网模拟 · 点击打开控制台")
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentIntent(pi)
                .setOngoing(true);
        startForeground(2, nb.build());
    }
}
