"""Optional image transcoding for Meta WhatsApp image messages (JPEG/PNG only)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Tuple

logger = logging.getLogger(__name__)

_META_NATIVE_IMAGE_MIMES = frozenset({"image/jpeg", "image/jpg", "image/png"})
_NEEDS_JPEG_MIMES = frozenset(
    {
        "image/avif",
        "image/webp",
        "image/heic",
        "image/heif",
        "application/octet-stream",
    }
)


def ensure_meta_image_jpeg(file_bytes: bytes, mime: str = "") -> Tuple[bytes, str]:
    """Return JPEG bytes when Meta rejects the source mime; else pass through.

    Meta media upload accepts image/jpeg and image/png for chat images (not AVIF/WebP).
    Uses ``ffmpeg`` on PATH when conversion is needed (temp files — AVIF often fails
    on stdin pipes).
    """
    if not file_bytes:
        return b"", mime or ""
    clean = (mime or "").split(";", 1)[0].strip().lower()
    if clean in _META_NATIVE_IMAGE_MIMES:
        return file_bytes, clean if clean != "image/jpg" else "image/jpeg"

    if clean and clean not in _NEEDS_JPEG_MIMES and not clean.startswith("image/"):
        return file_bytes, clean

    if not shutil.which("ffmpeg"):
        logger.warning(
            "ffmpeg not on PATH; cannot convert %s to JPEG for Meta image upload",
            clean or "unknown",
        )
        return file_bytes, clean or "application/octet-stream"

    suffix = ".avif"
    if "webp" in clean:
        suffix = ".webp"
    elif "heic" in clean or "heif" in clean:
        suffix = ".heic"
    elif "png" in clean:
        suffix = ".png"

    in_path = ""
    out_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fin:
            fin.write(file_bytes)
            in_path = fin.name
        out_fd, out_path = tempfile.mkstemp(suffix=".jpg")
        os.close(out_fd)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            in_path,
            "-frames:v",
            "1",
            "-update",
            "1",
            out_path,
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if proc.returncode != 0 or not os.path.isfile(out_path):
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
            logger.warning(
                "Meta image→JPEG transcoding failed (exit %s): %s",
                proc.returncode,
                err,
            )
            return file_bytes, clean or "application/octet-stream"

        with open(out_path, "rb") as fout:
            jpeg = fout.read()
        if not jpeg:
            return file_bytes, clean or "application/octet-stream"
        return jpeg, "image/jpeg"
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Meta image→JPEG transcoding failed: %s", exc)
        return file_bytes, clean or "application/octet-stream"
    finally:
        for path in (in_path, out_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
