"""Question node caching utilities.

Centralized caching logic for QuestionNode instances to avoid duplicate
implementations across question_walker.py and interview_interact_action.py.
"""

import logging
from typing import TYPE_CHECKING, Dict, Optional

from .constants import CACHE_KEY_QUESTION_NODES

if TYPE_CHECKING:
    from ..interview_session import InterviewSession
    from ..question_node import QuestionNode

logger = logging.getLogger(__name__)


class QuestionNodeCache:
    """Cache for QuestionNode instances.
    
    Provides centralized caching logic to avoid duplicate implementations.
    Cache is stored in session.context for persistence across interactions.
    """
    
    def __init__(self, session: "InterviewSession"):
        """Initialize cache with session.
        
        Args:
            session: Interview session containing cache in context
        """
        self.session = session
        if session.context is None:
            session.context = {}
        
        # Initialize cache dict if not present
        if CACHE_KEY_QUESTION_NODES not in session.context:
            session.context[CACHE_KEY_QUESTION_NODES] = {}
    
    def get_cache_dict(self) -> Dict[str, str]:
        """Get the cache dictionary from session context.
        
        Returns:
            Dictionary mapping question names to node IDs
        """
        return self.session.context.get(CACHE_KEY_QUESTION_NODES, {})
    
    def get(self, question_name: str) -> Optional[str]:
        """Get cached node ID for a question.
        
        Args:
            question_name: Name of the question
            
        Returns:
            Cached node ID if found, None otherwise
        """
        cache = self.get_cache_dict()
        return cache.get(question_name)
    
    def set(self, question_name: str, node_id: str) -> None:
        """Cache a node ID for a question.
        
        Args:
            question_name: Name of the question
            node_id: ID of the QuestionNode
        """
        cache = self.get_cache_dict()
        cache[question_name] = node_id
        self.session.context[CACHE_KEY_QUESTION_NODES] = cache
    
    def invalidate(self, question_name: Optional[str] = None) -> None:
        """Invalidate cache entry or all entries.
        
        Args:
            question_name: Optional specific question to invalidate.
                          If None, clears entire cache.
        """
        cache = self.get_cache_dict()
        if question_name:
            cache.pop(question_name, None)
        else:
            cache.clear()
        self.session.context[CACHE_KEY_QUESTION_NODES] = cache
    
    async def get_cached_node(
        self,
        question_name: str,
        fetch_func: callable
    ) -> Optional["QuestionNode"]:
        """Get cached node or fetch if not cached.
        
        Args:
            question_name: Name of the question
            fetch_func: Async function to fetch node if not cached.
                       Should accept question_name as argument.
                       
        Returns:
            QuestionNode if found, None otherwise
        """
        # Check cache first
        cached_id = self.get(question_name)
        if cached_id:
            try:
                from jvspatial.core import Node
                cached_node = await Node.get(cached_id)
                if cached_node:
                    # Check if it's a QuestionNode
                    from ..question_node import QuestionNode
                    if isinstance(cached_node, QuestionNode):
                        return cached_node
                    else:
                        # Cache entry is stale, remove it
                        self.invalidate(question_name)
            except Exception:
                # Cache entry is invalid, remove it
                self.invalidate(question_name)
        
        # Fetch and cache
        node = await fetch_func(question_name)
        if node:
            self.set(question_name, node.id)
        
        return node
    
    async def get_cached_node_by_id(
        self,
        question_name: str
    ) -> Optional["QuestionNode"]:
        """Get cached node by ID from cache.
        
        Args:
            question_name: Name of the question
            
        Returns:
            QuestionNode if found in cache, None otherwise
        """
        cached_id = self.get(question_name)
        if not cached_id:
            return None
        
        try:
            from jvspatial.core import Node
            cached_node = await Node.get(cached_id)
            if cached_node:
                from ..question_node import QuestionNode
                if isinstance(cached_node, QuestionNode):
                    return cached_node
                else:
                    # Cache entry is stale, remove it
                    self.invalidate(question_name)
        except Exception:
            # Cache entry is invalid, remove it
            self.invalidate(question_name)
        
        return None
