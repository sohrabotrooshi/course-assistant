#!/usr/bin/env node
// Dev harness: drive headless Chrome over the DevTools Protocol to verify
// the built HTML pages interactively and capture real screenshots for the
// README. Uses an isolated profile, so it never touches the user's Chrome.
//
// Usage (from repo root):
//   node scripts/shot.mjs quiz-initial                screenshots/quiz_initial.png
//   node scripts/shot.mjs quiz-answered               screenshots/quiz_answered.png
//   node scripts/shot.mjs chat-initial                screenshots/chat_initial.png
//   node scripts/shot.mjs chat-answer "a question"    screenshots/chat_answer.png
//
// Each mode prints the DOM assertions it checked before capturing.

import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
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
    await sleep(700); // let page JS (data injection, init) settle
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

async function quizAnswered(c) {
  await c.goto(QUIZ_URL);
  await c.eval(`localStorage.removeItem('mbax6418-quiz-v1')`);
  await c.goto(QUIZ_URL); // reload clean
  // switch to Week 5, answer first question correctly, check
  await c.eval(`
    (() => {
      [...document.querySelectorAll('.tab')].find(t => t.textContent === 'Week 5').click();
      const q = QUIZ.weeks['5'].questions[0];
      const card = document.querySelector('.q');
      card.querySelectorAll('input[type=radio]')[q.answer].click();
      card.querySelector('button.btn').click();
      // Check re-renders the card, so re-query after the click
      const fresh = document.querySelector('.q');
      return { verdict: fresh.querySelector('.verdict').textContent,
               src: fresh.querySelector('details.source summary').textContent,
               snippet: fresh.querySelector('.snippet').textContent.length };
    })()
  `);
  const st = await c.eval(`document.getElementById('sDone').textContent + '|' +
    document.getElementById('sRight').textContent + '|' +
    document.getElementById('sPct').textContent`);
  assert(/1\/30\|1\|100%/.test(st), "stats show 1/30 answered, 1 correct, 100% (" + st + ")");
  const v = await c.eval(`document.querySelector('.verdict').textContent`);
  assert(v.startsWith("✓ Correct"), "verdict says correct (" + v + ")");
  const src = await c.eval(`document.querySelector('details.source summary').textContent`);
  assert(/Week 5, slide \d+/.test(src) && /chunk w5-s\d+/.test(src) === false,
    "source cites the slide (" + src + ")");
  const cards = await c.eval(`document.querySelectorAll('.q').length`);
  assert(cards === 6, "Week 5 shows 6 questions (" + cards + ")");
  return c.shot(OUT("quiz_answered.png"));
}

async function quizInitial(c) {
  await c.goto(QUIZ_URL);
  const n = await c.eval(`document.querySelectorAll('.q').length`);
  assert(n === 6, "default week (2) shows its 6 questions (" + n + ")");
  const total = await c.eval(
    `Object.values(QUIZ.weeks).reduce((a, w) => a + w.questions.length, 0)`);
  assert(total === 30, "quiz data has 30 questions total (" + total + ")");
  return c.shot(OUT("quiz_initial.png"));
}

async function chatInitial(c) {
  await c.goto(CHAT_URL);
  const chips = await c.eval(`document.querySelectorAll('.msg').length`);
  assert(chips === 1, "welcome message rendered");
  return c.shot(OUT("chat_initial.png"));
}

async function chatAnswer(c, question) {
  await c.goto(CHAT_URL);
  await c.eval(`localStorage.setItem('mbax6418-chat-key', ${JSON.stringify(CLASS_KEY)})`);
  await c.goto(CHAT_URL); // reload so the status pill shows "ready"
  await c.eval(`
    (() => {
      const inp = document.getElementById('input');
      inp.value = ${JSON.stringify(question)};
      document.getElementById('send').click();
      return true;
    })()
  `);
  // poll for the real answer: a bot message that arrived with source chips
  // (the welcome message has none), or an error bubble
  let bot = null, err = null;
  for (let i = 0; i < 160; i++) {
    await sleep(800);
    bot = await c.eval(`
      (() => {
        const m = [...document.querySelectorAll('.msg.bot')]
          .find(x => x.querySelector('.srcs'));
        return m ? { text: m.childNodes[0] && m.childNodes[0].textContent
                       ? m.childNodes[0].textContent : m.textContent,
                    chips: m.querySelectorAll('.chip').length } : null;
      })()
    `);
    if (bot) break;
    err = await c.eval(`
      (() => { const e = document.querySelector('.msg.bot.err');
               return e ? e.textContent : null; })()`);
    if (err) break;
  }
  assert(!!bot, "bot answered with sources (not a timeout/error)");
  assert(!err, "no error bubble: " + (err || "none"));
  assert(bot && bot.chips > 0, "source chips rendered (" + (bot && bot.chips) + ")");
  const hasRefusal = bot && bot.text.includes("don't cover");
  console.log("ANSWER:\n" + (bot ? bot.text.slice(0, 900) : err || "timeout"));
  console.log("refusal said materials don't cover:", hasRefusal);
  // open one source box to prove the snippet panel
  if (bot) await c.eval(`document.querySelector('.chip').click()`);
  return c.shot(OUT("chat_answer.png"));
}

const mode = process.argv[2];
if (!mode) { console.error("mode required"); process.exit(1); }
const { proc, wsUrl } = await launch();
let code = 0;
try {
  const c = await CDP.connect(wsUrl);
  if (mode === "quiz-initial") await quizInitial(c);
  else if (mode === "quiz-answered") await quizAnswered(c);
  else if (mode === "chat-initial") await chatInitial(c);
  else if (mode === "chat-answer") {
    const q = process.argv[3] || "What is speculative decoding?";
    await chatAnswer(c, q);
  } else { console.error("unknown mode " + mode); code = 1; }
} catch (e) { console.error("harness error:", e.message); code = 1; }
finally { proc.kill(); }
process.exit(code);
