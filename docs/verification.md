# 验证记录

各版本的实测结果与已修缺陷。当前版本 v1.1.20 的汇总在最前面，更早的记录按时间倒序排列。

## v1.1.20 · 2026-11-20 可解读报告 + 悬浮窗交互修复版

发布页：<https://github.com/45944831/weaknet/releases/tag/v1.1.20>（tag `v1.1.20`）。
产物 EXE 10,871,576 B / `9d80713c6761e6d4…`、APK 222,244 B / `04e00ff5c3d4526f…`。
规格 hash 未变（`9990e03d`），版本 v1.1.20 / build 130。

### 新增：两端测试报告与「解读总结（给开发）」

`report_gen.py`（Windows）与 `ReportGen.java`（Android）生成同样的 9 段 Markdown 报告：
一句话结论 / 解读总结 / 测试概况 / 流量统计 / 参数快照 / 事件汇总 / 帧统计 / 口径说明 / 产物指纹。
解读总结做诊断：有效性判定（空跑 / 可能直连 / 有效）、测过场景、参数变更、关注点、下一步行动。
场景序列识别补齐主界面 `开始弱网测试：` 与组合播放多套文案；修复场景事件被 300 条环形缓冲
挤出后误判「全程直连」——用非默认 `last_profile` 兜底。两端各补回归单测。

### Android 悬浮窗交互

- 设置界面新增显示内容多选（勾选即时保存 + `FloatWindow.reload()`），移除没有输入入口的
  「自定义文本」勾选项；底层 `custom` 字段与 `/api/float` 参数保留供脚本使用。
- 悬浮窗切场景 / 暂停后主界面状态即时同步（`MainActivity` 注册为 `WeakNet` 的 uiWatcher）。
- 暂停态总开关改橙色「⏸ 已暂停 · 点此停止」，点击仍是彻底停止（恢复交给悬浮窗 ▶）。

### 修复：暂停态三个缺陷（代码审查发现，非测试可扫出）

1. `paused` 标志残留——`setLimitOn()` 关闭时不清暂停，导致「暂停 → 停止 → 再开始」
   界面误报「已暂停」而实际正在限速。新增 `clearPauseLocked()` 统一在总开关与
   `setProfile()` 落地参数时清理。
2. 暂停中切场景 / 组合会静默恢复限速——`selectScene` / `selectCombo` 暂停分支改为
   保持暂停、只更新恢复目标（`pausedProfile` / `pausedComboStep=0`）。
3. 组合线程竞态——`runCombo` / `sleepOwning` 补 `paused` 校验，防止暂停后线程挤进锁
   把暂停态清掉。

### 验证

版本一致性 16/16、pytest 全量通过、Java 纯 JDK 23 套件全过。真机三步闭环：
暂停→停止→再开始不误报暂停；暂停中 API 切 high_loss 保持直连、界面「已暂停 · 高丢包」；
点 ▶ 恢复后 `loss_pct=30` 按新场景生效。设备最后已复位（停限速 / 收起悬浮窗 / 清端口转发）。

## v1.1.19 · 2026-11-17 参数采集修复版

### 修掉的缺陷：超 int 上限的参数会静默关掉限速

Android 侧 `POST /api/profile` 对「整数型超 int 上限」的值（`2147483648`、`3000000000`，
以及自动化脚本实际下发的 `10^18`）用 `optInt` 解析，溢出截断成负数后回落 0，
`hasLimits()==false` 直接把限速关掉，接口却仍返回 `ok:true` —— 用户以为在限速，
实际是全速直连。Windows 侧同请求会夹到 `1000000`，两端语义不一致。

修复后统一按 `Number/String` 解析并夹取到 spec 上限（`Profile.clampNum()`），
新增 `_ValueClampTest` 22 项纯 JDK 单测，覆盖 `2^31` / `3e9` / `1e18` / 负值 /
字符串 / 布尔，Java 套件 19 → 20。

> 澄清：早期报告曾把 `1e18` 浮点写法当失效样本，实测它能夹到 `1000000`；
> 真正失效的是整数型 `2147483648` / `3000000000`。

### 新增：七相位参数采集回归

`tests/_t_20261116_param.py`，设备侧 100 ms 采样时间线分离「起始突发」与「稳态」：

| 相位 | 覆盖 |
|---|---|
| `cfg` | 17 个内置预设 + 3 组自定义 + 5 类边界的回读一致性 |
| `rate` | 500/1000/2000/4000/8000 kbps × 3 轮，稳态中位数判定 ±8% |
| `delay` | curl `time_starttransfer` + 过原点线性拟合 |
| `mtu` | 1280/1500/9000/16384 + MSS=MTU−40 + 非法档拒绝 |
| `premise` | IPv6 接管、Private DNS、tun_mtu 快照 |
| `integrity` | 8 MB 定长下载 sha 一致 |
| `resource` | 采集自身 CPU / RSS |

### 修正：delay 判定口径（把准确注入误判成缺陷）

旧脚本假设「首字节增量 = 2×(up+down)」。真机实测：新建 HTTPS 连接首字节要付
TCP 握手 + TLS 握手 + 请求-响应多段往返，而延迟是**逐包注入**的，实际增量
≈ **3.2×(up+down)**（过原点拟合 k=3.23、R²=0.989）。旧口径叠加 nc 探针
770 ms 高基线（同机直连 ping 仅 114 ms），把 50+50 / 150+150 / 100+20 判成 FAIL。

改为数据驱动的线性拟合判定（k∈[2,5] 且 R²≥0.95），修正后该相位
**k=3.13、R²=0.9891 → PASS**。产品代码在该轮未做任何修改。

### 前提快照：link-local IPv6 不再误判

`fe80::` 曾被当成 IPv6 已被 TUN 接管，导致 scope 虚报 `ipv4+ipv6`。
新增 `tun_ipv6_global`，只有存在全局 IPv6 时才报 `ipv4+ipv6`。

### 完整性判据

从「总字节 == 请求长度」改为「正文长度 == 请求长度且 SHA 一致」——不再把
HTTP 响应头算进正文；数据源从可变 `ls-lR.gz` 换成固定版本 TUNA ISO，
避免 Range 超过文件大小造成假限速不足。

### 实测（2026-11-17，SM-G9730 / Android 12 / SDK 31 / arm64）

| 项 | 结果 |
|---|---|
| 版本一致性 | **16/16** |
| pytest | **241 passed** |
| Java 纯 JDK | **20 套件** |
| 跨端一致性 | **22/22** |
| 数据全量复检 | **0 错误 / 0 遗漏 / 0 重复 / 24 提示** |
| 真机七相位 | **44/47 → delay 口径修正后全部通过** |
| 限速稳态精度 | **−0.97% ~ −2.75%**（5 档 × 3 轮，阈值 ±8%） |
| MTU / MSS | 1280/1500/9000/16384 四档全对，MSS=MTU−40，非法档 400 且 tun 不变 |
| 8 MB 完整性 | 直连与 4000 kbps 限速 sha 完全一致 |
| delay 线性拟合 | **k=3.13、R²=0.9891** |

### 产物

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `WeakNet.exe` | 10,862,736 B | `8b23f552cbf8a5343cda2b638f08d3c560973c333f451e3fc4f992040eb5cc09` |
| `WeakNet.apk` | 214,052 B | `56220dca5a8531ea85ad018dd8b8928fa1c86a254f8a11db93bd4aeb24229c80` |

APK 内容指纹（与签名时刻无关）：`62d70892…`；versionCode 129 / versionName 1.1.19。

发布页：<https://github.com/45944831/weaknet/releases/tag/v1.1.19>（tag `v1.1.19` → `54f1ed4`）。
发布后把两个资产下载回来重算 SHA-256，与上表逐字节一致。

> v1.1.18 为内部构建版本（P1 修复后的装机验证），未单独发布，改动全部含在 v1.1.19。

## v1.1.19 前置 · 2026-11-16 采集口径与测量不确定度补齐

全网检索（2025–2026）后确认 6 个旧方案零覆盖的方向，本轮把其中 5 个可落地的
全部落地；第 6 个（状态丢包）属产品语义变更，只补文档边界，不改代码。

### 改动

| 方向 | 落点 | 内容 |
|---|---|---|
| **IPv6 / DNS 接管边界** | `docs/precision.md` §1.1、`tests/device_premise.py`、`_t_apk_tun_real.py` | 明确 TUN 仅 IPv4；新增设备前提快照（`scope / ipv6_bypass / private_dns_mode / tun_mtu`），TUN 数据面测试自动记录 |
| **iperf3 已知偏差** | `tools/perf_iperf.py` | 新增 `--block-kb` / `--affinity`；丢包曲线默认 `-l 4K`（官方 Known Issue #125）；归档与每条结果记录实际生效参数 |
| **测量不确定度** | `tests/perf/baseline.py`、`test_baseline.py` | `stats()` 增 `sem` / `ci95_low` / `ci95_high`，小样本用 t 分布（n=7 → 2.447，不是 1.96） |
| **RFC 6349 前提** | `tests/_report_20261028_weak.py` | `premise()` 增 `baseline_rtt_ms` / `bdp_bytes` / `pmtu`（Windows 无 TUN，pmtu 明确 null） |
| **采集质量汇总** | `_check_collect_data.py` | 新增 `check_data_quality()`：有效样本、premise 覆盖率、steady_points<10、RFC6349 前提覆盖 |
| **状态丢包 / 工具边界** | `docs/precision.md` §5 | 声明不与 netem `loss state` 等价；Toxiproxy/Pumba 只能交叉证伪，不能当真值 |

### 实测（2026-11-16）

| 项 | 结果 |
|---|---|
| pytest | **239 passed**（新增 11 项：设备前提 6 + 置信区间 2 + iperf 参数 3） |
| 版本一致性 | **16/16**（C5/C6 仍通过：产品源码未改，产物仍比产品源码新） |
| Java 纯 JDK | **19 套件全过** |
| 数据复检 | **0 错误 / 0 遗漏 / 0 重复 / 24 提示**，exit 0 |
| 采集质量摘要 | 有效样本 **11/12**；premise 完整 11；steady_points<10 **4 条**；RFC6349 前提 **0 条**（旧数据集诚实标注） |
| 影子实证 | 注入 `valid=false, steady=32000`（设定 20000）后偏差仍 **−1.7%**，脏样本未进中位 → 上轮 P0 确认已堵死 |
| 全量编译 | **139 个 py** 全通过 |

### 边界

- **未重采数据集**：`_弱网测试数据_2026-11-14.json` 仍是旧 schema（无 RFC6349 前提），
  复检器已如实标注「0 条」，不冒充已覆盖。
- **未做真机数据面实测**：19:27 接回一台新设备 `70e07492`（alioth / SDK 33），
  但未安装 WeakNet，故只验证了前提快照本身：`scope=no-tun`、无全局 IPv6、
  `private_dns_mode=null`（已按 null 归一化），退出码 0；`device_premise` 的
  6 项纯函数单测通过，真机 `ip` 输出兼容性已在本机确认。
- **未改转发语义**：IPv6 仍未接管（这是刻意选择，加 `::/0` 会断网）。
- 未打包、未发布、未 commit/push。

## v1.1.17 · 2026-11-14 两端限速突发额度对齐

v1.1.16 遗留的两端不一致：Windows 令牌桶容量是 `rate × 0.25 s`，Android 是 `rate`（1.0 s），
差 4 倍，与 `docs/precision.md` §5「同一份配置两端行为一致」的承诺冲突。后果是 Android 上
空闲攒满后一笔短传输能吃整秒配额 —— 真机 1 MB / 4000 kbps 冷测超发 **1.463×**，看着像限速失效。

### 改动

| 位置 | 改动 |
|---|---|
| `Pipe.Bucket` | 新增 `static final double BURST_SEC = 0.25`；`cap()` 统一四处钳制（构造/`setKbps`/补偿/`refill`），构造起始为空（同 Windows） |
| `spec.json` | `4g`/`5g` 的「RTT 40~60 / 10~20ms」改为按上行+下行相加的真实往返（100 / 32 ms）；`down_kbps` 单位说明改为 1024 进制 |
| 规格 hash | `6a95fc73` → `9990e03d` |

### 两条独立证据（静态 + 动态）

1. **静态**：`dexdump -d` 从 APK 的 `classes.dex` 直接读出
   `BURST_SEC` = `STATIC FINAL` / `value: 0.25`，`<init>` 里 `tokens = 0`，`cap()` 里是 `#3fd0`。
   只搜字段名不够（名字改不改都在），必须读值。
2. **动态**：1000 kbps + 128 KB 样本，「空闲 3 s 攒满」0.985 s vs「不攒满」0.970 s ——
   **攒满没有变快**。若 cap 仍是 1.0 s，128 KB 应全免单、掉到 0.3 s 左右。
   真机 A/B 也一致：1 MB / 4000 kbps 冷测 1.463× → **1.014×**。

### 实测

| 项 | 结果 |
|---|---|
| 版本一致性 | **16/16**（新增 C5/C6：产物必须比源码新） |
| pytest | **228 passed** |
| Java 纯 JDK | **19 套件** |
| 跨端一致性 | **22/22**（A11 断言两端 `BURST_SEC` 相等） |
| 真机契约 | **13 passed** |
| TUN 数据面 | **11/11**：透明转发逐字节一致、限速 4,223 kbps、800 ms RTT 首字节 2,070 ms、5% 丢包正文完整、100% 拒连 80 ms、100% 黑洞 17.8 s 挂住、撤掉后 400 ms 恢复 |
| 10 并发限速 8000 | **7,995 kbps**（一份全局桶；每连接一份应为 80,000） |
| 限速精度 1/4/10 Mbps | **+0.3% / +0.3% / −0.2%** |

> 20/50 Mbps 两档本轮不可判：测试时段公网链路只有 25–35 Mbps（上轮 150 Mbps），
> 设定值已贴住或超过链路能力。与产品无关，待链路正常时补测。

### 产物

| 文件 | 大小 | SHA-256（前 16 位） |
|---|---:|---|
| `WeakNet.exe` | 10,862,443 B | `9c2233571971002a` |
| `WeakNet.apk` | 214,052 B | `dd506aa4d73477c2` |

发布页：<https://github.com/45944831/weaknet/releases/tag/v1.1.17>（tag `v1.1.17` → `c09ce68`）。
发布后把两个资产从 release 页下载回来重算 SHA-256，与上表逐字节一致；GitHub 侧记录的
`digest` 也是 `sha256:9c223357…` / `sha256:dd506aa4…`。说明「上传的不是本地文件」这类
事故被排除在发布环节之外。

## 未发版 · 2026-11-14（第十轮）外部真值采集质量自证

「采集数据的准确性」此前只在 Android 采集脚本那条线上做过（`premise` / `valid` /
`steady_points` 护栏），**Windows 外部真值这条线完全没有质量指标**：归档里只有
中位数、p95、极值，无法回答「这批样本本身可不可信」。本轮补这一块。

### 三项新增能力

| 能力 | 实现 | 解决什么 |
|---|---|---|
| **离散度** | `baseline.stats()` 增 `stdev` / `cv_pct` / `mad` / `iqr` / `p95_is_extreme`；`is_unstable()` 给判据 | `n=5` 时最近秩 `p95` 恒等于 `max`；「稳定 7,946」与「双峰 2,459/7,946」的 median 与 p95 **完全相同**，只有 cv 能区分（36.3% vs 0.1%） |
| **环境快照** | 每轮记 `cpu_busy_pct`（`GetSystemTimes`，纯 ctypes 零依赖）/ `mem_avail_mb` / `window_s` / `elapsed_s` | 唯一无法事后补算的维度。上次「并行跑编译导致 16 M→13.5 M」只能靠人回忆判定作废 |
| **三方对账** | 每轮读 `/api/status` 的 `bytes_up/down` 差值 → `engine_kbps`；聚合出 `engine_vs_received_pct` / `engine_crosscheck_ok` | 与 iperf3 完全独立的第三个计数器；直连档应为 0，非 0 说明测量链路串了（阴性对照） |

另修两处元数据自证：归档顶层 `schema` 原硬写 1 而 `context.schema` 是 2（同一文件两个
schema 号打架）；`tool` 原硬写 `"iperf3 3.22"`（换版本后归档仍自称 3.22），改为
`iperf3 --version` 动态取。新增 `--gap`（默认 1.5 s 轮间冷却），降低背靠背测量的样本自相关。

### 实测（2026-11-14 14:05，7×20 s、`-O 3`、冷却 1.5 s）

归档 `tests/perf/archive/v1.1.16/iperf3_external_truth_2026-11-14.json`：

| 场景 | 中位 kbps | 偏差 | 引擎自计 | 引擎 vs 接收 | `cv_pct` | `usable` |
|---|---:|---:|---:|---:|---:|---|
| `weaknet_p1` | 7,988 | −0.2% | 8,016 | +0.4% | 0.34% | ✅ |
| `weaknet_p4` | 7,987 | −0.2% | 8,076 | +1.1% | 0.33% | ✅ |
| `weaknet_p8` | 8,007 | **+0.1%** | 8,121 | +1.4% | 0.73% | ✅ |
| `weaknet_p1_rev` | 7,988 | −0.1% | 8,016 | +0.3% | 0.03% | ✅ |
| `direct_p4` | 38,169,492 | — | 0 | — | **5.91%** | ❌ |

**`direct_p4` 判 `usable=false` 是这套判据第一次真正派上用场，而且抓到的是采集者
自己的失误**：本轮在测量期间跑了数据复检与单测，该档吞吐在 34.3~40.4 M 之间摆动
（CPU 67%→86%→66% 同步起伏）。按判据如实判死，未做粉饰。这也说明
**「0 错误、0 告警、n=7」不等于数据可信**。

### 判据的一处自我修正

初版 `usable = cv 达标 and 无 CPU 异常轮`。实测立刻误报：`weaknet_p8` 第 1 轮
CPU 47.6%（其余 20~27%，上一档刚结束的余温），但该轮吞吐 8,032 就在中位 8,007 旁边、
整批 cv 仅 0.73% → 一批干净数据被判死。

改为 **「CPU 异常 **且** 该轮吞吐偏离中位 >2%」才作废**，只异常不偏离的轮仍记在
`suspect_rounds` 供追溯、并新增 `suspect_biased_rounds` 区分。判据频繁误报的后果是
字段被整体忽略，护栏等于没有。

### 回归

| 项目 | 结果 |
|---|---|
| `pytest` | **222 passed / 18 deselected**（新增 `tests/perf/test_collect_quality.py` 15 项） |
| `pytest -m perf` | **5 passed**（`test_drain_ab.py` 已随 P1-5 回退删除，故由 6 变 5） |
| 版本一致性 | **14/14** |
| 数据复检 | **错误 0 / 遗漏 0 / 重复 0 / 提示 25**，exit 0 |
| 外部真值 | 8 场景 × 7 次 × 20 s，22m52s、rc=0、**0 错误** |

另修一处复检器口径：`_tmp/` 下的**子目录**（单测夹具，如
`_tmp/final_full/test_real_saved_*/runner-baseline.json`）不再参与扫描——它们会让
复检结论随「今天跑过哪些测试」漂移。`_tmp/*.json`（一层，真机与外部测量证据）仍然检查。

**边界**：未打包、未发布、未 commit/push、未改产品代码。

## 未发版 · 2026-11-13（第六轮）性能参数 + 屏幕适配

第五轮之后的全网再扫描（3 组调研、约 80 个来源）+ 本地微基准先行验证，再落地。
两条线：**Windows/Android 性能参数**、**Android 全机型屏幕适配**。
**仍然未打包、未发版、未 commit/push**；`WeakNet.exe` / `WeakNet.apk` 还是 11-06 的
v1.1.16 产物。详见 `_性能参数与屏幕适配落地报告_2026-11-13.md`。

### 性能参数线（Windows 侧先微基准后改码）

| 项 | 改了什么 | 实测 |
|---|---|---|
| **P-A 定时器分辨率** | Python 3.10/Windows 的 `asyncio.sleep` 实际粒度是 15.625 ms 系统 tick（3.11 才换高精度定时器）。`weaknet.py` 启动时 `winmm.timeBeginPeriod(1)` + `atexit` 释放；`WN_TIMER_RES=0` 可关。Win10 2004+ 起只影响本进程 | 微基准：sleep(1ms) **15.74 → 1.14 ms**、sleep(16ms) **31.18 → 17.48 ms**、sleep(64ms) **78.26 → 65.56 ms**（30 次中位）。带宽门禁中位 8,389 → **8,192 kbps**（对 8000 的偏差 **+4.9% → +2.4%**）；RTT p95 与中位相等（407 ms）；停止放行 **203 ms**。64MB 直连 A/B 交替 5 对：B/A 中位 **1.001×**、sha256 全一致 |
| **P-B accept 侧 TCP_NODELAY** | 根因实测：asyncio `_set_nodelay()` 要求 `sock.proto == IPPROTO_TCP`，accept 进来的 proto 被推断成 0 → `start_server` 接受侧 NODELAY=0（出站侧=1）。`_tcp_nodelay(writer)` 在 handle_proxy / handle_ctrl / handle_share 入口补设 | 交互路径不再吃 Nagle+延迟 ACK 的 40~200ms 停顿；`test_unit_tuning.py` 起真回环 server 断言 NODELAY 1，并保留「asyncio 修了就删 helper」的守卫 |
| **P-GC 阈值** | `serve()` 启动时 `gc.collect(2) → gc.freeze() → set_threshold(20000, 50, 100)`（gen0 默认 700 对代理这种对象流转负载太激进） | Close.com 同方案 p95 −80~100 ms 的先例；本机 207 项 pytest + 门禁无回归；不做「关 GC」激进方案 |
| **基线重采** | 定时器修正属刻意参数变更，`WN_PERF_UPDATE_BASELINE=1` 重采 4 项 × 7 次 | 新基线：bw **8,192** / rtt **0.407** / conc8 **7,946** / stop **0.203**（n=7） |
| **Android 数据面参数** | 逐项目源码核对（AntMonitor/NetGuard/PCAPdroid/hev/tun2socks/wireguard-go）后落地 3 项：上游 TCP `setTcpNoDelay(true)`（NetGuard/Go/gVisor 共同默认，缓冲区刻意不设以保留 Linux 自调优）；UDP 收缓冲 512KB（hev-socks5-core 唯一手设项，UDP 无自调优）；`tun-read`/`tun-write` 线程 `Process.setThreadPriority(THREAD_PRIORITY_URGENT_DISPLAY)`（nice -8，AntMonitor 同角色先例，AUDIO 档会饿死自身 GC） | tun 读缓冲 32767 / 写队列 1024 / 阻塞读经证据确认保持现状（hev 单核 20Gbps 基准：决定性参数是每字节 syscall 数，不是 I/O 模型）；真机吞吐回归需装新 APK，按铁律未打包 |

### 屏幕适配线（Android，调研 2025-2026 机型矩阵 + 官方大屏规范）

调研结论要点：全部手机竖屏 <480dp（「1.5K」也是 20:9）；≥600dp 只有平板与折叠屏
内屏（750~900dp，跨 Medium/Expanded 且开合途中换档）；Android 12L+ 对大屏方向锁定
会 letterbox（Adaptive App Quality 红线）、16/17 直接忽略；API 29 起
`FLAG_LAYOUT_NO_LIMITS` 不再允许画进刘海；Material 规范弹层上限 560dp。

| 项 | 改了什么 |
|---|---|
| **Ui.java（新增）** | 适配参数单一收口：600/840dp 断点、560dp 弹层上限、`realDisplaySize`（WindowMetrics 30+ / getRealMetrics 降级）、`safeInsetsOf`（Insets 30+ / DisplayCutout 28+ / systemWindowInset 23+ / status_bar_height 兜底）、`clampOverlay`；纯数学核心全部提供 `(widthPx, density)` 原始类型重载（android.jar 类在纯 JVM 全是 stub） |
| **悬浮窗钳制** | FloatWindow 拖动 / 首次显示 / 500ms 轮询三个点都收口到 `clampPosition()`：位置钳进「真实屏 − 刘海/系统条 insets」，只在变化时回写；API 28+ 显式 `LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS`（API 29 起 NO_LIMITS 窗口被系统裁剪，双保险） |
| **弹层宽度封顶** | 7 处（SettingsSheet / ParamSheet+textWidth / ScopeSheet / ComboNotesSheet / ShareSheet / NotesSheet / MainPanels 编辑器）：紧凑屏逐像素保持旧行为（窗口 MATCH_PARENT、卡片屏宽-margin），≥600dp 窗口与卡片同宽 560dp、Dialog 自动居中 |
| **网格自适应列数** | `Ui.columns` 现算：手机 3 列（旧行为）、Medium 5 列、Expanded 6 列；MainPanels 与 ComboSheet 两处网格同口径，删掉两处硬编码 `COLS = 3` |
| **大屏方向解锁** | `MainActivity.applyOrientationPolicy`：sw<600dp 锁竖版（旧行为），≥600dp 放开；`onConfigurationChanged` 按折叠/旋转重套；Manifest `resizeableActivity="true"` 显式声明 |

### 回归

| 项目 | 结果 |
|---|---|
| 功能回归 | `pytest` **207 passed**（202 + 6 新增 `test_unit_tuning.py`），0 failed |
| 性能门禁 | 重采前 4 项 × 7 次全 `ok`（8,192 / 407 / 7,946 / 203）；慢回调 0 条 |
| 64MB 直连 | sha256 全一致，`WN_TIMER_RES` 交替 A/B B/A=1.001×（中位） |
| Java 纯 JDK 单测 | **18 个测试类全通过**（新增 `_UiScreenAdaptTest` 23 项；`_ComboCardTest` C3 契约随自适应列数同步更新），`javac` 0 error（android-34） |
| Manifest | XML 解析通过、`resizeableActivity=true`；行尾核查：Android 源 LF 为仓库原生，CRLF 文件未被翻转 |

### 真机全面深测（2026-11-14，用户明确授权打包；OPPO TestDevice / Android 13 / 1080×2400@480dpi）

**产物**：`WeakNet.apk` 214,052 B（file-sha256 `b69d221a…`，证书 `1c69f682…` 未变，
可覆盖安装，versionCode 126）；`WeakNet.exe` 10,860,637 B（sha256 `db70027e…`）。
APK/EXE 均为**本轮新构建**，未发版、未 commit/push。

| 测试项 | 结果 |
|---|---|
| **屏幕适配真机模拟** | 手机 360dp → **3 列**（旧行为）；`wm size 2160x1968 + density 420`（模拟 Z Fold 7 内屏 832dp）→ **5 列**、铺满近方形屏、无布局破损；同尺寸强制横屏 → **方向解锁生效**（旧版会被锁回竖版），横屏布局可用；设置弹层 → **560dp 居中卡片**，不再整屏拉伸 |
| **悬浮窗瘦身（用户反馈）** | 730px → **464px（−36%）**：全宽括号注解删除、状态后缀窄分隔、按钮内边距收紧、根内边距 10→7dp、标题 maxWidth 150dp 截断；真机 dumpsys 实测 frame 464×177，可读性完好 |
| **悬浮窗四角钳制** | 左上 frame `[0,107]`（状态栏底）、右下 `[1080,2268]`（手势条之上）——窗口完整可见可点，永不拖丢 |
| **真机缺陷修复 ×1** | 首版钳制用「lp 对比真实屏尺寸」：ColorOS 给 overlay 渲染叠加状态栏偏移（lp.y=0 → frame.y=107），右下角拖动实测**底部越界 43px**；且 NO_LIMITS 窗口的 `getWindowVisibleDisplayFrame` 返回哨兵 ±100000。改为「实测偏移 + 真实屏尺寸 − 状态栏/导航条」换算 **lp 空间可见框**，对 ROM 映射免疫；`_UiScreenAdaptTest` 增补 ColorOS/标准 ROM 两场景回归 |
| **MTU 档 A/B** | 1500 档 36,818 kbps vs 16384 档 27,364 kbps（同批 WiFi 本底波动大，只看比值）；**两档 sha256 一致**（`5a79840b…`），非法档 1501 被拒 ✓ |
| **线程优先级真机验证** | `tun-read`/`tun-write` nice = **−8**（THREAD_PRIORITY_URGENT_DISPLAY 生效），`tun-clean` = 0 不受影响 |
| **新包 vs 旧包 A/B** | 16MB × VPN 开/关 + 10 并发：**sha256 全一致**；10 并发峰值线程 **84**（旧包 82，持平）；VPN 开 cpu_ms/MB **107.5 vs 133.75（−20%）**；吞吐本底波动大（同日 36~49 Mbps），VPN 开/关比值 1.05（VPN 路径非瓶颈） |
| **EXE 冷启动（onefile）** | 中位 **1.59s** / RSS 46.6MB / MEI 21.4MB（n=5）——旧 2.18s，**−27%，首次达到 ≤2.0s 目标**；测量时踩坑：`adb forward tcp:8899` 未删导致 ready 探针命中**手机侧**服务（0.03s 假数字），移除转发后重测 |
| **EXE 冷启动（onedir，P2-2 闭环）** | 中位 **0.5s** / RSS 38.2MB / MEI 0（n=5）——比 onefile 省 **1.09s**（经验预估 0.3–0.8s，实测兑现）；两种形态均 ≤2.0s 达标 |
| **EXE 冒烟** | 进程启动、`/api/status` 版本 126、`/api/run` 开关正常、`GC 参数：gen0 阈值 (20000,50,100)` 日志确认新参数在 EXE 内生效 |
| 设备收尾 | VPN 关闭、`/data/local/tmp` 临时脚本清理、adb forward 移除；截图归档 `_shots_screen_20261114/` |

### 4.1 缺陷修复：限速开关 关→开 不再丢自定义参数（2026-11-14 下午）

复核报告（`_新修改优化空间复核_2026-11-14.md` §4.1）标记的最高优先功能缺陷：
`/api/run` enable=false 会把当前 profile 清成直连，enable=true 的 custom 分支又恒取
「当前 profile」——真机三步复现：4000 → 关 → 再开 **limit_on=true 但 down=0**（假开启）。

| 项 | 内容 |
|---|---|
| **修复** | 新增 `WeakNet.resolveEnable(presetResolved, presetKey, live, lastProfile)`（纯静态，JVM 可测）+ `resolveEnableRestore()` 实例入口；`CtrlServer` /api/run 与 `MainActivity.applyLimitOn` 两处接入。语义：custom live 带参数 → 原样保留；被「关闭」清空/全零 → 回落 lastProfile（setProfile 只在 hasLimits 时更新它，保有关闭前参数），与 Windows 端 last_profile 回落同构 |
| **有意语义变更** | custom 全零 + 存在 lastProfile 时会被上次场景覆盖（旧行为=假开启，报告认定更危险）；「全零自定义」想要的效果用「直连」场景达成。注释已写明 |
| **回归** | 新增 `_RunToggleRestoreTest` 8 项纯 JVM（主场景：关→开保 4000；刚设保留；内置直接落地；解析失败回落；全零回落；无历史直连兜底；live/last 为 null 防御）。踩坑：`Profile.copy()` 内部走 `JSONObject`，android.jar stub 在纯 JVM 抛 `Stub!` → 测试改独立构造同值实例 |
| **验证** | `pytest` 207 passed；JDK **19 类**全通过（新增 8 项）；APK 重打（214,052 B，sha256 `4d58c805…`）装真机：三步流程反转通过（关→再开 **down=4000 保持**），两次开关循环稳定，切内置 wifi（down=50000/延迟 30ms）路径不受影响；设备已收尾（限速关、VPN 关） |

### 采集准确性专项落地 · 2026-11-14 午后（第四轮报告 §4 的 P0/P1 实现主体）

第四轮报告（`_再次复核与采集准确性报告_2026-11-14.md`）确认三个功能修复放行后，
把「采集链路可观测性」列为最高优先。本轮实现其 P0/P1 全部 5 项：

| 项 | 实现 | 验证 |
|---|---|---|
| **P0-1 切档就绪语义** | `VpnNetService.effectiveTunMtu`（establish 成功落档 / stopVpn 清零）+ `mtuReady()`；`/api/status` 暴露 `mtu`/`mss`/`effective_mtu`/`mtu_ready`；`/api/vpn` 响应增加 `effective_mtu`/`ready` | 真机：切 16384 瞬间响应 `ready:false, effective_mtu:0`（重建窗口**可见可轮询**），2s 后 `ready:true` + tun0 实际 16384——§3.6 的脏样本窗口从此可检测 |
| **P0-2 前提快照** | 采集脚本新增 `premise(expect_down)`：每个样本前落引擎状态（limit_on/down_kbps/preset_key），与预期不符 `premise_ok=false`；accuracy runs 带 `premise`+`valid`，mid_stop 带 `premise`；meta.metric_notes 声明「valid=false 禁止进统计」 | 3.9× 假差异类问题在采集端结构性排除 |
| **P1-1 失效样本拦截** | 复检器：accuracy 样本 `ok=false`/`bytes=0` → **ERR**（含有效样本数）；kb>0 却无 runs → ERR；kb=0 空 runs（高时延，设计如此）→ NOTE「禁止引用 avg/steady」 | 当前数据集如实暴露 **错误 2**（4G 弱网 EOF、电梯 bytes=0——第四轮 §3.2 的 3 个失效样本中 2 个带宽档），不再被 0/0/0 掩盖 |
| **P1-2 稳态点数门槛** | `download()` 记录 `steady_points`；窗口内点数 <10 → `steady_kbps=null` + `steady_reason`；低速档样本下限 32KB→**512KB**（512kbps→8s/轮，配合点数门槛） | 网络拥塞档「1 个点算出 −44%」从机制上消除 |
| **P1-3 精度校验** | 复检器新增 deviation：纯带宽档（无 loss/stall/断连等症状）steady 中位 vs 设定 ±8%，症状档标「不适用」 | 现有数据：4G LTE −1.2% ✓、卫星 −3.1% ✓（产品限速精度确认）；症状档 avg −31% 类假偏差被显式标注 |
| 差值字段放行补全 | `dev_pct`/`steady_dev_pct`/`avg_dev_pct`/`payload_dev_pct` 加入 SIGNED_KEYS（上轮 recheck 产物的合法负读数） | 复检器输出从 52 ERR 收敛到诚实的 2 ERR |

APK：214,052 B，sha256 `154c5929…`（证书未变）已装真机；JDK 19 类 + pytest 207 全过；未发版、未 commit/push。

**参数准确性外部验证落地（`_参数准确性外部验证_2026-11-14.md`，同日）**：全网检索核对 17 场景 × 13 参数。
「必须改」3 条文案已修（spec hash `6a95fc73`→**`9990e03d`**，SpecGen 重生成、22 项 parity 全过、
黄金文件重生）：4g 文案改为「一次往返约 100ms（上行 40 + 下行 60）」、5g 改「约 32ms」并删
「低一个量级」的错话、down_kbps 单位说明「1 Mbps = 1000 kbps」→「1024」（与 BITS_PER_KBPS 对齐）。
**重打三产物**：APK 214,052 B（sha256 `8a56019f…`）、EXE onefile 10,862,851 B（`bfe19ed2…`）、
onedir——spec 均已烤进包，真机 /api/status 实测 spec_hash=9990e03d、4g 开启 down=20000/RTT=100ms ✓。
**产品决策待定 2 条（数值未动）**：4g RTT 100ms vs 现网 30–50ms、satellite 1400ms vs GEO 600–700ms
（各偏保守 ×2，改值需评估既有结论失效）；P3-1 出处表已建 `docs/parameter_presets_provenance.md`；
P3-2 direct 显式 stall_ms:0 需三源同步（spec/SpecGen/PRESETS），与决策项并列。
复检器对数据集旧 spec_hash 出 NOTE（采集时点元数据，历史真值不改）。

**全面深度检测响应（`_全面深度检测报告_2026-11-14.md`，同日）**：该报告改写了两个旧结论
（短样本超发是 **时间额度** `min(idle,0.25s)×rate` 而非固定字节额度，已同步 precision/parity
A11/单测；门禁 8,258 里 3.2 点是「32 块免 1 块」的结构性偏移，扣回后与 iperf 真值差 0.15%，
**门禁数字只可纵向比较、不可对外引用精度**）。本轮实现其优先级清单：
① **P1 两端桶容量对齐**：`Pipe.Bucket` 新增 `BURST_SEC=0.25`（原 1.0s，与 Windows 差 4 倍、
违反 precision §5 承诺），cap/补偿/refill 四处钳制统一走 `cap()`，构造起始为空（同 Windows）；
**真机 A/B：1MB@4000 冷测 1.463× → 1.014×（超发基本消除，warm/cold 差距同时消失）**；
A11 升级为「两端 BURST_SEC 相等」断言（0.25/0.25）。② P3×3：`resource_path`（0 调用且
`with_name` 对多段路径抛 ValueError）与 `_note_units_width`（逻辑已内联等价）删除；复检器
新增陈旧归档规则——`_t_*.json` 失败项早于当前 EXE 构建时间的标注「陈旧归档，不代表现况」
（11-04 的 4 条残留提示已正确标记）。③ P2：accuracy 样本落 `ideal_s`（kb×8/kbps），<1s
引用前必须核对突发额度。**短样本突发的 A/B 待办就此关闭**。

**外部真值线采集质量（第六轮报告 `_外部真值采集修正_2026-11-14.md`，已由该会话完成并经本会话核实）**：
`baseline.stats()` 增离散度（stdev/cv_pct/mad/iqr/`p95_is_extreme`——n<20 时 p95 恒等于 max，
显式标注防误读）与 `is_unstable(5%)` 判据；每轮落环境快照（CPU 忙率 GetSystemTimes、可用内存、
iperf 有效窗口）+ 三方对账（iperf 收/发 + 引擎自计）；`--gap` 轮间冷却防自相关。两处判据修正：
CPU 异常需**且**结果偏离 >2% 才作废整批（防护栏误报失效）；引擎对账期望 **+0~+8%**（覆盖区间
天然不同，换分母曾虚高 70%）。复检器扫描收紧到 `_tmp/` 一层（684→116 个 JSON，夹具不再漂移结论）。
证据：经代理四档 **−0.2%~+0.1%、cv ≤0.73%** 全部 usable；`direct_p4` 被判据如实判死
（cv 5.91%，测量期间跑了复检/单测——「0 错误」不等于可信）；`sum_sent` 误差方向不定（±46%），
只能作口径旁证。**判读指南见报告 §9**。

**stop_release 测量修正 + 基线重录（本会话）**：`t_stop` 移到 `/api/stop` 响应后——把 API 往返
从 released 窗口剔除，`stop_api_ms` 单独记录；口径变更后基线重录 **0.219**，复测两轮 **0.234 / 0.234**
（+6.8%，warn 内，4 passed）——判红消除。12:0x 的 0.219 与 13:2x 的 0.265 之争以新口径下的
稳定读数收尾；「分层中位 + 方差上限」的重设计保留为 P2（当前口径已稳定，暂无必要）。

**闭环补记（第五轮复核响应，同日）**：第五轮独立复核确认 5 项修正全部有效，但指出
「护栏只装在采集端、主数据集未重采、valid 无消费端」。已按其 §3 全部落地：
① 复检器消费 valid（影子场景堵住：valid=false 样本不再进精度中位）+ 新字段必填
（旧数据集 36 条 MISS，诚实标记护栏前数据）；② ok=false 按场景语义分级——断连症状
（disconnect/flap>0）截断记 NOTE（预期内），非断连场景才 ERR；③ premise 覆盖全部
段落（baseline/blackhole/flap/concurrency/post_stop_small/connect_cost/fairness/uploads）；
④ reverify `wait_active` 改 `mtu_ready` 轮询（不再靠固定 sleep 猜）；⑤ **数据集已用新脚本
重采**：`_弱网测试数据_2026-11-14.json`（meta 1.1.16 / spec 6a95fc73），复检
**0 ERR / 0 MISS / 0 DUP / 25 NOTE**，新字段覆盖 12/12，deviation：4G −0.7%、卫星档
稳态 9/8 点 <10 被门槛诚实置 null（宁可无值不给假精确值）；elevator 断连截断按预期 NOTE。
注：`mtu_ready` 是**采集就绪**判据（保守侧，tun 接口先建立、标志后落档），不是
「接口存在」判据；ColorOS 后台冻结坑：pid 在但 CtrlServer 不应答，先 `am start`
拉回前台并轮询 /api/status 再开测。
**遗留（第四轮 §4 P2，未动）**：起始突发治理（预热丢弃/有效整形字节标注）、门禁 stop_release 重设计——快尾已归因：`released` 窗口含 /api/stop 往返 + ~1MB 积压环回排空（排空速率 5~21 MB/s 波动 → 203↔47ms 双峰），需「多次分层中位 + 方差上限」重设计后再录基线。

### 外部复核三问题修复 · 2026-11-14 晚（P1 MTU 假生效 / P2 假开启与档位残留）

| 问题 | 修复 | 真机验证 |
|---|---|---|
| **P1 运行中切 MTU 档「假生效」** | `/api/vpn` enable+mtu 且 VPN 已运行时走 `VpnNetService.rebuild()`（stop + 700ms 后 ACTION_START）重建 tun；响应增加 `"rebuilt":true` 标记。重建路径用 `stop(ctx, false)` 不复位档位 | 运行中切 16384 → `rebuilt:true` + tun0 实际 **16384**；切回 1500 → tun0 **1500**；运行中非法 1501 → 400 且 tun0 不变 |
| **P2-1 关闭不复位实验档** | `stop(ctx)` 增加 `resetMtuTier` 参数：用户主动停（CtrlServer/MainActivity 路径）复位到 1500 并记日志；`rebuild` 走 `stop(ctx, false)` 保档 | 带档 9000 开 → 关 → 不带档开：响应 mtu:1500、tun0 **1500** ✓ |
| **P2-2 开启态 POST 全零 profile 假开启** | `/api/profile` 应用后若 `limitOn && !p.hasLimits()` → `setLimitOn(false)` 并响应 `limit_on:false`——与 Windows `set_profile` 的 has_limits 推断同语义（Windows 本无此问题） | 带参 4000 开 → down=4000 ✓；POST 全零 → 响应 `limit_on:false`、status limit_on=False ✓ |
| **P2-3 门禁 stop_release 判红** | **撤回 12:0x 的「并行噪声」归因**：第五轮复核在前台独占下连跑两次均 0.265（+30.5% FAIL），累计 5 次 = 4 判红 / 1 warn，从未复现 0.219——我的两轮 0.219 读数真实但不可复现，不能作为归因依据。当前状态：**stop_release 判红（数据可信，判定随轮次翻转）**，Windows 产品代码 11-14 零改动 → 非代码回归，是「高方差单点指标 + 单一基线」的测量问题：released 窗口含 /api/stop 往返 + ~1MB 积压环回排空（排空速率 5~21 MB/s 波动 → 203↔265/47ms 双峰）。处置：按既定方向重设计指标（分层中位 + 方差上限）后再录基线，不放宽阈值 | 判红保留；门禁其余 3 项（带宽/RTT/并发）稳定 ok。**→ 已于 2026-11-14 本会话解决**：released 窗口剔除 `/api/stop` 往返（`stop_api_ms` 单独记），口径变更后基线重录 0.219，连续两轮 0.219 / 0.203（均 ok）；详见本页顶部「stop_release 测量修正」 |

APK：214,052 B，sha256 `11d94186…`（4.1 修复版；就绪语义版为 `154c5929…`，证书未变），已装真机；未发版、未 commit/push。

### 全面复核（第二轮）· 2026-11-14 下午（详见 `_全面复核报告_2026-11-14下午.md`）

| 项 | 结果 |
|---|---|
| Windows 回归 | pytest **207** / 门禁 4×7 全 ok（bw +0.8% / RTT +0.2% / 并发 +0.0%）/ 版本 14/14 / JDK 19 类 / 行尾与遗留日志审计干净 |
| 数据复检 | **0/0/0/19**——修复检查器：`delta_after_vs_before`/`delta_peak_vs_idle` 为线程数差值（合法可负）加入 `SIGNED_DIFF_KEYS`，并补 `weaknet/_tmp/` 目录前缀 |
| EXE 冷启动 | onefile **1.59s** / onedir **0.51s**（n=5×2，与上午一致）；踩坑记录：设备测量脚本留 4 条 adb forward，**必须 `forward --remove-all`** 否则 ready 探针打到手机侧 |
| 4.1 修复复验 | 新装包三步流程 + 两次循环：关→再开 **down=4000 保持** ✓ |
| 4.2 复现 | 冷 **1.463×** / 暖 **1.151×**（报告 1.290/1.250，同量级）——短样本超发现象稳定复现，与 4.1 无关；按要求未改桶参数 |
| 4.3 复现 | VPN 空闲 ABBA：**9% / 1% / 1%** 单核（报告 6.6/2.2/1.0 同方向）——未归因不改码 |
| 4.4 复现 | 两次 valid=false（基线爬升 136→152，`pool-298` 佐证回收滞后）；**峰值增量 +19/+27 落在报告 +14~27 区间**，会话差 −43/−6 再次确认非泄漏 |
| 传输完整性 | 本日全部 16MB 样本 sha256 一致；「App 进程消失」疑点再现（重启即恢复，未归因） |

## 未发版 · 2026-11-13（第五轮）优化全面落地

第四轮是「只复测不改码」的独立复核（结论见
`_优化方案落地核对矩阵_2026-11-13.md` §5），本轮才真正动手改代码：
在第三轮「6 项完整 / 9 项部分」的基础上，把仍能在本机闭环的 5 项全部做完
（P1-2 / P1-5 / P1-6 / P2-3 / P2-5），判定升至 **11 完整 / 4 需外部条件 / 0 未动**。
**仍然未打包、未发版、未 commit/push**；
`WeakNet.exe` / `WeakNet.apk` 还是 11-06 的 v1.1.16 产物。

### 本轮新增的改动与实测

| 项 | 改了什么 | 实测 |
|---|---|---|
| **P1-6 GUI 空闲开销** | 新增 `gui_state.py`（纯视图模型）+ `App._set()` 脏标记：`refresh()` 只下发**变化**的选项，不再每 500 ms 无条件重配 20 个控件 | 空闲轮 Tk 重配 **20 次/轮 → 0 次/轮**（每分钟 2,400 → 0）；单轮耗时 **1.28 ms → 0.11 ms**；`tests/test_gui_refresh.py` 直接数 `Tk.configure` 调用次数锁住 |
| **P2-3 按面板拆分** | 抽出 `gui_theme.py`（196 行）与四个面板 mixin：`gui_notes.py` 428、`gui_scope.py` 275、`gui_share.py` 386、`gui_usb.py` 403；`weaknet_gui.py` **3,519 → 1,982 行** | `class App(NotesMixin, ScopeMixin, ShareMixin, UsbMixin)`；方法体逐字搬移，只补 import；`py_compile` + 自定义「未定义名」扫描（0 处）+ 真窗口冒烟（开/关说明窗与接管范围窗） |
| **P2-3 Windows 帧率** | 新增 `gui_perf.py`，与 Android `FrameStats` 同口径（120 帧窗口、16 ms / 100 ms 阈值、中位/p95/最大）；每 60 帧落一条 `[perf] GUI 刷新` 日志 | 7 项纯逻辑单测；双端字段名对齐（`gap_median_ms` / `over_frame` / `per_sec` …） |
| **P1-2 16 KB 缓冲对齐** | MTU 从编译期常量改为「默认 1500 + 白名单实验档」：`MTU_TIERS={1280,1500,9000,16384}`、`tunMtu()/defaultMss()/applyMtuTier()`；`am start-foreground-service -e mtu 16384` 可在建立 tun 前切档 | 16384 档 → MSS **16,344**、下游缓冲 **16,344 B**（16 KB 级）；非法档位被拒且不改当前值；`_MssClampTest` 8 → **17 项**、`_MtuAbTest` 23 → **25 项** |
| **P1-5 无收益改动** | 按两轮 A/B（−1.52% / +0.43%）**回退**按水位 drain：`_flush()` 恢复无条件 `drain()`，删除 `DRAIN_HIGH` 与 A/B 脚本 | 新增守卫用例「每块都等 drain + 不得再有 DRAIN_HIGH」；门禁带宽 **8,389 kbps（−0.8%）**、8 并发 **8,063 kbps（+1.5%）**，无劣化 |
| **P2-5 数据集重采** | 用当前代码重跑采集脚本，产出 `_弱网测试数据_2026-11-13.json`（64,107 B）：`meta.version=1.1.16`、`spec_hash=6a95fc73` | 复检 **错误 0 / 遗漏 0 / 重复 0 / 提示 18**（23 → 18）；新增 `_弱网复测摘要_2026-11-13.md` 由数据直接渲染 |
| **P2-5 报告基线口径** | 复检脚本按「报告版本 vs 数据集版本」决定是否做绝对值对照：跨版本只留一条 NOTE，不再把「换版重采」误判成 4 个错误 | 旧报告（1.1.9）对新数据（1.1.16）不再产生假 ERR |
| **P2-2 冷启动** | 新增 `tools/gen_spec_onedir.py` 生成 onedir spec，`docs/build.md` 补构建/测量流程（`_MEIPASS` 指向 `_internal`，`SPEC_PATH` 无需改代码） | **未验证**：按铁律未打包；`≤2.0 s` 仍未达标，切换形态前该结论不变 |

### 回归

| 项目 | 结果 |
|---|---|
| 功能回归 | `pytest` **202 passed**、0 failed（默认排除 `perf` / `device`），exit 0 |
| 性能门禁 | 4 项 × 7 次全 `ok`：带宽 **8,389 kbps（−0.8%）**、RTT **406 ms（+3.8%）**、8 并发 **8,063 kbps（+1.5%）**、停止放行 **265 ms（−6.0%）**；慢回调 **0 条** |
| Java 纯 JDK 单测 | 17 个测试类全通过，日志中可解析 **191 项断言 0 失败**，`javac` 0 error |
| 数据复检 | 错误 0 / 遗漏 0 / 重复 0 / 提示 18，exit 0 |
| 版本一致性 | `tests/_t_version.py` 14/14 |

> 途中修掉两个由拆分暴露的真实问题：`test_gui_refresh` 的夹具曾把 `G.inbox`
> （dict）替换成 list，导致后续 share 用例报 `list indices must be integers`；
> `test_unit_tab_sync` 的模拟控件缺少 `config`/`_set`，已按新契约补齐。
> 两处都是测试夹具问题，不是产品缺陷。

## 未发版 · 2026-11-13 性能改造 + 第二/三轮补齐与缺陷修复

按 `_性能优化方案_2026-11-13.md` 实施 P0/P1/P2，并按
`_优化落地核查与深度复测报告_2026-11-13.md` §3/§4 与
`_优化方案落地核对矩阵_2026-11-13.md` 补齐待办、修复问题。
**尚未打包、未发版、未 git commit/push**；版本号仍为 1.1.16 / build 126，
`WeakNet.exe` 与 `WeakNet.apk` 仍是 11-06 的旧产物。

### 回归与自检

| 项目 | 结果 |
|---|---|
| 功能回归 | `pytest` **172 passed / 19 deselected**（默认排除 `perf` / `device`），0 warning、0 error；`tests/test_unit_delayline.py` **10 passed** 覆盖死连接、半关闭、任务回收、首次 EOF |
| 性能门禁 | `pytest -m perf` **6 passed / 185 deselected**；4 项 × 7 次：`bw_8000_kbps` 中位 **8,454 kbps**（1.057×，**SI 口径**；按产品口径 1024 换算为 **8,258**，2026-11-14 起门禁已改用 1024 并重录基线）、`rtt_400ms_s` **422 ms**、`bw_concurrent8_kbps` **7,946 kbps**（0.993×，修复 94 ms 缺陷后由 5,295 提升 +50.0%）、`stop_release_s` **265 ms**（相对基线 −6.0%，在 10% warn 线内），判定全部 `ok`；慢回调清单 **0 条**；P1-5 A/B **+0.00% / −0.00%**（仍无收益） |
| 默认基线 | 已重生为 **SCHEMA=2**：`spec_hash` / `fingerprint` / `version` / `build` / `dependencies`（Python 3.10.11、pytest 9.2.0、certifi 2022.12.7）/ `inputs` / `warmup=0` / `repeats=7` / `unit_convention` / `ci`；新增 `test_shipped_baseline_carries_full_v2_context` 锁住，防止「代码支持 v2、文件仍是 v1」 |
| CI 门禁自检 | 模拟 runner 基线后注入 **-50%** 退化，`compare()` 判 **`fail`**（`阈值 15%`），工作流自检逻辑本机验证通过 |
| Java 纯 JDK 单测 | `android/_jw/run.sh` **17 个测试类全通过、rc=0**（新增 `_FrameStatsTest` 16 项锁浮窗帧率统计、`_MssClampTest` 锁 MTU↔MSS 联动、`_MtuAbTest` 锁四档参数化、`_TunWriteQueueTest` 锁有界/FIFO/清空） |
| javac 编译 | **40 个产品源文件 + 17 个纯 JDK 测试类 = 57 个 java 文件**零错误（`-source 8 -target 8`，android-34）。本轮新增的正是 `FrameStats.java` 与 `_FrameStatsTest.java` |
| 版本一致性 | `tests/_t_version.py` **14/14**（version.py / build.sh / Manifest / EXE 版本资源） |
| 数据复检 | **错误 0 / 遗漏 0 / 重复 0 / 提示 23**，exit 0；修复了复检脚本把测量产物中合法有符号字段（tracemalloc `size_diff`、偏差 `deviation_pct`、`DRAIN_HIGH=-1` 哨兵、`median_sender_receiver_gap_pct`）误判为 ERR 的缺陷，放行范围扩到 `_tmp/` 测量产物备份目录（核心数据集 `_弱网测试数据_*.json` 仍严格） |
| 改动文件语法 | **全量 116 个 Python 文件**语法零错误（逐个 `compile()`），根目录脚本另行 `py_compile` 通过 |

> 环境适配：本机会拦截对已有构建产物的批量删除（239 个 class 文件超过阈值），
> 导致 `run.sh` 里的 `rm -rf _jw/out` 被拦下、Java 回归假失败。已给脚本加
> `WN_JW_OUT` 隔离输出目录（目录不存在时清理是 no-op），并在 `.gitignore`
> 用 `android/_jw/out*/` 统一忽略；用全新目录重跑即 **16 类全通过、rc=0**。
> 这是测试脚本的可移植性修复，不涉及产品代码。

### P0 测量体系补齐

| 项 | 结果 |
|---|---|
| 基线契约 | 见上表；`same_machine(fp, ci=None)` 修复 runner 顶层 `ci` 字段不匹配导致门禁永远 `different-machine` 的问题 |
| profiling | py-spy 火焰图（`_tmp/flame_20261113.svg`，Samples 10 / Errors 0）、`WN_SLOW_CB_MS` 慢回调探针、tracemalloc 快照对比 `_perf_mem_snapshot.json` |
| 外部真值 | `tools/perf_iperf.py`：**8 场景 × 7 次 × 20 s `-O 3`**（直连 P1/P4/P8/UDP + 经代理 P1/P4/P8/P1-R），含 `streams_per_round` 与 `errors`（**全为空**）；最终批次（19:58，三趟共 58m28s，rc=0）**单流 @8000 = 7,988 kbps（−0.1%）、P4 = 8,010（+0.1%）、P8 = 7,939（−0.8%）、反向 = 7,991（−0.1%）、直连 UDP jitter 0.038 ms / loss 0%**；`socket.send()` / Task 泄漏 / `RuntimeError` / 失败项**全部为 0**。归档 `iperf3_external_truth_2026-11-13.json`，**完整数值表见 [精度声明 §4.1](precision.md)** |
| 偏差曲线 | **三张**（P2-1 要求的三个维度）：带宽 6 档（500→461 / 1000→973 / 4000→3,993 / 8000→7,989 / 20000→20,003 / 50000→49,990 kbps）、**高延迟+低带宽** 4 档（200ms+500→501 / 200ms+1000→953 / 200ms+4000→3,967 / 400ms+1000→984）、**丢包（停顿）** 3 档（5%+100ms@8000→8,037 / 10%+200ms@8000→7,731 / 5%+100ms@1000→973）；均为 7 次 × 20 s `-O 3`，**全部 13 个测点偏差在 ±8% 内**。归档 `iperf3_curve_2026-11-13.json` / `iperf3_curve_latency_loss_2026-11-13.json` |
| 单位与统计口径 | 产品统一 `1 kbps = 1024 bit/s`；iperf3 SI 原始 bps 与 `kbps_1024` 同时留档；**主口径 = 接收端 `sum_received`**（见下方「测量工具缺陷」），发送端数字并列留存 |
| 口径单测 | `tests/perf/test_iperf_extract.py` **5 passed**：钉死「主口径必须是接收端」，防止口径回退 |

**内外交叉验证**：进程内门禁单流 8,258 kbps（1024 口径）与 iperf3 外部真值单流 7,988 kbps
相差 3.4%；**8 并发门禁 7,946 kbps 与外部 `weaknet_p8` 7,939 kbps 相差仅 0.1%**。
两套完全独立的测量（HTTP 测试客户端 vs iperf3 + CONNECT 隧道）在并发档高度吻合。

> 这 3.4% 里 3.2 个百分点是门禁样本自己的结构偏移，不是差异：2 MB 下 32 个 64 KB 块
> 有 1 个被令牌桶的突发额度免掉，`8,258 = 8,000 × 32/31`。扣掉后为 8,000.0，
> 与 7,988 只差 **0.15%**。详见 `docs/precision.md` §4.6。

### P1 判定与 P2 治理

| 项 | 结果 |
|---|---|
| P1-5 重新判定 | 新增 `tests/perf/test_drain_ab.py`：同进程引擎、同一 2 MiB、交替顺序、唯一变量 `DRAIN_HIGH`，覆盖 8000 / 50000 kbps 两档 → **-0.81% / +0.00%**，中位与 p95 均无实质差异。**结论：不可宣称提速、不计入优化收益**；实现保留仅因无劣化，后续再改 Windows 写路径必须先有 py-spy/yappi 热点证据 |
| P1-1 竞态 | `writePacket()` / `stopWriter()` 共用 `writeLock`、`join(500)`、`outQ.clear()`；`_TunWriteQueueTest` 锁住 clear 不重放 |
| P2-1 精度 | **三张偏差曲线全部完成**（带宽 6 档 / 高延迟×低带宽 4 档 / 丢包×带宽 3 档，共 13 个测点、7 次 × 20 s）；偏差全部落在 **±8% 以内**（最低 −7.9% @500 kbps，绝大多数 ±3% 内），**并发档 −0.8% 达标**。上一版记的「500 kbps −23.3%、8 并发 −42.4% 不达标」已更正 —— 后者是测量口径造成的假缺陷 |
| P2-2 冷启动 | `tools/perf_coldstart.py` 5 轮：源码导入中位 **198.2 ms**、历史 EXE 冷启动 **2.66 s**、residual **2,461.8 ms（92.5%）**；`site` 初始化占导入一半以上。精简 hiddenimports 最多省 0.2 s，**不是有效路径**；应优先 onefile→onedir（去掉每次 21.4 MB 解包）与延后 Tk/引擎初始化。**本轮补上 EXE 实测**（`tools/perf_exe_measure.py`，构建到独立 distpath，**不覆盖 11-06 的 `weaknet/WeakNet.exe`**，其指纹仍为 `c9948caa…fe5edd35`）：新构建 5 轮冷启动中位 **2.15 s**、RSS 中位 **52.5 MB**、MEI **21.4 MB**；同机同时段测旧产物为 2.14 s / 52.1 MB → **本轮改动对冷启动无影响**；≤2.0 s 目标**未达标**（超 7.5%），RSS「不增」**达标**（+0.4 MB 在轮间噪声内） |
| P2-3 GUI 拆分 | 第一步：抽出 `gui_text.py` 纯文本/格式化层，保持 CRLF 与 `G.detail` 等 re-export 兼容，新增 `tests/test_gui_text.py` |
| P2-4 CI | runner 专属基线 + `-50%` 注入自检已落地；**远端 workflow 从未触发过** |
| P2-5 数据治理 | 复检回到 0/0/0，23 条 NOTE 仍在（历史 `meta.version=1.1.9`、`spec_hash` 不一致、11-04 历史 `[失败项]`） |

### 测量暴露并已修复的产品缺陷

1. **`socket.send() raised exception.` 刷屏** — 连接已丢失后 `DelayLine` 仍写
   asyncio proactor transport。新增 `_peer_alive()`（`is_closing()` +
   `transport._conn_lost`），`_flush()` / `submit()` 写前判活，死连接 `abort()` +
   `ConnectionError`。
2. **P8 波动 / 连接提前掐断** — CONNECT 转发器在一侧 EOF 时对**两侧**
   `shutdown(SHUT_RDWR)`。改为半关闭 `shutdown(SHUT_WR)`，另一方向继续转发；
   修复后 7 轮 `streams=8`、0 告警。
3. **`Task was destroyed but it is pending!`** — 三处来源：`aclose()` 只 cancel
   不 await、`pipe()` 的 `rate_key` 首次 EOF 时 `UnboundLocalError`、`_mark_dead()`
   同步 cancel 后无人回收。分别以 `_reap()` / 循环外定义 `rate_key` /
   `_schedule_reap()` 修复，并用 10 项单测锁住。
4. **每条新连接把事件循环卡住 94 ms**（本轮新发现）——
   `handle_proxy()` 在事件循环里**同步**做进程名反查：`proc_name_of_peer()`
   最多重试 4 次，每次 `time.sleep(15 ms)` + 一次 `GetExtendedTcpTable` 全表扫描，
   合计约 94 ms。等于**每来一条连接就把整个代理卡住 94 ms**。
   由新补的慢回调清单（`tests/perf/test_slow_callbacks.py`，阈值 50 ms）首次暴露：
   修复前 **3 条、最长 94.0 ms**，全部落在 `handle_proxy()`。
   修法：整体挪进执行器线程（`run_in_executor`），函数/参数/返回语义完全不变。
   验证：**94.0 ms → 0 条**；进程内 8 并发门禁 5,295 → **7,946 kbps（+50.0%）**。

> `_tmp/iperf_full2.log` 是修复前进程，数据有效但带 51 条 Task 告警与 4 条
> `no running event loop`；正式数值一律以归档 JSON 为准，日志只用于核对告警。

### 本轮新发现：测量工具自身的缺陷（差点当成产品缺陷）

外部真值工具原先优先取 iperf3 的 `sum_sent`（**客户端本地**发送计数）。多流共享
同一个瓶颈时客户端几乎全程阻塞在 `send()` 里，本地计数系统性少计最多 36%，
于是同一个产品被测出「8 并发限速低 36%」—— 并被上一版文档写成「产品真实行为，
明确不达标」。

定位靠**把链路拆成三段各测各的 + 两个独立计数器对账**：直连 45.1 Gbps；
过隧道+代理不限速 2.81 Gbps（代理自计 2.75 Gbps）；限速 8000 kbps、−P 8 时
iperf3 报 5,275/3,636 kbps，而**代理自计稳定在 8,083/7,935 kbps** —— 整形逻辑
没有少给字节。再看 iperf3 逐流明细：`sum_received` 是 8,009 kbps，个别流
`sender.bytes` = 0 而 `receiver.bytes` = 2.7 MB。

处置：`_extract()` 改以接收端 `sum_received` 为主口径，发送端数字并列留存
（`median_sender_kbps_1024` / `median_sender_receiver_gap_pct`），归档可自证；
新增 `tests/perf/test_iperf_extract.py` 5 条单测钉死口径。修正后 −P 8 = **7,939 kbps
（−0.8%）**，与进程内门禁 7,946 kbps 相差 0.1%。

**教训**：一个「看起来像产品的严重缺陷」的信号，最该先被质疑的是**测量工具本身**。
本项目此前已有 `tun_ab.json`「两轮结论完全相反」的先例，这次靠「独立计数器对账 +
逐流明细」才没重蹈覆辙。详见 [精度声明 §4.2](precision.md)。

### 明确未做（交付边界）

- **未重打 APK/EXE**：Android 数据面只有「编译 + 纯 JDK 单测」覆盖，
  **没有真机吞吐 / 10 并发线程数 / `cpu_ms/MB` / `dumpsys meminfo` 回归**。
  设备在线但 app 仍是 11-06 v1.1.16 / build 126、`WIFI NOT_VPN`、设备端无 iperf3；
  需重建 APK → 用户授权 VPN 弹窗 → 设备端 iperf3 才能补测，**未擅自改变设备状态**。
- **WPR/WPA 系统级 ETW 归因未做**（需管理员，当前会话 `NOT_ADMIN`）；≤2.0 s 目标未验证。
- **P2-3 未按面板完成全部拆分**（`weaknet_gui.py` 仍是单文件；浮窗帧率统计**已补**：
  Android `FrameStats` + 16 项纯 JDK 单测，Windows GUI 侧仍缺）；**P2-5 数据重采与
  `measure_dev.py` 补完未做**；23 条 NOTE 未清理。
- **`.github/workflows/perf.yml` 从未在 GitHub runner 上跑过**，首次触发需人工确认
  8 并发用例在共享 runner 上是否稳定。
- 线上 Release 与 v1.1.16 产物均未触碰；无 commit / push。

改造内容与依据见方案 §9（含第二轮 §9.5）与核查报告 §6；参数精度边界与实测偏差表见
[精度声明](precision.md)。

## v1.1.16

采集数据修复版，**产品功能与 v1.1.15 完全一致**（两端内核、GUI、`spec.json` 未改，spec hash `6a95fc73`）。改的是测试/采集脚本、报告口径与构建可移植性。

| 项目 | 结果 |
|---|---|
| 数据全量复检 | 错误 **11 → 0** / 遗漏 **1 → 0** / 重复 0（修复前 → 后，两次运行输出逐字节一致） |
| 采集缺陷修复验证 | 离线 16/16（直接 exec 真实源码）：旧口径复现 4 条 `steady_kbps` 假 0，新口径 0 条；历史行 PSS 由 271 变 142,988；RSS 补全；ctypes 降级返回 `None` 而非 0 |
| 真机 meminfo | 6/6：PSS 23,914 / RSS 84,852 / SWAP 234 / Dalvik 6,276 / Native 12,399 KB，`TOTAL RSS ÷ /proc VmRSS = 1.102` |
| EXE 冷启动 RSS | 5 轮重跑 50.8 / 51.3 / 51.4 / 52.3 / 52.3 MB（旧数据 5 次全 0.0），`rss_src=ctypes` |
| 版本一致性 | `tests/_t_version.py` **14/14**（version.py / build.sh / Manifest / EXE 版本资源） |
| pytest | **138 passed** |
| EXE 冒烟 | 版本资源 `FileVersion=ProductVersion=1.1.16`；启动 2.45 s；`/api/status` 返回 `version=1.1.16, build=126`；关闭后无残留进程 |
| APK 真机 | 安装成功；`versionCode=126 / versionName=1.1.16`；`/api/status` 返回 `1.1.16 / 126`；`aapt2 dump badging` + `apksigner verify` 通过 |
| 产物 | EXE 10,839,282 B / `c9948caa…d35`；APK 205,860 B / `6c268eb8…0a0` |
| 边界 | 完整 `measure_dev.py` 因设备 VPN 未授权在 D2 后中止，未产出新 `dev_measure.json`；内存指标由独立真机快照覆盖，线程/CPU/吞吐仍以 2026-10-29 归档为准 |

详见仓库外层的 `_修复与深度测试报告_2026-11-06.md` 与 `_采集数据复测报告_2026-11-06.md`。

## v1.1.15

| 项目 | 结果 |
|---|---|
| 新功能 | 名字限 10 字（双端码点截断）、组合改三列方块、标签同步状态机、首启注入 5+5 默认项、Android 检查更新 |
| 真机深度 | 34/34：数据面、故障注入、组合、自定义、悬浮窗、共享、稳定性 |
| 真机组合 | 11/11（三段组合按剖面推进，总时长与循环正确） |
| 默认项 | 12/12（首启注入 5 场景 + 5 组合，删空后不重复注入） |
| 真机 UI | 11/11（三列 250px 等宽方块、选中实心绿、标签 0.6s 换色且 4.6s 不回跳） |
| 单测 | pytest 138 通过；Android JVM 13 组（NameLimit 19、TabSync 19、组合卡片 19） |
| 版本 / 规格 | `tests/_t_version.py` 14/14；`gen_spec --check` hash `6a95fc73` |
| EXE 冒烟 | 7/7 |
| 关闭残留 | Windows 正常关窗 0 残留（进程、端口、系统代理全清，1.8–2.1s）；Android 强停后进程 / 服务 / tun 全清，10s 内不复活 |

## v1.1.14

| 项目 | 结果 |
|---|---|
| 新功能 | 顶栏版本号：后台查 GitHub 最新 Release，有新版变红并显示新版本号，点击直达下载页 |
| 新增用例 | `tests/test_unit_update.py` 22 项通过（版本比较、假 API、断网静默、草稿过滤、红色标签、GUI 接线），1 项因默认环境无 tkinter 跳过 |
| 真实控件 | py310gui（tk 8.6）下验证新版本时 `fg` 变为 `ERR #c0392b`、恢复正常后回到 `MUTED` |
| 自动化 | pytest 全量通过；`tests/_t_version.py` 14/14（四处版本号真值一致） |
| EXE 冒烟 | 7/7：GUI 子系统、无 conhost、主窗口、控制接口、限速开关 |
| Android | 仅同步版本号（124 / 1.1.14）并重新签名，无功能改动 |

## v1.1.13

| 项目 | 结果 |
|---|---|
| 真机 / EXE 场景 | 80/80：接管、限速、关闭（窗口内 / 独立 GUI / EXE 双进程 / 连接中关窗）、还原 |
| 自动化 | 170/170：pytest 91、关闭状态 15、版本 14、GUI 参数 26、GUI 说明 17、EXE 冒烟 7 |
| 关窗耗时 | 窗口内 0.93s、独立 GUI 1.06s、EXE 双进程 0.97s、连接中关窗 2.63s |
| 关闭后 | 手机代理 / 反向映射 / 守护全部清除，可直连测试源站 |

## 历史记录（v1.1.0 – v1.1.12）

| 项目 | 结果 |
|---|---|
| 300 kbps 限速（自定义参数） | 稳态 37 KB/s（理论 37.5），名单外进程不受影响 |
| 5G 良好 / 5G 弱覆盖 | 1MB 本地回环实测 9.5 MB/s / 747 KB/s（配额 100 Mbps / 6 Mbps） |
| 20 kbps 限速（Android 真机） | 实测 2.95 KB/s |
| 黑洞场景点停止 | 挂起的请求立即放行（修复前会挂死 10 分钟） |
| 令牌桶切场景 | 取消限速 507ms 内放行（原需等待 25s） |
| Android 真机 | 安装启动无崩溃，VPN 接管 `vpn_active=true` |
| APK 停止接管 → 再次启动 | 真机三轮 `False/True` 交替正常，停止后 tun0 释放、ServiceRecord 归零 |
| 指定目标接管 | 名单内进程限到 30 KB/s、名单外仍是全速（Windows）；Android 包名进出名单连接数 +69 / +0 |
| 自定义栏（保存 / 列表 / 应用 / 删除 / 重启持久化） | 接口 29 项、GUI 25 项自检全通过 |
| 打包产物回归（EXE 本体） | 19 项：自定义栏增删改、真实流量限速、进程识别全过 |
| Android 真机（自定义场景） | 16/16：自定义栏保存/应用/删除、越界夹值全过，`/api/targets` 能列出已安装应用 |
| Android 真机（指定 App 接管） | 名单内 Chrome 连接数 +53、移出名单后 +0，无崩溃 |
| 限速总开关（真实流量） | 17 项：启动后 128KB 下载 0.00s（未限速）→ 选场景后 2.81s → 关闭后 0.00s → 再开 2.01s，场景保留可复原 |
| 限速总开关（GUI） | 19 项：默认「未运行」与按钮态、切换真的生效、另一端改动时同步 |
| 桌面 GUI 二级窗口 | 51 项：两个并排按钮与高亮、勾选窗口、保存、返回确认、模式回滚、「接管一切」一键回到全部 |
| 环境共享（两端互推） | 29 项：两个实例双向发现、推送、同名进收件箱、覆盖 / 保留 / 另存、重启后仍在 |
| 环境共享窗口 | 28 项：改名、设备列表、推送（勾选不会被 3 秒自动刷新冲掉）、收件箱三种处理、按钮禁用态、关闭时停掉定时刷新 |
| 自定义场景 | 36 项：保存 / 重名加序号 / 与内置预设同款按钮 / 删除跟随选中项 / 外部新增自动重画 |
| 自定义参数越界值 | 丢包率等百分比字段夹到 0~100（修复前 999% 会变成「全丢」，与断网难区分） |
| USB 手机弱网（adb 层） | 真机（Android 13）：`adb devices` 识别、`reverse` 映射建立与撤销、`settings get` 回读全部正确；手机 → 电脑的 TCP 连接经 reverse 隧道打通（PC 侧收到握手） |
| USB 手机弱网（权限受限路径） | 同一台真机上 `settings put global` 被 ROM 拒绝时：如实报错、给出 4 条替代途径，并自动回滚已建的 reverse 映射，手机上无残留 |
| EXE 冒烟（v1.1.0） | 7/7：GUI 子系统无 DOS 窗口、无 conhost、主窗口标题、`/api/status` 响应、`/api/run` 开关正常 |
| **Android 上行吞吐（v1.1.1 修复）** | VPN 接管后上行 **0.03 Mbps → 206 Mbps**（物理基准 515 Mbps、下行同期 180 Mbps）。修复前只有 3~5 KB/s 且与限速档位无关 |
| **APK 场景矩阵回归（v1.1.1）** | 11 个场景全通过：延迟注入实测 RTT 仍压在「基线 37ms + 单向延迟×2」上（卫星 700 → 1452ms、高延迟 400 → 837ms）；4G LTE 下行设定 20000 kbps 实测 20534（-2.7% 误差，修复前为 19160） |
| **长连接延迟（v1.1.2 修复）** | 修复前延迟只在 TCP 建连时注入一次，长连接内的数据包零延迟（卫星档实测 5 个请求全部在 1443ms 同时到达，间隔 0ms）。修复后每个数据包都吃延迟：短连接总耗时 高延迟 837 → 1698ms、卫星 1452 → 2927ms，各多出一个完整 RTT |
| **UDP 延迟（v1.1.2 新增）** | UDP/DNS 此前完全没有延迟注入。修复后上行延迟实测 +364ms（高延迟档设定 +400）、+692ms（卫星档设定 +700） |
| **v1.1.2 全量回归** | 11 场景吞吐/延迟矩阵 + 6 个极端场景全通过：完全断网 0/5、黑洞 0/5、DNS 故障下随机新域名 rc=1 而 IP 直连 5/5；4G LTE 设定 20000 实测 20501（+2.5%）；直连档吞吐 123 Mbps |
| **EXE 全量测试（v1.1.3）** | 17 支内置档位 + 故障注入 + 进程接管 + 系统代理接管 + 畸形输入 + 建连性能，共 40+ 项，全部通过。方法：本地回环服务端记录到达时刻，逐项核对「设定 vs 实测」 |
| **延迟只在建连生效一次（v1.1.3 修复）** | 修复前一条长连接上 5 个请求的 RTT：4G `[70.4, 0.5, 0.5, 0.5, 0.4]`、高延迟 `[423.7, 0.7, 0.5, 0.5, 0.6]`、卫星 `[694.8, 0.5, 0.8, 0.7, 0.6]`——第 2 次起完全零延迟。修复后 `[762, 757, 792, 820, 755]` / `[1383, 1522, 1423, 1395, 1402]`，每个请求都吃完整往返 |
| **并发带宽成倍超标（v1.1.3 修复）** | 令牌桶原建在每条连接里：8 条并发下载总吞吐 52855 kbps = 设定 20000 的 **2.64 倍**。改为全局共享桶后 1/2/4/8 并发实测 22052 / 20520 / 20547 / 20515（均 1.03~1.10x） |
| **连接尾部数据丢失（v1.1.3 修复）** | 短连接（HTTP / curl / nc）响应尾部数据原先可能随连接一起被丢弃，现排空延迟队列后才返回，6/6 收满 |
| **打包后冒烟（v1.1.3）** | 启动并监听、长连接延迟已进包（RTT 110/111/111/126/93）、4 并发带宽 20868 kbps（1.04x）、停止限速后恢复 957 Mbps |
| **真机全量测试（v1.1.4）** | Android 真机深度测试：VPN 接管/还原、9 档场景矩阵、5 个极端场景、限速总开关、接管范围（74 个应用，仅 Chrome 时 shell 流量旁路）、自定义场景 API↔UI 双向即时刷新；APK 控制接口 16/16 通过 |
| **DNS 故障误伤 IP 直连（v1.1.5 修复）** | 修复前 DNS 故障档把 IP 字面量连接也判失败（Windows 端 4/5 失败、Android ProxyServer 同样误判），与真机 VpnService 路径（只对 UDP 53 生效）行为不一致。修复后两端均按「是否需要解析域名」判定：域名连接 rc=1、IP 直连 5/5，不再被误判成完全断网 |
| **`/api/run` 布尔解析（v1.1.5 修复）** | 修复前 `bool("false") == True`，传字符串 `"false"` 会把「停止限速」误执行为「开启」。修复后只认真正的布尔值，非法值原样拒绝，复测通过 |
| **APK `/api/status` 无响应（v1.1.5 修复）** | 旧包 `/api/status` 稳定返回空响应且异常被 `run()` 的 catch 吞掉。已加兜底：异常落 logcat + 体现在响应 `error` 字段；同时补齐 GET `/api/run` 查询开关与 `/api/me` 身份接口（与 Windows 端契约一致），APK 控制接口复测 16/16 |
| **v1.1.5 全量回归** | Windows 引擎 44 项（D1/D2 转通过、无回归）、真机极端场景（完全断网 / 黑洞 / DNS 故障）无回归、新 EXE 冒烟 + 4G 21.7 Mbps 全过；EXE 10.2 MB / APK 101 KB 重新打包 |
| **数据面性能优化（v1.1.6）** | 13 项热路径优化全部落地：Android 端每包 IP 字符串解析/冗余拷贝/会话 key 拼接归零、DelayLine 空转唤醒消除、CtrlServer 固定线程池；Windows 端令牌桶睡眠移出锁（停止响应 142ms）、nap 等满 remain、PID 快照缓存、HTTP keep-alive 中继、请求头 30s 超时 + 空闲回收、日志 4MB 轮转。弱网语义零变化 |
| **tun 路径两个回退缺陷（v1.1.6 修复）** | 优化引入的 `portIdx()` 下标越界（异常被 catch 吞掉，SYN 全丢）与 `sameFlow()` 漏核端口（新连接撞残留会话挂死），真机测试发现并修复；配套 1717 万组端口组合 + 363 例四元组穷举单测 |
| **Windows 引擎两个真实缺陷（v1.1.6 修复）** | `_LogSink` 传 str 路径时日志静默不轮转（AttributeError 被吞）；半截请求头的连接永不回收关闭（ConnCtx 登记时序），修复后 3.0s 回收 |
| **v1.1.6 全量回归** | 单测 21 + APK 接口 16 + ProxyServer 数据面 11 + tun 数据面 6 + USB 代理路径 7 = **61 项全过**，全部跑在本次构建包上（真机 Android 13）；EXE 冒烟 5/5、内嵌引擎与源码逐代码对象比对一致 |
| **并发后连接长时间卡死（v1.1.7 修复）** | `Pipe.Bucket.consume()` 先预扣令牌再等待，会话关闭时 `worker.interrupt()` 打断等待，被 `catch (Exception)` 吞掉但**预扣的令牌没归还**——每中断一次留一笔"幽灵欠债"。8 并发后欠债累积到远超实际流量，单连接 256KB 卡 90s+。修复：`catch (InterruptedException)` 中补偿预扣（上限不超过桶容量）后原样抛出。实测同一负载下 256KB **2.87s** 完成 |
| **自定义参数被上次场景覆盖（v1.1.7 修复）** | `hasLimits()` 对全 0 参数返回 false → `lastProfile` 不更新；`presetKey="custom"` 时 `buildKeyProfile()`/`profileOf()` 认不出该 key 而回落 `lastProfile`，把用户刚设的参数整个换掉（参数全 0 或只调 stall 这类非 limits 字段时触发）。修复：`custom` 时当前 profile 原样保留，`CtrlServer` 与 `MainActivity` 两处同源缺陷一并修 |
| **v1.1.7 全量回归** | 真机深度测试 **149 项全过**：APK 接口 16 + 边界健壮性 20 + tun 数据面 13 + USB 代理深度 8 + 并发切换长稳 11 + Windows 引擎 44 + EXE 冒烟功能 12 + Python 单测 13 + Java 单测 12。精度：tun 延迟 1490/1400、830/800ms，限速 523/512、24094/20000、270/256kbps；USB 延迟 1220/1200ms，限速 509/512、2005/2000、252/256kbps |
| **v1.1.7 已知偏差** | 并发总带宽偏高：设定 2000kbps 时单连接 1.06~1.13x（精确）、4 并发 1.15x、8 并发 1.29x，根因未定位；控制接口不校验 HTTP 方法（`DELETE /api/status` 返回 200 而非 405）。均不影响功能正确性。**方法校验已在 v1.1.9 修复**（405 + `Allow: GET, POST`），并发带宽偏差的测量口径也在 v1.1.9 修正（预热 + 大载荷后 8 并发 0.96x，原先的 1.29x 主要是令牌桶突发额度未预热） |
| **左栏无法下滑（v1.1.7 后修复）** | EXE 窗口模式下左栏是裸 Frame 直接 pack，没有滚动容器：窗口 700 高时左栏内容 782px、可视仅 361px，底部「断网间隔 / 断网时长 / 保存栏」被窗口下边缘裁掉，滚轮和拖拽都翻不出来。改为 Canvas + 滚动条（按需显隐、滚轮按事件源判定归属，右栏日志与范围列表不受影响），展开「自定义参数」后自动滚入视野；数字框滚轮改为翻页（ttk 默认会静默改值）。GUI 回归 **20 项全过**，13 个参数逐个验证可达 |
| **场景说明「！」入口（新增）** | 两端预设标题右侧加「！」：17 支档位的**参数摘要 + 适用症状**，按分类分组并标注当前选中；参数摘要由档位定义现算，改档位不会漏更新文案。EXE 回归 17 项（含逐条全文比对注释、切场景标记跟随、窗口复用与关闭）+ Java 数据层单测 18 项（覆盖完整性、文案唯一、参数语义一致）全过 |
| **参数说明「！」入口（新增）** | 两端参数**名字后面**各跟一个「！」：点开是这个参数的单位 / 取值范围 / 作用方向 / 易踩的坑（丢包≠报错、卡顿只在丢包后生效、黑洞挂住 vs 拒连立刻失败、带宽全局共享等）。入口刻意做淡（灰色小字、无边框、悬停才变蓝）；「紧跟名字」与「数字框对齐」冲突时把空白留在按钮右侧，列宽与每行空白都按字体实测宽度算，不写死。EXE 上贴着按钮弹小窗、再点即收起、换参数不叠窗、收起卡片自动关；文案两端同源逐条比对。EXE 回归 **26 项**、Python 一致性 **31 项**、Java 数据层 **19 项** 全过。顺手修掉两处：窗口缩到最小宽度 820 时参数两列溢出 30px（右侧单位被裁）、卫星链路注释把「单程 700ms」写成「往返 700ms」 |
| **`_jw/run.sh` classpath 缺陷（新增修复）** | Git Bash 下 `java` 不认 `/c/...` 形式的绝对路径（`javac` 认），`-cp` 里的 android.jar 被静默忽略：测试类一碰 `android.*` 就 `NoClassDefFoundError`，看着像代码坏了。改用 `cygpath -w` 转换（无 cygpath 的平台原样使用） |
| **弱网组合（v1.1.8 新增，两端）** | 把多支场景按时间轴排成一条，最多 5 段，每段 = 场景 + 时长（分/秒），到点自动切下一段，可循环。播放是按 steps 线性推进的，段里**不允许**再选另一支组合（套一层不会展开也不报错，只会在运行时把那一段跳过，用户排的第 2 段凭空消失最难排查）。接口 `GET /api/combos` / `POST /api/combo` / `POST /api/combo/delete` |
| **组合编辑器场景名不显示（v1.1.8 修复）** | 根因是系统 `Spinner` 的收起态在 OPPO/ColorOS 只画得出下拉箭头，选中的场景名一个字都不画（展开后的列表里名字是正常的 → 不是数据问题，看着像没选中）。改成自绘名字块 + 单选弹层，不再依赖 ROM 行为 |
| **组合编辑器排版重做（v1.1.8）** | 每段一行「场景名 + 分 + 秒」，场景名完整不截断；删除统一走**左滑露出删除按钮**（同时只留一行是滑开的）。踩过的坑：ScrollView 默认给子视图 WRAP_CONTENT 宽度会让整行量成「不确定宽度」，`weight=1` 完全不生效；`Button` 自带 elevation 会被 Android 按 Z 顺序画到内容层上面，把「秒」字盖住 |
| **新增段默认选中「直连」（v1.1.8 修复）** | 组合里放直连等于那段时间不做弱网，很容易被当成已经排好了。改为默认选第一支真正的弱网场景 |
| **存档场景被删后静默改写（v1.1.8 修复）** | 自定义场景被删掉后再编辑该组合并保存，原来那一段会悄悄变成别的场景。现在补一条「已删除的自定义场景」占位并保留原 key |
| **场景/组合标签合并（v1.1.8）** | 界面上「场景」与「组合」合并为一个标签页，两类内容不再分属两个入口 |
| **二级弹窗按钮重复（v1.1.8）** | 二级弹窗里既有 ✕ 又有「返回/取消」，两者作用重复。去掉了重复的那个，只留 ✕ |
| **签名口令不再入库（v1.1.8）** | `build.sh` 原先把 keystore 口令明文写在仓库里 —— 口令入库等于公开签名私钥的访问方式，哪怕 keystore 本身没提交。改为按 `KS_PASS` 环境变量 → 本地 `weaknet.keystore.pass`（已 gitignore）取；首次构建随机生成。另修掉 apksigner 主类名写死的问题（34.x 是 `com.android.apksigner`，早期是 `com.android.tools.apksigner`），改为自动探测，换 build-tools 版本不会直接 ClassNotFound |
| **版本号第一次真正生效（v1.1.8）** | `build.sh` 里 `aapt2 link` 写死 `--version-code 1 --version-name 1.0`，`AndroidManifest.xml` 里改了不起作用，APK 装的版本一直是 1.0。改为脚本顶部一处真值，本版起 `versionCode=118 / versionName=1.1.8`。**注：v1.1.9 复测发现这版其实仍没生效**（缺 `--replace-version`），详见下方「版本号始终不生效」一行 |
| **v1.1.8 回归** | 组合编辑器回归 **11/11**（场景名完整无省略号、行内无常驻删除按钮、每行一行、左滑露出删除、点删除生效、＋添加一段恢复三段、点名字弹 14 项单选、选完即时更新、保存后接口读回 `steps[].key` 全对）；组合播放切段实测 3 段各 3 秒**准点切换并循环**；EXE 冒烟 **7/7**（GUI 子系统无 DOS 窗口、进程起来、`/api/status` 有响应、`/api/run` 开关正常、无 conhost、主窗口标题正确）；新 APK 签名证书 SHA-256 `1c69f682…c1d8e` 与 v1.1.7 **完全一致**，可覆盖安装 |
| **版本号始终不生效（v1.1.9 修复）** | v1.1.8 那条「版本号第一次真正生效」的结论是**错的**。build-tools 34 的 `aapt2 link` 默认让 Manifest 里的 `versionCode/versionName` 覆盖 `--version-code/--version-name`，`--replace-version` 不加就白写：两次内容不同的构建 dump 出来都是 `118 / 1.1.8`。加上 `--replace-version` 后实测 `119 / 1.1.9`，并把 Manifest 同步为同一组值做双保险。**版本号真值已统一到 `version.py`**（Manifest 与 build.sh 跟它走，`tests/_t_version.py` 断言，构建末尾用 `aapt2 dump badging` 回读成品校验） |
| **共享服务中文载荷挂死（v1.1.9 修复，P0）** | `ShareNet.serve()` 用 `BufferedReader` 按 **Content-Length 字节数**去读 `char[]`：中文一字 3 字节，`len` 字节里只凑得出 `len/3` 个字符，循环永远读不满 → 卡满 5s SoTimeout → 静默 `close()`，手机端表现为「推送组合一直转圈然后失败」且日志无痕迹。对照实验确证：30 字节中文必挂、120 字节 ASCII 0.1s 完成，与长度无关只与字符/字节比有关。改为 `BufferedInputStream` 按字节读满 `len`（同时去掉原先 `Math.min(len, 65536)` 的静默截断），并补上 catch 日志 |
| **组合 API 删除不复位（v1.1.9 修复，P1）** | 正在播放的组合被 `POST /api/combo/delete` 删掉后，时间轴仍在按已删除的 steps 跑，界面却已从列表里消失，用户无处可停。两端改为：删除的是当前正在播放的组合 → 先 `stop_combo()` 再 `set_profile(全零, "direct")`（`hasLimits()` 为 false 即直连），删别的组合不打断当前场景 |
| **抖动把小延迟压成零（v1.1.9 修复，P2）** | 抖动按 `base ± jitter` 取值，`jitter > base` 时会算出负数延迟 —— 该包比直连还快，弱网测试里出现「零延迟包」直接污染 RTT 统计。两端改为抖动幅度以基础延迟封顶；`base = 0` 时按 `uniform(0, jitter)` 取值，不再出现负值 |
| **控制接口不校验方法（v1.1.9 修复，P3）** | `DELETE /api/status`、`PUT /api/run` 之类一律返回 200，脚本里写错方法也看不出来（v1.1.7 已记为已知偏差）。两端在路由入口加 `GET/POST` 白名单：`/api/*` 的其它方法返回 **405 + `Allow: GET, POST`**，非 API 路径（`/`）不受影响 |
| **v1.1.9 全量回归** | 修复验证 **19/19**（P0 四种载荷全 0.03s 完成且手机侧入库、中文名组合推送成功、两端删播放中组合均回直连、两端 40 次 PING 零延迟包 0 次、两端 405 + `Allow`）；Windows A 组 **31/31**、B 组 **31/31**；真机深度 **34/34**；APK 接口契约 **16/16**；Python 单测 13、参数文案一致性 31；GUI 滚动 20 / 参数 26 / 标签合并 33 / 二级窗口 34；JVM 单测 **36/36**；EXE 冒烟 **7/7** 且包内实测三处修复生效。吞吐稳态（8MB + 预热）：5G 1.05x、4G 1.04x、弱 Wi-Fi 0.99x、拥塞 0.87x、上行 0.91x、8 并发 0.96x |
| **v1.1.9 测试口径修正** | 带宽精度用例改为「**先预热 ≈0.35s 配额，再传 ≥4 倍桶容量**」：令牌桶空闲会攒满 `速率×0.25s` 的突发额度（100 Mbps ≈ 3.1 MB），短样本测的是突发不是稳态，同一档位在 1MB 载荷下反复测出 1.4x / 1.59x / 2.71x。带 `disconnect_pct` 的档位（随机断连）移出带宽精度用例，其丢包/断连语义由 B 组与真机覆盖 |
| **图标统一（v1.1.9）** | 一套图形两处落地：EXE 内嵌 10 档 ico（16~256，已核对最大档数据确实在 exe 内）、窗口与任务栏图标显式覆盖 Tk 默认的羽毛；APK 各密度 mipmap + 自适应图标（前景画在 108dp 画布的中央 70dp，圆形 / 方圆形 / 圆角遮罩下都不裁边）。逐档肉眼核对 16 / 24 / 32 / 48 / 192 / 1024，16px 仍能认出「四格缺一格」。生成器 `gen_icon.py` 复跑两次产物哈希一致（构建可复现）；真机 `install -r` 覆盖安装成功，`versionCode=119`、证书 `1c69f682…c1d8e` 均未变 |
| **pytest 回归体系（v1.1.9，P1）** | 新增 `tests/conftest.py` 夹具（进程内引擎 / 本地源站 / 隔离存储）与 8 个 `test_*.py`：默认套件 **91 项 / 13 秒**全绿（unit 46 + integration 45），`-m perf` 4 项、`-m device` 真机契约 13 项按需运行；存量 `_t_*` 里更细的源码比对由 `test_golden_scripts.py` 统一登记。以上套件均在本地运行，仓库不含 CI workflow |
| **停止限速仍按旧速率下载（v1.1.9 修复，P2）** | 令牌桶只在 pipe 下一轮读取时才 `sync` 新速率，等待中的 `consume` 要等当前 64KB 块按旧速率走完（300 kbps 下约 1.7s 才放行）。改为 `set_profile` 里立即同步上下行桶后，性能门槛实测停止后 **266ms** 放行全部积压（阈值 500ms） |
| **`/api/*` 404 响应形态不一致（v1.1.9 修复）** | Windows 未实现的 API 返回纯文本 `not found`，Android 返回 JSON `{"error":"not found"}`：脚本按 Content-Type 解析时在 Windows 上会崩在「不是 dict」。两端统一为 JSON，非 API 路径仍是纯文本 |
| **UDP 满队列仍会阻塞发送线程（v1.1.9 修复，P2）** | `DelayLine.tryPost` 先在锁内查积压、出锁再调 `post()`，而 `post()` 超限时会 `wait(50)`：并发提交时两个线程都能通过检查，后到者被卡住，与「UDP 满了就丢、绝不阻塞」相反。改为在同一个 `synchronized` 内完成容量判断 + 入队 + 唤醒，超限立即返回 false；`UdpSession` 计入 `udpDropped` 并按 1/2/4/8… 指数退避记一条事件。新增 JVM 用例 `_DelayLineTest`（容量、并发不阻塞、关闭后拒绝、内联/延迟语义）12/12 |
| **Android 每包热路径白做工（v1.1.9 优化，P2）** | `dlog("hp: TCP " + IP 字符串化 + …)` 的参数在 `DIAG=false` 提前返回**之前**就已求值，每个包白白拼 2 次 IP 字符串；TCP 快表查询与回填各自进一次监视器。改为调用点 `if (DIAG)` 包裹（无效/未知包路径一并处理），快表查询合并为一次锁内「查槽 + sameFlow 核对」。按方案要求，每包 `byte[]` 分配与 UDP 逐包 `exec.execute` 两项**未改**——它们需要真机 30s 压测确认占比后再定，本次无测量数据 |
| **控制接口 300 行 if/elif（v1.1.9 重构，P2）** | `handle_ctrl` 改为「(方法, 路径) → 处理函数」表：25 个端点各自成函数，`"*"` 表示不限方法；未注册的 `/api/*` 仍回 JSON 404、非 API 回纯文本 404，方法/状态码逐条与旧实现一致。GUI 的 500ms 刷新不再无条件 `config()` 全部场景/自定义/组合按钮：选中项没变就整轮跳过（按钮重建时重置缓存）。重构由控制接口契约用例 + 12 项 `_t_api_parity` + 金标准脚本验证；另新增 8 项畸形请求用例（负/非数字/超限 `Content-Length`、chunked、Origin、非回环 Host、超长头行、超 100 行头） |
| **发布脚本凭据与路径（v1.1.9，P1-5 卫生项）** | `_release_upload.py` 原先把仓库路径写死为本机绝对路径，并允许 token 从 argv 传入（会留在进程列表与 shell 历史）。改为按脚本位置推导产物路径，token 只读 `GITHUB_TOKEN` 环境变量；tag 仍可用 `RELEASE_TAG` 或 argv 指定 |
| **USB 拔线后仍断网（v1.1.11 修复）** | 根因两层：`adb reverse` 随 adb 会话消失，而全局代理写在系统设置里持久存在；MIUI 等 ROM 还会把全局代理镜像进 Wi-Fi 网络配置，`settings delete` 清不掉，网络校验探测持续打向旧代理。修复：连接时在手机侧装 `setsid` 守护，反向端口消失后自动清理；清理时先写 `http_proxy :0` 再删残留字段 |
| **USB 代理残留（v1.1.12 修复）** | 断开按下拉框选中项而非实际已接管设备；换端口重连会留下旧 reverse 映射；自动还原失败时提示的手动命令是无效的 `settings delete global http_proxy`。三处均改掉，并把 `Address already in use` 报错改为点名 WeakNet.apk 自己占用 8888 |
| **双端契约缺口（v1.1.10 修复）** | Android `/api/status` 缺 `version` / `build` / `me`，未知路径返回 `text/plain`，按 Windows 同构解析的客户端会拿到 `undefined` 或解析异常。补齐三个键（`me` 九字段逐键对齐）并把 404 改为 `application/json` 的 `{"error":"not found"}`；`pytest -m device` 由 11/13 变为 13/13。版本号同步为 1.1.10 / 120（`version.py` 单一真源，`_t_version.py` 14 项断言） |

