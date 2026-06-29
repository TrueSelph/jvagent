"""Optional audio transcoding for Meta WhatsApp voice notes (OGG/OPUS)."""

import logging
import shutil
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def transcode_mp3_to_ogg_opus(mp3_bytes: bytes) -> Optional[bytes]:
    """Transcode MP3 bytes to OGG/OPUS for Meta native voice-note bubbles.

    Requires ``ffmpeg`` on PATH. Returns None when ffmpeg is unavailable or
    transcoding fails (caller should fall back to sending MP3 as a plain audio file).
    """
    if not mp3_bytes:
        return None
    if not shutil.which("ffmpeg"):
        logger.debug("ffmpeg not on PATH; skipping MP3→OGG transcoding for Meta voice")
        return None

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-c:a",
        "libopus",
        "-b:a",
        "32k",
        "-f",
        "ogg",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=mp3_bytes,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Meta voice MP3→OGG transcoding failed: %s", exc)
        return None

    if proc.returncode != 0 or not proc.stdout:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
        logger.warning(
            "Meta voice MP3→OGG transcoding failed (exit %s): %s",
            proc.returncode,
            err,
        )
        return None

    return proc.stdout
