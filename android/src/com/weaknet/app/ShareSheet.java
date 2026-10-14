package com.weaknet.app;

import android.app.Dialog;
import android.content.DialogInterface;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 环境共享（悬浮二级界面）
 *
 * 从设置浮层里「⭐ 我的自定义」标题后的「🔗 环境共享」按钮弹出 —— 共享的就是那些
 * 自定义场景，入口放在它们旁边最顺手。协议与桌面端完全一致，Windows ↔ Android 可互推。
 *
 * 界面里三个列表每 3 秒自动刷新一次（要能看到新上线的设备和别人刚推来的环境）。
 * 勾选状态刻意存在 Set 里而不是 View 上：重建列表时按 id 回填，
 * 才不会「刚勾好就被下一次刷新冲掉、点了推送没反应」。
 */
public class ShareSheet {
    private static final int ACCENT = 0xFF2F6FED;
    private static final int PURPLE = 0xFF7A4FF3;

    private final MainActivity a;
    private final Handler h = new Handler();

    private Dialog dlg;
    private LinearLayout card;
    private EditText edtName;
    private LinearLayout peerBox;
    private LinearLayout mineBox;
    private LinearLayout inboxBox;
    private Button btnPush;
    private Button btnOver;
    private Button btnKeep;
    private Button btnRename;
    private TextView tip;

    /** 勾选状态存这里，列表重建后按 id 回填 */
    private final Set<String> pickPeers = new HashSet<String>();
    private final Set<String> pickMine = new HashSet<String>();
    private String pickInbox = "";

    private final Runnable tick = new Runnable() {
        public void run() {
            if (dlg == null || !dlg.isShowing()) return;
            reload();
            h.postDelayed(this, 3000);
        }
    };

    ShareSheet(MainActivity a) {
        this.a = a;
    }

    public void show() {
        if (a == null || a.isFinishing()) return;
        if (dlg == null) build();
        reload();
        if (dlg != null && !dlg.isShowing()) dlg.show();
        h.removeCallbacks(tick);
        h.postDelayed(tick, 3000);
    }

    public void close() {
        h.removeCallbacks(tick);
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
        card.setPadding(dp(14), dp(12), dp(14), dp(14));
        this.card = card;

        // ---- 标题栏 ----
        LinearLayout head = new LinearLayout(a);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView t = new TextView(a);
        t.setText("🔗 环境共享");
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
        card.addView(divider(dp(8)));

        // ---- 我的名字 ----
        card.addView(label("我的名字（局域网里显示给别人的）"));
        LinearLayout nrow = new LinearLayout(a);
        nrow.setOrientation(LinearLayout.HORIZONTAL);
        nrow.setGravity(Gravity.CENTER_VERTICAL);
        edtName = new EditText(a);
        edtName.setSingleLine(true);
        edtName.setTextSize(13);
        edtName.setTextColor(0xFF1F2430);
        edtName.setInputType(InputType.TYPE_CLASS_TEXT);
        edtName.setPadding(dp(8), dp(7), dp(8), dp(7));
        edtName.setBackground(box(0xFFF7F9FC, 0xFFD7DEEA, 8));
        nrow.addView(edtName, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        Button btnName = new Button(a);
        btnName.setAllCaps(false);
        btnName.setTextSize(12);
        btnName.setText("保存");
        btnName.setTextColor(Color.WHITE);
        btnName.setBackground(box(ACCENT, ACCENT, 8));
        btnName.setPadding(dp(12), dp(6), dp(12), dp(6));
        btnName.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                saveName();
            }
        });
        LinearLayout.LayoutParams np = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        np.setMargins(dp(8), 0, 0, 0);
        nrow.addView(btnName, np);
        card.addView(nrow);

        // ---- 设备 ----
        card.addView(divider(dp(8)));
        card.addView(label("局域网里的设备（可多选）"));
        peerBox = new LinearLayout(a);
        peerBox.setOrientation(LinearLayout.VERTICAL);
        card.addView(peerBox);

        // ---- 我的自定义环境 ----
        card.addView(divider(dp(8)));
        card.addView(label("我的自定义环境（可多选）"));
        mineBox = new LinearLayout(a);
        mineBox.setOrientation(LinearLayout.VERTICAL);
        card.addView(mineBox);

        btnPush = new Button(a);
        btnPush.setAllCaps(false);
        btnPush.setTextSize(14);
        btnPush.setText("→ 推送所选环境");
        btnPush.setTextColor(Color.WHITE);
        btnPush.setBackground(box(ACCENT, ACCENT, 8));
        btnPush.setPadding(dp(12), dp(10), dp(12), dp(10));
        btnPush.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                push();
            }
        });
        card.addView(btnPush, full(0, 6, 0, 2));

        // ---- 收件箱 ----
        card.addView(divider(dp(8)));
        card.addView(label("收到的环境（同名待处理）"));
        inboxBox = new LinearLayout(a);
        inboxBox.setOrientation(LinearLayout.VERTICAL);
        card.addView(inboxBox);

        LinearLayout brow = new LinearLayout(a);
        brow.setOrientation(LinearLayout.HORIZONTAL);
        btnOver = smallBtn("覆盖本地", new View.OnClickListener() {
            public void onClick(View v) {
                resolve("overwrite");
            }
        });
        brow.addView(btnOver);
        btnKeep = smallBtn("保留本地", new View.OnClickListener() {
            public void onClick(View v) {
                resolve("keep");
            }
        });
        brow.addView(btnKeep, pad(dp(8), 0, 0, 0));
        btnRename = smallBtn("另存为…", new View.OnClickListener() {
            public void onClick(View v) {
                resolve("rename");
            }
        });
        brow.addView(btnRename, pad(dp(8), 0, 0, 0));
        card.addView(brow, full(0, 6, 0, 2));

        tip = hint("");
        card.addView(tip);

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
            dlg.setOnShowListener(new DialogInterface.OnShowListener() {
                public void onShow(DialogInterface di) {
                    resize();
                }
            });
        }
    }

    private void resize() {
        Window w = dlg == null ? null : dlg.getWindow();
        if (w == null || card == null) return;
        try {
            int wide = a.getResources().getDisplayMetrics().widthPixels - dp(24);
            card.measure(
                    View.MeasureSpec.makeMeasureSpec(wide, View.MeasureSpec.EXACTLY),
                    View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
            int h2 = card.getMeasuredHeight() + dp(24);
            int maxH = (int) (a.getResources().getDisplayMetrics().heightPixels * 0.9);
            w.setLayout(WindowManager.LayoutParams.MATCH_PARENT, Math.min(h2, maxH));
        } catch (Exception e) {
            // ignore
        }
    }

    // ---------------------------------------------------------------- 数据

    private void reload() {
        if (dlg == null) return;
        if (edtName.getText().length() == 0) edtName.setText(ShareNet.getUserName());

        renderPeers();
        renderMine();
        renderInbox();
        syncButtons();
        tip.setText("本机：" + ShareNet.getUserName() + " · " + WeakNet.I.lanIp()
                + " · 共享端口 " + (ShareNet.statusJson().optInt("share_port", 0) == 0
                ? "未开启" : ShareNet.statusJson().optInt("share_port", 0))
                + "；对方也要开着 WeakNet 才会出现在这里。");
    }

    private void renderPeers() {
        peerBox.removeAllViews();
        // 已勾的设备若已下线（超时消失），顺手从勾选集合里剔掉，免得推送时找不到
        List<ShareNet.Peer> ps = ShareNet.peers();
        Set<String> alive = new HashSet<String>();
        for (ShareNet.Peer p : ps) alive.add(p.id);
        pickPeers.retainAll(alive);

        if (ps.isEmpty()) {
            peerBox.addView(hint("（暂未发现其它设备：对方也要开着 WeakNet，并在同一局域网）"));
            return;
        }
        for (final ShareNet.Peer p : ps) {
            boolean on = pickPeers.contains(p.id);
            String age = (int) ((System.currentTimeMillis() - p.at) / 1000) + "s前";
            Button b = rowBtn(p.name + " · " + p.ip + " · "
                    + (p.platform.length() > 0 ? p.platform + " · " : "") + age, on, ACCENT);
            b.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    if (pickPeers.contains(p.id)) pickPeers.remove(p.id);
                    else pickPeers.add(p.id);
                    renderPeers();
                    syncButtons();
                }
            });
            peerBox.addView(b);
        }
    }

    private void renderMine() {
        mineBox.removeAllViews();
        List<MyPresets.Item> items = MyPresets.load();
        Set<String> alive = new HashSet<String>();
        for (MyPresets.Item it : items) alive.add(it.id);
        pickMine.retainAll(alive);

        if (items.isEmpty()) {
            mineBox.addView(hint("（还没有自定义场景：先在设置里保存一条，才能推给别人）"));
            return;
        }
        for (final MyPresets.Item it : items) {
            boolean on = pickMine.contains(it.id);
            Button b = rowBtn(it.name + " · " + brief(it.profile), on, PURPLE);
            b.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    if (pickMine.contains(it.id)) pickMine.remove(it.id);
                    else pickMine.add(it.id);
                    renderMine();
                    syncButtons();
                }
            });
            mineBox.addView(b);
        }
    }

    private void renderInbox() {
        inboxBox.removeAllViews();
        List<ShareNet.InboxItem> items = ShareNet.inboxList();
        Set<String> alive = new HashSet<String>();
        for (ShareNet.InboxItem it : items) alive.add(it.id);
        if (!alive.contains(pickInbox)) pickInbox = "";

        if (items.isEmpty()) {
            inboxBox.addView(hint("（收件箱为空：别人推来同名环境时会出现在这里）"));
            return;
        }
        for (final ShareNet.InboxItem it : items) {
            boolean on = it.id.equals(pickInbox);
            Button b = rowBtn(it.from + " 推送「" + it.name + "」 · "
                    + (int) ((System.currentTimeMillis() - it.at) / 1000) + "s前", on, 0xFFD93025);
            b.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    pickInbox = it.id.equals(pickInbox) ? "" : it.id;
                    renderInbox();
                    syncButtons();
                }
            });
            inboxBox.addView(b);
        }
    }

    private void syncButtons() {
        btnPush.setEnabled(!pickPeers.isEmpty() && !pickMine.isEmpty());
        boolean has = pickInbox.length() > 0;
        btnOver.setEnabled(has);
        btnKeep.setEnabled(has);
        btnRename.setEnabled(has);
    }

    private String brief(Profile p) {
        StringBuilder sb = new StringBuilder();
        if (p.downKbps > 0) sb.append("↓").append(p.downKbps >= 1000 ? (p.downKbps / 1000) + "M" : p.downKbps + "k");
        if (p.upKbps > 0) sb.append(" ↑").append(p.upKbps >= 1000 ? (p.upKbps / 1000) + "M" : p.upKbps + "k");
        if (p.latencyDownMs > 0) sb.append(" 延迟").append(p.latencyDownMs).append("ms");
        if (p.lossPct > 0) sb.append(" 丢包").append((long) p.lossPct).append("%");
        return sb.length() > 0 ? sb.toString() : "不限制";
    }

    // ---------------------------------------------------------------- 动作

    private void saveName() {
        String n = edtName.getText() == null ? "" : edtName.getText().toString().trim();
        if (n.length() == 0) {
            Toast.makeText(a, "名字不能为空", Toast.LENGTH_SHORT).show();
            return;
        }
        ShareNet.setUserName(n);
        Toast.makeText(a, "已保存，局域网里显示为：" + ShareNet.getUserName(), Toast.LENGTH_SHORT).show();
        reload();
    }

    private void push() {
        if (pickPeers.isEmpty() || pickMine.isEmpty()) return;
        final List<ShareNet.Peer> targets = new ArrayList<ShareNet.Peer>();
        for (ShareNet.Peer p : ShareNet.peers()) {
            if (pickPeers.contains(p.id)) targets.add(p);
        }
        final List<String> ids = new ArrayList<String>(pickMine);
        btnPush.setEnabled(false);
        btnPush.setText("推送中…");
        Thread t = new Thread(new Runnable() {
            public void run() {
                final StringBuilder msg = new StringBuilder();
                for (ShareNet.Peer p : targets) {
                    ShareNet.PushResult r = ShareNet.push(p, ids);
                    if (msg.length() > 0) msg.append("\n");
                    msg.append(r.msg);
                }
                h.post(new Runnable() {
                    public void run() {
                        Toast.makeText(a, msg.toString(), Toast.LENGTH_LONG).show();
                        WeakNet.I.log("share", msg.toString());
                        btnPush.setText("→ 推送所选环境");
                        syncButtons();
                    }
                });
            }
        }, "share-push");
        t.setDaemon(true);
        t.start();
    }

    private void resolve(String action) {
        if (pickInbox.length() == 0) return;
        final String id = pickInbox;
        final String act = action;
        Thread t = new Thread(new Runnable() {
            public void run() {
                // 另存：自动用「原名（来自 XXX）」，手机上再弹输入框太重
                final org.json.JSONObject r = ShareNet.resolveInbox(id, act, null);
                h.post(new Runnable() {
                    public void run() {
                        String nm = r.optString("name", "");
                        Toast.makeText(a, "keep".equals(act) ? "已保留本地版本"
                                        : ("rename".equals(act) ? "已另存为「" + nm + "」"
                                        : "已覆盖为「" + nm + "」"),
                                Toast.LENGTH_SHORT).show();
                        pickInbox = "";
                        reload();
                        if (a != null) a.updateStatus();
                    }
                });
            }
        }, "share-inbox");
        t.setDaemon(true);
        t.start();
    }

    // ---------------------------------------------------------------- 控件工厂

    private Button rowBtn(String text, boolean on, int accent) {
        Button b = new Button(a);
        b.setAllCaps(false);
        b.setTextSize(12);
        b.setSingleLine(true);
        b.setText(text);
        b.setBackground(box(on ? accent : 0xFFF1F4FA, on ? accent : 0xFFD7DEEA, 8));
        b.setTextColor(on ? Color.WHITE : 0xFF2F3F52);
        b.setPadding(dp(10), dp(8), dp(10), dp(8));
        b.setLayoutParams(full(0, 3, 0, 3));
        return b;
    }

    private Button smallBtn(String text, View.OnClickListener l) {
        Button b = new Button(a);
        b.setAllCaps(false);
        b.setTextSize(12);
        b.setText(text);
        b.setTextColor(0xFF2F3F52);
        b.setBackground(box(0xFFF1F4FA, 0xFFD7DEEA, 8));
        b.setPadding(dp(10), dp(6), dp(10), dp(6));
        b.setOnClickListener(l);
        return b;
    }

    private LinearLayout.LayoutParams full(int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(dp(l), dp(t), dp(r), dp(b));
        return p;
    }

    private LinearLayout.LayoutParams pad(int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(dp(l), dp(t), dp(r), dp(b));
        return p;
    }

    private TextView label(String text) {
        TextView t = new TextView(a);
        t.setText(text);
        t.setTextSize(13);
        t.setTextColor(0xFF5A6472);
        t.setPadding(dp(2), dp(2), dp(2), dp(4));
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
