# -*- coding: utf-8 -*-
"""P1-1 规格单一真源的校验：spec.json → Python / Android 生成物 → 运行时 API。

三层各验一遍，缺一层都可能出现「文件改了、某一端还在用旧值」：

A  规格文件本身：hash 与 weaknet.py 载入的一致，各表内容对得上；
B  安卓生成物：SpecGen.java 解析出的常量 / 预置参数 / 参数表 / 文案与 spec 逐项相同，
   并且 tools/gen_spec.py --check 认为它没有过期；
C  运行时：Windows 控制服务的 /api/params 与 /api/status 带上同一个 spec_hash。

Android 端的运行时校验需要已安装的 App + adb forward，
不在默认路径里做（本测试不打包、不装 APK）：
    adb forward tcp:18899 tcp:8899
    python tests/_t_spec_parity.py --android http://127.0.0.1:18899
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:                     # Windows 控制台默认 GBK，生成器的报错信息里有非 GBK 字符
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import weaknet as W  # noqa: E402

SPEC_PATH = Path(ROOT) / "spec" / "spec.json"
SPEC_SRC_PATH = Path(ROOT) / "android" / "src" / "com" / "weaknet" / "app" / "SpecGen.java"
PROFILE_SRC_PATH = Path(ROOT) / "android" / "src" / "com" / "weaknet" / "app" / "Profile.java"

# 与 tools/gen_spec.py 的 FIELD_OF 互逆
KEY_OF_FIELD = {
    "latencyUpMs": "latency_up_ms",
    "latencyDownMs": "latency_down_ms",
    "jitterMs": "jitter_ms",
    "downKbps": "down_kbps",
    "upKbps": "up_kbps",
    "lossPct": "loss_pct",
    "stallMs": "stall_ms",
    "disconnectPct": "disconnect_pct",
    "timeoutPct": "timeout_pct",
    "dnsFailPct": "dns_fail_pct",
    "rejectPct": "reject_pct",
    "flapSec": "flap_sec",
    "flapBlackoutSec": "flap_blackout_sec",
}

R = []


def rec(name, ok, detail=""):
    R.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""),
          flush=True)


def read(path):
    return Path(path).read_text(encoding="utf-8")


def java_unescape(s):
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == "u" and i + 5 < len(s):
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"',
                        "\\": "\\", "'": "'"}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def const_string(src, name):
    m = re.search(
        r'public\s+static\s+final\s+String\s+' + re.escape(name)
        + r'\s*=\s*((?:"(?:[^"\\]|\\.)*"\s*\+\s*)*"(?:[^"\\]|\\.)*")\s*;',
        src, re.S)
    if not m:
        return None
    lits = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
    return java_unescape("".join(lits))


def const_int(src, name):
    m = re.search(r'public\s+static\s+final\s+int\s+' + re.escape(name)
                  + r'\s*=\s*(-?\d+)\s*;', src)
    return int(m.group(1)) if m else None


def string_array(src, name):
    m = re.search(re.escape(name) + r'\s*=\s*\{(.*?)\};', src, re.S)
    if not m:
        return None
    return [java_unescape(x) for x in re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))]


def int_array(src, name):
    m = re.search(re.escape(name) + r'\s*=\s*\{(.*?)\};', src, re.S)
    if not m:
        return None
    return [int(x) for x in re.findall(r"-?\d+", m.group(1))]


def parse_preset_meta(src):
    """SpecGen 里 PRESET_META.put("key", new String[]{name, short, cat, note});"""
    out = {}
    pat = re.compile(r'PRESET_META\.put\("([^"]+)",\s*new String\[\]\{'
                     r'((?:"(?:[^"\\]|\\.)*"\s*,?\s*)+)\}\);')
    for m in pat.finditer(src):
        lits = [java_unescape(x) for x in
                re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2))]
        if len(lits) == 4:
            out[m.group(1)] = lits
    return out


def parse_apply_preset(src):
    """SpecGen.applyPreset 的 if/else-if 分支 → {key: {param_key: 值}}"""
    out = {}
    pat = re.compile(r'(?:if|else if)\s*\("([^"]+)"\.equals\(key\)\)\s*\{(.*?)\n        \}',
                     re.S)
    for m in pat.finditer(src):
        key = m.group(1)
        vals = {}
        for f, v in re.findall(r'p\.([A-Za-z_]+)\s*=\s*(-?[\d.]+);', m.group(2)):
            pk = KEY_OF_FIELD.get(f)
            if pk:
                vals[pk] = float(v)
        if vals:
            out[key] = vals
    return out


def check_static(spec):
    raw = SPEC_PATH.read_bytes()
    h = hashlib.sha256(raw).hexdigest()[:8]
    rec("A1 weaknet.py 的 SPEC_HASH 与文件一致", W.SPEC_HASH == h,
        "%s vs %s" % (W.SPEC_HASH, h))

    presets = spec["presets"]
    rec("A2 预置 key 集合一致", sorted(W.PRESETS) == sorted(p["key"] for p in presets),
        "%d 支" % len(presets))
    bad = []
    for p in presets:
        name, cat, params = W.PRESETS[p["key"]]
        if name != p["name"] or cat != p["cat"]:
            bad.append(p["key"] + ":name/cat")
        if {k: float(v) for k, v in params.items()} != \
                {k: float(v) for k, v in (p.get("params") or {}).items()}:
            bad.append(p["key"] + ":params")
    rec("A3 预置名 / 分类 / 参数逐支一致", not bad, ",".join(bad) or "%d 支全部一致" % len(presets))

    rec("A4 参数表（key/label/unit/max/default）一致",
        [(p["key"], p["label"], p.get("unit", ""), p["max"], p["default"])
         for p in W.PARAM_SPECS]
        == [(p["key"], p["label"], p.get("unit", ""), p["max"], p["default"])
            for p in spec["params"]])
    rec("A5 上限表由参数表派生", W.PROF_LIMITS ==
        {p["key"]: int(p["max"]) for p in spec["params"]})
    rec("A6 场景文案一致", W.PRESET_NOTES == {k: v for k, v in spec["notes"].items()
                                             if v})
    rec("A7 参数文案一致", W.PARAM_NOTES == spec["param_notes"])
    combo = spec["combo"]
    parts = [combo["intro"]]
    for e in combo["examples"]:
        parts.append("\n".join([e["name"], e["desc"], e["effect"], e["steps"]]))
    parts.extend(combo["tips"])
    rec("A8 组合文档由 combo 段拼出", W.COMBO_DOC == "\x1f".join(parts))
    rec("A9 组合与共享常量一致",
        (W.MAX_COMBO_STEPS, W.STEP_MAX_MINUTES, W.STEP_MAX_SECONDS) ==
        (spec["combo"]["max_steps"], spec["combo"]["max_minutes"],
         spec["combo"]["max_seconds"])
        and (W.SHARE_VER, W.PIN_LEN, W.MAX_COMBO_STEPS) ==
        (spec["share"]["ver"], spec["share"]["pin_len"],
         spec["combo"]["max_steps"]))

    # 源码里不该再有第二份中文场景名（验收标准：搜「电梯」只命中 spec 与文档）
    prof = read(PROFILE_SRC_PATH)
    rec("A10 Profile.java 不再硬编码场景名 / 文案",
        "电梯" not in prof and "Wi-Fi 良好" not in prof and "PARAM_NOTES.put" not in prof)


def check_generated(spec):
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_spec.py"),
                           "--check"], capture_output=True, text=True,
                          encoding="utf-8", errors="ignore")
    rec("B1 SpecGen.java 与 spec.json 同步（gen_spec --check）",
        proc.returncode == 0, (proc.stdout + proc.stderr).strip().splitlines()[-1]
        if (proc.stdout + proc.stderr).strip() else "")

    src = read(SPEC_SRC_PATH)
    rec("B2 SPEC_HASH 一致", const_string(src, "SPEC_HASH") == W.SPEC_HASH,
        const_string(src, "SPEC_HASH"))

    keys = [p["key"] for p in spec["presets"]]
    rec("B3 PRESET_KEYS 顺序一致", string_array(src, "PRESET_KEYS") == keys)

    meta = parse_preset_meta(src)
    bad = []
    for p in spec["presets"]:
        got = meta.get(p["key"])
        want = [p["name"], p.get("short") or p["name"], p["cat"],
                spec["notes"].get(p["key"], "")]
        if got != want:
            bad.append(p["key"])
    rec("B4 预置名 / 短名 / 分类 / 文案一致", not bad,
        ",".join(bad) or "%d 支全部一致" % len(meta))

    applied = parse_apply_preset(src)
    bad = []
    for p in spec["presets"]:
        want = {k: float(v) for k, v in (p.get("params") or {}).items()}
        if applied.get(p["key"], {}) != want:
            bad.append(p["key"])
    rec("B5 applyPreset 的参数值与 spec 一致", not bad,
        ",".join(bad) or "%d 支全部一致" % len(applied))

    params = spec["params"]
    rec("B6 参数表平行数组一致",
        string_array(src, "PARAM_KEYS") == [p["key"] for p in params]
        and string_array(src, "PARAM_LABELS") == [p["label"] for p in params]
        and string_array(src, "PARAM_UNITS") == [p.get("unit", "") for p in params]
        and int_array(src, "PARAM_MAX") == [int(p["max"]) for p in params]
        and int_array(src, "PARAM_DEFAULT") == [int(p["default"]) for p in params])

    rec("B7 组合常量一致",
        (const_int(src, "COMBO_MAX_STEPS"), const_int(src, "COMBO_MAX_MINUTES"),
         const_int(src, "COMBO_MAX_SECONDS"), const_int(src, "COMBO_EXAMPLE_COUNT")) ==
        (spec["combo"]["max_steps"], spec["combo"]["max_minutes"],
         spec["combo"]["max_seconds"], len(spec["combo"]["examples"])))
    rec("B8 组合文档逐字一致", const_string(src, "COMBO_DOC") == W.COMBO_DOC)
    rec("B9 共享常量一致",
        (const_int(src, "SHARE_VER"), const_int(src, "PIN_LEN"),
         const_int(src, "INBOX_MAX"), const_int(src, "SHARE_MAX_STEPS")) ==
        (spec["share"]["ver"], spec["share"]["pin_len"],
         spec["share"]["inbox_max"], spec["share"]["max_shared_steps"]))


def http_json(host, port, path):
    s = socket.create_connection((host, port), timeout=8)
    try:
        s.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\n\r\n"
                   % (path, host, port)).encode("latin1"))
        buf = b""
        while True:
            part = s.recv(65536)
            if not part:
                break
            buf += part
    finally:
        s.close()
    head, _, body = buf.partition(b"\r\n\r\n")
    try:
        status = int(head.split(b" ")[1])
    except Exception:
        status = 0
    try:
        return status, json.loads(body.decode("utf-8"))
    except Exception:
        return status, body.decode("latin1")


async def check_api(host, port, label):
    try:
        st, obj = await asyncio.to_thread(http_json, host, port, "/api/params")
        ok = st == 200 and isinstance(obj, dict) and obj.get("spec_hash") == W.SPEC_HASH
        items = obj.get("items") if isinstance(obj, dict) else None
        ok = ok and isinstance(items, list) and len(items) == len(W.PARAM_SPECS)
        rec("%s /api/params 携带同一 spec_hash 与参数表" % label, ok,
            "status=%s hash=%s items=%s" % (st, obj.get("spec_hash")
                                            if isinstance(obj, dict) else "?",
                                            len(items) if isinstance(items, list) else "?"))
    except Exception as e:
        rec("%s /api/params 携带同一 spec_hash 与参数表" % label, False, repr(e))
    try:
        st, obj = await asyncio.to_thread(http_json, host, port, "/api/status")
        ok = st == 200 and isinstance(obj, dict) and obj.get("spec_hash") == W.SPEC_HASH
        rec("%s /api/status 携带同一 spec_hash" % label, ok,
            "status=%s hash=%s" % (st, obj.get("spec_hash")
                                   if isinstance(obj, dict) else "?"))
    except Exception as e:
        rec("%s /api/status 携带同一 spec_hash" % label, False, repr(e))


async def run_windows_api():
    tmp = tempfile.mkdtemp(prefix="wnspec_")
    W._settings_file = lambda: Path(tmp) / "weaknet_settings.json"
    W.ensure_identity()
    srv = await asyncio.start_server(W.handle_ctrl, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    try:
        await check_api("127.0.0.1", port, "Win")
    finally:
        srv.close()
        await srv.wait_closed()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--android", metavar="URL",
                    help="已 adb forward 的 Android 控制地址，如 http://127.0.0.1:18899")
    args = ap.parse_args()

    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    check_static(spec)
    check_generated(spec)

    asyncio.run(run_windows_api())
    if args.android:
        from urllib.parse import urlparse
        u = urlparse(args.android)
        asyncio.run(check_api(u.hostname or "127.0.0.1", u.port or 8899, "APK"))

    bad = [x for x in R if not x[1]]
    print("\n%d 通过 / %d 失败" % (len(R) - len(bad), len(bad)), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
