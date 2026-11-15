// Capture REAL screenshots of the CancerFlow apps for the video (public/screens/*.png). No extra dependencies:
// drives Remotion's bundled headless Chrome over the DevTools protocol with Node's built-in WebSocket.
//
//   node scripts/capture_screens.mjs snap <name> [url]     # one screenshot of the current state (read-only)
//   node scripts/capture_screens.mjs story --yes-reset-story   # FULL RUN: resets Emma's story, steps the director
//                                                           # through all 17 steps, captures phone + clinician after each
//
// `story` WRITES to the shared demo database (exactly what the Operations → Story → Reset/run buttons do): it resets the
// story namespace, runs the optimiser at the outage and approves its plan via the director's presenter fallback. Run it
// only when nobody is rehearsing, and run `python reset_story.py` (or Story → Reset) afterwards.
//
// Env: CARE=http://localhost:5174  API=http://localhost:8000  (defaults)
import { spawn } from "node:child_process";
import { readdirSync, mkdirSync, writeFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = join(root, "public", "screens");
const CARE = process.env.CARE || "http://localhost:5174";
const API = process.env.API || "http://localhost:8000";
const PHONE = { w: 412, h: 892, scale: 2 };
const DESK = { w: 1600, h: 900, scale: 1.2 };
mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  const base = join(root, "node_modules", ".remotion", "chrome-headless-shell");
  const walk = (d) => { for (const e of readdirSync(d, { withFileTypes: true })) {
    const p = join(d, e.name);
    if (e.isDirectory()) { const r = walk(p); if (r) return r; } else if (e.name === "chrome-headless-shell") return p; } };
  const p = existsSync(base) && walk(base);
  if (!p) throw new Error("headless shell missing: run `npx remotion browser ensure`");
  return p;
}

async function launch() {
  const libs = join(root, ".local-libs", "usr", "lib", "x86_64-linux-gnu");
  const env = { ...process.env, LD_LIBRARY_PATH: [libs, process.env.LD_LIBRARY_PATH].filter(Boolean).join(":") };
  const fc = join(root, ".local-libs", "fonts.conf");          // adds a local emoji font (app uses 🕒 etc.)
  if (existsSync(fc)) env.FONTCONFIG_FILE = fc;
  const proc = spawn(findChrome(), ["--no-sandbox", "--hide-scrollbars", "--remote-debugging-port=0", "about:blank"], { env });
  const wsUrl = await new Promise((res, rej) => {
    let buf = "";
    proc.stderr.on("data", (d) => { buf += d; const m = buf.match(/DevTools listening on (ws:\/\/\S+)/); if (m) res(m[1]); });
    proc.on("exit", (c) => rej(new Error(`chrome exited ${c}: ${buf.slice(-400)}`)));
  });
  const port = new URL(wsUrl).port;
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  let id = 0; const pending = new Map();
  ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
  const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, (m) => m.error ? rej(new Error(m.error.message)) : res(m.result)); ws.send(JSON.stringify({ id: i, method, params })); });
  await send("Page.enable"); await send("Runtime.enable");
  return { send, close: () => { ws.close(); proc.kill(); } };
}

async function shot(b, name, url, vp = PHONE, settleMs = 2500) {
  await b.send("Emulation.setDeviceMetricsOverride", { width: vp.w, height: vp.h, deviceScaleFactor: vp.scale, mobile: vp === PHONE });
  await b.send("Page.navigate", { url });
  // wait for the app to render data (spinner gone) instead of a fixed virtual-time budget (the app keeps a websocket open)
  for (let i = 0; i < 40; i++) {
    await sleep(250);
    const r = await b.send("Runtime.evaluate", { expression: "document.readyState==='complete' && !document.querySelector('.spinner') && document.body.innerText.length>40", returnByValue: true });
    if (r.result.value) break;
  }
  await sleep(settleMs);
  const { data } = await b.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  writeFileSync(join(OUT, `${name}.png`), Buffer.from(data, "base64"));
  console.log(`  ✓ screens/${name}.png`);
}

const api = async (method, path) => {
  const r = await fetch(API + path, { method, headers: { "Content-Type": "application/json" } });
  if (!r.ok) throw new Error(`${method} ${path}: ${r.status} ${await r.text()}`);
  return r.json();
};

const phoneUrl = `${CARE}/?as=pat-emma#/`;

async function main() {
  const [mode, ...rest] = process.argv.slice(2);
  if (mode === "snap") {
    const [name, url = phoneUrl] = rest;
    if (!name) throw new Error("usage: snap <name> [url]");
    const b = await launch();
    try { await shot(b, name, url, url.includes("as=pat-") ? PHONE : DESK); } finally { b.close(); }
    return;
  }
  if (mode === "story") {
    if (!rest.includes("--yes-reset-story")) throw new Error("story mode resets the shared demo story; pass --yes-reset-story to confirm");
    const b = await launch();
    try {
      console.log("reset story …"); await api("POST", "/ops/demo/reset");
      await shot(b, "phone-00-start", phoneUrl);
      const ov = await api("GET", "/ops/story");
      for (const st of ov.steps) {
        console.log(`step ${st.key} …`);
        const r = await api("POST", `/ops/story/${st.key}`);
        console.log(`  ${r.message}`);
        await shot(b, `phone-${st.key}`, phoneUrl);
        const o2 = await api("GET", "/ops/story");
        if (o2.emma_pathway) await shot(b, `clinician-${st.key}`, `${CARE}/?as=nurse-pkks-scr#/p/${o2.emma_pathway}`, DESK);
      }
      console.log("done. Restore the demo with: python reset_story.py");
    } finally { b.close(); }
    return;
  }
  console.log("usage: node scripts/capture_screens.mjs snap <name> [url] | story --yes-reset-story");
}
main().catch((e) => { console.error(e.message); process.exit(1); });
