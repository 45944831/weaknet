package com.weaknet.app;

import android.content.Context;
import android.graphics.Rect;
import android.os.Build;
import android.util.DisplayMetrics;
import android.util.TypedValue;
import android.view.Display;
import android.view.WindowManager;

/**
 * 屏幕适配助手（第六轮新增）：异形屏安全区 / 大屏断点 / 浮层与弹层的参数收敛点。
 *
 * 背景（2025-2026 机型矩阵 + 官方大屏规范调研）：
 *   - 全部手机竖屏宽度都在 480dp 以下（「1.5K」时代也是 20:9 / 420-480dpi），
 *     600dp 以上的只有平板与折叠屏内屏（750~900dp，跨 Medium/Expanded 边界）；
 *   - Android 12L 起，≥600dp 屏幕上固定竖屏会被 letterbox（Adaptive App Quality
 *     红线），Android 16/17 更是直接忽略方向锁定 —— 所以方向解锁只对 ≥600dp 生效；
 *   - Material 2/3 规范：大屏上弹层宽度上限 560dp，居中，不再整屏拉伸；
 *   - API 29 起带 FLAG_LAYOUT_NO_LIMITS 的窗口不再允许画进刘海（被系统裁剪），
 *     悬浮窗必须自己做「拖不出安全区」的钳制（见 {@link #clampOverlay}）。
 *
 * 一律用框架 API，不引 androidx。悬浮窗钳制曾按「真实屏尺寸 + WindowInsets
 * 安全边距」实现，真机（ColorOS）实测发现 overlay 渲染自带状态栏偏移、底部
 * 还有手势条，lp 对比物理屏会越界 43px——改为「屏幕坐标 + 可见显示框」口径，
 * 该口径对 ROM 的坐标映射差异免疫，安全区语义由可见显示框天然承载
 * （它已排除状态栏/刘海/导航条）。
 */
public final class Ui {
    private Ui() {
    }

    /** WindowSizeClass 宽度断点（官方定义：Compact <600 ≤ Medium <840 ≤ Expanded） */
    static final int WSC_MEDIUM_DP = 600;
    static final int WSC_EXPANDED_DP = 840;
    /** 大屏上弹层（Dialog）宽度上限：Material 2/3 组件规范值 */
    static final int SHEET_MAX_DP = 560;

    static int dp(Context c, int v) {
        return dp(c.getResources().getDisplayMetrics(), v);
    }

    /** 以 DisplayMetrics 为准的 dp 换算：纯数学，_jw 纯 JVM 单测可直接构造 */
    static int dp(DisplayMetrics dm, int v) {
        return dpOf(dm.widthPixels, dm.density, v);
    }

    /** dp 换算纯数学版：android.jar 的类在纯 JVM 里全是 stub，只能以原始类型为核 */
    static int dpOf(int widthPx, float density, int v) {
        return Math.round(v * density);
    }

    /** 当前窗口宽度（dp）：折叠 / 分屏 / 旋转都会随配置变化 */
    static int widthDp(Context c) {
        return widthDp(c.getResources().getDisplayMetrics());
    }

    static int widthDp(DisplayMetrics dm) {
        return widthDpOf(dm.widthPixels, dm.density);
    }

    static int widthDpOf(int widthPx, float density) {
        if (density <= 0) return 0;
        return Math.round(widthPx / density);
    }

    /** 最小边宽度（dp）：屏幕的「固有档位」，不随旋转变化 —— 方向决策用它 */
    static int smallestWidthDp(Context c) {
        return c.getResources().getConfiguration().smallestScreenWidthDp;
    }

    /**
     * 场景按钮网格列数：紧凑屏 3 列（原行为），Medium 5 列，Expanded 6 列。
     * 列数在每次 renderGrid 时现算：折叠屏开合途中跨断点也会自动跟上。
     */
    static int columns(Context c) {
        return columns(c.getResources().getDisplayMetrics());
    }

    static int columns(DisplayMetrics dm) {
        return columnsOf(dm.widthPixels, dm.density);
    }

    static int columnsOf(int widthPx, float density) {
        int w = widthDpOf(widthPx, density);
        if (w >= WSC_EXPANDED_DP) return 6;
        if (w >= WSC_MEDIUM_DP) return 5;
        return 3;
    }

    /**
     * 弹层内容宽度：紧凑屏 = 屏宽 - margin（旧行为，逐像素不变）；
     * ≥600dp 时封顶 560dp。min() 在紧凑屏上永远取 avail，所以手机行为不变。
     */
    static int sheetContentWidth(Context c, int marginDp) {
        return sheetContentWidth(c.getResources().getDisplayMetrics(), marginDp);
    }

    static int sheetContentWidth(DisplayMetrics dm, int marginDp) {
        return sheetContentWidthOf(dm.widthPixels, dm.density, marginDp);
    }

    static int sheetContentWidthOf(int widthPx, float density, int marginDp) {
        int avail = widthPx - dpOf(widthPx, density, marginDp);
        return Math.min(avail, dpOf(widthPx, density, SHEET_MAX_DP));
    }

    /**
     * 弹层窗口宽度：紧凑屏返回 MATCH_PARENT（窗口仍全宽，旧行为）；
     * ≥600dp 返回与 sheetContentWidth 相同的定宽 —— Dialog 默认居中，
     * 定宽窗口自动居中，不再整屏拉伸。
     */
    static int sheetWindowWidth(Context c, int marginDp) {
        return sheetWindowWidth(c.getResources().getDisplayMetrics(), marginDp);
    }

    static int sheetWindowWidth(DisplayMetrics dm, int marginDp) {
        return sheetWindowWidthOf(dm.widthPixels, dm.density, marginDp);
    }

    static int sheetWindowWidthOf(int widthPx, float density, int marginDp) {
        if (widthDpOf(widthPx, density) < WSC_MEDIUM_DP) {
            return WindowManager.LayoutParams.MATCH_PARENT;
        }
        return sheetContentWidthOf(widthPx, density, marginDp);
    }

    /**
     * 悬浮窗位置钳制：把窗口左上角坐标钳进 [L, R-vw] × [T, B-vh] 的可见框。
     *
     * 坐标口径由调用方决定（真机实测的坑）：ColorOS 给 overlay 渲染叠加了
     * 状态栏偏移（lp.y=0 → 屏上 frame.y=107），且 NO_LIMITS 窗口的
     * getWindowVisibleDisplayFrame 返回哨兵 ±100000（等于不设限）——所以
     * FloatWindow 实测「屏上位置 − lp」的偏移，再用真实屏尺寸与状态栏/导航条
     * 高度换算出 **lp 坐标空间下的可见框**传进来，对 ROM 的映射差异免疫。
     *
     * @param fx fy  窗口左上角坐标；vw vh 窗口尺寸
     * @param visL visT visR visB  可见框（调用方换算后的坐标空间）
     * @return {x, y} 钳制后的坐标
     */
    static int[] clampOverlay(int fx, int fy, int vw, int vh,
                              int visL, int visT, int visR, int visB) {
        int maxX = Math.max(visL, visR - vw);
        int maxY = Math.max(visT, visB - vh);
        int cx = Math.min(Math.max(fx, visL), maxX);
        int cy = Math.min(Math.max(fy, visT), maxY);
        return new int[]{cx, cy};
    }

    /**
     * 真实显示尺寸（像素）：API 30+ 用 WindowMetrics（折叠态感知），
     * 更早用 Display.getRealMetrics。overlay 的可见范围要按整块物理屏算，
     * DisplayMetrics.heightPixels 是应用窗口可用高度（被系统条扣过）。
     * 返回 {width, height}。
     */
    @SuppressWarnings("deprecation")
    static int[] realDisplaySize(Context c) {
        WindowManager wm = (WindowManager) c.getSystemService(Context.WINDOW_SERVICE);
        if (wm == null) {
            DisplayMetrics dm = c.getResources().getDisplayMetrics();
            return new int[]{dm.widthPixels, dm.heightPixels};
        }
        if (Build.VERSION.SDK_INT >= 30) {
            Rect b = wm.getCurrentWindowMetrics().getBounds();
            return new int[]{b.width(), b.height()};
        }
        Display d = wm.getDefaultDisplay();
        if (d == null) {
            DisplayMetrics dm = c.getResources().getDisplayMetrics();
            return new int[]{dm.widthPixels, dm.heightPixels};
        }
        DisplayMetrics real = new DisplayMetrics();
        d.getRealMetrics(real);
        return new int[]{real.widthPixels, real.heightPixels};
    }

    /**
     * 框架内部 dimen（status_bar_height / navigation_bar_height 等），
     * 读不到给 fallbackDp 的 AOSP 默认。Throwable 而非 Exception：纯 JVM
     * （_jw 单测）里 Resources.getSystem() 抛的是 Error，兜底必须照常工作。
     */
    static int systemBarHeight(float density, String name, int fallbackDp) {
        try {
            android.content.res.Resources res = android.content.res.Resources.getSystem();
            int id = res.getIdentifier(name, "dimen", "android");
            if (id > 0) return res.getDimensionPixelSize(id);
        } catch (Throwable e) {
            // ignore：非 Android 运行时
        }
        return Math.round(fallbackDp * density);
    }
}
