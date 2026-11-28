# -*- coding: utf-8 -*-
"""发布真值校验：README 里的发布数字，由脚本从真值来源取回来逐项比对。

要解决的问题：README 下载表写着「10.38 MB / `9229043b12d93705`」，这两个值
在代码里根本不存在 —— 它们只能来自 Release API。第八轮审查就是这么发现表里
两个资产的 SHA-256 全错的（`9d80713c…` / `04e00ff5…`），而当时 462 项回归
全绿：没有任何一条断言碰过 README。同理，`docs/testing.md` 的「26 套测试类」
在加了第 27 套之后不会自动变，只能靠人记得改。

三段检查：

  R 自洽（离线）：版本号三处对齐、下载链接指向同一 tag、短指纹是全指纹前缀
  A 真值（联网）：Release API 的 tag / 资产 / 字节数 / digest 逐项比对
  L 结构（离线）：run.sh 的套件清单与磁盘上的 `_*Test.java` 双向对齐，以及
     `docs/testing.md` 声称的套件数与实际一致

L 段的价值不在「数字好看」：新增一个 `_XxxTest.java` 却忘了写进 run.sh，它会
永远不跑，而 CI 依然全绿 —— 这类静默漏测只能靠双向对齐发现。

模式：

  默认        只跑 R + L，不联网，任何环境都能跑
  --online    加跑 A。README 指向的 tag 尚未发布时记 SKIP：发版前先提升
              版本号是正常流程，不该让 CI 变红
  --strict    加跑 A，并要求该 tag 已发布且就是「最新正式版」。发版清单和
              `release: published` 事件用这个 —— 它能发现「发了新版但
              README 还指着上一个版本」
  --download  不用 API 的 digest 字段，直接下载资产算 sha256（API 不返回
              digest 的老 GitHub Enterprise，或想验证 digest 本身时用）

用法：
    python tests/_t_release.py
    python tests/_t_release.py --online
    GITHUB_TOKEN=... python tests/_t_release.py --strict

本机装了会改写 TLS 证书的代理（企业网、抓包工具）时，`--online` 会报
`CERTIFICATE_VERIFY_FAILED` —— 加 `--no-proxy` 直连，不要为此关掉证书校验。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
README = ROOT / "README.md"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(ROOT))
from version import VERSION  # noqa: E402  只读常量，不 import weaknet（那是 tkinter 入口）

# 表格行：| [`文件名`](url) | 平台说明 | 10.38 MB | `9229043b12d93705` |
ROW_RE = re.compile(r"^\|\s*\[`([^`]+)`\]\((https?://[^)\s]+)\)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|")
HASH_RE = re.compile(r"`([0-9a-f]{16,64})`")
FULL_RE = re.compile(r"^([0-9a-f]{64})\s{2,}(\S+)$")
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)\b")
TAG_RE = re.compile(r"/releases/(?:download|tag)/([^/?#]+)")
REPO_RE = re.compile(r"https://github\.com/([^/\s]+)/([^/\s?#]+)/releases")

UNIT = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}

R = []


def rec(name, ok, detail=""):
    """ok: True=PASS / False=FAIL / None=SKIP（跳过不算失败）。"""
    R.append((name, ok, detail))
    tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    print("%-4s %s%s" % (tag, name, (" | " + detail) if detail else ""), flush=True)


def parse_readme():
    return parse_readme_text(README.read_text(encoding="utf-8"))


def parse_readme_text(text):
    """纯解析：喂一段 README 文本，返回 (原文, 版本, 表格行, 完整指纹, 仓库 slug)。

    单独拆出来是为了让单测能直接喂「写错的 README」验证判定会失败 ——
    校验脚本自己不可能失败的话，它就只是个装饰。
    """
    m = re.search(r"^\*\*(v\d+\.\d+\.\d+)\*\*", text, re.M)
    ver = m.group(1) if m else ""

    rows = []
    for line in text.splitlines():
        mm = ROW_RE.match(line)
        if not mm:
            continue
        name, url, _desc, size_cell, hash_cell = mm.groups()
        sm = SIZE_RE.search(size_cell)
        hm = HASH_RE.search(hash_cell)
        rows.append({
            "name": name.strip(),
            "url": url.strip(),
            "size": (float(sm.group(1)), sm.group(2).upper(), sm.group(1)) if sm else None,
            "short": hm.group(1) if hm else "",
        })

    full = {}
    for line in text.splitlines():
        fm = FULL_RE.match(line.strip())
        if fm:
            full[fm.group(2)] = fm.group(1)

    rm = REPO_RE.search(text)
    repo = "%s/%s" % (rm.group(1), rm.group(2)) if rm else ""
    return text, ver, rows, full, repo


# ---------------------------------------------------------------- R 自洽（离线）

def check_self(text, ver, rows, full):
    want = "v" + VERSION
    rec("R1 README 首页版本 == version.py", ver == want,
        "README=%s / version.py=%s" % (ver or "缺失", want))

    rec("R2 README 有发布资产表", len(rows) >= 2, "%d 行" % len(rows))
    if not rows:
        return

    tags = [TAG_RE.search(r["url"]).group(1) if TAG_RE.search(r["url"]) else ""
            for r in rows]
    rec("R3 两条下载链接指向同一 tag", len(set(tags)) == 1 and "" not in tags,
        " / ".join(t or "无 tag" for t in tags))
    rec("R4 下载链接的 tag == version.py（与 R1 合起来即「链接不落后于代码」）",
        set(tags) == {want}, "%s vs %s" % (sorted(set(tags)), want))
    rec("R5 不用 /releases/latest/download（链接会随发版漂移）",
        "/releases/latest/download/" not in text)

    for r in rows:
        n = r["name"]
        ok_fmt = bool(r["short"]) and re.fullmatch(r"[0-9a-f]{16}", r["short"]) is not None
        rec("R6 %s 短指纹是 16 位小写 hex" % n, ok_fmt, r["short"] or "缺失")
        f = full.get(n, "")
        rec("R7 %s 有完整 SHA-256（<details> 块）" % n,
            re.fullmatch(r"[0-9a-f]{64}", f) is not None, (f or "缺失")[:16])
        rec("R8 %s 短指纹 == 完整指纹前 16 位" % n,
            bool(f) and bool(r["short"]) and f.startswith(r["short"]),
            "%s vs %s" % (r["short"] or "?", (f or "?")[:16]))


# ---------------------------------------------------------------- L 结构（离线）

def check_local():
    run_sh = ROOT / "android" / "_jw" / "run.sh"
    src = run_sh.read_text(encoding="utf-8")
    m = re.search(r"for T in ([^;]+); do", src)
    listed = m.group(1).split() if m else []
    pkg = ROOT / "android" / "_jw" / "com" / "weaknet" / "app"
    on_disk = sorted(p.stem for p in pkg.glob("_*Test.java"))

    miss_run = sorted(set(on_disk) - set(listed))
    miss_disk = sorted(set(listed) - set(on_disk))
    rec("L1 run.sh 覆盖磁盘上全部测试类（漏一个就永远不跑）",
        not miss_run, "未列入：%s" % (", ".join(miss_run) if miss_run else "无"))
    rec("L2 run.sh 没有指向不存在的测试类（写错名字会静默少跑）",
        not miss_disk, "文件不存在：%s" % (", ".join(miss_disk) if miss_disk else "无"))

    testing = (ROOT / "docs" / "testing.md").read_text(encoding="utf-8")
    m2 = re.search(r"(\d+)\s*套测试类", testing)
    claimed = int(m2.group(1)) if m2 else -1
    rec("L3 docs/testing.md 声称的套件数 == 实际",
        claimed == len(on_disk), "文档 %s / 实际 %d" % (claimed if claimed >= 0 else "?", len(on_disk)))
    return len(on_disk)


def note_history(n_now):
    """README 里各版本的「N 套件」是发版时的记录，不自动改，只提示偏差。"""
    text = README.read_text(encoding="utf-8")
    m = re.search(r"v%s 为[^\n]*?Java 纯 JDK \*\*(\d+)\*\* 套件" % re.escape(VERSION), text)
    if not m:
        return
    old = int(m.group(1))
    if old != n_now:
        print("提示 README 的 v%s 记录写「Java 纯 JDK %d 套件」，当前实际 %d 套；"
              "该行是发版记录，不自动改（新版发布时另起一行写新数）"
              % (VERSION, old, n_now), flush=True)


# ---------------------------------------------------------------- A 真值（联网）

def api_get(path, token, timeout=20, tries=3, no_proxy=False):
    """返回 (json, None) 或 (None, (code, msg))。404 不重试，5xx/网络错重试。"""
    url = "https://api.github.com" + path
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "weaknet-release-truth",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    # 显式空 ProxyHandler：本机若有会改写证书的 MITM 代理（企业网、抓包工具），
    # urllib 从环境变量拿到它之后必然校验失败，curl 却因为信任系统证书库而正常。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({} if no_proxy else None))
    last = (0, "未执行")
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            if e.code in (401, 403, 404):
                return None, (e.code, body)
            last = (e.code, body)
        except Exception as e:                      # noqa: BLE001 网络错误统一重试
            reason = getattr(e, "reason", e)
            if "CERTIFICATE_VERIFY_FAILED" in str(reason):
                return None, ("ssl", str(reason))
            last = (0, "%s: %s" % (type(e).__name__, reason))
        if i < tries - 1:
            time.sleep(1 + i)
    return None, last


def fetch_digest(url, timeout=120, no_proxy=False):
    h = hashlib.sha256()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({} if no_proxy else None))
    req = urllib.request.Request(url, headers={"User-Agent": "weaknet-release-truth"})
    with opener.open(req, timeout=timeout) as resp:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def size_matches(claim, actual):
    val, unit, raw = claim
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    tol = 0.5 * (10 ** -decimals) + 1e-9
    got = actual / UNIT[unit]
    return abs(got - val) <= tol, "%.4f %s" % (got, unit)


def explain_error(err):
    """把网络/鉴权错误翻成能直接照做的下一步。"""
    if not err:
        return "未知错误"
    code, msg = err
    if code == "ssl":
        env = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
        extra = "（当前 https_proxy=%s）" % env if env else ""
        return ("TLS 证书校验失败%s：本机代理多半改写了证书。"
                "加 --no-proxy 直连再试；不要为此关掉证书校验" % extra)
    if code == 403:
        return ("403：未带 token 时匿名限流只有 60 次/小时，"
                "设 GITHUB_TOKEN 后重试（CI 里已注入）。原文：%s" % msg)
    if code == 401:
        return "401：token 无效或已过期。原文：%s" % msg
    return "失败 %s：%s" % (code, msg)


def check_release(repo, ver, rows, full, strict, token, download, no_proxy=False):
    if not repo:
        rec("A1 README 能解析出仓库 slug", False, "没找到 github.com/<owner>/<repo>/releases")
        return
    print("仓库 %s，目标 tag %s" % (repo, ver or "（空）"), flush=True)

    def get(path):
        return api_get(path, token, no_proxy=no_proxy)

    data, err = get("/repos/%s/releases/tags/%s" % (repo, ver))
    if data is None and err and err[0] == 404:
        msg = "tag %s 尚未发布（API 404）" % ver
        if strict:
            rec("A1 Release 已发布", False, msg + "；--strict 要求先发布再校验")
        else:
            rec("A1 Release 已发布", None, msg + "；发版前提升版本号属正常流程，跳过资产比对")
        return
    if data is None:
        rec("A1 读取 Release API", False, explain_error(err))
        return

    rec("A1 Release 可读", True, "tag=%s draft=%s prerelease=%s"
        % (data.get("tag_name"), data.get("draft"), data.get("prerelease")))
    rec("A2 目标 tag 不是草稿", not data.get("draft"), str(data.get("draft")))

    assets = {a.get("name"): a for a in (data.get("assets") or [])}
    missing = [r["name"] for r in rows if r["name"] not in assets]
    rec("A3 表格里的每个文件都在 Release 里", not missing,
        "缺：%s" % (", ".join(missing) if missing else "无"))
    extra = sorted(set(assets) - set(r["name"] for r in rows))
    if extra:
        print("提示 Release 里还有表格未列出的资产：%s" % ", ".join(extra), flush=True)

    for r in rows:
        n = r["name"]
        a = assets.get(n)
        if not a:
            continue
        if r["size"]:
            ok, got = size_matches(r["size"], int(a.get("size") or 0))
            claim = "%s %s" % (r["size"][2], r["size"][1])
            rec("A4 %s 字节数与表格一致" % n, ok, "表格 %s / 实际 %s（%d B）"
                % (claim, got, int(a.get("size") or 0)))
        else:
            rec("A4 %s 表格里有文件大小" % n, False, "大小单元格解析失败")

        got_hash = ""
        digest = a.get("digest") or ""
        if digest.startswith("sha256:"):
            got_hash = digest[len("sha256:"):].lower()
        if not got_hash and download:
            try:
                got_hash = fetch_digest(a["browser_download_url"], no_proxy=no_proxy)
            except Exception as e:                  # noqa: BLE001
                rec("A5 %s 取回真实 SHA-256" % n, False, "下载失败：%s" % e)
                continue
        if not got_hash:
            rec("A5 %s 取回真实 SHA-256" % n, None,
                "该 API 不返回 digest；加 --download 直接下载校验")
            continue

        rec("A5 %s 短指纹与资产 digest 一致" % n,
            bool(r["short"]) and got_hash.startswith(r["short"]),
            "文档 %s / 资产 %s" % (r["short"] or "?", got_hash[:16]))
        f = full.get(n, "")
        rec("A6 %s 完整 SHA-256 与资产 digest 一致" % n,
            bool(f) and f == got_hash, "文档 %s / 资产 %s" % ((f or "?")[:16], got_hash[:16]))

        want_url = "https://github.com/%s/releases/download/%s/%s" % (repo, ver, n)
        rec("A7 %s 下载链接就是该资产的地址" % n, r["url"] == want_url,
            r["url"] if r["url"] != want_url else "一致")

    if strict:
        latest, err2 = get("/repos/%s/releases/latest" % repo)
        if latest is None:
            rec("A8 README 指向最新正式版", False, "读 latest 失败：%s" % (err2,))
        else:
            tag = latest.get("tag_name")
            rec("A8 README 指向最新正式版（发了新版别忘了改 README）",
                tag == ver, "latest=%s / README=%s" % (tag, ver))


def main():
    ap = argparse.ArgumentParser(description="README 发布数字的真值校验")
    ap.add_argument("--online", action="store_true", help="联网比对 Release API")
    ap.add_argument("--strict", action="store_true",
                    help="联网 + 要求 tag 已发布且是最新正式版（发版用）")
    ap.add_argument("--download", action="store_true",
                    help="不使用 API digest，直接下载资产算 SHA-256")
    ap.add_argument("--repo", default="", help="owner/repo，默认从 README 解析")
    ap.add_argument("--token", default="", help="GitHub token，默认读 GITHUB_TOKEN/GH_TOKEN")
    ap.add_argument("--no-proxy", action="store_true",
                    help="忽略 https_proxy 直连（本机代理改写证书时用）")
    args = ap.parse_args()

    token = args.token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    online = args.online or args.strict

    text, ver, rows, full, repo = parse_readme()
    check_self(text, ver, rows, full)
    n = check_local()
    note_history(n)

    if online:
        check_release(args.repo or repo, ver, rows, full, args.strict, token,
                      args.download, no_proxy=args.no_proxy)
    else:
        print("未联网：加 --online 比对 Release 资产（--strict 供发版使用）", flush=True)

    bad = [x for x in R if x[1] is False]
    skip = [x for x in R if x[1] is None]
    print("\n%d 通过 / %d 失败 / %d 跳过" % (len(R) - len(bad) - len(skip), len(bad), len(skip)),
          flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
