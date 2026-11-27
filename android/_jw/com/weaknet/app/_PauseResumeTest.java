package com.weaknet.app;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;

/**
 * 悬浮窗「暂停 / 恢复」的等价断言。
 *
 * WeakNet 整体依赖 Android（Handler/Looper/Context），PC 上跑不起来；
 * 暂停语义里最容易出错的两块现已抽到 src 的纯 JDK 类，WeakNet 与本测试共用
 * 同一份实现（测试不再自己写一套影子逻辑 —— 历史上 WeakNet 改了真状态机、
 * 测试却照样全绿，就是因为两边各算各的）：
 *
 *   - PauseState.remainWait：本段「恢复后还要等多久」，参数统一为剩余时长；
 *   - PauseState 状态机：标志 / 现场 / 准入判据。
 *
 *   1. 30s 的段在第 12s 暂停 → 恢复只等 18s（不是 12s，更不是再等 30s）
 *   2. 第 29s 暂停 → 只等 1s
 *   3. remain > full 被夹到 full；没有现场（0）按整段走
 *   4. 暂停期间不推进段（时间到了也不切段）
 *   5. 三个历史 bug：标志残留 / 暂停中切场景静默限速 / 组合线程竞态
 */
public class _PauseResumeTest {

    static int ok = 0;
    static int bad = 0;

    static void check(String name, boolean cond, Object extra) {
        if (cond) {
            ok++;
            System.out.println("  OK   " + name);
        } else {
            bad++;
            System.out.println("  FAIL " + name + (extra == null ? "" : "  <- " + extra));
        }
    }

    /** 读被测源码，确保 WeakNet 真的调用了共用的纯函数，而不是测试自说自话 */
    static String readSource(String name) {
        String rel = "src/com/weaknet/app/" + name;
        String[] paths = {rel, "../" + rel, "../android/" + rel, "android/" + rel};
        for (String p : paths) {
            File f = new File(p);
            if (!f.isFile()) continue;
            FileInputStream in = null;
            try {
                in = new FileInputStream(f);
                ByteArrayOutputStream bo = new ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
                return new String(bo.toByteArray(), "UTF-8");
            } catch (IOException e) {
                return "";
            } finally {
                if (in != null) try { in.close(); } catch (IOException ignore) { }
            }
        }
        return "";
    }

    static long ms(int minutes, int seconds) {
        return ComboPlan.clamp(minutes, ComboPlan.MAX_MINUTES) * 60000L
                + ComboPlan.clamp(seconds, ComboPlan.MAX_SECONDS) * 1000L;
    }

    public static void main(String[] args) {
        final long FULL = ms(0, 30);           // 这一段整段 30 秒

        System.out.println("== 1. 30s 段第 12s 暂停 → 恢复只等 18s ==");
        // pause() 存的是 comboDeadline - now，即「还剩 18s」；resume 原样传回
        long remain = FULL - 12000L;
        long wait = PauseState.remainWait(remain, FULL);
        check("剩余 18000ms 原样生效", wait == 18000L, wait);
        check("不会只等 12s（旧 bug：把剩余当已走）", wait != 12000L, wait);
        check("也不会从头再等 30s", wait != FULL, wait);

        System.out.println("== 2. 第 29s 暂停 → 只等 1s ==");
        check("剩余 1000ms 原样生效",
                PauseState.remainWait(FULL - 29000L, FULL) == 1000L,
                PauseState.remainWait(FULL - 29000L, FULL));

        System.out.println("== 3. 夹取边界 ==");
        check("remain > full 被夹到 full",
                PauseState.remainWait(FULL + 5000L, FULL) == FULL,
                PauseState.remainWait(FULL + 5000L, FULL));
        check("remain = 0（没有暂停现场）按整段走",
                PauseState.remainWait(0L, FULL) == FULL,
                PauseState.remainWait(0L, FULL));
        check("负数按整段走",
                PauseState.remainWait(-1L, FULL) == FULL,
                PauseState.remainWait(-1L, FULL));
        check("段长为 0 时不产生等待",
                PauseState.remainWait(5000L, 0L) == 0L,
                PauseState.remainWait(5000L, 0L));

        System.out.println("== 4. 恢复后接着下一段走 ==");
        int nxt = ComboPlan.nextIndex(2, 5, false);      // 第 3 段（0 起 2）走完
        check("走完这段接着第 4 段", nxt == 3, nxt);
        check("循环组合回到第 1 段", ComboPlan.nextIndex(4, 5, true) == 0, null);
        check("非循环组合到此结束", ComboPlan.nextIndex(4, 5, false) == -1, null);
        check("没有现场时从第 1 段开始", ComboPlan.nextIndex(-1, 5, false) == 0, null);

        System.out.println("== 5. 暂停不该改段长规则 ==");
        check("整段时长仍是 30 秒", ms(0, 30) == 30000L, ms(0, 30));
        check("分秒换算正确", ms(1, 30) == 90000L, ms(1, 30));
        check("秒上限被夹住", ms(0, 99) == 59000L, ms(0, 99));

        System.out.println("== 6. 历史 bug 回归：WeakNet 与测试共用 PauseState ==");
        // bug1：总开关一动，暂停标志必须清掉，不能「显示暂停、实际在限速」
        PauseState ps = new PauseState();
        ps.enter("c1", 2, 18000L, "4G");
        check("进入暂停后标志置位", ps.paused(), null);
        ps.clear();                         // 对应 setLimitOn / setProfile
        check("标志不残留", !ps.paused() && ps.peek() == null, null);
        check("清空后 take 返回 null", ps.take() == null, null);

        // bug2：暂停中切场景保持暂停、只换快照，绝不落地参数
        ps.enter("c1", 2, 18000L, "4G");
        ps.whilePaused("5G");
        PauseState.Stored s2 = ps.peek();
        check("暂停中切场景仍暂停", ps.paused() && "5G".equals(s2.snapshot), s2.snapshot);
        check("切场景后组合位点清掉", s2.comboId == null && s2.step == -1, s2.comboId);
        check("暂停中组合线程不准落地", !ps.comboAllowed("c:c1", "c:c1", true), null);
        PauseState.Stored taken = ps.take();
        check("恢复时拿到新场景快照", taken != null && "5G".equals(taken.snapshot), null);

        // bug3：组合线程竞态 —— key / 总开关 / 暂停 三项在同一把锁里判
        ps.clear();
        ps.enter("c1", 1, 9000L, "4G");
        check("暂停期间旧组合被挡住", !ps.comboAllowed("c:c1", "c:c1", true), null);
        ps.take();
        check("恢复后同 key 可推进", ps.comboAllowed("c:c1", "c:c1", true), null);
        check("key 被换掉后不可推进", !ps.comboAllowed("c:other", "c:c1", true), null);
        check("总开关关闭后不可推进", !ps.comboAllowed("c:c1", "c:c1", false), null);
        final PauseState shared = new PauseState();
        shared.enter("c1", 0, 1000L, "4G");
        Thread clear = new Thread(new Runnable() {
            public void run() { shared.clear(); }
        });
        Thread probe = new Thread(new Runnable() {
            public void run() { shared.comboAllowed("c:c1", "c:c1", true); }
        });
        clear.start(); probe.start();
        try { clear.join(2000); probe.join(2000); } catch (InterruptedException e) { }
        check("并发清标志与准入判断不崩", !shared.paused(), null);

        System.out.println("== 7. 源码确实走生产纯函数 ==");
        String src = readSource("WeakNet.java");
        check("源码中 pause/resume 走 PauseState",
                src.contains("pauseState.enter") && src.contains("pauseState.take"), null);
        check("源码中切场景/总开关走 PauseState",
                src.contains("pauseState.whilePaused") && src.contains("pauseState.clear()"), null);
        check("源码中组合线程走 PauseState", src.contains("pauseState.comboAllowed"), null);
        check("组合等待走 PauseState.remainWait（不是测试影子逻辑）",
                src.contains("PauseState.remainWait(remain, full)"), null);
        check("旧的「full - skip」已走已过语义已删除", !src.contains("full - skip"), null);
        check("旧的影子字段已不再使用", !src.contains("pausedComboId"), null);
        // 段尾暂停（remain=0）必须跳到下一段，不能重等一整段
        check("段尾暂停跳到下一段", src.contains("saved.remain <= 0 ? saved.step + 1"), null);

        System.out.println();
        System.out.println(ok + " 项通过 / " + bad + " 项失败");
        if (bad > 0) {
            System.out.println("存在失败项");
            System.exit(1);
        }
        System.out.println("全部通过 ✅");
    }
}
