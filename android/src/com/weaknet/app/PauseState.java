package com.weaknet.app;

/**
 * 暂停现场：标志位 + 组合位点 + 场景快照。
 *
 * 历史上暂停相关的三个 bug（标志残留 / 暂停中切场景静默限速 / 组合线程竞态）
 * 都出在这几项状态的存取上。此前 _jw 测试自己实现了一套影子逻辑，WeakNet 改了
 * 真状态机测试却不会失败。现在把现场存取抽成这个纯 JDK 类，WeakNet.pause/resume
 * 与 _PauseResumeTest 共用同一份实现。
 *
 * 刻意不引用 Profile / org.json —— android.jar 里的 org.json 是 Stub，纯 JVM
 * 跑不起来。快照以 Object 保存：WeakNet 传入前自己 copy()，取回时自己转型。
 */
public final class PauseState {
    private volatile boolean paused = false;
    private volatile String comboId = null;
    private volatile int comboStep = -1;
    private volatile long comboRemain = 0;
    private volatile Object snapshot = null;

    /** 一次暂停留下的现场（取回时不可变） */
    public static final class Stored {
        public final String comboId;
        public final int step;
        public final long remain;
        public final Object snapshot;

        Stored(String comboId, int step, long remain, Object snapshot) {
            this.comboId = comboId;
            this.step = step;
            this.remain = remain;
            this.snapshot = snapshot;
        }
    }

    /**
     * 组合某一段「恢复后实际还要等多久」——暂停语义的唯一换算点，纯函数。
     *
     * 参数统一用**剩余时长**：pause() 存的是 comboDeadline - now（还剩多久），
     * 恢复时原样传进来即可。历史 bug 是这里按「已走过」算（full - skip），
     * 30s 的段在第 12s 暂停，恢复只等 12s 就跳段了。
     *
     * remain &lt;= 0 视为「没有暂停现场」（整段从头走）：从没暂停过时调用方
     * 传 0，语义必须是整段，而不是 0 毫秒直接跳段。
     * remain &gt; full（时钟回拨 / 现场被写坏）夹到 full，绝不把段拉长。
     *
     * 生产（WeakNet.runCombo）与 _PauseResumeTest 共用这一份实现。
     */
    public static long remainWait(long remain, long full) {
        if (full <= 0) return 0;
        if (remain <= 0) return full;
        return remain > full ? full : remain;
    }

    public synchronized boolean paused() {
        return paused;
    }

    /** 只读看一眼现场（暂停中才非空），供界面显示暂停进度用。 */
    public synchronized Stored peek() {
        return paused ? new Stored(comboId, comboStep, comboRemain, snapshot) : null;
    }

    /** 进入暂停：记现场并置标志。调用方须确保此时还没有参数要落地。 */
    public synchronized void enter(String comboId, int step, long remain, Object snapshot) {
        this.comboId = comboId;
        this.comboStep = step;
        this.comboRemain = Math.max(0L, remain);
        this.snapshot = snapshot;
        this.paused = true;
    }

    /** 取出并清空现场；不在暂停态返回 null。 */
    public synchronized Stored take() {
        if (!paused) return null;
        Stored s = new Stored(comboId, comboStep, comboRemain, snapshot);
        clearLocked();
        return s;
    }

    /** 总开关一动 / 新参数落地：暂停快照连同标志一起清掉，杜绝标志残留。 */
    public synchronized void clear() {
        clearLocked();
    }

    /**
     * 暂停中切了单支场景：保持暂停，参数绝不落地，只替换「恢复时该施加哪支」。
     * 这里若去 setProfile，用户以为还暂停着，流量却已被悄悄限速。
     */
    public synchronized void whilePaused(Object snapshot) {
        if (!paused) return;
        this.comboId = null;
        this.comboStep = -1;
        this.comboRemain = 0L;
        this.snapshot = snapshot;
    }

    /** 暂停中切了组合：保持暂停，恢复时从第 1 段重新播。 */
    public synchronized void whilePaused(String comboId, int step, long remain) {
        if (!paused) return;
        this.comboId = comboId;
        this.comboStep = step;
        this.comboRemain = Math.max(0L, remain);
        this.snapshot = null;
    }

    /**
     * 组合线程唯一准入判据：key 仍是当前场景、总开关仍开、且未暂停。
     * 三项放在同一把锁里判断，避免「校验完又被切场景/暂停」的窗口。
     */
    public synchronized boolean comboAllowed(String key, String presetKey, boolean limitOn) {
        return !paused && limitOn && key != null && key.equals(presetKey);
    }

    private void clearLocked() {
        paused = false;
        comboId = null;
        comboStep = -1;
        comboRemain = 0L;
        snapshot = null;
    }
}
