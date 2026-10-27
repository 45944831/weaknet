package com.weaknet.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 环境共享：发现同一个局域网里的其它 WeakNet，互相推送自定义测试环境。
 *
 * 协议与桌面端（weaknet.py）完全一致，Windows ↔ Android 可以互推：
 *  - 发现：UDP 广播 18899~18906，每 3 秒喊一句 {tag,id,name,platform,share,ver}
 *  - 接收：本机 TCP 8900 上一个只认 /api/hello 与 /api/share 的小 HTTP 服务
 *  - 推送：HTTP POST 到对方 share 端口的 /api/share
 *  - 同名环境不静默覆盖：先进收件箱，等本机用户决定 覆盖 / 保留 / 另存
 *
 * 三个后台线程（收听 / 广播 / 共享服务）随 NetService 常驻，全部是守护线程，
 * 进程被回收时不需要显式清理。所有网络调用都不能在主线程执行。
 */
public class ShareNet {
    public static final int DISCOVER_PORT = 18899;    // UDP 广播起始端口
    public static final int DISCOVER_SPAN = 8;        // 同机多实例时各占其后的一个端口
    public static final int SHARE_PORT = 8900;        // TCP 共享端口（接收别人推送用）
    public static final String TAG = "weaknet-discover-v1";
    public static final long PEER_TTL = 12000L;       // 超过这么久没收到广播就当对方下线

    public static final class Peer {
        public String id = "";
        public String name = "";
        public String ip = "";
        public String platform = "";
        public int share = SHARE_PORT;
        public long at = 0;
    }

    public static final class InboxItem {
        public String id = "";
        public String from = "";
        public String fromId = "";
        public String name = "";
        public JSONObject profile = new JSONObject();
        /** "preset"=自定义环境 / "combo"=弱网组合：两类处理逻辑不同，收件箱里要分开摆在明面上 */
        public String kind = "preset";
        /** kind=="combo" 时：整份组合载荷（loop + 原始 steps），处理时才映射到本机场景 */
        public JSONObject combo = new JSONObject();
        public long at = 0;
    }

    /** 推送结果：ok + 说给人听的一句话 */
    public static final class PushResult {
        public boolean ok;
        public String msg = "";
        public List<String> accepted = new ArrayList<String>();
        public List<String> pending = new ArrayList<String>();
        /** 对方那边一段都落不下、被丢弃的组合名 */
        public List<String> dropped = new ArrayList<String>();
    }

    private static final String PREF = "weaknet";
    private static final String KEY_ID = "share_id";
    private static final String KEY_NAME = "share_name";

    private static volatile Context ctx;
    private static volatile String selfId = "";
    private static volatile String userName = "";

    private static final Map<String, Peer> peers =
            Collections.synchronizedMap(new LinkedHashMap<String, Peer>());
    private static final Map<String, InboxItem> inbox =
            Collections.synchronizedMap(new LinkedHashMap<String, InboxItem>());

    private static volatile int sharePort = 0;
    private static volatile boolean discoverListen = false;
    private static volatile boolean running = false;

    private static volatile DatagramSocket dsock;
    private static volatile ServerSocket ssock;

    // ---------------------------------------------------------------- 身份

    public static void bind(Context c) {
        if (c != null) ctx = c.getApplicationContext();
    }

    /** 补齐「我是谁」：读过一次就缓存，避免两次调用拿到两个不同的 id */
    public static synchronized void ensureIdentity() {
        // 读过一次就缓存：每次都现编 id 的话，同一台设备在别人眼里会变成两台
        if (selfId != null && selfId.length() > 0
                && userName != null && userName.length() > 0) return;
        Context c = ctx;
        String id = "", name = "";
        if (c != null) {
            SharedPreferences sp = c.getSharedPreferences(PREF, Context.MODE_PRIVATE);
            id = sp.getString(KEY_ID, "");
            name = sp.getString(KEY_NAME, "");
        }
        if (id == null || id.length() == 0) {
            id = newId();
            if (c != null) {
                c.getSharedPreferences(PREF, Context.MODE_PRIVATE)
                        .edit().putString(KEY_ID, id).apply();
            }
        }
        selfId = id;
        if (name == null || name.length() == 0) name = defaultName();
        userName = name;
    }

    private static String defaultName() {
        try {
            String m = Build.MODEL;
            if (m != null && m.trim().length() > 0) return m.trim().substring(0, Math.min(24, m.trim().length()));
        } catch (Exception e) {
            // ignore
        }
        return "Android";
    }

    public static String getUserName() {
        ensureIdentity();
        return userName;
    }

    /** 改名：立刻落盘并广播一次，让别人尽快看到新名字 */
    public static synchronized void setUserName(String n) {
        ensureIdentity();
        userName = cleanName(n, defaultName());
        Context c = ctx;
        if (c != null) {
            c.getSharedPreferences(PREF, Context.MODE_PRIVATE)
                    .edit().putString(KEY_NAME, userName).apply();
        }
        WeakNet.I.log("share", "本机名称改为：" + userName);
        broadcastPresence();
    }

    public static String getSelfId() {
        ensureIdentity();
        return selfId;
    }

    // ---------------------------------------------------------------- 启停

    public static synchronized void start() {
        if (running) return;
        running = true;
        startListenThread();
        startBroadcastThread();
        startServerThread();
    }

    public static void stop() {
        running = false;
        try {
            if (dsock != null) dsock.close();
        } catch (Exception e) {
            // ignore
        }
        try {
            if (ssock != null) ssock.close();
        } catch (Exception e) {
            // ignore
        }
    }

    private static void startListenThread() {
        Thread t = new Thread(new Runnable() {
            public void run() {
                DatagramSocket s = null;
                for (int i = 0; i < DISCOVER_SPAN; i++) {
                    try {
                        s = new DatagramSocket(null);
                        s.setReuseAddress(true);
                        s.setBroadcast(true);
                        s.bind(new java.net.InetSocketAddress(DISCOVER_PORT + i));
                        break;
                    } catch (Exception e) {
                        try {
                            if (s != null) s.close();
                        } catch (Exception ignore) {
                        }
                        s = null;
                    }
                }
                if (s == null) {
                    discoverListen = false;
                    WeakNet.I.log("share", "局域网发现未能监听（UDP "
                            + DISCOVER_PORT + "-" + (DISCOVER_PORT + DISCOVER_SPAN - 1) + " 均被占用）");
                    return;
                }
                dsock = s;
                discoverListen = true;
                WeakNet.I.log("share", "局域网发现已开启（UDP " + s.getLocalPort() + "）");
                byte[] buf = new byte[4096];
                while (running) {
                    try {
                        DatagramPacket p = new DatagramPacket(buf, buf.length);
                        s.receive(p);
                        onDatagram(new String(p.getData(), 0, p.getLength(), "UTF-8"),
                                p.getAddress() == null ? "" : p.getAddress().getHostAddress());
                    } catch (Exception e) {
                        if (!running) break;
                    }
                }
            }
        }, "share-listen");
        t.setDaemon(true);
        t.start();
    }

    private static void startBroadcastThread() {
        Thread t = new Thread(new Runnable() {
            public void run() {
                boolean first = true;
                while (running) {
                    try {
                        broadcastPresence();
                        if (first) {
                            first = false;
                            WeakNet.I.log("share", "已向局域网广播本机名称：" + getUserName());
                        }
                    } catch (Exception e) {
                        WeakNet.I.log("share", "广播失败：" + String.valueOf(e.getMessage()).substring(
                                0, Math.min(60, String.valueOf(e.getMessage()).length())));
                    }
                    long now = System.currentTimeMillis();
                    synchronized (peers) {
                        List<String> dead = new ArrayList<String>();
                        for (Map.Entry<String, Peer> e : peers.entrySet()) {
                            if (now - e.getValue().at > PEER_TTL) dead.add(e.getKey());
                        }
                        for (String k : dead) peers.remove(k);
                    }
                    try {
                        Thread.sleep(3000);
                    } catch (InterruptedException e) {
                        return;
                    }
                }
            }
        }, "share-bc");
        t.setDaemon(true);
        t.start();
    }

    private static void startServerThread() {
        Thread t = new Thread(new Runnable() {
            public void run() {
                ServerSocket ss = null;
                try {
                    ss = new ServerSocket(SHARE_PORT);
                } catch (Exception e) {
                    sharePort = 0;
                    WeakNet.I.log("share", "环境共享未能开启（端口 " + SHARE_PORT + " 被占用）");
                    return;
                }
                ssock = ss;
                sharePort = SHARE_PORT;
                WeakNet.I.log("share", "环境共享已开启（端口 " + SHARE_PORT + "）");
                while (running) {
                    try {
                        Socket c = ss.accept();
                        serve(c);
                    } catch (Exception e) {
                        if (!running) break;
                    }
                }
            }
        }, "share-server");
        t.setDaemon(true);
        t.start();
    }

    // ---------------------------------------------------------------- 发现

    private static void onDatagram(String text, String fromIp) {
        try {
            JSONObject o = new JSONObject(text);
            if (!TAG.equals(o.optString("tag", ""))) return;
            String pid = o.optString("id", "");
            if (pid.length() == 0 || pid.equals(getSelfId())) return;
            Peer p = new Peer();
            p.id = pid;
            p.name = cleanName(o.optString("name", ""), "匿名");
            p.ip = fromIp;
            String plat = o.optString("platform", "");
            p.platform = plat != null && plat.length() > 16 ? plat.substring(0, 16) : (plat == null ? "" : plat);
            p.share = o.optInt("share", SHARE_PORT);
            p.at = System.currentTimeMillis();
            peers.put(pid, p);
        } catch (Exception e) {
            // 不是我们的包：忽略
        }
    }

    private static void broadcastPresence() {
        JSONObject o = new JSONObject();
        try {
            o.put("tag", TAG);
            o.put("id", getSelfId());
            o.put("name", getUserName());
            o.put("platform", "android");
            o.put("share", sharePort > 0 ? sharePort : SHARE_PORT);
            o.put("ver", 1);
        } catch (Exception e) {
            return;
        }
        byte[] data = o.toString().getBytes(java.nio.charset.Charset.forName("UTF-8"));
        List<String> addrs = new ArrayList<String>();
        addrs.add("255.255.255.255");
        try {
            String ip = WeakNet.I.lanIp();
            if (ip != null && !"127.0.0.1".equals(ip) && ip.indexOf('.') > 0) {
                addrs.add(ip.substring(0, ip.lastIndexOf('.')) + ".255");
            }
        } catch (Exception e) {
            // ignore
        }
        for (String a : addrs) {
            for (int i = 0; i < DISCOVER_SPAN; i++) {
                DatagramSocket s = null;
                try {
                    s = new DatagramSocket(null);
                    s.setReuseAddress(true);
                    s.setBroadcast(true);
                    DatagramPacket p = new DatagramPacket(data, data.length,
                            InetAddress.getByName(a), DISCOVER_PORT + i);
                    s.send(p);
                } catch (Exception e) {
                    // 某个地址/端口发不出去很正常，继续下一个
                } finally {
                    try {
                        if (s != null) s.close();
                    } catch (Exception ignore) {
                    }
                }
            }
        }
    }

    /** 当前还在线的设备（按名字排序） */
    public static List<Peer> peers() {
        long now = System.currentTimeMillis();
        List<Peer> out = new ArrayList<Peer>();
        synchronized (peers) {
            for (Peer p : peers.values()) {
                if (now - p.at <= PEER_TTL) out.add(p);
            }
        }
        Collections.sort(out, new Comparator<Peer>() {
            public int compare(Peer a, Peer b) {
                return a.name.toLowerCase().compareTo(b.name.toLowerCase());
            }
        });
        return out;
    }

    public static Peer findPeer(String id) {
        if (id == null) return null;
        for (Peer p : peers()) {
            if (id.equals(p.id)) return p;
        }
        return null;
    }

    // ---------------------------------------------------------------- 接收

    // ---------------------------------------------------------------- 共享契约

    /**
     * 组合里的段 → 可推送的形态。
     *
     * 段记的 key 是「u:<id>」还是内置档位：自定义 id 是每台机自己编的，推给别人时
     * 对方根本没有这个 id，照搬过去会解析成「已删除的场景」——组合看着收下了，播起来
     * 每段都是空的。所以自定义段连名字和整份参数一起塞进去（与 items 一个道理），
     * 内置段至少带上名字，版本不同导致对方没这一档时能把话说清楚。
     */
    public static JSONArray comboStepsJson(MyCombos.Item it) {
        JSONArray ss = new JSONArray();
        if (it == null) return ss;
        try {
            for (MyCombos.Step s : it.steps) {
                JSONObject so = new JSONObject();
                so.put("key", s.key);
                so.put("minutes", s.minutes);
                so.put("seconds", s.seconds);
                so.put("scene", sceneLabel(s.key));
                if (s.key != null && s.key.startsWith("u:")) {
                    MyPresets.Item ref = MyPresets.find(s.key.substring(2));
                    if (ref != null) so.put("profile", ref.profile.toJson());
                }
                ss.put(so);
            }
        } catch (Exception e) {
            // ignore
        }
        return ss;
    }

    /** 某个场景 key 现在的名字（用它来告诉对方「这一段是啥」） */
    private static String sceneLabel(String key) {
        if (key == null || key.length() == 0) return "";
        if (key.startsWith("u:")) {
            MyPresets.Item it = MyPresets.find(key.substring(2));
            return it == null ? key : it.name;
        }
        if (Profile.known(key)) return Profile.build(key).name;
        return key;
    }

    /**
     * 别人推来的一段 → 本机这一段的 key。返回 null 表示本机没有它要的场景，这段只能丢掉。
     *
     * 自定义段在本机没有同名场景时，会按对方带来的参数快照在本机新建一条：组合是
     * 「一段引用某场景」，场景本体不落地，这一段就无处可播。
     */
    private static String resolveStepKey(JSONObject so, JSONArray dropped, int idx) {
        if (so == null) return null;
        String key = so.optString("key", "").trim();
        if (key.length() == 0 || key.startsWith("c:")) {
            drop(dropped, idx, key, "嵌套组合");
            return null;
        }
        if (key.startsWith("u:")) {
            String nm = cleanScene(so.optString("scene", ""));
            MyPresets.Item exists = nm.length() > 0 ? MyPresets.findByName(nm) : null;
            if (exists != null) return "u:" + exists.id;
            JSONObject snap = so.optJSONObject("profile");
            if (snap != null) {
                MyPresets.Item made = MyPresets.save(null, nm.length() > 0 ? nm : "对方的环境", snap);
                return "u:" + made.id;
            }
            drop(dropped, idx, key, "缺场景参数");
            return null;
        }
        if (Profile.known(key)) return key;
        // 版本差异（对方比我们新）：退一步看本地有没有同名的自定义场景
        String nm = cleanScene(so.optString("scene", ""));
        MyPresets.Item like = nm.length() > 0 ? MyPresets.findByName(nm) : null;
        if (like != null) return "u:" + like.id;
        drop(dropped, idx, key, "本机没有这一档");
        return null;
    }

    /** 整份组合的段做本机映射：别人给的 raw steps → 本机 steps + 被丢掉的说明 */
    private static List<MyCombos.Step> resolveSteps(JSONArray raw, JSONArray dropped) {
        List<MyCombos.Step> out = new ArrayList<MyCombos.Step>();
        if (raw == null) return out;
        for (int i = 0; i < raw.length(); i++) {
            JSONObject so = raw.optJSONObject(i);
            if (so == null) continue;
            String key = resolveStepKey(so, dropped, i + 1);
            if (key == null) continue;
            MyCombos.Step s = new MyCombos.Step();
            s.key = key;
            s.minutes = Math.max(0, so.optInt("minutes", 0));
            s.seconds = Math.max(0, Math.min(59, so.optInt("seconds", 0)));
            out.add(s);
        }
        return out;
    }

    private static void drop(JSONArray dropped, int idx, String key, String why) {
        if (dropped == null) return;
        dropped.put("第" + idx + "段（" + why + "）");
    }

    /** 别人给的名字：压空白 + 限长（与本地限制一致） */
    private static String cleanScene(String s) {
        String t = s == null ? "" : s.trim().replaceAll("\\s+", " ");
        return t.length() > 40 ? t.substring(0, 40) : t;
    }

    /** 别人推给我的环境：不同名直接入库；同名进收件箱等本机决定 */
    public static synchronized JSONObject receiveShare(JSONObject payload) {
        JSONObject res = new JSONObject();
        JSONArray accepted = new JSONArray();
        JSONArray pending = new JSONArray();
        JSONArray dropList = new JSONArray();   // 对方收不下的组合（回执给推送方）
        try {
            JSONArray items = payload.optJSONArray("items");
            JSONArray combos = payload.optJSONArray("combos");
            if ((items == null || items.length() == 0)
                    && (combos == null || combos.length() == 0)) {
                res.put("ok", false);
                res.put("error", "没有内容");
                return res;
            }
            String from = cleanName(payload.optString("from", ""), "某人");
            String fromId = payload.optString("from_id", "");
            if (items != null) {
                for (int i = 0; i < items.length() && i < 20; i++) {
                    JSONObject it = items.optJSONObject(i);
                    if (it == null) continue;
                    String name = cleanName(it.optString("name", ""), "");
                    if (name.length() == 0) continue;
                    JSONObject prof = it.optJSONObject("profile");
                    if (prof == null) prof = new JSONObject();
                    if (findByName(name) == null) {
                        MyPresets.Item saved = MyPresets.save(null, name, prof);
                        accepted.put(saved.name);
                        WeakNet.I.log("share", "收到 " + from + " 推送的环境：" + saved.name);
                    } else {
                        String iid = newId();
                        while (inbox.containsKey(iid)) iid = newId();
                        InboxItem ib = new InboxItem();
                        ib.id = iid;
                        ib.from = from;
                        ib.fromId = fromId;
                        ib.name = name;
                        ib.kind = "preset";
                        ib.profile = prof;
                        ib.at = System.currentTimeMillis();
                        inbox.put(iid, ib);
                        JSONObject pi = new JSONObject();
                        pi.put("id", iid);
                        pi.put("name", name);
                        pi.put("kind", "preset");
                        pending.put(pi);
                        WeakNet.I.log("share", from + " 推送的「" + name + "」与本地同名，等你决定");
                    }
                }
            }
            if (combos != null) {
                for (int i = 0; i < combos.length() && i < 20; i++) {
                    JSONObject c = combos.optJSONObject(i);
                    if (c == null) continue;
                    String name = cleanScene(c.optString("name", ""));
                    if (name.length() == 0) continue;
                    JSONObject keep = new JSONObject();
                    keep.put("loop", c.optBoolean("loop", false));
                    JSONArray raw = c.optJSONArray("steps");
                    keep.put("steps", raw == null ? new JSONArray() : raw);
                    if (MyCombos.findByName(name) == null) {
                        JSONArray dropped = new JSONArray();
                        List<MyCombos.Step> steps = resolveSteps(keep.optJSONArray("steps"), dropped);
                        if (steps.isEmpty()) {
                            // 一段都落不下来：对方这台机器没法播这个组合。
                            // 要回执给推送方 —— 静默丢弃的话发送方会以为推成功了。
                            String why = dropped.length() > 0 ? joinArr(dropped) : "没有可用的段";
                            JSONObject d = new JSONObject();
                            d.put("name", name);
                            d.put("reason", why);
                            dropList.put(d);
                            WeakNet.I.log("share", from + " 推送的组合「" + name
                                    + "」没有一段能落在本机，已丢弃：" + why);
                            continue;
                        }
                        MyCombos.Item saved = MyCombos.save(null, name,
                                keep.optBoolean("loop", false), steps);
                        if (saved == null) {
                            // 引擎再兜一道：归一化后没段可用就不落库（不能收下一支空壳组合）
                            JSONObject d2 = new JSONObject();
                            d2.put("name", name);
                            d2.put("reason", "没有可用的段");
                            dropList.put(d2);
                            WeakNet.I.log("share", from + " 推送的组合「" + name
                                    + "」没有一段能落在本机，已丢弃");
                            continue;
                        }
                        accepted.put("🔀 " + saved.name);
                        WeakNet.I.log("share", "收到 " + from + " 推送的弱网组合：" + saved.name);
                        if (dropped.length() > 0) {
                            WeakNet.I.log("share", "组合「" + saved.name + "」丢掉 " + joinArr(dropped));
                        }
                    } else {
                        String iid = newId();
                        while (inbox.containsKey(iid)) iid = newId();
                        InboxItem ib = new InboxItem();
                        ib.id = iid;
                        ib.from = from;
                        ib.fromId = fromId;
                        ib.name = name;
                        ib.kind = "combo";
                        ib.combo = keep;
                        ib.at = System.currentTimeMillis();
                        inbox.put(iid, ib);
                        JSONObject pi = new JSONObject();
                        pi.put("id", iid);
                        pi.put("name", name);
                        pi.put("kind", "combo");
                        pending.put(pi);
                        WeakNet.I.log("share", from + " 推送的组合「" + name + "」与本地同名，等你决定");
                    }
                }
            }
            res.put("ok", true);
            res.put("accepted", accepted);
            res.put("pending", pending);
            res.put("dropped", dropList);
            res.put("name", getUserName());
        } catch (Exception e) {
            try {
                res.put("ok", false);
                res.put("error", String.valueOf(e.getMessage()));
            } catch (Exception ignore) {
            }
        }
        return res;
    }

    private static String joinArr(JSONArray a) {
        StringBuilder sb = new StringBuilder();
        if (a == null) return "";
        for (int i = 0; i < a.length(); i++) {
            if (sb.length() > 0) sb.append("、");
            sb.append(a.optString(i, ""));
        }
        return sb.toString();
    }

    private static MyPresets.Item findByName(String name) {
        String target = name == null ? "" : name.trim().toLowerCase();
        for (MyPresets.Item it : MyPresets.load()) {
            if (it.name.trim().toLowerCase().equals(target)) return it;
        }
        return null;
    }

    public static List<InboxItem> inboxList() {
        List<InboxItem> out = new ArrayList<InboxItem>();
        synchronized (inbox) {
            out.addAll(inbox.values());
        }
        Collections.sort(out, new Comparator<InboxItem>() {
            public int compare(InboxItem a, InboxItem b) {
                return a.at < b.at ? -1 : (a.at > b.at ? 1 : 0);
            }
        });
        return out;
    }

    public static int inboxCount() {
        synchronized (inbox) {
            return inbox.size();
        }
    }

    /**
     * 处理收件箱里的一条：overwrite=覆盖本地 / keep=保留本地 / rename=另存为新名字。
     *
     * 环境（preset）与组合（combo）共用这三个动作；组合到这一步才把段映射到本机场景
     * —— 收件箱里的条子随时可能被「保留本地」丢掉，提前写库会白留一堆自定义场景。
     */
    public static synchronized JSONObject resolveInbox(String iid, String action, String newName) {
        JSONObject res = new JSONObject();
        InboxItem item;
        synchronized (inbox) {
            item = inbox.remove(iid);
        }
        if (item == null) {
            try {
                res.put("ok", false);
                res.put("error", "该请求已处理或不存在");
            } catch (Exception ignore) {
            }
            return res;
        }
        try {
            boolean isCombo = "combo".equals(item.kind);
            res.put("kind", item.kind);
            if ("keep".equals(action)) {
                WeakNet.I.log("share", "保留本地的「" + item.name + "」，丢弃 " + item.from + " 推送的版本");
                res.put("ok", true);
                res.put("action", "keep");
                res.put("name", item.name);
                return res;
            }
            if (isCombo) {
                JSONArray dropped = new JSONArray();
                List<MyCombos.Step> steps = resolveSteps(item.combo.optJSONArray("steps"), dropped);
                if (steps.isEmpty()) {
                    WeakNet.I.log("share", item.from + " 推送的组合「" + item.name
                            + "」没有一段能落在本机，已丢弃");
                    res.put("ok", false);
                    res.put("error", "这个组合里没有任何一段在本机可用");
                    return res;
                }
                boolean loop = item.combo.optBoolean("loop", false);
                MyCombos.Item saved;
                if ("rename".equals(action)) {
                    String nm = cleanScene(newName);
                    if (nm.length() == 0) nm = cleanScene(item.name + "（来自 " + item.from + "）");
                    if (nm.length() == 0) nm = "收到的组合";
                    saved = MyCombos.save(null, nm, loop, steps);
                    if (saved == null) {
                        // 引擎兜底：不能拿一段都用不上的组合去覆盖本地那支排好的
                        res.put("ok", false);
                        res.put("error", "这个组合里没有任何一段在本机可用");
                        return res;
                    }
                    WeakNet.I.log("share", "已把 " + item.from + " 推送的组合另存为「" + saved.name + "」");
                } else {
                    MyCombos.Item exists = MyCombos.findByName(item.name);
                    saved = MyCombos.save(exists == null ? null : exists.id, item.name, loop, steps);
                    if (saved == null) {
                        res.put("ok", false);
                        res.put("error", "这个组合里没有任何一段在本机可用");
                        return res;
                    }
                    WeakNet.I.log("share", "已用 " + item.from + " 推送的版本覆盖组合「" + saved.name + "」");
                }
                if (dropped.length() > 0) {
                    WeakNet.I.log("share", "组合「" + saved.name + "」丢掉 " + joinArr(dropped));
                }
                res.put("ok", true);
                res.put("action", "rename".equals(action) ? "rename" : "overwrite");
                res.put("name", saved.name);
                return res;
            }
            if ("rename".equals(action)) {
                String nm = cleanName(newName, "");
                if (nm.length() == 0) nm = cleanName(item.name + "（来自 " + item.from + "）", "自定义");
                MyPresets.Item it = MyPresets.save(null, nm, item.profile);
                WeakNet.I.log("share", "已把 " + item.from + " 推送的环境另存为「" + it.name + "」");
                res.put("ok", true);
                res.put("action", "rename");
                res.put("name", it.name);
                return res;
            }
            MyPresets.Item exists = findByName(item.name);
            MyPresets.Item it = MyPresets.save(exists == null ? null : exists.id, item.name, item.profile);
            WeakNet.I.log("share", "已用 " + item.from + " 推送的版本覆盖「" + it.name + "」");
            res.put("ok", true);
            res.put("action", "overwrite");
            res.put("name", it.name);
        } catch (Exception e) {
            try {
                res.put("ok", false);
                res.put("error", String.valueOf(e.getMessage()));
            } catch (Exception ignore) {
            }
        }
        return res;
    }

    // ---------------------------------------------------------------- 推送

    /** 只推自定义环境（旧入口，保留给不想关心组合的调用方） */
    public static PushResult push(Peer peer, List<String> ids) {
        return push(peer, ids, null);
    }

    /**
     * 把选中的自定义环境 / 弱网组合一次推给一个 peer（同步 HTTP，别在主线程调）。
     * 两类在同一次请求里过去：同名判定各自独立，但整体上只是一次推送、日志上也
     * 只留一条「谁推来了什么」，事后两边对得上。
     */
    public static PushResult push(Peer peer, List<String> ids, List<String> comboIds) {
        PushResult r = new PushResult();
        if (peer == null) {
            r.msg = "请先选择一台设备";
            return r;
        }
        List<MyPresets.Item> mine = MyPresets.load();
        JSONArray items = new JSONArray();
        for (String id : ids) {
            for (MyPresets.Item it : mine) {
                if (it.id.equals(id)) {
                    try {
                        JSONObject o = new JSONObject();
                        o.put("id", it.id);
                        o.put("name", it.name);
                        o.put("profile", it.profile.toJson());
                        items.put(o);
                    } catch (Exception e) {
                        // ignore
                    }
                    break;
                }
            }
        }
        // 组合：段里的自定义场景在对方机器上多半没有 id 可对，连参数快照一起带过去
        List<MyCombos.Item> combos = MyCombos.load();
        JSONArray cs = new JSONArray();
        if (comboIds != null) {
            for (String id : comboIds) {
                for (MyCombos.Item it : combos) {
                    if (it.id.equals(id)) {
                        try {
                            JSONObject o = new JSONObject();
                            o.put("id", it.id);
                            o.put("name", it.name);
                            o.put("loop", it.loop);
                            o.put("steps", comboStepsJson(it));
                            cs.put(o);
                        } catch (Exception e) {
                            // ignore
                        }
                        break;
                    }
                }
            }
        }
        if (items.length() == 0 && cs.length() == 0) {
            r.msg = "没找到要推送的内容";
            return r;
        }
        JSONObject payload = new JSONObject();
        try {
            payload.put("from", getUserName());
            payload.put("from_id", getSelfId());
            payload.put("platform", "android");
            payload.put("items", items);
            payload.put("combos", cs);
        } catch (Exception e) {
            r.msg = "打包失败";
            return r;
        }
        int port = peer.share > 0 ? peer.share : SHARE_PORT;
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL("http://" + peer.ip + ":" + port + "/api/share").openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setConnectTimeout(6000);
            conn.setReadTimeout(6000);
            conn.setDoOutput(true);
            byte[] body = payload.toString().getBytes("UTF-8");
            OutputStream os = conn.getOutputStream();
            os.write(body);
            os.flush();
            os.close();
            int code = conn.getResponseCode();
            StringBuilder sb = new StringBuilder();
            BufferedReader br = new BufferedReader(new InputStreamReader(
                    code >= 400 ? conn.getErrorStream() : conn.getInputStream(), "UTF-8"));
            String line;
            while ((line = br.readLine()) != null) sb.append(line);
            br.close();
            JSONObject res = new JSONObject(sb.toString());
            JSONArray acc = res.optJSONArray("accepted");
            if (acc != null) {
                for (int i = 0; i < acc.length(); i++) r.accepted.add(acc.optString(i, ""));
            }
            JSONArray pen = res.optJSONArray("pending");
            if (pen != null) {
                for (int i = 0; i < pen.length(); i++) {
                    JSONObject p = pen.optJSONObject(i);
                    r.pending.add(p == null ? "" : p.optString("name", ""));
                }
            }
            // 对方那边一段都落不下 → 组合被丢了，必须回执，不然用户以为推成功了
            JSONArray drop = res.optJSONArray("dropped");
            if (drop != null) {
                for (int i = 0; i < drop.length(); i++) {
                    JSONObject d = drop.optJSONObject(i);
                    if (d != null) r.dropped.add(d.optString("name", ""));
                }
            }
            r.ok = true;
            String to = res.optString("name", peer.name);
            StringBuilder msg = new StringBuilder();
            if (!r.accepted.isEmpty()) msg.append("已推送给 ").append(to).append("：").append(join(r.accepted));
            if (!r.pending.isEmpty()) {
                if (msg.length() > 0) msg.append("；");
                msg.append("对方有同名内容，等他确认：").append(join(r.pending));
            }
            if (!r.dropped.isEmpty()) {
                if (msg.length() > 0) msg.append("；");
                msg.append("对方那边收不下：").append(join(r.dropped));
            }
            if (msg.length() == 0) msg.append("已推送给 ").append(to);
            r.msg = msg.toString();
            WeakNet.I.log("share", r.msg);
        } catch (Exception e) {
            r.msg = "推送给 " + peer.name + " 失败：" + String.valueOf(e.getMessage());
            WeakNet.I.log("share", r.msg);
        } finally {
            if (conn != null) {
                try {
                    conn.disconnect();
                } catch (Exception ignore) {
                }
            }
        }
        return r;
    }

    private static String join(List<String> xs) {
        StringBuilder sb = new StringBuilder();
        for (String x : xs) {
            if (sb.length() > 0) sb.append("、");
            sb.append(x);
        }
        return sb.toString();
    }

    // ---------------------------------------------------------------- 共享服务（只认 /api/hello 与 /api/share）

    private static void serve(Socket s) {
        try {
            s.setSoTimeout(5000);
            BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), "UTF-8"));
            String line = in.readLine();
            if (line == null) {
                s.close();
                return;
            }
            String[] parts = line.split(" ");
            String method = parts.length > 0 ? parts[0] : "";
            String path = parts.length > 1 ? parts[1] : "";
            int len = 0;
            String h;
            while ((h = in.readLine()) != null && h.length() > 0) {
                if (h.toLowerCase().startsWith("content-length:")) {
                    try {
                        len = Integer.parseInt(h.substring(15).trim());
                    } catch (Exception ignore) {
                    }
                }
            }
            String body = "";
            if (len > 0) {
                char[] buf = new char[Math.min(len, 65536)];
                int off = 0;
                while (off < buf.length) {
                    int rd = in.read(buf, off, buf.length - off);
                    if (rd < 0) break;
                    off += rd;
                }
                body = new String(buf, 0, off);
            }
            JSONObject res;
            if ("/api/hello".equals(path)) {
                res = helloJson();
            } else if ("POST".equals(method) && "/api/share".equals(path)) {
                try {
                    res = receiveShare(new JSONObject(body));
                } catch (Exception e) {
                    res = new JSONObject();
                    try {
                        res.put("ok", false);
                        res.put("error", "JSON 解析失败");
                    } catch (Exception ignore) {
                    }
                }
            } else {
                res = new JSONObject();
                try {
                    res.put("ok", false);
                    res.put("error", "不支持的路径");
                } catch (Exception ignore) {
                }
            }
            byte[] out = res.toString().getBytes("UTF-8");
            OutputStream os = s.getOutputStream();
            os.write(("HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n"
                    + "Content-Length: " + out.length + "\r\nConnection: close\r\n\r\n").getBytes("UTF-8"));
            os.write(out);
            os.flush();
            os.close();
            s.close();
        } catch (Exception e) {
            try {
                s.close();
            } catch (Exception ignore) {
            }
        }
    }

    private static JSONObject helloJson() {
        JSONObject o = new JSONObject();
        try {
            o.put("ok", true);
            o.put("name", getUserName());
            o.put("id", getSelfId());
            o.put("platform", "android");
            o.put("share", sharePort > 0 ? sharePort : SHARE_PORT);
        } catch (Exception e) {
            // ignore
        }
        return o;
    }

    // ---------------------------------------------------------------- 状态

    public static JSONObject statusJson() {
        JSONObject o = new JSONObject();
        try {
            o.put("name", getUserName());
            o.put("id", getSelfId());
            o.put("share_port", sharePort);
            o.put("discover", discoverListen);
            o.put("lan_ip", WeakNet.I.lanIp());
            o.put("inbox_count", inboxCount());
            JSONArray ps = new JSONArray();
            for (Peer p : peers()) {
                JSONObject it = new JSONObject();
                it.put("id", p.id);
                it.put("name", p.name);
                it.put("ip", p.ip);
                it.put("platform", p.platform);
                it.put("share", p.share);
                it.put("age", (int) ((System.currentTimeMillis() - p.at) / 1000));
                ps.put(it);
            }
            o.put("peers", ps);
            JSONArray ib = new JSONArray();
            for (InboxItem it : inboxList()) {
                JSONObject j = new JSONObject();
                j.put("id", it.id);
                j.put("from", it.from);
                j.put("name", it.name);
                j.put("age", (int) ((System.currentTimeMillis() - it.at) / 1000));
                ib.put(j);
            }
            o.put("inbox", ib);
        } catch (Exception e) {
            // ignore
        }
        return o;
    }

    // ---------------------------------------------------------------- 工具

    private static String cleanName(String s, String def) {
        if (s == null) return def;
        String t = s.replace("\r", " ").replace("\n", " ").replace("\t", " ").trim();
        if (t.length() == 0) return def;
        return t.length() > 24 ? t.substring(0, 24) : t;
    }

    private static String newId() {
        return Long.toHexString(System.nanoTime())
                + Integer.toHexString((int) (Math.random() * 0xffff));
    }
}
