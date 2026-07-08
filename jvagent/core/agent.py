"""Agent node and CRUD operations."""

import logging
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    TypeVar,
)

from jvspatial.core import Node
from jvspatial.core.annotations import attribute

if TYPE_CHECKING:
    from jvagent.action.actions import Actions
    from jvagent.action.response.response_bus import ResponseBus
    from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)

TAgent = TypeVar("TAgent", bound="Agent")


class Agent(Node):
    """Individual agent node in the system.

    Attributes:
        namespace: Namespace for the agent (e.g., 'jvagent', 'contrib')
        name: Unique machine name for the agent within the namespace (required, static)
        alias: Human-readable display name for the agent (optional)
        role: The agent's role/purpose for the model; with ``alias`` it forms the
            agent's identity (ADR-0014)
        enabled: Whether the agent is enabled (default: True)
        description: Optional description of the agent
    """

    namespace: str = attribute(indexed=True, description="Namespace for the agent")
    name: str = attribute(
        indexed=True,
        index_unique=True,
        # Scope uniqueness to Agent entities only. Without the entity
        # discriminator, embedded jvagent deployments that share the
        # ``node`` collection with the host's nodes hit E11000 the moment
        # any non-Agent node carries ``context.name`` (e.g. integral has
        # nodes named "Default"). The partial filter must match every
        # row that the unique constraint applies to — adding
        # ``entity == "Agent"`` keeps the constraint correct without
        # leaking into the host's domain rows.
        index_partial_filter_expression={
            "entity": "Agent",
            "context.name": {"$gt": ""},
        },
        description="Unique machine name for the agent",
    )
    alias: str = attribute(description="Human-readable display name")
    role: str = attribute(
        default="",
        description=(
            "The agent's role/purpose, expressed for the model. Combined with "
            "``alias`` it forms the agent's identity, injected into the "
            "orchestrator prompt and read by the egress voice (ADR-0014)."
        ),
    )
    enabled: bool = attribute(default=True, description="Whether the agent is enabled")
    description: str = attribute(description="Optional description of the agent")
    interaction_limit: int = attribute(
        default=0,
        description="Default interaction limit for conversations (0 = disabled, no pruning). Can be overridden per conversation.",
    )
    max_statement_length: Optional[int] = attribute(
        default=None,
        description="Default maximum length for truncating utterances and responses in conversation history. Can be overridden when calling methods that accept max_statement_length parameter. None = no truncation.",
    )

    # Runtime instances (private, transient)
    _response_bus: Any = attribute(private=True, default=None)

    # One-shot guard so the cache-bypass warning fires once per kwarg-name
    # combo, not on every call. AUDIT-core H-1.
    _bypass_warning_seen: ClassVar[Set[Tuple[str, ...]]] = set()

    @classmethod
    async def get(
        cls: Type[TAgent], agent_id: Optional[str] = None, **kwargs: Any
    ) -> Optional[TAgent]:
        """Get an Agent node by ID, with caching.

        When *agent_id* is provided alone, delegates through the cache for
        reduced database I/O. Passing **any** keyword arg alongside
        ``agent_id`` bypasses the cache and hits the DB directly; a debug
        log fires once per kwarg-name combo so action authors notice the
        full DB cost. AUDIT-core H-1.

        Args:
            agent_id: Node ID to fetch (cached).
            **kwargs: Passed to ``Node.get()``.

        Returns:
            Agent instance if found, None otherwise.
        """
        if agent_id is not None and not kwargs:
            from jvagent.core.cache import cache_manager

            return await cache_manager.get_agent(agent_id)  # type: ignore[return-value]
        if agent_id is not None and kwargs:
            seen_key = tuple(sorted(kwargs.keys()))
            if seen_key not in cls._bypass_warning_seen:
                cls._bypass_warning_seen.add(seen_key)
                logger.debug(
                    "Agent.get(agent_id=..., %s=...) bypasses the agent cache; "
                    "pass only agent_id for cached lookups (AUDIT-core H-1)",
                    ", ".join(seen_key),
                )
        return await super().get(agent_id, **kwargs)  # type: ignore[return-value]

    # =========================================================================
    # Graph Navigation Helpers
    # =========================================================================

    async def get_actions_manager(self) -> Optional["Actions"]:
        """Get the Actions manager node for this agent.

        Returns:
            Actions manager node if found, None otherwise
        """
        return await self.node(node="Actions")

    async def get_action(self, action_label: str) -> Optional[Any]:
        """Get an action by its label.

        Args:
            action_label: The label of the action to retrieve

        Returns:
            Action instance if found, None otherwise
        """
        actions_manager = await self.get_actions_manager()
        if not actions_manager:
            return None
        return await actions_manager.get_action_by_label(action_label)

    async def get_action_by_type(self, entity_type: str) -> Optional[Any]:
        """Get the first action matching the given entity type.

        This is useful for finding actions like "OpenAILanguageModelAction" without
        needing to know the specific ID or label.

        Args:
            entity_type: Entity type name (e.g., "OpenAILanguageModelAction", "ReplyAction")

        Returns:
            Action instance if found, None otherwise
        """
        from jvagent.action.base import Action

        # Use entity-centric find_one with explicit entity filter
        # This queries for the specific entity type belonging to this agent
        return await Action.find_one(
            {
                "entity": entity_type,
                "context.agent_id": self.id,
            }
        )

    async def get_access_control_action(self) -> Optional[Any]:
        """Return the agent's AccessControlAction, if any.

        Logs an error when more than one is present (undefined); the first match is returned.
        """
        from jvagent.action.base import Action

        found = await Action.find(
            {
                "entity": "AccessControlAction",
                "context.agent_id": self.id,
            }
        )
        if not found:
            return None
        if len(found) > 1:
            logger.error(
                "Multiple AccessControlAction nodes for agent %s (count=%s ids=%s); "
                "using the first instance",
                self.id,
                len(found),
                [getattr(a, "id", None) for a in found],
            )
        return found[0]

    async def get_actions(self, enabled_only: bool = False) -> List[Any]:
        """Get all actions for this agent.

        Args:
            enabled_only: If True, only return enabled actions

        Returns:
            List of Action instances
        """
        actions_manager = await self.get_actions_manager()
        if not actions_manager:
            return []
        return await actions_manager.get_actions(enabled_only=enabled_only)

    async def collect_capabilities(self) -> List[str]:
        """The agent's advertised abilities — the single aggregation point.

        Flattens each enabled action's ``Action.get_capabilities()`` contribution
        into one de-duplicated, order-preserving list. The per-action contribution
        is defined on the action (``Action.get_capabilities``); rendering (bullets,
        length caps) belongs to the caller; skills, when relevant, are merged in by
        the caller (the orchestrator appends skill descriptions for its system-
        prompt digest). The agent owns its action roster, so it owns the rollup.
        """
        caps: List[str] = []
        seen: Set[str] = set()
        for action in await self.get_actions(enabled_only=True):
            getter = getattr(action, "get_capabilities", None)
            if not callable(getter):
                continue
            try:
                contributed = getter() or []
            except Exception as exc:
                namer = getattr(action, "get_class_name", None)
                name = namer() if callable(namer) else type(action).__name__
                logger.debug(
                    "Agent.collect_capabilities: %s.get_capabilities failed: %s",
                    name,
                    exc,
                )
                continue
            for cap in contributed:
                text = str(cap).strip()
                if text and text not in seen:
                    seen.add(text)
                    caps.append(text)
        return caps

    async def get_memory(self) -> Optional[Any]:
        """Get the Memory node for this agent.

        Returns:
            Memory node if found, None otherwise
        """
        return await self.node(node="Memory")

    # =========================================================================
    # Response Bus (Agent-Scoped)
    # =========================================================================

    async def get_response_bus(self) -> "ResponseBus":
        """Get or initialize the agent-scoped ResponseBus instance.

        Each agent owns exactly one ResponseBus instance. Channel adapters and
        filters from this agent's actions register with this bus.

        Returns:
            ResponseBus instance for this agent
        """
        if self._response_bus is None:
            from jvagent.action.response.response_bus import ResponseBus

            self._response_bus = ResponseBus()
        return self._response_bus

    async def send_proactive_message(
        self,
        *,
        user_id: str,
        content: str,
        channel: str,
        session_id: Optional[str] = None,
        source_action: str = "ProactiveDispatch",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional["Interaction"]:
        """Send a proactive, response-only message to a user via the ResponseBus.

        No user utterance is involved. ``content`` is published through this
        agent's ResponseBus, which dispatches to the registered channel adapter,
        appends ``content`` to the bound Interaction's ``response`` field, and
        saves the Interaction. The new Interaction is created with empty
        ``utterance`` so the entry represents a standalone assistant turn in
        future LLM history.

        Args:
            user_id:       Target user identifier (e.g. phone number / external ID).
            content:       Text the agent wants to send.
            channel:       Channel key — must match a registered adapter
                           (e.g. ``"whatsapp"``, ``"default"``).
            session_id:    Optional session override. If ``None``, uses the
                           user's active conversation's session; creates one on demand.
            source_action: Action name tag stored on the Interaction's parameters
                           array — useful for downstream filters.
            metadata:      Arbitrary tag dict merged into the proactive tag entry
                           (e.g. ``{"job_id": "...", "reason": "..."}``).

        Returns:
            The persisted Interaction, or ``None`` if dispatch was skipped
            (missing inputs, no memory, user lookup failed).
        """
        if not user_id or not content or not channel:
            return None
        memory = await self.get_memory()
        if not memory:
            return None
        response_bus = await self.get_response_bus()

        user = await memory.get_user(user_id, create_if_missing=True)
        if not user:
            return None

        conversation = None
        if session_id:
            conversation = await user.get_conversation_by_session(session_id)
        if conversation is None:
            get_active = getattr(user, "get_active_conversation", None)
            if callable(get_active):
                conversation = await get_active()
        if conversation is None:
            conversation = await user.create_conversation(
                session_id=session_id,
                channel=channel,
            )
        effective_session_id = session_id or conversation.session_id or ""

        interaction = await conversation.add_interaction(
            utterance="",
            channel=channel,
            session_id=effective_session_id,
        )
        if not interaction:
            return None

        tag: Dict[str, Any] = {"is_proactive": True}
        if metadata:
            tag.update(metadata)
        interaction.add_parameter(tag, source_action)
        await interaction.save()

        await response_bus.publish(
            session_id=effective_session_id,
            content=content,
            channel=channel,
            user_id=user_id,
            interaction_id=interaction.id,
            interaction=interaction,
            category="user",
            stream=False,
            metadata={"is_proactive": True, "source_action": source_action},
        )

        return interaction

    async def enqueue_proactive_task(
        self,
        *,
        user_id: str,
        spec: Any,
        session_id: Optional[str] = None,
        channel: str = "default",
        owner_action: str = "Agent.enqueue_proactive_task",
        title: str = "",
    ) -> Optional[Any]:
        """Enqueue a PROACTIVE task on the user's conversation TaskStore."""
        from jvagent.memory.task_proactive import ProactiveTaskSpec
        from jvagent.memory.task_store import TaskStore

        if not user_id:
            return None
        if not isinstance(spec, ProactiveTaskSpec):
            spec = ProactiveTaskSpec.from_data(dict(spec or {}))

        memory = await self.get_memory()
        if not memory:
            return None

        user = await memory.get_user(user_id, create_if_missing=True)
        if not user:
            return None

        conversation = None
        if session_id:
            conversation = await user.get_conversation_by_session(session_id)
        if conversation is None:
            get_active = getattr(user, "get_active_conversation", None)
            if callable(get_active):
                conversation = await get_active()
        if conversation is None:
            conversation = await user.create_conversation(
                session_id=session_id,
                channel=channel,
            )

        if spec.channel is None:
            spec.channel = channel
        store = TaskStore(conversation)
        return await store.enqueue_proactive(
            spec,
            owner_action=owner_action,
            title=title,
        )

    async def save(self, *args, **kwargs):
        """Save the agent and invalidate cache.

        Overrides Node.save() to invalidate the agent cache after saving,
        ensuring cached agents reflect the latest state.

        Args:
            *args: Positional arguments passed to parent save()
            **kwargs: Keyword arguments passed to parent save()

        Returns:
            Result from parent save()

        Note:
            Cache invalidation errors are logged but do not prevent the save
            from succeeding. This ensures data is persisted even if cache
            operations fail.
        """
        result = await super().save(*args, **kwargs)
        # Invalidate ALL agent-scoped caches after save (AUDIT-core H-2).
        # Toggling ``enabled`` or other action-affecting fields previously
        # left the action cache and the class-name → action-id index stale
        # for up to ``action_cache_ttl`` (60s default).
        try:
            from jvagent.core.cache import (
                invalidate_action_cache,
                invalidate_action_type_index,
                invalidate_agent_cache,
            )

            await invalidate_agent_cache(self.id)
            await invalidate_action_cache(self.id)
            await invalidate_action_type_index(self.id)
        except Exception as e:
            # Log but don't fail — save already succeeded.
            logger.warning(f"Failed to invalidate agent caches for {self.id}: {e}")
        return result
