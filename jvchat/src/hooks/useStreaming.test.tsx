import * as attachments from "../utils/interactAttachments";
import { act, renderHook, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { useStreaming, ATTACHMENT_ONLY_USER_PROMPT } from "./useStreaming";

vi.mock("../config/api", () => ({
  apiClient: {
    streamInteract: vi.fn(),
  },
}));

vi.mock("../utils/storage", async (importOriginal) => ({
  ...await importOriginal<typeof import("../utils/storage")>(),
  saveMessages: vi.fn(),
  getMessages: vi.fn(() => []),
  syncUserIdFromAccessToken: vi.fn(() => "user-1"),
  getEffectiveUserId: vi.fn(() => "user-1"),
}));

import { apiClient } from "../config/api";
import { getMessages, saveMessages } from "../utils/storage";

const mockStreamInteract = vi.mocked(apiClient.streamInteract);
const mockGetMessages = vi.mocked(getMessages);
const mockSaveMessages = vi.mocked(saveMessages);

describe("useStreaming thought handling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const disk = new Map<string, unknown[]>();
    mockGetMessages.mockImplementation(session => disk.get(session) ?? []);
    mockSaveMessages.mockImplementation((session, messages) => {
      disk.set(session, structuredClone(messages));
    });
  });

  it("streams thought chunks and finalizes thought streaming state", async () => {
    mockStreamInteract.mockImplementation(async (_agentId, _request, onChunk) => {
      onChunk({
        type: "start",
        interaction_id: "int-1",
        session_id: "sess-1",
      });
      onChunk({
        type: "message",
        message: {
          id: "thought-1",
          session_id: "sess-1",
          interaction_id: "int-1",
          message_type: "stream_chunk",
          content: "Thinking...",
          channel: "default",
          category: "thought",
          thought_type: "reasoning",
          metadata: {},
        },
      });
      onChunk({
        type: "message",
        message: {
          id: "thought-1",
          session_id: "sess-1",
          interaction_id: "int-1",
          message_type: "final",
          content: "",
          channel: "default",
          category: "thought",
          thought_type: "reasoning",
          metadata: {},
        },
      });
      onChunk({
        type: "final",
        interaction: {
          id: "int-1",
          utterance: "hello",
          actions: [],
          directives: [],
          parameters: [],
          model_log: [],
          messages: [],
          streamed: true,
        },
      });
    });

    const { result } = renderHook(() => useStreaming("agent-1", "sess-1"));

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });

    const thought = result.current.messages.find((m) => m.category === "thought");
    expect(thought?.category).toBe("thought");
    expect(thought?.content).toBe("Thinking...");
    expect(thought?.streaming).toBe(false);
    expect(result.current.messages.find((m) => m.role === "user")?.interactionId).toBe("int-1");
  });

  it("merges thought adhoc flush into the streaming row instead of duplicating", async () => {
    mockStreamInteract.mockImplementation(async (_agentId, _request, onChunk) => {
      onChunk({
        type: "start",
        interaction_id: "int-merge",
        session_id: "sess-merge",
      });
      onChunk({
        type: "message",
        message: {
          id: "o.ResponseMessage.abc123",
          session_id: "sess-merge",
          interaction_id: "int-merge",
          message_type: "stream_chunk",
          content: "Part ",
          channel: "default",
          category: "thought",
          thought_type: "reasoning",
          segment_id: "seg-1",
          metadata: {},
        },
      });
      onChunk({
        type: "message",
        message: {
          id: "o.ResponseMessage.abc123",
          session_id: "sess-merge",
          interaction_id: "int-merge",
          message_type: "stream_chunk",
          content: "two",
          channel: "default",
          category: "thought",
          thought_type: "reasoning",
          segment_id: "seg-1",
          metadata: {},
        },
      });
      onChunk({
        type: "message",
        message: {
          id: "o.ResponseMessage.abc123",
          session_id: "sess-merge",
          interaction_id: "int-merge",
          message_type: "adhoc",
          content: "Part two",
          channel: "default",
          category: "thought",
          thought_type: "reasoning",
          segment_id: "seg-1",
          metadata: {},
        },
      });
      onChunk({
        type: "message",
        message: {
          id: "o.ResponseMessage.abc123",
          session_id: "sess-merge",
          interaction_id: "int-merge",
          message_type: "final",
          content: "",
          channel: "default",
          category: "thought",
          thought_type: "reasoning",
          metadata: {},
        },
      });
      onChunk({
        type: "final",
        interaction: {
          id: "int-merge",
          utterance: "hello",
          actions: [],
          directives: [],
          parameters: [],
          model_log: [],
          messages: [],
          streamed: true,
        },
      });
    });

    const { result } = renderHook(() => useStreaming("agent-1", "sess-merge"));

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });

    const thoughts = result.current.messages.filter((m) => m.category === "thought");
    expect(thoughts).toHaveLength(1);
    expect(thoughts[0]?.content).toBe("Part two");
    expect(thoughts[0]?.streaming).toBe(false);
  });

  it("does not route category=user messages into thought stream from metadata", async () => {
    mockStreamInteract.mockImplementation(async (_agentId, _request, onChunk) => {
      onChunk({
        type: "start",
        interaction_id: "int-2",
        session_id: "sess-2",
      });
      onChunk({
        type: "message",
        message: {
          id: "assistant-1",
          session_id: "sess-2",
          interaction_id: "int-2",
          message_type: "adhoc",
          content: "Visible assistant response",
          channel: "default",
          category: "user",
          metadata: { tool_call: true },
        },
      });
      onChunk({
        type: "final",
        interaction: {
          id: "int-2",
          utterance: "hi",
          actions: [],
          directives: [],
          parameters: [],
          model_log: [],
          messages: [],
          streamed: true,
        },
      });
    });

    const { result } = renderHook(() => useStreaming("agent-1", "sess-2"));

    await act(async () => {
      await result.current.sendMessage("hi");
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });

    expect(result.current.messages.filter((m) => m.category === "thought")).toHaveLength(0);
    expect(
      result.current.messages.some(
        (m) => m.role === "assistant" && m.content === "Visible assistant response",
      ),
    ).toBe(true);
  });

  it("persists thought messages inside transcript when loading saved transcript", async () => {
    mockStreamInteract.mockImplementation(async (_agentId, _request, onChunk) => {
      onChunk({
        type: "start",
        interaction_id: "int-3",
        session_id: "sess-3",
      });
      onChunk({
        type: "message",
        message: {
          id: "thought-ephemeral",
          session_id: "sess-3",
          interaction_id: "int-3",
          message_type: "adhoc",
          content: "Ephemeral thought",
          channel: "default",
          category: "thought",
          metadata: {},
        },
      });
      onChunk({
        type: "final",
        interaction: {
          id: "int-3",
          utterance: "prompt",
          actions: [],
          directives: [],
          parameters: [],
          model_log: [],
          messages: [],
          streamed: true,
        },
      });
    });

    const { result } = renderHook(() => useStreaming("agent-1", "sess-3"));

    await act(async () => {
      await result.current.sendMessage("prompt");
    });

    await waitFor(() => {
      expect(result.current.messages.filter((m) => m.category === "thought")).toHaveLength(1);
    });

    act(() => {
      result.current.loadMessages([
        {
          id: "saved-user-1",
          role: "user",
          content: "Saved transcript message",
          timestamp: "2026-01-01T00:00:00.000Z",
        },
      ]);
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].category).not.toBe("thought");
    expect(mockSaveMessages).toHaveBeenCalled();
  });

  it("passes data with image_urls and whatsapp_media to streamInteract when files are attached", async () => {
    mockStreamInteract.mockImplementation(async (_agentId, _request, onChunk) => {
      onChunk({
        type: "start",
        interaction_id: "int-files",
        session_id: "sess-files",
      });
      onChunk({
        type: "final",
        interaction: {
          id: "int-files",
          utterance: ATTACHMENT_ONLY_USER_PROMPT,
          actions: [],
          directives: [],
          parameters: [],
          model_log: [],
          messages: [],
          streamed: true,
        },
      });
    });

    const img = new File([Uint8Array.of(137, 80, 78, 71)], "a.png", {
      type: "image/png",
    });
    const pdf = new File([Uint8Array.of(37, 80, 68, 70)], "b.pdf", {
      type: "application/pdf",
    });

    const { result } = renderHook(() =>
      useStreaming("agent-1", "sess-files"),
    );

    await act(async () => {
      await result.current.sendMessage("", { files: [img, pdf] });
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });

    expect(mockStreamInteract).toHaveBeenCalled();
    const call = mockStreamInteract.mock.calls.find(
      (c) => (c[1] as { data?: unknown }).data,
    );
    expect(call).toBeDefined();
    const request = call![1] as {
      utterance: string;
      stream: boolean;
      data?: { image_urls?: unknown[]; whatsapp_media?: unknown[] };
    };
    expect(request.utterance).toBe(ATTACHMENT_ONLY_USER_PROMPT);
    expect(request.data?.image_urls?.length).toBe(1);
    expect(request.data?.whatsapp_media?.length).toBe(1);

    const user = result.current.messages.find((m) => m.role === "user");
    expect(user?.attachments).toHaveLength(2);
  });

  it("clears isStreaming when streamInteract rejects with Unauthorized", async () => {
    mockStreamInteract.mockRejectedValue(new Error("Unauthorized"));

    const { result } = renderHook(() => useStreaming("agent-1", "sess-auth"));

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });

    expect(result.current.error).toMatch(/unauthorized|failed/i);
  });

  it("merges user adhoc flush into the streaming row instead of duplicating", async () => {
    mockStreamInteract.mockImplementation(async (_agentId, _request, onChunk) => {
      onChunk({
        type: "start",
        interaction_id: "int-user-merge",
        session_id: "sess-user-merge",
      });
      onChunk({
        type: "message",
        message: {
          id: "o.ResponseMessage.user123",
          session_id: "sess-user-merge",
          interaction_id: "int-user-merge",
          message_type: "stream_chunk",
          content: "Ships ",
          channel: "default",
          category: "user",
          metadata: {},
        },
      });
      onChunk({
        type: "message",
        message: {
          id: "o.ResponseMessage.user123",
          session_id: "sess-user-merge",
          interaction_id: "int-user-merge",
          message_type: "adhoc",
          content: "Ships Tuesday.",
          channel: "default",
          category: "user",
          metadata: {},
        },
      });
      onChunk({
        type: "final",
        interaction: {
          id: "int-user-merge",
          utterance: "when",
          actions: [],
          directives: [],
          parameters: [],
          model_log: [],
          messages: [],
          streamed: true,
        },
      });
    });

    const { result } = renderHook(() => useStreaming("agent-1", "sess-user-merge"));

    await act(async () => {
      await result.current.sendMessage("when");
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });

    const assistants = result.current.messages.filter((m) => m.role === "assistant");
    expect(assistants).toHaveLength(1);
    expect(assistants[0]?.content).toBe("Ships Tuesday.");
  });
});

describe("session transcript transitions", () => {
  it("clears a new conversation despite a late first-stream preserve hint", () => {
    const { result, rerender } = renderHook(
      ({ session }: { session: string | undefined }) => useStreaming("agent-1", session),
      { initialProps: { session: "session-a" as string | undefined } },
    );
    act(() => result.current.loadMessages([
      { id: "old", role: "user", content: "old private transcript", timestamp: new Date().toISOString() },
    ]));
    expect(result.current.messages).toHaveLength(1);
    act(() => result.current.preserveNextTransition("session-a"));
    rerender({ session: undefined });
    expect(result.current.messages).toEqual([]);
    expect(result.current.sessionId).toBeUndefined();
  });

  it("never carries another conversation's transcript across sidebar selection", () => {
    const { result, rerender } = renderHook(
      ({ session }) => useStreaming("agent-1", session),
      { initialProps: { session: "session-a" } },
    );
    act(() => result.current.loadMessages([
      { id: "old", role: "user", content: "old private transcript", timestamp: new Date().toISOString() },
    ]));
    act(() => result.current.preserveNextTransition("session-b"));
    rerender({ session: "session-b" });
    expect(result.current.messages).toEqual([]);
  });
});

it("preserves the live first-turn transcript when the server assigns its session", async () => {
  mockStreamInteract.mockImplementation(async (_agent, _request, onChunk) => {
    onChunk({ type: "start", interaction_id: "live-turn", session_id: "live-session" });
    onChunk({ type: "final", interaction: {
      id: "live-turn", utterance: "first message", actions: [], directives: [],
      parameters: [], model_log: [], messages: [], streamed: true,
    } });
  });
  const { result, rerender } = renderHook(
    ({ session }: { session: string | undefined }) => useStreaming("agent-1", session),
    { initialProps: { session: undefined as string | undefined } },
  );
  await act(async () => { await result.current.sendMessage("first message"); });
  act(() => result.current.preserveNextTransition("live-session"));
  rerender({ session: "live-session" });
  expect(result.current.messages.some(message => message.content === "first message")).toBe(true);
});

it.each(["new-before-start", "sidebar", "stop"] as const)(
  "retires %s requests before late chunks or cleanup can affect the next request",
  async (mode) => {
    const streams: Array<{
      chunk: Parameters<typeof apiClient.streamInteract>[2];
      error: Parameters<typeof apiClient.streamInteract>[3];
      signal: Parameters<typeof apiClient.streamInteract>[4];
      finish: () => void;
    }> = [];
    mockStreamInteract.mockImplementation((_agent, _request, chunk, error, signal) =>
      new Promise<void>(finish => streams.push({ chunk, error, signal, finish })),
    );
    const { result, rerender } = renderHook(
      ({ session }: { session: string | undefined }) => useStreaming("agent-1", session),
      { initialProps: { session: mode === "new-before-start" ? undefined : "session-a" } },
    );
    let oldRequest!: Promise<string | undefined>;
    act(() => { oldRequest = result.current.sendMessage("old request"); });
    expect(streams).toHaveLength(1);
    act(() => {
      if (mode === "sidebar") rerender({ session: "session-b" });
      else if (mode === "stop") result.current.stopStreaming();
      else result.current.clearMessages();
    });
    expect(streams[0].signal?.aborted).toBe(true);
    expect(result.current.isStreaming).toBe(false);
    let newRequest!: Promise<string | undefined>;
    act(() => { newRequest = result.current.sendMessage("new request"); });
    expect(streams).toHaveLength(2);
    await act(async () => {
      streams[0].chunk({ type: "start", session_id: "late-session", interaction_id: "late-turn" });
      streams[0].error?.(new Error("late error"));
      streams[0].finish();
      expect(await oldRequest).toBeUndefined();
    });
    expect(result.current.isStreaming).toBe(true);
    expect(result.current.error).toBeNull();
    expect(result.current.sessionId).not.toBe("late-session");
    expect(result.current.messages.some(message => message.content === "new request")).toBe(true);
    await act(async () => {
      streams[1].chunk({ type: "start", session_id: "fresh-session", interaction_id: "fresh-turn" });
      streams[1].finish();
      expect(await newRequest).toBe("fresh-session");
    });
    expect(result.current.isStreaming).toBe(false);
  },
);

it("does not submit an attachment after its conversation was reset during preparation", async () => {
  vi.clearAllMocks();
  let finish!: (value: Awaited<ReturnType<typeof attachments.prepareOutgoingAttachments>>) => void;
  const prepare = vi.spyOn(attachments, "prepareOutgoingAttachments").mockImplementationOnce(
    () => new Promise(resolve => { finish = resolve; }),
  );
  try {
    const { result } = renderHook(() => useStreaming("agent-1"));
    let pending!: Promise<string | undefined>;
    act(() => { pending = result.current.sendMessage("file", { files: [new File(["data"], "input.txt")] }); });
    expect(result.current.isStreaming).toBe(true);
    act(() => result.current.clearMessages());
    await act(async () => {
      finish({ data: {}, previews: [] });
      expect(await pending).toBeUndefined();
    });
    expect(mockStreamInteract).not.toHaveBeenCalled();
    expect(result.current.messages).toEqual([]);
    expect(result.current.isStreaming).toBe(false);
  } finally {
    prepare.mockRestore();
  }
});

it("publishes first-session identity before completion without aborting its initial binding", async () => {
  let chunk!: Parameters<typeof apiClient.streamInteract>[2];
  let finish!: () => void;
  let signal: AbortSignal | undefined;
  mockStreamInteract.mockImplementation((_agent, _request, onChunk, _error, requestSignal) => {
    chunk = onChunk;
    signal = requestSignal;
    return new Promise(resolve => { finish = resolve; });
  });
  const { result, rerender } = renderHook(
    ({ session }: { session: string | undefined }) => useStreaming("agent-1", session),
    { initialProps: { session: undefined as string | undefined } },
  );
  let pending!: Promise<string | undefined>;
  act(() => { pending = result.current.sendMessage("first turn"); });
  act(() => chunk({ type: "start", session_id: "assigned-session", interaction_id: "assigned-turn" }));
  expect(result.current.sessionId).toBe("assigned-session");
  act(() => result.current.preserveNextTransition("assigned-session"));
  rerender({ session: "assigned-session" });
  expect(signal?.aborted).toBe(false);
  expect(result.current.isStreaming).toBe(true);
  act(() => result.current.stopStreaming());
  await act(async () => { finish(); expect(await pending).toBeUndefined(); });
});
