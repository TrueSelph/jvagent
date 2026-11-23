"""Graph persistence for the official Planning capability's public PlanStore."""

import asyncio
import json
from typing import Any

from jvspatial import flush_deferred_entities
from pydantic import TypeAdapter
from pydantic_ai_harness.planning import InMemoryPlanStore, PlanItem, TaskStatus


class ConversationPlanStore:
    """Delegate plan CRUD to the SDK; persist detached items in one conversation.

    Local single-writer admission owns conversation scope. Share the spend-store
    lock so delegated billing cannot race this field's graph writes. This is not
    a scheduler, task interpreter or distributed plan service.
    """

    def __init__(
        self,
        conversation: Any,
        *,
        agent_id: str,
        user_id: str,
        session_id: str,
        lock: asyncio.Lock,
    ) -> None:
        self.conversation = conversation
        self.lock = lock
        self.scope = {
            "agent_id": agent_id,
            "conversation_id": conversation.id,
            "user_id": user_id,
            "session_id": session_id,
        }
        self._owner_verified = False
        if conversation.user_id != user_id or conversation.session_id != session_id:
            raise ValueError("Planning caller does not own the conversation")
        if any(
            not isinstance(value, str) or not value for value in self.scope.values()
        ):
            raise ValueError("Native planning requires server-established graph scope")

    @staticmethod
    def _encoded(items: list[PlanItem]) -> list[dict[str, Any]]:
        if len(items) > 128 or len({item.id for item in items}) != len(items):
            raise ValueError("Plan item count or identity exceeds graph bounds")
        if any(not item.id.strip() or len(item.id) > 128 for item in items):
            raise ValueError("Plan item identity is invalid")
        encoded = [item.model_dump(mode="json") for item in items]
        if len(json.dumps(encoded, ensure_ascii=False).encode("utf-8")) > 65_536:
            raise ValueError("Plan exceeds graph storage allowance")
        return encoded

    async def _call(
        self, method: str, *args: Any, write: bool = False, **kwargs: Any
    ) -> Any:
        from jvagent.memory.conversation import Conversation

        async with self.lock:
            if (
                any(
                    getattr(self.conversation, key) != self.scope[key]
                    for key in ("user_id", "session_id")
                )
                or self.conversation.id != self.scope["conversation_id"]
            ):
                raise ValueError("Planning scope changed")
            rows = await Conversation.find({"id": self.conversation.id})
            if len(rows) != 1 or any(
                getattr(rows[0], key) != self.scope[key]
                for key in ("user_id", "session_id")
            ):
                raise ValueError("Planning conversation is missing or foreign")
            if not self._owner_verified:
                owner = await rows[0].get_agent()
                if owner is None or owner.id != self.scope["agent_id"]:
                    raise ValueError("Planning conversation belongs to another agent")
                self._owner_verified = True
            raw = rows[0].native_plan
            if not isinstance(raw, dict):
                raise ValueError("Stored plan format is invalid")
            if raw:
                if (
                    not isinstance(raw, dict)
                    or set(raw) != {"version", "scope", "items"}
                    or type(raw.get("version")) is not int
                    or raw.get("version") != 1
                    or raw.get("scope") != self.scope
                ):
                    raise ValueError("Stored plan scope or format is invalid")
                encoded = raw["items"]
                if (
                    not isinstance(encoded, list)
                    or len(encoded) > 128
                    or any(
                        not isinstance(item, dict)
                        or set(item) != set(PlanItem.model_fields)
                        for item in encoded
                    )
                ):
                    raise ValueError(
                        "Stored plan items require explicit schema migration"
                    )
                items = TypeAdapter(list[PlanItem]).validate_python(encoded)
                self._encoded(items)
            else:
                items = []
            store = InMemoryPlanStore()
            await store.set_items(items)
            result = await getattr(store, method)(*args, **kwargs)
            if write:
                encoded = self._encoded(await store.get_items())
                prior = self.conversation.native_plan
                object.__setattr__(
                    self.conversation,
                    "native_plan",
                    {"version": 1, "scope": dict(self.scope), "items": encoded},
                )
                try:
                    await self.conversation.save()
                    await flush_deferred_entities(self.conversation, strict=True)
                except BaseException:
                    object.__setattr__(self.conversation, "native_plan", prior)
                    raise
            return result

    async def get_items(self) -> list[PlanItem]:
        return await self._call("get_items")

    async def set_items(self, items: list[PlanItem]) -> None:
        self._encoded(items)
        await self._call("set_items", items, write=True)

    async def get_item(self, item_id: str) -> PlanItem | None:
        return await self._call("get_item", item_id)

    async def add_item(self, item: PlanItem) -> PlanItem:
        return await self._call("add_item", item, write=True)

    async def update_item(
        self,
        item_id: str,
        *,
        content: str | None = None,
        status: TaskStatus | None = None,
        active_form: str | None = None,
        parent_id: str | None = None,
        depends_on: list[str] | None = None,
    ) -> PlanItem | None:
        return await self._call(
            "update_item",
            item_id,
            content=content,
            status=status,
            active_form=active_form,
            parent_id=parent_id,
            depends_on=depends_on,
            write=True,
        )

    async def remove_item(self, item_id: str) -> bool:
        return await self._call("remove_item", item_id, write=True)
