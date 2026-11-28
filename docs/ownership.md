# 资源所有权四问（第 2 件）

第 1 件（`docs/op-matrix.md`）找的是「两个操作按某个顺序执行会怎样」。这一件
换一个维度：**不看操作，看资源**。凡是我们会去改、而别人也可能在改的东西，
都按同样四问过一遍：

1. **谁写**：写入方只有我们，还是多方共享？
2. **写前存不存原值**：能否原样还原，而不是「恢复成默认」？
3. **释放前比对不比对**：当前值还是不是我们写的？被改过就放手。
4. **部分失败怎么回滚**：写到一半失败，会不会留半套配置？

判断标准只有一条：**用户不装这个工具时是什么样，卸掉之后必须还是什么样。**

---

## 一、四个资源 × 四问

| 资源 | 谁写 | 写前存原值 | 释放前比对 | 部分失败回滚 |
|---|---|---|---|---|
| **R5 系统代理注册表**<br>`HKCU\...\Internet Settings` | 我们 + 公司策略 + 用户手动 + 其它代理软件 | 5 个字段快照落盘（`weaknet_sysproxy.json`） | 逐字段 `_reg_same`；代理地址易主则路由决策字段全放手 | 按已写字段逐个还原 + 删记录 + 放锁 |
| **R6a ADB 反向映射**<br>`adb reverse tcp:P tcp:P` | 我们 + 其它 adb 工具 + WeakNet.apk | 不备份原值（映射是「有/无」），但记 `reverse_created` 标明归属 | 只有自己建的才撤；别人的留着并说明 | 建完回读失败、后续步骤失败都会撤掉刚建的那条 |
| **R6b 手机全局代理**<br>`settings global http_proxy` | 我们 + 用户手动 + 公司 MDM | 3 个字段（含两个旧 ROM 兼容字段）存记录 | 当前值 ≠ 我们写的 → 保留现状不还原 | 各阶段 `_rollback`：先还原代理，再撤映射（顺序不能反） |
| **R7a TUN 路由**<br>`VpnService.Builder` | 只有本进程 | **不需要**：内核虚拟接口，fd 关闭即销毁，不存在「残留」 | `stopVpn` 无条件关闭，幂等 | 所有早退路径由 `startVpnGuarded` 兜底：撤前台 + `stopSelf` → `onDestroy` → `stopVpn` |
| **R7b 监听端口**<br>代理端口 / 控制台 8899 | 我们 + 任何先占用的程序 | 不需要（端口不是持久状态） | 重绑失败时按实际监听地址回报，不假设成功 | Windows：先绑新→失败关旧再绑→仍失败恢复旧监听；Android：绑定失败只记日志、不发前台通知 |

**结论：R7a / R7b 四问全过，本轮不改。** R5、R6 各查出一处真实缺陷，见下。

---

## 二、命中项

### O1 系统代理：只中和了静态代理，没中和 PAC / 自动检测（P1，静默失效）

**四问里缺的是第 2 问**：写前存的原值只覆盖 `ProxyEnable` / `ProxyServer` /
`ProxyOverride` 三项，而决定「流量到底走不走这个代理」的字段不止三个。

WinINET 用 `INTERNET_OPEN_TYPE_PRECONFIG` 打开会话时（Chrome / Edge / .NET
默认都是这个），解析顺序是：

```
AutoConfigURL（PAC） → AutoDetect（WPAD） → ProxyServer（静态代理）
```

**这个顺序不用靠经验推断**：WinINet 专门提供了
`INTERNET_OPEN_TYPE_PRECONFIG_WITH_NO_AUTOPROXY` 这个枚举，语义就是
「从注册表取配置，但**不要**用自动配置」。一个「不要自动配置」的开关之所以
需要存在，正是因为默认的 `PRECONFIG` 会用。也就是说 `AutoConfigURL` 非空时，
我们写进去的 `ProxyServer` **会被整个忽略**。企业网里 PAC 极常见，这类用户的
现场是：

- 界面显示「已接管」，报告也按弱网口径统计
- 实际一点弱网都没加，测出来的全是真实网络的质量
- 更麻烦的是它不报错，用户只会觉得「这工具没用」

修法（`weaknet.py`）：

- `SYSPROXY_REGS` 扩到 5 个字段，新增 `SYSPROXY_ROUTE_REGS` 标记「路由决策」字段
- 接管时 `AutoConfigURL` 写 `None`（走 `DeleteValue`）、`AutoDetect` 写 0
- 释放时按所有权还原；**代理地址已易主时这组字段一个都不还原** ——
  别人的配置是在「PAC 已关」的前提下写的静态代理，我们把 PAC 装回去等于让
  他的代理失效（PAC 优先）
- 旧版记账（只有 3 个键）按「记录里有的字段」比对，缺的视为当初没动过；
  再次接管时用当前注册表值补齐 `prev`，避免把用户真实的 PAC 当自己的删掉
- **`DeleteValue` 只吞 `FileNotFoundError`**，其余异常（权限被拒、策略锁定）
  往上抛并触发整体回滚 —— 原来裸 `except OSError: pass` 会把「PAC 删不掉」
  当成「PAC 本来就没有」
- **写完立刻回读自检**（`sysproxy_owned()`）。企业策略锁或安全软件可能让
  `SetValueEx` 返回成功但值不落地；不校验就是「显示已接管、实际零弱网」。
  自检不过 → 按 `prev` 回滚全部字段 + 清记录 + 放锁 + 返回失败

### O2 USB 手机：强杀后重连，把「自己写的值」当成用户原值（P1，原配置永久丢失）

`connect()` 的第一步是 `read_proxy()` 快照。进程被强杀后重启，手机上的
`http_proxy` 还是上次我们写进去的 `127.0.0.1:P`，于是：

```
read_proxy → 127.0.0.1:8888   ← 当成「用户原值」存进 prev
... 用户断开 → 还原成 127.0.0.1:8888
```

结果手机永久指向一个没人监听的端口。用户原本的公司代理（比如 `10.0.0.9:3128`）
在这一步彻底丢失，而现象只是「断开后手机没网」。

系统代理那条路径早就防了这一手（`if sysproxy_owned(): prev = 旧 prev`），
手机侧一直没有对应实现。

修法（`usb_phone.py`）：

- 记录里已有 `prev`，且手机当前代理 **等于记录里我们写的 proxy** → 沿用旧 `prev`
- 记录里 `reverse_created` 或 `pending` 为真 → 认领现有映射：上次建的映射如果
  还活着（adb server 没退），本次必须认领，否则断开时不会撤，留下一条没人管的
  映射。`pending` 是「建映射之前先落的记录」（见 O5），那时手机代理还没被改，
  不能用「值相等」判断归属

### O3 USB 残留没有跨进程发现机制（P2，可观测性）

系统代理有 `release_stale_sysproxy`：启动时读记账，端口没人听就按原值还原。
手机侧没有对应物 —— 记录留在磁盘、代理字段留在手机，而 WeakNet 已经不在了。
更隐蔽的是「重启电脑也不一定好」：adb server 活着时反向映射还在，手机侧监听
看着是通的，守护脚本也就不会触发还原。

修法：

- 记录里加 `pid`；`stale_serials()` 只返回**写下它的进程已经不在了**的记录
  （另一个实例开着的时候不能动它的接管）
- GUI 刷新设备时提示：「检测到上次未清理的接管：<serial>，选中它点断开」
- 只有一台在线残留时自动选中它，避免用户点「断开」清的是另一台正常设备

**没有做自动清理**：清理要改手机设置，必须让用户看见并自己确认；两个实例同时
跑时自动清理还会拆掉另一个实例正在用的接管。

### O5 建映射与写记录之间被杀（P2，孤儿反向映射）

原来的顺序是「建 `adb reverse` 映射 → 写接管记录」。进程若死在两步之间，映射
留在 adb server 里，磁盘上却没有记录：`stale_serials()` 只看记录，发现不了它。
`disconnect(serial, port)` 在没有记录时确实会按给定端口删映射，但**没人知道该
对哪台手机、哪个端口发起这次调用** —— 用户看到的只是「手机连不上网」。

修法是「记录先于动作」（`usb_phone.connect` 第 1.5 步）：

- 动手机之前先写 `pending=True` 的记录，建完映射再升级成正式记录；
- 崩溃窗口里磁盘上一定有 pending 记录 → 残留检测能列出它，`disconnect()` 对
  pending 也执行 `reverse --remove`（映射不存在时 adb 返回 not found，按成功处理）；
- 任一步失败都用 `_restore_record` 把记录退回上一次的样子，不能把上一次仍有效的
  接管记录降级成 pending；
- 记录写不进去（磁盘不可写）直接中止接管 —— 没有 `prev`，断开时只能清空代理，
  用户原来的公司代理会永久丢失，比不接管更糟。

用例：`test_pending_record_lands_before_reverse_is_created`、
`test_kill_between_mapping_and_record_leaves_cleanable_record`、
`test_reconnect_claims_reverse_left_by_pending_record`、
`test_failed_connect_restores_previous_record`、
`test_connect_aborts_when_record_cannot_be_written`。

---

## 三、判定为安全、未改动的部分

| 检查点 | 依据 |
|---|---|
| 系统代理多实例互斥 | `_sysproxy_lock_acquire` 按 pid 判活，死进程的锁视为残留可接管 |
| 系统代理写到一半失败 | 已写字段逐个按 `prev` 还原，记录与锁一起清 |
| 系统代理强杀残留 | 端口没人听才动，有人听说明是另一个活实例 |
| ADB 映射被别人占用 | `tcp:P` 已指向别处 → 拒绝连接，不覆盖别人的映射 |
| ADB 映射本来就有 | `tcp:P → tcp:P` 已存在 → 沿用但记为「不是我建的」，断开不撤 |
| 手机代理被第三方改动 | 回读值 ≠ 我们写的 → 保留现状，只做说明 |
| 手机代理还原顺序 | 先还原代理字段再撤映射：反过来的话中间那段时间手机断网 |
| TUN 早退路径 | `startVpnGuarded` 覆盖全部早退：撤前台 + `stopSelf` → `onDestroy` → `stopVpn` |
| TUN 撤销授权 | `onRevoke()` → `stopVpn()`，与正常停止同一条路径 |
| 监听端口切换失败 | 恢复旧监听并返回真实 host，GUI 按返回值提示 |

---

## 四、遗留风险

1. **`DefaultConnectionSettings` 二进制 blob 未纳入记账**。Windows 把代理配置
   另存一份在 `HKCU\...\Internet Settings\Connections`，WinINET 读取时以它为准；
   实测「直接改散值 → blob 会被系统同步」，所以当前写法可用，但同步时机依赖
   系统实现，不是我们能保证的契约。彻底做法是走 `InternetSetOption` 写
   per-connection 配置，改动面较大，本轮不做。
   用例 `test_takeover_never_writes_connections_blob` 锁住「不写坏」这一半：
   blob 在接管 / 还原前后逐字节不变，且它不在 `SYSPROXY_REGS` 里。哪天纳入
   记账，这条会红 —— 二进制格式、回滚与跨端契约必须一起改。
2. **PAC 用户的接管语义变了**：接管期间 PAC 被摘掉，公司内网域名不再走 PAC
   判定。这与「接管全部流量」的目的一致，但用户如果依赖 PAC 做内外网分流，
   接管期间会全部进弱网代理。README 需要说明。
3. **手机侧残留只提示不自动清理**：用户从不打开 USB 面板就发现不了。要彻底
   解决得在启动时探测一次 adb（有设备才查），代价是启动变慢。
4. **`stale_serials` 依赖 pid 判活**：pid 被系统回收后可能误判「还活着」。
   概率极低，且这种情况下本来也有另一个进程在跑。
