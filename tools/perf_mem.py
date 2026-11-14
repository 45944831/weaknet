# -*- coding: utf-8 -*-
"""P0-2 内存侧：tracemalloc 快照对比（stdlib，无需新依赖，不改正常运行路径）。

P0-2 要求「内存侧用 tracemalloc 快照对比，盯 DelayLine 4MB 积压、GUI 缓存」。
这里直接跑**产品里的 weaknet.DelayLine**，负载对齐它的背压上限
（MAX_BLOCKS=64 × 64KB ≈ 4MB），做「预热后稳态快照 → 多轮负载 → 快照 diff」，
并检测释放后是否回落（持续增长 = 泄漏嫌疑）。

用法
----
    py -3.10-64 tools/perf_mem.py                       # 默认 64 块 / 5 轮
    py -3.10-64 tools/perf_mem.py --rounds 10 --delay 0.002
    py -3.10-64 tools/perf_mem.py --json tests/perf/_perf_mem_snapshot.json

注意：tracemalloc 只在**本进程**生效，不能从外部 attach；要观察运行中的 EXE
请用 tools/perf_profile.py（py-spy）配合 WN_TRACEMALLOC 之外的方式。
"""
import argparse
import asyncio
import gc
import json
import os
import sys
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import weaknet as W  # noqa: E402


class _Transport:
    def get_write_buffer_size(self):
        return 0                    # 不触发 drain，专注分配路径


class _Writer:
    def __init__(self):
        self.transport = _Transport()
        self.total = 0

    def write(self, data):
        self.total += len(data)

    async def drain(self):
        return None


async def _workload(blocks, block_bytes, delay):
    """把 DelayLine 的队列路径跑满一轮（delay>0 才会入队，否则走零延迟快路径）。"""
    w = _Writer()
    dl = W.DelayLine(w, "tracemalloc-probe").start()
    data = b"x" * block_bytes
    try:
        for _ in range(blocks):
            await dl.submit(data, delay, 0)
        # 等投递协程把队列排空；有上界，卡住不会挂死
        for _ in range(4000):
            if dl.pending == 0 and dl.q.empty():
                break
            await asyncio.sleep(0.002)
        return w.total
    finally:
        dl.abort()                  # 走产品自己的收尾路径，取消投递协程
        await asyncio.sleep(0)


def _current():
    return tracemalloc.get_traced_memory()[0]


def main():
    ap = argparse.ArgumentParser(description="WeakNet 内存快照对比（tracemalloc）")
    ap.add_argument("--rounds", type=int, default=5, help="稳态后的负载轮数")
    ap.add_argument("--blocks", type=int, default=64, help="每轮块数（对齐 MAX_BLOCKS）")
    ap.add_argument("--block-kb", type=int, default=64, help="每块 KiB")
    ap.add_argument("--delay", type=float, default=0.002, help="每块延迟秒（>0 才入队）")
    ap.add_argument("--json", default="", help="结果 JSON 落盘路径")
    ap.add_argument("--max-growth-kb", type=int, default=512,
                    help="允许的最大常驻增长 KiB，超过判为泄漏嫌疑")
    args = ap.parse_args()

    tracemalloc.start(12)
    block = args.block_kb * 1024

    # 1) 预热：把 import / 首次分配的噪音排除在稳态快照之外
    asyncio.run(_workload(args.blocks, block, args.delay))
    gc.collect()
    snap1 = tracemalloc.take_snapshot()
    cur1 = _current()

    # 2) 多轮负载，每轮都应回到同一水位
    written = 0
    for _ in range(args.rounds):
        written += asyncio.run(_workload(args.blocks, block, args.delay))
    gc.collect()
    snap2 = tracemalloc.take_snapshot()
    cur2, peak = tracemalloc.get_traced_memory()

    diff = snap2.compare_to(snap1, "traceback")
    top = []
    for d in diff[:10]:
        top.append({
            "size_diff": d.size_diff,
            "count_diff": d.count_diff,
            "traceback": [str(f) for f in d.traceback.format()[:3]],
        })
    growth_kb = (cur2 - cur1) / 1024.0
    out = {
        "schema": 1,
        "python": sys.version.split()[0],
        "platform": os.name,
        "workload": {"rounds": args.rounds, "blocks": args.blocks,
                     "block_kb": args.block_kb, "delay_s": args.delay,
                     "max_backlog_mb": round(args.blocks * args.block_kb / 1024.0, 2)},
        "bytes_written": written,
        "current_before_kb": round(cur1 / 1024.0, 1),
        "current_after_kb": round(cur2 / 1024.0, 1),
        "peak_kb": round(peak / 1024.0, 1),
        "growth_kb": round(growth_kb, 1),
        "top_allocations": top,
        "verdict": "ok" if growth_kb <= args.max_growth_kb else "leak-suspected",
    }

    print("负载：%d 轮 × %d 块 × %d KiB（背压上限 %.2f MB），写出 %d B"
          % (args.rounds, args.blocks, args.block_kb,
             args.blocks * args.block_kb / 1024.0, written))
    print("常驻内存：%.1f KiB → %.1f KiB（增长 %.1f KiB），峰值 %.1f KiB"
          % (cur1 / 1024.0, cur2 / 1024.0, growth_kb, peak / 1024.0))
    print("判定：%s（阈值 %d KiB）" % (out["verdict"], args.max_growth_kb))
    for i, t in enumerate(top[:5], 1):
        print("  %d) %+d B / %+d 块  %s" % (i, t["size_diff"], t["count_diff"],
                                            t["traceback"][0][:100]))
    if args.json:
        p = Path(args.json)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
        print("已落盘：%s" % p)
    tracemalloc.stop()
    return 0 if out["verdict"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
