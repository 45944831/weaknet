package com.weaknet.app;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * 悬浮窗显示内容多选的纯 JDK 回归（设置界面勾选 → 有序 lines）。
 *
 * 背景：用户要在设置界面勾选决定飘窗显示哪几行。数据层 FloatSettings 早已支持
 * FIELDS + lines，但缺界面入口；本次补 UI，并把「勾选集合 → 稳定有序 lines」
 * 提取成 FloatSettings.orderedLines 供界面与测试共用。
 *
 * 注意：FloatSettings 用了 org.json（android.jar 里是 Stub），所以这里只调
 * 不碰 JSON 的纯静态方法（FIELDS / known / selectable / orderedLines / nameOf）。
 */
public class _FloatSettingsTest {
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

    static List<String> L(String... xs) {
        return new ArrayList<String>(Arrays.asList(xs));
    }

    public static void main(String[] args) {
        // ---- A 字段定义 ----
        ck("A1 FIELDS 非空且含可选项", FloatSettings.FIELDS.length > 1);
        ck("A2 known 认得预置行", FloatSettings.known("limit") && FloatSettings.known("rate")
                && FloatSettings.known("custom") && !FloatSettings.known("nope"));
        ck("A3 scene 固定标题行不可勾选", FloatSettings.known("scene")
                && !FloatSettings.selectable("scene"));
        ck("A4 普通行可勾选", FloatSettings.selectable("limit")
                && FloatSettings.selectable("rate") && FloatSettings.selectable("uptime"));
        ck("A5 nameOf 返回中文名", "实时速率".equals(FloatSettings.nameOf("rate")));

        // ---- B orderedLines：顺序稳定 / 去重 / 过滤 ----
        List<String> shuffled = L("uptime", "limit", "rate", "rate", "nope", "scene", "custom");
        List<String> ordered = FloatSettings.orderedLines(shuffled);
        String want = "[limit, rate, uptime, custom]";
        ck("B1 按 FIELDS 定义顺序，去重，过滤未知与 scene",
                ordered.toString().equals(want), ordered.toString());
        ck("B2 空勾选返回空列表（仅标题行）", FloatSettings.orderedLines(L("scene", "nope")).isEmpty());
        ck("B3 null 安全", FloatSettings.orderedLines(null).isEmpty());

        // ---- C 设置界面接线：渲染 CheckBox + 保存 + 即时生效 ----
        String sheet = src("src/com/weaknet/app/SettingsSheet.java");
        ck("C1 SettingsSheet 源码可读", sheet.length() > 0);
        ck("C2 渲染 CheckBox 多选", sheet.contains("new CheckBox(a)")
                && sheet.contains("selectable(id)"));
        ck("C3 勾选后保存并让飘窗重建",
                sheet.contains("FloatSettings.save(") && sheet.contains("FloatWindow.reload()"));
        ck("C4 用 orderedLines 收敛顺序", sheet.contains("FloatSettings.orderedLines("));
        ck("C5 外部改动时回填勾选框（带抑制标志防误触发）",
                sheet.contains("syncFloatFields()") && sheet.contains("syncingFloatFields"));

        // ---- D 自定义文本只从设置界面移除，底层能力保留（用户确认的范围）----
        ck("D1 设置界面跳过 custom 勾选项",
                sheet.contains("\"custom\".equals(id)") && sheet.contains("continue;"));
        ck("D2 提示文案不再提自定义文本",
                !sheet.contains("「自定义文本」需先在控制台写好内容"));
        ck("D3 底层 selectable(custom) 仍为真（API/脚本照常可用）",
                FloatSettings.selectable("custom"));
        ck("D4 orderedLines 仍保留 custom（底层未删）",
                FloatSettings.orderedLines(L("custom", "rate")).contains("custom"));

        System.out.println();
        System.out.println("  " + pass + " pass, " + fail + " fail");
        if (fail > 0) System.exit(1);
    }
}
