#!/usr/bin/env node
// Dev harness: drive headless Chrome over the DevTools Protocol to verify
// the built HTML pages interactively and capture real screenshots for the
// README. Uses an isolated profile, so it never touches the user's Chrome.
//
// Usage (from repo root):
//   node scripts/shot.mjs quiz-initial     screenshots/quiz_initial.png
//   node scripts/shot.mjs quiz-answered    screenshots/quiz_answered.png (+lightbox)
//   node scripts/shot.mjs quiz-generate    in-page quiz generation (live endpoint)
//   node scripts/shot.mjs chat-initial     screenshots/chat_initial.png
//   node scripts/shot.mjs chat-answer "Q"  live chat round-trip
//
// Each mode prints the DOM assertions it checked before capturing.

import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333;
const PROFILE = `/tmp/mbax-shot-profile-${Date.now()}`;
const OUT = (name) => resolve(ROOT, "screenshots", name);
const QUIZ_URL = "file://" + resolve(ROOT, "quiz_practice.html");
const CHAT_URL = "file://" + resolve(ROOT, "course_chat.html");
const CLASS_KEY = process.env.LLM_API_KEY || "6418"; // class-provided key

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
mkdirSync(resolve(ROOT, "screenshots"), { recursive: true });

async function launch() {
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${PROFILE}`,
    "--window-size=1280,1700", "about:blank",
  ], { stdio: "ignore" });
  for (let i = 0; i < 40; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const targets = await r.json();
      const page = targets.find((t) => t.type === "page");
      if (page) return { proc, wsUrl: page.webSocketDebuggerUrl };
    } catch { await sleep(250); }
  }
  throw new Error("chrome did not come up");
}

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    const c = new CDP(ws);
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && c.pending.has(m.id)) {
        const { resolve, reject } = c.pending.get(m.id);
        c.pending.delete(m.id);
        m.error ? reject(new Error(m.error.message)) : resolve(m.result);
      }
    };
    return c;
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async eval(expr) {
    const r = await this.send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error("eval failed: " + JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }
  async goto(url) {
    await this.send("Page.enable");
    await this.send("Runtime.enable");
    await this.send("Page.navigate", { url });
    for (let i = 0; i < 100; i++) {
      const ok = await this.eval("document.readyState === 'complete'");
      if (ok) break;
      await sleep(150);
    }
    await sleep(700);
  }
  async shot(file) {
    const r = await this.send("Page.captureScreenshot",
      { format: "png", captureBeyondViewport: true });
    writeFileSync(file, Buffer.from(r.data, "base64"));
    return file;
  }
}

const assert = (cond, label) => {
  console.log((cond ? "PASS " : "FAIL ") + label);
  if (!cond) process.exitCode = 1;
};

async function quizInit(c) {
  await c.goto(QUIZ_URL);
  const cards = await c.eval(`document.querySelectorAll('.q').length`);
  assert(cards === 48, "48 question cards render (" + cards + ")");
  const docs = await c.eval(`document.querySelectorAll('.docpill').length`);
  assert(docs === 8, "8 document groups render (" + docs + ")");
  const tabs = await c.eval(`[...document.querySelectorAll('.tab')].map(t=>t.textContent)`);
  assert(tabs[0] === "Course bank" && tabs.includes("+ New quiz"),
    "bank + new-quiz tabs (" + tabs.join("|") + ")");
  return c.shot(OUT("quiz_initial.png"));
}

async function quizAnswered(c) {
  await c.goto(QUIZ_URL);
  await c.eval(`localStorage.removeItem('mbax6418-quiz-v1')`);
  await c.goto(QUIZ_URL);
  await c.eval(`
    (() => {
      const q = Object.values(QUIZ.docs)[0].questions[0];
      const card = document.querySelector('.q');
      card.querySelectorAll('input[type=radio]')[q.answer].click();
      card.querySelector('button.check').click();
      return true;
    })()
  `);
  const info = await c.eval(`
    (() => {
      let pos = -1, idx = 0;
      for (const docId in QUIZ.docs)
        for (const q of QUIZ.docs[docId].questions) {
          const src = SOURCES[q.source_chunk_id] || {};
          if (idx > 0 && src.images && src.images.length) { pos = idx; break; }
          idx++;
        }
      if (pos >= 0) {
        const card = document.querySelectorAll('.q')[pos];
        const b = [...card.querySelectorAll('button')]
          .find(x => x.textContent === 'Reveal solution');
        if (b) b.click();
      }
      return pos;
    })()
  `);
  assert(info >= 0, "found a source-with-images question to reveal (pos " + info + ")");
  const st = await c.eval(`document.getElementById('sDone').textContent + '|' +
    document.getElementById('sRight').textContent + '|' +
    document.getElementById('sSkipped').textContent + '|' +
    document.getElementById('sPct').textContent`);
  assert(/2\/48\|1\|1\|100%/.test(st), "stats 2/48 answered, 1 right, 1 skipped, 100% (" + st + ")");
  const verdicts = await c.eval(`[...document.querySelectorAll('.verdict')].map(v=>v.textContent)`);
  assert(verdicts[0].startsWith("\u2713 Correct"), "Q1 verdict correct (" + verdicts[0] + ")");
  assert(verdicts[1].startsWith("Skipped"), "revealed question shows skipped (" + verdicts[1] + ")");
  const imgs = await c.eval(`
    (() => { const el = [...document.querySelectorAll('.evidence img')];
      return { count: el.length, loaded: el.filter(i => i.naturalWidth > 0).length }; })()`);
  assert(imgs.count >= 1 && imgs.loaded === imgs.count,
    "evidence images render (" + imgs.count + " found, " + imgs.loaded + " loaded)");
  const snippet = await c.eval(`document.querySelector('.snippet').textContent.length`);
  assert(snippet > 0, "source excerpt shown (" + snippet + " chars)");
  await c.eval(`document.querySelector('.evidence img').click()`);
  const lbOpen = await c.eval(`document.getElementById('lb').classList.contains('open')`);
  assert(lbOpen, "lightbox opens on image click");
  await c.shot(OUT("quiz_lightbox.png"));
  await c.eval(`document.getElementById('lb').click()`);
  return c.shot(OUT("quiz_answered.png"));
}

async function quizGenerate(c) {
  await c.goto(QUIZ_URL);
  await c.eval(`localStorage.setItem('mbax6418-chat-key', ${JSON.stringify(CLASS_KEY)})`);
  await c.goto(QUIZ_URL);
  await c.eval(`
    (() => {
      document.querySelector('.tab.newtab').click();
      [...document.querySelectorAll('#docPick input')].forEach(b => {
        if (b.value !== 'week05') b.checked = false;
      });
      const r = document.getElementById('countIn');
      r.value = 4; r.dispatchEvent(new Event('input'));
      document.getElementById('genGo').click();
      return true;
    })()
  `);
  // poll: the generated tab is named "Quiz <time> (4q)" and only appears when
  // the endpoint has finished all questions (4 live LLM calls + retries)
  let done = false;
  for (let i = 0; i < 500 && !done; i++) {
    await sleep(1000);
    const poll = await c.eval(`
      (() => {
        const tabs = [...document.querySelectorAll('.tab')].map(t => t.textContent);
        return { ok: tabs.some(t => t.indexOf("Quiz ") === 0 && t.endsWith("(4q)")),
                 tabs: tabs };
      })()
    `);
    if (i < 3 || i % 90 === 0) console.log("poll", i, JSON.stringify(poll).slice(0, 160));
    done = poll.ok;
  }
  assert(done, "generated quiz tab appeared (4 questions)");
  const genCards = await c.eval(`document.querySelectorAll('.q').length`);
  assert(genCards === 4, "generated quiz shows 4 questions (" + genCards + ")");
  const stored = await c.eval(`
    (() => {
      const g = JSON.parse(localStorage.getItem('mbax6418-quizgen-v1') || '{}');
      const id = Object.keys(g)[0];
      const n = Object.values(g[id].docs).reduce((a, d) => a + d.questions.length, 0);
      const docIds = Object.keys(g[id].docs).join();
      return { n, docIds };
    })()`);
  assert(stored.n === 4 && stored.docIds === "week05",
    "quiz persisted with fixed key (" + JSON.stringify(stored) + ")");
  return c.shot(OUT("quiz_generated.png"));
}

async function chatInitial(c) {
  await c.goto(CHAT_URL);
  const chips = await c.eval(`document.querySelectorAll('.docchip').length`);
  assert(chips === 8, "8 material chips render (" + chips + ")");
  const msg = await c.eval(`document.querySelectorAll('.msg').length`);
  assert(msg === 1, "welcome message rendered");
  return c.shot(OUT("chat_initial.png"));
}

async function chatAnswer(c, question) {
  await c.goto(CHAT_URL);
  await c.eval(`localStorage.setItem('mbax6418-chat-key', ${JSON.stringify(CLASS_KEY)})`);
  await c.goto(CHAT_URL);
  await c.eval(`
    (() => {
      const inp = document.getElementById('input');
      inp.value = ${JSON.stringify(question)};
      document.getElementById('send').click();
      return true;
    })()
  `);
  let bot = null, err = null;
  for (let i = 0; i < 160; i++) {
    await sleep(800);
    bot = await c.eval(`
      (() => {
        const m = [...document.querySelectorAll('.msg.bot')].find(x => x.querySelector('.srcs'));
        return m ? { text: m.childNodes[0] && m.childNodes[0].textContent
                       ? m.childNodes[0].textContent : m.textContent,
                     chips: [...m.querySelectorAll('.chip')].map(x => x.textContent),
                     imgs: m.querySelectorAll('.srev img').length } : null;
      })()
    `);
    if (bot) break;
    err = await c.eval(`(() => { const e = document.querySelector('.msg.bot.err');
      return e ? e.textContent : null; })()`);
    if (err) break;
  }
  assert(!!bot, "bot answered with sources");
  assert(!err, "no error bubble (" + (err || "none") + ")");
  assert(bot && bot.chips.length > 0, "source chips rendered ("
    + (bot ? bot.chips.join(",") : "?") + ")");
  console.log("ANSWER:\n" + (bot ? bot.text.slice(0, 700) : err || "timeout"));
  const imgCheck = await c.eval(`
    (() => {
      document.querySelectorAll('.chip').forEach(ch => ch.click());
      const imgs = [...document.querySelectorAll('.srev img')];
      return { count: imgs.length, loaded: imgs.filter(i => i.naturalWidth > 0).length };
    })()
  `);
  assert(imgCheck.loaded === imgCheck.count,
    "evidence images load (" + imgCheck.loaded + "/" + imgCheck.count + ")");
  return c.shot(OUT("chat_answer.png"));
}

const mode = process.argv[2];
if (!mode) { console.error("mode required"); process.exit(1); }
const { proc, wsUrl } = await launch();
let code = 0;
try {
  const c = await CDP.connect(wsUrl);
  if (mode === "quiz-initial") await quizInit(c);
  else if (mode === "quiz-answered") await quizAnswered(c);
  else if (mode === "quiz-generate") await quizGenerate(c);
  else if (mode === "chat-initial") await chatInitial(c);
  else if (mode === "chat-answer") await chatAnswer(c, process.argv[3] || "What is RAG and why would you use it?");
  else { console.error("unknown mode " + mode); code = 1; }
} catch (e) { console.error("harness error:", e.message); code = 1; }
finally { proc.kill(); }
process.exit(code);
