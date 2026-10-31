/* Отклик (docs/WEBAPP.md W14): every touch answers at once, every result of an action is visible.
 * Press states on pointerdown, the busy line under the top bar, the slow hint, toasts above the tabs.
 * app.js calls NCFeedback; nothing here sends an action or reads game data. */
(() => {
  "use strict";
  const tg = window.Telegram && window.Telegram.WebApp;
  const root = document.documentElement;
  const impact = (k) => { try { tg && tg.HapticFeedback && tg.HapticFeedback.impactOccurred(k); } catch (e) {} };

  // ---------- touch: react on pointerdown, not on release ----------
  const PRESSABLE = ".btn, .tab, .seg button, .send, .pill, .chat-send";
  let pressed = null, pressT = 0;
  function release() {
    if (!pressed) return;
    const p = pressed; pressed = null;
    const left = Math.max(0, 110 - (Date.now() - pressT)); // a quick tap still shows the press
    setTimeout(() => p.classList.remove("is-pressed"), left);
  }
  document.addEventListener("pointerdown", (ev) => {
    const b = ev.target.closest && ev.target.closest(PRESSABLE);
    if (!b || b.disabled || b.getAttribute("aria-disabled") === "true") return;
    release();
    pressed = b; pressT = Date.now();
    b.classList.add("is-pressed");
    impact("light");
  }, { passive: true });
  ["pointerup", "pointercancel", "pointerleave", "dragstart"].forEach((t) => document.addEventListener(t, release, { passive: true }));
  document.addEventListener("touchstart", () => {}, { passive: true }); // :active on iOS

  // ---------- toasts above the tabs ----------
  let box = null;
  function toasts() {
    if (!box) { box = document.createElement("div"); box.className = "fb-toasts"; box.setAttribute("aria-live", "polite"); box.setAttribute("role", "status"); document.body.append(box); }
    return box;
  }
  const shown = new Map();
  function toast(text, kind, ms) {
    if (shown.has(text)) return; // the same words are on screen already
    const t = document.createElement("div");
    t.className = "fb-toast" + (kind ? " " + kind : ""); t.textContent = text;
    toasts().append(t);
    shown.set(text, t);
    requestAnimationFrame(() => t.classList.add("in"));
    setTimeout(() => { t.classList.remove("in"); setTimeout(() => { t.remove(); shown.delete(text); }, 250); }, ms || 2500);
    while (box.children.length > 3) { const f = box.firstChild; shown.forEach((v, k) => { if (v === f) shown.delete(k); }); f.remove(); }
  }

  // ---------- waiting for the bot ----------
  let slowT = null, longT = null;
  function busy(on) {
    root.classList.toggle("busy", !!on);
    clearTimeout(slowT); clearTimeout(longT);
    root.classList.remove("slow");
    if (!on) return;
    slowT = setTimeout(() => root.classList.add("slow"), 1200);
    longT = setTimeout(() => toast("Бот ещё думает. Действие не потеряется.", "info", 3500), 6000);
  }

  function result(status, changed) {
    if (status === "ok" || status === "duplicate") { if (changed) impact("soft"); return; }
    if (status === "stale") toast("Экран уже сменился, показываю текущий.", "info");
    else if (status === "closed") toast("Окно закрыто. Открой его здесь или командой /play.", "info");
    else if (status === "error") toast("Не получилось. Ошибка записана, попробуй ещё раз.", "bad", 3500);
  }

  window.NCFeedback = { toast, busy, result };
})();
