"""Question node and branch caching utilities.

Centralized caching logic for QuestionNode instances and branch resolution
results to avoid duplicate implementations and optimize performance.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from .constants import CACHE_KEY_QUESTION_NODES, CACHE_KEY_BRANCH_CACHE, CACHE_KEY_PRUNED_RESPONSES

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


class BranchCache:
    """Cache for resolved branch targets per question.
    
    Stores one entry per question: question_name -> target (the resolved
    target node name). Enables efficient lookup and targeted invalidation.
    Cache is stored in session.context for persistence across interactions.
    """
    
    def __init__(self, session: "InterviewSession"):
        """Initialize branch cache with session.
        
        Args:
            session: Interview session containing cache in context
        """
        self.session = session
        if session.context is None:
            session.context = {}
        
        if CACHE_KEY_BRANCH_CACHE not in session.context:
            session.context[CACHE_KEY_BRANCH_CACHE] = {}
        
        if CACHE_KEY_PRUNED_RESPONSES not in session.context:
            session.context[CACHE_KEY_PRUNED_RESPONSES] = {}
    
    def get(self, question_name: str) -> Optional[str]:
        """Return cached target for a question, or None.
        
        Args:
            question_name: Name of the question
            
        Returns:
            Resolved target (node name) if cached, None otherwise
        """
        cache = self.session.context.get(CACHE_KEY_BRANCH_CACHE, {})
        return cache.get(question_name)
    
    def set(self, question_name: str, target: str) -> None:
        """Store resolved target for a question.
        
        Args:
            question_name: Name of the question
            target: Target question or state node name
        """
        cache = self.session.context.get(CACHE_KEY_BRANCH_CACHE, {})
        cache[question_name] = target
        self.session.context[CACHE_KEY_BRANCH_CACHE] = cache
        logger.debug(f"Branch cache set for '{question_name}': target='{target}'")
    
    def invalidate(self, question_name: str) -> None:
        """Remove the cache entry for a question (targeted invalidation).
        
        Args:
            question_name: Name of the question to invalidate
        """
        cache = self.session.context.get(CACHE_KEY_BRANCH_CACHE, {})
        if question_name in cache:
            cache.pop(question_name, None)
            self.session.context[CACHE_KEY_BRANCH_CACHE] = cache
            logger.debug(f"Branch cache invalidated for '{question_name}'")
    
    def invalidate_all(self) -> None:
        """Clear the entire branch cache (e.g. on session reset)."""
        self.session.context[CACHE_KEY_BRANCH_CACHE] = {}
        logger.debug("Branch cache cleared")
    
    def record_branch_path(
        self,
        question_name: str,
        condition_index: int,
        target: str,
        is_default: bool = False
    ) -> None:
        """Record which branch was taken for a question.
        
        Only question_name and target are stored; condition_index and
        is_default are accepted for API compatibility but not persisted.
        
        Args:
            question_name: Name of the question with branches
            condition_index: Index of the condition that matched (0-based); ignored
            target: Target question or state node name
            is_default: Whether this was the default branch; ignored
        """
        self.set(question_name, target)
    
    def get_previous_path(self, question_name: str) -> Optional[Dict[str, Any]]:
        """Get previously recorded branch path for a question.
        
        Returns a dict with at least 'target' for compatibility with callers
        that use path['target'].
        
        Args:
            question_name: Name of the question
            
        Returns:
            Dict with 'target' key if cached, None otherwise
        """
        target = self.get(question_name)
        if target is None:
            return None
        return {"target": target}
    
    def record_pruned_response(
        self,
        question_name: str,
        old_value: Any,
        reason: str
    ) -> None:
        """Record a response that was pruned due to path change.
        
        Maintains audit trail of pruned responses for debugging and undo.
        
        Args:
            question_name: Name of the question whose response was pruned
            old_value: The pruned value
            reason: Reason for pruning (e.g., 'branch_path_change')
        """
        pruned = self.session.context.get(CACHE_KEY_PRUNED_RESPONSES, {})
        # Record a snapshot of relevant dependency values to allow safe restoration
        try:
            dependency_snapshot = dict(self.session.responses) if isinstance(self.session.responses, dict) else {}
        except Exception:
            dependency_snapshot = {}

        pruned[question_name] = {
            "value": old_value,
            "reason": reason,
            "pruned_at": self.session.last_modified if hasattr(self.session, 'last_modified') else None,
            "dependency_snapshot": dependency_snapshot,
        }
        self.session.context[CACHE_KEY_PRUNED_RESPONSES] = pruned
        logger.debug(
            f"Recorded pruned response for '{question_name}': value={old_value!r}, reason='{reason}'"
        )
    
    def get_pruned_responses(self) -> Dict[str, Dict[str, Any]]:
        """Get all pruned responses from current session.
        
        Returns:
            Dictionary mapping question names to pruned response data
        """
        return self.session.context.get(CACHE_KEY_PRUNED_RESPONSES, {})
