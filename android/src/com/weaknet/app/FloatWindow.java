package com.weaknet.app;

import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
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

/**
 * 限速飘窗（悬浮窗）
 *
 * 接管流量后悬浮显示：当前场景 / 上行下行限速 / 实时速率 / 断网状态。
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
    private TextView tTitle, tLimit, tRate;
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

    // ---------------------------------------------------------------- 布局

    private void build() {
        try {
            removeView();

            root = new LinearLayout(app);
            root.setOrientation(LinearLayout.VERTICAL);
            GradientDrawable gd = new GradientDrawable();
            gd.setColor(0xD91F2430);
            gd.setCornerRadius(16f);
            gd.setStroke(1, 0x55FFFFFF);
            root.setBackgroundDrawable(gd);
            int pad = dp(10);
            root.setPadding(pad + 2, pad, pad + 2, pad);

            // 标题行：场景名 + 关闭
            LinearLayout top = new LinearLayout(app);
            top.setOrientation(LinearLayout.HORIZONTAL);
            tTitle = new TextView(app);
            tTitle.setTextColor(0xFFFFFFFF);
            tTitle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
            tTitle.setSingleLine(true);
            tTitle.setText("⚡ WeakNet");
            LinearLayout.LayoutParams tl = new LinearLayout.LayoutParams(0,
                    LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
            top.addView(tTitle, tl);

            TextView close = new TextView(app);
            close.setText("✕");
            close.setTextColor(0xFF9AA4B2);
            close.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
            close.setPadding(dp(6), 0, dp(2), 0);
            close.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    hide();
                }
            });
            top.addView(close);
            root.addView(top);

            tLimit = new TextView(app);
            tLimit.setTextColor(0xFF9EC5FF);
            tLimit.setTextSize(TypedValue.COMPLEX_UNIT_SP, 10);
            tLimit.setSingleLine(true);
            root.addView(tLimit);

            tRate = new TextView(app);
            tRate.setTextColor(0xFF7BE3A6);
            tRate.setTextSize(TypedValue.COMPLEX_UNIT_SP, 10);
            tRate.setSingleLine(true);
            root.addView(tRate);

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
            lp.alpha = 0.92f;

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
            tTitle.setText("⚡ " + p.name + state);
            tLimit.setText("限速 ↓" + kbps(p.downKbps) + "   ↑" + kbps(p.upKbps));
            tRate.setText("实时 ↓" + bps(rd) + "   ↑" + bps(ru));
        } catch (Exception e) {
            // ignore
        }
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

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                app.getResources().getDisplayMetrics());
    }
}
