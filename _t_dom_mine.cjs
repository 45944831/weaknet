/**
 * 控制台页面（真实 DOM）验证自定义栏：
 *   保存 → 出现在「⭐ 我的自定义」→ 点击可应用 → 两步删除 → 列表回到空
 * 用 jsdom 跑页面里真实的脚本，接口打到本地起的引擎实例。
 */
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");
const { JSDOM } = require("jsdom");

const BASE = __dirname;
const PY = process.env.PY || "python";
const PROXY_PORT = 8980;
const CTRL_PORT = 8981;
const ORIGIN = `http://127.0.0.1:${CTRL_PORT}`;

const checks = [];
const ck = (name, cond, extra = "") => checks.push([name, !!cond, extra]);
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitServer() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(ORIGIN + "/api/status");
      if (r.ok) return true;
    } catch (e) { /* 还没起来 */ }
    await sleep(250);
  }
  return false;
}

async function main() {
  const html = fs.readFileSync(path.join(BASE, "dashboard.html"), "utf-8");

  const dom = new JSDOM(html, {
    url: ORIGIN + "/",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(win) {
      // 相对路径按控制台地址解析，脚本里的 fetch("/api/...") 才能落到引擎上
      win.fetch = (u, o) => fetch(u.startsWith("/") ? ORIGIN + u : u, o);
      // jsdom 没有 canvas 实现，给个空壳，免得 drawChart 抛异常打断 refresh
      win.HTMLCanvasElement.prototype.getContext = () => ({
        setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {},
        lineTo() {}, stroke() {}, fillText() {}, fillStyle: "", strokeStyle: "",
        font: "", lineWidth: 1,
      });
    },
  });
  const win = dom.window;
  const doc = win.document;
  const $ = id => doc.getElementById(id);

  await sleep(2500);        // 等 init + 一轮轮询

  // ---- 1. 初始状态 ----
  ck("自定义栏已渲染并可见", $("myBar") && $("myBar").style.display === "",
     $("myBar") ? $("myBar").style.display : "无此节点");
  ck("无场景时显示空提示", $("myEmpty").style.display !== "none" && $("myChips").children.length === 0);

  // ---- 2. 保存 ----
  const setSlider = (f, v) => {
    const el = $("sl_" + f);
    el.value = String(v);
    el.dispatchEvent(new win.Event("input"));
  };
  setSlider("down_kbps", 1800);
  setSlider("up_kbps", 600);
  setSlider("latency_down_ms", 120);
  setSlider("loss_pct", 4);
  // 不填名字 -> 应给出提示且不落库
  $("saveMyBtn").dispatchEvent(new win.Event("click"));
  await sleep(600);
  ck("未填名字时不保存", $("myChips").children.length === 0
     && /起个名字/.test($("mySaveTip").textContent), $("mySaveTip").textContent);

  $("myName").value = "地铁 3G 抖动";
  $("saveMyBtn").dispatchEvent(new win.Event("click"));
  await sleep(900);
  const chips = $("myChips").querySelectorAll("button.chip");
  ck("保存后出现在自定义栏", chips.length === 1, "数量=" + chips.length);
  ck("显示的是自定义名字", chips[0] && chips[0].textContent === "地铁 3G 抖动",
     chips[0] && chips[0].textContent);
  ck("空提示已隐藏", $("myEmpty").style.display === "none");
  ck("删除按钮存在", $("myChips").querySelectorAll("button.mc-del").length === 1);
  ck("输入框已清空", $("myName").value === "");

  // 第二条（同名 -> 自动加序号）
  $("myName").value = "地铁 3G 抖动";
  setSlider("down_kbps", 900);
  $("saveMyBtn").dispatchEvent(new win.Event("click"));
  await sleep(900);
  const names = Array.from($("myChips").querySelectorAll("button.chip")).map(b => b.textContent);
  ck("重名自动加序号", names.length === 2 && names[1] === "地铁 3G 抖动 (2)", names.join(" | "));

  // ---- 3. 点击应用 ----
  const target = Array.from($("myChips").querySelectorAll("button.chip"))[1];
  target.dispatchEvent(new win.Event("click"));
  await sleep(900);
  ck("应用后成为选中态", target.classList.contains("active"));
  ck("顶栏当前场景已切换", /地铁 3G 抖动 \(2\)/.test($("stats").innerHTML),
     $("stats").textContent.slice(0, 80));
  const st = await (await fetch(ORIGIN + "/api/status")).json();
  ck("引擎侧参数为保存值", st.profile.down_kbps === 900 && st.preset_key === target.dataset.key,
     JSON.stringify({ k: st.preset_key, d: st.profile.down_kbps }));

  // ---- 4. 两步删除 ----
  const del = $("myChips").querySelectorAll("button.mc-del")[0];
  del.dispatchEvent(new win.Event("click"));
  ck("第一次点击变「确认」", del.textContent === "确认", del.textContent);
  await sleep(300);
  ck("未点确认前不删除", $("myChips").querySelectorAll("button.chip").length === 2);
  del.dispatchEvent(new win.Event("click"));
  await sleep(900);
  ck("确认后已删除", $("myChips").querySelectorAll("button.chip").length === 1,
     "剩余=" + $("myChips").querySelectorAll("button.chip").length);
  const left = await (await fetch(ORIGIN + "/api/upresets")).json();
  ck("引擎侧只剩一条", left.items.length === 1, JSON.stringify(left.items.map(i => i.name)));

  // ---- 5. 引擎侧改动能被页面拉回（syncMy）----
  await fetch(ORIGIN + "/api/upreset", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "控制台外新增", profile: { down_kbps: 100 } }),
  });
  await sleep(3200);       // 轮询每 5 次 refresh 同步一次（约 5s）
  ck("外部新增会被同步显示",
     /控制台外新增/.test($("myChips").innerHTML), $("myChips").textContent);

  dom.window.close();

  // 清理测试场景
  const final = await (await fetch(ORIGIN + "/api/upresets")).json();
  for (const it of final.items) {
    await fetch(ORIGIN + "/api/upreset/del", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: it.id }),
    });
  }
}

(async () => {
  const eng = spawn(PY, [path.join(BASE, "weaknet.py"),
    "--proxy-port", String(PROXY_PORT), "--ctrl-port", String(CTRL_PORT)],
    { cwd: BASE, stdio: "ignore" });
  try {
    if (!(await waitServer())) throw new Error("引擎未起来");
    await main();
  } catch (e) {
    ck("测试执行无异常", false, String(e && e.message || e));
  } finally {
    eng.kill();
  }
  console.log("=== 控制台页面（DOM）自定义栏验证 ===");
  let ok = true;
  for (const [n, v, e] of checks) {
    console.log((v ? "  PASS  " : "  FAIL  ") + n + ((e && !v) ? "  <- " + e : ""));
    ok = ok && v;
  }
  console.log("总体:", ok ? "PASS" : "FAIL");
  process.exit(ok ? 0 : 1);
})();
