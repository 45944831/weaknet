package com.weaknet.app;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.util.Arrays;

/** 管道：延迟 + 抖动 + 令牌桶限速 + 丢包卡顿 + 随机断连 */
public class Pipe {
    public static void run(InputStream in, OutputStream out, Bucket bucket, boolean up, ConnCtx ctx) {
        run(in, out, bucket, up, ctx, null);
    }

    /**
     * eofAction：本方向读到 EOF（对端半关闭）后的收尾动作。
     * 原来固定 out.close()——Java 里关 socket 输出流等于关掉整个 socket，
     * 客户端「发完请求就 shutdown 写端」的常见行为（curl/nc/POST）
     * 会被传导成连接全关，下行响应一个字节都出不去。
     * 正确做法是 shutdownOutput 只关本方向，对向继续跑到 EOF。
     */
    public static void run(InputStream in, OutputStream out, Bucket bucket, boolean up, ConnCtx ctx, Runnable eofAction) {
        // 延迟投递队列：延迟和带宽解耦，不能每块 sleep——那样同一时刻只有一块在途，
        // 吞吐退化成「1 块/RTT」。与 tun 路径的 TcpSession 用同一个 DelayLine。
        // 原来这里只在函数开头 Thread.sleep 一次：CONNECT 隧道里第一个请求之后
        // 完全零延迟（HTTPS / WebSocket 这类长连接延迟参数等于没设）。
        DelayLine dl = new DelayLine("pipe-" + (up ? "up" : "down"), 1 << 20);
        try {
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
                // 每块都按当前场景算延迟（切场景对已在途的连接立即生效）。
                // 必须拷贝：buf 下一轮 read 就覆写，而 job 是异步跑的。
                final byte[] blk = Arrays.copyOf(buf, n);
                final OutputStream w = out;
                dl.post(DelayLine.delayMs(up), new Runnable() {
                    public void run() {
                        try {
                            w.write(blk, 0, blk.length);
                            w.flush();
                        } catch (Exception e) {
                            // 连接已断：忽略
                        }
                    }
                }, n);
            }
        } catch (Exception e) {
            // 连接关闭 / 中断
        } finally {
            if (ctx.isAborted()) {
                dl.close();                     // 已掐断：积压数据不必投递
            } else {
                drain(dl);                      // 排空：否则连接尾部的数据被丢
            }
            try {
                if (eofAction != null) eofAction.run();
                else out.close();
            } catch (Exception e) {
                // ignore
            }
        }
    }

    /** 等延迟队列排空（慢档位 256kbps 下 1MB 积压需要 32s，120s 留足余量） */
    static void drain(DelayLine dl) {
        long deadline = System.currentTimeMillis() + 120000;
        while (dl.pendingBytes() > 0 && System.currentTimeMillis() < deadline) {
            try {
                Thread.sleep(20);
            } catch (InterruptedException e) {
                break;
            }
        }
        dl.close();
    }

    /**
     * 单块数据的延迟写出。用于代理路径的请求头：普通 HTTP 的请求是逐行 read 的，
     * 读到的瞬间就离开管道、不经过 DelayLine——不在这里补一下，上行延迟对
     * 正文为空的 GET 完全不生效（实测 RTT 只有设定值的一半）。
     */
    public static void writeDelayed(OutputStream out, byte[] data, boolean up) {
        DelayLine dl = new DelayLine("pipe-hdr", 0);
        dl.post(DelayLine.delayMs(up), new Runnable() {
            public void run() {
                try {
                    out.write(data);
                    out.flush();
                } catch (Exception e) {
                    // 连接已断：忽略
                }
            }
        }, data.length);
        drain(dl);
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
            try {
                while (tokens < 0) {
                    if (rate <= 0) {        // 限速被取消：立刻放行，避免除零与永久等待
                        tokens = 0;
                        return;
                    }
                    long ms = (long) Math.ceil((-tokens) / rate * 1000);
                    wait(Math.max(1, Math.min(ms, 250)));
                    refill();
                }
            } catch (InterruptedException ie) {
                // 补偿预扣：本次没等到令牌就放行了，这笔账不能留在桶里。
                // 否则会话关闭打断 worker 线程时，每次中断都留下一笔"幽灵欠债"，
                // 并发连接多、中断多，欠债会累积到远超实际流量的规模 ——
                // 实测 8 并发后单连接 256KB 卡 90s 以上、要几分钟才自然还清。
                tokens = Math.min(tokens + n, rate);
                throw ie;
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
    private volatile boolean aborted = false;   // 模拟断连标记：积压数据不必投递

    ConnCtx(Socket client) {
        this.client = client;
    }

    public void attach(Socket up, String label) {
        this.upstream = up;
        this.label = label;
    }

    public void closeAll() {
        aborted = true;
        cleanup();
    }

    public boolean isAborted() {
        return aborted;
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
