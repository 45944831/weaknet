# -*- coding: utf-8 -*-
"""`tests/_t_release.py`（发布真值校验）自身的测试。

重点不是「它能跑通」，而是**文档写错时它必须判失败**。上一轮遇到的真实现场：
README 下载表里两个资产的 SHA-256 全是错的，而 462 项回归全绿 —— 因为没有任何
断言碰过 README。所以这里用改坏的 README 逐条验证判定确实会红。
"""
import _t_release as T
from version import VERSION

# 夹具的默认版本必须跟着 version.py 走。写死 v1.1.20 的话，一旦发布新版
# （version.py 升到 v1.1.21），test_good_readme_passes 就会因为 R1/R4 变红 —— 
# 那不是被测代码坏了，是夹具自己落后了。这类「测试过期」比没有测试更坏。
VER = "v" + VERSION

FULL = "9229043b12d93705675a1634c1985acaf0e26620ba3d35be9faefbe931dd5511"
APK_FULL = "2a9901b698aebf8634e2f2417d7637d07c558bf4d7e088d9e4ef0df9c874720e"


def mk(ver=VER, tag=None, size="10.38 MB", short="9229043b12d93705",
       full=FULL):
    """拼一份最小可解析的 README。默认全部合法，靠参数逐个改坏。"""
    tag = tag or ver
    rows = [
        ("WeakNet.exe", "Windows GUI", size, short),
        ("WeakNet.apk", "Android 7.0+", "225 KB", "2a9901b698aebf86"),
    ]
    table = "".join(
        "| [`%s`](https://github.com/o/r/releases/download/%s/%s) | %s | %s | `%s` |\n"
        % (n, tag, n, desc, sz, sh) for n, desc, sz, sh in rows)
    detail = ("```text\n%s  WeakNet.exe\n%s  WeakNet.apk\n```" % (full, APK_FULL)
              if full else "（无）")
    return (
        "**%s** · Windows（EXE）\n\n"
        "| 文件 | 平台 | 大小 | SHA-256 |\n"
        "|---|---|---|---|\n"
        "%s\n"
        "<details>\n<summary>完整 SHA-256</summary>\n\n%s\n\n</details>\n"
    ) % (ver, table, detail)


def run_self(text):
    ver, rows, full, _repo = T.parse_readme_text(text)[1:]
    saved = T.R[:]
    T.R = []
    try:
        T.check_self(text, ver, rows, full)
        return [(n, ok) for n, ok, _d in T.R]
    finally:
        T.R = saved


def failed(results):
    return [n for n, ok in results if ok is False]


# ------------------------------------------------------------------ 解析

def test_parse_real_readme():
    _, ver, rows, full, repo = T.parse_readme()
    assert ver.startswith("v")
    assert len(rows) == 2
    names = sorted(r["name"] for r in rows)
    assert names == ["WeakNet.apk", "WeakNet.exe"]
    for r in rows:
        assert r["size"], "%s 的大小没解析出来" % r["name"]
        assert len(r["short"]) == 16
        assert r["url"].endswith("/" + r["name"])
    assert set(full) == set(names)
    # 不写死 owner：fork 出来的仓库也要能跑这套校验
    assert repo.endswith("/weaknet") and "/" in repo, repo


# ------------------------------------------------------------------ R 段：改坏必须红

def test_good_readme_passes():
    assert failed(run_self(mk())) == []


def test_hash_mismatch_fails():
    """短指纹与完整指纹对不上 —— 第八轮真实踩过的形态。"""
    bad = failed(run_self(mk(short="9d80713c00000000")))
    assert any(n.startswith("R8") for n in bad), bad


def test_full_hash_not_hex_fails():
    bad = failed(run_self(mk(full="z" * 64)))
    assert any(n.startswith("R7") for n in bad), bad


def test_missing_details_fails():
    bad = failed(run_self(mk(full="")))
    assert any(n.startswith("R7") for n in bad), bad


def test_short_hash_must_be_16():
    bad = failed(run_self(mk(short="9229043b")))
    assert any(n.startswith("R6") for n in bad), bad


def test_tag_drift_fails():
    """链接还指着上一个版本 —— README 改了版本号、下载表没跟着改。"""
    bad = failed(run_self(mk(tag="v1.1.19")))
    assert any(n.startswith("R4") for n in bad), bad


def test_readme_version_behind_code_fails():
    """发了新版、version.py 升了、README 首页没改（第八轮真实踩过）。"""
    bad = failed(run_self(mk(ver="v1.1.19", tag="v1.1.19")))
    assert any(n.startswith("R1") for n in bad), bad


def test_latest_download_url_fails():
    """用 /releases/latest/download/ 的链接会随发版漂移，和表里的指纹对不上。"""
    text = mk().replace("/releases/download/%s/" % VER, "/releases/latest/download/")
    bad = failed(run_self(text))
    assert any(n.startswith("R5") for n in bad), bad


# ------------------------------------------------------------------ 大小换算

def test_size_tolerance_matches_rounding():
    # 表格按 1024 进制、按写出来的小数位四舍五入
    assert T.size_matches((10.38, "MB", "10.38"), 10883116)[0] is True
    assert T.size_matches((225, "KB", "225"), 230436)[0] is True
    assert T.size_matches((10.4, "MB", "10.4"), 10883116)[0] is True
    assert T.size_matches((10.30, "MB", "10.30"), 10883116)[0] is False
    assert T.size_matches((226, "KB", "226"), 230436)[0] is False
    assert T.size_matches((1, "B", "1"), 1)[0] is True


# ------------------------------------------------------------------ L 段：真实仓库结构

def test_suite_list_matches_disk():
    """run.sh 的清单与磁盘上的 `_*Test.java` 双向对齐。

    少列一个 = 那个测试永远不跑且没人知道；多列一个 = 编译直接失败但报错难懂。
    """
    saved = T.R[:]
    T.R = []
    try:
        n = T.check_local()
        results = [(name, ok) for name, ok, _d in T.R]
    finally:
        T.R = saved
    assert n >= 20, "套件数异常：%d" % n
    assert failed(results) == [], failed(results)


def test_run_sh_has_no_duplicate_class():
    """同一个类写两遍会跑两遍，看上去「套件数」虚高。"""
    import re
    from pathlib import Path
    src = (Path(T.ROOT) / "android" / "_jw" / "run.sh").read_text(encoding="utf-8")
    listed = re.search(r"for T in ([^;]+); do", src).group(1).split()
    assert len(listed) == len(set(listed)), "run.sh 有重复的测试类"
