"""Service Directory Action for finding and connecting to services.

This action serves as a directory assistant that helps users find services
using vector search. It indexes available services from services.py and
matches user requests to the most relevant services.
"""

import logging
import json
from typing import Any, Dict, List, Optional, Union, Tuple

from jvspatial.core.annotations import attribute
from jvagent.action.base import Action
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.vectorstore.base import VectorStore
from .services import SERVICES

from jvagent.action.interview import (
    InterviewInteractAction,
    input_handler,
    input_validator,
    input_directive_override,
    on_interview_complete,
)
from jvagent.action.interview.core.session.interview_session import InterviewSession
from jvagent.action.interview.core.foundation.enums import ValidationStatus
from jvagent.memory import Interaction

logger = logging.getLogger(__name__)


class ServiceDirectoryInteractAction(InterviewInteractAction):
    """Directory assistant for finding and connecting to services."""

    vectorstore_action_type: str = attribute(
        default="",
        description="Entity type of VectorStore action (e.g., 'TypesenseVectorStore').",
    )
    collection: str = attribute(
        default="service_directory",
        description="Collection name for service directory.",
    )
    k: int = attribute(
        default=100,
        description="Number of search results to retrieve."
    )
    min_score_threshold: float = attribute(
        default=0.4,
        description="Minimum similarity score to consider a match.",
        ge=0.0,
        le=1.0,
    )

    # Anchors for routing
    anchors: List[str] = attribute(
        default=[
            "Find a service",
            "I need help finding something",
            "Directory assistance",
            "Show me available services",
            "I want to find a hotel",
            "Where can I get food?",
            "Look up a service",
            "Search for services",
        ],
        description="Anchor statements for routing to this action."
    )

    question_index: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "name": "category",
                "question": "What category of service are you looking for?",
                "constraints": {
                    "description": "The category of service the user is looking for",
                    "instructions": "The category of service must be one of the following: hotel, restaurant, transportation, entertainment, or other.",
                    "extraction_instructions": "Extract the category of service the user is looking for.",
                    "enum": ["hotel", "food", "transportation", "entertainment", "electronics", "health", "beauty", "fitness", "other"],
                    "type": "string",
                },
                "required": True
            },
            {
                "name": "subcategory",
                "question": "What type of product or service are you looking for?",
                "constraints": {
                    "description": "The subcategories of the product or service the user is looking for. Subcategories are based on the category selected and should not be called subcategories when interacting with user.",
                    "instructions": "Extract the options that best describe the user's request.",
                    "extraction_instructions": "Extract the options that best describe the user's request. if the user's request is not related to any of the options, do not extract anything for this field. Never return empty lists.",
                    "options": ['dine-in', 'asian cuisine', 'fast food', 'delivery', 'groceries', 'online shopping', 'cafe', 'bakery', 'luxury'],
                    "type": "list",
                },
                "required": True
            },
            {
                "name": "other_details",
                "question": "Are there any other details you would like??",
                "constraints": {
                    "description": "What the user wants",
                    "instructions": "Additional details of the product or service the user is looking for",
                    "type": "string",
                },
                "required": False
            },
        ],
        description="List of question configurations."
    )

    # Model Configuration
    model_action_type: str = attribute(
        default="OpenAILanguageModelAction",
        description="Entity type of the LanguageModelAction to use",
    )

    model: str = attribute(
        default="gpt-4.1",
        description="Default model name"
    )

    model_temperature: float = attribute(
        default=0.0,
        description="Temperature for extraction"
    )

    responses: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Accumulated responses from user interactions"
    )
    completion_event_message_template: str = attribute(
        default="",
        description="Event message template for completed interview state.",
    )

    async def _index_services(self) -> None:
        """Index services from services.py into the vector store."""
        try:
            vectorstore = await self._get_vectorstore_action()
            if not vectorstore:
                logger.warning("ServiceDirectoryAction: VectorStore not found, cannot index services.")
                return

            collection = await vectorstore._resolve_collection_name(self.collection)
            await vectorstore.delete_collection(collection)
            logger.info(f"ServiceDirectoryAction: Indexing {len(SERVICES)} services into '{collection}'...")

            documents = []
            for service in SERVICES:
                if not service.get("enabled", True):
                    continue

                content = (
                    f"Service: {service['name']}\n"
                    f"Category: {service.get('category', 'General')}\n"
                    f"Description: {service['description']}\n"
                )

                doc = {
                    "id": service['name'],
                    "content": content,
                    "service_name": service['name'],
                    "category": service.get("category", ""),
                    "subcategory": service.get("subcategory", []),
                    "tags": service.get("tags", []),
                    "address": service.get("address", ""),
                    "features": service.get("features", []),
                    "description": service['description'],
                    "connection_info": service.get("connection_info", ""),
                    "metadata": service
                }
                documents.append(doc)

            if documents:
                await vectorstore.create_collection(collection)
                await vectorstore.store(collection=collection, documents=documents)
                logger.info("ServiceDirectoryAction: Service indexing complete.")

        except Exception as e:
            logger.error(f"ServiceDirectoryAction: Error indexing services: {e}", exc_info=True)

    # async def execute(self, visitor: "InteractWalker") -> None:
    #     """Execute action: Set up session persistence and delegate to InterviewAction."""
    #     print("     ---- Executing ServiceDirectoryAction ----")

        # Store agent_id in session context so static handlers can access the action
        # print("\033[92m Agent ID stored in session context: ", visitor.interview_session, "\033[0m")
        # if not visitor.interview_session.context.get('config_k'):
        #     visitor.interview_session.context['config_k'] = self.k
        #     visitor.interview_session.context['config_collection'] = self.collection
        #     visitor.interview_session.context['config_min_score'] = self.min_score_threshold

        # Load persisted subcategories into enum if valid
        # responses = visitor.interview_session
        # print(responses)
        # if responses and 'available_subcategories' in responses:
        #     self.question_index[1]['constraints']['enum'] = responses['available_subcategories']
        #     print("\033[92m Loaded persisted subcategories:", self.question_index[1]['constraints']['enum'], "\033[0m")

        # await super().execute(visitor)

    async def _get_vectorstore_action(self) -> Optional[VectorStore]:
        """Get the VectorStore action."""
        if self.vectorstore_action_type:
            vectorstore = await self.get_action(self.vectorstore_action_type)
            if vectorstore:
                return vectorstore
        return await self.get_action(VectorStore)

    async def on_enable(self) -> None:
        await super().on_enable()
        await self._index_services()

    async def on_reload(self) -> None:
        await super().on_reload()
        await self._index_services()


# --- Handlers and Validators (Static/Module level) ---

async def _get_vectorstore_for_session(session: InterviewSession) -> Tuple[Optional[VectorStore], str, int, float]:
    """Retrieve vectorstore and config from session context/agent."""
    agent_id = session.context.get('agent_id')

    k = session.context.get('config_k', 100)
    collection = session.context.get('config_collection', 'service_directory')
    min_score = session.context.get('config_min_score', 0.4)

    if not agent_id:
        return None, collection, k, min_score

    # Find vectorstore for this agent
    # Assuming standard VectorStore
    # We can try to finding it using Action.find_one
    vectorstore = await Action.find_one({"context.agent_id": agent_id, "context.class_name": "TypesenseVectorStore"})
    if not vectorstore:
        vectorstore = await Action.find_one({"context.agent_id": agent_id, "context.class_name": "VectorStore"})

    return vectorstore, collection, k, min_score


@input_handler('category')
async def handle_category(
    raw_input: str,
    session: InterviewSession,
    interaction: Interaction
) -> str:
    """Handle category input by performing vector search."""
    # Handle case where raw_input might already be processed (list)
    if isinstance(raw_input, list):
        raw_input = raw_input[0] if raw_input else ""

    print(f"\033[91mRaw Input in handle category: {raw_input}\033[0m")
    query = raw_input.strip() if isinstance(raw_input, str) else str(raw_input)
    if not query:
        return raw_input

    try:
        # Find the actual ServiceDirectoryInteractAction instance from the graph
        service_directory_action = await ServiceDirectoryInteractAction.find_one({
            "context.enabled": True
        })

        if not service_directory_action:
            logger.warning("ServiceDirectoryAction: Could not find action instance in graph.")
            return raw_input

        vectorstore = await service_directory_action._get_vectorstore_action()
        print(f"\033[91mVectorstore in handle category: {vectorstore}\033[0m")
        collection = service_directory_action.collection
        k = service_directory_action.k
        min_score = service_directory_action.min_score_threshold

        if not vectorstore:
            logger.warning("ServiceDirectoryAction: VectorStore not found via session lookup.")
            return raw_input

        if not vectorstore._client:
            await vectorstore._initialize_client()

        # Perform search
        results = await vectorstore.search(
            collection=collection,
            query=query,
            k=k,
            filters={
                "category": query
            }
        )

        matches = []
        seen_ids = set()

        if results:
            for r in results:
                # Simple scoring check
                if r.get("score", 0.0) >= min_score:
                    matches.append(r)
                    seen_ids.add(r.get("id"))

        # Fallback
        if len(matches) < k:
            # Basic fallback logic
            pass

        matches = matches[:k]
        session.context['search_results'] = matches
        session.context['last_query'] = query

        # Update available subcategories
        categories = []
        for m in matches:
            category = m.get("metadata", {}).get("subcategory")
            if category:
                if isinstance(category, list):
                    for cat in category:
                        if cat not in categories:
                             categories.append(cat)
                elif isinstance(category, str) and category not in categories:
                    categories.append(category)
        print(f"\033[93mCategories: {categories}\033[0m")
        session.set_response('available_subcategories', categories)
        await session.save()

    except Exception as e:
        logger.error(f"ServiceDirectoryAction: Error in handle_category: {e}", exc_info=True)

    return raw_input

@input_validator('category')
def validate_category(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    """Validate that we found some services."""
    matches = session.context.get('search_results', [])
    print(f"\033[93 category validated\033[0m")
    if not matches:
        return ValidationStatus.INVALID, f"I couldn't find any services matching '{value}'. Could you try a different category?"
    return ValidationStatus.VALID, None

@input_directive_override('category')
async def custom_category_directive(
    field_name: str,
    value: str,
    session: InterviewSession,
    interaction: Interaction,
    visitor: InteractWalker
) -> Optional[Union[str, Tuple[str, str]]]:
    matches = session.context.get('search_results', [])
    if not matches:
        return None, None

    count = len(matches)
    subcategories = session.get_response('available_subcategories') or []

    if len(matches) > 3:
        service_directory_action = await ServiceDirectoryInteractAction.find_one({
            "context.enabled": True
        })
        for q in service_directory_action.question_index:
            if q['name'] == 'subcategory':
                q['constraints']['options'] = subcategories
                q['constraints']['description'] += f"Choose from the following options: {', '.join(subcategories)}."
                await service_directory_action.on_reload()
                break
        msg = f"I found {count} services related to your query."
        msg += f" They fall into these areas: {', '.join(subcategories)}."
        msg += " Please select which area you are interested in or feel free to descibe what it is you're looking for."
        return "append", f"Present the following information in markdown format to the user: {msg}"

    count = len(matches)
    items = []
    for m in matches[:3]:
        m_meta = m.get("metadata", {})
        items.append(f"- **{m_meta.get('service_name')}**: {m_meta.get('description')}")

    list_text = "\n".join(items)
    msg = f"I found {count} services matching that description.\n\n{list_text}\n\n"
    msg += "Which one would you like to know more about, or connect to?"

    return "replace", f"Present the information to the user: {msg}"

@input_handler('subcategory')
async def handle_subcategory(
    raw_input: str,
    session: InterviewSession,
    interaction: Interaction
) -> str:
    """Handle subcategory input by refining search."""
    # Handle case where raw_input might already be processed (list)
    print(f"\033[91mRaw input in handle_subcategory: {raw_input}\033[0m")
    if isinstance(raw_input, list):
        raw_input = ", ".join(raw_input)
    query = raw_input.strip() if isinstance(raw_input, str) else str(raw_input)
    if not query:
        return raw_input

    try:
        print(f"\033[92mresponses in interview session: {session.responses}\033[0m")
        # Find the actual ServiceDirectoryInteractAction instance from the graph
        service_directory_action = await ServiceDirectoryInteractAction.find_one({
            "context.enabled": True
        })

        if not service_directory_action:
            logger.warning("ServiceDirectoryAction: Could not find action instance in graph.")
            return raw_input

        vectorstore = await service_directory_action._get_vectorstore_action()
        if not vectorstore:
            return raw_input

        collection = service_directory_action.collection
        k = service_directory_action.k
        min_score = service_directory_action.min_score_threshold

        last_query = session.context.get('last_query', "")
        combined_query = f"{last_query} {query}"

        subcategories = session.get_response('available_subcategories') or []
        allowed_filters = {"category", "subcategory", "tags"}
        search_filters = {}
        for key, value in session.responses.items():
            if key in allowed_filters and value:
                search_filters[key] = value
        search_filters["subcategory"] = query

        if not vectorstore._client:
            await vectorstore._initialize_client()

        results = await vectorstore.search(
            collection=collection,
            query=combined_query,
            k=k,
            filters=search_filters
        )

        matches = []
        if results:
            for r in results:
                 if r.get("score", 0.0) >= min_score:
                      matches.append(r)

        session.context['search_results'] = matches[:k]

    except Exception as e:
        logger.error(f"ServiceDirectoryAction: Error in handle_subcategory: {e}", exc_info=True)

    return raw_input

@input_validator('subcategory')
def validate_subcategory(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
    print(f"\033[91mValue in validate_subcategory: {value}\033[0m")
    matches = session.context.get('search_results', [])
    if not matches:
        return ValidationStatus.INVALID, f"I couldn't find any specific services matching '{value}'. Could you try something else?"
    if not value:
        print(f"\033[91mValue is empty in validate_subcategory: {value}\033[0m")
        subcategories = session.get_response('available_subcategories') or []
        return ValidationStatus.INVALID, f"The subcategories are: {', '.join(subcategories)}"
    return ValidationStatus.VALID, None

@input_directive_override('subcategory')
def custom_subcategory_directive(
    field_name: str,
    value: str,
    session: InterviewSession,
    interaction: Interaction,
    visitor: InteractWalker
) -> Optional[Union[str, Tuple[str, str]]]:
    print(f"\033[91mMatches in custom_subcategory_directive: {session.context.get('search_results', [])}\033[0m")
    matches = session.context.get('search_results', [])
    if not matches:
        return "replace", "Tell the user: I couldn't find any services matching that description."

    count = len(matches)
    items = []
    for m in matches[:3]:
        m_meta = m.get("metadata", {})
        items.append(f"- **{m_meta.get('service_name')}**: {m_meta.get('description')}")

    list_text = "\n".join(items)
    msg = f"I found {count} services matching that description.\n\n{list_text}\n\n"
    msg += "Which one would you like to know more about, or connect to?"
    return "replace", f"Tell the user: {msg}"


@input_handler('other_details')
def handle_other_details(
    raw_input: str,
    session: InterviewSession,
    interaction: Interaction
) -> str:
    print(f"\033[91mRaw input in handle_other_details: {raw_input}\033[0m")

    return raw_input