# WeakNet — 弱网环境模拟测试工具

把设备流量接管到本地通道，按场景施加**带宽限速、延迟抖动、丢包卡顿、随机断连、黑洞超时、DNS 故障、周期断网**等网络损伤，用来验证 App 在真实弱网下的表现。

**v1.1.17** · Windows（EXE） · Android 7.0+ · [下载最新版](https://github.com/45944831/weaknet/releases/latest)

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
| [`WeakNet.exe`](https://github.com/45944831/weaknet/releases/latest/download/WeakNet.exe) | Windows GUI，免装 Python，无 DOS 黑框 | 10.36 MB | `9c2233571971002a` |
| [`WeakNet.apk`](https://github.com/45944831/weaknet/releases/latest/download/WeakNet.apk) | Android 7.0+ | 209 KB | `dd506aa4d73477c2` |

发布页：<https://github.com/45944831/weaknet/releases/latest>

> **v1.1.17 重点**：两端限速的突发额度对齐——Android 令牌桶容量从 1.0 s 收到 0.25 s，与 Windows 同源（`Bucket.BURST_SEC`）。此前 Android 空闲攒满后能一口气放行整秒配额，1 MB 短传输实测超发 1.46×，看起来像限速失效；对齐后降到 1.01×。同时修正三处参数说明：4G/5G 的「RTT」改成按上行+下行相加的真实往返口径，`down_kbps` 的单位说明改成代码实际使用的 1024 进制。规格 hash `6a95fc73` → `9990e03d`，**限速行为与文案都变了，属产品变更**。
>
> 上一版 **v1.1.16 重点**：采集数据修复版——产品功能与 v1.1.15 完全一致，修掉并发流 steady 假 0、dumpsys PSS 取错、EXE RSS 假 0 三个静默缺陷，补齐场景快照与报告口径，数据复检从 11 个错误降到 0。
>
> 上一版 **v1.1.15 重点**：自定义场景与弱网组合的名字统一限 10 个字（双端码点截断，中文输入法组字不被打断）；弱网组合列表改成和自定义场景一致的三列小方块，去掉 🔀、选中实心绿、下方带段数/循环/时长摘要；新增标签同步状态机，从飘窗或控制台选中项时才翻页，用户手动点开的页不再被 3 秒轮询拽回。首次启动自动注入 5 个内置场景 + 5 个组合。
>
> **v1.1.14** 新增版本号更新提示：顶栏版本号平时灰色，GitHub 发布新版后自动变红并标出新版本号，点一下直达下载页。
>
> 再往前 **v1.1.13** 修的是「点了关闭，进程还在」：关窗不再弹模态框，退出还原后台限时 10 秒，关窗前先等在途 adb 操作落地，退出时强制落盘日志。

<details>
<summary>完整 SHA-256</summary>

```text
9c2233571971002a96f81e05d653f48a52fb878d4a30eb7ea481c54429be9a0b  WeakNet.exe
dd506aa4d73477c2883ef505ffcde647ae4500953f65323e04b8adefcdbdcb2a  WeakNet.apk
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
python -m pytest -m perf              # 性能门禁：同机指纹 + 相对基线（见下）
python -m pytest -m device     # 需先设置 ANDROID_SERIAL

# 性能基线：默认只比较不写入；确认要换基线时才置 1
WN_PERF_UPDATE_BASELINE=1 python -m pytest -m perf
```

性能门禁的判定规则在 `tests/perf/baseline.py`：**同机指纹**才比较，默认
warn 5% / fail 15%，每项 7 次取中位数与 p95；换机器只提示不判定。
采样用 `tools/perf_profile.py`（py-spy，约 5% 开销，可直接 attach EXE）：

```bash
python tools/perf_profile.py record -d 30 -o perf-flame.svg
python tools/perf_profile.py top
python tools/perf_profile.py dump            # 卡死时看调用栈
```

外部真值、偏差曲线与冷启动归因（日志和 iperf3 二进制都放在仓库外 `_tmp/`，
结果与 JSON 归档在 `tests/perf/archive/v1.1.16/`）：

```bash
python tools/perf_iperf.py --quick                        # 链路自检：服务端 / 隧道 / 引擎
python tools/perf_iperf.py                               # 8 场景 × 7 次 × 20s，正式外部真值
python tools/perf_iperf.py --gap 1.5                     # 轮间冷却秒数（默认 1.5，0=背靠背）
python tools/perf_iperf.py --curve                       # 带宽偏差曲线（6 档）
python tools/perf_iperf.py --curve-latency --curve-loss  # 高延迟×低带宽 + 丢包×带宽曲线
python tools/perf_coldstart.py                           # -X importtime 冷启动导入归因
python tools/perf_exe_measure.py --exe <exe> --runs 5    # EXE 冷启动就绪时间 + 进程树 RSS
python -m pytest tests/perf/test_drain_ab.py -m perf -q -o addopts=""   # 写路径 A/B 证据
python -m pytest tests/perf/test_slow_callbacks.py -m perf -q -o addopts=""  # 慢回调清单
```

> **外部真值的主口径是接收端**（iperf3 的 `sum_received`），不是客户端本地的
> `sum_sent`：多流共享瓶颈时后者会系统性少计最多 36%，拿它当限速精度结论会造出
> 假缺陷。口径由 `tests/perf/test_iperf_extract.py` 锁死，详见
> [精度声明 §4.2](docs/precision.md)。
>
> 曲线与正式场景现在**统一窗口**（`--time 20 --omit 3 --repeats 7`），不再需要
> 额外参数；跨批次比较仍须核对归档 JSON 里的 `context.inputs`。
>
> **采集质量自证**：每轮记 `cpu_busy_pct` / `engine_kbps`（代理层自计，与 iperf3
> 独立），归档给出 `cv_pct` / `suspect_rounds` / `quality.usable`。
> **`cv_pct > 5` 时禁止引用中位数**——「稳定 7946」与「双峰 2459/7946」的中位数
> 与 p95 完全相同，只有变异系数能区分。详见 [精度声明 §4.5](docs/precision.md)。

慢回调清单（P0-2 的另一半产物）把「事件循环里堵了超过 50 ms 的同步回调」列成表：
py-spy 只看得到 CPU 热点，看不到谁把一段阻塞塞进了事件循环。排查「切场景不即时 /
界面卡顿」时先跑它：

```bash
WN_SLOW_CB_MS=50 python tools/perf_profile.py record --source --slow-cb 50 -d 30
```

基线与所有结果统一口径：产品 `1 kbps = 1024 bit/s`；iperf3 原始
`bits_per_second` 是 SI（1000 进制），归档同时保留原始值。默认基线为
`SCHEMA=2`，含机器指纹、依赖版本、固定输入、预热、重复次数与单位口径；
换基线必须显式 `WN_PERF_UPDATE_BASELINE=1`，避免「跑一次就自动改写门禁」。

依赖、版本号约束、签名口令与测试夹具说明见[构建与测试](docs/build.md)，
弱网参数能信到什么程度（含 2026-11-13 实测偏差表）见[精度声明](docs/precision.md)。


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

v1.1.17 为两端限速对齐版：版本一致性 **16/16**（新增 C5/C6 产物新鲜度检查）、pytest **228** 通过、Java 纯 JDK **19** 套件、跨端一致性 **22/22**、真机契约 **13**、TUN 数据面 **11/11**（透明转发逐字节一致、限速 4,223 kbps、800 ms RTT 首字节 2,070 ms、5% 丢包正文完整、100% 拒连 80 ms、100% 黑洞 17.8 s 挂住、撤掉后 400 ms 恢复）、真机 10 并发限速 8000 聚合 **7,995 kbps**（一份全局桶）、1–10 Mbps 限速精度 **±0.3%**；产物 EXE 10,862,443 B / `9c223357…`、APK 214,052 B / `dd506aa4…`。v1.1.16 为采集数据修复版（产品功能无变化）：版本一致性 14/14、pytest 138 通过、EXE 冒烟与 APK 真机版本校验通过、数据复检 0 错误；产物 EXE 10,839,282 B / `c9948caa…`、APK 205,860 B / `6c268eb8…`。v1.1.15 真机回归 **68/68**（深度 34 + 组合 11 + 默认项 12 + UI 11），EXE 冒烟 7/7，pytest 138 通过，版本一致性 14/14、规格 hash 一致。另做了关闭残留检测：Windows 正常关窗后进程 / 端口 / 系统代理全部清空，Android 强停后进程、服务与 tun 全清。v1.1.13 的 250/250 全量数据与更早版本见[验证记录](docs/verification.md)。

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
- [精度声明](docs/precision.md)：模拟层次、各参数真实语义、已知偏差与校准方法
- [验证记录](docs/verification.md)：v1.1.0 – v1.1.17 实测与已修缺陷

问题与建议请提 [Issue](https://github.com/45944831/weaknet/issues)。
