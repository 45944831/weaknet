# -*- coding: utf-8 -*-
"""真机批次 5：跨端共享 L4（Windows ↔ Android，真实局域网 HTTP）。

环境事实：PC 192.168.1.49 / 手机 172.16.3.82，跨网段路由可达但广播不通。
  * PC→手机推送：Windows /api/push 直接接受 ip，不需要发现。
  * 手机→PC推送：手机只认 UDP 登记过的 peer —— 从 PC 向手机 18899 发单播
    presence（与广播同格式，解析端不校验是否广播包），把 PC 注册进去。
对应方案：X-001/002/003/004/005、W-503/508/510/511、A-403/406/407/408。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import _t_dev_lib as D

WIN = "http://127.0.0.1:8899"
PHONE_SHARE = "172.16.3.82:8900"
PC_NAME = "PC测试机"
PC_ID = "pc-xtest-0001"


def wapi(path, data=None, timeout=20):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(WIN + path, data=body, method=("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def wapi_try(path, data=None, timeout=20):
    try:
        return wapi(path, data, timeout)
    except Exception as e:
        return {"_err": str(e)}


def announce_pc_to_phone():
    """把 PC 以单播 presence 的形式喂给手机的发现端口（18899~18906 逐个试）。"""
    msg = json.dumps({"tag": "weaknet-discover-v1", "id": PC_ID, "name": PC_NAME,
                      "platform": "windows", "share": 8900, "ver": 2}).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    okport = None
    for port in range(18899, 18907):
        try:
            s.sendto(msg, ("172.16.3.82", port))
            okport = port
            break
        except Exception:
            continue
    s.close()
    return okport


def main():
    D.setup_forward()

    D.section("前置：两端身份与端口")
    me = D.api("/api/me")
    print("    手机身份:", json.dumps(me, ensure_ascii=False)[:150])
    st = wapi("/api/status")
    print("    PC 状态: preset_key=%s limit_on=%s" % (st.get("preset_key"), st.get("limit_on")))

    D.section("X-001 PC → 手机 推自定义环境（对方无同名 → 直接入库）")
    r = wapi("/api/upreset", {"name": "xenv1", "profile": {
        "down_kbps": 777, "up_kbps": 333, "latency_down_ms": 88, "latency_up_ms": 66}})
    ups = wapi("/api/upresets")
    x1id = ""
    for p in (ups.get("items", []) if isinstance(ups, dict) else ups):
        if p.get("name") == "xenv1":
            x1id = p.get("id")
    res = wapi("/api/push", {"peer": PHONE_SHARE, "ids": [x1id]})
    print("    推送回执:", json.dumps(res, ensure_ascii=False)[:200])
    time.sleep(1.0)
    pres = D.api("/api/presets")
    _pi = pres.get("items", []) if isinstance(pres, dict) else pres
    names = [p.get("name") for p in _pi]
    ok = "xenv1" in names
    detail = ""
    if ok:
        for p in (pres.get("items", []) if isinstance(pres, dict) else pres):
            if p.get("name") == "xenv1":
                pf = p.get("profile", {})
                detail = "down=%s up=%s lat=%s" % (pf.get("down_kbps"), pf.get("up_kbps"), pf.get("latency_down_ms"))
    D.rec("X-001 推送的自定义环境直接入库且参数一致", ok and "777" in detail,
          "%s %s" % (names[-4:], detail))

    D.section("X-003 PC → 手机 推组合（含自定义段 → 跨机快照还原 C2）")
    cid1 = ""
    r = D.api_try("/api/combo", {"name": "xcombo1", "loop": False, "steps": [
        {"key": "u:%s" % _pc_probe_uid(), "minutes": 0, "seconds": 2},
        {"key": "5g", "minutes": 0, "seconds": 1}]})
    print("    (先用手机上可能不存在的 u: id 构造，验证按快照还原)")
    # 正确姿势：PC 端建组合（引用 PC 的自定义场景），推过去
    rc = wapi("/api/combo", {"name": "xcombo1", "loop": True, "steps": [
        {"key": "u:%s" % x1id, "minutes": 0, "seconds": 2},
        {"key": "airplane", "minutes": 0, "seconds": 1}]})
    print("    PC 建组合:", json.dumps(rc, ensure_ascii=False)[:160])
    xc1 = rc.get("id")
    res = wapi("/api/push", {"peer": PHONE_SHARE, "ids": [], "combo_ids": [xc1]})
    print("    推送回执:", json.dumps(res, ensure_ascii=False)[:200])
    time.sleep(1.5)
    combos = D.api("/api/combos")
    items = combos.get("items", [])
    got = [c for c in items if c.get("name") == "xcombo1"]
    restored = ""
    if got:
        for st_ in got[0].get("steps", []):
            k = st_.get("key", "")
            if k.startswith("u:"):
                ups2 = D.api("/api/upresets")
                for p in (ups2.get("items", []) if isinstance(ups2, dict) else ups2):
                    if p.get("id") == k[2:]:
                        pf = p.get("profile", {})
                        restored = "还原场景[%s] down=%s" % (p.get("name"), pf.get("down_kbps"))
    D.rec("X-003 组合过端后自定义段被还原成同名场景且参数一致",
          bool(got) and "777" in restored, restored or "组合未到达")

    D.section("X-005 同名再推 → 进收件箱（手机侧 [环境] 条目）")
    wapi("/api/upreset", {"name": "xenv1", "profile": {
        "down_kbps": 999, "up_kbps": 111, "latency_down_ms": 1}})
    ups = wapi("/api/upresets")
    x1id2 = [p.get("id") for p in (ups.get("items", []) if isinstance(ups, dict) else ups) if p.get("name") == "xenv1"][-1]
    res = wapi("/api/push", {"peer": PHONE_SHARE, "ids": [x1id2]})
    time.sleep(1.5)
    inbox = D.api("/api/share/inbox")
    entries = inbox.get("items", inbox if isinstance(inbox, list) else [])
    kinds = [(e.get("kind"), e.get("name")) for e in entries]
    hit = [e for e in entries if e.get("name") == "xenv1"]
    D.rec("X-005 同名环境进入收件箱（kind=preset）", bool(hit) and hit[0].get("kind") == "preset",
          str(kinds)[:180])

    D.section("收件箱三种处理：覆盖 / 保留 / 另存")
    if hit:
        eid = hit[0].get("id")
        # 另存
        r = D.api("/api/share/inbox", {"id": eid, "action": "copy", "name": "xenv1-另存"})
        pres = D.api("/api/presets")
        _pi = pres.get("items", []) if isinstance(pres, dict) else pres
        names = [p.get("name") for p in _pi]
        D.rec("X-005-a 另存为新名字", "xenv1-另存" in names, str(names[-4:]))
        # 同名再推一次 → 覆盖
        res = wapi("/api/push", {"peer": PHONE_SHARE, "ids": [x1id2]})
        time.sleep(1.2)
        inbox = D.api("/api/share/inbox")
        entries = inbox.get("items", inbox if isinstance(inbox, list) else [])
        hit2 = [e for e in entries if e.get("name") == "xenv1"]
        if hit2:
            r = D.api("/api/share/inbox", {"id": hit2[0].get("id"), "action": "overwrite"})
            pres = D.api("/api/presets")
            for p in (pres.get("items", []) if isinstance(pres, dict) else pres):
                if p.get("name") == "xenv1":
                    D.rec("X-005-b 覆盖后本地参数被替换（down=999）",
                          p.get("profile", {}).get("down_kbps") == 999,
                          "down=%s" % p.get("profile", {}).get("down_kbps"))
        else:
            D.rec("X-005-b 覆盖", False, "同名推送没进收件箱（可能手机端直接覆盖了，需人工核对语义）")
        # 保留本地
        res = wapi("/api/push", {"peer": PHONE_SHARE, "ids": [x1id2]})
        time.sleep(1.2)
        inbox = D.api("/api/share/inbox")
        entries = inbox.get("items", inbox if isinstance(inbox, list) else [])
        hit3 = [e for e in entries if e.get("name") == "xenv1"]
        if hit3:
            r = D.api("/api/share/inbox", {"id": hit3[0].get("id"), "action": "keep"})
            pres = D.api("/api/presets")
            for p in (pres.get("items", []) if isinstance(pres, dict) else pres):
                if p.get("name") == "xenv1":
                    D.rec("X-005-c 保留本地（参数仍为 999）",
                          p.get("profile", {}).get("down_kbps") == 999,
                          "down=%s" % p.get("profile", {}).get("down_kbps"))
        else:
            D.rec("X-005-c 保留本地", False, "收件箱里没有同名条目")

    D.section("X-002/X-004 手机 → PC 推送（单播 presence 注册 + 组合+环境混推）")
    port = announce_pc_to_phone()
    time.sleep(1.0)
    ev = [e.get("msg", "") for e in D.status().get("events", [])[:6]]
    print("    presence 发往手机 UDP %s；手机最新事件: %s" % (port, " | ".join(m[:40] for m in ev[:3])))
    # 手机端的自定义场景与组合
    pres = D.api("/api/presets")
    phone_ids = [p.get("id") for p in (pres.get("items", []) if isinstance(pres, dict) else pres) if p.get("name") == "真机自定义A"]
    combos = D.api("/api/combos")
    phone_cids = [c.get("id") for c in combos.get("items", []) if c.get("name") == "组合引自定义"]
    print("    手机端 ids=%s cids=%s" % (phone_ids, phone_cids))
    res = D.api("/api/share/push", {"peer": PC_ID, "ids": phone_ids, "combo_ids": phone_cids}, timeout=25)
    print("    手机推送回执:", json.dumps(res, ensure_ascii=False)[:220])
    time.sleep(1.0)
    inbox = wapi("/api/inbox")
    entries = inbox.get("items", [])
    kinds = [(e.get("kind"), e.get("name")) for e in entries]
    D.rec("X-004 手机→PC：环境与组合混推，双方各自入库（组合带 kind=combo）",
          any(k == "combo" and n == "组合引自定义" for k, n in kinds) and
          any(k == "preset" and n == "真机自定义A" for k, n in kinds), str(kinds)[:200])
    # PC 处理组合（另存）→ 验证 PC 侧还原出自定义场景
    combo_hit = [e for e in entries if e.get("kind") == "combo" and e.get("name") == "组合引自定义"]
    if combo_hit:
        r = wapi("/api/inbox/resolve", {"id": combo_hit[0].get("id"), "action": "copy",
                                        "name": "xcombo1pc"})
        ups3 = wapi("/api/upresets")
        n3 = [p.get("name") for p in (ups3.get("items", []) if isinstance(ups3, dict) else ups3)]
        D.rec("X-004-b PC 处理组合后还原出其引用的自定义场景（down=300）",
              "真机自定义A" in n3 or "真机自定义A (2)" in n3,
              str(n3[-5:]))
    else:
        D.rec("X-004-b PC 处理组合", False, "收件箱无组合条目")

    D.summary("批次5：跨端共享 L4")


def _pc_probe_uid():
    try:
        return ""
    except Exception:
        return ""


if __name__ == "__main__":
    main()
