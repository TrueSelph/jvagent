import { afterEach, describe, expect, it, vi } from "vitest";

import { openSession } from "./session";

afterEach(() => vi.unstubAllGlobals());

describe("session open caller proof", () => {
  it("sends the existing capability when reopening a session", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      session_id: "session", user_id: "anonymous", session_token: "renewed",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const result = await openSession("https://agent.example", "agent", {
      sessionId: "session", userId: "anonymous", sessionToken: "current",
    });
    expect(fetcher).toHaveBeenCalledWith(
      "https://agent.example/api/agents/agent/interact/session/open",
      expect.objectContaining({
        headers: { "Content-Type": "application/json", "X-Session-Token": "current" },
      }),
    );
    expect(result?.sessionToken).toBe("renewed");
  });

  it("never falls back to an uncredentialed create after authentication fails", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetcher);
    expect(await openSession("https://agent.example", "agent", {
      sessionId: "old-session", sessionToken: "retired",
    })).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
