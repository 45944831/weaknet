/**
 * 控制台页面（真实 DOM）验证「🔗 环境共享」：
 *   显示本机名字与地址 → 改名字真的 POST → 发现的设备可点选 →
 *   把自定义环境推给选中的设备 → 收到同名推送时三种处理（覆盖 / 保留 / 另存）
 *   另外验证 Android 端（没有 /api/me）会把整张卡片隐藏
 */
const path = require("path");
const fs = require("fs");
const http = require("http");
const { JSDOM } = require("jsdom");

const BASE = __dirname;
const CTRL_PORT = 8988;
const ORIGIN = `http://127.0.0.1:${CTRL_PORT}`;

const checks = [];
const ck = (name, cond, extra = "") => checks.push([name, !!cond, extra]);
const sleep = ms => new Promise(r => setTimeout(r, ms));

const PROF_A = { name: "地铁3G", down_kbps: 1500, up_kbps: 512, latency_down_ms: 120,
  latency_up_ms: 60, jitter_ms: 30, loss_pct: 3, stall_ms: 600, disconnect_pct: 0,
  timeout_pct: 0, dns_fail_pct: 0, reject_pct: 0, flap_sec: 0, flap_blackout_sec: 2 };
const PROF_B = { name: "电梯断连", down_kbps: 200, up_kbps: 100, latency_down_ms: 300,
  latency_up_ms: 300, jitter_ms: 80, loss_pct: 12, stall_ms: 800, disconnect_pct: 5,
  timeout_pct: 0, dns_fail_pct: 0, reject_pct: 0, flap_sec: 0, flap_blackout_sec: 2 };

const state = {
  name: "user",
  peers: [
    { id: "aaaaaaaaaaaa", name: "乙的电脑", ip: "192.168.1.30", platform: "windows",
      share: 8900, ago: 2 },
    { id: "bbbbbbbbbbbb", name: "丙的笔记本", ip: "192.168.1.44", platform: "windows",
      share: 8900, ago: 7 },
  ],
  inbox: [
    { id: "inbox1", from: "乙", name: "地铁3G", profile: PROF_B, ago: 3 },
  ],
  presets: [{ id: "p1", name: "地铁3G", profile: PROF_A },
            { id: "p2", name: "电梯断连", profile: PROF_B }],
  posts: [],
  resolves: [],
  shareSupported: true,       // 置 false 模拟 Android 端（没有 /api/me）
};

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
        profile: PROF_A, preset_key: "u:p1", gate_open: true,
        listen: { host: "127.0.0.1", port: 8888 }, lan_ip: "192.168.1.49",
        stats: { total: 0, active: 0, up: 0, down: 0, uptime: 1 }, events: [],
        scope: { mode: "all", list: [], supported: true },
      });
      if (p === "/api/presets") return send([]);
      if (p === "/api/targets") return send({ type: "process", unit: "程序", items: [] });
      if (p === "/api/upresets") return send({ items: state.presets });
      if (p === "/api/me") {
        if (!state.shareSupported) { res.writeHead(404); return res.end("not found"); }
        if (req.method === "POST") {
          const d = JSON.parse(body || "{}");
          if (d.name != null) state.name = String(d.name).trim().slice(0, 20) || state.name;
        }
        return send({ name: state.name, id: "self12345678", share: 8901,
                      discover: true, lan_ip: "192.168.1.49", platform: "windows" });
      }
      if (p === "/api/peers") return send({
        items: state.peers, discover: true,
        self: { name: state.name, id: "self12345678", share: 8901, lan_ip: "192.168.1.49" },
      });
      if (p === "/api/push") {
        const d = JSON.parse(body || "{}");
        state.posts.push(d);
        const nm = (d.ids || []).map(i => (state.presets.find(x => x.id === i) || {}).name);
        if (nm.includes("地铁3G"))       // 对方已有同名 → 进收件箱等确认
          return send({ ok: true, to: (d.peer || {}).name, accepted: [], pending: [{ id: "inbox9", name: "地铁3G" }] });
        return send({ ok: true, to: (d.peer || {}).name, accepted: nm, pending: [] });
      }
      if (p === "/api/inbox") return send({ items: state.inbox });
      if (p === "/api/inbox/resolve") {
        const d = JSON.parse(body || "{}");
        state.resolves.push(d);
        state.inbox = state.inbox.filter(x => x.id !== d.id);
        if (d.action === "rename") {
          state.presets.push({ id: "p9", name: d.name || "新名字", profile: PROF_B });
          return send({ ok: true, action: "rename", name: d.name });
        }
        return send({ ok: true, action: d.action, name: (state.presets[0] || {}).name });
      }
      res.writeHead(404); res.end("not found");
    });
  });
  return new Promise(r => srv.listen(CTRL_PORT, "127.0.0.1", () => r(srv)));
}

async function openPage() {
  const html = fs.readFileSync(path.join(BASE, "dashboard.html"), "utf-8");
  const dom = new JSDOM(html, {
    url: ORIGIN + "/",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(win) {
      win.fetch = (u, o) => fetch(u.startsWith("/") ? ORIGIN + u : u, o);
      win.alert = () => {};
      win.confirm = () => true;
      win.HTMLCanvasElement.prototype.getContext = () => ({
        setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {},
        lineTo() {}, stroke() {}, fillText() {}, fillStyle: "", strokeStyle: "",
        font: "", lineWidth: 1,
      });
    },
  });
  await sleep(2200);
  return dom;
}

async function main() {
  const dom = await openPage();
  const win = dom.window, doc = win.document;
  const $ = id => doc.getElementById(id);
  const click = id => $(id).dispatchEvent(new win.Event("click"));
  const rows = sel => Array.from($(sel).querySelectorAll(".shr-row"));

  // ---- 1. 卡片可见（Windows 端有 /api/me）----
  ck("共享卡片可见", $("shareCard").style.display !== "none");
  ck("本机名字已回填", $("shareName").value === "user", $("shareName").value);
  ck("显示本机地址与共享端口",
     /192\.168\.1\.49:8901/.test($("shareSelf").textContent), $("shareSelf").textContent);
  ck("发现开启时不报警", !/⚠/.test($("shareSelf").textContent), $("shareSelf").textContent);

  // ---- 2. 改名字 ----
  $("shareName").value = "  甲的工作机  ";
  click("shareNameBtn");
  await sleep(600);
  ck("改名真的发出去", state.name === "甲的工作机", state.name);
  ck("改名后提示已更新", /甲的工作机/.test($("shareTipText").textContent),
     $("shareTipText").textContent);
  $("shareName").value = "";
  click("shareNameBtn");
  await sleep(300);
  ck("空名字不给保存", state.name === "甲的工作机", state.name);
  ck("空名字有提示", /请先填个名字/.test($("shareTipText").textContent),
     $("shareTipText").textContent);

  // ---- 3. 设备列表与选中 ----
  ck("列出两台设备", rows("sharePeers").length === 2,
     "渲染 " + rows("sharePeers").length + " 行");
  ck("设备行带 IP 与端口", /192\.168\.1\.30:8900/.test($("sharePeers").textContent),
     $("sharePeers").textContent.slice(0, 80));
  ck("未选设备时提示先选", /先点选/.test($("shareTarget").textContent),
     $("shareTarget").textContent);
  rows("sharePeers")[0].dispatchEvent(new win.Event("click"));
  await sleep(200);
  ck("点选后显示目标", /乙的电脑/.test($("shareTarget").textContent),
     $("shareTarget").textContent);
  ck("选中行有高亮", rows("sharePeers")[0].className.includes("active"),
     rows("sharePeers")[0].className);

  // ---- 4. 我的自定义环境 ----
  ck("列出我的自定义环境", rows("shareMine").length === 2,
     "渲染 " + rows("shareMine").length + " 行");
  ck("环境行带参数摘要", /↓1\.5M/.test($("shareMine").textContent),
     $("shareMine").textContent.slice(0, 90));

  // ---- 5. 推送（同名 → 待对方确认）----
  rows("shareMine")[0].querySelector("button").dispatchEvent(new win.Event("click"));
  await sleep(700);
  let last = state.posts[state.posts.length - 1];
  ck("推送真的发出去", !!last, "posts=" + state.posts.length);
  ck("带上了选中的设备", last && last.peer && last.peer.name === "乙的电脑",
     last && JSON.stringify(last.peer));
  ck("带上了要推的环境 id", last && last.ids[0] === "p1", last && String(last.ids));
  ck("同名时提示等对方确认", /等对方决定/.test($("shareTipText").textContent),
     $("shareTipText").textContent);

  // ---- 6. 推送（不重名 → 直接接收）----
  rows("shareMine")[1].querySelector("button").dispatchEvent(new win.Event("click"));
  await sleep(700);
  ck("不重名时提示已推送", /已推送给 乙的电脑/.test($("shareTipText").textContent),
     $("shareTipText").textContent);

  // ---- 7. 收件箱：保留 ----
  ck("收件箱列出待处理项",
     $("shareInbox").querySelectorAll(".shr-in").length === 1,
     "渲染 " + $("shareInbox").querySelectorAll(".shr-in").length + " 条");
  ck("显示推送方与环境名", /乙/.test($("shareInbox").textContent)
     && /地铁3G/.test($("shareInbox").textContent));
  ck("另存默认名带来源",
     ($("shareInbox").querySelector("input") || {}).value === "地铁3G（来自 乙）",
     ($("shareInbox").querySelector("input") || {}).value);
  $("shareInbox").querySelector('button[data-act=keep]')
      .dispatchEvent(new win.Event("click"));
  await sleep(700);
  let rs = state.resolves[state.resolves.length - 1];
  ck("保留：action=keep", rs && rs.action === "keep", rs && rs.action);
  ck("保留后提示保留本地", /保留你本地/.test($("shareTipText").textContent),
     $("shareTipText").textContent);
  ck("保留后收件箱清空", $("shareInbox").querySelectorAll(".shr-in").length === 0);

  // ---- 8. 收件箱：另存为新名字 ----
  state.inbox = [{ id: "inbox2", from: "丙", name: "地铁3G", profile: PROF_B, ago: 1 }];
  click("shareReloadBtn");
  await sleep(700);
  ck("刷新后收到新的待处理项", $("shareInbox").querySelectorAll(".shr-in").length === 1);
  const inp = $("shareInbox").querySelector("input");
  inp.value = "丙的地铁3G";
  $("shareInbox").querySelector('button[data-act=rename]')
      .dispatchEvent(new win.Event("click"));
  await sleep(700);
  rs = state.resolves[state.resolves.length - 1];
  ck("另存：action=rename", rs && rs.action === "rename", rs && rs.action);
  ck("另存带上了新名字", rs && rs.name === "丙的地铁3G", rs && String(rs.name));
  ck("另存后自定义栏多一条", rows("shareMine").length === 3,
     "现在 " + rows("shareMine").length + " 条");

  // ---- 9. 收件箱：覆盖 ----
  state.inbox = [{ id: "inbox3", from: "丁", name: "电梯断连", profile: PROF_B, ago: 1 }];
  click("shareReloadBtn");
  await sleep(700);
  $("shareInbox").querySelector('button[data-act=overwrite]')
      .dispatchEvent(new win.Event("click"));
  await sleep(700);
  rs = state.resolves[state.resolves.length - 1];
  ck("覆盖：action=overwrite", rs && rs.action === "overwrite", rs && rs.action);
  ck("覆盖后提示已覆盖", /覆盖/.test($("shareTipText").textContent),
     $("shareTipText").textContent);

  // ---- 10. 目标设备下线后不再挂着它 ----
  state.peers = [];
  click("shareReloadBtn");
  await sleep(700);
  ck("设备下线后清空目标", /先点选/.test($("shareTarget").textContent),
     $("shareTarget").textContent);
  ck("空列表给出指引", /还没发现其他设备/.test($("sharePeers").textContent),
     $("sharePeers").textContent.slice(0, 40));

  dom.window.close();

  // ---- 11. Android 端（没有 /api/me）隐藏整张卡片 ----
  state.shareSupported = false;
  const dom2 = await openPage();
  ck("不支持时隐藏共享卡片",
     dom2.window.document.getElementById("shareCard").style.display === "none",
     "display=" + dom2.window.document.getElementById("shareCard").style.display);
  dom2.window.close();
  state.shareSupported = true;
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
  console.log("=== 控制台页面（DOM）环境共享验证 ===");
  let ok = true;
  for (const [n, v, e] of checks) {
    console.log((v ? "  PASS  " : "  FAIL  ") + n + ((e && !v) ? "  <- " + e : ""));
    ok = ok && v;
  }
  console.log("总体:", ok ? "PASS" : "FAIL");
  process.exit(ok ? 0 : 1);
})();
