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
    // 限速总开关：App 启动后默认关闭 —— 只是待命，不施加任何弱网参数，
    // 用户必须显式开启（控制台点「开启限速」或选一个场景）才生效
    public volatile boolean limitOn = false;
    public volatile Profile lastProfile = null;    // 最后一次生效的场景（再次开启时复原）
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

    /** 接管范围：all=全部 App / only=仅下列 / exclude=排除下列（存包名） */
    public volatile String scopeMode = "all";
    public final java.util.List<String> scopePkgs =
            java.util.Collections.synchronizedList(new java.util.ArrayList<String>());

    /** 更新接管范围。返回规范化后的 mode。非法值回落为 all。 */
    public synchronized String setScope(String mode, java.util.Collection<String> pkgs) {
        if (!"only".equals(mode) && !"exclude".equals(mode)) mode = "all";
        scopeMode = mode;
        scopePkgs.clear();
        if (pkgs != null) {
            for (String p : pkgs) {
                if (p == null) continue;
                p = p.trim();
                if (p.length() > 0 && !scopePkgs.contains(p)) scopePkgs.add(p);
            }
        }
        return scopeMode;
    }

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
        // 应用了「有内容」的场景就等于开启限速，回到全零（直连）就等于是关掉：
        // 三个入口（App 按钮 / 控制台 / 接口）不必各自维护一份开关状态
        limitOn = p.hasLimits();
        if (limitOn) lastProfile = p.copy();   // 记住最后一次生效的场景，供「再次开启」复原
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
     * 限速总开关。关闭不是丢掉用户选的场景：presetKey 保留，下次开启直接复原，
     * 免得每次开关都要重选一遍。
     *
     * @param restore 开启时要应用的场景（由调用方按 presetKey 解析好；为 null 则用直连）
     */
    public synchronized void setLimitOn(boolean on, Profile restore) {
        if (on) {
            setProfile(restore != null ? restore : new Profile(), presetKey);
        } else {
            setProfile(new Profile(), presetKey);
        }
        limitOn = on;      // 覆盖 setProfile 的推断：自定义全零也算「已开启」
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
