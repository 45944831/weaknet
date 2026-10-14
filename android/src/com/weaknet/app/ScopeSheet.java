package com.weaknet.app;

import android.app.Dialog;
import android.content.DialogInterface;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.CompoundButton;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 「指定程序」二级界面（接管范围）——从主界面顶部「接管一切」旁边的按钮弹出。
 *
 * Android 端靠 VpnService 按包名过滤，天然就能「只对这几个 App 生效」，
 * 所以这里给出三种范围：
 *   · 接管一切：全部 App 都进弱网
 *   · 仅选中的 App：名单里的进弱网，其余完全不受影响
 *   · 排除选中的 App：名单里的不受影响，其余都进弱网
 *
 * 改完范围必须重建 tun（见 {@link VpnNetService#rebuild}），否则对已有连接无效。
 */
public class ScopeSheet {
    private final MainActivity a;

    private Dialog dlg;
    private LinearLayout card;
    private LinearLayout blockPick;      // 搜索 + 全选/清空（「接管一切」时整块隐藏）
    private View listWrap;               // 列表滚动区（同上，隐藏时列表也不占位）
    private LinearLayout listHost;
    private EditText edSearch;
    private TextView tvCount;
    private TextView emptyTip;      // 搜索无结果时的提示（复用同一个，避免重复 new）
    private Button btnAll;
    private Button btnOnly;
    private Button btnExclude;

    /** 当前编辑中的模式（保存后才写进 WeakNet.I） */
    private String mode = "all";
    /** 当前编辑中的勾选：包名集合 */
    private final Set<String> sel = new LinkedHashSet<String>();
    private final Map<String, CheckBox> boxes = new LinkedHashMap<String, CheckBox>();
    private final List<AppList.Row> apps = new ArrayList<AppList.Row>();

    ScopeSheet(MainActivity a) {
        this.a = a;
    }

    // ---------------------------------------------------------------- 生命周期

    public void show() {
        if (a == null || a.isFinishing()) return;
        if (dlg == null) build();
        load();
        if (dlg != null) {
            dlg.setOnDismissListener(new DialogInterface.OnDismissListener() {
                public void onDismiss(DialogInterface d) {
                    a.restoreFloat();          // 飘窗是系统悬浮窗，弹窗期间收起过
                }
            });
        }
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

    /** 每次打开都从引擎读回当前生效值，免得显示的和实际的不一致 */
    private void load() {
        mode = WeakNet.I.scopeMode;
        if (mode == null) mode = "all";
        sel.clear();
        synchronized (WeakNet.I.scopePkgs) {
            sel.addAll(WeakNet.I.scopePkgs);
        }
        // 每次打开都重画列表：show() 里 dlg 可能是刚重建的（listHost 是全新的空容器），
        // 只在「apps 为空」时才 renderList 会导致第二次打开只剩一个空壳，勾选框也对不上。
        if (apps.isEmpty()) {
            apps.clear();
            apps.addAll(AppList.load(a));
        }
        renderList();
        applyMode();
        applyFilter();
    }

    // ---------------------------------------------------------------- 布局

    private void build() {
        dlg = new Dialog(a);
        dlg.requestWindowFeature(Window.FEATURE_NO_TITLE);
        dlg.setCancelable(true);
        dlg.setCanceledOnTouchOutside(true);

        card = new LinearLayout(a);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(box(Color.WHITE, 0xFFDDE3EC, 14));
        card.setPadding(dp(14), dp(12), dp(14), dp(12));

        // ---- 标题行（右侧 ✕ 关闭）----
        LinearLayout head = new LinearLayout(a);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView t = new TextView(a);
        t.setText("🎯 指定程序（接管范围）");
        t.setTextSize(16);
        t.setTextColor(0xFF1F2430);
        t.setTypeface(null, Typeface.BOLD);
        head.addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView close = new TextView(a);
        close.setText("✕");
        close.setTextSize(17);
        close.setTextColor(0xFF8A94A6);
        close.setPadding(dp(10), dp(2), dp(4), dp(2));
        close.setContentDescription("关闭指定程序");
        close.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                close();
            }
        });
        head.addView(close);
        card.addView(head);
        card.addView(divider(dp(8)));

        // ---- 三种范围（一行三按钮，当前那个高亮）----
        LinearLayout modes = new LinearLayout(a);
        modes.setOrientation(LinearLayout.HORIZONTAL);
        btnAll = modeBtn("接管一切", "all");
        btnOnly = modeBtn("仅选中的", "only");
        btnExclude = modeBtn("排除选中的", "exclude");
        modes.addView(btnAll, weight(0));
        modes.addView(btnOnly, weight(1));
        modes.addView(btnExclude, weight(1));
        card.addView(modes, full(0, 0, 0, 6));

        // ---- 搜索 / 全选（「接管一切」时整块收起）----
        blockPick = new LinearLayout(a);
        blockPick.setOrientation(LinearLayout.VERTICAL);

        edSearch = new EditText(a);
        edSearch.setHint("搜索应用名或包名");
        edSearch.setTextSize(13);
        edSearch.setSingleLine(true);
        edSearch.setInputType(InputType.TYPE_CLASS_TEXT);
        edSearch.setContentDescription("搜索应用");
        edSearch.addTextChangedListener(new TextWatcher() {
            public void beforeTextChanged(CharSequence s, int st, int c, int af) {
            }

            public void onTextChanged(CharSequence s, int st, int b, int c) {
            }

            public void afterTextChanged(Editable s) {
                applyFilter();
            }
        });
        blockPick.addView(edSearch, full(0, 0, 0, 4));

        LinearLayout picks = new LinearLayout(a);
        picks.setOrientation(LinearLayout.HORIZONTAL);
        tvCount = new TextView(a);
        tvCount.setTextSize(12);
        tvCount.setTextColor(0xFF5A6472);
        picks.addView(tvCount, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        Button btnPickAll = new Button(a);
        btnPickAll.setAllCaps(false);
        btnPickAll.setTextSize(12);
        btnPickAll.setText("全选");
        btnPickAll.setTextColor(0xFF2F6FED);
        btnPickAll.setBackground(box(0xFFF1F4FA, 0xFFD7DEEA, 8));
        btnPickAll.setPadding(dp(12), dp(4), dp(12), dp(4));
        btnPickAll.setContentDescription("全选应用");
        btnPickAll.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                for (AppList.Row r : apps) {
                    if (visible(r)) sel.add(r.id);
                }
                syncBoxes();
            }
        });
        picks.addView(btnPickAll);
        Button btnPickNone = new Button(a);
        btnPickNone.setAllCaps(false);
        btnPickNone.setTextSize(12);
        btnPickNone.setText("清空");
        btnPickNone.setTextColor(0xFF5A6472);
        btnPickNone.setBackground(box(0xFFFFFFFF, 0xFFD7DEEA, 8));
        btnPickNone.setPadding(dp(12), dp(4), dp(12), dp(4));
        btnPickNone.setContentDescription("清空选择");
        btnPickNone.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                sel.clear();
                syncBoxes();
            }
        });
        LinearLayout.LayoutParams np = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        np.setMargins(dp(6), 0, 0, 0);
        picks.addView(btnPickNone, np);
        blockPick.addView(picks, full(0, 0, 0, 4));
        card.addView(blockPick);

        // ---- 列表：吃掉剩余高度（weight 1），不再写死高度 ----
        // 写死 300dp 的坑：内容一超高（大字体 / 小屏 / 系统栏占用），LinearLayout 会把
        // 这个 ScrollView 压成 0 高，表现就是「界面上只剩一个搜索框，看不到任何应用」。
        ScrollView sv = new ScrollView(a);
        sv.setBackground(box(0xFFFFFFFF, 0xFFE3E8EF, 8));
        listHost = new LinearLayout(a);
        listHost.setOrientation(LinearLayout.VERTICAL);
        sv.addView(listHost);
        listWrap = sv;
        card.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        // ---- 底部：取消 + 保存并应用 ----
        Button cancel = new Button(a);
        cancel.setAllCaps(false);
        cancel.setTextSize(14);
        cancel.setText("取消");
        cancel.setTextColor(0xFF5A6472);
        cancel.setBackground(box(0xFFFFFFFF, 0xFFD7DEEA, 8));
        cancel.setPadding(dp(12), dp(11), dp(12), dp(11));
        cancel.setContentDescription("取消指定程序");
        cancel.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                close();
            }
        });
        Button save = new Button(a);
        save.setAllCaps(false);
        save.setTextSize(14);
        save.setText("保存并应用");
        save.setTextColor(Color.WHITE);
        save.setBackground(box(0xFF2F6FED, 0xFF2F6FED, 8));
        save.setPadding(dp(12), dp(11), dp(12), dp(11));
        save.setContentDescription("保存指定程序");
        save.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                saveScope();
            }
        });
        LinearLayout foot = new LinearLayout(a);
        foot.setOrientation(LinearLayout.HORIZONTAL);
        foot.addView(cancel, weight(0));
        foot.addView(save, weight(1));
        card.addView(foot, full(0, dp(10), 0, 0));

        LinearLayout outer = new LinearLayout(a);
        outer.setOrientation(LinearLayout.VERTICAL);
        outer.addView(card, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        int m = dp(12);
        FrameLayout.LayoutParams ol = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        ol.setMargins(m, m, m, m);
        dlg.setContentView(outer, ol);

        Window w = dlg.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            w.setLayout(WindowManager.LayoutParams.MATCH_PARENT,
                    WindowManager.LayoutParams.WRAP_CONTENT);
            w.setGravity(Gravity.CENTER);
            w.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
            dlg.setOnShowListener(new DialogInterface.OnShowListener() {
                public void onShow(DialogInterface di) {
                    fitHeight();
                }
            });
        }
    }

    /**
     * 重设窗口高度：
     *   · 「接管一切」——内容就三行，按实测高度收着显示，不做成占满屏的大窗口；
     *   · 其余两种——固定给到 88% 屏高，列表用剩余空间滚动。
     *
     * 为什么不全都按内容测高：应用动辄上百个，内容高度远超屏幕，一旦被 maxH 截断，
     * LinearLayout 会把列表 ScrollView 压成 0 高（只看见搜索框），底部按钮也会被挤出去。
     */
    private void fitHeight() {
        if (dlg == null) return;
        Window win = dlg.getWindow();
        if (win == null) return;
        try {
            int maxH = (int) (a.getResources().getDisplayMetrics().heightPixels * 0.88f);
            if ("all".equals(mode)) {
                int wide = a.getResources().getDisplayMetrics().widthPixels - dp(24);
                card.measure(
                        View.MeasureSpec.makeMeasureSpec(wide, View.MeasureSpec.EXACTLY),
                        View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
                win.setLayout(WindowManager.LayoutParams.MATCH_PARENT,
                        Math.min(card.getMeasuredHeight() + dp(24), maxH));
            } else {
                win.setLayout(WindowManager.LayoutParams.MATCH_PARENT, maxH);
            }
        } catch (Exception e) {
            // 测量失败就保持系统默认高度
        }
    }

    private Button modeBtn(String text, final String m) {
        Button b = new Button(a);
        b.setAllCaps(false);
        b.setTextSize(12);
        b.setSingleLine(true);
        b.setText(text);
        b.setPadding(dp(6), dp(8), dp(6), dp(8));
        b.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                mode = m;
                applyMode();
            }
        });
        return b;
    }

    /** 模式切换：按钮高亮 + 「接管一切」时把名单整块收起（此时名单不生效） */
    private void applyMode() {
        paint(btnAll, "all".equals(mode));
        paint(btnOnly, "only".equals(mode));
        paint(btnExclude, "exclude".equals(mode));
        int vis = "all".equals(mode) ? View.GONE : View.VISIBLE;
        if (blockPick != null) blockPick.setVisibility(vis);
        if (listWrap != null) listWrap.setVisibility(vis);
        syncBoxes();
        if (card != null) card.post(new Runnable() {   // 内容变了，窗口高度跟着重算
            public void run() {
                fitHeight();
            }
        });
    }

    private void paint(Button b, boolean on) {
        if (b == null) return;
        b.setBackground(box(on ? 0xFF2F6FED : 0xFFF1F4FA,
                on ? 0xFF2F6FED : 0xFFD7DEEA, 8));
        b.setTextColor(on ? Color.WHITE : 0xFF2F3F52);
    }

    // ---------------------------------------------------------------- 列表

    private void renderList() {
        listHost.removeAllViews();
        boxes.clear();
        if (apps.isEmpty()) {
            // 列表空着最容易让人以为「功能坏了」，所以把原因和出路都写在界面上
            TextView tv = new TextView(a);
            tv.setText("没读到应用列表。常见原因：Android 11+ 限制了「查看应用列表」，"
                    + "或刚装好还没拿到授权。点下面按钮再读一次。");
            tv.setTextSize(12);
            tv.setTextColor(0xFF8A94A6);
            tv.setPadding(dp(8), dp(8), dp(8), dp(6));
            listHost.addView(tv);
            Button again = new Button(a);
            again.setAllCaps(false);
            again.setText("重新读取应用列表");
            again.setTextSize(13);
            again.setTextColor(0xFF2F6FED);
            again.setBackground(box(0xFFF1F4FA, 0xFFD7DEEA, 8));
            again.setPadding(dp(12), dp(8), dp(12), dp(8));
            again.setContentDescription("重新读取应用列表");
            again.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    apps.clear();
                    apps.addAll(AppList.load(a));
                    renderList();
                    applyFilter();
                    updateCount();
                    Toast.makeText(a, apps.isEmpty()
                            ? "还是没读到（本机可能禁止查询应用列表）"
                            : "已读到 " + apps.size() + " 个应用", Toast.LENGTH_SHORT).show();
                }
            });
            listHost.addView(again, full(0, 0, 0, dp(8)));
            return;
        }
        for (AppList.Row r : apps) {
            CheckBox cb = new CheckBox(a);
            cb.setText(r.name);
            cb.setTextSize(13);
            cb.setTextColor(0xFF2F3F52);
            cb.setSingleLine(true);
            cb.setPadding(dp(8), dp(4), dp(4), dp(4));
            cb.setContentDescription(r.id);
            cb.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
                public void onCheckedChanged(CompoundButton v, boolean checked) {
                    String pkg = String.valueOf(v.getContentDescription());
                    if (checked) sel.add(pkg);
                    else sel.remove(pkg);
                    updateCount();
                }
            });
            listHost.addView(cb);
            boxes.put(r.id, cb);
        }
    }

    /** 把 sel 回填到复选框（只在值变化时触发监听，避免递归） */
    private void syncBoxes() {
        for (Map.Entry<String, CheckBox> e : boxes.entrySet()) {
            boolean want = sel.contains(e.getKey());
            if (e.getValue().isChecked() != want) e.getValue().setChecked(want);
        }
        updateCount();
    }

    private void updateCount() {
        if (tvCount == null) return;
        tvCount.setText("已选 " + sel.size() + " / " + apps.size() + " 个应用");
    }

    private boolean visible(AppList.Row r) {
        String q = edSearch == null ? "" : String.valueOf(edSearch.getText()).trim().toLowerCase();
        if (q.length() == 0) return true;
        return r.name.toLowerCase().contains(q) || r.id.toLowerCase().contains(q);
    }

    private void applyFilter() {
        if (listHost == null) return;
        int n = 0;
        for (Map.Entry<String, CheckBox> e : boxes.entrySet()) {
            AppList.Row r = rowOf(e.getKey());
            boolean v = r == null || visible(r);
            e.getValue().setVisibility(v ? View.VISIBLE : View.GONE);
            if (v) n++;
        }
        if (apps.isEmpty()) return;
        if (n == 0) {
            // 搜索无结果时给个可见提示（列表为空容易被当成「还没加载」）
            if (emptyTip == null) {
                emptyTip = new TextView(a);
                emptyTip.setTextSize(12);
                emptyTip.setTextColor(0xFF8A94A6);
                emptyTip.setPadding(dp(8), dp(8), dp(8), dp(8));
                listHost.addView(emptyTip);
            }
            emptyTip.setText("没有匹配「" + String.valueOf(edSearch.getText()).trim() + "」的应用");
            emptyTip.setVisibility(View.VISIBLE);
        } else if (emptyTip != null) {
            emptyTip.setVisibility(View.GONE);
        }
    }

    private AppList.Row rowOf(String pkg) {
        for (AppList.Row r : apps) {
            if (r.id.equals(pkg)) return r;
        }
        return null;
    }

    // ---------------------------------------------------------------- 保存

    private void saveScope() {
        List<String> list = new ArrayList<String>(sel);
        String m = "all".equals(mode) ? "all" : mode;
        WeakNet.I.setScope(m, "all".equals(m) ? new ArrayList<String>() : list);
        if ("only".equals(m)) {
            WeakNet.I.log("config", "接管范围：仅 " + (list.isEmpty()
                    ? "（空名单=不接管任何 App）" : join(list)));
        } else if ("exclude".equals(m)) {
            WeakNet.I.log("config", "接管范围：排除 " + (list.isEmpty()
                    ? "（空名单=全部接管）" : join(list)));
        } else {
            WeakNet.I.log("config", "接管范围：全部 App");
        }
        // 范围只在 establish() 时生效，改完必须重建通道
        VpnNetService.rebuild(a);
        Toast.makeText(a, "接管范围已保存", Toast.LENGTH_SHORT).show();
        close();
        a.updateStatus();
        a.getHandler().postDelayed(new Runnable() {
            public void run() {
                a.updateStatus();
            }
        }, 1200);
    }

    private String join(List<String> pkgs) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < pkgs.size() && i < 4; i++) {
            if (i > 0) sb.append(", ");
            sb.append(AppList.nameOf(apps, pkgs.get(i)));
        }
        if (pkgs.size() > 4) sb.append("…");
        return sb.toString();
    }

    // ---------------------------------------------------------------- 控件工厂

    private LinearLayout.LayoutParams weight(int marginDp) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        p.setMargins(marginDp == 0 ? 0 : dp(marginDp), 0, 0, 0);
        return p;
    }

    private LinearLayout.LayoutParams full(int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(dp(l), dp(t), dp(r), dp(b));
        return p;
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
