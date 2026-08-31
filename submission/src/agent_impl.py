"""Current submission implementation.

This early scaffold delegates to the frozen repository baseline so the official
entry point is testable immediately. Before final packaging, move the selected
implementation and all runtime helpers under ``submission/`` so the directory can
run independently of ``starter/``.
"""

from starter.agent import Agent

__all__ = ["Agent"]
