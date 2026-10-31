/* Night City Life Mini App, v2 «Тактический HUD» (docs/WEBAPP.md W12). Renders the player's window and the app's
 * own tabs (character, chat).
 *
 * Sync contract (server side: app/webapp/gateway.py):
 * - every tap becomes an op with a fresh id, written to localStorage BEFORE it is sent;
 * - ops go one at a time, in order; a network failure keeps the op and retries it with the same id,
 *   the server answers a repeated id with the stored result, so nothing runs twice and nothing is lost;
 * - while an op is unconfirmed the buttons are locked (the next screen is not known yet);
 * - the event stream pushes every change of the window, including the ones made from the group;
 * - "stale" means the window moved on (an old button): the app just shows the current screen.
 * Tabs «Главная», «Город», «Заказы» are ops too (/api/nav: what /play does, then the section tile).
 * v3 «Вывески»: every place has its own palette (app.css, html.t-<theme>), chosen here from the screen name.
 */
(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const INIT = tg ? tg.initData : "";
  const USER = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
  const OPS_KEY = "nc.ops." + (USER ? USER.id : "anon");
  const LOG_KEY = "nc.log." + (USER ? USER.id : "anon");
  const REQ_TIMEOUT_MS = 45000;
  const POLL_MS = 20000;

  const $ = (id) => document.getElementById(id);
  const el = {
    pill: $("pill"), banner: $("banner"), sub: $("sub"), name: $("name"), rep: $("rep"), cash: $("cash"), ap: $("ap"),
    text: $("text"), rows: $("rows"), input: $("input"), reply: $("reply"), journal: $("journal"),
    title: $("title"), hero: $("hero"), hudMain: $("hudMain"), tiles: $("tiles"), tilesHead: $("tilesHead"),
    dock: $("dock"), rowsAfter: $("rowsAfter"), screen: $("screen"), charView: $("charView"), charBody: $("charBody"),
    chatView: $("chatView"), tabs: $("tabs"), chatBadge: $("chatBadge"), sideChar: $("sideChar"),
  };

  const st = { boot: null, rev: -1, env: null, sseOpen: false, sending: false, retryMs: 1000, lastSse: 0,
    tab: "win", winTab: "menu", seg: "sum", back: null };

  // ---------- storage (never trusted to exist: private mode, cleared data) ----------
  const load = (k, d) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } };
  const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* memory only */ } };
  let ops = load(OPS_KEY, []);
  let log = load(LOG_KEY, []);
  const persistOps = () => save(OPS_KEY, ops);

  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : "op-" + Date.now() + "-" + Math.random().toString(16).slice(2));

  // ---------- telegram chrome ----------
  const ver = (v) => { try { return !!(tg && tg.isVersionAtLeast && tg.isVersionAtLeast(v)); } catch (e) { return false; } };
  if (tg) {
    tg.ready();
    try { tg.expand(); } catch (e) {}
    try { if (tg.disableVerticalSwipes) tg.disableVerticalSwipes(); } catch (e) {} // the chat and long screens scroll
  }
  if (/Telegram-Android\/[^ ]+ \([^)]*;\s*LOW\)/i.test(navigator.userAgent)) document.documentElement.classList.add("lowfx");
  const haptic = (kind) => { try { tg && tg.HapticFeedback && tg.HapticFeedback.impactOccurred(kind || "light"); } catch (e) {} };
  const hapticSel = () => { try { tg && tg.HapticFeedback && tg.HapticFeedback.selectionChanged(); } catch (e) {} };
  const hapticErr = () => { try { tg && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred("error"); } catch (e) {} };

  // ---------- themes of places (v3) ----------
  const THEME_BG = { neon: "#0B0A12", under: "#130D0A", clinic: "#EDF1F4", net: "#0C0905", corp: "#111315", alert: "#0F0B0B",
    jail: "#1A1C1D", death: "#0B0B0A", bar: "#100D0B" ,
    tokyo: "#0E0A14", asia: "#120A0A", euro: "#0D1116", sov: "#120D0D", desert: "#13100A", africa: "#120D08",
    frontier: "#0B0E14", latam: "#120B10", oceania: "#08121A", synod: "#0B1012", libria: "#121314", elysium: "#14120D",
    aeon: "#050806", arena: "#120A08" };
  // owner request 30.10 (docs/PLAN_WORLD2.md phase 5): the city of the character paints the app (hud.city)
  const CITY_THEME = {
    japan: "tokyo", korea: "tokyo", china: "asia", hongkong: "asia", singapore: "asia", thailand: "asia", india: "asia",
    europe: "euro", uk: "euro", germany: "euro", italy: "euro", spain: "euro", netherlands: "euro", sweden: "euro",
    poland: "euro", ukraine: "euro", russia: "sov", kazakhstan: "sov", turkey: "desert", uae: "desert", egypt: "desert",
    morocco: "desert", nigeria: "africa", kenya: "africa", south_africa: "africa", nusa: "frontier", texas: "frontier",
    canada: "frontier", mexico: "latam", brazil: "latam", argentina: "latam", colombia: "latam", cuba: "latam",
    australia: "oceania", new_zealand: "oceania", synod: "synod", libria: "libria", elysium: "elysium", aeon: "aeon",
  };
  function cityTheme() { const c = st.env && st.env.hud && st.env.hud.city; return CITY_THEME[c] || "neon"; }
  const THEME_RE = [
    [/^arena/, "arena"],
    [/^(clinic|prtrauma)$/, "clinic"], [/^(rip|chrom)/, "under"],
    [/^(net|ai|bw|engram|avatar|possess)/, "net"],
    [/^(work|duty|staff|biz|empire|corp|cforce|pcorp|pc[a-z]|hubhq)/, "corp"],
    [/^(crime|pbfight|plduel|plattack|graid|craid|raid|pboss|tdun|sortie|wanted|pally|ravenhunt|dgame|fugitive|purge|chall|cm)/, "alert"],
    [/^(gig|elitejob|dispatch|quests|relfixer|blackmarket|dealer)/, "bar"],
    [/^jail/, "jail"], [/^(death|legacy|lineage|chronicle|ending|endg|graveyard)/, "death"],
  ];
  function themeOf(env) {
    if (st.tab !== "win" || !env || !env.screen) return cityTheme();
    const s = env.screen;
    if (s.place === "clinic") return "clinic";
    if (s.place === "under") return "under";
    for (const [re, t] of THEME_RE) if (re.test(s.name || "")) return t;
    return cityTheme();
  }
  let curTheme = null;
  function applyTheme(t) {
    if (t === curTheme) return;
    const root = document.documentElement;
    if (curTheme) root.classList.remove("t-" + curTheme); else root.classList.remove("t-neon");
    root.classList.add("t-" + t);
    curTheme = t;
    const bg = THEME_BG[t] || THEME_BG.neon;
    const meta = document.querySelector('meta[name="theme-color"]'); if (meta) meta.setAttribute("content", bg);
    if (tg) {
      try { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); } catch (e) {}
      try { if (ver("7.10") && tg.setBottomBarColor) tg.setBottomBarColor(bg); } catch (e) {}
    }
  }

  // ---------- status ----------
  function setPill(state, text) { el.pill.dataset.state = state; el.pill.querySelector(".pill-text").textContent = text; el.pill.setAttribute("aria-label", text); }
  function pill() {
    const n = ops.length;
    if (!navigator.onLine || (!st.sseOpen && n && !st.sending)) setPill("off", n ? "Офлайн · " + n : "Офлайн");
    else if (n) setPill("send", "Отправка " + n);
    else if (st.sseOpen) setPill("live", "В сети");
    else setPill("wait", "Связь…");
    if (n && !navigator.onLine) banner("Нет связи. Действие сохранено на телефоне и уйдёт само, как только сеть вернётся.");
    else if (!el.banner.dataset.sticky) banner(null);
  }
  function banner(msg, kind, sticky) {
    if (!msg) { el.banner.hidden = true; el.banner.textContent = ""; delete el.banner.dataset.sticky; return; }
    el.banner.hidden = false; el.banner.textContent = msg; el.banner.dataset.kind = kind || "bad";
    if (sticky) el.banner.dataset.sticky = "1";
  }
  const STATUS_TEXT = { ok: "принято", duplicate: "принято", send: "отправка", stale: "экран сменился", closed: "окно закрыто", error: "ошибка" };
  function journal(label, status, id) {
    const hit = id ? log.find((x) => x.id === id) : null;
    if (hit) { hit.status = status; save(LOG_KEY, log); drawJournal(); return; }
    log.unshift({ id, label: String(label).slice(0, 60), status, t: Date.now() });
    log = log.slice(0, 12); save(LOG_KEY, log); drawJournal();
  }
  const hhmm = (t) => { const d = new Date(t); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); };
  function drawJournal() {
    el.journal.replaceChildren(...log.map((x) => {
      const li = node("li", "s-" + x.status);
      li.append(node("span", "j-t", hhmm(x.t || Date.now())), node("span", "", x.label), node("span", "j-s", STATUS_TEXT[x.status] || x.status));
      return li;
    }));
  }

  // ---------- network ----------
  async function api(path, body) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), REQ_TIMEOUT_MS);
    try {
      const r = await fetch(path, {
        method: body ? "POST" : "GET", signal: ctl.signal, cache: "no-store",
        headers: Object.assign({ "X-Init-Data": INIT }, body ? { "Content-Type": "application/json" } : {}),
        body: body ? JSON.stringify(body) : undefined,
      });
      let data = null;
      try { data = await r.json(); } catch (e) {}
      return { http: r.status, data };
    } finally { clearTimeout(t); }
  }

  // ---------- applying server state ----------
  function accept(env) {
    if (!env || !env.screen) return false;
    if (env.boot !== st.boot) { st.boot = env.boot; st.rev = -1; }
    if (typeof env.rev === "number" && env.rev < st.rev) return false;
    st.rev = typeof env.rev === "number" ? env.rev : st.rev;
    st.env = env;
    render();
    return true;
  }

  async function refresh() {
    try {
      const { http, data } = await api("/api/state");
      if (http === 200 && data) accept(data);
      else authProblem(http);
    } catch (e) { /* offline: the stream or the next poll catches up */ }
    pill();
  }

  function authProblem(http) {
    if (http === 401) banner("Сессия устарела. Закрой и открой приложение заново: действия в очереди не пропадут.", "bad", true);
    if (http === 403) banner("Приложение работает только для игроков игры.", "bad", true);
  }

  // ---------- the op queue ----------
  function enqueue(op) {
    if (ops.length) return; // one unconfirmed action at a time: the next screen is not known yet
    op.op = uuid(); op.t = Date.now();
    ops.push(op); persistOps();
    if (!window.NCFeedback) haptic("light"); // feedback.js answers on pointerdown already
    if (window.NCFeedback) window.NCFeedback.busy(true);
    if (window.NCFx) window.NCFx.click();
    journal(op.label, "send", op.op);
    render(); pill(); flush();
  }

  async function flush() {
    if (st.sending || !ops.length) return;
    st.sending = true; pill();
    const op = ops[0];
    const path = { text: "/api/text", open: "/api/open", nav: "/api/nav" }[op.kind] || "/api/press";
    const body = op.kind === "text" ? { op: op.op, text: op.text }
      : op.kind === "open" ? { op: op.op }
      : op.kind === "nav" ? { op: op.op, to: op.to }
      : { op: op.op, screen: op.screen, action: op.action, arg: op.arg };
    let retry = false;
    try {
      const { http, data } = await api(path, body);
      if (http === 200 && data) {
        ops.shift(); persistOps();
        st.retryMs = 1000;
        journal(op.label, data.status, op.op);
        if (data.status === "error") { hapticErr(); if (window.NCFx) window.NCFx.error(); }
        const before = st.env && st.env.screen ? st.env.screen.name + ":" + st.rev : null;
        st.fxArm = op.kind === "press"; // only the answer to the player's own press plays the chrome
        try { if (!accept(data)) await refresh(); } finally { st.fxArm = false; }
        const after = st.env && st.env.screen ? st.env.screen.name + ":" + st.rev : null;
        if (after !== before && st.tab === "win") window.scrollTo(0, 0); // a new screen starts at its top
        if (op.kind === "text" && data.status !== "error" && el.reply.value.trim() === op.text) el.reply.value = "";
        if (window.NCFeedback) window.NCFeedback.result(data.status, after !== before);
        if (op.kind === "press" && (op.action === "wplay" || op.action === "f3d")) autoOpenGame(); // f3d: the fight in 3D (31.10)
        if (op.kind === "press" && op.action === "d3" && /^trip\./.test(op.arg || "")) { // docs/PLAN_CITY3D_2.md phase 11
          const v = back3dGet(); // a trip from the 3D district: the new district opens in 3D at once (a road event comes first)
          if (v && st.env && st.env.screen && st.env.screen.name === "city") openGame(v.href, "Район в 3D");
        }
        if (op.kind === "nav" && back3dWanted) {
          const h = back3dWanted; back3dWanted = null;
          if (st.env && st.env.screen && st.env.screen.name === "city") openGame(h, "Район в 3D");
          back3dShow();
        }
      } else if (http >= 500 || http === 0) {
        retry = true;
      } else if (http === 401) {
        authProblem(http); retry = true; // a fresh initData after reopening sends it
      } else {
        ops.shift(); persistOps();
        journal(op.label, "error", op.op); authProblem(http); hapticErr(); if (window.NCFx) window.NCFx.error();
        if (window.NCFeedback && http !== 403) window.NCFeedback.toast("Действие не принято. Обнови окно и попробуй снова.", "bad", 3500);
      }
    } catch (e) {
      retry = true; // network down or timeout: same op id next time, the server will not run it twice
    }
    if (retry && !op.warned && window.NCFeedback) {
      op.warned = true;
      window.NCFeedback.toast(navigator.onLine ? "Связь с ботом прервалась. Действие сохранено и уйдёт само." : "Нет сети. Действие сохранено и уйдёт само.", "info", 3500);
    }
    st.sending = false;
    if (!ops.length && window.NCFeedback) window.NCFeedback.busy(false);
    render(); pill();
    if (retry) {
      setTimeout(flush, st.retryMs);
      st.retryMs = Math.min(st.retryMs * 2, 15000);
    } else if (ops.length) flush();
  }

  // ---------- small builders ----------
  const nf = new Intl.NumberFormat("ru-RU");
  const SVG = {
    profile: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    gigs: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    work: '<rect x="4" y="2" width="16" height="20" rx="1"/><path d="M9 22v-4h6v4M8 6h.01M12 6h.01M16 6h.01M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01M16 14h.01"/>',
    ripper: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/>',
    crime: '<circle cx="12" cy="12" r="10"/><path d="M22 12h-4M6 12H2M12 6V2M12 22v-4"/>',
    city: '<path d="M1 6v16l7-4 8 4 7-4V2l-7 4-8-4-7 4z"/><path d="M8 2v16M16 6v16"/>',
    move: '<rect x="1" y="3" width="15" height="13"/><path d="M16 8h4l3 3v5h-7V8z"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>',
    activities: '<path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>',
    relations: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    property: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
    daily: '<rect x="3" y="5" width="13" height="17" rx="2"/><path d="M8 2h11a2 2 0 0 1 2 2v14"/>',
    week: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  };
  function icon(name, size) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("width", size || 20); svg.setAttribute("height", size || 20);
    svg.setAttribute("fill", "none"); svg.setAttribute("stroke", "currentColor"); svg.setAttribute("stroke-width", "1.8");
    svg.setAttribute("stroke-linecap", "round"); svg.setAttribute("stroke-linejoin", "round"); svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = SVG[name] || ""; // static markup from the table above
    return svg;
  }
  const bareLabel = (label) => String(label).replace(/^[^\p{L}\p{N}«"(]+/u, "").trim() || String(label);
  function node(tag, cls, text) { const x = document.createElement(tag); if (cls) x.className = cls; if (text != null) x.textContent = text; return x; }
  // ---------- premium emoji (app/webapp/emoji.py) ----------
  let videoIds = new Set(), lottieIds = new Set();
  fetch("/api/emoji").then((r) => r.json()).then((d) => { videoIds = new Set(d.video || []); lottieIds = new Set(d.lottie || []); upgradeVideos(document); }).catch(() => {});
  function ceImg(id, alt) {
    const img = document.createElement("img");
    img.className = "ce"; img.src = "/emoji/" + id; img.alt = alt || ""; img.dataset.ce = id;
    img.width = 20; img.height = 20; img.style.width = "1.25em"; img.style.height = "1.25em"; img.style.verticalAlign = "-0.28em";
    return img;
  }
  function upgradeVideos(root) {
    root.querySelectorAll("img.ce").forEach((img) => {
      if (!img.dataset.wired) {
        img.dataset.wired = "1";
        img.addEventListener("error", () => img.replaceWith(document.createTextNode(img.alt || ""))); // plain emoji
      }
      if (lottieIds.has(img.dataset.ce) && window.NCFx2 && window.NCFx2.canLottie()) { window.NCFx2.lottie(img); return; } // W16: .tgs plays
      if (!videoIds.has(img.dataset.ce)) return;
      const v = document.createElement("video");
      v.className = "ce-video"; v.src = "/emoji/" + img.dataset.ce + ".webm"; v.poster = img.src;
      v.autoplay = true; v.loop = true; v.muted = true; v.playsInline = true; v.setAttribute("aria-label", img.alt || "");
      v.addEventListener("error", () => v.replaceWith(img));
      img.replaceWith(v);
    });
  }

  // ---------- pictures of places (webapp/img, W12) ----------
  const DISTRICT_IMG = {
    watson: "district_watson", northside: "district_watson", little_europe: "district_watson",
    westbrook: "district_westbrook", north_oak: "district_westbrook",
    city_center: "district_city_center", hot_zone: "district_city_center", waterfront: "district_city_center",
    heywood: "district_heywood", the_glen: "district_heywood", university: "district_heywood",
    santo_domingo: "district_santo_domingo", pacifica: "district_pacifica", combat_zone: "district_pacifica",
    dogtown: "district_dogtown", badlands: "district_badlands",
  };
  const SCREEN_IMG = [
    [/^clinic$/, "ripperdoc_clean"], [/^rip/, "ripperdoc_dirty"], [/^chrom/, "ripperdoc_dirty"],
    [/^(gigcheck|gigres)$/, "gig_complication"], [/^(gigs|gig|elitejob|dispatch)/, "bar_mercs"],
    [/^(work|duty|staff|biz)/, "corp_office"], [/^(net|aisub|aitarget)/, "gig_hack"],
    [/^(crime|pbfight|plduel|plattack|graid|craid|raid|pboss|tdun|sortie|wanted)/, "combat_street"],
    [/^jail/, "prison"], [/^(property|prhome|homeitems|lifestyle|garage)/, "apartment_megabuilding"],
    [/^prtrauma$/, "trauma_team"], [/^(death|legacy|lineage|chronicle)/, "graveyard"],
    [/^(activities|acts|actout|actsout|actskill|actstake|premieres)/, "braindance"], [/^studio/, "news_studio"],
  ];
  const imgUrl = (name) => "/static/img/" + name + ".webp";
  // owner request 30.10 (docs/PLAN_WORLD2.md phase 5): a city of the world shows its own street (free photos, LICENSES.txt)
  const CITY_IMGS = new Set(["china", "europe", "germany", "hongkong", "india", "italy", "japan", "kazakhstan", "korea", "mexico", "netherlands", "poland", "singapore", "sweden", "texas", "thailand", "turkey", "uae", "uk"]);
  function districtImg(h) {
    if (h && h.city && h.city !== "nc" && CITY_IMGS.has(h.city)) return "city_" + h.city;
    return DISTRICT_IMG[h && h.district_key] || "district_city_center";
  }
  function screenImage(name, h, place) {
    if (place === "clinic") return imgUrl("ripperdoc_clean");
    if (name === "city" && h && h.district_key) return imgUrl(districtImg(h));
    for (const [re, img] of SCREEN_IMG) if (re.test(name)) return imgUrl(img);
    return null;
  }
  function hero(url, place, titleNodes, chips) {
    const img = node("div", "hero-img"); img.style.backgroundImage = "url('" + url + "')";
    const box = node("div", "hero-in");
    const t = node("h1", "hero-t"); t.append(...titleNodes); box.append(t);
    if (place) box.append(node("span", "hero-k", place));
    if (chips && chips.length) { const c = node("div", "chips"); c.append(...chips); box.append(c); }
    el.hero.replaceChildren(img, node("div", "hero-tint"), box);
    el.hero.hidden = false;
  }
  // ---------- W17 liquid glass: the place itself, blurred, behind the whole page ----------
  let wallUrl = null, wallFront = 0;
  function drawWall(h) {
    let url = null;
    const img = !el.hero.hidden && el.hero.querySelector(".hero-img");
    if (img && st.tab === "win") url = img.style.backgroundImage.replace(/^url\(["']?|["']?\)$/g, "");
    if (!url && h) url = imgUrl(districtImg(h));
    if (!url || url === wallUrl) return;
    wallUrl = url;
    let wall = document.getElementById("wall");
    if (!wall) {
      wall = document.createElement("div"); wall.id = "wall"; wall.className = "wall"; wall.setAttribute("aria-hidden", "true");
      wall.append(document.createElement("i"), document.createElement("i"));
      document.body.prepend(wall);
    }
    const layers = wall.children;
    const next = layers[1 - wallFront];
    next.style.backgroundImage = "url('" + url + "')";
    const pic = new Image();
    pic.onload = () => { next.classList.add("on"); layers[wallFront].classList.remove("on"); wallFront = 1 - wallFront; };
    pic.src = url;
  }
  function chip(label, value) { const c = node("span", "chip", label + " "); c.append(node("b", "", value)); return c; }

  // ---------- the top bar ----------
  function drawTop(h) {
    if (h) {
      el.name.textContent = h.name; el.name.classList.remove("brand");
      el.sub.textContent = [h.cred_title, h.age + " лет", h.year].filter(Boolean).join(" · ");
      el.rep.textContent = "Реп " + h.cred; el.rep.hidden = false;
      el.rep.title = "Уличная репутация " + h.cred + " из " + h.cred_max;
      el.cash.textContent = nf.format(h.eddies) + " €$"; el.cash.hidden = false;
      el.cash.title = h.debt ? "Долг " + nf.format(h.debt) + " €$" : "Эдди";
      el.ap.textContent = "ОД " + h.ap; el.ap.hidden = false; el.ap.title = "Очки действий";
    } else {
      el.name.textContent = "Night City Life"; el.name.classList.add("brand");
      el.sub.textContent = USER ? [USER.first_name, USER.last_name].filter(Boolean).join(" ") : "";
      el.rep.hidden = true; el.cash.hidden = true; el.ap.hidden = true;
    }
  }

  // ---------- vitals (the main menu, the character tab, the desktop column) ----------
  function meter(v, max, low) {
    const m = node("div", "meter" + (low ? " low" : ""));
    const i = node("i"); i.style.width = (max > 0 ? Math.max(0, Math.min(100, (v / max) * 100)) : 0) + "%"; m.append(i);
    m.setAttribute("role", "img"); m.setAttribute("aria-label", v + " из " + max);
    return m;
  }
  function vrow(label, value, opts) {
    const r = node("div", "vrow" + (opts && opts.bad ? " bad" : ""));
    r.append(node("span", "vrow-k", label), node("span", "vrow-v" + (opts && opts.txt ? " txt" : ""), value));
    if (opts && opts.meter) r.append(opts.meter);
    if (opts && opts.note) r.append(node("span", "vrow-n", opts.note));
    return r;
  }
  function vitals(h) {
    const mood = String(h.mood_text || "").replace(/^Настроение\s*/, "");
    const [moodHead, moodTail] = mood.split(/:\s*/, 2);
    const low = h.hp_max > 0 && h.hp / h.hp_max < 0.3;
    const out = [
      vrow("Здоровье", h.hp + " / " + h.hp_max, { meter: meter(h.hp, h.hp_max, low), bad: low }),
      vrow("Настроение", moodHead || String(h.mood), { txt: true, note: moodTail || null }),
    ];
    if (h.humanity_max) out.push(vrow("Человечность", h.humanity + " / " + h.humanity_max));
    if (h.heat) out.push(vrow("Розыск", h.heat + " из " + h.heat_max, { bad: true }));
    if (h.debt) out.push(vrow("Долг", nf.format(h.debt) + " €$", { bad: true }));
    if (h.weather) out.push(node("div", "weather", h.weather));
    return out;
  }

  // ---------- the character summary: radar and skills ----------
  const SVGNS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs) { const x = document.createElementNS(SVGNS, tag); for (const k in attrs) x.setAttribute(k, attrs[k]); return x; }
  function radar(h) {
    const stats = h.stats || [];
    if (stats.length < 3) return null;
    const W = 300, H = 262, cx = 150, cy = 134, R = 98;
    const max = Math.max(h.stat_cap || 10, ...stats.map((x) => x.v));
    const ang = (i) => (-90 + (360 / stats.length) * i) * Math.PI / 180;
    const pt = (i, r) => [cx + Math.cos(ang(i)) * r, cy + Math.sin(ang(i)) * r];
    const poly = (r) => stats.map((_, i) => pt(i, r).map((v) => v.toFixed(1)).join(",")).join(" ");
    const svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, class: "radar", role: "img",
      "aria-label": "Характеристики: " + stats.map((x) => x.label + " " + x.v).join(", ") + " из " + max });
    [0.25, 0.5, 0.75, 1].forEach((f) => svg.append(svgEl("polygon", { points: poly(R * f), class: "ring" + (f === 1 ? " out" : "") })));
    stats.forEach((_, i) => { const [x, y] = pt(i, R); svg.append(svgEl("line", { x1: cx, y1: cy, x2: x.toFixed(1), y2: y.toFixed(1), class: "axis" })); });
    const pts = stats.map((s, i) => pt(i, R * Math.max(0.04, s.v / max)));
    svg.append(svgEl("polygon", { points: pts.map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" "), class: "area" }));
    pts.forEach(([x, y]) => svg.append(svgEl("circle", { cx: x.toFixed(1), cy: y.toFixed(1), r: 3, class: "dot" })));
    stats.forEach((s, i) => {
      const [x, y] = pt(i, R + 16);
      const dx = x - cx;
      const t = svgEl("text", { x: x.toFixed(1), y: (y + 4).toFixed(1), "text-anchor": Math.abs(dx) < 12 ? "middle" : dx > 0 ? "start" : "end" });
      t.append(document.createTextNode(s.label + " "));
      const v = svgEl("tspan", { class: "v" }); v.textContent = String(s.v); t.append(v);
      if (s.v !== s.base) { const b = svgEl("tspan", { class: "b" }); b.textContent = " (" + s.base + ")"; t.append(b); }
      svg.append(t);
    });
    return svg;
  }
  function skillsList(h) {
    const box = node("div", "skills");
    const list = [...(h.skills || [])].sort((a, b) => (b.lvl - a.lvl) || (b.xp - a.xp));
    for (const s of list) {
      const r = node("div", "skill" + (s.lvl ? "" : " zero"));
      r.append(node("span", "skill-lvl", String(s.lvl)));
      const name = node("span", "skill-name", s.label);
      if (s.bonus) name.append(node("small", "", (s.bonus > 0 ? "+" : "") + s.bonus));
      r.append(name);
      const capped = s.lvl >= s.cap || !s.need;
      r.append(node("span", "skill-xp", capped ? "предел" : s.xp + "/" + s.need));
      r.append(capped ? meter(1, 1) : meter(s.xp, s.need));
      box.append(r);
    }
    return box;
  }
  function drawSummary(target, h, full) {
    const kids = [];
    if (full) {
      const head = node("div", "char-head");
      head.append(node("h2", "char-name", h.name), node("span", "char-sub", [h.age + " лет", h.district, h.cred_title].filter(Boolean).join(" · ")));
      kids.push(head);
    }
    const rd = radar(h);
    if (rd) { const box = node("section", "radar-box"); box.append(rd); kids.push(box); }
    const v = node("div", "hud");
    const low = h.hp_max > 0 && h.hp / h.hp_max < 0.3;
    v.append(vrow("Здоровье", h.hp + " / " + h.hp_max, { meter: meter(h.hp, h.hp_max, low), bad: low }));
    if (h.humanity_max) v.append(vrow("Человечность", h.humanity + " / " + h.humanity_max, { meter: meter(h.humanity, h.humanity_max) }));
    v.append(vrow("Уличная репутация", h.cred + " / " + h.cred_max, { meter: meter(h.cred, h.cred_max), note: h.cred_title || null }));
    kids.push(v);
    if (full && h.skills && h.skills.length) {
      kids.push(node("div", "sh", "Навыки"), skillsList(h));
      const busy = ops.length > 0;
      const b = node("button", "btn wide-btn", "Профиль в окне: прокачка, обеты, род");
      b.type = "button"; b.disabled = busy;
      b.addEventListener("click", () => { setTab("win", "menu"); nav("profile", "Профиль"); });
      kids.push(b);
      if (window.NCFx) {  // W13: interface sounds, off by default
        const r = node("div", "sfx-row");
        const lbl = node("span", "", "Звуки интерфейса");
        const t = node("button", "btn", window.NCFx.soundOn() ? "Включены" : "Выключены");
        t.type = "button"; t.setAttribute("aria-pressed", String(window.NCFx.soundOn()));
        t.addEventListener("click", () => { window.NCFx.setSound(!window.NCFx.soundOn()); t.textContent = window.NCFx.soundOn() ? "Включены" : "Выключены"; t.setAttribute("aria-pressed", String(window.NCFx.soundOn())); });
        r.append(lbl, t); kids.push(r);
      }
      if (window.NCFx2) {  // W16: the iOS-like glass, on by default
        const r = node("div", "sfx-row");
        const t = node("button", "btn", window.NCFx2.glassOn() ? "Включено" : "Выключено");
        t.type = "button"; t.setAttribute("aria-pressed", String(window.NCFx2.glassOn()));
        t.addEventListener("click", () => { window.NCFx2.setGlass(!window.NCFx2.glassOn()); t.textContent = window.NCFx2.glassOn() ? "Включено" : "Выключено"; t.setAttribute("aria-pressed", String(window.NCFx2.glassOn())); });
        r.append(node("span", "", "Стекло"), t); kids.push(r);
      }
    }
    target.replaceChildren(...kids);
  }

  // ---------- tabs ----------
  const ROOT_OF = { menu: "menu", city: "city", gigs: "gigs" };
  const CITY_RE = /^(city|citymap|move|movec|world|wcountry|trip|gangs|corps|influence|equil|cdyn|ctrl|crisis|purge|hubs|hubpick|hubhq|look)/;
  const GIGS_RE = /^(gig|elitejob|dispatch|quests)/;
  function winTabOf(name) {
    if (name === "menu") return "menu";
    if (CITY_RE.test(name)) return "city";
    if (GIGS_RE.test(name)) return "gigs";
    return st.winTab;
  }
  function nav(to, label) { enqueue({ kind: "nav", to, label: label || to, key: "nav:" + to }); }
  function setTab(tab, winTab) {
    if (st.tab === "char" && tab !== "char" && window.NCChar) window.NCChar.stop();
    st.tab = tab;
    if (winTab) st.winTab = winTab;
    if (window.NCChat) window.NCChat.show(tab === "chat");
    render();
    if (tab === "char") drawChar(true);
    window.scrollTo(0, 0);
    if (window.NCFx) window.NCFx.tab(tab === "win" ? el.screen : tab === "char" ? el.charView : el.chatView);
  }
  el.tabs.addEventListener("click", (ev) => {
    const b = ev.target.closest(".tab");
    if (!b) return;
    const t = b.dataset.tab;
    hapticSel();
    if (t === "char" || t === "chat") { setTab(t); return; }
    const s = st.env && st.env.screen;
    const atRoot = s && !s.closed && !s.unknown && s.name === ROOT_OF[t];
    if (st.tab === "win" && atRoot) return;
    if (ops.length) { setTab("win"); return; } // an action is on its way: show where it lands first
    setTab("win", t);
    if (!atRoot) nav(t, { menu: "Главная", city: "Город", gigs: "Заказы" }[t]);
  });
  function drawTabs() {
    const active = st.tab === "win" ? st.winTab : st.tab;
    el.tabs.querySelectorAll(".tab").forEach((b) => {
      if (b.dataset.tab === active) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
  }
  if (window.NCChat) window.NCChat.onCount((n) => { el.chatBadge.hidden = !n; el.chatBadge.textContent = n > 99 ? "99+" : String(n); });
  el.charView.querySelector(".seg").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-seg]");
    if (!b || b.dataset.seg === st.seg) return;
    hapticSel();
    if (window.NCChar) window.NCChar.stop();
    st.seg = b.dataset.seg; drawChar(true);
  });
  function drawChar(load) {
    el.charView.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.seg === st.seg)));
    const h = st.env && st.env.hud;
    if (st.seg === "sum") {
      if (h) drawSummary(el.charBody, h, true);
      else el.charBody.replaceChildren(Object.assign(node("div", "notice"), { textContent: "Живого персонажа нет. Создай его в окне." }));
    } else if (load && window.NCChar) {
      window.NCChar[st.seg === "body" ? "body" : "fight"](el.charBody);
    }
  }

  // ---------- the Telegram back button ----------
  function findBack(s) {
    if (!s || !s.rows) return null;
    let menu = null;
    for (const r of s.rows) for (const b of r) {
      const l = bareLabel(b.label);
      if (b.action === "back" || /^Назад/.test(l)) return b;
      if (!menu && (b.action === "menu" || l === "Меню")) menu = b;
    }
    return menu;
  }
  function drawBack() {
    if (!tg || !tg.BackButton || !ver("6.1")) return;
    let show = false;
    if (st.tab !== "win") show = true;
    else {
      const s = st.env && st.env.screen;
      st.back = s && s.name !== "menu" && !s.closed && !s.unknown ? findBack(s) : null;
      show = !!st.back;
    }
    try { if (show) tg.BackButton.show(); else tg.BackButton.hide(); } catch (e) {}
  }
  if (tg && tg.BackButton && ver("6.1")) {
    tg.BackButton.onClick(() => {
      hapticSel();
      if (st.tab !== "win") { setTab("win"); return; }
      const b = st.back;
      if (b && !ops.length) enqueue({ kind: "press", screen: b.screen, action: b.action, arg: b.arg, label: bareLabel(b.label), key: b.screen + ":" + b.action + ":" + (b.arg || "") });
    });
  }

  // ---------- the main menu: hero, vitals, tiles ----------
  function drawMenu(s, env, busy, pendingKey, counter) {
    const h = env.hud;
    const ctl = h.control;
    const chips = [];
    if (ctl) chips.push(chip("банды", ctl[0] + "%"), chip("корпорации", ctl[1] + "%"));
    chips.push(chip("розыск", h.heat ? h.heat + " из " + h.heat_max : "нет"));
    hero(imgUrl(districtImg(h)), (h.city_name || "Найт-Сити") + ", " + h.year, [document.createTextNode(h.district || "Найт-Сити")], chips);
    el.hudMain.replaceChildren(...vitals(h)); el.hudMain.hidden = false;
    const rest = env.menu_rest != null ? env.menu_rest : s.html;
    el.text.innerHTML = rest; // server-built from Telegram HTML; player-typed strings are escaped there
    el.text.hidden = !String(rest).trim();
    wireText();
    const tiles = [];
    const after = document.createDocumentFragment(); // buttons the bot puts after the sections (bug report, close)
    let week = null;
    for (const r of s.rows) {
      const plain = [];
      for (const b of r) {
        if (b.action === "go" && b.arg === "week") week = b;
        else if (b.action === "go" && SVG[b.arg]) tiles.push(pressButton(b, busy, pendingKey, counter, true));
        else plain.push(b);
      }
      if (plain.length) (tiles.length ? after : el.rows).append(row(plain.map((b) => pressButton(b, busy, pendingKey, counter))));
    }
    el.tiles.replaceChildren(...tiles);
    el.tiles.hidden = tiles.length === 0; el.tilesHead.hidden = tiles.length === 0;
    el.rowsAfter.append(after);
    if (week) {
      el.dock.replaceChildren(pressButton(week, busy, pendingKey, counter, false, "week"));
      el.dock.hidden = false;
    }
  }
  function pressButton(b, busy, pendingKey, counter, tile, iconName, extra) {
    counter.n += 1;
    const key = b.screen + ":" + b.action + ":" + (b.arg || "");
    const onClick = () => {
      if (qteApplies(b)) { runQte(qteMode(b)).then((g) => enqueue({ kind: "press", screen: b.screen, action: b.action, arg: g ? b.arg + ".qt" + g : b.arg, label: bareLabel(b.label), key })); return; }
      enqueue({ kind: "press", screen: b.screen, action: b.action, arg: b.arg, label: bareLabel(b.label), key });
    };
    const plainTile = tile && !b.ce;
    const x = button(plainTile || iconName ? Object.assign({}, b, { label: bareLabel(b.label), noCe: !!iconName }) : b, onClick, busy, pendingKey === key, counter.n);
    if (tile) { x.classList.add("tile"); if (plainTile) x.prepend(icon(b.arg, 24)); }
    if (extra && extra.cls) x.classList.add(...extra.cls.split(" "));
    if (extra && extra.content) { x.replaceChildren(...extra.content); if (counter.n <= 9) x.dataset.n = String(counter.n); }
    if (iconName) { x.classList.add("dock-btn"); x.dataset.style = "success"; x.prepend(icon(iconName, 20)); }
    return x;
  }

  // ---------- rendering ----------
  function render() {
    back3dShow();
    const env = st.env;
    const busy = ops.length > 0;
    const pendingKey = busy ? ops[0].key : null;
    const h = env ? env.hud : null;
    drawTop(h);
    if (h) { el.sideChar.hidden = false; drawSummary(el.sideChar, h, false); } else el.sideChar.hidden = true;
    el.screen.hidden = st.tab !== "win";
    el.charView.hidden = st.tab !== "char";
    el.chatView.hidden = st.tab !== "chat";
    if (env && env.screen && !env.screen.closed && !env.screen.unknown && !busy) st.winTab = winTabOf(env.screen.name);
    applyTheme(themeOf(env));
    drawTabs();
    drawBack();
    if (st.tab === "char") { if (st.seg === "sum") drawChar(false); }
    drawWindow(env, busy, pendingKey, h);
    if (env && env.screen) { try { chromeFx(env.screen); } catch (e) { /* looks only */ } }
    document.documentElement.classList.toggle("has-dock", st.tab === "win" && !el.dock.hidden);
    drawWall(h);
    if (window.NCFx) { try { window.NCFx.after({ tab: st.tab, env, busy, theme: curTheme }); } catch (e) { /* looks only */ } }
    if (window.NCFx2) { try { window.NCFx2.after({ tab: st.tab, env, busy, theme: curTheme }); } catch (e) { /* looks only */ } }
  }
  function drawWindow(env, busy, pendingKey, h) {
    el.rows.replaceChildren();
    el.rowsAfter.replaceChildren();
    el.tiles.replaceChildren(); el.tiles.hidden = true; el.tilesHead.hidden = true;
    el.dock.replaceChildren(); el.dock.hidden = true;
    el.input.hidden = true;
    el.title.hidden = true; el.title.replaceChildren();
    el.hero.hidden = true; el.hero.replaceChildren();
    el.hudMain.hidden = true; el.hudMain.replaceChildren();
    el.text.hidden = false;
    if (!env) { el.text.innerHTML = '<div class="notice">Загружаю окно…</div>'; return; }
    const s = env.screen;
    if (s.closed || env.status === "closed") {
      el.text.innerHTML = '<div class="notice"><h2>Окно закрыто</h2>Открой его здесь: в чате оно откроется на том же месте.</div>';
      el.rows.append(row([button({ label: "Открыть окно", style: "success" }, () => enqueue({ kind: "open", label: "Открыть окно", key: "open" }), busy, pendingKey === "open", 1)]));
      return;
    }
    if (s.unknown || env.status === "unknown") {
      el.text.innerHTML = '<div class="notice"><h2>Окно уже открыто</h2>Приложение ещё не видело этот экран. Нажми в чате любую кнопку окна или открой меню здесь.</div>';
      el.rows.append(row([button({ label: "Открыть меню", style: "success" }, () => enqueue({ kind: "open", label: "Открыть меню", key: "open" }), busy, pendingKey === "open", 1)]));
      return;
    }
    const counter = { n: 0 };
    const view = window.NCViews && window.NCViews[s.name];
    let drawn = false;
    if (view) {  // W6-W10: screens with their own look (webapp/living.js); a failure falls back to the plain view
      try {
        drawn = !!view({ s, env, el, busy, row, press: (b, extra) => pressButton(b, busy, pendingKey, counter, false, null, extra) });
      } catch (e) { drawn = false; el.text.replaceChildren(); el.rows.replaceChildren(); }
    }
    if (drawn) {
      wireText();
    } else if (s.name === "menu" && h) {
      drawMenu(s, env, busy, pendingKey, counter);
    } else {
      const pic = screenImage(s.name, h, s.place);
      if (s.title) {
        const parts = [];
        if (s.title_icon) { const tmp = node("span"); tmp.innerHTML = s.title_icon; parts.push(...tmp.childNodes); } // server-built
        parts.push(document.createTextNode(s.title));
        if (pic) hero(pic, h && h.district ? h.district : null, parts, null);
        else { el.title.append(...parts); el.title.hidden = false; }
        el.text.innerHTML = s.body || "";
      } else {
        el.text.innerHTML = s.html; // server-built from Telegram HTML; player-typed strings are escaped there
      }
      el.text.hidden = !String(el.text.innerHTML).trim();
      wireText();
      for (const r of s.rows) el.rows.append(row(r.map((b) => pressButton(b, busy, pendingKey, counter))));
    }
    if (s.awaiting_text) {
      el.input.hidden = false;
      el.reply.disabled = busy;
    }
    upgradeVideos(document);
  }
  function row(buttons) { const d = document.createElement("div"); d.className = "row"; d.append(...buttons); return d; }
  function button(b, onClick, busy, pending, n) {
    const x = document.createElement("button");
    x.type = "button"; x.className = "btn" + (pending ? " pending" : "");
    if (b.style) x.dataset.style = b.style;
    if (n <= 9) { const k = document.createElement("span"); k.className = "key"; k.textContent = String(n); x.append(k); x.dataset.n = String(n); }
    if (b.ce && !b.noCe) { x.append(ceImg(b.ce, b.ce_alt)); x.append(document.createTextNode(b.text != null ? b.text : b.label)); }
    else x.append(document.createTextNode(b.label));
    if (pending) {  // W14: a running bar and, after a second, a word that the bot is on it
      const bar = document.createElement("span"); bar.className = "fb-bar"; bar.setAttribute("aria-hidden", "true");
      const w = document.createElement("span"); w.className = "fb-wait"; w.textContent = "Бот обрабатывает…";
      x.append(bar, w); x.setAttribute("aria-busy", "true");
    }
    x.disabled = busy;
    x.addEventListener("click", onClick);
    return x;
  }
  window.NCApp = { rewire: () => { wireText(); upgradeVideos(el.text); } };  // W16: after fx2.js restores the text
  function wireText() {
    el.text.querySelectorAll(".spoiler").forEach((sp) => sp.addEventListener("click", () => sp.classList.add("shown")));
    el.text.querySelectorAll("blockquote.expandable").forEach((q) => q.addEventListener("click", () => q.classList.toggle("open")));
    el.text.querySelectorAll("a[href]").forEach((a) => a.addEventListener("click", (ev) => {
      ev.preventDefault();
      const href = a.getAttribute("href");
      if (isGameLink(href)) { openGame(href, a.textContent); return; }  // an arcade game opens inside the app
      try {
        if (tg && /^https:\/\/t\.me\//.test(href)) tg.openTelegramLink(href);
        else if (tg && /^https?:/.test(href)) tg.openLink(href);
        else if (!tg) window.open(href, "_blank", "noopener");
      } catch (e) { window.open(href, "_blank", "noopener"); }
    }));
  }

  el.input.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = el.reply.value.trim();
    if (!text || ops.length) return;  // the text stays in the field until the bot takes it (W14)
    enqueue({ kind: "text", text, label: "Ввод: " + text, key: "text" });
  });

  document.addEventListener("keydown", (ev) => {
    if (qteOpen) return;
    if (game) { if (ev.key === "Escape") closeGame(); return; }
    if (ev.target && (ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA")) return;
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (st.tab === "win" && /^[1-9]$/.test(ev.key)) {
      const b = el.screen.querySelector('.btn[data-n="' + ev.key + '"]');
      if (b && !b.disabled) { ev.preventDefault(); b.click(); }
    }
    if (ev.key === "Escape" && st.tab !== "win") setTab("win");
  });

  // ---------- live stream ----------
  function connect() {
    if (!window.EventSource) return;
    const es = new EventSource("/api/events?init=" + encodeURIComponent(INIT));
    es.onopen = () => { st.sseOpen = true; st.lastSse = Date.now(); pill(); if (ops.length) flush(); };
    es.onmessage = (ev) => {
      st.lastSse = Date.now();
      let data = null;
      try { data = JSON.parse(ev.data); } catch (e) { return; }
      if (!ops.length) accept(data); // with an op in flight its own answer brings the screen
    };
    es.onerror = () => { st.sseOpen = false; pill(); };
  }

  // ---------- arcade games inside the app (owner request 29.10, docs/PLAN_ARCADE_WEB.md) ----------
  // The games are static pages of this same origin (/static/arcade/<version>/<game>/). A link to one opens in a full-screen
  // frame instead of leaving the app. The game's own bridge (nc.js) sends the score to the bot and asks the app to close.
  let game = null; // { box, frame }
  function isGameLink(href) {
    try { const u = new URL(href, location.href); return u.origin === location.origin && (u.pathname.indexOf("/static/arcade/") === 0 || u.pathname.indexOf("/static/city3d/") === 0 || u.pathname.indexOf("/static/unity/") === 0); }
    catch (e) { return false; }
  }
  function openGame(href, title) {
    if (game) return;
    const box = document.createElement("div"); box.className = "game-box";
    const bar = document.createElement("div"); bar.className = "game-bar";
    const back = document.createElement("button"); back.type = "button"; back.className = "game-back"; back.textContent = "← Найт-Сити";
    const name = document.createElement("span"); name.className = "game-title"; name.textContent = String(title || "").replace(/^▶\s*(Открыть\s*)?/, "");
    bar.append(back, name);
    const frame = document.createElement("iframe"); frame.className = "game-frame"; frame.title = "Игра";
    frame.setAttribute("allow", "autoplay; fullscreen; accelerometer; gyroscope");
    frame.addEventListener("load", () => { try { frame.contentWindow.focus(); } catch (e) {} });
    frame.src = href;
    box.append(bar, frame);
    document.body.append(box); document.body.classList.add("in-game");
    game = { box, frame };
    back.addEventListener("click", closeGame);
    if (tg && tg.BackButton) { try { tg.BackButton.onClick(closeGame); tg.BackButton.show(); } catch (e) {} }
    haptic("light");
  }
  function closeGame(quiet) {
    if (!game) return;
    if (quiet !== true) back3dSet(null); // left the 3D district on purpose: no way back offered
    const g = game; game = null;
    g.frame.src = "about:blank"; g.box.remove(); document.body.classList.remove("in-game");
    if (tg && tg.BackButton) { try { tg.BackButton.offClick(closeGame); tg.BackButton.hide(); } catch (e) {} }
    if (quiet !== true) refresh(); // the pay for the run is already in the character
    back3dShow();
  }
  // owner report 30.10: after a door of the 3D district the player can step back into it from any screen
  var B3D_KEY = "nc.back3d", B3D_TTL = 11 * 3600 * 1000; // the page's pass lives 12 hours
  var back3dEl = null, back3dWanted = null;
  function back3dGet() {
    if (!B3D_KEY) return null; // render() before this part of the script ran
    try { const v = JSON.parse(sessionStorage.getItem(B3D_KEY) || "null"); return v && Date.now() - v.t < B3D_TTL ? v : null; } catch (e) { return null; }
  }
  function back3dSet(href) {
    try { if (href) sessionStorage.setItem(B3D_KEY, JSON.stringify({ href, t: Date.now() })); else sessionStorage.removeItem(B3D_KEY); } catch (e) {}
    back3dShow();
  }
  function back3dShow() {
    const v = back3dGet();
    if (!back3dEl && v) {
      back3dEl = document.createElement("div"); back3dEl.className = "back3d";
      const go = document.createElement("button"); go.type = "button"; go.className = "back3d-go"; go.textContent = "🏙 Обратно в 3D";
      const x = document.createElement("button"); x.type = "button"; x.className = "back3d-x"; x.textContent = "✕"; x.setAttribute("aria-label", "Убрать");
      back3dEl.append(go, x); document.body.append(back3dEl);
      go.addEventListener("click", back3dGo);
      x.addEventListener("click", () => back3dSet(null));
    }
    const typing = !!(st.env && st.env.screen && st.env.screen.awaiting_text); // the reply field takes that place
    if (back3dEl) back3dEl.hidden = !v || !!game || typing;
  }
  function back3dGo() {
    const v = back3dGet(); if (!v) return back3dShow();
    haptic("light");
    if (st.env && st.env.screen && st.env.screen.name === "city" && !ops.length) { openGame(v.href, "Район в 3D"); back3dShow(); return; }
    back3dWanted = v.href; // the doors press the city screen: the window goes there first (the «Город» tab), then the frame opens
    nav("city", "Район в 3D");
  }
  function autoOpenGame() { // the press of «▶ <game>» in the app answers with a link: open it at once
    const a = el.text.querySelector('a[href*="/static/arcade/"], a[href*="/static/city3d/"], a[href*="/static/unity/"]');
    if (a && isGameLink(a.getAttribute("href"))) openGame(a.getAttribute("href"), /city3d/.test(a.getAttribute("href")) ? "Район в 3D" : /\/static\/unity\//.test(a.getAttribute("href")) ? (/[#&]m=fight/.test(a.getAttribute("href")) ? "Бой в 3D" : "Unity-клиент") : a.textContent);
  }
  window.addEventListener("message", (ev) => {
    if (!game || ev.origin !== location.origin || ev.source !== game.frame.contentWindow) return;
    if (ev.data && ev.data.nc === "close") closeGame();
    // owner request 30.10: a door of the 3D district (webapp/city3d/) presses the player's window on the city screen
    if (ev.data && ev.data.nc === "press" && ev.data.screen === "city" && ev.data.action === "d3" && typeof ev.data.arg === "string") {
      back3dSet(game.frame.src);
      closeGame(true);
      enqueue({ kind: "press", screen: "city", action: "d3", arg: ev.data.arg.slice(0, 40), label: "Район в 3D", key: "city:d3:" + ev.data.arg });
    }
  });

  // ---------- quick reactions in a fight (owner requests 30.10, docs/PLAN_QTE.md) ----------
  // A tap on an attack first runs a short game chosen by the weapon (screen.wq from app/bot/qte_modes.py): pistol — one
  // target fast, SMG and assault rifles — a burst over several targets, sniper and bows — catch the swaying target in the
  // crosshair, shotgun and launchers — fire when the target fills the ring, smart guns — lock three marks, blades — swipe the
  // thrown targets, blunt — stop the mark in the zone, fists — break the shop window. The grade (0 none, 1 good, 2 perfect)
  // goes to the bot as ".qt1" / ".qt2" in the button's arg; the bot clamps it and adds a few percent to the strike. A miss,
  // «Пропустить» and «Не показывать» cost nothing: the plain strike goes out.
  let qteOpen = false;
  function qteOn() { try { return localStorage.getItem("nc_qte") !== "off"; } catch (e) { return true; } }
  function qteApplies(b) { return !qteOpen && !game && !!INIT && b.screen === "cm" && (b.action === "shoot" || b.action === "melee") && qteOn(); }
  function qteMode(b) {
    const parts = String(b.arg || "").split(".");
    const weapon = parts[2] && !/^qt\d+$/.test(parts[2]) ? parts[2] : "";
    const wq = (st.env && st.env.screen && st.env.screen.wq) || {};
    return (weapon && wq["w:" + weapon]) || wq[b.action] || (b.action === "shoot" ? "quick" : "swing");
  }
  function hap(kind) { try { if (tg && tg.HapticFeedback) { if (kind === "ok") tg.HapticFeedback.notificationOccurred("success"); else tg.HapticFeedback.impactOccurred(kind); } } catch (e) {} }
  const QTE_HINT = {
    quick: "Цель! Жми по ней", spray: "Очередь! Сбей все цели", scope: "Поймай цель в перекрестье и жми",
    blast: "Подпусти ближе. Жми, когда цель заполнит круг", lock: "Захват! Отметь три цели", slice: "Режь! Проведи пальцем по целям",
    swing: "Жми, когда метка в зелёной зоне", smash: "Бей! Разбей витрину",
  };
  function runQte(mode) {
    return new Promise((resolve) => {
      qteOpen = true;
      const box = document.createElement("div"); box.className = "qte-box qm-" + mode;
      const hint = document.createElement("div"); hint.className = "qte-hint"; hint.textContent = QTE_HINT[mode] || QTE_HINT.quick;
      const arena = document.createElement("div"); arena.className = "qte-arena";
      const bar = document.createElement("div"); bar.className = "qte-actions";
      const skip = document.createElement("button"); skip.type = "button"; skip.className = "qte-skip"; skip.textContent = "Пропустить";
      const off = document.createElement("button"); off.type = "button"; off.className = "qte-off"; off.textContent = "Больше не показывать";
      bar.append(skip, off); box.append(hint, arena, bar); document.body.append(box);
      let done = false; const timers = []; let raf = 0;
      const later = (fn, ms) => { timers.push(setTimeout(fn, ms)); };
      const loop = (fn) => { const step = (now) => { if (done) return; fn(now); raf = requestAnimationFrame(step); }; raf = requestAnimationFrame(step); };
      const finish = (g, silent) => {
        if (done) return; done = true;
        timers.forEach(clearTimeout); cancelAnimationFrame(raf);
        document.removeEventListener("keydown", onKey, true);
        if (!silent) { hint.textContent = g === 2 ? "Точно!" : g === 1 ? "Хорошо" : "Мимо"; hint.dataset.g = String(g); hap(g ? "ok" : "light"); }
        setTimeout(() => { box.remove(); qteOpen = false; resolve(g); }, silent ? 0 : 450);
      };
      const onKey = (ev) => { if (ev.key === "Escape") { ev.preventDefault(); finish(0, true); } };
      document.addEventListener("keydown", onKey, true);
      skip.addEventListener("click", () => finish(0, true));
      off.addEventListener("click", () => { try { localStorage.setItem("nc_qte", "off"); } catch (e) {} finish(0, true); });
      const R = arena.getBoundingClientRect(); const W = R.width || 360, H = R.height || 500;
      const dot = (cls, x, y) => { const d = document.createElement("div"); d.className = cls; d.style.left = x + "px"; d.style.top = y + "px"; arena.append(d); return d; };
      const rnd = (a, b) => a + Math.random() * (b - a);
      (QTE_GAMES[mode] || QTE_GAMES.quick)({ arena, W, H, finish, later, loop, dot, rnd, hint });
    });
  }
  const QTE_GAMES = {
    quick({ arena, W, H, finish, later, dot, rnd }) { // pistol: one target, the faster the better
      later(() => {
        const t = dot("qte-target", rnd(0.14, 0.86) * W, rnd(0.12, 0.85) * H);
        const ring = document.createElement("div"); ring.className = "qte-ring"; t.append(ring);
        const t0 = performance.now(); hap("light");
        t.addEventListener("pointerdown", (ev) => { ev.stopPropagation(); const dt = performance.now() - t0; finish(dt <= 550 ? 2 : dt <= 1100 ? 1 : 0); });
        arena.addEventListener("pointerdown", () => finish(0));
        later(() => finish(0), 1400);
      }, rnd(250, 700));
    },
    spray({ W, H, finish, later, dot, rnd }) { // SMG, assault rifle: five targets in a row, knock down as many as you can
      const N = 5; let hits = 0, gone = 0;
      const over = () => { if (hits + gone >= N) finish(hits >= N ? 2 : hits >= 3 ? 1 : 0); };
      for (let i = 0; i < N; i++) {
        later(() => {
          const t = dot("qte-target small", rnd(0.12, 0.88) * W, rnd(0.1, 0.88) * H); hap("light");
          let alive = true;
          t.addEventListener("pointerdown", (ev) => { ev.stopPropagation(); if (!alive) return; alive = false; hits++; t.classList.add("hit"); hap("rigid"); setTimeout(() => t.remove(), 180); over(); });
          later(() => { if (alive) { alive = false; gone++; t.classList.add("gone"); setTimeout(() => t.remove(), 150); over(); } }, 1050);
        }, 200 + i * 170);
      }
    },
    scope({ arena, W, H, finish, later, loop, rnd }) { // sniper, bow: the target sways in the scope, shoot when it is in the cross
      const r = Math.min(W, H) * 0.42;
      const sc = document.createElement("div"); sc.className = "qte-scope"; sc.style.width = sc.style.height = (2 * r) + "px";
      sc.style.left = (W / 2 - r) + "px"; sc.style.top = (H / 2 - r) + "px";
      const t = document.createElement("div"); t.className = "qte-scope-t"; sc.append(t); arena.append(sc);
      const p1 = rnd(0.8, 2.3), p2 = rnd(0, 6), A = r * 0.6, t0 = performance.now(); let x = 0, y = 0;
      loop((now) => { const ph = (now - t0) / 1000 * 2.2 + p1; x = A * Math.sin(ph); y = A * 0.45 * Math.sin(2 * ph) * (p2 > 3 ? 1 : -1); t.style.transform = "translate(" + x + "px," + y + "px)"; }); // a figure eight through the cross
      arena.addEventListener("pointerdown", () => { const d = Math.hypot(x, y); finish(d <= r * 0.1 ? 2 : d <= r * 0.24 ? 1 : 0); });
      later(() => finish(0), 3400);
    },
    blast({ arena, W, H, finish, later, loop }) { // shotgun, launcher: wait until the target fills the ring, then one shot
      const R = Math.min(W, H) * 0.27;
      const g = document.createElement("div"); g.className = "qte-guide"; g.style.width = g.style.height = (2 * R) + "px"; g.style.left = (W / 2 - R) + "px"; g.style.top = (H / 2 - R) + "px";
      const t = document.createElement("div"); t.className = "qte-approach"; t.style.left = (W / 2) + "px"; t.style.top = (H / 2) + "px";
      arena.append(g, t);
      const t0 = performance.now(), dur = 1700; let ratio = 0.08;
      loop((now) => { const k = Math.min(1, (now - t0) / dur); ratio = 0.08 + 1.5 * k * k; const s = 2 * R * ratio; t.style.width = t.style.height = s + "px"; t.style.margin = (-s / 2) + "px 0 0 " + (-s / 2) + "px"; if (k >= 1) finish(0); });
      arena.addEventListener("pointerdown", () => { const d = Math.abs(ratio - 1); finish(d <= 0.12 ? 2 : d <= 0.28 ? 1 : 0); });
      later(() => finish(0), 2200);
    },
    lock({ W, H, finish, later, dot, rnd }) { // smart guns: three marks, lock them all
      const t0 = performance.now(); let n = 0;
      for (let i = 0; i < 3; i++) {
        const t = dot("qte-lock", rnd(0.14, 0.86) * W, rnd(0.12, 0.85) * H);
        t.addEventListener("pointerdown", (ev) => {
          ev.stopPropagation(); if (t.classList.contains("on")) return;
          t.classList.add("on"); hap("rigid"); n++;
          if (n === 3) { const dt = performance.now() - t0; finish(dt <= 1300 ? 2 : dt <= 2300 ? 1 : 0); }
        });
      }
      later(() => finish(0), 2800);
    },
    slice({ arena, W, H, finish, later, loop, rnd }) { // blades: targets fly up, swipe through them
      arena.style.touchAction = "none";
      const N = 3, things = []; let cut = 0, last = null, drag = false;
      for (let i = 0; i < N; i++) {
        const el2 = document.createElement("div"); el2.className = "qte-fruit"; arena.append(el2);
        things.push({ el: el2, x: rnd(0.2, 0.8) * W, y: H + 40, vx: rnd(-0.12, 0.12) * W, vy: -rnd(1.05, 1.3) * H, born: 150 + i * 330, cut: false, r: 34 });
      }
      const g = H * 1.6; let prev = performance.now(); const t0 = prev;
      loop((now) => {
        const dt = Math.min(0.05, (now - prev) / 1000); prev = now; let out = 0;
        for (const o of things) {
          if (now - t0 < o.born) { o.el.style.opacity = "0"; continue; }
          o.el.style.opacity = "1"; o.vy += g * dt; o.x += o.vx * dt; o.y += o.vy * dt;
          o.el.style.transform = "translate(" + (o.x - o.r) + "px," + (o.y - o.r) + "px)" + (o.cut ? " scale(1.25)" : "");
          if (o.y > H + 60 && o.vy > 0) out++;
        }
        if (out === N || cut === N) finish(cut >= N ? 2 : cut >= 2 ? 1 : 0);
      });
      const trail = (x, y) => { const d = document.createElement("i"); d.className = "qte-trail"; d.style.left = x + "px"; d.style.top = y + "px"; arena.append(d); setTimeout(() => d.remove(), 260); };
      const pt = (ev) => { const b = arena.getBoundingClientRect(); return { x: ev.clientX - b.left, y: ev.clientY - b.top }; };
      const seg = (a, b, o) => { const dx = b.x - a.x, dy = b.y - a.y, L = dx * dx + dy * dy || 1; const k = Math.max(0, Math.min(1, ((o.x - a.x) * dx + (o.y - a.y) * dy) / L)); return Math.hypot(a.x + k * dx - o.x, a.y + k * dy - o.y); };
      arena.addEventListener("pointerdown", (ev) => { drag = true; last = pt(ev); try { arena.setPointerCapture(ev.pointerId); } catch (e) {} });
      arena.addEventListener("pointermove", (ev) => {
        if (!drag) return; const p = pt(ev); trail(p.x, p.y);
        for (const o of things) if (!o.cut && o.y < H + 40 && seg(last, p, o) <= o.r + 6) { o.cut = true; cut++; o.el.classList.add("cut"); hap("rigid"); }
        last = p;
      });
      arena.addEventListener("pointerup", () => { drag = false; });
      later(() => finish(cut >= N ? 2 : cut >= 2 ? 1 : 0), 2900);
    },
    swing({ arena, finish, later, loop }) { // blunt: stop the swinging mark in the zone
      const zone = document.createElement("div"); zone.className = "qte-bar";
      zone.innerHTML = '<i class="z-good"></i><i class="z-perfect"></i>';
      const mark = document.createElement("b"); mark.className = "qte-mark"; zone.append(mark); arena.append(zone);
      const t0 = performance.now(), period = 1100; let pos = 0;
      loop((now) => { const ph = ((now - t0) % (period * 2)) / period; pos = ph <= 1 ? ph : 2 - ph; mark.style.left = (pos * 100) + "%"; });
      arena.addEventListener("pointerdown", () => { const d = Math.abs(pos - 0.5); finish(d <= 0.07 ? 2 : d <= 0.17 ? 1 : 0); });
      later(() => finish(0), 2300);
    },
    smash({ arena, W, H, finish, later }) { // fists: hit the shop window until it breaks
      const gw = Math.min(W * 0.84, 420), gh = Math.min(H * 0.62, 460);
      const glass = document.createElement("div"); glass.className = "qte-glass"; glass.style.width = gw + "px"; glass.style.height = gh + "px";
      glass.style.left = (W / 2 - gw / 2) + "px"; glass.style.top = (H / 2 - gh / 2) + "px";
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("viewBox", "0 0 " + gw + " " + gh); glass.append(svg);
      const meter = document.createElement("div"); meter.className = "qte-meter"; const fill = document.createElement("i"); meter.append(fill);
      arena.append(glass, meter);
      const NEED = 10; let n = 0;
      glass.addEventListener("pointerdown", (ev) => {
        ev.stopPropagation(); n++; hap(n % 3 ? "light" : "medium");
        const b = glass.getBoundingClientRect(), x = ev.clientX - b.left, y = ev.clientY - b.top;
        for (let i = 0; i < 4; i++) { // cracks from the hit
          const a = Math.random() * Math.PI * 2, L = 30 + Math.random() * 70 + n * 6;
          const l = document.createElementNS("http://www.w3.org/2000/svg", "path");
          l.setAttribute("d", "M" + x + " " + y + " l" + (Math.cos(a) * L * 0.5) + " " + (Math.sin(a) * L * 0.5) + " l" + (Math.cos(a + 0.4) * L * 0.5) + " " + (Math.sin(a + 0.4) * L * 0.5));
          svg.append(l);
        }
        glass.style.transform = "translate(" + ((Math.random() - 0.5) * 8) + "px," + ((Math.random() - 0.5) * 6) + "px)";
        fill.style.width = Math.min(100, n * 100 / NEED) + "%";
        if (n >= NEED) { glass.classList.add("shatter"); finish(2); }
      });
      later(() => finish(n >= NEED ? 2 : n >= 6 ? 1 : 0), 2200);
    },
  };

  // ---------- chrome on the screen (owner request 30.10, docs/PLAN_CHROME_FX.md) ----------
  // The bot lists the player's fresh chrome in a fight (screen.fx: {id, kind, word}, app/bot/combat_fx.py). The answer to
  // the player's own press plays each new one once: a themed layer over the app and a big title that slams in and recedes.
  const FIGHT_SCREENS = { cm: 1, cmab: 1, cmhack: 1, cmexit: 1, cmend: 1 };
  const fxSeen = new Set();
  function chromeFx(screen) {
    const list = screen && Array.isArray(screen.fx) ? screen.fx : [];
    if (screen && !FIGHT_SCREENS[screen.name] && !list.length) { fxSeen.clear(); return; } // a new fight counts from tick 1 again
    const fresh = list.filter((f) => f && f.id && !fxSeen.has(f.id));
    fresh.forEach((f) => fxSeen.add(f.id));
    if (!st.fxArm) return;
    fresh.forEach((f, i) => setTimeout(() => playFx(String(f.kind || "chrome"), String(f.word || "")), i * 750));
  }
  const FX_HAPTIC = { blackwall: ["heavy", "rigid", "heavy"], litany: ["soft", "soft", "soft"], shock: ["rigid", "rigid"], fire: ["medium"], berserk: ["heavy", "heavy", "heavy"], blast: ["heavy", "medium"], sandy: ["soft", "soft"], kerenzikov: ["rigid"], cloak: ["light"], emp: ["rigid", "light"] };
  function playFx(kind, word) {
    const box = document.createElement("div"); box.className = "cfx cfx-" + kind; box.setAttribute("aria-hidden", "true");
    const bg = document.createElement("div"); bg.className = "cfx-bg"; box.append(bg);
    if (kind === "sandy" || kind === "kerenzikov") {
      for (let i = 0; i < 9; i++) { const l = document.createElement("i"); l.className = "cfx-line"; l.style.top = (6 + Math.random() * 88) + "%"; l.style.animationDelay = (Math.random() * 260) + "ms"; bg.append(l); }
    }
    const PARTS = { smoke: 10, swarm: 26, blast: 10, emp: 6, shock: 7, virus: 30, fire: 22 };
    if (PARTS[kind]) {
      const wide = kind === "emp" || kind === "shock";
      for (let i = 0; i < PARTS[kind]; i++) {
        const p = document.createElement("i"); p.className = "cfx-p";
        if (kind === "fire") { // embers rise from the bottom
          p.style.left = (Math.random() * 100) + "%"; p.style.top = (85 + Math.random() * 15) + "%";
          p.style.setProperty("--dx", ((Math.random() - 0.5) * 20) + "vw"); p.style.setProperty("--dy", (-40 - Math.random() * 50) + "vh");
        } else {
          p.style.left = (50 + (Math.random() - 0.5) * (wide ? 90 : 30)) + "%"; p.style.top = (50 + (Math.random() - 0.5) * (wide ? 80 : 20)) + "%";
          p.style.setProperty("--dx", ((Math.random() - 0.5) * 120) + "vw"); p.style.setProperty("--dy", ((Math.random() - 0.5) * 90) + "vh");
        }
        p.style.animationDelay = (Math.random() * 220) + "ms"; bg.append(p);
      }
    }
    if (kind === "blackwall" || kind === "glitch" || kind === "jam") { // torn slices of the picture
      for (let i = 0; i < (kind === "blackwall" ? 8 : 5); i++) {
        const b = document.createElement("i"); b.className = "cfx-tear";
        b.style.top = (Math.random() * 96) + "%"; b.style.height = (2 + Math.random() * 9) + "%";
        b.style.animationDelay = (Math.random() * 500) + "ms"; bg.append(b);
      }
    }
    if (kind === "litany") { // the hymn spreads in rings
      for (let i = 0; i < 4; i++) { const r = document.createElement("i"); r.className = "cfx-wave"; r.style.animationDelay = (i * 220) + "ms"; bg.append(r); }
    }
    if (word) {
      let lines = [word];
      if (word.length > 12 && word.indexOf(" ") > 0) { // two lines, split at the space nearest the middle
        let cut = -1;
        for (let i = 0; i < word.length; i++) if (word[i] === " " && (cut < 0 || Math.abs(i - word.length / 2) < Math.abs(cut - word.length / 2))) cut = i;
        lines = [word.slice(0, cut), word.slice(cut + 1)];
      }
      const longest = Math.max(...lines.map((x) => x.length));
      const w = document.createElement("div"); w.className = "cfx-word"; w.textContent = lines.join("\n");
      const size = "min(120px, " + Math.max(6, Math.floor(88 / (longest * 0.8))) + "vw)"; // the whole title fits the width
      w.style.fontSize = size;
      word = lines.join("\n");
      if (kind === "sandy") { // after-images trailing the title
        ["c1", "c2", "c3"].forEach((c) => { const g = document.createElement("div"); g.className = "cfx-word cfx-ghost " + c; g.style.fontSize = size; g.textContent = word; box.append(g); });
      }
      box.append(w);
    }
    document.body.append(box);
    if (kind === "sandy") { el.screen.classList.add("cfx-slow"); setTimeout(() => el.screen.classList.remove("cfx-slow"), 1500); }
    if (kind === "blackwall") { el.screen.classList.add("cfx-void"); setTimeout(() => el.screen.classList.remove("cfx-void"), 1500); }
    if (kind === "berserk" || kind === "blast" || kind === "blackwall") { el.screen.classList.add("cfx-shake"); setTimeout(() => el.screen.classList.remove("cfx-shake"), 520); }
    (FX_HAPTIC[kind] || ["medium"]).forEach((h, i) => setTimeout(() => { try { if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred(h); } catch (e) {} }, i * 170));
    setTimeout(() => box.remove(), 2200);
  }

  // ---------- start ----------
  if (!INIT) {
    el.text.innerHTML = '<div class="notice"><h2>Открой из Telegram</h2>Это окно игры Night City Life. Команда /app в группе или кнопка «Найт-Сити» в личке бота.</div>';
    setPill("off", "Нет входа");
    el.tabs.querySelectorAll(".tab").forEach((b) => { b.disabled = true; });
    return;
  }
  el.pill.addEventListener("click", () => { refresh(); flush(); });
  back3dShow(); // a way back into the 3D district survives a reload of the app
  window.addEventListener("online", () => {
    if (ops.length && window.NCFeedback) window.NCFeedback.toast("Связь вернулась, отправляю.", "info");
    pill(); flush(); refresh();
  });
  window.addEventListener("offline", pill);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { refresh(); flush(); } });
  setInterval(() => { if (!st.sseOpen) refresh(); }, POLL_MS);

  drawJournal();
  render(); pill();
  if (ops.length && window.NCFeedback) window.NCFeedback.busy(true); // an action saved on the phone from the last time
  refresh().then(() => { connect(); flush(); });
})();
