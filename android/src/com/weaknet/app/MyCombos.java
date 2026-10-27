package com.weaknet.app;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * 弱网组合：把几支场景（内置预设 / 自定义）排好队、每段给个时长，点一下按顺序自动播放。
 *
 * 一次「地铁通勤」本来就是好几段网络（进站 5G → 隧道掉 4G 弱网 → 站间断一下），
 * 手动跟着点不现实，所以排好队存下来，选中后由 {@link WeakNet#selectCombo} 推进。
 *
 * 存储方式与 {@link MyPresets} 一致（同一个 weaknet SharedPreferences、不同 key）：
 * 读改写都由系统保证原子性，不用处理并发写半个文件的问题。
 */
public class MyCombos {
    /**
     * 一段：哪支场景 + 跑多久。分/秒至少一个大于 0（见 {@link ComboPlan#hasDur}）。
     *
     * 字段与 copy 语义都放在纯数据的 {@link ComboStep} 里，这里只是继承一层，
     * 好让 ComboPlan 能在 PC 上跑单测（见 ComboStep 的说明）。
     */
    public static final class Step extends ComboStep {
        public Step() {
            super();
        }

        public Step(String key, int minutes, int seconds) {
            super(key, minutes, seconds);
        }

        @Override
        public Step copy() {
            return new Step(key, minutes, seconds);
        }

        @Override
        public Step blank() {
            return new Step();
        }
    }

    public static final class Item {
        public String id;
        public String name;
        public boolean loop;
        public List<Step> steps = new ArrayList<Step>();

        /** 播放要用快照：存档随时可能被删改，拿着原对象跑会遇到半改状态 */
        public Item copy() {
            Item it = new Item();
            it.id = id;
            it.name = name;
            it.loop = loop;
            for (Step s : steps) it.steps.add(s.copy());
            return it;
        }
    }

    /** presetKey 前缀：与内置预设、"u:&lt;id&gt;" 自定义场景区分开（值与 ComboStep.KEY_PREFIX 相同） */
    public static final String KEY_PREFIX = ComboStep.KEY_PREFIX;

    private static final String PREF = "weaknet";
    private static final String KEY = "user_combos";

    private static volatile Context ctx;

    public static void bind(Context c) {
        if (c != null) ctx = c.getApplicationContext();
    }

    public static List<Item> load() {
        List<Item> out = new ArrayList<Item>();
        String raw = read();
        if (raw == null || raw.length() == 0) return out;
        try {
            JSONArray a = new JSONArray(raw);
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.optJSONObject(i);
                if (o == null) continue;
                String name = o.optString("name", "").trim();
                if (name.length() == 0) continue;
                Item it = new Item();
                it.id = o.optString("id", "");
                if (it.id.length() == 0) it.id = newId();
                it.name = name;
                it.loop = o.optBoolean("loop", false);
                JSONArray ss = o.optJSONArray("steps");
                List<Step> raw2 = new ArrayList<Step>();
                if (ss != null) {
                    for (int j = 0; j < ss.length(); j++) {
                        JSONObject so = ss.optJSONObject(j);
                        if (so == null) continue;
                        Step s = new Step();
                        s.key = so.optString("key", "").trim();
                        s.minutes = so.optInt("minutes", 0);
                        s.seconds = so.optInt("seconds", 0);
                        raw2.add(s);
                    }
                }
                // 老存档/手改文件可能带着嵌套组合或超上限的段，这里统一收敛掉
                it.steps = ComboPlan.norm(raw2, null);
                if (it.steps.isEmpty()) continue;      // 全被丢掉的组合没必要显示
                out.add(it);
            }
        } catch (Exception e) {
            WeakNet.I.log("error", "读取弱网组合失败：" + e.getMessage());
        }
        return out;
    }

    public static Item find(String id) {
        if (id == null || id.length() == 0) return null;
        for (Item it : load()) {
            if (it.id.equals(id)) return it;
        }
        return null;
    }

    /**
     * 按名字找（大小写不敏感）——共享时要靠名字判「本地是不是已经有同名组合」，
     * 对方那边的 id 和我们没有可比性。
     */
    public static Item findByName(String name) {
        String target = name == null ? "" : name.trim().toLowerCase();
        if (target.length() == 0) return null;
        for (Item it : load()) {
            if (it.name.trim().toLowerCase().equals(target)) return it;
        }
        return null;
    }

    /**
     * 保存：给了 id 就覆盖那一条，否则新建。重名自动加序号。
     * 段的合法性一律交给 {@link ComboPlan#norm}（丢掉嵌套组合、没填时长的、超上限的）。
     *
     * 归一化后一段都不剩时（全是嵌套组合 / 全是空时长）**不落库**，返回 null。
     * 存一个 0 段的空壳组合毫无意义：播不了、点开是空的，
     * 还会在列表里占一行让人以为排好了。覆盖已有组合时原组合保持不变 ——
     * 传了个空壳不该把人家排好的那支抹掉。
     */
    public static Item save(String id, String name, boolean loop, List<Step> steps) {
        List<Item> items = load();
        String nm = name == null ? "" : name.trim();
        if (nm.length() == 0) nm = "弱网组合";
        if (nm.length() > 40) nm = nm.substring(0, 40);

        List<Step> normed = ComboPlan.norm(steps, null);
        if (normed.isEmpty()) {
            WeakNet.I.log("config", "组合「" + nm + "」没有一段可用，未保存");
            return null;
        }

        Item target = null;
        if (id != null && id.length() > 0) {
            for (Item it : items) {
                if (it.id.equals(id)) { target = it; break; }
            }
        }
        if (target == null) {
            target = new Item();
            target.id = newId();
            items.add(target);
        }
        target.name = uniqueName(items, target, nm);
        target.loop = loop;
        target.steps = normed;
        store(items);
        return target;
    }

    public static boolean delete(String id) {
        if (id == null || id.length() == 0) return false;
        List<Item> items = load();
        List<Item> left = new ArrayList<Item>();
        boolean removed = false;
        for (Item it : items) {
            if (it.id.equals(id)) { removed = true; continue; }
            left.add(it);
        }
        if (removed) store(left);
        return removed;
    }

    public static JSONArray toJsonArray() {
        JSONArray a = new JSONArray();
        try {
            for (Item it : load()) {
                JSONObject o = new JSONObject();
                o.put("id", it.id);
                o.put("name", it.name);
                o.put("loop", it.loop);
                o.put("summary", WeakNet.summaryOf(it));
                JSONArray ss = new JSONArray();
                for (Step s : it.steps) {
                    JSONObject so = new JSONObject();
                    so.put("key", s.key);
                    so.put("minutes", s.minutes);
                    so.put("seconds", s.seconds);
                    ss.put(so);
                }
                o.put("steps", ss);
                a.put(o);
            }
        } catch (Exception e) {
            WeakNet.I.log("error", "序列化弱网组合失败：" + e.getMessage());
        }
        return a;
    }

    public static JSONObject listJson() {
        JSONObject o = new JSONObject();
        try {
            o.put("items", toJsonArray());
        } catch (Exception e) {
            // ignore
        }
        return o;
    }

    private static String uniqueName(List<Item> items, Item self, String name) {
        for (Item it : items) {
            if (it == self) continue;
            if (it.name.equals(name)) {
                for (int n = 2; n < 100; n++) {
                    String cand = name + " (" + n + ")";
                    if (!taken(items, self, cand)) return cand;
                }
                return name + " " + System.currentTimeMillis();
            }
        }
        return name;
    }

    private static boolean taken(List<Item> items, Item self, String name) {
        for (Item it : items) {
            if (it == self) continue;
            if (it.name.equals(name)) return true;
        }
        return false;
    }

    private static void store(List<Item> items) {
        Context c = ctx;
        if (c == null) {
            WeakNet.I.log("error", "弱网组合未持久化：尚未绑定 Context");
            return;
        }
        try {
            JSONArray a = new JSONArray();
            for (Item it : items) {
                JSONObject o = new JSONObject();
                o.put("id", it.id);
                o.put("name", it.name);
                o.put("loop", it.loop);
                JSONArray ss = new JSONArray();
                for (Step s : it.steps) {
                    JSONObject so = new JSONObject();
                    so.put("key", s.key);
                    so.put("minutes", s.minutes);
                    so.put("seconds", s.seconds);
                    ss.put(so);
                }
                o.put("steps", ss);
                a.put(o);
            }
            SharedPreferences sp = c.getSharedPreferences(PREF, Context.MODE_PRIVATE);
            sp.edit().putString(KEY, a.toString()).apply();
            FloatWindow.invalidateSceneCache();   // 组合改名/删除后飘窗标题要重新反查
        } catch (Exception e) {
            WeakNet.I.log("error", "保存弱网组合失败：" + e.getMessage());
        }
    }

    private static String read() {
        Context c = ctx;
        if (c == null) return null;
        try {
            return c.getSharedPreferences(PREF, Context.MODE_PRIVATE).getString(KEY, "");
        } catch (Exception e) {
            return null;
        }
    }

    private static String newId() {
        return Long.toHexString(System.nanoTime())
                + Integer.toHexString((int) (Math.random() * 0xffff));
    }
}
