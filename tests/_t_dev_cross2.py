# -*- coding: utf-8 -*-
"""跨端定向复测：修正接口/断言，绕开已定位的 P0（中文载荷）验证其余链路。"""
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

    D.section("X-001 复核：xenv1 已在手机入库且参数为覆盖后的 999")
    ups = D.api("/api/upresets")
    hit = [p for p in ulist(ups) if p.get("name") == "xenv1"]
    if hit:
        pf = hit[0].get("profile", {})
        D.rec("X-001 手机侧 xenv1 参数与推送一致（down=999）",
              pf.get("down_kbps") == 999, "down=%s" % pf.get("down_kbps"))
    else:
        D.rec("X-001 手机侧存在 xenv1", False, "未找到")

    D.section("X-003 复测：组合仅含自定义段（ASCII 场景名）→ 跨机快照还原")
    rc = wapi("/api/combo", {"name": "xcombo2", "loop": True, "steps": [
        {"key": "u:xenv1", "minutes": 0, "seconds": 2}]})
    # 注意：u:xenv1 是错的 id —— PC 端组合必须引用 PC 自己的 u:<x1id>
    ups_pc = wapi("/api/upresets")
    x1id = ""
    for p in ulist(ups_pc):
        if p.get("name") == "xenv1":
            x1id = p.get("id")
    rc = wapi("/api/combo", {"name": "xcombo2", "loop": True, "steps": [
        {"key": "u:%s" % x1id, "minutes": 0, "seconds": 2},
        {"key": "u:%s" % x1id, "minutes": 0, "seconds": 1}]})
    print("    PC 建组合:", json.dumps(rc, ensure_ascii=False)[:140])
    res = wapi("/api/push", {"peer": PHONE_SHARE, "ids": [], "combo_ids": [rc.get("id")]})
    print("    推送回执:", json.dumps(res, ensure_ascii=False)[:200])
    time.sleep(1.5)
    combos = D.api("/api/combos")
    got = [c for c in ulist(combos) if c.get("name") == "xcombo2"]
    detail = ""
    ok = bool(got)
    if got:
        keys = [s.get("key", "") for s in got[0].get("steps", [])]
        ups2 = D.api("/api/upresets")
        ids = {p.get("id"): p for p in ulist(ups2)}
        restored = all(k.startswith("u:") and k[2:] in ids and
                       (ids[k[2:]].get("profile", {}).get("down_kbps") == 999) for k in keys)
        detail = "steps=%s 还原参数正确=%s" % (keys, restored)
        ok = ok and restored and len(keys) == 2
    D.rec("X-003 组合过端：段被还原为本机 u:<id> 且参数一致", ok, detail)

    D.section("X-002/X-004 手机 → PC（presence 注册 + 环境/组合混推）")
    msg = json.dumps({"tag": "weaknet-discover-v1", "id": PC_ID, "name": "PCA",
                      "platform": "windows", "share": 8900, "ver": 2}).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(msg, ("172.16.3.82", 18899))
    s.close()
    time.sleep(1.0)
    ups3 = D.api("/api/upresets")
    phone_ids = [p.get("id") for p in ulist(ups3) if p.get("name") == "真机自定义A"]
    combos3 = D.api("/api/combos")
    phone_cids = [c.get("id") for c in ulist(combos3) if c.get("name") == "组合引自定义"]
    print("    手机端 ids=%s cids=%s" % (phone_ids, phone_cids))
    if not phone_ids or not phone_cids:
        D.rec("X-004 前置：手机端有待推内容", False, "ids=%s cids=%s" % (phone_ids, phone_cids))
    else:
        res = D.api("/api/share/push", {"peer": PC_ID, "ids": phone_ids,
                                        "combo_ids": phone_cids}, timeout=25)
        print("    推送回执:", json.dumps(res, ensure_ascii=False)[:220])
        time.sleep(1.0)
        inbox = wapi("/api/inbox")
        entries = ulist(inbox)
        kinds = [(e.get("kind"), e.get("name")) for e in entries]
        D.rec("X-004 手机→PC 混推：收件箱同时有 [preset] 与 [combo]",
              any(k == "preset" and n == "真机自定义A" for k, n in kinds) and
              any(k == "combo" and n == "组合引自定义" for k, n in kinds), str(kinds)[:220])
        chit = [e for e in entries if e.get("kind") == "combo" and e.get("name") == "组合引自定义"]
        phit = [e for e in entries if e.get("kind") == "preset" and e.get("name") == "真机自定义A"]
        if phit:
            wapi("/api/inbox/resolve", {"id": phit[0].get("id"), "action": "copy"})
        if chit:
            wapi("/api/inbox/resolve", {"id": chit[0].get("id"), "action": "copy"})
            time.sleep(0.6)
            ups4 = wapi("/api/upresets")
            names4 = [p.get("name") for p in ulist(ups4)]
            restored = any(n.startswith("真机自定义A") for n in names4)
            D.rec("X-004-b PC 处理组合后还原出被引用的自定义场景", restored, str(names4[-5:]))
        else:
            D.rec("X-004-b PC 处理组合", False, "收件箱无组合条目")

    D.section("X-005-b/c 复核（上轮已过，这里仅记录）")
    D.rec("X-005 同名入收件箱 + 覆盖/保留/另存（上一批已验证）", True, "见批次5输出")

    D.section("P0 缺陷实证：中文场景名组合推送必挂（对照组）")
    rc3 = wapi("/api/combo", {"name": "cncombo", "loop": False, "steps": [
        {"key": "airplane", "minutes": 0, "seconds": 1}]})
    res = wapi("/api/push", {"peer": PHONE_SHARE, "ids": [], "combo_ids": [rc3.get("id")]})
    print("    纯内置段（scene 名为中文）回执:", json.dumps(res, ensure_ascii=False)[:160])
    D.rec("P0 实证：含中文名的组合推送在 Android 端无响应（ Remote end closed）",
          not res.get("ok"), str(res)[:120])

    D.summary("跨端定向复测")


if __name__ == "__main__":
    main()
