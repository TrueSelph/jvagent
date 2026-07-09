"""Leadgen action — conversational lead capture with MCP sync."""

from .leadgen_action import LeadGenAction
from .store import LeadProfile, LeadProfileNode, LeadRecord, LeadRecordNode

__all__ = [
    "LeadGenAction",
    "LeadRecord",
    "LeadRecordNode",
    "LeadProfile",
    "LeadProfileNode",
]
