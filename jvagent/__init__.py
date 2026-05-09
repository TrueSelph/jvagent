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

from jvagent import embed
from jvagent.version import __version__

__all__ = ["__version__", "embed"]
