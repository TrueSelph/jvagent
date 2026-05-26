"""Reasoning-helm tool registry assembly.

Populated by C-3 (harness service tools) and C-5 (skills + actions). At C-2
only the assembler stub exists so the engine import resolves.
"""

from jvagent.action.helm.reasoning.registry.assembler import assemble_cockpit_tools

__all__ = ["assemble_cockpit_tools"]
