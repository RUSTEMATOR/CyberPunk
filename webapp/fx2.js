/* W16 «Живой текст»: text that arrives, emoji that move, interference where the place is dangerous, weather over the
 * district, sparks on a big win, and the iOS-like glass (glass.css). Libraries: webapp/vendor/fx-vendor2.js
 * (GSAP SplitText, PowerGlitch, canvas-confetti; source fx-vendor2.src.js) and, loaded only when a moving emoji is on
 * screen, webapp/vendor/lottie-light.min.js (lottie-web). GSAP itself is the one from fx-vendor.js (window.NCVendor).
 * Everything is optional: without the vendor files, on a weak phone (lowfx) or with "reduce motion" the app looks as in
 * W15. Nothing here touches game data; the DOM the app built is changed only for the time of an animation. */
(() => {
  "use strict";
  const GLASS_KEY = "nc.glass";
  const root = document.documentElement;
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const V = () => window.NCVendor || null;
  const V2 = () => window.NCVendor2 || null;
  const calm = () => reduce || root.classList.contains("lowfx");
  const on = () => !calm() && !!V() && !!V2();

  // ---------- glass (glass.css): on by default, a switch in Персонаж → Сводка ----------
  let glass = true;
  try { glass = localStorage.getItem(GLASS_KEY) !== "0"; } catch (e) {}
  root.classList.toggle("glass", glass);
  function setGlass(v) {
    glass = !!v; root.classList.toggle("glass", glass);
    try { localStorage.setItem(GLASS_KEY, glass ? "1" : "0"); } catch (e) {}
  }

  // ---------- SplitText registered on the GSAP of fx-vendor.js ----------
  let splitReady = false;
  function split() {
    const v2 = V2(), v = V();
    if (!v2 || !v || !v2.SplitText) return null;
    if (!splitReady) { try { v.gsap.registerPlugin(v2.SplitText); splitReady = true; } catch (e) { return null; } }
    return v2.SplitText;
  }

  // ---------- media inside a text that was restored from HTML: videos muted again, emoji animations rebuilt ----------
  function fixMedia(box) {
    box.querySelectorAll("video.ce-video").forEach((v) => { try { v.muted = true; const p = v.play(); if (p && p.catch) p.catch(() => {}); } catch (e) {} });
    box.querySelectorAll(".ce-lottie[data-ce]").forEach((sp) => { const img = stillFor(sp.dataset.ce, sp.getAttribute("aria-label") || ""); sp.replaceWith(img); lottie(img); });
    box.querySelectorAll("img.ce[data-lottie]").forEach((img) => { delete img.dataset.lottie; lottie(img); });
    box.querySelectorAll("img.ce[data-wired]").forEach((img) => { delete img.dataset.wired; });
    if (window.NCApp && window.NCApp.rewire) { try { window.NCApp.rewire(); } catch (e) {} }
  }

  // ---------- the text of a screen arrives line by line (called by fx.js on entering a screen) ----------
  const DICE_RE = /(кубик )(\d+)/;
  const SUM_RE = /(= )(\d+)(, нужно )(\d+)/;
  function markNumbers(box) {
    // the rolled check line: the die and the total roll like a counter (views.check_line)
    const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
    const hits = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) if (/кубик \d+/.test(n.nodeValue) && /нужно \d+/.test(n.nodeValue)) hits.push(n);
    const out = [];
    for (const n of hits) {
      const frag = document.createDocumentFragment();
      let s = n.nodeValue;
      const wrap = (re) => {
        const m = re.exec(s); if (!m) return;
        frag.append(document.createTextNode(s.slice(0, m.index) + m[1]));
        const sp = document.createElement("span"); sp.className = "fx-roll"; sp.textContent = m[2]; frag.append(sp); out.push(sp);
        s = s.slice(m.index + m[1].length + m[2].length);
      };
      wrap(DICE_RE); wrap(SUM_RE);
      frag.append(document.createTextNode(s));
      n.replaceWith(frag);
    }
    return out;
  }
  function outcome(box) {
    const b = Array.from(box.querySelectorAll("b")).find((x) => /^(Успех|Провал)\.?$/.test(x.textContent.trim()));
    if (!b) return null;
    const crit = /Критический успех/.test(box.textContent);
    return { b, ok: /^Успех/.test(b.textContent.trim()), crit };
  }
  function afterText(box) {
    // after the lines: the die rolls, then the verdict lands like a stamp, a critical success throws sparks
    if (!on() || !box.isConnected) return;
    const g = V().gsap;
    const rolls = Array.from(box.querySelectorAll(".fx-roll"));
    const res = outcome(box);
    let t = 0;
    rolls.forEach((sp) => {
      const final = sp.textContent;
      g.to(sp, { duration: 0.45, delay: t, scrambleText: { text: final, chars: "0123456789", speed: 1 } });
      t += 0.12;
    });
    if (res) {
      res.b.classList.add("fx-stamp", res.ok ? "ok" : "fail");
      g.fromTo(res.b, { scale: 1.9, rotation: res.ok ? -7 : 6, opacity: 0 },
        { scale: 1, rotation: 0, opacity: 1, duration: 0.28, delay: t + 0.25, ease: "back.out(2.2)", clearProps: "transform,opacity",
          onStart: () => { if (!res.ok && window.NCFx) window.NCFx.error(); },
          onComplete: () => { if (res.ok && res.crit) sparks(res.b, "crit"); } });
    }
  }
  // The split in progress. GSAP SplitText reverts an earlier split of the same element when a new one is made,
  // and a revert writes back the html it saw at split time. When the app has drawn a new screen into the box since,
  // that is the previous screen's text: drop the old split and keep what the box shows now.
  let active = null;
  function forget(box) {
    if (!active) return;
    const a = active; active = null;
    const fresh = a.box.innerHTML;
    const stale = !a.st.lines || !a.st.lines.length || !a.st.lines[0].isConnected;
    try { a.st.revert(); } catch (e) {}
    if (stale) a.box.innerHTML = fresh;
  }
  function text(box, theme, tl) {
    if (!on() || !box || box.hidden || curName === "npcchat") return false; // a talk types its last answer instead
    if (theme === "net") { markNumbers(box); setTimeout(() => afterText(box), 650); return false; } // the net keeps its line printer (W13)
    if (box.querySelector("blockquote, pre, .lv-map, .lv-breach, .lv-birth, .lv-duel")) {
      // owner report 28.10: splitting into lines cloned a quote once per line (several boxes that merged into one).
      // A screen with a quote or a block of its own comes in whole: the text fades up, each quote unfolds from the top.
      markNumbers(box);
      const quotes = Array.from(box.querySelectorAll("blockquote, pre")).slice(0, 6);
      tl.fromTo(box, { opacity: 0, y: 6 }, { opacity: 1, y: 0, duration: 0.3, ease: "power2.out", clearProps: "opacity,transform" }, 0.1);
      if (quotes.length) tl.fromTo(quotes, { clipPath: "inset(0 0 100% 0)" }, { clipPath: "inset(0 0 0% 0)", duration: 0.36,
        ease: theme === "under" || theme === "alert" ? "steps(5)" : "power3.out", stagger: 0.06, clearProps: "clipPath" }, 0.12);
      tl.call(() => afterText(box), null, 0.5);
      return true;
    }
    const S = split(); if (!S) return false;
    markNumbers(box);
    forget(box);  // owner report 28.10: a new split of the same box reverts the old one and brings back the old screen's text
    let st;
    try { st = S.create(box, { type: "lines", mask: "lines", linesClass: "fx-line" }); } catch (e) { return false; }
    active = { box, st };
    const lines = st.lines || [];
    if (!lines.length) { try { st.revert(); } catch (e) {} return false; }
    const first = lines[0];
    const done = () => {
      if (first.isConnected && box.contains(first)) {  // a redrawn box keeps the split registered: forget() handles it later
        if (active && active.st === st) active = null;
        try { st.revert(); } catch (e) {} fixMedia(box);
      }
      afterText(box);
    };
    const shown = lines.slice(0, 16);
    const stagger = Math.min(0.045, 0.42 / shown.length);
    tl.fromTo(shown, { yPercent: 105, opacity: 0 }, { yPercent: 0, opacity: 1, duration: 0.32, ease: theme === "under" || theme === "alert" ? "steps(5)" : "power3.out", stagger, onComplete: done }, 0.1);
    return true;
  }

  // ---------- an NPC answer is typed out (screen npcchat) ----------
  let lastNpc = "";
  function npcType(s) {
    if (!s || s.name !== "npcchat") { lastNpc = ""; return; }
    const box = document.getElementById("text"); if (!box) return;
    const names = Array.from(box.querySelectorAll("b")).filter((b) => /:\s*$/.test(b.textContent));
    const b = names[names.length - 1]; if (!b) return;
    const nodes = [];
    for (let n = b.nextSibling; n && !(n.nodeType === 1 && (n.tagName === "BR" || n.tagName === "I" || n.tagName === "B")); n = n.nextSibling) if (n.nodeType === 3) nodes.push(n);
    const full = nodes.map((n) => n.nodeValue).join("");
    const sig = b.textContent + full;
    const changed = sig !== lastNpc;
    lastNpc = sig;
    if (!changed || !on() || !full.trim()) return;
    const parts = nodes.map((n) => n.nodeValue);
    nodes.forEach((n) => { n.nodeValue = ""; });
    const o = { n: 0 };
    const cursor = document.createElement("span"); cursor.className = "fx-caret"; cursor.setAttribute("aria-hidden", "true");
    (nodes[nodes.length - 1] || b).after(cursor);
    V().gsap.to(o, { n: full.length, duration: Math.min(2.2, 0.25 + full.length * 0.018), ease: "none",
      onUpdate: () => { let left = Math.round(o.n); nodes.forEach((n, i) => { const p = parts[i]; n.nodeValue = p.slice(0, Math.max(0, left)); left -= p.length; }); },
      onComplete: () => { nodes.forEach((n, i) => { n.nodeValue = parts[i]; }); cursor.remove(); } });
  }

  // ---------- interference where the place is dangerous (PowerGlitch) ----------
  const HEAVY = /^(bw|possess|avatar|psylegend|cmaniac|engram|btears|tearz|death|deathoffer|deathback|dghost)$/;
  const LIGHT = /^(cm|cmab|cmhack|cmend|cmexit|exitchk|pbfight|plduel|plattack|pboss|purge|wanted)$/;
  let glitchOn = null;
  function glitchScreen(name) {
    if (glitchOn) { try { glitchOn.stopGlitch(); } catch (e) {} glitchOn = null; }
    if (!on() || !V2().PowerGlitch) return;
    const heavy = HEAVY.test(name || ""), light = LIGHT.test(name || "");
    if (!heavy && !light) return;
    const img = document.querySelector("#hero:not([hidden]) .hero-img");
    const title = document.querySelector("#hero:not([hidden]) .hero-t") || document.querySelector("#title:not([hidden])");
    if (title) { title.classList.remove("fx-split"); void title.offsetWidth; title.classList.add("fx-split"); setTimeout(() => title.classList.remove("fx-split"), heavy ? 900 : 500); }
    if (!img) return;
    // PowerGlitch stacks the picture and its copies in a grid: give them a box of the picture's size first
    let box = img.parentElement;
    if (!box.classList.contains("fx-gbox")) { box = document.createElement("div"); box.className = "fx-gbox"; img.before(box); box.append(img); }
    try {
      glitchOn = V2().PowerGlitch.glitch(img, heavy ? {
        playMode: "always", hideOverflow: true,
        timing: { duration: 4200, iterations: Infinity }, glitchTimeSpan: { start: 0.05, end: 0.22 },
        shake: { velocity: 14, amplitudeX: 0.06, amplitudeY: 0.04 },
        slice: { count: 7, velocity: 16, minHeight: 0.02, maxHeight: 0.14, hueRotate: true },
      } : {
        playMode: "manual", hideOverflow: true,
        timing: { duration: 650, iterations: 1 }, glitchTimeSpan: { start: 0, end: 1 },
        shake: { velocity: 18, amplitudeX: 0.04, amplitudeY: 0.03 },
        slice: { count: 5, velocity: 18, minHeight: 0.02, maxHeight: 0.12, hueRotate: false },
      });
      if (light) { glitchOn.startGlitch(); const g = glitchOn; setTimeout(() => { try { g.stopGlitch(); } catch (e) {} }, 700); }
    } catch (e) { glitchOn = null; }
  }

  // ---------- weather over the district picture (hud.weather, the same line as in the bot) ----------
  const WEATHER = [
    [/Кислотный дождь/, "acid"], [/Гроза/, "storm"], [/Дождь/, "rain"], [/Снег/, "snow"],
    [/Пыльная буря/, "dust"], [/Смог/, "smog"], [/Туман/, "fog"],
  ];
  let sky = null;
  function weather(env, screenName) {
    const h = env && env.hud;
    const hero = document.getElementById("hero");
    const kind = h && h.weather && (screenName === "menu" || screenName === "city") ? (WEATHER.find(([re]) => re.test(h.weather)) || [])[1] : null;
    if (!kind || calm() || !hero || hero.hidden) { stopSky(); return; }
    if (sky && sky.kind === kind && sky.cv.isConnected && hero.contains(sky.cv)) return;
    stopSky();
    const cv = document.createElement("canvas"); cv.className = "fx-sky"; cv.setAttribute("aria-hidden", "true");
    const tint = hero.querySelector(".hero-tint"); (tint || hero.firstChild) ? (tint || hero.firstChild).after(cv) : hero.append(cv);
    sky = { kind, cv, cx: cv.getContext("2d"), parts: [], raf: 0, flash: 0, w: 0, h: 0, dpr: Math.min(2, window.devicePixelRatio || 1) };
    seed(sky);
    sky.raf = requestAnimationFrame(skyTick);
  }
  function stopSky() { if (!sky) return; cancelAnimationFrame(sky.raf); sky.cv.remove(); sky = null; }
  function seed(s) {
    const r = Math.random;
    const n = { rain: 70, acid: 60, storm: 90, snow: 46, dust: 70, smog: 9, fog: 7 }[s.kind] || 40;
    s.parts = Array.from({ length: n }, () => ({ x: r(), y: r(), z: 0.4 + r() * 0.6, p: r() * Math.PI * 2 }));
  }
  function skyTick(now) {
    const s = sky; if (!s) return;
    if (!s.cv.isConnected) { stopSky(); return; }
    s.raf = requestAnimationFrame(skyTick);
    if (document.hidden) return;
    const w = s.cv.clientWidth, h = s.cv.clientHeight;
    if (w !== s.w || h !== s.h) { s.w = w; s.h = h; s.cv.width = Math.round(w * s.dpr); s.cv.height = Math.round(h * s.dpr); }
    const c = s.cx; c.setTransform(s.dpr, 0, 0, s.dpr, 0, 0); c.clearRect(0, 0, w, h);
    const k = s.kind;
    if (k === "rain" || k === "acid" || k === "storm") {
      c.strokeStyle = k === "acid" ? "rgba(190,255,90,.55)" : "rgba(200,220,255,.42)"; c.lineWidth = 1;
      c.beginPath();
      for (const p of s.parts) {
        p.y += 0.018 * p.z * (k === "storm" ? 1.5 : 1); p.x -= 0.004 * p.z * (k === "storm" ? 2 : 1);
        if (p.y > 1.05) { p.y = -0.05; p.x = Math.random() * 1.2; }
        const x = p.x * w, y = p.y * h, len = 10 + 12 * p.z;
        c.moveTo(x, y); c.lineTo(x - len * (k === "storm" ? 0.35 : 0.18), y + len);
      }
      c.stroke();
      if (k === "storm") {
        if (s.flash <= 0 && Math.random() < 0.004) s.flash = 1;
        if (s.flash > 0) { c.fillStyle = "rgba(220,230,255," + (0.35 * s.flash) + ")"; c.fillRect(0, 0, w, h); s.flash -= 0.08; }
      }
    } else if (k === "snow") {
      c.fillStyle = "rgba(255,255,255,.8)";
      for (const p of s.parts) {
        p.y += 0.0025 * p.z; p.p += 0.02; const x = (p.x + Math.sin(p.p) * 0.01) * w;
        if (p.y > 1.02) { p.y = -0.02; p.x = Math.random(); }
        c.beginPath(); c.arc(x, p.y * h, 1 + 1.4 * p.z, 0, Math.PI * 2); c.fill();
      }
    } else if (k === "dust") {
      c.fillStyle = "rgba(214,170,110,.55)";
      for (const p of s.parts) {
        p.x += 0.006 * p.z; p.p += 0.05; if (p.x > 1.05) { p.x = -0.05; p.y = Math.random(); }
        c.fillRect(p.x * w, (p.y + Math.sin(p.p) * 0.01) * h, 1.5 * p.z + 0.5, 1.2);
      }
      c.fillStyle = "rgba(170,120,60,.12)"; c.fillRect(0, 0, w, h);
    } else { // smog and fog: slow pools of haze
      const col = k === "smog" ? "150,140,110" : "200,210,220";
      for (const p of s.parts) {
        p.x += 0.0006 * p.z; if (p.x > 1.3) p.x = -0.3;
        const x = p.x * w, y = (0.35 + p.y * 0.65) * h, r = (0.35 + 0.35 * p.z) * Math.max(w, h) * 0.6;
        const g = c.createRadialGradient(x, y, 0, x, y, r);
        g.addColorStop(0, "rgba(" + col + "," + (k === "smog" ? 0.16 : 0.2) + ")"); g.addColorStop(1, "rgba(" + col + ",0)");
        c.fillStyle = g; c.fillRect(0, 0, w, h);
      }
    }
  }

  // ---------- sparks (canvas-confetti): a big win, a critical success ----------
  let sparkCv = null, sparkFire = null;
  function sparkGun() {
    if (sparkFire) return sparkFire;
    const v2 = V2(); if (!v2 || !v2.confetti) return null;
    sparkCv = document.createElement("canvas"); sparkCv.className = "fx-sparks"; sparkCv.setAttribute("aria-hidden", "true");
    document.body.append(sparkCv);
    sparkFire = v2.confetti.create(sparkCv, { resize: true, useWorker: false, disableForReducedMotion: true });
    return sparkFire;
  }
  function cssVar(n, f) { return getComputedStyle(root).getPropertyValue(n).trim() || f; }
  function sparks(fromEl, kind) {
    if (!on()) return;
    const fire = sparkGun(); if (!fire) return;
    const r = fromEl && fromEl.getBoundingClientRect ? fromEl.getBoundingClientRect() : null;
    const origin = r ? { x: (r.left + r.width / 2) / innerWidth, y: (r.top + r.height / 2) / innerHeight } : { x: 0.85, y: 0.08 };
    const colors = [cssVar("--acc2", "#FFB547"), cssVar("--acc", "#FF3E8A"), "#FFFFFF"];
    const money = kind === "money";
    fire({ particleCount: money ? 70 : 48, spread: money ? 70 : 100, startVelocity: money ? 32 : 26, gravity: 1.1, ticks: 120, scalar: 0.8,
      angle: money ? 240 : 90, origin, colors, shapes: money ? ["square"] : ["square", "circle"], disableForReducedMotion: true });
  }
  let lastCash = null, lastName = null;
  function money(h) {
    if (!h) { lastCash = null; return; }
    if (lastName === h.name && lastCash != null && h.eddies != null) {
      const d = h.eddies - lastCash;
      if (d >= Math.max(50000, Math.abs(lastCash) * 0.1)) sparks(document.getElementById("cash"), "money");
    }
    lastName = h.name; lastCash = h.eddies;
  }

  // ---------- animated premium emoji (.tgs, lottie-web) ----------
  let lottieLib = null, lottieLoading = null;
  const LOTTIE_MAX = 60;
  const anims = new Set();
  const canLottie = () => !calm() && typeof DecompressionStream === "function";
  function loadLottie() {
    if (lottieLib) return Promise.resolve(lottieLib);
    if (!lottieLoading) {
      lottieLoading = new Promise((ok, bad) => {
        const s = document.createElement("script"); s.src = "/static/vendor/lottie-light.min.js?v=5.13.0"; s.async = true;
        s.onload = () => { lottieLib = window.lottie || window.bodymovin || null; lottieLib ? ok(lottieLib) : bad(new Error("no lottie")); };
        s.onerror = () => bad(new Error("lottie not loaded"));
        document.head.append(s);
      });
      lottieLoading.catch(() => { lottieLoading = null; });
    }
    return lottieLoading;
  }
  const tgsCache = new Map();
  function tgs(id) {
    if (!tgsCache.has(id)) {
      tgsCache.set(id, fetch("/emoji/" + id + ".tgs").then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.blob(); })
        .then((b) => new Response(b.stream().pipeThrough(new DecompressionStream("gzip"))).text()));
      tgsCache.get(id).catch(() => tgsCache.delete(id));
    }
    return tgsCache.get(id);
  }
  function stillFor(id, alt) {
    const img = document.createElement("img");
    img.className = "ce"; img.src = "/emoji/" + id; img.alt = alt || ""; img.dataset.ce = id;
    img.width = 20; img.height = 20; img.style.width = "1.25em"; img.style.height = "1.25em"; img.style.verticalAlign = "-0.28em";
    return img;
  }
  const seen = "IntersectionObserver" in window ? new IntersectionObserver((es) => {
    for (const e of es) { const a = e.target._ncAnim; if (!a) continue; try { e.isIntersecting ? a.play() : a.pause(); } catch (x) {} }
  }) : null;
  function sweep() {
    for (const a of anims) if (!a._ncBox.isConnected) { try { a.destroy(); } catch (e) {} if (seen) seen.unobserve(a._ncBox); anims.delete(a); }
  }
  function lottie(img) {
    if (!canLottie() || !img || img.dataset.lottie) return false;
    img.dataset.lottie = "1";
    const id = img.dataset.ce;
    Promise.all([loadLottie(), tgs(id)]).then(([lib, json]) => {
      sweep();
      if (!img.isConnected || anims.size >= LOTTIE_MAX) return;
      const box = document.createElement("span");
      box.className = "ce-lottie " + img.className.replace(/\bce\b/, "").trim(); box.dataset.ce = id;
      box.setAttribute("role", "img"); box.setAttribute("aria-label", img.alt || "");
      box.style.cssText = img.style.cssText; box.style.display = "inline-block";
      img.replaceWith(box);
      try {
        const a = lib.loadAnimation({ container: box, renderer: "svg", loop: true, autoplay: true, animationData: JSON.parse(json),
          rendererSettings: { preserveAspectRatio: "xMidYMid meet", progressiveLoad: true } });
        a._ncBox = box; box._ncAnim = a; anims.add(a);
        if (seen) seen.observe(box);
      } catch (e) { box.replaceWith(img); }
    }).catch(() => {});
    return true;
  }

  // ---------- the hook app.js calls after every render ----------
  let lastKey = null, curName = "";
  function after(ctx) {
    sweep();
    const env = ctx.env, s = env && env.screen;
    curName = ctx.tab === "win" && s ? s.name || "" : "";
    money(env && env.hud);
    if (ctx.tab !== "win" || !s) { stopSky(); lastKey = ctx.tab; return; }
    const key = [s.name, s.title || "", s.closed ? "c" : ""].join("|");
    const changed = key !== lastKey; lastKey = key;
    setTimeout(() => {
      try { weather(env, s.name); } catch (e) {}
      if (changed && !ctx.busy) { try { glitchScreen(s.name); } catch (e) {} }
      try { npcType(s); } catch (e) {}
    }, 0);
  }

  window.NCFx2 = { after, text, lottie, canLottie, setGlass, glassOn: () => glass, sparks };
})();
