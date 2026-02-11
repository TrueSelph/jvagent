"""Standard validators for common field types.

This module provides a registry of reusable validators for common field types
(email, phone, URL, etc.) that can be referenced in question constraints via
the 'format' or 'standard_validators' keys.

Standard validators run BEFORE custom @input_validator decorators, allowing
custom validators to add domain-specific logic on top of format validation.
"""

import logging
import re
from typing import Any, Callable, Dict, Optional, Tuple

from .enums import ValidationStatus

logger = logging.getLogger(__name__)

# Registry of standard validators keyed by name
_STANDARD_VALIDATORS: Dict[str, Callable] = {}


def standard_validator(name: str):
    """Decorator to register a standard validator by name.
    
    Args:
        name: Unique name for this validator (e.g., "email", "phone", "url")
        
    Example:
        @standard_validator("email")
        def validate_email(value: Any, constraints: Dict[str, Any]) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
            if not re.match(r'^[^@]+@[^@]+\.[^@]+$', value):
                return ValidationStatus.INVALID, "Invalid email format", None
            return None  # Valid
    """
    def decorator(func: Callable) -> Callable:
        _STANDARD_VALIDATORS[name] = func
        logger.debug(f"Registered standard validator: {name}")
        return func
    return decorator


def get_standard_validator(name: str) -> Optional[Callable]:
    """Get a standard validator by name.
    
    Args:
        name: Name of the validator
        
    Returns:
        Validator function if found, None otherwise
    """
    return _STANDARD_VALIDATORS.get(name)


def get_all_standard_validators() -> Dict[str, Callable]:
    """Get all registered standard validators.
    
    Returns:
        Dictionary of validator name -> validator function
    """
    return _STANDARD_VALIDATORS.copy()


# ============================================================================
# Standard Validators
# ============================================================================

@standard_validator("string")
def validate_string_type(
    value: Any, constraints: Dict[str, Any]
) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
    """Validate value is a string.
    
    Args:
        value: Value to validate
        constraints: Question constraints
        
    Returns:
        Validation result tuple if invalid, None if valid
    """
    if not isinstance(value, str):
        return ValidationStatus.INVALID, f"Expected a string value, got {type(value).__name__}", None
    return None


@standard_validator("number")
def validate_number_type(
    value: Any, constraints: Dict[str, Any]
) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
    """Validate value is a number.
    
    Args:
        value: Value to validate
        constraints: Question constraints
        
    Returns:
        Validation result tuple if invalid, None if valid
    """
    try:
        float(value)
        return None
    except (ValueError, TypeError):
        return ValidationStatus.INVALID, "Expected a number value", None


@standard_validator("integer")
def validate_integer_type(
    value: Any, constraints: Dict[str, Any]
) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
    """Validate value is an integer.
    
    Args:
        value: Value to validate
        constraints: Question constraints
        
    Returns:
        Validation result tuple if invalid, None if valid
    """
    try:
        int(value)
        return None
    except (ValueError, TypeError):
        return ValidationStatus.INVALID, "Expected an integer value", None


@standard_validator("email")
def validate_email_format(
    value: Any, constraints: Dict[str, Any]
) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
    """Validate email address format.
    
    Args:
        value: Value to validate
        constraints: Question constraints
        
    Returns:
        Validation result tuple if invalid, None if valid
    """
    if not isinstance(value, str):
        return ValidationStatus.INVALID, "Email must be a string", None
    
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, value):
        return ValidationStatus.INVALID, "Please provide a valid email address", None
    
    return None


@standard_validator("phone")
def validate_phone_format(
    value: Any, constraints: Dict[str, Any]
) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
    """Validate phone number format (basic validation).
    
    Accepts various formats: (123) 456-7890, 123-456-7890, 1234567890, +1-123-456-7890
    
    Args:
        value: Value to validate
        constraints: Question constraints
        
    Returns:
        Validation result tuple if invalid, None if valid
    """
    if not isinstance(value, str):
        return ValidationStatus.INVALID, "Phone number must be a string", None
    
    # Remove common separators and spaces
    digits = re.sub(r'[\s\-\(\)\+\.]', '', value)
    
    # Check if remaining characters are digits and length is reasonable
    if not digits.isdigit():
        return ValidationStatus.INVALID, "Phone number should contain only digits and separators", None
    
    if len(digits) < 10 or len(digits) > 15:
        return ValidationStatus.INVALID, "Phone number should be 10-15 digits", None
    
    return None


@standard_validator("url")
def validate_url_format(
    value: Any, constraints: Dict[str, Any]
) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
    """Validate URL format.
    
    Args:
        value: Value to validate
        constraints: Question constraints
        
    Returns:
        Validation result tuple if invalid, None if valid
    """
    if not isinstance(value, str):
        return ValidationStatus.INVALID, "URL must be a string", None
    
    # Basic URL pattern - supports http, https, ftp
    url_pattern = r'^(https?|ftp)://[^\s/$.?#].[^\s]*$'
    if not re.match(url_pattern, value, re.IGNORECASE):
        return ValidationStatus.INVALID, "Please provide a valid URL (e.g., https://example.com)", None
    
    return None


@standard_validator("pattern")
def validate_pattern_constraint(
    value: Any, constraints: Dict[str, Any]
) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
    """Validate value matches a regex pattern from constraints.
    
    Uses constraints['pattern'] and optional constraints['pattern_error'].
    
    Args:
        value: Value to validate
        constraints: Question constraints with 'pattern' and optional 'pattern_error'
        
    Returns:
        Validation result tuple if invalid, None if valid
    """
    pattern = constraints.get("pattern")
    if not pattern:
        return None
    
    if not isinstance(value, str):
        return ValidationStatus.INVALID, "Value must be a string for pattern matching", None
    
    if not re.match(pattern, value):
        error_msg = constraints.get("pattern_error", "Value doesn't match required format")
        return ValidationStatus.INVALID, error_msg, None
    
    return None


@standard_validator("no_disposable_email")
def validate_no_disposable_email(
    value: Any, constraints: Dict[str, Any]
) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
    """Validate email is not from a disposable email provider.
    
    Args:
        value: Value to validate
        constraints: Question constraints
        
    Returns:
        Validation result tuple if invalid, None if valid
    """
    if not isinstance(value, str):
        return None
    
    # Common disposable email domains
    disposable_domains = [
        'tempmail.com', 'throwaway.email', '10minutemail.com',
        'guerrillamail.com', 'mailinator.com', 'trashmail.com',
        'temp-mail.org', 'fakeinbox.com', 'maildrop.cc'
    ]
    
    domain = value.split('@')[1].lower() if '@' in value else ''
    if domain in disposable_domains:
        return ValidationStatus.INVALID, "Please use a permanent email address, not a disposable one", None
    
    return None


@standard_validator("no_test_domain")
def validate_no_test_domain(
    value: Any, constraints: Dict[str, Any]
) -> Optional[Tuple[ValidationStatus, Optional[str], Optional[Any]]]:
    """Validate email is not from a test domain.
    
    Args:
        value: Value to validate
        constraints: Question constraints
        
    Returns:
        Validation result tuple if invalid, None if valid
    """
    if not isinstance(value, str):
        return None
    
    test_domains = ['example.com', 'test.com', 'invalid.com', 'localhost']
    domain = value.split('@')[1].lower() if '@' in value else ''
    
    if domain in test_domains:
        return ValidationStatus.INVALID, "Please provide a real email address, not a test domain", None
    
    return None
