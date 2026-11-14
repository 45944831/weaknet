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
import android.os.Process;

import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.util.Arrays;
import java.util.Iterator;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * VPN 接管服务：把设备全部流量导入 tun，在 IP 层重放 TCP/UDP 并注入弱网参数。
 * 这样无需在 Wi-Fi 里手动配置代理，App 内一键开启即可。
 */
public class VpnNetService extends VpnService {
    public static final String ACTION_STOP = "com.weaknet.app.STOP_VPN";
    public static final String ACTION_START = "com.weaknet.app.START_VPN";

    /**
     * tun 的 MTU —— **单一真值**。
     *
     * MSS 通告值、TcpSession 的下游读缓冲都从 {@link #tunMtu()} 派生。三者必须一起变：
     * 只把 MTU 调大，对端仍按旧 MSS 分段，TUN 侧永远收不到大包，实验会得出
     * 「提 MTU 没有收益」的错误结论（回归用例见 _jw/.../_MssClampTest.java 与
     * _MtuAbTest.java，语义说明见 docs/precision.md §3.5）。
     * 默认仍取 1500：更大 MTU 需要机型兼容性与 PMTUD 验证后才可切换；
     * 实验档只能通过 {@link #applyMtuTier(int)} 在建立 tun 之前选中。
     */
    static final int DEFAULT_TUN_MTU = 1500;

    /**
     * 允许的 MTU 档位（P1-2 / P1-3 的实验档）。
     *
     * 1280 是 IPv6 要求的最小链路 MTU；16384 让 TcpSession 的下游读缓冲与
     * MSS 一起落到 16 KB 级（16344 B），也就是方案 P1-2 说的「对齐到 16 KB 级」——
     * 只有把 MTU 抬上来才谈得上对齐，默认 1500 时上限就是 1460。
     *
     * 只允许取这几档：任意值会让「MSS / 缓冲 / setMtu 三处同源」失去可验证性，
     * 也让真机 A/B 的结论无法复现。
     */
    static final int[] MTU_TIERS = {1280, 1500, 9000, 16384};

    /** 当前生效的 MTU。默认 1500；实验档必须在建立 tun 之前切换。 */
    private static volatile int tunMtu = DEFAULT_TUN_MTU;

    /**
     * 当前 tun **实际**生效的 MTU：establish() 成功时落档，stopVpn() 清零。
     * 与 {@link #tunMtu()}（静态请求档）的区别：rebuild 是异步的（stop + 700ms
     * 后才 ACTION_START），期间静态档已是新值而 tun 还不存在或还是旧档——
     * 复测 §3.6 实证过「响应 ok 但 vpn_active=false、无 tun0」的窗口，
     * 贴着这个窗口开测会产出「标签正确、前提错误」的脏样本。调用方必须
     * 轮询 /api/status 的 effective_mtu == 目标档后才能开测。
     */
    private static volatile int effectiveTunMtu = 0;

    static int tunMtu() {
        return tunMtu;
    }

    static int effectiveMtu() {
        return effectiveTunMtu;
    }

    /** 静态档与实际生效档一致、且 tun 存在 = 切档调用方可以开测 */
    static boolean mtuReady() {
        return effectiveTunMtu != 0 && effectiveTunMtu == tunMtu;
    }

    /** 无对端 MSS 时的默认值：IPv4 20B + TCP 20B 头 */
    static int defaultMss() {
        return tunMtu - 40;
    }

    /**
     * 切换 MTU 档（仅实验用）。返回值 false 表示档位不在 {@link #MTU_TIERS} 里。
     *
     * 已经建立的会话各自持有协商好的 clientMss，不会被这次切换改变 —— 所以
     * A/B 必须在「停止 → 设档 → 启动」之间做，不能边跑边切。
     */
    static boolean applyMtuTier(int mtu) {
        for (int t : MTU_TIERS) {
            if (t == mtu) {
                tunMtu = mtu;
                return true;
            }
        }
        return false;
    }

    /**
     * MTU↔MSS 联动的**参数化纯函数**：P1-3 四档 A/B（1500/4096/8192/16384）
     * 只要换 mtu 就能算出该档的 MSS 与下游读缓冲，不必改四处硬编码。
     * 真机吞吐实验仍需打包后进行；这里锁住的是「三处必然一起变」的正确性。
     */
    static int mssForMtu(int mtu, int peerMss) {
        int max = Math.max(536, mtu - 40);      // IPv4 20B + TCP 20B 头
        if (peerMss <= 0) return max;          // 未知 MSS → 用该档上限
        return Math.max(536, Math.min(peerMss, max));
    }

    /** 下游读缓冲大小：与 MSS 同源派生，杜绝「MTU 提了、缓冲还锁在 1400」 */
    static int downstreamBufferForMtu(int mtu) {
        return mssForMtu(mtu, 0);
    }

    /** 把对端/默认 MSS 收进合法区间，上限随当前 MTU 走 */
    static int clampMss(int mss) {
        return mssForMtu(tunMtu(), mss);
    }

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
    /** 只在「写线程未运行」的兜底直写路径上使用；正常路径靠单写线程天然串行 */
    private final Object writeLock = new Object();
    private volatile boolean running = false;

    /**
     * 写回 tun 的有界队列 + 单写线程。
     *
     * 旧实现是 `synchronized (writeLock) { tunOut.write(packet); }`：一次 tun fd 写约
     * 50µs，所有 TCP/UDP 流的下行包都在这把锁上排队，且调用方（会话锁内）要陪着等
     * —— 一条慢连接能把所有流一起拖住（Outline SDK #631 是同一个问题）。
     * 现在改成「入队即返回、单线程串行写」：会话锁的临界区从「一次系统调用」
     * 缩到「一次入队」，下行顺序由 FIFO 保证，与原来的锁序等价。
     *
     * 队列必须有界：满了就让调用方阻塞，压力沿链路回传（与原 writeLock 的阻塞语义一致）。
     * 1024 × ~1.5KB ≈ 1.5MB 上限。
     */
    private static final int OUTQ_CAP = 1024;
    /** 抽成纯 JDK 类是为了让「有界 / FIFO / 停止清空」能被单测覆盖，见 _TunWriteQueueTest */
    private final TunWriteQueue outQ = new TunWriteQueue(OUTQ_CAP);
    private volatile boolean writerRunning = false;
    private Thread writerThread;

    private final ConcurrentHashMap<String, TcpSession> tcpSessions =
            new ConcurrentHashMap<String, TcpSession>();
    private final ConcurrentHashMap<String, UdpSession> udpSessions =
            new ConcurrentHashMap<String, UdpSession>();
    /**
     * 端口对直接映射缓存：端口对 -> 会话。
     * 每个包都要查会话表，原来为此把 IP 转成字符串再拼 key（每包十几次分配）。
     * 端口对相同但 IP 不同的流会抢同一个槽，命中后用 sameFlow() 核对 IP——
     * 核不上就回落字符串 key 的慢路径，正确性不受影响。
     * 两个数组共 512KB 引用，换热路径零分配，划算。
     */
    private final TcpSession[] tcpFast = new TcpSession[65536];
    private final UdpSession[] udpFast = new UdpSession[65536];
    private ExecutorService exec;
    /** 定时任务（延迟清理）：用调度器而不是「池线程里 sleep」。见 scheduleRemove */
    private ScheduledExecutorService sched;
    private Thread cleanThread;

    /** 会话关闭时清槽。身份校验：槽位可能已被同端口对的新流占用，不能误清。 */
    void clearTcpFast(int idx, TcpSession s) {
        synchronized (tcpFast) {
            if (tcpFast[idx] == s) tcpFast[idx] = null;
        }
    }

    void clearUdpFast(int idx, UdpSession s) {
        synchronized (udpFast) {
            if (udpFast[idx] == s) udpFast[idx] = null;
        }
    }

    /**
     * 把任务投到共享线程池（下游读循环用：省每连接建线程，顺序天然保持）。
     *
     * 这里**必须**保持 cached 语义、不能换成固定大小的池：下游读循环是阻塞式
     * `sin.read()`，每条活跃连接会长期占住一个线程直到 EOF。固定池在连接数超过
     * 池大小时会让新连接的上行直接没有线程可用（数据面停摆），而 cached 池的
     * 线程数与「活跃连接数」同阶，正是阻塞 IO 的固有代价。
     * 真正能压缩的线程在别处：延迟清理不再 sleep、UDP 不再每包派发。
     */
    public void runOnPool(Runnable r, String name) {
        ExecutorService e = exec;
        if (e == null || e.isShutdown()) {
            r.run();
            return;
        }
        e.execute(r);
    }

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
        stop(ctx, true);
    }

    /**
     * @param resetMtuTier 用户主动停止时把 MTU 实验档复位到默认 1500（复测 P2：
     *                     关闭不复位的话，下次不带 mtu 开启会悄悄沿用上次的
     *                     实验档）。rebuild 走 {@code stop(ctx, false)}——重建
     *                     就是为了用新档建 tun，复位会把刚选的档冲掉。
     */
    public static void stop(Context ctx, boolean resetMtuTier) {
        userStopped = true;
        if (resetMtuTier && tunMtu != DEFAULT_TUN_MTU) {
            tunMtu = DEFAULT_TUN_MTU;
            try {
                WeakNet.I.log("config", "MTU 实验档已复位 1500（MSS " + defaultMss() + "）");
            } catch (Exception e) {
                // ignore
            }
        }
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

    /**
     * 改了「接管范围」后重建通道。VpnService 的「哪些 App 走 tun」只在 establish() 时生效，
     * 不停掉重建的话，新范围对已经建立的连接无效（表现就是「加了名单却没生效」）。
     *
     * 用 ACTION_START 重启：它会清掉 userStopped，避免被上一轮的「不复活」逻辑挡住。
     */
    public static void rebuild(Context ctx) {
        boolean was = active;
        stop(ctx, false);      // 重建就是为用新档建 tun，复位会把刚选的档冲掉
        if (!was) return;
        new android.os.Handler(ctx.getMainLooper()).postDelayed(new Runnable() {
            public void run() {
                try {
                    Intent i = new Intent(ctx, VpnNetService.class);
                    i.setAction(ACTION_START);
                    if (android.os.Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i);
                    else ctx.startService(i);
                } catch (Exception e) {
                    WeakNet.I.log("error", "重建 VPN 失败：" + e.getMessage());
                }
            }
        }, 700);      // 等上一个 tun 完全释放再建，避免路由/端口冲突
    }

    public int onStartCommand(Intent intent, int flags, int startId) {
        // 实验档入口（P1-2 / P1-3 的真机 A/B 用）：
        //   POST /api/vpn {"enable":true,"mtu":16384}
        // 只接受 MTU_TIERS 里的值；非法值保持原档并记一条日志。
        // 注意不能从 adb 直接起服务：清单里本服务声明了 BIND_VPN_SERVICE 权限，
        // `am start-foreground-service ... -e mtu 16384` 会被系统拒掉
        // （Error: Requires permission android.permission.BIND_VPN_SERVICE），
        // 所以真机切档只能走控制接口（由 App 自己发起）。
        // 必须发生在 startVpn() 之前：tun 一旦建立，MTU 就定了。
        if (intent != null && intent.hasExtra("mtu")) {
            try {
                String raw = intent.getStringExtra("mtu");
                int want = Integer.parseInt(String.valueOf(raw).trim());
                if (applyMtuTier(want)) {
                    dlog("mtu: 实验档 " + want + "（MSS " + defaultMss() + "）"
                            + (running ? "；tun 已建立，下次启动生效" : ""));
                } else {
                    dlog("mtu: 忽略非法档位 " + want + "（可选 1280/1500/9000/16384）");
                }
            } catch (Exception e) {
                dlog("mtu: 解析失败 " + e);
            }
        }
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
            if (!running) startVpnGuarded();
            return START_STICKY;
        }
        // 走到这里说明 intent 为 null 或没有 action —— 典型情况是系统因 START_STICKY 重启本服务。
        // 此时才尊重用户「已停止」的意图，避免服务复活重建 tun。
        if (userStopped) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!running) startVpnGuarded();
        return START_STICKY;
    }

    /**
     * 建 tun 的统一入口：先无条件进前台，再决定要不要真的接管。
     *
     * Android 的硬性契约：被 startForegroundService() 拉起的服务必须在 5 秒内
     * 调用 startForeground()，否则系统直接杀掉整个进程，logcat 里是
     *   Killing <pid> (adj 200): user request after error:
     *   Context.startForegroundService() did not then call Service.startForeground()
     *
     * startVpn() 有若干条「这次不接管」的早退路径（接管范围=仅选定却没选 App、
     * 系统未授权导致 establish() 返回 null……）。以前这些路径都直接 return，
     * 于是「没建起来」被系统升级成「进程被杀」—— 用户看到的是 App 突然消失、
     * 悬浮窗和通知一起没了，日志里只有一句 ANR，与真实原因（没授权）完全对不上。
     * 真机实测踩到过：/api/vpn 打开时 establish() 返回 null，5 秒后进程被 KILL。
     *
     * 所以顺序改成「先进前台 → 再建 tun → 没建成就撤前台并 self-stop」，
     * 任何早退路径都不会再违反契约。
     */
    private void startVpnGuarded() {
        startForegroundCompat();
        startVpn();
        if (!running) {
            try {
                stopForeground(true);
            } catch (Exception e) {
                // ignore
            }
            stopSelf();
        }
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
        // 先向系统确认授权状态。
        //
        // VpnService.prepare() 在 appops ACTIVATE_VPN 已放行时直接返回 null，
        // 副作用是**把本包标记为已准备**——不调它，establish() 会一律返回 null
        // （logcat 只有一句 "VPN 未获得授权"，看不出其实是「没 prepare」）。
        // 这也让 /api/vpn 这条无界面通道以前根本开不了 VPN：只有界面点「开始」
        // 的路径调过 prepare()，脚本驱动的测试因此只能靠点按钮。
        // 返回非 null 说明系统要弹授权框，服务里没法弹，只能让用户去界面上点一次。
        try {
            Intent consent = VpnService.prepare(this);
            if (consent != null) {
                WeakNet.I.log("error", "VPN 尚未授权：请先在界面上点一次「开始弱网测试」");
                return;
            }
        } catch (Exception e) {
            WeakNet.I.log("error", "VPN 授权检查失败：" + e.getMessage());
            return;
        }
        Builder b = new Builder()
                .setSession("WeakNet 弱网模拟")
                .addAddress("10.8.0.2", 24)
                .addRoute("0.0.0.0", 0)
                .addDnsServer("8.8.8.8")
                .addDnsServer("114.114.114.114")
                .setMtu(tunMtu())
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
            // 把自己排除掉：一旦 WeakNet 自己的流量也进 tun，控制台 8899 就跟着被接管，
            // 界面会整个失联，只能杀进程恢复
            java.util.List<String> allow = new java.util.ArrayList<String>();
            for (String p : pkgs) {
                if (!getPackageName().equals(p)) allow.add(p);
            }
            if (allow.isEmpty()) {
                WeakNet.I.log("error", "接管范围只剩 WeakNet 自身，已忽略，暂不接管");
                return;
            }
            for (String p : allow) {
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
        effectiveTunMtu = tunMtu;   // 采集/脚本的就绪判据（见 effectiveTunMtu 注释）
        userStopped = false;
        exec = Executors.newCachedThreadPool();
        sched = Executors.newSingleThreadScheduledExecutor();
        // 写线程必须早于 readLoop 起来：readLoop 处理完第一个包就会开始回写
        startWriter();
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
        // 前台通知已经在 startVpnGuarded() 里发过了（必须在建 tun 之前，
        // 否则早退路径会触发系统的 5 秒契约检查）；这里只记账。
        WeakNet.I.log("config", "VPN 接管已开启：全部 App 流量进入弱网模拟");
        // 限速飘窗（悬浮显示当前场景与实时速率）；未授予悬浮窗权限时自动跳过
        try {
            FloatWindow.get(this).show();
        } catch (Exception e) {
            // ignore
        }

        Thread readThread = new Thread(new Runnable() {
            public void run() {
                // 数据面热线程提权到 nice -8（THREAD_PRIORITY_URGENT_DISPLAY）：
                // AntMonitor 在完全相同的两个角色（tun 读线程 + 受保护网 I/O 线程）
                // 上就是这么做的；净效果是 CPU 争抢（GC / 后台编译 / 其它 App）
                // 时不被降权。不用 AUDIO(-16/-19)：会饿死自己的非数据线程。
                Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY);
                readLoop();
            }
        }, "tun-read");
        readThread.setDaemon(true);
        readThread.start();
        dlog("readLoop thread started, tunIn=" + tunIn);

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
        effectiveTunMtu = 0;   // tun 已不存在：就绪判据立即失效（先于一切清理）
        for (TcpSession s : tcpSessions.values()) s.close();
        tcpSessions.clear();
        for (UdpSession s : udpSessions.values()) s.close();
        udpSessions.clear();
        stopWriter();          // 先停写线程再关 fd：否则它可能阻塞在已失效的 fd 上
        try {
            if (tunIn != null) tunIn.close();
            if (tunOut != null) tunOut.close();
            if (tun != null) tun.close();
        } catch (Exception e) {
            // ignore
        }
        if (sched != null) {
            sched.shutdownNow();
            sched = null;
        }
        if (exec != null) exec.shutdownNow();
        try {
            FloatWindow.get(this).hide();      // 停止接管时收起飘窗
        } catch (Exception e) {
            // ignore
        }
        WeakNet.I.log("config", "VPN 接管已停止，流量恢复正常");
    }

    /**
     * 诊断开关：定位 tun 路径丢包问题时置 true，日志 tag=WeakNetDiag（logcat -s WeakNetDiag:I）。
     * 正式包保持 false —— 每个进隧道的包会打 2-3 条日志，高频场景下既拖性能又刷爆日志缓冲。
     * 排障时改为 true 重新构建即可，无需其他改动。
     */
    static final boolean DIAG = false;
    private static final int DIAG_MAX = 100000;
    private static final java.util.concurrent.atomic.AtomicInteger diagCount =
            new java.util.concurrent.atomic.AtomicInteger();
    private static void dlog(String s) {
        if (!DIAG) return;
        if (diagCount.incrementAndGet() > DIAG_MAX) return;
        android.util.Log.i("WeakNetDiag", s);
    }

    private void readLoop() {
        byte[] buf = new byte[32767];
        while (running) {
            try {
                int n = tunIn.read(buf);
                if (n <= 0) { dlog("readLoop: read returned " + n); continue; }
                dlog("readLoop: read " + n + " bytes, first=0x"
                        + Integer.toHexString(buf[0] & 0xFF));
                // 直接传 buf：handlePacket 是同步调用，返回前 buf 不会被覆写；
                // 需要跨线程保留的负载它在内部各自拷贝（TCP/UDP payload）。
                // 原来外层先 Arrays.copyOf 一整包再处理，那份拷贝没有任何消费者。
                handlePacket(buf, n);
            } catch (Exception e) {
                dlog("readLoop: EXC " + e);
                if (!running) break;
            }
        }
        dlog("readLoop: EXIT running=" + running);
    }

    /** 快路径查表：一次进入监视器，命中且 IP 对得上才返回（miss 才走慢路径） */
    private TcpSession tcpFastGet(int idx, byte[] pkt) {
        synchronized (tcpFast) {
            TcpSession s = tcpFast[idx];
            return (s != null && s.sameFlow(pkt)) ? s : null;
        }
    }

    private void tcpFastSet(int idx, TcpSession s) {
        synchronized (tcpFast) {
            tcpFast[idx] = s;
        }
    }

    private UdpSession udpFastGet(int idx) {
        synchronized (udpFast) {
            return udpFast[idx];
        }
    }

    private void udpFastSet(int idx, UdpSession s) {
        synchronized (udpFast) {
            udpFast[idx] = s;
        }
    }

    /** 从会话表摘除（仅当表里还是它自己，避免把同 4 元组的新会话误删） */
    private void removeTcp(TcpSession s) {
        TcpSession cur = tcpSessions.get(s.key);
        if (cur == s) tcpSessions.remove(s.key);
    }

    private void handlePacket(final byte[] pkt, int len) {
        try {
            if ((pkt[0] >> 4) != 4) { dlog("hp: not ipv4, first=0x" + Integer.toHexString(pkt[0] & 0xFF)); return; }  // 仅处理 IPv4
            int ihl = (pkt[0] & 0xF) * 4;
            int totalLen = IpUtil.u16(pkt, 2);
            if (totalLen > len || totalLen <= ihl) { dlog("hp: bad totalLen=" + totalLen + " len=" + len + " ihl=" + ihl); return; }
            int frag = IpUtil.u16(pkt, 6);
            if ((frag & 0x1FFF) != 0) { dlog("hp: frag=0x" + Integer.toHexString(frag)); return; }     // 分片包：丢弃
            int proto = pkt[9] & 0xFF;
            if (DIAG) dlog("hp: len=" + len + " totalLen=" + totalLen + " proto=" + proto);

            if (proto == 6) {
                int off = ihl;
                if (off + 20 > totalLen) { dlog("hp: tcp off+20>totalLen"); return; }
                int srcPort = IpUtil.u16(pkt, off);
                int dstPort = IpUtil.u16(pkt, off + 2);
                long seq = IpUtil.u32(pkt, off + 4);
                long ack = IpUtil.u32(pkt, off + 8);
                int dataOff = ((pkt[off + 12] & 0xFF) >> 4) * 4;
                if (dataOff < 20) { dlog("hp: tcp dataOff=" + dataOff); return; }
                int flags = pkt[off + 13] & 0x3F;
                int window = IpUtil.u16(pkt, off + 14);
                int payloadLen = totalLen - ihl - dataOff;
                if (DIAG) dlog("hp: TCP " + IpUtil.ipToString(pkt, 12) + ":" + srcPort + ">"
                        + IpUtil.ipToString(pkt, 16) + ":" + dstPort
                        + " flags=0x" + Integer.toHexString(flags) + " payloadLen=" + payloadLen);
                byte[] payload = null;
                if (payloadLen > 0) {
                    payload = new byte[payloadLen];
                    System.arraycopy(pkt, ihl + dataOff, payload, 0, payloadLen);
                }
                boolean syn = (flags & IpUtil.SYN) != 0;
                boolean ackFlag = (flags & IpUtil.ACK) != 0;
                // 快路径：端口对直接映射，命中且 IP 核对得上就零分配拿到会话；
                // 只有未命中才走「IP 字符串化 + 拼 key + 查 map」的慢路径
                int idx = PortIdx.of(srcPort, dstPort);
                TcpSession s = tcpFastGet(idx, pkt);
                if (s == null) {
                    String src = IpUtil.ipToString(pkt, 12);
                    String dst = IpUtil.ipToString(pkt, 16);
                    String key = src + ":" + srcPort + ">" + dst + ":" + dstPort;
                    s = tcpSessions.get(key);
                    if (s != null) tcpFastSet(idx, s);
                }
                if (s == null) {
                    if (syn && !ackFlag) {
                        int mss = defaultMss();
                        int i = off + 20;
                        while (i + 1 < off + dataOff) {
                            int kind = pkt[i] & 0xFF;
                            if (kind == 0) break;
                            int optLen = (kind == 1) ? 1 : (pkt[i + 1] & 0xFF);
                            if (optLen <= 0) break;
                            if (kind == 2 && optLen == 4) mss = IpUtil.u16(pkt, i + 2);
                            i += optLen;
                        }
                        String src = IpUtil.ipToString(pkt, 12);
                        String dst = IpUtil.ipToString(pkt, 16);
                        final TcpSession ns = new TcpSession(this, src, srcPort, dst, dstPort,
                                src + ":" + srcPort + ">" + dst + ":" + dstPort, seq, mss);
                        tcpSessions.put(ns.key, ns);
                        tcpFastSet(idx, ns);
                        WeakNet.I.connTotal.incrementAndGet();
                        dlog("hp: NEW TCP SESSION " + ns.key + " total=" + WeakNet.I.connTotal.get());
                        // 必须走会话自己的串行队列：保证 start() 先于后续数据包执行，
                        // 否则首批数据包会在上游连好之前到达，被当成「上游未就绪」全部丢弃。
                        ns.submit(new Runnable() {
                            public void run() {
                                ns.start();
                                if (ns.isClosed()) removeTcp(ns);
                            }
                        });
                    } else {
                        // 未知会话：回 RST
                        if (DIAG) dlog("hp: RST unknown " + IpUtil.ipToString(pkt, 12) + ":" + srcPort + ">"
                                + IpUtil.ipToString(pkt, 16) + ":" + dstPort
                                + " flags=0x" + Integer.toHexString(flags));
                        String src = IpUtil.ipToString(pkt, 12);
                        String dst = IpUtil.ipToString(pkt, 16);
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
                // 走会话自己的串行队列，保证上行数据包严格按到达顺序进入 TCP 状态机
                // （放全局并发线程池会乱序，导致绝大多数包被误判成重传而丢弃）
                fs.submit(new Runnable() {
                    public void run() {
                        fs.onClientPacket(ff, fseq, lack, fp, fpl, fw);
                        if (fs.isClosed() || (ff & IpUtil.RST) != 0) removeTcp(fs);
                    }
                });
            } else if (proto == 17) {
                int off = ihl;
                if (off + 8 > totalLen) { dlog("hp: udp off+8>totalLen"); return; }
                int srcPort = IpUtil.u16(pkt, off);
                int dstPort = IpUtil.u16(pkt, off + 2);
                int ulen = IpUtil.u16(pkt, off + 4);
                int payloadLen = ulen - 8;
                if (payloadLen < 0 || off + ulen > totalLen) return;
                byte[] payload = new byte[payloadLen];
                System.arraycopy(pkt, off + 8, payload, 0, payloadLen);
                int idx = PortIdx.of(srcPort, dstPort);
                UdpSession us = udpFastGet(idx);
                if (us == null || !us.sameFlow(pkt)) {
                    String src = IpUtil.ipToString(pkt, 12);
                    String dst = IpUtil.ipToString(pkt, 16);
                    String key = src + ":" + srcPort + ">" + dst + ":" + dstPort;
                    us = udpSessions.get(key);
                    if (us == null) {
                        UdpSession nus = new UdpSession(this, src, srcPort, dst, dstPort, key);
                        UdpSession exist = udpSessions.putIfAbsent(key, nus);
                        us = exist != null ? exist : nus;
                        if (exist == null) WeakNet.I.connTotal.incrementAndGet();
                    }
                    udpFastSet(idx, us);
                }
                final UdpSession fus = us;
                final byte[] fp = payload;
                // 每个包都往共享池扔一个任务，在 DNS/QUIC 这种高包率下会让 cached 池
                // 反复建销线程，而且同一会话的包顺序没有保证。改走会话自己的串行队列：
                // 线程数按会话算（空闲 30s 回收），顺序也与到达顺序一致。
                fus.submit(new Runnable() {
                    public void run() {
                        fus.send(fp, fp.length);
                    }
                });
            }
        } catch (Exception e) {
            dlog("hp: EXC " + e);
            // ignore
        }
    }

    /**
     * 写回 tun —— 下行流量的唯一出口，限速在这里统一执行，
     * 保证送给 App 的每一个字节都过闸（包括 TCP/UDP/控制包）。
     *
     * 限速仍在**入队前**同步执行（语义不变：不是「先排队再限速」）；
     * 真正的 fd 写在单写线程里串行完成，调用方只花一次入队。
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
        if (DIAG) {
            dlog("write: " + packet.length + "B proto=" + (packet[9] & 0xFF)
                    + " dst=" + IpUtil.ipToString(packet, 16)
                    + ":" + IpUtil.u16(packet, ihlOf(packet) + 2));
        }
        // 入队；队列满则阻塞（保留背压）。写线程若已退出则退回直写兜底，
        // 避免停止过程中把 RST/FIN 这类控制包丢掉。
        while (writerRunning) {
            try {
                if (outQ.offer(packet, 200)) return;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }
        }
        writePacket(packet);          // 兜底直写也走唯一写入点，不会与写线程交错写 fd
    }

    /**
     * tun fd 的唯一写入点。
     *
     * 常态只有单写线程调用，`synchronized` 无竞争（偏向锁，成本可忽略）；
     * 保留这把锁是为了停止窗口：`stopWriter()` 把 writerRunning 置 false 后，
     * 旧写线程可能还在写，此时 `write()` 会走兜底直写 —— 两者若不共用一把锁，
     * 就有交错写同一个 FileOutputStream 的窄竞态（2026-11-13 核查发现）。
     */
    private boolean writePacket(byte[] p) {
        synchronized (writeLock) {
            try {
                if (tunOut == null) return false;
                tunOut.write(p);
                return true;
            } catch (Exception e) {
                dlog("write: EXC " + e);
                return false;
            }
        }
    }

    private void startWriter() {
        writerRunning = true;
        Thread t = new Thread(new Runnable() {
            public void run() {
                // 与 tun-read 同一优先级（nice -8），见 readThread 处注释
                Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY);
                writeLoop();
            }
        }, "tun-write");
        t.setDaemon(true);
        writerThread = t;
        t.start();
    }

    private void stopWriter() {
        writerRunning = false;
        Thread t = writerThread;
        writerThread = null;
        if (t != null) {
            t.interrupt();
            // 等写线程把在途控制包收尾再清队列；join 有上界，不会卡住 stopVpn()
            try {
                t.join(500);
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
            }
        }
        outQ.clear();
    }

    /** 单写线程：FIFO 出队 → 一次 fd 写。顺序即入队顺序，与旧锁序等价。 */
    private void writeLoop() {
        // 身份校验：重建通道时 startWriter 会起新线程，旧线程可能还没退出。
        // 只靠 writerRunning 的话，旧线程会在标志重新置 true 后「复活」，
        // 与写新 tun 的线程一起消费同一个队列 —— 顺序会乱。
        Thread me = Thread.currentThread();
        while (writerRunning && writerThread == me) {
            byte[] p;
            try {
                p = outQ.poll(200);
            } catch (InterruptedException e) {
                break;
            }
            if (p == null) continue;
            if (!writePacket(p) && !writerRunning) break;
        }
        // 退出前把剩余控制包尽量写完（tunOut 已关时立刻失败退出）
        if (writerThread != me && writerThread != null) return;   // 已被新线程接管：不要抢写
        byte[] p;
        while ((p = outQ.poll()) != null) {
            if (!writePacket(p)) break;
        }
    }

    private static int ihlOf(byte[] p) { return (p[0] & 0xF) * 4; }

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

    /**
     * 延迟关闭会话（黑洞模拟：SYN 后不响应，60s 后再清）。
     *
     * 旧实现在共享池里 `Thread.sleep(60000)`：每关一条连接就白占一个池线程一分钟，
     * 黑洞场景下几十条 SYN 就能堆出几十个「只睡觉」的线程。改用调度器后不占线程。
     */
    public void scheduleRemove(final String key, final TcpSession s, long delayMs) {
        ScheduledExecutorService sc = sched;
        if (sc == null || sc.isShutdown()) return;
        try {
            sc.schedule(new Runnable() {
                public void run() {
                    s.close();
                    tcpSessions.remove(key);
                }
            }, Math.max(0, delayMs), TimeUnit.MILLISECONDS);
        } catch (Exception e) {
            // 服务正在停止：调度器已关闭，丢弃即可
        }
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
