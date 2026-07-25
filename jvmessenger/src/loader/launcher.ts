/**
 * The floating launcher injected into the host page. Rendered inside a Shadow
 * DOM so the customer's site CSS cannot bleed in and our styles cannot leak out.
 *
 * Owns three things, all of which live *outside* the chat iframe so they work
 * before the panel has ever been opened:
 *  - the launcher button (avatar or glyph) + unread badge;
 *  - an optional **teaser**: a labelled pill, a short greeting card, and an
 *    inline mini-composer so a visitor can start typing without opening the
 *    panel first;
 *  - the entrance/attention animations, all gated on `prefers-reduced-motion`.
 *
 * The chat itself remains an iframe managed by the host bridge.
 */

export interface Launcher {
  /** Shadow host element appended to the document body. */
  host: HTMLElement;
  /** Container the host bridge mounts the iframe wrapper into. */
  mount: HTMLElement;
  /** Toggle the launcher button's open/closed visual state. */
  setOpen(open: boolean): void;
  /** Set the unread-count badge (0 hides it). */
  setUnread(count: number): void;
  /** Reveal the teaser card. No-op when dismissed or not configured. */
  showTeaser(text?: string): void;
  /** Hide the teaser card (without recording a dismissal). */
  hideTeaser(): void;
  /** Remove the launcher from the page. */
  destroy(): void;
}

const LAUNCHER_SVG = `
<svg viewBox="0 0 24 24" width="26" height="26" fill="none"
     stroke="currentColor" stroke-width="2" stroke-linecap="round"
     stroke-linejoin="round" aria-hidden="true">
  <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
</svg>`;

const CLOSE_SVG = `
<svg viewBox="0 0 24 24" width="24" height="24" fill="none"
     stroke="currentColor" stroke-width="2" stroke-linecap="round"
     stroke-linejoin="round" aria-hidden="true">
  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
</svg>`;

const X_SVG = `
<svg viewBox="0 0 24 24" width="16" height="16" fill="none"
     stroke="currentColor" stroke-width="2.2" stroke-linecap="round"
     stroke-linejoin="round" aria-hidden="true">
  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
</svg>`;

const SEND_SVG = `
<svg viewBox="0 0 24 24" width="16" height="16" fill="none"
     stroke="currentColor" stroke-width="2.2" stroke-linecap="round"
     stroke-linejoin="round" aria-hidden="true">
  <line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>
</svg>`;

const STYLE = `
:host { all: initial; }
.wrap { position: fixed; bottom: 20px; right: 20px; z-index: 2147483000;
        font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
        display: flex; flex-direction: column; align-items: flex-end; gap: 12px; }

/* ── Teaser ─────────────────────────────────────────────────────────── */
.teaser { display: none; flex-direction: column; align-items: stretch; gap: 8px;
          width: min(300px, calc(100vw - 40px)); }
.teaser[data-show="1"] { display: flex; animation: teaserIn .42s cubic-bezier(.16,1,.3,1) both; }
.teaser[data-hiding="1"] { animation: teaserOut .18s ease-in both; }

.teaserTop { display: flex; align-items: center; gap: 8px; justify-content: flex-end; }
.pill { display: inline-flex; align-items: center; gap: 8px; background: #fff;
        color: #18181b; border-radius: 999px; padding: 6px 12px 6px 6px;
        box-shadow: 0 6px 20px rgba(0,0,0,.13), 0 1px 3px rgba(0,0,0,.07);
        font-size: 13px; font-weight: 600; max-width: 210px; }
.pill img, .pill .glyph { width: 24px; height: 24px; border-radius: 999px; flex: none;
                          object-fit: cover; display: flex; align-items: center;
                          justify-content: center; background: #18181b; color: #fff; }
.pill span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dismiss { width: 26px; height: 26px; flex: none; border: none; cursor: pointer;
           border-radius: 999px; background: #fff; color: #52525b; display: flex;
           align-items: center; justify-content: center;
           box-shadow: 0 4px 14px rgba(0,0,0,.13); transition: background .15s ease, color .15s ease; }
.dismiss:hover { background: #f4f4f5; color: #18181b; }

.card { background: #fff; color: #18181b; border-radius: 16px; overflow: hidden;
        box-shadow: 0 10px 34px rgba(0,0,0,.16), 0 2px 6px rgba(0,0,0,.07); }
.msg { padding: 14px 16px; font-size: 14px; line-height: 1.5; cursor: pointer; }
.msg:hover { background: #fafafa; }
.composer { display: flex; align-items: center; gap: 8px; padding: 8px 8px 8px 14px;
            border-top: 1px solid #ececf0; }
.composer input { flex: 1; border: none; outline: none; background: transparent;
                  font: inherit; font-size: 14px; color: #18181b; min-width: 0; }
.composer input::placeholder { color: #9b9ba4; }
.send { width: 34px; height: 34px; flex: none; border: none; border-radius: 999px;
        background: #ececf0; color: #52525b; cursor: pointer; display: flex;
        align-items: center; justify-content: center;
        transition: background .15s ease, color .15s ease, transform .15s ease; }
.send:hover { transform: scale(1.06); }
.send[data-active="1"] { background: #18181b; color: #fff; }

/* ── Launcher button ────────────────────────────────────────────────── */
.btn { width: 58px; height: 58px; border-radius: 12px; border: none; cursor: pointer;
       background: #18181b; color: #fafafa; display: flex; align-items: center;
       justify-content: center; box-shadow: 0 8px 28px rgba(0,0,0,.22);
       transition: transform .15s ease, background .15s ease; position: relative; }
.btn:hover { transform: scale(1.05); background: #27272a; }
.btn:active { transform: scale(.97); }
.btn img { width: 58px; height: 58px; border-radius: 12px; object-fit: cover; }
.btn[data-attention="1"] { animation: pop .5s cubic-bezier(.16,1,.3,1) both, breathe 3.6s ease-in-out 1s infinite; }
.badge { position: absolute; top: -2px; right: -2px; min-width: 20px; height: 20px;
         padding: 0 5px; border-radius: 10px; background: #ef4444; color: #fff;
         font-size: 12px; font-weight: 700; display: none; align-items: center;
         justify-content: center; box-sizing: border-box; }
.badge[data-show="1"] { display: flex; animation: pop .32s cubic-bezier(.16,1,.3,1) both; }
.mount { position: fixed; inset: auto 20px 20px auto; z-index: 2147483001; }

@keyframes teaserIn  { from { opacity: 0; transform: translateY(14px) scale(.96); }
                       to   { opacity: 1; transform: none; } }
@keyframes teaserOut { to { opacity: 0; transform: translateY(8px) scale(.98); } }
@keyframes pop       { from { transform: scale(0); } 60% { transform: scale(1.18); }
                       to { transform: scale(1); } }
@keyframes breathe   { 0%,100% { box-shadow: 0 8px 28px rgba(0,0,0,.22); }
                       50% { box-shadow: 0 8px 28px rgba(0,0,0,.22), 0 0 0 10px rgba(24,24,27,.06); } }

@media (prefers-color-scheme: dark) {
  .pill, .dismiss, .card { background: #1f1f23; color: #fafafa; }
  .dismiss { color: #a1a1aa; }
  .dismiss:hover { background: #2a2a30; color: #fafafa; }
  .msg:hover { background: #26262b; }
  .composer { border-top-color: #303036; }
  .composer input { color: #fafafa; }
  .send { background: #303036; color: #d4d4d8; }
  .send[data-active="1"] { background: #fafafa; color: #18181b; }
}

@media (prefers-reduced-motion: reduce) {
  .btn, .send, .dismiss { transition: none; }
  .teaser[data-show="1"], .teaser[data-hiding="1"],
  .btn[data-attention="1"], .badge[data-show="1"] { animation: none; }
}
`;

export function createLauncher(opts: {
  avatar?: string;
  /** Name shown on the teaser pill (falls back to a neutral label). */
  title?: string;
  /** Greeting shown in the teaser card. Absent ⇒ the teaser never shows. */
  teaser?: string;
  onToggle: () => void;
  /** Fired when the visitor sends from the teaser's mini-composer. */
  onTeaserSend?: (text: string) => void;
  /** Fired when the visitor dismisses the teaser (persist a cooldown). */
  onTeaserDismiss?: () => void;
}): Launcher {
  const host = document.createElement("div");
  host.setAttribute("data-jvmessenger", "launcher");
  const shadow = host.attachShadow({ mode: "open" });

  const style = document.createElement("style");
  style.textContent = STYLE;

  const wrap = document.createElement("div");
  wrap.className = "wrap";

  // ── Teaser ───────────────────────────────────────────────────────────
  const teaser = document.createElement("div");
  teaser.className = "teaser";

  const teaserTop = document.createElement("div");
  teaserTop.className = "teaserTop";

  const pill = document.createElement("div");
  pill.className = "pill";
  if (opts.avatar) {
    const img = document.createElement("img");
    img.src = opts.avatar;
    img.alt = "";
    pill.appendChild(img);
  } else {
    const glyph = document.createElement("span");
    glyph.className = "glyph";
    glyph.innerHTML = LAUNCHER_SVG.replace('width="26" height="26"', 'width="14" height="14"');
    pill.appendChild(glyph);
  }
  const pillName = document.createElement("span");
  pillName.textContent = opts.title || "Chat";
  pill.appendChild(pillName);

  const dismissBtn = document.createElement("button");
  dismissBtn.className = "dismiss";
  dismissBtn.type = "button";
  dismissBtn.setAttribute("aria-label", "Dismiss");
  dismissBtn.innerHTML = X_SVG;

  teaserTop.append(pill, dismissBtn);

  const card = document.createElement("div");
  card.className = "card";

  const msg = document.createElement("div");
  msg.className = "msg";
  msg.setAttribute("role", "button");
  msg.setAttribute("tabindex", "0");
  msg.textContent = opts.teaser || "";

  const composer = document.createElement("div");
  composer.className = "composer";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "Write a message...";
  input.setAttribute("aria-label", "Write a message");
  const sendBtn = document.createElement("button");
  sendBtn.className = "send";
  sendBtn.type = "button";
  sendBtn.setAttribute("aria-label", "Send message");
  sendBtn.innerHTML = SEND_SVG;
  composer.append(input, sendBtn);

  card.append(msg, composer);
  teaser.append(teaserTop, card);

  // ── Launcher button ──────────────────────────────────────────────────
  const btn = document.createElement("button");
  btn.className = "btn";
  btn.setAttribute("aria-label", "Open chat");
  btn.type = "button";

  const badge = document.createElement("span");
  badge.className = "badge";

  const renderClosed = () => {
    btn.innerHTML = "";
    if (opts.avatar) {
      const img = document.createElement("img");
      img.src = opts.avatar;
      img.alt = "";
      btn.appendChild(img);
    } else {
      btn.innerHTML = LAUNCHER_SVG;
    }
    btn.appendChild(badge);
    btn.setAttribute("aria-label", "Open chat");
  };
  const renderOpen = () => {
    btn.innerHTML = CLOSE_SVG;
    btn.setAttribute("aria-label", "Close chat");
  };
  renderClosed();

  // ── Behaviour ────────────────────────────────────────────────────────
  let dismissed = false;

  const hideTeaser = () => {
    if (teaser.getAttribute("data-show") !== "1") return;
    teaser.setAttribute("data-hiding", "1");
    window.setTimeout(() => {
      teaser.removeAttribute("data-show");
      teaser.removeAttribute("data-hiding");
    }, 180);
  };

  const showTeaser = (text?: string) => {
    if (dismissed) return;
    const body = text ?? opts.teaser;
    if (!body) return;
    msg.textContent = body;
    teaser.setAttribute("data-show", "1");
    btn.setAttribute("data-attention", "1");
  };

  const submitTeaser = () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    sendBtn.removeAttribute("data-active");
    hideTeaser();
    opts.onTeaserSend?.(text);
  };

  input.addEventListener("input", () => {
    if (input.value.trim()) sendBtn.setAttribute("data-active", "1");
    else sendBtn.removeAttribute("data-active");
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submitTeaser();
    }
  });
  sendBtn.addEventListener("click", submitTeaser);

  // Tapping the greeting itself just opens the chat.
  const openFromTeaser = () => {
    hideTeaser();
    opts.onToggle();
  };
  msg.addEventListener("click", openFromTeaser);
  msg.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openFromTeaser();
    }
  });

  dismissBtn.addEventListener("click", () => {
    dismissed = true;
    hideTeaser();
    opts.onTeaserDismiss?.();
  });

  btn.addEventListener("click", () => {
    hideTeaser();
    btn.removeAttribute("data-attention");
    opts.onToggle();
  });

  wrap.append(teaser, btn);

  const mount = document.createElement("div");
  mount.className = "mount";

  shadow.append(style, wrap, mount);
  document.body.appendChild(host);

  return {
    host,
    mount,
    setOpen(open: boolean) {
      if (open) {
        renderOpen();
        hideTeaser();
        btn.removeAttribute("data-attention");
      } else {
        renderClosed();
      }
    },
    setUnread(count: number) {
      if (count > 0) {
        badge.textContent = count > 99 ? "99+" : String(count);
        badge.setAttribute("data-show", "1");
      } else {
        badge.removeAttribute("data-show");
      }
    },
    showTeaser,
    hideTeaser,
    destroy() {
      host.remove();
    },
  };
}
