"""UserModelAction

Collects user preferences using an LLM to analyze conversation history and update
the user's profile.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.memory import Interaction

from .prompts import USER_MODEL_UPDATE_PROMPT

logger = logging.getLogger(__name__)


class UserModelAction(InteractAction):
    """InteractAction that extracts user preferences from conversation history
    using an LLM and stores them in the conversation context under the `user_model` key.
    """

    weight: int = attribute(
        default=-75,
        description="Weight for when this action should be executed.",
    )
    always_execute: bool = attribute(
        default=True, description="Whether to execute this action on every interaction."
    )

    # Model Configuration
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Entity type of the LanguageModelAction to use (e.g., OpenAILanguageModelAction)",
    )
    model: str = attribute(
        default="gpt-4o", description="Default model name for user model updates"
    )
    model_temperature: float = attribute(
        default=0.0, description="Temperature for LLM generation"
    )
    model_max_tokens: int = attribute(
        default=4096, description="Max tokens for LLM generation"
    )

    async def execute(self, visitor: "InteractWalker") -> None:
        """Execute the user model update."""
        print(f"\033[94mExecuting UserModelAction for interaction\033[0m")
        interaction = visitor.interaction
        if not interaction:
            return

        user = await interaction.get_user()
        if not user:
            return

        # Determine if we should run (could add logic here to not run every single turn if expensive)
        # For now, we run every turn as requested, but maybe check if there is meaningful content.
        utterance = (visitor.utterance or interaction.utterance or "").strip()
        if not utterance and not interaction.response:
            # If there's literally no text to analyze from this turn, skip
            return

        try:
            # Get model action
            model_action = await self.get_action(self.model_action_type)
            if not model_action:
                logger.warning(
                    f"UserModelAction: Model action '{self.model_action_type}' not found."
                )
                return

            # Get conversation history
            history = await self._get_conversation_history(interaction, history_limit=5)
            if not history:
                return

            # Format history for prompt
            history_text = "\n".join(
                [f"{msg['role'].upper()}: {msg['content']}" for msg in history]
            )

            # Get current user model
            current_model = user.user_model if user.user_model else {}

            # Construct prompt
            prompt = USER_MODEL_UPDATE_PROMPT.format(
                user_model=json.dumps(current_model, indent=2)
            )

            # Call LLM
            response = await model_action.generate(
                prompt=prompt,
                model=self.model,
                history=history_text,
                temperature=self.model_temperature,
                max_tokens=self.model_max_tokens,
                response_format={"type": "json_object"},
            )

            if not response:
                return

            # Parse response
            try:
                # Clean up potential markdown code blocks
                response_clean = response.strip()
                if response_clean.startswith("```json"):
                    response_clean = response_clean[7:]
                if response_clean.startswith("```"):
                    response_clean = response_clean[3:]
                if response_clean.endswith("```"):
                    response_clean = response_clean[:-3]
                response_clean = response_clean.strip()

                new_model = json.loads(response_clean)
            except json.JSONDecodeError:
                logger.warning(
                    f"UserModelAction: Failed to parse JSON response: {response}"
                )
                return

            # Check if model changed
            if new_model != current_model:
                print(f"\033[95mUpdating user model for user: {user.id}\033[0m")
                print(f"\033[95mNew Model: {new_model}\033[0m")

                user.user_model = new_model

                # Log event
                interaction.add_event(
                    f"UserModelAction: updated profile", self.get_class_name()
                )
                await interaction.save()

        except Exception as e:
            logger.error(f"UserModelAction: Error during execution: {e}", exc_info=True)

    async def _get_conversation_history(
        self,
        interaction: Interaction,
        history_limit: int,
        with_utterance: bool = True,
        with_response: bool = True,
        with_interpretation: bool = False,
        with_event: bool = False,
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
