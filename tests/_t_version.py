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
    proc = subprocess.run([sys.executable,
                           os.path.join(ROOT, "tools", "gen_exe_version.py"),
                           "--check"], capture_output=True, text=True,
                          encoding="utf-8", errors="ignore")
    rec("C1 EXE 版本资源与 version.py 同步（gen_exe_version --check）",
        proc.returncode == 0, (proc.stdout + proc.stderr).strip())

    spec = read(os.path.join(ROOT, "_build_gui", "WeakNet.spec"))
    rec("C2 PyInstaller spec 带版本资源", "version_info.txt" in spec
        and re.search(r"version\s*=\s*os\.path\.join", spec) is not None)
    rec("C3 PyInstaller spec 把 spec.json 打进包", "'spec', 'spec.json'" in spec)

    vi = read(os.path.join(ROOT, "_build_gui", "version_info.txt"))
    nums = [int(x) for x in W.VERSION.split(".")] + [0]
    rec("C4 版本资源里的 filevers 与 version.py 一致",
        "filevers=(%d, %d, %d, %d)" % tuple(nums[:4]) in vi
        and "StringStruct('FileVersion', '%s')" % W.VERSION in vi)


def main():
    check_python()
    check_android()
    check_exe()
    bad = [x for x in R if not x[1]]
    print("\n%d 通过 / %d 失败" % (len(R) - len(bad), len(bad)), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
