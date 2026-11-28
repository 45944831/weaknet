# 崩溃一致性：进程在写注册表途中被杀

系统代理的还原逻辑，单测一直只覆盖「函数返回失败」：`SetValueEx` 抛 OSError，
`_sysproxy_takeover` 的 `except` 把已写字段回滚。但真实事故是进程**没有机会
执行回滚**——taskkill、断电、蓝屏发生在两次 `SetValueEx` 之间，内存里的
`prev` 随进程消失，只剩落盘的接管记账。

`tests/_probe_crash.py` 用独立子进程 + 写穿式假注册表复现这一类现场：
子进程在第 N 次写之后直接 `os._exit(9)`，父进程再启动一个新进程调用
`release_stale_sysproxy()`，检查注册表是否逐字段回到接管前。

## 场景矩阵

| 场景 | 注入点 | 期望 |
|---|---|---|
| 失败注入 ×5 | 第 1~5 个字段写入前抛 OSError | 返回 False；注册表 = 原值；无记账、无锁 |
| PAC 删除被拒 | `DeleteValue(AutoConfigURL)` 抛 PermissionError | 同上（不能把「删不掉」当成功） |
| 崩溃接管 ×5 | 第 1~5 个字段写完后 `os._exit(9)` | 下次启动按记账还原；无记账、无锁 |
| 崩溃还原 ×5 | 还原第 1~5 个字段后被杀 | 下次启动继续还完剩余字段 |
| 回滚中崩溃 | 第 5 个字段写失败 → 回滚第 1 个字段后被杀 | 下次启动仍能还原 |
| 记账落盘失败 | `_sysproxy_state_write` 返回 False | 进程内接管/还原仍正确 |
| 另一实例存活 | 记录端口 8899 有人监听，本次启动用 8888 | 不能按记录还原（先判记录端口，再判本次端口） |
| 记账丢失 | 崩溃后删掉 `weaknet_sysproxy.json` | 只能退化为「关 ProxyEnable」（已知降级） |

## 首轮实测发现

探针先在**修复前**的逻辑上跑了一遍（`--legacy` 负对照，把恢复函数换回
「全字段匹配才还原」）：20 个场景里 **10 个 FAIL** —— 5 个部分接管、4 个
部分还原 / 回滚中崩溃、1 个「另一实例存活」，全部集中在「按不完整的记账
做决定」这一处 —— 证明探针确实在检查还原结果，而不是空跑。

### P0：部分接管后删记录就走，整机断网

老逻辑要求「记录里的每个字段都等于我们写入的值」才执行还原：

```python
if not all(_reg_same(cur, written, n) for n in keys):
    _sysproxy_state_clear()
    return False
```

崩溃停在两次写之间时，现场必然混合了「我们写的」和「还没写的（原值）」，
`all()` 不成立 → 记账被当成过期记录删掉，一个字段都不还原。后果：

- `ProxyEnable` 已经写成 1；
- `ProxyServer` 已经指向 `127.0.0.1:8888`（进程已死，没人监听）；
- 记账被删，下次启动也救不回来。

用户看到的是「WeakNet 崩过一次之后，整台电脑上不了网」。

**修复**：逐字段判归属，只还原「当前值仍等于我们写入值」的字段；等于原值的
字段不动；代理地址被改成第三个值时，按 `_sysproxy_release` 的同一规则保护
开关 / PAC / 自动检测。部分接管、部分还原、完整接管三种现场走同一条路径。

```python
restore = [n for n in keys if _reg_same(cur, written, n)]
if server_foreign:
    restore = [n for n in restore if n not in SYSPROXY_ROUTE_REGS]
```

### P1：恢复时用本次端口探测，把活实例当成残留

`release_stale_sysproxy(port)` 的 `port` 来自本次启动参数，但记账里明明存了
上次的 `port`。上次跑在 8899、这次默认 8888 时：

- 8899 上的实例还活着，系统代理正是它在接管；
- 本次用 8888 探测，`can_bind(8888)` 为真（没人监听）；
- 于是把活实例的代理配置按「上次崩溃残留」还原掉，界面显示未接管，
  实际弱网已失效。

**修复**：有记账时用 `st["port"]` 探测；只有无记账的退化路径才用调用方端口。
探针新增「另一实例存活」场景，旧逻辑下同样 FAIL。

### P2：死进程的锁文件永久残留

还原完成后 `_sysproxy_lock_release()` 只删「pid == 自己」的锁；强杀留下的
记录里是死 pid，于是锁文件一直在。它不会拦住下一次接管（`_sysproxy_lock_acquire`
会覆盖），但属于明确残留，崩溃探针把它标成失败。

**修复**：`pid` 已死也删。

### 修复后实测

```
PASS  失败注入：写第 1~5 字段抛 OSError（5 项）
PASS  PAC 字段删不掉（策略锁定）
PASS  崩溃注入：接管写第 1~5 字段后被杀（5 项）
PASS  崩溃注入：还原写第 1~5 字段后被杀（5 项）
PASS  回滚到一半被杀
PASS  记账落盘失败：进程内仍能接管并还原
KNOWN 记账丢失：退化路径只关 ProxyEnable
```

`test_unit_sysproxy.py` 同步补了 5 条用例：部分接管、只写了开关、部分还原、
单一字段易主、死进程锁清理；全部用假注册表，不碰真实 HKCU。

## 已知边界

- **记账本身丢了就救不回来**：没有 `prev`，只能按端口精确匹配关掉
  `ProxyEnable`，用户原来的 `ProxyServer` 已被覆盖。探针把这条标为 `KNOWN`，
  不伪装成通过。记账写在所有注册表写入之前且 fsync + 原子替换，正常路径
  不会丢；只有用户手工删文件或磁盘损坏才会走到这里。
  用例 `test_lost_record_falls_back_to_disable_only` 把降级结果钉死：还原阶段
  **只写** `ProxyEnable=0`，`ProxyServer` / `ProxyOverride` / PAC / 自动检测
  全部维持我们写入后的样子。谁要把「猜一个原值写回去」加进来，这条会红。
- **Android 侧未纳入本探针**：`VpnService` 没有注册表这类全局副作用，
  进程被杀后 TUN 由系统回收；手机代理的残留由 `stale_serials()` 提示。
  孤儿反向映射（建好映射、还没写正式记录就被杀）曾是一条查不出来的残留，
  已改成**先写 pending 记录再建映射**：残留检测看得见它，`disconnect()` 按
  `pending` 撤映射，失败路径把记录退回上一次的样子（`usb_phone.connect`
  第 1.5 / 2 / 4 步）。用例：`test_kill_between_mapping_and_record_leaves_cleanable_record`
  等 5 条。
- **真实 Windows 注册表未参与**：探针全部用假 winreg。写真实 HKCU 需要
  管理员/策略环境，不适合放进自动检查；真实环境的行为由 `test_unit_sysproxy.py`
  的假注册表 + 人工真机复核覆盖。

## 复跑

```bash
python tests/_probe_crash.py        # 20 个场景，约 20 秒
python tests/_probe_crash.py -v     # 失败时带子进程输出
python tests/_probe_crash.py --legacy  # 负对照：换回旧逻辑，应有 9 项 FAIL
```
