package com.weaknet.app;

import android.app.AlertDialog;
import android.app.Dialog;
import android.content.DialogInterface;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 主界面的「🔀 我的弱网组合」卡片：新建 / 编辑 / 删除 / 选中后按序列播放。
 *
 * 悬浮窗面板（{@link FloatScenePanel}）只负责「切换」，组合本身得有个能创建的地方，
 * 否则手机上永远列不出任何组合 —— 这个卡片就是那个入口。
 *
 * 生效规矩与场景按钮完全一致（见 {@link WeakNet#selectCombo}）：
 *  - 弱网已在测试中：点一下立即从第一段开始播，到点自动切下一段
 *  - 弱网没开启：只把它记成当前项，绝不替用户偷偷开始限速
 */
public class ComboSheet {
    private final MainActivity a;

    private LinearLayout grid;
    private LinearLayout tailRow;
    private final Map<String, Button> btns = new LinkedHashMap<String, Button>();
    private String sig = null;                // 列表签名：没变就不重画按钮
    private ComboNotesSheet notes;            // 「！」打开的组合说明二级界面

    // ---- 编辑器 ----
    private Dialog dlg;
    private String editing = "";              // 正在编辑的组合 id；"" = 新建
    private EditText edName;
    private CheckBox cbLoop;
    private LinearLayout stepBox;
    private Button btnAdd;
    private TextView tvTitle;                // 标题：保存后切成「新建弱网组合」
    private final List<Row> rows = new ArrayList<Row>();

    public ComboSheet(MainActivity a) {
        this.a = a;
    }

    // ------------------------------------------------------------------ 卡片

    /**
     * 组合页内容（不含卡片外壳与标题）。
     *
     * 主界面把「我的自定义」和「我的弱网组合」合成了一张卡片、用标签切换，
     * 标题与「🔗 环境共享」由那张合并卡片统一负责；这里只产出「组合」这一页
     * 自己的内容（说明入口 + 新建按钮 + 列表 + 编辑/删除行）。
     */
    public LinearLayout buildContent() {
        LinearLayout page = new LinearLayout(a);
        page.setOrientation(LinearLayout.VERTICAL);

        // 动作行：只有「＋ 新建组合」。
        // 组合说明的「！」挪进了编辑器（标题右侧）——说明是给「要动手排组合的人」
        // 看的，摆在组合列表顶上反而像跟哪支组合都有关似的
        LinearLayout head = new LinearLayout(a);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);

        Button bNew = new Button(a);
        bNew.setAllCaps(false);
        bNew.setTextSize(12);
        bNew.setText("＋ 新建组合");
        bNew.setTextColor(0xFF1F8A70);
        bNew.setBackground(box(0xFFEAF7F3, 0xFFBFE9DA, 8));
        bNew.setPadding(dp(10), dp(4), dp(10), dp(4));
        bNew.setContentDescription("新建弱网组合");
        bNew.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openEditor(null);
            }
        });
        head.addView(bNew);
        page.addView(head, full(0, 2, 0, 2));

        grid = new LinearLayout(a);
        grid.setOrientation(LinearLayout.VERTICAL);
        page.addView(grid, full(0, 4, 0, 4));

        // 编辑 + 删除：选中了某支组合才出现（与自定义卡片一个路子）
        tailRow = new LinearLayout(a);
        tailRow.setOrientation(LinearLayout.HORIZONTAL);

        Button bEdit = new Button(a);
        bEdit.setAllCaps(false);
        bEdit.setTextSize(13);
        bEdit.setText("✏️ 编辑");
        bEdit.setTextColor(0xFF2F3F52);
        bEdit.setBackground(box(0xFFFFFFFF, 0xFFD7DEEA, 8));
        bEdit.setPadding(dp(12), dp(9), dp(12), dp(9));
        bEdit.setContentDescription("编辑当前组合");
        bEdit.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                String k = WeakNet.I.presetKey;
                if (k != null && k.startsWith(MyCombos.KEY_PREFIX)) {
                    openEditor(k.substring(MyCombos.KEY_PREFIX.length()));
                }
            }
        });
        LinearLayout.LayoutParams elp = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        elp.setMargins(0, 0, dp(6), 0);
        tailRow.addView(bEdit, elp);

        Button bDel = new Button(a);
        bDel.setAllCaps(false);
        bDel.setTextSize(13);
        bDel.setText("🗑 删除");
        bDel.setTextColor(0xFFD93025);
        bDel.setBackground(box(0xFFFFFFFF, 0xFFF3C3C0, 8));
        bDel.setPadding(dp(12), dp(9), dp(12), dp(9));
        bDel.setContentDescription("删除当前组合");
        bDel.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                delCurrent();
            }
        });
        tailRow.addView(bDel, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        tailRow.setVisibility(View.GONE);
        page.addView(tailRow, full(0, 0, 0, 4));

        page.addView(hint("把几支场景排好队、每段给个时长，点一下按顺序自动播放（最多 "
                + ComboPlan.MAX_STEPS + " 段）。选中某条后下面会出现「✏️ 编辑 / 🗑 删除」，"
                + "长按某条可以查看它的段并删除。组合本身是按序列播放的，"
                + "所以段里面不能再选另一支组合。"));
        refresh(true);
        return page;
    }

    /** 列表内容变了才重画按钮；高亮每次都同步 */
    public void refresh() {
        refresh(false);
    }

    private void refresh(boolean force) {
        if (grid == null) return;
        List<MyCombos.Item> items;
        try {
            items = MyCombos.load();
        } catch (Exception e) {
            items = new ArrayList<MyCombos.Item>();
        }
        StringBuilder sb = new StringBuilder();
        for (MyCombos.Item c : items) sb.append(c.id).append(",").append(c.name).append(";");
        String now = sb.toString();
        if (force || !now.equals(sig)) {
            sig = now;
            for (Button b : btns.values()) grid.removeView(b);
            btns.clear();
            for (final MyCombos.Item c : items) {
                Button b = new Button(a);
                b.setAllCaps(false);
                b.setTextSize(13);
                b.setText("🔀 " + c.name);
                b.setTextColor(0xFF2F3F52);
                b.setBackground(box(0xFFFFFFFF, 0xFFD7DEEA, 8));
                b.setPadding(dp(12), dp(9), dp(12), dp(9));
                b.setContentDescription("组合 " + c.name);
                b.setOnClickListener(new View.OnClickListener() {
                    public void onClick(View v) {
                        MyCombos.Item cur = MyCombos.find(c.id);
                        if (cur == null) {
                            Toast.makeText(a, "这条组合已不存在", Toast.LENGTH_SHORT).show();
                            refresh(true);
                            return;
                        }
                        pick(cur);
                    }
                });
                // 长按 = 打开查看/编辑（里面有删除按钮）；单击留给「选中/播放」
                b.setOnLongClickListener(new View.OnLongClickListener() {
                    public boolean onLongClick(View v) {
                        openEditor(c.id);
                        return true;
                    }
                });
                grid.addView(b, full(0, 4, 0, 0));
                btns.put(MyCombos.KEY_PREFIX + c.id, b);
            }
            if (items.isEmpty()) {
                TextView empty = new TextView(a);
                empty.setText("还没有组合，点右上「＋ 新建」排一支");
                empty.setTextSize(12);
                empty.setTextColor(0xFF8A94A6);
                empty.setPadding(dp(2), dp(4), dp(2), dp(2));
                grid.addView(empty);
            }
        }
        // 高亮 + 编辑/删除行
        String key = WeakNet.I.presetKey;
        boolean mine = key != null && key.startsWith(MyCombos.KEY_PREFIX);
        for (Map.Entry<String, Button> e : btns.entrySet()) {
            boolean on = e.getKey().equals(key);
            e.getValue().setBackground(box(on ? 0xFF1F8A70 : 0xFFFFFFFF,
                    on ? 0xFF1F8A70 : 0xFFD7DEEA, 8));
            e.getValue().setTextColor(on ? 0xFFFFFFFF : 0xFF2F3F52);
        }
        if (tailRow != null) tailRow.setVisibility(mine ? View.VISIBLE : View.GONE);
    }

    private void pick(MyCombos.Item c) {
        boolean started = WeakNet.I.selectCombo(c);
        WeakNet.I.log("config", (started ? "播放组合：" : "选择组合：") + c.name);
        Toast.makeText(a,
                started ? ("已开始播放：「" + c.name + "」" + c.steps.size() + " 段，按顺序自动切")
                        : ("已选「" + c.name + "」，点「▶ 开始弱网测试」后按顺序播放"),
                Toast.LENGTH_SHORT).show();
        refresh();
        a.updateStatus();
    }

    private void delCurrent() {
        String key = WeakNet.I.presetKey;
        if (key == null || !key.startsWith(MyCombos.KEY_PREFIX)) return;
        deleteById(key.substring(MyCombos.KEY_PREFIX.length()));
    }

    /**
     * 删掉一支组合，并把所有看到它的地方都刷新一遍。
     *
     * 入口有两处：卡片底部选中后的「🗑 删除」，以及查看/编辑窗口里的删除按钮 ——
     * 同一套删除逻辑，避免两处行为不一致（一个停播放另一个不停之类的）。
     */
    private void deleteById(final String id) {
        final MyCombos.Item it = MyCombos.find(id);
        if (it == null) {
            refresh(true);
            return;
        }
        // 删之前先问一句：组合是排好队的活儿，误删要重新排
        new AlertDialog.Builder(a)
                .setTitle("删除弱网组合")
                .setMessage("确定删除「" + it.name + "」？此操作不可撤销。")
                .setNegativeButton("取消", null)
                .setPositiveButton("删除", new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int w) {
                        doDelete(it);
                    }
                })
                .show();
    }

    private void doDelete(MyCombos.Item it) {
        MyCombos.delete(it.id);
        WeakNet.I.stopCombo();            // 删的正是正在播的那支时立刻停
        WeakNet.I.setProfile(new Profile(), "direct");
        WeakNet.I.limitOn = false;
        WeakNet.I.log("config", "删除弱网组合：" + it.name);
        Toast.makeText(a, "已删除「" + it.name + "」", Toast.LENGTH_SHORT).show();
        // 删掉的正是编辑窗里开着的那支 → 关掉它，别让用户对着一支不存在的组合继续改
        try {
            if (dlg != null && it.id.equals(editing)) dlg.dismiss();
        } catch (Exception e) {
            // ignore
        }
        refresh(true);                    // 卡片列表
        a.updateStatus();                 // 主界面 / 共享面板 / 飘窗标题都跟着刷新
    }

    // ------------------------------------------------------------------ 编辑器

    // ------------------------------------------------------------------ 组合说明

    /** 「！」：打开组合说明二级界面（机制 + 我保存的组合 + 推荐例子）
     *
     * 入口在组合编辑器的标题右侧。
     *
     * 浮窗的收起/恢复由 ComboNotesSheet 自己管（它知道是不是自己收起的）：
     * 从编辑器里打开时编辑器已经收起过飘窗了，这时若再恢复，飘窗就会跑回来
     * 盖住还开着的编辑器 —— 所以这里不能再无条件 restoreFloat。
     */
    void openNotes() {
        if (notes == null) notes = new ComboNotesSheet(a);
        if (notes.isShowing()) {
            notes.close();
            return;
        }
        notes.show();
    }

    /** 关掉组合说明页（编辑器重建 / 关闭时调用，免得留个孤儿页浮在上面） */
    private void closeNotes() {
        if (notes != null) notes.close();
    }

    /** 关掉编辑器：标题栏的 ✕ 走这条路径。 */
    private void dismissEditor() {
        try {
            if (dlg != null) dlg.dismiss();
        } catch (Exception e) {
            // ignore
        }
    }

    void openEditor(String id) {
        // 编辑器已经开着时再点一次：
        //   - 还是新建模式 → 复用这扇窗，把界面上填了一半的内容清掉。
        //     用户以为在排新组合，实际上一支没填完的还留在那儿，一不留神就存成两支。
        //   - 要看别的组合 → 得重建（界面上还是上一支的内容）。
        // 不管哪种，都不能叠出第二扇窗。
        if (dlg != null && dlg.isShowing()) {
            boolean wantNew = (id == null);
            if (wantNew && editing.length() == 0) {
                resetToNew();
                try {
                    dlg.show();
                } catch (Exception e) {
                    // ignore
                }
                return;
            }
            // 换看别的组合 → 旧编辑器要重建。说明页是从旧编辑器里点开的，
            // 不关掉的话它会孤零零浮在新编辑器上面（还压着飘窗的恢复时机）
            closeNotes();
            try {
                dlg.dismiss();
            } catch (Exception e) {
                // ignore
            }
            dlg = null;
        }
        editing = id == null ? "" : id;
        rows.clear();
        a.suspendFloat();          // 飘窗会盖住弹窗并吃掉点击
        dlg = new Dialog(a);
        dlg.requestWindowFeature(Window.FEATURE_NO_TITLE);
        dlg.setCancelable(true);
        dlg.setCanceledOnTouchOutside(true);
        dlg.setOnDismissListener(new DialogInterface.OnDismissListener() {
            public void onDismiss(DialogInterface d) {
                closeNotes();               // 说明页是从编辑器里点开的，别留孤儿页
                a.restoreFloat();
                refresh(true);
                a.updateStatus();
            }
        });

        MyCombos.Item cur = id == null ? null : MyCombos.find(id);

        ScrollView sv = new ScrollView(a);
        LinearLayout card = new LinearLayout(a);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(box(0xFFFFFFFF, 0xFFDDE3EC, 14));
        card.setPadding(dp(14), dp(12), dp(14), dp(14));

        // 标题行：左边写清现在是新建还是编辑，右边挂「组合说明」入口。
        // 说明入口跟着编辑器走：组合怎么播、有哪些例子，只有真正动手排组合的
        // 时候才需要知道，摆在组合列表顶上反倒像跟每一支组合都有关
        LinearLayout thead = new LinearLayout(a);
        thead.setOrientation(LinearLayout.HORIZONTAL);
        thead.setGravity(Gravity.CENTER_VERTICAL);

        TextView t = new TextView(a);
        t.setText(cur == null ? "新建弱网组合" : "编辑弱网组合");
        t.setTextSize(15);
        t.setTextColor(0xFF1F2430);
        t.setTypeface(null, Typeface.BOLD);
        thead.addView(t, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        tvTitle = t;

        TextView bHelp = new TextView(a);
        bHelp.setText("！");
        bHelp.setTextSize(15);
        bHelp.setTextColor(0xFF8A94A6);
        bHelp.setGravity(Gravity.CENTER);
        bHelp.setPadding(dp(8), dp(2), dp(2), dp(2));
        bHelp.setContentDescription("弱网组合说明");
        bHelp.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openNotes();
            }
        });
        thead.addView(bHelp);

        // 标题行右侧的 ✕：底部原本挂着一个「取消」，两者干的是同一件事
        // （都是不保存直接关）。两个入口摆在一起只会让人犹豫该点哪个，
        // 所以把关闭统一收到 ✕ 上
        TextView close = new TextView(a);
        close.setText("✕");
        close.setTextSize(17);
        close.setTextColor(0xFF8A94A6);
        close.setPadding(dp(10), dp(2), dp(4), dp(2));
        close.setContentDescription("关闭组合编辑器");
        close.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                dismissEditor();
            }
        });
        thead.addView(close);

        card.addView(thead, full(0, 0, 0, 8));

        edName = new EditText(a);
        edName.setHint("组合名称");
        edName.setSingleLine(true);
        edName.setTextSize(14);
        edName.setText(cur == null ? "" : cur.name);
        card.addView(edName, full(0, 0, 0, 8));

        cbLoop = new CheckBox(a);
        cbLoop.setText("播完后循环（不勾则停在最后一段）");
        cbLoop.setTextSize(13);
        cbLoop.setChecked(cur != null && cur.loop);
        card.addView(cbLoop, full(0, 0, 0, 8));

        TextView sub = new TextView(a);
        sub.setText("按顺序播放（每段都要填时间）");
        sub.setTextSize(13);
        sub.setTextColor(0xFF2F3F52);
        sub.setTypeface(null, Typeface.BOLD);
        card.addView(sub, full(0, 0, 0, 4));

        stepBox = new LinearLayout(a);
        stepBox.setOrientation(LinearLayout.VERTICAL);
        card.addView(stepBox, full(0, 0, 0, 4));

        List<MyCombos.Step> seed;
        if (cur != null && !cur.steps.isEmpty()) {
            seed = cur.steps;
        } else {
            seed = new ArrayList<MyCombos.Step>();
            MyCombos.Step s0 = new MyCombos.Step();
            s0.key = "5g";
            s0.minutes = 0;
            s0.seconds = 30;
            seed.add(s0);
        }
        for (MyCombos.Step s : seed) addStepRow(s);

        btnAdd = new Button(a);
        btnAdd.setAllCaps(false);
        btnAdd.setTextSize(13);
        btnAdd.setTextColor(0xFF1F8A70);
        btnAdd.setBackground(box(0xFFEAF7F3, 0xFFBFE9DA, 8));
        btnAdd.setPadding(dp(12), dp(8), dp(12), dp(8));
        btnAdd.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                addStepRow(null);
            }
        });
        card.addView(btnAdd, full(0, 0, 0, 8));
        syncAddRow();

        LinearLayout bar = new LinearLayout(a);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        // 查看已有组合时，删除入口就在这扇窗里：不用先切回卡片再找删除
        if (cur != null) {
            Button del = new Button(a);
            del.setAllCaps(false);
            del.setTextSize(14);
            del.setText("🗑 删除");
            del.setTextColor(0xFFD93025);
            del.setBackground(box(0xFFFFFFFF, 0xFFF3C3C0, 8));
            del.setPadding(dp(16), dp(9), dp(16), dp(9));
            del.setContentDescription("删除这个弱网组合");
            del.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    final String id = editing;
                    deleteById(id);
                }
            });
            bar.addView(del, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        }
        Button save = new Button(a);
        save.setAllCaps(false);
        save.setTextSize(14);
        save.setText("保存组合");
        save.setTextColor(0xFFFFFFFF);
        save.setBackground(box(0xFF1F8A70, 0xFF1F8A70, 8));
        save.setPadding(dp(16), dp(9), dp(16), dp(9));
        save.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                doSave();
            }
        });
        bar.addView(save, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        card.addView(bar, full(0, 4, 0, 0));

        TextView tip = new TextView(a);
        tip.setText("每段的「分」「秒」至少填一个且大于 0，到点自动切下一段；"
                + "最多 " + ComboPlan.MAX_STEPS + " 段，且不能选别的组合。"
                + "保存后可以接着排下一支，不用关窗。");
        tip.setTextSize(11);
        tip.setTextColor(0xFF8A94A6);
        card.addView(tip, full(0, 8, 0, 0));

        sv.addView(card);
        dlg.setContentView(sv);
        dlg.show();
    }

    private static final class Row {
        View view;
        Spinner sp;
        List<String> keys;
        EditText em;
        EditText es;
    }

    /** 编辑器里的一行：场景 + 分 + 秒 + 删除本行 */
    private void addStepRow(MyCombos.Step seed) {
        // 硬上限写在这里而不是只靠按钮禁用：绕过按钮的路径（编辑旧组合、手抖多按一次）
        // 排到第 6 段后会被 norm 悄悄砍掉，用户根本不知道自己排的那段没了
        if (rows.size() >= ComboPlan.MAX_STEPS) return;

        final Row r = new Row();
        r.keys = new ArrayList<String>();
        List<String> labels = new ArrayList<String>();
        for (String k : Profile.KEYS) {
            // 组合不参与组合：选它进去运行时只会把那一段跳过
            if (k.startsWith(MyCombos.KEY_PREFIX)) continue;
            String s = Profile.shortOf(k);
            labels.add(s != null && s.length() > 0 ? s : k);
            r.keys.add(k);
        }
        List<MyPresets.Item> mine;
        try {
            mine = MyPresets.load();
        } catch (Exception e) {
            mine = new ArrayList<MyPresets.Item>();
        }
        for (MyPresets.Item it : mine) {
            labels.add(it.name);
            r.keys.add("u:" + it.id);
        }

        LinearLayout row = new LinearLayout(a);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);

        r.sp = new Spinner(a);
        ArrayAdapter<String> ad = new ArrayAdapter<String>(a,
                android.R.layout.simple_spinner_dropdown_item, labels);
        r.sp.setAdapter(ad);
        int idx = 0;
        if (seed != null && seed.key != null) {
            int found = r.keys.indexOf(seed.key);
            if (found >= 0) idx = found;
        }
        r.sp.setSelection(idx);
        row.addView(r.sp, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        r.em = new EditText(a);
        r.em.setInputType(InputType.TYPE_CLASS_NUMBER);
        r.em.setText(seed == null ? "0" : String.valueOf(seed.minutes));
        r.em.setTextSize(13);
        row.addView(r.em, new LinearLayout.LayoutParams(dp(48), ViewGroup.LayoutParams.WRAP_CONTENT));
        row.addView(unitLabel("分"));

        r.es = new EditText(a);
        r.es.setInputType(InputType.TYPE_CLASS_NUMBER);
        r.es.setText(seed == null ? "30" : String.valueOf(seed.seconds));
        r.es.setTextSize(13);
        row.addView(r.es, new LinearLayout.LayoutParams(dp(48), ViewGroup.LayoutParams.WRAP_CONTENT));
        row.addView(unitLabel("秒"));

        Button del = new Button(a);
        del.setAllCaps(false);
        del.setText("✕");
        del.setTextSize(13);
        del.setTextColor(0xFF8A94A6);
        del.setBackground(box(0x00000000, 0x00000000, 6));
        del.setPadding(dp(8), dp(4), dp(8), dp(4));
        del.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                try {
                    stepBox.removeView(r.view);
                } catch (Exception e) {
                    // ignore
                }
                rows.remove(r);
                syncAddRow();
            }
        });
        row.addView(del);

        r.view = row;
        rows.add(r);
        stepBox.addView(row, full(0, 0, 0, 4));
        syncAddRow();
    }

    private TextView unitLabel(String s) {
        TextView t = new TextView(a);
        t.setText(s);
        t.setTextSize(12);
        t.setTextColor(0xFF8A94A6);
        t.setPadding(dp(4), 0, dp(6), 0);
        return t;
    }

    private void syncAddRow() {
        if (btnAdd == null) return;
        boolean full2 = rows.size() >= ComboPlan.MAX_STEPS;
        btnAdd.setEnabled(!full2);
        btnAdd.setText(full2 ? "已达上限（" + ComboPlan.MAX_STEPS + " 段）" : "＋ 添加一段");
    }

    private void doSave() {
        String name = edName.getText() == null ? "" : edName.getText().toString().trim();
        if (name.length() == 0) {
            Toast.makeText(a, "请先给这个组合起个名字。", Toast.LENGTH_SHORT).show();
            return;
        }
        List<MyCombos.Step> steps = new ArrayList<MyCombos.Step>();
        for (int i = 0; i < rows.size(); i++) {
            Row r = rows.get(i);
            int idx = r.sp.getSelectedItemPosition();
            if (idx < 0 || idx >= r.keys.size()) {
                Toast.makeText(a, "第 " + (i + 1) + " 段还没选场景。", Toast.LENGTH_SHORT).show();
                return;
            }
            MyCombos.Step s = new MyCombos.Step();
            s.key = r.keys.get(idx);
            s.minutes = intOf(r.em);
            s.seconds = intOf(r.es);
            if (s.minutes <= 0 && s.seconds <= 0) {
                Toast.makeText(a, "第 " + (i + 1) + " 段没填时间：分和秒至少要有一个大于 0。",
                        Toast.LENGTH_SHORT).show();
                return;
            }
            steps.add(s);
        }
        if (steps.isEmpty()) {
            Toast.makeText(a, "至少要排一段场景。", Toast.LENGTH_SHORT).show();
            return;
        }
        MyCombos.Item it = MyCombos.save(editing.length() == 0 ? null : editing,
                name, cbLoop.isChecked(), steps);
        if (it == null) {
            // 一段都留不下（全是嵌套组合之类的脏数据）：不存空壳，原来的组合也不动
            Toast.makeText(a, "这个组合里没有一段能用的场景，没有保存。\n\n"
                    + "组合里不能再套组合，请改选一个预设或自定义场景。",
                    Toast.LENGTH_LONG).show();
            return;
        }
        WeakNet.I.log("config", "保存弱网组合：" + it.name);
        Toast.makeText(a, "已保存「" + it.name + "」，可以接着排下一支。",
                Toast.LENGTH_SHORT).show();
        // 保存完不关窗：直接切成「新建」模式，接着排下一支（连着建几支时最省事）
        resetToNew();
    }

    /** 把编辑器清成「新建」状态：名字、循环开关、段列表都回到初始值 */
    private void resetToNew() {
        editing = "";
        try {
            edName.setText("");
            cbLoop.setChecked(false);
        } catch (Exception e) {
            // ignore
        }
        for (Row r : rows) {
            try {
                stepBox.removeView(r.view);
            } catch (Exception e) {
                // ignore
            }
        }
        rows.clear();
        MyCombos.Step s0 = new MyCombos.Step();
        s0.key = "5g";
        s0.minutes = 0;
        s0.seconds = 30;
        addStepRow(s0);
        try {
            tvTitle.setText("新建弱网组合");
        } catch (Exception e) {
            // ignore
        }
    }

    private int intOf(EditText e) {
        try {
            String s = e.getText() == null ? "" : e.getText().toString().trim();
            if (s.length() == 0) return 0;
            return Integer.parseInt(s);
        } catch (Exception ex) {
            return 0;
        }
    }

    // ------------------------------------------------------------------ 小工具

    private LinearLayout.LayoutParams full(int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(dp(l), dp(t), dp(r), dp(b));
        return p;
    }

    private TextView hint(String text) {
        TextView t = new TextView(a);
        t.setText(text);
        t.setTextSize(11);
        t.setTextColor(0xFF8A94A6);
        t.setPadding(dp(2), dp(2), dp(2), 0);
        return t;
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
