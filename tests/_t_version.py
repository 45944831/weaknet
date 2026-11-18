# -*- coding: utf-8 -*-
"""版本号 / EXE 元数据 / 构建产物的一致性校验（不需要打包）。

P1-5 要解决的问题：同一个版本号被写在四处（version.py、build.sh、Manifest、
PyInstaller 版本资源），任何一处忘了改，发布物就会自相矛盾 ——
exe 属性里写着 1.1.8、APK 却叫 1.1.9，用户没法判断装的是哪个。

这里把四处钉在一起；真正的成品校验（aapt2 dump badging）在 build.sh 末尾，
打包时执行，不依赖本测试。
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import weaknet as W  # noqa: E402

R = []


def rec(name, ok, detail=""):
    R.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""),
          flush=True)


def read(path):
    return Path(path).read_text(encoding="utf-8")


def check_python():
    v = W.VERSION
    b = W.BUILD
    rec("A1 version.py 版本号格式合法", bool(re.fullmatch(r"\d+\.\d+\.\d+", v)), v)
    rec("A2 build 号为正整数", isinstance(b, int) and b > 0, str(b))

    payload = W.status_payload()
    rec("A3 /api/status 载荷带 version / build",
        payload.get("version") == v and payload.get("build") == b,
        "version=%s build=%s" % (payload.get("version"), payload.get("build")))

    gui = read(os.path.join(ROOT, "weaknet_gui.py"))
    rec("A4 GUI 标题带版本号", 'App 弱网测试工具" % W.VERSION' in gui)


def check_android():
    v = W.VERSION
    b = W.BUILD

    bs = read(os.path.join(ROOT, "android", "build.sh"))
    mv = re.search(r'VERSION_NAME="\$\{VERSION_NAME:-([^}]*)\}"', bs)
    mc = re.search(r'VERSION_CODE="\$\{VERSION_CODE:-([^}]*)\}"', bs)
    rec("B1 build.sh 默认版本号与 version.py 一致",
        mv is not None and mc is not None
        and mv.group(1) == v and int(mc.group(1)) == b,
        "name=%s code=%s" % (mv.group(1) if mv else "?",
                             mc.group(1) if mc else "?"))

    manifest = read(os.path.join(ROOT, "android", "AndroidManifest.xml"))
    mv2 = re.search(r'android:versionName="([^"]+)"', manifest)
    mc2 = re.search(r'android:versionCode="([^"]+)"', manifest)
    rec("B2 AndroidManifest 与 version.py 一致",
        mv2 is not None and mc2 is not None
        and mv2.group(1) == v and int(mc2.group(1)) == b,
        "name=%s code=%s" % (mv2.group(1) if mv2 else "?",
                             mc2.group(1) if mc2 else "?"))

    # 构建脚本的失败可诊断性：javac/d8/aapt2 的退出码不能被管道吃掉
    rec("B3 build.sh 用 if 捕获 javac（set -e 下 $? 是死代码）",
        "if ! \"$JAVAC\"" in bs and "javac_rc=$?" not in bs)
    rec("B4 build.sh 不把 d8 / aapt2 的输出直接接管道",
        "com.android.tools.r8.D8" in bs and "build/d8.log" in bs
        and "aapt2 compile 失败" in bs and "aapt2 link 失败" in bs)
    rec("B5 build.sh 末尾断言成品版本号",
        "dump badging" in bs and "versionCode='$VERSION_CODE'" in bs)
    rec("B6 build.sh 断言 APK 内含 classes.dex",
        "'classes.dex' not in names" in bs)


def check_exe():
    """C1–C4 校验 PyInstaller 侧的版本资源。

    `_build_gui/` 是打包中间物、被 .gitignore 排除，CI 检出里必然不存在。
    早先在 CI 上 C1 被判失败、C2 直接 FileNotFoundError 把整个脚本打崩，
    于是功能回归全红，性能门禁根本没跑到。这里对「文件不在仓库」明确跳过，
    而不是伪装成通过或失败；本地打包后这些检查仍然照常执行。
    """
    vi_path = os.path.join(ROOT, "_build_gui", "version_info.txt")
    spec_path = os.path.join(ROOT, "_build_gui", "WeakNet.spec")
    skip = "构建产物不在仓库（CI 检出），跳过"

    if os.path.exists(vi_path):
        proc = subprocess.run([sys.executable,
                               os.path.join(ROOT, "tools", "gen_exe_version.py"),
                               "--check"], capture_output=True, text=True,
                              encoding="utf-8", errors="ignore")
        rec("C1 EXE 版本资源与 version.py 同步（gen_exe_version --check）",
            proc.returncode == 0, (proc.stdout + proc.stderr).strip())
    else:
        rec("C1 EXE 版本资源与 version.py 同步（gen_exe_version --check）",
            True, skip)

    if os.path.exists(spec_path):
        spec = read(spec_path)
        rec("C2 PyInstaller spec 带版本资源", "version_info.txt" in spec
            and re.search(r"version\s*=\s*os\.path\.join", spec) is not None)
        rec("C3 PyInstaller spec 把 spec.json 打进包",
            any(t in spec for t in ("spec/spec.json', 'spec'", "'spec', 'spec.json'")))
    else:
        rec("C2 PyInstaller spec 带版本资源", True, skip)
        rec("C3 PyInstaller spec 把 spec.json 打进包", True, skip)

    if os.path.exists(vi_path):
        vi = read(vi_path)
        nums = [int(x) for x in W.VERSION.split(".")] + [0]
        rec("C4 版本资源里的 filevers 与 version.py 一致",
            "filevers=(%d, %d, %d, %d)" % tuple(nums[:4]) in vi
            and "StringStruct('FileVersion', '%s')" % W.VERSION in vi)
    else:
        rec("C4 版本资源里的 filevers 与 version.py 一致", True, skip)


def _newest(paths):
    ts = [os.path.getmtime(p) for p in paths if os.path.exists(p)]
    return max(ts) if ts else 0.0


def _fmt(ts):
    return time.strftime("%m-%d %H:%M:%S", time.localtime(ts))


def check_artifacts():
    """C5/C6：产物不能比源码旧。

    `spec.json` 与 Java 源码都是**打进包里**的：只改源码不重打，所有单测仍然
    全绿，两端却跑着旧内容。v1.1.16 踩过两次 ——
      * 文案改完没重打：EXE 里的 spec.json 还是旧的（跨端校验查不出来，它比的是源码）；
      * `weaknet.py` 15:51 改过，`WeakNet.exe` 却是 15:43 的，靠人眼看 mtime 才发现。
    这里只比 mtime，不比内容：内容对不对由跨端一致性与 spec_hash 校验负责。
    """
    spec = os.path.join(ROOT, "spec", "spec.json")
    py_src = [os.path.join(ROOT, f) for f in os.listdir(ROOT) if f.endswith(".py")]

    java_src = [os.path.join(ROOT, "android", "AndroidManifest.xml"), spec]
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "android", "src")):
        java_src += [os.path.join(dirpath, f) for f in files if f.endswith(".java")]

    for tag, art, srcs in (
            ("C5 EXE 产物比源码新", os.path.join(ROOT, "WeakNet.exe"), py_src + [spec]),
            ("C6 APK 产物比源码新", os.path.join(ROOT, "android", "WeakNet.apk"), java_src)):
        if not os.path.exists(art):
            rec(tag, True, "产物不存在，跳过（仓库可不带产物）")
            continue
        a, n = os.path.getmtime(art), _newest(srcs)
        # 容差 2s：打包与保存源码可能落在同一秒
        ok = a + 2 >= n
        detail = "产物 %s / 最新源码 %s" % (_fmt(a), _fmt(n))
        if not ok:
            detail += "（产物旧了 %.0fs，必须重打）" % (n - a)
        rec(tag, ok, detail)


def main():
    check_python()
    check_android()
    check_exe()
    check_artifacts()
    bad = [x for x in R if not x[1]]
    print("\n%d 通过 / %d 失败" % (len(R) - len(bad), len(bad)), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
