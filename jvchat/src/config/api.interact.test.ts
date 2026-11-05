import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./config", () => ({
  getJvagentUrl: () => "http://127.0.0.1:8130",
  getJvforgeUrl: () => "http://127.0.0.1:8088",
  getJvagentTimeout: () => 1000,
  getConfigAsync: async () => ({ jvagent: { url: "http://127.0.0.1:8130" }, jvforge: { url: "http://127.0.0.1:8088" } }),
}));

import { apiClient } from "./api";
import { getInteractSessionToken } from "../utils/storage";

describe("interaction request safety", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    localStorage.clear();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    apiClient.updateBaseUrl("http://127.0.0.1:8130");
  });

  it.each([false, true])("does not replay an admitted 422 (stream=%s)", async (stream) => {
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ error: { message: "utterance exceeds maximum length" } }), { status: 422 }));
    const fail = vi.fn();
    const request = stream
      ? apiClient.streamInteract("agent", { utterance: "long" }, vi.fn(), fail)
      : apiClient.interact("agent", { utterance: "long" });
    await expect(request).rejects.toThrow("utterance exceeds maximum length");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fail).toHaveBeenCalledTimes(stream ? 1 : 0);
  });

  it.each([false, true])("does not replay unknown transport outcomes (stream=%s)", async (stream) => {
    fetchMock.mockRejectedValue(new TypeError("connection closed"));
    const request = stream
      ? apiClient.streamInteract("agent", { utterance: "hello" }, vi.fn())
      : apiClient.interact("agent", { utterance: "hello" });
    await expect(request).rejects.toThrow("connection closed");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([false, true])("retains explicit unprefixed-route compatibility (stream=%s)", async (stream) => {
    fetchMock.mockResolvedValueOnce(new Response("Not found", { status: 404 }));
    fetchMock.mockResolvedValueOnce(new Response(stream ? 'data: {"type":"final"}\n\n' : '{"response":"ok"}'));
    const onChunk = vi.fn();
    if (stream) {
      await apiClient.streamInteract("agent", { utterance: "hello" }, onChunk);
      expect(onChunk).toHaveBeenCalledWith({ type: "final" });
    } else {
      expect(await apiClient.interact("agent", { utterance: "hello" })).toEqual({ response: "ok" });
    }
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:8130/api/agents/agent/interact",
      "http://127.0.0.1:8130/agents/agent/interact",
    ]);
  });

  it("does not replay after receiving part of a stream", async () => {
    const body = new ReadableStream({
      start(controller) { controller.enqueue(new TextEncoder().encode('data: {"type":"start"}\n\n')); },
      pull(controller) { controller.error(new Error("stream disconnected")); },
    });
    fetchMock.mockResolvedValue(new Response(body));
    const chunks = vi.fn();
    const fail = vi.fn();
    await expect(apiClient.streamInteract("agent", { utterance: "hello" }, chunks, fail)).rejects.toThrow("stream disconnected");
    expect(chunks).toHaveBeenCalledWith({ type: "start" });
    expect(fail).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not swallow consumer errors or replay a parsed stream", async () => {
    fetchMock.mockResolvedValue(new Response('data: {"type":"final"}\n\n'));
    await expect(apiClient.streamInteract("agent", { utterance: "hello" }, () => { throw new Error("consumer failed"); })).rejects.toThrow("consumer failed");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reads CRLF framed events", async () => {
    fetchMock.mockResolvedValue(new Response('data: {"type":"start"}\r\n\r\ndata: {"type":"final"}\r\n\r\n'));
    const chunks = vi.fn();
    await apiClient.streamInteract("agent", { utterance: "hello" }, chunks);
    expect(chunks.mock.calls.map(([chunk]) => chunk.type)).toEqual(["start", "final"]);
  });

  it("discards an unterminated final frame and reports incomplete delivery", async () => {
    fetchMock.mockResolvedValue(new Response('data: {"type":"start"}\n\ndata: {"type":"final"}'));
    const chunks = vi.fn();
    await expect(apiClient.streamInteract("agent", { utterance: "hello" }, chunks)).rejects.toThrow("ended before completion");
    expect(chunks.mock.calls.map(([chunk]) => chunk.type)).toEqual(["start"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reports premature EOF instead of silently completing or replaying", async () => {
    fetchMock.mockResolvedValue(new Response('data: {"type":"start"}\n\n'));
    const fail = vi.fn();
    await expect(apiClient.streamInteract("agent", { utterance: "hello" }, vi.fn(), fail)).rejects.toThrow("ended before completion");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fail).toHaveBeenCalledTimes(1);
  });

  it("propagates cancellation without an error callback or a POST", async () => {
    const controller = new AbortController();
    controller.abort();
    const fail = vi.fn();
    await expect(apiClient.streamInteract("agent", { utterance: "hello" }, vi.fn(), fail, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(fail).not.toHaveBeenCalled();
  });

  it("retains the server-issued session token before forwarding the start event", async () => {
    fetchMock.mockResolvedValue(new Response('data: {"type":"start","session_id":"session","session_token":"test-session-token"}\n\ndata: {"type":"final"}\n\n'));
    await apiClient.streamInteract("agent", { utterance: "hello" }, (chunk) => {
      if (chunk.type === "start") expect(getInteractSessionToken("session")).toBe("test-session-token");
    });
  });

  it("bounds an unterminated stream without replaying the request", async () => {
    fetchMock.mockResolvedValue(new Response("data: " + "x".repeat(8 * 1024 * 1024 + 1)));
    await expect(apiClient.streamInteract("agent", { utterance: "hello" }, vi.fn())).rejects.toMatchObject({ type: "max-buffer-size-exceeded" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
