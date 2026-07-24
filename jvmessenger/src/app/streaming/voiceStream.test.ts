import { describe, expect, it } from "vitest";
import { wsUrl } from "./voiceStreamClient";

describe("wsUrl", () => {
  it("maps http → ws and builds the stream path", () => {
    expect(wsUrl("http://localhost:8000", "n.Agent.1", "tok")).toBe(
      "ws://localhost:8000/api/agents/n.Agent.1/voice/stt/stream?token=tok"
    );
  });

  it("maps https → wss (secure token transport)", () => {
    expect(wsUrl("https://agent.example.com", "n.Agent.1", "tok")).toBe(
      "wss://agent.example.com/api/agents/n.Agent.1/voice/stt/stream?token=tok"
    );
  });

  it("strips trailing slashes and url-encodes the token", () => {
    expect(wsUrl("https://a.co/", "n.Agent.1", "a b/c+d")).toBe(
      "wss://a.co/api/agents/n.Agent.1/voice/stt/stream?token=a%20b%2Fc%2Bd"
    );
  });
});
