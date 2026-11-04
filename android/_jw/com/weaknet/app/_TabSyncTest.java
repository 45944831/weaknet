package com.weaknet.app;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStreamReader;
import java.io.BufferedReader;

/**
 * 「场景 / 组合」标签状态机 + 即时刷新接线单测（纯 JDK 可跑）。
 *
 * 回归背景：用户反馈「自定义场景与弱网组合标签，选择后特效不会马上刷新」。
 * 两个原因叠在一起：
 *   1. 主界面只靠 3 秒轮询同步标签，从飘窗 / Web 控制台 / 共享面板改了选中项，
 *      要等下一轮才翻页染色 —— 现在 MainPanels 注册了 fireUiChange 的即时回调；
 *   2. 手动点标签不更新去重用的 lastTabKey，下一次轮询会按选中项把页面翻回去
 *      （真机上表现为：点「场景」立刻变紫，约 3 秒后又跳回「组合」）。
 */
public class _TabSyncTest {
    static int pass = 0, fail = 0;

    static void ck(String name, boolean ok) {
        ck(name, ok, "");
    }

    static void ck(String name, boolean ok, String extra) {
        if (ok) {
            pass++;
            System.out.println("  PASS  " + name + (extra.length() > 0 ? "   " + extra : ""));
        } else {
            fail++;
            System.out.println("  FAIL  " + name + "  <- " + extra);
        }
    }

    static String src(String path) {
        File f = new File(path);
        if (!f.exists()) f = new File("../" + path);
        BufferedReader br = null;
        StringBuilder sb = new StringBuilder();
        try {
            br = new BufferedReader(new InputStreamReader(new FileInputStream(f), "UTF-8"));
            String ln;
            while ((ln = br.readLine()) != null) sb.append(ln).append('\n');
        } catch (Exception e) {
            return "";
        } finally {
            try {
                if (br != null) br.close();
            } catch (Exception ignored) {
                // ignore
            }
        }
        return sb.toString();
    }

    public static void main(String[] args) {
        // ---- A 跟着选中项翻页 ----
        TabSync t = new TabSync();
        ck("A1 初始停在场景页", TabSync.SCENE.equals(t.tab()));
        ck("A2 首次选中组合 → 组合页",
                TabSync.COMBO.equals(t.onSelection("c:abc")));
        ck("A3 同一个 key 再来一次不重复翻页",
                TabSync.COMBO.equals(t.onSelection("c:abc")));
        ck("A4 换一支组合仍在组合页",
                TabSync.COMBO.equals(t.onSelection("c:def")));
        ck("A5 选中自定义场景 → 场景页",
                TabSync.SCENE.equals(t.onSelection("u:xyz")));
        ck("A6 内置档位 / 直连不抢标签",
                TabSync.SCENE.equals(t.onSelection("direct"))
                        && TabSync.SCENE.equals(t.onSelection("wifi_good")));
        ck("A7 null key 当成空串，不炸也不翻页",
                TabSync.SCENE.equals(t.onSelection(null)));

        // ---- B 手动点标签不能被轮询拽回去（真机复现的那一跳）----
        TabSync b = new TabSync();
        b.onSelection("c:abc");                       // 当前选中组合
        ck("B1 手动点「场景」立即生效",
                TabSync.SCENE.equals(b.onClick(TabSync.SCENE, "c:abc")));
        ck("B2 3 秒后轮询看到同一个 key，仍留在用户选的场景页",
                TabSync.SCENE.equals(b.onSelection("c:abc")));
        ck("B3 但选中项真变了，还是要跟着翻到组合页",
                TabSync.COMBO.equals(b.onSelection("c:new")));
        ck("B4 手动点「组合」，随后同 key 轮询不翻回场景",
                TabSync.COMBO.equals(b.onClick(TabSync.COMBO, "c:new"))
                        && TabSync.COMBO.equals(b.onSelection("c:new")));
        ck("B5 手动点标签时选中项为空也不炸",
                TabSync.SCENE.equals(b.onClick(TabSync.SCENE, null))
                        && TabSync.SCENE.equals(b.onSelection(null)));

        // ---- C 即时刷新的接线别再掉回「只靠轮询」----
        String panels = src("src/com/weaknet/app/MainPanels.java");
        String gui = src("../weaknet_gui.py");
        ck("C1 MainPanels 源码可读", panels.length() > 0);
        ck("C2 注册并注销 fireUiChange 即时回调",
                panels.contains("addUiWatcher(tabWatcher)")
                        && panels.contains("removeUiWatcher(tabWatcher)"));
        ck("C3 手动点标签走 onTabClicked（状态机记住这次选择）",
                panels.contains("onTabClicked(\"scene\")")
                        && panels.contains("onTabClicked(\"combo\")")
                        && panels.contains("tabSync.onClick("));
        ck("C4 轮询与初始化都走状态机",
                panels.contains("tabSync.onSelection("));
        ck("C5 按下态动画立刻落定，不盖住新配色",
                panels.contains("jumpDrawablesToCurrentState()"));
        ck("C6 Windows 端连 hover 态一起刷（activebackground）",
                gui.contains("activebackground=bg, activeforeground=fg")
                        && gui.contains("def _paint_tabs"));
        ck("C7 Windows 端手动点标签也标记已处理",
                gui.contains("manual=True") && gui.contains("if manual:"));

        System.out.println();
        System.out.println("  " + pass + " pass, " + fail + " fail");
        if (fail > 0) System.exit(1);
    }
}
