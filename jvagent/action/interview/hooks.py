"""Hook loading, the ``ctx`` interface, and dispatch.

Skills implement validators, pre/post processors, handlers, and skill tools as
functions in ``scripts/custom_tools.py``. Every such function takes a single
``ctx`` (:class:`HookExecutionContext`) — the one place it reads its inputs and
furnishes user-facing output. This module owns:

- the ``ctx`` object and its output vocabulary (``ctx.say`` / ``ctx.tool_response``
  / ``ctx.call_tool`` / ``ctx.no_session`` / ``ctx.valid`` / ``ctx.invalid``);
- the low-level directive-framing primitives (``user_directive`` etc.) — **internal**:
  used by the engine and by ``ctx``; skills never import them;
- ``call_hook``, which injects ``ctx``, runs the hook, and folds any ``ctx.say``
  statements into the result's ``response_directive`` (the single channel to the
  user — see the delivery note on :meth:`HookExecutionContext.say`).
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from jvagent.action.reply.reply_action import DIRECTIVE_GUIDANCE_MARKER

from .session import InterviewSession
from .spec import FieldDef, InterviewSpec
from .validators import get_validator

logger = logging.getLogger(__name__)

_module_cache: Dict[str, Any] = {}

# Model-only composition guidance lives after this marker in a directive. The
# compose model reads it (it steers rendering); ReplyAction's literal-relay fast
# path drops it so interview internals never reach the user.
_G = DIRECTIVE_GUIDANCE_MARKER

# ── Lifecycle phases ─────────────────────────────────────────────────
# The run a hook fires on. ``ctx.say`` only records on SAY_PHASES; on the others
# (the pre-processor STORE re-run, branch eval, validation) it is inert, so a
# prompt-builder that re-executes while the answer is stored can't bleed the
# previous prompt onto the next turn. Validators surface re-asks via
# ``ctx.invalid``, not ``ctx.say``.
ACTIVATION_PHASE = "activation"  # pre-processor building the field prompt
STORE_PHASE = "store"  # pre-processor re-run while storing the answer
POST_PHASE = "post"  # post-processor after a successful store
VALIDATE_PHASE = "validate"  # custom validator
BRANCH_PHASE = "branch"  # branch-condition evaluation
REVIEW_PHASE = "review"
COMPLETE_PHASE = "complete"
RESET_PHASE = "reset"
CANCEL_PHASE = "cancel"
TOOL_PHASE = "tool"  # LLM-invoked skill tool

SAY_PHASES = frozenset(
    {
        ACTIVATION_PHASE,
        POST_PHASE,
        VALIDATE_PHASE,  # a re-ask on validation failure is this turn's reply
        REVIEW_PHASE,
        COMPLETE_PHASE,
        RESET_PHASE,
        CANCEL_PHASE,
        TOOL_PHASE,
    }
)

# Keys forwarded from pre/post processor hook results to the LLM.
HOOK_RESULT_KEYS = (
    "ok",
    "status",
    "value",
    "error",
    "error_code",
    "system_message",
    "response_directive",
    "note",
    "next_tool",
    "interview_complete",
)


# ── Internal directive-framing primitives (engine + ctx; never imported by skills) ──


def interview_tool_response(
    *, ok: Optional[bool] = None, status: str, **data: Any
) -> str:
    """Serialize a tool response envelope; None values are dropped."""
    if ok is None:
        ok = status not in ("error", "validation_failed")
    payload: Dict[str, Any] = {"ok": ok, "status": status}
    payload.update({k: v for k, v in data.items() if v is not None})
    return json.dumps(payload)


def slim_hook_entry(tool: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Build a slim pre/post hook result entry for LLM consumption."""
    entry: Dict[str, Any] = {"tool": tool, "ok": parsed.get("ok", True)}
    for key in HOOK_RESULT_KEYS:
        if key in parsed:
            entry[key] = parsed[key]
    return entry


def user_directive(question: str, *, note: str = "") -> str:
    """Single-action directive: the model replies with one question.

    ``note`` is MODEL-FACING guidance placed AFTER the guidance marker — it steers
    the compose model but is never relayed verbatim. Anything the user must read
    belongs in ``question``.
    """
    guidance = (
        "You may paraphrase slightly but keep the same intent. "
        "Do not ask for other information in this reply."
    )
    if note:
        guidance = f"{guidance} {note}"
    return f"Tell the user: {question}{_G}{guidance}"


def with_hint(prompt: str, hint: str = "") -> str:
    """Frame a field ``hint`` into the question's USER-FACING text.

    A ``hint`` is plain answer-guidance for the user — how to answer this question
    (e.g. "enter your first, last, and any other names"; an accepted format; that a
    field is optional). It is delivered WITH the question so the agent instructs
    the user on the intended answer. It must be user-facing content (the compose
    model delivers a directive's parts IN FULL, in the agent's voice; the
    orchestrator strips model-only after-marker guidance before composing). Keep it
    to one line, non-redundant with ``prompt``, so the model weaves it into a single
    natural prompt. The same ``hint`` is also surfaced in ``field_reference`` /
    ``next_field`` so the model can answer the user's clarifications about the field.
    """
    if not hint:
        return prompt
    return f"{prompt}\n\n{hint}"


def field_prompt_directive(prompt: str, hint: str = "") -> str:
    """Frame a field's question, folding a field-level ``hint`` into the prompt."""
    return user_directive(with_hint(prompt, hint))


def user_followup_directive(message: str, follow_up_question: str) -> str:
    """Sidebar note plus the next interview question in one user-facing reply."""
    return (
        f"Tell the user: {message}\n\n{follow_up_question}"
        f"{_G}The text above is a note followed by the next question — deliver both, "
        "in that order. You may paraphrase but keep both the note and the question."
    )


def user_directive_then_tool(message: str, next_tool: str) -> str:
    """Sidebar note when no further questions remain; chain a tool in the same turn."""
    return (
        f"Tell the user: {message}"
        f"{_G}You may paraphrase slightly but keep the same intent. "
        f"Then call {next_tool}."
    )


def validation_guidance_directive(error: str, *, question_text: str = "") -> str:
    """Build a single user-facing re-ask from a validator error message."""
    raw = (error or "").strip()
    lower = raw.lower()
    prefixed = lower.startswith("tell the user:") or lower.startswith("ask:")
    err = raw.split(":", 1)[1].strip() if prefixed else raw
    self_contained = prefixed or err.endswith((".", "!", "?"))
    if question_text and not self_contained:
        return user_directive(f"{err} {question_text}".strip())
    return user_directive(err)


def review_confirmation_directive(
    summary: str,
    *,
    preamble: str = "Please review your details before we finalize.",
) -> str:
    """Confirmation-step directive — not completion."""
    summary_block = f"\n\n{summary}" if summary else ""
    return (
        f"Tell the user: {preamble}{summary_block}\n\n"
        "Ask whether everything looks correct and whether they want to confirm, "
        "and if they want changes, ask what to update."
        f"{_G}This is a confirmation step only — the process is NOT complete yet. "
        "Do NOT say the process is complete or that any account or record has been created. "
        "Do NOT call interview__complete until they explicitly confirm. "
        "Do NOT call interview__review again."
    )


def auto_confirm_directive(summary: str, *, preamble: str = "") -> str:
    """Review summary shown; chain interview__complete without user confirmation."""
    summary_block = f"\n\n{summary}" if summary else ""
    intro = (preamble or "Here is a summary of what was collected.").strip()
    return (
        f"Tell the user: {intro}{summary_block}"
        f"{_G}Do not ask whether everything looks correct. "
        "Call interview__complete now in this same turn. "
        "Do NOT call interview__review again."
    )


def _plain_directive_text(text: str) -> str:
    """Strip a legacy ``Tell the user:`` / ``Ask:`` prefix to a plain instruction."""
    raw = (text or "").strip()
    low = raw.lower()
    if low.startswith("tell the user:") or low.startswith("ask:"):
        return raw.split(":", 1)[1].strip()
    return raw


def call_tool_directive(next_tool: str) -> str:
    """Single-action directive: model should call one interview tool."""
    return f"Call {next_tool}."


def no_session_directive() -> str:
    """Directive when interview tools run without an active session."""
    return (
        "Activate the matching interview skill with use_skill, then call "
        "interview__next_field. Do not ask interview field questions via "
        "reply until the session is active."
    )


def restart_session_directive(interview_type: str) -> str:
    """Directive after complete/cancel when a new interview is needed."""
    return (
        f"Call use_skill with name '{interview_type}' to start a new interview "
        "session, then call interview__next_field."
    )


# ── The ctx interface ────────────────────────────────────────────────

SayMessage = Union[str, Sequence[str]]


@dataclass(frozen=True)
class HookExecutionContext:
    """The single interface for every interview hook.

    A hook declares one ``ctx`` parameter and gets this object — always injected,
    never ``None``. Read inputs as attributes; furnish output through the methods.

    **Inputs:** ``ctx.session``, ``ctx.value`` (validators), ``ctx.visitor``,
    ``ctx.interview`` (the InterviewAction), ``ctx.config`` (the spec),
    ``ctx.extracted_values``, ``ctx.args`` (validator_args / skill-tool args),
    ``ctx.phase``.

    **Output:**
    - ``ctx.say(msg | [msgs], *, continue_=False)`` — the single channel for
      user-facing text. One string is one question; a list is sequential
      statements (statement-then-followup); ``continue_=True`` appends the
      branch-aware next-field prompt. ``call_hook`` folds these into the result's
      ``response_directive`` (→ orchestrator → ``interaction.directives`` → reply).
      ``say`` is inert outside SAY_PHASES, so the store re-run of a prompt-builder
      can't bleed last prompt onto the next turn — call it unconditionally.
    - ``ctx.tool_response(...)`` — the control/return envelope (status, next_tool,
      interview_complete, value, retain_context_keys, review keys). NOT user text.
    - ``ctx.call_tool(tool)`` / ``ctx.no_session()`` — control directives.
    - ``ctx.valid(...)`` / ``ctx.invalid(...)`` — validator result dicts.
    """

    session: Optional[InterviewSession]
    spec: Optional[InterviewSpec]
    visitor: Any
    interview_action: Any
    phase: str = STORE_PHASE
    value: Optional[str] = None
    extracted_values: Optional[Dict[str, Any]] = None
    args: Dict[str, Any] = field(default_factory=dict)
    _can_say: bool = False
    _outbox: List[str] = field(default_factory=list)
    _flags: Dict[str, Any] = field(default_factory=dict)

    @property
    def interview(self) -> Any:
        """The InterviewAction instance (``_save_session``, ``_close_task``, …)."""
        return self.interview_action

    @property
    def config(self) -> Optional[InterviewSpec]:
        """The interview spec (alias used by handlers)."""
        return self.spec

    def say(
        self, message: SayMessage, *, continue_: bool = False, hint: str = ""
    ) -> None:
        """Record user-facing message(s) for this turn's reply.

        ``message`` is one statement or a list of sequential statements.
        ``continue_=True`` appends the branch-aware next-field prompt. ``hint`` is
        MODEL-ONLY guidance (e.g. "ask for the code only; do not skip") — it steers
        the compose model but is never relayed verbatim. Inert outside SAY_PHASES
        (e.g. the store re-run), so it is safe to call unconditionally.
        """
        if not self._can_say:
            return
        if isinstance(message, (list, tuple)):
            self._outbox.extend(str(m) for m in message if str(m).strip())
        elif message and str(message).strip():
            self._outbox.append(str(message))
        if continue_:
            self._flags["continue"] = True
        if hint and hint.strip():
            self._flags.setdefault("hints", []).append(hint.strip())

    def tool_response(
        self, *, ok: Optional[bool] = None, status: str = "ok", **data: Any
    ) -> str:
        """Build the control/return envelope. User text goes via ``say``, not here."""
        return interview_tool_response(ok=ok, status=status, **data)

    def call_tool(self, tool: str) -> str:
        """A control directive that chains one interview tool (no user text)."""
        return call_tool_directive(tool)

    def no_session(self) -> str:
        """The standard envelope for a hook that runs without an active session."""
        return interview_tool_response(
            ok=False, status="error", response_directive=no_session_directive()
        )

    def valid(self, value: Any = None, **extra: Any) -> Dict[str, Any]:
        """A validator success result. ``value`` defaults to the raw ``ctx.value``."""
        out: Dict[str, Any] = {
            "valid": True,
            "value": value if value is not None else self.value,
        }
        out.update(extra)
        return out

    def invalid(self, error: str, *, value: Any = None, **extra: Any) -> Dict[str, Any]:
        """A validator failure result.

        ``error`` is stated as a plain instruction — it is automatically framed and
        delivered to the user as the re-ask (same as ``ctx.say``); no ``Tell the
        user:`` prefix needed. Auto-framing is skipped when the validator already
        ``say``-ed something, or supplied an explicit ``response_directive`` (e.g.
        a ``ctx.call_tool`` control directive).
        """
        msg = _plain_directive_text(error)
        out: Dict[str, Any] = {
            "valid": False,
            "error": msg,
            "value": value if value is not None else self.value,
        }
        out.update(extra)
        if "response_directive" not in extra and not self._outbox:
            self.say(msg)
        return out


async def _build_say_directive(ctx: HookExecutionContext) -> str:
    """Compose ``ctx``'s recorded ``say`` statements into one directive string."""
    msgs = [m for m in ctx._outbox if m]
    directive = ""
    if ctx._flags.get("continue"):
        from .flow import build_next_field

        sidebar = "\n\n".join(msgs)
        action = ctx.interview_action
        nxt = None
        if ctx.spec is not None and ctx.session is not None:
            load_fn = action._load_fn(ctx.spec) if action else (lambda _: None)
            nxt = await build_next_field(
                ctx.session, ctx.spec, load_fn, ctx.visitor, action
            )
        prompt = str((nxt or {}).get("prompt") or "").strip()
        hint = str((nxt or {}).get("hint") or "")
        if prompt:
            directive = (
                user_followup_directive(sidebar, with_hint(prompt, hint))
                if sidebar
                else field_prompt_directive(prompt, hint)
            )
        elif sidebar:
            directive = user_directive_then_tool(sidebar, "interview__review")
        else:
            directive = call_tool_directive("interview__review")
    elif len(msgs) == 1:
        directive = user_directive(msgs[0])
    elif msgs:
        directive = user_followup_directive("\n\n".join(msgs[:-1]), msgs[-1])

    # Append any model-only guidance (hint=) after the existing post-marker guidance.
    hints = ctx._flags.get("hints")
    if hints and _G in directive:
        directive = f"{directive} {' '.join(hints)}"
    return directive


def load_hook_function(spec: InterviewSpec, function_name: str) -> Optional[Callable]:
    """Load a named function from the skill's scripts/custom_tools.py."""
    key = f"{spec.name}:{spec.source_dir}"
    module = _module_cache.get(key)
    if module is None:
        custom_tools_path = os.path.join(spec.source_dir, "scripts", "custom_tools.py")
        if not os.path.isfile(custom_tools_path):
            return None
        try:
            loader_spec = importlib.util.spec_from_file_location(
                f"interview_custom_tools_{spec.name}", custom_tools_path
            )
            if not loader_spec or not loader_spec.loader:
                return None
            module = importlib.util.module_from_spec(loader_spec)
            module.__dict__["InterviewSession"] = InterviewSession
            loader_spec.loader.exec_module(module)
            _module_cache[key] = module
        except Exception as e:
            logger.error(
                "Failed to load custom_tools from %s: %s", custom_tools_path, e
            )
            return None

    func = getattr(module, function_name, None)
    return func if callable(func) else None


def clear_module_cache() -> None:
    _module_cache.clear()


async def call_hook(
    func: Callable,
    *,
    session: Optional[InterviewSession] = None,
    spec: Optional[InterviewSpec] = None,
    visitor: Any = None,
    interview_action: Any = None,
    value: Optional[str] = None,
    kwargs: Optional[dict] = None,
    phase: str = STORE_PHASE,
) -> Any:
    """Invoke a hook with ``ctx`` and fold any ``ctx.say`` into ``response_directive``.

    ``phase`` names the run; it sets whether ``ctx.say`` records (SAY_PHASES) so a
    prompt-builder re-run during the store phase stays inert. The hook receives
    exactly one argument when it declares ``ctx``. After it returns, any recorded
    ``say`` statements become the result's ``response_directive`` (unless the hook
    set one explicitly, e.g. via ``ctx.call_tool``).
    """
    ctx = HookExecutionContext(
        session=session,
        spec=spec,
        visitor=visitor,
        interview_action=interview_action,
        phase=phase,
        value=value,
        extracted_values=(session.get_collected_summary() if session else {}),
        args=dict(kwargs or {}),
        _can_say=phase in SAY_PHASES,
    )
    call_kwargs: Dict[str, Any] = {"ctx": ctx}

    try:
        sig_params = set(inspect.signature(func).parameters.keys())
        if sig_params:
            call_kwargs = {k: v for k, v in call_kwargs.items() if k in sig_params}
    except (ValueError, TypeError):
        pass

    result = func(**call_kwargs)
    if asyncio.iscoroutine(result):
        result = await result

    if ctx._outbox or ctx._flags:
        directive = await _build_say_directive(ctx)
        if directive:
            parsed = coerce_hook_result(result)
            if not parsed.get("response_directive"):
                parsed["response_directive"] = directive
            result = parsed
    return result


def coerce_hook_result(result: Any) -> Dict[str, Any]:
    """Normalize a hook return value to a dict (str-JSON parsed, else empty)."""
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _parse_validation_result(
    result: Any, original_value: str, validator_name: str
) -> Dict[str, Any]:
    """Normalize a validator return value to a {valid, ...} dict."""
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                result = parsed
        except (json.JSONDecodeError, TypeError):
            pass
    if not isinstance(result, dict) or "valid" not in result:
        return {
            "valid": False,
            "error": f"Validator must return dict with 'valid' key, got {type(result)}",
            "validator": validator_name,
        }
    if result.get("valid") is True:
        out: Dict[str, Any] = {
            "valid": True,
            "value": result.get("value", original_value),
            "validator": validator_name,
        }
        for key in (
            "interview_complete",
            "response_directive",
            "retain_context_keys",
        ):
            if key in result:
                out[key] = result[key]
        return out
    out = {
        "valid": False,
        "error": result.get("error", f"Validation failed for {validator_name}"),
        "validator": validator_name,
    }
    if "response_directive" in result:
        out["response_directive"] = result["response_directive"]
    return out


async def run_validator(
    action: Any,
    spec: InterviewSpec,
    field: FieldDef,
    value: str,
    session: Optional[InterviewSession] = None,
    visitor: Any = None,
) -> Dict[str, Any]:
    """Run the field's configured validator. Returns a {valid, ...} dict.

    A field without a validator accepts any non-empty value. Built-in
    validators (by name) are tried first, then ``custom_tools.py`` functions.
    """
    cleaned = (value or "").strip()
    if not field.validator:
        return {"valid": True, "value": cleaned, "validator": None}
    if not cleaned:
        return {
            "valid": False,
            "error": f"No value provided for field '{field.key}'",
            "validator": field.validator,
        }

    builtin = get_validator(field.validator)
    if builtin:
        try:
            result = builtin(cleaned, **dict(field.validator_args))
        except Exception as e:
            return {
                "valid": False,
                "error": f"Validator error: {e}",
                "validator": field.validator,
            }
        return _parse_validation_result(result, cleaned, field.validator)

    func = load_hook_function(spec, field.validator)
    if not func:
        return {
            "valid": False,
            "error": f"No validator found for '{field.validator}' in {spec.name}",
            "validator": field.validator,
        }
    try:
        result = await call_hook(
            func,
            session=session,
            spec=spec,
            visitor=visitor,
            interview_action=action,
            value=cleaned,
            kwargs=dict(field.validator_args),
            phase=VALIDATE_PHASE,
        )
    except Exception as e:
        return {
            "valid": False,
            "error": f"Validator error: {e}",
            "validator": field.validator,
        }
    return _parse_validation_result(result, cleaned, field.validator)
