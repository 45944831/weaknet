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
    ├── src/com/weaknet/app/    Java 源码（VpnService / TCP 状态机 / 限速 / 悬浮窗）
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

---

## 已知事项

- Android 悬浮窗权限需用户在系统设置中授予一次（App 内会引导跳转）
- Android 端需在 VPN 授权弹窗点「允许」，这是 Android 系统强制的，无法绕过
- 桌面 EXE 是 PyInstaller 单文件打包，首次启动需解压临时文件，稍慢属正常
