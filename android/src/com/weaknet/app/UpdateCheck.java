package com.weaknet.app;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URL;
import java.util.ArrayList;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * GitHub 新版本检查（Android 端，与 Windows 的 update_check.py 同规则）。
 *
 * 设计约束：
 * - 只做一件事：拿线上最新正式 Release 的 tag，跟本机 versionName 比大小。
 * - 绝不阻塞界面：网络请求一律子线程，回调经主线程 Handler 派发。
 * - 绝不打扰用户：断网 / 限流 / 坏 JSON 一律返回 null，界面保持默认灰色。
 * - 直连优先：本工具会把系统代理 / VPN 指向本地，先以 Proxy.NO_PROXY 直连，
 *   失败再退回系统默认通道，避免请求被自己接管后绕回代理端口。
 * - **每次进程启动只检查一次**：结果常驻内存，运行中不再联网复查，
 *   设置界面只读这份结果；想看新版本就重启 App。draft / prerelease 不算新版。
 *
 * apiOverride 只用于真机自动化测试：MainActivity 从 Intent extra 注入，
 * 正式使用时始终为 null。
 */
public final class UpdateCheck {

    public static final String OWNER = "45944831";
    public static final String REPO = "weaknet";
    public static final String PAGE_LATEST =
            "https://github.com/" + OWNER + "/" + REPO + "/releases/latest";

    private static final String API_LATEST =
            "https://api.github.com/repos/" + OWNER + "/" + REPO + "/releases/latest";
    private static final int TIMEOUT = 6000;
    private static final Pattern VERSION_HEAD =
            Pattern.compile("^[vV]?(\\d+(?:\\.\\d+)*)");

    private static volatile String apiOverride = null;
    private static volatile State cached = null;

    private static final Object LOCK = new Object();
    private static final ArrayList<Callback> waiting = new ArrayList<Callback>();
    private static boolean checking = false;
    private static boolean attempted = false;

    private UpdateCheck() {
    }

    /** 结果快照。ok=false 表示这次检查失败（界面保持灰色，不弹错）。 */
    public static final class State {
        public final boolean ok;
        public final boolean hasNew;
        public final String latest;
        public final String page;

        State(boolean ok, boolean hasNew, String latest, String page) {
            this.ok = ok;
            this.hasNew = hasNew;
            this.latest = latest == null ? "" : latest;
            this.page = page == null || page.length() == 0 ? PAGE_LATEST : page;
        }
    }

    public interface Callback {
        void onResult(State s);
    }

    // ---------------------------------------------------------------- 对外接口

    /** 仅供真机测试：覆盖 API 地址（如 http://127.0.0.1:18999/latest）。 */
    public static void setApiOverride(String url) {
        apiOverride = (url == null || url.length() == 0) ? null : url;
        synchronized (LOCK) {
            cached = null;
            attempted = false;
        }
    }

    /** 启动那一次检查的结果；还没落地 / 检查失败时为 null。 */
    public static State cached() {
        return cached;
    }

    /** 检查是否还在飞（设置界面用来显示「正在检查更新…」，不触发任何请求）。 */
    public static boolean isChecking() {
        synchronized (LOCK) {
            return checking;
        }
    }

    /** 当前安装的 versionName（与 build.sh 的 VERSION_NAME 同源）。 */
    public static String currentVersion(Context c) {
        try {
            PackageInfo pi = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
            return pi.versionName == null ? "" : pi.versionName;
        } catch (Exception e) {
            return "";
        }
    }

    /**
     * 本次进程启动唯一的一次检查。无论成功失败都记下「已试过」，
     * 之后再被调用只回现有结果、绝不发第二次请求；cb 可以为 null（只预热缓存）。
     */
    public static void check(final Context ctx, final Callback cb) {
        final Handler ui = new Handler(Looper.getMainLooper());
        synchronized (LOCK) {
            if (cb != null) waiting.add(cb);
            if (checking) return;           // 请求在飞，结果由那次统一派发
            if (attempted) {                // 已查过（含失败），直接回缓存，不联网
                deliver(ui);
                return;
            }
            attempted = true;
            checking = true;
        }

        final Context app = ctx.getApplicationContext();
        Thread t = new Thread(new Runnable() {
            public void run() {
                State s = fetch(app);
                synchronized (LOCK) {
                    checking = false;
                    if (s != null) cached = s;
                }
                deliver(ui);
            }
        }, "update-check");
        t.setDaemon(true);
        t.start();
    }

    private static void deliver(Handler ui) {
        final ArrayList<Callback> list;
        synchronized (LOCK) {
            if (waiting.isEmpty()) return;
            list = new ArrayList<Callback>(waiting);
            waiting.clear();
        }
        final State s = cached;
        ui.post(new Runnable() {
            public void run() {
                for (Callback c : list) {
                    try {
                        c.onResult(s);
                    } catch (Exception e) {
                        // 界面回调抛错不该影响其它监听者
                    }
                }
            }
        });
    }

    // ---------------------------------------------------------------- 网络

    private static State fetch(Context ctx) {
        State first = request(ctx, true);
        if (first != null) return first;
        return request(ctx, false);          // 直连不通再退回系统默认通道
    }

    private static State request(Context ctx, boolean direct) {
        HttpURLConnection conn = null;
        InputStream in = null;
        try {
            URL u = new URL(apiOverride != null ? apiOverride : API_LATEST);
            conn = direct
                    ? (HttpURLConnection) u.openConnection(Proxy.NO_PROXY)
                    : (HttpURLConnection) u.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(TIMEOUT);
            conn.setReadTimeout(TIMEOUT);
            conn.setUseCaches(false);
            conn.setInstanceFollowRedirects(true);
            conn.setRequestProperty("User-Agent", "WeakNet-Android-update-check");
            conn.setRequestProperty("Accept", "application/vnd.github+json");

            int code = conn.getResponseCode();
            if (code < 200 || code >= 300) return null;
            in = conn.getInputStream();
            String body = new String(readAll(in), "UTF-8");
            JSONObject d = new JSONObject(body);

            String tag = d.optString("tag_name", "");
            if (tag.length() == 0) return null;
            String page = d.optString("html_url", "");
            // 草稿 / 预发布不算正式新版，别拿它去提示用户升级
            if (d.optBoolean("draft") || d.optBoolean("prerelease")) {
                return new State(true, false, tag, PAGE_LATEST);
            }
            return new State(true, isNewer(tag, currentVersion(ctx)), tag, page);
        } catch (Exception e) {
            return null;
        } finally {
            if (in != null) try { in.close(); } catch (Exception e) { /* ignore */ }
            if (conn != null) try { conn.disconnect(); } catch (Exception e) { /* ignore */ }
        }
    }

    private static byte[] readAll(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
            if (out.size() > (1 << 20)) throw new Exception("payload too large");
        }
        return out.toByteArray();
    }

    // ---------------------------------------------------------------- 版本比较（纯函数，便于对照单测）

    /** 'v1.1.14' / '1.2' / 'v2.0-rc1' -> [1,1,14]；非法输入返回 null。 */
    static int[] parseVersion(String s) {
        Matcher m = VERSION_HEAD.matcher(s == null ? "" : s.trim());
        if (!m.find()) return null;
        String[] parts = m.group(1).split("\\.");
        int[] r = new int[parts.length];
        for (int i = 0; i < parts.length; i++) {
            try {
                r[i] = Integer.parseInt(parts[i]);
            } catch (NumberFormatException e) {
                return null;
            }
        }
        return r;
    }

    /** 线上 tag 是否严格大于本地版本；缺位按 0 补齐（1.2 == 1.2.0）。 */
    static boolean isNewer(String latest, String current) {
        int[] a = parseVersion(latest);
        int[] b = parseVersion(current);
        if (a == null || b == null) return false;
        int n = Math.max(a.length, b.length);
        for (int i = 0; i < n; i++) {
            int x = i < a.length ? a[i] : 0;
            int y = i < b.length ? b[i] : 0;
            if (x != y) return x > y;
        }
        return false;
    }
}
