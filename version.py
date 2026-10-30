# -*- coding: utf-8 -*-
"""版本号的唯一真值（Windows 侧）。

- weaknet.py 的 /api/status、GUI 标题、PyInstaller 版本资源都读这里；
- Android 的版本号写不进 Python：aapt2 必须从 AndroidManifest.xml 取，
  build.sh 再从环境变量传给 --replace-version。两处的相等由
  tests/_t_version.py 断言，不靠人眼核对。
"""

VERSION = "1.1.12"
BUILD = 122
