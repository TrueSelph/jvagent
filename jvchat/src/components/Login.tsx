import { useState, FormEvent, useEffect } from "react";
import { useAuth } from "../hooks/useAuth";
import { getConfigAsync, saveConfig } from "../config/config";
import { saveAuthCreds, cleanupOldStorage } from "../utils/storage";

export function Login() {
  const [email, setEmail] = useState("admin@jvagent.example");
  const [password, setPassword] = useState("your-admin-password-here");
  const [serverUrl, setServerUrl] = useState("");
  const [autoAuth, setAutoAuth] = useState(true);
  const { login, loading, error } = useAuth();
  const [localError, setLocalError] = useState<string | null>(null);

  // Load saved URL from config on mount
  useEffect(() => {
    cleanupOldStorage();
    getConfigAsync()
      .then((config) => {
        if (config.jvagent.url) {
          setServerUrl(config.jvagent.url);
        }
        setAutoAuth(config.ui.auto_authenticate);
      })
      .catch((err) => {
        console.warn("Failed to load config:", err);
      });
  }, []);

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

    // Validate URL format (allow URLs without protocol for convenience)
    let validatedUrl = serverUrl.trim();
    if (!validatedUrl.match(/^https?:\/\//i)) {
      // If no protocol, assume http://
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

    // Update state with validated URL if it changed
    if (validatedUrl !== serverUrl) {
      setServerUrl(validatedUrl);
    }

    // Save the URL to config before attempting login
    saveConfig({ jvagent: { url: validatedUrl }, ui: { auto_authenticate: autoAuth } });

    // Save credentials if auto_authenticate is enabled
    if (autoAuth) {
      saveAuthCreds(email, password, validatedUrl);
    }

    try {
      await login({ email, password, serverUrl: validatedUrl });
    } catch (err: any) {
      let errorMsg =
        err.response?.data?.detail || err.message || "Login failed";

      // Check for network errors
      if (
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
    <div className="min-h-screen flex items-center justify-center bg-gray-50 py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full space-y-8">
        <div>
          <h2 className="mt-6 text-center text-3xl font-extrabold text-gray-900">
            Sign in to jvchat
          </h2>
          <p className="mt-2 text-center text-sm text-gray-600">
            Enter your jvagent admin credentials
          </p>
        </div>
        <form className="mt-8 space-y-6" onSubmit={handleSubmit}>
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
                className="appearance-none rounded-none relative block w-full px-3 py-2 border border-gray-300 placeholder-gray-500 text-gray-900 rounded-t-md focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 focus:z-10 sm:text-sm"
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
                className="appearance-none rounded-none relative block w-full px-3 py-2 border border-gray-300 placeholder-gray-500 text-gray-900 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 focus:z-10 sm:text-sm"
                placeholder="Email address"
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
                className="appearance-none rounded-none relative block w-full px-3 py-2 border border-gray-300 placeholder-gray-500 text-gray-900 rounded-b-md focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 focus:z-10 sm:text-sm"
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={loading}
              />
            </div>
          </div>

          <div className="flex items-center">
            <input
              id="auto-auth"
              name="auto-auth"
              type="checkbox"
              className="h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded"
              checked={autoAuth}
              onChange={(e) => setAutoAuth(e.target.checked)}
            />
            <label
              htmlFor="auto-auth"
              className="ml-2 block text-sm text-gray-900"
            >
              Auto Authenticate
            </label>
          </div>

          {/* <div className="flex items-center justify-between">
            <div className="flex items-center">
              <input
                id="auto-auth"
                name="auto-auth"
                type="checkbox"
                className="h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded"
                checked={getConfig().ui.auto_authenticate}
                onChange={(e) =>
                  saveConfig({ ui: { auto_authenticate: e.target.checked } })
                }
              />
              <label
                htmlFor="auto-auth"
                className="ml-2 block text-sm text-gray-900"
              >
                Auto Authenticate
              </label>
            </div>
          </div> */}

          {displayError && (
            <div className="rounded-md bg-red-50 p-4">
              <div className="flex">
                <div className="ml-3">
                  <h3 className="text-sm font-medium text-red-800">
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
              className="group relative w-full flex justify-center py-2 px-4 border border-transparent text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? "Signing in..." : "Sign in"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
