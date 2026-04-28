import {
  useState,
  FormEvent,
  KeyboardEvent,
  useRef,
  useEffect,
  ChangeEvent,
} from "react";
import type { SendMessageOptions } from "../hooks/useStreaming";

interface MessageInputProps {
  onSend: (message: string, options?: SendMessageOptions) => void;
  disabled?: boolean;
  placeholder?: string;
}

function filePickKey(file: File, idx: number) {
  return `${file.name}-${file.size}-${file.lastModified}-${idx}`;
}

export function MessageInput({
  onSend,
  disabled = false,
  placeholder = "Type your message...",
}: MessageInputProps) {
  const [value, setValue] = useState("");
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [value]);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!disabled) {
      textareaRef.current?.focus();
    }
  }, [disabled]);

  const canSend =
    (value.trim() || attachedFiles.length > 0) && !disabled;

  const clearAttachments = () => {
    setAttachedFiles([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSend) return;
    const text = value.trim();
    const filesOpt: SendMessageOptions =
      attachedFiles.length > 0 ? { files: [...attachedFiles] } : {};
    const hasOpts = attachedFiles.length > 0;

    if (hasOpts || text) {
      onSend(text || "", hasOpts ? filesOpt : undefined);
      setValue("");
      clearAttachments();
      textareaRef.current?.focus();
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const onPickFiles = (e: ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files || []);
    e.target.value = "";
    if (picked.length === 0) return;
    setAttachedFiles((prev) => [...prev, ...picked]);
  };

  const removeChip = (idx: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="border-t border-gray-200 dark:border-slate-700 p-3 sm:p-4 bg-white dark:bg-slate-900"
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        aria-hidden
        onChange={onPickFiles}
      />
      {attachedFiles.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-2">
          {attachedFiles.map((file, idx) => (
            <button
              type="button"
              key={filePickKey(file, idx)}
              onClick={() => removeChip(idx)}
              title="Remove"
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-slate-600 bg-gray-50 dark:bg-slate-800 px-2 py-1 text-xs text-gray-800 dark:text-slate-200 max-w-[min(100%,16rem)]"
            >
              <span className="truncate">{file.name}</span>
              <span className="text-gray-400 dark:text-slate-500" aria-hidden>
                ×
              </span>
            </button>
          ))}
        </div>
      )}
      <div className="flex items-end gap-2 sm:gap-3">
        <button
          type="button"
          disabled={disabled}
          onClick={() => fileInputRef.current?.click()}
          className="flex-shrink-0 p-2.5 rounded-lg border border-gray-300 dark:border-slate-600 text-gray-600 dark:text-slate-300 hover:bg-gray-50 dark:hover:bg-slate-800 disabled:opacity-50"
          aria-label="Attach files"
          title="Attach images or documents"
        >
          <svg
            className="w-5 h-5"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"
            />
          </svg>
        </button>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          rows={1}
          className="flex-1 resize-none rounded-lg border border-gray-300 dark:border-slate-600 px-3 sm:px-4 py-2 sm:py-3 text-sm sm:text-base bg-white dark:bg-slate-800 dark:[color-scheme:dark] text-gray-900 dark:text-slate-100 placeholder-gray-500 dark:placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ minHeight: "44px", maxHeight: "200px" }}
        />
        <button
          type="submit"
          disabled={!canSend}
          className="px-4 sm:px-6 py-2 sm:py-3 bg-indigo-600 dark:bg-indigo-500 text-white rounded-lg hover:bg-indigo-700 dark:hover:bg-indigo-600 focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors touch-manipulation text-sm sm:text-base font-medium"
        >
          {disabled ? "Sending..." : "Send"}
        </button>
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400 mt-2 hidden sm:block">
        Press Enter to send, Shift+Enter for newline. Attach files with the paperclip.
      </p>
    </form>
  );
}
