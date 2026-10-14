/**
 * 控制台页面（真实 DOM）验证「接管范围改为二级界面」：
 *   主页面只剩一个按钮 → 点开二级界面列全部应用 → 勾选 / 全选 / 清空 / 搜索 →
 *   保存后返回主页面且摘要更新 → 返回按钮（顶部 + 底部 + Esc）→ 未保存改动会先确认
 *
 * Windows 与 Android 两端接口同构（/api/targets + /api/scope），
 * 这里起一个 mock 引擎，按 Windows 端 weaknet.py 的返回结构应答。
 */
const path = require("path");
const fs = require("fs");
const http = require("http");
const { JSDOM } = require("jsdom");

const BASE = __dirname;
const CTRL_PORT = 8987;
const ORIGIN = `http://127.0.0.1:${CTRL_PORT}`;

const checks = [];
const ck = (name, cond, extra = "") => checks.push([name, !!cond, extra]);
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---- mock 引擎 ----
const APPS = [
  ["chrome.exe", "Google Chrome", "浏览器"],
  ["firefox.exe", "Mozilla Firefox", "浏览器"],
  ["msedge.exe", "Microsoft Edge", "浏览器"],
  ["curl.exe", "curl", "命令行工具"],
  ["python.exe", "Python", "解释器"],
  ["code.exe", "Visual Studio Code", "编辑器"],
  ["wechat.exe", "微信", "即时通讯"],
  ["qq.exe", "QQ", "即时通讯"],
  ["spotify.exe", "Spotify", "音乐"],
  ["steam.exe", "Steam", "游戏平台"],
];
const state = { mode: "all", list: [], posts: [], targets: 0 };

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
        scope: { mode: state.mode, list: state.list.slice(), supported: true },
      });
      if (p === "/api/presets") return send([]);
      if (p === "/api/upresets") return send({ items: [] });
      if (p === "/api/targets") {
        state.targets++;
        return send({
          type: "process", unit: "程序",
          items: APPS.map(a => ({ id: a[0], name: a[1], desc: a[2] })),
        });
      }
      if (p === "/api/scope") {
        if (req.method === "POST") {
          const d = JSON.parse(body || "{}");
          state.posts.push(d);
          state.mode = d.mode || "all";
          state.list = Array.isArray(d.list) ? d.list.slice() : [];
        }
        return send({ mode: state.mode, list: state.list.slice(), supported: true });
      }
      res.writeHead(404); res.end("not found");
    });
  });
  return new Promise(r => srv.listen(CTRL_PORT, "127.0.0.1", () => r(srv)));
}

async function main() {
  const html = fs.readFileSync(path.join(BASE, "dashboard.html"), "utf-8");
  let confirmRet = true;          // 控制「有未保存改动，确定返回吗？」
  const dom = new JSDOM(html, {
    url: ORIGIN + "/",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(win) {
      win.fetch = (u, o) => fetch(u.startsWith("/") ? ORIGIN + u : u, o);
      win.confirm = () => confirmRet;
      win.alert = () => {};
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
  const rows = () => Array.from($("scopeList").querySelectorAll(".scope-row"));
  const checkedIds = () => rows().filter(r => r.querySelector("input").checked)
                                .map(r => r.querySelector("input").value);
  const toggleId = id => {
    const cb = rows().find(r => r.querySelector("input").value === id).querySelector("input");
    cb.checked = !cb.checked;
    cb.dispatchEvent(new win.Event("change"));
  };

  await sleep(2200);

  // ---- 1. 主页面：只剩一个入口按钮，列表不在主页面里 ----
  ck("接管范围卡片可见", $("scopeCard").style.display !== "none");
  ck("二级界面默认隐藏", $("scopePage").style.display === "none");
  ck("入口是按钮", $("scopeOpenBtn").tagName === "BUTTON", $("scopeOpenBtn").tagName);
  ck("入口文案为「选择接管的应用」", $("scopeOpenBtn").textContent === "选择接管的应用",
     $("scopeOpenBtn").textContent);
  ck("主页面摘要显示全部", $("scopeBrief").textContent === "全部程序", $("scopeBrief").textContent);
  ck("初始化只探测一次（拿称谓用）", state.targets === 1, "targets=" + state.targets);

  // ---- 2. 点按钮打开二级界面 ----
  click("scopeOpenBtn");
  await sleep(700);
  ck("二级界面已打开", $("scopePage").style.display === "");
  ck("打开时拉一次最新列表", state.targets === 2, "targets=" + state.targets);
  ck("列出全部应用", rows().length === APPS.length, "渲染 " + rows().length + " 行");
  ck("计数显示总数", $("scopeListHint").textContent === "共 " + APPS.length + " 项",
     $("scopeListHint").textContent);
  ck("已选计数为 0", $("scopeCount").textContent === "已选 0 项", $("scopeCount").textContent);
  ck("顶部有返回按钮", $("scopeBackBtn").textContent === "← 返回", $("scopeBackBtn").textContent);
  ck("底部也有返回按钮", $("scopeBackBtn2").textContent === "返回", $("scopeBackBtn2").textContent);
  ck("底部有保存按钮", $("scopeApplyBtn").textContent === "保存", $("scopeApplyBtn").textContent);

  // ---- 3. 默认「全部接管」时列表置灰（勾了也不生效，免得误解）----
  ck("默认模式为全部接管",
     doc.querySelector('input[name=scopeMode]:checked').value === "all");
  ck("全部接管时列表置灰", $("scopeList").style.pointerEvents === "none",
     $("scopeList").style.pointerEvents);

  // ---- 4. 切到「仅接管选定项」并勾选 ----
  const only = doc.querySelector('input[name=scopeMode][value=only]');
  only.checked = true; only.dispatchEvent(new win.Event("change"));
  await sleep(150);
  ck("切换后列表可用", $("scopeList").style.pointerEvents !== "none");
  toggleId("chrome.exe");
  toggleId("curl.exe");
  await sleep(150);
  ck("勾选两项", checkedIds().sort().join(",") === "chrome.exe,curl.exe", checkedIds().join(","));
  ck("计数显示已选 2 项", /已选 2 项/.test($("scopeCount").textContent), $("scopeCount").textContent);
  ck("计数标出未保存", /未保存/.test($("scopeCount").textContent), $("scopeCount").textContent);

  // ---- 5. 搜索过滤 + 全选只作用于筛出来的项 ----
  inp("scopeSearch", "浏览器");
  await sleep(200);
  ck("按描述也能搜到", rows().length === 3, "剩 " + rows().length + " 行");
  ck("计数显示筛选比例",
     $("scopeListHint").textContent === "筛出 3 / 共 " + APPS.length + " 项",
     $("scopeListHint").textContent);
  click("scopeAllBtn");
  await sleep(150);
  ck("全选只勾筛出来的 3 项", checkedIds().length === 3, checkedIds().join(","));
  ck("全选不会把列表外的也勾上",
     !checkedIds().includes("steam.exe"), checkedIds().join(","));
  click("scopeNoneBtn");
  await sleep(150);
  ck("清空后为 0 项", checkedIds().length === 0);
  inp("scopeSearch", "");
  await sleep(200);

  // ---- 6. 未保存时点返回：先确认，取消则留在页面 ----
  toggleId("python.exe");
  await sleep(120);
  confirmRet = false;
  click("scopeBackBtn");
  await sleep(250);
  ck("取消确认时留在二级界面", $("scopePage").style.display === "");
  confirmRet = true;
  click("scopeBackBtn");
  await sleep(250);
  ck("确认后返回主页面", $("scopePage").style.display === "none");
  ck("放弃改动后摘要仍是全部", $("scopeBrief").textContent === "全部程序",
     $("scopeBrief").textContent);

  // ---- 7. 重新打开：改动已回滚，模式回到服务端值 ----
  click("scopeOpenBtn");
  await sleep(400);
  ck("重新打开后勾选项已回滚", checkedIds().length === 0, checkedIds().join(","));
  ck("重新打开后模式回到全部",
     doc.querySelector('input[name=scopeMode]:checked').value === "all");
  ck("第二次打开不重复拉列表（已缓存）", state.targets === 2, "targets=" + state.targets);

  // ---- 8. 勾选并保存 ----
  const r = doc.querySelector('input[name=scopeMode][value=only]');
  r.checked = true; r.dispatchEvent(new win.Event("change"));
  await sleep(120);
  toggleId("chrome.exe");
  toggleId("firefox.exe");
  await sleep(120);
  click("scopeApplyBtn");
  await sleep(700);
  const last = state.posts[state.posts.length - 1];
  ck("保存真的 POST 出去", !!last, "posts=" + state.posts.length);
  ck("POST 的 mode 正确", last && last.mode === "only", last && last.mode);
  ck("POST 的 list 与勾选一致",
     last && last.list.slice().sort().join(",") === "chrome.exe,firefox.exe",
     last && last.list.join(","));
  ck("保存后自动返回主页面", $("scopePage").style.display === "none");
  ck("主页面摘要已更新", $("scopeBrief").textContent === "仅接管 2 项",
     $("scopeBrief").textContent);

  // ---- 9. 再打开：勾选状态从服务端读回 ----
  click("scopeOpenBtn");
  await sleep(500);
  ck("重开后勾选状态与已保存一致",
     checkedIds().sort().join(",") === "chrome.exe,firefox.exe", checkedIds().join(","));
  ck("重开后不显示未保存标记", !/未保存/.test($("scopeCount").textContent),
     $("scopeCount").textContent);

  // ---- 10. 已选但当前不在候选里的目标也要列出来 ----
  state.list = ["chrome.exe", "ghost.exe"];      // ghost.exe 不在候选里
  click("scopeReloadBtn");
  await sleep(600);
  ck("刷新后候选变 11 项（含已选的 ghost）", rows().length === APPS.length + 1,
     "渲染 " + rows().length + " 行");
  ck("已选的幽灵项被列出并勾选",
     checkedIds().includes("ghost.exe"), checkedIds().join(","));
  ck("幽灵项标注未运行",
     /未运行/.test($("scopeList").textContent), "无未运行标注");

  // ---- 11. 底部返回按钮与 Esc ----
  click("scopeBackBtn2");
  await sleep(200);
  ck("底部返回按钮可关闭", $("scopePage").style.display === "none");
  click("scopeOpenBtn");
  await sleep(400);
  $("scopePage").dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape" }));
  await sleep(200);
  ck("Esc 可关闭二级界面", $("scopePage").style.display === "none");

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
  console.log("=== 控制台页面（DOM）接管范围二级界面验证 ===");
  let ok = true;
  for (const [n, v, e] of checks) {
    console.log((v ? "  PASS  " : "  FAIL  ") + n + ((e && !v) ? "  <- " + e : ""));
    ok = ok && v;
  }
  console.log("总体:", ok ? "PASS" : "FAIL");
  process.exit(ok ? 0 : 1);
})();
