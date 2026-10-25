package com.weaknet.app;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;

/** 控制 HTTP 服务：只提供 /api/*（界面改为 App 原生 MainActivity） */
public class CtrlServer implements Runnable {
    private static final String JSON_CT = "application/json; charset=utf-8";

    private final int port;
    private final ProxyServer proxy;
    private final android.content.Context ctx;
    private ServerSocket server;
    private volatile boolean stopped = false;
    /** 控制请求处理池：GUI 每 500ms poll 一次 /api/status，来一个请求建一个线程
     *  纯属浪费；控制接口都是低频小请求，4 个线程足够（多客户端同时 poll 也不堵） */
    private final java.util.concurrent.ExecutorService pool =
            Executors.newFixedThreadPool(4, new java.util.concurrent.ThreadFactory() {
                public Thread newThread(Runnable r) {
                    Thread t = new Thread(r, "ctrl-req");
                    t.setDaemon(true);
                    return t;
                }
            });

    public CtrlServer(int port, ProxyServer proxy, android.content.Context ctx) {
        this.port = port;
        this.proxy = proxy;
        this.ctx = ctx;
        MyPresets.bind(ctx);        // 自定义场景要落 SharedPreferences，先把上下文备好
        FloatSettings.bind(ctx);    // 悬浮窗设置同理
    }

    public void run() {
        try {
            server = new ServerSocket();
            server.setReuseAddress(true);
            server.bind(new InetSocketAddress("127.0.0.1", port));
        } catch (IOException e) {
            WeakNet.I.log("error", "控制台启动失败：" + e.getMessage());
            return;
        }
        WeakNet.I.log("config", "控制台启动 127.0.0.1:" + port);
        while (!stopped) {
            try {
                Socket c = server.accept();
                pool.execute(new Handler(c));
            } catch (IOException e) {
                if (stopped) break;
            }
        }
    }

    public void stop() {
        stopped = true;
        try {
            if (server != null) server.close();
        } catch (Exception e) {
            // ignore
        }
        pool.shutdownNow();
    }

    class Handler implements Runnable {
        private final Socket sock;

        Handler(Socket s) {
            sock = s;
        }

        public void run() {
            try {
                BufferedInputStream in = new BufferedInputStream(sock.getInputStream());
                String line = Util.readLine(in);
                if (line == null) return;
                String[] parts = line.split(" ");
                if (parts.length < 2) return;
                String method = parts[0].toUpperCase();
                String path = parts[1];
                int q = path.indexOf('?');
                if (q >= 0) path = path.substring(0, q);

                int len = 0;
                while (true) {
                    String h = Util.readLine(in);
                    if (h == null || h.length() == 0) break;
                    if (h.toLowerCase().startsWith("content-length:")) {
                        try {
                            len = Integer.parseInt(h.substring(h.indexOf(':') + 1).trim());
                        } catch (Exception e) {
                            // ignore
                        }
                    }
                }
                String body = "";
                if (len > 0) {
                    byte[] b = new byte[len];
                    int off = 0;
                    while (off < len) {
                        int r = in.read(b, off, len - off);
                        if (r < 0) break;
                        off += r;
                    }
                    body = new String(b, 0, off, "UTF-8");
                }
                route(method, path, body);
            } catch (Exception e) {
                // ignore
            } finally {
                try {
                    sock.close();
                } catch (Exception e) {
                    // ignore
                }
            }
        }

        private void route(String method, String path, String body) throws Exception {
            // 网页控制台已下线，界面在 App 原生 MainActivity 里。
            // 这里只保留 HTTP API（脚本/自动化仍可调用），访问根路径给一句说明而不是空白页。
            if ("/".equals(path) || "/index.html".equals(path)) {
                respond(200, "text/plain; charset=utf-8",
                        ("WeakNet\n\n控制台已改为 App 原生界面，网页端不再提供。\n"
                                + "HTTP 控制接口仍可用，见 /api/status。\n").getBytes("UTF-8"));
                return;
            }
            if ("/api/status".equals(path)) {
                respond(200, JSON_CT, statusJson().toString().getBytes("UTF-8"));
                return;
            }
            // ---- 环境共享：只读状态，便于脚本验证「手机也能被 PC 发现 / 收到推送」----
            if ("/api/share".equals(path)) {
                respond(200, JSON_CT, ShareNet.statusJson().toString().getBytes("UTF-8"));
                return;
            }
            if ("POST".equals(method) && "/api/share/name".equals(path)) {
                try {
                    ShareNet.setUserName(new JSONObject(body).optString("name", ""));
                    respond(200, JSON_CT, ShareNet.statusJson().toString().getBytes("UTF-8"));
                } catch (Exception e) {
                    respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                }
                return;
            }
            if ("POST".equals(method) && "/api/share/push".equals(path)) {
                // 给脚本用：{peer: "<id>", ids: [...]}；真实界面走 ShareSheet
                try {
                    JSONObject d = new JSONObject(body);
                    ShareNet.Peer p = ShareNet.findPeer(d.optString("peer", ""));
                    JSONArray ids = d.optJSONArray("ids");
                    List<String> list = new ArrayList<String>();
                    if (ids != null) {
                        for (int i = 0; i < ids.length(); i++) list.add(ids.optString(i, ""));
                    }
                    // 推送是同步 HTTP，必须挪出 HTTP 线程；最多等 15 秒
                    final ShareNet.Peer fp = p;
                    final List<String> flist = list;
                    final ShareNet.PushResult[] holder = new ShareNet.PushResult[1];
                    Thread t = new Thread(new Runnable() {
                        public void run() {
                            holder[0] = ShareNet.push(fp, flist);
                        }
                    }, "share-push-api");
                    t.setDaemon(true);
                    t.start();
                    t.join(15000);
                    JSONObject o = new JSONObject();
                    o.put("ok", holder[0] != null && holder[0].ok);
                    o.put("msg", holder[0] == null ? "推送超时或未完成" : holder[0].msg);
                    respond(200, JSON_CT, o.toString().getBytes("UTF-8"));
                } catch (Exception e) {
                    respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                }
                return;
            }
            if ("POST".equals(method) && "/api/share/inbox".equals(path)) {
                try {
                    JSONObject d = new JSONObject(body);
                    JSONObject r = ShareNet.resolveInbox(d.optString("id", ""),
                            d.optString("action", "keep"), d.optString("name", ""));
                    respond(200, JSON_CT, r.toString().getBytes("UTF-8"));
                } catch (Exception e) {
                    respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                }
                return;
            }
            if ("/api/presets".equals(path)) {
                JSONArray a = new JSONArray();
                for (String k : Profile.KEYS) {
                    JSONObject o = new JSONObject();
                    o.put("key", k);
                    o.put("name", Profile.build(k).name);
                    o.put("cat", Profile.catOf(k));
                    a.put(o);
                }
                respond(200, JSON_CT, a.toString().getBytes("UTF-8"));
                return;
            }
            // ---- 自定义栏：用户命名保存的参数（与桌面版接口一致）----
            if ("/api/upresets".equals(path)) {
                respond(200, JSON_CT, MyPresets.listJson().toString().getBytes("UTF-8"));
                return;
            }
            if (method.equals("POST") && "/api/upreset".equals(path)) {
                try {
                    JSONObject d = new JSONObject(body);
                    MyPresets.Item it = MyPresets.save(d.optString("id", ""),
                            d.optString("name", ""), d.optJSONObject("profile"));
                    WeakNet.I.log("config", "保存自定义场景：" + it.name);
                    JSONObject o = new JSONObject();
                    o.put("ok", true);
                    o.put("id", it.id);
                    o.put("name", it.name);
                    o.put("items", MyPresets.toJsonArray());
                    respond(200, JSON_CT, o.toString().getBytes("UTF-8"));
                } catch (Exception e) {
                    respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                }
                return;
            }
            if (method.equals("POST") && "/api/upreset/del".equals(path)) {
                String id = null;
                try {
                    id = new JSONObject(body).optString("id", "");
                } catch (Exception e) {
                    id = null;
                }
                if (id == null || id.length() == 0) {
                    respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                    return;
                }
                MyPresets.Item hit = MyPresets.find(id);
                boolean ok = MyPresets.delete(id);
                if (ok) WeakNet.I.log("config", "删除自定义场景：" + (hit != null ? hit.name : id));
                JSONObject o = new JSONObject();
                try {
                    o.put("ok", ok);
                    o.put("items", MyPresets.toJsonArray());
                } catch (Exception e) {
                    // ignore
                }
                respond(200, JSON_CT, o.toString().getBytes("UTF-8"));
                return;
            }
            if ("/api/targets".equals(path)) {
                // 可接管的目标 = 已安装的应用（与手机端「指定程序」共用 AppList，结果一致）
                JSONObject o = new JSONObject();
                o.put("type", "package");
                o.put("unit", "应用");
                JSONArray a = new JSONArray();
                java.util.List<AppList.Row> rows = AppList.load(ctx);
                try {
                    for (AppList.Row r : rows) {
                        JSONObject it = new JSONObject();
                        it.put("id", r.id);
                        it.put("name", r.name);
                        it.put("desc", r.id);
                        a.put(it);
                    }
                } catch (Exception e) {
                    WeakNet.I.log("error", "读取应用列表失败：" + e.getMessage());
                }
                WeakNet.I.log("config", "应用列表：" + rows.size() + " 个");
                o.put("items", a);
                respond(200, JSON_CT, o.toString().getBytes("UTF-8"));
                return;
            }
            if ("/api/scope".equals(path)) {
                if (method.equals("POST") && body != null && body.length() > 0) {
                    try {
                        JSONObject d = new JSONObject(body);
                        JSONArray arr = d.optJSONArray("list");
                        java.util.List<String> pkgs = new java.util.ArrayList<String>();
                        if (arr != null) {
                            for (int i = 0; i < arr.length(); i++) pkgs.add(arr.optString(i, ""));
                        }
                        String mode = WeakNet.I.setScope(d.optString("mode", "all"), pkgs);
                        if ("only".equals(mode)) {
                            WeakNet.I.log("config", "接管范围：仅 " + (pkgs.isEmpty()
                                    ? "（空名单=不接管任何 App）" : joinPkgs(pkgs)));
                        } else if ("exclude".equals(mode)) {
                            WeakNet.I.log("config", "接管范围：排除 " + (pkgs.isEmpty()
                                    ? "（空名单=全部接管）" : joinPkgs(pkgs)));
                        } else {
                            WeakNet.I.log("config", "接管范围：全部 App");
                        }
                        // 范围变了必须重建 tun：VpnService 的应用过滤只在 establish() 时生效
                        if (VpnNetService.active) rebuildVpn();
                    } catch (Exception e) {
                        respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                        return;
                    }
                }
                JSONObject o = new JSONObject();
                o.put("mode", WeakNet.I.scopeMode);
                o.put("supported", true);
                JSONArray a = new JSONArray();
                for (String p : new java.util.ArrayList<String>(WeakNet.I.scopePkgs)) a.put(p);
                o.put("list", a);
                respond(200, JSON_CT, o.toString().getBytes("UTF-8"));
                return;
            }
            // ---- 悬浮窗（限速飘窗）设置：显示哪些行 / 自定义文本 / 字号 / 透明度 ----
            if ("/api/float".equals(path)) {
                if (method.equals("POST") && body != null && body.length() > 0) {
                    try {
                        JSONObject d = new JSONObject(body);
                        java.util.List<String> lines = new java.util.ArrayList<String>();
                        JSONArray arr = d.optJSONArray("lines");
                        if (arr != null) {
                            for (int i = 0; i < arr.length(); i++) lines.add(arr.optString(i, ""));
                        }
                        FloatSettings.S s = FloatSettings.save(lines, d.optString("custom", ""),
                                (float) d.optDouble("alpha", 0.92), d.optInt("text_sp", 10));
                        FloatWindow.reload();      // 正在显示就按新设置重建，立刻看到效果
                        WeakNet.I.log("config", "悬浮窗设置：" + (s.lines.isEmpty()
                                ? "仅标题行" : s.lines.size() + " 行"));
                        JSONObject o = new JSONObject();
                        o.put("ok", true);
                        o.put("settings", FloatSettings.toJson(s));
                        o.put("shown", FloatWindow.isShown());
                        respond(200, JSON_CT, o.toString().getBytes("UTF-8"));
                    } catch (Exception e) {
                        respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                    }
                    return;
                }
                respond(200, JSON_CT, FloatSettings.describe(ctx).toString().getBytes("UTF-8"));
                return;
            }
            if (method.equals("POST") && "/api/float/show".equals(path)) {
                boolean want = false;
                try {
                    want = new JSONObject(body).optBoolean("show", false);
                } catch (Exception e) {
                    // ignore
                }
                boolean ok = true;
                String msg;
                if (want) {
                    if (!FloatWindow.canOverlay(ctx)) {
                        ok = false;
                        msg = "未授予悬浮窗权限";
                    } else {
                        FloatWindow.get(ctx).show();
                        ok = waitShown(true);      // show() 是 post 到主线程的，等它真的挂上
                        msg = ok ? "飘窗已显示" : "飘窗显示失败";
                    }
                } else {
                    FloatWindow.get(ctx).hide();
                    ok = waitShown(false);
                    msg = "飘窗已收起";
                }
                JSONObject o = new JSONObject();
                o.put("ok", ok);
                o.put("shown", FloatWindow.isShown());
                o.put("can_overlay", FloatWindow.canOverlay(ctx));
                o.put("msg", msg);
                respond(200, JSON_CT, o.toString().getBytes("UTF-8"));
                return;
            }
            if (method.equals("POST") && "/api/preset".equals(path)) {
                String key = null;
                try {
                    key = new JSONObject(body).optString("key");
                } catch (Exception e) {
                    // ignore
                }
                Profile p = buildKeyProfile(key);       // 内置预设 或 自定义场景（u:<id>）
                if (p == null) {
                    respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                    return;
                }
                // 与 App 界面同一条规矩：切场景只是选中，没在测试时不施加限速
                WeakNet.I.selectScene(p, key);
                WeakNet.I.log("config", "切换场景：" + p.name);
                respond(200, JSON_CT, "{\"ok\":true}".getBytes("UTF-8"));
                return;
            }
            if (method.equals("POST") && "/api/profile".equals(path)) {
                try {
                    JSONObject o = new JSONObject(body);
                    WeakNet.I.setProfile(Profile.custom(o), "custom");
                    WeakNet.I.log("config", "应用自定义参数");
                    respond(200, JSON_CT, "{\"ok\":true}".getBytes("UTF-8"));
                } catch (Exception e) {
                    respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                }
                return;
            }
            // 限速总开关：App 启动后默认关闭，用户点「开启限速」才生效
            if (method.equals("POST") && "/api/run".equals(path)) {
                boolean enable = false;
                try {
                    enable = new JSONObject(body).optBoolean("enable");
                } catch (Exception e) {
                    respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                    return;
                }
                Profile restore = null;
                if (enable) {
                    restore = buildKeyProfile(WeakNet.I.presetKey);   // 内置 / 自定义场景
                    // presetKey 为 "custom" 时 buildKeyProfile 认不出来（它不是内置 key，
                    // 也不是 u:<id>），此时当前 profile 就是用户刚设的那份参数，必须原样
                    // 保留。若回落到 lastProfile，用户把参数全调成 0（或只调了 stall 这类
                    // 不算 limits 的字段）再点开启，参数会被上一次的场景整个覆盖回去。
                    if (restore == null && "custom".equals(WeakNet.I.presetKey)) {
                        restore = WeakNet.I.profile;
                    }
                    if (restore == null) restore = WeakNet.I.lastProfile;
                    if (restore == null) restore = new Profile();
                }
                WeakNet.I.setLimitOn(enable, restore);
                WeakNet.I.log("config", enable
                        ? "开启限速：" + WeakNet.I.profile.name
                        : "停止限速（恢复直连，场景保留：" + WeakNet.I.presetKey + "）");
                respond(200, JSON_CT, ("{\"ok\":true,\"limit_on\":" + WeakNet.I.limitOn + "}")
                        .getBytes("UTF-8"));
                return;
            }
            // GET /api/run：查询当前开关状态（与 Windows 端同构，脚本/前端轮询用）
            if ("/api/run".equals(path)) {
                respond(200, JSON_CT, ("{\"ok\":true,\"limit_on\":" + WeakNet.I.limitOn + "}")
                        .getBytes("UTF-8"));
                return;
            }
            // 本机身份（与 Windows /api/me 同构）：Android 端用 /api/share 也能拿到同样信息
            if ("/api/me".equals(path)) {
                if (method.equals("POST") && body != null && body.length() > 0) {
                    try {
                        ShareNet.setUserName(new JSONObject(body).optString("name", ""));
                    } catch (Exception e) {
                        respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                        return;
                    }
                }
                JSONObject o = new JSONObject();
                o.put("name", ShareNet.getUserName());
                o.put("id", ShareNet.getSelfId());
                o.put("platform", "android");
                respond(200, JSON_CT, o.toString().getBytes("UTF-8"));
                return;
            }
            // 不限方法：页面若以 GET 发出也要能停（前端漏带 body 时会走 GET，
            // 只认 POST 就会返回 404，表现为「停止按钮点了没反应」）
            if ("/api/stop".equals(path)) {
                WeakNet.I.setLimitOn(false, null);
                WeakNet.I.log("config", "停止限速，恢复直连");
                respond(200, JSON_CT, "{\"ok\":true}".getBytes("UTF-8"));
                return;
            }
            if (method.equals("POST") && "/api/vpn".equals(path)) {
                boolean enable = false;
                try {
                    enable = new JSONObject(body).optBoolean("enable");
                } catch (Exception e) {
                    respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                    return;
                }
                android.content.Intent i = new android.content.Intent(ctx, VpnNetService.class);
                if (enable) {
                    // ACTION_START：显式启动意图，服务会清除 userStopped，保证停止后能再次接管
                    i.setAction(VpnNetService.ACTION_START);
                    if (android.os.Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i);
                    else ctx.startService(i);
                } else {
                    // 统一走服务内部的停止入口：stopService 停不掉被系统 bind 的 VpnService
                    VpnNetService.stop(ctx);
                }
                respond(200, JSON_CT, "{\"ok\":true}".getBytes("UTF-8"));
                return;
            }
            if (method.equals("POST") && "/api/listen".equals(path)) {
                String host = null;
                try {
                    host = new JSONObject(body).optString("host");
                } catch (Exception e) {
                    // ignore
                }
                if (!"127.0.0.1".equals(host) && !"0.0.0.0".equals(host)) {
                    respond(400, JSON_CT, "{\"error\":\"bad host\"}".getBytes("UTF-8"));
                    return;
                }
                try {
                    proxy.reopen(host);
                    Thread t = new Thread(proxy, "proxy");
                    t.setDaemon(true);
                    t.start();
                    WeakNet.I.log("config", "代理监听切换为 " + host + ":" + proxy.port());
                    respond(200, JSON_CT, "{\"ok\":true}".getBytes("UTF-8"));
                } catch (Exception e) {
                    respond(500, JSON_CT, ("{\"error\":\"" + e.getMessage() + "\"}").getBytes("UTF-8"));
                }
                return;
            }
            respond(404, "text/plain; charset=utf-8", "not found".getBytes("UTF-8"));
        }

        /**
         * key → Profile。内置预设走 Profile.build；用户自定义场景是 "u:<id>"，
         * 取出来后必须再拷贝一份 —— 直接复用保存的实例，应用时的原地改动会串到存档上。
         */
        private Profile buildKeyProfile(String key) {
            if (key == null) return null;
            if (key.startsWith("u:")) {
                MyPresets.Item it = MyPresets.find(key.substring(2));
                return it == null ? null : it.profile.copy();
            }
            if (!isValidPreset(key)) return null;
            return Profile.build(key);
        }

        private boolean isValidPreset(String key) {
            if (key == null) return false;
            for (String k : Profile.KEYS) {
                if (k.equals(key)) return true;
            }
            return false;
        }

        /**
         * 重建 VPN 通道。VpnService 的「哪些 App 走 tun」只在 establish() 时生效，
         * 因此改了接管范围后必须停掉再重建，否则新设置对当前连接无效。
         * 用 ACTION_START 重启：它会清除 userStopped，避免被上一轮的「不复活」逻辑挡住。
         */
        private void rebuildVpn() {
            VpnNetService.stop(ctx);
            new android.os.Handler(ctx.getMainLooper()).postDelayed(new Runnable() {
                public void run() {
                    try {
                        android.content.Intent i =
                                new android.content.Intent(ctx, VpnNetService.class);
                        i.setAction(VpnNetService.ACTION_START);
                        if (android.os.Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i);
                        else ctx.startService(i);
                    } catch (Exception e) {
                        WeakNet.I.log("error", "重建 VPN 失败：" + e.getMessage());
                    }
                }
            }, 700);      // 等上一个 tun 完全释放再建，避免端口/路由冲突
        }

        // 注：这是 CtrlServer 的内部类，Java 8 不允许内部类有 static 方法，故用实例方法
        private String joinPkgs(java.util.Collection<String> pkgs) {
            StringBuilder sb = new StringBuilder();
            int n = 0;
            for (String p : pkgs) {
                if (n > 0) sb.append(", ");
                if (n >= 4) { sb.append("…"); break; }
                sb.append(p);
                n++;
            }
            return sb.toString();
        }

        /**
         * 飘窗的 show()/hide() 都是 post 到主线程执行的，调用后不能立刻读到结果。
         * 这里等它真的挂上/摘掉再应答，否则页面会显示一个还没发生的旧状态。
         */
        private boolean waitShown(boolean want) {
            for (int i = 0; i < 20; i++) {
                if (FloatWindow.isShown() == want) return true;
                try {
                    Thread.sleep(50);
                } catch (InterruptedException e) {
                    return FloatWindow.isShown() == want;
                }
            }
            return FloatWindow.isShown() == want;
        }

        private JSONObject statusJson() throws Exception {
            try {
                return statusJsonInner();
            } catch (Throwable t) {
                // 曾经这里抛异常会被 run() 的 catch(Exception) 吞掉，客户端看到的是
                // 「连接被关闭、0 字节」，排查无从下手。现在至少把根因落到日志和响应里。
                try {
                    android.util.Log.e("WeakNet", "statusJson error", t);
                    WeakNet.I.log("error", "statusJson 异常：" + t);
                } catch (Exception ignore) {
                }
                JSONObject o = new JSONObject();
                o.put("error", String.valueOf(t));
                return o;
            }
        }

        private JSONObject statusJsonInner() throws Exception {
            JSONObject o = new JSONObject();
            o.put("profile", WeakNet.I.profile.toJson());
            o.put("preset_key", WeakNet.I.presetKey);
            o.put("limit_on", WeakNet.I.limitOn);
            o.put("gate_open", WeakNet.I.gateOpen);
            JSONObject listen = new JSONObject();
            listen.put("host", proxy.host());
            listen.put("port", proxy.port());
            o.put("listen", listen);
            o.put("lan_ip", WeakNet.I.lanIp());
            o.put("vpn_active", VpnNetService.active);
            // 接管范围（与 Windows 端同构，两端共用同一份控制台页面）
            JSONObject sc = new JSONObject();
            sc.put("mode", WeakNet.I.scopeMode);
            sc.put("supported", true);
            JSONArray sl = new JSONArray();
            for (String p : new java.util.ArrayList<String>(WeakNet.I.scopePkgs)) sl.put(p);
            sc.put("list", sl);
            o.put("scope", sc);
            // 悬浮窗（仅 Android 有）：桌面端没有这个字段，页面据此隐藏设置卡片
            JSONObject fw = new JSONObject();
            fw.put("supported", true);
            fw.put("shown", FloatWindow.isShown());
            fw.put("can_overlay", FloatWindow.canOverlay(ctx));
            o.put("float", fw);
            JSONObject st = new JSONObject();
            st.put("total", WeakNet.I.connTotal.get());
            st.put("active", WeakNet.I.connActive.get());
            st.put("up", WeakNet.I.bytesUp.get());
            st.put("down", WeakNet.I.bytesDown.get());
            st.put("uptime", (System.currentTimeMillis() - WeakNet.I.startedAt) / 1000);
            o.put("stats", st);

            JSONArray evs = new JSONArray();
            List<String[]> events = WeakNet.I.events;
            synchronized (events) {
                int n = events.size();
                int from = Math.max(0, n - 60);
                for (int i = n - 1; i >= from; i--) {
                    String[] e = events.get(i);
                    JSONObject j = new JSONObject();
                    j.put("t", e[0]);
                    j.put("type", e[1]);
                    j.put("msg", e[2]);
                    evs.put(j);
                }
            }
            o.put("events", evs);
            return o;
        }

        private void respond(int code, String ct, byte[] data) throws IOException {
            String reason = code == 200 ? "OK"
                    : (code == 404 ? "Not Found" : (code == 500 ? "Internal Server Error" : "Bad Request"));
            StringBuilder h = new StringBuilder();
            h.append("HTTP/1.1 ").append(code).append(" ").append(reason).append("\r\n");
            h.append("Content-Type: ").append(ct).append("\r\n");
            h.append("Content-Length: ").append(data.length).append("\r\n");
            h.append("Connection: close\r\n\r\n");
            OutputStream out = sock.getOutputStream();
            out.write(h.toString().getBytes("ISO-8859-1"));
            out.write(data);
            out.flush();
        }
    }

}
