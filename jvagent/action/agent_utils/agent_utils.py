"""Agent Utils action."""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


class AgentUtils(Action):
    """Action for agent utilities and power user controls."""

    async def list_interactions(
        self, limit: int = 100, offset: int = 0
    ) -> List[Dict]:
        """List interaction logs from the configured logs directory.

        AUDIT-actions XC-19: hardened against three previous footguns:

        1. **Pagination** (``limit``/``offset``) — previously read every
           file in the directory regardless of size.
        2. **Path containment** — ``JVSPATIAL_LOG_DB_NAME`` is now
           validated to resolve under either the app's
           ``file_storage_root_dir`` or the current working directory.
           Outside those, the request is refused with an empty list so
           a misconfigured / hostile env can't traverse the filesystem.
        3. **Sync I/O off the event loop** — file reads happen in a
           worker thread.

        Args:
            limit: Max number of interactions to return (capped at 1000).
            offset: Skip the first N most-recent interactions.

        Returns:
            List of interaction log contents (dictionaries).
        """
        import asyncio

        try:
            limit_int = max(0, min(int(limit), 1000))
        except Exception:
            limit_int = 100
        try:
            offset_int = max(0, int(offset))
        except Exception:
            offset_int = 0

        log_db_name = os.environ.get("JVSPATIAL_LOG_DB_NAME", "jvagent_logs")
        if not os.path.isabs(log_db_name):
            log_db_name = os.path.abspath(log_db_name)

        log_path = Path(log_db_name) / "object"

        # Containment check: refuse any path that doesn't sit under the
        # app's file_storage_root_dir OR the current working directory.
        # Defense-in-depth even though this endpoint is admin-only.
        safe_roots = []
        try:
            from jvagent.core.app import App

            app = await App.get()
            if app and app.file_storage_root_dir:
                safe_roots.append(
                    Path(app.file_storage_root_dir).resolve()
                )
        except Exception:
            pass
        safe_roots.append(Path.cwd().resolve())

        resolved = log_path.resolve()
        if not any(
            str(resolved).startswith(str(root)) for root in safe_roots
        ):
            logger.warning(
                "list_interactions: refusing to read %s (outside %s)",
                resolved,
                [str(r) for r in safe_roots],
            )
            return []

        if not log_path.exists():
            logger.warning(f"Log path does not exist: {log_path}")
            fallback_path = Path("jvspatial_logs").resolve() / "object"
            if fallback_path.exists() and any(
                str(fallback_path).startswith(str(root)) for root in safe_roots
            ):
                logger.info(f"Fallback: Found logs at {fallback_path}")
                log_path = fallback_path
            else:
                return []

        if not log_path.is_dir():
            logger.warning(f"Log path is not a directory: {log_path}")
            return []

        def _read_slice() -> List[Dict]:
            files_sorted = sorted(
                [f for f in log_path.iterdir() if f.is_file()],
                key=lambda f: f.stat().st_ctime,
                reverse=True,
            )
            chosen = files_sorted[offset_int : offset_int + limit_int]
            out: List[Dict] = []
            for file_path in chosen:
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        out.append(json.load(f))
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse JSON from {file_path}")
                except Exception as e:
                    logger.warning(f"Error reading {file_path}: {e}")
            return out

        return await asyncio.to_thread(_read_slice)
