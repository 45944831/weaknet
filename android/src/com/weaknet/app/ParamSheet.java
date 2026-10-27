package com.weaknet.app;

import android.app.Dialog;
import android.content.DialogInterface;
import android.graphics.Color;
import android.graphics.Paint;
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

    // ---------------------------------------------------------------- 排版常量
    //
    // 与桌面版 weaknet_gui.py 的 NOTE_* 一一对应，两端保持一样。
    // 折行由引擎层（Profile.wrapNoteLines）做，不交给 TextView 自己断：
    // 安卓的断行规则跟桌面端不同，同一段文案在两端会被断在不同位置；
    // 而且「·」条目符会被甩到单独一行，「「本机 → 服务器」」会被从中间劈开。
    private static final int NOTE_PAD = 14;           // 卡片内左右留白（dp）
    private static final int NOTE_TIP_INDENT = 14;    // 「·」条目符占的宽度（dp）
    private static final float NOTE_TEXT_SIZE = 14f;  // 正文字号（sp）
    private static final float NOTE_HEAD_SIZE = 15f;  // 标题字号（sp）

    private Dialog dlg;
    private LinearLayout card;
    private TextView tvName;
    private TextView tvBody;
    private LinearLayout tvTipsBox;
    private String currentKey = "";

    /** 量文字宽度用（安卓端按真实字体测，桌面端按 Tk 字体测） */
    private final Paint notePaint = new Paint();

    ParamSheet(MainActivity a) {
        this.a = a;
    }

    /** @param key 参数 key（如 loss_pct）；没有对应注释时不弹空窗 */
    void show(String key) {
        if (a == null || a.isFinishing()) return;
        String note = Profile.noteOfParam(key);
        if (note.length() == 0) return;
        if (dlg == null) build();
        currentKey = key;
        tvName.setText(labelOf(key));
        // 正文分两层：作用一段、提醒若干条（规则与桌面版 split_param_note 一致）
        Object[] parts = Profile.splitNote(key);
        String body = parts[0] == null ? "" : (String) parts[0];
        tvBody.setText(wrap(body.length() == 0 ? "（暂无说明）" : body,
                textWidth()));

        tvTipsBox.removeAllViews();
        java.util.List<?> tips = (java.util.List<?>) parts[1];
        if (tips != null && !tips.isEmpty()) {
            tvTipsBox.addView(sectionLabel("注意", 0xFFD97706, dp(12)));
            for (int i = 0; i < tips.size(); i++) {
                tvTipsBox.addView(tipRow((String) tips.get(i),
                        i == 0 ? dp(4) : dp(3)));
            }
        }
        if (dlg != null && !dlg.isShowing()) dlg.show();
    }

    /**
     * 一条提醒：左边一个「·」，右边一块正文。
     *
     * 「·」必须单独一个控件。之前跟正文拼成同一个字符串（"· " + tip），
     * TextView 会把「·」当成一个词，单独甩到一行 —— 用户截图里那行光秃秃的
     * 「·」就是这么来的。拆开后符号固定待在左边，正文整体缩进，悬挂缩进。
     */
    private View tipRow(String tip, int marginTop) {
        LinearLayout row = new LinearLayout(a);
        row.setOrientation(LinearLayout.HORIZONTAL);

        TextView dot = new TextView(a);
        dot.setText("·");
        dot.setTextSize(NOTE_TEXT_SIZE);
        dot.setTextColor(0xFF1F2430);
        LinearLayout.LayoutParams dotLp = new LinearLayout.LayoutParams(
                dp(NOTE_TIP_INDENT), ViewGroup.LayoutParams.WRAP_CONTENT);
        row.addView(dot, dotLp);

        TextView t = new TextView(a);
        t.setText(wrap(tip, textWidth() - dp(NOTE_TIP_INDENT)));
        t.setTextSize(NOTE_TEXT_SIZE);
        t.setTextColor(0xFF1F2430);
        t.setLineSpacing(dp(4), 1f);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        row.addView(t, lp);

        LinearLayout.LayoutParams rp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rp.setMargins(0, marginTop, 0, 0);
        row.setLayoutParams(rp);
        return row;
    }

    /** 正文可用宽度（像素）：屏幕宽减去卡片与浮层的留白 */
    private int textWidth() {
        int wide = a.getResources().getDisplayMetrics().widthPixels;
        return Math.max(dp(120), wide - dp(16) * 2 - NOTE_PAD * 2 - dp(2));
    }

    /** 用引擎层规则折行，返回带 \n 的文本 */
    private String wrap(String text, int maxWidth) {
        java.util.List<String> lines = Profile.wrapNoteLines(text, new Profile.NoteMeasurer() {
            public int measure(String s) {
                return (int) Math.ceil(notePaint.measureText(s));
            }
        }, maxWidth);
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < lines.size(); i++) {
            if (i > 0) sb.append('\n');
            sb.append(lines.get(i));
        }
        return sb.toString();
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

        // 测宽用的 Paint：字号必须跟正文 TextView 完全一致，
        // 否则量出来的宽度跟实际排版差一截，断行位置就全错了
        notePaint.setAntiAlias(false);
        notePaint.setTextSize(NOTE_TEXT_SIZE * a.getResources()
                .getDisplayMetrics().scaledDensity);
        notePaint.setTypeface(Typeface.DEFAULT);

        ScrollView sv = new ScrollView(a);
        sv.setBackgroundColor(Color.TRANSPARENT);

        card = new LinearLayout(a);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(box(Color.WHITE, 0xFFDDE3EC, 14));
        card.setPadding(dp(14), dp(12), dp(14), dp(14));

        // ---- 标题栏：! + 参数名 + ✕ ----
        // 用半角「!」，不用全角「！」：全角感叹号在中文正文字体里占一整格，
        // 墨迹却挤在格子左缘，跟参数名之间凭空多出一大段空隙，看起来像浮在半空。
        LinearLayout head = new LinearLayout(a);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView bang = new TextView(a);
        bang.setText("!");
        bang.setTextSize(NOTE_HEAD_SIZE);
        bang.setTextColor(0xFF2F6FED);
        bang.setTypeface(null, Typeface.BOLD);
        head.addView(bang);
        tvName = new TextView(a);
        tvName.setTextSize(NOTE_HEAD_SIZE);
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
        card.addView(divider(dp(10)));

        // ---- 单位 / 取值范围：两枚小标签，一眼分得清哪个是单位、哪个是上限 ----
        LinearLayout chips = new LinearLayout(a);
        chips.setOrientation(LinearLayout.HORIZONTAL);
        chips.setPadding(0, dp(2), 0, 0);
        chips.addView(chip("单位", unitOf(currentKey)));
        chips.addView(chip("取值范围", "0 ~ " + topOf(currentKey)));
        card.addView(chips);
        card.addView(divider(dp(10)));

        // ---- 正文：分「作用」与「注意」两层 ----
        // 之前整段糊在一起，读者分不清哪句是提醒，最容易踩的那句反倒被略过
        tvBody = new TextView(a);
        tvBody.setTextSize(NOTE_TEXT_SIZE);
        tvBody.setTextColor(0xFF1F2430);
        tvBody.setLineSpacing(dp(4), 1f);
        card.addView(sectionLabel("作用", 0xFF6B7280, dp(10)));
        card.addView(tvBody);

        tvTipsBox = new LinearLayout(a);
        tvTipsBox.setOrientation(LinearLayout.VERTICAL);
        card.addView(tvTipsBox);

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
        op.setMargins(0, dp(14), 0, 0);
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

    /** 这项的单位（可能为空串） */
    private String unitOf(String key) {
        return Profile.unitOfParam(key);
    }

    /** 这项的取值上限 */
    private String topOf(String key) {
        return String.valueOf(Profile.limitOfParam(key));
    }

    /** 小标题：「作用」/「注意」 */
    private TextView sectionLabel(String text, int color, int marginTop) {
        TextView v = new TextView(a);
        v.setText(text);
        v.setTextSize(12);
        v.setTextColor(color);
        v.setTypeface(null, Typeface.BOLD);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(0, marginTop, 0, 0);
        v.setLayoutParams(p);
        return v;
    }

    /** 一枚小标签：左侧标题（浅底）+ 右侧取值 */
    private View chip(String name, String value) {
        LinearLayout box = new LinearLayout(a);
        box.setOrientation(LinearLayout.HORIZONTAL);
        box.setBackground(box2(0xFFF1F4FA, 0xFFDDE3EC, 6));
        box.setPadding(dp(8), dp(3), dp(8), dp(3));
        TextView n = new TextView(a);
        n.setText(name);
        n.setTextSize(12);
        n.setTextColor(0xFF6B7280);
        box.addView(n);
        TextView v = new TextView(a);
        v.setText(value);
        v.setTextSize(12);
        v.setTextColor(0xFF1F2430);
        v.setTypeface(null, Typeface.BOLD);
        v.setPadding(dp(6), 0, 0, 0);
        box.addView(v);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(0, 0, dp(6), 0);
        box.setLayoutParams(p);
        return box;
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

    /** 同上，但指定圆角 —— 小标签的圆角比卡片小一号 */
    private GradientDrawable box2(int fill, int stroke, int radiusDp) {
        return box(fill, stroke, radiusDp);
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                a.getResources().getDisplayMetrics());
    }
}
