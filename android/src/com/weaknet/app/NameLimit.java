package com.weaknet.app;

import android.text.InputFilter;
import android.text.Spanned;
import android.widget.EditText;

import java.util.List;

/**
 * 自定义场景 / 弱网组合的取名上限：来自 spec/spec.json 的 name_max
 * （由 tools/gen_spec.py 生成到 {@link SpecGen#NAME_MAX}，与 Windows 端同源）。
 *
 * 只管「用户起的名字」：内置预设的显示名最长 14 字（如「弱 Wi-Fi（隔墙/远端）」），
 * 设备昵称走 ShareNet.cleanName（24 字），共享段里的场景标签走 cleanScene（40 字），
 * 这三种都不能按这里的 10 字截，否则跨端按名字反查场景会对不上。
 *
 * 三层保证：输入框 filter 让人打不进第 11 个字；保存前 clean() 兜住 API /
 * 共享 / 出厂默认这些没有输入框的入口；读档时再 clean() 一遍，让老存档里
 * 的超长名字也收敛到同一个上限。
 */
public final class NameLimit {
    public static final int MAX = SpecGen.NAME_MAX;

    private NameLimit() {
    }

    /**
     * 按 Unicode 码点截断：一个汉字、一个 emoji 都算 1 个字符，
     * 与 Windows 端 Python 的 len() 一致（不能用 String.length()，
     * 那个把 emoji 算成 2 个 UTF-16 单元，还可能把代理对截成半个字符）。
     */
    public static String trim(String s, int max) {
        if (s == null || max <= 0) return "";
        int n = s.codePointCount(0, s.length());
        if (n <= max) return s;
        return s.substring(0, s.offsetByCodePoints(0, max));
    }

    /** 压空白 + 截到上限；空了就用 fallback（fallback 同样受上限约束） */
    public static String clean(String s, String fallback) {
        String t = s == null ? "" : s.replace('\r', ' ').replace('\n', ' ')
                .replace('\t', ' ').trim().replaceAll("\\s+", " ");
        t = trim(t, MAX).trim();
        if (t.length() > 0) return t;
        String f = fallback == null ? "" : fallback.trim();
        return trim(f, MAX).trim();
    }

    /** 重名加序号时也要守住上限：名字满了就先截短基础名，再拼 " (2)" */
    public static String unique(List<String> others, String name) {
        String base = clean(name, "");
        if (others == null || !others.contains(base)) return base;
        for (int n = 2; n < 100; n++) {
            String suffix = " (" + n + ")";
            String cand = trim(base, MAX - suffix.length()).trim() + suffix;
            if (!others.contains(cand)) return cand;
        }
        return clean(base + " " + System.currentTimeMillis(), base);
    }

    /**
     * 给取名输入框挂过滤器：键盘、粘贴、输入法上屏一概打不进第 11 个字。
     * 不用 InputFilter.LengthFilter，因为它按 UTF-16 单元算，emoji 会被算成 2 个字。
     */
    public static void apply(EditText et) {
        if (et == null) return;
        et.setSingleLine(true);
        et.setFilters(new InputFilter[]{new InputFilter() {
            public CharSequence filter(CharSequence src, int start, int end,
                                       Spanned dest, int dstart, int dend) {
                String before = dest.toString();
                String cur = before.substring(0, dstart) + before.substring(dend);
                String add = src.subSequence(start, end).toString();
                String clipped = clipInsert(cur, add);
                return clipped.equals(add) ? null : clipped;
            }
        }});
    }

    /**
     * 输入框「还能打几个字」的纯逻辑，从 filter 里拆出来好让纯 JDK 单测直接验，
     * 不必在 android.jar 的 stub 上真的去 new EditText。
     */
    static String clipInsert(String cur, String add) {
        String c = cur == null ? "" : cur;
        String a = add == null ? "" : add;
        int room = MAX - c.codePointCount(0, c.length());
        if (room <= 0) return "";
        return trim(a, room);
    }
}
