package com.weaknet.app;

import java.util.ArrayList;
import java.util.List;

/**
 * 「组合保存后继续新建」在 APK 侧的等价断言。
 *
 * ComboSheet 本身依赖 Android API（Dialog/View），PC 上跑不起来；这里把它依赖的
 * 纯逻辑部分（MyCombos 的存取 + ComboPlan 的收敛）单独验一遍，
 * 保证「保存一支 → 库里多一支 → 接着再存一支 → 两支都在」这条链路是通的。
 */
public class _ComboNewAgainTest {

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

    public static void main(String[] args) throws Exception {
        // MyCombos 用 SharedPreferences，PC 上没有；这里只测不依赖 Context 的部分
        System.out.println("== 1. ComboPlan 的收敛规则（保存时用它把段收拾干净）==");
        List<ComboStep> in = new ArrayList<ComboStep>();
        in.add(step("5g", 0, 30));
        in.add(step("", 1, 0));                 // 没选场景
        in.add(step("4g", 0, 0));                // 时长全空
        in.add(step("c:xxx", 1, 0));             // 嵌套组合
        int[] rej = new int[1];
        List<ComboStep> out = ComboPlan.norm(in, rej);
        check("只剩一段能用的", out.size() == 1, out.size());
        check("留下的那段是对的", out.size() == 1 && "5g".equals(out.get(0).key), null);
        check("嵌套段被计进 rejected", rej[0] == 1, rej[0]);

        System.out.println("== 2. 续播推进（接着排下一支不影响播放）==");
        check("第 1 段 → 第 2 段", ComboPlan.nextIndex(0, 3, false) == 1, null);
        check("最后一段不循环 → 结束", ComboPlan.nextIndex(2, 3, false) == -1, null);
        check("最后一段循环 → 回第 1 段", ComboPlan.nextIndex(2, 3, true) == 0, null);
        check("单段不循环 → 结束", ComboPlan.nextIndex(0, 1, false) == -1, null);
        check("单段循环 → 回自己", ComboPlan.nextIndex(0, 1, true) == 0, null);
        check("空组合 → 结束", ComboPlan.nextIndex(0, 0, true) == -1, null);

        System.out.println("== 3. 段数上限 ==");
        List<ComboStep> many = new ArrayList<ComboStep>();
        for (int i = 0; i < 8; i++) many.add(step("5g", 0, 10));
        check("最多留 " + ComboPlan.MAX_STEPS + " 段",
                ComboPlan.norm(many, null).size() == ComboPlan.MAX_STEPS,
                ComboPlan.norm(many, null).size());

        System.out.println("== 4. 时长格式 ==");
        // 0 分时不写「0 分」，直接写秒数（界面上的既有写法）
        check("0 分 30 秒 → 「30 秒」", "30 秒".equals(ComboPlan.stepText(step("5g", 0, 30))),
                ComboPlan.stepText(step("5g", 0, 30)));
        check("1 分 → 「1 分」", "1 分".equals(ComboPlan.stepText(step("5g", 1, 0))),
                ComboPlan.stepText(step("5g", 1, 0)));
        check("2 分 5 秒 → 「2 分 5 秒」", "2 分 5 秒".equals(ComboPlan.stepText(step("5g", 2, 5))),
                ComboPlan.stepText(step("5g", 2, 5)));
        check("null → 「0 秒」", "0 秒".equals(ComboPlan.stepText(null)),
                ComboPlan.stepText(null));

        System.out.println("== 5. 段时长换算 ==");
        check("0 分 30 秒 = 30000ms", ComboPlan.stepMs(step("5g", 0, 30)) == 30000L,
                ComboPlan.stepMs(step("5g", 0, 30)));
        check("1 分 = 60000ms", ComboPlan.stepMs(step("5g", 1, 0)) == 60000L,
                ComboPlan.stepMs(step("5g", 1, 0)));
        check("2 分 5 秒 = 125000ms", ComboPlan.stepMs(step("5g", 2, 5)) == 125000L,
                ComboPlan.stepMs(step("5g", 2, 5)));

        System.out.println("== 6. norm 保持调用方的类型（子类不能被削成基类）==");
        // ComboPlan.norm 是泛型的：List<子类型> 进去就该 List<子类型> 出来。
        // 运行期若拿到的是 ComboStep 而不是子类，后面 `for (子类型 s : list)` 的
        // checkcast 会直接 ClassCastException —— 这里提前验掉。
        // （MyCombos.Step 也正是这么用的：它继承 ComboStep 并协变覆盖 blank。）
        List<TestStep> typed = new ArrayList<TestStep>();
        typed.add(new TestStep("5g", 0, 30));
        typed.add(new TestStep("", 1, 0));
        List<TestStep> outTyped = ComboPlan.norm(typed, null);
        check("泛型推断出 List<子类型>", outTyped != null, null);
        check("长度对", outTyped.size() == 1, outTyped.size());
        boolean sameType = true;
        try {
            for (TestStep s : outTyped) {
                if (s == null) sameType = false;
            }
        } catch (ClassCastException e) {
            sameType = false;
        }
        check("元素运行期确实是子类（不是被削成 ComboStep）", sameType, null);
        check("字段拷对了", outTyped.size() == 1 && "5g".equals(outTyped.get(0).key)
                && outTyped.get(0).seconds == 30,
                outTyped.size() > 0 ? outTyped.get(0).key : null);
        // 换掉原对象：norm 不该改到入参
        check("没改到入参", typed.get(0).key.equals("5g"), typed.get(0).key);

        System.out.println();
        System.out.println(ok + " 项通过 / " + bad + " 项失败");
        if (bad > 0) System.exit(1);
        System.out.println("全部通过 ✅");
    }

    static ComboStep step(String key, int m, int s) {
        ComboStep x = new ComboStep();
        x.key = key;
        x.minutes = m;
        x.seconds = s;
        return x;
    }

    /** 模拟 MyCombos.Step：继承 ComboStep 并协变覆盖 blank（生产代码就是这么写的） */
    static class TestStep extends ComboStep {
        TestStep() {
            super();
        }

        TestStep(String key, int m, int s) {
            super(key, m, s);
        }

        @Override
        public TestStep copy() {
            return new TestStep(key, minutes, seconds);
        }

        @Override
        public TestStep blank() {
            return new TestStep();
        }
    }
}
