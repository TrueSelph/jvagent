"""Signup interview interact action."""

# Ensure typing names are in namespace before submodule load (loader may run
# submodule code in package globals, where decorator annotations can reference Callable).
from typing import Callable  # noqa: F401

from .signup_interview_interact_action import SignupInterviewInteractAction

__all__ = ["SignupInterviewInteractAction"]

