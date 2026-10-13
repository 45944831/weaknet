package com.weaknet.app;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.List;

/** 控制台 HTTP 服务：提供页面与 /api/*（与桌面版接口完全一致） */
public class CtrlServer implements Runnable {
    private static final String JSON_CT = "application/json; charset=utf-8";

    private final int port;
    private final ProxyServer proxy;
    private final File dashFile;
    private final android.content.Context ctx;
    private ServerSocket server;
    private volatile boolean stopped = false;

    public CtrlServer(int port, ProxyServer proxy, File dashFile, android.content.Context ctx) {
        this.port = port;
        this.proxy = proxy;
        this.dashFile = dashFile;
        this.ctx = ctx;
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
                Thread t = new Thread(new Handler(c), "ctrl-req");
                t.setDaemon(true);
                t.start();
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
            if ("/".equals(path) || "/index.html".equals(path)) {
                respond(200, "text/html; charset=utf-8", readFile(dashFile));
                return;
            }
            if ("/api/status".equals(path)) {
                respond(200, JSON_CT, statusJson().toString().getBytes("UTF-8"));
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
            if (method.equals("POST") && "/api/preset".equals(path)) {
                String key = null;
                try {
                    key = new JSONObject(body).optString("key");
                } catch (Exception e) {
                    // ignore
                }
                if (!isValidPreset(key)) {
                    respond(400, JSON_CT, "{\"error\":\"bad request\"}".getBytes("UTF-8"));
                    return;
                }
                Profile p = Profile.build(key);
                WeakNet.I.setProfile(p, key);
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
            // 不限方法：页面若以 GET 发出也要能停（前端漏带 body 时会走 GET，
            // 只认 POST 就会返回 404，表现为「停止按钮点了没反应」）
            if ("/api/stop".equals(path)) {
                WeakNet.I.setProfile(new Profile(), "direct");
                WeakNet.I.log("config", "停止模拟，恢复直连");
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
                    VpnNetService.userStopped = false;
                    if (android.os.Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i);
                    else ctx.startService(i);
                } else {
                    // ACTION_STOP：让服务自行 stopSelf，避免 START_STICKY 把它重启
                    i.setAction(VpnNetService.ACTION_STOP);
                    if (android.os.Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i);
                    else ctx.startService(i);
                    try {
                        ctx.stopService(new android.content.Intent(ctx, VpnNetService.class));
                    } catch (Exception e) {
                        // ignore
                    }
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

        private boolean isValidPreset(String key) {
            if (key == null) return false;
            for (String k : Profile.KEYS) {
                if (k.equals(key)) return true;
            }
            return false;
        }

        private JSONObject statusJson() throws Exception {
            JSONObject o = new JSONObject();
            o.put("profile", WeakNet.I.profile.toJson());
            o.put("preset_key", WeakNet.I.presetKey);
            o.put("gate_open", WeakNet.I.gateOpen);
            JSONObject listen = new JSONObject();
            listen.put("host", proxy.host());
            listen.put("port", proxy.port());
            o.put("listen", listen);
            o.put("lan_ip", WeakNet.I.lanIp());
            o.put("vpn_active", VpnNetService.active);
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

    private static byte[] readFile(File f) {
        try {
            FileInputStream in = new FileInputStream(f);
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
            in.close();
            return bo.toByteArray();
        } catch (Exception e) {
            return "<html><body>控制台页面缺失</body></html>".getBytes();
        }
    }
}
