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
> 与 `version.py` 的 `VERSION` / `BUILD` 一致的 `versionName` / `versionCode`
> （当前为 `1.1.20` / `130`；版本号只认 `version.py`，此处数值仅作参考，升级后以真值为准）。

构建脚本对 javac / d8 / aapt2 的失败一律「日志落盘 + 明确报错 + 退出」：不接 `tail` 管道，
避免管道的退出码掩盖真实失败；d8 之后还会确认 `classes.dex` 真的存在。

**签名口令不入库**：脚本按 `KS_PASS` 环境变量 → `android/weaknet.keystore.pass` 文件的顺序取口令，
两者都没有时，若 `weaknet.keystore` 也不存在就随机生成一个并写进那个文件（该文件已被 `.gitignore` 排除）；
若 keystore 已存在却拿不到口令，会直接报错而不是猜。口令写在仓库里等于把签名私钥的访问方式公开出去，
哪怕 keystore 本身没提交。想让发布包与旧版签名一致（用户可直接覆盖安装），就保住原来的 keystore 与口令。

> **CI / 共享构建机**：只用 `KS_PASS` 环境变量，别把 `weaknet.keystore.pass` 拷进去。
> 口令文件一旦进了构建缓存或备份，等于私钥可被直接使用；环境变量进程结束即消失。
> `android/AndroidManifest.xml` 与 `build.sh` 的 `--target-sdk-version` 必须保持一致
> （当前 24 / 33），两边不一致时以 Manifest 为准会让运行时行为与构建参数对不上。

> 注：`android/sdk/` 与 `cmdline.zip` 是本地下载的 SDK，体积数百 MB，未纳入仓库，请自行通过 Android Studio 或 sdkmanager 安装。

## Windows EXE

需要 Python 3.8+（GUI 版需 tkinter）与 PyInstaller。Python 依赖声明见仓库根目录
`requirements.txt`（运行时仅标准库，该文件里没有任何要装的包）；测试依赖在
`requirements-dev.txt`，PyInstaller / py-spy / Pillow 按需安装。
仓库只发布 GUI 版包体，CLI 形态直接用源码运行即可：

```bash
# --add-data 不可省：缺 spec/spec.json 时 EXE 启动即抛「规格文件读取失败」。
# 分隔符按平台写：Windows 分号，Linux/macOS 冒号（spec/spec.json:spec）。
python -m PyInstaller --onefile --windowed --clean --noconfirm --name WeakNet \
  --icon assets/weaknet.ico --add-data "spec/spec.json;spec" \
  --distpath . --workpath _build_gui --specpath . weaknet_gui.py
```

> 坑：PyInstaller 的 `--icon` 相对路径是相对 spec 所在目录（`--specpath`）解析的。
> 这里让 spec 生成在仓库根目录（`--specpath .`），图标就能写 `assets/weaknet.ico`；
> 不要写死绝对路径，否则换盘符 / 换机器就打包失败。
> 网页控制台已下线，`--add-data` 现在只用于把 `spec/spec.json` 打进包。

正式产物用本机维护的 `_build_gui/WeakNet.spec` 构建（该目录在 `.gitignore` 内，不随仓库分发）。
这份 spec 是在上面参数生成的版本上手工补了两处：把 `spec/spec.json` 打进包、引用
`tools/gen_exe_version.py` 生成的版本资源。图标路径务必写成**相对 spec 目录**，不要写死
绝对路径，否则项目换盘符 / 换机器后打包会找不到图标：

```bash
python tools/gen_exe_version.py          # 升版本后先刷新版本资源
python -m PyInstaller --clean --noconfirm --distpath . --workpath _build_gui \
  _build_gui/WeakNet.spec
```

> 传 `.spec` 时不能再加 `--specpath`（PyInstaller 会报 `makespec options not valid`）。

### onedir 形态（冷启动专项）

`--onefile` 每次启动都要把约 21.4 MB 的 `_MEIxxxx` 解包到 `%TEMP%`，这是冷启动
中位 2.15 s、够不到 `≤2.0 s` 目标的主因（`tools/perf_coldstart.py` 的归因：
源码导入只占 198 ms，其余 2,461 ms 全是解包与进程初始化）。onedir 把文件常驻在
目录里，启动时不再解包。

spec 由生成器产出（`_build_gui/` 在 `.gitignore` 内，仓库存不下 spec 本身，
所以把生成逻辑放进版本管理）：

```bash
python tools/gen_exe_version.py                      # 升版本后先刷新版本资源
python tools/gen_spec_onedir.py                      # 写 _build_gui/WeakNet_onedir.spec
python -m PyInstaller --clean --noconfirm \
  --distpath _build_gui/dist_onedir --workpath _build_gui/_work_onedir \
  _build_gui/WeakNet_onedir.spec
python tools/perf_exe_measure.py \
  --exe _build_gui/dist_onedir/WeakNet/WeakNet.exe --runs 5
```

- 产物：`dist_onedir/WeakNet/WeakNet.exe` + `_internal/`（含 `spec/spec.json`）。
  运行时 `_MEIPASS` 指向 `_internal`，`weaknet.SPEC_PATH` 照旧解析，**代码无需改动**。
- 代价：不再是单文件，分发要整目录（可压成一个 zip）；对采集端宿主无影响 ——
  宿主只按路径拉起 EXE。
- **未验证**：本仓库默认不打包，因此「onedir 能省多少」目前只是假设（同类应用经验
  值 0.3–0.8 s）。发版前按上面的命令实测一次，把数字写进 `docs/verification.md` 再决定
  是否切换发行形态；`≤2.0 s` 未达标这一条在切换前始终成立。

## 自动化回归（pytest）

```bash
python -m pytest              # 默认跑 unit + integration（约 15 秒）
python -m pytest -m perf      # 性能门禁：产品语义（±15% / ±20% / ≤1.15x / <0.5s）+ 相对基线
python -m pytest -m device    # 真机契约：先 set ANDROID_SERIAL=<adb devices 里的序列号>
```

- 标记：`unit`（纯逻辑）/ `integration`（进程内真实 socket）/ `perf`（机器相关）/ `device`（真机）。
- 性能门禁每项跑 7 次（`WN_PERF_REPEATS`），判定规则在 `tests/perf/baseline.py`：
  **同机指纹**才比较基线，默认 warn 5% / fail 15%；换机器只提示不判定。
  确认要换基线时才用 `WN_PERF_UPDATE_BASELINE=1 python -m pytest -m perf`，
  平时只读不写 —— 自动更新基线等于把回归一起承认成新标准。
- 采样用 `python tools/perf_profile.py {record|top|dump}`（py-spy，可直接 attach EXE）。
- 夹具在 `tests/conftest.py`：`engine` 在独立线程起完整服务（端口随机、设置与自定义场景写临时目录），
  `http_origin` 提供大文件与慢速吐出的源站，用例只通过 HTTP / 代理端口访问 —— 测的是真实管道。
- 存量 `_t_*.py` 里逻辑更细的部分（源码逐字比对等）由 `tests/test_golden_scripts.py` 统一登记，
  新用例请写在 `test_*.py`。
- `spec/spec.json` 是**数据规格**（参数 / 场景 / 组合规则）唯一真源：Python 运行时直接读，
  Android 侧由 `tools/gen_spec.py` 生成 `SpecGen.java`（`--check` 用于漂移检查）；
  界面文案不在其中（按钮文字如「开启限速」在 `weaknet_gui.py` / Android 布局里硬编码）。
  API 契约另由 `tests/_t_api_parity.py` 校验，跨端算法常量由 `tests/_t_spec_parity.py` 校验。
- CI：`.github/workflows/perf.yml` 每晚（UTC 18:00）与手动触发跑一次性能门禁；
  共享 runner 上设 `WN_PERF_SAME_MACHINE=1` 忽略主机名，但仍校验平台 / Python / 核数 / 架构。
  性能不做 PR 阻断 —— 噪声可达 15%~30%，阻断只会训练大家「重跑一次就绿了」。

### 能离线判定什么，什么仍需真机

- **默认 `pytest -q` 可判定**：`unit` 层纯逻辑（参数 / spec / 组合 / 版本一致性 / 更新检查），
  `integration` 层本机真实 socket；`gui_text` / `gui_state` / `gui_perf` 是从 GUI 抽出的
  可注入层（不创建 Tk 窗口、不需要显示器，喂构造状态即可断言；`gui_text` 只 import
  `tkinter.font` 做字体测量），`report_gen` 报告结构同样用构造快照单测；
  `test_golden_scripts.py` 还把 `_t_spec_parity.py` / `_t_version.py` 等双端逐字比对脚本
  纳入默认回归，spec 或版本号漂移会直接失败。
- **必须真机 / 外部工具**：`-m device` 的 adb 全局代理与 TUN 逐字节转发；限速精度须用
  iperf3 外部真值（`tools/perf_iperf.py`）。这两类没有可注入替身，CI 只跑前者，
  绿灯不替代真机回归，也不代表限速精度达标。

## 发布前检查（正式发版必须全过）

1. `python tests/_t_version.py --require-artifacts` —— 四处版本号一致，且 EXE/APK
   **确实存在**、不比源码旧。日常源码检查可以不带该参数（仓库不带产物时跳过），
   正式发版必须带：缺产物直接判失败，不能拿「跳过」当通过。
2. `python -m pytest -q` —— 全量功能回归；CI 的 `unit-tests` job 已改成必过，
   不再挂在可失败的性能任务里。
3. `python tests/_t_spec_parity.py` 与 `python tests/_t_api_parity.py` —— 跨端数据
   规格、Windows API 契约；Android 侧契约需真机 `adb forward` 后加 `--android`。
4. `cd android && bash _jw/run.sh` —— Java 纯 JDK 套件。
5. 上传资产后跑 `python tests/_t_release.py --strict` —— 它从 Release API 取回
   字节数与 `digest`，逐项比对 README 下载表（大小 / 短指纹 / 完整摘要 / 下载链接），
   并校验 README 指向的就是**最新正式版**。**不要手抄指纹**：第八轮的错值就是这么来的
   （`9d80713c…` / `04e00ff5…`，两个都和真实资产不符，而当时全部测试是绿的）。
   脚本会直接打印「文档 X / 资产 Y」，照着改成资产那一侧。
6. 发布页与 README 必须引用同一份最终上传文件，不要贴本机重打产物的指纹。
7. 需要替换已发布文件时用新版本号 + 新标签，保留可追踪的变更记录。
