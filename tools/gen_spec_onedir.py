# -*- coding: utf-8 -*-
"""生成 onedir 形态的 PyInstaller spec（P2-2 冷启动专项）。

背景：`--onefile` 每次启动都要把 21.4 MB 的 `_MEIxxxx` 解包到 %TEMP%，实测冷启动
中位 2.15 s，`≤2.0 s` 的目标只差这一步。onedir 把文件常驻在目录里，启动时不再解包。

为什么用生成器而不是直接提交 spec：`_build_gui/` 在 `.gitignore` 内（构建产物目录），
仓库存不下那份 spec；把生成逻辑放进 tools/ 才能被版本管理，换机器也能一键重建。

用法：
    py -3.10-64 tools/gen_spec_onedir.py                    # 写到 _build_gui/WeakNet_onedir.spec
    py -3.10-64 tools/gen_spec_onedir.py --out 路径.spec

构建与测量（需要显式决定发版时再跑，平时不打包）：
    py -3.10-64 -m PyInstaller --clean --noconfirm \\
        --distpath _build_gui/dist_onedir --workpath _build_gui/_work_onedir \\
        _build_gui/WeakNet_onedir.spec
    py -3.10-64 tools/perf_exe_measure.py --exe _build_gui/dist_onedir/WeakNet/WeakNet.exe --runs 5

产物布局：`dist_onedir/WeakNet/WeakNet.exe` + `_internal/`（含 spec/spec.json）。
运行时 `_MEIPASS` 指向 `_internal`，`weaknet.SPEC_PATH` 因此照旧解析得到，
无需改代码。
"""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # weaknet/

SPEC_TEMPLATE = '''# -*- mode: python ; coding: utf-8 -*-
# 由 tools/gen_spec_onedir.py 生成，请勿手改（改生成器再重跑）。
#
# 与 _build_gui/WeakNet.spec 的唯一区别：不把依赖塞进单个 EXE，而是
# EXE(exclude_binaries=True) + COLLECT 输出目录。启动时不再解包 21.4 MB 的 _MEI，
# 这是冷启动 2.15 s 降不下去的主因（P2-2）。
import os

_here = os.path.dirname(os.path.abspath(SPEC))
_root = os.path.normpath(os.path.join(_here, '..'))

a = Analysis(
    [os.path.join(_root, 'weaknet_gui.py')],
    pathex=[_root],
    binaries=[],
    # spec.json 必须进包：运行时按 _MEIPASS/spec/spec.json 读取
    datas=[(os.path.join(_root, 'spec', 'spec.json'), 'spec')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WeakNet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.normpath(os.path.join(_root, 'assets', 'weaknet.ico')),
    version=os.path.join(_here, 'version_info.txt'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WeakNet',
)
'''


def main():
    ap = argparse.ArgumentParser(description="生成 onedir 形态的 PyInstaller spec")
    ap.add_argument("--out", default=str(ROOT / "_build_gui" / "WeakNet_onedir.spec"),
                    help="输出路径（默认 _build_gui/WeakNet_onedir.spec）")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(SPEC_TEMPLATE, encoding="utf-8", newline="\n")
    print("已生成 %s（%d 字节）" % (out, len(SPEC_TEMPLATE.encode("utf-8"))))

    vi = out.parent / "version_info.txt"
    if not vi.exists():
        print("提示：%s 不存在，构建前先跑 tools/gen_exe_version.py" % vi)
    ico = ROOT / "assets" / "weaknet.ico"
    if not ico.exists():
        print("提示：图标缺失 %s" % ico)
    print("构建：py -3.10-64 -m PyInstaller --clean --noconfirm "
          "--distpath _build_gui/dist_onedir --workpath _build_gui/_work_onedir %s"
          % os.path.relpath(out, ROOT))


if __name__ == "__main__":
    main()
