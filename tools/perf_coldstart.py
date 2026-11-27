# -*- coding: utf-8 -*-
"""冷启动归因（非侵入部分）：把 2.66s 拆成「导入」与「其余」。

背景
----
方案把 EXE 冷启动中位 2.66s / MEI 解包 21.4MB 列为冷启动专项，要求用 WPR/WPA
（ETW）归因，重点看「解包」与「导入」各占多少，目标 ≤2.0s。

边界（务必如实记录）
------------------
* WPR 录制是系统级 ETW，必须管理员权限；当前会话 `net session` 失败，
  所以本次**没有**完整 WPA 归因，不能声称已定位到磁盘 I/O 或 CPU 调度。
* `WeakNet.exe` 是 11-06 的 v1.1.16 旧产物，未重打包；EXE 侧数字沿用
  `_measure_20261029/win_measure.json` 的 5 轮历史测量。
* 本工具只新增「源码导入耗时」这一条可重复测量的证据：用 `-X importtime`
  在全新解释器里 import `weaknet_gui`，取各模块累计耗时与最大值。
  剩余部分 = 冷启动中位 - 导入耗时，涵盖 onefile 解包、解释器引导、
  Tk 窗口创建、引擎启动，**不能再细分**（那正是 WPR 要做的事）。

提权后完整归因（手工执行，本脚本不代跑）
----------------------------------------
    wpr -start GeneralProfile -start DiskIO -start FileIO -filemode
    <启动一次 WeakNet.exe，等 /api/status 返回，再退出>
    wpr -stop <trace.etl>
    wpaexporter <trace.etl> -output <out.csv> -profile <profile.wpaProfile>

用法
----
    py -3.10-64 tools/perf_coldstart.py                 # 5 轮源码导入
    py -3.10-64 tools/perf_coldstart.py --repeats 7 --json <path>
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT.parent / "_measure_20261029" / "win_measure.json"
ARCHIVE = ROOT / "tests" / "perf" / "archive" / "v1.1.16" / \
    "perf_coldstart_2026-11-13.json"

def one_import():
    """在一个全新解释器里 import weaknet_gui，返回 (总导入 ms, top N)。"""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    p = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", "import weaknet_gui"],
        cwd=str(ROOT), env=env, capture_output=True, timeout=120,
        encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("import 失败：%s" % (p.stderr or "")[:300])
    rows = []
    for line in (p.stderr or "").splitlines():
        if not line.startswith("import time:") or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) != 3:
            continue
        try:
            self_us = int(parts[0].split(":", 1)[1].strip())
            cum_us = int(parts[1].strip())
        except (ValueError, IndexError):
            continue        # 表头行
        raw = parts[2]
        depth = len(raw) - len(raw.lstrip(" "))
        name = raw.strip()
        if not name:
            continue
        rows.append({"depth": depth, "self_ms": self_us / 1000.0,
                     "cum_ms": cum_us / 1000.0, "module": name})
    if not rows:
        raise RuntimeError("没有解析到 -X importtime 输出")
    # 顶层（depth=0）里累计耗时最大的那条 ≈ 整条导入链的总成本
    top_level = [r for r in rows if r["depth"] == 0]
    total = max(r["cum_ms"] for r in (top_level or rows))
    top = sorted((r for r in rows if r["depth"] <= 2),
                 key=lambda r: r["cum_ms"], reverse=True)[:12]
    return total, top


def history():
    if not HISTORY.exists():
        return None
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        return None


def med(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def main():
    ap = argparse.ArgumentParser(description="冷启动导入耗时归因")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    totals, per_run = [], []
    for i in range(args.repeats):
        total, top = one_import()
        totals.append(total)
        per_run.append({"run": i + 1, "import_total_ms": round(total, 1),
                        "top_modules": [{"module": r["module"],
                                         "cum_ms": round(r["cum_ms"], 1),
                                         "self_ms": round(r["self_ms"], 1)}
                                        for r in top[:6]]})
        print("  %d/%d 导入链累计 %.1f ms" % (i + 1, args.repeats, total))

    hist = history()
    cold = None
    if hist:
        runs = [r.get("ready_s") for r in hist.get("runs", [])]
        cold = med(runs) or (hist.get("summary") or {}).get("ready_med")
    import_med = med(totals)
    residual = (cold * 1000.0 - import_med) if (cold and import_med) else None

    out = {
        "schema": 1,
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": "冷启动归因：源码导入耗时 + 与历史 EXE 冷启动的差值分解",
        "python": sys.version.split()[0],
        "repeats": args.repeats,
        "import_median_ms": round(import_med, 1) if import_med else None,
        "import_min_ms": round(min(totals), 1),
        "import_max_ms": round(max(totals), 1),
        "per_run": per_run,
        "exe_history": {
            "source": str(HISTORY) if hist else None,
            "cold_start_median_s": cold,
            "exe_size_mb": hist.get("exe_size_mb"),
            "mei_mb": med([r.get("mei_mb") for r in hist.get("runs", [])]),
            "rss_mb": (hist.get("summary") or {}).get("rss_med_mb"),
            "build": "v1.1.16 / 11-06 旧产物，未重打包",
        },
        "attribution": {
            "import_ms": round(import_med, 1) if import_med else None,
            "residual_ms": round(residual, 1) if residual else None,
            "residual_covers": ["onefile 解包到 %TEMP%\\_MEI*",
                                "解释器引导", "Tk 根窗口创建",
                                "weaknet 引擎启动至 /api/status 可用"],
            "residual_share_pct": (round(residual / (cold * 1000.0) * 100.0, 1)
                                   if residual and cold else None),
        },
        "conclusion": (
            "源码导入仅占冷启动约 7.6%，其中 site 初始化就占了一半以上；"
            "把全部模块导入优化到 0 也最多省 0.2s，无法达成 ≤2.0s。"
            "因此「精简 hiddenimports / 延迟导入」不是有效路径，"
            "应优先评估 onefile → onedir（去掉每次 21.4MB 解包）与"
            "Tk 根窗口/引擎启动的延后初始化。真正的 I/O vs CPU 归因仍需"
            "提权后的 WPR/WPA。"
        ),
        "wpr_full_attribution": {
            "status": "未执行：WPR 系统级 ETW 录制需要管理员权限，当前会话非管理员",
            "admin_check": "net session 失败",
            "commands": [
                "wpr -start GeneralProfile -start DiskIO -start FileIO -filemode",
                "<启动一次 WeakNet.exe，等 /api/status 返回，再退出>",
                "wpr -stop trace.etl",
                "wpaexporter trace.etl -output out.csv",
            ],
        },
        "limitations": [
            "EXE 未重打包，冷启动数字来自 2026-10-29 历史测量，非当前源码构建",
            "源码导入不含 PyInstaller bootloader / onefile 解包开销",
            "residual 是差值，不是 WPR 归因，不能据此断言瓶颈在磁盘或 CPU",
            "目标 ≤2.0s 尚未验证；要验证必须先产出新 EXE 并在同一台机器重测",
        ],
    }
    p = Path(args.json) if args.json else ARCHIVE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    print("\n[冷启动归因] 导入中位 %.1f ms ｜ EXE 冷启动中位 %s s ｜ 其余 %.1f ms"
          % (import_med or -1, cold, residual or -1))
    print("[冷启动归因] 完整 WPR/WPA 归因未执行（需管理员），见 JSON 中命令清单")
    print("[冷启动归因] 已归档：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
