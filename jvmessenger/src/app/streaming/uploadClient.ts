/** Client for the public messenger upload endpoint (multipart → stored URL). */

export interface UploadedAttachment {
  url: string;
  mime_type: string;
  filename: string;
  size: number;
}

export async function uploadFile(
  agentUrl: string,
  agentId: string,
  token: string,
  file: File
): Promise<UploadedAttachment | null> {
  const base = agentUrl.replace(/\/+$/, "");
  const urls = [
    `${base}/api/agents/${agentId}/uploads`,
    `${base}/agents/${agentId}/uploads`,
  ];
  const form = new FormData();
  form.append("file", file, file.name);
  for (const url of urls) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "X-Session-Token": token },
        body: form,
      });
      if (res.status === 404) continue;
      if (!res.ok) return null;
      const data = await res.json();
      const out = (data?.data ?? data) as UploadedAttachment;
      return out?.url ? out : null;
    } catch {
      return null;
    }
  }
  return null;
}
