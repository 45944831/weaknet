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

    // ---- 暂停 ----
    //
    // 暂停 ≠ 关闭限速：关闭是把流量放回直连、场景仍留着；
    // 暂停是「这段先按住别动」，恢复后要接着刚才的位置继续 ——
    // 组合播到第 3 段暂停，恢复应该还是第 3 段，而不是从头再播一遍。
    // 所以暂停时要记住当时播到哪、还剩多久。
    public volatile boolean paused = false;
    /** 暂停时正在播的组合 id（null = 当时播的是单支场景） */
    private volatile String pausedComboId = null;
    /** 暂停时播到的段序号（0 起） */
    private volatile int pausedComboStep = -1;
    /** 暂停时这一段还剩多少毫秒 */
    private volatile long pausedComboRemain = 0;
    /** 暂停时正在生效的那支场景的参数快照 */
    private volatile Profile pausedProfile = null;

    public final AtomicInteger connTotal = new AtomicInteger();
    public final AtomicInteger connActive = new AtomicInteger();
    public final AtomicLong bytesUp = new AtomicLong();
    public final AtomicLong bytesDown = new AtomicLong();
    /** UDP 延迟队列满导致丢弃的包数：供事件日志观察，不参与 /api 契约 */
    public final AtomicLong udpDropped = new AtomicLong();

    public final List<String[]> events = Collections.synchronizedList(new ArrayList<String[]>());
    public final Set<Object> active = Collections.synchronizedSet(new HashSet<Object>());

    public long startedAt = System.currentTimeMillis();

    /** 整机共享的上/下行令牌桶（手机只有一条无线链路，所有连接共享带宽） */
    public volatile Pipe.Bucket upBucket = new Pipe.Bucket(0);
    public volatile Pipe.Bucket downBucket = new Pipe.Bucket(0);

    /** 切换场景时回调（VPN 模式用它让已有连接立即按新参数重建） */
    public static volatile Runnable onProfileChange = null;

    /**
     * 界面即时刷新回调（飘窗、场景面板等），回调统一在主线程执行。
     *
     * 与 {@link #onProfileChange} 的区别：那是单槽、给引擎用的（切换后重建已有连接）；
     * 这里可以多注册、只负责重画界面，且允许界面自己判断要不要重画。
     */
    private final java.util.List<Runnable> uiWatchers = new java.util.ArrayList<Runnable>();
    /**
     * 主线程 Handler：界面刷新必须回到主线程做。
     *
     * 惰性创建，别写成字段初始化 —— android.os.Looper.getMainLooper() 在
     * 单元测试（JVM、无 Android 运行时）里会抛 RuntimeException: Stub!，
     * 而 WeakNet.I 是静态单例，静态初始化就崩会把整个类判死，
     * 连不碰 Android 的纯逻辑单测也一起跑不了。
     */
    private volatile android.os.Handler uiHandler;

    private android.os.Handler uiHandler() {
        android.os.Handler h = uiHandler;
        if (h == null) {
            synchronized (this) {
                h = uiHandler;
                if (h == null) {
                    h = new android.os.Handler(android.os.Looper.getMainLooper());
                    uiHandler = h;
                }
            }
        }
        return h;
    }

    /** 注册界面刷新回调；重复注册同一个对象不会生效两次 */
    public void addUiWatcher(Runnable r) {
        if (r == null) return;
        synchronized (uiWatchers) {
            if (!uiWatchers.contains(r)) uiWatchers.add(r);
        }
    }

    public void removeUiWatcher(Runnable r) {
        if (r == null) return;
        synchronized (uiWatchers) {
            uiWatchers.remove(r);
        }
    }

    /**
     * 参数 / 开关 / 断网状态一变，立刻让界面重画一次。
     *
     * 界面本来每 500ms 轮询，可「点一下场景」这种操作用户眼睛就盯着飘窗，
     * 慢半拍会让人以为没切成功，所以这里再推一次即时刷新。
     */
    public void fireUiChange() {
        android.os.Handler h;
        try {
            h = uiHandler();
        } catch (Throwable t) {
            // 没有 Android 运行时（单元测试 / 早期启动）：没人等界面刷新，直接跳过
            return;
        }
        h.post(new Runnable() {
            public void run() {
                synchronized (uiWatchers) {
                    for (int i = 0; i < uiWatchers.size(); i++) {
                        try {
                            uiWatchers.get(i).run();
                        } catch (Exception e) {
                            // 一个界面重画出错，不能拖垮其他回调
                        }
                    }
                }
            }
        });
    }

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
        // 同时输出到 logcat：事件日志在 App 里能看，但排查「连接建上了数据却没过去」
        // 这类问题时，adb logcat 是唯一能实时观察的窗口
        try {
            android.util.Log.d("WeakNet", type + " | " + msg);
        } catch (Exception e) {
            // ignore
        }
    }

    public synchronized void setProfile(Profile p, String key) {
        profile = p;
        presetKey = key;
        gateOpen = true;
        // 这里刻意不动 limitOn：限速与否只由总开关（setLimitOn）决定。
        // 早期版本在这里按「场景有没有限制」推断开关，结果是点一下场景按钮就等于
        // 静默开始了弱网测试——用户要的是「选好场景，点开始才生效」。
        if (p.hasLimits()) lastProfile = p.copy();   // 记住最后一次生效的场景，供「再次开启」复原
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
        fireUiChange();                 // 参数已落地，界面立刻跟上（不等 500ms 轮询）
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
                            fireUiChange();        // 飘窗上的「· 断网中」立刻出现
                            abortAll();
                            Thread.sleep(profile.flapBlackoutSec * 1000L);
                            gateOpen = true;
                            fireUiChange();        // 恢复也立刻刷掉「· 断网中」
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
     * 只「选中」一支场景，不启动弱网。
     *
     * 没在点总开关之前，引擎参数保持原样（否则选一下场景流量就被限住了，等于偷偷
     * 开始了测试）：这里只记下 presetKey，等用户点「▶ 开始弱网测试」时由
     * applyLimitOn() 按这个 key 复原出场景再施加。已经在测试中时，切场景照旧立即生效。
     */
    /**
     * 只「选中」一支场景，不启动弱网。
     *
     * 没在点总开关之前，引擎参数保持原样（否则选一下场景流量就被限住了，等于偷偷
     * 开始了测试）：这里只记下 presetKey，等用户点「▶ 开始弱网测试」时由
     * applyLimitOn() 按这个 key 复原出场景再施加。已经在测试中时，切场景照旧立即生效。
     *
     * 这里会掐掉正在播放的组合：点内置/自定义场景＝「我不要按序列走了，就用这支」，
     * 留着组合线程继续跑会反过来把刚点的场景又覆盖掉。
     */
    public synchronized void selectScene(Profile p, String key) {
        stopComboLocked();
        presetKey = key;
        if (p != null && p.hasLimits()) lastProfile = p.copy();
        if (!limitOn) {
            // 没开启时参数不会落地，但「已选中哪一支」变了，飘窗标题要立刻改过来
            fireUiChange();
            return;
        }
        setProfile(p, key);             // 里面也会 fire 一次
        limitOn = true;             // setProfile 不再推断开关，测试中切场景要保持开着
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
            stopComboLocked();        // 停止测试当然也不该留着组合继续按序列改参数
            setProfile(new Profile(), presetKey);
        }
        limitOn = on;      // 开关只认这里：自定义场景哪怕全零，用户点开了就算「已开启」
        fireUiChange();    // setProfile 那次 fire 时开关还是旧值，这里按最终状态再推一次
    }

    // ------------------------------------------------------------------ 暂停 / 恢复
    //
    // 暂停与「关闭限速」的区别：关闭是结束这次测试、场景只留着备查；
    // 暂停是「先按住」，恢复后要接着刚才的位置继续 —— 组合播到第 3 段暂停，
    // 恢复时应该还是第 3 段且那段的剩余时长接着走，而不是从头再播一遍。
    //
    // 暂停期间流量要真的放回直连（这是「暂停」的意义：让它缓一会儿），
    // 所以参数会被撤掉；恢复时再按记住的位置施加回去。

    /**
     * 暂停当前测试。返回 true = 确实暂停了。
     *
     * 没在测试（limitOn=false）时按暂停没有意义：这时本来就没在限速，
     * 直接返回 false，让界面把按钮显示成「未生效」而不是「已暂停」。
     */
    public synchronized boolean pause() {
        if (paused) return false;
        if (!limitOn) return false;
        // 记住组合播到哪一、还剩多久
        pausedComboId = comboId;
        pausedComboStep = comboStep;
        if (comboId != null && comboDeadline > 0) {
            pausedComboRemain = Math.max(0, comboDeadline - System.currentTimeMillis());
        } else {
            pausedComboRemain = 0;
        }
        pausedProfile = profile.copy();
        stopComboLocked();               // 组合线程先停，别让它在暂停期间继续改参数
        setProfile(new Profile(), presetKey);   // 参数撤掉 → 流量回到直连
        paused = true;
        log("config", "已暂停当前测试（恢复后接着刚才的位置继续）");
        fireUiChange();
        return true;
    }

    /**
     * 恢复被暂停的测试。返回 true = 确实恢复了。
     *
     * 恢复时按暂停前记住的位置接着走：组合回到当时那一段，剩余时长接着倒；
     * 单支场景则把暂停前的参数原样施加回去。
     */
    public synchronized boolean resume() {
        if (!paused) return false;
        paused = false;
        String cid = pausedComboId;
        int step = pausedComboStep;
        long remain = pausedComboRemain;
        Profile snap = pausedProfile;
        pausedComboId = null;
        pausedComboStep = -1;
        pausedComboRemain = 0;
        pausedProfile = null;
        limitOn = true;
        if (cid != null) {
            MyCombos.Item c = MyCombos.find(cid);
            if (c != null) {
                startComboLocked(c, step, remain);   // 接着当时那一段继续
                fireUiChange();
                return true;
            }
            // 组合已被删掉：退回单支场景恢复，至少别让用户卡在「暂停中」
            log("config", "暂停的组合已被删除，按单支场景恢复");
        }
        setProfile(snap != null ? snap : new Profile(), presetKey);
        limitOn = true;
        fireUiChange();
        return true;
    }

    /** 暂停 / 恢复的切换入口：飘窗上的按钮走这里 */
    public synchronized boolean togglePause() {
        return paused ? resume() : pause();
    }

    /** 暂停时正在生效的那支场景名（暂停期间参数已撤掉，profile 里只剩直连） */
    public String pausedSceneName() {
        Profile p = pausedProfile;
        return (p != null && p.name != null) ? p.name : null;
    }

    /**
     * 暂停期间的播放进度文案（如「组合名 2/5」）。
     *
     * 暂停时组合线程已经停了，comboProgress() 会返回空 —— 但用户恰恰需要在这时
     * 知道自己按住的是哪一支的第几段，所以这里用暂停时记下的信息还原。
     */
    public String pausedProgress() {
        String cid = pausedComboId;
        if (cid == null) return null;
        MyCombos.Item c = MyCombos.find(cid);
        String nm = c != null ? c.name : "组合";
        int step = pausedComboStep;
        int total = 0;
        try {
            if (c != null && c.steps != null) total = c.steps.size();
        } catch (Exception e) {
            total = 0;
        }
        if (total <= 0) return nm;
        int cur = step + 1;
        if (cur < 1) cur = 1;
        if (cur > total) cur = total;
        return nm + " " + cur + "/" + total;
    }

    // ------------------------------------------------------------- 弱网组合播放
    //
    // 一支组合 = 几支场景 + 每段时长，选中后按顺序自动推进（第一段 → 到点切第二段 → …）。
    // 用一个独立线程走 --steps-- 线性推进，而不是挂在计费式定时器上：
    // 段与段之间就是单纯的等待，开线程读起来最直观，也方便随时被掐掉。

    private Thread comboThread;
    /** 正在播放的组合 id；只是「选中」并不算，真正开始了才非空 */
    public volatile String comboId = null;
    /** 播放中的组合名：飘窗标题每 500ms 都要显示它，缓存下来免得成了定时读存储 */
    public volatile String comboName = null;
    public volatile int comboStep = -1;        // 当前第几段（0 起），-1 = 还没进入第一段
    public volatile int comboTotal = 0;        // 这一支总共几段（供界面显示 2/5）
    public volatile long comboDeadline = 0;    // 本段到点的时刻（界面倒计时用），0 = 无

    public static String comboKey(String cid) {
        return MyCombos.KEY_PREFIX + cid;
    }

    /**
     * 选中一支组合：弱网已在测试中就从第一段开始播；没开启只记下来。
     *
     * 与 {@link #selectScene} 同一条规矩 —— 没点过「开始」之前绝不替用户把流量限住，
     * 否则点一下组合就等于偷偷开始了测试。
     *
     * @return true = 已开始播放
     */
    public synchronized boolean selectCombo(MyCombos.Item c) {
        if (c == null || c.steps == null || c.steps.isEmpty()) return false;
        stopComboLocked();
        presetKey = comboKey(c.id);
        if (!limitOn) {
            fireUiChange();          // 「当前选中的是这支」要立刻反映到飘窗标题
            return false;
        }
        startComboLocked(c.copy());  // 播放必须用快照：存档随时可能被删改
        return true;
    }

    /** 停止播放：换场景、关限速、删组合都会走到这里 */
    public synchronized void stopCombo() {
        stopComboLocked();
    }

    /**
     * presetKey 指向一支组合时用它启动播放。
     *
     * 给「点开始弱网测试」「API /api/run enable」这类恢复链路用：它们按 presetKey
     * 复原场景，而组合不是一支静态场景，得专门把序列拉起来。
     *
     * @return true = 已按组合处理（调用方不必再挑静态 profile）
     */
    public synchronized boolean resumeIfCombo() {
        String k = presetKey;
        if (k == null || !k.startsWith(MyCombos.KEY_PREFIX)) return false;
        MyCombos.Item c = MyCombos.find(k.substring(MyCombos.KEY_PREFIX.length()));
        if (c == null) {
            presetKey = "direct";    // 组合已被删除：别留一个解析不出来的 key
            return false;
        }
        return selectCombo(c);
    }

    /** 界面上的一行：「组合名 2/5」。没在播放返回 null */
    public String comboProgress() {
        String id = comboId;
        if (id == null || comboTotal <= 0) return null;
        String nm = comboName != null && comboName.length() > 0 ? comboName : "组合";
        int cur = comboStep + 1;
        if (cur < 1) cur = 1;
        if (cur > comboTotal) cur = comboTotal;
        return nm + " " + cur + "/" + comboTotal;
    }

    private void startComboLocked(final MyCombos.Item c) {
        startComboLocked(c, -1, 0L);
    }

    /**
     * 从指定段开始播。
     *
     * @param startStep 起步段（0 起）；-1 = 从头播
     * @param firstRemain 起步段已经走过的时间（毫秒）；0 = 这段完整地开始
     *   暂停恢复时用它接着倒计时，而不是把这段时间白送掉
     */
    private void startComboLocked(final MyCombos.Item c, int startStep, long firstRemain) {
        final String key = comboKey(c.id);
        comboId = c.id;
        comboName = c.name;
        comboTotal = c.steps.size();
        comboStep = startStep - 1;
        comboDeadline = 0;
        comboThread = new Thread(new Runnable() {
            public void run() {
                runCombo(c, key, startStep, firstRemain);
            }
        }, "combo");
        comboThread.setDaemon(true);
        comboThread.start();
        log("config", (startStep > 0 ? "继续播放组合「" : "开始播放组合「")
                + c.name + "」：" + summaryOf(c));
    }

    private void stopComboLocked() {
        Thread t = comboThread;
        comboThread = null;
        comboId = null;
        comboName = null;
        comboStep = -1;
        comboTotal = 0;
        comboDeadline = 0;
        if (t != null) t.interrupt();   // 只 interrupt 不 join：持有锁时等线程收尾容易卡死界面
    }

    private void runCombo(MyCombos.Item c, String key, int startStep, long firstRemain) {
        try {
            int i = startStep > 0 ? startStep : 0;
            long skip = firstRemain;             // 起步段已经走过的时间
            while (true) {
                for (; i < c.steps.size(); i++) {
                    MyCombos.Step st = c.steps.get(i);
                    long waitMs = 0;              // 这一段实际要等多久（下面在锁里算）
                    // 「还是我在播吗」和「把这一段的参数落下去」必须在同一把锁里完成。
                    // 分两步做的话有个窗口：用户点别的场景时 selectScene 已改完 presetKey，
                    // 这里却还按旧步骤把参数覆盖回去，用户点的那支被静默顶掉。
                    // selectScene / setLimitOn 都上这把锁，所以进来再校验一次是可靠的。
                    synchronized (this) {
                        if (!key.equals(presetKey) || !limitOn) return;
                        Profile p = profileOfStep(st.key);
                        if (p == null) {
                            // 这段的场景被删了（自定义场景常见）：跳过它，别把整支带崩
                            log("config", "组合「" + c.name + "」第 " + (i + 1)
                                    + " 段的场景已不存在，跳过");
                            continue;
                        }
                        comboStep = i;
                        long full = ComboPlan.stepMs(st);
                        waitMs = Math.max(0, full - skip);
                        skip = 0;                // 只有起步段要扣，往后的段都是完整时长
                        comboDeadline = System.currentTimeMillis() + waitMs;
                        // 直接 setProfile，不走 selectScene：那是「手动切场景」的入口，
                        // 会把正在跑的自己给停掉
                        setProfile(p, key);
                        limitOn = true;
                    }
                    if (!sleepOwning(waitMs, key)) return;
                    if (MyCombos.find(c.id) == null) {
                        log("config", "组合「" + c.name + "」已被删除，停止播放");
                        return;
                    }
                }
                int n = ComboPlan.nextIndex(c.steps.size() - 1, c.steps.size(), c.loop);
                if (n < 0) {
                    comboDeadline = 0;     // 播完了：界面上的倒计时收掉
                    log("config", "组合「" + c.name + "」播放完毕，停在最后一段");
                    return;
                }
                i = n;
            }
        } catch (Exception e) {
            log("error", "组合播放异常：" + e);
        } finally {
            // 播完 / 被打断都要把「正在播放」收起来。若是被新的播放取代了，
            // comboThread 已经指到别人身上，这里就不能再动那些字段
            if (comboThread == Thread.currentThread()) {
                comboThread = null;
                comboId = null;
                comboName = null;
                comboStep = -1;
                comboTotal = 0;
                comboDeadline = 0;
            }
            fireUiChange();
        }
    }

    /**
     * 组合自己的等待：睡着期间被手动切场景 / 关限速就立刻退出。
     *
     * 不用 {@link #sleepInterruptible}：那个按 profileGen 判断，而每段开始都会
     * setProfile 把 gen +1，用它的话头一段就会被自己唤醒，段与段直接跳成一团。
     *
     * @return true = 睡满了；false = 被抢占，调用方应直接收工
     */
    private boolean sleepOwning(long ms, String key) {
        long end = System.currentTimeMillis() + Math.max(0, ms);
        while (true) {
            if (!key.equals(presetKey)) return false;   // 用户点了别的场景
            if (!limitOn) return false;                 // 关了总开关
            long left = end - System.currentTimeMillis();
            if (left <= 0) return true;
            try {
                Thread.sleep(Math.min(200, left));
            } catch (InterruptedException e) {
                return false;
            }
        }
    }

    /** 组合的一行摘要：5G 0:30 → 4G 弱网 1:00（循环） */
    public static String summaryOf(MyCombos.Item c) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < c.steps.size(); i++) {
            if (i > 0) sb.append(" → ");
            MyCombos.Step s = c.steps.get(i);
            sb.append(nameOfStepKey(s.key)).append(" ").append(ComboPlan.stepText(s));
        }
        if (c.loop) sb.append("（循环）");
        return sb.toString();
    }

    /** 段里的 key → 显示名：内置预设取短名，自定义场景取存档名字 */
    public static String nameOfStepKey(String key) {
        if (key == null || key.length() == 0) return "?";
        if (key.startsWith("u:")) {
            MyPresets.Item it = MyPresets.find(key.substring(2));
            return it != null ? it.name : "已删除的自定义场景";
        }
        String s = Profile.shortOf(key);
        if (s != null && s.length() > 0) return s;
        try {
            Profile p = Profile.build(key);
            if (p != null && p.name != null && p.name.length() > 0) return p.name;
        } catch (Exception e) {
            // 落到 key 本身
        }
        return key;
    }

    /**
     * 段里的 key → 实际施加的 Profile；场景已被删除返回 null。
     *
     * 自定义场景必须拷一份：{@link Profile} 应用时会被原地改动（name 之类），
     * 直接拿存档里的实例会把存档改坏。
     */
    public static Profile profileOfStep(String key) {
        if (key == null || key.length() == 0) return null;
        if (key.startsWith("u:")) {
            MyPresets.Item it = MyPresets.find(key.substring(2));
            return it == null ? null : it.profile.copy();
        }
        for (String k : Profile.KEYS) {
            if (k.equals(key)) {
                try {
                    return Profile.build(k);
                } catch (Exception e) {
                    return null;
                }
            }
        }
        return null;
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
