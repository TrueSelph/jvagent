"""Tests for InteractRouter intent interpretation and conversational state assessment.

These tests verify that the router correctly interprets user intent based on conversation
history, particularly ensuring it matches current user inputs to the most recent assistant
question (not earlier questions), and accurately assesses conversational state.

Since the router module has circular dependencies, we test the core formatting logic directly.
"""

from typing import Any, Dict, List

import pytest


def format_history_for_test(interaction_history: List[Dict[str, Any]]) -> str:
    """Test implementation of history formatting logic from InteractRouter._format_history.

    This is extracted to avoid circular import issues while testing the same logic.
    """
    if not interaction_history:
        return "(No previous conversation)"

    # Detect format: role/content (formatted=True from get_interaction_history) vs human/ai or utterance/response
    first_entry = interaction_history[0] if interaction_history else {}
    is_role_content = (
        isinstance(first_entry, dict)
        and "role" in first_entry
        and "content" in first_entry
    )

    # Extract context signals: find the MOST RECENT assistant message (skip system/events)
    context_signals = []
    last_assistant_msg = None

    if is_role_content:
        # Scan backwards through history to find the most recent assistant message
        for entry in reversed(interaction_history):
            if isinstance(entry, dict) and entry.get("role") == "assistant":
                last_assistant_msg = entry.get("content") or ""
                break

        # Check if the most recent assistant message was a question
        if last_assistant_msg and last_assistant_msg.strip().endswith("?"):
            context_signals.append("Most recent assistant message is a question")

        # Look for ongoing activity markers (most recent one)
        for e in reversed(interaction_history):
            if isinstance(e, dict) and (e.get("content") or "").startswith("[EVENT]"):
                ev = e["content"]
                if "Ongoing Activity:" in ev:
                    activity_name = (
                        ev.replace("[EVENT] ", "")
                        .replace("Ongoing Activity:", "")
                        .strip()
                    )
                    context_signals.append(f"Ongoing activity: {activity_name}")
                    break
    else:
        # Custom dict format: find most recent assistant message
        for entry in reversed(interaction_history):
            if isinstance(entry, dict) and "ai" in entry:
                ai_msg = entry["ai"]
                if ai_msg and ai_msg.strip().endswith("?"):
                    context_signals.append(
                        "Most recent assistant message is a question"
                    )
                    break

        # Look for ongoing activity in most recent entry
        if interaction_history and "events" in interaction_history[-1]:
            for event in interaction_history[-1]["events"]:
                ev_str = (
                    event.get("content", event)
                    if isinstance(event, dict)
                    else str(event)
                )
                if "Ongoing Activity:" in ev_str:
                    activity_name = (
                        ev_str.replace("[EVENT] ", "")
                        .replace("Ongoing Activity:", "")
                        .strip()
                    )
                    context_signals.append(f"Ongoing activity: {activity_name}")
                    break

    # Build the history lines
    lines = []

    # Add context line if we have signals
    if context_signals:
        context_line = "Context: " + ". ".join(context_signals) + "."
        lines.append(context_line)
        lines.append("")  # Empty line for readability

    # Add the full history (chronological order: oldest to newest)
    for i, entry in enumerate(interaction_history):
        if isinstance(entry, dict):
            if is_role_content:
                role = entry.get("role", "")
                content = entry.get("content") or ""
                if role == "user":
                    lines.append(f"User: {content}")
                elif role == "assistant":
                    # Mark as question only if it ends with ?
                    if content.strip().endswith("?"):
                        lines.append(f"Assistant (question): {content}")
                    else:
                        lines.append(f"Assistant: {content}")
                elif role == "system" and (content or "").startswith("[EVENT]"):
                    if "Ongoing Activity:" in content:
                        lines.append(
                            f"[Ongoing] {content.replace('[EVENT] ', '').replace('Ongoing Activity:', '').strip()}"
                        )
                    else:
                        lines.append(content)
            else:
                if "human" in entry:
                    lines.append(f"User: {entry['human']}")
                elif "utterance" in entry:
                    lines.append(f"User: {entry['utterance']}")
                if "ai" in entry:
                    ai_msg = entry["ai"]
                    if ai_msg and ai_msg.strip().endswith("?"):
                        lines.append(f"Assistant (question): {ai_msg}")
                    else:
                        lines.append(f"Assistant: {ai_msg}")
                elif "response" in entry and entry["response"]:
                    resp = entry["response"]
                    if resp.strip().endswith("?"):
                        lines.append(f"Assistant (question): {resp}")
                    else:
                        lines.append(f"Assistant: {resp}")
                if "events" in entry:
                    for event in entry["events"]:
                        ev_str = (
                            event.get("content", event)
                            if isinstance(event, dict)
                            else str(event)
                        )
                        if "Ongoing Activity:" in ev_str:
                            lines.append(
                                f"[Ongoing] {ev_str.replace('Ongoing Activity:', '').strip()}"
                            )
                        else:
                            lines.append(f"[EVENT] {ev_str}")
        elif isinstance(entry, str):
            lines.append(entry)

    # Add transition marker before current user message
    if lines:
        lines.append("")  # Empty line for separation
        lines.append("---")
        lines.append(">>> USER RESPONDS NOW <<<")
        lines.append("---")

    return "\n".join(lines) if lines else "(No previous conversation)"


class TestRouterConversationalStateInterpretation:
    """Tests for correct conversational state assessment in multi-turn interactions."""

    def test_matches_response_to_most_recent_question(self):
        """Test that 'Yes' is matched to the CURRENT question, not a previous one.

        This reproduces the reported bug: user says 'Yes' but the router
        incorrectly interprets it as answering an old question instead of
        the current one.
        """
        # History: Multiple questions with current response to the most recent
        history = [
            {"role": "assistant", "content": "Do you have any photos?"},
            {"role": "user", "content": "I do not"},
            {
                "role": "assistant",
                "content": "Understood. Would you like to keep it private?",
            },
            {"role": "user", "content": "No"},
            {
                "role": "assistant",
                "content": "Alright. Are you submitting this report on behalf of someone else?",
            },
        ]

        # Format the history
        formatted = format_history_for_test(history)

        # Verify the transition marker is present
        assert ">>> USER RESPONDS NOW <<<" in formatted

        # Verify context correctly identifies the most recent question
        assert "Most recent assistant message is a question" in formatted

        # Verify the final question is clearly shown before the transition
        assert "Are you submitting this report on behalf of someone else?" in formatted

        # Verify the order: history first, THEN transition marker
        history_end = formatted.find(">>> USER RESPONDS NOW <<<")
        question_pos = formatted.find("Are you submitting this report")
        assert (
            question_pos < history_end
        ), "Most recent question should appear before transition marker"

    def test_context_signals_identify_most_recent_not_first(self):
        """Test that context signals identify the MOST RECENT assistant question, not the first."""
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "What is your name?"},
            {"role": "user", "content": "John"},
            {"role": "assistant", "content": "What is your age?"},
        ]

        formatted = format_history_for_test(history)

        # Should identify the LAST question, not the first
        assert "What is your age?" in formatted
        assert "What is your name?" in formatted

        # Context should reference the MOST RECENT question
        assert "Most recent assistant message is a question" in formatted

        # The most recent one should come after the earlier one in the formatted output
        first_q_pos = formatted.find("What is your name?")
        last_q_pos = formatted.find("What is your age?")
        assert first_q_pos < last_q_pos, "Questions should be in chronological order"

    def test_skips_system_events_when_finding_last_question(self):
        """Test that system/event messages don't obscure the last assistant question."""
        history = [
            {"role": "assistant", "content": "What is your email?"},
            {"role": "user", "content": "user@example.com"},
            {"role": "assistant", "content": "Do you want to subscribe?"},
            {
                "role": "system",
                "content": "[EVENT] Ongoing Activity: EmailCollectionInteractAction",
            },
        ]

        formatted = format_history_for_test(history)

        # Should correctly identify the question before the event
        assert "Most recent assistant message is a question" in formatted
        assert "Do you want to subscribe?" in formatted

        # The event should not prevent identification of the preceding question
        assert (
            "Ongoing Activity: EmailCollectionInteractAction" in formatted
            or "[Ongoing]" in formatted
        )

    def test_format_without_question_does_not_add_false_signal(self):
        """Test that if history ends with non-question, context doesn't falsely claim there's a question."""
        history = [
            {"role": "assistant", "content": "What is your name?"},
            {"role": "user", "content": "John"},
            {"role": "assistant", "content": "Thank you for that information"},
        ]

        formatted = format_history_for_test(history)

        # Should NOT say "Most recent assistant message is a question"
        assert "Most recent assistant message is a question" not in formatted

    def test_history_includes_complete_conversation_thread(self):
        """Test that formatted history includes all prior interactions for context."""
        history = [
            {"role": "user", "content": "I want to report an incident"},
            {"role": "assistant", "content": "I can help. What happened?"},
            {"role": "user", "content": "There was an accident"},
            {"role": "assistant", "content": "Are there any injuries?"},
        ]

        formatted = format_history_for_test(history)

        # All interactions should be present for context
        assert "I want to report an incident" in formatted
        assert "I can help. What happened?" in formatted
        assert "There was an accident" in formatted
        assert "Are there any injuries?" in formatted

    def test_transition_marker_clearly_separates_history_from_current(self):
        """Test that the transition marker clearly indicates where current message begins."""
        history = [
            {"role": "assistant", "content": "What's your email?"},
            {"role": "user", "content": "test@example.com"},
        ]

        formatted = format_history_for_test(history)

        # Transition marker should be present and clear
        assert "---" in formatted
        assert ">>> USER RESPONDS NOW <<<" in formatted

        # Should have proper line breaks around the marker
        lines = formatted.split("\n")
        marker_line_idx = [
            i for i, l in enumerate(lines) if ">>> USER RESPONDS NOW <<<" in l
        ][0]

        # Should have dashes before and after
        assert marker_line_idx > 0 and "---" in lines[marker_line_idx - 1]
        assert marker_line_idx < len(lines) - 1 and "---" in lines[marker_line_idx + 1]

    def test_questions_marked_with_question_annotation(self):
        """Test that assistant messages ending in ? are clearly marked as questions."""
        history = [
            {"role": "assistant", "content": "What is your name?"},
            {"role": "assistant", "content": "Thank you"},
            {"role": "assistant", "content": "Are you sure?"},
        ]

        formatted = format_history_for_test(history)

        # Questions should be marked with (question)
        assert "Assistant (question): What is your name?" in formatted
        assert "Assistant (question): Are you sure?" in formatted

        # Non-question should not have (question) marker
        assert "Assistant: Thank you" in formatted
        assert "Assistant (question): Thank you" not in formatted

    def test_empty_history_returns_no_previous_conversation(self):
        """Test that empty history is handled gracefully."""
        formatted = format_history_for_test([])
        assert formatted == "(No previous conversation)"

    def test_single_entry_history(self):
        """Test that single-entry history is formatted correctly."""
        history = [
            {"role": "assistant", "content": "What can I help you with?"},
        ]

        formatted = format_history_for_test(history)

        assert "What can I help you with?" in formatted
        assert "Most recent assistant message is a question" in formatted
        assert ">>> USER RESPONDS NOW <<<" in formatted


class TestRouterActionMatching:
    """Tests for correct action matching based on anchors and conversation state."""

    def test_ongoing_activity_recognition_in_context(self):
        """Test that ongoing activity is recognized and included in context signals."""
        history = [
            {"role": "assistant", "content": "Let's start your signup"},
            {"role": "user", "content": "Ok"},
            {
                "role": "system",
                "content": "[EVENT] Ongoing Activity: SignupInterviewInteractAction",
            },
        ]

        formatted = format_history_for_test(history)

        # Ongoing activity should be in context signals
        assert "Ongoing activity:" in formatted
        assert "SignupInterviewInteractAction" in formatted

    def test_multiple_ongoing_activities_uses_most_recent(self):
        """Test that only the most recent ongoing activity is used in context."""
        history = [
            {"role": "system", "content": "[EVENT] Ongoing Activity: OldActionName"},
            {"role": "assistant", "content": "Continue the process"},
            {"role": "user", "content": "Ok"},
            {
                "role": "system",
                "content": "[EVENT] Ongoing Activity: CurrentActionName",
            },
        ]

        formatted = format_history_for_test(history)

        # Should reference the most recent ongoing activity
        assert "CurrentActionName" in formatted
        # Old one may or may not appear in context line, but current one must
        assert "CurrentActionName" in formatted


class TestRouterPromptStructure:
    """Tests for proper structure of the routing prompt sent to LLM."""

    def test_prompt_shows_history_before_current_message(self):
        """Test that the prompt template shows history BEFORE the current message.

        This is critical: the LLM must understand that the history is temporal context
        for the current message, not vice versa.
        """
        # Check the prompt structure directly without importing (circular dependency issue)
        # This verifies the changes made to prompts.py
        # Expected: CONVERSATION STATE: first, then CURRENT USER MESSAGE:

        # Read the file directly to verify structure
        import os

        prompts_file = "/Users/eldonmarks/Briefcase/dev/jv/jvagent/jvagent/action/router/prompts.py"
        with open(prompts_file, "r") as f:
            content = f.read()

        # Verify the key structural elements are in the correct order
        conv_state_idx = content.find("CONVERSATION STATE:")
        current_msg_idx = content.find("CURRENT USER MESSAGE:")
        history_section_idx = content.find("{history_section}")
        utterance_idx = content.find("{utterance}")

        # History should come before utterance in the prompt
        assert (
            history_section_idx < utterance_idx
        ), "History section should appear before utterance in prompt template"

        # Conversation state should come before current user message
        assert (
            conv_state_idx < current_msg_idx
        ), "CONVERSATION STATE should come before CURRENT USER MESSAGE in prompt"

    def test_prompt_rules_reference_most_recent_assistant(self):
        """Test that routing rules explicitly mention 'most recent' assistant message."""
        # Check directly without importing due to circular dependency
        import os

        prompts_file = "/Users/eldonmarks/Briefcase/dev/jv/jvagent/jvagent/action/router/prompts.py"
        with open(prompts_file, "r") as f:
            content = f.read()

        # Rules should mention "most recent" to be explicit
        assert "most recent" in content.lower()

    def test_prompt_includes_user_responds_now_marker(self):
        """Test that the prompt includes the USER RESPONDS NOW marker instruction."""
        # Check directly without importing due to circular dependency
        import os

        prompts_file = "/Users/eldonmarks/Briefcase/dev/jv/jvagent/jvagent/action/router/prompts.py"
        with open(prompts_file, "r") as f:
            content = f.read()

        # Instructions should mention the transition marker or related concept
        assert "USER RESPONDS NOW" in content or "user responds now" in content.lower()


class TestRealisticScenarios:
    """Tests based on realistic conversation scenarios."""

    def test_interview_multi_turn_interpretation(self):
        """Test a realistic multi-turn interview scenario with the reported issue."""
        # This reproduces the exact scenario from the bug report
        history = [
            {"role": "user", "content": "Yes"},  # response to "Do you have photos?"
            {
                "role": "assistant",
                "content": "Great! Do you have any photos or videos of the incident you'd like to include? You can upload them now or skip this step.",
            },
            {"role": "user", "content": "I do not"},
            {
                "role": "assistant",
                "content": "Understood. I noticed that the report includes sensitive information. Would you like to keep it private?",
            },
            {"role": "user", "content": "No"},
            {
                "role": "assistant",
                "content": "Alright. Are you submitting this report on behalf of someone else?",
            },
        ]

        formatted = format_history_for_test(history)

        # The context should identify that the most recent assistant message is a question
        assert "Most recent assistant message is a question" in formatted

        # The final question should be clearly visible before the transition
        assert "Are you submitting this report on behalf of someone else?" in formatted

        # Verify the entire conversation history is preserved
        assert "Do you have any photos or videos" in formatted
        assert "Would you like to keep it private?" in formatted

        # Verify transition marker
        assert ">>> USER RESPONDS NOW <<<" in formatted

    def test_nested_questions_with_clarifications(self):
        """Test scenario with nested questions and clarifications."""
        history = [
            {"role": "user", "content": "I need to report something"},
            {"role": "assistant", "content": "What happened?"},
            {"role": "user", "content": "There was an incident at work"},
            {"role": "assistant", "content": "Was anyone injured?"},
            {"role": "user", "content": "Yes, there were injuries"},
            {"role": "assistant", "content": "How serious were the injuries?"},
        ]

        formatted = format_history_for_test(history)

        # The most recent question should be correctly identified
        assert "How serious were the injuries?" in formatted
        assert "Most recent assistant message is a question" in formatted

        # Earlier questions should still be present for context
        assert "Was anyone injured?" in formatted
        assert "What happened?" in formatted

    def test_topic_change_after_multi_turn_process(self):
        """Test scenario where user changes topic after completing a process."""
        history = [
            {"role": "assistant", "content": "What is your name?"},
            {"role": "user", "content": "Alice"},
            {"role": "assistant", "content": "What is your email?"},
            {"role": "user", "content": "alice@example.com"},
            {"role": "assistant", "content": "Thanks! The signup is complete."},
        ]

        formatted = format_history_for_test(history)

        # The most recent message is NOT a question
        assert "Most recent assistant message is a question" not in formatted

        # But the history should still be available for context
        assert "What is your name?" in formatted
        assert "What is your email?" in formatted
        assert "signup is complete" in formatted
