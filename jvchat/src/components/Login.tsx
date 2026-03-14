import { useState, FormEvent, useEffect, useCallback } from "react";
import { useAuth } from "../hooks/useAuth";
import { useTheme } from "../context/ThemeContext";
import { getConfigAsync, saveConfig } from "../config/config";
import {
  cleanupOldStorage,
  getSavedCredentials,
  upsertSavedCredential,
  removeSavedCredential,
  type SavedCredential,
} from "../utils/storage";

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [serverUrl, setServerUrl] = useState("");
  const [name, setName] = useState("");
  const [savedCreds, setSavedCreds] = useState<SavedCredential[]>([]);
  const { login, loading, error } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [localError, setLocalError] = useState<string | null>(null);

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

    if (!serverUrl) {
      setLocalError("Please enter the server URL");
      return;
    }

    let validatedUrl = serverUrl.trim();
    if (!validatedUrl.match(/^https?:\/\//i)) {
      validatedUrl = `http://${validatedUrl}`;
    }

    try {
      new URL(validatedUrl);
    } catch {
      setLocalError(
        "Please enter a valid URL (e.g., localhost:8000 or http://localhost:8000)",
      );
      return;
    }

    if (validatedUrl !== serverUrl) {
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
      <div className="max-w-md w-full space-y-8">
        <div>
          <h2 className="mt-6 text-center text-3xl font-extrabold text-gray-900 dark:text-gray-100">
            Sign in to jvchat
          </h2>
          <p className="mt-2 text-center text-sm text-gray-600 dark:text-gray-400">
            Enter your jvagent admin credentials
          </p>
        </div>

        {/* Saved credentials cards */}
        {savedCreds.length > 0 && (
          <div className="space-y-2 w-full">
            <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
              Saved accounts
            </p>
            <div className="max-h-48 overflow-y-auto overflow-x-hidden rounded-lg border border-gray-200 dark:border-gray-600 min-w-0">
            <div className="grid gap-2 p-2 min-w-0">
              {savedCreds.map((cred) => (
                <div
                  key={cred.id}
                  onClick={() => handleSelectCredential(cred)}
                  className="group flex items-center justify-between gap-3 min-w-0 overflow-hidden px-4 py-3 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 hover:border-indigo-300 dark:hover:border-indigo-600 cursor-pointer transition-colors"
                >
                  <div className="min-w-0 flex-1 overflow-hidden">
                    <p className="font-medium text-gray-900 dark:text-gray-100 truncate">
                      {cred.name || cred.email}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
                      {cred.email} · {cred.serverUrl}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={(e) => handleRemoveCredential(e, cred.id)}
                    className="flex-shrink-0 p-1.5 text-gray-400 hover:text-red-600 dark:hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity rounded"
                    aria-label="Remove saved account"
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
              ))}
            </div>
            </div>
          </div>
        )}

        <form className="mt-8 space-y-6" onSubmit={handleSubmit}>
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
