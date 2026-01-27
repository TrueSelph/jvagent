"""Service Directory Action for finding and connecting to services.

This action serves as a directory assistant that helps users find services
using vector search. It indexes available services from services.py and
matches user requests to the most relevant services.
"""

import logging
import json
from typing import Any, Dict, List, Optional, Tuple

from jvspatial.core.annotations import attribute
from jvspatial.core import on_visit

from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.vectorstore.base import VectorStore
from .services import SERVICES

from jvagent.action.interview.interview_interact_action import (
    InterviewInteractAction,
    input_handler,
    input_validator,
    on_interview_complete,
)
from jvagent.action.interview.core.interview_session import InterviewSession
from jvagent.action.interview.core.validation import ValidationStatus

logger = logging.getLogger(__name__)


class ServiceDirectoryInteractAction(InterviewInteractAction):
    """Directory assistant for finding and connecting to services.

    Logic:
    1.  On startup (on_register/on_enable), indexes services from services.py
        into a dedicated vector store collection ('service_directory').
    2.  Listens for general "find service" or "help" requests.
    3.  Searches the vector store for matching services.
    4.  Presents matches to the user and offers to connect.

    Attributes:
        vectorstore_action_type: Entity type of VectorStore action.
        collection: Collection name for service directory (default: "service_directory").
        k: Number of search results to return (default: 5).
        min_score_threshold: Minimum similarity score (default: 0.5).
    """

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
                    "enum": ["hotel", "food", "transportation", "entertainment", "other"],
                    "type": "string",
                },
                "required": True
            },
            {
                "name": "subcategory",
                "question": "What subcategory of service are you looking for?",
                "constraints": {
                    "description": "The subcategory of service the user is looking for",
                    "instructions": "The subcategory of service",
                    "type": "list",
                },
                "required": True
            },
            # {
            #     "name": "tags",
            #     "question": "Are there any other details you would like??",
            #     "constraints": {
            #         "description": "The tags of service the user is looking for",
            #         "instructions": "The tags of service the user is looking for",
            #         "type": "list",
            #     },
            #     "required": False
            # },
            {
                "name": "other_details",
                "question": "Are there any other details you would like??",
                "constraints": {
                    "description": "The other details of service the user is looking for",
                    "instructions": "The other details of service the user is looking for",
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
        description="Event message template for completed interview state. Documents that the interview process has been completed. Use {class_name} placeholder. Defaults to COMPLETION_EVENT_MESSAGE_TEMPLATE from prompts.py",
    )

    async def _extract_filters(self, query: str) -> Dict[str, Any]:
        """Extract search filters from user query using LLM."""
        try:
            model_action = await self.get_action(self.model_action_type)
            if not model_action:
                logger.warning(f"ServiceDirectoryAction: Model action {self.model_action_type} not found.")
                return {}

            # Build schema description from question_index
            schema_desc = []
            for q in self.question_index:
                constraints = q.get("constraints", {})
                desc = f"- {q['name']}: {constraints.get('description', '')}"
                if "enum" in constraints:
                    desc += f" (Allowed values: {', '.join(constraints['enum'])})"
                if constraints.get("type") == "list":
                    desc += " (List of strings)"
                schema_desc.append(desc)

            schema_text = "\n".join(schema_desc)

            prompt = (
                f"Extract search filters from the user's request.\n\n"
                f"Available fields:\n{schema_text}\n\n"
                f"User Request: \"{query}\"\n\n"
                f"Return ONLY a valid JSON object with the extracted fields. "
                f"If a field is not mentioned, do not include it. "
                f"Do not include any explanation."
            )

            result = await model_action.generate(
                prompt=prompt,
                model=self.model,
                temperature=self.model_temperature,
                response_format={"type": "json_object"}
            )

            if not result:
                return {}

            # Parse JSON
            import json
            try:
                # Clean code blocks if present
                content = result
                if content.startswith("```json"):
                    content = content[7:]
                if content.endswith("```"):
                    content = content[:-3]

                extracted = json.loads(content.strip())
                logger.info(f"ServiceDirectoryAction: Extracted filters: {extracted}")
                return extracted
            except json.JSONDecodeError:
                logger.warning(f"ServiceDirectoryAction: Failed to parse extraction JSON: {result.content}")
                return {}

        except Exception as e:
            logger.error(f"ServiceDirectoryAction: Error extracting filters: {e}", exc_info=True)
            return {}

    async def on_enable(self) -> None:
        """Called when action is enabled. Ensures services are indexed."""
        await super().on_enable()
        await self._index_services()

    async def on_reload(self) -> None:
        """Called when action is reloaded. Ensures services are re-indexed."""
        await super().on_reload()
        await self._index_services()

    async def _index_services(self) -> None:
        """Index services from services.py into the vector store."""
        try:
            vectorstore = await self._get_vectorstore_action()
            if not vectorstore:
                logger.warning("ServiceDirectoryAction: VectorStore not found, cannot index services.")
                return

            collection = await vectorstore._resolve_collection_name(self.collection)

            # Check if collection has documents (simple check needed)
            # For now, we'll iterate and upsert to ensure up-to-date content
            # In a production scenario, we might check for versioning or hash

            logger.info(f"ServiceDirectoryAction: Indexing {len(SERVICES)} services into '{collection}'...")

            documents = []
            for service in SERVICES:
                if not service.get("enabled", True):
                    continue

                # Create a rich content string for embedding
                # Combine name, category, description, and anchors
                content = (
                    f"Service: {service['name']}\n"
                    f"Category: {service.get('category', 'General')}\n"
                    f"Description: {service['description']}\n"
                )

                doc = {
                    "id": service['name'],  # Use service name as ID
                    "content": content,
                    "service_name": service['name'],
                    "category": service.get("category", ""),
                    "subcategory": service.get("subcategory", []),
                    "tags": service.get("tags", []),
                    "address": service.get("address", ""),
                    "features": service.get("features", []),
                    "description": service['description'],
                    "connection_info": service.get("connection_info", ""),
                }
                documents.append(doc)

            if documents:
                 # Ensure collection exists (handled by store usually, but good to be safe)
                await vectorstore.create_collection(collection)
                await vectorstore.store(collection=collection, documents=documents)
                logger.info("ServiceDirectoryAction: Service indexing complete.")

        except Exception as e:
            logger.error(f"ServiceDirectoryAction: Error indexing services: {e}", exc_info=True)

    async def _get_or_create_collection(self, collection: str) -> None:
        """Get or create a Typesense collection.

        Args:
            collection: Collection name
        """
        if collection in self._collections:
            return

        # Check if collection exists
        if not vectorstore._client:
            await vectorstore._initialize_client()

        try:
            vectorstore._client.collections[collection].retrieve()
            vectorstore._collections[collection] = True
            return
        except Exception:
            # Collection doesn't exist, create it
            pass

        # Create collection schema
        # Note: Typesense doesn't allow 'id' as default_sorting_field
        # String fields need to be explicitly marked as sortable
        # enable_nested_fields is required for object type fields
        schema = {
            "name": collection,
            "fields": [
                {"name": "id", "type": "string"},
                {"name": "content", "type": "string", "sort": True},
                {
                    "name": "vector",
                    "type": "float[]",
                    "num_dim": self.embedding_dimensions,
                },
                {"name": "category", "type": "string"},
                {"name": "subcategory", "type": "list"},
                {"name": "tags", "type": "list"},
                {"name": "metadata", "type": "object", "optional": True},
            ],
            "default_sorting_field": "content",
            "enable_nested_fields": True,
        }

        try:
            vectorstore._client.collections.create(schema)
            vectorstore._collections[collection] = True
            logger.debug(f"Created Typesense collection: {collection}")
        except Exception as e:
            logger.error(f"Failed to create collection {collection}: {e}", exc_info=True)
            raise


    async def execute(self, visitor: "InteractWalker") -> None:
        responses = visitor.interview_session.responses
        if responses:
            # Load persisted subcategories into enum if valid
            if 'available_subcategories' in responses:
                self.question_index[1]['constraints']['enum'] = responses['available_subcategories']

        print("--- Executing ServiceDirectoryAction ----")
        await super().execute(visitor)
        """Execute action: Search for services and present to user."""
        interaction = visitor.interaction
        if not interaction:
            return

        responses = visitor.interview_session.responses
        if not responses:
            return

        if "other_details" in responses:
            query = responses["other_details"]
        # Simple logic: If we have an interpretation/utterance, search for services
        else:
            query = interaction.interpretation or interaction.utterance
        if not query:
            return

        print("\033[92m question index", self.question_index[1], "\033[0m")

        # Extract filters using LLM
        # responses = await self._extract_filters(interaction.utterance)
        logger.info(f"ServiceDirectoryAction: Extracted responses: {responses}")

        if responses:
            self.responses.update(responses)


        # If we have an "other_details" field, treat it as part of the query
        if "other_details" in responses:
             # Append to query or use it?
             # For simpler logic, we keep query as the user utterance
             pass


        print("\033[91mhas responses", responses, "\033[0m")

        try:
            vectorstore = await self._get_vectorstore_action()
            if not vectorstore:
                logger.warning("ServiceDirectoryAction: VectorStore not found, cannot search.")
                await self.publish(visitor, "I'm sorry, I cannot access the directory at the moment.")
                return

            collection = await vectorstore._resolve_collection_name(self.collection)

            # Prepare filters from responses
            search_filters = {}
            if self.responses:
                # Only include fields that are actual metadata filters
                allowed_filters = {"category", "subcategory", "tags"}
                for key, value in self.responses.items():
                    if key in allowed_filters and value:
                        search_filters[key] = value

            # DEBUG: Check collection stats to see how many valid docs exist
            try:
                # if hasattr(vectorstore, "_client") and vectorstore._client:
                col_info = vectorstore._client.collections[collection].retrieve()
                print(f"[DEBUG] Typesense Collection '{collection}' Stats: {col_info}")
                print(f"[DEBUG] Num Documents: {col_info.get('num_documents', 'Unknown')}")
            except Exception as e:
                print(f"[DEBUG] Error getting collection stats: {e}")

            # 1. Search with filters
            results = await vectorstore.search(
                collection=collection,
                query=query,
                k=self.k,
                filters=search_filters,
            )

            # 2. Filter matches
            matches = []
            seen_ids = set()

            if results:
                logger.debug(f"ServiceDirectoryAction: Found {len(results)} results for query '{query}'.")
                for r in results:
                    doc_id = r.get("id")
                    if r.get("score", 0.0) >= self.min_score_threshold:
                         matches.append(r)
                         seen_ids.add(doc_id)

            # 3. Fallback: If not enough matches, search without filters
            if len(matches) < self.k:
                logger.info(f"ServiceDirectoryAction: Only found {len(matches)} matches. broadening search...")
                remaining_k = self.k * 2 # Request more to ensure we filter dups and get enough

                fallback_results = await vectorstore.search(
                    collection=collection,
                    query=query,
                    k=remaining_k,
                )

                if fallback_results:
                    # Use a lower threshold for fallback results to ensure we fill quotas
                    new_matches = []
                    for r in fallback_results:
                        doc_id = r.get("id")
                        if doc_id in seen_ids:
                            continue
                        # Relaxed threshold
                        if r.get("score", 0.0) >= 0.05:
                            seen_ids.add(doc_id)
                            matches.append(r)

            # Trim to k
            matches = matches[:self.k]

            # 3. Formulate Response
            print("\033[91mhas matches", matches, "\033[0m")
            if matches:
                # Present top match + list others
                # For this interactive step, we can use the LLM (Persona) to present it nicely,
                # OR we can construct a direct response.
                # Let's use Persona with a directive for a natural conversation.


                match_descriptions = []
                for m in matches:
                    meta = m.get("metadata", {})
                    name = meta.get("service_name", "Unknown")
                    desc = meta.get("description", "")
                    conn = meta.get("connection_info", "")
                    score = m.get("score", 0.0)
                    match_descriptions.append(
                        f"- **{name}** ({meta.get('category', 'Service')}): {desc}\n"
                        f"  *Connection*: {conn} (Score: {score:.2f})"
                    )

                matches_text = "\n".join(match_descriptions)

                if len(matches) > 1:
                    # Sort matches by score
                    categories = []
                    for m in matches:
                        category = m.get("metadata", {}).get("subcategory")
                        if category:
                            for cat in category:
                                if cat not in categories and cat not in self.responses.get('subcategory', []):
                                    categories.append(cat)
                        continue

                    # categories = {m.get("metadata", {}).get("subcategory") for m in matches}
                    has_multiple_categories = len(categories) > 2
                    print("\033[91mhas_multiple_categories\033[0m", categories)

                    # Store persistently
                    visitor.interview_session.set_response('available_subcategories', list(categories))
                    await visitor.interview_session.save()
                    self.responses['available_subcategories'] = categories
                    # Update current index
                    self.question_index[1]['constraints']['enum'] = categories

                    print("\033[91mself.question_index\033[0m", self.question_index[1])
                    if has_multiple_categories:
                        directive = (
                            f"Look at the following subcategories to determine key differences between services.\n"
                            f"{categories}\n\n"
                            f"Arrange the subcategories into general topics and select the most appropriate topic to present to the user eg location, type of service, type of product."
                            f"Ask the user a concise clarifying question to get more information about which service would be most appropriate based on the topic."
                        )
                        await visitor.add_directive(directive)
                        return

                    matches.sort(key=lambda x: x.get("score", 0.0), reverse=True)
                    directive = (
                        f"Several macthes have been found for '{query}'.\n"
                        f"The Directory Search returned the following relevant services:\n\n"
                        f"{matches_text}\n\n"
                        f"Do not select a service automatically. Check for key differences between services. "
                        f"Ask the user a clarifying question to determine which service would be most appropriate based on the key differences."
                    )
                    await visitor.add_directive(directive)
                    return

                directive = (
                    f"You are a Directory Assistant. The user searched for '{query}'.\n"
                    f"The Directory Search returned the following relevant services:\n\n"
                    f"{matches_text}\n\n"
                    f"Present these options to the user politely. "
                    f"If the user explicitly asked to go to one, confirm and provide the connection info."
                    f"If they were just searching, ask which one they would like to connect to."
                )

                await visitor.add_directive(directive)

            else:
                await visitor.add_directive(
                    f"The user searched for '{query}' but found no matching services in the directory. "
                    "Apologize and ask if they can rephrase or look for something else."
                )

        except Exception as e:
            logger.error(f"ServiceDirectoryAction: Error during search: {e}", exc_info=True)
            await self.publish(visitor, "An error occurred while searching the directory.")

    async def _get_vectorstore_action(self) -> Optional[VectorStore]:
        """Get the VectorStore action."""
        if self.vectorstore_action_type:
            vectorstore = await self.get_action(self.vectorstore_action_type)
            if vectorstore:
                return vectorstore
        return await self.get_action(VectorStore)

# @input_validator('category')
# def validate_category(value: str, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]:
#     """Validate category format and common domains.

#     Args:
#         value: The category string to validate
#         session: Interview session (for context)

#     Returns:
#         Tuple of (ValidationStatus, optional error message)
#     """
#     if not value or not isinstance(value, str):
#         return ValidationStatus.INVALID, "Ask: Please provide a valid category"

#     print("\033[91m question index value\033[0m", value)
#     category = value.strip().lower()

#     # Check for common invalid domains
#     invalid_domains = ['example.com', 'test.com', 'invalid.com']
#     domain = category.split('@')[1] if '@' in category else ''
#     if domain in invalid_domains:
#         return ValidationStatus.INVALID, "Tell the user: Please provide a real email address, not a test domain"

#     # Check for common email providers or valid domain structure
#     if len(domain.split('.')) < 2:
#         return ValidationStatus.INVALID, "Tell the user: Email domain appears to be invalid"

#     return ValidationStatus.VALID, "Tell the user: Is there anything else you would like to know?"
