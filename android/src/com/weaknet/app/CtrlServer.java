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
    /** 推送独立成池：同步 HTTP 最长要等十几秒，不能占着控制池把状态轮询全堵住 */
    private final java.util.concurrent.ExecutorService pushPool =
            Executors.newFixedThreadPool(2, new java.util.concurrent.ThreadFactory() {
                public Thread newThread(Runnable r) {
                    Thread t = new Thread(r, "share-push");
                    t.setDaemon(true);
                    return t;
                }
            });

    public CtrlServer(int port, ProxyServer proxy, android.content.Context ctx) {
        this.port = port;
        this.proxy = proxy;
        this.ctx = ctx;
        MyPresets.bind(ctx);        // 自定义场景要落 SharedPreferences，先把上下文备好
        MyCombos.bind(ctx);         // 弱网组合同理（同一个 weaknet 存储，不同 key）
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
        pushPool.shutdownNow();
    }

    /** JSON 数组 → 字符串列表（没有就给个空列表，省得每个路由都判一遍 null） */
    private static List<String> jsonList(JSONArray a) {
        List<String> out = new ArrayList<String>();
        if (a == null) return out;
        for (int i = 0; i < a.length(); i++) out.add(a.optString(i, ""));
        return out;
    }

    class Handler implements Runnable {
        private final Socket sock;
        /** 异步响应（推送不能占着 4 线程的控制池等对方回包） */
        private volatile boolean async = false;

        Handler(Socket s) {
            sock = s;
        }

        public void run() {
            try {
                sock.setSoTimeout(30000);
                BufferedInputStream in = new BufferedInputStream(sock.getInputStream());
                HttpIn.Req req;
                try {
                    req = HttpIn.read(in, HttpIn.MAX_BODY_CTRL);
                } catch (HttpIn.Bad bad) {
                    respond(bad.code, JSON_CT, ("{\"error\":\"" + bad.getMessage() + "\"}")
                            .getBytes("UTF-8"));
                    return;
                }
                if (req == null) return;
                String path = req.path;
                int q = path.indexOf('?');
                if (q >= 0) path = path.substring(0, q);
                route(req.method, path, req.body == null ? "" : req.body);
            } catch (Exception e) {
                // ignore
            } finally {
                if (async) {
                    // 由异步任务负责回包并关连接
                } else {
                    finish();
                }
            }
        }

        private void finish() {
            try {
                sock.close();
            } catch (Exception e) {
                // ignore
            }
        }

        private void route(String method, String path, String body) throws Exception {
            // 控制接口只接受 GET / POST。以前完全不看方法，DELETE /api/status 也会返回 200，
            // 脚本里写错方法时会静默「成功」，很难发现。
            if (!"GET".equals(method) && !"POST".equals(method) && path.startsWith("/api/")) {
                respond(405, JSON_CT, "{\"error\":\"method not allowed\"}".getBytes("UTF-8"));
                return;
            }
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
            // 与 Windows 端同构：参数表来自 spec/spec.json（安卓侧是生成的 SpecGen），
            // spec_hash 用来断言两端确实载入了同一份规格。
            if ("/api/params".equals(path)) {
                respond(200, JSON_CT, SpecGen.paramsJson().toString().getBytes("UTF-8"));
                return;
            }
            // ---- 环境共享：只读状态，便于脚本验证「手机也能被 PC 发现 / 收到推送」----
            // 正式路径是 /api/share/status：旧版这里任意方法都回 200，而共享端口 8900 上的
            // POST /api/share 是「提交推送」，同名不同义 —— 脚本照文档写会静默成功却什么也没推过去。
            // GET /api/share 保留一个版本作为别名，POST 明确 405 并指向 /api/push。
            if ("/api/share/status".equals(path) || "/api/share".equals(path)) {
                if ("POST".equals(method)) {
                    respond(405, JSON_CT,
                            "{\"error\":\"method not allowed, use POST /api/push\"}"
                                    .getBytes("UTF-8"));
                    return;
                }
                respond(200, JSON_CT, ShareNet.statusJson().toString().getBytes("UTF-8"));
                return;
            }
            if ("/api/share/pin".equals(path)) {
                // 与 Windows 端同名同语义：GET 看状态，POST 改 PIN / 旧版兼容开关
                if ("POST".equals(method)) {
                    try {
                        JSONObject d = new JSONObject(body);
                        JSONObject r;
                        if (d.has("allow_legacy")) {
                            r = ShareNet.setAllowLegacy(d.optBoolean("allow_legacy"));
                        } else {
                            r = ShareNet.setPin(d.optString("pin", ""), d.optBoolean("refresh"));
                        }
                        respond(r.optBoolean("ok") ? 200 : 400, JSON_CT,
                                r.toString().getBytes("UTF-8"));
                    } catch (Exception e) {
                        respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                    }
                } else {
                    respond(200, JSON_CT, ShareNet.pinStatusJson().toString().getBytes("UTF-8"));
                }
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
            if ("POST".equals(method) && ("/api/share/push".equals(path) || "/api/push".equals(path))) {
                // 给脚本用：{peer: "<id>" | {ip, share} | "ip:port", ids: [...], combo_ids: [...]}
                // /api/push 与 Windows 同名同义；/api/share/push 是旧路径，保留一个版本
                final ShareNet.Peer fp;
                final List<String> flist;
                final List<String> fclist;
                try {
                    JSONObject d = new JSONObject(body);
                    fp = resolvePeer(d);
                    flist = jsonList(d.optJSONArray("ids"));
                    fclist = jsonList(d.optJSONArray("combo_ids"));
                } catch (Exception e) {
                    respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                    return;
                }
                if (fp == null) {
                    respond(400, JSON_CT, "{\"error\":\"bad peer\"}".getBytes("UTF-8"));
                    return;
                }
                // 推送是同步 HTTP（对方 6s 连/读超时），放到独立池里跑；
                // 本请求先不关 socket，由任务回包后关闭 —— 否则控制池的 4 个线程会被堵死
                async = true;
                pushPool.execute(new Runnable() {
                    public void run() {
                        JSONObject o = new JSONObject();
                        try {
                            ShareNet.PushResult pr = ShareNet.push(fp, flist, fclist);
                            o.put("ok", pr.ok);
                            o.put("msg", pr.msg);
                            JSONArray acc = new JSONArray();
                            for (String s : pr.accepted) acc.put(s);
                            o.put("accepted", acc);
                            JSONArray pen = new JSONArray();
                            for (String s : pr.pending) pen.put(s);
                            o.put("pending", pen);
                        } catch (Exception e) {
                            try {
                                o.put("ok", false);
                                o.put("msg", "推送失败：" + e.getMessage());
                            } catch (Exception ignore) {
                            }
                        }
                        try {
                            respond(200, JSON_CT, o.toString().getBytes("UTF-8"));
                        } catch (Exception e) {
                            // 对方已断开：结果已进日志
                        } finally {
                            finish();
                        }
                    }
                });
                return;
            }
            if ("GET".equals(method) && ("/api/share/inbox".equals(path) || "/api/inbox".equals(path))) {
                // 收件箱列表：kind 区分「环境」和「组合」，与桌面端 /api/inbox 同构
                try {
                    JSONArray arr = new JSONArray();
                    long now = System.currentTimeMillis();
                    for (ShareNet.InboxItem it : ShareNet.inboxList()) {
                        JSONObject o = new JSONObject();
                        o.put("id", it.id);
                        o.put("from", it.from);
                        o.put("name", it.name);
                        o.put("kind", it.kind);
                        o.put("ago", (int) ((now - it.at) / 1000));
                        if ("combo".equals(it.kind)) {
                            o.put("combo", it.combo);
                        } else {
                            o.put("profile", it.profile);
                        }
                        arr.put(o);
                    }
                    JSONObject o = new JSONObject();
                    o.put("items", arr);
                    respond(200, JSON_CT, o.toString().getBytes("UTF-8"));
                } catch (Exception e) {
                    respond(500, JSON_CT, "{\"error\":\"inbox failed\"}".getBytes("UTF-8"));
                }
                return;
            }
            if ("POST".equals(method) && ("/api/share/inbox".equals(path)
                    || "/api/inbox/resolve".equals(path))) {
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
            if ("/api/peers".equals(path)) {
                // 与 Windows /api/peers 同构：items + self + discover
                JSONObject s = ShareNet.statusJson();
                JSONArray items = new JSONArray();
                JSONArray src = s.optJSONArray("peers");
                if (src != null) {
                    for (int i = 0; i < src.length(); i++) {
                        JSONObject p = src.optJSONObject(i);
                        if (p == null) continue;
                        JSONObject it = new JSONObject();
                        it.put("id", p.optString("id", ""));
                        it.put("name", p.optString("name", ""));
                        it.put("ip", p.optString("ip", ""));
                        it.put("platform", p.optString("platform", ""));
                        it.put("share", p.optInt("share", 0));
                        it.put("ver", p.optInt("ver", 1));
                        it.put("pin_ok", p.optBoolean("pin_ok", false));
                        it.put("ago", p.optInt("ago", 0));
                        items.put(it);
                    }
                }
                JSONObject o = new JSONObject();
                o.put("items", items);
                JSONObject self = new JSONObject();
                self.put("name", s.optString("name", ""));
                self.put("id", s.optString("id", ""));
                self.put("share", s.optInt("share_port", 0));
                self.put("lan_ip", s.optString("lan_ip", ""));
                o.put("self", self);
                o.put("discover", s.optBoolean("discover", false));
                respond(200, JSON_CT, o.toString().getBytes("UTF-8"));
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
                // "c:<id>" = 弱网组合：与 App 里点组合按钮走同一条路
                // （已开限速就立刻按序播放，没开只选中，不替用户偷偷开始限速）
                if (key != null && key.startsWith(MyCombos.KEY_PREFIX)) {
                    MyCombos.Item ci = MyCombos.find(key.substring(MyCombos.KEY_PREFIX.length()));
                    if (ci == null) {
                        respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                        return;
                    }
                    boolean applied = WeakNet.I.selectCombo(ci);
                    respond(200, JSON_CT, ("{\"ok\":true,\"applied\":" + applied
                            + ",\"kind\":\"combo\"}").getBytes("UTF-8"));
                    return;
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
            // GET /api/combos：弱网组合列表（含每段 steps 与一行摘要）
            if ("/api/combos".equals(path)) {
                respond(200, JSON_CT, MyCombos.listJson().toString().getBytes("UTF-8"));
                return;
            }

            // POST /api/combo：新增或覆盖一支组合 {id?, name, loop, steps:[{key,minutes,seconds}]}
            if (method.equals("POST") && "/api/combo".equals(path)) {
                try {
                    JSONObject o = new JSONObject(body);
                    boolean loop = o.optBoolean("loop", false);
                    List<MyCombos.Step> steps = new ArrayList<MyCombos.Step>();
                    JSONArray ss = o.optJSONArray("steps");
                    if (ss != null) {
                        for (int i = 0; i < ss.length(); i++) {
                            JSONObject so = ss.optJSONObject(i);
                            if (so == null) continue;
                            MyCombos.Step s = new MyCombos.Step();
                            s.key = so.optString("key", "");
                            s.minutes = so.optInt("minutes", 0);
                            s.seconds = so.optInt("seconds", 0);
                            steps.add(s);
                        }
                    }
                    MyCombos.Item it = MyCombos.save(
                            o.optString("id", null), o.optString("name", ""), loop, steps);
                    if (it == null) {
                        // 一段都留不下：不当成保存成功，否则调用方会以为库里多了一支空组合
                        respond(400, JSON_CT,
                                "{\"error\":\"没有一段能用的场景\"}".getBytes("UTF-8"));
                        return;
                    }
                    JSONObject r = new JSONObject();
                    r.put("ok", true);
                    r.put("id", it.id);
                    r.put("name", it.name);
                    r.put("steps", it.steps.size());
                    respond(200, JSON_CT, r.toString().getBytes("UTF-8"));
                } catch (Exception e) {
                    respond(500, JSON_CT, ("{\"error\":\"" + e.getMessage() + "\"}")
                            .getBytes("UTF-8"));
                }
                return;
            }

            // POST /api/combo/delete：{id}。删的这支万一正在播，立刻停，别让它继续改参数
            if (method.equals("POST") && "/api/combo/delete".equals(path)) {
                String cid = null;
                try {
                    cid = new JSONObject(body).optString("id");
                } catch (Exception e) {
                    // ignore
                }
                // 删的这支是不是「当前这支」：正在播放（comboId）或被选中（presetKey）
                // 都要复位，否则 presetKey 悬挂指向已删除的组合、弱网参数无限期残留。
                boolean active = (cid != null && cid.equals(WeakNet.I.comboId))
                        || (cid != null && ("c:" + cid).equals(WeakNet.I.presetKey));
                boolean ok = MyCombos.delete(cid);
                if (active) {
                    // 与界面删除（ComboSheet.doDelete）同一条路径：停播放 + 回直连 + 关总开关
                    WeakNet.I.stopCombo();
                    WeakNet.I.setProfile(new Profile(), "direct");
                    WeakNet.I.limitOn = false;
                    WeakNet.I.log("config", "删除组合后切回直连");
                }
                respond(200, JSON_CT, ("{\"ok\":" + ok + "}").getBytes("UTF-8"));
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
                // 选中的是弱网组合时，上面那支静态 restore 不算数：
                // 让播放序列自己按 steps 把第一段顶上去
                boolean combo = enable && WeakNet.I.resumeIfCombo();
                WeakNet.I.log("config", enable
                        ? (combo ? "开启限速，开始播放组合：" + WeakNet.I.comboProgress()
                                 : "开启限速：" + WeakNet.I.profile.name)
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
                JSONObject o = meJson();
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
                    JSONObject o = new JSONObject();
                    o.put("ok", true);
                    o.put("host", host);
                    o.put("token", proxy.token());
                    o.put("warn", "0.0.0.0".equals(host)
                            ? "局域网代理已开启：其它设备需要 Proxy-Authorization 才能使用" : "");
                    WeakNet.I.log("config", "代理监听切换为 " + host + ":" + proxy.port()
                            + ("0.0.0.0".equals(host) ? "（局域网代理已开启，需要 token）" : ""));
                    respond(200, JSON_CT, o.toString().getBytes("UTF-8"));
                } catch (Exception e) {
                    respond(500, JSON_CT, ("{\"error\":\"" + e.getMessage() + "\"}").getBytes("UTF-8"));
                }
                return;
            }
            // ---- 平台专属能力：系统代理设置只有桌面端有 ----
            if ("/api/sysproxy".equals(path)) {
                respond(501, JSON_CT,
                        "{\"error\":\"not supported on this platform\"}".getBytes("UTF-8"));
                return;
            }
            respond(404, JSON_CT, "{\"error\":\"not found\"}".getBytes("UTF-8"));
        }

        /**
         * 解析推送目标：对端 id（界面路径）/ "ip:port" / {ip, share}（脚本路径，与 Windows 一致）。
         * 端口越界或写法认不出来返回 null，由调用方回 400 bad peer。
         */
        private ShareNet.Peer resolvePeer(JSONObject d) {
            Object raw = d.opt("peer");
            if (raw instanceof JSONObject) {
                JSONObject o = (JSONObject) raw;
                String ip = o.optString("ip", "").trim();
                int share = o.optInt("share", ShareNet.SHARE_PORT);
                if (ip.length() == 0 || share < 1 || share > 65535) return null;
                ShareNet.Peer p = new ShareNet.Peer();
                p.id = o.optString("id", "");
                p.ip = ip;
                p.name = o.optString("name", ip);
                p.share = share;
                return p;
            }
            String s = raw == null ? "" : String.valueOf(raw).trim();
            ShareNet.Peer hit = ShareNet.findPeer(s);
            if (hit != null) return hit;
            int colon = s.lastIndexOf(':');
            if (colon <= 0) return null;
            String ip = s.substring(0, colon).trim();
            int share;
            try {
                share = Integer.parseInt(s.substring(colon + 1).trim());
            } catch (NumberFormatException e) {
                return null;
            }
            if (ip.length() == 0 || share < 1 || share > 65535) return null;
            ShareNet.Peer p = new ShareNet.Peer();
            p.ip = ip;
            p.name = ip;
            p.share = share;
            return p;
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
            // 版本与身份：与 Windows /api/status 同构。缺这三个键时，按 status.version
            // 做兼容判断的脚本在 Android 上只能拿到 undefined，双端「同构」名不副实。
            android.content.pm.PackageInfo pi = ctx.getPackageManager()
                    .getPackageInfo(ctx.getPackageName(), 0);
            o.put("version", pi.versionName);
            o.put("build", pi.versionCode);
            o.put("me", meJson());
            o.put("spec_hash", SpecGen.SPEC_HASH);
            o.put("profile", WeakNet.I.profile.toJson());
            o.put("preset_key", WeakNet.I.presetKey);
            o.put("limit_on", WeakNet.I.limitOn);
            o.put("gate_open", WeakNet.I.gateOpen);
            JSONObject listen = new JSONObject();
            listen.put("host", proxy.host());
            listen.put("port", proxy.port());
            listen.put("lan", !ProxyServer.loopback(proxy.host()));
            listen.put("token", proxy.token());
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

        /** 本机身份：/api/me 与 /api/status.me 共用一份，避免两处各写一套字段。 */
        private JSONObject meJson() throws Exception {
            JSONObject o = new JSONObject();
            JSONObject st = ShareNet.statusJson();
            o.put("name", ShareNet.getUserName());
            o.put("id", ShareNet.getSelfId());
            o.put("platform", "android");
            o.put("share", st.optInt("share_port", 0));
            o.put("discover", st.optBoolean("discover", false));
            o.put("lan_ip", st.optString("lan_ip", ""));
            o.put("share_ver", ShareNet.SHARE_VER);
            o.put("pin_len", ShareNet.PIN_LEN);
            o.put("allow_legacy", ShareNet.isAllowLegacy());
            return o;
        }

        private void respond(int code, String ct, byte[] data) throws IOException {
            String reason;
            switch (code) {
                case 200: reason = "OK"; break;
                case 400: reason = "Bad Request"; break;
                case 403: reason = "Forbidden"; break;
                case 404: reason = "Not Found"; break;
                case 405: reason = "Method Not Allowed"; break;
                case 408: reason = "Request Timeout"; break;
                case 409: reason = "Conflict"; break;
                case 413: reason = "Payload Too Large"; break;
                case 431: reason = "Request Header Fields Too Large"; break;
                case 500: reason = "Internal Server Error"; break;
                case 501: reason = "Not Implemented"; break;
                default: reason = "Bad Request"; break;
            }
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
