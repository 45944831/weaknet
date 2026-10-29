package com.weaknet.app;

import java.util.Locale;

import org.json.JSONObject;

/** 弱网参数模型 + 内置场景预设（规格来自 SpecGen ← spec/spec.json） */
public class Profile {
    public String name = SpecGen.DEFAULT_PROFILE_NAME;
    public int latencyUpMs = SpecGen.paramDefault("latency_up_ms");
    public int latencyDownMs = SpecGen.paramDefault("latency_down_ms");
    public int jitterMs = SpecGen.paramDefault("jitter_ms");
    public int downKbps = SpecGen.paramDefault("down_kbps");   // 0 = 不限
    public int upKbps = SpecGen.paramDefault("up_kbps");
    public double lossPct = SpecGen.paramDefault("loss_pct");
    public int stallMs = SpecGen.paramDefault("stall_ms");
    public double disconnectPct = SpecGen.paramDefault("disconnect_pct");
    public double timeoutPct = SpecGen.paramDefault("timeout_pct");
    public double dnsFailPct = SpecGen.paramDefault("dns_fail_pct");
    public double rejectPct = SpecGen.paramDefault("reject_pct");
    public int flapSec = SpecGen.paramDefault("flap_sec");
    public int flapBlackoutSec = SpecGen.paramDefault("flap_blackout_sec");

    // 2026-10 起去掉 3g / edge / gprs / dialup：这几种制式与拨号上网已退网，
    // 不再是有意义的测试档位；需要极慢网时直接用自定义参数压带宽。
    public static final String[] KEYS = SpecGen.PRESET_KEYS;

    /** key 是不是本机认识的某一档内置预设（别人推来的组合里可能有我们没有的档位） */
    public static boolean known(String key) {
        if (key == null) return false;
        for (String k : KEYS) {
            if (k.equals(key)) return true;
        }
        return false;
    }

    /** 按钮上的短名（手机屏窄，全名会把按钮撑出卡片；与桌面版 SHORT 保持一致） */
    public static String shortOf(String key) {
        return SpecGen.presetShort(key);
    }

    public static String catOf(String key) {
        return SpecGen.presetCat(key);
    }

    /** 档位注释；没有注释时返回空串（界面据此跳过这一行） */
    public static String noteOf(String key) {
        return SpecGen.presetNote(key);
    }

    /** 单个参数的说明；未知 key 返回空串（界面据此不显示「！」） */
    public static String noteOfParam(String key) {
        return SpecGen.paramNote(key);
    }

    // ---------------------------------------------------------------- 弱网组合说明
    //
    // 文案与桌面版 weaknet.py 的 COMBO_DOC 逐字一致：两者都来自 spec/spec.json 的
    // combo 段（安卓侧由 tools/gen_spec.py 生成到 SpecGen）。

    /** 块与块之间的分隔符（U+001F 单元分隔符，正常文本里不会出现） */
    public static final String COMBO_DOC_SEP = "";

    /** 例子条数（分界用：前 N 块是例子，之后是提醒） */
    public static final int COMBO_EXAMPLE_COUNT = SpecGen.COMBO_EXAMPLE_COUNT;

    /** 完整文档：第 1 块是机制，之后每 4 行一个例子，剩下的是提醒 */
    public static final String COMBO_DOC = SpecGen.COMBO_DOC;

    /**
     * 把文档串拆成三块：说明、例子列表、提醒列表。
     *
     * 例子每条 4 行：名字 / 一句话说明 / 效果 / 怎么排。
     * 规则与桌面版 combo_doc_parts 一致 —— 两端拆法必须一样，
     * 不然同一个组合在电脑上和在手机上看到的排版会不同。
     */
    public static Object[] comboDocParts() {
        String[] blocks = COMBO_DOC.split(COMBO_DOC_SEP);
        java.util.List<String> examples = new java.util.ArrayList<String>();
        java.util.List<String> tips = new java.util.ArrayList<String>();
        String intro = blocks.length > 0 ? blocks[0] : "";
        for (int i = 1; i < blocks.length; i++) {
            if (i <= COMBO_EXAMPLE_COUNT) {
                String[] lines = blocks[i].split("\n", -1);
                if (lines.length >= 4) {
                    examples.add(lines[0] + "\n" + lines[1] + "\n"
                            + lines[2] + "\n" + lines[3]);
                }
            } else {
                tips.add(blocks[i]);
            }
        }
        return new Object[]{intro, examples, tips};
    }


    /**
     * 把说明拆成「作用」与若干条「注意」。
     *
     * 规则与桌面版 weaknet.py 的 split_param_note 完全一致（两端文案逐字相同，
     * 切分规则也必须一致，不然同一个参数在两端会长得不一样）：
     * 第一段讲这项干什么，第二段按句号切成一条条提醒。
     *
     * @return 第一个元素是作用段，第二个是提醒条目（没有提醒时为空数组）
     */
    public static Object[] splitNote(String key) {
        String note = noteOfParam(key);
        String[] out = new String[1];
        java.util.List<String> tips = new java.util.ArrayList<String>();
        if (note == null || note.length() == 0) {
            out[0] = "";
            return new Object[]{out[0], tips};
        }
        String[] parts = note.split("\n");
        java.util.List<String> segs = new java.util.ArrayList<String>();
        for (int i = 0; i < parts.length; i++) {
            String p = parts[i].trim();
            if (p.length() > 0) segs.add(p);
        }
        if (segs.isEmpty()) {
            out[0] = "";
            return new Object[]{out[0], tips};
        }
        if (segs.size() == 1) {
            java.util.List<String> sents = splitSentences(segs.get(0));
            out[0] = sents.isEmpty() ? segs.get(0) : sents.get(0);
            for (int i = 1; i < sents.size(); i++) tips.add(sents.get(i));
            return new Object[]{out[0], tips};
        }
        out[0] = segs.get(0);
        StringBuilder rest = new StringBuilder();
        for (int i = 1; i < segs.size(); i++) {
            if (rest.length() > 0) rest.append(" ");
            rest.append(segs.get(i));
        }
        tips.addAll(splitSentences(rest.toString()));
        return new Object[]{out[0], tips};
    }

    /** 按中文句号 / 问号 / 感叹号切句，保留标点 */
    private static java.util.List<String> splitSentences(String text) {
        java.util.List<String> out = new java.util.ArrayList<String>();
        if (text == null) return out;
        StringBuilder buf = new StringBuilder();
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            buf.append(ch);
            if (ch == '。' || ch == '！' || ch == '？') {
                String s = buf.toString().trim();
                if (s.length() > 0) out.add(s);
                buf.setLength(0);
            }
        }
        String tail = buf.toString().trim();
        if (tail.length() > 0) out.add(tail);
        return out;
    }

    /** 参数取值上限（与桌面版同一份 SPEC 的 max） */
    public static int limitOfParam(String key) {
        return SpecGen.paramMax(key);
    }

    /** 参数单位（如「下行带宽 (kbps)」-> kbps） */
    public static String unitOfParam(String key) {
        return SpecGen.paramUnit(key);
    }

    // ---------------------------------------------------------------- 说明文案的排版
    //
    // 与桌面版 weaknet.py 的同名逻辑逐条对应（split_note_atoms /
    // _note_group_units / wrap_note_lines）。两端必须一模一样：
    // 同一个参数在电脑上和手机上会长得不一样，是最难解释的一类 bug。
    //
    // 为什么不能直接用 TextView 的自动换行：安卓的断行规则跟桌面端不同，
    // 同一个文案在两端会被断在不同的位置；而且「·」这种条目符会被甩到单独一行，
    // 「「本机 → 服务器」」这种引号短语会被从中间劈开。
    // 所以两端都把文字先切成「不可再分的原子」，再按像素宽贪心拼行。
    //
    // 原子的四种：
    //   c  单个字符（汉字 / 中文标点 / 独立符号），可以在它前后断行
    //   q  引号短语（「」「『』“”《》整段不可断）
    //   w  英文单词 / 数字带单位（800ms、0.5~1.5、RTT 整段不可断）
    //   s  空白（只用来标记「这里原本有空格」，断开时丢掉）
    //
    // 断行时还做两件中文排版的事：
    //   避头：收尾标点（，。、）」等）不另起一行 —— 把它留在上一行
    //   避尾：起头标点（（《等）不留在行末 —— 把它带到下一行

    /** 引号配对（开 -> 闭） */
    private static final String NOTE_QUOTE_OPEN = "「『“《";
    private static final String NOTE_QUOTE_CLOSE = "」』”》";

    /** 行首禁则：这些标点不能出现在一行的开头 */
    public static final String NOTE_NO_START = "，。、；：？！）」』】”’%‰°′″℃…";

    /** 行尾禁则：这些标点不能出现在一行的末尾 */
    public static final String NOTE_NO_END = "（「『【《“";

    /** 不能孤零零出现在行首的符号：数学 / 箭头符号 */
    public static final String NOTE_NO_START_GLUE = "≈→←±×÷·〜～";

    /** 数字 / 单词内部的连接符（0.5~1.5 里的 . ~ ，800ms% 里的 %） */
    private static final String NOTE_WORD_CONNECT = ".-~_%+/";

    /** 视为「空白」的字符（断行时空格丢弃，行内保留一个） */
    private static final String NOTE_SPACE_CHARS = " \t　";

    /** 一个排版原子 */
    static final class NoteAtom {
        final String kind;   // c / q / w / s
        final String text;
        NoteAtom(String kind, String text) {
            this.kind = kind;
            this.text = text;
        }
    }

    /** 一个排版单元：行内不可再分的一段文字 */
    static final class NoteUnit {
        final String text;
        final boolean sep;   // 前面有没有空格
        NoteUnit(String text, boolean sep) {
            this.text = text;
            this.sep = sep;
        }
    }

    /** 是不是中日韩字符 / 中文标点（含全角形式） */
    private static boolean isNoteCjk(char ch) {
        int code = ch;
        return (code >= 0x2E80 && code <= 0x9FFF)
                || (code >= 0xF900 && code <= 0xFAFF)
                || (code >= 0xFF00 && code <= 0xFFEF);
    }

    /** 是不是 ASCII 的字母 / 数字 */
    private static boolean isAsciiAlnum(char ch) {
        return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z')
                || (ch >= '0' && ch <= '9');
    }

    /**
     * s.charAt(i) 能不能算「英文单词 / 数字」的一部分。
     * 字母数字当然算；连接符只在两边都是字母数字时才算，
     * 这样 0.5~1.5 是一个整体，而「上行 + 下行」里的 + 是独立符号。
     */
    private static boolean isNoteWordChar(String s, int i) {
        char ch = s.charAt(i);
        if (isAsciiAlnum(ch)) return true;
        if (NOTE_WORD_CONNECT.indexOf(ch) >= 0) {
            boolean prevAlnum = i > 0 && isAsciiAlnum(s.charAt(i - 1));
            boolean nextAlnum = i + 1 < s.length() && isAsciiAlnum(s.charAt(i + 1));
            return prevAlnum && nextAlnum;
        }
        return false;
    }

    /**
     * 把一段说明文字切成不可再分的排版原子。
     * 返回 [kind, text] 的列表，kind 取值 c / q / w / s。
     * 折行只能发生在原子与原子之间，绝不会把一个原子劈成两半 ——
     * 「「本机 → 服务器」」里的箭头和空格因此永远跟参数名待在一起。
     */
    static java.util.List<NoteAtom> splitNoteAtoms(String text) {
        java.util.List<NoteAtom> out = new java.util.ArrayList<NoteAtom>();
        if (text == null) return out;
        int i = 0;
        int n = text.length();
        while (i < n) {
            char ch = text.charAt(i);
            if (NOTE_SPACE_CHARS.indexOf(ch) >= 0) {
                int j = i;
                while (j < n && NOTE_SPACE_CHARS.indexOf(text.charAt(j)) >= 0) j++;
                out.add(new NoteAtom("s", text.substring(i, j)));
                i = j;
                continue;
            }
            int oi = NOTE_QUOTE_OPEN.indexOf(ch);
            if (oi >= 0) {
                char close = NOTE_QUOTE_CLOSE.charAt(oi);
                int j = text.indexOf(close, i + 1);
                if (j != -1) {
                    out.add(new NoteAtom("q", text.substring(i, j + 1)));
                    i = j + 1;
                    continue;
                }
            }
            if (isNoteCjk(ch)) {
                out.add(new NoteAtom("c", String.valueOf(ch)));
                i++;
                continue;
            }
            if (isAsciiAlnum(ch)) {
                int j = i;
                while (j < n && isNoteWordChar(text, j)) j++;
                // 百分号跟在词尾（100%）也算这个词的一部分
                if (j < n && text.charAt(j) == '%') j++;
                out.add(new NoteAtom("w", text.substring(i, j)));
                i = j;
                continue;
            }
            // 其它 ASCII 符号（= + % 等）单独成原子
            out.add(new NoteAtom("c", String.valueOf(ch)));
            i++;
        }
        return out;
    }

    /**
     * 原子表 -> 排版单元表。
     * 这里顺手做了中文排版的两条「避头尾」规则 —— 不做的话，断行处会出现
     * 孤零零的标点（比如一行的开头是「，」）：
     *   行首禁则（，。、；：？！）等）不能起行 -> 粘到前一个单元末尾
     *   行尾禁则（（《 等）不能收行 -> 粘到后一个单元开头
     * 合成之后，断行只发生在单元之间，标点自然跟着前面的字走。
     */
    static java.util.List<NoteUnit> noteGroupUnits(java.util.List<NoteAtom> atoms) {
        java.util.List<NoteUnit> units = new java.util.ArrayList<NoteUnit>();
        boolean sep = false;              // 上一段和这一段之间有没有空格
        StringBuilder hold = new StringBuilder();   // 待与下一个单元合并的起头标点
        boolean holdSep = false;
        for (NoteAtom a : atoms) {
            String kind = a.kind;
            String t = a.text;
            if ("s".equals(kind)) {
                sep = true;
                continue;
            }
            if ("c".equals(kind) && t.length() == 1) {
                char ch = t.charAt(0);
                if (NOTE_NO_END.indexOf(ch) >= 0) {
                    // 起头标点不能收在行末 —— 先攒着，跟下一个单元走
                    hold.append(ch);
                    holdSep = sep;
                    sep = false;
                    continue;
                }
                if (hold.length() > 0) {
                    t = hold.toString() + ch;
                    sep = holdSep;
                    hold.setLength(0);
                    holdSep = false;
                }
                if (NOTE_NO_START.indexOf(ch) >= 0 || NOTE_NO_START_GLUE.indexOf(ch) >= 0) {
                    if (!units.isEmpty()) {
                        // 收尾标点不能起行 —— 粘到上一个单元末尾
                        NoteUnit last = units.get(units.size() - 1);
                        units.set(units.size() - 1,
                                new NoteUnit(last.text + (sep ? " " : "") + ch, last.sep));
                        sep = false;
                        continue;
                    }
                }
                units.add(new NoteUnit(t, sep));
                sep = false;
                continue;
            }
            // 引号短语 / 英文单词：整体不可断
            if (hold.length() > 0) {
                t = hold.toString() + t;
                sep = holdSep;
                hold.setLength(0);
                holdSep = false;
            }
            units.add(new NoteUnit(t, sep));
            sep = false;
        }
        if (hold.length() > 0) {
            units.add(new NoteUnit(hold.toString(), holdSep));
        }
        return units;
    }

    /** 一行的宽度：行首单元不算它前面的空格 */
    private static int noteUnitsWidth(java.util.List<NoteUnit> units, NoteMeasurer measurer) {
        int total = 0;
        for (int i = 0; i < units.size(); i++) {
            NoteUnit u = units.get(i);
            if (i > 0 && u.sep) total += measurer.measure(" ");
            total += measurer.measure(u.text);
        }
        return total;
    }

    /** 单元表 -> 一行文字（行首不留空格，行内空格保留） */
    private static String noteUnitsText(java.util.List<NoteUnit> units) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < units.size(); i++) {
            NoteUnit u = units.get(i);
            if (i > 0 && u.sep) sb.append(' ');
            sb.append(u.text);
        }
        return sb.toString();
    }

    /**
     * 把一个单元切成「行内安全的最小块」。
     * 单元宽过整行时只能硬切，但切之前先按避头尾规则重新分块：
     *   · 收尾标点（，。、；：？！）等）粘到前一块末尾 —— 它不能起行
     *   · 起头标点（（《 等）粘到后一块开头 —— 它不能收行
     * 切完之后，断行只发生在块与块之间，标点自然跟着前面的字走。
     * 少了这一步，窄屏上就会出现「」。」这种一行开头的怪事
     * （实测：引号短语被硬切后，收尾引号正好落在下一行开头）。
     */
    private static java.util.List<String> noteSafeChunks(String text) {
        java.util.List<String> chunks = new java.util.ArrayList<String>();
        StringBuilder hold = new StringBuilder();   // 待与下一块合并的起头标点
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (NOTE_NO_END.indexOf(ch) >= 0) {
                hold.append(ch);
                continue;
            }
            String piece;
            if (hold.length() > 0) {
                piece = hold.toString() + ch;
                hold.setLength(0);
            } else {
                piece = String.valueOf(ch);
            }
            char last = piece.charAt(piece.length() - 1);
            if (NOTE_NO_START.indexOf(last) >= 0 && !chunks.isEmpty()) {
                chunks.set(chunks.size() - 1, chunks.get(chunks.size() - 1) + piece);
                continue;
            }
            chunks.add(piece);
        }
        if (hold.length() > 0) {
            if (!chunks.isEmpty()) {
                chunks.set(chunks.size() - 1, chunks.get(chunks.size() - 1) + hold);
            } else {
                chunks.add(hold.toString());
            }
        }
        return chunks;
    }

    /**
     * 把宽过整行的单元按「安全块」硬切。
     * 超长英文词 / 超长引号短语在正常文案里不会出现，但真出现了也不能把
     * 版式撑破 —— 宁可切碎，也不能让文字溢出卡片。
     */
    private static java.util.List<NoteUnit> noteHardSplit(java.util.List<NoteUnit> units,
                                                          NoteMeasurer measurer, int maxWidth) {
        java.util.List<NoteUnit> out = new java.util.ArrayList<NoteUnit>();
        for (NoteUnit u : units) {
            if (measurer.measure(u.text) <= maxWidth) {
                out.add(u);
                continue;
            }
            boolean sp = u.sep;
            StringBuilder cur = new StringBuilder();
            for (String ch : noteSafeChunks(u.text)) {
                if (cur.length() > 0 && measurer.measure(cur.toString() + ch) > maxWidth) {
                    out.add(new NoteUnit(cur.toString(), sp));
                    sp = false;
                    cur.setLength(0);
                    cur.append(ch);
                } else {
                    cur.append(ch);
                }
            }
            if (cur.length() > 0) out.add(new NoteUnit(cur.toString(), sp));
        }
        return out;
    }

    /**
     * 把一段说明文字按宽度折成若干行。
     *
     * measurer : 能量出一段文字有多宽（安卓端用 Paint 测量）
     * maxWidth : 每行最多允许多宽（像素）
     *
     * 规则（桌面版 weaknet.wrap_note_lines 完全一致）：
     *   · 引号短语、英文单词、数字带单位整体不可拆开
     *   · 断行只发生在排版单元之间；行首不留空格
     *   · 收尾标点不另起一行，起头标点不留在行末（避头尾）
     *   · 单个单元宽过整行时按字符硬切，不撑破版式
     */
    static java.util.List<String> wrapNoteLines(String text, NoteMeasurer measurer, int maxWidth) {
        java.util.List<String> lines = new java.util.ArrayList<String>();
        if (text == null || text.length() == 0) return lines;
        java.util.List<NoteUnit> units = noteHardSplit(noteGroupUnits(splitNoteAtoms(text)),
                measurer, maxWidth);
        if (units.isEmpty()) return lines;
        java.util.List<NoteUnit> cur = new java.util.ArrayList<NoteUnit>();
        int curW = 0;
        for (NoteUnit u : units) {
            int w = measurer.measure(u.text);
            int add = ((u.sep && !cur.isEmpty()) ? measurer.measure(" ") : 0) + w;
            if (!cur.isEmpty() && curW + add > maxWidth) {
                lines.add(noteUnitsText(cur));
                cur = new java.util.ArrayList<NoteUnit>();
                curW = 0;
                add = w;                 // 断行后行首不留空格
            }
            cur.add(u);
            curW += add;
        }
        if (!cur.isEmpty()) lines.add(noteUnitsText(cur));
        return lines;
    }

    /** 文字测宽器：安卓端用 Paint 实现，纯逻辑测试里可以自己实现一个 */
    interface NoteMeasurer {
        int measure(String text);
    }

    /**
     * 文字测宽器：按「汉字算 1 个单位、ASCII 算 0.5 个单位」估算。
     * 纯逻辑测试用（不依赖 Android 的 Paint，能在 JVM 上跑）。
     * 界面上要精确排版时由 ParamSheet 传入一个基于 Paint 的实现。
     */
    static class NoteRoughMeasurer implements NoteMeasurer {
        private final int unit;

        NoteRoughMeasurer(int unit) {
            this.unit = unit;
        }

        public int measure(String text) {
            if (text == null) return 0;
            int w = 0;
            for (int i = 0; i < text.length(); i++) {
                char ch = text.charAt(i);
                if (ch == ' ') w += unit / 4;
                else if (isNoteCjk(ch)) w += unit;
                else w += unit / 2;
            }
            return w;
        }
    }

    /**
     * 内置预设 -> Profile。名字与参数都来自 SpecGen（spec/spec.json 的 presets），
     * 未知 key 回落到默认的「直连」。
     */
    public static Profile build(String key) {
        Profile p = new Profile();
        if (!SpecGen.hasPreset(key)) return p;
        p.name = SpecGen.presetName(key);
        SpecGen.applyPreset(key, p);
        return p;
    }

    /** 从自定义参数 JSON 构建（字段缺失则保持默认，越界值夹到 spec 的 max） */
    public static Profile custom(JSONObject o) {
        Profile p = new Profile();
        p.name = "自定义";
        p.apply(o);
        return p;
    }

    /** 只覆盖 JSON 里出现的字段；夹取上限来自 spec，避免两端各写一套 */
    private void apply(JSONObject o) {
        if (o == null) return;
        latencyUpMs = num(o.optInt("latency_up_ms", latencyUpMs), SpecGen.paramMax("latency_up_ms"));
        latencyDownMs = num(o.optInt("latency_down_ms", latencyDownMs), SpecGen.paramMax("latency_down_ms"));
        jitterMs = num(o.optInt("jitter_ms", jitterMs), SpecGen.paramMax("jitter_ms"));
        downKbps = num(o.optInt("down_kbps", downKbps), SpecGen.paramMax("down_kbps"));
        upKbps = num(o.optInt("up_kbps", upKbps), SpecGen.paramMax("up_kbps"));
        stallMs = num(o.optInt("stall_ms", stallMs), SpecGen.paramMax("stall_ms"));
        flapSec = num(o.optInt("flap_sec", flapSec), SpecGen.paramMax("flap_sec"));
        flapBlackoutSec = num(o.optInt("flap_blackout_sec", flapBlackoutSec),
                SpecGen.paramMax("flap_blackout_sec"));
        lossPct = pct(o.optDouble("loss_pct", lossPct));
        disconnectPct = pct(o.optDouble("disconnect_pct", disconnectPct));
        timeoutPct = pct(o.optDouble("timeout_pct", timeoutPct));
        dnsFailPct = pct(o.optDouble("dns_fail_pct", dnsFailPct));
        rejectPct = pct(o.optDouble("reject_pct", rejectPct));
    }

    /**
     * 百分比字段夹到 0~100。999% 丢包实际就是「全丢」，和直连故障看不出区别，
     * 用户填的时候多半是想写 99% 或漏看了输入框上限。
     */
    private static double pct(double v) {
        if (v != v) return 0;                 // NaN
        return v < 0 ? 0 : (v > 100 ? 100 : v);
    }

    private static int num(int v, int max) {
        return v < 0 ? 0 : (v > max ? max : v);
    }

    /**
     * 深拷贝。自定义场景每次应用都要给出独立实例：
     * 直接复用存下来的那份，后续任何原地修改都会连带改掉「已保存的参数」。
     */
    public Profile copy() {
        Profile p = Profile.custom(this.toJson());
        p.name = name;
        return p;
    }

    public JSONObject toJson() {
        JSONObject o = new JSONObject();
        try {
            o.put("name", name);
            o.put("latency_up_ms", latencyUpMs);
            o.put("latency_down_ms", latencyDownMs);
            o.put("jitter_ms", jitterMs);
            o.put("down_kbps", downKbps);
            o.put("up_kbps", upKbps);
            o.put("loss_pct", lossPct);
            o.put("stall_ms", stallMs);
            o.put("disconnect_pct", disconnectPct);
            o.put("timeout_pct", timeoutPct);
            o.put("dns_fail_pct", dnsFailPct);
            o.put("reject_pct", rejectPct);
            o.put("flap_sec", flapSec);
            o.put("flap_blackout_sec", flapBlackoutSec);
        } catch (Exception e) {
            // ignore
        }
        return o;
    }

    /**
     * 与全默认（直连）相比，这组参数是否真的会「动」流量。
     * stallMs 默认 600ms，本身不构成限制，不参与比较。
     */
    public boolean hasLimits() {
        Profile d = new Profile();
        return latencyUpMs != d.latencyUpMs || latencyDownMs != d.latencyDownMs
                || jitterMs != d.jitterMs || downKbps != d.downKbps || upKbps != d.upKbps
                || lossPct != d.lossPct || disconnectPct != d.disconnectPct
                || timeoutPct != d.timeoutPct || dnsFailPct != d.dnsFailPct
                || rejectPct != d.rejectPct || flapSec != d.flapSec;
    }

    public String summary() {
        return String.format(Locale.getDefault(),
                "延迟 %d/%dms 带宽 %s/%s kbps 丢包 %.0f%%",
                latencyUpMs, latencyDownMs,
                downKbps == 0 ? "∞" : String.valueOf(downKbps),
                upKbps == 0 ? "∞" : String.valueOf(upKbps), lossPct);
    }

    /**
     * 参数全量摘要（含抖动 / 卡顿 / 周期断网），给「场景说明」用。
     *
     * 与 {@link #summary()} 分开：那个是列表里的一行简述，越长越挤；这里是详情页，
     * 用户点进来就是想搞清「这支档位到底设了什么」，宁可全一点。
     */
    public String detail() {
        StringBuilder sb = new StringBuilder();
        if (downKbps != 0 || upKbps != 0) {
            add(sb, "下行 " + (downKbps == 0 ? "∞" : String.valueOf(downKbps))
                    + " / 上行 " + (upKbps == 0 ? "∞" : String.valueOf(upKbps)) + " kbps");
        }
        if (latencyDownMs != 0 || latencyUpMs != 0) {
            add(sb, "延迟 " + latencyDownMs + "/" + latencyUpMs + " ms（下/上）");
        }
        if (jitterMs != 0) add(sb, "抖动 ±" + jitterMs + " ms");
        if (lossPct > 0) {
            add(sb, "丢包 " + pctText(lossPct) + "%");
            // 卡顿时长只在丢包后生效（模拟 TCP 重传等待），单独列出来没意义
            if (stallMs > 0) add(sb, "丢包后停顿 " + stallMs + " ms");
        }
        if (disconnectPct > 0) add(sb, "随机断连 " + pctText(disconnectPct) + "%");
        if (timeoutPct > 0) add(sb, "黑洞 " + pctText(timeoutPct) + "%");
        if (dnsFailPct > 0) add(sb, "DNS 故障 " + pctText(dnsFailPct) + "%");
        if (rejectPct > 0) add(sb, "拒连 " + pctText(rejectPct) + "%");
        if (flapSec > 0) add(sb, "每 " + flapSec + "s 断网 " + flapBlackoutSec + "s");
        return sb.length() == 0 ? "（不限制：所有流量原样直通）" : sb.toString();
    }

    private static void add(StringBuilder sb, String part) {
        if (sb.length() > 0) sb.append(" · ");
        sb.append(part);
    }

    /** 百分比去掉多余的小数位：4.0 → "4"，0.5 → "0.5" */
    private static String pctText(double v) {
        if (v == Math.rint(v)) return String.valueOf((int) v);
        return String.valueOf(Math.round(v * 10) / 10.0);
    }
}
