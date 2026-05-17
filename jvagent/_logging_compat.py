"""Logger compatibility shim.

jvagent calls ``logger.error(..., details={...})`` extensively across the
codebase. Stdlib :class:`logging.Logger` does not accept ``details`` as a
kwarg; it raises ``TypeError: Logger._log() got an unexpected keyword
argument 'details'``. This shim installs a :class:`Logger` subclass that
recognizes ``details=`` and routes it to ``extra["details"]`` so that
:class:`jvspatial.logging.handler.DBLogHandler` picks it up under its
documented ``extra`` contract.

Install BEFORE any ``logging.getLogger(...)`` call in jvagent — see
``jvagent/__init__.py``.
"""

import logging
from collections.abc import Mapping
from typing import Any


class JvAgentLogger(logging.Logger):
    """Stdlib-compatible :class:`Logger` that also accepts a ``details`` kwarg.

    Any ``details=`` passed to ``logger.error/warning/info/debug/critical``
    (and the underlying ``_log``) is merged into the ``extra`` dict under the
    ``"details"`` key. Caller-supplied ``extra["details"]`` wins on collision.
    All other kwargs pass through unchanged.
    """

    def _log(
        self,
        level: int,
        msg: object,
        args: Any,
        exc_info: Any = None,
        extra: Any = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        details: Any = None,
    ) -> None:
        if details is not None:
            if extra is None:
                extra = {"details": details}
            elif isinstance(extra, Mapping):
                if "details" not in extra:
                    extra = {**extra, "details": details}
        super()._log(
            level,
            msg,
            args,
            exc_info=exc_info,
            extra=extra,
            stack_info=stack_info,
            stacklevel=stacklevel,
        )


def install() -> None:
    """Install :class:`JvAgentLogger` as the default Logger class.

    Idempotent. Must be called before any ``logging.getLogger(__name__)``
    call inside jvagent — typically from ``jvagent/__init__.py`` at the very
    top of the module.

    Loggers created BEFORE this call keep their original class. That's fine:
    jvagent's own modules are imported after ``jvagent/__init__.py`` runs,
    so their module-level ``logging.getLogger(__name__)`` returns instances
    of this subclass.
    """
    if not issubclass(logging.getLoggerClass(), JvAgentLogger):
        logging.setLoggerClass(JvAgentLogger)
