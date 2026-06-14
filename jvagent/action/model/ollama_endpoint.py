"""Ollama URL helpers shared by LM and embedding actions."""


def ollama_host_root(api_endpoint: str) -> str:
    """Return host root for appending native paths like ``/api/chat``.

    Ollama docs describe the API base as ``https://ollama.com/api`` while this
    codebase stores an ``api_endpoint`` and appends ``/api/...``. If callers set
    ``api_endpoint`` to ``.../api``, paths would become ``.../api/api/chat``
    (404). Strip a single trailing ``/api`` so both conventions work.
    """
    base = (api_endpoint or "").rstrip("/")
    if base.endswith("/api"):
        return base[: -len("/api")]
    return base
