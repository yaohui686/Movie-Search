// 网页前端运行时冒烟：最小 DOM stub + mock 流式 fetch，跑通搜索→渲染→直链展开。
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync(process.argv[2], "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("no script found"); process.exit(1); }
const script = m[1];

const NDJSON = [
  JSON.stringify({ t: "n", m: "正在检索 15 个资源源…" }),
  JSON.stringify({ t: "p", d: 3, tot: 15, l: "暴风资源", c: 20, e: 1.2 }),
  JSON.stringify({ t: "r", rows: [{
    source: "暴风资源", title: "旋风战车队", year: "2015", type: "欧美动漫",
    area: "美国", quality: "高清", quality_raw: "HD", lines: 1, episodes: 20,
    url: "https://cdn.example.com/e01/index.m3u8", m3u8: true,
    play: [["第01集", "https://cdn.example.com/e01/index.m3u8"],
           ["第02集", "https://cdn.example.com/e02/index.m3u8"]],
    down: [], direct_down: false, sources: ["暴风资源"], score: 0.92, is_extra: false,
  }]}),
];
const enc = new TextEncoder();
const stream = new ReadableStream({
  start(c) {
    for (const line of NDJSON) c.enqueue(enc.encode(line + "\n"));
    c.close();
  },
});

function mkEl(props = {}) {
  const el = {
    innerHTML: "", textContent: "", value: props.value ?? "",
    style: props.styleProp ? { display: props.styleProp } : {},
    dataset: {}, disabled: false,
    classList: { contains: () => false, add() {}, remove() {} },
    addEventListener() {}, appendChild() {}, select() {}, remove() {}, click() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    setAttribute() {},
    ...props,
  };
  Object.defineProperty(el, "children", {
    get() { return { length: el.innerHTML ? 1 : 0 }; },
  });
  return el;
}
const els = {
  "#wd": mkEl({ value: "泰坦尼克号" }),
  "#go": mkEl(),
  "#status": mkEl(),
  "#out": mkEl(),
  "#toast": mkEl(),
};
const sandbox = {
  document: {
    querySelector: s => els[s] || (els[s] = mkEl(
      /^#lk\d+$/.test(s) ? { styleProp: "none" } : {})),   // #lkN 初始 display:none
    querySelectorAll: () => [],
    addEventListener(ev, fn) { sandbox._docHandler = fn; },
    body: mkEl(),
    createElement: tag => mkEl(),
    execCommand: () => true,
  },
  window: { open: () => {} },
  navigator: {},
  fetch: async () => ({ ok: true, body: stream }),
  Blob: class { constructor() {} },
  URL: { createObjectURL: () => "blob:x", revokeObjectURL: () => {} },
  setTimeout: (fn) => setTimeout(fn, 0), clearTimeout: () => {},
  setInterval: () => 0, clearInterval: () => {},
  TextDecoder, TextEncoder,
  btoa, atob, unescape, escape,
  console,
  _docHandler: null,
};
vm.createContext(sandbox);
vm.runInContext(script, sandbox);

(async () => {
  let fail = 0;
  const check = (name, cond) => {
    console.log((cond ? "PASS" : "FAIL") + "  " + name);
    if (!cond) fail++;
  };
  await sandbox.doSearch();
  if (els["#toast"].textContent) {
    console.log("页面捕获的错误:", els["#toast"].textContent);
  }
  const out = els["#out"].innerHTML;
  check("结果渲染含片名", out.includes("旋风战车队"));
  check("清单标签(可迅雷)", out.includes("清单 2 集 · 迅雷可下"));
  check("全季复制按钮", out.includes("全季复制给迅雷"));
  check("全季导出按钮", out.includes("全季导出 txt"));
  check("统计行", out.includes("共 1 条结果"));
  check("进度被结果覆盖", els["#status"].innerHTML === "");
  check("go 恢复可用", els["#go"].disabled === false);

  // 一键全季：不展开列表直接点卡片按钮
  els["#toast"].textContent = "";
  sandbox.copyRow(0);
  await new Promise(r => setTimeout(r, 20));   // 等 copyText 的微任务 toast
  check("全季复制 toast 引导", els["#toast"].textContent.includes("整批开下"));

  sandbox.exportRowTxt(0);
  check("全季导出不报错", true);

  const lk = els["#lk0"].innerHTML;
  check("直链列表默认展开(含每集清单)", lk.includes("第01集") && lk.includes("第02集"));
  check("清单行也有迅雷下载", (lk.match(/迅雷下载/g) || []).length >= 2);
  check("清单行有播放按钮", lk.includes("playUrl"));
  check("选集框有全部按钮", lk.includes("setAllEps"));
  check("无嵌套引号", !/copyText\(this\.dataset\.u,'/.test(lk));
  const th = (lk.match(/href="thunder:\/\/([^"]+)"/) || [])[1] || "";
  let thOK = false;
  try { thOK = Buffer.from(atob(th), "binary").length > 0
        && atob(th).startsWith("AAhttps://"); } catch { thOK = false; }
  check("thunder 链接可解码", thOK);

  sandbox.toggleLinks(0, "2");   // 选集筛选：只留第 2 集
  const lk2 = els["#lk0"].innerHTML;
  check("选集筛选只留第02集", lk2.includes("第02集") && !lk2.includes("第01集</span>"));
  sandbox.setAllEps(0);          // 一键恢复全部
  check("全部按钮恢复全集", els["#lk0"].innerHTML.includes("第01集</span>"));
  sandbox.toggleLinks(0);        // 再点应收起
  check("再点收起", els["#lk0"].style.display === "none");

  check("选集解析 1-3,7", JSON.stringify(sandbox.parseEps("1-3,7", 26)) === "[1,2,3,7]");
  check("选集解析全部", sandbox.parseEps("", 3).length === 3);
  const got = sandbox.safeName('a/b:c*"d', "2024", "第01集", "https://x.com/a.mp4");
  console.log("  safeName 实际输出:", JSON.stringify(got));
  check("文件名非法字符清理", got === "a_b_c__d(2024) 第01集.mp4");

  console.log(fail === 0 ? "ALL SMOKE PASS" : `SMOKE FAILED: ${fail}`);
  process.exit(fail === 0 ? 0 : 1);
})();
