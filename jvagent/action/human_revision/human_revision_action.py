"""Human-in-the-Loop Approval Action.

A generic, reusable approval gate that sends any payload (script, post, document, etc.)
to a designated WhatsApp number for human review and interprets the reply as:

  - ``approved``  → caller proceeds (e.g. publishes broadcast).
  - ``revision``  → caller regenerates with optional reviewer instructions injected.
  - ``rejected``  → caller aborts entirely.

Only one review can be *pending* at a time.  Additional requests are saved with
status ``waiting`` and are automatically promoted once the active review resolves.
"""

import logging
import os
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import ConfigDict
from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outcome enum
# ---------------------------------------------------------------------------


class RevisionOutcome(str, Enum):
    """Possible outcomes of a human review."""

    APPROVED = "approved"
    REVISION = "revision"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Persistence node
# ---------------------------------------------------------------------------


class RevisionRequest(Action):
    """Graph node that persists a single human-revision review request."""

    context_key: str = attribute(
        default="",
        description="Caller-supplied unique key (e.g. video_id) identifying this review.",
    )
    callback_action: str = attribute(
        default="",
        description="Class name of the action to call back when the review resolves.",
    )
    payload_text: str = attribute(
        default="",
        description="The content that was sent to the reviewer.",
    )
    metadata: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Arbitrary extra data echoed back in the callback (e.g. agent_id, summary).",
    )
    status: str = attribute(
        default="pending",
        description="Review status: pending | waiting | approved | revision | rejected.",
    )
    reviewer_phone: str = attribute(
        default="",
        description="WhatsApp number the request was (or will be) sent to.",
    )
    reviewer_reply: str = attribute(
        default="",
        description="Raw reply text from the reviewer.",
    )
    revision_instructions: str = attribute(
        default="",
        description=(
            "Parsed revision instructions extracted from the reviewer's reply "
            "(text after the revision trigger word/phrase)."
        ),
    )
    created_at: str = attribute(
        default="",
        description="ISO-8601 UTC timestamp at which this request was created.",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ---------------------------------------------------------------------------
# Main action
# ---------------------------------------------------------------------------


class HumanRevisionAction(Action):
    """Generic Human-in-the-Loop revision gate.

    Sends any payload to a designated WhatsApp number for review and interprets
    replies as *approved*, *revision* (with optional instructions), or *rejected*
    before invoking a callback on the originating action via ``on_revision_outcome``.

    Only one :class:`RevisionRequest` can be **pending** at a time.  Additional
    calls to :meth:`request_approval` while a review is active will save the new
    request as **waiting** and promote it automatically once the active one resolves.
    """

    reviewer_phone: Optional[str] = attribute(
        default=None,
        description=(
            "WhatsApp number that will receive approval requests. "
            "Falls back to the APPROVAL_REVIEWER_PHONE environment variable."
        ),
    )

    approval_keywords: List[str] = attribute(
        default_factory=lambda: [
            "yes",
            "approve",
            "approved",
            "ok",
            "good to go",
            "lgtm",
            "looks good",
        ],
        description="Words/phrases that signal the reviewer approves the payload.",
    )

    revision_keywords: List[str] = attribute(
        default_factory=lambda: [
            "revise",
            "redo",
            "revision",
            "edit",
            "change",
            "again",
            "rewrite",
            "update",
            "fix",
            "modify",
        ],
        description=(
            "Words/phrases that signal the reviewer wants the payload revised. "
            "Any text after the trigger word is treated as specific revision instructions."
        ),
    )

    rejection_keywords: List[str] = attribute(
        default_factory=lambda: [
            "no",
            "reject",
            "rejected",
            "deny",
            "stop",
            "cancel",
            "abort",
            "kill",
            "drop",
            "nope",
        ],
        description="Words/phrases that signal the reviewer rejects the payload.",
    )

    use_llm_classification: bool = attribute(
        default=False,
        description=(
            "When True, falls back to the LLM to classify a reply that does not "
            "match any keyword bucket.  When False (default), unrecognised replies "
            "are returned as ApprovalOutcome.UNKNOWN and no callback is invoked."
        ),
    )

    message_template: str = attribute(
        default=(
            "*Review Request*\n\n"
            "Please review the following content before it is published:\n\n"
            "---\n"
            "{payload_text}\n"
            "---\n\n"
            "Reply with:\n"
            "*approve* — to publish as-is\n"
            "*revise [instructions]* — to request edits (add instructions after the word)\n"
            "*reject* — to cancel entirely"
        ),
        description=(
            "WhatsApp message template.  Use ``{payload_text}`` as the placeholder "
            "for the content being reviewed."
        ),
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _apply_env_defaults(self) -> None:
        """Fill ``reviewer_phone`` from env var if not explicitly set."""
        if not self.reviewer_phone or not self.reviewer_phone.strip():
            env_phone = os.environ.get("REVISION_REVIEWER_PHONE") or os.environ.get("APPROVAL_REVIEWER_PHONE", "").strip()
            if env_phone:
                self.reviewer_phone = env_phone
                logger.debug(
                    f"HumanRevisionAction: using REVISION_REVIEWER_PHONE from environment: {env_phone}"
                )

    async def on_register(self) -> None:
        self._apply_env_defaults()
        if not self.reviewer_phone:
            logger.debug("HumanRevisionAction: reviewer_phone not configured — action will be inactive.")
        else:
            logger.debug(f"HumanRevisionAction registered (reviewer: {self.reviewer_phone})")

    def is_configured(self) -> bool:
        """Return True only when a reviewer phone number is available."""
        return bool(self.reviewer_phone and self.reviewer_phone.strip())

    def get_capabilities(self) -> List[str]:
        if not self.enabled or not self.is_configured():
            return []
        return [
            "Gate any generated content through a human reviewer on WhatsApp before publishing",
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def request_approval(
        self,
        payload_text: str,
        context_key: str,
        callback_action: str,
        metadata: Optional[Dict[str, Any]] = None,
        agent_id: Optional[str] = None,
    ) -> "RevisionRequest":
        """Submit content for human review.

        If a review is already *pending* for this agent, the new request is
        saved as *waiting* and will be promoted automatically once the active
        one resolves.

        Args:
            payload_text:    The content to send to the reviewer.
            context_key:     A unique identifier for this review (e.g. a video ID).
            callback_action: Class name of the action that implements
                             ``on_revision_outcome``.
            metadata:        Arbitrary dict echoed back in the callback.
            agent_id:        Agent ID used for scoping the active-review query.
                             Inferred from the action's agent if not supplied.

        Returns:
            The persisted :class:`RevisionRequest` node.
        """
        self._apply_env_defaults()
        if not self.is_configured():
            raise ValueError(
                "HumanRevisionAction: reviewer_phone is not configured. "
                "Set REVISION_REVIEWER_PHONE or agent.yaml reviewer_phone."
            )

        metadata = metadata or {}

        # Resolve agent_id for scoping
        if not agent_id:
            try:
                agent = await self.get_agent()
                agent_id = str(agent.id) if agent else "unknown"
            except Exception:
                agent_id = "unknown"

        metadata.setdefault("agent_id", agent_id)

        # Check whether a review is already pending
        pending_exists = await self._has_pending_request(agent_id)
        status = "waiting" if pending_exists else "pending"

        # Persist the request node
        request = await RevisionRequest.create(
            context_key=context_key,
            callback_action=callback_action,
            payload_text=payload_text,
            metadata=metadata,
            status=status,
            reviewer_phone=self.reviewer_phone,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        await self.connect(request)

        if status == "pending":
            await self._send_review_message(request)
            logger.info(
                f"HumanRevisionAction: revision request '{context_key}' sent to {self.reviewer_phone}"
            )
        else:
            logger.info(
                f"HumanRevisionAction: revision request '{context_key}' queued (waiting — another review is active)"
            )

        return request

    async def handle_reply(self, sender_phone: str, reply_text: str) -> bool:
        """Process an incoming WhatsApp reply from the reviewer.

        Classifies the reply, updates the :class:`ApprovalRequest` node,
        invokes the calling action's ``on_revision_outcome`` callback, and
        promotes the next *waiting* request (if any).

        Args:
            sender_phone: The WhatsApp number that sent the reply.
            reply_text:   The raw text of the reply.

        Returns:
            True if the reply was handled (sender is the reviewer and a pending
            request was found), False otherwise.
        """
        self._apply_env_defaults()

        # Normalise phone for comparison  (strip non-digit chars except leading +)
        if not self._phones_match(sender_phone, self.reviewer_phone):
            return False

        # Find the active pending request
        request = await self._get_pending_request()
        if not request:
            logger.debug(
                "HumanRevisionAction: reply received from reviewer but no pending request found."
            )
            return False

        # Classify
        outcome, revision_instructions = await self._classify_reply(reply_text)

        if outcome == RevisionOutcome.UNKNOWN:
            logger.info(
                f"HumanRevisionAction: could not classify reply '{reply_text[:60]}' "
                "— sending clarification message to reviewer."
            )
            await self._send_clarification(request)
            return True  # Handled (we replied to the reviewer), but review still pending

        # Persist outcome
        request.status = outcome.value
        request.reviewer_reply = reply_text
        request.revision_instructions = revision_instructions or ""
        await request.save()

        logger.info(
            f"HumanRevisionAction: request '{request.context_key}' resolved as '{outcome.value}'"
            + (f" with instructions: '{revision_instructions}'" if revision_instructions else "")
        )

        # Invoke callback on the originating action
        await self._invoke_callback(request, outcome, revision_instructions)

        # Promote the next waiting request (if any)
        await self._promote_next_waiting(request.metadata.get("agent_id", "unknown"))

        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _has_pending_request(self, agent_id: str) -> bool:
        """Return True if there is already a pending revision for this agent."""
        try:
            existing = await RevisionRequest.find_one(
                {"context.status": "pending", "context.metadata.agent_id": agent_id}
            )
            return existing is not None
        except Exception as e:
            logger.warning(f"HumanRevisionAction: error checking pending requests: {e}")
            return False

    async def _get_pending_request(self) -> Optional[RevisionRequest]:
        """Retrieve the currently active (pending) revision request."""
        self._apply_env_defaults()
        try:
            return await RevisionRequest.find_one(
                {
                    "context.status": "pending",
                    "context.reviewer_phone": self.reviewer_phone,
                }
            )
        except Exception as e:
            logger.error(f"HumanRevisionAction: error fetching pending request: {e}")
            return None

    async def _promote_next_waiting(self, agent_id: str) -> None:
        """Find the oldest *waiting* request for this agent and activate it."""
        try:
            # Query for the oldest waiting request (sort by created_at ascending)
            waiting = await RevisionRequest.find_one(
                {
                    "context.status": "waiting",
                    "context.metadata.agent_id": agent_id,
                    "context.reviewer_phone": self.reviewer_phone,
                }
            )
            if not waiting:
                return

            waiting.status = "pending"
            await waiting.save()
            await self._send_review_message(waiting)
            logger.info(
                f"HumanRevisionAction: promoted waiting request '{waiting.context_key}' to pending"
            )
        except Exception as e:
            logger.error(f"HumanRevisionAction: error promoting waiting request: {e}")

    async def _send_review_message(self, request: RevisionRequest) -> None:
        """Send the review message to the reviewer via WhatsAppAction."""
        try:
            whatsapp = await self.get_action("WhatsAppAction")
            if not whatsapp:
                logger.error(
                    "HumanRevisionAction: WhatsAppAction not found — cannot send review message."
                )
                return

            message = self.message_template.format(payload_text=request.payload_text)
            await (await whatsapp.api()).send_message(
                phone=request.reviewer_phone,
                message=message,
            )
        except Exception as e:
            logger.error(f"HumanRevisionAction: failed to send review message: {e}", exc_info=True)

    async def _send_clarification(self, request: RevisionRequest) -> None:
        """Notify the reviewer that their reply was not understood."""
        try:
            whatsapp = await self.get_action("WhatsAppAction")
            if not whatsapp:
                return

            clarification = (
                "(!) I didn't quite get that. Please reply with:\n"
                "*approve* — to publish\n"
                "*revise [optional instructions]* — to request changes\n"
                "*reject* — to cancel"
            )
            await (await whatsapp.api()).send_message(
                phone=request.reviewer_phone,
                message=clarification,
            )
        except Exception as e:
            logger.debug(f"HumanApprovalAction: failed to send clarification: {e}")

    async def _classify_reply(
        self, reply_text: str
    ) -> tuple[RevisionOutcome, Optional[str]]:
        """Classify the reviewer's reply.

        Returns:
            A tuple of (outcome, revision_instructions).  ``revision_instructions``
            is the text after the revision trigger word/phrase (or ``None`` for
            non-revision outcomes).
        """
        normalised = reply_text.strip().lower()

        # --- Revision check (with instruction extraction) ---
        # Highest priority: if they say 'revise', it's always a revision, even if 'no' is present.
        for kw in self.revision_keywords:
            pattern = re.compile(
                r"\b" + re.escape(kw.lower()) + r"\b",
                re.IGNORECASE,
            )
            match = pattern.search(normalised)
            if match:
                # Everything after the trigger word is the revision instructions
                after = reply_text[match.end():].strip()
                # Strip common separators at the start
                after = re.sub(r"^[\-–—:,;]+\s*", "", after).strip()
                instructions = after if after else None
                return RevisionOutcome.REVISION, instructions

        # --- Approval check ---
        # Uses word boundaries to avoid matching partial words (e.g., 'ok' vs 'joke')
        for kw in self.approval_keywords:
            pattern = re.compile(
                r"\b" + re.escape(kw.lower()) + r"\b",
                re.IGNORECASE,
            )
            if pattern.search(normalised):
                return RevisionOutcome.APPROVED, None

        # --- Rejection check ---
        # Uses word boundaries to avoid matching partial words (e.g., 'no' vs 'not')
        for kw in self.rejection_keywords:
            pattern = re.compile(
                r"\b" + re.escape(kw.lower()) + r"\b",
                re.IGNORECASE,
            )
            if pattern.search(normalised):
                return RevisionOutcome.REJECTED, None

        # --- LLM fallback (opt-in) ---
        if self.use_llm_classification:
            return await self._llm_classify(reply_text)

        return RevisionOutcome.UNKNOWN, None

    async def _llm_classify(
        self, reply_text: str
    ) -> tuple[RevisionOutcome, Optional[str]]:
        """Use the language model to classify a reply that matched no keyword."""
        try:
            model_action = await self.get_model_action()
            if not model_action:
                logger.warning("HumanRevisionAction: LLM classification requested but no model action found.")
                return RevisionOutcome.UNKNOWN, None

            prompt = (
                "You are classifying a reviewer's reply to a content approval request.\n"
                "Classify the following reply into exactly one of these three categories:\n"
                "APPROVED  — the reviewer accepts the content as-is\n"
                "  REVISION  — the reviewer wants changes (extract any specific instructions). Do not include emojis in the revision_instructions.\n"
                "  REJECTED  — the reviewer wants the content cancelled/abandoned\n\n"
                f"Reply: \"{reply_text}\"\n\n"
                "Respond in JSON only, e.g.:\n"
                '{"outcome": "APPROVED", "revision_instructions": null}\n'
                '{"outcome": "REVISION", "revision_instructions": "make it shorter"}\n'
                '{"outcome": "REJECTED", "revision_instructions": null}'
            )

            raw = await model_action.generate(
                prompt=prompt,
                calling_action_name=self.get_class_name(),
                model="gpt-4o-mini",
                temperature=0.0,
            )

            import json

            text = str(raw).strip()
            # Strip markdown fences if present
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
            data = json.loads(text)
            outcome_str = data.get("outcome", "UNKNOWN").upper()
            instructions = data.get("revision_instructions") or None

            outcome_map = {
                "APPROVED": RevisionOutcome.APPROVED,
                "REVISION": RevisionOutcome.REVISION,
                "REJECTED": RevisionOutcome.REJECTED,
            }
            outcome = outcome_map.get(outcome_str, RevisionOutcome.UNKNOWN)
            return outcome, instructions

        except Exception as e:
            logger.warning(f"HumanRevisionAction: LLM classification failed: {e}")
            return RevisionOutcome.UNKNOWN, None

    async def _invoke_callback(
        self,
        request: RevisionRequest,
        outcome: RevisionOutcome,
        revision_instructions: Optional[str],
    ) -> None:
        """Locate the originating action and call its ``on_revision_outcome`` method.

        The calling action must be registered on the same agent and implement::

            async def on_revision_outcome(
                self,
                request: RevisionRequest,
                outcome: RevisionOutcome,
                revision_instructions: str | None,
            ) -> None: ...
        """
        callback_class = request.callback_action
        if not callback_class:
            logger.warning("HumanRevisionAction: no callback_action set — cannot invoke callback.")
            return

        try:
            action = await self.get_action(callback_class)
            if not action:
                logger.error(
                    f"HumanRevisionAction: callback action '{callback_class}' not found on agent."
                )
                return

            callback = getattr(action, "on_revision_outcome", None)
            if not callable(callback):
                logger.error(
                    f"HumanRevisionAction: action '{callback_class}' has no 'on_revision_outcome' method."
                )
                return

            await callback(request, outcome, revision_instructions)

        except Exception as e:
            logger.error(
                f"HumanRevisionAction: error invoking callback on '{callback_class}': {e}",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _phones_match(phone_a: str, phone_b: Optional[str]) -> bool:
        """Compare two phone numbers by their digit sequences only."""
        if not phone_a or not phone_b:
            return False

        def digits(p: str) -> str:
            return re.sub(r"\D", "", p)

        return digits(phone_a) == digits(phone_b)
