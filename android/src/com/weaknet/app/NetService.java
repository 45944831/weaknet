package com.weaknet.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;

/** 常驻前台服务：承载代理 + 控制台 */
public class NetService extends Service {
    public static final int PROXY_PORT = 8888;
    public static final int CTRL_PORT = 8899;
    private static final String CHANNEL = "weaknet";

    private ProxyServer proxy;
    private CtrlServer ctrl;

    public IBinder onBind(Intent i) {
        return null;
    }

    public void onCreate() {
        super.onCreate();
        WeakNet.I.startedAt = System.currentTimeMillis();
        try {
            proxy = new ProxyServer("0.0.0.0", PROXY_PORT);
            Thread t = new Thread(proxy, "proxy");
            t.setDaemon(true);
            t.start();
            WeakNet.I.log("config", "代理启动 0.0.0.0:" + PROXY_PORT);
        } catch (Exception e) {
            WeakNet.I.log("error", "代理启动失败：" + e.getMessage());
        }
        ctrl = new CtrlServer(CTRL_PORT, proxy, exportDashboard(), this);
        Thread ct = new Thread(ctrl, "ctrl");
        ct.setDaemon(true);
        ct.start();
        startForegroundCompat();
    }

    public int onStartCommand(Intent i, int flags, int startId) {
        return START_STICKY;
    }

    public void onDestroy() {
        if (ctrl != null) ctrl.stop();
        if (proxy != null) proxy.stop();
        WeakNet.I.abortAll();
        super.onDestroy();
    }

    /** 把资产里的控制台页面导出到内部存储，并替换为 Android 版使用说明 */
    private File exportDashboard() {
        File out = new File(getFilesDir(), "dashboard.html");
        String html;
        try {
            InputStream in = getAssets().open("dashboard.html");
            html = readAll(in);
        } catch (Exception e) {
            html = "<html><body>控制台资源缺失</body></html>";
        }
        html = androidHelp(html);
        try {
            FileOutputStream fos = new FileOutputStream(out);
            fos.write(html.getBytes("UTF-8"));
            fos.close();
        } catch (Exception e) {
            // ignore
        }
        return out;
    }

    private static String androidHelp(String html) {
        String help =
                "<li><b>免配置接管：</b>打开本 App 会自动申请 VPN 授权并接管<b>全部 App 的流量</b>，"
                        + "无需在 Wi-Fi 里手动配置代理；未自动接管时点上方「一键接管」按钮并在系统弹窗点「确定」。</li>"
                        + "<li><b>覆盖范围：</b>HTTP / HTTPS / TCP / UDP（游戏、直播、IM 等）全部生效，无需安装证书；"
                        + "工具只模拟网络状况，不解密、不篡改内容。</li>"
                        + "<li><b>把手机当弱网热点：</b>其它设备（电脑 / 另一台手机）把代理指向本机 IP <code>%IP%</code> 端口 <code>8888</code>（同一 Wi-Fi）。</li>"
                        + "<li><b>恢复：</b>点「停止接管」或「直连（不限制）」即可恢复正常网络；也可从系统 VPN 设置里断开。</li>";
        help = help.replace("%IP%", WeakNet.I.lanIp());
        int s = html.indexOf("<!--HELP_START-->");
        int e = html.indexOf("<!--HELP_END-->");
        if (s >= 0 && e > s) html = html.substring(0, s) + help + html.substring(e);
        return html;
    }

    private static String readAll(InputStream in) throws Exception {
        ByteArrayOutputStream bo = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
        in.close();
        return new String(bo.toByteArray(), "UTF-8");
    }

    private void startForegroundCompat() {
        Notification.Builder nb;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "弱网模拟服务",
                    NotificationManager.IMPORTANCE_LOW);
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) nm.createNotificationChannel(ch);
            nb = new Notification.Builder(this, CHANNEL);
        } else {
            nb = new Notification.Builder(this);
        }
        Intent i = new Intent(this, MainActivity.class);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pi = PendingIntent.getActivity(this, 0, i, flags);
        nb.setContentTitle("WeakNet 弱网模拟运行中")
                .setContentText("代理 " + PROXY_PORT + " · 控制台 " + CTRL_PORT)
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentIntent(pi)
                .setOngoing(true);
        startForeground(1, nb.build());
    }
}
