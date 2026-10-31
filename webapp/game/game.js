/* «Протокол взлома»: the Telegram game of Night City Life (docs/WEBAPP.md W11). Plays in the page; the score goes to
 * /api/game/score with the signed token Telegram's Play button opened us with, and Telegram keeps the table. */
(() => {
  "use strict";
  const CODES = ["1C", "55", "7A", "BD", "E9", "FF"];
  const N = 5, BUF = 6, TIME_S = 90;
  const DAEMON_NAMES = ["Демон доступа", "Демон данных", "Демон ядра"];
  const token = (new URLSearchParams(location.hash.slice(1))).get("t") || "";
  const $ = (id) => document.getElementById(id);
  const el = { score: $("score"), round: $("round"), timer: $("timer"), time: $("time"), start: $("startScreen"), play: $("playScreen"),
    end: $("endScreen"), daemons: $("daemons"), buffer: $("buffer"), hint: $("hint"), grid: $("grid"), final: $("finalScore"),
    save: $("saveNote"), top: $("top") };
  let g = null, tick = null;

  const rnd = (n) => Math.floor(Math.random() * n);
  function n(tag, cls, text) { const x = document.createElement(tag); if (cls) x.className = cls; if (text != null) x.textContent = text; return x; }

  function newRound() {
    const grid = Array.from({ length: N }, () => Array.from({ length: N }, () => CODES[rnd(CODES.length)]));
    // a path that obeys the rules (row, column, row…) guarantees every daemon can be built
    const path = []; const usedCells = new Set(); let mode = "row", line = 0;
    for (let i = 0; i < BUF; i++) {
      const free = []; for (let k = 0; k < N; k++) { const cell = mode === "row" ? [line, k] : [k, line]; if (!usedCells.has(cell.join(":"))) free.push(cell); }
      if (!free.length) break;
      const c = free[rnd(free.length)]; usedCells.add(c.join(":")); path.push(grid[c[0]][c[1]]);
      line = mode === "row" ? c[1] : c[0]; mode = mode === "row" ? "col" : "row";
    }
    const pick = (len) => { const s = rnd(Math.max(1, path.length - len + 1)); return path.slice(s, s + len); };
    return { grid, daemons: [2, 3, 4].map((len, i) => ({ name: DAEMON_NAMES[i], seq: pick(len), ok: false })), buf: [], used: new Set(), mode: "row", line: 0 };
  }

  function active(r) { const out = []; for (let k = 0; k < N; k++) { const c = r.mode === "row" ? [r.line, k] : [k, r.line]; if (!r.used.has(c.join(":"))) out.push(c.join(":")); } return out; }
  const contains = (buf, seq) => { for (let i = 0; i + seq.length <= buf.length; i++) if (seq.every((x, j) => buf[i + j] === x)) return true; return false; };

  function draw() {
    const r = g.r;
    el.score.textContent = String(g.score); el.round.textContent = String(g.round);
    el.daemons.replaceChildren(...r.daemons.map((d) => {
      const row = n("div", "g-daemon" + (d.ok ? " ok" : ""));
      row.append(n("span", "", (d.ok ? "Взломан · " : "") + d.name));
      const seq = n("span", "g-seq"); d.seq.forEach((c) => seq.append(n("span", "g-code", c))); row.append(seq);
      return row;
    }));
    el.buffer.replaceChildren(...Array.from({ length: BUF }, (_, i) => n("span", "g-slot" + (r.buf[i] ? " full" : ""), r.buf[i] || "")));
    el.hint.textContent = "Бери код в " + (r.mode === "row" ? "строке " : "столбце ") + (r.line + 1) + ".";
    const act = new Set(active(r));
    el.grid.replaceChildren();
    for (let i = 0; i < N; i++) for (let k = 0; k < N; k++) {
      const key = i + ":" + k; const used = r.used.has(key);
      const b = n("button", "g-cell" + (used ? " used" : "") + (act.has(key) ? " active" : ""), used ? "··" : r.grid[i][k]);
      b.type = "button"; b.disabled = used || !act.has(key);
      b.setAttribute("aria-label", used ? "взят" : "Код " + r.grid[i][k] + ", строка " + (i + 1) + ", столбец " + (k + 1));
      b.addEventListener("click", () => take(i, k));
      el.grid.append(b);
    }
  }

  function take(i, k) {
    const r = g.r; if (!g.running) return;
    r.used.add(i + ":" + k); r.buf.push(r.grid[i][k]);
    r.line = r.mode === "row" ? k : i; r.mode = r.mode === "row" ? "col" : "row";
    let gained = 0;
    r.daemons.forEach((d) => { if (!d.ok && contains(r.buf, d.seq)) { d.ok = true; gained += d.seq.length * 100; } });
    if (gained) { g.score += gained; el.daemons.classList.remove("flash"); void el.daemons.offsetWidth; el.daemons.classList.add("flash"); }
    const all = r.daemons.every((d) => d.ok);
    if (all) { g.score += 500 + (BUF - r.buf.length) * 50; return nextRound(); }
    if (r.buf.length >= BUF || active(r).length === 0) return nextRound();
    draw();
  }

  function nextRound() { g.round += 1; g.r = newRound(); draw(); }

  function start() {
    g = { score: 0, round: 1, r: newRound(), running: true, ends: Date.now() + TIME_S * 1000 };
    el.start.hidden = true; el.end.hidden = true; el.play.hidden = false;
    draw();
    clearInterval(tick);
    tick = setInterval(() => {
      const left = Math.max(0, g.ends - Date.now());
      el.timer.style.width = (left / (TIME_S * 1000)) * 100 + "%";
      el.timer.classList.toggle("low", left < 15000);
      el.time.textContent = Math.ceil(left / 1000) + " с";
      if (left <= 0) finish();
    }, 250);
  }

  async function finish() {
    clearInterval(tick); g.running = false;
    el.play.hidden = true; el.end.hidden = false; el.final.textContent = String(g.score);
    el.save.textContent = token ? "Сохраняю рекорд…" : "Рекорд не сохранён: игра открыта не из Telegram.";
    if (!token) return;
    try {
      const r = await fetch("/api/game/score", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t: token, score: g.score }) });
      const d = await r.json();
      el.save.textContent = d.status === "ok" ? "Рекорд записан в таблицу под игрой." : d.status === "kept" ? "Твой прошлый рекорд выше, он остаётся." : "Не удалось сохранить рекорд.";
    } catch (e) { el.save.textContent = "Нет связи: рекорд не сохранён."; }
    loadTop();
  }

  async function loadTop() {
    try {
      const d = await (await fetch("/api/game/top?t=" + encodeURIComponent(token))).json();
      el.top.replaceChildren(...(d.top || []).map((x) => { const li = n("li", x.me ? "me" : ""); li.append(n("span", "", x.pos + ". " + x.name), n("b", "", String(x.score))); return li; }));
    } catch (e) { /* the table is optional */ }
  }

  $("startBtn").addEventListener("click", start);
  $("againBtn").addEventListener("click", start);
  if (!token) $("authNote").hidden = false;
  const share = $("shareBtn");
  if (window.TelegramGameProxy && window.TelegramGameProxy.shareScore) {
    share.hidden = false; share.addEventListener("click", () => { try { window.TelegramGameProxy.shareScore(); } catch (e) {} });
  }
})();
