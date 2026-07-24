import { describe, it, expect } from "vitest";
import { parseSSEBuffer } from "./sseClient";

describe("parseSSEBuffer", () => {
  it("parses complete frames and returns the incomplete remainder", () => {
    const buf =
      'data: {"type":"start","session_id":"s1"}\n\n' +
      'data: {"type":"message","message":{"message_type":"stream_chunk","content":"Hi","category":"user"}}\n\n' +
      'data: {"type":"final"'; // incomplete
    const [chunks, rest] = parseSSEBuffer(buf);
    expect(chunks).toHaveLength(2);
    expect(chunks[0].type).toBe("start");
    expect(chunks[0].session_id).toBe("s1");
    expect(chunks[1].type).toBe("message");
    expect(rest).toContain('"final"');
  });

  it("ignores malformed frames without throwing", () => {
    const [chunks] = parseSSEBuffer("data: not-json\n\ndata: {}\n\n");
    expect(chunks).toHaveLength(1);
  });

  it("skips non-data lines", () => {
    const [chunks] = parseSSEBuffer(
      'event: ping\ndata: {"type":"final"}\n\n'
    );
    expect(chunks).toHaveLength(1);
    expect(chunks[0].type).toBe("final");
  });
});
