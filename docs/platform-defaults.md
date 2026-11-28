# 平台默认值清单（Android SDK / HTTP / DNS）

第三件：凡调用平台 API 或依赖协议规范的地方，先把「不显式设置时默认是什么」查成
**可引用的原文**，再对照代码判断我们是在利用默认值、还是被默认值坑了。

规则：**真值只能来自官方文档或 RFC 原文**。记忆、博客、二手教程一律不作为判定依据；
确实只能靠实测的（OEM 行为），单独放「待真机验证」，不写进结论。

来源：

- Android SDK：<https://developer.android.com/reference/android/net/VpnService.Builder>
- HTTP：RFC 9112（报文定界）、RFC 9110（语义与 Expect）
- DNS：RFC 1035 §4.2（UDP/TCP）
- JDK：`Executors` / `Socket` / `ThreadPoolExecutor` 的 Javadoc

---

## 一、Android `VpnService.Builder`

| # | 条目 | 不设置时的官方默认 | 我们的代码 | 判定 |
|---|---|---|---|---|
| A1 | `setMtu` | 「未设置时使用**操作系统的默认值**」——文档没有承诺 1500 | 显式 `.setMtu(tunMtu())`，默认档 1500 | 显式覆盖，✅ |
| A2 | `setBlocking` | 「`establish()` 返回的 fd **默认是非阻塞的**」 | 显式 `.setBlocking(true)` | 必须设：我们按 `FileInputStream` 阻塞读，非阻塞下 `read` 会拿不到包 | ✅ |
| A3 | 地址族副作用（`addAddress` / `addRoute` / `addDnsServer` / `allowFamily`） | 「若某地址族没有任何地址、路由或 DNS 服务器，则该族的**所有出站流量被阻断**；只要加了其中任意一项，该族即被允许」 | 只加了 IPv4 地址/路由/DNS，未调用 `allowFamily` | **依赖默认值**：IPv6 是被系统确定性阻断，不是「部分机型」；文档已按原文改写，取舍见 §五 |
| A4 | `addDnsServer` | 「若一个都不设，使用**默认网络的 DNS 服务器**」 | 设了两个（原 `8.8.8.8` + `114.114.114.114`） | 显式覆盖，但首选地址在国内不可达 → 命中（H8） |
| A5 | `addAllowedApplication` | 「只要调用过至少一次，就只有列出的 App 允许；**一次都没调用则默认允许全部**」 | 逐个添加、数 `okCount`，为 0 时拒绝建立 VPN | 已有守卫（第八轮），✅ |
| A6 | `addDisallowedApplication` | 「默认所有 App 都允许，除被拒绝的」 | 排除自身，避免控制口 8899 被自己捕获 | ✅ |
| A7 | `setMetered` | 「targetSdk ≥ Q 的 VPN **默认被视为计费网络**」；传 `false` 才「沿用底层网络的计费属性」 | 原未设置（targetSdk 33） | **命中**（H7）：App 会自限码率/后台传输，测的不是真实网络 |
| A8 | `establish()` | 「App 未 prepare 或授权被撤销时返回 `null`」 | 先 `VpnService.prepare()`，再判 `tun == null` | ✅ |
| A9 | `setUnderlyingNetworks` | 不设置 = 走系统默认网络 | 上游 socket 全部 `protect()` 防止回环 | ✅ |

## 二、Android / Java 运行时

| # | 条目 | 默认 | 我们的代码 | 判定 |
|---|---|---|---|---|
| J1 | `Executors.newFixedThreadPool` | 「shared **unbounded** queue」——源码是无参 `LinkedBlockingQueue` | `CtrlServer` 已有界；`ShareNet` 原用该工厂 | **命中**（H9）：慢客户端多时任务无限堆积 |
| J2 | `Socket.setSoTimeout` | `0` = **无限等待** | `TcpSession` 未设，靠自有 2/5 分钟空闲 sweeper 回收；`UdpSession` 设 30s | 依赖默认值但有兜底，记录不改 |
| J3 | `Socket.setTcpNoDelay` | 默认 `false`（Nagle 开启） | Android 显式 `true`；Windows accept 侧 `_tcp_nodelay()` 补设 | ✅（不设会给小包路径平白加 40~200ms） |
| J4 | `ServerSocket.setReuseAddress` | 平台相关（JDK 文档明示） | 显式 `true` | ✅ |
| J5 | `DatagramSocket` 接收缓冲 | 平台相关 | 显式 512KB；超出按 MTU 派生的缓冲时**整包丢弃并计数**，不静默截断 | ✅ |

## 三、HTTP：RFC 9112 §6.3 报文长度判定（按优先级）

| # | 规范原文要点 | 我们的代码（修复前） | 判定 |
|---|---|---|---|
| P1 | 第 1 步：对 HEAD 的响应、以及 1xx/204/304 响应，头后第一个空行即结束，**不能有正文** | 先判 `HEAD/204/304`、后判 1xx | **命中**（H5）：对 HEAD 的 103 被当成最终响应，真正的 200 留在上游缓冲 |
| P2 | 第 2 步：CONNECT 的 2xx 之后立即变成隧道，客户端 MUST 忽略 CL/TE | CONNECT 走裸隧道，不解析 HTTP | ✅ |
| P3 | 第 3 步：同时有 TE 和 CL 时 **TE 覆盖 CL**；转发的中间件 **MUST 先删掉 CL**；处理完 MUST 关连接 | 请求与响应都把 CL 原样转发 | **命中**（H1/H3） |
| P4 | 第 4 步：TE 存在且 **chunked 是最后一个编码**才按块读；否则响应读到连接关闭；请求若 chunked 不在末尾 MUST 回 400 | 用 `b"chunked" in te` 子串判断 | **命中**（H4） |
| P5 | 第 5 步：CL 无效（非数字/负数/多个不一致）→ 请求 400、响应 502 | 解析失败时当成「有体」，继续往下走 | **命中**（H6）：残留字节被当成下一条请求 |
| P6 | 第 6 步：有效 CL 定长 | 按 CL 读满 | ✅ |
| P7 | 第 7 步：请求既无 TE 也无 CL → 无体 | 同 | ✅ |
| P8 | 第 8 步：响应既无 TE 也无 CL → 读到连接关闭，且必须关客户端 | `mode == "eof"` → `Connection: close` + 关闭 | ✅ |
| P9 | §9.3：HTTP/1.1 **默认持久**；HTTP/1.0 默认不持久，除非显式 `keep-alive` | `_client_keepalive` 按版本分别判断 | ✅ |
| P10 | RFC 9110 §10.1.1：收到 `100-continue` 的服务器 **MUST** 回 100 或最终状态 | 把 Expect 原样转给上游，自己立刻阻塞读正文 | **命中**（H2）：上游不发 100 时双向死锁 |

## 四、DNS：RFC 1035 §4.2

| # | 条目 | 原文要点 | 我们的代码 | 判定 |
|---|---|---|---|---|
| D1 | UDP 53 | 报文限制 **512 字节**（不含 IP/UDP 头），超长截断并置 **TC 位** | 只做透传，不构造 DNS 响应；超过缓冲整包丢弃并计数 | ✅ 边界由真实服务器决定，我们不伪造 TC |
| D2 | TCP 53 | 消息前有 **2 字节长度字段**（不含自身） | 按字节流透传，不改写 | ✅ |
| D3 | 重传策略 | 客户端应「先试其他服务器/地址」，最小重传间隔 2–5 秒 | 由 App 的系统解析器决定 | 与 A4 相关：首选服务器不可达时，这段等待会实打实计入用户体验 |

---

## 五、命中的缺陷与修法

### H1（P1）请求同时带 TE 与 CL：CL 被原样转给上游

RFC 9112 §6.3 第 3 步要求中间件**先删 CL 再转发**。原实现把所有头原样透传，
上游若按 CL 定界，会把 chunk 头当正文、把剩余字节当下一个请求——请求走私的经典形态。

修法：`_rewrite_origin_request` 检测到 TE 就丢掉 CL；`framing_ambiguous` 为真时，
响应发完强制关闭客户端连接（§6.1 的 MUST）。

### H2（P1）`Expect: 100-continue` 被降级成裸隧道，上游不发 100 时双向死锁

原调用方把带 Expect 的请求交给 `run_tunnel` 双向直通，注释写的是「客户端会等 100，
先读正文会死等」。但隧道只是原样转发：**上游若也不主动发 100**（很多源站在读到
正文前不会发），客户端等 100、源站等正文，两边互等到超时。集成测试实测 8 秒无响应。

修法：由代理承担应答责任——转发前删掉 `Expect`，向 HTTP/1.1 客户端立即回
`100 Continue`，再按 CL 读正文。HTTP/1.0 客户端不回 100（规范要求忽略该期望）。

### H3（P2）响应同时带 TE 与 CL：CL 被原样转给客户端

客户端按 CL 读完就停，chunked 的结尾留在连接里，下一条响应必然错位。
修法：转发响应头时，只要响应带 TE 就删掉 CL。

### H4（P2）TE 用子串判定

`Transfer-Encoding: chunked, gzip` 中 chunked **不是最后一个编码**，按 §6.3 第 4 步
只能读到连接关闭；原实现用 `b"chunked" in te` 判成分块，解析必然错位。
修法：按逗号拆 token，只看最后一个编码，并去掉 `;` 参数。

### H5（P2）HEAD 与 1xx 的判定顺序反了

原实现先判 `method == HEAD`，于是对 HEAD 的 103 被当成「无正文的最终响应」，
发完就去等下一条请求。修法：1xx（101 除外）一律先判为 `interim`。

### H6（P2）非法 Content-Length 没有拒绝

`Content-Length: abc` / 负号 / 多个不一致的值，按 §6.3 第 5 步请求必须 400。
原实现解析失败时当成「有体」继续，客户端的残留字节会被当成下一条请求。
修法：新增 `_req_framing_error()`，统一校验 TE 末尾编码与 CL 取值，命中即 400 + 关连接。

### H7（P2）`setMetered` 默认把 VPN 标成计费网络

targetSdk 33，官方文档明确「Q 及以上的 VPN 默认被视为 metered」。App 会据此降码率、
推迟后台传输、切 Data Saver 行为——测出来的是「系统告诉 App 这是计费网」之后的表现。
修法：`setMetered(false)`（沿用底层网络的真实计费属性）。**API 29 才有**，minSdk 24
必须加版本判断，否则低版本开 VPN 直接 `NoSuchMethodError`。

### H8（P2）DNS 首选 8.8.8.8 在国内不可达

不是 SDK 默认值，而是「8.8.8.8 通用」这个隐含假设。国内网络到 `8.8.8.8:53` 基本不可达，
它排第一时，每个未命中缓存的解析都要先等它失败，这段等待会被算进「网络慢」。
修法：改为 `223.5.5.5` + `119.29.29.29`，查询内容仍原样转发。
没有改成「不设 DNS、沿用底层网络」：IPv6-only 载波下底层 DNS 可能是 IPv6 地址，
而我们的 tun 只接管 IPv4，反而会解析不了（取舍见 §六）。

### H9（P2）`ShareNet` 用无界线程池

`Executors.newFixedThreadPool(4)` 内部是无界 `LinkedBlockingQueue`。慢客户端一多，
任务在队列里无限堆积，accept 循环看着没堵，实际把堆吃光（`CtrlServer` 早因同一原因改过）。
修法：4 线程 + 64 容量的有界队列 + `AbortPolicy`；拒绝时立刻关掉连接，让压力沿
TCP 窗口回传。新增 `_RunSharePoolTest`（6 条断言）钉住「有界 + 满了拒绝」。

---

## 六、依赖了默认值、但判定可接受的

| 项 | 为什么不动 |
|---|---|
| IPv6 被系统阻断 | 只加 IPv4 地址/路由是**唯一**能保证「不把未解析的 IPv6 包写回 tun」的做法。`allowFamily(AF_INET6)` 会让 IPv6 直接落到真实网络：连通性回来了，但这条流量**完全不受弱网影响**，测出来的是混合口径。取舍是「明确断」而不是「静默绕过」，文档必须写「会被阻断」。 |
| `TcpSession` 不设 `SO_TIMEOUT` | 默认 0（无限）确实可能挂住，但已有 2/5 分钟空闲 sweeper 兜底；给每条连接设读超时反而会误杀长连接下载。 |
| 单条 `addRoute("0.0.0.0", 0)` | 有资料称部分国产 ROM 会静默忽略 `/0`，建议拆成 `/1 + /1`。该说法**没有官方文档或可复现的真机证据**，不写进结论；列入 §七。 |
| 内部 `HttpIn` 拒绝 chunked | 控制/共享接口只服务自家客户端，遇到 chunked 直接 400 比猜长度安全；这是刻意行为，不是遗漏。 |
| HTTP/1.0 客户端收到 chunked 响应 | 没有降级成定长：必须先把整个响应缓冲下来才能算长度，内存与尾延迟代价都高，而主流客户端早已是 1.1。记录为已知边界，不装作支持。 |

## 七、待真机验证（不作为结论）

1. 单条 `0.0.0.0/0` 路由在目标机型上是否被忽略（对照 `/1 + /1`）。
2. `setMetered(false)` 后，`ConnectivityManager.isActiveNetworkMetered()` 是否确实随底层网络变化。
3. 换 DNS 后，WeakNet 报告的 DNS 故障注入比例是否与真机 `dumpsys` 的解析失败率同量级。

---

## 八、这一轮的边界

- 只覆盖**纯 JVM / 本机可判定**的部分；Android 三项改动（A3/A7 与 DNS）需要真机确认
  系统行为，单元测试只能钉住「代码确实这么调了」。
- RFC 侧的修复全部有集成测试（真实 socket，不是 mock），用例见
  `tests/test_integration_http_framing.py` 与 `tests/test_unit_http_framing_rules.py`。
- 未打包、未发布。
