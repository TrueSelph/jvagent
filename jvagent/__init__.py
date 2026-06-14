"""jvagent - Agentive Platform built on jvspatial.

Two supported usage modes:

* **Standalone server** (CLI) — ``jvagent run`` boots jvagent's own
  jvspatial ``Server`` and HTTP surface. The default for new apps.
* **Embedded library** — host an existing jvspatial app (e.g. another
  product also built on jvspatial) calls :mod:`jvagent.embed` to mount
  jvagent's runtime in-process, sharing the host's database and
  authentication. See :mod:`jvagent.embed` for the supported surface.

Public, semver-tracked exports live here. Anything reached via
``jvagent.<internal_module>`` is implementation detail and may break
between minor versions.
"""

# Install the Logger subclass that accepts ``details=`` as a kwarg before
# any submodule's ``logging.getLogger(__name__)`` call runs. See
# ``jvagent/_logging_compat.py`` and AUDIT-INDEX §2.2.
from jvagent._logging_compat import install as _install_logging_compat

_install_logging_compat()

# Register markdown extensions in ``mimetypes`` so jvspatial's storage MIME
# allow-list accepts ``.md`` saves on hosts without libmagic or an OS mime
# entry for markdown. Must run before any file-save path. See
# ``jvagent/_mimetypes_compat.py``.
from jvagent._mimetypes_compat import install as _install_mimetypes_compat  # noqa: E402

_install_mimetypes_compat()

from jvagent import embed  # noqa: E402  -- must follow logging shim install
from jvagent.version import __version__  # noqa: E402

__all__ = ["__version__", "embed"]
