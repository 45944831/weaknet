# -*- coding: utf-8 -*-
"""跨端终验：X-001 参数一致性 / X-003 快照还原 / X-004 手机→PC 混推。"""
import json
import socket
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import _t_dev_lib as D

WIN = "http://127.0.0.1:8899"
PHONE_SHARE = "172.16.3.82:8900"
PC_ID = "pc-xtest-0001"


def wapi(path, data=None, timeout=20):
    import urllib.request
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(WIN + path, data=body, method=("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def ulist(x):
    return x.get("items", []) if isinstance(x, dict) else (x or [])


def main():
    D.setup_forward()
    ok_all = True

    D.section("X-001 终验：xenv1 参数与 PC 端一致")
    pc_x = [p for p in ulist(wapi("/api/upresets")) if p.get("name") == "xenv1"]
    ph_x = [p for p in ulist(D.api("/api/upresets")) if p.get("name") == "xenv1"]
    if pc_x and ph_x:
        a, b = pc_x[0].get("profile", {}), ph_x[0].get("profile", {})
        same = (a.get("down_kbps") == b.get("down_kbps") and
                a.get("up_kbps") == b.get("up_kbps") and
                a.get("latency_down_ms") == b.get("latency_down_ms"))
        D.rec("X-001 手机侧参数与 PC 端逐字段一致", same,
              "PC down=%s up=%s lat=%s | 手机 down=%s up=%s lat=%s" % (
                  a.get("down_kbps"), a.get("up_kbps"), a.get("latency_down_ms"),
                  b.get("down_kbps"), b.get("up_kbps"), b.get("latency_down_ms")))
        ok_all &= same
    else:
        D.rec("X-001 两端都有 xenv1", False, "pc=%s ph=%s" % (bool(pc_x), bool(ph_x)))

    D.section("X-003 终验：xcombo2 (2) 已到手机且段还原")
    combos = D.api("/api/combos")
    got = [c for c in ulist(combos) if c.get("name").startswith("xcombo2")]
    if got:
        keys = [s.get("key", "") for s in got[0].get("steps", [])]
        ids = {p.get("id"): p for p in ulist(D.api("/api/upresets"))}
        restored = bool(keys) and all(k.startswith("u:") and k[2:] in ids and
                                      ids[k[2:]].get("name") == "xenv1" for k in keys)
        D.rec("X-003 段还原为本机自定义场景且指向 xenv1", restored, "steps=%s" % keys[:4])
        ok_all &= restored
    else:
        D.rec("X-003 手机侧存在 xcombo2", False, str([c.get("name") for c in ulist(combos)])[:120])

    D.section("X-004 终验：手机重建组合后向 PC 混推")
    ups3 = ulist(D.api("/api/upresets"))
    uid = ""
    for p in ups3:
        if p.get("name") == "真机自定义A":
            uid = p.get("id")
    if uid:
        D.api("/api/combo", {"name": "devcombo", "loop": False, "steps": [
            {"key": "u:%s" % uid, "minutes": 0, "seconds": 2},
            {"key": "5g", "minutes": 0, "seconds": 1}]})
    combos3 = ulist(D.api("/api/combos"))
    phone_ids = [p.get("id") for p in ups3 if p.get("name") == "真机自定义A"]
    phone_cids = [c.get("id") for c in combos3 if c.get("name") == "devcombo"]
    print("    ids=%s cids=%s" % (phone_ids, phone_cids))
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(json.dumps({"tag": "weaknet-discover-v1", "id": PC_ID, "name": "PCA",
                         "platform": "windows", "share": 8900, "ver": 2}).encode(),
             ("172.16.3.82", 18899))
    s.close()
    time.sleep(1.0)
    res = D.api("/api/share/push", {"peer": PC_ID, "ids": phone_ids,
                                    "combo_ids": phone_cids}, timeout=25)
    print("    回执:", json.dumps(res, ensure_ascii=False)[:200])
    time.sleep(1.2)
    entries = ulist(wapi("/api/inbox"))
    kinds = [(e.get("kind"), e.get("name")) for e in entries]
    hit_p = any(k == "preset" and n == "真机自定义A" for k, n in kinds)
    hit_c = any(k == "combo" and n == "devcombo" for k, n in kinds)
    D.rec("X-004 手机→PC 混推：收件箱同时出现 [preset] 与 [combo]", hit_p and hit_c, str(kinds)[:200])
    ok_all &= (hit_p and hit_c)
    for e in entries:
        if e.get("name") in ("真机自定义A", "devcombo"):
            wapi("/api/inbox/resolve", {"id": e.get("id"), "action": "copy"})
    time.sleep(0.6)
    names4 = [p.get("name") for p in ulist(wapi("/api/upresets"))]
    restored2 = any(n.startswith("真机自定义A") for n in names4)
    D.rec("X-004-b PC 另存组合后还原出被引用的自定义场景", restored2, str(names4[-5:]))
    ok_all &= restored2

    D.summary("跨端终验")


if __name__ == "__main__":
    main()
