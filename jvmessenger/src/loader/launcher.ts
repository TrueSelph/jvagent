/**
 * The floating launcher button injected into the host page. Rendered inside a
 * Shadow DOM so the customer's site CSS cannot bleed in and our styles cannot
 * leak out. Owns only the button + an unread badge; the chat itself lives in an
 * iframe managed by the host bridge.
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

const STYLE = `
:host { all: initial; }
.wrap { position: fixed; bottom: 20px; right: 20px; z-index: 2147483000;
        font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
.btn { width: 58px; height: 58px; border-radius: 12px; border: none; cursor: pointer;
       background: #18181b; color: #fafafa; display: flex; align-items: center;
       justify-content: center; box-shadow: 0 8px 28px rgba(0,0,0,.22);
       transition: transform .15s ease, background .15s ease; position: relative; }
.btn:hover { transform: scale(1.05); background: #27272a; }
.btn:active { transform: scale(.97); }
.btn img { width: 58px; height: 58px; border-radius: 12px; object-fit: cover; }
.badge { position: absolute; top: -2px; right: -2px; min-width: 20px; height: 20px;
         padding: 0 5px; border-radius: 10px; background: #ef4444; color: #fff;
         font-size: 12px; font-weight: 700; display: none; align-items: center;
         justify-content: center; box-sizing: border-box; }
.badge[data-show="1"] { display: flex; }
.mount { position: fixed; inset: auto 20px 20px auto; z-index: 2147483001; }
@media (prefers-reduced-motion: reduce) { .btn { transition: none; } }
`;

export function createLauncher(opts: {
  avatar?: string;
  onToggle: () => void;
}): Launcher {
  const host = document.createElement("div");
  host.setAttribute("data-jvmessenger", "launcher");
  const shadow = host.attachShadow({ mode: "open" });

  const style = document.createElement("style");
  style.textContent = STYLE;

  const wrap = document.createElement("div");
  wrap.className = "wrap";

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

  btn.addEventListener("click", opts.onToggle);
  wrap.appendChild(btn);

  const mount = document.createElement("div");
  mount.className = "mount";

  shadow.append(style, wrap, mount);
  document.body.appendChild(host);

  return {
    host,
    mount,
    setOpen(open: boolean) {
      if (open) renderOpen();
      else renderClosed();
    },
    setUnread(count: number) {
      if (count > 0) {
        badge.textContent = count > 99 ? "99+" : String(count);
        badge.setAttribute("data-show", "1");
      } else {
        badge.removeAttribute("data-show");
      }
    },
    destroy() {
      host.remove();
    },
  };
}
