import type { InteractGuestDataPayload } from '../types/api'
import type { UserMessageAttachmentPreview } from '../types/message'

/** Default cap per request (~12 MB raw bytes before base64 inflation). */
export const MAX_ATTACHMENT_BYTES = 12 * 1024 * 1024

/**
 * Embedded image previews in localStorage (~4 chars per 3 bytes in base64).
 * Images larger than this only get ephemeral blob previews for the active session.
 */
export const MAX_PERSIST_IMAGE_PREVIEW_BYTES = 2 * 1024 * 1024

async function readBlobAsArrayBuffer(blob: Blob): Promise<ArrayBuffer> {
  const fn = (blob as Blob & { arrayBuffer?: () => Promise<ArrayBuffer> }).arrayBuffer
  if (typeof fn === 'function') {
    try {
      return await fn.call(blob)
    } catch {
      /* fall through */
    }
  }
  return new Response(blob).arrayBuffer()
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  const chunk = 0x8000
  let binary = ''
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(
      null,
      bytes.subarray(i, i + chunk) as unknown as number[]
    )
  }
  return btoa(binary)
}

/** Guess MIME from extension for outbound API payloads and data: URLs when the browser lied. */
export function guessImageMimeFromFileName(fileName: string): string | null {
  const dot = /\.([^.]+)$/i.exec(fileName)
  if (!dot) return null
  const ext = dot[1].toLowerCase()
  const map: Record<string, string> = {
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    png: 'image/png',
    gif: 'image/gif',
    webp: 'image/webp',
    bmp: 'image/bmp',
    svg: 'image/svg+xml',
    ico: 'image/x-icon',
    avif: 'image/avif',
    heic: 'image/heic',
    heif: 'image/heif',
  }
  return map[ext] ?? null
}

/** Normalize MIME for API + multimodal after browser reports octet-stream for real images. */
export function normalizeMimeForImageEntry(mimeRaw: string, fileName: string): string {
  const trimmed = mimeRaw.trim()
  if (!trimmed) {
    return guessImageMimeFromFileName(fileName) ?? 'image/jpeg'
  }
  const lower = trimmed.toLowerCase()
  if (lower.startsWith('image/')) {
    return trimmed
  }
  if (
    isImageMime(mimeRaw, fileName) &&
    (lower === 'application/octet-stream' || lower === 'binary/octet-stream')
  ) {
    return guessImageMimeFromFileName(fileName) ?? 'image/jpeg'
  }
  return trimmed
}

/** Classify MIME: treat `image/*` as image; trust known extensions when MIME is missing or generic. */
export function isImageMime(mime: string, fileName: string): boolean {
  const m = mime.trim().toLowerCase()
  if (m.startsWith('image/')) return true

  const lower = fileName.toLowerCase()
  const extLooksImage = /\.(png|jpe?g|gif|webp|bmp|svg|ico|avif|heic|heif)$/i.test(lower)
  if (!m) return extLooksImage

  if (
    extLooksImage &&
    (m === 'application/octet-stream' || m === 'binary/octet-stream')
  ) {
    return true
  }

  return false
}

export function validateAttachmentSize(file: File, maxBytes = MAX_ATTACHMENT_BYTES): string | null {
  if (file.size > maxBytes) {
    return `File "${file.name}" exceeds ${Math.round(maxBytes / (1024 * 1024))} MB.`
  }
  return null
}

/** API payload + previews with persistedDataUrl for images (stored in saveMessages/localStorage). */
export async function prepareOutgoingAttachments(files: File[]): Promise<{
  data: InteractGuestDataPayload
  previews: UserMessageAttachmentPreview[]
}> {
  const image_urls: NonNullable<InteractGuestDataPayload['image_urls']> = []
  const whatsapp_media: NonNullable<InteractGuestDataPayload['whatsapp_media']> = []
  const previews: UserMessageAttachmentPreview[] = []

  for (const file of files) {
    const err = validateAttachmentSize(file)
    if (err) throw new Error(err)

    const buf = await readBlobAsArrayBuffer(file)
    const base64 = arrayBufferToBase64(buf)

    let mimeRaw = file.type?.trim() || ''
    const isImg = isImageMime(mimeRaw, file.name)
    const mimeForPayload = isImg
      ? normalizeMimeForImageEntry(mimeRaw, file.name)
      : mimeRaw || 'application/octet-stream'

    const entry = {
      base64,
      mime_type: mimeForPayload,
      filename: file.name,
    }

    if (isImg) {
      image_urls.push(entry)

      let persistedDataUrl: string | undefined
      let previewUrl: string | undefined
      if (buf.byteLength <= MAX_PERSIST_IMAGE_PREVIEW_BYTES) {
        persistedDataUrl = `data:${mimeForPayload};base64,${base64}`
      } else if (typeof URL.createObjectURL === 'function') {
        previewUrl = URL.createObjectURL(file)
      }

      previews.push({
        name: file.name,
        kind: 'image',
        ...(persistedDataUrl ? { persistedDataUrl } : {}),
        ...(previewUrl ? { previewUrl } : {}),
      })
    } else {
      whatsapp_media.push(entry)
      previews.push({
        name: file.name,
        kind: 'document',
      })
    }
  }

  const data: InteractGuestDataPayload = {}
  if (image_urls.length) data.image_urls = image_urls
  if (whatsapp_media.length) data.whatsapp_media = whatsapp_media

  return { data, previews }
}

export async function attachmentsToInteractData(
  files: File[]
): Promise<InteractGuestDataPayload> {
  const { data } = await prepareOutgoingAttachments(files)
  return data
}

/** @deprecated Prefer prepareOutgoingAttachments; kept for isolated tests */
export function buildAttachmentPreviews(files: File[]): UserMessageAttachmentPreview[] {
  return files.map((file) => {
    const mime = file.type?.trim() || ''
    const kind = isImageMime(mime, file.name) ? 'image' : 'document'
    let previewUrl: string | undefined
    if (kind === 'image' && typeof URL.createObjectURL === 'function') {
      previewUrl = URL.createObjectURL(file)
    }
    return { name: file.name, kind, previewUrl }
  })
}
