"""Utilities for safely initializing DSPy to handle bootstrap errors.

This module provides functions to safely initialize DSPy, handling errors
that can occur during cache initialization or state loading.
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def safe_configure_dspy_cache(
    disk_cache_dir: Optional[str] = None,
    enable_disk_cache: bool = False,
    enable_memory_cache: bool = True,
) -> None:
    """Safely configure DSPy cache with error handling.
    
    This function ensures that DSPy cache is properly initialized even if
    the default cache directory cannot be created. By default, disk cache
    is disabled to avoid file system issues during bootstrap.
    
    Args:
        disk_cache_dir: Optional custom cache directory. If None, uses
            DSPY_CACHEDIR env var or default ~/.dspy_cache
        enable_disk_cache: Whether to enable disk cache (default: False)
        enable_memory_cache: Whether to enable memory cache (default: True)
    """
    try:
        import dspy
        from dspy.clients import configure_cache, DISK_CACHE_DIR, DISK_CACHE_LIMIT
        
        # Use provided directory or default
        cache_dir = disk_cache_dir or DISK_CACHE_DIR
        
        # Normalize and validate cache directory path
        # Handle malformed paths (e.g., "1/.dspy_cache/000")
        try:
            cache_path = Path(cache_dir).resolve()
            # If path looks malformed (starts with a number or has invalid segments), use default
            if cache_path.parts and (
                cache_path.parts[0].isdigit() or 
                any(part in ('', '.', '..') for part in cache_path.parts)
            ):
                logger.warning(
                    f"Malformed DSPy cache directory path detected: {cache_dir}. "
                    f"Using default: {DISK_CACHE_DIR}"
                )
                cache_dir = DISK_CACHE_DIR
                cache_path = Path(cache_dir).resolve()
        except (ValueError, OSError) as e:
            logger.warning(
                f"Invalid DSPy cache directory path: {cache_dir}. "
                f"Using default: {DISK_CACHE_DIR}. Error: {e}"
            )
            cache_dir = DISK_CACHE_DIR
            cache_path = Path(cache_dir).resolve()
        
        # Ensure cache directory exists and is writable
        if enable_disk_cache:
            try:
                # Create parent directories if they don't exist
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                # Try to create the cache directory
                cache_path.mkdir(parents=True, exist_ok=True)
                
                # Verify it's writable by creating a test file
                test_file = cache_path / ".dspy_test"
                try:
                    test_file.touch()
                    test_file.unlink()
                except (OSError, PermissionError) as e:
                    logger.warning(
                        f"DSPy cache directory is not writable: {cache_dir}. "
                        f"Falling back to memory-only cache. Error: {e}"
                    )
                    enable_disk_cache = False
            except (OSError, PermissionError, ValueError) as e:
                logger.warning(
                    f"Failed to create DSPy cache directory: {cache_dir}. "
                    f"Falling back to memory-only cache. Error: {e}"
                )
                enable_disk_cache = False
        
        # Configure cache with error handling
        try:
            configure_cache(
                enable_disk_cache=enable_disk_cache,
                enable_memory_cache=enable_memory_cache,
                disk_cache_dir=cache_dir,
                disk_size_limit_bytes=DISK_CACHE_LIMIT,
            )
            logger.debug(
                f"DSPy cache configured: disk={enable_disk_cache}, "
                f"memory={enable_memory_cache}, dir={cache_dir}"
            )
        except Exception as e:
            logger.warning(
                f"Failed to configure DSPy cache: {e}. "
                f"Using memory-only cache as fallback."
            )
            # Fallback to memory-only cache
            try:
                configure_cache(
                    enable_disk_cache=False,
                    enable_memory_cache=True,
                    disk_cache_dir=cache_dir,
                    disk_size_limit_bytes=DISK_CACHE_LIMIT,
                )
            except Exception as fallback_error:
                logger.error(
                    f"Failed to configure memory-only DSPy cache: {fallback_error}. "
                    f"DSPy may not function correctly."
                )
    except ImportError:
        logger.debug("DSPy not available, skipping cache configuration")
    except Exception as e:
        logger.warning(f"Unexpected error configuring DSPy cache: {e}")


def safe_import_dspy() -> bool:
    """Safely import DSPy, handling initialization errors.
    
    This function imports DSPy and handles any errors that occur during
    module-level initialization (such as cache setup).
    
    Returns:
        True if DSPy was successfully imported, False otherwise
    """
    try:
        import dspy
        # Try to access dspy.cache to ensure it's initialized
        _ = dspy.cache
        return True
    except Exception as e:
        logger.warning(f"Error importing or initializing DSPy: {e}")
        # Try to configure cache as fallback with disk cache disabled
        try:
            safe_configure_dspy_cache(enable_disk_cache=False, enable_memory_cache=True)
            import dspy
            _ = dspy.cache
            return True
        except Exception as fallback_error:
            logger.error(f"Failed to initialize DSPy even with fallback: {fallback_error}")
            return False


def ensure_dspy_initialized() -> None:
    """Ensure DSPy is properly initialized before use.
    
    This should be called early in the application bootstrap process,
    before any dspy modules are imported or used.
    
    By default, configures memory-only cache to avoid file system issues.
    This prevents errors from malformed cache paths and old state loading issues.
    """
    try:
        # Step 1: Set environment variable BEFORE any dspy imports
        # This helps prevent disk cache creation, though we'll still reconfigure after import
        # Use a safe temp directory that won't cause issues if it doesn't exist
        import tempfile
        temp_cache_dir = os.path.join(tempfile.gettempdir(), ".dspy_cache_disabled")
        if "DSPY_CACHEDIR" not in os.environ:
            os.environ["DSPY_CACHEDIR"] = temp_cache_dir
        
        # Step 2: Import dspy.clients module (this triggers cache creation at module level)
        # We need to import it to access configure_cache, but we'll reconfigure immediately
        try:
            from dspy.clients import configure_cache, DISK_CACHE_LIMIT
            import dspy
        except ImportError:
            logger.debug("DSPy not available, skipping initialization")
            return
        except AttributeError as e:
            # Handle signature_version and other attribute errors from old cached state
            if "signature_version" in str(e) or "attribute" in str(e).lower():
                logger.warning(
                    f"Error accessing DSPy cache (possibly old cached state): {e}. "
                    f"Clearing cache and reconfiguring."
                )
                # Clear the cache by reconfiguring
                try:
                    configure_cache(
                        enable_disk_cache=False,
                        enable_memory_cache=True,
                        disk_cache_dir=temp_cache_dir,
                        disk_size_limit_bytes=DISK_CACHE_LIMIT,
                    )
                    # Clear memory cache if it exists
                    if hasattr(dspy, 'cache') and hasattr(dspy.cache, 'reset_memory_cache'):
                        dspy.cache.reset_memory_cache()
                except Exception as clear_error:
                    logger.warning(f"Failed to clear cache: {clear_error}")
            raise
        
        # Step 3: Immediately reconfigure cache with disk cache disabled
        # This overrides the default cache created at import time
        try:
            configure_cache(
                enable_disk_cache=False,
                enable_memory_cache=True,
                disk_cache_dir=temp_cache_dir,
                disk_size_limit_bytes=DISK_CACHE_LIMIT,
            )
            logger.debug("DSPy cache configured: disk=False, memory=True")
        except Exception as config_error:
            logger.warning(
                f"Failed to configure DSPy cache: {config_error}. "
                f"Continuing with default cache."
            )
        
        # Step 4: Clear memory cache to remove any old state
        # This prevents signature_version errors from old cached data
        try:
            if hasattr(dspy, 'cache') and hasattr(dspy.cache, 'reset_memory_cache'):
                dspy.cache.reset_memory_cache()
                logger.debug("Cleared DSPy memory cache to remove old state")
        except Exception as clear_error:
            logger.debug(f"Could not clear memory cache (may not exist): {clear_error}")
        
        # Step 5: Verify cache is accessible
        try:
            _ = dspy.cache
            logger.debug("DSPy initialized successfully with memory-only cache")
        except AttributeError as e:
            if "signature_version" in str(e):
                logger.warning(
                    f"signature_version error detected: {e}. "
                    f"This may indicate old cached state. Cache has been reconfigured."
                )
            else:
                logger.warning(f"DSPy cache access error: {e}")
                
    except Exception as e:
        # Catch any other errors (including signature_version errors)
        error_msg = str(e)
        if "signature_version" in error_msg:
            logger.warning(
                f"signature_version error during DSPy initialization: {e}. "
                f"This is likely from old cached state. Continuing with memory-only cache."
            )
        else:
            logger.warning(
                f"Error initializing DSPy: {e}. "
                f"Continuing, but DSPy features may not work correctly."
            )
