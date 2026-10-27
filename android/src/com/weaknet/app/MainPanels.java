package com.weaknet.app;

import android.app.Dialog;
import android.content.DialogInterface;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 主界面的三块内容：测试环境 / ⭐ 我的场景与组合 / 事件日志。
 *
 * 这三块原本散落在设置浮层里（桌面版是网页控制台），现在直接铺在主界面上：
 * 选择弱网场景是最高频的操作，为了点它而多开一层浮层纯属多余。
 * 布局是「顶栏固定 + 下面整体滚动」（见 {@link MainActivity}），这里只负责往
 * 容器里塞卡片，不承担窗口标题那类职责。
 *
 * 「我的自定义场景」与「我的弱网组合」合成同一张卡片，用顶部标签切换
 * （见 {@link #mineComboCard()}）——两块都是自己攒下来的东西，分成两张卡片
 * 会把主界面拉得很长。
 *
 * 刷新策略：MainActivity 每 3 秒的状态循环会调 {@link #sync()}——
 *   · 场景按钮：只有「内置+自定义」的 key 集合变了才重建网格，否则只更新高亮，
 *     免得每隔几秒把整片按钮推翻重画（用户在滚动时会被抖走）；
 *   · 事件日志：比对条数与最后一条时间戳，没变化就不碰 TextView。
 */
public class MainPanels {
    /** 场景按钮一行几个：手机竖屏窄，3 列刚好放得下短名 */
    private static final int COLS = 3;
    /** 日志最多显示多少条（引擎侧保留 300 条） */
    private static final int LOG_MAX = 60;

    private final MainActivity a;

    private LinearLayout builtinGrid;
    private LinearLayout mineGrid;
    /** 选中内置档位时才出现的那一行：查看 / 修改该档位的参数 */
    private LinearLayout builtinRow;
    private Button btnPeek;
    /** ➕ 新建：文案与行为恒定（选中自定义场景时也不变成编辑，否则就没法新建第二个了） */
    private Button btnNew;
    private Button btnShare;
    /** 选中自定义场景时才出现的那一行：编辑 + 删除 */
    private LinearLayout tailRow;
    private Button btnEdit;
    private Button btnDel;
    private TextView logText;

    /** key -> 按钮：内置预设 key 或 u:<id> */
    private final Map<String, Button> builtinBtns = new LinkedHashMap<String, Button>();
    private final Map<String, Button> mineBtns = new LinkedHashMap<String, Button>();
    /** 编辑场景时挂着的 id（新建为空） */
    private final java.util.concurrent.atomic.AtomicReference<String> editing =
            new java.util.concurrent.atomic.AtomicReference<String>("");
    private final Map<String, EditText> editorFields = new LinkedHashMap<String, EditText>();
    private EditText edName;
    private Dialog editorDlg;

    private ShareSheet share;
    private NotesSheet notes;
    private ParamSheet param;
    private ComboSheet comboSheet;
    // 「场景 / 组合」标签：两张页合并成一张卡片后，切换只换显隐
    private Button tabScene;
    private Button tabCombo;
    private LinearLayout scenePage;
    private LinearLayout comboPage;
    private String curTab = "scene";
    /** 上一次同步时的选中项：只有它变了才自动翻标签页 */
    private String lastTabKey = null;
    private String sceneSig = null;
    private String logSig = "";

    MainPanels(MainActivity a) {
        this.a = a;
    }

    // ---------------------------------------------------------------- 生命周期

    /** 构建三张卡片，挂到主界面的滚动容器上 */
    void build(LinearLayout host) {
        host.addView(builtinCard(), cardLp());
        // 「我的自定义场景」与「我的弱网组合」合成一张卡片，顶部标签切换
        host.addView(mineComboCard(), cardLp());
        host.addView(logCard(), cardLp());
        sync();
    }

    /** 关窗口：共享浮层要在 Activity 销毁前收起 */
    void close() {
        if (share != null) share.close();
        if (notes != null) notes.close();
        if (param != null) param.close();
        if (editorDlg != null) {
            try {
                editorDlg.dismiss();
            } catch (Exception e) {
                // ignore
            }
            editorDlg = null;
        }
    }

    boolean shareShowing() {
        return share != null && share.isShowing();
    }

    void closeShare() {
        if (share != null) share.close();
    }

    /**
     * 状态循环回调：同步按钮高亮、删除/编辑按钮的显隐、共享待处理条数与日志。
     * 每隔几秒被调用一次，所以里面刻意做了「没变化就不重画」的判断。
     */
    void sync() {
        reloadScenesIfChanged();
        syncHighlight();
        syncSceneBtns();
        // 组合可能在别处被改到（飘窗面板选了别的、接口新建了组合），这里跟上
        if (comboSheet != null) comboSheet.refresh();
        // 标签跟着选中项走：组合与自定义场景合成一张卡片后，
        // 选中的东西不在当前这一页时用户会以为「没生效」——自动翻到对应的那页
        syncTabToSelection();
        // 共享面板开着时也立刻重画：删了组合 / 来了新推送，别等那 3 秒的自动刷新
        if (share != null) share.reloadNow();
        int n = ShareNet.inboxCount();
        if (btnShare != null) {
            btnShare.setText(n > 0 ? "🔗 环境共享（" + n + " 条待处理）" : "🔗 环境共享");
        }
        renderLog();
    }

    // ---------------------------------------------------------------- 测试环境

    private LinearLayout builtinCard() {
        LinearLayout card = cardShell();

        // 标题行：「！」贴在最右。17 支档位光看名字分不清差别（「4G 弱网」vs「弱 Wi-Fi」、
        // 「黑洞」vs「完全断网」），点开是每支的场景说明 + 参数摘要。
        // 单独占一行按钮会白占高度，跟标题同行最省地方。
        LinearLayout head = new LinearLayout(a);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        head.addView(title("🎯 测试环境（点一下即生效）"),
                new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        Button btnHelp = new Button(a);
        btnHelp.setAllCaps(false);
        btnHelp.setTextSize(13);
        btnHelp.setText("！");
        btnHelp.setTextColor(0xFF2F6FED);
        btnHelp.setBackground(box(0xFFF1F4FA, 0xFFD7DEEA, 8));
        btnHelp.setPadding(dp(12), dp(1), dp(12), dp(1));
        // Button 默认带 48dp 的最小尺寸，不压掉会撑成一个大方块，把标题挤走
        btnHelp.setMinWidth(0);
        btnHelp.setMinimumWidth(0);
        btnHelp.setMinHeight(0);
        btnHelp.setMinimumHeight(0);
        btnHelp.setContentDescription("查看所有测试环境的说明");
        btnHelp.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openNotes();
            }
        });
        head.addView(btnHelp, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        card.addView(head);

        builtinGrid = new LinearLayout(a);
        builtinGrid.setOrientation(LinearLayout.VERTICAL);
        card.addView(builtinGrid, full(0, 0, 0, 2));

        // 查看 / 修改参数：内置档位是写死在代码里的，改不动也存不住，
        // 所以这里打开的是「以该档位为初值」的表单，改完另存一条到「我的自定义」。
        builtinRow = new LinearLayout(a);
        builtinRow.setOrientation(LinearLayout.HORIZONTAL);
        btnPeek = new Button(a);
        btnPeek.setAllCaps(false);
        btnPeek.setTextSize(13);
        btnPeek.setSingleLine(true);
        btnPeek.setText("👀 查看 / 修改参数");
        btnPeek.setTextColor(0xFF2F3F52);
        btnPeek.setBackground(box(0xFFFFFFFF, 0xFFD7DEEA, 8));
        btnPeek.setPadding(dp(12), dp(9), dp(12), dp(9));
        btnPeek.setContentDescription("查看或修改当前测试环境的参数");
        btnPeek.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                peekBuiltin();
            }
        });
        builtinRow.addView(btnPeek, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        builtinRow.setVisibility(View.GONE);
        card.addView(builtinRow, full(0, dp(6), 0, 2));

        card.addView(hint("点一下只是「选中」（选中的会变蓝），不会开始弱网——"
                + "要真正生效，点上面的「▶ 开始弱网测试」。"
                + "想改某支内置档位的参数，选中后点上面的「👀 查看 / 修改参数」"
                + "（改完会另存一条到「我的自定义」）。"));
        return card;
    }

    // ---------------------------------------------------------------- 我的场景与组合

    /**
     * 「我的自定义场景」与「我的弱网组合」合并成一张卡片，顶部用标签切换。
     *
     * 这两块本来就是一类东西（都是我自己攒下来的、点一下即切换的环境），
     * 原来却各占一张卡片，主界面向下拖很长才够得着下面那块；
     * 合并后用「场景 / 组合」两个标签切换，一次只显示当前那一页。
     */
    private LinearLayout mineComboCard() {
        LinearLayout card = cardShell();

        // 标题行：名字 + 环境共享（自定义场景与弱网组合在同一个窗口里共享）
        LinearLayout head = new LinearLayout(a);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        head.addView(title("⭐ 我的场景与组合"),
                new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        btnShare = new Button(a);
        btnShare.setAllCaps(false);
        btnShare.setTextSize(12);
        btnShare.setText("🔗 环境共享");
        btnShare.setTextColor(0xFF7A4FF3);
        btnShare.setBackground(box(0xFFF3EFFF, 0xFFD9CBFB, 8));
        btnShare.setPadding(dp(10), dp(4), dp(10), dp(4));
        btnShare.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openShare();
            }
        });
        head.addView(btnShare);
        card.addView(head);

        // 标签行：场景 / 组合。做成两段按钮，选中的那段落成主色，一眼能看出在看哪边
        LinearLayout tabs = new LinearLayout(a);
        tabs.setOrientation(LinearLayout.HORIZONTAL);
        tabScene = tabBtn("场景");
        tabCombo = tabBtn("组合");
        tabScene.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                switchTab("scene");
            }
        });
        tabCombo.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                switchTab("combo");
            }
        });
        LinearLayout.LayoutParams tlp = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        tlp.setMargins(0, 0, dp(6), 0);
        tabs.addView(tabScene, tlp);
        tabs.addView(tabCombo, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        card.addView(tabs, full(0, dp(8), 0, dp(4)));

        // 两个页面：同一时刻只显示其中一个
        scenePage = new LinearLayout(a);
        scenePage.setOrientation(LinearLayout.VERTICAL);
        comboPage = new LinearLayout(a);
        comboPage.setOrientation(LinearLayout.VERTICAL);
        card.addView(scenePage);
        card.addView(comboPage);

        buildScenePage(scenePage);
        comboSheet = new ComboSheet(a);
        comboPage.addView(comboSheet.buildContent(), full(0, 0, 0, 0));

        // 初始停在当前选中的那一页：选中的正是组合，就别让用户再点一次标签
        String key = WeakNet.I.presetKey;
        switchTab(key != null && key.startsWith(MyCombos.KEY_PREFIX) ? "combo" : "scene");
        return card;
    }

    /** 标签按钮：默认灰底，切换时由 syncTabStyle 染色 */
    private Button tabBtn(String text) {
        Button b = new Button(a);
        b.setAllCaps(false);
        b.setText(text);
        b.setTextSize(13);
        b.setTypeface(null, Typeface.BOLD);
        b.setPadding(dp(8), dp(4), dp(8), dp(4));
        b.setMinWidth(0);
        b.setMinimumWidth(0);
        b.setMinHeight(0);
        b.setMinimumHeight(0);
        b.setContentDescription(text + "标签");
        return b;
    }

    /** 切换「场景 / 组合」标签页：只换显示，不重建内容 */
    private void switchTab(String key) {
        boolean scene = "scene".equals(key);
        if (scenePage != null) {
            scenePage.setVisibility(scene ? View.VISIBLE : View.GONE);
        }
        if (comboPage != null) {
            comboPage.setVisibility(scene ? View.GONE : View.VISIBLE);
        }
        syncTabStyle();
        curTab = key;
    }

    /** 当前标签的配色：选中方染主色，另一方灰底 */
    private void syncTabStyle() {
        boolean scene = "scene".equals(curTab);
        if (tabScene != null) {
            tabScene.setTextColor(scene ? 0xFFFFFFFF : 0xFF2F3F52);
            tabScene.setBackground(box(scene ? 0xFF7A4FF3 : 0xFFFFFFFF,
                    scene ? 0xFF7A4FF3 : 0xFFD7DEEA, 8));
        }
        if (tabCombo != null) {
            tabCombo.setTextColor(scene ? 0xFF2F3F52 : 0xFFFFFFFF);
            tabCombo.setBackground(box(scene ? 0xFFFFFFFF : 0xFF1F8A70,
                    scene ? 0xFFD7DEEA : 0xFF1F8A70, 8));
        }
    }

    /**
     * 选中项变了就翻到对应的那一页。
     *
     * 只在「选中项真的变了」时才翻页，否则每 3 秒的同步会把用户手动切过去的
     * 标签又拽回来，看着像标签点不动。
     */
    private void syncTabToSelection() {
        String key = WeakNet.I.presetKey;
        String k = key == null ? "" : key;
        if (k.equals(lastTabKey)) return;
        lastTabKey = k;
        if (k.startsWith(MyCombos.KEY_PREFIX)) {
            switchTab("combo");
        } else if (k.startsWith("u:")) {
            switchTab("scene");
        }
    }

    /** 「场景」页的内容：新建按钮 + 场景列表 + 编辑/删除行 + 提示 */
    private void buildScenePage(LinearLayout page) {
        LinearLayout row = new LinearLayout(a);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);

        btnNew = new Button(a);
        btnNew.setAllCaps(false);
        btnNew.setTextSize(12);
        btnNew.setText("➕ 新建场景");
        btnNew.setTextColor(0xFF7A4FF3);
        btnNew.setBackground(box(0xFFF3EFFF, 0xFFD9CBFB, 8));
        btnNew.setPadding(dp(10), dp(4), dp(10), dp(4));
        btnNew.setContentDescription("新建自定义场景");
        btnNew.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openEditor(null);       // 永远是新建：想改已有的场景用下面的「✏️ 编辑」
            }
        });
        row.addView(btnNew);
        page.addView(row, full(0, 0, 0, 2));

        mineGrid = new LinearLayout(a);
        mineGrid.setOrientation(LinearLayout.VERTICAL);
        page.addView(mineGrid, full(0, 4, 0, 4));

        // 编辑 + 删除：只有选中自定义场景时才出现，两个并排各占一半宽度
        tailRow = new LinearLayout(a);
        tailRow.setOrientation(LinearLayout.HORIZONTAL);

        btnEdit = new Button(a);
        btnEdit.setAllCaps(false);
        btnEdit.setTextSize(13);
        btnEdit.setText("✏️ 编辑");
        btnEdit.setTextColor(0xFF2F3F52);
        btnEdit.setBackground(box(0xFFFFFFFF, 0xFFD7DEEA, 8));
        btnEdit.setPadding(dp(12), dp(9), dp(12), dp(9));
        btnEdit.setContentDescription("编辑当前场景");
        btnEdit.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                String key = WeakNet.I.presetKey;
                if (key != null && key.startsWith("u:")) openEditor(key.substring(2));
            }
        });
        LinearLayout.LayoutParams elp = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        elp.setMargins(0, 0, dp(6), 0);
        tailRow.addView(btnEdit, elp);

        btnDel = new Button(a);
        btnDel.setAllCaps(false);
        btnDel.setTextSize(13);
        btnDel.setText("🗑 删除");
        btnDel.setTextColor(0xFFD93025);
        btnDel.setBackground(box(0xFFFFFFFF, 0xFFF3C3C0, 8));
        btnDel.setPadding(dp(12), dp(9), dp(12), dp(9));
        btnDel.setContentDescription("删除当前场景");
        btnDel.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                delCurrent();
            }
        });
        tailRow.addView(btnDel, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        tailRow.setVisibility(View.GONE);
        page.addView(tailRow, full(0, 0, 0, 4));

        page.addView(hint("自定义场景可保存 13 项参数；「➕ 新建场景」随时可以再加一条，"
                + "选中某条后下面会出现「✏️ 编辑 / 🗑 删除」。也能通过「🔗 环境共享」"
                + "与同一局域网里的电脑/其他手机互推。"));
    }

    /**
     * 场景相关按钮跟随当前生效场景：
     *   · 选中内置档位 → 测试环境卡片出现「👀 查看 / 修改参数」；
     *   · 选中自定义场景 → 自定义卡片出现「编辑 + 删除」这一行。
     * 「➕ 新建」在标题行，文案和行为都不随选中场景变化——否则选中一条自定义之后
     * 按钮变成「编辑」，就再也建不了第二条了。
     */
    private void syncSceneBtns() {
        String key = WeakNet.I.presetKey;
        boolean mine = key != null && key.startsWith("u:");
        boolean builtin = isBuiltinKey(key);
        if (tailRow != null) tailRow.setVisibility(mine ? View.VISIBLE : View.GONE);
        if (builtinRow != null) builtinRow.setVisibility(builtin ? View.VISIBLE : View.GONE);
        if (btnPeek != null && builtin) {
            String s = Profile.shortOf(key);
            // 带上场景名才知道在看哪一支；名字长时尾部省略，别把按钮撑成两行
            btnPeek.setText("👀 参数：" + (s.length() > 0 ? s : key));
        }
        if (btnNew != null) btnNew.setText("➕ 新建");
    }

    /** 是不是有参数可看的内置档位（直连就是不限速，没什么可改的，排除掉） */
    private static boolean isBuiltinKey(String key) {
        if (key == null || key.startsWith("u:") || "direct".equals(key)) return false;
        for (String k : Profile.KEYS) {
            if (k.equals(key)) return true;
        }
        return false;
    }

    /**
     * 打开内置档位的参数表单：以该档位当前的参数为初值。
     * 内置档位本身改不了（写死在代码里），所以保存时是另存一条自定义场景并应用。
     */
    private void peekBuiltin() {
        String key = WeakNet.I.presetKey;
        if (!isBuiltinKey(key)) {
            Toast.makeText(a, "先选中一支内置测试环境", Toast.LENGTH_SHORT).show();
            return;
        }
        Profile p = profileOf(key);
        if (p == null) return;
        String s = Profile.shortOf(key);
        openEditor(null, p, s.length() > 0 ? s : p.name);
    }

    // ---------------------------------------------------------------- 事件日志

    private LinearLayout logCard() {
        LinearLayout card = cardShell();

        LinearLayout head = new LinearLayout(a);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView t = new TextView(a);
        t.setText("📋 事件日志");
        t.setTextSize(14);
        t.setTextColor(0xFF1F2430);
        t.setTypeface(null, Typeface.BOLD);
        head.addView(t, new LinearLayout.LayoutParams(0, dp(30), 1f));
        Button btnClear = new Button(a);
        btnClear.setAllCaps(false);
        btnClear.setTextSize(12);
        btnClear.setText("清空");
        btnClear.setTextColor(0xFF5A6472);
        btnClear.setBackground(box(0xFFFFFFFF, 0xFFD7DEEA, 8));
        btnClear.setPadding(dp(12), dp(4), dp(12), dp(4));
        btnClear.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                synchronized (WeakNet.I.events) {
                    WeakNet.I.events.clear();
                }
                logSig = "";
                renderLog();
                Toast.makeText(a, "已清空日志", Toast.LENGTH_SHORT).show();
            }
        });
        head.addView(btnClear);
        card.addView(head);

        // 固定高度的独立滚动区：外层是整页 ScrollView，这里必须自己能滚，
        // 并且触摸时禁止外层拦截，否则手指一动就把整页带走了。
        final ScrollView sv = new ScrollView(a);
        sv.setBackground(box(0xFFFFFFFF, 0xFFE3E8EF, 8));
        logText = new TextView(a);
        logText.setTextSize(11);
        logText.setTypeface(Typeface.MONOSPACE);
        logText.setTextColor(0xFF4A5260);
        logText.setPadding(dp(8), dp(8), dp(8), dp(8));
        sv.addView(logText);
        sv.setOnTouchListener(new View.OnTouchListener() {
            public boolean onTouch(View v, MotionEvent ev) {
                v.getParent().requestDisallowInterceptTouchEvent(true);
                return false;
            }
        });
        card.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(150)));
        return card;
    }

    /** 画日志：最新的在最上面，条数与最后一条时间戳都没变就不重画 */
    private void renderLog() {
        if (logText == null) return;
        List<String[]> snap;
        synchronized (WeakNet.I.events) {
            snap = new ArrayList<String[]>(WeakNet.I.events);
        }
        String sig;
        synchronized (WeakNet.I.events) {
            sig = snap.size() + "|" + (snap.isEmpty() ? "" : snap.get(snap.size() - 1)[0]
                    + snap.get(snap.size() - 1)[2]);
        }
        if (sig.equals(logSig)) return;
        logSig = sig;

        StringBuilder sb = new StringBuilder();
        if (snap.isEmpty()) {
            sb.append("（暂无事件）\n开启接管并访问网络后，连接、断连、丢包、DNS 故障等会记在这里。");
        } else {
            int from = Math.max(0, snap.size() - LOG_MAX);
            for (int i = snap.size() - 1; i >= from; i--) {
                String[] e = snap.get(i);
                sb.append(e[0]).append("  ").append(tagOf(e[1])).append(e[2]).append('\n');
            }
            if (snap.size() > LOG_MAX) {
                sb.append("… 另有 ").append(snap.size() - LOG_MAX).append(" 条较早事件未显示");
            }
        }
        logText.setText(sb.toString());
    }

    /** 事件类型 → 便于扫一眼的中文前缀 */
    private static String tagOf(String type) {
        if (type == null) return "";
        if ("config".equals(type)) return "[配置] ";
        if ("connect".equals(type)) return "[连接] ";
        if ("disconnect".equals(type)) return "[断连] ";
        if ("dns".equals(type)) return "[DNS] ";
        if ("timeout".equals(type)) return "[超时] ";
        if ("reject".equals(type)) return "[拒连] ";
        if ("flap".equals(type)) return "[网络切换] ";
        if ("share".equals(type)) return "[共享] ";
        if ("error".equals(type)) return "[错误] ";
        return "[" + type + "] ";
    }

    // ---------------------------------------------------------------- 场景

    /** key → Profile：内置预设 key 或 u:<id> 形式的自定义场景 */
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

    /** key 集合没变就不重建网格（避免每 3 秒把整片按钮推翻重画） */
    private void reloadScenesIfChanged() {
        StringBuilder sb = new StringBuilder();
        List<String[]> builtin = new ArrayList<String[]>();
        for (String k : Profile.KEYS) {
            Profile p = null;
            try {
                p = Profile.build(k);
            } catch (Exception e) {
                p = null;
            }
            if (p == null) continue;
            String s = Profile.shortOf(k);
            builtin.add(new String[]{k, s.length() > 0 ? s : p.name});
            sb.append(k).append(',');
        }
        sb.append('|');
        List<String[]> mine = new ArrayList<String[]>();
        for (MyPresets.Item it : MyPresets.load()) {
            mine.add(new String[]{"u:" + it.id, it.name});
            sb.append(it.id).append(',');
        }
        String sig = sb.toString();
        if (sig.equals(sceneSig)) return;
        sceneSig = sig;
        builtinBtns.clear();
        mineBtns.clear();
        renderGrid(builtinGrid, builtin, builtinBtns, "（没有可用场景）");
        renderGrid(mineGrid, mine, mineBtns,
                "（还没有自定义场景：点「➕ 新建」配一组参数，或从别人那儿共享过来）");
    }

    private void renderGrid(LinearLayout host, List<String[]> items,
                            Map<String, Button> out, String empty) {
        if (host == null) return;
        host.removeAllViews();
        if (items.isEmpty()) {
            host.addView(hint(empty));
            return;
        }
        LinearLayout row = null;
        for (int i = 0; i < items.size(); i++) {
            if (i % COLS == 0) {
                row = new LinearLayout(a);
                row.setOrientation(LinearLayout.HORIZONTAL);
                host.addView(row, new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            }
            final String key = items.get(i)[0];
            Button b = new Button(a);
            b.setAllCaps(false);
            b.setTextSize(12);
            b.setSingleLine(true);
            b.setText(items.get(i)[1]);
            b.setBackground(box(0xFFF1F4FA, 0xFFD7DEEA, 8));
            b.setTextColor(0xFF2F3F52);
            b.setPadding(dp(6), dp(8), dp(6), dp(8));
            LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(
                    0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            bp.setMargins(0, dp(3), dp(6), dp(3));
            b.setLayoutParams(bp);
            b.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    applyKey(key);
                }
            });
            row.addView(b);
            out.put(key, b);
        }
    }

    /** 当前生效的场景高亮（内置蓝、自定义紫） */
    private void syncHighlight() {
        String cur = WeakNet.I.presetKey;
        for (Map.Entry<String, Button> e : builtinBtns.entrySet()) {
            boolean on = e.getKey().equals(cur);
            e.getValue().setBackground(box(on ? 0xFF2F6FED : 0xFFF1F4FA,
                    on ? 0xFF2F6FED : 0xFFD7DEEA, 8));
            e.getValue().setTextColor(on ? Color.WHITE : 0xFF2F3F52);
        }
        for (Map.Entry<String, Button> e : mineBtns.entrySet()) {
            boolean on = e.getKey().equals(cur);
            e.getValue().setBackground(box(on ? 0xFF7A4FF3 : 0xFFF1F4FA,
                    on ? 0xFF7A4FF3 : 0xFFD7DEEA, 8));
            e.getValue().setTextColor(on ? Color.WHITE : 0xFF2F3F52);
        }
    }

    private void applyKey(String key) {
        Profile p = profileOf(key);
        if (p == null) {
            Toast.makeText(a, "这条场景已不存在，已刷新列表", Toast.LENGTH_SHORT).show();
            sceneSig = null;
            reloadScenesIfChanged();
            return;
        }
        // 选中 ≠ 开始：没在测试时只把这支场景记下来，不接管、不限速，
        // 必须用户自己点「▶ 开始弱网测试」才落到流量上（selectScene 里已按 limitOn 判断）
        boolean testing = WeakNet.I.limitOn;
        WeakNet.I.selectScene(p, key);
        WeakNet.I.log("config", (testing ? "切换场景：" : "选择场景：") + p.name);
        Toast.makeText(a, testing ? ("已切换场景：" + p.name)
                        : ("已选「" + p.name + "」，点「▶ 开始弱网测试」才生效"),
                Toast.LENGTH_SHORT).show();
        syncHighlight();
        syncSceneBtns();
        a.updateStatus();
    }

    private void delCurrent() {
        String key = WeakNet.I.presetKey;
        if (key == null || !key.startsWith("u:")) return;
        MyPresets.Item it = MyPresets.find(key.substring(2));
        if (it == null) {
            sceneSig = null;
            reloadScenesIfChanged();
            return;
        }
        String name = it.name;
        MyPresets.delete(it.id);
        WeakNet.I.log("config", "删除自定义场景：" + name);
        // 删掉的正是当前的场景：切回直连，免得 presetKey 指向不存在的 id
        WeakNet.I.setProfile(new Profile(), "direct");
        WeakNet.I.limitOn = false;
        Toast.makeText(a, "已删除「" + name + "」", Toast.LENGTH_SHORT).show();
        sceneSig = null;
        reloadScenesIfChanged();
        syncHighlight();
        syncSceneBtns();
        a.updateStatus();
    }

    // ---------------------------------------------------------------- 场景说明

    /** 「！」入口：列出全部档位的注释与参数摘要（只读参考，点里面的条目不会切换场景） */
    private void openNotes() {
        if (notes == null) notes = new NotesSheet(a);
        notes.show();
    }

    boolean notesShowing() {
        return notes != null && notes.isShowing();
    }

    void closeNotes() {
        if (notes != null) notes.close();
    }

    // ---------------------------------------------------------------- 参数说明

    /** 点参数行末尾的「！」→ 弹出这一个参数的说明 */
    private void openParamNote(String key) {
        if (param == null) param = new ParamSheet(a);
        param.show(key);
    }

    /** 参数说明浮层是否开着（返回键兜底用，见 MainActivity.onBackPressed） */
    boolean paramShowing() {
        return param != null && param.isShowing();
    }

    void closeParam() {
        if (param != null) param.close();
    }

    // ---------------------------------------------------------------- 环境共享

    /** 打开环境共享浮层（组合卡片上的「🔗 共享」按钮也走这里） */
    void openShare() {
        if (share == null) share = new ShareSheet(a);
        share.show();
    }

    // ---------------------------------------------------------------- 自定义场景编辑器

    /** 13 项参数：key / 中文标签 / 默认值（与桌面版自定义面板一致） */
    /**
     * 参数编辑界面的行定义：{key, 显示名（含单位）, 默认值}。
     *
     * 包可见而不是 private：_ParamNotesTest 要拿它跟 {@link Profile#noteOfParam}
     * 做一致性校验（每个参数行都必须有对应的注释，否则「！」点开是空白）。
     */
    static final String[][] FIELDS = {
            {"down_kbps", "下行带宽 (kbps)", "0"},
            {"up_kbps", "上行带宽 (kbps)", "0"},
            {"latency_up_ms", "上行延迟 (ms)", "0"},
            {"latency_down_ms", "下行延迟 (ms)", "0"},
            {"jitter_ms", "抖动 (ms)", "0"},
            {"loss_pct", "丢包 (%)", "0"},
            {"disconnect_pct", "随机断连 (%)", "0"},
            {"timeout_pct", "黑洞：无响应 (%)", "0"},
            {"dns_fail_pct", "DNS 故障 (%)", "0"},
            {"reject_pct", "拒连 (%)", "0"},
            {"stall_ms", "卡顿时长 (ms)", "600"},
            {"flap_sec", "网络切换周期 (s，0=关)", "0"},
            {"flap_blackout_sec", "切换黑窗时长 (s)", "2"},
    };

    /** @param id 要编辑的自定义场景 id；null = 新建 */
    void openEditor(String id) {
        openEditor(id, null, "");
    }

    /**
     * 打开 13 项参数表单。
     *
     * @param id       要编辑的自定义场景 id；null = 新建
     * @param seed     非空 = 以这个内置档位的参数为初值打开（内置档位改不动，
     *                 所以保存时是「另存一条自定义」而不是覆盖它）
     * @param seedName 内置档位的显示名，用来给另存的那条起默认名
     */
    void openEditor(String id, Profile seed, String seedName) {
        final boolean fromBuiltin = (id == null && seed != null);
        editing.set(id == null ? "" : id);
        editorFields.clear();
        edName = null;
        a.suspendFloat();          // 飘窗会盖住弹窗上半部分并吃掉点击
        editorDlg = new Dialog(a);
        editorDlg.requestWindowFeature(Window.FEATURE_NO_TITLE);
        editorDlg.setCancelable(true);
        editorDlg.setCanceledOnTouchOutside(true);
        editorDlg.setOnDismissListener(new DialogInterface.OnDismissListener() {
            public void onDismiss(DialogInterface d) {
                a.restoreFloat();
            }
        });

        JSONObject cur = null;
        String curName = "";
        if (id != null) {
            MyPresets.Item it = MyPresets.find(id);
            if (it != null) {
                cur = it.profile.toJson();
                curName = it.name;
            }
        } else if (fromBuiltin) {
            cur = seed.toJson();
            String n = (seedName == null || seedName.length() == 0) ? "自定义场景" : seedName;
            curName = n + " 副本";
        }

        ScrollView sv = new ScrollView(a);
        LinearLayout card = new LinearLayout(a);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(box(Color.WHITE, 0xFFDDE3EC, 14));
        card.setPadding(dp(14), dp(12), dp(14), dp(14));

        // 标题行：标题占满剩余宽度，右侧一个 ✕ 关闭——
        // 表单是模态浮层，只靠点外部/返回键收起的话，键盘弹起时用户容易找不到退路
        LinearLayout head = new LinearLayout(a);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView t = new TextView(a);
        t.setText(id != null ? "✏️ 编辑自定义场景"
                : (fromBuiltin ? "👀 内置档位参数" : "➕ 新建自定义场景"));
        t.setTextSize(16);
        t.setTextColor(0xFF1F2430);
        t.setTypeface(null, Typeface.BOLD);
        head.addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView close = new TextView(a);
        close.setText("✕");
        close.setTextSize(17);
        close.setTextColor(0xFF8A94A6);
        close.setPadding(dp(10), dp(2), dp(4), dp(2));
        close.setContentDescription("关闭自定义参数");
        close.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                cancelEdit();
            }
        });
        head.addView(close);
        card.addView(head);
        card.addView(divider(dp(8)));

        if (fromBuiltin) {
            String n = (seedName == null || seedName.length() == 0) ? "该档位" : seedName;
            card.addView(hint("下面是「" + n + "」的参数，可以直接改。"
                    + "内置档位本身是固定的，改完点「另存为自定义并应用」，"
                    + "会新建一条到「我的自定义」，不会影响原来的档位。"));
        }

        edName = new EditText(a);
        edName.setHint("场景名称，例：地铁早高峰");
        edName.setText(curName);
        edName.setTextSize(14);
        // content-desc：既是无障碍标签，也让 uiautomator 脚本能精确定位每个输入框
        edName.setContentDescription("场景名称");
        card.addView(edName, full(0, 0, 0, 6));

        for (String[] f : FIELDS) {
            LinearLayout row = new LinearLayout(a);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            TextView lb = new TextView(a);
            lb.setText(f[1]);
            lb.setTextSize(12);
            lb.setTextColor(0xFF5A6472);
            // 名字按内容宽度排，不能给 weight=1 —— 那样名字会占满整行，
            // 把「！」顶到最右边，看着像输入框的前缀而不是「这个参数的说明」
            row.addView(lb, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));

            // 名字后面紧跟一个「！」：点开是这个参数的作用与坑（见 ParamSheet）。
            // 用 TextView 而不是 Button —— Button 自带 48dp 最小尺寸，会把
            // 右边的输入框挤掉；也不加背景框、只用灰字，13 个蓝框按钮会把参数名淹掉。
            final String fk = f[0];
            final String fl = f[1];
            TextView help = new TextView(a);
            help.setText("！");
            help.setTextSize(11);
            help.setTextColor(0xFF9AA4B2);
            help.setGravity(Gravity.CENTER);
            help.setPadding(dp(5), dp(1), dp(5), dp(1));
            help.setContentDescription("查看「" + fl + "」的说明");
            help.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    openParamNote(fk);
                }
            });
            row.addView(help, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));

            // 弹性空白：把输入框顶到右边，各行的输入框才能对齐
            View spacer = new View(a);
            row.addView(spacer, new LinearLayout.LayoutParams(0, dp(1), 1f));

            EditText ed = new EditText(a);
            ed.setTextSize(13);
            ed.setSingleLine(true);
            ed.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
            ed.setGravity(Gravity.END);
            ed.setContentDescription(f[1]);
            String v = cur != null ? String.valueOf(cur.opt(f[0])) : f[2];
            if (cur == null) v = f[2];
            ed.setText(v);
            LinearLayout.LayoutParams ep = new LinearLayout.LayoutParams(dp(96),
                    ViewGroup.LayoutParams.WRAP_CONTENT);
            ep.setMargins(dp(8), dp(2), 0, dp(2));
            row.addView(ed, ep);
            card.addView(row);
            editorFields.put(f[0], ed);
        }

        card.addView(hint("带宽填 0 表示不限；百分比是每条连接命中该故障的概率。"));

        // 底部固定一行：保存并应用——
        // 13 行参数肯定超屏，按钮放内容末尾的话得先滚到底才点得到；
        // 固定在底部后键盘弹出、翻看参数时它们都一直在眼前。
        // 原来这里还挂了个「取消」，跟标题行的 ✕ 是同一个功能
        // （都是不保存直接关），两个入口摆在一起只会让人犹豫该点哪个
        Button save = new Button(a);
        save.setAllCaps(false);
        save.setText(fromBuiltin ? "另存为自定义并应用" : "保存并应用");
        save.setTextSize(14);
        save.setTextColor(Color.WHITE);
        save.setBackground(box(0xFF2F6FED, 0xFF2F6FED, 8));
        save.setPadding(dp(12), dp(11), dp(12), dp(11));
        save.setContentDescription("保存并应用");
        save.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                saveEdited();
            }
        });

        LinearLayout foot = new LinearLayout(a);
        foot.setOrientation(LinearLayout.HORIZONTAL);
        foot.addView(save, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        sv.addView(card, new android.widget.FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        LinearLayout outer = new LinearLayout(a);
        outer.setOrientation(LinearLayout.VERTICAL);
        outer.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        outer.addView(foot, full(0, dp(8), 0, 0));

        int m = dp(12);
        android.widget.FrameLayout.LayoutParams ol =
                new android.widget.FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        ol.setMargins(m, m, m, m);
        editorDlg.setContentView(outer, ol);

        Window w = editorDlg.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            w.setLayout(WindowManager.LayoutParams.MATCH_PARENT,
                    WindowManager.LayoutParams.WRAP_CONTENT);
            w.setGravity(Gravity.CENTER);
            // 键盘弹出时压缩内容区（而不是把整个窗口平推出屏），
            // 底部固定的保存按钮在键盘上方始终可见可点
            w.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
            editorDlg.setOnShowListener(new DialogInterface.OnShowListener() {
                public void onShow(DialogInterface di) {
                    if (editorDlg == null) return;
                    Window win = editorDlg.getWindow();
                    if (win == null) return;
                    try {
                        int wide = a.getResources().getDisplayMetrics().widthPixels - dp(24);
                        int maxH = (int) (a.getResources().getDisplayMetrics().heightPixels * 0.9);
                        card.measure(
                                View.MeasureSpec.makeMeasureSpec(wide, View.MeasureSpec.EXACTLY),
                                View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
                        // 内容高度 + 底部按钮行（约 46dp）+ 外边距
                        int h = card.getMeasuredHeight() + dp(46) + dp(24);
                        win.setLayout(WindowManager.LayoutParams.MATCH_PARENT,
                                Math.min(h, maxH));
                    } catch (Exception e) {
                        // 测量失败就保持系统默认高度
                    }
                }
            });
        }
        editorDlg.show();
    }

    /** 关掉表单（不保存） */
    private void closeEditor() {
        if (editorDlg != null) {
            try {
                editorDlg.dismiss();
            } catch (Exception e) {
                // ignore
            }
            editorDlg = null;
        }
        editorFields.clear();
        edName = null;
    }

    /** ✕ / 取消：不写入任何东西，直接收起表单 */
    private void cancelEdit() {
        closeEditor();
        Toast.makeText(a, "已取消，未保存", Toast.LENGTH_SHORT).show();
    }

    /** 读取表单 → 保存 → 立即应用为当前场景 */
    private void saveEdited() {
        String name = edName == null ? "" : String.valueOf(edName.getText()).trim();
        if (name.length() == 0) name = "自定义场景";
        JSONObject o = new JSONObject();
        try {
            for (String[] f : FIELDS) {
                EditText ed = editorFields.get(f[0]);
                String s = ed == null ? "" : String.valueOf(ed.getText()).trim();
                if (s.length() == 0) continue;
                // 有小数点的按 double 读（百分比），其余按整数（带宽、毫秒）
                if (s.indexOf('.') >= 0) o.put(f[0], Double.parseDouble(s));
                else o.put(f[0], Long.parseLong(s));
            }
        } catch (Exception e) {
            Toast.makeText(a, "参数填得不对：" + e.getMessage(), Toast.LENGTH_SHORT).show();
            return;
        }
        String id = editing.get();
        MyPresets.Item it = MyPresets.save(id.length() == 0 ? null : id, name, o);
        if (it == null) {
            Toast.makeText(a, "保存失败", Toast.LENGTH_SHORT).show();
            return;
        }
        // 与「选场景」同一条规矩：没在测试时保存只是存下来并选中，不自动开始
        boolean testing = WeakNet.I.limitOn;
        WeakNet.I.selectScene(it.profile.copy(), "u:" + it.id);
        WeakNet.I.log("config", "保存自定义场景：" + it.name);
        Toast.makeText(a, testing ? ("已保存并应用：" + it.name)
                        : ("已保存「" + it.name + "」，点「▶ 开始弱网测试」才生效"),
                Toast.LENGTH_SHORT).show();
        closeEditor();
        sceneSig = null;
        reloadScenesIfChanged();
        syncHighlight();
        syncSceneBtns();
        a.updateStatus();
    }

    // ---------------------------------------------------------------- 控件工厂

    private LinearLayout cardShell() {
        LinearLayout c = new LinearLayout(a);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setBackground(box(0xFFF7F9FC, 0xFFE3E8EF, 12));
        c.setPadding(dp(12), dp(10), dp(12), dp(12));
        return c;
    }

    private LinearLayout.LayoutParams cardLp() {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(dp(14), 0, dp(14), dp(10));
        return p;
    }

    private LinearLayout.LayoutParams full(int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(dp(l), dp(t), dp(r), dp(b));
        return p;
    }

    private TextView title(String text) {
        TextView t = new TextView(a);
        t.setText(text);
        t.setTextSize(14);
        t.setTextColor(0xFF1F2430);
        t.setTypeface(null, Typeface.BOLD);
        t.setPadding(0, 0, 0, dp(2));
        return t;
    }

    private TextView hint(String text) {
        TextView t = new TextView(a);
        t.setText(text);
        t.setTextSize(11);
        t.setTextColor(0xFF8A94A6);
        t.setPadding(dp(2), dp(2), dp(2), 0);
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
