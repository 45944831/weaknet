# 内置场景参数出处与对照表

> 回答「这 17 个预设的数值参考了谁」。本项目 1024 口径（1 kbps = 1024 bit/s）；
> RTT = latency_up_ms + latency_down_ms（两个方向相加才是一次往返）。
> 外部参照与逐项核对来自 `_参数准确性外部验证_2026-11-14.md`（2026-11-14 全网检索）。
> 口径换算：项目 kbps = SI kbps × 1000 / 1024（差 2.4%）。

## 结论分级

- **合理**（13 支）：wifi / 4g_weak / 5g / 5g_weak / wifi_weak / congestion（时延）/ high_loss /
  high_latency / highspeed_rail / elevator / flapping / blackhole / dns_fail / airplane
- **数值偏保守 ×2，引用时注意**（2 支，**产品决策待定**）：
  - `4g`：RTT 100ms vs 现网 LTE 30–50ms —— 若贴合现实改 20/30（RTT 50），既有基于 4g 的测试结论需重看
  - `satellite`：RTT 1400ms vs GEO 单程 250ms（天通一号实测往返 600–700ms）—— 若为「双跳/星间链路」极端档请在文案注明，否则改 350/350（RTT 700）
- **带宽取值说明**：`wifi` 50Mbps 偏低于 Wi-Fi 5 实测 100–400Mbps（保守，可用）；
  `congestion` 512kbps 属「极端拥塞」档；`satellite` 2Mbps 更像窄带卫星，宽带卫星为 10–50Mbps
- **丢包率**：全部在压测常用区间（1–30%），无需改。注意「丢包」实现为停顿（stall_ms），
  非 TCP 重传语义——引用数据必须带上 `docs/precision.md` §3 第 1 条

## 外部参照（查到确切数值的）

| 来源 | 数值 |
|---|---|
| Chrome DevTools（Chromium `NetworkManager.ts`，三版本一致） | Slow 3G 400/400 kbps @400ms；Fast 3G 1.44Mbps/675kbps @150ms（latency 字段有 ×5/×3.75 模型补偿，只取标称值做量级对照） |
| Puppeteer `PredefinedNetworkConditions` | Fast 4G 8.1/1.35 Mbps @60ms |
| Android 模拟器 `-netdelay` | UMTS 35–200ms、edge 80–400ms |
| 现网 LTE 实测口径（多篇时延分级） | 30–50ms（游戏分级 <30 优 / 30–50 好 / 50–100 凑合） |
| GEO 卫星 | 单程 250ms、天通一号实测往返 600–700ms |
| 3GPP / ITU | 5G eMBB 用户面 4ms、URLLC 1ms（空口理论值，非现网） |

**未取得可引用数值、未参与判定**（查了没有）：Apple Network Link Conditioner 预设数值、
`tc netem` 默认参数、Ookla 5G 中位速率、运营商级丢包/时延分位数。

## 本项目取值原则（出处声明）

1. 预设是**一套自洽的产品预设**，不是任何外部标准的拷贝——取值原则是
   「现实区间内偏保守」（弱网测试工具宁可把用户测得更慢，不虚报快）。
2. 场景命名对齐现实档位（4G/5G/高铁/电梯…），数值取该场景的**弱侧**或**极端侧**。
3. 文案与参数不一致属缺陷（2026-11-14 已修 3 处：4g/5g 的 RTT 文案、down_kbps
   单位说明「1000」→「1024」）；数值与现实的偏离属**产品决策**，修前必须评估
   既有测试结论的失效范围（见「数值偏保守」两条）。
4. 后续调整任何预设数值，必须同时：改 `spec.json` → `tools/gen_spec.py` 重生成
   → `_t_spec_parity.py` 23 项 → 重打 APK 与 EXE（spec 烤进包）→ 更新本表。
