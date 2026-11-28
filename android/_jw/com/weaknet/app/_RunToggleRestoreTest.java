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

        // ---- 停止态草稿：开启时原样生效（外部审查：停止态编辑参数丢失）----
        WeakNet w = new WeakNet();
        w.presetKey = "custom";
        Profile draft = custom(480);
        w.pendingProfile = draft;
        w.pendingKey = "custom";
        Profile got = w.resolveEnableRestore(null);
        ck("停止态草稿优先于 lastProfile", got == draft && got.downKbps == 480,
           "" + got.downKbps);
        w.pendingKey = "other";
        Profile got2 = w.resolveEnableRestore(null);
        ck("草稿 key 不匹配时不误用", got2 != draft && got2.downKbps == 0,
           "" + got2.downKbps);

        // ---- 操作序列矩阵（第 1 轮）：跨操作时序 ----
        // 开关只跟「参数是否真的会动流量」走：运行中点「直连」这一档不能停在
        // 「已开启」——否则界面显示弱网、实际零限制，导出报告还会把直连当弱网。
        WeakNet w2 = new WeakNet();
        w2.limitOn = true;
        w2.selectScene(new Profile(), "direct");
        ck("运行中切直连：开关按实际参数关掉", !w2.limitOn, "" + w2.limitOn);

        // 切直连已经等于「停止」，之后的切场景只算选中（与桌面端 S6b 同行为）
        w2.selectScene(custom(300), "4g");
        ck("停止后切场景：只选中不生效", !w2.limitOn, "" + w2.limitOn);
        ck("停止后切场景：参数留成草稿",
           w2.pendingProfile != null && w2.pendingProfile.downKbps == 300,
           w2.pendingProfile == null ? "null" : "" + w2.pendingProfile.downKbps);

        // 关闭前把运行中调过的参数存成草稿，否则再开启按预设重建会静默覆盖
        WeakNet w3 = new WeakNet();
        w3.profile = custom(800);
        w3.presetKey = "4g";
        w3.limitOn = true;
        w3.setLimitOn(false, null);
        ck("关闭时把运行中调参存成草稿",
           w3.pendingProfile != null && w3.pendingProfile.downKbps == 800,
           w3.pendingProfile == null ? "null" : "" + w3.pendingProfile.downKbps);
        ck("关闭后开关为 false", !w3.limitOn, "" + w3.limitOn);

        // 组合 key 不存草稿：下一段由播放器重新给参数
        WeakNet w3b = new WeakNet();
        w3b.profile = custom(800);
        w3b.presetKey = MyCombos.KEY_PREFIX + "1";
        w3b.limitOn = true;
        w3b.setLimitOn(false, null);
        ck("组合播放中关闭不存草稿", w3b.pendingProfile == null,
           "" + (w3b.pendingProfile == null ? "null" : "not-null"));

        // 开启时目标组合已失效且没有可施加的参数：不能谎报「已开启」
        WeakNet w4 = new WeakNet();
        w4.presetKey = MyCombos.KEY_PREFIX + "nope";
        w4.profile = new Profile();
        w4.limitOn = true;
        boolean handled = w4.resumeIfCombo();
        ck("组合已删且参数全零：开关回落 false",
           !handled && !w4.limitOn, "handled=" + handled + " on=" + w4.limitOn);

        // 组合没了但回落参数有内容：保持开启
        WeakNet w5 = new WeakNet();
        w5.presetKey = MyCombos.KEY_PREFIX + "nope";
        w5.profile = custom(1200);
        w5.limitOn = true;
        boolean handled2 = w5.resumeIfCombo();
        ck("组合已删但回落参数有内容：保持开启",
           !handled2 && w5.limitOn, "handled=" + handled2 + " on=" + w5.limitOn);

        System.out.println("_RunToggleRestoreTest: " + pass + " passed, " + fail + " failed");
        if (fail > 0) System.exit(1);
    }
}
