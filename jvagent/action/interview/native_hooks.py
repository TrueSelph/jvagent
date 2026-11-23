"""Host approval for reviewed, conversation-only interview extension code.

This is a trusted plugin boundary, not a Python sandbox. SDK tools still own
execution and mutation receipts. Skill frontmatter cannot grant this approval.
"""

import ast
import contextlib
import contextvars
import hashlib
import re
from pathlib import Path
from typing import Iterator

from .spec import InterviewSpec, load_interview_spec_from_skill
from .validators import BUILTIN_VALIDATORS

_MAX_BYTES = 256_000
_scope: contextvars.ContextVar[tuple[str, str, str | None] | None] = (
    contextvars.ContextVar("native_interview_hooks", default=None)
)


def _sources(directory: Path) -> tuple[bytes, str]:
    digest = hashlib.sha256()
    module = b""
    for relative in ("SKILL.md", "scripts/custom_tools.py"):
        path = directory / relative
        if path.resolve(strict=True) != path.absolute() or not path.is_file():
            raise ValueError("Native hook sources must be regular non-symlink files")
        with path.open("rb") as stream:
            data = stream.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            raise ValueError("Native hook source exceeds the admission bound")
        digest.update(relative.encode() + b"\0" + data + b"\0")
        if relative.endswith("custom_tools.py"):
            module = data
    return module, "sha256:" + digest.hexdigest()


def native_hook_digest(directory: Path) -> str:
    """Fingerprint the reviewed SOP and extension entrypoint for host config.

    Operators must review imports and side effects as they do other host Action
    code. This does not pin arbitrary imported libraries or grant external work.
    """
    return _sources(directory.resolve(strict=True))[1]


def approved_source(spec: InterviewSpec, digest: str | None) -> bytes:
    """Validate approval and freshness without importing the extension."""
    if (
        not isinstance(digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
    ):
        raise ValueError("Native interview hooks require a host-reviewed source digest")
    directory = Path(spec.source_dir).resolve(strict=True)
    source, actual = _sources(directory)
    if actual != digest:
        raise ValueError("Native interview hook sources changed after review")
    if load_interview_spec_from_skill(directory) != spec:
        raise ValueError("Native interview declaration changed; reload the Action")
    from dataclasses import fields

    from .hooks import BUILTIN_HOOKS

    references = {
        getattr(spec.handlers, item.name)
        for item in fields(spec.handlers)
        if getattr(spec.handlers, item.name)
    }
    for field in spec.fields:
        references.update(field.pre_processor or [])
        references.update(field.post_processor or [])
        if field.validator and field.validator not in BUILTIN_VALIDATORS:
            references.add(field.validator)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        raise ValueError("Reviewed interview hook source has invalid syntax") from None
    definitions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in references:
        definition = definitions.get(name)
        if name in BUILTIN_HOOKS or definition is None:
            raise ValueError(
                "Native hooks must name reviewed local extension functions"
            )
        args = definition.args
        if (
            args.posonlyargs
            or args.kwonlyargs
            or args.vararg
            or args.kwarg
            or len(args.args) != 1
            or args.args[0].arg != "ctx"
        ):
            raise ValueError("Native interview hooks require the single ctx interface")
    return source


@contextlib.contextmanager
def bind_native_hooks(spec: InterviewSpec, digest: str | None) -> Iterator[None]:
    """Bind server-established approval only during this Action tool call."""
    token = _scope.set((spec.name, str(Path(spec.source_dir).resolve()), digest))
    try:
        yield
    finally:
        _scope.reset(token)


def scoped_source(spec: InterviewSpec) -> tuple[bool, bytes | None, str | None]:
    """Return verified bytes for the existing loader, never reopen them to exec."""
    scope = _scope.get()
    if scope is None:
        return False, None, None
    name, directory, digest = scope
    if name != spec.name or directory != str(Path(spec.source_dir).resolve()):
        raise ValueError("Native interview hook scope changed")
    return True, approved_source(spec, digest), digest
