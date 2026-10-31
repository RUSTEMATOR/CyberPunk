/* The group chat inside the Mini App (owner request 25.10). Reads /api/chat, writes through the bot. */
(function () {
  "use strict";
  const tg = window.Telegram && window.Telegram.WebApp;
  const INIT = tg ? tg.initData : "";
  const ME = tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : 0;
  const OPEN_MS = 3000, CLOSED_MS = 20000;

  let last = 0, seen = 0, open = false, timer = null, replyTo = null, busy = false;
  const items = [];
  try { seen = parseInt(localStorage.getItem("ncl_chat_seen") || "0", 10) || 0; } catch (e) { seen = 0; }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  /* the button */
  const fab = el("button", "chat-fab");
  fab.type = "button";
  fab.setAttribute("aria-label", "Беседа");
  fab.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  const badge = el("span", "chat-badge");
  badge.hidden = true;
  fab.appendChild(badge);

  /* the panel */
  const panel = el("section", "chat-panel");
  panel.hidden = true;
  const head = el("header", "chat-head");
  const title = el("span", "chat-title", "Беседа");
  const close = el("button", "chat-close", "Закрыть");
  close.type = "button";
  head.append(title, close);
  const list = el("ol", "chat-list");
  const note = el("p", "chat-note", "Сообщения уходят в беседу от бота с твоим именем.");
  const replyBar = el("div", "chat-reply");
  replyBar.hidden = true;
  const replyText = el("span", "chat-reply-text");
  const replyX = el("button", "chat-reply-x", "×");
  replyX.type = "button";
  replyBar.append(replyText, replyX);
  const form = el("form", "chat-form");
  const input = el("input", "chat-input");
  input.type = "text"; input.maxLength = 1000; input.placeholder = "Написать в беседу"; input.autocomplete = "off";
  const send = el("button", "chat-send");
  send.type = "submit";
  send.setAttribute("aria-label", "Отправить");
  send.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>';
  form.append(input, send);
  panel.append(head, list, note, replyBar, form);

  // W12: the panel lives in the «Беседа» tab when the page has one; the floating button is the old way
  let embedded = false;
  const counters = [];
  function mount() {
    const host = document.getElementById("chatView");
    if (host) {
      embedded = true; panel.hidden = false; panel.classList.add("embedded"); close.hidden = true;
      host.appendChild(panel);
      return;
    }
    document.body.appendChild(fab);
    document.body.appendChild(panel);
  }
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);

  function hhmm(ts) {
    const d = new Date(ts * 1000);
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  function bubble(m) {
    const li = el("li", "chat-msg" + (m.uid && m.uid === ME ? " mine" : ""));
    li.dataset.id = m.id;
    const top = el("div", "chat-meta");
    top.appendChild(el("b", "chat-from", m.from || "Аноним"));
    if (m.user) top.appendChild(el("span", "chat-user", "@" + m.user));
    top.appendChild(el("span", "chat-time", hhmm(m.ts || 0)));
    li.appendChild(top);
    if (m.reply) {
      const q = el("div", "chat-quote");
      q.appendChild(el("b", "", m.reply.from || ""));
      q.appendChild(el("span", "", " " + (m.reply.text || "")));
      li.appendChild(q);
    }
    const kind = m.kind ? "[" + m.kind + (m.sticker ? " " + m.sticker : "") + "] " : "";
    const body = el("div", "chat-text", kind + (m.text || ""));
    li.appendChild(body);
    li.addEventListener("click", () => setReply(m));
    return li;
  }

  function nearBottom() {
    return list.scrollHeight - list.scrollTop - list.clientHeight < 80;
  }

  function render(newOnes) {
    const stick = nearBottom();
    newOnes.forEach((m) => list.appendChild(bubble(m)));
    while (list.children.length > 150) list.removeChild(list.firstChild);
    if (stick || newOnes.some((m) => m.uid === ME)) list.scrollTop = list.scrollHeight;
  }

  function markSeen() {
    if (!open) return;
    seen = last;
    try { localStorage.setItem("ncl_chat_seen", String(seen)); } catch (e) { /* private mode */ }
    badge.hidden = true;
  }

  function updateBadge() {
    const n = items.filter((m) => m.seq > seen && m.uid !== ME).length;
    badge.textContent = n > 99 ? "99+" : String(n);
    badge.hidden = open || n === 0;
    counters.forEach((fn) => { try { fn(open ? 0 : n); } catch (e) { /* the tab badge is optional */ } });
  }

  async function poll() {
    clearTimeout(timer);
    try {
      const r = await fetch("/api/chat?after=" + last, { headers: { "X-Init-Data": INIT }, cache: "no-store" });
      if (r.ok) {
        const d = await r.json();
        const fresh = (d.items || []).filter((m) => m.seq > last);
        if (fresh.length) {
          fresh.forEach((m) => items.push(m));
          while (items.length > 150) items.shift();
          last = Math.max(last, ...fresh.map((m) => m.seq));
          render(fresh);
        } else if (d.last < last) {
          last = 0; items.length = 0; list.innerHTML = "";  // the server restarted with an older counter
        }
        markSeen();
        updateBadge();
      }
    } catch (e) { /* offline: try again later */ }
    timer = setTimeout(poll, open ? OPEN_MS : CLOSED_MS);
  }

  function setReply(m) {
    replyTo = m;
    replyText.textContent = "Ответ: " + (m.from || "") + " — " + (m.text || m.kind || "").slice(0, 60);
    replyBar.hidden = false;
    input.focus();
  }
  replyX.addEventListener("click", () => { replyTo = null; replyBar.hidden = true; });

  function show(v) {
    open = v;
    if (!embedded) {
      panel.hidden = !v;
      fab.hidden = v;
      document.documentElement.classList.toggle("chat-open", v);
    }
    if (v) {
      list.scrollTop = list.scrollHeight;
      markSeen();
      poll();
    }
    updateBadge();
  }
  fab.addEventListener("click", () => show(true));
  close.addEventListener("click", () => show(false));

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const text = input.value.trim();
    if (!text || busy) return;
    busy = true; send.disabled = true;
    try {
      const r = await fetch("/api/chat", {
        method: "POST", cache: "no-store",
        headers: { "X-Init-Data": INIT, "Content-Type": "application/json" },
        body: JSON.stringify({ text: text, reply: replyTo ? replyTo.id : null }),
      });
      if (r.ok) {
        input.value = "";
        replyTo = null; replyBar.hidden = true;
        await poll();
      } else if (r.status === 429) {
        note.textContent = "Не так быстро.";
      } else {
        note.textContent = "Не отправилось. Попробуй ещё раз.";
      }
    } catch (e) {
      note.textContent = "Нет связи. Сообщение не ушло.";
    }
    setTimeout(() => { note.textContent = "Сообщения уходят в беседу от бота с твоим именем."; }, 4000);
    busy = false; send.disabled = false;
  });

  window.NCChat = { show: show, onCount: (fn) => { counters.push(fn); updateBadge(); } };
  poll();
})();
