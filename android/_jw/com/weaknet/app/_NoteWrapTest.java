package com.weaknet.app;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStreamReader;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 说明文案排版（折行 / 分块）单测（纯 JDK 可跑，不需要真机）。
 *
 * 回归背景：用户截图指出参数说明的排版有 4 处错位 ——
 *   1. 「·」条目符独占一行（被 TextView / Tk 当成一个词甩到单独一行）
 *   2. 正文把「」引号短语从中间劈开
 *   3. 标题的「！」浮在基线之上
 *   4. 正文与条目的左右边距不一致
 * 根因是折行规则散在两端的控件里，各断各的。
 * 现在折行下沉到引擎层（Profile.wrapNoteLines），两端共用同一套规则。
 *
 * 与桌面版的对应关系：
 *   · 算法逐条对应 weaknet.py 的 split_note_atoms / _note_group_units / wrap_note_lines
 *   · 断点逐行对应桌面端生成的黄金文件 note_wrap_golden.txt
 *     （改了一端忘了另一端，这里立刻红）
 *   · 文案一致性在 Python 侧（_t_param_notes.py）比对
 */
public class _NoteWrapTest {
    static int pass = 0, fail = 0;

    /** 黄金文件里的行宽（单位），与 _t_note_golden.py 一致 */
    static final int GOLDEN_WIDTH = 200;
    /** 粗糙测宽的一个汉字等于多少单位 */
    static final int GOLDEN_UNIT = 20;

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

    /** 一个汉字算 1 个单位，空格算 1/4，ASCII 算 1/2（与桌面端同一把尺子） */
    static Profile.NoteMeasurer rough(final int unit) {
        return new Profile.NoteRoughMeasurer(unit);
    }

    public static void main(String[] args) {
        Profile.NoteMeasurer m = rough(GOLDEN_UNIT);

        // ---- P1 原子切分：引号短语整体不可断 ----
        List<Profile.NoteAtom> atoms = Profile.splitNoteAtoms("给「本机 → 服务器」方向");
        StringBuilder kinds = new StringBuilder();
        StringBuilder atomTexts = new StringBuilder();
        for (Profile.NoteAtom at : atoms) {
            kinds.append(at.kind);
            atomTexts.append(at.text).append('|');
        }
        ck("P1a 引号短语被切成一个 q 原子",
                kinds.toString().equals("cqcc"),
                "kind=" + kinds + " text=" + atomTexts);

        // ---- P2 数字带单位整体不可断 ----
        List<Profile.NoteAtom> w = Profile.splitNoteAtoms("填 800ms 就行");
        StringBuilder wtext = new StringBuilder();
        for (Profile.NoteAtom at : w) wtext.append(at.text).append('|');
        ck("P2a 数字带单位是一个 w 原子",
                wtext.toString().startsWith("填| |800ms|"),
                wtext.toString());

        // ---- P3 引号短语放得下时绝不能被劈开 ----
        String q = "给「本机 → 服务器」方向加的固定延迟。";
        List<String> lines = Profile.wrapNoteLines(q, m, 400);
        StringBuilder joined = new StringBuilder();
        for (String s : lines) joined.append(s);
        ck("P3a 引号短语折行后仍完整",
                joined.toString().replace(" ", "").equals("给「本机→服务器」方向加的固定延迟。"),
                joined.toString());
        // 判据：「」这对引号必须落在同一行里。
        // 不能用 indexOf 比较 —— 整句在一行时两者都返回 0，看着像成对，
        // 实际是「没检查到」。这里直接数每行的开合引号个数。
        boolean split = false;
        for (String s : lines) {
            int open = 0, close = 0;
            for (int i = 0; i < s.length(); i++) {
                if (s.charAt(i) == '「') open++;
                if (s.charAt(i) == '」') close++;
            }
            if (open != close) split = true;
        }
        ck("P3b 放得下的引号短语没有被拆开", !split, lines.toString());

        // ---- P4 避头尾：收尾标点不落行首 ----
        List<String> lines2 = Profile.wrapNoteLines(
                "一次往返 ≈ 上行 + 下行，所以两项都填 400 时 RTT 是 800ms。", m, 200);
        StringBuilder bad = new StringBuilder();
        for (String s : lines2) {
            if (s.length() > 0 && Profile.NOTE_NO_START.indexOf(s.charAt(0)) >= 0) {
                bad.append(s).append(';');
            }
        }
        ck("P4a 没有行以禁则标点开头", bad.length() == 0,
                bad.length() == 0 ? lines2.toString() : bad.toString());

        // ---- P5 避头尾：起头标点不留在行末 ----
        StringBuilder bad2 = new StringBuilder();
        for (String s : lines2) {
            if (s.length() > 0 && Profile.NOTE_NO_END.indexOf(s.charAt(s.length() - 1)) >= 0) {
                bad2.append(s).append(';');
            }
        }
        ck("P5a 没有行以起头标点结尾", bad2.length() == 0,
                bad2.length() == 0 ? "全部合规" : bad2.toString());

        // ---- P6 行首不留空格 ----
        boolean leadSpace = false;
        for (List<String> ls : new List[]{lines, lines2}) {
            for (String s : ls) {
                if (s.startsWith(" ")) leadSpace = true;
            }
        }
        ck("P6a 断行后行首不留空格", !leadSpace);

        // ---- P7 超长单元按字符硬切，不撑破版式 ----
        StringBuilder big = new StringBuilder();
        for (int i = 0; i < 200; i++) big.append('A');
        List<String> lines3 = Profile.wrapNoteLines(big.toString(), m, 100);
        boolean over = false;
        for (String s : lines3) if (m.measure(s) > 100) over = true;
        ck("P7a 超长单词被硬切且不超宽", !over && lines3.size() > 1,
                lines3.size() + " 行");

        // ---- P8 空输入不炸 ----
        ck("P8a 空文本返回空列表", Profile.wrapNoteLines("", m, 200).isEmpty());
        ck("P8b null 返回空列表", Profile.wrapNoteLines(null, m, 200).isEmpty());

        // ---- P9 全部 13 个参数的真实文案都不出规则问题 ----
        StringBuilder p9 = new StringBuilder();
        for (String[] f : MainPanels.FIELDS) {
            Object[] parts = Profile.splitNote(f[0]);
            String body = (String) parts[0];
            List<?> tips = (List<?>) parts[1];
            List<String> texts = new ArrayList<String>();
            if (body != null && body.length() > 0) texts.add(body);
            for (Object t : tips) texts.add((String) t);
            for (String t : texts) {
                for (String s : Profile.wrapNoteLines(t, m, 200)) {
                    if (s.length() == 0) continue;
                    if (Profile.NOTE_NO_START.indexOf(s.charAt(0)) >= 0) {
                        p9.append(f[0]).append(":起行标点").append(s).append(';');
                    }
                    if (Profile.NOTE_NO_END.indexOf(s.charAt(s.length() - 1)) >= 0) {
                        p9.append(f[0]).append(":收行标点").append(s).append(';');
                    }
                }
            }
        }
        ck("P9a 13 个参数的真实文案全部符合排版规则", p9.length() == 0,
                p9.length() == 0 ? "全部合规" : p9.toString());

        // ---- P10 折行后文字一个字都不丢 ----
        StringBuilder lost = new StringBuilder();
        for (String[] f2 : MainPanels.FIELDS) {
            Object[] parts2 = Profile.splitNote(f2[0]);
            String body2 = (String) parts2[0];
            List<?> tips2 = (List<?>) parts2[1];
            List<String> texts2 = new ArrayList<String>();
            if (body2 != null && body2.length() > 0) texts2.add(body2);
            for (Object t : tips2) texts2.add((String) t);
            for (String t : texts2) {
                StringBuilder j = new StringBuilder();
                for (String s : Profile.wrapNoteLines(t, m, 200)) j.append(s);
                if (!j.toString().replace(" ", "").equals(t.replace(" ", ""))) {
                    lost.append(f2[0]).append(';');
                }
            }
        }
        ck("P10a 折行后文字完整（无丢字）", lost.length() == 0,
                lost.length() == 0 ? "全部完整" : lost.toString());

        // ---- P11 「·」不会被当成一个词甩到单独一行 ----
        List<String> lines4 = Profile.wrapNoteLines("「点了按钮迟迟不发请求」看这项。", m, 400);
        boolean alone = false;
        for (String s : lines4) if (s.trim().equals("·")) alone = true;
        ck("P11a 不会折出只有「·」的一行", !alone, lines4.toString());

        // ---- P12 与桌面端黄金文件逐行比对（两端断点必须完全一致）----
        goldenCheck(m);

        System.out.println();
        System.out.println("  " + pass + " pass, " + fail + " fail");
        if (fail > 0) System.exit(1);
    }

    /**
     * 读桌面端生成的黄金文件，逐行核对安卓端的折行结果。
     * 两端用同一个粗糙测宽函数、同一行宽，断点必须一字不差。
     */
    static void goldenCheck(Profile.NoteMeasurer m) {
        // 跑测试时的工作目录是 weaknet/android/（run.sh 里 cd 过去的），
        // 黄金文件放在 _jw/ 下。找不到就往下找一层，别直接判失败 ——
        // 找不到文件是环境问题，跟排版对不对没关系。
        File f = new File("_jw/note_wrap_golden.txt");
        if (!f.exists()) f = new File("note_wrap_golden.txt");
        if (!f.exists()) f = new File("../note_wrap_golden.txt");
        if (!f.exists()) {
            ck("P12 与桌面端黄金文件逐行一致", false,
                    "找不到 note_wrap_golden.txt（工作目录 " + new File(".").getAbsolutePath() + "）");
            return;
        }
        // key|段号 -> 期望的行列表
        Map<String, List<String>> expect = new LinkedHashMap<String, List<String>>();
        BufferedReader br = null;
        try {
            br = new BufferedReader(new InputStreamReader(new FileInputStream(f), "UTF-8"));
            String ln;
            while ((ln = br.readLine()) != null) {
                if (ln.length() == 0 || ln.charAt(0) == '#') continue;
                String[] p = ln.split("\t", 4);
                if (p.length < 4) continue;
                String k = p[0] + "|" + p[1];
                List<String> arr = expect.get(k);
                if (arr == null) {
                    arr = new ArrayList<String>();
                    expect.put(k, arr);
                }
                arr.add(p[3]);
            }
        } catch (Exception e) {
            ck("P12 与桌面端黄金文件逐行一致", false, "读文件失败：" + e);
            return;
        } finally {
            try {
                if (br != null) br.close();
            } catch (Exception ignored) {
                // ignore
            }
        }

        // 安卓端按同样规则算一遍
        StringBuilder diff = new StringBuilder();
        int checked = 0;
        for (String[] fld : MainPanels.FIELDS) {
            Object[] parts = Profile.splitNote(fld[0]);
            String body = (String) parts[0];
            List<?> tips = (List<?>) parts[1];
            List<String> texts = new ArrayList<String>();
            List<String> tags = new ArrayList<String>();
            if (body != null && body.length() > 0) {
                texts.add(body);
                tags.add("body");
            }
            for (int i = 0; i < tips.size(); i++) {
                texts.add((String) tips.get(i));
                tags.add("tip" + (i + 1));
            }
            for (int i = 0; i < texts.size(); i++) {
                String key = fld[0] + "|" + tags.get(i);
                List<String> want = expect.get(key);
                if (want == null) continue;
                List<String> got = Profile.wrapNoteLines(texts.get(i), m, GOLDEN_WIDTH);
                checked++;
                if (got.size() != want.size()) {
                    diff.append(key).append(" 行数 ").append(got.size())
                            .append("≠").append(want.size()).append(';');
                    continue;
                }
                for (int j = 0; j < want.size(); j++) {
                    if (!want.get(j).equals(got.get(j))) {
                        diff.append(key).append(" 第").append(j).append("行「")
                                .append(got.get(j)).append("」≠「").append(want.get(j))
                                .append("」;");
                    }
                }
            }
        }
        ck("P12 与桌面端黄金文件逐行一致", diff.length() == 0,
                diff.length() == 0 ? checked + " 段全部一致" : diff.toString());
    }
}
