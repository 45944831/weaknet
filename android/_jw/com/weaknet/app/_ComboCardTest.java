package com.weaknet.app;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStreamReader;
import java.io.BufferedReader;

/**
 * 弱网组合列表排版单测（纯 JDK 可跑，不需要真机）。
 *
 * 回归背景：组合列表最早是整宽白色 Button，「🔀 名字」被 Button 默认居中，
 * 左边空一大片、五行卡片又散又高；后来改过一次左对齐信息行，仍然和
 * 「自定义场景」那排紧凑小方块不是一套样子。用户要求两者样式一致，
 * 现在是同一套 3 列等宽小方块。
 *
 * 这里守住三件事：摘要文案的换算与长度、网格与场景同参数、列表代码不退回
 * 居中整宽 Button / 彩色 emoji。
 */
public class _ComboCardTest {
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
        // ---- A 摘要：时长换算 ----
        ck("A1 分秒都在：2 分 30 秒 + 循环",
                ComboPlan.brief(3, 150000L, true).equals("3 段 · 2 分 30 秒 · 循环"),
                ComboPlan.brief(3, 150000L, true));
        ck("A2 整分不带零秒",
                ComboPlan.brief(2, 60000L, false).equals("2 段 · 1 分"),
                ComboPlan.brief(2, 60000L, false));
        ck("A3 不足一分只写秒",
                ComboPlan.brief(1, 5000L, false).equals("1 段 · 5 秒"));
        ck("A4 空组合也给得出文案",
                ComboPlan.brief(0, 0L, false).equals("0 段 · 0 秒"));
        ck("A5 负数 / 不足一秒不会写进界面",
                ComboPlan.brief(1, -1L, false).equals("1 段 · 0 秒")
                        && ComboPlan.brief(1, 999L, false).equals("1 段 · 0 秒"));
        ck("A6 十分钟上限级别的长组合",
                ComboPlan.brief(6, 600000L, true).equals("6 段 · 10 分 · 循环"));

        // ---- B 摘要：不能把界面撑破、不能掺 emoji ----
        String longest = ComboPlan.brief(99, 99L * 10 * 60000L, true);
        ck("B1 极端值下摘要仍然短、一行放得下", longest.length() <= 24, longest);
        ck("B2 摘要不含彩色 emoji",
                longest.indexOf('\uD83D') < 0 && longest.indexOf('\u2705') < 0);
        ck("B3 循环标记只在开启时出现",
                ComboPlan.brief(2, 30000L, true).endsWith(" · 循环")
                        && !ComboPlan.brief(2, 30000L, false).contains("循环"));

        // ---- C 布局：与「自定义场景」同一套网格 ----
        String sheet = src("src/com/weaknet/app/ComboSheet.java");
        String panels = src("src/com/weaknet/app/MainPanels.java");
        String gui = src("../weaknet_gui.py");
        ck("C1 组合列表源码可读", sheet.length() > 0, "ComboSheet.java");
        String shuffle = "\uD83D\uDD00";   // 🔀
        ck("C2 组合列表不再用「🔀 名字」的居中 Button",
                sheet.indexOf("setText(\"" + shuffle + " \" + c.name)") < 0);
        ck("C3 与场景同一网格密度：COLS = 3",
                sheet.contains("int COLS = 3") && panels.contains("int COLS = 3"));
        ck("C4 每个小方块等宽：weight 1f + 同色底同圆角 + 单行省略",
                sheet.contains("0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f")
                        && sheet.contains("box(0xFFF1F4FA, 0xFFD7DEEA, 8)")
                        && panels.contains("box(0xFFF1F4FA, 0xFFD7DEEA, 8)")
                        && panels.contains("setEllipsize(TextUtils.TruncateAt.END)"));
        ck("C5 新建按钮是紧凑小方块，不再拉整宽横条",
                sheet.contains("bNew.setText(\"＋ 新建组合\")")
                        && sheet.contains("head.addView(bNew);")
                        && sheet.indexOf("head.addView(bNew, new LinearLayout.LayoutParams(") < 0);
        ck("C6 选中态与场景一致：整块主色 + 白字",
                sheet.contains("e.getValue().setTextColor(on ? Color.WHITE : 0xFF2F3F52)")
                        && sheet.contains("box(on ? 0xFF1F8A70 : 0xFFF1F4FA"));
        ck("C7 段数/时长/循环改为选中后的摘要行（仍用 ComboPlan.brief）",
                sheet.contains("selInfo") && sheet.contains("ComboPlan.brief(")
                        && sheet.contains("已选「"));
        ck("C8 列表签名跟着段数/时长变化（编辑后摘要会刷新）",
                sheet.contains("totalMs(c)") && sheet.contains("c.loop"));
        ck("C9 单击选中、长按编辑两个入口都还在",
                sheet.contains("b.setContentDescription(\"组合 \" + c.name)")
                        && sheet.contains("pick(cur);") && sheet.contains("openEditor(c.id);"));
        ck("C10 Windows 端组合按钮同样去掉了 emoji、文字左对齐",
                gui.indexOf("text=c[\"name\"], font=FONT, bg=BTN2") >= 0
                        && gui.indexOf("anchor=\"w\"") >= 0);

        System.out.println();
        System.out.println("  " + pass + " pass, " + fail + " fail");
        if (fail > 0) System.exit(1);
    }
}
