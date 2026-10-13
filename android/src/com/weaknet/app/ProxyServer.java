package com.weaknet.app;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
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
    private ServerSocket server;
    private volatile String host;
    private final int port;
    private volatile boolean stopped = false;

    public ProxyServer(String host, int port) throws IOException {
        this.host = host;
        this.port = port;
        open();
    }

    private void open() throws IOException {
        server = new ServerSocket();
        server.setReuseAddress(true);
        server.bind(new InetSocketAddress(host, port));
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
            String line = Util.readLine(in);
            if (line == null) return;
            String[] parts = line.split(" ");
            if (parts.length < 2) return;
            String method = parts[0].toUpperCase();
            String target = parts[1];

            List<String> headers = new ArrayList<String>();
            while (true) {
                String h = Util.readLine(in);
                if (h == null || h.length() == 0) break;
                headers.add(h);
            }

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
            if (p.dnsFailPct > 0 && Math.random() * 100 < p.dnsFailPct) {
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
            uout.write(req.toString().getBytes("ISO-8859-1"));
            uout.flush();
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

            Thread t1 = new Thread(new Runnable() {
                public void run() {
                    Pipe.run(cin, uout, upBucket, true, ctx);
                }
            }, "pipe-up");
            Thread t2 = new Thread(new Runnable() {
                public void run() {
                    Pipe.run(uin, cout, downBucket, false, ctx);
                }
            }, "pipe-down");
            t1.setDaemon(true);
            t2.setDaemon(true);
            t1.start();
            t2.start();
            t1.join();
            t2.join(2000);
            ctx.cleanup();
        }
    }
}
