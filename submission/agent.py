"""Official participant entry point.

The evaluator imports ``Agent`` from this file. Keep diagnostics and experiment
logic out of the official response payload.
"""

from submission.src.agent_impl import Agent

__all__ = ["Agent"]
