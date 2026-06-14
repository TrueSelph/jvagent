import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAgents } from "../hooks/useAgents";
import { getSelectedAgent, saveSelectedAgent } from "../utils/storage";

/**
 * `/chat` resolves to `/chat/:agentId` using saved selection or first available agent.
 */
export function ChatRedirect() {
  const navigate = useNavigate();
  const { agents, loading, error } = useAgents();

  useEffect(() => {
    if (loading || error || agents.length === 0) return;

    const savedName = getSelectedAgent();
    const bySaved = savedName
      ? agents.find(
          (a) =>
            (a.name && a.name === savedName) ||
            (a.alias && a.alias === savedName) ||
            a.id === savedName,
        )
      : undefined;

    const pick = bySaved ?? agents[0];
    if (!pick?.id) return;

    saveSelectedAgent(pick.name || pick.id);
    navigate(`/chat/${encodeURIComponent(pick.id)}`, { replace: true });
  }, [loading, error, agents, navigate]);

  if (error) {
    return (
      <div className="flex h-full min-h-0 w-full flex-1 flex-col items-center justify-center p-8 text-center text-sm text-zinc-600 dark:text-zinc-400">
        Could not load agents. Check your connection and try again.
      </div>
    );
  }

  if (!loading && agents.length === 0) {
    return (
      <div className="flex h-full min-h-0 w-full flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
        <p className="text-zinc-700 dark:text-zinc-300">No agents available.</p>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          Configure agents on the server, then refresh this page.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-1 items-center justify-center">
      <div className="text-center">
        <div className="mx-auto h-10 w-10 animate-spin rounded-full border-2 border-zinc-400 border-t-transparent" />
        <p className="mt-4 text-sm text-zinc-500 dark:text-zinc-400">Loading chat…</p>
      </div>
    </div>
  );
}
