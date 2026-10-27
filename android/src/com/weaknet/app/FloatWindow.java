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
import android.widget.Toast;

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
    /** 浮层场景面板（点标题行「⇄」弹出），不接管流量时也不随 Activity 走 */
    private FloatScenePanel scenePanel;
    private TextView bSwitch;
    /** 标题行的暂停按钮：⏸ 暂停 / ▶ 恢复 */
    private TextView bPause;
    private WindowManager.LayoutParams lp;
    private volatile boolean shown = false;

    private long lastDown = 0, lastUp = 0, lastT = 0;
    /** 最近一次实测出来的速率：刷新间隔太短时沿用，避免显示忽大忽小 */
    private double lastRd = 0, lastRu = 0;
    /** 场景名反查结果的缓存（见 {@link #nameOfKey}）；自定义场景改名/删除后要失效 */
    private static volatile String cachedKey = null;
    private static volatile String cachedName = null;

    /** 自定义场景被改名/删除时由 MyPresets 调用：不清的话标题会一直挂着旧名字 */
    static void invalidateSceneCache() {
        cachedKey = null;
        cachedName = null;
    }
    /** 参数变化时由 WeakNet 回调立刻重画一次，不用干等 500ms 轮询 */
    private final Runnable uiWatcher = new Runnable() {
        public void run() {
            if (shown) update();
        }
    };
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

            // 「⇄」切换场景：直接在飘窗上换档位，不用切回 App（面板自己的 window）
            bSwitch = new TextView(app);
            bSwitch.setText("⇄");
            bSwitch.setTextColor(0xFF9EC5FF);
            bSwitch.setTextSize(TypedValue.COMPLEX_UNIT_SP, st.textSp + 3);
            bSwitch.setPadding(dp(8), 0, dp(6), 0);
            bSwitch.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    toggleScene();
                }
            });
            top.addView(bSwitch);

            // 「⏸ / ▶」暂停：按住当前这段不动，恢复后接着刚才的位置继续。
            // 跟「⇄」并列放在标题行 —— 限速中的人最常做的就是「先按住，别动」
            bPause = new TextView(app);
            bPause.setText("⏸");
            bPause.setTextColor(0xFF9EC5FF);
            bPause.setTextSize(TypedValue.COMPLEX_UNIT_SP, st.textSp + 2);
            bPause.setPadding(dp(6), 0, dp(6), 0);
            bPause.setContentDescription("暂停弱网测试");
            bPause.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    togglePause();
                }
            });
            top.addView(bPause);

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
                if ("scene".equals(id)) continue;         // 场景名已在标题行常驻，不重复占一行
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
                                // 面板的位置是在「打开那一刻」算出来的，拖动后它会挂在错的地方：
                                // 直接收起，用户要换场景时再点一下「⇄」即可
                                if (scenePanel != null) scenePanel.close();
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
            WeakNet.I.addUiWatcher(uiWatcher);   // 切场景/开关变化时会被即时唤醒
            h.removeCallbacks(tick);
            h.postDelayed(tick, 500);
        } catch (Exception e) {
            shown = false;
            WeakNet.I.log("error", "飘窗显示失败：" + e.getMessage());
        }
    }

    // ------------------------------------------------------------ 场景面板

    private void toggleScene() {
        if (root == null) return;
        if (scenePanel == null) {
            scenePanel = new FloatScenePanel(app, new Runnable() {
                public void run() {
                    syncSwitchBtn();          // 面板开了/关了，按钮颜色跟着变
                }
            });
        }
        int h = root.getHeight();
        if (h <= 0) h = dp(40);               // 刚显示、还没测量出来时给个兜底高度
        scenePanel.toggle(lp != null ? lp.x : 0, lp != null ? lp.y : 0, h);
    }

    private void syncSwitchBtn() {
        if (bSwitch == null) return;
        boolean on = scenePanel != null && scenePanel.isShown();
        bSwitch.setTextColor(on ? 0xFF2F6FED : 0xFF9EC5FF);
    }

    /**
     * 暂停 / 恢复。
     *
     * 没在测试时点了没意义（本来就没限速），给个提示而不是静默失败 ——
     * 用户看到按钮没反应会以为程序卡了。
     */
    private void togglePause() {
        try {
            if (!WeakNet.I.limitOn && !WeakNet.I.paused) {
                Toast.makeText(app, "当前没有在跑的弱网测试", Toast.LENGTH_SHORT).show();
                return;
            }
            boolean resumed = WeakNet.I.togglePause();
            Toast.makeText(app, resumed ? "已恢复" : "已暂停",
                    Toast.LENGTH_SHORT).show();
            update();
        } catch (Exception e) {
            // ignore
        }
    }

    /** 暂停按钮的状态：⏸ / ▶，以及未生效时的灰掉 */
    private void syncPauseBtn() {
        if (bPause == null) return;
        boolean paused = WeakNet.I.paused;
        boolean running = WeakNet.I.limitOn;
        bPause.setText(paused ? "▶" : "⏸");
        bPause.setTextColor(paused ? 0xFF7BE3A6 : (running ? 0xFFFFC46B : 0xFF6B7280));
        bPause.setContentDescription(paused ? "恢复弱网测试" : "暂停弱网测试");
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
        bSwitch = null;
        shown = false;
        // 飘窗收起/重建时把场景面板一并带走：它挂在飘窗位置上，飘窗没了它也没有落点
        if (scenePanel != null) {
            scenePanel.close();
            scenePanel = null;
        }
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
            // 500ms 的 tick 之外还有「切场景即时推送」，两次间隔可能只有几毫秒：
            // 那么短时间差做除法得到的全是噪声（0 或几个 MB/s），所以不满 300ms
            // 就沿用上一次算好的值，字节累计也留着，等够时间了一起算差分
            if (lastT == 0) {
                lastDown = dn;             // 第一次只记基线：没有上一帧就谈不上差分
                lastUp = up;
                lastT = now;
            } else if (now - lastT >= 300) {
                rd = (dn - lastDown) * 1000.0 / (now - lastT);
                ru = (up - lastUp) * 1000.0 / (now - lastT);
                if (rd < 0) rd = 0;
                if (ru < 0) ru = 0;
                lastRd = rd;
                lastRu = ru;
                lastDown = dn;
                lastUp = up;
                lastT = now;
            } else {
                // 字节照样累计到下一次够时间的帧上，不会被这次刷新吞掉
                rd = lastRd;
                ru = lastRu;
            }

            // 场景名固定在标题行常驻显示（不看「场景名」行有没有勾选）：
            // 悬浮窗本来就是给「现在在测哪支场景」看的，藏在可选行里容易被关掉找不到
            String scene = sceneName(p);
            String state = "";
            if (WeakNet.I.paused) state = " · 已暂停";
            else if (!WeakNet.I.gateOpen) state = " · 断网中";
            else if (!WeakNet.I.limitOn) state = " · 未生效";
            tTitle.setText("⚡ " + scene + state);
            syncPauseBtn();

            java.util.Map<String, String> v = new java.util.HashMap<String, String>();
            v.put("scene", scene + state);
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

    /**
     * 标题行要显示的场景名。
     *
     * 限速开着时 profile 就是真正生效的那支，直接用它的名字（最权威）；
     * 没开时 profile 已被重置成直连（见 {@link WeakNet#setLimitOn}），照着它显示
     * 会永远停留在「直连（不限制）」，所以这时改按 presetKey 反查已选中的那支。
     */
    static String sceneName(Profile p) {
        // 暂停期间参数已撤到直连，profile 里看不到原来那支场景了：
        // 这时用暂停时记住的快照显示，标题才不会突然变成「直连（不限制）」
        if (WeakNet.I.paused) {
            String pp = WeakNet.I.pausedProgress();
            if (pp != null && pp.length() > 0) return pp;
            String pn = WeakNet.I.pausedSceneName();
            if (pn != null && pn.trim().length() > 0) return pn.trim();
        }
        // 组合在播时优先显示「组合名 2/5」：此时 profile 只是序列里当前那一段，
        // 光看它不知道整支走到哪了
        String prog = WeakNet.I.comboProgress();
        if (prog != null && prog.length() > 0) return prog;
        String name = WeakNet.I.limitOn && p != null ? p.name : null;
        if (name != null && name.trim().length() > 0) return name.trim();
        return nameOfKey(WeakNet.I.presetKey);
    }

    /** 按场景 key 反查名字：内置预设查 {@link Profile#shortOf}，自定义（u:）查 {@link MyPresets} */
    static String nameOfKey(String key) {
        // 没开启弱网时标题每 500ms 都要走这里，自定义场景那一步会去读存储；
        // key 没变就直接用上次的结果，别把这种轮询变成定时 IO
        if (key != null && key.equals(cachedKey) && cachedName != null) return cachedName;
        String n = resolveName(key);
        cachedKey = key;
        cachedName = n;
        return n;
    }

    private static String resolveName(String key) {
        if (key == null || key.length() == 0 || "direct".equals(key)) return "直连（不限制）";
        if (key.startsWith("u:")) {
            try {
                MyPresets.Item it = MyPresets.find(key.substring(2));
                if (it != null && it.name != null && it.name.length() > 0) return it.name;
            } catch (Exception e) {
                // ignore
            }
            return "自定义场景";
        }
        if (key.startsWith(MyCombos.KEY_PREFIX)) {
            // 组合：选中/播完时标题也要显示它的名字，而不是把原始 key 甩给用户看
            try {
                MyCombos.Item c = MyCombos.find(key.substring(MyCombos.KEY_PREFIX.length()));
                if (c != null && c.name != null && c.name.length() > 0) return c.name;
            } catch (Exception e) {
                // ignore
            }
            return "弱网组合";
        }
        String s = Profile.shortOf(key);
        return s != null && s.length() > 0 ? s : key;
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
