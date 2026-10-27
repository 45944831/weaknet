package com.weaknet.app;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.VpnService;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

/**
 * 主界面（原生 UI，取代原来的内嵌网页控制台）：
 *   顶栏（标题 + 「⚙ 设置」按钮） + 状态卡 + 一键接管按钮 + 三块内容面板。
 *
 * 顶栏固定不滚，下面的整体滚动：状态卡 + **总开关一行**（**开始 / 停止弱网测试** 与
 * **「指定程序…」并排**）+ 来自 {@link MainPanels} 的「🎯 测试环境」
 * 「⭐ 我的场景与组合」（场景 / 组合用标签切换）「📋 事件日志」——
 * 这些都是日常高频操作，为了选个场景或开关限速还要先开一层浮层纯属多余。
 *
 * 「接管流量」与「施加限速」原来是两个按钮，实际是一件事的两半：不接管，限速根本落不到
 * App 上；不限速，接管了也只是白跑一趟。所以合并成一个总开关——**开 = 接管并限速（开始
 * 弱网测试），关 = 断开接管（停止弱网测试，网络立刻恢复正常）**。
 *
 * 「这次测谁」（接管范围）与这个总开关是同一层的两个决定，所以「指定程序…」
 * 直接和它并排，勾选界面放在 {@link ScopeSheet} 里。
 *
 * 「⚙ 设置」浮层里只剩**限速飘窗**这一项（见 {@link SettingsSheet}）。
 *
 * 网页控制台下线后，HTTP 服务只剩 /api/* 供脚本调用。
 */
public class MainActivity extends Activity {
    static final int REQ_VPN = 1001;
    static final int REQ_OVERLAY = 1002;

    private Button btn;
    private Button btnScope;
    /** 授权 VPN（系统弹窗）回来后才补上「开启限速」这一步 */
    private boolean pendingLimit = false;
    private TextView labScope;
    private TextView status;
    private TextView tip;
    private SettingsSheet sheet;
    private ScopeSheet scope;
    private MainPanels panels;
    private final Handler handler = new Handler();

    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 固定竖版：Manifest 已声明 portrait，这里再做一次运行时兜底，
        // 覆盖部分厂商 ROM 忽略 manifest 声明 / 横屏状态下冷启动的情况。
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT);

        // 自定义场景与弱网组合都落在同一个 SharedPreferences：先把上下文备好，
        // 免得服务还没起来时读到的列表是空的
        MyPresets.bind(this);
        MyCombos.bind(this);

        // 控制台（同时提供可选的 HTTP 代理通道）
        Intent console = new Intent(this, NetService.class);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(console);
        else startService(console);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);

        // ---- 顶栏：标题 + 设置按钮（设置界面放在程序顶部，点它弹出悬浮二级界面）----
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(0xFFF4F6FA);
        bar.setPadding(dp(14), dp(10), dp(14), dp(10));

        TextView title = new TextView(this);
        title.setText("⚡ WeakNet");
        title.setTextSize(16);
        title.setTextColor(0xFF1F2430);
        title.setTypeface(null, Typeface.BOLD);
        bar.addView(title, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        Button btnSet = new Button(this);
        btnSet.setText("⚙ 设置");
        btnSet.setAllCaps(false);
        btnSet.setTextSize(13);
        btnSet.setTextColor(0xFF2F6FED);
        btnSet.setBackground(box(Color.WHITE, 0xFFD7DEEA, 8));
        btnSet.setPadding(dp(14), dp(6), dp(14), dp(6));
        btnSet.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openSettings();
            }
        });
        bar.addView(btnSet);
        root.addView(bar);

        // ---- 顶栏之下整体滚动（顶栏固定，不在滚动区内）----
        ScrollView sv = new ScrollView(this);
        sv.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        sv.addView(content);
        root.addView(sv);

        // ---- 状态卡（只读概览；改设置请走「⚙ 设置」）----
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(box(0xFFF7F9FC, 0xFFE3E8EF, 12));
        status = new TextView(this);
        status.setTextColor(0xFF4A5260);
        status.setTextSize(13);
        status.setPadding(dp(12), dp(10), dp(12), dp(10));
        card.addView(status);
        LinearLayout.LayoutParams lpCard = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lpCard.setMargins(dp(14), dp(14), dp(14), dp(10));
        content.addView(card, lpCard);

        // ---- 总开关一行：[开始 / 停止弱网测试] + [指定程序…] ----
        // 接管与限速合成一个动作：开=接管并按当前场景限速；关=断开接管，网络立刻恢复。
        // 「这次测谁」和「测不测」是同一层的两个决定，并排放，别让后者藏在浮层里。
        LinearLayout takeRow = new LinearLayout(this);
        takeRow.setOrientation(LinearLayout.HORIZONTAL);
        takeRow.setGravity(Gravity.CENTER_VERTICAL);

        btn = new Button(this);
        btn.setAllCaps(false);
        btn.setTextSize(15);
        btn.setTextColor(Color.WHITE);
        btn.setPadding(dp(12), dp(13), dp(12), dp(13));
        btn.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                toggleTest();
            }
        });
        takeRow.addView(btn, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        btnScope = new Button(this);
        btnScope.setAllCaps(false);
        btnScope.setTextSize(13);
        btnScope.setText("指定程序…");
        btnScope.setPadding(dp(12), dp(13), dp(12), dp(13));
        btnScope.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openScope();
            }
        });
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        sp.setMargins(dp(8), 0, 0, 0);
        takeRow.addView(btnScope, sp);

        LinearLayout.LayoutParams lpBtn = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lpBtn.setMargins(dp(14), 0, dp(14), dp(4));
        content.addView(takeRow, lpBtn);

        labScope = new TextView(this);
        labScope.setTextSize(11);
        labScope.setTextColor(0xFF8A94A6);
        labScope.setPadding(dp(14), 0, dp(14), dp(8));
        content.addView(labScope);

        tip = new TextView(this);
        tip.setTextColor(Color.GRAY);
        tip.setTextSize(11);
        tip.setPadding(dp(14), dp(2), dp(14), dp(10));
        tip.setText("选好场景按总开关就开始测；停止会断开接管、网络立刻恢复正常。"
                + "右上角「⚙ 设置」只管限速飘窗。");
        content.addView(tip);

        // ---- 三块内容面板：测试环境 / 我的自定义 / 事件日志 ----
        panels = new MainPanels(this);
        panels.build(content);

        setContentView(root);

        updateStatus();
        // 启动时不自动接管：接管与限速是一体的，只能由用户点总开关触发。
        // 早期版本会在这里 requestVpn(false) 自动接管，结果是刚打开 App 就已经把
        // 流量拽进通道（状态栏挂着 VPN 图标，却什么都没限），与「手动启动才生效」相悖。
        startStatusLoop();
    }

    private void openSettings() {
        suspendFloat();
        if (sheet == null) sheet = new SettingsSheet(this);
        sheet.show();
    }

    /** 「指定程序…」：接管范围（哪些 App 进弱网），二级界面里勾选 */
    private void openScope() {
        suspendFloat();
        if (scope == null) scope = new ScopeSheet(this);
        scope.show();
    }

    // ---- 限速飘窗是 SYSTEM_ALERT_WINDOW，会盖在二级界面上方把点击吃掉 ----
    // 表现：弱网测试中打开「指定程序」，模式按钮（接管一切 / 仅选中的）点不动，
    // 因为飘窗的矩形正好压在弹窗上半部分。所以弹窗期间先收起来，关掉再放回去。
    private boolean floatWasShown = false;

    /** 打开二级界面前调用：记下飘窗状态并收起 */
    void suspendFloat() {
        if (FloatWindow.isShown()) {
            floatWasShown = true;
            FloatWindow.get(this).hide();
        }
    }

    /** 二级界面关掉后调用：原来显示着就放回去 */
    void restoreFloat() {
        if (!floatWasShown) return;
        floatWasShown = false;
        if (FloatWindow.canOverlay(this)) FloatWindow.get(this).show();
    }

    /** 二级界面里改完范围后要延迟再看一次状态（重建 tun 需要一点时间） */
    Handler getHandler() {
        return handler;
    }

    /**
     * 申请 VPN 授权并接管（免手动配置代理）。
     * @param userInitiated true=用户点了接管按钮；false=App 启动时的自动接管。
     *   只有「自动接管」才需要尊重上次的停止意图；用户主动点按钮时必须放行，
     *   否则停止一次后就再也启不来了。
     */
    private void requestVpn(boolean userInitiated) {
        requestVpn(userInitiated, false);
    }

    /**
     * @param silent true = 尚未授权时不弹系统授权框（悄悄放弃）。
     *   切场景后的自动接管用它：用户只是换个场景，不该被系统弹窗打断；
     *   真要接管时他会自己点总开关，那时再弹。
     */
    private void requestVpn(boolean userInitiated, boolean silent) {
        if (VpnNetService.active) {
            updateStatus();
            return;
        }
        if (!userInitiated && VpnNetService.userStopped) {   // 自动接管：用户刚点过停止，不要自作主张
            updateStatus();
            return;
        }
        Intent prepare = null;
        try {
            prepare = VpnService.prepare(this);
        } catch (Exception e) {
            prepare = null;
        }
        if (prepare != null) {
            if (silent) {
                updateStatus();
                return;
            }
            startActivityForResult(prepare, REQ_VPN);
        } else {
            startVpn();
        }
    }

    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_OVERLAY) {
            if (FloatWindow.canOverlay(this)) {
                FloatWindow.get(this).show();
                Toast.makeText(this, "已获得悬浮窗权限，飘窗已显示", Toast.LENGTH_SHORT).show();
            }
            updateStatus();
            return;
        }
        if (requestCode == REQ_VPN) {
            if (resultCode == RESULT_OK) {
                startVpn();          // pendingLimit 由 startVpn 接着处理
            } else {
                pendingLimit = false;
                Toast.makeText(this, "未授权 VPN，弱网不会作用于本机 App", Toast.LENGTH_SHORT).show();
            }
        }
    }

    private void startVpn() {
        Intent i = new Intent(this, VpnNetService.class);
        i.setAction(VpnNetService.ACTION_START);   // 显式启动意图：服务据此清除 userStopped
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
        else startService(i);
        handler.postDelayed(new Runnable() {
            public void run() {
                updateStatus();
            }
        }, 800);
        // 已经授权过时不会走 onActivityResult，pendingLimit 必须在这里消费掉，
        // 否则表现就是「点了开始，流量接管了却一点没变慢」
        if (pendingLimit) {
            pendingLimit = false;
            handler.postDelayed(new Runnable() {
                public void run() {
                    applyLimitOn();
                }
            }, 900);
        }
    }

    /** 是否正在弱网测试：既接管了流量、又施加了限速，缺一个都不算在测 */
    private boolean testing() {
        return VpnNetService.active && WeakNet.I.limitOn;
    }

    /** 打开环境共享浮层（组合卡片上的「🔗 共享」按钮走这里） */
    void openShareSheet() {
        if (panels != null) panels.openShare();
    }

    /** 状态刷新：主界面 + 设置弹窗（若打开着）一起更新 */
    void updateStatus() {
        boolean on = VpnNetService.active;
        boolean lim = WeakNet.I.limitOn;
        Profile p = WeakNet.I.profile;
        String scope = scopeText();
        // 未启动时 profile 还是直连（选场景不会灌进引擎），所以「已选哪一支」要按
        // presetKey 找，直接显示 p.name 会永远显示「直连（不限制）」
        String scene = sceneName();
        if (on && lim) {
            status.setText("弱网测试进行中 · " + p.name + "\n" + p.summary() + "\n已接管流量（" + scope + "）");
        } else if (on) {
            status.setText("已接管流量，但未开始弱网测试\n按上面的按钮开始（已选：" + scene + "）");
        } else {
            status.setText("未接管 · 按「▶ 开始弱网测试」开始\n已选场景：" + scene);
        }
        syncTestBtn();
        syncScope();
        if (sheet != null) sheet.sync();
        if (panels != null) panels.sync();
    }

    /** 当前「选中」的场景名（没开始时 profile 仍是直连，得按 presetKey 反查） */
    private String sceneName() {
        String key = WeakNet.I.presetKey;
        if (key == null) return "直连";
        if (key.startsWith("u:")) {
            MyPresets.Item it = MyPresets.find(key.substring(2));
            return it != null ? it.name : "自定义";
        }
        String s = Profile.shortOf(key);
        return s.length() > 0 ? s : key;
    }

    /** 当前接管范围的一句话简述，供状态卡复用 */
    private String scopeText() {
        String m = WeakNet.I.scopeMode;
        int n = WeakNet.I.scopePkgs.size();
        if ("only".equals(m)) return "仅 " + n + " 个 App";
        if ("exclude".equals(m)) return "排除 " + n + " 个 App";
        return "全部 App";
    }

    /** 「指定程序…」按钮与其下方一行说明：显示当前生效的接管范围 */
    private void syncScope() {
        if (btnScope == null || labScope == null) return;
        String m = WeakNet.I.scopeMode;
        int n = WeakNet.I.scopePkgs.size();
        boolean all = !"only".equals(m) && !"exclude".equals(m);
        if (all) {
            btnScope.setText("指定程序…");
            labScope.setText("范围：全部 App（点「指定程序…」改成只测某几个 / 排除某几个）");
        } else if ("only".equals(m)) {
            btnScope.setText("指定程序（仅 " + n + " 个）");
            labScope.setText("范围：仅名单内的 " + n + " 个 App 进弱网，其余完全不受影响");
        } else {
            btnScope.setText("指定程序（排除 " + n + " 个）");
            labScope.setText("范围：名单内的 " + n + " 个 App 不受影响，其余都进弱网");
        }
        btnScope.setTextColor(all ? 0xFF2F3F52 : 0xFF7A4FF3);
        btnScope.setBackground(box(all ? 0xFFF1F4FA : 0xFFF3EFFF,
                all ? 0xFFD7DEEA : 0xFFD9CBFB, 10));
    }

    /** 总开关按钮的文字与配色跟着实时状态走 */
    private void syncTestBtn() {
        if (btn == null) return;
        boolean on = testing();
        btn.setText(on ? "⏹ 停止弱网测试" : "▶ 开始弱网测试");
        btn.setTextColor(Color.WHITE);
        btn.setBackground(box(on ? 0xFFE5484D : 0xFF2F6FED,
                on ? 0xFFE5484D : 0xFF2F6FED, 10));
    }

    /** 总开关：开 = 接管并按当前场景限速；关 = 断开接管，网络恢复 */
    private void toggleTest() {
        if (testing()) stopTest();
        else startTest();
    }

    private void startTest() {
        pendingLimit = true;
        if (!VpnNetService.active) {
            requestVpn(true);      // 授权回来（onActivityResult）再补 applyLimitOn
        } else {
            pendingLimit = false;
            applyLimitOn();
        }
    }

    /** 施加限速：按当前 presetKey 复原场景；没有就用最后一次生效的场景 */
    private void applyLimitOn() {
        Profile restore = profileOf(WeakNet.I.presetKey);
        if (restore == null && "custom".equals(WeakNet.I.presetKey)) {
            // 自定义参数：当前 profile 就是用户刚拖出来的那份，原样保留。
            // 不能走下面的 lastProfile 回落——参数全 0（或只有 stall 之类不算 limits
            // 的字段）时 hasLimits() 为 false，会把用户刚设的参数整个换成上次的场景。
            restore = WeakNet.I.profile;
        } else if (restore == null || !restore.hasLimits()) {
            if (WeakNet.I.lastProfile != null) restore = WeakNet.I.lastProfile;
        }
        if (restore == null) {
            try {
                restore = Profile.build("direct");
            } catch (Exception e) {
                restore = new Profile();
            }
        }
        WeakNet.I.setLimitOn(true, restore);
        // 选中的是弱网组合时，它不是一支静态场景：交给播放序列按 steps 逐段推进
        boolean combo = WeakNet.I.resumeIfCombo();
        WeakNet.I.log("config", combo ? "开始播放组合：" + WeakNet.I.comboProgress()
                : "开始弱网测试：" + restore.name);
        Toast.makeText(this, combo
                        ? ("已开始播放组合：" + WeakNet.I.comboProgress())
                        : ("已开始弱网测试：" + restore.name),
                Toast.LENGTH_SHORT).show();
        updateStatus();
        // 开了限速但没接管（例：授权被拒）时说清楚，免得以为在测其实没生效
        handler.postDelayed(new Runnable() {
            public void run() {
                if (WeakNet.I.limitOn && !VpnNetService.active) {
                    Toast.makeText(MainActivity.this,
                            "流量未被接管，弱网不会生效——请重新点「▶ 开始弱网测试」并允许 VPN",
                            Toast.LENGTH_LONG).show();
                }
            }
        }, 1500);
    }

    /** 停止：先撤掉限速再断开接管，网络立刻恢复；场景保留，再开直接复原 */
    private void stopTest() {
        WeakNet.I.setLimitOn(false, null);
        WeakNet.I.log("config", "停止弱网测试（场景保留：" + WeakNet.I.presetKey + "）");
        VpnNetService.stop(this);
        Toast.makeText(this, "已停止弱网测试，网络恢复正常", Toast.LENGTH_SHORT).show();
        updateStatus();
        handler.postDelayed(new Runnable() {
            public void run() {
                updateStatus();
            }
        }, 900);
    }

    /**
     * key → Profile：内置预设名或 u:<id> 形式的自定义场景。
     *
     * 组合（c:<id>）这里故意返回 null：它不是一支静态场景，没有「一个」Profile 可以代表，
     * 真正的生效由 {@link WeakNet#resumeIfCombo} 按 steps 逐段推进（见 applyLimitOn）。
     */
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

    private void startStatusLoop() {
        handler.postDelayed(new Runnable() {
            public void run() {
                if (!isFinishing()) {
                    updateStatus();
                    handler.postDelayed(this, 3000);
                }
            }
        }, 3000);
    }

    protected void onDestroy() {
        if (panels != null) panels.close();
        if (scope != null) scope.close();
        if (sheet != null) sheet.close();
        super.onDestroy();
    }

    /** 极少数 ROM 仍会投递横屏配置变化，这里再按回竖版，保证界面方向恒定 */
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        if (newConfig.orientation != Configuration.ORIENTATION_PORTRAIT) {
            setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT);
        }
    }

    public void onBackPressed() {
        // 说明浮层是 Dialog，正常会自己吃掉返回键；这里兜一道底，
        // 免得将来改成不可取消时，返回键直接把整个界面退到后台
        if (panels != null && panels.notesShowing()) {
            panels.closeNotes();
            return;
        }
        // 参数说明浮层叠在参数编辑浮层上面，先收最上面那一层
        if (panels != null && panels.paramShowing()) {
            panels.closeParam();
            return;
        }
        if (panels != null && panels.shareShowing()) {
            panels.closeShare();            // 共享浮层开着时，返回先收起它
            return;
        }
        if (scope != null && scope.isShowing()) {
            scope.close();                  // 「指定程序」浮层同理
            return;
        }
        if (sheet != null && sheet.isShowing()) {
            sheet.close();                  // 设置浮层开着时同理
            return;
        }
        moveTaskToBack(true);
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
                getResources().getDisplayMetrics());
    }
}
