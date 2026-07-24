/** Attachment picker + pending-upload chips (multipart upload endpoint). */

import { useCallback, useRef, useState } from "react";
import { PaperclipIcon, XIcon, Loader2Icon } from "lucide-react";
import { uploadFile, type UploadedAttachment } from "../streaming/uploadClient";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { useChatServices } from "./context";

export function AttachmentButton({
  onUploaded,
}: {
  onUploaded: (a: UploadedAttachment) => void;
}) {
  const { config, getToken } = useChatServices();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);

  const onChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || []);
      e.target.value = "";
      const token = getToken();
      if (!token || !files.length) return;
      setBusy(true);
      try {
        for (const file of files) {
          const out = await uploadFile(config.agentUrl, config.agentId, token, file);
          if (out) onUploaded(out);
        }
      } finally {
        setBusy(false);
      }
    },
    [config, getToken, onUploaded]
  );

  const disabled = busy || !getToken();
  return (
    <>
      <TooltipIconButton
        tooltip={disabled ? "Send a message first to enable uploads" : "Attach a file"}
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
      >
        {busy ? <Loader2Icon className="animate-spin" /> : <PaperclipIcon />}
      </TooltipIconButton>
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        onChange={onChange}
        accept="image/*,application/pdf,text/plain,text/csv"
      />
    </>
  );
}

export function AttachmentChips({
  attachments,
  onRemove,
}: {
  attachments: UploadedAttachment[];
  onRemove: (url: string) => void;
}) {
  if (!attachments.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {attachments.map((a) => (
        <span
          key={a.url}
          className="bg-muted text-foreground flex max-w-[180px] items-center gap-1.5 truncate rounded-lg border py-1 pr-1 pl-2.5 text-xs"
        >
          <span className="truncate">{a.filename}</span>
          <button
            type="button"
            onClick={() => onRemove(a.url)}
            aria-label={`Remove ${a.filename}`}
            className="text-muted-foreground hover:text-foreground inline-flex"
          >
            <XIcon className="size-3" />
          </button>
        </span>
      ))}
    </div>
  );
}
