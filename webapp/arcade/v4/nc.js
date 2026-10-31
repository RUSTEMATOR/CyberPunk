/* Night City Life arcade bridge (owner request 29.10, docs/PLAN_ARCADE_WEB.md).
   Included by every open-source game in webapp/arcade/<game>/. Reads the player's signed token from #t=...,
   sends the score of a finished run to the bot (POST /api/arcade/score), shows the answer, adds a way back,
   and gives keyboard-only games an on-screen pad. No game logic here. */
(function () {
  "use strict";
  var me = document.currentScript;
  var CORNER = (me && me.getAttribute("data-bar")) || "bl";  // where «← Найт-Сити» sits: tl tr bl br bc
  var SHORT = !!(me && me.getAttribute("data-short"));  // a bare arrow where the title sits close to the corner
  var EMBED = window.parent !== window;  // inside the Mini App's overlay (webapp/app.js): the app draws the way back
  var KEY = "nc-arcade-t:" + location.pathname;
  var hash = new URLSearchParams(location.hash.replace(/^#/, ""));
  var T = hash.get("t") || "";
  try {
    if (T) sessionStorage.setItem(KEY, T); else T = sessionStorage.getItem(KEY) || "";
  } catch (e) { /* storage off: the token lives as long as the hash */ }
  if (T && location.hash) {  // keep the token out of the address a player might copy
    try { history.replaceState(null, "", location.pathname + location.search); } catch (e) {}
  }

  var css = document.createElement("style");
  css.textContent =
    "#nc-bar{position:fixed;" + (CORNER.charAt(0) === "t" ? "top:8px;" : "bottom:8px;") +
    (CORNER.charAt(1) === "l" ? "left:8px;" : CORNER.charAt(1) === "c" ? "left:50%;transform:translateX(-50%);" : "right:8px;") +
    "z-index:2147483646;display:flex;gap:6px;font:600 13px/1 system-ui,sans-serif}" +
    "#nc-bar button{background:#0D0E12;color:#FFD23F;border:1px solid #FFD23F;border-radius:6px;padding:7px 10px;opacity:.85}" +
    "#nc-toast{position:fixed;left:50%;top:48px;transform:translateX(-50%);z-index:2147483647;max-width:86vw;background:#0D0E12;" +
    "color:#F2F2F2;border:1px solid #FFD23F;border-radius:8px;padding:10px 14px;font:14px/1.35 system-ui,sans-serif;" +
    "box-shadow:0 6px 24px rgba(0,0,0,.5);text-align:center;transition:opacity .3s}" +
    "#nc-pad{position:fixed;left:0;right:0;bottom:10px;z-index:2147483645;display:flex;justify-content:space-between;" +
    "align-items:flex-end;padding:0 12px;pointer-events:none;user-select:none;-webkit-user-select:none}" +
    "#nc-pad .grp{display:grid;grid-template-columns:repeat(3,56px);grid-template-rows:repeat(2,56px);gap:6px;pointer-events:auto}" +
    "#nc-pad .act{display:flex;flex-direction:column;gap:8px;pointer-events:auto}" +
    "#nc-pad b{display:flex;align-items:center;justify-content:center;background:rgba(13,14,18,.72);color:#FFD23F;" +
    "border:1px solid rgba(255,210,63,.6);border-radius:10px;font:600 22px/1 system-ui,sans-serif;touch-action:none}" +
    "#nc-pad .act b{width:76px;height:56px;font-size:14px}";
  document.head.appendChild(css);

  var toastEl = null, toastTimer = 0;
  function toast(text, ms) {
    if (!document.body) return;
    if (!toastEl) { toastEl = document.createElement("div"); toastEl.id = "nc-toast"; document.body.appendChild(toastEl); }
    toastEl.textContent = text; toastEl.style.opacity = "1"; toastEl.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.style.opacity = "0"; setTimeout(function () { toastEl.style.display = "none"; }, 300); }, ms || 4000);
  }

  function back() {
    if (EMBED) { try { window.parent.postMessage({ nc: "close" }, location.origin); return; } catch (e) { /* fall through */ } }
    var tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.close) { tg.close(); return; }
    if (history.length > 1) history.back(); else window.close();
  }

  var last = { s: -1, at: 0 };
  function score(s) {
    s = Math.max(0, Math.floor(Number(s) || 0));
    var now = Date.now();
    if (s === last.s && now - last.at < 5000) return;  // the same run reported twice
    last = { s: s, at: now };
    if (!T) { toast("Счёт " + s + ". Открой игру из «Аркады» в боте, чтобы он записался."); return; }
    fetch("/api/arcade/score", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ t: T, s: s }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.status === "ok") {
          var parts = ["Счёт " + d.score + "."];
          if (d.record) parts.push("Новый рекорд!");
          if (d.paid) parts.push("+" + d.paid + " эдди персонажу.");
          else if (d.capped) parts.push("Лимит наград аркады на сегодня исчерпан.");
          else if (d.soon) parts.push("Награда не чаще раза в 20 секунд.");
          toast(parts.join(" "), 5000);
        } else if (d.status === "auth") toast("Ссылка устарела. Открой игру заново из «Аркады» в боте.");
        else if (d.status === "dead") toast("Счёт " + s + ". Живого персонажа нет, награда не начислена.");
        else toast("Счёт " + s + ". Бот не ответил, попробуй позже.");
      })
      .catch(function () { toast("Счёт " + s + ". Нет связи с ботом."); });
  }

  function fireKey(type, code) {
    var names = { 37: "ArrowLeft", 38: "ArrowUp", 39: "ArrowRight", 40: "ArrowDown", 32: " ", 13: "Enter" };
    var ev = document.createEvent("Event");
    ev.initEvent(type, true, true);
    Object.defineProperty(ev, "keyCode", { get: function () { return code; } });
    Object.defineProperty(ev, "which", { get: function () { return code; } });
    Object.defineProperty(ev, "key", { get: function () { return names[code] || String.fromCharCode(code); } });
    (document.activeElement || document.body || document).dispatchEvent(ev);
  }

  // pad({left:37, up:38, right:39, down:40, actions:[{label:"Старт", key:78}]})
  function pad(map) {
    function make() {
      var root = document.createElement("div"); root.id = "nc-pad";
      var grp = document.createElement("div"); grp.className = "grp";
      var cells = [null, ["up", "↑"], null, ["left", "←"], ["down", "↓"], ["right", "→"]];
      cells.forEach(function (c) {
        var b = document.createElement("b");
        if (!c || map[c[0]] == null) { b.style.visibility = "hidden"; grp.appendChild(b); return; }
        b.textContent = c[1]; bind(b, map[c[0]], map.repeat); grp.appendChild(b);
      });
      var act = document.createElement("div"); act.className = "act";
      (map.actions || []).forEach(function (a) {
        var b = document.createElement("b"); b.textContent = a.label; bind(b, a.key, false); act.appendChild(b);
      });
      root.appendChild(grp); root.appendChild(act); document.body.appendChild(root);
    }
    function bind(el, code, repeat) {
      var t = 0;
      function down(e) { e.preventDefault(); fireKey("keydown", code); if (repeat) t = setInterval(function () { fireKey("keydown", code); }, 140); }
      function up(e) { e.preventDefault(); clearInterval(t); fireKey("keyup", code); }
      el.addEventListener("pointerdown", down); el.addEventListener("pointerup", up);
      el.addEventListener("pointercancel", up); el.addEventListener("pointerleave", up);
    }
    if (document.body) make(); else document.addEventListener("DOMContentLoaded", make);
  }

  function bar() {
    if (EMBED) return;  // the overlay has its own «← Найт-Сити»
    var b = document.createElement("div"); b.id = "nc-bar";
    var x = document.createElement("button"); x.textContent = SHORT ? "←" : "← Найт-Сити"; x.title = "Назад в Найт-Сити"; x.addEventListener("click", back);
    b.appendChild(x); document.body.appendChild(b);
  }
  if (document.body) bar(); else document.addEventListener("DOMContentLoaded", bar);

  try {  // canvas games draw text in these fonts: a canvas alone does not make the browser load them
    ["600 16px \"NC Title\"", "500 16px \"NC Mono\"", "500 16px \"NC Text\"", "16px \"NC Pixel\""].forEach(function (f) {
      document.fonts.load(f).catch(function () {});
    });
  } catch (e) {}

  window.NC = { score: score, pad: pad, toast: toast, back: back, key: fireKey };
})();
