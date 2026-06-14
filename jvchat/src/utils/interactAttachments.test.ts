import { describe, it, expect } from 'vitest'
import {
  attachmentsToInteractData,
  buildAttachmentPreviews,
  isImageMime,
  prepareOutgoingAttachments,
  validateAttachmentSize,
  MAX_ATTACHMENT_BYTES,
} from './interactAttachments'

describe('interactAttachments', () => {
  it('isImageMime detects image/* and extension when type empty', () => {
    expect(isImageMime('image/png', 'x')).toBe(true)
    expect(isImageMime('application/pdf', '')).toBe(false)
    expect(isImageMime('', 'pic.JPEG')).toBe(true)
  })

  it('isImageMime treats octet-stream with image extension as image (browser file picker)', () => {
    expect(isImageMime('application/octet-stream', 'avatar.jpg')).toBe(true)
    expect(isImageMime('application/octet-stream', 'document.pdf')).toBe(false)
  })

  it('validateAttachmentSize returns error when file too large', () => {
    const f = {
      name: 'big.bin',
      size: MAX_ATTACHMENT_BYTES + 1,
    } as File
    expect(validateAttachmentSize(f)).toMatch(/exceeds/)
  })

  it('routes images to image_urls and documents to whatsapp_media', async () => {
    const img = new File([Uint8Array.from([137, 80, 78, 71])], 'a.png', {
      type: 'image/png',
    })
    const pdf = new File([Uint8Array.from([37, 80, 68, 70])], 'b.pdf', {
      type: 'application/pdf',
    })

    const data = await attachmentsToInteractData([img, pdf])

    expect(data.image_urls).toHaveLength(1)
    const i0 = data.image_urls?.[0] as { base64?: string; mime_type?: string }
    expect(i0?.mime_type).toBe('image/png')
    expect(i0?.base64).toBeTruthy()

    expect(data.whatsapp_media).toHaveLength(1)
    const w0 = data.whatsapp_media?.[0] as { base64?: string; filename?: string }
    expect(w0?.filename).toBe('b.pdf')
    expect(w0?.base64).toBeTruthy()
  })

  it('routes JPEG with application/octet-stream to image_urls', async () => {
    const jpeg = new File([Uint8Array.of(0xff, 0xd8, 0xff)], 'avatar.jpg', {
      type: 'application/octet-stream',
    })
    const data = await attachmentsToInteractData([jpeg])
    expect(data.image_urls).toHaveLength(1)
    expect(data.whatsapp_media).toBeFalsy()
    const j0 = data.image_urls?.[0] as { mime_type?: string }
    expect(j0?.mime_type).toBe('image/jpeg')
  })

  it('prepareOutgoingAttachments embeds persistedDataUrl for small JPEGs (localStorage-safe)', async () => {
    const jpeg = new File([Uint8Array.of(0xff, 0xd8, 0xff)], '20260410_084350_bf9311b3.jpg', {
      type: 'application/octet-stream',
    })
    const { data, previews } = await prepareOutgoingAttachments([jpeg])
    expect(previews).toHaveLength(1)
    expect(previews[0].persistedDataUrl?.startsWith('data:image/jpeg;base64,')).toBe(true)
    expect(previews[0].previewUrl).toBeUndefined()
    expect(data.image_urls?.[0]).toMatchObject({
      mime_type: 'image/jpeg',
    })
  })

  it('buildAttachmentPreviews assigns image kind and previewUrl', () => {
    const img = new File([Uint8Array.of(0)], 'a.png', { type: 'image/png' })
    const pdf = new File([Uint8Array.of(0)], 'b.pdf', { type: 'application/pdf' })

    const previews = buildAttachmentPreviews([img, pdf])
    expect(previews[0].kind).toBe('image')
    expect(previews[0].previewUrl).toMatch(/^blob:/)
    expect(previews[1].kind).toBe('document')
    expect(previews[1].previewUrl).toBeUndefined()

    URL.revokeObjectURL(previews[0].previewUrl!)
  })
})
