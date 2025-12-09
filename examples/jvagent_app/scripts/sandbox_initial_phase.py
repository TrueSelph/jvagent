#!/usr/bin/env python3
"""Initial Phase Sandbox - Interactive CLI for testing Initial Phase Action.

Usage:
    # Single utterance test
    python sandbox_initial_phase.py "Hello there"

    # Interactive mode
    python sandbox_initial_phase.py

    # With custom agent
    python sandbox_initial_phase.py --agent my_namespace/my_agent

    # JSON output
    python sandbox_initial_phase.py "test" --json
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
import ast
from datetime import datetime
from typing import Any, Dict, List, Optional

from jvagent.utils.model_utils import model_call_to_json
from jvagent.action.model.openai import OpenAIModelAction
from jvspatial.core.annotations import attribute

# Import mock actions for testing
from mock_actions import (
    MockGetIceCreamLocationAction,
    MockGetUserLocationAction,
    MockFilterIceCreamPlacesAction,
    MockExtractSubscriptionPreferencesAction,
    MockCreateReportAction,
    VectorSearchAction,
    ExamplePersonaAction,
    MockPersonaAction
)
from jvagent.action.initial_phase.models import (
    Competency,
    ExecutionRequirement,
    InitialPhaseInstructions,
    Parameter,
    Workflow,
)
from jvagent.action.initial_phase.prompts import (
    INITIAL_PHASE_EVALUATION_PROMPT,
    COMPETENCY_FILTER_PROMPT,
    INTERPRETATION_PROMPT,
    format_competencies_for_prompt,
    format_history_for_prompt,
    format_parameters_for_prompt,
)

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))


class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'


class InitialPhaseSandbox:
    """Interactive sandbox for testing Initial Phase Action."""

    def __init__(self, agent_ref: str = "jvagent/example_agent", competencies: List[Competency] = []):
        self.agent_ref = agent_ref
        self.competencies = competencies
        self.agent = None
        self.session_id = "custom_session_id"
        self.user_id = "custom_user_id"

        # Conversation history tracking
        self.conversation_history = []
        self.max_history_length = 10  # Keep last 10 interactions

        # ProcessingPhase instance - persists across utterances to maintain action state
        self.processing_phase = None

    async def initialize(self):
        """Initialize the sandbox by loading agent and action."""
        from jvagent.core.agent import Agent
        from jvagent.action.initial_phase import InitialPhaseAction

        self.action = InitialPhaseAction()

        if not self.action or not isinstance(self.action, InitialPhaseAction):
            print(f"{Colors.RED}❌ Error: InitialPhaseAction not found on agent{Colors.END}")
            return False

        print(f"{Colors.GREEN}✅ Found InitialPhaseAction{Colors.END}")

        # Load some default parameters if none exist
        params = self.get_default_parameters()
        self.agent_description = "An example agent for testing Initial Phase Action."
        self.agent_name = "Example Agent"
        self.agent_role = "answer the users questions"
        self.agent_capabilities = []
        if not params:
            print(f"{Colors.YELLOW}⚠️  No parameters found, adding defaults...{Colors.END}")
        else:
            print(f"{Colors.CYAN}Found {len(params)} existing parameters{Colors.END}")

        return True

    def get_default_parameters(self):
        """Add default parameters for testing."""
        defaults = [
            {
                "condition": "User sends a greeting like hello, hi, or hey",
                "response": "Respond warmly and introduce yourself",
                "execution_requirement": "on_first_interaction",
            },
            {
                "condition": "User asks for help or information",
                "response": "Provide helpful information about capabilities",
                "execution_requirement": "conditional",
            },
            {
                "condition": "User wants to subscribe or sign up",
                "response": "Initiate subscription workflow",
                "action": "subscription_action",
                "workflow": "subscription_workflow",
                "execution_requirement": "conditional",
            },
        ]

        return defaults

    async def get_applicable_parameters(self, utterance: str) -> list:
        """Get applicable parameters for a given utterance using vector search.

        This method generates an embedding for the utterance and uses vector search
        to find semantically similar parameters from Typesense.

        Args:
            utterance: User utterance to match against parameters

        Returns:
            List of applicable parameter dictionaries with their details
        """
        model_action = OpenAIModelAction(
            api_key=os.getenv("OPENAI_API_KEY"),
            api_endpoint="https://api.openai.com/v1",
            model="gpt-4o-mini",
            temperature=0.7,
            max_tokens=1000
        )
        results = await model_action._query([{"role": "user", "content": classifier_prompt.replace("{utterance}", utterance).replace("{context}", "")}])
        if not results:
            print(f"{Colors.RED}Error: Could not generate embedding{Colors.END}")
            return []

        try:
            # Generate embedding for the utterance
            embedding = await self.action._generate_embedding(utterance)

            if not embedding:
                print(f"{Colors.YELLOW}Warning: Could not generate embedding{Colors.END}")
                return []

            # Use the action's vector search to find applicable parameters
            from jvagent.action.initial_phase.events import InitialPhaseEventBus
            event_bus = InitialPhaseEventBus("parameter_check")

            filtered_params, _ = await self.action._vector_search(
                embedding, event_bus
            )

            return filtered_params

        except Exception as e:
            print(f"{Colors.RED}Error getting applicable parameters: {e}{Colors.END}")
            import traceback
            traceback.print_exc()
            return []

    async def model_call(self, prompt: str, model: str = "gpt-4o") -> dict:
        """Process a single utterance and return results."""
        model_action = OpenAIModelAction(
            api_key=os.getenv("OPENAI_API_KEY"),
            api_endpoint="https://api.openai.com/v1",
            model=model,
            temperature=0.7,
            max_tokens=1000
        )
        results = await model_action._query([{"role": "user", "content": prompt}])
        return results.response

    async def classify_utterance(self, utterance: str, show_json: bool = False) -> dict:
        """Process a single utterance and return results."""

        classifier_prompt = """
            Classify the user's request along with the conversation history in context into ONE category:
            1. SIMPLE: Greetings, farewells, acknowledgments, or questions with obvious answers
            Examples: "hello", "goodbye", "thanks", "what time is it?", "who are you?"
            2. RETRIEVAL: Requests requiring knowledge lookup but no multi-step complex processing
            Examples: "what's my balance?", "show me my profile", "what is the company address?"
            3. COMPLEX: Multi-step processes, workflow initiation, or competency activation
            Examples: "I want to subscribe", "help me set up my account", "book a flight to Paris",
            Note: check recent context to determine if a retrieval or complex request is needed based on the conversation history and triggered events
            If a workflow has been started but not completed then it is a COMPLEX request
            User Utterance: {utterance}
            Recent Context: {context}
            Return ONLY: SIMPLE, RETRIEVAL, or COMPLEX
        """
        classify_start = time.time()

        results = await self.model_call(prompt= classifier_prompt.replace("{utterance}", utterance).replace("{context}", str(self.get_context())))
        classify_duration = time.time() - classify_start

        print(f"{Colors.CYAN}⏱️  Classification: {classify_duration:.3f}s{Colors.END} - Result: {results}")

        if show_json:
            # JSON output mode
            return results
        return results

    async def interpreter(self, utterance: str, show_json: bool = False) -> dict:
        """Process a single utterance and return results."""

        simplify_prompt2 = """
        Your task is to create a clear and concise interpretation of the user's request.
        This interpretation should enscapulate the entire context as well as the user_utterance. Note if the user is responding to question or any ongoing events
        The interpretation should mention if the user is asking for information, providing information or doing both.
        Using the interpretation to match the given competencies' anchors. Generate a list of competencies that match the user intent.
        Return ONLY: a json with the interpretation and the labels of the competencies under the keys "interpretation" and "competencies".

        Example:
        {{
            "interpretation": "User is asking for information about the company address.",
            "competencies": ["competency1"]
        }}
        {{
            "interpretation": "User is providing confirmation and asking for information about the company address.",
            "competencies": ["competency1", "competency2"]
        }}


        user_utterance: {utterance}
        context: {context}
        competencies: {competencies}
        """
        simplify_prompt = INTERPRETATION_PROMPT.format(
            utterance=utterance,
            context=str(self.get_context()),
            anchors=str(self.get_anchors())
        )
        simplify_start = time.time()
        context = self.get_context()
        competencies = self.get_anchors()

        # results = await self.model_call(prompt= simplify_prompt.replace("{utterance}", utterance).replace("{context}", str(context)).replace("{competencies}", str(competencies)))
        results = await self.model_call(prompt= simplify_prompt)
        results = self.convert_str_to_json(results)
        simplify_duration = time.time() - simplify_start

        print(f"{Colors.CYAN}⏱️  Simplification: {simplify_duration:.3f}s{Colors.END} - Result: {results}")

        if show_json:
            # JSON output mode
            return results
        return results

    async def get_relevant_competencies(self, utterance: str, show_json: bool = False) -> dict:
        """Process an utterance and return results."""

        classify_start = time.time()
        for competency in self.get_competencies():
            competency_short = {
                "id": competency["id"],
                "label": competency["label"],
                "description": competency["description"],
            }
            print(f"{Colors.CYAN}Competency: {competency_short}{Colors.END}")

        prompt = COMPETENCY_FILTER_PROMPT.format(
            utterance=utterance,
            context=str(self.get_context()),
            competencies=str(competency_short)
        )
        results = self.convert_str_to_json(await self.model_call(prompt=prompt))
        classify_duration = time.time() - classify_start

        print(f"{Colors.CYAN}⏱️  Competencies filter: {classify_duration:.3f}s{Colors.END} - Result: {results}")

        if show_json:
            # JSON output mode
            return results
        return results

    async def _generate_instructions(
        self,
        utterance: str,
        # user: str = "User",
        # conversation: Conversation,
        context: List[Dict],
        filtered_parameters: List[Dict] = [],
        filtered_competencies: List[Dict]= [],
        is_first_interaction: bool = False,
        # event_bus: InitialPhaseEventBus,
    ) -> InitialPhaseInstructions:
        """Generate structured instructions using LLM evaluation.

        Args:
            utterance: User utterance
            user: User node
            conversation: Conversation node
            context: Conversation history
            filtered_parameters: Parameters from vector search
            filtered_competencies: Competencies from vector search
            is_first_interaction: Whether this is the first interaction
            event_bus: Event bus
            interaction: Interaction node

        Returns:
            InitialPhaseInstructions object
        """
        # Get current date/time
        now = datetime.now()
        date_str = now.strftime("%A, %d %B, %Y")
        time_str = now.strftime("%I:%M %p")

        # Get agent actions and workflows
        # agent = await self._get_agent()
        # available_actions = await self._get_available_actions(agent)
        available_actions = []
        # available_workflows = list(self._workflows.values())
        available_workflows = []

        # Format prompt components
        history_text = " "
        params_text = format_parameters_for_prompt(filtered_parameters)
        comps_text = format_competencies_for_prompt(filtered_competencies)

        # Build prompt
        prompt = INITIAL_PHASE_EVALUATION_PROMPT.format(
            agent_role=self.agent_role,
            agent_description=self.agent_description,
            agent_capabilities="\n- ".join(self.agent_capabilities),
            date=date_str,
            time=time_str,
            user_id="me", # user.user_id,
            is_first_interaction=is_first_interaction,
            utterance=utterance,
            context=self.get_context(),
            filtered_parameters=params_text,
            competencies=comps_text,
            available_actions=self.get_actions(),
            available_workflows=available_workflows,
        )

        # Call model with timing
        print(f"{Colors.YELLOW}🔄 Generating instructions...{Colors.END}")
        instruction_start = time.time()

        model_action = OpenAIModelAction(
            api_key=os.getenv("OPENAI_API_KEY"),
            api_endpoint="https://api.openai.com/v1",
            model="gpt-4o",
            temperature=0.7,
            max_tokens=1000
        )
        result = await model_action._query([{"role": "user", "content": prompt}])

        instruction_duration = time.time() - instruction_start
        print(f"{Colors.CYAN}⏱️  Instruction Generation: {instruction_duration:.3f}s{Colors.END}")

        if not result.response:
            # Fallback: return basic instructions
            await event_bus.emit_log("warning", "LLM returned no result, using fallback")
            instructions = InitialPhaseInstructions(
                simplified_intent="user message",
                applicable_parameters=[],
                required_workflows=[],
                required_actions=[],
                context={},
                metadata={"fallback": True},
            )

        # Parse JSON response
        try:
            instructions_dict = self.convert_str_to_json(result.response)
            instructions = InitialPhaseInstructions.from_dict(instructions_dict)
        except Exception as e:
            # await event_bus.emit_error(f"Failed to parse instructions JSON: {e}")
            # logger.error(f"Failed to parse instructions: {result[:200]}")
            # Return fallback
            instructions = InitialPhaseInstructions(
                simplified_intent="user message",
                applicable_parameters=[],
                required_workflows=[],
                required_actions=[],
                context={},
                metadata={"error": str(e), "fallback": True},
            )
        # print("instructions ",instructions)
        return instructions

    async def interactive_mode(self):
        """Run in interactive REPL mode."""
        print(f"\n{Colors.BOLD}{Colors.GREEN}{'='*70}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.GREEN}Initial Phase Sandbox - Interactive Mode{Colors.END}")
        print(f"{Colors.BOLD}{Colors.GREEN}{'='*70}{Colors.END}\n")

        print(f"{Colors.CYAN}Commands:{Colors.END}")
        print(f"  • Type your utterance to test")
        print(f"  • {Colors.YELLOW}/params{Colors.END} - List parameters")
        print(f"  • {Colors.YELLOW}/add{Colors.END} - Add a parameter")
        print(f"  • {Colors.YELLOW}/history{Colors.END} - View conversation history")
        print(f"  • {Colors.YELLOW}/reset{Colors.END} - Reset session")
        print(f"  • {Colors.YELLOW}/json{Colors.END} - Toggle JSON output")
        print(f"  • {Colors.YELLOW}/help{Colors.END} - Show commands")
        print(f"  • {Colors.YELLOW}/quit{Colors.END} or Ctrl+C - Exit")
        print()

        json_mode = False

        try:
            while True:
                try:
                    # Get input
                    utterance = input(f"{Colors.BOLD}{Colors.BLUE}> {Colors.END}").strip()

                    if not utterance:
                        continue

                    # Handle commands
                    if utterance.startswith('/'):
                        command = utterance[1:].lower()

                        if command in ('quit', 'exit', 'q'):
                            print(f"\n{Colors.CYAN}Goodbye!{Colors.END}\n")
                            break

                        elif command == 'params':
                            await self._show_parameters()

                        elif command == 'add':
                            await self._add_parameter_interactive()

                        elif command == 'reset':
                            self.session_id = None
                            self.user_id = None
                            print(f"{Colors.GREEN}✅ Session reset{Colors.END}\n")

                        elif command == 'history':
                            history = self.get_conversation_history()
                            if history:
                                print(f"{Colors.CYAN}Conversation history (total: {len(history)}):{Colors.END}")
                                for message in history:
                                    print(f"{Colors.CYAN}• {message}{Colors.END}")
                                    # print(f"{Colors.CYAN}• {message['role']}: {message['content']}{Colors.END}")
                            else:
                                print(f"{Colors.YELLOW}No conversation history available{Colors.END}\n")

                        elif command == 'json':
                            json_mode = not json_mode
                            print(f"{Colors.GREEN}✅ JSON mode: {'ON' if json_mode else 'OFF'}{Colors.END}\n")

                        elif command == 'help':
                            self._show_help()

                        else:
                            print(f"{Colors.RED}Unknown command: {command}{Colors.END}\n")

                        continue

                    # Process utterance
                    process_start = time.time()
                    # result = await self.classify_utterance(utterance, show_json=json_mode)
                    result = await self.interpreter(utterance)
                    if result:
                        # Create ProcessingPhase only on first use, then reuse it
                        if self.processing_phase is None:
                            self.processing_phase = ProcessingPhase(
                                result,
                                utterance=utterance,
                                competencies=self.competencies,
                                context=self.get_context(),
                                sandbox=self,  # Pass sandbox instance for conversation history
                                agent_namespace="jvagent",
                                agent_name="example_agent"
                            )
                        else:
                            # Reuse existing ProcessingPhase but update the utterance and intent
                            self.processing_phase.user_intent = result
                            self.processing_phase.utterance = utterance
                            self.processing_phase.context = self.get_context()

                        responses = await self.processing_phase.initialize()  # Initialize/execute

                    if json:
                        do_nothing = "yes"
                        # print(json.dumps(result, indent=2, default=str))


                    total_duration = time.time() - process_start

                    # Note: Conversation history is now added per-action in ProcessingPhase.initialize()
                    # so we don't need to add it here again

                    print(f"\n{Colors.BOLD}{Colors.CYAN}⏱️  Total Processing Time: {total_duration:.3f}s{Colors.END}")

                    if json_mode:
                        # print(json.dumps(result, indent=2, default=str))
                        print()

                except KeyboardInterrupt:
                    print(f"\n\n{Colors.CYAN}Use /quit to exit{Colors.END}\n")
                    continue

        except KeyboardInterrupt:
            print(f"\n\n{Colors.CYAN}Goodbye!{Colors.END}\n")

    async def _show_parameters(self):
        """Show all parameters."""
        params = await self.action.get_parameters()

        print(f"\n{Colors.BOLD}{Colors.GREEN}📋 PARAMETERS ({len(params)}){Colors.END}\n")

        if not params:
            print(f"{Colors.YELLOW}No parameters configured{Colors.END}\n")
            return

        for i, param in enumerate(params, 1):
            print(f"{i}. {Colors.CYAN}{param.condition}{Colors.END}")
            print(f"   Response: {param.response}")
            print(f"   Execution: {param.execution_requirement.value}")
            if param.action:
                print(f"   Action: {param.action}")
            if param.workflow:
                print(f"   Workflow: {param.workflow}")
            print(f"   ID: {Colors.YELLOW}{param.id}{Colors.END}")
            print()

    def get_set_context(self):
        """Get the context."""
        context = {
            "user": "There is a bus on fire at 12th street and main street",
            "event": "User is in process of making a report",
            "ai": "When did you notice the incident?",
            "user": "5 mins ago",
            "event": "User is in process of making a report",
            "ai": "What is your name?",
            "user": "What's your policy on data privacy?",
            "ai": "We do not collect any personal data unless it is necessary for the service and we do not share data with third parties.",
            # "ai": "Ok please confirm the details for your report \n**Incident:** Bus Fire \n**Incident Location:** 12th street and main street \n**Incident Time:** 5 mins ago",
            # "user": "yes that is correct",
            # "event": "User has confirmed the details for their report",
            # "ai": "Aright I have submitted your report. Your reference number is R61.",
            # "event": "User has submitted their report",
        }
        return context

    def get_anchors(self):
        """Get the anchors."""
        anchors = []
        for competency in self.competencies:
            anchors.append({"label": competency.label, "anchors": competency.anchors})
        return anchors

    def add_to_conversation_history(self, utterance: str, response: str = None, events: dict = None):
        """Add an interaction to conversation history.

        Args:
            utterance: User's utterance
            response: AI's response (optional, can be set later)
            events: Log of internal events (optional)
        """
        interaction = {
            "timestamp": datetime.now().isoformat(),
            "user": utterance,
            "ai": response,
            "events": events,
        }

        self.conversation_history.append(interaction)

        # Trim history to max length
        if len(self.conversation_history) > self.max_history_length:
            self.conversation_history = self.conversation_history[-self.max_history_length:]

        print(f"{Colors.CYAN}📝 Added to conversation history (total: {len(self.conversation_history)}){Colors.END}")

    def get_conversation_history(self, limit: Optional[int] = None) -> List[Dict]:
        """Get conversation history.

        Args:
            limit: Number of recent interactions to return (None = all)

        Returns:
            List of interaction dictionaries
        """
        if limit:
            return self.conversation_history[-limit:]
        return self.conversation_history

    def get_context(self):
        """Get the context from conversation history.

        Returns formatted context including recent interactions and any active workflows.
        """
        if not self.conversation_history:
            return {}

        # Get last 4 interactions for context
        recent = self.conversation_history[-3:] if len(self.conversation_history) >= 3 else self.conversation_history

        # Build list of recent interactions
        recent_interactions = []
        for item in recent:
            if item["ai"]:  # Only include if there's an AI response
                recent_interactions.append({"role": "user", "content": item["user"]})
                recent_interactions.append({"role": "assistant", "content": item["ai"], "events": item.get("events", [])})

        context = {}
        if recent_interactions:
            context = {
                "recent_interactions": recent_interactions,
                "total_interactions": len(self.conversation_history),
            }

        # Check if there's an active workflow from last instruction
        if recent and recent[-1].get("instructions"):
            last_instructions = recent[-1]["instructions"]
            if last_instructions.get("required_workflows"):
                context["active_workflows"] = last_instructions["required_workflows"]
                context["event"] = f"User started workflow: {', '.join(last_instructions['required_workflows'])}"

        return context

    def clear_conversation_history(self):
        """Clear all conversation history."""
        self.conversation_history = []
        print(f"{Colors.GREEN}✅ Conversation history cleared{Colors.END}")

    def format_history_for_display(self) -> str:
        """Format conversation history for display.

        Returns:
            Formatted string representation of conversation history
        """
        if not self.conversation_history:
            return f"{Colors.YELLOW}No conversation history{Colors.END}"

        output = [f"\n{Colors.BOLD}{Colors.GREEN}📜 CONVERSATION HISTORY ({len(self.conversation_history)} interactions){Colors.END}\n"]

        for i, item in enumerate(self.conversation_history, 1):
            timestamp = datetime.fromisoformat(item["timestamp"]).strftime("%H:%M:%S")
            output.append(f"{Colors.CYAN}[{timestamp}] #{i}{Colors.END}")
            output.append(f"  👤 User: {item['user']}")
            if item["ai"]:
                output.append(f"  🤖 AI: {item['ai']}")
            if item.get("instructions"):
                intent = item["instructions"].get("simplified_intent", "unknown")
                output.append(f"  📋 Intent: {intent}")
            output.append("")

        return "\n".join(output)

    async def generate_and_display_ai_response(self,response: str, utterance: str, events: dict = {}) -> str:
        """Generate AI response from instructions and display it.

        This simulates what the AI would say based on the generated instructions.
        The response is displayed and can be tracked in conversation history.

        Args:
            response: The generated response
            events: The events that occurred
            utterance: The user's original utterance

        Returns:
            The AI's response text
        """
        # Use the example_message from instructions as the base response
        response = response

        # Build a more detailed response based on instructions
        if response:
            response_parts = [response]
        else:
            return

        # if events:
        #     response_parts.append(f"event: {events}")

        ai_response = " ".join(response_parts)

        # Display the AI response
        print(f"\n{Colors.BOLD}{Colors.GREEN}🤖 AI RESPONSE:{Colors.END}")
        print(f"{Colors.CYAN}{ai_response}{Colors.END}\n")


    def convert_str_to_json(self, text: str) -> dict | None:
        """Convert a string to a JSON object."""
        if isinstance(text, str):
            text = text.replace("```json", "")
            text = text.replace("```", "")
        try:
            if isinstance(text, (dict, list)):
                return text
            else:
                return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            try:
                return ast.literal_eval(text)
            except (SyntaxError, ValueError) as e:
                if "'{' was never closed" in str(e):
                    text = text + "}"
                    return json.loads(text)
                else:
                    return None

    def _show_help(self):
        """Show help information."""
        print(f"\n{Colors.BOLD}{Colors.GREEN}COMMANDS{Colors.END}")
        print(f"  {Colors.YELLOW}/params{Colors.END}  - List all configured parameters")
        print(f"  {Colors.YELLOW}/add{Colors.END}     - Add a new parameter interactively")
        print(f"  {Colors.YELLOW}/reset{Colors.END}   - Reset session (start new conversation)")
        print(f"  {Colors.YELLOW}/json{Colors.END}    - Toggle JSON output mode")
        print(f"  {Colors.YELLOW}/help{Colors.END}    - Show this help")
        print(f"  {Colors.YELLOW}/quit{Colors.END}    - Exit sandbox")
        print()
        print(f"{Colors.BOLD}{Colors.GREEN}USAGE{Colors.END}")
        print(f"  Just type any text to process it through Initial Phase")
        print(f"  The action will generate embeddings, search parameters,")
        print(f"  and use LLM to generate structured instructions")
        print()

class ProcessingPhase:
    def __init__(self, user_intent: dict, utterance: str, competencies: list, context: dict, sandbox=None, agent_namespace: str = "jvagent", agent_name: str = "example_agent"):
        self.user_intent = user_intent
        self.utterance = utterance
        self.agent = None
        self.competencies = competencies
        self.context = context
        self.agent_namespace = agent_namespace
        self.agent_name = agent_name
        self.sandbox = sandbox  # Reference to sandbox for conversation history
        self.action_loader = None
        self.loaded_actions = {}

    async def initialize(self):
        """Initialize and register mock actions based on competencies."""
        # print(f"{Colors.GREEN}✅ Initializing ProcessingPhase with mock actions{Colors.END}\n")

        # Map action labels to their mock action classes
        mock_action_map = {
            "get_ice_cream_location_action": MockGetIceCreamLocationAction,
            "get_user_location_action": MockGetUserLocationAction,
            "filter_ice_cream_places_action": MockFilterIceCreamPlacesAction,
            "extract_subscription_preferences_action": MockExtractSubscriptionPreferencesAction,
            "create_report": MockCreateReportAction,
            "vector_search": VectorSearchAction,
        }


        # Collect all unique action labels from all competencies
        all_action_labels = set()
        for competency in self.competencies:
            comp_dict = {
                "id": competency.id,
                "label": competency.label,
                "states": competency.states if hasattr(competency, 'states') else [],
                "actions": competency.actions if hasattr(competency, 'actions') else []
            }
            action_labels = self.extract_action_labels(comp_dict)
            all_action_labels.update(action_labels)

        # Register mock actions for all found action labels
        # Only create new instances if they don't already exist (preserves state like self.results)
        for action_label in all_action_labels:
            if action_label in mock_action_map:
                # Only create if not already in loaded_actions (preserves state)
                if action_label not in self.loaded_actions:
                    mock_action_class = mock_action_map[action_label]
                    self.loaded_actions[action_label] = mock_action_class()
                    # print(f"{Colors.GREEN}   ✅ Registered mock action: {action_label}{Colors.END}")
            else:
                if action_label not in self.loaded_actions:
                    print(f"{Colors.YELLOW}   ⚠️  No mock action available for: {action_label}{Colors.END}")

        print()

        # Now execute the competencies
        competency_labels = self.user_intent.get("competencies", [])
        print(f"{Colors.BOLD}{Colors.GREEN}Processing Competencies{Colors.END}")
        print(f"{Colors.YELLOW}Competencies to process: {competency_labels}{Colors.END}\n")
        responses = []
        for comp_label in competency_labels:
            competency = self.get_competency_by_label(comp_label)
            if competency:
                print(f"{Colors.CYAN}🔄 Processing competency: {competency.get('label')}{Colors.END}")

                # Extract actions from competency states
                action_labels = self.extract_action_labels(competency)

                if action_labels:

                    # Run each action
                    for action_label in action_labels:
                        # Execute the action
                        result = await self.run_action_by_name(action_label, self.utterance, self.context)
                        responses.append(result)

                        # Immediately add to conversation history if sandbox is available
                        if self.sandbox and result and result[0]:
                            self.sandbox.add_to_conversation_history(
                                utterance=self.utterance,
                                events=[result[1]] if result[1] else [],
                                response=result[0]
                            )
                            # Update context for next action to see this result
                            self.context = self.sandbox.get_context()


                else:
                    print(f"{Colors.YELLOW}   No actions found in competency{Colors.END}")
                print()
        if not responses:
            response = (("Hi", "User has made a request that is not supported by the agent."))
            sandbox = InitialPhaseSandbox()
            await sandbox.generate_and_display_ai_response(response[0], response[1], self.utterance)
        return responses
        print()

    def get_competency_by_label(self, label: str) -> Optional[Dict]:
        """Get a competency by its label."""
        for comp in self.competencies:
            if comp.label == label:
                return {
                    "id": comp.id,
                    "label": comp.label,
                    "title": comp.title if hasattr(comp, 'title') else comp.label,
                    "description": comp.description,
                    "anchors": comp.anchors,
                    "states": comp.states if hasattr(comp, 'states') else [],
                    "actions": comp.actions if hasattr(comp, 'actions') else []
                }
        return None

    def extract_action_labels(self, competency: Dict) -> List[str]:
        """Extract action labels from a competency's states."""
        action_labels = []

        states = competency.get("states", [])
        for state in states:
            actions = state.get("actions", [])
            for action in actions:
                if isinstance(action, dict):
                    action_label = action.get("label")
                    if action_label:
                        action_labels.append(action_label)
                elif isinstance(action, str):
                    action_labels.append(action)
        actions = competency.get("actions", [])
        for action in actions:
            if isinstance(action, dict):
                action_label = action.get("label")
                if action_label:
                    action_labels.append(action_label)
            elif isinstance(action, str):
                action_labels.append(action)

        return action_labels

    async def run_action_by_name(self, action_label: str, utterance: str, context: Any = None) -> Optional[Any]:
        """Dynamically load and run an action by its label.

        This function:
        1. Looks up the action in the filesystem using ActionLoader
        2. Instantiates it if not already loaded
        3. Executes the action with the provided context

        Args:
            action_label: The label/name of the action to run (e.g., "submit_report_action")
            context: Context to pass to the action (e.g., user utterance, extracted entities)

        Returns:
            The result of the action execution, or None if action not found/failed
        """
        try:
            # Check if action is already loaded in cache
            if action_label in self.loaded_actions:
                action_instance = self.loaded_actions[action_label]
            else:

                if not action_instance:
                    print(f"{Colors.RED}   ❌ Failed to load action: {action_label}{Colors.END}")
                    return None

                # Cache it for future use
                self.loaded_actions[action_label] = action_instance
                print(f"{Colors.GREEN}   ✅ Loaded and cached action: {action_label}{Colors.END}")

            # Execute the action
            print(f"{Colors.CYAN}   🚀 Executing action: {action_label}{Colors.END}")

            sandbox = InitialPhaseSandbox()
            result = await self.execute_action(action_instance, utterance, context)

            await sandbox.generate_and_display_ai_response(result[0], result[1], self.utterance)

            print(f"{Colors.GREEN}   ✅ Action completed: {action_label}{Colors.END}")
            return result

        except Exception as e:
            print(f"{Colors.RED}   ❌ Error running action {action_label}: {e}{Colors.END}")
            import traceback
            traceback.print_exc()
            return None

    async def execute_action(self, action_instance: Any, utterance: str, context: Any = None) -> Any:
        """Execute an action instance with the provided context.

        Args:
            action_instance: The action object to execute
            context: Context to pass to the action

        Returns:
            The result of the action execution
        """
        # Check if the action has a standard execute method
        if hasattr(action_instance, 'execute'):
            return await action_instance.execute(utterance, context)

        # Check for __call__ method
        elif hasattr(action_instance, '__call__'):
            return await action_instance(utterance, context)

        # Check for run method
        elif hasattr(action_instance, 'run'):
            return await action_instance.run(utterance, context)

        else:
            print(f"{Colors.YELLOW}   ⚠️  Action has no standard execution method{Colors.END}")
            return None

    async def get_competencies(self, anchors):
        """Get the relevant competencies."""
        competencies = self.competencies
        relevant_competencies = []

        for competency in competencies:
            for anchor in anchors:
                if anchor in competency["anchors"]:
                    relevant_competencies.append(competency["label"])
        return relevant_competencies

    async def get_applicable_parameters(self, utterance: str) -> list:
        """Get applicable parameters for a given utterance using vector search.

        This method generates an embedding for the utterance and uses vector search
        to find semantically similar parameters from Typesense.

        Args:
            utterance: User utterance to match against parameters

        Returns:
            List of applicable parameter dictionaries with their details
        """
        model_action = OpenAIModelAction(
            api_key=os.getenv("OPENAI_API_KEY"),
            api_endpoint="https://api.openai.com/v1",
            model="gpt-4o-mini",
            temperature=0.7,
            max_tokens=1000
        )
        results = await model_action._query([{"role": "user", "content": classifier_prompt.replace("{utterance}", utterance).replace("{context}", "")}])
        if not results:
            print(f"{Colors.RED}Error: Could not generate embedding{Colors.END}")
            return []

        try:
            # Generate embedding for the utterance
            embedding = await self.action._generate_embedding(utterance)

            if not embedding:
                print(f"{Colors.YELLOW}Warning: Could not generate embedding{Colors.END}")
                return []

            # Use the action's vector search to find applicable parameters
            from jvagent.action.initial_phase.events import InitialPhaseEventBus
            event_bus = InitialPhaseEventBus("parameter_check")

            filtered_params, _ = await self.action._vector_search(
                embedding, event_bus
            )

            return filtered_params

        except Exception as e:
            print(f"{Colors.RED}Error getting applicable parameters: {e}{Colors.END}")
            import traceback
            traceback.print_exc()
            return []

    async def model_call(prompt: str, model: str = "gpt-4o-mini", context: dict = {}, utterance: str = "") -> dict:
        """Process a single utterance and return results."""
        start = time.time()
        model_action = OpenAIModelAction(
            api_key=os.getenv("OPENAI_API_KEY"),
            api_endpoint="https://api.openai.com/v1",
            model=model,
            temperature=0.3,
            max_tokens=1000
        )
        if context:
            # System message first, then context (conversation history), then current user message
            messages = [{"role": "system", "content": prompt}]
            messages.extend(context)  # Add conversation history
            messages.append({"role": "user", "content": utterance})
        else:
            messages = [
                {"role": "system", "content": prompt},  # System first
                {"role": "user", "content": utterance}
            ]
        results = await model_action._query(messages)
        total_duration = time.time() - start
        print(f"\n{Colors.BOLD}{Colors.CYAN}⏱️  Total LLM call Time: {total_duration:.3f}s{Colors.END}")
        return results.response

    async def generate_extraction_prompt(entities:dict) -> str:
        # accepts the question index schema and prepares an extraction prompt
        entities_list = []
        sample_json_lines = []
        extraction_prompt = """
            Review the user's message and the conversation history to accurately extract the following entities.
            Only extract data that has not been specifically cancelled.
            Be strict on the constraints specified for each entity. Return a JSON object with keys exactly as listed below.
            Include only keys for which you could extract a valid value adhering to all constraints.

            Entities to extract:
            {entities}

            Return ONLY the JSON object with the extracted entities, no delimiters. Do not include any other text or explanation.
            The JSON must have the following structure (only include keys with valid values):
            {sample_json}
        """

        if(constraints := entities.get('constraints', {})):

            desc = constraints.get('description', '')
            other_constraints = {k: v for (k, v) in constraints.items() if k != 'description'}
            constraint_strs = [f"{k}: {v}" for (k, v) in other_constraints.items()]
            constraint_part = f" ({', '.join(constraint_strs)})" if constraint_strs else ""
            name = entities.get("name")
            entities_list.append(f"- {name}: {desc}{constraint_part}")
            sample_json_lines.append(f"  '{name}': '<extracted value>'")


            entities = "\n".join(entities_list)
            sample_json = '{\n' + ',\n'.join(sample_json_lines) + '\n}'
            # prepate the prompt
            prompt = extraction_prompt.format(entities=entities, sample_json=sample_json)
            # escape the conflicting symbols
            prompt = prompt.replace('{', '{{').replace('}','}}')

            return prompt

        else:
            return ""

class MockCreateReportAction:

    def __init__(self, **kwargs):
        self.label = kwargs.get('label', 'mock_create_report_action')
        self.description = kwargs.get('description', 'Mock action for creating reports')
        self.agent_id = kwargs.get('agent_id', 'test_agent')
        self.results:dict = {}
        print(f"{Colors.CYAN}🔧 MockCreateReportAction initialized{Colors.END}")

    async def execute(self, utterance: str, context: Any = None) -> Dict[str, Any]:
        """Execute the mock create report action.

        Args:
            utterance: User utterance
            context: Context data (e.g., user utterance, extracted entities)

        Returns:
            Dictionary with mock report data
        """
        states:list = [
            {
                "name": "reporting_on_behalf",
                "question": "Are you creating this report on behalf of someone else?",
                "constraints": {
                    "description": "Determine if the user is reporting on behalf of another person",
                    "type": "string",
                    "items": ["yes", "no"]
                },
                "required": True
            },
            {
                "name": "report_description",
                "question": "Describe to me the incident you'd like to report in a single message.",
                "constraints": {
                    "description": "A description of an incident or complaint for a report or matter of grievance. Do not extract a request, only the full details if provided.",
                    "type": "string"
                },
                "required": True
            },
            {
                "name": "report_media",
                "question": "Please upload any images you may have related to your report. These are necessary to help us understand the incident.",
                "constraints": {
                    "description": "Any images related to the incident",
                    "additional_notes": """If the user declines to answer or avoids sending a picture after being asked for images, return an empty string in a dictionary under key 'url' as an item in an array.
                    Only return this array if the user explicitly states they don't have or want to give a picture after being asked for one or if they do send a picture.
                    Do not return anything if no picture is sent and user does not decline to send one.""",
                    "type": "array"
                },
                "required": True
            },
            {
                "name": "report_location",
                "question": "Please provide the address of the location where the incident occurred or send a WhatsApp location pin.",
                "constraints": {
                    "description": "The specific address where the incident took place, including street and area names, followed by any additional directions if needed. Ignore vague or non-descript location descriptions such as 'my area', 'my street', 'at the corner', or similar general references.",
                    "type": "string"
                },
                "required": True
            },
            {
                "name": "new_report",
                "question": "Would you like to create a new report?",
                "constraints": {
                    "description": "Whether or not the user wishes to continue creating a new report.",
                    "type": "string",
                    "options": ["yes", "no"]
                },
                "required": True
            },
            {
                "name": "stakeholder",
                "question": "Please provide the full name of the person you're reporting on behalf of.",
                "constraints": {
                    "description": "The full name of the person the report is being created for",
                    "additional_instructions": "If the user declines to answer, mark as N/A. Only mark as N/A if they explicitly decline.",
                    "type": "string"
                },
                "required": True,
                "conditional": {"reporting_on_behalf": "yes"}
            },
            {
                "name": "stakeholder_address",
                "question": "What is the address of the person you're reporting on behalf of?",
                "constraints": {
                    "description": "The address of the person the report is being created for",
                    "additional_instructions": "If the user declines to answer, mark as N/A. Only mark as N/A if they explicitly decline.",
                    "type": "string"
                },
                "required": True,
                "conditional": {"reporting_on_behalf": "yes"}
            },
            {
                "name": "stakeholder_number",
                "question": "What is the phone number of the person you're reporting on behalf of?",
                "constraints": {
                    "description": "The contact number of the person the report is being created for",
                    "additional_instructions": "If the user declines to answer, mark as N/A",
                    "type": "string"
                },
                "required": True,
                "conditional": {"reporting_on_behalf": "yes"}
            },
            {
                "name": "reporter",
                "question": "Please provide your full name.",
                "constraints": {
                    "description": "The full name of the person making the report",
                    "additional_instructions": "the full name must include the first and last name of the person making the report. Do not return anything if you only have one name",
                    "type": "string"
                },
                "required": True
            },
            {
                "name": "reporter_address",
                "question": "Please provide your residential address.",
                "constraints": {
                    "description": "The complete address of the person making the report, not the location of the incident.",
                    "type": "string"
                },
                "required": True,
                "conditional": {"reporting_on_behalf": "no"}
            }
        ]
        print(f"{Colors.GREEN}   📝 MockCreateReportAction.execute() called{Colors.END}")
        for state in states:
            if not self.results:
                self.results = {}
            if state.get("name") not in self.results:
                prompt = await ProcessingPhase.generate_extraction_prompt(state)
                extraction = await ProcessingPhase.model_call(prompt, context=context.get("recent_interactions", None), utterance=utterance)
                result = InitialPhaseSandbox.convert_str_to_json(InitialPhaseSandbox, extraction)
                print(f"{Colors.YELLOW} Extraction:  {result}{Colors.END}")
                directive = ""

                if not result or state.get("name") not in result:
                    directive_template = """
                        Tailor your response to get the information needed based on the following description:
                        {description}
                        Avoid asking for other information not related to this description unless specified elsewhere.  {question}

                        Take note of the following additional instructions while responding to the user but do not mention them unless it is needed:
                        {instructions}
                    """
                    question = state.get("question", "")
                    constraints = state.get("constraints", {})
                    description = constraints.get("description", "")
                    additional_instructions = constraints.get("additional_instructions", "")

                    directive = directive_template.replace("{description}", description)
                    directive = directive.replace("{instructions}", additional_instructions)
                    directive = directive.replace("{question}", f"E.g. {question}")

                    if(options:= constraints.get("options", "")):
                        directive = directive + "\n They can choose from the list of options below\n" + str(options)
                    break
                self.results[state.get("name")] = result[state.get("name")]
                print(f"{Colors.YELLOW} Result:  {result}{Colors.END}")
                report_id = f"R{hash(str(context)) % 10000:04d}"
                directive = f"Tell the user that the report has been created with the id {report_id}"
            else:
                continue
        # Simulate report creation

        directives = [directive]
        instructions = {
            "role": "to assist the user in creating a report. Once the report is created, you can submit it to the resolv system. They are free to cancel it at any time during the creation process.",
            "directives": directives,
            "capabilities": ["create incident reports", "submit reports to resolv system"],
            "parameters": [{
                "condition": "You are likely to say you have submitted or cancelled a report or are going to do those things a report",
                "response": "Never say you have submitted or cancelled a report or are going to do those things unless instructed to by a directive."
            }, {
                "condition": "User is talking about reports, reporting or making a report but has not specified that it wants you to create a report and is not in the process of creating a report",
                "response": "Ask them if they want to create a report that will be submitted to the resolv system"
            }]
        }
        persona = MockPersonaAction()

        message = await persona.execute(
            utterance=utterance,
            context=context,
            instructions=instructions,

        )
        event = "User is in the process of creating a report"

        return (message, event)



        mock_report = {
            "report_id": report_id,
            "status": "created",
            "context": str(context) if context else "No context provided",
            "timestamp": datetime.now().isoformat(),
            "message": f"Mock report {report_id} created successfully"
        }


        print(f"{Colors.GREEN}   ✅ Mock report created: {report_id}{Colors.END}")
        return mock_report


class VectorSearchAction:
    """Mock action for submitting reports."""

    def __init__(self, **kwargs):
        self.label = kwargs.get('label', 'mock_submit_report_action')
        self.description = kwargs.get('description', 'Mock action for submitting reports')
        self.agent_id = kwargs.get('agent_id', 'test_agent')
        # print(f"{Colors.CYAN}🔧 MockSubmitReportAction initialized{Colors.END}")

    async def execute(self, utterance: str, context: Any = None) -> Dict[str, Any]:
        """Execute the mock submit report action."""
        print(f"{Colors.GREEN}   📤 VectorSearchAction.execute() called{Colors.END}")
        print(f"{Colors.YELLOW}   Context: {context}{Colors.END}")

        instructions = {"directives": [
            "If the information required to answer the user request is not available in your role or description or capabilities then advise the user that you don't have the relevant information to answer their question."
        ]}
        persona = MockPersonaAction()

        result = await persona.execute(
            utterance=utterance,
            context=context,
            instructions=instructions,

        )
        return (result, {"event": "Vector store could not asnwer the user request"})


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Interactive sandbox for testing Initial Phase Action"
    )
    parser.add_argument(
        "utterance",
        nargs="?",
        help="Single utterance to process (if not provided, enters interactive mode)"
    )
    parser.add_argument(
        "--agent",
        default="jvagent/example_agent",
        help="Agent reference (default: jvagent/example_agent)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format"
    )

    args = parser.parse_args()
    competencies_dict = [
        {
            "id": "1.ic_location",
            "label": "get_ice_cream_location_workflow",
            "title": "Get location of all HQ ice cream parlors",
            "description": "Gets the location of all HQ ice cream parlors and filters them based on user location whenever user wants ice cream",
            "anchors": ["Message is a request to get location of all HQ ice cream parlors or cancelling the process to get location of all HQ ice cream parlors"],
            "states": [
                {
                    "id": "1.get_ice_cream_location_state",
                    "label": "get_ice_cream_location_state",
                    "description": "Gets the location of all ice cream parlors",
                    "actions": [
                        {
                            "label": "get_ice_cream_location_action",
                            "description": "Gets the location of all ice cream parlors",
                        }
                    ]
                },
                {
                    "id": "2.user_location",
                    "label": "get_user_location_state",
                    "description": "Get location of user",
                    "actions": [
                        {
                            "label": "get_user_location_action",
                            "description": "Get location of user",
                        }
                    ]
                },
                {
                    "id": "3.filter_ic_places",
                    "label": "filter_ice_cream_places",
                    "description": "Filter ice cream places",
                    "actions": [
                        {
                            "label": "filter_ice_cream_places_action",
                            "description": "Filter ice cream places",
                        }
                    ]
                }
            ]
        },
        {
            "id": "2.sw",
            "label": "update_notification_subscription_workflow",
            "description": "Update notifications subscription for GDF",
            "anchors": ["Message is a request to update notifications subscription for GDF"],
            "states": [
                {
                    "id": "1.extraction_state",
                    "label": "extraction_state",
                    "description": "Extracts user's subscription preferences",
                    "actions": [
                        {
                            "label": "extract_subscription_preferences_action",
                            "description": "Extracts user's subscription preferences",
                        }
                    ]
                },
            ]
        },
        {
            "id": "3.rc",
            "label": "create_report",
            "description": "Create a report",
            "anchors": ["Message is a request to create a report or  presenting an issue related to the the city", "Message is a request to cancel the process to create a report","Message is providing information to create a report"],
            "states": [
                {
                    "id": "1.extraction_state",
                    "label": "extraction_state",
                    "description": "Extracts user's subscription preferences",
                },
                {
                    "id": "2.create_report_state",
                    "label": "create_report_state",
                    "description": "Create report",
                    "actions": [
                        {
                            "label": "create_report",
                            "description": "Submit report",
                        }
                    ]
                }
            ]
        },
        {
            "id": "4.rag",
            "label": "vector_search",
            "title": "Vector search",
            "description": "Vector search",
            "anchors": [
                # "Message is a request for information",
                "Message is a request to get the number of incident reports submitted"
                "User asks a knowledge-based question",
                "User requests information from knowledge base",
                "User needs context or knowledge retrieval",
                "User asks a question requiring search",
                "User wants to find information",
                "User queries knowledge base",
                "User seeks information or facts",
                "User asks about something that requires context retrieval"
            ],
            "actions": [
                {
                    "label": "vector_search",
                    "description": "Search Vector Database for information",
                }
            ]
        }
    ]
    competencies = [Competency.from_dict(c) for c in competencies_dict]

    # Create sandbox
    sandbox = InitialPhaseSandbox(agent_ref=args.agent, competencies=competencies)

    # Initialize
    if not await sandbox.initialize():
        return 1

    process_start = time.time()

    # Single utterance mode or interactive
    if args.utterance:
        # result = await sandbox.classify_utterance(args.utterance, show_json=args.json)
        result = await sandbox.interpreter(args.utterance, show_json=args.json)
        if result:
            process = ProcessingPhase(
                result,
                utterance=args.utterance,
                context=sandbox.get_context(),
                competencies=competencies,
                agent_namespace="jvagent",
                agent_name="example_agent"
            )
            responses = await process.initialize()  # Initialize action loader

        if args.json:
            do_nothing = "yes"
            # print(json.dumps(result, indent=2, default=str))

        total_duration = time.time() - process_start

        print(f"\n{Colors.BOLD}{Colors.CYAN}⏱️  Total Processing Time: {total_duration:.3f}s{Colors.END}")

        # Store in history
        # for response in responses:
        #     sandbox.add_to_conversation_history(
        #         utterance=args.utterance,
        #         events=response[1],
        #         response=response[0],
        #     )

    else:
        await sandbox.interactive_mode()

    return 0
    vector_events = [e for e in events if "vector_search" in e.event_type]
    llm_events = [e for e in events if "llm_evaluation" in e.event_type]

    print(f"\n{Colors.BOLD}{Colors.BLUE}📡 EVENTS ({len(events)} total){Colors.END}")
    for ev in vector_events:
        if "complete" in ev.event_type:
            data = ev.data
            print(f"  🔍 Vector Search: {data.get('parameters_found', 0)} params, "
                    f"{data.get('competencies_found', 0)} competencies "
                    f"({data.get('search_duration_ms', 0):.1f}ms)")

    for ev in llm_events:
        if "complete" in ev.event_type:
            data = ev.data
            print(f"  🤖 LLM Evaluation: Generated intent + {data.get('parameters_count', 0)} params "
                    f"({data.get('evaluation_duration_ms', 0):.1f}ms)")

    print(f"\n{Colors.BOLD}{'='*70}{Colors.END}\n")

    async def _show_parameters(self):
        """Show all parameters."""
        params = await self.action.get_parameters()

        print(f"\n{Colors.BOLD}{Colors.GREEN}📋 PARAMETERS ({len(params)}){Colors.END}\n")

        if not params:
            print(f"{Colors.YELLOW}No parameters configured{Colors.END}\n")
            return

        for i, param in enumerate(params, 1):
            print(f"{i}. {Colors.CYAN}{param.condition}{Colors.END}")
            print(f"   Response: {param.response}")
            print(f"   Execution: {param.execution_requirement.value}")
            if param.action:
                print(f"   Action: {param.action}")
            if param.workflow:
                print(f"   Workflow: {param.workflow}")
            print(f"   ID: {Colors.YELLOW}{param.id}{Colors.END}")
            print()

    async def _add_parameter_interactive(self):
        """Add a parameter interactively."""
        print(f"\n{Colors.BOLD}Add New Parameter{Colors.END}\n")

        try:
            condition = input(f"Condition (when it applies): ").strip()
            response = input(f"Response (what to do): ").strip()
            action = input(f"Action (optional, press Enter to skip): ").strip() or None
            workflow = input(f"Workflow (optional, press Enter to skip): ").strip() or None

            exec_req = input(f"Execution (conditional/always_execute/on_first_interaction) [conditional]: ").strip()
            exec_req = exec_req or "conditional"

            param_id = await self.action.add_parameter({
                "condition": condition,
                "response": response,
                "action": action,
                "workflow": workflow,
                "execution_requirement": exec_req,
                "enabled": True,
            })

            print(f"\n{Colors.GREEN}✅ Added parameter: {param_id}{Colors.END}\n")

        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Cancelled{Colors.END}\n")

    async def _check_parameters(self, utterance: str):
        """Check which parameters apply to the utterance."""
        print(f"\n{Colors.BOLD}Checking parameters for: {Colors.CYAN}{utterance}{Colors.END}\n")

        applicable = await self.get_applicable_parameters(utterance)

        if not applicable:
            print(f"{Colors.YELLOW}No applicable parameters found{Colors.END}\n")
            return

        print(f"{Colors.GREEN}Found {len(applicable)} applicable parameter(s):{Colors.END}\n")

        for i, param in enumerate(applicable, 1):
            print(f"{i}. {Colors.CYAN}{param.get('condition')}{Colors.END}")
            print(f"   Response: {param.get('response')}")
            if param.get('action'):
                print(f"   Action: {Colors.GREEN}{param.get('action')}{Colors.END}")
            if param.get('workflow'):
                print(f"   Workflow: {Colors.GREEN}{param.get('workflow')}{Colors.END}")
            print(f"   Execution: {param.get('execution_requirement', 'conditional')}")
            print(f"   ID: {Colors.YELLOW}{param.get('id')}{Colors.END}")
            print()

    def _show_help(self):
        """Show help information."""
        print(f"\n{Colors.BOLD}{Colors.GREEN}COMMANDS{Colors.END}")
        print(f"  {Colors.YELLOW}/params{Colors.END}  - List all configured parameters")
        print(f"  {Colors.YELLOW}/add{Colors.END}     - Add a new parameter interactively")
        print(f"  {Colors.YELLOW}/reset{Colors.END}   - Reset session (start new conversation)")
        print(f"  {Colors.YELLOW}/json{Colors.END}    - Toggle JSON output mode")
        print(f"  {Colors.YELLOW}/help{Colors.END}    - Show this help")
        print(f"  {Colors.YELLOW}/quit{Colors.END}    - Exit sandbox")
        print()
        print(f"{Colors.BOLD}{Colors.GREEN}USAGE{Colors.END}")
        print(f"  Just type any text to process it through Initial Phase")
        print(f"  The action will generate embeddings, search parameters,")
        print(f"  and use LLM to generate structured instructions")
        print()

if __name__ == "__main__":
    # Check if running from correct directory
    if not os.path.exists("agents"):
        print(f"{Colors.RED}[X] Error: Please run from the examples/jvagent_app directory{Colors.END}")
        print(f"{Colors.YELLOW}   cd examples/jvagent_app{Colors.END}")
        print(f"{Colors.YELLOW}   python scripts/sandbox_initial_phase.py{Colors.END}")
        sys.exit(1)

    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print(f"\n\n{Colors.CYAN}Interrupted{Colors.END}\n")
        sys.exit(0)
