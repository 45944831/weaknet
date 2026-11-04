package com.weaknet.app;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * 出厂默认：首次启动往「⭐ 我的自定义」写 5 支场景、往「弱网组合」写 5 支组合。
 *
 * 数据来自 spec/spec.json 的 defaults 段（由 tools/gen_spec.py 生成到
 * {@link SpecGen#DEFAULT_SCENES_JSON} / {@link SpecGen#DEFAULT_COMBOS_JSON}），
 * 与 Windows 端共用同一份规格，两端名字和参数不会漂移。
 *
 * 写进去的是**普通用户条目**：能改、能删、能共享。判重用名字而不是 id，
 * 一半写成功一半失败时下次重试不会重复插。完成一轮后打 seed 标记，
 * 用户把默认项全删了也不会在下次启动复活；想重新拿回这批默认项，
 * 升 spec 里的 defaults.seed 即可。
 *
 * 只引用内置预设 key 的组合段：自定义场景 id 是各端现编的，跨端对不上，
 * 写进组合会变成「已删除的场景」。
 */
public final class DefaultItems {
    private static final String PREF = "weaknet";
    private static final String KEY = "defaults_seed";

    private DefaultItems() {
    }

    /** 由 {@link MyCombos#bind} 调用：那时两个存储类都已绑好 Context。 */
    public static synchronized void seed(Context c) {
        if (c == null) return;
        Context app = c.getApplicationContext();
        SharedPreferences sp = app.getSharedPreferences(PREF, Context.MODE_PRIVATE);
        if (SpecGen.DEFAULT_SEED.equals(sp.getString(KEY, ""))) return;

        try {
            int scenes = seedScenes();
            int combos = seedCombos();
            sp.edit().putString(KEY, SpecGen.DEFAULT_SEED).apply();
            if (scenes + combos > 0) {
                WeakNet.I.log("config", "已载入出厂默认：" + scenes
                        + " 支自定义场景、" + combos + " 支弱网组合");
            }
        } catch (Exception e) {
            // 不写标记：下次启动再试一轮；已写入的条目靠名字判重，不会变成两份
            try {
                WeakNet.I.log("error", "载入出厂默认失败：" + e.getMessage());
            } catch (Exception ignored) {
                // 服务还没起来时日志通道可能没准备好，静默即可
            }
        }
    }

    private static int seedScenes() throws Exception {
        JSONArray arr = new JSONArray(SpecGen.DEFAULT_SCENES_JSON);
        int n = 0;
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.optJSONObject(i);
            if (o == null) continue;
            String name = o.optString("name", "").trim();
            if (name.length() == 0) continue;
            if (MyPresets.findByName(name) != null) continue;   // 已有同名：不覆盖用户数据
            if (MyPresets.save(null, name, o.optJSONObject("params")) != null) n++;
        }
        return n;
    }

    private static int seedCombos() throws Exception {
        JSONArray arr = new JSONArray(SpecGen.DEFAULT_COMBOS_JSON);
        int n = 0;
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.optJSONObject(i);
            if (o == null) continue;
            String name = o.optString("name", "").trim();
            if (name.length() == 0) continue;
            if (MyCombos.findByName(name) != null) continue;

            List<MyCombos.Step> steps = new ArrayList<MyCombos.Step>();
            JSONArray ss = o.optJSONArray("steps");
            if (ss != null) {
                for (int j = 0; j < ss.length(); j++) {
                    JSONObject so = ss.optJSONObject(j);
                    if (so == null) continue;
                    steps.add(new MyCombos.Step(so.optString("key", "").trim(),
                            so.optInt("minutes", 0), so.optInt("seconds", 0)));
                }
            }
            // 段合法性交给 ComboPlan.norm（MyCombos.save 内部会调），空壳不落库
            if (MyCombos.save(null, name, o.optBoolean("loop", false), steps) != null) n++;
        }
        return n;
    }
}
