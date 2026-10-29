package com.weaknet.app;

import java.util.ArrayList;
import java.util.List;

/**
 * 弱网组合的纯计算部分：时长换算、入库清洗、播放序号推进。
 *
 * 这里刻意不引用任何 Android / org.json 的类：
 * 组合最容易错的是「第几段该切了 / 这段时长到底多少毫秒 / 脏段有没有被丢掉」，
 * 全是纯算术，放在一个能直接用 JVM 跑单测的类里，比只能靠真机点更能兜住问题。
 * 涉及 IO 的存取在 {@link MyCombos}，涉及 {@link WeakNet} 状态的播放在 {@link WeakNet}。
 */
public class ComboPlan {
    /** 上限：再长就不好在飘窗上按顺序核对了（数值来自 spec/spec.json 的 combo 段） */
    public static final int MAX_STEPS = SpecGen.COMBO_MAX_STEPS;
    public static final int MAX_MINUTES = SpecGen.COMBO_MAX_MINUTES;   // 单段最多 10 分钟
    public static final int MAX_SECONDS = SpecGen.COMBO_MAX_SECONDS;   // 秒这一格只收到 59，再大请用「分」

    public static int clamp(int v, int max) {
        if (v < 0) return 0;
        return v > max ? max : v;
    }

    /** 这段跑多久（毫秒） */
    public static long stepMs(ComboStep s) {
        if (s == null) return 0;
        return clamp(s.minutes, MAX_MINUTES) * 60000L + clamp(s.seconds, MAX_SECONDS) * 1000L;
    }

    /** 分秒至少得有一个大于 0，否则这段没法定时，等于没排 */
    public static boolean hasDur(ComboStep s) {
        return stepMs(s) > 0;
    }

    /** 界面上这段时长怎么写：「2 分 30 秒」 */
    public static String stepText(ComboStep s) {
        if (s == null) return "0 秒";
        int mi = clamp(s.minutes, MAX_MINUTES);
        int se = clamp(s.seconds, MAX_SECONDS);
        if (mi > 0 && se > 0) return mi + " 分 " + se + " 秒";
        if (mi > 0) return mi + " 分";
        return se + " 秒";
    }

    /**
     * 收敛步骤列表：丢掉没选场景的、时长全空的、嵌套组合的，超上限的裁掉。
     *
     * 不允许段里选另一支组合：播放是按 ──steps── 线性推进的，套一层它既不会展开
     * 也不会报错，只会在运行时把这一段跳过，用户排的第 2 段凭空消失最难排查。
     *
     * 泛型是为了调用方类型不被削掉：MyCombos 里存的是 List&lt;Step&gt;，
     * 这里进出都保持 Step，调用点不用到处强转。
     *
     * @param rejected 传入长度 &gt;= 1 的 int[] 时，被嵌套规则丢掉的段数写进 [0]
     */
    @SuppressWarnings("unchecked")
    public static <T extends ComboStep> List<T> norm(List<T> in, int[] rejected) {
        List<T> out = new ArrayList<T>();
        if (in == null) return out;
        for (T s : in) {
            if (s == null) continue;
            String key = s.key == null ? "" : s.key.trim();
            if (key.length() == 0) continue;
            if (key.startsWith(ComboStep.KEY_PREFIX)) {
                if (rejected != null && rejected.length > 0) rejected[0]++;
                continue;
            }
            // blank() 由子类协变覆盖，运行期拿到的就是调用方那个类型（Step / ComboStep），
            // 强转安全：外面 `for (Step s : steps)` 的 checkcast 才不会炸
            T t = (T) s.blank();
            t.key = key;
            t.minutes = clamp(s.minutes, MAX_MINUTES);
            t.seconds = clamp(s.seconds, MAX_SECONDS);
            if (!hasDur(t)) continue;
            out.add(t);
            if (out.size() >= MAX_STEPS) break;
        }
        return out;
    }

    /**
     * 第 i 段（0 起）播完后的下一段；返回 -1 表示整支组合结束。
     *
     * loop 只在最后一段兜回来：中途的段正常往下走，不做任何跳段。
     */
    public static int nextIndex(int i, int total, boolean loop) {
        if (total <= 0) return -1;
        int n = i + 1;
        if (n < total) return n;
        return loop ? 0 : -1;
    }
}
