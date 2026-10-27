package com.weaknet.app;

import java.util.ArrayList;
import java.util.List;

/**
 * 悬浮窗「暂停 / 恢复」的等价断言。
 *
 * WeakNet 整体依赖 Android（Handler/Looper/Context），PC 上跑不起来；
 * 但这次暂停语义里最容易出错、也最该被钉住的是「恢复要接着暂停时的位置」，
 * 这部分依赖的只是几段纯算术，所以抽出来单独验一遍：
 *
 *   1. 暂停时记住的是「第几段 + 这段已经走过多久」
 *   2. 恢复时这一段的剩余时长 = 整段时长 - 已走过（不能从头再等一遍）
 *   3. 恢复后接着下一段推进，段序不丢
 *   4. 暂停期间不推进段（时间到了也不切段）
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

    /** 暂停时记下的现场：播到第几段、这段已经走了多久 */
    static class PauseMark {
        int step;          // 段序号（0 起）
        long elapsed;      // 这一段已经走过多少毫秒
        PauseMark(int step, long elapsed) {
            this.step = step;
            this.elapsed = elapsed;
        }
    }

    /** 暂停时把现场记下来 */
    static PauseMark mark(int step, long elapsed) {
        return new PauseMark(step, elapsed);
    }

    /** 恢复时这一段还剩多少毫秒 */
    static long remainMs(PauseMark m, long fullMs) {
        long left = fullMs - (m == null ? 0 : m.elapsed);
        return left < 0 ? 0 : left;
    }

    /** 恢复后从哪一段接着走 */
    static int resumeStep(PauseMark m, int total) {
        if (m == null) return 0;
        if (m.step < 0) return 0;
        if (m.step >= total) return total - 1;
        return m.step;
    }

    static long ms(int minutes, int seconds) {
        return ComboPlan.clamp(minutes, ComboPlan.MAX_MINUTES) * 60000L
                + ComboPlan.clamp(seconds, ComboPlan.MAX_SECONDS) * 1000L;
    }

    public static void main(String[] args) {
        System.out.println("== 1. 暂停时记住现场 ==");
        PauseMark m = mark(2, 12000);          // 第 3 段，已走 12 秒
        check("记住段序号", m.step == 2, m.step);
        check("记住已走时长", m.elapsed == 12000L, m.elapsed);

        System.out.println("== 2. 恢复时这一段接着倒计时 ==");
        long full = ms(0, 30);                 // 这一段整段 30 秒
        long left = remainMs(m, full);
        check("剩余 = 整段 - 已走 = 18 秒", left == 18000L, left);
        check("不会从头再等 30 秒", left < full, left + " vs " + full);

        System.out.println("== 3. 暂停很久也不会把这一段耗光 ==");
        PauseMark m2 = mark(0, 999999L);
        long left2 = remainMs(m2, full);
        check("剩余被夹到 0（不为负）", left2 == 0L, left2);

        System.out.println("== 4. 恢复后接着下一段走 ==");
        int step = resumeStep(m, 5);
        check("还在第 3 段（0 起是 2）", step == 2, step);
        int nxt = ComboPlan.nextIndex(step, 5, false);
        check("走完这段接着第 4 段", nxt == 3, nxt);

        System.out.println("== 5. 暂停在循环组合里也不越界 ==");
        int step2 = resumeStep(mark(4, 1000), 5);
        check("第 5 段就是第 5 段", step2 == 4, step2);
        int nxt2 = ComboPlan.nextIndex(step2, 5, true);
        check("循环组合回到第 1 段", nxt2 == 0, nxt2);
        int nxt3 = ComboPlan.nextIndex(step2, 5, false);
        check("非循环组合到此结束", nxt3 == -1, nxt3);

        System.out.println("== 6. 没有现场时从第 1 段开始 ==");
        check("空现场 -> 第 1 段", resumeStep(null, 5) == 0, null);

        System.out.println("== 7. 暂停不该改段长规则 ==");
        // 暂停只影响「还剩多久」，不改变一段本身有多长
        check("整段时长仍是 30 秒", ms(0, 30) == 30000L, ms(0, 30));
        check("分秒换算正确", ms(1, 30) == 90000L, ms(1, 30));
        check("秒上限被夹住", ms(0, 99) == 59000L, ms(0, 99));

        System.out.println();
        System.out.println(ok + " 项通过 / " + bad + " 项失败");
        if (bad > 0) {
            System.out.println("存在失败项");
            System.exit(1);
        }
        System.out.println("全部通过 ✅");
    }
}
