/**
 * Tiny WebAudio notification chime for incoming assistant messages. Synthesized
 * (two soft sine notes) rather than a bundled audio file, so there is no asset
 * to load and nothing for the iframe CSP to block.
 *
 * Browser autoplay policy: audio needs a prior user gesture. {@link primeAudio}
 * is called on send (a gesture) to unlock/resume the context; {@link playChime}
 * then plays when the reply lands.
 */

let ctx: AudioContext | null = null;

function getCtx(): AudioContext | null {
  if (typeof window === "undefined") return null;
  const AC =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext;
  if (!AC) return null;
  if (!ctx) {
    try {
      ctx = new AC();
    } catch {
      return null;
    }
  }
  return ctx;
}

/** Unlock/resume the audio context from within a user gesture (e.g. send). */
export function primeAudio(): void {
  const c = getCtx();
  if (c && c.state === "suspended") c.resume().catch(() => {});
}

/** Play a short, soft two-note "ding" for an incoming assistant message. */
export function playChime(): void {
  const c = getCtx();
  if (!c) return;
  if (c.state === "suspended") c.resume().catch(() => {});
  const now = c.currentTime;
  // Two ascending sine notes with a fast exponential decay — quiet and brief.
  const notes = [
    { freq: 660, at: 0 },
    { freq: 880, at: 0.08 },
  ];
  for (const n of notes) {
    const osc = c.createOscillator();
    const gain = c.createGain();
    osc.type = "sine";
    osc.frequency.value = n.freq;
    const t0 = now + n.at;
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(0.05, t0 + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.18);
    osc.connect(gain).connect(c.destination);
    osc.start(t0);
    osc.stop(t0 + 0.2);
  }
}
