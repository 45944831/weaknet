package com.weaknet.app;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * 悬浮窗（限速飘窗）的显示设置：显示哪几行、顺序、自定义文本、字号、透明度。
 *
 * 存 SharedPreferences，与自定义场景（MyPresets）同一套做法：
 * 卸载 App 才丢，读写由系统保证原子性。
 *
 * 所有外部输入都要过 {@link #sanitize}：控制台页面可能被旧版本引擎服务，
 * 不能假设传进来的字段 id 一定认识、数值一定在合理区间。
 */
public class FloatSettings {
    // ---- 可选显示行：id / 名称 / 说明 / 示例 ----
    public static final String[][] FIELDS = {
            {"scene",  "场景名",     "当前弱网场景；周期断网时追加「· 断网中」", "地铁 5G"},
            {"limit",  "限速值",     "上下行限速，0 = 不限",                     "限速 ↓1.5M ↑512k"},
            {"rate",   "实时速率",   "每 500ms 实测的上下行速度",               "实时 ↓120KB/s ↑8KB/s"},
            {"loss",   "弱网参数",   "延迟 / 抖动 / 丢包",                       "延迟 120±30ms · 丢包 3%"},
            {"conns",  "连接数",     "活跃 / 累计连接",                          "连接 12 / 348"},
            {"total",  "累计流量",   "接管以来的上下行总量",                     "累计 ↓18.4MB ↑2.1MB"},
            {"uptime", "运行时长",   "本次接管已进行多久",                       "已运行 1h 12m"},
            {"custom", "自定义文本", "自己写一行，支持变量（见页面提示）",       "↓{rate_down} · 丢包 {loss}%"},
    };

    /** 自定义文本支持的变量：{xxx} 会在刷新时被替换 */
    public static final String[][] VARS = {
            {"{scene}", "场景名", "地铁 5G"},
            {"{down}", "下行限速", "1.5M"},
            {"{up}", "上行限速", "512k"},
            {"{rate_down}", "下行实时速率", "120KB/s"},
            {"{rate_up}", "上行实时速率", "8KB/s"},
            {"{delay}", "下行延迟", "120ms"},
            {"{jitter}", "抖动", "30ms"},
            {"{loss}", "丢包率", "3"},
            {"{conn}", "活跃连接数", "12"},
            {"{total_down}", "下行累计流量", "18.4MB"},
            {"{total_up}", "上行累计流量", "2.1MB"},
            {"{uptime}", "运行时长", "1h 12m"},
    };

    private static final String PREF = "weaknet";
    private static final String KEY = "float_settings";
    private static final int MAX_LINES = FIELDS.length;
    private static final int MAX_CUSTOM = 200;

    private static volatile Context ctx;
    private static volatile S cache;

    public static final class S {
        /** 显示哪些行，数组顺序即显示顺序 */
        public List<String> lines = new ArrayList<String>();
        public String custom = "";
        public float alpha = 0.92f;
        public int textSp = 10;
    }

    public static void bind(Context c) {
        if (c != null) ctx = c.getApplicationContext();
    }

    /** 当前设置（内存缓存优先，避免每 500ms 刷新都去读 SharePrefs） */
    public static synchronized S get() {
        if (cache != null) return cache;
        S s = new S();
        String raw = read();
        if (raw != null && raw.length() > 0) {
            try {
                JSONObject o = new JSONObject(raw);
                JSONArray a = o.optJSONArray("lines");
                if (a != null) {
                    for (int i = 0; i < a.length(); i++) s.lines.add(a.optString(i, ""));
                }
                s.custom = o.optString("custom", "");
                s.alpha = (float) o.optDouble("alpha", 0.92);
                s.textSp = o.optInt("text_sp", 10);
            } catch (Exception e) {
                WeakNet.I.log("error", "读取悬浮窗设置失败：" + e.getMessage());
            }
        }
        sanitize(s);
        // 从未设置过：给一组默认行，避免刚授权就看到一个空壳
        if (s.lines.isEmpty() && (raw == null || raw.length() == 0)) {
            s.lines.add("scene");
            s.lines.add("limit");
            s.lines.add("rate");
        }
        cache = s;
        return s;
    }

    /** 保存（覆盖）。传入的 lines 为空是合法的：用户就是想只留一个标题行。 */
    public static synchronized S save(List<String> lines, String custom, float alpha, int textSp) {
        S s = new S();
        if (lines != null) s.lines.addAll(lines);
        s.custom = custom == null ? "" : custom;
        s.alpha = alpha;
        s.textSp = textSp;
        sanitize(s);
        cache = s;
        store(s);
        return s;
    }

    /** 恢复默认：三行（场景名 / 限速 / 实时速率）+ 默认字号透明度 */
    public static synchronized S reset() {
        S s = new S();
        s.lines.add("scene");
        s.lines.add("limit");
        s.lines.add("rate");
        sanitize(s);
        cache = s;
        store(s);
        return s;
    }

    /** 收敛到合法范围：未知的行 id 直接丢弃，数值夹到可用区间 */
    private static void sanitize(S s) {
        List<String> out = new ArrayList<String>();
        if (s.lines != null) {
            for (String id : s.lines) {
                if (id == null) continue;
                id = id.trim();
                if (!known(id) || out.contains(id)) continue;   // 去重 + 只保留已知行
                out.add(id);
                if (out.size() >= MAX_LINES) break;
            }
        }
        s.lines = out;
        if (s.custom == null) s.custom = "";
        if (s.custom.length() > MAX_CUSTOM) s.custom = s.custom.substring(0, MAX_CUSTOM);
        if (!(s.alpha >= 0.3f)) s.alpha = 0.3f;
        if (s.alpha > 1f) s.alpha = 1f;
        if (s.textSp < 8) s.textSp = 8;
        if (s.textSp > 18) s.textSp = 18;
    }

    public static boolean known(String id) {
        if (id == null) return false;
        for (String[] f : FIELDS) if (f[0].equals(id)) return true;
        return false;
    }

    public static String nameOf(String id) {
        for (String[] f : FIELDS) if (f[0].equals(id)) return f[1];
        return id;
    }

    // ---------------------------------------------------------------- 序列化

    public static JSONObject toJson(S s) {
        JSONObject o = new JSONObject();
        try {
            JSONArray a = new JSONArray();
            for (String id : s.lines) a.put(id);
            o.put("lines", a);
            o.put("custom", s.custom);
            o.put("alpha", s.alpha);
            o.put("text_sp", s.textSp);
        } catch (Exception e) {
            // ignore
        }
        return o;
    }

    /** 给控制台的完整应答：设置值 + 可选行定义 + 变量说明 + 当前显示状态 */
    public static JSONObject describe(Context c) {
        JSONObject o = new JSONObject();
        try {
            o.put("supported", true);
            o.put("shown", FloatWindow.isShown());
            o.put("can_overlay", FloatWindow.canOverlay(c));
            o.put("settings", toJson(get()));
            JSONArray fs = new JSONArray();
            for (String[] f : FIELDS) {
                JSONObject it = new JSONObject();
                it.put("id", f[0]);
                it.put("name", f[1]);
                it.put("desc", f[2]);
                it.put("sample", f[3]);
                fs.put(it);
            }
            o.put("fields", fs);
            JSONArray vs = new JSONArray();
            for (String[] v : VARS) {
                JSONObject it = new JSONObject();
                it.put("var", v[0]);
                it.put("desc", v[1]);
                it.put("sample", v[2]);
                vs.put(it);
            }
            o.put("vars", vs);
        } catch (Exception e) {
            // ignore
        }
        return o;
    }

    private static void store(S s) {
        Context c = ctx;
        if (c == null) {
            WeakNet.I.log("error", "悬浮窗设置未持久化：尚未绑定 Context");
            return;
        }
        try {
            JSONObject o = toJson(s);
            o.put("saved", true);                  // 空配置也标记为「已设置过」，免得被当成首次启动
            SharedPreferences sp = c.getSharedPreferences(PREF, Context.MODE_PRIVATE);
            sp.edit().putString(KEY, o.toString()).commit();
        } catch (Exception e) {
            WeakNet.I.log("error", "保存悬浮窗设置失败：" + e.getMessage());
        }
    }

    private static String read() {
        Context c = ctx;
        if (c == null) return null;
        try {
            return c.getSharedPreferences(PREF, Context.MODE_PRIVATE).getString(KEY, "");
        } catch (Exception e) {
            return null;
        }
    }
}
