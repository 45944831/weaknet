# 变异测试：测试真的会红吗

测试数量、覆盖率、断言条数都只能证明「代码被执行过」，不能证明「写错了会被
抓住」。本项目已经出现过两次反例：

* 测试跟着实现写 —— 实现里的 bug 被当成期望值（`BURST_SEC` 超发那轮）；
* 恒真断言 —— `assert W.X == W.X` 写了两遍也没人发现。

变异测试从另一头问：**把实现里的一个运算符改错，测试会不会红？**
不会红的变异体叫「幸存者」，它要么指向缺断言，要么是等价变异（改了但行为不变）。

## 探针

`tests/_t_mutation.py`，零第三方依赖（不需要 mutmut / PIT），4 分钟能跑完：

1. `ast` 定位目标函数，`tokenize` 在函数体范围内生成**单点**变异：
   比较符互换（`>` ↔ `>=` ↔ `<`）、`and/or` 互换、删 `not`、整数 ±1、
   布尔取反、`+ - * /` 互换；
2. 把仓库复制到临时目录（**绝不改工作区**），只替换 `weaknet.py`；
3. 跑该函数对应的测试文件：绿 = 幸存，红 = 杀死，语法错 = 无效变异；
4. 按函数均匀抽样（每个函数 4~18 个，共 99 个），输出得分与幸存者清单。

隔离与稳定性上的三个必要细节：

* **每个变异体都删一次 `__pycache__`**：同一秒内改写同长度源码时，CPython 会
  继续加载旧 pyc —— 那等于「跑了变异体、其实还是原实现」，会把幸存者错判成杀死。
* **每个变异体 20s 硬超时**：死循环变异体（例如 `take > 0` 改成 `> 1` 后
  `remain` 永不减少）会让 `run_async` 的取消失效，没有硬超时整轮就挂住。
  超时按「杀死」计 —— 挂死也是可观察的行为变化。
* **不用 `-k` 按测试名收窄**（第一版踩过，见下）。

```bash
python tests/_t_mutation.py                    # 99 个变异体，约 4 分钟
python tests/_t_mutation.py --list             # 只看变异体
python tests/_t_mutation.py --only Bucket.consume
python tests/_t_mutation.py --min-score 85     # CI 门禁：低于门槛返回 1
```

覆盖范围：令牌桶（`sync/cap/consume`）、`DelayLine`（`submit/_drain/_idle/
_mark_dead/_peer_alive`）、HTTP 定界（`_hdr_get/_hdr_values/_hdr_tokens/
_resp_frame/_resp_keepalive/_client_keepalive/_req_framing_error/_down_head/
_rewrite_origin_request`）—— 都是纯函数、失败后果直接（限速不准、连接挂死、
报文错位），值得先把测试强度钉死。

## 三轮结果

| 轮次 | 得分 | 幸存 | 说明 |
|---|---|---|---|
| 首轮 | 58.6%（58/99） | 41 | 其中 9 个等价变异，32 个真缺口 |
| 次轮 | 81.8%（81/99） | 18 | **数据受污染**：补断言与跑测试并行，部分新用例在跑到一半时才落盘 |
| 第三轮 | **89.9%（89/99）** | 10 | 干净基线，4 分钟跑完，10 个幸存者全部为已判定的等价变异 |

次轮为什么不算数：我一边补用例一边让探针在后台跑。探针每个变异体都会重新
复制测试文件，于是「先跑到的变异体用旧测试、后跑到的用新测试」，同一份报告
里混了两种基线。**结论只认第三轮**；这条也写进流程：跑变异时不要改测试。

第三轮分函数：

| 函数 | 杀死/总数 | 得分 |
|---|---|---|
| `Bucket.sync` / `Bucket.cap` | 3/3 · 1/1 | 100% |
| `Bucket.consume` | 10/12 | 83.3% |
| `DelayLine._peer_alive` | 8/8 | 100% |
| `DelayLine.submit/_drain/_idle/_mark_dead` | 19/19 | 100% |
| `_hdr_get` / `_hdr_values` | 2/4 · 2/2 | 50% / 100% |
| `_resp_frame` | 12/14 | 85.7% |
| `_resp_keepalive` / `_client_keepalive` | 6/6 · 3/3 | 100% |
| `_req_framing_error` | 8/9 | 88.9% |
| `_down_head` | 8/10 | 80% |
| `_rewrite_origin_request` | 7/8 | 87.5% |

## 补上的 32 个缺口

按后果排序，每行都对应一条现在会红的用例：

| 缺口 | 变异体 | 不改会怎样 | 补的用例 |
|---|---|---|---|
| 尾字节不计费 | `remain > 0` → `> 1` | 每个响应的最后 1 字节白送，单连接看不出，压测吞吐偏高 | `test_bucket_charges_single_byte` |
| 等待公式写成乘法 | `wait_s = remain / rate` → `*` | 单字节等 0.25s（高 256 倍），小包业务直接卡死 | 同上 |
| 令牌刚好够用仍睡 1ms | `remain <= 0` → `<` | **每一次 take == remain 都多等 1ms** —— 最常见路径，2G 档位下每秒几十次无谓调度 | `test_bucket_exact_fit_returns_without_sleeping` |
| 差 1 字节提前放行 | `remain <= 0` 的 `0` → `1` | 扣到剩 1 字节就返回，最后一个字节不等待 | `test_bucket_last_byte_must_wait` |
| 死连接判定被短路 | `_peer_alive` 的 `is_closing`/`_conn_lost` 取反 | 往已 RST 的连接继续投递，延迟队列积压到超时才释放 | `test_peer_alive_*` 4 条 |
| 关闭检查抛异常当活着 | `except: return False` → `True` | `is_closing()` 自己坏了就永远判活，连接泄漏 | `test_peer_alive_treats_broken_close_check_as_dead` |
| 中止后不清计数 | `_mark_dead` 的 `not`/`False` 变异 | `pending` 永不归零，优雅关闭一直等到超时 | `test_abort_*` |
| 队列空但未投递完就退出 | `_idle` 的 `and` → `or` | 已出队未写出的块被丢弃（响应尾部缺失） | `test_idle_waits_for_inflight` |
| 正延迟走零延迟快路径 | `delay <= 0` → `<= 1`、入队到期时间 `+` → `-` | 弱网延迟配置形同虚设，且没有任何用例量过「到底等了多久」 | `test_positive_delay_does_not_take_fast_path` |
| 100 Continue 当最终响应 | `100 <= status` → `<` | `Expect: 100-continue` 的客户端等正文、代理等下一个请求，双向挂死 | `test_100_continue_is_interim` |
| 带参数的 chunked 判错 | `split(b";", 1)[0]` 的 `[0]` → `[1]` | `chunked;q=1` 被当成未知编码，回退到 EOF 定界，keep-alive 失效 | `test_chunked_with_parameters_is_chunked` |
| EOF/1xx 连接被复用 | `_resp_keepalive` 的 `return False` → `True` | 往已经关闭的上游发下一个请求，客户端收到 502 | `test_eof_and_interim_are_not_reusable` + 6 条组合 |
| 非 cl 模式长度非 0 | `return "eof", 0` 的 `0` → `1` | 目前调用方只在 `cl` 分支读长度，线上无症状；契约被误读就会多读 1 字节 | `test_non_cl_modes_report_zero_length` |
| `_down_head` 默认改写头 | 两个 `force_close=False` 默认值 → `True` | 默认路径就给所有响应加 `Connection: close`，keep-alive 全废 | `test_down_head_default_keeps_headers_untouched` |
| 端到端头被一起删 | `i > 0 and ...` 的 `and` → `or` | `X-Keep` 这类端到端头被当成逐跳头删掉 | `test_down_head_force_close_keeps_end_to_end_headers` |
| Upgrade 丢失 | `_rewrite_origin_request` 的 `is not None` 取反 | WebSocket 握手失败 —— 「代理开着网页版聊天全挂」 | `test_rewrite_origin_keeps_upgrade_and_drops_proxy_headers` |
| 默认 keep-alive 丢失 | `keep_alive=True` 默认值 → `False` | 每条请求都 `Connection: close`，每条都重连 | `test_rewrite_origin_defaults_to_keepalive` |

## 判定为等价变异的 10 个

等价变异不是「测试不好」，是变异算子改到了行为不变的位置。每条都要给出理由，
否则下次换个人跑又会重新怀疑一遍（`_hdr_get` 那一行覆盖 2 个变异体）：

| 位置 | 变异 | 为什么等价 |
|---|---|---|
| `Bucket.consume` `while remain > 0` | `>` → `>=` | `remain == 0` 只可能来自 `consume(0)`；变异后多进一次循环，`take = 0`、`remain <= 0` 立刻返回，只多做一次惰性补币（下次消费本来也要补） |
| `Bucket.consume` `if take > 0` | `>` → `>=` | 函数体在 `take == 0` 时全是「减 0 / 减 0」，`wait_s` 在 `if` 之外计算，两条路径完全一致 |
| `_hdr_get` 两处 `split(b":", 1)` | `1` → `2` | 只看 `[0]` / `[1]`，maxsplit 是 1 还是 2 不影响前两段 |
| `_resp_frame` chunked 分支 | `("chunked", 0)` 的 `0` → `1` | 长度字段只在 `mode == "cl"` 时被读（`weaknet.py` 3305 行） |
| `_resp_frame` 非法 CL 分支 | `("eof", 0)` 的 `0` → `1` | 同上，`eof` 模式不读长度 |
| `_req_framing_error` `split(b";", 1)[0]` | `1` → `2` | 同 `_hdr_get`，只取第一段 |
| `_down_head` `if i > 0` | `>` → `>=` | `i == 0` 是状态行，`"HTTP/1.1 200 OK"` 不可能等于 `connection`/`content-length` |
| `_down_head` `split(b":", 1)[0]` | `1` → `2` | 同上，只取第一段 |
| `_rewrite_origin_request` `split(b":", 1)[0]` | `1` → `2` | 同上，只取第一段 |

## 探针自身的两个缺陷（都是这轮查出来的）

**1. `-k` 按测试名收窄会制造假幸存者。** 第一版为了省时间，给
`_rewrite_origin_request` 写了 `-k "content_length or expect_100"`。后来补的
`test_rewrite_origin_defaults_to_keepalive` 名字不含这两个词，**根本没被执行**，
于是「补了断言仍然 SURVIVED」。现在改为按文件收窄（整个文件都跑），
单轮从 2 分钟涨到 4 分钟，换取「不会漏跑」。

**2. 补测试和跑测试并行，报告不可信。** 见「三轮结果」。探针每个变异体都重新
复制一次测试目录，所以中途落盘的用例只对后半段生效。

这两个缺陷的共同点：**探针自己也会说谎**。所以每次改完探针，必须拿一个已知
会红的变异体验证它真的会红（等价于崩溃探针的 `--legacy` 负对照）。

## 局限

* 只覆盖 16 个纯函数。asyncio 转发主循环、GUI、注册表接管、Android 侧没有
  变异体 —— 它们的失败模式是时序/系统状态，单点变异测不出来；
* 单点变异，测不出「两处同时改才出错」的组合缺陷；
* 变异只能证明「改坏会红」，不能证明「期望值本身是对的」。后者是
  `tests/_t_expect.py`（期望来源）与 `_t_spec_parity`（跨端对拍）的职责，
  两者互补，谁也替代不了谁；
* 得分不是越高越好：追到 100% 只能靠给等价变异加无意义断言。当前 89.9%
  减去 10 个等价变异，有效得分是 100%（89/89）。

## 门禁

`.github/workflows/perf.yml` 的 `mutation` job：夜间与手动触发（不进 PR，
单轮 4 分钟对合并门禁太重），`--min-score 85`。门槛取 85 而不是 89.9，
是给等价变异留余量 —— 新增函数进 `TARGETS` 时会先出现几个真缺口，应该由人
判定后补断言或登记等价，而不是让门禁数字逼着写凑数断言。
