import { describe, it, expect } from "vitest";
import { envelope, readEnvelope, PROTOCOL_SOURCE } from "./protocol";
import { parseConfig } from "./config";

describe("protocol envelope", () => {
  it("round-trips a valid message", () => {
    const env = envelope({ type: "ready" });
    expect(env.source).toBe(PROTOCOL_SOURCE);
    expect(readEnvelope(env)?.type).toBe("ready");
  });

  it("rejects foreign / malformed payloads", () => {
    expect(readEnvelope(null)).toBeNull();
    expect(readEnvelope({ source: "other", v: 1, message: {} })).toBeNull();
    expect(readEnvelope({ source: PROTOCOL_SOURCE, v: 99, message: {} })).toBeNull();
    expect(readEnvelope({ source: PROTOCOL_SOURCE, v: 1 })).toBeNull();
  });
});

describe("parseConfig (data-* contract)", () => {
  const base = { agentUrl: "https://a.host/", agentId: "ag1" };

  it("requires agent url + id", () => {
    expect(() => parseConfig({} as DOMStringMap)).toThrow();
  });

  it("applies defaults + strips trailing slash", () => {
    const c = parseConfig({ ...base } as unknown as DOMStringMap);
    expect(c.agentUrl).toBe("https://a.host");
    expect(c.showReasoning).toBe(false); // masked by default
    expect(c.fullscreen).toBe(true);
    expect(c.attachments).toBe(false);
    expect(c.quickReplies).toEqual([]);
  });

  it("parses booleans, theme, and quick replies (JSON + CSV)", () => {
    const c = parseConfig({
      ...base,
      showReasoning: "true",
      voice: "1",
      theme: "dark",
      quickReplies: '["A","B"]',
    } as unknown as DOMStringMap);
    expect(c.showReasoning).toBe(true);
    expect(c.voice).toBe(true);
    expect(c.theme).toBe("dark");
    expect(c.quickReplies).toEqual(["A", "B"]);

    const csv = parseConfig({
      ...base,
      quickReplies: "One, Two ,Three",
    } as unknown as DOMStringMap);
    expect(csv.quickReplies).toEqual(["One", "Two", "Three"]);
  });
});
