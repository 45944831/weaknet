# -*- coding: utf-8 -*-
"""从 spec/spec.json 生成 Android 的 SpecGen.java（规格单一真源）。

用法（在 weaknet/ 下）：
    python tools/gen_spec.py          # 重新生成
    python tools/gen_spec.py --check  # 只校验生成物是否与 spec 一致（CI 用）

为什么生成 Java 而不是运行时读 JSON：
    APK 的资源打包流程要额外把 spec.json 塞进 assets，构建脚本和四处
    资源路径都要跟着改；生成一个纯数据的 Java 类没有这些依赖，
    且 --check 能在提交前发现「改了 spec 忘了重新生成」。

为什么生成物里几乎不用 org.json：
    _jw/run.sh 的纯 JDK 单测跑在 android.jar 的 stub 上，org.json 的构造器
    一律抛 "Stub!" —— 只要静态初始化里 new JSONObject，所有单测都会在
    Profile.<clinit> 阶段挂掉。所以预置参数生成为直接给 Profile 赋值的代码，
    参数表生成为平行数组；只有设备上才调用的 /api/params 才延迟解析一段
    JSON 文本（不参与类初始化）。
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "spec" / "spec.json"
OUT = ROOT / "android" / "src" / "com" / "weaknet" / "app" / "SpecGen.java"

# Profile 的字段名（参数 key 不总是与字段同名，这里显式映射）
FIELD_OF = {
    "latency_up_ms": "latencyUpMs",
    "latency_down_ms": "latencyDownMs",
    "jitter_ms": "jitterMs",
    "down_kbps": "downKbps",
    "up_kbps": "upKbps",
    "loss_pct": "lossPct",
    "stall_ms": "stallMs",
    "disconnect_pct": "disconnectPct",
    "timeout_pct": "timeoutPct",
    "dns_fail_pct": "dnsFailPct",
    "reject_pct": "rejectPct",
    "flap_sec": "flapSec",
    "flap_blackout_sec": "flapBlackoutSec",
}


def literal(value):
    """Python 值 → Java 字符串字面量（纯 ASCII，避免源码编码问题）"""
    return json.dumps(value, ensure_ascii=True)


def number(v):
    """Python 数值 → Java 字面量（整数不带 .0，浮点保留小数）"""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return repr(v)
    return str(v)


def build():
    raw = SPEC.read_bytes()
    spec = json.loads(raw.decode("utf-8"))
    # 与 weaknet.py 完全一致：对原始字节取哈希，格式变化也算变化
    h = hashlib.sha256(raw).hexdigest()[:8]

    presets = spec["presets"]
    params = spec["params"]
    notes = spec["notes"]
    param_notes = spec["param_notes"]
    combo = spec["combo"]
    share = spec["share"]

    keys = [p["key"] for p in presets]
    default_name = next(p["name"] for p in presets if p["key"] == "direct")

    parts = [combo["intro"]]
    for e in combo["examples"]:
        parts.append("\n".join([e["name"], e["desc"], e["effect"], e["steps"]]))
    parts.extend(combo["tips"])
    combo_doc = "\x1f".join(parts)

    L = []
    A = L.append
    A("package com.weaknet.app;")
    A("")
    A("import java.util.LinkedHashMap;")
    A("import java.util.Map;")
    A("")
    A("import org.json.JSONArray;")
    A("import org.json.JSONObject;")
    A("")
    A("/**")
    A(" * 自动生成：weaknet/tools/gen_spec.py ← spec/spec.json（hash %s）。请勿手改。" % h)
    A(" *")
    A(" * 预置场景 / 参数规格 / 文案 / 组合规则的唯一来源是 spec.json；")
    A(" * 改动规格后执行 python tools/gen_spec.py 重新生成本类。")
    A(" */")
    A("public final class SpecGen {")
    A("    private SpecGen() {")
    A("    }")
    A("")
    A("    public static final String SPEC_HASH = %s;" % literal(h))
    A("    public static final int SHARE_VER = %d;" % share["ver"])
    A("    public static final int PIN_LEN = %d;" % share["pin_len"])
    A("    public static final int INBOX_MAX = %d;" % share["inbox_max"])
    A("    public static final int SHARE_MAX_STEPS = %d;" % share["max_shared_steps"])
    A("    public static final int COMBO_MAX_STEPS = %d;" % combo["max_steps"])
    A("    public static final int COMBO_MAX_MINUTES = %d;" % combo["max_minutes"])
    A("    public static final int COMBO_MAX_SECONDS = %d;" % combo["max_seconds"])
    A("    public static final int COMBO_EXAMPLE_COUNT = %d;" % len(combo["examples"]))
    A("    public static final String COMBO_DOC = %s;" % literal(combo_doc))
    A("    public static final String DEFAULT_PROFILE_NAME = %s;" % literal(default_name))
    A("")
    A("    // ---------------------------------------------------------------- 预置场景")
    A("")
    A("    public static final String[] PRESET_KEYS = {%s};"
      % ", ".join(literal(k) for k in keys))
    A("")
    A("    /** key -> {name, short, cat, note} */")
    A("    private static final Map<String, String[]> PRESET_META =")
    A("            new LinkedHashMap<String, String[]>();")
    A("")
    A("    static {")
    for p in presets:
        meta = [
            p["name"],
            p.get("short") or p["name"],
            p["cat"],
            notes.get(p["key"], ""),
        ]
        A("        PRESET_META.put(%s, new String[]{%s});"
          % (literal(p["key"]), ", ".join(literal(v) for v in meta)))
    A("    }")
    A("")
    A("    public static boolean hasPreset(String key) {")
    A("        return key != null && PRESET_META.containsKey(key);")
    A("    }")
    A("")
    A("    private static String presetMeta(String key, int i) {")
    A("        String[] m = key == null ? null : PRESET_META.get(key);")
    A("        return m == null ? \"\" : m[i];")
    A("    }")
    A("")
    A("    public static String presetName(String key) {")
    A("        return presetMeta(key, 0);")
    A("    }")
    A("")
    A("    public static String presetShort(String key) {")
    A("        return presetMeta(key, 1);")
    A("    }")
    A("")
    A("    public static String presetCat(String key) {")
    A("        return presetMeta(key, 2);")
    A("    }")
    A("")
    A("    public static String presetNote(String key) {")
    A("        return presetMeta(key, 3);")
    A("    }")
    A("")
    A("    /**")
    A("     * 把预置覆盖的参数写进 Profile。")
    A("     *")
    A("     * 直接生成赋值语句而不是 JSON：纯 JDK 单测用 android.jar 的 org.json stub，")
    A("     * 一碰 new JSONObject 就抛 Stub!，会让 Profile 的静态初始化整体失败。")
    A("     */")
    A("    public static void applyPreset(String key, Profile p) {")
    A("        if (p == null || key == null) return;")
    first = True
    for p in presets:
        vals = p.get("params") or {}
        if not vals:
            continue
        cond = "if" if first else "} else if"
        A("        %s (%s.equals(key)) {" % (cond, literal(p["key"])))
        first = False
        for k, v in vals.items():
            field = FIELD_OF.get(k)
            if field is None:
                raise SystemExit("spec 里有未知参数 %s（请补 FIELD_OF 映射）" % k)
            A("            p.%s = %s;" % (field, number(v)))
    if not first:
        A("        }")
    A("    }")
    A("")
    A("    // ---------------------------------------------------------------- 参数表")
    A("")
    A("    public static final String[] PARAM_KEYS = {%s};"
      % ", ".join(literal(p["key"]) for p in params))
    A("")
    A("    private static final String[] PARAM_LABELS = {%s};"
      % ", ".join(literal(p["label"]) for p in params))
    A("    private static final String[] PARAM_UNITS = {%s};"
      % ", ".join(literal(p.get("unit", "")) for p in params))
    A("    private static final int[] PARAM_MAX = {%s};"
      % ", ".join(str(int(p["max"])) for p in params))
    A("    private static final int[] PARAM_DEFAULT = {%s};"
      % ", ".join(str(int(p["default"])) for p in params))
    A("")
    A("    /** /api/params 的原始条目（只在设备上解析，不参与类初始化） */")
    A("    private static final String PARAM_JSON = %s;"
      % literal(json.dumps(params, ensure_ascii=True, separators=(",", ":"))))
    A("")
    A("    private static final Map<String, String> PARAM_NOTES =")
    A("            new LinkedHashMap<String, String>();")
    A("    private static final Map<String, Integer> PARAM_IDX =")
    A("            new LinkedHashMap<String, Integer>();")
    A("")
    A("    static {")
    for i, p in enumerate(params):
        A("        PARAM_IDX.put(%s, Integer.valueOf(%d));" % (literal(p["key"]), i))
    for k, v in param_notes.items():
        A("        PARAM_NOTES.put(%s, %s);" % (literal(k), literal(v)))
    A("    }")
    A("")
    A("    private static int paramIdx(String key) {")
    A("        Integer i = key == null ? null : PARAM_IDX.get(key);")
    A("        return i == null ? -1 : i.intValue();")
    A("    }")
    A("")
    A("    public static String paramNote(String key) {")
    A("        String s = key == null ? null : PARAM_NOTES.get(key);")
    A("        return s == null ? \"\" : s;")
    A("    }")
    A("")
    A("    public static String paramLabel(String key) {")
    A("        int i = paramIdx(key);")
    A("        return i < 0 ? \"\" : PARAM_LABELS[i];")
    A("    }")
    A("")
    A("    public static String paramUnit(String key) {")
    A("        int i = paramIdx(key);")
    A("        return i < 0 ? \"\" : PARAM_UNITS[i];")
    A("    }")
    A("")
    A("    public static int paramMax(String key) {")
    A("        int i = paramIdx(key);")
    A("        return i < 0 ? 0 : PARAM_MAX[i];")
    A("    }")
    A("")
    A("    public static int paramDefault(String key) {")
    A("        int i = paramIdx(key);")
    A("        return i < 0 ? 0 : PARAM_DEFAULT[i];")
    A("    }")
    A("")
    A("    /** 参数编辑界面的行：{key, \"标签 (单位)\", 默认值文本}，顺序与 spec 一致 */")
    A("    public static String[][] fields() {")
    A("        String[][] out = new String[PARAM_KEYS.length][3];")
    A("        for (int i = 0; i < PARAM_KEYS.length; i++) {")
    A("            String unit = PARAM_UNITS[i];")
    A("            out[i] = new String[]{PARAM_KEYS[i],")
    A("                    unit.length() == 0 ? PARAM_LABELS[i]")
    A("                            : PARAM_LABELS[i] + \" (\" + unit + \")\",")
    A("                    String.valueOf(PARAM_DEFAULT[i])};")
    A("        }")
    A("        return out;")
    A("    }")
    A("")
    A("    /** /api/params 的响应体：{spec_hash, items:[...]}（仅设备侧调用） */")
    A("    public static JSONObject paramsJson() {")
    A("        JSONObject o = new JSONObject();")
    A("        try {")
    A("            o.put(\"spec_hash\", SPEC_HASH);")
    A("            o.put(\"items\", new JSONArray(PARAM_JSON));")
    A("        } catch (Exception e) {")
    A("            // ignore")
    A("        }")
    A("        return o;")
    A("    }")
    A("}")
    return "\n".join(L) + "\n", h


def main():
    text, h = build()
    if "--check" in sys.argv:
        old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if old == text:
            print("SpecGen.java 与 spec.json 一致（hash %s）" % h)
            return 0
        print("SpecGen.java 已过期：请运行 python tools/gen_spec.py（spec hash %s）" % h,
              file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print("已生成 %s（spec hash %s）" % (OUT, h))
    return 0


if __name__ == "__main__":
    sys.exit(main())
