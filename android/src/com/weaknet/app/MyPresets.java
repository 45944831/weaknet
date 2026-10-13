package com.weaknet.app;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * 用户自定义场景（控制台里的「⭐ 我的自定义」栏）：命名保存 / 删除 / 读取。
 *
 * 存 SharedPreferences 而不是单独的文件：Android 的内部存储路径外界拿不到，
 * 用户重装 App 才丢；SharePrefs 的读写由系统保证原子性，省得处理并发写半个文件的问题。
 */
public class MyPresets {
    public static final class Item {
        public String id;
        public String name;
        public Profile profile;
    }

    private static final String PREF = "weaknet";
    private static final String KEY = "user_presets";

    private static volatile Context ctx;

    /** 绑定 Context（服务一启动就调，避免首次请求时还拿不到上下文） */
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
                JSONObject prof = o.optJSONObject("profile");
                it.profile = Profile.custom(prof == null ? new JSONObject() : prof);
                it.profile.name = name;
                out.add(it);
            }
        } catch (Exception e) {
            WeakNet.I.log("error", "读取自定义场景失败：" + e.getMessage());
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
     * 保存：给了 id 就覆盖那一条，否则新建。重名会自动加序号，避免两条同名分不清。
     */
    public static Item save(String id, String name, JSONObject prof) {
        List<Item> items = load();
        String nm = name == null ? "" : name.trim();
        if (nm.length() == 0) nm = "自定义";
        if (nm.length() > 40) nm = nm.substring(0, 40);

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
        Profile p = Profile.custom(prof == null ? new JSONObject() : prof);
        p.name = target.name;
        target.profile = p;
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
                o.put("profile", it.profile.toJson());
                a.put(o);
            }
        } catch (Exception e) {
            WeakNet.I.log("error", "序列化自定义场景失败：" + e.getMessage());
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
        for (int i = 0; i < items.size(); i++) {
            Item it = items.get(i);
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
        for (int i = 0; i < items.size(); i++) {
            Item it = items.get(i);
            if (it == self) continue;
            if (it.name.equals(name)) return true;
        }
        return false;
    }

    private static void store(List<Item> items) {
        Context c = ctx;
        if (c == null) {
            WeakNet.I.log("error", "自定义场景未持久化：尚未绑定 Context");
            return;
        }
        try {
            JSONArray a = new JSONArray();
            for (Item it : items) {
                JSONObject o = new JSONObject();
                o.put("id", it.id);
                o.put("name", it.name);
                o.put("profile", it.profile.toJson());
                a.put(o);
            }
            SharedPreferences sp = c.getSharedPreferences(PREF, Context.MODE_PRIVATE);
            sp.edit().putString(KEY, a.toString()).apply();
        } catch (Exception e) {
            WeakNet.I.log("error", "保存自定义场景失败：" + e.getMessage());
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
