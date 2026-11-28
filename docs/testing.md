# 测试入口

仓库里的测试分四层。**日常改动只需要跑第一、二层**，真机层按需。

## 1. 单元 / 集成回归（本机 ~30 秒）

```bash
py -3 -m pytest -q                 # 默认排除 perf 与 device
py -3 -m pytest -m unit -q         # 只跑纯逻辑
py -3 -m pytest -m integration -q  # 只跑真实 socket 链路
```

分层标记定义在 `pytest.ini`：`unit` / `integration` / `perf` / `device`。

> **性能基线含 `spec_hash` / `version` / `build`**：这三项任一与当前构建不一致时，
> `tests/perf/baseline.py` 的 `compare()` 返回 `stale-baseline`（不判 ok/fail）并提示重刷 ——
> 参数或版本变更后历史中位数已不可比。重刷：`WN_PERF_UPDATE_BASELINE=1 py -3 -m pytest -m perf`；
> 不要手改 `tests/perf/_perf_baseline.json` 里的数字。

## 2. 静态检查

```bash
py -3 -m ruff check .              # E/F：未定义名、未使用导入、语法级问题
```

只开 `E`、`F`，不做风格强制。`tests/_*.py` 是探针脚本，排版类规则已豁免；
`F821`（未定义名）在所有文件上仍然生效。

> **契约一致性脚本的覆盖范围**：`python tests/_t_api_parity.py` 默认只校验 Windows 侧
> API 契约（进程内隔离服务，无需设备）；Android-only 6 项（`/api/float` 读取、旧路径
> `/api/share*` 别名等）只在传 `--android` 时执行。Android 侧契约需在真机连接后先
> `adb forward tcp:18899 tcp:8899`，再本地运行
> `python tests/_t_api_parity.py --android http://127.0.0.1:18899`；CI 不覆盖 Android-only 项。
> 跨端规格 / 算法常量由 `tests/_t_spec_parity.py` 校验，版本一致性由 `tests/_t_version.py` 校验，
> 二者不需要设备。
>
> **发布数字**由 `tests/_t_release.py` 校验。默认只看仓库内能判定的部分（README 三处
> 版本号是否对齐、短指纹是否真是完整摘要的前缀、`_jw/run.sh` 的套件清单与磁盘上的
> `_*Test.java` 是否双向对齐、`docs/testing.md` 声称的套件数是否等于实际）；加
> `--online` 才联网比对 Release 资产的字节数与 `digest`。发版用 `--strict`：额外要求
> README 指向的就是最新正式版。该脚本的 12 项单测会把 README 逐条改坏，确认它真的会红。
>
> **期望来源**由 `tests/_t_expect.py` 校验：每个测试模块必须在 `SOURCES` 登记真值来源；
> 断言里出现「被测常量/函数 ⇒ 被测输出」的镜像比较、或把实现公式抄一遍的，必须在
> `MIRRORS` / `FORMULAS` 登记并写明「这个值本身谁保证」。详见
> [测试期望的来源](test-truth.md)。

## 3. Android（纯 JDK，无需设备）

```bash
cd android
sh _jw/run.sh
```

27 套测试类，覆盖令牌桶、延迟队列、参数夹取、MTU、组合计划、控制口 Host 校验、
共享鉴权等。CI 的 `lint-and-java` job 会跑同一份脚本。

套件清单以 `_jw/run.sh` 的 `for T in ...` 为唯一来源：`tests/_t_release.py` 会
双向核对「清单里写的」与「磁盘上有的」，新增测试文件忘了列入会被它判失败 ——
那种情况下测试永远不跑，而 CI 依然全绿。

## 4. 真机 / 真网络（需要设备或外网）

按用途选一个入口，不要挨个跑：

| 场景 | 入口 |
|---|---|
| 参数采集七相位（限速/时延/MTU/前提/完整性/资源） | `tests/_t_20261116_param.py all --rounds 3` |
| 时延专项复核（curl 精确计时 + 线性拟合） | `tests/_t_delay_curl_verify.py` |
| APK TUN 数据面（透明转发、限速、丢包） | `tests/_t_apk_tun_real.py` |
| APK 边界（拒连、黑洞、超 int 参数） | `tests/_t_apk_edge.py` |
| Windows 全功能 | `tests/_t_win_full.py` |
| 版本真值一致性 | `tests/_t_version.py` |
| 发布数字真值（README 的字节数 / SHA-256 / 套件数） | `tests/_t_release.py --online` |
| 测试期望来源（防「改实现 → 改期望 → 绿」） | `tests/_t_expect.py` |
| 变异测试（测试能不能抓住改坏的实现） | `tests/_t_mutation.py`（抽样 99 个变异体，约 4 分钟） |
| 崩溃一致性（写注册表途中被 kill） | `tests/_probe_crash.py`（20 个场景，约 20 秒） |

变异探针会把 `WN_TEST_TIMEOUT` 收到 5 秒：被改坏的循环条件经常变成死循环，
默认 30 秒会让一轮变异跑一小时。正常测试不受影响（默认仍是 30 秒）。

约定：
- 真机脚本必须在**VPN 已授权、`tun0` 已建立**后跑，否则流量绕过 TUN，
  限速/时延全部失真（表现是「稳态采样 0 个点、速率等于直连」）。
- 脚本会自己重建 `adb forward tcp:18899 tcp:8899`；设备后台冻结时先
  `adb shell am start -n com.weaknet.app/.MainActivity` 再跑。

## 5. 历史脚本

`tests/_t_*.py` 共 70 余个，包含早期逐项验证与一次性探针。**新增测试请写进
`tests/test_*.py`（pytest 收集）**，不要再加 `_t_` 脚本；旧脚本保留作为历史证据，
不再维护参数兼容性。清理时按前缀成组归档（`dev` / `combo` / `note` / `apk` /
`win` / `share` / `usb`），不要逐个删。
