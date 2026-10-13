package com.weaknet.app;

import java.io.Closeable;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/** 全局状态：当前参数、统计、事件、在途连接、周期断网调度 */
public class WeakNet {
    public static final WeakNet I = new WeakNet();

    public volatile Profile profile = new Profile();
    public volatile String presetKey = "direct";
    public volatile boolean gateOpen = true;

    public final AtomicInteger connTotal = new AtomicInteger();
    public final AtomicInteger connActive = new AtomicInteger();
    public final AtomicLong bytesUp = new AtomicLong();
    public final AtomicLong bytesDown = new AtomicLong();

    public final List<String[]> events = Collections.synchronizedList(new ArrayList<String[]>());
    public final Set<Object> active = Collections.synchronizedSet(new HashSet<Object>());

    public long startedAt = System.currentTimeMillis();

    /** 整机共享的上/下行令牌桶（手机只有一条无线链路，所有连接共享带宽） */
    public volatile Pipe.Bucket upBucket = new Pipe.Bucket(0);
    public volatile Pipe.Bucket downBucket = new Pipe.Bucket(0);

    /** 切换场景时回调（VPN 模式用它让已有连接立即按新参数重建） */
    public static volatile Runnable onProfileChange = null;

    /** 参数代数：每次切换场景 +1，用于打断黑洞等长时间挂起 */
    public volatile int profileGen = 0;

    private Thread flapThread;

    public void log(String type, String msg) {
        String t = new SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(new Date());
        synchronized (events) {
            events.add(new String[]{t, type, msg});
            while (events.size() > 300) events.remove(0);
        }
    }

    public synchronized void setProfile(Profile p, String key) {
        profile = p;
        presetKey = key;
        gateOpen = true;
        profileGen++;                       // 唤醒/打断黑洞等长时间挂起
        // 原地更新速率：已在途连接持有的正是这两个桶，切换/停止后立刻生效
        upBucket.setKbps(p.upKbps);
        downBucket.setKbps(p.downKbps);
        Runnable hook = onProfileChange;
        if (hook != null) {
            try {
                hook.run();
            } catch (Exception e) {
                // ignore
            }
        }
        if (flapThread != null) {
            flapThread.interrupt();
            flapThread = null;
        }
        if (p.flapSec > 0) {
            flapThread = new Thread(new Runnable() {
                public void run() {
                    try {
                        while (true) {
                            Thread.sleep(WeakNet.I.profile.flapSec * 1000L);
                            if (WeakNet.I.profile.flapSec <= 0) return;
                            gateOpen = false;
                            log("flap", "网络切换：全部连接中断 " + profile.flapBlackoutSec + "s");
                            abortAll();
                            Thread.sleep(profile.flapBlackoutSec * 1000L);
                            gateOpen = true;
                            log("flap", "网络恢复");
                        }
                    } catch (InterruptedException e) {
                        gateOpen = true;
                    }
                }
            }, "flap");
            flapThread.setDaemon(true);
            flapThread.start();
        }
    }

    /**
     * 可被「切换场景 / 停止模拟」打断的 sleep。
     * @return true = 参数已变化，被提前唤醒（调用方应按新参数继续，而不是继续挂死）
     */
    public boolean sleepInterruptible(long ms) {
        int gen = profileGen;
        long end = System.currentTimeMillis() + Math.max(0, ms);
        while (true) {
            if (profileGen != gen) return true;
            long left = end - System.currentTimeMillis();
            if (left <= 0) return false;
            try {
                Thread.sleep(Math.min(200, left));
            } catch (InterruptedException e) {
                return profileGen != gen;
            }
        }
    }

    public void register(Object o) {
        if (o != null) active.add(o);
    }

    public void unregister(Object o) {
        if (o != null) active.remove(o);
    }

    /** 周期断网：立即掐断所有在途连接 */
    public void abortAll() {
        List<Object> copy;
        synchronized (active) {
            copy = new ArrayList<Object>(active);
            active.clear();
        }
        for (Object o : copy) {
            if (o instanceof Closeable) {
                try {
                    ((Closeable) o).close();
                } catch (Exception e) {
                    // ignore
                }
            }
        }
    }

    public String lanIp() {
        try {
            Enumeration<NetworkInterface> en = NetworkInterface.getNetworkInterfaces();
            while (en.hasMoreElements()) {
                NetworkInterface ni = en.nextElement();
                if (ni.isLoopback() || !ni.isUp()) continue;
                Enumeration<InetAddress> addrs = ni.getInetAddresses();
                while (addrs.hasMoreElements()) {
                    InetAddress a = addrs.nextElement();
                    if (a instanceof java.net.Inet4Address && !a.isLoopbackAddress()) {
                        return a.getHostAddress();
                    }
                }
            }
        } catch (Exception e) {
            // ignore
        }
        return "127.0.0.1";
    }
}
