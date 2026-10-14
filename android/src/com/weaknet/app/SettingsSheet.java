package com.weaknet.app;

import android.app.Dialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;


/**
 * 悬浮二级界面（设置）——**只管限速飘窗一件事**。
 *
 * 限速总开关、测试场景、自定义场景、环境共享、事件日志全部在主界面上
 * （见 {@link MainActivity} 与 {@link MainPanels}）：它们都是高频操作，
 * 再套一层浮层纯属多余。这里只留下低频、且必须有个「去授权」入口的飘窗设置。
 *
 * 关闭后设置依然生效；弹窗打开期间主界面的 3 秒状态循环会调用 {@link #sync()}
 * 同步按钮文字，避免「外面状态变了、弹窗里还显示旧值」。
 */
public class SettingsSheet {
    private final MainActivity a;

    private Dialog dlg;
    private LinearLayout card;
    private Button btnFloat;
    private Button btnPerm;
    private TextView tvPerm;

    SettingsSheet(MainActivity a) {
        this.a = a;
    }

    // ---------------------------------------------------------------- 生命周期

    public void show() {
        if (a == null || a.isFinishing()) return;
        if (dlg == null) build();
        sync();
        if (dlg != null && !dlg.isShowing()) dlg.show();
    }

    public void close() {
        if (dlg != null) {
            try {
                dlg.dismiss();
            } catch (Exception e) {
                // ignore
            }
            dlg = null;
        }
    }

    public boolean isShowing() {
        return dlg != null && dlg.isShowing();
    }

    /** 主界面状态循环回调：让弹窗里的飘窗按钮与权限行跟着实时状态走 */
    void sync() {
        if (dlg == null) return;
        btnFloat.setText(FloatWindow.isShown() ? "收起限速飘窗" : "显示限速飘窗");
        btnFloat.setTextColor(FloatWindow.isShown() ? 0xFFD93025 : 0xFF2F6FED);
        btnFloat.setBackground(box(FloatWindow.isShown() ? 0xFFFDECEC : 0xFFF1F4FA,
                FloatWindow.isShown() ? 0xFFF3C3C0 : 0xFFD7DEEA, 8));
        boolean granted = FloatWindow.canOverlay(a);
        tvPerm.setText(granted ? "悬浮窗权限：已授予" : "悬浮窗权限：未授予（飘窗显示不出来）");
        tvPerm.setTextColor(granted ? 0xFF2E7D32 : 0xFFD93025);
        btnPerm.setVisibility(granted ? View.GONE : View.VISIBLE);
    }

    // ---------------------------------------------------------------- 布局

    private void build() {
        dlg = new Dialog(a);
        dlg.requestWindowFeature(Window.FEATURE_NO_TITLE);
        dlg.setCancelable(true);
        dlg.setCanceledOnTouchOutside(true);

        ScrollView sv = new ScrollView(a);
        sv.setBackgroundColor(Color.TRANSPARENT);

        LinearLayout card = new LinearLayout(a);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(box(Color.WHITE, 0xFFDDE3EC, 14));
        card.setPadding(dp(14), dp(12), dp(14), dp(14));
        this.card = card;

        // ---- 标题栏 ----
        LinearLayout head = new LinearLayout(a);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView t = new TextView(a);
        t.setText("⚙ 设置");
        t.setTextSize(16);
        t.setTextColor(0xFF1F2430);
        t.setTypeface(null, Typeface.BOLD);
        head.addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView close = new TextView(a);
        close.setText("✕");
        close.setTextSize(17);
        close.setTextColor(0xFF8A94A6);
        close.setPadding(dp(8), dp(2), dp(4), dp(2));
        close.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                close();
            }
        });
        head.addView(close);
        card.addView(head);
        card.addView(divider(dp(10)));

        // ---- 限速飘窗 ----
        card.addView(label("限速飘窗"));
        btnFloat = new Button(a);
        btnFloat.setAllCaps(false);
        btnFloat.setTextSize(14);
        btnFloat.setPadding(dp(12), dp(10), dp(12), dp(10));
        btnFloat.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                toggleFloat();
            }
        });
        card.addView(btnFloat, full(0, 0, 0, 6));
        card.addView(hint("悬浮显示当前场景与实时速率，可拖动，点主体回到 App。"));

        tvPerm = new TextView(a);
        tvPerm.setTextSize(12);
        tvPerm.setPadding(dp(2), dp(2), dp(2), dp(4));
        card.addView(tvPerm);

        btnPerm = new Button(a);
        btnPerm.setAllCaps(false);
        btnPerm.setTextSize(13);
        btnPerm.setText("去授予悬浮窗权限");
        btnPerm.setTextColor(Color.WHITE);
        btnPerm.setBackground(box(0xFF2F6FED, 0xFF2F6FED, 8));
        btnPerm.setPadding(dp(12), dp(9), dp(12), dp(9));
        btnPerm.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                requestOverlayPermission();
            }
        });
        card.addView(btnPerm, full(0, 0, 0, 4));

        card.addView(divider(dp(6)));
        card.addView(hint("限速总开关、接管范围、测试场景、自定义场景、环境共享与事件日志"
                + "都在主界面上，直接滚下去就能改。"));

        int m = dp(12);
        FrameLayout.LayoutParams fl = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        fl.setMargins(m, m, m, m);
        sv.addView(card, fl);
        dlg.setContentView(sv);

        Window w = dlg.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            w.setLayout(WindowManager.LayoutParams.MATCH_PARENT,
                    WindowManager.LayoutParams.WRAP_CONTENT);
            w.setGravity(Gravity.CENTER);
            // 部分系统上 WRAP_CONTENT 的 Dialog 窗口会被压得很矮，内容只能在小窗里滚。
            // 显示后按内容实际高度重设：内容多时也不超过 90% 屏高。
            dlg.setOnShowListener(new DialogInterface.OnShowListener() {
                public void onShow(DialogInterface di) {
                    resize();
                }
            });
        }
    }

    /** 显示后按内容高度重设浮层窗口：内容少时紧贴内容，内容多时上限 90% 屏高 */
    private void resize() {
        Window w = dlg == null ? null : dlg.getWindow();
        if (w == null || card == null) return;
        try {
            int wide = a.getResources().getDisplayMetrics().widthPixels - dp(24);
            card.measure(
                    View.MeasureSpec.makeMeasureSpec(wide, View.MeasureSpec.EXACTLY),
                    View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
            int h = card.getMeasuredHeight() + dp(24);
            int maxH = (int) (a.getResources().getDisplayMetrics().heightPixels * 0.9);
            w.setLayout(WindowManager.LayoutParams.MATCH_PARENT, Math.min(h, maxH));
        } catch (Exception e) {
            // 测量失败就保持系统默认，不影响功能
        }
    }

    // ---------------------------------------------------------------- 飘窗

    /** 限速飘窗：显示 / 收起（悬浮窗显示当前场景与实时速率，可拖动） */
    private void toggleFloat() {
        if (FloatWindow.isShown()) {
            FloatWindow.get(a).hide();
            Toast.makeText(a, "已收起限速飘窗", Toast.LENGTH_SHORT).show();
            sync();
            a.updateStatus();
            return;
        }
        if (!FloatWindow.canOverlay(a)) {
            requestOverlayPermission();
            return;
        }
        FloatWindow.get(a).show();
        if (FloatWindow.isShown()) {
            Toast.makeText(a, "已显示限速飘窗（可拖动，点✕收起）", Toast.LENGTH_SHORT).show();
        } else {
            Toast.makeText(a, "飘窗显示失败，请确认已授权", Toast.LENGTH_SHORT).show();
        }
        sync();
        a.updateStatus();
    }

    private void requestOverlayPermission() {
        if (Build.VERSION.SDK_INT >= 23) {
            try {
                Intent i = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:" + a.getPackageName()));
                a.startActivityForResult(i, MainActivity.REQ_OVERLAY);
                Toast.makeText(a, "请开启「显示在其他应用上层」权限后重试", Toast.LENGTH_LONG).show();
            } catch (Exception e) {
                Toast.makeText(a, "无法打开悬浮窗权限设置", Toast.LENGTH_SHORT).show();
            }
        }
    }

    // ---------------------------------------------------------------- 控件工厂

    private LinearLayout.LayoutParams full(int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(dp(l), dp(t), dp(r), dp(b));
        return p;
    }

    private TextView label(String text) {
        TextView t = new TextView(a);
        t.setText(text);
        t.setTextSize(13);
        t.setTextColor(0xFF5A6472);
        t.setPadding(dp(2), dp(6), dp(2), dp(4));
        return t;
    }

    private TextView hint(String text) {
        TextView t = new TextView(a);
        t.setText(text);
        t.setTextSize(11);
        t.setTextColor(0xFF8A94A6);
        t.setPadding(dp(2), dp(2), dp(2), dp(4));
        return t;
    }

    /** 细分割线（上下留白用 margin 模拟） */
    private View divider(int gap) {
        View v = new View(a);
        v.setBackgroundColor(0xFFE6EAF0);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 1);
        p.setMargins(0, gap, 0, gap);
        v.setLayoutParams(p);
        return v;
    }

    private GradientDrawable box(int fill, int stroke, int radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(fill);
        g.setStroke(1, stroke);
        g.setCornerRadius(dp(radiusDp));
        return g;
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                a.getResources().getDisplayMetrics());
    }
}
