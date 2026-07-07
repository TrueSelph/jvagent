"""InteractWalker for traversing InteractActions in the interact subsystem.

This module provides the InteractWalker that serves as the common entry point
for agent interactions, replacing the PersonaAction interact endpoint.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core import Walker, on_visit

from jvagent.action.access_control.access_control_action import (
    AccessControlAction,
    log_access_denied,
)
from jvagent.action.interact.base import InteractAction
from jvagent.core.cache import cache_actions, get_cached_actions
from jvagent.memory.interaction import Interaction

if TYPE_CHECKING:
    from jvagent.core.agent import Agent
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.task_store import TaskStore
else:
    # Import at runtime for Pydantic model_rebuild
    from jvagent.memory.conversation import Conversation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InteractionInitResult:
    """Outcome of :meth:`InteractWalker.initialize_interaction` (HTTP / programmatic startup).

    ``code`` is machine-readable; ``detail`` is for logs only (may contain internal errors).
    """

    ok: bool
    code: str
    detail: Optional[str] = None


class InteractWalker(Walker):
    """Walker that traverses InteractActions for agent interactions.

    InteractWalker is the common entry point for agent interactions. It:
    - Handles user/conversation resolution
    - Creates Interaction node
    - Traverses from Agent -> Actions -> InteractActions
    - Executes top-level InteractActions in weight order (from Actions node)
    - Provides helper methods to record and remove executed actions on the active interaction

    Architecture:
        InteractActions serve as modular points of execution that may exist in a
        prescribed chain of interact actions. The InteractWalker traverses and executes
        this modular pipeline of interact actions. Core actions like InteractRouter, when
        employed, provide additional logic which alters or curates the walker's walk path
        or traversal, allowing specific actions to be executed based on the nature of the input.

        While interact actions may have branches of other interact actions, the top-level
        interact actions (that is, the actions directly connected to the Actions branch node)
        must employ logic to further route the interact walker to its children (since this
        may always be done conditionally) instead of having it done automatically.

    Usage:
        The walker should be spawned directly on the Agent node:
            await walker.spawn(agent)

        This skips the Root -> Agent traversal and starts directly where needed.

    Weight Ordering:
        Weight is only applied at the top tier of the InteractAction graph when
        launching from the Actions node. This is because top-level InteractActions
        are connected in a flat arrangement and there may be multiple top-level
        actions that need ordering.
    """

    # Walker state
    agent_id: str = ""
    utterance: str = ""
    channel: str = "default"
    data: Dict[str, Any] = {}
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    interaction: Optional["Interaction"] = None
    conversation: Optional["Conversation"] = None
    stream: bool = False
    response_bus: Optional[Any] = None
    _current_action: Optional["InteractAction"] = (
        None  # Track current executing action for convenience methods
    )
    _skip_current_action_record: bool = (
        False  # Allow actions to opt-out of being recorded as executed
    )
    _agent: Optional["Agent"] = None  # Agent node, set in on_agent for access control
    _task_store: Optional["TaskStore"] = None
    _bootstrap_error: Optional[str] = (
        None  # outcome code from _bootstrap_interaction when failed
    )
    background_actions: List["InteractAction"] = (
        []
    )  # Actions deferred for post-interaction execution

    @property
    def tasks(self) -> "TaskStore":
        """Return conversation-scoped task store."""
        if not self.conversation:
            raise RuntimeError("No conversation available for task tracker")
        if self._task_store is None:
            from jvagent.memory.task_store import TaskStore

            self._task_store = TaskStore(self.conversation)
        return self._task_store

    async def record_action_execution(
        self, action_name: Optional[str] = None
    ) -> Optional[str]:
        """Record an executed action on the active interaction.

        If called from within an InteractAction execution and no action_name is
        provided, the current action's class name is inferred automatically.

        Args:
            action_name: Optional explicit action class name to record.

        Returns:
            The action name recorded, or None if no interaction or action was available.
        """
        if not self.interaction:
            return None

        name = action_name
        if not name and self._current_action:
            try:
                name = self._current_action.get_class_name()
            except Exception:
                name = None

        if not name:
            return None

        self.interaction.record_action_execution(name)
        await self.interaction.save()
        return name

    async def unrecord_action_execution(
        self, action_name: Optional[str] = None
    ) -> Optional[str]:
        """Remove or prevent recording of a previously/currently executed action.

        If called from within an InteractAction execution and no action_name is
        provided, the current action's class name is inferred automatically.

        Behavior:
        - If the action has already been recorded on the interaction, it is removed.
        - If the action has not yet been recorded for the current execution, it
          marks the current action so it will NOT be recorded after execute().

        Args:
            action_name: Optional explicit action class name to remove.

        Returns:
            The action name removed or skipped, or None if no interaction or action was available.
        """
        if not self.interaction:
            return None

        name = action_name
        if not name and self._current_action:
            try:
                name = self._current_action.get_class_name()
            except Exception:
                name = None

        if not name:
            return None

        # If this refers to the currently executing action, mark it so it won't be recorded
        if self._current_action:
            try:
                current_name = self._current_action.get_class_name()
            except Exception:
                current_name = None
            if current_name and current_name == name:
                self._skip_current_action_record = True

        # If it was already recorded, remove it from the interaction history
        if name in self.interaction.actions:
            self.interaction.unrecord_action_execution(name)
            await self.interaction.save()

        return name

    def _interact_action_class_name(self, here: "InteractAction") -> str:
        try:
            return here.get_class_name()
        except Exception:
            return here.__class__.__name__

    async def _apply_access_denied_to_interaction(
        self, here: "InteractAction", action_label: str
    ) -> None:
        if self.interaction and getattr(here, "deny_access_directive", None):
            if self.interaction.add_directive(here.deny_access_directive, action_label):
                await self.interaction.save()

        await self.report(
            {
                "action_skipped": {
                    "action": here.label,
                    "weight": here.weight,
                    "reason": "access_denied",
                }
            }
        )

    async def enforce_interact_action_access(
        self,
        here: "InteractAction",
        *,
        stage: str,
    ) -> bool:
        """Return True if the action may run; False if denied (directive/report/logging applied)."""
        if not self._agent:
            return True
        access_control = await self._agent.get_access_control_action()
        if not access_control or not isinstance(access_control, AccessControlAction):
            return True
        if not access_control.policy_applies():
            return True

        action_label = self._interact_action_class_name(here)
        user_id = (self.user_id or "").strip()
        if not user_id and not access_control.allow_anonymous:
            log_access_denied(
                agent_id=self.agent_id or getattr(self._agent, "id", ""),
                user_id=None,
                channel=self.channel,
                action_label=action_label,
                stage=stage,
                reason="missing_user_id",
            )
            await self._apply_access_denied_to_interaction(here, action_label)
            return False

        allowed = await access_control.has_action_access(
            user_id=user_id,
            action_label=action_label,
            channel=self.channel,
        )
        if allowed:
            return True
        log_access_denied(
            agent_id=self.agent_id or getattr(self._agent, "id", ""),
            user_id=user_id or None,
            channel=self.channel,
            action_label=action_label,
            stage=stage,
        )
        await self._apply_access_denied_to_interaction(here, action_label)
        return False

    async def _bootstrap_interaction(
        self, here: "Agent", *, through: str = "full"
    ) -> str:
        """Resolve session, enforce entry access control, create Interaction.

        ``through`` selects a phase for turn-lock serialization:
        - ``full`` (default): session resolution + create
        - ``session``: resolve session and access only; returns ``ready`` on success
        - ``create``: create Interaction only (requires ``self.conversation``)

        Preconditions: ``self._agent`` is set to ``here`` by caller.

        Returns:
            Machine-readable outcome: ``ok``, ``ready``, ``no_memory``, ``access_denied``,
            or ``init_error``.
        """
        if through == "create":
            if not self.conversation:
                return "init_error"
            return await self._bootstrap_create_interaction(here)

        t_start = time.perf_counter()
        memory = await here.get_memory()
        if not memory:
            await self.report({"error": "Agent has no Memory node"})
            logger.info(
                "interact_init_bootstrap",
                extra={
                    "outcome": "no_memory",
                    "agent_id": self.agent_id,
                    "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 3),
                },
            )
            return "no_memory"

        self.response_bus = await here.get_response_bus()

        t_session = time.perf_counter()
        try:
            user, conversation, resolved_user_id, resolved_session_id, new_user = (
                await memory.get_session(
                    user_id=self.user_id,
                    session_id=self.session_id,
                    user_name=self.user_name,
                    channel=self.channel,
                )
            )
        except ValueError as e:
            await self.report({"error": str(e), "code": "session_resolution_error"})
            logger.info(
                "interact_init_bootstrap",
                extra={
                    "outcome": "session_resolution_error",
                    "agent_id": self.agent_id,
                    "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 3),
                    "get_session_ms": round(
                        (time.perf_counter() - t_session) * 1000, 3
                    ),
                    "detail": str(e),
                },
            )
            return "session_resolution_error"
        except Exception as e:
            await self.report({"error": f"Failed to initialize interaction: {e}"})
            logger.error(
                "interact_init_bootstrap session/get_session failed",
                exc_info=True,
                extra={
                    "outcome": "init_error",
                    "agent_id": self.agent_id,
                    "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 3),
                    "get_session_ms": round(
                        (time.perf_counter() - t_session) * 1000, 3
                    ),
                },
            )
            return "init_error"

        self.user_id = resolved_user_id
        self.session_id = resolved_session_id
        self.new_user = new_user
        conversation.context["new_user"] = new_user
        self.conversation = conversation
        get_session_ms = (time.perf_counter() - t_session) * 1000

        access_control = await here.get_access_control_action()
        if (
            access_control
            and isinstance(access_control, AccessControlAction)
            and access_control.policy_applies()
        ):
            uid = (self.user_id or "").strip()
            if not uid and not access_control.allow_anonymous:
                log_access_denied(
                    agent_id=self.agent_id or here.id,
                    user_id=None,
                    channel=self.channel,
                    action_label="interact",
                    stage="entry",
                    reason="missing_user_id",
                )
                await self.report(
                    {
                        "access_denied": True,
                        "error": (
                            "Access denied: user identity required for this agent"
                        ),
                    }
                )
                logger.info(
                    "interact_init_bootstrap",
                    extra={
                        "outcome": "access_denied",
                        "agent_id": self.agent_id,
                        "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 3),
                        "get_session_ms": round(get_session_ms, 3),
                    },
                )
                return "access_denied"
            if not await access_control.has_action_access(
                user_id=uid,
                action_label="interact",
                channel=self.channel,
            ):
                log_access_denied(
                    agent_id=self.agent_id or here.id,
                    user_id=uid or None,
                    channel=self.channel,
                    action_label="interact",
                    stage="entry",
                )
                await self.report({"access_denied": True, "error": "Access denied"})
                logger.info(
                    "interact_init_bootstrap",
                    extra={
                        "outcome": "access_denied",
                        "agent_id": self.agent_id,
                        "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 3),
                        "get_session_ms": round(get_session_ms, 3),
                    },
                )
                return "access_denied"

        if through == "session":
            return "ready"

        return await self._bootstrap_create_interaction(here, t_start=t_start)

    async def _bootstrap_create_interaction(
        self, here: "Agent", *, t_start: Optional[float] = None
    ) -> str:
        """Create and wire the Interaction under an active conversation turn lock."""
        if t_start is None:
            t_start = time.perf_counter()
        conversation = self.conversation
        if not conversation:
            return "init_error"

        t_create = time.perf_counter()
        try:
            from jvagent.action.model.context import set_interaction

            self.interaction = await conversation.create_interaction(
                utterance=self.utterance,
                channel=self.channel,
                session_id=self.session_id or "",
            )
            set_interaction(self.interaction)
            create_ms = (time.perf_counter() - t_create) * 1000
            await self.report(
                {
                    "interaction_created": {
                        "interaction_id": self.interaction.id,
                        "user_id": self.user_id,
                        "session_id": self.session_id,
                    }
                }
            )
            logger.info(
                "interact_init_bootstrap",
                extra={
                    "outcome": "ok",
                    "agent_id": self.agent_id,
                    "interaction_id": self.interaction.id,
                    "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 3),
                    "create_interaction_ms": round(create_ms, 3),
                },
            )
            return "ok"
        except Exception as e:
            await self.report({"error": f"Failed to initialize interaction: {e}"})
            logger.error(
                "interact_init_bootstrap create_interaction failed",
                exc_info=True,
                extra={
                    "outcome": "init_error",
                    "agent_id": self.agent_id,
                    "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 3),
                },
            )
            return "init_error"

    async def initialize_interaction(self, agent: "Agent") -> InteractionInitResult:
        """Deterministic phase 1: memory, session, access, create Interaction (no Actions traversal).

        Call this before :meth:`~jvspatial.core.entities.walker.Walker.spawn` for HTTP
        endpoints so streaming and non-streaming share the same startup semantics.
        Programmatic callers may still use ``spawn(agent)`` alone; :meth:`on_agent` runs
        the same bootstrap when ``interaction`` is not yet set.
        """
        self._agent = agent
        if self.interaction:
            return InteractionInitResult(True, "ok", detail="already_initialized")
        code = await self._bootstrap_interaction(agent)
        if code == "ok" and self.interaction:
            return InteractionInitResult(True, "ok")
        if code == "no_memory":
            return InteractionInitResult(False, "no_memory")
        if code == "access_denied":
            return InteractionInitResult(False, "access_denied")
        if code == "session_resolution_error":
            return InteractionInitResult(False, "session_resolution_error")
        return InteractionInitResult(False, "init_error")

    @on_visit("Agent")
    async def on_agent(self, here: "Agent") -> None:
        """Visit Agent node and walk to Actions node.

        Args:
            here: The Agent node being visited
        """
        self._agent = here

        if not self.conversation and not self.interaction:
            pre = await self._bootstrap_interaction(here, through="session")
            if pre != "ready":
                self._bootstrap_error = pre
                return

        conv_id = getattr(self.conversation, "id", None) if self.conversation else None

        async def _execute_turn() -> None:
            if not self.interaction:
                code = await self._bootstrap_interaction(here, through="create")
                if not self.interaction:
                    self._bootstrap_error = code
                    return
            await self._visit_agent_actions(here)

        if conv_id:
            from jvagent.memory.distributed_conversation_lock import (
                conversation_mutation_lock,
            )

            async with conversation_mutation_lock(conv_id):
                await _execute_turn()
        else:
            await _execute_turn()

    async def _visit_agent_actions(self, here: "Agent") -> None:
        """Traverse Actions → InteractActions under the conversation turn lock."""
        actions_node = await here.get_actions_manager()
        if not actions_node:
            await self.report({"error": "Agent has no Actions node"})
            return

        await self.visit(actions_node)
        await self._finalize()

    async def _finalize(self) -> None:
        """Finalize the current interaction (attach observability, emit signal, clean buffers).

        Called at end of on_agent() so finalization happens whether the walker is
        used from an HTTP endpoint or programmatically.
        """
        if not self.response_bus or not self.interaction:
            return
        try:
            await self.response_bus.finalize_interaction(
                interaction_id=self.interaction.id,
                interaction=self.interaction,
                session_id=self.session_id or "",
                channel=self.channel,
            )
        except Exception as e:
            logger.error(f"Failed to finalize interaction: {e}", exc_info=True)

    @on_visit("Actions")
    async def on_actions(self, here: Any) -> None:
        """Visit Actions node and queue top-level InteractActions for traversal.

        Gets all connected InteractActions, filters to enabled ones, sorts by weight
        (weight is only considered at this top tier), and queues them for traversal.
        The InteractRouter (if present) will curate the walk path after it executes.

        Note:
            Top-level InteractActions (those directly connected to the Actions node)
            must explicitly route the walker to their children within their execute()
            method. The walker does not automatically traverse child InteractActions
            from top-level actions - this allows for conditional routing based on
            the action's internal logic and state.

        Args:
            here: The Actions node being visited
        """
        from jvagent.action.base import Action
        from jvagent.action.interact.base import InteractAction

        # Debug: Get all actions first to see what's available
        all_actions = await here.nodes(node=Action)
        action_info = []
        for a in all_actions:
            try:
                class_name = a.get_class_name()
                action_info.append(f"{a.label} ({class_name}, enabled={a.enabled})")
            except Exception as e:
                action_info.append(f"{a.label} (error getting class name: {e})")
        logger.debug(
            f"InteractWalker: Found {len(all_actions)} total actions connected to Actions node: {action_info}"
        )

        # Try to get enabled InteractActions from cache first
        cached_actions = await get_cached_actions(self.agent_id, enabled_only=True)

        if cached_actions is not None:
            # Cache hit - use cached actions
            enabled_actions: List[InteractAction] = cached_actions
            logger.debug(
                f"InteractWalker: Using {len(enabled_actions)} cached actions for agent {self.agent_id}"
            )
        else:
            # Cache miss - query database
            # Get all enabled InteractActions (forward direction from Actions node)
            # Use class type instead of string to match by isinstance() (includes subclasses like InteractRouter)
            # Filter by enabled=True directly in the query using kwargs
            enabled_actions = await here.nodes(node=InteractAction, enabled=True)
            # Cache the result for future requests
            await cache_actions(self.agent_id, enabled_actions, enabled_only=True)

        if not enabled_actions:
            # Debug: Check if there are any InteractActions at all (even disabled)
            all_interact_actions = await here.nodes(node=InteractAction)
            logger.warning(
                f"InteractWalker: No enabled InteractActions found. "
                f"Total InteractActions: {len(all_interact_actions)}, "
                f"Enabled: {[a.label for a in all_interact_actions if a.enabled]}, "
                f"Disabled: {[a.label for a in all_interact_actions if not a.enabled]}"
            )
            await self.report({"info": "No enabled InteractActions found"})
            return

        # Sort by weight (negative first, then ascending)
        # Actions with same weight maintain descriptor order (stable sort)
        ordered_actions = sorted(enabled_actions, key=lambda a: a.weight)

        await self.report(
            {
                "interact_actions_found": {
                    "count": len(ordered_actions),
                    "actions": [a.label for a in ordered_actions],
                }
            }
        )

        # Queue actions for traversal (walker will process them via @on_visit)
        await self.visit(ordered_actions)

    @on_visit(InteractAction)
    async def on_interact_action(self, here: "InteractAction") -> None:
        """Visit an InteractAction node and execute it.

        This method is automatically called when the walker visits an InteractAction.
        It executes the action's execute() method. The walker continues naturally to
        process the next action in the queue.

        Args:
            here: The InteractAction node being visited
        """
        if not here.enabled:
            await self.report(
                {
                    "action_skipped": {
                        "action": here.label,
                        "weight": here.weight,
                        "reason": "action is disabled",
                    }
                }
            )
            return

        # Background action check: defer to post-interaction phase (access enforced here and at run)
        if getattr(here, "run_in_background", False):
            if not await self.enforce_interact_action_access(
                here, stage="walker_deferred"
            ):
                return
            self.background_actions.append(here)
            await self.report(
                {
                    "action_deferred": {
                        "action": here.label,
                        "weight": here.weight,
                        "reason": "run_in_background=True",
                    }
                }
            )
            return

        if not await self.enforce_interact_action_access(here, stage="walker"):
            return

        try:
            # Store current action for convenience methods and reset skip flag
            self._current_action = here
            self._skip_current_action_record = False

            # Record action execution BEFORE execution to ensure it appears before
            # any actions it calls (like PersonaAction). This preserves the call order
            # in the actions list - the calling action appears before the called action.
            if self.interaction and not self._skip_current_action_record:
                await self.record_action_execution()

            # Execute the action
            # Note: 'here' is the node (self from node's perspective), 'self' is the walker (visitor)
            await here.execute(self)

            await self.report(
                {
                    "action_executed": {
                        "action": here.label,
                        "weight": here.weight,
                        "class": here.get_class_name(),
                    }
                }
            )

        except Exception as e:
            # Log to console (database logging handled automatically by DBLogHandler)
            agent_id = here.agent_id if hasattr(here, "agent_id") else None
            interaction_id = self.interaction.id if self.interaction else None
            session_id = self.session_id
            user_id = self.user_id

            logger.error(  # type: ignore[call-arg]  # details= via _logging_compat
                f"Error processing InteractAction {here.label}: {e}",
                exc_info=True,
                details={
                    "agent_id": agent_id,
                    "interaction_id": interaction_id,
                    "session_id": session_id,
                    "user_id": user_id,
                    "action_class": (
                        here.get_class_name()
                        if hasattr(here, "get_class_name")
                        else here.__class__.__name__
                    ),
                    "action_id": here.id,
                    "action_label": here.label,
                    "context": "execute",
                    "error_code": "interact_action_execute_error",
                },
            )

            await self.report(
                {
                    "error": f"Failed to process {here.label}",
                    "exception": str(e),
                }
            )
            # Continue to next action (don't raise, let walker continue)
        finally:
            # ALWAYS commit pending adhoc, even on error
            if self.response_bus and self.interaction:
                try:
                    await self.response_bus.commit_pending_adhoc(
                        self.interaction.id,
                        self.interaction,
                    )
                    await self.response_bus.commit_pending_thoughts(
                        self.interaction.id,
                        self.interaction,
                    )
                except Exception as e:
                    logger.error(f"Failed to commit pending response streams: {e}")

            # Always clear current action and skip flag after execution
            self._current_action = None
            self._skip_current_action_record = False

    async def curate_walk_path(
        self, actions: List["InteractAction"]
    ) -> List["InteractAction"]:
        """Curate the walker's current walk path to the specified actions.

        Filters the current queue: only actions that are BOTH in the
        current queue AND in ``actions`` are kept. Actions not in the
        queue are NOT prepended — they would otherwise cause re-entry
        loops when the supplied list contains the action that is
        currently being visited (e.g. an orchestrator's own self-re-add
        path).

        AUDIT-interact CRIT-03 fix: the original silent-drop of routed
        sub-InteractActions stays in place at this layer — drop is a
        precondition for the orchestrator's walker-revisit loop, not a bug.
        Callers that need to ROUTE TO a sub-IA must instead use
        :meth:`visit` / :meth:`prepend` explicitly. The previous Wave B
        attempt to "include" un-queued actions caused infinite orchestrator
        re-entry; this revert preserves correctness.

        AUDIT-interact MED-13: items lacking ``.id`` are skipped with a
        warning rather than raising AttributeError.

        Args:
            actions: List of InteractActions to keep in the walk path.

        Returns:
            List of actions that are now in the walk path, in order.
        """
        # Get current queue.
        current_queue = await self.get_queue()

        # Filter to only InteractActions; index by id for fast lookup.
        interact_actions_in_queue: List["InteractAction"] = []
        queued_by_id: dict = {}
        seen_missing_id: set = set()
        for item in current_queue:
            if not isinstance(item, InteractAction):
                continue
            item_id = getattr(item, "id", None)
            if not item_id:
                cls_name = type(item).__name__
                if cls_name not in seen_missing_id:
                    seen_missing_id.add(cls_name)
                    logger.warning(
                        "curate_walk_path: queued InteractAction %r has no .id; "
                        "skipping (MED-13)",
                        cls_name,
                    )
                continue
            interact_actions_in_queue.append(item)
            queued_by_id[item_id] = item

        interact_actions_requested = [
            a for a in actions if isinstance(a, InteractAction)
        ]

        actions_set: set = set()
        for a in interact_actions_requested:
            a_id = getattr(a, "id", None)
            if a_id:
                actions_set.add(a_id)

        actions_to_keep = [
            item for item in interact_actions_in_queue if item.id in actions_set
        ]

        # Remove all InteractActions from queue, then re-add only the
        # intersection (queue ∩ actions) in the order of ``actions``.
        if interact_actions_in_queue:
            await self.dequeue(interact_actions_in_queue)

        dropped_ids: List[str] = []
        for a in interact_actions_requested:
            a_id = getattr(a, "id", None)
            if a_id and a_id not in queued_by_id:
                dropped_ids.append(a_id)
        if dropped_ids:
            logger.debug(
                "curate_walk_path: %d caller-supplied action(s) were not in "
                "the queue and were NOT added (use prepend()/visit() to enqueue "
                "sub-InteractActions explicitly): %s",
                len(dropped_ids),
                dropped_ids,
            )

        curated_order: List["InteractAction"] = []
        for action in interact_actions_requested:
            action_id = getattr(action, "id", None)
            if not action_id:
                continue
            for queued_action in actions_to_keep:
                if queued_action.id == action_id:
                    curated_order.append(queued_action)
                    break

        if curated_order:
            await self.prepend(curated_order)

        return curated_order

    async def set_walk_path(
        self, actions: List["InteractAction"]
    ) -> List["InteractAction"]:
        """Replace the walker's current walk path with the provided actions.

        This method clears all InteractActions from the queue and replaces them
        with the provided actions in the specified order.

        Args:
            actions: List of InteractActions to set as the new walk path

        Returns:
            List of actions that were added to the queue
        """
        # Get current queue
        current_queue = await self.get_queue()

        # Remove all InteractActions from queue
        interact_actions_in_queue = [
            item for item in current_queue if isinstance(item, InteractAction)
        ]
        if interact_actions_in_queue:
            await self.dequeue(interact_actions_in_queue)

        # Add new actions to queue in the order provided
        if actions:
            await self.prepend(actions)

        return actions

    async def add_directives(self, directives: List[str]) -> None:
        """Add multiple directives to the interaction with current action name.

        Bulk convenience method that automatically uses the current executing action's
        class name. Must be called from within an InteractAction's execute() method.
        The interaction is automatically saved once after adding all directives.

        Args:
            directives: List of directive strings to add

        Raises:
            RuntimeError: If called outside of action execution context or no interaction available
        """
        if not self.interaction:
            raise RuntimeError("No interaction available")
        if not self._current_action:
            raise RuntimeError(
                "add_directives() must be called from within InteractAction.execute()"
            )

        if not directives:
            return

        action_name = self._current_action.get_class_name()
        # Filter out empty directives before calling bulk method
        valid_directives = [d for d in directives if d]
        if valid_directives:
            # Only save if directives were actually added (not duplicates)
            if self.interaction.add_directives(valid_directives, action_name):
                await self.interaction.save()

    async def add_directive(self, directive: str) -> None:
        """Add a directive to the interaction with current action name.

        Convenience method that automatically uses the current executing action's
        class name. Must be called from within an InteractAction's execute() method.
        The interaction is automatically saved after adding the directive.

        Args:
            directive: Directive string to add

        Raises:
            RuntimeError: If called outside of action execution context or no interaction available
        """
        await self.add_directives([directive])

    async def add_event(self, event: str) -> None:
        """Add an event to the interaction with current action name.

        Convenience method that automatically uses the current executing action's
        class name. Must be called from within an InteractAction's execute() method.
        The interaction is automatically saved after adding the event.

        Args:
            event: Event string to add

        Raises:
            RuntimeError: If called outside of action execution context or no interaction available
        """
        if not self.interaction:
            raise RuntimeError("No interaction available")
        if not self._current_action:
            raise RuntimeError(
                "add_event() must be called from within InteractAction.execute()"
            )

        action_name = self._current_action.get_class_name()
        # Only save if event was actually added (not invalid)
        if self.interaction.add_event(event, action_name):
            await self.interaction.save()

    async def add_parameters(self, parameters: List[Dict[str, Any]]) -> None:
        """Add multiple parameters to the interaction with current action name.

        Bulk convenience method that automatically uses the current executing action's
        class name. Must be called from within an InteractAction's execute() method.
        The interaction is automatically saved once after adding all parameters.

        Args:
            parameters: List of parameter dictionaries to add (each should have 'condition' and 'response' keys)

        Raises:
            RuntimeError: If called outside of action execution context or no interaction available
        """
        if not self.interaction:
            raise RuntimeError("No interaction available")
        if not self._current_action:
            raise RuntimeError(
                "add_parameters() must be called from within InteractAction.execute()"
            )

        if not parameters:
            return

        action_name = self._current_action.get_class_name()
        # Filter and validate parameters before calling bulk method
        valid_parameters = []
        for parameter in parameters:
            if parameter and isinstance(parameter, dict):
                valid_parameters.append(parameter)
            elif parameter:
                logger.warning(
                    f"add_parameters: Skipping invalid parameter type: {type(parameter)}, value: {parameter}"
                )

        if valid_parameters:
            self.interaction.add_parameters(valid_parameters, action_name)
            await self.interaction.save()

    async def add_parameter(self, parameter: Dict[str, Any]) -> None:
        """Add a parameter to the interaction with current action name.

        Convenience method that automatically uses the current executing action's
        class name. Must be called from within an InteractAction's execute() method.
        The interaction is automatically saved after adding the parameter.

        Args:
            parameter: Parameter data (id, condition, response, etc.)

        Raises:
            RuntimeError: If called outside of action execution context or no interaction available
        """
        await self.add_parameters([parameter])


# Rebuild Pydantic model to resolve forward references
InteractWalker.model_rebuild()
