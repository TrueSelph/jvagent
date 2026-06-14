import { describe, it, expect, beforeEach } from "vitest";
import {
  getToken,
  setToken,
  removeToken,
  getRefreshToken,
  setRefreshToken,
  removeRefreshToken,
  getUserId,
  setUserId,
  removeUserId,
  getEffectiveUserId,
  getUserIdFromAccessToken,
  syncUserIdFromAccessToken,
  getConversations,
  saveConversations,
  addConversation,
  updateConversation,
  removeConversation,
  getMessages,
  saveMessages,
  deleteMessages,
  messagesForPersistence,
  getSavedCredentials,
  addSavedCredential,
  removeSavedCredential,
} from "../utils/storage";

function makeJwt(payload: Record<string, unknown>): string {
  const enc = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `header.${enc(payload)}.signature`;
}

describe("token storage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns null when no token stored", () => {
    expect(getToken()).toBeNull();
  });

  it("stores and retrieves a token", () => {
    setToken("test-token");
    expect(getToken()).toBe("test-token");
  });

  it("removes a token", () => {
    setToken("test-token");
    removeToken();
    expect(getToken()).toBeNull();
  });

  it("stores and retrieves a refresh token", () => {
    setRefreshToken("refresh-token");
    expect(getRefreshToken()).toBe("refresh-token");
  });

  it("removes a refresh token", () => {
    setRefreshToken("refresh-token");
    removeRefreshToken();
    expect(getRefreshToken()).toBeNull();
  });
});

describe("user ID storage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns null when no user ID stored", () => {
    expect(getUserId()).toBeNull();
  });

  it("stores and retrieves a user ID", () => {
    setUserId("user-123");
    expect(getUserId()).toBe("user-123");
  });

  it("removes a user ID", () => {
    setUserId("user-123");
    removeUserId();
    expect(getUserId()).toBeNull();
  });

  it("extracts user ID from JWT sub claim", () => {
    setToken(makeJwt({ sub: "jwt-user-456" }));
    expect(getUserIdFromAccessToken()).toBe("jwt-user-456");
  });

  it("extracts user ID from JWT user_id claim fallback", () => {
    setToken(makeJwt({ user_id: "fallback-user" }));
    expect(getUserIdFromAccessToken()).toBe("fallback-user");
  });

  it("returns null for malformed JWT", () => {
    setToken("not.a.jwt");
    expect(getUserIdFromAccessToken()).toBeNull();
  });

  it("getEffectiveUserId returns stored user ID first", () => {
    setUserId("stored-user");
    expect(getEffectiveUserId()).toBe("stored-user");
  });

  it("getEffectiveUserId falls back to JWT extraction", () => {
    setToken(makeJwt({ sub: "jwt-user" }));
    expect(getEffectiveUserId()).toBe("jwt-user");
  });

  it("syncUserIdFromAccessToken persists from JWT when no stored ID", () => {
    setToken(makeJwt({ sub: "synced-user" }));
    const result = syncUserIdFromAccessToken();
    expect(result).toBe("synced-user");
    expect(getUserId()).toBe("synced-user");
  });
});

describe("conversation storage", () => {
  const userId = "user-1";
  const conv = {
    session_id: "sess-1",
    agent_id: "agent-a",
    agent_name: "Agent A",
    created_at: "2024-01-01T00:00:00Z",
    last_message: "Hello",
    last_message_at: "2024-01-01T01:00:00Z",
  };

  beforeEach(() => {
    localStorage.clear();
  });

  it("returns empty array when no conversations", () => {
    expect(getConversations(userId)).toEqual([]);
  });

  it("saves and retrieves conversations", () => {
    saveConversations([conv], userId);
    const result = getConversations(userId);
    expect(result).toHaveLength(1);
    expect(result[0].session_id).toBe("sess-1");
  });

  it("adds a conversation", () => {
    addConversation(conv, userId);
    const result = getConversations(userId);
    expect(result).toHaveLength(1);
    expect(result[0].session_id).toBe("sess-1");
  });

  it("does not duplicate conversations with same session_id", () => {
    addConversation(conv, userId);
    addConversation(conv, userId);
    expect(getConversations(userId)).toHaveLength(1);
  });

  it("updates a conversation", () => {
    addConversation(conv, userId);
    updateConversation("sess-1", { last_message: "Updated" }, userId);
    const result = getConversations(userId);
    expect(result[0].last_message).toBe("Updated");
  });

  it("removes a conversation", () => {
    addConversation(conv, userId);
    removeConversation("sess-1", userId);
    expect(getConversations(userId)).toHaveLength(0);
  });

  it("is a no-op when removing non-existent conversation", () => {
    removeConversation("nonexistent", userId);
    expect(getConversations(userId)).toEqual([]);
  });
});

describe("message storage", () => {
  const sessionId = "sess-1";
  const msg = {
    id: "msg-1",
    role: "user" as const,
    content: "Hello",
    timestamp: "2024-01-01T00:00:00Z",
  };

  beforeEach(() => {
    localStorage.clear();
  });

  it("returns empty array when no messages", () => {
    expect(getMessages(sessionId)).toEqual([]);
  });

  it("saves and retrieves messages", () => {
    saveMessages(sessionId, [msg]);
    const result = getMessages(sessionId);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("msg-1");
  });

  it("deletes messages for a session", () => {
    saveMessages(sessionId, [msg]);
    deleteMessages(sessionId);
    expect(getMessages(sessionId)).toEqual([]);
  });

  it("overwrites messages on re-save", () => {
    saveMessages(sessionId, [msg]);
    const msg2 = { ...msg, id: "msg-2", content: "World" };
    saveMessages(sessionId, [msg2]);
    const result = getMessages(sessionId);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("msg-2");
  });

  it("does not persist debugData on saveMessages", () => {
    saveMessages(sessionId, [
      {
        ...msg,
        debugData: { type: "final", interaction: { id: "int-1" } },
      },
    ]);
    const stored = getMessages(sessionId);
    expect(stored[0].debugData).toBeUndefined();
  });
});

describe("messagesForPersistence", () => {
  it("strips debugData from message objects", () => {
    const stripped = messagesForPersistence([
      { id: "1", content: "hi", debugData: { secret: true } },
    ]);
    expect(stripped[0]).toEqual({ id: "1", content: "hi" });
  });
});

describe("saved credentials storage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns empty array when no credentials", () => {
    expect(getSavedCredentials()).toEqual([]);
  });

  it("adds and retrieves credentials", () => {
    const cred = addSavedCredential({
      serverUrl: "http://localhost:8000",
      email: "test@example.com",
      password: "test-password",
    });
    expect(cred.id).toBeTruthy();
    expect(cred.serverUrl).toBe("http://localhost:8000");

    const all = getSavedCredentials();
    expect(all).toHaveLength(1);
    expect(all[0].email).toBe("test@example.com");
  });

  it("removes credentials", () => {
    const cred = addSavedCredential({
      serverUrl: "http://localhost:8000",
      email: "test@example.com",
      password: "test-password",
    });
    removeSavedCredential(cred.id);
    expect(getSavedCredentials()).toHaveLength(0);
  });
});
