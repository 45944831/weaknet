# WeakNet — 弱网环境模拟测试工具

把设备流量接管到本地通道，按场景施加**带宽限速、延迟抖动、丢包卡顿、随机断连、黑洞超时、DNS 故障、周期断网**等网络损伤，用来验证 App 在真实弱网下的表现。

**v1.1.21** · Windows（EXE） · Android 7.0+ · [下载最新版](https://github.com/45944831/weaknet/releases/latest)

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

两端共用同一套场景、参数与文案规格（`spec/spec.json` 是**数据规格**唯一真源；API 路由/状态码由两端控制层实现并通过 `tests/_t_api_parity.py` 校验一致性），Windows ↔ Android 结果可直接对照。

> **覆盖范围**：Windows / USB 走 HTTP 代理，只影响遵循系统代理的 HTTP/HTTPS 流量；Android 走 TUN 用户态转发，覆盖 IPv4 TCP/UDP。直连 socket、QUIC、ICMP 与 IPv6 不在接管范围（IPv6 不施加弱网），三类接入方式的差异见 [docs/precision.md](docs/precision.md)。

## 下载

> 源码仓库不含 EXE / APK 产物：二者由本机构建 / 发布流程生成，已加入 `.gitignore` 不入库。
> 请从下方 Releases 链接下载，不要指望 clone 仓库后直接拿到二进制。

| 文件 | 平台 | 大小 | SHA-256（前 16 位，发布资产） |
|---|---|---|---|
| [`WeakNet.exe`](https://github.com/45944831/weaknet/releases/download/v1.1.21/WeakNet.exe) | Windows GUI，免装 Python，无 DOS 黑框 | 10.40 MB | `f4703278e037636f` |
| [`WeakNet.apk`](https://github.com/45944831/weaknet/releases/download/v1.1.21/WeakNet.apk) | Android 7.0+ | 229 KB | `ec9cf8354337e9ef` |

> 上表是 **Releases v1.1.21 发布资产**的指纹，由 `tests/_t_release.py --strict` 从 Release API
> 取回字节数与 SHA-256 逐项比对。本机重打的产物与发布资产不是同一份文件，指纹必然不同
> （每次重打都会变，见 [验证记录](docs/verification.md)）。校验下载完整性时只与 Release 页
> 公布的值比对，不要拿本机重打值套发布资产。

发布页：<https://github.com/45944831/weaknet/releases/latest>

> **v1.1.21 重点**：发版前加固版。修复 Windows 系统代理接管恒失败——WinINET 刷新会删掉 `AutoDetect`，旧的自检按逐字段全等判定，把整套接管回滚（`ProxyEnable` 永远写不进去）；现在按「缺失即未开启 WPAD」判定，并在解除接管时把用户原值在最后一次刷新后还原。另补齐 Android TCP 会话创建的并发竞态（同 key 并发 SYN 会留下永不关闭的会话）。新增变异测试（99 变异体、有效得分 100%）与崩溃注入（20 场景）两套探针，以及发布真值校验进入 CI；双端全量实测：真机 tun 数据面 12/12、代理路径 11/11、并发长稳 11/11、USB 联调 7/7、Win 关闭残留 37/0、APK 关闭残留 29/0。
>
> 上一版 **v1.1.20 重点**：可解读报告 + 悬浮窗交互修复版。两端新增「测试报告」导出（9 段结构，含给开发的「解读总结」——有效性判定 / 测过场景 / 参数变更 / 关注点 / 下一步），场景序列识别补齐主界面「开始弱网测试」与组合播放文案，并修掉场景事件被环形缓冲挤出后误判「全程直连」的问题。Android 设置界面新增悬浮窗显示内容多选（勾选即时重建飘窗）；移除没有输入入口的「自定义文本」勾选项，底层 `/api/float` 的 custom 参数仍保留给脚本。悬浮窗暂停/切场景后主界面状态即时同步，总开关在暂停态改为橙色「⏸ 已暂停 · 点此停止」；同时修复 `paused` 标志残留（暂停→停止→再开会误报暂停）、暂停中切场景静默恢复限速、组合线程竞态清掉暂停态三个缺陷。
>
> 上一版 **v1.1.19 重点**：参数采集修复版 —— 修掉一个会「静默关掉限速」的越界解析缺陷。Android 侧 `POST /api/profile` 对整数型超 int 上限的值（`2147483648`、`3000000000`、脚本下发的 `10^18`）用 `optInt` 解析，溢出截断后回落 0，`hasLimits()==false` 直接把限速关掉却仍返回 `ok:true`，用户以为在限速、实际是全速直连；Windows 侧同请求会夹到上限，两端语义不一致。现统一按 `Number/String` 解析并夹取到 spec 上限，新增 22 项纯 JDK 单测覆盖 `2^31` / `3e9` / `1e18` / 负值 / 字符串 / 布尔。同时把采集精度验证补成可复跑的真机七相位回归（`cfg` / `rate` / `delay` / `mtu` / `premise` / `integrity` / `resource`），并把 delay 判定从「首字节 = 2 跳往返」的错误假设改成数据驱动线性拟合——真机上新建 HTTPS 首字节实际 ≈ `3.2×(up+down)`（k=3.23、R²=0.989），旧口径会把准确注入误判成缺陷。
>
> 上一版 **v1.1.17 重点**：两端限速的突发额度对齐——Android 令牌桶容量从 1.0 s 收到 0.25 s，与 Windows 同源（`Bucket.BURST_SEC`）。此前 Android 空闲攒满后能一口气放行整秒配额，1 MB 短传输实测超发 1.46×，看起来像限速失效；对齐后降到 1.01×。同时修正三处参数说明：4G/5G 的「RTT」改成按上行+下行相加的真实往返口径，`down_kbps` 的单位说明改成代码实际使用的 1024 进制。规格 hash `6a95fc73` → `9990e03d`，**限速行为与文案都变了，属产品变更**。
>
> 再上一版 **v1.1.16 重点**：采集数据修复版——产品功能与 v1.1.15 完全一致，修掉并发流 steady 假 0、dumpsys PSS 取错、EXE RSS 假 0 三个静默缺陷，补齐场景快照与报告口径，数据复检从 11 个错误降到 0。
>
> **v1.1.15 重点**：自定义场景与弱网组合的名字统一限 10 个字（双端码点截断，中文输入法组字不被打断）；弱网组合列表改成和自定义场景一致的三列小方块，去掉 🔀、选中实心绿、下方带段数/循环/时长摘要；新增标签同步状态机，从飘窗或控制台选中项时才翻页，用户手动点开的页不再被 3 秒轮询拽回。首次启动自动注入 5 个内置场景 + 5 个组合。
>
> **v1.1.14** 新增版本号更新提示：顶栏版本号平时灰色，GitHub 发布新版后自动变红并标出新版本号，点一下直达下载页。
>
> 再往前 **v1.1.13** 修的是「点了关闭，进程还在」：关窗不再弹模态框，退出还原后台限时 10 秒，关窗前先等在途 adb 操作落地，退出时强制落盘日志。

<details>
<summary>完整 SHA-256</summary>

```text
f4703278e037636fece8dc6ae6f40cd06c1dac6dddfcea7c426ae021aef42251  WeakNet.exe
ec9cf8354337e9efaa76e73ea6802225d12b45863e7286b34440d84b661f90a4  WeakNet.apk
```

以上两项是 Releases v1.1.21 **发布资产**的完整指纹，与本机重打产物（见验证记录）不同。

</details>

> GitHub 下载慢时可在链接前加 `https://ghfast.top/` 走镜像，例如
> `https://ghfast.top/https://github.com/45944831/weaknet/releases/download/v1.1.21/WeakNet.exe`。

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
  - 接管时会临时摘掉「自动配置脚本（PAC）」和「自动检测设置」。Windows 的解析顺序是 PAC → WPAD → 静态代理，不摘掉这两项的话，写进去的代理地址会被直接忽略（企业网里很常见）。退出时按原值还原，且只还原仍是我们写的那部分。
- **Android**：`VpnService` 建立 tun 接口，用户态 TCP 状态机解析并转发 IPv4 / TCP / UDP，限速统一收敛到 `vpn.write` 一个出口，保证整机共享带宽不叠加。仅接管 IPv4。
- **USB 手机弱网**：`adb reverse` 把手机的 `localhost:8888` 映射到电脑代理端口，再 `settings put global http_proxy` 指向它；连接时在手机侧装一个拔线守护，反向端口消失后自动清掉代理。

## 构建与测试

Python 依赖见 [`requirements.txt`](requirements.txt)：运行时只用标准库（该文件不含任何要装的包）；
测试与静态检查的依赖在 [`requirements-dev.txt`](requirements-dev.txt)（pytest / pytest-cov / ruff），
py-spy / PyInstaller / Pillow 是按需安装的可选工具。

```bash
# Android（JDK 8+、Android SDK）
cd android && bash build.sh

# Windows EXE（Python 3.8+、PyInstaller）
# --add-data 必须带上 spec/spec.json，否则打出的 EXE 启动即报「规格文件读取失败」。
# 分隔符按平台写：Windows 用分号 "spec/spec.json;spec"，Linux/macOS 用冒号 "spec/spec.json:spec"。
python -m PyInstaller --onefile --windowed --clean --noconfirm --name WeakNet \
  --icon assets/weaknet.ico --add-data "spec/spec.json;spec" \
  --distpath . --workpath _build_gui --specpath . weaknet_gui.py

# 正式发版请用 _build_gui/WeakNet.spec（已含 spec 收集与版本资源），见 docs/build.md
python -m PyInstaller --clean --noconfirm --distpath . --workpath _build_gui _build_gui/WeakNet.spec

# 回归（先 pip install -r requirements-dev.txt）
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

慢回调清单（性能分析的另一半产物）把「事件循环里堵了超过 50 ms 的同步回调」列成表：
py-spy 只看得到 CPU 热点，看不到谁把一段阻塞塞进了事件循环。排查「切场景不即时 /
界面卡顿」时先跑它：

```bash
WN_SLOW_CB_MS=50 python tools/perf_profile.py record --source --slow-cb 50 -d 30
```

基线与所有结果统一口径：产品 `1 kbps = 1024 bit/s`；iperf3 原始
`bits_per_second` 是 SI（1000 进制），归档同时保留原始值。默认基线为
`SCHEMA=2`，含机器指纹、依赖版本、固定输入、预热、重复次数与单位口径，以及
`version` / `build` / `spec_hash` —— 后三项与当前构建不一致时判定为 `stale-baseline`
（不参与 ok/fail，只提示重刷），避免拿两个不同版本的被测对象互相比较。
换基线必须显式 `WN_PERF_UPDATE_BASELINE=1`，避免「跑一次就自动改写门禁」。

依赖、版本号约束、签名口令与测试夹具说明见[构建与测试](docs/build.md)，
弱网参数能信到什么程度（含 2026-11-13 实测偏差表）见[精度声明](docs/precision.md)。


## 目录结构

```text
weaknet/
├── weaknet.py          核心引擎（asyncio 本地 HTTP/HTTPS 代理 + 8899 控制接口）
├── weaknet_gui.py      Windows GUI 主窗口（tkinter；多继承组装下列 gui_* 面板）
├── gui_notes.py        场景/参数说明二级窗口（NotesMixin）
├── gui_perf.py         桌面刷新帧统计（纯逻辑，不依赖 tkinter，可单测）
├── gui_scope.py        接管范围面板：按进程名过滤哪些程序进弱网（ScopeMixin）
├── gui_share.py        环境共享二级窗口：局域网互推场景/组合（ShareMixin）
├── gui_state.py        GUI 视图模型：状态→文本/色调，只在变化时刷新控件（纯逻辑）
├── gui_text.py         GUI 纯文本与排版：参数摘要、带宽格式化、名字限长（纯逻辑）
├── text_layout.py     说明文案的中文折行排版（从 weaknet.py 抽出，行为不变）
├── gui_theme.py        配色、字体与 ttk 主题层（各面板公共底座）
├── gui_usb.py          USB 手机弱网面板（UsbMixin）
├── usb_phone.py        USB 手机弱网（adb reverse / 系统代理 / 拔线守护）
├── report_gen.py       测试报告生成（纯函数：状态快照 → Markdown）
├── update_check.py     GitHub 新版本检查（后台线程 + 跳转域名白名单）
├── logserver.py        限速校验用的简易速率记录服务端（测试辅助）
├── gen_icon.py         统一图标生成：EXE/APK 共用（需 Pillow）
├── version.py          版本号唯一真值（VERSION / BUILD）
├── requirements.txt    运行时依赖声明（零第三方包，只留说明；测试依赖见下）
├── requirements-dev.txt 开发/CI 依赖声明（pytest / pytest-cov / ruff；普通用户无需安装）
├── ruff.toml           ruff 静态检查配置（只开 E/F，不做风格强制）
├── spec/spec.json      数据规格（参数 / 场景 / 组合规则）唯一真源；界面文案不在此列（API 契约见 tests/_t_api_parity.py）
├── tests/              pytest 用例 + 历史回归脚本
├── tools/              性能采样、iperf3 真值、冷启动等工具脚本
├── assets/             Windows 图标
├── LICENSE             MIT 许可证文本
├── WeakNet.exe         Windows 产物（源码仓库不含，由本机构建/发布流程生成）
└── android/            Java 源码、资源、build.sh、WeakNet.apk（产物同上）
```

## 验证与已知限制

**哪些判定已可自动化**（`python -m pytest`，不需要真机 / iperf3）：纯逻辑层单测（参数夹取、
spec 规则、场景与组合、名字限长、更新检查的版本比较与静默失败）；从 GUI 抽出的可注入层
`gui_text` / `gui_state` / `gui_perf` 不创建 Tk 窗口、不需要显示器，可直接喂构造状态
（`gui_text` 只 `from tkinter import font` 做字体测量）；`report_gen.build()`
只读快照、不碰 GUI、不写文件，报告结构与解读结论由单测覆盖；跨端规格 / 算法常量
（`_t_spec_parity.py`）与版本一致性（`_t_version.py`）通过 `tests/test_golden_scripts.py`
登记后在离线环境执行，API 契约脚本 `_t_api_parity.py` 默认只校验 Windows 侧。
`integration` 用例起本机真实 socket，但仍在单机内。

> **API 契约的覆盖范围**：`python tests/_t_api_parity.py` 默认只跑 Windows 侧（进程内隔离服务，
> 无需设备）；Android-only 6 项（`/api/float` 读取、旧路径 `/api/share*` 别名等）只在传
> `--android` 时执行。Android 侧契约需在真机连接后先 `adb forward tcp:18899 tcp:8899`，
> 再本地运行 `python tests/_t_api_parity.py --android http://127.0.0.1:18899`；CI 不覆盖 Android-only 项。

**仍需真机 / 外部工具**（默认不跑，`pytest -m device`）：TUN 逐字节转发、限速精度
（iperf3 外部真值）、adb 写全局代理与部分 ROM 的拒绝路径。这些没有可注入替身，CI 绿灯
不能替代真机回归，也不代表限速精度已达标。

v1.1.21 为发版前加固版：版本一致性 **16/16**、pytest 全量通过、Java 纯 JDK 套件全过；真机 tun 12/12、代理 11/11、长稳 11/11、USB 7/7；产物（**发布资产**）EXE 10,907,448 B / `f4703278…`、APK 234,532 B / `ec9cf835…`。v1.1.20 为可解读报告 + 悬浮窗交互修复版：版本一致性 **16/16**、pytest 全量通过、Java 纯 JDK **26** 套件全过；真机逐项验证三项功能（报告解读总结 / 悬浮窗多选 / 暂停态按钮同步），并修复暂停标志残留、暂停中切场景静默限速、组合线程竞态三个缺陷；产物（**发布资产**，非本机重打）EXE 10,883,116 B / `9229043b…`、APK 230,436 B / `2a9901b6…`；本机重打产物的指纹与大小见验证记录「第六轮」，两者不同属正常。规格 hash 未变，仍 `9990e03d`。
v1.1.19 为参数采集修复版：版本一致性 **16/16**、pytest **241** 通过、Java 纯 JDK **20** 套件、跨端一致性 **22/22**、数据全量复检 **0 错误 / 0 遗漏 / 0 重复**（24 条提示）；真机七相位（某国产 Android 12 机型）**44/47 → delay 口径修正后全部通过**，限速稳态偏差 **−0.97% ~ −2.75%**（阈值 ±8%）、MTU/MSS 四档全对（1280/1500/9000/16384，MSS=MTU−40）、8 MB 定长下载直连与 4000 kbps 限速 sha 完全一致；产物 EXE 10,862,736 B / `8b23f552…`、APK 214,052 B / `56220dca…`。v1.1.17 为两端限速对齐版：版本一致性 **16/16**（新增 C5/C6 产物新鲜度检查）、pytest **228** 通过、Java 纯 JDK **19** 套件、跨端一致性 **22/22**、真机契约 **13**、TUN 数据面 **11/11**（透明转发逐字节一致、限速 4,223 kbps、800 ms RTT 首字节 2,070 ms、5% 丢包正文完整、100% 拒连 80 ms、100% 黑洞 17.8 s 挂住、撤掉后 400 ms 恢复）、真机 10 并发限速 8000 聚合 **7,995 kbps**（一份全局桶）、1–10 Mbps 限速精度 **±0.3%**；产物 EXE 10,862,443 B / `9c223357…`、APK 214,052 B / `dd506aa4…`。v1.1.16 为采集数据修复版（产品功能无变化）：版本一致性 14/14、pytest 138 通过、EXE 冒烟与 APK 真机版本校验通过、数据复检 0 错误；产物 EXE 10,839,282 B / `c9948caa…`、APK 205,860 B / `6c268eb8…`。v1.1.15 真机回归 **68/68**（深度 34 + 组合 11 + 默认项 12 + UI 11），EXE 冒烟 7/7，pytest 138 通过，版本一致性 14/14、规格 hash 一致。另做了关闭残留检测：Windows 正常关窗后进程 / 端口 / 系统代理全部清空，Android 强停后进程、服务与 tun 全清。v1.1.13 的 250/250 全量数据与更早版本见[验证记录](docs/verification.md)。

已知限制：

- Android 首次使用需在系统弹窗允许 VPN，无法绕过；悬浮窗权限需手动授予一次
- USB 手机弱网依赖 adb 写全局设置，部分 ROM 会拒绝（报 `WRITE_SECURE_SETTINGS`），可改用 APK 或手动 Wi-Fi 代理
- USB 路径只影响走系统 HTTP 代理的 App，直连 socket 或自带代理的 App 不受影响
- Android 仅接管 IPv4 TCP/UDP；IPv6 **不经过 WeakNet**、不会施加弱网。按 `VpnService` 的规定，某个地址族没有任何地址/路由/DNS 时系统会**阻断该族出站**——我们只加了 IPv4，所以纳管 App 的 IPv6 会被确定性阻断（IPv6-only 环境会无法联网），需要时请关闭 VPN 或改用 IPv4 网络；分片的 IP 包会被丢弃
- 低带宽 + 高延迟档位的实测吞吐可能高于设定值，判读时请比较「开场景前后」而不是「实测 vs 设定」
- EXE 为 PyInstaller 单文件，首次启动需解压临时文件，稍慢属正常
- APK 声明 `QUERY_ALL_PACKAGES` 是为了列出可接管的 App（Android 11+ 包可见性限制）。若计划上架 Google Play，需改用 `<queries>` 精确声明并申报用途；自发布 APK 不受影响

## 平台要求

- **Windows**：**Windows 10 及以上（64 位）**。EXE 由 PyInstaller 打包并内置 Python 3.10 运行时；Windows 7 / 8 不在支持范围（Python 3.9+ 与打包运行时均已停止支持）。
- **Android**：**Android 7.0（API 24）及以上**。APK 为纯 Java 实现、不含原生 `.so`，arm64-v8a / armeabi-v7a / x86_64 设备均可直接安装。
- **EXE 未做代码签名**：首次运行可能被 SmartScreen 提示「Windows 已保护你的电脑」，点「更多信息 → 仍要运行」即可；请用 Release 页公布的 SHA-256 校验完整性。

## 许可证

[MIT](LICENSE)

## 文档

- [使用说明](docs/guide.md)：Windows / Android / USB 全功能操作、17 支场景与 13 项参数说明
- [构建与测试](docs/build.md)：两端构建、版本号约束、pytest 回归
- [精度声明](docs/precision.md)：模拟层次、各参数真实语义、已知偏差与校准方法
- [验证记录](docs/verification.md)：v1.1.0 – v1.1.21 实测与已修缺陷
- [测试入口](docs/testing.md)：四层测试怎么跑、真机脚本选哪个、历史脚本约定
- [操作序列矩阵](docs/op-matrix.md)：12 个有副作用操作的共享资源与两两组合结论
- [资源所有权四问](docs/ownership.md)：系统代理 / ADB 映射 / TUN 路由 / 监听端口的所有权判定
- [平台默认值清单](docs/platform-defaults.md)：Android SDK / HTTP / DNS 不显式设置时的真实默认值，以及被默认值坑到的 9 处
- [测试期望的来源](docs/test-truth.md)：全绿 ≠ 正确 —— 哪些期望是从实现抄来的，以及每条期望由谁保证
- [变异测试](docs/mutation.md)：把实现改坏 99 次，测试抓住 89 次；剩下 10 个为什么可以放过
- [崩溃一致性](docs/crash.md)：进程在写注册表途中被杀，原配置会不会永久丢失

问题与建议请提 [Issue](https://github.com/45944831/weaknet/issues)。
