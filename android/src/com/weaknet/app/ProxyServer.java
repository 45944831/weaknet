package com.weaknet.app;

import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URI;
import java.util.ArrayList;
import java.util.List;

/** 弱网代理服务器：CONNECT 隧道 + 普通 HTTP 转发，注入延迟/抖动/限速/丢包/断连/故障 */
public class ProxyServer implements Runnable {
    /** 头部阶段最多等 30s：空闲连接不能一直占着线程 */
    private static final int HEAD_TIMEOUT_MS = 30000;

    private ServerSocket server;
    private volatile String host;
    private final int port;
    private volatile boolean stopped = false;
    /** 绑定非回环地址时要求 Proxy-Authorization；回环上不需要（别的机器也连不进来） */
    private volatile String token = "";

    public ProxyServer(String host, int port) throws IOException {
        this.host = host;
        this.port = port;
        open();
    }

    private void open() throws IOException {
        if (!loopback(host) && token.length() == 0) token = newToken();
        server = new ServerSocket();
        server.setReuseAddress(true);
        server.bind(new InetSocketAddress(host, port));
    }

    static boolean loopback(String h) {
        return h == null || "localhost".equals(h) || "::1".equals(h)
                || h.startsWith("127.");
    }

    static String newToken() {
        String chars = "abcdefghijkmnpqrstuvwxyz23456789";
        java.security.SecureRandom r = new java.security.SecureRandom();
        StringBuilder sb = new StringBuilder(8);
        for (int i = 0; i < 8; i++) sb.append(chars.charAt(r.nextInt(chars.length())));
        return sb.toString();
    }

    public String token() {
        return token;
    }

    public void setToken(String t) {
        token = t == null ? "" : t;
    }

    public String host() {
        return host;
    }

    public int port() {
        return port;
    }

    public void stop() {
        stopped = true;
        try {
            if (server != null) server.close();
        } catch (IOException e) {
            // ignore
        }
    }

    public void reopen(String newHost) throws IOException {
        stop();
        stopped = false;
        host = newHost;
        // 换绑时同步换 token：回环不需要，局域网模式必须有一个，否则等于开放代理
        token = loopback(newHost) ? "" : newToken();
        open();
    }

    public void run() {
        while (!stopped) {
            try {
                Socket c = server.accept();
                Thread t = new Thread(new Conn(c), "conn");
                t.setDaemon(true);
                t.start();
            } catch (IOException e) {
                if (stopped) break;
            }
        }
    }

    private static String reason(int code) {
        switch (code) {
            case 400: return "Bad Request";
            case 407: return "Proxy Authentication Required";
            case 408: return "Request Timeout";
            case 413: return "Payload Too Large";
            case 431: return "Request Header Fields Too Large";
            default: return "Bad Request";
        }
    }

    /** Proxy-Authorization: 认 raw token、Basic token、Basic base64(user:token) 三种写法 */
    static boolean authorized(String value, String token) {
        if (value == null || token.length() == 0) return false;
        String v = value.trim();
        if (v.equals(token)) return true;
        if (!v.regionMatches(true, 0, "Basic ", 0, 6)) return false;
        String raw = v.substring(6).trim();
        if (raw.equals(token)) return true;
        try {
            String dec = new String(android.util.Base64.decode(raw, android.util.Base64.NO_WRAP), "UTF-8");
            if (dec.equals(token)) return true;
            int c = dec.indexOf(':');
            return c >= 0 && dec.substring(c + 1).equals(token);
        } catch (Exception e) {
            return false;
        }
    }

    class Conn implements Runnable {
        private final Socket client;

        Conn(Socket c) {
            client = c;
        }

        public void run() {
            WeakNet.I.connTotal.incrementAndGet();
            WeakNet.I.connActive.incrementAndGet();
            ConnCtx ctx = new ConnCtx(client);
            try {
                client.setSoTimeout(HEAD_TIMEOUT_MS);
                handle(ctx);
            } catch (Exception e) {
                // 连接异常，忽略
            } finally {
                WeakNet.I.connActive.decrementAndGet();
                ctx.cleanup();
            }
        }

        private void handle(ConnCtx ctx) throws Exception {
            if (!WeakNet.I.gateOpen) {
                WeakNet.I.log("flap", "黑窗期：拒绝新连接");
                ctx.cleanup();
                return;
            }
            BufferedInputStream in = new BufferedInputStream(client.getInputStream());
            HttpIn.Req req;
            try {
                req = HttpIn.readHead(in);
            } catch (HttpIn.Bad bad) {
                Util.writeAscii(client, "HTTP/1.1 " + bad.code + " " + reason(bad.code)
                        + "\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                WeakNet.I.log("error", "代理请求头被拒（" + bad.getMessage() + "）");
                return;
            } catch (java.net.SocketTimeoutException e) {
                Util.writeAscii(client, "HTTP/1.1 408 Request Timeout\r\nContent-Length: 0\r\n"
                        + "Connection: close\r\n\r\n");
                return;
            }
            if (req == null) return;
            if (!loopback(host) && token.length() > 0
                    && !authorized(req.header("proxy-authorization"), token)) {
                Util.writeAscii(client, "HTTP/1.1 407 Proxy Authentication Required\r\n"
                        + "Proxy-Authenticate: Basic realm=\"WeakNet\"\r\n"
                        + "Content-Length: 0\r\nConnection: close\r\n\r\n");
                WeakNet.I.log("reject", "局域网代理拒绝未授权请求");
                return;
            }
            // 头部读完，后面是长连接/隧道：清掉超时，交给空闲回收
            client.setSoTimeout(0);
            String method = req.method;
            String target = req.path;
            List<String> headers = req.head.subList(1, req.head.size());

            Profile p = WeakNet.I.profile;

            // 故障模拟（每连接判定）
            if (p.rejectPct > 0 && Math.random() * 100 < p.rejectPct) {
                Util.writeAscii(client, "HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                WeakNet.I.log("reject", "模拟拒连（完全断网）：" + Util.safe(target));
                return;
            }
            if (p.timeoutPct > 0 && Math.random() * 100 < p.timeoutPct) {
                WeakNet.I.log("timeout", "模拟黑洞（无响应）：" + Util.safe(target));
                // 挂起期间用户切换场景 / 点停止 → 立即放行并按新参数继续处理
                if (!WeakNet.I.sleepInterruptible(600000)) return;
                WeakNet.I.log("config", "黑洞解除，恢复处理：" + Util.safe(target));
                p = WeakNet.I.profile;
            }
            // DNS 故障只影响「需要解析域名」的连接；IP 字面量（含 HTTPDNS / 固定 IP 的 App）
            // 不经过 DNS，与 VpnNetService 路径（只对 UDP 53 生效）保持一致
            if (p.dnsFailPct > 0 && !isIpLiteral(target)
                    && Math.random() * 100 < p.dnsFailPct) {
                Util.writeAscii(client, "HTTP/1.1 502 DNS resolution failed (simulated)\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                WeakNet.I.log("dns", "模拟 DNS 故障：" + Util.safe(target));
                return;
            }

            if ("CONNECT".equals(method)) {
                doConnect(ctx, target, in);
            } else if (target.startsWith("http://") || target.startsWith("https://")) {
                doHttp(ctx, method, target, headers, in);
            }
        }

        private void doConnect(ConnCtx ctx, String target, BufferedInputStream in) throws Exception {
            String host;
            int port;
            int i = target.indexOf(':');
            if (i > 0) {
                host = target.substring(0, i);
                port = Integer.parseInt(target.substring(i + 1).trim());
            } else {
                host = target;
                port = 443;
            }
            Socket up = new Socket();
            try {
                up.connect(new InetSocketAddress(host, port), 15000);
            } catch (Exception e) {
                Util.writeAscii(client, "HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                WeakNet.I.log("error", host + ":" + port + " 上游连接失败");
                return;
            }
            ctx.attach(up, host + ":" + port);
            WeakNet.I.register(client);
            WeakNet.I.register(up);
            WeakNet.I.log("connect", "建立隧道 " + host + ":" + port);
            Util.writeAscii(client, "HTTP/1.1 200 Connection Established\r\n\r\n");
            pump(ctx, in, up);
        }

        private void doHttp(ConnCtx ctx, String method, String target, List<String> headers, BufferedInputStream in) throws Exception {
            URI u = new URI(target);
            String host = u.getHost();
            int port = u.getPort() > 0 ? u.getPort() : ("https".equals(u.getScheme()) ? 443 : 80);
            String path = (u.getRawPath() == null || u.getRawPath().length() == 0) ? "/" : u.getRawPath();
            if (u.getRawQuery() != null) path = path + "?" + u.getRawQuery();

            Socket up = new Socket();
            try {
                up.connect(new InetSocketAddress(host, port), 15000);
            } catch (Exception e) {
                Util.writeAscii(client, "HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                WeakNet.I.log("error", host + ":" + port + " 上游连接失败");
                return;
            }
            ctx.attach(up, host + ":" + port);
            WeakNet.I.register(client);
            WeakNet.I.register(up);

            StringBuilder req = new StringBuilder();
            req.append(method).append(" ").append(path).append(" HTTP/1.1\r\n");
            for (String h : headers) {
                String lower = h.toLowerCase();
                if (lower.startsWith("proxy-connection") || lower.startsWith("connection")
                        || lower.startsWith("keep-alive")) continue;
                req.append(h).append("\r\n");
            }
            req.append("Connection: close\r\n\r\n");
            OutputStream uout = up.getOutputStream();
            // 请求头也要吃上行延迟：请求是逐行 read 的，读到的瞬间就离开管道，
            // 不经过 DelayLine——正文为空的 GET 会只能吃到单向延迟，RTT 少一半。
            Pipe.writeDelayed(uout, req.toString().getBytes("ISO-8859-1"), true);
            WeakNet.I.log("connect", "HTTP 代理 " + host + ":" + port + path);
            pump(ctx, in, up);
        }

        private void pump(ConnCtx ctx, BufferedInputStream cin, Socket up) throws Exception {
            Profile p = WeakNet.I.profile;
            final OutputStream cout = client.getOutputStream();
            final InputStream uin = up.getInputStream();
            final OutputStream uout = up.getOutputStream();
            // 用整机共享的令牌桶：① 并发连接不会叠加超速；② 切场景/停止时 WeakNet.setProfile
            // 会原地更新速率，已在途的连接立即跟上（每条连接 new 一个桶会一直按旧速率跑完）
            final Pipe.Bucket upBucket = WeakNet.I.upBucket;
            final Pipe.Bucket downBucket = WeakNet.I.downBucket;

            // EOF 收尾用 shutdownOutput 只关本方向（Java 里 close 输出流会连整个
            // socket 一起关掉）。客户端「发完请求就 shutdown 写端」时，上行读到 EOF
            // 不能把下行一起掐死——否则响应一个字节都出不去。
            final Socket fUp = up;
            final Socket fClient = client;
            Thread t1 = new Thread(new Runnable() {
                public void run() {
                    Pipe.run(cin, uout, upBucket, true, ctx, new Runnable() {
                        public void run() {
                            try { fUp.shutdownOutput(); }
                            catch (Exception e) { try { fUp.close(); } catch (Exception e2) {} }
                        }
                    });
                }
            }, "pipe-up");
            Thread t2 = new Thread(new Runnable() {
                public void run() {
                    Pipe.run(uin, cout, downBucket, false, ctx, new Runnable() {
                        public void run() {
                            try { fClient.shutdownOutput(); }
                            catch (Exception e) { try { fClient.close(); } catch (Exception e2) {} }
                        }
                    });
                }
            }, "pipe-down");
            t1.setDaemon(true);
            t2.setDaemon(true);
            t1.start();
            t2.start();
            t1.join();
            // 必须等下行彻底跑完再收尾。原来这里是 t2.join(2000)：客户端一发完请求
            // 就半关闭（curl / nc / 带 body 的 POST 都这样），慢档位下大响应要几十秒，
            // 2s 后 cleanup() 直接掐断连接，响应尾部整段丢掉（下载总是差最后一段）。
            // 下行会在上游 EOF 或 socket 出错时自然结束，300s 只是兜底。
            t2.join(300000);
            ctx.cleanup();
        }
    }

    /** 请求目标是否为 IP 字面量（v4/v6）。IP 直连不经过 DNS */
    static boolean isIpLiteral(String target) {
        String t = target == null ? "" : target;
        int sl = t.indexOf("://");
        if (sl >= 0) t = t.substring(sl + 3);
        int sp = t.indexOf('/');
        if (sp >= 0) t = t.substring(0, sp);
        if (t.startsWith("[")) {                    // IPv6 [::1]:443
            int e = t.indexOf(']');
            return e > 1;
        }
        int c = t.lastIndexOf(':');
        if (c > 0) t = t.substring(0, c);
        if (t.length() == 0) return false;
        boolean digits = true;
        for (int i = 0; i < t.length(); i++) {
            char ch = t.charAt(i);
            if (!(Character.isDigit(ch) || ch == '.' || ch == ':' || (ch >= 'a' && ch <= 'f')
                    || (ch >= 'A' && ch <= 'F'))) {
                digits = false;
                break;
            }
        }
        return digits;
    }
}
