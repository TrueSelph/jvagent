import { act, renderHook, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { useStreaming } from "./useStreaming";

vi.mock("../config/api", () => ({
  apiClient: {
    streamInteract: vi.fn(),
  },
}));

vi.mock("../utils/storage", () => ({
  saveMessages: vi.fn(),
  getMessages: vi.fn(() => []),
  getUserId: vi.fn(() => "user-1"),
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
      expect(result.current.thoughtMessages).toHaveLength(1);
    });

    expect(result.current.thoughtMessages[0].category).toBe("thought");
    expect(result.current.thoughtMessages[0].content).toBe("Thinking...");
    expect(result.current.thoughtMessages[0].streaming).toBe(false);
    expect(result.current.messages.some((m) => m.role === "user")).toBe(true);
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

    expect(result.current.thoughtMessages).toHaveLength(0);
    expect(
      result.current.messages.some(
        (m) => m.role === "assistant" && m.content === "Visible assistant response",
      ),
    ).toBe(true);
  });

  it("keeps thought messages ephemeral when loading saved transcript", async () => {
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
      expect(result.current.thoughtMessages).toHaveLength(1);
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
    expect(result.current.thoughtMessages).toHaveLength(0);
    expect(mockSaveMessages).toHaveBeenCalled();
  });
});
