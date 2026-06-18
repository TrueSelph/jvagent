"""Invariant 6 (ADR-0026): no consumer domain vocabulary lives in jvagent core.

The work-stack orchestration is a reusable service. A consumer app (zoon, or any
other) wires its meaning through the precondition registry + declarative
``requires-tasks`` frontmatter — never by name inside the framework. This guard
greps the shipped ``jvagent/`` package for known consumer terms; a match means a
domain concept leaked into core and must be pulled back behind a seam.
"""

import pathlib
import re

# Terms that belong to a specific consumer (zoon), never to the framework.
# Generic words ("onboarding", "whatsapp", "interview") are intentionally absent —
# they are legitimate framework concepts. Only consumer-specific names are banned.
BANNED = (
    "zoon",
    "pre_alert",
    "prealert",
    "quotation",
    "account_session",
    "account_known",
    "identity_verification",
    "onboarding_interview",
)

_PKG_ROOT = pathlib.Path(__file__).resolve().parents[1] / "jvagent"
_PATTERN = re.compile("|".join(re.escape(t) for t in BANNED), re.IGNORECASE)


def test_no_consumer_domain_vocabulary_in_core():
    offenders = []
    for path in _PKG_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _PATTERN.search(line):
                offenders.append(
                    f"{path.relative_to(_PKG_ROOT.parent)}:{lineno}: {line.strip()}"
                )
    assert not offenders, (
        "Consumer domain vocabulary leaked into jvagent/ (ADR-0026 invariant 6). "
        "Route it through register_precondition + requires-tasks instead:\n"
        + "\n".join(offenders)
    )
