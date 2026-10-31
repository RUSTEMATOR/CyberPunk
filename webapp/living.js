/* Night City Life: the "living city" screens of the Mini App (docs/WEBAPP.md W6-W10, contract docs/PLAN_LIVING.md).
 * Views draw what the bot sent; every press is a button of the current screen sent through app.js (same op queue).
 * Sheets (body, last fight) only read. Nothing here computes a game number. */
(() => {
  "use strict";
  const tg = window.Telegram && window.Telegram.WebApp;
  const INIT = tg ? tg.initData : "";

  function n(tag, cls, text) { const x = document.createElement(tag); if (cls) x.className = cls; if (text != null) x.textContent = text; return x; }
  function meter(pct, cls) { const m = n("div", "lv-meter " + (cls || "")); const f = n("div", "lv-meter-fill"); f.style.width = Math.max(0, Math.min(100, pct)) + "%"; m.append(f); return m; }
  const btnOf = (s, action, arg) => { for (const r of s.rows) for (const b of r) if (b.action === action && String(b.arg || "") === String(arg || "")) return b; return null; };
  const restRows = (s, skip) => s.rows.map((r) => r.filter((b) => !skip(b))).filter((r) => r.length);

  // ---------- W6: the city map ----------
  // Schematic, not to scale: north at the top, the Badlands on the east edge. Unknown districts go in a list below.
  const LAYOUT = {
    northside: [0, 0], watson: [1, 0], little_europe: [2, 0], north_oak: [3, 0],
    waterfront: [0, 1], hot_zone: [1, 1], city_center: [2, 1], westbrook: [3, 1],
    combat_zone: [0, 2], university: [1, 2], heywood: [2, 2], santo_domingo: [3, 2],
    pacifica: [0, 3], dogtown: [1, 3], the_glen: [2, 3], badlands: [3, 3],
  };
  function districtTile(d, b, ctx) {
    const here = b && /📍/.test(b.label);
    const kids = [];
    const head = n("span", "lv-d-name", d.name);
    kids.push(head);
    const bars = n("span", "lv-d-bars");
    const g = n("span", "lv-d-bar"); g.append(n("span", "lv-d-k", "Б " + d.gang), meter(d.gang, "gang"));
    const c = n("span", "lv-d-bar"); c.append(n("span", "lv-d-k", "К " + d.corp), meter(d.corp, "corp"));
    bars.append(g, c);
    kids.push(bars);
    kids.push(n("span", "lv-d-word", d.word || ""));
    if (d.holder && d.holder.name) kids.push(n("span", "lv-d-holder", "держит " + d.holder.name));
    if (d.crews && d.crews.length) kids.push(n("span", "lv-d-crews", d.crews.map((x) => "«" + x.name + "»").join(", ")));
    if (d.deeds && d.deeds.length) kids.push(n("span", "lv-d-deed", d.deeds[d.deeds.length - 1]));
    if (!b) { const x = n("div", "lv-district off"); x.append(...kids); return x; }
    const x = ctx.press(b, { cls: "lv-district" + (here ? " here" : ""), content: kids });
    x.setAttribute("aria-label", d.name + (here ? ", ты здесь" : "") + ": банды " + d.gang + ", корпорации " + d.corp + ". Поехать");
    if (here) x.prepend(n("span", "lv-here", "ты здесь"));
    return x;
  }
  function cityMap(ctx) {
    const st = ctx.s.state && ctx.s.state.map;
    if (!st || !st.districts) return false;
    const { el } = ctx;
    el.title.hidden = false; el.title.replaceChildren(document.createTextNode("Карта Найт-Сити"));
    const wrap = n("div", "lv-map");
    wrap.append(n("p", "lv-note", "Хватка банд (Б) и корпораций (К) от 0 до 100. Нажми на район, чтобы поехать туда."));
    const grid = n("div", "lv-map-grid");
    const extra = [];
    for (const d of st.districts) {
      const b = btnOf(ctx.s, "go", d.key);
      const tile = districtTile(d, b, ctx);
      const pos = LAYOUT[d.key];
      if (pos) { tile.style.gridColumn = String(pos[0] + 1); tile.style.gridRow = String(pos[1] + 1); grid.append(tile); }
      else extra.push(tile);
    }
    wrap.append(grid);
    if (extra.length) { const more = n("div", "lv-map-more"); more.append(...extra); wrap.append(more); }
    if (st.corps && st.corps.length) {
      const box = n("section", "lv-panel");
      box.append(n("h3", "lv-h", "Мегакорпорации"));
      for (const c of [...st.corps].sort((a, b) => a.hp - b.hp)) {
        const r = n("div", "lv-corp" + (c.down ? " down" : ""));
        r.append(n("span", "lv-corp-name", c.name), n("span", "lv-corp-hp", c.down ? "лежит" : c.hp + "/100"), meter(c.down ? 0 : c.hp, "corp"));
        box.append(r);
      }
      wrap.append(box);
    }
    if (st.conflict && st.conflict.a) {
      const name = (k) => ((st.corps || []).find((c) => c.key === k) || {}).name || k;
      const box = n("section", "lv-panel");
      box.append(n("h3", "lv-h", "Война корпораций"));
      const f = Number(st.conflict.front || 0);
      const row = n("div", "lv-front");
      row.append(n("span", "lv-front-side", name(st.conflict.a)));
      const bar = n("div", "lv-front-bar"); const mark = n("div", "lv-front-mark"); mark.style.left = (50 + f / 2) + "%"; bar.append(mark);
      row.append(bar, n("span", "lv-front-side right", name(st.conflict.b)));
      box.append(row, n("p", "lv-note", "Фронт " + (f > 0 ? "+" : "") + f + " из 100"));
      wrap.append(box);
    }
    if (st.crisis) {
      const box = n("section", "lv-panel crisis");
      box.append(n("h3", "lv-h", "Кризис города"), n("p", "lv-note", "Идёт кризис: окно кризиса первым в главном меню."));
      wrap.append(box);
    }
    el.text.replaceChildren(wrap);
    for (const r of restRows(ctx.s, (b) => b.action === "go")) el.rows.append(ctx.row(r.map((b) => ctx.press(b))));
    return true;
  }

  // ---------- W8: the breach protocol and the ICE duel ----------
  function netrun(ctx) {
    const h = ctx.env.hud || {};
    const run = h.netrun;
    if (!run) return false;
    const { el, s } = ctx;
    el.title.hidden = false;
    if (run.kind === "duel") {
      el.title.replaceChildren(document.createTextNode("Дуэль с ЛЕДом"));
      const box = n("div", "lv-duel");
      const line = (label, v, mx, cls) => { const r = n("div", "lv-duel-row"); r.append(n("span", "lv-duel-k", label), n("span", "lv-duel-v", v + "/" + mx), meter(mx ? (v / mx) * 100 : 0, cls)); return r; };
      box.append(line("ЛЕД", run.ice, run.ice_max, "corp"), line("Твой мозг", run.brain, run.brain_max, "gang"));
      box.append(n("p", "lv-note", "Ход " + run.round + " из " + run.rounds + ". " + (run.intent ? "ЛЕД " + run.intent + "." : "ЛЕД скрывает намерение.")));
      if (run.log && run.log.length) { const lg = n("div", "lv-duel-log"); for (const x of run.log) lg.append(n("div", "", x)); box.append(lg); }
      el.text.replaceChildren(box);
      for (const r of s.rows) el.rows.append(ctx.row(r.map((b) => ctx.press(b))));
      return true;
    }
    el.title.replaceChildren(document.createTextNode("Протокол взлома: " + run.title));
    const box = n("div", "lv-breach");
    if (run.sub) box.append(n("p", "lv-note", run.sub));
    const ds = n("div", "lv-daemons");
    for (const d of run.daemons) {
      const r = n("div", "lv-daemon" + (d.ok ? " ok" : ""));
      r.append(n("span", "lv-daemon-name", (d.ok ? "Взломан · " : "") + d.name));
      const seq = n("span", "lv-seq"); for (const c of d.seq) seq.append(n("span", "lv-code", c)); r.append(seq);
      ds.append(r);
    }
    box.append(ds);
    const buf = n("div", "lv-buffer");
    buf.append(n("span", "lv-buffer-k", "Буфер"));
    for (let i = 0; i < run.max; i++) buf.append(n("span", "lv-slot" + (run.buffer[i] ? " full" : ""), run.buffer[i] || ""));
    box.append(buf);
    box.append(n("p", "lv-note", "Бери код в " + (run.mode === "row" ? "строке " : "столбце ") + (run.line + 1) + " (подсвечена). Следующий — в столбце или строке только что взятого."));
    const grid = n("div", "lv-grid");
    grid.style.gridTemplateColumns = "repeat(" + (run.grid[0] || []).length + ", minmax(0, 1fr))";
    const used = new Set(run.used.map((x) => x[0] + ":" + x[1]));
    const active = new Set(run.active.map((x) => x[0] + ":" + x[1]));
    run.grid.forEach((rowCodes, r) => rowCodes.forEach((code, k) => {
      const b = btnOf(s, "c", "" + r + k);
      const key = r + ":" + k;
      const cls = "lv-cell" + (used.has(key) ? " used" : "") + (active.has(key) ? " active" : "");
      if (!b || used.has(key)) { const x = n("span", cls, used.has(key) ? "··" : code); x.setAttribute("aria-hidden", "true"); grid.append(x); return; }
      const x = ctx.press(b, { cls, content: [document.createTextNode(code)] });
      x.setAttribute("aria-label", "Код " + code + ", строка " + (r + 1) + ", столбец " + (k + 1));
      if (!active.has(key)) x.disabled = true;
      grid.append(x);
    }));
    box.append(grid);
    el.text.replaceChildren(box);
    for (const r of restRows(s, (b) => b.action === "c")) el.rows.append(ctx.row(r.map((b) => ctx.press(b))));
    return true;
  }

  // ---------- W10: the birth card ----------
  function birth(ctx) {
    const b = ctx.s.state && ctx.s.state.birth;
    if (!b) return false;
    const { el, s } = ctx;
    const card = n("article", "lv-birth");
    const top = n("div", "lv-birth-top");
    top.append(n("span", "lv-birth-org", "Найт-Сити · регистрация рождения"), n("span", "lv-birth-no", "№ " + b.no));
    card.append(top);
    card.append(n("h2", "lv-birth-name", b.name));
    card.append(n("p", "lv-birth-line", "Рождение в " + b.month + " " + b.year + " года, " + b.place + "."));
    const dl = n("dl", "lv-birth-dl");
    const add = (k, v) => { if (v == null || v === "") return; const r = n("div", "lv-birth-row"); r.append(n("dt", "", k), n("dd", "", String(v))); dl.append(r); };
    add("Пол", b.gender);
    add("Корни", b.roots);
    const q = Math.max(0, Math.min(5, Number(b.quintile) || 0));
    const dots = n("dd", "lv-dots"); for (let i = 0; i < 5; i++) dots.append(n("span", "lv-dot" + (i < q ? " on" : "")));
    const qr = n("div", "lv-birth-row"); qr.append(n("dt", "", "Достаток"), dots); dl.append(qr);
    add("Доход семьи", b.income);
    add("Порядок рождения", b.order);
    add("Предыстория", b.origin);
    card.append(dl);
    if (b.parents && b.parents.length) {
      const ps = n("div", "lv-parents");
      for (const p of b.parents) {
        const pc = n("div", "lv-parent");
        pc.append(n("span", "lv-parent-who", p.who + ", " + p.age), n("span", "lv-parent-name", p.name || "имя не записано"), n("span", "lv-parent-job", p.job || ""));
        ps.append(pc);
      }
      card.append(ps);
    }
    if (b.story) card.append(n("blockquote", "lv-birth-story", b.story));
    card.append(n("p", "lv-birth-foot", "Жизнь начинается в " + b.age + ". Дальше — как сложится."));
    el.text.replaceChildren(card);
    for (const r of s.rows) el.rows.append(ctx.row(r.map((x) => ctx.press(x))));
    return true;
  }

  window.NCViews = { citymap: cityMap, netrun, birth };

  // ---------- the character tab (W12): W7 body, W9 last fight, drawn into the target the app gives ----------
  let sheetBody = null;
  let player = null;
  function openSheet(target) {
    stopPlay();
    sheetBody = target;
    sheetBody.replaceChildren(n("p", "lv-note", "Загружаю…"));
  }
  async function getJSON(path) {
    const r = await fetch(path, { headers: { "X-Init-Data": INIT }, cache: "no-store" });
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }

  // Where each slot sits on the figure (viewBox 0 0 120 240).
  const SLOT_POS = { brain: [60, 20], eyes: [60, 34], face: [60, 44], spine: [60, 92], circulatory: [70, 78],
    body: [50, 110], skin: [82, 120], forearms: [24, 120], hands: [18, 150], legs: [52, 196] };
  function figure(slots) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 120 240"); svg.setAttribute("class", "lv-figure"); svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = '<g fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round">' +
      '<circle cx="60" cy="30" r="17"/><path d="M60 47v10"/><path d="M36 62h48l6 70H30z"/>' +
      '<path d="M36 64L22 112l-6 40M84 64l14 48 6 40"/><path d="M42 132l-4 96M78 132l4 96M60 132v8"/></g>';
    for (const sl of slots) {
      const p = SLOT_POS[sl.slot]; if (!p) continue;
      const c = document.createElementNS(ns, "circle");
      c.setAttribute("cx", p[0]); c.setAttribute("cy", p[1]); c.setAttribute("r", sl.items.length ? 6 : 3.5);
      c.setAttribute("class", sl.items.length ? (sl.items.some((i) => i.complication) ? "lv-node bad" : "lv-node on") : "lv-node");
      svg.append(c);
    }
    return svg;
  }
  async function showBody(target) {
    openSheet(target);
    try {
      const d = (await getJSON("/api/body")).body;
      if (!d) { sheetBody.replaceChildren(n("p", "lv-note", "Живого персонажа нет.")); return; }
      const wrap = n("div", "lv-body");
      const left = n("div", "lv-body-fig");
      left.append(figure(d.slots));
      const hum = n("div", "lv-hum");
      const hmax = d.humanity_max || Math.max(d.humanity, 1);
      hum.append(n("span", "lv-hum-k", "Человечность " + d.humanity + (d.humanity_max ? "/" + d.humanity_max : "")), meter((d.humanity / hmax) * 100, "corp"));
      left.append(hum);
      const tags = n("div", "lv-tags");
      if (d.borg) tags.append(n("span", "lv-tag", "Борг"));
      if (d.avatar) tags.append(n("span", "lv-tag", "Аватар Заслона"));
      if (tags.childNodes.length) left.append(tags);
      const list = n("div", "lv-slots");
      for (const sl of d.slots) {
        const box = n("section", "lv-slot-box" + (sl.items.length ? "" : " empty"));
        box.append(n("h3", "lv-h", sl.name.charAt(0).toUpperCase() + sl.name.slice(1)));
        if (!sl.items.length) box.append(n("p", "lv-note", "пусто"));
        for (const it of sl.items) {
          const r = n("div", "lv-implant" + (it.complication ? " bad" : ""));
          r.append(n("span", "lv-implant-name", it.name));
          const bits = [];
          if (it.charge != null && it.charge < 100) bits.push("заряд " + it.charge + "%");
          if (it.complication) bits.push("осложнение");
          if (it.humanity_paid) bits.push("−" + it.humanity_paid + " человечности");
          if (bits.length) r.append(n("span", "lv-implant-note", bits.join(" · ")));
          box.append(r);
        }
        list.append(box);
      }
      wrap.append(left, list);
      sheetBody.replaceChildren(wrap);
    } catch (e) { sheetBody.replaceChildren(n("p", "lv-note", "Не удалось загрузить. Проверь связь и открой ещё раз.")); }
  }

  let fightState = null;
  function stopPlay() { if (player) { clearInterval(player); player = null; } const b = document.getElementById("fightPlay"); if (b) b.textContent = "Пуск"; }
  function drawTick(i) {
    const f = fightState.data; const t = f.ticks[i]; fightState.i = i;
    const hp = t ? t.hp : null;
    for (const [id, row] of fightState.rows) {
      const max = row.max || 1; const v = hp && hp[id] != null ? hp[id] : max;
      row.fill.style.width = Math.max(0, Math.min(100, (v / max) * 100)) + "%";
      row.val.textContent = v + "/" + max;
      row.el.classList.toggle("down", v <= 0);
      row.el.classList.toggle("hit", !!(t && t.hits.some((h) => h.to === id)));
    }
    fightState.count.textContent = "Ход " + (t ? t.tick : 0) + " · " + (i + 1) + " из " + f.ticks.length;
    fightState.log.replaceChildren(...(t ? t.lines : []).map((l) => { const d = n("div", "lv-log-line"); d.innerHTML = l; return d; }));
    if (t && t.hits.length) {  // the hits of the tick in one line each, under the bot's own lines
      const box = n("div", "lv-hits");
      for (const h of t.hits) box.append(n("div", "lv-hit" + (h.down ? " down" : ""), (fightState.names[h.by] || h.by) + " → " + (fightState.names[h.to] || h.to) + ": −" + h.dmg + (h.down ? ", падает" : "")));
      fightState.log.append(box);
    } else if (t && !t.lines.length) fightState.log.append(n("div", "lv-log-line", "Ход без попаданий."));
  }
  async function showFight(target) {
    openSheet(target);
    try {
      const f = (await getJSON("/api/fight")).fight;
      if (!f || !f.ticks || !f.ticks.length) { sheetBody.replaceChildren(n("p", "lv-note", "Боёв ещё не было.")); return; }
      const wrap = n("div", "lv-fight");
      wrap.append(n("p", "lv-fight-result " + (f.result === "won" ? "won" : "lost"), (f.title ? f.title + " · " : "") + (f.result_word || f.result) + (f.elite ? " · элитный бой" : "")));
      const people = [f.player, ...(f.allies || []), ...(f.enemies || [])].filter(Boolean);
      const names = {}; people.forEach((p) => { names[p.id] = p.name; });
      const rows = new Map();
      const side = (title, list, cls) => {
        if (!list.length) return null;
        const box = n("div", "lv-side " + cls); box.append(n("h3", "lv-h", title));
        for (const p of list) {
          const r = n("div", "lv-fighter"); const fill = n("div", "lv-meter-fill"); const m = n("div", "lv-meter " + cls); m.append(fill);
          const val = n("span", "lv-fighter-hp");
          r.append(n("span", "lv-fighter-name", p.name + (p.role ? " · " + p.role : "")), val, m);
          rows.set(p.id, { el: r, fill, val, max: p.max_hp }); box.append(r);
        }
        return box;
      };
      const sides = n("div", "lv-sides");
      const a = side("Свои", [f.player, ...(f.allies || [])].filter(Boolean), "ours");
      const b = side("Враги", f.enemies || [], "corp");
      if (a) sides.append(a); if (b) sides.append(b);
      wrap.append(sides);
      const ctrl = n("div", "lv-ctrl");
      const count = n("span", "lv-count");
      const mk = (id, label, fn) => { const x = n("button", "btn lv-ctrl-btn", label); x.type = "button"; x.id = id; x.addEventListener("click", fn); return x; };
      const step = (d) => { stopPlay(); const i = Math.max(0, Math.min(f.ticks.length - 1, fightState.i + d)); drawTick(i); };
      const play = () => {
        if (player) { stopPlay(); return; }
        if (fightState.i >= f.ticks.length - 1) drawTick(0);
        document.getElementById("fightPlay").textContent = "Пауза";
        player = setInterval(() => { if (fightState.i >= f.ticks.length - 1) { stopPlay(); return; } drawTick(fightState.i + 1); }, 1400);
      };
      ctrl.append(mk("fightPrev", "Назад", () => step(-1)), mk("fightPlay", "Пуск", play), mk("fightNext", "Шаг", () => step(1)), count);
      wrap.append(ctrl);
      const log = n("div", "lv-log"); log.setAttribute("aria-live", "polite");
      wrap.append(log);
      sheetBody.replaceChildren(wrap);
      fightState = { data: f, i: 0, rows, log, count, names };
      drawTick(0);
    } catch (e) { sheetBody.replaceChildren(n("p", "lv-note", "Не удалось загрузить. Проверь связь и открой ещё раз.")); }
  }
  window.NCChar = { body: showBody, fight: showFight, stop: stopPlay };
})();
