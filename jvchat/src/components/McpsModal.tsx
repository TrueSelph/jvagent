import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Copy, RefreshCw } from "lucide-react";
import { apiClient } from "../config/api";
import { useTheme } from "../context/ThemeContext";

interface ActionItem {
  id: string;
  entity?: string;
  archetype?: string;
  action?: string;
  context?: Record<string, unknown>;
  [key: string]: unknown;
}

interface OAuthSetupItem {
  server: string;
  service?: string;
  label?: string;
  redirect_uri: string;
  auth_url: string;
}

interface McpAuthBinding {
  email?: string;
}

interface McpsModalProps {
  agentId: string;
  onClose: () => void;
  isEmbedded?: boolean;
}

const SERVER_HEADINGS: Record<string, string> = {
  google_workspace: "Google Workspace",
  microsoft_365: "Microsoft 365",
};

function isMcpOAuthAction(a: ActionItem): boolean {
  if (a.action === "jvagent/mcp_oauth") return true;
  const entity = String(a.entity ?? "");
  const archetype = String(a.archetype ?? "");
  if (entity.includes("MCPOAuthAction") || archetype.includes("MCPOAuthAction")) {
    return true;
  }
  const label = String(a.context?.label ?? a.label ?? "");
  return label === "mcp_oauth";
}

function parseOAuthSetup(raw: unknown): OAuthSetupItem[] {
  if (!Array.isArray(raw)) return [];
  const out: OAuthSetupItem[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const rec = item as Record<string, unknown>;
    const server = String(rec.server ?? "").trim();
    const redirect_uri = String(rec.redirect_uri ?? "").trim();
    const auth_url = String(rec.auth_url ?? "").trim();
    if (!server || !redirect_uri) continue;
    const service = String(rec.service ?? "").trim();
    const label = String(rec.label ?? "").trim();
    out.push({
      server,
      redirect_uri,
      auth_url,
      ...(service ? { service } : {}),
      ...(label ? { label } : {}),
    });
  }
  return out;
}

function oauthSetupFromAction(action: Record<string, unknown> | null): unknown {
  if (!action) return undefined;
  const ctx = action.context;
  if (ctx && typeof ctx === "object" && !Array.isArray(ctx)) {
    return (ctx as Record<string, unknown>).oauth_setup;
  }
  return action.oauth_setup;
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const el = document.createElement("textarea");
      el.value = text;
      el.setAttribute("readonly", "");
      el.style.position = "fixed";
      el.style.left = "-9999px";
      document.body.appendChild(el);
      el.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(el);
      return ok;
    } catch {
      return false;
    }
  }
}

export function McpsModal({
  agentId,
  onClose,
  isEmbedded = true,
}: McpsModalProps) {
  const { theme } = useTheme();
  const dark = theme === "dark";
  const [setup, setSetup] = useState<OAuthSetupItem[]>([]);
  const [bindingsByServer, setBindingsByServer] = useState<
    Record<string, Record<string, McpAuthBinding>>
  >({});
  const [loading, setLoading] = useState(true);
  const [statusLoading, setStatusLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const fetchBindings = useCallback(async (items: OAuthSetupItem[]) => {
    const servers = [...new Set(items.map((item) => item.server))];
    if (servers.length === 0) {
      setBindingsByServer({});
      return;
    }
    setStatusLoading(true);
    try {
      const next: Record<string, Record<string, McpAuthBinding>> = {};
      await Promise.all(
        servers.map(async (server) => {
          try {
            const st = await apiClient.getMcpAuthStatus(server);
            next[server] = st?.bindings ?? {};
          } catch {
            next[server] = {};
          }
        }),
      );
      setBindingsByServer(next);
    } finally {
      setStatusLoading(false);
    }
  }, []);

  const fetchSetup = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiClient.getActions(agentId, {
        page: 1,
        per_page: 100,
        enabled_only: false,
      });
      const list = res?.actions ?? res?.data?.actions ?? res ?? [];
      const arr: ActionItem[] = Array.isArray(list) ? list : [];
      const oauthAction = arr.find(isMcpOAuthAction) ?? null;
      if (!oauthAction) {
        setSetup([]);
        setBindingsByServer({});
        return;
      }
      let items = parseOAuthSetup(oauthSetupFromAction(oauthAction));
      if (items.length === 0 && oauthAction.id) {
        const full = await apiClient.getAction(oauthAction.id);
        items = parseOAuthSetup(oauthSetupFromAction(full));
      }
      setSetup(items);
      await fetchBindings(items);
    } catch (err: unknown) {
      console.error("Failed to fetch MCP OAuth setup:", err);
      setError(
        err instanceof Error && err.message
          ? err.message
          : "Failed to load MCP OAuth setup",
      );
      setSetup([]);
      setBindingsByServer({});
    } finally {
      setLoading(false);
    }
  }, [agentId, fetchBindings]);

  useEffect(() => {
    void fetchSetup();
  }, [fetchSetup]);

  useEffect(() => {
    const onFocus = () => {
      if (setup.length === 0) return;
      void fetchBindings(setup);
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [setup, fetchBindings]);

  useEffect(() => {
    if (!isEmbedded || !onClose) return;
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [isEmbedded, onClose]);

  const groups = useMemo(() => {
    const byServer = new Map<string, OAuthSetupItem[]>();
    for (const item of setup) {
      const list = byServer.get(item.server) ?? [];
      list.push(item);
      byServer.set(item.server, list);
    }
    return Array.from(byServer.entries());
  }, [setup]);

  const handleCopy = async (key: string, value: string) => {
    const ok = await copyText(value);
    if (!ok) return;
    setCopiedKey(key);
    window.setTimeout(() => {
      setCopiedKey((prev) => (prev === key ? null : prev));
    }, 1500);
  };

  const content = (
    <div
      className={`rounded-lg shadow-xl w-full h-full max-w-[95vw] max-h-[95vh] flex flex-col border ${dark ? "bg-zinc-900 border-zinc-700 text-zinc-100" : "bg-white border-zinc-200"}`}
      onClick={(e) => isEmbedded && e.stopPropagation()}
    >
      <div
        className={`flex-shrink-0 border-b px-4 sm:px-6 py-4 flex items-center justify-between ${dark ? "border-zinc-700" : "border-zinc-200"}`}
      >
        <h2
          className={`text-xl sm:text-2xl font-semibold ${dark ? "text-zinc-100" : "text-zinc-900"}`}
        >
          MCPs
        </h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void fetchBindings(setup)}
            disabled={statusLoading || loading || setup.length === 0}
            className={`px-3 py-2 text-sm rounded-lg transition-colors flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed ${
              dark
                ? "text-zinc-300 bg-zinc-800 hover:bg-zinc-700"
                : "text-zinc-700 bg-zinc-100 hover:bg-zinc-200"
            }`}
            title="Refresh connection status"
          >
            <RefreshCw
              className={`h-4 w-4 ${statusLoading ? "animate-spin" : ""}`}
              strokeWidth={2}
            />
            <span className="hidden sm:inline">Refresh</span>
          </button>
          <button
            onClick={onClose}
            className={`p-2 rounded-lg transition-colors ${dark ? "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-700" : "text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100"}`}
            aria-label="Close"
          >
          <svg
            className="w-5 h-5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-4 sm:p-6 space-y-6">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div
              className={`animate-spin rounded-full h-8 w-8 border-b-2 ${dark ? "border-zinc-400" : "border-zinc-600"}`}
            />
          </div>
        ) : error ? (
          <p className={`text-sm ${dark ? "text-red-400" : "text-red-600"}`}>
            {error}
          </p>
        ) : groups.length === 0 ? (
          <p className={`text-sm ${dark ? "text-zinc-400" : "text-zinc-500"}`}>
            No OAuth setup yet. Add an MCP OAuth action and set a public base
            URL, then restart the agent.
          </p>
        ) : (
          groups.map(([server, items]) => (
            <section key={server} className="space-y-3">
              <h3
                className={`text-sm font-semibold ${dark ? "text-zinc-200" : "text-zinc-800"}`}
              >
                {SERVER_HEADINGS[server] ?? server}
              </h3>
              <ul className="space-y-3">
                {items.map((item, index) => {
                  const title = item.label || item.service || server;
                  const copyKey = `${server}:${item.service || title}:${index}`;
                  const boundEmail = item.service
                    ? bindingsByServer[server]?.[item.service]?.email
                    : Object.values(bindingsByServer[server] ?? {}).find(
                        (binding) => binding?.email,
                      )?.email;
                  const connected = Boolean(boundEmail);
                  return (
                    <li
                      key={copyKey}
                      className={`space-y-3 rounded-lg border p-4 ${
                        dark
                          ? "border-zinc-700 bg-zinc-800/50"
                          : "border-zinc-200 bg-white"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h4
                            className={`text-sm font-medium ${
                              dark ? "text-zinc-100" : "text-zinc-900"
                            }`}
                          >
                            {title}
                          </h4>
                          <p
                            className={`mt-1 text-xs ${
                              connected
                                ? "text-emerald-500"
                                : dark
                                  ? "text-zinc-500"
                                  : "text-zinc-400"
                            }`}
                          >
                            {connected
                              ? `Connected as ${boundEmail}`
                              : "Not connected"}
                          </p>
                        </div>
                        <button
                          type="button"
                          disabled={!item.auth_url}
                          title={title}
                          onClick={() => {
                            if (item.auth_url) {
                              window.open(
                                item.auth_url,
                                "_blank",
                                "noopener,noreferrer",
                              );
                            }
                          }}
                          className="shrink-0 px-4 py-2 bg-zinc-600 text-white text-sm font-medium rounded-lg hover:bg-zinc-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        >
                          {connected ? "Reconnect" : "Connect"}
                        </button>
                      </div>
                      <div className="flex items-start gap-2">
                        <code
                          className={`flex-1 min-w-0 break-all rounded px-2 py-1.5 text-xs font-mono ${
                            dark
                              ? "bg-zinc-900 text-zinc-200"
                              : "bg-zinc-50 text-zinc-800 border border-zinc-200"
                          }`}
                        >
                          {item.redirect_uri}
                        </code>
                        <button
                          type="button"
                          onClick={() => void handleCopy(copyKey, item.redirect_uri)}
                          className={`shrink-0 p-1.5 rounded-md transition-colors ${
                            dark
                              ? "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-700"
                              : "text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100"
                          }`}
                          aria-label={
                            copiedKey === copyKey
                              ? "Redirect URI copied"
                              : "Copy redirect URI"
                          }
                          title={copiedKey === copyKey ? "Copied" : "Copy"}
                        >
                          {copiedKey === copyKey ? (
                            <Check className="h-4 w-4" strokeWidth={2} />
                          ) : (
                            <Copy className="h-4 w-4" strokeWidth={2} />
                          )}
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))
        )}
      </div>
    </div>
  );

  if (isEmbedded) {
    return (
      <div
        className={`fixed inset-0 z-50 flex items-center justify-center p-4 ${dark ? "bg-black/70" : "bg-black/50"}`}
        onClick={(e) => {
          if (e.target === e.currentTarget && onClose) onClose();
        }}
      >
        {content}
      </div>
    );
  }

  return content;
}
