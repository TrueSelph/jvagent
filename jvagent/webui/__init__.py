"""Bundled jvchat web UI served by the ``jvagent chat`` command.

The built static assets live in ``jvagent/webui/dist/`` (produced from the
``jvchat/`` source at release time; see ``scripts/build_jvchat.py``). The dist
directory is a build artifact — it is git-ignored and only present in built
wheels / after a local build.
"""

from jvagent.webui.server import dist_dir, is_built, serve

__all__ = ["serve", "dist_dir", "is_built"]
