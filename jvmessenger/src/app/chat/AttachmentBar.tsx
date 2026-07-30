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
  const { config, getToken, ensureSession } = useChatServices();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || []);
      e.target.value = "";
      if (!files.length) return;
      setBusy(true);
      setError(null);
      let failed = 0;
      try {
        let token = getToken();
        if (!token) token = (await ensureSession()) ?? undefined;
        if (!token) {
          setError("Session unavailable — set JVSPATIAL_JWT_SECRET_KEY");
          return;
        }
        for (const file of files) {
          const out = await uploadFile(config.agentUrl, config.agentId, token, file);
          if (out) onUploaded(out);
          else failed += 1;
        }
      } finally {
        setBusy(false);
      }
      // Surface upload failures instead of failing silently.
      if (failed) setError(failed === 1 ? "Upload failed" : `${failed} uploads failed`);
    },
    [config, getToken, ensureSession, onUploaded]
  );

  const onPick = useCallback(async () => {
    if (busy) return;
    setError(null);
    if (!getToken()) {
      setBusy(true);
      try {
        const tok = await ensureSession();
        if (!tok) {
          setError("Session unavailable — set JVSPATIAL_JWT_SECRET_KEY");
          return;
        }
      } finally {
        setBusy(false);
      }
    }
    inputRef.current?.click();
  }, [busy, getToken, ensureSession]);

  return (
    <div className="flex items-center gap-1.5">
      <TooltipIconButton
        tooltip={busy ? "Preparing upload…" : "Attach a file"}
        onClick={() => void onPick()}
        disabled={busy}
      >
        {busy ? <Loader2Icon className="animate-spin" /> : <PaperclipIcon />}
      </TooltipIconButton>
      {error && <span className="text-destructive text-xs">{error}</span>}
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        onChange={onChange}
        accept="image/*,application/pdf,text/plain,text/csv"
      />
    </div>
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
