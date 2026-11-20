package com.weaknet.app;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStreamReader;
import java.io.BufferedReader;

/**
 * 悬浮窗操作后主界面状态卡 / 总开关同步刷新的接线单测（纯 JDK 可跑）。
 *
 * 回归背景：用户反馈「通过悬浮窗停止弱网，App 上状态未变」。
 * 悬浮窗的 ⇄切场景、⏸暂停/恢复都不在 Activity 里，走 WeakNet.fireUiChange()
 * 推即时刷新；但 MainActivity.updateStatus()（刷状态卡文字与开始/停止按钮）
 * 原先没注册成 watcher，也没有 onResume —— 飘窗自己刷了，主界面纹丝不动。
 * 而且暂停时 limitOn 仍为真，文案里没有「已暂停」分支，即便刷了也显示「进行中」。
 *
 * 本测试用源码结构断言守住这条接线：注册 / 注销 / 回调内容 / 暂停态文案 /
 * 以及 pause、resume 确实 fireUiChange。
 */
public class _MainStatusSyncTest {
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
        String act = src("src/com/weaknet/app/MainActivity.java");
        String wn = src("src/com/weaknet/app/WeakNet.java");

        ck("A1 MainActivity 源码可读", act.length() > 0);
        ck("A2 WeakNet 源码可读", wn.length() > 0);

        // ---- B 主界面把 updateStatus 挂到 fireUiChange ----
        ck("B1 注册 uiWatcher", act.contains("addUiWatcher(uiWatcher)"));
        ck("B2 回调体调用 updateStatus",
                act.contains("public void run() {\n            updateStatus();")
                        || act.contains("public void run() {\n                updateStatus();")
                        || (act.contains("uiWatcher") && act.contains("updateStatus();\n        }")));
        ck("B3 onDestroy 注销 uiWatcher（不打到已销毁 Activity）",
                act.contains("removeUiWatcher(uiWatcher)"));

        // ---- C 暂停态文案：别让用户以为没生效 ----
        ck("C1 状态卡有「已暂停」分支",
                act.contains("on && lim && WeakNet.I.paused")
                        && act.contains("弱网测试已暂停"));
        ck("C2 暂停态给出恢复提示", act.contains("点悬浮窗"));

        // ---- D 悬浮窗操作确实触发即时通知 ----
        ck("D1 pause 会 fireUiChange",
                wn.contains("public synchronized boolean pause()")
                        && wn.contains("paused = true;")
                        && wn.indexOf("fireUiChange()", wn.indexOf("boolean pause()")) > 0);
        ck("D2 resume 会 fireUiChange",
                wn.indexOf("fireUiChange()", wn.indexOf("boolean resume()")) > 0);

        // ---- F 暂停标志不能残留（暂停→停止→再开始，界面会误报「已暂停」）----
        int iSetLimit = wn.indexOf("public synchronized void setLimitOn(");
        int iClear = wn.indexOf("private void clearPauseLocked()");
        int iClearCall = iSetLimit > 0 ? wn.indexOf("clearPauseLocked()", iSetLimit) : -1;
        int iIfOn = iSetLimit > 0 ? wn.indexOf("if (on)", iSetLimit) : -1;
        ck("F1 setLimitOn 开头就清暂停快照",
                iSetLimit > 0 && iClear > 0
                        && iClearCall > 0 && iIfOn > 0 && iClearCall < iIfOn,
                "call@" + iClearCall + " ifOn@" + iIfOn);
        ck("F2 clearPauseLocked 把 paused 置回 false",
                iClear > 0 && wn.indexOf("paused = false;", iClear) > 0
                        && wn.indexOf("pausedProfile = null;", iClear) > 0);
        ck("F3 setProfile 落地新参数时也清暂停（防 /api/profile 路径残留）",
                wn.indexOf("if (paused) clearPauseLocked();",
                        wn.indexOf("public synchronized void setProfile(")) > 0);

        // ---- G 暂停中换场景/组合：保持暂停，不能静默开始限速 ----
        int iSelScene = wn.indexOf("public synchronized void selectScene(");
        int iSelCombo = wn.indexOf("public synchronized boolean selectCombo(");
        ck("G1 selectScene 有暂停分支且不调用 setProfile",
                iSelScene > 0
                        && wn.indexOf("if (paused)", iSelScene) > 0
                        && wn.indexOf("if (paused)", iSelScene) < wn.indexOf("setProfile(p, key)", iSelScene));
        ck("G2 暂停中换场景只更新恢复目标（pausedProfile）",
                iSelScene > 0 && wn.indexOf("pausedProfile = p != null", iSelScene) > 0);
        ck("G3 selectCombo 有暂停分支，只记住从第 1 段播",
                iSelCombo > 0
                        && wn.indexOf("if (paused)", iSelCombo) > 0
                        && wn.indexOf("pausedComboStep = 0;", iSelCombo) > 0
                        && wn.indexOf("if (paused)", iSelCombo) < wn.indexOf("startComboLocked(c.copy())", iSelCombo));
        ck("G4 组合循环在暂停时不再推进（sleepOwning / runCombo 双保险）",
                wn.contains("if (paused) return false;")
                        && wn.contains("!limitOn || paused"));

        // ---- E 总开关按钮的暂停态（真机反馈：状态卡已显示已暂停，红按钮却没变）----
        ck("E1 syncTestBtn 取 paused 分支",
                act.contains("boolean paused = on && WeakNet.I.paused"));
        ck("E2 暂停按钮文案含「已暂停」且点此停止",
                act.contains("⏸ 已暂停 · 点此停止"));
        ck("E3 暂停按钮换成橙色（区别于红色进行中/蓝色未开始）",
                act.contains("0xFFF5A623"));
        ck("E4 暂停文案不含「点此恢复」（点击行为仍是停止）",
                act.indexOf("点此恢复") < 0);

        System.out.println();
        System.out.println("  " + pass + " pass, " + fail + " fail");
        if (fail > 0) System.exit(1);
    }
}
