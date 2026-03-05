"""UserModelInteractAction

A passive action that observes conversations and maintains a user profile
by extracting facts and preferences using an LLM.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.core.app import App
from jvagent.memory import Interaction

from .prompts import CUSTOM_USER_MODEL_UPDATE_PROMPT, USER_MODEL_UPDATE_PROMPT

logger = logging.getLogger(__name__)


class UserModelInteractAction(InteractAction):
    """Passive action that maintains a user profile by analyzing conversations.

    This action runs after interactions complete and updates the user's profile
    with facts and preferences extracted from the conversation history.
    """

    # Model Configuration
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Entity type of the LanguageModelAction to use",
    )
    model: str = attribute(
        default="gpt-4o",
        description="Model for user profile updates",
    )
    model_temperature: float = attribute(
        default=0.1, description="Low temperature for consistent extraction"
    )
    model_max_tokens: int = attribute(
        default=1000, description="Max tokens for profile updates"
    )
    points_of_interest: List[str] = attribute(
        default=[], description="Points of interest to extract from conversation"
    )
    update_frequency: int = attribute(
        default=3,
        description="Update user model every N interactions (1 = every interaction)",
    )
    user_model_update_prompt: str = attribute(
        default=USER_MODEL_UPDATE_PROMPT,
        description="Custom prompt to use for user model updates",
    )
    user_model_update_custom_prompt: str = attribute(
        default=CUSTOM_USER_MODEL_UPDATE_PROMPT,
        description="Custom prompt to use for user model updates",
    )

    history_limit: int = attribute(
        default=6, description="Number of recent interactions to analyze"
    )
    weight: int = attribute(default=150, description="Weight of the action")
    always_execute: bool = attribute(
        default=True, description="Always execute the action"
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute the user model action."""
        interaction = visitor.interaction
        conv_history = await self._get_conversation_history(
            interaction, self.history_limit
        )

        # Count interactions after the last event
        num_interactions = self.count_interactions_after_event(conv_history)

        if num_interactions >= self.update_frequency:
            await self._update_user_profile(visitor)

    def count_interactions_after_event(
        self,
        conversation_history: List[Dict[str, Any]],
        event_pattern: str = "[EVENT] UserModelAction: Updated user profile",
    ) -> int:
        """
        Count the number of interactions (user messages and assistant responses)
        after the last occurrence of a specific event pattern in the conversation history.

        Args:
            conversation_history: List of message dictionaries with 'role' and 'content' keys
            event_pattern: The pattern to search for in content

        Returns:
            int: Number of interactions after the last event occurrence
        """
        # Find the index of the last event message
        last_event_index = -1
        for i, message in enumerate(conversation_history):
            if message.get("role") == "system" and event_pattern in message.get(
                "content", ""
            ):
                last_event_index = i

        # If no event found, return 0 or total count based on preference
        if last_event_index == -1:
            logger.debug("No matching event found in conversation history")
            return sum(1 for e in conversation_history if e.get("role") == "user")

        # Count messages after the last event
        messages_after_event = conversation_history[last_event_index + 1 :]

        # Count both user and assistant messages as interactions
        # (excluding the event message itself which is a system message)
        interaction_count = sum(
            1 for e in messages_after_event if e.get("role") == "user"
        )

        return interaction_count

    async def _update_user_profile(self, visitor: "InteractWalker") -> None:
        """Analyze conversation and update user profile."""

        # Get user
        interaction = visitor.interaction
        user = await interaction.get_user()
        user_id = visitor.user_id
        if not user:
            logger.debug("UserModelAction: No user found, skipping")
            return

        # Get model action
        model_action = await self.get_action(self.model_action_type)
        if not model_action:
            logger.warning(
                f"UserModelAction: Model action '{self.model_action_type}' not found"
            )
            return

        # Get conversation history
        history = await self._get_conversation_history(interaction, self.history_limit)
        if not history:
            return

        # Format history for prompt
        history_text = "\n".join(
            [f"{msg['role'].upper()}: {msg['content']}" for msg in history]
        )

        # Get current user model (now stored as markdown string)
        current_model = user.user_model["profile"] if user.user_model else ""
        app = await App.get()
        if app:
            # Get formatted string
            today_date_str = await app.now("%A, %B %d, %Y")

        # Construct system prompt
        if self.points_of_interest:
            prompt = self.user_model_update_custom_prompt.format(
                points_of_interest=self.points_of_interest,
                current_model=current_model,
                today=today_date_str,
            )
        else:
            prompt = self.user_model_update_prompt.format(
                current_model=current_model, today=today_date_str
            )

        # Call LLM with conversation history
        try:
            response = await model_action.generate(
                prompt=prompt,
                model=self.model,
                history=history,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                response_format={"type": "json_object"},
            )

            if not response:
                logger.warning("UserModelAction: Empty response from LLM")
                return

            # Clean markdown response
            new_model = self._parse_model_response(response)
            if not new_model or new_model.strip() == "No updates":
                interaction.add_event(
                    "UserModelAction: Updated user profile - No actual updates",
                    self.get_class_name(),
                )
                logger.debug("UserModelAction: No updates needed")
                return

            # Check if model changed
            if new_model.strip() != current_model.strip():
                logger.info(f"UserModelAction: Updating profile for user {user_id}")

                user.user_model = {"profile": new_model}
                await user.save()

                from jvagent.action.pageindex.documents import (
                    delete_document,
                    list_documents,
                )
                from jvagent.action.pageindex.endpoints import _do_assimilate

                # Delete existing user model doc(s) for this user to avoid duplicates
                try:
                    existing_docs = await list_documents(
                        collection_name="user_model",
                        metadata_filter={"user_id": user_id},
                    )
                    for doc in existing_docs:
                        doc_name = doc.get("doc_name")
                        if doc_name:
                            await delete_document(
                                doc_name, collection_name="user_model"
                            )
                            logger.info(
                                f"Deleted existing user model doc: {doc_name} for user {user_id}"
                            )
                except Exception as e:
                    logger.warning(f"Failed to clean up existing user model docs: {e}")

                await _do_assimilate(
                    content=new_model.encode("utf-8"),
                    ext="md",
                    doc_name=f"user_model_{user_id}",
                    model=self.model,
                    if_add_node_summary=True,
                    collection_name="user_model",
                    metadata={"user_id": user_id, "type": "user_model"},
                    doc_description="User model",
                )

                # Log event
                interaction.add_event(
                    "UserModelAction: Updated user profile", self.get_class_name()
                )
                await interaction.save()
            else:
                interaction.add_event(
                    "UserModelAction: Updated user profile - No meaningful changes detected",
                    self.get_class_name(),
                )
                logger.debug("UserModelAction: No meaningful changes detected")

        except Exception as e:
            logger.error(f"UserModelAction: Error in LLM call: {e}", exc_info=True)

    def _parse_model_response(self, response: str) -> Optional[str]:
        """Parse and clean the markdown LLM response."""
        # Clean up potential markdown code blocks
        response_clean = response.strip()
        if response_clean.startswith("```markdown"):
            response_clean = response_clean[11:]
        if response_clean.startswith("```"):
            response_clean = response_clean[3:]
        if response_clean.endswith("```"):
            response_clean = response_clean[:-3]
        response_clean = response_clean.strip()

        return response_clean if response_clean else None

    async def _get_conversation_history(
        self,
        interaction: Interaction,
        history_limit: int,
        with_utterance: bool = True,
        with_response: bool = True,
        with_interpretation: bool = False,
        with_event: bool = True,
        max_statement_length: Optional[int] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Get formatted conversation history for the language model.

        Includes previous interactions and the current interaction (if it has a response)
        as part of the conversation flow. This provides natural multi-call awareness
        within a single interaction by including the current interaction's existing
        response in the history.

        Args:
            interaction: Current interaction
            history_limit: Number of past interactions to include
            with_utterance: Include user utterances in history (default: True)
            with_response: Include AI responses in history (default: True)
            with_interpretation: Include interpretations in history (default: False)
            with_event: Include events in history (default: False)
            max_statement_length: Truncate utterances/responses to this length (default: None)

        Returns:
            List of message dictionaries with 'role' and 'content' keys, or None if disabled
        """
        if history_limit <= 0:
            return None

        from jvagent.memory.conversation import Conversation

        # Get conversation
        conversation = await Conversation.get(interaction.conversation_id)
        if not conversation:
            return []

        # Get conversation history from previous interactions (excluding current)
        history = await conversation.get_interaction_history(
            limit=history_limit,
            excluded=interaction.id,
            with_utterance=with_utterance,
            with_response=with_response,
            with_interpretation=with_interpretation,
            with_event=with_event,
            formatted=True,
            max_statement_length=max_statement_length,
        )

        # Helper function for truncation
        def _truncate(content: str) -> str:
            if max_statement_length and len(content) > max_statement_length:
                return content[:max_statement_length] + "..."
            return content

        # Include current interaction's utterance and response in history if present (multi-call awareness)
        # Important: Add utterance BEFORE response to maintain chronological order
        if with_utterance and with_response and interaction.response:
            # Add current utterance first
            history.append(
                {
                    "role": "user",
                    "content": _truncate(interaction.utterance),
                }
            )
            # Then add current response
            history.append(
                {
                    "role": "assistant",
                    "content": _truncate(interaction.response),
                }
            )
        elif with_response and interaction.response:
            # Only add response if utterance not requested
            history.append(
                {
                    "role": "assistant",
                    "content": _truncate(interaction.response),
                }
            )

        return history if history else []
