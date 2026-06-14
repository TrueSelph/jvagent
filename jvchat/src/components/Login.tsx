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
      const localA = isLocalhostServerUrl(a.serverUrl) ? 0 : 1;
      const localB = isLocalhostServerUrl(b.serverUrl) ? 0 : 1;
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
    setAccountsNotice("Exported saved accounts. Warning: JSON file contains plaintext passwords — store it securely.");
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
    <div className="h-full min-h-0 overflow-y-auto flex items-center justify-center bg-zinc-50 dark:bg-zinc-950 py-12 px-4 sm:px-6 lg:px-8 relative">
      <button
        onClick={toggleTheme}
        className="absolute top-4 right-4 z-50 p-2 text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200 hover:bg-zinc-200 dark:hover:bg-zinc-800 rounded-lg transition-colors duration-150"
        aria-label={
          theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
        }
      >
        {theme === "dark" ? (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
          </svg>
        ) : (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
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
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50"
          role="presentation"
          onClick={closeEditModal}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="edit-saved-account-title"
            className="w-full max-w-md rounded-lg border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-900 p-5 space-y-4"
            onClick={(ev) => ev.stopPropagation()}
          >
            <h3
              id="edit-saved-account-title"
              className="text-lg font-semibold text-zinc-900 dark:text-zinc-50"
            >
              Edit saved account
            </h3>
            {editModalError && (
              <div className="rounded-lg bg-red-50 dark:bg-red-900/30 px-3 py-2 text-sm text-red-800 dark:text-red-300">
                {editModalError}
              </div>
            )}
            <form onSubmit={saveEditModal} className="space-y-3">
              <div>
                <label htmlFor="edit-server-url" className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                  Server URL
                </label>
                <input
                  id="edit-server-url"
                  type="text"
                  required
                  className="block w-full px-3 py-2 border border-zinc-200 dark:border-white/10 rounded-lg text-sm text-zinc-900 dark:text-zinc-50 bg-white dark:bg-zinc-800 focus:ring-zinc-400 focus:border-zinc-400"
                  value={editModal.serverUrl}
                  onChange={(e) => patchEditModal({ serverUrl: e.target.value })}
                />
              </div>
              <div>
                <label htmlFor="edit-email" className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                  Email
                </label>
                <input
                  id="edit-email"
                  type="email"
                  required
                  autoComplete="email"
                  className="block w-full px-3 py-2 border border-zinc-200 dark:border-white/10 rounded-lg text-sm text-zinc-900 dark:text-zinc-50 bg-white dark:bg-zinc-800 focus:ring-zinc-400 focus:border-zinc-400"
                  value={editModal.email}
                  onChange={(e) => patchEditModal({ email: e.target.value })}
                />
              </div>
              <div>
                <label htmlFor="edit-password" className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                  Password
                </label>
                <input
                  id="edit-password"
                  type="password"
                  required
                  autoComplete="current-password"
                  className="block w-full px-3 py-2 border border-zinc-200 dark:border-white/10 rounded-lg text-sm text-zinc-900 dark:text-zinc-50 bg-white dark:bg-zinc-800 focus:ring-zinc-400 focus:border-zinc-400"
                  value={editModal.password}
                  onChange={(e) => patchEditModal({ password: e.target.value })}
                />
              </div>
              <div>
                <label htmlFor="edit-name" className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                  Display name (optional)
                </label>
                <input
                  id="edit-name"
                  type="text"
                  autoComplete="name"
                  className="block w-full px-3 py-2 border border-zinc-200 dark:border-white/10 rounded-lg text-sm text-zinc-900 dark:text-zinc-50 bg-white dark:bg-zinc-800 focus:ring-zinc-400 focus:border-zinc-400"
                  value={editModal.name}
                  onChange={(e) => patchEditModal({ name: e.target.value })}
                />
              </div>
              <div className="flex gap-2 justify-end pt-2">
                <button
                  type="button"
                  onClick={closeEditModal}
                  className="px-4 py-2 text-sm font-medium rounded-lg border border-zinc-200 dark:border-white/10 text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800 transition-colors duration-150"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 text-sm font-medium rounded-lg text-white bg-zinc-900 hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200 transition-colors duration-150"
                >
                  Save
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="w-full max-w-6xl rounded-lg border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-900 overflow-hidden">
        <div className="flex flex-col lg:flex-row">
          <div className="lg:w-2/3 flex flex-col border-r border-zinc-200 dark:border-white/10">
            <div className="p-6">
              <h2 className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                Saved Accounts
              </h2>
              <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
                {sortedSavedCreds.length === 0
                  ? "No saved accounts yet"
                  : `${sortedSavedCreds.length} ${sortedSavedCreds.length === 1 ? "account" : "accounts"} available`}
              </p>
            </div>

            {sortedSavedCreds.length > 0 && (
              <div className="px-6 pb-3">
                <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
                  <strong>Security note:</strong> Credentials are stored in this
                  browser&apos;s local storage as plain text. Anyone with access to
                  this device or browser can view saved passwords. Do not save
                  credentials on shared devices.
                </div>
              </div>
            )}

            <div className="px-6 pb-4">
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={handleExportAccounts}
                  disabled={sortedSavedCreds.length === 0}
                  className="px-3 py-1.5 text-xs font-medium rounded-lg border border-zinc-200 dark:border-white/10 text-zinc-600 dark:text-zinc-300 bg-white dark:bg-zinc-800 hover:bg-zinc-50 dark:hover:bg-zinc-700 disabled:opacity-45 disabled:cursor-not-allowed transition-colors duration-150"
                >
                  Export JSON
                </button>
                <button
                  type="button"
                  onClick={() => triggerImport("merge")}
                  className="px-3 py-1.5 text-xs font-medium rounded-lg border border-zinc-200 dark:border-white/10 text-zinc-600 dark:text-zinc-300 bg-white dark:bg-zinc-800 hover:bg-zinc-50 dark:hover:bg-zinc-700"
                >
                  Import (merge)
                </button>
                <button
                  type="button"
                  onClick={() => triggerImport("replace")}
                  className="px-3 py-1.5 text-xs font-medium rounded-lg border border-amber-300 dark:border-amber-700 text-amber-800 dark:text-amber-100 bg-amber-50 dark:bg-amber-950/40 hover:bg-amber-100 dark:hover:bg-amber-900/30 transition-colors duration-150"
                >
                  Import (replace all)
                </button>
              </div>

              {accountsNotice && (
                <div className="mt-3 rounded-lg bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 px-3 py-2 text-sm text-emerald-800 dark:text-emerald-200">
                  {accountsNotice}
                </div>
              )}
            </div>

            <div className="flex-1 overflow-y-auto px-6 pb-6" style={{ maxHeight: "50vh" }}>
              {sortedSavedCreds.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center text-center py-12">
                  <div className="w-16 h-16 rounded-full bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center mb-4">
                    <svg className="w-8 h-8 text-zinc-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                    </svg>
                  </div>
                  <p className="text-zinc-900 dark:text-zinc-50 font-medium">
                    No saved accounts
                  </p>
                  <p className="mt-1 text-sm text-zinc-400 dark:text-zinc-500 max-w-xs">
                    Sign in with your credentials on the right, or import a JSON backup to get started
                  </p>
                  <p className="mt-3 text-xs text-amber-600 dark:text-amber-400 max-w-xs">
                    Note: saved credentials are stored in this browser&apos;s local storage as plain text.
                  </p>
                </div>
              ) : (
                <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
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
                        className="group flex items-center gap-2 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-3 py-3 min-w-0 cursor-pointer transition-colors duration-150 hover:border-zinc-400 dark:hover:border-zinc-600 outline-none focus-visible:ring-2 focus-visible:ring-zinc-400"
                      >
                        <p
                          className="min-w-0 flex-1 text-left font-medium text-zinc-900 dark:text-zinc-50 truncate"
                          title={agentLabel}
                        >
                          {agentLabel}
                        </p>
                        <div className="flex flex-shrink-0 items-center gap-0.5">
                          <button
                            type="button"
                            onClick={(e) => openEditModal(e, cred)}
                            className="p-2 text-zinc-400 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-300 rounded-lg hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors duration-150"
                            aria-label={`Edit ${agentLabel}`}
                            title="Edit"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                            </svg>
                          </button>
                          <button
                            type="button"
                            onClick={(e) => handleRemoveCredential(e, cred.id)}
                            className="p-2 text-zinc-400 hover:text-red-600 dark:text-zinc-500 dark:hover:text-red-400 rounded-lg hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors duration-150"
                            aria-label={`Remove saved account ${agentLabel}`}
                            title="Remove"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          <div className="lg:w-1/3 p-6 lg:p-8 bg-zinc-50 dark:bg-zinc-900/50">
            <div className="w-full max-w-md mx-auto space-y-6">
              <div className="text-center">
                <h2 className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                  Sign in
                </h2>
                <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
                  Enter your jvagent admin credentials
                </p>
              </div>

              <form className="space-y-6" onSubmit={handleSubmit}>
                <div className="rounded-lg -space-y-px">
                  <div>
                    <label htmlFor="server-url" className="sr-only">Server URL</label>
                    <input
                      id="server-url"
                      name="server-url"
                      type="text"
                      required
                      className="appearance-none rounded-none relative block w-full px-3 py-2 border border-zinc-200 dark:border-white/10 placeholder-zinc-400 dark:placeholder-zinc-500 text-zinc-900 dark:text-zinc-50 bg-white dark:bg-zinc-800 rounded-t-lg focus:outline-none focus:ring-zinc-400 focus:border-zinc-400 focus:z-10 sm:text-sm"
                      placeholder="Server URL (e.g., localhost:8000)"
                      value={serverUrl}
                      onChange={(e) => setServerUrl(e.target.value)}
                      disabled={loading}
                    />
                  </div>
                  <div>
                    <label htmlFor="email" className="sr-only">Email address</label>
                    <input
                      id="email"
                      name="email"
                      type="email"
                      autoComplete="email"
                      required
                      className="appearance-none rounded-none relative block w-full px-3 py-2 border border-zinc-200 dark:border-white/10 placeholder-zinc-400 dark:placeholder-zinc-500 text-zinc-900 dark:text-zinc-50 bg-white dark:bg-zinc-800 focus:outline-none focus:ring-zinc-400 focus:border-zinc-400 focus:z-10 sm:text-sm"
                      placeholder="Enter your email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      disabled={loading}
                    />
                  </div>
                  <div>
                    <label htmlFor="password" className="sr-only">Password</label>
                    <input
                      id="password"
                      name="password"
                      type="password"
                      autoComplete="current-password"
                      required
                      className="appearance-none rounded-none relative block w-full px-3 py-2 border border-zinc-200 dark:border-white/10 placeholder-zinc-400 dark:placeholder-zinc-500 text-zinc-900 dark:text-zinc-50 bg-white dark:bg-zinc-800 focus:outline-none focus:ring-zinc-400 focus:border-zinc-400 focus:z-10 sm:text-sm"
                      placeholder="Enter your password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      disabled={loading}
                    />
                  </div>
                  <div>
                    <label htmlFor="name" className="sr-only">Display name (optional)</label>
                    <input
                      id="name"
                      name="name"
                      type="text"
                      autoComplete="name"
                      className="appearance-none rounded-none relative block w-full px-3 py-2 border border-zinc-200 dark:border-white/10 placeholder-zinc-400 dark:placeholder-zinc-500 text-zinc-900 dark:text-zinc-50 bg-white dark:bg-zinc-800 rounded-b-lg focus:outline-none focus:ring-zinc-400 focus:border-zinc-400 focus:z-10 sm:text-sm"
                      placeholder="Display name (optional)"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      disabled={loading}
                    />
                  </div>
                </div>

                {displayError && (
                  <div className="rounded-lg bg-red-50 dark:bg-red-900/30 p-4">
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
                    className="group relative w-full flex justify-center py-2 px-4 border border-transparent text-sm font-medium rounded-lg text-white bg-zinc-900 hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-zinc-400 dark:focus:ring-offset-zinc-900 transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {loading ? "Signing in..." : "Sign in"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
