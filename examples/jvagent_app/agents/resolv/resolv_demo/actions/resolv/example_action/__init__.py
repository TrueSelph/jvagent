"""Example Action Package

This package provides the ExampleAction class and its API endpoints.
"""

# Import the action class so it can be imported from the package
from .example_action import ExampleAction

# Import endpoints module to ensure endpoints are discovered and registered
# This must be imported for endpoint discovery to work
from . import endpoints  # noqa: F401

__all__ = ["ExampleAction"]
