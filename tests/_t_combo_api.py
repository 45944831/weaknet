#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""弱网组合 HTTP 控制接口测试（Windows 端）。

背景：这轮把「弱网组合」补到了 Android 端已有的 /api/combos、/api/combo、
/api/combo/delete 三个接口上。Android 端本来就有，Windows 端补齐后，
同一套脚本在两端都能跑，不用为平台各写一份。

覆盖：
  1. GET  /api/combos          列表结构（id/name/loop/summary/steps）
  2. POST /api/combo            新建
  3. POST /api/combo            带 id 覆盖
  4. POST /api/preset  c:<id>   选中组合（未开限速 -> 只选中不生效）
  5. POST /api/combo/delete     删除
  6. 组合段里不能套组合（引擎侧兜底）
  7. 删不存在的组合
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, ROOT)

OK = 0
FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  OK   %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, extra))


def api(port, path, data=None, timeout=6):
    url = "http://127.0.0.1:%d%s" % (port, path)
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method=("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}


def main():
    # 组合与自定义场景共用一个存档文件：整套测试都放在临时目录里，
    # 绝不能碰到用户真实的 weaknet_presets.json
    tmp = tempfile.mkdtemp(prefix="wn_combo_api_")
    try:
        import weaknet as W
    except Exception as e:
        print("导入 weaknet 失败：", e)
        return 1
    print("数据目录：", tmp)
    # _preset_files() 是惰性求值并缓存的，所以必须在它第一次被调用前改掉候选路径
    W._PRESET_FILES = [Path(tmp) / "weaknet_presets.json"]

    import asyncio

    # 控制端口用个临时高位端口，避免和正在跑的 WeakNet 撞上
    port = 18899

    async def run():
        server = await asyncio.start_server(W.handle_ctrl, "127.0.0.1", port)
        try:
            await asyncio.sleep(0.2)

            # 控制接口跑在同一个事件循环里，HTTP 请求必须放到线程里发 ——
            # 同步请求会把事件循环堵死，服务端永远没机会回包（表现为超时）
            pool = ThreadPoolExecutor(max_workers=4)

            async def call(path, data=None):
                return await asyncio.get_running_loop().run_in_executor(
                    pool, api, port, path, data)

            print("== 1. GET /api/combos ==")
            st, d = await call("/api/combos")
            check("状态 200", st == 200, "st=%s" % st)
            check("返回 items 数组", isinstance(d.get("items"), list), str(d)[:80])

            print("== 2. POST /api/combo 新建 ==")
            st, d = await call("/api/combo", {
                "name": "接口组合",
                "loop": True,
                "steps": [{"key": "5g", "minutes": 0, "seconds": 30},
                          {"key": "4g", "minutes": 1, "seconds": 0}]})
            check("状态 200", st == 200, "st=%s %s" % (st, str(d)[:80]))
            cid = d.get("id")
            check("返回 id", bool(cid), str(cid))
            check("返回 name", d.get("name") == "接口组合", str(d.get("name")))
            check("返回段数 2", d.get("steps") == 2, str(d.get("steps")))

            print("== 3. GET 列表能看到刚建的 ==")
            st, d = await call("/api/combos")
            items = d.get("items") or []
            check("列表有 1 支", len(items) == 1, str(len(items)))
            it = items[0] if items else {}
            check("字段 name", it.get("name") == "接口组合", str(it.get("name")))
            check("字段 loop", it.get("loop") is True, str(it.get("loop")))
            check("字段 summary", isinstance(it.get("summary"), str) and it.get("summary"),
                  str(it.get("summary"))[:60])
            check("字段 steps 含 key/minutes/seconds",
                  bool(it.get("steps")) and "key" in it["steps"][0]
                  and "minutes" in it["steps"][0] and "seconds" in it["steps"][0],
                  str(it.get("steps"))[:80])

            print("== 4. POST /api/combo 带 id 覆盖 ==")
            st, d = await call("/api/combo", {
                "id": cid, "name": "接口组合改名", "loop": False,
                "steps": [{"key": "2g", "minutes": 0, "seconds": 10}]})
            check("状态 200", st == 200, "st=%s" % st)
            check("id 不变", d.get("id") == cid, "%s vs %s" % (d.get("id"), cid))
            check("名字已改", d.get("name") == "接口组合改名", str(d.get("name")))
            st, d = await call("/api/combos")
            check("列表仍是 1 支（覆盖不是新增）", len(d.get("items") or []) == 1,
                  str(len(d.get("items") or [])))

            print("== 5. POST /api/preset c:<id> 选中组合 ==")
            st, d = await call("/api/preset", {"key": "c:" + str(cid)})
            check("状态 200", st == 200, "st=%s %s" % (st, str(d)[:80]))
            check("kind=combo", d.get("kind") == "combo", str(d.get("kind")))
            check("未开限速 -> applied=false（只选中，不偷偷限速）",
                  d.get("applied") is False, str(d.get("applied")))
            check("preset_key 已记成 c:<id>", W.G.preset_key == "c:" + str(cid),
                  str(W.G.preset_key))

            print("== 6. 段里套组合被引擎拦下 ==")
            st, d = await call("/api/combo", {
                "name": "套娃组合", "loop": False,
                "steps": [{"key": "c:" + str(cid), "minutes": 0, "seconds": 5}]})
            # 嵌套段被剔除后一段都不剩 -> 引擎不落库，接口回 400 而不是假装存成功
            check("一段都留不下 -> 400（不当成保存成功）", st == 400, "st=%s" % st)
            check("错误信息说清原因", "没有一段能用的场景" in str(d.get("error", "")),
                  str(d.get("error")))
            st, d = await call("/api/combos")
            names = [x.get("name") for x in (d.get("items") or [])]
            check("嵌套段被丢弃，没存成 0 段组合", "套娃组合" not in names, str(names))
            bad = [x for x in (d.get("items") or []) if x.get("name") == "套娃组合"]
            check("套娃组合确实不在库里", not bad, str(bad))
            check("原来的组合没被覆盖掉",
                  [x.get("name") for x in (d.get("items") or [])] == ["接口组合改名"],
                  str(names))

            print("== 7. POST /api/combo/delete ==")
            st, d = await call("/api/combo/delete", {"id": cid})
            check("状态 200", st == 200, "st=%s" % st)
            check("ok=true", d.get("ok") is True, str(d.get("ok")))
            st, d = await call("/api/combos")
            check("列表已清空", len(d.get("items") or []) == 0,
                  str(len(d.get("items") or [])))

            print("== 8. 删不存在的组合 ==")
            st, d = await call("/api/combo/delete", {"id": "nope"})
            check("状态 200", st == 200, "st=%s" % st)
            check("ok=false", d.get("ok") is False, str(d.get("ok")))

            print("== 9. 组合段里不能引用另一个组合（引擎兜底）==")
            combo2, _all = W.upsert_combo("合法组合",
                                          [{"key": "5g", "minutes": 0, "seconds": 5}])
            normed = W.norm_combo_steps([
                {"key": "c:" + combo2["id"], "minutes": 0, "seconds": 5},
                {"key": "5g", "minutes": 0, "seconds": 5}])
            check("嵌套段被剔除", len(normed) == 1, str(len(normed)))
            check("留下的是合法那段", normed and normed[0].get("key") == "5g",
                  str(normed))
            pool.shutdown(wait=False)
        finally:
            server.close()
            try:
                await server.wait_closed()
            except Exception:
                pass

    try:
        asyncio.run(run())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("运行异常：", e)
        globals()["FAIL"] = FAIL + 1

    print()
    print("%d 项通过 / %d 项失败" % (OK, FAIL))
    print("全部通过 ✅" if FAIL == 0 else "有失败项 ❌")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
