"""Keep artifact download credentials out of operational log projections."""

import logging
import re
from typing import Any

_DOWNLOAD = re.compile(r"(/actions/[^/\s\"'<>]+/downloads/)[A-Za-z0-9_-]+")
_NAME = "jv-download-capability-redaction"


def redact_download_links(value: Any) -> Any:
    """Return a log-only projection; canonical results retain their live links."""
    if isinstance(value, str):
        return _DOWNLOAD.sub(r"\1[capability-redacted]", value)
    if isinstance(value, dict):
        return {k: redact_download_links(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_download_links(v) for v in value]
    return value


class _CapabilityFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "jvspatial.storage.managers.proxy":
            # The dependency logs naked codes as well as URLs, including errors.
            record.msg = "File proxy service event (capability details redacted)"
            record.exc_info = None
            record.exc_text = None
        else:
            record.msg = redact_download_links(record.getMessage())
        record.args = ()
        return True


def install_download_log_filters() -> None:
    """Use public logging filters; do not patch proxy or server execution."""
    for name in ("jvspatial.storage.managers.proxy", "uvicorn.access"):
        logger = logging.getLogger(name)
        if not any(getattr(f, "name", None) == _NAME for f in logger.filters):
            logger.addFilter(_CapabilityFilter(_NAME))
