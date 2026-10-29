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
            // 默认只绑回环：代理本身没有鉴权，绑 0.0.0.0 等于把手机变成同网段可用的
            // 开放代理（还能绕过 VPN 的按 App 接管范围）。需要时由 /api/listen 显式打开，
            // 打开后 ProxyServer 会要求 Proxy-Authorization。
            proxy = new ProxyServer("127.0.0.1", PROXY_PORT);
            Thread t = new Thread(proxy, "proxy");
            t.setDaemon(true);
            t.start();
            WeakNet.I.log("config", "代理启动 127.0.0.1:" + PROXY_PORT);
        } catch (Exception e) {
            WeakNet.I.log("error", "代理启动失败：" + e.getMessage());
        }
        // 网页控制台已下线：控制服务只提供 /api/*，界面在 App 原生 MainActivity
        ctrl = new CtrlServer(CTRL_PORT, proxy, this);
        Thread ct = new Thread(ctrl, "ctrl");
        ct.setDaemon(true);
        ct.start();
        // 环境共享：UDP 发现 + 共享端口都挂在这条常驻服务上，App 退到后台也能收推送
        ShareNet.bind(this);
        ShareNet.start();
        startForegroundCompat();
    }

    public int onStartCommand(Intent i, int flags, int startId) {
        return START_STICKY;
    }

    public void onDestroy() {
        if (ctrl != null) ctrl.stop();
        ShareNet.stop();
        if (proxy != null) proxy.stop();
        WeakNet.I.abortAll();
        super.onDestroy();
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
