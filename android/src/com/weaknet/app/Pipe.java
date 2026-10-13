package com.weaknet.app;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;

/** 管道：延迟 + 抖动 + 令牌桶限速 + 丢包卡顿 + 随机断连 */
public class Pipe {
    public static void run(InputStream in, OutputStream out, Bucket bucket, boolean up, ConnCtx ctx) {
        try {
            Profile p = WeakNet.I.profile;
            int base = up ? p.latencyUpMs : p.latencyDownMs;
            int jit = p.jitterMs;
            long d = base + (jit > 0 ? (long) ((Math.random() * 2 - 1) * jit) : 0);
            if (d > 0) Thread.sleep(d);

            byte[] buf = new byte[65536];
            while (true) {
                int n = in.read(buf);
                if (n < 0) break;
                if (up) WeakNet.I.bytesUp.addAndGet(n);
                else WeakNet.I.bytesDown.addAndGet(n);

                Profile cur = WeakNet.I.profile;
                // 丢包 -> 传输停顿
                if (cur.lossPct > 0 && Math.random() * 100 < cur.lossPct) {
                    Thread.sleep((long) (cur.stallMs * (0.5 + Math.random())));
                }
                // 随机断连 -> 掐断整条连接
                if (cur.disconnectPct > 0 && Math.random() * 100 < cur.disconnectPct) {
                    WeakNet.I.log("disconnect", "模拟断连：" + ctx.label);
                    ctx.closeAll();
                    break;
                }
                bucket.consume(n);
                out.write(buf, 0, n);
                out.flush();
            }
        } catch (Exception e) {
            // 连接关闭 / 中断
        } finally {
            try {
                out.close();
            } catch (Exception e) {
                // ignore
            }
        }
    }

    /** 令牌桶限速器（字节/秒） */
    public static class Bucket {
        private double rate;
        private double tokens;
        private long last;

        public Bucket(int kbps) {
            rate = kbps > 0 ? kbps * 128.0 : 0;
            tokens = Math.min(rate, 4096);
            last = System.nanoTime();
        }

        /**
         * 原地更新速率（场景切换 / 停止模拟时调用）。
         * 必须原地改而不是 new 一个新桶：已在途的连接持有的是旧桶引用，
         * 换成新对象的话它们会一直按旧速率跑，表现为「点了停止但还是慢」。
         */
        public synchronized void setKbps(int kbps) {
            refill();
            rate = kbps > 0 ? kbps * 128.0 : 0;
            if (tokens > rate) tokens = rate;
            if (rate <= 0) tokens = 0;      // 不限速：把欠账清零
            notifyAll();                    // 唤醒正在等令牌的线程，立即恢复
        }

        /**
         * 预扣式令牌桶：先扣除再等待，保证多线程共享时总速率不超上限。
         * （若先等待再扣除，并发线程都会看到"有令牌"，导致实际速率成倍超标）
         */
        public synchronized void consume(int n) throws InterruptedException {
            if (rate <= 0) return;
            refill();
            tokens -= n;                    // 预扣（允许为负）
            while (tokens < 0) {
                if (rate <= 0) {            // 限速被取消：立刻放行，避免除零与永久等待
                    tokens = 0;
                    return;
                }
                long ms = (long) Math.ceil((-tokens) / rate * 1000);
                wait(Math.max(1, Math.min(ms, 250)));
                refill();
            }
        }

        private void refill() {
            long now = System.nanoTime();
            if (tokens < rate) {
                tokens = Math.min(tokens + (now - last) / 1e9 * rate, rate);
            }
            last = now;
        }
    }
}

class ConnCtx {
    public final Socket client;
    public Socket upstream;
    public String label = "?";

    ConnCtx(Socket client) {
        this.client = client;
    }

    public void attach(Socket up, String label) {
        this.upstream = up;
        this.label = label;
    }

    public void closeAll() {
        cleanup();
    }

    public void cleanup() {
        WeakNet.I.unregister(client);
        WeakNet.I.unregister(upstream);
        close(client);
        close(upstream);
    }

    private static void close(Socket s) {
        if (s == null) return;
        try {
            s.close();
        } catch (Exception e) {
            // ignore
        }
    }
}

class Util {
    /** 按 ISO-8859-1 读取一行（不含换行符） */
    public static String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream bo = new ByteArrayOutputStream();
        int c = -1;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            bo.write(c);
        }
        if (c == -1 && bo.size() == 0) return null;
        String s = new String(bo.toByteArray(), "ISO-8859-1");
        if (s.endsWith("\r")) s = s.substring(0, s.length() - 1);
        return s;
    }

    public static void writeAscii(Socket s, String text) {
        try {
            s.getOutputStream().write(text.getBytes("ISO-8859-1"));
            s.getOutputStream().flush();
        } catch (Exception e) {
            // ignore
        }
    }

    public static String safe(String s) {
        if (s == null) return "";
        return s.length() > 80 ? s.substring(0, 80) : s;
    }
}
