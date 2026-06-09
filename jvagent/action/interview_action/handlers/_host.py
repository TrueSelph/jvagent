"""Typing base for handler mixins (implemented across mixin MRO on InterviewAction)."""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

from ..core.interview_loader import InterviewRegistry, InterviewSpec, ValidatorDef
from ..core.session import InterviewSession


class InterviewHandlersHost:
    """Stub base declaring attributes and methods provided by InterviewAction."""

    _registry: InterviewRegistry
    description: str

    def _load_fn(
        self, spec: InterviewSpec
    ) -> Callable[[str], Optional[Callable[..., Any]]]:
        raise NotImplementedError

    async def _ensure_specs_loaded(self) -> None:
        raise NotImplementedError

    async def _get_session(self, visitor: Any = None) -> Optional[InterviewSession]:
        raise NotImplementedError

    async def _get_session_and_contract(
        self, visitor: Any = None
    ) -> Tuple[Optional[InterviewSession], Optional[InterviewSpec]]:
        raise NotImplementedError

    async def _save_session(
        self, session: InterviewSession, visitor: Any = None
    ) -> None:
        raise NotImplementedError

    async def _clear_interview_session(
        self,
        visitor: Any = None,
        *,
        retain_context_keys: Optional[List[str]] = None,
    ) -> None:
        raise NotImplementedError

    async def _ensure_active_task(self, visitor: Any, spec: InterviewSpec) -> None:
        raise NotImplementedError

    async def _close_task(
        self,
        visitor: Any,
        status: str = "completed",
        spec_name: Optional[str] = None,
    ) -> None:
        raise NotImplementedError

    async def _handle_next_question(self, visitor: Any = None) -> str:
        raise NotImplementedError

    async def _handle_review(self, visitor: Any = None) -> str:
        raise NotImplementedError

    async def _run_validator(
        self,
        vdef: ValidatorDef,
        value: str,
        kwargs: dict,
        visitor: Any = None,
        session: Optional[InterviewSession] = None,
        spec: Optional[InterviewSpec] = None,
    ) -> str:
        raise NotImplementedError
