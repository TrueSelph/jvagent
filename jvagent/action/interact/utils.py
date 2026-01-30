"""Shared utilities for the interact subsystem."""
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)

async def flush_deferred_saves(interaction: Any, conversation: Optional[Any] = None) -> bool:
    """Flush deferred saves for interaction and conversation with error handling.
    
    This function disables deferred save mode and flushes accumulated changes
    for both the interaction and optionally the conversation. Errors are logged
    but not re-raised to avoid failing the response after interaction completion.
    
    Args:
        interaction: Interaction instance to flush
        conversation: Optional Conversation instance to flush
        
    Returns:
        True if all flushes succeeded, False if any failed
    """
    success = True
    
    # Flush interaction
    try:
        if hasattr(interaction, "disable_deferred_saves"):
            interaction.disable_deferred_saves()
        if hasattr(interaction, "flush"):
            await interaction.flush()
    except Exception as e:
        interaction_id = getattr(interaction, "id", "unknown")
        logger.error(f"Failed to flush interaction {interaction_id}: {e}")
        success = False
    
    # Flush conversation if provided
    if conversation:
        try:
            if hasattr(conversation, "disable_deferred_saves"):
                conversation.disable_deferred_saves()
            if hasattr(conversation, "flush"):
                await conversation.flush()
        except Exception as e:
            conversation_id = getattr(conversation, "id", "unknown")
            logger.error(f"Failed to flush conversation {conversation_id}: {e}")
            success = False
    
    return success
