/* «Оптика Кироши»: motion and sound of the Mini App (docs/WEBAPP.md W13).
 * Libraries come from webapp/vendor/fx-vendor.js (GSAP + ScrambleText, Arwes frames and bleeps, Motion);
 * the clipped corners of rough themes are augmented-ui (webapp/vendor/augmented-ui.min.css).
 * app.js calls NCFx after every render; without the vendor file, on a weak phone or with reduced motion
 * everything here is skipped and the app looks exactly as without it. Nothing here touches game data. */
(() => {
  "use strict";
  const SFX_KEY = "nc.sfx";
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const V = () => window.NCVendor || null;
  const on = () => !reduce && !document.documentElement.classList.contains("lowfx") && !!V();
  const nf = new Intl.NumberFormat("ru-RU");

  // what each place's optics does on entering a screen
  const OPTICS = {
    neon:   { tag: "район",        scan: 0.45, ease: "power2.out", chars: "upperCase", scramble: true },
    under:  { tag: "подполье",     scan: 0.5,  ease: "steps(7)",   chars: "upperCase", scramble: true, flicker: true },
    clinic: { tag: "клиника",      scan: 0.7,  ease: "sine.inOut", scramble: false },
    net:    { tag: "узел сети",    scan: 0.5,  ease: "steps(10)",  chars: "0123456789ABCDEF", scramble: true, print: true },
    corp:   { tag: "корпорация",   scan: 0.6,  ease: "sine.out",   scramble: false },
    alert:  { tag: "угроза",       scan: 0.3,  ease: "power3.out", chars: "upperCase", scramble: true },
    bar:    { tag: "бар фиксера",  scan: 0.5,  ease: "power2.out", chars: "upperCase", scramble: true },
    jail:   { tag: "тюрьма",       scan: 0.6,  ease: "sine.out",   scramble: false },
    death:  { tag: "кладбище",     scan: 0.8,  ease: "sine.inOut", scramble: false },
  };
  const ROUGH = new Set(["under", "alert"]);

  // ---------- sound (Arwes bleeps), off by default ----------
  let sfxOn = false, bleeps = null;
  try { sfxOn = localStorage.getItem(SFX_KEY) === "1"; } catch (e) {}
  function manager() {
    if (bleeps || !V()) return bleeps;
    const src = (n) => ({ sources: [{ src: "/static/sfx/" + n + ".wav", type: "audio/wav" }] });
    try {
      bleeps = V().createBleepsManager({
        master: { volume: 0.7 }, common: { muteOnWindowBlur: true, preload: true },
        bleeps: { click: { ...src("click"), volume: 0.35 }, scan: { ...src("scan"), volume: 0.3 }, lock: { ...src("lock"), volume: 0.3 }, error: { ...src("error"), volume: 0.45 } },
      });
    } catch (e) { bleeps = null; }
    return bleeps;
  }
  function play(name) {
    if (!sfxOn) return;
    const m = manager();
    try { m && m.bleeps[name] && m.bleeps[name].play(); } catch (e) {}
  }
  function setSound(v) {
    sfxOn = !!v;
    try { localStorage.setItem(SFX_KEY, sfxOn ? "1" : "0"); } catch (e) {}
    if (sfxOn) { manager(); setTimeout(() => play("click"), 60); }
  }

  // ---------- numbers under the top bar ----------
  let deltas = null;
  function deltaBox() {
    if (!deltas) { deltas = document.createElement("div"); deltas.className = "fx-deltas"; deltas.setAttribute("aria-live", "polite"); document.body.append(deltas); }
    return deltas;
  }
  function showDelta(text, good) {
    const x = document.createElement("div");
    x.className = "fx-delta " + (good ? "up" : "down"); x.textContent = text;
    deltaBox().append(x);
    if (!on()) { setTimeout(() => x.remove(), 1800); return; }
    const g = V().gsap;
    g.fromTo(x, { opacity: 0, y: -6 }, { opacity: 1, y: 0, duration: 0.18, ease: "power2.out" });
    g.to(x, { opacity: 0, y: -4, delay: 1.6, duration: 0.3, onComplete: () => x.remove() });
  }
  const signed = (n) => (n > 0 ? "+" : "−") + nf.format(Math.abs(n));
  function roll(elm, from, to, fmt) {
    if (!elm || !on()) return;
    const o = { v: from };
    V().gsap.to(o, { v: to, duration: 0.6, ease: "power2.out", onUpdate: () => { elm.textContent = fmt(Math.round(o.v)); } });
  }
  let lastHud = null;
  function readouts(h) {
    const p = lastHud;
    lastHud = h ? { name: h.name, eddies: h.eddies, ap: h.ap, hp: h.hp, humanity: h.humanity, cred: h.cred } : null;
    if (!p || !h || p.name !== h.name) return;
    const d = (k) => (h[k] != null && p[k] != null ? h[k] - p[k] : 0);
    if (d("eddies")) { showDelta(signed(d("eddies")) + " €$", d("eddies") > 0); roll(document.getElementById("cash"), p.eddies, h.eddies, (v) => nf.format(v) + " €$"); }
    if (d("ap")) { showDelta(signed(d("ap")) + " ОД", d("ap") > 0); roll(document.getElementById("ap"), p.ap, h.ap, (v) => "ОД " + v); }
    if (d("hp")) showDelta(signed(d("hp")) + " здоровья", d("hp") > 0);
    if (d("humanity")) showDelta(signed(d("humanity")) + " человечности", d("humanity") > 0);
    if (d("cred")) showDelta(signed(d("cred")) + " репутации", d("cred") > 0);
    if (d("hp") < 0 || d("humanity") < 0) glitch();
  }

  // ---------- interference: only when something happened to the character ----------
  let glitchT = null;
  function glitch() {
    play("error");
    if (!on()) return;
    const root = document.documentElement;
    root.classList.remove("fx-glitch"); void root.offsetWidth; root.classList.add("fx-glitch");
    clearTimeout(glitchT); glitchT = setTimeout(() => root.classList.remove("fx-glitch"), 260);
  }

  // ---------- entering a screen ----------
  let lastKey = null, frame = null;
  function wrapTitleText(t) {
    if (!t) return null;
    const last = Array.from(t.childNodes).reverse().find((n) => n.nodeType === 3 && n.textContent.trim());
    if (!last) return null;
    const sp = document.createElement("span"); sp.className = "fx-t"; sp.textContent = last.textContent;
    last.replaceWith(sp);
    return sp;
  }
  function lockOn(hero, o, animate) {
    if (frame) { try { frame.remove(); } catch (e) {} frame = null; }
    hero.querySelectorAll(".fx-frame, .fx-tag").forEach((x) => x.remove());
    const v = V();
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "fx-frame"); svg.setAttribute("aria-hidden", "true");
    hero.append(svg);
    const tag = document.createElement("span"); tag.className = "fx-tag"; tag.textContent = o.tag; tag.setAttribute("aria-hidden", "true");
    hero.append(tag);
    try {
      frame = v.createFrame(svg, v.createFrameCornersSettings({ styled: true, animated: false, padding: 10, strokeWidth: 2, cornerLength: 22 }));
      if (animate) v.animateFrameAssembler({ element: svg, duration: 0.5, isEntering: true });
    } catch (e) { svg.remove(); }
    if (!animate) return;
    if (o.scramble) v.gsap.fromTo(tag, { opacity: 0 }, { opacity: 1, duration: 0.2, delay: 0.25, scrambleText: { text: o.tag, chars: o.chars, speed: 0.6 } });
    else v.gsap.fromTo(tag, { opacity: 0 }, { opacity: 1, duration: 0.4, delay: 0.3 });
  }
  function enter(theme) {
    const v = V(); const g = v.gsap; const o = OPTICS[theme] || OPTICS.neon;
    const hero = document.getElementById("hero");
    const text = document.getElementById("text");
    const tl = g.timeline({ defaults: { overwrite: "auto" } });
    let title = null;
    if (hero && !hero.hidden) {
      const img = hero.querySelector(".hero-img");
      if (img) tl.fromTo(img, { clipPath: "inset(0 0 100% 0)" }, { clipPath: "inset(0 0 0% 0)", duration: o.scan, ease: o.ease, clearProps: "clipPath" }, 0);
      if (o.flicker && img) tl.fromTo(img, { opacity: 0.4 }, { opacity: 1, duration: 0.24, ease: "steps(4)", repeat: 1, clearProps: "opacity" }, o.scan * 0.6);
      const line = document.createElement("div"); line.className = "fx-scan"; hero.append(line);
      tl.fromTo(line, { y: 0, opacity: 1 }, { y: () => hero.clientHeight, duration: o.scan, ease: o.ease }, 0)
        .to(line, { opacity: 0, duration: 0.15, onComplete: () => line.remove() }, o.scan);
      lockOn(hero, o, true);
      title = hero.querySelector(".hero-t");
    } else {
      const t = document.getElementById("title");
      if (t && !t.hidden) title = t;
    }
    const span = title ? wrapTitleText(title) : null;
    if (span && o.scramble) tl.to(span, { duration: 0.5, scrambleText: { text: span.textContent, chars: o.chars, speed: 0.7, revealDelay: 0.1 } }, 0.05);
    else if (title) tl.fromTo(title, { opacity: 0 }, { opacity: 1, duration: 0.35, clearProps: "opacity" }, 0.05);
    let lines = false;  // W16: fx2.js brings the text in line by line when it can
    if (text && !text.hidden && text.textContent.trim() && window.NCFx2) { try { lines = window.NCFx2.text(text, theme, tl); } catch (e) { lines = false; } }
    if (!lines && text && !text.hidden && text.textContent.trim()) {
      if (o.print) tl.fromTo(text, { clipPath: "inset(0 0 100% 0)" }, { clipPath: "inset(0 0 0% 0)", duration: 0.55, ease: "steps(12)", clearProps: "clipPath" }, 0.1);
      else tl.fromTo(text, { opacity: 0, y: 6 }, { opacity: 1, y: 0, duration: 0.3, ease: "power2.out", clearProps: "opacity,transform" }, 0.12);
    }
    const items = Array.from(document.querySelectorAll("#hudMain .vrow, #rows .btn, #tiles .btn, #rowsAfter .btn, #dock .btn")).slice(0, 18);
    if (items.length) tl.fromTo(items, { opacity: 0, y: 6 }, { opacity: 1, y: 0, duration: 0.22, ease: "power2.out", stagger: 0.022, clearProps: "opacity,transform" }, 0.15);
    play("scan");
    if (hero && !hero.hidden) setTimeout(() => play("lock"), 420);
  }

  // ---------- rough themes: clipped corners with a border on the cut (augmented-ui) ----------
  function shapes(theme) {
    const rough = ROUGH.has(theme);
    document.querySelectorAll(".view .btn:not(.tile)").forEach((b) => {
      if (rough) b.setAttribute("data-augmented-ui", "tl-clip br-clip border"); else b.removeAttribute("data-augmented-ui");
    });
  }

  // ---------- the hooks app.js calls ----------
  function after(ctx) {
    shapes(ctx.theme);
    readouts(ctx.env && ctx.env.hud);
    const s = ctx.env && ctx.env.screen;
    const key = ctx.tab === "win" && s ? [s.name, s.title || "", s.closed ? "c" : "", s.unknown ? "u" : ""].join("|") : ctx.tab;
    const changed = key !== lastKey;
    lastKey = key;
    if (ctx.tab !== "win" || !on()) return;
    if (changed && !ctx.busy) {
      // the app may render the same screen again in the same tick (the op answer, then the idle state): animate the last one
      clearTimeout(enterT);
      enterT = setTimeout(() => { try { enter(ctx.theme); } catch (e) { /* motion is optional */ } }, 0);
      return;
    }
    const hero = document.getElementById("hero");  // a redraw of the same screen keeps the lock-on corners, without motion
    if (hero && !hero.hidden && !hero.querySelector(".fx-frame")) {
      try { lockOn(hero, OPTICS[ctx.theme] || OPTICS.neon, false); } catch (e) {}
    }
  }
  let enterT = null;
  function tab(view) {
    play("click");
    if (!on() || !view) return;
    try { V().animate(view, { opacity: [0, 1], transform: ["translateY(8px)", "none"] }, { duration: 0.22, easing: "ease-out" }); } catch (e) {}
  }

  // ---------- code crawling from the touch (W15): circuit-like glyph traces on one canvas ----------
  const CODE = {
    neon: "0123456789ABCDEF<>/\\[]{}#*+=",
    under: "#%&@$*!?/\\|=+0123456789",
    clinic: "+-·0123456789",
    net: "0123456789ABCDEF",
    corp: "0123456789.:,",
    alert: "!#*/\\<>0123456789",
    bar: "0123456789$€#*+",
    jail: "|/\\-+0123456789",
    death: "·.:+-",
  };
  const STEP = 6, GLYPH_MS = 360, MAX = 320;  // owner request 28.10 «меньше размером»: was 10 / 420 / 520
  let cv = null, cx = null, crawlers = [], glyphs = [], raf = 0, dpr = 1;
  const calm = () => reduce || document.documentElement.classList.contains("lowfx");
  function canvas() {
    if (cv) return cv;
    cv = document.createElement("canvas"); cv.className = "fx-code"; cv.setAttribute("aria-hidden", "true");
    document.body.append(cv);
    cx = cv.getContext("2d");
    const size = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      cv.width = Math.round(innerWidth * dpr); cv.height = Math.round(innerHeight * dpr);
      cv.style.width = innerWidth + "px"; cv.style.height = innerHeight + "px";
    };
    size(); addEventListener("resize", size, { passive: true });
    return cv;
  }
  function themeVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  const DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1]];
  function burst(x, y, strong) {
    if (calm()) return;
    canvas();
    const t = (Array.from(document.documentElement.classList).find((c) => c.startsWith("t-")) || "t-neon").slice(2);
    const chars = CODE[t] || CODE.neon;
    const c1 = themeVar("--acc", "#FF3E8A"), c2 = themeVar("--acc2", "#FFB547");
    const n = strong ? 6 : 4;
    const now = performance.now();
    for (let i = 0; i < n; i++) {
      const d = DIRS[i % 4];
      crawlers.push({ x: x + d[0] * 3, y: y + d[1] * 3, dx: d[0], dy: d[1], left: (strong ? 7 : 4) + Math.floor(Math.random() * (strong ? 7 : 4)),
        next: now + i * 12, chars, color: Math.random() < 0.72 ? c1 : c2 });
    }
    if (!raf) raf = requestAnimationFrame(tick);
  }
  function tick(now) {
    raf = 0;
    for (const c of crawlers) {
      while (c.left > 0 && now >= c.next) {
        glyphs.push({ x: c.x, y: c.y, ch: c.chars[(Math.random() * c.chars.length) | 0], color: c.color, born: c.next });
        c.x += c.dx * STEP; c.y += c.dy * STEP; c.left -= 1; c.next += 16;
        if (Math.random() < 0.22) { const turn = Math.random() < 0.5 ? 1 : -1; [c.dx, c.dy] = [c.dy * turn, -c.dx * turn]; } // a trace bends like a circuit
      }
    }
    crawlers = crawlers.filter((c) => c.left > 0);
    glyphs = glyphs.filter((g) => now - g.born < GLYPH_MS).slice(-MAX);
    cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cx.clearRect(0, 0, innerWidth, innerHeight);
    cx.font = "500 7px 'Martian Mono', ui-monospace, monospace";
    cx.textAlign = "center"; cx.textBaseline = "middle";
    for (const g of glyphs) {
      const a = 1 - (now - g.born) / GLYPH_MS;
      cx.globalAlpha = Math.max(0, a) * 0.9;
      cx.fillStyle = g.color;
      if (Math.random() < 0.08) g.ch = "01"[(Math.random() * 2) | 0]; // glyphs flip while they live
      cx.fillText(g.ch, g.x, g.y);
    }
    cx.globalAlpha = 1;
    if (crawlers.length || glyphs.length) raf = requestAnimationFrame(tick);
    else cx.clearRect(0, 0, innerWidth, innerHeight);
  }
  document.addEventListener("pointerdown", (ev) => {
    if (ev.button > 0) return;
    const strong = !!(ev.target.closest && ev.target.closest(".btn, .tab, .seg button, .send, .chat-send, a"));
    burst(ev.clientX, ev.clientY, strong);
  }, { passive: true });

  window.NCFx = { after, tab, click: () => play("click"), error: glitch, setSound, soundOn: () => sfxOn };
})();
