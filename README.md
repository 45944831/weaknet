# WeakNet — 弱网环境模拟测试工具

把设备流量接管到本地通道，按场景施加**带宽限速、延迟抖动、丢包卡顿、随机断连、黑洞超时、DNS 故障、周期断网**等网络损伤，用来验证 App 在真实弱网下的表现。

**v1.1.16** · Windows（EXE） · Android 7.0+ · [下载最新版](https://github.com/45944831/weaknet/releases/latest)

## 功能

| 能力 | 说明 |
|---|---|
| **17 支内置场景** | 常规 / 弱网 / 极端三组，覆盖 4G、5G、弱 Wi-Fi、卫星、高丢包、高延迟、断连、黑洞、DNS 故障等 |
| **13 项参数可调** | 上下行带宽、延迟、抖动、丢包、卡顿、断连、黑洞、DNS 故障、拒连、周期断网等，可存成自定义场景 |
| **时间轴组合** | 把多支场景按「先 30 秒弱网、再 20 秒断网、最后 10 秒直连」排成一条，可循环 |
| **接管范围** | 全部接管 / 仅接管指定项 / 排除指定项（Windows 按进程名，Android 按包名） |
| **一键接管与还原** | Windows 自动改系统代理并在退出时还原；Android 一个开关接管整机，停止即刻恢复 |
| **USB 手机弱网** | 手机不装 App，通过 `adb reverse` + 系统代理给 USB 连接的手机施加弱网 |
| **环境共享** | 同一局域网互推场景与组合，对方不用手抄参数 |
| **新版本提示** | 后台查 GitHub 最新 Release，有新版时顶栏版本号变红，点击直达下载页 |

两端共用同一套场景、参数与接口契约（`spec/spec.json` 为唯一真源），Windows ↔ Android 结果可直接对照。

## 下载

| 文件 | 平台 | 大小 | SHA-256（前 16 位） |
|---|---|---|---|
| [`WeakNet.exe`](https://github.com/45944831/weaknet/releases/latest/download/WeakNet.exe) | Windows GUI，免装 Python，无 DOS 黑框 | 10.34 MB | `c9948caa016bbc53` |
| [`WeakNet.apk`](https://github.com/45944831/weaknet/releases/latest/download/WeakNet.apk) | Android 7.0+ | 201 KB | `6c268eb879b2133f` |

发布页：<https://github.com/45944831/weaknet/releases/latest>

> **v1.1.16 重点**：采集数据修复版——产品功能与 v1.1.15 完全一致，修掉并发流 steady 假 0、dumpsys PSS 取错、EXE RSS 假 0 三个静默缺陷，补齐场景快照与报告口径，数据复检从 11 个错误降到 0。
>
> 上一版 **v1.1.15 重点**：自定义场景与弱网组合的名字统一限 10 个字（双端码点截断，中文输入法组字不被打断）；弱网组合列表改成和自定义场景一致的三列小方块，去掉 🔀、选中实心绿、下方带段数/循环/时长摘要；新增标签同步状态机，从飘窗或控制台选中项时才翻页，用户手动点开的页不再被 3 秒轮询拽回。首次启动自动注入 5 个内置场景 + 5 个组合。
>
> **v1.1.14** 新增版本号更新提示：顶栏版本号平时灰色，GitHub 发布新版后自动变红并标出新版本号，点一下直达下载页。
>
> 再往前 **v1.1.13** 修的是「点了关闭，进程还在」：关窗不再弹模态框，退出还原后台限时 10 秒，关窗前先等在途 adb 操作落地，退出时强制落盘日志。

<details>
<summary>完整 SHA-256</summary>

```text
c9948caa016bbc531d004190c95e93f49c9d18ff12c67bd23b0619d1fe5edd35  WeakNet.exe
6c268eb879b2133fa0442b7a110b78f47374c61fdf1ee54a9c34d176768016a0  WeakNet.apk
```

</details>

> GitHub 下载慢时可在链接前加 `https://ghfast.top/` 走镜像，例如
> `https://ghfast.top/https://github.com/45944831/weaknet/releases/latest/download/WeakNet.exe`。

## 快速开始

### Windows

1. 双击 `WeakNet.exe` 打开界面
2. 选择场景（只是选中，不会开始限速）
3. 点 **「▶ 开启限速」** 开始；点 **「⏹ 停止限速（恢复直连）」** 恢复直连
4. 退出程序会自动解除系统代理

也可以从源码运行（Python 3.8+，GUI 需 tkinter）：

```bash
python weaknet_gui.py     # GUI
python weaknet.py         # 无界面引擎：代理 8888 / 控制接口 8899
```

### Android

1. 安装 `WeakNet.apk`（允许未知来源）
2. 点 **「▶ 开始弱网测试」**，首次按系统提示允许 VPN
3. 需要只测某些 App 时点 **「指定程序…」** 勾选
4. 点 **「⏹ 停止弱网测试」** 断开接管，网络立即恢复

### USB 手机弱网（手机免装 App）

1. 手机打开「开发者选项 → USB 调试」，插线并允许调试
2. 界面右栏 **「📱 USB 手机弱网」→ 刷新设备 → 连接手机**
3. 选择场景，点 **「▶ 开启限速」**
4. 测完点 **「断开」** 或退出程序，都会还原手机；直接拔线也有守护进程自动清理

> 这条路径走系统 HTTP 代理，只影响按系统代理走的 App；要整机接管请用 APK。
> Android 13+ 的部分国产 ROM 会拒绝 adb 改全局设置，界面会给出替代途径，详见[使用说明](docs/guide.md#手机usb-弱网免装-apk)。

## 内置场景

| 分组 | 场景 |
|---|---|
| 常规 | 直连（不限制）、Wi-Fi 良好、4G LTE、5G（良好覆盖） |
| 弱网 | 4G 弱网（边缘/室内）、5G 弱覆盖（室内/边缘）、弱 Wi-Fi（隔墙/远端）、网络拥塞、卫星链路（高延迟）、高丢包（30% 丢包）、高延迟（RTT 800ms） |
| 极端 | 高铁 / 地铁（高速移动）、电梯 / 地下室（信号极差）、频繁断连（网络切换）、黑洞（请求无响应）、DNS 故障（无法解析域名）、完全断网（立即拒连） |

场景可查看参数摘要与适用症状，也能以此为底子另存为自定义场景。参数行为、组合编排与界面细节见[使用说明](docs/guide.md)。

## 工作原理

- **Windows**：`weaknet.py` 是一个 `asyncio` 本地 HTTP/HTTPS 代理。双向令牌桶全局共享带宽，延迟、抖动、丢包、断连、黑洞、DNS 故障与周期断网都在转发链路上注入；接管系统代理通过注册表 + `InternetSetOptionW` 完成，退出自动还原。
- **Android**：`VpnService` 建立 tun 接口，用户态 TCP 状态机解析并转发 IPv4 / TCP / UDP，限速统一收敛到 `vpn.write` 一个出口，保证整机共享带宽不叠加。仅接管 IPv4。
- **USB 手机弱网**：`adb reverse` 把手机的 `localhost:8888` 映射到电脑代理端口，再 `settings put global http_proxy` 指向它；连接时在手机侧装一个拔线守护，反向端口消失后自动清掉代理。

## 构建与测试

```bash
# Android（JDK 8+、Android SDK）
cd android && bash build.sh

# Windows EXE（Python 3.8+、PyInstaller）
python -m PyInstaller --onefile --windowed --clean --noconfirm --name WeakNet \
  --icon /abs/path/to/assets/weaknet.ico \
  --distpath . --workpath _build_gui --specpath _build_gui weaknet_gui.py

# 回归
python -m pytest
python -m pytest -m perf
python -m pytest -m device     # 需先设置 ANDROID_SERIAL
```

依赖、版本号约束、签名口令与测试夹具说明见[构建与测试](docs/build.md)。

## 目录结构

```text
weaknet/
├── weaknet.py          核心引擎（asyncio 代理 + HTTP 控制接口）
├── weaknet_gui.py      Windows GUI（tkinter）
├── usb_phone.py        USB 手机弱网（adb reverse / 全局代理 / 守护）
├── version.py          版本号唯一真值
├── spec/spec.json      场景 / 参数 / 文案 / 组合规则唯一真源
├── tests/              pytest 用例 + 历史回归脚本
├── assets/             Windows 图标
├── WeakNet.exe         Windows 产物
└── android/            Java 源码、资源、build.sh、WeakNet.apk
```

## 验证与已知限制

v1.1.16 为采集数据修复版（产品功能无变化）：版本一致性 14/14、pytest 138 通过、EXE 冒烟与 APK 真机版本校验通过、数据复检 0 错误；产物 EXE 10,839,282 B / `c9948caa…`、APK 205,860 B / `6c268eb8…`。v1.1.15 真机回归 **68/68**（深度 34 + 组合 11 + 默认项 12 + UI 11），EXE 冒烟 7/7，pytest 138 通过，版本一致性 14/14、规格 hash 一致。另做了关闭残留检测：Windows 正常关窗后进程 / 端口 / 系统代理全部清空，Android 强停后进程、服务与 tun 全清。v1.1.13 的 250/250 全量数据与更早版本见[验证记录](docs/verification.md)。

已知限制：

- Android 首次使用需在系统弹窗允许 VPN，无法绕过；悬浮窗权限需手动授予一次
- USB 手机弱网依赖 adb 写全局设置，部分 ROM 会拒绝（报 `WRITE_SECURE_SETTINGS`），可改用 APK 或手动 Wi-Fi 代理
- USB 路径只影响走系统 HTTP 代理的 App，直连 socket 或自带代理的 App 不受影响
- Android 仅接管 IPv4，IPv6 流量走真实网络；分片的 IP 包会被丢弃
- 低带宽 + 高延迟档位的实测吞吐可能高于设定值，判读时请比较「开场景前后」而不是「实测 vs 设定」
- EXE 为 PyInstaller 单文件，首次启动需解压临时文件，稍慢属正常

## 文档

- [使用说明](docs/guide.md)：Windows / Android / USB 全功能操作、17 支场景与 13 项参数说明
- [构建与测试](docs/build.md)：两端构建、版本号约束、pytest 回归
- [验证记录](docs/verification.md)：v1.1.0 – v1.1.16 实测与已修缺陷

问题与建议请提 [Issue](https://github.com/45944831/weaknet/issues)。
