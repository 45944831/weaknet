# -*- coding: utf-8 -*-
"""生成 PyInstaller 的 Windows 版本资源（_build_gui/version_info.txt）。

用法（在 weaknet/ 下）：
    python tools/gen_exe_version.py          # 生成
    python tools/gen_exe_version.py --check  # 校验是否与 version.py 同步（CI 用）

为什么不直接写在 WeakNet.spec 里：spec 只在打包时被读取，测试拿不到；
生成成文件后，`--check` 能像 SpecGen 一样在提交前发现版本号忘了同步。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from version import BUILD, VERSION  # noqa: E402

OUT = ROOT / "_build_gui" / "version_info.txt"


def build():
    parts = VERSION.split(".")
    nums = [int(x) for x in parts] + [0] * (4 - len(parts))
    tuple_text = "(%d, %d, %d, %d)" % tuple(nums[:4])
    text = """# 由 tools/gen_exe_version.py 从 weaknet/version.py 生成，请勿手改。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=%(vtuple)s,
    prodvers=%(vtuple)s,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'WeakNet'),
        StringStruct('FileDescription', 'WeakNet App 弱网测试工具'),
        StringStruct('FileVersion', '%(version)s'),
        StringStruct('InternalName', 'WeakNet'),
        StringStruct('OriginalFilename', 'WeakNet.exe'),
        StringStruct('ProductName', 'WeakNet'),
        StringStruct('ProductVersion', '%(version)s'),
        StringStruct('Comments', 'build %(build)d')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""" % {"vtuple": tuple_text, "version": VERSION, "build": BUILD}
    return text


def main():
    text = build()
    if "--check" in sys.argv:
        old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if old == text:
            print("version_info.txt 与 version.py 一致（%s / build %d）"
                  % (VERSION, BUILD))
            return 0
        print("version_info.txt 已过期：请运行 python tools/gen_exe_version.py",
              file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print("已生成 %s（%s / build %d）" % (OUT, VERSION, BUILD))
    return 0


if __name__ == "__main__":
    sys.exit(main())
