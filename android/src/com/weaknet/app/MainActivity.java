package com.weaknet.app;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.net.VpnService;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.provider.Settings;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/** 主界面：顶部一键接管开关 + 飘窗开关 + 内嵌控制台 */
public class MainActivity extends Activity {
    private static final int REQ_VPN = 1001;
    private static final int REQ_OVERLAY = 1002;
    private static final String URL = "http://127.0.0.1:" + NetService.CTRL_PORT + "/";

    private WebView web;
    private Button btn;
    private Button btnFloat;
    private TextView status;
    private final Handler handler = new Handler();
    private boolean loaded = false;

    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 控制台（同时提供可选的 HTTP 代理通道）
        Intent console = new Intent(this, NetService.class);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(console);
        else startService(console);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);

        status = new TextView(this);
        status.setTextColor(Color.DKGRAY);
        status.setTextSize(13);
        status.setPadding(14, 12, 14, 4);

        btn = new Button(this);
        btn.setTextSize(14);
        btn.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                if (VpnNetService.active) stopVpn();
                else requestVpn();
            }
        });
        btnFloat = new Button(this);
        btnFloat.setTextSize(13);
        btnFloat.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                toggleFloat();
            }
        });

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(14, 6, 14, 8);
        LinearLayout.LayoutParams lp2 = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp2.setMargins(14, 0, 14, 8);
        root.addView(status);
        root.addView(btn, lp);
        root.addView(btnFloat, lp2);

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        web.setWebViewClient(new WebViewClient() {
            public void onPageFinished(WebView view, String url) {
                loaded = true;
            }

            public void onReceivedError(WebView view, int errorCode, String desc, String failingUrl) {
                handler.postDelayed(new Runnable() {
                    public void run() {
                        web.loadUrl(URL);
                    }
                }, 1000);
            }
        });
        root.addView(web, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        setContentView(root);
        web.loadUrl(URL);
        handler.postDelayed(new Runnable() {
            public void run() {
                if (!loaded) web.loadUrl(URL);
            }
        }, 2500);

        updateStatus();
        handler.postDelayed(new Runnable() {
            public void run() {
                requestVpn();
            }
        }, 600);
        startStatusLoop();
    }

    /** 自动申请 VPN 授权并接管（免手动配置代理） */
    private void requestVpn() {
        if (VpnNetService.active) {
            updateStatus();
            return;
        }
        if (VpnNetService.userStopped) {     // 用户刚点过停止，不要再自动接管
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
            startActivityForResult(prepare, REQ_VPN);
        } else {
            startVpn();
        }
    }

    /** 限速飘窗：显示 / 收起（悬浮窗显示当前场景与实时速率，可拖动） */
    private void toggleFloat() {
        if (FloatWindow.isShown()) {
            FloatWindow.get(this).hide();
            Toast.makeText(this, "已收起限速飘窗", Toast.LENGTH_SHORT).show();
            updateStatus();
            return;
        }
        if (!FloatWindow.canOverlay(this)) {
            requestOverlayPermission();
            return;
        }
        FloatWindow.get(this).show();
        if (FloatWindow.isShown()) {
            Toast.makeText(this, "已显示限速飘窗（可拖动，点✕收起）", Toast.LENGTH_SHORT).show();
        } else {
            Toast.makeText(this, "飘窗显示失败，请确认已授权", Toast.LENGTH_SHORT).show();
        }
        updateStatus();
    }

    private void requestOverlayPermission() {
        if (Build.VERSION.SDK_INT >= 23) {
            try {
                Intent i = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:" + getPackageName()));
                startActivityForResult(i, REQ_OVERLAY);
                Toast.makeText(this, "请开启「显示在其他应用上层」权限后重试", Toast.LENGTH_LONG).show();
            } catch (Exception e) {
                Toast.makeText(this, "无法打开悬浮窗权限设置", Toast.LENGTH_SHORT).show();
            }
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
                startVpn();
                Toast.makeText(this, "已接管全部 App 流量", Toast.LENGTH_SHORT).show();
            } else {
                Toast.makeText(this, "未授权 VPN，弱网不会作用于本机 App", Toast.LENGTH_SHORT).show();
            }
        }
    }

    private void startVpn() {
        VpnNetService.userStopped = false;
        Intent i = new Intent(this, VpnNetService.class);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
        else startService(i);
        handler.postDelayed(new Runnable() {
            public void run() {
                updateStatus();
            }
        }, 800);
    }

    /**
     * 停止接管。必须同时做两件事：
     *   1) 发 ACTION_STOP —— 让服务自己 stopSelf，避免 START_STICKY 把它「复活」；
     *   2) stopService —— 兜底。
     * 只调 stopService 的话，服务会被系统重启、tun 重新建立，用户会觉得停不下来。
     */
    private void stopVpn() {
        try {
            Intent i = new Intent(this, VpnNetService.class);
            i.setAction(VpnNetService.ACTION_STOP);
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
            else startService(i);
        } catch (Exception e) {
            // ignore
        }
        try {
            stopService(new Intent(this, VpnNetService.class));
        } catch (Exception e) {
            // ignore
        }
        updateStatus();
        handler.postDelayed(new Runnable() {
            public void run() {
                updateStatus();
            }
        }, 800);
        Toast.makeText(this, "已停止接管，流量恢复正常", Toast.LENGTH_SHORT).show();
    }

    private void updateStatus() {
        boolean on = VpnNetService.active;
        Profile p = WeakNet.I.profile;
        status.setText(on
                ? "状态：已接管全部 App 流量（无需配置代理）\n当前场景：" + p.name + " · " + p.summary()
                : "状态：未接管 · 点击下面按钮一键开启（无需在 Wi-Fi 里手配代理）");
        btn.setText(on ? "停止接管" : "一键接管全部 App 流量");
        if (btnFloat != null) {
            btnFloat.setText(FloatWindow.isShown() ? "收起限速飘窗" : "显示限速飘窗");
        }
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

    public void onBackPressed() {
        moveTaskToBack(true);
    }

    protected void onDestroy() {
        if (web != null) {
            try {
                web.destroy();
            } catch (Exception e) {
                // ignore
            }
        }
        super.onDestroy();
    }
}
