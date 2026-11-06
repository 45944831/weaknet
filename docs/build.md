# 构建与测试

从源码构建 Windows EXE / Android APK，以及自动化回归的跑法。

---

## Android APK

依赖：JDK 8+、Android SDK（platform + build-tools）。脚本会自动探测路径，也可用环境变量指定：

```bash
cd android
export JAVA_HOME=/path/to/jdk
export ANDROID_HOME=/path/to/android-sdk
bash build.sh
```

> 坑（v1.1.16）：本仓库自带 `android/sdk`（platforms/android-34 + build-tools/34.0.0），
> 而系统 SDK 可能只有 build-tools、没有 `platforms/`。这种情况下脚本会在 `ANDROID_JAR`
> 探测处的 `ls | sort | tail` 管道里因 `pipefail` **静默退出（exit 2，无任何错误输出）**。
> 直接把 `ANDROID_HOME` 指向仓库自带 SDK 即可：`cd android && ANDROID_HOME="$PWD/sdk" bash build.sh`。

输出 `android/WeakNet.apk`。版本号的真值在 `version.py`（`VERSION` / `BUILD`），
`AndroidManifest.xml` 的 `versionCode` / `versionName` 与 `build.sh` 顶部默认值必须与之相同，
由 `tests/_t_version.py` 断言。构建结束前脚本会用 `aapt2 dump badging` 回读成品版本号，
与构建参数不符就失败 —— 不再需要人眼核对。

> 坑（v1.1.9 才踩明白）：build-tools 34 的 `aapt2 link` **默认让 Manifest 里的 `versionCode` / `versionName`
> 覆盖 `--version-code` / `--version-name`**，不加 `--replace-version` 时脚本里的版本号等于白写
> （现象：内容明明变了，`aapt2 dump badging` 出来的版本号一直是旧值）。脚本已带上该参数，
> 并在构建末尾自动断言，验证方式：`aapt2 dump badging WeakNet.apk | head -1` 应显示
> 与 `version.py` 一致的 `versionCode` / `versionName`（当前为 `125` / `1.1.15`）。

构建脚本对 javac / d8 / aapt2 的失败一律「日志落盘 + 明确报错 + 退出」：不接 `tail` 管道，
避免管道的退出码掩盖真实失败；d8 之后还会确认 `classes.dex` 真的存在。

**签名口令不入库**：脚本按 `KS_PASS` 环境变量 → `android/weaknet.keystore.pass` 文件的顺序取口令，
两者都没有时，若 `weaknet.keystore` 也不存在就随机生成一个并写进那个文件（该文件已被 `.gitignore` 排除）；
若 keystore 已存在却拿不到口令，会直接报错而不是猜。口令写在仓库里等于把签名私钥的访问方式公开出去，
哪怕 keystore 本身没提交。想让发布包与旧版签名一致（用户可直接覆盖安装），就保住原来的 keystore 与口令。

> 注：`android/sdk/` 与 `cmdline.zip` 是本地下载的 SDK，体积数百 MB，未纳入仓库，请自行通过 Android Studio 或 sdkmanager 安装。

## Windows EXE

需要 Python 3.8+（GUI 版需 tkinter）与 PyInstaller。仓库只发布 GUI 版包体，CLI 形态直接用源码运行即可：

```bash
python -m PyInstaller --onefile --windowed --clean --noconfirm --name WeakNet \
  --icon /abs/path/to/assets/weaknet.ico \
  --distpath . --workpath _build_gui --specpath _build_gui weaknet_gui.py
```

> 坑：PyInstaller 的 `--icon` 相对路径是相对 `--specpath` 解析的，建议直接用绝对路径。
> 网页控制台已下线，打包时不再需要 `--add-data dashboard.html`。

正式产物用本机维护的 `_build_gui/WeakNet.spec` 构建（该目录在 `.gitignore` 内，不随仓库分发）。
这份 spec 是在上面参数生成的版本上手工补了两处：把 `spec/spec.json` 打进包、引用
`tools/gen_exe_version.py` 生成的版本资源。图标路径务必写成**相对 spec 目录**，不要写死
绝对路径，否则项目换盘符 / 换机器后打包会找不到图标：

```bash
py -3.10-64 tools/gen_exe_version.py          # 升版本后先刷新版本资源
py -3.10-64 -m PyInstaller --clean --noconfirm --distpath . --workpath _build_gui \
  _build_gui/WeakNet.spec
```

> 传 `.spec` 时不能再加 `--specpath`（PyInstaller 会报 `makespec options not valid`）。

## 自动化回归（pytest）

```bash
python -m pytest              # 默认跑 unit + integration（约 15 秒）
python -m pytest -m perf      # 性能门槛：带宽 ±15% / RTT ±20% / 8 并发 ≤1.15x / 停止放行 <0.5s
python -m pytest -m device    # 真机契约：先 set ANDROID_SERIAL=<adb devices 里的序列号>
```

- 标记：`unit`（纯逻辑）/ `integration`（进程内真实 socket）/ `perf`（机器相关）/ `device`（真机）。
- 夹具在 `tests/conftest.py`：`engine` 在独立线程起完整服务（端口随机、设置与自定义场景写临时目录），
  `http_origin` 提供大文件与慢速吐出的源站，用例只通过 HTTP / 代理端口访问 —— 测的是真实管道。
- 存量 `_t_*.py` 里逻辑更细的部分（源码逐字比对等）由 `tests/test_golden_scripts.py` 统一登记，
  新用例请写在 `test_*.py`。
- `spec/spec.json` 是参数 / 场景 / 文案 / 组合规则的唯一真源：Python 运行时直接读，
  Android 侧由 `tools/gen_spec.py` 生成 `SpecGen.java`（`--check` 用于漂移检查）。
- 仓库不含 CI workflow；以上命令需在本地或自建流水线运行。
