package com.weaknet.app;

import android.content.Context;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;

/**
 * 飘窗上的「场景切换面板」——不用回到 App 就能换档位。
 *
 * 规则与 App 主界面完全一致（见 {@link WeakNet#selectScene}）：
 *  - 弱网已经在测试中：点一下立即生效（已在途的连接也跟着变）
 *  - 弱网没开启：只把它记成 current（下次点「▶ 开始弱网测试」就按它来），
 *    绝不替用户把流量悄悄限住
 *
 * 用 {@link WindowManager} 单独加一层，不走 Activity：
 *  - 不打断当前正在用的 App（弹 Activity 会把界面切走）
 *  - FLAG_NOT_FOCUSABLE：面板不抢焦点，底下的输入照常
 *  - FLAG_WATCH_OUTSIDE_TOUCH：点面板外面自动收起
 */
public class FloatScenePanel {
    private final Context app;
    private final WindowManager wm;
    private final Runnable onStateChange;

    private LinearLayout root;
    private LinearLayout list;
    private TextView tTitle;            // 标题：「当前：xxx」，换场景时跟着变
    private WindowManager.LayoutParams lp;
    private volatile boolean shown = false;

    public FloatScenePanel(Context c, Runnable onStateChange) {
        app = c.getApplicationContext();
        wm = (WindowManager) app.getSystemService(Context.WINDOW_SERVICE);
        this.onStateChange = onStateChange;
    }

    public boolean isShown() {
        return shown;
    }

    public void toggle(int anchorX, int anchorY, int anchorH) {
        if (shown) close();
        else show(anchorX, anchorY, anchorH);
    }

    public void show(int anchorX, int anchorY, int anchorH) {
        if (shown) {
            removeView();
        }
        try {
            build();
            place(anchorX, anchorY, anchorH);
            wm.addView(root, lp);
            shown = true;
            WeakNet.I.addUiWatcher(uiWatcher);   // 外面改了场景，面板也要立刻跟上
            fireState();
        } catch (Exception e) {
            shown = false;
            WeakNet.I.log("error", "场景面板显示失败：" + e.getMessage());
        }
    }

    public void close() {
        removeView();
        fireState();
    }

    /** 参数变了就重画：标题的「当前：xxx」和列表高亮跟着外部操作（API、主界面）一起动 */
    private final Runnable uiWatcher = new Runnable() {
        public void run() {
            if (shown && list != null) fill();
        }
    };

    private void fireState() {
        if (onStateChange != null) {
            try {
                onStateChange.run();
            } catch (Exception e) {
                // ignore
            }
        }
    }

    // ---------------------------------------------------------------- 布局

    private void build() {
        FloatSettings.S st = FloatSettings.get();

        root = new LinearLayout(app);
        root.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(8);
        root.setPadding(pad, pad, pad, pad);
        root.setBackgroundDrawable(box(0xF21F2430, 0x66FFFFFF, 14));

        // 标题：顺带把当前场景标出来，不用靠高亮点去猜在测哪一支
        tTitle = new TextView(app);
        tTitle.setText(headerText());
        tTitle.setTextColor(0xFFFFFFFF);
        tTitle.setTextSize(TypedValue.COMPLEX_UNIT_SP, st.textSp + 1);
        tTitle.setSingleLine(true);
        tTitle.setPadding(0, 0, 0, dp(6));
        root.addView(tTitle);

        // 列表最高 300dp：写死高度会让只有两三条自定义时也撑出一屏空白，
        // 这里改成 AT_MOST —— 内容不超高就按内容一点点长，超了才开始滚
        final int maxH = dp(300);
        ScrollView sv = new ScrollView(app) {
            @Override
            protected void onMeasure(int wms, int hms) {
                super.onMeasure(wms, View.MeasureSpec.makeMeasureSpec(
                        maxH, View.MeasureSpec.AT_MOST));
            }
        };
        sv.setVerticalScrollBarEnabled(true);
        sv.setLayoutParams(new LinearLayout.LayoutParams(
                dp(176), LinearLayout.LayoutParams.WRAP_CONTENT));

        list = new LinearLayout(app);
        list.setOrientation(LinearLayout.VERTICAL);
        sv.addView(list, new ScrollView.LayoutParams(
                ScrollView.LayoutParams.MATCH_PARENT, ScrollView.LayoutParams.WRAP_CONTENT));
        root.addView(sv);

        fill();

        // 点面板外面收起：悬浮窗不能抢焦点，靠 WATCH_OUTSIDE_TOUCH 拿 ACTION_OUTSIDE
        root.setOnTouchListener(new View.OnTouchListener() {
            public boolean onTouch(View v, MotionEvent e) {
                if (e.getAction() == MotionEvent.ACTION_OUTSIDE) {
                    close();
                    return true;
                }
                return false;
            }
        });

        lp = new WindowManager.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
                overlayType(),
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT);
        lp.gravity = Gravity.TOP | Gravity.LEFT;
        lp.alpha = Math.max(st.alpha, 0.92f);     // 面板比飘窗更实一点，方便看清字
    }

    /** 标题文案：当前场景名；没开启弱网时补一句「未生效」，免得以为已经限速了 */
    private String headerText() {
        // 组合在播时标题写「▶ 组合名 2/5」：这时候 profile 是序列里当前那一段的，
        // 直接显示段名会让人看不出整支序列走到哪了
        String prog = WeakNet.I.comboProgress();
        if (prog != null) return "▶ " + prog + (WeakNet.I.limitOn ? "" : "（未生效）");
        String n = FloatWindow.sceneName(WeakNet.I.profile);
        if (TextUtils.isEmpty(n)) n = "未知";
        return WeakNet.I.limitOn ? "当前：" + n : "当前：" + n + "（未生效）";
    }

    /** 内置预设 + 自定义场景，按当前生效的那支做高亮 */
    private void fill() {
        list.removeAllViews();
        if (tTitle != null) tTitle.setText(headerText());
        String cur = WeakNet.I.presetKey;

        for (String k : Profile.KEYS) {
            Profile p;
            try {
                p = Profile.build(k);
            } catch (Exception e) {
                continue;
            }
            if (p == null) continue;
            String shortName = Profile.shortOf(k);
            addRow(k, shortName != null && shortName.length() > 0 ? shortName : p.name, cur, false);
        }

        List<MyPresets.Item> mine;
        try {
            mine = MyPresets.load();
        } catch (Exception e) {
            mine = new ArrayList<MyPresets.Item>();
        }
        if (mine.size() > 0) {
            addSep("自定义", 0xFF9AA4B2);
            for (MyPresets.Item it : mine) {
                addRow("u:" + it.id, it.name, cur, true);
            }
        }

        // 弱网组合：点一下不是切到某支场景，而是按 steps 排队自动播放（见 selectCombo）
        List<MyCombos.Item> combos;
        try {
            combos = MyCombos.load();
        } catch (Exception e) {
            combos = new ArrayList<MyCombos.Item>();
        }
        if (combos.size() > 0) {
            addSep("我的弱网组合", 0xFF7FD6C0);
            for (MyCombos.Item c : combos) {
                String label = "🔀 " + c.name;
                String cid = WeakNet.I.comboId;
                int st = WeakNet.I.comboStep;
                int tt = WeakNet.I.comboTotal;
                if (cid != null && cid.equals(c.id) && tt > 0) {
                    // 正在播这支：把「第几段」标在名字后面，不用猜序列走到哪了
                    int cur2 = Math.max(1, Math.min(tt, st + 1));
                    label = "▶ " + c.name + " " + cur2 + "/" + tt;
                }
                addComboRow(MyCombos.KEY_PREFIX + c.id, label, cur);
            }
        }
    }

    private void addSep(String text, int color) {
        TextView sep = new TextView(app);
        sep.setText(text);
        sep.setTextColor(color);
        sep.setTextSize(TypedValue.COMPLEX_UNIT_SP, Math.max(10, st().textSp - 1));
        sep.setPadding(0, dp(6), 0, dp(2));
        list.addView(sep);
    }

    private FloatSettings.S st() {
        return FloatSettings.get();
    }

    private void addRow(String key, String label, String curKey, boolean mine) {
        boolean on = key.equals(curKey);
        TextView t = new TextView(app);
        t.setText(label);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, st().textSp);
        t.setTextColor(on ? 0xFFFFFFFF : (mine ? 0xFFD8C4FF : 0xFFD6DEEA));
        t.setPadding(dp(8), dp(7), dp(8), dp(7));
        t.setBackgroundDrawable(box(on ? (mine ? 0xFF7A4FF3 : 0xFF2F6FED) : 0x00000000,
                on ? (mine ? 0xFF7A4FF3 : 0xFF2F6FED) : 0x33FFFFFF, 8));
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        p.setMargins(0, 0, 0, dp(4));
        t.setLayoutParams(p);
        t.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                apply(key);
            }
        });
        list.addView(t);
    }

    /** 组合行：配色故意与场景行不同——点它会启动一段序列，不是切到单支静态场景 */
    private void addComboRow(String key, String label, String curKey) {
        boolean on = key.equals(curKey);
        TextView t = new TextView(app);
        t.setText(label);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, st().textSp);
        t.setTextColor(on ? 0xFFFFFFFF : 0xFFBFE9DA);
        t.setPadding(dp(8), dp(7), dp(8), dp(7));
        t.setBackgroundDrawable(box(on ? 0xFF1F8A70 : 0x00000000,
                on ? 0xFF1F8A70 : 0x33FFFFFF, 8));
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        p.setMargins(0, 0, 0, dp(4));
        t.setLayoutParams(p);
        t.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                apply(key);
            }
        });
        list.addView(t);
    }

    // ---------------------------------------------------------------- 交互

    private void apply(String key) {
        if (key != null && key.startsWith(MyCombos.KEY_PREFIX)) {
            applyCombo(key.substring(MyCombos.KEY_PREFIX.length()));
            return;
        }
        Profile p = profileOf(key);
        if (p == null) {
            Toast.makeText(app, "这条场景已不存在", Toast.LENGTH_SHORT).show();
            fill();
            return;
        }
        boolean testing = WeakNet.I.limitOn;
        WeakNet.I.selectScene(p, key);
        WeakNet.I.log("config", (testing ? "飘窗切换场景：" : "飘窗选择场景：") + p.name);
        Toast.makeText(app, testing ? ("已切换：" + p.name)
                        : ("已选「" + p.name + "」，点开始测试才生效"),
                Toast.LENGTH_SHORT).show();
        fill();                    // 高亮跟到新场景（飘窗标题由 tick 自动刷新，这里不必重建）
    }

    /**
     * 选中一支组合：弱网已在测试中就从第一段开始按顺序播（到点自动切下一段）；
     * 没开启只把它记成当前项 —— 与场景同一条规矩，不替用户偷偷开始限速。
     */
    private void applyCombo(String cid) {
        MyCombos.Item c = MyCombos.find(cid);
        if (c == null) {
            Toast.makeText(app, "这条组合已不存在", Toast.LENGTH_SHORT).show();
            fill();
            return;
        }
        boolean started = WeakNet.I.selectCombo(c);
        WeakNet.I.log("config", (started ? "飘窗播放组合：" : "飘窗选择组合：") + c.name);
        Toast.makeText(app,
                started ? ("已开始播放：「" + c.name + "」" + c.steps.size() + " 段，按顺序自动切")
                        : ("已选「" + c.name + "」，点开始测试后才按顺序播放"),
                Toast.LENGTH_SHORT).show();
        fill();
    }

    /** key → Profile：内置预设 key，或 u:<id> 形式的自定义场景 */
    private static Profile profileOf(String key) {
        if (key == null) return null;
        if (key.startsWith("u:")) {
            MyPresets.Item it = MyPresets.find(key.substring(2));
            return it == null ? null : it.profile;
        }
        for (String k : Profile.KEYS) {
            if (k.equals(key)) {
                try {
                    return Profile.build(k);
                } catch (Exception e) {
                    return null;
                }
            }
        }
        return null;
    }

    /** 面板挂在飘窗下方，放不下就翻到上方；水平方向不许出屏 */
    private void place(int anchorX, int anchorY, int anchorH) {
        android.util.DisplayMetrics dm = app.getResources().getDisplayMetrics();
        int sh = dm.heightPixels;
        root.measure(View.MeasureSpec.makeMeasureSpec(dm.widthPixels, View.MeasureSpec.AT_MOST),
                View.MeasureSpec.makeMeasureSpec(sh, View.MeasureSpec.AT_MOST));
        int ph = root.getMeasuredHeight();
        int pw = root.getMeasuredWidth();
        int x = Math.max(0, Math.min(anchorX, dm.widthPixels - pw));
        int below = anchorY + anchorH + dp(6);
        int y = below;
        if (ph > 0 && below + ph > sh) {
            int above = anchorY - ph - dp(6);
            y = Math.max(0, above);
        }
        lp.x = x;
        lp.y = y;
    }

    private void removeView() {
        WeakNet.I.removeUiWatcher(uiWatcher);   // 面板拆掉了就别再回调它（fill 会炸空 view）
        if (root != null) {
            try {
                wm.removeView(root);
            } catch (Exception e) {
                // ignore
            }
            root = null;
            list = null;
        }
        shown = false;
    }

    private static GradientDrawable box(int fill, int stroke, int r) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(fill);
        g.setCornerRadius(r);
        g.setStroke(1, stroke);
        return g;
    }

    private static int overlayType() {
        if (Build.VERSION.SDK_INT >= 26) return WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY;
        return WindowManager.LayoutParams.TYPE_PHONE;
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                app.getResources().getDisplayMetrics());
    }
}
