import {
  useState,
  FormEvent,
  useEffect,
  useCallback,
  useMemo,
  useRef,
} from "react";
import { useAuth } from "../hooks/useAuth";
import { useTheme } from "../context/ThemeContext";
import { getConfigAsync, saveConfig } from "../config/config";
import {
  cleanupOldStorage,
  getSavedCredentials,
  upsertSavedCredential,
  removeSavedCredential,
  updateSavedCredential,
  buildSavedAccountsExportJson,
  parseSavedAccountsImport,
  importSavedAccountsPortable,
  type SavedCredential,
} from "../utils/storage";

function accountSortLabel(cred: SavedCredential): string {
  return (cred.name?.trim() || cred.email).toLowerCase();
}

/** Non-localhost accounts sort first; localhost (and loopback) last, still A–Z within each group. */
function isLocalhostServerUrl(serverUrl: string): boolean {
  const s = serverUrl.toLowerCase();
  return s.includes("localhost") || s.includes("127.0.0.1");
}

function normalizeServerUrlInput(
  serverUrl: string,
): { ok: true; url: string } | { ok: false; message: string } {
  let validatedUrl = serverUrl.trim();
  if (!validatedUrl) {
    return { ok: false, message: "Please enter the server URL" };
  }
  if (!validatedUrl.match(/^https?:\/\//i)) {
    validatedUrl = `http://${validatedUrl}`;
  }
  try {
    new URL(validatedUrl);
  } catch {
    return {
      ok: false,
      message:
        "Please enter a valid URL (e.g., localhost:8000 or http://localhost:8000)",
    };
  }
  return { ok: true, url: validatedUrl };
}

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [serverUrl, setServerUrl] = useState("");
  const [name, setName] = useState("");
  const [savedCreds, setSavedCreds] = useState<SavedCredential[]>([]);
  const { login, loading, error } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [localError, setLocalError] = useState<string | null>(null);
  const [accountsNotice, setAccountsNotice] = useState<string | null>(null);
  const [editModal, setEditModal] = useState<{
    id: string;
    serverUrl: string;
    email: string;
    password: string;
    name: string;
  } | null>(null);
  const [editModalError, setEditModalError] = useState<string | null>(null);
  const importFileRef = useRef<HTMLInputElement>(null);
  const importModeRef = useRef<"merge" | "replace">("merge");

  const loadSavedCredentials = useCallback(() => {
    setSavedCreds(getSavedCredentials());
  }, []);

  useEffect(() => {
    cleanupOldStorage();
    loadSavedCredentials();
    getConfigAsync()
      .then((config) => {
        if (config.jvagent.url) {
          setServerUrl(config.jvagent.url);
        }
      })
      .catch((err) => {
        console.warn("Failed to load config:", err);
      });
  }, [loadSavedCredentials]);

  const handleSelectCredential = async (cred: SavedCredential) => {
    setServerUrl(cred.serverUrl);
    setEmail(cred.email);
    setPassword(cred.password);
    setName(cred.name ?? "");
    setLocalError(null);

    saveConfig({ jvagent: { url: cred.serverUrl } });

    try {
      await login({
        email: cred.email,
        password: cred.password,
        serverUrl: cred.serverUrl,
      });
    } catch (err: any) {
      let errorMsg =
        err.response?.data?.detail || err.message || "Login failed";
      if (err.response?.status === 401) {
        errorMsg = "Invalid email or password.";
      } else if (
        err.code === "ERR_NETWORK" ||
        err.message?.includes("Network Error") ||
        err.message?.includes("Failed to fetch")
      ) {
        errorMsg =
          "Network Error: Cannot connect to jvagent server. Please check the server URL.";
      }
      setLocalError(errorMsg);
    }
  };

  const handleRemoveCredential = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    removeSavedCredential(id);
    loadSavedCredentials();
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLocalError(null);

    if (!email || !password) {
      setLocalError("Please enter both email and password");
      return;
    }

    const urlResult = normalizeServerUrlInput(serverUrl);
    if (!urlResult.ok) {
      setLocalError(urlResult.message);
      return;
    }
    const validatedUrl = urlResult.url;

    if (validatedUrl !== serverUrl.trim()) {
      setServerUrl(validatedUrl);
    }

    saveConfig({ jvagent: { url: validatedUrl } });

    try {
      await login({ email, password, serverUrl: validatedUrl });
      upsertSavedCredential({
        serverUrl: validatedUrl,
        email,
        password,
        name: name.trim() || undefined,
      });
      loadSavedCredentials();
    } catch (err: any) {
      let errorMsg =
        err.response?.data?.detail || err.message || "Login failed";
      if (err.response?.status === 401) {
        errorMsg = "Invalid email or password.";
      } else if (
        err.code === "ERR_NETWORK" ||
        err.message?.includes("Network Error") ||
        err.message?.includes("Failed to fetch")
      ) {
        errorMsg =
          "Network Error: Cannot connect to jvagent server. Please check the server URL and ensure the server is running.";
      }
      setLocalError(errorMsg);
    }
  };

  const displayError = localError || error;

  const sortedSavedCreds = useMemo(() => {
    return [...savedCreds].sort((a, b) => {
      const localA = isLocalhostServerUrl(a.serverUrl) ? 1 : 0;
      const localB = isLocalhostServerUrl(b.serverUrl) ? 1 : 0;
      if (localA !== localB) return localA - localB;
      const cmp = accountSortLabel(a).localeCompare(accountSortLabel(b), undefined, {
        sensitivity: "base",
      });
      if (cmp !== 0) return cmp;
      return a.email.localeCompare(b.email, undefined, { sensitivity: "base" });
    });
  }, [savedCreds]);

  const handleExportAccounts = () => {
    setAccountsNotice(null);
    const json = buildSavedAccountsExportJson();
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().slice(0, 10);
    a.href = url;
    a.download = `jvchat-saved-accounts-${stamp}.json`;
    a.click();
    URL.revokeObjectURL(url);
    setAccountsNotice("Exported saved accounts (file contains passwords).");
  };

  const triggerImport = (mode: "merge" | "replace") => {
    setLocalError(null);
    setAccountsNotice(null);
    importModeRef.current = mode;
    importFileRef.current?.click();
  };

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      const parsed = parseSavedAccountsImport(text);
      if (!parsed.ok) {
        setLocalError(parsed.error);
        return;
      }
      const mode = importModeRef.current;
      if (mode === "replace") {
        const ok = window.confirm(
          `Replace all ${savedCreds.length} saved account(s) with ${parsed.accounts.length} from file? This cannot be undone.`,
        );
        if (!ok) return;
      }
      importSavedAccountsPortable(parsed.accounts, mode);
      loadSavedCredentials();
      setLocalError(null);
      setAccountsNotice(
        mode === "merge"
          ? `Imported ${parsed.accounts.length} account(s) (merged with existing).`
          : `Replaced saved accounts with ${parsed.accounts.length} from file.`,
      );
    };
    reader.readAsText(file);
  };

  const openEditModal = (e: React.MouseEvent, cred: SavedCredential) => {
    e.stopPropagation();
    setLocalError(null);
    setEditModalError(null);
    setEditModal({
      id: cred.id,
      serverUrl: cred.serverUrl,
      email: cred.email,
      password: cred.password,
      name: cred.name ?? "",
    });
  };

  const closeEditModal = () => {
    setEditModal(null);
    setEditModalError(null);
  };

  const patchEditModal = (
    patch: Partial<NonNullable<typeof editModal>>,
  ) => {
    setEditModalError(null);
    setEditModal((m) => (m ? { ...m, ...patch } : m));
  };

  const saveEditModal = (e: FormEvent) => {
    e.preventDefault();
    if (!editModal) return;
    if (!editModal.email.trim() || !editModal.password) {
      setEditModalError("Email and password are required.");
      return;
    }
    const urlResult = normalizeServerUrlInput(editModal.serverUrl);
    if (!urlResult.ok) {
      setEditModalError(urlResult.message);
      return;
    }
    updateSavedCredential(editModal.id, {
      serverUrl: urlResult.url,
      email: editModal.email.trim(),
      password: editModal.password,
      name: editModal.name.trim() || undefined,
    });
    loadSavedCredentials();
    setLocalError(null);
    closeEditModal();
    setAccountsNotice("Saved account updated.");
  };

  useEffect(() => {
    if (!editModal) return;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") closeEditModal();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editModal]);

  useEffect(() => {
    if (!accountsNotice) return;
    const t = window.setTimeout(() => setAccountsNotice(null), 5000);
    return () => window.clearTimeout(t);
  }, [accountsNotice]);

  return (
    <div className="h-full min-h-0 overflow-y-auto flex items-center justify-center bg-gray-50 dark:bg-slate-950 py-12 px-4 sm:px-6 lg:px-8 relative">
      <button
        onClick={toggleTheme}
        className="absolute top-4 right-4 p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition-colors"
        aria-label={
          theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
        }
      >
        {theme === "dark" ? (
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
              d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"
            />
          </svg>
        ) : (
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
              d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"
            />
          </svg>
        )}
      </button>
      <input
        ref={importFileRef}
        type="file"
        accept="application/json,.json"
        className="hidden"
        aria-hidden
        onChange={handleImportFile}
      />

      {editModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 dark:bg-black/60"
          role="presentation"
          onClick={closeEditModal}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="edit-saved-account-title"
            className="w-full max-w-md rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 shadow-xl p-5 space-y-4"
            onClick={(ev) => ev.stopPropagation()}
          >
            <h3
              id="edit-saved-account-title"
              className="text-lg font-semibold text-gray-900 dark:text-gray-100"
            >
              Edit saved account
            </h3>
            {editModalError && (
              <div className="rounded-md bg-red-50 dark:bg-red-900/30 px-3 py-2 text-sm text-red-800 dark:text-red-300">
                {editModalError}
              </div>
            )}
            <form onSubmit={saveEditModal} className="space-y-3">
              <div>
                <label
                  htmlFor="edit-server-url"
                  className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
                >
                  Server URL
                </label>
                <input
                  id="edit-server-url"
                  type="text"
                  required
                  className="block w-full px-3 py-2 border border-gray-300 dark:border-gray-500 rounded-md text-sm text-gray-900 dark:text-gray-100 bg-white dark:bg-gray-900 dark:[color-scheme:dark] focus:ring-indigo-500 focus:border-indigo-500"
                  value={editModal.serverUrl}
                  onChange={(e) => patchEditModal({ serverUrl: e.target.value })}
                />
              </div>
              <div>
                <label
                  htmlFor="edit-email"
                  className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
                >
                  Email
                </label>
                <input
                  id="edit-email"
                  type="email"
                  required
                  autoComplete="email"
                  className="block w-full px-3 py-2 border border-gray-300 dark:border-gray-500 rounded-md text-sm text-gray-900 dark:text-gray-100 bg-white dark:bg-gray-900 dark:[color-scheme:dark] focus:ring-indigo-500 focus:border-indigo-500"
                  value={editModal.email}
                  onChange={(e) => patchEditModal({ email: e.target.value })}
                />
              </div>
              <div>
                <label
                  htmlFor="edit-password"
                  className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
                >
                  Password
                </label>
                <input
                  id="edit-password"
                  type="password"
                  required
                  autoComplete="current-password"
                  className="block w-full px-3 py-2 border border-gray-300 dark:border-gray-500 rounded-md text-sm text-gray-900 dark:text-gray-100 bg-white dark:bg-gray-900 dark:[color-scheme:dark] focus:ring-indigo-500 focus:border-indigo-500"
                  value={editModal.password}
                  onChange={(e) => patchEditModal({ password: e.target.value })}
                />
              </div>
              <div>
                <label
                  htmlFor="edit-name"
                  className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
                >
                  Display name (optional)
                </label>
                <input
                  id="edit-name"
                  type="text"
                  autoComplete="name"
                  className="block w-full px-3 py-2 border border-gray-300 dark:border-gray-500 rounded-md text-sm text-gray-900 dark:text-gray-100 bg-white dark:bg-gray-900 dark:[color-scheme:dark] focus:ring-indigo-500 focus:border-indigo-500"
                  value={editModal.name}
                  onChange={(e) => patchEditModal({ name: e.target.value })}
                />
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button
                  type="button"
                  onClick={closeEditModal}
                  className="px-4 py-2 text-sm font-medium rounded-md border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-600"
                >
                  Save
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="max-w-5xl w-full space-y-8">
        <div>
          <h2 className="mt-6 text-center text-3xl font-extrabold text-gray-900 dark:text-gray-100">
            Sign in to jvchat
          </h2>
          <p className="mt-2 text-center text-sm text-gray-600 dark:text-gray-400">
            Enter your jvagent admin credentials
          </p>
        </div>

        {/* Saved accounts — grid A–Z; import/export */}
        <div className="space-y-3 w-full">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                Saved accounts
              </p>
              <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                {sortedSavedCreds.length === 0
                  ? "None yet — sign in below or import a JSON backup."
                  : `${sortedSavedCreds.length} ${sortedSavedCreds.length === 1 ? "account" : "accounts"}, A–Z · localhost last`}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleExportAccounts}
                disabled={sortedSavedCreds.length === 0}
                className="px-3 py-1.5 text-xs font-medium rounded-md border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-45 disabled:cursor-not-allowed"
              >
                Export JSON
              </button>
              <button
                type="button"
                onClick={() => triggerImport("merge")}
                className="px-3 py-1.5 text-xs font-medium rounded-md border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700"
              >
                Import (merge)
              </button>
              <button
                type="button"
                onClick={() => triggerImport("replace")}
                className="px-3 py-1.5 text-xs font-medium rounded-md border border-amber-300 dark:border-amber-700 text-amber-900 dark:text-amber-100 bg-amber-50 dark:bg-amber-950/40 hover:bg-amber-100 dark:hover:bg-amber-900/30"
              >
                Import (replace all)
              </button>
            </div>
          </div>

          {accountsNotice && (
            <div className="rounded-md bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 px-3 py-2 text-sm text-emerald-800 dark:text-emerald-200">
              {accountsNotice}
            </div>
          )}

          {sortedSavedCreds.length > 0 && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 w-full">
              {sortedSavedCreds.map((cred) => {
                const agentLabel = cred.name?.trim() || cred.email;
                return (
                  <div
                    key={cred.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => handleSelectCredential(cred)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        handleSelectCredential(cred);
                      }
                    }}
                    className="group flex items-center gap-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800/80 shadow-sm px-3 py-3 min-w-0 cursor-pointer transition-colors hover:border-indigo-300 dark:hover:border-indigo-600 hover:shadow-md outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:focus-visible:ring-indigo-400"
                  >
                    <p
                      className="min-w-0 flex-1 text-left font-medium text-gray-900 dark:text-gray-100 truncate"
                      title={agentLabel}
                    >
                      {agentLabel}
                    </p>
                    <div className="flex flex-shrink-0 items-center gap-0.5">
                      <button
                        type="button"
                        onClick={(e) => openEditModal(e, cred)}
                        className="p-2 text-gray-500 hover:text-indigo-600 dark:text-gray-400 dark:hover:text-indigo-400 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700/80"
                        aria-label={`Edit ${agentLabel}`}
                        title="Edit"
                      >
                        <svg
                          className="w-4 h-4"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"
                          />
                        </svg>
                      </button>
                      <button
                        type="button"
                        onClick={(e) => handleRemoveCredential(e, cred.id)}
                        className="p-2 text-gray-500 hover:text-red-600 dark:text-gray-400 dark:hover:text-red-400 rounded-lg hover:bg-red-50 dark:hover:bg-red-950/30"
                        aria-label={`Remove saved account ${agentLabel}`}
                        title="Remove"
                      >
                        <svg
                          className="w-4 h-4"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                          />
                        </svg>
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <form className="mt-8 space-y-6 max-w-md mx-auto" onSubmit={handleSubmit}>
          <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
            Or sign in with new credentials
          </p>
          <div className="rounded-md shadow-sm -space-y-px">
            <div>
              <label htmlFor="server-url" className="sr-only">
                Server URL
              </label>
              <input
                id="server-url"
                name="server-url"
                type="text"
                required
                className="appearance-none rounded-none relative block w-full px-3 py-2 border border-gray-300 dark:border-gray-500 placeholder-gray-500 dark:placeholder-gray-400 text-gray-900 dark:text-gray-100 bg-white dark:bg-gray-800 dark:[color-scheme:dark] rounded-t-md focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 focus:z-10 sm:text-sm"
                placeholder="Server URL (e.g., localhost:8000 or http://localhost:8000)"
                value={serverUrl}
                onChange={(e) => setServerUrl(e.target.value)}
                disabled={loading}
              />
            </div>
            <div>
              <label htmlFor="email" className="sr-only">
                Email address
              </label>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                required
                className="appearance-none rounded-none relative block w-full px-3 py-2 border border-gray-300 dark:border-gray-500 placeholder-gray-500 dark:placeholder-gray-400 text-gray-900 dark:text-gray-100 bg-white dark:bg-gray-800 dark:[color-scheme:dark] focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 focus:z-10 sm:text-sm"
                placeholder="Enter your email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={loading}
              />
            </div>
            <div>
              <label htmlFor="password" className="sr-only">
                Password
              </label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                className="appearance-none rounded-none relative block w-full px-3 py-2 border border-gray-300 dark:border-gray-500 placeholder-gray-500 dark:placeholder-gray-400 text-gray-900 dark:text-gray-100 bg-white dark:bg-gray-800 dark:[color-scheme:dark] focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 focus:z-10 sm:text-sm"
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={loading}
              />
            </div>
            <div>
              <label htmlFor="name" className="sr-only">
                Display name (optional)
              </label>
              <input
                id="name"
                name="name"
                type="text"
                autoComplete="name"
                className="appearance-none rounded-none relative block w-full px-3 py-2 border border-gray-300 dark:border-gray-500 placeholder-gray-500 dark:placeholder-gray-400 text-gray-900 dark:text-gray-100 bg-white dark:bg-gray-800 dark:[color-scheme:dark] rounded-b-md focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 focus:z-10 sm:text-sm"
                placeholder="Display name (optional, for saved account card)"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={loading}
              />
            </div>
          </div>

          {displayError && (
            <div className="rounded-md bg-red-50 dark:bg-red-900/30 p-4">
              <div className="flex">
                <div className="ml-3">
                  <h3 className="text-sm font-medium text-red-800 dark:text-red-300">
                    {displayError}
                  </h3>
                </div>
              </div>
            </div>
          )}

          <div>
            <button
              type="submit"
              disabled={loading}
              className="group relative w-full flex justify-center py-2 px-4 border border-transparent text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 dark:focus:ring-offset-gray-900 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? "Signing in..." : "Sign in"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
