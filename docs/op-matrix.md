# 操作序列矩阵（第 1 轮）

**为什么需要它**：前 8 轮审查都是「读函数 → 找分支不自洽」。这种方法对单函数逻辑有效，
但对「操作 A 做完再做 B 会怎样」结构性失明 —— 外部审查的 11 条 P1 里，9 条落在这类格子上。

**方法**：先列出所有「有副作用的操作」，再列出它们共享的资源。冲突不发生在函数之间，
发生在资源上 —— 两个操作只要写同一份资源，就必须判定先后顺序。矩阵按资源聚合，
比 12×12 全枚举更可读，也不会漏。

**复跑方式**：`python tests/_probe_ops.py`（纯函数级探针，不启端口；输出每个序列后的
`limit_on / preset_key / profile 关键参数 / test_open`）。新增或修改 `set_limit` /
`select_scene` / `start_combo` 后必须重跑。

---

## 一、12 个操作（Windows）

| 编号 | 操作 | 入口 | 写的资源 |
|---|---|---|---|
| W1 | 开启限速 | `set_limit(True)` | profile、limit_on、会话、组合 |
| W2 | 关闭限速 | `set_limit(False)` | profile、limit_on、会话、草稿、组合 |
| W3 | 运行中切场景 | `select_scene` → `set_profile` | profile、last_profile、令牌桶、flap |
| W4 | 停止态选场景/调参 | `select_scene`（未开启分支） | pending_profile/pending_key |
| W5 | 开始播放组合 | `start_combo` / `select_combo` | combo_task、profile（逐段）、limit_on |
| W6 | 停止播放组合 | `stop_combo` | combo_task、combo_id/step/deadline |
| W7 | 接管系统代理 | `set_sysproxy(True)` | HKCU 注册表 + 接管记账文件 |
| W8 | 解除系统代理 | `set_sysproxy(False)` | 同上 |
| W9 | 切换监听地址 | `restart_proxy(host)` | proxy_server、proxy_host |
| W10 | USB 连接手机 | `usb_phone.connect` | ADB reverse 映射、手机 `http_proxy`、接管记录 |
| W11 | USB 断开手机 | `usb_phone.disconnect` | 同上 |
| W12 | 退出程序 | `on_close` / atexit | 以上全部 |

Android 侧同构 12 项：`A1` 开启、`A2` 停止、`A3` 运行中切场景、`A4` 停止态选场景、
`A5` 开始组合、`A6` 停止组合、`A7` 暂停、`A8` 恢复、`A9` 改接管范围（重建）、
`A10` 改 MTU（重建）、`A11` 改参数、`A12` 退出/强杀。

## 二、资源 → 操作（冲突判定表）

| 资源 | 写它的操作 | 冲突形态 | 判定口径 |
|---|---|---|---|
| R1 `profile` / `limit_on` | W1 W2 W3 W5 W6 | 开关状态与实际参数脱节 | 开关必须由「参数是否真的动流量」决定，只有显式开关可用意图覆盖 |
| R2 `pending_*` 草稿 | W2 W3 W4 | 关闭时丢参数 / 草稿串到别的场景 | key 相同才认；组合 key 不存 |
| R3 `combo_task` | W1 W2 W3 W5 W6 | 两个播放器抢改参数 / 空转占事件循环 | 任何切场景/关闭都必须先 `stop_combo` |
| R4 会话基线 | W1 W2 W3 W4 | 历史流量算进本次 | 只在真正「关→开」时开新会话 |
| R5 系统代理注册表 | W7 W8 W12 | 关掉别人的代理 / 残留 | 所有权记账 + 只还原自己写的字段 |
| R6 ADB 映射 + 手机代理 | W10 W11 W12 | 退出后手机断网 / 换机残留 | 按 serial 记账 + 各阶段可回滚 |
| R7 监听 socket | W9 W12 | 绑失败后哪都连不上 | 先绑新再关旧，失败恢复旧监听 |
| R8 报告 payload | W1 W2 W12 | 停止后导出读到全零 | 会话增量 + `last_profile` 兜底 |

## 三、逐格结论（按操作展开）

符号：**✓ 安全** ｜ **✗ 缺陷（本轮已修）** ｜ **↺ 入口已处理（内部函数不负责）** ｜ **? 待真机**

### W1 开启限速

| 后继 | 结论 | 依据 |
|---|---|---|
| W1（重复开启） | ✓ | `ensure_test_session` 幂等；`on && !wasOn` 才开新会话 |
| W2 | ✓ | 见 W2 |
| W3 | ✓ | 运行中切场景走 `set_profile`，令牌桶同步 `sync(rate)` |
| W4 | — | 运行中不存在「只选中」分支 |
| W5 | ✓ | 组合段逐个 `set_profile`，`played==0` 已退出 |
| W7 | ✓ | 系统代理指向本机端口，与开关无关 |
| W9 | ✓ | 切监听不影响已建立的连接 |
| W12 | ? | 退出时若未先停止，会话不关闭（设计：进程内状态不跨进程） |
| 组合已失效 | **✗** | 兜底分支把 `limit_on` 置 True，而 `last_profile` 可能为全零 → 已修为按 `has_limits` 落 |

### W2 关闭限速

| 后继 | 结论 | 依据 |
|---|---|---|
| W1（关→开） | **✗** | 运行中调过的参数被预设值覆盖（4G 调 800 → 关 → 开 → 回 60）。已修：关闭前存草稿 |
| W4 | ✓ | 停止态选场景只记草稿；`pending_key` 相同才认 |
| W5 | ↺ | 关闭会先 `stop_combo`，组合不会在后台换参数 |
| W12 | ✓ | 关闭后 `limit_on=False`、`test_open=False`，导出读增量 + 上次生效参数 |
| 并发 W1 | ✓ | 实测 `True+False` 并发最终为 False（最后一次点击生效） |

### W3 运行中切场景

| 后继 | 结论 | 依据 |
|---|---|---|
| 切「直连」 | **✗** | `select_scene` 末尾无条件 `limit_on = True`，而参数已清成全零 → 界面显示弱网、实际零限制。已修为 `has_limits(prof)` |
| 直连 → 切回内容场景 | ✓ | 此时已成「停止」态，切场景只算选中并留草稿（与 W4 同语义，双端一致） |
| W5（组合中手动切） | ✓ | `select_scene` 检测 `combo_task` 且 key 不同 → 先 `stop_combo` |
| W6 后 | ✓ | `stop_combo` 不清 `preset_key`，再开启可复原 |

### W4 停止态选场景 / 调参

| 后继 | 结论 | 依据 |
|---|---|---|
| W1 | ✓ | 草稿 key 匹配才用，否则按预设/`last_profile` |
| 换另一支场景再开 | ✓ | 草稿 key 不同不误用（实测 3g 草稿 → 选 4g → 开 = 4g 预设） |
| W12 | **已知限制** | 草稿只在内存，退出即丢（不落盘）。下一轮若做，需进 settings 存储并加版本迁移。用例 `test_stopped_draft_is_memory_only_across_restart` 钉住两件事：草稿没被写进 settings、冷启动后 `pending_profile` 为空 |

### W5 开始播放组合

| 后继 | 结论 | 依据 |
|---|---|---|
| W2 | ✓ | `set_limit(False)` 先 `stop_combo` |
| W5（换组合） | ✓ | `start_combo` 先 `stop_combo` |
| W3（手动切） | ✓ | 同上，`select_scene` 拦截 |
| 播放中删组合 | ↺ | 两个删除入口（GUI `delete_combo_by_id`、API `DELETE /api/combo`）都先 `stop_combo`。内部 `delete_combo` 不负责停播 —— 新增入口时容易漏，见「遗留风险」 |
| W12 | ✓ | GUI 退出先 `loop.stop`，播放任务随循环结束 |

### W6 停止播放组合

| 后继 | 结论 | 依据 |
|---|---|---|
| W1 | ✓ | `preset_key` 仍是 `c:<id>`，再开启重播这支 |
| W3 | ✓ | 停止后切场景正常落地 |
| 组合 key 关闭 | **✗** | 关闭时把「当前段参数」存成草稿会污染下次开启。已修：组合 key 不存 |

### W7 / W8 系统代理接管与解除

| 后继 | 结论 | 依据 |
|---|---|---|
| 用户原本有公司代理 | ✓ | 所有权记账：未接管则 `_sysproxy_release` 一个字段都不写 |
| 接管期间被别的程序改过 | ✓ | `_reg_same` 逐字段比对，只还原仍等于自己写入值的字段 |
| W9（切监听） | ✓ | 系统代理指向 `127.0.0.1:<port>`，切到 `0.0.0.0` 后仍可用 |
| W12 | ✓ | `_auto_release_sysproxy` / `on_close` 走所有权判定 |
| 强杀后重启 | ✓ | `release_stale_sysproxy`：有记账按原值还原，无记账要求 `ProxyServer` 精确等于本端口 |

### W9 切换监听

| 后继 | 结论 | 依据 |
|---|---|---|
| 端口被占用 | ✓ | 先绑新 → 失败关旧再绑 → 仍失败恢复旧监听并抛错 |
| W7 | ✓ | 恢复后 `proxy_host` 是实际值，GUI 按返回值提示 |
| W12 | ✓ | 关窗时随循环关闭 |

### W10 / W11 USB 接管

| 后继 | 结论 | 依据 |
|---|---|---|
| 连接 → 拔线 | ✓ | `start_watch` 守护 + 按 serial 记账 |
| 连接 → 退出 | ✓ | `on_close` 等 3s 让在途 adb 落地，再 `_usb_restore_on_close` |
| 连接 A → 断开 → 连接 B | ✓ | `record_for(serial)` 按机器隔离，`forget` 只在还原成功后调用 |
| 部分失败 | ✓ | 各阶段 `_rollback`；回读映射失败不再直接返回 |

### W12 退出

| 检查项 | 结论 | 依据 |
|---|---|---|
| 系统代理 | ✓ | 所有权判定后再解除 |
| 手机代理/映射 | ✓ | 后台线程 + 限时等待还原 |
| 监听/任务 | ✓ | `after_cancel` 后 `loop.stop` |
| 日志 | ✓ | `atexit` 注册 `flush_logs` |
| 停止态草稿 | **已知限制** | 不落盘（用例 `test_stopped_draft_is_memory_only_across_restart`） |

## 四、本轮修复（3 项，双端同构）

| 编号 | 缺陷 | 证据（修复前） | 修复 |
|---|---|---|---|
| S1 | 运行中调参 → 停止 → 再开启，参数回退到预设 | 探针 S1b：`4g` 调 800 → 关 → 开 → **60** | `set_limit(False)` 关闭前存草稿（组合 key 除外）；Android `setLimitOn(false)` 同构 |
| S2 | 运行中切「直连」，开关仍显示开启 | 探针 S6：`limit=True / delay=0 / open=False` | `select_scene` 末尾按 `has_limits(prof)` 落；Android `selectScene` 同构 |
| S3 | 开启时目标组合已失效且无历史，开关停在开启 | 探针 S14：`limit=True / delay=0` | 兜底分支按 `has_limits(G.profile)` 落；Android `resumeIfCombo` 同构 |

回归守护：`tests/test_unit_op_sequences.py`（5 项）、`_RunToggleRestoreTest`（+8 项断言）。

## 五、遗留风险与下一轮

1. **`delete_combo` 依赖调用方停播**：内部函数不调 `stop_combo`，两个入口各自处理。新增第三个入口（如共享导入覆盖）时容易漏 → 建议把「删当前播放的组合」下沉到 `delete_combo` 的调用约定里，或改成 `async def`。
2. **停止态草稿不落盘**：退出即丢，属产品级取舍，需要先定存储格式与迁移策略。
3. **Android 的 A9/A10（改接管范围 / MTU 重建）与 A12（强杀）未纳入本轮**：需要真机 + `adb` 时序，纯 JVM 只能覆盖纯函数。
4. **W12 未先停止就退出**：会话不关闭、报告不可导出（设计如此），需在文档里明确「退出前先停止才能导出报告」。
5. 矩阵是**静态 + 探针**级别的，`W9`/`W10` 的真机时序（端口冲突、拔线竞态）仍需设备验证。

---

第 2 件换了维度：不看操作、看资源，按「谁写 / 存不存原值 / 释放前比不比 / 部分失败怎么回滚」
把这四个资源逐项过了一遍，产出见 [`docs/ownership.md`](ownership.md)（命中 2 项 P1 + 1 项 P2）。
