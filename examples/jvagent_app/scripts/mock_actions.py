"""Mock actions for testing the dynamic action runner.

These mock actions match the action labels defined in the competencies and can be
registered with ProcessingPhase for testing without needing actual action files.
"""
import json
import logging
from datetime import datetime
import ast
from typing import Any, Dict
from jvspatial.core.annotations import attribute

from jvagent.action.persona.base import PersonaAction

logger = logging.getLogger(__name__)


class ExamplePersonaAction(PersonaAction):
    """Example PersonaAction with custom persona configuration.

    This is an example implementation showing how to customize the PersonaAction
    for a specific agent. The persona is configured with:
    - Custom name, role, and description
    - Specific capabilities
    - Custom base parameters

    Configuration can be overridden via agent.yaml context.
    """

    # Override persona defaults for the example agent
    persona_name: str = attribute(
        default="Example Assistant",
        description="Agent display name",
    )
    persona_role: str = attribute(
        default="A helpful AI assistant for demonstrations",
        description="Agent role description",
    )
    persona_description: str = attribute(
        default=(
            "You are a friendly and knowledgeable assistant that helps users "
            "understand how the jvagent framework works. You provide clear, "
            "concise answers and demonstrate best practices."
        ),
        description="Detailed agent description",
    )
    persona_capabilities: list = attribute(
        default_factory=lambda: [
            "Answer questions about jvagent",
            "Demonstrate action delegation",
            "Process user interactions with behavioral parameters",
            "Provide streaming and non-streaming responses",
        ],
        description="List of agent capabilities",
    )

    # Custom base parameters for this agent
    base_parameters: list = attribute(
        default_factory=lambda: [
            {
                "condition": "User asks about jvagent",
                "response": "Explain jvagent as a modular AI agent framework built on jvspatial.",
            },
            {
                "condition": "User requests a demonstration",
                "response": "Provide a brief demonstration with example outputs.",
            },
            {
                "condition": "User asks technical questions",
                "response": "Give accurate technical details while keeping explanations accessible.",
            },
        ],
        description="Base behavioral parameters for this agent",
    )

    async def on_register(self) -> None:
        """Initialize the example persona action."""
        await super().on_register()
        logger.info(
            f"ExamplePersonaAction '{self.label}' registered with persona: {self.persona_name}"
        )


class Colors:
    """ANSI color codes for terminal output."""
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'


# Prompt templates for MockPersonaAction
class AgentPromptTemplate:
    AGENT_PROMPT_TEMPLATE = """# ROLE AND IDENTITY
You are an agent evaluator for the jvagent framework. Your name is {agent_name}, a {agent_role}.
Description: {agent_description}
Capabilities:
-{agent_capabilities}

# CONVERSATION CONTEXT
**CRITICAL**: Before analyzing the current message, FIRST review the entire conversation history above or in the context below. The conversation history contains all previous messages between the user and assistant. You MUST understand the full context of what was previously discussed to respond appropriately.

If the user's current message is short (like "yes", "no", "okay"), you MUST look at the conversation history to understand what they're responding to.

# RESPONSE FRAMEWORK
You must analyze the conversation history and current interaction through a systematic process before responding:

## STEP 1: PARAMETER ANALYSIS
Review all provided parameters. For each parameter:
- Check if its conditions are met by the current interaction
- If conditions are met, add the parameter number to "applied_parameters"
- If not met, ignore for this response

## STEP 2: DIRECTIVE ANALYSIS
Review all directives. For each directive:
- Check if it contradicts any applied parameter
- If contradictory, exclude from "applied_directives"
- If non-contradictory, include the directive number in "applied_directives"
- All the directives are included in "applied_directives" by default

## STEP 3: RESPONSE FORMULATION
Using ONLY the applied parameters and directives:
1. Determine appropriate tone and content
2. Apply the GENERAL PRINCIPLES below
3. Craft a response that feels human, not robotic
4. If the last message was sent by the AI assistant then do not repeat the same idea or phrasing unnecessarily, instead add additional information to the response. If there is nothing to add, then do not respond

# GENERAL PRINCIPLES (ALWAYS APPLY THESE)
1. **Be human-like**: Natural, conversational, avoid robotic patterns
2. **Avoid repetition**: Never repeat the same idea or phrasing unnecessarily
3. **Process secrecy**: Never mention your analysis process or parameters
4. **Information accuracy**: Only use information from directives/parameters
5. **Be concise**: Keep responses under 100 words unless context requires more
6. **Be clear**: Structure thoughts logically

# REQUIRED OUTPUT FORMAT
You MUST output ONLY valid JSON with this exact structure:
```json
{{
  "analysis_summary": "Brief explanation of your reasoning process.",
  "applied_parameters": ["parameter1", "parameter2", ...],
  "applied_directives": ["directive1", "directive2", ...],
  "message": "Your actual response to the user here" Use the applied parameters and the applied directives to craft a response to the user.
}}

# DIRECTIVES
- {directives}

# PARAMETERS
- {parameters}

# CONVERSATION HISTORY
the context below lists most recent interactions last
{conversation_history}

# USER MESSAGE
{user_message}

# FINAL INSTRUCTION
Think step-by-step internally, then output ONLY the JSON. The "message" field should be ready to send directly to the user.
"""
#     """Prompt template for MockPersonaAction."""

#     AGENT_PROMPT_TEMPLATE = """
# Evaluate the given parameters and use them to craft a message to respond to the provided interaction in a natural and human-like manner.

# The message should take the following into consideration
# Your name is {agent_name}. Your role is {agent_role}. You are described as follows:
# {agent_description}

# You are capable of carrying out the following special abilities:
# -{agent_capabilities}

# TASK DESCRIPTION:
# -----------------
# Evaluate the given parameters and use them to craft a message to respond to the provided interaction in a natural and human-like manner.
# Your task is to produce a evaluate a response JSON object that use your directives and parameters to craft a message that responds to the user's message.
# Return only a JSON structure with the following keys:
# - applied parameters: the list of parameters whose conditions are met, Return an empty list if none are applicable
# - applied directives: the list of directives that are used to craft the message. Only directives that are contradicting applied parameters should be excluded from this list
# - message: The response to the user's message formed based on the applied parameters and any directives you might have. If there are no aplicable parameters respond based on the directives.

# When creating the message, always abide by the following general principles:

# 1. GENERAL BEHAVIOR: Make your response as human-like as possible. Be concise and avoid being overly polite.
# 2. AVOID REPEATING YOURSELF: When replying— avoid repeating yourself.
# 3. MAINTAIN GENERATION SECRECY: Never reveal details about the process you followed to produce your response.
# 4. ACCURACY OF RESPONSES: Only share information if it was given in the directives or parameters. Do NOT hallucinate.
# 5. BRIEF RESPONSES: Keep your responses brief and to the point, preferably under 100 words unless the context requires more detail.
# 6. EASY-TO-READ FORMATTING: Make responses easy to read by utilizing paragraphs, bolding and bullet points when necessary

# {directives}

# {parameters}
# """

DIRECTIVES_INSTRUCTION = """
### DIRECTIVES
Use the following directives to respond to the user."""
# Avoid mentioning or asking for things not specified by the directive.
# Be as concise as possible when carrying out the directive.
# You must follow the directive unless the directive conflicts with a parameter.
# """

NO_DIRECTIVES_INSTRUCTION = """
### DIRECTIVES
There are no specific directives for this interaction.
1. Please generate your response using your best judgment, following general conversational principles.
"""

PARAMETERS_INSTRUCTION = """
You may choose not to follow a parameter only in the following cases:
    - It conflicts with a previous customer request.
    - It is clearly inappropriate given the current context of the conversation.
"""

NO_PARAMETERS_INSTRUCTION = """
### PARAMETERS
In formulating your reply, you are normally required to follow behavioral parameters.
However, in this case, no special behavioral parameters were provided.
"""


class MockPersonaAction:
    """Simplified PersonaAction for sandbox testing.

    This mock version provides basic persona-driven response generation
    without the full jvagent infrastructure (Memory, Conversations, etc.).
    """

    def __init__(self, **kwargs):
        self.label = kwargs.get('label', 'persona_action')
        self.description = kwargs.get('description', 'Generate persona-based responses')
        self.agent_id = kwargs.get('agent_id', 'test_agent')

        # Persona configuration
        self.persona_name = kwargs.get('persona_name', 'Test Assistant')
        self.persona_role = kwargs.get('persona_role', 'A helpful AI assistant')
        self.persona_description = kwargs.get('persona_description',
            'You are a friendly and helpful assistant that provides clear, concise answers.')
        self.persona_capabilities = kwargs.get('persona_capabilities', [
            'Answer questions',
            'Provide helpful information',
            'Can create and submit reports to resolv ims system'
        ])
        self.base_parameters = [
            {
                "condition": "User asks about your identity",
                "response": "Do not mention OpenAI only mention your name and role.",
            },
            {
                "condition": "The previous message was by the 'assistant' and you have more information to add.",
                "response": "Create another response that builds on the previous response. Use words like 'furthermore' or 'moreover' etc.to show that this is a continuation of the previous response.",
            },
            {
                "condition": "You are likely to repeat yourself",
                "response": "Do not repeat yourself. Instead add additional information to the response or do not respond if additional information is not available.",
            },
        ]

        # Model configuration
        self.model_name = kwargs.get('model_name', 'gpt-4o')
        self.temperature = kwargs.get('temperature', 0.7)
        self.max_tokens = kwargs.get('max_tokens', 1000)


        # Custom prompt template
        self.agent_prompt_template = AgentPromptTemplate()


    async def execute(self, utterance: str, context: Any = None, instructions: dict = {}) -> tuple[str, dict]:
        """Execute the persona action to generate a response.

        Args:
            utterance: User's current message
            context: Conversation context with recent interactions

        Returns:
            Tuple of (response_text, event_dict)
        """
        print(f"{Colors.YELLOW}   Instructions: {instructions}{Colors.END}")

        # Handle None instructions
        if instructions is None:
            instructions = {}

        if instructions.get('directives', ""):
            directives_prompt = await self._build_directives_prompt(instructions.get('directives'))
        else:
            directives_prompt = NO_DIRECTIVES_INSTRUCTION

        if instructions.get('parameters', []):
            parameters = instructions.get('parameters')
            parameters.extend(self.base_parameters)
            parameters_prompt = await self._build_parameters_prompt(parameters)
        elif self.base_parameters:
            parameters_prompt = await self._build_parameters_prompt(self.base_parameters)
        else:
            parameters_prompt = NO_PARAMETERS_INSTRUCTION

        if instructions.get('role', ""):
            self.persona_role = instructions.get('role')

        if instructions.get('capabilities', ""):
            self.persona_capabilities = instructions.get('capabilities')

        # Build system prompt
        if context and isinstance(context, dict) and 'recent_interactions' in context:
            system_prompt = self._build_system_prompt(parameters_prompt, directives_prompt, utterance, context)
        else:
            system_prompt = self._build_system_prompt(parameters_prompt, directives_prompt, utterance, {})

        # Build conversation messages
        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history if available
        # if context and isinstance(context, dict) and 'recent_interactions' in context:
        #     messages.extend(context['recent_interactions'])

        # Add current user message
        # if context and isinstance(context, list) and not context['recent_interactions'][-1].get("role") == "assistant" or not context:
        #     messages.append({"role": "user", "content": utterance})
        # else:
        #     messages.append({"role": "user", "content": utterance})

        # Call LLM
        print()
        try:
            from jvagent.action.model.openai import OpenAIModelAction
            import time

            start = time.time()
            model_action = OpenAIModelAction(
                api_key="YOUR_OPENAI_API_KEY_HERE",
                api_endpoint="https://api.openai.com/v1",
                model=self.model_name,
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )

            result = await model_action._query(messages)
            duration = time.time() - start

            print(f"{Colors.CYAN}   ⏱️  Persona LLM call: {duration:.3f}s{Colors.END}")
            print()

            response = self.convert_str_to_json(result.response)
            print(f"{Colors.GREEN}  Persona result after conversion: \n {response}")

            response_text = response.get("message", "") #if response else "I apologize, I'm having trouble responding right now."
            return response_text

        except Exception as e:
            print(f"{Colors.RED}   ❌ Error generating persona response: {e}{Colors.END}")
            return ("I apologize, I encountered an error while trying to respond.",
                    {"action": "persona_response", "error": str(e)})

    def _build_system_prompt(self, parameters_prompt: str, directives_prompt: str, utterance: str, context: dict) -> str:
        """Build the system prompt with persona information."""
        template = self.agent_prompt_template.AGENT_PROMPT_TEMPLATE or AGENT_PROMPT_TEMPLATE
        # Build final prompt
        prompt = template.format(
            agent_name=self.persona_name,
            agent_role=self.persona_role,
            agent_description=self.persona_description,
            agent_capabilities="\n-".join(self.persona_capabilities),
            # date=date_str,
            # time=time_str,
            parameters=parameters_prompt,
            directives=directives_prompt,
            conversation_history=str(context.get('recent_interactions', {})),
            user_message=utterance
        )
        return prompt

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


    async def _build_directives_prompt(self, directives: list[str]) -> str:
        """Build the directives prompt."""
        if not directives:
            return NO_DIRECTIVES_INSTRUCTION

        directives_text = []
        for i, directive in enumerate(directives, 1):
            if isinstance(directive, str):
                directives_text.append(f"{i}. {directive}")
        return f"{DIRECTIVES_INSTRUCTION}\n{directives_text}"

    async def _build_parameters_prompt(self, parameters: list[dict]) -> str:
        """Build the parameters prompt."""
        if not parameters:
            return NO_PARAMETERS_INSTRUCTION

        params_text = []
        for i, param in enumerate(parameters, 1):
            if isinstance(param, dict):
                condition = param.get('condition', '')
                response = param.get('response', '')
                params_text.append(f"{i}.When {condition} then {response.lower()}")

        params_str = "\n".join(params_text)
        return f"### PARAMETERS\nWhen crafting your reply, you must follow the behavioral parameters provided below:\n\n{params_str}\n\n{PARAMETERS_INSTRUCTION}"

# Mock Actions for Ice Cream Location Workflow
class MockGetIceCreamLocationAction:
    """Mock action for getting ice cream parlor locations."""

    def __init__(self, **kwargs):
        self.label = kwargs.get('label', 'get_ice_cream_location_action')
        self.description = kwargs.get('description', 'Gets the location of all ice cream parlors')
        self.agent_id = kwargs.get('agent_id', 'test_agent')
        # print(f"{Colors.CYAN}🔧 MockGetIceCreamLocationAction initialized{Colors.END}")

    async def execute(self, context: Any = None) -> Dict[str, Any]:
        """Execute the mock get ice cream location action."""
        print(f"{Colors.GREEN}   🍦 MockGetIceCreamLocationAction.execute() called{Colors.END}")
        print(f"{Colors.YELLOW}   Context: {context}{Colors.END}")

        ice_cream_locations = [
            {"name": "HQ Ice Cream Parlor - Downtown", "address": "123 Main St", "lat": 40.7128, "lon": -74.0060},
            {"name": "HQ Ice Cream Parlor - Uptown", "address": "456 Park Ave", "lat": 40.7589, "lon": -73.9851},
            {"name": "HQ Ice Cream Parlor - Midtown", "address": "789 Broadway", "lat": 40.7549, "lon": -73.9840}
        ]

        result = {
            "status": "success",
            "locations": ice_cream_locations,
            "count": len(ice_cream_locations),
            "timestamp": datetime.now().isoformat()
        }

        print(f"{Colors.GREEN}   ✅ Found {len(ice_cream_locations)} ice cream parlor locations{Colors.END}")
        return result


class MockGetUserLocationAction:
    """Mock action for getting user location."""

    def __init__(self, **kwargs):
        self.label = kwargs.get('label', 'get_user_location_action')
        self.description = kwargs.get('description', 'Get location of user')
        self.agent_id = kwargs.get('agent_id', 'test_agent')
        # print(f"{Colors.CYAN}🔧 MockGetUserLocationAction initialized{Colors.END}")

    async def execute(self, context: Any = None) -> Dict[str, Any]:
        """Execute the mock get user location action."""
        print(f"{Colors.GREEN}   📍 MockGetUserLocationAction.execute() called{Colors.END}")
        print(f"{Colors.YELLOW}   Context: {context}{Colors.END}")

        # Mock user location (New York City)
        user_location = {
            "city": "New York",
            "state": "NY",
            "lat": 40.7128,
            "lon": -74.0060,
            "address": "User's current location"
        }

        result = {
            "status": "success",
            "user_location": user_location,
            "timestamp": datetime.now().isoformat()
        }

        print(f"{Colors.GREEN}   ✅ User location: {user_location['city']}, {user_location['state']}{Colors.END}")
        return result


class MockFilterIceCreamPlacesAction:
    """Mock action for filtering ice cream places."""

    def __init__(self, **kwargs):
        self.label = kwargs.get('label', 'filter_ice_cream_places_action')
        self.description = kwargs.get('description', 'Filter ice cream places')
        self.agent_id = kwargs.get('agent_id', 'test_agent')
        # print(f"{Colors.CYAN}🔧 MockFilterIceCreamPlacesAction initialized{Colors.END}")

    async def execute(self, context: Any = None) -> Dict[str, Any]:
        """Execute the mock filter ice cream places action."""
        print(f"{Colors.GREEN}   🔍 MockFilterIceCreamPlacesAction.execute() called{Colors.END}")
        print(f"{Colors.YELLOW}   Context: {context}{Colors.END}")

        # Mock filtered results (closest parlors)
        filtered_locations = [
            {"name": "HQ Ice Cream Parlor - Downtown", "address": "123 Main St", "distance_miles": 0.5},
            {"name": "HQ Ice Cream Parlor - Midtown", "address": "789 Broadway", "distance_miles": 1.2}
        ]

        result = {
            "status": "success",
            "filtered_locations": filtered_locations,
            "count": len(filtered_locations),
            "criteria": "closest to user location",
            "timestamp": datetime.now().isoformat()
        }

        print(f"{Colors.GREEN}   ✅ Filtered to {len(filtered_locations)} nearby ice cream parlors{Colors.END}")
        return result


# Mock Actions for Subscription Workflow
class MockExtractSubscriptionPreferencesAction:
    """Mock action for extracting subscription preferences."""

    def __init__(self, **kwargs):
        self.label = kwargs.get('label', 'extract_subscription_preferences_action')
        self.description = kwargs.get('description', 'Extracts user subscription preferences')
        self.agent_id = kwargs.get('agent_id', 'test_agent')
        # print(f"{Colors.CYAN}🔧 MockExtractSubscriptionPreferencesAction initialized{Colors.END}")

    async def execute(self, context: Any = None) -> Dict[str, Any]:
        """Execute the mock extract subscription preferences action."""
        print(f"{Colors.GREEN}   📋 MockExtractSubscriptionPreferencesAction.execute() called{Colors.END}")
        print(f"{Colors.YELLOW}   Context: {context}{Colors.END}")

        # Mock extracted preferences
        preferences = {
            "notification_type": "email",
            "frequency": "daily",
            "topics": ["updates", "alerts"],
            "opt_in": True
        }

        result = {
            "status": "success",
            "extracted_preferences": preferences,
            "timestamp": datetime.now().isoformat()
        }

        print(f"{Colors.GREEN}   ✅ Extracted subscription preferences{Colors.END}")
        return result


class VectorSearchAction:
    """Mock action for submitting reports."""

    def __init__(self, **kwargs):
        self.label = kwargs.get('label', 'mock_submit_report_action')
        self.description = kwargs.get('description', 'Mock action for submitting reports')
        self.agent_id = kwargs.get('agent_id', 'test_agent')
        # print(f"{Colors.CYAN}🔧 MockSubmitReportAction initialized{Colors.END}")

    async def execute(self, context: Any = None) -> Dict[str, Any]:
        """Execute the mock submit report action."""
        print(f"{Colors.GREEN}   📤 VectorSearchAction.execute() called{Colors.END}")
        print(f"{Colors.YELLOW}   Context: {context}{Colors.END}")

        result = {
            "status": "submitted",
            "confirmation": "Report has been submitted to the system",
            "timestamp": datetime.now().isoformat()
        }

        print(f"{Colors.GREEN}   ✅ Mock report submitted{Colors.END}")
        return result



# Mock Actions for Report Creation Workflow
class MockCreateReportAction:
    """Mock action for creating reports."""

    def __init__(self, **kwargs):
        self.label = kwargs.get('label', 'create_report')
        self.description = kwargs.get('description', 'Create a report')
        self.agent_id = kwargs.get('agent_id', 'test_agent')
        self.results:dict = {}
        # print(f"{Colors.CYAN}🔧 MockCreateReportAction initialized{Colors.END}")

    async def execute(self, context: Any = None) -> Dict[str, Any]:
        """Execute the mock create report action."""
        print(f"{Colors.GREEN}   📝 MockCreateReportAction.execute() called{Colors.END}")
        print(f"{Colors.YELLOW}   Context: {context}{Colors.END}")

        # Simulate report creation
        report_id = f"R{hash(str(context)) % 10000:04d}"

        mock_report = {
            "report_id": report_id,
            "status": "created",
            "description": str(context) if context else "No description provided",
            "timestamp": datetime.now().isoformat(),
            "message": f"Report {report_id} created successfully"
        }

        print(f"{Colors.GREEN}   ✅ Report created: {report_id}{Colors.END}")
        return mock_report


def register_all_mock_actions(processing_phase):
    """Register all mock actions with a ProcessingPhase instance.

    Args:
        processing_phase: ProcessingPhase instance to register actions with
    """
    processing_phase.register_mock_action("get_ice_cream_location_action", MockGetIceCreamLocationAction())
    processing_phase.register_mock_action("get_user_location_action", MockGetUserLocationAction())
    processing_phase.register_mock_action("filter_ice_cream_places_action", MockFilterIceCreamPlacesAction())
    processing_phase.register_mock_action("extract_subscription_preferences_action", MockExtractSubscriptionPreferencesAction())
    processing_phase.register_mock_action("create_report", MockCreateReportAction())

    print(f"{Colors.GREEN}✅ All mock actions registered{Colors.END}")
