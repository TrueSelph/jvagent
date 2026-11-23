"""Declarative child skills and host-owned conversation scope for native delegates."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.agent import WrapperAgent


class NativeDelegate(BaseModel):
    """A named, bounded skill worker; model and caller authority come from the host."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    description: str = Field(min_length=1, max_length=1000)
    skill_directory: str = Field(min_length=1)
    max_calls: int = Field(default=2, ge=1, le=10, strict=True)
    timeout_seconds: float = Field(default=90.0, gt=0, le=180, allow_inf_nan=False)


class ConversationAgent(WrapperAgent[Any, Any]):
    """Keep a child's native spend in its server-established conversation.

    Official foreground SubAgents leaves conversation_id unset on child runs.
    This public WrapperAgent supplies only that application identity; the library
    still owns execution, history isolation, limits and delegation lifecycle.
    Remove this adapter when SubAgents exposes a conversation resolver.
    """

    def __init__(self, wrapped: Any, conversation_id: str) -> None:
        super().__init__(wrapped)
        if not conversation_id.strip():
            raise ValueError("Delegation requires a host conversation ID")
        self.conversation_id = conversation_id

    async def run(self, user_prompt: Any = None, **kwargs: Any) -> Any:
        supplied = kwargs.pop("conversation_id", None)
        if supplied is not None and supplied != self.conversation_id:
            raise ValueError("Delegate conversation scope cannot change")
        return await self.wrapped.run(
            user_prompt, conversation_id=self.conversation_id, **kwargs
        )
