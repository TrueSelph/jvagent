/** Parse a string for JSON viewer display; returns null if not JSON object/array text. */
export function tryParseJsonDisplay(s: string | undefined | null): unknown | null {
  if (s == null || typeof s !== "string") return null;
  const t = s.trim();
  if (t.length === 0) return null;
  const c = t[0];
  if (c !== "{" && c !== "[") return null;
  try {
    return JSON.parse(t) as unknown;
  } catch {
    return null;
  }
}
