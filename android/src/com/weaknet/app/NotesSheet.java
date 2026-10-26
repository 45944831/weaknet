package com.weaknet.app;

import android.app.Dialog;
import android.content.DialogInterface;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * 场景说明（悬浮二级界面）
 *
 * 入口是「🎯 测试环境」标题右侧的「！」。17 支档位里有一半光看名字分不清：
 *   · 「4G 弱网」和「弱 Wi-Fi」都是低速 + 丢包；
 *   · 「黑洞」和「完全断网」都是连不上，但机制完全不同（挂住不返回 vs 立刻失败）。
 * 这里把「模拟的什么现场 + 典型症状」和实际参数一起摊开，省得每次都去翻源码。
 *
 * 内容全部来自 {@link Profile#noteOf} 与 {@link Profile#detail()}，本类只管排版，
 * 不维护任何场景数据 —— 改档位参数时不用动这里。
 */
public class NotesSheet {
    private static final int ACCENT = 0xFF2F6FED;

    private final MainActivity a;

    private Dialog dlg;
    private LinearLayout body;

    NotesSheet(MainActivity a) {
        this.a = a;
    }

    public void show() {
        if (a == null || a.isFinishing()) return;
        a.suspendFloat();          // 飘窗会盖住弹窗上半部分并吃掉点击
        if (dlg == null) build();
        render();
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

    // ---------------------------------------------------------------- 布局

    private void build() {
        dlg = new Dialog(a);
        dlg.requestWindowFeature(Window.FEATURE_NO_TITLE);
        dlg.setCancelable(true);
        dlg.setCanceledOnTouchOutside(true);
        dlg.setOnDismissListener(new DialogInterface.OnDismissListener() {
            public void onDismiss(DialogInterface d) {
                a.restoreFloat();
            }
        });

        ScrollView sv = new ScrollView(a);
        sv.setBackgroundColor(Color.TRANSPARENT);

        LinearLayout card = new LinearLayout(a);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(box(Color.WHITE, 0xFFDDE3EC, 14));
        card.setPadding(dp(14), dp(12), dp(14), dp(14));

        // ---- 标题栏 ----
        LinearLayout head = new LinearLayout(a);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView t = new TextView(a);
        t.setText("！ 场景说明");
        t.setTextSize(16);
        t.setTextColor(0xFF1F2430);
        t.setTypeface(null, Typeface.BOLD);
        head.addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView close = new TextView(a);
        close.setText("✕");
        close.setTextSize(17);
        close.setTextColor(0xFF8A94A6);
        close.setPadding(dp(10), dp(2), dp(4), dp(2));
        close.setContentDescription("关闭场景说明");
        close.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                close();
            }
        });
        head.addView(close);
        card.addView(head);
        card.addView(divider(dp(8)));

        body = new LinearLayout(a);
        body.setOrientation(LinearLayout.VERTICAL);
        card.addView(body);

        sv.addView(card, new android.widget.FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        dlg.setContentView(sv);

        Window w = dlg.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            // 内容有 17 支档位、必然超屏，直接给固定高度让 ScrollView 自己滚，
            // 不用像参数表单那样先 measure 再算高度
            int h = (int) (a.getResources().getDisplayMetrics().heightPixels * 0.86);
            w.setLayout(WindowManager.LayoutParams.MATCH_PARENT, h);
            w.setGravity(Gravity.CENTER);
        }
    }

    /** 按当前选中的档位重画正文（「← 当前」标记跟着走） */
    private void render() {
        if (body == null) return;
        body.removeAllViews();
        String cur = WeakNet.I.presetKey;

        String cat = null;
        for (String key : Profile.KEYS) {
            String c = Profile.catOf(key);
            if (!c.equals(cat)) {
                cat = c;
                body.addView(catHeader(c));
            }
            Profile p;
            try {
                p = Profile.build(key);
            } catch (Exception e) {
                continue;
            }
            String shortName = Profile.shortOf(key);
            String name = shortName.length() > 0 ? shortName : p.name;
            boolean on = key.equals(cur);

            TextView nt = new TextView(a);
            nt.setText(on ? (name + "　← 当前选中") : name);
            nt.setTextSize(14);
            nt.setTextColor(on ? ACCENT : 0xFF1F2430);
            nt.setTypeface(null, Typeface.BOLD);
            nt.setPadding(0, dp(8), 0, dp(1));
            body.addView(nt);

            TextView pt = new TextView(a);
            pt.setText(p.detail());
            pt.setTextSize(11);
            pt.setTextColor(0xFF6B7280);
            pt.setPadding(dp(6), 0, 0, dp(2));
            body.addView(pt);

            String note = Profile.noteOf(key);
            if (note.length() > 0) {
                TextView dt = new TextView(a);
                dt.setText(note);
                dt.setTextSize(12);
                dt.setTextColor(0xFF3A4250);
                dt.setLineSpacing(dp(2), 1f);
                dt.setPadding(dp(6), 0, 0, dp(2));
                body.addView(dt);
            }
        }
    }

    // ---------------------------------------------------------------- 控件工厂

    private TextView catHeader(String text) {
        TextView t = new TextView(a);
        t.setText(text);
        t.setTextSize(13);
        t.setTextColor(ACCENT);
        t.setTypeface(null, Typeface.BOLD);
        t.setPadding(0, dp(12), 0, dp(2));
        return t;
    }

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
