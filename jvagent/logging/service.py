"""Logging service for interaction logging."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from jvspatial.db import get_database_manager
from jvspatial.core.context import GraphContext

from jvagent.logging.models import InteractionLog, ErrorLog
from jvagent.core.app import App
from jvagent.memory.interaction import Interaction

logger = logging.getLogger(__name__)


class LoggingService:
    """Service for logging interactions to a separate database."""

    def __init__(self):
        """Initialize the logging service."""
        self._log_db = None

    def _get_log_database(self):
        """Get the logging database instance.

        Returns:
            Database instance or None if logging is disabled
        """
        if self._log_db is None:
            try:
                manager = get_database_manager()
                registered_dbs = manager.list_databases()
                logger.debug(f"Registered databases: {registered_dbs}")
                
                if "logs" in registered_dbs:
                    self._log_db = manager.get_database("logs")
                    logger.debug(f"Retrieved logging database: {type(self._log_db).__name__}")
                else:
                    logger.warning(f"Logging database 'logs' not found in registered databases: {registered_dbs}")
                    return None
            except Exception as e:
                logger.error(f"Failed to get logging database: {e}", exc_info=True)
                return None
        return self._log_db

    async def _is_logging_enabled(self, app_id: str) -> bool:
        """Check if logging is enabled for the application.

        Args:
            app_id: Application node ID

        Returns:
            True if logging is enabled, False otherwise
        """
        try:
            # Check global config first
            from jvagent.logging.config import get_logging_config
            config = get_logging_config()
            if not config.get("enabled", True):
                logger.debug("Logging disabled in global config")
                return False

            # Check app-level setting
            app = await App.get()
            if app:
                # Verify app ID matches (should always match, but check anyway)
                if app.id == app_id:
                    enabled = app.logging_enabled
                    logger.debug(f"App-level logging enabled: {enabled} for app {app_id}")
                    return enabled
                else:
                    logger.warning(f"App ID mismatch: expected {app_id}, got {app.id}")
            
            # If app not found, default to enabled (logging should work even if app lookup fails)
            logger.debug(f"App not found, defaulting to logging enabled for app {app_id}")
            return True
        except Exception as e:
            logger.warning(f"Error checking logging enabled: {e}", exc_info=True)
            return True  # Default to enabled on error

    async def log_interaction(
        self, interaction: Interaction, app_id: str, agent_id: Optional[str] = None
    ) -> None:
        """Log a completed interaction asynchronously.

        This method is non-blocking and should be called after the interaction
        response has been sent to the user.

        Args:
            interaction: Completed interaction instance
            app_id: Application node ID
            agent_id: Optional agent node ID. If not provided, will be retrieved from interaction.
        """
        try:
            logger.debug(f"Attempting to log interaction {interaction.id} for app {app_id}")
            
            # Check if logging is enabled
            is_enabled = await self._is_logging_enabled(app_id)
            if not is_enabled:
                logger.debug(f"Logging is disabled for app {app_id}, skipping log entry")
                return

            # Get logging database
            log_db = self._get_log_database()
            if not log_db:
                logger.warning("Logging database not available, skipping log entry")
                return

            logger.debug(f"Logging database retrieved: {type(log_db).__name__}")

            # Export interaction data
            interaction_data = await interaction.export()
            logger.debug(f"Exported interaction data for {interaction.id}")

            # Get agent_id if not provided
            resolved_agent_id = agent_id
            if not resolved_agent_id:
                try:
                    agent = await interaction.get_agent()
                    if agent:
                        resolved_agent_id = agent.id
                        logger.debug(f"Retrieved agent_id {resolved_agent_id} from interaction")
                except Exception as e:
                    logger.warning(f"Failed to get agent from interaction: {e}")

            # Create log entry
            log_entry = InteractionLog(
                app_id=app_id,
                agent_id=resolved_agent_id or "",
                interaction_id=interaction.id,
                conversation_id=interaction.conversation_id,
                session_id=interaction.session_id,
                user_id=interaction.user_id,
                logged_at=datetime.now(timezone.utc),
                interaction_data=interaction_data,
            )
            logger.debug(f"Created log entry with ID {log_entry.id}")

            # Save to logging database using separate context
            log_context = GraphContext(database=log_db)
            # Ensure indexes are created before saving
            await log_context.ensure_indexes(InteractionLog)
            logger.debug("Ensured indexes for InteractionLog")
            
            # Set context on the log entry so it uses the logging database
            await log_entry.set_context(log_context)
            await log_entry.save()
            logger.info(f"Successfully logged interaction {interaction.id} to logging database")

        except Exception as e:
            # Log error but don't fail - logging should never break the main flow
            logger.error(f"Failed to log interaction {interaction.id}: {e}", exc_info=True)

    async def get_logs(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        session_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """Query logs with filters and pagination.

        Args:
            agent_id: Agent node ID (required)
            user_id: Optional user ID filter
            conversation_id: Optional conversation ID filter
            session_id: Optional session ID filter
            start_time: Optional start time filter
            end_time: Optional end time filter
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Dictionary with logs grouped by conversation and pagination metadata
        """
        log_db = self._get_log_database()
        if not log_db:
            return {"conversations": [], "pagination": {"page": page, "page_size": page_size, "total": 0}}

        try:
            # Build query - require agent_id and entity type
            query: Dict[str, Any] = {
                "entity": "InteractionLog",
                "context.agent_id": agent_id,
            }

            if user_id:
                query["context.user_id"] = user_id
            if conversation_id:
                query["context.conversation_id"] = conversation_id
            if session_id:
                query["context.session_id"] = session_id
            # Handle datetime filters
            if start_time or end_time:
                logged_at_filter: Dict[str, Any] = {}
                if start_time:
                    logged_at_filter["$gte"] = start_time.isoformat()
                if end_time:
                    logged_at_filter["$lte"] = end_time.isoformat()
                query["context.logged_at"] = logged_at_filter

            # Query logs
            log_context = GraphContext(database=log_db)
            all_logs = await log_context.database.find("object", query)

            # Convert to InteractionLog objects and sort by logged_at descending
            log_entries: List[InteractionLog] = []
            for log_data in all_logs:
                try:
                    context_data = log_data.get("context", {}).copy()
                    log_id = log_data.get("id", "")
                    
                    # Parse logged_at if it's a string
                    if "logged_at" in context_data and isinstance(context_data["logged_at"], str):
                        try:
                            context_data["logged_at"] = datetime.fromisoformat(
                                context_data["logged_at"].replace("Z", "+00:00")
                            )
                        except (ValueError, AttributeError):
                            # If parsing fails, try to keep original or use current time
                            logger.warning(f"Failed to parse logged_at: {context_data.get('logged_at')}")
                            context_data["logged_at"] = datetime.now(timezone.utc)
                    
                    # Create InteractionLog with id passed during initialization (required for protected attribute)
                    log_entry = InteractionLog(id=log_id, **context_data)
                    log_entries.append(log_entry)
                except Exception as e:
                    logger.warning(f"Failed to parse log entry {log_data.get('id', 'unknown')}: {e}")
                    continue

            # Sort by logged_at descending (most recent first)
            log_entries.sort(key=lambda x: x.logged_at, reverse=True)

            # Group by conversation_id
            conversations: Dict[str, List[InteractionLog]] = {}
            for log_entry in log_entries:
                conv_id = log_entry.conversation_id or "unknown"
                if conv_id not in conversations:
                    conversations[conv_id] = []
                conversations[conv_id].append(log_entry)

            # Sort conversations by most recent interaction
            sorted_conversations = sorted(
                conversations.items(),
                key=lambda x: max(log.logged_at for log in x[1]),
                reverse=True,
            )

            # Sort interactions within each conversation chronologically
            for conv_id, logs in sorted_conversations:
                logs.sort(key=lambda x: x.logged_at)

            # Paginate conversations
            total_conversations = len(sorted_conversations)
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            paginated_conversations = sorted_conversations[start_idx:end_idx]

            # Format response
            result_conversations = []
            for conv_id, logs in paginated_conversations:
                result_conversations.append({
                    "conversation_id": conv_id,
                    "interactions": [
                        {
                            "log_id": log.id,
                            "interaction_id": log.interaction_id,
                            "logged_at": log.logged_at.isoformat(),
                            "interaction_data": log.interaction_data,
                        }
                        for log in logs
                    ],
                })

            return {
                "conversations": result_conversations,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total_conversations,
                    "total_pages": (total_conversations + page_size - 1) // page_size,
                },
            }

        except Exception as e:
            logger.error(f"Failed to get logs: {e}", exc_info=True)
            return {"conversations": [], "pagination": {"page": page, "page_size": page_size, "total": 0}}

    async def purge_logs(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Purge logs matching criteria.

        Args:
            agent_id: Agent node ID (required)
            user_id: Optional user ID filter
            conversation_id: Optional conversation ID filter
            start_time: Optional start time filter
            end_time: Optional end time filter

        Returns:
            Dictionary with purge statistics
        """
        log_db = self._get_log_database()
        if not log_db:
            return {"deleted": 0, "error": "Logging database not available"}

        try:
            # Build query - require agent_id and entity type
            query: Dict[str, Any] = {
                "entity": "InteractionLog",
                "context.agent_id": agent_id,
            }

            if user_id:
                query["context.user_id"] = user_id
            if conversation_id:
                query["context.conversation_id"] = conversation_id
            # Handle datetime filters
            if start_time or end_time:
                logged_at_filter: Dict[str, Any] = {}
                if start_time:
                    logged_at_filter["$gte"] = start_time.isoformat()
                if end_time:
                    logged_at_filter["$lte"] = end_time.isoformat()
                query["context.logged_at"] = logged_at_filter

            # Find matching logs
            log_context = GraphContext(database=log_db)
            matching_logs = await log_context.database.find("object", query)

            # Delete each log
            deleted_count = 0
            for log_data in matching_logs:
                try:
                    log_id = log_data.get("id")
                    if log_id:
                        await log_context.database.delete("object", log_id)
                        deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete log {log_data.get('id')}: {e}")

            return {"deleted": deleted_count}

        except Exception as e:
            logger.error(f"Failed to purge logs: {e}", exc_info=True)
            return {"deleted": 0, "error": str(e)}

    async def log_error(
        self,
        error_data: Dict[str, Any],
        app_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        path: Optional[str] = None,
        method: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        interaction_id: Optional[str] = None,
    ) -> None:
        """Log an error asynchronously.

        This method is non-blocking and should be called when an error occurs.
        It extracts app_id and agent_id if not provided, and stores comprehensive
        error information for debugging and traceability.

        Args:
            error_data: Dictionary containing error-specific information:
                - message: Error message (required)
                - details: Additional error details (optional)
                - traceback: Traceback string (optional, for 5xx errors)
            app_id: Optional application node ID. If not provided, will be retrieved from App.get().
            agent_id: Optional agent node ID. If not provided, will be extracted from path or context.
            status_code: HTTP status code (extracted from error_data if not provided)
            error_code: Machine-readable error code (extracted from error_data if not provided)
            path: Request path (extracted from error_data if not provided)
            method: HTTP method (extracted from error_data if not provided)
            user_id: User ID (extracted from error_data if not provided)
            session_id: Session ID (extracted from error_data if not provided)
            interaction_id: Interaction ID (extracted from error_data if not provided)
        """
        try:
            # Extract fields from parameters or error_data (for backward compatibility)
            status_code = status_code or error_data.get("status_code", 500)
            error_code = error_code or error_data.get("error_code", "internal_error")
            path = path or error_data.get("path", "")
            method = method or error_data.get("method", "")
            user_id = user_id or error_data.get("user_id") or ""
            session_id = session_id or error_data.get("session_id") or ""
            interaction_id = interaction_id or error_data.get("interaction_id") or ""
            
            logger.debug(f"Attempting to log error {error_code} (status {status_code}) at {path}")
            
            # Get app_id if not provided
            resolved_app_id = app_id
            if not resolved_app_id:
                try:
                    app = await App.get()
                    if app:
                        resolved_app_id = app.id
                        logger.debug(f"Retrieved app_id {resolved_app_id} from App.get()")
                except Exception as e:
                    logger.warning(f"Failed to get app_id: {e}")
            
            if not resolved_app_id:
                logger.debug("No app_id available, skipping error log entry")
                return
            
            # Check if logging is enabled
            is_enabled = await self._is_logging_enabled(resolved_app_id)
            if not is_enabled:
                logger.debug(f"Logging is disabled for app {resolved_app_id}, skipping error log entry")
                return

            # Get logging database
            log_db = self._get_log_database()
            if not log_db:
                logger.warning("Logging database not available, skipping error log entry")
                return

            logger.debug(f"Logging database retrieved: {type(log_db).__name__}")

            # Create log entry
            log_entry = ErrorLog(
                app_id=resolved_app_id,
                agent_id=agent_id or "",
                user_id=user_id,
                session_id=session_id,
                interaction_id=interaction_id,
                status_code=status_code,
                error_code=error_code,
                path=path,
                method=method,
                logged_at=datetime.now(timezone.utc),
                error_data=error_data,
            )
            logger.debug(f"Created error log entry with ID {log_entry.id}")

            # Save to logging database using separate context
            log_context = GraphContext(database=log_db)
            # Ensure indexes are created before saving
            await log_context.ensure_indexes(ErrorLog)
            logger.debug("Ensured indexes for ErrorLog")
            
            # Set context on the log entry so it uses the logging database
            await log_entry.set_context(log_context)
            await log_entry.save()
            logger.info(f"Successfully logged error {error_code} (status {status_code}) to logging database")

        except Exception as e:
            # Log error but don't fail - logging should never break the main flow
            logger.error(f"Failed to log error: {e}", exc_info=True)

    async def get_error_logs(
        self,
        app_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        error_code: Optional[str] = None,
        status_code: Optional[int] = None,
        user_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """Query error logs with filters and pagination.

        Args:
            app_id: Optional application node ID filter
            agent_id: Optional agent node ID filter
            error_code: Optional error code filter
            status_code: Optional status code filter
            user_id: Optional user ID filter
            start_time: Optional start time filter
            end_time: Optional end time filter
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Dictionary with error logs and pagination metadata
        """
        log_db = self._get_log_database()
        if not log_db:
            return {"errors": [], "pagination": {"page": page, "page_size": page_size, "total": 0}}

        try:
            # Build query - require entity type
            query: Dict[str, Any] = {
                "entity": "ErrorLog",
            }

            if app_id:
                query["context.app_id"] = app_id
            if agent_id:
                query["context.agent_id"] = agent_id
            if error_code:
                query["context.error_code"] = error_code
            if status_code:
                query["context.status_code"] = status_code
            if user_id:
                query["context.user_id"] = user_id
            # Handle datetime filters
            if start_time or end_time:
                logged_at_filter: Dict[str, Any] = {}
                if start_time:
                    logged_at_filter["$gte"] = start_time.isoformat()
                if end_time:
                    logged_at_filter["$lte"] = end_time.isoformat()
                query["context.logged_at"] = logged_at_filter

            # Query logs
            log_context = GraphContext(database=log_db)
            all_logs = await log_context.database.find("object", query)

            # Convert to ErrorLog objects and sort by logged_at descending
            log_entries: List[ErrorLog] = []
            for log_data in all_logs:
                try:
                    context_data = log_data.get("context", {}).copy()
                    log_id = log_data.get("id", "")
                    
                    # Parse logged_at if it's a string
                    if "logged_at" in context_data and isinstance(context_data["logged_at"], str):
                        try:
                            context_data["logged_at"] = datetime.fromisoformat(
                                context_data["logged_at"].replace("Z", "+00:00")
                            )
                        except (ValueError, AttributeError):
                            logger.warning(f"Failed to parse logged_at: {context_data.get('logged_at')}")
                            context_data["logged_at"] = datetime.now(timezone.utc)
                    
                    # Create ErrorLog with id passed during initialization
                    log_entry = ErrorLog(id=log_id, **context_data)
                    log_entries.append(log_entry)
                except Exception as e:
                    logger.warning(f"Failed to parse error log entry {log_data.get('id', 'unknown')}: {e}")
                    continue

            # Sort by logged_at descending (most recent first)
            log_entries.sort(key=lambda x: x.logged_at, reverse=True)

            # Paginate
            total_errors = len(log_entries)
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            paginated_errors = log_entries[start_idx:end_idx]

            # Format response
            result_errors = []
            for log in paginated_errors:
                result_errors.append({
                    "log_id": log.id,
                    "status_code": log.status_code,
                    "error_code": log.error_code,
                    "message": log.error_data.get("message", ""),
                    "path": log.path,
                    "method": log.method,
                    "app_id": log.app_id,
                    "agent_id": log.agent_id,
                    "user_id": log.user_id,
                    "session_id": log.session_id,
                    "interaction_id": log.interaction_id,
                    "logged_at": log.logged_at.isoformat(),
                    "error_data": log.error_data,
                })

            return {
                "errors": result_errors,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total_errors,
                    "total_pages": (total_errors + page_size - 1) // page_size,
                },
            }

        except Exception as e:
            logger.error(f"Failed to get error logs: {e}", exc_info=True)
            return {"errors": [], "pagination": {"page": page, "page_size": page_size, "total": 0}}

    async def purge_error_logs(
        self,
        app_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        error_code: Optional[str] = None,
        status_code: Optional[int] = None,
        user_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Purge error logs matching criteria.

        Args:
            app_id: Optional application node ID filter
            agent_id: Optional agent node ID filter
            error_code: Optional error code filter
            status_code: Optional status code filter
            user_id: Optional user ID filter
            start_time: Optional start time filter
            end_time: Optional end time filter

        Returns:
            Dictionary with purge statistics
        """
        log_db = self._get_log_database()
        if not log_db:
            return {"deleted": 0, "error": "Logging database not available"}

        try:
            # Build query - require entity type
            query: Dict[str, Any] = {
                "entity": "ErrorLog",
            }

            if app_id:
                query["context.app_id"] = app_id
            if agent_id:
                query["context.agent_id"] = agent_id
            if error_code:
                query["context.error_code"] = error_code
            if status_code:
                query["context.status_code"] = status_code
            if user_id:
                query["context.user_id"] = user_id
            # Handle datetime filters
            if start_time or end_time:
                logged_at_filter: Dict[str, Any] = {}
                if start_time:
                    logged_at_filter["$gte"] = start_time.isoformat()
                if end_time:
                    logged_at_filter["$lte"] = end_time.isoformat()
                query["context.logged_at"] = logged_at_filter

            # Find matching logs
            log_context = GraphContext(database=log_db)
            matching_logs = await log_context.database.find("object", query)

            # Delete each log
            deleted_count = 0
            for log_data in matching_logs:
                try:
                    log_id = log_data.get("id")
                    if log_id:
                        await log_context.database.delete("object", log_id)
                        deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete error log {log_data.get('id')}: {e}")

            return {"deleted": deleted_count}

        except Exception as e:
            logger.error(f"Failed to purge error logs: {e}", exc_info=True)
            return {"deleted": 0, "error": str(e)}

    async def apply_retention_policy(self, agent_id: str) -> Dict[str, Any]:
        """Apply retention policy for an agent.

        Args:
            agent_id: Agent node ID

        Returns:
            Dictionary with retention statistics
        """
        try:
            # Get retention days from app (agents belong to apps)
            retention_days = 60  # Default
            app = await App.get()
            if app:
                retention_days = app.log_retention_days

            # Calculate cutoff time
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=retention_days)

            # Purge interaction logs older than cutoff
            interaction_result = await self.purge_logs(agent_id=agent_id, end_time=cutoff_time)
            
            # Purge error logs older than cutoff
            error_result = await self.purge_error_logs(agent_id=agent_id, end_time=cutoff_time)

            return {
                "interaction_logs_deleted": interaction_result.get("deleted", 0),
                "error_logs_deleted": error_result.get("deleted", 0),
                "total_deleted": interaction_result.get("deleted", 0) + error_result.get("deleted", 0),
            }

        except Exception as e:
            logger.error(f"Failed to apply retention policy: {e}", exc_info=True)
            return {"deleted": 0, "error": str(e)}


# Singleton instance
_logging_service: Optional[LoggingService] = None


def get_logging_service() -> LoggingService:
    """Get the singleton logging service instance.

    Returns:
        LoggingService instance
    """
    global _logging_service
    if _logging_service is None:
        _logging_service = LoggingService()
    return _logging_service

