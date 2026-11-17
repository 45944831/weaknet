package com.weaknet.app;

/**
 * 参数数值解析单测（纯 JDK 可跑）。
 *
 * 回归背景（2026-11-16，v1.1.19）：Profile.apply 曾用 {@code JSONObject.optInt}。
 * 该实现把 Number 先截成 int 再和原值比较，2147483648 / 3000000000 这类
 * 「整数型但超 int 上限」的值截成负数后比较失败，回落到默认值 0：
 *   {"down_kbps": 3000000000} → down=0（不限速），接口却返回 ok:true
 * 而 Windows 的 _norm_user_profile 是 min(max(0,v), 上限) → 1000000。
 * 采集脚本若按 10 Gbps 下发，Android 上会静默测到直连流量，前提全错。
 *
 * 现在统一走 clampNum(Object, def, max)，这里锁住：
 *   * 超 int 上限（Long / Double）必须夹到 max，不能变 0；
 *   * 负值归 0、NaN/非数值/布尔回默认值；
 *   * 数字字符串仍被接受（org.json 原本就会解析 "5000"）。
 */
public class _ValueClampTest {
    static int pass = 0, fail = 0;
    static final int MAX = 1000000;      // spec 的 down_kbps 上限
    static final int DEF = 777;          // 用非零默认值，才能区分「回默认」和「归 0」

    static void ck(String name, boolean ok, String extra) {
        if (ok) { pass++; System.out.println("  PASS  " + name); }
        else { fail++; System.out.println("  FAIL  " + name + "  <- " + extra); }
    }

    static int clamp(Object raw) {
        return Profile.clampNum(raw, DEF, MAX);
    }

    public static void main(String[] a) {
        // 1) 常规值原样保留（不能被无谓压低或抬到上限）
        ck("Integer 5000 → 5000", clamp(Integer.valueOf(5000)) == 5000,
           "" + clamp(Integer.valueOf(5000)));
        ck("上限本身 1000000 原样通过", clamp(Long.valueOf(1000000L)) == MAX,
           "" + clamp(Long.valueOf(1000000L)));

        // 2) 核心回归：整数型超 int 上限不得变 0
        ck("Long 2147483648 → 1000000（旧实现给 0）",
           clamp(Long.valueOf(2147483648L)) == MAX,
           "" + clamp(Long.valueOf(2147483648L)));
        ck("Long 3000000000 → 1000000（旧实现给 0）",
           clamp(Long.valueOf(3000000000L)) == MAX,
           "" + clamp(Long.valueOf(3000000000L)));
        ck("Long 10000000000 → 1000000（10 Gbps 写法）",
           clamp(Long.valueOf(10000000000L)) == MAX,
           "" + clamp(Long.valueOf(10000000000L)));
        ck("Long.MAX_VALUE 不溢出、夹到上限",
           clamp(Long.valueOf(Long.MAX_VALUE)) == MAX,
           "" + clamp(Long.valueOf(Long.MAX_VALUE)));

        // 3) 浮点同上（1e18 旧实现恰好靠 int 饱和碰对，这里一并锁住）
        ck("Double 1e18 → 1000000", clamp(Double.valueOf(1e18)) == MAX,
           "" + clamp(Double.valueOf(1e18)));
        ck("Double 2147483648.0 → 1000000",
           clamp(Double.valueOf(2147483648.0)) == MAX,
           "" + clamp(Double.valueOf(2147483648.0)));

        // 4) 小数按 Windows 的 int() 截断语义
        ck("Double 5000.9 → 5000", clamp(Double.valueOf(5000.9)) == 5000,
           "" + clamp(Double.valueOf(5000.9)));
        ck("Double 0.9 → 0", clamp(Double.valueOf(0.9)) == 0,
           "" + clamp(Double.valueOf(0.9)));

        // 5) 越界/非法：负值归 0，NaN/文本/布尔回默认值
        ck("Integer -5 → 0", clamp(Integer.valueOf(-5)) == 0,
           "" + clamp(Integer.valueOf(-5)));
        ck("Double -1e18 → 0", clamp(Double.valueOf(-1e18)) == 0,
           "" + clamp(Double.valueOf(-1e18)));
        ck("Double.NaN → 默认值", clamp(Double.valueOf(Double.NaN)) == DEF,
           "" + clamp(Double.valueOf(Double.NaN)));
        ck("Double.POSITIVE_INFINITY → 上限",
           clamp(Double.valueOf(Double.POSITIVE_INFINITY)) == MAX,
           "" + clamp(Double.valueOf(Double.POSITIVE_INFINITY)));
        ck("字符串 abc → 默认值", clamp("abc") == DEF, "" + clamp("abc"));
        ck("null → 默认值", clamp(null) == DEF, "" + clamp(null));
        ck("布尔 true → 默认值（不当作 1）", clamp(Boolean.TRUE) == DEF,
           "" + clamp(Boolean.TRUE));

        // 6) 数字字符串：org.json 原本接受 "5000"，修完不能反而丢掉这个能力
        ck("字符串 \"5000\" → 5000", clamp("5000") == 5000, "" + clamp("5000"));
        ck("字符串 \" 42 \" 去空格 → 42", clamp(" 42 ") == 42, "" + clamp(" 42 "));
        ck("字符串 \"3000000000\" → 上限", clamp("3000000000") == MAX,
           "" + clamp("3000000000"));

        // 7) 小上限（如 loss 之外的 flap_sec=3600）也要按 max 夹，不是按 int 夹
        ck("max=3600 时 9999 → 3600",
           Profile.clampNum(Integer.valueOf(9999), 0, 3600) == 3600,
           "" + Profile.clampNum(Integer.valueOf(9999), 0, 3600));

        // 8) 单调性：0..1e10 采样，结果不得随输入增大而变小
        boolean mono = true;
        String bad = "";
        int prev = -1;
        for (long v = 0; v <= 10000000000L; v += 137000000L) {
            int cur = Profile.clampNum(Long.valueOf(v), 0, MAX);
            if (cur < prev) { mono = false; bad = v + " -> " + cur + " < " + prev; break; }
            prev = cur;
        }
        ck("clampNum 单调不减", mono, bad);

        System.out.println("\n_ValueClampTest: pass=" + pass + " fail=" + fail);
        if (fail > 0) System.exit(1);
    }
}
