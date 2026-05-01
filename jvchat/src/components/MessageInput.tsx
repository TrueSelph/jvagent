import {
  useState,
  FormEvent,
  KeyboardEvent,
  useRef,
  useEffect,
  ChangeEvent,
  DragEvent,
  type ReactNode,
} from "react";
import { Plus } from "lucide-react";
import type { SendMessageOptions } from "../hooks/useStreaming";
import { cn } from "../lib/utils";

interface MessageInputProps {
  onSend: (message: string, options?: SendMessageOptions) => void;
  disabled?: boolean;
  placeholder?: string;
  /** Inside Thread sticky footer — omit top border */
  variant?: "default" | "thread";
  /** Shown left of send (e.g. composer tools flyout menu) */
  composerMenu?: ReactNode;
  /** Invoked when the user clicks the stop button while disabled (e.g. while streaming). */
  onStop?: () => void;
}

function filePickKey(file: File, idx: number) {
  return `${file.name}-${file.size}-${file.lastModified}-${idx}`;
}

const ArrowUpIcon = () => (
  <svg className="size-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 10l7-7m0 0l7 7m-7-7v18" />
  </svg>
);

const SquareIcon = () => (
  <svg className="size-3" fill="currentColor" stroke="none" viewBox="0 0 24 24" aria-hidden>
    <rect x="3" y="3" width="18" height="18" rx="2" />
  </svg>
);

export function MessageInput({
  onSend,
  disabled = false,
  placeholder = "Send a message...",
  variant = "default",
  composerMenu,
  onStop,
}: MessageInputProps) {
  const [value, setValue] = useState("");
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragDepthRef = useRef(0);

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

  const addDroppedFiles = (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;
    setAttachedFiles((prev) => [...prev, ...list]);
  };

  const handleDragEnter = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (disabled) return;
    dragDepthRef.current += 1;
    setIsDragging(true);
  };

  const handleDragLeave = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragDepthRef.current -= 1;
    if (dragDepthRef.current <= 0) {
      dragDepthRef.current = 0;
      setIsDragging(false);
    }
  };

  const handleDragOver = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!disabled) e.dataTransfer.dropEffect = "copy";
  };

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragDepthRef.current = 0;
    setIsDragging(false);
    if (disabled) return;
    const dropped = e.dataTransfer.files;
    if (dropped?.length) addDroppedFiles(dropped);
  };

  return (
    <form
      onSubmit={handleSubmit}
      className={cn(
        variant === "thread"
          ? "bg-background py-3 sm:py-4"
          : "p-3 sm:p-4 border-t border-zinc-200 bg-white dark:border-white/10 dark:bg-zinc-900",
      )}
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
              className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-zinc-800 px-2 py-1 text-xs text-zinc-700 dark:text-zinc-300 max-w-[min(100%,16rem)]"
            >
              <span className="truncate">{file.name}</span>
              <span className="text-zinc-400 dark:text-zinc-500" aria-hidden>
                ×
              </span>
            </button>
          ))}
        </div>
      )}
      <div
        data-dragging={isDragging ? "true" : undefined}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        className="flex flex-col rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-900 px-1 pt-2 transition-shadow data-[dragging=true]:border-dashed data-[dragging=true]:border-zinc-400 dark:data-[dragging=true]:border-zinc-500 data-[dragging=true]:bg-zinc-50 dark:data-[dragging=true]:bg-zinc-800/50 has-[textarea:focus-visible]:border-zinc-400 has-[textarea:focus-visible]:ring-2 has-[textarea:focus-visible]:ring-zinc-400/20"
      >
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          rows={1}
          className="mb-1 max-h-32 min-h-14 w-full resize-none bg-transparent px-4 pt-2 pb-3 text-sm outline-none placeholder:text-zinc-400 dark:placeholder:text-zinc-500 text-zinc-900 dark:text-zinc-50 focus-visible:ring-0"
          style={{ minHeight: "3.5rem" }}
          autoFocus
          aria-label="Message input"
        />
        <div className="mx-2 mb-2 flex items-center justify-between gap-2">
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              disabled={disabled}
              onClick={() => fileInputRef.current?.click()}
              className="flex size-[34px] shrink-0 items-center justify-center rounded-full p-1 font-semibold text-xs text-zinc-500 transition-colors duration-150 hover:bg-zinc-100 hover:text-zinc-700 dark:border-white/10 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 disabled:opacity-50"
              aria-label="Attach files"
            >
              <Plus className="size-5 stroke-[1.5px]" strokeWidth={1.5} aria-hidden />
            </button>
            {!disabled ? composerMenu : null}
          </div>

          {disabled ? (
            <button
              type="button"
              onClick={onStop}
              disabled={!onStop}
              className={cn(
                "size-8 shrink-0 rounded-full flex items-center justify-center transition-colors",
                onStop
                  ? "bg-zinc-900 text-white hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200"
                  : "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900 cursor-not-allowed",
              )}
              aria-label="Stop generating"
            >
              <SquareIcon />
            </button>
          ) : (
            <button
              type="submit"
              disabled={!canSend}
              className={cn(
                "size-8 shrink-0 rounded-full flex items-center justify-center transition-colors",
                canSend
                  ? "bg-zinc-900 text-white hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200"
                  : "cursor-not-allowed bg-zinc-200 text-zinc-500 dark:bg-zinc-700 dark:text-zinc-200",
              )}
              aria-label="Send message"
            >
              <ArrowUpIcon />
            </button>
          )}
        </div>
      </div>
    </form>
  );
}
