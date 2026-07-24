"""Bundled jvmessenger embeddable popup chat served by ``jvagent messenger``.

The built static assets live in ``jvagent/messenger/dist/`` (produced from the
``jvmessenger/`` source at release time; see ``scripts/build_jvmessenger.py``). The
dist directory is a build artifact — it is git-ignored and only present in built
wheels / after a local build.
"""

from jvagent.messenger.server import DEFAULT_FRAME_ANCESTORS, dist_dir, is_built, serve

__all__ = ["serve", "dist_dir", "is_built", "DEFAULT_FRAME_ANCESTORS"]
