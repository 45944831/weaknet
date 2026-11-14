package com.weaknet.app;

/**
 * 屏幕适配助手的纯 JDK 回归（第六轮新增）。
 *
 * 钉住三组决定（调研：2025-2026 机型矩阵 + 官方大屏规范）：
 *   1. 断点：600/840dp（WindowSizeClass）—— 折叠屏内屏 750~900dp 跨界必须落在
 *      预期档位；Xiaomi Pad 7（~777dp）是 Medium 的代表样本。
 *   2. 弹层宽度：紧凑屏逐像素保持旧行为（屏宽 - margin，窗口 MATCH_PARENT），
 *      ≥600dp 封顶 560dp（Material 2/3 规范）。
 *   3. 悬浮窗钳制：任何 (x, y) 都收进「真实屏尺寸 - 安全边距」，窗口大于
 *      屏幕时贴安全边距，绝不拖进刘海或屏外。
 *
 * Ui 的宽度类函数全部提供 (widthPx, density) 原始类型核心重载 —— android.jar
 * 里的类在纯 JVM 全是 stub（一实例化就 `Stub!`），只能以原始类型为核；
 * 这里直接构造真实机型：S25(360dp) / Xiaomi Pad 7(777dp) /
 * Z Fold 内屏(832dp) / Tab S11(1463dp)。
 */
public class _UiScreenAdaptTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + "  <- " + extra); }
    }

    public static void main(String[] a) {
        // ---- 1. 断点与列数 ----
        ck("断点常量 = WindowSizeClass 官方值",
           Ui.WSC_MEDIUM_DP == 600 && Ui.WSC_EXPANDED_DP == 840,
           Ui.WSC_MEDIUM_DP + "/" + Ui.WSC_EXPANDED_DP);
        ck("弹层上限 = Material 规范 560dp", Ui.SHEET_MAX_DP == 560,
           "" + Ui.SHEET_MAX_DP);

        int[] phone = {1080, 3000};     // 360dp：S25 / 主流机（density 3.0）
        int[] fold = {2184, 2625};      // ~832dp：Z Fold 内屏（density 用 2.625）
        int[] pad = {2136, 2750};       // ~777dp：Xiaomi Pad 7（2.75）
        int[] padXL = {2560, 1750};     // ~1463dp：Tab S11（1.75）

        ck("手机 360dp → 3 列（旧行为）",
           Ui.columnsOf(phone[0], phone[1] / 1000f) == 3,
           "" + Ui.columnsOf(phone[0], phone[1] / 1000f));
        ck("Xiaomi Pad 7 ~777dp（Medium）→ 5 列",
           Ui.columnsOf(pad[0], pad[1] / 1000f) == 5,
           "" + Ui.columnsOf(pad[0], pad[1] / 1000f));
        ck("Z Fold 内屏 ~832dp（Medium 上段）→ 5 列",
           Ui.columnsOf(fold[0], fold[1] / 1000f) == 5,
           "" + Ui.columnsOf(fold[0], fold[1] / 1000f));
        ck("Tab S11 ~1463dp（Expanded）→ 6 列",
           Ui.columnsOf(padXL[0], padXL[1] / 1000f) == 6,
           "" + Ui.columnsOf(padXL[0], padXL[1] / 1000f));
        ck("840dp 正好在 Expanded 边界 → 6 列",
           Ui.columnsOf(2520, 3f) == 6, "840dp 边界");

        // ---- 2. 弹层宽度 ----
        ck("手机弹层内容宽 = 屏宽 - margin（逐像素旧行为）",
           Ui.sheetContentWidthOf(1080, 3f, 24) == 1080 - 72,
           "" + Ui.sheetContentWidthOf(1080, 3f, 24));
        ck("手机弹层窗口宽 = MATCH_PARENT（旧行为）",
           Ui.sheetWindowWidthOf(1080, 3f, 24)
                   == android.view.WindowManager.LayoutParams.MATCH_PARENT,
           "" + Ui.sheetWindowWidthOf(1080, 3f, 24));
        ck("平板弹层内容宽封顶 560dp",
           Ui.sheetContentWidthOf(2136, 2.75f, 24) == Math.round(560 * 2.75f),
           "" + Ui.sheetContentWidthOf(2136, 2.75f, 24));
        ck("平板弹层窗口宽 = 封顶值（居中而非全宽）",
           Ui.sheetWindowWidthOf(2136, 2.75f, 24) == Math.round(560 * 2.75f),
           "" + Ui.sheetWindowWidthOf(2136, 2.75f, 24));
        ck("超大屏弹层窗口宽仍 = 560dp 封顶",
           Ui.sheetWindowWidthOf(2560, 1.75f, 24) == Math.round(560 * 1.75f),
           "" + Ui.sheetWindowWidthOf(2560, 1.75f, 24));
        ck("折叠屏内屏弹层窗口宽 = 560dp 封顶",
           Ui.sheetWindowWidthOf(2184, 2.625f, 32) == Math.round(560 * 2.625f),
           "" + Ui.sheetWindowWidthOf(2184, 2.625f, 32));

        // ---- 3. 悬浮窗钳制（lp 空间可见框口径，见 Ui.clampOverlay 注释）----
        int[] p;
        // 场景 A · ColorOS 实测：偏移 (0,107)、真实屏 1080x2400、手势条 48px
        // → lp 空间可见框 T = 107-107 = 0，B = 2400-48-107 = 2245
        int L = 0, T = 0, R = 1080, B = 2245;
        p = Ui.clampOverlay(50, 300, 464, 177, L, T, R, B);
        ck("屏内自由位置不动", p[0] == 50 && p[1] == 300, p[0] + "," + p[1]);
        p = Ui.clampOverlay(-30, 300, 464, 177, L, T, R, B);
        ck("左出屏钳回 0", p[0] == 0, "" + p[0]);
        p = Ui.clampOverlay(1000, 300, 464, 177, L, T, R, B);
        ck("右出屏钳回 right-vw", p[0] == 1080 - 464, "" + p[0]);
        p = Ui.clampOverlay(50, 2300, 464, 177, L, T, R, B);
        ck("手势条不压（B=2245 生效）", p[1] == 2245 - 177, "" + p[1]);
        // 真机越界回归：拖到右下时 lp.y=2159，frame 底 = 2159+107+177 = 2443 > 2400
        p = Ui.clampOverlay(616, 2159, 464, 177, L, T, R, B);
        ck("ColorOS 底部越界回归：钳回 2245-177", p[1] == 2245 - 177, "" + p[1]);
        p = Ui.clampOverlay(9999, 9999, 3000, 3000, L, T, R, B);
        ck("窗口比可见框大：贴左上可见（≥0）", p[0] == 0 && p[1] == 0,
           p[0] + "," + p[1]);
        // 场景 B · 标准 ROM（lp 与屏上坐标 1:1，无偏移）：状态栏 107 → T = 107
        int T2 = 107, B2 = 2400 - 48;
        p = Ui.clampOverlay(50, 40, 464, 177, 0, T2, 1080, B2);
        ck("状态栏底下不放（标准 ROM T=107 生效）", p[1] == 107, "" + p[1]);
        p = Ui.clampOverlay(50, 2300, 464, 177, 0, T2, 1080, B2);
        ck("手势条不压（标准 ROM B=2352 生效）", p[1] == B2 - 177, "" + p[1]);
        p = Ui.clampOverlay(-999, 9999, 3000, 3000, 0, T2, 1080, B2);
        ck("钳制结果永不越出可见框左边/顶边", p[0] == 0 && p[1] == 107,
           p[0] + "," + p[1]);

        System.out.println("_UiScreenAdaptTest: " + pass + " passed, " + fail + " failed");
        if (fail > 0) System.exit(1);
    }
}
