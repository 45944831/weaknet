package com.weaknet.app;

import android.content.Context;
import android.content.Intent;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 限速飘窗（悬浮窗）
 *
 * 接管流量后悬浮显示实时网络状况；显示哪些行、顺序、字号、透明度都在
 * 控制台的「⚙ 悬浮窗设置」里配（见 {@link FloatSettings}）。
 *  - 可拖动到屏幕任意位置
 *  - 点主体打开控制台（MainActivity），点 ✕ 收起
 *  - 数据 500ms 刷新一次（取 WeakNet 的累计字节做差分）
 *
 * 需要 SYSTEM_ALERT_WINDOW 权限；未授权时自动跳过显示，弱网功能不受影响。
 */
public class FloatWindow {
    private static FloatWindow inst;

    private final Context app;
    private final WindowManager wm;
    private final Handler h = new Handler(Looper.getMainLooper());

    private LinearLayout root;
    private TextView tTitle;
    /** 已显示的每一行：id -> TextView，顺序与设置里的 lines 一致 */
    private final Map<String, TextView> rows = new LinkedHashMap<String, TextView>();
    private WindowManager.LayoutParams lp;
    private volatile boolean shown = false;

    private long lastDown = 0, lastUp = 0, lastT = 0;
    private final Runnable tick = new Runnable() {
        public void run() {
            if (!shown) return;
            update();
            h.postDelayed(this, 500);
        }
    };

    private FloatWindow(Context c) {
        app = c.getApplicationContext();
        wm = (WindowManager) app.getSystemService(Context.WINDOW_SERVICE);
    }

    public static synchronized FloatWindow get(Context c) {
        if (inst == null) inst = new FloatWindow(c);
        return inst;
    }

    public static boolean isShown() {
        return inst != null && inst.shown;
    }

    /** 是否已获得悬浮窗权限 */
    public static boolean canOverlay(Context c) {
        if (Build.VERSION.SDK_INT >= 23) {
            try {
                return Settings.canDrawOverlays(c);
            } catch (Exception e) {
                return false;
            }
        }
        return true;
    }

    public void show() {
        if (shown) return;
        if (!canOverlay(app)) return;          // 没权限就不显示，不打扰用户
        if (Looper.myLooper() == Looper.getMainLooper()) {
            build();                           // 同步执行，调用方能立刻拿到 isShown()
            return;
        }
        h.post(new Runnable() {
            public void run() {
                build();
            }
        });
    }

    public void hide() {
        h.post(new Runnable() {
            public void run() {
                removeView();
            }
        });
    }

    /**
     * 设置变更后立刻重建（保存后马上看到效果，不用收起再显示）。
     * 没在显示就什么都不做。
     */
    public static void reload() {
        final FloatWindow f = inst;
        if (f == null || !f.shown) return;
        f.h.post(new Runnable() {
            public void run() {
                if (f.shown) f.build();
            }
        });
    }

    // ---------------------------------------------------------------- 布局

    private void build() {
        try {
            removeView();
            FloatSettings.S st = FloatSettings.get();

            root = new LinearLayout(app);
            root.setOrientation(LinearLayout.VERTICAL);
            GradientDrawable gd = new GradientDrawable();
            gd.setColor(0xD91F2430);
            gd.setCornerRadius(16f);
            gd.setStroke(1, 0x55FFFFFF);
            root.setBackgroundDrawable(gd);
            int pad = dp(10);
            root.setPadding(pad + 2, pad, pad + 2, pad);

            // 标题行：场景名（未勾选「场景名」时只显示产品标识）+ 关闭
            LinearLayout top = new LinearLayout(app);
            top.setOrientation(LinearLayout.HORIZONTAL);
            tTitle = new TextView(app);
            tTitle.setTextColor(0xFFFFFFFF);
            tTitle.setTextSize(TypedValue.COMPLEX_UNIT_SP, st.textSp + 2);
            tTitle.setSingleLine(true);
            tTitle.setText("⚡ WeakNet");
            LinearLayout.LayoutParams tl = new LinearLayout.LayoutParams(0,
                    LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
            top.addView(tTitle, tl);

            TextView close = new TextView(app);
            close.setText("✕");
            close.setTextColor(0xFF9AA4B2);
            close.setTextSize(TypedValue.COMPLEX_UNIT_SP, st.textSp + 3);
            close.setPadding(dp(6), 0, dp(2), 0);
            close.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    hide();
                }
            });
            top.addView(close);
            root.addView(top);

            // 内容行：顺序完全由设置决定
            rows.clear();
            for (String id : new ArrayList<String>(st.lines)) {
                if ("scene".equals(id)) continue;         // 场景名显示在标题行
                TextView t = new TextView(app);
                t.setTextColor("limit".equals(id) ? 0xFF9EC5FF : 0xFF7BE3A6);
                t.setTextSize(TypedValue.COMPLEX_UNIT_SP, st.textSp);
                t.setSingleLine(true);
                rows.put(id, t);
                root.addView(t);
            }

            // 点主体 → 打开控制台；长按/移动 → 拖动
            root.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    try {
                        Intent i = new Intent(app, MainActivity.class);
                        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
                        app.startActivity(i);
                    } catch (Exception e) {
                        // ignore
                    }
                }
            });
            root.setOnTouchListener(new View.OnTouchListener() {
                private int sx, sy;
                private float tx, ty;
                private boolean moved = false;

                public boolean onTouch(View v, MotionEvent e) {
                    switch (e.getAction()) {
                        case MotionEvent.ACTION_DOWN:
                            sx = lp.x;
                            sy = lp.y;
                            tx = e.getRawX();
                            ty = e.getRawY();
                            moved = false;
                            return false;                 // 交给 onClick 判定
                        case MotionEvent.ACTION_MOVE:
                            int dx = (int) (e.getRawX() - tx);
                            int dy = (int) (e.getRawY() - ty);
                            if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
                            if (moved && lp != null) {
                                lp.x = sx + dx;
                                lp.y = sy + dy;
                                try {
                                    wm.updateViewLayout(root, lp);
                                } catch (Exception ignore) {
                                }
                            }
                            return moved;
                        case MotionEvent.ACTION_UP:
                            return moved;                 // 拖动过就不再触发点击
                    }
                    return false;
                }
            });

            lp = new WindowManager.LayoutParams(
                    WindowManager.LayoutParams.WRAP_CONTENT,
                    WindowManager.LayoutParams.WRAP_CONTENT,
                    overlayType(),
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                            | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                    PixelFormat.TRANSLUCENT);
            lp.gravity = Gravity.TOP | Gravity.LEFT;
            lp.x = dp(12);
            lp.y = dp(90);
            lp.alpha = st.alpha;

            wm.addView(root, lp);
            shown = true;
            lastT = 0;
            update();
            h.removeCallbacks(tick);
            h.postDelayed(tick, 500);
        } catch (Exception e) {
            shown = false;
            WeakNet.I.log("error", "飘窗显示失败：" + e.getMessage());
        }
    }

    private static int overlayType() {
        if (Build.VERSION.SDK_INT >= 26) return WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY;
        return WindowManager.LayoutParams.TYPE_PHONE;
    }

    private void removeView() {
        try {
            h.removeCallbacks(tick);
        } catch (Exception e) {
            // ignore
        }
        if (root != null) {
            try {
                wm.removeView(root);
            } catch (Exception e) {
                // ignore
            }
            root = null;
        }
        rows.clear();
        tTitle = null;
        shown = false;
    }

    // ---------------------------------------------------------------- 数据

    private void update() {
        if (root == null || tTitle == null) return;
        try {
            Profile p = WeakNet.I.profile;
            long dn = WeakNet.I.bytesDown.get();
            long up = WeakNet.I.bytesUp.get();
            long now = System.currentTimeMillis();
            double rd = 0, ru = 0;
            if (lastT > 0 && now > lastT) {
                rd = (dn - lastDown) * 1000.0 / (now - lastT);
                ru = (up - lastUp) * 1000.0 / (now - lastT);
                if (rd < 0) rd = 0;
                if (ru < 0) ru = 0;
            }
            lastDown = dn;
            lastUp = up;
            lastT = now;

            String state = WeakNet.I.gateOpen ? "" : " · 断网中";
            // 标题恒显示产品标识；勾选「场景名」时才把当前场景拼上去
            // （scene 不进 rows，它占的是标题行这一格）
            tTitle.setText(hasLine("scene") ? "⚡ " + p.name + state : "⚡ WeakNet");

            java.util.Map<String, String> v = new java.util.HashMap<String, String>();
            v.put("scene", p.name + state);
            v.put("down", kbps(p.downKbps));
            v.put("up", kbps(p.upKbps));
            v.put("rate_down", bps(rd));
            v.put("rate_up", bps(ru));
            v.put("delay", p.latencyDownMs + "ms");
            v.put("jitter", p.jitterMs + "ms");
            v.put("loss", trimNum(p.lossPct));
            v.put("conn", String.valueOf(WeakNet.I.connActive.get()));
            v.put("total_down", bytes(dn));
            v.put("total_up", bytes(up));
            v.put("uptime", dur((now - WeakNet.I.startedAt) / 1000));

            for (java.util.Map.Entry<String, TextView> e : rows.entrySet()) {
                e.getValue().setText(lineText(e.getKey(), p, rd, ru, dn, up, now, v));
            }
        } catch (Exception ex) {
            // ignore
        }
    }

    private boolean hasLine(String id) {
        return FloatSettings.get().lines.contains(id);
    }

    private String lineText(String id, Profile p, double rd, double ru,
                            long dn, long up, long now, java.util.Map<String, String> v) {
        if ("limit".equals(id)) return "限速 ↓" + kbps(p.downKbps) + "   ↑" + kbps(p.upKbps);
        if ("rate".equals(id)) return "实时 ↓" + bps(rd) + "   ↑" + bps(ru);
        if ("conns".equals(id))
            return "连接 " + WeakNet.I.connActive.get() + " / " + WeakNet.I.connTotal.get();
        if ("total".equals(id)) return "累计 ↓" + bytes(dn) + " ↑" + bytes(up);
        if ("uptime".equals(id)) return "已运行 " + dur((now - WeakNet.I.startedAt) / 1000);
        if ("loss".equals(id)) {
            StringBuilder sb = new StringBuilder();
            if (p.latencyDownMs > 0) {
                sb.append("延迟 ").append(p.latencyDownMs);
                if (p.jitterMs > 0) sb.append("±").append(p.jitterMs);
                sb.append("ms");
            }
            if (p.lossPct > 0) {
                if (sb.length() > 0) sb.append(" · ");
                sb.append("丢包 ").append(trimNum(p.lossPct)).append("%");
            }
            if (p.disconnectPct > 0) {
                if (sb.length() > 0) sb.append(" · ");
                sb.append("断连 ").append(trimNum(p.disconnectPct)).append("%");
            }
            return sb.length() > 0 ? sb.toString() : "无额外损耗";
        }
        if ("custom".equals(id)) return expand(FloatSettings.get().custom, v);
        return "";
    }

    /** 把自定义文本里的 {变量} 换成实时值；未知变量原样保留，方便用户发现自己写错了 */
    private String expand(String tpl, java.util.Map<String, String> v) {
        if (tpl == null || tpl.length() == 0) return "";
        StringBuilder out = new StringBuilder();
        int i = 0;
        while (i < tpl.length()) {
            int a = tpl.indexOf('{', i);
            if (a < 0) { out.append(tpl.substring(i)); break; }
            out.append(tpl, i, a);
            int b = tpl.indexOf('}', a + 1);
            if (b < 0) { out.append(tpl.substring(a)); break; }
            String key = tpl.substring(a, b + 1);
            String val = v.get(key);
            out.append(val == null ? key : val);
            i = b + 1;
        }
        return out.toString();
    }

    private static String kbps(int v) {
        if (v <= 0) return "不限";
        if (v >= 1000) return (v / 1000) + "M";
        return v + "k";
    }

    private static String bps(double v) {
        if (v >= 1024 * 1024) return String.format("%.2fMB/s", v / 1024 / 1024);
        if (v >= 1024) return String.format("%.0fKB/s", v / 1024);
        return String.format("%.0fB/s", v);
    }

    private static String bytes(long v) {
        if (v >= 1024 * 1024 * 1024) return String.format("%.2fGB", v / 1073741824.0);
        if (v >= 1024 * 1024) return String.format("%.1fMB", v / 1048576.0);
        if (v >= 1024) return String.format("%.0fKB", v / 1024.0);
        return v + "B";
    }

    private static String dur(long sec) {
        if (sec < 60) return sec + "s";
        long h = sec / 3600, m = sec % 3600 / 60, s = sec % 60;
        if (h > 0) return h + "h " + m + "m";
        return m + "m " + s + "s";
    }

    /** 3.0 → "3"，3.5 → "3.5"：百分比不想让用户看见一堆 .0 */
    private static String trimNum(double v) {
        if (Math.abs(v - Math.rint(v)) < 1e-6) return String.valueOf((long) Math.rint(v));
        return String.format("%.1f", v);
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                app.getResources().getDisplayMetrics());
    }
}
