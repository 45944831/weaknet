# WeakNet — 弱网环境模拟测试工具

一款用于 App / 网络程序弱网测试的工具：把设备流量接管到本地模拟通道，按预设场景施加**带宽限速、延迟抖动、丢包卡顿、随机断连、黑洞超时、DNS 故障、周期断网**等网络损伤，用来验证 App 在真实弱网下的表现。

支持两端：

- **Windows** — 本地代理 + 一键接管系统代理，提供 GUI（tkinter）与 CLI 两种形态，已打包 `WeakNet.exe` / `WeakNet-CLI.exe`
- **Android** — 基于 `VpnService` 接管整机全部 App 流量（无需手动配代理），已打包 `WeakNet.apk`，带实时限速悬浮窗

---

## 快速开始

### Windows

直接运行 `WeakNet.exe`（GUI 版，图形界面）或 `WeakNet-CLI.exe`（控制台版）。

- 点 **「一键接管本机流量」** → 自动写入系统代理，无需手动配置
- 选择预设场景或自定义参数 → 即时生效
- 点 **「停止模拟（恢复直连）」** → 恢复，退出时自动还原系统代理

也可以从源码运行（Python 3.8+，GUI 版需要 tkinter）：

```bash
python weaknet.py                    # CLI 版，默认代理 8888 / 控制台 8899
python weaknet_gui.py                # GUI 版
python weaknet.py --proxy-port 8888 --ctrl-port 8899
```

控制台地址：<http://127.0.0.1:8899/>

### Android

安装 `WeakNet.apk`（允许未知来源安装），打开后：

1. 点 **「一键接管全部 App 流量」**，首次会弹出 VPN 授权确认 → 允许
2. 选场景，整机所有 App 的流量立即进入模拟通道
3. 可选：点 **「显示限速飘窗」** 打开悬浮窗（需授予「显示在其他应用上层」权限，App 会自动引导跳转）

悬浮窗实时显示：当前场景名、限速上下行、实测速率，可拖动、点主体回到控制台。

---

## 内置场景（16 种）

| 分组 | 场景 |
|---|---|
| 常规 | 直连（无限制）、Wi-Fi、4G LTE、3G、2G EDGE |
| 弱网 | GPRS、56K 拨号、弱 Wi-Fi、网络拥塞、高延迟 |
| 极端 | 卫星链路、高铁快速移动、电梯信号丢失、频繁断连、黑洞（无响应）、DNS 故障、完全断网 |

自定义参数（13 项）：上下行带宽、基础延迟、抖动、丢包率、卡顿时长、随机断连率、黑洞超时率、DNS 故障率、周期断网开关与周期等。

### 自定义栏（⭐ 我的自定义）

调好一组参数后可以起个名字保存下来，之后像内置预设一样一键切换，不需要了就删掉：

| 操作 | 位置 |
|---|---|
| 保存 | 展开「🎛 自定义参数」→ 填名字 → 点「保存为自定义」（GUI 版在同一张卡片的「保存为自定义场景」处） |
| 切换 | 点「⭐ 我的自定义」里的名字即可生效 |
| 删除 | Web 端点右侧 ✕（两步确认：✕ → 确认，避免在 WebView 里误删）；GUI 版选中后点「删除选中」，有二次确认 |

- 保存的场景与内置预设并列常驻显示，切换后会显示为当前场景名
- 重名自动加序号（如「地铁 3G (2)」），不会静默覆盖旧的那条
- 存储：Windows 写程序目录下的 `weaknet_presets.json`（目录不可写时自动退回 `~/.weaknet/`）；Android 存在 App 私有的 SharedPreferences 里，卸载才丢

对应接口：`GET /api/upresets`（列表）、`POST /api/upreset`（保存 / 按 id 覆盖）、`POST /api/upreset/del`（删除），应用时仍走 `/api/preset`，key 形如 `u:<id>`。

### 接管范围（二级界面）

默认接管全部程序 / App。只想让一部分走弱网时，点「选择接管的应用」打开二级界面勾选：

| 模式 | 含义 |
|---|---|
| 全部接管 | 全部走弱网（此时勾选列表置灰，勾了也不生效） |
| 仅接管选定项 | 只有勾选的程序 / App 走弱网，其余走真实网络 |
| 排除选定项 | 勾选的不走弱网，其余都走 |

- 二级界面里有搜索、全选 / 清空（只作用于当前筛出来的项）、刷新，以及顶部与底部的返回按钮（Esc 同效）
- 勾选后必须点「保存」才生效；有未保存改动时点返回会先确认，放弃的改动会回滚到引擎当前值
- 桌面 GUI 版是同样的结构：主界面只留一个「选择接管的程序…」按钮，勾选在弹出窗口里做
- Windows 按进程名（需该程序发起过网络连接才会出现在列表里），Android 按包名（列已安装应用）

对应接口：`GET /api/targets`（候选）、`GET·POST /api/scope`（读写名单）。

### 环境共享（局域网互推环境）

同一局域网里开着 WeakNet 的机器会通过 UDP 广播自动互相发现，可以把调好的环境推给对方：

1. 在「🔗 环境共享」里给自己起个名字（对方看到的就是它）
2. 在「局域网里的设备」里点选一台目标
3. 在「我的自定义环境」里点对应环境右侧的「推送」
4. 对方若已有同名环境，不会静默覆盖 —— 会进对方的「收到的环境」，由对方决定**覆盖我的 / 保留我的 / 另存为**（另存默认建议名带推送方，如「地铁3G（来自 乙）」）

- 用户名与本机标识存在 `weaknet_settings.json`；标识只生成一次，改名不会换身份（否则对方会看到幽灵设备）
- 共享端口默认 `8900`，对局域网开放，只认 `/api/hello` 与 `/api/share` 两个路径
- 发现用 UDP `18899` 起的 8 个端口：同机跑多个实例时各占一个，广播时整段都发一份

对应接口：`GET·POST /api/me`、`GET /api/peers`、`POST /api/push`、`GET /api/inbox`、`POST /api/inbox/resolve`。

---

## 工作原理

### Windows

`weaknet.py` 是一个 `asyncio` 本地 HTTP/HTTPS 代理：

- 令牌桶双向限速（上行 / 下行独立）
- 延迟 + 抖动、丢包（表现为传输停顿，近似 TCP 重传）、随机断连（立即掐断）
- 黑洞：挂起不响应；DNS 故障：返回解析失败；周期断网：定时开关闸门
- 一键接管：写注册表 `Internet Settings` + `InternetSetOptionW` 通知生效，退出自动还原

### Android

无 Gradle，手工工具链 `javac → d8 → aapt2 → zipalign → apksigner`：

- `VpnService` 建立 tun 接口，解析 IPv4 / TCP / UDP
- 伪造 TCP 状态机（SYN-ACK、seq/ack、MSS、校验和），用 `protect()` 的 socket 转发到真实网络
- 全部限速收敛到 `vpn.write` 这一个出口，保证整机共享带宽不叠加

---

## 从源码构建

### Android APK

依赖：JDK 8+、Android SDK（platform + build-tools）。脚本会自动探测路径，也可用环境变量指定：

```bash
cd android
export JAVA_HOME=/path/to/jdk
export ANDROID_HOME=/path/to/android-sdk
bash build.sh
```

输出 `android/WeakNet.apk`。首次构建会自动生成自签名 keystore（`weaknet.keystore`，已被 `.gitignore` 排除）。

> 注：`android/sdk/` 与 `cmdline.zip` 是本地下载的 SDK，体积数百 MB，未纳入仓库，请自行通过 Android Studio 或 sdkmanager 安装。

### Windows EXE

需要 Python 3.8+（GUI 版需 tkinter）与 PyInstaller：

```bash
# GUI 版（--windowed）
python -m PyInstaller --onefile --windowed --clean --noconfirm --name WeakNet \
  --icon /abs/path/to/assets/weaknet.ico \
  --add-data "/abs/path/to/dashboard.html;." \
  --distpath . --workpath _build_gui --specpath _build_gui weaknet_gui.py

# CLI 版（--console）
python -m PyInstaller --onefile --console --clean --noconfirm --name WeakNet-CLI \
  --icon /abs/path/to/assets/weaknet.ico \
  --add-data "/abs/path/to/dashboard.html;." \
  --distpath . --workpath _build_cli --specpath _build_cli weaknet.py
```

> 坑：PyInstaller 的 `--icon` / `--add-data` 相对路径是相对 `--specpath` 解析的，建议直接用绝对路径。

---

## 目录结构

```
weaknet/
├── weaknet.py            核心引擎（asyncio 代理 + 控制台服务，CLI 入口）
├── weaknet_gui.py        GUI 主程序（tkinter）
├── dashboard.html        Web 控制台（Windows 与 Android 共用）
├── gen_icon.py           图标生成脚本（PIL 纯代码绘制）
├── logserver.py          限速验证用的本地测速服务端
├── assets/               Windows 图标（ico / png）
├── WeakNet.exe           【产物】Windows GUI 版
├── WeakNet-CLI.exe       【产物】Windows 控制台版
└── android/
    ├── src/com/weaknet/app/    Java 源码（VpnService / TCP 状态机 / 限速 / 悬浮窗 /
    │                           MyPresets = 自定义场景持久化）
    ├── res/                    应用图标（mipmap 各密度 + adaptive icon）
    ├── assets/                 控制台页面
    ├── AndroidManifest.xml
    ├── build.sh                手工构建脚本
    └── WeakNet.apk             【产物】Android 安装包
```

---

## 验证记录

| 项目 | 结果 |
|---|---|
| 2G EDGE 下载限速 | 稳态 22 KB/s，停止后恢复 3.96 MB/s |
| 20 kbps 限速（Android 真机） | 实测 2.95 KB/s |
| 黑洞场景点停止 | 挂起的请求立即放行（修复前会挂死 10 分钟） |
| 令牌桶切场景 | 取消限速 507ms 内放行（原需等待 25s） |
| Android 真机 | 安装启动无崩溃，VPN 接管 `vpn_active=true` |
| APK 停止接管 → 再次启动 | 真机三轮 `False/True` 交替正常，停止后 tun0 释放、ServiceRecord 归零 |
| 指定目标接管 | 名单内进程限到 30 KB/s、名单外仍是全速（Windows）；Android 包名进出名单连接数 +69 / +0 |
| 自定义栏（保存 / 列表 / 应用 / 删除 / 重启持久化） | 接口 29 项、GUI 25 项自检全通过 |
| 打包产物回归（`_t_exe.py`） | 对 `WeakNet-CLI.exe` 本体跑 19 项：自定义栏增删改、真实流量限速、进程识别全过 |
| Android 真机（`_t_apk_mine.py`） | 16/16：自定义栏保存/应用/删除、越界夹值全过，`/api/targets` 能列出已安装应用 |
| Android 真机（`_t_apk_scope.py`） | 指定 App 接管：名单内 Chrome 连接数 +53、移出名单后 +0，无崩溃 |
| 接管范围二级界面（`_t_dom_scope.cjs`） | 42 项：入口按钮 → 打开 → 勾选 / 搜索 / 全选 / 清空 → 保存返回 → 未保存确认与回滚 → Esc |
| 桌面 GUI 二级窗口（`_t_gui_scope.py`） | 37 项：Toplevel 勾选窗口、保存、返回确认、模式回滚 |
| 环境共享（`_t_share.py`） | 29 项：两个实例双向发现、推送、同名进收件箱、覆盖 / 保留 / 另存、重启后仍在 |
| 环境共享界面（`_t_dom_share.cjs`） | 30 项：改名、选设备、推送、收件箱三种处理、不支持时整卡隐藏 |
| 自定义参数越界值 | 丢包率等百分比字段夹到 0~100（修复前 999% 会变成「全丢」，与断网难区分） |

> `_t_*.py` / `_t_dom_mine.cjs` 是自测脚本，`python _t_exe.py` 可直接验证打包后的 EXE。

---

## 已知事项

- Android 悬浮窗权限需用户在系统设置中授予一次（App 内会引导跳转）
- Android 端需在 VPN 授权弹窗点「允许」，这是 Android 系统强制的，无法绕过
- 桌面 EXE 是 PyInstaller 单文件打包，首次启动需解压临时文件，稍慢属正常
