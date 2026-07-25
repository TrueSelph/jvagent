/**
 * Host-page context + behavioural signals, collected in the loader (which runs
 * in the customer's page and therefore actually has access to them).
 *
 * Two consumers:
 *  - the app, which forwards the context to the agent on each turn so replies
 *    can be tailored to where the visitor actually is;
 *  - the launcher, which uses the behavioural signals to decide when to offer
 *    the teaser.
 *
 * **Privacy:** query strings and hashes are deliberately dropped — they routinely
 * carry emails, tokens and order ids. Only origin + path + title + referrer
 * origin are reported, and nothing here is persisted beyond a visit counter.
 */

export interface PageContext {
  /** Page origin, e.g. `https://acme.com`. */
  origin: string;
  /** Path only — query and hash are stripped on purpose. */
  path: string;
  /** Document title at capture time. */
  title: string;
  /** Referrer *origin* only (never the full URL). */
  referrer?: string;
  /** Whole seconds since the messenger booted on this page. */
  secondsOnPage: number;
  /** Deepest scroll reached, 0-100. */
  scrollDepth: number;
  /** How many times this visitor has loaded a page with this agent embedded. */
  visitCount: number;
  /** True when this is not their first visit. */
  returning: boolean;
}

export interface BehaviourSignals {
  /** Stop watching and detach every listener. */
  destroy(): void;
  /** Current context snapshot. */
  snapshot(): PageContext;
}

/** Behavioural triggers the embed can opt into for the teaser. */
export type TriggerKind = "delay" | "scroll" | "exit" | "idle";

function safeOrigin(url: string): string | undefined {
  try {
    return new URL(url).origin;
  } catch {
    return undefined;
  }
}

function readVisitCount(agentId: string): number {
  const key = `jvmessenger:visits:${agentId}`;
  try {
    const next = Number(window.localStorage.getItem(key) || 0) + 1;
    window.localStorage.setItem(key, String(next));
    return next;
  } catch {
    return 1;
  }
}

/**
 * Start collecting page context and behavioural signals.
 *
 * `onTrigger` fires **at most once**, for whichever enabled trigger matches
 * first — the caller decides whether to act on it (e.g. show the teaser).
 */
export function watchPage(opts: {
  agentId: string;
  triggers: TriggerKind[];
  /** Seconds of dwell before the `delay`/`idle` triggers fire. */
  delaySeconds: number;
  /** Scroll percentage (0-100) that fires the `scroll` trigger. */
  scrollPercent: number;
  onTrigger?: (kind: TriggerKind) => void;
}): BehaviourSignals {
  const startedAt = Date.now();
  const visitCount = readVisitCount(opts.agentId);
  let scrollDepth = 0;
  let fired = false;
  const timers: number[] = [];

  const enabled = new Set(opts.triggers);

  const fire = (kind: TriggerKind) => {
    if (fired || !enabled.has(kind)) return;
    // Never interrupt someone filling in the host page's own form.
    const el = document.activeElement;
    const tag = el?.tagName?.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    fired = true;
    opts.onTrigger?.(kind);
  };

  const onScroll = () => {
    const doc = document.documentElement;
    const scrollable = doc.scrollHeight - window.innerHeight;
    const pct =
      scrollable > 0
        ? Math.min(100, Math.round((window.scrollY / scrollable) * 100))
        : 0;
    if (pct > scrollDepth) scrollDepth = pct;
    if (scrollDepth >= opts.scrollPercent) fire("scroll");
  };

  // Exit intent: pointer leaving through the top of the viewport, which is the
  // gesture toward the tab bar / address bar. Ignored on touch (no hover).
  const onMouseOut = (e: MouseEvent) => {
    if (e.relatedTarget) return; // moved to another element, not out of the page
    if (e.clientY > 40) return; // only the top edge reads as "leaving"
    fire("exit");
  };

  window.addEventListener("scroll", onScroll, { passive: true });
  document.addEventListener("mouseout", onMouseOut);

  if (enabled.has("delay") || enabled.has("idle")) {
    timers.push(
      window.setTimeout(
        () => fire(enabled.has("delay") ? "delay" : "idle"),
        Math.max(0, opts.delaySeconds) * 1000
      )
    );
  }

  return {
    destroy() {
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("mouseout", onMouseOut);
      timers.forEach((t) => window.clearTimeout(t));
    },
    snapshot(): PageContext {
      return {
        origin: window.location.origin,
        // Query + hash stripped: they carry PII far more often than signal.
        path: window.location.pathname,
        title: document.title,
        referrer: document.referrer ? safeOrigin(document.referrer) : undefined,
        secondsOnPage: Math.round((Date.now() - startedAt) / 1000),
        scrollDepth,
        visitCount,
        returning: visitCount > 1,
      };
    },
  };
}
