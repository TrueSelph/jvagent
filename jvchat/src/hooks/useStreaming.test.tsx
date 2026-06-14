import { act, renderHook, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { useStreaming, ATTACHMENT_ONLY_USER_PROMPT } from "./useStreaming";

vi.mock("../config/api", () => ({
  apiClient: {
    streamInteract: vi.fn(),
  },
}));

vi.mock("../utils/storage", () => ({
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
    mockGetMessages.mockReturnValue([]);
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
    expect(result.current.messages.some((m) => m.role === "user")).toBe(true);
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
});
