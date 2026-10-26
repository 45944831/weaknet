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
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * 参数说明（悬浮小窗）
 *
 * 入口是参数编辑浮层里每一行末尾的「！」。13 个参数里有一半光看名字猜不出行为：
 *   · 「丢包率」填 30 是让请求报错，还是只是变慢？
 *   · 「黑洞率」和「拒连率」都是连不上，差别在哪？
 *   · 「卡顿时长」单独填为什么一点效果都没有？
 * 这些都得翻引擎源码才能确定，所以把答案写进 {@link Profile#noteOfParam}，
 * 点一下「！」直接看到。本类只管排版，不维护任何文案。
 *
 * <b>不要在这里调 suspendFloat / restoreFloat。</b>
 * 这个浮层只会叠在参数编辑浮层上面打开，那时飘窗已经被编辑浮层收起来了；
 * 这里再 restoreFloat 会把飘窗放回来盖住编辑浮层（suspendFloat 只是一个布尔标记，
 * 不支持嵌套，两次 suspend 只对应一次 restore）。
 */
public class ParamSheet {
    private final MainActivity a;

    private Dialog dlg;
    private LinearLayout card;
    private TextView tvName;
    private TextView tvNote;

    ParamSheet(MainActivity a) {
        this.a = a;
    }

    /** @param key 参数 key（如 loss_pct）；没有对应注释时不弹空窗 */
    void show(String key) {
        if (a == null || a.isFinishing()) return;
        String note = Profile.noteOfParam(key);
        if (note.length() == 0) return;
        if (dlg == null) build();
        tvName.setText(labelOf(key));
        tvNote.setText(note);
        if (dlg != null && !dlg.isShowing()) dlg.show();
    }

    void close() {
        if (dlg != null) {
            try {
                dlg.dismiss();
            } catch (Exception e) {
                // ignore
            }
            dlg = null;
        }
    }

    boolean isShowing() {
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

        card = new LinearLayout(a);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(box(Color.WHITE, 0xFFDDE3EC, 14));
        card.setPadding(dp(14), dp(12), dp(14), dp(14));

        // ---- 标题栏：！ + 参数名 + ✕ ----
        LinearLayout head = new LinearLayout(a);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView bang = new TextView(a);
        bang.setText("！");
        bang.setTextSize(15);
        bang.setTextColor(0xFF2F6FED);
        bang.setTypeface(null, Typeface.BOLD);
        head.addView(bang);
        tvName = new TextView(a);
        tvName.setTextSize(15);
        tvName.setTextColor(0xFF1F2430);
        tvName.setTypeface(null, Typeface.BOLD);
        tvName.setPadding(dp(6), 0, 0, 0);
        head.addView(tvName, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView close = new TextView(a);
        close.setText("✕");
        close.setTextSize(17);
        close.setTextColor(0xFF8A94A6);
        close.setPadding(dp(10), dp(2), dp(4), dp(2));
        close.setContentDescription("关闭参数说明");
        close.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                close();
            }
        });
        head.addView(close);
        card.addView(head);
        card.addView(divider(dp(8)));

        tvNote = new TextView(a);
        tvNote.setTextSize(13);
        tvNote.setTextColor(0xFF3A4250);
        tvNote.setLineSpacing(dp(3), 1f);
        card.addView(tvNote);

        Button ok = new Button(a);
        ok.setAllCaps(false);
        ok.setText("知道了");
        ok.setTextSize(14);
        ok.setTextColor(0xFF2F6FED);
        ok.setBackground(box(0xFFF1F4FA, 0xFFD7DEEA, 8));
        ok.setPadding(dp(14), dp(8), dp(14), dp(8));
        ok.setContentDescription("关闭参数说明");
        ok.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                close();
            }
        });
        LinearLayout.LayoutParams op = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        op.gravity = Gravity.END;
        op.setMargins(0, dp(12), 0, 0);
        card.addView(ok, op);

        int m = dp(16);
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
            // 部分系统上 WRAP_CONTENT 的 Dialog 会被压得很矮，内容只能在小窗里滚。
            // 显示后按内容实际高度重设，最多不超过 80% 屏高（说明只有几行，一般用不满）。
            dlg.setOnShowListener(new DialogInterface.OnShowListener() {
                public void onShow(DialogInterface di) {
                    resize();
                }
            });
        }
    }

    /** 显示后按内容高度重设浮层：说明文字短，窗口贴着内容走 */
    private void resize() {
        Window w = dlg == null ? null : dlg.getWindow();
        if (w == null || card == null) return;
        try {
            int wide = a.getResources().getDisplayMetrics().widthPixels - dp(32);
            card.measure(
                    View.MeasureSpec.makeMeasureSpec(wide, View.MeasureSpec.EXACTLY),
                    View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
            int h = card.getMeasuredHeight() + dp(32);
            int maxH = (int) (a.getResources().getDisplayMetrics().heightPixels * 0.8);
            w.setLayout(WindowManager.LayoutParams.MATCH_PARENT, Math.min(h, maxH));
        } catch (Exception e) {
            // 测量失败就保持系统默认，不影响功能
        }
    }

    // ---------------------------------------------------------------- 控件工厂

    /** 参数显示名（含单位），直接取编辑界面那份定义，避免两处各写一套 */
    private String labelOf(String key) {
        for (String[] f : MainPanels.FIELDS) {
            if (f[0].equals(key)) return f[1];
        }
        return key;
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
