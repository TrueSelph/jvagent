import { useState, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import { apiClient } from "../config/api";
import { useTheme } from "../context/ThemeContext";
import { UserMemoryResponse } from "../types/api";

interface MemoryViewerProps {
  agentId: string;
  onClose: () => void;
}

export function MemoryViewer({ agentId, onClose }: MemoryViewerProps) {
  const { theme } = useTheme();
  const dark = theme === "dark";
  const [memoryData, setMemoryData] = useState<UserMemoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);

  const fetchMemory = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiClient.getMyMemory(agentId);
      setMemoryData(data);

      // Auto-select first category if none selected
      const categories = Object.keys(data.memory || {});
      if (categories.length > 0 && !selectedCategory) {
        setSelectedCategory(categories[0]);
      }
    } catch (err: any) {
      console.error("Failed to fetch memory:", err);
      setError(err.message || "Failed to load memory");
    } finally {
      setLoading(false);
    }
  }, [agentId, selectedCategory]);

  useEffect(() => {
    fetchMemory();
  }, [fetchMemory]);

  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [onClose]);

  const categories = Object.keys(memoryData?.memory || {});

  const content = (
    <div
      className={`rounded-lg shadow-xl w-full h-full max-w-[95vw] max-h-[95vh] flex flex-col border ${
        dark ? "bg-zinc-900 border-zinc-700 text-zinc-100" : "bg-white border-zinc-200"
      }`}
      onClick={(e) => e.stopPropagation()}
    >
      <div
        className={`flex-shrink-0 border-b px-4 sm:px-6 py-4 flex items-center justify-between ${
          dark ? "border-zinc-700" : "border-zinc-200"
        }`}
      >
        <div className="flex items-center gap-3">
          <div className={`p-2 rounded-lg ${dark ? "bg-zinc-500/20 text-zinc-400" : "bg-zinc-100 text-zinc-600"}`}>
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
            </svg>
          </div>
          <h2 className={`text-xl sm:text-2xl font-semibold ${dark ? "text-zinc-100" : "text-zinc-900"}`}>
            Long-Term Memory
          </h2>
        </div>
        <button
          onClick={onClose}
          className={`p-2 rounded-lg transition-colors ${
            dark ? "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-700" : "text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100"
          }`}
          aria-label="Close"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Left: Categories sidebar */}
        <div className={`flex-shrink-0 w-64 sm:w-72 border-r overflow-y-auto ${
          dark ? "border-zinc-700 bg-zinc-800/50" : "border-zinc-200 bg-zinc-50"
        }`}>
          {loading && !memoryData ? (
            <div className="flex items-center justify-center py-8">
              <div className={`animate-spin rounded-full h-8 w-8 border-b-2 ${dark ? "border-zinc-400" : "border-zinc-600"}`} />
            </div>
          ) : error ? (
            <p className={`px-4 py-4 text-sm ${dark ? "text-red-400" : "text-red-600"}`}>{error}</p>
          ) : categories.length === 0 ? (
            <p className={`px-4 py-4 text-sm ${dark ? "text-zinc-400" : "text-zinc-500"}`}>No memory data yet.</p>
          ) : (
            <div className="py-2">
              {categories.map((catKey) => {
                const cat = memoryData?.memory[catKey];
                return (
                  <button
                    key={catKey}
                    onClick={() => setSelectedCategory(catKey)}
                    className={`w-full text-left px-4 py-3 text-sm transition-all border-l-4 ${
                      selectedCategory === catKey
                        ? dark
                          ? "bg-zinc-600/20 text-zinc-300 border-zinc-500"
                          : "bg-zinc-50 text-zinc-700 border-zinc-600"
                        : dark
                        ? "hover:bg-zinc-700/80 text-zinc-400 border-transparent hover:text-zinc-200"
                        : "hover:bg-zinc-100 text-zinc-600 border-transparent hover:text-zinc-900"
                    }`}
                  >
                    <div className="font-medium truncate">{cat?.title}</div>
                    {cat?.updated_at && (
                      <div className="text-[10px] opacity-60 mt-0.5">
                        Updated {new Date(cat.updated_at).toLocaleDateString()}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Right: Content area */}
        <div className={`flex-1 overflow-y-auto p-6 sm:p-8 ${dark ? "bg-zinc-900" : "bg-white"}`}>
          {!selectedCategory ? (
            <div className="h-full flex flex-col items-center justify-center text-center">
              <div className={`mb-4 p-4 rounded-full ${dark ? "bg-zinc-800" : "bg-zinc-50"}`}>
                <svg className={`w-12 h-12 ${dark ? "text-zinc-600" : "text-zinc-300"}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                </svg>
              </div>
              <p className={`text-lg font-medium ${dark ? "text-zinc-400" : "text-zinc-500"}`}>
                Select a category to view details.
              </p>
            </div>
          ) : (
            <div className="max-w-3xl mx-auto animate-fadeIn">
              <div className="mb-6 flex items-center justify-between pb-4 border-b border-zinc-200 dark:border-zinc-800">
                <h1 className={`text-2xl font-bold ${dark ? "text-white" : "text-zinc-900"}`}>
                  {memoryData?.memory[selectedCategory]?.title}
                </h1>
                {memoryData?.memory[selectedCategory]?.updated_at && (
                  <span className={`text-xs ${dark ? "text-zinc-500" : "text-zinc-400"}`}>
                    Last updated: {new Date(memoryData.memory[selectedCategory].updated_at!).toLocaleString()}
                  </span>
                )}
              </div>

              <div className={`prose ${dark ? "prose-invert" : ""} max-w-none`}>
                <ReactMarkdown
                  components={{
                    h1: ({node, ...props}) => <h1 className="text-xl font-bold mb-4 mt-6" {...props} />,
                    h2: ({node, ...props}) => <h2 className="text-lg font-bold mb-3 mt-5" {...props} />,
                    p: ({node, ...props}) => <p className="mb-4 leading-relaxed opacity-90" {...props} />,
                    ul: ({node, ...props}) => <ul className="list-disc pl-5 mb-4 space-y-1" {...props} />,
                    li: ({node, ...props}) => <li className="opacity-90" {...props} />,
                    code: ({node, ...props}) => (
                      <code className={`px-1.5 py-0.5 rounded text-sm ${dark ? "bg-zinc-800 text-zinc-300" : "bg-zinc-100 text-zinc-600"}`} {...props} />
                    ),
                  }}
                >
                  {memoryData?.memory[selectedCategory]?.content || "No content available."}
                </ReactMarkdown>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 ${dark ? "bg-black/80 backdrop-blur-sm" : "bg-black/60 backdrop-blur-sm"}`}
      onClick={(e) => {
        if (e.target === e.currentTarget && onClose) onClose();
      }}
    >
      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-fadeIn {
          animation: fadeIn 0.3s ease-out forwards;
        }
      `}</style>
      {content}
    </div>
  );
}
