package com.weaknet.app;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 有界 HTTP/1.1 请求读取：代理 / 控制 / 共享三个本地服务共用。
 *
 * 都是自家客户端在调，不支持 chunked —— 与其猜长度，不如 400 拒绝。
 * 任何超限都抛 {@link Bad}，调用方按 code 回状态码并关连接，
 * 既不 allocate 一个「对方声明的 2GB」缓冲区，也不会被无限头部拖死。
 */
public final class HttpIn {
    public static final int MAX_LINE = 8 * 1024;
    public static final int MAX_HEAD = 64 * 1024;
    public static final int MAX_LINES = 100;
    public static final int MAX_BODY_SHARE = 256 * 1024;
    public static final int MAX_BODY_CTRL = 1 << 20;

    private HttpIn() {
    }

    /** 读取阶段的错误：code 直接用作 HTTP 状态码 */
    public static final class Bad extends IOException {
        public final int code;

        public Bad(int code, String msg) {
            super(msg);
            this.code = code;
        }
    }

    public static final class Req {
        public final String method;
        /** 原始请求目标（可能带 query、http:// 绝对地址） */
        public final String path;
        public final List<String> head;
        public final String body;
        private final Map<String, String> map = new LinkedHashMap<String, String>();

        Req(String method, String path, List<String> head, String body) {
            this.method = method;
            this.path = path;
            this.head = head;
            this.body = body;
            for (int i = 1; i < head.size(); i++) {
                String h = head.get(i);
                int c = h.indexOf(':');
                if (c <= 0) continue;
                String k = h.substring(0, c).trim().toLowerCase();
                String v = h.substring(c + 1).trim();
                if (map.containsKey(k)) map.put(k, map.get(k) + ", " + v);
                else map.put(k, v);
            }
        }

        public String header(String name) {
            return map.get(name == null ? "" : name.toLowerCase());
        }
    }

    /** 只读头部（代理用）：正文原样留给后续管道，不在这里消费 */
    public static Req readHead(InputStream in) throws IOException {
        List<String> head = readHeadLines(in);
        if (head == null) return null;
        String[] parts = head.get(0).split(" ");
        if (parts.length < 2) throw new Bad(400, "bad request line");
        return new Req(parts[0].toUpperCase(), parts[1], head, null);
    }

    /** 头部 + 正文；正文长度先校验上限，再分配 */
    public static Req read(InputStream in, int maxBody) throws IOException {
        List<String> head = readHeadLines(in);
        if (head == null) return null;
        String[] parts = head.get(0).split(" ");
        if (parts.length < 2) throw new Bad(400, "bad request line");
        Req req = new Req(parts[0].toUpperCase(), parts[1], head, "");
        String chunked = req.header("transfer-encoding");
        if (chunked != null && chunked.length() > 0) {
            throw new Bad(400, "chunked not supported");
        }
        String raw = req.header("content-length");
        long len = 0;
        if (raw != null && raw.trim().length() > 0) {
            try {
                len = Long.parseLong(raw.trim());
            } catch (Exception e) {
                throw new Bad(400, "bad content-length");
            }
            if (len < 0) throw new Bad(400, "bad content-length");
        }
        if (len > maxBody) throw new Bad(413, "body too large");
        if (len == 0) return req;
        byte[] buf = new byte[(int) len];
        int off = 0;
        while (off < buf.length) {
            int n = in.read(buf, off, buf.length - off);
            if (n < 0) throw new Bad(400, "incomplete body");
            off += n;
        }
        return new Req(req.method, req.path, head, new String(buf, "UTF-8"));
    }

    /** 逐行读到空行为止；只发了一半就断开按 400 处理 */
    private static List<String> readHeadLines(InputStream in) throws IOException {
        List<String> head = new ArrayList<String>();
        int total = 0;
        boolean blank = false;
        String line;
        while ((line = readLine(in)) != null) {
            total += line.length() + 2;
            if (total > MAX_HEAD) throw new Bad(431, "header too large");
            if (line.length() == 0) {
                blank = true;
                break;
            }
            if (head.size() >= MAX_LINES) throw new Bad(431, "too many headers");
            head.add(line);
        }
        if (head.isEmpty() && !blank) return null;
        if (!blank) throw new Bad(400, "incomplete request");
        return head;
    }

    /** 按 ISO-8859-1 读一行（去掉行尾 \r\n），单行超过 MAX_LINE 抛 431 */
    public static String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream bo = new ByteArrayOutputStream(128);
        int c;
        while ((c = in.read()) >= 0) {
            if (c == '\n') break;
            if (c == '\r') continue;
            bo.write(c);
            if (bo.size() > MAX_LINE) throw new Bad(431, "line too large");
        }
        if (c < 0 && bo.size() == 0) return null;
        return new String(bo.toByteArray(), "ISO-8859-1");
    }
}
