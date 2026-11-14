# v1.1.16 历史测量归档

P0-1「建立性能基线契约」要求的归档。三份文件原在项目根 `_measure_20261029/`，
随项目搬迁散落在仓库之外；这里按版本收进仓库，保证「任一指标都能回答和哪个版本、
哪台机器比」。

| 文件 | 采集时间 | 内容 | SHA-256 |
|---|---|---|---|
| `win_measure.json` | 2026-10-29 | Windows EXE 冷启动 5 轮、RSS、MEI 解包体积 | `2fd2ff83b3039903000788d6de0f93dc1f66ae9028451cf259325c139172a9dd` |
| `tun_16mb.json` | 2026-10-29 | Android 16MB 直连 3 轮吞吐与 `cpu_ms/MB` | `c06f40e0ddb111058d250d38a13a38d4ac2033cfdd194cb998cc5b4e55f9b3e2` |
| `conn_threads.json` | 2026-10-29 | Android conn0=52 / conn10=82 线程快照与线程名分布 | `2212ae820097dfde0dab79eed05397b8b6e06c007885b630292d6b23c6fd47ae` |

## 使用边界（重要）

- **只作历史参考，不参与自动化判定。** 自动门禁读的是 `tests/perf/_perf_baseline.json`
  （带机器指纹、版本、spec 哈希、输入规模、依赖版本、单位口径）。
- 这三项采集于 2026-10-29，早于 v1.1.16 发布（11-06），**没有记录机器指纹、
  Python/Android 版本、输入规模、预热与重复次数**，也没有 p95。
  因此跨批次比较没有意义 —— 2026-11-13 复测时历史「214.2 MiB/s」无法复现，
  同机同口径得到 178.3 MiB/s，正是这个原因。
- `tun_16mb.json` 只有 3 轮，`conn_threads.json` 是单次快照，均不满足方案
  「≥7 次取中位数 + p95」的测量纪律，只用于说明量级与线程构成。
- `win_measure.json` 的 RSS 在 11-06 修过采集脚本（psutil → Win32 ctypes），
  这里保留的是**修复前**的原始文件，不要与 `_弱网测试数据_2026-11-06.json` 混用。

## 2026-11-13 新增证据（本轮补齐）

这四份由仓库内工具生成，都带 `context()` 完整上下文（机器指纹、版本、依赖、
单位口径）或明确的数据来源，**不参与自动化门禁判定**，只作为结论依据：

| 文件 | 生成命令 | 内容 |
|---|---|---|
| `iperf3_external_truth_2026-11-13.json` | `tools/perf_iperf.py` | 8 场景 × 7 次 × 20 s `-O 3`：直连 P1/P4/P8/UDP、经代理 P1/P4/P8/P1-R；中位 + p95 + 每轮流数 + 错误列表 + **发送端/接收端两套数字** |
| `iperf3_curve_2026-11-13.json` | `tools/perf_iperf.py --curve` | 带宽偏差曲线 6 档（500 / 1000 / 4000 / 8000 / 20000 / 50000 kbps） |
| `iperf3_curve_latency_loss_2026-11-13.json` | `tools/perf_iperf.py --curve-latency --curve-loss` | P2-1 的另外两个维度：**高延迟 + 低带宽**组合、**丢包（停顿）**组合 |
| `perf_coldstart_2026-11-13.json` | `tools/perf_coldstart.py` | 5 轮 `-X importtime` 导入耗时分解、历史 EXE 冷启动差值、WPR/WPA 待执行命令 |
| `perf_drain_ab_2026-11-13.json` | `tests/perf/test_drain_ab.py -m perf` | P1-5 两档限速受控 A/B（交替顺序、唯一变量 `DRAIN_HIGH`），用于证伪收益 |
| `slow_callbacks_2026-11-13.json` | `tests/perf/test_slow_callbacks.py -m perf` | P0-2 慢回调清单：事件循环里超过 50 ms 的同步阻塞回调 |
| `slow_callbacks_before_fix_2026-11-13.json` | 同上（修复前） | 对照组：修复前命中 3 条、最长 **94.0 ms**，全部落在 `handle_proxy()` 的进程名反查 |
| `exe_measure_2026-11-13.json` | `tools/perf_exe_measure.py --exe _build_exe_20261113/WeakNet.exe --runs 5` | 当前代码新构建的 EXE 冷启动 5 轮：就绪中位 **2.15 s**、进程树 RSS 中位 **52.5 MB**、`_MEI*` 解包 **21.4 MB** |
| `exe_measure_baseline_1106_2026-11-13.json` | 同上，`--exe WeakNet.exe`（11-06 发布版） | 对照组：同机同时段测旧产物，就绪中位 **2.14 s**、RSS 中位 **52.1 MB** —— 与新构建的差在噪声内，说明本轮代码改动**对冷启动无影响** |

> EXE 测量**不覆盖仓库里的 `weaknet/WeakNet.exe`**：新构建走独立 distpath
> （`_build_exe_20261113/`），旧产物保持 11-06 原样（指纹 `c9948caa…fe5edd35`）。
> 冷启动口径 = 进程拉起到 `/api/status` 返回 200 的墙钟，与 `perf_coldstart.py`
> 读 `win_measure.json` 的 `ready_s` 同口径，可跨批次比。

**统计口径（2026-11-13 修正，重要）**

主口径 = iperf3 的 **`sum_received`（接收端累计字节）**，即真正抵达链路终点的量。
**不要**用 `sum_sent`（iperf3 客户端本地发送计数）当限速精度结论：多流共享同一个
瓶颈时客户端几乎全程阻塞在 `send()` 里，本地计数系统性少计，最多能差 36%。

实测证据（限速 8000 kbps、−P 8、20 s）：

| 并发 | `sum_sent` | `sum_received` | 代理 `bytes_up` |
|---|---:|---:|---:|
| −P 1 | 8,347 kbps | **7,988 kbps** | 8,010 kbps |
| −P 4 | 8,502 kbps | **8,035 kbps** | 8,028 kbps |
| −P 8 | 6,708 kbps | **8,009 kbps** | 8,083 kbps |

个别流更极端：`sender.bytes` = 0 而 `receiver.bytes` = 2.7 MB。两个互相独立的
计数器（对端接收、代理自计）都落在设定值上，说明**原先的「−P 8 低 36%」是
统计口径造成的假缺陷，不是产品限速不准**。

口径由 `tests/perf/test_iperf_extract.py`（5 条单测）锁死；归档 JSON 里同时保留
`median_sender_kbps_1024` 与 `median_sender_receiver_gap_pct`，可回溯自证。

**引用规则**：

- 正式数值以 JSON 为准，不以 `_tmp/iperf_*.log` 的日志尾部为准；日志只用来核对
  告警（`socket.send() raised exception.`、`Task was destroyed`）。
- `iperf3_external_truth_2026-11-13.json` 会被同一命令重跑覆盖，引用时请连同文件
  里的 `date` / `context` 一起说明批次。
- `direct_*` 是本机回环，只证明外部链路不是瓶颈；UDP 经代理场景因 Windows 侧是
  TCP 应用层代理而**不可测**，见 `docs/precision.md §4`。
- **测量窗口统一**：8 场景与三张曲线都用 `-t 20 -O 3 -P N`、7 次取中位 + p95。
  同一批里所有档位窗口一致，可以横向比较；跨批次比较仍须核对 `context.inputs`。

## 第四轮复测（2026-11-13 20:50 起）

| 文件 | 内容 |
|---|---|
| `iperf3_external_truth_recheck_2026-11-13.json` | 8 场景、`--repeats 5 --time 10`。经代理四档落在 8000 kbps ±1%（p1 8,080 / p4 7,989 / p8 7,938 / 反向 7,987），与正式批次（7,988 / 8,010 / 7,939 / 7,991）一致 |
| `exe_measure_recheck_2026-11-13.json` | 新构建 2.18 s / 51.1 MB vs 11-06 旧产物 2.25 s / 51.6 MB，`_MEI` 均 21.4 MB |
| `perf_drain_ab_2026-11-13.json`（覆写） | P1-5 补跑：8000 kbps −1.52%、50000 kbps +0.43%，仍无收益 |
| `slow_callbacks_2026-11-13.json`（覆写） | 阈值 50 ms，命中 0 条，最长 0.0 ms |

> 复测批次窗口与正式批次不同（`-t 10` vs `-t 20`），只用于**验证可复现性**，
> 不与正式批次混合计算中位数。`weaknet_p8` 发送端 4,095 kbps（口径差 −46.7%）
> 与接收端 7,938 kbps 并列，再次印证「多流共享瓶颈时 `sum_sent` 系统性少计」。

## 采集质量自证（2026-11-14）

`tools/perf_iperf.py` 从本批起给每轮都记环境，并把「这批数据能不能用」写成
结构化结论。归档里多出三类字段：

| 位置 | 字段 | 含义 |
|---|---|---|
| `scenarios.*` | `stdev_kbps_1024` / `cv_pct` / `mad_kbps_1024` / `iqr_kbps_1024` / `p95_is_extreme` | 离散度。**`cv_pct > 5` 即禁止引用中位数**；`p95_is_extreme` 在 `n<20` 时为 true（最近秩法下 p95 就是 max） |
| `scenarios.*.rounds[]` | `cpu_busy_pct` / `mem_avail_mb` / `engine_kbps` / `window_s` / `elapsed_s` | 轮级环境与三方对账。`engine_kbps` 是代理层自计，与 iperf3 完全独立 |
| `scenarios.*.quality` | `usable` / `suspect_rounds` / `engine_crosscheck_ok` / `reason` | 数据自己的结论，不是人的判断 |
| 顶层 | `env` / `schema=2` / `tool` | `tool` 由 `iperf3 --version` 动态取得（不再硬编码版本号） |

**为什么必须加**：`-P 8` 曾混入 2,459 / 2,918 kbps 的离群轮，而中位数与 p95
（7,946）与稳定批**完全相同**，旧统计无法区分；`cv_pct` 在那批是 36%，稳定批是 0.1%。
另有一次重测并行跑了编译与测试导致 `direct_p1` 从 16 M 掉到 13.5 M，
归档里没有任何环境信息，只能靠人回忆判定作废。

**新增归档**：

| 文件 | 内容 |
|---|---|
| `iperf3_external_truth_2026-11-14.json` | 8 场景 × 7 次 × 20 s `-O 3`，轮间冷却 1.5 s，带完整 `rounds` / `quality` / `env` |

**判读要点**：

- `suspect_rounds` 用「CPU > 中位 + 3×MAD **且** > 15% **且** 高出中位 ≥15pp」三重判据。
  对 `direct_*` 高速档要谨慎：那里的 CPU 90%+ 是**测量对象自己吃满 CPU**（吞吐与 CPU
  强负相关），不是外部抢占，不应据此作废整批。
- `engine_vs_received_pct` 正常为 **+0~+2%**（引擎含 CONNECT 握手与重传、按墙钟计），
  不是「越接近 0 越好」；负值或 >12% 才说明链路串了或口径被改坏。
- 直连档 `engine_kbps` 合法为 **0.0**，非 0 说明测量链路串了（阴性对照）。

## 后续

重新打包后若要更新这三项，请用 `_measure_20261029/measure_win.py` 与
`measure_dev.py`，并把产物连同 `tests/perf/baseline.py:context()` 的全部字段一起归档，
不要只留数字。
