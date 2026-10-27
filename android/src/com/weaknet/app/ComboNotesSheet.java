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
 * 弱网组合说明（悬浮二级界面）
 *
 * 入口是「🔀 我的弱网组合」标题右侧的「！」。组合是把几支场景按时间排队播，
 * 按钮上只写着「通勤」「抢购」这类名字，猜不出它到底模拟了什么 ——
 * 这里把机制、我保存的组合、推荐例子摊开，省得每次都去翻源码。
 *
 * 内容全部来自 {@link Profile#comboDocParts()}，本类只管排版，
 * 不维护任何文案 —— 文案在引擎里，Windows 端读的是同一份。
 */
public class ComboNotesSheet {

    private final MainActivity a;

    private Dialog dlg;
    private LinearLayout body;

    ComboNotesSheet(MainActivity a) {
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

        ScrollView sv = new ScrollView(a);
        sv.setBackgroundColor(Color.TRANSPARENT);

        LinearLayout card = new LinearLayout(a);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(box(Color.WHITE, 0xFFDDE3EC, 14));
        card.setPadding(dp(16), dp(14), dp(16), dp(16));
        body = card;

        // ---- 标题栏：🔀 弱网组合说明 + ✕ ----
        LinearLayout head = new LinearLayout(a);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView icon = new TextView(a);
        icon.setText("🔀");
        icon.setTextSize(16);
        head.addView(icon);
        TextView title = new TextView(a);
        title.setText("弱网组合说明");
        title.setTextSize(16);
        title.setTextColor(0xFF1F2430);
        title.setTypeface(null, Typeface.BOLD);
        title.setPadding(dp(6), 0, 0, 0);
        head.addView(title, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView close = new TextView(a);
        close.setText("✕");
        close.setTextSize(18);
        close.setTextColor(0xFF8A94A6);
        close.setPadding(dp(10), dp(2), dp(4), dp(2));
        close.setContentDescription("关闭组合说明");
        close.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                close();
            }
        });
        head.addView(close);
        card.addView(head);
        card.addView(divider(dp(10)));

        sv.addView(card, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        dlg.setContentView(sv);

        Window w = dlg.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            w.setLayout(WindowManager.LayoutParams.MATCH_PARENT,
                    WindowManager.LayoutParams.WRAP_CONTENT);
            w.setGravity(Gravity.CENTER);
            dlg.setOnShowListener(new DialogInterface.OnShowListener() {
                public void onShow(DialogInterface di) {
                    resize();
                }
            });
        }
    }

    /** 显示后按内容高度重设浮层：最多不超过 85% 屏高 */
    private void resize() {
        Window w = dlg == null ? null : dlg.getWindow();
        if (w == null || body == null) return;
        try {
            int wide = a.getResources().getDisplayMetrics().widthPixels - dp(32);
            body.measure(
                    View.MeasureSpec.makeMeasureSpec(wide, View.MeasureSpec.EXACTLY),
                    View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
            int h = body.getMeasuredHeight() + dp(32);
            int maxH = (int) (a.getResources().getDisplayMetrics().heightPixels * 0.85);
            w.setLayout(WindowManager.LayoutParams.MATCH_PARENT, Math.min(h, maxH));
        } catch (Exception e) {
            // 测量失败就保持系统默认，不影响功能
        }
    }

    // ---------------------------------------------------------------- 内容

    private void render() {
        if (body == null) return;
        // 保留标题栏与分隔线，其余重画
        while (body.getChildCount() > 2) {
            body.removeViewAt(body.getChildCount() - 1);
        }
        Object[] parts = Profile.comboDocParts();
        String intro = parts[0] == null ? "" : (String) parts[0];
        java.util.List<?> examples = (java.util.List<?>) parts[1];
        java.util.List<?> tips = (java.util.List<?>) parts[2];

        // ---- 1. 机制 ----
        body.addView(sectionLabel("组合怎么播", 0xFF2F6FED, dp(12)));
        for (String line : intro.split("\n")) {
            body.addView(text(line, 14, 0xFF1F2430, dp(6)));
        }

        // ---- 2. 我保存的组合 ----
        body.addView(sectionLabel("我保存的组合", 0xFF2F6FED, dp(14)));
        java.util.List<MyCombos.Item> mine = MyCombos.load();
        if (mine == null || mine.isEmpty()) {
            body.addView(text("还没有组合。点「＋ 新建」把几支场景排成一条链。",
                    14, 0xFF6B7280, dp(6)));
        } else {
            String cur = WeakNet.I.presetKey;
            for (MyCombos.Item it : mine) {
                String mark = (MyCombos.KEY_PREFIX + it.id).equals(cur)
                        ? "　　← 当前选中" : "";
                body.addView(text(it.name + mark, 14, 0xFF1F2430, dp(6)));
                body.addView(text(WeakNet.summaryOf(it), 13, 0xFF6B7280, dp(8)));
            }
        }

        // ---- 3. 推荐例子 ----
        body.addView(sectionLabel("推荐例子（照抄就能用）", 0xFF2F6FED, dp(14)));
        if (examples != null) {
            for (Object o : examples) {
                String[] lines = String.valueOf(o).split("\n");
                if (lines.length < 4) continue;
                body.addView(text(lines[0] + "　" + lines[1], 14, 0xFF1F2430, dp(6)));
                body.addView(text("怎么排：" + lines[3], 13, 0xFF6B7280, dp(3)));
                body.addView(text(lines[2], 13, 0xFF1F2430, dp(8)));
            }
        }

        // ---- 4. 注意 ----
        body.addView(sectionLabel("注意", 0xFFD97706, dp(14)));
        if (tips != null) {
            for (Object o : tips) {
                // 「·」单独一个控件：拼成 "· " + tip 的话，TextView 会把「·」
                // 当成一个词，窄屏上会把它甩到单独一行（参数说明页已经踩过这个坑）。
                body.addView(tipRow(String.valueOf(o)));
            }
        }
    }

    /** 一条提醒：左边「·」，右边正文（悬挂缩进） */
    private View tipRow(String tip) {
        LinearLayout row = new LinearLayout(a);
        row.setOrientation(LinearLayout.HORIZONTAL);

        TextView dot = new TextView(a);
        dot.setText("·");
        dot.setTextSize(13);
        dot.setTextColor(0xFFD97706);
        LinearLayout.LayoutParams dotLp = new LinearLayout.LayoutParams(
                dp(14), ViewGroup.LayoutParams.WRAP_CONTENT);
        row.addView(dot, dotLp);

        TextView t = new TextView(a);
        t.setText(tip);
        t.setTextSize(13);
        t.setTextColor(0xFFD97706);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        row.addView(t, lp);

        LinearLayout.LayoutParams rp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rp.setMargins(0, dp(4), 0, 0);
        row.setLayoutParams(rp);
        return row;
    }

    // ---------------------------------------------------------------- 控件工厂

    private TextView sectionLabel(String s, int color, int marginTop) {
        TextView v = new TextView(a);
        v.setText(s);
        v.setTextSize(13);
        v.setTextColor(color);
        v.setTypeface(null, Typeface.BOLD);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(0, marginTop, 0, 0);
        v.setLayoutParams(p);
        return v;
    }

    private TextView text(String s, int sp, int color, int marginTop) {
        TextView v = new TextView(a);
        v.setText(s);
        v.setTextSize(sp);
        v.setTextColor(color);
        v.setLineSpacing(dp(3), 1f);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(0, marginTop, 0, 0);
        v.setLayoutParams(p);
        return v;
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
