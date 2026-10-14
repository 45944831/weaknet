/**
 * 控制台页面（真实 DOM）验证「⚙ 悬浮窗设置」：
 *   展开卡片 → 勾选/排序显示行 → 自定义文本与变量插入 → 预览随之变化 →
 *   应用后真的 POST 出去 → 收起/显示飘窗
 *
 * 悬浮窗只有 Android 端有，Windows 引擎没有 /api/float，
 * 所以这里起一个 mock 引擎，按 Android 端 CtrlServer 的返回结构应答。
 */
const path = require("path");
const fs = require("fs");
const http = require("http");
const { JSDOM } = require("jsdom");

const BASE = __dirname;
const CTRL_PORT = 8986;
const ORIGIN = `http://127.0.0.1:${CTRL_PORT}`;

const checks = [];
const ck = (name, cond, extra = "") => checks.push([name, !!cond, extra]);
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---- mock 引擎：只实现控制台初始化要用的那几个接口 ----
const FIELDS = [
  ["scene", "场景名", "当前弱网场景", "地铁 3G"],
  ["limit", "限速值", "上下行限速", "限速 ↓1.5M ↑512k"],
  ["rate", "实时速率", "每 500ms 实测", "实时 ↓120KB/s ↑8KB/s"],
  ["loss", "弱网参数", "延迟 / 抖动 / 丢包", "延迟 120±30ms · 丢包 3%"],
  ["conns", "连接数", "活跃 / 累计", "连接 12 / 348"],
  ["total", "累计流量", "接管以来总量", "累计 ↓18.4MB ↑2.1MB"],
  ["uptime", "运行时长", "已进行多久", "已运行 1h 12m"],
  ["custom", "自定义文本", "自己写一行", "↓{rate_down} · 丢包 {loss}%"],
];
const VARS = [
  ["{scene}", "场景名", "地铁 3G"], ["{rate_down}", "下行实时速率", "120KB/s"],
  ["{rate_up}", "上行实时速率", "8KB/s"], ["{loss}", "丢包率", "3"],
  ["{uptime}", "运行时长", "1h 12m"],
];
const state = {
  settings: { lines: ["scene", "limit", "rate"], custom: "", alpha: 0.92, text_sp: 10 },
  shown: false,
  canOverlay: true,
  posts: [],
};

const PROF = { name: "直连（不限制）", down_kbps: 0, up_kbps: 0, latency_down_ms: 0,
  latency_up_ms: 0, jitter_ms: 0, loss_pct: 0, stall_ms: 600, disconnect_pct: 0,
  timeout_pct: 0, dns_fail_pct: 0, reject_pct: 0, flap_sec: 0, flap_blackout_sec: 2 };

function startMock() {
  const srv = http.createServer((req, res) => {
    let body = "";
    req.on("data", c => body += c);
    req.on("end", () => {
      const p = req.url.split("?")[0];
      const send = o => {
        res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
        res.end(JSON.stringify(o));
      };
      if (p === "/api/status") return send({
        profile: PROF, preset_key: "direct", gate_open: true,
        listen: { host: "127.0.0.1", port: 8888 }, lan_ip: "127.0.0.1",
        stats: { total: 0, active: 0, up: 0, down: 0, uptime: 1 }, events: [],
        float: { supported: true, shown: state.shown, can_overlay: state.canOverlay },
      });
      if (p === "/api/presets") return send([]);
      if (p === "/api/upresets") return send({ items: [] });
      if (p === "/api/float" && req.method === "POST") {
        const d = JSON.parse(body || "{}");
        state.posts.push(d);
        state.settings = {
          lines: Array.isArray(d.lines) ? d.lines.filter(x => FIELDS.some(f => f[0] === x)) : [],
          custom: d.custom || "",
          alpha: Math.min(1, Math.max(0.3, +(d.alpha ?? 0.92))),
          text_sp: Math.min(18, Math.max(8, +(d.text_sp ?? 10))),
        };
        return send({ ok: true, settings: state.settings, shown: state.shown });
      }
      if (p === "/api/float") return send({
        supported: true, shown: state.shown, can_overlay: state.canOverlay,
        settings: state.settings,
        fields: FIELDS.map(f => ({ id: f[0], name: f[1], desc: f[2], sample: f[3] })),
        vars: VARS.map(v => ({ var: v[0], desc: v[1], sample: v[2] })),
      });
      if (p === "/api/float/show") {
        const want = JSON.parse(body || "{}").show;
        if (want && !state.canOverlay) return send({ ok: false, shown: false, can_overlay: false, msg: "未授予悬浮窗权限" });
        state.shown = !!want;
        return send({ ok: true, shown: state.shown, can_overlay: true, msg: state.shown ? "飘窗已显示" : "飘窗已收起" });
      }
      res.writeHead(404); res.end("not found");
    });
  });
  return new Promise(r => srv.listen(CTRL_PORT, "127.0.0.1", () => r(srv)));
}

async function main() {
  const html = fs.readFileSync(path.join(BASE, "dashboard.html"), "utf-8");
  const dom = new JSDOM(html, {
    url: ORIGIN + "/",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(win) {
      win.fetch = (u, o) => fetch(u.startsWith("/") ? ORIGIN + u : u, o);
      win.HTMLCanvasElement.prototype.getContext = () => ({
        setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {},
        lineTo() {}, stroke() {}, fillText() {}, fillStyle: "", strokeStyle: "",
        font: "", lineWidth: 1,
      });
    },
  });
  const win = dom.window, doc = win.document;
  const $ = id => doc.getElementById(id);
  const click = id => $(id).dispatchEvent(new win.Event("click"));
  const inp = (id, v) => { $(id).value = String(v); $(id).dispatchEvent(new win.Event("input")); };

  await sleep(2200);

  // ---- 1. 卡片存在且默认折叠 ----
  ck("设置卡片未被隐藏", $("fsetCard").style.display !== "none");
  ck("默认折叠", $("floatBody").style.display === "none");
  ck("折叠时箭头为 ▸", $("floatArrow").textContent === "▸");

  // ---- 2. 展开后加载设置 ----
  click("floatToggle");
  await sleep(600);
  ck("展开后正文可见", $("floatBody").style.display === "");
  ck("箭头变为 ▾", $("floatArrow").textContent === "▾");
  const rowNms = Array.from($("floatLines").querySelectorAll(".nm")).map(e => e.textContent);
  ck("默认三行已列出", rowNms.join(",") === "场景名,限速值,实时速率", rowNms.join(","));
  ck("简短说明显示 3 行", $("floatBrief").textContent === "3 行", $("floatBrief").textContent);
  ck("下拉只列未选项", $("floatAddSel").options.length === 5,
     "剩 " + $("floatAddSel").options.length + " 项");
  ck("未选自定义时隐藏文本框", $("floatCustomWrap").style.display === "none");
  ck("预览含标题场景名", /⚡/.test($("floatPreview").textContent), $("floatPreview").textContent);
  ck("预览含限速行", /限速/.test($("floatPreview").textContent));

  // ---- 3. 添加一行 + 上移 ----
  $("floatAddSel").value = "loss";
  click("floatAddBtn");
  await sleep(200);
  let nms = Array.from($("floatLines").querySelectorAll(".nm")).map(e => e.textContent);
  ck("添加后为 4 行", nms.length === 4 && nms[3] === "弱网参数", nms.join(","));
  ck("未应用时有标记", /未应用/.test($("floatBrief").textContent), $("floatBrief").textContent);
  // 把最后一行上移两次 → 排到第 2 位
  let ups = $("floatLines").querySelectorAll("button.up");
  ups[3].dispatchEvent(new win.Event("click"));
  await sleep(120);
  ups = $("floatLines").querySelectorAll("button.up");
  ups[2].dispatchEvent(new win.Event("click"));
  await sleep(120);
  nms = Array.from($("floatLines").querySelectorAll(".nm")).map(e => e.textContent);
  ck("上移生效（弱网参数排到第 2）", nms[1] === "弱网参数", nms.join(","));

  // ---- 4. 移除一行 ----
  $("floatLines").querySelectorAll("button.del")[0].dispatchEvent(new win.Event("click"));
  await sleep(150);
  nms = Array.from($("floatLines").querySelectorAll(".nm")).map(e => e.textContent);
  ck("移除后剩 3 行", nms.length === 3 && nms[0] === "弱网参数", nms.join(","));

  // ---- 5. 自定义文本 + 变量插入 ----
  $("floatAddSel").value = "custom";
  click("floatAddBtn");
  await sleep(150);
  ck("选自定义后显示文本框", $("floatCustomWrap").style.display === "");
  ck("变量标签已渲染", $("floatVars").querySelectorAll(".fl-var").length === VARS.length);
  $("floatCustom").value = "";
  const vScene = Array.from($("floatVars").querySelectorAll(".fl-var"))
      .find(e => e.dataset.v === "{scene}");
  vScene.dispatchEvent(new win.Event("click"));
  await sleep(120);
  ck("点变量插入到文本框", $("floatCustom").value === "{scene}", $("floatCustom").value);
  inp("floatCustom", "↓{rate_down} · {scene}");
  await sleep(120);
  ck("预览替换变量为示例值", /↓120KB\/s · 地铁 3G/.test($("floatPreview").textContent),
     $("floatPreview").textContent);

  // ---- 6. 字号 / 透明度 ----
  inp("floatSp", 14);
  inp("floatAlpha", 60);
  await sleep(120);
  ck("字号显示已更新", $("floatSpV").textContent === "14sp", $("floatSpV").textContent);
  ck("透明度显示已更新", $("floatAlphaV").textContent === "60%", $("floatAlphaV").textContent);

  // ---- 7. 应用 ----
  click("floatApplyBtn");
  await sleep(500);
  const last = state.posts[state.posts.length - 1];
  ck("应用真的 POST 出去", !!last, "posts=" + state.posts.length);
  // 初始 scene,limit,rate → 加 loss 上移到第2 → 删掉 scene → 再加 custom
  ck("POST 的 lines 与界面一致",
     last && last.lines.join(",") === "loss,limit,rate,custom",
     last && last.lines.join(","));
  ck("POST 带自定义文本", last && last.custom === "↓{rate_down} · {scene}", last && last.custom);
  ck("POST 带字号与透明度", last && last.text_sp === 14 && Math.abs(last.alpha - 0.6) < 1e-6,
     last && JSON.stringify({ sp: last.text_sp, a: last.alpha }));
  ck("应用后提示已更新", /已应用/.test($("floatTip").textContent), $("floatTip").textContent);
  ck("应用后不再标未应用", !/未应用/.test($("floatBrief").textContent), $("floatBrief").textContent);

  // ---- 8. 显示 / 收起飘窗 ----
  ck("按钮初始为「显示飘窗」", $("floatShowBtn").textContent === "显示飘窗", $("floatShowBtn").textContent);
  click("floatShowBtn");
  await sleep(500);
  ck("点后服务端已显示", state.shown === true);
  ck("按钮变为「收起飘窗」", $("floatShowBtn").textContent === "收起飘窗", $("floatShowBtn").textContent);
  click("floatShowBtn");
  await sleep(500);
  ck("再点已收起", state.shown === false);
  ck("提示显示收起文案", /收起/.test($("floatTip").textContent), $("floatTip").textContent);

  // ---- 9. 无悬浮窗权限时按钮禁用 ----
  state.canOverlay = false;
  await sleep(1400);                       // 等一轮 refresh 把状态同步回来
  ck("无权限时按钮禁用", $("floatShowBtn").disabled === true);
  state.canOverlay = true;
  await sleep(1400);
  ck("恢复权限后按钮可用", $("floatShowBtn").disabled === false);

  // ---- 10. 恢复默认 ----
  click("floatResetBtn");
  await sleep(600);
  const rs = state.settings;
  ck("恢复默认回到三行", rs.lines.join(",") === "scene,limit,rate", rs.lines.join(","));
  ck("默认透明度 0.92", Math.abs(rs.alpha - 0.92) < 1e-6, String(rs.alpha));
  nms = Array.from($("floatLines").querySelectorAll(".nm")).map(e => e.textContent);
  ck("界面同步回三行", nms.join(",") === "场景名,限速值,实时速率", nms.join(","));

  dom.window.close();
}

(async () => {
  const srv = await startMock();
  try {
    await main();
  } catch (e) {
    ck("测试执行无异常", false, String((e && e.stack) || e));
  } finally {
    srv.close();
  }
  console.log("=== 控制台页面（DOM）悬浮窗设置验证 ===");
  let ok = true;
  for (const [n, v, e] of checks) {
    console.log((v ? "  PASS  " : "  FAIL  ") + n + ((e && !v) ? "  <- " + e : ""));
    ok = ok && v;
  }
  console.log("总体:", ok ? "PASS" : "FAIL");
  process.exit(ok ? 0 : 1);
})();
