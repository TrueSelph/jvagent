"""Database logging handler for automatic log persistence."""

import asyncio
import inspect
import logging
import traceback
from typing import Any, Dict, Optional

from jvagent.logging.service import get_logging_service
from jvagent.action.model.context import get_interaction_id, get_calling_action_name


class DBLogHandler(logging.Handler):
    """Logging handler that automatically logs log records to the database.
    
    This handler intercepts all log records and automatically sends them to the
    LoggingService for database persistence. This eliminates the need for explicit
    database logging calls throughout the codebase.
    
    The handler:
    - Always processes ERROR/CRITICAL level logs (if logging enabled)
    - Conditionally processes WARNING/INFO/DEBUG based on log_db_level config
    - Extracts exception information from log records
    - Extracts context information (agent_id, interaction_id, etc.) from log record details,
      call stack, and context variables
    - Asynchronously logs to database (non-blocking)
    - Never raises exceptions (fails silently to avoid breaking main flow)
    
    Attributes:
        _log_db_level: Minimum log level to persist to database (ERROR/CRITICAL always logged)
        _logging_service: Cached reference to LoggingService instance
    """
    
    def __init__(self, log_db_level: int = logging.ERROR):
        """Initialize the database log handler.
        
        Args:
            log_db_level: Minimum log level to persist to database (default: ERROR).
                ERROR/CRITICAL are always logged regardless of this setting.
        """
        # Set handler level to DEBUG to catch all levels (we filter in emit())
        super().__init__(level=logging.DEBUG)
        self._log_db_level = log_db_level
        self._logging_service = None
    
    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to the database.
        
        This method is called by the logging system for each log record that
        meets the handler's level threshold.
        
        Args:
            record: The log record to process
        """
        try:
            # Check if level should be logged
            # ERROR/CRITICAL are always logged (if logging enabled)
            # Other levels only if above configured threshold
            if record.levelno < logging.ERROR:
                if record.levelno < self._log_db_level:
                    # Below threshold, skip database logging but allow other handlers
                    return
            
            # Check global logging configuration
            from jvagent.logging.config import get_logging_config
            config = get_logging_config()
            if not config.get("enabled", True):
                # Logging disabled globally, skip database logging
                return
            
            # Get logging service (lazy initialization)
            if self._logging_service is None:
                self._logging_service = get_logging_service()
            
            # Extract exception information - be thorough in capturing exception details
            exc_info = record.exc_info
            traceback_str = None
            
            # Priority 1: Use exc_info from record if available (set when exc_info=True is passed)
            if exc_info:
                exc_type, exc_value, exc_traceback = exc_info
                traceback_str = "".join(
                    traceback.format_exception(exc_type, exc_value, exc_traceback)
                )
            else:
                # Priority 2: Try to get exception info from sys.exc_info() if available
                # This captures exceptions that were caught but not explicitly logged with exc_info=True
                try:
                    import sys
                    exc_type, exc_value, exc_traceback = sys.exc_info()
                    if exc_type is not None and exc_value is not None:
                        traceback_str = "".join(
                            traceback.format_exception(exc_type, exc_value, exc_traceback)
                        )
                except Exception:
                    # If sys.exc_info() fails, continue without traceback
                    pass
            
            # Priority 3: If still no traceback but we have an exception in the message/details,
            # try to extract exception information from the record
            if not traceback_str and record.levelno >= logging.ERROR:
                # Check if there's exception information in record details
                details = getattr(record, "details", None)
                if details and isinstance(details, dict):
                    # Check for exception object in details
                    for key, value in details.items():
                        if isinstance(value, Exception):
                            try:
                                traceback_str = "".join(
                                    traceback.format_exception(
                                        type(value), value, value.__traceback__
                                    )
                                )
                                break
                            except Exception:
                                pass
                
                # If still no traceback, try to get from record.__dict__
                if not traceback_str:
                    for key, value in record.__dict__.items():
                        if isinstance(value, Exception):
                            try:
                                traceback_str = "".join(
                                    traceback.format_exception(
                                        type(value), value, value.__traceback__
                                    )
                                )
                                break
                            except Exception:
                                pass
            
            # Extract context from multiple sources
            context = self._extract_context(record)
            
            # Get app_id for app-level config check
            app_id = context.get("app_id")
            if not app_id:
                try:
                    from jvagent.core.app import App
                    # Try to get app synchronously (may not work in all contexts)
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            # Can't use asyncio.run() in running loop, skip app check
                            # Will check in async task
                            pass
                        else:
                            app = asyncio.run(App.get())
                            if app:
                                app_id = app.id
                    except RuntimeError:
                        # No event loop, try to get from agent_id
                        pass
                except Exception:
                    pass
            
            # If still no app_id, try to get from agent_id
            if not app_id and context.get("agent_id"):
                try:
                    from jvagent.core.agent import Agent
                    try:
                        loop = asyncio.get_event_loop()
                        if not loop.is_running():
                            agent = asyncio.run(Agent.get(context["agent_id"]))
                            if agent and hasattr(agent, 'app_id'):
                                app_id = agent.app_id
                    except RuntimeError:
                        pass
                except Exception:
                    pass
            
            # Check app-level logging config if we have app_id (app_id is optional)
            # If no app_id, the check will be done in async context
            if app_id:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Can't check synchronously, will check in async task
                        pass
                    else:
                        is_enabled = asyncio.run(self._logging_service._is_logging_enabled(app_id))
                        if not is_enabled:
                            # App-level logging disabled, skip database logging
                            return
                except Exception:
                    # If check fails, continue anyway (fail-safe)
                    pass
            
            # Extract fields from context
            agent_id = context.get("agent_id") or ""
            interaction_id = context.get("interaction_id") or ""
            session_id = context.get("session_id") or ""
            user_id = context.get("user_id") or ""
            error_code = context.get("error_code")
            status_code = context.get("status_code")
            
            # Determine error_code and status_code based on log level if not provided
            if not error_code:
                if record.levelno >= logging.ERROR:
                    error_code = "application_error"
                elif record.levelno == logging.WARNING:
                    error_code = "application_warning"
                elif record.levelno == logging.INFO:
                    error_code = "application_info"
                else:  # DEBUG
                    error_code = "application_debug"
            
            if status_code is None:
                if record.levelno >= logging.ERROR:
                    status_code = 500
                else:
                    status_code = 200  # Non-error status
            
            # Build error data
            error_data: Dict[str, Any] = {
                "message": record.getMessage(),
                "logger_name": record.name,
                "module": record.module,
                "function": record.funcName,
                "line_number": record.lineno,
                "log_level": record.levelname,
            }
            
            # Add traceback if available
            if traceback_str:
                error_data["traceback"] = traceback_str
            
            # Add context fields
            if "action_class" in context:
                error_data["action_class"] = context["action_class"]
            if "action_id" in context:
                error_data["action_id"] = context["action_id"]
            if "action_label" in context:
                error_data["action_label"] = context["action_label"]
            if "context" in context:
                error_data["context"] = context["context"]
            
            # Log to database asynchronously (fire-and-forget)
            # We need app_id for log_error, so check it again in async context
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Event loop is running, create task
                    # Create a coroutine that checks app-level config and logs
                    async def _log_to_db():
                        try:
                            # Get app_id if not already available
                            nonlocal app_id
                            if not app_id:
                                try:
                                    from jvagent.core.app import App
                                    app = await App.get()
                                    if app:
                                        app_id = app.id
                                except Exception:
                                    pass
                            
                            # If still no app_id, try from agent_id
                            if not app_id and agent_id:
                                try:
                                    from jvagent.core.agent import Agent
                                    agent = await Agent.get(agent_id)
                                    if agent and hasattr(agent, 'app_id'):
                                        app_id = agent.app_id
                                except Exception:
                                    pass
                            
                            # Check app-level config (app_id is optional - jvagent has its own log database)
                            is_enabled = await self._logging_service._is_logging_enabled(app_id)
                            if not is_enabled:
                                return  # Skip if disabled
                            
                            # Always attempt to log - app_id is optional
                            # LoggingService will use empty string if app_id is None
                            await self._logging_service.log_error(
                                error_data=error_data,
                                app_id=app_id,  # May be None, will default to empty string
                                agent_id=agent_id or None,
                                status_code=status_code,
                                error_code=error_code,
                                interaction_id=interaction_id or None,
                                session_id=session_id or None,
                                user_id=user_id or None,
                            )
                        except Exception:
                            # Fail silently to avoid breaking main flow
                            pass
                    
                    # Create task with error callback to catch task failures
                    task = asyncio.create_task(_log_to_db())
                    
                    # Add done callback to catch and log task errors
                    def task_done_callback(task):
                        try:
                            exc = task.exception()
                            if exc:
                                # Log task failure to stderr for debugging
                                import sys
                                print(
                                    f"DBLogHandler: Async task failed to log error: {exc}",
                                    file=sys.stderr
                                )
                        except Exception:
                            # Ignore errors in callback
                            pass
                    
                    task.add_done_callback(task_done_callback)
                else:
                    # No event loop running, run synchronously
                    async def _log_to_db_sync():
                        try:
                            # Get app_id if not already available
                            nonlocal app_id
                            if not app_id:
                                try:
                                    from jvagent.core.app import App
                                    app = await App.get()
                                    if app:
                                        app_id = app.id
                                except Exception:
                                    pass
                            
                            # If still no app_id, try from agent_id
                            if not app_id and agent_id:
                                try:
                                    from jvagent.core.agent import Agent
                                    agent = await Agent.get(agent_id)
                                    if agent and hasattr(agent, 'app_id'):
                                        app_id = agent.app_id
                                except Exception:
                                    pass
                            
                            # Check app-level config (app_id is optional - jvagent has its own log database)
                            is_enabled = await self._logging_service._is_logging_enabled(app_id)
                            if not is_enabled:
                                return  # Skip if disabled
                            
                            # Always attempt to log - app_id is optional
                            # LoggingService will use empty string if app_id is None
                            await self._logging_service.log_error(
                                error_data=error_data,
                                app_id=app_id,  # May be None, will default to empty string
                                agent_id=agent_id or None,
                                status_code=status_code,
                                error_code=error_code,
                                interaction_id=interaction_id or None,
                                session_id=session_id or None,
                                user_id=user_id or None,
                            )
                        except Exception:
                            # Fail silently to avoid breaking main flow
                            pass
                    
                    try:
                        asyncio.run(_log_to_db_sync())
                    except Exception:
                        # Fail silently to avoid breaking main flow
                        pass
            except RuntimeError:
                # No event loop available, skip database logging
                # This can happen in some edge cases, fail silently
                pass
            except Exception:
                # Log handler errors silently - logging should never break the main flow
                pass
                
        except Exception:
            # Never fail - logging should never break the main flow
            # Silently ignore any errors in the handler itself
            pass
    
    def _extract_context(self, record: logging.LogRecord) -> Dict[str, Any]:
        """Extract context from log record and call stack.
        
        Context is extracted in priority order:
        1. record.details dict (explicit context)
        2. Call stack inspection (Action/InteractWalker instances)
        3. Context variables (current_interaction_id, current_action_name)
        4. Log record metadata
        
        Args:
            record: The log record
            
        Returns:
            Dictionary with extracted context
        """
        context = {}
        
        # Priority 1: Extract from record.details (renamed from extra)
        details = getattr(record, "details", None)
        if details and isinstance(details, dict):
            context.update(details)
        
        # Also check record.__dict__ for details (in case it was set differently)
        if "details" in record.__dict__:
            details = record.__dict__["details"]
            if isinstance(details, dict):
                context.update(details)
        
        # Priority 2: Infer from call stack
        stack_context = self._infer_context_from_stack()
        # Only add stack context if not already in context (explicit takes precedence)
        for key, value in stack_context.items():
            if key not in context and value is not None:
                context[key] = value
        
        # Priority 3: Extract from context variables
        interaction_id = get_interaction_id()
        if interaction_id and "interaction_id" not in context:
            context["interaction_id"] = interaction_id
        
        action_name = get_calling_action_name()
        if action_name and "action_class" not in context:
            context["action_class"] = action_name
        
        # Priority 4: Infer context from function name
        if "context" not in context and record.funcName:
            func_name = record.funcName
            if func_name.startswith("on_"):
                context["context"] = func_name
        
        return context
    
    def _infer_context_from_stack(self) -> Dict[str, Any]:
        """Infer context from call stack.
        
        Walks up the call stack to find Action and InteractWalker instances
        and extract context information from them.
        
        Returns:
            Dictionary with inferred context
        """
        context = {}
        
        try:
            # Walk up the call stack
            frame = inspect.currentframe()
            if not frame:
                return context
            
            # Skip handler frames, go to actual error location
            # Start from frame.f_back to skip this method
            frame = frame.f_back
            if not frame:
                return context
            
            # Check up to 10 frames
            for _ in range(10):
                if not frame:
                    break
                
                # Check for Action instance in local variables
                for name, obj in frame.f_locals.items():
                    if obj is None:
                        continue
                    
                    # Check for Action instance
                    if hasattr(obj, 'agent_id') and hasattr(obj, 'id') and hasattr(obj, 'get_class_name'):
                        # Likely an Action instance
                        if 'action_class' not in context:
                            try:
                                context['action_class'] = obj.get_class_name()
                                context['action_id'] = obj.id
                                if hasattr(obj, 'label'):
                                    context['action_label'] = obj.label
                                if hasattr(obj, 'agent_id') and obj.agent_id:
                                    context['agent_id'] = obj.agent_id
                            except Exception:
                                pass
                    
                    # Check for InteractWalker instance
                    if hasattr(obj, 'interaction') and hasattr(obj, 'agent_id'):
                        # Likely an InteractWalker
                        if 'interaction_id' not in context:
                            try:
                                if obj.interaction:
                                    context['interaction_id'] = obj.interaction.id
                            except Exception:
                                pass
                        if 'session_id' not in context:
                            try:
                                session_id = getattr(obj, 'session_id', None)
                                if session_id:
                                    context['session_id'] = session_id
                            except Exception:
                                pass
                        if 'user_id' not in context:
                            try:
                                user_id = getattr(obj, 'user_id', None)
                                if user_id:
                                    context['user_id'] = user_id
                            except Exception:
                                pass
                        if 'agent_id' not in context:
                            try:
                                if obj.agent_id:
                                    context['agent_id'] = obj.agent_id
                            except Exception:
                                pass
                
                # Move to next frame
                frame = frame.f_back
                
        except Exception:
            # If stack inspection fails, return what we have
            pass
        finally:
            # Clean up frame reference
            try:
                del frame
            except Exception:
                pass
        
        return context

