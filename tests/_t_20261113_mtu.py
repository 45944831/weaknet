# -*- coding: utf-8 -*-
"""2026-11-13 MTU 实验档真机 A/B（量化验收）。

验的是「16 KB 级缓冲对齐」到底能不能在真机上落地：
  默认档 1500  -> MSS 1460，下游读缓冲 1460 B
  实验档 16384 -> MSS 16344，下游读缓冲 16344 B（方案说的 16 KB 级）

切档不需要重新打包：`POST /api/vpn {"enable":true,"mtu":16384}` 在建 tun 之前落档，
只接受 MTU_TIERS={1280,1500,9000,16384}，响应里直接回 `mtu`/`mss` 供核对。
（不能用 `am start-foreground-service -e mtu 16384`：VpnNetService 声明了
BIND_VPN_SERVICE 权限，adb shell 起服务会被系统拒掉，实测踩到过。）

判定口径：两档各拉同一个 16 MB 定长 Range 段，正文 sha256 必须一致（大 MTU 不能
改变数据），吞吐与 cpu_ms/MB 只做**同机同次**对照 —— WiFi 本底波动很大
（同一天测到过 10.6 / 37.6 / 49.3 / 51.8 Mbps），所以结论只看同批次内的比值。
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _t_apk_tun_real as T

PKG = "com.weaknet.app"


def log_mtu():
    out = T.sh("logcat -d -v time 2>/dev/null | grep -E 'mtu: ' | tail -5")
    return out.strip()


def vpn_state():
    return bool(T.api("/api/status").get("vpn_active"))


def stop_vpn():
    T.api("/api/vpn", {"enable": False})
    for _ in range(20):
        if not vpn_state():
            return True
        time.sleep(0.4)
    return False


def start_vpn(mtu=None):
    """mtu=None 走默认档；否则 POST /api/vpn {"enable":true,"mtu":N} 切档后启动。

    为什么不用 `am start-foreground-service -e mtu N`：
    VpnNetService 在清单里声明了 android:permission="BIND_VPN_SERVICE"，
    adb shell(uid 2000) 起服务会被系统拒掉（Error: Requires permission
    android.permission.BIND_VPN_SERVICE），实测踩到过。只能走控制接口。
    """
    if mtu is None:
        T.api("/api/vpn", {"enable": True})
    else:
        r = T.api("/api/vpn", {"enable": True, "mtu": mtu})
        if r.get("error"):
            return {"ok": False, "resp": r}
        resp = {"mtu": r.get("mtu"), "mss": r.get("mss")}
    # 就绪判据（复测 §3.6）：rebuild 是异步的，vpn_active=true 不代表新档的
    # tun 已建立——必须轮询 /api/status 的 mtu_ready（effective_mtu == 请求档）
    # 才能开测，否则产出「标签正确、前提错误」的脏样本（F 段实证过 −32% 假差异）。
    for _ in range(40):
        st = T.api("/api/status")
        if st.get("vpn_active") and st.get("mtu_ready"):
            return resp if mtu is not None else True
        time.sleep(0.4)
    return False


def tun_iface():
    for cmd in ("ifconfig tun0 2>/dev/null | head -3",
                "ip addr show tun0 2>/dev/null | head -4"):
        out = T.sh(cmd).strip()
        if out:
            return out.replace("\n", " | ")
    return "(读不到 tun0 接口)"


def measure(tag, nbytes):
    s0 = (T.api("/api/status").get("stats") or {})
    sz, done_ms, first_ms, _wall = T.probe(nbytes, tag=tag, t=240)
    body, sha, _ = T.pull_body(tag, nbytes)
    s1 = (T.api("/api/status").get("stats") or {})
    got = len(body) if body else 0
    return {
        "bytes": got,
        "sha256": sha,
        "done_ms": done_ms,
        "first_byte_ms": first_ms,
        "kbps": round((got * 8 / 1024.0) / (done_ms / 1000.0), 1) if got and done_ms else 0,
        "app_down_delta": (s1.get("down", 0) or 0) - (s0.get("down", 0) or 0),
    }


def main():
    nbytes = 16 * 1024 * 1024
    out_path = os.path.join(HERE, "..", "_tmp", "mtu_ab_20261113.json")
    out = {"when": time.strftime("%Y-%m-%d %H:%M:%S")}

    T.api("/api/preset", {"key": "direct"})
    T.api("/api/run", {"enable": False})
    time.sleep(0.6)

    print("[1/3] 默认档 1500 …", flush=True)
    stop_vpn()
    T.sh("logcat -c")
    out["vpn_1500"] = start_vpn(None)
    time.sleep(1.5)
    out["iface_1500"] = tun_iface()
    out["measure_1500"] = measure(1, nbytes)
    out["log_1500"] = log_mtu()

    print("[2/3] 实验档 16384 …", flush=True)
    stop_vpn()
    time.sleep(1.0)
    T.sh("logcat -c")
    out["vpn_16384"] = start_vpn(16384)
    time.sleep(1.5)
    out["iface_16384"] = tun_iface()
    out["measure_16384"] = measure(2, nbytes)
    out["log_16384"] = log_mtu()

    print("[3/3] 非法档 1501 应被拒 …", flush=True)
    stop_vpn()
    time.sleep(1.0)
    T.sh("logcat -c")
    r = T.api("/api/vpn", {"enable": True, "mtu": 1501})
    out["reject_1501"] = r
    time.sleep(2.5)
    out["vpn_1501"] = vpn_state()
    out["log_1501"] = log_mtu()

    # 收尾：回默认档并关闭
    stop_vpn()
    T.api("/api/preset", {"key": "direct"})
    T.api("/api/run", {"enable": False})

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n写入 %s" % os.path.abspath(out_path), flush=True)
    print("1500  -> %s kbps, sha %s…" % (out["measure_1500"]["kbps"],
                                        (out["measure_1500"]["sha256"] or "")[:12]))
    print("16384 -> %s kbps, sha %s…" % (out["measure_16384"]["kbps"],
                                        (out["measure_16384"]["sha256"] or "")[:12]))
    print("16264 日志：%s" % out["log_16384"].replace("\n", " / ")[:200])
    print("1501 日志：%s" % out["log_1501"].replace("\n", " / ")[:160])
    return 0


if __name__ == "__main__":
    sys.exit(main())
