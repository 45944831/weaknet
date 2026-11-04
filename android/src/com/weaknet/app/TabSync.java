package com.weaknet.app;

/**
 * 「场景 / 组合」标签的选中状态机（纯 JVM，无 Android 依赖，可直接跑单测）。
 *
 * 两条规矩，看着矛盾其实都得要：
 *  1. 选中项真的变了 → 标签跟着它翻页（从飘窗/控制台选了组合，主界面得显示组合页）；
 *  2. 选中项没变 → 保留**用户手动点的那一页**（点开「场景」看看，3 秒后被自动
 *     同步拽回「组合」，是最招人烦的一种「标签乱跳」）。
 *
 * 早先 MainPanels 只用一个 lastTabKey 去重、又完全依赖 3 秒轮询，于是：
 * 手动切页不更新 lastTabKey、选中项变化也没有即时通知，两种现象都出现过。
 * 现在把判定收在这里，MainPanels 只负责按 {@link #tab()} 画界面，
 * 并由 WeakNet 的 fireUiChange 即时驱动，不再等轮询。
 */
public class TabSync {
    public static final String SCENE = "scene";
    public static final String COMBO = "combo";

    private String lastKey = null;
    private String tab = SCENE;

    /** 当前应该显示的页 */
    public String tab() {
        return tab;
    }

    /** 上一次处理过的选中项（单测与排查用） */
    public String lastKey() {
        return lastKey;
    }

    /**
     * 引擎侧选中项同步：返回这一刻该显示的页。
     * key 没变就原样返回 —— 用户手动切过去的页不会被定时轮询拽回。
     * 内置预设 / 直连不强制翻页（它们不属于这两个自定义页）。
     */
    public String onSelection(String key) {
        String k = key == null ? "" : key;
        if (k.equals(lastKey)) return tab;
        lastKey = k;
        if (k.startsWith(ComboStep.KEY_PREFIX)) tab = COMBO;
        else if (k.startsWith("u:")) tab = SCENE;
        return tab;
    }

    /**
     * 用户手动点标签：立刻切到那一页，并把当前选中项标记为「已处理」，
     * 这样接下来几次轮询看到同一个 key 就不会把页面翻回去。
     */
    public String onClick(String t, String key) {
        tab = COMBO.equals(t) ? COMBO : SCENE;
        lastKey = key == null ? "" : key;
        return tab;
    }
}
