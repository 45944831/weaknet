package com.weaknet.app;

/**
 * 复测报告 4.1 回归：限速开关 关→开 后自定义参数不得丢失。
 *
 * 真机 /api/run 三步复现过「假开启」：
 *   POST /api/profile {"down_kbps":4000} → 开（4000 ✓）→ 关（live 被清直连）
 *   → 再开：limit_on=true 但 down=0 —— 界面显示已开启、实际不限速。
 * 根因：开启时对 custom 恒取「当前 profile」，而关闭恰好把它清空了。
 * 修复：WeakNet.resolveEnable —— live 还带参数就保留，被清空/全零则回落
 * lastProfile（setProfile 只在 hasLimits 时更新它，所以它保有关闭前的参数），
 * 与 Windows 端 set_limit 对 custom 的 last_profile 回落同构。
 */
public class _RunToggleRestoreTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + "  <- " + extra); }
    }

    private static Profile custom(int down) {
        Profile p = new Profile();
        p.name = "rv" + down;
        p.downKbps = down;
        return p;
    }

    public static void main(String[] a) {
        Profile live4000 = custom(4000);
        // lastProfile 是 live 的副本（Profile.copy 内部走 JSONObject，android.jar
        // stub 在纯 JVM 会抛 Stub!，这里独立构造同值实例，语义等价）
        Profile last = custom(4000);
        Profile wiped = new Profile();           // setLimitOn(false) 清空后的 live

        // ---- 报告 4.1 主场景 ----
        Profile r = WeakNet.resolveEnable(null, "custom", wiped, last);
        ck("关→开：回落 lastProfile，down=4000 不丢（旧代码这里取到清空直连）",
           r.downKbps == 4000, "" + r.downKbps);

        // ---- 用户刚设（live 带参数）：原样保留，不换对象 ----
        r = WeakNet.resolveEnable(null, "custom", live4000, last);
        ck("刚设的自定义参数原样保留（同一份对象）",
           r.downKbps == 4000 && r == live4000, "" + r.downKbps);

        // ---- 内置 / u: 场景：调用方解析结果直接落地 ----
        Profile builtin = custom(8000);
        r = WeakNet.resolveEnable(builtin, "wifi", wiped, last);
        ck("内置场景解析结果直接落地", r == builtin, "" + r.downKbps);

        // ---- 非 custom key 解析失败 → lastProfile（旧行为保留）----
        r = WeakNet.resolveEnable(null, "whatever", wiped, last);
        ck("解析失败回落 lastProfile", r == last, "" + r.downKbps);

        // ---- custom 全零 + 有 lastProfile：回落（与 Windows 同构，不假开启）----
        r = WeakNet.resolveEnable(null, "custom", new Profile(), last);
        ck("全零自定义回落 lastProfile", r.downKbps == 4000, "" + r.downKbps);

        // ---- custom 全零 + 无 lastProfile：直连落地（全新会话，旧行为保留）----
        r = WeakNet.resolveEnable(null, "custom", new Profile(), null);
        ck("全零自定义且无历史：直连落地", r.downKbps == 0, "" + r.downKbps);

        // ---- live 为 null 的防御 ----
        r = WeakNet.resolveEnable(null, "custom", null, last);
        ck("live 为 null 时回落 lastProfile", r.downKbps == 4000, "" + r.downKbps);

        // ---- lastProfile 也为 null：直连兜底 ----
        r = WeakNet.resolveEnable(null, "custom", null, null);
        ck("无任何历史：直连兜底", r.downKbps == 0, "" + r.downKbps);

        // ---- /api/profile 的 limit_on 契约（与 Windows set_profile 对齐）----
        // setProfile 刻意不动开关（selectScene 也走它，「选中不生效」），
        // 所以 API handler 用 limitOnForApply 显式补齐「应用即生效」。
        ck("应用带限制的参数 → limit_on=true",
           WeakNet.limitOnForApply(custom(100)), "false");
        ck("应用全零参数 → limit_on=false",
           !WeakNet.limitOnForApply(new Profile()), "true");
        ck("null 参数按不限速处理", !WeakNet.limitOnForApply(null), "true");
        Profile onlyLatency = new Profile();
        onlyLatency.latencyUpMs = 200;
        ck("只改延迟也算有极限（hasLimits 口径）",
           WeakNet.limitOnForApply(onlyLatency), "false");

        System.out.println("_RunToggleRestoreTest: " + pass + " passed, " + fail + " failed");
        if (fail > 0) System.exit(1);
    }
}
