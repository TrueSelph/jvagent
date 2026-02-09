"""Question node and branch function caching utilities.

Centralized caching logic for QuestionNode instances and branch function
results to avoid duplicate implementations and optimize performance.
"""

import hashlib
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from .constants import CACHE_KEY_QUESTION_NODES, CACHE_KEY_BRANCH_FUNCTIONS, CACHE_KEY_BRANCH_PATHS, CACHE_KEY_PRUNED_RESPONSES

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


class BranchFunctionCache:
    """Cache for branch function execution results and dependency tracking.
    
    Stores computed results from branch function executions and tracks which
    response keys (questions) were accessed during execution. Enables:
    - Avoiding redundant function executions when dependencies unchanged
    - Smart invalidation when responses change
    - Intelligent response pruning when branching path changes
    
    Cache is stored in session.context for persistence across interactions.
    """
    
    def __init__(self, session: "InterviewSession"):
        """Initialize branch function cache with session.
        
        Args:
            session: Interview session containing cache in context
        """
        self.session = session
        if session.context is None:
            session.context = {}
        
        # Initialize cache dict if not present
        if CACHE_KEY_BRANCH_FUNCTIONS not in session.context:
            session.context[CACHE_KEY_BRANCH_FUNCTIONS] = {}
        
        # Initialize branch paths cache if not present
        if CACHE_KEY_BRANCH_PATHS not in session.context:
            session.context[CACHE_KEY_BRANCH_PATHS] = {}
        
        # Initialize pruned responses tracking if not present
        if CACHE_KEY_PRUNED_RESPONSES not in session.context:
            session.context[CACHE_KEY_PRUNED_RESPONSES] = {}
    
    def _make_cache_key(
        self,
        question_name: str,
        condition: Dict[str, Any],
        function_name: Optional[str] = None
    ) -> str:
        """Generate a unique cache key for a branch function condition.
        
        Cache key includes:
        - Question name (implicit question for branch)
        - Condition dict (hashified to avoid long keys)
        - Function name if provided
        
        Args:
            question_name: Name of the question
            condition: Condition dictionary
            function_name: Optional function name
            
        Returns:
            Unique cache key string
        """
        # Create string representation of condition
        condition_str = str(sorted(condition.items()))
        condition_hash = hashlib.md5(condition_str.encode()).hexdigest()[:8]
        
        if function_name:
            return f"{question_name}:{function_name}:{condition_hash}"
        else:
            return f"{question_name}:{condition_hash}"
    
    def get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Get cached result for a branch function condition.
        
        Args:
            cache_key: Cache key from _make_cache_key
            
        Returns:
            Cached result dict if found and valid, None otherwise.
            Result dict contains:
            - 'result': The cached return value
            - 'dependencies': Set of response keys accessed
            - 'timestamp': When result was cached
        """
        cache = self.session.context.get(CACHE_KEY_BRANCH_FUNCTIONS, {})
        entry = cache.get(cache_key)
        
        if not entry:
            return None
        
        # Check if dependencies have changed since cache was created
        dependencies: Set[str] = set(entry.get("dependencies", []))
        if dependencies:
            # Get current values of all dependencies
            current_values = {k: self.session.responses.get(k) for k in dependencies}
            cached_values = entry.get("dependency_values", {})
            
            # If any dependency changed, cache is invalid
            if current_values != cached_values:
                logger.debug(
                    f"Branch cache miss for key '{cache_key}': dependencies changed. "
                    f"Previous: {cached_values}, Current: {current_values}"
                )
                return None
        
        logger.debug(f"Branch cache hit for key '{cache_key}'")
        return entry
    
    def set(
        self,
        cache_key: str,
        result: Any,
        dependencies: Set[str]
    ) -> None:
        """Cache a branch function result with dependency tracking.
        
        Args:
            cache_key: Cache key from _make_cache_key
            result: The return value from the branch function
            dependencies: Set of response keys (question names) that were accessed
        """
        cache = self.session.context.get(CACHE_KEY_BRANCH_FUNCTIONS, {})
        
        # Store current values of dependencies for invalidation detection
        dependency_values = {k: self.session.responses.get(k) for k in dependencies}
        
        cache[cache_key] = {
            "result": result,
            "dependencies": list(dependencies),
            "dependency_values": dependency_values,
            "timestamp": self.session.last_modified if hasattr(self.session, 'last_modified') else None
        }
        
        self.session.context[CACHE_KEY_BRANCH_FUNCTIONS] = cache
        logger.debug(
            f"Branch cache set for key '{cache_key}' with dependencies: {dependencies}"
        )
    
    def invalidate_by_response(self, response_key: str) -> List[str]:
        """Invalidate all cached branches that depend on a response.
        
        Args:
            response_key: Response key (question name) that was updated
            
        Returns:
            List of cache keys that were invalidated
        """
        cache = self.session.context.get(CACHE_KEY_BRANCH_FUNCTIONS, {})
        invalidated = []
        
        keys_to_remove = []
        for cache_key, entry in cache.items():
            dependencies = set(entry.get("dependencies", []))
            if response_key in dependencies:
                keys_to_remove.append(cache_key)
                invalidated.append(cache_key)
        
        for key in keys_to_remove:
            cache.pop(key, None)
        
        self.session.context[CACHE_KEY_BRANCH_FUNCTIONS] = cache
        
        if invalidated:
            logger.debug(
                f"Branch cache invalidated {len(invalidated)} entries due to response change: {response_key}. "
                f"Invalidated keys: {invalidated}"
            )
        
        return invalidated
    
    def invalidate_all(self) -> None:
        """Invalidate all cached branch results.
        
        Called when session is reset or when comprehensive re-evaluation needed.
        """
        self.session.context[CACHE_KEY_BRANCH_FUNCTIONS] = {}
        logger.debug("Branch function cache cleared")
    
    def record_branch_path(
        self,
        question_name: str,
        condition_index: int,
        target: str,
        is_default: bool = False
    ) -> None:
        """Record which branch was taken for a question.
        
        Enables detection of path changes when branches re-evaluated.
        
        Args:
            question_name: Name of the question with branches
            condition_index: Index of the condition that matched (0-based)
            target: Target question or state node name
            is_default: Whether this was the default branch
        """
        paths = self.session.context.get(CACHE_KEY_BRANCH_PATHS, {})
        paths[question_name] = {
            "condition_index": condition_index,
            "target": target,
            "is_default": is_default
        }
        self.session.context[CACHE_KEY_BRANCH_PATHS] = paths
        logger.debug(
            f"Recorded branch path for '{question_name}': target='{target}' "
            f"(condition_index={condition_index}, is_default={is_default})"
        )
    
    def get_previous_path(self, question_name: str) -> Optional[Dict[str, Any]]:
        """Get previously recorded branch path for a question.
        
        Args:
            question_name: Name of the question
            
        Returns:
            Previous path dict if found, None otherwise
        """
        paths = self.session.context.get(CACHE_KEY_BRANCH_PATHS, {})
        return paths.get(question_name)
    
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
